#!/usr/bin/env python3
"""Telecharge et met en cache les cours de l'univers.

    python scripts/fetch_data.py --config config/us.yaml
    python scripts/fetch_data.py --config config/fr.yaml --force
    python scripts/fetch_data.py --synthetic          # sans reseau, pour essayer

Relancer ce script ne retelecharge que les jours manquants.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import datafeed
from quantbot.config import Config
from quantbot.universe import get_universe


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--force", action="store_true", help="ignore le cache et retelecharge tout")
    ap.add_argument("--synthetic", action="store_true",
                    help="genere des donnees synthetiques au lieu de telecharger")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )
    cfg = Config.load(args.config)

    if args.synthetic:
        from quantbot.synthetic import generate_prices
        cache = cfg.get("data.cache_dir", "data/prices").replace("prices/", "prices/synthetic_")
        print("Generation de donnees synthetiques (aucun acces reseau)...")
        prices = generate_prices(
            n_tickers=80,
            start=str(cfg.get("data.start", "2005-01-01")),
            end="2025-12-31",
            momentum_strength=0.55,
        )
        datafeed.save_panel(prices, cache)
        print(f"  {len(prices)} series ecrites dans {cache}/")
        print(f"  Lance ensuite : python scripts/run_backtest.py --config {args.config} --synthetic")
        return 0

    tickers = get_universe(cfg)
    benchmark = cfg.get("universe.benchmark")
    if benchmark:
        tickers = tickers + [benchmark]

    print(f"Univers '{cfg.get('name')}' : {len(tickers)} tickers "
          f"(dont l'indice {benchmark})")
    print(f"Periode demandee : {cfg.get('data.start')} -> {cfg.get('data.end') or 'aujourd hui'}")
    print(f"Fournisseur : {cfg.get('data.provider')}\n")

    prices = datafeed.fetch(cfg, tickers, force=args.force)

    missing = sorted(set(tickers) - set(prices))
    cache_dir = Path(cfg.get("data.cache_dir"))
    size_mb = sum(f.stat().st_size for f in cache_dir.glob("*.parquet")) / 1e6

    print(f"\n{len(prices)} series en cache ({size_mb:.1f} Mo dans {cache_dir}/)")
    if prices:
        last = max(df.index.max() for df in prices.values())
        print(f"Derniere date disponible : {last.date()}")
    if missing:
        print(f"\n{len(missing)} tickers sans donnees - a verifier ou retirer de "
              f"src/quantbot/universe.py :\n  {', '.join(missing)}")
    return 0 if prices else 1


if __name__ == "__main__":
    sys.exit(main())
