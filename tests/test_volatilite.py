"""Pilotage de la volatilite.

Ordre d'importance, comme pour la contrainte de correlation :
1. le DEFAUT ne change rien ;
2. le pilotage agit vraiment, et dans le bon sens ;
3. il ne lit aucune donnee future ;
4. il s'abstient quand il ne sait pas, au lieu de piloter au hasard ;
5. il n'emprunte jamais sans qu'on le lui demande explicitement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.backtest import run_backtest
from quantbot.portfolio import rendements_correlation, target_weights
from quantbot.volatilite import (EXPOSITION_MAX, exposition, parametres,
                                 volatilite_ex_ante)


def _serie(vol_annuelle, n=400, seed=0, cols=("A", "B", "C", "D")):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    sig = vol_annuelle / np.sqrt(252.0)
    return pd.DataFrame(rng.normal(0.0, sig, (n, len(cols))), index=idx,
                        columns=list(cols))


class TestVolatiliteExAnte:
    def test_retrouve_la_volatilite_implantee(self):
        """Titres independants, poids egaux : la vol du panier vaut celle d'un
        titre divisee par racine(n)."""
        r = _serie(0.20, n=1500, seed=1)
        poids = pd.Series(0.25, index=["A", "B", "C", "D"])
        v = volatilite_ex_ante(r, poids, fenetre=1500)
        assert v == pytest.approx(0.20 / 2.0, rel=0.12), v

    def test_des_titres_correles_donnent_une_vol_plus_haute(self):
        n = 800
        rng = np.random.default_rng(4)
        idx = pd.bdate_range("2024-01-01", periods=n)
        commun = rng.normal(0, 0.012, n)
        correle = pd.DataFrame(
            {c: commun + rng.normal(0, 0.002, n) for c in "ABCD"}, index=idx)
        independant = pd.DataFrame(
            {c: rng.normal(0, 0.0122, n) for c in "ABCD"}, index=idx)
        poids = pd.Series(0.25, index=list("ABCD"))
        assert (volatilite_ex_ante(correle, poids, fenetre=n)
                > 1.5 * volatilite_ex_ante(independant, poids, fenetre=n))

    def test_s_abstient_quand_l_estimation_n_est_pas_fiable(self):
        r = _serie(0.2, n=20)
        poids = pd.Series(0.5, index=["A", "B"])
        assert volatilite_ex_ante(r, poids, fenetre=20, min_obs=40) is None
        assert volatilite_ex_ante(r, pd.Series(1.0, index=["A"])) is None
        assert volatilite_ex_ante(r, pd.Series(dtype="float64")) is None
        assert volatilite_ex_ante(r, pd.Series(1.0, index=["INCONNU"])) is None

    def test_le_levier_des_poids_n_influe_pas_sur_la_mesure(self):
        """On mesure le risque du PANIER ; multiplier tous les poids par deux
        ne change pas sa composition, donc pas sa volatilite unitaire."""
        r = _serie(0.2, n=500, seed=3)
        a = volatilite_ex_ante(r, pd.Series(0.25, index=list("ABCD")), fenetre=500)
        b = volatilite_ex_ante(r, pd.Series(0.50, index=list("ABCD")), fenetre=500)
        assert a == pytest.approx(b)


class TestExposition:
    def test_reduit_quand_c_est_agite_augmente_quand_c_est_calme(self):
        assert exposition(0.24, 0.12) == pytest.approx(0.5)
        assert exposition(0.06, 0.12, maxi=2.0) == pytest.approx(2.0)

    def test_bornee(self):
        assert exposition(0.01, 0.12) == EXPOSITION_MAX        # jamais de levier
        assert exposition(10.0, 0.12, mini=0.10) == 0.10

    def test_sans_estimation_on_ne_pilote_pas(self):
        """Ne rien faire est le seul comportement defendable quand on ne sait
        pas. Piloter sur une volatilite inventee serait pire."""
        for mauvais in (None, 0.0, -1.0, float("nan"), float("inf")):
            assert exposition(mauvais, 0.12) == 1.0


class TestIntegration:
    def test_desactive_par_defaut(self, base_config):
        assert base_config.get("portfolio.volatilite.active") is False
        assert parametres(base_config) is None

    def test_le_defaut_ne_change_rien(self, momentum_prices, base_config):
        a = run_backtest(momentum_prices, base_config)
        b = run_backtest(momentum_prices, base_config)
        pd.testing.assert_series_equal(a.equity, b.equity)
        assert a.meta["volatilite"] is None

    def test_le_pilotage_rapproche_la_volatilite_de_la_cible(
            self, momentum_prices, base_config):
        """Le test qui compte : la volatilite realisee doit se rapprocher de
        la cible, sinon le module ne fait pas ce qu'il annonce."""
        cible = 0.10
        cfg = base_config.copy()
        cfg.set("portfolio.volatilite.active", True, strict=True)
        cfg.set("portfolio.volatilite.cible", cible, strict=True)

        nu = run_backtest(momentum_prices, base_config)
        pilote = run_backtest(momentum_prices, cfg)
        v_nu = float(nu.returns.std(ddof=1) * np.sqrt(252))
        v_pilote = float(pilote.returns.std(ddof=1) * np.sqrt(252))
        assert abs(v_pilote - cible) < abs(v_nu - cible), (v_nu, v_pilote)

    def test_l_exposition_ne_depasse_jamais_le_plafond(
            self, momentum_prices, base_config):
        """Depasser 1.0 signifie emprunter, et le backtest ne facture aucun
        interet : ca ne doit jamais arriver sans demande explicite."""
        cfg = base_config.copy()
        cfg.set("portfolio.volatilite.active", True, strict=True)
        cfg.set("portfolio.volatilite.cible", 0.60, strict=True)   # cible absurde
        res = run_backtest(momentum_prices, cfg)
        assert float(res.weights.sum(axis=1).max()) <= 1.0 + 1e-9

    def test_aucune_donnee_future(self, momentum_prices, base_config):
        """Meme sonde que pour les correlations : on permute les rendements
        futurs titre par titre, ce qui detruit la covariance sans toucher aux
        distributions. Multiplier les cours par une constante ne prouverait
        rien - la covariance normalisee y est insensible."""
        from conftest import futur_decorrele

        cfg = base_config.copy()
        cfg.set("portfolio.volatilite.active", True, strict=True)

        coupe = pd.Timestamp("2018-01-01")
        a = run_backtest(momentum_prices, cfg)
        b = run_backtest(futur_decorrele(momentum_prices, coupe), cfg)

        avant = a.equity.index < coupe
        assert avant.sum() > 400
        pd.testing.assert_series_equal(a.equity[avant], b.equity[avant])
        assert not np.allclose(a.equity[~avant].to_numpy(),
                               b.equity[~avant].to_numpy()), \
            "la perturbation du futur n'a rien change : la sonde est inerte"

    def test_les_liquidites_absorbent_la_reduction(self, momentum_prices, base_config):
        """Reduire l'exposition laisse de la tresorerie, elle ne disparait pas."""
        cfg = base_config.copy()
        cfg.set("portfolio.volatilite.active", True, strict=True)
        cfg.set("portfolio.volatilite.cible", 0.05, strict=True)
        res = run_backtest(momentum_prices, cfg)
        investi = res.weights.sum(axis=1)
        assert float(investi.mean()) < 0.9
        assert np.allclose((investi + res.cash).to_numpy(), 1.0, atol=1e-6)
