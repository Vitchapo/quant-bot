#!/usr/bin/env python3
"""Lance le backtest et produit le rapport HTML.

    python scripts/run_backtest.py --config config/us.yaml
    python scripts/run_backtest.py --config config/fr.yaml --no-regime
    python scripts/run_backtest.py --synthetic --sensitivity
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import datafeed, metrics, report
from quantbot.backtest import BenchmarkMissingError
from quantbot.config import Config
from quantbot.universe import UniverseError

warnings.filterwarnings("ignore", category=FutureWarning)


def load_prices(cfg, synthetic: bool):
    if synthetic:
        cfg.set("data.cache_dir", cfg.get("data.cache_dir").replace("prices/", "prices/synthetic_"))
        cfg.set("universe.benchmark", "^SYN")
    return datafeed.load_panel(cfg)


def _amical(exc) -> int:
    """Affiche une erreur de donnees manquantes sans trace d'appel."""
    print("\n" + "-" * 72)
    print(exc)
    print("-" * 72)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--benchmark", help="surcharge universe.benchmark "
                    "(ex : ^GSPC pour comparer a l'indice de prix)")
    ap.add_argument("--no-regime", action="store_true", help="desactive le filtre de regime")
    ap.add_argument("--top-n", type=int, help="surcharge portfolio.top_n")
    ap.add_argument("--start", help="date de debut (surcharge la config)")
    ap.add_argument("--sensitivity", action="store_true",
                    help="evalue toute la grille de parametres et mesure la dispersion")
    ap.add_argument("--output", help="chemin du rapport HTML")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    if args.benchmark:
        cfg.set("universe.benchmark", args.benchmark)
    if args.no_regime:
        cfg.set("regime.enabled", False)
    if args.top_n:
        cfg.set("portfolio.top_n", args.top_n)
    if args.start:
        cfg.set("data.start", args.start)

    prices = load_prices(cfg, args.synthetic)
    if args.start:
        prices = {t: df.loc[args.start:] for t, df in prices.items()}
    print(f"{len(prices)} series chargees\n")

    from quantbot.backtest import run_backtest
    result = run_backtest(prices, cfg, verbose=True)
    stats = metrics.compute(result, float(cfg.get("execution.risk_free_annual", 0.0)))

    print("\n" + "=" * 56)
    print(f"  BACKTEST - univers '{cfg.get('name')}'")
    print("=" * 56)
    print(metrics.format_report(stats))

    if args.sensitivity:
        from quantbot.validation import parameter_sensitivity
        print("\n" + "=" * 56)
        print("  SENSIBILITE AUX PARAMETRES (plein echantillon)")
        print("=" * 56)
        grid = parameter_sensitivity(prices, cfg)
        print("\n  Une strategie robuste reste correcte sur toute la grille.")
        print("  Si un seul reglage surnage, c'est du surajustement.")

    out = Path(args.output or f"reports/backtest_{cfg.get('name')}.html")
    report.save_report(result, stats, out,
                       title=f"Backtest multi-facteurs - univers {cfg.get('name').upper()}")
    print(f"\nRapport ecrit : {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BenchmarkMissingError, UniverseError, FileNotFoundError) as exc:
        sys.exit(_amical(exc))
