"""Tests des defauts trouves en revue de code.

Chaque test correspond a un bug reel qui passait inapercu : ils sont ici pour
qu'il ne revienne pas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import metrics
from quantbot.backtest import BacktestResult, run_backtest
from quantbot.synthetic import generate_prices
from quantbot.validation import _expand_grid, slice_result


def _ohlcv(close, index):
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6}, index=index)


class TestGrilleDeParametres:
    """La grille declarait `top_n` la ou le moteur lit `portfolio.top_n`.

    Resultat : 12 combinaisons produisant 2 resultats distincts, un
    walk-forward purement decoratif et une sensibilite aux parametres qui
    annoncait une robustesse fictive.
    """

    def test_une_cle_inconnue_leve_une_erreur(self, base_config):
        with pytest.raises(KeyError, match="Parametre inconnu"):
            base_config.with_overrides({"top_n": 10})
        with pytest.raises(KeyError):
            base_config.with_overrides({"momentum.lookback": 126})

    def test_les_cles_des_yaml_livres_sont_valides(self, base_config):
        grid = base_config.get("walk_forward.grid") or {}
        assert grid, "la grille ne doit pas etre vide"
        for chemin in grid:
            assert base_config.has(chemin), f"chemin invalide dans la grille : {chemin}"

    def test_la_grille_produit_bien_des_resultats_differents(self, momentum_prices, base_config):
        grid = {"portfolio.top_n": [5, 40]}
        finals = {}
        for combo in _expand_grid(grid):
            res = run_backtest(momentum_prices, base_config.with_overrides(combo))
            finals[str(combo)] = float(res.equity.iloc[-1])
        assert len(set(finals.values())) == len(finals), \
            f"les combinaisons donnent le meme resultat : la grille n'explore rien ({finals})"


class TestSortiedeCote:
    """Un titre qui cesse d'etre cote ne doit ni etre conserve indefiniment
    a un cours gele, ni voir sa chute effacee."""

    @pytest.fixture
    def univers_avec_radiation(self):
        """T0 est nettement le meilleur titre : la strategie le detient sans
        interruption jusqu'a sa radiation au 1200e jour."""
        idx = pd.bdate_range("2010-01-01", periods=1600)
        prices = {f"T{i}": _ohlcv(100 * np.exp(np.linspace(0, 0.30, 1600)), idx)
                  for i in range(1, 12)}
        prices["T0"] = _ohlcv(100 * np.exp(np.linspace(0, 1.50, 1600)), idx)
        mort = prices["T0"].copy()
        mort.iloc[1200:] = np.nan          # plus aucune cotation
        prices["T0"] = mort
        prices["^BENCH"] = _ohlcv(100 * np.exp(np.linspace(0, 0.35, 1600)), idx)
        return prices

    @pytest.fixture
    def cfg_radiation(self, base_config):
        return base_config.with_overrides({
            "universe.benchmark": "^BENCH", "data.min_history_days": 300,
            "data.stale_days": 10, "portfolio.top_n": 1, "portfolio.max_weight": 1.0,
            "regime.enabled": False, "factors.low_volatility.weight": 0.0,
            "factors.trend.weight": 0.0, "factors.min_valid_factors": 1})

    def test_le_titre_est_bien_detenu_avant_sa_radiation(self, univers_avec_radiation, cfg_radiation):
        res = run_backtest(univers_avec_radiation, cfg_radiation)
        assert res.weights["T0"].iloc[1199] > 0.99, "le scenario ne teste rien si le titre n'est pas detenu"

    def test_la_position_est_soldee_et_comptee(self, univers_avec_radiation, cfg_radiation):
        res = run_backtest(univers_avec_radiation, cfg_radiation)
        derniere = univers_avec_radiation["T0"]["close"].last_valid_index()
        apres = res.weights.loc[res.weights.index > derniere, "T0"]
        stale = int(cfg_radiation.get("data.stale_days"))
        assert (apres.iloc[stale + 2:].abs() < 1e-12).all(), \
            "un titre radie est reste en portefeuille"
        assert res.meta["n_delistings"] >= 1

    def test_le_mouvement_est_trace_avec_son_motif(self, univers_avec_radiation, cfg_radiation):
        res = run_backtest(univers_avec_radiation, cfg_radiation)
        sorties = res.trades[res.trades["reason"] == "sortie de cote"]
        assert len(sorties) >= 1
        assert sorties["price"].notna().all(), "vente enregistree a un prix indefini"
        assert sorties["last_quote"].notna().all(), "date de derniere cotation absente"

    def test_la_decote_de_radiation_est_appliquee(self, univers_avec_radiation, cfg_radiation):
        sans = run_backtest(univers_avec_radiation, cfg_radiation)
        avec = run_backtest(univers_avec_radiation,
                            cfg_radiation.with_overrides({"execution.delisting_haircut_bps": 5000.0}))
        # une decote de 50% sur une position pesant 100% du portefeuille
        ratio = float(avec.equity.iloc[-1] / sans.equity.iloc[-1])
        assert ratio == pytest.approx(0.5, abs=0.02), \
            f"la decote de radiation n'a pas l'effet attendu (ratio {ratio:.3f})"

    def test_le_titre_radie_n_est_plus_rachetable(self, univers_avec_radiation, cfg_radiation):
        """Meme s'il garde le meilleur score (calcule sur un cours gele), un
        titre qui ne cote plus ne doit jamais etre rachete."""
        res = run_backtest(univers_avec_radiation, cfg_radiation)
        derniere = univers_avec_radiation["T0"]["close"].last_valid_index()
        rachats = res.trades[(res.trades["ticker"] == "T0")
                             & (res.trades["date"] > derniere)
                             & (res.trades["delta"] > 1e-9)]
        assert rachats.empty, "un titre radie a ete rachete"


class TestSuspensionDeCotation:
    def test_la_reprise_porte_la_variation_complete(self, base_config):
        """Un titre suspendu 15 jours qui rouvre a -50% doit couter -50%.

        Avec un `ffill` borne a 5 jours, le rendement du jour de reprise
        valait NaN, converti en 0 : la chute disparaissait purement et
        simplement du backtest.
        """
        idx = pd.bdate_range("2010-01-01", periods=1400)
        prices = {}
        for i in range(12):
            prices[f"T{i}"] = _ohlcv(100 * np.exp(np.linspace(0, 0.6, 1400)), idx)
        # T0 est le seul a monter fort -> il sera selectionne
        prices["T0"] = _ohlcv(100 * np.exp(np.linspace(0, 1.4, 1400)), idx)
        suspendu = prices["T0"].copy()
        suspendu.iloc[1200:1215] = np.nan                     # 15 seances sans cours
        suspendu.iloc[1215:] = suspendu.iloc[1199] * 0.5      # reprise a -50%
        prices["T0"] = suspendu
        prices["^BENCH"] = _ohlcv(100 * np.exp(np.linspace(0, 0.5, 1400)), idx)

        cfg = base_config.with_overrides({
            "universe.benchmark": "^BENCH", "data.min_history_days": 300,
            "data.stale_days": 30, "portfolio.top_n": 1, "portfolio.max_weight": 1.0,
            "regime.enabled": False, "execution.commission_bps": 0.0,
            "execution.slippage_bps": 0.0, "factors.low_volatility.weight": 0.0,
            "factors.trend.weight": 0.0, "factors.min_valid_factors": 1})
        res = run_backtest(prices, cfg)
        assert res.weights["T0"].iloc[1210] == pytest.approx(1.0, abs=1e-6)
        choc = res.returns.iloc[1215]
        assert choc < -0.45, f"la chute de -50% n'a pas ete enregistree (rendement {choc:.2%})"


class TestSortino:
    def test_formule_conforme_a_la_definition(self):
        """Deviation a la baisse = racine du moment d'ordre 2 des rendements
        negatifs autour de ZERO, divisee par l'effectif TOTAL."""
        rng = np.random.default_rng(0)
        idx = pd.bdate_range("2010-01-01", periods=3000)
        r = 0.0004 + 0.01 * rng.standard_normal(3000)
        eq = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
        z = pd.Series(0.0, index=idx)
        res = BacktestResult(eq, eq.pct_change().fillna(0.0),
                             pd.DataFrame(1.0, index=idx, columns=["X"]), z, z, z, pd.DataFrame())
        ret = eq.pct_change().fillna(0.0).to_numpy()
        attendu = ret.mean() / np.sqrt(np.mean(np.minimum(ret, 0.0) ** 2)) * np.sqrt(252)
        assert metrics.compute(res)["sortino"] == pytest.approx(attendu, rel=1e-12)

    def test_superieur_au_sharpe_sur_une_serie_symetrique(self):
        rng = np.random.default_rng(1)
        idx = pd.bdate_range("2010-01-01", periods=3000)
        eq = pd.Series(100 * np.exp(np.cumsum(0.0004 + 0.01 * rng.standard_normal(3000))), index=idx)
        z = pd.Series(0.0, index=idx)
        res = BacktestResult(eq, eq.pct_change().fillna(0.0),
                             pd.DataFrame(1.0, index=idx, columns=["X"]), z, z, z, pd.DataFrame())
        st = metrics.compute(res)
        assert st["sortino"] > st["sharpe"]


class TestIndiceDeReference:
    def test_periode_commune_uniquement(self):
        """Un indice qui ne couvre qu'une partie de la periode ne doit pas
        voir ses jours manquants remplaces par des rendements nuls."""
        rng = np.random.default_rng(2)
        idx = pd.bdate_range("2010-01-01", periods=2000)
        eq = pd.Series(100 * np.exp(np.cumsum(0.0004 + 0.01 * rng.standard_normal(2000))), index=idx)
        bench = pd.Series(np.nan, index=idx)
        bench.iloc[1000:] = 100 * np.exp(np.cumsum(0.0003 + 0.009 * rng.standard_normal(1000)))
        z = pd.Series(0.0, index=idx)
        res = BacktestResult(eq, eq.pct_change().fillna(0.0),
                             pd.DataFrame(1.0, index=idx, columns=["X"]), z, z, z,
                             pd.DataFrame(), benchmark=bench)
        st = metrics.compute(res)
        assert st["benchmark_coverage"] == pytest.approx(0.5, abs=0.01)

        # reference : les memes mesures calculees a la main sur la periode commune
        commun = idx[1000:]
        r_s = eq.pct_change().fillna(0.0).loc[commun]
        r_b = bench.loc[commun].pct_change().fillna(0.0)
        beta = np.cov(r_s, r_b, ddof=1)[0, 1] / r_b.var(ddof=1)
        assert st["beta"] == pytest.approx(beta, rel=1e-9)


class TestDecoupageWalkForward:
    def test_equity_et_returns_decrivent_la_meme_serie(self, momentum_prices, base_config):
        res = run_backtest(momentum_prices, base_config)
        sous = slice_result(res, "2015-01-01", "2019-12-31")
        reconstruit = 100_000.0 * (1 + sous.returns).cumprod()
        assert np.allclose(reconstruit.to_numpy(), sous.equity.to_numpy(), rtol=1e-9)

    def test_train_et_test_sont_disjoints(self, momentum_prices, base_config):
        res = run_backtest(momentum_prices, base_config)
        train = slice_result(res, "2012-01-01", "2016-01-01")
        test = slice_result(res, "2016-01-01", "2017-01-01", inclusive_start=False)
        assert train.equity.index[-1] < test.equity.index[0]


class TestJourDExecution:
    def test_le_jour_d_execution_est_detecte(self, base_config):
        """`is_due` etait toujours faux : le point d'entree production etait
        inutilisable sans --force."""
        from quantbot.portfolio import rebalance_dates
        idx = pd.bdate_range("2020-01-01", "2023-06-15")
        sched = set(rebalance_dates(idx, "monthly"))
        lag = 1
        # on rejoue la logique du script pour plusieurs dates de fin
        trouves = 0
        for fin in range(len(idx) - 60, len(idx)):
            sous_idx = idx[:fin + 1]
            sched_local = set(rebalance_dates(sous_idx, "monthly"))
            i = len(sous_idx) - 1 - lag
            if i >= 0 and sous_idx[i] in sched_local and sous_idx[i] in sched:
                trouves += 1
        assert trouves >= 2, "aucun jour d'execution detecte sur deux mois"


class TestFraicheurDesCours:
    """Le cache se completait-il vraiment ?

    Bug reel du 11 septembre 2026 : la regle "recompleter si le cache a plus
    de 3 jours calendaires" toleraient en permanence jusqu'a trois jours de
    retard. Un cache arrete au mardi 8, consulte le vendredi 11, affichait
    exactement 3 jours - pas "plus de 3" - donc aucun telechargement. Et
    `rafraichir()` annoncait quand meme "504 series, 0 manquant(s)".
    """

    def test_le_cas_qui_a_echoue(self):
        from quantbot.datafeed import seances_ecoulees
        # mardi 8 septembre en cache, on est le vendredi 11 : mercredi et
        # jeudi manquent, il FAUT telecharger.
        assert seances_ecoulees("2026-09-08", "2026-09-11") == 2

    def test_la_seance_du_jour_n_est_jamais_comptee(self):
        """Sa barre est partielle tant que le marche n'a pas ferme."""
        from quantbot.datafeed import seances_ecoulees
        assert seances_ecoulees("2026-09-08", "2026-09-09") == 0

    def test_le_week_end_ne_compte_pas(self):
        from quantbot.datafeed import seances_ecoulees
        assert seances_ecoulees("2026-09-11", "2026-09-14") == 0   # vendredi -> lundi
        assert seances_ecoulees("2026-09-11", "2026-09-15") == 1   # lundi manque

    def test_cache_futur_ou_identique(self):
        from quantbot.datafeed import seances_ecoulees
        assert seances_ecoulees("2026-09-14", "2026-09-14") == 0
        assert seances_ecoulees("2026-09-20", "2026-09-14") == 0

    def test_le_rapport_denonce_le_telechargement_vide(self, tmp_path):
        """Des seances attendues + aucune ligne ajoutee = panne, pas succes."""
        import pandas as pd
        from quantbot import datafeed
        from quantbot.config import Config

        idx = pd.bdate_range("2026-08-03", "2026-09-08")
        df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                           "volume": 10.0}, index=idx)
        cache = tmp_path / "px"
        cache.mkdir()
        df.to_parquet(cache / "AAA.parquet")

        cfg = Config({"data": {"cache_dir": str(cache), "start": "2026-01-01",
                               "provider": "yfinance"}})
        # Le fournisseur ne renvoie rien : exactement le cas du 11 septembre.
        vrai = datafeed._download_yfinance
        datafeed._download_yfinance = lambda t, s, e: {}
        try:
            rapport = {}
            out = datafeed.fetch(cfg, ["AAA"], rapport=rapport)
        finally:
            datafeed._download_yfinance = vrai

        assert len(out) == 1                    # la serie est bien la...
        assert rapport["a_completer"] == 1      # ...mais des seances manquaient
        assert rapport["seances_ajoutees"] == 0
        assert rapport["ok"] is False, "un telechargement vide ne doit pas passer pour un succes"

    def test_un_cache_a_jour_ne_declenche_rien(self, tmp_path):
        import pandas as pd
        from quantbot import datafeed
        from quantbot.config import Config

        fin = pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
        while fin.weekday() >= 5:
            fin -= pd.Timedelta(days=1)
        idx = pd.bdate_range(fin - pd.Timedelta(days=40), fin)
        df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                           "volume": 10.0}, index=idx)
        cache = tmp_path / "px"; cache.mkdir()
        df.to_parquet(cache / "AAA.parquet")
        cfg = Config({"data": {"cache_dir": str(cache), "start": "2026-01-01"}})

        appels = []
        vrai = datafeed._download_yfinance
        datafeed._download_yfinance = lambda t, s, e: appels.append(t) or {}
        try:
            rapport = {}
            datafeed.fetch(cfg, ["AAA"], rapport=rapport)
        finally:
            datafeed._download_yfinance = vrai
        assert appels == [], "telechargement inutile declenche"
        assert rapport["a_completer"] == 0 and rapport["ok"] is True


    def test_le_11_septembre_le_cache_du_8_doit_declencher_un_telechargement(self, tmp_path):
        """Le test qui pince exactement l'ancien bug.

        3 jours calendaires d'ecart : l'ancienne regle `(today - last).days > 3`
        repondait False et ne telechargeait rien, alors que mercredi et jeudi
        manquaient au cache.
        """
        import pandas as pd
        from quantbot import datafeed
        from quantbot.config import Config

        idx = pd.bdate_range("2026-08-03", "2026-09-08")     # se termine le mardi 8
        df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                           "volume": 10.0}, index=idx)
        cache = tmp_path / "px"; cache.mkdir()
        df.to_parquet(cache / "AAA.parquet")
        cfg = Config({"data": {"cache_dir": str(cache), "start": "2026-01-01"}})

        demandes = []
        vrai = datafeed._download_yfinance
        datafeed._download_yfinance = lambda t, s, e: demandes.append(t) or {}
        try:
            rapport = {}
            datafeed.fetch(cfg, ["AAA"], rapport=rapport, aujourdhui="2026-09-11")
        finally:
            datafeed._download_yfinance = vrai

        assert demandes == [["AAA"]], \
            "cache arrete au mardi 8, on est le vendredi 11 : le telechargement " \
            "doit partir (c'est le bug qui a laisse le robot decider sur des " \
            "cours vieux de trois jours)"
        assert rapport["a_completer"] == 1
