"""La sonde de l'historique Dukascopy, sur un serveur simule.

Aucune vraie donnee ici. Les fichiers sont fabriques au format de Dukascopy
(bougies de 24 octets, compressees en LZMA). On verifie que la sonde compte
les minutes actives au lieu de se contenter qu'un fichier existe, qu'elle
regarde la fenetre et l'ouverture a la bonne heure UTC, qu'elle trouve le
premier jour de l'historique, qu'elle s'arrete si Dukascopy refuse, et
qu'aucun cours ne sort.
"""
from __future__ import annotations

import csv
import io
import lzma
import sys
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sonde_dukascopy as S  # noqa: E402
from quantbot import intraday_donnees as I  # noqa: E402

T = pd.Timestamp
COURS = 4242424                                     # un cours reconnaissable : il ne doit sortir nulle part


def fichier(volumes, debut=0, pas=60) -> bytes:
    """Un fichier de bougies au format de Dukascopy."""
    b = np.zeros(len(volumes), dtype=S.BOUGIE)
    b["t"] = debut + pas * np.arange(len(volumes))
    b["o"] = b["c"] = b["l"] = b["h"] = COURS
    b["v"] = volumes
    return lzma.compress(b.tobytes(), format=lzma.FORMAT_ALONE)


class Reponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Serveur:
    """Un Dukascopy simule. `volume(code, minutes UTC du jour)` -> volumes des minutes ;
    `depuis[code]` : premier jour UTC avec des minutes ; `refus` : code HTTP rendu a tout."""

    def __init__(self, volume, depuis=None, refus=None, pannes=0):
        self.volume, self.depuis, self.refus, self.pannes = volume, depuis or {}, refus, pannes
        self.urls = []

    def __call__(self, requete, timeout=None):
        url = requete.full_url
        self.urls.append(url)
        assert requete.get_header("User-agent") == S.UA
        if self.refus:
            raise urllib.error.HTTPError(url, self.refus, "Forbidden", {}, None)
        if self.pannes:
            self.pannes -= 1
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
        morceaux = url[len(S.BASE):].split("/")
        code, an = morceaux[0], int(morceaux[1])
        depuis = T(self.depuis.get(code, "2000-01-01"))
        if morceaux[-1] == "BID_candles_day_1.bi5":
            jours = pd.bdate_range("%d-01-01" % an, "%d-12-31" % an)
            return Reponse(fichier((jours >= depuis).astype(float) * 1000.0, pas=86400))
        jour = T(an, int(morceaux[2]) + 1, int(morceaux[3]))
        if jour < depuis or jour.dayofweek >= 5:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        minutes = jour + pd.to_timedelta(np.arange(1440), unit="min")
        return Reponse(fichier(self.volume(code, minutes)))


def volume_normal(code, minutes):
    """Un marche qui cote toute la journee, avec un pic a l'ouverture du marche au comptant."""
    n = {c: k for k, v in S.CODES.items() for c in v}[code]
    v = np.full(len(minutes), 100.0)
    for j in (minutes[0], minutes[0] + pd.Timedelta(days=1)):     # l'ouverture locale peut tomber la veille en UTC
        o = S.en_utc(n, j, S.OUVERTURES[n])
        v[np.asarray((minutes >= o) & (minutes < o + pd.Timedelta(minutes=30)))] = 300.0
    return v


def _source(serveur):
    return S.Source(ouvrir=serveur, attendre=lambda _: None)


# -- les fichiers ---------------------------------------------------------------------
def test_les_adresses_de_dukascopy_comptent_les_mois_a_partir_de_zero():
    assert S.url_jour("USA500IDXUSD", "2024-01-05") == S.BASE + "USA500IDXUSD/2024/00/05/BID_candles_min_1.bi5"
    assert S.url_jour("HKGIDXHKD", "2019-12-31") == S.BASE + "HKGIDXHKD/2019/11/31/BID_candles_min_1.bi5"
    assert S.url_annee("DEUIDXEUR", 2015) == S.BASE + "DEUIDXEUR/2015/BID_candles_day_1.bi5"


def test_les_bougies_ne_rendent_que_les_heures_et_les_volumes():
    t, v = S.lire_bougies(fichier([0.0, 2.5, 0.0], debut=120))
    assert list(t) == [120, 180, 240] and list(v) == [0.0, 2.5, 0.0]
    assert S.lire_bougies(b"")[0].size == 0
    with pytest.raises(ValueError):
        S.lire_bougies(lzma.compress(b"x" * 25, format=lzma.FORMAT_ALONE))


def test_un_fichier_absent_une_panne_et_un_refus():
    serveur = Serveur(volume_normal, pannes=2)
    # 503 deux fois, puis le fichier
    assert S.telecharger(S.url_jour("DEUIDXEUR", "2024-01-03"), serveur, lambda _: None)
    assert S.telecharger(S.url_jour("DEUIDXEUR", "2024-01-06"), serveur, lambda _: None) is None  # samedi : 404
    with pytest.raises(urllib.error.HTTPError):
        S.telecharger(S.url_jour("DEUIDXEUR", "2024-01-03"), Serveur(volume_normal, refus=403), lambda _: None)


# -- les mesures d'une seance -----------------------------------------------------------
def test_la_fenetre_et_l_ouverture_sont_lues_a_la_bonne_heure_utc():
    assert S.fenetre_utc("US500", "2024-07-10") == (T("2024-07-10 19:30"), T("2024-07-10 20:00"))
    assert S.fenetre_utc("US500", "2024-01-10") == (T("2024-01-10 20:30"), T("2024-01-10 21:00"))
    assert S.fenetre_utc("JP225", "2024-11-05")[0] == T("2024-11-05 06:00")      # 15:00 a Tokyo
    assert S.en_utc("AUS200", "2024-01-10", "10:00") == T("2024-01-09 23:00")     # la veille en UTC
    # le 13 mars 2024, New York est a l'heure d'ete, pas l'Europe
    m = S.mesurer(_source(Serveur(volume_normal)), "US500", T("2024-03-13"))
    assert m["avec_minutes"] and m["actives"] == 1440 and m["fenetre_actives"] == 30 and m["fenetre_complete"]
    assert m["rapport_ouverture"] == pytest.approx(3.0)
    assert m["rapport_avant"] == pytest.approx(1.0) and m["rapport_apres"] == pytest.approx(1.0)
    assert S.pic([m]) == "oui"


def test_un_pic_une_heure_trop_tard_fait_echouer_le_controle_d_heure():
    """Des bougies decalees d'une heure : le pic n'est plus a l'ouverture."""
    def decale(code, minutes):
        return volume_normal(code, minutes - pd.Timedelta(hours=1))
    m = S.mesurer(_source(Serveur(decale)), "GER40", T("2024-01-10"))
    assert m["rapport_apres"] == pytest.approx(3.0) and m["rapport_ouverture"] == pytest.approx(1.0)
    assert S.pic([m]) == "non"


def test_une_ouverture_a_cheval_sur_minuit_utc_lit_le_fichier_de_la_veille():
    serveur = Serveur(volume_normal)
    m = S.mesurer(_source(serveur), "AUS200", T("2024-01-10"))
    assert m["rapport_ouverture"] == pytest.approx(3.0)
    assert S.url_jour("AUSIDXAUD", "2024-01-09") in serveur.urls


def test_un_fichier_present_mais_vide_de_minutes_actives_ne_compte_pas():
    """La lecon de la sonde FTMO : un fichier qui existe n'est pas un historique."""
    def quotidien_seulement(code, minutes):
        v = np.zeros(len(minutes))
        v[0] = 1000.0
        return v
    m = S.mesurer(_source(Serveur(quotidien_seulement)), "GER40", T("2024-01-10"))
    assert m["avec_minutes"] and m["actives"] == 1 and m["fenetre_actives"] == 0 and not m["fenetre_complete"]


def test_une_fenetre_sans_derniere_minute_n_est_pas_complete():
    def trou(code, minutes):
        return np.where((minutes.hour == 16) & (minutes.minute == 29), 0.0, 100.0)   # 17:29 a Francfort, en hiver
    m = S.mesurer(_source(Serveur(trou)), "GER40", T("2024-01-10"))
    assert m["fenetre_actives"] == 29 and not m["fenetre_complete"]


def test_une_seance_par_mois_la_plus_proche_du_15():
    jours = pd.bdate_range("2024-01-01", "2024-12-31").drop([T("2024-05-15")])
    s = S.seances_sondees(jours, 2024)
    assert len(s) == 12 and s[0] == T("2024-01-15") and s[4] in (T("2024-05-14"), T("2024-05-16"))


# -- la sonde complete -----------------------------------------------------------------
def _calendrier():
    j = pd.bdate_range("2009-01-01", "2026-11-13")
    return {n: pd.Series(100.0, index=j) for n in I.INDICES}


def test_la_sonde_trouve_le_premier_jour_et_ne_sort_aucun_cours(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "ANNEES", (2011, 2012, 2013, 2014, 2015, 2016, 2026))
    depuis = {"USA500IDXUSD": "2012-06-07", "HKGIDXHKD": "2015-02-10", "AUDIDXAUD": "2099-01-01"}
    serveur = Serveur(volume_normal, depuis=depuis)
    vus = []
    lignes = S.sonder(_source(serveur), _calendrier(), maintenant=T("2026-11-19", tz="UTC"), dire=vus.append)
    assert len(lignes) == 7 * len(S.ANNEES)                         # l'Australie sous ses deux noms
    us = {l["annee"]: l for l in lignes if l["instrument"] == "US500"}
    assert us[2011]["avec_minutes"] == 0 and us[2011]["rapport_ouverture_median"] == ""
    assert us[2013]["avec_minutes"] == 12 and us[2013]["fenetres_completes"] == 12
    assert us[2012]["premier_jour_minutes"] == "2012-06-07"
    assert us[2013]["minutes_actives_mediane"] == 1440 and us[2013]["rapport_ouverture_median"] == "3.00"
    assert us[2013]["rapport_une_heure_avant_median"] == "1.00" and us[2013]["pic_a_l_ouverture"] == "oui"
    au = {(l["code"], l["annee"]): l for l in lignes if l["instrument"] == "AUS200"}
    assert au[("AUSIDXAUD", 2013)]["avec_minutes"] == 12 and au[("AUDIDXAUD", 2013)]["avec_minutes"] == 0
    hk = [l for l in lignes if l["instrument"] == "HK50"]
    assert hk[0]["premier_jour_minutes"] == "2015-02-10"
    assert us[2026]["seances_sondees"] == 10                        # le calendrier s'arrete au 30 octobre
    assert "    premier jour avec des minutes : 2012-06-07" in vus
    f = tmp_path / "sonde.csv"
    S.ecrire(lignes, f)
    with f.open(encoding="utf-8", newline="") as fh:
        relu = list(csv.DictReader(fh))
    assert list(relu[0]) == S.COLONNES
    assert str(COURS) not in f.read_text(encoding="utf-8") and not any(str(COURS) in v for v in vus)


def test_une_page_web_a_la_place_d_un_fichier_arrete_la_sonde_en_disant_laquelle(monkeypatch, capsys):
    class Page(Serveur):
        def __call__(self, requete, timeout=None):
            self.urls.append(requete.full_url)
            return Reponse(b"<html>pas un fichier</html>")
    serveur, vraie = Page(volume_normal), S.Source
    monkeypatch.setattr(S, "Source", lambda: vraie(ouvrir=serveur, attendre=lambda _: None))
    monkeypatch.setattr(S.I, "calendrier", lambda dossier: _calendrier())
    assert S.main([]) == 2
    sortie = capsys.readouterr().out
    assert "fichier illisible" in sortie and "BID_candles_" in sortie


def test_si_dukascopy_refuse_la_sonde_s_arrete_sans_forcer(monkeypatch, capsys):
    serveur, vraie = Serveur(volume_normal, refus=403), S.Source
    monkeypatch.setattr(S, "Source", lambda: vraie(ouvrir=serveur, attendre=lambda _: None))
    monkeypatch.setattr(S.I, "calendrier", lambda dossier: _calendrier())
    assert S.main([]) == 2
    assert "on ne force pas" in capsys.readouterr().out
    assert len(serveur.urls) == 1
