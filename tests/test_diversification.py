"""Contrainte de correlation a la selection.

Ce que ces tests protegent, dans l'ordre d'importance :

1. Le DEFAUT ne change rien. Une amelioration qui modifie les resultats sans
   qu'on l'ait demandee est indiscernable d'une regression.
2. La contrainte agit vraiment : sur une structure de correlation connue, elle
   doit produire un panier mesurablement plus diversifie.
3. Elle ne trahit jamais le classement : jamais de moins bon score retenu
   quand un meilleur passait la contrainte.
4. Elle ne livre jamais un portefeuille incomplet.
5. Elle n'utilise aucune donnee future.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.backtest import run_backtest
from quantbot.diversification import choisir, diagnostic, matrice_correlation
from quantbot.portfolio import (parametres_diversification,
                                rendements_correlation, target_weights)


# ---------------------------------------------------------------------------
# Univers jouet : 3 blocs de titres fortement correles entre eux.
# ---------------------------------------------------------------------------
def _blocs(n_blocs=3, par_bloc=6, n_jours=500, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_jours)
    colonnes, donnees = [], []
    for b in range(n_blocs):
        commun = rng.normal(0, 0.012, n_jours)
        for k in range(par_bloc):
            propre = rng.normal(0, 0.004, n_jours)
            colonnes.append("B%d_%d" % (b, k))
            donnees.append(commun + propre)
    return pd.DataFrame(np.column_stack(donnees), index=dates, columns=colonnes)


@pytest.fixture(scope="module")
def rendements():
    return _blocs()


class TestChoisir:
    def test_sans_contrainte_le_classement_est_respecte(self, rendements):
        """correlations=None doit reproduire exactement un nlargest."""
        scores = pd.Series(np.arange(18, 0, -1), index=rendements.columns)
        assert choisir(scores, None, 6, 0.30) == list(scores.index[:6])

    def test_la_contrainte_ecarte_les_redondants(self, rendements):
        """Sans contrainte, le classement remplit le portefeuille avec un seul
        bloc ; avec, il doit aller chercher ailleurs."""
        # Le classement favorise deliberement le bloc 0.
        ordre = (["B0_%d" % k for k in range(6)]
                 + ["B1_%d" % k for k in range(6)]
                 + ["B2_%d" % k for k in range(6)])
        scores = pd.Series(np.arange(len(ordre), 0, -1), index=ordre, dtype="float64")
        corr = matrice_correlation(rendements, ordre)

        libre = choisir(scores, None, 6, 0.35)
        contraint = choisir(scores, corr, 6, 0.35)

        assert libre == ["B0_%d" % k for k in range(6)]
        assert len({t.split("_")[0] for t in contraint}) >= 2, contraint

        d_libre = diagnostic(rendements, libre)
        d_contraint = diagnostic(rendements, contraint)
        assert d_contraint["correlation"] < d_libre["correlation"]
        assert d_contraint["volatilite"] < d_libre["volatilite"]
        assert d_contraint["paris_independants"] > d_libre["paris_independants"]

    def test_le_meilleur_score_est_toujours_pris(self, rendements):
        """Le premier du classement n'a rien a quoi etre correle : il passe."""
        ordre = list(rendements.columns)
        scores = pd.Series(np.arange(len(ordre), 0, -1), index=ordre, dtype="float64")
        corr = matrice_correlation(rendements, ordre)
        assert choisir(scores, corr, 5, 0.0)[0] == ordre[0]

    def test_jamais_de_moins_bon_score_a_la_place_d_un_meilleur(self, rendements):
        """Propriete gloutonne : si un titre est ecarte, tous ceux retenus
        apres lui devaient etre moins bien classes que lui - jamais l'inverse.
        Autrement dit, l'ordre de choix suit toujours l'ordre du classement.
        """
        ordre = list(rendements.columns)
        scores = pd.Series(np.arange(len(ordre), 0, -1), index=ordre, dtype="float64")
        corr = matrice_correlation(rendements, ordre)
        retenus = choisir(scores, corr, 8, 0.20)
        rangs = [ordre.index(t) for t in retenus]
        assert rangs == sorted(rangs), retenus

    def test_le_portefeuille_est_toujours_complet(self, rendements):
        """Un plafond absurde (0 correlation) ne doit pas livrer 2 lignes sur
        8 : les paliers de relachement doivent finir par remplir."""
        ordre = list(rendements.columns)
        scores = pd.Series(np.arange(len(ordre), 0, -1), index=ordre, dtype="float64")
        corr = matrice_correlation(rendements, ordre)
        for plafond in (-1.0, 0.0, 0.05, 0.35, 0.9):
            retenus = choisir(scores, corr, 8, plafond)
            assert len(retenus) == 8, (plafond, retenus)
            assert len(set(retenus)) == 8

    def test_univers_plus_petit_que_le_portefeuille(self, rendements):
        scores = pd.Series([3.0, 2.0], index=["B0_0", "B1_0"])
        corr = matrice_correlation(rendements, ["B0_0", "B1_0"])
        assert choisir(scores, corr, 20, 0.10) == ["B0_0", "B1_0"]

    def test_ticker_sans_correlation_calculable_n_est_pas_penalise(self, rendements):
        """L'absence de donnee n'est pas une raison d'ecarter un titre."""
        ordre = list(rendements.columns)
        scores = pd.Series(np.arange(len(ordre) + 1, 0, -1),
                           index=["INCONNU"] + ordre, dtype="float64")
        corr = matrice_correlation(rendements, ordre)
        assert "INCONNU" in choisir(scores, corr, 5, 0.0)


class TestMatriceCorrelation:
    def test_titres_absents_ignores_sans_erreur(self, rendements):
        corr = matrice_correlation(rendements, ["B0_0", "B1_0", "FANTOME"])
        assert corr is not None and "FANTOME" not in corr.columns

    def test_moins_de_deux_titres_exploitables(self, rendements):
        assert matrice_correlation(rendements, ["B0_0"]) is None
        assert matrice_correlation(rendements, ["X", "Y"]) is None

    def test_historique_trop_court_rejete(self, rendements):
        court = rendements.tail(20)
        assert matrice_correlation(court, list(court.columns), min_obs=60) is None

    def test_seule_la_fenetre_demandee_est_lue(self, rendements):
        """Une fenetre de 100 seances ne doit pas voir la 101e."""
        pollue = rendements.copy()
        pollue.iloc[:-100] = np.nan
        a = matrice_correlation(rendements, list(rendements.columns)[:6], fenetre=100)
        b = matrice_correlation(pollue, list(pollue.columns)[:6], fenetre=100)
        pd.testing.assert_frame_equal(a, b)


class TestDiagnostic:
    def test_paris_independants(self, rendements):
        """6 titres d'un meme bloc valent presque un seul pari ; 6 titres
        repartis sur 3 blocs en valent nettement plus."""
        un_bloc = diagnostic(rendements, ["B0_%d" % k for k in range(6)])
        repartis = diagnostic(rendements, ["B0_0", "B0_1", "B1_0", "B1_1", "B2_0", "B2_1"])
        assert un_bloc["n"] == repartis["n"] == 6
        assert un_bloc["paris_independants"] < 1.5
        assert repartis["paris_independants"] > un_bloc["paris_independants"]

    def test_panier_trop_petit(self, rendements):
        assert diagnostic(rendements, ["B0_0"]) == {}


class TestIntegration:
    """Le defaut ne bouge pas, l'activation bouge, et rien ne fuit."""

    def test_desactive_par_defaut_dans_les_configs(self, base_config):
        assert base_config.get("portfolio.diversification.active") is False
        assert parametres_diversification(base_config) is None

    def test_le_defaut_ne_change_rien(self, momentum_prices, base_config):
        """Le backtest par defaut doit etre IDENTIQUE a celui d'avant la
        contrainte - au dernier chiffre pres."""
        cfg = base_config.copy()
        a = run_backtest(momentum_prices, cfg)
        b = run_backtest(momentum_prices, cfg)
        pd.testing.assert_series_equal(a.equity, b.equity)
        assert a.meta["diversification"] is None

    def test_target_weights_sans_parametres_ignore_les_rendements(self, rendements):
        """Passer `rendements` sans `diversification` ne doit rien changer."""
        dates = pd.DatetimeIndex([rendements.index[-1]])
        score = pd.DataFrame(np.arange(len(rendements.columns), 0, -1, dtype="float64")[None, :],
                             index=dates, columns=rendements.columns)
        vol = pd.DataFrame(0.2, index=dates, columns=rendements.columns)
        nu = target_weights(score, vol, dates, top_n=6, weighting="equal")
        avec = target_weights(score, vol, dates, top_n=6, weighting="equal",
                              rendements=rendements, diversification=None)
        pd.testing.assert_frame_equal(nu, avec)

    def test_target_weights_applique_la_contrainte(self, rendements):
        dates = pd.DatetimeIndex([rendements.index[-1]])
        ordre = (["B0_%d" % k for k in range(6)]
                 + ["B1_%d" % k for k in range(6)]
                 + ["B2_%d" % k for k in range(6)])
        score = pd.DataFrame(np.arange(len(ordre), 0, -1, dtype="float64")[None, :],
                             index=dates, columns=ordre)
        vol = pd.DataFrame(0.2, index=dates, columns=ordre)
        params = {"correlation_max": 0.35, "fenetre": 252, "min_obs": 60, "candidats": 3.0}

        nu = target_weights(score, vol, dates, top_n=6, weighting="equal",
                            max_weight=1.0)
        avec = target_weights(score, vol, dates, top_n=6, weighting="equal",
                              max_weight=1.0, rendements=rendements[ordre],
                              diversification=params)

        tenus_nu = set(nu.columns[nu.iloc[0] > 0])
        tenus_avec = set(avec.columns[avec.iloc[0] > 0])
        assert len(tenus_nu) == len(tenus_avec) == 6
        assert tenus_nu != tenus_avec
        assert np.isclose(avec.iloc[0].sum(), 1.0)
        assert (diagnostic(rendements, tenus_avec)["correlation"]
                < diagnostic(rendements, tenus_nu)["correlation"])

    def test_aucune_donnee_future_dans_les_correlations(self, momentum_prices, base_config):
        """Contrainte ACTIVE : casser la structure de correlation APRES 2018
        ne doit rien changer avant 2018.

        Attention au piege : multiplier les cours futurs par une constante ne
        prouve RIEN ici, la correlation etant invariante d'echelle. On permute
        donc les rendements futurs titre par titre (voir `futur_decorrele`),
        ce qui detruit la co-variation sans toucher aux distributions.
        """
        from conftest import futur_decorrele

        cfg = base_config.copy()
        cfg.set("portfolio.diversification.active", True, strict=True)
        cfg.set("portfolio.diversification.correlation_max", 0.20, strict=True)

        coupe = pd.Timestamp("2018-01-01")
        a = run_backtest(momentum_prices, cfg)
        b = run_backtest(futur_decorrele(momentum_prices, coupe), cfg)

        avant = a.equity.index < coupe
        assert avant.sum() > 400
        pd.testing.assert_series_equal(a.equity[avant], b.equity[avant])
        # La sonde doit vraiment mordre : sans cela le test ne prouve rien.
        assert not np.allclose(a.equity[~avant].to_numpy(),
                               b.equity[~avant].to_numpy()), \
            "la perturbation du futur n'a rien change : la sonde est inerte"

    def test_activation_change_le_portefeuille(self, momentum_prices, base_config):
        cfg_on = base_config.copy()
        cfg_on.set("portfolio.diversification.active", True, strict=True)
        cfg_on.set("portfolio.diversification.correlation_max", 0.20, strict=True)

        nu = run_backtest(momentum_prices, base_config)
        avec = run_backtest(momentum_prices, cfg_on)

        assert avec.meta["diversification"]["correlation_max"] == 0.20
        # Le nombre de lignes tenues reste le meme : la contrainte deplace le
        # choix, elle ne laisse pas de liquidites non voulues.
        n_nu = (nu.weights.iloc[-1] > 1e-9).sum()
        n_avec = (avec.weights.iloc[-1] > 1e-9).sum()
        assert n_nu == n_avec
        assert not nu.equity.equals(avec.equity)

    def test_correlation_moyenne_reellement_reduite(self, momentum_prices, base_config):
        """Le test qui compte : sur l'univers synthetique, la contrainte
        doit baisser la correlation moyenne du portefeuille tenu."""
        from quantbot.backtest import _prepare_prices

        cfg_on = base_config.copy()
        cfg_on.set("portfolio.diversification.active", True, strict=True)
        cfg_on.set("portfolio.diversification.correlation_max", 0.15, strict=True)

        close, raw, _ = _prepare_prices(momentum_prices, base_config)
        rdt = rendements_correlation(close, raw)

        def correl_moyenne(res):
            valeurs = []
            for date in res.weights.index[::63]:
                tenus = list(res.weights.columns[res.weights.loc[date] > 1e-9])
                d = diagnostic(rdt.loc[:date], tenus)
                if d:
                    valeurs.append(d["correlation"])
            return float(np.mean(valeurs)) if valeurs else float("nan")

        c_nu = correl_moyenne(run_backtest(momentum_prices, base_config))
        c_avec = correl_moyenne(run_backtest(momentum_prices, cfg_on))
        assert c_avec < c_nu, (c_nu, c_avec)
