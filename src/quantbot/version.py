"""Quelle version du code tourne ici, et depuis quand.

Trois questions, trois reponses distinctes :

* le CODE SUR DISQUE : dernier commit, sa date, son sujet, et les fichiers
  suivis modifies a la main depuis (la config du VPS a ete retouchee a la
  main le 02/10 : sans cette liste, la version affichee mentirait) ;
* la DATE D'INSTALLATION sur cette machine : le dernier mouvement de HEAD
  dans le reflog, et non la date du commit. Un commit de septembre peut tres
  bien n'avoir ete installe qu'en octobre ;
* le code CHARGE par un processus long. Le tableau de bord ne relit pas ses
  modules : apres une mise a jour, il tourne sur l'ancien code tant qu'on ne
  le redemarre pas. Le robot et la veille, eux, repartent du disque a chaque
  passage. C'est a l'appelant de garder la version lue a son demarrage.

Aucune exception ne sort d'ici : un depot illisible donne `ok: False` et une
raison, jamais une page en erreur.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]


def _git(args, racine):
    # safe.directory : sur le VPS, le depot appartient a ubuntu ; lu par un
    # autre utilisateur, git refuserait par securite et la version
    # disparaitrait sans bruit.
    r = subprocess.run(["git", "-c", "safe.directory=%s" % racine, "-C", str(racine)]
                       + list(args), capture_output=True, timeout=5)
    if r.returncode != 0:
        msg = (r.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(msg[:200] or "git a echoue (code %d)" % r.returncode)
    # rstrip et non strip : la premiere ligne de `git status --porcelain`
    # commence par un espace (" M fichier") qu'il faut garder.
    return r.stdout.decode("utf-8", "replace").rstrip()


def lire(racine=None) -> dict:
    """Le code sur disque : commit, date du commit, sujet, date d'installation
    ici (reflog), fichiers suivis modifies depuis le commit."""
    racine = Path(racine) if racine else RACINE
    out = {"ok": False, "raison": "", "commit": None, "date_commit": None,
           "sujet": None, "installe_le": None, "modifs": []}
    try:
        h, d, s = _git(["log", "-1", "--format=%h%x1f%cI%x1f%s"], racine).split("\x1f", 2)
        out.update(commit=h, date_commit=d, sujet=s)
    except FileNotFoundError:
        out["raison"] = "git n'est pas installe sur cette machine"
        return out
    except Exception as exc:
        out["raison"] = "version illisible : %s" % str(exc)[:200]
        return out
    try:
        brut = _git(["reflog", "-1", "--date=iso-strict", "--format=%gd"], racine)
        if "{" in brut and brut.endswith("}"):
            out["installe_le"] = brut[brut.index("{") + 1:-1]
    except Exception:
        pass
    try:
        lignes = _git(["status", "--porcelain", "--untracked-files=no"], racine).splitlines()
        out["modifs"] = sorted(l[3:].strip() for l in lignes if len(l) > 3)
    except Exception:
        pass
    out["ok"] = True
    return out
