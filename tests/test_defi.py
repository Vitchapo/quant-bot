"""Garde-fous de perte au format prop firm."""
from __future__ import annotations

import pytest

from quantbot.defi import (PERTE_JOUR_DEFAUT, evaluer, lire_etat, parametres,
                           resume, ecrire_etat)

P = {"perte_jour_max": 0.04, "perte_totale_max": 0.08, "objectif": 0.10,
     "reference": "statique"}


class TestEvaluer:
    def test_compte_sain(self):
        v = evaluer(101000, 100000, P, {"capital_depart": 100000, "plus_haut": 101000})
        assert v["ok"] and not v["raison"]

    def test_perte_du_jour_bloque(self):
        v = evaluer(95500, 100000, P, {"capital_depart": 100000, "plus_haut": 100000})
        assert not v["ok"] and "perte du jour" in v["raison"]

    def test_juste_sous_la_limite_journaliere_passe(self):
        v = evaluer(96500, 100000, P, {"capital_depart": 100000, "plus_haut": 100000})
        assert v["ok"], v["raison"]

    def test_perte_depuis_le_depart_bloque(self):
        """Regle STATIQUE, celle du defi vise : la limite est a -8 % du solde
        de depart, quel que soit le plus haut atteint entre-temps."""
        etat = {"capital_depart": 100000, "plus_haut": 110000}
        v = evaluer(91500, 92000, P, etat)
        assert not v["ok"] and "le depart" in v["raison"]

    def test_le_plus_haut_monte_et_ne_redescend_jamais(self):
        etat = {"capital_depart": 100000, "plus_haut": 100000}
        v = evaluer(105000, 104000, P, etat)
        assert v["etat"]["plus_haut"] == 105000
        v2 = evaluer(102000, 105000, P, v["etat"])
        assert v2["etat"]["plus_haut"] == 105000, "le plus-haut a recule"

    def test_premier_passage_initialise_le_depart(self):
        v = evaluer(100000, 100000, P, {})
        assert v["etat"]["capital_depart"] == 100000
        assert v["etat"]["plus_haut"] == 100000

    def test_objectif_atteint(self):
        v = evaluer(110500, 110000, P, {"capital_depart": 100000, "plus_haut": 110500})
        assert v["objectif_atteint"] and v["ok"]

    def test_veille_absente_ne_divise_pas_par_zero(self):
        v = evaluer(100000, 0, P, {"capital_depart": 100000, "plus_haut": 100000})
        assert v["ok"] and v["perte_jour"] == 0.0

    def test_compte_vide(self):
        v = evaluer(0, 0, P, {})
        assert v["ok"]

    def test_resume_lisible(self):
        v = evaluer(98000, 100000, P, {"capital_depart": 100000, "plus_haut": 102000})
        t = resume(v, P)
        assert "jour" in t and "depuis" in t and "progression" in t


class TestEtatSurDisque:
    def test_aller_retour(self, tmp_path):
        chemin = tmp_path / "defi.json"
        assert lire_etat(chemin) == {}
        ecrire_etat({"capital_depart": 100000, "plus_haut": 101000}, chemin)
        assert lire_etat(chemin)["plus_haut"] == 101000

    def test_fichier_corrompu_ne_leve_pas(self, tmp_path):
        chemin = tmp_path / "defi.json"
        chemin.write_text("{ ceci n'est pas du json", encoding="utf-8")
        assert lire_etat(chemin) == {}


class TestConfiguration:
    def test_inactif_par_defaut(self, base_config):
        assert base_config.get("defi.active") is False
        assert parametres(base_config) is None

    def test_actif(self, base_config):
        cfg = base_config.copy()
        cfg.set("defi.active", True, strict=True)
        p = parametres(cfg)
        assert p["perte_jour_max"] == PERTE_JOUR_DEFAUT
        assert p["perte_totale_max"] == 0.08

    def test_les_seuils_laissent_une_marge_sous_le_contrat(self, base_config):
        """Un garde-fou qui se declenche pile a la limite du contrat ne sert a
        rien : entre la decision et l'execution, le marche bouge."""
        cfg = base_config.copy()
        cfg.set("defi.active", True, strict=True)
        p = parametres(cfg)
        assert p["perte_jour_max"] < 0.05
        assert p["perte_totale_max"] < 0.10


class TestReferenceStatiqueOuGlissante:
    """La difference la plus lourde de consequences du module.

    Statique  : limite fixe a -10 % du DEPART. Les gains sont un matelas.
    Glissante : elle suit le plus haut. Gagner 8 % puis rendre 10 % elimine,
                meme en etant encore au-dessus du depart.

    Le defi vise annonce "Static". Lui appliquer la regle glissante
    arreterait le bot sans aucune raison contractuelle.
    """

    ETAT = {"capital_depart": 100000, "plus_haut": 110000}

    def test_statique_tolere_un_recul_depuis_le_plus_haut(self):
        p = dict(P, reference="statique")
        v = evaluer(101000, 101500, p, self.ETAT)      # -8 % du plus haut, +1 % du depart
        assert v["ok"], v["raison"]

    def test_glissante_l_interdit(self):
        p = dict(P, reference="glissante")
        v = evaluer(101000, 101500, p, self.ETAT)
        assert not v["ok"] and "plus haut" in v["raison"]

    def test_statique_bloque_sous_le_depart(self):
        p = dict(P, reference="statique")
        v = evaluer(91500, 92000, p, self.ETAT)
        assert not v["ok"] and "le depart" in v["raison"]

    def test_le_defaut_est_statique(self, base_config):
        cfg = base_config.copy()
        cfg.set("defi.active", True, strict=True)
        assert parametres(cfg)["reference"] == "statique"
