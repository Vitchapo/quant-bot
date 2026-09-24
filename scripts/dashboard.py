#!/usr/bin/env python3
"""Tableau de bord interactif du backtest.

    python scripts/dashboard.py --config config/us.yaml
    python scripts/dashboard.py --config config/us.yaml --benchmark ^GSPC
    python scripts/dashboard.py --synthetic
    python scripts/dashboard.py --export reports/dashboard_us.html

Deux usages :

* sans --export, un petit serveur local s'ouvre dans le navigateur. Chaque
  reglage relance REELLEMENT le backtest : tu explores des combinaisons que
  personne n'a prevues. Rien ne sort de ta machine, l'ecoute est limitee a
  127.0.0.1.
* avec --export, la meme interface est figee dans un seul fichier HTML, sans
  serveur ni dependance, avec une grille de variantes precalculee. Ouvrable
  hors ligne, transmissible tel quel.

Aucune bibliotheque tierce : serveur de la bibliotheque standard, graphiques
dessines sur canvas.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import warnings
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import _bootstrap  # noqa: F401

from quantbot import datafeed, explore, operations, webui
from quantbot.backtest import BenchmarkMissingError
from quantbot.config import Config
from quantbot.universe import UniverseError

warnings.filterwarnings("ignore", category=FutureWarning)

MAX_BODY = 256 * 1024      # une requete de reglages ne pese que quelques centaines d'octets


def load(args):
    cfg = Config.load(args.config)
    if args.benchmark:
        cfg.set("universe.benchmark", args.benchmark)
    if args.synthetic:
        cfg.set("data.cache_dir", cfg.get("data.cache_dir").replace("prices/", "prices/synthetic_"))
        cfg.set("universe.benchmark", "^SYN")
    prices = datafeed.load_panel(cfg)
    return cfg, prices


def page_context(cfg, ex):
    return {
        "title": "quantbot - univers %s" % str(cfg.get("name", "")).upper(),
        # La reference figure en haut de page, pas seulement en pied : comparer
        # a un indice de prix plutot qu'a un total return change tout l'alpha,
        # et cela doit se voir sans avoir a faire defiler.
        "periode": "%s -> %s  ·  reference %s" % (
            ex.index[0].date(), ex.index[-1].date(),
            cfg.get("universe.benchmark") or "aucune"),
        "footer": ("Reference : %s. Frais : %s bps de commission + %s bps de glissement "
                   "par transaction. Delai signal -> execution : %s jour(s)."
                   % (cfg.get("universe.benchmark") or "aucune",
                      cfg.get("execution.commission_bps"), cfg.get("execution.slippage_bps"),
                      cfg.get("execution.execution_lag"))),
    }


# ---------------------------------------------------------------------------
# Serveur local
# ---------------------------------------------------------------------------
class Rafraichissement:
    """Etat du telechargement des cours, mene dans un fil separe.

    Il ne prend PAS le verrou de calcul : il ne fait qu'ecrire des fichiers,
    et bloquer l'interface pendant dix minutes serait pire que le probleme
    qu'il resout.
    """

    def __init__(self):
        self.en_cours = False
        self.termine = False
        self.journal = ""
        self._verrou = threading.Lock()

    def dire(self, message):
        self.journal = (self.journal + "\n" + message).strip()[-1200:]

    def lancer(self, ops, apres):
        with self._verrou:
            if self.en_cours:
                return False
            self.en_cours, self.termine, self.journal = True, False, ""

        def travail():
            try:
                self.dire("Telechargement demarre.")
                ops.rafraichir(journal=self.dire)
                self.termine = True
                apres()
            except Exception as exc:
                self.dire("ECHEC : %s" % exc)
            finally:
                self.en_cours = False

        threading.Thread(target=travail, daemon=True).start()
        return True


def serve(cfg, prices, port, open_browser=True):
    ex = explore.Explorer(prices, cfg)
    values = explore.defaults(cfg)
    # Un premier calcul AVANT d'ouvrir le port : une donnee manquante doit se
    # voir dans le terminal, et non sous forme d'erreur rouge dans le
    # navigateur une fois le serveur lance. Il rechauffe aussi le cache.
    ex.run(values)
    ctx = page_context(cfg, ex)
    html = webui.render(
        "live",
        {"values": values, "years": ex.years, "n_assets": ex.n_assets},
        webui.build_controls(explore.CONTROLS, values, ex.years,
                             simple_ids=explore.SIMPLE_IDS),
        ctx["title"], ctx["periode"], ctx["footer"]).encode("utf-8")
    lock = threading.Lock()
    rafraichissement = Rafraichissement()

    # Le panel est relu depuis le disque apres un telechargement, jamais avant :
    # recharger 500 fichiers a chaque affichage rendrait l'interface poussive.
    cache_prix = {"panel": prices}

    def charger_prix():
        return cache_prix["panel"]

    def recharger_prix():
        try:
            cache_prix["panel"] = datafeed.load_panel(cfg)
        except Exception:
            pass

    ops = operations.Operations(cfg, charger_prix)

    # Cache du battement. Le navigateur interroge toutes les 15 a 60 secondes,
    # et plusieurs onglets peuvent le faire en meme temps ; sans ce cache,
    # chaque onglet declencherait ses trois appels au courtier. Une seconde de
    # peremption suffit a les regrouper sans jamais montrer une donnee vieille.
    pouls_cache = {"t": 0.0, "valeur": None}
    pouls_verrou = threading.Lock()

    def pouls():
        maintenant = time.time()
        with pouls_verrou:
            frais = pouls_cache["valeur"] is not None and maintenant - pouls_cache["t"] < 1.0
            if frais:
                return pouls_cache["valeur"]
        try:
            valeur = ops.pouls()
        except Exception as exc:
            valeur = {"ok": False, "erreur": str(exc), "empreinte": "erreur"}
        # Retard du cache de cours : lu sur le panel DEJA en memoire, donc sans
        # le moindre acces disque. C'est ce qui permet a l'interface de lancer
        # elle-meme le telechargement quand des seances manquent.
        try:
            panel = cache_prix["panel"]
            dates = [df.index.max() for df in panel.values() if len(df.index)]
            derniere = max(dates) if dates else None
            valeur = dict(valeur)
            valeur["derniere_seance"] = str(derniere.date()) if derniere is not None else None
            valeur["retard_seances"] = (datafeed.seances_ecoulees(derniere)
                                        if derniere is not None else None)
            valeur["fetch_en_cours"] = rafraichissement.en_cours
        except Exception:
            pass
        with pouls_verrou:
            pouls_cache.update(t=maintenant, valeur=valeur)
        return valeur

    # L'historique du compte est une donnee JOURNALIERE : le redemander au
    # courtier a chaque battement serait du bruit reseau pour rien. Une minute
    # de cache ; le client le relit de toute facon apres chaque transaction.
    historique_cache = {"t": 0.0, "valeur": None}
    historique_verrou = threading.Lock()

    def historique():
        maintenant = time.time()
        with historique_verrou:
            if (historique_cache["valeur"] is not None
                    and maintenant - historique_cache["t"] < 60.0):
                return historique_cache["valeur"]
        try:
            valeur = ops.historique()
        except Exception as exc:
            valeur = {"ok": False, "raison": str(exc)[:200]}
        with historique_verrou:
            historique_cache.update(t=maintenant, valeur=valeur)
        return valeur

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body, ctype):
            # Le navigateur peut fermer la connexion avant qu'on ait fini
            # d'ecrire : rechargement de page, onglet ferme, ou simplement une
            # connexion persistante recyclee. Avec un battement toutes les
            # minutes, cela arrive regulierement.
            #
            # Ce n'est PAS une erreur : personne n'attend plus la reponse. La
            # laisser remonter affichait une trace de dix lignes dans le
            # terminal, ce qui apprend a ignorer les traces - exactement ce
            # qu'on ne veut pas d'un bot qui doit signaler ses vrais ennuis.
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, BrokenPipeError) as exc:
                self._client_parti = exc

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, html, "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send(404, b"introuvable", "text/plain; charset=utf-8")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(413, b"requete trop volumineuse", "text/plain; charset=utf-8")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except ValueError:
                self._send(400, b"json invalide", "text/plain; charset=utf-8")
                return
            # Ces deux routes sont servies AVANT le verrou de calcul, et ne
            # doivent jamais l'attendre : elles existent precisement pour
            # renseigner l'interface pendant qu'un backtest occupe le moteur.
            if self.path == "/api/ops/pouls":
                self._send(200, json.dumps(pouls(), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if self.path == "/api/ops/historique":
                # Hors verrou, comme le battement : les courbes du compte ne
                # doivent pas geler pendant qu'un test du hasard occupe le
                # moteur pour une minute et demie.
                self._send(200, json.dumps(historique(), ensure_ascii=False,
                                           allow_nan=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            if self.path == "/api/ops/statut":
                # Interroge pendant qu'un calcul long occupe la place : il ne
                # doit surtout pas attendre le verrou.
                self._send(200, json.dumps(
                    {"en_cours": rafraichissement.en_cours,
                     "termine": rafraichissement.termine,
                     "journal": rafraichissement.journal}).encode("utf-8"),
                    "application/json; charset=utf-8")
                return
            try:
                # Un seul calcul a la fois : `random_null` remplace
                # temporairement `factors.composite_score`, ce qui ne supporte
                # pas deux backtests simultanes - et l'etat des operations lit
                # ce meme score, il doit donc partager le verrou.
                with lock:
                    if self.path == "/api/run":
                        out = ex.run(payload)
                    elif self.path == "/api/decompose":
                        out = ex.decompose(payload)
                    elif self.path == "/api/ic":
                        out = ex.ic(payload)
                    elif self.path == "/api/deciles":
                        out = ex.deciles(payload)
                    elif self.path == "/api/ops/etat":
                        out = ops.etat()
                    elif self.path == "/api/ops/envoyer":
                        out = ops.envoyer(forcer=bool(payload.get("forcer")))
                    elif self.path == "/api/ops/annuler":
                        out = ops.annuler()
                    elif self.path == "/api/ops/executions":
                        out = ops.executions()
                    elif self.path == "/api/ops/rafraichir":
                        out = {"lance": rafraichissement.lancer(ops, recharger_prix)}
                    elif self.path == "/api/ops/statut":
                        out = {"en_cours": rafraichissement.en_cours,
                               "termine": rafraichissement.termine,
                               "journal": rafraichissement.journal}
                    elif self.path == "/api/null":
                        out = ex.null(payload.get("values", {}),
                                      n_draws=int(payload.get("n_draws", 150)))
                    else:
                        self._send(404, b"route inconnue", "text/plain; charset=utf-8")
                        return
                body = json.dumps(out, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except Exception as exc:                       # renvoye tel quel au navigateur
                self._send(500, ("%s : %s" % (type(exc).__name__, exc)).encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            self._send(200, body, "application/json; charset=utf-8")

        def log_message(self, fmt, *a):                    # pas de bruit dans le terminal
            pass

        def handle_one_request(self):
            # Meme raison : une connexion coupee pendant la LECTURE de la
            # requete remonte ici, hors de portee de `_send`.
            try:
                BaseHTTPRequestHandler.handle_one_request(self)
            except (ConnectionError, BrokenPipeError):
                self.close_connection = True

    class Serveur(ThreadingHTTPServer):
        """Serveur qui ne crie pas quand un navigateur s'en va.

        `socketserver` imprime une trace complete pour toute exception d'un
        fil de traitement. Une deconnexion client est ordinaire et attendue :
        on la tait, et on laisse passer tout le reste - une vraie erreur doit
        rester visible.
        """

        daemon_threads = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionError, BrokenPipeError)):
                return
            ThreadingHTTPServer.handle_error(self, request, client_address)

    server = Serveur(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % server.server_port
    print("Tableau de bord : %s" % url)
    print("  %d titres, %s -> %s" % (ex.n_assets, ex.index[0].date(), ex.index[-1].date()))
    print("  Ctrl+C pour arreter.\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# Export autonome
# ---------------------------------------------------------------------------
def export(cfg, prices, path, draws=120, with_null=True, null_budget=300.0):
    ex = explore.Explorer(prices, cfg)
    base = explore.defaults(cfg)
    years = ex.years

    tops = sorted({n for n in (5, 10, 20, 30, 50, ex.n_assets) if 1 <= n <= ex.n_assets})
    weightings = ["equal", "inv_vol"]
    regimes = [True, False]
    starts = [""] + [y + "-01-01" for y in years[::5][1:] if y < years[-1]]
    period_ids = [s or "full" for s in starts]

    print("Export : %d variantes x %d periodes" % (len(tops) * 4, len(starts)))
    dates, grid_variants = None, {}
    reference = {"periods": {}}
    total, done = len(tops) * len(weightings) * len(regimes), 0

    for n in tops:
        for w in weightings:
            for r in regimes:
                res = ex.run_periods(dict(base, top_n=n, weighting=w, regime=r,
                                          start="", end=""), starts)
                if dates is None:
                    dates = res["dates"]
                    reference["univers"] = res["univers"]
                    reference["indice"] = res["indice"]
                    reference["periods"] = res["univers_periods"]
                    reference["mensuels"] = {k: res["mensuels"][k]
                                             for k in ("mois", "univers", "indice")}
                grid_variants["%d|%s|%d" % (n, w, 1 if r else 0)] = {
                    "curve": res["curve"], "periods": res["periods"],
                    "mensuels": res["mensuels"]["strategie"]}
                done += 1
                sys.stdout.write("\r  %d/%d variantes" % (done, total)); sys.stdout.flush()
    print()

    precomputed = {"dec": {}, "ic": {}, "null": {}, "deciles": {}}
    for s in starts:
        pid = s or "full"
        print("  decomposition + IC + deciles, periode %s" % pid)
        precomputed["dec"][pid] = ex.decompose(dict(base, start=s))
        precomputed["ic"][pid] = ex.ic(dict(base, start=s))
        precomputed["deciles"][pid] = ex.deciles(dict(base, start=s))
    if with_null:
        # Le test du hasard est de loin l'etape la plus longue et son cout croit
        # avec la taille de l'univers. On lui donne un budget de temps plutot
        # qu'un nombre de tirages ferme : l'export se termine toujours dans un
        # delai previsible, et le fichier indique honnetement combien de
        # tirages ont ete faits.
        print("  test du hasard : jusqu'a %d tirages, %.0f s maximum..." % (draws, null_budget))
        out = ex.null(dict(base, start=""), n_draws=draws, budget=null_budget)
        precomputed["null"]["full"] = out
        print("    %d tirages effectues" % out["n_draws"])

    ctx = page_context(cfg, ex)
    # En statique, `top_n` ne peut prendre que les valeurs precalculees : on le
    # presente donc comme une liste et non comme un curseur, pour ne jamais
    # afficher un reglage qui ne correspond pas aux chiffres montres.
    controls = []
    for c in explore.CONTROLS:
        if c["id"] == "top_n":
            c = dict(c, type="choice", options=[str(t) for t in tops],
                     help="Valeurs precalculees. %d = l'univers entier, sans selection." % ex.n_assets)
        controls.append(c)
    values = dict(base, top_n=str(base["top_n"] if base["top_n"] in tops else tops[0]))
    html = webui.render(
        "static",
        {"values": values, "years": years, "n_assets": ex.n_assets},
        webui.build_controls(controls, values, years,
                             live_ids={"start", "top_n", "weighting", "regime"},
                             simple_ids=explore.SIMPLE_IDS),
        ctx["title"], ctx["periode"], ctx["footer"],
        grid={"dates": dates, "reference": reference, "variants": grid_variants,
              "top_n_values": tops, "n_assets": ex.n_assets},
        precomputed=precomputed)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print("\nFichier autonome ecrit : %s (%.1f Mo)" % (out, out.stat().st_size / 1e6))
    return 0


def _amical(exc) -> int:
    """Affiche une erreur de donnees manquantes sans trace d'appel."""
    print("\n" + "-" * 72)
    print(exc)
    print("-" * 72)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/us.yaml")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--benchmark", help="surcharge universe.benchmark")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--export", metavar="FICHIER.html",
                    help="ecrit un fichier autonome au lieu de lancer le serveur")
    ap.add_argument("--export-draws", type=int, default=120,
                    help="tirages du test du hasard embarque dans l'export")
    ap.add_argument("--no-export-null", action="store_true")
    ap.add_argument("--export-null-budget", type=float, default=300.0,
                    help="temps maximal, en secondes, accorde au test du hasard de l'export")
    args = ap.parse_args()

    cfg, prices = load(args)
    print("%d series chargees depuis %s" % (len(prices), cfg.get("data.cache_dir")))
    if args.export:
        return export(cfg, prices, args.export, args.export_draws,
                      not args.no_export_null, args.export_null_budget)
    return serve(cfg, prices, args.port, not args.no_browser)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BenchmarkMissingError, UniverseError, FileNotFoundError) as exc:
        sys.exit(_amical(exc))
