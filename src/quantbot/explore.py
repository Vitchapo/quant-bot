"""Couche de calcul du tableau de bord.

Un seul endroit produit les chiffres, que l'interface soit servie en direct
par `scripts/dashboard.py` ou figee dans un fichier HTML autonome. Sans cela
les deux versions finiraient par diverger, et c'est exactement le genre
d'ecart qui fait perdre confiance dans un outil de mesure.

`CONTROLS` est declaratif : il decrit les reglages une fois, et sert a la fois
a construire le formulaire HTML, a valider ce que renvoie le navigateur et a
fabriquer les surcharges de configuration. Ajouter un reglage = ajouter une
ligne ici.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import edge, metrics
from .backtest import run_backtest

# ---------------------------------------------------------------------------
# Description des reglages
#   path  : chemin COMPLET dans la config (le piege du bug n.1 : `top_n` seul
#           creerait une cle que personne ne lit). None = traite a part.
# ---------------------------------------------------------------------------
CONTROLS = [
    {"id": "start", "label": "Debut de periode", "type": "select", "path": None,
     "group": "Periode", "options": [], "help": "Rejoue le backtest complet puis "
     "ne mesure qu'a partir de cette date."},
    {"id": "end", "label": "Fin de periode", "type": "select", "path": None,
     "group": "Periode", "options": []},

    {"id": "regime", "label": "Sortir du marche quand il baisse", "type": "bool",
     "path": "regime.enabled", "group": "Strategie",
     "help": "Vend tout et attend en liquidites tant que l'indice reste sous sa moyenne "
             "des 200 derniers jours. Protege des grandes baisses, coute du rendement le "
             "reste du temps."},
    {"id": "ma_window", "label": "Moyenne mobile du regime", "type": "int",
     "path": "regime.ma_window", "min": 50, "max": 300, "step": 10, "group": "Strategie"},
    {"id": "top_n", "label": "Nombre de titres detenus", "type": "int",
     "path": "portfolio.top_n", "min": 1, "max": 120, "step": 1, "group": "Strategie",
     "help": "Combien de titres le portefeuille detient a la fois. Mets-le au nombre "
             "total de titres pour tout detenir sans rien choisir : c'est la reference."},
    {"id": "weighting", "label": "Repartition de l'argent", "type": "choice",
     "path": "portfolio.weighting", "options": ["equal", "inv_vol"], "group": "Strategie",
     "labels": {"equal": "a parts egales", "inv_vol": "davantage sur les titres calmes"},
     "help": "Comment repartir le capital entre les titres retenus."},
    {"id": "max_weight", "label": "Poids maximal par ligne", "type": "float",
     "path": "portfolio.max_weight", "min": 0.01, "max": 1.0, "step": 0.01, "group": "Strategie"},

    {"id": "diversif", "label": "Eviter les titres qui bougent ensemble", "type": "bool",
     "path": "portfolio.diversification.active", "group": "Strategie",
     "help": "Le classement retient surtout des titres volatils du meme secteur : "
             "20 lignes qui montent et descendent ensemble valent en realite 3 paris. "
             "Cette option descend le classement en ecartant les candidats trop "
             "ressemblants. Mesure : volatilite 23% -> 19%, rendement 16% -> 14%, "
             "Sharpe inchange. Baisse le risque, pas le rendement par unite de risque."},
    {"id": "correlation_max", "label": "Ressemblance maximale toleree", "type": "float",
     "path": "portfolio.diversification.correlation_max", "min": 0.0, "max": 1.0,
     "step": 0.05, "group": "Strategie",
     "help": "0 = titres totalement independants exiges, 1 = aucune contrainte. "
             "Sans effet si l'option ci-dessus est decochee."},

    {"id": "w_mom", "label": "Poids momentum", "type": "float",
     "path": "factors.momentum.weight", "min": 0.0, "max": 2.0, "step": 0.1, "group": "Facteurs"},
    {"id": "w_vol", "label": "Poids faible volatilite", "type": "float",
     "path": "factors.low_volatility.weight", "min": 0.0, "max": 2.0, "step": 0.1,
     "group": "Facteurs", "help": "IC mesure negatif sur les grandes capitalisations "
     "americaines : ce facteur y a coute de l'argent."},
    {"id": "w_trend", "label": "Poids tendance", "type": "float",
     "path": "factors.trend.weight", "min": 0.0, "max": 2.0, "step": 0.1, "group": "Facteurs"},
    {"id": "mom_lookback", "label": "Fenetre du momentum", "type": "int",
     "path": "factors.momentum.lookback", "min": 42, "max": 504, "step": 21, "group": "Facteurs"},
    {"id": "mom_skip", "label": "Mois saute (momentum)", "type": "int",
     "path": "factors.momentum.skip", "min": 0, "max": 63, "step": 1, "group": "Facteurs"},
    {"id": "trend_window", "label": "Moyenne mobile de tendance", "type": "int",
     "path": "factors.trend.window", "min": 20, "max": 300, "step": 10, "group": "Facteurs"},

    {"id": "rebalance", "label": "Frequence des ordres", "type": "choice",
     "path": "execution.rebalance", "options": ["weekly", "monthly", "quarterly"],
     "labels": {"weekly": "chaque semaine", "monthly": "chaque mois",
                "quarterly": "chaque trimestre"},
     "group": "Execution"},
    {"id": "lag", "label": "Delai signal -> execution (jours)", "type": "int",
     "path": "execution.execution_lag", "min": 0, "max": 5, "step": 1, "group": "Execution"},
    {"id": "commission_bps", "label": "Frais par transaction (bps)", "type": "float",
     "path": "execution.commission_bps", "min": 0.0, "max": 50.0, "step": 0.5,
     "group": "Execution"},
    {"id": "slippage_bps", "label": "Glissement (bps)", "type": "float",
     "path": "execution.slippage_bps", "min": 0.0, "max": 50.0, "step": 0.5,
     "group": "Execution"},
    {"id": "haircut_bps", "label": "Decote sur radiation (bps)", "type": "float",
     "path": "execution.delisting_haircut_bps", "min": 0.0, "max": 5000.0, "step": 100.0,
     "group": "Execution", "help": "2000 = on suppose -20% le jour ou un titre sort "
     "de la cote. 0 = hypothese optimiste de vente au dernier cours."},
]

CONTROLS_BY_ID = {c["id"]: c for c in CONTROLS}

#: Reglages que l'export HTML autonome sait faire varier (les autres exigent
#: un recalcul, donc un serveur).
STATIC_AXES = ["top_n", "weighting", "regime"]

#: Reglages montres en niveau de lecture "simple". Les autres restent
#: disponibles, mais seulement en mode complet : quatorze curseurs sur une
#: page d'accueil, c'est un mur pour qui debute.
SIMPLE_IDS = {"start", "end", "regime", "top_n", "weighting"}


def defaults(cfg, close_index=None) -> dict:
    """Valeurs de depart, lues dans la configuration elle-meme."""
    out = {}
    for c in CONTROLS:
        if c["path"] is None:
            continue
        value = cfg.get(c["path"])
        if c["type"] == "bool":
            value = bool(value)
        elif c["type"] == "int" and value is not None:
            value = int(value)
        elif c["type"] == "float" and value is not None:
            value = float(value)
        out[c["id"]] = value
    out["start"] = ""
    out["end"] = ""
    return out


def sanitize(values: dict, cfg) -> dict:
    """Nettoie ce qui vient du navigateur. Rien n'est jamais evalue."""
    clean = defaults(cfg)
    for c in CONTROLS:
        if c["id"] not in values:
            continue
        raw = values[c["id"]]
        try:
            if c["type"] == "bool":
                clean[c["id"]] = bool(raw)
            elif c["type"] == "int":
                clean[c["id"]] = int(np.clip(int(raw), c["min"], c["max"]))
            elif c["type"] == "float":
                clean[c["id"]] = float(np.clip(float(raw), c["min"], c["max"]))
            elif c["type"] == "choice":
                clean[c["id"]] = raw if raw in c["options"] else clean[c["id"]]
            elif c["type"] == "select":
                clean[c["id"]] = str(raw)[:10]
        except (TypeError, ValueError):
            pass
    return clean


def overrides(values: dict) -> dict:
    """Traduit les reglages en surcharges de configuration a chemin complet."""
    return {c["path"]: values[c["id"]]
            for c in CONTROLS if c["path"] is not None and c["id"] in values}


def configure(cfg, values: dict):
    # strict=True : un chemin inexistant leve, il ne cree pas une cle morte.
    return cfg.with_overrides(overrides(values), strict=True)


# ---------------------------------------------------------------------------
# Series pretes a dessiner
# ---------------------------------------------------------------------------
def _thin(series: pd.Series, step: int = 5):
    """Un point par semaine environ, dernier point toujours conserve."""
    s = series.dropna()
    if len(s) == 0:
        return [], []
    keep = list(range(0, len(s), max(step, 1)))
    if keep[-1] != len(s) - 1:
        keep.append(len(s) - 1)
    sub = s.iloc[keep]
    return [d.strftime("%Y-%m-%d") for d in sub.index], [round(float(v), 2) for v in sub.to_numpy()]


def _curve(result, key="equity"):
    series = getattr(result, key)
    return _thin(series / series.dropna().iloc[0] * 100.0)


def _drawdown(result):
    eq = result.equity.dropna()
    return _thin(metrics.drawdown_series(eq) * 100.0)


def _mensuels(series: pd.Series):
    """Rendement de chaque mois civil, calcule sur l'equite JOURNALIERE.

    Ici, cote serveur, et non dans le navigateur a partir de la courbe
    dessinee : celle-ci est amincie a un point par semaine, donc ses fins de
    mois tombent jusqu'a quatre seances a cote des vraies. Sur une carte ou
    chaque case se lit seule, cet ecart est du meme ordre que le rendement
    d'un mois calme - la case mentirait.

    Le premier mois, souvent partiel, est mesure depuis la premiere valeur
    connue. Regroupement par (annee, mois) plutot que `resample` : l'alias de
    frequence a change entre pandas 1.x et 2.2 ('M' -> 'ME'), et le projet
    tourne sur les deux (Python 3.8 plafonne pandas a 2.0).
    """
    s = series.dropna()
    if len(s) < 2:
        return [], []
    fins = s.groupby([s.index.year, s.index.month]).last()
    precedent = fins.shift(1)
    precedent.iloc[0] = s.iloc[0]
    r = (fins / precedent - 1.0).to_numpy(dtype="float64")
    mois = ["%04d-%02d" % (a, m) for a, m in fins.index]
    return mois, [round(float(x), 6) if np.isfinite(x) else None for x in r]


def _mensuels_alignes(strat, univ):
    """Les trois series mensuelles sur le MEME calendrier de mois.

    Strategie et univers partagent le calendrier du backtest ; l'indice peut
    commencer plus tard ou s'arreter plus tot. On l'aligne mois par mois, avec
    None la ou il n'existe pas - jamais de report d'un mois sur l'autre.
    """
    mois, s = _mensuels(strat.equity)
    mois_u, u = _mensuels(univ.equity)
    par_u = dict(zip(mois_u, u))
    out = {"mois": mois, "strategie": s, "univers": [par_u.get(m) for m in mois],
           "indice": [None] * len(mois)}
    if strat.benchmark is not None:
        mois_i, i = _mensuels(strat.benchmark)
        par_i = dict(zip(mois_i, i))
        out["indice"] = [par_i.get(m) for m in mois]
    return out


def _records(df):
    """DataFrame -> liste de dictionnaires JSON-compatibles.

    La substitution des NaN par None via pandas ne se comporte pas de la meme
    facon selon la version installee ; on convertit donc a la main, une bonne
    fois, et le resultat est serialisable sans surprise.
    """
    out = []
    for row in df.to_dict("records"):
        out.append({k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                    for k, v in row.items()})
    return out


class Explorer:
    """Garde le panel en memoire et met en cache ce qui ne depend pas des facteurs."""

    def __init__(self, prices: dict, cfg):
        self.prices = prices
        self.cfg = cfg
        self._cache = {}
        close = edge._asset_matrix(prices, cfg)
        self.n_assets = int(close.shape[1])
        self.index = close.index
        self.years = sorted({str(d.year) for d in close.index})

    # -- reference "univers entier", independante des reglages de facteurs --
    def _universe_key(self, v):
        return ("univers", v["rebalance"], v["lag"], v["commission_bps"],
                v["slippage_bps"], v["haircut_bps"])

    def universe_run(self, values):
        key = self._universe_key(values)
        if key not in self._cache:
            over = dict(overrides(values))
            over.update({"regime.enabled": False, "portfolio.top_n": self.n_assets,
                         "portfolio.weighting": "equal", "portfolio.max_weight": 1.0})
            self._cache[key] = run_backtest(self.prices, self.cfg.with_overrides(over, strict=True))
        return self._cache[key]

    # -- appel principal ----------------------------------------------------
    def run(self, values: dict) -> dict:
        t0 = time.time()
        v = sanitize(values, self.cfg)
        start, end = v.get("start") or None, v.get("end") or None
        rf = float(self.cfg.get("execution.risk_free_annual", 0.0))

        strat = edge._slice(run_backtest(self.prices, configure(self.cfg, v)), start, end)
        univ = edge._slice(self.universe_run(v), start, end)
        st = metrics.compute(strat, rf)
        su = metrics.compute(univ, rf)

        dates, strat_curve = _curve(strat)
        _, univ_curve = _curve(univ)
        bench_curve = []
        if strat.benchmark is not None:
            b = strat.benchmark.dropna()
            if len(b) > 2:
                bench_dates, bench_curve = _thin(b / b.iloc[0] * 100.0)
                if bench_dates != dates:      # alignement defensif
                    bench_curve = list(pd.Series(bench_curve, index=pd.to_datetime(bench_dates))
                                       .reindex(pd.to_datetime(dates)).ffill().round(2)
                                       .where(lambda s: s.notna(), None))
        dd_dates, dd_strat = _drawdown(strat)
        _, dd_univ = _drawdown(univ)

        return {
            "stats": {k: (None if isinstance(x, float) and not np.isfinite(x) else x)
                      for k, x in st.items()},
            "stats_univers": {k: (None if isinstance(x, float) and not np.isfinite(x) else x)
                              for k, x in su.items()},
            "dates": dates,
            "series": {"strategie": strat_curve, "univers": univ_curve, "indice": bench_curve},
            "dd_dates": dd_dates,
            "drawdowns": {"strategie": dd_strat, "univers": dd_univ},
            "mensuels": _mensuels_alignes(strat, univ),
            "n_assets": self.n_assets,
            "duree_ms": int(1000 * (time.time() - t0)),
            "values": v,
        }

    def run_periods(self, values: dict, starts) -> dict:
        """Un seul backtest, decoupe en plusieurs fenetres.

        L'export precalcule une grille de variantes x periodes. Relancer le
        moteur pour chaque fenetre serait un gachis pur : le backtest est
        identique, seule la fenetre de mesure change. On calcule donc une fois
        et on decoupe - ce qui divise le temps d'export par le nombre de
        periodes, et rend l'export tenable sur un univers de 500 titres.
        """
        v = sanitize(values, self.cfg)
        rf = float(self.cfg.get("execution.risk_free_annual", 0.0))
        strat = run_backtest(self.prices, configure(self.cfg, v))
        univ = self.universe_run(v)

        def clean(stats):
            return {k: (None if isinstance(x, float) and not np.isfinite(x) else x)
                    for k, x in stats.items()}

        dates, curve = _curve(strat)
        _, univ_curve = _curve(univ)
        indice = []
        if strat.benchmark is not None:
            b = strat.benchmark.dropna()
            if len(b) > 2:
                bd, bc = _thin(b / b.iloc[0] * 100.0)
                indice = list(pd.Series(bc, index=pd.to_datetime(bd))
                              .reindex(pd.to_datetime(dates)).ffill().round(2)
                              .where(lambda x: x.notna(), None))
        out = {"dates": dates, "curve": curve, "univers": univ_curve, "indice": indice,
               "periods": {}, "univers_periods": {},
               "mensuels": _mensuels_alignes(strat, univ)}
        for s in starts:
            key = s or "full"
            out["periods"][key] = clean(metrics.compute(edge._slice(strat, s or None), rf))
            out["univers_periods"][key] = clean(metrics.compute(edge._slice(univ, s or None), rf))
        return out

    def decompose(self, values: dict) -> dict:
        v = sanitize(values, self.cfg)
        df = edge.decomposition(self.prices, configure(self.cfg, v),
                                start=v.get("start") or None)
        return {"lignes": _records(df)}

    def ic(self, values: dict) -> dict:
        v = sanitize(values, self.cfg)
        df = edge.information_coefficient(self.prices, configure(self.cfg, v),
                                          start=v.get("start") or None,
                                          end=v.get("end") or None)
        return {"lignes": _records(df)}

    def deciles(self, values: dict) -> dict:
        v = sanitize(values, self.cfg)
        cfg = configure(self.cfg, v)
        out = edge.decile_returns(self.prices, cfg, start=v.get("start") or None,
                                  end=v.get("end") or None)
        out["inverse"] = edge.inverted_score(self.prices, cfg, start=v.get("start") or None)
        return out

    def null(self, values: dict, n_draws: int = 150, budget: float = 90.0) -> dict:
        v = sanitize(values, self.cfg)
        out = edge.random_null(self.prices, configure(self.cfg, v), n_draws=n_draws,
                               start=v.get("start") or None, time_budget=budget)
        out["reel"] = {k: (None if isinstance(x, float) and not np.isfinite(x) else x)
                       for k, x in out["reel"].items()}
        return out
