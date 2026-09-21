"""Couche "operations" : etat du compte, plan d'ordres, envoi, executions.

Le tableau de bord et `scripts/trade.py` s'appuient tous deux sur les MEMES
trois briques de decision - `live.portefeuille_cible`, `orders.seuil_minimal`
et `orders.planifier`. Ils ne peuvent donc pas diverger sur ce qu'il faut
acheter : seule la presentation differe.

Une regle absolue ici : ce module ne travaille QUE sur le compte de
simulation. Passer des ordres d'argent reel depuis un bouton de navigateur
serait un mauvais compromis - un clic n'a pas le poids d'une phrase tapee a la
main. Le chemin reel reste `scripts/trade.py --reel`, avec ses trois verrous
et sa confirmation au clavier.
"""
from __future__ import annotations

import csv
import hashlib
import time
from datetime import datetime
from pathlib import Path

from . import broker, defi, executions as exe, live, orders as ord_mod

JOURNAL = Path("data/journal_ordres.csv")
ETAT_ROBOT = Path("data/robot_etat.json")

COLONNES = ["horodatage", "compte", "mode", "ticker", "sens", "quantite", "montant",
            "cours", "date_cours", "cours_marche", "fourchette_bps", "motif",
            "statut", "id_courtier"]


def _instant(valeur):
    """Parse un horodatage ISO du courtier, fuseau compris."""
    if not valeur:
        return None
    texte = str(valeur).strip().replace("Z", "+00:00")
    # Alpaca peut renvoyer des nanosecondes ; `fromisoformat` (3.8) plafonne
    # a six chiffres de fraction.
    if "." in texte:
        tete, _, reste = texte.partition(".")
        chiffres = ""
        for c in reste:
            if c.isdigit() and len(chiffres) < 6:
                chiffres += c
            elif not c.isdigit():
                reste = reste[reste.index(c):]
                break
        else:
            reste = ""
        texte = "%s.%s%s" % (tete, chiffres or "0", reste)
    try:
        return datetime.fromisoformat(texte)
    except ValueError:
        return None


def _duree_lisible(delta) -> str:
    secondes = int(abs(delta.total_seconds()))
    heures, minutes = secondes // 3600, (secondes % 3600) // 60
    if heures >= 24:
        return "%d j %d h" % (heures // 24, heures % 24)
    if heures:
        return "%d h %02d" % (heures, minutes)
    return "%d min" % max(minutes, 1)


def horloge_lisible(horloge) -> str:
    """L'etat du marche, en HEURE LOCALE et avec un delai relatif.

    Le courtier donne ses horaires en heure de New York
    ('2026-09-14T09:30:00-04:00'). L'affichage precedent tronquait la chaine a
    seize caracteres, ce qui supprimait exactement le decalage horaire : on
    lisait '09:30' comme une heure locale alors qu'il etait 15:30 a Paris.
    Devant cet ecran a midi, la seule conclusion raisonnable etait que le
    tableau de bord se trompait - alors que le controle, lui, avait raison.

    Le delai relatif ("dans 3 h 12") est la partie qui ne peut pas etre mal
    lue : elle ne depend d'aucun fuseau.
    """
    ouvert = bool(horloge.get("is_open"))
    maintenant = _instant(horloge.get("timestamp")) or datetime.now().astimezone()
    if maintenant.tzinfo is None:
        maintenant = maintenant.astimezone()

    cle = "next_close" if ouvert else "next_open"
    cible = _instant(horloge.get(cle))
    if cible is None:
        return "ouvert" if ouvert else "ferme"

    locale = cible.astimezone()
    # `%a` rend un nom de jour anglais selon la locale du systeme : on garde
    # une forme numerique, lisible partout et sans dependance.
    if locale.date() == maintenant.astimezone().date():
        quand = locale.strftime("a %H:%M")
    else:
        quand = locale.strftime("le %d/%m a %H:%M")
    delai = _duree_lisible(cible - maintenant)
    fuseau = locale.tzname() or "heure locale"
    verbe = "fermeture" if ouvert else "ouverture"
    return "%s, %s %s %s (dans %s)" % ("ouvert" if ouvert else "ferme",
                                       verbe, quand, fuseau, delai)


def entete(chemin) -> list:
    """Noms de colonnes deja presents dans le fichier. [] s'il n'existe pas."""
    chemin = Path(chemin)
    if not chemin.exists() or chemin.stat().st_size == 0:
        return []
    with chemin.open("r", newline="", encoding="utf-8") as fh:
        for ligne in csv.reader(fh):
            return [c.strip() for c in ligne]
    return []


def migrer_journal(chemin, colonnes=None) -> dict:
    """Reecrit un journal dont l'entete ne correspond plus aux colonnes.

    Le bug repare ici
    -----------------
    `journaliser` n'ecrivait l'entete QUE si le fichier n'existait pas. Le jour
    ou la liste des colonnes est passee de 10 a 14, l'ancien entete est reste
    en place et les nouvelles lignes se sont empilees derriere avec 14 champs.
    Resultat : un CSV que `pandas` refuse d'ouvrir - ParserError des la
    premiere ligne au nouveau format - donc plus aucune analyse d'execution
    possible, sans le moindre message d'erreur au moment de l'ecriture.

    La reparation lit chaque ligne selon SA propre largeur : les anciennes
    lignes sont relues avec l'ancien entete (tous ses noms existent encore
    dans le format courant), les nouvelles avec le format courant. Rien n'est
    perdu, les champs absents restent vides. Une copie de sauvegarde est
    ecrite a cote avant toute reecriture.
    """
    colonnes = list(colonnes or COLONNES)
    chemin = Path(chemin)
    ancienne = entete(chemin)
    rapport = {"chemin": str(chemin), "migre": False, "lignes": 0,
               "anciennes": 0, "courantes": 0, "illisibles": 0,
               "entete_avant": ancienne, "entete_apres": colonnes,
               "sauvegarde": None}
    if not ancienne or ancienne == colonnes:
        return rapport

    with chemin.open("r", newline="", encoding="utf-8") as fh:
        brut = list(csv.reader(fh))[1:]

    lignes = []
    for row in brut:
        if not row or not any(c.strip() for c in row):
            continue
        if len(row) == len(colonnes):
            source = colonnes
            rapport["courantes"] += 1
        elif len(row) == len(ancienne):
            source = ancienne
            rapport["anciennes"] += 1
        else:
            # Ni l'un ni l'autre : on ne devine pas. La ligne est conservee
            # telle quelle dans la sauvegarde, jamais silencieusement jetee.
            rapport["illisibles"] += 1
            continue
        lignes.append(dict(zip(source, row)))

    sauvegarde = chemin.with_suffix(chemin.suffix + ".avant-migration")
    sauvegarde.write_bytes(chemin.read_bytes())
    with chemin.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=colonnes)
        w.writeheader()
        for ligne in lignes:
            w.writerow({c: ligne.get(c, "") for c in colonnes})

    rapport.update(migre=True, lignes=len(lignes), sauvegarde=str(sauvegarde))
    return rapport


def journaliser(lignes, chemin=None) -> None:
    # Le chemin est resolu A L'APPEL, pas a la definition : une valeur par
    # defaut figee rendrait le module intestable et, pire, ferait ecrire un
    # essai dans le journal de production. C'est exactement ce qui est arrive.
    chemin = Path(chemin) if chemin else JOURNAL
    chemin.parent.mkdir(parents=True, exist_ok=True)

    # Avant d'ajouter quoi que ce soit : l'entete en place decrit-il encore ce
    # qu'on s'apprete a ecrire ? Sans ce controle, toute evolution des colonnes
    # corrompt le journal en silence (c'est arrive).
    existante = entete(chemin)
    if existante and existante != COLONNES:
        migrer_journal(chemin)

    nouveau = not chemin.exists() or chemin.stat().st_size == 0
    with chemin.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLONNES)
        if nouveau:
            w.writeheader()
        for ligne in lignes:
            w.writerow({c: ligne.get(c, "") for c in COLONNES})


class Operations:
    """Etat et actions du compte de simulation. Aucun acces a l'argent reel."""

    def __init__(self, cfg, charger_prix):
        self.cfg = cfg
        self._charger_prix = charger_prix
        self._api = None

    # -- connexion ---------------------------------------------------------
    @property
    def api(self):
        if self._api is None:
            self._api = broker.connecter(self.cfg, reel=False)
        return self._api

    # -- battement bon marche ---------------------------------------------
    def pouls(self, journal=None, etat_robot=None) -> dict:
        """Empreinte de ce qui peut changer, SANS rien recalculer.

        Pourquoi cette methode existe separement de `etat()`
        ----------------------------------------------------
        `etat()` recalcule le score composite sur tout l'univers - une seconde
        ou deux sur 500 titres - et partage le verrou du moteur avec les
        backtests. L'appeler en boucle pour surveiller le compte gelerait la
        vue analyse a chaque passage, ce qui est exactement le contraire de
        l'effet recherche.

        `pouls()` ne fait que LIRE : trois appels au courtier et deux
        `stat()` sur des fichiers. Aucun calcul, aucun verrou. L'interface
        s'en sert pour savoir s'il s'est passe quelque chose, et ne demande
        l'etat complet que dans ce cas.

        L'empreinte couvre tout ce qui doit reveiller l'ecran : la valeur du
        compte, le detail des positions (un ordre execute la modifie), les
        ordres encore en vol, la taille du journal (le robot y ecrit) et
        l'etat du robot lui-meme.
        """
        journal = Path(journal) if journal else JOURNAL
        etat_robot = Path(etat_robot) if etat_robot else ETAT_ROBOT

        morceaux, out = [], {"ok": True, "horodatage": time.time()}
        try:
            compte = self.api.compte()
            equity = float(compte.get("equity", 0.0))
            positions = self.api.positions()
            try:
                en_vol = self.api.ordres_ouverts()
            except Exception:
                en_vol = []
            # L'ouverture du marche entre dans l'empreinte : sans elle, la
            # bascule ouvert/ferme ne rafraichit l'ecran que si un cours a
            # bouge entre-temps, et le controle reste faux le temps d'un
            # battement.
            try:
                ouvert = bool(self.api.horloge().get("is_open"))
            except Exception:
                ouvert = None
            out.update({
                "equity": equity,
                "liquidites": float(compte.get("cash", 0.0)),
                "numero": compte.get("account_number"),
                "n_positions": len(positions),
                "n_en_vol": len(en_vol),
                "marche_ouvert": ouvert,
            })
            morceaux.append("m=%s" % ouvert)
            # Les quantites sont arrondies avant d'entrer dans l'empreinte :
            # sans cela, la derniere decimale d'une position fractionnaire
            # suffirait a declarer un changement a chaque passage.
            morceaux.append("e=%.2f" % equity)
            morceaux.append("p=" + ";".join(
                "%s:%.6f" % (t, float(q)) for t, q in sorted(positions.items())))
            morceaux.append("v=" + ";".join(
                sorted(str(o.get("id", "")) for o in en_vol)))
        except Exception as exc:
            out.update({"ok": False, "erreur": str(exc)})
            morceaux.append("hors-ligne:%s" % exc)

        for chemin, cle in ((journal, "j"), (etat_robot, "r")):
            try:
                st = chemin.stat()
                morceaux.append("%s=%d/%d" % (cle, st.st_size, int(st.st_mtime)))
            except OSError:
                morceaux.append("%s=0" % cle)

        out["empreinte"] = hashlib.sha1(
            "|".join(morceaux).encode("utf-8")).hexdigest()[:16]
        return out

    def disponible(self) -> dict:
        try:
            compte = self.api.compte()
            return {"ok": True, "compte": compte}
        except Exception as exc:
            return {"ok": False, "erreur": str(exc)}

    # -- le compte est-il vide ? ------------------------------------------
    def compte_vide(self, equity=None, detail=None) -> bool:
        """Moins de 1 % de la valeur du compte est investi.

        Cette methode existe parce que `robot.py` l'appelait avant qu'elle
        n'existe : `compte_vide` etait une VARIABLE LOCALE de `etat()`, et
        `ops.compte_vide()` levait donc un `AttributeError` qui arretait le
        robot au tout debut de sa passe - apres la mise a jour des cours,
        avant toute decision. Le planificateur relancait, le traceback
        repartait dans `data/robot_sortie.log`, et rien n'etait envoye.

        Le seuil de 1 % n'est pas zero a dessein : une fraction de titre
        oubliee a 3 dollars sur un compte de 100 000 ne fait pas d'un compte
        vide un compte investi.

        `equity` et `detail` sont acceptes pour que `etat()`, qui les a deja
        obtenus, ne repose pas deux fois les memes questions au courtier.
        """
        if detail is None:
            detail = self.api.positions_detail()
        if equity is None:
            equity = float(self.api.compte().get("equity", 0.0) or 0.0)
        valeur = sum(abs(float(p.get("market_value", 0.0) or 0.0)) for p in detail)
        return valeur <= 0.01 * max(float(equity), 1e-9)

    # -- etat complet ------------------------------------------------------
    def etat(self, signal=None) -> dict:
        """`signal` impose la seance de decision.

        Sans lui, `envoyer()` recalculerait le portefeuille cible sur la
        seance courante alors que le robot a decide sur une fin de mois : la
        decision envoyee ne serait pas celle qui a ete validee. Le parametre
        traverse donc toute la chaine plutot que d'etre recalcule a chaque
        etage.
        """
        dispo = self.disponible()
        if not dispo["ok"]:
            return {"connecte": False, "erreur": dispo["erreur"]}

        compte = dispo["compte"]
        equity = float(compte.get("equity", 0.0))
        liquidites = float(compte.get("cash", 0.0))
        horloge = self.api.horloge()
        positions = self.api.positions()
        detail = self.api.positions_detail()
        try:
            en_vol = self.api.ordres_ouverts()
        except Exception:
            en_vol = []

        prices = self._charger_prix()

        # -- garde-fous du defi (inactifs par defaut) -----------------------
        #
        # Evalues ICI, avant l'amorcage, et pas plus bas comme avant : leur
        # verdict est une ENTREE de la decision d'amorcage. Un compte qui vient
        # d'etre solde sur verrou est un compte vide, donc un candidat parfait a
        # l'amorcage - qui rachetait tout le portefeuille le lendemain matin,
        # dans le marche meme qui venait de declencher la limite. Le verrou doit
        # etre connu avant qu'on se demande si le compte est "neuf".
        garde_defi, verdict_defi = None, None
        params_defi = defi.parametres(self.cfg)
        if params_defi is not None:
            verdict_defi = defi.evaluer(
                equity, float(compte.get("last_equity", 0.0) or 0.0),
                params_defi, defi.lire_etat())
            defi.ecrire_etat(verdict_defi["etat"])
            garde_defi = {"nom": "Limites du defi", "ok": verdict_defi["ok"],
                          "detail": verdict_defi["raison"]
                                    or defi.resume(verdict_defi, params_defi)}

        # -- AMORCAGE : un compte neuf ne doit pas attendre le prochain signal
        #
        # Sans cela, un compte vide reste 100 % en liquidites jusqu'au
        # prochain rebalancement - une semaine en hebdomadaire, jusqu'a un
        # mois en mensuel. Ce n'est pas la strategie qui se protege, c'est une
        # machine qui n'a jamais demarre, et les statistiques mesurees sur
        # cette periode ne mesurent rien du tout.
        #
        # Ce n'est PAS un rebalancement force : on entre sur la cible du
        # dernier signal ECHU, c'est-a-dire exactement le portefeuille que le
        # backtest detiendrait aujourd'hui. Le backtest aussi entre a une date
        # arbitraire - celle ou son historique commence. La CADENCE n'est pas
        # touchee : le prochain rebalancement reste a sa date.
        #
        # Deux conditions, et les deux comptent. Le compte doit etre reellement
        # vide (moins de 1 % investi), et la cible doit contenir quelque chose :
        # si le filtre de regime dit "liquidites", un compte vide est le
        # portefeuille CORRECT, pas une machine en panne.
        # Et la troisieme, ajoutee apres coup : aucun VERROU de defi ne doit
        # etre pose. Un compte vide sous verrou n'est pas une machine qui n'a
        # jamais demarre, c'est une machine qu'on vient d'arreter.
        vide = self.compte_vide(equity=equity, detail=detail)
        verrouille = bool(verdict_defi and verdict_defi.get("verrou"))
        amorcage = False
        if (vide and signal is None and not verrouille
                and self.cfg.get("execution.amorcage", True)):
            try:
                close = live._prepare_prices(prices, self.cfg)[0]
                echus = live.signaux_echus(
                    close.index, self.cfg.get("execution.rebalance", "monthly"))
                if len(echus):
                    signal = echus[-1]
                    amorcage = True
            except Exception:
                amorcage = False

        cible = live.portefeuille_cible(prices, self.cfg, forcer_signal=signal)
        cours = {t: float(v) for t, v in cible.cours.items()}
        cibles = {t: float(p) for t, p in cible.poids.items()}

        seuil = ord_mod.seuil_minimal(self.cfg, equity)
        max_pct = float(self.cfg.get("broker.max_order_pct", 0.15))
        liste = ord_mod.planifier(
            cibles, positions, cours, equity,
            seuil_notional=seuil, max_notional=max_pct * equity,
            fractionnaire=bool(self.cfg.get("broker.fractionnaire", True)))
        resume = ord_mod.resume(liste)
        residus = ord_mod.poussieres(cibles, positions, cours)

        # Le plan valorise les positions au dernier cours EN CACHE (c'est ce
        # que modelise le backtest), le courtier les valorise au marche. Un
        # ecart important entre les deux signale un cache perime : les poids
        # affiches ne veulent alors plus dire la meme chose d'un tableau a
        # l'autre, et le dimensionnement des ordres est fausse.
        valeur_cache = sum(float(positions.get(p["symbol"], 0.0)) * cours.get(p["symbol"], 0.0)
                           for p in detail)
        valeur_marche = sum(float(p["market_value"]) for p in detail)
        ecart_valo = (abs(valeur_cache - valeur_marche) / valeur_marche) if valeur_marche else 0.0

        # -- controles, dans l'ordre ou trade.py les applique ---------------
        plafond = float(self.cfg.get("broker.max_echange_par_seance", 2.0))
        besoin_marge = resume["achats"] - resume["ventes"] - liquidites
        controles = [
            {"nom": "Donnees a jour",
             # En seances, pas en jours : 0 signifie "rien ne manque". Le
             # seuil de 5 JOURS tolerait une semaine entiere de retard ; en
             # seances, tolerer plus d'une cloture manquante n'a pas de sens
             # pour un bot qui decide a la cloture.
             "ok": cible.anciennete_donnees <= 1,
             "detail": ("derniere cloture %s, a jour" % cible.as_of.date())
                       if cible.anciennete_donnees == 0 else
                       ("derniere cloture %s, %d seance(s) manquante(s)"
                        % (cible.as_of.date(), cible.anciennete_donnees))},
            # En amorcage, `est_jour_execution` est artificiellement vrai : on
            # a IMPOSE le signal. Le dire autrement serait mentir sur ce qui se
            # passe. Les deux branches sont donc separees - une premiere version
            # affichait "oui, signal du ..." en s'appuyant sur un drapeau qu'elle
            # venait elle-meme de forcer.
            {"nom": "Jour de rebalancement",
             "ok": (bool(amorcage and len(cible.poids)) if amorcage
                    else bool(cible.est_jour_execution)),
             "detail": ("amorcage : compte vide, entree initiale sur le signal "
                        "du %s" % cible.date_signal.date()) if amorcage and len(cible.poids)
                       else ("amorcage impossible : la cible est vide "
                             "(filtre de regime), un compte en liquidites est correct")
                       if amorcage
                       else ("oui, signal du %s" % cible.date_signal.date())
                       if cible.est_jour_execution else
                       "non : %s, prochain signal a la derniere seance de la periode"
                       % self.cfg.get("execution.rebalance")},
            {"nom": "Marche ouvert",
             "ok": bool(horloge.get("is_open")),
             "detail": horloge_lisible(horloge)},
            {"nom": "Echange sous le plafond",
             "ok": resume["echange"] <= plafond * equity,
             "detail": "%.0f %% du compte, plafond %.0f %%"
                       % (100 * resume["echange"] / max(equity, 1e-9), 100 * plafond)},
            {"nom": "Compte sans decouvert",
             "ok": liquidites >= -0.01,
             "detail": "liquidites %.2f" % liquidites},
            {"nom": "Aucun achat a credit",
             "ok": besoin_marge <= 0.01,
             "detail": "liquidites suffisantes" if besoin_marge <= 0.01
                       else "il manque %.2f" % besoin_marge},
            {"nom": "Valorisation coherente",
             "ok": ecart_valo <= 0.02 or not detail,
             "detail": "cache %.0f contre marche %.0f, ecart %.1f %%"
                       % (valeur_cache, valeur_marche, 100 * ecart_valo)
                       if detail else "aucune position a valoriser"},
            # Garde-fous du defi. Places AVANT le controle d'ordres en vol
            # parce qu'ils priment : si une limite est franchie, la question
            # n'est plus de savoir si l'envoi est propre, mais s'il doit avoir
            # lieu. Volontairement absents de `contournables` - aucune urgence
            # ne justifie de passer outre une limite de perte.
            # Un ordre encore en vol immobilise les titres chez le courtier
            # (`held_for_orders`), alors que `positions()` les compte toujours
            # comme detenus. Le bot recalcule donc une cible sur des titres
            # qu'il ne peut pas vendre - Alpaca a refuse un ordre pour cette
            # raison exacte le 2026-09-09. Et dans l'autre sens c'est pire :
            # deux achats successifs sur la meme ligne peuvent tous les deux
            # s'executer et doubler l'exposition.
            {"nom": "Aucun ordre en attente",
             "ok": not en_vol,
             "detail": "aucun ordre en vol" if not en_vol
                       else "%d ordre(s) encore en attente chez le courtier "
                            "(%s) - annule-les avant d'en envoyer d'autres"
                            % (len(en_vol),
                               ", ".join(sorted({str(o.get("symbol", "?"))
                                                 for o in en_vol})[:6]))},
            {"nom": "Compte non bloque",
             "ok": not (compte.get("trading_blocked") or compte.get("account_blocked")),
             "detail": compte.get("status", "?")},
        ]
        # Les garde-fous du defi ferment la liste : si une limite de perte est
        # franchie, la question n'est plus de savoir si l'envoi est propre,
        # mais s'il doit avoir lieu du tout.
        if garde_defi is not None:
            controles.append(garde_defi)

        return {
            "connecte": True,
            "compte": {
                "numero": compte.get("account_number"), "statut": compte.get("status"),
                "devise": compte.get("currency", "USD"), "equity": equity,
                "liquidites": liquidites,
                "pouvoir_achat": float(compte.get("buying_power", 0.0)),
            },
            "valorisation": {"cache": valeur_cache, "marche": valeur_marche,
                             "ecart": ecart_valo},
            "positions": sorted(
                ({"ticker": p["symbol"], "titres": float(p["qty"]),
                  "valeur": float(p["market_value"]),
                  "latent": float(p["unrealized_pl"]),
                  "poids": float(p["market_value"]) / equity if equity else 0.0}
                 for p in detail), key=lambda x: -x["valeur"]),
            "marche_ouvert": bool(horloge.get("is_open")),
            "prochaine_ouverture": horloge.get("next_open"),
            "donnees": {"derniere_cloture": str(cible.as_of.date()),
                        "anciennete": cible.anciennete_donnees},
            "regime": {"texte": cible.regime_texte, "actif": cible.regime_actif},
            # Expose pour que le tableau de bord et les tests puissent
            # distinguer une entree initiale d'un rebalancement ordinaire.
            "amorcage": bool(amorcage),
            "date_decision": str(cible.date_decision.date()) if cible.date_decision is not None else None,
            "cible": [{"ticker": t, "poids": float(p),
                       "cours": cours.get(t), "detenu": float(positions.get(t, 0.0))}
                      for t, p in cible.poids.items()],
            "part_liquidites": cible.part_liquidites,
            "ordres": [o.as_dict() for o in liste],
            "residus": residus,
            "resume": resume,
            "seuil": seuil,
            "controles": controles,
            # Le verdict brut du defi, pour que l'appelant sache non seulement
            # qu'il est bloque mais POURQUOI, et s'il reste des positions a
            # solder. Absent quand le mode defi est inactif.
            "defi": verdict_defi,
            "pret": all(c["ok"] for c in controles if c["nom"] != "Jour de rebalancement"),
        }

    # -- actions -----------------------------------------------------------
    def envoyer(self, forcer: bool = False, signal=None) -> dict:
        etat = self.etat(signal=signal)
        if not etat.get("connecte"):
            return {"ok": False, "message": etat.get("erreur", "courtier injoignable")}
        # `forcer` ne leve que les contraintes de CALENDRIER. Les donnees
        # perimees, le credit, le plafond et la coherence des valorisations
        # restent bloquants : ce sont des conditions de justesse, pas de
        # confort.
        # Un decouvert deja constitue ne doit pas bloquer les VENTES qui le
        # resorbent : c'est le seul moyen d'en sortir.
        # "Aucun ordre en attente" n'y figure volontairement PAS : envoyer par
        # dessus des ordres en vol peut doubler une position, ce qu'aucune
        # urgence ne justifie. Le remede est d'annuler, pas de forcer.
        contournables = {"Jour de rebalancement", "Marche ouvert", "Compte sans decouvert"}
        bloquants = [c for c in etat["controles"]
                     if not c["ok"] and not (forcer and c["nom"] in contournables)]
        if bloquants:
            return {"ok": False, "message": "Controles non satisfaits : "
                    + ", ".join(c["nom"] for c in bloquants), "controles": bloquants}
        if not etat["ordres"]:
            return {"ok": True, "message": "Portefeuille deja aligne, aucun ordre.", "envoyes": 0}

        tickers = sorted({o["ticker"] for o in etat["ordres"]})
        try:
            cotations = self.api.cotations(tickers)
        except Exception:
            cotations = {}

        horodatage = datetime.now().isoformat(timespec="seconds")
        numero = str(etat["compte"]["numero"])
        lignes, envoyes, echecs = [], 0, []
        for o in etat["ordres"]:
            ligne = {"horodatage": horodatage, "compte": numero, "mode": "simulation",
                     "ticker": o["ticker"], "sens": o["sens"], "quantite": o["quantite"],
                     "montant": o["montant"], "cours": o["cours"],
                     "date_cours": etat.get("date_decision")
                                   or etat["donnees"]["derniere_cloture"],
                     "motif": o["motif"]}
            cot = cotations.get(o["ticker"])
            if cot:
                ligne["cours_marche"] = round(cot[2], 4)
                ligne["fourchette_bps"] = round(10_000 * (cot[1] - cot[0]) / cot[2], 1)
            try:
                rep = self.api.envoyer_ordre(
                    o["ticker"], o["sens"], quantite=o["quantite"],
                    montant=None if o["quantite"] is not None else o["montant"],
                    client_order_id="quantbot-%s-%s-%d"
                                    % (self.cfg.get("name"), o["ticker"], int(time.time() * 1000) % 10**9))
                ligne["statut"] = rep.get("status", "envoye")
                ligne["id_courtier"] = rep.get("id", "")
                envoyes += 1
            except Exception as exc:
                ligne["statut"] = "ECHEC"
                ligne["id_courtier"] = str(exc)[:180]
                echecs.append({"ticker": o["ticker"], "raison": str(exc)[:120]})
            lignes.append(ligne)
            time.sleep(0.1)

        journaliser(lignes)
        return {"ok": not echecs, "envoyes": envoyes, "echecs": echecs,
                "fourchettes": len(cotations),
                "message": "%d ordre(s) envoye(s) en simulation, %d echec(s)."
                           % (envoyes, len(echecs))}

    def annuler(self) -> dict:
        try:
            n = len(self.api.ordres_ouverts())
            self.api.annuler_ordres()
            return {"ok": True, "message": "%d ordre(s) en attente annule(s)." % n}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    # -- sortie d'urgence --------------------------------------------------
    def solder(self, motif: str = "verrou du defi") -> dict:
        """Annule les ordres en vol puis SOLDE toutes les positions, au marche.

        Le seul chemin du projet qui vende sans consulter la strategie, et la
        seule chose qui compte ici est la vitesse : on ne cherche ni le bon
        cours, ni le bon ordonnancement, ni a economiser des frais. Entre -8 %
        (le garde-fou) et -10 % (le contrat), il reste deux points ; les
        depenser en finesse d'execution serait absurde.

        Annuler AVANT de vendre n'est pas cosmetique. Un ordre d'achat encore
        en vol peut s'executer pendant la liquidation et rouvrir une ligne
        qu'on vient de fermer ; et un ordre de vente en vol immobilise les
        titres (`held_for_orders`), ce qui ferait refuser la vente de solde.

        `quantite` et non `montant` : un ordre en montant laisse derriere lui
        une poussiere de position, et une poussiere n'est pas une sortie.

        Chaque ordre est journalise avec le motif "solde (verrou)", pour que la
        reconciliation d'executions ne prenne pas une liquidation d'urgence
        pour un rebalancement rate.
        """
        rapport = {"ok": True, "annules": 0, "soldes": 0, "echecs": [],
                   "ignores": [], "message": ""}
        try:
            en_vol = self.api.ordres_ouverts()
        except Exception:
            en_vol = []
        if en_vol:
            try:
                self.api.annuler_ordres()
                rapport["annules"] = len(en_vol)
            except Exception as exc:
                rapport["ok"] = False
                rapport["echecs"].append({"ticker": "-", "raison": "annulation : %s"
                                                                  % str(exc)[:120]})

        try:
            detail = self.api.positions_detail()
            numero = str(self.api.compte().get("account_number", ""))
        except Exception as exc:
            rapport.update({"ok": False, "message": "courtier injoignable : %s"
                                                    % str(exc)[:160]})
            return rapport

        horodatage = datetime.now().isoformat(timespec="seconds")
        lignes = []
        for p in detail:
            ticker = p.get("symbol")
            titres = float(p.get("qty", 0.0) or 0.0)
            # Une position courte se solde en ACHETANT. La strategie n'en prend
            # pas, mais une liquidation d'urgence qui laisserait une ligne
            # ouverte parce qu'elle est du mauvais signe ne serait pas une
            # liquidation.
            sens = ord_mod.VENTE if titres > 0 else ord_mod.ACHAT
            quantite = round(abs(titres), 6)
            if quantite <= 0:
                rapport["ignores"].append({"ticker": ticker, "titres": titres,
                                           "raison": "poussiere inferieure a 1e-6"})
                continue
            ligne = {"horodatage": horodatage, "compte": numero, "mode": "simulation",
                     "ticker": ticker, "sens": sens, "quantite": quantite,
                     "montant": round(abs(float(p.get("market_value", 0.0) or 0.0)), 2),
                     "cours": "", "date_cours": "",
                     "motif": "solde (%s)" % motif}
            try:
                rep = self.api.envoyer_ordre(ticker, sens, quantite=quantite)
                ligne["statut"] = rep.get("status", "envoye")
                ligne["id_courtier"] = rep.get("id", "")
                rapport["soldes"] += 1
            except Exception as exc:
                ligne["statut"] = "ECHEC"
                ligne["id_courtier"] = str(exc)[:180]
                rapport["ok"] = False
                rapport["echecs"].append({"ticker": ticker, "raison": str(exc)[:120]})
            lignes.append(ligne)
            time.sleep(0.1)

        if lignes:
            journaliser(lignes)
        rapport["message"] = ("%d position(s) soldee(s), %d ordre(s) annule(s), "
                              "%d echec(s)." % (rapport["soldes"], rapport["annules"],
                                                len(rapport["echecs"])))
        return rapport

    def appliquer_defi(self, verdict=None, marche_ouvert=None) -> dict:
        """Traduit un verrou de defi en actes. Le seul endroit qui le fasse.

        `defi.evaluer` constate, cette methode agit. Separer les deux permet de
        tester tout le raisonnement sans courtier, et de n'avoir qu'un seul
        endroit ou du code vend.

        Le marche ferme n'annule pas la consigne, il la reporte : `a_solder`
        reste vrai sur disque et la liquidation partira au prochain passage en
        seance. Un ordre au marche envoye hors seance serait refuse par le
        courtier, ou - pire - execute a l'ouverture a un cours qu'on n'a pas
        vu. Entre les deux, garder la consigne et attendre l'ouverture est le
        seul comportement qui ne mente pas sur ce qui s'est passe.

        Renvoie ce qui a ete fait, pour que l'appelant l'ecrive dans son
        journal. Idempotent : une fois la liquidation faite, `a_solder` tombe
        et les passages suivants ne font plus rien.
        """
        params = defi.parametres(self.cfg)
        if params is None:
            return {"actif": False, "verrou": None, "solde": None,
                    "message": "mode defi inactif"}

        if verdict is None:
            dispo = self.disponible()
            if not dispo["ok"]:
                return {"actif": True, "verrou": None, "solde": None,
                        "erreur": dispo["erreur"],
                        "message": "courtier injoignable : %s" % dispo["erreur"][:120]}
            compte = dispo["compte"]
            verdict = defi.evaluer(
                float(compte.get("equity", 0.0) or 0.0),
                float(compte.get("last_equity", 0.0) or 0.0),
                params, defi.lire_etat())

        # L'etat du verdict est ecrit ICI, avant d'agir, et c'est cette methode
        # qui s'en charge - jamais l'appelant.
        #
        # Le contraire etait un piege silencieux : `marquer_solde()` relit le
        # DISQUE pour eteindre la consigne de liquidation. Un appelant qui
        # ecrivait son verdict APRES coup - ce que faisait `veille_defi.py` -
        # remettait donc `a_solder` a vrai par dessus, et la liquidation
        # repartait a chaque passage, soit toutes les trente minutes pendant
        # toute la seance. Poser l'invariant "le disque est a jour avant d'agir"
        # ici plutot que dans chaque appelant est la seule version qui tient.
        defi.ecrire_etat(verdict["etat"])

        out = {"actif": True, "verrou": verdict.get("verrou"),
               "nouveau_verrou": bool(verdict.get("nouveau_verrou")),
               "solde": None, "resume": defi.resume(verdict, params),
               "message": ""}

        if not verdict.get("a_solder"):
            out["message"] = (verdict["raison"] if verdict.get("verrou")
                              else "limites respectees")
            return out

        if marche_ouvert is None:
            try:
                marche_ouvert = bool(self.api.horloge().get("is_open"))
            except Exception:
                marche_ouvert = False
        if not marche_ouvert:
            out["message"] = ("%s -- marche ferme : la liquidation partira a la "
                              "prochaine seance." % verdict["raison"])
            return out

        out["solde"] = self.solder(motif=verdict["verrou"]["raison"])
        if out["solde"]["ok"]:
            defi.ecrire_etat(defi.marquer_solde(defi.lire_etat()))
        out["message"] = "%s -- %s" % (verdict["raison"], out["solde"]["message"])
        return out

    def executions(self, depuis=None) -> dict:
        if not JOURNAL.exists():
            return {"n": 0, "verdict": "Aucun ordre au journal.", "niveau": "flat",
                    "releves": [], "sans_suite": []}
        with JOURNAL.open(encoding="utf-8") as fh:
            lignes = list(csv.DictReader(fh))
        numero = str(self.api.compte().get("account_number", ""))
        lignes = [l for l in lignes if not l.get("compte") or l["compte"] == numero]
        if depuis:
            lignes = [l for l in lignes if (l.get("horodatage") or "") >= depuis]
        return exe.analyser(self.cfg, self.api, lignes)

    # -- rafraichissement des cours ---------------------------------------
    def rafraichir(self, journal=None) -> dict:
        """Retelecharge les cours. Long : a lancer dans un fil separe.

        Le compte rendu porte sur ce qui a ete AJOUTE, pas sur ce qui est
        disponible. L'ancienne version annoncait "504 series, 0 manquant(s)"
        alors qu'elle n'avait rien telecharge du tout : le chiffre etait
        exact, rassurant, et sans rapport avec la question posee.
        """
        from . import datafeed
        from .universe import get_universe
        dire = journal or (lambda m: None)
        dire("Constitution de l'univers...")
        tickers = list(get_universe(self.cfg))
        benchmark = self.cfg.get("universe.benchmark")
        if benchmark:
            tickers.append(benchmark)
        dire("%d tickers, telechargement des seances manquantes..." % len(tickers))

        rapport = {}
        prices = datafeed.fetch(self.cfg, tickers, rapport=rapport)
        manquants = sorted(set(tickers) - set(prices))
        apres = rapport.get("derniere_seance_apres")
        retard = rapport.get("retard_seances")

        if rapport.get("a_completer", 0) == 0:
            dire("cache deja a jour (derniere seance %s)"
                 % (apres.date() if apres is not None else "?"))
        elif rapport.get("seances_ajoutees", 0) == 0:
            # Des seances etaient attendues et rien n'est arrive : reseau
            # coupe, fournisseur en panne, tickers refuses. Le dire.
            dire("ECHEC : %d serie(s) attendaient des seances, AUCUNE ligne "
                 "ajoutee. Cours toujours arretes au %s."
                 % (rapport["a_completer"],
                    apres.date() if apres is not None else "?"))
        else:
            dire("termine : %d seance(s) ajoutee(s) sur %d serie(s), "
                 "derniere cloture %s"
                 % (rapport["seances_ajoutees"], rapport.get("telecharges", 0),
                    apres.date() if apres is not None else "?"))
        if manquants:
            dire("%d ticker(s) sans aucune donnee : %s"
                 % (len(manquants), ", ".join(manquants[:10])))
        if retard:
            dire("ATTENTION : il manque encore %d seance(s)." % retard)

        return {"ok": bool(rapport.get("ok", True)), "series": len(prices),
                "manquants": manquants[:20],
                "seances_ajoutees": rapport.get("seances_ajoutees", 0),
                "a_completer": rapport.get("a_completer", 0),
                "retard_seances": retard,
                "derniere_seance": str(apres.date()) if apres is not None else None}
