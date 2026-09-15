#!/usr/bin/env python3
"""Le pilotage de volatilite tient-il sa promesse ? Mesure unique.

    python scripts/mesurer_volatilite.py

L'HYPOTHESE, ecrite AVANT de lancer quoi que ce soit
----------------------------------------------------
A volatilite cible constante, le portefeuille doit afficher :

  (a) une volatilite realisee nettement plus proche de la cible ;
  (b) une perte maximale INFERIEURE ;
  (c) un Sharpe au moins EGAL.

Le critere qui tranche est (c). (a) est presque garanti par construction - ce
serait un bug s'il echouait - et (b) s'obtient trivialement en investissant
moins. Seul (c) dit si le pilotage ameliore le rapport rendement/risque ou se
contente de deplacer le curseur du risque.

Regle acceptee d'avance : cette mesure est faite UNE FOIS. Si le verdict est
negatif, on ne recommence pas avec une autre cible, une autre fenetre ou une
autre borne jusqu'a ce qu'il passe. C'est exactement ce mecanisme qui a
produit le faux positif du score composite - le meilleur de vingt essais au
hasard atteint deja un Sharpe de 0,78 sur cet univers.

Le taux de reussite d'un defi de prop firm est calcule en prime, sur la
fenetre reelle visee (3 a 8 mois), en rejouant le defi depuis chaque jour.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import pandas as pd

from quantbot import metrics
from quantbot.backtest import run_backtest
from quantbot.config import Config
from quantbot.datafeed import load_panel


def defi(rendements, objectif=0.10, perte_jour=0.05, perte_max=0.10,
         duree=105):
    """Taux de reussite d'un defi, rejoue depuis chaque jour de l'historique."""
    r = np.asarray(rendements, dtype="float64")
    n = len(r)
    if n <= duree:
        return float("nan")
    reussite = total = 0
    for debut in range(0, n - duree):
        equity = pic = 1.0
        for i in range(debut, debut + duree):
            if r[i] <= -perte_jour:
                break
            equity *= (1.0 + r[i])
            pic = max(pic, equity)
            if equity / pic - 1.0 <= -perte_max:
                break
            if equity - 1.0 >= objectif:
                reussite += 1
                break
        total += 1
    return reussite / total if total else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--cible", type=float, default=0.12,
                    help="volatilite annualisee visee (defaut 0.12)")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    prices = load_panel(cfg)
    print("%d series chargees\n" % len(prices))

    cfg_pilote = cfg.copy()
    cfg_pilote.set("portfolio.volatilite.active", True, strict=True)
    cfg_pilote.set("portfolio.volatilite.cible", args.cible, strict=True)

    nu = run_backtest(prices, cfg)
    pilote = run_backtest(prices, cfg_pilote)
    rf = float(cfg.get("execution.risk_free_annual", 0.0))
    a, b = metrics.compute(nu, rf), metrics.compute(pilote, rf)

    print("=" * 66)
    print("  VOLATILITE SUBIE  contre  VOLATILITE PILOTEE (cible %.0f %%)"
          % (100 * args.cible))
    print("=" * 66)
    print("  %-26s %12s %12s %10s" % ("", "subie", "pilotee", "ecart"))
    lignes = [("rendement annualise", "cagr", True),
              ("volatilite realisee", "volatility", True),
              ("Sharpe", "sharpe", False),
              ("perte maximale", "max_drawdown", True),
              ("rotation annuelle", "annual_turnover", False),
              ("exposition moyenne", "avg_exposure", True)]
    for libelle, cle, pct in lignes:
        x, y = a.get(cle), b.get(cle)
        if x is None or y is None:
            continue
        if pct:
            print("  %-26s %11.2f%% %11.2f%% %9.2f pt"
                  % (libelle, 100 * x, 100 * y, 100 * (y - x)))
        else:
            print("  %-26s %12.2f %12.2f %10.2f" % (libelle, x, y, y - x))

    # -- les trois criteres, juges dans l'ordre annonce --------------------
    ecart_nu = abs(a["volatility"] - args.cible)
    ecart_pilote = abs(b["volatility"] - args.cible)
    crit = [
        ("(a) volatilite plus proche de la cible", ecart_pilote < ecart_nu),
        ("(b) perte maximale inferieure", b["max_drawdown"] > a["max_drawdown"]),
        ("(c) Sharpe au moins egal", b["sharpe"] >= a["sharpe"]),
    ]
    print("\n  criteres pre-enregistres")
    for libelle, ok in crit:
        print("    %s  %s" % ("REUSSI" if ok else "ECHOUE", libelle))

    # L'erreur-type du Sharpe : sans elle, un ecart de 0,03 passe pour un
    # progres alors qu'il est vingt fois plus petit que l'incertitude.
    annees = a["years"]
    for nom, st in (("subie", a), ("pilotee", b)):
        se = np.sqrt((1 + 0.5 * st["sharpe"] ** 2) / annees)
        print("    Sharpe %-8s %.2f +/- %.2f" % (nom, st["sharpe"], se))
    se_a = np.sqrt((1 + 0.5 * a["sharpe"] ** 2) / annees)
    distinguable = abs(b["sharpe"] - a["sharpe"]) > 1.96 * se_a
    print("    -> l'ecart de Sharpe %s distinguable du bruit"
          % ("EST" if distinguable else "n'est PAS"))

    # -- le defi, sur la fenetre reellement visee --------------------------
    print("\n  taux de reussite d'un defi +10 %% / -5 %% jour / -10 %% max")
    print("  %-14s %12s %12s" % ("fenetre", "subie", "pilotee"))
    for mois, duree in ((3, 63), (5, 105), (8, 168)):
        p_a = defi(nu.returns.to_numpy(), duree=duree)
        p_b = defi(pilote.returns.to_numpy(), duree=duree)
        print("  %-14s %11.0f%% %11.0f%%" % ("%d mois" % mois, 100 * p_a, 100 * p_b))

    bench = nu.benchmark
    if bench is not None:
        rb = bench.dropna().pct_change().reindex(nu.returns.index).fillna(0.0)
        print("  %-14s %11.0f%%   (achat-conservation, 5 mois)"
              % ("SPY", 100 * defi(rb.to_numpy(), duree=105)))

    print("\n  Rappel : ces chiffres portent sur l'univers d'AUJOURD'HUI.")
    print("  La correction point-in-time retire 13,6 points de rendement")
    print("  annuel (voir scripts/mesurer_biais.py). Les deux colonnes")
    print("  subissent le meme biais, donc leur COMPARAISON reste valide ;")
    print("  leurs niveaux absolus, non.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
