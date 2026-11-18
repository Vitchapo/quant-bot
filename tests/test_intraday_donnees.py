"""Les donnees du momentum intrajournalier, sur des barres FABRIQUEES.

Aucune vraie donnee ici. On verifie l'heure de chaque fenetre (changements
d'heure compris), la lecture et l'ecriture des barres a l'octet pres, et que
chaque controle de l'etape 2 attrape le defaut qu'il doit attraper, sans
condamner une donnee saine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import intraday_synthetique as SY  # noqa: E402
from quantbot import intraday_donnees as I  # noqa: E402
from quantbot import tendance_donnees as TD  # noqa: E402

T = pd.Timestamp


# -- les heures ----------------------------------------------------------------------
@pytest.mark.parametrize("indice, jour, attendu", [
    ("US500", "2026-10-15", "2026-10-15 22:30"),    # 15:30 a New York, toujours 22:30 au serveur
    ("US500", "2026-01-15", "2026-01-15 22:30"),
    ("GER40", "2026-01-15", "2026-01-15 18:00"),    # 17:00 a Francfort
    ("GER40", "2026-07-15", "2026-07-15 18:00"),
    ("GER40", "2026-03-20", "2026-03-20 19:00"),    # New York a change d'heure, pas l'Europe
    ("GER40", "2026-10-28", "2026-10-28 19:00"),    # l'Europe est revenue a l'heure d'hiver, pas New York
    ("UK100", "2026-07-15", "2026-07-15 18:00"),    # 16:00 a Londres
    ("UK100", "2026-03-20", "2026-03-20 19:00"),
    ("JP225", "2024-11-01", "2024-11-01 08:30"),    # 14:30 a Tokyo
    ("JP225", "2024-11-05", "2024-11-05 08:00"),    # 15:00 a Tokyo depuis le 5/11/2024 ; New York a l'heure d'hiver
    ("AUS200", "2026-01-15", "2026-01-15 06:30"),   # 15:30 a Sydney, ete austral
    ("AUS200", "2026-07-15", "2026-07-15 08:30"),
    ("HK50", "2026-07-15", "2026-07-15 10:30"),     # 15:30 a Hong Kong
])
def test_le_debut_de_la_fenetre_a_l_heure_du_serveur(indice, jour, attendu):
    assert I.debut_fenetre(indice, [jour])[0] == T(attendu)


def test_les_semaines_de_decalage_entre_l_europe_et_les_etats_unis():
    printemps = pd.date_range("2026-03-01", "2026-04-05")
    vus = printemps[I.semaines_de_decalage(printemps)]
    assert vus.min() == T("2026-03-08") and vus.max() == T("2026-03-28") and len(vus) == 21
    automne = pd.date_range("2026-10-20", "2026-11-05")
    vus = automne[I.semaines_de_decalage(automne)]
    assert list(vus) == list(pd.date_range("2026-10-25", "2026-10-31"))
    assert not I.semaines_de_decalage(pd.date_range("2026-06-01", "2026-06-30")).any()


def test_mt5_recoit_et_rend_l_heure_du_serveur_comme_si_c_etait_de_l_utc():
    d = I.pour_mt5(T("2026-10-15 22:30"))
    assert d.tzinfo is not None and d.utcoffset().total_seconds() == 0 and (d.hour, d.minute) == (22, 30)
    secondes = int(d.timestamp())
    assert I.depuis_mt5([secondes])[0] == T("2026-10-15 22:30")
    assert I.depuis_mt5([secondes * 1000 + 250], unite="ms")[0] == T("2026-10-15 22:30:00.250")


# -- les fichiers ----------------------------------------------------------------------
def test_les_barres_se_relisent_exactement_et_s_ecrivent_aux_memes_octets(tmp_path):
    b = SY.barres("GER40", SY.seances("2024-01-02", "2024-03-29"), graine=4)
    b["close"][5] = 0.1 + 0.2                               # un cours sans ecriture decimale courte
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    I.ecrire_barres(f1, b)
    d = I.lire_barres(f1)
    assert len(d) == len(b) and list(d.columns) == list(I.COLONNES[1:])
    for c in ("open", "high", "low", "close"):
        assert np.array_equal(d[c].to_numpy(), b[c])        # a l'octet pres, pas a peu pres
    assert (d.index == I.depuis_mt5(b["time"])).all()
    I.ecrire_barres(f2, b)
    assert f1.read_bytes() == f2.read_bytes() and b"\r" not in f1.read_bytes()


def test_des_barres_en_double_ou_dans_le_desordre_sont_refusees(tmp_path):
    b = SY.barres("US500", SY.seances("2024-01-02", "2024-01-10"))
    f = tmp_path / "x.csv"
    I.ecrire_barres(f, np.concatenate([b[:5], b[4:]]))
    with pytest.raises(ValueError):
        I.lire_barres(f)
    I.ecrire_barres(f, b[::-1])
    with pytest.raises(ValueError):
        I.lire_barres(f)


def test_le_manifeste_attrape_un_octet_change(tmp_path):
    brut = tmp_path / "brut"
    brut.mkdir()
    lignes = []
    for n in I.INDICES:
        f = brut / I.nom_fichier(n)
        I.ecrire_barres(f, SY.barres(n, SY.seances("2024-01-02", "2024-01-31")))
        lignes.append({"fichier": f.name, "instrument": n, "symbole": I.SYMBOLES[n], "telecharge_utc": "x",
                       "barres": 1, "premiere_barre": "x", "derniere_barre": "x", "octets": 1,
                       "sha256": TD.sha256(f)})
    I.ecrire_manifeste(lignes, tmp_path / "manifeste.csv")
    assert I.verifier_empreintes(brut, tmp_path / "manifeste.csv") == []
    f = brut / I.nom_fichier("HK50")
    f.write_bytes(f.read_bytes().replace(b"\n1", b"\n2", 1))
    assert I.verifier_empreintes(brut, tmp_path / "manifeste.csv") == [
        "%s : l'empreinte ne correspond plus au manifeste" % f.name]
    assert I.verifier_empreintes(brut, tmp_path / "absent.csv")[0].endswith("n'ont pas ete telechargees")


def test_le_calendrier_vient_des_fichiers_scelles_du_protocole_de_tendance(tmp_path):
    jours = SY.seances("2026-09-01", "2026-11-13")
    tableau = {}
    for n in I.INDICES:
        f = tmp_path / I.fichier_calendrier(n)
        SY.ecrire_yahoo(f, pd.Series(np.linspace(100, 110, len(jours)), index=jours))
        tableau[f.name] = (TD.sha256(f), "retenu")
    assert I.verifier_calendrier(tableau, tmp_path) == []
    cal = I.calendrier(tmp_path)
    assert all(s.index.max() == T("2026-10-30") for s in cal.values())     # jusqu'au 30 octobre 2026
    assert cal["US500"].index.min() == T("2026-09-01")
    tableau["yahoo__HSI.json"] = ("0" * 64, "retenu")
    del tableau["yahoo__N225.json"]
    assert I.verifier_calendrier(tableau, tmp_path) == [
        "yahoo__N225.json : pas d'empreinte dans le protocole de tendance",
        "yahoo__HSI.json : l'empreinte ne correspond plus a celle du protocole de tendance"]


# -- la barre de fenetre -----------------------------------------------------------------
def test_la_barre_de_fenetre_est_celle_de_l_heure_locale_meme_quand_tokyo_change_d_horaire():
    jours = SY.seances("2024-10-21", "2024-11-15")
    b = SY.barres("JP225", jours, graine=2)
    fen = I.barres_de_fenetre(SY.en_dataframe(b), "JP225", jours)
    k = np.arange(len(jours)) * SY.PAR_JOUR + SY.position_fenetre("JP225", jours)
    assert np.array_equal(fen["open"].to_numpy(), b["open"][k])
    assert np.array_equal(fen["close"].to_numpy(), b["close"][k])
    assert np.array_equal(fen["cloture_avant"].to_numpy(), b["close"][k - 1])
    assert np.array_equal(fen["ouverture_apres"].to_numpy(), b["open"][k + 1])
    assert fen.loc["2024-11-04", "debut"] == T("2024-11-04 07:30")        # 14:30 a Tokyo
    assert fen.loc["2024-11-05", "debut"] == T("2024-11-05 08:00")        # 15:00 a Tokyo


def test_une_barre_absente_donne_une_fenetre_vide():
    jours = SY.seances("2024-01-02", "2024-01-31")
    d = SY.en_dataframe(SY.barres("HK50", jours))
    debut = I.debut_fenetre("HK50", [jours[3]])[0]
    fen = I.barres_de_fenetre(d.drop(index=debut), "HK50", jours)
    assert fen["open"].isna().sum() == 1 and np.isnan(fen.loc[jours[3], "close"])


# -- le controle de l'heure du serveur ------------------------------------------------
JOURS_HEURE = SY.seances("2023-01-02", "2024-12-31")


def test_l_heure_du_serveur_de_ftmo_passe_le_controle():
    b = SY.barres("US500", JOURS_HEURE, serveur="new_york")
    h = I.controle_heure(SY.en_dataframe(b), JOURS_HEURE)
    assert h["ok"] and h["part"] == 1.0 and h["decalage"]["part"] == 1.0
    assert h["seances"] == len(JOURS_HEURE) and h["sans_barres"] == 0
    assert 30 <= h["decalage"]["seances"] <= 45                     # 3 a 4 semaines par an
    assert set(h["par_annee"]) == {2023, 2024}


def test_une_heure_du_serveur_au_rythme_europeen_echoue_la_ou_cela_se_voit():
    """Athenes = New York + 7 h, sauf les semaines de decalage : le controle sur
    toutes les seances passerait, celui des semaines de decalage l'attrape."""
    b = SY.barres("US500", JOURS_HEURE, serveur="athenes")
    h = I.controle_heure(SY.en_dataframe(b), JOURS_HEURE)
    assert h["part"] > 0.9                         # a lui seul, ce critere ne verrait rien
    assert h["decalage"]["part"] == 0.0 and not h["ok"]


def test_une_seance_sans_barre_de_9_30_compte_comme_un_echec():
    d = SY.en_dataframe(SY.barres("US500", JOURS_HEURE))
    neuf_trente = I.debut_fenetre("US500", JOURS_HEURE[:300]) - pd.Timedelta(hours=6)    # 9:30 a New York
    h = I.controle_heure(d.drop(index=neuf_trente), JOURS_HEURE)
    assert h["reussies"] == len(JOURS_HEURE) - 300 and not h["ok"]


def test_un_volume_record_hors_ouverture_fait_echouer_la_seance():
    b = SY.barres("US500", JOURS_HEURE)
    d = SY.en_dataframe(b)
    huit_trente = I.debut_fenetre("US500", JOURS_HEURE[::5]) - pd.Timedelta(hours=7)     # 8:30 a New York
    d.loc[huit_trente, "tick_volume"] = 10_000
    h = I.controle_heure(d, JOURS_HEURE)
    assert h["part"] == pytest.approx(0.8, abs=0.01) and not h["ok"]


# -- les controles d'un indice -----------------------------------------------------------
JOURS = SY.seances("2017-12-28", "2019-06-28")


def _indice(indice="GER40", graine=7):
    b = SY.barres(indice, JOURS, graine=graine)
    return b, SY.clotures_yahoo(indice, JOURS, b)


def _k(indice, jour):
    """La position de la barre de fenetre de `jour` dans le tableau des barres."""
    i = JOURS.get_loc(T(jour))
    return i * SY.PAR_JOUR + SY.position_fenetre(indice, JOURS[i:i + 1])[0]


def test_des_barres_saines_sont_retenues():
    b, y = _indice()
    c = I.controler_indice("GER40", SY.en_dataframe(b), y)
    assert c["retenu"] and c["raisons"] == [] and c["manquantes"] == [] and c["figees"] == []
    assert c["sauts"] == []
    assert c["debut_juge"] == T("2018-04-01") and c["seances_jugees"] == len(JOURS[JOURS >= "2018-04-01"])
    assert c["yahoo"]["correlation"] > 0.99 and c["yahoo"]["ecarts_1pc"] == 0


def test_les_seances_jugees_commencent_a_la_premiere_barre_si_elle_est_apres_avril_2018():
    b, y = _indice("AUS200")
    debut = int((T("2018-11-01") - SY.EPOQUE).total_seconds())
    c = I.controler_indice("AUS200", SY.en_dataframe(b[b["time"] >= debut]), y)
    assert c["debut_juge"] == T("2018-11-01") and c["manquantes"] == [] and c["retenu"]


@pytest.mark.parametrize("part, retenu", [(0.04, True), (0.06, False)])
def test_plus_de_5_pc_de_barres_de_fenetre_manquantes_excluent_l_indice(part, retenu):
    b, y = _indice()
    d = SY.en_dataframe(b)
    jours = JOURS[JOURS >= "2018-04-01"]
    otees = jours[np.linspace(0, len(jours) - 1, int(round(part * len(jours)))).astype(int)]
    c = I.controler_indice("GER40", d.drop(index=I.debut_fenetre("GER40", otees)), y)
    assert len(c["manquantes"]) == len(otees) and c["retenu"] is retenu
    if not retenu:
        assert c["raisons"][0][0] == "manquantes"


def test_des_barres_figees_au_dela_de_5_pc_sont_un_defaut():
    b, y = _indice()
    jours = JOURS[JOURS >= "2018-04-01"]
    for j in jours[::15]:                                          # 6,7 %
        k = _k("GER40", j)
        b["high"][k] = b["low"][k] = b["open"][k] = b["close"][k] = b["close"][k - 1]
    c = I.controler_indice("GER40", SY.en_dataframe(b), SY.clotures_yahoo("GER40", JOURS, b))
    assert len(c["figees"]) == len(jours[::15]) and ("figees", pytest.approx(c["part_figees"])) in c["raisons"]


def test_un_mauvais_cours_de_sortie_est_un_defaut_non_resolu():
    b, y = _indice()
    k = _k("GER40", "2018-09-12")
    b["close"][k] *= 1.4                                           # un cours aberrant, Yahoo ne bouge pas
    b["high"][k] = b["close"][k]
    c = I.controler_indice("GER40", SY.en_dataframe(b), y)
    assert not c["retenu"]
    non = [s for s in c["sauts"] if not s["resolu"]]
    assert {(s["type"], s["date"]) for s in non} >= {("seance", T("2018-09-12")), ("seance", T("2018-09-13"))}


def test_un_vrai_krach_confirme_par_yahoo_est_garde():
    b, _ = _indice()
    debut = b["time"][JOURS.get_loc(T("2018-10-10")) * SY.PAR_JOUR]          # la premiere barre du jour
    for c in ("open", "high", "low", "close"):
        b[c][b["time"] >= debut] *= 0.7                            # -30 % pendant la nuit
    y = SY.clotures_yahoo("GER40", JOURS, b)                       # que Yahoo voit aussi
    c = I.controler_indice("GER40", SY.en_dataframe(b), y)
    assert c["retenu"]
    s = [s for s in c["sauts"] if s["type"] == "seance"]
    assert len(s) == 1 and s[0]["date"] == T("2018-10-10") and s[0]["resolu"]


def test_un_grand_mouvement_reel_dans_la_fenetre_est_corrobore():
    b, _ = _indice()
    k = _k("GER40", "2019-02-14")
    b["close"][k] *= 0.95
    b["low"][k] = min(b["low"][k], b["close"][k])
    for c in ("open", "high", "low", "close"):
        b[c][k + 1:] *= 0.95                                       # le cours reste la : un vrai mouvement
    c = I.controler_indice("GER40", SY.en_dataframe(b), SY.clotures_yahoo("GER40", JOURS, b))
    f = [s for s in c["sauts"] if s["type"] == "fenetre"]
    assert len(f) == 1 and f[0]["date"] == T("2019-02-14") and f[0]["resolu"] and c["retenu"]


def test_un_mauvais_cours_d_entree_n_est_pas_corrobore_par_la_barre_d_avant():
    b, y = _indice()
    k = _k("GER40", "2019-02-14")
    b["open"][k] *= 1.05                                           # E(t) aberrant, C(t) juste
    b["high"][k] = b["open"][k]
    c = I.controler_indice("GER40", SY.en_dataframe(b), y)
    f = [s for s in c["sauts"] if s["type"] == "fenetre"]
    assert len(f) == 1 and not f[0]["resolu"] and f[0]["continuite"] > 0.04 and not c["retenu"]
