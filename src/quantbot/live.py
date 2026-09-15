"""Portefeuille cible du jour : le calcul, une seule fois, pour tout le monde.

`daily_signals.py` (qui imprime les ordres a passer a la main) et `trade.py`
(qui les envoie au courtier) DOIVENT calculer exactement la meme chose. Les
faire diverger, ne serait-ce que d'un detail de calendrier, produirait un bot
qui n'execute pas ce que l'humain a valide - le genre d'ecart qu'on ne
remarque qu'apres coup. D'ou ce module unique.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from . import factors as F
from .backtest import DEFAULT_MIN_HISTORY, DEFAULT_STALE_DAYS, _prepare_prices
from .portfolio import (parametres_diversification, rebalance_dates,
                        rendements_correlation, target_weights)
from .volatilite import parametres as parametres_volatilite


@dataclass
class Cible:
    """Ce que le portefeuille devrait etre a la cloture connue."""
    as_of: pd.Timestamp                 # derniere seance disponible
    poids: pd.Series                    # poids cibles strictement positifs
    scores: pd.Series                   # score composite a cette date
    cours: pd.Series                    # dernier cours connu de chaque titre
    est_jour_execution: bool
    date_signal: Optional[pd.Timestamp]
    regime_actif: Optional[bool]        # None = filtre desactive
    regime_texte: str
    anciennete_donnees: int             # SEANCES manquantes depuis la derniere cloture
    #: Seance dont la cloture a decide des poids. C'est la date de signal un
    #: jour d'execution, la derniere seance connue le reste du temps.
    date_decision: Optional[pd.Timestamp] = None
    eligibles: pd.Series = field(default_factory=pd.Series)

    @property
    def part_liquidites(self) -> float:
        return float(1.0 - self.poids.sum())


def signaux_echus(index, frequence: str = "monthly"):
    """Dates de signal REVOLUES, celle de la periode en cours exclue.

    `rebalance_dates` renvoie la derniere seance disponible de chaque periode.
    Pour la periode en cours, c'est donc aujourd'hui - une date qui n'est pas
    encore une fin de mois. La confondre avec un vrai signal ferait rebalancer
    le bot tous les jours. On ne garde donc que les dates suivies d'une seance
    appartenant a une autre periode.
    """
    idx = pd.DatetimeIndex(index)
    candidates = rebalance_dates(idx, frequence)
    if len(candidates) == 0:
        return pd.DatetimeIndex([])
    periodes = {"weekly": "W", "monthly": "M", "quarterly": "Q"}[frequence]
    revolues = []
    for date in candidates:
        position = idx.get_indexer([date])[0]
        if position + 1 >= len(idx):
            continue                      # aucune seance apres : periode en cours
        if idx[position + 1].to_period(periodes) != date.to_period(periodes):
            revolues.append(date)
    return pd.DatetimeIndex(revolues)


def portefeuille_cible(prices: dict, cfg, aujourdhui=None, forcer_signal=None) -> Cible:
    """Calcule le portefeuille vise a partir des dernieres donnees en cache.

    `forcer_signal` impose la seance de decision. Le robot s'en sert pour
    rattraper un signal manque - machine eteinte, coupure reseau - sans avoir
    a mentir sur la date de decision, qui reste celle de la fin de periode.
    """
    close, raw_close, bench = _prepare_prices(prices, cfg)
    as_of = close.index[-1]
    aujourdhui = pd.Timestamp(aujourdhui) if aujourdhui is not None else pd.Timestamp.today()
    # En SEANCES, pas en jours calendaires. Un cache arrete vendredi et
    # consulte lundi affichait "3 jours" et passait pour perime, alors que la
    # cloture de vendredi EST la derniere existante. Le compte en seances dit
    # la seule chose qui compte : combien de clotures manquent.
    from .datafeed import seances_ecoulees
    anciennete = seances_ecoulees(as_of, aujourdhui)

    # -- sommes-nous un jour d'execution ? --------------------------------
    # On execute aujourd'hui si la seance situee `lag` jours EN ARRIERE etait
    # une date de signal. Comparer `sched[-1] + lag` a aujourd'hui ne marche
    # pas : `rebalance_dates` renvoie le dernier jour disponible de chaque
    # periode, donc pour la periode en cours c'est toujours aujourd'hui.
    sched = rebalance_dates(close.index, cfg.get("execution.rebalance", "monthly"))
    lag = int(cfg.get("execution.execution_lag", 1))
    idx_signal = len(close.index) - 1 - lag
    date_signal = close.index[idx_signal] if idx_signal >= 0 else None
    est_du = date_signal is not None and date_signal in set(sched)
    if forcer_signal is not None:
        date_signal = pd.Timestamp(forcer_signal)
        est_du = date_signal in set(close.index)

    # -- eligibilite, calculee comme dans le backtest ----------------------
    stale = int(cfg.get("data.stale_days", DEFAULT_STALE_DAYS) or DEFAULT_STALE_DAYS)
    min_hist = int(cfg.get("data.min_history_days", DEFAULT_MIN_HISTORY) or 0)
    has_raw = raw_close.notna()
    eligible = (has_raw.rolling(stale, min_periods=1).max().fillna(0).astype(bool)
                & (has_raw.cumsum() >= min_hist))

    score, _ = F.composite_score(close, cfg)
    vol = F.realized_volatility(close, int(cfg.get("portfolio.vol_window", 63)))

    # Les poids sont calcules a la DATE DE SIGNAL, pas a la derniere seance
    # connue. La nuance est decisive : quand on execute le lendemain d'une fin
    # de mois, la derniere seance du cache est la seance EN COURS, dont la
    # barre est partielle. Calculer dessus reviendrait a decider sur un cours
    # qui n'existe pas encore, et a s'ecarter de ce que le backtest modelise -
    # lui decide a la cloture du signal et execute le lendemain.
    date_decision = date_signal if (est_du and date_signal is not None) else as_of
    # Toute option lue par le backtest DOIT l'etre ici aussi. Un reglage actif
    # d'un cote et absent de l'autre fait vivre au compte une strategie
    # differente de celle qui a ete mesuree, sans le moindre signal. Le
    # pilotage de volatilite avait ete oublie ici : le backtest le mesurait,
    # le compte tradait sans.
    div = parametres_diversification(cfg)
    pil = parametres_volatilite(cfg)
    poids = target_weights(
        score, vol, pd.DatetimeIndex([date_decision]),
        top_n=int(cfg.get("portfolio.top_n", 20)),
        weighting=cfg.get("portfolio.weighting", "inv_vol"),
        max_weight=float(cfg.get("portfolio.max_weight", 1.0)),
        eligible=eligible,
        rendements=(rendements_correlation(close, raw_close)
                    if (div or pil) else None),
        diversification=div,
        volatilite=pil,
    ).iloc[0]

    # -- filtre de regime ---------------------------------------------------
    # Meme regle : on lit le regime a la date de decision, pas au dernier
    # cours connu, pour que la strategie vecue soit celle qui a ete mesuree.
    regime_actif, regime_texte = None, "desactive"
    if cfg.get("regime.enabled", False):
        serie = bench if bench is not None else close.mean(axis=1)
        actif = F.regime_filter(serie, int(cfg.get("regime.ma_window", 200)))
        regime_actif = bool(actif.loc[:date_decision].ffill().iloc[-1])
        moyenne = serie.rolling(int(cfg.get("regime.ma_window", 200))).mean().loc[date_decision]
        regime_texte = ("%s (indice %.1f vs moyenne %.1f)"
                        % ("RISK-ON" if regime_actif else "RISK-OFF",
                           serie.loc[date_decision], moyenne))
        if not regime_actif:
            poids = poids * (1.0 - float(cfg.get("regime.cash_weight_when_off", 1.0)))

    return Cible(
        as_of=as_of,
        poids=poids[poids > 1e-6].sort_values(ascending=False),
        scores=score.loc[date_decision],
        date_decision=date_decision,
        cours=close.loc[as_of],
        est_jour_execution=bool(est_du),
        date_signal=date_signal,
        regime_actif=regime_actif,
        regime_texte=regime_texte,
        anciennete_donnees=anciennete,
        eligibles=eligible.loc[date_decision],
    )
