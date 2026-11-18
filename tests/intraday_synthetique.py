"""Barres de 30 minutes FABRIQUEES, au format de MT5, pour tester les etapes 1 et 2
du momentum intrajournalier sans aucune vraie donnee.

Chaque seance a 20 barres, de 8:00 a 18:00 heure locale de la bourse, posees a
l'heure du serveur selon une convention au choix :
  "new_york"  heure de New York + 7 h : ce que dit FTMO ;
  "athenes"   heure d'Athenes (GMT+2, GMT+3 l'ete, au rythme europeen) : la meme
              sauf pendant les semaines ou l'Europe et les Etats-Unis n'ont pas
              encore tous les deux change d'heure.
Le volume (nombre de ticks) triple a l'ouverture du marche au comptant de
New York (9:30) pour US500. Les cours suivent une marche au hasard sans
aucun effet ; la barre d'apres s'ouvre a la cloture de la barre d'avant.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantbot import intraday_donnees as I

#: Un protocole aux cases encore vides, fait des passages dont les scripts des
#: etapes 1 et 2 ont besoin (copies du protocole tel que commite le
#: 18/11/2026, 444c403). Les tests n'utilisent pas le vrai protocole : ses
#: cases se remplissent au fil des etapes.
PROTOCOLE = """# Protocole fabrique pour les tests

## Ce qui est fig\u00e9

### Les donn\u00e9es

- **Calendrier des s\u00e9ances :** les jours de cotation de chaque indice dans ses
  cours quotidiens du protocole de tendance (fichiers bruts scell\u00e9s \u00e0 son
  \u00e9tape 2, `5f3e492`), jusqu'au 30 octobre 2026. Un jour o\u00f9 le march\u00e9 au
  comptant est ferm\u00e9 n'est jamais une s\u00e9ance, m\u00eame si le CFD cote.

### La fen\u00eatre et la position

La position ne d\u00e9pend que du calendrier et de cours d\u00e9j\u00e0 connus \u00e0 l'entr\u00e9e.

### Les co\u00fbts FTMO

Aucune commission sur les indices FTMO (tableau des co\u00fbts du protocole de
tendance), et aucun swap : la position la plus tardive, US500, est ferm\u00e9e \u00e0
23:00 heure du serveur, avant le passage de minuit.

### La p\u00e9riode

- Rendements jug\u00e9s : **avril 2018 \u00e0 octobre 2026**.

---

## Les cinq crit\u00e8res, au seuil t > 2,5

| # | crit\u00e8re | mesure | seuil | r\u00e9sultat |
|---|---|---|---|---|
| 1 | l'effet existe | r\u00e9gression | pente > 0, t > 2,5 | |

## \u00c9tapes

| # | \u00e9tape | fait le |
|---|---|---|
| 0 | relire, valider, committer et pousser ce protocole | |
| 1 | co\u00fbts : relever les spreads FTMO aux heures exactes d'entr\u00e9e et de sortie (historique de ticks), \
consigner le tableau, committer | |
| 2 | donn\u00e9es : t\u00e9l\u00e9charger une fois les barres de 30 minutes, contr\u00f4ler (heure du serveur, barres \
manquantes, cours fig\u00e9s, sauts), consigner empreintes et exclusions, committer | |
| 5 | appliquer la d\u00e9cision | |
"""


TYPE_BARRE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
              ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]
PAR_JOUR = 20                      # de 8:00 a 18:00, heure locale
EPOQUE = pd.Timestamp("1970-01-01")


def seances(debut: str, fin: str) -> pd.DatetimeIndex:
    """Les jours ouvres, sans aucun ferie."""
    return pd.bdate_range(debut, fin)


def heures_locales(jours) -> pd.DatetimeIndex:
    j = pd.DatetimeIndex(jours)
    offs = pd.Timedelta(hours=8) + pd.to_timedelta(np.arange(PAR_JOUR) * 30, unit="min")
    return pd.DatetimeIndex((j.values[:, None] + offs.values[None, :]).ravel())


def barres(indice: str, jours, graine: int = 0, serveur: str = "new_york", prix0: float = 5000.0,
           sigma: float = 0.001) -> np.ndarray:
    rng = np.random.default_rng(graine)
    local = heures_locales(jours).tz_localize(I.FUSEAUX[indice])
    if serveur == "new_york":
        srv = local.tz_convert(I.NEW_YORK).tz_localize(None) + I.DECALAGE_SERVEUR
    elif serveur == "athenes":
        srv = local.tz_convert("Europe/Athens").tz_localize(None)
    else:
        raise ValueError(serveur)
    n = len(srv)
    r = rng.normal(0.0, sigma, n)
    g = np.zeros(n)
    g[::PAR_JOUR] = rng.normal(0.0, 5 * sigma, n // PAR_JOUR)          # la nuit
    log_c = np.log(prix0) + np.cumsum(g + r)
    c = np.exp(log_c)
    o = np.exp(log_c - r)
    b = np.zeros(n, dtype=TYPE_BARRE)
    b["time"] = (srv - EPOQUE) // pd.Timedelta(seconds=1)
    b["open"], b["close"] = o, c
    b["high"] = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sigma / 3, n)))
    b["low"] = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sigma / 3, n)))
    vol = rng.integers(900, 1100, n)
    if indice == "US500":
        vol[np.asarray(local.tz_localize(None).time == pd.Timestamp("09:30").time())] *= 3
    b["tick_volume"] = vol
    b["spread"] = 50
    return b


def position_fenetre(indice: str, jours) -> np.ndarray:
    """Pour chaque seance, la position de la barre de fenetre dans ses 20 barres :
    calculee a l'heure LOCALE, sans passer par l'heure du serveur."""
    h = I.heure_locale_debut(indice, jours)
    return np.asarray((h - pd.Timedelta(hours=8)) // pd.Timedelta(minutes=30), dtype=int)


def clotures_yahoo(indice: str, jours, b: np.ndarray, bruit: float = 1e-4, graine: int = 1) -> pd.Series:
    """Les clotures quotidiennes d'une source "Yahoo" : la cloture de la barre de
    fenetre, a un bruit pres (heure de releve et base differentes)."""
    j = pd.DatetimeIndex(jours)
    k = np.arange(len(j)) * PAR_JOUR + position_fenetre(indice, j)
    rng = np.random.default_rng(graine)
    return pd.Series(b["close"][k] * np.exp(rng.normal(0, bruit, len(j))), index=j)


def ecrire_yahoo(chemin: Path, s: pd.Series) -> None:
    """Au format des fichiers Yahoo du protocole de tendance (dates a minuit UTC)."""
    with open(str(chemin), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "UTC"},
            "timestamp": [int((t - EPOQUE).total_seconds()) for t in s.index],
            "indicators": {"quote": [{"close": [float(v) for v in s]}]}}]}}))


def en_dataframe(b: np.ndarray) -> pd.DataFrame:
    """Comme quantbot.intraday_donnees.lire_barres, sans passer par un fichier."""
    d = pd.DataFrame({c: b[c] for c in I.COLONNES[1:]})
    d.index = I.depuis_mt5(b["time"])
    d.index.name = "debut"
    return d
