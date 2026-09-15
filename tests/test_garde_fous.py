"""Les replis silencieux doivent rester bruyants.

Chacun de ces tests correspond a un bug qui a REELLEMENT produit des resultats
faux et plausibles : un univers de 64 titres presente comme le S&P 500, et une
strategie comparee a un indice de prix alors qu'elle est calculee dividendes
reinvestis. Un resultat faux qui ressemble a un resultat juste est le pire cas
possible ; ces tests existent pour qu'il ne puisse plus se produire en silence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import metrics
from quantbot.backtest import BenchmarkMissingError, run_backtest
from quantbot.universe import US_FALLBACK_TICKERS, UniverseError, get_universe


def _ohlcv(close, index):
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6}, index=index)


class TestUnivers:
    def test_un_univers_trop_court_leve_au_lieu_de_passer(self, base_config):
        cfg = base_config.with_overrides({"universe.source": "static",
                                          "universe.min_tickers": 400})
        with pytest.raises(UniverseError) as err:
            get_universe(cfg)
        assert "min_tickers" in str(err.value)

    def test_un_univers_suffisant_passe(self, base_config):
        cfg = base_config.with_overrides({"universe.source": "static",
                                          "universe.min_tickers": 10})
        assert len(get_universe(cfg)) == len(US_FALLBACK_TICKERS)

    def test_max_tickers_est_un_choix_pas_un_accident(self, base_config):
        """Tronquer volontairement pour un essai rapide ne doit pas lever.

        min_tickers controle ce que la SOURCE a fourni ; max_tickers est un
        bouton actionne en connaissance de cause. Verifier apres troncature
        rendrait tout essai rapide impossible.
        """
        cfg = base_config.with_overrides({"universe.source": "static",
                                          "universe.min_tickers": 10,
                                          "universe.max_tickers": 5})
        assert len(get_universe(cfg)) == 5

    def test_source_inconnue_leve(self, base_config):
        with pytest.raises(ValueError):
            get_universe(base_config.with_overrides({"universe.source": "n_importe_quoi"}))


class TestBenchmark:
    """Le ticker de reference n'est ni investissable, ni facultatif."""

    @staticmethod
    def _prices(bench_name):
        """Huit titres distincts (des cours identiques donneraient un z-score
        indefini et le portefeuille resterait vide), plus une reference qui
        monte plus vite que tous : si elle etait investissable, le score la
        classerait premiere a chaque date."""
        idx = pd.bdate_range("2015-01-01", periods=1200)
        rng = np.random.default_rng(7)
        n = len(idx)
        prices = {}
        for i in range(8):
            bruit = rng.normal(0.0, 0.008, n).cumsum()
            prices["T%d" % i] = _ohlcv(100 * np.exp(0.00010 + 0.00004 * i * np.arange(n) + bruit), idx)
        prices[bench_name] = _ohlcv(
            100 * np.exp(0.0009 * np.arange(n) + rng.normal(0.0, 0.004, n).cumsum()), idx)
        return prices

    def test_un_benchmark_sans_accent_circonflexe_reste_hors_univers(self, base_config):
        """Regression : le passage de ^GSPC a SPY.

        L'exclusion reposait sur "le ticker commence par ^". SPY est un ticker
        ordinaire : sans exclusion explicite, la strategie pouvait detenir
        l'indice qu'elle est censee battre.
        """
        prices = self._prices("SPY")
        cfg = base_config.with_overrides({
            "universe.benchmark": "SPY", "data.min_history_days": 300,
            "portfolio.top_n": 3, "regime.enabled": False})
        res = run_backtest(prices, cfg)
        assert "SPY" not in res.weights.columns
        assert float(res.weights.to_numpy().max()) > 0.0   # le backtest a bien investi

    def test_un_benchmark_absent_leve_au_lieu_de_se_taire(self, base_config):
        prices = self._prices("SPY")
        del prices["SPY"]
        cfg = base_config.with_overrides({"universe.benchmark": "SPY",
                                          "data.min_history_days": 300})
        with pytest.raises(BenchmarkMissingError):
            run_backtest(prices, cfg)

    def test_sans_benchmark_configure_le_backtest_tourne(self, base_config):
        prices = self._prices("SPY")
        del prices["SPY"]
        cfg = base_config.with_overrides({"universe.benchmark": None,
                                          "data.min_history_days": 300,
                                          "regime.enabled": False})
        res = run_backtest(prices, cfg)
        assert res.benchmark is None
        assert metrics.compute(res).get("beta") is None

    def test_le_benchmark_ne_fausse_pas_le_score_composite(self, base_config):
        """Meme resultat que si la reference n'avait jamais ete chargee."""
        cfg_base = {"data.min_history_days": 300, "portfolio.top_n": 3,
                    "regime.enabled": False}
        avec = run_backtest(self._prices("SPY"),
                            base_config.with_overrides(dict(cfg_base, **{"universe.benchmark": "SPY"})))
        sans_prices = self._prices("SPY")
        del sans_prices["SPY"]
        sans = run_backtest(sans_prices,
                            base_config.with_overrides(dict(cfg_base, **{"universe.benchmark": None})))
        pd.testing.assert_series_equal(avec.returns, sans.returns)
