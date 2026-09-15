"""Le tableau de bord peut envoyer des ordres : ses garde-fous sont testes.

Un bouton dans un navigateur est plus facile a cliquer qu'une commande a
taper. Les controles doivent donc etre au moins aussi stricts - et surtout,
`forcer` ne doit lever que les contraintes de CALENDRIER, jamais celles qui
portent sur la justesse des donnees.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.operations import Operations


def _ohlcv(close, index):
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1e6}, index=index)


@pytest.fixture
def panneau():
    """Univers de douze titres, cours du jour a 100."""
    idx = pd.bdate_range("2020-01-01", periods=900)
    rng = np.random.default_rng(3)
    prix = {}
    for i in range(12):
        serie = 100 * np.exp(rng.normal(0.0002 + i * 0.00004, 0.01, 900).cumsum())
        serie = serie / serie[-1] * 100.0
        prix["T%02d" % i] = _ohlcv(serie, idx)
    prix["^BENCH"] = _ohlcv(100 * np.exp(np.linspace(0, 0.5, 900)), idx)
    return prix


class FauxApi:
    simulation = True

    def __init__(self, positions=None, marche=None, cash=100_000.0, ouvert=True,
                 en_vol=None, equity=100_000.0, veille=100_000.0):
        self._positions = positions or {}
        self._marche = marche or {}
        self.cash = cash
        self.ouvert = ouvert
        self.en_vol = list(en_vol or [])
        self.equity = equity
        self.veille = veille
        self.envoyes = []

    def compte(self):
        return {"account_number": "PATEST1", "status": "ACTIVE", "currency": "USD",
                "equity": str(self.equity), "last_equity": str(self.veille),
                "cash": str(self.cash), "buying_power": "200000",
                "trading_blocked": False, "account_blocked": False}

    def horloge(self):
        return {"is_open": self.ouvert, "next_open": "2026-09-09T09:30:00-04:00"}

    def positions(self):
        return dict(self._positions)

    def positions_detail(self):
        return [{"symbol": t, "qty": str(q),
                 "market_value": str(self._marche.get(t, 100.0 * q)),
                 "unrealized_pl": "0"} for t, q in self._positions.items()]

    def ordres_ouverts(self):
        return list(self.en_vol)

    def annuler_ordres(self):
        return None

    def cotations(self, tickers, feed="iex"):
        return {t: (99.95, 100.05, 100.0) for t in tickers}

    def envoyer_ordre(self, ticker, sens, quantite=None, montant=None, **kw):
        self.envoyes.append((ticker, sens))
        return {"id": "x%d" % len(self.envoyes), "status": "accepted"}


def _ops(cfg, panneau, api):
    o = Operations(cfg, lambda: panneau)
    o._api = api
    return o


@pytest.fixture
def cfg_ops(base_config):
    return base_config.with_overrides({
        "universe.benchmark": "^BENCH", "data.min_history_days": 300,
        "portfolio.top_n": 5, "regime.enabled": False})


def _controle(etat, nom):
    return next(c for c in etat["controles"] if c["nom"] == nom)


class TestControles:
    def test_les_controles_sont_presents(self, cfg_ops, panneau):
        etat = _ops(cfg_ops, panneau, FauxApi()).etat()
        noms = [c["nom"] for c in etat["controles"]]
        for attendu in ("Donnees a jour", "Jour de rebalancement", "Marche ouvert",
                        "Echange sous le plafond", "Aucun achat a credit",
                        "Valorisation coherente", "Compte non bloque",
                        "Aucun ordre en attente"):
            assert attendu in noms

    def test_une_valorisation_divergente_est_signalee(self, cfg_ops, panneau):
        """Le courtier marque au marche, le plan au cache. Un ecart important
        veut dire que le cache est perime."""
        api = FauxApi(positions={"T00": 10.0}, marche={"T00": 5000.0})   # 10 x 100 != 5000
        etat = _ops(cfg_ops, panneau, api).etat()
        assert _controle(etat, "Valorisation coherente")["ok"] is False

    def test_une_valorisation_coherente_passe(self, cfg_ops, panneau):
        api = FauxApi(positions={"T00": 10.0}, marche={"T00": 1000.0})   # 10 x 100
        etat = _ops(cfg_ops, panneau, api).etat()
        assert _controle(etat, "Valorisation coherente")["ok"] is True

    def test_un_achat_a_credit_est_detecte(self, cfg_ops, panneau):
        api = FauxApi(cash=10.0)          # il faut acheter 100 000 avec 10 de liquidites
        etat = _ops(cfg_ops, panneau, api).etat()
        assert _controle(etat, "Aucun achat a credit")["ok"] is False


class TestEnvoi:
    def test_rien_ne_part_si_un_controle_bloque(self, cfg_ops, panneau):
        api = FauxApi(cash=10.0)
        rep = _ops(cfg_ops, panneau, api).envoyer()
        assert rep["ok"] is False
        assert api.envoyes == [], "aucun ordre ne doit avoir ete envoye"

    def test_forcer_ne_leve_que_le_calendrier(self, cfg_ops, panneau):
        """Le contournement porte sur le confort, jamais sur la justesse."""
        api = FauxApi(cash=10.0, ouvert=False)
        rep = _ops(cfg_ops, panneau, api).envoyer(forcer=True)
        assert rep["ok"] is False
        noms = [c["nom"] for c in rep["controles"]]
        assert "Aucun achat a credit" in noms
        assert "Marche ouvert" not in noms, "celui-la est contournable"
        assert api.envoyes == []

    def test_le_journal_porte_le_compte_et_la_fourchette(self, cfg_ops, panneau, tmp_path,
                                                         monkeypatch):
        import quantbot.operations as ops_mod
        journal = tmp_path / "j.csv"
        monkeypatch.setattr(ops_mod, "JOURNAL", journal)
        api = FauxApi()
        ops = _ops(cfg_ops, panneau, api)
        # donnees anciennes : on neutralise ce seul controle pour tester l'envoi
        monkeypatch.setattr(ops, "etat",
                            lambda signal=None, _v=ops.etat: _neutralise(_v(signal=signal)))
        rep = ops.envoyer(forcer=True)
        assert rep["envoyes"] > 0 and api.envoyes
        import csv
        lignes = list(csv.DictReader(journal.open(encoding="utf-8")))
        assert all(l["compte"] == "PATEST1" for l in lignes)
        assert all(l["cours_marche"] for l in lignes), "la fourchette doit etre relevee"
        assert all(l["mode"] == "simulation" for l in lignes)


def _neutralise(etat):
    for c in etat["controles"]:
        c["ok"] = True
    return etat


class TestDecouvert:
    def test_un_solde_negatif_est_signale(self, cfg_ops, panneau):
        etat = _ops(cfg_ops, panneau, FauxApi(cash=-2245.0)).etat()
        assert _controle(etat, "Compte sans decouvert")["ok"] is False

    def test_les_ventes_qui_resorbent_le_decouvert_restent_possibles(self, cfg_ops, panneau):
        """Bloquer sur un decouvert deja constitue empecherait d'en sortir :
        les ventes sont precisement le seul moyen de le reduire."""
        api = FauxApi(positions={"T00": 500.0}, marche={"T00": 50000.0}, cash=-2245.0)
        rep = _ops(cfg_ops, panneau, api).envoyer(forcer=True)
        noms = [c["nom"] for c in rep.get("controles", [])]
        assert "Compte sans decouvert" not in noms


class TestSignalImpose:
    """Le robot decide sur une fin de mois, parfois plusieurs seances apres.
    Si `envoyer` recalculait la cible sur la seance du jour, il enverrait une
    autre decision que celle qui vient d'etre validee."""

    def test_le_signal_impose_change_la_date_de_decision(self, cfg_ops, panneau):
        ops = _ops(cfg_ops, panneau, FauxApi())
        index = panneau["T00"].index
        impose = index[-6]
        sans = ops.etat()
        avec = ops.etat(signal=impose)
        assert avec["date_decision"] == str(impose.date())
        assert sans["date_decision"] != avec["date_decision"]

    def test_le_journal_enregistre_la_date_de_decision(self, cfg_ops, panneau, tmp_path,
                                                       monkeypatch):
        import csv
        import quantbot.operations as ops_mod
        journal = tmp_path / "j.csv"
        monkeypatch.setattr(ops_mod, "JOURNAL", journal)
        ops = _ops(cfg_ops, panneau, FauxApi())
        impose = panneau["T00"].index[-6]
        monkeypatch.setattr(ops, "etat",
                            lambda signal=None, _v=ops.etat: _neutralise(_v(signal=signal)))
        ops.envoyer(forcer=True, signal=impose)
        lignes = list(csv.DictReader(journal.open(encoding="utf-8")))
        assert lignes and all(l["date_cours"] == str(impose.date()) for l in lignes)


class TestOrdresEnVol:
    """Un ordre encore en attente chez le courtier immobilise les titres.

    Incident reel du 2026-09-09 : le bot a demande la vente de 19,877273 CRL
    alors qu'un ordre identique etait deja en vol. Alpaca a repondu
    `insufficient qty available` - `held_for_orders: 19.877273`, disponible
    0,000000363. `positions()` comptait pourtant la ligne comme entierement
    detenue. Dans l'autre sens le risque est pire : deux achats successifs sur
    la meme ligne peuvent tous les deux s'executer et doubler l'exposition.
    """

    EN_VOL = [{"symbol": "T03", "qty": "5", "side": "sell"}]

    def test_le_controle_echoue_quand_un_ordre_est_en_vol(self, cfg_ops, panneau):
        etat = _ops(cfg_ops, panneau, FauxApi(en_vol=self.EN_VOL)).etat()
        c = _controle(etat, "Aucun ordre en attente")
        assert not c["ok"]
        assert "T03" in c["detail"]

    def test_le_controle_passe_quand_rien_n_est_en_vol(self, cfg_ops, panneau):
        etat = _ops(cfg_ops, panneau, FauxApi()).etat()
        assert _controle(etat, "Aucun ordre en attente")["ok"]

    def test_rien_n_est_envoye_par_dessus_des_ordres_en_vol(self, cfg_ops, panneau):
        api = FauxApi(en_vol=self.EN_VOL)
        res = _ops(cfg_ops, panneau, api).envoyer(forcer=False)
        assert not res["ok"]
        assert "Aucun ordre en attente" in res["message"]
        assert api.envoyes == []

    def test_forcer_ne_leve_PAS_ce_controle(self, cfg_ops, panneau):
        """Aucune urgence ne justifie de doubler une position."""
        api = FauxApi(en_vol=self.EN_VOL)
        res = _ops(cfg_ops, panneau, api).envoyer(forcer=True)
        assert not res["ok"]
        assert "Aucun ordre en attente" in res["message"]
        assert api.envoyes == []

    def test_un_courtier_qui_ne_repond_pas_ne_bloque_pas_tout(self, cfg_ops, panneau):
        """Si la liste des ordres ouverts est inaccessible, on ne fabrique pas
        un blocage supplementaire : les autres controles font deja leur office."""
        class ApiMuette(FauxApi):
            def ordres_ouverts(self):
                raise RuntimeError("endpoint indisponible")

        etat = _ops(cfg_ops, panneau, ApiMuette()).etat()
        assert _controle(etat, "Aucun ordre en attente")["ok"]


class TestPouls:
    """Le battement qui alimente le rafraichissement automatique.

    Sa seule raison d'exister est d'etre BON MARCHE : `etat()` recalcule le
    score composite sur tout l'univers et prend le verrou du moteur. Interroge
    toutes les minutes, il gelerait la vue analyse a chaque passage.
    """

    def test_ne_recalcule_jamais_le_portefeuille(self, cfg_ops, panneau):
        """Le test qui protege la propriete essentielle : si `pouls()` touchait
        au chargement des cours, il aurait le meme cout que `etat()` et tout
        l'interet du dispositif disparaitrait."""
        appels = []

        def charger():
            appels.append(1)
            return panneau

        o = Operations(cfg_ops, charger)
        o._api = FauxApi(positions={"T01": 5.0})
        o.pouls(journal="/inexistant", etat_robot="/inexistant")
        assert appels == [], "pouls() a charge les cours : il n'est plus bon marche"

    def test_empreinte_stable_quand_rien_ne_bouge(self, cfg_ops, panneau, tmp_path):
        api = FauxApi(positions={"T01": 5.0})
        o = _ops(cfg_ops, panneau, api)
        a = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")
        b = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")
        assert a["empreinte"] == b["empreinte"]
        assert a["ok"] and a["n_positions"] == 1

    def test_une_position_qui_change_change_l_empreinte(self, cfg_ops, panneau, tmp_path):
        api = FauxApi(positions={"T01": 5.0})
        o = _ops(cfg_ops, panneau, api)
        avant = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")["empreinte"]
        api._positions["T01"] = 6.0          # un ordre vient d'etre execute
        apres = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")["empreinte"]
        assert avant != apres

    def test_un_ordre_en_vol_change_l_empreinte_et_se_compte(self, cfg_ops, panneau, tmp_path):
        api = FauxApi(positions={"T01": 5.0})
        o = _ops(cfg_ops, panneau, api)
        avant = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")
        api.en_vol = [{"id": "abc", "symbol": "T02"}]
        apres = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")
        assert avant["empreinte"] != apres["empreinte"]
        assert avant["n_en_vol"] == 0 and apres["n_en_vol"] == 1

    def test_une_ecriture_du_robot_au_journal_change_l_empreinte(self, cfg_ops, panneau, tmp_path):
        """Le cas « transaction du bot » : le robot ecrit dans le journal
        pendant que l'ecran est ouvert."""
        journal = tmp_path / "j.csv"
        journal.write_text("horodatage\n", encoding="utf-8")
        o = _ops(cfg_ops, panneau, FauxApi())
        avant = o.pouls(journal=journal, etat_robot=tmp_path / "r.json")["empreinte"]
        journal.write_text("horodatage\n2026-09-13T21:00:00\n", encoding="utf-8")
        apres = o.pouls(journal=journal, etat_robot=tmp_path / "r.json")["empreinte"]
        assert avant != apres

    def test_le_courtier_injoignable_ne_leve_pas(self, cfg_ops, panneau, tmp_path):
        """Un battement qui plante couperait la surveillance au moment ou elle
        sert le plus."""
        class ApiMorte(FauxApi):
            def compte(self):
                raise RuntimeError("reseau coupe")

        o = _ops(cfg_ops, panneau, ApiMorte())
        p = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")
        assert p["ok"] is False and "reseau coupe" in p["erreur"]
        assert p["empreinte"]

    def test_l_empreinte_ne_bouge_pas_pour_une_poussiere(self, cfg_ops, panneau, tmp_path):
        """Une variation sous le millionieme de titre ne doit pas declencher un
        rafraichissement : l'ecran clignoterait en permanence."""
        api = FauxApi(positions={"T01": 5.0})
        o = _ops(cfg_ops, panneau, api)
        avant = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")["empreinte"]
        api._positions["T01"] = 5.0 + 1e-9
        apres = o.pouls(journal=tmp_path / "j.csv", etat_robot=tmp_path / "r.json")["empreinte"]
        assert avant == apres


class TestHorlogeLisible:
    """L'affichage de l'horaire de marche.

    Bug reel : le detail affichait `next_open[:16]`, ce qui coupait exactement
    le decalage horaire. Le courtier renvoyant ses horaires en heure de New
    York ('2026-09-14T09:30:00-04:00'), l'ecran annoncait "ouverture
    2026-09-14T09:30" a un utilisateur parisien pour qui il etait midi - et
    pour qui 09:30 etait donc deja passe depuis longtemps. Le controle etait
    juste, l'affichage le faisait passer pour faux.
    """

    OUVERTURE = "2026-09-14T09:30:00-04:00"     # 15:30 a Paris

    def _rendu(self, horloge):
        import os
        import time
        from quantbot.operations import horloge_lisible
        avant = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Paris"
        if hasattr(time, "tzset"):
            time.tzset()
        try:
            return horloge_lisible(horloge)
        finally:
            if avant is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = avant
            if hasattr(time, "tzset"):
                time.tzset()

    def test_l_heure_est_convertie_dans_le_fuseau_local(self):
        texte = self._rendu({"is_open": False,
                             "timestamp": "2026-09-14T06:18:00-04:00",
                             "next_open": self.OUVERTURE})
        assert "15:30" in texte, texte
        assert "09:30" not in texte, "l'heure de New York est affichee telle quelle"

    def test_le_delai_relatif_est_donne(self):
        """La partie qui ne peut pas etre mal lue : elle ne depend d'aucun fuseau."""
        texte = self._rendu({"is_open": False,
                             "timestamp": "2026-09-14T06:18:00-04:00",
                             "next_open": self.OUVERTURE})
        assert "dans 3 h 12" in texte, texte

    def test_marche_ouvert_annonce_la_fermeture(self):
        texte = self._rendu({"is_open": True,
                             "timestamp": "2026-09-14T10:30:00-04:00",
                             "next_close": "2026-09-14T16:00:00-04:00"})
        assert texte.startswith("ouvert") and "22:00" in texte, texte

    def test_ouverture_un_autre_jour(self):
        texte = self._rendu({"is_open": False,
                             "timestamp": "2026-09-11T17:00:00-04:00",
                             "next_open": self.OUVERTURE})
        assert "le 14/09" in texte and "15:30" in texte, texte

    def test_nanosecondes_supportees(self):
        """Alpaca horodate a la nanoseconde ; `fromisoformat` plafonne a six
        chiffres en 3.8."""
        texte = self._rendu({"is_open": False,
                             "timestamp": "2026-09-14T06:18:00.123456789-04:00",
                             "next_open": self.OUVERTURE})
        assert "15:30" in texte, texte

    def test_horloge_incomplete_ne_leve_pas(self):
        assert self._rendu({"is_open": False}) == "ferme"
        assert self._rendu({"is_open": True}) == "ouvert"
        assert self._rendu({"is_open": False, "next_open": "n'importe quoi"}) == "ferme"


class TestEmpreinteMarche:
    def test_l_ouverture_du_marche_change_l_empreinte(self, cfg_ops, panneau, tmp_path):
        """Sans cela, la bascule ouvert/ferme n'atteint l'ecran que si un cours
        a bouge entre-temps."""
        api = FauxApi()
        o = _ops(cfg_ops, panneau, api)
        ferme = o.pouls(journal=tmp_path / "j", etat_robot=tmp_path / "r")
        api.ouvert = False
        ouvert_apres = o.pouls(journal=tmp_path / "j", etat_robot=tmp_path / "r")
        assert ferme["empreinte"] != ouvert_apres["empreinte"]
        assert ferme["marche_ouvert"] is True and ouvert_apres["marche_ouvert"] is False


class TestGardeFousDuDefi:
    """Les limites de perte, au format des defis de prop firm.

    Ce ne sont pas des signaux : elles n'influencent jamais le choix des
    titres. Elles empechent seulement d'envoyer des ordres quand une limite
    est franchie - et `forcer` ne les leve pas, parce qu'aucune urgence ne
    justifie de passer outre une limite de perte.
    """

    def _cfg(self, base, **kw):
        cfg = base.copy()
        cfg.set("defi.active", True, strict=True)
        for k, v in kw.items():
            cfg.set("defi." + k, v, strict=True)
        return cfg

    def test_absent_quand_le_mode_est_inactif(self, cfg_ops, panneau):
        etat = _ops(cfg_ops, panneau, FauxApi()).etat()
        assert all(c["nom"] != "Limites du defi" for c in etat["controles"])

    def test_present_et_vert_sur_un_compte_sain(self, cfg_ops, panneau, tmp_path,
                                                monkeypatch):
        from quantbot import defi
        monkeypatch.setattr(defi, "ETAT_DEFI", tmp_path / "d.json")
        etat = _ops(self._cfg(cfg_ops), panneau, FauxApi()).etat()
        assert _controle(etat, "Limites du defi")["ok"]

    def test_une_perte_journaliere_bloque_l_envoi(self, cfg_ops, panneau, tmp_path,
                                                  monkeypatch):
        from quantbot import defi
        monkeypatch.setattr(defi, "ETAT_DEFI", tmp_path / "d.json")
        api = FauxApi(equity=95_000.0, veille=100_000.0)     # -5 %
        res = _ops(self._cfg(cfg_ops), panneau, api).envoyer(forcer=False)
        assert not res["ok"] and "Limites du defi" in res["message"]
        assert api.envoyes == []

    def test_forcer_ne_leve_PAS_la_limite(self, cfg_ops, panneau, tmp_path, monkeypatch):
        from quantbot import defi
        monkeypatch.setattr(defi, "ETAT_DEFI", tmp_path / "d.json")
        api = FauxApi(equity=95_000.0, veille=100_000.0)
        res = _ops(self._cfg(cfg_ops), panneau, api).envoyer(forcer=True)
        assert not res["ok"] and "Limites du defi" in res["message"]
        assert api.envoyes == []

    def test_les_references_survivent_au_redemarrage(self, cfg_ops, panneau,
                                                     tmp_path, monkeypatch):
        """Sans persistance, depart et plus-haut repartiraient de la valeur du
        jour a chaque lancement, et la limite ne voudrait plus rien dire."""
        from quantbot import defi
        chemin = tmp_path / "d.json"
        monkeypatch.setattr(defi, "ETAT_DEFI", chemin)

        _ops(self._cfg(cfg_ops), panneau,
             FauxApi(equity=110_000.0, veille=109_000.0)).etat()
        enregistre = defi.lire_etat(chemin)
        assert enregistre["capital_depart"] == 110_000.0
        assert enregistre["plus_haut"] == 110_000.0

        # nouvelle instance, comme apres un redemarrage : -9,1 % sous le depart
        etat = _ops(self._cfg(cfg_ops), panneau,
                    FauxApi(equity=100_000.0, veille=100_500.0)).etat()
        c = _controle(etat, "Limites du defi")
        assert not c["ok"] and "le depart" in c["detail"]

    def test_la_regle_glissante_est_plus_severe(self, cfg_ops, panneau, tmp_path,
                                                monkeypatch):
        """Meme situation, deux references : statique laisse passer, glissante
        bloque. Se tromper de regle a des consequences reelles."""
        from quantbot import defi
        for reference, attendu_ok in (("statique", True), ("glissante", False)):
            chemin = tmp_path / ("d_%s.json" % reference)
            monkeypatch.setattr(defi, "ETAT_DEFI", chemin)
            defi.ecrire_etat({"capital_depart": 100_000.0, "plus_haut": 110_000.0}, chemin)
            cfg = self._cfg(cfg_ops, reference=reference)
            etat = _ops(cfg, panneau, FauxApi(equity=101_000.0, veille=101_500.0)).etat()
            c = _controle(etat, "Limites du defi")
            assert c["ok"] is attendu_ok, (reference, c["detail"])
