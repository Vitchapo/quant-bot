"""La passerelle MT5, testee sans MT5.

`MetaTrader5` n'existe qu'en roue Windows. Sans double, ce module - le seul du
projet qui envoie des ordres a de l'argent - serait le seul non teste. Le
double (`tests/faux_mt5.py`) reproduit ce qui casse en vrai : pas de volume,
masques de remplissage, codes retour, et la fermeture par ordre oppose.
"""
from __future__ import annotations

import pytest

from faux_mt5 import (ACCOUNT_TRADE_MODE_REAL, ORDER_FILLING_IOC,
                      ORDER_FILLING_RETURN, SYMBOL_FILLING_FOK,
                      SYMBOL_FILLING_IOC, FauxMT5)
from quantbot import mt5broker
from quantbot.broker import BrokerError, LiveRefuse


TICKERS = ["AAPL", "MSFT", "NVDA", "US500.cash"]


@pytest.fixture
def api():
    faux = FauxMT5()
    a = mt5broker.MT5(mt5=faux)
    a.initialiser()
    a.construire_carte(TICKERS)
    return a


class TestConnexion:
    def test_initialisation_reussie(self):
        a = mt5broker.MT5(mt5=FauxMT5())
        info = a.initialiser()
        assert info["simulation"] is True
        assert info["serveur"] == "FTMO-Demo"

    def test_initialize_qui_echoue_est_explicite(self):
        a = mt5broker.MT5(mt5=FauxMT5(init_ok=False))
        with pytest.raises(mt5broker.MT5Indisponible, match="initialize"):
            a.initialiser()

    def test_terminal_ferme(self):
        a = mt5broker.MT5(mt5=FauxMT5(terminal_ok=False))
        with pytest.raises(mt5broker.MT5Indisponible, match="terminal"):
            a.initialiser()

    def test_LE_piege_autotrading_desactive(self):
        """Celui qu'on oublie : `initialize()` reussit, le compte s'affiche,
        tout semble en place, et chaque ordre revient en 10027 parce qu'une
        case n'est pas cochee dans les options du terminal."""
        a = mt5broker.MT5(mt5=FauxMT5(trade_allowed=False))
        with pytest.raises(mt5broker.MT5Indisponible, match="trading automatique"):
            a.initialiser()

    def test_un_compte_reel_est_refuse_sans_les_verrous(self, base_config):
        faux = FauxMT5(trade_mode=ACCOUNT_TRADE_MODE_REAL)
        cfg = base_config.copy()
        with pytest.raises(LiveRefuse, match="REEL"):
            mt5broker.connecter(cfg, reel=False, mt5=faux)
        assert faux.arrete, "la connexion doit etre refermee avant de lever"

    def test_le_repr_ne_fuit_aucun_identifiant(self, api):
        t = repr(api)
        assert "12345678" not in t and "FTMO-Demo" not in t


class TestResolutionDesSymboles:
    """Le nom du symbole n'est pas le ticker, et rien ne permet de le deviner."""

    def test_le_suffixe_est_trouve_tout_seul(self, api):
        assert api.symbole("AAPL") == "AAPL.US"

    def test_un_nom_natif_passe_tel_quel(self, api):
        assert api.symbole("US500.cash") == "US500.cash"

    def test_UN_TICKER_HORS_CATALOGUE_NE_PLANTE_PAS(self):
        """Le comportement demande : un titre du S&P 500 absent du catalogue
        FTMO est ECARTE au demarrage, pas au moment de l'ordre - sinon une
        ligne manquante ferait echouer le rebalancement entier."""
        a = mt5broker.MT5(mt5=FauxMT5())
        a.initialiser()
        carte = a.construire_carte(["AAPL", "KO", "XOM", "MSFT"])
        assert set(carte) == {"AAPL", "MSFT"}
        assert a.absents == {"KO", "XOM"}
        assert a.symbole("KO") is None

    def test_les_cotations_omettent_les_absents_sans_lever(self, api):
        cot = api.cotations(["AAPL", "KO", "MSFT"])
        assert set(cot) == {"AAPL", "MSFT"}

    def test_negociables_repond_faux_au_lieu_de_lever(self, api):
        assert api.negociables(["AAPL", "KO"]) == {"AAPL": True, "KO": False}


class TestFormatAlpaca:
    """`operations.py` ne doit pas voir la difference : meme huit methodes,
    memes cles dans les dictionnaires."""

    def test_les_huit_methodes_existent(self, api):
        for nom in ("compte", "horloge", "positions", "positions_detail",
                    "ordres_ouverts", "cotations", "envoyer_ordre", "annuler_ordres"):
            assert callable(getattr(api, nom, None)), nom

    def test_compte_expose_les_cles_attendues(self, api):
        c = api.compte()
        for k in ("account_number", "status", "currency", "equity", "cash",
                  "buying_power", "last_equity", "trading_blocked", "account_blocked"):
            assert k in c, k

    def test_last_equity_est_le_SOLDE_pas_l_equity(self):
        """Regle FTMO : la perte journaliere se mesure sur le solde a 00:00 CET.

        Prendre l'equity comme reference donnerait une limite plus haute que la
        vraie quand du latent positif traine - un garde-fou qui laisse passer.
        """
        faux = FauxMT5(solde=100_000.0, equity=103_000.0)
        a = mt5broker.MT5(mt5=faux); a.initialiser()
        c = a.compte()
        assert c["last_equity"] == 100_000.0
        assert c["equity"] == 103_000.0
        assert c["profit_flottant"] == 3_000.0

    def test_positions_rend_des_tickers_projet(self, api):
        api._mt5.ajouter_position("AAPL.US", 1.0)
        assert api.positions() == {"AAPL": 1.0}

    def test_une_position_vendeuse_est_negative(self, api):
        api._mt5.ajouter_position("AAPL.US", 2.0, vendeuse=True)
        assert api.positions() == {"AAPL": -2.0}

    def test_positions_detail_valorise_en_notionnel(self, api):
        api._mt5.ajouter_position("AAPL.US", 3.0)
        d = api.positions_detail()[0]
        assert d["symbol"] == "AAPL"
        assert float(d["market_value"]) == pytest.approx(3.0 * 180.0)


class TestVolumeEtPas:
    """Un volume non aligne sur `volume_step` est refuse par le serveur a
    chaque tentative, indefiniment. On arrondit AVANT d'envoyer."""

    def test_le_volume_est_aligne_sur_le_pas(self, api):
        r = api.volume_pour("AAPL", 1000.0)          # 1000/180 = 5.5555...
        assert r["volume"] == pytest.approx(5.55)

    def test_on_arrondit_VERS_LE_BAS(self, api):
        """Depasser le notionnel vise consomme une marge non prevue, et c'est
        la premiere cause de 10019."""
        r = api.volume_pour("AAPL", 1000.0)
        assert r["volume"] * 180.0 <= 1000.0

    def test_un_notionnel_trop_petit_renvoie_zero_sans_lever(self, api):
        r = api.volume_pour("AAPL", 0.50)
        assert r["volume"] == 0.0 and "trop petit" in r["raison"]

    def test_un_ticker_absent_renvoie_zero_sans_lever(self, api):
        r = api.volume_pour("KO", 1000.0)
        assert r["volume"] == 0.0 and r["raison"] == "hors catalogue"

    def test_le_serveur_accepte_le_volume_calcule(self, api):
        """Le test de bout en bout : ce que `volume_pour` produit doit passer
        le controle de pas du serveur."""
        v = api.volume_pour("AAPL", 5000.0)["volume"]
        rep = api.envoyer_ordre("AAPL", "buy", quantite=v)
        assert rep["retcode"] == 10009


class TestModeDeRemplissage:
    """`filling_mode` est un masque. Les actions n'exposent souvent ni FOK ni
    IOC, seulement RETURN. Envoyer le mauvais donne 10030, ce qui ressemble a
    un bug de code alors que c'est une propriete du symbole."""

    def test_ioc_choisi_quand_le_symbole_l_expose(self, api):
        api.envoyer_ordre("AAPL", "buy", quantite=0.1)
        assert api._mt5.envoyes[-1]["type_filling"] == ORDER_FILLING_IOC

    def test_return_choisi_quand_NI_fok_NI_ioc(self):
        faux = FauxMT5(filling_mode=0)
        a = mt5broker.MT5(mt5=faux); a.initialiser(); a.construire_carte(["AAPL"])
        a.envoyer_ordre("AAPL", "buy", quantite=0.1)
        assert faux.envoyes[-1]["type_filling"] == ORDER_FILLING_RETURN

    def test_le_serveur_ne_refuse_jamais_notre_choix(self):
        """Pour chaque masque possible, le mode choisi doit passer."""
        for masque in (0, SYMBOL_FILLING_FOK, SYMBOL_FILLING_IOC,
                       SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC):
            faux = FauxMT5(filling_mode=masque)
            a = mt5broker.MT5(mt5=faux); a.initialiser(); a.construire_carte(["AAPL"])
            rep = a.envoyer_ordre("AAPL", "buy", quantite=0.1)
            assert rep["retcode"] == 10009, "masque %d refuse" % masque


class TestMarge:
    """Le levier actions est plafonne. On ne le calcule pas depuis une
    constante : on le DEMANDE au terminal."""

    def test_la_marge_vient_du_terminal(self, api):
        m = api.marge_requise("AAPL", "buy", 1.0)
        assert m == pytest.approx(180.0 * 1.0001 / 5, rel=1e-3)

    def test_marge_insuffisante_refusee_AVANT_l_envoi(self):
        """Le controle a priori : on ne laisse pas le serveur repondre 10019,
        on refuse et on l'explique."""
        faux = FauxMT5(marge_libre=50.0)
        a = mt5broker.MT5(mt5=faux); a.initialiser(); a.construire_carte(["AAPL"])
        with pytest.raises(BrokerError, match="marge insuffisante"):
            a.envoyer_ordre("AAPL", "buy", quantite=5.0)
        assert faux.envoyes == [], "aucun ordre ne doit avoir ete envoye"

    def test_le_message_dit_combien_il_manque(self):
        faux = FauxMT5(marge_libre=50.0)
        a = mt5broker.MT5(mt5=faux); a.initialiser(); a.construire_carte(["AAPL"])
        with pytest.raises(BrokerError) as e:
            a.envoyer_ordre("AAPL", "buy", quantite=5.0)
        assert "requis" in str(e.value) and "libre" in str(e.value)

    def test_un_levier_plus_faible_demande_plus_de_marge(self):
        faible = FauxMT5(); faible.levier = 2
        fort = FauxMT5(); fort.levier = 20
        out = []
        for faux in (faible, fort):
            a = mt5broker.MT5(mt5=faux); a.initialiser(); a.construire_carte(["AAPL"])
            out.append(a.marge_requise("AAPL", "buy", 1.0))
        assert out[0] > out[1], "un levier 1:2 doit couter plus de marge que 1:20"


class TestOrdres:
    def test_achat_avec_sl_et_tp(self, api):
        rep = api.envoyer_ordre("AAPL", "buy", quantite=0.5, sl=170.0, tp=200.0)
        req = api._mt5.envoyes[-1]
        assert rep["retcode"] == 10009
        assert req["sl"] == 170.0 and req["tp"] == 200.0
        assert req["magic"] == 770315

    def test_vente(self, api):
        api.envoyer_ordre("AAPL", "sell", quantite=0.5)
        assert api._mt5.envoyes[-1]["type"] == 1

    def test_ordre_en_montant_converti_en_lots(self, api):
        api.envoyer_ordre("AAPL", "buy", montant=1000.0)
        assert api._mt5.envoyes[-1]["volume"] == pytest.approx(5.55)

    def test_quantite_ET_montant_ensemble_est_une_erreur(self, api):
        with pytest.raises(ValueError):
            api.envoyer_ordre("AAPL", "buy", quantite=1.0, montant=1000.0)

    def test_un_ticker_hors_catalogue_leve_clairement(self, api):
        with pytest.raises(BrokerError, match="catalogue"):
            api.envoyer_ordre("KO", "buy", quantite=1.0)

    def test_un_refus_serveur_est_traduit_en_francais(self, api):
        api._mt5.forcer_retcode = 10019
        with pytest.raises(BrokerError, match="marge insuffisante"):
            api.envoyer_ordre("AAPL", "buy", quantite=0.1)

    def test_le_code_10027_est_nomme(self, api):
        api._mt5.forcer_retcode = 10027
        with pytest.raises(BrokerError, match="AutoTrading"):
            api.envoyer_ordre("AAPL", "buy", quantite=0.1)


class TestFermeture:
    """MT5 ne connait pas d'ordre « close » : on ferme en envoyant l'ordre
    OPPOSE avec le champ `position`."""

    def test_fermer_une_position_acheteuse_envoie_une_vente(self, api):
        t = api._mt5.ajouter_position("AAPL.US", 1.0)
        api.fermer_position(t)
        req = api._mt5.envoyes[-1]
        assert req["type"] == 1 and req["position"] == t
        assert api.positions() == {}

    def test_fermer_une_position_vendeuse_envoie_un_achat(self, api):
        t = api._mt5.ajouter_position("AAPL.US", 1.0, vendeuse=True)
        api.fermer_position(t)
        assert api._mt5.envoyes[-1]["type"] == 0

    def test_fermer_une_position_deja_fermee_ne_leve_pas(self, api):
        assert api.fermer_position(999999)["ok"] is True

    def test_la_fermeture_ne_verifie_PAS_la_marge(self):
        """Sortir doit toujours etre possible. Verifier la marge avant de
        fermer bloquerait la liquidation au moment ou elle compte - un appel de
        marge est exactement le moment ou la marge libre est nulle."""
        faux = FauxMT5(marge_libre=0.0)
        a = mt5broker.MT5(mt5=faux); a.initialiser(); a.construire_carte(["AAPL"])
        t = faux.ajouter_position("AAPL.US", 1.0)
        assert a.fermer_position(t)["ok"] is True
        assert a.positions() == {}

    def test_fermer_tout_ferme_tout(self, api):
        api._mt5.ajouter_position("AAPL.US", 1.0)
        api._mt5.ajouter_position("MSFT.US", 2.0)
        api._mt5.ajouter_position("NVDA.US", 0.5, vendeuse=True)
        res = api.fermer_tout()
        assert res["ok"] and len(res["fermees"]) == 3
        assert api.positions() == {}

    def test_fermer_tout_itere_sur_une_PHOTO(self, api):
        """Fermer modifie la liste des positions. Iterer sur une collection qui
        change en sauterait la moitie - et une liquidation qui laisse des
        positions ouvertes n'est pas une liquidation."""
        for s in ("AAPL.US", "MSFT.US", "NVDA.US", "US500.cash"):
            api._mt5.ajouter_position(s, 1.0)
        assert len(api.fermer_tout()["fermees"]) == 4

    def test_un_echec_de_fermeture_est_rapporte_pas_avale(self, api):
        api._mt5.ajouter_position("AAPL.US", 1.0)
        api._mt5.forcer_retcode = 10018            # marche ferme
        res = api.fermer_tout()
        assert not res["ok"] and res["echecs"]

    def test_annuler_ordres_ne_touche_pas_aux_positions(self, api):
        api._mt5.ajouter_position("AAPL.US", 1.0)
        api._mt5.ajouter_ordre_en_attente("MSFT.US")
        api.annuler_ordres()
        assert api.ordres_ouverts() == []
        assert api.positions() == {"AAPL": 1.0}, "une position a ete fermee"


class TestHorloge:
    def test_un_tick_recent_vaut_marche_ouvert(self, api):
        h = api.horloge()
        assert h["is_open"] is True and h["reference"]

    def test_sans_symbole_de_reference_on_repond_ferme(self):
        a = mt5broker.MT5(mt5=FauxMT5(symboles={"XYZ": 1.0}))
        a.initialiser()
        h = a.horloge()
        assert h["is_open"] is False and h["reference"] is None
