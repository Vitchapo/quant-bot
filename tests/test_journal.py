"""Integrite du journal d'ordres.

Le journal est la memoire du bot : c'est sur lui que reposent l'analyse
d'execution, la reconciliation et tout jugement posterieur sur ce qui s'est
reellement passe. Un journal illisible ne provoque aucune erreur au moment de
l'ecriture - il rend seulement toutes les analyses ulterieures impossibles.

Bug reellement survenu : `journaliser` n'ecrivait l'entete que si le fichier
n'existait pas. Le passage de 10 a 14 colonnes a donc laisse l'ancien entete
en place et empile des lignes a 14 champs derriere. `pandas` refusait
d'ouvrir le fichier, silencieusement, depuis des semaines.
"""
from __future__ import annotations

import csv

import pandas as pd
import pytest

from quantbot.operations import COLONNES, entete, journaliser, migrer_journal


def ligne(ticker="AAPL", **kw):
    base = {c: "" for c in COLONNES}
    base.update({"horodatage": "2026-09-01T10:00:00", "compte": "PA1",
                 "mode": "simulation", "ticker": ticker, "sens": "buy",
                 "montant": "100.0", "statut": "accepted"})
    base.update(kw)
    return base


class TestEcriture:
    def test_cree_le_fichier_avec_entete(self, tmp_path):
        chemin = tmp_path / "j.csv"
        journaliser([ligne()], chemin)
        assert entete(chemin) == COLONNES
        assert len(pd.read_csv(chemin)) == 1

    def test_ajout_successif(self, tmp_path):
        chemin = tmp_path / "j.csv"
        journaliser([ligne("AAPL")], chemin)
        journaliser([ligne("MSFT"), ligne("NVDA")], chemin)
        df = pd.read_csv(chemin)
        assert list(df["ticker"]) == ["AAPL", "MSFT", "NVDA"]

    def test_fichier_vide_recoit_un_entete(self, tmp_path):
        chemin = tmp_path / "j.csv"
        chemin.write_text("", encoding="utf-8")
        journaliser([ligne()], chemin)
        assert entete(chemin) == COLONNES


class TestMigration:
    """Le scenario exact qui a corrompu le journal de production."""

    def _journal_ancien_format(self, chemin, anciennes):
        with chemin.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=anciennes)
            w.writeheader()
            w.writerow({c: c.upper() for c in anciennes})
            w.writerow({c: c.lower() for c in anciennes})

    def test_ecriture_apres_evolution_des_colonnes(self, tmp_path):
        """C'EST le test qui manquait. Ecrire dans un journal a l'ancien
        format doit produire un fichier lisible, pas un fichier mixte."""
        chemin = tmp_path / "j.csv"
        anciennes = ["horodatage", "mode", "ticker", "sens", "quantite",
                     "montant", "cours", "motif", "statut", "id_courtier"]
        self._journal_ancien_format(chemin, anciennes)

        journaliser([ligne("NVDA")], chemin)

        assert entete(chemin) == COLONNES
        df = pd.read_csv(chemin)          # echouait avant le correctif
        assert len(df) == 3
        assert "NVDA" in set(df["ticker"])

    def test_aucune_ligne_perdue(self, tmp_path):
        chemin = tmp_path / "j.csv"
        anciennes = ["horodatage", "mode", "ticker", "sens", "quantite",
                     "montant", "cours", "motif", "statut", "id_courtier"]
        self._journal_ancien_format(chemin, anciennes)
        r = migrer_journal(chemin)
        assert r["migre"] is True
        assert r["lignes"] == 2 and r["anciennes"] == 2 and r["illisibles"] == 0

    def test_les_valeurs_suivent_leur_colonne(self, tmp_path):
        """Une migration qui decale les valeurs serait pire que la corruption."""
        chemin = tmp_path / "j.csv"
        anciennes = ["horodatage", "mode", "ticker", "sens", "quantite",
                     "montant", "cours", "motif", "statut", "id_courtier"]
        with chemin.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=anciennes)
            w.writeheader()
            w.writerow({"horodatage": "2026-01-02T09:00:00", "mode": "simulation",
                        "ticker": "TSLA", "sens": "sell", "quantite": "3",
                        "montant": "750.5", "cours": "250.17", "motif": "sortie",
                        "statut": "accepted", "id_courtier": "abc-123"})
        migrer_journal(chemin)
        d = pd.read_csv(chemin).iloc[0]
        assert d["ticker"] == "TSLA" and d["sens"] == "sell"
        assert d["montant"] == 750.5 and d["cours"] == 250.17
        assert d["id_courtier"] == "abc-123" and d["motif"] == "sortie"
        assert pd.isna(d["compte"])       # colonne nouvelle : vide, pas inventee

    def test_sauvegarde_ecrite_avant_reecriture(self, tmp_path):
        chemin = tmp_path / "j.csv"
        anciennes = ["horodatage", "mode", "ticker", "sens", "quantite",
                     "montant", "cours", "motif", "statut", "id_courtier"]
        self._journal_ancien_format(chemin, anciennes)
        avant = chemin.read_bytes()
        r = migrer_journal(chemin)
        assert (tmp_path / "j.csv.avant-migration").read_bytes() == avant
        assert r["sauvegarde"].endswith(".avant-migration")

    def test_journal_deja_au_bon_format_intouche(self, tmp_path):
        chemin = tmp_path / "j.csv"
        journaliser([ligne()], chemin)
        avant = chemin.read_bytes()
        r = migrer_journal(chemin)
        assert r["migre"] is False
        assert chemin.read_bytes() == avant
        assert not (tmp_path / "j.csv.avant-migration").exists()

    def test_fichier_inexistant(self, tmp_path):
        r = migrer_journal(tmp_path / "absent.csv")
        assert r["migre"] is False and r["lignes"] == 0

    def test_ligne_de_largeur_inconnue_signalee_et_conservee(self, tmp_path):
        """On ne devine jamais : la ligne est comptee comme illisible et
        reste dans la sauvegarde."""
        chemin = tmp_path / "j.csv"
        anciennes = ["horodatage", "mode", "ticker"]
        with chemin.open("w", newline="", encoding="utf-8") as fh:
            fh.write(",".join(anciennes) + "\n")
            fh.write("2026-01-01,simulation,AAPL\n")
            fh.write("a,b,c,d,e,f,g\n")
        r = migrer_journal(chemin)
        assert r["anciennes"] == 1 and r["illisibles"] == 1
        assert "a,b,c,d,e,f,g" in (tmp_path / "j.csv.avant-migration").read_text()


class TestJournalDeProduction:
    def test_le_journal_reel_est_lisible(self):
        """Garde-fou sur le vrai fichier, s'il est present."""
        from pathlib import Path
        chemin = Path(__file__).resolve().parent.parent / "data" / "journal_ordres.csv"
        if not chemin.exists():
            pytest.skip("pas de journal de production dans cet environnement")
        df = pd.read_csv(chemin)
        assert list(df.columns) == COLONNES
        assert len(df) > 0
        assert df["compte"].notna().all(), "des ordres sans compte identifie"
