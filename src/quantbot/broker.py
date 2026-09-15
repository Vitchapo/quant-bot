"""Connexion au courtier (Alpaca), en REST direct.

Pourquoi pas le SDK officiel
----------------------------
`alpaca-py` tire une dizaine de dependances et ses versions recentes exigent
Python 3.9+. L'API REST tient en quelques appels et `requests` est deja une
dependance du projet : on garde donc la main, et le code reste lisible - ce
qui compte quand il finira par passer de vrais ordres.

Les deux mondes
---------------
* SIMULATION (`https://paper-api.alpaca.markets`) - argent fictif, aucun
  document a fournir, un simple compte suffit. C'est le defaut, partout.
* REEL (`https://api.alpaca.markets`) - argent reel. Trois verrous
  INDEPENDANTS doivent etre ouverts pour y acceder (voir `connecter`), plus
  une confirmation tapee a la main dans `scripts/trade.py`. Aucun de ces
  verrous n'est franchissable par accident, par une faute de frappe ou par une
  tache planifiee.

Les identifiants ne sont jamais ecrits dans le depot, jamais journalises,
jamais affiches - meme partiellement dans les messages d'erreur.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

URL_SIMULATION = "https://paper-api.alpaca.markets"
URL_REEL = "https://api.alpaca.markets"

#: Donnees de marche. Le flux `iex` est accessible sans abonnement ; il ne
#: couvre qu'une partie du volume, ce qui suffit largement pour horodater une
#: fourchette au moment ou l'on envoie un ordre.
URL_DONNEES = "https://data.alpaca.markets"

#: Fichier de repli, hors depot (voir .gitignore). Cherche d'abord depuis le
#: dossier courant, puis depuis la racine du projet : lancer un script depuis
#: un autre repertoire ne doit pas faire disparaitre les identifiants.
FICHIER_SECRETS = Path("secrets/alpaca.json")
FICHIER_SECRETS_PROJET = Path(__file__).resolve().parent.parent.parent / "secrets" / "alpaca.json"

#: Valeurs du modele livre : elles ne sont pas des identifiants, et les
#: accepter enverrait l'utilisateur chercher un 401 incomprehensible.
_GABARITS = {"", "PKF...", "PK...", "AK...", "3Y...", "a-remplir", "à remplir",
             "colle-ta-cle-ici", "colle-ton-secret-ici"}

#: Valeur exacte attendue dans la variable d'environnement du verrou n.2.
PHRASE_VERROU = "oui-argent-reel"


class BrokerError(RuntimeError):
    """Erreur de dialogue avec le courtier."""


class LiveRefuse(BrokerError):
    """Le passage en argent reel a ete refuse par un des verrous."""


def _lire_identifiants(profil: str = "paper") -> tuple:
    """Identifiants depuis l'environnement, sinon depuis `secrets/alpaca.json`.

    Ordre de priorite : variables d'environnement d'abord, fichier ensuite.
    Les noms officiels d'Alpaca (`APCA_API_*`) sont acceptes en plus des noms
    courts, pour que le meme poste puisse servir a d'autres outils.
    """
    suffixe = "" if profil == "paper" else "_LIVE"
    for cle, secret in (
        ("ALPACA_KEY_ID" + suffixe, "ALPACA_SECRET_KEY" + suffixe),
        ("APCA_API_KEY_ID" + suffixe, "APCA_API_SECRET_KEY" + suffixe),
    ):
        if os.environ.get(cle) and os.environ.get(secret):
            return os.environ[cle], os.environ[secret], "variables d'environnement"

    for chemin in (FICHIER_SECRETS, FICHIER_SECRETS_PROJET):
        if not chemin.exists():
            continue
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise BrokerError("%s n'est pas un JSON valide : %s" % (chemin, exc))
        bloc = data.get(profil) or {}
        cle, secret = bloc.get("key_id"), bloc.get("secret_key")
        if cle in _GABARITS or secret in _GABARITS:
            raise BrokerError(
                "Le fichier %s contient encore les valeurs du modele pour le "
                "profil %r.\n"
                "Remplace \"key_id\" et \"secret_key\" par les tiennes, "
                "generees sur https://app.alpaca.markets\n"
                "(section Home, selecteur sur Paper, bouton de generation)."
                % (chemin, profil))
        if cle and secret:
            return cle, secret, str(chemin)

    raise BrokerError(
        "Aucun identifiant trouve pour le profil %r.\n"
        "\n"
        "Deux facons de les fournir :\n"
        "  1. variables d'environnement (recommande) :\n"
        "       Windows PowerShell :  $env:ALPACA_KEY_ID%s=\"...\"\n"
        "                             $env:ALPACA_SECRET_KEY%s=\"...\"\n"
        "  2. fichier %s :\n"
        '       {\"%s\": {\"key_id\": \"...\", \"secret_key\": \"...\"}}\n'
        "\n"
        "Les cles se generent sur https://app.alpaca.markets (section Home, "
        "bouton de generation). La cle secrete ne s'affiche qu'UNE fois."
        % (profil, suffixe, suffixe, FICHIER_SECRETS, profil))


def masquer(cle: str) -> str:
    """Rend une cle affichable sans la reveler."""
    return (cle[:4] + "..." + cle[-3:]) if cle and len(cle) > 9 else "***"


class Alpaca:
    """Client REST minimal. Toutes les methodes levent `BrokerError` en cas d'echec."""

    def __init__(self, key_id: str, secret_key: str, base_url: str = URL_SIMULATION,
                 timeout: float = 20.0, simulation: bool = None):
        # `simulation` est explicite et non deduit de l'URL : une heuristique de
        # sous-chaine sur une adresse est exactement le genre de detail qui
        # finit par annoncer "simulation" devant un compte reel. En l'absence
        # d'indication, on retombe sur l'URL, et le doute profite a la prudence
        # (tout ce qui n'est pas reconnu comme simulation est traite comme reel).
        self.base_url = base_url.rstrip("/")
        self._simulation = (("paper-api" in self.base_url) if simulation is None
                            else bool(simulation))
        self.timeout = timeout
        self._key_id = key_id
        self._session = None
        self._entetes = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type": "application/json",
        }

    # -- identite ----------------------------------------------------------
    @property
    def simulation(self) -> bool:
        return self._simulation

    def __repr__(self) -> str:      # jamais le secret, meme tronque
        return "Alpaca(%s, cle=%s)" % ("SIMULATION" if self.simulation else "REEL",
                                       masquer(self._key_id))

    # -- transport ---------------------------------------------------------
    def _appel(self, methode: str, chemin: str, base: str = None, **kwargs):
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(self._entetes)
        url = (base or self.base_url) + chemin
        try:
            reponse = self._session.request(methode, url, timeout=self.timeout, **kwargs)
        except Exception as exc:
            raise BrokerError("%s %s : %s" % (methode, chemin, exc))
        if reponse.status_code == 401:
            raise BrokerError(
                "Identifiants refuses (401) sur %s.\n"
                "Verifie que la cle correspond bien au bon compte : les cles de "
                "SIMULATION et les cles REELLES sont differentes et ne sont pas "
                "interchangeables." % self.base_url)
        if reponse.status_code >= 400:
            detail = reponse.text[:400]
            raise BrokerError("%s %s -> HTTP %d : %s"
                              % (methode, chemin, reponse.status_code, detail))
        if not reponse.content:
            return None
        return reponse.json()

    # -- lecture -----------------------------------------------------------
    def compte(self) -> dict:
        return self._appel("GET", "/v2/account")

    def horloge(self) -> dict:
        return self._appel("GET", "/v2/clock")

    def positions(self) -> dict:
        """{ticker: nombre de titres} (negatif si vente a decouvert)."""
        brut = self._appel("GET", "/v2/positions") or []
        return {p["symbol"]: float(p["qty"]) for p in brut}

    def positions_detail(self) -> list:
        return self._appel("GET", "/v2/positions") or []

    def cotations(self, tickers, feed: str = "iex") -> dict:
        """Fourchette du moment : {ticker: (achat, vente, milieu)}.

        C'est la SEULE reference contemporaine d'une execution. Comparer un
        prix obtenu a une cloture - de la veille ou du soir meme - ne mesure
        pas le cout de l'ordre, cela mesure le chemin parcouru par le marche
        entre les deux instants. Enregistrer la fourchette au moment de
        l'envoi est ce qui rend la mesure possible plus tard.
        """
        out = {}
        tickers = list(tickers)
        for i in range(0, len(tickers), 100):          # l'API limite le lot
            lot = tickers[i:i + 100]
            try:
                rep = self._appel("GET", "/v2/stocks/quotes/latest",
                                  base=URL_DONNEES,
                                  params={"symbols": ",".join(lot), "feed": feed})
            except BrokerError:
                continue
            for ticker, q in (rep or {}).get("quotes", {}).items():
                achat, vente = float(q.get("bp") or 0.0), float(q.get("ap") or 0.0)
                if achat > 0 and vente > 0:
                    out[ticker] = (achat, vente, (achat + vente) / 2.0)
        return out

    def ordre(self, identifiant: str) -> dict:
        return self._appel("GET", "/v2/orders/%s" % identifiant)

    def ordres_ouverts(self) -> list:
        return self._appel("GET", "/v2/orders", params={"status": "open"}) or []

    def ordres_executes(self, limite: int = 500, apres: str = None) -> list:
        """Ordres CLOS, avec ce qui a reellement ete rempli.

        Le journal du bot enregistre ce qu'il a ENVOYE ; seul le courtier sait
        ce qui a ete execute, en quelle quantite et a quel prix. Pour toute
        reconciliation, c'est cette source qui fait foi - et c'est la seule qui
        renseigne une quantite pour les ordres passes en MONTANT, dont le bot
        ignore par construction le nombre de titres au moment de l'envoi.
        """
        params = {"status": "closed", "limit": int(limite), "direction": "desc"}
        if apres:
            params["after"] = apres
        return self._appel("GET", "/v2/orders", params=params) or []

    def actif(self, ticker: str) -> dict:
        return self._appel("GET", "/v2/assets/%s" % ticker)

    def negociables(self, tickers) -> dict:
        """{ticker: True/False} - un titre suspendu ou radie n'est pas negociable."""
        out = {}
        for t in tickers:
            try:
                a = self.actif(t)
                out[t] = bool(a.get("tradable")) and a.get("status") == "active"
            except BrokerError:
                out[t] = False
        return out

    # -- ecriture ----------------------------------------------------------
    def annuler_ordres(self) -> None:
        self._appel("DELETE", "/v2/orders")

    def envoyer_ordre(self, ticker: str, sens: str, quantite=None, montant=None,
                      type_ordre: str = "market", duree: str = "day",
                      client_order_id: Optional[str] = None) -> dict:
        """Un ordre au marche. Fournir `quantite` (titres) OU `montant` (devise)."""
        if (quantite is None) == (montant is None):
            raise ValueError("fournir exactement l'un de quantite / montant")
        corps = {"symbol": ticker, "side": sens, "type": type_ordre, "time_in_force": duree}
        if quantite is not None:
            corps["qty"] = str(quantite)
        else:
            corps["notional"] = str(round(float(montant), 2))
        if client_order_id:
            corps["client_order_id"] = client_order_id
        return self._appel("POST", "/v2/orders", data=json.dumps(corps))


# ---------------------------------------------------------------------------
# Ouverture de connexion, avec les verrous de l'argent reel
# ---------------------------------------------------------------------------
def connecter(cfg, reel: bool = False, timeout: float = 20.0) -> Alpaca:
    """Ouvre une connexion. En SIMULATION par defaut, toujours.

    Le passage en reel exige TROIS verrous independants, places a trois
    endroits differents exprès : un fichier de configuration versionne, une
    variable d'environnement de la machine, et un argument de ligne de
    commande. Aucun ne peut etre ouvert par megarde par les deux autres, et
    aucune tache planifiee heritee ne peut les reunir toute seule.
    """
    if not reel:
        key, secret, source = _lire_identifiants("paper")
        return Alpaca(key, secret, URL_SIMULATION, timeout, simulation=True)

    if not bool(cfg.get("broker.allow_live", False)):
        raise LiveRefuse(
            "Verrou 1/3 ferme : 'broker.allow_live' vaut false dans la configuration.\n"
            "Ouvre-le a la main dans le YAML si tu sais precisement ce que tu fais.")
    if os.environ.get("QUANTBOT_LIVE") != PHRASE_VERROU:
        raise LiveRefuse(
            "Verrou 2/3 ferme : la variable d'environnement QUANTBOT_LIVE doit valoir\n"
            "exactement %r sur cette machine.\n"
            "  PowerShell :  $env:QUANTBOT_LIVE=\"%s\"" % (PHRASE_VERROU, PHRASE_VERROU))
    key, secret, source = _lire_identifiants("live")
    return Alpaca(key, secret, URL_REEL, timeout, simulation=False)
