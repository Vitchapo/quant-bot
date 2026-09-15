"""Mesure du glissement : le signe doit toujours dire "ce que ca m'a coute"."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "scripts"))


@pytest.fixture
def cfg_sans_cache(base_config):
    """Config dont le cache de cours n'existe pas : la comparaison a la
    cloture du jour est alors indisponible, ce qui est le cas qu'on teste."""
    return base_config.with_overrides({"data.cache_dir": "data/_cache_absent_"})


class FauxCourtier:
    """Renvoie des executions decidees a l'avance, sans reseau."""

    def __init__(self, prix):
        self.prix = prix

    def horloge(self):
        return {"timestamp": "2026-09-08T14:59:00-04:00", "is_open": True}

    def ordre(self, identifiant):
        p = self.prix[identifiant]
        if p is None:
            return {"status": "canceled"}
        return {"status": "filled", "filled_avg_price": str(p), "filled_qty": "10",
                "filled_at": "2026-09-08T13:31:02Z"}


def _ligne(ticker, sens, cours, ident):
    return {"horodatage": "2026-09-08T09:00:00", "mode": "simulation", "ticker": ticker,
            "sens": sens, "quantite": "10", "montant": str(10 * cours), "cours": str(cours),
            "motif": "entree", "statut": "accepted", "id_courtier": ident}


def test_payer_plus_cher_a_l_achat_est_un_cout(cfg_sans_cache, capsys):
    import verifier_executions as ve
    api = FauxCourtier({"o1": 101.0})           # prevu 100, obtenu 101 : +100 bps
    assert ve.analyser(cfg_sans_cache, api, [_ligne("AAA", "buy", 100.0, "o1")]) == 0
    sortie = capsys.readouterr().out
    assert "+100.0" in sortie


def test_encaisser_moins_a_la_vente_est_aussi_un_cout(cfg_sans_cache, capsys):
    import verifier_executions as ve
    api = FauxCourtier({"o1": 99.0})            # prevu 100, vendu 99 : +100 bps de cout
    assert ve.analyser(cfg_sans_cache, api, [_ligne("AAA", "sell", 100.0, "o1")]) == 0
    assert "+100.0" in capsys.readouterr().out


def test_un_prix_favorable_donne_un_glissement_negatif(cfg_sans_cache, capsys):
    import verifier_executions as ve
    api = FauxCourtier({"o1": 99.0})            # achete moins cher que prevu
    ve.analyser(cfg_sans_cache, api, [_ligne("AAA", "buy", 100.0, "o1")])
    assert "-100.0" in capsys.readouterr().out


def test_un_ordre_non_execute_est_signale_pas_compte(cfg_sans_cache, capsys):
    import verifier_executions as ve
    api = FauxCourtier({"o1": 101.0, "o2": None})
    ve.analyser(cfg_sans_cache, api, [_ligne("AAA", "buy", 100.0, "o1"),
                                   _ligne("BBB", "buy", 100.0, "o2")])
    sortie = capsys.readouterr().out
    assert "1 ordres remplis" in sortie or "(1 ordres remplis)" in sortie
    assert "canceled" in sortie


def test_sans_aucune_execution_le_script_signale_l_echec(cfg_sans_cache, capsys):
    import verifier_executions as ve
    api = FauxCourtier({"o1": None})
    assert ve.analyser(cfg_sans_cache, api, [_ligne("AAA", "buy", 100.0, "o1")]) == 1


def test_le_cours_du_plan_ne_sert_jamais_a_regler_les_frais(cfg_sans_cache, capsys):
    """Le defaut qui a produit un mauvais conseil : appeler "glissement" un
    ecart de plusieurs seances, et recommander de remonter les frais du
    backtest de 70 points de base sur cette base."""
    import verifier_executions as ve
    ligne = _ligne("AAA", "buy", 100.0, "o1")
    ligne["date_cours"] = "2026-09-04"
    ve.analyser(cfg_sans_cache, FauxCourtier({"o1": 112.0}), [ligne])
    sortie = capsys.readouterr().out
    assert "ne doit jamais servir a regler" in sortie
    assert "remonte execution.slippage_bps a environ" not in sortie


def test_sans_fourchette_relevee_aucun_chiffre_de_cout_n_est_donne(cfg_sans_cache, capsys):
    import verifier_executions as ve
    ve.analyser(cfg_sans_cache, FauxCourtier({"o1": 100.5}), [_ligne("AAA", "buy", 100.0, "o1")])
    sortie = capsys.readouterr().out
    assert "Indisponible pour ces ordres" in sortie
    assert "seule reference contemporaine" in sortie


def test_avec_la_fourchette_le_cout_devient_mesurable(cfg_sans_cache, capsys):
    """Reference relevee a l'envoi : 100,00. Rempli a 100,20 -> 20 bps."""
    import verifier_executions as ve
    ligne = _ligne("AAA", "buy", 100.0, "o1")
    ligne["cours_marche"] = "100.00"
    ligne["fourchette_bps"] = "8.0"
    ve.analyser(cfg_sans_cache, FauxCourtier({"o1": 100.20}), [ligne])
    sortie = capsys.readouterr().out
    assert "+20.0" in sortie
    assert "remonte execution.slippage_bps a environ 20 bps" in sortie


def test_un_cout_negatif_est_signale_comme_impossible(cfg_sans_cache, capsys):
    """Obtenir systematiquement mieux que la fourchette serait de l'argent
    gratuit : c'est une mesure faussee, pas une bonne execution."""
    import verifier_executions as ve
    ligne = _ligne("AAA", "buy", 100.0, "o1")
    ligne["cours_marche"] = "100.00"
    ve.analyser(cfg_sans_cache, FauxCourtier({"o1": 99.0}), [ligne])
    sortie = capsys.readouterr().out
    assert "cout d'execution NEGATIF n'existe pas" in sortie
    assert "l'hypothese du backtest tient" not in sortie


def test_une_seance_en_cours_fait_refuser_la_comparaison_a_la_cloture():
    """Avant la cloture, la barre du jour n'est qu'un instantane.

    Le test n'importe pas `zoneinfo` (absent en Python 3.8) : il interroge la
    fonction, qui gere elle-meme ce cas en tranchant dans le sens prudent.
    """
    from datetime import datetime, timezone
    from quantbot.executions import barre_etablie
    assert barre_etablie("2020-01-02") is True, "une seance ancienne est etablie"
    assert barre_etablie("2099-01-01") is False, "le futur n'est jamais etabli"
    # avec l'horloge du courtier, plus aucune ambiguite d'heure
    assert barre_etablie("2026-09-08", "2026-09-08T14:59:00-04:00") is False, "seance en cours"
    assert barre_etablie("2026-09-08", "2026-09-08T16:31:00-04:00") is True, "seance finie"
    assert barre_etablie("2026-09-05", "2026-09-08T09:31:00-04:00") is True, "veille"
    assert barre_etablie("pas une date") is False
