"""Tests des mesures de performance sur des series a resultat calculable a la main."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import metrics
from quantbot.backtest import BacktestResult


def _result(equity: pd.Series, benchmark: pd.Series | None = None) -> BacktestResult:
    ret = equity.pct_change().fillna(0.0)
    zeros = pd.Series(0.0, index=equity.index)
    return BacktestResult(
        equity=equity, returns=ret,
        weights=pd.DataFrame(1.0, index=equity.index, columns=["X"]),
        cash=zeros, turnover=zeros, costs=zeros,
        trades=pd.DataFrame(), benchmark=benchmark,
    )


class TestPerteMaximale:
    def test_valeur_exacte(self):
        idx = pd.bdate_range("2020-01-01", periods=5)
        eq = pd.Series([100.0, 120.0, 60.0, 90.0, 130.0], index=idx)
        mdd, peak, trough, _ = metrics.max_drawdown(eq)
        assert mdd == pytest.approx(-0.5)          # de 120 a 60
        assert peak == idx[1] and trough == idx[2]

    def test_serie_croissante(self):
        eq = pd.Series(np.arange(100.0, 200.0),
                       index=pd.bdate_range("2020-01-01", periods=100))
        assert metrics.max_drawdown(eq)[0] == pytest.approx(0.0)

    def test_recuperation_non_atteinte(self):
        idx = pd.bdate_range("2020-01-01", periods=4)
        eq = pd.Series([100.0, 200.0, 50.0, 60.0], index=idx)
        assert metrics.max_drawdown(eq)[3] == -1   # jamais revenu au sommet


class TestCagr:
    def test_doublement_en_un_an(self):
        idx = pd.date_range("2020-01-01", "2021-01-01", freq="D")
        eq = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
        # 2020 est bissextile : 366 jours / 365,25 -> ecart de 0,3% attendu
        assert metrics.compute(_result(eq))["cagr"] == pytest.approx(1.0, rel=5e-3)

    def test_quadruplement_en_deux_ans(self):
        idx = pd.date_range("2020-01-01", "2022-01-01", freq="D")
        eq = pd.Series(np.geomspace(100, 400, len(idx)), index=idx)
        assert metrics.compute(_result(eq))["cagr"] == pytest.approx(1.0, rel=1e-2)


class TestSharpe:
    def test_rendement_strictement_constant_donne_un_sharpe_nul(self):
        """Volatilite nulle => Sharpe de 0 par convention.

        Sans garde-fou numerique, l'ecart-type vaut ici 1e-16 (bruit
        d'arrondi flottant) et le Sharpe explose a plusieurs milliards.
        """
        idx = pd.bdate_range("2020-01-01", periods=500)
        eq = pd.Series(100 * 1.0001 ** np.arange(500), index=idx)
        res = _result(eq)
        res.returns.iloc[0] = 1e-4        # meme rendement des le premier jour
        assert float(res.returns.std(ddof=1)) < 1e-12
        assert metrics.compute(res)["sharpe"] == 0.0

    def test_premier_jour_a_zero_cree_une_variance_reelle(self):
        """Piege a connaitre : le jour 0 sans position pese dans l'ecart-type.

        Sur un backtest de plusieurs annees l'effet est negligeable, mais sur
        une serie tres courte il fausse le Sharpe. C'est une raison de plus de
        ne pas interpreter un Sharpe calcule sur quelques mois.
        """
        idx = pd.bdate_range("2020-01-01", periods=500)
        eq = pd.Series(100 * 1.0001 ** np.arange(500), index=idx)
        assert metrics.compute(_result(eq))["sharpe"] > 100

    def test_ordre_de_grandeur(self):
        rng = np.random.default_rng(0)
        idx = pd.bdate_range("2000-01-01", periods=6000)
        mu, sigma = 0.10 / 252, 0.20 / np.sqrt(252)
        eq = pd.Series(100 * np.exp(np.cumsum(mu + sigma * rng.standard_normal(6000))), index=idx)
        sharpe = metrics.compute(_result(eq))["sharpe"]
        assert 0.2 < sharpe < 0.9   # theorique ~0,5

    def test_le_taux_sans_risque_reduit_le_sharpe(self):
        rng = np.random.default_rng(1)
        idx = pd.bdate_range("2010-01-01", periods=2000)
        eq = pd.Series(100 * np.exp(np.cumsum(0.0004 + 0.01 * rng.standard_normal(2000))), index=idx)
        res = _result(eq)
        assert metrics.compute(res, 0.05)["sharpe"] < metrics.compute(res, 0.0)["sharpe"]


class TestComparaisonIndice:
    def test_beta_unitaire_si_identique(self):
        rng = np.random.default_rng(2)
        idx = pd.bdate_range("2015-01-01", periods=1500)
        eq = pd.Series(100 * np.exp(np.cumsum(0.0003 + 0.01 * rng.standard_normal(1500))), index=idx)
        stats = metrics.compute(_result(eq, benchmark=eq.copy()))
        assert stats["beta"] == pytest.approx(1.0, abs=1e-6)
        assert stats["tracking_error"] == pytest.approx(0.0, abs=1e-9)
        assert stats["alpha_annual"] == pytest.approx(0.0, abs=1e-9)

    def test_beta_double_si_deux_fois_plus_volatil(self):
        rng = np.random.default_rng(3)
        idx = pd.bdate_range("2015-01-01", periods=2000)
        b_ret = 0.01 * rng.standard_normal(2000)
        bench = pd.Series(100 * np.exp(np.cumsum(b_ret)), index=idx)
        strat = pd.Series(100 * np.exp(np.cumsum(2 * b_ret)), index=idx)
        assert metrics.compute(_result(strat, bench))["beta"] == pytest.approx(2.0, rel=0.05)


class TestFormatage:
    def test_pourcentages_et_valeurs_absentes(self):
        texte = metrics.format_report(
            {"cagr": 0.1234, "sharpe": 1.5, "calmar": float("nan"), "start": "2020-01-01"})
        assert "12.34%" in texte and "1.50" in texte and "n/a" in texte
