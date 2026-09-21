"""Tests du moteur de backtest sur des scenarios a resultat connu d'avance."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import metrics
from quantbot.backtest import run_backtest
from quantbot.portfolio import cap_weights, rebalance_dates


def _ohlcv(close: np.ndarray, index) -> pd.DataFrame:
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6}, index=index)


@pytest.fixture
def scenario_gagnant_evident():
    """Un titre monte regulierement, les neuf autres stagnent.

    Une strategie momentum doit trouver le gagnant et coller a sa performance.
    """
    idx = pd.bdate_range("2015-01-01", periods=1500)
    prices = {}
    for i in range(10):
        rate = 0.0004 if i == 0 else 0.0
        prices[f"T{i}"] = _ohlcv(100 * np.exp(rate * np.arange(1500)), idx)
    bench = 100 * np.exp(0.00004 * np.arange(1500))
    prices["^BENCH"] = _ohlcv(bench, idx)
    return prices


@pytest.fixture
def config_simple(base_config):
    return base_config.with_overrides({
        "universe.benchmark": "^BENCH",
        "data.min_history_days": 300,
        "portfolio.top_n": 1,
        "portfolio.weighting": "equal",
        "portfolio.max_weight": 1.0,
        "regime.enabled": False,
        "execution.commission_bps": 0.0,
        "execution.slippage_bps": 0.0,
        "execution.risk_free_annual": 0.0,
        "factors.low_volatility.weight": 0.0,
        "factors.trend.weight": 0.0,
        "factors.min_valid_factors": 1,
    })


class TestExactitude:
    def test_trouve_le_gagnant(self, scenario_gagnant_evident, config_simple):
        res = run_backtest(scenario_gagnant_evident, config_simple)
        held = res.weights.iloc[-1]
        assert held["T0"] == pytest.approx(1.0, abs=1e-6)

    def test_performance_egale_a_celle_du_titre_detenu(self, scenario_gagnant_evident, config_simple):
        """Sans frais et pleinement investi sur un seul titre, la valeur du
        portefeuille doit suivre exactement le cours de ce titre."""
        res = run_backtest(scenario_gagnant_evident, config_simple)
        w = res.weights["T0"]
        pleinement_investi = w[w > 0.999]
        debut, fin = pleinement_investi.index[0], pleinement_investi.index[-1]
        px = scenario_gagnant_evident["T0"]["close"]
        attendu = px.loc[fin] / px.loc[debut]
        obtenu = res.equity.loc[fin] / res.equity.loc[debut]
        assert obtenu == pytest.approx(attendu, rel=1e-9)

    def test_capital_initial_respecte(self, scenario_gagnant_evident, config_simple):
        cfg = config_simple.with_overrides({"execution.initial_capital": 50_000.0})
        res = run_backtest(scenario_gagnant_evident, cfg)
        assert res.equity.iloc[0] == pytest.approx(50_000.0)

    def test_poids_toujours_valides(self, momentum_prices, base_config):
        res = run_backtest(momentum_prices, base_config)
        assert (res.weights.to_numpy() >= -1e-12).all(), "aucun poids negatif (pas de vente a decouvert)"
        assert res.net_exposure.max() <= 1.0 + 1e-9, "jamais d'effet de levier"
        assert (res.weights.to_numpy() <= base_config.get("portfolio.max_weight") + 1e-9).all()

    def test_liquidites_et_positions_font_cent_pour_cent(self, momentum_prices, base_config):
        res = run_backtest(momentum_prices, base_config)
        total = res.net_exposure + res.cash
        assert np.allclose(total.to_numpy(), 1.0, atol=1e-9)


class TestFrais:
    def test_les_frais_degradent_la_performance(self, scenario_gagnant_evident, config_simple):
        sans = run_backtest(scenario_gagnant_evident, config_simple)
        avec = run_backtest(scenario_gagnant_evident, config_simple.with_overrides(
            {"execution.commission_bps": 25.0, "execution.slippage_bps": 25.0}))
        assert avec.equity.iloc[-1] < sans.equity.iloc[-1]

    def test_cout_exact_du_premier_rebalancement(self, scenario_gagnant_evident, config_simple):
        """Le premier achat porte sur 100% du capital : rotation = 1,0."""
        cfg = config_simple.with_overrides(
            {"execution.commission_bps": 30.0, "execution.slippage_bps": 20.0})
        res = run_backtest(scenario_gagnant_evident, cfg)
        premier = res.turnover[res.turnover > 0].index[0]
        assert res.turnover.loc[premier] == pytest.approx(1.0, abs=1e-9)
        assert res.costs.loc[premier] == pytest.approx(50.0 / 10_000, rel=1e-9)

    def test_rotation_nulle_si_le_classement_ne_change_pas(self, scenario_gagnant_evident, config_simple):
        res = run_backtest(scenario_gagnant_evident, config_simple)
        rebal = res.turnover[res.turnover > 0]
        # Apres le premier achat, plus rien ne bouge : le gagnant reste le gagnant.
        assert len(rebal) == 1


class TestFiltreDeRegime:
    def test_le_filtre_met_en_liquidites_dans_un_marche_baissier(self, base_config):
        idx = pd.bdate_range("2015-01-01", periods=1500)
        # 4 ans de hausse puis une longue baisse
        path = np.concatenate([np.linspace(0, 0.6, 1000), np.linspace(0.6, -0.4, 500)])
        prices = {f"T{i}": _ohlcv(100 * np.exp(path + i * 0.01), idx) for i in range(12)}
        prices["^BENCH"] = _ohlcv(100 * np.exp(path), idx)
        cfg = base_config.with_overrides({
            "universe.benchmark": "^BENCH", "data.min_history_days": 300,
            "portfolio.top_n": 5, "regime.enabled": True, "regime.ma_window": 200})
        res = run_backtest(prices, cfg)
        assert res.net_exposure.iloc[-1] == pytest.approx(0.0, abs=1e-9)
        assert res.cash.iloc[-1] == pytest.approx(1.0, abs=1e-9)

    def test_le_filtre_amortit_un_vrai_krach(self, base_config):
        """La ou le filtre gagne son salaire : une baisse durable et marquee."""
        idx = pd.bdate_range("2012-01-01", periods=2000)
        path = np.concatenate([
            np.linspace(0.0, 0.55, 900),     # marche haussier
            np.linspace(0.55, -0.20, 400),   # krach etale sur 18 mois
            np.linspace(-0.20, 0.35, 700),   # reprise
        ])
        prices = {f"T{i}": _ohlcv(100 * np.exp(path + np.sin(np.arange(2000) / 90 + i) * 0.04), idx)
                  for i in range(12)}
        prices["^BENCH"] = _ohlcv(100 * np.exp(path), idx)
        cfg = base_config.with_overrides({
            "universe.benchmark": "^BENCH", "data.min_history_days": 300,
            "portfolio.top_n": 5})
        avec = run_backtest(prices, cfg.with_overrides({"regime.enabled": True}))
        sans = run_backtest(prices, cfg.with_overrides({"regime.enabled": False}))
        assert metrics.compute(avec)["max_drawdown"] > metrics.compute(sans)["max_drawdown"]

    def test_effet_de_scie_documente(self, random_walk_prices, base_config):
        """Propriete a connaitre, pas un defaut du code.

        Sur une marche aleatoire sans tendance persistante, le filtre vend
        apres la baisse et rachete apres la hausse : il retire de l'exposition
        SANS ameliorer la perte maximale, et peut meme l'aggraver. Il n'apporte
        quelque chose que si les baisses sont durables, ce que teste le cas
        precedent. On verifie donc seulement ce qui est structurellement vrai :
        le filtre reduit l'exposition moyenne.
        """
        avec = run_backtest(random_walk_prices, base_config.with_overrides({"regime.enabled": True}))
        sans = run_backtest(random_walk_prices, base_config.with_overrides({"regime.enabled": False}))
        assert avec.net_exposure.mean() < sans.net_exposure.mean()


class TestCalendrier:
    def test_un_rebalancement_par_mois(self):
        idx = pd.bdate_range("2020-01-01", "2022-12-31")
        dates = rebalance_dates(idx, "monthly")
        assert len(dates) == 36
        assert (dates.to_series().diff().dt.days.dropna() > 20).all()

    def test_frequences_ordonnees(self):
        idx = pd.bdate_range("2020-01-01", "2024-12-31")
        w = len(rebalance_dates(idx, "weekly"))
        m = len(rebalance_dates(idx, "monthly"))
        q = len(rebalance_dates(idx, "quarterly"))
        assert w > m > q
        assert m == 60 and q == 20

    def test_frequence_inconnue(self):
        with pytest.raises(ValueError):
            rebalance_dates(pd.bdate_range("2020-01-01", periods=10), "daily")


class TestPlafonnement:
    def test_le_plafond_est_respecte(self):
        w = cap_weights(np.array([100.0, 1.0, 1.0, 1.0, 1.0, 1.0]), 0.25)
        assert w.max() <= 0.25 + 1e-12
        assert w.sum() == pytest.approx(1.0)

    def test_reliquat_en_liquidites_si_le_plafond_l_impose(self):
        w = cap_weights(np.ones(10), 0.05)
        assert w.sum() == pytest.approx(0.5)

    def test_poids_negatifs_ecartes(self):
        w = cap_weights(np.array([1.0, -5.0, 1.0]), 1.0)
        assert (w >= 0).all() and w[1] == 0.0


@pytest.fixture(scope="module")
def runs(momentum_prices, base_config):
    """Les backtests dont `TestCoutDeLArgentEmprunte` a besoin, calcules une fois.

    Sept backtests sur l'univers complet valent une quarantaine de secondes :
    les recalculer par test en couterait cinq fois plus pour le meme resultat.
    """
    combinaisons = [(1.0, 0.0), (1.0, 600.0), (1.25, 0.0), (1.25, 400.0),
                    (1.5, 0.0), (1.5, 400.0), (1.5, 600.0)]
    return {c: run_backtest(momentum_prices, _cfg_levier(base_config, *c))
            for c in combinaisons}


def _cfg_levier(base, exposition: float, spread: float):
    """Exposition CONSTANTE : mini = maxi force la valeur quelle que soit la
    volatilite estimee. On mesure le cout du levier, pas le pilotage."""
    return base.with_overrides({
        "regime.enabled": False,
        "portfolio.volatilite.active": True,
        "portfolio.volatilite.exposition_min": exposition,
        "portfolio.volatilite.exposition_max": exposition,
        "execution.financing_spread_bps": spread,
    })


class TestCoutDeLArgentEmprunte:
    """Ce que coute un levier, et pourquoi le chiffre n'etait pas le bon.

    La config affirmait : "Le backtest ne facture aucun interet sur l'argent
    emprunte". C'etait faux dans les deux sens. La ligne `cash * rf_daily`
    facturait bien un interet - mais au taux SANS RISQUE, que personne ne
    pratique. Un courtier de detail prete a ce taux plus 3 a 6 points, et cet
    ecart est precisement ce qui decide si un levier rapporte quelque chose ou
    finance le courtier.

    Ces tests tournent sur l'univers ALEATOIRE, et pas sur
    `scenario_gagnant_evident` : le titre gagnant y monte a taux rigoureusement
    constant, sa volatilite estimee est donc nulle, et le pilotage renvoie 1.0
    sans jamais emprunter. Une premiere version de ces tests s'est cassee
    dessus - et c'etait le test qui avait raison.
    """

    def test_le_levier_emprunte_vraiment(self, runs):
        """Le prealable a tout le reste : sans `cash` negatif, ces tests
        mesureraient l'effet d'un cout jamais preleve."""
        assert runs[(1.5, 0.0)].cash.min() < -0.4
        assert runs[(1.0, 0.0)].cash.min() >= -1e-9

    def test_sans_levier_la_marge_ne_change_RIEN(self, runs):
        """La garantie de non-regression : `exposition_max` vaut 1.00 par
        defaut, donc tous les resultats deja publies restent valables au
        dernier chiffre pres."""
        sans, avec = runs[(1.0, 0.0)], runs[(1.0, 600.0)]
        assert avec.equity.iloc[-1] == pytest.approx(sans.equity.iloc[-1], rel=1e-12)
        assert avec.costs.sum() == pytest.approx(sans.costs.sum(), rel=1e-12)

    def test_avec_levier_la_marge_degrade_la_performance(self, runs):
        assert runs[(1.5, 600.0)].equity.iloc[-1] < runs[(1.5, 0.0)].equity.iloc[-1]

    def test_la_marge_apparait_dans_les_frais(self, runs):
        """Elle est prelevee comme un frais, donc elle se LIT dans le rapport
        au lieu de disparaitre silencieusement dans la performance."""
        assert runs[(1.5, 600.0)].costs.sum() > runs[(1.5, 0.0)].costs.sum()

    def test_le_cout_est_proportionnel_a_la_part_empruntee(self, runs):
        """Emprunter deux fois plus coute deux fois plus. Le test qui distingue
        "un cout est preleve" de "le BON cout est preleve".

        A exposition 1,25 on emprunte 0,25 ; a 1,50 on emprunte 0,50.
        """
        cout_peu = (runs[(1.25, 400.0)].costs - runs[(1.25, 0.0)].costs).sum()
        cout_double = (runs[(1.5, 400.0)].costs - runs[(1.5, 0.0)].costs).sum()
        assert cout_peu > 0
        assert cout_double == pytest.approx(2 * cout_peu, rel=0.05)
