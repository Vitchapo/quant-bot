"""La discipline du robot, verifiee sans courtier ni reseau.

`decider` est une fonction pure : c'est deliberе. Un programme qui envoie des
ordres pendant que personne ne regarde doit avoir une logique qu'on peut
inspecter ligne a ligne, sans monter un faux serveur pour chaque cas.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import robot  # noqa: E402

SIGNAL = pd.Timestamp("2026-09-30")


class TestDecision:
    def test_sans_signal_il_ne_se_passe_rien(self):
        assert robot.decider({}, None, 0)[0] == robot.AUCUN

    def test_le_jour_du_signal_on_attend_le_lendemain(self):
        """La strategie mesuree execute au lendemain du signal. Agir le jour
        meme serait une autre strategie."""
        assert robot.decider({}, SIGNAL, 0)[0] == robot.ATTENDRE

    def test_le_lendemain_on_execute(self):
        assert robot.decider({}, SIGNAL, 1)[0] == robot.EXECUTER

    def test_un_signal_deja_traite_ne_l_est_jamais_deux_fois(self):
        """Dix lancements le meme jour ne doivent produire qu'un rebalancement."""
        etat = {"dernier_signal": "2026-09-30", "dernier_passage": "2026-10-01T21:00:00"}
        for seances in (1, 2, 3, 9):
            assert robot.decider(etat, SIGNAL, seances)[0] == robot.DEJA_FAIT

    def test_un_signal_trop_ancien_est_saute(self):
        assert robot.decider({}, SIGNAL, 4)[0] == robot.TROP_TARD
        assert robot.decider({}, SIGNAL, 40)[0] == robot.TROP_TARD

    def test_la_fenetre_de_rattrapage_est_reglable(self):
        assert robot.decider({}, SIGNAL, 5, rattrapage=7)[0] == robot.EXECUTER
        assert robot.decider({}, SIGNAL, 5, rattrapage=2)[0] == robot.TROP_TARD

    def test_un_signal_different_relance_le_cycle(self):
        """Le mois suivant, le meme etat ne doit plus bloquer."""
        etat = {"dernier_signal": "2026-08-31"}
        assert robot.decider(etat, SIGNAL, 1)[0] == robot.EXECUTER

    def test_chaque_decision_porte_une_raison_lisible(self):
        for signal, seances in ((None, 0), (SIGNAL, 0), (SIGNAL, 1), (SIGNAL, 99)):
            _, raison = robot.decider({}, signal, seances)
            assert raison and len(raison) > 10


class TestEtatSurDisque:
    def test_l_etat_survit_d_un_lancement_a_l_autre(self, tmp_path):
        chemin = tmp_path / "etat.json"
        assert robot._lire_etat(chemin) == {}
        robot._ecrire_etat({"dernier_signal": "2026-09-30"}, chemin)
        assert robot._lire_etat(chemin)["dernier_signal"] == "2026-09-30"

    def test_un_etat_illisible_ne_fait_pas_planter_le_robot(self, tmp_path):
        """Mieux vaut repartir de zero que de refuser de demarrer."""
        chemin = tmp_path / "etat.json"
        chemin.write_text("{ ceci n'est pas du json", encoding="utf-8")
        assert robot._lire_etat(chemin) == {}


class TestAucunAccesALArgentReel:
    def test_le_robot_n_expose_aucune_option_reelle(self):
        """Limite deliberee : pas d'ordres reels sans humain devant l'ecran.

        On inspecte le CODE, pas la documentation - le docstring a le droit
        d'expliquer pourquoi l'option n'existe pas."""
        import re
        source = Path(robot.__file__).read_text(encoding="utf-8")
        code = "\n".join(l for l in source.splitlines() if not l.strip().startswith("#"))
        code = re.sub(r'"""..*?"""', "", code, flags=re.S)
        assert 'add_argument("--reel"' not in code
        assert "reel=True" not in code

    def test_lancer_le_robot_avec_reel_est_refuse(self, capsys):
        import subprocess
        r = subprocess.run([sys.executable, robot.__file__, "--reel"],
                           capture_output=True, text=True)
        assert r.returncode != 0
        assert "unrecognized arguments" in (r.stderr + r.stdout)

    def test_il_passe_toujours_par_le_compte_de_simulation(self):
        from quantbot.operations import Operations
        import inspect
        assert "reel=False" in inspect.getsource(Operations.api.fget)
