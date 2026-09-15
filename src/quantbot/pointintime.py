"""Appartenance historique a l'indice (point-in-time).

Le biais que ce module corrige - et celui qu'il ne corrige PAS
--------------------------------------------------------------
Le backtest tourne sur les constituants ACTUELS du S&P 500. Cela produit deux
biais distincts, qu'on confond souvent :

1. SELECTION ANTICIPEE. En 2010, le backtest peut acheter NVDA parce qu'elle
   figure dans la liste d'aujourd'hui - alors qu'a l'epoque, personne n'avait
   de raison de la surveiller au titre de l'indice. Le backtest choisit dans
   un vivier compose avec la connaissance de la suite. Ce module corrige
   exactement ce biais.

2. SURVIVANTS DISPARUS. Lehman Brothers, Bear Stearns, Circuit City et des
   centaines d'autres ont quitte la cote. Leurs rendements - souvent tres
   negatifs - manquent au calcul. Ce module NE CORRIGE PAS ce biais : savoir
   que Lehman appartenait a l'indice en 2007 ne sert a rien tant qu'on n'a pas
   sa serie de cours, et le cache n'en contient aucune.

Autrement dit : la moitie du probleme. La performance restera optimiste apres
activation ; elle le sera simplement moins. Le diagnostic ci-dessous chiffre
precisement ce qui manque encore, pour que personne ne confonde "corrige" et
"corrige a moitie".

Fiabilite de la source
----------------------
La table Wikipedia s'intitule "Selected changes to the list of S&P 500
components". SELECTED : elle est incomplete, et d'autant plus en remontant
loin. La reconstruction est donc une APPROXIMATION dont la qualite se degrade
avec l'anciennete. `Appartenance.qualite()` renvoie l'effectif reconstitue
annee par annee : tant qu'il reste proche de 500, la reconstruction tient ;
quand il s'en ecarte franchement, elle ne vaut plus rien et il faut acheter de
vraies donnees point-in-time (CRSP, Sharadar, Norgate).
"""
from __future__ import annotations

import bisect
import logging

import numpy as np
import pandas as pd

from .universe import HTTP_HEADERS, WIKI_SP500_URL, _sp500_from_wikipedia

logger = logging.getLogger(__name__)

#: Effectif nominal de l'indice, pour juger la reconstruction.
EFFECTIF_NOMINAL = 500


class PointInTimeError(RuntimeError):
    """La reconstruction n'a pas pu etre faite telle qu'elle est exigee."""


def normaliser_ticker(valeur) -> str:
    """'BRK.B' -> 'BRK-B'. Meme convention que le reste du projet (Yahoo)."""
    if valeur is None:
        return ""
    texte = str(valeur).strip()
    if not texte or texte.lower() in ("nan", "none", "-", "—", "--"):
        return ""
    # Les notes de bas de page Wikipedia laissent parfois '[1]' colle au texte.
    texte = texte.split("[")[0].strip()
    return texte.replace(".", "-").upper()


def normaliser_changements(table: pd.DataFrame) -> pd.DataFrame:
    """Table brute Wikipedia -> colonnes date / ajoute / retire / motif.

    La table a des en-tetes sur deux niveaux ('Added' > 'Ticker'), et pandas
    les renvoie en MultiIndex ou aplaties selon la version. On reconnait donc
    les colonnes par leur contenu textuel plutot que par une position fixe :
    une page qui gagne une colonne ne doit pas silencieusement decaler la
    lecture.
    """
    cols = []
    for c in table.columns:
        parties = [str(x) for x in c] if isinstance(c, tuple) else [str(c)]
        parties = [p for p in parties if not p.lower().startswith("unnamed")]
        cols.append(" ".join(parties).strip().lower())

    def trouver(*mots, exclure=()):
        for i, nom in enumerate(cols):
            if all(m in nom for m in mots) and not any(x in nom for x in exclure):
                return i
        return None

    i_date = trouver("date")
    i_ajout = trouver("added", "ticker") or trouver("added", "symbol")
    i_retrait = trouver("removed", "ticker") or trouver("removed", "symbol")
    i_motif = trouver("reason")

    if i_date is None or (i_ajout is None and i_retrait is None):
        raise PointInTimeError(
            "table de changements non reconnue (colonnes : %s)" % cols)

    brut = table.to_numpy(dtype=object)
    lignes = []
    for row in brut:
        date = pd.to_datetime(str(row[i_date]).split("[")[0].strip(), errors="coerce")
        if pd.isna(date):
            continue
        lignes.append({
            "date": date,
            "ajoute": normaliser_ticker(row[i_ajout]) if i_ajout is not None else "",
            "retire": normaliser_ticker(row[i_retrait]) if i_retrait is not None else "",
            "motif": str(row[i_motif]) if i_motif is not None else "",
        })
    if not lignes:
        raise PointInTimeError("aucune ligne de changement exploitable")
    return pd.DataFrame(lignes).sort_values("date").reset_index(drop=True)


def lire_changements_wikipedia(timeout: float = 20.0) -> pd.DataFrame:
    """Telecharge et normalise la table des changements. Necessite le reseau."""
    import io

    import requests

    resp = requests.get(WIKI_SP500_URL, timeout=timeout, headers=HTTP_HEADERS)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))

    derniere = None
    for table in tables:
        try:
            normalisee = normaliser_changements(table)
        except PointInTimeError:
            continue
        if derniere is None or len(normalisee) > len(derniere):
            derniere = normalisee
    if derniere is None:
        raise PointInTimeError(
            "aucune table de changements trouvee sur %s. La page a change de "
            "structure." % WIKI_SP500_URL)
    return derniere


class Appartenance:
    """Composition de l'indice par periode, reconstituee a rebours.

    `segments` est une liste (date_de_debut, membres) triee par date. La
    composition applicable a une date est celle du dernier segment commence
    a cette date ou avant.
    """

    def __init__(self, segments, actuels, changements):
        self.segments = segments
        self._debuts = [d for d, _ in segments]
        self.actuels = frozenset(actuels)
        self.changements = changements

    # -- lecture -----------------------------------------------------------
    def membres(self, date) -> frozenset:
        date = pd.Timestamp(date)
        i = bisect.bisect_right(self._debuts, date) - 1
        if i < 0:
            return self.segments[0][1]
        return self.segments[i][1]

    def masque(self, index, colonnes) -> pd.DataFrame:
        """Matrice booleenne (dates x tickers) : True = membre ce jour-la.

        Destinee a etre combinee au filtre d'eligibilite du backtest par un
        simple ET logique.
        """
        index = pd.DatetimeIndex(index)
        colonnes = list(colonnes)
        position = {t: i for i, t in enumerate(colonnes)}

        debuts = np.array(self._debuts, dtype="datetime64[ns]")
        seg = np.searchsorted(debuts, index.values, side="right") - 1
        seg = np.clip(seg, 0, len(self.segments) - 1)

        out = np.zeros((len(index), len(colonnes)), dtype=bool)
        for j in np.unique(seg):
            vecteur = np.zeros(len(colonnes), dtype=bool)
            for t in self.segments[int(j)][1]:
                k = position.get(t)
                if k is not None:
                    vecteur[k] = True
            out[seg == j] = vecteur
        return pd.DataFrame(out, index=index, columns=colonnes)

    def effectif(self) -> pd.Series:
        return pd.Series([len(m) for _, m in self.segments],
                         index=pd.DatetimeIndex(self._debuts), name="effectif")

    # -- honnetete ---------------------------------------------------------
    def qualite(self, colonnes_disponibles=None) -> dict:
        """Chiffre ce que vaut la reconstruction, et ce qui manque encore."""
        eff = self.effectif()
        reels = eff[eff.index > pd.Timestamp("1900-01-01")]
        par_an = reels.groupby(reels.index.year).median()

        info = {
            "n_changements": int(len(self.changements)),
            "n_segments": int(len(self.segments)),
            "premiere_date": str(reels.index.min().date()) if len(reels) else None,
            "effectif_median_par_an": {int(k): int(v) for k, v in par_an.items()},
            "ecart_max_au_nominal": int((par_an - EFFECTIF_NOMINAL).abs().max())
                                     if len(par_an) else None,
        }

        if colonnes_disponibles is not None:
            disponibles = set(colonnes_disponibles)
            historiques = set()
            for _, m in self.segments:
                historiques |= m
            manquants = historiques - disponibles
            info["n_membres_historiques"] = len(historiques)
            info["n_sans_cours"] = len(manquants)
            info["part_sans_cours"] = (len(manquants) / len(historiques)
                                       if historiques else 0.0)
            info["exemples_sans_cours"] = sorted(manquants)[:15]
        return info


def reconstruire(actuels, changements: pd.DataFrame) -> Appartenance:
    """Remonte le temps depuis la composition ACTUELLE.

    Regle appliquee a chaque changement date d : la composition AVANT d est
    celle d'apres d, moins les entrants, plus les sortants. On empile ainsi
    un segment par date de changement, du plus recent au plus ancien.

    Un entrant absent de la composition courante (entre puis ressorti depuis)
    est simplement ignore, et un sortant deja present n'est pas duplique :
    la table est incomplete, elle doit pouvoir l'etre sans faire echouer la
    reconstruction.
    """
    courant = {normaliser_ticker(t) for t in actuels}
    courant.discard("")
    if not courant:
        raise PointInTimeError("composition actuelle vide")

    ch = changements.copy()
    ch["date"] = pd.to_datetime(ch["date"])
    ch = ch.sort_values("date")

    segments = []
    for date, groupe in sorted(ch.groupby("date"), key=lambda kv: kv[0], reverse=True):
        segments.append((pd.Timestamp(date), frozenset(courant)))
        for _, ligne in groupe.iterrows():
            entrant = normaliser_ticker(ligne.get("ajoute"))
            sortant = normaliser_ticker(ligne.get("retire"))
            if entrant:
                courant.discard(entrant)
            if sortant:
                courant.add(sortant)

    # Avant la premiere date connue, on ne sait rien de mieux : on prolonge la
    # composition la plus ancienne reconstituee. C'est une hypothese, et
    # `qualite()` est la pour dire a partir de quand elle devient douteuse.
    segments.append((pd.Timestamp.min, frozenset(courant)))
    segments.reverse()
    return Appartenance(segments, actuels, ch)


def construire(cfg=None, timeout: float = 20.0) -> Appartenance:
    """Chemin complet avec reseau : composition actuelle + changements."""
    actuels = _sp500_from_wikipedia(timeout=timeout)
    changements = lire_changements_wikipedia(timeout=timeout)
    appartenance = reconstruire(actuels, changements)
    logger.info("appartenance reconstituee : %d changements, %d segments",
                len(changements), len(appartenance.segments))
    return appartenance
