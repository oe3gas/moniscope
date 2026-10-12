#!/usr/bin/env python3
"""
analyze_mscan.py

Ergebnisansicht fuer MSCAN-Laeufe (PLAN.md T-1106a) - Port 8060. Bis T-1106
lag diese Seite als Uebergangsloesung in manage_jobs.py (:8050, /mscan/run,
/api/mscan/run, /api/mscan/wav); seit T-1106a eigener Server, analog zu
analyze_sessions.py (:8020)/analyze_spectrum.py (:8040)/analyze_audio.py
(:8070). Die Kernlogik (build_mscan_run_result/resolve_run_file) liegt in
eb200_core/mscan_view.py - dieser Server ruft sie nur noch ueber HTTP auf.

KEINE eigene Aufnahmeliste - Einstieg ausschliesslich per Deep-Link "?run="
aus der Laeufe-Tabelle in :8050 (eb200_core/recording_index.py,
auswertung.server == "mscan"). Die MSCAN-DEFINITIONSSEITEN (/mscan,
/mscan/job, /mscan/pick) sowie die Job-Verwaltung (/api/mscan/jobs|job|
catalog|runs|create|update|channels|delete|start) BLEIBEN in :8050 -
MSCAN-Segmente erscheinen NUR hier (T-1106), nie als eigene Zeilen in
:8050.

Jede Aufnahme in der Trefferliste traegt seit T-1106a einen "Spektrum"-Link
auf :8070 (analyze_audio.py) - siehe eb200_core/mscan_view.build_mscan_run_
result() fuer die Sandbox-Bedingung (job_dir muss real unterhalb des
Repo-Roots liegen; sonst ausgegraut, "T-201" offen).

Routen:
  GET  /?run=<run_id>          HTML-Seite (Ergebnisansicht); ohne "run":
                                Hinweisseite (Aufruf per Deep-Link aus :8050).
  GET  /api/run?run=           mscan_view.build_mscan_run_result() als JSON;
                                KeyError -> 404, ValueError -> 400.
  GET  /api/wav?run=&file=     mscan_view.resolve_run_file() + WAV-Auslieferung
                                mit HTTP-Range (200/206/416, Muster
                                analyze_audio.py); Sandbox-Fehler -> 403 ohne
                                Namensecho.

Start (aus dem Repo-Root E:\\EB200\\EB200repo, damit jobs/index.json und
storage_roots.json stimmen):
    python analyze_mscan.py
    python analyze_mscan.py --selftest
"""

from __future__ import annotations

import argparse
import http.server
import os
import signal
import sys
from urllib.parse import parse_qs, urlparse

from eb200_core.job_store import JobStore
from eb200_core.mscan_view import build_mscan_run_result, resolve_run_file
from eb200_core.param_store import register_server
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8060


# ---------------------------------------------------------------------------
# HTTP-Range-Parsing - Kopie aus analyze_audio.py, T-401-analog (siehe dort
# fuer die volle Begruendung: eine gemeinsame Render-/Hilfsschicht fuer alle
# Auswerte-Server ist nach T-401 noch offen, bis dahin bewusst markierte
# Kopien statt eines verfruehten gemeinsamen Moduls).
# ---------------------------------------------------------------------------

def parse_range(header_value, file_size: int):
    """
    Parst einen HTTP-Range-Header (EIN Bereich: "bytes=a-b" / "bytes=a-" /
    "bytes=-n") gegen file_size. Gibt (start, end) zurueck (inklusive,
    0-basiert), wenn der Header einen gueltigen UND erfuellbaren
    Einzelbereich beschreibt.

    Gibt None zurueck, wenn KEIN Range-Header vorliegt ODER er
    kommasepariert mehrere Bereiche traegt - Multi-Range wird bewusst NICHT
    unterstuetzt; der Aufrufer erkennt das an None und liefert stattdessen
    die komplette Datei mit 200 (zulaessiges Verhalten nach RFC 7233).

    Wirft ValueError bei syntaktisch ungueltigem ODER nicht erfuellbarem
    Einzelbereich (falsche Einheit, kaputte Zahlen, start > end, Bereich
    hinter dem Dateiende) - der Aufrufer antwortet dann mit 416.
    """
    if not header_value:
        return None
    if not header_value.startswith("bytes="):
        raise ValueError(f"unsupported range unit: {header_value!r}")

    spec = header_value[len("bytes="):].strip()
    if "," in spec:
        return None  # Multi-Range: siehe Docstring - Aufrufer liefert 200.

    a, sep, b = spec.partition("-")
    if not sep or (a == "" and b == ""):
        raise ValueError(f"invalid range syntax: {header_value!r}")

    try:
        if a == "":
            suffix = int(b)
            if suffix <= 0:
                raise ValueError("suffix length must be greater than 0")
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(a)
            end = int(b) if b != "" else file_size - 1
    except ValueError as exc:
        raise ValueError(f"invalid range numbers: {header_value!r}") from exc

    if file_size <= 0 or start < 0 or start > end or start >= file_size:
        raise ValueError(f"range not satisfiable: {header_value!r} (file size {file_size})")
    end = min(end, file_size - 1)
    return start, end


# ---------------------------------------------------------------------------
# HTML/JS. CSS kommt seit T-1401 aus eb200_core/ui_theme.py (Tokens + BASE_CSS,
# gleiche Quelle wie manage_jobs.py) - die bewusste CSS-Kopie aus T-1106a
# entfaellt damit. Die JS-Helfer (el/esc/fetchJSON/fmtDateTime) bleiben als
# markierte Kopie von manage_jobs.COMMON_JS (T-1106a), bis eine gemeinsame
# JS-Schicht (T-401) entschieden ist.
# ---------------------------------------------------------------------------

COMMON_CSS = THEME_CSS + BASE_CSS + """
  table { width:auto; }
  .arrow { font-size:0.8em; }
  .empty { color:var(--fg-muted); font-style:italic; padding:8px 0; }
"""

_COMMON_JS_COPY = """
function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
  if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
  return data;
}
function fmtDateTime(iso) { return iso ? isoDateTime(new Date(iso)) : '-'; }
"""

HTML_NO_RUN = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>MoniScope &ndash; MSCAN analysis</title>
<style>/*COMMON_CSS*/</style>
<script>/*THEME_HEAD*/</script>
</head>
<body>
/*HEADER*/
<p>Open only via a deep link from the run list (:8050).</p>
<p><a href="#" onclick="location.href='http://'+location.hostname+':8050/'">&laquo; to the run list (:8050)</a></p>
</body>
</html>
"""

HTML_MSCAN_RESULT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; MSCAN run</title>
<style>/*COMMON_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  #errorLine { color:var(--danger); margin:8px 0; }
  .rec-count { font-size:0.8em; color:var(--fg-muted); margin-left:8px; }
  /* T-1401: Lauf-Kopf als zweispaltige Karte (Entwurf :8060); Spaltenfluss
     column, Rest rechts. T-1014b: 6 statt 7 Zeilen links - Start/Ende/Dauer
     stehen jetzt in EINER Zeile, dafuer kam die Audiobilanz dazu. Rechts bleiben
     Dwell/Hold/Mindestdauer, Kanaele und Ordner ("orphaned" ist in den Tooltip
     der Bilanz gewandert). Aendert sich die Zeilenzahl links, MUSS dieser Wert
     mit, sonst rutscht die rechte Spalte. */
  #runHeader { display:grid; grid-template-columns:1fr 1fr; grid-auto-flow:column;
               grid-template-rows:repeat(6, auto); column-gap:40px; background:var(--panel);
               border:1px solid var(--border); border-radius:6px; padding:4px 16px 8px; margin:12px 0; }
  .kv { display:grid; grid-template-columns:190px 1fr; gap:12px; align-items:center;
        padding:8px 0; border-bottom:1px solid var(--border); }
  .kv .k { font-weight:600; color:var(--fg); }
  .kv code { font-family:inherit; font-size:0.92em; }
  .kv button { margin-left:10px; }
  .kv-warn { color:var(--warn); margin:8px 0; }
  #playerBar { position:sticky; top:0; z-index:5; background:var(--panel); border:1px solid var(--border);
               border-radius:6px; padding:10px 14px; margin:12px 0; display:flex;
               align-items:center; gap:16px; flex-wrap:wrap; }
  #playerLabel { font-size:0.92em; }
  #filterRow { display:flex; gap:18px; align-items:center; flex-wrap:wrap; margin:12px 0; border:1px solid var(--border);
               background:var(--panel); border-radius:6px; padding:10px 14px; }
  #filterRow .filter-title { font-weight:600; margin-right:4px; }
  #filterRow input[type=number] { width:80px; }
  #filterRow input[type=text] { width:220px; }
  tr.playing { background:var(--success-soft); }
  tr.chanRow { cursor:pointer; }
  tr.chanRow:hover { background:var(--panel-2); }
  #chanHidden { color:var(--fg-muted); font-size:0.8em; margin:4px 0 12px; }
  .tab-pane table { margin-top:8px; }
  #silentCtl { margin:10px 0; display:flex; gap:14px; align-items:center; } #silentCtl #silentMsg { color:var(--fg-muted); }
  .spektrum-off { color:var(--fg-faint); }
  /* T-1106b: Zeitverlauf-Matrix (Kanal x Zeit, reines Canvas - bleibt in beiden
     Themes dunkel, Farben im Zeichencode) */
  #timelineCanvas { background:var(--canvas-bg); border:1px solid var(--canvas-border); border-radius:4px; cursor:pointer; }
  #timelineTooltip { position:absolute; background:var(--panel); border:1px solid var(--border-strong); border-radius:4px;
                      padding:6px 10px; font-size:0.8em; color:var(--fg); pointer-events:none; z-index:20;
                      max-width:260px; box-shadow:var(--shadow); }
  /* Kurzzeitige Hervorhebung nach Klick auf einen Zeitverlauf-Balken (T-1106b) */
  #recBody tr { transition: background-color 1.5s ease; }
  #recBody tr.rec-highlight { background-color:var(--success-soft); }
  /* Belegungs-Ranking (T-1106b): Prozentwert + Mini-Balken je Zeile */
  .occ-cell { display:flex; align-items:center; gap:8px; justify-content:flex-end; }
  .occ-pct { min-width:42px; text-align:right; }
  .occ-bar { width:90px; height:6px; background:var(--panel-2); border:1px solid var(--border); border-radius:3px; overflow:hidden; }
  .occ-bar-fill { height:100%; background:var(--accent); }
  /* Verlaufsstreifen der Pegel-Inventur (T-1019f): ein Balken je Durchgang.
     fill folgt der Textfarbe, damit der Streifen in beiden Themes sitzt. */
  .spark { display:block; color:var(--accent); }
  .spark rect { fill:currentColor; }
  .spark-empty { color:var(--muted); }
</style>
</head>
<body>
/*HEADER*/
<div class="top-links">
  <a href="#" onclick="location.href='http://'+location.hostname+':8050/#definitionen'">Recordings / definitions</a>
  <a id="linkDef" href="#">Definition</a>
  <a href="#" onclick="location.href='http://'+location.hostname+':8050/'">Long-run jobs (:8050)</a>
</div>
<div id="errorLine" style="display:none"></div>

<div id="runHeader"></div>

<div id="playerBar">
<!-- T-1019h: bei einer Pegel-Inventur ausgeblendet (loadResult) - es gibt
     nichts abzuspielen. -->
  <audio id="player" controls preload="none"></audio>
  <span id="playerLabel"></span>
  <a id="playerDownload" download style="display:none">Download WAV</a>
</div>

<div id="filterRow">
  <span class="filter-title">Filter recordings:</span>
  <label>Min. duration (s) <input type="number" id="minDurInput" min="0" step="0.5" value="0"></label>
  <label>Channel <select id="channelSelect"><option value="">all channels</option></select></label>
  <input type="text" id="textFilter" placeholder="Label / MHz / file">
</div>

<div id="recTabs" class="tabbar">
  <button class="tab active" id="tabChan">Active channels</button>
  <button class="tab" id="tabSilent">Silent channels (0)</button>
  <button class="tab" id="tabRecs">Recordings <span id="recCount" class="rec-count"></span></button>
  <button class="tab" id="tabTimeline">Timeline</button>
  <button class="tab" id="tabEval">Analysis</button>
</div>
<div id="chanPanel" class="tab-pane active">
<table>
  <thead><tr id="chanHead"></tr></thead>
  <tbody id="chanBody"></tbody>
</table>
<div id="chanHidden"></div>
</div>
<div id="silentPanel" class="tab-pane">
<div id="silentCtl">
  <label><input type="checkbox" id="silentAllCb"> Select all</label>
  <button id="silentRemoveBtn">Remove selected from the definition</button>
  <span id="silentMsg"></span>
</div>
<table>
  <thead><tr><th></th><th>Label</th><th>MHz</th><th>Demod</th><th>Catalog file</th></tr></thead>
  <tbody id="silentBody"></tbody>
</table>
</div>
<div id="recPanel" class="tab-pane">
<table>
  <thead><tr id="recHead"></tr></thead>
  <tbody id="recBody"></tbody>
</table>
</div>
<div id="timelinePanel" class="tab-pane">
<div class="field-row" id="timelineSortRow" style="display:none">
  <label for="timelineSort">Sort rows</label>
  <select id="timelineSort">
    <option value="mem">Memory slot</option>
    <option value="freq">Frequency</option>
    <option value="hits">Openings</option>
    <option value="cycles">Active in cycles</option>
  </select>
</div>
<canvas id="timelineCanvas" width="980" height="200"></canvas>
<div id="timelineHint" class="empty" style="display:none"></div>
<div id="timelineTooltip" hidden></div>
</div>
<div id="evalPanel" class="tab-pane">
<table>
  <thead><tr id="evalHead"></tr></thead>
  <tbody id="evalBody"></tbody>
</table>
</div>

<script>/*COMMON_JS*/</script>
<script>
const runId = new URLSearchParams(location.search).get('run') || '';
el('pageTitle').textContent = 'MSCAN run ' + runId;
document.title = 'MoniScope – MSCAN run ' + runId;

let runData = null;
let allRecordings = [];
let chanSortKey = 'hits';
let chanSortDir = -1;
let recSortKey = 'start_at';
let recSortDir = 1;
let evalSortKey = 'total_s';
let evalSortDir = -1;
// T-1106b: Laufdauer in Sekunden (Basis fuer Belegungs-Ranking UND
// Zeitverlauf-Matrix), einmal pro loadResult() ueber computeRunTimeRange()
// bestimmt - null, wenn weder end_iso noch ein Aufnahme-Fallback greift.
let runDurationS = null;
// vorberechnete Balkenliste der Zeitverlauf-Matrix {x, y, w, h, ref} fuer
// matrixBarAt() (Tooltip/Klick) - siehe renderTimeline().
let timelineBars = [];

function showError(msg) {
  const line = el('errorLine');
  line.textContent = msg;
  line.style.display = '';
}

// T-1014b: Sekunden mit einer Nachkommastelle. Punkt als Dezimaltrennzeichen
// (T-1803c).
function fmtSek(x) {
  if (x == null) return '–';
  return Number(x).toLocaleString('en-US', { minimumFractionDigits: 1,
                                             maximumFractionDigits: 1 }) + ' s';
}

// T-1014b: Audiobilanz des Laufs, vollstaendig im Frontend gerechnet.
// stats.bytes zaehlt ALLES am Empfaenger Eingetroffene - MscanJobRecorder.
// record_audio() erhoeht den Zaehler auch fuer verwaiste Datagramme, die der
// Segmenter verwirft. Der Verlust braucht deshalb KEINE Annahme ueber die
// Datagrammdauer: 1 - behalten/empfangen. Die 30 ms je Datagramm (Annex F)
// dienen nur der Aufschluesselung in verwaist und in verworfene Kurzsegmente.
// Achtung: duration_s taugt hier nicht - seit T-1014a ist es die Wanduhr-
// Spanne ueber alle Teile und enthaelt die stillen Luecken.
function tonbilanz() {
  const r = runData.run;
  const empfB = (r.stats && r.stats.bytes) || 0;
  if (!empfB) return null;
  let keptB = 0;
  runData.channels.forEach(ch => (ch.recordings || []).forEach(rec => {
    keptB += rec.bytes || 0;
  }));
  const empf = empfB / 32000, kept = keptB / 32000;
  const verwaist = (r.audio_orphan_mid || 0) * 0.030;
  return {
    empf: empf, kept: kept, verwaist: verwaist,
    kurz: Math.max(0, empf - kept - verwaist),
    verlust: 100 * (empf - kept) / empf,
    resumed: r.audio_resumed,
  };
}

function fmtDur(s) {
  if (s == null) return '–';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
  return m + ':' + String(sec).padStart(2, '0');
}
function fmtKB(bytes) { return bytes == null ? '–' : Math.round(bytes / 1024) + ' kB'; }
function fmtMHz(hz) { return hz == null ? '?' : (hz / 1e6).toFixed(4); }

function kv(label, valueHtml) {
  return '<div class="kv"><span class="k">' + esc(label) + '</span><span class="v">' + valueHtml + '</span></div>';
}

function buildSortableHead(headId, columns, sortKey, sortDir, onSort) {
  const head = el(headId);
  head.innerHTML = '';
  columns.forEach(col => {
    const th = document.createElement('th');
    if (col.cls) th.classList.add(col.cls);
    let txt = col.label;
    if (col.sortable) {
      th.classList.add('sortable');
      if (sortKey === col.key) txt += ' <span class="arrow">' + (sortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
      th.onclick = () => onSort(col.key);
    }
    th.innerHTML = txt;
    head.appendChild(th);
  });
}

function renderHeader() {
  const r = runData.run;
  // T-1113: Kopfzeile mit dem Kurztext des Laufs (job_store-Label, in :8050
  // editierbar) - der Lauf ist damit ohne Blick in die Kopfkarte
  // identifizierbar. Erst hier, weil das Label mit den Ergebnisdaten kommt;
  // die Zeile im Skriptkopf bleibt Vorbelegung waehrend des Ladens.
  const kopfText = r.label
    ? 'MSCAN run “' + r.label + '” ' + runId
    : 'MSCAN run ' + runId;
  el('pageTitle').textContent = kopfText;
  document.title = 'MoniScope – ' + kopfText;
  const rows = [];
  rows.push(kv('Title', esc(r.params.title || '(no title)')));
  rows.push(kv('Mode', esc(labelOf('mode', r.params.mode))));
  rows.push(kv('State', esc(labelOf('zustand', r.state))));
  // T-1014b: Start, Ende und Dauer in EINER Zeile - drei einzelne Zeilen
  // haben die Kopfkarte unnoetig hoch gemacht.
  const endeText = r.ended_iso ? fmtDateTime(r.ended_iso) : (r.end_iso ? fmtDateTime(r.end_iso) : 'until stopped');
  let dauerText = '–';
  if (r.ended_iso && r.start_iso) {
    dauerText = fmtDur((new Date(r.ended_iso) - new Date(r.start_iso)) / 1000);
  }
  rows.push(kv('Start / end / duration', esc(fmtDateTime(r.start_iso)) + ' – ' +
    esc(endeText) + ' · ' + esc(dauerText)));
  rows.push(kv('Cycles', String(r.cycles)));

  const bil = tonbilanz();
  let bilanzHtml = '–';
  if (bil) {
    let tip = 'orphaned ' + fmtSek(bil.verwaist) +
      ' · in discarded short segments ' + fmtSek(bil.kurz);
    if (bil.resumed != null) tip += ' · resumptions ' + bil.resumed;
    bilanzHtml = '<span title="' + esc(tip) + '">' + fmtSek(bil.empf) +
      ' received · ' + fmtSek(bil.kept) + ' recorded · ' +
      bil.verlust.toLocaleString('en-US', { maximumFractionDigits: 1 }) +
      ' % loss</span>';
  }
  // T-1019d: Ohne Ton gibt es keine Bilanz zu ziehen - statt eines stillen
  // "–" steht dort, WARUM nichts da ist, samt der Schwelle, ab der ein Kanal
  // als aktiv galt. Die Mindestdauer entfaellt aus derselben Zeile: sie wirkt
  // nur auf WAV-Segmente.
  const dwellText = r.params.dwell_s == null ? 'unlimited' : (r.params.dwell_s + ' s');
  const holdText = r.params.hold_s != null ? (r.params.hold_s + ' s') : '–';
  if (isLevelRun()) {
    const schwelle = r.params.level_active_dbuv != null
      ? (r.params.level_active_dbuv + ' dBµV')
      : 'every squelch opening';
    const endeArt = r.params.stop_after === 'runtime' ? 'until end of run or stop' : 'one cycle';
    rows.push(kv('Survey', 'level only, no audio recording · threshold ' + esc(schwelle) +
      ' · ' + esc(endeArt)));
    if (r.below_threshold) {
      rows.push(kv('Below threshold', r.below_threshold +
        ' ' + pluralWort(r.below_threshold, 'opening', 'openings') + ' discarded (squelch open, level below the threshold)'));
    }
    rows.push(kv('Dwell / hold', dwellText + ' / ' + holdText));
    // T-1019h: Die Programmierung der Speicherplaetze laeuft VOR dem Scan und
    // kostet bei vielen Kanaelen einen erheblichen Teil der Laufzeit - sie
    // gehoert sichtbar in den Kopf, nicht versteckt in die Zeitachse.
    if (r.programming_s != null) {
      rows.push(kv('Programming', fmtDur(r.programming_s) + ' before the scan'));
    }
  } else {
    rows.push(kv('Audio balance', bilanzHtml));
    const minRecText = r.params.min_recording_s != null ? (r.params.min_recording_s + ' s') : '–';
    rows.push(kv('Dwell / hold / min. duration', dwellText + ' / ' + holdText + ' / ' + minRecText));
  }
  rows.push(kv('Channels', r.channel_count + ' in snapshot · ' + runData.channels.length +
    ' active · ' + runData.silent_channels.length + ' silent'));
  rows.push(kv('Folder', '<code id="jobDirText">' + esc(r.job_dir || '-') + '</code> ' +
    '<button id="copyDirBtn">Copy</button>'));
  if (r.state === 'laeuft') {
    rows.push('<div class="kv-warn">Run in progress – reload the page for the current state</div>');
  }
  el('runHeader').innerHTML = rows.join('');
  el('copyDirBtn').onclick = () => {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      showError('clipboard not available');
      return;
    }
    navigator.clipboard.writeText(r.job_dir || '').catch(() => showError('clipboard not available'));
  };
  el('linkDef').href = r.params.mscan_job_id
    ? 'http://' + location.hostname + ':8050/mscan/job?id=' + encodeURIComponent(r.params.mscan_job_id)
    : '#';
}

function populateChannelSelect() {
  const sel = el('channelSelect');
  sel.innerHTML = '<option value="">all channels</option>';
  runData.channels.forEach(ch => {
    if (!ch.channel_id) return;
    const opt = document.createElement('option');
    opt.value = ch.channel_id;
    opt.textContent = (ch.label || ch.channel_id) + ' – ' + fmtMHz(ch.freq_hz) + ' MHz';
    sel.appendChild(opt);
  });
}

function buildFlatRecordings() {
  const flat = [];
  runData.channels.forEach(ch => {
    (ch.recordings || []).forEach(rec => {
      const item = Object.assign({}, rec);
      item.channel_label = ch.label;
      item.channel_id = ch.channel_id;
      item.freq_hz = ch.freq_hz;
      flat.push(item);
    });
  });
  return flat;
}

function filteredRecordings() {
  const minDur = parseFloat(el('minDurInput').value) || 0;
  const chanFilter = el('channelSelect').value;
  const text = el('textFilter').value.trim().toLowerCase();
  return allRecordings.filter(rec => {
    if ((rec.duration_s || 0) < minDur) return false;
    if (chanFilter && rec.channel_id !== chanFilter) return false;
    if (text) {
      const hay = ((rec.channel_label || '') + ' ' + fmtMHz(rec.freq_hz) + ' ' + (rec.file || '')).toLowerCase();
      if (!hay.includes(text)) return false;
    }
    return true;
  });
}

// T-1106b (Nachtrag): Zeitbasis fuer Zeitverlauf-Matrix UND Belegungs-
// Ranking - t0 ist IMMER run.start_iso, t1 in dieser Reihenfolge:
// ended_iso = tatsaechliches Ende, end_iso = Planende - Reihenfolge wie
// im Seitenkopf (renderHeader: r.ended_iso ? ... : r.end_iso ? ... :
// 'bis Stopp'); erst wenn BEIDE fehlen (laufender/abgebrochener Lauf
// ohne Ende), das Maximum aus start_at + duration_s ueber ALLE
// Aufnahmen (allRecordings, ungefiltert vom Mindestdauer-Filter). Liefert
// null, wenn t0 fehlt oder auch der Fallback keinen Zeitraum ergibt -
// Aufrufer zeigen dann Hinweistext bzw. "–" statt Matrix/Prozentwert.
function computeRunTimeRange() {
  const r = runData.run;
  if (!r.start_iso) return null;
  const t0Ms = new Date(r.start_iso).getTime();
  if (isNaN(t0Ms)) return null;
  let t1Ms = null;
  const endSource = r.ended_iso || r.end_iso;
  if (endSource) {
    const parsed = new Date(endSource).getTime();
    if (!isNaN(parsed)) t1Ms = parsed;
  }
  if (t1Ms == null) {
    allRecordings.forEach(rec => {
      if (!rec.start_at) return;
      const startMs = new Date(rec.start_at).getTime();
      if (isNaN(startMs)) return;
      const endMs = startMs + (rec.duration_s || 0) * 1000;
      if (t1Ms == null || endMs > t1Ms) t1Ms = endMs;
    });
  }
  if (t1Ms == null || t1Ms <= t0Ms) return null;
  return { t0Ms, t1Ms };
}

// T-1019d: Ein Lauf der Pegel-Inventur (run.kind === 'level', siehe
// eb200_core/mscan_run.MscanLevelInventory) hat KEINE Aufnahmen - alle
// Kennzahlen stehen an der Kanalzeile selbst. Die Unterscheidung steckt an
// genau dieser einen Stelle; alles Weitere waehlt darueber nur seine
// Spalten bzw. blendet aus, was ohne Ton keinen Sinn ergibt.
function isLevelRun() {
  return !!(runData && runData.run && runData.run.kind === 'level');
}

function computeChannelStats() {
  const minDur = parseFloat(el('minDurInput').value) || 0;
  const stats = [];
  let hiddenCount = 0;
  if (isLevelRun()) {
    // Die Mindestdauer misst Sekunden Ton und greift hier nicht - es wird
    // deshalb auch nichts ausgeblendet (hiddenCount bleibt 0). total_s
    // bleibt 0 und wird bei einem Pegel-Lauf nirgends angezeigt.
    runData.channels.forEach(ch => {
      stats.push({
        channel_id: ch.channel_id, label: ch.label, freq_hz: ch.freq_hz,
        demod: ch.demod, bw_hz: ch.bw_hz,
        hits: ch.hits || 0,
        cycles_active: ch.cycles_active || 0,
        below_threshold: ch.below_threshold || 0,
        // T-1019f: Rohdaten des Verlaufs - [Durchgang, Treffer] bei Laeufen
        // von vor T-1019f, [Durchgang, Treffer, max_dbuv] bei neueren.
        per_cycle: ch.per_cycle || [],
        per_cycle_truncated: !!ch.per_cycle_truncated,
        total_s: 0,
        level_max_dbuv: ch.level_max_dbuv != null ? ch.level_max_dbuv : null,
        level_min_dbuv: null,
        last_start: ch.last_at || null,
      });
    });
    return { stats, hiddenCount };
  }
  runData.channels.forEach(ch => {
    const recs = (ch.recordings || []).filter(rec => (rec.duration_s || 0) >= minDur);
    if (!recs.length) { hiddenCount++; return; }
    let totalS = 0, maxLevel = null, minLevel = null, lastStart = null;
    recs.forEach(rec => {
      totalS += rec.duration_s || 0;
      if (rec.level_max_dbuv != null) {
        maxLevel = maxLevel == null ? rec.level_max_dbuv : Math.max(maxLevel, rec.level_max_dbuv);
        minLevel = minLevel == null ? rec.level_max_dbuv : Math.min(minLevel, rec.level_max_dbuv);
      }
      if (rec.start_at && (!lastStart || rec.start_at > lastStart)) lastStart = rec.start_at;
    });
    stats.push({
      channel_id: ch.channel_id, label: ch.label, freq_hz: ch.freq_hz, demod: ch.demod, bw_hz: ch.bw_hz,
      hits: recs.length, total_s: totalS, level_max_dbuv: maxLevel, level_min_dbuv: minLevel,
      last_start: lastStart,
    });
  });
  return { stats, hiddenCount };
}

// T-1019d: Anteil der Durchgaenge, in denen der Kanal mindestens einmal
// aktiv war. Das ersetzt bei einem Pegel-Lauf die Belegung in Prozent -
// die ist ohne Sendedauern nicht berechenbar. Darstellung bewusst wie
// fmtOccupancyCell() (gleiche CSS-Klassen), damit beide Laufarten gleich
// aussehen.
function fmtCyclesCell(c) {
  const total = (runData && runData.run && runData.run.cycles) || 0;
  if (!total) return String(c.cycles_active || 0);
  const pct = Math.min(100, (c.cycles_active / total) * 100);
  return '<div class="occ-cell"><span class="occ-pct">' + (c.cycles_active || 0) +
    ' / ' + total + '</span>' +
    '<div class="occ-bar"><div class="occ-bar-fill" style="width:' + pct.toFixed(1) + '%"></div></div></div>';
}

// Spalten fuer einen Pegel-Lauf. "Hits" meint hier Squelch-Oeffnungen
// oberhalb der Schwelle - NICHT die Zahl der WAV-Dateien wie in
// CHAN_TABLE_COLUMNS. Deshalb ein eigener Spaltensatz statt einer
// Umdeutung der bestehenden Spalte: derselbe Kopftext mit zwei Bedeutungen
// waere die schlechtere Loesung.
// T-1019f: Verlaufsstreifen aus per_cycle. Ein Balken je Durchgang; gibt es
// mehr Durchgaenge als Balken, werden benachbarte zu einer Gruppe
// zusammengefasst. Der Balken zeigt dann das MAXIMUM seiner Gruppe, nicht die
// Summe - ein einzelner Treffer neben einem dichten Nachbarn soll nicht
// verschwinden. SVG statt Canvas, weil die Tabelle bei jeder Sortierung neu
// gebaut wird und SVG das ohne eigenes Neuzeichnen ueberlebt.
const SPARK_BARS = 120;
const SPARK_W = 120;
const SPARK_H = 14;

function fmtCycleSpark(c) {
  const total = (runData && runData.run && runData.run.cycles) || 0;
  const eintraege = c.per_cycle || [];
  if (!total || !eintraege.length) {
    return '<svg class="spark spark-empty" width="' + SPARK_W + '" height="' + SPARK_H +
      '" viewBox="0 0 ' + SPARK_W + ' ' + SPARK_H + '"></svg>';
  }
  const gruppen = Math.min(SPARK_BARS, total);
  const proGruppe = total / gruppen;
  const werte = new Array(gruppen).fill(0);
  let maxWert = 0;
  eintraege.forEach(e => {
    const idx = Math.min(gruppen - 1, Math.floor((e[0] || 0) / proGruppe));
    const treffer = e[1] || 0;
    if (treffer > werte[idx]) werte[idx] = treffer;
    if (treffer > maxWert) maxWert = treffer;
  });
  const breite = SPARK_W / gruppen;
  let rects = '';
  werte.forEach((v, i) => {
    if (!v) return;
    // Deckkraft nach Trefferzahl, aber nie unter 0,35 - ein einzelner
    // Treffer muss sichtbar bleiben.
    const deck = maxWert > 1 ? (0.35 + 0.65 * (v / maxWert)) : 1;
    rects += '<rect x="' + (i * breite).toFixed(2) + '" y="0" width="' +
      Math.max(1, breite).toFixed(2) + '" height="' + SPARK_H +
      '" opacity="' + deck.toFixed(2) + '"></rect>';
  });
  // T-1019g: kein Tooltip am Streifen. Die Zahl der aktiven Durchgaenge steht
  // in derselben Zeile eine Spalte weiter links - ein Tooltip, der sie
  // wiederholt, ist Aufwand ohne Ertrag.
  return '<svg class="spark" width="' + SPARK_W + '" height="' + SPARK_H +
    '" viewBox="0 0 ' + SPARK_W + ' ' + SPARK_H + '">' + rects + '</svg>';
}

const LEVEL_CHAN_COLUMNS = [
  { key: 'label', label: 'Label', sortable: true,
    sort: c => (c.label || '').toLowerCase(), cell: c => esc(c.label || '') },
  { key: 'freq_hz', label: 'MHz', sortable: true, cls: 'num',
    sort: c => c.freq_hz || 0, cell: c => fmtMHz(c.freq_hz) },
  // T-1019g: keine Demod-Spalte. Ohne Tonaufnahme sagt die Betriebsart
  // nichts ueber das Ergebnis - sie steht im Kanalkatalog und im Snapshot,
  // hier kostet sie nur Breite.
  { key: 'hits', label: 'Openings', sortable: true, cls: 'num',
    sort: c => c.hits, cell: c => c.hits },
  { key: 'cycles_active', label: 'active in cycles', sortable: true, cls: 'num',
    sort: c => c.cycles_active, cell: c => fmtCyclesCell(c) },
  // T-1019f: nicht sortierbar - der Streifen ist ein Bild, seine Ordnung
  // ergibt sich aus cycles_active daneben.
  { key: 'spark', label: 'History', sortable: false, cell: c => fmtCycleSpark(c) },
  { key: 'level_max_dbuv', label: 'max dBµV', sortable: true, cls: 'num',
    sort: c => c.level_max_dbuv == null ? -1e9 : c.level_max_dbuv,
    cell: c => c.level_max_dbuv != null ? c.level_max_dbuv.toFixed(1) : '–' },
  { key: 'below_threshold', label: 'below threshold', sortable: true, cls: 'num',
    sort: c => c.below_threshold, cell: c => c.below_threshold },
  { key: 'last_start', label: 'last hit', sortable: true,
    sort: c => c.last_start || '', cell: c => c.last_start ? fmtDateTime(c.last_start) : '–' },
];

function chanColumns() { return isLevelRun() ? LEVEL_CHAN_COLUMNS : CHAN_TABLE_COLUMNS; }

const CHAN_TABLE_COLUMNS = [
  { key: 'label', label: 'Label', sortable: true,
    sort: c => (c.label || '').toLowerCase(), cell: c => esc(c.label || '') },
  { key: 'freq_hz', label: 'MHz', sortable: true, cls: 'num',
    sort: c => c.freq_hz || 0, cell: c => fmtMHz(c.freq_hz) },
  { key: 'demod', label: 'Demod', sortable: true,
    sort: c => c.demod || '', cell: c => esc(c.demod || '–') },
  { key: 'bw_hz', label: 'BW (kHz)', sortable: true, cls: 'num',
    sort: c => c.bw_hz || 0, cell: c => c.bw_hz != null ? (c.bw_hz / 1000).toFixed(0) : '–' },
  { key: 'hits', label: 'Hits', sortable: true, cls: 'num',
    sort: c => c.hits, cell: c => c.hits },
  { key: 'total_s', label: 'Total', sortable: true, cls: 'num',
    sort: c => c.total_s, cell: c => fmtDur(c.total_s) },
  { key: 'level_max_dbuv', label: 'max dBµV', sortable: true, cls: 'num',
    sort: c => c.level_max_dbuv == null ? -1e9 : c.level_max_dbuv,
    cell: c => c.level_max_dbuv != null ? c.level_max_dbuv.toFixed(1) : '–' },
  { key: 'last_start', label: 'last recording', sortable: true,
    sort: c => c.last_start || '', cell: c => c.last_start ? fmtDateTime(c.last_start) : '–' },
];

function renderChanTable() {
  const { stats, hiddenCount } = computeChannelStats();
  const cols = chanColumns();
  buildSortableHead('chanHead', cols, chanSortKey, chanSortDir, key => {
    if (chanSortKey === key) chanSortDir = -chanSortDir; else { chanSortKey = key; chanSortDir = -1; }
    renderChanTable();
  });
  const col = cols.find(c => c.key === chanSortKey && c.sortable) || cols.find(c => c.sortable);
  stats.sort((a, b) => (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * chanSortDir);
  const tbody = el('chanBody');
  tbody.innerHTML = '';
  if (!stats.length) {
    tbody.innerHTML = '<tr><td colspan="' + cols.length + '" class="empty">no active channels</td></tr>';
  } else {
    stats.forEach(c => {
      const tr = document.createElement('tr');
      // T-1019d: Ohne Aufnahmen fuehrt der Sprung in die Trefferliste ins
      // Leere - die Zeile bleibt bei einem Pegel-Lauf deshalb unklickbar
      // (keine chanRow-Klasse, kein onclick).
      tr.className = isLevelRun() ? '' : 'chanRow';
      tr.innerHTML = cols.map(col2 => {
        const cls = col2.cls || '';
        return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + col2.cell(c) + '</td>';
      }).join('');
      if (!isLevelRun()) {
        tr.onclick = () => {
          el('channelSelect').value = c.channel_id || '';
          renderRecTable();
          showResultTab('recs');
          el('recTabs').scrollIntoView({ block: 'start' });
        };
      }
      tbody.appendChild(tr);
    });
  }
  el('chanHidden').textContent = hiddenCount > 0 ? (hiddenCount + ' ' + pluralWort(hiddenCount, 'channel', 'channels') + ' below the min. duration') : '';
}

const REC_TABLE_COLUMNS = [
  { key: 'play', label: '', sortable: false,
    cell: rec => rec.file ? '<button class="playBtn">&#9654;</button>' : '' },
  { key: 'channel_label', label: 'Channel', sortable: true,
    sort: rec => (rec.channel_label || '').toLowerCase(), cell: rec => esc(rec.channel_label || '') },
  { key: 'freq_hz', label: 'MHz', sortable: true, cls: 'num',
    sort: rec => rec.freq_hz || 0, cell: rec => fmtMHz(rec.freq_hz) },
  { key: 'start_at', label: 'Start', sortable: true,
    sort: rec => rec.start_at || '', cell: rec => fmtDateTime(rec.start_at) },
  { key: 'duration_s', label: 'Duration (s)', sortable: true, cls: 'num',
    sort: rec => rec.duration_s || 0, cell: rec => rec.duration_s != null ? rec.duration_s.toFixed(1) : '–' },
  { key: 'level_max_dbuv', label: 'max dBµV', sortable: true, cls: 'num',
    sort: rec => rec.level_max_dbuv == null ? -1e9 : rec.level_max_dbuv,
    cell: rec => rec.level_max_dbuv != null ? rec.level_max_dbuv.toFixed(1) : '–' },
  { key: 'cycle', label: 'Cycle', sortable: true, cls: 'num',
    sort: rec => rec.cycle || 0, cell: rec => rec.cycle != null ? rec.cycle : '–' },
  { key: 'bytes', label: 'Size', sortable: true, cls: 'num',
    sort: rec => rec.bytes || 0, cell: rec => fmtKB(rec.bytes) },
  { key: 'file', label: 'File', sortable: true,
    sort: rec => rec.file || '', cell: rec => esc(rec.file || '–') },
  { key: 'spektrum', label: 'Spectrum', sortable: false,
    cell: rec => rec.spektrum_path
      ? '<a href="' + ('http://' + location.hostname + ':8070/?path=' + encodeURIComponent(rec.spektrum_path)) + '" target="eb200_eval_audio">Spectrum</a>'
      : '<span class="spektrum-off" title="Storage location outside the repo root (T-201)">Spectrum</span>' },
];

function renderRecTable() {
  buildSortableHead('recHead', REC_TABLE_COLUMNS, recSortKey, recSortDir, key => {
    if (recSortKey === key) recSortDir = -recSortDir; else { recSortKey = key; recSortDir = 1; }
    renderRecTable();
  });
  const rows = filteredRecordings();
  const col = REC_TABLE_COLUMNS.find(c => c.key === recSortKey && c.sortable) || REC_TABLE_COLUMNS.find(c => c.sortable);
  rows.sort((a, b) => (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * recSortDir);
  const tbody = el('recBody');
  tbody.innerHTML = '';
  el('recCount').textContent = rows.length + ' of ' + allRecordings.length + ' ' + pluralWort(allRecordings.length, 'recording', 'recordings');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="' + REC_TABLE_COLUMNS.length + '" class="empty">no recordings</td></tr>';
    return;
  }
  rows.forEach(rec => {
    const tr = document.createElement('tr');
    // T-1106b: Ziel fuer den Klick-Sprung aus der Zeitverlauf-Matrix
    // (matrixBarAt -> Datei -> hierueber gefundene Zeile).
    if (rec.file) tr.dataset.file = rec.file;
    tr.innerHTML = REC_TABLE_COLUMNS.map(c => {
      const cls = c.cls || '';
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + c.cell(rec) + '</td>';
    }).join('');
    const playBtn = tr.querySelector('.playBtn');
    if (playBtn) {
      playBtn.onclick = () => {
        document.querySelectorAll('#recBody tr.playing').forEach(row => row.classList.remove('playing'));
        tr.classList.add('playing');
        const url = '/api/wav?run=' + encodeURIComponent(runId) + '&file=' + encodeURIComponent(rec.file);
        const player = el('player');
        player.src = url;
        el('playerLabel').textContent = (rec.channel_label || '') + ' · ' + fmtDateTime(rec.start_at) +
          ' · ' + (rec.duration_s != null ? rec.duration_s.toFixed(1) : '?') + ' s';
        const dl = el('playerDownload');
        dl.href = url;
        dl.download = rec.file;
        dl.style.display = '';
        player.play().catch(() => {});
      };
    }
    tbody.appendChild(tr);
  });
}

const silentSelected = new Set();

function updateSilentAllCb(list) {
  const allCb = el('silentAllCb');
  if (!allCb) return;
  const withId = list.filter(c => c.id);
  const nSel = withId.filter(c => silentSelected.has(c.id)).length;
  allCb.checked = withId.length > 0 && nSel === withId.length;
  allCb.indeterminate = nSel > 0 && nSel < withId.length;
}

function renderSilentTable() {
  const list = runData.silent_channels || [];
  el('tabSilent').textContent = 'Silent channels (' + list.length + ')';
  const tbody = el('silentBody');
  tbody.innerHTML = '';
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">none</td></tr>';
  } else {
    const frag = document.createDocumentFragment();
    list.forEach(c => {
      const tr = document.createElement('tr');
      const tdSel = document.createElement('td');
      if (c.id) {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.cid = c.id;
        cb.checked = silentSelected.has(c.id);
        cb.onchange = () => {
          if (cb.checked) silentSelected.add(c.id); else silentSelected.delete(c.id);
          updateSilentAllCb(list);
        };
        tdSel.appendChild(cb);
      }
      tr.appendChild(tdSel);
      const tdLabel = document.createElement('td');
      tdLabel.textContent = c.label || '';
      tr.appendChild(tdLabel);
      const tdFreq = document.createElement('td');
      tdFreq.className = 'num';
      tdFreq.textContent = fmtMHz(c.freq_hz);
      tr.appendChild(tdFreq);
      const tdDemod = document.createElement('td');
      tdDemod.textContent = c.demod || '–';
      tr.appendChild(tdDemod);
      const tdFile = document.createElement('td');
      tdFile.textContent = c.file || '–';
      tr.appendChild(tdFile);
      frag.appendChild(tr);
    });
    tbody.appendChild(frag);
  }
  updateSilentAllCb(list);

  const mscanJobId = (runData.run.params || {}).mscan_job_id;
  const removeBtn = el('silentRemoveBtn');
  const allCb = el('silentAllCb');
  if (!mscanJobId) {
    removeBtn.disabled = true;
    allCb.disabled = true;
    el('silentMsg').textContent = 'no definition linked';
  }
}

// T-1106b: Belegung = total_s / Laufdauer (runDurationS, siehe
// computeRunTimeRange) - "–" wenn die Laufdauer nicht bestimmbar ist.
// Sortierung identisch zu Gesamtzeit (dieselbe Groesse, nur anders
// dargestellt), siehe EVAL_COLUMNS.
function fmtOccupancyCell(totalS) {
  if (runDurationS == null || runDurationS <= 0) return '–';
  const pct = Math.min(100, (totalS / runDurationS) * 100);
  return '<div class="occ-cell"><span class="occ-pct">' + pct.toFixed(1) + '%</span>' +
    '<div class="occ-bar"><div class="occ-bar-fill" style="width:' + pct.toFixed(1) + '%"></div></div></div>';
}

// T-1019d: Auswertung eines Pegel-Laufs - dieselben Kennzahlen wie in der
// Kanaltabelle, nur ohne Demod (die Auswertung ist die verdichtete Sicht).
const LEVEL_EVAL_COLUMNS = [
  { key: 'label', label: 'Label', sortable: true,
    sort: c => (c.label || '').toLowerCase(), cell: c => esc(c.label || '') },
  { key: 'freq_hz', label: 'MHz', sortable: true, cls: 'num',
    sort: c => c.freq_hz || 0, cell: c => fmtMHz(c.freq_hz) },
  { key: 'hits', label: 'Openings', sortable: true, cls: 'num',
    sort: c => c.hits, cell: c => c.hits },
  { key: 'cycles_active', label: 'active in cycles', sortable: true, cls: 'num',
    sort: c => c.cycles_active, cell: c => fmtCyclesCell(c) },
  { key: 'level_max_dbuv', label: 'max dBµV', sortable: true, cls: 'num',
    sort: c => c.level_max_dbuv == null ? -1e9 : c.level_max_dbuv,
    cell: c => c.level_max_dbuv != null ? c.level_max_dbuv.toFixed(1) : '–' },
  { key: 'below_threshold', label: 'below threshold', sortable: true, cls: 'num',
    sort: c => c.below_threshold, cell: c => c.below_threshold },
];

function evalColumns() { return isLevelRun() ? LEVEL_EVAL_COLUMNS : EVAL_COLUMNS; }

const EVAL_COLUMNS = [
  { key: 'label', label: 'Label', sortable: true,
    sort: c => (c.label || '').toLowerCase(), cell: c => esc(c.label || '') },
  { key: 'freq_hz', label: 'MHz', sortable: true, cls: 'num',
    sort: c => c.freq_hz || 0, cell: c => fmtMHz(c.freq_hz) },
  { key: 'hits', label: 'Recordings', sortable: true, cls: 'num',
    sort: c => c.hits, cell: c => c.hits },
  { key: 'total_s', label: 'Total time', sortable: true, cls: 'num',
    sort: c => c.total_s, cell: c => fmtDur(c.total_s) },
  { key: 'occupancy', label: 'Occupancy', sortable: true, cls: 'num',
    sort: c => c.total_s, cell: c => fmtOccupancyCell(c.total_s) },
  { key: 'level_max_dbuv', label: 'max dBµV', sortable: true, cls: 'num',
    sort: c => c.level_max_dbuv == null ? -1e9 : c.level_max_dbuv,
    cell: c => c.level_max_dbuv != null ? c.level_max_dbuv.toFixed(1) : '–' },
  { key: 'level_min_dbuv', label: 'min dBµV', sortable: true, cls: 'num',
    sort: c => c.level_min_dbuv == null ? 1e9 : c.level_min_dbuv,
    cell: c => c.level_min_dbuv != null ? c.level_min_dbuv.toFixed(1) : '–' },
];

function renderEvalTable() {
  const { stats } = computeChannelStats();
  const cols = evalColumns();
  buildSortableHead('evalHead', cols, evalSortKey, evalSortDir, key => {
    if (evalSortKey === key) evalSortDir = -evalSortDir; else { evalSortKey = key; evalSortDir = -1; }
    renderEvalTable();
  });
  const col = cols.find(c => c.key === evalSortKey && c.sortable) || cols.find(c => c.sortable);
  stats.sort((a, b) => (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * evalSortDir);
  const tbody = el('evalBody');
  tbody.innerHTML = '';
  if (!stats.length) {
    tbody.innerHTML = '<tr><td colspan="' + cols.length + '" class="empty">' +
      (isLevelRun() ? 'no channel active' : 'no recordings') + '</td></tr>';
    return;
  }
  stats.forEach(c => {
    const tr = document.createElement('tr');
    tr.innerHTML = cols.map(col2 => {
      const cls = col2.cls || '';
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + col2.cell(c) + '</td>';
    }).join('');
    tbody.appendChild(tr);
  });
}

// ---------------------------------------------------------------------------
// T-1106b: Reiter "Timeline" - Kanal x Zeit-Matrix als Canvas. Pure
// Funktionen OHNE DOM-Zugriff (matrixTimeToX/matrixRowY/matrixBarAt) sind
// bewusst von renderTimeline() getrennt, damit sie eigenstaendig testbar
// bleiben (siehe Node-Testskript des Selbsttest-Berichts).
// ---------------------------------------------------------------------------

const TIMELINE_WIDTH = 980;
const TIMELINE_LEFT = 150;
const TIMELINE_HEADER_H = 12;
const TIMELINE_ROW_H = 18;
const TIMELINE_AXIS_H = 22;
const TIMELINE_BAR_H = 12;

function matrixTimeToX(tMs, geom) {
  const frac = (tMs - geom.t0Ms) / (geom.t1Ms - geom.t0Ms);
  return geom.left + frac * geom.plotWidth;
}

// T-1019f Stufe 3b: x-Position eines Durchgangs. Anders als matrixTimeToX()
// rechnet diese Funktion mit Sekunden seit Laufbeginn (geom.s0/s1) und holt
// sich den Wert des Durchgangs aus geom.offsets - der Liste der
// Durchgangsenden. Ein Index jenseits der Liste wird auf den letzten Eintrag
// geklemmt (ein Treffer im gerade laufenden, noch nicht beendeten Durchgang).
function matrixCycleToX(cycle, geom) {
  const off = geom.offsets;
  const idx = Math.max(0, Math.min(cycle, off.length - 1));
  const frac = (off[idx] - geom.s0) / (geom.s1 - geom.s0);
  return geom.left + frac * geom.plotWidth;
}

// Sekunden-Offsets der Durchgangsenden. cycle_ends_s stammt aus T-1019f
// Stufe 3a und ist exakt; fehlt es (Laeufe von vorher), wird gleichmaessig
// genaehert - das truegt, weil ein Durchgang ueber viele belegte Kanaele
// laenger dauert, ist aber besser als keine Achse. Der Rueckgabewert traegt
// exakt mit, damit die Legende es benennen kann.
function levelCycleAxis(run) {
  const n = run.cycles || 0;
  const gemessen = run.cycle_ends_s;
  if (Array.isArray(gemessen) && gemessen.length) {
    return { offsets: gemessen.slice(), exakt: true };
  }
  let dauerS = 0;
  if (run.start_iso && (run.ended_iso || run.end_iso)) {
    dauerS = (new Date(run.ended_iso || run.end_iso) - new Date(run.start_iso)) / 1000;
  }
  if (!(dauerS > 0) || !n) return { offsets: [], exakt: false };
  const off = [];
  for (let i = 1; i <= n; i++) off.push(dauerS * i / n);
  return { offsets: off, exakt: false };
}

// Farbe eines Balkens nach Pegel: blau (schwach) bis rot (stark), Grenzen aus
// dem WERTEBEREICH DES LAUFS. Eine feste Skala 0-120 dBuV wuerde die real
// gemessenen 20-47 dBuV auf drei ununterscheidbare Toene stauchen. Ohne
// Pegel (Laeufe vor T-1019f Stufe 1, per_cycle als Paare) bleibt es beim
// einfarbigen Blau der Tonlauf-Matrix.
function levelColor(dbuv, skala) {
  if (dbuv == null || !skala) return '#6bf';
  const spanne = skala.max - skala.min;
  const frac = spanne > 0 ? Math.max(0, Math.min(1, (dbuv - skala.min) / spanne)) : 0.5;
  return 'hsl(' + Math.round(210 - 210 * frac) + ', 85%, 58%)';
}

// Wertebereich der Pegel ueber alle Durchgaenge aller Kanaele. null, wenn der
// Lauf keine Pegel je Durchgang traegt.
function levelScale(rows) {
  let min = null, max = null;
  (rows || []).forEach(ch => {
    (ch.per_cycle || []).forEach(e => {
      if (e.length < 3 || e[2] == null) return;
      if (min == null || e[2] < min) min = e[2];
      if (max == null || e[2] > max) max = e[2];
    });
  });
  return min == null ? null : { min, max };
}

function matrixRowY(rowIndex, geom) {
  return geom.headerH + rowIndex * geom.rowH;
}

function matrixBarAt(px, py, bars) {
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    if (px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h) return b.ref;
  }
  return null;
}

function truncateLabel(label, n) {
  if (!label) return '';
  return label.length > n ? label.slice(0, n - 1) + '…' : label;
}

// T-1106-Nachtrag: Aufnahme-Zeitraum ueber ALLE recordings der uebergebenen
// Kanaele (Min aller start_at, Max aller start_at + duration_s) - reine
// Funktion ohne DOM. null, wenn keine (gueltige) Aufnahme vorhanden ist.
function computeRecordingExtent(channels) {
  let t0Ms = null, t1Ms = null;
  (channels || []).forEach(ch => {
    (ch.recordings || []).forEach(rec => {
      if (!rec.start_at) return;
      const startMs = new Date(rec.start_at).getTime();
      if (isNaN(startMs)) return;
      const endMs = startMs + (rec.duration_s || 0) * 1000;
      if (t0Ms == null || startMs < t0Ms) t0Ms = startMs;
      if (t1Ms == null || endMs > t1Ms) t1Ms = endMs;
    });
  });
  if (t0Ms == null || t1Ms == null) return null;
  return { t0Ms, t1Ms };
}

function renderTimeline() {
  // T-1019f Stufe 3b: Ein Pegel-Lauf hat keine Aufnahmen - dort zeichnet eine
  // eigene Routine. Vier der Bausteine dieser Funktion (computeRunTimeRange,
  // computeRecordingExtent, matrixTimeToX und sie selbst) setzen eine
  // Wanduhr-Achse aus Aufnahmen voraus und liessen sich nicht anpassen,
  // sondern nur ersetzen.
  if (isLevelRun()) { renderLevelTimeline(); return; }
  const canvas = el('timelineCanvas');
  const hint = el('timelineHint');
  const runRange = computeRunTimeRange();
  // Ergebnis-Kanaele nach mem aufsteigend - der Mindestdauer-Filter des
  // Auswertung-Reiters (minDurInput) wirkt hier BEWUSST NICHT: die Matrix
  // zeigt IMMER alle Aufnahmen des Laufs, unabhaengig vom Filterstand.
  const rows = runData.channels.slice().sort((a, b) => {
    const ma = a.mem != null ? a.mem : 1e9, mb = b.mem != null ? b.mem : 1e9;
    return ma - mb;
  });
  timelineBars = [];
  if (!runRange || !rows.length) {
    canvas.hidden = true;
    hint.style.display = '';
    hint.textContent = !rows.length
      ? 'no active channels'
      : 'time span of the run cannot be determined (start/end missing)';
    return;
  }
  canvas.hidden = false;
  hint.style.display = 'none';

  // T-1106-Nachtrag: Anzeige folgt dem Aufnahme-Zeitraum (Polster
  // max(30 s, 2 % der Spanne) an beiden Enden), geklemmt auf den
  // (tatsaechlichen) Lauf-Zeitraum aus computeRunTimeRange() - keine
  // Aufnahmen -> Fallback auf den vollen Lauf-Zeitraum. Belegungs-%
  // rechnet weiterhin mit der Laufdauer (computeRunTimeRange), NICHT mit
  // diesem (ggf. kuerzeren) Anzeigebereich.
  const extent = computeRecordingExtent(rows);
  let t0Ms = runRange.t0Ms, t1Ms = runRange.t1Ms;
  if (extent) {
    const pad = Math.max(30000, (extent.t1Ms - extent.t0Ms) * 0.02);
    t0Ms = Math.max(runRange.t0Ms, extent.t0Ms - pad);
    t1Ms = Math.min(runRange.t1Ms, extent.t1Ms + pad);
  }

  const height = TIMELINE_HEADER_H + rows.length * TIMELINE_ROW_H + TIMELINE_AXIS_H;
  canvas.width = TIMELINE_WIDTH;
  canvas.height = height;
  const geom = {
    left: TIMELINE_LEFT, plotWidth: TIMELINE_WIDTH - TIMELINE_LEFT - 10,
    t0Ms, t1Ms, headerH: TIMELINE_HEADER_H, rowH: TIMELINE_ROW_H,
  };

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.font = '11px sans-serif';
  ctx.textBaseline = 'middle';

  rows.forEach((ch, i) => {
    const rowTop = matrixRowY(i, geom);
    ctx.fillStyle = i % 2 === 0 ? '#161c24' : '#12171d';
    ctx.fillRect(0, rowTop, canvas.width, geom.rowH);
    ctx.strokeStyle = '#2a323c';
    ctx.beginPath();
    ctx.moveTo(0, rowTop + geom.rowH);
    ctx.lineTo(canvas.width, rowTop + geom.rowH);
    ctx.stroke();
    ctx.fillStyle = '#9ab';
    const label = truncateLabel(ch.label || ch.channel_id || '?', 16) + ' – ' + fmtMHz(ch.freq_hz);
    ctx.fillText(label, 4, rowTop + geom.rowH / 2);

    (ch.recordings || []).forEach(rec => {
      if (!rec.start_at) return;
      const startMs = new Date(rec.start_at).getTime();
      if (isNaN(startMs)) return;
      const endMs = startMs + (rec.duration_s || 0) * 1000;
      const x0 = matrixTimeToX(startMs, geom);
      const x1 = matrixTimeToX(endMs, geom);
      const w = Math.max(2, x1 - x0);
      const y = rowTop + (geom.rowH - TIMELINE_BAR_H) / 2;
      ctx.fillStyle = '#6bf';
      ctx.fillRect(x0, y, w, TIMELINE_BAR_H);
      timelineBars.push({
        x: x0, y, w, h: TIMELINE_BAR_H,
        ref: Object.assign({ channel_label: ch.label }, rec),
      });
    });
  });

  const axisY = TIMELINE_HEADER_H + rows.length * TIMELINE_ROW_H;
  ctx.strokeStyle = '#456';
  ctx.beginPath();
  ctx.moveTo(geom.left, axisY);
  ctx.lineTo(canvas.width, axisY);
  ctx.stroke();
  const nTicks = 5;   // 6 Marken (0..5) - innerhalb der geforderten 4-6
  ctx.fillStyle = '#9ab';
  ctx.textBaseline = 'top';
  for (let i = 0; i <= nTicks; i++) {
    const tMs = geom.t0Ms + (geom.t1Ms - geom.t0Ms) * (i / nTicks);
    const x = matrixTimeToX(tMs, geom);
    ctx.strokeStyle = '#456';
    ctx.beginPath();
    ctx.moveTo(x, axisY);
    ctx.lineTo(x, axisY + 4);
    ctx.stroke();
    ctx.fillText(new Date(tMs).toTimeString().slice(0, 8), Math.min(x, canvas.width - 60), axisY + 6);
  }
}

// T-1019f Stufe 3b/3c: Matrix eines Pegel-Laufs. Aufbau wie
// renderTimeline() - eine Zeile je Kanal, Achse unten -, aber gespeist aus
// per_cycle statt aus recordings[]: ein Balken je Durchgang mit Treffer,
// Breite = Dauer dieses Durchgangs, Farbe nach seinem Pegelmaximum.
function renderLevelTimeline() {
  const canvas = el('timelineCanvas');
  const hint = el('timelineHint');
  const run = runData.run || {};
  // T-1019h: Zeilenreihenfolge waehlbar. Speicherplatz ist die Vorgabe (die
  // Reihenfolge, in der die Kanaele im Job stehen); nach Oeffnungen oder
  // aktiven Durchgaengen sortiert stehen die ergiebigen Kanaele oben, was bei
  // vielen Zeilen der eigentliche Gewinn ist.
  const sortKey = el('timelineSort').value || 'mem';
  const rows = runData.channels.slice().sort((a, b) => {
    if (sortKey === 'freq') return (a.freq_hz || 0) - (b.freq_hz || 0);
    if (sortKey === 'hits') return (b.hits || 0) - (a.hits || 0);
    if (sortKey === 'cycles') return (b.cycles_active || 0) - (a.cycles_active || 0);
    const ma = a.mem != null ? a.mem : 1e9, mb = b.mem != null ? b.mem : 1e9;
    return ma - mb;
  });
  timelineBars = [];
  const achse = levelCycleAxis(run);
  if (!rows.length || !achse.offsets.length) {
    canvas.hidden = true;
    hint.style.display = '';
    hint.textContent = !rows.length
      ? 'no active channels'
      : 'time axis cannot be determined (neither cycle times nor run duration)';
    return;
  }
  canvas.hidden = false;

  const skala = levelScale(rows);
  const height = TIMELINE_HEADER_H + rows.length * TIMELINE_ROW_H + TIMELINE_AXIS_H;
  canvas.width = TIMELINE_WIDTH;
  canvas.height = height;
  const geom = {
    left: TIMELINE_LEFT, plotWidth: TIMELINE_WIDTH - TIMELINE_LEFT - 10,
    offsets: achse.offsets, s0: 0, s1: achse.offsets[achse.offsets.length - 1],
    headerH: TIMELINE_HEADER_H, rowH: TIMELINE_ROW_H,
  };
  if (!(geom.s1 > geom.s0)) geom.s1 = geom.s0 + 1;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.font = '11px sans-serif';
  ctx.textBaseline = 'middle';

  rows.forEach((ch, i) => {
    const rowTop = matrixRowY(i, geom);
    ctx.fillStyle = i % 2 === 0 ? '#161c24' : '#12171d';
    ctx.fillRect(0, rowTop, canvas.width, geom.rowH);
    ctx.strokeStyle = '#2a323c';
    ctx.beginPath();
    ctx.moveTo(0, rowTop + geom.rowH);
    ctx.lineTo(canvas.width, rowTop + geom.rowH);
    ctx.stroke();
    ctx.fillStyle = '#9ab';
    const label = truncateLabel(ch.label || ch.channel_id || '?', 16) + ' – ' + fmtMHz(ch.freq_hz);
    ctx.fillText(label, 4, rowTop + geom.rowH / 2);

    (ch.per_cycle || []).forEach(e => {
      const zyklus = e[0] || 0;
      const treffer = e[1] || 0;
      const pegel = e.length > 2 ? e[2] : null;
      const x1 = matrixCycleToX(zyklus, geom);
      const x0 = zyklus > 0 ? matrixCycleToX(zyklus - 1, geom) : geom.left;
      const w = Math.max(2, x1 - x0);
      const y = rowTop + (geom.rowH - TIMELINE_BAR_H) / 2;
      ctx.fillStyle = levelColor(pegel, skala);
      ctx.fillRect(x0, y, w, TIMELINE_BAR_H);
      timelineBars.push({
        x: x0, y, w, h: TIMELINE_BAR_H,
        ref: { channel_label: ch.label, cycle: zyklus, hits: treffer, level_max_dbuv: pegel },
      });
    });
  });

  const axisY = TIMELINE_HEADER_H + rows.length * TIMELINE_ROW_H;
  ctx.strokeStyle = '#456';
  ctx.beginPath();
  ctx.moveTo(geom.left, axisY);
  ctx.lineTo(canvas.width, axisY);
  ctx.stroke();
  const startMs = run.start_iso ? new Date(run.start_iso).getTime() : null;
  const nTicks = 5;
  ctx.fillStyle = '#9ab';
  ctx.textBaseline = 'top';
  for (let i = 0; i <= nTicks; i++) {
    const sek = geom.s0 + (geom.s1 - geom.s0) * (i / nTicks);
    const frac = (sek - geom.s0) / (geom.s1 - geom.s0);
    const x = geom.left + frac * geom.plotWidth;
    ctx.strokeStyle = '#456';
    ctx.beginPath();
    ctx.moveTo(x, axisY);
    ctx.lineTo(x, axisY + 4);
    ctx.stroke();
    // Uhrzeit, wenn der Laufbeginn bekannt ist - sonst Sekunden seit Start.
    const txt = startMs != null
      ? new Date(startMs + sek * 1000).toTimeString().slice(0, 8)
      : Math.round(sek) + ' s';
    ctx.fillText(txt, Math.min(x, canvas.width - 60), axisY + 6);
  }

  // Legende statt Fehlermeldung: das Hinweisfeld unter dem Bild traegt die
  // Farbgrenzen (sie gelten nur fuer DIESEN Lauf) und sagt, ob die Achse
  // gemessen oder genaehert ist.
  hint.style.display = '';
  hint.textContent = (skala
    ? 'Color: ' + skala.min.toFixed(1) + ' dBµV (blue) to ' + skala.max.toFixed(1) +
      ' dBµV (red), limits from this run'
    : 'single color – this run carries no level per cycle') +
    ' · time axis ' + (achse.exakt ? 'measured' : 'approximated from the run duration');
}

function showTimelineTooltip(pageX, pageY, html) {
  const tip = el('timelineTooltip');
  tip.innerHTML = html;
  tip.style.left = (pageX + 12) + 'px';
  tip.style.top = (pageY + 12) + 'px';
  tip.hidden = false;
}

el('timelineCanvas').addEventListener('mousemove', ev => {
  const rect = ev.target.getBoundingClientRect();
  const bar = matrixBarAt(ev.clientX - rect.left, ev.clientY - rect.top, timelineBars);
  if (!bar) { el('timelineTooltip').hidden = true; return; }
  if (isLevelRun()) {
    // T-1019f Stufe 3b: andere Groessen, also anderer Text - der Balken steht
    // fuer einen Durchgang, nicht fuer eine Aufnahme.
    showTimelineTooltip(ev.pageX, ev.pageY,
      esc(bar.channel_label || '') + '<br>Cycle ' + bar.cycle + ' · ' +
      bar.hits + ' ' + pluralWort(bar.hits, 'opening', 'openings') +
      (bar.level_max_dbuv != null ? ' · ' + bar.level_max_dbuv.toFixed(1) + ' dBµV' : ''));
    return;
  }
  showTimelineTooltip(ev.pageX, ev.pageY,
    esc(bar.channel_label || '') + '<br>' +
    fmtDateTime(bar.start_at) + ' · ' + (bar.duration_s != null ? bar.duration_s.toFixed(1) : '?') + ' s' +
    (bar.level_max_dbuv != null ? ' · ' + bar.level_max_dbuv.toFixed(1) + ' dBµV' : '') +
    '<br>' + esc(bar.file || ''));
});
el('timelineSort').onchange = () => { renderTimeline(); };
el('timelineCanvas').addEventListener('mouseleave', () => { el('timelineTooltip').hidden = true; });
el('timelineCanvas').addEventListener('click', ev => {
  const rect = ev.target.getBoundingClientRect();
  const bar = matrixBarAt(ev.clientX - rect.left, ev.clientY - rect.top, timelineBars);
  // T-1019f Stufe 3b: Ein Pegel-Balken hat keine Datei, der Sprung in die
  // Trefferliste ginge ins Leere - die Bedingung faengt das mit ab.
  if (!bar || !bar.file) return;
  showResultTab('recs');
  const row = document.querySelector('#recBody tr[data-file="' + CSS.escape(bar.file) + '"]');
  if (row) {
    row.scrollIntoView({ block: 'center' });
    row.classList.add('rec-highlight');
    setTimeout(() => row.classList.remove('rec-highlight'), 2000);
  } else {
    showTimelineTooltip(ev.pageX, ev.pageY, 'row hidden by the min. duration filter');
    setTimeout(() => { el('timelineTooltip').hidden = true; }, 2000);
  }
});

async function loadResult() {
  try {
    runData = await fetchJSON('/api/run?run=' + encodeURIComponent(runId));
  } catch (e) {
    showError(e.message);
    return;
  }
  allRecordings = buildFlatRecordings();
  const range = computeRunTimeRange();
  runDurationS = range ? (range.t1Ms - range.t0Ms) / 1000 : null;
  // T-1019d: "Recordings" und "Timeline" speisen sich beide aus
  // recordings[] und blieben bei einem Pegel-Lauf dauerhaft leer - ein
  // leerer Reiter sieht aus wie ein Fehler. Die Panels bleiben im Markup,
  // nur die Reiterknoepfe verschwinden. Die Leiste "Filter recordings"
  // (Mindestdauer/Kanal/Text) filtert ebenfalls nur Aufnahmen und faellt
  // mit weg; ihre Eingabefelder bleiben im DOM, damit die Leser in
  // filteredRecordings()/computeChannelStats() weiterhin greifen.
  if (isLevelRun()) {
    el('tabRecs').style.display = 'none';
    el('filterRow').style.display = 'none';
    el('playerBar').style.display = 'none';
    // T-1019f Stufe 3b: Der Zeitverlauf ist zurueck - er zeichnet fuer einen
    // Pegel-Lauf aus per_cycle statt aus recordings[] (renderLevelTimeline).
    // Er behaelt bewusst SEIN Panel: Canvas, Tooltip und Hinweisfeld stehen
    // dort bereits. Der Auswertungs-Reiter bleibt ausgeblendet (T-1019g), die
    // Reiterleiste zeigt damit genau das, was der Lauf hergibt.
    el('tabTimeline').style.display = '';
    el('timelineSortRow').style.display = '';
    // T-1019g: Die Auswertung zeigt bei einem Pegel-Lauf dieselben Zahlen
    // wie die Kanaltabelle, nur ohne Verlauf - zwei Reiter fuer denselben
    // Inhalt. Das Panel bleibt im Markup und uebernimmt mit T-1019f
    // Stufe 3 den Zeitverlauf.
    el('tabEval').style.display = 'none';
  }
  renderHeader();
  populateChannelSelect();
  renderChanTable();
  renderRecTable();
  renderSilentTable();
  renderTimeline();
  renderEvalTable();
}

function showResultTab(name) {
  el('tabChan').classList.toggle('active', name === 'chan');
  el('tabSilent').classList.toggle('active', name === 'silent');
  el('tabRecs').classList.toggle('active', name === 'recs');
  el('tabTimeline').classList.toggle('active', name === 'timeline');
  el('tabEval').classList.toggle('active', name === 'eval');
  el('chanPanel').classList.toggle('active', name === 'chan');
  el('silentPanel').classList.toggle('active', name === 'silent');
  el('recPanel').classList.toggle('active', name === 'recs');
  el('timelinePanel').classList.toggle('active', name === 'timeline');
  el('evalPanel').classList.toggle('active', name === 'eval');
}
el('tabChan').onclick = () => showResultTab('chan');
el('tabSilent').onclick = () => showResultTab('silent');
el('tabRecs').onclick = () => showResultTab('recs');
el('tabTimeline').onclick = () => showResultTab('timeline');
el('tabEval').onclick = () => showResultTab('eval');
el('minDurInput').oninput = () => { renderChanTable(); renderRecTable(); renderEvalTable(); };
el('channelSelect').onchange = () => renderRecTable();
el('textFilter').oninput = () => renderRecTable();

el('silentAllCb').onchange = () => {
  const list = runData.silent_channels || [];
  if (el('silentAllCb').checked) {
    list.forEach(c => { if (c.id) silentSelected.add(c.id); });
  } else {
    list.forEach(c => { if (c.id) silentSelected.delete(c.id); });
  }
  renderSilentTable();
};

el('silentRemoveBtn').onclick = async () => {
  const mscanJobId = (runData.run.params || {}).mscan_job_id;
  if (!mscanJobId) return;
  if (silentSelected.size === 0) {
    el('silentMsg').classList.remove('err');
    el('silentMsg').textContent = 'No channels selected.';
    return;
  }
  const ids = Array.from(silentSelected);
  if (!confirm('Remove ' + ids.length + ' ' + pluralWort(ids.length, 'channel', 'channels') + ' from definition ' + mscanJobId +
               '? The run snapshot stays unchanged.')) return;
  el('silentRemoveBtn').disabled = true;
  try {
    await fetchJSON('http://' + location.hostname + ':8050/api/mscan/channels', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: mscanJobId, remove: ids }),
    });
    el('silentMsg').classList.remove('err');
    el('silentMsg').textContent = ids.length + ' ' + pluralWort(ids.length, 'channel', 'channels') + ' removed from definition ' + mscanJobId +
      ' – the run snapshot (list here) stays unchanged; the next run ' +
      'uses the reduced definition.';
    silentSelected.clear();
    renderSilentTable();
  } catch (e) {
    el('silentMsg').classList.add('err');
    el('silentMsg').textContent = e.message;
  } finally {
    el('silentRemoveBtn').disabled = false;
  }
};

loadResult();
</script>
</body>
</html>
"""

def _assemble(page: str, header: str) -> str:
    return (page.replace("/*COMMON_CSS*/", COMMON_CSS)
                .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                .replace("/*COMMON_JS*/", _COMMON_JS_COPY)
                .replace("/*HEADER*/", header))

HTML_MSCAN_RESULT = _assemble(HTML_MSCAN_RESULT, header_html("MSCAN run", view_id="pageTitle"))
HTML_NO_RUN = _assemble(HTML_NO_RUN, header_html("MSCAN analysis"))


# ---------------------------------------------------------------------------
# HTTP-Handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    store: JobStore = None

    def log_message(self, fmt, *args):
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                params = parse_qs(parsed.query)
                if params.get("run", [""])[0]:
                    self._send_html(HTML_MSCAN_RESULT)
                else:
                    self._send_html(HTML_NO_RUN)
            elif parsed.path == "/api/run":
                self._handle_api_run(parse_qs(parsed.query))
            elif parsed.path == "/api/wav":
                self._handle_api_wav(parse_qs(parsed.query))
            else:
                self._send_json({"error": "unknown endpoint"}, status=404)
        except Exception:
            # Sicherheitsnetz - jeder erwartete Fehlerfall wird bereits gezielt
            # in den _handle_*-Methoden abgefangen (Muster analyze_audio.py).
            self._send_json({"error": "internal error"}, status=500)

    def _handle_api_run(self, params: dict) -> None:
        run_id = params.get("run", [""])[0]
        try:
            result = build_mscan_run_result(self.store, run_id)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except KeyError as exc:
            self._send_json({"error": str(exc)}, status=404)
            return
        self._send_json(result)

    def _handle_api_wav(self, params: dict) -> None:
        """Liefert die WAV mit HTTP-Range-Unterstuetzung (parse_range()
        oben, Kopie aus analyze_audio.py). Sandbox-/Whitelist-Verstoesse
        aus resolve_run_file() werden IMMER generisch 403 (kein Namensecho) -
        Verhalten des bisherigen /api/mscan/wav-Handlers in manage_jobs.py."""
        run_id = params.get("run", [""])[0]
        name = params.get("file", [""])[0]
        try:
            path = resolve_run_file(self.store, run_id, name)
        except ValueError:
            self._send_json({"error": "access denied"}, status=403)
            return
        except (KeyError, FileNotFoundError):
            self._send_json({"error": "not found"}, status=404)
            return

        try:
            size = os.path.getsize(path)
        except OSError:
            self._send_json({"error": "not found"}, status=404)
            return

        try:
            rng = parse_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return

        if rng is None:
            start, end, status = 0, size - 1, 200
        else:
            start, end = rng
            status = 206

        length = end - start + 1
        try:
            self.send_response(status)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Browser bricht Audio-Streams beim Spulen regelmaessig ab -
            # kein Fehler (Muster analyze_audio.py/manage_jobs._send_file).
            pass

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status: int = 200) -> None:
        import json
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


from eb200_core.http_base import QuietThreadingHTTPServer as ThreadingHTTPServer


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def _run_server() -> int:
    Handler.store = JobStore()
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"[analyze-mscan] Server laeuft auf http://{HTTP_HOST}:{HTTP_PORT}")
    register_server("analyze_mscan", HTTP_PORT)
    print("Beenden mit Strg+C")

    def handle_sigint(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, handle_sigint)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[main] fahre herunter ...")
        httpd.shutdown()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="MSCAN-Ergebnisansicht :8060 (T-1106a)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren (kein Server)")
    ap.add_argument("--data-dir", metavar="PFAD",
                    help="Datenwurzel (wertet eb200_core/paths.py beim Import aus, T-2003)")
    args = ap.parse_args()
    if args.selftest:
        _self_test()
        return 0
    return _run_server()


# ---------------------------------------------------------------------------
# Selbsttest (python analyze_mscan.py --selftest, ohne Hardware/Socket)
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile

    # --- T1: parse_range (Kurzform, Kernfaelle - volle Deckung siehe
    # analyze_audio.py, Ursprung dieser Kopie) --------------------------------
    size = 1000
    assert parse_range(None, size) is None
    assert parse_range("bytes=0-99", size) == (0, 99)
    assert parse_range("bytes=100-", size) == (100, size - 1)
    assert parse_range("bytes=-100", size) == (size - 100, size - 1)
    try:
        parse_range("bytes=5-2", size)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet (start > end)")
    try:
        parse_range("seiten=1", size)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet (falsche Einheit)")
    print("T1 ok  (parse_range: einfache Bereiche, Suffix, offenes Ende, kaputt/Unsinn)")

    # --- T2: Handler-Bausteine gegen tmp-JobStore (Muster mscan_view.py) -----
    with tempfile.TemporaryDirectory() as tmp:
        index_path = os.path.join(tmp, "jobs", "index.json")
        records_dir = os.path.join(tmp, "records")
        os.makedirs(records_dir, exist_ok=True)
        roots_path = os.path.join(tmp, "storage_roots.json")
        with open(roots_path, "w", encoding="utf-8") as f:
            import json
            json.dump([records_dir], f)
        store = JobStore(index_path=index_path, storage_roots_path=roots_path)

        other_job = store.create_job(
            type="dscan", label="Kein MSCAN",
            params={"start_hz": 88_000_000, "stop_hz": 90_000_000, "speed": "NORM"},
            start_iso="2020-01-01T10:00:00+01:00", end_iso="2020-01-01T11:00:00+01:00",
            decimation_s=5,
        )
        run_manifest = store.create_job(
            type="mscan", label="Handler-Test",
            params={"mscan_job_id": None, "title": "Handler-Test", "mode": "ueberwachung",
                    "dwell_s": None, "hold_s": None, "min_recording_s": None,
                    "squelch_from_memory": None,
                    "channels": [{"mem": 0, "id": "T1", "label": "Test T1", "freq_hz": 100_000_000,
                                  "demod": "FM", "bw_hz": 15000, "service": None, "file": None}]},
            start_iso="2096-02-01T10:00:00+01:00", end_iso="2096-02-01T10:15:00+01:00",
        )
        run_id = run_manifest["job_id"]
        job_dir = store.get(run_id)["job_dir"]
        with open(os.path.join(job_dir, "a.wav"), "wb") as f:
            f.write(b"\x00" * 44)
        store.heartbeat(run_id, stats={"cycles": 1, "recordings": 1, "active_channels": 1},
                         files=["a.wav"],
                         results={"channels": [{
                             "channel_id": "T1", "label": "Test T1", "freq_hz": 100_000_000,
                             "mem": 0, "hits": 1, "total_s": 3.5, "level_max_dbuv": 40.0,
                             "recordings": [{"file": "a.wav", "start_at": "2096-02-01T10:00:05+01:00",
                                             "duration_s": 3.5, "level_max_dbuv": 40.0, "cycle": 1,
                                             "device_cycle": 1, "bytes": 44}],
                         }], "cycles": 1, "audio_orphan": 0, "audio_orphan_pre": 0,
                             "audio_orphan_mid": 0, "audio_orphan_post": 0})

        # /api/run-Baustein (build_mscan_run_result) - 404/400-Abbildung
        # wird hier direkt an den zugrundeliegenden Ausnahmen geprueft
        # (Handler._handle_api_run macht daraus nur noch HTTP-Status).
        result = build_mscan_run_result(store, run_id)
        assert result["run"]["run_id"] == run_id, result["run"]
        try:
            build_mscan_run_result(store, other_job["job_id"])
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: build_mscan_run_result auf dscan-Job (-> 400)")
        try:
            build_mscan_run_result(store, "unbekannt-id")
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: unbekannte run_id (-> 404)")

        # /api/wav-Baustein (resolve_run_file) - Sandbox-Fehler -> 403,
        # unbekannt/fehlend -> 404 (siehe _handle_api_wav).
        path = resolve_run_file(store, run_id, "a.wav")
        assert os.path.isfile(path), path
        try:
            resolve_run_file(store, run_id, "fremd.wav")
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: nicht gelistete Datei (-> 403)")
        try:
            resolve_run_file(store, "unbekannt-id", "a.wav")
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: unbekannte run_id (-> 404)")

        print("T2 ok  (Handler-Bausteine: /api/run 400/404, /api/wav 403/404 ueber "
              "build_mscan_run_result/resolve_run_file gegen tmp-JobStore)")

    # --- T3: Pegel-Inventur-Darstellung (T-1019d Teil 2) ----------------------
    # Reine Frontend-Logik in HTML_MSCAN_RESULT - hier nur die Textbausteine
    # pruefen (kein Browser noetig), Muster manage_jobs.py T-1112.
    for _frag in ("function isLevelRun()", "LEVEL_CHAN_COLUMNS", "LEVEL_EVAL_COLUMNS",
                  "function fmtCyclesCell(c)", "function chanColumns()",
                  "function evalColumns()", "el('filterRow').style.display = 'none'",
                  "level only, no audio recording",
                  # T-1019f Stufe 2: Verlaufsstreifen
                  "function fmtCycleSpark(c)", "const SPARK_BARS = 120",
                  "label: 'History'", ".spark rect { fill:currentColor; }",
                  "per_cycle: ch.per_cycle || []",
                  # T-1019g: Kosmetik
                  "el('tabEval').style.display = 'none'",
                  # T-1019f Stufe 3b/3c: eigene Matrix mit Pegelfarbe
                  "function renderLevelTimeline()", "function matrixCycleToX(cycle, geom)",
                  "function levelCycleAxis(run)", "function levelColor(dbuv, skala)",
                  "function levelScale(rows)",
                  "if (isLevelRun()) { renderLevelTimeline(); return; }",
                  # T-1019h: Sortierung, Kopfzeile, ausgeblendete Player-Leiste
                  "id=\"timelineSort\"", "const sortKey = el('timelineSort').value",
                  "el('playerBar').style.display = 'none'",
                  "rows.push(kv('Programming'"):
        assert _frag in HTML_MSCAN_RESULT, _frag
    # T-1019g: Die Pegel-Kanaltabelle fuehrt keine Demod-Spalte mehr, und der
    # Streifen traegt keinen Tooltip. Beide Proben laufen gegen den
    # ausgeschnittenen Spalten- bzw. Funktionstext, nicht gegen die ganze
    # Seite - "label: 'Demod'" gehoert weiterhin zu CHAN_TABLE_COLUMNS des
    # Tonlaufs, und ein title-Element steht schon im Seitenkopf. Der
    # Kommentar im Streifen nennt das Element bewusst NICHT beim Namen, sonst
    # zaehlte die Verbotsprobe ihre eigene Begruendung mit.
    _lvl = HTML_MSCAN_RESULT[HTML_MSCAN_RESULT.index("const LEVEL_CHAN_COLUMNS = ["):]
    _lvl = _lvl[:_lvl.index("\n];")]
    assert "label: 'Demod'" not in _lvl, _lvl
    _spark = HTML_MSCAN_RESULT[HTML_MSCAN_RESULT.index("function fmtCycleSpark(c) {"):]
    _spark = _spark[:_spark.index("\n}")]
    assert "<title>" not in _spark, _spark
    for _frag in ():
        assert _frag in HTML_MSCAN_RESULT, _frag
    # Die Spaltensaetze duerfen nicht mehr fest verdrahtet gelesen werden -
    # beide Tabellen holen sie ueber chanColumns()/evalColumns().
    assert "buildSortableHead('chanHead', CHAN_TABLE_COLUMNS" not in HTML_MSCAN_RESULT
    assert "buildSortableHead('evalHead', EVAL_COLUMNS" not in HTML_MSCAN_RESULT
    # Kein Steuerzeichen im gerenderten Template (CLAUDE.md, eingebettete
    # Templates - faengt ein versehentlich gedeutetes Escape ab).
    assert not [c for c in HTML_MSCAN_RESULT if ord(c) < 32 and c not in "\n\t"]
    print("T3 ok  (Pegel-Inventur: isLevelRun/LEVEL-Spalten/Kopftext in HTML_MSCAN_RESULT, "
          "Spaltenwahl ueber chanColumns/evalColumns)")

    print("\n[analyze_mscan] Selbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    sys.exit(main())
