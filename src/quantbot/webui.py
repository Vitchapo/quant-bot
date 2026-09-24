# -*- coding: utf-8 -*-
"""Interface du tableau de bord : une seule page, deux modes.

* mode "live"   - servie par `scripts/dashboard.py`. Chaque reglage relance
  reellement le backtest cote serveur.
* mode "static" - `--export`. Les memes ecrans, mais alimentes par une grille
  de variantes precalculee et embarquee dans le fichier. Aucun serveur, aucune
  dependance, ouvrable hors ligne et transmissible tel quel.

La page est volontairement autonome : pas de CDN, pas de police distante, pas
de librairie de graphiques. Tout est dessine sur `<canvas>` avec une centaine
de lignes de JavaScript, ce qui evite d'ajouter une dependance a un projet qui
tient a n'en avoir aucune de superflue.

Palette : bleu / orange / turquoise, dans cet ordre fixe, validee pour les
deficiences de vision des couleurs (ecart minimal 9,2 en deuteranopie et 24,0
en vision normale, sur toutes les paires). Le turquoise passe sous 3:1 de
contraste en theme clair : chaque courbe porte donc une etiquette en bout de
trace et chaque graphique a une vue tableau, de sorte que la couleur ne porte
jamais seule l'information.
"""
from __future__ import annotations

import json

CSS = """
:root {
  color-scheme: light;
  --page:            #f9f9f7;
  --surface:         #fcfcfb;
  --ink:             #0b0b0b;
  --ink-2:           #52514e;
  --muted:           #898781;
  --grid:            #e1e0d9;
  --axis:            #c3c2b7;
  --border:          rgba(11,11,11,0.10);
  --series-1:        #2a78d6;
  --series-2:        #eb6834;
  --series-3:        #1baf7a;
  --pos:             #2a78d6;
  --neg:             #e34948;
  --neutral-bar:     #c3c2b7;
  --good:            #0ca30c;
  --critical:        #d03b3b;
  --field:           #ffffff;
  --shadow:          0 1px 2px rgba(11,11,11,0.05);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page:        #0d0d0d;
    --surface:     #1a1a19;
    --ink:         #ffffff;
    --ink-2:       #c3c2b7;
    --muted:       #898781;
    --grid:        #2c2c2a;
    --axis:        #383835;
    --border:      rgba(255,255,255,0.10);
    --series-1:    #3987e5;
    --series-2:    #d95926;
    --series-3:    #199e70;
    --pos:         #3987e5;
    --neg:         #e66767;
    --neutral-bar: #383835;
    --field:       #232322;
    --shadow:      none;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835;
  --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
  --pos: #3987e5; --neg: #e66767; --neutral-bar: #383835;
  --field: #232322; --shadow: none;
}

* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
header {
  padding: 20px 24px 16px; border-bottom: 1px solid var(--border);
  background: var(--surface);
  display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
}
header h1 { margin: 0; font-size: 17px; font-weight: 600; letter-spacing: -0.01em; }
header .sub { color: var(--ink-2); font-size: 13px; }
header .spacer { flex: 1 1 auto; }
.badge {
  font-size: 11px; padding: 3px 8px; border-radius: 999px;
  border: 1px solid var(--border); color: var(--ink-2); white-space: nowrap;
}
.layout { display: grid; grid-template-columns: 288px minmax(0, 1fr); gap: 20px; padding: 20px 24px 56px; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }

aside {
  align-self: start; position: sticky; top: 16px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 4px 14px 14px; box-shadow: var(--shadow);
  max-height: calc(100vh - 40px); overflow-y: auto;
}
aside h2 {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.07em;
  color: var(--muted); margin: 18px 0 8px; font-weight: 600;
}
.field { margin-bottom: 11px; }
.field label { display: flex; justify-content: space-between; gap: 8px; font-size: 12.5px; color: var(--ink-2); margin-bottom: 3px; }
.field label b { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.field input[type=range] { width: 100%; margin: 0; accent-color: var(--series-1); }
.field select, .field input[type=number] {
  width: 100%; padding: 5px 7px; font: inherit; font-size: 13px;
  color: var(--ink); background: var(--field);
  border: 1px solid var(--border); border-radius: 6px;
}
.field .hint { font-size: 11.5px; color: var(--muted); margin-top: 3px; line-height: 1.4; }
.switch { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 13px; }
.switch input { accent-color: var(--series-1); width: 15px; height: 15px; }
button {
  font: inherit; font-size: 13px; padding: 6px 12px; border-radius: 7px;
  border: 1px solid var(--border); background: var(--field); color: var(--ink);
  cursor: pointer;
}
button:hover { border-color: var(--axis); }
button.primary { background: var(--series-1); border-color: var(--series-1); color: #fff; }
button:disabled { opacity: 0.5; cursor: default; }

.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 20px; }
@media (max-width: 1180px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.tile {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; box-shadow: var(--shadow);
}
.tile .k { font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;
  min-height: 2.4em; display: flex; align-items: flex-start; }
.tile .v { font-size: 25px; font-weight: 600; letter-spacing: -0.02em; margin: 3px 0 1px; }
.tile .c { font-size: 11.5px; color: var(--ink-2); }
.tile .c i { font-style: normal; color: var(--muted); }

.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px 18px; margin-bottom: 20px; box-shadow: var(--shadow);
}
.card h3 { margin: 0 0 2px; font-size: 14.5px; font-weight: 600; }
.card p.note { margin: 0 0 14px; color: var(--ink-2); font-size: 12.5px; max-width: 76ch; }
.card .head { display: flex; align-items: flex-start; gap: 12px; }
.card .head .spacer { flex: 1 1 auto; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; margin: 2px 0 10px; font-size: 12.5px; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 11px; height: 3px; border-radius: 2px; display: inline-block; }
.plot { position: relative; }
canvas { display: block; width: 100%; }
.tip {
  position: absolute; pointer-events: none; opacity: 0; transition: opacity .1s;
  background: var(--surface); border: 1px solid var(--axis); border-radius: 7px;
  padding: 7px 9px; font-size: 12px; box-shadow: 0 4px 14px rgba(0,0,0,0.13);
  white-space: nowrap; z-index: 5; color: var(--ink);
}
.tip b { font-variant-numeric: tabular-nums; }
.tip .row { display: flex; align-items: center; gap: 6px; justify-content: space-between; }
.tip .row i { width: 9px; height: 3px; border-radius: 2px; }

table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th, td { text-align: right; padding: 5px 9px; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }
th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 10.5px; letter-spacing: 0.05em; }
th:first-child, td:first-child { text-align: left; font-variant-numeric: normal; }
tbody tr:last-child td { border-bottom: none; }
td.pos { color: var(--good); } td.neg { color: var(--critical); }
tr.total td { font-weight: 600; }
details.tv { margin-top: 12px; }
details.tv summary { cursor: pointer; font-size: 12.5px; color: var(--ink-2); }
details.tv > div { margin-top: 8px; max-height: 340px; overflow: auto; }

.msg { font-size: 12.5px; color: var(--muted); padding: 10px 0; }
.msg.err { color: var(--critical); }
.busy { opacity: 0.45; transition: opacity .15s; }
.verdict { margin-top: 12px; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border); font-size: 13px; line-height: 1.55; }
.verdict.bad { border-left: 3px solid var(--critical); }
.verdict.good { border-left: 3px solid var(--good); }
.verdict.flat { border-left: 3px solid var(--muted); }
footer { padding: 0 24px 40px; color: var(--muted); font-size: 12px; max-width: 90ch; }

/* --- Deux niveaux de lecture -------------------------------------------
   "simple" masque le vocabulaire technique et les tableaux de chiffres
   bruts ; "complet" montre tout. Rien n'est supprime, seulement replie :
   la page doit rester utile a mesure qu'on apprend. */
body[data-niveau="simple"] .avance { display: none !important; }
body[data-niveau="complet"] .hint-simple { display: none; }

.niveau { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.niveau button { border: 0; border-radius: 0; padding: 5px 12px; background: transparent; color: var(--ink-2); }
.niveau button[aria-pressed="true"] { background: var(--series-1); color: #fff; }

/* --- Carte de synthese --------------------------------------------------- */
.resume { border-left: 3px solid var(--series-1); }
.resume .titre { font-size: 20px; font-weight: 600; letter-spacing: -0.01em; line-height: 1.35; margin: 2px 0 14px; }
.qr { display: grid; grid-template-columns: 26px minmax(0,1fr); gap: 10px 12px; align-items: start; }
.qr + .qr { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--grid); }
.pastille {
  width: 22px; height: 22px; border-radius: 50%; display: inline-flex;
  align-items: center; justify-content: center; font-size: 13px; font-weight: 700;
  color: #fff; background: var(--muted); margin-top: 1px;
}
.pastille.oui { background: var(--good); } .pastille.non { background: var(--critical); }
.pastille.attente { background: transparent; color: var(--muted); border: 1px dashed var(--axis); font-weight: 400; }
.qr .q { font-weight: 600; font-size: 13.5px; }
.qr .r { color: var(--ink-2); font-size: 13px; margin-top: 2px; }
.conclusion { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--grid); font-size: 13.5px; line-height: 1.6; }

/* --- Tuiles : libelle en clair, nom technique en second ------------------- */
.tile .k abbr { text-decoration: none; color: var(--muted); font-weight: 400;
  border-bottom: 1px dotted var(--axis); cursor: help; margin-left: 5px; }
.tile .h { font-size: 11.5px; color: var(--muted); line-height: 1.4; margin-top: 7px;
  padding-top: 7px; border-top: 1px solid var(--grid); }

/* --- Cartes : un titre numerote, une phrase, le detail replie ------------- */
.card h3 .num { color: var(--muted); font-weight: 400; margin-right: 7px; }
.regarder { font-size: 13px; color: var(--ink); margin: 0 0 6px; }
.regarder b { font-weight: 600; }
details.plus { margin: 0 0 14px; }
details.plus > summary { cursor: pointer; font-size: 12.5px; color: var(--series-1); list-style: none; }
details.plus > summary::-webkit-details-marker { display: none; }
details.plus > summary::before { content: "▸ "; }
details.plus[open] > summary::before { content: "▾ "; }
details.plus p { color: var(--ink-2); font-size: 12.5px; max-width: 76ch; margin: 8px 0 0; }

/* --- Glossaire ----------------------------------------------------------- */
.glossaire { display: grid; grid-template-columns: repeat(auto-fill, minmax(288px, 1fr)); gap: 14px 26px; }
.glossaire dt { font-weight: 600; font-size: 13px; }
.glossaire dd { margin: 3px 0 0; color: var(--ink-2); font-size: 12.5px; line-height: 1.5; }
.glossaire div { break-inside: avoid; }

/* --- Deux vues : analyse et operations ---------------------------------- */
/* -- suivi automatique du compte ------------------------------------------ */
.auto { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.auto .vu { font-size: 11.5px; color: var(--muted); font-variant-numeric: tabular-nums;
            min-width: 9.5em; }
.pouls { width: 9px; height: 9px; border-radius: 50%; background: var(--muted);
         flex: none; opacity: .45; }
.pouls.actif { background: #1baf7a; opacity: 1; }
.pouls.occupe { background: #2a78d6; opacity: 1; }
.pouls.perdu { background: #eb6834; opacity: 1; }
@media (prefers-reduced-motion: no-preference) {
  .pouls.actif { animation: battement 2.4s ease-in-out infinite; }
  .pouls.occupe { animation: battement .9s ease-in-out infinite; }
  @keyframes battement { 0%, 100% { opacity: 1; } 50% { opacity: .32; } }
}
#vue-ops-pastille { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
                    background: #eb6834; margin-left: 6px; vertical-align: middle; }
.change { animation: surligne 1.1s ease-out; }
@media (prefers-reduced-motion: reduce) { .change { animation: none; } }
@keyframes surligne { from { background: rgba(27,175,122,.18); } to { background: transparent; } }

body[data-vue="analyse"] #ops { display: none; }
body[data-vue="ops"] #panels, body[data-vue="ops"] aside { display: none; }
body[data-vue="ops"] .layout { grid-template-columns: minmax(0, 1fr); }

.controles { display: grid; gap: 8px; }
.controle { display: grid; grid-template-columns: 22px minmax(0,1fr); gap: 10px; align-items: start; }
.controle .q { font-weight: 600; font-size: 13px; }
.controle .r { color: var(--ink-2); font-size: 12.5px; }

.actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
button.danger { background: var(--critical); border-color: var(--critical); color: #fff; }
button.danger:hover { filter: brightness(1.08); }
.confirmation { margin-top: 12px; padding: 12px 14px; border-radius: 8px;
  border: 1px solid var(--critical); border-left-width: 3px; font-size: 13px; }
.confirmation b { font-variant-numeric: tabular-nums; }
.progression { font-size: 12.5px; color: var(--ink-2); margin-top: 10px; white-space: pre-line; }
.pastille-mode { background: var(--good); color: #fff; border-color: transparent; }

/* -- graphiques du compte et carte mensuelle --------------------------- */
/* Statuts : fixes dans les deux themes (jamais reutilises pour une serie). */
:root { --warning: #fab219; --serious: #ec835a; --neutral-mid: #f0efec; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) { --neutral-mid: #383835; }
}
:root[data-theme="dark"] { --neutral-mid: #383835; }
.legend i.point { width: 9px; height: 9px; border-radius: 50%; background: var(--series-1); }
.legend i.creux { width: 11px; height: 11px; border-radius: 50%; background: transparent;
  border: 2px solid var(--series-1); }
.card h4 { margin: 22px 0 2px; font-size: 13.5px; font-weight: 600; }
.bascule { display: inline-flex; border: 1px solid var(--border); border-radius: 7px;
  overflow: hidden; margin: 6px 0 10px; }
.bascule button { border: 0; border-radius: 0; padding: 4px 11px; font-size: 12.5px;
  background: transparent; color: var(--ink-2); }
.bascule button[aria-pressed="true"] { background: var(--series-1); color: #fff; }
.note-graphe { font-size: 12.5px; color: var(--ink-2); margin: 8px 0 0; max-width: 80ch; }
"""


JS = r"""
// ---------------------------------------------------------------------------
// Utilitaires de format
// ---------------------------------------------------------------------------
const NB = " ";
function fmtPct(x, dec) { if (x === null || x === undefined || !isFinite(x)) return "n/a";
  return (100 * x).toFixed(dec === undefined ? 2 : dec).replace(".", ",") + NB + "%"; }
function fmtNum(x, dec) { if (x === null || x === undefined || !isFinite(x)) return "n/a";
  return x.toFixed(dec === undefined ? 2 : dec).replace(".", ","); }
function fmtSigned(x, dec) { if (x === null || x === undefined || !isFinite(x)) return "n/a";
  return (x >= 0 ? "+" : "") + fmtPct(x, dec); }
function cssVar(n) { return getComputedStyle(document.body).getPropertyValue(n).trim(); }
function el(id) { return document.getElementById(id); }

// ---------------------------------------------------------------------------
// Socle canvas : densite de pixels, marges, echelles
// ---------------------------------------------------------------------------
function surface(cv, height) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.parentElement.clientWidth;
  cv.width = Math.round(w * dpr); cv.height = Math.round(height * dpr);
  cv.style.height = height + "px";
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, height);
  ctx.font = "11px system-ui, -apple-system, 'Segoe UI', sans-serif";
  return { ctx: ctx, w: w, h: height };
}
function niceTicks(lo, hi, count) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / Math.max(count, 2);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out = []; let v = Math.ceil(lo / step) * step;
  for (; v <= hi + 1e-9; v += step) out.push(Math.abs(v) < 1e-9 ? 0 : v);
  return out;
}
function logTicks(lo, hi) {
  const out = [];
  for (let k = Math.floor(Math.log10(lo)); k <= Math.ceil(Math.log10(hi)); k++)
    for (const m of [1, 2, 5]) { const v = m * Math.pow(10, k); if (v >= lo && v <= hi) out.push(v); }
  return out.length >= 3 ? out : niceTicks(lo, hi, 5);
}
// Etiquettes en bout de trace, ecartees pour ne jamais se chevaucher.
function placeLabels(items, minGap, top, bottom) {
  items.sort(function (a, b) { return a.y - b.y; });
  for (let i = 1; i < items.length; i++)
    if (items[i].y - items[i - 1].y < minGap) items[i].y = items[i - 1].y + minGap;
  const over = items.length ? items[items.length - 1].y - bottom : 0;
  if (over > 0) for (const it of items) it.y -= over;
  for (const it of items) it.y = Math.max(top, it.y);
  return items;
}

// ---------------------------------------------------------------------------
// Courbes (avec curseur reticule et infobulle)
// ---------------------------------------------------------------------------
function drawLines(cv, tip, opt) {
  const S = surface(cv, opt.height || 300), ctx = S.ctx;
  const series = opt.series.filter(function (s) { return s.values && s.values.length; });
  if (!series.length) return;
  const padL = 52, padR = opt.labels === false ? 16 : 104, padT = 10, padB = 26;
  const x0 = padL, x1 = S.w - padR, y0 = padT, y1 = S.h - padB;
  const n = opt.dates.length;
  const clean = function (v) { return v !== null && v !== undefined && isFinite(v); };

  let lo = Infinity, hi = -Infinity;
  for (const s of series) for (const v of s.values) if (clean(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo)) return;
  if (opt.log) { lo = Math.max(lo * 0.94, 1e-6); hi = hi * 1.06; }
  else {
    if (opt.fillZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }   // le zero reste dans le cadre
    const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad; if (opt.zeroTop && hi > 0) hi = 0;
  }

  const ty = opt.log ? function (v) { return y1 - (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)) * (y1 - y0); }
                     : function (v) { return y1 - (v - lo) / (hi - lo) * (y1 - y0); };
  const tx = function (i) { return x0 + (n <= 1 ? 0 : i / (n - 1) * (x1 - x0)); };

  // grille + axe y
  ctx.strokeStyle = cssVar("--grid"); ctx.lineWidth = 1;
  ctx.fillStyle = cssVar("--muted"); ctx.textAlign = "right"; ctx.textBaseline = "middle";
  const ticks = opt.log ? logTicks(lo, hi) : niceTicks(lo, hi, 5);
  for (const t of ticks) {
    const y = Math.round(ty(t)) + 0.5;
    if (y < y0 - 1 || y > y1 + 1) continue;
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillText(opt.yFmt ? opt.yFmt(t) : fmtNum(t, 0), x0 - 8, y);
  }
  // axe x : une graduation par annee, espacees
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  const years = [];
  for (let i = 0; i < n; i++) { const y = opt.dates[i].slice(0, 4); if (!years.length || years[years.length - 1].y !== y) years.push({ y: y, i: i }); }
  const stride = Math.max(1, Math.ceil(years.length / 9));
  for (let k = 0; k < years.length; k += stride) {
    const p = Math.round(tx(years[k].i)) + 0.5;
    ctx.strokeStyle = cssVar("--grid");
    ctx.beginPath(); ctx.moveTo(p, y0); ctx.lineTo(p, y1); ctx.stroke();
    ctx.fillStyle = cssVar("--muted"); ctx.fillText(years[k].y, p, y1 + 7);
  }
  ctx.strokeStyle = cssVar("--axis");
  ctx.beginPath(); ctx.moveTo(x0, y1 + 0.5); ctx.lineTo(x1, y1 + 0.5); ctx.stroke();

  // Lavis au-dessus / au-dessous de zero : une serie dont le SIGNE est le
  // message. 12 % d'opacite, jamais un aplat.
  if (opt.fillZero && !opt.log) {
    const z = ty(0);
    for (const s of series) {
      const P = [];
      for (let i = 0; i < s.values.length; i++) if (clean(s.values[i])) P.push([tx(i), ty(s.values[i])]);
      if (P.length < 2) continue;
      for (const zo of [[y0, z, cssVar("--pos")], [z, y1, cssVar("--neg")]]) {
        ctx.save(); ctx.beginPath(); ctx.rect(x0, zo[0], x1 - x0, zo[1] - zo[0]); ctx.clip();
        ctx.beginPath(); ctx.moveTo(P[0][0], z);
        for (const p of P) ctx.lineTo(p[0], p[1]);
        ctx.lineTo(P[P.length - 1][0], z); ctx.closePath();
        ctx.globalAlpha = 0.12; ctx.fillStyle = zo[2]; ctx.fill(); ctx.restore();
      }
    }
    ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x0, Math.round(z) + 0.5); ctx.lineTo(x1, Math.round(z) + 0.5); ctx.stroke();
  }

  // traces : 2px, sans remplissage lourd
  ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
  for (const s of series) {
    ctx.strokeStyle = s.color; ctx.beginPath();
    let started = false;
    for (let i = 0; i < s.values.length; i++) {
      const v = s.values[i]; if (!clean(v)) { started = false; continue; }
      const X = tx(i), Y = ty(v);
      if (!started) { ctx.moveTo(X, Y); started = true; } else ctx.lineTo(X, Y);
    }
    ctx.stroke();
  }
  // etiquettes directes : la couleur ne porte jamais seule l'identite
  if (opt.labels !== false) {
    const items = [];
    for (const s of series) {
      let last = null;
      for (let i = s.values.length - 1; i >= 0; i--) if (clean(s.values[i])) { last = s.values[i]; break; }
      if (last === null) continue;
      items.push({ y: ty(last), name: s.name, color: s.color, v: last });
    }
    placeLabels(items, 13, y0 + 6, y1);
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    for (const it of items) {
      ctx.fillStyle = it.color;
      ctx.fillRect(x1 + 5, it.y - 1.5, 9, 3);
      ctx.fillStyle = cssVar("--ink-2");
      ctx.fillText(it.name, x1 + 18, it.y);
    }
  }

  // reticule
  const state = { i: -1 };
  function hover(ev) {
    const r = cv.getBoundingClientRect();
    const px = ev.clientX - r.left;
    if (px < x0 - 6 || px > x1 + 6) { leave(); return; }
    const i = Math.max(0, Math.min(n - 1, Math.round((px - x0) / (x1 - x0) * (n - 1))));
    if (i === state.i) return;
    state.i = i;
    drawLines(cv, null, Object.assign({}, opt, { _cross: i }));
    const rows = series.map(function (s) {
      return '<div class="row"><span><i style="background:' + s.color + '"></i> ' + s.name +
             '</span><b>' + (clean(s.values[i]) ? (opt.tipFmt ? opt.tipFmt(s.values[i]) : fmtNum(s.values[i])) : "n/a") + '</b></div>';
    }).join("");
    const extra = opt.tipExtra ? opt.tipExtra(i).map(function (r) {
      return '<div class="row"><span>' + r[0] + '</span><b>' + r[1] + '</b></div>'; }).join("") : "";
    tip.innerHTML = '<div style="color:var(--muted);margin-bottom:4px">' +
      (opt.dateFmt ? opt.dateFmt(opt.dates[i]) : opt.dates[i]) + '</div>' + rows + extra;
    tip.style.opacity = 1;
    const tw = tip.offsetWidth;
    tip.style.left = Math.min(Math.max(tx(i) + 14, 4), S.w - tw - 4) + "px";
    tip.style.top = Math.max(4, Math.min(ev.clientY - r.top - 12, S.h - tip.offsetHeight - 4)) + "px";
  }
  function leave() { state.i = -1; tip.style.opacity = 0; drawLines(cv, null, Object.assign({}, opt, { _cross: -1 })); }
  if (tip) {
    cv.onmousemove = hover; cv.onmouseleave = leave;
    cv.onpointerdown = hover;
  }
  if (opt._cross !== undefined && opt._cross >= 0) {
    const i = opt._cross, X = Math.round(tx(i)) + 0.5;
    ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(X, y0); ctx.lineTo(X, y1); ctx.stroke();
    for (const s of series) {
      const v = s.values[i]; if (!clean(v)) continue;
      ctx.beginPath(); ctx.arc(tx(i), ty(v), 4.5, 0, 6.284);
      ctx.fillStyle = s.color; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
    }
  }
}

// ---------------------------------------------------------------------------
// Cascade (decomposition)
// ---------------------------------------------------------------------------
function drawWaterfall(cv, tip, steps) {
  const S = surface(cv, 260), ctx = S.ctx;
  if (!steps.length) return;
  const padL = 52, padR = 12, padT = 14, padB = 62;
  const x0 = padL, x1 = S.w - padR, y0 = padT, y1 = S.h - padB;
  let lo = 0, hi = 0, cum = 0;
  const bars = [];
  for (const s of steps) {
    if (s.total) { bars.push({ lo: Math.min(0, s.value), hi: Math.max(0, s.value), v: s.value, label: s.label, kind: "total" }); cum = s.value; }
    else { const a = cum, b = cum + s.value; bars.push({ lo: Math.min(a, b), hi: Math.max(a, b), v: s.value, label: s.label, kind: s.value >= 0 ? "pos" : "neg" }); cum = b; }
    lo = Math.min(lo, bars[bars.length - 1].lo); hi = Math.max(hi, bars[bars.length - 1].hi);
  }
  const pad = (hi - lo) * 0.12 || 0.01; lo -= pad; hi += pad;
  const ty = function (v) { return y1 - (v - lo) / (hi - lo) * (y1 - y0); };
  ctx.strokeStyle = cssVar("--grid"); ctx.fillStyle = cssVar("--muted");
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const t of niceTicks(lo, hi, 5)) {
    const y = Math.round(ty(t)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillText(fmtPct(t, 0), x0 - 8, y);
  }
  const zero = Math.round(ty(0)) + 0.5;
  ctx.strokeStyle = cssVar("--axis");
  ctx.beginPath(); ctx.moveTo(x0, zero); ctx.lineTo(x1, zero); ctx.stroke();

  const slot = (x1 - x0) / bars.length, bw = Math.min(slot - 14, 74);
  const colors = { pos: cssVar("--pos"), neg: cssVar("--neg"), total: cssVar("--neutral-bar") };
  bars.forEach(function (b, i) {
    const cx = x0 + slot * (i + 0.5);
    const top = ty(b.hi), bot = ty(b.lo);
    ctx.fillStyle = colors[b.kind];
    const h = Math.max(bot - top, 2);
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(cx - bw / 2, top, bw, h, 4); ctx.fill(); }
    else ctx.fillRect(cx - bw / 2, top, bw, h);
    // valeur au-dessus (ou en dessous) de la barre, jamais dedans
    ctx.fillStyle = cssVar("--ink"); ctx.textAlign = "center";
    ctx.textBaseline = b.v >= 0 ? "bottom" : "top";
    ctx.fillText(b.kind === "total" ? fmtPct(b.v, 2) : fmtSigned(b.v, 2), cx, b.v >= 0 ? top - 5 : bot + 5);
    // libelle sur deux lignes maximum
    ctx.fillStyle = cssVar("--ink-2"); ctx.textBaseline = "top";
    const words = b.label.split(" "); let line = "", ln = 0;
    for (const w of words) {
      const test = line ? line + " " + w : w;
      if (ctx.measureText(test).width > slot - 6 && line) { ctx.fillText(line, cx, y1 + 8 + ln * 13); line = w; ln++; if (ln > 2) break; }
      else line = test;
    }
    if (ln <= 2) ctx.fillText(line, cx, y1 + 8 + ln * 13);
  });
}

// ---------------------------------------------------------------------------
// Histogramme du null aleatoire
// ---------------------------------------------------------------------------
function drawHist(cv, values, real, realLabel) {
  const S = surface(cv, 230), ctx = S.ctx;
  if (!values.length) return;
  const padL = 40, padR = 16, padT = 30, padB = 30;
  const x0 = padL, x1 = S.w - padR, y0 = padT, y1 = S.h - padB;
  let lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
  lo = Math.min(lo, real); hi = Math.max(hi, real);
  const pad = (hi - lo) * 0.08 || 0.05; lo -= pad; hi += pad;
  const nb = Math.max(12, Math.min(28, Math.round(Math.sqrt(values.length) * 1.7)));
  const bins = new Array(nb).fill(0);
  for (const v of values) bins[Math.max(0, Math.min(nb - 1, Math.floor((v - lo) / (hi - lo) * nb)))]++;
  const top = Math.max.apply(null, bins) || 1;
  const tx = function (v) { return x0 + (v - lo) / (hi - lo) * (x1 - x0); };

  ctx.fillStyle = cssVar("--series-1");
  const bw = (x1 - x0) / nb;
  bins.forEach(function (c, i) {
    if (!c) return;
    const h = (c / top) * (y1 - y0);
    const x = x0 + i * bw + 1;
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(x, y1 - h, Math.max(bw - 2, 1), h, 3); ctx.fill(); }
    else ctx.fillRect(x, y1 - h, Math.max(bw - 2, 1), h);
  });
  ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x0, y1 + 0.5); ctx.lineTo(x1, y1 + 0.5); ctx.stroke();
  ctx.fillStyle = cssVar("--muted"); ctx.textBaseline = "top"; ctx.textAlign = "center";
  for (const t of niceTicks(lo, hi, 6)) { if (t < lo || t > hi) continue; ctx.fillText(fmtNum(t, 2), tx(t), y1 + 7); }

  // repere du reel : trait plein + etiquette, jamais une couleur seule
  const X = Math.round(tx(real)) + 0.5;
  ctx.strokeStyle = cssVar("--ink"); ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(X, y0 - 8); ctx.lineTo(X, y1); ctx.stroke();
  ctx.fillStyle = cssVar("--ink"); ctx.textBaseline = "bottom";
  ctx.textAlign = X > (x0 + x1) / 2 ? "right" : "left";
  ctx.fillText(realLabel, X + (X > (x0 + x1) / 2 ? -6 : 6), y0 - 10);
}

// ---------------------------------------------------------------------------
// Barres horizontales (t de Student des facteurs)
// ---------------------------------------------------------------------------
function drawTBars(cv, items) {
  const S = surface(cv, Math.max(90, 30 + items.length * 30)), ctx = S.ctx;
  if (!items.length) return;
  const padL = 150, padR = 22, padT = 8, padB = 22;
  const x0 = padL, x1 = S.w - padR, y0 = padT, y1 = S.h - padB;
  const span = Math.max(2.6, Math.max.apply(null, items.map(function (d) { return Math.abs(d.t); })) * 1.25);
  const tx = function (v) { return x0 + (v + span) / (2 * span) * (x1 - x0); };
  ctx.strokeStyle = cssVar("--grid");
  for (const t of [-2, 2]) { const X = Math.round(tx(t)) + 0.5; ctx.beginPath(); ctx.moveTo(X, y0); ctx.lineTo(X, y1); ctx.stroke(); }
  ctx.fillStyle = cssVar("--muted"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.fillText("-2", tx(-2), y1 + 5); ctx.fillText("+2", tx(2), y1 + 5);
  ctx.fillText("seuil de credibilite  |t| = 2", (x0 + x1) / 2, y1 + 5);
  const zero = Math.round(tx(0)) + 0.5;
  ctx.strokeStyle = cssVar("--axis"); ctx.beginPath(); ctx.moveTo(zero, y0); ctx.lineTo(zero, y1); ctx.stroke();
  const slot = (y1 - y0) / items.length, bh = Math.min(slot - 10, 15);
  items.forEach(function (d, i) {
    const cy = y0 + slot * (i + 0.5);
    ctx.fillStyle = d.t >= 0 ? cssVar("--pos") : cssVar("--neg");
    const a = Math.min(tx(0), tx(d.t)), w = Math.max(Math.abs(tx(d.t) - tx(0)), 2);
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(a, cy - bh / 2, w, bh, 3); ctx.fill(); }
    else ctx.fillRect(a, cy - bh / 2, w, bh);
    ctx.fillStyle = cssVar("--ink-2"); ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(d.label, x0 - 10, cy);
    ctx.fillStyle = cssVar("--ink"); ctx.textAlign = d.t >= 0 ? "left" : "right";
    ctx.fillText("t = " + fmtNum(d.t), tx(d.t) + (d.t >= 0 ? 7 : -7), cy);
  });
}


// ---------------------------------------------------------------------------
// Deciles de score : le profil dit tout. Croissant = le score classe.
// En U = il trouve les extremes, ce qui n'est pas la meme chose.
// ---------------------------------------------------------------------------
function drawDeciles(cv, d) {
  const S = surface(cv, 250), ctx = S.ctx;
  const items = d.deciles.map(function (x) { return { label: String(x.rang), v: x.ecart, t: x.t_stat }; });
  items.push({ gap: true });
  items.push({ label: "top " + d.top_n, v: d.meilleurs.ecart, t: d.meilleurs.t_stat, fort: true });
  items.push({ label: "pires " + d.top_n, v: d.pires.ecart, t: d.pires.t_stat, fort: true });
  const padL = 56, padR = 14, padT = 16, padB = 46;
  const x0 = padL, x1 = S.w - padR, y0 = padT, y1 = S.h - padB;
  const vals = items.filter(function (i) { return !i.gap; }).map(function (i) { return i.v; });
  let lo = Math.min.apply(null, vals.concat([0])), hi = Math.max.apply(null, vals.concat([0]));
  const pad = (hi - lo) * 0.18 || 0.001; lo -= pad; hi += pad;
  const ty = function (v) { return y1 - (v - lo) / (hi - lo) * (y1 - y0); };

  ctx.strokeStyle = cssVar("--grid"); ctx.fillStyle = cssVar("--muted");
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const t of niceTicks(lo, hi, 5)) {
    const y = Math.round(ty(t)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillText(fmtPct(t, 2), x0 - 8, y);
  }
  const zero = Math.round(ty(0)) + 0.5;
  ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x0, zero); ctx.lineTo(x1, zero); ctx.stroke();

  const slot = (x1 - x0) / items.length, bw = Math.min(slot - 8, 46);
  items.forEach(function (it, i) {
    if (it.gap) return;
    const cx = x0 + slot * (i + 0.5);
    const top = ty(Math.max(it.v, 0)), bot = ty(Math.min(it.v, 0));
    ctx.fillStyle = it.v >= 0 ? cssVar("--pos") : cssVar("--neg");
    ctx.globalAlpha = it.fort ? 1 : 0.75;
    const h = Math.max(bot - top, 2);
    if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(cx - bw / 2, top, bw, h, 3); ctx.fill(); }
    else ctx.fillRect(cx - bw / 2, top, bw, h);
    ctx.globalAlpha = 1;
    // seuls les ecarts credibles portent une etiquette : un nombre sur chaque
    // barre serait illisible et donnerait du poids a du bruit
    if (Math.abs(it.t) > 2) {
      ctx.fillStyle = cssVar("--ink"); ctx.textAlign = "center";
      ctx.textBaseline = it.v >= 0 ? "bottom" : "top";
      ctx.fillText("t " + fmtNum(it.t, 1), cx, it.v >= 0 ? top - 4 : bot + 4);
    }
    ctx.fillStyle = it.fort ? cssVar("--ink-2") : cssVar("--muted");
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.fillText(it.label, cx, y1 + 8);
  });
  ctx.fillStyle = cssVar("--muted"); ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText("deciles de score : 1 = pires, " + d.n_buckets + " = meilleurs", x0, y1 + 26);
}

// ---------------------------------------------------------------------------
// Outils des graphiques du compte et de la carte mensuelle
// ---------------------------------------------------------------------------
const MOIS_COURTS = ["janv.", "fevr.", "mars", "avr.", "mai", "juin",
                     "juil.", "aout", "sept.", "oct.", "nov.", "dec."];
const MOIS_LONGS = ["janvier", "fevrier", "mars", "avril", "mai", "juin",
                    "juillet", "aout", "septembre", "octobre", "novembre", "decembre"];
const POLICE = "11px system-ui, -apple-system, 'Segoe UI', sans-serif";
const POLICE_FORTE = "600 11px system-ui, -apple-system, 'Segoe UI', sans-serif";
function propre(v) { return v !== null && v !== undefined && isFinite(v); }
function jourCourt(d) { return d.slice(8, 10) + "/" + d.slice(5, 7); }
function jourLong(d) {
  return parseInt(d.slice(8, 10), 10) + " " + MOIS_LONGS[parseInt(d.slice(5, 7), 10) - 1] +
         " " + d.slice(0, 4);
}
function moisLong(m) { return MOIS_LONGS[parseInt(m.slice(5, 7), 10) - 1] + " " + m.slice(0, 4); }
function pts(x) { return propre(x) ? fmtNum(100 * x, 2) + NB + "pts" : "n/a"; }
function symbole(dev) { return !dev || dev === "USD" ? "$" : dev; }
function montant(x, dev) {
  if (!propre(x)) return "n/a";
  return x.toLocaleString("fr-FR", { maximumFractionDigits: 0 }) + NB + symbole(dev);
}
function argent(x, dev) {
  if (!propre(x)) return "n/a";
  return (x > 0 ? "+" : x < 0 ? "-" : "") + montant(Math.abs(x), dev);
}
// Graduation d'un axe en % : "0 %" au zero plutot que "+0 %", une decimale
// seulement quand le pas l'exige.
function pctAxe(t, pas) {
  if (Math.abs(t) < 1e-12) return "0" + NB + "%";
  return fmtSigned(t, pas < 0.00999 ? 1 : 0);
}

// Infobulle construite noeud par noeud : les noms affiches viennent parfois
// du courtier (tickers) et ne passent donc jamais par innerHTML.
function tipRemplir(tip, titre, lignes) {
  while (tip.firstChild) tip.removeChild(tip.firstChild);
  const t = document.createElement("div");
  t.style.color = "var(--muted)"; t.style.marginBottom = "4px";
  t.textContent = titre;
  tip.appendChild(t);
  for (const l of lignes) {
    const row = document.createElement("div"); row.className = "row";
    const nom = document.createElement("span");
    if (l.couleur) {
      const i = document.createElement("i"); i.style.background = l.couleur;
      nom.appendChild(i); nom.appendChild(document.createTextNode(" "));
    }
    nom.appendChild(document.createTextNode(l.nom));
    const val = document.createElement("b"); val.textContent = l.valeur;
    row.appendChild(nom); row.appendChild(val); tip.appendChild(row);
  }
}
function tipPoser(tip, S, x, y) {
  tip.style.opacity = 1;
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  tip.style.left = Math.min(Math.max(x + 14, 4), S.w - tw - 4) + "px";
  tip.style.top = Math.max(4, Math.min(y - 12, S.h - th - 4)) + "px";
}
function tipCacher(tip) { if (tip) tip.style.opacity = 0; }
// Legende en DOM : meme regle que l'infobulle, jamais de nom dans innerHTML.
function legendeDom(hote, items) {
  const cle = JSON.stringify(items);
  if (!hote || hote.dataset.cle === cle) return;
  hote.dataset.cle = cle;
  while (hote.firstChild) hote.removeChild(hote.firstChild);
  for (const it of items) {
    const s = document.createElement("span"), i = document.createElement("i");
    i.style.background = it[1];
    s.appendChild(i); s.appendChild(document.createTextNode(it[0])); hote.appendChild(s);
  }
}

// Geometrie commune aux deux graphiques du defi : chaque seance occupe une
// tranche de meme largeur, au meme endroit dans les deux. La barre du jour
// tombe donc exactement a l'aplomb de son point sur la courbe du dessus.
function geoSeances(S, n, padL, padR, padT, padB) {
  const x0 = padL, x1 = S.w - padR, y0 = padT, y1 = S.h - padB;
  const pas = (x1 - x0) / Math.max(n, 1);
  return { x0: x0, x1: x1, y0: y0, y1: y1, pas: pas,
           tx: function (i) { return x0 + pas * (i + 0.5); } };
}
function axeY(ctx, lo, hi, ty, x0, x1, y0, y1, fmt) {
  const ticks = niceTicks(lo, hi, 6), pas = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
  ctx.strokeStyle = cssVar("--grid"); ctx.lineWidth = 1;
  ctx.fillStyle = cssVar("--muted"); ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const t of ticks) {
    const y = Math.round(ty(t)) + 0.5;
    if (y < y0 - 1 || y > y1 + 1) continue;
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillText(fmt(t, pas), x0 - 8, y);
  }
}
// Axe de SEANCES. Celui des backtests est gradue par annee : sur trois
// semaines d'historique il n'afficherait qu'un libelle. Ici une etiquette
// par seance ou par mois selon la duree, et une etiquette qui chevaucherait
// la precedente est sautee plutot que tassee.
function axeSeances(ctx, dates, tx, y0, y1, x1) {
  const n = dates.length, cand = [], parMois = n > 45;
  if (!parMois) { for (let i = 0; i < n; i++) cand.push({ i: i, t: jourCourt(dates[i]) }); }
  else {
    let m = null;
    for (let i = 0; i < n; i++) {
      const k = dates[i].slice(0, 7);
      if (k !== m) { m = k; cand.push({ i: i, t: MOIS_COURTS[parseInt(k.slice(5, 7), 10) - 1] }); }
    }
  }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  let fin = -Infinity;
  for (const c of cand) {
    const X = tx(c.i), w = ctx.measureText(c.t).width;
    if (X - w / 2 < fin + 10 || X + w / 2 > x1 + 30) continue;
    if (parMois) {
      const p = Math.round(X) + 0.5;
      ctx.strokeStyle = cssVar("--grid"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(p, y0); ctx.lineTo(p, y1); ctx.stroke();
    }
    ctx.fillStyle = cssVar("--muted"); ctx.fillText(c.t, X, y1 + 7);
    fin = X + w / 2;
  }
}
// Un SEUIL est tirete : c'est ce qui le distingue d'une graduation, laquelle
// reste un filet plein et discret.
function seuil(ctx, xs, ys, couleur) {
  ctx.save(); ctx.strokeStyle = couleur; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]);
  ctx.beginPath();
  for (let k = 0; k < xs.length; k++) { if (k === 0) ctx.moveTo(xs[k], ys[k]); else ctx.lineTo(xs[k], ys[k]); }
  ctx.stroke(); ctx.restore();
}
// Etiquettes de marge droite : une cle (trait plein pour une serie, ICONE
// pour un seuil), puis le texte a l'encre. Le texte ne porte jamais la
// couleur de la donnee : un jaune ou un turquoise seraient illisibles.
//
// Pourquoi une icone sur les seuils : le vert de l'objectif et le rouge du
// contrat sont indiscernables en deuteranopie (ecart 4,1 au validateur, sous
// le plancher de 6). Une couleur de statut ne porte jamais seule le sens :
// icone + libelle + position, trois canaux qui ne dependent pas de la vue.
const ICONES = { objectif: "✓", "garde-fou": "!", contrat: "✕" };
function etiquettesDroite(ctx, items, x1, y0, y1) {
  placeLabels(items, 14, y0 + 6, y1 - 2);
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  for (const it of items) {
    const icone = it.seuil ? ICONES[it.texte.split(" ")[0]] : null;
    if (icone) {
      ctx.font = POLICE_FORTE; ctx.fillStyle = it.couleur; ctx.textAlign = "center";
      ctx.fillText(icone, x1 + 11, it.y); ctx.textAlign = "left";
    } else {
      ctx.save(); ctx.strokeStyle = it.couleur; ctx.lineWidth = it.seuil ? 1.5 : 3;
      if (it.seuil) ctx.setLineDash([3, 2]);
      ctx.beginPath(); ctx.moveTo(x1 + 6, it.y); ctx.lineTo(x1 + 16, it.y); ctx.stroke(); ctx.restore();
    }
    ctx.font = it.fort ? POLICE_FORTE : POLICE;
    ctx.fillStyle = cssVar(it.fort ? "--ink" : "--ink-2");
    ctx.fillText(it.texte, x1 + 21, it.y);
  }
  ctx.font = POLICE;
}
// Barre a bout ARRONDI cote donnee, CARRE cote ligne de base.
function barreV(ctx, x, w, yBase, yVal) {
  const haut = Math.min(yBase, yVal), bas = Math.max(yBase, yVal), h = bas - haut;
  if (h < 0.5) return;
  const r = Math.min(4, h, w / 2);
  ctx.beginPath();
  if (yVal <= yBase) {
    ctx.moveTo(x, bas); ctx.lineTo(x, haut + r); ctx.arcTo(x, haut, x + r, haut, r);
    ctx.lineTo(x + w - r, haut); ctx.arcTo(x + w, haut, x + w, haut + r, r); ctx.lineTo(x + w, bas);
  } else {
    ctx.moveTo(x, haut); ctx.lineTo(x, bas - r); ctx.arcTo(x, bas, x + r, bas, r);
    ctx.lineTo(x + w - r, bas); ctx.arcTo(x + w, bas, x + w, bas - r, r); ctx.lineTo(x + w, haut);
  }
  ctx.closePath(); ctx.fill();
}
function barreH(ctx, y, h, xBase, xVal) {
  const g = Math.min(xBase, xVal), d = Math.max(xBase, xVal), w = d - g;
  if (w < 0.5) return;
  const r = Math.min(4, w, h / 2);
  ctx.beginPath();
  if (xVal >= xBase) {
    ctx.moveTo(g, y); ctx.lineTo(d - r, y); ctx.arcTo(d, y, d, y + r, r);
    ctx.lineTo(d, y + h - r); ctx.arcTo(d, y + h, d - r, y + h, r); ctx.lineTo(g, y + h);
  } else {
    ctx.moveTo(d, y); ctx.lineTo(g + r, y); ctx.arcTo(g, y, g, y + r, r);
    ctx.lineTo(g, y + h - r); ctx.arcTo(g, y + h, g + r, y + h, r); ctx.lineTo(d, y + h);
  }
  ctx.closePath(); ctx.fill();
}
// Point plein avec anneau de 2 px couleur de surface : il reste lisible la
// ou il croise une courbe.
function point(ctx, x, y, couleur) {
  ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 6.2832);
  ctx.fillStyle = couleur; ctx.fill();
  ctx.lineWidth = 2; ctx.strokeStyle = cssVar("--surface"); ctx.stroke();
}
function bandeSurvol(ctx, x, y, w, h) {
  ctx.save(); ctx.globalAlpha = 0.55; ctx.fillStyle = cssVar("--grid");
  ctx.fillRect(x, y, w, h); ctx.restore();
}

// Interpolation PERCEPTUELLE (OKLab) pour l'echelle divergente. Un melange en
// RVB entre le gris neutre et le bleu traverse des teintes ternes : les mois
// moyens paraitraient plus faibles qu'ils ne sont.
function hexRgb(h) {
  h = (h || "#000000").trim().replace("#", "");
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  return [parseInt(h.slice(0, 2), 16) / 255, parseInt(h.slice(2, 4), 16) / 255,
          parseInt(h.slice(4, 6), 16) / 255];
}
function versLin(c) { return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
function versSrgb(c) { return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; }
function oklab(hex) {
  const c = hexRgb(hex).map(versLin);
  const l = Math.cbrt(0.4122214708 * c[0] + 0.5363325363 * c[1] + 0.0514459929 * c[2]);
  const m = Math.cbrt(0.2119034982 * c[0] + 0.6806995451 * c[1] + 0.1073969566 * c[2]);
  const s = Math.cbrt(0.0883024619 * c[0] + 0.2817188376 * c[1] + 0.6299787005 * c[2]);
  return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
          1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
          0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
}
function depuisOklab(L) {
  const l = Math.pow(L[0] + 0.3963377774 * L[1] + 0.2158037573 * L[2], 3);
  const m = Math.pow(L[0] - 0.1055613458 * L[1] - 0.0638541728 * L[2], 3);
  const s = Math.pow(L[0] - 0.0894841775 * L[1] - 1.2914855480 * L[2], 3);
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
          -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
          -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s]
    .map(function (c) { return Math.round(255 * Math.min(1, Math.max(0, versSrgb(c)))); });
}
// Rouge <- gris neutre -> bleu : deux teintes opposees, rien d'autre au milieu.
function echelleDivergente() {
  const neg = oklab(cssVar("--neg")), mid = oklab(cssVar("--neutral-mid")), pos = oklab(cssVar("--pos"));
  return function (t) {
    t = Math.max(-1, Math.min(1, t));
    const b = t >= 0 ? pos : neg, k = Math.abs(t);
    return depuisOklab([mid[0] + (b[0] - mid[0]) * k, mid[1] + (b[1] - mid[1]) * k,
                        mid[2] + (b[2] - mid[2]) * k]);
  };
}
function rgbCss(c) { return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")"; }
// Encre ou blanc, selon ce qui contraste le plus avec la case.
function encreSur(c) {
  const L = 0.2126 * versLin(c[0] / 255) + 0.7152 * versLin(c[1] / 255) + 0.0722 * versLin(c[2] / 255);
  return 1.05 / (L + 0.05) >= (L + 0.05) / 0.0533 ? "#ffffff" : "#0b0b0b";
}

// ---------------------------------------------------------------------------
// Le defi : ou est le compte entre l'objectif et les deux planchers
// ---------------------------------------------------------------------------
// Une seule echelle, en % du capital de depart, pour le compte, l'indice et
// les barrieres. Le domaine contient TOUJOURS l'objectif et la limite du
// contrat : cadre sur la seule courbe, un recul de 0,5 % paraitrait
// dramatique, et la distance qu'il faut lire - celle qui reste jusqu'aux
// lignes - sortirait du cadre.
function drawDefi(cv, tip, D, croix) {
  const S = surface(cv, 300), ctx = S.ctx, n = D.dates.length;
  if (!n) return;
  const G = geoSeances(S, n, 52, 128, 12, 26);
  const x0 = G.x0, x1 = G.x1, y0 = G.y0, y1 = G.y1, tx = G.tx;

  let lo = 0, hi = 0;
  const bornes = [D.compte, D.indice];
  if (D.actif) bornes.push(D.plancherContrat, [D.objectif]);
  for (const s of bornes) for (const v of s || []) if (propre(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const marge = (hi - lo) * 0.08 || 0.01; lo -= marge; hi += marge;
  const ty = function (v) { return y1 - (v - lo) / (hi - lo) * (y1 - y0); };

  axeY(ctx, lo, hi, ty, x0, x1, y0, y1, pctAxe);
  axeSeances(ctx, D.dates, tx, y0, y1, x1);
  const z = Math.round(ty(0)) + 0.5;                 // le depart : l'axe de reference
  ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x0, z); ctx.lineTo(x1, z); ctx.stroke();

  const labels = [];
  if (D.actif) {
    // Plancher STATIQUE = une droite ; GLISSANT = un escalier sous le plus haut.
    const trace = function (vals, couleur, texte) {
      const plat = vals.every(function (v) { return Math.abs(v - vals[0]) < 1e-12; });
      const xs = [x0], ys = [ty(vals[0])];
      if (!plat) for (let i = 0; i < n; i++) { xs.push(tx(i)); ys.push(ty(vals[i])); }
      xs.push(x1); ys.push(ty(vals[n - 1]));
      seuil(ctx, xs, ys, couleur);
      labels.push({ y: ys[ys.length - 1], texte: texte, couleur: couleur, seuil: true });
    };
    trace(D.objectifs, cssVar("--good"), "objectif " + fmtSigned(D.objectif, 0));
    trace(D.plancherBot, cssVar("--serious"), "garde-fou " + fmtSigned(D.plancherBot[n - 1], 0));
    trace(D.plancherContrat, cssVar("--critical"), "contrat " + fmtSigned(D.plancherContrat[n - 1], 0));
  }
  if (D.verrou && D.verrou.depuis) {
    let iv = -1;
    for (let i = 0; i < n; i++) if (D.dates[i] >= D.verrou.depuis) { iv = i; break; }
    if (iv >= 0) {
      const X = Math.round(tx(iv)) + 0.5, droite = X > (x0 + x1) / 2;
      ctx.strokeStyle = cssVar("--critical"); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(X, y0); ctx.lineTo(X, y1); ctx.stroke();
      ctx.font = POLICE_FORTE; ctx.fillStyle = cssVar("--ink");
      ctx.textAlign = droite ? "right" : "left"; ctx.textBaseline = "top";
      ctx.fillText("verrou pose", X + (droite ? -6 : 6), y0 + 2);
      ctx.font = POLICE;
    }
  }

  const courbe = function (vals, couleur) {
    ctx.strokeStyle = couleur; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath(); let ok = false;
    for (let i = 0; i < n; i++) {
      const v = vals[i];
      if (!propre(v)) { ok = false; continue; }
      if (!ok) { ctx.moveTo(tx(i), ty(v)); ok = true; } else ctx.lineTo(tx(i), ty(v));
    }
    ctx.stroke();
  };
  const dernier = function (vals) { for (let i = n - 1; i >= 0; i--) if (propre(vals[i])) return i; return -1; };
  const cI = cssVar("--muted"), cC = cssVar("--series-1");
  if (D.indiceNom) {
    courbe(D.indice, cI);
    const ii = dernier(D.indice);
    if (ii >= 0) labels.push({ y: ty(D.indice[ii]), texte: D.indiceNom + " " + fmtSigned(D.indice[ii], 2), couleur: cI });
  }
  courbe(D.compte, cC);
  const ic = dernier(D.compte);
  if (ic >= 0) {
    point(ctx, tx(ic), ty(D.compte[ic]), cC);
    labels.push({ y: ty(D.compte[ic]), texte: "compte " + fmtSigned(D.compte[ic], 2), couleur: cC, fort: true });
  }
  etiquettesDroite(ctx, labels, x1, y0, y1);

  if (croix !== undefined && croix >= 0) {
    const X = Math.round(tx(croix)) + 0.5;
    ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(X, y0); ctx.lineTo(X, y1); ctx.stroke();
    if (D.indiceNom && propre(D.indice[croix])) point(ctx, tx(croix), ty(D.indice[croix]), cI);
    if (propre(D.compte[croix])) point(ctx, tx(croix), ty(D.compte[croix]), cC);
  }
  if (!tip) return;
  const quitter = function () { tipCacher(tip); drawDefi(cv, null, D, -1); };
  const survol = function (ev) {
    const r = cv.getBoundingClientRect(), px = ev.clientX - r.left;
    if (px < x0 - 6 || px > x1 + 6) { quitter(); return; }
    const i = Math.max(0, Math.min(n - 1, Math.floor((px - x0) / G.pas)));
    drawDefi(cv, null, D, i);
    const L = [{ nom: "compte", valeur: fmtSigned(D.compte[i], 2), couleur: cC },
               { nom: "valeur", valeur: montant(D.equity[i], D.devise) }];
    if (D.indiceNom) L.push({ nom: D.indiceNom, couleur: cI,
                              valeur: propre(D.indice[i]) ? fmtSigned(D.indice[i], 2) : "pas de cloture" });
    if (D.actif) {
      L.push({ nom: "jusqu'a l'objectif", valeur: pts(D.objectif - D.compte[i]) });
      L.push({ nom: "marge avant le garde-fou", valeur: pts(D.compte[i] - D.plancherBot[i]) });
      L.push({ nom: "marge avant le contrat", valeur: pts(D.compte[i] - D.plancherContrat[i]) });
    }
    tipRemplir(tip, jourLong(D.dates[i]), L);
    tipPoser(tip, S, tx(i), ev.clientY - r.top);
  };
  cv.onmousemove = survol; cv.onmouseleave = quitter; cv.onpointerdown = survol;
}

// ---------------------------------------------------------------------------
// Gains et pertes seance par seance, en % du CAPITAL DE DEPART
// ---------------------------------------------------------------------------
// Meme denominateur que le garde-fou et que le contrat ("5 % of initial
// balance") : la barre et la limite parlent de la meme chose. Le domaine
// contient toujours la limite du contrat : la lecture utile est la distance
// entre la pire barre et la ligne rouge, pas la forme des petites barres.
function drawJour(cv, tip, D, survolee) {
  const S = surface(cv, 190), ctx = S.ctx, n = D.dates.length;
  if (!n) return;
  const G = geoSeances(S, n, 52, 128, 10, 26);
  const x0 = G.x0, x1 = G.x1, y0 = G.y0, y1 = G.y1, tx = G.tx;
  let lo = 0, hi = 0.004;
  for (const v of D.jour) if (propre(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (D.actif) lo = Math.min(lo, -D.contratJour);
  const marge = (hi - lo) * 0.10; lo -= marge; hi += marge;
  const ty = function (v) { return y1 - (v - lo) / (hi - lo) * (y1 - y0); };

  axeY(ctx, lo, hi, ty, x0, x1, y0, y1, pctAxe);
  axeSeances(ctx, D.dates, tx, y0, y1, x1);
  const labels = [];
  if (D.actif) {
    const a = ty(-D.perteJourMax), b = ty(-D.contratJour);
    seuil(ctx, [x0, x1], [a, a], cssVar("--serious"));
    seuil(ctx, [x0, x1], [b, b], cssVar("--critical"));
    labels.push({ y: a, texte: "garde-fou " + fmtSigned(-D.perteJourMax, 0), couleur: cssVar("--serious"), seuil: true });
    labels.push({ y: b, texte: "contrat " + fmtSigned(-D.contratJour, 0), couleur: cssVar("--critical"), seuil: true });
  }
  const w = Math.max(2, Math.min(24, G.pas * 0.62));
  const cP = cssVar("--pos"), cN = cssVar("--neg");
  for (let i = 0; i < n; i++) {
    const v = D.jour[i];
    if (!propre(v)) continue;
    ctx.globalAlpha = survolee === undefined || survolee < 0 || survolee === i ? 1 : 0.5;
    ctx.fillStyle = v >= 0 ? cP : cN;
    barreV(ctx, tx(i) - w / 2, w, ty(0), ty(v));
  }
  ctx.globalAlpha = 1;
  const z = Math.round(ty(0)) + 0.5;
  ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x0, z); ctx.lineTo(x1, z); ctx.stroke();
  etiquettesDroite(ctx, labels, x1, y0, y1);

  if (!tip) return;
  const quitter = function () { tipCacher(tip); drawJour(cv, null, D, -1); };
  const survol = function (ev) {
    const r = cv.getBoundingClientRect(), i = Math.floor((ev.clientX - r.left - x0) / G.pas);
    if (i < 0 || i >= n || !propre(D.jour[i])) { quitter(); return; }
    drawJour(cv, null, D, i);
    const v = D.jour[i];
    const L = [{ nom: "variation du jour", valeur: fmtSigned(v, 2), couleur: v >= 0 ? cP : cN },
               { nom: "en montant", valeur: argent(v * D.depart, D.devise) }];
    if (D.actif) L.push({ nom: "marge avant le garde-fou", valeur: pts(v + D.perteJourMax) });
    tipRemplir(tip, jourLong(D.dates[i]), L);
    tipPoser(tip, S, tx(i), ev.clientY - r.top);
  };
  cv.onmousemove = survol; cv.onmouseleave = quitter; cv.onpointerdown = survol;
}

// ---------------------------------------------------------------------------
// Detenu contre vise, ligne par ligne
// ---------------------------------------------------------------------------
// Point plein : le poids vise. Anneau : ce que tu detiens. Point dans son
// anneau = ligne alignee ; l'ecart entre les deux, c'est l'ordre que le
// prochain rebalancement passera. La FORME porte l'identite : les deux
// marques sont du meme bleu.
function drawHalteres(cv, tip, L, survolee) {
  const H = 21, padT = 4, padB = 24, padL = 62, padR = 16;
  const S = surface(cv, padT + L.length * H + padB), ctx = S.ctx;
  if (!L.length) return;
  const x0 = padL, x1 = S.w - padR, y1 = S.h - padB;
  let hi = 0;
  for (const r of L) hi = Math.max(hi, r.detenu || 0, r.vise || 0);
  const ticks = niceTicks(0, (hi || 0.01) * 1.06, 5);
  hi = Math.max((hi || 0.01) * 1.06, ticks[ticks.length - 1]);
  const pasT = ticks.length > 1 ? ticks[1] - ticks[0] : 0.01;
  const tx = function (v) { return x0 + v / hi * (x1 - x0); };
  ctx.lineWidth = 1; ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const t of ticks) {
    const X = Math.round(tx(t)) + 0.5;
    ctx.strokeStyle = cssVar(t === 0 ? "--axis" : "--grid");
    ctx.beginPath(); ctx.moveTo(X, padT); ctx.lineTo(X, y1); ctx.stroke();
    ctx.fillStyle = cssVar("--muted"); ctx.fillText(fmtPct(t, pasT < 0.01 ? 1 : 0), X, y1 + 7);
  }
  const c = cssVar("--series-1");
  for (let k = 0; k < L.length; k++) {
    const r = L[k], y = padT + (k + 0.5) * H;
    if (survolee === k) bandeSurvol(ctx, 2, y - H / 2, S.w - 4, H);
    ctx.fillStyle = cssVar("--ink-2"); ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(r.ticker, x0 - 10, y);
    const xa = tx(r.detenu || 0), xb = tx(r.vise || 0);
    if (r.vise > 0 && r.detenu > 0 && Math.abs(xa - xb) > 12) {
      const dir = xb > xa ? 1 : -1;
      ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(xa + dir * 7, y); ctx.lineTo(xb - dir * 5, y); ctx.stroke();
    }
    if (r.vise > 0) point(ctx, xb, y, c);
    if (r.detenu > 0) {
      ctx.beginPath(); ctx.arc(xa, y, 6.5, 0, 6.2832);
      ctx.lineWidth = 2; ctx.strokeStyle = c; ctx.stroke();
    }
  }
  if (!tip) return;
  const quitter = function () { tipCacher(tip); drawHalteres(cv, null, L, -1); };
  const survol = function (ev) {
    const r = cv.getBoundingClientRect(), py = ev.clientY - r.top, k = Math.floor((py - padT) / H);
    if (k < 0 || k >= L.length) { quitter(); return; }
    drawHalteres(cv, null, L, k);
    const x = L[k], ecart = (x.vise || 0) - (x.detenu || 0);
    tipRemplir(tip, x.ticker, [
      { nom: "vise", valeur: fmtPct(x.vise || 0) },
      { nom: "detenu", valeur: fmtPct(x.detenu || 0) },
      { nom: !x.vise ? "a solder" : !x.detenu ? "a acheter" : ecart > 0 ? "a renforcer" : "a alleger",
        valeur: pts(Math.abs(ecart)) }]);
    tipPoser(tip, S, ev.clientX - r.left, py);
  };
  cv.onmousemove = survol; cv.onmouseleave = quitter; cv.onpointerdown = survol;
}

// ---------------------------------------------------------------------------
// Gain latent par ligne : barres divergentes autour de zero
// ---------------------------------------------------------------------------
function drawLatent(cv, tip, L, dev, survolee) {
  const H = 21, padT = 4, padB = 6, padL = 62, padR = 86;
  const S = surface(cv, padT + L.length * H + padB), ctx = S.ctx;
  if (!L.length) return;
  const x0 = padL, x1 = S.w - padR;
  let lo = 0, hi = 0;
  for (const r of L) { lo = Math.min(lo, r.latent); hi = Math.max(hi, r.latent); }
  if (hi - lo < 1e-9) hi = lo + 1;
  const tx = function (v) { return x0 + (v - lo) / (hi - lo) * (x1 - x0); };
  const cP = cssVar("--pos"), cN = cssVar("--neg");
  for (let k = 0; k < L.length; k++) {
    const r = L[k], y = padT + (k + 0.5) * H;
    if (survolee === k) bandeSurvol(ctx, 2, y - H / 2, S.w - 4, H);
    ctx.fillStyle = cssVar("--ink-2"); ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(r.ticker, x0 - 10, y);
    ctx.fillStyle = r.latent >= 0 ? cP : cN;
    barreH(ctx, y - 6, 12, tx(0), tx(r.latent));
    ctx.fillStyle = cssVar("--ink-2"); ctx.textAlign = "right";
    ctx.fillText(argent(r.latent, dev), S.w - 6, y);
  }
  const z = Math.round(tx(0)) + 0.5;
  ctx.strokeStyle = cssVar("--axis"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(z, padT); ctx.lineTo(z, S.h - padB); ctx.stroke();
  if (!tip) return;
  const quitter = function () { tipCacher(tip); drawLatent(cv, null, L, dev, -1); };
  const survol = function (ev) {
    const r = cv.getBoundingClientRect(), py = ev.clientY - r.top, k = Math.floor((py - padT) / H);
    if (k < 0 || k >= L.length) { quitter(); return; }
    drawLatent(cv, null, L, dev, k);
    const x = L[k], cout = x.valeur - x.latent;
    tipRemplir(tip, x.ticker, [
      { nom: "gain latent", valeur: argent(x.latent, dev), couleur: x.latent >= 0 ? cP : cN },
      { nom: "sur le prix d'achat", valeur: cout > 0 ? fmtSigned(x.latent / cout, 2) : "n/a" },
      { nom: "valeur de la ligne", valeur: montant(x.valeur, dev) }]);
    tipPoser(tip, S, ev.clientX - r.left, py);
  };
  cv.onmousemove = survol; cv.onmouseleave = quitter; cv.onpointerdown = survol;
}

// ---------------------------------------------------------------------------
// Carte des rendements mensuels
// ---------------------------------------------------------------------------
// Echelle DIVERGENTE, bornee au 95e centile des valeurs absolues : sans cette
// borne un seul mois de krach repousserait toutes les autres cases vers le
// gris. Les cases au-dela sont saturees, et la legende le dit.
function grilleMensuelle(m, mode) {
  const G = { annees: [], cases: {}, detail: {}, total: {}, mode: mode, domaine: 0.05 };
  const abs = [];
  for (let k = 0; k < m.mois.length; k++) {
    const an = m.mois[k].slice(0, 4), mo = parseInt(m.mois[k].slice(5, 7), 10) - 1;
    if (!G.cases[an]) {
      G.annees.push(an);
      G.cases[an] = [null, null, null, null, null, null, null, null, null, null, null, null];
      G.detail[an] = [null, null, null, null, null, null, null, null, null, null, null, null];
    }
    const s = m.strategie[k], i = m.indice ? m.indice[k] : null;
    const v = mode === "ecart" ? (propre(s) && propre(i) ? s - i : null) : s;
    G.cases[an][mo] = propre(v) ? v : null;
    G.detail[an][mo] = { s: s, i: i };
    if (propre(v)) abs.push(Math.abs(v));
  }
  for (const an of G.annees) {
    // l'annee se COMPOSE, elle ne s'additionne pas
    let ps = 1, pi = 1, okS = false, okI = true;
    for (const d of G.detail[an]) {
      if (!d) continue;
      if (propre(d.s)) { ps *= 1 + d.s; okS = true; }
      if (propre(d.i)) pi *= 1 + d.i; else okI = false;
    }
    G.total[an] = mode === "ecart" ? (okS && okI ? ps - pi : null) : (okS ? ps - 1 : null);
  }
  abs.sort(function (a, b) { return a - b; });
  const q = abs.length ? abs[Math.min(abs.length - 1, Math.floor(0.95 * abs.length))] : 0.05;
  const lisibles = [0.01, 0.02, 0.03, 0.05, 0.08, 0.1, 0.15, 0.2, 0.3, 0.5];
  G.domaine = lisibles.find(function (p) { return p >= q; }) || q;
  return G;
}
// "-0,0" n'est pas un chiffre : un ecart de -0,01 % s'affiche 0,0.
function caseTexte(v) { return Math.abs(100 * v) < 0.05 ? "0,0" : fmtNum(100 * v, 1); }
// En mode ecart, l'annee est une DIFFERENCE de rendements : des points, pas des %.
function totalTexte(t, mode) {
  if (!propre(t)) return "";
  return mode === "ecart" ? (t > 0 ? "+" : "") + fmtNum(100 * t, 1) + NB + "pts" : fmtSigned(t, 1);
}
function drawCarte(cv, tip, G, survolee) {
  const colAn = 44, colTot = 66, H = 22, padT = 22, padB = 46;
  const S = surface(cv, padT + G.annees.length * H + padB), ctx = S.ctx;
  if (!G.annees.length) return;
  const x0 = colAn, x1 = S.w - colTot, cw = (x1 - x0) / 12;
  const couleur = echelleDivergente(), ecrire = cw >= 34;
  ctx.textBaseline = "middle"; ctx.textAlign = "center"; ctx.fillStyle = cssVar("--muted");
  for (let m = 0; m < 12; m++)
    ctx.fillText(cw >= 34 ? MOIS_COURTS[m] : MOIS_COURTS[m][0].toUpperCase(), x0 + (m + 0.5) * cw, padT / 2);
  ctx.textAlign = "right"; ctx.fillText("annee", S.w - 6, padT / 2);
  for (let a = 0; a < G.annees.length; a++) {
    const an = G.annees[a], y = padT + a * H;
    ctx.fillStyle = cssVar("--ink-2"); ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(an, 2, y + H / 2);
    for (let m = 0; m < 12; m++) {
      const v = G.cases[an][m];
      if (!propre(v)) continue;
      const rgb = couleur(v / G.domaine);
      ctx.fillStyle = rgbCss(rgb);
      ctx.fillRect(x0 + m * cw + 1, y + 1, cw - 2, H - 2);           // 2 px d'air entre les cases
      if (survolee && survolee.a === a && survolee.m === m) {
        ctx.strokeStyle = cssVar("--ink"); ctx.lineWidth = 1.5;
        ctx.strokeRect(x0 + m * cw + 1.75, y + 1.75, cw - 3.5, H - 3.5);
      }
      if (ecrire) {
        ctx.fillStyle = encreSur(rgb); ctx.textAlign = "center";
        ctx.fillText(caseTexte(v), x0 + (m + 0.5) * cw, y + H / 2);
      }
    }
    const t = G.total[an];
    ctx.fillStyle = cssVar("--ink-2"); ctx.textAlign = "right";
    ctx.fillText(totalTexte(t, G.mode), S.w - 6, y + H / 2);
  }
  // legende d'echelle : le degrade lui-meme, echantillonne en OKLab
  const ly = padT + G.annees.length * H + 12, lw = Math.max(120, Math.min(280, x1 - x0));
  for (let k = 0; k < lw; k++) {
    ctx.fillStyle = rgbCss(couleur(2 * k / (lw - 1) - 1));
    ctx.fillRect(x0 + k, ly, 1.5, 9);
  }
  ctx.fillStyle = cssVar("--muted"); ctx.textBaseline = "top";
  const borne = function (v) {
    return G.mode === "ecart" ? (v > 0 ? "+" : "") + fmtNum(100 * v, 0) + NB + "pts" : fmtSigned(v, 0); };
  ctx.textAlign = "left"; ctx.fillText(borne(-G.domaine) + " et moins", x0, ly + 13);
  ctx.textAlign = "center"; ctx.fillText("0", x0 + lw / 2, ly + 13);
  ctx.textAlign = "right"; ctx.fillText(borne(G.domaine) + " et plus", x0 + lw, ly + 13);

  if (!tip) return;
  const quitter = function () { tipCacher(tip); drawCarte(cv, null, G, null); };
  const survol = function (ev) {
    const r = cv.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
    const a = Math.floor((py - padT) / H), m = Math.floor((px - x0) / cw);
    if (a < 0 || a >= G.annees.length || m < 0 || m > 11) { quitter(); return; }
    const an = G.annees[a], v = G.cases[an][m];
    if (!propre(v)) { quitter(); return; }
    drawCarte(cv, null, G, { a: a, m: m });
    const d = G.detail[an][m], L = [];
    if (G.mode === "ecart") {
      L.push({ nom: "ecart a l'indice", valeur: pts(v) });
      L.push({ nom: "strategie", valeur: fmtSigned(d.s, 2) });
      L.push({ nom: "indice", valeur: fmtSigned(d.i, 2) });
    } else {
      L.push({ nom: "strategie", valeur: fmtSigned(v, 2) });
      if (propre(d.i)) L.push({ nom: "indice", valeur: fmtSigned(d.i, 2) });
    }
    tipRemplir(tip, MOIS_LONGS[m] + " " + an, L);
    tipPoser(tip, S, x0 + (m + 0.5) * cw, py);
  };
  cv.onmousemove = survol; cv.onmouseleave = quitter; cv.onpointerdown = survol;
}

// ---------------------------------------------------------------------------
// Vue tableau : chaque graphique a son equivalent lisible
// ---------------------------------------------------------------------------
function buildTable(host, headers, rows, classer) {
  let h = "<table><thead><tr>";
  for (const c of headers) h += "<th>" + c + "</th>";
  h += "</tr></thead><tbody>";
  for (const r of rows) {
    h += "<tr" + (r._total ? ' class="total"' : "") + ">";
    r.cells.forEach(function (c, i) {
      const cls = classer ? classer(c, i, r) : "";
      h += "<td" + (cls ? ' class="' + cls + '"' : "") + ">" + c + "</td>";
    });
    h += "</tr>";
  }
  h += "</tbody></table>";
  // Idempotent : une table reconstruite a l'identique perdrait sa position de
  // defilement et la selection de l'utilisateur a chaque rafraichissement.
  if (host.innerHTML !== h) host.innerHTML = h;
}
"""


APP = r"""
// ---------------------------------------------------------------------------
// Etat et rendu
// ---------------------------------------------------------------------------
const state = Object.assign({}, BOOT.values);
let current = null, pending = 0;
// Ce que chaque test a renvoye : sert a redessiner apres un changement de
// niveau de lecture, et a alimenter la carte "En resume".
const DERNIER = { dec: null, deciles: null, ic: null, "null": null };
const REP = { deciles: null, hasard: null };

function post(path, body) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                       body: JSON.stringify(body) })
    .then(function (r) { return r.text().then(function (t) {
      if (!r.ok) throw new Error(t || ("HTTP " + r.status));
      return JSON.parse(t); }); });
}

// -- mode statique : on retrouve la variante precalculee ---------------------
function staticKey() {
  const caps = GRID ? GRID.top_n_values : [];
  let n = state.top_n, best = caps[0];
  for (const c of caps) if (Math.abs(c - n) < Math.abs(best - n)) best = c;
  return best + "|" + state.weighting + "|" + (state.regime ? 1 : 0);
}
function sliceFrom(dates, values, start) {
  if (!start) return { dates: dates.slice(), values: values.slice() };
  let i = 0; while (i < dates.length && dates[i] < start) i++;
  if (i >= dates.length - 1) i = 0;
  const base = values[i];
  return { dates: dates.slice(i),
           values: values.slice(i).map(function (v) { return v === null ? null : +(v / base * 100).toFixed(2); }) };
}
function staticRun() {
  const v = GRID.variants[staticKey()];
  const pid = state.start || "full";
  const strat = sliceFrom(GRID.dates, v.curve, state.start);
  const univ = sliceFrom(GRID.dates, GRID.reference.univers, state.start);
  const ind = sliceFrom(GRID.dates, GRID.reference.indice, state.start);
  let mensuels = null;
  const M = GRID.reference.mensuels;
  if (M && v.mensuels) {
    const deb = (state.start || "").slice(0, 7), k = [];
    for (let j = 0; j < M.mois.length; j++) if (!deb || M.mois[j] >= deb) k.push(j);
    const pris = function (a) { return k.map(function (j) { return a ? a[j] : null; }); };
    mensuels = { mois: pris(M.mois), strategie: pris(v.mensuels), univers: pris(M.univers), indice: pris(M.indice) };
  }
  return { dates: strat.dates, n_assets: GRID.n_assets, duree_ms: 0,
           stats: v.periods[pid] || {}, stats_univers: (GRID.reference.periods[pid] || {}),
           series: { strategie: strat.values, univers: univ.values, indice: ind.values },
           mensuels: mensuels };
}

// -- drawdown calcule cote client, identique dans les deux modes -------------
function toDrawdown(values) {
  let peak = -Infinity;
  return values.map(function (v) {
    if (v === null || !isFinite(v)) return null;
    if (v > peak) peak = v;
    return +((v / peak - 1) * 100).toFixed(3);
  });
}

// Chaque mesure porte un libelle en francais courant, son nom technique en
// second, et une phrase qui dit ce qu'elle veut dire. Le meme dictionnaire
// alimente les tuiles et le glossaire : une seule definition a maintenir.
const AIDE = {
  cagr: ["Performance annuelle", "CAGR",
    "Ce que le portefeuille gagne en moyenne chaque annee, une fois les bonnes et les mauvaises annees lissees."],
  max_drawdown: ["Pire chute", "perte maximale",
    "La baisse la plus violente entre un sommet et le creux qui a suivi. -30 % veut dire qu'il faut regagner 43 % pour revenir au sommet."],
  sharpe: ["Rendement par unite de risque", "Sharpe",
    "Le rendement rapporte a l'agitation subie pour l'obtenir. Au-dessus de 1 c'est tres bon, autour de 0,5 c'est mediocre. Deux strategies au meme rendement ne se valent pas si l'une secoue deux fois plus."],
  annual_turnover: ["Rotation", "turnover",
    "Part du portefeuille remplacee chaque annee. 500 % veut dire que tout est change cinq fois par an : chaque changement coute des frais."],
  beta: ["Sensibilite au marche", "beta",
    "1 = le portefeuille bouge comme le marche. 0,5 = deux fois moins. Un beta bas n'est ni bon ni mauvais en soi, il dit seulement qu'on est moins expose."],
  alpha_annual: ["Surperformance propre", "alpha",
    "Ce qui reste du rendement une fois retire ce que l'exposition au marche explique deja. C'est censement l'apport de la strategie elle-meme."],
  information_ratio: ["Regularite de la surperformance", "ratio d'information",
    "L'alpha rapporte au risque supplementaire pris pour l'obtenir. Negatif : on s'ecarte de l'indice et on y perd."],
  hit_rate_monthly: ["Mois gagnants", null,
    "Part des mois qui finissent en hausse. Utile pour savoir si la performance vient de beaucoup de petits gains ou de quelques coups."],
  volatility: ["Agitation", "volatilite",
    "De combien la valeur du portefeuille bouge d'un jour a l'autre, exprimee en rythme annuel."],
  tracking_error: ["Ecart a l'indice", "tracking error",
    "De combien la strategie s'ecarte de l'indice. Plus il est grand, plus on parie contre le marche - et plus il faut que ca rapporte."]
};

const GLOSSAIRE_SUP = {
  "Univers entier": "Acheter tous les titres disponibles a parts egales, sans en choisir aucun, et ne plus y toucher. C'est le vrai concurrent d'une strategie de selection : s'il fait mieux, la selection ne sert a rien.",
  "Biais du survivant": "La liste des titres testes est celle des societes qui font partie de l'indice AUJOURD'HUI. Les faillites et les sorties de cote en sont absentes. Un backtest ne voit donc que des gagnantes, ce qui gonfle tous les rendements de 1 a 4 points par an - et bien davantage pour une strategie qui penche vers les titres extremes.",
  "Decile": "Un dixieme du classement. On range les titres par note et on coupe en dix paquets de taille egale : le decile 1 contient les moins bien notes, le decile 10 les mieux notes.",
  "t (statistique)": "Mesure a quel point un ecart observe depasse ce que le hasard produirait. Au-dela de 2 en valeur absolue, on considere l'ecart credible ; en dessous, il peut tres bien n'etre que du bruit.",
  "Rebalancement": "Le moment ou l'on recalcule le portefeuille et passe les ordres. Ici, une fois par mois. Entre deux rebalancements, on ne touche a rien et les poids derivent avec les cours.",
  "Delai d'execution": "Le nombre de jours entre le calcul du signal et le passage des ordres. Il vaut 1 : on decide apres la cloture, on execute le lendemain. Sans ce delai, un backtest s'attribue des rendements qu'il n'aurait pas pu capter.",
  "Filtre de regime": "Une regle qui sort entierement du marche quand l'indice passe sous sa moyenne des 200 derniers jours. C'est une assurance : elle protege des grandes baisses et coute du rendement le reste du temps.",
  "Ponderation inverse-vol": "Donner plus de poids aux titres les moins agites. L'idee est de lisser le portefeuille ; l'effet secondaire est de sous-ponderer les titres qui montent le plus.",
  "Momentum": "L'idee que les titres qui ont monte sur les douze derniers mois continuent a monter. On saute le dernier mois, ou l'effet s'inverse souvent.",
  "Backtest": "Rejouer la strategie sur le passe, jour par jour, en n'utilisant a chaque date que les informations disponibles a cette date. C'est une simulation, pas une preuve."
};

function tile(cle, valeur, comparaison, avance) {
  const a = AIDE[cle] || [cle, null, ""];
  const tech = a[1] ? '<abbr title="' + a[2].replace(/"/g, "&quot;") + '">' + a[1] + "</abbr>" : "";
  return '<div class="tile' + (avance ? " avance" : "") + '"><div class="k">' + a[0] + tech +
         '</div><div class="v">' + valeur + '</div><div class="c">' + (comparaison || "&nbsp;") +
         '</div><div class="h hint-simple">' + a[2] + "</div></div>";
}

function paint(d) {
  current = d;
  const s = d.stats, u = d.stats_univers || {};
  const C = { s1: cssVar("--series-1"), s2: cssVar("--series-2"), s3: cssVar("--series-3") };

  const cf = function (x) { return "sans selection <i>" + x + "</i>"; };
  el("tiles").innerHTML =
    tile("cagr", fmtPct(s.cagr), cf(fmtPct(u.cagr))) +
    tile("max_drawdown", fmtPct(s.max_drawdown), cf(fmtPct(u.max_drawdown))) +
    tile("sharpe", fmtNum(s.sharpe), cf(fmtNum(u.sharpe))) +
    tile("annual_turnover", fmtPct(s.annual_turnover, 0), cf(fmtPct(u.annual_turnover, 0))) +
    tile("beta", fmtNum(s.beta), "par rapport a l'indice", true) +
    tile("alpha_annual", fmtPct(s.alpha_annual), "ecart a l'indice <i>" + fmtPct(s.tracking_error) + "</i>", true) +
    tile("information_ratio", fmtNum(s.information_ratio), "alpha rapporte au risque actif", true) +
    tile("hit_rate_monthly", fmtPct(s.hit_rate_monthly, 0), fmtNum(s.years, 1) + " ans", true);

  const lines = [
    { name: "strategie", values: d.series.strategie, color: C.s1 },
    { name: "univers entier", values: d.series.univers, color: C.s2 },
  ];
  if (d.series.indice && d.series.indice.length) lines.push({ name: "indice", values: d.series.indice, color: C.s3 });
  el("legend-perf").innerHTML = lines.map(function (l) {
    return '<span><i style="background:' + l.color + '"></i>' + l.name + "</span>"; }).join("");

  drawLines(el("cv-perf"), el("tip-perf"), {
    dates: d.dates, series: lines, log: true, height: 320,
    yFmt: function (v) { return fmtNum(v, 0); },
    tipFmt: function (v) { return fmtNum(v, 1); } });

  const ddLines = [
    { name: "strategie", values: toDrawdown(d.series.strategie), color: C.s1 },
    { name: "univers entier", values: toDrawdown(d.series.univers), color: C.s2 },
  ];
  if (d.series.indice && d.series.indice.length)
    ddLines.push({ name: "indice", values: toDrawdown(d.series.indice), color: C.s3 });
  el("legend-dd").innerHTML = el("legend-perf").innerHTML;
  drawLines(el("cv-dd"), el("tip-dd"), {
    dates: d.dates, series: ddLines, height: 210, zeroTop: true,
    yFmt: function (v) { return fmtNum(v, 0) + "%"; },
    tipFmt: function (v) { return fmtNum(v, 1) + " %"; } });

  // vue tableau : une ligne par annee civile
  const rows = [], seen = {};
  for (let i = 0; i < d.dates.length; i++) {
    const y = d.dates[i].slice(0, 4);
    seen[y] = { d: d.dates[i], a: d.series.strategie[i], b: d.series.univers[i],
                c: d.series.indice ? d.series.indice[i] : null };
  }
  Object.keys(seen).sort().forEach(function (y) {
    const r = seen[y];
    rows.push({ cells: [y, fmtNum(r.a, 1), fmtNum(r.b, 1), r.c === null ? "n/a" : fmtNum(r.c, 1)] });
  });
  buildTable(el("tv-perf"), ["Annee", "Strategie", "Univers entier", "Indice"], rows);

  // lecture en clair
  const dS = (s.sharpe || 0) - (u.sharpe || 0);
  const ir = s.information_ratio;
  let cls = "flat", txt = "";
  if (dS > 0.10 && ir > 0.2) { cls = "good";
    txt = "La strategie fait mieux que la detention de l'univers entier (Sharpe " + fmtNum(s.sharpe) +
          " contre " + fmtNum(u.sharpe) + ") avec un ratio d'information de " + fmtNum(ir) + "." +
          " A confirmer hors echantillon avant d'y croire."; }
  else if (dS < -0.05) { cls = "bad";
    txt = "Detenir l'univers entier, sans aucune selection, fait mieux : Sharpe " + fmtNum(u.sharpe) +
          " contre " + fmtNum(s.sharpe) + ". La machinerie retranche de la valeur au lieu d'en ajouter," +
          " et elle paie " + fmtPct(s.annual_turnover, 0) + " de rotation par an pour cela."; }
  else { txt = "Strategie et univers entier sont au coude a coude (Sharpe " + fmtNum(s.sharpe) +
          " contre " + fmtNum(u.sharpe) + "). Une selection qui n'ecarte pas son propre univers ne" +
          " selectionne rien : lance le test du hasard plus bas pour trancher."; }
  el("verdict").className = "verdict " + cls;
  el("verdict").textContent = txt;
  renderGlissant(d);
  renderCarte(d);
  majResume();

  el("meta").textContent = d.n_assets + " titres" + (d.duree_ms ? " · recalcul " + d.duree_ms + " ms" : "");
  document.querySelectorAll(".busy").forEach(function (n) { n.classList.remove("busy"); });
}

function refresh() {
  document.querySelectorAll("#panels .card").forEach(function (n) { n.classList.add("busy"); });
  if (MODE === "static") {
    // Les sections a la demande dependent de la periode : on les replie plutot
    // que de laisser a l'ecran un resultat calcule sur une autre fenetre.
    ["dec", "deciles", "ic", "null"].forEach(function (k) {
      el(k + "-body").hidden = true; el(k + "-msg").textContent = ""; DERNIER[k] = null; });
    REP.deciles = null; REP.hasard = null;
    paint(staticRun()); return;
  }
  const mine = ++pending;
  post("/api/run", state).then(function (d) { if (mine === pending) paint(d); })
    .catch(function (e) { el("error").textContent = "Erreur : " + e.message; el("error").className = "msg err"; });
}

// ---------------------------------------------------------------------------
// Sections a la demande
// ---------------------------------------------------------------------------
function renderDecomposition(d) {
  const rows = d.lignes;
  const steps = [];
  rows.forEach(function (r, i) {
    if (i === 0) steps.push({ label: r.etape, value: r.cagr, total: true });
    else if (i === rows.length - 1) { steps.push({ label: r.etape, value: r.delta_cagr });
                                      steps.push({ label: "strategie complete", value: r.cagr, total: true }); }
    else steps.push({ label: r.etape, value: r.delta_cagr });
  });
  el("dec-body").hidden = false;   // avant de dessiner : un canvas dans un
  drawWaterfall(el("cv-dec"), null, steps);   // conteneur masque a une largeur nulle
  buildTable(el("tv-dec"), ["Etape", "CAGR", "Sharpe", "Perte max", "Ecart CAGR", "Ecart Sharpe"],
    rows.map(function (r, i) {
      return { _total: i === 0 || i === rows.length - 1,
               cells: [r.etape, fmtPct(r.cagr), fmtNum(r.sharpe), fmtPct(r.max_drawdown),
                       r.delta_cagr === null ? "—" : fmtSigned(r.delta_cagr),
                       r.delta_sharpe === null ? "—" : (r.delta_sharpe >= 0 ? "+" : "") + fmtNum(r.delta_sharpe)] };
    }),
    function (c, i) { if (i < 4 || c === "—") return ""; return c.indexOf("+") === 0 ? "pos" : (c.indexOf("-") === 0 ? "neg" : ""); });
}

function renderIC(d) {
  const rows = d.lignes;
  el("ic-body").hidden = false;
  drawTBars(el("cv-ic"), rows.map(function (r) { return { label: r.facteur, t: r.t_stat }; }));
  buildTable(el("tv-ic"), ["Facteur", "IC moyen", "IC median", "t", "p", "% dates > 0", "n"],
    rows.map(function (r) {
      return { cells: [r.facteur, fmtNum(r.ic_moyen, 4), fmtNum(r.ic_median, 4), fmtNum(r.t_stat),
                       fmtNum(r.p_value, 3), fmtPct(r.part_positive, 0), r.n_dates] };
    }));
  const best = rows.reduce(function (a, b) { return Math.abs(b.t_stat) > Math.abs(a.t_stat) ? b : a; }, rows[0]);
  el("ic-verdict").className = "verdict " + (Math.abs(best.t_stat) > 2 ? "good" : "flat");
  el("ic-verdict").textContent = Math.abs(best.t_stat) > 2
    ? "Le facteur le plus net (" + best.facteur + ") atteint t = " + fmtNum(best.t_stat) +
      " : il y a la un signal, faible mais mesurable."
    : "Aucun facteur n'atteint |t| = 2 (le plus net est " + best.facteur + " a t = " + fmtNum(best.t_stat) +
      "). Sur cet univers et cette periode, ces scores ne se distinguent pas d'un classement au hasard.";
}

function renderNull(d) {
  const dist = d.distribution.sharpe;
  el("null-body").hidden = false;
  drawHist(el("cv-null"), dist.valeurs, d.reel.sharpe, "reel " + fmtNum(d.reel.sharpe));
  const keys = [["sharpe", "Sharpe", false], ["cagr", "CAGR", true],
                ["max_drawdown", "Perte maximale", true], ["annual_turnover", "Rotation", true]];
  buildTable(el("tv-null"), ["Mesure", "Reel", "Hasard : moyenne", "p5", "Mediane", "p95", "Centile du reel"],
    keys.filter(function (k) { return d.distribution[k[0]]; }).map(function (k) {
      const x = d.distribution[k[0]], f = k[2] ? function (v) { return fmtPct(v); } : function (v) { return fmtNum(v, 3); };
      return { cells: [k[1], f(d.reel[k[0]]), f(x.moyenne), f(x.p05), f(x.median), f(x.p95),
                       Math.round(x.centile_reel) + "e"] };
    }));
  const p = d.p_hasard_fait_mieux;
  el("null-verdict").className = "verdict " + (p < 0.25 ? "good" : p > 0.5 ? "bad" : "flat");
  // Battre un tirage au sort n'est pas savoir classer : le hasard choisit des
  // titres moyens, un score qui vise les extremes le bat sans rien classer.
  const piege = (REP.deciles === false && p < 0.25)
    ? " Attention : ce resultat ne suffit pas a conclure. Un tirage au sort choisit des " +
      "titres moyens ; un score qui vise les titres les plus agites le bat sans savoir " +
      "classer pour autant. Le test 4 ci-dessus est celui qui tranche, et il dit non."
    : "";
  el("null-verdict").textContent =
    d.n_draws + " tirages, persistance de rang reproduite (rho = " + fmtNum(d.rho) + "). " +
    "Le Sharpe reel se situe au " + Math.round(dist.centile_reel) + "e centile du hasard, soit " +
    fmtNum(d.z_sharpe) + " ecart-type. Un score SANS aucune information fait mieux dans " +
    fmtPct(p, 0) + " des tirages" +
    (p > 0.5 ? " : ce score ne selectionne rien, il paie des frais." :
     p < 0.25 ? " : le score apporte quelque chose que le hasard n'apporte pas." :
                " : indiscernable du hasard.") + piege;
  REP.hasard = p < 0.25 && REP.deciles !== false;
  majResume();
}


function renderDeciles(d) {
  el("deciles-body").hidden = false;
  if (!d.spread) { el("deciles-msg").textContent = "Pas assez de dates sur cette periode."; return; }
  drawDeciles(el("cv-deciles"), d);
  const lignes = d.deciles.map(function (x) {
    return { cells: ["decile " + x.rang + (x.rang === 1 ? " (pires)" : x.rang === d.n_buckets ? " (meilleurs)" : ""),
                     fmtPct(x.rendement, 3), fmtSigned(x.ecart, 3), fmtNum(x.t_stat)] };
  });
  lignes.push({ _total: true, cells: ["les " + d.top_n + " meilleurs scores", fmtPct(d.meilleurs.rendement, 3),
                                      fmtSigned(d.meilleurs.ecart, 3), fmtNum(d.meilleurs.t_stat)] });
  lignes.push({ _total: true, cells: ["les " + d.top_n + " pires scores", fmtPct(d.pires.rendement, 3),
                                      fmtSigned(d.pires.ecart, 3), fmtNum(d.pires.t_stat)] });
  lignes.push({ cells: ["univers entier", fmtPct(d.univers, 3), "—", "—"] });
  buildTable(el("tv-deciles"), ["Groupe", "Rendement/mois", "Ecart a l'univers", "t"], lignes);

  const inv = d.inverse;
  buildTable(el("tv-inverse"), ["Meme moteur, score...", "CAGR", "Sharpe", "Perte max"],
    [{ cells: ["score normal (meilleurs scores)", fmtPct(inv.normal.cagr), fmtNum(inv.normal.sharpe), fmtPct(inv.normal.max_drawdown)] },
     { cells: ["score INVERSE (pires scores)", fmtPct(inv.inverse.cagr), fmtNum(inv.inverse.sharpe), fmtPct(inv.inverse.max_drawdown)] },
     { cells: ["univers entier, sans selection", fmtPct(inv.univers.cagr), fmtNum(inv.univers.sharpe), fmtPct(inv.univers.max_drawdown)] }]);

  const t = d.spread.t_stat, fort = Math.abs(t) > 2;
  el("deciles-verdict").className = "verdict " + (fort && t > 0 ? "good" : "bad");
  el("deciles-verdict").textContent = fort && t > 0
    ? "Les meilleurs scores battent les pires de " + fmtSigned(d.spread.moyenne, 3) + " par mois (t = " +
      fmtNum(t) + ") : le score classe reellement, l'ecart n'est pas un accident."
    : "L'ecart entre les meilleurs et les pires scores est de " + fmtSigned(d.spread.moyenne, 3) +
      " par mois, t = " + fmtNum(t) + " : il n'est pas significatif. Le score ne CLASSE pas. " +
      "Si les deux bouts du classement battent le milieu, un top " + d.top_n + " brillant ne prouve rien : " +
      "il selectionne des titres extremes, pas des gagnants — et sur un univers de survivants, les " +
      "extremes qui ont survecu sont par construction ceux qui sont montes. Le tableau ci-dessous " +
      "tranche : avec le score retourne, la strategie fait " + fmtPct(inv.inverse.cagr) + " contre " +
      fmtPct(inv.normal.cagr) + ".";
  REP.deciles = fort && t > 0;
  majResume();
}

function onDemand(id, path, body, render, label) {
  const btn = el(id + "-btn"), msg = el(id + "-msg");
  if (MODE === "static") {
    const data = PRECOMPUTED[id] ? PRECOMPUTED[id][state.start || "full"] : null;
    if (!data) { msg.textContent = "Cette mesure n'a pas ete precalculee pour cette periode. " +
      "Le fichier autonome ne peut pas la recalculer : lance le tableau de bord en direct " +
      "(python scripts/dashboard.py) pour l'obtenir."; return Promise.resolve(); }
    DERNIER[id] = data; render(data); msg.textContent = "";
    return Promise.resolve();
  }
  btn.disabled = true; msg.textContent = label; msg.className = "msg";
  return post(path, body()).then(function (d) { DERNIER[id] = d; render(d); msg.textContent = ""; })
    .catch(function (e) { msg.textContent = "Erreur : " + e.message; msg.className = "msg err"; })
    .then(function () { btn.disabled = false; });
}

// ---------------------------------------------------------------------------
// Cablage
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Carte "En resume" : trois questions, dans l'ordre ou elles comptent.
// ---------------------------------------------------------------------------
function ligneQR(etat, question, reponse) {
  const p = etat === null ? '<span class="pastille attente">?</span>'
          : etat ? '<span class="pastille oui">✓</span>'
                 : '<span class="pastille non">✕</span>';
  return '<div class="qr">' + p + '<div><div class="q">' + question +
         '</div><div class="r">' + reponse + "</div></div></div>";
}

function majResume() {
  if (!current) return;
  const s = current.stats, u = current.stats_univers || {};
  const aIndice = s.benchmark_cagr !== null && s.benchmark_cagr !== undefined;

  const q1 = aIndice ? (s.cagr > s.benchmark_cagr && s.sharpe > s.benchmark_sharpe) : null;
  const r1 = aIndice
    ? "La strategie rapporte " + fmtPct(s.cagr) + " par an contre " + fmtPct(s.benchmark_cagr) +
      " pour l'indice, avec un rendement par unite de risque de " + fmtNum(s.sharpe) +
      " contre " + fmtNum(s.benchmark_sharpe) + "."
    : "Aucun indice de reference n'est charge.";

  const q2 = (s.sharpe > u.sharpe + 0.03) ? true : (s.sharpe < u.sharpe - 0.03 ? false : null);
  const r2 = "Acheter les " + current.n_assets + " titres a parts egales, sans rien choisir, donne " +
    fmtPct(u.cagr) + " par an (" + fmtNum(u.sharpe) + " par unite de risque) contre " +
    fmtPct(s.cagr) + " (" + fmtNum(s.sharpe) + ") pour la strategie. " +
    "C'est la comparaison qui compte : les deux subissent le meme defaut de donnees.";

  let q3 = null;
  const faits = [REP.deciles, REP.hasard].filter(function (x) { return x !== null; });
  if (faits.length) q3 = faits.every(function (x) { return x === true; });
  const r3 = faits.length === 0
    ? "Pas encore mesure. Les tests 4 et 5 plus bas y repondent en quelques secondes."
    : (q3 ? "Le score resiste aux tests : ses meilleures notes battent ses pires notes, et il fait mieux qu'un tirage au sort."
          : "Les tests disent non. Selon les cas : les meilleures notes ne battent pas les pires, ou un score tire au hasard fait aussi bien.");

  el("resume-questions").innerHTML =
    ligneQR(q1, "1. Fait-elle mieux qu'un placement indiciel ?", r1) +
    ligneQR(q2, "2. Fait-elle mieux que d'acheter tout sans reflechir ?", r2) +
    ligneQR(q3, "3. Le score contient-il vraiment de l'information ?", r3);

  let titre, conclusion;
  if (q3 === false) {
    titre = "Pas d'avantage demontrable.";
    conclusion = "C'est la reponse a la question 3 qui commande, pas les deux autres. " +
      "Une belle courbe sans signal, c'est de la chance ou un artefact des donnees : rien " +
      "qui se reproduira sur l'argent reel. En l'etat, il n'y a rien a exploiter ici - ce " +
      "qui est une information utile, pas un echec.";
  } else if (q3 === true) {
    titre = q2 === false ? "Un signal reel, mais qui ne suffit pas encore."
                         : "Un avantage qui resiste aux trois tests.";
    conclusion = q2 === false
      ? "Le score porte de l'information, mais la strategie construite autour ne bat pas " +
        "encore la simple detention de l'univers. Le probleme est dans la construction du " +
        "portefeuille - ponderation, nombre de lignes, filtre de marche - pas dans le signal."
      : "C'est le meilleur cas possible a ce stade. Prochaine etape : verifier que ca tient " +
        "hors echantillon (walk_forward.py), puis en conditions reelles sans engager d'argent " +
        "pendant plusieurs mois. Un backtest, meme propre, reste une simulation.";
  } else {
    titre = q2 === false ? "Pour l'instant, ne rien faire fait mieux."
          : q2 === true ? "Sur le papier, elle prend l'avantage. Reste a savoir si c'est reel."
          : "Au coude a coude avec la detention passive.";
    conclusion = "Les deux premieres reponses ne veulent rien dire tant que la troisieme est " +
      "inconnue : une strategie peut tres bien afficher une belle performance sans contenir " +
      "la moindre information. Lance les tests avec le bouton ci-dessus.";
  }
  el("resume-titre").textContent = titre;
  el("resume-conclusion").textContent = conclusion;
}

el("tout-btn").onclick = function () {
  const b = el("tout-btn"), m = el("tout-msg");
  b.disabled = true; m.className = "msg";
  m.textContent = MODE === "static" ? "" : "Calcul en cours, le test du hasard prend le plus de temps...";
  el("dec-btn").onclick();
  Promise.resolve()
    .then(function () { return el("deciles-btn").onclick(); })
    .then(function () { return el("null-btn").onclick(); })
    .then(function () { m.textContent = ""; b.disabled = false; });
};

// ---------------------------------------------------------------------------
// Glossaire : le meme dictionnaire que les tuiles, plus les notions de fond.
// ---------------------------------------------------------------------------
function renderGlossaire() {
  let h = "";
  for (const k in AIDE) {
    const a = AIDE[k];
    h += "<div><dt>" + a[0] + (a[1] ? " <span style='color:var(--muted);font-weight:400'>(" + a[1] + ")</span>" : "") +
         "</dt><dd>" + a[2] + "</dd></div>";
  }
  for (const k in GLOSSAIRE_SUP) h += "<div><dt>" + k + "</dt><dd>" + GLOSSAIRE_SUP[k] + "</dd></div>";
  el("glossaire").innerHTML = h;
}

// ---------------------------------------------------------------------------
// Niveau de lecture. Les canvas caches ont une largeur nulle : on redessine
// tout ce qui a deja ete calcule apres avoir change de niveau.
// ---------------------------------------------------------------------------
function setNiveau(n) {
  document.body.dataset.niveau = n;
  el("niv-simple").setAttribute("aria-pressed", n === "simple");
  el("niv-complet").setAttribute("aria-pressed", n === "complet");
  try { localStorage.setItem("quantbot.niveau", n); } catch (e) {}
  if (current) paint(current);
  if (DERNIER.dec) renderDecomposition(DERNIER.dec);
  if (DERNIER.deciles) renderDeciles(DERNIER.deciles);
  if (DERNIER.ic) renderIC(DERNIER.ic);
  if (DERNIER["null"]) renderNull(DERNIER["null"]);
}
el("niv-simple").onclick = function () { setNiveau("simple"); };
el("niv-complet").onclick = function () { setNiveau("complet"); };
let niveau0 = "simple";
try { niveau0 = localStorage.getItem("quantbot.niveau") || "simple"; } catch (e) {}
renderGlossaire();
setNiveau(niveau0);

// ---------------------------------------------------------------------------
// Vue "Operations" : etat du compte, plan d'ordres, envoi, executions.
// Compte de SIMULATION uniquement - un clic n'a pas le poids d'une phrase
// tapee a la main, donc l'argent reel reste sur la ligne de commande.
// ---------------------------------------------------------------------------
let opsCharge = false, opsEtatCourant = null, opsFetchTimer = null;

function tuile(k, v, c) {
  return '<div class="tile"><div class="k">' + k + '</div><div class="v">' + v +
         '</div><div class="c">' + (c || "&nbsp;") + "</div></div>";
}
function devise(x, d) {
  if (x === null || x === undefined || !isFinite(x)) return "n/a";
  return x.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + (d || "");
}

// Ecrit dans le DOM uniquement si le contenu differe. Reassigner `innerHTML`
// a l'identique detruit et reconstruit le sous-arbre : la position de
// defilement saute, la selection de texte disparait, et l'oeil percoit un
// clignotement. Sur un ecran qui se rafraichit tout seul c'est la difference
// entre "vivant" et "agite".
function poser(noeud, html, signaler) {
  if (!noeud || noeud.innerHTML === html) return false;
  noeud.innerHTML = html;
  if (signaler) { noeud.classList.remove("change"); void noeud.offsetWidth;
                  noeud.classList.add("change"); }
  return true;
}
function poserTexte(noeud, texte) {
  if (noeud && noeud.textContent !== texte) noeud.textContent = texte;
}

function renderOps(d) {
  opsEtatCourant = d;
  if (!d.connecte) {
    el("ops-erreur").className = "msg err";
    el("ops-erreur").textContent = "Courtier injoignable : " + (d.erreur || "");
    el("ops-titre").textContent = "Non connecte";
    return;
  }
  el("ops-erreur").textContent = "";
  const c = d.compte, dev = c.devise;

  poser(el("ops-tuiles"),
    tuile("Valeur du compte", devise(c.equity, dev), "compte " + c.numero) +
    tuile("Liquidites", devise(c.liquidites, dev),
          d.marche_ouvert ? "marche ouvert" : "marche ferme") +
    tuile("Positions", String(d.positions.length),
          "cible : " + d.cible.length + " ligne(s)") +
    tuile("Ordres a passer", String(d.ordres.length),
          d.ordres.length ? fmtPct(d.resume.echange / c.equity, 1) + " du compte" : "rien a faire"),
    true);

  poser(el("ops-controles"), d.controles.map(function (x) {
    const p = x.ok ? '<span class="pastille oui">✓</span>' : '<span class="pastille non">✕</span>';
    return '<div class="controle">' + p + '<div><div class="q">' + x.nom +
           '</div><div class="r">' + x.detail + "</div></div></div>";
  }).join(""));

  const bloquants = d.controles.filter(function (x) { return !x.ok; });
  poserTexte(el("ops-titre"), d.ordres.length === 0
    ? "Portefeuille aligne, aucun ordre a passer."
    : (bloquants.length === 0
        ? d.ordres.length + " ordre(s) prets a partir."
        : d.ordres.length + " ordre(s) calcules, " + bloquants.length + " controle(s) bloquant(s)."));

  if (d.ordres.length) {
    buildTable(el("ops-ordres"),
      ["Ticker", "Sens", "Titres", "Montant", "Actuel (cache)", "Cible", "Motif"],
      d.ordres.map(function (o) {
        return { cells: [o.ticker, o.sens === "sell" ? "VENTE" : "ACHAT",
                         o.quantite === null ? "au montant" : fmtNum(o.quantite, 4),
                         devise(o.montant, ""), fmtPct(o.poids_actuel), fmtPct(o.poids_cible),
                         o.motif] };
      }));
  } else {
    poser(el("ops-ordres"), '<div class="msg">Aucun ecart superieur au seuil de ' +
      devise(d.seuil, dev) + " (0,25 % du compte).</div>");
  }

  buildTable(el("ops-positions"), ["Ticker", "Titres", "Valeur (marche)", "Poids", "Latent"],
    d.positions.map(function (p) {
      return { cells: [p.ticker, fmtNum(p.titres, 3), devise(p.valeur, ""),
                       fmtPct(p.poids), devise(p.latent, "")] };
    }));
  buildTable(el("ops-cible"), ["Ticker", "Poids vise", "Cours", "Detenu"],
    d.cible.map(function (t) {
      return { cells: [t.ticker, fmtPct(t.poids), fmtNum(t.cours, 2), fmtNum(t.detenu, 3)] };
    }));

  poserTexte(el("meta"), d.donnees.derniere_cloture + " · " + d.regime.texte);
  renderPortefeuilleGraphes(d);
}

// ---------------------------------------------------------------------------
// Graphiques du compte : le defi, le jour, le portefeuille
// ---------------------------------------------------------------------------
let opsHisto = null;

// Tout ce que les deux graphiques du defi dessinent, calcule une fois. Les
// planchers suivent la regle de `defi.evaluer` : fixes en reference
// statique, en escalier sous le plus haut en reference glissante.
function deriverDefi(h) {
  const f = h.defi || {}, n = h.dates.length;
  const depart = f.depart || (n ? h.equity[0] : 1);
  let base = null;
  for (const v of h.indice || []) if (propre(v)) { base = v; break; }
  const glissant = f.reference === "glissante";
  let haut = depart;
  if (glissant && f.plus_haut && f.plus_haut > Math.max.apply(null, h.equity)) haut = f.plus_haut;
  const pBot = [], pCon = [];
  for (let i = 0; i < n; i++) {
    if (glissant) haut = Math.max(haut, h.equity[i]);
    const ref = glissant ? haut : depart;
    pBot.push(ref * (1 - (f.perte_totale_max || 0)) / depart - 1);
    pCon.push(ref * (1 - (f.contrat_total || 0.10)) / depart - 1);
  }
  return {
    dates: h.dates, equity: h.equity, depart: depart,
    compte: h.equity.map(function (e) { return e / depart - 1; }),
    indice: (h.indice || []).map(function (v) { return propre(v) && base ? v / base - 1 : null; }),
    indiceNom: h.indice_nom, jour: h.perte_jour || [],
    devise: opsEtatCourant && opsEtatCourant.compte ? opsEtatCourant.compte.devise : "USD",
    actif: !!f.actif && propre(f.perte_totale_max),
    objectif: f.objectif || 0, objectifs: h.dates.map(function () { return f.objectif || 0; }),
    plancherBot: pBot, plancherContrat: pCon,
    perteJourMax: f.perte_jour_max || 0, contratJour: f.contrat_jour || 0.05,
    verrou: f.verrou || null,
  };
}

function renderDefi(h) {
  opsHisto = h;
  const msg = el("ops-defi-msg"), corps = el("ops-defi-corps");
  if (!h || !h.ok || !h.dates || !h.dates.length) {
    corps.hidden = true; msg.className = "msg";
    poserTexte(msg, "Pas encore de courbe : " + ((h && h.raison) || "historique indisponible") + ".");
    return;
  }
  const D = deriverDefi(h), n = D.dates.length, fin = D.compte[n - 1];
  corps.hidden = false;
  if (D.verrou) {
    msg.className = "msg err";
    poserTexte(msg, "Verrou pose le " + D.verrou.depuis + " : " + (D.verrou.detail || D.verrou.raison) +
               ". Le bot n'achete plus rien tant qu'il n'est pas leve a la main.");
  } else { msg.className = "msg"; poserTexte(msg, ""); }
  poserTexte(el("ops-defi-titre"), D.actif ? "Le defi, seance par seance" : "Le compte, seance par seance");

  let pire = null, iPire = -1;
  for (let i = 0; i < n; i++) if (propre(D.jour[i]) && (pire === null || D.jour[i] < pire)) { pire = D.jour[i]; iPire = i; }
  const tPire = tuile("Pire seance", pire === null ? "n/a" : fmtSigned(pire, 2),
    pire === null ? "il faut deux seances" :
    jourCourt(D.dates[iPire]) + (D.actif ? ", limite " + fmtSigned(-D.perteJourMax, 0) : ""));
  poser(el("ops-defi-tuiles"), D.actif
    ? tuile("Progression", fmtSigned(fin, 2), "objectif " + fmtSigned(D.objectif, 0)) +
      tuile("Reste a faire", pts(Math.max(0, D.objectif - fin)), "jusqu'a l'objectif") +
      tuile("Marge", pts(fin - D.plancherBot[n - 1]), "avant le garde-fou (" + fmtSigned(D.plancherBot[n - 1], 0) + ")") +
      tPire
    : tuile("Depuis le depart", fmtSigned(fin, 2), n + " seance(s)") +
      tuile("Valeur", montant(D.equity[n - 1], D.devise), "depart " + montant(D.depart, D.devise)) + tPire);

  const leg = [["compte", cssVar("--series-1")]];
  if (D.indiceNom) leg.push([D.indiceNom + ", meme point de depart", cssVar("--muted")]);
  legendeDom(el("legend-defi"), leg);
  drawDefi(el("cv-defi"), el("tip-defi"), D);
  drawJour(el("cv-jour"), el("tip-jour"), D);

  const lignes = [];
  for (let i = n - 1; i >= 0; i--)
    lignes.push({ cells: [D.dates[i], montant(D.equity[i], D.devise), fmtSigned(D.compte[i], 2),
                          propre(D.jour[i]) ? fmtSigned(D.jour[i], 2) : "",
                          propre(D.indice[i]) ? fmtSigned(D.indice[i], 2) : "n/a"] });
  buildTable(el("tv-defi"), ["Seance", "Valeur", "Depuis le depart", "Jour (du depart)",
                              D.indiceNom || "Indice"], lignes);
}

function renderPortefeuilleGraphes(d) {
  if (!d || !d.connecte) return;
  const vise = {}, detenu = {};
  for (const t of d.cible) vise[t.ticker] = t.poids;
  for (const p of d.positions) detenu[p.ticker] = p.poids;
  const L = Object.keys(Object.assign({}, vise, detenu)).map(function (t) {
    return { ticker: t, vise: vise[t] || 0, detenu: detenu[t] || 0 }; });
  L.sort(function (a, b) { return (b.vise - a.vise) || (b.detenu - a.detenu); });
  const P = d.positions.map(function (p) { return { ticker: p.ticker, latent: p.latent, valeur: p.valeur }; })
    .sort(function (a, b) { return b.latent - a.latent; });
  // Les DEUX blocs sont demasques avant le premier dessin. Ils partagent une
  // grille : dessine seul, le premier mesurait toute la largeur de la ligne,
  // puis se retrouvait comprime dans une demi-colonne quand le second
  // apparaissait - texte ecrase de moitie.
  el("ops-halteres-bloc").hidden = !L.length;
  el("ops-latent-bloc").hidden = !P.length;
  if (L.length) drawHalteres(el("cv-halteres"), el("tip-halteres"), L);
  if (P.length) drawLatent(el("cv-latent"), el("tip-latent"), P, d.compte.devise);
}

// L'historique ne doit jamais faire echouer le reste de l'ecran : il gere
// ses erreurs lui-meme et affiche une phrase a la place de la courbe.
function opsHistorique() {
  if (MODE === "static") return Promise.resolve();
  return post("/api/ops/historique", {}).then(renderDefi).catch(function (e) {
    renderDefi({ ok: false, raison: e.message });
  });
}
function repeindreOps() {
  if (opsEtatCourant) renderPortefeuilleGraphes(opsEtatCourant);
  if (opsHisto) renderDefi(opsHisto);
}
// Un canvas masque a une largeur nulle : on redessine la vue VISIBLE au
// changement de theme ou de taille, et l'autre quand on bascule vers elle.
function repeindre() {
  if (document.body.dataset.vue === "ops") repeindreOps();
  else if (current) paint(current);
}

// ---------------------------------------------------------------------------
// Analyse : avance glissante et carte mensuelle
// ---------------------------------------------------------------------------
let carteMode = "rendement";

function glissant12(m) {
  const out = { dates: [], ecart: [], strat: [], indice: [] };
  for (let k = 11; k < m.mois.length; k++) {
    let ps = 1, pi = 1, ok = !!m.indice;
    for (let j = k - 11; ok && j <= k; j++) {
      if (!propre(m.strategie[j]) || !propre(m.indice[j])) { ok = false; break; }
      ps *= 1 + m.strategie[j]; pi *= 1 + m.indice[j];
    }
    out.dates.push(m.mois[k] + "-01");
    out.strat.push(ok ? ps - 1 : null);
    out.indice.push(ok ? pi - 1 : null);
    out.ecart.push(ok ? ps - pi : null);
  }
  return out;
}

function renderGlissant(d) {
  const cv = el("cv-glissant");
  if (!cv) return;
  const m = d.mensuels, note = el("glissant-note");
  if (!m || !m.mois || m.mois.length < 13 || !m.indice || !m.indice.some(propre)) {
    surface(cv, 24);
    poserTexte(note, !m || !m.mois ? "Pas de rendements mensuels pour cette periode." :
      "Il faut au moins treize mois, et un indice, pour une premiere fenetre de douze mois.");
    return;
  }
  const g = glissant12(m);
  drawLines(cv, el("tip-glissant"), {
    dates: g.dates, height: 220, fillZero: true,
    // Nom COURT : l'etiquette de bout de trace dispose de 104 px, et une
    // etiquette qui ne tient pas est rognee par le bord du canvas. Le titre
    // du graphique porte deja "douze mois".
    series: [{ name: "avance", color: cssVar("--series-1"),
               values: g.ecart.map(function (v) { return propre(v) ? 100 * v : null; }) }],
    yFmt: function (v) { return (v > 0 ? "+" : "") + fmtNum(v, 0) + NB + "pts"; },
    tipFmt: function (v) { return (v > 0 ? "+" : "") + fmtNum(v, 1) + NB + "pts"; },
    dateFmt: function (s) { return "12 mois a fin " + moisLong(s.slice(0, 7)); },
    tipExtra: function (i) { return [["strategie", fmtSigned(g.strat[i], 1)], ["indice", fmtSigned(g.indice[i], 1)]]; },
  });
  const v = g.ecart.filter(propre);
  if (!v.length) { poserTexte(note, ""); return; }
  const part = v.filter(function (x) { return x > 0; }).length / v.length;
  const tri = v.slice().sort(function (a, b) { return a - b; }), med = tri[Math.floor(tri.length / 2)];
  let txt = "La strategie bat l'indice dans " + fmtPct(part, 0) + " des " + v.length +
            " fenetres de douze mois ; ecart median " + (med > 0 ? "+" : "") + fmtNum(100 * med, 1) + NB + "pts. ";
  if (part >= 0.4 && part <= 0.6) txt += "Autant de fenetres gagnees que perdues : aucune avance qui se maintienne.";
  else if (part > 0.6) txt += "Une avance qui revient souvent - a confirmer hors echantillon, et sur l'univers corrige.";
  else txt += "La strategie passe plus de temps derriere l'indice que devant.";
  poserTexte(note, txt);
}

function renderCarte(d) {
  const cv = el("cv-carte");
  if (!cv) return;
  const m = d.mensuels, note = el("carte-note");
  if (!m || !m.mois || !m.mois.length) {
    surface(cv, 24); poserTexte(note, "Pas de rendements mensuels pour cette periode."); return;
  }
  const aIndice = !!m.indice && m.indice.some(propre);
  const bE = document.querySelector('[data-carte="ecart"]');
  if (bE) bE.disabled = !aIndice;
  const mode = aIndice ? carteMode : "rendement";
  const G = grilleMensuelle(m, mode);
  drawCarte(cv, el("tip-carte"), G);
  poserTexte(note, mode === "ecart"
    ? "Chaque case : le mois de la strategie MOINS celui de l'indice, en points. Bleu = mois gagne sur l'indice. A droite, l'annee composee."
    : "Chaque case : le rendement du mois, en %. A droite, l'annee composee.");
  buildTable(el("tv-carte"), ["Annee"].concat(MOIS_COURTS).concat(["Annee entiere"]),
    G.annees.map(function (an) {
      return { cells: [an].concat(G.cases[an].map(function (v) { return propre(v) ? caseTexte(v) : ""; }))
                          .concat([totalTexte(G.total[an], G.mode)]) }; }));
}

document.querySelectorAll("[data-carte]").forEach(function (b) {
  b.onclick = function () {
    carteMode = b.getAttribute("data-carte");
    document.querySelectorAll("[data-carte]").forEach(function (x) {
      x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
    if (current) renderCarte(current);
  };
});

function opsRecharger(silencieux) {
  // Un rafraichissement automatique n'annonce pas "Lecture du compte..." :
  // remplacer un titre lisible par un message d'attente toutes les minutes
  // donne l'impression d'une page qui se cherche.
  if (!silencieux) poserTexte(el("ops-titre"), "Lecture du compte...");
  return post("/api/ops/etat", {}).then(renderOps).then(opsHistorique).catch(function (e) {
    el("ops-erreur").className = "msg err";
    el("ops-erreur").textContent = "Erreur : " + e.message;
  });
}

// -- envoi, en deux temps ---------------------------------------------------
el("ops-envoyer").onclick = function () {
  const d = opsEtatCourant;
  if (!d || !d.connecte || !d.ordres.length) return;
  const total = devise(d.resume.echange, d.compte.devise);
  el("ops-confirmation").innerHTML =
    '<div class="confirmation">Envoyer <b>' + d.ordres.length + "</b> ordre(s), " +
    "<b>" + total + "</b> echanges, sur le compte de simulation " + d.compte.numero + " ?" +
    '<div class="actions"><button id="ops-confirmer" class="danger">Confirmer l\'envoi</button>' +
    '<button id="ops-renoncer">Renoncer</button></div></div>';
  el("ops-renoncer").onclick = function () { el("ops-confirmation").innerHTML = ""; };
  el("ops-confirmer").onclick = function () {
    el("ops-confirmation").innerHTML = "";
    el("ops-message").className = "msg";
    el("ops-message").textContent = "Envoi en cours...";
    post("/api/ops/envoyer", { forcer: el("ops-forcer").checked }).then(function (rep) {
      el("ops-message").className = rep.ok ? "msg" : "msg err";
      el("ops-message").textContent = rep.message +
        (rep.fourchettes ? "  (fourchette relevee pour " + rep.fourchettes + " titre(s))" : "");
      // Des ordres viennent de partir : ils peuvent s'executer dans la
      // seconde. On repasse tout de suite en cadence rapide plutot que
      // d'attendre le prochain battement calme.
      opsPlanifier(2000);
      return opsRecharger(true);
    }).catch(function (e) {
      el("ops-message").className = "msg err";
      el("ops-message").textContent = "Erreur : " + e.message;
    });
  };
};

el("ops-annuler").onclick = function () {
  el("ops-message").textContent = "Annulation...";
  post("/api/ops/annuler", {}).then(function (r) {
    el("ops-message").className = r.ok ? "msg" : "msg err";
    el("ops-message").textContent = r.message;
    opsPlanifier(2000);
  });
};

// -- qualite d'execution ----------------------------------------------------
el("ops-exec-btn").onclick = function () {
  const b = el("ops-exec-btn"), m = el("ops-exec-msg");
  b.disabled = true; m.className = "msg"; m.textContent = "Lecture des executions chez le courtier...";
  post("/api/ops/executions", {}).then(function (d) {
    m.textContent = "";
    el("ops-exec-body").hidden = false;
    el("ops-exec-verdict").className = "verdict " + (d.niveau || "flat");
    el("ops-exec-verdict").textContent = d.verdict || "";
    const bps = function (x) { return x === null || x === undefined ? "n/a" : fmtNum(10000 * x, 1); };
    buildTable(el("ops-exec-table"),
      ["Ticker", "Sens", "Prevu", "Obtenu", "vs fourchette", "vs cloture", "vs plan"],
      (d.releves || []).map(function (r) {
        return { cells: [r.ticker, r.sens === "sell" ? "VENTE" : "ACHAT",
                         fmtNum(r.prevu, 2), fmtNum(r.obtenu, 2),
                         bps(r.ecart_marche), bps(r.ecart_cloture), bps(r.ecart_plan)] };
      }));
  }).catch(function (e) {
    m.className = "msg err"; m.textContent = "Erreur : " + e.message;
  }).then(function () { b.disabled = false; });
};

// -- rafraichissement des cours, en tache de fond ---------------------------
function opsSuivreFetch() {
  post("/api/ops/statut", {}).then(function (s) {
    el("ops-fetch-etat").textContent = s.journal || "";
    if (s.en_cours) {
      opsFetchTimer = setTimeout(opsSuivreFetch, 1500);
    } else {
      el("ops-fetch-btn").disabled = false;
      if (s.termine) {
        opsEmpreinte = null;        // force la relecture au prochain battement
        opsRecharger(true);
      }
    }
  });
}
el("ops-fetch-btn").onclick = function () {
  el("ops-fetch-btn").disabled = true;
  el("ops-fetch-etat").textContent = "Demarrage...";
  post("/api/ops/rafraichir", {}).then(function () {
    clearTimeout(opsFetchTimer); opsSuivreFetch();
  });
};
el("ops-recharger").onclick = function () { opsRecharger(); };

// ---------------------------------------------------------------------------
// Suivi automatique du compte
// ---------------------------------------------------------------------------
// Principe : on n'interroge PAS l'etat complet en boucle. `/api/ops/etat`
// recalcule le score sur tout l'univers et prend le verrou du moteur ; le
// faire toutes les minutes gelerait la vue analyse a chaque passage.
//
// A la place, un battement bon marche (`/api/ops/pouls`, hors verrou : trois
// appels au courtier et deux `stat()`) renvoie une empreinte. Tant qu'elle ne
// bouge pas, l'ecran ne bouge pas non plus - aucun octet de DOM reecrit,
// aucune requete lourde. Quand elle change, et seulement alors, on va
// chercher l'etat complet.
//
// Trois regles pour que la page reste fluide :
//   * une seule requete en vol a la fois, jamais d'empilement ;
//   * rien du tout quand l'onglet est en arriere-plan (Page Visibility), et
//     une verification immediate au retour ;
//   * le DOM n'est touche que la ou le contenu differe reellement (`poser`).
const OPS_PERIODE_CALME = 60000;   // rien en vol : une fois par minute
const OPS_PERIODE_CHAUDE = 12000;  // ordres en attente : on suit de pres

let opsEmpreinte = null, opsTimerPouls = null, opsEnVol = false;
let opsDernierSucces = 0, opsEchecs = 0, opsPaused = false;
let opsFetchTentatives = 0, opsFetchDernier = 0;

function opsPastille(etat) {
  const p = el("ops-pouls");
  if (p) p.className = "pouls" + (etat ? " " + etat : "");
}

function opsMajVu() {
  const v = el("ops-vu");
  if (!v) return;
  if (!opsDernierSucces) { v.textContent = ""; return; }
  const secondes = Math.round((Date.now() - opsDernierSucces) / 1000);
  v.textContent = secondes < 5 ? "a l'instant"
    : secondes < 90 ? "il y a " + secondes + " s"
    : "il y a " + Math.round(secondes / 60) + " min";
}
setInterval(opsMajVu, 5000);

function opsSignalerChangement() {
  // Si l'utilisateur regarde ailleurs, on ne le deplace pas de force : une
  // pastille sur l'onglet suffit, il ira voir quand il voudra.
  if (document.body.dataset.vue !== "ops") {
    const b = el("vue-ops-pastille");
    if (b) b.hidden = false;
  }
  // Une confirmation d'envoi ouverte porte sur une liste d'ordres qui vient
  // de changer : la laisser afficher serait un piege - un clic validerait
  // autre chose que ce qui est ecrit. On la referme en disant pourquoi.
  const conf = el("ops-confirmation");
  if (conf && conf.innerHTML) {
    conf.innerHTML = "";
    const m = el("ops-message");
    m.className = "msg err";
    m.textContent = "Le compte a change pendant la confirmation : la liste d'ordres "
      + "a ete recalculee. Verifie-la et relance l'envoi si elle te convient.";
  }
}

function opsPlanifier(delai) {
  clearTimeout(opsTimerPouls);
  if (opsPaused || !el("ops-auto") || !el("ops-auto").checked) return;
  opsTimerPouls = setTimeout(opsBattre, delai);
}

function opsBattre() {
  if (opsEnVol || document.hidden) { opsPlanifier(OPS_PERIODE_CALME); return; }
  opsEnVol = true;
  opsPastille("occupe");
  post("/api/ops/pouls", {}).then(function (p) {
    opsEchecs = 0;
    opsDernierSucces = Date.now();
    opsMajVu();
    opsPastille("actif");
    const change = opsEmpreinte !== null && p.empreinte !== opsEmpreinte;
    const premier = opsEmpreinte === null;
    opsEmpreinte = p.empreinte;
    // Cadence : tant qu'un ordre est en vol, son execution peut tomber a
    // n'importe quel moment ; sinon rien ne bouge entre deux rebalancements.
    const periode = p.n_en_vol ? OPS_PERIODE_CHAUDE : OPS_PERIODE_CALME;

    // -- les cours se completent tout seuls ---------------------------------
    // Un ecran a jour devant des cours perimes est pire qu'un ecran fige : il
    // donne confiance dans des chiffres qui ne decrivent plus rien. Des qu'une
    // seance close manque au cache, on la telecharge sans rien demander.
    // Trois tentatives au maximum, espacees de dix minutes : si le reseau est
    // coupe, insister ne le reparera pas, et le controle "Donnees a jour"
    // reste rouge pour le dire.
    if (p.retard_seances > 0) {
        const assezAttendu = Date.now() - opsFetchDernier > 600000;
        if (!p.fetch_en_cours && opsFetchTentatives < 3 && assezAttendu
            && el("ops-auto") && el("ops-auto").checked) {
          opsFetchTentatives += 1;
          opsFetchDernier = Date.now();
          el("ops-fetch-btn").disabled = true;
          el("ops-fetch-etat").textContent =
            "Cours en retard de " + p.retard_seances + " seance(s) : telechargement automatique...";
          post("/api/ops/rafraichir", {}).then(function () {
            clearTimeout(opsFetchTimer); opsSuivreFetch();
          }).catch(function () {
            el("ops-fetch-etat").textContent = "Telechargement impossible (serveur injoignable).";
            el("ops-fetch-btn").disabled = false;
          });
        }
    } else {
      opsFetchTentatives = 0;       // le cache est a jour : on repart a zero
    }

    if (change && !premier) {
      opsSignalerChangement();
      return opsRecharger(true).then(function () { opsPlanifier(periode); });
    }
    opsPlanifier(periode);
  }).catch(function () {
    // Serveur arrete ou courtier injoignable : on espace au lieu d'insister,
    // et on le dit au lieu de faire semblant que tout va bien.
    opsEchecs += 1;
    opsPastille("perdu");
    const v = el("ops-vu");
    if (v) v.textContent = "hors contact";
    opsPlanifier(Math.min(OPS_PERIODE_CALME * Math.pow(2, opsEchecs - 1), 300000));
  }).then(function () { opsEnVol = false; });
}

// L'onglet passe en arriere-plan : on arrete tout. Il revient : on verifie
// immediatement, parce que c'est exactement le moment ou l'utilisateur veut
// savoir ce qui s'est passe pendant son absence.
document.addEventListener("visibilitychange", function () {
  if (document.hidden) { clearTimeout(opsTimerPouls); }
  else { opsPlanifier(300); }
});

el("ops-auto").onchange = function () {
  if (this.checked) { opsPastille("actif"); opsPlanifier(300); }
  else { clearTimeout(opsTimerPouls); opsPastille(""); poserTexte(el("ops-vu"), "suivi en pause"); }
};

// -- bascule entre les deux vues -------------------------------------------
function setVue(v) {
  document.body.dataset.vue = v;
  el("vue-analyse").setAttribute("aria-pressed", v === "analyse");
  el("vue-ops").setAttribute("aria-pressed", v === "ops");
  try { localStorage.setItem("quantbot.vue", v); } catch (e) {}
  if (v === "ops") {
    const b = el("vue-ops-pastille");
    if (b) b.hidden = true;
    if (!opsCharge) { opsCharge = true; opsRecharger().then(function () { opsPlanifier(OPS_PERIODE_CALME); }); }
  }
  if (v === "analyse" && current) paint(current);
  if (v === "ops") repeindreOps();
}
el("vue-analyse").onclick = function () { setVue("analyse"); };
el("vue-ops").onclick = function () { setVue("ops"); };
if (MODE === "static") {
  el("nav-vue").hidden = true;             // sans serveur, aucune operation possible
  document.body.dataset.vue = "analyse";
} else {
  let vue0 = "analyse";
  try { vue0 = localStorage.getItem("quantbot.vue") || "analyse"; } catch (e) {}
  setVue(vue0);
  // Le battement demarre meme si la vue Operations n'a jamais ete ouverte :
  // c'est ce qui permet d'etre prevenu d'une transaction pendant qu'on
  // travaille sur l'analyse, par une pastille sur l'onglet plutot que par
  // une page qui change sous les yeux.
  opsPlanifier(1500);
}


let timer = null;
function change(id, value, slow) {
  state[id] = value;
  const out = el("out-" + id);
  if (out) out.textContent = typeof value === "number" ? fmtNum(value, value % 1 ? 2 : 0) : value;
  if (slow) { clearTimeout(timer); timer = setTimeout(refresh, 220); }
  else refresh();
}
document.querySelectorAll("[data-ctl]").forEach(function (node) {
  const id = node.getAttribute("data-ctl"), type = node.getAttribute("data-type");
  node.addEventListener(type === "range" ? "input" : "change", function () {
    let v;
    if (type === "bool") v = node.checked;
    else if (type === "range") v = parseFloat(node.value);
    else v = node.value;
    change(id, v, type === "range");
  });
});
el("dec-btn").onclick = function () {
  onDemand("dec", "/api/decompose", function () { return state; }, renderDecomposition,
           "calcul des quatre etapes..."); };
el("ic-btn").onclick = function () {
  onDemand("ic", "/api/ic", function () { return state; }, renderIC, "calcul des correlations..."); };
el("deciles-btn").onclick = function () {
  onDemand("deciles", "/api/deciles", function () { return state; }, renderDeciles,
           "rendements par decile, puis relance avec le score inverse..."); };
el("null-btn").onclick = function () {
  const n = parseInt(el("null-draws").value, 10) || 150;
  onDemand("null", "/api/null", function () { return { values: state, n_draws: n }; }, renderNull,
           n + " backtests sur scores aleatoires, patiente..."); };
el("theme").onclick = function () {
  const now = document.documentElement.getAttribute("data-theme");
  const dark = now ? now === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  repeindre();
};
window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(repeindre, 150); });
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)")
  .addEventListener("change", repeindre);
refresh();
"""


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body data-niveau="simple">
<header>
  <h1>__TITLE__</h1>
  <span class="sub" id="meta"></span>
  <span class="spacer"></span>
  <span class="niveau" id="nav-vue">
    <button id="vue-analyse" aria-pressed="true">Analyse</button>
    <button id="vue-ops" aria-pressed="false">Operations<span id="vue-ops-pastille" hidden></span></button>
  </span>
  <span class="niveau">
    <button id="niv-simple" aria-pressed="true">Simple</button>
    <button id="niv-complet" aria-pressed="false">Complet</button>
  </span>
  <span class="badge avance">__MODEBADGE__</span>
  <span class="badge">__PERIODE__</span>
  <button id="theme" title="Basculer clair / sombre">Theme</button>
</header>

<div class="layout">
  <aside>__CONTROLS__</aside>

  <main id="panels">
    <div id="error" class="msg"></div>

    <section class="card resume">
      <div class="head">
        <div style="flex:1 1 auto">
          <h3>En resume</h3>
          <div class="titre" id="resume-titre">...</div>
        </div>
        <button id="tout-btn" class="primary">Repondre aux 3 questions</button>
      </div>
      <div id="resume-questions"></div>
      <div class="conclusion" id="resume-conclusion"></div>
      <div id="tout-msg" class="msg"></div>
    </section>

    <details class="plus" style="margin:-8px 0 20px">
      <summary>Comment lire cette page</summary>
      <p><b>Une strategie qui gagne de l'argent dans un backtest ne prouve rien
      par elle-meme.</b> Trois choses peuvent expliquer une belle courbe : un
      vrai avantage, de la chance, ou un defaut dans les donnees. Cette page
      sert a distinguer les trois, dans cet ordre :</p>
      <p><b>1. Que s'est-il passe ?</b> Les deux premiers graphiques montrent
      ce qu'aurait fait la strategie. On les compare toujours a deux reperes :
      l'indice (SPY, un placement passif que n'importe qui peut acheter) et
      <b>l'univers entier</b> — acheter tous les titres a parts egales, sans
      rien choisir. Battre le second est bien plus dur que battre le premier.</p>
      <p><b>2. D'ou vient le resultat ?</b> La decomposition separe ce qui vient
      du choix de l'univers, de la selection des titres, de la ponderation et
      du filtre de marche. Une brique qui n'apporte rien se voit tout de suite.</p>
      <p><b>3. Est-ce reel ?</b> Les trois derniers tests verifient que le
      resultat n'est ni de la chance ni un artefact. Ce sont eux qui comptent :
      tant qu'ils ne repondent pas oui, la performance affichee plus haut n'est
      pas une information exploitable.</p>
      <p>Passe en mode <b>Complet</b> quand tu veux les chiffres bruts, les
      statistiques et tous les reglages. Le glossaire est en bas de page.</p>
    </details>

    <div class="tiles" id="tiles"></div>

    <section class="card">
      <div class="head">
        <div>
          <h3><span class="num">1.</span>Combien ca aurait rapporte</h3>
          <p class="regarder">A regarder : <b>la courbe bleue est-elle au-dessus de l'orange ?</b>
            L'orange, c'est acheter tous les titres sans rien choisir. Si le bleu ne
            passe pas devant, la selection ne sert a rien.</p>
          <details class="plus"><summary>Pourquoi l'echelle est bizarre, et pourquoi l'orange est le vrai concurrent</summary>
            <p>Base 100 au debut de la periode, echelle logarithmique : a cette echelle,
            une meme pente represente un meme rendement, quel que soit le niveau atteint.
            Sans cela les dernieres annees ecraseraient visuellement les premieres.</p>
            <p>La courbe <b>univers entier</b> detient tous les titres, equiponderes, sans
            aucune selection ni filtre. C'est elle qu'il faut battre plutot que l'indice :
            l'univers est constitue des societes qui font partie de l'indice
            <i>aujourd'hui</i>, donc de survivantes, ce qui gonfle sa performance. La
            strategie beneficie exactement du meme avantage, donc la comparaison entre les
            deux est la seule qui soit propre.</p></details>
        </div>
      </div>
      <div class="legend" id="legend-perf"></div>
      <div class="plot"><canvas id="cv-perf"></canvas><div class="tip" id="tip-perf"></div></div>
      <div class="verdict" id="verdict"></div>
      <details class="tv avance"><summary>Vue tableau (valeurs de fin d'annee)</summary><div id="tv-perf"></div></details>

      <h4>Avance sur l'indice, sur douze mois glissants</h4>
      <p class="regarder">A regarder : <b>combien de temps la courbe passe au-dessus de zero.</b>
        Chaque point compare les douze mois qui s'achevent. Une vraie avance s'y voit comme une
        courbe qui reste en haut ; une avance de hasard, comme une courbe qui oscille autour
        de zero.</p>
      <div class="plot"><canvas id="cv-glissant"></canvas><div class="tip" id="tip-glissant"></div></div>
      <p class="note-graphe" id="glissant-note"></p>
    </section>

    <section class="card">
      <h3><span class="num">2.</span>Ce qu'on aurait vecu en cours de route</h3>
      <p class="regarder">A regarder : <b>jusqu'ou ca descend, et combien de temps ca reste en bas.</b>
        Une courbe qui passe deux ans a -40 % se detient tres mal, meme si elle finit bien.</p>
      <details class="plus"><summary>Definition exacte</summary>
        <p>Ecart au plus haut historique atteint, jour par jour. Zero signifie
        « au plus haut » ; -30 % signifie qu'il faut regagner 43 % pour revenir
        au sommet precedent.</p></details>
      <div class="legend" id="legend-dd"></div>
      <div class="plot"><canvas id="cv-dd"></canvas><div class="tip" id="tip-dd"></div></div>

      <h4>Mois par mois</h4>
      <p class="regarder">A regarder : <b>les series de cases rouges.</b> Une carte bleue piquee de
        rouge se detient bien ; trois mois rouges d'affilee, c'est la que l'on abandonne une
        strategie - souvent juste avant qu'elle ne reparte.</p>
      <div class="bascule" role="group" aria-label="Mesure affichee">
        <button type="button" data-carte="rendement" aria-pressed="true">Rendement</button>
        <button type="button" data-carte="ecart" aria-pressed="false">Ecart a l'indice</button>
      </div>
      <div class="plot"><canvas id="cv-carte"></canvas><div class="tip" id="tip-carte"></div></div>
      <p class="note-graphe" id="carte-note"></p>
      <details class="tv avance"><summary>Vue tableau</summary><div id="tv-carte"></div></details>
    </section>

    <section class="card">
      <div class="head">
        <div>
          <h3><span class="num">3.</span>D'ou vient le resultat</h3>
          <p class="regarder">A regarder : <b>la barre « selection factorielle ».</b>
            C'est la seule qui mesure l'intelligence de la strategie. Les autres
            mesurent l'univers choisi, la ponderation et le filtre de marche.</p>
          <details class="plus"><summary>Comment lire une cascade</summary>
            <p>On part de l'indice, puis on ajoute une brique a la fois : l'univers,
            puis la selection des titres, puis la ponderation, puis le filtre de
            marche. Chaque barre est la contribution de la brique ajoutee, en points
            de rendement annuel. Une barre nulle veut dire que la brique ne sert a
            rien ; une barre rouge, qu'elle coute de l'argent.</p></details>
        </div>
        <span class="spacer"></span>
        <button id="dec-btn" class="primary">Calculer</button>
      </div>
      <div id="dec-msg" class="msg"></div>
      <div id="dec-body" hidden>
        <div class="plot"><canvas id="cv-dec"></canvas></div>
        <details class="tv avance" open><summary>Vue tableau</summary><div id="tv-dec"></div></details>
      </div>
    </section>

    <section class="card">
      <div class="head">
        <div>
          <h3><span class="num">4.</span>Le score choisit-il vraiment les bons titres ?</h3>
          <p class="regarder">A regarder : <b>la forme du graphique.</b>
            Elle doit <b>monter de gauche a droite</b> — les titres les mieux notes
            rapportent plus que les moins bien notes. Si les deux bouts remontent et
            que le milieu s'affaisse (une forme en U), le score ne classe rien du
            tout : il repere seulement les titres agites, et acheter les plus mal
            notes marche aussi bien.</p>
          <details class="plus"><summary>Le detail, et pourquoi ce test existe</summary>
            <p>On range les titres en dix groupes selon leur note, du moins bien note
            (1) au mieux note (10), et on mesure ce que chaque groupe a rapporte le
            mois suivant, en ecart a la moyenne de l'univers. Les deux dernieres
            barres isolent les extremes reellement achetes par la strategie.</p>
            <p>Ce test existe parce que les deux mesures precedentes peuvent se
            contredire sans qu'aucune ne soit fausse : une correlation calculee sur
            tout le classement peut etre nulle alors qu'un portefeuille de 20 titres
            sur 500, qui ne vit que dans la queue du classement, brille. La ligne qui
            tranche est l'ecart entre les meilleurs et les pires notes. Sur un univers
            de survivants, c'est le piege principal : les titres extremes qui ont
            survecu jusqu'a aujourd'hui sont, par construction, ceux qui sont montes.</p></details>
        </div>
        <span class="spacer"></span>
        <button id="deciles-btn" class="primary">Calculer</button>
      </div>
      <div id="deciles-msg" class="msg"></div>
      <div id="deciles-body" hidden>
        <div class="plot"><canvas id="cv-deciles"></canvas></div>
        <div class="verdict" id="deciles-verdict"></div>
        <details class="tv avance" open><summary>Vue tableau : rendement par groupe</summary><div id="tv-deciles"></div></details>
        <details class="tv" open><summary>La preuve par l'absurde : le meme moteur avec le score retourne</summary><div id="tv-inverse"></div></details>
      </div>
    </section>

    <section class="card">
      <div class="head">
        <div>
          <h3><span class="num">5.</span>Fait-on mieux qu'en tirant au sort ?</h3>
          <p class="regarder">A regarder : <b>ou tombe le trait noir par rapport aux barres.</b>
            Les barres montrent ce qu'auraient donne des centaines de strategies
            choisissant leurs titres <i>au hasard</i>. Le trait est la vraie
            strategie. S'il tombe au milieu du tas, elle ne fait pas mieux que le
            hasard ; il faut qu'il soit nettement a droite.</p>
          <p class="regarder"><b>Ce test ne suffit pas a lui seul.</b> Un tirage au sort
            choisit des titres moyens ; un score qui vise les titres les plus agites le
            bat sans savoir classer pour autant. C'est le test 4 qui tranche.</p>
          <details class="plus"><summary>Comment le hasard est fabrique</summary>
            <p>On remplace la note de chaque titre par du bruit ayant la <b>meme
            persistance d'un mois sur l'autre</b> que la vraie note, et on relance le
            backtest complet des centaines de fois. Le controle de la persistance est
            indispensable : un bruit sans memoire ferait tourner le portefeuille bien
            plus vite et paierait des frais que la vraie strategie ne paie pas, ce qui
            truquerait la comparaison en sa faveur.</p></details>
        </div>
        <span class="spacer"></span>
        <span><input type="number" id="null-draws" value="150" min="20" max="600" step="10"
              style="width:74px" class="avance" __NULLDISABLED__>
        <button id="null-btn" class="primary">Lancer</button></span>
      </div>
      <div id="null-msg" class="msg"></div>
      <div id="null-body" hidden>
        <div class="plot"><canvas id="cv-null"></canvas></div>
        <div class="verdict" id="null-verdict"></div>
        <details class="tv avance"><summary>Vue tableau</summary><div id="tv-null"></div></details>
      </div>
    </section>

    <section class="card avance">
      <div class="head">
        <div>
          <h3><span class="num">6.</span>Mesure statistique du signal</h3>
          <p class="regarder">A regarder : <b>la colonne t.</b> Au-dela de 2 en valeur
            absolue, le facteur porte une information credible ; en dessous, il est
            indiscernable d'un classement au hasard.</p>
          <details class="plus"><summary>Ce que mesure l'information coefficient</summary>
            <p>Correlation de rang entre la note du jour de signal et le rendement
            effectivement realise jusqu'au rebalancement suivant. Cette mesure utilise
            <b>tous</b> les titres a chaque date, la ou une courbe de performance ne
            retient que les lignes selectionnees : elle est bien plus efficace
            statistiquement. Elle ne capte en revanche qu'une relation monotone
            moyenne, d'ou le test n.4 qui regarde les queues.</p></details>
        </div>
        <span class="spacer"></span>
        <button id="ic-btn" class="primary">Calculer</button>
      </div>
      <div id="ic-msg" class="msg"></div>
      <div id="ic-body" hidden>
        <div class="plot"><canvas id="cv-ic"></canvas></div>
        <div class="verdict" id="ic-verdict"></div>
        <details class="tv" open><summary>Vue tableau</summary><div id="tv-ic"></div></details>
      </div>
    </section>

    <section class="card">
      <h3>Glossaire</h3>
      <p class="regarder">Chaque terme employe sur cette page, en francais courant.</p>
      <dl class="glossaire" id="glossaire"></dl>
    </section>
  </main>

  <main id="ops">
    <div id="ops-erreur" class="msg"></div>

    <section class="card resume">
      <div class="head">
        <div style="flex:1 1 auto">
          <h3>Compte de simulation <span class="badge pastille-mode">aucun argent reel</span></h3>
          <div class="titre" id="ops-titre">Connexion...</div>
        </div>
        <div class="auto">
          <span id="ops-pouls" class="pouls" title="Etat de la surveillance"></span>
          <label class="switch"><input type="checkbox" id="ops-auto" checked>
            suivi automatique</label>
          <span id="ops-vu" class="vu"></span>
          <button id="ops-recharger">Rafraichir maintenant</button>
        </div>
      </div>
      <div class="tiles" id="ops-tuiles"></div>
      <h3 style="margin:18px 0 10px;font-size:13.5px">Controles avant envoi</h3>
      <div class="controles" id="ops-controles"></div>
    </section>

    <section class="card" id="ops-defi">
      <h3 id="ops-defi-titre">Le defi, seance par seance</h3>
      <p class="regarder">A regarder : <b>la distance entre la courbe et les lignes.</b>
        En haut l'objectif ; en bas deux planchers - le garde-fou, ou le bot s'arrete de
        lui-meme, puis la limite ou le contrat elimine. L'ecart entre ces deux planchers est
        ta marge : c'est lui qui separe s'arreter de se faire arreter.</p>
      <div id="ops-defi-msg" class="msg">Lecture de l'historique...</div>
      <div id="ops-defi-corps" hidden>
        <div class="tiles" id="ops-defi-tuiles"></div>
        <div class="legend" id="legend-defi"></div>
        <div class="plot"><canvas id="cv-defi"></canvas><div class="tip" id="tip-defi"></div></div>
        <h4>Gains et pertes, seance par seance</h4>
        <p class="regarder">En % du <b>capital de depart</b>, comme le contrat les compte - et
          non de la valeur de la veille. Chaque barre est a l'aplomb de sa seance sur la
          courbe du dessus.</p>
        <div class="plot"><canvas id="cv-jour"></canvas><div class="tip" id="tip-jour"></div></div>
        <details class="tv"><summary>Vue tableau</summary><div id="tv-defi"></div></details>
      </div>
    </section>

    <section class="card">
      <h3><span class="num">1.</span>Ordres a passer</h3>
      <p class="regarder">Calcules a partir du meme code que la ligne de commande : le
        portefeuille cible du jour, moins ce que tu detiens deja. Les ventes passent avant
        les achats.</p>
      <div id="ops-ordres"></div>
      <div class="actions">
        <button id="ops-envoyer" class="primary">Envoyer les ordres</button>
        <button id="ops-annuler">Annuler les ordres en attente</button>
        <label class="switch" style="margin-left:6px"><input type="checkbox" id="ops-forcer">
          passer outre le calendrier et l'horaire</label>
      </div>
      <div id="ops-confirmation"></div>
      <div id="ops-message" class="msg"></div>
    </section>

    <section class="card">
      <h3><span class="num">2.</span>Portefeuille</h3>
      <p class="regarder">A gauche ce que tu detiens, a droite ce que la strategie vise
        aujourd'hui. Attention aux valorisations : le courtier marque au marche, le plan
        au dernier cours en cache. Un ecart de plus de 2 % entre les deux fait echouer le
        controle "Valorisation coherente" - c'est le signe d'un cache perime.</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:22px;margin-bottom:18px">
        <div id="ops-halteres-bloc" hidden>
          <h4 style="margin-top:8px">Detenu contre vise</h4>
          <div class="legend"><span><i class="point"></i>vise par la strategie</span>
            <span><i class="creux"></i>detenu</span></div>
          <div class="plot"><canvas id="cv-halteres"></canvas><div class="tip" id="tip-halteres"></div></div>
        </div>
        <div id="ops-latent-bloc" hidden>
          <h4 style="margin-top:8px">Gain latent par ligne</h4>
          <div class="legend"><span>depuis le prix d'achat, montants a droite</span></div>
          <div class="plot"><canvas id="cv-latent"></canvas><div class="tip" id="tip-latent"></div></div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:22px">
        <div><h3 style="font-size:13px;margin:0 0 8px">Detenu</h3><div id="ops-positions"></div></div>
        <div><h3 style="font-size:13px;margin:0 0 8px">Vise</h3><div id="ops-cible"></div></div>
      </div>
    </section>

    <section class="card">
      <div class="head">
        <div>
          <h3><span class="num">3.</span>Qualite d'execution</h3>
          <p class="regarder">A regarder : <b>le cout par rapport a la fourchette au moment
            de l'envoi.</b> C'est la seule reference contemporaine d'une execution, donc la
            seule qui mesure un cout et non un mouvement de marche.</p>
        </div>
        <span class="spacer"></span>
        <button id="ops-exec-btn" class="primary">Analyser</button>
      </div>
      <div id="ops-exec-msg" class="msg"></div>
      <div id="ops-exec-body" hidden>
        <div class="verdict" id="ops-exec-verdict"></div>
        <details class="tv" open><summary>Detail par ordre</summary><div id="ops-exec-table"></div></details>
      </div>
    </section>

    <section class="card">
      <div class="head">
        <div>
          <h3><span class="num">4.</span>Donnees de marche</h3>
          <p class="regarder">Le plan est calcule sur la derniere cloture en cache. Au-dela
            de cinq jours, les ordres sont refuses.</p>
        </div>
        <span class="spacer"></span>
        <button id="ops-fetch-btn">Rafraichir les cours</button>
      </div>
      <div id="ops-fetch-etat" class="progression"></div>
    </section>
  </main>
</div>

<footer>
  <p><b>A garder en tete en lisant ces chiffres.</b> L'univers est constitue des
  membres ACTUELS de l'indice : les societes sorties de la cote en sont absentes, ce
  qui gonfle mecaniquement tous les rendements affiches, strategie comme univers
  entier. La comparaison entre les deux reste valable puisqu'elles subissent le meme
  biais ; les niveaux absolus, eux, sont optimistes. Les frais sont forfaitaires. Aucun
  ordre n'est passe automatiquement.</p>
  <p>__FOOTER__</p>
</footer>

<script>
const MODE = "__MODE__";
const BOOT = __BOOT__;
const GRID = __GRID__;
const PRECOMPUTED = __PRECOMPUTED__;
__JS__
__APP__
</script>
</body>
</html>
"""


def _field(control, value, disabled=False, avance=False):
    """Un reglage -> son fragment HTML.

    `avance=True` ajoute la classe qui fait disparaitre le champ en niveau de
    lecture simple. Le champ reste dans la page : il n'est pas retire, il est
    replie.
    """
    cid, label, ctype = control["id"], control["label"], control["type"]
    dis = " disabled" if disabled else ""
    cls = "field avance" if avance else "field"
    hint = ('<div class="hint">%s</div>' % control["help"]) if control.get("help") else ""

    if ctype == "bool":
        checked = " checked" if value else ""
        return ('<div class="%s"><label class="switch">'
                '<input type="checkbox" data-ctl="%s" data-type="bool"%s%s>%s</label>%s</div>'
                % (cls, cid, checked, dis, label, hint))

    if ctype in ("choice", "select"):
        options = control.get("options") or []
        libelles = control.get("labels") or {}
        opts = []
        for o in options:
            valeur = o if isinstance(o, str) else o[0]
            defaut = o if isinstance(o, str) else o[1]
            opts.append('<option value="%s"%s>%s</option>'
                        % (valeur, " selected" if str(value) == str(valeur) else "",
                           libelles.get(valeur, defaut)))
        return ('<div class="%s"><label>%s</label>'
                '<select data-ctl="%s" data-type="choice"%s>%s</select>%s</div>'
                % (cls, label, cid, dis, "".join(opts), hint))

    step = control.get("step", 1)
    shown = ("%g" % value) if value is not None else ""
    return ('<div class="%s"><label>%s<b id="out-%s">%s</b></label>'
            '<input type="range" data-ctl="%s" data-type="range" min="%s" max="%s" step="%s" '
            'value="%s"%s>%s</div>'
            % (cls, label, cid, shown, cid, control["min"], control["max"], step, value, dis, hint))


def build_controls(controls, values, years, live_ids=None, simple_ids=None) -> str:
    """Panneau de reglages, groupe par section, dans l'ordre de CONTROLS.

    Les reglages absents de `simple_ids` sont marques "avance" : ils
    disparaissent en niveau de lecture simple sans etre retires de la page.
    """
    periods = [("", "tout l'historique")] + [(y + "-01-01", "a partir de " + y) for y in years[:-1]]
    ends = [("", "jusqu'a la fin")] + [(y + "-12-31", "jusqu'a fin " + y) for y in years[1:]]
    simple_ids = simple_ids if simple_ids is not None else set(c["id"] for c in controls)
    html, group = [], None
    for c in controls:
        if live_ids is not None and c["id"] not in live_ids:
            continue
        avance = c["id"] not in simple_ids
        if c["group"] != group:
            group = c["group"]
            # Un titre de section n'apparait en simple que s'il reste au moins
            # un reglage visible dessous.
            visible = any(o["group"] == group and o["id"] in simple_ids
                          and (live_ids is None or o["id"] in live_ids) for o in controls)
            html.append('<h2%s>%s</h2>' % ("" if visible else ' class="avance"', group))
        spec = dict(c)
        if c["id"] == "start":
            spec["options"] = periods
        elif c["id"] == "end":
            spec["options"] = ends
        html.append(_field(spec, values.get(c["id"]), avance=avance))
    return "\n".join(html)


def render(mode, boot, controls_html, title, periode, footer,
           grid=None, precomputed=None) -> str:
    """Assemble la page complete. Aucune ressource externe n'est chargee."""
    badge = ("recalcul en direct" if mode == "live"
             else "fichier autonome, variantes precalculees")
    return (PAGE
            .replace("__THEME__", "")
            .replace("__CSS__", CSS)
            .replace("__JS__", JS)
            .replace("__APP__", APP)
            .replace("__TITLE__", title)
            .replace("__MODEBADGE__", badge)
            .replace("__PERIODE__", periode)
            .replace("__CONTROLS__", controls_html)
            .replace("__FOOTER__", footer)
            .replace("__NULLDISABLED__", "" if mode == "live" else "disabled")
            .replace("__MODE__", mode)
            .replace("__BOOT__", json.dumps(boot, ensure_ascii=False))
            .replace("__GRID__", json.dumps(grid, ensure_ascii=False) if grid else "null")
            .replace("__PRECOMPUTED__", json.dumps(precomputed, ensure_ascii=False)
                     if precomputed else "null"))
