from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quantbot.config import Config          # noqa: E402
from quantbot.synthetic import generate_prices  # noqa: E402


@pytest.fixture(scope="session")
def base_config():
    cfg = Config.load(Path(__file__).resolve().parent.parent / "config" / "us.yaml")
    cfg.set("universe.benchmark", "^SYN")
    cfg.set("data.min_history_days", 260)
    return cfg


@pytest.fixture(scope="session")
def random_walk_prices():
    """Univers SANS aucun signal exploitable."""
    return generate_prices(n_tickers=60, start="2006-01-01", end="2022-12-31",
                           seed=123, momentum_strength=0.0)


@pytest.fixture(scope="session")
def momentum_prices():
    """Univers AVEC un effet momentum implante."""
    return generate_prices(n_tickers=60, start="2006-01-01", end="2022-12-31",
                           seed=123, momentum_strength=0.55)


def futur_decorrele(prices: dict, coupe, seed: int = 99) -> dict:
    """Detruit la STRUCTURE DE CORRELATION apres `coupe`, sans toucher au passe.

    Pourquoi cette sonde existe
    ---------------------------
    Le test classique de fuite temporelle multiplie les cours futurs par une
    constante. C'est aveugle pour toute statistique invariante d'echelle - et
    la correlation en est une. Une contrainte de diversification qui lirait la
    matrice de correlation de TOUTE la periode passait ce test sans broncher,
    alors qu'elle utilise massivement le futur.

    Ici on permute les rendements de chaque titre APRES la coupure, avec un
    tirage independant par titre. Chaque serie garde sa distribution
    marginale - meme volatilite, memes amplitudes - mais les titres cessent de
    bouger ensemble. Toute lecture du futur par une statistique croisee
    devient visible.
    """
    import numpy as np
    import pandas as pd

    coupe = pd.Timestamp(coupe)
    altere = {}
    for i, (ticker, df) in enumerate(sorted(prices.items())):
        copie = df.copy()
        masque = copie.index >= coupe
        if masque.sum() < 3:
            altere[ticker] = copie
            continue
        rng = np.random.default_rng(seed + i)
        close = copie["close"].to_numpy(dtype="float64").copy()
        debut = int(np.argmax(masque))
        base = close[debut - 1] if debut > 0 else close[0]
        rendements = close[debut:] / np.concatenate([[base], close[debut:-1]])
        nouveau = base * np.cumprod(rng.permutation(rendements))
        facteur = nouveau / close[debut:]
        for col in ("open", "high", "low", "close"):
            if col in copie.columns:
                valeurs = copie[col].to_numpy(dtype="float64").copy()
                valeurs[debut:] = valeurs[debut:] * facteur
                copie[col] = valeurs
        altere[ticker] = copie
    return altere
