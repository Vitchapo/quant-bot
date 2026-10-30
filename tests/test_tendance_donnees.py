"""Les donnees du test de tendance (etape 2), sur fichiers et series FABRIQUES.

Aucune vraie donnee de marche ici. On verifie que chaque format de source
est bien lu, que les controles du protocole trouvent les trous, les cours
figes et les sauts, et que la source de repli tranche comme prevu : un vrai
mouvement est confirme a une seance pres, un cours aberrant ne l'est jamais.
On verifie aussi qu'un fichier brut ne se remplace jamais.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import donnees_tendance as S  # noqa: E402
from quantbot import tendance as T  # noqa: E402
from quantbot import tendance_donnees as D  # noqa: E402

CSV_STOOQ = ("Date,Open,High,Low,Close,Volume\n"
             "1998-01-02,289.0,290.1,288.5,289.6,0\n"
             "1998-01-05,289.6,291.0,289.0,290.4,0\n")


def test_les_sources_figees_par_le_protocole():
    assert list(D.SERIES) == list(T.INSTRUMENTS)
    princ = [D.SERIES[n][0][:2] for n in T.INSTRUMENTS]
    assert princ[:6] == [("yahoo", s) for s in ("^GSPC", "^GDAXI", "^FTSE", "^N225", "^AXJO", "^HSI")]
    assert princ[6:13] == [("fred", s) for s in ("DEXUSEU", "DEXUSUK", "DEXJPUS", "DEXSZUS",
                                                  "DEXCAUS", "DEXUSAL", "DEXUSNZ")]
    assert princ[13:] == [("stooq", "XAUUSD"), ("stooq", "XAGUSD")]
    assert [D.SERIES[n][1][0] for n in T.INSTRUMENTS] == ["stooq"] * 6 + ["yahoo"] * 9
    assert (D.SERIES["XAUUSD"][1][1], D.SERIES["XAGUSD"][1][1]) == ("GC=F", "SI=F")
    besoins = {s.get(k) for s in T.INSTRUMENTS.values() for k in ("taux", "achetee", "vendue")}
    assert besoins - {None} <= set(D.TAUX) and "DE" in D.TAUX
    assert (D.TROU_MAX, D.FIGE_MAX, D.SAUT_MAX, D.SIGMAS_MAX) == (5, 5, 0.25, 10.0)
    assert D.DEBUT_MAX == pd.Timestamp("2004-01-31")
    assert D.nom_fichier(D._yahoo("^GSPC")) == "yahoo__GSPC.json"
    assert D.nom_fichier(D._stooq("XAUUSD")) == "stooq_XAUUSD.csv"
    assert D.nom_fichier(D._fred("DEXUSEU")) == "fred_DEXUSEU.csv"
    assert D.nom_fichier(D._bri("US")) == "bri_US.csv"


# -- lecture des fichiers bruts -----------------------------------------------------
def _yahoo_json(instants, closes, tz):
    return json.dumps({"chart": {"result": [{
        "meta": {"exchangeTimezoneName": tz},
        "timestamp": [int(t.timestamp()) for t in instants],
        "indicators": {"quote": [{"close": closes}]}}]}})


def test_lire_les_quatre_formats(tmp_path):
    f = tmp_path / "fred.csv"
    f.write_text("observation_date,DEXUSEU\n2020-01-02,1.1215\n2020-01-03,.\n2020-01-06,1.1194\n")
    assert D.lire(f, "fred").to_dict() == {pd.Timestamp("2020-01-02"): 1.1215,
                                           pd.Timestamp("2020-01-06"): 1.1194}

    b = tmp_path / "bri.csv"
    b.write_text("FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\n"
                 "D,CH,2015-01-15,-0.75\nD,CH,2015-01-16,\nD,CH,2015-01-17,-0.75\n")
    t = D.lire(b, "bri")
    assert len(t) == 2 and (t == -0.75).all()                 # un taux negatif est garde

    for entete in ("Date,Open,High,Low,Close,Volume",                        # stooq.com
                   "Data,Otwarcie,Najwyzszy,Najnizszy,Zamkniecie,Wolumen"):  # stooq.pl
        q = tmp_path / "stooq.csv"
        q.write_text(CSV_STOOQ.replace("Date,Open,High,Low,Close,Volume", entete))
        assert D.lire(q, "stooq").tolist() == [289.6, 290.4]

    # Sydney ouvre a 10:00, soit 23:00 UTC la VEILLE en janvier : la date
    # retenue est celle de la bourse, pas celle d'UTC.
    y = tmp_path / "yahoo.json"
    instants = [pd.Timestamp(j + " 10:00", tz="Australia/Sydney")
                for j in ("2020-01-06", "2020-01-07", "2020-01-08")]
    y.write_text(_yahoo_json(instants, [6826.4, None, 6817.6], "Australia/Sydney"))
    s = D.lire(y, "yahoo")
    assert list(s.index) == [pd.Timestamp("2020-01-06"), pd.Timestamp("2020-01-08")]
    assert s.tolist() == [6826.4, 6817.6]                     # le cours manquant est ecarte


def test_la_page_de_verification_de_stooq_n_est_pas_un_fichier(tmp_path):
    q = tmp_path / "stooq.csv"
    q.write_text("<!DOCTYPE html><html><body><noscript>This site requires JavaScript to "
                 "verify your browser.</noscript></body></html>")
    with pytest.raises(ValueError, match="page web"):
        D.lire(q, "stooq")
    q.write_text("No data\n")
    with pytest.raises(ValueError):
        D.lire(q, "stooq")


# -- les controles du protocole ---------------------------------------------------------
def _marche(debut="2003-01-01", fin="2006-12-31", graine=0, vol=0.01):
    """Une marche au hasard sur jours ouvres, sans aucun defaut."""
    jours = pd.bdate_range(debut, fin)
    rng = np.random.default_rng(graine)
    return pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, vol, len(jours)))), index=jours)


def test_une_serie_propre_n_a_aucun_defaut():
    p = _marche()
    c = D.controler(p, p * 1.001)
    assert c["defauts"] == [] and c["n"] == len(p)


def test_un_trou_se_tranche_avec_le_repli():
    """Golden Week 2019 : Tokyo ferme du 27 avril au 6 mai, 6 jours ouvres."""
    p = _marche("2003-01-01", "2020-12-31")
    ferme = (p.index > "2019-04-26") & (p.index < "2019-05-07")
    assert ferme.sum() == 6
    trou = p[~ferme]
    sans = D.controler(trou, None)["defauts"]
    assert [(x["type"], x["jours"], x["resolu"]) for x in sans] == [("trou", 6, False)]
    ferme_aussi = D.controler(trou, trou * 1.001)["defauts"]
    assert [(x["type"], x["resolu"]) for x in ferme_aussi] == [("trou", True)]
    cote = D.controler(trou, p)["defauts"]                   # le repli cote : des jours perdus
    assert [(x["type"], x["resolu"]) for x in cote] == [("trou", False)]
    cinq = p[~((p.index > "2019-04-26") & (p.index < "2019-05-06"))]
    assert D.controler(cinq, None)["defauts"] == []           # 5 jours ouvres : tolere


def test_un_cours_fige_se_tranche_avec_le_repli():
    p = _marche(graine=1)
    fige = p.copy()
    fige.iloc[500:507] = fige.iloc[500]                       # 7 cotations identiques
    d = D.controler(fige, None)["defauts"]
    assert [(x["type"], x["n"], x["resolu"]) for x in d] == [("fige", 7, False)]
    assert [x["resolu"] for x in D.controler(fige, p)["defauts"]] == [False]      # le repli bouge
    assert [x["resolu"] for x in D.controler(fige, fige)["defauts"]] == [True]    # lui non plus
    cinq = p.copy()
    cinq.iloc[500:505] = cinq.iloc[500]                       # 5 identiques : tolere
    assert D.controler(cinq, None)["defauts"] == []


def test_un_vrai_saut_est_confirme_a_une_seance_pres():
    """Le Brexit vu par deux sources : FRED releve a midi a New York, Yahoo
    date sa barre autrement. Le meme mouvement, decale d'une seance."""
    p = _marche(graine=2)
    k = 700
    choc = p.copy()
    choc.iloc[k:] *= 0.85                                     # -15 % en un jour
    decale = p.copy()
    decale.iloc[k + 1:] *= 0.85                               # le repli le voit le lendemain
    d = D.controler(choc, decale)["defauts"]
    assert [(x["type"], x["resolu"]) for x in d] == [("saut", True)]
    assert d[0]["date"] == p.index[k]
    assert d[0]["rendement"] == pytest.approx(0.85 * p.iloc[k] / p.iloc[k - 1] - 1.0)
    # a la meme date, le repli n'a presque pas bouge ; une seance plus loin, il a vu le choc
    assert abs(d[0]["mouvements_repli"][0]) < 0.05 and d[0]["mouvements_repli"][1] < -0.10
    assert [x["resolu"] for x in D.controler(choc, p)["defauts"]] == [False]     # rien vu
    assert [x["resolu"] for x in D.controler(choc, None)["defauts"]] == [False]  # pas de repli


def test_un_cours_aberrant_n_est_jamais_confirme():
    """Un cours faux un seul jour : deux sauts, et aucune fenetre du repli ne
    les confirme, meme elargie de deux seances."""
    p = _marche(graine=3)
    faux = p.copy()
    faux.iloc[700] *= 1.30
    d = D.controler(faux, p)["defauts"]
    assert [(x["type"], x["resolu"]) for x in d] == [("saut", False), ("saut", False)]
    assert all(len(x["mouvements_repli"]) == 3 for x in d)
    assert all(max(abs(m) for m in x["mouvements_repli"]) < 0.10 for x in d)


def test_une_serie_qui_commence_apres_janvier_2004_est_exclue():
    d = D.controler(_marche("2004-02-02", "2008-12-31"), None)["defauts"]
    assert [(x["type"], x["resolu"]) for x in d] == [("debut tardif", False)]
    assert D.controler(_marche("2004-01-30", "2008-12-31"), None)["defauts"] == []


# -- empreintes et chargement -------------------------------------------------------------
def test_une_empreinte_qui_change_est_signalee(tmp_path):
    brut = tmp_path / "brut"
    brut.mkdir()
    (brut / "a.csv").write_bytes(b"1,2\n")
    (brut / "b.csv").write_bytes(b"3,4\n")
    m = tmp_path / "manifeste.csv"
    m.write_text("fichier,sha256\na.csv,%s\nb.csv,%s\nc.csv,\n"
                 % (D.sha256(brut / "a.csv"), D.sha256(brut / "b.csv")))
    assert D.verifier_empreintes(brut, m) == []
    (brut / "b.csv").write_bytes(b"3,5\n")
    assert D.verifier_empreintes(brut, m) == ["b.csv"]
    (brut / "a.csv").unlink()
    assert D.verifier_empreintes(brut, m) == ["a.csv", "b.csv"]


def _bri(chemin, valeurs: dict):
    lignes = ["FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE"] + ["D,X,%s,%s" % kv for kv in valeurs.items()]
    chemin.write_text("\n".join(lignes) + "\n")


def test_charger_prolonge_la_zone_euro_par_l_allemagne(tmp_path):
    for src in D.TAUX.values():
        _bri(tmp_path / D.nom_fichier(src), {"1998-06-01": 1.0, "2000-06-01": 2.0})
    _bri(tmp_path / "bri_DE.csv", {"1998-06-01": 3.3, "1998-12-31": 3.0})
    _bri(tmp_path / "bri_XM.csv", {"1998-06-01": 9.9, "1999-01-01": 3.0, "2000-06-01": 4.25})
    (tmp_path / D.nom_fichier(D.SERIES["EURUSD"][0])).write_text(
        "observation_date,DEXUSEU\n1999-01-04,1.1812\n2026-09-30,1.17\n2026-10-01,1.18\n")
    prix, taux = D.charger(exclus=[n for n in D.SERIES if n != "EURUSD"], dossier=tmp_path)
    assert list(prix) == ["EURUSD"]
    assert prix["EURUSD"].index.max() == pd.Timestamp("2026-09-30")   # rien apres le dernier mois juge
    assert "DE" not in taux
    assert taux["XM"].to_dict() == {pd.Timestamp("1998-06-01"): 3.3, pd.Timestamp("1998-12-31"): 3.0,
                                    pd.Timestamp("1999-01-01"): 3.0, pd.Timestamp("2000-06-01"): 4.25}


# -- Stooq, a la main -------------------------------------------------------------------------
def test_un_fichier_stooq_depose_a_la_main_est_recopie_tel_quel(tmp_path):
    depot, brut = tmp_path / "a_importer", tmp_path / "brut"
    depot.mkdir()
    brut.mkdir()
    src = D.SERIES["XAUUSD"][0]
    cible = brut / D.nom_fichier(src)
    with pytest.raises(FileNotFoundError):
        S.importer(src, cible, depot)
    (depot / "xauusd_d.csv").write_bytes(CSV_STOOQ.encode())
    (depot / "xagusd_d.csv").write_text("<!DOCTYPE html><html>verification</html>")
    ligne = S.importer(src, cible, depot)
    assert cible.read_bytes() == CSV_STOOQ.encode()
    assert ligne["sha256"] == D.sha256(cible) and ligne["octets"] == len(CSV_STOOQ)
    assert (depot / "xauusd_d.csv").exists()                 # le depot n'est pas efface
    with pytest.raises(FileExistsError):                      # un fichier brut ne se remplace pas
        S.importer(src, cible, depot)
    argent = D.SERIES["XAGUSD"][0]
    with pytest.raises(ValueError, match="page web"):         # la page de verification de Stooq
        S.importer(argent, brut / D.nom_fichier(argent), depot)
    assert not (brut / D.nom_fichier(argent)).exists()


def test_le_nom_donne_par_stooq_ou_le_navigateur_est_reconnu(tmp_path):
    for nom in ("^NKX_d.csv", "nkx_d (1).csv", "nkx.csv", "nkxx_d.csv", "^nkx_d.txt", "xauusd_d.csv"):
        (tmp_path / nom).write_text("x")
    trouves = [f.name for f in S.candidats(D.SERIES["JP225"][1], tmp_path)]
    assert trouves == sorted(["^NKX_d.csv", "nkx_d (1).csv", "nkx.csv"])
    with pytest.raises(ValueError, match="plusieurs"):
        S.importer(D.SERIES["JP225"][1], tmp_path / "cible.csv", tmp_path)


def test_recuperer_ne_retelecharge_jamais_un_fichier_present(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "BRUT", tmp_path / "brut")
    monkeypatch.setattr(S, "A_IMPORTER", tmp_path / "a_importer")
    monkeypatch.setattr(D, "SERIES", {"XAUUSD": D.SERIES["XAUUSD"]})
    monkeypatch.setattr(D, "TAUX", {})
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    appels = []

    def faux_telecharger(adresse, source):
        appels.append(source)
        instants = [pd.Timestamp("2000-08-30 00:00", tz="America/New_York")]
        return _yahoo_json(instants, [273.9], "America/New_York").encode()

    monkeypatch.setattr(S, "telecharger", faux_telecharger)
    m = S.recuperer({})
    assert appels == ["yahoo"]                                # Stooq : jamais par le script
    assert m["stooq_XAUUSD.csv"]["statut"] == "A TELECHARGER A LA MAIN"
    assert m["yahoo_GC_F.json"]["sha256"] == D.sha256(tmp_path / "brut" / "yahoo_GC_F.json")
    (tmp_path / "a_importer").mkdir()
    (tmp_path / "a_importer" / "xauusd_d.csv").write_text(CSV_STOOQ)
    m = S.recuperer(m)
    assert appels == ["yahoo"]                                # rien de retelecharge
    assert m["stooq_XAUUSD.csv"]["statut"] == "ok, telecharge a la main (xauusd_d.csv)"
    assert m["stooq_XAUUSD.csv"]["sha256"] == D.sha256(tmp_path / "brut" / "stooq_XAUUSD.csv")


def test_le_tableau_du_protocole_dit_pourquoi_en_clair():
    saut = {"type": "saut", "resolu": True, "date": pd.Timestamp("2016-06-24"), "rendement": -0.078,
            "sigmas": 13.7, "repli": True, "mouvements_repli": [-0.016, -0.083]}
    assert S.decrire(saut) == "saut du 24/06/2016 (−7,8 %, 14 écarts-types), confirmé par le repli"
    trou = {"type": "trou", "resolu": False, "a": pd.Timestamp("2019-04-26"),
            "b": pd.Timestamp("2019-05-07"), "jours": 6, "repli": False}
    assert S.verdict({"absent": False, "defauts": [trou]}) == (
        "**exclu** : trou du 26/04/2019 au 07/05/2019 (6 jours ouvrés sans cotation), "
        "pas de source de repli pour trancher")
    assert S.verdict({"absent": False, "defauts": []}) == "retenu, aucun défaut"
    assert S.verdict({"absent": True, "defauts": []}) == "**exclu** : fichier absent"
