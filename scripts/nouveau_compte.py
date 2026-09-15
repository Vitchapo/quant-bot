#!/usr/bin/env python3
"""Repartir proprement apres avoir cree un nouveau compte papier.

    python scripts/nouveau_compte.py            # montre ce qui serait fait
    python scripts/nouveau_compte.py --appliquer

LE PIEGE QUE CE SCRIPT EXISTE POUR EVITER
------------------------------------------
Creer un compte neuf chez Alpaca ne remet pas a zero ce que le bot a retenu
sur SA machine. Trois fichiers gardent une memoire du compte precedent :

  data/defi_etat.json    capital de depart et plus-haut. S'ils survivent, les
                         garde-fous du defi mesurent le compte NEUF contre le
                         capital de l'ANCIEN - la progression et les limites
                         de perte deviennent fausses sans rien signaler.
  data/robot_etat.json   dernier signal traite. S'il survit, le robot croit
                         avoir deja agi et saute le prochain rebalancement.
  data/journal_ordres.csv  les ordres de l'ancien compte. Celui-la porte une
                         colonne `compte`, donc il se filtre tout seul - mais
                         on l'archive quand meme, pour que les analyses
                         d'execution ne melangent pas deux historiques.

Ce script ne touche JAMAIS au courtier. Creer ou supprimer un compte se fait
sur le tableau de bord d'Alpaca, avec des identifiants ; un script n'a rien a
y faire. Ici on ne fait que du menage local, et rien n'est supprime : tout est
deplace dans data/archives/.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "src"))

from quantbot.config import Config          # noqa: E402

FICHIERS = [
    ("data/defi_etat.json", "capital de depart et plus-haut du defi"),
    ("data/robot_etat.json", "dernier signal traite par le robot"),
    ("data/journal_ordres.csv", "journal des ordres"),
    ("data/robot_journal.txt", "compte rendu du robot"),
]


def comptes_du_journal(chemin: Path) -> set:
    if not chemin.exists():
        return set()
    try:
        with chemin.open("r", newline="", encoding="utf-8") as fh:
            return {l.get("compte", "") for l in csv.DictReader(fh) if l.get("compte")}
    except OSError:
        return set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(RACINE / "config" / "us.yaml"))
    ap.add_argument("--appliquer", action="store_true",
                    help="archive reellement (sans ce drapeau, on ne fait que montrer)")
    ap.add_argument("--sans-courtier", action="store_true")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    print("=" * 68)
    print("  REPARTIR SUR UN COMPTE PAPIER NEUF")
    print("=" * 68)

    # -- 1. quel compte le bot voit-il maintenant ? ------------------------
    numero = None
    if not args.sans_courtier:
        try:
            from quantbot import broker
            api = broker.connecter(cfg, reel=False)
            compte = api.compte()
            numero = compte.get("account_number")
            print("\n  [courtier]  compte %s, %s %s"
                  % (numero, f'{float(compte.get("equity", 0)):,.2f}',
                     compte.get("currency", "")))
            print("              liquidites %s"
                  % f'{float(compte.get("cash", 0)):,.2f}')
        except Exception as exc:
            print("\n  [courtier]  injoignable : %s" % str(exc)[:160])
            print("              (verifie secrets/alpaca.json : un compte neuf")
            print("               exige de NOUVELLES cles API)")

    # -- 2. le journal parle-t-il d'un autre compte ? ----------------------
    journal = RACINE / "data" / "journal_ordres.csv"
    anciens = comptes_du_journal(journal)
    if anciens:
        print("\n  [journal]   %d ordre(s) au nom de : %s"
              % (sum(1 for _ in open(journal, encoding="utf-8")) - 1,
                 ", ".join(sorted(anciens))))
        if numero and numero not in anciens:
            print("              -> le compte actuel (%s) n'y figure pas :" % numero)
            print("                 c'est bien un compte NEUF.")
        elif numero:
            print("              -> le compte actuel y figure deja.")

    # -- 3. menage ---------------------------------------------------------
    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    archives = RACINE / "data" / "archives" / horodatage
    print("\n  [menage]    %s" % ("archivage vers data/archives/%s/" % horodatage
                                  if args.appliquer else "SIMULATION (ajoute --appliquer)"))

    deplaces = 0
    for relatif, quoi in FICHIERS:
        chemin = RACINE / relatif
        if not chemin.exists():
            print("    -  %-26s absent" % Path(relatif).name)
            continue
        print("    %s %-26s %s" % ("->" if args.appliquer else "  ",
                                   Path(relatif).name, quoi))
        if args.appliquer:
            archives.mkdir(parents=True, exist_ok=True)
            # On COPIE puis on vide, plutot que de deplacer : certains montages
            # refusent la suppression, et un echec a mi-chemin laisserait le
            # projet sans son journal.
            shutil.copy2(str(chemin), str(archives / chemin.name))
            if chemin.suffix == ".csv":
                with chemin.open("r", newline="", encoding="utf-8") as fh:
                    entete = fh.readline()
                chemin.write_text(entete, encoding="utf-8")   # on garde l'entete
            else:
                chemin.write_text("", encoding="utf-8")
            deplaces += 1

    if args.appliquer:
        print("\n  %d fichier(s) archive(s). Rien n'a ete supprime." % deplaces)
    else:
        print("\n  Rien n'a ete touche. Relance avec --appliquer.")

    print("\n" + "-" * 68)
    print("  RAPPEL DE L'ORDRE DES OPERATIONS")
    print("-" * 68)
    print("  1. cree le compte sur alpaca.markets (Open New Paper Account, 100 000 $)")
    print("  2. genere de NOUVELLES cles API pour ce compte")
    print("  3. remplace-les dans secrets/alpaca.json")
    print("  4. lance ce script avec --appliquer")
    print("  5. verifie :  python scripts/verifier_sante.py")
    print()
    print("  Et regle la strategie AVANT de demarrer, sinon les premieres")
    print("  seances ne mesureront pas ce que tu crois mesurer :")
    print("    portfolio.volatilite.active : true")
    print("    defi.active                 : true")
    print("    execution.rebalance         : weekly  (pour les 5 jours minimum)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
