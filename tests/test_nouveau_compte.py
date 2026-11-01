"""Repartir sur un compte papier neuf : le menage local, sans courtier.

Le piege corrige le 01/11/2026 : le script VIDAIT data/defi_etat.json. Or le
defi lit un etat vide comme illisible, donc comme un VERROU. Le compte neuf
serait reste bloque, sans un achat. On verifie qu'apres le menage le defi
s'ouvre neuf, et que rien ne s'est perdu en route.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import nouveau_compte as N  # noqa: E402
from quantbot import defi  # noqa: E402

CONFIG = str(Path(__file__).resolve().parent.parent / "config" / "us.yaml")


def _ancien_compte(racine: Path) -> dict:
    data = racine / "data"
    data.mkdir(parents=True)
    contenus = {
        "defi_etat.json": json.dumps({"capital_depart": 100000.0, "plus_haut": 105503.52,
                                      "a_solder": False,
                                      "verrou": {"raison": "perte", "depuis": "2026-10-29",
                                                 "equity": 90416.36, "detail": "perte"}}),
        "robot_etat.json": json.dumps({"dernier_signal": "2026-10-23"}),
        "journal_ordres.csv": "horodatage,compte,ticker\n2026-10-30T14:35:01,PA0000TEST,AMD\n",
        "robot_journal.txt": "compte rendu\n",
    }
    for nom, texte in contenus.items():
        (data / nom).write_text(texte, encoding="utf-8")
    return contenus


def test_le_menage_ouvre_un_defi_neuf_sans_rien_perdre(tmp_path, monkeypatch):
    contenus = _ancien_compte(tmp_path)
    monkeypatch.setattr(N, "RACINE", tmp_path)
    data = tmp_path / "data"
    assert N.main(["--config", CONFIG, "--sans-courtier"]) == 0       # simulation : rien ne bouge
    assert (data / "defi_etat.json").read_text(encoding="utf-8") == contenus["defi_etat.json"]
    assert N.main(["--config", CONFIG, "--sans-courtier", "--appliquer"]) == 0
    # le defi et le robot repartent sans memoire : ni verrou, ni dernier signal
    assert not (data / "defi_etat.json").exists() and not (data / "robot_etat.json").exists()
    etat = defi.lire_etat(data / "defi_etat.json")
    assert etat == {} and not defi.est_verrouille(etat)
    assert (data / "journal_ordres.csv").read_text(encoding="utf-8") == "horodatage,compte,ticker\n"
    assert (data / "robot_journal.txt").read_text(encoding="utf-8") == ""
    # et tout est archive, octet pour octet
    archives = list((data / "archives").iterdir())
    assert len(archives) == 1
    for nom, texte in contenus.items():
        assert (archives[0] / nom).read_text(encoding="utf-8") == texte


def test_si_la_suppression_est_refusee_l_etat_reste_lisible(tmp_path, monkeypatch):
    """Un montage qui refuse la suppression : un objet vide, jamais un fichier vide."""
    f = tmp_path / "defi_etat.json"
    f.write_text(json.dumps({"capital_depart": 100000.0, "verrou": {"raison": "perte"}}))

    def refuse(self, *args, **kwargs):
        raise PermissionError("suppression refusee")

    monkeypatch.setattr(Path, "unlink", refuse)
    assert N.vider(f) == "remis a {}"
    etat = defi.lire_etat(f)
    assert etat == {} and not defi.est_verrouille(etat)
