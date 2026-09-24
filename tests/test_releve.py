"""Le releve quotidien : la bande de bruit, et l'interdiction de lire un
chiffre seul.

Ces fonctions sont pures a dessein. Elles encodent la seule chose qui a
reellement fait derailler ce projet - lire un rendement sans son incertitude -
et doivent donc etre verifiables sans compte ni reseau.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import releve_quotidien as rq  # noqa: E402


class TestBandeDeBruit:
    def test_elle_grandit_en_racine_du_temps(self):
        """Doubler la duree multiplie la bande par racine de 2, pas par 2."""
        b1, b2 = rq.bande_de_bruit(30), rq.bande_de_bruit(60)
        assert b2 / b1 == pytest.approx(2 ** 0.5, rel=1e-6)

    def test_les_valeurs_de_reference(self):
        """Les chiffres cites dans les deux protocoles. S'ils changent, les
        documents deviennent faux."""
        assert 100 * rq.bande_de_bruit(1) == pytest.approx(1.57, abs=0.02)
        assert 100 * rq.bande_de_bruit(5) == pytest.approx(3.52, abs=0.02)
        assert 100 * rq.bande_de_bruit(30) == pytest.approx(8.63, abs=0.02)
        assert 100 * rq.bande_de_bruit(126) == pytest.approx(17.7, abs=0.1)

    def test_a_zero_seance_la_bande_est_nulle(self):
        assert rq.bande_de_bruit(0) == 0.0


class TestVerdict:
    def test_LE_CAS_du_21_septembre(self):
        """+0,96 %% sur une seance : dans le bruit. C'est le chiffre exact qui
        a fait conclure que le defi passerait en deux mois."""
        v = rq.verdict(0.0096, 1)
        assert v["dans_le_bruit"] is True
        assert "ne dit rien" in v["texte"]

    def test_un_gros_rendement_tot_sort_de_la_bande(self):
        assert rq.verdict(0.09, 30)["dans_le_bruit"] is False

    def test_un_rendement_moyen_a_six_semaines_reste_du_bruit(self):
        """+4 %% a six semaines : beaucoup de gens y liraient un succes."""
        assert rq.verdict(0.04, 30)["dans_le_bruit"] is True

    def test_la_bande_est_symetrique(self):
        assert rq.verdict(-0.04, 30)["dans_le_bruit"] is True

    def test_le_texte_contient_TOUJOURS_la_bande(self):
        """Le coeur du dispositif : le rendement ne doit jamais s'afficher
        sans son incertitude dans le meme champ de vision."""
        for r, n in ((0.0096, 1), (0.05, 10), (-0.03, 20), (0.20, 40)):
            assert "bande de bruit" in rq.verdict(r, n)["texte"]


class TestSeances:
    def test_une_semaine_calendaire_fait_cinq_seances(self):
        assert rq.seances_ecoulees("2026-09-21", "2026-09-28") == 5

    def test_le_week_end_ne_compte_pas(self):
        # vendredi -> lundi
        assert rq.seances_ecoulees("2026-09-18", "2026-09-21") == 1

    def test_la_periode_du_gel(self):
        """22 septembre -> 2 novembre : les ~30 seances annoncees."""
        n = rq.seances_ecoulees("2026-09-22", "2026-11-02")
        assert 28 <= n <= 32, n


class TestEcritureEtRelecture:
    def test_aller_retour(self, tmp_path):
        f = tmp_path / "r.csv"
        rq.ecrire({"date": "2026-09-22", "equity": 101000, "rendement": 0.01,
                   "seances": 2, "capital_depart": 100000,
                   "dans_le_bruit": True}, f)
        lu = rq.lire(f)
        assert len(lu) == 1 and lu[0]["date"] == "2026-09-22"

    def test_les_lignes_s_accumulent(self, tmp_path):
        f = tmp_path / "r.csv"
        for i in range(3):
            rq.ecrire({"date": "2026-09-2%d" % i, "equity": 100000 + i}, f)
        assert len(rq.lire(f)) == 3

    def test_un_fichier_absent_renvoie_une_liste_vide(self, tmp_path):
        assert rq.lire(tmp_path / "rien.csv") == []


class TestLesProtocolesExistent:
    """Ces documents sont des engagements pris avant de connaitre le resultat.
    Un test qui verifie leur presence empeche de les perdre dans un
    reorganisation de dossier."""

    def _doc(self, nom):
        return Path(__file__).resolve().parent.parent / "docs" / nom

    def test_le_protocole_du_gel(self):
        t = self._doc("protocole_paper_2026-11-02.md").read_text(encoding="utf-8")
        assert "8,6 %" in t, "la bande a six semaines doit y figurer"
        assert "n'est pas un critère" in t

    def test_le_protocole_du_defi(self):
        t = self._doc("protocole_defi_ftmo.md").read_text(encoding="utf-8")
        assert "0 sur 5" in t
        for critere in ("IC du composite", "déciles", "score inversé",
                        "null aléatoire", "survivant"):
            assert critere in t, critere

    def test_les_chiffres_des_protocoles_viennent_du_code(self):
        """Si `bande_de_bruit` change, les documents mentent. Ce test lie les
        deux."""
        t = self._doc("protocole_defi_ftmo.md").read_text(encoding="utf-8")
        assert "%.1f" % (100 * rq.bande_de_bruit(30)) in t.replace(",", ".")
