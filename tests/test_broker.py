"""Les verrous de l'argent reel, et la discretion sur les identifiants.

Ces tests ne touchent aucun reseau : ils verifient que le code REFUSE de
partir en argent reel, et qu'un secret ne fuit ni dans un `repr` ni dans un
message d'erreur.
"""
from __future__ import annotations

import pytest

from quantbot import broker
from quantbot.broker import Alpaca, BrokerError, LiveRefuse, connecter, masquer

CLE = "PKTESTABCDEFGHIJ"
SECRET = "ceci-ne-doit-jamais-apparaitre"


class TestDiscretion:
    def test_le_repr_ne_montre_pas_le_secret(self):
        assert SECRET not in repr(Alpaca(CLE, SECRET))

    def test_le_masquage_cache_le_milieu(self):
        m = masquer(CLE)
        assert m.startswith("PKTE") and m.endswith(CLE[-3:]) and len(m) < len(CLE)

    def test_une_cle_courte_est_entierement_masquee(self):
        assert masquer("abc") == "***"


class TestVerrousArgentReel:
    def test_par_defaut_on_est_en_simulation(self, base_config, monkeypatch):
        monkeypatch.setenv("ALPACA_KEY_ID", CLE)
        monkeypatch.setenv("ALPACA_SECRET_KEY", SECRET)
        api = connecter(base_config, reel=False)
        assert api.simulation and api.base_url == broker.URL_SIMULATION

    def test_verrou_1_la_configuration(self, base_config, monkeypatch):
        monkeypatch.setenv("QUANTBOT_LIVE", broker.PHRASE_VERROU)
        with pytest.raises(LiveRefuse) as err:
            connecter(base_config.with_overrides({"broker.allow_live": False}), reel=True)
        assert "1/3" in str(err.value)

    def test_verrou_2_la_variable_d_environnement(self, base_config, monkeypatch):
        monkeypatch.delenv("QUANTBOT_LIVE", raising=False)
        with pytest.raises(LiveRefuse) as err:
            connecter(base_config.with_overrides({"broker.allow_live": True}), reel=True)
        assert "2/3" in str(err.value)

    def test_une_phrase_approchante_ne_suffit_pas(self, base_config, monkeypatch):
        monkeypatch.setenv("QUANTBOT_LIVE", broker.PHRASE_VERROU.upper())
        with pytest.raises(LiveRefuse):
            connecter(base_config.with_overrides({"broker.allow_live": True}), reel=True)

    def test_les_deux_verrous_ouverts_exigent_des_cles_LIVE_distinctes(self, base_config, monkeypatch):
        """Les cles de simulation ne doivent pas ouvrir le compte reel."""
        monkeypatch.setenv("QUANTBOT_LIVE", broker.PHRASE_VERROU)
        monkeypatch.setenv("ALPACA_KEY_ID", CLE)          # cles de SIMULATION
        monkeypatch.setenv("ALPACA_SECRET_KEY", SECRET)
        monkeypatch.delenv("ALPACA_KEY_ID_LIVE", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY_LIVE", raising=False)
        monkeypatch.setattr(broker, "FICHIER_SECRETS", broker.Path("secrets/_absent_.json"))
        with pytest.raises(BrokerError) as err:
            connecter(base_config.with_overrides({"broker.allow_live": True}), reel=True)
        assert "live" in str(err.value)


class TestOrdres:
    def test_il_faut_choisir_entre_quantite_et_montant(self):
        api = Alpaca(CLE, SECRET)
        with pytest.raises(ValueError):
            api.envoyer_ordre("AAPL", "buy")
        with pytest.raises(ValueError):
            api.envoyer_ordre("AAPL", "buy", quantite=1, montant=100)

    def test_le_corps_de_l_ordre_est_correctement_forme(self, monkeypatch):
        api = Alpaca(CLE, SECRET)
        vus = {}

        def faux(methode, chemin, **kw):
            import json
            vus["methode"], vus["chemin"] = methode, chemin
            vus["corps"] = json.loads(kw["data"])
            return {"id": "x", "status": "accepted"}

        monkeypatch.setattr(api, "_appel", faux)
        api.envoyer_ordre("AAPL", "sell", quantite=3.5)
        assert vus["chemin"] == "/v2/orders" and vus["corps"]["qty"] == "3.5"
        assert vus["corps"]["side"] == "sell" and vus["corps"]["type"] == "market"
        api.envoyer_ordre("MSFT", "buy", montant=250.0)
        assert vus["corps"]["notional"] == "250.0" and "qty" not in vus["corps"]

    def test_les_positions_sont_converties_en_nombres(self, monkeypatch):
        api = Alpaca(CLE, SECRET)
        monkeypatch.setattr(api, "_appel", lambda *a, **k: [
            {"symbol": "AAPL", "qty": "12.5"}, {"symbol": "MSFT", "qty": "-3"}])
        assert api.positions() == {"AAPL": 12.5, "MSFT": -3.0}
