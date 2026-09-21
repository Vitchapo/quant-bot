#!/usr/bin/env python3
"""Prepare la configuration FTMO : trouve le terminal, l'ecrit, et VERIFIE.

    python scripts/preparer_ftmo.py                 # detecte et verifie
    python scripts/preparer_ftmo.py --terminal "C:/.../terminal64.exe"
    python scripts/preparer_ftmo.py --sans-ecrire   # diagnostic seul

Appele par `preparer_ftmo.bat`, qu'on peut double-cliquer.

Pourquoi ce script ne se contente pas d'ecrire la config
--------------------------------------------------------
Le 21 septembre 2026, `coupe_circuit.py --installer` a rendu « VERIFIE : tache
enregistree » alors que MT5 n'etait pas installe. La tache existait, partait a
l'heure, et ne surveillait rien. Un installateur qui verifie son propre travail
mais pas le fait que ce travail SERVE a quelque chose n'est pas un
installateur, c'est une suggestion bien presentee.

Ce script enchaine donc cinq verifications, dans l'ordre ou elles echouent, et
s'arrete a la premiere. Il ne dit « pret » que si le terminal a REPONDU.

  1. l'interpreteur Python : celui de la tache planifiee, pas un autre
  2. la librairie MetaTrader5, importable DANS cet interpreteur
  3. le terminal sur le disque
  4. la connexion au terminal, avec un compte connecte et l'AutoTrading actif
  5. le catalogue : combien de nos tickers existent reellement chez FTMO
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "src"))
sys.path.insert(0, str(RACINE / "scripts"))

#: Emplacements ou un terminal MT5 se trouve en pratique. Le nom du dossier
#: varie selon le courtier : FTMO livre le sien sous son propre nom.
DOSSIERS = (
    r"C:\Program Files",
    r"C:\Program Files (x86)",
)
INDICES_FTMO = ("ftmo",)


def candidats_terminaux(dossiers=DOSSIERS, lister=None) -> list:
    """Tous les terminal64.exe trouves. Fonction separee pour etre testable.

    `lister` permet a un test d'injecter une arborescence sans toucher au
    disque - c'est le seul moyen de tester ce chemin hors Windows.
    """
    trouves = []
    for base in dossiers:
        for chemin in (lister(base) if lister else _chercher(base)):
            trouves.append(str(chemin))
    return trouves


def _chercher(base: str) -> list:
    p = Path(base)
    if not p.is_dir():
        return []
    out = []
    try:
        for enfant in p.iterdir():
            if not enfant.is_dir():
                continue
            exe = enfant / "terminal64.exe"
            if exe.exists():
                out.append(exe)
    except OSError:
        pass
    return out


def choisir_terminal(candidats) -> tuple:
    """Renvoie (chemin, raison). Prefere un terminal dont le nom dit FTMO.

    Plusieurs MT5 peuvent cohabiter - c'est meme le cas normal quand on a
    essaye deux prop firms. Se tromper de terminal, c'est se connecter au
    mauvais compte, et ca ne se voit pas dans un journal.
    """
    if not candidats:
        return None, "aucun terminal64.exe trouve"
    ftmo = [c for c in candidats if any(m in c.lower() for m in INDICES_FTMO)]
    if len(ftmo) == 1:
        return ftmo[0], "seul terminal dont le chemin mentionne FTMO"
    if len(ftmo) > 1:
        return None, ("%d terminaux mentionnent FTMO : choisis avec "
                      "--terminal\n    " % len(ftmo)) + "\n    ".join(ftmo)
    if len(candidats) == 1:
        return candidats[0], ("seul terminal installe, mais son chemin ne "
                              "mentionne pas FTMO - verifie que c'est le bon")
    return None, ("%d terminaux trouves, aucun ne mentionne FTMO. Choisis avec "
                  "--terminal :\n    " % len(candidats)) + "\n    ".join(candidats)


def ecrire_terminal(chemin_config: Path, terminal: str) -> bool:
    """Ecrit `broker.terminal` sans toucher au reste du fichier.

    Substitution ciblee sur la ligne, pas de relecture/reecriture YAML : un
    aller-retour par pyyaml perdrait tous les commentaires du fichier, qui sont
    la moitie de sa valeur.

    Les antislash Windows sont convertis en slash : `C:\\Program Files\\...`
    dans une chaine YAML entre guillemets fait de `\\P` une sequence
    d'echappement invalide, et le fichier ne se charge plus.
    """
    texte = chemin_config.read_text(encoding="utf-8")
    valeur = str(terminal).replace("\\", "/")
    lignes = texte.splitlines(keepends=True)
    for i, ligne in enumerate(lignes):
        nu = ligne.strip()
        if nu.startswith("terminal:"):
            indent = ligne[:len(ligne) - len(ligne.lstrip())]
            lignes[i] = '%sterminal: "%s"\n' % (indent, valeur)
            chemin_config.write_text("".join(lignes), encoding="utf-8")
            return True
    return False


def etape(n: int, titre: str) -> None:
    print("\n[%d/5] %s" % (n, titre))


def echouer(message: str, remede: str = "") -> int:
    print("\n" + "!" * 72)
    print("ARRET : " + message)
    if remede:
        print()
        for l in remede.splitlines():
            print("  " + l)
    print("!" * 72)
    print("\nLa configuration n'a PAS ete declaree prete. C'est voulu : un")
    print("coupe-circuit qui ne joint pas le terminal ne protege rien.")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/ftmo.yaml")
    ap.add_argument("--terminal", help="chemin de terminal64.exe, si la detection echoue")
    ap.add_argument("--sans-ecrire", action="store_true",
                    help="diagnostic seul, ne modifie aucun fichier")
    ap.add_argument("--pas-de-relance", action="store_true",
                    help="interne : empeche la relance avec l'interpreteur "
                         "de la tache planifiee (et donc toute boucle)")
    args = ap.parse_args()

    os.chdir(RACINE)
    config = Path(args.config)
    print("=" * 72)
    print("  Preparation FTMO - quantbot")
    print("  dossier : %s" % RACINE)
    print("=" * 72)

    # -- 1. l'interpreteur -------------------------------------------------
    etape(1, "interpreteur Python")
    print("  en cours d'execution : %s" % sys.executable)
    print("  version              : %s" % sys.version.split()[0])
    lanceur = RACINE / "lancer_coupe_circuit.ps1"
    attendu = None
    if lanceur.exists():
        for l in lanceur.read_text(encoding="utf-8").splitlines():
            if "python.exe" in l and l.strip().startswith("&"):
                attendu = l.split('"')[1]
    if attendu:
        print("  utilise par la tache : %s" % attendu)
        meme = False
        try:
            meme = Path(attendu).resolve() == Path(sys.executable).resolve()
        except OSError:
            meme = str(attendu) == str(sys.executable)
        if not meme and Path(attendu).exists() and not args.pas_de_relance:
            # On se RELANCE avec l'interpreteur de la tache, au lieu de
            # demander a l'utilisateur de recopier une commande.
            #
            # Sans cela, `pip install MetaTrader5` irait dans le mauvais
            # Python : tout se passerait bien ici, et la tache planifiee
            # echouerait en ImportError - en pleine seance, dans un journal
            # que personne ne lit. C'est exactement le genre d'ecart qu'un
            # script de preparation existe pour supprimer.
            print("\n  La tache utilise un AUTRE interpreteur. Une librairie")
            print("  installee ici lui serait invisible. On se relance avec le sien.")
            print("  " + "-" * 60)
            r = subprocess.run([attendu, str(Path(__file__).resolve()),
                                "--pas-de-relance"] + sys.argv[1:])
            return r.returncode
        if not meme and not Path(attendu).exists():
            print("\n  ATTENTION : l'interpreteur de la tache n'existe plus :")
            print("    %s" % attendu)
            print("  Recree la tache apres cette preparation :")
            print("    python scripts\\coupe_circuit.py --installer --minutes 10")

    # -- 2. la librairie ---------------------------------------------------
    etape(2, "librairie MetaTrader5")
    try:
        import MetaTrader5 as mt5  # noqa: N813
        print("  installee, version %s" % getattr(mt5, "__version__", "?"))
    except ImportError as exc:
        print("  absente : %s" % exc)
        print("  installation...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "MetaTrader5"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return echouer(
                "impossible d'installer MetaTrader5.",
                (r.stderr or r.stdout or "")[-500:] +
                "\n\nLa librairie n'existe QU'EN ROUE WINDOWS et exige un Python\n"
                "64 bits. Verifie les deux.")
        try:
            import MetaTrader5 as mt5  # noqa: N813, F811
            print("  installee : version %s" % getattr(mt5, "__version__", "?"))
        except ImportError as exc2:
            return echouer("installee mais non importable : %s" % exc2)

    # -- 3. le terminal sur le disque -------------------------------------
    etape(3, "terminal MT5 sur le disque")
    if args.terminal:
        terminal, raison = args.terminal, "impose par --terminal"
        if not Path(terminal).exists():
            return echouer("le chemin donne n'existe pas : %s" % terminal)
    else:
        candidats = candidats_terminaux()
        print("  %d terminal64.exe trouve(s)" % len(candidats))
        for c in candidats:
            print("    %s" % c)
        terminal, raison = choisir_terminal(candidats)
        if terminal is None:
            return echouer(
                "impossible de choisir un terminal : " + raison,
                "Telecharge le terminal MT5 depuis l'espace client FTMO (pas\n"
                "celui de MetaQuotes : celui de FTMO est preconfigure sur leurs\n"
                "serveurs), installe-le, ouvre-le et connecte-toi.\n\n"
                "Puis relance, ou passe le chemin a la main :\n"
                '  python scripts\\preparer_ftmo.py --terminal "C:/.../terminal64.exe"')
    print("  retenu : %s" % terminal)
    print("  (%s)" % raison)

    if args.sans_ecrire:
        print("\n--sans-ecrire : la config n'est pas modifiee.")
    else:
        if ecrire_terminal(config, terminal):
            print("  ecrit dans %s (broker.terminal)" % config)
        else:
            return echouer("aucune ligne `terminal:` dans %s" % config)

    # -- 4. la connexion ---------------------------------------------------
    etape(4, "connexion au terminal")
    from quantbot import mt5broker
    from quantbot.config import Config
    cfg = Config.load(config)
    if not os.environ.get("MT5_PASSWORD") and cfg.get("broker.login"):
        print("  broker.login est renseigne mais MT5_PASSWORD est vide.")
        print("  On tente le compte DEJA OUVERT dans le terminal.")
    try:
        api = mt5broker.connecter(cfg, chemin_terminal=terminal)
    except Exception as exc:
        return echouer(
            "le terminal ne repond pas.",
            str(exc) + "\n\n"
            "Dans l'ordre : le terminal est-il OUVERT ? un compte est-il\n"
            "connecte (en bas a droite, pas « Pas de connexion ») ? et\n"
            "Outils > Options > Expert Advisors > « Autoriser le trading\n"
            "automatique » est-il coche ?")
    try:
        c = api.compte()
        print("  compte  : %s sur %s" % (c["account_number"], cfg.get("broker.serveur") or "?"))
        print("  devise  : %s   solde %.2f   equity %.2f"
              % (c["currency"], c["balance"], c["equity"]))
        print("  levier  : 1:%s" % c.get("leverage"))
        print("  mode    : %s" % ("SIMULATION / challenge" if api.simulation else "REEL"))
        if not api.simulation:
            print("\n  ATTENTION : ce compte est marque REEL par le terminal.")
            print("  Les comptes de challenge FTMO sont des comptes DEMO.")
            print("  Verifie quel compte est ouvert avant toute chose.")

        # -- 5. le catalogue -----------------------------------------------
        etape(5, "catalogue : nos tickers existent-ils chez FTMO ?")
        from quantbot.universe import get_universe
        voulus = get_universe(cfg)
        carte = api.construire_carte(voulus)
        absents = sorted(api.absents)
        print("  %d/%d trouve(s)" % (len(carte), len(voulus)))
        exemples = list(carte.items())[:6]
        for t, s in exemples:
            print("    %-12s -> %s" % (t, s))
        if absents:
            print("  %d absent(s), ecarte(s) sans planter :" % len(absents))
            print("    " + " ".join(absents))

        if len(carte) < 15:
            return echouer(
                "seulement %d ticker(s) negociables." % len(carte),
                "Sous 15 titres, un classement transversal n'est pas mesurable\n"
                "statistiquement : l'erreur-type d'un rho de Spearman sur 12\n"
                "noms est de 0,30, il faudrait un IC de 0,60 pour etre\n"
                "significatif. Verifie que le bon compte est ouvert, ou releve\n"
                "le catalogue reel :\n"
                "  python scripts\\ftmo_catalogue.py --ecrire config\\univers_ftmo.txt")

        print("\n" + "=" * 72)
        print("  PRET. Le terminal a repondu, %d ticker(s) negociables." % len(carte))
        print("=" * 72)
        print("\nLa suite, dans cet ordre :")
        print("  1. essai a blanc du coupe-circuit, sans rien envoyer")
        print("       python scripts\\coupe_circuit.py --a-blanc")
        print("  2. l'essai qui compte : force la coupure pour de vrai")
        print("       ouvre une petite position a la main dans MT5, mets")
        print("       defi.perte_jour_max a 0.001 dans config/ftmo.yaml, lance")
        print("       le coupe-circuit, et verifie que la position se ferme.")
        print("       Ce chemin n'a jamais tourne que contre un faux serveur.")
        print("  3. alors seulement, planifie la tache")
        print("       python scripts\\coupe_circuit.py --installer --minutes 10")
        print("\nN'installe pas la tache avant l'etape 2 : une tache qui")
        print("s'execute sans pouvoir solder donne l'illusion d'une protection.")
        return 0
    finally:
        api.fermer()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrompu.")
        sys.exit(1)
