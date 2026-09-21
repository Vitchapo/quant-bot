"""Mesure d'edge : le signal contient-il de l'information, oui ou non ?

Pourquoi ce module existe
-------------------------
Un backtest produit toujours une courbe. Une courbe qui monte ne prouve rien :
elle peut monter parce que l'univers monte, parce que la ponderation a de la
chance, ou simplement parce qu'on a tire 20 titres sur 64 dans un marche
haussier. `validation.py` verifie que la METHODE est honnete (pas de fuite, pas
de surajustement). Ce module-ci verifie que le SIGNAL existe. Ce sont deux
questions differentes, et la seconde est la seule qui decide s'il faut
continuer.

Trois mesures independantes, qui doivent concorder :

1. `information_coefficient` - correlation de rang entre le score du jour de
   signal et le rendement effectivement realise jusqu'au rebalancement suivant.
   C'est la mesure la plus directe et la plus efficace statistiquement : elle
   utilise TOUS les titres a chaque date, la ou une courbe de performance ne
   retient que les `top_n` retenus. Un IC moyen de 0,02 avec un t de 2 est un
   vrai signal faible ; un IC de 0,001 avec un t de 0,05 est du bruit.

2. `random_null` - on remplace le score par du bruit ayant la MEME persistance
   mensuelle de rang, et on relance le backtest complet des centaines de fois.
   Si la vraie strategie ne sort pas de cette distribution, elle ne fait pas
   mieux que le hasard. Le controle de la persistance est indispensable : un
   bruit sans memoire ferait tourner le portefeuille beaucoup plus vite et
   paierait des frais que la vraie strategie ne paie pas, ce qui truquerait la
   comparaison en faveur de cette derniere.

3. `decomposition` - on empile les briques une par une (indice -> univers ->
   selection -> ponderation -> filtre de regime) pour voir laquelle apporte
   quelque chose. C'est la mesure qui repond a la question "ai-je construit un
   tracker cher ?".

Aucune dependance en dehors de numpy/pandas : la correlation de Spearman est
obtenue en classant puis en correlant (Pearson sur les rangs), ce qui evite
d'imposer scipy.
"""
from __future__ import annotations

import math
import warnings
import time
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import factors as F
from . import metrics
from .backtest import BacktestResult, run_backtest
from .datafeed import to_matrix
from .portfolio import rebalance_dates

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Correlation de rang, sans scipy (Pearson applique aux rangs)."""
    if len(a) < 3:
        return float("nan")
    return float(a.rank().corr(b.rank()))


def _two_sided_p(t_stat: float, n: int) -> float:
    """p bilaterale, approximation normale (n est grand : 50 a 250 dates)."""
    if not np.isfinite(t_stat):
        return float("nan")
    return float(math.erfc(abs(t_stat) / math.sqrt(2.0)))


def _asset_matrix(prices: dict, cfg) -> pd.DataFrame:
    """Matrice des cours, benchmark exclu, prolongee - comme dans le moteur."""
    bench = cfg.get("universe.benchmark")
    assets = {t: df for t, df in prices.items()
              if t != bench and not t.startswith("^")}
    return to_matrix(assets, "close", min_history=2).ffill()


def factor_panels(close: pd.DataFrame, cfg) -> dict:
    """Z-scores cross-sectionnels de chaque facteur, plus le composite.

    Les signes sont ceux effectivement utilises par le moteur : la faible
    volatilite est deja retournee, de sorte qu'un IC positif signifie toujours
    "ce facteur, tel qu'il est utilise, aide".
    """
    fcfg = cfg.get("factors", {}) or {}
    winsor = float(fcfg.get("winsorize", 3.0) or 0.0)
    score, raw = F.composite_score(close, cfg)

    panels = {}
    if "momentum" in raw:
        panels["momentum %d-%d" % (int(cfg.get("factors.momentum.lookback", 252)),
                                   int(cfg.get("factors.momentum.skip", 21)))] = \
            F.cross_sectional_zscore(raw["momentum"], winsor)
    if "volatility" in raw:
        panels["faible volatilite"] = -F.cross_sectional_zscore(raw["volatility"], winsor)
    if "trend" in raw:
        panels["tendance MM%d" % int(cfg.get("factors.trend.window", 200))] = \
            F.cross_sectional_zscore(raw["trend"], winsor)
    panels["COMPOSITE"] = score
    return panels


# ---------------------------------------------------------------------------
# 1. Information coefficient
# ---------------------------------------------------------------------------
COLONNES_IC = ["facteur", "ic_moyen", "ic_median", "ic_ecart_type", "t_stat",
               "p_value", "part_positive", "n_dates"]


def information_coefficient(prices: dict, cfg, start=None, end=None) -> pd.DataFrame:
    """IC de Spearman entre le score en t et le rendement jusqu'au rebalancement suivant.

    Chronologie identique a celle du backtest : le score est lu a la cloture du
    jour de signal t, le rendement est mesure de la cloture t+lag (donc apres
    execution) jusqu'a la date de signal suivante. Aucune information du futur
    n'entre dans le score, et aucun rendement anterieur a l'execution n'est
    compte dans le resultat.
    """
    close = _asset_matrix(prices, cfg)
    panels = factor_panels(close, cfg)
    dates = rebalance_dates(close.index, cfg.get("execution.rebalance", "monthly"))
    if start is not None:
        dates = dates[dates >= pd.Timestamp(start)]
    if end is not None:
        dates = dates[dates <= pd.Timestamp(end)]
    lag = int(cfg.get("execution.execution_lag", 1))

    positions = close.index.get_indexer(dates)
    rows = []
    fenetres_vides = 0
    for name, panel in panels.items():
        ics = []
        for k, p in enumerate(positions):
            if k + 1 >= len(dates):
                break
            entry = p + lag
            exit_ = close.index.get_indexer([dates[k + 1]])[0]
            # entry >= exit_ signifie que l'execution tombe APRES le signal
            # suivant : la fenetre de mesure est vide. C'est le cas des qu'on
            # demande un delai superieur ou egal a l'ecart entre deux
            # rebalancements - par exemple lag=5 en cadence hebdomadaire. Il
            # n'y a alors rien a mesurer, et c'est une contrainte de
            # chronologie, pas une anomalie de donnees.
            if entry >= exit_ or exit_ >= len(close.index):
                fenetres_vides += 1
                continue
            forward = close.iloc[exit_] / close.iloc[entry] - 1.0
            pair = pd.concat([panel.loc[close.index[p]], forward], axis=1).dropna()
            if len(pair) < 15:
                continue
            ics.append(_spearman(pair.iloc[:, 0], pair.iloc[:, 1]))

        arr = np.asarray(ics, dtype="float64")
        arr = arr[np.isfinite(arr)]
        if len(arr) < 3:
            continue
        t_stat = float(arr.mean() / arr.std(ddof=1) * math.sqrt(len(arr)))
        rows.append({
            "facteur": name,
            "ic_moyen": float(arr.mean()),
            "ic_median": float(np.median(arr)),
            "ic_ecart_type": float(arr.std(ddof=1)),
            "t_stat": t_stat,
            "p_value": _two_sided_p(t_stat, len(arr)),
            "part_positive": float((arr > 0).mean()),
            "n_dates": int(len(arr)),
        })
    # Sans colonnes, un resultat vide fait exploser l'appelant sur un KeyError
    # illisible au lieu de lui montrer un tableau vide. Le cas se produit pour
    # de bonnes raisons - delai >= cadence, historique trop court - et un outil
    # de MESURE qui echoue de facon confuse est pire qu'un outil qui dit "rien
    # a mesurer" : on croit avoir mesure.
    if not rows:
        if fenetres_vides:
            warnings.warn(
                "aucun IC calculable : %d fenetre(s) vide(s). L'execution "
                "(delai %d seance(s)) tombe apres le signal suivant en cadence "
                "%s - il n'y a aucun intervalle a mesurer."
                % (fenetres_vides, lag,
                   cfg.get("execution.rebalance", "monthly")), stacklevel=2)
        return pd.DataFrame(columns=COLONNES_IC)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Null aleatoire
# ---------------------------------------------------------------------------
def rank_persistence(score: pd.DataFrame, dates: pd.DatetimeIndex) -> float:
    """Autocorrelation de rang du score d'un rebalancement au suivant."""
    sub = score.reindex(dates)
    vals = [_spearman(sub.iloc[i].dropna(), sub.iloc[i - 1].reindex(sub.iloc[i].dropna().index))
            for i in range(1, len(sub))]
    vals = [v for v in vals if np.isfinite(v)]
    return float(np.mean(vals)) if vals else 0.0


def _slice(result: BacktestResult, start, end=None) -> BacktestResult:
    if start is None and end is None:
        return result
    mask = np.ones(len(result.equity.index), dtype=bool)
    if start is not None:
        mask &= np.asarray(result.equity.index >= pd.Timestamp(start))
    if end is not None:
        mask &= np.asarray(result.equity.index <= pd.Timestamp(end))
    eq = result.equity[mask]
    if len(eq) < 3:
        raise ValueError("fenetre trop courte")
    ret = result.returns[mask].copy()
    ret.iloc[0] = 0.0
    bench = None
    if result.benchmark is not None:
        b = result.benchmark[mask].dropna()
        if len(b) > 1:
            bench = b / b.iloc[0] * 100_000.0
    return BacktestResult(
        equity=eq / eq.iloc[0] * 100_000.0, returns=ret, weights=result.weights[mask],
        cash=result.cash[mask], turnover=result.turnover[mask], costs=result.costs[mask],
        trades=result.trades, benchmark=bench, meta=result.meta)


def random_null(prices: dict, cfg, n_draws: int = 200, seed: int = 1000,
                start=None, time_budget: Optional[float] = None,
                progress: Optional[Callable] = None) -> dict:
    """Compare la strategie a des scores SANS information mais aussi persistants.

    Renvoie un dictionnaire contenant les mesures reelles, la distribution des
    tirages, le centile occupe par la strategie et la probabilite qu'un score
    aleatoire fasse mieux. Cette derniere est la lecture la plus parlante : si
    elle depasse 50 %, le score coute de l'argent.
    """
    rf = float(cfg.get("execution.risk_free_annual", 0.0))
    close = _asset_matrix(prices, cfg)
    score, raw = F.composite_score(close, cfg)
    dates = rebalance_dates(close.index, cfg.get("execution.rebalance", "monthly"))
    rho = rank_persistence(score, dates)

    real = metrics.compute(_slice(run_backtest(prices, cfg), start), rf)

    # Le score n'est lu qu'aux dates de rebalancement (`target_weights` ignore
    # les autres), donc le bruit n'est fabrique que sur ces dates : ~260 lignes
    # au lieu de ~5500. Sur un univers de 500 titres, cela divise par plus de
    # dix le cout d'un tirage et rend le test praticable.
    mask = score.reindex(dates).notna().to_numpy()
    columns = score.columns
    n_assets = mask.shape[1]
    original = F.composite_score
    draws = []
    t0 = time.time()
    try:
        for d in range(n_draws):
            if time_budget is not None and time.time() - t0 > time_budget:
                break
            rng = np.random.default_rng(seed + d)
            z = rng.standard_normal(n_assets)
            rows = np.empty((len(dates), n_assets))
            damp = math.sqrt(max(1.0 - rho ** 2, 0.0))
            for i in range(len(dates)):
                z = rho * z + damp * rng.standard_normal(n_assets)
                rows[i] = z
            fake = pd.DataFrame(np.where(mask, rows, np.nan), index=dates, columns=columns)
            F.composite_score = lambda c, k, _f=fake: (_f, raw)
            draws.append(metrics.compute(_slice(run_backtest(prices, cfg), start), rf))
            if progress is not None:
                progress(d + 1, n_draws)
    finally:
        F.composite_score = original

    dist = pd.DataFrame(draws)
    out = {"rho": rho, "n_draws": len(dist), "reel": real,
           "duree_s": round(time.time() - t0, 1), "distribution": {}}
    for key in ("sharpe", "cagr", "max_drawdown", "annual_turnover", "volatility"):
        if key not in dist.columns:
            continue
        col = dist[key].dropna()
        value = real.get(key, float("nan"))
        out["distribution"][key] = {
            "moyenne": float(col.mean()), "ecart_type": float(col.std(ddof=1)),
            "p05": float(col.quantile(0.05)), "median": float(col.median()),
            "p95": float(col.quantile(0.95)),
            "centile_reel": float(100.0 * (col < value).mean()),
            "valeurs": [float(v) for v in col],
        }
    sh = dist["sharpe"].dropna()
    out["z_sharpe"] = float((real["sharpe"] - sh.mean()) / sh.std(ddof=1)) if len(sh) > 2 else float("nan")
    out["p_hasard_fait_mieux"] = float((sh > real["sharpe"]).mean()) if len(sh) else float("nan")
    return out


# ---------------------------------------------------------------------------
# 3. Decomposition
# ---------------------------------------------------------------------------
def decomposition(prices: dict, cfg, start=None) -> pd.DataFrame:
    """Empile les briques une par une pour voir laquelle apporte quelque chose.

    indice -> univers entier equipondere -> selection factorielle ->
    ponderation -> filtre de regime. Chaque ligne ajoute UNE brique a la
    precedente, et la colonne `delta_cagr` dit ce que cette brique a coute ou
    rapporte. Une selection factorielle qui affiche un delta nul ne selectionne
    rien : elle brasse.
    """
    rf = float(cfg.get("execution.risk_free_annual", 0.0))
    top_n = int(cfg.get("portfolio.top_n", 20))
    weighting = cfg.get("portfolio.weighting", "inv_vol")
    regime = bool(cfg.get("regime.enabled", False))
    n_assets = len(_asset_matrix(prices, cfg).columns)

    etapes = [
        ("univers entier, equipondere",
         {"regime.enabled": False, "portfolio.top_n": n_assets,
          "portfolio.weighting": "equal", "portfolio.max_weight": 1.0}),
        ("+ selection factorielle (top %d, equipondere)" % top_n,
         {"regime.enabled": False, "portfolio.top_n": top_n,
          "portfolio.weighting": "equal", "portfolio.max_weight": 1.0}),
    ]
    if weighting != "equal":
        etapes.append(("+ ponderation %s" % weighting,
                       {"regime.enabled": False, "portfolio.top_n": top_n,
                        "portfolio.weighting": weighting}))
    if regime:
        etapes.append(("+ filtre de regime (strategie complete)",
                       {"regime.enabled": True, "portfolio.top_n": top_n,
                        "portfolio.weighting": weighting}))

    rows, previous = [], None
    reference = None
    for i, (label, overrides) in enumerate(etapes):
        res = _slice(run_backtest(prices, cfg.with_overrides(overrides)), start)
        st = metrics.compute(res, rf)
        if reference is None and st.get("benchmark_cagr") is not None:
            reference = {"etape": "indice de reference",
                         "cagr": st["benchmark_cagr"], "sharpe": st.get("benchmark_sharpe"),
                         "max_drawdown": st.get("benchmark_max_drawdown"),
                         "delta_cagr": float("nan"), "delta_sharpe": float("nan"),
                         "annual_turnover": 0.0}
            rows.append(reference)
            previous = reference
        row = {"etape": label, "cagr": st["cagr"], "sharpe": st["sharpe"],
               "max_drawdown": st["max_drawdown"],
               "annual_turnover": st.get("annual_turnover", float("nan")),
               "delta_cagr": st["cagr"] - previous["cagr"] if previous else float("nan"),
               "delta_sharpe": st["sharpe"] - previous["sharpe"] if previous else float("nan")}
        rows.append(row)
        previous = row
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Le score CLASSE-t-il, ou se contente-t-il de trouver les extremes ?
# ---------------------------------------------------------------------------
def decile_returns(prices: dict, cfg, start=None, end=None, n_buckets: int = 10,
                   top_n: Optional[int] = None) -> dict:
    """Rendement moyen par decile de score, plus les `top_n` extremes de chaque bord.

    Pourquoi cette mesure existe
    ----------------------------
    L'IC et la decomposition peuvent se contredire, et ce n'est pas un bug :
    ils ne mesurent pas la meme chose. L'IC est une correlation de rang sur
    TOUT le classement - il capte une relation monotone moyenne. Un
    portefeuille de 20 titres sur 500 ne vit que dans la queue droite. Un score
    peut donc avoir un IC nul et produire un top 20 brillant.

    Reste a savoir POURQUOI le top 20 brille. Deux causes possibles, aux
    consequences opposees :

    * le score classe reellement -> le rendement croit avec le decile, et
      l'ecart entre le haut et le bas du classement est significatif ;
    * le score trouve les titres EXTREMES -> les deux bouts du classement
      surperforment, le milieu sous-performe, et l'ecart haut-bas est nul.
      Dans ce cas le top 20 ne doit rien a la selection : acheter les 20 PIRES
      scores marche aussi bien. C'est un profil en U, et il est d'autant plus
      trompeur que l'univers est constitue de survivants - les titres extremes
      qui ont survecu sont, par construction, ceux qui sont montes.

    La colonne qui decide est `spread` : l'ecart entre les meilleurs et les
    pires scores. Sans elle, une decomposition flatteuse fait croire a un edge
    qui n'existe pas.
    """
    close = _asset_matrix(prices, cfg)
    score, _ = F.composite_score(close, cfg)
    dates = rebalance_dates(close.index, cfg.get("execution.rebalance", "monthly"))
    if start is not None:
        dates = dates[dates >= pd.Timestamp(start)]
    if end is not None:
        dates = dates[dates <= pd.Timestamp(end)]
    lag = int(cfg.get("execution.execution_lag", 1))
    top_n = int(top_n or cfg.get("portfolio.top_n", 20))

    positions = close.index.get_indexer(dates)
    seaux = [[] for _ in range(n_buckets)]
    hauts, bas, univers = [], [], []
    for k, p in enumerate(positions):
        if k + 1 >= len(dates):
            break
        entree = p + lag
        sortie = close.index.get_indexer([dates[k + 1]])[0]
        if entree >= sortie or sortie >= len(close.index):
            continue
        forward = close.iloc[sortie] / close.iloc[entree] - 1.0
        paire = pd.concat([score.loc[close.index[p]].rename("s"),
                           forward.rename("r")], axis=1).dropna()
        if len(paire) < max(5 * n_buckets, 2 * top_n):
            continue
        paire = paire.sort_values("s")
        rangs = paire["s"].rank(method="first")
        seau = pd.qcut(rangs, n_buckets, labels=False)
        for b in range(n_buckets):
            seaux[b].append(float(paire["r"][seau == b].mean()))
        hauts.append(float(paire["r"].iloc[-top_n:].mean()))
        bas.append(float(paire["r"].iloc[:top_n].mean()))
        univers.append(float(paire["r"].mean()))

    if len(univers) < 5:
        return {"n_dates": len(univers), "deciles": [], "spread": None}

    ref = np.asarray(univers)

    def _vs_univers(serie):
        a = np.asarray(serie)
        ecart = a - ref
        t = float(ecart.mean() / ecart.std(ddof=1) * math.sqrt(len(ecart)))
        return {"rendement": float(a.mean()), "ecart": float(ecart.mean()),
                "t_stat": t, "p_value": _two_sided_p(t, len(ecart))}

    ecart_hb = np.asarray(hauts) - np.asarray(bas)
    t_hb = float(ecart_hb.mean() / ecart_hb.std(ddof=1) * math.sqrt(len(ecart_hb)))
    return {
        "n_dates": int(len(univers)),
        "n_buckets": int(n_buckets),
        "top_n": top_n,
        "univers": float(ref.mean()),
        "deciles": [dict(rang=b + 1, **_vs_univers(seaux[b])) for b in range(n_buckets)],
        "meilleurs": _vs_univers(hauts),
        "pires": _vs_univers(bas),
        "spread": {"moyenne": float(ecart_hb.mean()), "t_stat": t_hb,
                   "p_value": _two_sided_p(t_hb, len(ecart_hb))},
    }


def inverted_score(prices: dict, cfg, start=None) -> dict:
    """Relance la meme strategie avec le score RETOURNE.

    Le test le plus court qui soit : un vrai signal doit s'effondrer quand on
    l'inverse. Si acheter les 20 pires scores rapporte autant que les 20
    meilleurs, le score ne classe pas - il selectionne des titres particuliers
    (les plus volatils, les plus extremes) dont les deux bouts se ressemblent.
    """
    rf = float(cfg.get("execution.risk_free_annual", 0.0))
    n_assets = len(_asset_matrix(prices, cfg).columns)
    original = F.composite_score

    def _mesure(c):
        return metrics.compute(_slice(run_backtest(prices, c), start), rf)

    try:
        normal = _mesure(cfg)
        F.composite_score = lambda cl, k: (-original(cl, k)[0], original(cl, k)[1])
        inverse = _mesure(cfg)
    finally:
        F.composite_score = original
    univers = _mesure(cfg.with_overrides({
        "regime.enabled": False, "portfolio.top_n": n_assets,
        "portfolio.weighting": "equal", "portfolio.max_weight": 1.0}))

    garde = ("cagr", "sharpe", "max_drawdown", "volatility", "annual_turnover")
    return {
        "normal": {k: normal.get(k) for k in garde},
        "inverse": {k: inverse.get(k) for k in garde},
        "univers": {k: univers.get(k) for k in garde},
        "ecart_cagr": float(normal["cagr"] - inverse["cagr"]),
        "ecart_sharpe": float(normal["sharpe"] - inverse["sharpe"]),
    }
