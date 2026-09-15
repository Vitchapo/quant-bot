#!/usr/bin/env python3
"""Validation walk-forward : le seul chiffre a peu pres honnete du projet.

    python scripts/walk_forward.py --config config/us.yaml
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import metrics, report
from quantbot.backtest import BenchmarkMissingError
from quantbot.config import Config
from quantbot.universe import UniverseError
from run_backtest import load_prices

warnings.filterwarnings("ignore", category=FutureWarning)


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
    ap.add_argument("--output", help="chemin du rapport HTML")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    if args.benchmark:
        cfg.set("universe.benchmark", args.benchmark)
    prices = load_prices(cfg, args.synthetic)
    print(f"{len(prices)} series chargees\n")

    from quantbot.backtest import run_backtest
    from quantbot.validation import walk_forward

    wf = walk_forward(prices, cfg, verbose=True)

    print("\n" + "=" * 56)
    print("  PERFORMANCE HORS ECHANTILLON (fenetres de test recollees)")
    print("=" * 56)
    print(metrics.format_report(wf.oos_stats))

    full = run_backtest(prices, cfg)
    full_stats = metrics.compute(full, float(cfg.get("execution.risk_free_annual", 0.0)))
    gap = full_stats.get("sharpe", 0) - wf.oos_stats.get("sharpe", 0)
    print(f"\n  Sharpe plein echantillon : {full_stats.get('sharpe', 0):.2f}")
    print(f"  Sharpe hors echantillon  : {wf.oos_stats.get('sharpe', 0):.2f}")
    print(f"  Ecart                    : {gap:+.2f}")
    if gap > 0.5:
        print("\n  /!\\ L'ecart est important : une bonne part de la performance en")
        print("      plein echantillon vient du choix des parametres, pas d'un edge.")

    out = Path(args.output or f"reports/walkforward_{cfg.get('name')}.html")
    report.save_report(full, full_stats, out,
                       title=f"Walk-forward - univers {cfg.get('name').upper()}",
                       walk_forward=wf)
    print(f"\nRapport ecrit : {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BenchmarkMissingError, UniverseError, FileNotFoundError) as exc:
        sys.exit(_amical(exc))
