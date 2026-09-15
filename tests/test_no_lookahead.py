"""Le test le plus important du projet.

Une fuite d'information future (look-ahead bias) est l'erreur la plus
frequente et la plus couteuse en trading quantitatif : elle produit des
courbes de performance magnifiques et totalement fausses. Elle se glisse
partout - un `shift` oublie, une moyenne calculee sur toute la periode, un
rebalancement execute au cours du jour du signal.

Trois controles independants ici :

1. TRONCATURE   : couper l'historique ne doit rien changer au passe.
2. FUTUR MODIFIE: alterer les cours futurs ne doit rien changer au passe.
3. BRUIT PUR    : sur des marches aleatoires, la strategie ne doit produire
                  aucun avantage. Une belle performance sur du bruit est la
                  signature d'une fuite.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import metrics
from quantbot.backtest import run_backtest
from quantbot.synthetic import generate_prices


class TestPasDeFuiteTemporelle:
    def test_troncature_de_l_historique(self, momentum_prices, base_config):
        """Un backtest arrete en 2018 doit donner exactement la meme courbe
        que le backtest complet, jusqu'en 2018."""
        complet = run_backtest(momentum_prices, base_config)
        coupe = "2018-06-30"
        tronque = run_backtest(
            {t: df.loc[:coupe] for t, df in momentum_prices.items()}, base_config)

        # On compare jusqu'a 2 mois avant la coupure : le dernier mois tronque
        # cree un rebalancement de fin de periode qui n'existe pas dans la
        # serie complete, ce qui est un artefact de bord, pas une fuite.
        limite = pd.Timestamp("2018-04-30")
        a = complet.equity.loc[:limite]
        b = tronque.equity.loc[:limite]
        assert len(a) == len(b) > 500
        assert np.allclose(a.to_numpy(), b.to_numpy(), rtol=1e-10), \
            "la performance passee depend de donnees futures : il y a une fuite"

    def test_modification_du_futur(self, momentum_prices, base_config):
        """Multiplier par 5 tous les cours a partir de 2019 ne doit rien
        changer a la valeur du portefeuille avant 2019."""
        coupe = pd.Timestamp("2019-01-02")
        altere = {}
        for ticker, df in momentum_prices.items():
            copie = df.copy()
            mask = copie.index >= coupe
            copie.loc[mask, ["open", "high", "low", "close"]] *= 5.0
            altere[ticker] = copie

        ref = run_backtest(momentum_prices, base_config)
        mod = run_backtest(altere, base_config)
        avant = ref.equity.index < coupe
        assert np.allclose(ref.equity[avant].to_numpy(), mod.equity[avant].to_numpy(), rtol=1e-10)

    def test_modification_du_futur_sans_changer_l_echelle(self, momentum_prices, base_config):
        """Sonde complementaire, et plus severe que la precedente.

        Multiplier les cours futurs par une constante ne teste RIEN pour une
        statistique invariante d'echelle - la correlation, la beta, tout ratio
        de cours. On permute donc les rendements futurs titre par titre : les
        distributions marginales survivent, la co-variation disparait. Toute
        statistique croisee qui lirait le futur devient detectable.
        """
        from conftest import futur_decorrele

        coupe = pd.Timestamp("2019-01-02")
        ref = run_backtest(momentum_prices, base_config)
        mod = run_backtest(futur_decorrele(momentum_prices, coupe), base_config)

        avant = ref.equity.index < coupe
        assert np.allclose(ref.equity[avant].to_numpy(),
                           mod.equity[avant].to_numpy(), rtol=1e-10), \
            "la performance passee depend de la structure des rendements futurs"
        assert not np.allclose(ref.equity[~avant].to_numpy(),
                               mod.equity[~avant].to_numpy()), \
            "la perturbation n'a rien change : la sonde est inerte"

    def test_aucun_rendement_le_jour_du_signal(self, momentum_prices, base_config):
        """Verifie la chronologie : entre la date de signal et la date
        d'execution il doit s'ecouler `execution_lag` jours."""
        from quantbot.portfolio import rebalance_dates
        res = run_backtest(momentum_prices, base_config)
        close_index = res.equity.index
        signaux = rebalance_dates(close_index, base_config.get("execution.rebalance"))
        executions = res.turnover[res.turnover > 0].index
        lag = int(base_config.get("execution.execution_lag"))
        for exe in executions[:20]:
            pos_exe = close_index.get_loc(exe)
            assert close_index[pos_exe - lag] in signaux, \
                f"execution du {exe.date()} non alignee sur un signal + {lag} jour(s)"


class TestPasDeMiracleSurDuBruit:
    @pytest.mark.parametrize("seed", [1, 7, 21])
    def test_bruit_pur_ne_bat_pas_l_indice(self, seed, base_config):
        """Sur des marches aleatoires il n'y a rien a trouver.

        Apres frais, la strategie doit meme faire MOINS BIEN que l'achat-
        conservation, puisqu'elle paie de la rotation pour rien. Un ratio
        d'information nettement positif signalerait une fuite.
        """
        prices = generate_prices(n_tickers=50, start="2006-01-01", end="2022-12-31",
                                 seed=seed, momentum_strength=0.0)
        res = run_backtest(prices, base_config)
        stats = metrics.compute(res)
        assert stats["information_ratio"] < 0.5, (
            f"ratio d'information de {stats['information_ratio']:.2f} sur du bruit pur : "
            "signature probable d'une fuite d'information future")

    def test_signal_reel_detecte(self, momentum_prices, base_config):
        """Controle inverse : quand un signal EXISTE, la chaine doit le voir.

        Sans ce test, une strategie cassee qui ne trouve jamais rien passerait
        le test precedent haut la main.
        """
        res = run_backtest(momentum_prices, base_config)
        stats = metrics.compute(res)
        assert stats["information_ratio"] > 0.3, (
            "un momentum implante n'est pas detecte : la chaine de signal est cassee")


class TestRobustesseDesDonnees:
    def test_trous_dans_les_cours(self, base_config):
        """Des jours manquants ne doivent ni planter ni fausser le resultat."""
        prices = generate_prices(n_tickers=20, start="2010-01-01", end="2020-12-31", seed=5)
        rng = np.random.default_rng(0)
        troue = {}
        for ticker, df in prices.items():
            copie = df.copy()
            if not ticker.startswith("^"):
                drop = rng.choice(len(copie), size=len(copie) // 50, replace=False)
                copie.iloc[drop, copie.columns.get_indexer(["close"])] = np.nan
            troue[ticker] = copie
        res = run_backtest(troue, base_config)
        assert np.isfinite(res.equity.to_numpy()).all()
        assert res.equity.iloc[-1] > 0

    def test_titre_arrivant_en_cours_de_route(self, base_config):
        """Une introduction en bourse recente ne doit pas etre selectionnee
        avant d'avoir assez d'historique."""
        prices = generate_prices(n_tickers=20, start="2010-01-01", end="2020-12-31", seed=6)
        nouveau = prices["SYN000"].copy()
        nouveau.loc[nouveau.index < "2018-01-01"] = np.nan
        prices["NOUVEAU"] = nouveau
        res = run_backtest(prices, base_config)
        avant = res.weights.loc[res.weights.index < "2018-01-01", "NOUVEAU"]
        assert (avant.abs() < 1e-12).all(), "un titre sans historique a ete selectionne"
