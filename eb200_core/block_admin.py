#!/usr/bin/env python3
"""
block_admin.py

Browser-basierte Pflegemaske für die Frequenzkataloge (eb200_core/known_freq.py,
JSON-Dateien im Katalogverzeichnis). Hauptansicht (GET /) ist eine
Fliesenuebersicht der Katalogdateien; Klick auf eine Fliese oeffnet ein
Bearbeitungsblatt als Overlay mit Reitern (Bearbeiten/Datei-Kopf/JSON/Neuer
Kanal/Neue Kanalgruppe/Neues Band) - die Formular-Reiter sind eingebettet
und wechseln nur die sichtbare Pane, keine Navigation. Frueher (bis T-1008)
lag hier zusaetzlich eine SQL-Verwaltungsmaske fuer die Frequenzblock-Tabelle
(eb200_core/db.py); dieser Teil ist entfallen, /known bleibt als Redirect auf
die Wurzel fuer alte Links erhalten.

CSV-Export und CSV-Import (T-121, Export seit T-122) sind zwei getrennte
Knoepfe neben "+ New catalog file". GET /known/csv-export?file=<name> gibt
eine BESTEHENDE Katalogdatei als CSV aus - Kopfzeile und je eine Zeile pro
Band und Kanal, in der Reihenfolge der Datei, seit T-123 ohne jede
Kommentarzeile. Ausgegeben wird, was in der Datei STEHT: ein leeres Feld
heisst weiterhin "geerbt aus den defaults", der Export ist also die
Umkehrung des Imports und nicht die aufgeloeste Katalogsicht. Eine leere
Katalogdatei exportiert damit die Kopfzeile allein - die Vorlage zum
Ausfuellen (die fruehere Route /known/csv-template ist seit T-122
entfallen). Was die CSV nicht abbildet - Raster und Schluessel ohne Spalte -
steht seit T-123 im Export-Formular statt in der Datei.

Das Trennzeichen der Kopfzeile wird beim Import erkannt (Semikolon, Komma,
Tabulator - siehe _csv_delimiter_of). Der Export schreibt Semikolon, aber
Excel und LibreOffice speichern eine geoeffnete CSV je nach Version und
Spracheinstellung mit einem anderen Zeichen zurueck, als sie sie gelesen
haben; ohne Erkennung scheiterte genau daran jeder Re-Import (Befund T-123).

GET /known/csv-import bzw. POST /known/csv-import/save nehmen eine
ausgefuellte CSV als Datei-Upload entgegen. Die CSV kennt Kanaele und
Baender (Spalte typ), nicht aber Raster - ein Raster ist eine
Erzeugungsvorschrift, keine Zeile; der Export gibt es als Kommentarzeile mit
seinen Kennwerten aus und zaehlt es im Kopf. Gebaut wird ueber dieselben
build_*-Funktionen wie im Formular, geschrieben ueber
validate_candidate/_write_known_file. Der Import ist alles-oder-nichts: eine
einzige fehlerhafte Zeile schreibt nichts. Ziel ist wahlweise eine neue
Katalogdatei oder eine bestehende (Anhaengen); gleiche ids ueberschreiben
nur mit ausdruecklichem Haken, und dabei bleiben Schluessel ohne CSV-Spalte
(site/erp_w der ORS-Listen, segments eines Bandes) aus dem bestehenden
Eintrag erhalten - siehe _CSV_CHANNEL_KEYS.

Ansichtsauswahl (T-126): jede Fliese traegt einen Schalter "Ansicht". Was hier
abgeschaltet wird, verschwindet in den Anzeige-Servern (:8000 D-SCAN und IFPAN,
:8030 SDR-Scan, :8020, :8040) aus dem Katalogstreifen. Gespeichert wird als
AUSSCHLUSSliste in catalog_view.json (Repo-Root, nicht versioniert, Leser:
known_freq.load_view_config) - eine neue Katalogdatei ist damit sofort
sichtbar. Geschaltet wird ueber POST /known/view ohne Seitenwechsel; der
Editor selbst zeigt IMMER alle Dateien, der Schalter wirkt nur auf die
Darstellung in den Anzeige-Servern.

Architektur (bewusst analog zu waterfall.py gehalten, damit beide später in
eine gemeinsame Web-App zusammengeführt werden können):
  - Reines http.server (Python-Standardbibliothek) - kein Flask, kein
    zusätzliches Paket nötig.
  - Katalog-Uebersicht (GET /), Formulare fuer Kanal/Kanalgruppe/Band/
    Datei-Kopf/JSON (GET /known/...), je mit eigenem Speichern-POST.
  - Formular-Handling nach dem Post/Redirect/Get-Muster: nach jedem Speichern/
    Löschen wird auf GET /?open=<Datei> umgeleitet (das Bearbeitungsblatt
    der Datei oeffnet sich wieder), damit ein Browser-Reload nicht
    versehentlich denselben POST wiederholt.
  - Läuft eigenständig auf einem eigenen Port (Standard: 8010), unabhängig
    vom EB200-Gerät - die Pflegemaske braucht keine SCPI-/UDP-Verbindung,
    nur Zugriff auf das Katalogverzeichnis.

Formular-Besonderheiten:
  - Start-/Stopp-/Schrittfrequenz werden im Browser in kHz mit deutscher
    Zahlenformatierung angezeigt (Punkt = Tausendertrennzeichen, Komma =
    Dezimaltrennzeichen, bis zu 3 Nachkommastellen = 1 Hz Auflösung). Intern
    (Katalogdatei, Formularübertragung) wird weiterhin in Hz gerechnet - die
    Umrechnung passiert per JavaScript beim Verlassen des jeweiligen Feldes.
  - Bandbreite ist eine Auswahl aus den zwölf festen EB200-ZF-Filterstufen
    (0,15 kHz bis 150 kHz, siehe Annex C, MEMory:CONTents-Kodierung) statt
    eines Freitextfelds - am Gerät sind ohnehin nur diese Werte einstellbar.

Start (regulaer ueber start_eb200.py, cwd = Repo-Root):
    python eb200_core/block_admin.py
    -> Browser öffnen: http://localhost:8010/
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import shutil
import signal
import sys
import tempfile
from datetime import date
from typing import Optional
from urllib.parse import urlparse, parse_qs, quote

import http.server

# T-2000: Geschwistermodule nur mit Paketpraefix (eb200_core.xxx), wie in allen
# anderen Modulen. Beim Direktaufruf "python eb200_core/block_admin.py" (so
# startet start_eb200.py den Server) fehlt sonst das Repo-Root im Suchpfad -
# die Weiche ist wortgleich mit der in eb200_core/path_sandbox.py.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.param_store import register_server
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
from eb200_core.known_freq import (
    CATALOG_COLORS,
    CATALOG_COLOR_LABELS,
    resolve_color,
    DEFAULT_CATALOG_DIR,
    DEFAULT_VIEW_PATH,
    DEMOD_TOKENS,
    BW_STAGES_HZ,
    FREQ_MIN_HZ,
    FREQ_MAX_HZ,
    Catalog,
    load_catalog,
    load_view_config,
)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8010

# Auswahllisten für die Formularfelder. Bewusst als einfache Listen gehalten
# (keine eigene Tabelle in der DB) - bei Bedarf hier leicht erweiterbar.
ANTENNA_OPTIONS = [
    ("kw_dipol", "HF dipole (3–12 MHz)"),
    ("kw_beam", "HF beam (14–30 MHz)"),
    ("yagi_6m", "6 m Yagi (50–52 MHz)"),
    ("discone", "Discone (25–1300 MHz)"),
]

MODE_OPTIONS = ["AM", "FM", "USB", "LSB", "CW", "IQ"]

# Die zwölf festen ZF-Filterbandbreiten des EB200 (Annex C, packed_struct-
# Kodierung der Speicherplätze MEM0-999: 0=0.15kHz ... 11=150kHz). Andere
# Bandbreiten sind am Gerät nicht einstellbar, daher Auswahl statt Freitext.
BANDWIDTH_OPTIONS_HZ = [150, 300, 600, 1500, 2400, 6000, 9000, 15000, 30000, 50000, 120000, 150000]

# ---------------------------------------------------------------------------
# Known-Frequencies-Editor (known_freq/*.json - siehe eb200_core/known_freq.py)
# ---------------------------------------------------------------------------

# Katalog-Dateiname: kein Pfad, nur ein einfacher Name. Bewusst eng (wie die
# Kanal-id in known_freq._ID_RE), damit os.path.join(DEFAULT_CATALOG_DIR, name)
# nie das Verzeichnis verlassen kann.
_KNOWN_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.json$")

# Kanal-id: identisches Muster zu known_freq._ID_RE (dort privat, daher hier
# eine eigene Konstante fuer die Formular-Vorpruefung; massgeblich bleibt in
# jedem Fall die load_catalog()-Validierung im Temp-Spiegel).
_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _demod_options_inherit_html(selected: Optional[str], effective: Optional[str]) -> str:
    """
    <option>-Liste mit fuehrender "geerbt"-Option (value=""): zeigt den
    wirksamen (aus den Datei-defaults aufgeloesten) Wert als Hinweistext.
    selected ist der EXPLIZITE Wert des Eintrags selbst (None -> die
    geerbt-Option ist vorselektiert, der Eintrag hat kein eigenes demod).
    """
    eff_txt = effective if effective else "-"
    parts = [f'<option value=""{" selected" if not selected else ""}>'
            f'(inherited: {html.escape(eff_txt)})</option>']
    for token in DEMOD_TOKENS:
        sel = " selected" if token == selected else ""
        parts.append(f'<option value="{token}"{sel}>{token}</option>')
    return "\n".join(parts)


def _bandwidth_options_inherit_html(selected_hz, effective_hz) -> str:
    """Wie _demod_options_inherit_html, fuer die Bandbreiten-Auswahl."""
    try:
        selected_hz = int(selected_hz)
    except (TypeError, ValueError):
        selected_hz = None
    eff_txt = f"{_format_khz(effective_hz)} kHz" if effective_hz else "-"
    parts = [f'<option value=""{" selected" if selected_hz is None else ""}>'
            f'(inherited: {html.escape(eff_txt)})</option>']
    for hz in BANDWIDTH_OPTIONS_HZ:
        sel = " selected" if hz == selected_hz else ""
        label = f"{_format_khz(hz)} kHz"
        parts.append(f'<option value="{hz}"{sel}>{label}</option>')
    return "\n".join(parts)


def _antenna_options_inherit_html(selected: Optional[str], effective: Optional[str]) -> str:
    """Wie _demod_options_inherit_html, fuer die Antennen-Auswahl."""
    eff_label = dict(ANTENNA_OPTIONS).get(effective, effective) if effective else "-"
    parts = [f'<option value=""{" selected" if not selected else ""}>'
            f'(inherited: {html.escape(eff_label)})</option>']
    for value, label in ANTENNA_OPTIONS:
        sel = " selected" if value == selected else ""
        parts.append(f'<option value="{value}"{sel}>{html.escape(label)}</option>')
    return "\n".join(parts)


def _resolve_known_file(name: str) -> str:
    """
    Prueft einen Katalog-Dateinamen (kein Pfad!) gegen das Namensmuster und
    baut den vollen Pfad im Katalogverzeichnis. Wirft ValueError bei
    ungueltigem Namen - der Aufrufer antwortet dann mit HTTP 400.
    """
    if (not name or not _KNOWN_FILENAME_RE.match(name)
            or os.sep in name or (os.altsep and os.altsep in name) or ".." in name):
        raise ValueError(f"invalid catalog file name {name!r}")
    return os.path.join(DEFAULT_CATALOG_DIR, name)


def _normalize_new_catalog_filename(name: str) -> str:
    """
    Normalisiert den Dateinamen bei der Neuanlage einer Katalogdatei (T-1020):
    ohne Punkt wird .json angehaengt, eine vorhandene .json-Endung (jede
    Schreibweise) wird auf Kleinbuchstaben normalisiert - der Namensteil vor
    dem Punkt bleibt unangetastet (Air.json, DAB_Band3.json sollen so
    geschrieben bleiben duerfen). Jede andere Endung ist ein Fehler.
    Massgeblich ist der Teil nach dem LETZTEN Punkt: "Air.2026" zaehlt als
    Endung "2026", nicht als endungsloser Name.

    Die Zeichenpruefung selbst bleibt unveraendert in _resolve_known_file /
    _KNOWN_FILENAME_RE und laeuft weiterhin auf dem Namen einschliesslich
    Endung - dieser Schritt liegt davor.
    """
    if not name:
        raise ValueError("catalog file name must not be empty")
    if "." not in name:
        return name + ".json"
    stamm, endung = name.rsplit(".", 1)
    if not endung:
        raise ValueError("catalog file name must not end with a dot")
    if endung.lower() != "json":
        raise ValueError(f"catalog file name must end with .json (found: .{endung})")
    return stamm + ".json"


def _eol_of(data: bytes) -> bytes:
    """Dominantes Zeilenende einer Datei (CRLF, wenn die Mehrheit der Zeilen
    so endet) - gleiche Logik wie tools/sync_known_block.py eol_of()."""
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf * 2 > data.count(b"\n") else b"\n"


def _load_known_dict(path: str) -> dict:
    """Laedt eine Katalogdatei als dict. Wirft ValueError mit Zeilen-/
    Spaltenangabe bei kaputtem JSON, OSError wenn die Datei fehlt/unlesbar ist."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON error in line {exc.lineno}, column {exc.colno}: {exc.msg}")
    if not isinstance(doc, dict):
        raise ValueError("top level is not a JSON object")
    return doc


def validate_candidate(file_name: str, content) -> list[str]:
    """
    Prueft einen Kandidateninhalt (dict oder JSON-Text) fuer file_name gegen
    einen Temp-Spiegel des echten Katalogverzeichnisses: alle *.json aus
    DEFAULT_CATALOG_DIR hineinkopieren, die Kandidatendatei darin ersetzen/
    ergaenzen, load_catalog(tmpdir) ausfuehren. Rueckgabe: Fehlerliste (leer
    = Kandidat akzeptiert).

    Abgelehnt wird bei Meldungen, die die Kandidatendatei selbst betreffen
    (fname-Praefix) ODER die vorher (Baseline: load_catalog auf dem echten
    Verzeichnis) nicht vorhanden waren - damit werden auch dateiuebergreifende
    ID-Duplikate erkannt. Vorbestehende Fehler ANDERER Dateien blockieren nicht.
    """
    if isinstance(content, (bytes, str)):
        try:
            candidate = json.loads(content)
        except json.JSONDecodeError as exc:
            return [f"JSON error in line {exc.lineno}, column {exc.colno}: {exc.msg}"]
    else:
        candidate = content
    if not isinstance(candidate, dict):
        return ["top level must be a JSON object"]

    baseline = load_catalog(DEFAULT_CATALOG_DIR)
    baseline_errors = set(baseline.errors)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            vorhandene = os.listdir(DEFAULT_CATALOG_DIR)
        except OSError:
            vorhandene = []
        for name in vorhandene:
            if name.endswith(".json"):
                try:
                    shutil.copy2(os.path.join(DEFAULT_CATALOG_DIR, name),
                                 os.path.join(tmp, name))
                except OSError:
                    pass
        with open(os.path.join(tmp, file_name), "w", encoding="utf-8") as fh:
            json.dump(candidate, fh, ensure_ascii=False)
        probe = load_catalog(tmp)

    prefix = f"{file_name}:"
    neu = [e for e in probe.errors if e not in baseline_errors or e.startswith(prefix)]
    return neu


def _write_known_file(path: str, content: dict) -> None:
    """
    Schreibt eine Katalogdatei per Schreibdisziplin: .bak der vorherigen
    Version (falls die Datei schon existiert, sonst keins - Neuanlage hat
    nichts zu sichern), Zeilenende an die bestehende Datei angepasst
    (Neuanlage: CRLF), atomar (Temp-Datei im selben Verzeichnis + os.replace).
    """
    # T-2004: in einer Datenwurzel ohne known_freq/ legt die erste neue
    # Katalogdatei das Verzeichnis an.
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    eol = b"\r\n"
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            old = fh.read()
        with open(path + ".bak", "wb") as fh:
            fh.write(old)
        eol = _eol_of(old)
    text = json.dumps(content, ensure_ascii=False, indent=2)
    data = text.encode("utf-8").replace(b"\n", eol)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _view_hidden_set() -> set:
    """T-126: ausgeblendete Katalogdateien (catalog_view.json) als Menge.
    Nie fatal - fehlende oder defekte Datei ergibt eine leere Menge."""
    return set(load_view_config().get("hidden_files") or [])


def _write_view_config(hidden_files) -> None:
    """
    Schreibt catalog_view.json atomar (Temp-Datei im selben Verzeichnis +
    os.replace, wie _write_known_file). Bewusst OHNE .bak: die Datei ist
    reiner Anzeigezustand und ueber die Fliesen jederzeit wiederherstellbar.
    Zeilenende CRLF wie bei einer Neuanlage in _write_known_file.
    """
    doc = {"version": 1, "hidden_files": sorted(set(hidden_files))}
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    data = text.encode("utf-8").replace(b"\n", b"\r\n")
    tmp = DEFAULT_VIEW_PATH + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, DEFAULT_VIEW_PATH)


def _find_channel_entry(doc: dict, cid: str) -> Optional[int]:
    """Index des passenden entries-Elements (kind=='channel', id==cid) oder
    None, falls kein solcher Eintrag existiert."""
    entries = doc.get("entries")
    if not isinstance(entries, list):
        return None
    for i, e in enumerate(entries):
        if isinstance(e, dict) and e.get("kind") == "channel" and e.get("id") == cid:
            return i
    return None


def _apply_inherited_form_fields(entry: dict, form: dict) -> None:
    """
    Uebernimmt die vererbbaren Formularfelder (demod/bw_hz/antenna/
    squelch_dbuv/tags/notes) in einen entries-Eintrag: leeres Feld = geerbt
    -> Schluessel entfernen, gesetztes Feld = expliziter Override.
    Gemeinsame Logik fuer build_channel_entry und build_raster_entry;
    wirft ValueError mit formulartauglichem Text.
    """
    # -- vererbbare Auswahlfelder: leer = geerbt -> Schluessel entfernen ---
    demod = form.get("demod", "").strip()
    if demod:
        if demod not in DEMOD_TOKENS:
            raise ValueError(f"unknown demod {demod!r} (allowed: {', '.join(DEMOD_TOKENS)})")
        entry["demod"] = demod
    else:
        entry.pop("demod", None)

    bw_raw = form.get("bw_hz", "").strip()
    if bw_raw:
        try:
            bw_hz = int(bw_raw)
        except ValueError:
            raise ValueError("bw_hz is not a number")
        if bw_hz not in BW_STAGES_HZ:
            raise ValueError(f"bw_hz {bw_hz} is not an EB200 filter step "
                             f"({', '.join(str(b) for b in BW_STAGES_HZ)})")
        entry["bw_hz"] = bw_hz
    else:
        entry.pop("bw_hz", None)

    antenna = form.get("antenna", "").strip()
    if antenna:
        entry["antenna"] = antenna
    else:
        entry.pop("antenna", None)

    # -- vererbbare Textfelder: leer = geerbt -> Schluessel entfernen ------
    squelch_raw = form.get("squelch_dbuv", "").strip()
    if squelch_raw:
        try:
            entry["squelch_dbuv"] = float(squelch_raw)
        except ValueError:
            raise ValueError("squelch_dbuv is not a number")
    else:
        entry.pop("squelch_dbuv", None)

    tags_raw = form.get("tags", "").strip()
    if tags_raw:
        entry["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        entry.pop("tags", None)

    notes = form.get("notes", "").strip()
    if notes:
        entry["notes"] = notes
    else:
        entry.pop("notes", None)


def build_channel_entry(form: dict, existing: Optional[dict] = None,
                         defaults: Optional[dict] = None) -> dict:
    """
    Baut den entries-Eintrag (kind:'channel') aus Formularfeldern, ausgehend
    von einem eventuell vorhandenen bestehenden Eintrag (existing) - dessen
    unbekannte Schluessel bleiben unangetastet erhalten (Schreibdisziplin,
    siehe _write_known_file).

    id/label/freq_hz/enabled sind NICHT vererbt: immer aus dem Formular.
    demod/bw_hz/antenna/squelch_dbuv/tags/notes SIND vererbbar (aus den
    Datei-defaults): ein leeres Formularfeld bedeutet "geerbt" und entfernt
    den Schluessel aus dem Eintrag (Rueckweg zum geerbten Zustand); ein
    gesetztes Feld schreibt einen expliziten Override.

    defaults ist der defaults-Block der Zieldatei (fuer den Wirksamer-Wert-
    Test bei demod/bw_hz, T-1020): None bedeutet "Aufrufer kennt die
    Datei-defaults nicht" und laesst die Pruefung aus (Stand vor T-1020,
    genutzt vom CSV-Import - dort ist die Zieldatei zum Zeitpunkt des
    Zeilenaufbaus nicht bekannt). Ein uebergebenes dict (auch leer) schaltet
    die Pruefung scharf.

    Wirft ValueError mit einem Text, der direkt im Formular angezeigt
    werden kann.
    """
    entry: dict = dict(existing) if existing else {}
    entry["kind"] = "channel"

    cid = form.get("id", "").strip()
    if not cid or not _CHANNEL_ID_RE.match(cid):
        raise ValueError(f"invalid id {cid!r} (allowed: A-Z a-z 0-9 . _ -)")
    entry["id"] = cid

    label = form.get("label", "").strip()
    if not label:
        raise ValueError("label must not be empty")
    entry["label"] = label

    try:
        freq_hz = int(form.get("freq_hz", ""))
    except ValueError:
        raise ValueError("frequency is missing or invalid")
    if not (FREQ_MIN_HZ <= freq_hz <= FREQ_MAX_HZ):
        raise ValueError(f"frequency must be between {FREQ_MIN_HZ} and {FREQ_MAX_HZ} Hz")
    entry["freq_hz"] = freq_hz

    if form.get("enabled") == "on":
        entry.pop("enabled", None)
    else:
        entry["enabled"] = False

    _apply_inherited_form_fields(entry, form)

    if defaults is not None:
        if not entry.get("demod") and not defaults.get("demod"):
            raise ValueError("demodulation is missing - set neither in the entry nor in the file defaults")
        if not entry.get("bw_hz") and not defaults.get("bw_hz"):
            raise ValueError("bandwidth is missing - set neither in the entry nor in the file defaults")

    return entry


def _find_raster_entry(doc: dict, prefix: str) -> Optional[int]:
    """Index des passenden entries-Elements (kind=='raster',
    id_prefix==prefix) oder None, falls kein solcher Eintrag existiert."""
    entries = doc.get("entries")
    if not isinstance(entries, list):
        return None
    for i, e in enumerate(entries):
        if (isinstance(e, dict) and e.get("kind") == "raster"
                and e.get("id_prefix") == prefix):
            return i
    return None


def build_raster_entry(form: dict, existing: Optional[dict] = None,
                        defaults: Optional[dict] = None) -> dict:
    """
    Baut den entries-Eintrag (kind:'raster') aus Formularfeldern, analog zu
    build_channel_entry: unbekannte Schluessel eines bestehenden Eintrags
    bleiben erhalten (Schreibdisziplin), leere vererbbare Felder = geerbt.

    step_scheme steuert die Rasterart: "airband_8k33" ohne step_hz/
    label_template; leer = festes Raster mit step_hz (Pflicht) und
    optionalem label_template (leer = Loader-Default "{mhz:.4f}").

    defaults: siehe build_channel_entry (T-1020) - None laesst die
    Wirksamer-Wert-Pruefung fuer demod/bw_hz aus, ein uebergebenes dict
    (auch leer) schaltet sie scharf. label_template bleibt ohne eigene
    Pruefung: der Loader-Standard "{mhz:.4f}" ist immer ein wirksamer Wert.
    """
    entry: dict = dict(existing) if existing else {}
    entry["kind"] = "raster"

    prefix = form.get("id_prefix", "").strip()
    if not prefix or not _CHANNEL_ID_RE.match(prefix):
        raise ValueError(f"invalid id_prefix {prefix!r} (allowed: A-Z a-z 0-9 . _ -)")
    entry["id_prefix"] = prefix

    try:
        start_hz = int(form.get("start_hz", ""))
        stop_hz = int(form.get("stop_hz", ""))
    except ValueError:
        raise ValueError("start/stop frequency is missing or invalid")
    if not (FREQ_MIN_HZ <= start_hz <= FREQ_MAX_HZ) or not (FREQ_MIN_HZ <= stop_hz <= FREQ_MAX_HZ):
        raise ValueError(f"frequencies must be between {FREQ_MIN_HZ} and {FREQ_MAX_HZ} Hz")
    if stop_hz < start_hz:
        raise ValueError("stop frequency must be greater than start frequency")
    entry["start_hz"] = start_hz
    entry["stop_hz"] = stop_hz

    scheme = form.get("step_scheme", "").strip()
    if scheme == "airband_8k33":
        entry["step_scheme"] = "airband_8k33"
        entry.pop("step_hz", None)
        entry.pop("label_template", None)
    elif scheme == "":
        entry.pop("step_scheme", None)
        try:
            step_hz = int(form.get("step_hz", ""))
        except ValueError:
            raise ValueError("grid spacing is missing or invalid (required for a fixed channel group)")
        if step_hz <= 0:
            raise ValueError("grid spacing must be > 0")
        entry["step_hz"] = step_hz
        template = form.get("label_template", "").strip()
        if template:
            try:
                template.format(mhz=100.0, khz=100000.0, hz=100_000_000, n=0)
            except (KeyError, IndexError, ValueError):
                raise ValueError("invalid label_template (placeholders: {mhz} {khz} {hz} {n})")
            entry["label_template"] = template
        else:
            entry.pop("label_template", None)
    else:
        raise ValueError(f"unknown step_scheme {scheme!r}")

    if form.get("enabled") == "on":
        entry.pop("enabled", None)
    else:
        entry["enabled"] = False

    _apply_inherited_form_fields(entry, form)

    if defaults is not None:
        if not entry.get("demod") and not defaults.get("demod"):
            raise ValueError("demodulation is missing - set neither in the entry nor in the file defaults")
        if not entry.get("bw_hz") and not defaults.get("bw_hz"):
            raise ValueError("bandwidth is missing - set neither in the entry nor in the file defaults")

    return entry


def _raster_preview(file_name: str, doc: dict, prefix: str):
    """
    Vorschau fuer einen Raster-Eintrag: Kandidatendatei ALLEIN in ein
    Temp-Verzeichnis schreiben, load_catalog() darauf ausfuehren und die vom
    Raster erzeugten Kanaele einsammeln. enabled wird fuer die Vorschau auf
    True gesetzt, damit auch abgeschaltete Raster zeigen, was sie erzeugen
    WUERDEN. Dateiuebergreifende ID-Duplikate prueft erst validate_candidate()
    beim Speichern.

    Rueckgabe: (anzahl, beispielzeilen, fehler) - beispielzeilen sind alle
    Kanaele (bis 6) bzw. die ersten und letzten drei mit "..." dazwischen.
    """
    cand = json.loads(json.dumps(doc))
    for e in cand.get("entries", []):
        if (isinstance(e, dict) and e.get("kind") == "raster"
                and e.get("id_prefix") == prefix):
            e["enabled"] = True
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, file_name), "w", encoding="utf-8") as fh:
            json.dump(cand, fh, ensure_ascii=False)
        probe = load_catalog(tmp)
    chans = [c for c in probe.channels
             if c.get("raster") and str(c.get("id", "")).startswith(prefix + "-")]
    fehler = [e for e in probe.errors if "channel group" in e]
    def _zeile(c):
        return f"{c['label']} - {_mhz(c['freq_hz'])} MHz"
    if len(chans) <= 6:
        zeilen = [_zeile(c) for c in chans]
    else:
        zeilen = [_zeile(c) for c in chans[:3]] + ["..."] + [_zeile(c) for c in chans[-3:]]
    return len(chans), zeilen, fehler


# Text-Schluessel des meta-Blocks, die das Datei-Kopf-Formular bedient.
# Alles andere in meta (z. B. eigene Schluessel) bleibt beim Speichern erhalten.
_META_TEXT_FIELDS = ("service", "source", "region", "maintainer")


# T-132b: ein <select> zeigt die Farbe seiner Option nur im aufgeklappten
# Zustand - zugeklappt steht dort bloss das Wort. Deshalb ein
# <input type="color">: das Feld IST der Farbfleck und oeffnet den vollen
# RGB-Waehler. Die benannten Farben stehen als Schnellwahl daneben, damit
# gleiche Dienstfamilien weiterhin mit einem Klick dieselbe Farbe bekommen.
_COLOR_SWATCH_STYLE = ("width:22px;height:22px;padding:0;border:1px solid #888;"
                       "border-radius:3px;cursor:pointer;margin:0 4px 4px 0")


def _color_presets_html() -> str:
    """Die benannten Farben als Knopfreihe. Jeder Knopf traegt seinen Hexwert
    in data-farbe; das Skript unten setzt ihn ins Farbfeld."""
    knoepfe = []
    for name, hexwert in CATALOG_COLORS.items():
        knoepfe.append(f'<button type="button" class="farb-preset" '
                       f'data-farbe="{hexwert}" title="{CATALOG_COLOR_LABELS.get(name, name)}" '
                       f'style="background:{hexwert};{_COLOR_SWATCH_STYLE}"></button>')
    return "".join(knoepfe)


def build_file_head(form: dict, existing: Optional[dict] = None) -> dict:
    """
    Baut/aktualisiert den Datei-Kopf (meta + defaults) aus Formularfeldern -
    fuer Neuanlage (existing=None) und Bearbeitung. Unbekannte Schluessel in
    meta/defaults sowie die Listen bands/entries bleiben unangetastet
    erhalten (Schreibdisziplin, siehe _write_known_file).

    Notiz-Schluessel: der Bestand behaelt seinen (note ODER notes); eine
    neue Datei bekommt note. Der jeweils andere Schluessel wird nie
    angefasst.
    """
    doc: dict = dict(existing) if existing else {
        "meta": {}, "defaults": {}, "bands": [], "entries": [],
    }
    if not isinstance(doc.get("meta"), dict):
        doc["meta"] = {}
    if not isinstance(doc.get("defaults"), dict):
        doc["defaults"] = {}
    if not isinstance(doc.get("bands"), list):
        doc["bands"] = []
    if not isinstance(doc.get("entries"), list):
        doc["entries"] = []

    meta = doc["meta"]
    for key in _META_TEXT_FIELDS:
        wert = form.get(key, "").strip()
        if wert:
            meta[key] = wert
        else:
            meta.pop(key, None)
    if not meta.get("service"):
        raise ValueError("service name must not be empty")

    # T-132: Farbe der Baender dieser Datei. Leeres Feld entfernt den
    # Schluessel - die Darstellung faellt dann auf den Hash ueber den
    # Dienstnamen zurueck.
    # T-132b: das Formular schickt einen Hexwert aus <input type="color">.
    # Der Haken "keine eigene Farbe" gewinnt immer - ein Farbfeld kann keinen
    # leeren Wert liefern, der Verzicht braucht also ein eigenes Bedienelement.
    farbe = form.get("color", "").strip()
    if form.get("color_aus"):
        meta.pop("color", None)
    elif farbe:
        if resolve_color(farbe) is None:
            raise ValueError(f"unusable color {farbe!r} (allowed: "
                             f"{', '.join(CATALOG_COLORS)} or #rrggbb)")
        meta["color"] = farbe
    else:
        meta.pop("color", None)

    notiz_key = "notes" if ("notes" in meta and "note" not in meta) else "note"
    notiz = form.get("meta_note", "").strip()
    if notiz:
        meta[notiz_key] = notiz
    else:
        meta.pop(notiz_key, None)

    _apply_inherited_form_fields(doc["defaults"], form)

    return doc


def delete_known_file(path: str) -> None:
    """
    Loescht eine Katalogdatei ENDGUELTIG - nur erlaubt, wenn sie keine
    Baender und keine Eintraege mehr enthaelt (Messdaten-/Katalogschutz:
    nichts wird stillschweigend verworfen). Entfernt zusaetzlich eine
    vorhandene .bak/.tmp-Reste (OSError dabei ignoriert - die Hauptdatei
    ist bereits weg, die Reste sind best effort).
    """
    doc = _load_known_dict(path)
    bands = doc.get("bands") or []
    entries = doc.get("entries") or []
    if bands or entries:
        raise ValueError(
            f"catalog file is not empty - bands: {len(bands)}, entries: {len(entries)}; "
            f"empty it first, then delete"
        )
    os.remove(path)
    for suffix in (".bak", ".tmp"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def _find_band_entry(doc: dict, bid: str) -> Optional[int]:
    """Index des passenden bands-Elements (id==bid) oder None."""
    bands = doc.get("bands")
    if not isinstance(bands, list):
        return None
    for i, b in enumerate(bands):
        if isinstance(b, dict) and b.get("id") == bid:
            return i
    return None


def build_band_entry(form: dict, existing: Optional[dict] = None) -> dict:
    """
    Baut ein bands-Element aus Formularfeldern. Unbekannte Schluessel eines
    bestehenden Bandes bleiben erhalten (Schreibdisziplin). Regeln wie im
    Loader (_load_bands): id Pflicht, stop_hz STRENG groesser start_hz,
    Segmente vollstaendig innerhalb des Bandes. title leer -> Schluessel
    entfernen (Loader-Default: title = id). Die Segmente kommen als
    JSON-Liste im Feld segments_json (vom Formular-JS beim Absenden
    erzeugt); eine leere Liste entfernt den segments-Schluessel.
    """
    entry: dict = dict(existing) if existing else {}

    bid = form.get("id", "").strip()
    if not bid or not _CHANNEL_ID_RE.match(bid):
        raise ValueError(f"invalid band id {bid!r} (allowed: A-Z a-z 0-9 . _ -)")
    entry["id"] = bid

    title = form.get("title", "").strip()
    if title:
        entry["title"] = title
    else:
        entry.pop("title", None)

    try:
        start_hz = int(form.get("start_hz", ""))
        stop_hz = int(form.get("stop_hz", ""))
    except ValueError:
        raise ValueError("start/stop frequency is missing or invalid")
    if stop_hz <= start_hz:
        raise ValueError("stop_hz must be greater than start_hz")
    entry["start_hz"] = start_hz
    entry["stop_hz"] = stop_hz

    seg_raw = form.get("segments_json", "").strip() or "[]"
    try:
        seg_list = json.loads(seg_raw)
    except json.JSONDecodeError:
        raise ValueError("segment list unreadable (segments_json)")
    if not isinstance(seg_list, list):
        raise ValueError("segment list unreadable (segments_json)")
    segmente = []
    for i, seg in enumerate(seg_list, start=1):
        if not isinstance(seg, dict):
            raise ValueError(f"segment {i}: not an object")
        try:
            s_start = int(seg.get("start_hz"))
            s_stop = int(seg.get("stop_hz"))
        except (TypeError, ValueError):
            raise ValueError(f"segment {i}: start/stop frequency is missing or invalid")
        if s_stop <= s_start:
            raise ValueError(f"segment {i}: stop_hz must be greater than start_hz")
        if s_start < start_hz or s_stop > stop_hz:
            raise ValueError(f"segment {i}: not completely inside the band")
        segmente.append({"title": str(seg.get("title", "")).strip(),
                         "start_hz": s_start, "stop_hz": s_stop})
    if segmente:
        entry["segments"] = segmente
    else:
        entry.pop("segments", None)
    return entry


# ---------------------------------------------------------------------------
# T-1015: Marker-Import (Auto-Markerliste aus dem Wasserfall -> Katalog)
# ---------------------------------------------------------------------------

def parse_marker_table(text: str) -> tuple[list[dict], list[str]]:
    """
    Zerlegt die Auto-Markerliste (Wasserfall-Overlay T-505b: Spalten Zeit/
    MHz/dBuV) in Zeilen. Trennzeichen Tab ODER Semikolon (je Zeile erkannt),
    Dezimalpunkt; Komma gilt als Tausendertrennzeichen und wird entfernt
    (T-1803a), Kopfzeile optional (erkannt: erste Zeile, deren
    zweite Spalte keine Zahl ist - wird stillschweigend uebersprungen).
    Fehlerhafte Datenzeilen werden NICHT verworfen, sondern als Fehlermeldung
    zurueckgegeben; gueltige Zeilen bleiben erhalten.

    Rueckgabe: (rows, errors). rows-Eintraege: time (Rohtext Spalte 1),
    freq_hz (int), level_dbuv (float oder None bei leerer Spalte 3).
    """
    rows: list[dict] = []
    errors: list[str] = []
    normalisiert = text.replace("\r\n", "\n").replace("\r", "\n")
    for i, line in enumerate(normalisiert.split("\n"), start=1):
        if not line.strip():
            continue
        sep = "\t" if "\t" in line else ";"
        parts = [p.strip() for p in line.split(sep)]
        if len(parts) != 3:
            errors.append(f"line {i}: expected 3 columns (Time/MHz/dBuV), found {len(parts)}")
            continue
        zeit_txt, mhz_txt, dbuv_txt = parts
        try:
            mhz = float(mhz_txt.replace(",", ""))
        except ValueError:
            if i == 1:
                continue      # Kopfzeile: zweite Spalte keine Zahl
            errors.append(f"line {i}: MHz {mhz_txt!r} is not a number")
            continue
        freq_hz = round(mhz * 1_000_000)
        level_dbuv = None
        if dbuv_txt:
            try:
                level_dbuv = float(dbuv_txt.replace(",", ""))
            except ValueError:
                errors.append(f"line {i}: dBuV {dbuv_txt!r} is not a number")
                continue
        rows.append({"time": zeit_txt, "freq_hz": freq_hz, "level_dbuv": level_dbuv})
    return rows, errors


def marker_channel_id(freq_hz: int) -> str:
    """MK-<MHz mit 4 Nachkommastellen> - wiederholter Import derselben
    Frequenz ergibt dieselbe id (Peak-Bin-Genauigkeit der Detektion ist
    deutlich groeber als die 4. Nachkommastelle, siehe T-1015-Begruendung
    "kein automatisches Einrasten auf ein Kanalraster")."""
    return f"MK-{_mhz(freq_hz)}"


def marker_channel_label(freq_hz: int) -> str:
    return f"MK {_mhz(freq_hz)} MHz"


def _marker_notes(time_txt: str, level_dbuv: Optional[float]) -> str:
    """Notiztext als Herkunftsnachweis fuer einen importierten Auto-Marker-
    Kanal: Erkennungszeit und Pegel aus der Markerliste."""
    zeit = (time_txt or "").strip()
    pegel_txt = f", {level_dbuv:.1f} dBµV" if level_dbuv is not None else ""
    if zeit:
        return f"Auto marker, detected at {zeit}{pegel_txt}"
    return f"Auto marker{pegel_txt}" if pegel_txt else "Auto marker"


def build_marker_channel_entry(freq_hz: int, time_txt: str,
                                level_dbuv: Optional[float]) -> dict:
    """
    Baut den entries-Eintrag (kind:'channel') fuer einen importierten Auto-
    Marker: id/label aus der Frequenz (marker_channel_id/-label), tags=
    ['marker'], Erkennungszeit/Pegel als Herkunftsnachweis in notes. demod/
    bw_hz/squelch_dbuv werden bewusst NICHT gesetzt - die Kanaele erben die
    im selben Zug geschriebenen Datei-defaults (siehe
    _handle_known_marker_import_save).
    """
    return {
        "kind": "channel",
        "id": marker_channel_id(freq_hz),
        "label": marker_channel_label(freq_hz),
        "freq_hz": freq_hz,
        "tags": ["marker"],
        "notes": _marker_notes(time_txt, level_dbuv),
    }


def marker_duplicate_of(cat: Catalog, freq_hz: int, bw_hz: int) -> Optional[dict]:
    """Sucht einen bestehenden Kanal im Katalog (ALLE Dateien) innerhalb
    +-bw_hz/2 um freq_hz - Duplikatpruefung fuer den Marker-Import. bw_hz<=0
    (Bandbreite im Formular noch nicht gewaehlt) prueft nur exakte Treffer."""
    tol = max(int(bw_hz), 0) // 2
    treffer = cat.channels_in_range(freq_hz - tol, freq_hz + tol)
    return treffer[0] if treffer else None


def _marker_prefill_defaults(cat: Catalog, freq_hz: int) -> dict:
    """
    Vorbelegung fuer Demod/Bandbreite/Squelch im Marker-Import-Formular: aus
    den defaults der Datei des ERSTEN ueber Catalog.bands_in_range bei
    freq_hz gefundenen Bandes (Baender selbst tragen kein demod/bw_hz/
    squelch_dbuv, siehe eb200_core/known_freq._load_bands) - reiner
    Vorschlag, das Formular laesst jeden Wert aendern. Ohne Treffer oder bei
    unlesbarer Datei bleiben die Felder leer.
    """
    for band in cat.bands_in_range(freq_hz, freq_hz):
        try:
            doc = _load_known_dict(_resolve_known_file(band["file"]))
        except (OSError, ValueError):
            continue
        defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}
        if defaults.get("demod") or "bw_hz" in defaults or "squelch_dbuv" in defaults:
            return {
                "demod": defaults.get("demod", "") or "",
                "bw_hz": str(defaults["bw_hz"]) if "bw_hz" in defaults else "",
                "squelch_dbuv": str(defaults["squelch_dbuv"]) if "squelch_dbuv" in defaults else "",
            }
    return {"demod": "", "bw_hz": "", "squelch_dbuv": ""}


def _marker_default_filename() -> str:
    return f"marker_{date.today().isoformat()}.json"


def _as_bw_int(bw_raw: str) -> int:
    try:
        return int(bw_raw) if bw_raw else 0
    except ValueError:
        return 0


def _marker_rows_from_form(form: dict, row_count: int) -> list[dict]:
    """Baut die Vorschauzeilen aus dem Speichern-POST zurueck (Vorschau
    stellt row_count sowie time_i/freq_i/level_i/sel_i je Zeile bereit) -
    die editierte Frequenz je Zeile ist damit massgeblich fuers Speichern,
    nicht die urspruengliche Rohtabelle. Eine Zeile mit unlesbarer Frequenz
    (vom Nutzer geleert/verstellt) faellt aus der Liste. sel_i = Auswahl fuer
    "Add to job", neu_i = "create anyway" (nur bei
    Duplikattreffern gerendert, Standard aus)."""
    rows = []
    for i in range(row_count):
        freq_txt = form.get(f"freq_{i}", "").strip()
        try:
            freq_hz = round(float(freq_txt.replace(",", "")) * 1_000_000)
        except ValueError:
            continue
        level_txt = form.get(f"level_{i}", "").strip()
        level_dbuv = None
        if level_txt:
            try:
                level_dbuv = float(level_txt)
            except ValueError:
                level_dbuv = None
        rows.append({
            "idx": i,
            "time": form.get(f"time_{i}", ""),
            "freq_hz": freq_hz,
            "level_dbuv": level_dbuv,
            "checked": form.get(f"sel_{i}") == "on",
            "neu": form.get(f"neu_{i}") == "on",
        })
    return rows


def _marker_annotate_dup(cat: Catalog, rows: list[dict], bw_hz: int) -> None:
    for row in rows:
        row["dup"] = marker_duplicate_of(cat, row["freq_hz"], bw_hz)


def _format_khz(hz) -> str:
    """
    Formatiert einen Hz-Wert als kHz-Zeichenkette. Komma als Tausender-, Punkt
    als Dezimaltrennzeichen (T-1803a), mit bis zu 3 Nachkommastellen
    (kHz-Auflösung bis auf 1 Hz genau) und ohne überflüssige Nullen. Wird
    sowohl für die Frequenzspalten in der Liste als auch für die
    Bandbreiten-Labels im Formular verwendet.
    """
    khz = hz / 1000
    s = f"{khz:,.3f}"          # Komma=Tausender, Punkt=Dezimal (Endformat, T-1803a)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# ---------------------------------------------------------------------------
# T-121/T-122: CSV-Export und -Import - Ausgabe, Zerlegung, Multipart
# ---------------------------------------------------------------------------

# Semikolon statt Komma: Excel schreibt und erwartet auf einem deutschen
# Windows Semikolon, und Labels/Notizen enthalten haeufig Kommata.
CSV_DELIM = ";"

CSV_COLUMNS = ("typ", "id", "label", "freq_hz", "start_hz", "stop_hz", "title",
               "demod", "bw_hz", "squelch_dbuv", "antenna", "tags", "notes",
               "enabled")

# Schluessel eines Eintrags, die die CSV abbildet. Alles andere (site,
# programm, bundesland, pol, erp_w in den ORS-Listen; segments eines Bandes;
# eigene Schluessel im JSON) hat keine Spalte und ginge beim Ersetzen
# verloren - der Import uebernimmt solche Schluessel aus dem bestehenden
# Eintrag (_csv_merge_bestand), der Export nennt sie im Kommentarkopf.
_CSV_CHANNEL_KEYS = ("kind", "id", "label", "freq_hz", "enabled", "demod",
                     "bw_hz", "squelch_dbuv", "antenna", "tags", "notes")
_CSV_BAND_KEYS = ("id", "title", "start_hz", "stop_hz")

# Obergrenze des Uploads. Eine Katalogdatei mit einigen tausend Kanaelen liegt
# im niedrigen einstelligen MB-Bereich; alles darueber ist ein Bedienfehler
# und wird abgewiesen, bevor der Rumpf ueberhaupt gelesen wird.
CSV_UPLOAD_MAX_BYTES = 4 * 1024 * 1024

def _csv_wert(wert) -> str:
    """
    Ein JSON-Wert als CSV-Zelle. Ganze Zahlen bleiben ganzzahlig (eine
    Hz-Spalte darf keine Exponentialschreibweise bekommen), ein float ohne
    Nachkommateil verliert sein ".0" (sonst stuende in bw_hz "9000.0", was
    der Import als "is not an integer" zurueckwiese), None wird leer.
    """
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return "1" if wert else "0"
    if isinstance(wert, float) and wert.is_integer():
        return str(int(wert))
    return str(wert)


def _csv_merge_bestand(neu: dict, alt: dict, csv_keys) -> dict:
    """
    Beim Ersetzen eines bestehenden Eintrags dessen Schluessel OHNE CSV-Spalte
    uebernehmen (Schreibdisziplin, wie build_channel_entry sie mit `existing`
    fuehrt). Schluessel MIT Spalte bleiben unangetastet - dort heisst ein
    leeres Feld bewusst "geerbt" und muss einen zuvor gesetzten Wert
    entfernen duerfen. Ohne diese Stufe verloere ein Re-Import der ORS-Listen
    Sender-Standort, Programm und Sendeleistung, ohne dass irgendetwas
    danach noch anzeigen wuerde, dass sie je da waren.
    """
    for schluessel, wert in alt.items():
        if schluessel not in csv_keys and schluessel not in neu:
            neu[schluessel] = wert
    return neu


def catalog_csv_text(doc: dict) -> str:
    """
    Eine Katalogdatei als CSV (T-122). Aufbau: Kopfzeile, Hinweiszeilen, ein
    Kommentarblock zu Herkunft und Datei-Kopf, danach je eine Zeile pro Band
    und pro Kanal - in der Reihenfolge der Datei.

    Ausgegeben wird, was in der Datei STEHT, nicht der aufgeloeste Katalog:
    ein leeres Feld bedeutet weiterhin "geerbt aus den defaults". Damit ist
    der Export die Umkehrung des Imports; wer die wirksamen Werte sehen will,
    hat dafuer die Tabelle im Bearbeitungsblatt.

    Raster bildet die CSV nicht ab - ein Raster ist eine
    Erzeugungsvorschrift, keine Zeile, und expandiert brachte es aus Air.json
    2280 Einzelkanaele mit. Ein Raster wird beim Export uebergangen und beim
    Re-Import nicht angeruehrt, solange in dieselbe Datei geschrieben wird;
    dass eine Datei mehr enthaelt als ihre CSV-Zeilen, sagt seit T-123 das
    Export-Formular (_csv_export_form_body) statt einer Kommentarzeile.

    Seit T-123 enthaelt die Ausgabe ueberhaupt keine Kommentarzeilen mehr -
    nur Kopfzeile und Daten, damit die Datei in der Tabellenkalkulation ohne
    Vorspann beginnt. Der Import ueberspringt "#"-Zeilen trotzdem weiter,
    fremde CSV-Dateien duerfen also welche mitbringen.

    CRLF wie im uebrigen Windows-Repo; die BOM setzt erst der ausliefernde
    Handler (ohne sie oeffnet Excel die Datei als cp1252 und zerlegt Umlaute).
    """
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=CSV_DELIM, lineterminator="\r\n")
    puffer.write(CSV_DELIM.join(CSV_COLUMNS) + "\r\n")

    for band in doc.get("bands") or []:
        if not isinstance(band, dict):
            continue
        schreiber.writerow(["band", str(band.get("id", "")), "", "",
                            _csv_wert(band.get("start_hz")),
                            _csv_wert(band.get("stop_hz")),
                            str(band.get("title", "")),
                            "", "", "", "", "", "", ""])

    for eintrag in doc.get("entries") or []:
        if not isinstance(eintrag, dict) or eintrag.get("kind") != "channel":
            continue        # Raster und Unbekanntes haben keine CSV-Zeile
        schreiber.writerow(["channel", str(eintrag.get("id", "")),
                            str(eintrag.get("label", "")),
                            _csv_wert(eintrag.get("freq_hz")), "", "", "",
                            str(eintrag.get("demod", "")),
                            _csv_wert(eintrag.get("bw_hz")),
                            _csv_wert(eintrag.get("squelch_dbuv")),
                            str(eintrag.get("antenna", "")),
                            ",".join(eintrag.get("tags") or []),
                            str(eintrag.get("notes", "")),
                            "0" if eintrag.get("enabled") is False else "1"])

    return puffer.getvalue()


def decode_csv_bytes(data: bytes) -> str:
    """
    CSV-Rohbytes zu Text: erst UTF-8 mit oder ohne BOM ("CSV UTF-8" aus
    Excel), dann cp1252 ("CSV (Trennzeichen-getrennt)" auf deutschem
    Windows). Streng dekodiert mit Rueckfall statt errors='replace' - ein
    Ersatzzeichen in einem Label waere ein stiller Datenfehler im Katalog.
    """
    for codec in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV file is readable neither as UTF-8 nor as cp1252 - "
                     "save it in Excel as 'CSV UTF-8'")


def _csv_hz(row: dict, key: str) -> str:
    """
    Hz-Feld einer CSV-Zeile als Zeichenkette fuer die build_*-Funktionen (die
    nehmen Formularwerte, also Text). Leerzeichen werden entfernt, sonst sind
    nur Ziffern erlaubt: Punkt und Komma waeren nicht unterscheidbar als
    Dezimal- oder Tausendertrennzeichen ("118.5" = 118,5 Hz oder 1185 Hz?).
    Leeres Feld bleibt leer - die aufrufende build_*-Funktion entscheidet,
    ob das ein Fehler ist oder Vererbung bedeutet.
    """
    roh = row.get(key, "")
    wert = roh.replace(" ", "")
    if not wert:
        return ""
    if not wert.isdigit():
        raise ValueError(f"{key} {roh!r} is not an integer in Hz "
                         f"(digits only, no separators)")
    return wert


def _csv_channel_entry(row: dict) -> dict:
    """Eine typ=channel-Zeile zu einem entries-Eintrag - die eigentliche
    Pruefung macht build_channel_entry(), hier nur die CSV-eigenen Regeln
    (Bandspalten leer, enabled 1/0)."""
    for leer in ("start_hz", "stop_hz", "title"):
        if row.get(leer, ""):
            raise ValueError(f"channel row: column {leer} must be empty")
    enabled = row.get("enabled", "")
    if enabled not in ("", "0", "1"):
        raise ValueError(f"invalid enabled {enabled!r} (allowed: 1, 0 or empty)")
    form = {
        "id": row.get("id", ""),
        "label": row.get("label", ""),
        "freq_hz": _csv_hz(row, "freq_hz"),
        "demod": row.get("demod", "").upper(),
        "bw_hz": _csv_hz(row, "bw_hz"),
        "squelch_dbuv": row.get("squelch_dbuv", "").replace(",", ""),
        "antenna": row.get("antenna", ""),
        "tags": row.get("tags", ""),
        "notes": row.get("notes", ""),
        "enabled": "" if enabled == "0" else "on",
    }
    return build_channel_entry(form)


def _csv_band_entry(row: dict) -> dict:
    """Eine typ=band-Zeile zu einem bands-Element (build_band_entry prueft
    id/Bereich). Empfangsparameter sind auf Bandebene sinnlos (der Loader
    liest sie dort nicht, siehe known_freq._load_bands) - statt sie still zu
    verwerfen, ist eine gefuellte Spalte ein Fehler."""
    for leer in ("label", "freq_hz", "demod", "bw_hz", "squelch_dbuv",
                 "antenna", "tags", "notes", "enabled"):
        if row.get(leer, ""):
            raise ValueError(f"band row: column {leer} must be empty "
                             f"(bands carry no reception parameters)")
    form = {
        "id": row.get("id", ""),
        "title": row.get("title", ""),
        "start_hz": _csv_hz(row, "start_hz"),
        "stop_hz": _csv_hz(row, "stop_hz"),
        "segments_json": "",
    }
    return build_band_entry(form)


def _csv_delimiter_of(text: str) -> str:
    """
    Trennzeichen einer Import-CSV aus ihrer Kopfzeile bestimmen (T-123).

    Der Export schreibt Semikolon, aber Excel und LibreOffice speichern eine
    geoeffnete CSV je nach Version und Spracheinstellung mit einem anderen
    Zeichen zurueck, als sie sie gelesen haben. Ohne Erkennung las der Parser
    die ganze Kopfzeile als EINE Spalte und meldete "Spalte 'typ' fehlt" -
    fuer den Anwender unverstaendlich, weil die Spalte ja sichtbar da ist.

    Gewaehlt wird das Zeichen, mit dem die Kopfzeile die meisten bekannten
    Spaltennamen ergibt; bei Gleichstand bleibt es beim Semikolon des
    Exports. Anfuehrungszeichen werden dabei abgestreift, damit auch eine
    durchgaengig gequotete Kopfzeile erkannt wird.
    """
    kopf = ""
    for zeile in text.splitlines():
        if zeile.strip() and not zeile.lstrip().startswith("#"):
            kopf = zeile
            break
    if not kopf:
        return CSV_DELIM
    bestes = CSV_DELIM
    treffer = -1
    for kandidat in (CSV_DELIM, ",", "\t"):
        spalten = [z.strip().strip('"').lower() for z in kopf.split(kandidat)]
        anzahl = sum(1 for s in spalten if s in CSV_COLUMNS)
        if anzahl > treffer:
            bestes, treffer = kandidat, anzahl
    return bestes


def parse_catalog_csv(text: str):
    """
    Zerlegt den Inhalt einer Import-CSV in fertige entries-/bands-Elemente.

    Rueckgabe (entries, bands, fehler). Ist die Fehlerliste nicht leer, wird
    NICHTS geschrieben - der Import ist alles-oder-nichts, eine halb
    uebernommene Senderliste waere im Katalog nicht wiederzuerkennen.

    Die erste nicht leere Nicht-Kommentarzeile ist die Kopfzeile und legt die
    Spaltenreihenfolge fest (Reihenfolge also frei, Schreibweise egal).
    Spalten ausserhalb von CSV_COLUMNS sind ein Fehler, keine stille
    Auslassung. Doppelte ids INNERHALB der CSV werden hier erkannt; gegen den
    bestehenden Katalog prueft erst validate_candidate().
    """
    entries: list[dict] = []
    bands: list[dict] = []
    fehler: list[str] = []
    kopf = None
    gesehen: set[str] = set()

    for nr, roh in enumerate(csv.reader(io.StringIO(text),
                                        delimiter=_csv_delimiter_of(text)),
                             start=1):
        if not roh or all(not z.strip() for z in roh):
            continue
        if roh[0].lstrip().startswith("#"):
            continue
        felder = [z.strip() for z in roh]

        if kopf is None:
            kopf = [z.lower() for z in felder]
            unbekannt = [s for s in kopf if s and s not in CSV_COLUMNS]
            if unbekannt:
                fehler.append(f"line {nr}: unknown columns: "
                              f"{', '.join(unbekannt)}")
            for pflicht in ("typ", "id"):
                if pflicht not in kopf:
                    fehler.append(f"line {nr}: column '{pflicht}' is missing "
                                  f"from the header row")
            continue

        row = {name: (felder[i] if i < len(felder) else "")
               for i, name in enumerate(kopf) if name}
        typ = row.get("typ", "").lower()
        cid = row.get("id", "")
        if cid and cid in gesehen:
            fehler.append(f"line {nr}: id '{cid}' occurs more than once in the CSV")
            continue
        # Die id wird VOR dem Bau-Versuch gemerkt: welche ids die CSV enthaelt,
        # haengt nicht davon ab, ob die Zeile sonst gueltig ist. Erst danach zu
        # merken wuerde eine Wiederholung durchlassen, sobald die erste Zeile
        # derselben id an einem anderen Fehler scheitert - und das Duplikat
        # erst im naechsten Anlauf melden, statt mit allen uebrigen Fehlern
        # zusammen.
        if cid:
            gesehen.add(cid)
        try:
            if typ == "channel":
                entries.append(_csv_channel_entry(row))
            elif typ == "band":
                bands.append(_csv_band_entry(row))
            else:
                raise ValueError(f"unknown typ {typ!r} (allowed: channel, band)")
        except ValueError as exc:
            fehler.append(f"line {nr}: {exc}")
            continue

    if kopf is None:
        fehler.append("no header row found - use the CSV template")
    elif not entries and not bands and not fehler:
        fehler.append("no data rows found")
    return entries, bands, fehler


def parse_multipart_form(body: bytes, content_type: str):
    """
    Zerlegt einen multipart/form-data-Rumpf - stdlib-only, weil das
    cgi-Modul seit Python 3.13 entfernt ist und die Suite kein Zusatzpaket
    kennt. Rueckgabe (felder, dateien): felder sind UTF-8-dekodierte
    Textfelder, dateien je {"filename", "data"} mit den Rohbytes.

    Bewusst NICHT unterstuetzt, weil das Importformular es nicht braucht:
    mehrere Teile gleichen Namens (der letzte gewinnt), verschachtelte
    multipart-Teile, Content-Transfer-Encoding ausser 8bit/binary.
    """
    felder: dict = {}
    dateien: dict = {}

    typ, _semi, rest = content_type.partition(";")
    if typ.strip().lower() != "multipart/form-data":
        raise ValueError("form was not sent as multipart/form-data")
    grenze = ""
    for teil in rest.split(";"):
        name, _gleich, wert = teil.strip().partition("=")
        if name.strip().lower() == "boundary":
            grenze = wert.strip().strip('"')
    if not grenze:
        raise ValueError("boundary is missing from the form's Content-Type")

    trenner = b"--" + grenze.encode("ascii", "replace")
    for stueck in body.split(trenner):
        # Den Rahmen GEZIELT abtragen, nie per strip(): ein Teilstueck sieht
        # aus wie b"\r\n<Kopf>\r\n\r\n<Daten>\r\n". Das \r\n vor dem naechsten
        # Trenner gehoert zur multipart-Syntax, ein unmittelbar davor
        # stehendes \r\n aber schon zur hochgeladenen Datei. strip(b"\r\n")
        # traegt beide ab und nimmt jeder normal gespeicherten CSV das
        # Zeilenende ihrer letzten Datenzeile.
        if stueck.startswith(b"\r\n"):
            stueck = stueck[2:]
        if not stueck or stueck.startswith(b"--"):
            continue        # Praeambel bzw. Abschlusstrenner "--\r\n"
        kopf_roh, marke, daten = stueck.partition(b"\r\n\r\n")
        if not marke:
            continue
        if daten.endswith(b"\r\n"):
            daten = daten[:-2]
        name = ""
        dateiname = None
        for kopfzeile in kopf_roh.decode("utf-8", "replace").split("\r\n"):
            if not kopfzeile.lower().startswith("content-disposition:"):
                continue
            for teil in kopfzeile.split(";")[1:]:
                schluessel, _gleich, wert = teil.strip().partition("=")
                wert = wert.strip().strip('"')
                if schluessel.lower() == "name":
                    name = wert
                elif schluessel.lower() == "filename":
                    dateiname = wert
        if not name:
            continue
        if dateiname is None:
            felder[name] = daten.decode("utf-8", "replace")
        else:
            dateien[name] = {"filename": dateiname, "data": daten}
    return felder, dateien


def _known_file_names() -> list[str]:
    """Sortierte Liste der Katalogdateien - fehlendes Verzeichnis ergibt eine
    leere Liste (nie fatal, wie load_catalog)."""
    try:
        return sorted(n for n in os.listdir(DEFAULT_CATALOG_DIR)
                      if n.endswith(".json"))
    except OSError:
        return []


# ---------------------------------------------------------------------------
# HTML-Bausteine (gemeinsames Aussehen mit waterfall.py: dunkles Theme)
# ---------------------------------------------------------------------------

PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; Frequency catalog</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  /* T-1405: Farben nur noch als Tokens (eb200_core/ui_theme.py); Kopfleiste,
     Knoepfe, Felder, Tabellen, Reiter liefert BASE_CSS. Diese Seite ist
     servergerendert - jede Unterseite setzt ihre eigene Ueberschrift unter die
     gemeinsame Kopfleiste (h1 bleibt daher als Seitentitel erhalten). */
  h1 { font-size:1.15em; font-weight:600; color:var(--fg); margin:14px 0 10px; }
  table { width:100%; margin-top:12px; }
  th, td { border:1px solid var(--border); padding:6px 10px; }
  th { background:var(--panel-2); }
  tr:hover td { background:var(--panel-2); }
  .actions a { margin-right:10px; }
  .comment-cell { max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  td.empty-row { text-align:center; color:var(--fg-muted); }
  input[type=submit] {
    font:inherit; font-size:0.92em; background:var(--btn-bg); color:var(--btn-fg);
    border:1px solid var(--btn-border); border-radius:4px; padding:5px 12px; cursor:pointer;
  }
  input[type=submit]:hover { background:var(--btn-hover); }
  form.inline { display:inline; }
  .new-button { margin:12px 0; display:inline-block; }
  .form-grid { display:grid; grid-template-columns: 220px 260px; gap:10px 14px;
               max-width:520px; margin-top:16px; align-items:start; }
  .form-grid label { font-size:0.9em; color:var(--fg-muted); padding-top:6px; }
  input[type=text], input[type=number], select, textarea { width:100%; }
  textarea { resize:vertical; min-height:60px; font-family:inherit; }
  .field-hint { font-size:0.75em; color:var(--fg-muted); margin-top:2px; }
  .error { color:var(--danger); margin:10px 0; }
  /* T-124: Fehlerbericht des CSV-Imports - ein ruhiger Kasten statt einer
     Kette roter Meldungen. Der Balken links zeigt, dass etwas nicht
     durchgelaufen ist; der Text bleibt in normaler Schriftfarbe und damit
     lesbar, auch wenn er laenger wird. */
  .bericht { border:1px solid var(--border); border-left:4px solid var(--danger);
             border-radius:6px; background:var(--panel); padding:10px 14px;
             margin:12px 0; max-width:52em; }
  .bericht-kopf { font-weight:600; margin:0 0 8px; }
  .bericht p { margin:6px 0; }
  .bericht ul { margin:6px 0 0 18px; }
  .bericht table.bericht-tabelle { max-width:36em; }
  .hint { margin-top:20px; max-width:520px; }
  .known-file-section { border:1px solid var(--border); border-radius:6px;
                        background:var(--panel); padding:10px 14px; margin-top:16px; }
  .known-file-section summary { font-size:1em; font-weight:600; color:var(--fg); cursor:pointer; padding:2px 0; }
  .known-file-section summary:hover { color:var(--accent); }
  .known-file-section .sec-meta { color:var(--fg-muted); font-size:0.85em; margin-left:10px; font-weight:400; }
  .known-file-section .sec-err { color:var(--danger); margin-left:10px; }
  table.known-table { table-layout:fixed; }
  table.marker-table { max-width:60em; }
  table.marker-table td { vertical-align:top; }
  table.marker-table th.num, table.marker-table td.num { text-align:right; padding-right:0.8em; }
  .dup-file { color:var(--accent); }
  table.marker-table input[type="text"] { width:100%; box-sizing:border-box; }
  table.known-table td { overflow-wrap:anywhere; }
  table.known-table th.sortable { cursor:pointer; user-select:none; }
  table.known-table th.sortable[data-dir="asc"]::after { content:" ▲"; color:var(--accent); }
  table.known-table th.sortable[data-dir="desc"]::after { content:" ▼"; color:var(--accent); }
  /* T-125: Katalogtabellen kompakter, Kopfzeile als Block. Die beiden
     Tabellen im Reiter "Edit" liefen mit width:100% ueber die volle
     Blattbreite (das Sheet ist inset:4% 6%) - auf einem breiten Schirm
     standen die Spalten dadurch weit auseinander. Deckel in em, damit die
     Grenze an der Schriftgroesse haengt und nicht an Pixeln. Die Kopfzeile
     wird leicht blau hinterlegt (--accent-soft ist in beiden Themes
     definiert, im dunklen als dunkles Blau), groesser und fett.
     Ausgenommen ist marker-table (Dublettenpflege): sie traegt
     Eingabefelder und behaelt ihre 60em und die weiten Zellen. */
  table.known-table:not(.marker-table) { max-width:88em; margin-top:8px; }
  table.known-table:not(.marker-table) th,
  table.known-table:not(.marker-table) td { padding:3px 8px; line-height:1.25; }
  table.known-table:not(.marker-table) th {
    background:var(--accent-soft); color:var(--fg); font-size:1.1em; font-weight:700;
    border-bottom:2px solid var(--accent); padding-top:5px; padding-bottom:5px; }
  .tick { font-weight:bold; }
  .tick.on { color:var(--success); }
  .tick.off { color:var(--fg-faint); }
  .seg-row { display:flex; gap:8px; margin:4px 0; }
  .seg-row .seg-title { width:220px; }
  .seg-row .seg-start, .seg-row .seg-stop { width:120px; }
  /* T-110: Katalog-Uebersicht als Fliesen + Bearbeitungsblatt (Overlay) */
  #tileGrid { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));
              gap:12px; margin-top:14px; }
  .tile { border:1px solid var(--btn-border); border-radius:6px; padding:12px 14px; cursor:pointer;
          background:var(--panel); }
  .tile:hover { border-color:var(--accent); background:var(--panel-2); }
  /* T-126: Ansichtsauswahl. Bewusst nicht allein ueber Farbe erkennbar -
     ausgeblendete Fliesen sind zusaetzlich gestrichelt und abgeblendet.
     Nur Tokens (eb200_core/ui_theme.py), damit beide Themes stimmen. */
  .tile.view-on { border-color:var(--accent); box-shadow:inset 4px 0 0 var(--accent); }
  .tile.view-off { opacity:0.5; border-style:dashed; }
  .tile.view-off .t-name { color:var(--fg-muted); }
  .tile .t-view { display:inline-flex; align-items:center; gap:5px; cursor:pointer;
                  font-size:0.78em; color:var(--fg-muted); border:1px solid var(--border-strong);
                  border-radius:10px; padding:2px 10px; background:var(--panel-2); }
  .tile.view-on .t-view { color:var(--accent); border-color:var(--accent);
                          background:var(--accent-soft); }
  .tile .t-view input { margin:0; }
  #viewCount { color:var(--fg-muted); font-size:0.85em; margin-left:10px; }
  .tile .t-name { color:var(--accent); font-size:1.15em; font-weight:bold; }
  .tile .t-service { color:var(--fg-muted); font-size:0.85em; margin-top:4px;
                     white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .tile .t-pills { margin-top:10px; display:flex; gap:8px; flex-wrap:wrap; }
  .pill { border:1px solid var(--border-strong); border-radius:10px; padding:2px 10px;
          font-size:0.78em; color:var(--fg); background:var(--panel-2); }
  .pill-err { border-color:var(--danger); color:var(--danger); background:var(--danger-soft); }
  #sheetBackdrop { position:fixed; inset:0; background:rgba(0,0,0,0.45); z-index:40; }
  #sheetBackdrop[hidden] { display:none; }
  .sheet { position:fixed; inset:4% 6%; overflow:auto; background:var(--bg);
           border:1px solid var(--border-strong); border-radius:6px; padding:14px 18px; z-index:50;
           box-shadow:var(--shadow); }
  .sheet[hidden] { display:none; }
  .sheet-head { display:flex; align-items:baseline; gap:14px; }
  .sheet-head .s-name { color:var(--fg); font-size:1.1em; font-weight:600; }
  .sheet-head .s-close { margin-left:auto; }
  .tabbar .tab { text-decoration:none; display:inline-block; cursor:pointer; }
  .tabbar .tab:hover { text-decoration:none; }
  /* T-122: Der Reiter "Edit entry" ist im Markup mit hidden angelegt
     und wird von KNOWN_SHEET_JS erst eingeblendet, wenn ein Formular in seine
     Pane geladen wurde. Die display-Regel eine Zeile darueber ist aber eine
     Autorenregel und schlaegt damit das display:none, mit dem das
     Browser-Stylesheet das hidden-Attribut umsetzt - der leere Reiter war
     deshalb immer sichtbar. */
  .tabbar .tab[hidden] { display:none; }
  .sheet-pane { display:none; margin-top:10px; }
  .sheet-pane.active { display:block; }
</style>
</head>
<body>
/*HEADER*/
"""

# T-1405: Theme-Bausteine einsetzen. PAGE_HEAD wird an sieben Stellen mit
# f-Strings zu einer Seite zusammengesetzt (Uebersicht, Kanal-/Raster-/Band-/
# Datei-Kopf-Formular, Datei-Editor, Fehlerseite Loeschen) - die Kopfleiste
# steckt deshalb hier EINMAL drin, die seitenspezifische <h1> bleibt darunter.
PAGE_HEAD = (PAGE_HEAD.replace("/*THEME_CSS*/", THEME_CSS + BASE_CSS)
                      .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                      .replace("/*HEADER*/", header_html("Frequency catalog")))

# T-132d: Skript der Farbwahl im Datei-Kopf. Es steht EINMAL je Seite und
# arbeitet per Delegation, weil die Uebersicht je Katalogdatei ein eigenes
# Bearbeitungsblatt rendert und alle gleichzeitig im DOM liegen (siehe
# KNOWN_SHEET_JS). In T-132b lag es im Formular, also 25-mal, und suchte seine
# Felder ueber deren id - was stets das ERSTE Vorkommen der Seite traf, jeder
# Klick faerbte also ALE_UDXF.json. Die Felder tragen deshalb keine id mehr,
# sondern werden ueber ihren name im umschliessenden Container gesucht.
# Eigene Konstante ohne f-String, damit tools/check_js_blocks.py sie pruefen
# kann; im Formular waere sie Teil eines f-Strings und bliebe ungeprueft.
FARB_WAHL_JS = """<script>
(function () {
  function teilVon(el, name) {
    var box = el.closest('.farb-wahl');
    return box ? box.querySelector('input[name="' + name + '"]') : null;
  }
  document.addEventListener('click', function (ev) {
    var knopf = ev.target.closest ? ev.target.closest('.farb-preset') : null;
    if (!knopf) return;
    var feld = teilVon(knopf, 'color');
    if (!feld) return;
    feld.value = knopf.dataset.farbe;
    var haken = teilVon(knopf, 'color_aus');
    if (haken) haken.checked = false;
  });
  document.addEventListener('input', function (ev) {
    if (!ev.target.matches || !ev.target.matches('.farb-wahl input[name="color"]')) return;
    var haken = teilVon(ev.target, 'color_aus');
    if (haken) haken.checked = false;
  });
})();
</script>"""

PAGE_TAIL = FARB_WAHL_JS + "</body></html>"

# Sortier-Logik fuer die Tabellen der Known-Frequencies-Liste. Eigene
# Konstante (kein f-String), damit die JS-Klammern nicht mit den f-Strings
# der Render-Funktionen kollidieren. Header-Zeile ist rows[0] und bleibt
# beim Umsortieren stehen; data-sort-Attribute (Hz-Wert, aktiv-Flag)
# haben Vorrang vor dem Zelltext, sonst natuerliche Textsortierung.
KNOWN_SORT_JS = """<script>
document.querySelectorAll("table.known-table").forEach(function (tab) {
  var body = tab.tBodies[0] || tab;
  var head = tab.rows[0];
  if (!head) return;
  Array.prototype.forEach.call(head.cells, function (th, idx) {
    if (!th.classList.contains("sortable")) return;
    th.addEventListener("click", function () {
      var asc = th.dataset.dir !== "asc";
      Array.prototype.forEach.call(head.cells, function (h) { delete h.dataset.dir; });
      th.dataset.dir = asc ? "asc" : "desc";
      var rows = Array.prototype.slice.call(tab.rows, 1)
        .filter(function (r) { return r.cells.length > 1; });
      rows.sort(function (a, b) {
        var ka = a.cells[idx].dataset.sort !== undefined
          ? a.cells[idx].dataset.sort : a.cells[idx].textContent;
        var kb = b.cells[idx].dataset.sort !== undefined
          ? b.cells[idx].dataset.sort : b.cells[idx].textContent;
        var na = parseFloat(ka), nb = parseFloat(kb), cmp;
        if (!isNaN(na) && !isNaN(nb)) { cmp = na - nb; }
        else { cmp = cmpText(ka, kb, true); }
        return asc ? cmp : -cmp;
      });
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });
});
</script>"""

KNOWN_SHEET_JS = """<script>
(function () {
  var backdrop = document.getElementById('sheetBackdrop');
  if (!backdrop) return;
  var offen = null;
  var ENTRY_FORM_RE = /^\\/known\\/(channel|raster|band)\\?/;
  function resetEntryTab(sheet) {
    var tab = sheet.querySelector('.tab[data-pane="entry"]');
    var pane = sheet.querySelector('.sheet-pane[data-pane="entry"]');
    if (tab) tab.hidden = true;
    if (pane) pane.innerHTML = '';
  }
  function showSheetTab(sheet, which) {
    if (which !== 'entry') resetEntryTab(sheet);
    sheet.querySelectorAll('.tabbar .tab').forEach(function (b) {
      b.classList.toggle('active', b.dataset.pane === which);
    });
    sheet.querySelectorAll('.sheet-pane').forEach(function (p) {
      p.classList.toggle('active', p.dataset.pane === which);
    });
  }
  function openSheet(name) {
    closeSheet();
    var s = document.getElementById('sheet-' + name);
    if (!s) return;
    showSheetTab(s, 'bearbeiten');
    s.hidden = false; backdrop.hidden = false; offen = s;
    document.body.style.overflow = 'hidden';
  }
  function closeSheet() {
    if (offen) { resetEntryTab(offen); offen.hidden = true; }
    backdrop.hidden = true; offen = null;
    document.body.style.overflow = '';
  }
  document.querySelectorAll('#tileGrid .tile').forEach(function (t) {
    t.addEventListener('click', function () { openSheet(t.dataset.file); });
  });
  // T-126: Ansichtsschalter je Fliese. Der Klick auf das Label darf NICHT bis
  // zum Fliesen-Handler darueber durchschlagen - der wuerde sonst bei jedem
  // Umschalten das Bearbeitungsblatt aufklappen.
  function setTileView(file, ein) {
    var tile = document.querySelector('#tileGrid .tile[data-file="' + file + '"]');
    if (!tile) return;
    tile.classList.toggle('view-on', ein);
    tile.classList.toggle('view-off', !ein);
  }
  function setViewCount(shown, total) {
    var sp = document.getElementById('viewCount');
    if (sp) sp.textContent = 'Catalog files in view: ' + shown + ' of ' + total;
  }
  function postView(body) {
    return fetch('/known/view', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ok) throw new Error((d && d.error) || 'unknown error');
      setViewCount(d.shown, d.total);
      return d;
    });
  }
  document.querySelectorAll('#tileGrid .t-view').forEach(function (lab) {
    lab.addEventListener('click', function (ev) { ev.stopPropagation(); });
  });
  document.querySelectorAll('#tileGrid .t-view-cb').forEach(function (cb) {
    cb.addEventListener('change', function () {
      var ein = cb.checked, file = cb.dataset.file;
      setTileView(file, ein);
      postView('file=' + encodeURIComponent(file) + '&visible=' + (ein ? '1' : '0'))
        .catch(function (err) {
          cb.checked = !ein;
          setTileView(file, !ein);
          alert('View not saved: ' + err.message);
        });
    });
  });
  function setAllView(ein) {
    postView('all=' + (ein ? '1' : '0')).then(function () {
      document.querySelectorAll('#tileGrid .t-view-cb').forEach(function (cb) {
        cb.checked = ein;
        setTileView(cb.dataset.file, ein);
      });
    }).catch(function (err) {
      alert('View not saved: ' + err.message);
    });
  }
  var btnViewOn = document.getElementById('btnViewAllOn');
  if (btnViewOn) btnViewOn.addEventListener('click', function () { setAllView(true); });
  var btnViewOff = document.getElementById('btnViewAllOff');
  if (btnViewOff) btnViewOff.addEventListener('click', function () { setAllView(false); });
  // Neuanlage-Sheet (T-119): id 'sheet--neu' = 'sheet-' + '-neu'. Keine
  // Kollision mit Dateinamen moeglich, da diese auf .json enden muessen.
  var btnNeu = document.getElementById('btnNeueKatalogdatei');
  if (btnNeu) btnNeu.addEventListener('click', function () { openSheet('-neu'); });
  // T-121: CSV-Import als zweites Sheet ('sheet--csv'). T-122: CSV-Export als
  // drittes ('sheet--export') - der Export braucht eine Dateiauswahl, sein
  // Sheet enthaelt deshalb ein GET-Formular und kein JavaScript.
  var btnCsv = document.getElementById('btnCsvImport');
  if (btnCsv) btnCsv.addEventListener('click', function () { openSheet('-csv'); });
  var btnExport = document.getElementById('btnCsvExport');
  if (btnExport) btnExport.addEventListener('click', function () { openSheet('-export'); });
  document.querySelectorAll('.sheet [data-close]').forEach(function (b) {
    b.addEventListener('click', closeSheet);
  });
  document.querySelectorAll('.sheet .tabbar .tab[data-pane]').forEach(function (b) {
    b.addEventListener('click', function () {
      showSheetTab(b.closest('.sheet'), b.dataset.pane);
    });
  });
  document.querySelectorAll('.sheet [data-tab-back]').forEach(function (b) {
    b.addEventListener('click', function () {
      showSheetTab(b.closest('.sheet'), 'bearbeiten');
    });
  });
  document.addEventListener('click', function (ev) {
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest('a[href]');
    if (!a) return;
    var href = a.getAttribute('href');
    if (!ENTRY_FORM_RE.test(href)) return;
    var sheet = a.closest('.sheet');
    if (!sheet) return;
    ev.preventDefault();
    var tab = sheet.querySelector('.tab[data-pane="entry"]');
    var pane = sheet.querySelector('.sheet-pane[data-pane="entry"]');
    fetch(href + '&embed=1')
      .then(function (resp) { return resp.text(); })
      .then(function (text) {
        pane.innerHTML = text;
        // BUG-0003: ueber innerHTML eingefuegte <script>-Elemente fuehrt der Browser
        // laut HTML-Spezifikation NICHT aus - die Formular-Initialisierung
        // (disp.value = hzToKHzDisplay(hid.value)) lief deshalb nie, ohne Fehlermeldung.
        // Jedes Skript wird durch ein frisch erzeugtes ersetzt. Die Kapselung in eine
        // sofort aufgerufene Funktion ist noetig, weil die Formularskripte const auf
        // oberster Ebene deklarieren - ein zweites geoeffnetes Formular braeche sonst mit
        // "Identifier has already been declared" ab. document.currentScript bleibt darin
        // gueltig, und replaceWith erhaelt die Position, sodass previousElementSibling
        // weiterhin das Formular findet.
        pane.querySelectorAll('script').forEach(function (altSkript) {
          // Nur klassische, ausfuehrbare Skripte neu erzeugen. Ein Block mit
          // type-Attribut traegt Nutzdaten, nicht Code - etwa
          // <script type="application/json" id="seg_init"> mit den Segmenten des
          // Bandformulars, der IM Formular liegt. Wird er ersetzt, ist das
          // Datenelement weg und querySelector('#seg_init') liefert null.
          var typ = altSkript.getAttribute('type');
          if (typ && typ.toLowerCase() !== 'text/javascript') { return; }
          var neuSkript = document.createElement('script');
          for (var i = 0; i < altSkript.attributes.length; i++) {
            neuSkript.setAttribute(altSkript.attributes[i].name, altSkript.attributes[i].value);
          }
          neuSkript.textContent = '(function () {\\n' + altSkript.textContent + '\\n})();';
          altSkript.replaceWith(neuSkript);
        });
        tab.hidden = false;
        showSheetTab(sheet, 'entry');
      })
      .catch(function () {
        pane.innerHTML = '<p class="error">form could not be loaded</p>';
        tab.hidden = false;
        showSheetTab(sheet, 'entry');
      });
  });
  backdrop.addEventListener('click', closeSheet);
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') closeSheet();
  });
  var root = document.getElementById('knownRoot');
  if (root && root.dataset.open) openSheet(root.dataset.open);
})();
</script>"""

# Formular-Logik fuer den Raster-Editor: kHz-Anzeige (wie im Kanalformular),
# Umschalten festes Raster / ICAO 8,33 und Submit-Pruefung. Eigene
# Raw-String-Konstante: keine f-String-Klammern, keine Escape-Probleme.
RASTER_FORM_JS = r"""<script>
(function () {
window.name = 'eb200_blockadmin';
const root = document.currentScript.previousElementSibling;
function hzToKHzDisplay(hz) {
  if (hz === '' || hz === null || hz === undefined) return '';
  const khz = Number(hz) / 1000;
  if (isNaN(khz)) return '';
  return khz.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 3});
}
function khzDisplayToHz(str) {
  if (!str || !str.trim()) return null;
  const normalized = str.replace(/,/g, '');
  const khz = parseFloat(normalized);
  if (isNaN(khz)) return null;
  return Math.round(khz * 1000);
}
function bindKHzField(dispId, hidId) {
  const disp = root.querySelector('#' + dispId);
  const hid = root.querySelector('#' + hidId);
  disp.value = hzToKHzDisplay(hid.value);
  disp.addEventListener('change', () => {
    const hz = khzDisplayToHz(disp.value);
    if (hz === null) {
      alert('Please enter a valid number.');
      disp.value = hzToKHzDisplay(hid.value);
      return;
    }
    hid.value = hz;
    disp.value = hzToKHzDisplay(hz);
  });
  return {disp: disp, hid: hid};
}
const fStart = bindKHzField('start_display', 'start_hz');
const fStop = bindKHzField('stop_display', 'stop_hz');
const fStep = bindKHzField('step_display', 'step_hz');
const schemeSel = root.querySelector('#step_scheme');
function syncSchemeFields() {
  const fest = schemeSel.value === '';
  fStep.disp.disabled = !fest;
  root.querySelector('#label_template').disabled = !fest;
}
schemeSel.addEventListener('change', syncSchemeFields);
syncSchemeFields();
root.addEventListener('submit', e => {
  const paare = [[fStart, 'start frequency'], [fStop, 'stop frequency']];
  if (schemeSel.value === '') paare.push([fStep, 'grid spacing']);
  for (const [f, was] of paare) {
    const hz = khzDisplayToHz(f.disp.value);
    if (hz === null) {
      e.preventDefault();
      alert('Please enter a valid number for the ' + was + '.');
      f.disp.focus();
      return;
    }
    f.hid.value = hz;
  }
});
})();
</script>"""

# Formular-Logik fuer den Band-Editor: kHz-Anzeige fuer Bandgrenzen,
# dynamische Segmentzeilen (hinzufuegen/entfernen, Hz-Werte im dataset der
# Zeile), Serialisierung nach segments_json beim Absenden. Initialbefuellung
# kommt als JSON-Block (script type=application/json, id=seg_init) mit.
BAND_FORM_JS = r"""<script>
(function () {
window.name = 'eb200_blockadmin';
const root = document.currentScript.previousElementSibling;
function hzToKHzDisplay(hz) {
  if (hz === '' || hz === null || hz === undefined) return '';
  const khz = Number(hz) / 1000;
  if (isNaN(khz)) return '';
  return khz.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 3});
}
function khzDisplayToHz(str) {
  if (!str || !str.trim()) return null;
  const normalized = str.replace(/,/g, '');
  const khz = parseFloat(normalized);
  if (isNaN(khz)) return null;
  return Math.round(khz * 1000);
}
function bindKHzField(dispId, hidId) {
  const disp = root.querySelector('#' + dispId);
  const hid = root.querySelector('#' + hidId);
  disp.value = hzToKHzDisplay(hid.value);
  disp.addEventListener('change', () => {
    const hz = khzDisplayToHz(disp.value);
    if (hz === null) {
      alert('Please enter a valid number.');
      disp.value = hzToKHzDisplay(hid.value);
      return;
    }
    hid.value = hz;
    disp.value = hzToKHzDisplay(hz);
  });
  return {disp: disp, hid: hid};
}
const fStart = bindKHzField('start_display', 'start_hz');
const fStop = bindKHzField('stop_display', 'stop_hz');
function bindSegRow(row) {
  const st = row.querySelector('.seg-start');
  const sp = row.querySelector('.seg-stop');
  st.value = hzToKHzDisplay(row.dataset.startHz || '');
  sp.value = hzToKHzDisplay(row.dataset.stopHz || '');
  for (const [inp, key] of [[st, 'startHz'], [sp, 'stopHz']]) {
    inp.addEventListener('change', () => {
      const hz = khzDisplayToHz(inp.value);
      if (hz === null) {
        alert('Please enter a valid number.');
        inp.value = hzToKHzDisplay(row.dataset[key] || '');
        return;
      }
      row.dataset[key] = hz;
      inp.value = hzToKHzDisplay(hz);
    });
  }
  row.querySelector('.seg-del').addEventListener('click', () => row.remove());
}
function addSegRow(title, startHz, stopHz) {
  const row = document.createElement('div');
  row.className = 'seg-row';
  if (startHz !== undefined && startHz !== null && startHz !== '') row.dataset.startHz = startHz;
  if (stopHz !== undefined && stopHz !== null && stopHz !== '') row.dataset.stopHz = stopHz;
  row.innerHTML = '<input type="text" class="seg-title" placeholder="Title (optional)">' +
    '<input type="text" class="seg-start" inputmode="decimal" placeholder="Start [kHz]">' +
    '<input type="text" class="seg-stop" inputmode="decimal" placeholder="Stop [kHz]">' +
    '<button type="button" class="seg-del">Remove</button>';
  row.querySelector('.seg-title').value = title || '';
  root.querySelector('#segRows').appendChild(row);
  bindSegRow(row);
}
root.querySelector('#segAdd').addEventListener('click', () => addSegRow('', '', ''));
const SEG_INIT = JSON.parse(root.querySelector('#seg_init').textContent || '[]');
SEG_INIT.forEach(s => addSegRow(s.title, s.start_hz, s.stop_hz));
root.addEventListener('submit', e => {
  const paare = [[fStart, 'start frequency'], [fStop, 'stop frequency']];
  for (const [f, was] of paare) {
    const hz = khzDisplayToHz(f.disp.value);
    if (hz === null) {
      e.preventDefault();
      alert('Please enter a valid number for the ' + was + '.');
      f.disp.focus();
      return;
    }
    f.hid.value = hz;
  }
  const segs = [];
  for (const row of root.querySelectorAll('#segRows .seg-row')) {
    const titel = row.querySelector('.seg-title').value.trim();
    const st = khzDisplayToHz(row.querySelector('.seg-start').value);
    const sp = khzDisplayToHz(row.querySelector('.seg-stop').value);
    if (st === null && sp === null && !titel) continue;
    if (st === null || sp === null) {
      e.preventDefault();
      alert('Please fill in start and stop of every segment (or remove the row).');
      return;
    }
    segs.push({title: titel, start_hz: st, stop_hz: sp});
  }
  root.querySelector('#segments_json').value = JSON.stringify(segs);
});
})();
</script>"""


# ---------------------------------------------------------------------------
# Known-Frequencies-Editor: HTML-Bausteine
# ---------------------------------------------------------------------------

def _mhz(hz) -> str:
    return f"{hz / 1e6:.4f}"


def render_known_list_page(cat, raw_docs: dict, open_file: str = "") -> str:
    """
    GET /: je Katalogdatei eine Fliese (Name, Dienst, Baender/Eintraege als
    Pills, Ladefehler rot); Klick oeffnet das Bearbeitungsblatt mit der rohen
    Eintragsliste (kind, id, label, Frequenz(en), enabled, Bearbeiten/Loeschen
    fuer Einzelkanaele und Kanalgruppen) und den eingebetteten Formular-Reitern.
    """
    # T-126: Ansichtsauswahl je Request lesen (winzige Datei, kein Cache) -
    # der Editor zeigt IMMER alle Dateien, die Auswahl faerbt nur die Fliese.
    hidden = _view_hidden_set()
    sichtbar_n = sum(1 for x in cat.files if x["name"] not in hidden)
    tiles = []
    sheets = []
    for f in cat.files:
        name = f["name"]
        baender_n = sum(1 for b in cat.bands if b["file"] == name)
        doc = raw_docs.get(name)
        entries = (doc.get("entries") if isinstance(doc, dict)
                   and isinstance(doc.get("entries"), list) else [])

        fehler_html = ""
        if "error" in f:
            fehler_html = f'<p class="error">catalog file unreadable: {html.escape(str(f["error"]))}</p>'
        else:
            eigene_fehler = [e for e in cat.errors if e.startswith(f"{name}:")]
            fehler_html = "".join(f'<p class="error">{html.escape(e)}</p>' for e in eigene_fehler)

        abdeckung = ", ".join(f"{_mhz(lo)}-{_mhz(hi)} MHz" for lo, hi in f["coverage"]) or "-"
        quelle = ((doc.get("meta") or {}).get("source") or "-") if isinstance(doc, dict) else "-"

        # Band-Tabelle aus den ROHEN bands der Datei (nicht der Loader-
        # Sicht), damit auch fehlerhafte Baender sichtbar und ueber den
        # Editor reparierbar bleiben.
        raw_bands = (doc.get("bands") if isinstance(doc, dict)
                     and isinstance(doc.get("bands"), list) else [])
        band_rows = []
        for b in raw_bands:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("id", "-"))
            titel = str(b.get("title", "") or bid)
            try:
                b_sort = int(b["start_hz"])
                bereich = f"{_mhz(b_sort)}-{_mhz(int(b['stop_hz']))} MHz"
            except (KeyError, TypeError, ValueError):
                b_sort = -1
                bereich = "-"
            segs = b.get("segments") if isinstance(b.get("segments"), list) else []
            del_titel = html.escape(titel)
            b_akt = (
                f'<a href="/known/band?file={quote(name)}&id={quote(bid)}">Edit</a> '
                f'<form class="inline" method="post" action="/known/band/delete" '
                f'onsubmit="return confirm(\'Delete band &quot;{del_titel}&quot; permanently?\');">'
                f'<input type="hidden" name="file" value="{html.escape(name)}">'
                f'<input type="hidden" name="id" value="{html.escape(bid)}">'
                f'<button type="submit">Delete</button></form>'
            )
            band_rows.append(f"""
        <tr>
          <td>{html.escape(bid)}</td>
          <td>{html.escape(titel)}</td>
          <td data-sort="{b_sort}">{bereich}</td>
          <td data-sort="{len(segs)}">{len(segs)}</td>
          <td class="actions">{b_akt}</td>
        </tr>""")
        if band_rows:
            bands_html = f"""
  <table class="known-table">
    <colgroup>
      <col style="width:18%"><col style="width:28%"><col style="width:20%">
      <col style="width:10%"><col style="width:24%">
    </colgroup>
    <tr><th class="sortable">Band id</th><th class="sortable">Title</th>
        <th class="sortable">Range</th><th class="sortable">Segments</th>
        <th>Actions</th></tr>
    {"".join(band_rows)}
  </table>"""
        else:
            bands_html = ""

        rows = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            kind = str(e.get("kind", "?"))
            kind_txt = {"channel": "Single channel", "raster": "Channel group"}.get(kind, kind)
            eid = str(e.get("id") or e.get("id_prefix") or "-")
            label = str(e.get("label", ""))
            ist_aktiv = e.get("enabled", True) is not False
            enabled_html = ('<span class="tick on" title="Active">&#10004;</span>'
                            if ist_aktiv else
                            '<span class="tick off" title="Inactive">&#10008;</span>')

            if kind == "channel":
                try:
                    sort_hz = int(e["freq_hz"])
                    freq_txt = _mhz(sort_hz) + " MHz"
                except (KeyError, TypeError, ValueError):
                    sort_hz = -1
                    freq_txt = "-"
                del_label = html.escape(label or eid)
                aktionen = (
                    f'<a href="/known/channel?file={quote(name)}&id={quote(eid)}">Edit</a> '
                    f'<form class="inline" method="post" action="/known/channel/delete" '
                    f'onsubmit="return confirm(\'Delete channel &quot;{del_label}&quot; permanently?\');">'
                    f'<input type="hidden" name="file" value="{html.escape(name)}">'
                    f'<input type="hidden" name="id" value="{html.escape(eid)}">'
                    f'<button type="submit">Delete</button></form>'
                )
            elif kind == "raster":
                try:
                    sort_hz = int(e["start_hz"])
                    freq_txt = f"{_mhz(sort_hz)}-{_mhz(int(e['stop_hz']))} MHz"
                except (KeyError, TypeError, ValueError):
                    sort_hz = -1
                    freq_txt = "-"
                del_label = html.escape(label or eid)
                aktionen = (
                    f'<a href="/known/raster?file={quote(name)}&id={quote(eid)}">Edit</a> '
                    f'<form class="inline" method="post" action="/known/raster/delete" '
                    f'onsubmit="return confirm(\'Delete channel group &quot;{del_label}&quot; permanently?\');">'
                    f'<input type="hidden" name="file" value="{html.escape(name)}">'
                    f'<input type="hidden" name="id" value="{html.escape(eid)}">'
                    f'<button type="submit">Delete</button></form>'
                )
            else:
                sort_hz = -1
                freq_txt = "-"
                aktionen = "–"

            rows.append(f"""
        <tr>
          <td>{html.escape(kind_txt)}</td>
          <td>{html.escape(eid)}</td>
          <td>{html.escape(label)}</td>
          <td data-sort="{sort_hz}">{freq_txt}</td>
          <td data-sort="{1 if ist_aktiv else 0}">{enabled_html}</td>
          <td class="actions">{aktionen}</td>
        </tr>""")

        rows_html = "".join(rows) if rows else (
            '<tr><td colspan="6" class="empty-row">'
            "no entries</td></tr>"
        )

        if len(entries) == 0 and baender_n == 0:
            delete_html = (
                f'<form class="inline" method="post" action="/known/file/delete" '
                f'onsubmit="return confirm(\'Delete catalog file {html.escape(name)} permanently?\');">'
                f'<input type="hidden" name="file" value="{html.escape(name)}">'
                f'<button type="submit">Delete catalog file</button></form>'
            )
        else:
            # T-123: kein Ersatztext - eine nicht leere Datei zeigt an dieser
            # Stelle einfach nichts an.
            delete_html = ""
        # T-124: Knopf statt Link - gleiche Bauart wie "Delete catalog file"
        # daneben. GET-Formular, weil der Export nichts veraendert.
        export_html = (
            f'<form class="inline" method="get" action="/known/csv-export">'
            f'<input type="hidden" name="file" value="{html.escape(name)}">'
            f'<button type="submit">CSV export</button></form>'
        )
        aktions_html = (f"{export_html} &middot; {delete_html}" if delete_html
                        else export_html)

        err_pill = '<span class="pill pill-err">Load error</span>' if fehler_html else ""
        # T-126: Ansichtsschalter. Zustand steht serverseitig in der Klasse,
        # ueberlebt also den Reload nach jedem Speichern (Post/Redirect/Get).
        in_view = name not in hidden
        view_cls = "view-on" if in_view else "view-off"
        view_chk = " checked" if in_view else ""
        tiles.append(f"""
<div class="tile {view_cls}" id="file-{html.escape(name)}" data-file="{html.escape(name)}">
  <div class="t-name">{html.escape(name)}</div>
  <div class="t-service">{html.escape(f.get("service") or "-")}</div>
  <div class="t-pills">
    <label class="t-view" title="Show this catalog file in D-SCAN, IFPAN and SDR scan">
      <input type="checkbox" class="t-view-cb" data-file="{html.escape(name)}"{view_chk}>
      <span>Show</span>
    </label>
    <span class="pill">Bands: {baender_n}</span>
    <span class="pill">Entries: {len(entries)}</span>
    {err_pill}
  </div>
</div>""")
        defaults = (doc.get("defaults") if isinstance(doc, dict)
                    and isinstance(doc.get("defaults"), dict) else {})

        if isinstance(doc, dict):
            kopf_pane_html = _file_head_form_body(
                mode="edit", file_name=name, form=_file_head_form_from_doc(doc), embed=True)
        else:
            kopf_pane_html = ('<p class="error">catalog file unreadable - the file header '
                               'cannot be edited</p>')

        try:
            with open(_resolve_known_file(name), encoding="utf-8") as fh:
                json_text = fh.read()
            json_pane_html = _file_editor_form_body(file=name, text=json_text, embed=True)
        except (OSError, ValueError) as exc:
            json_pane_html = f'<p class="error">catalog file unreadable: {html.escape(str(exc))}</p>'

        kanal_pane_html = _channel_form_body(file=name, is_new=True, defaults=defaults, embed=True)
        raster_pane_html = _raster_form_body(file=name, is_new=True, defaults=defaults, embed=True)
        band_pane_html = _band_form_body(file=name, is_new=True, embed=True)

        sheets.append(f"""
<div class="sheet" id="sheet-{html.escape(name)}" hidden>
  <div class="sheet-head">
    <span class="s-name">{html.escape(name)}</span>
    <span class="sec-meta">Entries: {len(entries)} &middot; Bands: {baender_n}</span>
    <button type="button" class="s-close" data-close="1">&#10005; Close</button>
  </div>
  <div class="tabbar">
    <button type="button" class="tab active" data-pane="bearbeiten">Edit</button>
    <button type="button" class="tab" data-pane="kopf">File header</button>
    <button type="button" class="tab" data-pane="json">JSON</button>
    <button type="button" class="tab" data-pane="kanal">New channel</button>
    <button type="button" class="tab" data-pane="kanalgruppe">New channel group</button>
    <button type="button" class="tab" data-pane="band">New band</button>
    <button type="button" class="tab" data-pane="entry" hidden>Edit entry</button>
  </div>
  <div class="sheet-pane active" data-pane="bearbeiten">
  {fehler_html}
  <p class="field-hint">
    Service: {html.escape(f.get("service") or "-")} &middot;
    Source: {html.escape(quelle)} &middot;
    Coverage: {abdeckung} &middot;
    Bands: {baender_n} &middot;
    Entries: {len(entries)}
  </p>
  <p>{aktions_html}</p>{bands_html}
  <table class="known-table">
    <colgroup>
      <col style="width:13%"><col style="width:20%"><col style="width:23%">
      <col style="width:16%"><col style="width:8%"><col style="width:20%">
    </colgroup>
    <tr><th class="sortable">Type</th><th class="sortable">ID</th>
        <th class="sortable">Label</th><th class="sortable">Frequency</th>
        <th class="sortable">Enabled</th><th>Actions</th></tr>
    {rows_html}
  </table>
  </div>
  <div class="sheet-pane" data-pane="kopf">{kopf_pane_html}</div>
  <div class="sheet-pane" data-pane="json">{json_pane_html}</div>
  <div class="sheet-pane" data-pane="kanal">{kanal_pane_html}</div>
  <div class="sheet-pane" data-pane="kanalgruppe">{raster_pane_html}</div>
  <div class="sheet-pane" data-pane="band">{band_pane_html}</div>
  <div class="sheet-pane" data-pane="entry"></div>
</div>""")

    tiles_html = "".join(tiles) if tiles else (
        '<p class="field-hint">No catalog files yet.</p>'
    )
    sheets_html = "".join(sheets)

    # Neuanlage als Sheet direkt auf der Uebersicht (T-119) statt eigener
    # Seite - die Route /known/newfile bleibt als Fallback und als
    # Fehlerseite des Save-Handlers bestehen. embed=True rendert den
    # Abbrechen-Knopf als data-close (Sheet schliessen, siehe Edit 4).
    neu_pane_html = _file_head_form_body(mode="new", file_name="", form={}, embed=True)
    # T-121: CSV-Import im selben Muster. Zieldateiliste aus dem Verzeichnis
    # statt aus cat.files - auch eine nicht ladbare Datei bleibt so als
    # Anhaengeziel waehlbar.
    csv_pane_html = _csv_import_form_body(file_names=_known_file_names(), embed=True)
    # T-122: Export braucht nur eine Dateiauswahl - GET-Formular, kein JS.
    export_pane_html = _csv_export_form_body(file_names=_known_file_names())

    return PAGE_HEAD + f"""
<h1>Frequency catalog</h1>
<p class="new-button">
  <button type="button" class="primary" id="btnNeueKatalogdatei">+ New catalog file</button>
  <button type="button" id="btnCsvImport">CSV import</button>
  <button type="button" id="btnCsvExport">CSV export</button>
  <button type="button" id="btnViewAllOn">Show all</button>
  <button type="button" id="btnViewAllOff">Show none</button>
  <span id="viewCount">Catalog files in view: {sichtbar_n} of {len(cat.files)}</span>
</p>
<div id="knownRoot" data-open="{html.escape(open_file)}"></div>
<div id="tileGrid">{tiles_html}</div>
{sheets_html}
<div class="sheet" id="sheet--neu" hidden>
  <div class="sheet-head">
    <span class="s-name">Create new catalog file</span>
    <button type="button" class="s-close" data-close="1">&#10005; Close</button>
  </div>
  <div class="sheet-pane active" data-pane="bearbeiten">{neu_pane_html}</div>
</div>
<div class="sheet" id="sheet--csv" hidden>
  <div class="sheet-head">
    <span class="s-name">CSV import</span>
    <button type="button" class="s-close" data-close="1">&#10005; Close</button>
  </div>
  <div class="sheet-pane active" data-pane="bearbeiten">{csv_pane_html}</div>
</div>
<div class="sheet" id="sheet--export" hidden>
  <div class="sheet-head">
    <span class="s-name">CSV export</span>
    <button type="button" class="s-close" data-close="1">&#10005; Close</button>
  </div>
  <div class="sheet-pane active" data-pane="bearbeiten">{export_pane_html}</div>
</div>
<div id="sheetBackdrop" hidden></div>
<p class="hint">
  Catalog directory: {html.escape(str(DEFAULT_CATALOG_DIR))}<br>
  View selection: {html.escape(str(DEFAULT_VIEW_PATH))} - the "Show"
  switch on each tile hides a catalog file in the display servers. This
  editor always shows all catalog files; scan sets, MSCAN jobs and the
  channel picker on :8050 stay complete as well.<br>
  Changes take effect in the display servers (:8000/:8020/:8030/:8040) the
  next time the page is loaded - the catalog is re-read there when its
  mtime changes, and the browser fetches it once per page load.
</p>
""" + KNOWN_SHEET_JS + KNOWN_SORT_JS + PAGE_TAIL


def _channel_form_body(
    *,
    file: str,
    cid: str = "",
    is_new: bool = True,
    label: str = "",
    freq_hz: str = "",
    demod: Optional[str] = None,
    bw_hz: Optional[str] = None,
    squelch_dbuv: Optional[str] = None,
    antenna: Optional[str] = None,
    tags: Optional[str] = None,
    notes: Optional[str] = None,
    enabled: bool = True,
    defaults: Optional[dict] = None,
    error: Optional[str] = None,
    embed: bool = False,
) -> str:
    """
    defaults ist der (rohe) defaults-Block der Datei - nur fuer die
    "(inherited: ...)"-Hinweise. demod/bw_hz/squelch_dbuv/antenna/tags/notes
    sind None (bzw. leer), wenn der Eintrag selbst KEINEN eigenen Wert
    traegt (== geerbt); ein gesetzter Wert ist ein expliziter Override.
    embed=True: Abbrechen wechselt nur die Reiter-Pane zurueck (kein
    location.href) - Formular ist Teil des Bearbeitungsblatts einer Fliese.
    """
    defaults = defaults or {}
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    if is_new:
        id_field = (f'<input type="text" id="id" name="id" value="{html.escape(cid)}" '
                    f'pattern="[A-Za-z0-9._-]+" required>')
    else:
        id_field = f'<input type="text" id="id" name="id" value="{html.escape(cid)}" readonly>'
    checked = " checked" if enabled else ""

    squelch_eff = defaults.get("squelch_dbuv")
    squelch_hint = f"inherited: {squelch_eff:g}" if isinstance(squelch_eff, (int, float)) else "inherited: -"
    tags_eff = defaults.get("tags")
    tags_hint = f"inherited: {', '.join(tags_eff)}" if tags_eff else "inherited: -"
    notes_eff = defaults.get("notes")
    notes_hint = f"inherited: {notes_eff}" if notes_eff else "inherited: -"
    cancel_html = ('<button type="button" data-tab-back="1">Cancel</button>' if embed else
                   f'<button type="button" onclick="location.href=\'/?open={quote(file)}\'">Cancel</button>')

    return f"""
{error_html}
<form method="post" action="/known/channel/save" id="channelForm">
  <input type="hidden" name="file" value="{html.escape(file)}">
  <input type="hidden" name="orig_id" value="{html.escape(cid) if not is_new else ''}">
  <div class="form-grid">
    <label for="id">ID</label>
    <div>
      {id_field}
      <div class="field-hint">Allowed: A-Z a-z 0-9 . _ - (cannot be changed after creation).</div>
    </div>

    <label for="label">Label</label>
    <input type="text" id="label" name="label" value="{html.escape(label)}" required>

    <label for="freq_display">Frequency [kHz]</label>
    <div>
      <input type="text" id="freq_display" inputmode="decimal">
      <input type="hidden" id="freq_hz" name="freq_hz" value="{html.escape(freq_hz)}">
    </div>

    <label for="demod">Demodulation</label>
    <select id="demod" name="demod">
      {_demod_options_inherit_html(demod, defaults.get("demod"))}
    </select>

    <label for="bw_hz">Bandwidth</label>
    <select id="bw_hz" name="bw_hz">
      {_bandwidth_options_inherit_html(bw_hz, defaults.get("bw_hz"))}
    </select>

    <label for="squelch_dbuv">Squelch [dBµV]</label>
    <input type="number" step="0.1" id="squelch_dbuv" name="squelch_dbuv"
           value="{html.escape(squelch_dbuv) if squelch_dbuv else ''}" placeholder="{squelch_hint}">

    <label for="antenna">Antenna</label>
    <select id="antenna" name="antenna">
      {_antenna_options_inherit_html(antenna, defaults.get("antenna"))}
    </select>

    <label for="tags">Tags (comma-separated)</label>
    <input type="text" id="tags" name="tags" value="{html.escape(tags) if tags else ''}"
           placeholder="{html.escape(tags_hint)}">

    <label for="notes">Notes</label>
    <textarea id="notes" name="notes" placeholder="{html.escape(notes_hint)}"
      >{html.escape(notes) if notes else ''}</textarea>

    <label for="enabled">Active</label>
    <input type="checkbox" id="enabled" name="enabled"{checked}>
  </div>
  <p class="field-hint">Empty field = inherited from the file defaults (the hint shows the
    effective value); a value set here overrides it for this channel only.</p>
  <p>
    <input type="submit" value="{'Create' if is_new else 'Save'}">
    {cancel_html}
  </p>
</form>

<script>
(function () {{
window.name = 'eb200_blockadmin';
const root = document.currentScript.previousElementSibling;
function hzToKHzDisplay(hz) {{
  if (hz === '' || hz === null || hz === undefined) return '';
  const khz = Number(hz) / 1000;
  if (isNaN(khz)) return '';
  return khz.toLocaleString('en-US', {{minimumFractionDigits: 0, maximumFractionDigits: 3}});
}}
function khzDisplayToHz(str) {{
  if (!str || !str.trim()) return null;
  const normalized = str.replace(/,/g, '');
  const khz = parseFloat(normalized);
  if (isNaN(khz)) return null;
  return Math.round(khz * 1000);
}}
const freqDisplay = root.querySelector('#freq_display');
const freqHidden = root.querySelector('#freq_hz');
freqDisplay.value = hzToKHzDisplay(freqHidden.value);
freqDisplay.addEventListener('change', () => {{
  const hz = khzDisplayToHz(freqDisplay.value);
  if (hz === null) {{
    alert('Please enter a valid number.');
    freqDisplay.value = hzToKHzDisplay(freqHidden.value);
    return;
  }}
  freqHidden.value = hz;
  freqDisplay.value = hzToKHzDisplay(hz);
}});
root.addEventListener('submit', e => {{
  const hz = khzDisplayToHz(freqDisplay.value);
  if (hz === null) {{
    e.preventDefault();
    alert('Please enter a valid number for the frequency.');
    freqDisplay.focus();
    return;
  }}
  freqHidden.value = hz;
}});
}})();
</script>
"""


def render_channel_form_page(
    *,
    file: str,
    cid: str = "",
    is_new: bool = True,
    label: str = "",
    freq_hz: str = "",
    demod: Optional[str] = None,
    bw_hz: Optional[str] = None,
    squelch_dbuv: Optional[str] = None,
    antenna: Optional[str] = None,
    tags: Optional[str] = None,
    notes: Optional[str] = None,
    enabled: bool = True,
    defaults: Optional[dict] = None,
    error: Optional[str] = None,
) -> str:
    title = (f"Edit channel ({html.escape(cid)})" if not is_new
             else f"New channel in {html.escape(file)}")
    body = _channel_form_body(
        file=file, cid=cid, is_new=is_new, label=label, freq_hz=freq_hz,
        demod=demod, bw_hz=bw_hz, squelch_dbuv=squelch_dbuv, antenna=antenna,
        tags=tags, notes=notes, enabled=enabled, defaults=defaults, error=error,
    )
    return PAGE_HEAD + f"""
<h1>{title}</h1>
{body}
""" + PAGE_TAIL


def _raster_form_body(
    *,
    file: str,
    prefix: str = "",
    is_new: bool = True,
    start_hz: str = "",
    stop_hz: str = "",
    step_scheme: str = "",
    step_hz: str = "",
    label_template: str = "",
    demod: Optional[str] = None,
    bw_hz: Optional[str] = None,
    squelch_dbuv: Optional[str] = None,
    antenna: Optional[str] = None,
    tags: Optional[str] = None,
    notes: Optional[str] = None,
    enabled: bool = True,
    defaults: Optional[dict] = None,
    preview: Optional[str] = None,
    error: Optional[str] = None,
    embed: bool = False,
) -> str:
    """
    Formular fuer kind:'raster' (Kanalgruppe), analog _channel_form_body.
    preview ist fertiges HTML aus der Vorschau-Aktion und erscheint ueber den
    Buttons; geschrieben wird erst bei Aktion 'Create' bzw. 'Save'. Enter loest den
    ERSTEN Submit-Button aus - bewusst die Vorschau, nicht das Speichern (auch
    im Embed: der Vorschau-Knopf POSTet regulaer und rendert dabei die
    eigenstaendige Seite mit dem Vorschau-Ergebnis).
    """
    defaults = defaults or {}
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    preview_html = preview or ""
    if is_new:
        prefix_field = (f'<input type="text" id="id_prefix" name="id_prefix" '
                        f'value="{html.escape(prefix)}" pattern="[A-Za-z0-9._-]+" required>')
    else:
        prefix_field = (f'<input type="text" id="id_prefix" name="id_prefix" '
                        f'value="{html.escape(prefix)}" readonly>')
    checked = " checked" if enabled else ""
    sel_fest = " selected" if step_scheme != "airband_8k33" else ""
    sel_air = " selected" if step_scheme == "airband_8k33" else ""

    squelch_eff = defaults.get("squelch_dbuv")
    squelch_hint = f"inherited: {squelch_eff:g}" if isinstance(squelch_eff, (int, float)) else "inherited: -"
    tags_eff = defaults.get("tags")
    tags_hint = f"inherited: {', '.join(tags_eff)}" if tags_eff else "inherited: -"
    notes_eff = defaults.get("notes")
    notes_hint = f"inherited: {notes_eff}" if notes_eff else "inherited: -"
    cancel_html = ('<button type="button" data-tab-back="1">Cancel</button>' if embed else
                   f'<button type="button" onclick="location.href=\'/?open={quote(file)}\'">Cancel</button>')

    return f"""
{error_html}
<form method="post" action="/known/raster/save" id="rasterForm">
  <input type="hidden" name="file" value="{html.escape(file)}">
  <input type="hidden" name="orig_id" value="{html.escape(prefix) if not is_new else ''}">
  <div class="form-grid">
    <label for="id_prefix">ID prefix</label>
    <div>
      {prefix_field}
      <div class="field-hint">Channel IDs become &lt;prefix&gt;-&lt;label&gt;
        (cannot be changed after creation).</div>
    </div>

    <label for="start_display">Start [kHz]</label>
    <div>
      <input type="text" id="start_display" inputmode="decimal">
      <input type="hidden" id="start_hz" name="start_hz" value="{html.escape(start_hz)}">
    </div>

    <label for="stop_display">Stop [kHz]</label>
    <div>
      <input type="text" id="stop_display" inputmode="decimal">
      <input type="hidden" id="stop_hz" name="stop_hz" value="{html.escape(stop_hz)}">
    </div>

    <label for="step_scheme">Grid type</label>
    <select id="step_scheme" name="step_scheme">
      <option value=""{sel_fest}>Fixed grid (grid spacing)</option>
      <option value="airband_8k33"{sel_air}>ICAO 8.33 kHz (airband)</option>
    </select>

    <label for="step_display">Grid spacing [kHz]</label>
    <div>
      <input type="text" id="step_display" inputmode="decimal">
      <input type="hidden" id="step_hz" name="step_hz" value="{html.escape(step_hz)}">
      <div class="field-hint">Fixed grid only.</div>
    </div>

    <label for="label_template">Label template</label>
    <div>
      <input type="text" id="label_template" name="label_template"
             value="{html.escape(label_template)}" placeholder="{{mhz:.4f}}">
      <div class="field-hint">Placeholders: {{mhz}} {{khz}} {{hz}} {{n}} -
        empty = loader default {{mhz:.4f}}. Fixed grid only.</div>
    </div>

    <label for="demod">Demodulation</label>
    <select id="demod" name="demod">
      {_demod_options_inherit_html(demod, defaults.get("demod"))}
    </select>

    <label for="bw_hz">Bandwidth</label>
    <select id="bw_hz" name="bw_hz">
      {_bandwidth_options_inherit_html(bw_hz, defaults.get("bw_hz"))}
    </select>

    <label for="squelch_dbuv">Squelch [dBµV]</label>
    <input type="number" step="0.1" id="squelch_dbuv" name="squelch_dbuv"
           value="{html.escape(squelch_dbuv) if squelch_dbuv else ''}" placeholder="{squelch_hint}">

    <label for="antenna">Antenna</label>
    <select id="antenna" name="antenna">
      {_antenna_options_inherit_html(antenna, defaults.get("antenna"))}
    </select>

    <label for="tags">Tags (comma-separated)</label>
    <input type="text" id="tags" name="tags" value="{html.escape(tags) if tags else ''}"
           placeholder="{html.escape(tags_hint)}">

    <label for="notes">Notes</label>
    <textarea id="notes" name="notes" placeholder="{html.escape(notes_hint)}"
      >{html.escape(notes) if notes else ''}</textarea>

    <label for="enabled">Active</label>
    <input type="checkbox" id="enabled" name="enabled"{checked}>
  </div>
  <p class="field-hint">Empty inheritable field = inherited from the file defaults.
    Complete channel groups usually stay switched off (Active unchecked) and
    serve as a catalog reserve.</p>
  {preview_html}
  <p>
    <button type="submit" name="action" value="preview">Preview</button>
    <button type="submit" name="action" value="save">{'Create' if is_new else 'Save'}</button>
    {cancel_html}
  </p>
</form>
""" + RASTER_FORM_JS


def render_raster_form_page(
    *,
    file: str,
    prefix: str = "",
    is_new: bool = True,
    start_hz: str = "",
    stop_hz: str = "",
    step_scheme: str = "",
    step_hz: str = "",
    label_template: str = "",
    demod: Optional[str] = None,
    bw_hz: Optional[str] = None,
    squelch_dbuv: Optional[str] = None,
    antenna: Optional[str] = None,
    tags: Optional[str] = None,
    notes: Optional[str] = None,
    enabled: bool = True,
    defaults: Optional[dict] = None,
    preview: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    title = (f"Edit channel group ({html.escape(prefix)})" if not is_new
             else f"New channel group in {html.escape(file)}")
    body = _raster_form_body(
        file=file, prefix=prefix, is_new=is_new, start_hz=start_hz, stop_hz=stop_hz,
        step_scheme=step_scheme, step_hz=step_hz, label_template=label_template,
        demod=demod, bw_hz=bw_hz, squelch_dbuv=squelch_dbuv, antenna=antenna,
        tags=tags, notes=notes, enabled=enabled, defaults=defaults,
        preview=preview, error=error,
    )
    return PAGE_HEAD + f"""
<h1>{title}</h1>
{body}
""" + PAGE_TAIL


def _band_form_body(
    *,
    file: str,
    bid: str = "",
    is_new: bool = True,
    title: str = "",
    start_hz: str = "",
    stop_hz: str = "",
    segments: Optional[list] = None,
    error: Optional[str] = None,
    embed: bool = False,
) -> str:
    """
    Formular fuer ein bands-Element (Band mit optionalen Segmenten).
    Segmente werden clientseitig als Zeilenliste gepflegt und beim Absenden
    vom Formular-JS als JSON in das Hidden-Feld segments_json geschrieben;
    die Initialbefuellung kommt als JSON-Block (id=seg_init) mit -
    '<' wird dabei escaped, damit kein Inhalt den script-Block beendet. Der
    seg_init-Block liegt IM Formular (statt danach), damit BAND_FORM_JS ihn
    ueber root.querySelector() findet - root ist die eigene <form>, nicht
    global per getElementById (mehrere Baender-Formulare koennen gleichzeitig
    eingebettet im DOM stehen, wenn mehrere Katalogdateien offen waeren).
    """
    seg_init = json.dumps(segments or [], ensure_ascii=False).replace("<", "\\u003c")
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    if is_new:
        id_field = (f'<input type="text" id="id" name="id" '
                    f'value="{html.escape(bid)}" pattern="[A-Za-z0-9._-]+" required>')
    else:
        id_field = (f'<input type="text" id="id" name="id" '
                    f'value="{html.escape(bid)}" readonly>')
    cancel_html = ('<button type="button" data-tab-back="1">Cancel</button>' if embed else
                   f'<button type="button" onclick="location.href=\'/?open={quote(file)}\'">Cancel</button>')

    return f"""
{error_html}
<form method="post" action="/known/band/save" id="bandForm">
  <input type="hidden" name="file" value="{html.escape(file)}">
  <input type="hidden" name="orig_id" value="{html.escape(bid) if not is_new else ''}">
  <input type="hidden" name="segments_json" id="segments_json" value="">
  <div class="form-grid">
    <label for="id">Band id</label>
    <div>
      {id_field}
      <div class="field-hint">Cannot be changed after creation.</div>
    </div>

    <label for="title">Title</label>
    <div>
      <input type="text" id="title" name="title" value="{html.escape(title)}">
      <div class="field-hint">Empty = the band id is shown as title.</div>
    </div>

    <label for="start_display">Start [kHz]</label>
    <div>
      <input type="text" id="start_display" inputmode="decimal">
      <input type="hidden" id="start_hz" name="start_hz" value="{html.escape(start_hz)}">
    </div>

    <label for="stop_display">Stop [kHz]</label>
    <div>
      <input type="text" id="stop_display" inputmode="decimal">
      <input type="hidden" id="stop_hz" name="stop_hz" value="{html.escape(stop_hz)}">
    </div>
  </div>

  <h2>Segments</h2>
  <p class="field-hint">Optional; every segment must lie completely inside the
    band (stop strictly greater than start). Empty rows are skipped when
    saving.</p>
  <div id="segRows"></div>
  <p><button type="button" id="segAdd">Add segment</button></p>

  <p>
    <button type="submit">{'Create' if is_new else 'Save'}</button>
    {cancel_html}
  </p>
  <script type="application/json" id="seg_init">{seg_init}</script>
</form>
""" + BAND_FORM_JS


def render_band_form_page(
    *,
    file: str,
    bid: str = "",
    is_new: bool = True,
    title: str = "",
    start_hz: str = "",
    stop_hz: str = "",
    segments: Optional[list] = None,
    error: Optional[str] = None,
) -> str:
    page_title = (f"Edit band ({html.escape(bid)})" if not is_new
                  else f"New band in {html.escape(file)}")
    body = _band_form_body(
        file=file, bid=bid, is_new=is_new, title=title,
        start_hz=start_hz, stop_hz=stop_hz, segments=segments, error=error,
    )
    return PAGE_HEAD + f"""
<h1>{page_title}</h1>
{body}
""" + PAGE_TAIL


def _file_editor_form_body(*, file: str, text: str, error: Optional[str] = None,
                            embed: bool = False) -> str:
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    cancel_html = ('<button type="button" data-tab-back="1">Cancel</button>' if embed else
                   f'<button type="button" onclick="location.href=\'/?open={quote(file)}\'">Cancel</button>')
    return f"""
{error_html}
<form method="post" action="/known/file/save">
  <input type="hidden" name="file" value="{html.escape(file)}">
  <textarea name="content" rows="30"
    style="width:100%;font-family:monospace;font-size:0.85em;box-sizing:border-box;"
    >{html.escape(text)}</textarea>
  <p>
    <input type="submit" value="Save">
    {cancel_html}
  </p>
</form>
"""


def render_file_editor_page(*, file: str, text: str, error: Optional[str] = None) -> str:
    body = _file_editor_form_body(file=file, text=text, error=error)
    return PAGE_HEAD + f"""
<h1>Edit catalog file: {html.escape(file)}</h1>
<p class="field-hint">For bands, segments, channel groups and defaults - raw format see
  eb200_core/known_freq.py. Single channels are easier to maintain via
  "Edit"/"New channel" on the overview.</p>
{body}
""" + PAGE_TAIL


def _file_head_form_from_doc(doc: dict) -> dict:
    """Baut das form-dict fuer render_file_head_form_page aus einer bereits
    geladenen Katalogdatei (Vorbefuellung bei mode='edit')."""
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}
    form = {key: str(meta.get(key, "")) for key in _META_TEXT_FIELDS}
    form["meta_note"] = str(meta.get("note", meta.get("notes", "")))
    form["color"] = str(meta.get("color", ""))
    form["demod"] = str(defaults.get("demod", ""))
    form["bw_hz"] = str(defaults["bw_hz"]) if "bw_hz" in defaults else ""
    form["antenna"] = str(defaults.get("antenna", ""))
    form["squelch_dbuv"] = str(defaults["squelch_dbuv"]) if "squelch_dbuv" in defaults else ""
    tags = defaults.get("tags")
    form["tags"] = ", ".join(tags) if isinstance(tags, list) else ""
    form["notes"] = str(defaults.get("notes", ""))
    return form


def _file_head_form_body(*, mode: str, file_name: str, form: dict,
                          error: Optional[str] = None, embed: bool = False) -> str:
    """
    Formular fuer den Datei-Kopf (meta + defaults) einer Katalogdatei -
    Neuanlage (mode='new', Dateiname wird abgefragt) oder Bearbeitung
    (mode='edit', Dateiname liegt schon fest; das Bearbeitungsblatt nutzt
    ausschliesslich mode='edit', embed=True). Die drei inherit-Helfer
    beschriften die Leer-Option mit "(inherited: -)" - auf Datei-Ebene (es
    gibt nichts, wovon geerbt werden koennte) irrefuehrend, darum hier
    durch "(not set)" ersetzt.
    """
    is_new = mode == "new"
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""

    if is_new:
        name_field = f"""
    <label for="name">Catalog file name</label>
    <input type="text" id="name" name="name" value="{html.escape(form.get('name', ''))}"
           pattern="[A-Za-z0-9._-]+\\.json" placeholder="e.g. my_catalog.json" required>
"""
        file_hidden = ""
    else:
        name_field = ""
        file_hidden = f'<input type="hidden" name="file" value="{html.escape(file_name)}">'

    demod_html = _demod_options_inherit_html(form.get("demod"), None).replace(
        "(inherited: -)", "(not set)", 1)
    bw_html = _bandwidth_options_inherit_html(form.get("bw_hz"), None).replace(
        "(inherited: -)", "(not set)", 1)
    antenna_html = _antenna_options_inherit_html(form.get("antenna"), None).replace(
        "(inherited: -)", "(not set)", 1)
    if embed and is_new:
        # Neuanlage-Sheet auf der Uebersicht (T-119): dort gibt es keinen
        # "Edit"-Tab, Abbrechen schliesst das Sheet (data-close wird
        # von KNOWN_SHEET_JS generisch bedient).
        cancel_html = '<button type="button" data-close="1">Cancel</button>'
    elif embed:
        cancel_html = '<button type="button" data-tab-back="1">Cancel</button>'
    else:
        cancel_href = "/" if is_new else "/?open=" + quote(file_name)
        cancel_html = f'<button type="button" onclick="location.href=\'{cancel_href}\'">Cancel</button>'

    return f"""
{error_html}
<form method="post" action="/known/head/save">
  <input type="hidden" name="mode" value="{html.escape(mode)}">
  {file_hidden}
  <div class="form-grid">
    {name_field}
    <label for="service">Service name</label>
    <input type="text" id="service" name="service" value="{html.escape(form.get('service', ''))}" required>

    <label>Band color</label>
    <div class="farb-wahl">
      <input type="color" name="color"
             value="{html.escape(resolve_color(form.get('color', '')) or '#5a5a5a')}">
      <label style="font-weight:normal;margin-left:10px">
        <input type="checkbox" name="color_aus"
               {"checked" if not resolve_color(form.get('color', '')) else ""}>
        no color of its own (derive from the service name)
      </label>
      <div style="margin-top:6px">{_color_presets_html()}</div>
    </div>

    <label for="source">Source</label>
    <input type="text" id="source" name="source" value="{html.escape(form.get('source', ''))}">

    <label for="region">Region</label>
    <input type="text" id="region" name="region" value="{html.escape(form.get('region', ''))}">

    <label for="maintainer">Maintainer</label>
    <input type="text" id="maintainer" name="maintainer" value="{html.escape(form.get('maintainer', ''))}">

    <label for="meta_note">Note</label>
    <textarea id="meta_note" name="meta_note" rows="3">{html.escape(form.get('meta_note', ''))}</textarea>
  </div>

  <h2>Defaults for all channels</h2>
  <div class="form-grid">
    <label for="demod">Demodulation</label>
    <select id="demod" name="demod">
      {demod_html}
    </select>

    <label for="bw_hz">Bandwidth</label>
    <select id="bw_hz" name="bw_hz">
      {bw_html}
    </select>

    <label for="antenna">Antenna</label>
    <select id="antenna" name="antenna">
      {antenna_html}
    </select>

    <label for="squelch_dbuv">Squelch [dBµV]</label>
    <input type="text" id="squelch_dbuv" name="squelch_dbuv" value="{html.escape(form.get('squelch_dbuv', ''))}">

    <label for="tags">Tags (comma-separated)</label>
    <input type="text" id="tags" name="tags" value="{html.escape(form.get('tags', ''))}">

    <label for="notes">Notes</label>
    <textarea id="notes" name="notes" rows="3">{html.escape(form.get('notes', ''))}</textarea>
  </div>
  <p class="field-hint">Empty field = no key in defaults; channels without a
    value of their own inherit these values.</p>

  <p>
    <input type="submit" value="{'Create' if is_new else 'Save'}">
    {cancel_html}
  </p>
</form>
"""


def render_file_head_form_page(*, mode: str, file_name: str, form: dict,
                                error: Optional[str] = None) -> str:
    is_new = mode == "new"
    page_title = ("Create new catalog file" if is_new
                  else f"Edit file header: {html.escape(file_name)}")
    body = _file_head_form_body(mode=mode, file_name=file_name, form=form, error=error)
    return PAGE_HEAD + f"""
<h1>{page_title}</h1>
{body}
""" + PAGE_TAIL


# ---------------------------------------------------------------------------
# T-122: CSV-Export - HTML-Baustein
# ---------------------------------------------------------------------------

# T-123: Das Importformular hat zwei Ziele, die einander ausschliessen. Statt
# den Anwender raten zu lassen, welche Felder gerade gelten, sperrt dieses
# Skript die jeweils anderen. Gesperrte Felder senden nichts - fuer den
# Handler ist das unkritisch, er liest je Ziel ohnehin nur die zugehoerigen
# Felder.
CSV_IMPORT_JS = """
(function () {
  var form = document.getElementById('csvImportForm');
  if (!form) return;
  var modus = form.querySelector('#modus');
  if (!modus) return;
  var nurNeu = ['#new_name', '#service', '#demod', '#bw_hz', '#squelch_dbuv'];
  function sync() {
    var neu = modus.value !== 'append';
    nurNeu.forEach(function (sel) {
      var el = form.querySelector(sel);
      if (el) el.disabled = !neu;
    });
    var ziel = form.querySelector('#target');
    if (ziel) ziel.disabled = neu;
  }
  modus.addEventListener('change', sync);
  sync();
})();
"""


def _csv_export_form_body(*, file_names: list[str]) -> str:
    """
    Dateiauswahl fuer den Export. Bewusst ein GET-Formular: der Browser baut
    daraus /known/csv-export?file=<name> und laedt herunter - kein
    JavaScript, kein Zwischenschritt, und der Link laesst sich aus der
    Adresszeile wiederverwenden.
    """
    if not file_names:
        return ('<p class="field-hint">No catalog file yet - create one first, '
                'then export it. The export of an empty catalog file is also '
                'the CSV template to fill in.</p>')
    opts = "".join(f'<option value="{html.escape(n)}">{html.escape(n)}</option>'
                   for n in file_names)
    return f"""
<form method="get" action="/known/csv-export">
  <div class="form-grid">
    <label for="exportfile">Catalog file</label>
    <select id="exportfile" name="file">
      {opts}
    </select>
  </div>
  <p class="field-hint">
    The CSV contains only the header row and data. It holds what is in the
    catalog file: an empty field still means "inherited from the defaults".<br>
    Not in the CSV are channel groups and fields without a column of their
    own - such as transmitter site, program and transmitter power of the ORS
    lists, or the segments of a band. Both stay untouched as long as the CSV
    is imported back into the same catalog file.
  </p>
  <p>
    <input type="submit" value="Export">
    <button type="button" data-close="1">Close</button>
  </p>
</form>
"""


# ---------------------------------------------------------------------------
# T-121: CSV-Import - HTML-Bausteine
# ---------------------------------------------------------------------------

# Zwei Meldungsformen tragen einen ID-Konflikt: die des Loaders ueber
# validate_candidate ("id 'x' already used in y.json - ...") und die des
# Importhandlers gegen die Zieldatei ("channel 'x' already exists in
# y.json"). Fuer den Bericht werden beide in (ID, Datei) zerlegt; was auf
# keines der Muster passt, bleibt unveraendert als Liste stehen.
_CSV_KONFLIKT_RES = (
    re.compile(r"id '([^']+)' already used in (\S+\.json)"),
    re.compile(r"(?:channel|band) '([^']+)' already exists in (\S+\.json)"),
)


def _csv_zieldateiname(name: str) -> str:
    """
    Dateiname aus dem Importformular, mit ergaenzter Endung (T-124). Wer
    "OE3GAS_2" eintippt, meint die Katalogdatei - eine Fehlermeldung dafuer
    waere Schikane. Geprueft wird der Name weiterhin von
    _resolve_known_file(); diese Funktion macht ihn nur vollstaendig.
    """
    name = name.strip()
    if name and not name.lower().endswith(".json"):
        name += ".json"
    return name


def _csv_bericht_html(titel: str, zeilen: list[str], ziel: str,
                      is_new: bool) -> str:
    """
    Fehlerbericht des CSV-Imports (T-124).

    Die Meldungen der Pruefstufen sind maschinennah formuliert und standen
    bisher als Kette roter Zeilen da. Fuer den Anwender zaehlt aber zuerst,
    DASS nichts geschrieben wurde - bei einem fehlgeschlagenen Import in eine
    neue Datei war aus den Meldungen nicht zu erkennen, ob die Datei nun
    halb angelegt im Katalog liegt. Deshalb beginnt der Bericht mit genau
    diesem Satz. Danach kommen die ID-Konflikte als Tabelle (der mit Abstand
    haeufigste Fall) und zuletzt alles, was auf kein Konfliktmuster passt.
    """
    konflikte: list[tuple] = []
    rest: list[str] = []
    for zeile in zeilen or []:
        for muster in _CSV_KONFLIKT_RES:
            treffer = muster.search(zeile)
            if treffer:
                konflikte.append((treffer.group(1), treffer.group(2)))
                break
        else:
            rest.append(zeile)

    if is_new:
        kopf = (f"Nothing was written - the catalog file "
                f"{ziel or '(no name)'} was NOT created.")
    else:
        kopf = (f"Nothing was written - "
                f"{ziel or 'the target catalog file'} is unchanged.")

    teile = [f'<p class="bericht-kopf">{html.escape(kopf)}</p>']
    if titel:
        teile.append(f"<p>{html.escape(titel)}</p>")

    if konflikte:
        reihen = "".join(
            f"<tr><td>{html.escape(cid)}</td><td>{html.escape(datei)}</td></tr>"
            for cid, datei in konflikte[:50])
        weitere = len(konflikte) - 50
        if weitere > 0:
            reihen += f'<tr><td colspan="2">... and {weitere} more</td></tr>'
        teile.append(
            "<p>The following IDs are already in use:</p>"
            '<table class="known-table bericht-tabelle">'
            "<tr><th>ID</th><th>already in use in</th></tr>"
            f"{reihen}</table>"
            '<p class="field-hint">Channel and band IDs must be unique across the '
            'WHOLE catalog, not just within one catalog file - scan sets '
            'address their channels by ID. Options: change the IDs in the '
            'CSV - or, if they are the same channels, choose the target '
            '"Append to existing catalog file" with the catalog file named '
            'above and tick "Replace existing ids".</p>')

    if rest:
        posten = "".join(f"<li>{html.escape(z)}</li>" for z in rest[:50])
        weitere = len(rest) - 50
        if weitere > 0:
            posten += f"<li>... and {weitere} more</li>"
        teile.append(f"<ul>{posten}</ul>")

    return f'<div class="bericht">{"".join(teile)}</div>'


def _csv_import_form_body(*, file_names: list[str], modus: str = "neu",
                          new_name: str = "", service: str = "",
                          target: str = "", demod: str = "", bw_hz: str = "",
                          squelch_dbuv: str = "", overwrite: bool = False,
                          embed: bool = False, bericht: str = "") -> str:
    """
    Ein Formular fuer den gesamten Import (Upload + Ziel + Voreinstellungen).
    Bewusst OHNE Vorschauschritt: der Dateiinhalt eines <input type=file>
    laesst sich nach einem Fehlversuch nicht wieder vorbelegen, eine Vorschau
    muesste den Upload also zwischenspeichern. Stattdessen ist der Import
    alles-oder-nichts und meldet im Fehlerfall ALLE Beanstandungen auf
    einmal, damit die CSV in einem Durchgang berichtigt werden kann; seit
    T-123 sperrt CSV_IMPORT_JS zusaetzlich die Felder des jeweils anderen
    Ziels.

    `bericht` ist fertiges HTML aus _csv_bericht_html (T-124) und ersetzt die
    fruehere Kette roter Einzelmeldungen.

    embed=True: Abbrechen schliesst das Sheet (data-close, KNOWN_SHEET_JS);
    sonst zurueck zur Uebersicht.
    """
    modus_opts = "".join([
        f'<option value="neu"{" selected" if modus != "append" else ""}>'
        f'Create new catalog file</option>',
        f'<option value="append"{" selected" if modus == "append" else ""}>'
        f'Append to existing catalog file</option>',
    ])
    target_opts = [f'<option value=""{" selected" if not target else ""}>'
                   f'(please select)</option>']
    for name in file_names:
        sel = " selected" if name == target else ""
        target_opts.append(f'<option value="{html.escape(name)}"{sel}>'
                           f'{html.escape(name)}</option>')

    demod_html = _demod_options_inherit_html(demod or None, None).replace(
        "(inherited: -)", "(not set)", 1)
    bw_html = _bandwidth_options_inherit_html(bw_hz or None, None).replace(
        "(inherited: -)", "(not set)", 1)
    cancel_html = ('<button type="button" data-close="1">Cancel</button>' if embed
                   else '<button type="button" onclick="location.href=\'/\'">Cancel</button>')

    return f"""
{bericht}
<form method="post" action="/known/csv-import/save" enctype="multipart/form-data"
      id="csvImportForm">
  <div class="form-grid">
    <label for="csvdatei">CSV file</label>
    <input type="file" id="csvdatei" name="csvdatei" accept=".csv,text/csv" required>

    <label for="modus">Target</label>
    <select id="modus" name="modus">
      {modus_opts}
    </select>

    <label for="new_name">Catalog file name (only with "Create new catalog file")</label>
    <input type="text" id="new_name" name="new_name" value="{html.escape(new_name)}"
           placeholder="e.g. my_catalog">

    <label for="service">Service name (only with "Create new catalog file")</label>
    <input type="text" id="service" name="service" value="{html.escape(service)}">

    <label for="target">Existing catalog file (only with "Append to existing catalog file")</label>
    <select id="target" name="target">
      {"".join(target_opts)}
    </select>

    <label for="demod">Demodulation (defaults of the new catalog file)</label>
    <select id="demod" name="demod">
      {demod_html}
    </select>

    <label for="bw_hz">Bandwidth (defaults of the new catalog file)</label>
    <select id="bw_hz" name="bw_hz">
      {bw_html}
    </select>

    <label for="squelch_dbuv">Squelch [dBµV] (defaults of the new catalog file)</label>
    <input type="text" id="squelch_dbuv" name="squelch_dbuv"
           value="{html.escape(squelch_dbuv)}">

    <label for="overwrite">Replace existing ids</label>
    <input type="checkbox" id="overwrite" name="overwrite"{" checked" if overwrite else ""}>
  </div>
  <p class="field-hint">
    The defaults go into the header of the NEW catalog file; when appending,
    the header of the target catalog file stays untouched. Channels without
    their own demod/bw_hz inherit these values - if they are missing on both
    levels, validation rejects the import.<br>
    Without a tick at "Replace existing ids", an id that already exists is an
    error and nothing is written.
  </p>
  <p>
    <input type="submit" value="Import">
    {cancel_html}
    <a href="/known/csv-export">File layout: export a catalog file</a>
  </p>
</form>
<script>{CSV_IMPORT_JS}</script>
"""


def render_csv_import_page(**kwargs) -> str:
    body = _csv_import_form_body(**kwargs)
    return PAGE_HEAD + f"""
<h1>CSV import</h1>
{body}
""" + PAGE_TAIL


# ---------------------------------------------------------------------------
# T-1015: Marker-Import - HTML-Bausteine
# ---------------------------------------------------------------------------

def _marker_row_html(row: dict) -> str:
    idx = row["idx"]
    checked = " checked" if row.get("checked") else ""
    zeit = str(row.get("time", ""))
    freq_txt = _mhz(row["freq_hz"])
    level = row.get("level_dbuv")
    level_disp = f"{level:.1f}" if level is not None else "-"
    level_val = "" if level is None else str(level)
    dup = row.get("dup")
    cid = marker_channel_id(row["freq_hz"])
    if dup:
        neu_checked = " checked" if row.get("neu") else ""
        dup_freq_hz = dup.get("freq_hz")
        freq_paren = f" ({html.escape(_mhz(dup_freq_hz))} MHz)" if dup_freq_hz is not None else ""
        kanal_html = (
            f'existing: {html.escape(str(dup.get("id", "")))}{freq_paren} '
            f'<span class="dup-file">[{html.escape(str(dup.get("file", "")))}]</span><br>'
            f'<label><input type="checkbox" name="neu_{idx}"{neu_checked}> create anyway '
            f'as {html.escape(cid)}</label>'
        )
    else:
        kanal_html = f"new: {html.escape(cid)}"
    return f"""<tr>
  <td><input type="checkbox" name="sel_{idx}"{checked}><input type="hidden" name="time_{idx}" value="{html.escape(zeit)}"></td>
  <td><input type="text" name="freq_{idx}" value="{freq_txt}" inputmode="decimal"></td>
  <td class="num">{level_disp}<input type="hidden" name="level_{idx}" value="{html.escape(level_val)}"></td>
  <td>{kanal_html}</td>
</tr>"""


def _marker_import_form_body(*, data: str, target: str, new_name: str,
                              demod: str, bw_hz: str, squelch_dbuv: str,
                              rows: list[dict], parse_errors: list[str],
                              file_names: list[str],
                              error: Optional[str] = None) -> str:
    """
    Marker-Import-Formular (T-1015): EIN Formular fuer Vorschau UND
    Speichern, analog _raster_form_body (Buttons name="action"
    value="preview"/"save"). "Preview" parst IMMER die Rohtabelle im
    data-Textfeld frisch (Rueckfall auf einen neuen Einfuegevorgang);
    Frequenz-/Auswahl-Aenderungen in der Vorschautabelle gelten daher nur bis
    zum naechsten Vorschau-Klick - direkt danach "Importieren".
    """
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    parse_err_html = "".join(f'<p class="error">{html.escape(e)}</p>' for e in parse_errors)

    target_opts = [f'<option value=""{" selected" if not target else ""}>(new catalog file)</option>']
    for name in file_names:
        sel = " selected" if name == target else ""
        target_opts.append(f'<option value="{html.escape(name)}"{sel}>{html.escape(name)}</option>')

    if rows:
        rows_html = "".join(_marker_row_html(r) for r in rows)
        table_html = f"""
<table class="known-table marker-table">
  <colgroup><col style="width:3em"><col style="width:9em"><col style="width:5em"><col></colgroup>
  <thead><tr><th></th><th>MHz</th><th class="num">dBµV</th><th>Channel</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<input type="hidden" name="row_count" value="{len(rows)}">
"""
    else:
        table_html = '<p class="field-hint">No rows recognized yet - paste the table and click "Preview".</p>'

    demod_html = _demod_options_inherit_html(demod or None, None).replace(
        "(inherited: -)", "(please select)", 1)
    bw_html = _bandwidth_options_inherit_html(bw_hz or None, None).replace(
        "(inherited: -)", "(please select)", 1)

    return f"""
{error_html}
{parse_err_html}
<form method="post" action="/known/marker-import/save">
  <div class="form-grid">
    <label for="data">Marker list</label>
    <textarea id="data" name="data" rows="8"
      placeholder="Time / MHz / dBµV - tab- or semicolon-separated, header row optional">{html.escape(data)}</textarea>
  </div>
  <p class="field-hint">Take it from the waterfall overlay "Marker list" with "Copy" or paste it
    by hand (decimal point; a comma counts as thousands separator).</p>

  <div class="form-grid">
    <label for="target">Target catalog file</label>
    <select id="target" name="target">
      {"".join(target_opts)}
    </select>

    <label for="new_name">Catalog file name (with "(new catalog file)")</label>
    <input type="text" id="new_name" name="new_name" value="{html.escape(new_name)}"
           pattern="[A-Za-z0-9._-]+\\.json">

    <label for="demod">Demodulation (for all)</label>
    <select id="demod" name="demod">
      {demod_html}
    </select>

    <label for="bw_hz">Bandwidth (for all)</label>
    <select id="bw_hz" name="bw_hz">
      {bw_html}
    </select>

    <label for="squelch_dbuv">Squelch [dBµV] (for all)</label>
    <input type="text" id="squelch_dbuv" name="squelch_dbuv" value="{html.escape(squelch_dbuv)}">
  </div>
  <p class="field-hint">Demod/bandwidth/squelch apply to ALL imported markers (stored as
    defaults in the header of the target catalog file, the channels themselves inherit them) - preset
    from the catalog file of the first band hit by the first frequency, changeable at any time.</p>

  {table_html}
  <p class="field-hint">"Add to job" sends the selected channels to MSCAN job
    management (:8050). Hits of the duplicate check (tolerance +-bandwidth/2, against ALL
    catalog files) supply the EXISTING channel and are not created again; only new finds
    go into the target catalog file.<br>
    "Catalog only" writes just the new finds and creates no job.<br>
    The second tick in a hit row creates the channel anyway.</p>

  <p>
    <button type="submit" name="action" value="preview">Preview</button>
    <button type="submit" name="action" value="job">Add to job</button>
    <button type="submit" name="action" value="save">Catalog only</button>
    <button type="button" onclick="location.href='/'">Cancel</button>
  </p>
</form>
"""


def render_marker_import_page(**kwargs) -> str:
    body = _marker_import_form_body(**kwargs)
    return PAGE_HEAD + f"""
<h1>Marker import</h1>
{body}
""" + PAGE_TAIL


# Eigene Konstante (kein f-String), damit die JavaScript-Klammern nicht mit
# der f-String-Formatierung von render_marker_handoff_page kollidieren
# (CLAUDE.md-Regel). Zieladresse zur Laufzeit aus location.hostname
# (Fernzugriff/Pi), Formular-POST statt fetch wegen der Portgrenze, POST
# statt Query-Parameter wegen der URL-Laengengrenze. Der Knopf im Markup
# bleibt trotzdem bedienbar, falls JavaScript deaktiviert ist.
MARKER_HANDOFF_JS = """
document.getElementById('handoff').action =
  'http://' + location.hostname + ':8050/mscan/import';
document.getElementById('handoff').submit();
"""


def render_marker_handoff_page(ids: list[str]) -> str:
    ids_val = ",".join(ids)
    return PAGE_HEAD + f"""
<h1>Channels into MSCAN job</h1>
<p>Channels handed over to MSCAN job management: {len(ids)}</p>
<form id="handoff" method="post">
  <input type="hidden" name="ids" value="{html.escape(ids_val)}">
  <button type="submit">Continue to :8050</button>
</form>
<p><a href="/">Cancel</a></p>
<script>{MARKER_HANDOFF_JS}</script>
""" + PAGE_TAIL


# ---------------------------------------------------------------------------
# HTTP-Handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # Konsole ruhig halten, analog zu waterfall.py

    # -- Hilfsfunktionen -----------------------------------------------------

    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)  # "See Other" - Post/Redirect/Get-Muster
        self.send_header("Location", location)
        self.end_headers()

    def _read_form_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw)
        return {key: values[0] for key, values in parsed.items()}

    def _send_json(self, payload: dict, status: int = 200) -> None:
        """T-126: einzige JSON-Antwort dieses Servers (POST /known/view). Alle
        uebrigen Routen bleiben beim Post/Redirect/Get-Muster mit HTML."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_known_view_toggle(self) -> None:
        """
        T-126: eine Katalogdatei in die Ansicht der Anzeige-Server nehmen bzw.
        herausnehmen (file=<name>&visible=0|1) oder alle auf einmal
        (all=0|1). Gespeichert wird die AUSSCHLUSSliste in catalog_view.json.
        Bewusst JSON statt Redirect: die Uebersicht hat bis zu ~25 Fliesen,
        ein Seiten-Reload je Klick waere unbrauchbar.
        """
        form = self._read_form_body()
        hidden = _view_hidden_set()
        alle = form.get("all", "")
        if alle in ("0", "1"):
            hidden = set() if alle == "1" else set(_known_file_names())
        else:
            name = form.get("file", "")
            if not _KNOWN_FILENAME_RE.match(name):
                self._send_json({"error": f"invalid catalog file name {name!r}"}, status=400)
                return
            if form.get("visible", "") == "1":
                hidden.discard(name)
            else:
                hidden.add(name)
        try:
            _write_view_config(hidden)
        except OSError as exc:
            self._send_json({"error": f"catalog_view.json not writable: {exc}"},
                            status=500)
            return
        namen = _known_file_names()
        self._send_json({"ok": True,
                         "hidden_files": sorted(hidden),
                         "total": len(namen),
                         "shown": sum(1 for n in namen if n not in hidden)})

    # -- GET-Routen -----------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/":
            cat = load_catalog(DEFAULT_CATALOG_DIR)
            raw_docs = {}
            for f in cat.files:
                try:
                    raw_docs[f["name"]] = _load_known_dict(
                        os.path.join(DEFAULT_CATALOG_DIR, f["name"]))
                except (OSError, ValueError):
                    raw_docs[f["name"]] = None
            open_file = parse_qs(parsed.query).get("open", [""])[0]
            self._send_html(render_known_list_page(cat, raw_docs, open_file))

        elif parsed.path == "/known":
            self._redirect("/" + ("?" + parsed.query if parsed.query else ""))

        elif parsed.path == "/known/channel":
            self._handle_known_channel_form(parse_qs(parsed.query))

        elif parsed.path == "/known/raster":
            self._handle_known_raster_form(parse_qs(parsed.query))

        elif parsed.path == "/known/band":
            self._handle_known_band_form(parse_qs(parsed.query))

        elif parsed.path == "/known/file":
            self._handle_known_file_form(parse_qs(parsed.query))

        elif parsed.path == "/known/newfile":
            self._send_html(render_file_head_form_page(mode="new", file_name="", form={}))

        elif parsed.path == "/known/csv-export":
            self._handle_known_csv_export(parse_qs(parsed.query))

        elif parsed.path == "/known/csv-import":
            self._handle_known_csv_import_form()

        elif parsed.path == "/known/head":
            self._handle_known_head_form(parse_qs(parsed.query))

        elif parsed.path == "/known/marker-import":
            self._handle_known_marker_import_form(parse_qs(parsed.query))

        else:
            self.send_response(404)
            self.end_headers()

    # -- POST-Routen ----------------------------------------------------------

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/known/channel/save":
            self._handle_known_channel_save()
        elif parsed.path == "/known/channel/delete":
            self._handle_known_channel_delete()
        elif parsed.path == "/known/raster/save":
            self._handle_known_raster_save()
        elif parsed.path == "/known/raster/delete":
            self._handle_known_raster_delete()
        elif parsed.path == "/known/band/save":
            self._handle_known_band_save()
        elif parsed.path == "/known/band/delete":
            self._handle_known_band_delete()
        elif parsed.path == "/known/file/save":
            self._handle_known_file_save()
        elif parsed.path == "/known/head/save":
            self._handle_known_head_save()
        elif parsed.path == "/known/file/delete":
            self._handle_known_file_delete()
        elif parsed.path == "/known/marker-import/save":
            self._handle_known_marker_import_save()
        elif parsed.path == "/known/csv-import/save":
            self._handle_known_csv_import_save()
        elif parsed.path == "/known/view":
            self._handle_known_view_toggle()
        else:
            self.send_response(404)
            self.end_headers()

    # -- Known-Frequencies-Editor ---------------------------------------------

    def _rerender_channel_form(self, form: dict, raw_file: str, orig_id: str,
                               is_new: bool, error: str, status: int = 400,
                               defaults: Optional[dict] = None) -> None:
        """Formular mit den ROHEN Eingaben des Nutzers erneut anzeigen (nichts
        geht verloren), egal an welcher Stelle die Pruefung fehlgeschlagen ist."""
        self._send_html(render_channel_form_page(
            file=raw_file, cid=form.get("id", "").strip() or orig_id, is_new=is_new,
            label=form.get("label", ""), freq_hz=form.get("freq_hz", ""),
            demod=form.get("demod") or None, bw_hz=form.get("bw_hz") or None,
            squelch_dbuv=form.get("squelch_dbuv") or None, antenna=form.get("antenna") or None,
            tags=form.get("tags") or None, notes=form.get("notes") or None,
            enabled=(form.get("enabled") == "on"),
            defaults=defaults,
            error=error,
        ), status=status)

    def _handle_known_channel_form(self, params: dict) -> None:
        raw_file = params.get("file", [""])[0]
        cid = params.get("id", [""])[0]
        embed = params.get("embed", [""])[0] == "1"

        def send(status: int = 200, **kwargs) -> None:
            if embed:
                self._send_html(_channel_form_body(embed=True, **kwargs), status=status)
            else:
                self._send_html(render_channel_form_page(**kwargs), status=status)

        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            send(file=raw_file, cid=cid, is_new=not cid,
                 error=f"catalog file unreadable: {exc}", status=400)
            return

        defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}

        if not cid:
            send(file=raw_file, is_new=True, defaults=defaults)
            return

        idx = _find_channel_entry(doc, cid)
        if idx is None:
            self.send_response(404)
            self.end_headers()
            return
        entry = doc["entries"][idx]

        # Nur der EIGENE Wert des Eintrags wird vorbelegt (== expliziter
        # Override); fehlt der Schluessel, bleibt das Feld leer (== geerbt)
        # und render_channel_form_page zeigt den wirksamen Wert als Hinweis.
        tags_val = entry.get("tags") if "tags" in entry else None
        squelch_val = entry.get("squelch_dbuv") if "squelch_dbuv" in entry else None
        send(
            file=raw_file, cid=cid, is_new=False,
            label=str(entry.get("label", "")),
            freq_hz=str(entry.get("freq_hz", "")),
            demod=entry.get("demod") if "demod" in entry else None,
            bw_hz=str(entry["bw_hz"]) if "bw_hz" in entry else None,
            squelch_dbuv=str(squelch_val) if squelch_val is not None else None,
            antenna=entry.get("antenna") if "antenna" in entry else None,
            tags=", ".join(tags_val) if tags_val else None,
            notes=entry.get("notes") if "notes" in entry else None,
            enabled=entry.get("enabled", True) is not False,
            defaults=defaults,
        )

    def _handle_known_channel_save(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        orig_id = form.get("orig_id", "").strip()
        is_new = not orig_id

        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return

        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            self._rerender_channel_form(form, raw_file, orig_id, is_new,
                                        f"catalog file unreadable: {exc}")
            return

        defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}

        idx = None
        existing = None
        if not is_new:
            idx = _find_channel_entry(doc, orig_id)
            if idx is None:
                self.send_response(404)
                self.end_headers()
                return
            existing = doc["entries"][idx]

        try:
            entry = build_channel_entry(form, existing, defaults)
        except ValueError as exc:
            self._rerender_channel_form(form, raw_file, orig_id, is_new, str(exc),
                                        defaults=defaults)
            return

        if not is_new and entry["id"] != orig_id:
            self._rerender_channel_form(
                form, raw_file, orig_id, False,
                "id cannot be changed when editing", defaults=defaults)
            return

        entries = doc.get("entries")
        if not isinstance(entries, list):
            entries = doc["entries"] = []

        if is_new:
            if _find_channel_entry(doc, entry["id"]) is not None:
                self._rerender_channel_form(
                    form, raw_file, orig_id, True,
                    f"id {entry['id']!r} already exists in this catalog file",
                    defaults=defaults)
                return
            entries.append(entry)
        else:
            entries[idx] = entry

        fehler = validate_candidate(raw_file, doc)
        if fehler:
            self._rerender_channel_form(
                form, raw_file, orig_id, is_new,
                "validation failed: " + "; ".join(fehler), defaults=defaults)
            return

        _write_known_file(path, doc)
        self._redirect("/?open=" + quote(raw_file))

    def _handle_known_channel_delete(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        cid = form.get("id", "").strip()
        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        idx = _find_channel_entry(doc, cid)
        if idx is not None:
            del doc["entries"][idx]
            _write_known_file(path, doc)
        self._redirect("/?open=" + quote(raw_file))

    def _rerender_raster_form(self, form: dict, raw_file: str, orig_id: str,
                              is_new: bool, error: Optional[str], status: int = 400,
                              defaults: Optional[dict] = None,
                              preview: Optional[str] = None) -> None:
        """Raster-Formular mit den ROHEN Eingaben erneut anzeigen - fuer
        Fehler (status 400) UND fuer die Vorschau (status 200)."""
        self._send_html(render_raster_form_page(
            file=raw_file, prefix=form.get("id_prefix", "").strip() or orig_id,
            is_new=is_new,
            start_hz=form.get("start_hz", ""), stop_hz=form.get("stop_hz", ""),
            step_scheme=form.get("step_scheme", ""), step_hz=form.get("step_hz", ""),
            label_template=form.get("label_template", ""),
            demod=form.get("demod") or None, bw_hz=form.get("bw_hz") or None,
            squelch_dbuv=form.get("squelch_dbuv") or None,
            antenna=form.get("antenna") or None,
            tags=form.get("tags") or None, notes=form.get("notes") or None,
            enabled=(form.get("enabled") == "on"),
            defaults=defaults, preview=preview, error=error,
        ), status=status)

    def _handle_known_raster_form(self, params: dict) -> None:
        raw_file = params.get("file", [""])[0]
        prefix = params.get("id", [""])[0]
        embed = params.get("embed", [""])[0] == "1"

        def send(status: int = 200, **kwargs) -> None:
            if embed:
                self._send_html(_raster_form_body(embed=True, **kwargs), status=status)
            else:
                self._send_html(render_raster_form_page(**kwargs), status=status)

        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            send(file=raw_file, prefix=prefix, is_new=not prefix,
                 error=f"catalog file unreadable: {exc}", status=400)
            return

        defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}

        if not prefix:
            send(file=raw_file, is_new=True, defaults=defaults)
            return

        idx = _find_raster_entry(doc, prefix)
        if idx is None:
            self.send_response(404)
            self.end_headers()
            return
        entry = doc["entries"][idx]

        # Nur der EIGENE Wert des Eintrags wird vorbelegt (== expliziter
        # Override); fehlt der Schluessel, bleibt das Feld leer (== geerbt)
        # und render_raster_form_page zeigt den wirksamen Wert als Hinweis.
        tags_val = entry.get("tags") if "tags" in entry else None
        squelch_val = entry.get("squelch_dbuv") if "squelch_dbuv" in entry else None
        send(
            file=raw_file, prefix=prefix, is_new=False,
            start_hz=str(entry.get("start_hz", "")),
            stop_hz=str(entry.get("stop_hz", "")),
            step_scheme=str(entry.get("step_scheme") or ""),
            step_hz=str(entry.get("step_hz", "")),
            label_template=str(entry.get("label_template", "")),
            demod=entry.get("demod") if "demod" in entry else None,
            bw_hz=str(entry["bw_hz"]) if "bw_hz" in entry else None,
            squelch_dbuv=str(squelch_val) if squelch_val is not None else None,
            antenna=entry.get("antenna") if "antenna" in entry else None,
            tags=", ".join(tags_val) if tags_val else None,
            notes=entry.get("notes") if "notes" in entry else None,
            enabled=entry.get("enabled", True) is not False,
            defaults=defaults,
        )

    def _handle_known_raster_save(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        orig_id = form.get("orig_id", "").strip()
        is_new = not orig_id

        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return

        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            self._rerender_raster_form(form, raw_file, orig_id, is_new,
                                       f"catalog file unreadable: {exc}")
            return

        defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}

        idx = None
        existing = None
        if not is_new:
            idx = _find_raster_entry(doc, orig_id)
            if idx is None:
                self.send_response(404)
                self.end_headers()
                return
            existing = doc["entries"][idx]

        try:
            entry = build_raster_entry(form, existing, defaults)
        except ValueError as exc:
            self._rerender_raster_form(form, raw_file, orig_id, is_new, str(exc),
                                       defaults=defaults)
            return

        if not is_new and entry["id_prefix"] != orig_id:
            self._rerender_raster_form(
                form, raw_file, orig_id, False,
                "id_prefix cannot be changed when editing", defaults=defaults)
            return

        entries = doc.get("entries")
        if not isinstance(entries, list):
            entries = doc["entries"] = []

        if is_new:
            if _find_raster_entry(doc, entry["id_prefix"]) is not None:
                self._rerender_raster_form(
                    form, raw_file, orig_id, True,
                    f"id_prefix {entry['id_prefix']!r} already exists in this catalog file",
                    defaults=defaults)
                return
            entries.append(entry)
        else:
            entries[idx] = entry

        if form.get("action") == "preview":
            # Nur anzeigen, nichts schreiben - doc wurde ausschliesslich im
            # Speicher veraendert.
            anzahl, zeilen, fehler = _raster_preview(raw_file, doc, entry["id_prefix"])
            teile = [f"<b>Preview</b> - channels: {anzahl}"]
            teile.extend(html.escape(z) for z in zeilen)
            teile.extend(f'<span class="error">{html.escape(x)}</span>' for x in fehler)
            if anzahl == 0 and not fehler:
                teile.append("This channel group generates no channels in this range.")
            preview_html = '<p class="field-hint">' + "<br>".join(teile) + "</p>"
            self._rerender_raster_form(form, raw_file, orig_id, is_new, None,
                                       status=200, defaults=defaults,
                                       preview=preview_html)
            return

        fehler = validate_candidate(raw_file, doc)
        if fehler:
            self._rerender_raster_form(
                form, raw_file, orig_id, is_new,
                "validation failed: " + "; ".join(fehler), defaults=defaults)
            return

        _write_known_file(path, doc)
        self._redirect("/?open=" + quote(raw_file))

    def _handle_known_raster_delete(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        prefix = form.get("id", "").strip()
        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        idx = _find_raster_entry(doc, prefix)
        if idx is not None:
            del doc["entries"][idx]
            _write_known_file(path, doc)
        self._redirect("/?open=" + quote(raw_file))

    def _rerender_band_form(self, form: dict, raw_file: str, orig_id: str,
                            is_new: bool, error: str) -> None:
        """Band-Formular mit den ROHEN Eingaben erneut anzeigen (Status 400).
        Die Segmentzeilen kommen aus segments_json zurueck, sofern lesbar."""
        try:
            segmente = json.loads(form.get("segments_json", "") or "[]")
            if not isinstance(segmente, list):
                segmente = []
        except json.JSONDecodeError:
            segmente = []
        self._send_html(render_band_form_page(
            file=raw_file, bid=form.get("id", "").strip() or orig_id,
            is_new=is_new, title=form.get("title", ""),
            start_hz=form.get("start_hz", ""), stop_hz=form.get("stop_hz", ""),
            segments=segmente, error=error,
        ), status=400)

    def _handle_known_band_form(self, params: dict) -> None:
        raw_file = params.get("file", [""])[0]
        bid = params.get("id", [""])[0]
        embed = params.get("embed", [""])[0] == "1"

        def send(status: int = 200, **kwargs) -> None:
            if embed:
                self._send_html(_band_form_body(embed=True, **kwargs), status=status)
            else:
                self._send_html(render_band_form_page(**kwargs), status=status)

        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            send(file=raw_file, bid=bid, is_new=not bid,
                 error=f"catalog file unreadable: {exc}", status=400)
            return

        if not bid:
            send(file=raw_file, is_new=True)
            return

        idx = _find_band_entry(doc, bid)
        if idx is None:
            self.send_response(404)
            self.end_headers()
            return
        band = doc["bands"][idx]

        segs = band.get("segments") if isinstance(band.get("segments"), list) else []
        send(
            file=raw_file, bid=bid, is_new=False,
            title=str(band.get("title", "")),
            start_hz=str(band.get("start_hz", "")),
            stop_hz=str(band.get("stop_hz", "")),
            segments=segs,
        )

    def _handle_known_band_save(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        orig_id = form.get("orig_id", "").strip()
        is_new = not orig_id

        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return

        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            self._rerender_band_form(form, raw_file, orig_id, is_new,
                                     f"catalog file unreadable: {exc}")
            return

        idx = None
        existing = None
        if not is_new:
            idx = _find_band_entry(doc, orig_id)
            if idx is None:
                self.send_response(404)
                self.end_headers()
                return
            existing = doc["bands"][idx]

        try:
            entry = build_band_entry(form, existing)
        except ValueError as exc:
            self._rerender_band_form(form, raw_file, orig_id, is_new, str(exc))
            return

        if not is_new and entry["id"] != orig_id:
            self._rerender_band_form(
                form, raw_file, orig_id, False,
                "band id cannot be changed when editing")
            return

        bands = doc.get("bands")
        if not isinstance(bands, list):
            bands = doc["bands"] = []

        if is_new:
            if _find_band_entry(doc, entry["id"]) is not None:
                self._rerender_band_form(
                    form, raw_file, orig_id, True,
                    f"band id {entry['id']!r} already exists in this catalog file")
                return
            bands.append(entry)
        else:
            bands[idx] = entry

        fehler = validate_candidate(raw_file, doc)
        if fehler:
            self._rerender_band_form(
                form, raw_file, orig_id, is_new,
                "validation failed: " + "; ".join(fehler))
            return

        _write_known_file(path, doc)
        self._redirect("/?open=" + quote(raw_file))

    def _handle_known_band_delete(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        bid = form.get("id", "").strip()
        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        idx = _find_band_entry(doc, bid)
        if idx is not None:
            del doc["bands"][idx]
            _write_known_file(path, doc)
        self._redirect("/?open=" + quote(raw_file))

    def _handle_known_file_form(self, params: dict) -> None:
        raw_file = params.get("file", [""])[0]
        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        self._send_html(render_file_editor_page(file=raw_file, text=text))

    def _handle_known_file_save(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        text = form.get("content", "")
        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        if not os.path.isfile(path):
            self.send_response(404)
            self.end_headers()
            return

        try:
            candidate = json.loads(text)
        except json.JSONDecodeError as exc:
            self._send_html(render_file_editor_page(
                file=raw_file, text=text,
                error=f"JSON error in line {exc.lineno}, column {exc.colno}: {exc.msg}",
            ), status=400)
            return
        if not isinstance(candidate, dict):
            self._send_html(render_file_editor_page(
                file=raw_file, text=text,
                error="top level must be a JSON object",
            ), status=400)
            return

        fehler = validate_candidate(raw_file, candidate)
        if fehler:
            self._send_html(render_file_editor_page(
                file=raw_file, text=text,
                error="validation failed: " + "; ".join(fehler),
            ), status=400)
            return

        _write_known_file(path, candidate)
        self._redirect("/?open=" + quote(raw_file))

    def _handle_known_head_form(self, params: dict) -> None:
        name = params.get("file", [""])[0]
        try:
            path = _resolve_known_file(name)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        self._send_html(render_file_head_form_page(
            mode="edit", file_name=name, form=_file_head_form_from_doc(doc),
        ))

    def _handle_known_head_save(self) -> None:
        form = self._read_form_body()
        mode = form.get("mode", "new")
        name = form.get("file", "") if mode == "edit" else form.get("name", "").strip()

        if mode != "edit":
            try:
                name = _normalize_new_catalog_filename(name)
            except ValueError as exc:
                self._send_html(render_file_head_form_page(
                    mode=mode, file_name=name, form=form, error=str(exc),
                ), status=400)
                return

        try:
            path = _resolve_known_file(name)
        except ValueError:
            self._send_html(render_file_head_form_page(
                mode=mode, file_name=name, form=form,
                error="invalid catalog file name (allowed: A-Z a-z 0-9 . _ -, must end with .json)",
            ), status=400)
            return

        existing = None
        if mode == "edit":
            try:
                existing = _load_known_dict(path)
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            except ValueError as exc:
                self._send_html(render_file_head_form_page(
                    mode=mode, file_name=name, form=form,
                    error=f"catalog file unreadable: {exc}",
                ), status=400)
                return
        elif os.path.isfile(path):
            self._send_html(render_file_head_form_page(
                mode=mode, file_name=name, form=form,
                error=f"catalog file {name!r} already exists",
            ), status=400)
            return

        try:
            doc = build_file_head(form, existing=existing)
        except ValueError as exc:
            self._send_html(render_file_head_form_page(
                mode=mode, file_name=name, form=form, error=str(exc),
            ), status=400)
            return

        fehler = validate_candidate(name, doc)
        if fehler:
            self._send_html(render_file_head_form_page(
                mode=mode, file_name=name, form=form,
                error="validation failed: " + "; ".join(fehler),
            ), status=400)
            return

        _write_known_file(path, doc)
        self._redirect("/?open=" + quote(name))

    def _handle_known_file_delete(self) -> None:
        form = self._read_form_body()
        raw_file = form.get("file", "")
        try:
            path = _resolve_known_file(raw_file)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        try:
            delete_known_file(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            self._send_html(
                PAGE_HEAD + f"""
<h1>Catalog file not deleted</h1>
<p class="error">{html.escape(str(exc))}</p>
<p><a href="/">Back</a></p>
""" + PAGE_TAIL,
                status=409,
            )
            return
        self._redirect("/")

    def _handle_known_csv_export(self, params: dict) -> None:
        """
        Eine Katalogdatei als CSV-Download (T-122). Ohne brauchbaren
        Dateinamen geht es zurueck zur Uebersicht, wo das Export-Sheet die
        Auswahl anbietet - so landet auch ein Aufruf ohne Parameter (der Link
        im Importformular) nicht auf einer Fehlerseite. Die BOM steht nur
        hier, nicht in catalog_csv_text(): ohne sie oeffnet Excel die Datei
        als cp1252.
        """
        name = params.get("file", [""])[0]
        try:
            path = _resolve_known_file(name)
        except ValueError:
            self._redirect("/")
            return
        try:
            doc = _load_known_dict(path)
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        except ValueError as exc:
            self._send_html(PAGE_HEAD + f"""
<h1>CSV export not possible</h1>
<p class="error">{html.escape(name)}: {html.escape(str(exc))}</p>
<p><a href="/">Back to the overview</a></p>
""" + PAGE_TAIL, status=400)
            return

        data = b"\xef\xbb\xbf" + catalog_csv_text(doc).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{os.path.splitext(name)[0]}.csv"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_known_csv_import_form(self) -> None:
        """GET-Maske als eigenstaendige Seite - Rueckfall fuer alte Links und
        Fehlerseite des Save-Handlers; der uebliche Weg ist das Sheet auf der
        Uebersicht (gleiches Muster wie /known/newfile seit T-119)."""
        self._send_html(render_csv_import_page(file_names=_known_file_names()))

    def _handle_known_csv_import_save(self) -> None:
        """
        POST des CSV-Imports: Upload zerlegen, CSV pruefen, Zieldokument
        bauen, EINMAL validieren, EINMAL schreiben. Jeder Fehler bricht vor
        dem Schreiben ab (alles-oder-nichts) und rendert die eigenstaendige
        Formularseite mit Fehlertext bzw. der Liste der beanstandeten Zeilen.
        """
        modus = "neu"
        new_name = ""
        service = ""
        target = ""
        demod = ""
        bw_raw = ""
        squelch_raw = ""
        overwrite = False

        def fehlerseite(text: str, zeilen: Optional[list[str]] = None,
                        status: int = 400) -> None:
            # Ziel und Zielart bestimmen den Kopfsatz des Berichts - "was
            # NOT created" gilt nur fuer eine neue Datei.
            is_new = modus != "append"
            ziel = new_name if is_new else target
            self._send_html(render_csv_import_page(
                file_names=_known_file_names(), modus=modus, new_name=new_name,
                service=service, target=target, demod=demod, bw_hz=bw_raw,
                squelch_dbuv=squelch_raw, overwrite=overwrite,
                bericht=_csv_bericht_html(text, zeilen or [], ziel, is_new),
                ), status=status)

        try:
            laenge = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            laenge = 0
        if laenge <= 0:
            fehlerseite("Empty upload - no file was transferred.")
            return
        if laenge > CSV_UPLOAD_MAX_BYTES:
            fehlerseite(f"Upload too large ({laenge} bytes, allowed: "
                        f"{CSV_UPLOAD_MAX_BYTES}).", status=413)
            return

        body = self.rfile.read(laenge)
        try:
            felder, dateien = parse_multipart_form(
                body, self.headers.get("Content-Type", ""))
        except ValueError as exc:
            fehlerseite(str(exc))
            return

        modus = felder.get("modus", "neu").strip() or "neu"
        new_name = _csv_zieldateiname(felder.get("new_name", ""))
        service = felder.get("service", "").strip()
        target = felder.get("target", "").strip()
        demod = felder.get("demod", "").strip()
        bw_raw = felder.get("bw_hz", "").strip()
        squelch_raw = felder.get("squelch_dbuv", "").strip()
        overwrite = felder.get("overwrite", "") != ""

        datei = dateien.get("csvdatei")
        if not datei or not datei.get("data"):
            fehlerseite("No CSV file selected.")
            return
        try:
            text = decode_csv_bytes(datei["data"])
        except ValueError as exc:
            fehlerseite(str(exc))
            return

        entries, bands, csv_fehler = parse_catalog_csv(text)
        if csv_fehler:
            fehlerseite(f"The CSV could not be read - "
                        f"rows with problems: {len(csv_fehler)}", csv_fehler)
            return

        if modus == "append":
            if target not in _known_file_names():
                fehlerseite("Please select an existing target catalog file.")
                return
            try:
                path = _resolve_known_file(target)
            except ValueError:
                fehlerseite(f"invalid catalog file name {target!r}")
                return
            try:
                doc = _load_known_dict(path)
            except OSError:
                fehlerseite(f"catalog file {target!r} unreadable", status=404)
                return
            except ValueError as exc:
                fehlerseite(f"catalog file unreadable: {exc}")
                return
            if not isinstance(doc.get("entries"), list):
                doc["entries"] = []
            if not isinstance(doc.get("bands"), list):
                doc["bands"] = []
            raw_file = target
        else:
            raw_file = new_name
            try:
                path = _resolve_known_file(raw_file)
            except ValueError:
                fehlerseite("invalid catalog file name (allowed: A-Z a-z 0-9 . _ -, "
                            "must end with .json)")
                return
            if os.path.isfile(path):
                fehlerseite(f"catalog file {raw_file!r} already exists - "
                            f"append to it as target instead of creating it")
                return
            try:
                doc = build_file_head({
                    "service": service, "demod": demod, "bw_hz": bw_raw,
                    "squelch_dbuv": squelch_raw,
                })
            except ValueError as exc:
                fehlerseite(str(exc))
                return

        kollisionen: list[str] = []
        for entry in entries:
            idx = _find_channel_entry(doc, entry["id"])
            if idx is None:
                doc["entries"].append(entry)
            elif overwrite:
                # Schluessel ohne CSV-Spalte aus dem bestehenden Eintrag
                # mitnehmen (T-122) - sonst loescht ein Re-Import der
                # ORS-Listen deren Standort- und Leistungsangaben.
                doc["entries"][idx] = _csv_merge_bestand(
                    entry, doc["entries"][idx], _CSV_CHANNEL_KEYS)
            else:
                kollisionen.append(f"channel '{entry['id']}' already exists in "
                                   f"{raw_file}")
        for band in bands:
            idx = _find_band_entry(doc, band["id"])
            if idx is None:
                doc["bands"].append(band)
            elif overwrite:
                doc["bands"][idx] = _csv_merge_bestand(
                    band, doc["bands"][idx], _CSV_BAND_KEYS)
            else:
                kollisionen.append(f"band '{band['id']}' already exists in "
                                   f"{raw_file}")
        if kollisionen:
            fehlerseite("The check against the target catalog file found "
                        "the following:", kollisionen)
            return

        fehlerliste = validate_candidate(raw_file, doc)
        if fehlerliste:
            fehlerseite("The check against the whole catalog found "
                        "the following:", fehlerliste)
            return

        _write_known_file(path, doc)
        self._redirect("/?open=" + quote(raw_file))

    def _handle_known_marker_import_form(self, params: dict) -> None:
        """GET-Maske: leeres Formular, Textfeld optional aus ?data= vorbelegt
        (verstecktes Feld/Link aus dem Wasserfall, T-506 - hier nur der
        Empfangsweg, kein Aufrufer)."""
        try:
            file_names = sorted(n for n in os.listdir(DEFAULT_CATALOG_DIR) if n.endswith(".json"))
        except OSError:
            file_names = []
        self._send_html(render_marker_import_page(
            data=params.get("data", [""])[0], target="", new_name=_marker_default_filename(),
            demod="", bw_hz="", squelch_dbuv="",
            rows=[], parse_errors=[], file_names=file_names,
        ))

    def _handle_known_marker_import_save(self) -> None:
        """
        POST fuer die Marker-Import-Maske: EIN Formular, drei Aktionen
        (analog _handle_known_raster_save). "preview" parst data IMMER
        frisch und schlaegt Demod/Bandbreite/Squelch aus dem ersten Band der
        ersten Frequenz vor (nur wenn diese Felder noch leer sind).

        "save" und "job" bauen beide zuerst die Vorschauzeilen aus den
        row_i-Feldern (editierte Frequenz ist massgeblich) und werten den
        Haken je Zeile seit T-1016 als Job-Auswahl, nicht als Katalog-
        Uebernahme: selected = ausgewaehlte Zeilen, neufunde = davon jene
        ohne Duplikattreffer (oder mit gesetztem Zweithaken neu_i). Nur
        neufunde werden als Kanaele in die Zieldatei geschrieben
        (demod/bw_hz/squelch_dbuv als defaults im Dateikopf, id-basiertes
        Update bei wiederholtem Import derselben Frequenz, ueber
        validate_candidate/_write_known_file wie die uebrigen Editoren) -
        bei "save" ist eine leere neufunde-Liste ein Fehler (nichts zu
        importieren), bei "job" nicht (dann werden ausschliesslich
        bestehende Kanal-IDs uebergeben). "job" schickt anschliessend die
        IDs aller selected (Duplikat-IDs oder frisch gebildete MK-IDs, ohne
        Wiederholungen) an render_marker_handoff_page zur Uebergabe an
        :8050; "save" bleibt beim bisherigen Verhalten (Redirect auf die
        Zieldatei).
        """
        form = self._read_form_body()
        action = form.get("action", "preview")
        data_txt = form.get("data", "")
        target = form.get("target", "")
        new_name = form.get("new_name", "").strip() or _marker_default_filename()
        demod = form.get("demod", "").strip()
        bw_raw = form.get("bw_hz", "").strip()
        squelch_raw = form.get("squelch_dbuv", "").strip()

        try:
            file_names = sorted(n for n in os.listdir(DEFAULT_CATALOG_DIR) if n.endswith(".json"))
        except OSError:
            file_names = []
        cat = load_catalog(DEFAULT_CATALOG_DIR)

        def rerender(rows: list[dict], parse_errors: list[str],
                     error: Optional[str] = None, status: int = 200) -> None:
            _marker_annotate_dup(cat, rows, _as_bw_int(bw_raw))
            self._send_html(render_marker_import_page(
                data=data_txt, target=target, new_name=new_name,
                demod=demod, bw_hz=bw_raw, squelch_dbuv=squelch_raw,
                rows=rows, parse_errors=parse_errors, file_names=file_names, error=error,
            ), status=status)

        if action in ("save", "job"):
            try:
                row_count = int(form.get("row_count", "0"))
            except ValueError:
                row_count = 0
            if row_count <= 0:
                rerender([], [], error="Please create a preview first.")
                return

            rows = _marker_rows_from_form(form, row_count)
            selected = [r for r in rows if r["checked"]]

            if not selected:
                rerender(rows, [], error="No row selected.")
                return

            for row in selected:
                if not (FREQ_MIN_HZ <= row["freq_hz"] <= FREQ_MAX_HZ):
                    fehler = (f"line {row['idx'] + 1}: frequency must be between "
                              f"{FREQ_MIN_HZ} and {FREQ_MAX_HZ} Hz")
                    rerender(rows, [], error=fehler, status=400)
                    return

            # Bandbreite ist fuer BEIDE Aktionen Pflicht - sie bestimmt die
            # Toleranz der Duplikatpruefung (marker_duplicate_of). Bei
            # fehlender Bandbreite waere die Toleranz 0, es wuerde kein
            # einziges Duplikat erkannt und jede Zeile landete als neuer
            # Kanal im Katalog - genau der Zustand, den T-1016 beseitigt. In
            # KEINEM Fall weiterrechnen, wenn diese Pruefung fehlschlaegt.
            bw_hz_int = _as_bw_int(bw_raw)
            if bw_hz_int not in BW_STAGES_HZ:
                fehler = (f"bandwidth is not an EB200 filter step "
                          f"({', '.join(str(b) for b in BW_STAGES_HZ)})")
                rerender(rows, [], error=fehler, status=400)
                return

            # rows aus _marker_rows_from_form tragen noch kein "dup", und die
            # Frequenz kann seit der Vorschau von Hand geaendert worden sein.
            # selected referenziert dieselben dict-Objekte wie rows, "dup"
            # steht damit auch dort.
            _marker_annotate_dup(cat, rows, bw_hz_int)
            neufunde = [r for r in selected if r["dup"] is None or r.get("neu")]

            raw_file = None
            if neufunde:
                fehler: Optional[str] = None
                if demod not in DEMOD_TOKENS:
                    fehler = f"demodulation is missing or unknown (allowed: {', '.join(DEMOD_TOKENS)})"

                squelch_val: Optional[float] = None
                if fehler is None and squelch_raw:
                    try:
                        squelch_val = float(squelch_raw.replace(",", ""))
                    except ValueError:
                        fehler = "squelch is not a number"

                is_new_file = target == ""
                if fehler is None and not is_new_file and target not in file_names:
                    fehler = f"target catalog file {target!r} not found"
                raw_file = new_name if is_new_file else target

                path = None
                if fehler is None:
                    try:
                        path = _resolve_known_file(raw_file)
                    except ValueError:
                        fehler = f"invalid catalog file name {raw_file!r}"

                if fehler is None and is_new_file and os.path.isfile(path):
                    fehler = (f"catalog file {raw_file!r} already exists - select it as target "
                              f"instead of creating it")

                if fehler:
                    rerender(rows, [], error=fehler, status=400)
                    return

                if is_new_file:
                    doc = {"meta": {"service": "Marker"}, "defaults": {}, "bands": [], "entries": []}
                else:
                    try:
                        doc = _load_known_dict(path)
                    except OSError:
                        doc = {"meta": {"service": "Marker"}, "defaults": {}, "bands": [], "entries": []}
                    except ValueError as exc:
                        rerender(rows, [], error=f"catalog file unreadable: {exc}", status=400)
                        return
                    if not isinstance(doc.get("entries"), list):
                        doc["entries"] = []
                    if not isinstance(doc.get("defaults"), dict):
                        doc["defaults"] = {}

                defaults = doc["defaults"]
                defaults["demod"] = demod
                defaults["bw_hz"] = bw_hz_int
                if squelch_val is not None:
                    defaults["squelch_dbuv"] = squelch_val
                else:
                    defaults.pop("squelch_dbuv", None)

                for row in neufunde:
                    entry = build_marker_channel_entry(row["freq_hz"], row["time"], row["level_dbuv"])
                    idx = _find_channel_entry(doc, entry["id"])
                    if idx is None:
                        doc["entries"].append(entry)
                    else:
                        doc["entries"][idx] = entry

                fehler_liste = validate_candidate(raw_file, doc)
                if fehler_liste:
                    rerender(rows, [], error="validation failed: " + "; ".join(fehler_liste),
                             status=400)
                    return

                _write_known_file(path, doc)

            if action == "save":
                if not neufunde:
                    rerender(rows, [], error="No new channels - all selected "
                                              "rows are already in the catalog.")
                    return
                self._redirect("/?open=" + quote(raw_file))
                return

            # action == "job": Kanal-IDs in der Reihenfolge von selected
            # bilden - Trefferzeile ohne Zweithaken liefert die bestehende
            # ID, alles andere (Neufund oder Treffer MIT Zweithaken) eine
            # frisch gebildete MK-ID. Duplikate entfernen, Reihenfolge
            # erhalten.
            ids: list[str] = []
            for row in selected:
                dup = row.get("dup")
                cid = dup["id"] if (dup and not row.get("neu")) else marker_channel_id(row["freq_hz"])
                if cid not in ids:
                    ids.append(cid)
            self._send_html(render_marker_handoff_page(ids))
            return

        # action == "preview" (oder unbekannt/erstmalig): Rohtabelle frisch parsen.
        parsed_rows, parse_errors = parse_marker_table(data_txt)
        rows = [{"idx": i, "time": r["time"], "freq_hz": r["freq_hz"], "level_dbuv": r["level_dbuv"]}
                for i, r in enumerate(parsed_rows)]
        if rows and not demod and not bw_raw and not squelch_raw:
            vorschlag = _marker_prefill_defaults(cat, rows[0]["freq_hz"])
            demod, bw_raw, squelch_raw = (vorschlag["demod"], vorschlag["bw_hz"],
                                           vorschlag["squelch_dbuv"])
        _marker_annotate_dup(cat, rows, _as_bw_int(bw_raw))
        for row in rows:
            # seit T-1016 ist der Haken die Job-Auswahl - Treffer der
            # Duplikatpruefung gehoeren genauso in den Job wie Neufunde, sie
            # liefern nur eine andere Kanal-ID.
            row["checked"] = True
        self._send_html(render_marker_import_page(
            data=data_txt, target=target, new_name=new_name,
            demod=demod, bw_hz=bw_raw, squelch_dbuv=squelch_raw,
            rows=rows, parse_errors=parse_errors, file_names=file_names,
        ))


from eb200_core.http_base import QuietThreadingHTTPServer as ThreadingHTTPServer


# ---------------------------------------------------------------------------
# T-1015: Marker-Import - Selbsttest (Parser, ID-Bildung, Duplikaterkennung,
# Eintragsbau; ohne Browser/Server, siehe eb200_core/known_freq.py-Vorbild)
# ---------------------------------------------------------------------------

def _self_test_marker_import() -> None:
    # -- Parser: Tab mit Kopfzeile, Semikolon ohne Kopfzeile --
    text = "Zeit\tMHz\tdBuV\n12:34:56\t118.5000\t42.3\n12:35:01\t118.5010\t39.0\n"
    rows, errors = parse_marker_table(text)
    assert not errors, errors
    assert len(rows) == 2, rows
    assert rows[0]["freq_hz"] == 118_500_000, rows[0]
    assert rows[1]["freq_hz"] == 118_501_000, rows[1]
    assert rows[0]["time"] == "12:34:56"
    assert abs(rows[0]["level_dbuv"] - 42.3) < 1e-9
    assert abs(rows[1]["level_dbuv"] - 39.0) < 1e-9

    rows2, errors2 = parse_marker_table("13:00:00;100.1;10.0")
    assert not errors2 and len(rows2) == 1, (rows2, errors2)
    assert rows2[0]["freq_hz"] == 100_100_000

    # -- T-1803a: Komma ist Tausendertrennzeichen und wird entfernt --
    rows5, errors5 = parse_marker_table("14:00:00\t1,234.5\t5.0")
    assert not errors5 and len(rows5) == 1, (rows5, errors5)
    assert rows5[0]["freq_hz"] == 1_234_500_000, rows5[0]

    # kaputte Zeile wird gemeldet, gueltige Zeilen bleiben erhalten
    rows3, errors3 = parse_marker_table(
        "12:00:00\t118.5\t1.0\nkaputte Zeile\n12:01:00\t118.6\t2.0")
    assert len(rows3) == 2 and len(errors3) == 1, (rows3, errors3)

    # leere dBuV-Spalte -> level_dbuv None statt Fehler
    rows4, errors4 = parse_marker_table("12:00:00\t118.5\t")
    assert not errors4 and rows4[0]["level_dbuv"] is None, (rows4, errors4)

    # -- ID-/Label-Bildung --------------------------------------------------
    assert marker_channel_id(118_500_000) == "MK-118.5000"
    assert marker_channel_label(118_500_000) == "MK 118.5000 MHz"
    # < 100 Hz Unterschied rundet auf dieselbe 4-Dezimalstellen-id (kein
    # Kollisionsrisiko in der Praxis, siehe T-1015-Begruendung)
    assert marker_channel_id(118_500_010) == marker_channel_id(118_500_040)

    # -- Duplikaterkennung (synthetischer Katalog in einem Temp-Verzeichnis) --
    with tempfile.TemporaryDirectory() as tmp:
        bestand = {
            "meta": {"service": "Test"}, "defaults": {"demod": "AM", "bw_hz": 9000},
            "bands": [], "entries": [
                {"kind": "channel", "id": "x1", "label": "X1", "freq_hz": 118_500_000},
            ],
        }
        with open(os.path.join(tmp, "a.json"), "w", encoding="utf-8") as fh:
            json.dump(bestand, fh)
        cat = load_catalog(tmp)
        assert not cat.errors, cat.errors

        treffer = marker_duplicate_of(cat, 118_500_050, 9000)      # 50 Hz < 4500 Hz Toleranz
        assert treffer is not None and treffer["id"] == "x1", treffer
        kein_treffer = marker_duplicate_of(cat, 130_000_000, 9000)
        assert kein_treffer is None, kein_treffer
        # ausserhalb der Toleranz (bw_hz/2 = 75 Hz), 50 kHz entfernt
        weit_weg = marker_duplicate_of(cat, 118_550_000, 150)
        assert weit_weg is None, weit_weg
        # bw_hz<=0 (noch nicht gewaehlt) prueft nur exakte Treffer
        exakt = marker_duplicate_of(cat, 118_500_000, 0)
        assert exakt is not None and exakt["id"] == "x1"
        assert marker_duplicate_of(cat, 118_500_050, 0) is None

    # -- Eintragsbau ----------------------------------------------------------
    entry = build_marker_channel_entry(118_500_000, "12:34:56", 42.3)
    assert entry["kind"] == "channel"
    assert entry["id"] == "MK-118.5000"
    assert entry["label"] == "MK 118.5000 MHz"
    assert entry["freq_hz"] == 118_500_000
    assert entry["tags"] == ["marker"]
    assert "12:34:56" in entry["notes"] and "42.3" in entry["notes"], entry["notes"]
    assert "demod" not in entry and "bw_hz" not in entry     # erben aus defaults

    entry_ohne_pegel = build_marker_channel_entry(100_000_000, "", None)
    assert entry_ohne_pegel["notes"] == "Auto marker"

    # -- S8: _marker_rows_from_form liest neu_i korrekt (an/aus/fehlend) ----
    form_s8 = {
        "freq_0": "118.5000", "neu_0": "on",
        "freq_1": "118.6000", "neu_1": "off",
        "freq_2": "118.7000",
    }
    rows_s8 = _marker_rows_from_form(form_s8, 3)
    assert rows_s8[0]["neu"] is True, rows_s8[0]
    assert rows_s8[1]["neu"] is False, rows_s8[1]
    assert rows_s8[2]["neu"] is False, rows_s8[2]

    # -- S9: Aufteilung selected/neufunde ------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        bestand_s9 = {
            "meta": {"service": "Test"}, "defaults": {"demod": "AM", "bw_hz": 9000},
            "bands": [], "entries": [
                {"kind": "channel", "id": "y1", "label": "Y1", "freq_hz": 125_680_000},
            ],
        }
        with open(os.path.join(tmp, "b.json"), "w", encoding="utf-8") as fh:
            json.dump(bestand_s9, fh)
        cat_s9 = load_catalog(tmp)
        assert not cat_s9.errors, cat_s9.errors

        rows_s9 = [
            {"idx": 0, "time": "", "freq_hz": 125_680_000, "level_dbuv": None, "checked": True, "neu": False},
            {"idx": 1, "time": "", "freq_hz": 132_062_500, "level_dbuv": None, "checked": True, "neu": False},
        ]
        _marker_annotate_dup(cat_s9, rows_s9, 15_000)
        selected_s9 = [r for r in rows_s9 if r["checked"]]
        neufunde_s9 = [r for r in selected_s9 if r["dup"] is None or r.get("neu")]
        assert len(neufunde_s9) == 1 and neufunde_s9[0]["freq_hz"] == 132_062_500, neufunde_s9

        # Zusatzfall: mit derselben (nicht exakten) Frequenz wuerde eine
        # Bandbreite von 0 die Duplikatpruefung nichts finden lassen (nur
        # exakte Treffer) - deshalb ist die Bandbreitenpruefung in
        # _handle_known_marker_import_save Pflicht (AENDERUNG 6 Schritt 3).
        rows_s9b = [{"idx": 0, "time": "", "freq_hz": 125_680_050, "level_dbuv": None,
                     "checked": True, "neu": False}]
        _marker_annotate_dup(cat_s9, rows_s9b, 15_000)
        assert rows_s9b[0]["dup"] is not None, rows_s9b[0]
        _marker_annotate_dup(cat_s9, rows_s9b, 0)
        assert rows_s9b[0]["dup"] is None, rows_s9b[0]

    # -- S10: ID-Bildung fuer den Job (Logik aus AENDERUNG 6 Schritt 8) ------
    dup_y1 = {"id": "y1", "label": "Y1", "file": "b.json"}
    rows_s10 = [
        {"idx": 0, "freq_hz": 125_680_000, "checked": True, "neu": False, "dup": dup_y1},
        {"idx": 1, "freq_hz": 125_680_000, "checked": True, "neu": True, "dup": dup_y1},
        {"idx": 2, "freq_hz": 132_062_500, "checked": True, "neu": False, "dup": None},
        {"idx": 3, "freq_hz": 125_680_000, "checked": True, "neu": False, "dup": dup_y1},
    ]
    ids_s10: list[str] = []
    for row in rows_s10:
        dup = row.get("dup")
        cid = dup["id"] if (dup and not row.get("neu")) else marker_channel_id(row["freq_hz"])
        if cid not in ids_s10:
            ids_s10.append(cid)
    assert ids_s10 == ["y1", "MK-125.6800", "MK-132.0625"], ids_s10

    # -- S11: Uebergabeseite --------------------------------------------------
    handoff_html = render_marker_handoff_page(["a", "b"])
    assert 'name="ids"' in handoff_html
    assert 'value="a,b"' in handoff_html
    assert ":8050/mscan/import" in handoff_html

    # -- S12: _marker_row_html nach T-1016c (kompakte Trefferanzeige, Zeit-
    # Spalte entfallen, verstecktes Feld time_ bleibt erhalten) ------------
    row_treffer = {
        "idx": 0, "checked": True, "time": "12:34:56", "freq_hz": 128_315_000,
        "level_dbuv": 42.3, "neu": False,
        "dup": {"id": "air-128.315", "file": "Air.json", "freq_hz": 128_315_000},
    }
    row_html_treffer = _marker_row_html(row_treffer)
    assert 'class="dup-file"' in row_html_treffer, row_html_treffer
    assert 'name="time_' in row_html_treffer, row_html_treffer

    row_neufund = {
        "idx": 1, "checked": True, "time": "12:35:00", "freq_hz": 130_000_000,
        "level_dbuv": None, "neu": False, "dup": None,
    }
    row_html_neufund = _marker_row_html(row_neufund)
    assert "new: MK-" in row_html_neufund, row_html_neufund

    print("[block_admin] Selbsttest Marker-Import OK")


# ---------------------------------------------------------------------------
# T-121: CSV-Import - Selbsttest (Vorlage, Zerlegung, Multipart; ohne
# Browser/Server, Vorbild _self_test_marker_import)
# ---------------------------------------------------------------------------

def _self_test_csv_import() -> None:
    # -- C1: Export einer leeren Datei ist die Kopfzeile allein (T-123) und
    # selbst kein gueltiger Import -------------------------------------------
    vorlage = catalog_csv_text({"meta": {"service": "Leer"},
                                "defaults": {"demod": "AM"}, "bands": [],
                                "entries": []})
    assert vorlage == CSV_DELIM.join(CSV_COLUMNS) + "\r\n", vorlage
    e_v, b_v, f_v = parse_catalog_csv(vorlage)
    assert e_v == [] and b_v == [], (e_v, b_v)
    assert f_v and "no data rows" in f_v[0], f_v

    # -- C2: gueltige CSV mit Kanal und Band --------------------------------
    gut = (
        "typ;id;label;freq_hz;start_hz;stop_hz;title;demod;bw_hz;"
        "squelch_dbuv;antenna;tags;notes;enabled\r\n"
        "channel;t-1;Eins;118500000;;;;AM;9000;10;discone;land-at,test;Notiz;1\r\n"
        "channel;t-2;Zwei;121 500 000;;;;;;;;;;0\r\n"
        "band;t-b;;;118000000;137000000;Testband;;;;;;;\r\n"
    )
    entries, bands, fehler = parse_catalog_csv(gut)
    assert not fehler, fehler
    assert len(entries) == 2 and len(bands) == 1, (entries, bands)
    assert entries[0]["freq_hz"] == 118_500_000, entries[0]
    assert entries[0]["demod"] == "AM" and entries[0]["bw_hz"] == 9000, entries[0]
    assert entries[0]["tags"] == ["land-at", "test"], entries[0]
    assert "enabled" not in entries[0], entries[0]
    # Leerzeichen als Tausendertrennung sind erlaubt, geerbte Felder bleiben weg
    assert entries[1]["freq_hz"] == 121_500_000, entries[1]
    assert entries[1]["enabled"] is False, entries[1]
    assert "demod" not in entries[1] and "bw_hz" not in entries[1], entries[1]
    assert bands[0]["start_hz"] == 118_000_000, bands[0]
    assert bands[0]["title"] == "Testband", bands[0]

    # -- C3: Zeilenfehler -> Eintrag faellt, Meldung nennt die Zeilennummer --
    schlecht = (
        "typ;id;label;freq_hz;start_hz;stop_hz;title;demod;bw_hz\r\n"
        "channel;s-1;Punkt;118.5;;;;AM;9000\r\n"
        "channel;s-2;Bandspalte;118500000;118000000;;;AM;9000\r\n"
        "channel;s-3;Demod;118600000;;;;NFM;9000\r\n"
        "band;s-4;;;137000000;118000000;Rueckwaerts;;\r\n"
        "kanal;s-5;Typ;118700000;;;;AM;9000\r\n"
        "channel;s-1;Doppelt;118800000;;;;AM;9000\r\n"
    )
    e_s, b_s, f_s = parse_catalog_csv(schlecht)
    assert e_s == [] and b_s == [], (e_s, b_s)
    assert len(f_s) == 6, f_s
    assert f_s[0].startswith("line 2:") and "is not an integer" in f_s[0], f_s[0]
    assert "column start_hz must be empty" in f_s[1], f_s[1]
    assert "NFM" in f_s[2], f_s[2]
    assert f_s[3].startswith("line 5:"), f_s[3]
    assert "'kanal'" in f_s[4], f_s[4]
    assert "more than once" in f_s[5], f_s[5]

    # unbekannte Spalte in der Kopfzeile
    _, _, f_k = parse_catalog_csv("typ;id;quatsch\r\nchannel;k-1;x\r\n")
    assert any("quatsch" in z for z in f_k), f_k

    # -- C4: Dekodierung ----------------------------------------------------
    assert decode_csv_bytes("typ;id\r\n".encode("utf-8-sig")).startswith("typ")
    assert decode_csv_bytes("Gruen;ue".encode("cp1252")) == "Gruen;ue"
    assert decode_csv_bytes("Grün;ü".encode("cp1252")) == "Grün;ü"

    # -- C5: multipart-Zerlegung -------------------------------------------
    grenze = "----MoniScopeTest"
    rumpf = (
        f"--{grenze}\r\n"
        'Content-Disposition: form-data; name="modus"\r\n\r\n'
        "append\r\n"
        f"--{grenze}\r\n"
        'Content-Disposition: form-data; name="csvdatei"; filename="a.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
        "typ;id\r\nchannel;m-1\r\n"
        f"\r\n--{grenze}--\r\n"
    ).encode("utf-8")
    felder, dateien = parse_multipart_form(
        rumpf, f"multipart/form-data; boundary={grenze}")
    assert felder["modus"] == "append", felder
    assert dateien["csvdatei"]["filename"] == "a.csv", dateien
    # Das Zeilenende der letzten CSV-Zeile MUSS erhalten bleiben (siehe
    # Kommentar in parse_multipart_form), und weder Praeambel noch
    # Abschlusstrenner duerfen ein zusaetzliches Feld erzeugen.
    assert dateien["csvdatei"]["data"] == b"typ;id\r\nchannel;m-1\r\n", dateien
    assert list(felder) == ["modus"], felder
    assert list(dateien) == ["csvdatei"], dateien

    for falsch, grund in (("application/x-www-form-urlencoded", "multipart"),
                          ("multipart/form-data", "boundary")):
        try:
            parse_multipart_form(b"", falsch)
            raise AssertionError(f"{grund} nicht abgelehnt")
        except ValueError:
            pass

    # -- C6: Formular/Seite -------------------------------------------------
    seite = render_csv_import_page(file_names=["a.json"], modus="append",
                                   target="a.json")
    assert 'enctype="multipart/form-data"' in seite, seite[:200]
    assert 'action="/known/csv-import/save"' in seite
    assert '<option value="a.json" selected>a.json</option>' in seite
    assert 'href="/known/csv-export"' in seite

    export_seite = _csv_export_form_body(file_names=["a.json", "b.json"])
    assert 'action="/known/csv-export"' in export_seite, export_seite
    assert '<option value="b.json">b.json</option>' in export_seite
    assert "No catalog file yet" in _csv_export_form_body(file_names=[])

    # -- C7: Export (T-122) --------------------------------------------------
    doc_x = {
        "meta": {"service": "X-Dienst"},
        "defaults": {"demod": "FM", "bw_hz": 15000},
        "bands": [{"id": "x-band", "title": "X-Band", "start_hz": 118_000_000,
                   "stop_hz": 137_000_000,
                   "segments": [{"title": "S", "start_hz": 118_000_000,
                                 "stop_hz": 119_000_000}]}],
        "entries": [
            {"kind": "channel", "id": "x-1", "label": "Eins",
             "freq_hz": 118_500_000, "tags": ["land-at", "test"],
             "site": "ABTENAU", "erp_w": 100},
            {"kind": "channel", "id": "x-2", "label": "Zwei",
             "freq_hz": 119_000_000, "demod": "AM", "bw_hz": 9000,
             "squelch_dbuv": 10.0, "enabled": False},
            {"kind": "raster", "id_prefix": "x-r", "start_hz": 120_000_000,
             "stop_hz": 121_000_000, "step_hz": 25_000, "enabled": False},
        ],
    }
    text_x = catalog_csv_text(doc_x)

    # T-123: keine Kommentarzeile mehr - weder Herkunft/Dienst/defaults noch
    # der Raster- oder Zusatzfeldhinweis. Beides steht jetzt im Formular.
    assert "#" not in text_x, text_x
    for feld in ("erp_w", "segments", "X-Dienst"):
        assert feld not in text_x, (feld, text_x)

    # Datenzeilen: Reihenfolge Band, dann Kanaele; Werte wie in der Datei
    zeilen_x = [z for z in text_x.split("\r\n") if z]
    assert zeilen_x[0] == CSV_DELIM.join(CSV_COLUMNS), zeilen_x[0]
    assert zeilen_x[1].startswith("band;x-band;;;118000000;137000000;X-Band;"), zeilen_x[1]
    assert zeilen_x[2] == "channel;x-1;Eins;118500000;;;;;;;;land-at,test;;1", zeilen_x[2]
    assert zeilen_x[3] == "channel;x-2;Zwei;119000000;;;;AM;9000;10;;;;0", zeilen_x[3]
    assert len(zeilen_x) == 4, zeilen_x

    # Rueckweg: der Export ist ein gueltiger Import und bringt dieselben Werte
    e_x, b_x, f_x = parse_catalog_csv(text_x)
    assert not f_x, f_x
    assert len(e_x) == 2 and len(b_x) == 1, (e_x, b_x)
    assert e_x[0]["id"] == "x-1" and e_x[0]["freq_hz"] == 118_500_000, e_x[0]
    assert e_x[0]["tags"] == ["land-at", "test"], e_x[0]
    assert "demod" not in e_x[0] and "bw_hz" not in e_x[0], e_x[0]
    assert e_x[1]["enabled"] is False and e_x[1]["squelch_dbuv"] == 10.0, e_x[1]
    assert b_x[0]["start_hz"] == 118_000_000, b_x[0]

    # Zusatzfelder ohne Spalte ueberleben das Ersetzen, Spaltenfelder nicht
    verschmolzen = _csv_merge_bestand(dict(e_x[0]), doc_x["entries"][0],
                                      _CSV_CHANNEL_KEYS)
    assert verschmolzen["site"] == "ABTENAU" and verschmolzen["erp_w"] == 100, verschmolzen
    ohne_notiz = _csv_merge_bestand({"kind": "channel", "id": "x-1"},
                                    {"kind": "channel", "id": "x-1",
                                     "notes": "alt", "site": "ABTENAU"},
                                    _CSV_CHANNEL_KEYS)
    assert "notes" not in ohne_notiz, ohne_notiz      # leeres Feld = geerbt
    assert ohne_notiz["site"] == "ABTENAU", ohne_notiz
    band_verschmolzen = _csv_merge_bestand(dict(b_x[0]), doc_x["bands"][0],
                                           _CSV_BAND_KEYS)
    assert band_verschmolzen["segments"], band_verschmolzen

    # -- C8: Trennzeichen der Kopfzeile (T-123) ------------------------------
    # Befund aus der Browser-Validierung: LibreOffice/Excel speichern eine
    # geoeffnete CSV mit anderem Trennzeichen zurueck, als sie sie gelesen
    # haben - der Re-Import scheiterte daran mit "Spalte 'typ' fehlt".
    assert _csv_delimiter_of("typ;id;label\r\n") == ";"
    assert _csv_delimiter_of("typ,id,label\r\n") == ","
    assert _csv_delimiter_of("typ\tid\tlabel\r\n") == "\t"
    assert _csv_delimiter_of('"typ";"id";"label"\r\n') == ";"
    # Kommentarzeilen und Leerzeilen zaehlen nicht als Kopfzeile
    assert _csv_delimiter_of("# eine, Zeile, mit, Kommata\r\n\r\ntyp;id\r\n") == ";"
    assert _csv_delimiter_of("") == ";"

    komma = ("typ,id,label,freq_hz,start_hz,stop_hz,title,demod,bw_hz\r\n"
             "channel,k-1,Komma,118500000,,,,AM,9000\r\n"
             "band,k-b,,,118000000,119000000,Kommaband,,\r\n")
    e_k, b_k, f_k2 = parse_catalog_csv(komma)
    assert not f_k2, f_k2
    assert len(e_k) == 1 and len(b_k) == 1, (e_k, b_k)
    assert e_k[0]["freq_hz"] == 118_500_000 and e_k[0]["demod"] == "AM", e_k[0]
    assert b_k[0]["title"] == "Kommaband", b_k[0]

    tab = "typ\tid\tlabel\tfreq_hz\r\nchannel\tk-2\tTabulator\t119000000\r\n"
    e_t, _b_t, f_t = parse_catalog_csv(tab)
    assert not f_t and e_t[0]["id"] == "k-2", (e_t, f_t)

    # Der eigene Export bleibt mit Semikolon lesbar
    e_r, b_r, f_r = parse_catalog_csv(catalog_csv_text(doc_x))
    assert not f_r and len(e_r) == 2 and len(b_r) == 1, (e_r, b_r, f_r)

    # -- C9: Dateiname-Ergaenzung und Fehlerbericht (T-124) ------------------
    assert _csv_zieldateiname("OE3GAS_2") == "OE3GAS_2.json"
    assert _csv_zieldateiname("  OE3GAS_2  ") == "OE3GAS_2.json"
    assert _csv_zieldateiname("a.json") == "a.json"
    assert _csv_zieldateiname("a.JSON") == "a.JSON"      # Endung bleibt, wie sie ist
    assert _csv_zieldateiname("") == ""
    assert _csv_zieldateiname("   ") == ""

    bericht = _csv_bericht_html(
        "Der Abgleich mit dem gesamten Katalog hat Folgendes ergeben:",
        ["OE3GAS_2.json: id 'HF-APRS' already used in OE3GAS.json - "
         "entry from OE3GAS_2.json discarded",
         "channel 'oe3gas_HL_HausQRG' already exists in OE3GAS.json",
         "band 'b-1' already exists in Test.json",
         "etwas ganz anderes"],
        "OE3GAS_2.json", True)
    assert "was NOT created" in bericht, bericht
    assert "OE3GAS_2.json" in bericht, bericht
    assert "<td>HF-APRS</td><td>OE3GAS.json</td>" in bericht, bericht
    assert "<td>oe3gas_HL_HausQRG</td><td>OE3GAS.json</td>" in bericht, bericht
    assert "<td>b-1</td><td>Test.json</td>" in bericht, bericht
    assert "<li>etwas ganz anderes</li>" in bericht, bericht
    assert "The following IDs are already in use:" in bericht, bericht
    assert 'class="error"' not in bericht, bericht

    # Anhaengen: andere Kopfzeile, kein "angelegt"
    bericht_a = _csv_bericht_html("", ["channel 'x' already exists in Z.json"],
                                  "Z.json", False)
    assert "Z.json is unchanged" in bericht_a, bericht_a
    assert "created" not in bericht_a, bericht_a

    # Ohne Zeilen bleibt der Kopfsatz - er ist die eigentliche Aussage
    assert "bericht-kopf" in _csv_bericht_html("", [], "", True)

    print("[block_admin] Selbsttest CSV-Import OK")


# ---------------------------------------------------------------------------
# T-1803a: kHz-Anzeige/Eingabe - Selbsttest (Vertrag zwischen _format_khz und
# dem JS-Parser khzDisplayToHz, siehe dortige Docstrings)
# ---------------------------------------------------------------------------

def _self_test_khz_format() -> None:
    # -- F1 bis F4: _format_khz() einzeln --
    assert _format_khz(12_345_678) == "12,345.678", _format_khz(12_345_678)
    assert _format_khz(1_000) == "1", _format_khz(1_000)
    assert _format_khz(1_500) == "1.5", _format_khz(1_500)
    assert _format_khz(1_234_567_890) == "1,234,567.89", _format_khz(1_234_567_890)

    # -- F5: Rundlauf - Anzeige gegen die Python-Entsprechung des JS-Parsers --
    for hz in (1, 1_000, 12_345_678, 1_234_567_890):
        text = _format_khz(hz)
        zurueck = round(float(text.replace(",", "")) * 1000)
        assert zurueck == hz, (hz, text, zurueck)

    print("[block_admin] Selbsttest kHz-Format OK")


# ---------------------------------------------------------------------------
# T-1020: Pflichtfeldpruefung Demod/BW (mit Vererbung) und Dateiendung .json
# ---------------------------------------------------------------------------

def _self_test_pflichtfelder() -> None:
    basis_form = {"id": "t-1", "label": "Testkanal", "freq_hz": "118525000"}

    # -- P1: Demodulation nur in den Datei-defaults - kein Fehler --
    form_p1 = dict(basis_form, demod="", bw_hz="")
    entry_p1 = build_channel_entry(form_p1, defaults={"demod": "AM", "bw_hz": 6000})
    assert "demod" not in entry_p1, entry_p1

    # -- P2: Demodulation an keiner Stelle - ValueError, Meldung nennt sie --
    form_p2 = dict(basis_form, demod="", bw_hz="")
    try:
        build_channel_entry(form_p2, defaults={"bw_hz": 6000})
        assert False, "P2: ValueError erwartet"
    except ValueError as exc:
        assert "demodulation" in str(exc), exc

    # -- P3: Regressionstest VIE_info (Air.json) - Demod/BW komplett geerbt --
    form_p3 = dict(id="VIE_info", label="Wien Information - VFR/TFI",
                    freq_hz="118525000", demod="", bw_hz="")
    air_defaults = {"demod": "AM", "bw_hz": 6000, "squelch_dbuv": 20,
                     "antenna": "discone", "tags": "air,voice"}
    entry_p3 = build_channel_entry(form_p3, defaults=air_defaults)
    assert "demod" not in entry_p3 and "bw_hz" not in entry_p3, entry_p3

    # -- P4: Dateiname ohne Endung bekommt .json --
    assert _normalize_new_catalog_filename("Marker_2026") == "Marker_2026.json"

    # -- P5: .json-Endung wird auf Kleinbuchstaben normalisiert, Namensteil bleibt --
    assert _normalize_new_catalog_filename("Air.JSON") == "Air.json"

    # -- P6: andere Endung ist ein Fehler, Meldung nennt .json --
    try:
        _normalize_new_catalog_filename("Air.txt")
        assert False, "P6: ValueError erwartet"
    except ValueError as exc:
        assert ".json" in str(exc), exc

    # -- P7: Name endet auf einen Punkt - Fehler --
    try:
        _normalize_new_catalog_filename("Air.")
        assert False, "P7: ValueError erwartet"
    except ValueError:
        pass

    # -- P8: Bandbreite analog zu P1 und P2 - beide Felder sind so abgedeckt --
    form_p8 = dict(basis_form, demod="", bw_hz="")
    entry_p8a = build_channel_entry(form_p8, defaults={"demod": "AM", "bw_hz": 6000})
    assert "bw_hz" not in entry_p8a, entry_p8a
    try:
        build_channel_entry(form_p8, defaults={"demod": "AM"})
        assert False, "P8: ValueError erwartet"
    except ValueError as exc:
        assert "bandwidth" in str(exc), exc

    print("[block_admin] Selbsttest Pflichtfelder OK")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Frequenzkatalog-Pflegemaske")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren (ohne Server)")
    ap.add_argument("--data-dir", metavar="PFAD",
                    help="Datenwurzel (wertet eb200_core/paths.py beim Import aus, T-2003)")
    args = ap.parse_args()

    if args.selftest:
        _self_test_marker_import()
        _self_test_csv_import()
        _self_test_khz_format()
        _self_test_pflichtfelder()
        return 0

    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"[http] Frequenzkataloge verfügbar unter http://localhost:{HTTP_PORT}/")
    register_server("block_admin", HTTP_PORT)
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


if __name__ == "__main__":
    sys.exit(main())