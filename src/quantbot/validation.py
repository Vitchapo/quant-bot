"""Validation hors echantillon.

Le probleme que ce module resout
--------------------------------
Essayer 50 jeux de parametres sur 20 ans d'historique et garder le meilleur
ne produit pas une strategie : cela produit un resultat de concours de beaute
sur du bruit. Avec assez d'essais, on trouve toujours une combinaison qui a
brillamment fonctionne par hasard.

Deux garde-fous ici :

* `walk_forward` : on choisit les parametres sur une fenetre d'apprentissage,
  puis on les applique a la fenetre SUIVANTE, jamais vue au moment du choix,
  et on avance. La courbe obtenue en recollant les fenetres de test est le
  seul resultat a peu pres honnete que produise ce projet.

* `parameter_sensitivity` : on evalue toute la grille et on regarde la
  DISPERSION des resultats. Une strategie robuste fonctionne correctement sur
  une large plage de reglages. Si seul un reglage precis fonctionne et que
  ses voisins immediats s'effondrent, c'est du surajustement, pas un edge.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import metrics
from .backtest import BacktestResult, run_backtest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
def slice_result(result: BacktestResult, start, end, inclusive_start: bool = True) -> BacktestResult:
    """Restreint un resultat a une fenetre, en rebasant la valeur a 100 000.

    `equity` est rebasee sur la premiere valeur de la fenetre : le rendement
    de ce premier jour a donc ete gagne AVANT le debut de la fenetre et doit
    etre exclu de `returns`, sans quoi les mesures tirees de `equity` (CAGR,
    perte maximale) et celles tirees de `returns` (Sharpe, volatilite) ne
    portent pas sur la meme serie.
    """
    idx = result.equity.index
    lower = (idx >= start) if inclusive_start else (idx > start)
    mask = lower & (idx <= end)
    eq = result.equity[mask]
    if len(eq) < 2:
        raise ValueError(f"Fenetre trop courte : {start} -> {end}")
    rebased = eq / eq.iloc[0] * 100_000.0
    bench = None
    if result.benchmark is not None:
        b = result.benchmark[mask].dropna()
        if len(b) > 1:
            bench = b / b.iloc[0] * 100_000.0
    returns = result.returns[mask].copy()
    returns.iloc[0] = 0.0    # le 1er jour sert de base, son rendement est hors fenetre
    return BacktestResult(
        equity=rebased,
        returns=returns,
        weights=result.weights[mask],
        cash=result.cash[mask],
        turnover=result.turnover[mask],
        costs=result.costs[mask],
        trades=result.trades,
        benchmark=bench,
        meta={**result.meta, "window": (str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date()))},
    )


def _expand_grid(grid: dict[str, list]) -> list[dict]:
    """{'a':[1,2],'b':[3]} -> [{'a':1,'b':3},{'a':2,'b':3}]"""
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def _objective_value(stats: dict, objective: str) -> float:
    value = stats.get(objective, float("-inf"))
    if value is None or not np.isfinite(value):
        return float("-inf")
    return float(value)


def _combo_label(combo: dict) -> str:
    """Identifiant unique d'une combinaison.

    On garde le chemin complet : deux parametres partageant la meme feuille
    (par exemple `factors.momentum.lookback` et `factors.trend.lookback`)
    produiraient sinon la meme cle et s'ecraseraient dans le dictionnaire
    des resultats.
    """
    return ", ".join(f"{k}={v}" for k, v in sorted(combo.items())) or "defaut"


def _combo_short(combo: dict) -> str:
    """Version courte, pour l'affichage seulement."""
    return ", ".join(f"{k.split('.')[-1]}={v}" for k, v in sorted(combo.items())) or "defaut"


# ---------------------------------------------------------------------------
@dataclass
class WalkForwardResult:
    windows: pd.DataFrame          # une ligne par fenetre de test
    oos_returns: pd.Series         # rendements hors echantillon recolles
    oos_equity: pd.Series
    oos_stats: dict
    full_grid: pd.DataFrame        # performance de chaque combinaison, plein echantillon


def walk_forward(prices: dict, cfg, verbose: bool = True) -> WalkForwardResult:
    grid = cfg.get("walk_forward.grid", {}) or {}
    combos = _expand_grid(grid)
    objective = cfg.get("walk_forward.objective", "sharpe")
    train_years = float(cfg.get("walk_forward.train_years", 4))
    test_years = float(cfg.get("walk_forward.test_years", 1))
    rf = float(cfg.get("execution.risk_free_annual", 0.0))

    # Chaque combinaison n'est backtestee QU'UNE fois sur tout l'historique ;
    # les fenetres sont ensuite decoupees dedans. Resultat identique,
    # temps de calcul divise par le nombre de fenetres.
    if verbose:
        print(f"Backtest de {len(combos)} combinaisons de parametres...")
    runs: dict[str, BacktestResult] = {}
    for combo in combos:
        label = _combo_label(combo)
        runs[label] = run_backtest(prices, cfg.with_overrides(combo))
        if verbose:
            print(f"  . {label}")

    index = next(iter(runs.values())).equity.index
    start, end = index[0], index[-1]

    # Les facteurs ont besoin de chauffe : on demarre apres la premiere annee.
    cursor = start + pd.DateOffset(years=1)
    rows, oos_chunks = [], []

    while True:
        train_start = cursor
        train_end = train_start + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(years=test_years)
        if train_end >= end:
            break
        test_end = min(test_end, end)
        if (test_end - train_end).days < 30:
            break

        scores = {}
        for label, res in runs.items():
            try:
                scores[label] = _objective_value(
                    metrics.compute(slice_result(res, train_start, train_end), rf), objective)
            except ValueError:
                scores[label] = float("-inf")
        best = max(scores, key=scores.get)

        # inclusive_start=False : le jour `train_end` appartient a
        # l'apprentissage, pas au test. Sans cela le Sharpe de test contient
        # une journee deja vue.
        test_res = slice_result(runs[best], train_end, test_end, inclusive_start=False)
        test_stats = metrics.compute(test_res, rf)
        oos_chunks.append(runs[best].returns[
            (index > train_end) & (index <= test_end)])

        rows.append({
            "train_start": train_start.date(), "train_end": train_end.date(),
            "test_end": test_end.date(), "params": best,
            f"train_{objective}": scores[best],
            f"test_{objective}": _objective_value(test_stats, objective),
            "test_cagr": test_stats.get("cagr", np.nan),
            "test_sharpe": test_stats.get("sharpe", np.nan),
            "test_max_dd": test_stats.get("max_drawdown", np.nan),
        })
        if verbose:
            print(f"  {train_start.date()} -> {train_end.date()} | choisi : {best:<40} "
                  f"| test {train_end.date()}->{test_end.date()} : "
                  f"CAGR {rows[-1]['test_cagr']:+.1%}, Sharpe {rows[-1]['test_sharpe']:.2f}")

        cursor = cursor + pd.DateOffset(years=test_years)

    if not oos_chunks:
        raise ValueError(
            "Historique trop court pour la validation walk-forward : il faut au "
            f"moins {1 + train_years + test_years:.0f} annees de donnees.")

    oos_ret = pd.concat(oos_chunks).sort_index()
    oos_ret = oos_ret[~oos_ret.index.duplicated(keep="first")]
    oos_eq = 100_000.0 * (1.0 + oos_ret).cumprod()

    oos_result = BacktestResult(
        equity=oos_eq, returns=oos_ret,
        weights=pd.DataFrame(index=oos_eq.index), cash=pd.Series(0.0, index=oos_eq.index),
        turnover=pd.Series(0.0, index=oos_eq.index), costs=pd.Series(0.0, index=oos_eq.index),
        trades=pd.DataFrame(), benchmark=None,
    )
    oos_stats = metrics.compute(oos_result, rf)
    # La courbe hors echantillon est un recollement de fenetres : les poids,
    # la rotation et les frais n'y ont pas de sens agrege. On les retire
    # plutot que d'afficher des zeros trompeurs.
    for cle in ("annual_turnover", "total_costs_pct", "avg_exposure", "n_rebalances"):
        oos_stats.pop(cle, None)

    grid_rows = []
    for label, res in runs.items():
        st = metrics.compute(res, rf)
        grid_rows.append({"params": label, "cagr": st.get("cagr"), "sharpe": st.get("sharpe"),
                          "calmar": st.get("calmar"), "max_drawdown": st.get("max_drawdown")})

    return WalkForwardResult(
        windows=pd.DataFrame(rows),
        oos_returns=oos_ret,
        oos_equity=oos_eq,
        oos_stats=oos_stats,
        full_grid=pd.DataFrame(grid_rows).sort_values("sharpe", ascending=False),
    )


def parameter_sensitivity(prices: dict, cfg, grid: dict | None = None,
                          verbose: bool = True) -> pd.DataFrame:
    """Evalue toute la grille en plein echantillon et mesure la dispersion.

    Lis le resultat ainsi : si l'ecart-type des Sharpe est faible et que la
    mediane reste positive, la strategie est robuste. Si un seul reglage
    surnage, laisse tomber.
    """
    grid = grid or cfg.get("walk_forward.grid", {}) or {}
    rows = []
    for combo in _expand_grid(grid):
        res = run_backtest(prices, cfg.with_overrides(combo))
        st = metrics.compute(res, float(cfg.get("execution.risk_free_annual", 0.0)))
        row = {k.split(".")[-1]: v for k, v in combo.items()}
        row.update({"cagr": st.get("cagr"), "sharpe": st.get("sharpe"),
                    "calmar": st.get("calmar"), "max_drawdown": st.get("max_drawdown"),
                    "turnover": st.get("annual_turnover")})
        rows.append(row)
        if verbose:
            print(f"  {_combo_short(combo):<45} Sharpe {row['sharpe']:>6.2f}  CAGR {row['cagr']:>7.2%}")
    df = pd.DataFrame(rows)
    if verbose and len(df):
        print(f"\n  Sharpe sur la grille : mediane {df['sharpe'].median():.2f}, "
              f"ecart-type {df['sharpe'].std():.2f}, "
              f"min {df['sharpe'].min():.2f}, max {df['sharpe'].max():.2f}")
    return df
