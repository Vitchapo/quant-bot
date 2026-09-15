"""Tests des facteurs : exactitude du calcul et causalite."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import factors as F


def _frame(values, n_cols=1, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({f"T{i}": values for i in range(n_cols)}, index=idx)


class TestMomentum:
    def test_valeur_exacte(self):
        # Cours qui double a intervalle regulier : le calcul doit etre exact.
        close = _frame(np.arange(1.0, 401.0))
        mom = F.momentum(close, lookback=100, skip=10)
        # a t=200 : cours[190]/cours[100] - 1 = 191/101 - 1
        assert mom["T0"].iloc[200] == pytest.approx(191 / 101 - 1)

    def test_periode_de_chauffe_vide(self):
        close = _frame(np.arange(1.0, 401.0))
        mom = F.momentum(close, lookback=252, skip=21)
        assert mom["T0"].iloc[:252].isna().all()
        assert not np.isnan(mom["T0"].iloc[252])

    def test_lookback_doit_depasser_skip(self):
        with pytest.raises(ValueError):
            F.momentum(_frame(np.arange(1.0, 100.0)), lookback=10, skip=10)

    def test_causalite(self):
        """Modifier le futur ne doit pas changer le passe."""
        close = _frame(np.linspace(100, 300, 500), n_cols=3)
        ref = F.momentum(close, 252, 21)
        altered = close.copy()
        altered.iloc[400:] *= 3.0
        new = F.momentum(altered, 252, 21)
        assert np.allclose(ref.iloc[:400], new.iloc[:400], equal_nan=True)


class TestVolatilite:
    def test_serie_constante_donne_zero(self):
        close = _frame(np.full(300, 50.0))
        assert F.realized_volatility(close, 63)["T0"].iloc[-1] == pytest.approx(0.0)

    def test_ordre_de_grandeur(self):
        rng = np.random.default_rng(0)
        daily = 0.20 / np.sqrt(252)
        close = _frame(100 * np.exp(np.cumsum(rng.standard_normal(2000) * daily)))
        vol = F.realized_volatility(close, 252)["T0"].dropna()
        assert 0.15 < vol.mean() < 0.25    # on vise 20%


class TestZScore:
    def test_moyenne_nulle_ecart_type_unitaire(self):
        rng = np.random.default_rng(1)
        df = pd.DataFrame(rng.standard_normal((50, 30)),
                          index=pd.bdate_range("2020-01-01", periods=50))
        z = F.cross_sectional_zscore(df, winsorize=0)
        assert np.allclose(z.mean(axis=1), 0, atol=1e-10)
        assert np.allclose(z.std(axis=1, ddof=0), 1, atol=1e-10)

    def test_winsorisation_borne_les_valeurs(self):
        df = pd.DataFrame([[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 500.0]],
                          index=pd.bdate_range("2020-01-01", periods=1))
        z = F.cross_sectional_zscore(df, winsorize=3.0)
        assert z.to_numpy().max() == pytest.approx(3.0)

    def test_normalisation_par_ligne_pas_par_colonne(self):
        """Ajouter une constante a une DATE ne doit rien changer au classement."""
        rng = np.random.default_rng(2)
        df = pd.DataFrame(rng.standard_normal((20, 20)),
                          index=pd.bdate_range("2020-01-01", periods=20))
        z1 = F.cross_sectional_zscore(df, winsorize=0)
        shifted = df.copy()
        shifted.iloc[5] += 1000.0
        z2 = F.cross_sectional_zscore(shifted, winsorize=0)
        assert np.allclose(z1, z2)

    def test_date_trop_pauvre_est_ecartee(self):
        df = pd.DataFrame([[1.0, 2.0, np.nan, np.nan, np.nan]],
                          index=pd.bdate_range("2020-01-01", periods=1))
        assert F.cross_sectional_zscore(df, min_names=5).isna().all().all()


class TestRegime:
    def test_signal_haussier_et_baissier(self):
        idx = pd.bdate_range("2020-01-01", periods=500)
        up = pd.Series(np.linspace(100, 200, 500), index=idx)
        assert F.regime_filter(up, 200).dropna().all()
        down = pd.Series(np.linspace(200, 100, 500), index=idx)
        assert not F.regime_filter(down, 200).dropna().any()


class TestScoreComposite:
    def test_poids_de_la_volatilite_a_le_bon_signe(self, base_config):
        """A tendance egale, les titres les moins volatils doivent mieux scorer."""
        rng = np.random.default_rng(3)
        idx = pd.bdate_range("2018-01-01", periods=600)
        drift = np.linspace(0, 0.5, 600)
        data = {}
        for i in range(5):
            data[f"CALME{i}"] = 100 * np.exp(drift + rng.standard_normal(600) * 0.003)
        for i in range(5):
            data[f"AGITE{i}"] = 100 * np.exp(drift + rng.standard_normal(600) * 0.030)
        close = pd.DataFrame(data, index=idx)

        cfg = base_config.with_overrides({
            "factors.momentum.weight": 0.0,
            "factors.trend.weight": 0.0,
            "factors.low_volatility.weight": 1.0,
            "factors.min_valid_factors": 1,
        })
        score, _ = F.composite_score(close, cfg)
        last = score.dropna().iloc[-1]
        calmes = last[[c for c in last.index if c.startswith("CALME")]]
        agites = last[[c for c in last.index if c.startswith("AGITE")]]
        assert calmes.min() > agites.max(), "la faible volatilite doit etre recompensee"

    def test_score_ne_regarde_jamais_le_futur(self, base_config):
        """Tronquer l'historique ne doit pas changer les scores passes."""
        rng = np.random.default_rng(4)
        idx = pd.bdate_range("2015-01-01", periods=1200)
        close = pd.DataFrame(
            100 * np.exp(np.cumsum(rng.standard_normal((1200, 12)) * 0.01, axis=0)),
            index=idx, columns=[f"T{i}" for i in range(12)])

        complet, _ = F.composite_score(close, base_config)
        tronque, _ = F.composite_score(close.iloc[:900], base_config)
        assert np.allclose(complet.iloc[:900].to_numpy(),
                           tronque.to_numpy(), equal_nan=True)
