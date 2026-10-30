#!/usr/bin/env python3
"""Etape 1 du protocole de tendance : releve les conditions FTMO dans MT5.

    python scripts/releve_ftmo.py               # un releve des 15 instruments, ajoute au journal
    python scripts/releve_ftmo.py --synthese    # ou en est-on : jours couverts, spread retenu
    python scripts/releve_ftmo.py --installer   # tache Windows : un releve toutes les heures
    python scripts/releve_ftmo.py --retirer     # supprime la tache

Ce que demande docs/protocole_tendance_multi_actifs.md, pour chaque
instrument : nom exact, taille du contrat, spread, commission, swap acheteur
et vendeur avec leur unite, jour du swap triple, levier. Spread retenu : le
plus eleve de trois releves faits trois jours differents, marche sous-jacent
ouvert.

Pourquoi toutes les heures : les marches sous-jacents ne sont pas ouverts en
meme temps. Tokyo, Hong Kong et Sydney cotent pendant la nuit de Paris. Un
releve par heure, trois jours de semaine, couvre chaque marche a ses heures.
Le releve d'un JOUR est le spread median des heures ou son marche est ouvert
(les pointes d'ouverture et de fermeture ne disent rien du cout habituel) ;
le spread retenu est le plus eleve des trois premiers jours couverts.

Un marche est tenu pour ouvert quand sa derniere cotation date de moins de
deux minutes. L'heure du serveur n'etant pas celle de la tour, on la deduit
de la cotation la plus recente du releve (le change cote 24 h sur 24 en
semaine), a l'heure ronde pres.

Ce que le terminal ne donne PAS, et qu'il faut lire sur le site de FTMO : la
commission, et ce que FTMO fait des dividendes sur ses CFD d'indices.

Lecture seule : aucun ordre n'est envoye. Le compte doit etre de
demonstration (l'essai gratuit l'est) : `mt5broker.connecter` refuse un
compte reel.
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import mt5broker
from quantbot.config import Config

RACINE = Path(__file__).resolve().parent.parent
JOURNAL = RACINE / "data" / "tendance" / "releves_ftmo.csv"
NOM_TACHE = "quantbot-releve-ftmo"
#: Les 15 instruments du protocole, sous leur nom FTMO sans suffixe.
INSTRUMENTS = ["US500", "GER40", "UK100", "JP225", "AUS200", "HK50",
               "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
               "XAUUSD", "XAGUSD"]
FRAICHEUR_S = 120            # une cotation plus vieille : marche ferme
JOURS_VOULUS = 3
#: SYMBOL_SWAP_MODE de MetaTrader 5 : l'unite des swaps.
MODES_SWAP = {0: "desactive", 1: "points", 2: "devise de base", 3: "devise de marge",
              4: "devise du compte", 5: "% par an (cours actuel)",
              6: "% par an (cours d'ouverture)", 7: "points (reouverture au cours actuel)",
              8: "points (reouverture au bid)"}
JOURS_SEMAINE = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"]
COLONNES = ["releve_utc", "instrument", "symbole", "ouvert", "age_s", "bid", "ask",
            "spread_prix", "spread_relatif", "spread_points", "point", "digits", "contrat",
            "devise_base", "devise_profit", "devise_marge", "swap_mode", "swap_unite",
            "swap_achat", "swap_vente", "swap_triple", "marge_1_lot", "valeur_tick",
            "taille_tick", "notionnel_compte", "levier", "heure_tick_serveur",
            "decalage_serveur_h"]


def _num(objet, champ, defaut=None):
    v = getattr(objet, champ, defaut)
    try:
        return float(v)
    except (TypeError, ValueError):
        return defaut


def releve(mt5, carte: dict, maintenant: datetime = None) -> list:
    """Un releve : une ligne par instrument trouve au catalogue."""
    maintenant = maintenant or datetime.now(timezone.utc)
    lignes = []
    for nom in INSTRUMENTS:
        sym = carte.get(nom)
        if not sym:
            continue
        try:
            mt5.symbol_select(sym, True)          # sans cela, pas de cotation
        except Exception:
            pass
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if info is None or tick is None:
            continue
        bid, ask = _num(tick, "bid", 0.0), _num(tick, "ask", 0.0)
        valeur_tick, taille_tick = _num(info, "trade_tick_value"), _num(info, "trade_tick_size")
        notionnel = (ask * valeur_tick / taille_tick
                     if valeur_tick and taille_tick and ask else None)
        try:
            marge = mt5.order_calc_margin(getattr(mt5, "ORDER_TYPE_BUY", 0), sym, 1.0, ask)
        except Exception:
            marge = None
        mode = int(_num(info, "swap_mode", -1) or 0)
        triple = getattr(info, "swap_rollover3days", None)
        lignes.append({
            "releve_utc": maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "instrument": nom, "symbole": sym, "bid": bid, "ask": ask,
            "spread_prix": ask - bid if ask and bid else None,
            "spread_relatif": (ask - bid) / ((ask + bid) / 2.0) if ask and bid else None,
            "spread_points": _num(info, "spread"), "point": _num(info, "point"),
            "digits": _num(info, "digits"), "contrat": _num(info, "trade_contract_size"),
            "devise_base": getattr(info, "currency_base", ""),
            "devise_profit": getattr(info, "currency_profit", ""),
            "devise_marge": getattr(info, "currency_margin", ""),
            "swap_mode": mode, "swap_unite": MODES_SWAP.get(mode, "inconnu (%s)" % mode),
            "swap_achat": _num(info, "swap_long"), "swap_vente": _num(info, "swap_short"),
            "swap_triple": JOURS_SEMAINE[int(triple)] if triple is not None and 0 <= int(triple) <= 6 else "",
            "marge_1_lot": marge, "valeur_tick": valeur_tick, "taille_tick": taille_tick,
            "notionnel_compte": notionnel,
            "levier": notionnel / marge if notionnel and marge else None,
            "heure_tick_serveur": _num(tick, "time", 0.0),
        })
    return marquer_ouverts(lignes, maintenant)


def marquer_ouverts(lignes: list, maintenant: datetime) -> list:
    """Ouvert = cotation de moins de deux minutes, a l'heure du SERVEUR.

    L'heure du serveur se deduit de la cotation la plus recente, a l'heure
    ronde pres ; si meme celle-ci n'est pas fraiche (week-end, nuit du
    vendredi), tout est ferme.
    """
    if not lignes:
        return lignes
    local = maintenant.timestamp()
    ref = max(l["heure_tick_serveur"] for l in lignes)
    decalage = round((ref - local) / 3600.0)
    reference_fraiche = abs(ref - (local + 3600.0 * decalage)) <= FRAICHEUR_S
    for l in lignes:
        age = (local + 3600.0 * decalage) - l["heure_tick_serveur"]
        l["age_s"] = round(age, 1)
        l["decalage_serveur_h"] = decalage
        l["ouvert"] = bool(reference_fraiche and age <= FRAICHEUR_S and l["spread_prix"])
    return lignes


def ecrire(lignes: list, chemin: Path = JOURNAL) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    neuf = not chemin.exists()
    with chemin.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLONNES)
        if neuf:
            w.writeheader()
        for l in lignes:
            w.writerow({k: l.get(k, "") for k in COLONNES})


def synthese(chemin: Path = JOURNAL) -> dict:
    """Par instrument : jours couverts, releve de chaque jour, spread retenu."""
    if not chemin.exists():
        return {}
    par = {}
    with chemin.open(encoding="utf-8") as fh:
        for l in csv.DictReader(fh):
            par.setdefault(l["instrument"], []).append(l)
    out = {}
    for nom in INSTRUMENTS:
        lignes = par.get(nom, [])
        ouvertes = [l for l in lignes if l["ouvert"] == "True" and l["spread_prix"]]
        par_jour = {}
        for l in ouvertes:
            par_jour.setdefault(l["releve_utc"][:10], []).append(l)
        jours = sorted(par_jour)[:JOURS_VOULUS]
        quotidiens = [(j, statistics.median(float(l["spread_prix"]) for l in par_jour[j]),
                       statistics.median(float(l["spread_relatif"]) for l in par_jour[j]))
                      for j in jours]
        dernier = lignes[-1] if lignes else {}
        out[nom] = {
            "symbole": dernier.get("symbole", ""),
            "releves": len(lignes), "releves_ouverts": len(ouvertes),
            "jours": [j for j, _, _ in quotidiens],
            "complet": len(quotidiens) >= JOURS_VOULUS,
            "spread_retenu": max((s for _, s, _ in quotidiens), default=None),
            "spread_relatif_retenu": max((r for _, _, r in quotidiens), default=None),
            "contrat": dernier.get("contrat"), "swap_unite": dernier.get("swap_unite"),
            "swap_achat": dernier.get("swap_achat"), "swap_vente": dernier.get("swap_vente"),
            "swap_triple": dernier.get("swap_triple"), "levier": dernier.get("levier"),
        }
    return out


def afficher_synthese(s: dict) -> int:
    if not s:
        print("Aucun releve dans %s." % JOURNAL)
        return 1
    print("%-7s %-12s %4s %8s  %-32s %14s  %s"
          % ("", "nom MT5", "jours", "ouverts", "spread retenu (relatif)", "levier", "swaps"))
    for nom, v in s.items():
        rel = v["spread_relatif_retenu"]
        lev = v["levier"]
        print("%-7s %-12s %4d/%d %8d  %-32s %14s  achat %s / vente %s (%s), triple le %s"
              % (nom, v["symbole"] or "ABSENT", len(v["jours"]), JOURS_VOULUS, v["releves_ouverts"],
                 ("%.6g (%.4f %%)" % (v["spread_retenu"], 100 * rel)) if rel is not None else "-",
                 ("1:%.0f" % float(lev)) if lev not in (None, "") else "-",
                 v["swap_achat"], v["swap_vente"], v["swap_unite"], v["swap_triple"]))
    manque = [n for n, v in s.items() if not v["complet"]]
    print()
    if manque:
        print("Pas encore %d jours de marche ouvert pour : %s" % (JOURS_VOULUS, ", ".join(manque)))
        print("Laisse tourner la tache (terminal MT5 ouvert et connecte).")
        return 1
    print("Les %d instruments ont leurs %d jours : le releve est complet." % (len(s), JOURS_VOULUS))
    print("Retire la tache : python scripts/releve_ftmo.py --retirer")
    print("Il reste a lire sur le site de FTMO la commission et le traitement des dividendes.")
    return 0


def commande_tache(python: str = None) -> list:
    python = python or sys.executable
    script = str(Path(__file__).resolve())
    return ["schtasks", "/Create", "/TN", NOM_TACHE, "/SC", "HOURLY", "/MO", "1", "/F",
            "/TR", '"%s" "%s"' % (python, script)]


def installer() -> int:
    cmd = commande_tache()
    if os.name != "nt":
        print("Pas sous Windows : la tache ne peut pas etre enregistree ici. Commande :")
        print("  " + " ".join(cmd))
        return 0
    r = subprocess.run(cmd, capture_output=True, text=True)
    print((r.stdout or "") + (r.stderr or ""))
    q = subprocess.run(["schtasks", "/Query", "/TN", NOM_TACHE], capture_output=True, text=True)
    if q.returncode != 0:
        print("ECHEC : la tache n'apparait pas dans le planificateur.")
        return 2
    print("VERIFIE : un releve toutes les heures. Garde le terminal MT5 ouvert et connecte.")
    print("Suivi : python scripts/releve_ftmo.py --synthese")
    return 0


def retirer() -> int:
    if os.name != "nt":
        print("Pas sous Windows : rien a retirer ici.")
        return 0
    r = subprocess.run(["schtasks", "/Delete", "/TN", NOM_TACHE, "/F"], capture_output=True, text=True)
    print((r.stdout or "") + (r.stderr or ""))
    return 0 if r.returncode == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(RACINE / "config" / "ftmo.yaml"))
    ap.add_argument("--synthese", action="store_true")
    ap.add_argument("--installer", action="store_true")
    ap.add_argument("--retirer", action="store_true")
    args = ap.parse_args()
    os.chdir(RACINE)
    if args.synthese:
        return afficher_synthese(synthese())
    if args.installer:
        return installer()
    if args.retirer:
        return retirer()

    cfg = Config.load(args.config)
    try:
        api = mt5broker.connecter(cfg)
    except mt5broker.MT5Indisponible as exc:
        print("-" * 72); print(exc); print("-" * 72)
        return 2
    try:
        carte = api.construire_carte(INSTRUMENTS)
        lignes = releve(api._mt5, carte)
    finally:
        api.fermer()
    ecrire(lignes)
    ouverts = [l["instrument"] for l in lignes if l["ouvert"]]
    absents = [n for n in INSTRUMENTS if n not in carte]
    print("%s : %d instrument(s) releve(s), %d marche(s) ouvert(s) : %s"
          % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(lignes), len(ouverts), " ".join(ouverts)))
    if absents:
        print("ABSENTS du catalogue : %s" % " ".join(absents))
    return 0


if __name__ == "__main__":
    sys.exit(main())
