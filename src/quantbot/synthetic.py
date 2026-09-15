"""Generateur de cours synthetiques.

Sert a deux choses, toutes deux essentielles :

1. Tester la chaine sans reseau ni quota d'API.
2. VALIDER L'ABSENCE DE BIAIS. C'est l'usage le plus important. On genere un
   univers de marches aleatoires purs, sans aucun signal exploitable. Une
   strategie correctement implementee doit y produire une performance
   ajustee du risque proche de zero. Si elle affiche un beau Sharpe sur du
   bruit, c'est qu'il y a une fuite d'information future dans le code.
   `tests/test_no_lookahead.py` automatise exactement ce controle.

Le generateur sait aussi produire un univers avec un effet momentum implante
(momentum_strength > 0), ce qui permet de verifier l'inverse : que la chaine
detecte bien un signal quand il existe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trading_days(start: str, end: str) -> pd.DatetimeIndex:
    """Jours ouvres approximatifs (lundi-vendredi, jours feries ignores)."""
    return pd.bdate_range(start=start, end=end)


def generate_prices(
    n_tickers: int = 60,
    start: str = "2005-01-01",
    end: str = "2025-12-31",
    seed: int = 42,
    annual_drift: float = 0.06,
    market_vol: float = 0.16,
    idio_vol: float = 0.28,
    momentum_strength: float = 0.0,
    regime_switching: bool = True,
) -> dict[str, pd.DataFrame]:
    """Genere un univers de cours OHLCV synthetiques.

    Parametres
    ----------
    momentum_strength :
        0.0 = marches aleatoires purs, aucun signal a trouver.
        > 0 = chaque titre recoit une esperance de rendement persistante
        (processus AR(1)), ce qui cree un vrai effet momentum detectable.
    regime_switching :
        Alterne des phases haussieres et baissieres au niveau du marche, ce
        qui permet de tester le filtre de regime.
    """
    rng = np.random.default_rng(seed)
    dates = trading_days(start, end)
    n_days = len(dates)
    dt = 1.0 / 252.0

    # --- facteur de marche, avec regimes ---------------------------------
    if regime_switching:
        # Chaine de Markov a deux etats : haussier (persistant) / baissier (bref)
        regime = np.zeros(n_days, dtype=int)
        p_stay = {0: 0.995, 1: 0.985}          # proba de rester dans l'etat
        mu_reg = {0: annual_drift, 1: -0.25}   # derive annualisee par etat
        vol_reg = {0: market_vol, 1: market_vol * 1.9}
        state = 0
        for i in range(n_days):
            if rng.random() > p_stay[state]:
                state = 1 - state
            regime[i] = state
        mkt_mu = np.array([mu_reg[s] for s in regime])
        mkt_sigma = np.array([vol_reg[s] for s in regime])
    else:
        mkt_mu = np.full(n_days, annual_drift)
        mkt_sigma = np.full(n_days, market_vol)

    mkt_ret = mkt_mu * dt + mkt_sigma * np.sqrt(dt) * rng.standard_normal(n_days)

    # --- titres individuels ----------------------------------------------
    betas = rng.uniform(0.6, 1.5, n_tickers)
    idio_vols = idio_vol * rng.uniform(0.6, 1.6, n_tickers)

    # Esperance de rendement propre a chaque titre, lentement variable.
    # phi proche de 1 => la surperformance persiste => momentum exploitable.
    if momentum_strength > 0:
        phi = 0.995
        alpha = np.zeros((n_days, n_tickers))
        alpha[0] = rng.standard_normal(n_tickers) * momentum_strength
        shock = rng.standard_normal((n_days, n_tickers)) * momentum_strength * np.sqrt(1 - phi**2)
        for i in range(1, n_days):
            alpha[i] = phi * alpha[i - 1] + shock[i]
    else:
        alpha = np.zeros((n_days, n_tickers))

    idio = rng.standard_normal((n_days, n_tickers)) * idio_vols * np.sqrt(dt)
    rets = alpha * dt + betas * mkt_ret[:, None] + idio

    # Cours a partir des rendements logarithmiques (garantit la positivite)
    log_prices = np.log(rng.uniform(20, 200, n_tickers)) + np.cumsum(rets, axis=0)
    closes = np.exp(log_prices)

    out: dict[str, pd.DataFrame] = {}
    for j in range(n_tickers):
        close = closes[:, j]
        # OHLC plausible reconstruit autour de la cloture
        noise = np.abs(rng.standard_normal(n_days)) * close * 0.006
        open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.standard_normal(n_days) * 0.002)
        high = np.maximum(open_, close) + noise
        low = np.minimum(open_, close) - noise
        volume = rng.lognormal(13.5, 0.7, n_days).round()
        out[f"SYN{j:03d}"] = pd.DataFrame(
            {"open": open_, "high": high, "low": np.maximum(low, 0.01),
             "close": close, "volume": volume},
            index=pd.DatetimeIndex(dates, name="date"),
        )

    # Indice de reference = moyenne equiponderee de l'univers, base 1000
    bench_ret = pd.DataFrame({t: df["close"] for t, df in out.items()}).pct_change().mean(axis=1)
    bench_close = 1000 * (1 + bench_ret.fillna(0)).cumprod()
    out["^SYN"] = pd.DataFrame(
        {"open": bench_close, "high": bench_close, "low": bench_close,
         "close": bench_close, "volume": 0.0},
        index=pd.DatetimeIndex(dates, name="date"),
    )
    return out
