#!/usr/bin/env python3
"""Passe les ordres du portefeuille cible chez le courtier (Alpaca).

    python scripts/trade.py --config config/us.yaml                 # simulation, SANS envoyer
    python scripts/trade.py --config config/us.yaml --envoyer       # simulation, envoie
    python scripts/trade.py --config config/us.yaml --compte        # etat du compte seulement
    python scripts/trade.py --config config/us.yaml --annuler       # annule les ordres en attente

Par defaut : compte de SIMULATION, et RIEN n'est envoye. Il faut `--envoyer`
pour que le moindre ordre parte, et trois verrous supplementaires plus une
confirmation tapee a la main pour toucher a de l'argent reel.

Le script refuse de travailler si aujourd'hui n'est pas un jour de
rebalancement (`--force` pour outrepasser), si le marche est ferme
(`--hors-seance`), si les donnees en cache datent, ou si le montant total a
echanger depasse le plafond fixe dans la configuration.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import broker, live, operations, orders as ord_mod
from quantbot.backtest import BenchmarkMissingError
from quantbot.config import Config
from quantbot.universe import UniverseError
from run_backtest import load_prices

warnings.filterwarnings("ignore", category=FutureWarning)

JOURNAL = Path("data/journal_ordres.csv")


def _euros(x, devise="USD"):
    return ("%s %s" % (("%.2f" % float(x)).replace(",", " "), devise))


def _journaliser(lignes, chemin=JOURNAL):
    """Trace ecrite de tout ce qui a ete envoye. Sert de memoire au bot.

    Une SEULE implementation, celle de `operations` : la liste des colonnes
    existait ici en double, et c'est cette duplication qui a laisse les deux
    formats diverger jusqu'a rendre le journal illisible.
    """
    operations.journaliser(lignes, chemin)


def _dernier_passage(chemin) -> str:
    """Horodatage du dernier ordre journalise, pour rappeler la cadence reelle."""
    try:
        with Path(chemin).open(encoding="utf-8") as fh:
            lignes = [l.get("horodatage", "") for l in csv.DictReader(fh)]
        return max(lignes) if lignes else ""
    except Exception:
        return ""


def _afficher_compte(compte, api):
    devise = compte.get("currency", "USD")
    print("\n%s\n  COMPTE %s\n%s" % ("=" * 68,
          "DE SIMULATION" if api.simulation else "REEL - ARGENT VERITABLE", "=" * 68))
    print("  numero            %s" % compte.get("account_number"))
    print("  statut            %s" % compte.get("status"))
    print("  valeur totale     %s" % _euros(compte.get("equity", 0), devise))
    print("  liquidites        %s" % _euros(compte.get("cash", 0), devise))
    print("  pouvoir d'achat   %s" % _euros(compte.get("buying_power", 0), devise))
    bloques = [c for c in ("trading_blocked", "account_blocked", "transfers_blocked")
               if compte.get(c)]
    if bloques:
        print("  /!\\ BLOCAGES : %s" % ", ".join(bloques))
    return devise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--envoyer", action="store_true",
                    help="envoie reellement les ordres (sinon : simulation d'affichage)")
    ap.add_argument("--reel", action="store_true",
                    help="compte d'ARGENT REEL au lieu du compte de simulation")
    ap.add_argument("--force", action="store_true",
                    help="agit meme si aujourd'hui n'est pas un jour de rebalancement")
    ap.add_argument("--hors-seance", action="store_true",
                    help="agit meme marche ferme (les ordres seront executes a l'ouverture)")
    ap.add_argument("--journal", default=str(JOURNAL),
                    help="fichier de journal (a rediriger pour tout essai)")
    ap.add_argument("--marge", action="store_true",
                    help="autorise les achats a credit si les liquidites ne suffisent pas")
    ap.add_argument("--plafond", type=float,
                    help="plafond d'echange, en fraction du compte "
                         "(surcharge broker.max_echange_par_seance)")
    ap.add_argument("--compte", action="store_true", help="affiche l'etat du compte et s'arrete")
    ap.add_argument("--annuler", action="store_true", help="annule les ordres en attente et s'arrete")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    api = broker.connecter(cfg, reel=args.reel)
    compte = api.compte()
    devise = _afficher_compte(compte, api)

    if args.annuler:
        api.annuler_ordres()
        print("\nOrdres en attente annules.")
        return 0
    if args.compte:
        detail = api.positions_detail()
        if detail:
            print("\n  %-8s %10s %12s %12s" % ("ticker", "titres", "valeur", "+/- latent"))
            for p in sorted(detail, key=lambda x: -abs(float(x["market_value"]))):
                print("  %-8s %10s %12.2f %12.2f"
                      % (p["symbol"], p["qty"], float(p["market_value"]),
                         float(p["unrealized_pl"])))
        else:
            print("\n  Aucune position.")
        return 0

    if compte.get("trading_blocked") or compte.get("account_blocked"):
        print("\nCompte bloque cote courtier : rien ne sera envoye.")
        return 2

    # -- portefeuille cible -------------------------------------------------
    prices = load_prices(cfg, False)
    cible = live.portefeuille_cible(prices, cfg)
    print("\n  derniere cloture  %s  (%d seance(s) manquante(s))"
          % (cible.as_of.date(), cible.anciennete_donnees))
    print("  filtre de regime  %s" % cible.regime_texte)

    if cible.anciennete_donnees > 1:
        print("\nDonnees trop anciennes (%d seance(s) manquante(s)). Lance d'abord :"
              % cible.anciennete_donnees)
        print("    python scripts/fetch_data.py --config %s" % args.config)
        return 2
    if not cible.est_jour_execution and args.force:
        dernier = _dernier_passage(Path(args.journal))
        print("\n  /!\\ --force : aujourd'hui n'est PAS un jour de rebalancement.")
        if dernier:
            print("      Dernier passage enregistre : %s." % dernier[:16].replace("T", " a "))
        print("      La strategie a ete mesuree en rebalancant UNE FOIS PAR MOIS. La")
        print("      forcer plus souvent n'est pas une version prudente de la meme")
        print("      strategie : c'en est une autre, qui paie plus de frais et dont le")
        print("      backtest ne dit rien. Le prochain vrai signal tombe a la derniere")
        print("      seance de la periode.")

    if not cible.est_jour_execution and not args.force:
        print("\nAujourd'hui n'est pas un jour d'execution (%s, decalage %d jour(s))."
              % (cfg.get("execution.rebalance"), cfg.get("execution.execution_lag")))
        print("La discipline de calendrier fait partie de la strategie : reagir entre")
        print("deux rebalancements ne fait que payer des frais. --force pour outrepasser.")
        return 0

    horloge = api.horloge()
    if not horloge.get("is_open") and not args.hors_seance:
        print("\nMarche ferme (prochaine ouverture : %s)." % horloge.get("next_open"))
        print("--hors-seance pour envoyer quand meme : les ordres seront alors")
        print("executes a l'ouverture, a un cours qu'on ne connait pas encore.")
        return 0

    # -- planification ------------------------------------------------------
    equity = float(compte.get("equity", 0.0))
    positions = api.positions()
    cours = {t: float(v) for t, v in cible.cours.items()}
    cibles = {t: float(p) for t, p in cible.poids.items()}

    negociables = api.negociables(sorted(cibles))
    refuses = [t for t, ok in negociables.items() if not ok]
    if refuses:
        print("\n  %d titre(s) vise(s) non negociable(s) chez le courtier, ignore(s) : %s"
              % (len(refuses), ", ".join(refuses)))
        for t in refuses:
            cibles.pop(t, None)

    seuil = ord_mod.seuil_minimal(cfg, equity)
    max_pct = float(cfg.get("broker.max_order_pct", 0.15))
    liste = ord_mod.planifier(
        cibles, positions, cours, equity,
        seuil_notional=seuil, max_notional=max_pct * equity,
        fractionnaire=bool(cfg.get("broker.fractionnaire", True)))
    manquants = ord_mod.sans_cours(cibles, positions, cours)
    residus = ord_mod.poussieres(cibles, positions, cours)

    print("\n%s\n  ORDRES  (%d ligne(s) visee(s), valeur du compte %s)\n%s"
          % ("=" * 68, len(cibles), _euros(equity, devise), "=" * 68))
    if not liste:
        print("  Aucun ordre : le portefeuille est deja aligne.")
        return 0
    print("  %-7s %-6s %10s %12s %9s %9s  %s"
          % ("ticker", "sens", "titres", "montant", "actuel", "cible", "motif"))
    for o in liste:
        qte = "%.4f" % o.quantite if o.quantite is not None else "au montant"
        print("  %-7s %-6s %10s %12.2f %8.2f%% %8.2f%%  %s"
              % (o.ticker, "VENTE" if o.sens == "sell" else "ACHAT", qte, o.montant,
                 100 * o.poids_actuel, 100 * o.poids_cible, o.motif))
    r = ord_mod.resume(liste)
    print("\n  ventes %s | achats %s | echange total %s (%.1f%% du compte)"
          % (_euros(r["ventes"], devise), _euros(r["achats"], devise),
             _euros(r["echange"], devise), 100 * r["echange"] / max(equity, 1e-9)))
    if manquants:
        print("  /!\\ sans cours en cache, non traites : %s" % ", ".join(manquants))
    if residus:
        print("\n  %d ligne(s) residuelle(s) trop petite(s) pour etre soldee(s) :" % len(residus))
        for r in residus:
            print("     %-6s %.3e titre(s), soit %s" % (r["ticker"], r["titres"],
                                                        _euros(r["valeur"], devise)))
        print("     Aucun courtier n'executera un ordre d'un tel montant. Ces lignes")
        print("     restent au bilan sans consequence : elles ne sont plus reenvoyees.")

    plafond = float(args.plafond if args.plafond is not None
                    else cfg.get("broker.max_echange_par_seance", 2.0))
    if r["echange"] > plafond * equity:
        print("\nREFUS : l'echange represente %.0f%% du compte, au-dessus du plafond de %.0f%%"
              % (100 * r["echange"] / equity, 100 * plafond))
        print("Ce plafond attrape les bugs de calcul : tout vendre puis tout racheter")
        print("represente 200%, donc rien de legitime ne va au-dela. Verifie le plan")
        print("ci-dessus avant d'utiliser --plafond pour outrepasser.")
        return 2

    # La strategie est concue SANS levier : elle dimensionne sur la valeur du
    # compte, pas sur le pouvoir d'achat. Mais si le produit des ventes n'est
    # pas encore disponible, les achats partent quand meme - a credit, en
    # silence. On le detecte plutot que de le decouvrir sur un releve.
    liquidites = float(compte.get("cash", 0.0))
    if liquidites < -0.01:
        print("\n  /!\\ Le compte est DEJA a decouvert de %s." % _euros(-liquidites, devise))
        print("      La strategie est concue sans levier : un solde negatif veut dire")
        print("      qu'un passage precedent a achete a credit. Les ventes ci-dessus le")
        print("      reduisent ; verifie le solde au prochain passage.")
    besoin = r["achats"] - r["ventes"] - liquidites
    if besoin > 0.01:
        print("\n  /!\\ Ces achats depassent de %s les liquidites disponibles plus le"
              % _euros(besoin, devise))
        print("      produit des ventes : le courtier les executerait A CREDIT.")
        if not args.marge:
            print("\nREFUS : la strategie est concue sans levier.")
            print("Attends le denouement des ventes (J+1), ou passe --marge si tu")
            print("assumes explicitement d'emprunter.")
            return 2
        print("      --marge fourni : on continue.")

    if not args.envoyer:
        print("\nRien n'a ete envoye. Ajoute --envoyer pour passer ces ordres.")
        return 0

    # -- confirmation pour l'argent reel ------------------------------------
    if args.reel:
        numero = str(compte.get("account_number", ""))
        print("\n%s" % ("!" * 68))
        print("  ARGENT REEL. Compte %s, valeur %s." % (numero, _euros(equity, devise)))
        print("  Tape les 4 derniers caracteres du numero de compte pour confirmer.")
        print("%s" % ("!" * 68))
        try:
            saisie = input("  > ").strip()
        except EOFError:
            saisie = ""
        if saisie != numero[-4:]:
            print("\nConfirmation incorrecte : rien n'a ete envoye.")
            return 2

    # -- fourchette au moment de l'envoi ------------------------------------
    # Sans cette photographie, aucune mesure honnete du cout d'execution n'est
    # possible apres coup : toute autre reference est separee du remplissage
    # par un intervalle de temps pendant lequel le marche a bouge.
    cotations = {}
    try:
        cotations = api.cotations(sorted({o.ticker for o in liste}))
        if cotations:
            print("\n  fourchette relevee pour %d titre(s) au moment de l'envoi"
                  % len(cotations))
    except Exception as exc:
        print("\n  cotations indisponibles (%s) : le cout d'execution ne pourra pas"
              % str(exc)[:60])
        print("  etre mesure pour ces ordres.")

    # -- envoi --------------------------------------------------------------
    mode = "REEL" if args.reel else "simulation"
    numero_compte = str(compte.get("account_number", "?"))
    horodatage = datetime.now().isoformat(timespec="seconds")
    lignes, envoyes, echecs = [], 0, 0
    for o in liste:
        ligne = {"horodatage": horodatage, "compte": numero_compte,
                 "mode": mode, "ticker": o.ticker,
                 "sens": o.sens, "quantite": o.quantite, "montant": o.montant,
                 # la DATE du cours de reference : sans elle, impossible de
                 # savoir plus tard si l'ecart mesure vient du glissement
                 # d'execution ou simplement de donnees qui dataient.
                 "cours": o.cours, "date_cours": str(cible.as_of.date()),
                 "motif": o.motif}
        cot = cotations.get(o.ticker)
        if cot:
            achat, vente, milieu = cot
            ligne["cours_marche"] = round(milieu, 4)
            ligne["fourchette_bps"] = round(10_000 * (vente - achat) / milieu, 1)
        try:
            reponse = api.envoyer_ordre(
                o.ticker, o.sens, quantite=o.quantite,
                montant=None if o.quantite is not None else o.montant,
                client_order_id="quantbot-%s-%s-%d" % (cfg.get("name"), o.ticker, int(time.time())))
            ligne["statut"] = reponse.get("status", "envoye")
            ligne["id_courtier"] = reponse.get("id", "")
            envoyes += 1
            print("  envoye   %-7s %-5s %s" % (o.ticker, o.sens, ligne["statut"]))
        except broker.BrokerError as exc:
            ligne["statut"] = "ECHEC"
            ligne["id_courtier"] = str(exc)[:180]
            echecs += 1
            print("  ECHEC    %-7s %-5s %s" % (o.ticker, o.sens, str(exc)[:120]))
        lignes.append(ligne)
        time.sleep(0.12)          # on reste poli avec l'API

    _journaliser(lignes, Path(args.journal))
    print("\n%d ordre(s) envoye(s), %d echec(s). Journal : %s" % (envoyes, echecs, JOURNAL))
    if not args.reel:
        print("Compte de SIMULATION : aucun argent reel n'a bouge.")
    return 0 if echecs == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (broker.BrokerError, BenchmarkMissingError, UniverseError, FileNotFoundError) as exc:
        print("\n" + "-" * 72)
        print(exc)
        print("-" * 72)
        sys.exit(2)
