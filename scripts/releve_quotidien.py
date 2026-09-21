#!/usr/bin/env python3
"""Releve quotidien du compte papier : le rendement AVEC sa bande de bruit.

    python scripts/releve_quotidien.py                  # releve et affiche
    python scripts/releve_quotidien.py --historique     # tout l'historique
    python scripts/releve_quotidien.py --installer      # tache quotidienne

Pourquoi ce script existe
-------------------------
Le 21 septembre 2026, apres UNE seance a +0,96 %, la conclusion tiree etait
que le defi passerait en deux mois au lieu de huit. L'erreur n'est pas de
mauvaise foi, c'est le fonctionnement normal d'un cerveau devant une courbe
qui monte : un chiffre seul se lit toujours comme une tendance.

Ce script rend cette lecture impossible en n'affichant JAMAIS le rendement
sans sa bande de bruit a cote. A une seance, la bande est de +/- 1,6 % : un
+0,96 % s'y voit immediatement comme du bruit. A six semaines elle sera de
+/- 8,6 %, et le meme reflexe sera tout aussi faux.

C'est le meme principe que le tableau de bord, qui affiche le nom technique
d'une mesure a cote de son nom courant : le contexte doit etre dans le meme
champ de vision que le chiffre, pas dans une note de bas de page.

Ce qu'il ne fait pas
--------------------
Il ne juge pas la strategie. Six semaines n'ont aucun pouvoir statistique sur
l'avantage - il faudrait finir au-dela de +8,6 % pour conclure, soit 100 %
annualise, ce qui signalerait un bug plutot qu'un succes. Ce script mesure le
compte, il ne valide rien.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import broker
from quantbot.config import Config

RELEVE = Path("data/releve_quotidien.csv")
COLONNES = ["date", "horodatage", "equity", "solde", "latent", "n_positions",
            "capital_depart", "rendement", "seances", "sigma", "dans_le_bruit"]
NOM_TACHE = "quantbot-releve"

#: Volatilite annualisee mesuree de la strategie (walk-forward hors
#: echantillon, 505 titres). Sert a calculer la bande de bruit.
VOL_ANNUELLE = 0.125
SEANCES_AN = 252


def bande_de_bruit(seances: int, vol_annuelle: float = VOL_ANNUELLE,
                   sigmas: float = 2.0) -> float:
    """Demi-largeur de la bande a 95 % apres `seances` jours de bourse.

    Un rendement cumule sur T annees a un ecart-type de vol x sqrt(T). La
    bande a deux sigma est donc l'intervalle dans lequel un resultat ne dit
    RIEN - ni bon ni mauvais.

    Fonction pure, et separee a dessein : c'est elle qu'il faut pouvoir
    verifier sans compte ni reseau.
    """
    if seances <= 0:
        return 0.0
    return sigmas * vol_annuelle * math.sqrt(seances / SEANCES_AN)


def seances_ecoulees(depuis: str, jusqu_a: str = None) -> int:
    """Jours de bourse (lundi-vendredi) entre deux dates ISO.

    Approximation assumee : les jours feries americains ne sont pas retires.
    Sur six semaines cela fait au plus une seance d'ecart, et la bande de
    bruit varie en racine carree - l'effet est negligeable devant ce qu'elle
    mesure.
    """
    d0 = datetime.fromisoformat(str(depuis)[:10]).date()
    d1 = datetime.fromisoformat(str(jusqu_a)[:10]).date() if jusqu_a else datetime.now().date()
    n, jour = 0, d0
    from datetime import timedelta
    while jour < d1:
        jour += timedelta(days=1)
        if jour.weekday() < 5:
            n += 1
    return n


def verdict(rendement: float, seances: int, vol_annuelle: float = VOL_ANNUELLE) -> dict:
    """Le rendement est-il distinguable de zero ? Fonction pure."""
    b = bande_de_bruit(seances, vol_annuelle)
    dedans = abs(rendement) <= b
    return {
        "rendement": rendement, "seances": seances, "bande": b,
        "dans_le_bruit": dedans,
        "sigma": (rendement / (b / 2.0)) if b > 0 else 0.0,
        "texte": ("%+.2f %% sur %d seance(s) -- bande de bruit +/- %.2f %% -- %s"
                  % (100 * rendement, seances, 100 * b,
                     "DANS LE BRUIT, ce chiffre ne dit rien" if dedans
                     else "HORS de la bande")),
    }


def lire(chemin=None) -> list:
    chemin = Path(chemin) if chemin else RELEVE
    if not chemin.exists():
        return []
    with chemin.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ecrire(ligne: dict, chemin=None) -> None:
    chemin = Path(chemin) if chemin else RELEVE
    chemin.parent.mkdir(parents=True, exist_ok=True)
    neuf = not chemin.exists()
    with chemin.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLONNES)
        if neuf:
            w.writeheader()
        w.writerow({c: ligne.get(c, "") for c in COLONNES})


def afficher(v: dict, equity: float, depart: float) -> None:
    b = v["bande"]
    print("  equity          %12.2f" % equity)
    print("  depart          %12.2f" % depart)
    print("  rendement       %+11.2f %%" % (100 * v["rendement"]))
    print("  bande de bruit  %11s" % ("+/- %.2f %%" % (100 * b)))
    # Une barre pour VOIR ou on est dans la bande, pas seulement le lire.
    if b > 0:
        pos = max(-1.0, min(1.0, v["rendement"] / b))
        largeur = 41
        curseur = int((pos + 1) / 2 * (largeur - 1))
        barre = ["-"] * largeur
        barre[largeur // 2] = "0"
        barre[curseur] = "#"
        print("  %s" % "".join(barre))
        print("  %-20s%-21s" % ("-%.1f %%" % (100 * b), "+%.1f %%" % (100 * b)))
    print()
    print("  " + ("DANS LE BRUIT : ce chiffre ne dit rien encore."
                  if v["dans_le_bruit"] else
                  "HORS de la bande. A verifier : un tel ecart si tot "
                  "signale plus souvent un bug qu'un avantage."))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--fichier", default=str(RELEVE))
    ap.add_argument("--historique", action="store_true")
    ap.add_argument("--installer", action="store_true")
    ap.add_argument("--heure", default="22:30")
    args = ap.parse_args()

    if args.installer:
        return installer(args)

    lignes = lire(args.fichier)
    if args.historique:
        if not lignes:
            print("Aucun releve enregistre.")
            return 0
        print("%-12s %12s %10s %10s %s" % ("date", "equity", "rendement",
                                           "bande", "verdict"))
        print("-" * 64)
        for l in lignes:
            dedans = l.get("dans_le_bruit") == "True"
            print("%-12s %12s %9.2f %% %9.2f %% %s"
                  % (l["date"], l["equity"], 100 * float(l["rendement"]),
                     100 * bande_de_bruit(int(l["seances"])),
                     "bruit" if dedans else "HORS BANDE"))
        return 0

    cfg = Config.load(args.config)
    try:
        api = broker.connecter(cfg, reel=False)
        c = api.compte()
    except Exception as exc:
        print("Courtier injoignable : %s" % str(exc)[:200])
        return 2

    equity = float(c.get("equity", 0.0))
    solde = float(c.get("cash", 0.0))
    latent = equity - float(c.get("last_equity", equity) or equity)
    detail = api.positions_detail()

    # Le capital de depart vient du PREMIER releve, pas de la config : c'est
    # la seule valeur qui ne bouge pas quand on change un reglage.
    depart = float(lignes[0]["capital_depart"]) if lignes else equity
    origine = lignes[0]["date"] if lignes else datetime.now().date().isoformat()
    n = seances_ecoulees(origine)
    rendement = equity / depart - 1.0 if depart > 0 else 0.0
    v = verdict(rendement, n)

    print("=" * 64)
    print("  Releve du %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 64)
    afficher(v, equity, depart)
    print("\n  %d position(s), latent %+.2f" % (len(detail), latent))

    ecrire({"date": datetime.now().date().isoformat(),
            "horodatage": datetime.now().isoformat(timespec="seconds"),
            "equity": round(equity, 2), "solde": round(solde, 2),
            "latent": round(latent, 2), "n_positions": len(detail),
            "capital_depart": round(depart, 2), "rendement": round(rendement, 6),
            "seances": n, "sigma": round(v["sigma"], 2),
            "dans_le_bruit": v["dans_le_bruit"]}, args.fichier)
    return 0


MODELE_PS1 = r"""# Releve quotidien. Genere par scripts/releve_quotidien.py --installer.
Set-Location -LiteralPath "{racine}"
& "{python}" "scripts\releve_quotidien.py" --config "{config}" *>> "data\releve.log"
"""


def installer(args) -> int:
    racine = Path.cwd().resolve()
    script = racine / "lancer_releve.ps1"
    if os.name != "nt":
        print("Systeme non-Windows : le lanceur n'est pas ecrit (ses chemins")
        print("seraient ceux de cette machine). Sur Windows :")
        print("  python scripts\\releve_quotidien.py --installer")
        return 0
    script.write_text(MODELE_PS1.format(racine=racine, python=sys.executable,
                                       config=args.config), encoding="utf-8")
    print("Ecrit : %s" % script)
    tr = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % script
    subprocess.run(["schtasks", "/Create", "/TN", NOM_TACHE, "/SC", "DAILY",
                    "/ST", args.heure, "/F", "/TR", tr], capture_output=True)
    ok = subprocess.run(["schtasks", "/Query", "/TN", NOM_TACHE],
                        capture_output=True).returncode == 0
    print("VERIFIE : tache \"%s\" enregistree a %s." % (NOM_TACHE, args.heure)
          if ok else "ECHEC : la tache n'apparait pas.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
