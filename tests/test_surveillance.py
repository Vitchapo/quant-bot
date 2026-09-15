"""Reconciliation et veille-homme-mort."""
from __future__ import annotations

from datetime import datetime

import pytest

from quantbot.surveillance import (POUSSIERE_TITRES, bilan,
                                   positions_attendues_du_journal,
                                   positions_executees,
                                   rebalancement_incomplet, reconcilier,
                                   sante_robot)

COURS = {"AAPL": 200.0, "MSFT": 400.0, "NVDA": 100.0, "CRL": 150.0}


class TestReconciliation:
    def test_comptes_d_accord(self):
        r = reconcilier({"AAPL": 10.0}, {"AAPL": 10.0}, COURS, 100_000)
        assert r["conforme"] and r["compte"]["conforme"] == 1

    def test_petit_ecart_tolere(self):
        """0,1 % du portefeuille : un arrondi, pas un incident."""
        r = reconcilier({"AAPL": 10.5}, {"AAPL": 10.0}, COURS, 100_000)
        assert r["conforme"], r["anomalies"]

    def test_ecart_significatif_signale(self):
        r = reconcilier({"AAPL": 20.0}, {"AAPL": 10.0}, COURS, 100_000)
        assert not r["conforme"]
        a = r["anomalies"][0]
        assert a["verdict"] == "ecart"
        assert a["ecart_titres"] == 10.0 and a["ecart_valeur"] == 2000.0

    def test_ligne_detenue_mais_inconnue_du_bot(self):
        r = reconcilier({"NVDA": 50.0}, {}, COURS, 100_000)
        assert r["anomalies"][0]["verdict"] == "inconnue"

    def test_ligne_attendue_mais_absente_du_compte(self):
        r = reconcilier({}, {"NVDA": 50.0}, COURS, 100_000)
        assert r["anomalies"][0]["verdict"] == "disparue"

    def test_poussiere_non_alarmante(self):
        """La ligne CRL reelle : 3.6e-7 titre des deux cotes."""
        r = reconcilier({"CRL": 3.63e-7}, {"CRL": 0.0}, COURS, 100_000)
        assert r["conforme"]
        assert r["lignes"][0]["verdict"] == "poussiere"

    def test_sans_cours_on_ne_minimise_pas(self):
        """Une ligne qu'on ne sait pas valoriser doit remonter, pas disparaitre."""
        r = reconcilier({"XYZ": 10.0}, {"XYZ": 3.0}, COURS, 100_000)
        assert not r["conforme"]
        assert r["anomalies"][0]["verdict"] == "invalorisable"

    def test_la_tolerance_suit_la_taille_du_compte(self):
        """Le meme ecart de 200 $ est du bruit a 100k et un incident a 1k."""
        gros = reconcilier({"AAPL": 11.0}, {"AAPL": 10.0}, COURS, 100_000)
        petit = reconcilier({"AAPL": 11.0}, {"AAPL": 10.0}, COURS, 1_000)
        assert gros["conforme"] and not petit["conforme"]

    def test_ecart_total_cumule(self):
        r = reconcilier({"AAPL": 20.0, "MSFT": 5.0}, {"AAPL": 10.0, "MSFT": 10.0},
                        COURS, 100_000)
        assert r["ecart_total"] == pytest.approx(2000.0 + 2000.0)
        assert r["ecart_total_pct"] == pytest.approx(0.04)

    def test_equity_nulle_ne_divise_pas_par_zero(self):
        r = reconcilier({"AAPL": 1.0}, {}, COURS, 0)
        assert r["ecart_total_pct"] is None


class TestPositionsDepuisLeJournal:
    """Reconstituer ce que le bot devrait detenir, a partir de son journal.

    LE BUG QUE CES TESTS EXISTENT POUR EMPECHER
    -------------------------------------------
    Les achats fractionnaires partent en MONTANT, pas en nombre de titres :
    leur colonne `quantite` est vide - 44 lignes sur 44 dans le journal reel.
    Les ventes en portent une. La premiere version ignorait les lignes sans
    quantite, donc tous les achats, et n'additionnait que les ventes en
    negatif. Resultat affiche a l'utilisateur : "21 anomalies, ecart total
    110,66 % du portefeuille" - un portefeuille parfaitement sain.

    Pire : le test `test_quantite_absente_ignoree` affirmait que c'etait le
    comportement correct. Un test peut sanctuariser un bug ; celui-la l'a fait.
    """

    def test_achats_en_montant_comptes_via_le_cours(self):
        """LE test. Un achat sans quantite doit etre reconstitue, pas ignore."""
        j = [{"ticker": "AAPL", "sens": "buy", "quantite": "", "montant": "1000",
              "cours": "200", "statut": "accepted"}]
        positions, fiable = positions_attendues_du_journal(j)
        assert positions == {"AAPL": pytest.approx(5.0)}
        assert fiable["ok"] and fiable["approximations"] == 1

    def test_aucune_position_negative_sur_un_journal_realiste(self):
        """Achats en montant puis vente partielle : le solde reste positif."""
        j = [{"ticker": "WBD", "sens": "buy", "quantite": "", "montant": "10000",
              "cours": "28.0", "statut": "accepted"},
             {"ticker": "WBD", "sens": "sell", "quantite": "12.715659",
              "montant": "357.82", "cours": "28.14", "statut": "accepted"}]
        positions, fiable = positions_attendues_du_journal(j)
        assert positions["WBD"] > 0, positions
        assert fiable["ok"], fiable["message"]

    def test_un_negatif_important_invalide_la_reconstitution(self):
        """Un bot exclusivement acheteur ne peut pas detenir du negatif. Quand
        le calcul en produit franchement, c'est l'outil qui se trompe - et il
        doit le dire au lieu de denoncer le compte."""
        j = [{"ticker": "CRL", "sens": "sell", "quantite": "19.88",
              "montant": "5625", "cours": "283", "statut": "accepted"}]
        positions, fiable = positions_attendues_du_journal(j)
        assert fiable["ok"] is False
        assert "CRL" in fiable["negatives"]
        assert "negative" in fiable["message"]

    def test_un_residu_negatif_minuscule_ne_declenche_rien(self):
        """Cas REEL du journal : CRL achete 5683,34 a 288,50 (soit 19,70
        titres reconstitues) puis vendu 19,877273 - le solde ressort a -0,17,
        pur artefact de l'approximation `montant / cours`. Invalider toute la
        reconciliation pour cela revient a crier au loup."""
        j = [{"ticker": "CRL", "sens": "buy", "quantite": "", "montant": "5683.34",
              "cours": "288.50", "statut": "accepted"},
             {"ticker": "CRL", "sens": "sell", "quantite": "19.877273",
              "montant": "5625.32", "cours": "283.00", "statut": "accepted"}]
        positions, fiable = positions_attendues_du_journal(j)
        assert fiable["ok"] is True, fiable["message"]
        assert "CRL" not in positions          # ligne soldee, pas de residu affiche

    def test_achats_et_ventes_se_compensent(self):
        j = [{"ticker": "AAPL", "sens": "buy", "quantite": "10", "statut": "accepted"},
             {"ticker": "AAPL", "sens": "sell", "quantite": "4", "statut": "accepted"}]
        positions, fiable = positions_attendues_du_journal(j)
        assert positions == {"AAPL": 6.0} and fiable["ok"]

    def test_les_ordres_en_echec_sont_ignores(self):
        """Compter un ordre refuse fabriquerait l'ecart qu'on cherche."""
        j = [{"ticker": "AAPL", "sens": "buy", "quantite": "10", "statut": "accepted"},
             {"ticker": "AAPL", "sens": "buy", "quantite": "99", "statut": "ECHEC"},
             {"ticker": "AAPL", "sens": "buy", "quantite": "5", "statut": "rejected"}]
        positions, _ = positions_attendues_du_journal(j)
        assert positions == {"AAPL": 10.0}

    def test_filtrage_par_compte(self):
        j = [{"ticker": "AAPL", "sens": "buy", "quantite": "10",
              "statut": "accepted", "compte": "PA1"},
             {"ticker": "AAPL", "sens": "buy", "quantite": "99",
              "statut": "accepted", "compte": "AUTRE"}]
        positions, _ = positions_attendues_du_journal(j, compte="PA1")
        assert positions == {"AAPL": 10.0}

    def test_ni_quantite_ni_montant_exploitable(self):
        j = [{"ticker": "AAPL", "sens": "buy", "quantite": "", "montant": "",
              "cours": "", "statut": "accepted"},
             {"ticker": "AAPL", "sens": "buy", "quantite": "3", "statut": "accepted"}]
        positions, _ = positions_attendues_du_journal(j)
        assert positions == {"AAPL": 3.0}

    def test_solde_nul_non_retourne(self):
        j = [{"ticker": "AAPL", "sens": "buy", "quantite": "5", "statut": "accepted"},
             {"ticker": "AAPL", "sens": "sell", "quantite": "5", "statut": "accepted"}]
        positions, _ = positions_attendues_du_journal(j)
        assert positions == {}


class TestPositionsExecutees:
    """La source qui fait foi : ce que le courtier dit avoir rempli."""

    def test_filled_qty_additionne(self):
        o = [{"symbol": "AAPL", "side": "buy", "filled_qty": "5.5"},
             {"symbol": "AAPL", "side": "sell", "filled_qty": "1.5"},
             {"symbol": "MSFT", "side": "buy", "filled_qty": "2"}]
        assert positions_executees(o) == {"AAPL": 4.0, "MSFT": 2.0}

    def test_un_ordre_non_rempli_ne_compte_pas(self):
        """C'est tout l'interet : un ordre envoye mais jamais execute ne doit
        pas apparaitre, alors que le journal du bot le contient."""
        o = [{"symbol": "AAPL", "side": "buy", "filled_qty": "0"},
             {"symbol": "AAPL", "side": "buy", "filled_qty": "3"}]
        assert positions_executees(o) == {"AAPL": 3.0}

    def test_execution_partielle_prise_telle_quelle(self):
        o = [{"symbol": "AAPL", "side": "buy", "filled_qty": "2.5"}]
        assert positions_executees(o) == {"AAPL": 2.5}

    def test_liste_vide(self):
        assert positions_executees([]) == {} and positions_executees(None) == {}


class TestReconciliationNonExploitable:
    def test_une_reconstitution_fausse_ne_denonce_pas_le_compte(self):
        """Le comportement du 14 septembre : 110 % d'ecart annonces alors que
        seul l'outil etait en faute."""
        rec = reconcilier({"AAPL": 10.0}, {}, COURS, 100_000,
                          fiabilite={"ok": False, "negatives": ["AAPL"],
                                     "message": "reconstitution incoherente"})
        assert rec["exploitable"] is False
        assert rec["anomalies"] == [] and rec["conforme"] is None
        assert rec["ecart_total_pct"] is None

    def test_le_bilan_parle_d_outil_pas_de_compte(self):
        from datetime import datetime
        rec = reconcilier({"AAPL": 10.0}, {}, COURS, 100_000,
                          fiabilite={"ok": False, "negatives": ["AAPL"],
                                     "message": "reconstitution incoherente"})
        b = bilan({"dernier_passage": "2026-09-10T00:30:00", "dernier_resultat": "ATTENDRE"},
                  rec, rebalancement_incomplet([]),
                  maintenant=datetime(2026, 9, 10, 12, 0))
        assert b["niveau"] == "attention"
        assert "Reconciliation impossible" in " ".join(b["attentions"])


class TestSanteRobot:
    MAINTENANT = datetime(2026, 9, 10, 12, 0)      # un jeudi

    def test_passage_recent(self):
        s = sante_robot({"dernier_passage": "2026-09-10T00:30:00",
                         "dernier_resultat": "EXECUTER"}, self.MAINTENANT)
        assert s["ok"] and s["niveau"] == "normal"

    def test_silence_trop_long(self):
        s = sante_robot({"dernier_passage": "2026-09-05T00:30:00",
                         "dernier_resultat": "ATTENDRE"}, self.MAINTENANT)
        assert not s["ok"] and s["niveau"] == "alerte"
        assert "Aucun passage" in s["message"]

    def test_jamais_lance(self):
        s = sante_robot({}, self.MAINTENANT)
        assert not s["ok"] and s["niveau"] == "alerte"
        assert "jamais" in s["message"]

    def test_le_week_end_n_est_pas_une_panne(self):
        """Lundi matin, le dernier passage remonte a vendredi : c'est normal.
        Une alerte qui crie tous les lundis est une alerte qu'on ignore."""
        lundi = datetime(2026, 9, 14, 9, 0)
        s = sante_robot({"dernier_passage": "2026-09-11T23:00:00",
                         "dernier_resultat": "ATTENDRE"}, lundi)
        assert s["ok"], s["message"]

    def test_robot_vivant_mais_qui_rate_sa_fenetre(self):
        """L'etat reel trouve dans data/robot_etat.json : il tourne, mais il
        arrive trop tard. Vivant n'est pas synonyme de sain."""
        s = sante_robot({"dernier_passage": "2026-09-10T00:43:25",
                         "dernier_resultat": "saute (trop tard)"}, self.MAINTENANT)
        assert not s["ok"] and s["niveau"] == "attention"
        assert "fenetre" in s["message"]

    def test_horodatage_illisible(self):
        s = sante_robot({"dernier_passage": "pas une date"}, self.MAINTENANT)
        assert not s["ok"] and s["niveau"] == "alerte"


class TestRebalancementIncomplet:
    def test_vague_complete(self):
        j = [{"horodatage": "2026-09-01T21:00:00", "sens": "sell", "statut": "accepted"},
             {"horodatage": "2026-09-01T21:00:10", "sens": "buy", "statut": "accepted"}]
        r = rebalancement_incomplet(j)
        assert r["ok"], r["incidents"]

    def test_ventes_sans_achats(self):
        """Le cas dangereux : interrompu entre les ventes et les achats."""
        j = [{"horodatage": "2026-09-01T21:00:00", "sens": "sell", "statut": "accepted"},
             {"horodatage": "2026-09-01T21:00:01", "sens": "sell", "statut": "accepted"}]
        r = rebalancement_incomplet(j)
        assert not r["ok"]
        assert "sans aucun achat" in r["incidents"][0]["souci"]

    def test_echecs_signales(self):
        j = [{"horodatage": "2026-09-01T21:00:00", "sens": "sell", "statut": "accepted"},
             {"horodatage": "2026-09-01T21:00:01", "sens": "buy", "statut": "ECHEC"}]
        r = rebalancement_incomplet(j)
        assert not r["ok"] and "echec" in r["incidents"][0]["souci"]

    def test_journal_vide(self):
        assert rebalancement_incomplet([])["ok"]


class TestBilan:
    MAINTENANT = datetime(2026, 9, 10, 12, 0)

    def _sain(self):
        return ({"dernier_passage": "2026-09-10T00:30:00", "dernier_resultat": "ATTENDRE"},
                reconcilier({"AAPL": 10.0}, {"AAPL": 10.0}, COURS, 100_000),
                rebalancement_incomplet([]))

    def test_tout_va_bien(self):
        b = bilan(*self._sain(), maintenant=self.MAINTENANT)
        assert b["niveau"] == "normal" and not b["alertes"]

    def test_gros_ecart_devient_une_alerte(self):
        etat, _, inc = self._sain()
        rec = reconcilier({"AAPL": 40.0}, {"AAPL": 10.0}, COURS, 100_000)  # 6 %
        b = bilan(etat, rec, inc, maintenant=self.MAINTENANT)
        assert b["niveau"] == "alerte"

    def test_petit_ecart_reste_une_attention(self):
        etat, _, inc = self._sain()
        rec = reconcilier({"AAPL": 15.0}, {"AAPL": 10.0}, COURS, 100_000)  # 1 %
        b = bilan(etat, rec, inc, maintenant=self.MAINTENANT)
        assert b["niveau"] == "attention"

    def test_robot_muet_prime_sur_le_reste(self):
        _, rec, inc = self._sain()
        b = bilan({"dernier_passage": "2026-08-01T00:00:00"}, rec, inc,
                  maintenant=self.MAINTENANT)
        assert b["niveau"] == "alerte" and "Aucun passage" in b["resume"]


class TestTachePlanifiee:
    """Nommer la cause du silence, pas seulement le constater."""

    def test_hors_windows_le_controle_s_abstient(self, monkeypatch):
        import quantbot.surveillance as sv
        monkeypatch.setattr("os.name", "posix")
        t = sv.tache_planifiee()
        assert t["connu"] is False and t["existe"] is None

    def test_tache_absente_signalee(self, monkeypatch):
        import subprocess
        import quantbot.surveillance as sv

        class R:
            returncode = 1
        monkeypatch.setattr("os.name", "nt")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        t = sv.tache_planifiee()
        assert t["connu"] and t["existe"] is False
        assert "AUCUNE tache" in t["message"]
        assert "--installer" in t["message"]

    def test_tache_presente(self, monkeypatch):
        import subprocess
        import quantbot.surveillance as sv

        class R:
            returncode = 0
        monkeypatch.setattr("os.name", "nt")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
        t = sv.tache_planifiee()
        assert t["existe"] is True

    def test_schtasks_introuvable(self, monkeypatch):
        import subprocess
        import quantbot.surveillance as sv

        def boum(*a, **k):
            raise OSError("schtasks introuvable")
        monkeypatch.setattr("os.name", "nt")
        monkeypatch.setattr(subprocess, "run", boum)
        t = sv.tache_planifiee()
        assert t["connu"] is False and "introuvable" in t["message"]
