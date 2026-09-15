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

        # -- garde-fous du defi (inactifs par defaut) -----------------------
        garde_defi = None
        params_defi = defi.parametres(self.cfg)
        if params_defi is not None:
            v = defi.evaluer(equity, float(compte.get("last_equity", 0.0) or 0.0),
                             params_defi, defi.lire_etat())
            defi.ecrire_etat(v["etat"])
            garde_defi = {"nom": "Limites du defi", "ok": v["ok"],
                          "detail": v["raison"] or defi.resume(v, params_defi)}

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
            {"nom": "Jour de rebalancement",
             "ok": bool(cible.est_jour_execution),
             "detail": ("oui, signal du %s" % cible.date_signal.date())
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
