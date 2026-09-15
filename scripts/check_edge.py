#!/usr/bin/env python3
"""Le signal contient-il de l'information ? Trois mesures independantes.

    python scripts/check_edge.py --config config/us.yaml
    python scripts/check_edge.py --config config/us.yaml --start 2010-01-01
    python scripts/check_edge.py --synthetic --draws 100

A lire dans cet ordre : si l'IC du composite est nul, inutile de regarder le
reste. Si le null aleatoire place la strategie sous la mediane du hasard, le
score coute de l'argent. Si la decomposition montre un delta nul sur la ligne
"selection factorielle", la selection ne selectionne rien.
"""
from __future__ import annotations

import argparse
import sys
import warnings

import _bootstrap  # noqa: F401

from quantbot import datafeed, edge
from quantbot.backtest import BenchmarkMissingError
from quantbot.config import Config
from quantbot.universe import UniverseError

warnings.filterwarnings("ignore", category=FutureWarning)


def _pct(x, width=8):
    return "n/a".rjust(width) if x is None or x != x else ("%.2f%%" % (100 * x)).rjust(width)


def _num(x, width=7, dec=2):
    return "n/a".rjust(width) if x is None or x != x else (("%." + str(dec) + "f") % x).rjust(width)


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
    ap.add_argument("--start", help="ne mesurer qu'a partir de cette date")
    ap.add_argument("--draws", type=int, default=200,
                    help="nombre de tirages pour le null aleatoire (defaut 200)")
    ap.add_argument("--budget", type=float, default=180.0,
                    help="temps maximal accorde au null, en secondes")
    ap.add_argument("--no-null", action="store_true", help="saute le null aleatoire")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    if args.benchmark:
        cfg.set("universe.benchmark", args.benchmark)
    if args.synthetic:
        cfg.set("data.cache_dir", cfg.get("data.cache_dir").replace("prices/", "prices/synthetic_"))
        cfg.set("universe.benchmark", "^SYN")
    prices = datafeed.load_panel(cfg)
    print("%d series chargees depuis %s\n" % (len(prices), cfg.get("data.cache_dir")))

    # -- 1. information coefficient ---------------------------------------
    print("=" * 92)
    print("  1. INFORMATION COEFFICIENT")
    print("     Correlation de rang entre le score du jour de signal et le rendement")
    print("     realise jusqu'au rebalancement suivant. |t| > 2 = signal credible.")
    print("=" * 92)
    ic = edge.information_coefficient(prices, cfg, start=args.start)
    print("  %-22s %9s %9s %8s %9s %9s %7s" % (
        "facteur", "IC moyen", "IC median", "t", "p", "% > 0", "n"))
    for _, r in ic.iterrows():
        verdict = "  <-- signal" if abs(r["t_stat"]) > 2 else ""
        print("  %-22s %9.4f %9.4f %8.2f %9.3f %8.1f%% %7d%s" % (
            r["facteur"], r["ic_moyen"], r["ic_median"], r["t_stat"], r["p_value"],
            100 * r["part_positive"], r["n_dates"], verdict))
    if not (ic["t_stat"].abs() > 2).any():
        print("\n  Aucun facteur ne depasse |t| = 2 : sur cet univers et cette periode,")
        print("  rien ne distingue ces scores d'un classement au hasard.")

    # -- 2. decomposition ---------------------------------------------------
    print("\n" + "=" * 92)
    print("  2. DECOMPOSITION : quelle brique apporte quelque chose ?")
    print("     Chaque ligne ajoute UNE brique a la precedente.")
    print("=" * 92)
    dec = edge.decomposition(prices, cfg, start=args.start)
    print("  %-46s %8s %7s %8s %9s %9s" % (
        "etape", "CAGR", "Sharpe", "maxDD", "d.CAGR", "d.Sharpe"))
    for _, r in dec.iterrows():
        print("  %-46s %s %s %s %s %s" % (
            r["etape"][:46], _pct(r["cagr"]), _num(r["sharpe"]), _pct(r["max_drawdown"]),
            _pct(r["delta_cagr"], 9), _num(r["delta_sharpe"], 9)))

    # -- 3. le score classe-t-il vraiment ? ---------------------------------
    print("\n" + "=" * 92)
    print("  3. LE SCORE CLASSE-T-IL, OU TROUVE-T-IL SEULEMENT LES EXTREMES ?")
    print("     Rendement mensuel moyen par decile, en ecart a l'univers entier.")
    print("=" * 92)
    dec = edge.decile_returns(prices, cfg, start=args.start)
    if dec.get("spread"):
        print("  univers entier : %.3f%%/mois sur %d dates\n" % (100 * dec["univers"], dec["n_dates"]))
        print("  %-26s %11s %10s %8s" % ("", "rdt/mois", "ecart", "t"))
        for d in dec["deciles"]:
            marque = "  <--" if abs(d["t_stat"]) > 2 else ""
            nom = "decile %d" % d["rang"]
            if d["rang"] == 1:
                nom += " (pires scores)"
            elif d["rang"] == dec["n_buckets"]:
                nom += " (meilleurs)"
            print("  %-26s %10.3f%% %+9.3f%% %8.2f%s"
                  % (nom, 100 * d["rendement"], 100 * d["ecart"], d["t_stat"], marque))
        for cle, nom in (("meilleurs", "les %d meilleurs scores" % dec["top_n"]),
                         ("pires", "les %d pires scores" % dec["top_n"])):
            d = dec[cle]
            print("  %-26s %10.3f%% %+9.3f%% %8.2f%s"
                  % (nom, 100 * d["rendement"], 100 * d["ecart"], d["t_stat"],
                     "  <--" if abs(d["t_stat"]) > 2 else ""))
        sp = dec["spread"]
        print("\n  ECART MEILLEURS - PIRES : %+.3f%%/mois, t = %.2f, p = %.3f"
              % (100 * sp["moyenne"], sp["t_stat"], sp["p_value"]))
        if abs(sp["t_stat"]) < 2:
            print("  Cet ecart n'est pas significatif : le score ne CLASSE pas.")
            print("  Si les deux bouts du classement surperforment le milieu, un top 20")
            print("  brillant ne prouve rien - il selectionne des titres extremes, pas")
            print("  des gagnants. Sur un univers de survivants, les extremes qui ont")
            print("  survecu sont precisement ceux qui sont montes.")

        print("\n  Verification par l'absurde : on retourne le score et on relance.")
        inv = edge.inverted_score(prices, cfg, start=args.start)
        print("  %-34s %9s %8s %10s" % ("", "CAGR", "Sharpe", "maxDD"))
        for cle, nom in (("normal", "score normal (meilleurs scores)"),
                         ("inverse", "score INVERSE (pires scores)"),
                         ("univers", "univers entier, sans selection")):
            v = inv[cle]
            print("  %-34s %s %s %s" % (nom, _pct(v["cagr"], 9), _num(v["sharpe"], 8),
                                        _pct(v["max_drawdown"], 10)))
        print("\n  Ecart normal - inverse : %+.2f point de CAGR, %+.2f de Sharpe."
              % (100 * inv["ecart_cagr"], inv["ecart_sharpe"]))
        if inv["ecart_cagr"] < 0.02:
            print("  Acheter les PIRES scores rapporte autant : le score ne classe rien.")

    # -- 4. null aleatoire --------------------------------------------------
    if not args.no_null:
        print("\n" + "=" * 92)
        print("  4. NULL ALEATOIRE : la strategie bat-elle un score sans information ?")
        print("=" * 92)
        done = [0]

        def progress(i, n):
            if i * 20 // max(n, 1) != done[0]:
                done[0] = i * 20 // max(n, 1)
                sys.stdout.write("\r     %d/%d tirages..." % (i, n))
                sys.stdout.flush()

        null = edge.random_null(prices, cfg, n_draws=args.draws, start=args.start,
                                time_budget=args.budget, progress=progress)
        sys.stdout.write("\r" + " " * 40 + "\r")
        print("     %d tirages en %.0f s, persistance de rang reproduite : rho = %.2f\n"
              % (null["n_draws"], null["duree_s"], null["rho"]))
        print("  %-16s %9s | %9s %9s %9s %9s | %s" % (
            "", "reel", "hasard moy", "p5", "median", "p95", "centile reel"))
        for key, label, pct in [("sharpe", "Sharpe", False), ("cagr", "CAGR", True),
                                ("max_drawdown", "perte max", True),
                                ("annual_turnover", "rotation", True)]:
            d = null["distribution"].get(key)
            if not d:
                continue
            f = _pct if pct else (lambda v, w=9: _num(v, w, 3))
            print("  %-16s %s | %s %s %s %s | %12s" % (
                label, f(null["reel"][key], 9), f(d["moyenne"], 9), f(d["p05"], 9),
                f(d["median"], 9), f(d["p95"], 9), "%.0fe" % d["centile_reel"]))
        print("\n  Sharpe reel a %+.2f ecart-type du hasard." % null["z_sharpe"])
        print("  Un score SANS information fait mieux dans %.0f %% des tirages."
              % (100 * null["p_hasard_fait_mieux"]))
        if null["p_hasard_fait_mieux"] > 0.5:
            print("\n  Plus d'un tirage sur deux fait mieux que le vrai score :")
            print("  ce score ne selectionne rien, il coute des frais.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BenchmarkMissingError, UniverseError, FileNotFoundError) as exc:
        sys.exit(_amical(exc))
