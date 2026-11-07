#!/usr/bin/env python3
"""Sonde : quelle profondeur d'historique intrajournalier le serveur FTMO donne-t-il ?

    python scripts/sonde_intraday.py

Avant d'ecrire le protocole du momentum intrajournalier, il faut savoir sur
quelle periode on pourra le tester. Ce script demande au terminal MT5
(compte FTMO de demonstration, lecture seule : aucun ordre) les barres de
30 minutes des 6 indices. Il regarde ensuite si les barres de 5 minutes et
d'une minute remontent aussi loin.

Il n'affiche aucun cours et ne calcule aucun rendement : seulement des dates
et des nombres de barres. Il ecrit ces comptes dans data/intraday/sonde_mt5.csv.

Le terminal ne rend jamais plus de barres que sa limite "Max bars in chart"
(Outils > Options > Graphiques). Si un compte tombe pile sur cette limite,
c'est le terminal qui tronque, pas le serveur : mettre la limite sur
"Unlimited", redemarrer MT5 et relancer la sonde.

Les dates sont celles du serveur FTMO (UTC+2, UTC+3 l'ete), telles que MT5
les donne : a quelques heures pres, sans importance ici.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import mt5broker
from quantbot.config import Config

RACINE = Path(__file__).resolve().parent.parent
SORTIE = RACINE / "data" / "intraday" / "sonde_mt5.csv"
INDICES = ["US500", "GER40", "UK100", "JP225", "AUS200", "HK50"]
PLAFOND_M30 = 500_000         # 40 ans de barres de 30 minutes, 23 h sur 24 : jamais atteint
ESSAIS, PAUSE_S = 60, 5.0     # le terminal telecharge l'historique a la demande
STABLE = 3                    # lectures identiques de suite : telechargement fini
ESSAIS_TAILLE = 2             # lectures vides avant de demander moitie moins de barres
MINIMUM = 1000                # la plus petite demande essayee
BUDGET_S = 240                # par indice ; au-dela, on note ou on en est et on passe au suivant
JOURS_DEBUT = 5               # jours regardes au debut de l'historique, en M5 et en M1
DEBUT_PLAGE = datetime(2000, 1, 1, tzinfo=timezone.utc)
COLONNES = ["sonde_utc", "instrument", "symbole", "limite_terminal", "barres_m30",
            "premiere_m30", "derniere_m30", "tronque", "m5_au_debut", "m1_au_debut", "note"]


def _date(t) -> datetime:
    return datetime.fromtimestamp(int(t), tz=timezone.utc)


def dire(texte: str) -> None:
    """Affiche tout de suite : la premiere sonde peut durer plusieurs minutes."""
    print(texte, flush=True)


def _barres_m30(mt5, sym: str, limite: int, maintenant: datetime, attendre, horloge, dire) -> tuple:
    """(barres, fini, demande, mode) : les barres M30 que le terminal rend.

    Trois etapes.
      1. Toute la plage depuis 2000 (copy_rates_range) : pas de nombre de
         barres a deviner.
      2. Si le terminal ne rend rien, un nombre de barres (copy_rates_from_pos),
         divise par deux jusqu'a MINIMUM. Le 07/11, 100 000 barres demandees
         ont rendu "Invalid params" pour chaque indice.
      3. La fin du telechargement : la premiere fois, le terminal va chercher
         des annees d'historique minute chez FTMO, et chaque appel ne rend que
         ce qui est deja arrive. On redemande jusqu'a lire STABLE fois de
         suite le meme nombre de barres, dans la limite de BUDGET_S secondes.
         L'historique telecharge reste sur le disque : une sonde relancee
         repart de la.
    """
    t0 = horloge()
    etat = {"essai": 0, "demande": limite, "mode": "plage"}

    def lire():
        etat["essai"] += 1
        if etat["mode"] == "plage":
            b = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M30, DEBUT_PLAGE, maintenant)
            quoi = "depuis %d" % DEBUT_PLAGE.year
        else:
            b = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M30, 0, etat["demande"])
            quoi = "sur %d demandees" % etat["demande"]
        n = 0 if b is None else len(b)
        erreur = "" if n else " (MT5 : %s)" % (mt5.last_error(),)
        dire("    lecture %d : %d barres M30 %s, %.0f s%s" % (etat["essai"], n, quoi, horloge() - t0, erreur))
        return b, n

    def premiere_reponse():
        n, b = 0, None
        for _ in range(ESSAIS_TAILLE):
            b, n = lire()
            if n:
                break
            attendre(PAUSE_S)
        return b, n

    # 1. toute la plage
    barres, n = premiere_reponse()
    # 2. sinon, un nombre de barres que le terminal accepte
    if not n:
        etat["mode"] = "nombre"
        while True:
            barres, n = premiere_reponse()
            if n or etat["demande"] <= MINIMUM:
                break
            etat["demande"] = max(MINIMUM, etat["demande"] // 2)
    if not n:
        return barres, True, etat["demande"], etat["mode"]
    # 3. la fin du telechargement
    lus = [n]
    while etat["essai"] < ESSAIS:
        if len(lus) >= STABLE and len(set(lus[-STABLE:])) == 1:
            return barres, True, etat["demande"], etat["mode"]
        if horloge() - t0 > BUDGET_S:
            return barres, False, etat["demande"], etat["mode"]
        attendre(PAUSE_S)
        b, n = lire()
        if n:
            barres = b
        lus.append(n)
    return barres, False, etat["demande"], etat["mode"]


def sonder(mt5, carte: dict, maintenant: datetime = None, attendre=time.sleep,
           horloge=time.monotonic, dire=dire) -> list:
    """Une ligne par indice : profondeur de l'historique, rien d'autre."""
    maintenant = maintenant or datetime.now(timezone.utc)
    info = mt5.terminal_info()
    limite_terminal = int(getattr(info, "maxbars", 0) or 0)
    limite = min(limite_terminal, PLAFOND_M30) if limite_terminal > 0 else PLAFOND_M30
    dire("Limite du terminal (Max bars in chart) : %s barres" % (limite_terminal or "inconnue"))
    lignes = []
    for nom in INDICES:
        sym = carte.get(nom)
        ligne = {c: "" for c in COLONNES}
        ligne.update(sonde_utc=maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"), instrument=nom,
                     symbole=sym or "", limite_terminal=limite_terminal, barres_m30=0)
        lignes.append(ligne)
        if not sym:
            ligne["note"] = "absent du catalogue"
            continue
        dire("%s (%s) :" % (nom, sym))
        try:
            mt5.symbol_select(sym, True)
        except Exception:
            pass
        barres, fini, demande, mode = _barres_m30(mt5, sym, limite, maintenant, attendre, horloge, dire)
        n = 0 if barres is None else len(barres)
        ligne["barres_m30"] = n
        if not fini:
            ligne["note"] = ("telechargement pas fini apres %d s : relancer la sonde, "
                             "elle repartira de la" % BUDGET_S)
        if n == 0:
            ligne["note"] = ligne["note"] or ("aucune barre rendue, meme pour %d demandees (MT5 : %s)"
                                              % (demande, mt5.last_error(),))
            continue
        if mode == "nombre" and demande < limite:
            ligne["note"] = (ligne["note"] + " ; " if ligne["note"] else "") + (
                "le terminal n'a accepte que %d barres par demande" % demande)
        debut, fin = _date(barres["time"][0]), _date(barres["time"][-1])
        ligne.update(premiere_m30=debut.strftime("%Y-%m-%d"), derniere_m30=fin.strftime("%Y-%m-%d"),
                     tronque="oui" if n >= demande else "non")
        for champ, tf in (("m5_au_debut", mt5.TIMEFRAME_M5), ("m1_au_debut", mt5.TIMEFRAME_M1)):
            b = mt5.copy_rates_range(sym, tf, debut, debut + timedelta(days=JOURS_DEBUT))
            ligne[champ] = "oui" if b is not None and len(b) > 0 else "non"
    return lignes


def ecrire(lignes: list, chemin: Path = SORTIE) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLONNES)
        w.writeheader()
        w.writerows(lignes)


def afficher(lignes: list) -> None:
    if not lignes:
        return
    print()
    print("Resume :")
    for l in lignes:
        if l["premiere_m30"]:
            print("  %-7s %-12s M30 : %7d barres, du %s au %s%s ; M5 au debut : %s ; M1 au debut : %s"
                  % (l["instrument"], l["symbole"], l["barres_m30"], l["premiere_m30"], l["derniere_m30"],
                     "  (TRONQUE PAR LE TERMINAL)" if l["tronque"] == "oui" else "",
                     l["m5_au_debut"], l["m1_au_debut"]))
        if l["note"]:
            print("  %-7s %-12s %s" % (l["instrument"], l["symbole"] or "-", l["note"]))
    print()
    print("Si des dates semblent trop recentes, relancer la sonde : MT5 telecharge parfois")
    print("l'historique en plusieurs fois.")
    if any(l["tronque"] == "oui" for l in lignes):
        print()
        print("Historique tronque par le terminal : dans MT5, Outils > Options > Graphiques,")
        print("mettre \"Max bars in chart\" sur Unlimited, redemarrer MT5, relancer la sonde.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(RACINE / "config" / "ftmo.yaml"))
    args = ap.parse_args(argv)
    os.chdir(RACINE)
    cfg = Config.load(args.config)
    dire("Connexion a MT5...")
    try:
        # Lecture seule : la case "Algo Trading" du terminal n'est pas exigee.
        api = mt5broker.connecter(cfg, exiger_trading=False)
    except mt5broker.MT5Indisponible as exc:
        print("-" * 72)
        print(exc)
        print("-" * 72)
        return 2
    dire("Connecte. La premiere fois, MT5 telecharge des annees d'historique : "
         "compter quelques minutes par indice.")
    try:
        carte = api.construire_carte(INDICES)
        lignes = sonder(api._mt5, carte)
    finally:
        api.fermer()
    ecrire(lignes)
    afficher(lignes)
    print()
    print("Comptes ecrits dans %s" % SORTIE.relative_to(RACINE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
