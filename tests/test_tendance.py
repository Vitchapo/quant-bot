"""Le test de la tendance multi-actifs, mis au point sur donnees SYNTHETIQUES.

Etape 3 de docs/protocole_tendance_multi_actifs.md : sans signal, aucun
avantage ; avec une tendance implantee, les criteres la detectent ; modifier
les cours futurs ne change aucune position passee. Aucune donnee reelle ici.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot import tendance as T


def test_ce_que_le_protocole_fige():
    """Changer une de ces valeurs, c'est changer le protocole apres coup."""
    assert T.MOIS_SIGNAL == 12
    assert T.COM_VOL == 60 and T.JOURS_AN == 261 and T.MIN_JOURS_VOL == 60
    assert T.VOL_POSITION == 0.40
    assert T.BASE_TAUX == 365.0
    assert (str(T.DEBUT_JUGE), str(T.FIN_JUGE), str(T.DEBUT_APRES)) == ("2000-01", "2026-09", "2013-01")
    assert T.TIRAGES == 1000 and T.GRAINE == 20261010
    assert T.RETARDS_NW == 3 and T.SEUIL_T == 2.0 and T.QUINTILES == 5
    assert list(T.INSTRUMENTS) == ["US500", "GER40", "UK100", "JP225", "AUS200", "HK50",
                                   "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
                                   "AUDUSD", "NZDUSD", "XAUUSD", "XAGUSD"]
    assert T.INSTRUMENTS["HK50"]["taux"] == "US"            # monnaie arrimee au dollar
    assert T.FAMILLES_PASSIVES == ("indice", "metal")


# -- ce que rapporte une position ---------------------------------------------
def _taux_constants(**niveaux):
    jours = pd.date_range("1997-01-01", "2030-12-31", freq="D")
    return {k: pd.Series(v, index=jours) for k, v in niveaux.items()}


def test_portage_d_une_devise_achetee():
    """EURUSD acheteur : + taux de l'euro - taux du dollar, jours calendaires."""
    taux = _taux_constants(XM=2.0, US=3.0)
    dates = pd.DatetimeIndex(["2020-01-03", "2020-01-06", "2020-01-07"])   # vendredi -> lundi
    prix = pd.Series([1.10, 1.10, 1.10], index=dates)
    r = T.rendements_exces(prix, T.INSTRUMENTS["EURUSD"], taux)
    assert r["jours"].tolist() == [3, 1]
    assert r["r"].iloc[0] == pytest.approx((0.02 - 0.03) * 3 / 365)
    assert r["r"].iloc[1] == pytest.approx((0.02 - 0.03) * 1 / 365)


def test_indice_et_metal_paient_le_taux_court():
    taux = _taux_constants(US=4.0, JP=0.5)
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07"])
    r_jp = T.rendements_exces(pd.Series([100.0, 101.0], index=dates), T.INSTRUMENTS["JP225"], taux)
    assert r_jp["r"].iloc[0] == pytest.approx(0.01 - 0.005 / 365)
    r_or = T.rendements_exces(pd.Series([100.0, 99.0], index=dates), T.INSTRUMENTS["XAUUSD"], taux)
    assert r_or["r"].iloc[0] == pytest.approx(-0.01 - 0.04 / 365)


def test_le_taux_est_celui_connu_la_veille():
    """Une hausse de taux annoncee aujourd'hui ne paie pas les jours passes."""
    jours = pd.date_range("2019-12-01", "2020-02-01", freq="D")
    us = pd.Series(1.0, index=jours)
    us[us.index >= "2020-01-07"] = 5.0
    dates = pd.DatetimeIndex(["2020-01-06", "2020-01-07", "2020-01-08"])
    r = T.rendements_exces(pd.Series([100.0] * 3, index=dates), T.INSTRUMENTS["US500"], {"US": us})
    assert r["r"].iloc[0] == pytest.approx(-0.01 / 365)     # taux du 06/01
    assert r["r"].iloc[1] == pytest.approx(-0.05 / 365)     # taux du 07/01


# -- le signal et les positions -------------------------------------------------
def test_signal_douze_mois_et_execution_le_lendemain():
    jours = pd.bdate_range("2000-01-03", "2002-12-31")
    r = pd.Series(0.0004, index=jours)                       # tendance haussiere reguliere
    r[jours >= "2001-07-01"] = -0.0009                       # puis baissiere
    d = T.decisions(r)
    assert not d.loc[pd.Period("2000-12", "M"), "dispo"]     # pas encore 12 fins de mois
    assert d.loc[pd.Period("2001-01", "M"), "dispo"]
    assert d.loc[pd.Period("2001-01", "M"), "signal"] == 1
    assert d.loc[pd.Period("2002-12", "M"), "signal"] == -1
    ligne = d.loc[pd.Period("2001-03", "M")]
    assert ligne["date"] == pd.Timestamp("2001-03-30")
    assert ligne["execution"] == pd.Timestamp("2001-04-02")  # cotation suivante
    assert ligne["taille"] == pytest.approx(T.VOL_POSITION / ligne["vol"])


def test_la_position_vaut_a_partir_du_lendemain_de_l_execution():
    prix, taux = T.synthetique(graine=3)
    rend, dec = T.preparer({"US500": prix["US500"]}, taux)
    g = T.Grille(rend, dec)
    d = dec["US500"]
    k = int(np.where(d["dispo"].to_numpy())[0][0])
    t_exe = g.exe[0][k]
    sig = {"US500": np.ones(len(d))}
    pos, _ = g.positions(sig)
    assert np.isnan(pos[0, t_exe])                            # le jour meme : rien encore
    assert pos[0, t_exe + 1] == pytest.approx(d["taille"].iloc[k])


def test_les_cours_futurs_ne_changent_aucune_position_passee():
    prix, taux = T.synthetique(graine=4, tendance=0.5)
    coupure = pd.Timestamp("2012-06-15")
    rng = np.random.default_rng(0)
    trafique = {}
    for n, p in prix.items():
        q = p.copy()
        apres = q.index > coupure
        q[apres] = q[apres] * np.exp(np.cumsum(rng.normal(0.0, 0.05, apres.sum())))
        trafique[n] = q
    _, d1 = T.preparer(prix, taux)
    _, d2 = T.preparer(trafique, taux)
    for n in d1:
        a, b = d1[n], d2[n]
        avant = a["date"] <= coupure
        pd.testing.assert_series_equal(a.loc[avant, "signal"], b.loc[avant, "signal"])
        pd.testing.assert_series_equal(a.loc[avant, "taille"], b.loc[avant, "taille"])
    # et le rendement du portefeuille jusqu'a la coupure est le meme
    r1 = T.Grille(*T.preparer(prix, taux))
    r2 = T.Grille(*T.preparer(trafique, taux))
    s1 = r1.rendements({n: d1[n]["signal"].to_numpy() for n in r1.noms})
    s2 = r2.rendements({n: d2[n]["signal"].to_numpy() for n in r2.noms})
    pd.testing.assert_series_equal(s1[s1.index <= coupure], s2[s2.index <= coupure])


# -- les couts ------------------------------------------------------------------
def test_couts_une_transaction_a_l_entree_et_la_marge_chaque_jour():
    prix, taux = T.synthetique(graine=5)
    rend, dec = T.preparer({"US500": prix["US500"]}, taux)
    g = T.Grille(rend, dec)
    toujours = {"US500": np.ones(len(dec["US500"]))}
    sans = g.rendements(toujours)
    c = {"US500": {"transaction": 0.001, "marge_achat": 0.02, "marge_vente": 0.0}}
    avec = g.rendements(toujours, c)
    ecart = (sans - avec)
    d = dec["US500"]
    k = int(np.where(d["dispo"].to_numpy())[0][0])
    t_exe = g.exe[0][k]
    tailles = d["taille"].to_numpy()
    # a l'entree : spread + commission sur tout le nominal
    assert ecart.iloc[t_exe] == pytest.approx(0.001 * tailles[k])
    # ensuite, chaque mois : seulement l'ajustement de taille, et la marge
    t_suiv = g.exe[0][k + 1]
    marge = (0.02 * tailles[k] * g.j[0, t_exe + 1:t_suiv + 1]).sum()
    ajust = 0.001 * abs(tailles[k + 1] - tailles[k])
    assert ecart.iloc[t_exe + 1:t_suiv + 1].sum() == pytest.approx(marge + ajust)


# -- le hasard du critere 4 -------------------------------------------------------
def _periodes(s):
    s = np.asarray(s)
    debuts = np.r_[0, np.where(np.diff(s) != 0)[0] + 1]
    fins = np.r_[debuts[1:], len(s)]
    return [(s[a], b - a) for a, b in zip(debuts, fins)]


@pytest.mark.parametrize("graine", [0, 1, 2, 3])
def test_le_hasard_garde_periodes_durees_et_retournements(graine):
    rng = np.random.default_rng(graine)
    vrai = np.repeat(np.tile([1.0, -1.0], 9), rng.integers(1, 15, 18))
    dispo = np.ones(len(vrai), dtype=bool)
    dispo[:5] = False
    tire = T.melanger(vrai, dispo, np.random.default_rng(graine + 10))
    a, b = _periodes(vrai[5:]), _periodes(tire[5:])
    assert len(a) == len(b)                                         # retournements
    for v in (1.0, -1.0):
        assert sorted(l for s, l in a if s == v) == sorted(l for s, l in b if s == v)
    assert (tire[5:] == 1).sum() == (vrai[5:] == 1).sum()            # temps acheteur
    np.testing.assert_array_equal(tire[:5], vrai[:5])               # mois indisponibles


# -- les criteres -------------------------------------------------------------------
def test_sans_signal_aucun_avantage():
    prix, taux = T.synthetique(graine=1, tendance=0.0)
    res = T.tester(prix, taux, T.couts_nuls(), tirages=100)
    assert len(res["instruments"]) == 15
    assert not res["c1"]["ok"] and not res["c4"]["ok"] and not res["c5"]["ok"]
    assert res["score"] == 0


def test_une_tendance_implantee_est_detectee():
    prix, taux = T.synthetique(graine=2, tendance=1.0)
    res = T.tester(prix, taux, T.couts_nuls(), tirages=200)
    assert res["score"] == 5, T.rapport(res)


def test_les_couts_ne_comptent_qu_aux_criteres_4_et_5():
    prix, taux = T.synthetique(graine=2, tendance=1.0)
    chers = {n: {"transaction": 0.02, "marge_achat": 0.05, "marge_vente": 0.05} for n in T.INSTRUMENTS}
    a = T.tester(prix, taux, T.couts_nuls(), tirages=20)
    b = T.tester(prix, taux, chers, tirages=20)
    for k in ("c1", "c2", "c3"):
        assert a[k] == b[k]
    assert b["c5"]["moyenne_mensuelle"] < a["c5"]["moyenne_mensuelle"]


def test_une_serie_trop_courte_est_refusee():
    """Commencer apres janvier 2004 exclut un instrument a l'etape 2 ; le
    test refuse de le recevoir plutot que de l'ecarter sans le dire."""
    prix, taux = T.synthetique(graine=6)
    prix["HK50"] = prix["HK50"][prix["HK50"].index >= "2005-01-01"]
    with pytest.raises(ValueError):
        T.preparer(prix, taux)
