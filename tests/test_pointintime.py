"""Reconstruction de l'appartenance historique a l'indice.

Toute la logique est testee HORS RESEAU sur une table de changements
synthetique : le comportement de la reconstruction ne doit dependre ni de
Wikipedia ni du moment ou le test tourne.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantbot.pointintime import (Appartenance, PointInTimeError,
                                  normaliser_changements, normaliser_ticker,
                                  reconstruire)


def table(lignes):
    return pd.DataFrame(lignes, columns=["date", "ajoute", "retire", "motif"])


@pytest.fixture
def histoire():
    """Indice a 3 places.

    2020-01-01 : D entre, A sort   -> avant : {A,B,C} / apres : {B,C,D}
    2022-01-01 : E entre, B sort   -> apres : {C,D,E}
    Composition actuelle : {C, D, E}
    """
    changements = table([
        ("2020-01-01", "D", "A", "remplacement"),
        ("2022-01-01", "E", "B", "remplacement"),
    ])
    return reconstruire(["C", "D", "E"], changements)


class TestReconstruction:
    def test_composition_a_chaque_epoque(self, histoire):
        assert histoire.membres("2019-06-01") == {"A", "B", "C"}
        assert histoire.membres("2021-06-01") == {"B", "C", "D"}
        assert histoire.membres("2023-06-01") == {"C", "D", "E"}

    def test_le_jour_du_changement_compte_pour_apres(self, histoire):
        """Un titre entre le 1er janvier EST membre le 1er janvier."""
        assert "D" in histoire.membres("2020-01-01")
        assert "A" not in histoire.membres("2020-01-01")
        assert "A" in histoire.membres("2019-12-31")

    def test_avant_le_premier_changement_connu(self, histoire):
        """On prolonge la plus ancienne composition reconstituee, faute de
        mieux - et `qualite()` dit a partir de quand c'est douteux."""
        assert histoire.membres("1990-01-01") == {"A", "B", "C"}

    def test_effectif_constant_quand_les_changements_sont_apparies(self, histoire):
        assert set(histoire.effectif().to_numpy()) == {3}

    def test_entrant_inconnu_de_la_composition_actuelle(self):
        """X est entre en 2015 puis ressorti en 2018 : il ne figure pas dans
        la liste d'aujourd'hui. Le retirer a rebours ne doit pas planter."""
        changements = table([
            ("2015-01-01", "X", "A", "entree"),
            ("2018-01-01", "B", "X", "sortie"),
        ])
        a = reconstruire(["B", "C"], changements)
        assert a.membres("2016-01-01") == {"X", "C"}
        assert a.membres("2014-01-01") == {"A", "C"}
        assert a.membres("2019-01-01") == {"B", "C"}

    def test_plusieurs_changements_le_meme_jour(self):
        changements = table([
            ("2020-01-01", "D", "A", ""),
            ("2020-01-01", "E", "B", ""),
        ])
        a = reconstruire(["C", "D", "E"], changements)
        assert a.membres("2019-01-01") == {"A", "B", "C"}
        assert a.membres("2020-01-01") == {"C", "D", "E"}

    def test_entree_sans_sortie(self):
        """Elargissement de l'indice : l'effectif passe de 2 a 3."""
        a = reconstruire(["A", "B", "C"], table([("2020-01-01", "C", "", "")]))
        assert a.membres("2019-01-01") == {"A", "B"}
        assert a.membres("2021-01-01") == {"A", "B", "C"}

    def test_sortie_sans_entree(self):
        a = reconstruire(["A", "B"], table([("2020-01-01", "", "Z", "faillite")]))
        assert a.membres("2019-01-01") == {"A", "B", "Z"}
        assert a.membres("2021-01-01") == {"A", "B"}

    def test_composition_actuelle_vide(self):
        with pytest.raises(PointInTimeError):
            reconstruire([], table([("2020-01-01", "A", "B", "")]))

    def test_normalisation_des_tickers(self):
        assert normaliser_ticker("BRK.B") == "BRK-B"
        assert normaliser_ticker(" aapl ") == "AAPL"
        assert normaliser_ticker("GOOGL[1]") == "GOOGL"
        assert normaliser_ticker(float("nan")) == ""
        assert normaliser_ticker(None) == ""

    def test_les_tickers_sont_normalises_de_bout_en_bout(self):
        a = reconstruire(["BRK.B"], table([("2020-01-01", "BRK.B", "OLD.A", "")]))
        assert a.membres("2021-01-01") == {"BRK-B"}
        assert a.membres("2019-01-01") == {"OLD-A"}


class TestMasque:
    def test_alignement_sur_une_matrice_de_cours(self, histoire):
        index = pd.DatetimeIndex(["2019-06-01", "2021-06-01", "2023-06-01"])
        m = histoire.masque(index, ["A", "B", "C", "D", "E"])
        assert list(m.columns) == ["A", "B", "C", "D", "E"]
        assert m.loc["2019-06-01"].to_dict() == {"A": True, "B": True, "C": True,
                                                 "D": False, "E": False}
        assert m.loc["2021-06-01"].to_dict() == {"A": False, "B": True, "C": True,
                                                 "D": True, "E": False}
        assert m.loc["2023-06-01"].to_dict() == {"A": False, "B": False, "C": True,
                                                 "D": True, "E": True}

    def test_ticker_absent_des_colonnes_ignore(self, histoire):
        m = histoire.masque(pd.DatetimeIndex(["2021-06-01"]), ["C", "INCONNU"])
        assert m.loc["2021-06-01", "C"]
        assert not m.loc["2021-06-01", "INCONNU"]

    def test_le_masque_ne_regarde_jamais_devant(self, histoire):
        """Chaque ligne ne depend que des changements deja survenus : tronquer
        l'index ne change pas les lignes conservees."""
        long = pd.date_range("2019-01-01", "2023-12-31", freq="MS")
        court = long[long < "2021-07-01"]
        a = histoire.masque(long, ["A", "B", "C", "D", "E"])
        b = histoire.masque(court, ["A", "B", "C", "D", "E"])
        pd.testing.assert_frame_equal(a.loc[court], b)


class TestQualite:
    def test_signale_les_membres_sans_cours(self, histoire):
        """Le point essentiel : reconstituer l'appartenance ne restitue PAS
        les cours des disparus. Le diagnostic doit le dire."""
        q = histoire.qualite(colonnes_disponibles=["C", "D", "E"])
        assert q["n_membres_historiques"] == 5
        assert q["n_sans_cours"] == 2          # A et B n'ont pas de serie
        assert set(q["exemples_sans_cours"]) == {"A", "B"}
        assert q["part_sans_cours"] == pytest.approx(0.4)

    def test_effectif_par_an(self, histoire):
        q = histoire.qualite()
        assert q["n_changements"] == 2
        assert set(q["effectif_median_par_an"].values()) == {3}


class TestLectureTableWikipedia:
    def test_entetes_sur_deux_niveaux(self):
        brut = pd.DataFrame(
            [["January 1, 2020", "D", "Delta Co", "A", "Alpha Co", "remplacement"]],
            columns=pd.MultiIndex.from_tuples([
                ("Date", "Unnamed: 0_level_1"),
                ("Added", "Ticker"), ("Added", "Security"),
                ("Removed", "Ticker"), ("Removed", "Security"),
                ("Reason", "Unnamed: 5_level_1"),
            ]))
        out = normaliser_changements(brut)
        assert len(out) == 1
        assert out.loc[0, "date"] == pd.Timestamp("2020-01-01")
        assert out.loc[0, "ajoute"] == "D" and out.loc[0, "retire"] == "A"

    def test_lignes_sans_date_ignorees(self):
        brut = pd.DataFrame(
            [["January 1, 2020", "D", "A"], ["—", "E", "B"]],
            columns=["Date", "Added Ticker", "Removed Ticker"])
        assert len(normaliser_changements(brut)) == 1

    def test_table_non_reconnue(self):
        brut = pd.DataFrame([[1, 2]], columns=["Symbol", "Security"])
        with pytest.raises(PointInTimeError):
            normaliser_changements(brut)

    def test_une_colonne_en_plus_ne_decale_pas_la_lecture(self):
        """On reconnait les colonnes par leur intitule, pas par leur rang."""
        brut = pd.DataFrame(
            [["extra", "January 1, 2020", "D", "A", "remplacement"]],
            columns=["Notes", "Date", "Added Ticker", "Removed Ticker", "Reason"])
        out = normaliser_changements(brut)
        assert out.loc[0, "ajoute"] == "D" and out.loc[0, "retire"] == "A"


class TestBranchementBacktest:
    """Le masque doit vraiment contraindre le backtest, et ne rien changer
    quand il n'est pas fourni."""

    def test_sans_masque_rien_ne_change(self, momentum_prices, base_config):
        from quantbot.backtest import run_backtest
        a = run_backtest(momentum_prices, base_config)
        b = run_backtest(momentum_prices, base_config, appartenance=None)
        pd.testing.assert_series_equal(a.equity, b.equity)
        assert a.meta["point_in_time"] is False

    def test_un_titre_non_membre_n_est_jamais_detenu(self, momentum_prices, base_config):
        from quantbot.backtest import run_backtest

        tickers = sorted(t for t in momentum_prices if not t.startswith("^"))
        exclus = set(tickers[: len(tickers) // 2])
        index = momentum_prices[tickers[0]].index
        masque = pd.DataFrame(True, index=index, columns=tickers)
        masque[sorted(exclus)] = False

        res = run_backtest(momentum_prices, base_config, appartenance=masque)
        assert res.meta["point_in_time"] is True
        tenus = set(res.weights.columns[(res.weights.abs() > 1e-9).any()])
        assert not (tenus & exclus), sorted(tenus & exclus)
        assert tenus, "le backtest ne detient plus rien : le masque est trop large"

    def test_un_ticker_absent_du_masque_est_traite_comme_non_membre(
            self, momentum_prices, base_config):
        """Une donnee manquante ne doit pas ouvrir un droit."""
        from quantbot.backtest import run_backtest

        tickers = sorted(t for t in momentum_prices if not t.startswith("^"))
        garde = tickers[:10]
        index = momentum_prices[tickers[0]].index
        masque = pd.DataFrame(True, index=index, columns=garde)

        res = run_backtest(momentum_prices, base_config, appartenance=masque)
        tenus = set(res.weights.columns[(res.weights.abs() > 1e-9).any()])
        assert tenus <= set(garde), sorted(tenus - set(garde))

    def test_le_masque_suit_le_calendrier(self, momentum_prices, base_config):
        """Un titre qui entre dans l'indice en cours de route ne doit pas etre
        detenu avant sa date d'entree."""
        from quantbot.backtest import run_backtest

        tickers = sorted(t for t in momentum_prices if not t.startswith("^"))
        index = momentum_prices[tickers[0]].index
        entrant = tickers[0]
        entree = pd.Timestamp("2015-01-01")
        masque = pd.DataFrame(True, index=index, columns=tickers)
        masque.loc[masque.index < entree, entrant] = False

        res = run_backtest(momentum_prices, base_config, appartenance=masque)
        avant = res.weights.loc[res.weights.index < entree, entrant]
        assert float(avant.abs().max()) < 1e-9
