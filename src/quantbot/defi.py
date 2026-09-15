"""Garde-fous de perte, au format des defis de prop firm.

Pourquoi ils vivent ici et pas dans la strategie
------------------------------------------------
Une limite de perte n'est pas un signal : elle ne dit rien sur ce qu'il faut
acheter. C'est un INTERRUPTEUR, et son seul role est d'arreter la machine
avant qu'un seuil contractuel ne soit franchi. Le melanger a la selection
donnerait une strategie dont les decisions dependent du chemin parcouru, donc
impossible a comparer a un backtest.

Le principe de la marge
-----------------------
Les seuils par defaut sont SOUS ceux du defi : 4 % quand le contrat dit 5 %,
8 % quand il dit 10 %. Un garde-fou qui se declenche pile a la limite ne sert
a rien - entre le moment ou le bot decide et celui ou l'ordre est execute, le
marche continue de bouger. La marge est ce qui separe "s'arreter" de "se faire
arreter".

Le plus-haut, et pourquoi il est stocke
---------------------------------------
La perte maximale d'un defi se mesure depuis le plus haut atteint, pas depuis
le depart. Le courtier ne connait pas ce plus-haut : il faut le tenir soi-meme,
sur disque, faute de quoi il repartirait a zero a chaque redemarrage et la
limite ne voudrait plus rien dire.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ETAT_DEFI = Path("data/defi_etat.json")

#: Marges par defaut, volontairement sous les seuils contractuels usuels.
PERTE_JOUR_DEFAUT = 0.04     # le contrat dit typiquement 5 %
PERTE_TOTALE_DEFAUT = 0.08   # le contrat dit typiquement 10 %


def lire_etat(chemin=None) -> dict:
    chemin = Path(chemin) if chemin else ETAT_DEFI
    if not chemin.exists():
        return {}
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def ecrire_etat(etat: dict, chemin=None) -> None:
    chemin = Path(chemin) if chemin else ETAT_DEFI
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(etat, indent=1, sort_keys=True), encoding="utf-8")


def parametres(cfg):
    """Lit `defi`. None = garde-fous inactifs, et c'est le defaut."""
    if not cfg.get("defi.active", False):
        return None
    return {
        "perte_jour_max": float(cfg.get("defi.perte_jour_max", PERTE_JOUR_DEFAUT)),
        "perte_totale_max": float(cfg.get("defi.perte_totale_max", PERTE_TOTALE_DEFAUT)),
        "objectif": float(cfg.get("defi.objectif", 0.10)),
    }


def evaluer(equity: float, equity_veille: float, params: dict,
            etat: dict = None, aujourdhui=None) -> dict:
    """Ou en est le compte par rapport aux limites du defi ?

    `equity_veille` est la valeur a la cloture precedente - `last_equity` chez
    Alpaca. C'est bien cette reference que mesure une limite journaliere, et
    non le plus haut du jour.

    Renvoie l'etat a jour ET le verdict. L'appelant decide quoi en faire :
    ce module constate, il n'arrete rien tout seul.
    """
    etat = dict(etat or {})
    equity = float(equity or 0.0)
    aujourdhui = str(aujourdhui or date.today())

    if equity <= 0:
        return {"etat": etat, "ok": True, "raison": "", "equity": equity,
                "perte_jour": 0.0, "perte_totale": 0.0, "progression": 0.0}

    depart = float(etat.get("capital_depart") or equity)
    plus_haut = max(float(etat.get("plus_haut") or equity), equity)
    etat.update({"capital_depart": depart, "plus_haut": plus_haut,
                 "derniere_maj": aujourdhui})

    veille = float(equity_veille or 0.0) or equity
    perte_jour = equity / veille - 1.0 if veille > 0 else 0.0
    perte_totale = equity / plus_haut - 1.0
    progression = equity / depart - 1.0

    raisons = []
    if perte_jour <= -params["perte_jour_max"]:
        raisons.append("perte du jour %.2f %% (limite %.2f %%)"
                       % (100 * perte_jour, -100 * params["perte_jour_max"]))
    if perte_totale <= -params["perte_totale_max"]:
        raisons.append("perte depuis le plus haut %.2f %% (limite %.2f %%)"
                       % (100 * perte_totale, -100 * params["perte_totale_max"]))

    return {"etat": etat, "ok": not raisons, "raison": " ; ".join(raisons),
            "equity": equity, "depart": depart, "plus_haut": plus_haut,
            "perte_jour": perte_jour, "perte_totale": perte_totale,
            "progression": progression,
            "objectif_atteint": progression >= params["objectif"]}


def resume(v: dict, params: dict) -> str:
    """Une ligne lisible pour le tableau de bord."""
    if not v.get("equity"):
        return "compte vide"
    texte = ("jour %+.2f %% (limite %.0f %%), depuis le plus haut %+.2f %% "
             "(limite %.0f %%), progression %+.2f %% sur %.0f %% vises"
             % (100 * v["perte_jour"], -100 * params["perte_jour_max"],
                100 * v["perte_totale"], -100 * params["perte_totale_max"],
                100 * v["progression"], 100 * params["objectif"]))
    if v.get("objectif_atteint"):
        texte += "  -- OBJECTIF ATTEINT"
    return texte
