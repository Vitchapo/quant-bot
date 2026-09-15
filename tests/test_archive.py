"""L'archive ne doit jamais pouvoir emporter un identifiant.

Le test qui compte est le dernier : on plante volontairement une fausse cle
dans un fichier inclus, et on verifie que le script REFUSE de produire
l'archive. Un garde-fou qu'on n'a jamais vu se declencher n'est pas un
garde-fou, c'est une intention.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
ARCHIVER = RACINE / "scripts" / "archiver.py"


def _construire(destination: Path):
    return subprocess.run([sys.executable, str(ARCHIVER), "--sortie", str(destination)],
                          cwd=str(RACINE), capture_output=True, text=True)


def test_l_archive_se_construit(tmp_path):
    cible = tmp_path / "a.zip"
    res = _construire(cible)
    assert res.returncode == 0, res.stderr
    assert cible.exists()
    with zipfile.ZipFile(cible) as z:
        noms = z.namelist()
    assert any(n.endswith("src/quantbot/backtest.py") for n in noms)
    assert any(n.endswith("LISEZ-MOI-ARCHIVE.txt") for n in noms)


def test_ni_secrets_ni_donnees_ni_caches(tmp_path):
    cible = tmp_path / "b.zip"
    assert _construire(cible).returncode == 0
    with zipfile.ZipFile(cible) as z:
        noms = z.namelist()
    for interdit in ("secrets/", "data/", "__pycache__", ".pytest_cache"):
        assert not any(interdit in n for n in noms), interdit


def test_une_cle_plantee_fait_echouer_la_construction(tmp_path):
    """Le garde-fou doit se declencher pour de vrai."""
    # La fausse cle est ASSEMBLEE a l'execution : ecrite en clair, elle ferait
    # echouer la construction de l'archive a cause de ce fichier de test
    # lui-meme, et le test ne prouverait plus rien.
    fausse_cle = "PK" + "ZZ1234567890ABCDEF"
    piege = RACINE / "config" / "_piege_test.yaml"
    piege.write_text("cle: %s\n" % fausse_cle, encoding="utf-8")
    try:
        cible = tmp_path / "c.zip"
        res = _construire(cible)
        assert res.returncode == 1, "l'archive aurait du etre refusee"
        assert "motif sensible" in res.stderr
        assert not cible.exists(), "aucune archive douteuse ne doit subsister"
    finally:
        piege.write_text("", encoding="utf-8")   # vide : le dossier est en lecture seule
