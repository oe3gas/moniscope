#!/usr/bin/env python3
"""
eb200_core/ui_theme.py

Gemeinsame Farb- und Stilschicht aller MoniScope-Server (Meilenstein 14,
T-1400). Reine Textbausteine (stdlib-only, keine Abhaengigkeiten), die jeder
Server in seine HTML-Templates einsetzt:

  THEME_CSS      Farb-Tokens als CSS-Variablen. :root = Light (Standard),
                 [data-theme="dark"] = Dark (die bisherigen Farben der Suite).
                 Beide Bloecke definieren dieselben Namen - _self_test prueft das.
  BASE_CSS       Grundregeln, die alle Server teilen (body, Kopfleiste, Knoepfe,
                 Felder, Tabellen, Reiter, Badges). Nur Tokens, keine Hex-Werte.
  THEME_HEAD_JS  Skript fuer den <head>: liest das Cookie und setzt data-theme
                 am <html>, BEVOR der Body gemalt wird (kein Aufblitzen);
                 stellt toggleTheme()/paintThemeToggle() bereit. Traegt seit
                 T-1801a LABELS_JS angehaengt (siehe dort).
  LABELS_JS      EB200_LABELS (Anzeige-Abbildung persistierter Datenwerte:
                 zustand, art, phase, klasse, level, bw_class, ...) sowie
                 labelOf()/labelOrDash() (T-1801). Wird an THEME_HEAD_JS
                 angehaengt statt einzeln eingesetzt, damit jeder Server sie
                 automatisch bekommt, ohne selbst geaendert zu werden. Nur die
                 Abbildung - die Anzeigestellen der Server stellt erst
                 T-1801b/T-1801c auf labelOf() um.
  header_html()  Kopfleiste (Marke, Ansichtsname, EB200-Status, Umschalter).

Beschluss 2026-08-31 (Entscheidungslog): Light ist Standard, Dark waehlbar;
kein prefers-color-scheme-Automatismus. Persistenz per Cookie "eb200_theme"
auf dem Host - Cookies ignorieren den Port, die Wahl gilt damit ueber alle
Server hinweg (localStorage waere je Port getrennt). Canvas-Flaechen
(Wasserfall, Spektrum, Pegel, Katalogstreifen) bleiben in beiden Themes
dunkel; nur das Drumherum wechselt.

Regel fuer Bedienglyphen (alle Server): Transport-, Pause-, Speichern-Symbole
als Inline-SVG mit fill:currentColor (.glyph), nie als Unicode-Zeichen mit
Emoji-Darstellung (U+23E9/U+23EA/U+23F8/U+1F5BC ...) - Windows rendert die
als farbige Emoji.

Einsatz in einem Server (Muster manage_jobs.py):
  from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
  <style>/*COMMON_CSS*/</style><script>/*THEME_HEAD*/</script> im <head>,
  COMMON_CSS = THEME_CSS + BASE_CSS + eigene Regeln, Platzhalter am Modulende
  per .replace() fuellen; header_html(...) statt <h1>.

Selbsttest: python eb200_core/ui_theme.py --selftest
"""
from __future__ import annotations

import re
import sys

THEME_COOKIE = "eb200_theme"

# ---------------------------------------------------------------------------
# Farb-Tokens. Reihenfolge und Namen in beiden Bloecken identisch halten.
# ---------------------------------------------------------------------------
THEME_CSS = """
  :root {
    --bg:            #f3f5f7;
    --panel:         #ffffff;
    --panel-2:       #eef1f4;
    --fg:            #1c2430;
    --fg-muted:      #6b7684;
    --fg-faint:      #9aa4b1;
    --heading:       #1c2430;
    --label:         #6b7684;
    --border:        #d9dee5;
    --border-strong: #b6bfca;
    --head-bg:       #0d2340;
    --head-fg:       #ffffff;
    --head-muted:    #9fb6d1;
    --head-border:   #0d2340;
    --head-danger:   #ff9a8a;
    --accent:        #1a6fb5;
    --accent-soft:   #e3effa;
    --accent-fg:     #ffffff;
    --primary:       #0d2340;
    --primary-fg:    #ffffff;
    --btn-bg:        #ffffff;
    --btn-fg:        #1a6fb5;
    --btn-border:    #1a6fb5;
    --btn-hover:     #e3effa;
    --tab-active-bg: #0d2340;
    --tab-active-fg: #ffffff;
    --danger:        #c0392b;
    --danger-soft:   #fbeae7;
    --success:       #2e7d4f;
    --success-soft:  #e6f4ea;
    --success-soft-strong: #bfe6cd;
    --warn:          #b7791f;
    --warn-soft:     #fbf1dc;
    --field-bg:      #ffffff;
    --field-border:  #b6bfca;
    --canvas-bg:     #000000;
    --canvas-border: #8e97a3;
    --canvas-overlay-bg:     rgba(10,16,24,0.86);
    --canvas-overlay-fg:     #e6ecf3;
    --canvas-overlay-border: #4a5766;
    --overlay-bg:    rgba(255,255,255,0.92);
    --shadow:        0 6px 24px rgba(20,30,50,0.18);
    --badge-audio-bg: #2e7d4f; --badge-audio-fg: #ffffff;
    --badge-mscan-bg: #5e35b1; --badge-mscan-fg: #ffffff;
    --badge-dscan-bg: #1a6fb5; --badge-dscan-fg: #ffffff;
    --badge-ifpan-bg: #00796b; --badge-ifpan-fg: #ffffff;
    --badge-iq-bg: #c05621; --badge-iq-fg: #ffffff;
    --font: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  }
  [data-theme="dark"] {
    --bg:            #111;
    --panel:         #181818;
    --panel-2:       #1e2530;
    --fg:            #ddd;
    --fg-muted:      #999;
    --fg-faint:      #666;
    --heading:       #9cf;
    --label:         #9cf;
    --border:        #454545;
    --border-strong: #48a;
    --head-bg:       #111;
    --head-fg:       #8fd;
    --head-muted:    #9cf;
    --head-border:   #223;
    --head-danger:   #f88;
    --accent:        #8cf;
    --accent-soft:   #1d2733;
    --accent-fg:     #012;
    --primary:       #246;
    --primary-fg:    #fff;
    --btn-bg:        #246;
    --btn-fg:        #fff;
    --btn-border:    #48a;
    --btn-hover:     #357;
    --tab-active-bg: #2a5a8a;
    --tab-active-fg: #fff;
    --danger:        #c66;
    --danger-soft:   #3a1010;
    --success:       #8f8;
    --success-soft:  #1a2a1a;
    --success-soft-strong: #24492f;
    --warn:          #fd8;
    --warn-soft:     #332a10;
    --field-bg:      #181818;
    --field-border:  #444;
    --canvas-bg:     #000;
    --canvas-border: #333;
    --canvas-overlay-bg:     rgba(10,16,24,0.86);
    --canvas-overlay-fg:     #e6ecf3;
    --canvas-overlay-border: #4a5766;
    --overlay-bg:    rgba(10,16,24,0.88);
    --shadow:        0 6px 24px rgba(0,0,0,0.55);
    --badge-audio-bg: #9f9; --badge-audio-fg: #012;
    --badge-mscan-bg: #c9f; --badge-mscan-fg: #012;
    --badge-dscan-bg: #8fd; --badge-dscan-fg: #012;
    --badge-ifpan-bg: #fc8; --badge-ifpan-fg: #012;
    --badge-iq-bg: #f93; --badge-iq-fg: #012;
    --font: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  }
"""

# ---------------------------------------------------------------------------
# Grundregeln (nur Tokens). Die Kopfleiste zieht sich per negativem Rand ueber
# die volle Breite, damit die Server ihre Body-Struktur nicht umbauen muessen.
# ---------------------------------------------------------------------------
BASE_CSS = """
  * { box-sizing:border-box; }
  html { background:var(--bg); }
  body { margin:0; padding:0 20px 24px; background:var(--bg); color:var(--fg);
         font-family:var(--font); font-size:14px; line-height:1.4; }
  a { color:var(--accent); }
  h2 { font-size:1em; font-weight:600; color:var(--heading); margin:22px 0 6px; }
  .hint { color:var(--fg-muted); font-size:0.85em; }
  .err { color:var(--danger); }
  .ok { color:var(--success); }
  .num { font-variant-numeric:tabular-nums; }
  button, .btn { font:inherit; font-size:0.92em; background:var(--btn-bg); color:var(--btn-fg);
    border:1px solid var(--btn-border); border-radius:4px; padding:5px 12px; cursor:pointer; }
  button:hover, .btn:hover { background:var(--btn-hover); }
  button.primary { background:var(--primary); color:var(--primary-fg); border-color:var(--primary); }
  button.primary:hover { filter:brightness(1.15); }
  button.danger { color:var(--danger); border-color:var(--danger); background:var(--panel); }
  button.danger:hover { background:var(--danger-soft); }
  button.active { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  button:disabled { opacity:0.45; cursor:not-allowed; }
  input[type=text], input[type=number], input[type=datetime-local], select, textarea {
    font:inherit; font-size:0.92em; background:var(--field-bg); color:var(--fg);
    border:1px solid var(--field-border); border-radius:4px; padding:5px 8px; }
  input[type=range], input[type=checkbox], input[type=radio] { accent-color:var(--accent); }
  table { border-collapse:collapse; font-size:0.92em; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--border); vertical-align:top; }
  th { font-weight:600; color:var(--fg); white-space:nowrap; }
  th.sortable { cursor:pointer; user-select:none; }
  th.sortable:hover { color:var(--accent); }
  th.num, td.num { text-align:right; white-space:nowrap; }
  /* Achtung bei GESTAPELTEN Canvas (Overlay ueber Plot, z. B. :8070): der obere
     braucht "background:transparent", sonst verdeckt er den unteren (T-1404a).
     Flaechen, die AUF einer Canvas liegen (Infokaesten, Listen), nehmen die
     Tokens --canvas-overlay-* - sie bleiben in beiden Themes dunkel (T-1407). */
  canvas { display:block; background:var(--canvas-bg); }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:6px; }
  .glyph { width:14px; height:14px; fill:currentColor; display:inline-block; vertical-align:-2px; }

  #headerBar { background:var(--head-bg); color:var(--head-fg); border-bottom:1px solid var(--head-border);
    display:flex; align-items:center; gap:14px; padding:10px 20px; margin:0 -20px 12px; }
  #headerBar .brand { font-size:1.25em; font-weight:600; letter-spacing:0.01em; }
  #headerBar .sep { width:1px; height:1.5em; background:var(--head-muted); opacity:0.5; }
  #headerBar .view-name { font-size:1.1em; }
  #headerBar .view-sub { color:var(--head-muted); }
  #headerBar .spacer { flex:1; }
  #headerBar .status-link { color:var(--head-fg); border:1px solid var(--head-muted); border-radius:4px;
    padding:4px 10px; text-decoration:none; font-size:0.9em; background:transparent; }
  #headerBar .status-link:hover { background:rgba(255,255,255,0.08); }
  #themeToggle { color:var(--head-fg); border:1px solid var(--head-muted); background:transparent;
    font-size:0.9em; padding:4px 10px; }
  #themeToggle:hover { background:rgba(255,255,255,0.08); }
  /* T-1810a: Glyphe des Umschalters ausdruecklich an die Kopfleistenfarbe binden,
     damit sie nicht von der Textfarbe des Knopfes abhaengt. Anlass war ein
     unsichtbarer Mond - dessen Ursache lag aber im Pfad, nicht in der Farbe
     (siehe T-1810b). Die Regel bleibt, weil sie die Farbe unabhaengig macht. */
  #themeToggle .glyph { fill:var(--head-fg); }
  .top-links { font-size:0.92em; margin:0 0 12px; display:flex; gap:22px; }

  .tabbar { display:flex; gap:4px; margin-top:18px; border-bottom:1px solid var(--border-strong); }
  .tabbar .tab { background:var(--panel); color:var(--accent); border:1px solid var(--border-strong); border-bottom:none;
    border-radius:6px 6px 0 0; padding:8px 18px; margin-bottom:-1px; font-size:0.95em; font-weight:400; }
  .tabbar .tab:hover { background:var(--accent-soft); }
  .tabbar .tab.active { background:var(--tab-active-bg); color:var(--tab-active-fg); border-color:var(--tab-active-bg); font-weight:600; }
  .tab-pane { display:none; }
  .tab-pane.active { display:block; margin-top:10px; }

  .badge { display:inline-block; padding:2px 8px; border-radius:3px; font-size:0.78em; font-weight:700;
           letter-spacing:0.03em; }
  .badge-audio { background:var(--badge-audio-bg); color:var(--badge-audio-fg); }
  .badge-mscan { background:var(--badge-mscan-bg); color:var(--badge-mscan-fg); }
  .badge-dscan { background:var(--badge-dscan-bg); color:var(--badge-dscan-fg); }
  .badge-ifpan { background:var(--badge-ifpan-bg); color:var(--badge-ifpan-fg); }
  /* T-1112: IQ ist keine Aufzeichnungsart, sondern ein Zusatz-Badge neben
     AUDIO (orange) - gesetzt von badgeIq() in manage_jobs.py. */
  .badge-iq { background:var(--badge-iq-bg); color:var(--badge-iq-fg); }
  .badge-unknown { background:var(--panel-2); color:var(--fg-muted); }

  /* Tooltip-Umbruch (T-1413). Lange Hover-Texte - typisch der an den Pegel
     angehaengte Katalogtreffer mit FNV-Zeile - liefen bisher als Einzeiler
     ueber die halbe Bildschirmbreite. Obergrenze in ch (Breite der Ziffer 0
     in der Schrift des Elements), damit die Grenze wirklich in ZEICHEN und
     nicht in Pixeln gilt; 92 liegt in dem von OE3GAS gesetzten Band 80-100.
     pre-line laesst eingebettete Zeilenumbrueche stehen, overflow-wrap
     bricht auch ueberlange Einzelwoerter (Dateipfade) um.
     Seitenspezifische Regeln duerfen white-space und max-width fuer diese
     beiden Selektoren NICHT mehr setzen - siehe Gates G5a/G5b.
     Nicht enthalten: #timelineTooltip (:8060, eigene 260-px-Grenze) und
     #knownTip (Rasterlayout, CSS im geteilten KNOWN_STRIP_BLOCK). */
  #tooltip, .hover-tip { max-width:92ch; white-space:pre-line;
    overflow-wrap:break-word; line-height:1.35; }
"""

# ---------------------------------------------------------------------------
# Head-Skript: Cookie -> data-theme (vor dem ersten Paint), Umschalter.
# ---------------------------------------------------------------------------
THEME_HEAD_JS = r"""
  (function(){
    var m = document.cookie.match(/(?:^|; )eb200_theme=(dark|light)/);
    if (m && m[1] === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
  })();
  function toggleTheme(){
    var h = document.documentElement;
    var dark = h.getAttribute('data-theme') === 'dark';
    if (dark) h.removeAttribute('data-theme'); else h.setAttribute('data-theme', 'dark');
    document.cookie = 'eb200_theme=' + (dark ? 'light' : 'dark') + '; path=/; max-age=31536000; SameSite=Lax';
    paintThemeToggle();
    document.dispatchEvent(new CustomEvent('themechange', { detail: { dark: !dark } }));
  }
  function paintThemeToggle(){
    var b = document.getElementById('themeToggle'); if (!b) return;
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    // T-1407b: Inline-SVG statt Unicode - U+2600/U+263E werden unter Windows
    // teils als farbige Emoji gerendert.
    b.innerHTML = dark ? '<svg class="glyph" viewBox="0 0 16 16"><path d="M8 4.8A3.2 3.2 0 108 11.2 3.2 3.2 0 008 4.8zM7 0h2v2.6H7zm0 13.4h2V16H7zM0 7h2.6v2H0zm13.4 0H16v2h-2.6zM2.3 3.7l1.4-1.4 1.8 1.8-1.4 1.4zm8.2 8.2 1.4-1.4 1.8 1.8-1.4 1.4zm1.8-9.6 1.4 1.4-1.8 1.8-1.4-1.4zM2.3 12.3l1.8-1.8 1.4 1.4-1.8 1.8z"/></svg> Light' : '<svg class="glyph" viewBox="0 0 16 16"><path d="M14.568 9.761A6.8 6.8 0 1 1 6.239 1.432A6.123 6.123 0 1 0 14.568 9.761Z"/></svg> Dark';
    b.title = dark ? 'Switch to light theme' : 'Switch to dark theme';
  }
  document.addEventListener('DOMContentLoaded', paintThemeToggle);
  // T-1803b: feste Datums- und Zeitschreibweise, ISO-nah und ohne Gebietsschema.
  // Vorher folgte die Anzeige an 20 Stellen der Browsersprache, weil kein
  // Gebietsschema angegeben war - zwei Leser sahen damit verschiedene Daten.
  // Ortszeit bleibt Ortszeit; wo ausdruecklich UTC gezeigt wird, steht das
  // weiterhin dabei.
  function _z2(n) { return n < 10 ? '0' + n : String(n); }
  function isoDate(d) {
    if (!(d instanceof Date) || isNaN(d.getTime())) return '-';
    return d.getFullYear() + '-' + _z2(d.getMonth() + 1) + '-' + _z2(d.getDate());
  }
  function isoTime(d) {
    if (!(d instanceof Date) || isNaN(d.getTime())) return '-';
    return _z2(d.getHours()) + ':' + _z2(d.getMinutes()) + ':' + _z2(d.getSeconds());
  }
  function isoHM(d) {
    if (!(d instanceof Date) || isNaN(d.getTime())) return '-';
    return _z2(d.getHours()) + ':' + _z2(d.getMinutes());
  }
  function isoDateTime(d) {
    if (!(d instanceof Date) || isNaN(d.getTime())) return '-';
    return isoDate(d) + ' ' + isoTime(d);
  }
  // T-1803c: Textsortierung mit festem Gebietsschema. Vorher sortierten vier Stellen
  // unterschiedlich - zwei nach Browsersprache, zwei nach deutscher Sortierung. Der
  // Schalter numerisch erhaelt die zahlenbewusste Variante fuer Kennungen wie 5A und
  // 10A, die sonst als 10A vor 5A einsortiert wuerden.
  function cmpText(a, b, numerisch) {
    return String(a).localeCompare(String(b), 'en', numerisch ? { numeric: true } : undefined);
  }
  // T-1804a: deutsche Pluralbildung an einer Stelle. Der Helfer liefert nur das WORT,
  // nicht die Zahl - so bleibt jede Zahlformatierung (fmtN, toFixed, roh) unberuehrt,
  // und die Aufrufstelle liest sich weiter wie ein Satz. Bei der Uebersetzung wird
  // lediglich das Wortpaar getauscht.
  function pluralWort(n, einzahl, mehrzahl) {
    return Math.abs(Number(n)) === 1 ? einzahl : mehrzahl;
  }
"""

# ---------------------------------------------------------------------------
# Anzeige-Abbildung persistierter Datenwerte (T-1801). Ein Objekt je
# Wertgruppe, eine Zeile je Gruppe, damit tools/*-Selbsttests die Codeliste
# per Regex je Gruppenzeile herauslesen koennen. Unbekannte Gruppe/Code:
# labelOf() reicht den Code unveraendert durch (siehe dort).
# ---------------------------------------------------------------------------
LABELS_JS = r"""
  /* ui-lang: ignore-begin - persistierte Datenwerte, siehe Glossar Abschnitt 4 */
  var EB200_LABELS = {
    zustand: {geplant:'Scheduled',laeuft:'Running',beendet:'Finished',gestoppt:'Stopped',abgebrochen:'Aborted',verpasst:'Missed',verwaist:'Orphaned',unbekannt:'Unknown'},
    art: {dscan:'D-SCAN',ifpan:'IFPAN',mscan:'MSCAN',audio:'Audio'},
    herkunft: {direkt:'Direct',longrun:'Long run'},
    mode: {inventur:'Inventory',ueberwachung:'Surveillance'},
    phase: {wartet:'Waiting',programmieren:'Programming',scan:'Scanning',ende:'Complete'},
    klasse: {LEER:'Empty',RAUSCHEN:'Noise',CW:'CW',DIGITAL:'Digital',SPRACHE:'Voice',UNKLAR:'Unclear'},
    sicherheit: {normal:'Normal',schwach:'Weak'},
    layer: {zuweisung:'Allocation',dienst:'Service'},
    confidence: {match:'Match',near:'Near'},
    level: {sicher:'Certain',wahrscheinlich:'Probable',moeglich:'Possible'},
    bw_class: {schmal:'Narrow',kanal:'Channel',breit:'Wide'},
    period_class: {schnell:'Fast',sekunden:'Seconds',minuten:'Minutes'},
    kind: {channel:'Single channel',raster:'Channel group'},
    empfangskette: {auto:'Auto',ein:'On',aus:'Off','n/a':'n/a'},
    sched_phase: {check:'Checking',start_dscan:'Starting D-SCAN',start_ifpan:'Starting IFPAN',start_mscan:'Starting MSCAN',stop_finished:'Stopping (finished)',stop_aborted:'Stopping (aborted)'},
    abort_code: {server_restart:'Server restart',server_stop:'Server stop',receiver_busy:'Receiver busy',recording_error:'Recording error',antenna_unknown:'Antenna not detectable',program_failed:'Programming failed',udp_bind_failed:'UDP bind failed',scpi_error:'SCPI error',bin_record_failed:'Bin recording could not be started',stop_while_programming:'Stopped while programming',unexpected_exit:'Unexpected exit',geometry_changed:'Geometry changed'},
    busy_by: {session_recording:'session recording',spectrum_recording:'spectrum recording',monitoring:'audio monitoring'},
    err_code: {outside_storage:'Path outside the allowed storage locations',wrong_extension:'Wrong file type',longrun_part:'Part of a long-run job, handle it via job management',may_be_running:'May still be running',incomplete:'Still running or incomplete',no_sidecar:'No sidecar file',sidecar_unreadable:'Sidecar unreadable',wrong_orphan_dir:'Not a longrun_* directory',no_storage_root:'Directory is not under any storage root',known_job_dir:'Directory belongs to a known job, delete it as a job',recently_written:'Directory was written to recently',unknown_kind:'Unknown kind',sigmf_iq_only:'SigMF export is available for IQ recordings only',not_found:'Recording not found',orphan_no_label:'Orphaned directories have no label',sigmf_export_failed:'SigMF export failed',no_index_entry:'No entry in jobs/index.json',unexpected_format:'Unexpected format',session_db_unreadable:'Session database unreadable',json_object_expected:'JSON object expected',kind_and_ref_required:'kind and ref are required',title_required:'Title must not be empty',retry_not_missed:'Retry is only possible for jobs in state missed',mscan_delete_blocked:'MSCAN job cannot be deleted while runs are planned or running',mscan_no_channels:'MSCAN job has no channels - a run cannot be started',mscan_missing_channels:'Channel IDs are missing from the catalog - clean up the channel list before starting',invalid_request:'Invalid request'}
  };
  /* ui-lang: ignore-end */
  function labelOf(gruppe, code) {
    if (code === null || code === undefined || code === '') return '';
    var tabelle = EB200_LABELS[gruppe];
    if (!tabelle) return code;
    var wert = tabelle[code];
    return (wert === undefined) ? code : wert;
  }
  function labelOrDash(gruppe, code) {
    if (code === null || code === undefined || code === '') return '-';
    return labelOf(gruppe, code);
  }
  /* T-1802b/T-1832: Abbruchgrund. Neue Abbrueche tragen abort_code plus abort_detail,
     alte Manifeste nur den deutschen Freitext abort_reason - deshalb der Rueckfall.
     Steht hier und nicht in einer einzelnen Seite, weil die Hauptseite (Spalte Zustand)
     und die MSCAN-Detailseite (Spalte Abort reason) sie beide brauchen. BUG-0006: sie
     lag bis T-1832 nur in HTML_PAGE, und die Lauftabelle der Detailseite blieb deshalb
     leer. */
  function abortText(r) {
    if (r.abort_code) {
      const txt = labelOf('abort_code', r.abort_code);
      // T-1821b: bei receiver_busy ist abort_detail ein Code aus busy_by; alte
      // Manifeste tragen dort einen deutschen Satz - labelOf gibt ihn unveraendert zurueck.
      const detail = r.abort_code === 'receiver_busy' ? labelOf('busy_by', r.abort_detail) : r.abort_detail;
      return detail ? txt + ': ' + detail : txt;
    }
    return r.abort_reason || '';
  }
  function errText(data, status) {
    if (data && data.error_code) {
      const txt = labelOf('err_code', data.error_code);
      const p = data.error_params || {};
      const keys = Object.keys(p);
      return keys.length ? txt + ' (' + keys.map(k => k + '=' + p[k]).join(', ') + ')' : txt;
    }
    return (data && data.error) || ('HTTP ' + status);
  }
"""

THEME_HEAD_JS = THEME_HEAD_JS + LABELS_JS


def header_html(view: str, sub: str = "", view_id: str = "", status_href: str = "",
                sub_id: str = "") -> str:
    """Kopfleiste. view = Ansichtsname, sub = Untertitel (optional), view_id =
    id des Ansichtsnamens (Server, die den Titel per JS setzen), sub_id = id
    des Untertitels; ist sie gesetzt, wird das Feld AUCH bei leerem sub
    angelegt (T-1625: :8070 traegt den Kurztext erst nach /api/meta nach),
    status_href =
    Ziel des EB200-Status-Links; leer = Link auf :8000/status desselben Hosts
    (Muster der Auswerte-Server), sonst der uebergebene Pfad (waterfall.py: /status)."""
    id_attr = f' id="{view_id}"' if view_id else ""
    sub_id_attr = f' id="{sub_id}"' if sub_id else ""
    sub_html = (f'\n  <span class="view-sub"{sub_id_attr}>{sub}</span>'
                if (sub or sub_id) else "")
    if status_href:
        status = f'<a href="{status_href}" class="status-link" target="eb200_status">&#9679; EB200 Status</a>'
    else:
        status = ('<a href="#" class="status-link" target="eb200_status"\n'
                  '     onclick="this.href=\'http://\'+location.hostname+\':8000/status\'">&#9679; EB200 Status</a>')
    return (
        '<div id="headerBar">\n'
        '  <span class="brand">MoniScope</span>\n'
        '  <span class="sep"></span>\n'
        f'  <span class="view-name"{id_attr}>{view}</span>{sub_html}\n'
        '  <span class="spacer"></span>\n'
        f'  {status}\n'
        '  <button id="themeToggle" type="button" onclick="toggleTheme()">&#9790; Dark</button>\n'
        '</div>'
    )


_HEX_RE = re.compile(r"(?<!&)#[0-9a-fA-F]{3,6}\b")
_TOKEN_RE = re.compile(r"(--[a-z0-9-]+)\s*:")

_LABEL_GRUPPENZEILE_RE = re.compile(r"^\s*(\w+):\s*\{(.*)\},?\s*$", re.MULTILINE)
_LABEL_EINTRAG_RE = re.compile(r"(?:'([^']*)'|(\w+)):'([^']*)'")
_UMLAUT_RE = re.compile(r"[äöüÄÖÜß]")


def _labels_aus_js(js_text):
    """Liest EB200_LABELS aus LABELS_JS zurueck in ein Python-dict
    {gruppe: {code: anzeige}} - fuer die Selbsttests L1/L2/L3/L6. Das Format
    ist bewusst eine Gruppe je Zeile (siehe LABELS_JS), damit dieser Parser
    ohne echtes JS-Parsing auskommt."""
    gruppen = {}
    for zeile in _LABEL_GRUPPENZEILE_RE.finditer(js_text):
        name, inhalt = zeile.group(1), zeile.group(2)
        eintraege = {}
        for treffer in _LABEL_EINTRAG_RE.finditer(inhalt):
            code = treffer.group(1) if treffer.group(1) is not None else treffer.group(2)
            eintraege[code] = treffer.group(3)
        if eintraege:
            gruppen[name] = eintraege
    return gruppen


def _self_test() -> None:
    light = THEME_CSS.split('[data-theme="dark"]')[0]
    dark = THEME_CSS.split('[data-theme="dark"]')[1]
    t_light = _TOKEN_RE.findall(light)
    t_dark = _TOKEN_RE.findall(dark)
    assert t_light == t_dark, (set(t_light) ^ set(t_dark))
    assert len(t_light) == len(set(t_light)), "doppelte Token"
    assert not _HEX_RE.search(BASE_CSS), "Hex-Farbe in BASE_CSS"
    assert ".badge-iq {" in BASE_CSS, "IQ-Badge-Klasse (T-1112) fehlt in BASE_CSS"
    assert "#tooltip, .hover-tip" in BASE_CSS and "92ch" in BASE_CSS, \
        "Tooltip-Umbruchregel (T-1413) fehlt in BASE_CSS"
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", BASE_CSS))
    assert used <= set(t_light), used - set(t_light)
    h = header_html("Test", sub="Sub", view_id="pageTitle")
    assert 'id="pageTitle"' in h and "Sub" in h and "themeToggle" in h and "location.hostname" in h
    # T-1625: Untertitelfeld wird auch ohne sub angelegt, wenn es eine id hat.
    h3 = header_html("Test", sub_id="pageSub")
    assert 'class="view-sub" id="pageSub"' in h3, h3
    assert 'class="view-sub"' not in header_html("Test"), "leerer Untertitel ohne id"
    h2 = header_html("Test", status_href="/status")
    assert 'href="/status"' in h2 and "location.hostname" not in h2
    assert THEME_COOKIE in THEME_HEAD_JS and "toggleTheme" in THEME_HEAD_JS

    # T-1810: Umschalterbeschriftung und Seitensprache sind englisch.
    for wort in ("Light", "Dark", "Switch to light theme", "Switch to dark theme"):
        assert wort in THEME_HEAD_JS or wort in header_html("Test"), \
            f"K1: {wort} fehlt in der Umschalterschicht"
    for wort in ("Hell", "Dunkel", "Design umschalten"):
        assert wort not in THEME_HEAD_JS, f"K1: {wort} noch in THEME_HEAD_JS"
        assert wort not in header_html("Test"), f"K1: {wort} noch in header_html"
    print("K1 ok: Umschalter englisch, keine deutschen Reste")

    # T-1810a: die Glyphe des Umschalters traegt eine eigene Farbregel; ueber
    # currentColor allein war sie auf der Kopfleiste unsichtbar.
    assert "#themeToggle .glyph" in BASE_CSS, "K2: Farbregel der Umschalter-Glyphe fehlt"
    assert "fill:var(--head-fg)" in BASE_CSS, "K2: Glyphe nicht an --head-fg gebunden"
    print("K2 ok: Umschalter-Glyphe an --head-fg gebunden")

    # T-1810b/d: die Mondglyphe schloss zuerst keine Flaeche ein (zwei
    # aufeinanderliegende Boegen), dann zeichnete sie zwei Sicheln, weil
    # fill-rule evenodd nicht abzieht, sondern die symmetrische Differenz bildet.
    # Endform: EINE Kontur aus Aussenbogen und Innenbogen, Umfassung 240 Grad.
    assert 'M9.2 1.6A6.4' not in THEME_HEAD_JS, "K3: alter, flaechenloser Mondpfad noch vorhanden"
    assert "fill-rule" not in THEME_HEAD_JS, \
        "K3: fill-rule zurueck - die Sichel ist EINE Kontur aus zwei Boegen, kein zweiter Kreis"
    assert 'M14.568 9.761A6.8 6.8 0 1 1 6.239 1.432A6.123 6.123 0 1 0 14.568 9.761Z' in THEME_HEAD_JS, \
        "K3: Mondpfad weicht von der abgenommenen Form ab"
    assert THEME_HEAD_JS.count("<svg") == 2, \
        f"K3: {THEME_HEAD_JS.count('<svg')} SVG statt 2 im Umschalter"
    print("K3 ok: Mondsichel als eine Kontur aus zwei Boegen, kein fill-rule")

    # T-1801a: Anzeige-Abbildung LABELS_JS.
    gruppen = _labels_aus_js(LABELS_JS)

    # L1: jede Gruppe hat mindestens einen Eintrag, kein Anzeigetext ist leer.
    for name, eintraege in gruppen.items():
        assert eintraege, f"L1: Gruppe {name} ohne Eintraege"
        for code, anzeige in eintraege.items():
            assert anzeige, f"L1: leerer Anzeigetext in {name}.{code}"
    print(f"L1 ok: {len(gruppen)} Gruppen, kein leerer Anzeigetext")

    # L2: Codelisten je Gruppe exakt gegen eine hartkodierte Erwartung.
    erwartete_codes = {
        "zustand": ["geplant", "laeuft", "beendet", "gestoppt", "abgebrochen", "verpasst", "verwaist", "unbekannt"],
        "art": ["dscan", "ifpan", "mscan", "audio"],
        "herkunft": ["direkt", "longrun"],
        "mode": ["inventur", "ueberwachung"],
        "phase": ["wartet", "programmieren", "scan", "ende"],
        "klasse": ["LEER", "RAUSCHEN", "CW", "DIGITAL", "SPRACHE", "UNKLAR"],
        "sicherheit": ["normal", "schwach"],
        "layer": ["zuweisung", "dienst"],
        "confidence": ["match", "near"],
        "level": ["sicher", "wahrscheinlich", "moeglich"],
        "bw_class": ["schmal", "kanal", "breit"],
        "period_class": ["schnell", "sekunden", "minuten"],
        "kind": ["channel", "raster"],
        "empfangskette": ["auto", "ein", "aus", "n/a"],
        "sched_phase": ["check", "start_dscan", "start_ifpan", "start_mscan", "stop_finished", "stop_aborted"],
        "abort_code": ["server_restart", "server_stop", "receiver_busy", "recording_error",
                       "antenna_unknown", "program_failed", "udp_bind_failed", "scpi_error",
                       "bin_record_failed", "stop_while_programming", "unexpected_exit",
                       "geometry_changed"],
        "err_code": ["outside_storage", "wrong_extension", "longrun_part", "may_be_running",
                     "incomplete", "no_sidecar", "sidecar_unreadable", "wrong_orphan_dir",
                     "no_storage_root", "known_job_dir", "recently_written", "unknown_kind",
                     "sigmf_iq_only", "not_found", "orphan_no_label", "sigmf_export_failed",
                     "no_index_entry", "unexpected_format", "session_db_unreadable",
                     "json_object_expected", "kind_and_ref_required", "title_required",
                     "retry_not_missed", "mscan_delete_blocked", "mscan_no_channels",
                     "mscan_missing_channels", "invalid_request"],
        "busy_by": ["session_recording", "spectrum_recording", "monitoring"],
    }
    assert len(erwartete_codes) == 18, len(erwartete_codes)
    assert set(gruppen.keys()) == set(erwartete_codes.keys()), set(gruppen.keys()) ^ set(erwartete_codes.keys())
    for name, codes in erwartete_codes.items():
        ist = sorted(gruppen[name].keys())
        soll = sorted(codes)
        assert ist == soll, f"L2: {name}: ist={ist} soll={soll}"
    print(f"L2 ok: {len(erwartete_codes)} Gruppen gegen hartkodierte Codelisten geprueft")

    # L3: kein Anzeigetext enthaelt einen Umlaut (EB200_LABELS ist die
    # englische Seite der Abbildung).
    for name, eintraege in gruppen.items():
        for code, anzeige in eintraege.items():
            assert not _UMLAUT_RE.search(anzeige), f"L3: Umlaut in {name}.{code} = {anzeige!r}"
    print("L3 ok: kein Umlaut in einem Anzeigetext")

    # L4: die Verkettung THEME_HEAD_JS = THEME_HEAD_JS + LABELS_JS wurde nicht
    # vergessen.
    assert "labelOf" in THEME_HEAD_JS and "EB200_LABELS" in THEME_HEAD_JS
    print("L4 ok: labelOf und EB200_LABELS in THEME_HEAD_JS enthalten")

    # L5: ausgewogen geklammert (die echte Syntaxpruefung macht
    # tools/check_js_blocks.py).
    assert LABELS_JS.count("{") == LABELS_JS.count("}"), \
        (LABELS_JS.count("{"), LABELS_JS.count("}"))
    print(f"L5 ok: {LABELS_JS.count('{')} '{{' == {LABELS_JS.count('}')} '}}' in LABELS_JS")

    # L6: kein Code kommt in zwei Gruppen mit unterschiedlichem Anzeigetext
    # vor. Gleichheit (CW -> CW) ist erlaubt, Widerspruch nicht.
    code_zu_anzeigen = {}
    for eintraege in gruppen.values():
        for code, anzeige in eintraege.items():
            code_zu_anzeigen.setdefault(code, set()).add(anzeige)
    widerspruechlich = {c: a for c, a in code_zu_anzeigen.items() if len(a) > 1}
    assert not widerspruechlich, widerspruechlich
    print("L6 ok: kein Code mit widerspruechlichem Anzeigetext ueber Gruppen hinweg")

    # T-1803b: gemeinsame Helfer fuer feste Datums-/Zeitschreibweise.
    # D1: isoDateTime und isoDate, isoTime, isoHM sind in THEME_HEAD_JS enthalten.
    for name in ("isoDateTime", "isoDate", "isoTime", "isoHM"):
        assert name in THEME_HEAD_JS, f"D1: {name} fehlt in THEME_HEAD_JS"
    print("D1 ok: isoDateTime, isoDate, isoTime, isoHM in THEME_HEAD_JS enthalten")

    # D2: der Block ist ausgewogen geklammert.
    assert THEME_HEAD_JS.count("{") == THEME_HEAD_JS.count("}"), \
        (THEME_HEAD_JS.count("{"), THEME_HEAD_JS.count("}"))
    print(f"D2 ok: {THEME_HEAD_JS.count('{')} '{{' == {THEME_HEAD_JS.count('}')} '}}' in THEME_HEAD_JS")

    # D3: keine der neuen Zeilen enthaelt toLocale.
    assert "toLocale" not in THEME_HEAD_JS, "D3: toLocale in THEME_HEAD_JS gefunden"
    print("D3 ok: kein toLocale in THEME_HEAD_JS")

    # T-1803c: gemeinsamer Sortierhelfer.
    # S1: cmpText ist in THEME_HEAD_JS enthalten.
    assert "cmpText" in THEME_HEAD_JS, "S1: cmpText fehlt in THEME_HEAD_JS"
    print("S1 ok: cmpText in THEME_HEAD_JS enthalten")

    # S2: der neue Block enthaelt kein de-AT und kein de-DE.
    assert "de-AT" not in THEME_HEAD_JS, "S2: de-AT in THEME_HEAD_JS gefunden"
    assert "de-DE" not in THEME_HEAD_JS, "S2: de-DE in THEME_HEAD_JS gefunden"
    print("S2 ok: kein de-AT und kein de-DE in THEME_HEAD_JS")

    # T-1804a: gemeinsamer Pluralhelfer. Der Selbsttest prueft Textbausteine,
    # nicht laufendes JS - P1/P2 bilden die beiden Zweige darum als Python-Code
    # nach (dieselbe Bedingung wie im JS-Helfer) statt sie im Text zu suchen.
    # P1: Einzahl bei n == 1.
    def _pluralwort_py(n, einzahl, mehrzahl):
        return einzahl if abs(n) == 1 else mehrzahl
    assert _pluralwort_py(1, "Datei", "Dateien") == "Datei", "P1: Einzahl bei n=1 erwartet"
    print("P1 ok: pluralWort(1, ...) liefert die Einzahl")

    # P2: Mehrzahl bei n == 0 und n == 2.
    assert _pluralwort_py(0, "Datei", "Dateien") == "Dateien", "P2: Mehrzahl bei n=0 erwartet"
    assert _pluralwort_py(2, "Datei", "Dateien") == "Dateien", "P2: Mehrzahl bei n=2 erwartet"
    print("P2 ok: pluralWort(0, ...) und pluralWort(2, ...) liefern die Mehrzahl")

    # P3: der Helfer selbst ist in THEME_HEAD_JS enthalten.
    assert "function pluralWort(n, einzahl, mehrzahl)" in THEME_HEAD_JS, \
        "P3: pluralWort fehlt in THEME_HEAD_JS"
    print("P3 ok: pluralWort in THEME_HEAD_JS enthalten")

    # P4 (T-1832, BUG-0006): abortText gehoert in die gemeinsame Schicht. Lag sie in
    # einer einzelnen Seite, warf jede andere Seite, die sie aufruft, einen
    # ReferenceError - genau so blieb die Lauftabelle der MSCAN-Detailseite leer.
    assert "function abortText(r)" in THEME_HEAD_JS, \
        "P4: abortText fehlt in THEME_HEAD_JS"
    assert "abort_code:" in THEME_HEAD_JS, \
        "P4: Labelgruppe abort_code fehlt in THEME_HEAD_JS"
    print("P4 ok: abortText und die Gruppe abort_code in THEME_HEAD_JS enthalten")

    print(f"ui_theme selftest ok: {len(t_light)} Token, {len(used)} in BASE_CSS verwendet")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _self_test()
    else:
        print(__doc__)
