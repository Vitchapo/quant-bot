"""Le tableau des couts FTMO (etape 1 du protocole de tendance), sur releves FABRIQUES.

On verifie ce qui decide des chiffres : quel releve compte (bourse ouverte, a
son heure locale, hors jours feries), le spread retenu (le plus eleve des
medianes des trois premiers jours), l'annualisation des swaps, et l'ecart
des ajustements de dividende, qui ne sont pas un cout de financement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import couts_tendance as C  # noqa: E402

COLONNES = ["releve_utc", "instrument", "symbole", "age_s", "bid", "ask", "point", "contrat",
            "swap_achat", "swap_vente", "notionnel_compte", "levier", "devise_profit", "devise_base"]


def _releves(tmp_path, lignes):
    f = tmp_path / "releves.csv"
    pd.DataFrame(lignes, columns=COLONNES).to_csv(f, index=False)
    return C.lire_releves(f)


def _ligne(quand, nom, bid=100.0, ask=100.01, sa=-1.0, sv=-1.0, point=0.01, age=1.0):
    return [quand, nom, nom, age, bid, ask, point, 1.0, sa, sv, 100.0, 15.0, "USD", "USD"]


def test_sydney_compte_le_jour_de_sa_bourse(tmp_path):
    """23:42 UTC le 03/11, c'est 10:42 le 04/11 a Sydney : seance du 04/11."""
    d = _releves(tmp_path, [_ligne("2026-11-03T23:42:02Z", "AUS200"),
                            _ligne("2026-11-04T06:42:02Z", "AUS200")])     # 17:42 a Sydney : ferme
    x = C.ouverts(d, "AUS200")
    assert list(x["jour"]) == ["2026-11-04"]


def test_un_jour_ferie_ne_compte_pas(tmp_path):
    """Le 03/11, Tokyo est ferme (fete de la culture) : FTMO cote, la bourse non."""
    d = _releves(tmp_path, [_ligne("2026-11-03T01:42:02Z", "JP225"),
                            _ligne("2026-11-04T01:42:02Z", "JP225")])
    assert list(C.ouverts(d, "JP225")["jour"]) == ["2026-11-04"]


def test_le_spread_retenu_est_le_plus_eleve_des_trois_premiers_jours(tmp_path):
    lignes = []
    ecarts = {"2026-11-02": [0.0, 0.02, 0.04],     # mediane 0,02 : un spread nul compte
              "2026-11-03": [0.01, 0.01, 0.05],    # mediane 0,01
              "2026-11-04": [0.03, 0.03, 0.03],    # mediane 0,03 : la plus elevee des trois
              "2026-11-05": [0.50, 0.50, 0.50]}    # quatrieme jour : ne compte pas
    for jour, valeurs in ecarts.items():
        for h, e in zip(("10", "12", "14"), valeurs):
            lignes.append(_ligne("%sT%s:42:02Z" % (jour, h), "EURUSD", bid=1.0, ask=1.0 + e))
    d = _releves(tmp_path, lignes)
    spread, jours = C.spread_retenu(d, "EURUSD")
    assert jours == ["2026-11-02", "2026-11-03", "2026-11-04"]
    assert spread == pytest.approx(0.03 / 1.015)


def test_un_swap_en_points_s_annualise_sur_le_cours():
    """US500 le 30/10 : -175,78 points par nuit, cours 7 620 -> -8,42 % par an."""
    x = pd.DataFrame({"swap_achat": [-175.78], "swap_vente": [-8.27], "point": [0.01], "milieu": [7620.0]})
    y = C.swaps_annualises(x)
    assert y["achat_an"].iloc[0] == pytest.approx(-175.78 * 0.01 * 365 / 7620)
    assert y["achat_an"].iloc[0] == pytest.approx(-0.0842, abs=5e-4)


def test_un_dividende_est_ecarte_un_mouvement_de_taux_non():
    """AUS200 le 04/11 au soir : +993 a l'achat, -1 230 a la vente. La somme ne
    bouge pas : c'est un dividende. Les swaps des devises bougent aussi, avec
    les taux : on ne les ecarte jamais."""
    indice = pd.DataFrame({"swap_achat": [-251.5, -249.3, 993.4, -254.3],
                           "swap_vente": [13.4, 13.0, -1229.6, 13.5]})
    assert list(C.jours_de_dividende(indice, "AUS200")) == [False, False, True, False]
    devise = pd.DataFrame({"swap_achat": [2.6, 4.3, 2.55, 3.4], "swap_vente": [-17.4, -20.7, -17.4, -20.0]})
    assert not C.jours_de_dividende(devise, "USDJPY").any()


def test_une_marge_n_est_jamais_negative():
    """Si FTMO paie plus que le modele, la marge vaut zero, pas un gain."""
    jours = pd.date_range("2026-10-01", "2026-11-10", freq="D")
    taux = {c: pd.Series(v, index=jours) for c, v in
            {"US": 3.875, "JP": 1.25, "XM": 2.5, "GB": 3.75, "CH": 0.0, "CA": 2.5, "AU": 4.6, "NZ": 2.75}.items()}
    d = pd.DataFrame({"instrument": "USDJPY", "t": pd.to_datetime(["2026-11-04T10:00Z"] * 2),
                      "swap_achat": [80.0, 80.0], "swap_vente": [-20.0, -20.0],
                      "point": [0.001, 0.001], "milieu": [157.0, 157.0]})
    m = C.marges(d, "USDJPY", taux)
    # le modele fait rapporter 3,875 - 1,25 = 2,625 %/an a l'achat ; FTMO paie 80 x 0,001 x 365 / 157 = 18,6 %
    assert m["portage_achat"] == pytest.approx(0.02625)
    assert m["marge_achat"] == 0.0
    assert m["marge_vente"] == pytest.approx(-0.02625 - (-20 * 0.001 * 365 / 157))
