#!/usr/bin/env python3
"""Portefeuille cible et ordres a passer.

C'est la partie "production" : on calcule le portefeuille vise a partir des
dernieres donnees disponibles, on le compare aux positions detenues, et on
sort la liste des ordres.

    python scripts/daily_signals.py --config config/us.yaml
    python scripts/daily_signals.py --config config/us.yaml --positions data/positions.csv
    python scripts/daily_signals.py --config config/us.yaml --capital 25000 --force

Le fichier de positions est un CSV a deux colonnes : ticker,shares

Par defaut le script ne propose des ordres QUE les jours de rebalancement
prevus par la configuration. C'est volontaire : la discipline de calendrier
fait partie de la strategie, et reagir entre deux rebalancements est le
meilleur moyen de payer des frais pour rien.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import _bootstrap  # noqa: F401

import numpy as np
import pandas as pd

from quantbot import live
from quantbot.config import Config
from run_backtest import load_prices

warnings.filterwarnings("ignore", category=FutureWarning)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--positions", help="CSV des positions actuelles (ticker,shares)")
    ap.add_argument("--capital", type=float, help="capital total (defaut : celui de la config)")
    ap.add_argument("--force", action="store_true",
                    help="calcule les ordres meme si aujourd'hui n'est pas un jour de rebalancement")
    ap.add_argument("--output", help="CSV de sortie des ordres")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    prices = load_prices(cfg, args.synthetic)

    # Le calcul du portefeuille cible vit dans quantbot.live : c'est le MEME
    # code que celui utilise par scripts/trade.py pour envoyer les ordres au
    # courtier. Les faire diverger, ne serait-ce que d'un detail de calendrier,
    # produirait un bot qui n'execute pas ce que ce script affiche.
    cible = live.portefeuille_cible(prices, cfg)
    as_of = cible.as_of
    print(f"Derniere cloture disponible : {as_of.date()}  ({cible.anciennete_donnees} seance(s) manquante(s))")
    if cible.anciennete_donnees > 1 and not args.synthetic:
        print("  /!\\ Donnees anciennes : relance scripts/fetch_data.py avant de passer des ordres.")

    if not cible.est_jour_execution and not args.force:
        print(f"\nAujourd'hui n'est pas un jour d'execution "
              f"({cfg.get('execution.rebalance')}, decalage de "
              f"{cfg.get('execution.execution_lag')} jour(s)).")
        if cible.date_signal is not None:
            print(f"Derniere seance de signal examinee : {cible.date_signal.date()}")
        print("Fin de periode en cours : le signal se calculera a la derniere "
              "seance du mois.")
        print("Utilise --force pour calculer quand meme le portefeuille cible.")
        return 0

    if cible.est_jour_execution:
        print(f"Jour d'execution : signal du {cible.date_signal.date()}, "
              f"ordres a passer aujourd'hui.")
    print(f"Filtre de regime : {cible.regime_texte}")

    capital = float(args.capital or cfg.get("execution.initial_capital", 100_000.0))
    last_px = cible.cours

    target = cible.poids
    capital_txt = f"{capital:,.0f}".replace(",", " ")
    print(f"\n{'=' * 62}\n  PORTEFEUILLE CIBLE  ({len(target)} lignes, "
          f"capital {capital_txt})\n{'=' * 62}")
    if target.empty:
        print("  100% liquidites (aucune position ciblee).")
    else:
        print(f"  {'Ticker':<10}{'Poids':>8}{'Montant':>13}{'Cours':>10}{'Titres':>9}"
              f"{'Score':>8}")
        for ticker, weight in target.items():
            amount = weight * capital
            price = float(last_px.get(ticker, np.nan))
            shares = int(amount // price) if np.isfinite(price) and price > 0 else 0
            note = float(cible.scores.get(ticker, np.nan))
            print(f"  {ticker:<10}{weight:>7.2%}{amount:>13,.0f}{price:>10.2f}"
                  f"{shares:>9d}{note:>8.2f}".replace(",", " "))
    print(f"\n  Liquidites : {1 - target.sum():.1%}")

    # -- comparaison avec les positions detenues ---------------------------
    orders = pd.DataFrame()
    if args.positions:
        path = Path(args.positions)
        if not path.exists():
            print(f"\n/!\\ Fichier de positions introuvable : {path}")
        else:
            held = pd.read_csv(path).set_index("ticker")["shares"].astype(float)
            seuil = capital * 0.002    # en dessous, l'ordre coute plus qu'il ne rapporte
            rows, ignores, inconnus = [], [], []
            for ticker in sorted(set(held.index) | set(target.index)):
                price = float(last_px.get(ticker, np.nan))
                if not np.isfinite(price) or price <= 0:
                    inconnus.append(ticker)
                    continue
                cur = float(held.get(ticker, 0.0))
                tgt = int((target.get(ticker, 0.0) * capital) // price)
                delta = tgt - cur
                if delta == 0:
                    continue
                montant = abs(delta) * price
                if montant < seuil:
                    ignores.append((ticker, int(abs(delta)), montant))
                    continue
                rows.append({"ticker": ticker, "action": "ACHAT" if delta > 0 else "VENTE",
                             "shares": int(abs(delta)), "price": round(price, 2),
                             "amount": round(montant, 2),
                             "current": int(cur), "target": tgt})
            orders = pd.DataFrame(rows)
            print(f"\n{'=' * 62}\n  ORDRES A PASSER\n{'=' * 62}")
            if orders.empty:
                print("  Aucun ordre : le portefeuille est deja aligne.")
            else:
                print(orders.to_string(index=False))
                traded = orders["amount"].sum()
                print(f"\n  Montant echange : {traded:,.0f} ({traded / capital:.1%} du capital)"
                      .replace(",", " "))
                bps = (float(cfg.get("execution.commission_bps", 0)) +
                       float(cfg.get("execution.slippage_bps", 0)))
                print(f"  Cout estime : {traded * bps / 10000:,.0f} ({bps:.0f} pdb)".replace(",", " "))
            if ignores:
                print(f"\n  {len(ignores)} mouvement(s) sous le seuil de {seuil:,.0f} "
                      "et donc NON executes :".replace(",", " "))
                for ticker, qte, montant in ignores:
                    print(f"    {ticker:<10} {qte:>6} titres  ({montant:,.2f})".replace(",", " "))
                print("    Ces lignes restent en portefeuille : a solder a la main si besoin.")
            if inconnus:
                print(f"\n  /!\\ {len(inconnus)} ticker(s) detenu(s) sans cours disponible : "
                      f"{', '.join(inconnus)}")

    out = Path(args.output or f"reports/signals_{cfg.get('name')}_{as_of:%Y%m%d}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    # `Series.reset_index(names=...)` n'existe pas selon les versions de pandas :
    # on renomme l'axe avant, ce qui marche partout. Sans cela le script
    # plantait a la toute derniere ligne des qu'on le lancait SANS --positions,
    # c'est-a-dire dans le cas le plus courant - apres avoir tout affiche, donc
    # sans que le CSV promis soit jamais ecrit.
    frame = (orders if not orders.empty
             else target.rename("weight").rename_axis("ticker").reset_index())
    frame.to_csv(out, index=False)
    print(f"\nEcrit : {out}")
    print("\nRappel : ces signaux sortent d'un backtest sur donnees passees, "
          "biaisees par la survie des societes. Ce n'est pas un conseil en investissement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
