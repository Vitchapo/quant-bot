#!/usr/bin/env python3
"""Fabrique l'archive du projet, et refuse de l'ecrire si elle fuit un secret.

    python scripts/archiver.py
    python scripts/archiver.py --sortie reports/quantbot.zip

Principe : liste BLANCHE. On nomme ce qui entre, jamais ce qui sort. Une liste
noire finit toujours par oublier quelque chose, et ce qu'elle oublierait ici
s'appelle secrets/alpaca.json.

Deux verifications sont faites APRES construction, sur le contenu reel de
l'archive et non sur ce qu'on croit y avoir mis :

1. aucune entree sous un chemin interdit ;
2. aucun fichier dont le contenu ressemble a une cle d'API.

Si l'une des deux echoue, l'archive est ecrite dans un fichier temporaire puis
SUPPRIMEE, et le script sort en erreur. Une archive douteuse ne doit jamais
exister assez longtemps pour etre envoyee par megarde.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

RACINE_ARCHIVE = "quantbot/"

FICHIERS = ["README.md", "pyproject.toml", "requirements.txt",
            "requirements-py38.txt", ".gitignore"]
DOSSIERS = ["config", "src", "scripts", "tests", ".git"]
EXTRAS = ["reports/dashboard_us.html"]

#: Jamais dans l'archive, a aucun niveau de l'arborescence.
INTERDITS = {"secrets", "data", "__pycache__", ".pytest_cache", ".venv", "venv",
             ".venv314", "node_modules"}

#: Ce qui ressemble a un identifiant, PAS a du code qui en manipule un.
#: La nuance compte : "APCA-API-SECRET-KEY" est un nom d'en-tete HTTP, il a
#: parfaitement sa place dans broker.py. Ce qu'on traque, c'est une VALEUR.
MOTIFS_SENSIBLES = [
    # une cle Alpaca telle qu'elle est reellement formee
    re.compile(rb"\b(PK|AK)[A-Z0-9]{16,}\b"),
    # un secret affecte a une variable ou a un champ, valeur entre guillemets
    re.compile(rb"APCA[_-]API[_-]SECRET[_-]KEY[\"']?\s*[:=]\s*[\"'][^\"']{16,}", re.I),
    re.compile(rb"ALPACA_SECRET_KEY[\"']?\s*[:=]\s*[\"'][^\"']{16,}", re.I),
    re.compile(rb"\bsecret_key\"\s*:\s*\"(?!colle-)[^\"]{16,}"),
]

NOTE = """ARCHIVE quantbot
================

Genereee le {date}.

Ce qui N'EST PAS dans cette archive, et pourquoi :

  secrets/    Les identifiants du courtier. Volontairement exclus : une archive
              circule, une cle secrete non. Recree le dossier avec le modele
              decrit dans le README, section "Connecter le bot a un courtier".

  data/       Les cours en cache (plusieurs centaines de Mo) et les donnees
              synthetiques. Entierement regenerable :

                  python scripts/fetch_data.py --config config/us.yaml --synthetic
                  python scripts/fetch_data.py --config config/us.yaml

Premiere mise en route sur une machine neuve :

    python -m venv .venv
    .venv\\Scripts\\Activate.ps1          (Windows)  |  source .venv/bin/activate
    pip install -r requirements.txt
    python -m pytest tests/ -q                       -> {n_tests} tests attendus
    python scripts/fetch_data.py   --config config/us.yaml --synthetic
    python scripts/run_backtest.py --config config/us.yaml --synthetic

Teste sur Python 3.8, 3.11 et 3.14 (avec pandas 3.0).
"""


def _autorise(chemin: Path) -> bool:
    return not any(part in INTERDITS for part in chemin.parts)


def _compter_tests() -> int:
    """Nombre de fonctions de test, pour que la note ne mente pas."""
    n = 0
    for p in Path("tests").rglob("test_*.py"):
        n += len(re.findall(r"^\s*def test_", p.read_text(encoding="utf-8"), re.M))
    return n


PREFIXE_TEMPORAIRE = "quantbot-archive-"


def _nettoyer(chemin) -> None:
    """Supprime le dossier temporaire de construction, et RIEN d'autre.

    Jusqu'au 24 septembre 2026, la boucle `for dossier in DOSSIERS` reutilisait
    le nom de la variable qui designait le dossier temporaire. Apres la boucle,
    elle valait ".git" - le dernier element de DOSSIERS - et
    rmtree(".git", ignore_errors=True) effacait le depot du projet, en silence,
    a chaque archive : donc a chaque passage de la suite de tests. Sous Windows,
    seuls les objets (fichiers en lecture seule) survivaient ; HEAD, config,
    refs et index disparaissaient. C'est arrive au moins le 22 septembre 2026
    a 23 h 22.

    Le garde rend cette classe d'erreur impossible : on ne supprime qu'un
    dossier cree par mkdtemp avec notre prefixe, sous le repertoire temporaire
    du systeme. Tout autre chemin est une erreur de programmation, et elle doit
    faire du bruit - c'est le silence d'ignore_errors qui l'a cachee.
    """
    p = Path(chemin).resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    if tmp not in p.parents or not p.name.startswith(PREFIXE_TEMPORAIRE):
        raise RuntimeError("refus de supprimer %s : ce n'est pas le dossier "
                           "temporaire de l'archive" % p)
    shutil.rmtree(p, ignore_errors=True)


def construire(sortie: Path) -> int:
    # L'archive est batie dans un dossier temporaire du systeme, verifiee la-bas,
    # et seulement ensuite COPIEE a destination. Deux raisons : une archive
    # douteuse n'atteint jamais le dossier de sortie, et la copie ecrase sans
    # avoir besoin de supprimer - ce que certains montages interdisent.
    temporaire = tempfile.mkdtemp(prefix=PREFIXE_TEMPORAIRE)
    provisoire = Path(temporaire) / "archive.zip"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(provisoire, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(RACINE_ARCHIVE + "LISEZ-MOI-ARCHIVE.txt",
                   NOTE.format(date=datetime.now().strftime("%Y-%m-%d %H:%M"),
                               n_tests=_compter_tests()))
        for nom in FICHIERS + EXTRAS:
            p = Path(nom)
            if p.exists():
                z.write(p, RACINE_ARCHIVE + p.as_posix())
                n += 1
        for dossier in DOSSIERS:
            if not Path(dossier).is_dir():
                continue
            for courant, sous_dossiers, fichiers in os.walk(dossier):
                sous_dossiers[:] = [d for d in sous_dossiers if d not in INTERDITS]
                for nom in fichiers:
                    p = Path(courant) / nom
                    if p.is_file() and _autorise(p):
                        z.write(p, RACINE_ARCHIVE + p.as_posix())
                        n += 1

    problemes = verifier(provisoire)
    if problemes:
        _nettoyer(temporaire)
        print("ARCHIVE REFUSEE - elle n'a pas ete ecrite :", file=sys.stderr)
        for p in problemes:
            print("  " + p, file=sys.stderr)
        return 1

    shutil.copyfile(provisoire, sortie)
    _nettoyer(temporaire)
    taille = sortie.stat().st_size
    print("%s  -  %d fichiers, %.2f Mo" % (sortie, n, taille / 1e6))
    print("Verifications passees : aucun chemin interdit, aucun motif de cle.")
    return 0


def verifier(archive: Path) -> list:
    """Relit l'archive construite. Renvoie la liste des problemes trouves."""
    problemes = []
    with zipfile.ZipFile(archive) as z:
        for nom in z.namelist():
            relatif = nom[len(RACINE_ARCHIVE):] if nom.startswith(RACINE_ARCHIVE) else nom
            if any(part in INTERDITS for part in Path(relatif).parts):
                problemes.append("chemin interdit : %s" % nom)
            if nom.endswith("/"):
                continue
            contenu = z.read(nom)
            for motif in MOTIFS_SENSIBLES:
                if motif.search(contenu):
                    problemes.append("motif sensible dans : %s" % nom)
                    break
    return problemes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sortie", help="chemin du zip (defaut : reports/quantbot-<date>.zip)")
    args = ap.parse_args()
    defaut = "reports/quantbot-%s.zip" % datetime.now().strftime("%Y%m%d-%H%M")
    return construire(Path(args.sortie or defaut))


if __name__ == "__main__":
    sys.exit(main())
