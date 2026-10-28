"""Le domaine d'activite de chaque titre : semi-conducteurs, energie, agro...

D'ou vient le classement
------------------------
De la norme GICS - secteur et sous-industrie - telle que la publie la table
des constituants du S&P 500 sur Wikipedia, la page d'ou le bot tire deja son
univers (`universe.py`). Rien n'est devine : un titre absent de la table est
"Non classe".

Pourquoi regrouper, et pas seulement traduire
---------------------------------------------
Le secteur GICS "Information Technology" met dans le meme sac les fondeurs de
puces, les fabricants de serveurs et de stockage, et les editeurs de
logiciels. Le 26/10/2026, il portait les trois quarts des lignes du compte :
"Technologie 74 %" ne dit pas si le pari porte sur les puces ou sur le
logiciel. Le secteur est donc coupe en trois domaines, reunis dans une
famille "Tech" pour que le total reste visible.

L'agroalimentaire est sorti de ses secteurs d'origine : le groupe GICS "Food,
Beverage & Tobacco" (produits agricoles, aliments, boissons, tabac), plus les
engrais (Materials) et les machines agricoles (Industrials). Tout le reste
suit les secteurs GICS, traduits.

Le cache
--------
`data/sp500_secteurs.csv`, cumulatif : un titre sorti de l'indice garde son
classement, puisqu'on peut encore le detenir. Rafraichi au plus une fois par
semaine, ou quand un titre demande y manque, mais jamais plus d'une tentative
par heure - et toujours en tache de fond : l'ecran n'attend jamais Wikipedia.
"""
from __future__ import annotations

import csv
import os
import threading
import time
from datetime import date
from pathlib import Path

CACHE = Path("data/sp500_secteurs.csv")
DUREE_VIE = 7 * 86400.0          # un classement GICS change rarement
ENTRE_DEUX_ESSAIS = 3600.0       # Wikipedia injoignable : on ne la harcele pas
NON_CLASSE = "Non classe"
TECH = "Tech"

#: Ordre stable des domaines : celui des secteurs GICS, tech coupee en trois.
DOMAINES = ("Energie", "Materiaux & chimie", "Industrie", "Consommation cyclique",
            "Agroalimentaire", "Consommation de base", "Sante", "Finance",
            "Semi-conducteurs", "Materiel & reseaux", "Logiciels & services",
            "Medias & telecoms", "Electricite, gaz, eau", "Immobilier", NON_CLASSE)
MEMBRES_TECH = ("Semi-conducteurs", "Materiel & reseaux", "Logiciels & services")

_SECTEURS = {
    "energy": "Energie",
    "materials": "Materiaux & chimie",
    "industrials": "Industrie",
    "consumer discretionary": "Consommation cyclique",
    "consumer staples": "Consommation de base",
    "health care": "Sante",
    "financials": "Finance",
    "communication services": "Medias & telecoms",
    "utilities": "Electricite, gaz, eau",
    "real estate": "Immobilier",
}
# Par mots-cles plutot que par libelles exacts : la norme a deja renomme des
# sous-industries (revision GICS de 2023), et la table de Wikipedia suit.
_AGRO = ("agricultur", "packaged foods", "beverage", "brewers", "distillers", "tobacco")
_LOGICIEL = ("software", "it consulting", "internet services", "data processing")


def domaine(secteur, sous_industrie) -> str:
    """Le domaine affiche pour un couple (secteur, sous-industrie) GICS."""
    s = str(secteur or "").strip().lower()
    si = str(sous_industrie or "").strip().lower()
    if any(k in si for k in _AGRO):
        return "Agroalimentaire"
    if s == "information technology":
        if "semiconductor" in si:
            return "Semi-conducteurs"
        if any(k in si for k in _LOGICIEL):
            return "Logiciels & services"
        return "Materiel & reseaux"
    return _SECTEURS.get(s, NON_CLASSE)


def famille(nom: str) -> str:
    """Les trois domaines de la tech forment une seule famille."""
    return TECH if nom in MEMBRES_TECH else nom


def normaliser(ticker) -> str:
    """BRK.B chez le courtier, BRK-B dans l'univers : une seule cle."""
    return str(ticker or "").strip().upper().replace(".", "-")


def lire_cache(chemin=CACHE) -> dict:
    """{ticker: (secteur, sous_industrie)}. Fichier absent ou abime : {}."""
    out = {}
    try:
        with open(chemin, newline="", encoding="utf-8") as fh:
            for ligne in csv.DictReader(fh):
                t = normaliser(ligne.get("ticker"))
                if t:
                    out[t] = ((ligne.get("secteur") or "").strip(),
                              (ligne.get("sous_industrie") or "").strip())
    except (OSError, csv.Error, UnicodeDecodeError):
        return {}
    return out


def ecrire_cache(table: dict, chemin=CACHE) -> None:
    """Ecriture atomique : un lecteur ne voit jamais un fichier a moitie ecrit."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_name(chemin.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "secteur", "sous_industrie"])
        for t in sorted(table):
            w.writerow([t, table[t][0], table[t][1]])
    os.replace(tmp, chemin)


def lire_table_gics(html: str) -> dict:
    """{ticker: (secteur, sous_industrie)} depuis le HTML de la page Wikipedia.

    Leve une exception si la table manque ou parait tronquee : un classement
    partiel passerait pour complet.
    """
    import io

    import pandas as pd

    from .universe import SP500_MIN_ROWS

    for table in pd.read_html(io.StringIO(html)):
        cols = {str(c).strip(): c for c in table.columns}
        if not {"Symbol", "GICS Sector", "GICS Sub-Industry"} <= set(cols):
            continue
        out = {}
        for sym, sec, sub in zip(table[cols["Symbol"]], table[cols["GICS Sector"]],
                                 table[cols["GICS Sub-Industry"]]):
            t = normaliser(sym)
            if t and t != "NAN":
                out[t] = (str(sec).strip(), str(sub).strip())
        if len(out) < SP500_MIN_ROWS:
            raise ValueError("table GICS de %d lignes seulement (minimum attendu : %d) : "
                             "la page a probablement change" % (len(out), SP500_MIN_ROWS))
        return out
    raise ValueError("aucune table Symbol / GICS Sector / GICS Sub-Industry sur la page")


def telecharger_wikipedia(timeout: float = 15.0) -> dict:
    """La table des constituants du S&P 500, colonnes GICS comprises.

    Meme page et meme en-tete HTTP que l'univers du bot. Leve une exception a
    la moindre anomalie : c'est `Classement` qui decide quoi en faire (garder
    l'ancien cache, reessayer dans une heure).
    """
    import requests

    from .universe import HTTP_HEADERS, WIKI_SP500_URL

    resp = requests.get(WIKI_SP500_URL, timeout=timeout, headers=HTTP_HEADERS)
    resp.raise_for_status()
    return lire_table_gics(resp.text)


class Classement:
    """Le classement des titres, servi depuis le cache, rafraichi en tache de fond.

    `pour()` ne fait jamais d'appel reseau dans le fil de l'appelant : il lit
    le cache, et lance au besoin un telechargement dans un fil a part. Les
    titres encore inconnus sont rendus dans `inconnus` ; la page redemande
    quelques secondes plus tard tant que `en_cours` est vrai.
    """

    def __init__(self, chemin=CACHE, telecharger=None, duree_vie=DUREE_VIE,
                 entre_deux_essais=ENTRE_DEUX_ESSAIS, horloge=time.time, fond=True):
        self.chemin = Path(chemin)
        self._telecharger = telecharger or telecharger_wikipedia
        self.duree_vie = float(duree_vie)
        self.entre_deux_essais = float(entre_deux_essais)
        self._horloge = horloge
        self._fond = fond
        self._verrou = threading.Lock()
        self._table = None           # lu sur disque au premier appel
        self._date = None            # instant du dernier rafraichissement
        self._en_cours = False
        self._dernier_essai = None
        self._erreur = None

    def _charger(self):
        if self._table is None:
            self._table = lire_cache(self.chemin)
            try:
                self._date = self.chemin.stat().st_mtime if self._table else None
            except OSError:
                self._date = None

    def pour(self, tickers) -> dict:
        tickers = [str(t) for t in (tickers or []) if t]
        with self._verrou:
            self._charger()
            maintenant = self._horloge()
            manque = any(normaliser(t) not in self._table for t in tickers)
            perime = self._date is None or maintenant - self._date > self.duree_vie
            essayer = ((manque or perime) and not self._en_cours
                       and (self._dernier_essai is None
                            or maintenant - self._dernier_essai >= self.entre_deux_essais))
            if essayer:
                self._en_cours = True
                self._dernier_essai = maintenant
        if essayer:
            if self._fond:
                threading.Thread(target=self._rafraichir, name="classement-gics",
                                 daemon=True).start()
            else:
                self._rafraichir()
        with self._verrou:
            titres, inconnus = {}, []
            for t in tickers:
                gics = self._table.get(normaliser(t))
                if gics is None:
                    if t not in inconnus:
                        inconnus.append(t)
                    continue
                d = domaine(gics[0], gics[1])
                titres[t] = {"domaine": d, "famille": famille(d),
                             "secteur": gics[0], "sous_industrie": gics[1]}
            return {"ok": True, "titres": titres, "inconnus": inconnus,
                    "en_cours": self._en_cours, "erreur": self._erreur,
                    "mis_a_jour": (date.fromtimestamp(self._date).isoformat()
                                   if self._date else None),
                    "source": "GICS, table des constituants du S&P 500 (Wikipedia)"}

    def _rafraichir(self):
        try:
            neuf = self._telecharger()
            with self._verrou:
                table = dict(self._table or {})
                # Cumulatif : un titre sorti de l'indice garde son classement.
                table.update(neuf)
                self._table = table
                self._date = self._horloge()
                self._erreur = None
            try:
                ecrire_cache(table, self.chemin)
            except OSError as exc:
                with self._verrou:
                    self._erreur = "cache non ecrit (%s) : classement garde en memoire" % exc
        except Exception as exc:                 # reseau, page changee : jamais fatal
            with self._verrou:
                self._erreur = "%s : %s" % (type(exc).__name__, str(exc)[:160])
        finally:
            with self._verrou:
                self._en_cours = False
