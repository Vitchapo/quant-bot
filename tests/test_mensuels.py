"""Rendements mensuels du backtest : la carte mois x annee les lit case par case.

Une case fausse se lit comme une vraie. D'ou l'ordre des tests : d'abord que
le produit des mois redonne exactement le rendement total, ensuite que chaque
mois est borne par sa VRAIE derniere seance, enfin qu'aucun mois n'est
invente pour l'indice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.explore import _mensuels, _mensuels_alignes


def _serie(debut="2021-01-04", n=520, seed=7):
    idx = pd.bdate_range(debut, periods=n)
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(rng.normal(0.0004, 0.011, n).cumsum()), index=idx)


class TestExactitude:
    def test_le_produit_des_mois_redonne_le_total(self):
        s = _serie()
        _, r = _mensuels(s)
        total = np.prod([1 + x for x in r]) - 1
        assert total == pytest.approx(s.iloc[-1] / s.iloc[0] - 1, rel=1e-4)

    def test_chaque_mois_est_borne_par_sa_vraie_derniere_seance(self):
        """C'est la raison d'etre du calcul cote serveur : la courbe dessinee
        est amincie a un point par semaine et raterait la fin de mois."""
        s = _serie()
        mois, r = _mensuels(s)
        k = mois.index("2021-06")
        fin_mai = s[(s.index.year == 2021) & (s.index.month == 5)].iloc[-1]
        fin_juin = s[(s.index.year == 2021) & (s.index.month == 6)].iloc[-1]
        assert r[k] == pytest.approx(fin_juin / fin_mai - 1, abs=1e-6)

    def test_un_mois_connu(self):
        idx = pd.bdate_range("2022-01-03", "2022-03-31")
        v = pd.Series(100.0, index=idx)
        v[v.index.month == 2] = 110.0
        v[v.index.month == 3] = 99.0
        mois, r = _mensuels(v)
        assert mois == ["2022-01", "2022-02", "2022-03"]
        assert r[0] == pytest.approx(0.0)
        assert r[1] == pytest.approx(0.10)
        assert r[2] == pytest.approx(99 / 110 - 1)

    def test_le_premier_mois_partiel_part_de_la_premiere_valeur(self):
        idx = pd.bdate_range("2022-01-17", "2022-02-28")          # debut en milieu de mois
        v = pd.Series(np.linspace(100, 120, len(idx)), index=idx)
        mois, r = _mensuels(v)
        jan = v[v.index.month == 1]
        assert r[0] == pytest.approx(jan.iloc[-1] / jan.iloc[0] - 1, abs=1e-6)

    def test_serie_trop_courte(self):
        assert _mensuels(pd.Series([1.0], index=pd.bdate_range("2022-01-03", periods=1))) == ([], [])
        assert _mensuels(pd.Series(dtype="float64")) == ([], [])


class _Resultat:
    def __init__(self, equity, benchmark=None):
        self.equity, self.benchmark = equity, benchmark


class TestAlignement:
    def test_l_indice_n_est_jamais_invente(self):
        """Un indice qui commence plus tard laisse des None, pas des zeros ni
        un report du premier mois connu."""
        s = _serie("2021-01-04", 300)
        ind = s[s.index >= "2021-04-01"] * 3.0
        out = _mensuels_alignes(_Resultat(s, ind), _Resultat(s * 2.0))
        k = out["mois"].index("2021-04")
        assert all(x is None for x in out["indice"][:k])
        assert out["indice"][k] is not None

    def test_meme_calendrier_pour_les_trois(self):
        s = _serie()
        out = _mensuels_alignes(_Resultat(s, s * 1.5), _Resultat(s * 0.5))
        n = len(out["mois"])
        assert n > 20
        assert len(out["strategie"]) == len(out["univers"]) == len(out["indice"]) == n
        # une serie proportionnelle a la strategie a les memes rendements
        assert out["univers"][5] == pytest.approx(out["strategie"][5])

    def test_sans_indice(self):
        s = _serie()
        out = _mensuels_alignes(_Resultat(s, None), _Resultat(s))
        assert out["indice"] == [None] * len(out["mois"])


def test_le_backtest_les_expose(momentum_prices, base_config):
    """Bout en bout : ce que le navigateur recoit."""
    from quantbot.explore import Explorer, defaults
    ex = Explorer(momentum_prices, base_config)
    out = ex.run(defaults(base_config))
    m = out["mensuels"]
    assert len(m["mois"]) > 12
    assert len(m["strategie"]) == len(m["mois"])
    assert m["mois"] == sorted(m["mois"])
    import json
    json.dumps(out, allow_nan=False)                 # le serveur l'exige
