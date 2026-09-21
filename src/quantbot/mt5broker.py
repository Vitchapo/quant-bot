"""Passerelle MetaTrader 5, au meme contrat que broker.Alpaca.

Pourquoi ce module existe
-------------------------
FTMO ne publie aucune API REST. Le seul chemin officiel est la librairie
`MetaTrader5`, qui parle a un TERMINAL MT5 deja ouvert sur la machine, par
memoire partagee. Consequences a connaitre avant d'ecrire une ligne :

* elle ne fonctionne que sous **Windows** (pas de roue Linux ni macOS) ;
* le terminal doit tourner, etre connecte, et « Algo Trading » doit etre
  active dans ses options - sinon les ordres partent et sont refuses ;
* un seul processus Python peut tenir la connexion a la fois.

Ce module expose EXACTEMENT les huit methodes qu'`operations.py` appelle sur
`broker.Alpaca` - compte, horloge, positions, positions_detail,
ordres_ouverts, cotations, envoyer_ordre, annuler_ordres. Tout le reste du
projet (controles, plan d'ordres, journal, garde-fous du defi) fonctionne donc
sans modification. C'est le seul interet d'avoir garde `orders.py` pur.

Les trois pieges de MT5 que ce module traite
--------------------------------------------
1. **Le nom des symboles n'est pas le ticker.** Selon le courtier, Apple
   s'appelle `AAPL`, `AAPL.US`, `#AAPL` ou `AAPL_us`. Rien ne permet de le
   deviner : on interroge donc le catalogue au demarrage et on construit la
   correspondance une fois pour toutes (`_resoudre`).

2. **Le volume n'est pas un nombre libre.** Chaque symbole impose un
   `volume_min`, un `volume_max` et un `volume_step`. Un volume non aligne sur
   le pas est refuse par le serveur avec `10014 invalid volume`, a chaque
   tentative, indefiniment. On arrondit AVANT d'envoyer.

3. **Le mode de remplissage depend du symbole.** `filling_mode` est un masque
   de bits. Les actions n'acceptent souvent ni FOK ni IOC, seulement RETURN.
   Envoyer le mauvais donne `10030 unsupported filling mode`, ce qui ressemble
   a un bug de code alors que c'est une propriete du symbole. On lit le masque
   et on choisit un mode reellement supporte.

La marge : mesuree, pas calculee
--------------------------------
Le levier sur les actions est plafonne (1:5 chez FTMO au moment d'ecrire).
Mais on ne calcule PAS la marge a partir d'un levier suppose : on la demande a
`order_calc_margin`, qui connait le vrai barème du compte, symbole par
symbole. Un levier code en dur dans le bot est faux le jour ou le courtier le
change, et l'erreur se manifeste par des `10019 no money` en pleine seance.

Aucun acces a l'argent reel par accident
----------------------------------------
`connecter()` refuse par defaut tout compte qui n'est pas de demonstration ou
de challenge. Les comptes FTMO de challenge sont des comptes DEMO cotes en
reel ; un compte `ACCOUNT_TRADE_MODE_REAL` exige les memes trois verrous que
le chemin Alpaca (`broker.allow_live`, la variable d'environnement, et un
argument explicite).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

from .broker import BrokerError, LiveRefuse, PHRASE_VERROU

#: Suffixes essayes pour retrouver un ticker dans le catalogue du courtier.
#: L'ordre compte : on prefere une correspondance exacte.
SUFFIXES = ("", ".US", ".us", ".NAS", ".cash", "_us", ".USD")
PREFIXES = ("", "#", "_")

#: Codes retour MT5 qu'on nomme, pour ne pas journaliser des entiers nus.
RETCODES = {
    10009: "execute",
    10008: "place",
    10004: "requote",
    10006: "rejete par le serveur",
    10013: "requete invalide",
    10014: "volume invalide",
    10015: "prix invalide",
    10016: "stops invalides",
    10018: "marche ferme",
    10019: "marge insuffisante (Not enough money)",
    10021: "aucune cotation",
    10027: "AutoTrading desactive dans le terminal",
    10030: "mode de remplissage non supporte",
}


class MT5Indisponible(BrokerError):
    """La librairie n'est pas installee, ou le terminal est injoignable."""


def _mt5():
    """Importe MetaTrader5 a l'APPEL, jamais au chargement du module.

    Sans cela, tout le projet devient non importable hors Windows - y compris
    les backtests et les tests, qui n'ont aucun besoin de MT5. C'est aussi ce
    qui permet aux tests d'injecter un double.
    """
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as exc:
        raise MT5Indisponible(
            "La librairie MetaTrader5 n'est pas disponible (%s).\n"
            "  pip install MetaTrader5\n"
            "Elle n'existe QUE pour Windows : sous Linux ou macOS il n'y a pas\n"
            "de roue a installer, et aucun contournement. Le backtest et les\n"
            "tests fonctionnent sans elle." % exc)
    return mt5


class MT5:
    """Connexion a un terminal MT5. Meme surface que `broker.Alpaca`."""

    def __init__(self, mt5=None, magic: int = 770315, deviation: int = 20,
                 catalogue=None):
        self._mt5 = mt5 if mt5 is not None else _mt5()
        self.magic = int(magic)
        self.deviation = int(deviation)
        self._carte: dict = {}        # ticker projet -> symbole courtier
        self._absents: set = set()    # tickers hors catalogue, signales une fois
        self._catalogue = catalogue   # liste imposee (tests) ou None = interroge
        self._simulation = None

    # -- connexion ---------------------------------------------------------
    def initialiser(self, chemin_terminal: str = None, login: int = None,
                    mot_de_passe: str = None, serveur: str = None,
                    timeout_ms: int = 30_000) -> dict:
        """Ouvre la connexion et VERIFIE qu'elle est utilisable.

        Trois verifications distinctes, parce qu'elles echouent pour trois
        raisons differentes et que le message doit le dire :
          1. le terminal repond-il ?
          2. un compte est-il connecte ?
          3. le trading automatique est-il autorise ?

        La troisieme est celle qu'on oublie : `initialize()` reussit, le compte
        s'affiche, tout semble en place, et chaque ordre revient en 10027
        parce qu'une case n'est pas cochee dans les options du terminal.
        """
        mt5 = self._mt5
        kwargs = {"timeout": int(timeout_ms)}
        if chemin_terminal:
            kwargs["path"] = chemin_terminal
        if login:
            kwargs.update({"login": int(login), "password": mot_de_passe or "",
                           "server": serveur or ""})
        if not mt5.initialize(**kwargs):
            code, texte = mt5.last_error()
            # Le code -10003 avec "not found" a une cause TRES precise, et un
            # message generique fait tourner en rond : la librairie n'a trouve
            # AUCUNE installation MT5 sur la machine. Ce n'est pas un terminal
            # ferme, ni un mauvais mot de passe, ni un pare-feu. Le dire.
            introuvable = "not found" in str(texte).lower() or int(code or 0) == -10003
            if introuvable and not chemin_terminal:
                raise MT5Indisponible(
                    "Aucune installation MetaTrader 5 trouvee sur cette machine.\n"
                    "  (%s, code %s)\n\n"
                    "Ce n'est pas un terminal ferme : la librairie ne trouve aucun\n"
                    "MT5 installe. Deux choses a faire, dans cet ordre :\n\n"
                    "  1. verifier s'il est installe\n"
                    "       Get-ChildItem \"C:\\Program Files\" -Filter terminal64.exe "
                    "-Recurse -Depth 2\n\n"
                    "  2. si la liste est vide : telecharge MT5 depuis l'espace\n"
                    "     client FTMO (pas celui de MetaQuotes), installe-le,\n"
                    "     ouvre-le et connecte-toi.\n\n"
                    "  3. puis prepare la config, qui ecrira le chemin trouve :\n"
                    "       python scripts\\preparer_ftmo.py\n"
                    % (texte, code))
            if introuvable:
                raise MT5Indisponible(
                    "Le chemin configure ne mene a aucun terminal utilisable :\n"
                    "  %s\n"
                    "  (%s, code %s)\n\n"
                    "Verifie que ce fichier existe vraiment, puis relance :\n"
                    "  python scripts\\preparer_ftmo.py --terminal \"<chemin>\"\n"
                    % (chemin_terminal, texte, code))
            raise MT5Indisponible(
                "initialize() a echoue : %s (code %s).\n"
                "Le terminal est-il OUVERT, avec un compte connecte ?\n"
                "  python scripts\\preparer_ftmo.py    pour un diagnostic complet"
                % (texte, code))

        info = mt5.terminal_info()
        if info is None:
            raise MT5Indisponible("terminal_info() ne renvoie rien : terminal ferme ?")
        if not getattr(info, "trade_allowed", False):
            raise MT5Indisponible(
                "Le terminal refuse le trading automatique.\n"
                "Dans MT5 : Outils > Options > Expert Advisors >\n"
                "cocher « Autoriser le trading automatique ».\n"
                "Sans cela chaque ordre revient en 10027 et rien ne part.")

        compte = mt5.account_info()
        if compte is None:
            raise MT5Indisponible(
                "account_info() ne renvoie rien : aucun compte connecte dans "
                "le terminal.")

        self._simulation = self._est_simulation(compte)
        return {
            "login": getattr(compte, "login", None),
            "serveur": getattr(compte, "server", ""),
            "devise": getattr(compte, "currency", "USD"),
            "levier": getattr(compte, "leverage", None),
            "simulation": self._simulation,
            "terminal": getattr(info, "name", ""),
            "build": getattr(info, "build", None),
            "symboles_catalogue": mt5.symbols_total(),
        }

    def _est_simulation(self, compte) -> bool:
        mode = getattr(compte, "trade_mode", None)
        demo = getattr(self._mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        concours = getattr(self._mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1)
        return mode in (demo, concours)

    @property
    def simulation(self) -> bool:
        return bool(self._simulation)

    def fermer(self) -> None:
        try:
            self._mt5.shutdown()
        except Exception:
            pass

    def __repr__(self) -> str:                      # jamais d'identifiants
        return "MT5(simulation=%s, symboles=%d)" % (self._simulation, len(self._carte))

    # -- resolution des symboles ------------------------------------------
    def _noms_catalogue(self) -> list:
        if self._catalogue is not None:
            return list(self._catalogue)
        symboles = self._mt5.symbols_get()
        return [s.name for s in (symboles or [])]

    def construire_carte(self, tickers) -> dict:
        """Associe chaque ticker du projet a son nom chez le courtier.

        Renvoie la carte et remplit `self._absents` avec ce qui n'existe pas.
        C'est ici qu'un ticker du S&P 500 hors catalogue FTMO est ECARTE - pas
        plus tard au moment de l'ordre, ou il ferait echouer un rebalancement
        entier pour une ligne manquante.
        """
        noms = set(self._noms_catalogue())
        for t in tickers:
            trouve = None
            for pre in PREFIXES:
                for suf in SUFFIXES:
                    candidat = "%s%s%s" % (pre, t, suf)
                    if candidat in noms:
                        trouve = candidat
                        break
                if trouve:
                    break
            if trouve:
                self._carte[t] = trouve
            else:
                self._absents.add(t)
        return dict(self._carte)

    @property
    def absents(self) -> set:
        return set(self._absents)

    def symbole(self, ticker: str) -> Optional[str]:
        """Nom courtier, ou None si le ticker n'est pas au catalogue."""
        if ticker in self._carte:
            return self._carte[ticker]
        if ticker in self._absents:
            return None
        self.construire_carte([ticker])
        return self._carte.get(ticker)

    def _selectionner(self, symbole: str) -> bool:
        """Un symbole non visible dans le Market Watch ne cote pas."""
        info = self._mt5.symbol_info(symbole)
        if info is None:
            return False
        if not getattr(info, "visible", True):
            return bool(self._mt5.symbol_select(symbole, True))
        return True

    # -- lecture -----------------------------------------------------------
    def compte(self) -> dict:
        """Au format Alpaca, pour qu'`operations.py` ne voie pas la difference.

        `last_equity` n'existe pas chez MT5. On expose le SOLDE, qui est la
        bonne reference pour FTMO : leur perte journaliere se mesure sur le
        solde a 00:00 CET, pas sur l'equity de la veille.
        """
        a = self._mt5.account_info()
        if a is None:
            raise BrokerError("account_info() ne renvoie rien")
        return {
            "account_number": str(getattr(a, "login", "")),
            "status": "ACTIVE",
            "currency": getattr(a, "currency", "USD"),
            "equity": float(getattr(a, "equity", 0.0)),
            "balance": float(getattr(a, "balance", 0.0)),
            "last_equity": float(getattr(a, "balance", 0.0)),
            "cash": float(getattr(a, "margin_free", 0.0)),
            "buying_power": float(getattr(a, "margin_free", 0.0)),
            "margin_used": float(getattr(a, "margin", 0.0)),
            "leverage": getattr(a, "leverage", None),
            "trading_blocked": not bool(getattr(a, "trade_allowed", True)),
            "account_blocked": False,
            "profit_flottant": float(getattr(a, "profit", 0.0)),
        }

    def horloge(self) -> dict:
        """MT5 n'a pas d'endpoint d'horloge : on deduit du symbole de reference.

        Un tick recent (moins de 5 minutes) signifie que le marche cote. C'est
        moins net que l'horloge d'Alpaca, et c'est pour cela que la fonction
        dit quel symbole elle a interroge : un `False` doit etre verifiable.
        """
        ref = None
        for t in ("US500.cash", "US500", "AAPL", "EURUSD"):
            if self._mt5.symbol_info(t) is not None:
                ref = t
                break
        if ref is None:
            return {"is_open": False, "reference": None,
                    "detail": "aucun symbole de reference au catalogue"}
        self._selectionner(ref)
        tick = self._mt5.symbol_info_tick(ref)
        if tick is None or not getattr(tick, "time", 0):
            return {"is_open": False, "reference": ref, "detail": "aucun tick"}
        age = time.time() - float(tick.time)
        return {"is_open": age < 300, "reference": ref,
                "age_tick_s": round(age, 1),
                "detail": "dernier tick il y a %.0f s sur %s" % (age, ref)}

    def positions(self) -> dict:
        """{ticker projet: volume signe}. Negatif = position vendeuse."""
        brut = self._mt5.positions_get() or []
        inverse = {v: k for k, v in self._carte.items()}
        out: dict = {}
        for p in brut:
            nom = getattr(p, "symbol", "")
            ticker = inverse.get(nom, nom)
            vol = float(getattr(p, "volume", 0.0))
            if getattr(p, "type", 0) == getattr(self._mt5, "POSITION_TYPE_SELL", 1):
                vol = -vol
            out[ticker] = out.get(ticker, 0.0) + vol
        return out

    def positions_detail(self) -> list:
        """Au format Alpaca : symbol / qty / market_value / unrealized_pl.

        `market_value` est la VALEUR NOTIONNELLE de la position (volume x taille
        de contrat x prix), pas la marge immobilisee. C'est ce que suppose le
        reste du projet quand il calcule des poids.
        """
        brut = self._mt5.positions_get() or []
        inverse = {v: k for k, v in self._carte.items()}
        out = []
        for p in brut:
            nom = getattr(p, "symbol", "")
            info = self._mt5.symbol_info(nom)
            taille = float(getattr(info, "trade_contract_size", 1.0) or 1.0) if info else 1.0
            vol = float(getattr(p, "volume", 0.0))
            signe = -1.0 if getattr(p, "type", 0) == getattr(
                self._mt5, "POSITION_TYPE_SELL", 1) else 1.0
            prix = float(getattr(p, "price_current", 0.0) or 0.0)
            out.append({
                "symbol": inverse.get(nom, nom),
                "symbole_courtier": nom,
                "ticket": int(getattr(p, "ticket", 0)),
                "qty": str(signe * vol * taille),
                "volume": signe * vol,
                "market_value": str(signe * vol * taille * prix),
                "unrealized_pl": str(float(getattr(p, "profit", 0.0))),
                "sl": float(getattr(p, "sl", 0.0) or 0.0),
                "tp": float(getattr(p, "tp", 0.0) or 0.0),
            })
        return out

    def ordres_ouverts(self) -> list:
        brut = self._mt5.orders_get() or []
        inverse = {v: k for k, v in self._carte.items()}
        return [{"id": int(getattr(o, "ticket", 0)),
                 "symbol": inverse.get(getattr(o, "symbol", ""), getattr(o, "symbol", "")),
                 "volume": float(getattr(o, "volume_current", 0.0))}
                for o in brut]

    def cotations(self, tickers, feed: str = None) -> dict:
        """{ticker: (achat, vente, milieu)}. Un ticker absent est simplement omis."""
        out = {}
        for t in tickers:
            sym = self.symbole(t)
            if not sym or not self._selectionner(sym):
                continue
            tick = self._mt5.symbol_info_tick(sym)
            if tick is None:
                continue
            bid, ask = float(getattr(tick, "bid", 0.0)), float(getattr(tick, "ask", 0.0))
            if bid > 0 and ask > 0:
                out[t] = (bid, ask, (bid + ask) / 2.0)
        return out

    def negociables(self, tickers) -> dict:
        out = {}
        for t in tickers:
            sym = self.symbole(t)
            if not sym:
                out[t] = False
                continue
            info = self._mt5.symbol_info(sym)
            mode = getattr(info, "trade_mode", None) if info else None
            plein = getattr(self._mt5, "SYMBOL_TRADE_MODE_FULL", 4)
            out[t] = info is not None and mode == plein
        return out

    # -- dimensionnement ---------------------------------------------------
    def specs(self, ticker: str) -> Optional[dict]:
        sym = self.symbole(ticker)
        if not sym:
            return None
        i = self._mt5.symbol_info(sym)
        if i is None:
            return None
        return {
            "symbole": sym,
            "volume_min": float(getattr(i, "volume_min", 0.01) or 0.01),
            "volume_max": float(getattr(i, "volume_max", 1e9) or 1e9),
            "volume_step": float(getattr(i, "volume_step", 0.01) or 0.01),
            "taille_contrat": float(getattr(i, "trade_contract_size", 1.0) or 1.0),
            "digits": int(getattr(i, "digits", 2) or 2),
            "filling_mode": int(getattr(i, "filling_mode", 0) or 0),
            "stops_level": int(getattr(i, "trade_stops_level", 0) or 0),
            "point": float(getattr(i, "point", 0.01) or 0.01),
        }

    def _mode_remplissage(self, spec: dict):
        """Choisit un mode que le SYMBOLE accepte, pas celui qu'on prefere.

        `filling_mode` est un masque : bit 1 = FOK, bit 2 = IOC. Les actions
        n'exposent souvent aucun des deux, et il faut alors RETURN. Deviner
        donne 10030 a chaque envoi.
        """
        mt5 = self._mt5
        masque = spec["filling_mode"]
        fok = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
        ioc = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
        if masque & ioc:
            return getattr(mt5, "ORDER_FILLING_IOC", 1)
        if masque & fok:
            return getattr(mt5, "ORDER_FILLING_FOK", 0)
        return getattr(mt5, "ORDER_FILLING_RETURN", 2)

    def volume_pour(self, ticker: str, notionnel: float, prix: float = None) -> dict:
        """Convertit un montant en devise en volume MT5 executable.

        Renvoie toujours un dict avec `volume` (0.0 si infaisable) et `raison`.
        Ne leve pas : un ticker infaisable ne doit pas interrompre le
        rebalancement des autres.
        """
        spec = self.specs(ticker)
        if spec is None:
            return {"volume": 0.0, "raison": "hors catalogue", "spec": None}
        if prix is None:
            cot = self.cotations([ticker])
            if ticker not in cot:
                return {"volume": 0.0, "raison": "aucune cotation", "spec": spec}
            prix = cot[ticker][2]
        if prix <= 0 or spec["taille_contrat"] <= 0:
            return {"volume": 0.0, "raison": "prix ou taille de contrat nuls", "spec": spec}

        brut = abs(float(notionnel)) / (prix * spec["taille_contrat"])
        pas = spec["volume_step"]
        # On arrondit VERS LE BAS : depasser le notionnel vise consomme de la
        # marge qu'on n'a pas prevue, et c'est la premiere cause de 10019.
        volume = int(brut / pas) * pas
        volume = round(volume, 8)
        if volume < spec["volume_min"]:
            return {"volume": 0.0, "spec": spec,
                    "raison": "notionnel %.2f trop petit : %.4f lot demande, "
                              "minimum %.4f" % (notionnel, brut, spec["volume_min"])}
        volume = min(volume, spec["volume_max"])
        return {"volume": volume, "raison": "", "spec": spec, "prix": prix}

    def marge_requise(self, ticker: str, sens: str, volume: float,
                      prix: float = None) -> Optional[float]:
        """Marge en devise du compte, DEMANDEE au terminal.

        On ne la calcule pas depuis un levier suppose : `order_calc_margin`
        connait le vrai barème, symbole par symbole, y compris les plafonds
        que FTMO applique aux actions. Un levier code en dur devient faux le
        jour ou le courtier le change, et l'erreur sort en pleine seance.
        """
        spec = self.specs(ticker)
        if spec is None or volume <= 0:
            return None
        mt5 = self._mt5
        if prix is None:
            cot = self.cotations([ticker])
            if ticker not in cot:
                return None
            prix = cot[ticker][1] if sens == "buy" else cot[ticker][0]
        action = (getattr(mt5, "ORDER_TYPE_BUY", 0) if sens == "buy"
                  else getattr(mt5, "ORDER_TYPE_SELL", 1))
        m = mt5.order_calc_margin(action, spec["symbole"], volume, float(prix))
        return None if m is None else float(m)

    # -- ecriture ----------------------------------------------------------
    def envoyer_ordre(self, ticker: str, sens: str, quantite=None, montant=None,
                      sl: float = None, tp: float = None,
                      type_ordre: str = "market", duree: str = "day",
                      client_order_id: str = None, position: int = None,
                      verifier_marge: bool = True) -> dict:
        """Un ordre au marche. `quantite` est un VOLUME EN LOTS, pas des titres.

        `montant` est accepte pour rester compatible avec l'appel d'Alpaca dans
        `operations.py` : il est converti en volume par `volume_pour`.

        `position` ferme une position existante par son ticket (MT5 ferme en
        envoyant l'ordre OPPOSE sur la meme position, il n'existe pas d'ordre
        « close »).
        """
        mt5 = self._mt5
        if (quantite is None) == (montant is None):
            raise ValueError("fournir exactement l'un de quantite / montant")
        sens = sens.lower()
        if sens not in ("buy", "sell"):
            raise ValueError("sens doit valoir buy ou sell")

        spec = self.specs(ticker)
        if spec is None:
            raise BrokerError("%s n'est pas au catalogue du courtier" % ticker)
        if not self._selectionner(spec["symbole"]):
            raise BrokerError("%s introuvable dans le Market Watch" % spec["symbole"])

        cot = self.cotations([ticker])
        if ticker not in cot:
            raise BrokerError("aucune cotation pour %s : marche ferme ?" % ticker)
        prix = cot[ticker][1] if sens == "buy" else cot[ticker][0]

        if quantite is None:
            calc = self.volume_pour(ticker, montant, prix=prix)
            if calc["volume"] <= 0:
                raise BrokerError("volume infaisable pour %s : %s" % (ticker, calc["raison"]))
            volume = calc["volume"]
        else:
            pas = spec["volume_step"]
            volume = round(int(abs(float(quantite)) / pas) * pas, 8)
            if volume < spec["volume_min"]:
                raise BrokerError(
                    "volume %.4f sous le minimum %.4f pour %s"
                    % (volume, spec["volume_min"], ticker))

        if verifier_marge and position is None:
            besoin = self.marge_requise(ticker, sens, volume, prix=prix)
            libre = float(self.compte()["cash"])
            if besoin is not None and besoin > libre:
                raise BrokerError(
                    "marge insuffisante pour %s : %.2f requis, %.2f libre. "
                    "Reduis le nombre de lignes ou l'exposition (levier actions "
                    "plafonne)." % (ticker, besoin, libre))

        requete = {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
            "symbol": spec["symbole"],
            "volume": float(volume),
            "type": (getattr(mt5, "ORDER_TYPE_BUY", 0) if sens == "buy"
                     else getattr(mt5, "ORDER_TYPE_SELL", 1)),
            "price": float(prix),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": (client_order_id or "quantbot")[:31],
            "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
            "type_filling": self._mode_remplissage(spec),
        }
        if position is not None:
            requete["position"] = int(position)
        if sl:
            requete["sl"] = round(float(sl), spec["digits"])
        if tp:
            requete["tp"] = round(float(tp), spec["digits"])

        res = mt5.order_send(requete)
        if res is None:
            code, texte = mt5.last_error()
            raise BrokerError("order_send() n'a rien renvoye : %s (%s)" % (texte, code))
        retcode = int(getattr(res, "retcode", -1))
        ok = retcode in (getattr(mt5, "TRADE_RETCODE_DONE", 10009), 10008)
        if not ok:
            raise BrokerError(
                "ordre %s %s refuse : %d (%s) - %s"
                % (sens, ticker, retcode, RETCODES.get(retcode, "code inconnu"),
                   getattr(res, "comment", "")))
        return {
            "id": str(getattr(res, "order", "") or getattr(res, "deal", "")),
            "status": "filled" if retcode == 10009 else "accepted",
            "retcode": retcode,
            "volume": float(getattr(res, "volume", volume)),
            "price": float(getattr(res, "price", prix) or prix),
            "symbole_courtier": spec["symbole"],
        }

    def fermer_position(self, ticket: int) -> dict:
        """Ferme UNE position par son ticket, en envoyant l'ordre oppose."""
        brut = self._mt5.positions_get(ticket=int(ticket)) or []
        if not brut:
            return {"ok": True, "message": "position %s deja fermee" % ticket}
        p = brut[0]
        vendeuse = getattr(p, "type", 0) == getattr(self._mt5, "POSITION_TYPE_SELL", 1)
        inverse = {v: k for k, v in self._carte.items()}
        ticker = inverse.get(getattr(p, "symbol", ""), getattr(p, "symbol", ""))
        rep = self.envoyer_ordre(ticker, "buy" if vendeuse else "sell",
                                 quantite=float(getattr(p, "volume", 0.0)),
                                 position=int(ticket), verifier_marge=False)
        return {"ok": True, "ticket": int(ticket), "reponse": rep}

    def fermer_tout(self) -> dict:
        """Ferme TOUTES les positions. C'est ce qu'appelle le coupe-circuit.

        On itere sur une photo prise au debut : fermer modifie la liste, et
        iterer sur une collection qui change en sautant des elements est
        exactement ce qu'on ne peut pas se permettre ici.
        """
        tickets = [int(getattr(p, "ticket", 0))
                   for p in (self._mt5.positions_get() or [])]
        fermees, echecs = [], []
        for t in tickets:
            try:
                self.fermer_position(t)
                fermees.append(t)
            except Exception as exc:
                echecs.append({"ticket": t, "raison": str(exc)[:160]})
            time.sleep(0.05)
        return {"ok": not echecs, "fermees": fermees, "echecs": echecs,
                "message": "%d position(s) fermee(s), %d echec(s)"
                           % (len(fermees), len(echecs))}

    def annuler_ordres(self) -> None:
        """Annule les ordres EN ATTENTE (pas les positions ouvertes)."""
        mt5 = self._mt5
        for o in (mt5.orders_get() or []):
            mt5.order_send({"action": getattr(mt5, "TRADE_ACTION_REMOVE", 8),
                            "order": int(getattr(o, "ticket", 0))})


def connecter(cfg, reel: bool = False, mt5=None, **kw) -> MT5:
    """Ouvre une connexion MT5. Refuse un compte REEL sans les trois verrous.

    Les comptes de challenge FTMO sont des comptes DEMO : le chemin normal
    n'ouvre donc aucun verrou et ne touche a aucun argent. Un compte marque
    reel par le terminal exige les memes conditions que le chemin Alpaca.
    """
    api = MT5(mt5=mt5, magic=int(cfg.get("broker.magic", 770315)),
              deviation=int(cfg.get("broker.deviation", 20)),
              catalogue=kw.pop("catalogue", None))
    api.initialiser(
        chemin_terminal=kw.pop("chemin_terminal", None) or cfg.get("broker.terminal"),
        login=kw.pop("login", None) or cfg.get("broker.login"),
        mot_de_passe=kw.pop("mot_de_passe", None) or os.environ.get("MT5_PASSWORD"),
        serveur=kw.pop("serveur", None) or cfg.get("broker.serveur"),
    )
    if not api.simulation:
        if not reel:
            api.fermer()
            raise LiveRefuse(
                "Le terminal est connecte a un compte REEL et --reel n'a pas ete\n"
                "demande. Les comptes de challenge FTMO sont des comptes DEMO :\n"
                "si tu vois ce message sur un challenge, verifie quel compte est\n"
                "ouvert dans le terminal avant toute chose.")
        if not bool(cfg.get("broker.allow_live", False)):
            api.fermer()
            raise LiveRefuse("Verrou 1/3 ferme : broker.allow_live vaut false.")
        if os.environ.get("QUANTBOT_LIVE") != PHRASE_VERROU:
            api.fermer()
            raise LiveRefuse("Verrou 2/3 ferme : QUANTBOT_LIVE doit valoir %r."
                             % PHRASE_VERROU)
    return api
