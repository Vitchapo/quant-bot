"""Du portefeuille cible aux ordres a passer. Aucune connexion reseau ici.

Tout ce module est une fonction pure : positions detenues + poids cibles +
cours -> liste d'ordres. C'est deliberе. La partie qui decide ce qu'on achete
doit etre testable sans courtier, sans jeton d'authentification et sans risque
d'envoyer un ordre par accident pendant un test.

Deux regles a connaitre :

* un ecart de reponderation inferieur a `seuil_notional` est IGNORE. En
  dessous, les frais coutent plus que la precision gagnee, et un portefeuille
  qui se rebalance a l'euro pres paie sa propre precision.
* la SORTIE d'une ligne absente de la cible est toujours executee, quel que
  soit son montant. Laisser trainer des lignes que la strategie ne veut plus
  est un risque, pas une economie de frais.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

VENTE = "sell"
ACHAT = "buy"

#: En dessous de ce montant, une ligne n'est plus une position : c'est un
#: residu d'arrondi sur des fractions de titre. Aucun courtier ne l'executera,
#: et le lui envoyer a chaque passage produit un echec permanent.
SEUIL_POUSSIERE = 0.01


@dataclass
class Ordre:
    ticker: str
    sens: str                  # "buy" ou "sell"
    quantite: Optional[float]  # en titres ; None si l'ordre est en montant
    montant: float             # valeur absolue en devise du compte
    cours: float
    poids_actuel: float
    poids_cible: float
    motif: str                 # "entree", "renforcement", "allegement", "sortie"

    def as_dict(self) -> dict:
        return asdict(self)


def seuil_minimal(cfg, equity: float) -> float:
    """Montant en dessous duquel un ajustement ne vaut pas ses frais.

    Le plus grand entre un POURCENTAGE de la valeur du compte et un plancher
    absolu. Un seuil purement absolu ne peut pas convenir a deux tailles de
    compte : 25 USD represente 2,5 % d'un compte de 1000 USD et 0,025 % d'un
    compte de 100 000 USD. Dans le second cas il laisse passer des ordres de
    trente dollars qui ne font que payer des frais pour suivre le bruit.
    """
    return max(float(cfg.get("broker.min_order_pct", 0.0025) or 0.0) * float(equity),
               float(cfg.get("broker.min_order_notional", 0.0) or 0.0))


def _motif(actuel: float, cible: float) -> str:
    if actuel <= 1e-9:
        return "entree"
    if cible <= 1e-9:
        return "sortie"
    return "renforcement" if cible > actuel else "allegement"


def planifier(cibles: dict, positions: dict, cours: dict, equity: float,
              seuil_notional: float = 0.0, max_notional: Optional[float] = None,
              fractionnaire: bool = True) -> list:
    """Renvoie les ordres qui font passer `positions` a `cibles`.

    `positions` est en NOMBRE DE TITRES, `cibles` en poids (fraction de
    `equity`). Les ventes sont renvoyees avant les achats : chez la plupart des
    courtiers, le produit d'une vente n'est disponible qu'une fois l'ordre
    execute, et commencer par les achats ferait echouer la moitie des ordres
    faute de liquidites.
    """
    if equity <= 0:
        return []

    tickers = sorted(set(cibles) | set(positions))
    ordres = []
    for ticker in tickers:
        prix = float(cours.get(ticker, 0.0) or 0.0)
        detenu = float(positions.get(ticker, 0.0) or 0.0)
        if prix <= 0:
            # Sans cours, on ne sait ni valoriser ni dimensionner : on ne touche
            # pas a la ligne et l'appelant est prevenu par `sans_cours`.
            continue
        valeur_actuelle = detenu * prix
        poids_actuel = valeur_actuelle / equity
        poids_cible = float(cibles.get(ticker, 0.0) or 0.0)
        valeur_cible = poids_cible * equity
        delta = valeur_cible - valeur_actuelle
        if abs(delta) < 1e-9:
            continue

        sortie = poids_cible <= 1e-9 and detenu > 0
        if not sortie and abs(delta) < seuil_notional:
            continue

        montant = abs(delta)
        if max_notional is not None and montant > max_notional:
            montant = max_notional

        if sortie:
            # On solde exactement ce qui est detenu : passer par un montant
            # laisserait une poussiere de position derriere lui.
            quantite = detenu
            montant = detenu * prix
        elif delta < 0:
            quantite = montant / prix
            if not fractionnaire:
                quantite = float(int(quantite))
            quantite = min(quantite, detenu)
            montant = quantite * prix
        else:
            quantite = montant / prix if not fractionnaire else None
            if quantite is not None:
                quantite = float(int(quantite))
                montant = quantite * prix

        # On ARRONDIT AVANT de valider, jamais l'inverse : `round(1e-7, 6)`
        # vaut exactement 0.0, et un ordre a quantite nulle est refuse par le
        # courtier a chaque passage, indefiniment.
        quantite = None if quantite is None else round(quantite, 6)
        if (quantite is not None and quantite <= 0) or montant < SEUIL_POUSSIERE:
            continue
        ordres.append(Ordre(
            ticker=ticker, sens=VENTE if delta < 0 else ACHAT,
            quantite=quantite,
            montant=round(montant, 2), cours=round(prix, 4),
            poids_actuel=poids_actuel, poids_cible=poids_cible,
            motif=_motif(poids_actuel, poids_cible)))

    ordres.sort(key=lambda o: (o.sens != VENTE, o.ticker))
    return ordres


def poussieres(cibles: dict, positions: dict, cours: dict) -> list:
    """Lignes residuelles trop petites pour etre soldees.

    Une fraction de titre valant un millieme de centime reste au bilan sans
    qu'aucun ordre puisse s'en debarrasser. Les signaler une fois vaut mieux
    que de les reessayer a chaque passage : c'est une anomalie a connaitre,
    pas une erreur a repeter.
    """
    out = []
    for ticker, detenu in positions.items():
        prix = float(cours.get(ticker, 0.0) or 0.0)
        detenu = float(detenu or 0.0)
        if prix <= 0 or detenu <= 0:
            continue
        valeur = detenu * prix
        if valeur < SEUIL_POUSSIERE or round(detenu, 6) <= 0:
            out.append({"ticker": ticker, "titres": detenu, "valeur": valeur,
                        "visee": float(cibles.get(ticker, 0.0) or 0.0)})
    return sorted(out, key=lambda x: x["ticker"])


def sans_cours(cibles: dict, positions: dict, cours: dict) -> list:
    """Tickers vises ou detenus dont on n'a pas de cours : a traiter a la main."""
    return sorted(t for t in set(cibles) | set(positions)
                  if not float(cours.get(t, 0.0) or 0.0) > 0)


def resume(ordres: list) -> dict:
    achats = sum(o.montant for o in ordres if o.sens == ACHAT)
    ventes = sum(o.montant for o in ordres if o.sens == VENTE)
    return {"n_ordres": len(ordres), "achats": round(achats, 2),
            "ventes": round(ventes, 2), "echange": round(achats + ventes, 2)}
