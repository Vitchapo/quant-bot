"""La page se construit, et les cartes du compte y sont."""
from __future__ import annotations

import re

from quantbot import webui


def _page(mode="live"):
    return webui.render(mode, {"values": {}, "years": [], "n_assets": 0}, "", "titre",
                        "periode", "pied")


def test_les_cartes_du_compte_sont_dans_la_page():
    page = _page()
    for ident in ("ops-capital", "cv-capital", "cv-variations", "ops-capital-tuiles",
                  "cv-domaines", "leg-domaines", "tv-domaines", "ops-argent", "cv-defi"):
        assert 'id="%s"' % ident in page, ident
    for mode in ("seance", "semaine", "mois"):
        assert 'data-capital="%s"' % mode in page


def test_aucun_gabarit_oublie():
    """Un __MARQUEUR__ non remplace casserait le script de la page."""
    for mode in ("live", "static"):
        assert not re.findall(r"__[A-Z]+__", _page(mode)), mode


def test_un_identifiant_n_apparait_qu_une_fois():
    ids = re.findall(r'\bid="([^"]+)"', _page())
    doublons = sorted({i for i in ids if ids.count(i) > 1})
    assert not doublons, doublons
