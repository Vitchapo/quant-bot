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

import pytest

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


# --- Le depot du projet ne doit jamais etre touche ------------------------------
#
# Regression du 24 septembre 2026 : l'archiveur effacait .git a chaque passage
# (voir _nettoyer dans archiver.py). Ces tests tournent dans un FAUX projet : si
# le defaut revient, c'est ce faux .git qui disparait, pas le vrai.

def _faux_projet(tmp_path: Path) -> Path:
    projet = tmp_path / "projet"
    for d in ("config", "src", "scripts", "tests"):
        (projet / d).mkdir(parents=True)
    (projet / "src" / "module.py").write_text("x = 1\n", encoding="utf-8")
    depot = projet / ".git"
    (depot / "refs" / "heads").mkdir(parents=True)
    (depot / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (depot / "config").write_text("[core]\n", encoding="utf-8")
    (depot / "refs" / "heads" / "main").write_text("0" * 40 + "\n", encoding="utf-8")
    return projet


def _etat(projet: Path) -> list:
    return sorted((p.relative_to(projet).as_posix(), p.read_bytes() if p.is_file() else b"")
                  for p in projet.rglob("*"))


def _archiver_dans(projet: Path, sortie: Path):
    return subprocess.run([sys.executable, str(ARCHIVER), "--sortie", str(sortie)],
                          cwd=str(projet), capture_output=True, text=True)


def test_une_archive_reussie_laisse_le_depot_intact(tmp_path):
    projet = _faux_projet(tmp_path)
    avant = _etat(projet)
    res = _archiver_dans(projet, tmp_path / "ok.zip")
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "ok.zip").exists()
    assert _etat(projet) == avant, "l'archiveur a modifie le projet"


def test_une_archive_refusee_laisse_le_depot_intact(tmp_path):
    """Le chemin du refus avait le meme defaut que celui du succes."""
    projet = _faux_projet(tmp_path)
    (projet / "config" / "piege.yaml").write_text(
        "cle: %s\n" % ("PK" + "ZZ1234567890ABCDEF"), encoding="utf-8")
    avant = _etat(projet)
    res = _archiver_dans(projet, tmp_path / "refus.zip")
    assert res.returncode == 1
    assert not (tmp_path / "refus.zip").exists()
    assert _etat(projet) == avant, "l'archiveur a modifie le projet"


def test_le_nettoyage_refuse_ce_qui_n_est_pas_son_dossier_temporaire(tmp_path, monkeypatch):
    sys.path.insert(0, str(RACINE / "scripts"))
    import archiver
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    for cible in (".git", str(tmp_path / ".git"), str(tmp_path)):
        with pytest.raises(RuntimeError):
            archiver._nettoyer(cible)
    assert (tmp_path / ".git").is_dir()
