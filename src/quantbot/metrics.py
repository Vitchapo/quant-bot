"""Mesures de performance et de risque.

Un seul chiffre ne dit jamais rien. On regarde toujours ensemble : le
rendement, la volatilite subie pour l'obtenir, la perte maximale traversee,
et la rotation qui genere les frais.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# En dessous de ce seuil, un ecart-type n'est plus que du bruit d'arrondi
# flottant. Sans ce garde-fou, une serie a rendement quasi constant produit un
# ratio de Sharpe absurde (plusieurs centaines) au lieu de zero.
_EPS_STD = 1e-12


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Ecart relatif au plus haut historique, a chaque date."""
    return equity / equity.cummax() - 1.0


def max_drawdown(equity: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None, int]:
    """(perte maximale, date du sommet, date du creux, duree de recuperation)."""
    dd = drawdown_series(equity)
    if dd.empty or not np.isfinite(dd.to_numpy()).any():
        return 0.0, None, None, 0
    trough = dd.idxmin()
    peak = equity.loc[:trough].idxmax()
    after = equity.loc[trough:]
    recovered = after[after >= equity.loc[peak]]
    recovery_days = (
        int(np.busday_count(trough.date(), recovered.index[0].date()))
        if len(recovered) else -1  # -1 = jamais recupere sur la periode
    )
    return float(dd.min()), peak, trough, recovery_days


def _annualisation_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max((index[-1] - index[0]).days / 365.25, 1e-9)


def compute(result, risk_free_annual: float = 0.0) -> dict[str, float]:
    """Tableau de bord complet a partir d'un BacktestResult."""
    eq = result.equity.dropna()
    ret = result.returns.reindex(eq.index).fillna(0.0)
    if len(eq) < 2:
        return {}

    years = _annualisation_years(eq.index)
    total_return = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1.0)
    vol = float(ret.std(ddof=1) * np.sqrt(TRADING_DAYS))

    rf_daily = (1.0 + risk_free_annual) ** (1 / TRADING_DAYS) - 1.0
    excess = ret - rf_daily
    excess_std = float(excess.std(ddof=1))
    sharpe = float(excess.mean() / excess_std * np.sqrt(TRADING_DAYS)) if excess_std > _EPS_STD else 0.0

    # Deviation a la baisse : racine du moment d'ordre 2 des rendements
    # NEGATIFS, mesuree autour de ZERO et divisee par le nombre TOTAL
    # d'observations. Utiliser l'ecart-type des seuls rendements negatifs
    # (autour de leur propre moyenne, sur leur propre effectif) donne un
    # chiffre qui n'est pas un Sortino : il surestime sur une serie
    # symetrique et sous-estime justement quand la queue gauche est epaisse.
    downside = np.minimum(excess.to_numpy(dtype="float64"), 0.0)
    dd_std = float(np.sqrt(np.mean(downside ** 2)))
    sortino = float(excess.mean() / dd_std * np.sqrt(TRADING_DAYS)) if dd_std > _EPS_STD else 0.0

    mdd, peak, trough, recovery = max_drawdown(eq)

    monthly = eq.groupby(eq.index.to_period("M")).last().pct_change().dropna()
    hit_rate = float((monthly > 0).mean()) if len(monthly) else 0.0

    turn = result.turnover[result.turnover > 0]
    ann_turnover = float(turn.sum() / years) if years > 0 else 0.0

    out = {
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "years": round(years, 2),
        "total_return": total_return,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "max_dd_peak": str(peak.date()) if peak is not None else "",
        "max_dd_trough": str(trough.date()) if trough is not None else "",
        "recovery_days": recovery,
        "calmar": float(cagr / abs(mdd)) if mdd < -1e-9 else float("nan"),
        "hit_rate_monthly": hit_rate,
        "best_month": float(monthly.max()) if len(monthly) else 0.0,
        "worst_month": float(monthly.min()) if len(monthly) else 0.0,
        "annual_turnover": ann_turnover,
        "total_costs_pct": float(result.costs.sum()),
        "avg_exposure": float(result.net_exposure.mean()),
        "n_rebalances": int((result.turnover > 0).sum()),
    }

    if result.benchmark is not None:
        # On restreint TOUT a la periode ou l'indice cote reellement.
        # Combler les jours manquants par un rendement nul fausse beta,
        # alpha et tracking error sans le moindre avertissement.
        b = result.benchmark.reindex(eq.index).ffill().dropna()
        commun = eq.index.intersection(b.index)
        if len(commun) > 2:
            b = b.loc[commun]
            ret_c = ret.loc[commun]
            b_ret = b.pct_change().fillna(0.0)
            b_years = _annualisation_years(b.index)
            out["benchmark_coverage"] = float(len(commun) / len(eq.index))
            out["benchmark_cagr"] = float((b.iloc[-1] / b.iloc[0]) ** (1 / b_years) - 1.0)
            out["benchmark_max_drawdown"] = float(drawdown_series(b).min())
            b_excess = b_ret - rf_daily
            b_std = float(b_excess.std(ddof=1))
            out["benchmark_sharpe"] = (
                float(b_excess.mean() / b_std * np.sqrt(TRADING_DAYS))
                if b_std > _EPS_STD else 0.0
            )
            var_b = float(b_ret.var(ddof=1))
            beta = float(np.cov(ret_c, b_ret, ddof=1)[0, 1] / var_b) if var_b > _EPS_STD else 0.0
            out["beta"] = beta
            out["alpha_annual"] = float(
                (ret_c.mean() - rf_daily - beta * (b_ret.mean() - rf_daily)) * TRADING_DAYS)
            active = ret_c - b_ret
            te = float(active.std(ddof=1) * np.sqrt(TRADING_DAYS))
            out["tracking_error"] = te
            out["information_ratio"] = (
                float(active.mean() * TRADING_DAYS / te) if te > _EPS_STD else 0.0)
    return out


PERCENT_KEYS = {
    "total_return", "cagr", "volatility", "max_drawdown", "hit_rate_monthly",
    "best_month", "worst_month", "annual_turnover", "total_costs_pct",
    "avg_exposure", "benchmark_cagr", "benchmark_max_drawdown",
    "alpha_annual", "tracking_error", "benchmark_coverage",
}

LABELS = {
    "start": "Debut", "end": "Fin", "years": "Duree (annees)",
    "total_return": "Performance totale", "cagr": "Performance annualisee",
    "volatility": "Volatilite annualisee", "sharpe": "Ratio de Sharpe",
    "sortino": "Ratio de Sortino", "max_drawdown": "Perte maximale",
    "max_dd_peak": "  sommet", "max_dd_trough": "  creux",
    "recovery_days": "  jours pour recuperer", "calmar": "Ratio de Calmar",
    "hit_rate_monthly": "Mois positifs", "best_month": "Meilleur mois",
    "worst_month": "Pire mois", "annual_turnover": "Rotation annuelle",
    "total_costs_pct": "Frais cumules", "avg_exposure": "Exposition moyenne",
    "n_rebalances": "Nb de rebalancements", "benchmark_cagr": "Indice : perf. annualisee",
    "benchmark_max_drawdown": "Indice : perte maximale",
    "benchmark_sharpe": "Indice : Sharpe", "beta": "Beta",
    "alpha_annual": "Alpha annualise", "tracking_error": "Tracking error",
    "information_ratio": "Ratio d'information",
    "benchmark_coverage": "Indice : couverture de la periode",
}


def format_report(stats: dict) -> str:
    """Rendu texte lisible dans un terminal."""
    lines = []
    for key, value in stats.items():
        label = LABELS.get(key, key)
        if isinstance(value, float) and not np.isfinite(value):
            shown = "n/a"
        elif key in PERCENT_KEYS and isinstance(value, (int, float)):
            shown = f"{value:>8.2%}"
        elif isinstance(value, float):
            shown = f"{value:>8.2f}"
        else:
            shown = f"{value:>8}"
        lines.append(f"  {label:<30} {shown}")
    return "\n".join(lines)
