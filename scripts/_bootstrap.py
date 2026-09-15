"""Ajoute src/ au chemin d'import pour pouvoir lancer les scripts sans installer le paquet."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
