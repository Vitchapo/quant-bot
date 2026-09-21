#!/usr/bin/env python3
"""Veille intraday des limites de perte. A lancer toutes les 15-30 min en seance.

    python scripts/veille_defi.py --config config/us.yaml
    python scripts/veille_defi.py --config config/us.yaml --a-blanc
    python scripts/veille_defi.py --etat-defi          # ou en est-on, sans rien faire
    python scripts/veille_defi.py --lever-verrou       # repartir, apres analyse
    python scripts/veille_defi.py --installer          # tache Windows toutes les 30 min

Pourquoi ce script existe
-------------------------
`robot.py` tourne une fois par jour, a 21:00 Paris, soit APRES la cloture de
New York. Il y verifiait les limites du defi - y compris la limite
JOURNALIERE. Or une limite journaliere se franchit en seance, sur du non
realise : la constater le soir, c'est en prendre acte, pas s'en proteger. Le
compte etait deja elimine depuis six heures.

Une limite qu'on ne mesure qu'apres la cloture n'est pas un garde-fou, c'est
un compte rendu d'autopsie.

Pourquoi il est separe, et pourquoi il est si court
--------------------------------------------------
`operations.etat()` recalcule le score composite sur tout l'univers - une a
deux secondes sur 500 titres - et partage le verrou du moteur avec les
backtests. L'appeler toutes les quinze minutes gelerait le tableau de bord a
chaque passage et ferait tourner la strategie pour rien : la question posee
ici ne porte pas sur ce qu'il faut acheter, seulement sur la valeur du compte.

Ce script ne fait donc que LIRE le compte (un appel REST), evaluer les
limites (calcul pur, sans reseau), et - si une limite est franchie - solder.
Aucun cours n'est telecharge, aucun score n'est calcule, aucun verrou de
moteur n'est pris.

Ce qu'il ne fait pas
--------------------
Il n'achete jamais rien. Un script planifie toutes les demi-heures qui
pourrait ouvrir des positions serait une machine a surprises ; celui-ci ne
sait que sortir. Et comme `robot.py`, il ne touche QUE le compte de
simulation : `--reel` n'existe pas ici.

Codes de sortie, pour un planificateur : 0 rien a signaler, 1 verrou en place
(nouveau ou deja pose), 2 erreur.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import datafeed, defi, operations
from quantbot.config import Config

JOURNAL = Path("data/veille_defi.txt")
NOM_TACHE = "quantbot-veille"

RIEN, VERROU, ERREUR = 0, 1, 2


class Rapport:
    """Ecrit a l'ecran ET dans un fichier : personne ne regardera l'ecran."""

    def __init__(self, chemin=JOURNAL):
        self.chemin = Path(chemin)
        self.lignes = []

    def __call__(self, texte=""):
        print(texte)
        self.lignes.append(texte)

    def clore(self, verdict, silencieux=False):
        self("VERDICT : %s" % verdict)
        # Un passage sans rien a signaler n'ecrit RIEN. A raison de 20 passages
        # par seance, journaliser les non-evenements noierait les trois lignes
        # qui comptent sous des milliers de "tout va bien".
        if silencieux:
            return
        try:
            self.chemin.parent.mkdir(parents=True, exist_ok=True)
            entete = "\n%s\n%s  %s\n%s\n" % ("-" * 72,
                                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                             verdict, "-" * 72)
            with self.chemin.open("a", encoding="utf-8") as fh:
                fh.write(entete + "\n".join(self.lignes) + "\n")
        except Exception:
            pass


def montrer_etat(cfg) -> int:
    """Ou en est le defi, d'apres le seul fichier d'etat. Aucun reseau."""
    params = defi.parametres(cfg)
    if params is None:
        print("Mode defi INACTIF (defi.active est faux dans %s)." % cfg.path)
        return RIEN
    etat = defi.lire_etat()
    if not etat:
        print("Aucun etat enregistre : le defi n'a pas encore commence.")
        print("Le prochain passage fixera le capital de depart a la valeur du compte.")
        return RIEN
    print("Capital de depart : %12.2f" % float(etat.get("capital_depart", 0.0)))
    print("Plus haut atteint : %12.2f" % float(etat.get("plus_haut", 0.0)))
    print("Derniere mise a jour : %s" % etat.get("derniere_maj", "?"))
    print("Limites : jour %.1f %% du depart, total %.1f %% (%s), objectif %.1f %%"
          % (100 * params["perte_jour_max"], 100 * params["perte_totale_max"],
             params["reference"], 100 * params["objectif"]))
    verrou = etat.get("verrou")
    if not verrou:
        print("\nVerrou : AUCUN. Le bot peut travailler.")
        return RIEN
    print("\nVERROU en place depuis le %s" % verrou.get("depuis", "?"))
    print("  motif  : %s" % verrou.get("raison", "?"))
    print("  detail : %s" % verrou.get("detail", ""))
    print("  equity au moment de la pose : %.2f" % float(verrou.get("equity", 0.0)))
    if etat.get("a_solder"):
        print("  POSITIONS ENCORE A SOLDER : la liquidation n'a pas pu partir.")
    print("\nLe verrou ne se leve pas tout seul :")
    print("  python scripts/veille_defi.py --lever-verrou")
    return VERROU


def lever(cfg) -> int:
    """Leve le verrou, apres confirmation au clavier.

    Volontairement manuel, et volontairement bavard. Un verrou qu'on leve par
    reflexe ne protege de rien : la seule question qui vaille est de savoir
    pourquoi il a ete pose, et elle se pose devant les chiffres.
    """
    if defi.parametres(cfg) is None:
        print("Mode defi inactif : il n'y a pas de verrou a lever.")
        return RIEN
    etat = defi.lire_etat()
    verrou = etat.get("verrou")
    if not verrou:
        print("Aucun verrou en place.")
        return RIEN
    print("Verrou pose le %s - %s" % (verrou.get("depuis", "?"), verrou.get("detail", "")))
    print()
    print("Lever le verrou rend au bot le droit d'acheter. Cela ne remet a zero")
    print("NI le capital de depart NI le plus haut : la limite totale continue")
    print("de se mesurer depuis les memes references, donc un verrou leve sans")
    print("que le compte soit remonte se reposera au prochain passage.")
    print()
    print("Pour commencer un defi NEUF, supprime data/defi_etat.json a la main.")
    print()
    try:
        reponse = input("Lever le verrou ? [o/N] ").strip().lower()
    except EOFError:
        reponse = ""
    if reponse not in ("o", "oui", "y", "yes"):
        print("Annule. Le verrou reste en place.")
        return VERROU
    defi.ecrire_etat(defi.lever_verrou(etat))
    print("Verrou leve.")
    return RIEN


def passer(args) -> int:
    rapport = Rapport(Path(args.journal))
    rapport("veille defi - %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    cfg = Config.load(args.config)

    if defi.parametres(cfg) is None:
        rapport("  mode defi inactif : rien a surveiller.")
        rapport.clore("INACTIF", silencieux=True)
        return RIEN

    # `datafeed.load_panel` n'est JAMAIS appele : le lambda est la parce que
    # `Operations` l'exige dans son constructeur, et les methodes utilisees ici
    # (`disponible`, `horloge`, `solder`) ne touchent pas aux cours. C'est tout
    # l'interet de ce script : aucun telechargement, aucun score recalcule.
    ops = operations.Operations(cfg, lambda: datafeed.load_panel(cfg))

    dispo = ops.disponible()
    if not dispo["ok"]:
        rapport("  courtier injoignable : %s" % str(dispo["erreur"])[:200])
        rapport.clore("ERREUR - courtier injoignable")
        return ERREUR

    compte = dispo["compte"]
    equity = float(compte.get("equity", 0.0) or 0.0)
    veille = float(compte.get("last_equity", 0.0) or 0.0)
    params = defi.parametres(cfg)
    verdict = defi.evaluer(equity, veille, params, defi.lire_etat())

    rapport("  compte %s : equity %.2f (veille %.2f)"
            % (compte.get("account_number", "?"), equity, veille))
    rapport("  %s" % defi.resume(verdict, params))

    if args.a_blanc:
        defi.ecrire_etat(verdict["etat"])
        if verdict.get("verrou"):
            rapport("  A BLANC : un verrou serait applique, rien n'est envoye.")
            rapport.clore("A BLANC - verrou")
            return VERROU
        rapport.clore("A BLANC - rien a signaler", silencieux=True)
        return RIEN

    # `appliquer_defi` ecrit l'etat lui-meme AVANT d'agir, solde si besoin, et
    # respecte les horaires. Ne pas reecrire l'etat apres coup : cela ecraserait
    # la consigne de liquidation qu'il vient d'eteindre, et les ventes
    # repartiraient au passage suivant. Voir le commentaire dans la methode.
    applique = ops.appliquer_defi(verdict=verdict)

    if not verdict.get("verrou"):
        rapport.clore("RIEN A SIGNALER", silencieux=True)
        return RIEN

    rapport("  " + applique["message"])
    for echec in (applique.get("solde") or {}).get("echecs", []):
        rapport("    ECHEC %-6s %s" % (echec["ticker"], echec["raison"][:80]))
    if verdict.get("nouveau_verrou"):
        rapport("  Verrou POSE a l'instant. Le robot quotidien n'enverra plus rien.")
    rapport.clore("VERROU - " + verdict["verrou"]["raison"])
    return VERROU


MODELE_PS1 = r"""# Lance la veille du defi. Genere par scripts/veille_defi.py --installer.
Set-Location -LiteralPath "{racine}"
& "{python}" "scripts\veille_defi.py" --config "{config}" *>> "data\veille_sortie.log"
"""


def _sortie(commande) -> tuple:
    try:
        r = subprocess.run(commande, capture_output=True)
    except OSError as exc:
        return 1, str(exc)
    brut = (r.stdout or b"") + (r.returncode and (r.stderr or b"") or b"")
    for codec in ("utf-8", "cp1252", "latin-1"):
        try:
            return r.returncode, brut.decode(codec)
        except UnicodeDecodeError:
            continue
    return r.returncode, brut.decode("utf-8", "replace")


def tache_enregistree(nom: str = NOM_TACHE) -> bool:
    if os.name != "nt":
        return False
    code, _ = _sortie(["schtasks", "/Query", "/TN", nom])
    return code == 0


def installer(args) -> int:
    """Enregistre la tache, puis VERIFIE qu'elle existe.

    Meme lecon que `robot.py` : un installateur qui affiche une commande a
    recopier n'installe rien. La tache quantbot n'a jamais existe pendant des
    semaines pour cette raison exacte, et personne ne s'en est apercu parce que
    l'ecran se terminait par un message qui se lisait comme un succes.
    """
    racine = Path.cwd().resolve()
    script = racine / "lancer_veille.ps1"
    if os.name != "nt" and script.exists():
        print("Systeme non-Windows : %s n'est PAS reecrit," % script.name)
        print("ses chemins seraient ceux de cette machine-ci.")
    else:
        script.write_text(MODELE_PS1.format(racine=racine, python=sys.executable,
                                            config=args.config), encoding="utf-8")
        print("Ecrit : %s" % script)

    commande_tr = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % script
    manuel = ('schtasks /Create /TN "%s" /SC MINUTE /MO %d /F /TR "%s"'
              % (NOM_TACHE, args.minutes, commande_tr.replace(chr(34), chr(92) + chr(34))))

    if os.name != "nt":
        print("\nSysteme non-Windows : la tache ne peut pas etre enregistree ici.")
        print("Sur la machine Windows, lance :\n\n  %s\n" % manuel)
        return RIEN

    if tache_enregistree() and not args.remplacer:
        print("\nLa tache \"%s\" existe deja. Relance avec --remplacer." % NOM_TACHE)
        return RIEN

    if not args.oui:
        print("\nUne tache toutes les %d minutes va etre enregistree :" % args.minutes)
        print("\n  %s\n" % commande_tr)
        try:
            reponse = input("Confirmer ? [o/N] ").strip().lower()
        except EOFError:
            reponse = ""
        if reponse not in ("o", "oui", "y", "yes"):
            print("Annule. Pour le faire a la main :\n\n  %s\n" % manuel)
            return RIEN

    code, texte = _sortie(["schtasks", "/Create", "/TN", NOM_TACHE,
                           "/SC", "MINUTE", "/MO", str(args.minutes), "/F",
                           "/TR", commande_tr])
    if texte.strip():
        print(texte.strip())

    if tache_enregistree():
        print("\nVERIFIE : la tache \"%s\" est enregistree, toutes les %d minutes."
              % (NOM_TACHE, args.minutes))
    else:
        print("\nECHEC : la tache n'apparait pas dans le planificateur (code %d)." % code)
        print("Enregistre-la a la main, puis verifie :\n")
        print("  %s" % manuel)
        print('  schtasks /Query /TN "%s"\n' % NOM_TACHE)
        return ERREUR

    print("\nElle tourne 24 h sur 24 et ne fait rien hors seance : la veille")
    print("n'ecrit dans son journal que lorsqu'il y a quelque chose a dire, et")
    print("`appliquer_defi` ne solde que marche ouvert.")
    print("\nPour verifier, lancer tout de suite, ou supprimer :")
    print('  schtasks /Query  /TN "%s" /V /FO LIST' % NOM_TACHE)
    print('  schtasks /Run    /TN "%s"' % NOM_TACHE)
    print('  schtasks /Delete /TN "%s" /F' % NOM_TACHE)
    return RIEN


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--a-blanc", action="store_true",
                    help="evalue et journalise, mais ne solde rien")
    ap.add_argument("--etat-defi", action="store_true",
                    help="affiche l'etat du defi et sort, sans contacter le courtier")
    ap.add_argument("--lever-verrou", action="store_true",
                    help="leve le verrou apres confirmation au clavier")
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--installer", action="store_true",
                    help="enregistre la tache periodique et verifie qu'elle existe")
    ap.add_argument("--minutes", type=int, default=30,
                    help="periode de la tache, en minutes (defaut 30)")
    ap.add_argument("--remplacer", action="store_true")
    ap.add_argument("--oui", action="store_true",
                    help="n'attend pas de confirmation au clavier")
    args = ap.parse_args()

    if args.installer:
        return installer(args)
    if args.etat_defi:
        return montrer_etat(Config.load(args.config))
    if args.lever_verrou:
        return lever(Config.load(args.config))
    return passer(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FileNotFoundError as exc:
        print("\n" + "-" * 72); print(exc); print("-" * 72)
        sys.exit(ERREUR)
