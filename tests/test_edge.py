"""Le module de mesure d'edge doit dire "non" quand il n'y a rien.

Le piege classique d'un outil de validation, c'est qu'il valide. On verifie
donc les deux sens : sur un univers ou un signal a ete IMPLANTE, les mesures
doivent le detecter ; sur des marches aleatoires pures, elles doivent
conclure a l'absence de signal. Un test qui ne verifie que le premier sens ne
prouve rien.
"""
from __future__ import annotations

import numpy as np
import pytest

from quantbot import edge


@pytest.fixture(scope="module")
def cfg_syn(base_config):
    return base_config.with_overrides({
        "universe.benchmark": "^SYN", "data.min_history_days": 260,
        "portfolio.top_n": 10, "regime.enabled": False,
        "factors.low_volatility.weight": 0.0, "factors.trend.weight": 0.0,
        "factors.min_valid_factors": 1})


def _ligne(df, contient):
    sub = df[df["facteur"].str.contains(contient, case=False)]
    assert len(sub) == 1, "facteur %r introuvable dans %s" % (contient, list(df["facteur"]))
    return sub.iloc[0]


class TestInformationCoefficient:
    def test_detecte_un_signal_implante(self, momentum_prices, cfg_syn):
        ic = edge.information_coefficient(momentum_prices, cfg_syn)
        mom = _ligne(ic, "momentum")
        assert mom["ic_moyen"] > 0.02
        assert mom["t_stat"] > 2.0, "un momentum implante doit ressortir"

    def test_ne_detecte_rien_sur_des_marches_aleatoires(self, random_walk_prices, cfg_syn):
        ic = edge.information_coefficient(random_walk_prices, cfg_syn)
        mom = _ligne(ic, "momentum")
        assert abs(mom["ic_moyen"]) < 0.05
        assert abs(mom["t_stat"]) < 2.5, "aucun signal ne doit apparaitre sur du bruit"

    def test_la_chronologie_exclut_le_jour_d_execution(self, momentum_prices, cfg_syn):
        """Le rendement mesure commence APRES l'execution, jamais avant.

        Avec un delai de 5 jours, l'IC doit changer : s'il ne bougeait pas,
        c'est que la fenetre de mesure ignore le delai - donc qu'elle
        attribuerait a la strategie des rendements survenus avant qu'elle
        n'ait pu acheter.
        """
        a = _ligne(edge.information_coefficient(momentum_prices, cfg_syn), "momentum")
        b = _ligne(edge.information_coefficient(
            momentum_prices, cfg_syn.with_overrides({"execution.execution_lag": 5})), "momentum")
        assert a["ic_moyen"] != pytest.approx(b["ic_moyen"], abs=1e-9)

    def test_le_composite_est_toujours_calcule(self, random_walk_prices, cfg_syn):
        ic = edge.information_coefficient(random_walk_prices, cfg_syn)
        assert "COMPOSITE" in list(ic["facteur"])
        assert (ic["n_dates"] > 20).all()


class TestNullAleatoire:
    def test_le_hasard_encadre_une_strategie_sans_signal(self, random_walk_prices, cfg_syn):
        out = edge.random_null(random_walk_prices, cfg_syn, n_draws=25, seed=7)
        assert out["n_draws"] == 25
        centile = out["distribution"]["sharpe"]["centile_reel"]
        assert 0.0 <= centile <= 100.0
        # La distribution doit avoir de la dispersion, sinon la comparaison ne
        # veut rien dire (ce serait le symptome d'un score fige).
        assert out["distribution"]["sharpe"]["ecart_type"] > 1e-6

    def test_la_persistance_est_reproduite(self, momentum_prices, cfg_syn):
        """Sans ce controle, le bruit tournerait bien plus vite que la vraie
        strategie et paierait des frais qu'elle ne paie pas."""
        out = edge.random_null(momentum_prices, cfg_syn, n_draws=15, seed=3)
        assert 0.0 < out["rho"] < 1.0
        reelle = out["reel"]["annual_turnover"]
        hasard = out["distribution"]["annual_turnover"]["moyenne"]
        assert hasard == pytest.approx(reelle, rel=0.6)

    def test_le_score_reel_est_restaure_apres_les_tirages(self, random_walk_prices, cfg_syn):
        import quantbot.factors as F
        avant = F.composite_score
        edge.random_null(random_walk_prices, cfg_syn, n_draws=3, seed=1)
        assert F.composite_score is avant, "le monkeypatch doit etre defait"


class TestDecomposition:
    def test_les_ecarts_chainent(self, momentum_prices, cfg_syn):
        dec = edge.decomposition(momentum_prices, cfg_syn)
        assert len(dec) >= 3
        for i in range(1, len(dec)):
            attendu = dec["cagr"].iloc[i] - dec["cagr"].iloc[i - 1]
            assert dec["delta_cagr"].iloc[i] == pytest.approx(attendu, abs=1e-12)

    def test_la_premiere_ligne_est_l_indice(self, momentum_prices, cfg_syn):
        dec = edge.decomposition(momentum_prices, cfg_syn)
        assert dec["etape"].iloc[0] == "indice de reference"
        assert np.isnan(dec["delta_cagr"].iloc[0])

    def test_l_univers_entier_est_bien_l_univers_entier(self, momentum_prices, cfg_syn):
        """La ligne "univers entier" ne doit rien selectionner du tout."""
        from quantbot.backtest import run_backtest
        res = run_backtest(momentum_prices, cfg_syn.with_overrides({
            "regime.enabled": False, "portfolio.top_n": 10_000,
            "portfolio.weighting": "equal", "portfolio.max_weight": 1.0}))
        derniers = res.weights.iloc[-1]
        detenus = int((derniers > 1e-9).sum())
        assert detenus > 50, "on doit detenir tout l'univers, pas une selection"


class TestDeciles:
    """Un score qui classe et un score qui trouve les extremes n'ont pas le
    meme profil de deciles. C'est cette distinction qui empeche de prendre un
    top 20 flatteur pour un edge."""

    def test_un_signal_implante_donne_un_ecart_significatif(self, momentum_prices, cfg_syn):
        d = edge.decile_returns(momentum_prices, cfg_syn, n_buckets=5, top_n=6)
        assert d["spread"]["t_stat"] > 2.0
        assert d["deciles"][-1]["rendement"] > d["deciles"][0]["rendement"]

    def test_du_bruit_ne_donne_aucun_ecart(self, random_walk_prices, cfg_syn):
        d = edge.decile_returns(random_walk_prices, cfg_syn, n_buckets=5, top_n=6)
        assert abs(d["spread"]["t_stat"]) < 2.5

    def test_les_seaux_encadrent_la_moyenne_de_l_univers(self, momentum_prices, cfg_syn):
        d = edge.decile_returns(momentum_prices, cfg_syn, n_buckets=5, top_n=6)
        rendements = [x["rendement"] for x in d["deciles"]]
        assert min(rendements) <= d["univers"] <= max(rendements)
        assert d["n_dates"] > 20

    def test_inverser_un_vrai_signal_le_detruit(self, momentum_prices, cfg_syn):
        inv = edge.inverted_score(momentum_prices, cfg_syn)
        assert inv["ecart_cagr"] > 0.02, "un momentum implante doit s'effondrer une fois retourne"

    def test_le_score_est_restaure_apres_inversion(self, random_walk_prices, cfg_syn):
        import quantbot.factors as F
        avant = F.composite_score
        edge.inverted_score(random_walk_prices, cfg_syn)
        assert F.composite_score is avant
