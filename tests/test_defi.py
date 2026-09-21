"""Garde-fous de perte au format prop firm."""
from __future__ import annotations

import pytest

from quantbot.defi import (PERTE_JOUR_DEFAUT, VERROU_OBJECTIF, VERROU_PERTE,
                           est_verrouille, evaluer, lever_verrou, lire_etat,
                           marquer_solde, parametres, resume, ecrire_etat)

P = {"perte_jour_max": 0.04, "perte_totale_max": 0.08, "objectif": 0.10,
     "reference": "statique", "solder": True}


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

    def test_objectif_atteint_ARRETE_le_bot(self):
        """L'ancienne version calculait `objectif_atteint` et n'en faisait rien.

        C'etait le seul moment du defi ou le rapport risque/gain est
        strictement defavorable : la phase est acquise, chaque seance de plus ne
        peut que la reprendre. Le drapeau existait, personne ne le lisait, et le
        bot continuait a s'exposer.
        """
        v = evaluer(110500, 110000, P, {"capital_depart": 100000, "plus_haut": 110500})
        assert v["objectif_atteint"]
        assert not v["ok"], "objectif atteint et le bot continue"
        assert v["verrou"]["raison"] == VERROU_OBJECTIF
        assert "objectif atteint" in v["raison"]

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
    """Deux questions distinctes, et les confondre est exactement ce qui a
    laisse passer le bug de reference statique/glissante :

      - que fait le CODE quand la cle est absente ?
      - que dit le fichier de configuration EXPEDIE ?

    Un seul test qui relit `config/us.yaml` ne repond ni a l'une ni a l'autre :
    il passe aussi bien si le code obeit a la config que s'il code en dur la
    meme valeur, et il casse des que quelqu'un change le fichier pour une
    raison parfaitement legitime.
    """

    def test_le_repli_du_code_est_inactif(self, base_config):
        """Cle absente : on ne s'arrete pas tout seul. Un garde-fou qui
        s'allume sans qu'on l'ait demande est une regression."""
        cfg = base_config.copy()
        cfg.set("defi.active", None, strict=True)
        assert parametres(cfg) is None

    def test_le_fichier_expedie_allume_les_garde_fous(self, base_config):
        """Ce que le projet expedie aujourd'hui : actif, en reference statique,
        avec une marge sous les seuils contractuels (-5 % / -10 %)."""
        assert base_config.get("defi.active") is True
        p = parametres(base_config)
        assert p is not None
        assert p["reference"] == "statique"
        assert p["perte_jour_max"] < 0.05
        assert p["perte_totale_max"] < 0.10

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

    def test_le_fichier_de_config_dit_bien_statique(self, base_config):
        """Ce que le projet EXPEDIE. Le defi vise annonce "Static"."""
        cfg = base_config.copy()
        cfg.set("defi.active", True, strict=True)
        assert parametres(cfg)["reference"] == "statique"

    def test_sans_la_cle_le_repli_est_statique(self, base_config):
        """Le REPLI du code, cle absente - ce que le test precedent ne voit
        pas. La distinction n'est pas une coquetterie : `evaluer` a longtemps
        calcule la perte depuis le plus haut en IGNORANT `defi.reference`,
        pendant que la config affichait `statique`. Un test qui relit la
        config ne peut pas distinguer "le code obeit a la config" de "le code
        code en dur la meme valeur que la config".
        """
        cfg = base_config.copy()
        cfg.set("defi.active", True, strict=True)
        cfg.set("defi.reference", None, strict=True)
        assert parametres(cfg)["reference"] == "statique"

    def test_la_config_est_reellement_lue(self, base_config):
        """Et le pendant : mettre `glissante` dans la config DOIT changer le
        comportement. Sans cela, `reference` pourrait etre ignore sans qu'aucun
        test ne bronche - le bug exact qui est passe."""
        cfg = base_config.copy()
        cfg.set("defi.active", True, strict=True)
        cfg.set("defi.reference", "glissante", strict=True)
        p = parametres(cfg)
        assert p["reference"] == "glissante"
        v = evaluer(101000, 101500, p, self.ETAT)
        assert not v["ok"], "la config dit glissante, le verdict doit suivre"


class TestPerteJournaliereRapporteeAuDepart:
    """Le denominateur de la limite journaliere, et pourquoi il compte.

    Le contrat dit "5 % of initial balance" : un montant fixe en dollars,
    mesure sur le capital de DEPART. L'ancienne version rapportait la perte du
    jour a l'equity de la VEILLE. Les deux formules coincident au premier jour
    et divergent ensuite - dans les deux sens, et les deux sont mauvais.
    """

    ETAT = {"capital_depart": 100000, "plus_haut": 200000}

    def test_le_compte_a_double_et_la_limite_ne_suit_pas(self):
        """Le cas qui laissait passer une elimination.

        Parti de 100 000, monte a 200 000, une seance a -6 000. Le contrat
        compte 6 000 sur une enveloppe de 5 000 : elimine. L'ancienne formule
        voyait -3 % de 200 000 et trouvait cela tres raisonnable.
        """
        v = evaluer(194000, 200000, P, self.ETAT)
        assert abs(v["perte_jour"] - (-0.06)) < 1e-12, "la base n'est pas le depart"
        assert not v["ok"], "6 000 de perte sur une enveloppe de 4 000 doit bloquer"
        # Et la demonstration que l'ancienne formule ne bloquait pas :
        assert abs(194000 / 200000 - 1.0) < 0.04

    def test_apres_une_baisse_la_limite_ne_se_resserre_pas(self):
        """Le cas symetrique : l'ancienne formule arretait le bot pour rien.

        Descendu a 60 000, une seance a -3 000. Le contrat compte 3 000 sur
        5 000 : rien a signaler. L'ancienne formule lisait -5 % et arretait la
        machine pour une perte que le contrat ne comptait pas.
        """
        etat = {"capital_depart": 100000, "plus_haut": 100000}
        v = evaluer(57000, 60000, dict(P, perte_totale_max=0.90), etat)
        assert abs(v["perte_jour"] - (-0.03)) < 1e-12
        assert v["ok"], v["raison"]
        assert abs(57000 / 60000 - 1.0) > 0.04   # l'ancienne formule bloquait

    def test_au_premier_jour_les_deux_formules_coincident(self):
        """Ce qui explique que le bug ait pu passer : sur un compte neuf,
        depart et veille sont le meme nombre."""
        v = evaluer(96000, 100000, P, {"capital_depart": 100000, "plus_haut": 100000})
        assert abs(v["perte_jour"] - (-0.04)) < 1e-12
        assert not v["ok"]


class TestVerrou:
    """Une breche doit survivre au rebond, sinon elle ne constate rien.

    Et surtout : un bot qui vient de solder se retrouve avec un compte VIDE,
    ce qui est exactement la condition d'amorcage. Sans verrou, la sequence
    etait perte -> liquidation -> compte vide -> "un compte neuf !" -> rachat
    du portefeuille entier le lendemain, dans le marche qui venait de
    declencher la limite.
    """

    SAIN = {"capital_depart": 100000, "plus_haut": 100000}

    def test_la_breche_pose_un_verrou_et_demande_a_solder(self):
        v = evaluer(91000, 100000, P, dict(self.SAIN))
        assert not v["ok"]
        assert v["verrou"]["raison"] == VERROU_PERTE
        assert v["nouveau_verrou"] is True
        assert v["a_solder"] is True
        assert est_verrouille(v["etat"])

    def test_le_verrou_survit_au_rebond(self):
        """LE test de ce module. L'equity remonte au-dessus de tout ; le verrou
        reste, parce qu'un defi franchi est franchi."""
        v1 = evaluer(91000, 100000, P, dict(self.SAIN))
        v2 = evaluer(105000, 91000, P, v1["etat"])
        assert not v2["ok"], "le verrou s'est leve parce que le compte est remonte"
        assert v2["verrou"]["raison"] == VERROU_PERTE
        assert v2["verrou"]["depuis"] == v1["verrou"]["depuis"]

    def test_nouveau_verrou_ne_vaut_vrai_qu_une_seule_fois(self):
        """C'est ce drapeau qui declenche la liquidation. S'il restait vrai, le
        bot renverrait des ordres de vente a chaque passage."""
        v1 = evaluer(91000, 100000, P, dict(self.SAIN))
        v2 = evaluer(91000, 91000, P, v1["etat"])
        assert v1["nouveau_verrou"] is True
        assert v2["nouveau_verrou"] is False

    def test_le_motif_du_premier_verrou_est_conserve(self):
        """Verrouille pour perte puis objectif atteint : le motif ne change
        pas. On ne requalifie pas une elimination en victoire."""
        v1 = evaluer(91000, 100000, P, dict(self.SAIN))
        v2 = evaluer(115000, 91000, P, v1["etat"])
        assert v2["verrou"]["raison"] == VERROU_PERTE

    def test_solder_desactive_ne_demande_pas_de_liquidation(self):
        """`solder_sur_verrou: false` : on gele, on ne vend pas."""
        v = evaluer(91000, 100000, dict(P, solder=False), dict(self.SAIN))
        assert not v["ok"] and v["verrou"] is not None
        assert v["a_solder"] is False

    def test_marquer_solde_eteint_la_consigne_sans_lever_le_verrou(self):
        v = evaluer(91000, 100000, P, dict(self.SAIN))
        etat = marquer_solde(v["etat"])
        assert etat["a_solder"] is False
        assert est_verrouille(etat), "le verrou est tombe avec la consigne"
        assert not evaluer(91000, 91000, P, etat)["a_solder"]

    def test_lever_le_verrou_ne_remet_pas_les_references_a_zero(self):
        """Lever un verrou n'est pas commencer un nouveau defi : un verrou leve
        sans que le compte soit remonte se repose au passage suivant."""
        v = evaluer(91000, 100000, P, dict(self.SAIN))
        etat = lever_verrou(v["etat"])
        assert not est_verrouille(etat)
        assert etat["capital_depart"] == 100000
        assert evaluer(91000, 91000, P, etat)["verrou"] is not None

    def test_le_verrou_franchit_le_disque(self, tmp_path):
        chemin = tmp_path / "defi.json"
        v = evaluer(91000, 100000, P, dict(self.SAIN))
        ecrire_etat(v["etat"], chemin)
        assert est_verrouille(lire_etat(chemin))
        assert lire_etat(chemin)["verrou"]["raison"] == VERROU_PERTE

    def test_le_resume_annonce_le_verrou_et_la_consigne(self):
        v = evaluer(91000, 100000, P, dict(self.SAIN))
        t = resume(v, P)
        assert "VERROU" in t and "A SOLDER" in t

    def test_un_compte_vide_sous_verrou_reste_sous_verrou(self):
        """Apres liquidation, l'equity peut passer a zero le temps que les
        ordres se reglent. Le chemin court de `evaluer` doit quand meme
        rapporter le verrou, sans quoi l'amorcage reprendrait la main."""
        v = evaluer(91000, 100000, P, dict(self.SAIN))
        v2 = evaluer(0, 0, P, v["etat"])
        assert v2["verrou"] is not None
