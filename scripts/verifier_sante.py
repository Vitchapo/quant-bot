#!/usr/bin/env python3
"""Le bot va-t-il bien ? Une seule commande, un seul verdict.

    python scripts/verifier_sante.py

Trois controles, independants les uns des autres :

  1. le robot a-t-il donne signe de vie, et qu'a-t-il fait ?
  2. ce que le courtier detient correspond-il a ce que le bot croit ?
  3. un rebalancement a-t-il ete interrompu en cours de route ?

Le code de sortie vaut 0 si tout va bien, 1 en cas d'attention, 2 en cas
d'alerte - pour qu'une tache planifiee puisse s'en servir sans lire le texte.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import broker, surveillance
from quantbot.config import Config
from quantbot.operations import COLONNES, JOURNAL, entete, migrer_journal

RACINE = Path(__file__).resolve().parent.parent
ETAT_ROBOT = RACINE / "data" / "robot_etat.json"

SYMBOLES = {"normal": "OK ", "attention": "/!\\", "alerte": "!!!"}


def lire_journal(chemin: Path) -> list:
    if not chemin.exists():
        return []
    # Un journal a l'ancien format ferait echouer la lecture en silence.
    if entete(chemin) not in ([], COLONNES):
        rapport = migrer_journal(chemin)
        if rapport["migre"]:
            print("  journal migre au format courant (%d lignes, sauvegarde : %s)"
                  % (rapport["lignes"], Path(rapport["sauvegarde"]).name))
    with chemin.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(RACINE / "config" / "us.yaml"))
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--etat", default=str(ETAT_ROBOT))
    ap.add_argument("--sans-courtier", action="store_true",
                    help="ne pas contacter le courtier (controles hors ligne)")
    ap.add_argument("--silence-max", type=int, default=surveillance.SILENCE_MAX_HEURES,
                    help="heures de silence tolerees avant alerte")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    print("=" * 68)
    print("  ETAT DE SANTE DU BOT")
    print("=" * 68)

    # -- 1. le robot -------------------------------------------------------
    etat = surveillance.lire_etat(args.etat)
    sante = surveillance.sante_robot(etat, silence_max_heures=args.silence_max)
    print("\n  [robot]")
    print("    %s %s" % (SYMBOLES[sante["niveau"]], sante["message"]))
    tache = surveillance.tache_planifiee()
    if tache.get("connu"):
        print("    %s %s" % ("OK " if tache["existe"] else "!!!", tache["message"]))

    # -- 2. le journal -----------------------------------------------------
    lignes = lire_journal(Path(args.journal))
    incomplet = surveillance.rebalancement_incomplet(lignes)
    print("\n  [journal]  %d ordre(s)" % len(lignes))
    if incomplet["ok"]:
        print("    OK  aucun rebalancement interrompu detecte.")
    else:
        for v in incomplet["incidents"]:
            print("    /!\\ %s : %s" % (v["quand"], v["souci"]))

    # -- 3. le courtier ----------------------------------------------------
    reconciliation = None
    if args.sans_courtier:
        print("\n  [courtier]  ignore (--sans-courtier)")
    else:
        try:
            api = broker.connecter(cfg, reel=False)
            compte = api.compte()
            reelles = api.positions()
            equity = float(compte.get("equity", 0.0))
            identifiant = compte.get("account_number")

            # Source de verite, par ordre de preference :
            #   1. ce que le COURTIER dit avoir execute (filled_qty) ;
            #   2. a defaut, le journal du bot - qui n'enregistre que des
            #      ordres ENVOYES, et dont les achats fractionnaires sont
            #      passes en montant, donc sans quantite.
            source, fiabilite = "courtier", {"ok": True}
            try:
                executes = api.ordres_executes(limite=500)
                attendues = surveillance.positions_executees(executes)
                if not attendues:
                    raise ValueError("aucun ordre execute renvoye")
            except Exception:
                source = "journal (approximatif)"
                attendues, fiabilite = surveillance.positions_attendues_du_journal(
                    lignes, compte=identifiant)
            cours = {t: None for t in set(reelles) | set(attendues)}
            try:
                for t, c in api.cotations(sorted(cours)).items():
                    prix = c.get("ap") or c.get("bp")
                    if prix:
                        cours[t] = float(prix)
            except Exception:
                pass
            for d in api.positions_detail():
                t = d.get("symbol")
                if t and cours.get(t) in (None, 0):
                    try:
                        cours[t] = float(d.get("current_price") or 0.0)
                    except (TypeError, ValueError):
                        pass

            # Sur le chemin de repli, les quantites sont reconstituees par
            # `montant / cours` : l'ecart de prix a l'execution introduit
            # mecaniquement quelques dixiemes de pour cent. Comparer cela au
            # courtier avec la tolerance serree du chemin exact produirait des
            # anomalies a chaque ligne, toutes fausses.
            tolerance = (surveillance.TOLERANCE_DEFAUT if source == "courtier"
                         else 5 * surveillance.TOLERANCE_DEFAUT)
            reconciliation = surveillance.reconcilier(
                reelles, attendues, cours, equity, tolerance=tolerance,
                fiabilite=fiabilite, source=source)
            print("\n  [courtier]  compte %s, %s USD  (reference : %s)"
                  % (identifiant, f"{equity:,.2f}", source))
            if not reconciliation["exploitable"]:
                print("    /!\\ %s" % fiabilite["message"])
                print("    Rien n'est conclu sur le compte : l'outil ne sait pas")
                print("    reconstituer ce qui devrait etre detenu.")
            elif reconciliation["conforme"]:
                print("    OK  %d ligne(s) conformes au journal." % len(reelles))
            else:
                print("    /!\\ %d anomalie(s), ecart total %.2f USD (%.2f %%)"
                      % (len(reconciliation["anomalies"]),
                         reconciliation["ecart_total"],
                         (reconciliation["ecart_total_pct"] or 0) * 100))
                print("      %-8s %14s %14s %12s  %s"
                      % ("ticker", "chez toi", "attendu", "ecart USD", "verdict"))
                for a in sorted(reconciliation["anomalies"],
                                key=lambda x: -abs(x["ecart_valeur"] or 0))[:15]:
                    val = ("%12.2f" % a["ecart_valeur"]) if a["ecart_valeur"] is not None else "           ?"
                    print("      %-8s %14.6f %14.6f %s  %s"
                          % (a["ticker"], a["reel"], a["attendu"], val, a["verdict"]))
                print("\n    Rappel : le journal enregistre des ordres ENVOYES, pas")
                print("    executes. Un ecart peut donc venir d'un ordre partiellement")
                print("    rempli autant que d'une vraie desynchronisation.")
        except Exception as exc:
            print("\n  [courtier]  injoignable : %s" % exc)

    # -- verdict -----------------------------------------------------------
    # L'etat du coupe-circuit journalier : une tache qui s'execute a l'heure
    # contre un terminal absent a l'air vivante vue du planificateur. Elle
    # doit remonter ici, pas seulement dans un fichier que personne n'ouvre.
    etat_cc = surveillance.lire_etat(Path("data/coupe_circuit_etat.json"))
    b = surveillance.bilan(etat, reconciliation, incomplet,
                           etat_coupe_circuit=etat_cc)
    print("\n" + "-" * 68)
    print("  VERDICT : %s  %s" % (SYMBOLES[b["niveau"]], b["resume"]))
    for message in b["alertes"][1:] + b["attentions"]:
        print("            - %s" % message)
    print("-" * 68)
    return {"normal": 0, "attention": 1, "alerte": 2}[b["niveau"]]


if __name__ == "__main__":
    sys.exit(main())
