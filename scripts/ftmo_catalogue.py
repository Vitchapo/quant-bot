#!/usr/bin/env python3
"""Releve le catalogue REEL du terminal FTMO. A lancer sous Windows.

    python scripts/ftmo_catalogue.py                         # affiche
    python scripts/ftmo_catalogue.py --actions                # actions seules
    python scripts/ftmo_catalogue.py --ecrire config/univers_ftmo.txt
    python scripts/ftmo_catalogue.py --confronter config/univers_ftmo.txt

Pourquoi ce script existe
-------------------------
`config/univers_ftmo.txt` livre avec le projet est une HYPOTHESE : FTMO ne
publie pas son catalogue sous une forme lisible par machine, et il depend du
serveur et du type de compte. Une liste de tickers ecrite a la main est fausse
des le premier changement, et l'erreur est silencieuse - un titre absent du
catalogue disparait simplement du portefeuille sans que rien ne le dise.

`--confronter` repond a la seule question qui compte avant de lancer le bot :
lesquels de mes tickers existent reellement chez ce courtier, et sous quel nom.

`--ecrire` remplace la liste par la verite du terminal. C'est la seule facon
honnete de la maintenir.
"""
from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401

from quantbot import mt5broker
from quantbot.config import Config

#: Mots-cles des groupes MT5 qui contiennent des actions, selon les courtiers.
GROUPES_ACTIONS = ("stock", "share", "equit", "us_", "usa", "nasdaq", "nyse")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/ftmo.yaml")
    ap.add_argument("--actions", action="store_true",
                    help="ne garder que ce qui ressemble a une action")
    ap.add_argument("--ecrire", metavar="FICHIER",
                    help="ecrit le catalogue releve dans ce fichier")
    ap.add_argument("--confronter", metavar="FICHIER",
                    help="compare une liste existante au catalogue du terminal")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    try:
        api = mt5broker.connecter(cfg)
    except mt5broker.MT5Indisponible as exc:
        print("\n" + "-" * 72); print(exc); print("-" * 72)
        return 2

    try:
        mt5 = api._mt5
        symboles = mt5.symbols_get() or []
        print("Terminal : %d symbole(s) au catalogue\n" % len(symboles))

        par_groupe: dict = {}
        for s in symboles:
            groupe = getattr(s, "path", "").split("\\")[0] or "(sans groupe)"
            par_groupe.setdefault(groupe, []).append(s.name)
        print("Groupes :")
        for g in sorted(par_groupe):
            print("  %-28s %4d" % (g, len(par_groupe[g])))

        retenus = [s.name for s in symboles]
        if args.actions:
            retenus = [s.name for s in symboles
                       if any(m in getattr(s, "path", "").lower() for m in GROUPES_ACTIONS)]
            print("\n%d symbole(s) dans un groupe d'actions." % len(retenus))

        if args.confronter:
            voulus = [l.strip() for l in open(args.confronter, encoding="utf-8")
                      if l.strip() and not l.startswith("#")]
            carte = api.construire_carte(voulus)
            print("\n%-14s %-16s %s" % ("ticker", "nom courtier", "etat"))
            print("-" * 48)
            for t in voulus:
                if t in carte:
                    print("%-14s %-16s trouve" % (t, carte[t]))
                else:
                    print("%-14s %-16s ABSENT" % (t, "-"))
            print("\n%d/%d trouve(s), %d absent(s)."
                  % (len(carte), len(voulus), len(api.absents)))
            if api.absents:
                print("Absents : " + " ".join(sorted(api.absents)))
                print("\nCes tickers seront ecartes au demarrage, sans planter.")

        if args.ecrire:
            with open(args.ecrire, "w", encoding="utf-8") as fh:
                fh.write("# Catalogue releve sur le terminal FTMO le %s.\n"
                         "# Genere par scripts/ftmo_catalogue.py --ecrire.\n\n"
                         % __import__("datetime").date.today())
                for n in sorted(retenus):
                    fh.write(n + "\n")
            print("\nEcrit : %s (%d symboles)" % (args.ecrire, len(retenus)))
        return 0
    finally:
        api.fermer()


if __name__ == "__main__":
    sys.exit(main())
