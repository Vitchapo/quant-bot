"""Piloter la volatilite du portefeuille au lieu de la subir.

L'HYPOTHESE, ecrite AVANT de regarder le resultat
--------------------------------------------------
Enonce : a volatilite cible constante, le portefeuille doit afficher un Sharpe
au moins egal et une perte maximale nettement inferieure au portefeuille
actuel, dont la volatilite flotte librement entre 10 % et 40 % selon les
periodes.

Justification, qui vient de l'EXTERIEUR des donnees - c'est ce qui distingue
une hypothese d'une trouvaille :

  1. La volatilite des actions est persistante (elle s'autocorrele fortement),
     ce qui rend la volatilite RECENTE predictive de la volatilite a venir -
     alors que le rendement recent, lui, ne predit pas le rendement a venir.
     On pilote donc la seule des deux grandeurs qui soit previsible.
  2. Les rendements ne sont PAS proportionnels a la volatilite : les periodes
     les plus agitees delivrent en moyenne des rendements plus faibles, pas
     plus eleves. Reduire l'exposition quand la volatilite monte retire donc
     de l'exposition la ou elle est le moins bien payee.

Ce que ce module NE FAIT PAS
----------------------------
Il ne cree aucun avantage de selection. Le score composite reste aussi vide
qu'avant - IC t = 0,17, deciles en U, -13,6 points une fois l'univers corrige.
Piloter la volatilite d'un portefeuille tire d'un classement sans information
donne un portefeuille sans information a volatilite pilotee. Ce qui peut
changer, c'est le RAPPORT rendement/risque et la profondeur des pertes, pas
l'esperance de gain.

Verdict accepte d'avance, quel qu'il soit : une hypothese testee une fois ne
se re-teste pas avec d'autres reglages jusqu'a ce qu'elle passe. C'est
exactement ce mecanisme qui a produit le faux positif du score composite -
le meilleur de vingt essais au hasard atteint deja un Sharpe de 0,78.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Volatilite annualisee visee par defaut, si le pilotage est active sans cible.
VOL_CIBLE_DEFAUT = 0.12

#: Bornes de l'exposition. Le plafond vaut 1.0 : DEPASSER 1 SIGNIFIE EMPRUNTER,
#: et le backtest ne facture aucun interet sur l'argent emprunte. Tout resultat
#: obtenu au-dessus de 1.0 est donc optimiste d'un cout qui n'est pas modelise.
EXPOSITION_MIN = 0.10
EXPOSITION_MAX = 1.00


def volatilite_ex_ante(rendements: pd.DataFrame, poids, fenetre: int = 63,
                       min_obs: int = 40):
    """Volatilite annualisee du panier, estimee sur la covariance recente.

    On calcule `sqrt(w' Σ w)` plutot que l'ecart-type des rendements passes du
    portefeuille, pour une raison de fond : le portefeuille d'aujourd'hui n'a
    pas les memes lignes que celui d'il y a trois mois. Sa volatilite passee
    decrit d'autres positions. La covariance des titres REELLEMENT detenus
    repond a la bonne question - quel risque porte le panier que je m'apprete
    a tenir - et elle reagit immediatement a un changement de composition.

    Renvoie None si l'estimation n'est pas fiable : l'appelant doit alors
    s'abstenir de piloter plutot que de piloter au hasard.
    """
    poids = pd.Series(poids).dropna()
    poids = poids[poids > 0]
    if poids.empty:
        return None
    presents = [t for t in poids.index if t in rendements.columns]
    if len(presents) < 2:
        return None

    sub = rendements[presents].tail(fenetre)
    valides = sub.columns[sub.notna().sum() >= min_obs]
    if len(valides) < 2:
        return None

    w = poids[valides].to_numpy(dtype="float64")
    total = w.sum()
    if total <= 0:
        return None
    w = w / total                      # on mesure la vol du panier, pas du levier

    cov = sub[valides].cov().to_numpy(dtype="float64")
    if not np.isfinite(cov).all():
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
    variance = float(w @ cov @ w)
    if not np.isfinite(variance) or variance <= 0:
        return None
    return float(np.sqrt(variance * 252.0))


def exposition(vol_estimee, vol_cible: float,
               mini: float = EXPOSITION_MIN, maxi: float = EXPOSITION_MAX) -> float:
    """Facteur par lequel multiplier les poids pour viser `vol_cible`.

    Sans estimation exploitable, on renvoie 1.0 : ne rien faire est le seul
    comportement defendable quand on ne sait pas. Piloter sur une volatilite
    inventee serait pire que de ne pas piloter.
    """
    if vol_estimee is None or not np.isfinite(vol_estimee) or vol_estimee <= 1e-9:
        return 1.0
    return float(np.clip(vol_cible / vol_estimee, mini, maxi))


def parametres(cfg):
    """Lit `portfolio.volatilite`. None = pilotage inactif, et c'est le defaut.

    Un module qui recoit None doit se comporter EXACTEMENT comme s'il
    n'existait pas : une amelioration qui modifie les resultats sans qu'on
    l'ait demandee est indiscernable d'une regression.
    """
    if not cfg.get("portfolio.volatilite.active", False):
        return None
    maxi = float(cfg.get("portfolio.volatilite.exposition_max", EXPOSITION_MAX))
    return {
        "cible": float(cfg.get("portfolio.volatilite.cible", VOL_CIBLE_DEFAUT)),
        "fenetre": int(cfg.get("portfolio.volatilite.fenetre", 63)),
        "min_obs": int(cfg.get("portfolio.volatilite.min_obs", 40)),
        "mini": float(cfg.get("portfolio.volatilite.exposition_min", EXPOSITION_MIN)),
        "maxi": maxi,
        "levier": maxi > 1.0,
    }
