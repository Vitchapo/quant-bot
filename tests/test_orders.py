"""Passage du portefeuille cible aux ordres.

Ce module est celui qui, un jour, decidera d'envoyer de l'argent quelque part.
Il est volontairement sans reseau, pour etre testable ligne a ligne.
"""
from __future__ import annotations

import pytest

from quantbot.orders import ACHAT, VENTE, planifier, resume, sans_cours

COURS = {"AAA": 100.0, "BBB": 50.0, "CCC": 25.0}


class TestPlanification:
    def test_ouvre_une_ligne_absente(self):
        o = planifier({"AAA": 0.5}, {}, COURS, 10_000)
        assert len(o) == 1
        assert (o[0].ticker, o[0].sens, o[0].motif) == ("AAA", ACHAT, "entree")
        assert o[0].montant == pytest.approx(5000.0)

    def test_solde_une_ligne_qui_n_est_plus_visee(self):
        o = planifier({}, {"AAA": 10}, COURS, 10_000)
        assert (o[0].sens, o[0].motif) == (VENTE, "sortie")
        assert o[0].quantite == pytest.approx(10.0), "on solde EXACTEMENT ce qui est detenu"

    def test_une_sortie_s_execute_meme_sous_le_seuil(self):
        """Laisser trainer une ligne dont la strategie ne veut plus est un
        risque, pas une economie de frais."""
        o = planifier({}, {"CCC": 1}, COURS, 100_000, seuil_notional=1000.0)
        assert len(o) == 1 and o[0].motif == "sortie"

    def test_une_reponderation_sous_le_seuil_est_ignoree(self):
        o = planifier({"AAA": 0.501}, {"AAA": 50}, COURS, 10_000, seuil_notional=100.0)
        assert o == []

    def test_les_ventes_passent_avant_les_achats(self):
        """Le produit d'une vente n'est disponible qu'apres son execution :
        commencer par les achats ferait echouer la moitie des ordres."""
        o = planifier({"BBB": 0.9}, {"AAA": 50}, COURS, 10_000)
        assert [x.sens for x in o] == [VENTE, ACHAT]

    def test_le_plafond_par_ordre_s_applique(self):
        o = planifier({"AAA": 1.0}, {}, COURS, 10_000, max_notional=2_000.0)
        assert o[0].montant == pytest.approx(2000.0)

    def test_sans_fractionnaire_on_arrondit_au_titre_entier(self):
        o = planifier({"AAA": 0.55}, {}, COURS, 1_000, fractionnaire=False)
        assert o[0].quantite == 5.0 and o[0].montant == pytest.approx(500.0)

    def test_on_ne_vend_jamais_plus_que_detenu(self):
        o = planifier({"AAA": 0.0, "BBB": 1.0}, {"AAA": 3}, COURS, 1_000_000)
        vente = [x for x in o if x.ticker == "AAA"][0]
        assert vente.quantite <= 3.0

    def test_un_titre_sans_cours_est_signale_pas_traite(self):
        o = planifier({"ZZZ": 0.5}, {"ZZZ": 10}, COURS, 10_000)
        assert o == []
        assert sans_cours({"ZZZ": 0.5}, {"ZZZ": 10}, COURS) == ["ZZZ"]

    def test_un_compte_vide_ne_genere_rien(self):
        assert planifier({"AAA": 0.5}, {}, COURS, 0.0) == []

    def test_le_resume_totalise_les_deux_sens(self):
        o = planifier({"BBB": 0.5}, {"AAA": 50}, COURS, 10_000)
        r = resume(o)
        assert r["n_ordres"] == 2
        assert r["echange"] == pytest.approx(r["achats"] + r["ventes"])


class TestSeuilMinimal:
    """Un seuil absolu ne peut pas convenir a deux tailles de compte."""

    def test_le_seuil_suit_la_taille_du_compte(self, base_config):
        from quantbot.orders import seuil_minimal
        cfg = base_config.with_overrides({"broker.min_order_pct": 0.0025,
                                          "broker.min_order_notional": 25.0})
        assert seuil_minimal(cfg, 100_000) == pytest.approx(250.0)
        assert seuil_minimal(cfg, 1_000) == pytest.approx(25.0), "le plancher prend le relais"

    def test_le_petit_bruit_est_filtre_sur_un_gros_compte(self, base_config):
        """Regression : sur un compte de 100 000, un seuil de 25 laissait
        passer dix-sept ordres de quelques dizaines de dollars qui ne
        faisaient que suivre la derive des cours."""
        from quantbot.orders import planifier, seuil_minimal
        cfg = base_config.with_overrides({"broker.min_order_pct": 0.0025,
                                          "broker.min_order_notional": 25.0})
        equity = 101_121.0
        cours = {"AAA": 100.0}
        # position derivee de 0,13 % par rapport a la cible : du bruit
        cibles = {"AAA": 0.0335}
        positions = {"AAA": 0.0321 * equity / 100.0}
        assert planifier(cibles, positions, cours, equity,
                         seuil_notional=25.0) != [], "l'ancien seuil laissait passer"
        assert planifier(cibles, positions, cours, equity,
                         seuil_notional=seuil_minimal(cfg, equity)) == []


class TestPoussieres:
    """Une fraction de titre valant un millieme de centime n'est pas une
    position : c'est un residu d'arrondi. L'envoyer au courtier produit un
    echec permanent, repete a chaque passage."""

    def test_une_quantite_qui_s_arrondit_a_zero_n_est_jamais_envoyee(self):
        from quantbot.orders import planifier
        assert planifier({}, {"AAA": 1e-7}, COURS, 100_000) == []
        assert planifier({}, {"AAA": 1e-9}, COURS, 100_000) == []

    def test_l_arrondi_a_lieu_avant_la_validation(self):
        """round(1e-7, 6) vaut exactement 0.0 : valider avant d'arrondir
        laissait passer un ordre a quantite nulle."""
        from quantbot.orders import planifier
        for o in planifier({}, {"AAA": 5e-7}, COURS, 100_000):
            assert o.quantite is None or o.quantite > 0

    def test_une_vraie_sortie_reste_executee(self):
        from quantbot.orders import planifier
        o = planifier({}, {"AAA": 2.0}, COURS, 100_000)
        assert len(o) == 1 and o[0].quantite == pytest.approx(2.0)

    def test_les_residus_sont_listes_a_part(self):
        from quantbot.orders import poussieres
        p = poussieres({}, {"AAA": 1e-7, "BBB": 4.0}, COURS)
        assert [x["ticker"] for x in p] == ["AAA"]
        assert p[0]["valeur"] < 0.01
