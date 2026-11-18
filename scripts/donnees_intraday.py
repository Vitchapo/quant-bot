#!/usr/bin/env python3
"""Etape 2 du protocole du momentum intrajournalier : les barres de 30 minutes.

    python scripts/donnees_intraday.py --telecharger   # UNE fois : telecharge, scelle, controle
    python scripts/donnees_intraday.py                 # controle les barres scellees ;
                                                       # n'ecrit rien dans le protocole
    python scripts/donnees_intraday.py --ecrire        # controle, puis consigne le tableau
                                                       # de l'etape 2 dans le protocole

Le telechargement (terminal MT5, compte de demonstration FTMO, lecture seule) :
les barres M30 des 6 CFD .cash, de la premiere disponible au 31 octobre 2026
inclus (heure du serveur), telles que MT5 les rend. Le terminal rend parfois
l'historique en plusieurs fois : chaque indice est relu jusqu'a trois lectures
identiques de suite. Rien n'est ecrit tant que les six ne sont pas complets.
Le telechargement refuse de recommencer si le manifeste existe : le protocole
dit "telechargees une seule fois".

Ecrit dans data/intraday/ :
  brut/            un fichier CSV par indice, jamais modifie ensuite
  manifeste.csv    date, nombre de barres, taille et empreinte SHA-256 de chacun
  controle.txt     le compte rendu des controles

Les controles (quantbot.intraday_donnees) ne regardent que les cours, jamais le
sens d'une position ni le lien entre r_RJ et r_DD :
  - les empreintes des barres (manifeste) et du calendrier (etape 2 du
    protocole de tendance) ;
  - l'heure du serveur, sur US500 : si ce controle echoue, l'etape 2 s'arrete
    et le test n'est pas lance (protocole) ;
  - par indice : barres de fenetre manquantes ou figees, sauts, ecarts avec Yahoo.
Les barres manquantes se comptent sur les seances jugees (avril 2018 a octobre
2026) a partir de la premiere barre de l'indice : AUS200 et HK50 commencent
plus tard, ce que le protocole annonce ("sept ans et demi pour AUS200 et HK50").
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

import pandas as pd

import tester_tendance as TT        # lecture du protocole de tendance, deja commitee
from couts_intraday import jour_fr
from quantbot import intraday_donnees as I
from quantbot import mt5broker
from quantbot import tendance_donnees as TD
from quantbot.config import Config

RACINE = Path(__file__).resolve().parent.parent
PROTOCOLE = RACINE / "docs" / "protocole_momentum_intrajournalier.md"
TENDANCE = RACINE / "docs" / "protocole_tendance_multi_actifs.md"
BRUT = RACINE / I.BRUT
MANIFESTE = RACINE / I.MANIFESTE
CONTROLE = RACINE / I.DOSSIER / "controle.txt"
BRUT_TENDANCE = RACINE / TD.BRUT
ESSAIS, PAUSE_S, STABLE, BUDGET_S = 30, 2.0, 3, 300
AVANT = "### La fenêtre et la position"
ENTETE = ("| fichier brut | empreinte SHA-256 | barres | première barre | dernière barre "
          "| barres de fenêtre manquantes | décision |")
ETAPE_2 = "| 2 | données : télécharger une fois les barres de 30 minutes"


def dire(texte: str) -> None:
    print(texte, flush=True)


def _rel(p) -> str:
    """Le chemin a afficher : relatif au depot quand il y est."""
    try:
        return Path(p).resolve().relative_to(RACINE).as_posix()
    except ValueError:
        return str(p)


# -- le telechargement ---------------------------------------------------------------
def lire_complet(mt5, sym: str, attendre=time.sleep, horloge=time.monotonic, dire=dire):
    """Les barres M30 de `sym`, relues jusqu'a STABLE lectures identiques de suite."""
    t0 = horloge()
    d1 = I.pour_mt5(I.DEBUT_DEMANDE)
    d2 = I.pour_mt5(I.FIN_BARRES - pd.Timedelta(seconds=1))
    lus, barres = [], None
    for k in range(ESSAIS):
        b = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M30, d1, d2)
        n = 0 if b is None else len(b)
        dire("    lecture %d : %d barres, %.0f s%s" % (k + 1, n, horloge() - t0,
                                                     "" if n else " (MT5 : %s)" % (mt5.last_error(),)))
        if n:
            barres = b
        lus.append((n, int(b["time"][0]), int(b["time"][-1])) if n else (0, 0, 0))
        if n and len(lus) >= STABLE and len(set(lus[-STABLE:])) == 1:
            return barres
        if horloge() - t0 > BUDGET_S:
            break
        attendre(PAUSE_S)
    raise RuntimeError("%s : historique pas stable apres %d lectures (dernieres : %s). Rien n'a ete "
                       "ecrit ; relancer, le terminal repartira de ce qu'il a deja telecharge."
                       % (sym, len(lus), lus[-STABLE:]))


def telecharger(mt5, carte: dict, brut: Path = BRUT, manifeste: Path = MANIFESTE, maintenant=None,
                attendre=time.sleep, horloge=time.monotonic, dire=dire) -> list:
    """Telecharge les six indices, puis seulement les ecrit et les scelle."""
    if Path(manifeste).exists():
        raise RuntimeError("%s existe : les barres ont deja ete telechargees, et le protocole dit "
                           "\"une seule fois\"" % Path(manifeste).name)
    if Path(brut).exists() and any(Path(brut).iterdir()):
        raise RuntimeError("%s n'est pas vide : les barres ont deja ete telechargees" % brut)
    maintenant = maintenant or datetime.now(timezone.utc)
    fin = int((I.FIN_BARRES - pd.Timestamp("1970-01-01")).total_seconds())
    lues = {}
    for n in I.INDICES:
        sym = carte[n]
        dire("%s (%s) :" % (n, sym))
        try:
            mt5.symbol_select(sym, True)
        except Exception:
            pass
        b = lire_complet(mt5, sym, attendre, horloge, dire)
        lues[n] = b[b["time"] < fin]
    Path(brut).mkdir(parents=True, exist_ok=True)
    lignes = []
    for n, b in lues.items():
        f = Path(brut) / I.nom_fichier(n)
        I.ecrire_barres(f, b)
        t = I.depuis_mt5([b["time"][0], b["time"][-1]])
        lignes.append({"fichier": f.name, "instrument": n, "symbole": carte[n],
                       "telecharge_utc": maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"), "barres": len(b),
                       "premiere_barre": t[0].strftime("%Y-%m-%d %H:%M"),
                       "derniere_barre": t[1].strftime("%Y-%m-%d %H:%M"),
                       "octets": f.stat().st_size, "sha256": TD.sha256(f)})
    I.ecrire_manifeste(lignes, manifeste)
    return lignes


# -- les controles ---------------------------------------------------------------------
def controler(brut: Path = BRUT, manifeste: Path = MANIFESTE, tendance: Path = TENDANCE,
              brut_tendance: Path = BRUT_TENDANCE) -> dict:
    """Empreintes, heure du serveur, puis les controles de chaque indice."""
    problemes = I.verifier_empreintes(brut, manifeste)
    problemes += I.verifier_calendrier(TT.tableau_donnees(tendance.read_text(encoding="utf-8")),
                                       brut_tendance)
    if problemes:
        return {"problemes": problemes}
    cal = I.calendrier(brut_tendance)
    barres = {n: I.lire_barres(Path(brut) / I.nom_fichier(n)) for n in I.INDICES}
    return {"problemes": [], "manifeste": I.lire_manifeste(manifeste),
            "heure": I.controle_heure(barres["US500"], cal["US500"].index),
            "indices": {n: I.controler_indice(n, barres[n], cal[n]) for n in I.INDICES}}


def _p(x: float, d: int = 1) -> str:
    return "%.*f %%" % (d, 100 * x)


def _pf(x: float, d: int = 1, signe: bool = False) -> str:
    """Un pourcentage a la francaise, pour le protocole : virgule, vrai signe moins."""
    return (("%+.*f %%" if signe else "%.*f %%") % (d, 100 * x)).replace(".", ",").replace("-", "−")


def _date(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d")


def _saut(s: dict, fr: bool = False) -> str:
    """Un saut et son verdict : en francais pour le protocole, en ASCII pour la console."""
    jour = pd.Timestamp(s["date"]).strftime("%d/%m/%Y")
    if s["type"] == "seance":
        if fr:
            yahoo = " / ".join(_pf(m, 1, True) for m in s["yahoo"]) or "aucun cours ces jours-là"
            return ("saut du %s (%s, %s écarts-types), %s" % (
                jour, _pf(s["rendement"], 1, True), ("%.0f" % s["sigmas"]),
                "confirmé par Yahoo" if s["resolu"] else "non confirmé par Yahoo (%s)" % yahoo))
        return ("saut du %s : %+.2f %% (%.1f sigmas) ; Yahoo %s -> %s"
                % (jour, 100 * s["rendement"], s["sigmas"],
                   " / ".join("%+.2f %%" % (100 * m) for m in s["yahoo"]) or "absent",
                   "confirme" if s["resolu"] else "NON confirme"))
    if fr:
        return ("saut dans la fenêtre du %s (%s, %s écarts-types), %s" % (
            jour, _pf(s["rendement"], 1, True), "%.0f" % s["sigmas"],
            "corroboré par Yahoo et par la barre d'avant" if s["resolu"]
            else "non corroboré (écart avec Yahoo %s, avec la barre d'avant %s)"
            % (_pf(s["ecart_yahoo"], 2), _pf(s["continuite"], 2))))
    return ("saut dans la fenetre du %s : %+.2f %% (%.1f sigmas) ; ecart avec Yahoo %.2f %%, avec la barre "
            "d'avant %.2f %% -> %s" % (jour, 100 * s["rendement"], s["sigmas"], 100 * s["ecart_yahoo"],
                                      100 * s["continuite"], "corrobore" if s["resolu"] else "NON corrobore"))


def _raison(r: tuple, fr: bool = True) -> str:
    quoi, x = r
    if quoi == "aucune":
        return "aucune séance jugée" if fr else "aucune seance jugee"
    if quoi in ("manquantes", "figees"):
        etat = {"manquantes": ("manquante", "manquante"), "figees": ("figée", "figee")}[quoi][0 if fr else 1]
        if fr:
            return "barre de fenêtre %s pour %s des séances jugées" % (etat, _pf(x))
        return "barre de fenetre %s pour %s des seances jugees" % (etat, _p(x))
    return _saut(x, fr)


def decision(c: dict) -> str:
    """La case "decision" du tableau ; le test de l'etape 4 lit "**exclu**"."""
    return "retenu" if c["retenu"] else "**exclu** : " + " ; ".join(_raison(r) for r in c["raisons"])


def _liste(jours, n: int = 12) -> str:
    return " ".join(_date(d) for d in jours[:n]) + (" ..." if len(jours) > n else "")


def rapport(res: dict) -> str:
    """Le compte rendu des controles, en ASCII (console Windows comprise)."""
    if res["problemes"]:
        return "\n".join(["PAS DE CONTROLE :"] + ["  - " + p for p in res["problemes"]])
    h = res["heure"]
    L = ["Heure du serveur (US500 : parmi les barres de 8:00 a 12:00 a New York, la barre de 9:30",
         "doit avoir le plus gros volume ; seuil 90 %, sur toutes les seances et sur les semaines",
         "de decalage entre l'Europe et les Etats-Unis) :",
         "  toutes les seances    : %d sur %d (%s)" % (h["reussies"], h["seances"], _p(h["part"])),
         "  semaines de decalage  : %d sur %d (%s)" % (h["decalage"]["reussies"], h["decalage"]["seances"],
                                                     _p(h["decalage"]["part"])),
         "  seances sans aucune barre de 8:00 a 12:00 : %d" % h["sans_barres"],
         "  par annee : " + ", ".join("%d %d/%d" % (a, k, n) for a, (n, k) in h["par_annee"].items()),
         "  -> " + ("VERIFIEE" if h["ok"] else "ECHEC : l'etape 2 s'arrete et le test n'est pas lance "
                                               "(protocole, \"Les donnees\")"),
         ""]
    for n in I.INDICES:
        c, m = res["indices"][n], res["manifeste"][I.nom_fichier(n)]
        y = c["yahoo"]
        L += ["%s (%s, %s barres, du %s au %s) :" % (n, m["fichier"], m["barres"], m["premiere_barre"],
                                                    m["derniere_barre"]),
              "  seances jugees : %d%s" % (c["seances_jugees"], "" if not c["seances_jugees"] else ", du %s au %s"
                                           % (_date(c["premiere_jugee"]), _date(c["derniere_jugee"]))),
              "  barres de fenetre manquantes : %d (%s)%s"
              % (len(c["manquantes"]), _p(c["part_manquantes"], 2),
                 " : " + _liste(c["manquantes"]) if c["manquantes"] else ""),
              "  barres de fenetre figees : %d%s"
              % (len(c["figees"]), " : " + _liste(c["figees"]) if c["figees"] else ""),
              "  sauts : %s" % ("aucun" if not c["sauts"] else "%d" % len(c["sauts"]))]
        L += ["    " + _saut(s) for s in c["sauts"]]
        L += ["  Yahoo (cloture a cloture) : correlation %.4f sur %d seances ; ecart median %.3f %% ; "
              "%d ecarts de plus de 1 %%" % (y["correlation"], y["comparees"], 100 * y["ecart_median"],
                                            y["ecarts_1pc"]),
              "  -> " + ("retenu" if c["retenu"] else "EXCLU : " + " ; ".join(
                  _raison(r, fr=False) for r in c["raisons"])),
              ""]
    return "\n".join(L)


def _paragraphe(texte: str, puce: str = "", retrait: int = 0) -> str:
    """Un paragraphe du protocole, coupe a 78 colonnes ; jamais entre un nombre et son %."""
    debut = " " * retrait + puce
    lignes = textwrap.wrap(texte.replace(" %", "\u00a0%"), width=78, initial_indent=debut,
                           subsequent_indent=" " * len(debut), break_long_words=False, break_on_hyphens=False)
    return "\n".join(lignes).replace("\u00a0%", " %")


def _milliers(x) -> str:
    return "{:,}".format(int(x)).replace(",", " ")


def bloc_protocole(res: dict, jour: str) -> str:
    """Le tableau de l'etape 2 et ses notes, a inserer dans le protocole."""
    h, m = res["heure"], res["manifeste"]
    L = [ENTETE, "|---|---|---|---|---|---|---|"]
    for n in I.INDICES:
        c, l = res["indices"][n], m[I.nom_fichier(n)]
        L.append("| `%s` | `%s` | %s | %s | %s | %d sur %s séances jugées (%s) | %s |"
                 % (l["fichier"], l["sha256"], _milliers(l["barres"]),
                    pd.Timestamp(l["premiere_barre"]).strftime("%d/%m/%Y"),
                    pd.Timestamp(l["derniere_barre"]).strftime("%d/%m/%Y"),
                    len(c["manquantes"]), _milliers(c["seances_jugees"]), _pf(c["part_manquantes"]), decision(c)))
    exclus = [n for n in I.INDICES if not res["indices"][n]["retenu"]]
    L += ["", _paragraphe(
        "Téléchargé le %s par `scripts/donnees_intraday.py` (terminal MT5, compte de démonstration FTMO, "
        "lecture seule). Les fichiers sont dans `data/intraday/brut/` (hors du dépôt), leurs empreintes "
        "dans `data/intraday/manifeste.csv`. %s"
        % (jour, "**Aucun indice n'est exclu.**" if not exclus
           else "**Exclu%s : %s.**" % ("s" if len(exclus) > 1 else "", ", ".join(exclus)))), ""]
    L += [_paragraphe(
        "**Heure du serveur.** Parmi les barres US500 de 8:00 à 12:00 à New York, celle de 9:30 a le plus "
        "gros volume pour %s des %s séances, et pour %s des %s séances des semaines de décalage entre "
        "l'Europe et les États-Unis : %s."
        % (_pf(h["part"]), _milliers(h["seances"]), _pf(h["decalage"]["part"]),
           _milliers(h["decalage"]["seances"]),
           "vérifiée" if h["ok"] else "**échec**, l'étape 2 s'arrête"), "- "),
          _paragraphe("**Calendrier.** Les six fichiers de cours quotidiens ont l'empreinte consignée à "
                      "l'étape 2 du protocole de tendance.", "- "),
          _paragraphe("**Barres manquantes.** Comptées sur les séances jugées, à partir de la première barre "
                      "de l'indice : AUS200 et HK50 commencent après avril 2018, ce que le protocole "
                      "annonçait.", "- "),
          _paragraphe("**Cours figés, sauts, Yahoo.** Une barre de fenêtre figée a un plus haut égal au plus "
                      "bas, ou un volume nul. Un saut d'une séance à l'autre (plus de 25 % ou de 10 "
                      "écarts-types) doit être confirmé par Yahoo ; un saut dans la fenêtre (plus de 10 "
                      "écarts-types), corroboré par Yahoo pour le cours de sortie et par la barre d'avant "
                      "pour le cours d'entrée.", "- ")]
    for n in I.INDICES:
        c = res["indices"][n]
        k = len(c["figees"])
        sauts = "aucun saut" if not c["sauts"] else " ; ".join(_saut(s, fr=True) for s in c["sauts"])
        L.append(_paragraphe("%s : %d barre%s figée%s ; %s ; corrélation avec Yahoo, de clôture à clôture, "
                             "%s."
                             % (n, k, "s" * (k > 1), "s" * (k > 1), sauts,
                                ("%.3f" % c["yahoo"]["correlation"]).replace(".", ",")), "- ", 2))
    return "\n".join(L)


def consigner(texte: str, bloc: str, jour: str) -> str:
    """Insere le tableau avant "### La fenetre et la position" et date l'etape 2."""
    crlf = "\r\n" in texte
    lignes = texte.replace("\r\n", "\n").split("\n")
    if ENTETE in lignes:
        raise ValueError("le tableau des donnees est deja dans le protocole : l'etape 2 est faite")
    try:
        k = lignes.index(AVANT)
    except ValueError:
        raise ValueError("titre %r introuvable dans le protocole" % AVANT)
    i = next((i for i, l in enumerate(lignes) if l.startswith(ETAPE_2) and l.rstrip().endswith("| |")), None)
    if i is None:
        raise ValueError("ligne de l'etape 2 introuvable ou deja datee dans le protocole")
    lignes[i] = lignes[i].rstrip()[:-1] + jour + " |"
    lignes[k:k] = bloc.split("\n") + [""]
    texte = "\n".join(lignes)
    return texte.replace("\n", "\r\n") if crlf else texte


# -- le programme ------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--telecharger", action="store_true", help="telecharger les barres (une seule fois)")
    ap.add_argument("--ecrire", action="store_true", help="consigner le tableau de l'etape 2 dans le protocole")
    ap.add_argument("--config", default=str(RACINE / "config" / "ftmo.yaml"))
    args = ap.parse_args(argv)
    os.chdir(RACINE)
    if args.ecrire and ENTETE in PROTOCOLE.read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n"):
        print("Le tableau des donnees est deja dans le protocole : l'etape 2 est faite.")
        return 1
    if args.telecharger:
        if MANIFESTE.exists():
            print("%s existe : les barres ont deja ete telechargees (une seule fois, dit le protocole)."
                  % _rel(MANIFESTE))
            return 1
        cfg = Config.load(args.config)
        dire("Connexion a MT5...")
        try:
            api = mt5broker.connecter(cfg, exiger_trading=False)       # lecture seule
        except mt5broker.MT5Indisponible as exc:
            print("-" * 72)
            print(exc)
            print("-" * 72)
            return 2
        try:
            carte = api.construire_carte(I.INDICES)
            faux = ["%s -> %s" % (n, carte.get(n)) for n in I.INDICES if carte.get(n) != I.SYMBOLES[n]]
            if faux:
                print("Symboles inattendus (le protocole porte sur les CFD .cash) : " + " ; ".join(faux))
                return 2
            dire("Connecte. Telechargement des barres M30 (deja sur le disque depuis la sonde : "
                 "quelques secondes par indice).")
            lignes = telecharger(api._mt5, carte, BRUT, MANIFESTE)
        except RuntimeError as exc:
            print(exc)
            return 2
        finally:
            api.fermer()
        print()
        for l in lignes:
            print("  %-7s %7s barres, du %s au %s  sha256 %s" % (l["instrument"], l["barres"], l["premiere_barre"],
                                                                l["derniere_barre"], l["sha256"][:16]))
        print("Scelle dans %s." % _rel(MANIFESTE))
        print()
    res = controler(BRUT, MANIFESTE, TENDANCE, BRUT_TENDANCE)
    texte = rapport(res)
    if not res["problemes"]:
        CONTROLE.write_text(texte + "\n", encoding="utf-8")
    print(texte)
    if res["problemes"]:
        return 1
    if not res["heure"]["ok"]:
        print()
        print("Le controle de l'heure du serveur a echoue : rien n'est consigne. Le protocole arrete")
        print("l'etape 2 ici ; le test n'est pas lance.")
        return 3
    if not args.ecrire:
        print("Rien n'a ete consigne. Pour ecrire le tableau dans le protocole :")
        print("  python scripts/donnees_intraday.py --ecrire")
        return 0
    jour = jour_fr(datetime.now())
    doc = PROTOCOLE.read_bytes().decode("utf-8")
    PROTOCOLE.write_bytes(consigner(doc, bloc_protocole(res, jour), jour).encode("utf-8"))
    print("Tableau de l'etape 2 et date consignes dans %s." % _rel(PROTOCOLE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
