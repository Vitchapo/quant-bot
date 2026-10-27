"""La version affichee doit dire la verite : commit, installation, retouches."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from quantbot import version

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git absent")


def _git(depot, *args):
    subprocess.run(["git", "-C", str(depot)] + list(args), check=True, capture_output=True)


@pytest.fixture
def depot(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "config.yaml").write_text("objectif: 0.10\n", encoding="utf-8")
    _git(tmp_path, "add", "config.yaml")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m",
         "premier commit : le defi vise 10 %")
    return tmp_path


def test_le_commit_et_son_sujet(depot):
    v = version.lire(depot)
    attendu = subprocess.run(["git", "-C", str(depot), "log", "-1", "--format=%h"],
                             capture_output=True, text=True).stdout.strip()
    assert v["ok"] and v["commit"] == attendu
    assert v["sujet"] == "premier commit : le defi vise 10 %"
    assert v["date_commit"] and v["installe_le"]
    assert v["modifs"] == []


def test_une_retouche_a_la_main_se_voit(depot):
    """Le VPS a eu sa config retouchee a la main : la version affichee ne doit
    pas pretendre que le code est celui du commit."""
    (depot / "config.yaml").write_text("objectif: 0.08\n", encoding="utf-8")
    assert version.lire(depot)["modifs"] == ["config.yaml"]


def test_hors_depot_une_raison_pas_une_exception(tmp_path):
    v = version.lire(tmp_path / "pas-un-depot")
    assert v["ok"] is False and v["raison"]
