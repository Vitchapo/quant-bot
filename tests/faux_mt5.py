"""Double du module MetaTrader5, pour tester la passerelle hors Windows.

La librairie officielle n'existe qu'en roue Windows : sans ce double, aucun
test de `mt5broker` ne pourrait tourner, et c'est exactement le genre de code
qu'on ne peut pas se permettre de ne pas tester - il envoie des ordres.

Le double reproduit ce qui casse en vrai : les pas de volume, les masques de
mode de remplissage, les codes retour d'erreur, et le fait que fermer une
position se fait en envoyant l'ordre OPPOSE avec le champ `position`.
"""
from __future__ import annotations

# -- constantes, memes valeurs que la vraie librairie ---------------------
TRADE_ACTION_DEAL, TRADE_ACTION_REMOVE = 1, 8
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
SYMBOL_FILLING_FOK, SYMBOL_FILLING_IOC = 1, 2
SYMBOL_TRADE_MODE_FULL = 4
ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
TRADE_RETCODE_DONE = 10009


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FauxMT5:
    """Un terminal MT5 simule. Tout est parametrable par le test."""

    def __init__(self, symboles=None, solde=100_000.0, equity=100_000.0,
                 marge_libre=100_000.0, trade_mode=ACCOUNT_TRADE_MODE_DEMO,
                 trade_allowed=True, terminal_ok=True, init_ok=True,
                 filling_mode=SYMBOL_FILLING_IOC, marge_par_lot=None):
        defaut = {"AAPL.US": 180.0, "MSFT.US": 400.0, "NVDA.US": 175.0,
                  "US500.cash": 7690.0, "EURUSD": 1.147}
        self.prix = dict(symboles if symboles is not None else defaut)
        self.solde, self.equity, self.marge_libre = solde, equity, marge_libre
        self.trade_mode, self.trade_allowed = trade_mode, trade_allowed
        self.terminal_ok, self.init_ok = terminal_ok, init_ok
        self.filling_mode = filling_mode
        self.marge_par_lot = marge_par_lot        # None = prix*taille/levier
        self.levier = 5
        self._positions: list = []
        self._ordres: list = []
        self.envoyes: list = []                   # journal des requetes recues
        self._ticket = 1000
        self.arrete = False
        self.forcer_retcode = None                # pour tester les refus
        self._erreur = (0, "ok")
        self.selectionnes: set = set()

    # -- cycle de vie ------------------------------------------------------
    def initialize(self, **kw):
        self.init_kwargs = kw
        if not self.init_ok:
            self._erreur = (-10003, "IPC initialize failed")
        return bool(self.init_ok)

    def shutdown(self):
        self.arrete = True

    def last_error(self):
        return self._erreur

    def terminal_info(self):
        if not self.terminal_ok:
            return None
        return _Obj(trade_allowed=self.trade_allowed, name="FauxTerminal", build=4000)

    def account_info(self):
        return _Obj(login=12345678, server="FTMO-Demo", currency="USD",
                    equity=self.equity, balance=self.solde,
                    margin_free=self.marge_libre, margin=self.solde - self.marge_libre,
                    leverage=self.levier, trade_mode=self.trade_mode,
                    trade_allowed=self.trade_allowed,
                    profit=self.equity - self.solde)

    # -- symboles ----------------------------------------------------------
    def symbols_total(self):
        return len(self.prix)

    def symbols_get(self, group=None):
        return [_Obj(name=n, path="Stocks\\US\\" + n if ".cash" not in n
                     else "Indices\\" + n) for n in self.prix]

    def symbol_info(self, nom):
        if nom not in self.prix:
            return None
        return _Obj(name=nom, visible=nom in self.selectionnes or True,
                    volume_min=0.01, volume_max=500.0, volume_step=0.01,
                    trade_contract_size=1.0 if ".cash" not in nom else 1.0,
                    digits=2, point=0.01, filling_mode=self.filling_mode,
                    trade_stops_level=0, trade_mode=SYMBOL_TRADE_MODE_FULL)

    def symbol_select(self, nom, activer=True):
        if nom not in self.prix:
            return False
        self.selectionnes.add(nom)
        return True

    def symbol_info_tick(self, nom):
        if nom not in self.prix:
            return None
        p = self.prix[nom]
        import time as _t
        return _Obj(bid=p * 0.9999, ask=p * 1.0001, last=p, time=_t.time())

    def order_calc_margin(self, action, symbole, volume, prix):
        if symbole not in self.prix:
            return None
        if self.marge_par_lot is not None:
            return float(self.marge_par_lot) * float(volume)
        info = self.symbol_info(symbole)
        return float(prix) * float(volume) * info.trade_contract_size / self.levier

    # -- positions et ordres ----------------------------------------------
    def positions_get(self, ticket=None, symbol=None):
        out = list(self._positions)
        if ticket is not None:
            out = [p for p in out if p.ticket == int(ticket)]
        if symbol is not None:
            out = [p for p in out if p.symbol == symbol]
        return out

    def orders_get(self, ticket=None):
        return list(self._ordres)

    def order_send(self, requete):
        self.envoyes.append(dict(requete))
        if self.forcer_retcode is not None:
            return _Obj(retcode=int(self.forcer_retcode), comment="refus force",
                        order=0, deal=0, volume=0.0, price=0.0)

        if requete.get("action") == TRADE_ACTION_REMOVE:
            self._ordres = [o for o in self._ordres
                            if o.ticket != int(requete.get("order", 0))]
            return _Obj(retcode=TRADE_RETCODE_DONE, comment="removed",
                        order=requete.get("order", 0), deal=0, volume=0.0, price=0.0)

        sym, vol = requete["symbol"], float(requete["volume"])
        info = self.symbol_info(sym)
        if info is None:
            return _Obj(retcode=10013, comment="symbole inconnu", order=0, deal=0,
                        volume=0.0, price=0.0)
        # le serveur refuse un volume non aligne sur le pas
        if abs(round(vol / info.volume_step) - vol / info.volume_step) > 1e-6:
            return _Obj(retcode=10014, comment="invalid volume", order=0, deal=0,
                        volume=0.0, price=0.0)
        if vol < info.volume_min:
            return _Obj(retcode=10014, comment="volume too small", order=0, deal=0,
                        volume=0.0, price=0.0)
        # mode de remplissage : le serveur refuse ce que le symbole n'expose pas
        demande = requete.get("type_filling")
        if demande == ORDER_FILLING_IOC and not (self.filling_mode & SYMBOL_FILLING_IOC):
            return _Obj(retcode=10030, comment="unsupported filling mode",
                        order=0, deal=0, volume=0.0, price=0.0)
        if demande == ORDER_FILLING_FOK and not (self.filling_mode & SYMBOL_FILLING_FOK):
            return _Obj(retcode=10030, comment="unsupported filling mode",
                        order=0, deal=0, volume=0.0, price=0.0)

        ferme = requete.get("position")
        if ferme is not None:
            avant = len(self._positions)
            self._positions = [p for p in self._positions if p.ticket != int(ferme)]
            if len(self._positions) == avant:
                return _Obj(retcode=10013, comment="position inconnue", order=0,
                            deal=0, volume=0.0, price=0.0)
            return _Obj(retcode=TRADE_RETCODE_DONE, comment="closed",
                        order=int(ferme), deal=int(ferme), volume=vol,
                        price=requete.get("price", 0.0))

        besoin = self.order_calc_margin(requete["type"], sym, vol, requete["price"])
        if besoin is not None and besoin > self.marge_libre:
            return _Obj(retcode=10019, comment="Not enough money", order=0, deal=0,
                        volume=0.0, price=0.0)

        self._ticket += 1
        self._positions.append(_Obj(
            ticket=self._ticket, symbol=sym, volume=vol,
            type=POSITION_TYPE_BUY if requete["type"] == ORDER_TYPE_BUY
            else POSITION_TYPE_SELL,
            price_open=requete["price"], price_current=requete["price"],
            sl=requete.get("sl", 0.0), tp=requete.get("tp", 0.0), profit=0.0,
            magic=requete.get("magic", 0)))
        self.marge_libre -= (besoin or 0.0)
        return _Obj(retcode=TRADE_RETCODE_DONE, comment="done",
                    order=self._ticket, deal=self._ticket, volume=vol,
                    price=requete["price"])

    # -- aides pour les tests ---------------------------------------------
    def ajouter_position(self, symbole, volume, vendeuse=False, profit=0.0):
        self._ticket += 1
        self._positions.append(_Obj(
            ticket=self._ticket, symbol=symbole, volume=float(volume),
            type=POSITION_TYPE_SELL if vendeuse else POSITION_TYPE_BUY,
            price_open=self.prix[symbole], price_current=self.prix[symbole],
            sl=0.0, tp=0.0, profit=float(profit), magic=770315))
        return self._ticket

    def ajouter_ordre_en_attente(self, symbole, volume=0.1):
        self._ticket += 1
        self._ordres.append(_Obj(ticket=self._ticket, symbol=symbole,
                                 volume_current=float(volume)))
        return self._ticket
