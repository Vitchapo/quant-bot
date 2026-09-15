"""Choisir des titres qui ne bougent pas tous ensemble.

Le probleme mesure
------------------
Un portefeuille de vingt lignes issues d'un classement de momentum affiche une
correlation moyenne de 0,21 entre ses titres, contre 0,12 pour vingt titres
tires au hasard dans le meme univers - et une volatilite de 35 % contre 14 %.
Autrement dit : vingt lignes qui se comportent comme quatre.

La cause est structurelle, pas accidentelle. Un score de momentum classe en
tete les titres qui ont le plus monte ; or ce sont, presque par construction,
les plus volatils, et ils appartiennent souvent au meme secteur puisque les
secteurs montent ensemble. Le classement produit donc mecaniquement un pari
sectoriel concentre, que personne n'a decide.

Pourquoi la ponderation inverse-vol n'y peut rien
-------------------------------------------------
Elle repartit l'argent APRES que la selection n'a retenu que des titres
volatils et correles. Egaliser les contributions au risque entre vingt titres
qui montent et descendent ensemble ne diversifie rien : cela repartit
differemment un seul et meme pari. La contrainte doit agir au moment de
CHOISIR.

Ce que fait ce module
---------------------
Une selection gloutonne : on prend le meilleur score, puis on descend le
classement en n'acceptant un candidat que si sa correlation moyenne aux titres
DEJA retenus reste sous un plafond. Le classement du score est respecte -
on ne prend jamais un moins bon score si un meilleur passe la contrainte -
mais on saute ceux qui ne feraient que redoubler un pari existant.

Si le plafond est trop serre pour remplir le portefeuille, il est desserre par
paliers plutot que de livrer un portefeuille incomplet : mieux vaut un peu
trop de correlation qu'une poche de liquidites non voulue. Le nombre de lignes
demande est garanti quel que soit le reglage - c'est une invariante testee.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def matrice_correlation(rendements: pd.DataFrame, tickers, fenetre: int = 252,
                        min_obs: int = 60):
    """Correlations entre les seuls candidats, sur la fenetre recente.

    On ne calcule jamais la matrice complete de l'univers : seuls les
    candidats comptent, et passer de 500x500 a 60x60 rend la contrainte
    gratuite a l'echelle d'un backtest.
    """
    presents = [t for t in tickers if t in rendements.columns]
    if len(presents) < 2:
        return None
    sub = rendements[presents].tail(fenetre)
    valides = sub.columns[sub.notna().sum() >= min_obs]
    if len(valides) < 2:
        return None
    return sub[valides].corr()


def choisir(scores: pd.Series, correlations, top_n: int, correlation_max: float,
            paliers=(0.0, 0.10, 0.25, 1.0)):
    """Selection gloutonne sous contrainte de correlation.

    `scores` est deja restreint aux titres eligibles et trie par ordre
    decroissant. Renvoie la liste des tickers retenus, dans l'ordre de choix.
    """
    classement = list(scores.index)
    if correlations is None or top_n >= len(classement):
        return classement[:top_n]

    for relachement in paliers:
        plafond = correlation_max + relachement
        retenus = []
        for ticker in classement:
            if len(retenus) >= top_n:
                break
            if ticker not in correlations.columns:
                # Sans correlation calculable, on ne penalise pas le titre :
                # l'absence de donnee n'est pas une raison de l'ecarter.
                retenus.append(ticker)
                continue
            if not retenus:
                retenus.append(ticker)
                continue
            connus = [t for t in retenus if t in correlations.columns]
            if not connus:
                retenus.append(ticker)
                continue
            moyenne = float(np.nanmean(correlations.loc[ticker, connus].to_numpy()))
            if not np.isfinite(moyenne) or moyenne <= plafond:
                retenus.append(ticker)
        if len(retenus) >= top_n:
            return retenus[:top_n]

    # Filet de securite : les paliers sont RELATIFS a `correlation_max`, donc
    # un plafond tres bas peut n'etre jamais atteignable (avec -1.0, le
    # dernier palier plafonne encore a 0.0). On complete alors dans l'ordre du
    # classement plutot que de livrer un portefeuille a moitie vide.
    #
    # On garde le noyau deja diversifie au lieu de repartir du classement brut :
    # un plafond trop serre exprime une volonte de diversification, y repondre
    # par le portefeuille le MOINS diversifie serait l'inverse de la demande.
    deja = set(retenus)
    for ticker in classement:
        if len(retenus) >= top_n:
            break
        if ticker not in deja:
            retenus.append(ticker)
            deja.add(ticker)
    return retenus[:top_n]


def diagnostic(rendements: pd.DataFrame, tickers, fenetre: int = 252) -> dict:
    """Correlation moyenne, volatilite equiponderee et nombre de paris
    reellement independants d'un panier donne."""
    tickers = [t for t in tickers if t in rendements.columns]
    if len(tickers) < 2:
        return {}
    sub = rendements[tickers].tail(fenetre)
    c = sub.corr().to_numpy()
    n = c.shape[0]
    correl = float(np.nanmean(c[np.triu_indices(n, 1)]))
    poids = np.full(n, 1.0 / n)
    vol = float((sub.fillna(0.0) @ poids).std() * np.sqrt(252))
    return {"n": n, "correlation": correl, "volatilite": vol,
            "paris_independants": n / (1 + (n - 1) * max(correl, 0.0))}
