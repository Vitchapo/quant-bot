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

Statique ou glissante : ce n'est PAS un detail de reglage
---------------------------------------------------------
Les defis mesurent la perte maximale de deux facons, et l'ecart est enorme :

  statique  : la limite basse est fixe a -10 % du solde de DEPART. Les gains
              accumules deviennent un matelas definitif. Parti de 100 000 $,
              le plancher reste 90 000 $ quoi qu'il arrive ensuite.
  glissante : la limite suit le plus haut atteint. Monte a 108 000 $, le
              plancher monte a 97 200 $ - on peut donc etre elimine en etant
              ENCORE EN GAIN par rapport au depart.

Le defaut est `statique`, parce que c'est ce qu'annonce le defi vise
(Stellar 2-Step : "Static"). Se tromper de regle dans un sens arrete le bot
sans aucune raison contractuelle ; dans l'autre, le laisse franchir une limite
reelle. La premiere erreur coute des gains, la seconde coute le defi.

Depart ET plus-haut sont tous deux stockes sur disque : le courtier ne connait
ni l'un ni l'autre, et sans persistance ils repartiraient de la valeur du jour
a chaque redemarrage - la limite ne voudrait alors plus rien dire.

Le denominateur de la perte JOURNALIERE
---------------------------------------
Cette version mesure la perte du jour en fraction du CAPITAL DE DEPART, pas
de l'equity de la veille. C'est la regle du contrat : "5 % of initial
balance". Les deux formules coincident au premier jour et divergent ensuite,
dans le sens le plus desagreable. Parti de 100 000 $ et monte a 200 000 $, une
seance a -4 % coute 8 000 $ : le contrat compte 8 % de son plafond
journalier, l'ancienne formule affichait 4 % et laissait passer. Rapporter une
perte a une base qui bouge revient a mesurer une limite fixe avec une regle
elastique.

Le VERROU : pourquoi une breche doit survivre au rebond
-------------------------------------------------------
Constater une breche puis l'oublier des que l'equity remonte, c'est ne rien
constater du tout. Un defi franchi est franchi definitivement, et surtout : un
bot qui vient de solder ses positions se retrouve avec un compte VIDE, ce qui
est exactement la condition d'amorcage. Sans verrou ecrit sur disque, la
sequence etait : perte -> liquidation -> compte vide -> "tiens, un compte
neuf" -> rachat de tout le portefeuille le lendemain matin, dans le marche
meme qui venait de declencher la limite.

Le verrou est donc pose sur disque, il ne se leve jamais tout seul, et il
bloque aussi bien l'envoi que l'amorcage. `lever_verrou()` est le seul chemin
pour repartir, et il est volontairement manuel.

L'OBJECTIF ATTEINT est aussi un motif d'arret
---------------------------------------------
L'ancienne version calculait `objectif_atteint` et n'en faisait rien : le bot
continuait a s'exposer apres avoir gagne la phase. C'est le seul moment du
defi ou le rapport risque/gain est strictement defavorable - la phase est
acquise, chaque seance supplementaire ne peut que la reprendre. Atteindre
l'objectif pose donc le verrou lui aussi, avec un motif distinct.

Ce module CONSTATE. Il ne vend rien, ne touche pas au courtier, n'a aucune
dependance reseau. C'est l'appelant - `operations.appliquer_defi()` - qui
traduit un verrou en ordres de vente.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ETAT_DEFI = Path("data/defi_etat.json")

#: Marges par defaut, volontairement sous les seuils contractuels usuels.
PERTE_JOUR_DEFAUT = 0.04     # le contrat dit typiquement 5 %
PERTE_TOTALE_DEFAUT = 0.08   # le contrat dit typiquement 10 %

#: Motifs de verrou. Les deux bloquent l'envoi ; seul le libelle change.
VERROU_PERTE = "perte"
VERROU_OBJECTIF = "objectif"


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
        # Une cle vide ("reference:" sans valeur) vaut None en YAML : on la
        # ramene au defaut plutot que de fabriquer la chaine "none", qui
        # s'afficherait telle quelle sur le tableau de bord.
        "reference": (str(cfg.get("defi.reference", "") or "statique").lower()),
        # Solder a la pose du verrou. Par defaut oui : rester expose apres
        # avoir franchi -8 % alors que le contrat elimine a -10 % est le pire
        # des deux mondes. Mettre false donne le comportement "gel seul", ou
        # c'est l'operateur qui solde a la main.
        "solder": bool(cfg.get("defi.solder_sur_verrou", True)),
    }


def est_verrouille(etat: dict) -> bool:
    return bool((etat or {}).get("verrou"))


def lever_verrou(etat: dict) -> dict:
    """Retire le verrou. SEUL chemin de retour, et volontairement manuel.

    Ne touche ni a `capital_depart` ni a `plus_haut` : lever un verrou n'est
    pas commencer un nouveau defi. Pour repartir de zero, il faut supprimer le
    fichier d'etat - un geste explicite, pas un effet de bord.
    """
    etat = dict(etat or {})
    etat.pop("verrou", None)
    etat.pop("a_solder", None)
    return etat


def marquer_solde(etat: dict) -> dict:
    """Les positions ont ete soldees : le verrou reste, la consigne tombe."""
    etat = dict(etat or {})
    etat["a_solder"] = False
    return etat


def _libelle(verrou: dict) -> str:
    if (verrou or {}).get("raison") == VERROU_OBJECTIF:
        return "VERROU (objectif atteint le %s) : %s" % (
            verrou.get("depuis", "?"), verrou.get("detail", ""))
    return "VERROU (limite franchie le %s) : %s" % (
        (verrou or {}).get("depuis", "?"), (verrou or {}).get("detail", ""))


def evaluer(equity: float, equity_veille: float, params: dict,
            etat: dict = None, aujourdhui=None) -> dict:
    """Ou en est le compte par rapport aux limites du defi ?

    `equity_veille` est la valeur a la cloture precedente - `last_equity` chez
    Alpaca. C'est bien cette reference que mesure une limite journaliere, et
    non le plus haut du jour.

    Renvoie l'etat a jour ET le verdict. L'appelant decide quoi en faire :
    ce module constate, il n'arrete rien tout seul.

    Clefs du verdict qui pilotent l'appelant :
      ok             : False des qu'un verrou est pose (ancien ou nouveau)
      verrou         : le dictionnaire du verrou, ou None
      nouveau_verrou : True uniquement au passage ou il vient d'etre pose.
                       C'est ce drapeau qui doit declencher la liquidation et
                       un cri dans le journal - et une seule fois.
      a_solder       : il reste des positions a solder pour honorer le verrou
    """
    etat = dict(etat or {})
    equity = float(equity or 0.0)
    aujourdhui = str(aujourdhui or date.today())

    if equity <= 0:
        return {"etat": etat, "ok": True, "raison": "", "equity": equity,
                "perte_jour": 0.0, "perte_totale": 0.0, "progression": 0.0,
                "verrou": etat.get("verrou"), "nouveau_verrou": False,
                "a_solder": bool(etat.get("a_solder"))}

    depart = float(etat.get("capital_depart") or equity)
    plus_haut = max(float(etat.get("plus_haut") or equity), equity)
    etat.update({"capital_depart": depart, "plus_haut": plus_haut,
                 "derniere_maj": aujourdhui})

    # La perte du jour est rapportee au CAPITAL DE DEPART, comme le contrat.
    # Voir l'en-tete du module : rapporter une limite fixe a une base qui
    # bouge, c'est mesurer au metre elastique.
    veille = float(equity_veille or 0.0) or equity
    perte_jour = (equity - veille) / depart if depart > 0 else 0.0

    # STATIQUE ou GLISSANTE : voir l'en-tete du module. Appliquer une regle
    # glissante a un defi qui annonce "Static" arrete le bot alors qu'il est
    # encore en gain ; l'inverse le laisse franchir une limite reelle.
    reference = plus_haut if params.get("reference") == "glissante" else depart
    perte_totale = equity / reference - 1.0
    progression = equity / depart - 1.0
    objectif_atteint = progression >= params["objectif"]

    raisons = []
    if perte_jour <= -params["perte_jour_max"]:
        raisons.append("perte du jour %.2f %% du depart (limite %.2f %%)"
                       % (100 * perte_jour, -100 * params["perte_jour_max"]))
    if perte_totale <= -params["perte_totale_max"]:
        raisons.append("perte depuis %s %.2f %% (limite %.2f %%)"
                       % ("le plus haut" if params.get("reference") == "glissante"
                          else "le depart",
                          100 * perte_totale, -100 * params["perte_totale_max"]))

    # -- le verrou ---------------------------------------------------------
    # Un verrou deja pose n'est PAS reevalue : il ne se leve pas parce que
    # l'equity a rebondi. Voir l'en-tete du module (sequence liquidation ->
    # compte vide -> amorcage -> rachat).
    verrou = etat.get("verrou")
    nouveau = False
    if verrou is None and (raisons or objectif_atteint):
        verrou = {
            "raison": VERROU_PERTE if raisons else VERROU_OBJECTIF,
            "detail": " ; ".join(raisons) if raisons
                      else ("progression %+.2f %% sur %.0f %% vises"
                            % (100 * progression, 100 * params["objectif"])),
            "depuis": aujourdhui,
            "equity": equity,
        }
        etat["verrou"] = verrou
        etat["a_solder"] = bool(params.get("solder", True))
        nouveau = True

    return {"etat": etat,
            "ok": verrou is None,
            "raison": _libelle(verrou) if verrou else "",
            "equity": equity, "depart": depart, "plus_haut": plus_haut,
            "perte_jour": perte_jour, "perte_totale": perte_totale,
            "progression": progression,
            "objectif_atteint": objectif_atteint,
            "verrou": verrou, "nouveau_verrou": nouveau,
            "a_solder": bool(etat.get("a_solder"))}


def resume(v: dict, params: dict) -> str:
    """Une ligne lisible pour le tableau de bord."""
    if not v.get("equity"):
        return "compte vide"
    texte = ("jour %+.2f %% du depart (limite %.0f %%), depuis %s %+.2f %% "
             "(limite %.0f %%), progression %+.2f %% sur %.0f %% vises"
             % (100 * v["perte_jour"], -100 * params["perte_jour_max"],
                "le plus haut" if params.get("reference") == "glissante" else "le depart",
                100 * v["perte_totale"], -100 * params["perte_totale_max"],
                100 * v["progression"], 100 * params["objectif"]))
    if v.get("verrou"):
        texte = _libelle(v["verrou"]) + "  --  " + texte
        if v.get("a_solder"):
            texte += "  --  POSITIONS A SOLDER"
    elif v.get("objectif_atteint"):
        texte += "  -- OBJECTIF ATTEINT"
    return texte
