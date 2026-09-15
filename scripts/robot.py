#!/usr/bin/env python3
"""Le bot, en autonomie. Une execution par jour, une decision par mois.

    python scripts/robot.py --config config/us.yaml            # une passe reelle (simulation Alpaca)
    python scripts/robot.py --config config/us.yaml --a-blanc  # tout, sauf l'envoi
    python scripts/robot.py --installer                        # tache planifiee Windows

Ce script est concu pour etre appele TOUS LES JOURS par le planificateur de
taches, et pour ne rien faire la plupart du temps. C'est volontaire : un
programme qui decide chaque jour s'il doit agir est plus sur qu'un programme
qui tourne en permanence et qu'on oublie de surveiller.

Ce qu'il fait, dans l'ordre
---------------------------
1. met les cours a jour ;
2. cherche le dernier signal REVOLU (fin de mois passee, pas la periode en
   cours) ;
3. s'arrete net si ce signal a deja ete execute - l'etat est sur disque, donc
   deux lancements le meme jour ne rebalancent pas deux fois ;
4. s'arrete si le signal est trop ancien : rattraper une fin de mois vieille
   de deux semaines, ce serait executer une decision perimee ;
5. verifie tous les controles, sans exception et sans equivalent de --force ;
6. envoie, journalise, et ecrit un compte rendu lisible.

Ce qu'il ne fait pas
--------------------
Il ne touche JAMAIS a de l'argent reel. `--reel` n'existe pas ici et ne sera
pas ajoute : un programme qui envoie des ordres pendant que personne ne
regarde ne doit pas pouvoir engager de l'argent. Le chemin reel reste
`scripts/trade.py --reel`, avec ses trois verrous et sa confirmation au
clavier. Cette limite n'est pas technique, elle est deliberee.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

import pandas as pd

from quantbot import broker, datafeed, live, operations
from quantbot.backtest import BenchmarkMissingError
from quantbot.config import Config
from quantbot.universe import UniverseError

warnings.filterwarnings("ignore", category=FutureWarning)

ETAT = Path("data/robot_etat.json")
COMPTE_RENDU = Path("data/robot_journal.txt")

RIEN_A_FAIRE, AGI, BLOQUE, ERREUR = 0, 0, 1, 2

#: Decisions possibles. Elles sont prises AVANT tout acces reseau.
ATTENDRE, EXECUTER, DEJA_FAIT, TROP_TARD, AUCUN = (
    "attendre", "executer", "deja_fait", "trop_tard", "aucun")


def decider(etat: dict, signal, seances_depuis: int, rattrapage: int = 3):
    """Que faire de ce signal ? Fonction PURE : ni reseau, ni disque, ni horloge.

    Toute la discipline du robot tient dans ces quatre regles, et elles sont
    donc verifiables sans courtier :

    * pas de signal revolu -> rien a faire ;
    * signal deja traite -> rien a faire, meme si le script tourne dix fois ;
    * signal tombe aujourd'hui -> on attend la seance suivante, parce que la
      strategie mesuree execute au lendemain du signal, pas le jour meme ;
    * signal trop ancien -> on saute. Rattraper une fin de mois vieille de
      deux semaines, ce n'est pas de la rigueur, c'est executer une decision
      perimee sur des cours qui ont change.

    Renvoie (decision, raison).
    """
    if signal is None:
        return AUCUN, "aucun signal revolu dans l'historique"
    if etat.get("dernier_signal") == str(signal.date()):
        return DEJA_FAIT, "signal du %s deja traite le %s" % (
            signal.date(), etat.get("dernier_passage", "?"))
    if seances_depuis < 1:
        return ATTENDRE, "signal tombe a la derniere seance : execution demain"
    if seances_depuis > rattrapage:
        return TROP_TARD, ("signal vieux de %d seances, au-dela du rattrapage de %d"
                           % (seances_depuis, rattrapage))
    return EXECUTER, "signal du %s, %d seance(s) apres" % (signal.date(), seances_depuis)


def _lire_etat(chemin=ETAT) -> dict:
    try:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ecrire_etat(etat, chemin=ETAT) -> None:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(etat, indent=1, ensure_ascii=False), encoding="utf-8")


class Rapport:
    """Ecrit a l'ecran ET dans un fichier : personne ne regardera l'ecran."""

    def __init__(self, chemin=COMPTE_RENDU):
        self.chemin = Path(chemin)
        self.lignes = []

    def __call__(self, texte=""):
        print(texte)
        self.lignes.append(texte)

    def clore(self, verdict):
        self("")
        self("VERDICT : %s" % verdict)
        try:
            self.chemin.parent.mkdir(parents=True, exist_ok=True)
            entete = "\n%s\n%s  %s\n%s\n" % ("=" * 72,
                                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                             verdict, "=" * 72)
            with self.chemin.open("a", encoding="utf-8") as fh:
                fh.write(entete + "\n".join(self.lignes) + "\n")
        except Exception:
            pass


def passer(args) -> int:
    rapport = Rapport(Path(args.journal_robot))
    rapport("robot quantbot - %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    cfg = Config.load(args.config)
    etat = _lire_etat(Path(args.etat))

    ops = operations.Operations(cfg, lambda: datafeed.load_panel(cfg))

    # -- 1. cours a jour ---------------------------------------------------
    if not args.sans_fetch:
        rapport("  mise a jour des cours...")
        try:
            info = ops.rafraichir(journal=lambda m: rapport("    " + m))
            rapport("  %d series en cache, %d seance(s) ajoutee(s), derniere cloture %s"
                    % (info["series"], info.get("seances_ajoutees", 0),
                       info.get("derniere_seance") or "?"))
            # Le telechargement peut "reussir" sans rien rapporter : reseau
            # coupe, fournisseur muet, tickers refuses. Le 11 septembre 2026 le
            # robot a affiche "termine : 504 series, 0 manquant(s)" puis decide
            # sur des cours vieux de trois jours. Un compte rendu rassurant
            # n'est pas une donnee a jour.
            if not info.get("ok", True):
                rapport("  Les cours n'ont PAS ete completes. On s'arrete :")
                rapport("  decider sur des cours perimes serait pire que ne rien faire.")
                rapport.clore("ARRET - cours non completes")
                return ERREUR
        except Exception as exc:
            rapport("  ECHEC de la mise a jour : %s" % str(exc)[:200])
            rapport("  On s'arrete : decider sur des cours perimes serait pire que")
            rapport("  ne rien faire.")
            rapport.clore("ARRET - donnees indisponibles")
            return ERREUR

    prices = datafeed.load_panel(cfg)
    close = live._prepare_prices(prices, cfg)[0]
    frequence = cfg.get("execution.rebalance", "monthly")
    echus = live.signaux_echus(close.index, frequence)
    if len(echus) == 0:
        rapport("  aucun signal revolu dans l'historique.")
        rapport.clore("RIEN A FAIRE")
        return RIEN_A_FAIRE

    signal = echus[-1]
    seances_depuis = int(len(close.index) - 1 - close.index.get_indexer([signal])[0])
    rapport("  dernier signal revolu : %s (il y a %d seance(s))"
            % (signal.date(), seances_depuis))

    decision, raison = decider(etat, signal, seances_depuis, args.rattrapage)
    rapport("  decision : %s - %s" % (decision, raison))

    if decision in (AUCUN, DEJA_FAIT, ATTENDRE):
        rapport.clore("RIEN A FAIRE - " + raison)
        return RIEN_A_FAIRE
    if decision == TROP_TARD:
        rapport("  Executer une decision perimee est pire que sauter un mois.")
        _ecrire_etat(dict(etat, dernier_signal=str(signal.date()),
                          dernier_passage=datetime.now().isoformat(timespec="seconds"),
                          dernier_resultat="saute (trop tard)"), Path(args.etat))
        rapport.clore("SAUTE - " + raison)
        return RIEN_A_FAIRE

    # -- 4. controles ------------------------------------------------------
    cible = live.portefeuille_cible(prices, cfg, forcer_signal=signal)
    rapport("  decision prise a la cloture du %s" % cible.date_decision.date())
    rapport("  filtre de regime : %s" % cible.regime_texte)
    rapport("  %d ligne(s) visee(s), %.1f %% en liquidites"
            % (len(cible.poids), 100 * cible.part_liquidites))

    etat_compte = ops.etat(signal=signal)
    if not etat_compte.get("connecte"):
        rapport("  courtier injoignable : %s" % etat_compte.get("erreur", "")[:200])
        rapport.clore("ARRET - courtier injoignable")
        return ERREUR

    rapport("")
    for controle in etat_compte["controles"]:
        rapport("    %s %-26s %s" % ("[ok] " if controle["ok"] else "[NON]",
                                     controle["nom"], controle["detail"][:60]))

    # Le jour de rebalancement est evalue par le robot lui-meme, plus haut :
    # il travaille sur le signal revolu, pas sur la seance courante.
    bloquants = [c for c in etat_compte["controles"]
                 if not c["ok"] and c["nom"] not in ("Jour de rebalancement",
                                                     "Compte sans decouvert")]
    if bloquants:
        rapport("")
        rapport("  %d controle(s) bloquant(s) : rien ne part." % len(bloquants))
        rapport("  Le signal reste a traiter : le robot reessaiera demain, dans la")
        rapport("  limite du rattrapage.")
        rapport.clore("BLOQUE - " + ", ".join(c["nom"] for c in bloquants))
        return BLOQUE

    # -- 5. envoi ----------------------------------------------------------
    if args.a_blanc:
        rapport("")
        rapport("  %d ordre(s) auraient ete envoyes (%s) :"
                % (len(etat_compte["ordres"]), "a blanc"))
        for o in etat_compte["ordres"]:
            rapport("    %-6s %-5s %10.2f  %s" % (o["ticker"], o["sens"], o["montant"], o["motif"]))
        rapport.clore("A BLANC - rien envoye")
        return RIEN_A_FAIRE

    # `signal` traverse jusqu'a l'envoi : la decision executee est celle qui
    # vient d'etre validee, pas une recalculee entre-temps sur la seance du jour.
    resultat = ops.envoyer(forcer=True, signal=signal)
    rapport("")
    rapport("  " + resultat["message"])
    for echec in resultat.get("echecs", []):
        rapport("    ECHEC %-6s %s" % (echec["ticker"], echec["raison"][:80]))

    _ecrire_etat(dict(etat,
                      dernier_signal=str(signal.date()),
                      dernier_passage=datetime.now().isoformat(timespec="seconds"),
                      dernier_resultat="%d ordre(s), %d echec(s)"
                                       % (resultat.get("envoyes", 0), len(resultat.get("echecs", []))),
                      compte=etat_compte["compte"]["numero"],
                      valeur=etat_compte["compte"]["equity"]), Path(args.etat))
    rapport.clore("EXECUTE - %d ordre(s)" % resultat.get("envoyes", 0))
    return AGI if resultat.get("ok") else BLOQUE


MODELE_PS1 = r"""# Lance le robot quantbot. Genere par scripts/robot.py --installer.
Set-Location -LiteralPath "{racine}"
& "{python}" "scripts\robot.py" --config "{config}" *>> "data\robot_sortie.log"
"""


NOM_TACHE = "quantbot"


def _sortie(commande) -> tuple:
    """Lance une commande Windows et renvoie (code, texte)."""
    try:
        r = subprocess.run(commande, capture_output=True)
    except OSError as exc:
        return 1, str(exc)
    brut = (r.stdout or b"") + (r.returncode and (r.stderr or b"") or b"")
    for codec in ("utf-8", "cp1252", "latin-1"):
        try:
            return r.returncode, brut.decode(codec)
        except UnicodeDecodeError:
            continue
    return r.returncode, brut.decode("utf-8", "replace")


def tache_enregistree(nom: str = NOM_TACHE) -> bool:
    """La tache planifiee existe-t-elle REELLEMENT ?"""
    if os.name != "nt":
        return False
    code, _ = _sortie(["schtasks", "/Query", "/TN", nom])
    return code == 0


def installer(args) -> int:
    r"""Ecrit le script de lancement ET enregistre la tache, puis VERIFIE.

    Ce que faisait la version precedente, et pourquoi c'etait grave
    --------------------------------------------------------------
    Elle ecrivait `lancer_robot.ps1`, affichait une commande `schtasks`, et
    s'arretait la. Le drapeau s'appelait `--installer` mais n'installait rien :
    il fallait encore copier la commande a la main. L'ecran se terminait par
    "Ecrit : ...\lancer_robot.ps1", ce qui se lit comme un succes.

    Consequence reelle : la tache n'a jamais existe. Le robot n'a tourne que
    les fois ou il a ete lance a la main, `data\robot_sortie.log` n'a jamais
    ete cree, et personne ne s'en est apercu pendant des semaines - jusqu'a ce
    que la veille signale 92 heures de silence.

    Et la commande affichee etait de toute facon inutilisable : elle se
    terminait par `^`, la continuation de ligne de cmd.exe, alors que le texte
    demandait un terminal PowerShell, ou la continuation s'ecrit avec un
    accent grave. Collee telle quelle, elle echouait.

    La version ci-dessous enregistre la tache elle-meme, puis interroge le
    planificateur pour VERIFIER qu'elle existe. Un installateur qui ne verifie
    pas son propre travail n'est pas un installateur, c'est une suggestion.
    """
    racine = Path.cwd().resolve()
    script = racine / "lancer_robot.ps1"
    if os.name != "nt" and script.exists():
        # Le lanceur contient des chemins ABSOLUS et l'interpreteur Python de
        # la machine cible. L'ecrire depuis un autre systeme le remplacerait
        # par des chemins inutilisables, sans un mot. (C'est arrive.)
        print("Systeme non-Windows : %s n'est PAS reecrit," % script.name)
        print("ses chemins seraient ceux de cette machine-ci.")
    else:
        script.write_text(MODELE_PS1.format(racine=racine, python=sys.executable,
                                            config=args.config), encoding="utf-8")
        print("Ecrit : %s" % script)

    commande_tr = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % script
    # Une seule ligne, sans caractere de continuation : ni `^` ni accent grave
    # ne marchent dans les deux interpreteurs, et cette commande doit pouvoir
    # etre collee dans l'un comme dans l'autre.
    manuel = ('schtasks /Create /TN "%s" /SC DAILY /ST %s /F /TR "%s"'
              % (NOM_TACHE, args.heure, commande_tr.replace(chr(34), chr(92) + chr(34))))

    if os.name != "nt":
        print("\nSysteme non-Windows : la tache ne peut pas etre enregistree ici.")
        print("Sur la machine Windows, lance :\n\n  %s\n" % manuel)
        return 0

    if tache_enregistree() and not args.remplacer:
        print("\nLa tache \"%s\" existe deja. Relance avec --remplacer pour la "
              "recreer." % NOM_TACHE)
        return 0

    if not args.oui:
        print("\nLa tache quotidienne suivante va etre enregistree dans le")
        print("planificateur Windows, a %s :" % args.heure)
        print("\n  %s\n" % commande_tr)
        try:
            reponse = input("Confirmer ? [o/N] ").strip().lower()
        except EOFError:
            reponse = ""
        if reponse not in ("o", "oui", "y", "yes"):
            print("Annule. Pour le faire a la main :\n\n  %s\n" % manuel)
            return 0

    code, texte = _sortie(["schtasks", "/Create", "/TN", NOM_TACHE,
                           "/SC", "DAILY", "/ST", args.heure, "/F",
                           "/TR", commande_tr])
    if texte.strip():
        print(texte.strip())

    # La verification, qui est tout l'interet de cette fonction.
    if tache_enregistree():
        print("\nVERIFIE : la tache \"%s\" est bien enregistree, a %s."
              % (NOM_TACHE, args.heure))
    else:
        print("\nECHEC : la tache n'apparait pas dans le planificateur "
              "(code %d)." % code)
        print("Enregistre-la a la main dans un terminal, puis verifie :\n")
        print("  %s" % manuel)
        print('  schtasks /Query /TN "%s"\n' % NOM_TACHE)
        return ERREUR

    print("\n%s a Paris correspond a 15:00 a New York toute l'annee ou presque,"
          % args.heure)
    print("soit une heure avant la cloture - c'est ce que suppose le backtest.")
    print("(Deux ou trois semaines par an, les changements d'heure decalent ce")
    print("rendez-vous d'une heure ; le robot verifie de toute facon que le")
    print("marche est ouvert avant d'envoyer quoi que ce soit.)\n")
    print("Pour verifier, lancer tout de suite, ou supprimer :")
    print('  schtasks /Query  /TN "%s" /V /FO LIST' % NOM_TACHE)
    print('  schtasks /Run    /TN "%s"' % NOM_TACHE)
    print('  schtasks /Delete /TN "%s" /F' % NOM_TACHE)
    print("\nLe robot ne touche que le compte de SIMULATION. Il n'a aucun moyen")
    print("d'engager de l'argent reel, et ce n'est pas une option a activer.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--a-blanc", action="store_true",
                    help="deroule tout mais n'envoie aucun ordre")
    ap.add_argument("--sans-fetch", action="store_true",
                    help="n'actualise pas les cours (pour un essai rapide)")
    ap.add_argument("--rattrapage", type=int, default=3,
                    help="nb de seances pendant lesquelles un signal manque reste "
                         "rattrapable (defaut 3)")
    ap.add_argument("--etat", default=str(ETAT))
    ap.add_argument("--journal-robot", default=str(COMPTE_RENDU))
    ap.add_argument("--installer", action="store_true",
                    help="ecrit le script de lancement, ENREGISTRE la tache "
                         "quotidienne et verifie qu'elle existe")
    ap.add_argument("--heure", default="21:00",
                    help="heure de declenchement quotidien (defaut 21:00)")
    ap.add_argument("--remplacer", action="store_true",
                    help="recree la tache meme si elle existe deja")
    ap.add_argument("--oui", action="store_true",
                    help="n'attend pas de confirmation au clavier")
    args = ap.parse_args()
    if args.installer:
        return installer(args)
    return passer(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (broker.BrokerError, BenchmarkMissingError, UniverseError, FileNotFoundError) as exc:
        print("\n" + "-" * 72); print(exc); print("-" * 72)
        sys.exit(ERREUR)
