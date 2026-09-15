"""Moteur de backtest vectorise-par-jour.

Chronologie retenue (c'est le point critique de tout backtest)
--------------------------------------------------------------
    jour t          : cloture connue -> calcul du score et des poids cibles
    jour t + lag    : passage des ordres a la cloture, frais preleves
    jour t + lag + 1: le portefeuille commence a produire des rendements

Autrement dit, aucun rendement n'est jamais attribue a une position decidee
le meme jour. Avec le reglage par defaut `execution_lag = 1`, il s'ecoule une
journee complete entre le signal et l'execution : c'est le rythme reel d'un
bot qui tourne apres la cloture et passe ses ordres le lendemain.

Entre deux rebalancements les poids DERIVENT avec les cours (on ne touche a
rien), ce qui est le comportement reel d'un portefeuille et donne une mesure
honnete de la rotation.

Traitement des interruptions de cotation
----------------------------------------
Un jour sans cours n'est pas un jour a rendement nul. Deux cas distincts :

* suspension puis reprise : les cours sont prolonges sans limite de duree,
  si bien que le jour de la reprise porte la variation COMPLETE depuis la
  derniere cotation. Un titre suspendu quinze jours qui rouvre a -50% coute
  bien -50% au portefeuille.
* arret definitif (radiation, faillite) : passe `data.stale_days` seances
  sans cours reel, le titre devient intraitable. S'il est en portefeuille, il
  est solde au dernier cours connu, frais compris, et le compteur
  `delistings` du resultat le signale.

Le dernier cours connu est une hypothese OPTIMISTE pour une faillite et
pessimiste pour un rachat avec prime. `execution.delisting_haircut_bps`
permet de tester la sensibilite du resultat a cette hypothese.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import factors as F
from .portfolio import (parametres_diversification, rebalance_dates,
                        rendements_correlation, target_weights)
from .volatilite import parametres as parametres_volatilite

#: Defaut unique, pour que backtest et daily_signals ne divergent jamais.
DEFAULT_MIN_HISTORY = 250
DEFAULT_STALE_DAYS = 10


class BenchmarkMissingError(RuntimeError):
    """Le ticker de reference configure est absent des donnees chargees."""


@dataclass
class BacktestResult:
    equity: pd.Series                 # valeur du portefeuille
    returns: pd.Series                # rendements quotidiens nets de frais
    weights: pd.DataFrame             # poids detenus a la cloture de chaque jour
    cash: pd.Series
    turnover: pd.Series               # rotation (somme des |variations de poids|)
    costs: pd.Series                  # frais preleves (fraction de la valeur)
    trades: pd.DataFrame              # detail des mouvements
    benchmark: pd.Series | None = None
    meta: dict = field(default_factory=dict)

    @property
    def net_exposure(self) -> pd.Series:
        return self.weights.sum(axis=1)


def _prepare_prices(prices: dict[str, pd.DataFrame], cfg):
    """Prepare les matrices de travail.

    Renvoie (close, raw_close, benchmark) ou `close` est prolonge sans limite
    (pour que la reprise apres suspension porte la variation complete) et
    `raw_close` conserve les trous (pour savoir ce qui cote vraiment).
    """
    from .datafeed import to_matrix

    benchmark_ticker = cfg.get("universe.benchmark")

    # Le ticker de reference est EXCLU de l'univers investissable. La regle
    # "commence par ^" ne suffit plus : depuis le passage a SPY (ETF total
    # return) le benchmark est un ticker ordinaire, et sans cette exclusion il
    # devient un titre selectionnable par le score - la strategie pourrait
    # detenir l'indice qu'elle est censee battre.
    assets = {t: df for t, df in prices.items()
              if t != benchmark_ticker and not t.startswith("^")}

    # min_history=2 : on ne retire ici que les series vides. Le filtre
    # d'anciennete reel est applique jour par jour plus bas, de facon causale
    # (filtrer sur la longueur TOTALE de la serie serait une decision prise
    # avec la connaissance de toute la periode).
    raw_close = to_matrix(assets, "close", min_history=2)

    bench = None
    if benchmark_ticker:
        if benchmark_ticker not in prices:
            # Sans cette verification, un benchmark absent du cache passait
            # inapercu : `metrics` renoncait simplement a beta/alpha, et le
            # filtre de regime basculait en silence sur la moyenne des cours
            # de l'univers - un tout autre signal que celui annonce.
            raise BenchmarkMissingError(
                "Ticker de reference %r absent des donnees chargees.\n"
                "Series disponibles : %d. Lance d'abord :\n"
                "    python scripts/fetch_data.py --config <ta config>\n"
                "ou retire 'universe.benchmark' de la configuration si tu "
                "assumes de tourner sans reference (ni beta, ni alpha, et le "
                "filtre de regime retombe sur la moyenne de l'univers)."
                % (benchmark_ticker, len(prices)))
        bench = prices[benchmark_ticker]["close"].reindex(raw_close.index).ffill()

    return raw_close.ffill(), raw_close, bench


def run_backtest(prices: dict[str, pd.DataFrame], cfg, verbose: bool = False,
                 appartenance: pd.DataFrame | None = None) -> BacktestResult:
    """`appartenance` : matrice booleenne (dates x tickers) d'appartenance a
    l'indice, telle que la produit `pointintime.Appartenance.masque`. Elle est
    combinee par ET a l'eligibilite : un titre non membre ce jour-la n'est ni
    achetable ni conservable. None (le defaut) = aucun filtre d'appartenance,
    donc le comportement historique du projet, inchange.
    """
    close, raw_close, bench = _prepare_prices(prices, cfg)

    # ---- signaux -------------------------------------------------------
    score, raw_factors = F.composite_score(close, cfg)
    # La ponderation inverse-vol utilise TOUJOURS portfolio.vol_window, meme
    # quand le facteur faible-volatilite est actif avec une autre fenetre :
    # ce sont deux usages differents de la meme grandeur.
    vol = F.realized_volatility(close, int(cfg.get("portfolio.vol_window", 63)))

    # ---- eligibilite (causale) ------------------------------------------
    has_raw = raw_close.notna()
    stale_days = int(cfg.get("data.stale_days", DEFAULT_STALE_DAYS) or DEFAULT_STALE_DAYS)
    min_hist = int(cfg.get("data.min_history_days", DEFAULT_MIN_HISTORY) or 0)

    # cote recemment = au moins un cours reel dans les `stale_days` seances
    recent = has_raw.rolling(stale_days, min_periods=1).max().fillna(0).astype(bool)
    seniority = has_raw.cumsum()          # nb de seances reellement cotees
    tradeable = recent & (seniority >= min_hist)

    # Appartenance a l'indice, si elle est fournie. Le reindex avec
    # fill_value=False est volontaire : un titre absent de la matrice
    # d'appartenance est traite comme NON membre, jamais comme membre par
    # defaut - une donnee manquante ne doit pas ouvrir un droit.
    if appartenance is not None:
        membre = appartenance.astype(bool).reindex(
            index=close.index, columns=close.columns, fill_value=False)
        tradeable = tradeable & membre

    # ---- filtre de regime ----------------------------------------------
    regime_on = None
    if cfg.get("regime.enabled", False):
        series = bench if bench is not None else close.mean(axis=1)
        regime_on = F.regime_filter(series, int(cfg.get("regime.ma_window", 200)))
        regime_on = regime_on.reindex(close.index).ffill()

    # ---- calendrier ------------------------------------------------------
    sig_dates = rebalance_dates(close.index, cfg.get("execution.rebalance", "monthly"))
    lag = int(cfg.get("execution.execution_lag", 1))
    pos = close.index.get_indexer(sig_dates)
    exec_pos = pos + lag
    keep = exec_pos < len(close.index)
    sig_dates, exec_pos = sig_dates[keep], exec_pos[keep]
    exec_dates = close.index[exec_pos]

    # Les poids sont calcules sur les donnees de la date de SIGNAL,
    # puis reindexes sur la date d'EXECUTION.
    div = parametres_diversification(cfg)
    pil = parametres_volatilite(cfg)
    tw = target_weights(
        score, vol, sig_dates,
        top_n=int(cfg.get("portfolio.top_n", 20)),
        weighting=cfg.get("portfolio.weighting", "inv_vol"),
        max_weight=float(cfg.get("portfolio.max_weight", 1.0)),
        eligible=tradeable,
        rendements=(rendements_correlation(close, raw_close)
                    if (div or pil) else None),
        diversification=div,
        volatilite=pil,
    )
    tw.index = exec_dates

    if regime_on is not None:
        off = ~regime_on.reindex(sig_dates).fillna(False).to_numpy(dtype=bool)
        scale = 1.0 - float(cfg.get("regime.cash_weight_when_off", 1.0))
        tw.loc[off] = tw.loc[off] * scale

    # ---- parametres d'execution -----------------------------------------
    cost_rate = (float(cfg.get("execution.commission_bps", 0.0))
                 + float(cfg.get("execution.slippage_bps", 0.0))) / 10_000.0
    haircut = float(cfg.get("execution.delisting_haircut_bps", 0.0)) / 10_000.0
    rf_daily = (1.0 + float(cfg.get("execution.risk_free_annual", 0.0))) ** (1 / 252) - 1.0
    nav0 = float(cfg.get("execution.initial_capital", 100_000.0))

    dates = close.index
    n_days, n_assets = close.shape
    ret_filled = np.nan_to_num(close.pct_change().to_numpy(dtype="float64"),
                               nan=0.0, posinf=0.0, neginf=0.0)
    tradeable_arr = tradeable.to_numpy(dtype=bool)

    exec_lookup = {d: i for i, d in enumerate(tw.index)}
    tw_arr = tw.to_numpy(dtype="float64")

    w = np.zeros(n_assets)
    cash = 1.0
    nav = nav0
    n_delistings = 0

    equity = np.empty(n_days)
    daily_ret = np.zeros(n_days)
    w_hist = np.zeros((n_days, n_assets))
    cash_hist = np.empty(n_days)
    turn_hist = np.zeros(n_days)
    cost_hist = np.zeros(n_days)
    trade_rows: list[dict] = []

    def _charge(i: int, cost: float) -> None:
        """Preleve des frais et repercute sur le rendement du jour."""
        nonlocal nav
        nav *= (1.0 - cost)
        daily_ret[i] = (1.0 + daily_ret[i]) * (1.0 - cost) - 1.0
        cost_hist[i] += cost

    for i in range(n_days):
        if i > 0:
            gross = float(w @ ret_filled[i]) + cash * rf_daily
            growth = 1.0 + gross
            nav *= growth
            if growth > 1e-12:
                w = w * (1.0 + ret_filled[i]) / growth
                cash = cash * (1.0 + rf_daily) / growth
            daily_ret[i] = gross

        # -- sortie de cote d'un titre detenu -----------------------------
        dead = (w > 1e-12) & ~tradeable_arr[i]
        if dead.any():
            freed = float(w[dead].sum())
            for k in np.nonzero(dead)[0]:
                last_px = raw_close.iloc[:i + 1, k].last_valid_index()
                trade_rows.append({
                    "date": dates[i], "ticker": close.columns[k],
                    "weight_before": w[k], "weight_after": 0.0, "delta": -w[k],
                    "price": float(close.iat[i, k]), "reason": "sortie de cote",
                    "last_quote": last_px,
                })
            n_delistings += int(dead.sum())
            w[dead] = 0.0
            cash = 1.0 - float(w.sum())
            turn_hist[i] += freed
            _charge(i, freed * (cost_rate + haircut))

        # -- rebalancement -------------------------------------------------
        j = exec_lookup.get(dates[i])
        if j is not None:
            target = np.where(tradeable_arr[i], tw_arr[j], 0.0)
            delta = target - w
            turnover = float(np.abs(delta).sum())
            turn_hist[i] += turnover
            _charge(i, turnover * cost_rate)
            for k in np.nonzero(np.abs(delta) > 1e-6)[0]:
                trade_rows.append({
                    "date": dates[i], "ticker": close.columns[k],
                    "weight_before": w[k], "weight_after": target[k],
                    "delta": delta[k], "price": float(close.iat[i, k]),
                    "reason": "rebalancement", "last_quote": pd.NaT,
                })
            w = target.copy()
            cash = 1.0 - float(w.sum())

        equity[i] = nav
        w_hist[i] = w
        cash_hist[i] = cash

    if verbose:
        actifs = int((turn_hist > 0).sum())
        moyenne = float(turn_hist[turn_hist > 0].mean()) if actifs else 0.0
        print(f"  {len(sig_dates)} dates de signal, {actifs} journees avec des ordres "
              f"(rotation moyenne {moyenne:.1%})")
        if n_delistings:
            print(f"  {n_delistings} sortie(s) de cote soldee(s) au dernier cours connu")

    # ---- indice de reference : periode commune uniquement ----------------
    bench_series = None
    if bench is not None:
        b = bench.dropna()
        if len(b):
            # On ne prolonge JAMAIS l'indice par des rendements nuls avant sa
            # premiere cotation : cela fausserait beta, alpha et tracking error.
            bench_series = (b / b.iloc[0] * nav0).reindex(dates).ffill()

    return BacktestResult(
        equity=pd.Series(equity, index=dates, name="equity"),
        returns=pd.Series(daily_ret, index=dates, name="returns"),
        weights=pd.DataFrame(w_hist, index=dates, columns=close.columns),
        cash=pd.Series(cash_hist, index=dates, name="cash"),
        turnover=pd.Series(turn_hist, index=dates, name="turnover"),
        costs=pd.Series(cost_hist, index=dates, name="costs"),
        trades=pd.DataFrame(trade_rows),
        benchmark=bench_series,
        meta={
            "n_signal_dates": int(len(sig_dates)),
            "n_assets": int(n_assets),
            "n_delistings": n_delistings,
            "cost_rate": cost_rate,
            "diversification": div,
            "volatilite": pil,
            "point_in_time": appartenance is not None,
            "start": str(dates[0].date()),
            "end": str(dates[-1].date()),
            "config": cfg.as_dict(),
        },
    )
