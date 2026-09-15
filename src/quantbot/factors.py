"""Facteurs et score composite.

Toutes les fonctions prennent une matrice large (dates x tickers) et
renvoient une matrice de meme forme. La valeur en (t, i) n'utilise QUE des
donnees disponibles a la cloture du jour t : c'est la condition pour que le
backtest ne triche pas. Les fenetres glissantes de pandas (`rolling`) sont
causales par construction, et `shift(k)` decale vers le PASSE.

Les trois facteurs retenus
--------------------------
* momentum 12-1 : rendement sur 12 mois en excluant le dernier mois. C'est
  l'anomalie la mieux documentee (Jegadeesh & Titman 1993). On saute le
  dernier mois car il presente au contraire un effet de retournement.
* faible volatilite : la volatilite realisee est l'une des rares grandeurs
  reellement persistantes en finance. A rendement egal on prefere le titre
  le moins volatil.
* tendance : ecart au cours moyen 200 jours, qui filtre les titres en
  descente d'escalier que le momentum long peut encore bien classer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Facteurs elementaires
# ---------------------------------------------------------------------------
def momentum(close: pd.DataFrame, lookback: int = 252, skip: int = 21) -> pd.DataFrame:
    """Rendement de t-lookback a t-skip. Positif = tendance haussiere."""
    if lookback <= skip:
        raise ValueError("lookback doit etre strictement superieur a skip")
    return close.shift(skip) / close.shift(lookback) - 1.0


def realized_volatility(close: pd.DataFrame, window: int = 63,
                        periods_per_year: int = 252) -> pd.DataFrame:
    """Ecart-type annualise des rendements logarithmiques sur `window` jours."""
    log_ret = np.log(close).diff()
    return log_ret.rolling(window, min_periods=max(5, window // 2)).std() * np.sqrt(periods_per_year)


def trend(close: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    """Ecart relatif entre le cours et sa moyenne mobile. Positif = au-dessus."""
    ma = close.rolling(window, min_periods=max(20, window // 2)).mean()
    return close / ma - 1.0


# ---------------------------------------------------------------------------
# Normalisation cross-sectionnelle
# ---------------------------------------------------------------------------
def cross_sectional_zscore(df: pd.DataFrame, winsorize: float = 3.0,
                           min_names: int = 5) -> pd.DataFrame:
    """Centre-reduit chaque LIGNE (chaque date), pas chaque colonne.

    On compare les titres entre eux a une date donnee. C'est ce qui rend le
    score independant du niveau general du marche : peu importe que tout
    monte, on veut savoir qui monte le plus. Comme le calcul ne porte que sur
    une seule date, il n'introduit aucune fuite temporelle.
    """
    valid = df.notna().sum(axis=1)
    mu = df.mean(axis=1)
    sd = df.std(axis=1, ddof=0)
    sd = sd.where(sd > 1e-12)
    z = df.sub(mu, axis=0).div(sd, axis=0)
    if winsorize and winsorize > 0:
        z = z.clip(-winsorize, winsorize)
    # Une date ou trop peu de titres sont disponibles n'est pas exploitable
    return z.where(valid >= min_names)


# ---------------------------------------------------------------------------
# Score composite
# ---------------------------------------------------------------------------
def composite_score(close: pd.DataFrame, cfg) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Combine les facteurs en un score unique par titre et par date.

    Renvoie (score, dictionnaire des facteurs bruts) - le second sert au
    diagnostic et aux rapports.
    """
    fcfg = cfg.get("factors", {}) or {}
    winsor = float(fcfg.get("winsorize", 3.0) or 0.0)
    min_valid = int(fcfg.get("min_valid_factors", 1))

    raw: dict[str, pd.DataFrame] = {}
    weighted: list[pd.DataFrame] = []
    weights: list[float] = []

    mom_cfg = fcfg.get("momentum")
    if mom_cfg and float(mom_cfg.get("weight", 0)) != 0:
        raw["momentum"] = momentum(close, int(mom_cfg.get("lookback", 252)),
                                   int(mom_cfg.get("skip", 21)))
        weighted.append(cross_sectional_zscore(raw["momentum"], winsor))
        weights.append(float(mom_cfg["weight"]))

    vol_cfg = fcfg.get("low_volatility")
    if vol_cfg and float(vol_cfg.get("weight", 0)) != 0:
        raw["volatility"] = realized_volatility(close, int(vol_cfg.get("window", 63)))
        # Signe negatif : une volatilite ELEVEE doit degrader le score.
        weighted.append(-cross_sectional_zscore(raw["volatility"], winsor))
        weights.append(float(vol_cfg["weight"]))

    trend_cfg = fcfg.get("trend")
    if trend_cfg and float(trend_cfg.get("weight", 0)) != 0:
        raw["trend"] = trend(close, int(trend_cfg.get("window", 200)))
        weighted.append(cross_sectional_zscore(raw["trend"], winsor))
        weights.append(float(trend_cfg["weight"]))

    if not weighted:
        raise ValueError("Aucun facteur actif : verifie la section 'factors' de la config")

    stack = np.stack([w.to_numpy(dtype="float64") for w in weighted])   # (f, dates, tickers)
    warr = np.asarray(weights, dtype="float64")[:, None, None]

    present = ~np.isnan(stack)
    n_valid = present.sum(axis=0)

    # Moyenne ponderee en ignorant les facteurs manquants, sans que
    # np.nansum ne transforme "tout manquant" en zero.
    contrib = np.where(present, stack * warr, 0.0)
    wsum = np.where(present, np.broadcast_to(warr, stack.shape), 0.0).sum(axis=0)
    total = contrib.sum(axis=0)

    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.where(wsum > 0, total / wsum, np.nan)
    score = np.where(n_valid >= min_valid, score, np.nan)

    return pd.DataFrame(score, index=close.index, columns=close.columns), raw


# ---------------------------------------------------------------------------
# Filtre de regime de marche
# ---------------------------------------------------------------------------
def regime_filter(benchmark_close: pd.Series, ma_window: int = 200) -> pd.Series:
    """True quand l'indice cloture au-dessus de sa moyenne mobile.

    Ce filtre unique explique l'essentiel de la reduction de perte maximale
    d'une strategie momentum : il coupe l'exposition pendant les krachs, au
    prix de quelques faux signaux dans les marches sans direction.
    """
    ma = benchmark_close.rolling(ma_window, min_periods=max(20, ma_window // 2)).mean()
    on = benchmark_close > ma
    return on.where(ma.notna())
