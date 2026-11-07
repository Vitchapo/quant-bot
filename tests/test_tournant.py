"""Le test du tournant du mois, sur donnees SYNTHETIQUES uniquement.

Etape 3 du protocole (docs/protocole_tournant_du_mois.md) : sans effet, aucun
avantage ; avec un effet implante, les criteres le detectent ; modifier les
cours futurs ne change aucune position passee. Aucune vraie donnee ici.
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from quantbot import tendance as T
from quantbot import tournant as TM

#: Des couts de l'ordre de ceux de FTMO (docs/tendance_couts.csv), ronds.
COUTS = {n: {"transaction": 0.0002, "marge_achat": 0.05, "marge_vente": 0.04} for n in TM.INDICES}


def _rend(dates, r=None) -> pd.DataFrame:
    """Rendements (r, jours) au calendrier `dates` ; par defaut +-1 % en
    alternance : volatilite estimee constante des le 60e jour."""
    dates = pd.DatetimeIndex(dates)
    if r is None:
        r = np.where(np.arange(len(dates)) % 2 == 0, 0.01, -0.01)
    jours = pd.Series(dates, index=dates).diff().dt.days.fillna(1).to_numpy()
    return pd.DataFrame({"r": r, "jours": jours}, index=dates)


def _calendrier_janvier_2024():
    """Le 31 janvier 2024 (mercredi) et le 1er fevrier (jeudi) sont fermes."""
    return pd.bdate_range("2023-09-01", "2024-03-29").drop(
        pd.DatetimeIndex(["2024-01-31", "2024-02-01"]))


def _blocs(p: np.ndarray) -> list:
    """(debut, longueur, valeur) des suites de jours a position non nulle."""
    out, t = [], 0
    while t < len(p):
        if p[t] == 0:
            t += 1
            continue
        d = t
        while t < len(p) and p[t] == p[d]:
            t += 1
        out.append((d, t - d, p[d]))
    return out


# -- ce que le protocole fige ---------------------------------------------------------
def test_ce_que_le_protocole_fige():
    assert TM.INDICES == ("US500", "GER40", "UK100", "JP225", "AUS200", "HK50")
    assert TM.JOURS_FENETRE == 4 and TM.VOL_POSITION == 0.40
    assert TM.DEBUT_JUGE == pd.Period("2000-01", "M") and TM.FIN_JUGE == pd.Period("2026-09", "M")
    assert TM.DEBUT_APRES == pd.Period("2009-01", "M")
    assert (TM.TIRAGES, TM.GRAINE, TM.DECALAGE_MIN, TM.DECALAGE_MAX) == (1000, 20261106, 4, 14)
    assert (TM.SEUIL_T, TM.SEUIL_P, TM.MIN_POSITIFS, TM.RETARDS_NW) == (2.5, 0.01, 5, 3)
    # repris du protocole de tendance, que celui-ci dit identiques
    assert (T.COM_VOL, T.JOURS_AN, T.MIN_JOURS_VOL, T.BASE_TAUX) == (60, 261, 60, 365.0)
    assert T.DEBUT_DONNEES == pd.Timestamp("1998-01-01")
    assert T.DEBUT_MAX_SERIE == pd.Timestamp("2004-01-31")
    assert {n: T.INSTRUMENTS[n]["taux"] for n in TM.INDICES} == {
        "US500": "US", "GER40": "XM", "UK100": "GB", "JP225": "JP", "AUS200": "AU", "HK50": "US"}
    assert all(T.INSTRUMENTS[n]["famille"] == "indice" for n in TM.INDICES)
    # le script du test passe les valeurs figees, les tests en tirent moins
    sig = inspect.signature(TM.tester).parameters
    assert sig["tirages"].default == TM.TIRAGES and sig["graine"].default == TM.GRAINE


# -- la fenetre --------------------------------------------------------------------
def test_la_fenetre_sur_un_calendrier_avec_des_jours_feries():
    ix = TM.Indice("US500", _rend(_calendrier_janvier_2024()))
    assert pd.Timestamp("2024-01-29") in set(ix.dates[ix.entree])          # -2
    f = pd.Series(ix.fenetre(), index=ix.dates)
    jours = f["2024-01-22":"2024-02-16"]
    assert list(jours[jours].index.strftime("%m-%d")) == ["01-30", "02-02", "02-05", "02-06"]
    # partout ailleurs : les jours -1, +1, +2, +3 d'apres leur rang dans le mois,
    # sauf le premier mois des donnees, qui n'a pas de mois precedent
    rangs = TM.jours_de_fenetre(ix.dates).astype(bool)
    autres = ix.dates.to_period("M") != ix.dates[0].to_period("M")
    assert (f.to_numpy()[autres] == rangs[autres]).all()


def test_la_fenetre_suit_le_calendrier_de_chaque_indice():
    prix, taux = TM.synthetique(graine=21)
    g = TM.Grille(TM.preparer(prix, taux))
    for ix in g.indices:
        autres = ix.dates.to_period("M") != ix.dates[0].to_period("M")
        assert (ix.fenetre()[autres] == TM.jours_de_fenetre(ix.dates)[autres].astype(bool)).all()
    # les calendriers different d'un indice a l'autre (jours feries tires a part)
    assert len({len(ix.dates) for ix in g.indices}) > 1


def test_taille_fixe_pendant_la_fenetre_nulle_ailleurs():
    prix, taux = TM.synthetique(graine=3)
    g = TM.Grille(TM.preparer(prix, taux))
    for ix in g.indices:
        p = g.positions()[ix.nom].to_numpy()
        assert (p[~ix.fenetre()] == 0).all()
        blocs = _blocs(p)
        assert all(n == 4 for _, n, _ in blocs[:-1]) and blocs[-1][1] in (1, 2, 3, 4)
        for d, _, w in blocs:
            # achat a la cloture de la veille du premier jour : 40 % / la volatilite de ce jour-la
            assert w == pytest.approx(0.40 / ix.vol[d - 1], rel=1e-12)
            assert ix.fenetre()[d] and ix.dates[d - 1].to_period("M") == ix.dates[d].to_period("M")
        # toutes les fenetres dont la volatilite est connue a l'achat, et elles seules
        attendu = np.zeros(ix.n, dtype=bool)
        for e in ix.entree[ix.dispo[ix.entree]]:
            attendu[e + 1:e + 5] = True
        assert ((p > 0) == attendu).all()
        assert not ix.dispo[ix.entree[0]] and ix.dispo[ix.entree[-1]]


def test_les_donnees_finissent_en_fin_de_mois_la_derniere_fenetre_n_a_que_son_jour_moins_1():
    prix, taux = TM.synthetique(graine=3)
    g = TM.Grille(TM.preparer(prix, taux))
    for ix in g.indices:
        p = g.positions()[ix.nom]
        assert p.index[-1].to_period("M") == pd.Period("2026-09", "M")
        assert p.iloc[-1] > 0 and p.iloc[-2] == 0                          # -1 seul, sans -2
    ix = g.indices[0]
    frais = (pd.Series(g.jour(), index=g.cal) - pd.Series(g.jour(couts=COUTS), index=g.cal))
    a = ix.dates[ix.entree[-1]]
    assert frais[a] > 0 and a.to_period("M") == pd.Period("2026-09", "M")  # l'achat est paye


# -- les couts ---------------------------------------------------------------------
def test_couts_a_l_achat_a_la_vente_et_par_jour_calendaire():
    g = TM.Grille({"US500": _rend(_calendrier_janvier_2024())})
    c = {"US500": {"transaction": 0.001, "marge_achat": 0.0365, "marge_vente": 0.02}}
    frais = pd.Series(g.jour() - g.jour(couts=c), index=g.cal)
    ix = g.indices[0]
    w = 0.40 / ix.vol[ix.dates.get_loc(pd.Timestamp("2024-01-29"))]
    attendu = pd.Series(0.0, index=frais["2024-01-22":"2024-02-16"].index)
    attendu["2024-01-29"] += 0.001 * w                      # achat a la cloture de -2
    attendu["2024-01-30"] += w * 0.0365 * 1 / 365           # -1 : une nuit
    attendu["2024-02-02"] += w * 0.0365 * 3 / 365           # +1 : trois nuits (31/01 et 01/02 fermes)
    attendu["2024-02-05"] += w * 0.0365 * 3 / 365           # +2 : le week-end
    attendu["2024-02-06"] += w * 0.0365 * 1 / 365 + 0.001 * w   # +3 : vente
    np.testing.assert_allclose(frais["2024-01-22":"2024-02-16"].to_numpy(), attendu.to_numpy(),
                               rtol=1e-12, atol=1e-16)
    # au total : deux transactions et 8 jours calendaires de marge, du 29/01 au 06/02
    assert frais["2024-01-29":"2024-02-06"].sum() == pytest.approx(w * (2 * 0.001 + 0.0365 * 8 / 365))


def test_la_marge_vendeuse_ne_sert_jamais():
    prix, taux = TM.synthetique(graine=4)
    g = TM.Grille(TM.preparer(prix, taux))
    autre = {n: dict(c, marge_vente=0.3) for n, c in COUTS.items()}
    np.testing.assert_array_equal(g.jour(couts=COUTS), g.jour(couts=autre))


def test_les_couts_ne_touchent_que_les_criteres_4_et_5():
    prix, taux = TM.synthetique(graine=11, effet=0.1)
    a = TM.tester(prix, taux, TM.couts_nuls(), tirages=20)
    b = TM.tester(prix, taux, COUTS, tirages=20)
    assert a["c1"] == b["c1"] and a["c2"] == b["c2"] and a["c3"] == b["c3"]
    assert b["c5"]["moyenne_mensuelle"] < a["c5"]["moyenne_mensuelle"]
    assert b["c4"]["sharpe"] < a["c4"]["sharpe"]
    pd.testing.assert_series_equal(a["net"], a["brut"])                   # sans couts, net = brut
    # de l'ordre de deux transactions et six nuits de marge par mois, au nominal moyen
    assert 0.5 < b["infos"]["couts_moyens"] / (2.2 * (2 * 0.0002 + 0.05 * 6 / 365)) < 1.5


# -- rien ne lit l'avenir -------------------------------------------------------------
def test_les_cours_futurs_ne_changent_aucune_position_passee():
    prix, taux = TM.synthetique(graine=4, effet=0.2)
    coupe = pd.Timestamp("2015-06-17")
    rng = np.random.default_rng(7)
    autres = {n: s.where(s.index < coupe, s * np.exp(np.cumsum(rng.normal(0.0, 0.02, len(s)))))
              for n, s in prix.items()}
    g1, g2 = TM.Grille(TM.preparer(prix, taux)), TM.Grille(TM.preparer(autres, taux))
    avant = g1.cal < coupe
    k = g1.tirage(np.random.default_rng(1))
    for kk in (None, k):
        for c in (None, COUTS):
            np.testing.assert_array_equal(g1.jour(kk, c)[avant], g2.jour(kk, c)[avant])
        p1, p2 = g1.positions(kk), g2.positions(kk)
        for n in TM.INDICES:
            pd.testing.assert_series_equal(p1[n][p1[n].index < coupe], p2[n][p2[n].index < coupe])
    np.testing.assert_array_equal(g1.passif()[avant], g2.passif()[avant])
    assert not np.allclose(g1.jour()[~avant], g2.jour()[~avant])         # l'apres a bien change


def test_couper_les_donnees_a_une_fin_de_mois_ne_change_rien_avant():
    prix, taux = TM.synthetique(graine=5, effet=0.2)
    fin = pd.Timestamp("2013-04-30")
    g1 = TM.Grille(TM.preparer(prix, taux))
    g2 = TM.Grille(TM.preparer({n: s[s.index <= fin] for n, s in prix.items()}, taux))
    n = len(g2.cal)
    assert (g1.cal[:n] == g2.cal).all() and g2.cal[-1] <= fin
    k = g1.tirage(np.random.default_rng(2))
    for c in (None, COUTS):
        np.testing.assert_array_equal(g1.jour(None, c)[:n], g2.jour(None, c))
        np.testing.assert_array_equal(g1.jour(k, c)[:n], g2.jour(k[:len(g2.mois_fen)], c))


# -- le hasard du critere 4 ------------------------------------------------------------
def test_les_tirages_decalent_chaque_fenetre_du_meme_nombre_de_seances():
    prix, taux = TM.synthetique(graine=6)
    g = TM.Grille(TM.preparer(prix, taux))
    k = g.tirage(np.random.default_rng(3))
    assert set(np.unique(k)) == set(range(4, 15))
    pos = g.positions(k)
    for ix in g.indices:
        p = pos[ix.nom].to_numpy()
        blocs = _blocs(p)
        assert all(n == 4 for _, n, _ in blocs[:-1])
        assert len(blocs) >= 12 * 28
        for d, _, w in blocs:
            achat = d - 1
            # la vraie fenetre d'ou vient ce bloc : le dernier achat 4 a 14 seances plus tot
            j = np.flatnonzero((ix.entree <= achat - 4) & (ix.entree >= achat - 14))
            assert len(j) == 1
            assert achat - ix.entree[j[0]] == k[ix.m[j[0]]]                # le k de CE mois
            assert w == pytest.approx(0.40 / ix.vol[achat], rel=1e-12)     # meme dosage
        # jamais sur un vrai jour de fenetre, sauf apres un mois de 17 seances ou moins
        mois = ix.dates.to_period("M")
        courts = set(m for m, nb in pd.Series(mois).value_counts().items() if nb <= 17)
        touche = (p > 0) & ix.fenetre()
        assert all(mois[t] in courts or (mois[t] - 1) in courts for t in np.flatnonzero(touche))


def test_les_tirages_ont_le_meme_decalage_pour_tous_les_indices():
    prix, taux = TM.synthetique(graine=7, feries=0.0)        # memes jours de cotation partout
    g = TM.Grille(TM.preparer(prix, taux))
    k = g.tirage(np.random.default_rng(4))
    pos = g.positions(k)
    ref = pos[TM.INDICES[0]].to_numpy() > 0
    assert all(((pos[n].to_numpy() > 0) == ref).all() for n in TM.INDICES)
    assert not (ref & g.indices[0].fenetre()).any()          # mois de 20 seances au moins


# -- les criteres ----------------------------------------------------------------------
def test_le_critere_1_porte_sur_les_couples_indice_jour_de_la_periode():
    prix, taux = TM.synthetique(graine=14)
    g = TM.Grille(TM.preparer(prix, taux))
    c = g.couples()
    assert c["mois"].min() == TM.DEBUT_JUGE and c["mois"].max() == TM.FIN_JUGE
    for ix in g.indices:
        d = c[c["indice"] == ix.nom]
        m = ix.dates.to_period("M")
        k = np.flatnonzero((m >= TM.DEBUT_JUGE) & (m <= TM.FIN_JUGE))
        assert len(d) == len(k)
        np.testing.assert_allclose(d["y"].to_numpy(), ix.r[k] / ix.vol[k - 1], rtol=1e-12)
        assert (d["fenetre"].to_numpy() == ix.fenetre()[k]).all()
    # 4 jours de fenetre par mois juge et par indice, ni plus ni moins
    assert int(c["fenetre"].sum()) == 4 * 321 * 6


def test_portefeuille_passif_et_rendements_mensuels():
    prix, taux = TM.synthetique(graine=13, effet=0.1, feries=0.0)
    g = TM.Grille(TM.preparer(prix, taux))
    # meme dosage : les jours de fenetre, la strategie EST le portefeuille passif
    fen = g.indices[0].fenetre()
    assert all((ix.dates == g.cal).all() for ix in g.indices)
    np.testing.assert_allclose(g.jour(), np.where(fen, g.passif(), 0.0), rtol=0, atol=1e-15)
    # et les mois composent les jours comme pour la tendance
    j = g.jour(couts=COUTS)
    pd.testing.assert_series_equal(g.mensuels(j), T.mensuels(pd.Series(j, index=g.cal)),
                                   check_names=False, rtol=1e-12)


@pytest.mark.parametrize("graine", [1, 2])
def test_sans_effet_aucun_avantage(graine):
    res = TM.tester(*TM.synthetique(graine=graine), COUTS, tirages=100)
    assert not res["c1"]["ok"] and not res["c3"]["ok"] and not res["c4"]["ok"] and not res["c5"]["ok"]
    assert res["score"] <= 1


def test_un_marche_qui_monte_ne_suffit_pas():
    """Sans effet de calendrier, etre acheteur rapporte quand le marche monte.
    Le critere 5 seul s'y tromperait ; les criteres 1 et 3 non. Sur douze
    jeux de donnees, pour ne pas juger sur un seul tirage."""
    ok = {k: 0 for k in ("c1", "c3", "c5")}
    for graine in range(100, 112):
        res = TM.tester(*TM.synthetique(graine=graine, derive=1.5), TM.couts_nuls(), tirages=0)
        for k in ok:
            ok[k] += res[k]["ok"]
        assert res["score"] <= 2
    assert ok == {"c1": 0, "c3": 0, "c5": 12}


def test_avec_un_effet_implante_les_cinq_criteres_le_detectent():
    res = TM.tester(*TM.synthetique(graine=9, effet=0.3, derive=0.3), COUTS, tirages=100)
    assert res["score"] == 5
    assert res["c2"]["positifs"] == 6
    assert res["c4"]["au_moins_aussi_bons"] == 0 and res["c4"]["p"] == pytest.approx(1 / 101)
    assert res["infos"]["fenetres_irregulieres"] == 0
    assert len(res["net"]) == 321 and res["c5"]["mois_2009"] == 213
    texte = TM.rapport(res)
    assert "SCORE : 5 sur 5" in texte and texte.count("-> OUI") == 5


def test_le_critere_2_compte_les_indices_ou_l_effet_est_positif():
    effet = {n: 0.3 for n in TM.INDICES}
    effet.update(AUS200=-0.3, HK50=-0.3)
    res = TM.tester(*TM.synthetique(graine=10, effet=effet), COUTS, tirages=10)
    assert res["c2"]["positifs"] == 4 and not res["c2"]["ok"]
    assert res["c2"]["par_indice"]["HK50"]["coef"] < 0 < res["c2"]["par_indice"]["US500"]["coef"]
    effet["AUS200"] = 0.3
    res = TM.tester(*TM.synthetique(graine=10, effet=effet), COUTS, tirages=10)
    assert res["c2"]["positifs"] == 5 and res["c2"]["ok"]


def test_meme_graine_meme_resultat():
    prix, taux = TM.synthetique(graine=15, effet=0.05)
    a = TM.tester(prix, taux, COUTS, tirages=30, graine=123)
    b = TM.tester(prix, taux, COUTS, tirages=30, graine=123)
    assert a["c4"] == b["c4"]


# -- les donnees refusees et les fenetres irregulieres -------------------------------------------
def test_une_serie_tardive_ou_un_indice_absent_est_refuse():
    prix, taux = TM.synthetique(graine=12)
    tard = dict(prix, JP225=prix["JP225"][prix["JP225"].index >= "2005-01-03"])
    with pytest.raises(ValueError, match="JP225 commence apres janvier 2004"):
        TM.preparer(tard, taux)
    with pytest.raises(ValueError, match="indices absents : HK50"):
        TM.preparer({n: s for n, s in prix.items() if n != "HK50"}, taux)


def test_un_mois_trop_court_est_signale():
    """Un mois de deux seances dans la periode jugee : la fenetre qui le
    traverse deborde sur le mois suivant ; le test le compte et le publie."""
    jours = pd.bdate_range("2011-06-01", "2012-06-29")
    mars = jours[(jours >= "2012-03-01") & (jours <= "2012-03-31")]
    g = TM.Grille({"US500": _rend(jours.drop(mars[2:]))})
    assert g.irregulieres() == 1
    assert TM.Grille({"US500": _rend(jours)}).irregulieres() == 0
