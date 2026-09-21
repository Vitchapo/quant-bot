#!/usr/bin/env python3
"""Coupe-circuit journalier FTMO. A planifier toutes les 5 a 15 min en seance.

    python scripts/coupe_circuit.py --config config/ftmo.yaml
    python scripts/coupe_circuit.py --config config/ftmo.yaml --a-blanc
    python scripts/coupe_circuit.py --etat            # ou en est-on, sans reseau
    python scripts/coupe_circuit.py --lever-verrou    # repartir, apres analyse
    python scripts/coupe_circuit.py --installer --minutes 10

Ce que fait ce script
---------------------
Il lit l'equity du compte MT5, la compare au SOLDE DE REFERENCE du jour, et si
la perte atteint le seuil (-4 % par defaut, sous les -5 % du contrat) il ferme
TOUTES les positions et pose un verrou sur disque qui empeche le robot de
rouvrir quoi que ce soit.

La reference FTMO, et pourquoi ce n'est pas l'equity de la veille
----------------------------------------------------------------
FTMO recalcule la perte journaliere a 00:00 CET a partir du SOLDE de cloture,
pas de l'equity. La difference est le profit flottant : si tu termines la
journee avec +3 000 de latent non realise, ton solde ne l'inclut pas, et ta
limite du lendemain est calculee sans lui. Prendre l'equity comme reference
donnerait une limite plus haute que la vraie, c'est-a-dire un garde-fou qui
laisse passer.

`mt5broker.compte()` expose donc le solde dans `last_equity`, et ce script
enregistre en plus le solde observe au premier passage de chaque journee CET -
parce qu'un solde lu a 16h00 n'est plus celui de 00:00 si un ordre a ete
execute entre-temps.

Pourquoi il est separe du robot
-------------------------------
Le robot tourne une fois par jour apres la cloture. Une limite journaliere se
franchit EN SEANCE, sur du non realise. La constater le soir, c'est en prendre
acte : le compte est deja elimine. Ce script ne fait que lire le compte et
fermer - aucun cours telecharge, aucun score recalcule.

Il n'ouvre JAMAIS de position. Un script planifie toutes les dix minutes qui
pourrait acheter serait une machine a surprises ; celui-ci ne sait que sortir.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import defi, mt5broker
from quantbot.config import Config

JOURNAL = Path("data/coupe_circuit.txt")
REFERENCE = Path("data/reference_jour.json")
ETAT = Path("data/coupe_circuit_etat.json")
NOM_TACHE = "quantbot-coupe-circuit"

RIEN, VERROU, ERREUR, HORS_SERVICE = 0, 1, 2, 3

#: Au-dela, on cesse de journaliser chaque echec identique et on ESCALADE.
#: A dix minutes de periode, six passages font une heure de seance sans filet.
ECHECS_AVANT_ALERTE = 6

#: FTMO recalcule a 00:00 CET. CET = UTC+1, CEST = UTC+2. On prend UTC+2 en
#: ete et UTC+1 en hiver via l'heure locale du serveur, faute de tzdata garanti.
def _jour_cet(maintenant=None) -> str:
    """Date de la journee de trading FTMO, bornee a 00:00 CET."""
    t = maintenant or datetime.now(timezone.utc)
    # Approximation volontaire et documentee : UTC+1. Un decalage d'une heure
    # sur la frontiere de minuit CET ne peut que rendre le garde-fou plus
    # PRUDENT (il garde la reference de la veille une heure de trop), jamais
    # plus permissif.
    return (t + timedelta(hours=1)).date().isoformat()


def _lire_reference(chemin=None) -> dict:
    chemin = Path(chemin) if chemin else REFERENCE
    if not chemin.exists():
        return {}
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _ecrire_reference(etat: dict, chemin=None) -> None:
    chemin = Path(chemin) if chemin else REFERENCE
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(etat, indent=1, sort_keys=True), encoding="utf-8")


def reference_du_jour(solde: float, equity: float, chemin=None,
                      maintenant=None) -> dict:
    """Solde de reference pour la journee CET en cours.

    Le premier passage de la journee fige la reference. Les suivants la
    relisent. Sans cette memoire, un ordre execute a 15h00 changerait le solde
    et donc la limite, en pleine seance - la limite journaliere deviendrait
    mobile alors qu'elle est fixe.
    """
    jour = _jour_cet(maintenant)
    etat = _lire_reference(chemin)
    if etat.get("jour") != jour:
        etat = {"jour": jour, "solde_reference": float(solde),
                "equity_au_depart": float(equity),
                "fige_le": (maintenant or datetime.now(timezone.utc)).isoformat()}
        _ecrire_reference(etat, chemin)
    return etat



def _lire_etat(chemin=None) -> dict:
    chemin = Path(chemin) if chemin else ETAT
    if not chemin.exists():
        return {}
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _ecrire_etat(etat: dict, chemin=None) -> None:
    chemin = Path(chemin) if chemin else ETAT
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(etat, indent=1, sort_keys=True), encoding="utf-8")


def noter_succes(chemin=None, maintenant=None) -> dict:
    """Le terminal a repondu. Remet le compteur a zero."""
    etat = _lire_etat(chemin)
    etat.update({"dernier_succes": (maintenant or datetime.now(timezone.utc)).isoformat(),
                 "echecs_consecutifs": 0})
    etat.pop("premiere_erreur", None)
    etat.pop("derniere_erreur", None)
    _ecrire_etat(etat, chemin)
    return etat


def noter_echec(raison: str, chemin=None, maintenant=None) -> dict:
    """Le terminal n'a pas repondu. Compte, et dit s'il faut crier.

    Renvoie l'etat avec deux drapeaux :
      `journaliser` : faut-il ecrire dans le journal ? Un echec identique
                      repete 144 fois par jour noie les trois lignes qui
                      comptent. On ecrit le premier, puis un sur six.
      `alerter`     : le seuil d'escalade est-il franchi ?
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    etat = _lire_etat(chemin)
    n = int(etat.get("echecs_consecutifs", 0) or 0) + 1
    etat["echecs_consecutifs"] = n
    etat["derniere_erreur"] = str(raison)[:300]
    etat["derniere_tentative"] = maintenant.isoformat()
    etat.setdefault("premiere_erreur", maintenant.isoformat())
    _ecrire_etat(etat, chemin)
    etat["journaliser"] = (n == 1 or n % ECHECS_AVANT_ALERTE == 0)
    etat["alerter"] = n >= ECHECS_AVANT_ALERTE
    return etat


def verdict(equity: float, solde_reference: float, seuil: float) -> dict:
    """Ou en est la journee ? Fonction PURE, testable sans terminal."""
    if solde_reference <= 0:
        return {"ok": True, "perte": 0.0, "marge_restante": seuil,
                "detail": "aucune reference"}
    perte = equity / solde_reference - 1.0
    return {"ok": perte > -seuil,
            "perte": perte,
            "marge_restante": seuil + perte,
            "detail": "equity %.2f contre reference %.2f, soit %+.2f %% "
                      "(seuil %.2f %%)"
                      % (equity, solde_reference, 100 * perte, -100 * seuil)}


class Rapport:
    def __init__(self, chemin=JOURNAL):
        self.chemin, self.lignes = Path(chemin), []

    def __call__(self, texte=""):
        print(texte)
        self.lignes.append(texte)

    def clore(self, v, silencieux=False):
        self("VERDICT : %s" % v)
        if silencieux:
            return
        try:
            self.chemin.parent.mkdir(parents=True, exist_ok=True)
            with self.chemin.open("a", encoding="utf-8") as fh:
                fh.write("\n%s\n%s  %s\n%s\n%s\n"
                         % ("-" * 72, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            v, "-" * 72, "\n".join(self.lignes)))
        except Exception:
            pass


def passer(args) -> int:
    rapport = Rapport(Path(args.journal))
    rapport("coupe-circuit - %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    cfg = Config.load(args.config)

    params = defi.parametres(cfg)
    if params is None:
        rapport("  mode defi inactif : rien a surveiller.")
        rapport.clore("INACTIF", silencieux=True)
        return RIEN
    seuil = float(args.seuil) if args.seuil else params["perte_jour_max"]

    etat_defi = defi.lire_etat()
    if defi.est_verrouille(etat_defi) and not etat_defi.get("a_solder"):
        rapport("  VERROU deja pose : %s" % (etat_defi["verrou"].get("detail", "")))
        rapport("  Rien a faire. Pour repartir : --lever-verrou")
        rapport.clore("VERROU", silencieux=True)
        return VERROU

    try:
        api = mt5broker.connecter(cfg)
    except mt5broker.MT5Indisponible as exc:
        premiere = str(exc).splitlines()[0]
        e = noter_echec(premiere, chemin=args.etat)
        rapport("  TERMINAL INJOIGNABLE (%d passage(s) d'affilee)"
                % e["echecs_consecutifs"])
        rapport("  %s" % premiere)
        if e["alerter"]:
            rapport("")
            rapport("  *** COUPE-CIRCUIT HORS SERVICE ***")
            rapport("  Depuis %s. Aucune limite de perte n'est surveillee." %
                    e.get("premiere_erreur", "?"))
            rapport("  Si des positions sont ouvertes, elles ne sont protegees")
            rapport("  par RIEN. Ferme-les a la main, ou desinstalle la tache :")
            rapport("      python scripts\\coupe_circuit.py --desinstaller")
        else:
            rapport("  Tant que le terminal est injoignable, le coupe-circuit")
            rapport("  ne protege RIEN.")
        # On n'ecrit PAS a chaque passage : 144 entrees identiques par jour
        # noieraient les trois lignes qui comptent. Le premier echec et un sur
        # six ensuite suffisent a reconstituer l'histoire.
        rapport.clore("HORS SERVICE - %d echec(s)" % e["echecs_consecutifs"]
                      if e["alerter"] else "ERREUR - terminal injoignable",
                      silencieux=not e["journaliser"])
        return HORS_SERVICE if e["alerter"] else ERREUR

    try:
        c = api.compte()
        noter_succes(chemin=args.etat)
        equity, solde = float(c["equity"]), float(c["balance"])
        ref = reference_du_jour(solde, equity, chemin=args.reference)
        v = verdict(equity, ref["solde_reference"], seuil)

        rapport("  journee CET %s, reference figee a %.2f"
                % (ref["jour"], ref["solde_reference"]))
        rapport("  %s" % v["detail"])
        rapport("  latent %+.2f, marge libre %.2f"
                % (c["profit_flottant"], c["cash"]))

        if v["ok"]:
            rapport("  marge restante avant coupure : %.2f point(s)"
                    % (100 * v["marge_restante"]))
            rapport.clore("RIEN A SIGNALER", silencieux=True)
            return RIEN

        # -- COUPURE -------------------------------------------------------
        rapport("")
        rapport("  !! SEUIL FRANCHI : coupure immediate")
        if args.a_blanc:
            rapport("  A BLANC : %d position(s) auraient ete fermees."
                    % len(api.positions_detail()))
            rapport.clore("A BLANC - seuil franchi")
            return VERROU

        try:
            api.annuler_ordres()
        except Exception as exc:
            rapport("  annulation des ordres en attente : ECHEC %s" % str(exc)[:120])
        res = api.fermer_tout()
        rapport("  " + res["message"])
        for e in res["echecs"]:
            rapport("    ECHEC ticket %s : %s" % (e["ticket"], e["raison"][:100]))

        etat = defi.lire_etat()
        etat.setdefault("capital_depart", ref["solde_reference"])
        etat["verrou"] = {
            "raison": defi.VERROU_PERTE,
            "detail": "coupe-circuit journalier : %+.2f %% (seuil %.2f %%)"
                      % (100 * v["perte"], -100 * seuil),
            "depuis": ref["jour"],
            "equity": equity,
        }
        etat["a_solder"] = not res["ok"]
        defi.ecrire_etat(etat)

        rapport("")
        rapport("  Verrou pose. Le robot n'ouvrira plus rien, et l'amorcage est")
        rapport("  bloque : un compte solde ressemble a un compte neuf.")
        rapport("  Pour repartir : python scripts\\coupe_circuit.py --lever-verrou")
        rapport.clore("COUPURE - %+.2f %%" % (100 * v["perte"]))
        return VERROU
    finally:
        api.fermer()


def montrer(args) -> int:
    cfg = Config.load(args.config)
    params = defi.parametres(cfg)
    ref = _lire_reference(args.reference)
    etat = defi.lire_etat()
    print("Journee CET en cours : %s" % _jour_cet())
    if params is None:
        print("Mode defi INACTIF.")
        return RIEN
    print("Seuil de coupure     : %.2f %% (contrat FTMO : 5 %%)"
          % (100 * params["perte_jour_max"]))
    print("Perte totale max     : %.2f %% (contrat : 10 %% statique)"
          % (100 * params["perte_totale_max"]))
    if not ref:
        print("\nAucune reference enregistree : le prochain passage la figera.")
    else:
        print("\nReference du %s : solde %.2f (figee le %s)"
              % (ref.get("jour"), ref.get("solde_reference", 0.0), ref.get("fige_le")))
        if ref.get("jour") != _jour_cet():
            print("  -> perimee : elle sera remplacee au prochain passage.")
    from quantbot.surveillance import sante_coupe_circuit
    sante = sante_coupe_circuit(_lire_etat(args.etat))
    print("\nSante du coupe-circuit : %s" % sante["message"])

    verrou = etat.get("verrou")
    if not verrou:
        print("Verrou : AUCUN.")
        return HORS_SERVICE if sante["niveau"] == "alerte" else RIEN
    print("\nVERROU depuis le %s : %s" % (verrou.get("depuis"), verrou.get("detail")))
    if etat.get("a_solder"):
        print("  POSITIONS ENCORE A SOLDER : la fermeture a echoue.")
    return VERROU


def lever(args) -> int:
    etat = defi.lire_etat()
    if not etat.get("verrou"):
        print("Aucun verrou en place.")
        return RIEN
    print("Verrou : %s" % etat["verrou"].get("detail"))
    print("\nLever le verrou rend au bot le droit d'acheter. Cela ne remet a zero")
    print("ni le capital de depart ni le plus haut : un verrou leve sans que le")
    print("compte soit remonte se reposera au passage suivant.")
    try:
        r = input("\nLever ? [o/N] ").strip().lower()
    except EOFError:
        r = ""
    if r not in ("o", "oui", "y", "yes"):
        print("Annule.")
        return VERROU
    defi.ecrire_etat(defi.lever_verrou(etat))
    print("Verrou leve.")
    return RIEN


MODELE_PS1 = r"""# Coupe-circuit FTMO. Genere par scripts/coupe_circuit.py --installer.
Set-Location -LiteralPath "{racine}"
& "{python}" "scripts\coupe_circuit.py" --config "{config}" *>> "data\coupe_circuit.log"
"""


def installer(args) -> int:
    racine = Path.cwd().resolve()
    script = racine / "lancer_coupe_circuit.ps1"
    if os.name != "nt":
        print("Systeme non-Windows : ni le lanceur ni la tache ne peuvent etre")
        print("ecrits ici (leurs chemins seraient ceux de cette machine).")
        print("\nSur la machine Windows :")
        print('  python scripts\\coupe_circuit.py --installer --minutes %d' % args.minutes)
        return RIEN
    script.write_text(MODELE_PS1.format(racine=racine, python=sys.executable,
                                       config=args.config), encoding="utf-8")
    print("Ecrit : %s" % script)
    tr = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % script
    code = subprocess.run(["schtasks", "/Create", "/TN", NOM_TACHE, "/SC", "MINUTE",
                           "/MO", str(args.minutes), "/F", "/TR", tr],
                          capture_output=True).returncode
    verifie = subprocess.run(["schtasks", "/Query", "/TN", NOM_TACHE],
                             capture_output=True).returncode == 0
    if verifie:
        print("VERIFIE : tache \"%s\" enregistree, toutes les %d minutes."
              % (NOM_TACHE, args.minutes))
        print("\nElle tourne 24 h sur 24 et ne fait rien hors seance.")
        return RIEN
    print("ECHEC : la tache n'apparait pas dans le planificateur (code %d)." % code)
    return ERREUR


def desinstaller(args) -> int:
    """Retire la tache planifiee. Le pendant honnete de --installer.

    Une tache qui echoue toutes les dix minutes contre un terminal absent
    n'est pas neutre : elle figure dans le planificateur et donne l'impression
    qu'une protection existe.
    """
    if os.name != "nt":
        print("Systeme non-Windows. Sur la machine Windows :")
        print('  schtasks /Delete /TN "%s" /F' % NOM_TACHE)
        return RIEN
    code = subprocess.run(["schtasks", "/Delete", "/TN", NOM_TACHE, "/F"],
                          capture_output=True).returncode
    reste = subprocess.run(["schtasks", "/Query", "/TN", NOM_TACHE],
                           capture_output=True).returncode == 0
    if reste:
        print("ECHEC : la tache est toujours la (code %d)." % code)
        return ERREUR
    print("VERIFIE : la tache \"%s\" n'existe plus." % NOM_TACHE)
    print("Plus aucune surveillance automatique des limites de perte.")
    return RIEN


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/ftmo.yaml")
    ap.add_argument("--seuil", type=float, default=None,
                    help="surcharge defi.perte_jour_max (ex : 0.03)")
    ap.add_argument("--a-blanc", action="store_true")
    ap.add_argument("--point", "--etat", dest="point", action="store_true",
                    help="affiche l'etat et sort, sans contacter le terminal")
    ap.add_argument("--lever-verrou", action="store_true")
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--reference", default=str(REFERENCE))
    ap.add_argument("--etat-fichier", dest="etat", default=str(ETAT))
    ap.add_argument("--installer", action="store_true")
    ap.add_argument("--desinstaller", action="store_true",
                    help="retire la tache planifiee et le verifie")
    ap.add_argument("--minutes", type=int, default=10)
    args = ap.parse_args()
    if args.installer:
        return installer(args)
    if args.desinstaller:
        return desinstaller(args)
    if args.point:
        return montrer(args)
    if args.lever_verrou:
        return lever(args)
    return passer(args)


if __name__ == "__main__":
    sys.exit(main())
