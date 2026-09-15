"""Construction du portefeuille : selection puis ponderation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .diversification import choisir, matrice_correlation
from .volatilite import exposition, volatilite_ex_ante


def cap_weights(w: np.ndarray, cap: float, max_iter: int = 100) -> np.ndarray:
    """Plafonne chaque poids a `cap` et redistribue l'exces sur les autres.

    Si le plafond rend impossible une exposition totale de 100% (par exemple
    10 lignes plafonnees a 5%), le reliquat reste en liquidites plutot que de
    violer la contrainte : une limite de concentration n'est pas negociable.
    """
    w = np.asarray(w, dtype="float64").copy()
    w[~np.isfinite(w)] = 0.0
    w = np.clip(w, 0.0, None)
    total = w.sum()
    if total <= 0:
        return w
    w /= total
    if cap is None or cap >= 1.0:
        return w

    for _ in range(max_iter):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        under = ~over & (w > 0)
        room = float((cap - w[under]).sum()) if under.any() else 0.0
        if room <= 1e-12:
            break  # plus de place : l'exces reste en liquidites
        share = (cap - w[under]) / room
        w[under] += np.minimum(excess * share, cap - w[under])
    return w


def parametres_diversification(cfg) -> dict | None:
    """Lit le bloc `portfolio.diversification`.

    Renvoie None quand la contrainte est inactive - et c'est le defaut. Un
    module qui recoit None doit se comporter EXACTEMENT comme avant son
    existence : une amelioration qui change les resultats en silence est
    indiscernable d'une regression.
    """
    if not cfg.get("portfolio.diversification.active", False):
        return None
    return {
        "correlation_max": float(cfg.get("portfolio.diversification.correlation_max", 0.35)),
        "fenetre": int(cfg.get("portfolio.diversification.fenetre", 252)),
        "min_obs": int(cfg.get("portfolio.diversification.min_obs", 60)),
        "candidats": float(cfg.get("portfolio.diversification.candidats", 3.0)),
    }


def rendements_correlation(close: pd.DataFrame, raw_close: pd.DataFrame) -> pd.DataFrame:
    """Matrice de rendements propre au calcul des correlations.

    On part des cours prolonges (pour que la reprise apres suspension porte la
    variation complete) mais on REMET A NaN les jours sans cotation reelle. Un
    jour sans cours n'est pas un jour a rendement nul : le laisser a zero
    diluerait artificiellement la correlation d'un titre peu liquide et le
    ferait passer pour un bon diversificateur.
    """
    return close.pct_change().where(raw_close.notna())


def target_weights(
    score: pd.DataFrame,
    vol: pd.DataFrame,
    dates: pd.DatetimeIndex,
    top_n: int,
    weighting: str = "inv_vol",
    max_weight: float = 0.10,
    eligible: pd.DataFrame | None = None,
    rendements: pd.DataFrame | None = None,
    diversification: dict | None = None,
    volatilite: dict | None = None,
) -> pd.DataFrame:
    """Poids cibles aux dates de signal fournies.

    Pour chaque date : on garde les `top_n` meilleurs scores parmi les titres
    eligibles, puis on pondere.

    * equal   : equipondere. Simple, robuste, difficile a battre.
    * inv_vol : poids proportionnel a 1/volatilite. Egalise la contribution au
      risque de chaque ligne au lieu d'egaliser les montants investis, ce qui
      evite qu'un seul titre tres volatil pilote la performance.

    Contrainte de correlation (optionnelle)
    ---------------------------------------
    Quand `diversification` est fourni ET que `rendements` l'est aussi, la
    selection n'est plus un simple `nlargest` : on descend le classement en
    ecartant les candidats trop correles a ce qui est deja retenu (voir
    `diversification.choisir`). La correlation est calculee UNIQUEMENT sur les
    rendements anterieurs ou egaux a la date de signal - une matrice calculee
    sur toute la periode rendrait le backtest sans valeur.
    """
    cols = score.columns
    out = pd.DataFrame(0.0, index=dates, columns=cols)

    pilotage = bool(volatilite) and rendements is not None
    actif = bool(diversification) and rendements is not None
    if actif:
        correlation_max = float(diversification.get("correlation_max", 0.35))
        fenetre = int(diversification.get("fenetre", 252))
        min_obs = int(diversification.get("min_obs", 60))
        multiple = float(diversification.get("candidats", 3.0))

    for date in dates:
        if date not in score.index:
            continue
        s = score.loc[date]
        if eligible is not None and date in eligible.index:
            s = s.where(eligible.loc[date].astype(bool))
        s = s.dropna()
        if s.empty:
            continue

        if actif:
            # Le vivier doit etre plus large que le portefeuille, sinon il n'y
            # a rien a arbitrer : sans candidats de rechange, la contrainte ne
            # peut qu'accepter le classement tel quel.
            n_cand = min(len(s), max(top_n, int(round(top_n * multiple))))
            candidats = s.nlargest(n_cand)
            # Decoupe POSITIONNELLE, pas `rendements.loc[:date]` : cette
            # derniere recopie tout l'historique a chaque rebalancement
            # (des centaines de millions de valeurs sur un backtest complet)
            # alors que seules les `fenetre` dernieres seances servent.
            # Meme resultat, et la causalite est aussi explicite : on ne peut
            # pas lire au-dela de `fin`.
            fin = int(rendements.index.searchsorted(date, side="right"))
            debut = max(0, fin - fenetre)
            corr = matrice_correlation(
                rendements.iloc[debut:fin], candidats.index, fenetre, min_obs)
            picks = pd.Index(choisir(candidats, corr, top_n, correlation_max))
        else:
            picks = s.nlargest(min(top_n, len(s))).index

        if weighting == "equal":
            raw = np.ones(len(picks))
        elif weighting == "inv_vol":
            v = vol.loc[date, picks].to_numpy(dtype="float64") if date in vol.index else np.full(len(picks), np.nan)
            # Une volatilite manquante prend la mediane du groupe : on ne
            # veut ni exclure le titre, ni lui donner un poids aberrant.
            median = np.nanmedian(v) if np.isfinite(v).any() else 1.0
            v = np.where(np.isfinite(v) & (v > 1e-6), v, median)
            raw = 1.0 / v
        else:
            raise ValueError(f"weighting inconnu : {weighting!r}")

        poids = cap_weights(raw, max_weight)

        if pilotage:
            # Meme decoupe positionnelle que pour les correlations : on ne lit
            # jamais au-dela de la date de signal.
            fin = int(rendements.index.searchsorted(date, side="right"))
            debut = max(0, fin - int(volatilite["fenetre"]))
            estimee = volatilite_ex_ante(
                rendements.iloc[debut:fin], pd.Series(poids, index=picks),
                fenetre=int(volatilite["fenetre"]),
                min_obs=int(volatilite["min_obs"]))
            poids = poids * exposition(estimee, volatilite["cible"],
                                       volatilite["mini"], volatilite["maxi"])

        out.loc[date, picks] = poids

    return out


def rebalance_dates(index: pd.DatetimeIndex, frequency: str = "monthly") -> pd.DatetimeIndex:
    """Dernier jour de bourse de chaque periode, calcule sans `resample`.

    On passe par les periodes calendaires pour rester compatible avec toutes
    les versions de pandas (les alias 'M'/'ME' ont change entre 2.x et 3.x).
    """
    idx = pd.DatetimeIndex(index).sort_values()
    freq_map = {"weekly": "W", "monthly": "M", "quarterly": "Q"}
    if frequency not in freq_map:
        raise ValueError(f"rebalance inconnu : {frequency!r}")
    periods = idx.to_period(freq_map[frequency])
    last_positions = pd.Series(np.arange(len(idx)), index=periods).groupby(level=0).max()
    return idx[np.sort(last_positions.to_numpy())]
