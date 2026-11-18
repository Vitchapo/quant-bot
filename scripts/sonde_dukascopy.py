#!/usr/bin/env python3
"""Sonde : Dukascopy a-t-il un historique de minutes complet pour les 6 indices ?

    python scripts/sonde_dukascopy.py

Le protocole du momentum intrajournalier s'est arrete a l'etape 2 : chez FTMO,
les barres de 30 minutes ne commencent qu'en septembre 2021, avec une barre par
jour avant. Avant d'ecrire un nouveau protocole sur un autre historique, on
regarde celui de Dukascopy : des CFD d'indices, des bougies d'une minute
gratuites, horodatees en UTC.

Lecon de la sonde FTMO : il ne suffit pas de verifier qu'il y a des barres,
il faut COMPTER les minutes actives de chaque jour et regarder la fenetre
elle-meme. Pour chaque indice et chaque annee, de 2010 a 2026 :
  - les jours qui ont une bougie quotidienne (fichier annuel, volume > 0) ;
  - une seance par mois (celle du calendrier la plus proche du 15) : le
    fichier de ses minutes, ses minutes actives (volume > 0) sur 1 440, celles
    de la fenetre (les 30 minutes avant la cloture du marche au comptant) ;
  - le rapport entre le volume des 30 minutes qui suivent l'ouverture du
    marche au comptant et celui des 30 minutes qui la precedent. Si l'heure
    des bougies est juste, l'ouverture se voit : le rapport depasse 1, en
    hiver comme en ete.
Puis le premier jour qui a des minutes, cherche jour par jour.

Il n'ecrit et n'affiche AUCUN cours : des dates, des comptes et des rapports
de volumes. Les cours des fichiers sont jetes des leur lecture. Sortie :
data/intraday/sonde_dukascopy.csv. Compter 5 a 10 minutes.
"""
from __future__ import annotations

import argparse
import csv
import lzma
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import _bootstrap  # noqa: F401

import numpy as np
import pandas as pd

from quantbot import intraday_donnees as I
from quantbot import tendance_donnees as TD

RACINE = Path(__file__).resolve().parent.parent
SORTIE = RACINE / "data" / "intraday" / "sonde_dukascopy.csv"
BASE = "https://datafeed.dukascopy.com/datafeed/"
CODES = {"US500": "USA500IDXUSD", "GER40": "DEUIDXEUR", "UK100": "GBRIDXGBP",
         "JP225": "JPNIDXJPY", "AUS200": "AUSIDXAUD", "HK50": "HKGIDXHKD"}
#: Ouverture du marche au comptant, heure locale.
OUVERTURES = {"US500": "09:30", "GER40": "09:00", "UK100": "08:00", "JP225": "09:00",
              "AUS200": "10:00", "HK50": "09:30"}
ANNEES = tuple(range(2010, 2027))
FIN = pd.Timestamp("2026-10-30")
UA = "quantbot/1.0 (sonde de l'historique Dukascopy)"
PAUSE_S = 0.1
ESSAIS = 3
BOUGIE = np.dtype([("t", ">i4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"), ("v", ">f4")])
DEMI = pd.Timedelta(minutes=30)
COLONNES = ["sonde_utc", "instrument", "code", "annee", "jours_quotidiens", "seances_sondees",
            "avec_minutes", "minutes_actives_mediane", "fenetres_completes", "fenetre_actives_mediane",
            "rapport_ouverture_median", "premier_jour_minutes"]


def dire(texte: str) -> None:
    print(texte, flush=True)


def _texte(x: float) -> str:
    return "" if np.isnan(x) else "%.2f" % x


def _mediane(valeurs) -> float:
    """La mediane des valeurs qui en sont (NaN ecartes) ; NaN s'il n'y en a pas."""
    v = [x for x in valeurs if not np.isnan(x)]
    return float(np.median(v)) if v else float("nan")


# -- les fichiers ---------------------------------------------------------------------
def url_jour(code: str, jour) -> str:
    """Les bougies d'une minute d'un jour UTC (le mois compte a partir de 00)."""
    j = pd.Timestamp(jour)
    return BASE + "%s/%04d/%02d/%02d/BID_candles_min_1.bi5" % (code, j.year, j.month - 1, j.day)


def url_annee(code: str, annee: int) -> str:
    return BASE + "%s/%04d/BID_candles_day_1.bi5" % (code, annee)


def telecharger(url: str, ouvrir=urllib.request.urlopen, attendre=time.sleep):
    """Les octets du fichier ; None s'il n'existe pas."""
    requete = urllib.request.Request(url, headers={"User-Agent": UA})
    for k in range(ESSAIS):
        try:
            with ouvrir(requete, timeout=30) as rep:
                return rep.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (429, 500, 502, 503, 504) and k + 1 < ESSAIS:
                attendre(2.0 * (k + 1))
                continue
            raise
        except (urllib.error.URLError, socket.timeout, ConnectionError):
            if k + 1 < ESSAIS:
                attendre(2.0 * (k + 1))
                continue
            raise
    return None


def lire_bougies(brut) -> tuple:
    """(secondes depuis le debut du fichier, volumes). Les cours ne sont pas gardes."""
    if not brut:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    data = lzma.decompress(brut)
    if len(data) % BOUGIE.itemsize:
        raise ValueError("fichier de %d octets : pas des bougies de %d octets" % (len(data), BOUGIE.itemsize))
    b = np.frombuffer(data, dtype=BOUGIE)
    return b["t"].astype(np.int64), b["v"].astype(float)


class Source:
    """Les fichiers de Dukascopy, telecharges une fois chacun."""

    def __init__(self, ouvrir=urllib.request.urlopen, attendre=time.sleep):
        self.ouvrir, self.attendre = ouvrir, attendre
        self.jours, self.demandes = {}, 0

    def _get(self, url):
        self.demandes += 1
        brut = telecharger(url, self.ouvrir, self.attendre)
        self.attendre(PAUSE_S)
        return brut

    @staticmethod
    def _lire(url, brut) -> tuple:
        try:
            return lire_bougies(brut)
        except (lzma.LZMAError, ValueError) as exc:
            raise ValueError("%s : fichier illisible (%s)" % (url, exc))

    def jour(self, code: str, jour):
        """(minutes UTC, volumes) d'un jour UTC, ou None s'il n'y a pas de fichier."""
        j = pd.Timestamp(jour).normalize()
        if (code, j) not in self.jours:
            url = url_jour(code, j)
            brut = self._get(url)
            if not brut:
                self.jours[(code, j)] = None
            else:
                t, v = self._lire(url, brut)
                self.jours[(code, j)] = (j + pd.to_timedelta(t, unit="s"), v)
        return self.jours[(code, j)]

    def quotidiens(self, code: str, annee: int):
        """Les jours de l'annee a volume non nul (bougies quotidiennes), ou None."""
        url = url_annee(code, annee)
        brut = self._get(url)
        if not brut:
            return None
        _, v = self._lire(url, brut)
        return int((v > 0).sum())

    def intervalle(self, code: str, debut, fin) -> tuple:
        """(minutes, volumes) de [debut, fin[ (UTC), sur un ou deux fichiers ; None sans fichier."""
        morceaux = []
        dernier = (pd.Timestamp(fin) - pd.Timedelta(seconds=1)).normalize()
        for j in pd.date_range(pd.Timestamp(debut).normalize(), dernier):
            x = self.jour(code, j)
            if x is None:
                return None
            morceaux.append(x)
        t = pd.DatetimeIndex(np.concatenate([np.asarray(m[0]) for m in morceaux]))
        v = np.concatenate([m[1] for m in morceaux])
        garde = np.asarray((t >= debut) & (t < fin))
        return t[garde], v[garde]


# -- les mesures ---------------------------------------------------------------------
def en_utc(indice: str, jour, heure) -> pd.Timestamp:
    """Une heure locale de la bourse ("HH:MM" ou Timedelta) -> heure UTC (naive)."""
    if isinstance(heure, str):
        heure = pd.Timedelta(heure + ":00")
    local = pd.Timestamp(jour).normalize() + heure
    return local.tz_localize(I.FUSEAUX[indice]).tz_convert("UTC").tz_localize(None)


def fenetre_utc(indice: str, jour) -> tuple:
    debut = en_utc(indice, jour, I.heure_locale_debut(indice, [jour])[0])
    return debut, debut + I.DUREE


def seances_sondees(seances, annee: int) -> list:
    """Une seance par mois : celle du calendrier la plus proche du 15."""
    s = pd.DatetimeIndex(seances)
    out = []
    for mois in range(1, 13):
        cible = pd.Timestamp(annee, mois, 15)
        dans = s[(s.year == annee) & (s.month == mois)]
        if len(dans):
            out.append(dans[np.argmin(np.abs((dans - cible).days))])
    return out


def mesurer(src: Source, indice: str, seance) -> dict:
    """Les comptes d'une seance : minutes actives du jour UTC, de la fenetre, et l'ouverture."""
    code = CODES[indice]
    j = src.jour(code, seance)
    out = {"seance": seance, "avec_minutes": j is not None and len(j[1]) > 0, "actives": 0,
           "fenetre_actives": 0, "fenetre_complete": False, "rapport_ouverture": float("nan")}
    if not out["avec_minutes"]:
        return out
    out["actives"] = int((j[1] > 0).sum())
    debut, fin = fenetre_utc(indice, seance)
    f = src.intervalle(code, debut, fin)
    if f is not None:
        t, v = f
        actives = set(t[v > 0])
        out["fenetre_actives"] = len(actives)
        out["fenetre_complete"] = debut in actives and (fin - pd.Timedelta(minutes=1)) in actives
    o = en_utc(indice, seance, OUVERTURES[indice])
    x = src.intervalle(code, o - DEMI, o + DEMI)
    if x is not None:
        t, v = x
        avant, apres = v[np.asarray(t < o)].sum(), v[np.asarray(t >= o)].sum()
        if avant > 0:
            out["rapport_ouverture"] = float(apres / avant)
        elif apres > 0:
            out["rapport_ouverture"] = float("inf")          # rien avant l'ouverture : elle se voit aussi
    return out


def premier_jour(src: Source, code: str, seances, sans, avec):
    """Le premier jour avec des minutes, entre une seance sans et une seance avec (dichotomie)."""
    s = pd.DatetimeIndex(seances)
    s = s[(s > sans) & (s <= avec)]
    bas, haut = 0, len(s) - 1
    while bas < haut:
        m = (bas + haut) // 2
        x = src.jour(code, s[m])
        if x is not None and len(x[1]) > 0 and (x[1] > 0).any():
            haut = m
        else:
            bas = m + 1
    return s[haut] if len(s) else avec


def sonder(src: Source, calendrier: dict, maintenant=None, dire=dire) -> list:
    maintenant = maintenant or pd.Timestamp.now(tz="UTC")
    lignes = []
    for n in I.INDICES:
        code = CODES[n]
        seances = calendrier[n].index
        seances = seances[seances <= FIN]
        dire("%s (%s) :" % (n, code))
        mesures, par_annee = [], {}
        for a in ANNEES:
            q = src.quotidiens(code, a)
            m = [mesurer(src, n, s) for s in seances_sondees(seances, a)]
            mesures += m
            par_annee[a] = (q, m)
            avec = [x for x in m if x["avec_minutes"]]
            dire("    %d : %s jours quotidiens ; minutes %d/%d seances%s"
                 % (a, "-" if q is None else q, len(avec), len(m),
                    "" if not avec else " ; %d minutes actives par jour ; fenetre complete %d/%d ; ouverture x%.1f"
                    % (np.median([x["actives"] for x in avec]), sum(x["fenetre_complete"] for x in avec),
                       len(avec), _mediane([x["rapport_ouverture"] for x in avec]))))
        avec = [x["seance"] for x in mesures if x["avec_minutes"]]
        sans = [x["seance"] for x in mesures if not x["avec_minutes"] and (not avec or x["seance"] < min(avec))]
        premier = None
        if avec:
            bas = max(sans) if sans else seances.min() - pd.Timedelta(days=1)
            premier = premier_jour(src, code, seances, bas, min(avec))
            dire("    premier jour avec des minutes : %s" % premier.date())
        for a, (q, m) in par_annee.items():
            avec_a = [x for x in m if x["avec_minutes"]]
            lignes.append({
                "sonde_utc": maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"), "instrument": n, "code": code,
                "annee": a, "jours_quotidiens": "" if q is None else q, "seances_sondees": len(m),
                "avec_minutes": len(avec_a),
                "minutes_actives_mediane": int(np.median([x["actives"] for x in avec_a])) if avec_a else "",
                "fenetres_completes": sum(x["fenetre_complete"] for x in avec_a),
                "fenetre_actives_mediane": (int(np.median([x["fenetre_actives"] for x in avec_a]))
                                            if avec_a else ""),
                "rapport_ouverture_median": _texte(_mediane([x["rapport_ouverture"] for x in avec_a])),
                "premier_jour_minutes": "" if premier is None else premier.strftime("%Y-%m-%d")})
    return lignes


def ecrire(lignes: list, chemin: Path = SORTIE) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(str(chemin), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLONNES, lineterminator="\n")
        w.writeheader()
        w.writerows(lignes)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    os.chdir(RACINE)
    cal = I.calendrier(RACINE / TD.BRUT)
    src = Source()
    try:
        lignes = sonder(src, cal)
    except urllib.error.HTTPError as exc:
        print("Dukascopy refuse la demande (%s %s) apres %d fichiers. La sonde s'arrete : on ne force pas."
              % (exc.code, exc.reason, src.demandes))
        return 2
    except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
        print("Dukascopy injoignable (%s) apres %d fichiers." % (exc, src.demandes))
        return 2
    except ValueError as exc:
        print("La sonde s'arrete : %s" % exc)
        return 2
    ecrire(lignes)
    print()
    print("%d fichiers lus. Comptes ecrits dans %s" % (src.demandes, SORTIE.relative_to(RACINE).as_posix()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
