"""Savoir que le bot va mal AVANT de perdre de l'argent.

Deux angles morts, tous deux verifies absents du projet jusqu'ici.

1. LE ROBOT S'ARRETE SANS RIEN DIRE
   PC en veille, tache planifiee en echec, cle API expiree, courtier
   indisponible : le robot cesse simplement de tourner. Rien ne previent. Le
   portefeuille reste fige sur une allocation decidee des semaines plus tot,
   et la decouverte se fait en regardant par hasard.

   Pire que l'arret franc : l'arret AU MILIEU. Un rebalancement envoie
   d'abord les ventes puis les achats. Interrompu entre les deux, il laisse
   un portefeuille a moitie solde, moitie en liquidites, qui n'est ni
   l'ancien ni le nouveau - et qu'aucun backtest n'a jamais mesure.

2. LE BOT ET LE COURTIER NE SONT PLUS D'ACCORD
   Le bot recalcule sa cible a partir des cours, puis compare a ce qu'il
   CROIT detenir. Si un ordre n'a ete execute qu'en partie, si un titre a
   fait l'objet d'un split, si une ligne a ete touchee a la main, les deux
   visions divergent. Les ordres suivants sont alors calcules sur un
   portefeuille qui n'existe pas, et l'ecart ne se resorbe jamais tout seul :
   il se propage a chaque rebalancement.

   La poussiere CRL restee dans le compte (0,000000363 titre) est la version
   miniature et inoffensive de ce probleme. La version couteuse, c'est la
   meme chose sur une ligne a 10 %.

Ce module ne corrige rien tout seul - il CONSTATE, il classe, et il donne un
verdict exploitable. Reparer automatiquement un ecart qu'on n'a pas compris
est le meilleur moyen de transformer une anomalie en perte.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: Sous ce nombre de titres, une ligne est de la poussiere : elle vient des
#: arrondis successifs, pas d'une decision. On la signale sans s'en alarmer.
POUSSIERE_TITRES = 1e-4

#: Ecart en valeur sous lequel on ne s'emeut pas, en fraction du portefeuille.
TOLERANCE_DEFAUT = 0.002          # 0,2 %

#: Age maximal du dernier passage du robot avant alerte, en heures.
SILENCE_MAX_HEURES = 30


# ---------------------------------------------------------------------------
# 1. Reconciliation
# ---------------------------------------------------------------------------
def reconcilier(positions_courtier: dict, positions_attendues: dict,
                cours: dict, equity: float,
                tolerance: float = TOLERANCE_DEFAUT,
                fiabilite: dict = None, source: str = "journal") -> dict:
    """Compare ligne a ligne ce que le courtier detient et ce que le bot croit.

    `positions_courtier` et `positions_attendues` sont en NOMBRE DE TITRES.
    Les deux sources sont traitees symetriquement : une ligne presente chez
    l'un et absente chez l'autre est une anomalie dans les deux sens.

    Le classement d'un ecart depend de sa VALEUR, pas du nombre de titres :
    0,5 titre de Berkshire et 0,5 titre d'Intel ne sont pas le meme
    probleme. Sans cours, l'ecart est classe `invalorisable` plutot que
    minimise a zero - une ligne qu'on ne sait pas evaluer est precisement
    celle sur laquelle il ne faut pas fermer les yeux.
    """
    equity = float(equity or 0.0)
    seuil_valeur = max(tolerance * equity, 0.0)

    tickers = sorted(set(positions_courtier) | set(positions_attendues))
    lignes, compte = [], {}
    for ticker in tickers:
        reel = float(positions_courtier.get(ticker, 0.0) or 0.0)
        attendu = float(positions_attendues.get(ticker, 0.0) or 0.0)
        ecart = reel - attendu
        prix = cours.get(ticker)
        prix = float(prix) if prix not in (None, "") else None

        if abs(reel) <= POUSSIERE_TITRES and abs(attendu) <= POUSSIERE_TITRES:
            verdict = "poussiere"
        elif prix is None or prix <= 0:
            verdict = "invalorisable"
        elif abs(ecart * prix) <= seuil_valeur:
            verdict = "conforme"
        elif attendu <= POUSSIERE_TITRES:
            verdict = "inconnue"      # detenue chez le courtier, ignoree du bot
        elif reel <= POUSSIERE_TITRES:
            verdict = "disparue"      # attendue par le bot, absente du compte
        else:
            verdict = "ecart"

        lignes.append({
            "ticker": ticker, "reel": reel, "attendu": attendu,
            "ecart_titres": ecart,
            "ecart_valeur": (ecart * prix) if prix else None,
            "cours": prix, "verdict": verdict,
        })
        compte[verdict] = compte.get(verdict, 0) + 1

    graves = [l for l in lignes if l["verdict"] in ("ecart", "inconnue",
                                                    "disparue", "invalorisable")]
    total = sum(abs(l["ecart_valeur"] or 0.0) for l in graves)
    # Une reconstitution incoherente rend TOUTE comparaison sans objet : on ne
    # transforme pas un defaut d'outil en alerte sur le compte. L'ecart de
    # 110 % annonce le 14 septembre 2026 ne decrivait rien d'autre que le bug
    # de lecture du journal.
    exploitable = fiabilite is None or fiabilite.get("ok", True)
    return {
        "lignes": lignes,
        "anomalies": graves if exploitable else [],
        "compte": compte,
        "ecart_total": total if exploitable else None,
        "ecart_total_pct": (total / equity) if (exploitable and equity > 0) else None,
        "conforme": (not graves) if exploitable else None,
        "exploitable": exploitable,
        "source": source,
        "fiabilite": fiabilite or {"ok": True},
        "equity": equity,
        "seuil_valeur": seuil_valeur,
    }


def positions_executees(ordres_courtier) -> dict:
    """Ce que le COURTIER dit avoir execute. La source qui fait foi.

    Chaque ordre clos porte `filled_qty` : la quantite reellement obtenue,
    y compris pour les ordres passes en montant, dont le bot ne connaissait
    pas le nombre de titres au moment de l'envoi. C'est la seule
    reconstitution fiable.
    """
    positions: dict = {}
    for o in ordres_courtier or []:
        ticker = o.get("symbol")
        if not ticker:
            continue
        try:
            qte = float(o.get("filled_qty") or 0.0)
        except (TypeError, ValueError):
            qte = 0.0
        if qte <= 0:
            continue
        signe = -1.0 if str(o.get("side", "")).lower() == "sell" else 1.0
        positions[ticker] = positions.get(ticker, 0.0) + signe * qte
    return {t: q for t, q in positions.items() if abs(q) > POUSSIERE_TITRES}


def positions_attendues_du_journal(lignes_journal, compte=None) -> tuple:
    """Reconstitue ce que le bot croit detenir a partir de son journal.

    Renvoie (positions, fiable) - et `fiable` n'est pas decoratif.

    LE PIEGE, paye au prix fort
    ---------------------------
    Les achats fractionnaires sont envoyes en MONTANT, pas en nombre de
    titres : leur colonne `quantite` est vide (44 lignes sur 44 dans le
    journal reel). Les ventes, elles, portent toujours une quantite. La
    premiere version ignorait les lignes sans quantite - donc TOUS les achats -
    et n'additionnait que les ventes, en negatif. Chaque position se
    reconstituait a zero ou en dessous, et la reconciliation annoncait
    fierement "21 anomalies, 110 % du portefeuille en desaccord".

    Une position NEGATIVE est impossible pour un bot exclusivement acheteur.
    Quand le calcul en produit une, ce n'est pas le compte qui derive : c'est
    la reconstitution qui est fausse. On le signale au lieu de crier au loup -
    une alerte qui se trompe est pire qu'une absence d'alerte, parce qu'elle
    apprend a ne plus regarder les alertes.
    """
    refuses = {"ECHEC", "rejected", "canceled", "expired", "", None}
    positions: dict = {}
    brut: dict = {}
    approximations = 0
    for ligne in lignes_journal:
        if compte is not None and ligne.get("compte") not in (None, "", compte):
            continue
        if str(ligne.get("statut")) in refuses:
            continue
        ticker = ligne.get("ticker")
        if not ticker:
            continue

        def _nombre(cle):
            try:
                valeur = ligne.get(cle)
                return float(valeur) if valeur not in (None, "") else 0.0
            except (TypeError, ValueError):
                return 0.0

        qte = _nombre("quantite")
        if qte <= 0:
            # Ordre passe en montant : on reconstitue au cours note a l'envoi.
            # C'est une APPROXIMATION - le prix d'execution differe de quelques
            # points de base - d'ou le drapeau et la tolerance qui suivent.
            montant, cours = _nombre("montant"), _nombre("cours")
            if montant > 0 and cours > 0:
                qte = montant / cours
                approximations += 1
        if qte <= 0:
            continue
        signe = -1.0 if str(ligne.get("sens", "")).lower() == "sell" else 1.0
        positions[ticker] = positions.get(ticker, 0.0) + signe * qte
        brut[ticker] = brut.get(ticker, 0.0) + qte

    # Un solde legerement negatif n'est pas une incoherence : reconstituer un
    # achat par `montant / cours` sous-estime la quantite reelle des que
    # l'execution se fait un peu sous le cours note. Vendre ensuite la ligne
    # entiere laisse alors un residu negatif de quelques dixiemes de pour cent.
    # Seul un negatif SIGNIFICATIF - plus de 2 % du volume traite sur la ligne -
    # signale que la lecture du journal est reellement fausse.
    negatives = sorted(t for t, q in positions.items()
                       if q < -max(POUSSIERE_TITRES, 0.02 * brut.get(t, 0.0)))
    positions = {t: q for t, q in positions.items() if q > POUSSIERE_TITRES}
    fiable = {"ok": not negatives, "negatives": negatives,
              "approximations": approximations,
              "message": ("" if not negatives else
                          "Reconstitution incoherente : %d position(s) nettement "
                          "negative(s) (%s). Un bot exclusivement acheteur ne peut "
                          "pas en produire - c'est la lecture du journal qui est "
                          "fausse, pas le compte."
                          % (len(negatives), ", ".join(negatives[:8])))}
    return positions, fiable


# ---------------------------------------------------------------------------
# 2. Veille-homme-mort
# ---------------------------------------------------------------------------
def _horodatage(valeur):
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(str(valeur))
    except ValueError:
        return None


def sante_robot(etat: dict, maintenant=None,
                silence_max_heures: int = SILENCE_MAX_HEURES,
                jours_ouvres_seulement: bool = True) -> dict:
    """Le robot a-t-il donne signe de vie recemment, et avec quel resultat ?

    `etat` est le contenu de `data/robot_etat.json`.

    Le silence est compte en heures, mais la tolerance s'elargit le week-end :
    un robot qui ne tourne pas le dimanche n'est pas en panne. Sans cette
    nuance l'alerte se declenche tous les lundis matin, et une alerte qui crie
    pour rien est une alerte qu'on finit par ignorer - c'est le seul mode de
    defaillance qui rend une surveillance pire que son absence.
    """
    maintenant = maintenant or datetime.now()
    dernier = _horodatage((etat or {}).get("dernier_passage"))
    resultat = (etat or {}).get("dernier_resultat") or "inconnu"

    if dernier is None:
        return {"ok": False, "niveau": "alerte", "heures": None,
                "resultat": resultat,
                "message": "Le robot n'a jamais enregistre de passage. "
                           "Verifie que la tache planifiee existe et tourne."}

    heures = (maintenant - dernier).total_seconds() / 3600.0
    tolerance = float(silence_max_heures)
    if jours_ouvres_seulement:
        # chaque jour non ouvre traverse ajoute 24 h de tolerance
        jour = dernier.date()
        while jour < maintenant.date():
            if jour.weekday() >= 5:
                tolerance += 24.0
            jour += timedelta(days=1)

    if heures > tolerance:
        return {"ok": False, "niveau": "alerte", "heures": heures,
                "resultat": resultat, "tolerance": tolerance,
                "message": "Aucun passage du robot depuis %.0f h (limite %.0f h). "
                           "Dernier resultat connu : %s."
                           % (heures, tolerance, resultat)}

    # Le robot tourne, mais que fait-il ? Un robot qui tourne en refusant
    # systematiquement d'agir est un robot en panne qui a l'air vivant.
    inquietants = {"TROP_TARD": "il a rate la fenetre d'execution",
                   "saute (trop tard)": "il a rate la fenetre d'execution",
                   "ECHEC": "sa derniere tentative a echoue"}
    for cle, explication in inquietants.items():
        if cle.lower() in str(resultat).lower():
            return {"ok": False, "niveau": "attention", "heures": heures,
                    "resultat": resultat, "tolerance": tolerance,
                    "message": "Le robot tourne (%.0f h) mais %s : %r."
                               % (heures, explication, resultat)}

    return {"ok": True, "niveau": "normal", "heures": heures,
            "resultat": resultat, "tolerance": tolerance,
            "message": "Dernier passage il y a %.0f h, resultat : %s."
                       % (heures, resultat)}


def tache_planifiee(nom: str = "quantbot") -> dict:
    """La tache planifiee qui reveille le robot existe-t-elle ?

    Le silence du robot a deux causes tres differentes, et les confondre fait
    perdre des jours : soit la tache existe et echoue, soit elle n'a jamais ete
    creee. La seconde est arrivee ici - `--installer` ecrivait le script de
    lancement, affichait une commande `schtasks` et s'arretait la, sans que
    personne ne la lance. La veille signalait 92 heures de silence sans pouvoir
    dire pourquoi.
    """
    import os
    import subprocess

    if os.name != "nt":
        return {"connu": False, "existe": None,
                "message": "verification possible seulement sous Windows"}
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", nom], capture_output=True)
    except OSError as exc:
        return {"connu": False, "existe": None, "message": str(exc)}
    existe = r.returncode == 0
    return {"connu": True, "existe": existe, "nom": nom,
            "message": ("tache \"%s\" enregistree" % nom) if existe else
                       ("AUCUNE tache \"%s\" dans le planificateur : le robot "
                        "ne peut pas se reveiller tout seul. Lance "
                        "`python scripts/robot.py --installer`." % nom)}


def rebalancement_incomplet(lignes_journal, compte=None) -> dict:
    """Detecte un rebalancement interrompu entre les ventes et les achats.

    Le planificateur envoie TOUJOURS les ventes avant les achats. Une vague
    d'ordres qui ne contient que des ventes, ou dont une partie a echoue,
    laisse le portefeuille dans un etat intermediaire que personne n'a voulu.
    On regroupe donc les ordres par horodatage d'envoi et on examine chaque
    vague.
    """
    vagues: dict = {}
    for ligne in lignes_journal:
        if compte is not None and ligne.get("compte") not in (None, "", compte):
            continue
        cle = str(ligne.get("horodatage", ""))[:16]     # a la minute
        vagues.setdefault(cle, []).append(ligne)

    if not vagues:
        return {"ok": True, "message": "Aucun ordre au journal.", "vagues": []}

    rapport = []
    for cle in sorted(vagues):
        ordres = vagues[cle]
        ventes = [o for o in ordres if str(o.get("sens", "")).lower() == "sell"]
        achats = [o for o in ordres if str(o.get("sens", "")).lower() == "buy"]
        echecs = [o for o in ordres if str(o.get("statut")) in ("ECHEC", "rejected")]
        souci = None
        if ventes and not achats:
            souci = ("%d vente(s) sans aucun achat : le produit des ventes est "
                     "peut-etre reste en liquidites" % len(ventes))
        elif echecs:
            souci = ("%d ordre(s) en echec sur %d : le portefeuille n'a pas "
                     "atteint sa cible" % (len(echecs), len(ordres)))
        rapport.append({"quand": cle, "ordres": len(ordres),
                        "ventes": len(ventes), "achats": len(achats),
                        "echecs": len(echecs), "souci": souci})

    soucis = [v for v in rapport if v["souci"]]
    return {"ok": not soucis, "vagues": rapport, "incidents": soucis,
            "message": ("%d vague(s) d'ordres, %d avec un souci."
                        % (len(rapport), len(soucis)))}


# ---------------------------------------------------------------------------
# 3. Bilan
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sante du coupe-circuit
# ---------------------------------------------------------------------------
#: Au-dela, un coupe-circuit muet n'est plus un incident, c'est une absence de
#: protection. Six passages a dix minutes = une heure de seance sans filet.
ECHECS_AVANT_ALERTE = 6

#: Silence tolere avant de conclure que la tache ne tourne plus du tout.
#: Large a dessein : le coupe-circuit ne sert qu'en seance, et une alerte qui
#: crie tous les week-ends est une alerte qu'on finit par ignorer.
SILENCE_MAX_MINUTES = 90


def sante_coupe_circuit(etat: dict, maintenant=None,
                        echecs_max: int = ECHECS_AVANT_ALERTE,
                        silence_max_minutes: int = SILENCE_MAX_MINUTES) -> dict:
    """Le coupe-circuit journalier protege-t-il REELLEMENT quelque chose ?

    `etat` est le contenu de `data/coupe_circuit_etat.json`.

    Pourquoi cette fonction existe
    ------------------------------
    Un coupe-circuit qui ne joint pas le terminal ne protege rien - mais il
    continue de figurer dans le planificateur, de s'executer a l'heure, et de
    renvoyer un code d'erreur que personne ne lit. Vu du planificateur, il a
    l'air vivant. C'est la forme la plus dangereuse de panne : celle qui
    ressemble a un fonctionnement.

    C'est exactement le mode de defaillance de la tache `quantbot` qui n'a
    jamais existe pendant des semaines sans que personne ne s'en apercoive.
    Ici l'enjeu est pire : entre-temps, des positions peuvent etre ouvertes.
    """
    etat = etat or {}
    if not etat:
        return {"ok": True, "niveau": "normal", "echecs": 0,
                "message": "Coupe-circuit jamais lance."}

    echecs = int(etat.get("echecs_consecutifs", 0) or 0)
    derniere = etat.get("derniere_erreur") or ""
    succes = etat.get("dernier_succes")

    # -- la tache tourne-t-elle encore ? ---------------------------------
    #
    # Le trou que le compteur d'echecs ne voit pas : une tache dont
    # l'interpreteur a disparu echoue AVANT Python, donc n'ecrit rien. Le
    # compteur reste a zero, et un coupe-circuit qui ne s'execute plus du tout
    # passerait pour operationnel. C'est arrive le 21 septembre 2026 : le
    # lanceur pointait sur un `python.exe` inexistant.
    #
    # Ce que cette mesure NE PEUT PAS savoir : si le marche etait ouvert. On
    # reste donc en "attention" tant que le silence est court, et on n'escalade
    # qu'au-dela de six fois le seuil - un week-end ne doit pas crier.
    derniere_tentative = _horodatage(etat.get("derniere_tentative")
                                     or etat.get("dernier_succes"))
    if derniere_tentative is not None:
        # `coupe_circuit.py` horodate en UTC AVEC fuseau, `robot_etat.json` en
        # heure locale SANS fuseau. Soustraire l'un de l'autre leve
        # `TypeError: can't subtract offset-naive and offset-aware datetimes`,
        # et la veille plante au lieu de surveiller - une surveillance qui
        # tombe en panne sur le format de sa propre entree est pire qu'absente.
        #
        # Les tests ne l'avaient pas vu : leurs horodatages etaient naifs. Ce
        # sont les vraies donnees qui l'ont montre.
        if derniere_tentative.tzinfo is not None:
            reference = maintenant or datetime.now(timezone.utc)
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
        else:
            reference = maintenant or datetime.now()
            if reference.tzinfo is not None:
                reference = reference.replace(tzinfo=None)
        minutes = (reference - derniere_tentative).total_seconds() / 60.0
        if minutes > silence_max_minutes * 6:
            return {"ok": False, "niveau": "alerte", "echecs": echecs,
                    "minutes_silence": round(minutes),
                    "message": "COUPE-CIRCUIT MUET depuis %.0f h. Il ne s'execute "
                               "probablement plus du tout : verifie que la tache "
                               "existe et que son interpreteur Python existe "
                               "encore." % (minutes / 60.0)}
        if minutes > silence_max_minutes:
            return {"ok": False, "niveau": "attention", "echecs": echecs,
                    "minutes_silence": round(minutes),
                    "message": "Coupe-circuit sans signe de vie depuis %.0f min "
                               "(hors seance, c'est normal)." % minutes}

    maintenant = maintenant or datetime.now()
    if echecs == 0:
        return {"ok": True, "niveau": "normal", "echecs": 0,
                "dernier_succes": succes,
                "message": "Coupe-circuit operationnel (dernier contact %s)."
                           % (succes or "?")}

    depuis = etat.get("premiere_erreur") or "?"
    if echecs < echecs_max:
        return {"ok": False, "niveau": "attention", "echecs": echecs,
                "message": "Coupe-circuit en echec depuis %d passage(s) (%s) : %s"
                           % (echecs, depuis, derniere[:120])}

    return {"ok": False, "niveau": "alerte", "echecs": echecs,
            "message": "COUPE-CIRCUIT HORS SERVICE depuis %d passages (%s). "
                       "Aucune limite de perte n'est surveillee. Si des "
                       "positions sont ouvertes, elles ne sont protegees par "
                       "RIEN. Cause : %s" % (echecs, depuis, derniere[:120])}

def bilan(etat_robot: dict, reconciliation: dict, incomplet: dict,
          maintenant=None, etat_coupe_circuit: dict = None) -> dict:
    """Un seul verdict, lisible sans connaitre le detail."""
    sante = sante_robot(etat_robot, maintenant=maintenant)
    alertes, attentions = [], []

    # Le coupe-circuit passe AVANT tout le reste : un desaccord de
    # reconciliation se rattrape, une limite de perte non surveillee non.
    cc = sante_coupe_circuit(etat_coupe_circuit, maintenant=maintenant)
    if cc["niveau"] == "alerte":
        alertes.append(cc["message"])
    elif cc["niveau"] == "attention":
        attentions.append(cc["message"])

    if sante["niveau"] == "alerte":
        alertes.append(sante["message"])
        # Un robot muet a deux causes possibles. Si la tache n'existe pas, le
        # dire tout de suite evite de chercher du cote du code.
        tache = tache_planifiee()
        if tache.get("connu") and tache.get("existe") is False:
            alertes.append(tache["message"])
    elif sante["niveau"] == "attention":
        attentions.append(sante["message"])

    if reconciliation and not reconciliation.get("exploitable", True):
        # Probleme d'OUTIL, pas de compte. Le dire comme tel.
        attentions.append(
            "Reconciliation impossible : %s"
            % reconciliation.get("fiabilite", {}).get("message", "source non fiable"))
    elif reconciliation and not reconciliation.get("conforme", True):
        n = len(reconciliation["anomalies"])
        pct = reconciliation.get("ecart_total_pct") or 0.0
        texte = ("%d ligne(s) en desaccord avec le courtier, %.2f %% du "
                 "portefeuille." % (n, pct * 100))
        (alertes if pct > 0.02 else attentions).append(texte)

    if incomplet and not incomplet.get("ok", True):
        for v in incomplet.get("incidents", []):
            attentions.append("%s : %s" % (v["quand"], v["souci"]))

    if alertes:
        niveau, resume = "alerte", alertes[0]
    elif attentions:
        niveau, resume = "attention", attentions[0]
    else:
        niveau, resume = "normal", "Robot vivant, comptes d'accord."

    return {"niveau": niveau, "resume": resume, "alertes": alertes,
            "attentions": attentions, "sante": sante, "coupe_circuit": cc}


def lire_etat(chemin) -> dict:
    chemin = Path(chemin)
    if not chemin.exists():
        return {}
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
