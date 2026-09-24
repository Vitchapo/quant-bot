"""Le coupe-circuit journalier FTMO.

Toute la decision tient dans deux fonctions PURES - `verdict` et
`reference_du_jour` - et c'est deliberе : un code qui ferme des positions doit
etre verifiable sans terminal MT5 ni faux serveur.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import coupe_circuit as cc  # noqa: E402


class TestVerdict:
    """Seuil a 4 %, sous les 5 % du contrat FTMO."""

    def test_compte_sain(self):
        v = cc.verdict(100_000, 100_000, 0.04)
        assert v["ok"] and v["perte"] == 0.0

    def test_juste_sous_le_seuil_passe(self):
        v = cc.verdict(96_500, 100_000, 0.04)          # -3,5 %
        assert v["ok"]

    def test_le_seuil_coupe(self):
        v = cc.verdict(96_000, 100_000, 0.04)          # -4,0 % pile
        assert not v["ok"]

    def test_au_dela_coupe(self):
        v = cc.verdict(94_000, 100_000, 0.04)
        assert not v["ok"] and v["perte"] == pytest.approx(-0.06)

    def test_la_marge_restante_est_exposee(self):
        """Le chiffre utile en seance : combien de points avant la coupure."""
        v = cc.verdict(98_000, 100_000, 0.04)
        assert v["marge_restante"] == pytest.approx(0.02)

    def test_une_reference_nulle_ne_divise_pas_par_zero(self):
        assert cc.verdict(100_000, 0, 0.04)["ok"] is True

    def test_la_marge_du_contrat_est_reelle(self):
        """A -4,5 %, le bot a coupe et FTMO n'a pas encore elimine. C'est tout
        l'interet d'un seuil sous le seuil : entre la decision et l'execution,
        le marche continue de bouger."""
        v = cc.verdict(95_500, 100_000, 0.04)
        assert not v["ok"]
        assert v["perte"] > -0.05, "on coupe AVANT la limite contractuelle"


class TestReferenceDuJour:
    """La reference FTMO est le SOLDE a 00:00 CET, et elle ne bouge pas en
    cours de journee."""

    def test_le_premier_passage_fige_la_reference(self, tmp_path):
        f = tmp_path / "ref.json"
        r = cc.reference_du_jour(100_000, 100_000, chemin=f)
        assert r["solde_reference"] == 100_000
        assert f.exists()

    def test_LE_PIEGE_un_ordre_execute_ne_deplace_pas_la_limite(self, tmp_path):
        """Sans memoire sur disque, un ordre execute a 15h00 changerait le solde
        et donc la limite, EN PLEINE SEANCE : la limite journaliere deviendrait
        mobile alors qu'elle est fixe."""
        f = tmp_path / "ref.json"
        cc.reference_du_jour(100_000, 100_000, chemin=f)
        r = cc.reference_du_jour(92_000, 92_000, chemin=f)     # solde a bouge
        assert r["solde_reference"] == 100_000, "la reference a suivi le solde"

    def test_un_nouveau_jour_cet_remet_la_reference(self, tmp_path):
        f = tmp_path / "ref.json"
        hier = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        cc.reference_du_jour(100_000, 100_000, chemin=f, maintenant=hier)
        demain = hier + timedelta(days=1)
        r = cc.reference_du_jour(97_000, 97_000, chemin=f, maintenant=demain)
        assert r["solde_reference"] == 97_000
        assert r["jour"] != cc._jour_cet(hier)

    def test_un_fichier_corrompu_ne_leve_pas(self, tmp_path):
        f = tmp_path / "ref.json"
        f.write_text("{ pas du json", encoding="utf-8")
        assert cc.reference_du_jour(100_000, 100_000, chemin=f)["solde_reference"] == 100_000

    def test_la_journee_cet_est_en_avance_sur_utc(self):
        """Minuit CET tombe a 23h00 UTC la veille : une perte subie a 23h30 UTC
        appartient deja au lendemain CET."""
        t = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
        assert cc._jour_cet(t) == "2026-09-22"


class TestReferenceEtVerdictEnsemble:
    def test_le_scenario_complet_d_une_journee(self, tmp_path):
        f = tmp_path / "ref.json"
        matin = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
        ref = cc.reference_du_jour(100_000, 100_000, chemin=f, maintenant=matin)

        # midi : -2 %, on continue
        v = cc.verdict(98_000, ref["solde_reference"], 0.04)
        assert v["ok"]

        # 16h : -4,2 %, on coupe. La reference n'a pas bouge entre-temps.
        ref2 = cc.reference_du_jour(98_000, 95_800, chemin=f,
                                    maintenant=matin + timedelta(hours=8))
        assert ref2["solde_reference"] == 100_000
        assert not cc.verdict(95_800, ref2["solde_reference"], 0.04)["ok"]


class TestConfigurationFTMO:
    """Ce que le fichier expedie annonce, confronte aux regles relevees sur
    ftmo.com le 21 septembre 2026 : +10 %/+5 %, jour -5 %, max -10 % statique."""

    @pytest.fixture
    def cfg_ftmo(self):
        from quantbot.config import Config
        return Config.load(Path(__file__).resolve().parent.parent / "config" / "ftmo.yaml")

    def test_les_seuils_laissent_une_marge_sous_le_contrat(self, cfg_ftmo):
        assert cfg_ftmo.get("defi.perte_jour_max") < 0.05
        assert cfg_ftmo.get("defi.perte_totale_max") < 0.10

    def test_la_reference_est_statique_comme_le_2_step(self, cfg_ftmo):
        assert cfg_ftmo.get("defi.reference") == "statique"

    def test_l_objectif_est_celui_de_la_phase_1(self, cfg_ftmo):
        assert cfg_ftmo.get("defi.objectif") == 0.10

    def test_le_verrou_solde(self, cfg_ftmo):
        assert cfg_ftmo.get("defi.solder_sur_verrou") is True

    def test_aucun_levier_demande_par_defaut(self, cfg_ftmo):
        """Le levier actions est plafonne a 1:5 : une exposition superieure a
        100 % consommerait plus de 20 % du capital en marge."""
        assert cfg_ftmo.get("marge.exposition_max") <= 1.0

    def test_le_courtier_est_mt5_et_le_reel_est_verrouille(self, cfg_ftmo):
        assert cfg_ftmo.get("broker.provider") == "mt5"
        assert cfg_ftmo.get("broker.allow_live") is False

    def test_aucun_mot_de_passe_dans_la_config(self, cfg_ftmo):
        """Il vient de MT5_PASSWORD, jamais d'un fichier versionne."""
        brut = (Path(__file__).resolve().parent.parent / "config" / "ftmo.yaml"
                ).read_text(encoding="utf-8").lower()
        assert "password" not in brut.replace("mt5_password", "")
        assert cfg_ftmo.get("broker.mot_de_passe") is None

    def test_top_n_est_adapte_a_un_petit_univers(self, cfg_ftmo):
        """Sur ~55 titres, un top 20 ne selectionne plus : il detient un tiers
        du catalogue."""
        assert cfg_ftmo.get("portfolio.top_n") <= 10

    def test_l_univers_est_une_liste_figee(self, cfg_ftmo):
        assert cfg_ftmo.get("universe.source") == "file"
        f = Path(__file__).resolve().parent.parent / cfg_ftmo.get("universe.file")
        assert f.exists()
        lignes = [l.strip() for l in f.read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#")]
        assert 30 <= len(lignes) <= 120, len(lignes)


class TestSanteDuCoupeCircuit:
    """Le defaut trouve le 21 septembre 2026, en production.

    `--installer` a reussi alors que le terminal MT5 n'etait pas installe. La
    tache s'est donc enregistree et s'execute toutes les dix minutes contre
    rien. Vu du planificateur Windows, elle a l'air vivante : elle existe, elle
    part a l'heure, elle rend la main. C'est la forme la plus dangereuse de
    panne - celle qui ressemble a un fonctionnement.

    C'est le meme mode de defaillance que la tache `quantbot` qui n'a jamais
    existe pendant des semaines. Ici l'enjeu est pire : entre-temps, des
    positions peuvent etre ouvertes et ne sont protegees par rien.
    """

    def test_le_premier_echec_est_journalise(self, tmp_path):
        e = cc.noter_echec("terminal absent", chemin=tmp_path / "e.json")
        assert e["echecs_consecutifs"] == 1
        assert e["journaliser"] is True
        assert e["alerter"] is False

    def test_les_echecs_identiques_ne_sont_PAS_tous_journalises(self, tmp_path):
        """144 entrees identiques par jour noieraient les trois lignes qui
        comptent. Une alerte qui crie pour rien est une alerte qu'on ignore."""
        f = tmp_path / "e.json"
        ecrits = sum(1 for _ in range(20)
                     if cc.noter_echec("terminal absent", chemin=f)["journaliser"])
        assert ecrits < 20 / 2, "le journal n'est pas etrangle"

    def test_l_escalade_se_declenche_au_seuil(self, tmp_path):
        f = tmp_path / "e.json"
        for i in range(1, cc.ECHECS_AVANT_ALERTE):
            assert cc.noter_echec("x", chemin=f)["alerter"] is False, i
        assert cc.noter_echec("x", chemin=f)["alerter"] is True

    def test_un_succes_remet_le_compteur_a_zero(self, tmp_path):
        f = tmp_path / "e.json"
        for _ in range(10):
            cc.noter_echec("x", chemin=f)
        e = cc.noter_succes(chemin=f)
        assert e["echecs_consecutifs"] == 0
        assert "derniere_erreur" not in e
        assert cc.noter_echec("y", chemin=f)["alerter"] is False

    def test_la_cause_est_conservee(self, tmp_path):
        f = tmp_path / "e.json"
        cc.noter_echec("MetaTrader 5 x64 not found", chemin=f)
        assert "x64 not found" in cc._lire_etat(f)["derniere_erreur"]

    def test_la_date_du_premier_echec_ne_bouge_pas(self, tmp_path):
        """Pour savoir depuis QUAND on n'est plus protege."""
        f = tmp_path / "e.json"
        premier = cc.noter_echec("x", chemin=f)["premiere_erreur"]
        for _ in range(5):
            cc.noter_echec("x", chemin=f)
        assert cc._lire_etat(f)["premiere_erreur"] == premier

    def test_un_etat_corrompu_ne_leve_pas(self, tmp_path):
        f = tmp_path / "e.json"
        f.write_text("{ pas du json", encoding="utf-8")
        assert cc.noter_echec("x", chemin=f)["echecs_consecutifs"] == 1


class TestSanteRemonteeDansLeBilan:
    """La veille existante doit VOIR un coupe-circuit muet, sinon l'information
    reste dans un fichier que personne n'ouvre."""

    def test_jamais_lance_est_normal(self):
        from quantbot.surveillance import sante_coupe_circuit
        assert sante_coupe_circuit({})["niveau"] == "normal"

    def test_quelques_echecs_donnent_une_attention(self):
        from quantbot.surveillance import sante_coupe_circuit
        s = sante_coupe_circuit({"echecs_consecutifs": 2, "derniere_erreur": "x"})
        assert s["niveau"] == "attention"

    def test_au_dela_du_seuil_c_est_une_ALERTE(self):
        from quantbot.surveillance import sante_coupe_circuit
        s = sante_coupe_circuit({"echecs_consecutifs": 20, "derniere_erreur": "x",
                                 "premiere_erreur": "2026-09-21T08:00:00"})
        assert s["niveau"] == "alerte"
        assert "RIEN" in s["message"], "le message doit etre sans ambiguite"

    def test_le_bilan_fait_remonter_l_alerte_en_PREMIER(self):
        """Un desaccord de reconciliation se rattrape ; une limite de perte
        non surveillee, non."""
        from quantbot.surveillance import bilan
        b = bilan({"dernier_passage": "2026-09-21T21:00:00",
                   "dernier_resultat": "2 ordres"},
                  {"conforme": True}, {"ok": True},
                  etat_coupe_circuit={"echecs_consecutifs": 30,
                                      "derniere_erreur": "terminal absent",
                                      "premiere_erreur": "2026-09-21T08:00:00"})
        assert b["niveau"] == "alerte"
        assert "COUPE-CIRCUIT" in b["resume"]

    def test_un_coupe_circuit_sain_ne_declenche_rien(self):
        """La date doit etre RELATIVE : une date en dur devient « muette »
        des le lendemain, et le test se met a echouer tout seul. C'est
        exactement ce qui est arrive le 22 septembre 2026."""
        from datetime import datetime, timedelta
        from quantbot.surveillance import bilan
        recent = (datetime.now() - timedelta(minutes=10)).isoformat()
        b = bilan({"dernier_passage": datetime.now().isoformat(), "dernier_resultat": "ok"},
                  {"conforme": True}, {"ok": True},
                  etat_coupe_circuit={"echecs_consecutifs": 0,
                                      "dernier_succes": recent,
                                      "derniere_tentative": recent})
        assert b["coupe_circuit"]["niveau"] == "normal"


class TestLeCoupeCircuitQuiNeTourneePlus:
    """Le trou trouve le 21 septembre 2026, dans le correctif de la veille.

    `lancer_coupe_circuit.ps1` pointait sur `C:\\Program Files\\Python38\\
    python.exe`, qui n'existe pas sur cette machine. La tache echouait donc
    AVANT Python, et n'ecrivait rien du tout - ni journal, ni compteur.

    Un compteur d'echecs ne voit pas ca : il reste a zero. Un coupe-circuit qui
    ne s'execute plus passait donc pour operationnel. C'est le meme mode de
    defaillance que les deux precedents, au troisieme etage : la panne qui
    ressemble a un fonctionnement, puis la surveillance de cette panne qui
    ressemble elle aussi a un fonctionnement.
    """

    def _etat(self, minutes, echecs=0):
        from datetime import datetime, timedelta
        t = datetime.now() - timedelta(minutes=minutes)
        return {"derniere_tentative": t.isoformat(), "echecs_consecutifs": echecs,
                "dernier_succes": t.isoformat()}

    def test_un_passage_recent_est_normal(self):
        from quantbot.surveillance import sante_coupe_circuit
        assert sante_coupe_circuit(self._etat(5))["niveau"] == "normal"

    def test_un_silence_moyen_est_une_attention(self):
        """Hors seance, un silence est normal : on ne crie pas encore."""
        from quantbot.surveillance import sante_coupe_circuit
        s = sante_coupe_circuit(self._etat(120))
        assert s["niveau"] == "attention"
        assert "normal" in s["message"], "le message doit dire pourquoi on doute"

    def test_un_silence_LONG_est_une_alerte(self):
        from quantbot.surveillance import sante_coupe_circuit
        s = sante_coupe_circuit(self._etat(60 * 24))
        assert s["niveau"] == "alerte"
        assert "MUET" in s["message"]

    def test_l_alerte_nomme_la_cause_la_plus_probable(self):
        """Elle doit pointer vers l'interpreteur : c'est la cause reelle
        observee, et elle est invisible autrement."""
        from quantbot.surveillance import sante_coupe_circuit
        s = sante_coupe_circuit(self._etat(60 * 48))
        assert "interpreteur" in s["message"]

    def test_le_silence_prime_sur_le_compteur_a_zero(self):
        """Le coeur du trou : echecs = 0 ET muet depuis deux jours ne doit pas
        donner « operationnel »."""
        from quantbot.surveillance import sante_coupe_circuit
        s = sante_coupe_circuit(self._etat(60 * 48, echecs=0))
        assert s["niveau"] == "alerte"
        assert "operationnel" not in s["message"]

    def test_le_bilan_fait_remonter_le_silence(self):
        from quantbot.surveillance import bilan
        b = bilan({"dernier_passage": "2026-09-21T21:00:00", "dernier_resultat": "ok"},
                  {"conforme": True}, {"ok": True},
                  etat_coupe_circuit=self._etat(60 * 24))
        assert b["niveau"] == "alerte" and "MUET" in b["resume"]

    def test_un_etat_vide_reste_normal(self):
        """Jamais lance n'est pas une panne : il ne faut pas crier avant la
        premiere installation."""
        from quantbot.surveillance import sante_coupe_circuit
        assert sante_coupe_circuit({})["niveau"] == "normal"


class TestFuseauxHoraires:
    """Le bug que les tests precedents ont manque.

    `coupe_circuit.py` horodate en UTC AVEC fuseau ; `robot_etat.json` en heure
    locale SANS fuseau. Soustraire l'un de l'autre leve `TypeError: can't
    subtract offset-naive and offset-aware datetimes`, et la veille PLANTE au
    lieu de surveiller.

    Mes tests ne l'avaient pas vu parce que leurs horodatages etaient tous
    naifs. Ce sont les vraies donnees de `data/coupe_circuit_etat.json` qui
    l'ont montre - et une surveillance qui tombe en panne sur le format de sa
    propre entree est pire qu'une surveillance absente.
    """

    def test_un_horodatage_AVEC_fuseau_ne_plante_pas(self):
        from datetime import datetime, timedelta, timezone
        from quantbot.surveillance import sante_coupe_circuit
        t = datetime.now(timezone.utc) - timedelta(minutes=10)
        s = sante_coupe_circuit({"derniere_tentative": t.isoformat(),
                                 "echecs_consecutifs": 0})
        assert s["niveau"] == "normal"

    def test_un_horodatage_SANS_fuseau_ne_plante_pas(self):
        from datetime import datetime, timedelta
        from quantbot.surveillance import sante_coupe_circuit
        t = datetime.now() - timedelta(minutes=10)
        s = sante_coupe_circuit({"derniere_tentative": t.isoformat(),
                                 "echecs_consecutifs": 0})
        assert s["niveau"] == "normal"

    def test_les_deux_formats_donnent_le_MEME_verdict(self):
        """Le format de l'horodatage ne doit rien changer au diagnostic."""
        from datetime import datetime, timedelta, timezone
        from quantbot.surveillance import sante_coupe_circuit
        for minutes, attendu in ((10, "normal"), (120, "attention"), (60*24, "alerte")):
            aware = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            naif = datetime.now() - timedelta(minutes=minutes)
            a = sante_coupe_circuit({"derniere_tentative": aware.isoformat()})
            n = sante_coupe_circuit({"derniere_tentative": naif.isoformat()})
            assert a["niveau"] == n["niveau"] == attendu, (minutes, a, n)

    def test_le_vrai_fichier_du_depot_est_lisible(self):
        """Regression directe : l'etat reellement present sur la machine le
        21 septembre 2026 faisait planter la veille."""
        from pathlib import Path
        from quantbot.surveillance import lire_etat, sante_coupe_circuit
        f = Path(__file__).resolve().parent.parent / "data" / "coupe_circuit_etat.json"
        if not f.exists():
            import pytest
            pytest.skip("pas d'etat de production dans cet environnement")
        s = sante_coupe_circuit(lire_etat(f))       # ne doit pas lever
        assert s["niveau"] in ("normal", "attention", "alerte")
