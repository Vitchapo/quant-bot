#!/usr/bin/env python3
"""Etape 1 du protocole du momentum intrajournalier : les spreads FTMO aux heures exactes.

    python scripts/couts_intraday.py            # releve et affiche ; n'ecrit rien
    python scripts/couts_intraday.py --ecrire   # ecrit aussi docs/intraday_couts.csv
                                                # et le tableau des couts dans le protocole

Ce que demande docs/protocole_momentum_intrajournalier.md ("Les couts FTMO") :
le spread retenu, par indice, pour l'entree et pour la sortie separement, est
le plus eleve de
  1. la mediane, sur les 60 dernieres seances de l'historique de ticks FTMO,
     du spread relatif median de la minute qui suit le debut de la fenetre
     (entree) ou qui precede sa fin (sortie) ;
  2. la valeur `transaction` de docs/tendance_couts.csv (c33bba4).
Sans historique de ticks, la seconde valeur seule.

Comment c'est mesure :
  * Les seances sont celles du calendrier du protocole : les cours quotidiens
    scelles a l'etape 2 du protocole de tendance (empreintes verifiees), qui
    s'arretent au 30 octobre 2026. On part de la plus recente et on remonte
    jusqu'a avoir 60 seances avec des ticks dans la minute, sans regarder plus
    de 120 seances. Si les 10 plus recentes n'ont aucun tick, l'historique de
    ticks est tenu pour indisponible. Le terminal telecharge les ticks d'un
    mois a la premiere demande : celle-la attend jusqu'a 15 secondes.
  * Ticks de cotation (copy_ticks_range, COPY_TICKS_INFO). Spread relatif d'un
    tick = (ask - bid) / milieu ; un tick sans cours valable est ecarte. Spread
    d'une minute = la mediane de ses ticks.
  * Heures : celles du serveur FTMO (New York + 7 h), changements d'heure des
    deux cotes compris (quantbot.intraday_donnees).
Lecture seule : aucun ordre, aucun cours de cloture lu, aucun rendement calcule.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

import numpy as np
import pandas as pd

import tester_tendance as TT        # lecture du protocole et des couts de tendance, deja commitee
from quantbot import intraday_donnees as I
from quantbot import mt5broker
from quantbot import tendance_donnees as TD
from quantbot.config import Config

RACINE = Path(__file__).resolve().parent.parent
PROTOCOLE = RACINE / "docs" / "protocole_momentum_intrajournalier.md"
TENDANCE = RACINE / "docs" / "protocole_tendance_multi_actifs.md"
COUTS_TENDANCE = RACINE / "docs" / "tendance_couts.csv"
SORTIE = RACINE / "docs" / "intraday_couts.csv"
BRUT_TENDANCE = RACINE / TD.BRUT
SEANCES_VOULUES = 60
SEANCES_MAX = 120
SANS_TICKS = 10             # les 10 seances les plus recentes sans aucun tick : pas d'historique
ESSAIS, PAUSE_S = 3, 0.5    # une demande sans reponse est refaite deux fois
#: La premiere demande d'un mois attend plus longtemps : le terminal telecharge
#: alors les ticks de tout le mois chez FTMO.
PATIENCE_S = (0.5, 1.0, 2.0, 4.0, 8.0)
COURTES = (PAUSE_S,) * (ESSAIS - 1)
MINUTE = pd.Timedelta(minutes=1)
AVANT = "### La période"
ENTETE = ("| indice | spread des ticks à l'entrée | spread des ticks à la sortie | séances relevées "
          "| tableau de tendance | retenu à l'entrée | retenu à la sortie |")
ETAPE_1 = "| 1 | coûts : relever les spreads FTMO"
CHAMPS = ["instrument", "entree", "sortie", "entree_ticks", "sortie_ticks", "seances_entree",
          "seances_sortie", "premiere_seance", "derniere_seance", "transaction_tendance"]
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre")


def dire(texte: str) -> None:
    print(texte, flush=True)


def _rel(p) -> str:
    """Le chemin a afficher : relatif au depot quand il y est."""
    try:
        return Path(p).resolve().relative_to(RACINE).as_posix()
    except ValueError:
        return str(p)


# -- le releve --------------------------------------------------------------------------
def minutes_mesurees(indice: str, seance) -> tuple:
    """((debut, fin) de la minute d'entree, (debut, fin) de la minute de sortie), heure du serveur."""
    d = I.debut_fenetre(indice, [seance])[0]
    return (d, d + MINUTE), (d + I.DUREE - MINUTE, d + I.DUREE)


def spread_minute(mt5, sym: str, debut, fin, attendre=time.sleep, pauses=COURTES):
    """Le spread relatif median des ticks de [debut, fin[ ; None s'il n'y en a aucun.

    Une reponse vide ou une erreur est redemandee apres chacune des `pauses`.
    """
    drapeau = getattr(mt5, "COPY_TICKS_INFO", 1)
    ticks = None
    for k in range(len(pauses) + 1):
        ticks = mt5.copy_ticks_range(sym, I.pour_mt5(debut), I.pour_mt5(fin), drapeau)
        if ticks is not None and len(ticks) > 0:
            break
        if k < len(pauses):
            attendre(pauses[k])
    if ticks is None or len(ticks) == 0:
        return None
    t = I.depuis_mt5(ticks["time_msc"], unite="ms")
    bid = np.asarray(ticks["bid"], dtype=float)
    ask = np.asarray(ticks["ask"], dtype=float)
    garde = np.asarray((t >= debut) & (t < fin)) & (bid > 0) & (ask >= bid)
    if not garde.any():
        return None
    return float(np.median(((ask - bid) / ((ask + bid) / 2.0))[garde]))


def relever_indice(mt5, indice: str, sym: str, seances, attendre=time.sleep, dire=dire) -> dict:
    """Les spreads des minutes d'entree et de sortie, de la seance la plus recente en remontant."""
    entree, sortie, regardees, mois_vus = [], [], 0, set()
    for seance in sorted(pd.DatetimeIndex(seances), reverse=True)[:SEANCES_MAX]:
        if len(entree) >= SEANCES_VOULUES and len(sortie) >= SEANCES_VOULUES:
            break
        if regardees == SANS_TICKS and not entree and not sortie:
            dire("    aucun tick sur les %d seances les plus recentes : historique indisponible" % SANS_TICKS)
            break
        regardees += 1
        (e1, e2), (s1, s2) = minutes_mesurees(indice, seance)
        pauses = COURTES if (seance.year, seance.month) in mois_vus else PATIENCE_S
        mois_vus.add((seance.year, seance.month))
        if len(entree) < SEANCES_VOULUES:
            x = spread_minute(mt5, sym, e1, e2, attendre, pauses)
            pauses = COURTES
            if x is not None:
                entree.append((seance, x))
        if len(sortie) < SEANCES_VOULUES:
            x = spread_minute(mt5, sym, s1, s2, attendre, pauses)
            if x is not None:
                sortie.append((seance, x))
        if regardees % 20 == 0:
            dire("    %d seances regardees (jusqu'au %s) : %d minutes d'entree, %d de sortie avec des ticks"
                 % (regardees, seance.strftime("%d/%m/%Y"), len(entree), len(sortie)))
    return {"entree": entree, "sortie": sortie, "regardees": regardees}


def resumer(rel: dict, transaction: float) -> dict:
    """Le spread retenu a l'entree et a la sortie : le plus eleve des ticks et du tableau."""
    def mediane(l):
        return float(np.median([x for _, x in l])) if l else None
    me, ms = mediane(rel["entree"]), mediane(rel["sortie"])
    jours = [s for s, _ in rel["entree"] + rel["sortie"]]
    return {"entree_ticks": me, "sortie_ticks": ms,
            "seances_entree": len(rel["entree"]), "seances_sortie": len(rel["sortie"]),
            "premiere": min(jours) if jours else None, "derniere": max(jours) if jours else None,
            "regardees": rel["regardees"], "transaction": float(transaction),
            "entree": float(transaction) if me is None else max(me, float(transaction)),
            "sortie": float(transaction) if ms is None else max(ms, float(transaction))}


# -- les sorties ------------------------------------------------------------------------
def ecrire_csv(res: dict, chemin: Path = SORTIE) -> None:
    def f(x, d=8):
        return "" if x is None else "%.*f" % (d, x)
    with open(str(chemin), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(CHAMPS)
        for n, r in res.items():
            w.writerow([n, f(r["entree"]), f(r["sortie"]), f(r["entree_ticks"]), f(r["sortie_ticks"]),
                        r["seances_entree"], r["seances_sortie"],
                        "" if r["premiere"] is None else r["premiere"].strftime("%Y-%m-%d"),
                        "" if r["derniere"] is None else r["derniere"].strftime("%Y-%m-%d"),
                        f(r["transaction"])])


def _pc(x: float, d: int = 4) -> str:
    return ("%.*f %%" % (d, 100 * x)).replace(".", ",")


def jour_fr(t) -> str:
    return "%d %s %d" % (t.day, MOIS[t.month - 1], t.year)


def bloc_protocole(res: dict, jour: str) -> str:
    """Le tableau des couts et ce qu'il faut savoir pour le lire, a inserer dans le protocole."""
    L = ["Relevé du %s dans MetaTrader 5 (essai gratuit FTMO), par" % jour,
         "`scripts/couts_intraday.py`. Minute d'entrée : la première de la fenêtre (de",
         "15:30 à 15:31 à New York pour US500) ; minute de sortie : la dernière (de",
         "15:59 à 16:00). Séances : les plus récentes du calendrier, qui s'arrête au",
         "30 octobre 2026. Une position acheteuse paie le spread retenu à l'entrée, une",
         "vendeuse celui de la sortie. Les fractions que lit le test sont dans",
         "`docs/intraday_couts.csv`.",
         "",
         ENTETE, "|---|---|---|---|---|---|---|"]
    for n, r in res.items():
        def ticks(x):
            return "pas de ticks" if x is None else _pc(x)
        if r["premiere"] is None:
            seances = "aucune : historique de ticks indisponible"
        else:
            seances = "%d à l'entrée, %d à la sortie, du %s au %s" % (
                r["seances_entree"], r["seances_sortie"], r["premiere"].strftime("%d/%m/%Y"),
                r["derniere"].strftime("%d/%m/%Y"))
        L.append("| %s | %s | %s | %s | %s | %s | %s |"
                 % (n, ticks(r["entree_ticks"]), ticks(r["sortie_ticks"]), seances,
                    _pc(r["transaction"]), _pc(r["entree"]), _pc(r["sortie"])))
    return "\n".join(L)


def consigner(texte: str, bloc: str, jour: str) -> str:
    """Insere le tableau avant "### La periode" et date l'etape 1. Rien d'autre."""
    crlf = "\r\n" in texte
    lignes = texte.replace("\r\n", "\n").split("\n")
    if ENTETE in lignes:
        raise ValueError("le tableau des couts est deja dans le protocole : l'etape 1 est faite")
    try:
        k = lignes.index(AVANT)
    except ValueError:
        raise ValueError("titre %r introuvable dans le protocole" % AVANT)
    i = next((i for i, l in enumerate(lignes) if l.startswith(ETAPE_1) and l.rstrip().endswith("| |")), None)
    if i is None:
        raise ValueError("ligne de l'etape 1 introuvable ou deja datee dans le protocole")
    lignes[i] = lignes[i].rstrip()[:-1] + jour + " |"
    lignes[k:k] = bloc.split("\n") + [""]
    texte = "\n".join(lignes)
    return texte.replace("\n", "\r\n") if crlf else texte


def afficher(res: dict) -> None:
    print()
    print("%-7s %12s %12s %9s %12s %12s %12s" % ("", "ticks entree", "ticks sortie", "seances",
                                                  "tendance", "retenu E", "retenu S"))
    for n, r in res.items():
        def p(x):
            return "-" if x is None else "%.4f%%" % (100 * x)
        print("%-7s %12s %12s %4d/%-4d %12s %12s %12s"
              % (n, p(r["entree_ticks"]), p(r["sortie_ticks"]), r["seances_entree"], r["seances_sortie"],
                 p(r["transaction"]), p(r["entree"]), p(r["sortie"])))


# -- le programme ------------------------------------------------------------------------
def preparer(protocole: Path = PROTOCOLE, tendance: Path = TENDANCE, brut: Path = BRUT_TENDANCE,
             couts: Path = COUTS_TENDANCE, ecrire: bool = False) -> tuple:
    """(calendrier, transactions, problemes) : tout ce qui se verifie avant MT5."""
    problemes = I.verifier_calendrier(TT.tableau_donnees(tendance.read_text(encoding="utf-8")), brut)
    trans, p = TT.lire_couts(couts, list(I.INDICES))
    problemes += p
    if ecrire and ENTETE in protocole.read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n"):
        problemes.append("le tableau des couts est deja dans le protocole : l'etape 1 est faite")
    if problemes:
        return {}, {}, problemes
    cal = I.calendrier(brut)
    return {n: s.index for n, s in cal.items()}, {n: c["transaction"] for n, c in trans.items()}, []


def relever(mt5, carte: dict, seances: dict, transactions: dict, attendre=time.sleep, dire=dire) -> dict:
    res = {}
    for n in I.INDICES:
        dire("%s (%s) :" % (n, carte[n]))
        try:
            mt5.symbol_select(carte[n], True)
        except Exception:
            pass
        rel = relever_indice(mt5, n, carte[n], seances[n], attendre, dire)
        res[n] = resumer(rel, transactions[n])
        dire("    %d minutes d'entree et %d de sortie avec des ticks, sur %d seances regardees"
             % (res[n]["seances_entree"], res[n]["seances_sortie"], rel["regardees"]))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ecrire", action="store_true",
                    help="ecrire docs/intraday_couts.csv et le tableau dans le protocole")
    ap.add_argument("--config", default=str(RACINE / "config" / "ftmo.yaml"))
    args = ap.parse_args(argv)
    os.chdir(RACINE)
    seances, transactions, problemes = preparer(PROTOCOLE, TENDANCE, BRUT_TENDANCE, COUTS_TENDANCE,
                                                ecrire=args.ecrire)
    if problemes:
        print("PAS PRET :")
        for p in problemes:
            print("  - " + p)
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
        dire("Connecte. Lecture des ticks, la plus recente seance d'abord (le terminal les "
             "telecharge a la demande : compter quelques minutes).")
        res = relever(api._mt5, carte, seances, transactions)
    finally:
        api.fermer()
    afficher(res)
    if not args.ecrire:
        print()
        print("Rien n'a ete ecrit. Pour consigner : python scripts/couts_intraday.py --ecrire")
        return 0
    ecrire_csv(res, SORTIE)
    texte = PROTOCOLE.read_bytes().decode("utf-8")
    jour = jour_fr(datetime.now())
    PROTOCOLE.write_bytes(consigner(texte, bloc_protocole(res, jour), jour).encode("utf-8"))
    print()
    print("Ecrit : %s ; tableau des couts et date de l'etape 1 dans %s."
          % (_rel(SORTIE), _rel(PROTOCOLE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
