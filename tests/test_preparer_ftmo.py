"""La preparation FTMO : detection du terminal et ecriture de la config.

Les parties Windows (registre, Program Files, connexion MT5) ne sont pas
testables ici. Mais les deux qui peuvent casser en silence le sont : le CHOIX
du terminal quand plusieurs coexistent, et l'ECRITURE dans le YAML.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import preparer_ftmo as pf  # noqa: E402


class TestChoixDuTerminal:
    """Plusieurs MT5 coexistent des qu'on a essaye deux prop firms. Se tromper
    de terminal, c'est se connecter au mauvais compte - et ca ne se voit pas
    dans un journal."""

    def test_aucun_terminal(self):
        t, raison = pf.choisir_terminal([])
        assert t is None and "aucun" in raison

    def test_un_seul_qui_mentionne_ftmo(self):
        t, _ = pf.choisir_terminal([r"C:\Program Files\FTMO MetaTrader 5\terminal64.exe"])
        assert "FTMO" in t

    def test_ftmo_prefere_quand_plusieurs_terminaux(self):
        t, raison = pf.choisir_terminal([
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
            r"C:\Program Files\FTMO MetaTrader 5\terminal64.exe",
            r"C:\Program Files\FundedNext MT5\terminal64.exe",
        ])
        assert "FTMO" in t and "seul" in raison

    def test_DEUX_ftmo_ne_sont_PAS_devines(self):
        """Deviner entre deux comptes FTMO serait pire que refuser : le bot
        pourrait se connecter au compte finance au lieu du challenge."""
        t, raison = pf.choisir_terminal([
            r"C:\Program Files\FTMO MT5 challenge\terminal64.exe",
            r"C:\Program Files\FTMO MT5 finance\terminal64.exe",
        ])
        assert t is None
        assert "--terminal" in raison

    def test_un_seul_terminal_sans_ftmo_est_retenu_mais_signale(self):
        t, raison = pf.choisir_terminal([r"C:\Program Files\MetaTrader 5\terminal64.exe"])
        assert t is not None
        assert "ne mentionne pas FTMO" in raison

    def test_plusieurs_sans_ftmo_ne_sont_pas_devines(self):
        t, raison = pf.choisir_terminal([
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
            r"C:\Program Files\Autre MT5\terminal64.exe",
        ])
        assert t is None and "--terminal" in raison

    def test_la_casse_du_chemin_est_ignoree(self):
        t, _ = pf.choisir_terminal([
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
            r"C:\Program Files\ftmo mt5\terminal64.exe",
        ])
        assert "ftmo" in t.lower()

    def test_la_recherche_est_injectable(self):
        """`lister` existe pour que ce chemin soit testable hors Windows."""
        faux = {r"C:\Program Files": [Path(r"C:\Program Files\FTMO MT5\terminal64.exe")],
                r"C:\Program Files (x86)": []}
        out = pf.candidats_terminaux(lister=lambda base: faux.get(base, []))
        assert len(out) == 1 and "FTMO" in out[0]


class TestEcritureDansLaConfig:
    MODELE = """broker:
  provider: mt5
  allow_live: false
  # Chemin du terminal. Laisse vide si un seul MT5 est installe.
  terminal: null               # ex : "C:/.../terminal64.exe"
  login: null
"""

    def test_la_valeur_est_ecrite(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text(self.MODELE, encoding="utf-8")
        assert pf.ecrire_terminal(f, r"C:\Program Files\FTMO MT5\terminal64.exe")
        assert 'terminal: "C:/Program Files/FTMO MT5/terminal64.exe"' in f.read_text(encoding="utf-8")

    def test_LES_ANTISLASH_SONT_CONVERTIS(self, tmp_path):
        """`"C:\\Program Files\\..."` dans une chaine YAML entre guillemets fait
        de `\\P` une sequence d'echappement invalide : le fichier ne se charge
        plus du tout. C'est une panne totale pour un caractere."""
        import yaml
        f = tmp_path / "c.yaml"
        f.write_text(self.MODELE, encoding="utf-8")
        pf.ecrire_terminal(f, r"C:\Program Files\FTMO MT5\terminal64.exe")
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert "\\" not in d["broker"]["terminal"]
        assert d["broker"]["terminal"].endswith("terminal64.exe")

    def test_les_commentaires_survivent(self, tmp_path):
        """Un aller-retour par pyyaml les perdrait tous - et ils sont la
        moitie de la valeur de ce fichier."""
        f = tmp_path / "c.yaml"
        f.write_text(self.MODELE, encoding="utf-8")
        pf.ecrire_terminal(f, "C:/x/terminal64.exe")
        t = f.read_text(encoding="utf-8")
        assert "# Chemin du terminal" in t
        assert "provider: mt5" in t and "login: null" in t

    def test_l_indentation_est_conservee(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text(self.MODELE, encoding="utf-8")
        pf.ecrire_terminal(f, "C:/x/terminal64.exe")
        ligne = [l for l in f.read_text(encoding="utf-8").splitlines()
                 if "terminal:" in l][0]
        assert ligne.startswith("  terminal:")

    def test_une_seconde_ecriture_remplace_sans_dupliquer(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text(self.MODELE, encoding="utf-8")
        pf.ecrire_terminal(f, "C:/a/terminal64.exe")
        pf.ecrire_terminal(f, "C:/b/terminal64.exe")
        t = f.read_text(encoding="utf-8")
        assert t.count("terminal:") == 1
        assert "C:/b/" in t and "C:/a/" not in t

    def test_sans_ligne_terminal_on_renvoie_faux(self, tmp_path):
        f = tmp_path / "c.yaml"
        f.write_text("broker:\n  provider: mt5\n", encoding="utf-8")
        assert pf.ecrire_terminal(f, "C:/x/terminal64.exe") is False

    def test_la_vraie_config_ftmo_a_bien_la_ligne(self):
        """Le test qui attrape un renommage de cle : si `terminal:` disparait
        de config/ftmo.yaml, la preparation echouerait a l'etape 3."""
        f = Path(__file__).resolve().parent.parent / "config" / "ftmo.yaml"
        assert any(l.strip().startswith("terminal:")
                   for l in f.read_text(encoding="utf-8").splitlines())


class TestLeBatchExiste:
    def test_le_bat_est_present_et_en_crlf(self):
        """Un .bat en fins de ligne Unix se comporte de facon imprevisible
        sous cmd.exe."""
        f = Path(__file__).resolve().parent.parent / "preparer_ftmo.bat"
        assert f.exists()
        brut = f.read_bytes()
        assert b"\r\n" in brut
        assert brut.count(b"\n") == brut.count(b"\r\n"), "des LF sans CR"

    def test_le_bat_appelle_bien_le_script(self):
        f = Path(__file__).resolve().parent.parent / "preparer_ftmo.bat"
        t = f.read_text(encoding="ascii", errors="replace")
        assert "preparer_ftmo.py" in t
        assert "pause" in t, "sans pause, un double-clic ferme la fenetre"

    def test_le_bat_verifie_les_64_bits(self):
        f = Path(__file__).resolve().parent.parent / "preparer_ftmo.bat"
        assert "calcsize" in f.read_text(encoding="ascii", errors="replace")
