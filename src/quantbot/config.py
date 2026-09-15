"""Chargement et acces a la configuration YAML.

La config est volontairement plate et accessible par chemin pointe
("factors.momentum.lookback"), ce qui permet a la recherche walk-forward de
faire varier n'importe quel parametre sans code specifique.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml


class Config:
    """Dictionnaire de configuration accessible par chemin pointe."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self._data = data
        self.path = path

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Fichier de configuration introuvable : {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data, path)

    def copy(self) -> "Config":
        return Config(copy.deepcopy(self._data), self.path)

    # -- acces -------------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        """cfg.get("factors.momentum.lookback") -> 252"""
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def has(self, dotted: str) -> bool:
        sentinel = object()
        return self.get(dotted, sentinel) is not sentinel

    def set(self, dotted: str, value: Any, strict: bool = False) -> None:
        """Ecrit une valeur. Avec strict=True, refuse un chemin inexistant.

        Le mode strict evite le piege le plus vicieux du projet : ecrire
        `top_n` au lieu de `portfolio.top_n` cree une cle que personne ne lit,
        sans la moindre erreur. La grille walk-forward n'explore alors rien du
        tout, et la sensibilite aux parametres affiche une robustesse
        parfaitement fictive.
        """
        if strict and not self.has(dotted):
            raise KeyError(
                f"Parametre inconnu : {dotted!r}. Chemin complet attendu, "
                f"par exemple 'portfolio.top_n' et non 'top_n'."
            )
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            existing = node.get(part)
            node = existing if isinstance(existing, dict) else node.setdefault(part, {})
        node[parts[-1]] = value

    def with_overrides(self, overrides: dict[str, Any], strict: bool = True) -> "Config":
        """Renvoie une copie ou certains parametres sont remplaces.

        Utilise par la recherche walk-forward pour tester une combinaison
        sans jamais muter la configuration d'origine. `strict=True` par
        defaut : une faute de frappe dans un nom de parametre leve une
        erreur au lieu de produire silencieusement une grille inerte.
        """
        new = self.copy()
        for dotted, value in overrides.items():
            new.set(dotted, value, strict=strict)
        return new

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def __repr__(self) -> str:
        return f"Config(name={self.get('name')!r}, path={self.path})"
