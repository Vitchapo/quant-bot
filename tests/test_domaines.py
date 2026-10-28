"""Le domaine d'activite des lignes : un classement GICS regroupe, jamais devine."""
from __future__ import annotations

import threading
import time

import pytest

from quantbot import domaines

# Extrait de la table des constituants du S&P 500 (Wikipedia, octobre 2026).
GICS = {
    "VLO": ("Energy", "Oil & Gas Refining & Marketing"),
    "AMD": ("Information Technology", "Semiconductors"),
    "TER": ("Information Technology", "Semiconductor Materials & Equipment"),
    "PANW": ("Information Technology", "Systems Software"),
    "DELL": ("Information Technology", "Technology Hardware, Storage & Peripherals"),
    "LITE": ("Information Technology", "Communications Equipment"),
    "BE": ("Industrials", "Electrical Components & Equipment"),
    "CNC": ("Health Care", "Managed Health Care"),
    "ADM": ("Consumer Staples", "Agricultural Products & Services"),
    "CF": ("Materials", "Fertilizers & Agricultural Chemicals"),
    "DE": ("Industrials", "Agricultural & Farm Machinery"),
    "KO": ("Consumer Staples", "Soft Drinks & Non-alcoholic Beverages"),
    "PM": ("Consumer Staples", "Tobacco"),
    "PG": ("Consumer Staples", "Household Products"),
    "BRK-B": ("Financials", "Multi-Sector Holdings"),
}


@pytest.mark.parametrize("ticker, attendu", [
    ("VLO", "Energie"),
    ("AMD", "Semi-conducteurs"),
    ("TER", "Semi-conducteurs"),          # les machines a puces vont avec les puces
    ("PANW", "Logiciels & services"),
    ("DELL", "Materiel & reseaux"),
    ("LITE", "Materiel & reseaux"),
    ("BE", "Industrie"),
    ("CNC", "Sante"),
    ("ADM", "Agroalimentaire"),
    ("CF", "Agroalimentaire"),            # engrais : Materials en GICS
    ("DE", "Agroalimentaire"),            # machines agricoles : Industrials en GICS
    ("KO", "Agroalimentaire"),
    ("PM", "Agroalimentaire"),
    ("PG", "Consommation de base"),
    ("BRK-B", "Finance"),
])
def test_domaine(ticker, attendu):
    assert domaines.domaine(*GICS[ticker]) == attendu


def test_la_tech_est_une_seule_famille():
    for nom in domaines.MEMBRES_TECH:
        assert domaines.famille(nom) == domaines.TECH
    assert domaines.famille("Energie") == "Energie"


def test_un_secteur_inconnu_n_est_jamais_devine():
    assert domaines.domaine("", "") == domaines.NON_CLASSE
    assert domaines.domaine("Secteur invente", "Quelque chose") == domaines.NON_CLASSE


def test_tous_les_domaines_sont_dans_l_ordre_d_affichage():
    for s in domaines._SECTEURS.values():
        assert s in domaines.DOMAINES
    for s in domaines.MEMBRES_TECH + ("Agroalimentaire",):
        assert s in domaines.DOMAINES


class Horloge:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class Telechargeur:
    def __init__(self, table=None, erreur=None):
        self.table, self.erreur, self.appels = dict(table or GICS), erreur, 0

    def __call__(self):
        self.appels += 1
        if self.erreur:
            raise self.erreur
        return dict(self.table)


def _page_wikipedia(n):
    lignes = ["<tr><td>%s</td><td>Societe %d</td><td>Energy</td><td>Oil &amp; Gas Refining &amp; Marketing</td></tr>"
              % ("T%03d" % i if i else "BRK.B", i) for i in range(n)]
    return ("<html><body><table><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>"
            "<th>GICS Sub-Industry</th></tr>" + "".join(lignes) + "</table></body></html>")


def test_la_table_de_wikipedia_se_lit():
    pytest.importorskip("lxml")
    table = domaines.lire_table_gics(_page_wikipedia(450))
    assert len(table) == 450
    assert table["BRK-B"] == ("Energy", "Oil & Gas Refining & Marketing")


def test_une_table_tronquee_est_refusee():
    pytest.importorskip("lxml")
    with pytest.raises(ValueError):
        domaines.lire_table_gics(_page_wikipedia(30))


def _classement(tmp_path, tele, horloge=None):
    return domaines.Classement(tmp_path / "sp500_secteurs.csv", telecharger=tele,
                               horloge=horloge or Horloge(), fond=False)


class TestClassement:
    def test_premier_appel_telecharge_et_ecrit_le_cache(self, tmp_path):
        tele = Telechargeur()
        r = _classement(tmp_path, tele).pour(["VLO", "AMD"])
        assert tele.appels == 1
        assert r["titres"]["VLO"]["domaine"] == "Energie"
        assert r["titres"]["AMD"]["famille"] == domaines.TECH
        assert r["titres"]["AMD"]["sous_industrie"] == "Semiconductors"
        assert r["inconnus"] == [] and r["erreur"] is None
        assert domaines.lire_cache(tmp_path / "sp500_secteurs.csv")["VLO"] == GICS["VLO"]

    def test_un_cache_frais_suffit(self, tmp_path):
        _classement(tmp_path, Telechargeur()).pour(["VLO"])
        tele = Telechargeur(erreur=AssertionError("Wikipedia ne doit pas etre appelee"))
        r = _classement(tmp_path, tele).pour(["VLO", "PANW"])
        assert tele.appels == 0
        assert r["titres"]["PANW"]["domaine"] == "Logiciels & services"

    def test_un_titre_sorti_de_l_indice_garde_son_classement(self, tmp_path):
        horloge = Horloge()
        c = _classement(tmp_path, Telechargeur(), horloge)
        c.pour(["CNC"])
        sans_cnc = {t: v for t, v in GICS.items() if t != "CNC"}
        c._telecharger = Telechargeur(sans_cnc)
        horloge.t += domaines.DUREE_VIE + 1           # cache perime : on retelecharge
        r = c.pour(["CNC"])
        assert c._telecharger.appels == 1
        assert r["titres"]["CNC"]["domaine"] == "Sante"

    def test_wikipedia_injoignable_ni_exception_ni_harcelement(self, tmp_path):
        horloge = Horloge()
        tele = Telechargeur(erreur=ConnectionError("pas de reseau"))
        c = _classement(tmp_path, tele, horloge)
        r = c.pour(["VLO"])
        assert r["ok"] and r["titres"] == {} and r["inconnus"] == ["VLO"]
        assert "pas de reseau" in r["erreur"]
        c.pour(["VLO"])
        assert tele.appels == 1                        # pas de nouvel essai avant une heure
        horloge.t += domaines.ENTRE_DEUX_ESSAIS
        c.pour(["VLO"])
        assert tele.appels == 2

    def test_un_titre_inconnu_relance_le_telechargement_une_fois_par_heure(self, tmp_path):
        horloge = Horloge()
        tele = Telechargeur()
        c = _classement(tmp_path, tele, horloge)
        r = c.pour(["VLO", "ZZZZ"])
        assert r["inconnus"] == ["ZZZZ"] and tele.appels == 1
        c.pour(["ZZZZ"])
        assert tele.appels == 1
        horloge.t += domaines.ENTRE_DEUX_ESSAIS
        c.pour(["ZZZZ"])
        assert tele.appels == 2

    def test_point_ou_tiret_meme_titre(self, tmp_path):
        r = _classement(tmp_path, Telechargeur()).pour(["BRK.B"])
        assert r["titres"]["BRK.B"]["domaine"] == "Finance"

    def test_cache_illisible_on_retelecharge(self, tmp_path):
        (tmp_path / "sp500_secteurs.csv").write_bytes(b"\xff\xfe\x00 pas un csv")
        tele = Telechargeur()
        r = _classement(tmp_path, tele).pour(["VLO"])
        assert tele.appels == 1 and r["titres"]["VLO"]["domaine"] == "Energie"

    def test_la_reponse_n_attend_pas_wikipedia(self, tmp_path):
        """En tache de fond, `pour()` rend la main tout de suite, `en_cours` vrai."""
        feu = threading.Event()
        fini = threading.Event()

        def lent():
            feu.wait(5)
            fini.set()
            return dict(GICS)

        c = domaines.Classement(tmp_path / "sp500_secteurs.csv", telecharger=lent)
        debut = time.time()
        r = c.pour(["VLO"])
        assert time.time() - debut < 1.0
        assert r["en_cours"] and r["inconnus"] == ["VLO"]
        feu.set()
        assert fini.wait(5)
        for _ in range(100):                           # le fil finit d'enregistrer
            r = c.pour(["VLO"])
            if not r["en_cours"]:
                break
            time.sleep(0.01)
        assert r["titres"]["VLO"]["domaine"] == "Energie"
