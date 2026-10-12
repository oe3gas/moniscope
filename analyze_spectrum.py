#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
analyze_spectrum.py

Eigenstaendiges, rein LESENDES Auswerte-/Wiedergabewerkzeug fuer die IFPAN-
Bin-Aufnahmen aus eb200_core/spectrum_store.py (.specbin + .specbin.json,
Format "eb200-ifpan-bins-v1"). Durchsucht records/ (bzw. beliebige Pfade
innerhalb der Repo-Wurzel) im Browser und spielt eine Aufnahme wie ein
Videorekorder ab: Frame fuer Frame vor/zurueck, mit Pause und variabler
Geschwindigkeit, dazu eine Spektrogramm-Gesamtansicht, Peak-Hold und die
Ein-Bin-ueber-die-Zeit-Ansicht (Pegelverlauf einer festen Frequenz).

Layout: der Wasserfall steht links in einem HOEHENFESTEN, scrollbaren
Viewport (intern volle Aufloesung, mindestens 1 Pixelzeile je Frame), Spektrum
und Pegelverlauf stehen rechts daneben; der Viewport ist per Flexbox genauso
hoch wie die beiden rechten Anzeigen zusammen. Zeit-/Frequenz-/Bin-Auswahl geht
per Klick UND per Ziehen (Drag-Scrubbing) auf allen drei Canvas; Shift bleibt
fuer eine spaetere Bereichsmessung frei.

Bewusst analog zu analyze_sessions.py aufgebaut (gleicher Handler-Aufbau,
gleiche Pfad-Sandbox, gleiche base64-Level-Uebertragung, gleiches Farb-
mapping wie der Live-Wasserfall), aber fuer den anderen Datentyp: hier
Flatfile-Frames statt SQLite-Segmente, hier Transport statt Segment-Browsing.
Die Aufnahmen (.specbin + Sidecar) werden nie veraendert; geschrieben werden
nur eigene Metadaten-Dateien daneben (Kurztexte/Notizen via LabelStore,
regenerierbare Befund-Sidecars). Der Reader
(eb200_core/spectrum_reader.py) ist die einzige Abhaengigkeit ausserhalb der
Standardbibliothek-Nutzung.

Sicherheits-Hinweis: der Server bindet auf 0.0.0.0:8040 und ist damit im
gesamten Shack-LAN erreichbar. Jeder "path"-Query-Parameter MUSS deshalb
zwingend zuerst durch resolve_safe_path() laufen, bevor irgendein
Dateizugriff passiert - siehe dort. Nur Python-Standardbibliothek.

Schutzregeln fuer das Loeschen (Sandbox, Long-Run-Teilschutz, Live-Sperre)
liegen seit T-1104 zentral in eb200_core/recording_index.py - siehe
delete_recording().

Start (aus dem Repo-Root E:\\EB200\\EB200repo, damit die Sandbox-Wurzel und
records/ stimmen):
    python analyze_spectrum.py
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import signal
import subprocess
import sys
from array import array
from urllib.parse import parse_qs, urlparse

from eb200_core.spectrum_reader import SpectrumRecording, FORMAT_ID, FORMAT_ID_DSCAN
from eb200_core.job_store import load_storage_roots
from eb200_core.label_store import LabelStore, clean_note
from eb200_core.param_store import register_server
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
from eb200_core.known_freq import (
    load_catalog, load_catalog_cached, load_station_config, load_view_config,
)
from eb200_core.recording_index import (
    delete_recording as _delete_recording,
    RecordingLockedError as _RecLocked,
    RecordingNotDeletableError as _RecNotDeletable,
)
from eb200_core import paths
from eb200_core.path_sandbox import sandbox_roots, resolve_within, to_portable

# ---------------------------------------------------------------------------
# Konfiguration / Pfad-Sandbox (siehe Moduldoku - PFLICHT wegen LAN-Bindung)
# ---------------------------------------------------------------------------

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8040

# Wurzel der Sandbox: die Datenwurzel (T-2003, eb200_core/paths.py; ohne
# --data-dir das Repo-Root wie bisher). Kein path-Query-Parameter darf jemals
# ausserhalb davon bzw. der storage_roots aufgeloest werden (resolve_safe_path()).
SPECTRUM_ROOT = paths.DATA_ROOT
# T-1205: erster Eintrag aus storage_roots.json (Fallback ["records"],
# siehe eb200_core/job_store.load_storage_roots) - gleiche Regel wie
# waterfall.py. Einmalig beim Start ausgewertet.
RECORD_DIR = load_storage_roots()[0]

# Getrennter, schreibbarer Kurztext-Speicher (eb200_core/label_store.py). Die
# Aufnahmen (.specbin + Sidecar) bleiben rein lesend; die frei vergebene
# Bezeichnung liegt daneben in dieser JSON-Datei.
LABELS = LabelStore(os.path.join(SPECTRUM_ROOT, "spectrum_labels.json"))

# Freitext-Notizen je IFPAN-Aufnahme (mehrzeilig, getrennt vom Kurztext und
# vom REGENERIERBAREN Befund-Sidecar - eine Notiz im .befund.json waere bei
# der naechsten Versions-Regeneration verloren). Gleicher Schluessel wie LABELS.
NOTES = LabelStore(os.path.join(SPECTRUM_ROOT, "spectrum_notes.json"), cleaner=clean_note)

# Muss mit ANALYSIS_VERSION in session_analyze.py uebereinstimmen; Sidecars
# mit anderer/fehlender Version werden bei Zugriff neu erzeugt.
EXPECTED_ANALYSIS_VERSION = 3

# Deckel fuer /api/frames: die komplette Zeit×Bin-Matrix wird EINMAL an den
# Client geliefert, damit Scrubbing/Wiedergabe rein clientseitig und ohne
# Request-Sturm laufen (der O(1)-Seek des Readers bleibt fuer spaeteres
# Chunking/Einzel-Frame-Nachladen die Reserve). Groessere Aufnahmen werden
# nach MAX_FRAMES abgeschnitten (capped) statt den Browser zu ueberfahren.
MAX_FRAMES = 8000

ERR_SANDBOX = "access denied: path lies outside the permitted storage locations"
ERR_NOT_FOUND = "file not found"
ERR_INCOMPLETE = "recording incomplete (no sidecar - may still be running)"
ERR_INVALID_REC = "not a valid bin recording"
ERR_BAD_PARAMS = "invalid request parameters"
ERR_INTERNAL = "internal error"


class IncompleteRecordingError(Exception):
    """Die .specbin existiert, aber die Sidecar .specbin.json fehlt - die
    Aufnahme wurde nie sauber geschlossen (Absturz) oder laeuft gerade noch
    (der Writer schreibt die Sidecar erst in close())."""


class InvalidRecordingError(Exception):
    """Datei existiert samt Sidecar, ist aber keine gueltige Bin-Aufnahme
    (falsche Formatkennung, inkonsistente Geometrie o. ae.)."""


class RecordingLockedError(Exception):
    """Aufnahme laeuft noch oder ist unvollstaendig (Sidecar fehlt oder
    running:true) - siehe delete_recording()."""


class RecordingIsLongRunPartError(Exception):
    """Datei ist Teil eines Long-Run-Jobs (records/longrun_*/, job.json
    daneben) - siehe delete_recording()."""


def _rewrap(ziel_cls, exc):
    """Lokale Ausnahme aus einer Kernausnahme, unter Mitnahme von code/params (T-1802c2)."""
    neu = ziel_cls(str(exc))
    neu.code = getattr(exc, "code", None)
    neu.params = getattr(exc, "params", {}) or {}
    return neu


def resolve_safe_path(user_path: str) -> str:
    """
    Loest user_path (ein "path"-Query-Parameter) zu einem absoluten Pfad auf
    und stellt sicher, dass dieser innerhalb von SPECTRUM_ROOT ODER einem
    konfigurierten storage_root liegt und auf eine .specbin-Datei zeigt.
    MUSS bei JEDEM eingehenden path-Parameter als allererstes aufgerufen
    werden, bevor irgendein Dateizugriff passiert. Whitelist = Repo-Root +
    storage_roots, siehe eb200_core/path_sandbox.py.

    Wirft ValueError, wenn der Pfad leer ist, ausserhalb aller Roots liegt
    oder nicht auf .specbin endet (nur die Binaerdatei ist als path zulaessig;
    die Sidecar oeffnet der Reader selbst daneben). Der Handler uebersetzt
    ValueError IMMER in eine generische 403-Antwort, die den eingegebenen Pfad
    nicht wiederholt (kein Oracle ueber Pfade ausserhalb der Sandbox).
    """
    # sandbox_roots() liest storage_roots.json bei JEDEM Aufruf neu (Datei
    # winzig) - Aenderungen daran wirken so ohne Server-Neustart.
    return resolve_within(user_path, sandbox_roots(SPECTRUM_ROOT), suffixes=(".specbin",))


def to_relative(path: str) -> str:
    """Portables Pfadformat (siehe eb200_core.path_sandbox.to_portable):
    SPECTRUM_ROOT-relativ mit Vorwaertsschraegstrichen innerhalb des
    Repo-Roots, sonst absolut mit Vorwaertsschraegstrichen (externer
    storage_root) - so, wie ihn der Client unveraendert an /api/recording
    bzw. /api/frames zurueckreichen kann."""
    return to_portable(path, SPECTRUM_ROOT)


# ---------------------------------------------------------------------------
# Datenzugriff (read-only) - alles ueber den Reader
# ---------------------------------------------------------------------------

def open_recording(resolved_path: str) -> SpectrumRecording:
    """
    Oeffnet resolved_path als Bin-Aufnahme (der Reader oeffnet beide Dateien
    ausschliesslich lesend). Uebersetzt die Rohfehler in die drei Faelle, die
    der Handler in HTTP-Statuscodes abbildet - niemals einen Traceback an den
    Client durchreichen:
      - FileNotFoundError        : .specbin fehlt
      - IncompleteRecordingError : Sidecar .specbin.json fehlt (laeuft noch?)
      - InvalidRecordingError    : Format/Geometrie ungueltig
    Der Aufrufer ist fuer close() verantwortlich.
    """
    if not os.path.isfile(resolved_path):
        raise FileNotFoundError(resolved_path)
    if not os.path.isfile(resolved_path + ".json"):
        raise IncompleteRecordingError(resolved_path)
    try:
        return SpectrumRecording(resolved_path)
    except ValueError as exc:
        raise InvalidRecordingError(resolved_path) from exc


def _job_context_for(dir_path: str):
    """
    Liest job.json NEBEN den .specbin-Dateien eines records/longrun_*/-
    Ordners (WS3 11.5) - defensiv: fehlt die Datei oder ist sie kein
    lesbares JSON-Objekt mit job_id, liefert diese Funktion None, die
    Aufnahmen des Ordners werden dann OHNE Job-Kontext gelistet statt die
    ganze Antwort scheitern zu lassen. Liefert nur die vier fuers Frontend-
    Badge noetigen Felder - das volle Manifest (params/stats/...) braucht
    die Aufnahmeliste nicht.
    """
    job_json_path = os.path.join(dir_path, "job.json")
    if not os.path.isfile(job_json_path):
        return None
    try:
        with open(job_json_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or not manifest.get("job_id"):
        return None
    return {
        "job_id": manifest.get("job_id"),
        "label": manifest.get("label"),
        "type": manifest.get("type"),
        "state": manifest.get("state"),
    }


def _list_specbin_dir(dir_path: str, job_ctx) -> list:
    """
    Listet *.specbin-Dateien in EINEM Verzeichnis (nicht rekursiv) - Helfer
    fuer list_recordings(), sowohl fuer RECORD_DIR selbst (job_ctx=None,
    Ad-hoc-Aufnahmen) als auch fuer je einen records/longrun_*/-Unterordner
    (job_ctx aus _job_context_for(), oder None falls job.json fehlt/kaputt)
    genutzt. Fuer jede Datei ein kompakter Steckbrief aus der Sidecar-JSON
    (kein Oeffnen der Binaerdatei noetig). Eine unvollstaendige (Sidecar
    fehlt) oder defekte Aufnahme fliegt NICHT die ganze Antwort um, sondern
    erscheint nur als {"valid": false, ...} - so bleibt eine gerade laufende
    Ad-hoc-Aufnahme (die ihre Sidecar erst bei close() bekommt) sichtbar,
    ohne die Liste zu sprengen.
    """
    result = []
    if not os.path.isdir(dir_path):
        return result

    for entry in sorted(os.scandir(dir_path), key=lambda e: e.name):
        if not entry.is_file() or not entry.name.endswith(".specbin"):
            continue
        sidecar_path = entry.path + ".json"

        if not os.path.isfile(sidecar_path):
            item = {"valid": False, "name": entry.name,
                    "reason": "incomplete (no sidecar)"}
            if job_ctx:
                item["job"] = job_ctx
            result.append(item)
            continue
        try:
            with open(sidecar_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            item = {"valid": False, "name": entry.name, "reason": "sidecar unreadable"}
            if job_ctx:
                item["job"] = job_ctx
            result.append(item)
            continue
        # Zwei Aufnahmeformate akzeptiert (WS3 11.3/11.7, Schritt "Job-
        # bewusste Aufnahmeliste"): eb200-ifpan-bins-v1 (IFPAN, urspruenglich
        # das einzige) und eb200-dscan-maxhold-v1 (D-SCAN-Max-Hold, siehe
        # eb200_core/dscan_store.py) - der Reader liefert fuer beide die
        # Max-Ebene ueber dieselbe frame()-API, der Player braucht dafuer
        # keine Anpassung.
        if meta.get("format") not in (FORMAT_ID, FORMAT_ID_DSCAN):
            item = {"valid": False, "name": entry.name, "reason": "unexpected format"}
            if job_ctx:
                item["job"] = job_ctx
            result.append(item)
            continue

        item = {
            "valid": True,
            "name": entry.name,
            "path": to_relative(entry.path),
            "label": LABELS.get(to_relative(entry.path)),
            "start_iso": meta.get("start_iso"),
            "stop_iso": meta.get("stop_iso"),
            "center_hz": meta.get("center_hz"),
            "span_hz": meta.get("span_hz"),
            "bw_hz": meta.get("bw_hz"),
            "demod": meta.get("demod"),
            "n_window": meta.get("n_window"),
            "frames_written": meta.get("frames_written"),
            # true, solange der Recorder noch aktiv schreibt (Checkpoint-
            # Sidecar, siehe SpectrumBinWriter.checkpoint() bzw.
            # DscanMaxholdWriter.checkpoint()) - fehlt der Schluessel (finale
            # Sidecar nach close()), ist die Aufnahme fertig.
            "running": bool(meta.get("running", False)),
        }
        if job_ctx:
            item["job"] = job_ctx
        result.append(item)
    return result


def list_recordings():
    """
    Listet *.specbin-Dateien in RECORD_DIR (Ad-hoc-Aufnahmen, wie bisher)
    UND zusaetzlich in JEDEM records/longrun_*/-Unterordner (Long-Run-Job-
    Aufnahmen, WS3 11.5) - genau EINE Ebene tief, kein os.walk (die Struktur
    ist per Konzept definiert: Job-Teildateien liegen nie tiefer
    verschachtelt, siehe JobStore/Job-Recorder in eb200_core/job_runner.py).
    Job-Aufnahmen bekommen zusaetzlich ein "job"-Feld (job_id/label/type/
    state, siehe _job_context_for()). Sandbox unveraendert: alle Pfade
    bleiben unterhalb SPECTRUM_ROOT, /api/recording und /api/frames pruefen
    jeden path-Parameter weiterhin ueber resolve_safe_path() - das gilt
    bereits ohne Aenderung fuer Pfade in Unterordnern (kein "muss direkt in
    RECORD_DIR liegen"-Check dort).
    """
    record_dir_abs = os.path.join(SPECTRUM_ROOT, RECORD_DIR)
    result = _list_specbin_dir(record_dir_abs, None)

    if os.path.isdir(record_dir_abs):
        for entry in sorted(os.scandir(record_dir_abs), key=lambda e: e.name):
            if not entry.is_dir() or not entry.name.startswith("longrun_"):
                continue
            result.extend(_list_specbin_dir(entry.path, _job_context_for(entry.path)))
    return result


def recording_meta(resolved_path: str) -> dict:
    """
    Leichte Kopfdaten einer Aufnahme (KEINE Bin-Matrix): das rohe Sidecar plus
    die abgeleiteten Achsen-Eckwerte, die echten t-Werte, die seq-Folge und die
    Lueckenliste. Schnell auch fuer grosse Aufnahmen, weil nur die 12-Byte-
    Frame-Koepfe gelesen werden (times/seqs/gaps), nicht die Level.
    """
    rec = open_recording(resolved_path)
    try:
        n = rec.n_frames
        freq_lo = rec.bin0_hz
        freq_hi = rec.bin0_hz + (rec.n_window - 1) * rec.step_hz
        return {
            "meta": rec.meta,
            "n_frames": n,
            "n_window": rec.n_window,
            "bin0_hz": rec.bin0_hz,
            "step_hz": rec.step_hz,
            "freq_lo_hz": freq_lo,
            "freq_hi_hz": freq_hi,
            "truncated": rec.truncated,
            "times": rec.times(),
            "seqs": rec.seqs(),
            "gaps": rec.gaps(),
        }
    finally:
        rec.close()


def recording_frames(resolved_path: str) -> dict:
    """
    Die komplette Zeit×Bin-Matrix als base64-int16 (row-major: Frame 0 alle
    Bins, dann Frame 1 ...), dazu times/seqs und die Achsen-Eckwerte. Bei mehr
    als MAX_FRAMES Frames wird nach MAX_FRAMES abgeschnitten (capped=true), um
    den Browser nicht zu ueberfahren - der Reader-Seek bleibt die Reserve fuer
    spaeteres Chunking. Level bleiben ROH (Deci-dBµV); die /10-Umrechnung macht
    der Client im Anzeigepfad.
    """
    rec = open_recording(resolved_path)
    try:
        total = rec.n_frames
        n = min(total, MAX_FRAMES)
        combined = array("h")
        for k in range(n):
            combined.extend(rec.frame(k)[2])       # array('h') je Frame anhaengen
        all_times = rec.times()
        all_seqs = rec.seqs()
        return {
            "n_frames": n,
            "total_frames": total,
            "capped": total > MAX_FRAMES,
            "n_window": rec.n_window,
            "bin0_hz": rec.bin0_hz,
            "step_hz": rec.step_hz,
            "times": all_times[:n],
            "seqs": all_seqs[:n],
            "levels_b64": base64.b64encode(combined.tobytes()).decode("ascii"),
        }
    finally:
        rec.close()


# ---------------------------------------------------------------------------
# Lazy-Fill: Befund-JSON neben der Aufnahme (bei Bedarf per Subprozess erzeugt)
# ---------------------------------------------------------------------------

def befund_path_for(resolved_specbin: str) -> str:
    """Pfad des Befund-JSON: neben der Aufnahme, Aufnahmename im Dateinamen
    (.../<name>.specbin -> .../<name>.befund.json)."""
    return resolved_specbin[:-len(".specbin")] + ".befund.json"


def ensure_befund(resolved_specbin: str) -> str:
    """
    Stellt den Befund-JSON sicher (Lazy-Fill). Fehlt er, wird er JETZT durch
    einen Subprozess erzeugt: session_analyze.py im selben venv (sys.executable),
    cwd = Repo-Root, damit "from eb200_core ..." im Kind aufgeht. numpy bleibt
    damit im Kindprozess; dieser Server importiert kein numpy. Gibt den Pfad
    des Befund-JSON zurueck. Fehlerabbildung passt zu _payload_or_error:
      - FileNotFoundError   -> 404 (Aufnahme fehlt)
      - InvalidRecordingError -> 422 (Analyse fehlgeschlagen)
    Vorhandene Sidecars werden nur akzeptiert, wenn ihre analysis_version zu
    EXPECTED_ANALYSIS_VERSION passt; sonst Neuberechnung.
    """
    if not os.path.isfile(resolved_specbin):
        raise FileNotFoundError(resolved_specbin)
    out = befund_path_for(resolved_specbin)
    if os.path.isfile(out):
        # Versionspruefung: alte (unversionierte) oder abweichende Befunde
        # werden geloescht und unten neu erzeugt; unlesbares JSON ebenso.
        try:
            with open(out, encoding="utf-8") as fh:
                if json.load(fh).get("analysis_version") == EXPECTED_ANALYSIS_VERSION:
                    return out
        except (OSError, ValueError):
            pass
        try:
            os.unlink(out)
        except OSError:
            pass
    # T-2003: das Skript ist Programmcode (PROGRAM_ROOT), nicht Datenwurzel.
    analyzer = paths.program_path("session_analyze.py")
    proc = subprocess.run(
        [sys.executable, analyzer, resolved_specbin, "--json", out],
        cwd=paths.PROGRAM_ROOT, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(out):
        # keinen kaputten Teil-Cache zuruecklassen
        if os.path.isfile(out):
            try:
                os.unlink(out)
            except OSError:
                pass
        raise InvalidRecordingError(proc.stderr.strip() or "analysis failed")
    return out


def recording_befund(resolved_specbin: str) -> dict:
    """Builder fuer /api/befund: Lazy-Fill sicherstellen, dann JSON liefern."""
    out = ensure_befund(resolved_specbin)
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Loeschen ganzer Aufnahmen (T-804) - Messdaten-Invariante erweitert: das
# Entfernen KOMPLETTER Datensaetze ist erlaubt, das Veraendern von Inhalten
# bleibt verboten (siehe PLAN.md Entscheidungslog).
# ---------------------------------------------------------------------------

def delete_recording(resolved_specbin: str) -> dict:
    """
    Loescht eine komplette Aufnahme (.specbin + Sidecar .specbin.json +
    Befund-Sidecar) und leert die zugehoerigen Label-/Notiz-Eintraege.
    Schutzregeln (Sandbox, Long-Run-Teilschutz, Live-Sperre) liegen seit
    T-1104 ZENTRAL in eb200_core/recording_index.delete_recording() - diese
    Funktion ist nur noch ein duenner Adapter, der dessen RecordingLockedError/
    RecordingNotDeletableError in die hier erwarteten lokalen Klassen
    (RecordingLockedError/RecordingIsLongRunPartError) uebersetzt, damit der
    HTTP-Handler unveraendert bleibt.

    Die zentrale Funktion leert LABELS/NOTES ueber eine EIGENE LabelStore-
    Instanz (kennt die Globals dieses Moduls nicht) - hier zusaetzlich auf
    den Modul-Instanzen wiederholt, sonst bliebe deren In-Memory-Cache
    (LabelStore haelt den Stand nur im Speicher, kein Reload je Zugriff)
    veraltet und wuerde beim naechsten .set() auf einen ANDEREN Schluessel
    den geloeschten Eintrag beim Zurueckschreiben versehentlich wiederbeleben.

    Gibt {"deleted": [Dateinamen der tatsaechlich entfernten Dateien]}
    zurueck; einzelne fehlende Beidateien sind kein Fehler.
    """
    try:
        result = _delete_recording(None, "spectrum", to_relative(resolved_specbin), repo_root=SPECTRUM_ROOT)
    except _RecLocked as exc:
        raise _rewrap(RecordingLockedError, exc) from exc
    except _RecNotDeletable as exc:
        raise _rewrap(RecordingIsLongRunPartError, exc) from exc
    rel = to_relative(resolved_specbin)
    LABELS.set(rel, "")
    NOTES.set(rel, "")
    return {"deleted": result["deleted"]}


# ---------------------------------------------------------------------------
# App-Shell (Client) - einzelner HTML_PAGE-String, kein Templating-Framework
# ---------------------------------------------------------------------------

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope Spectrum analysis</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  /* T-1402: Farben nur noch als Tokens (eb200_core/ui_theme.py); Kopfleiste,
     Knoepfe, Felder, Tabellen liefert BASE_CSS. Canvas-Flaechen (Spektrogramm,
     Spektrum, Pegel, Gutter, Katalogstreifen) bleiben in beiden Themes
     schwarz - ihre Zeichenfarben stehen unveraendert im Script. */
  h2 { margin:0 0 6px; }
  button:disabled { background:var(--panel-2); color:var(--fg-faint); border-color:var(--border); cursor:default; }
  .delete-error { color:var(--danger); font-size:0.75em; margin-left:6px; }
  input[type=text] { width:360px; }
  #pathError { color:var(--danger); font-size:0.85em; margin-top:4px; }
  table { width:100%; margin-top:10px; }
  th .arrow { color:var(--accent); margin-left:4px; }
  td.label-cell input { padding:2px 4px; width:220px; font-size:1em; }
  td.label-cell input.saved { border-color:var(--success); }
  td.label-cell input.err { border-color:var(--danger); }
  /* Aufnahmetabelle: technische Spalten rechtsbuendig, Mittenfrequenz betont */
  #listHead th.num, tr.rec-row td.num { text-align:right; font-variant-numeric:tabular-nums; }
  tr.rec-row td.freq { color:var(--accent); font-weight:600; }
  #noteText { width:100%; min-height:70px; resize:vertical; margin:4px 0 4px; }
  #noteText.saved { border-color:var(--success); }
  .note-hint { color:var(--fg-muted); font-size:0.75em; min-height:1em; }
  /* Job-Badge (Long-Run-Aufnahmen aus records/longrun_<job_id>/, siehe
     list_recordings()) - Kurztext/job_id + Teil-Name, "läuft" hebt eine
     gerade noch beschriebene aktuelle Teildatei hervor (Sidecar
     running:true). Ad-hoc-Aufnahmen bleiben ohne Badge (Zelle leer). */
  .job-badge { display:inline-block; padding:1px 6px; border-radius:3px;
    background:var(--badge-mscan-bg); color:var(--badge-mscan-fg); font-size:0.85em; }
  .job-running { color:var(--warn); font-weight:bold; margin-left:4px; }
  #searchInput { width:320px; }
  tr.rec-row { cursor:pointer; }
  tr.rec-row:hover { background:var(--panel-2); }
  .view { display:none; }
  .toolbar { margin:8px 0 10px; }
  #recHeader { color:var(--accent); font-size:1.05em; font-weight:600; margin:6px 0 2px; }
  #recMeta { font-size:0.9em; color:var(--fg-muted); margin:0 0 6px; }
  #recHint { color:var(--warn); font-size:0.82em; margin:4px 0 10px; }
  #scaleRow { margin:6px 0 12px; font-size:0.9em; display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
  #scaleRow label { display:flex; align-items:center; gap:8px; }
  #scaleRow input[type=range] { width:240px; }

  /* Drei-Spalten-Layout: Befund-Navigator links, Wasserfall Mitte, Spektrum+Pegel
     rechts - jede Spalte eine Karte (Entwurf :8040). stretch laesst die
     Wasserfall-Spalte so hoch werden wie die rechte -> der scrollbare Viewport
     (flex:1) fuellt den Raum zwischen Ueberschrift und Frequenzachse. */
  .player-grid { display:flex; gap:16px; align-items:flex-start; margin-top:6px; }
  .wf-col { display:flex; flex-direction:column; flex:0 0 auto; }
  .side-col { display:flex; flex-direction:column; flex:0 0 auto; }
  .wf-col, .side-col { background:var(--panel); border:1px solid var(--border);
    border-radius:6px; padding:10px 12px; }
  .side-col h2 { margin-top:0; }
  .side-col > .canvas-wrap + h2, .side-col > #binLabel + h2 { margin-top:14px; }

  /* Schmale, klebende Befund-Navigator-Leiste (linkes Flex-Kind der
     .player-grid). align-self:flex-start + position:sticky: bleibt beim
     Body-Scroll oben stehen, waehrend Wasserfall/Spektrum scrollen; eigener
     Overflow-Scroll, damit lange Befundlisten die Leiste nicht sprengen. */
  .nav-col { flex:0 0 260px; max-width:260px; box-sizing:border-box;
             align-self:flex-start; position:sticky; top:8px;
             max-height:calc(100vh - 16px); overflow-y:auto;
             border:1px solid var(--border); border-radius:6px; background:var(--panel); padding:10px 12px; }
  .nav-col h2 { margin-top:0; }
  .nav-group { margin:4px 0; border-bottom:1px solid var(--border); }
  .nav-group > summary { cursor:pointer; color:var(--fg); font-weight:600; font-size:0.92em;
                         padding:5px 0; user-select:none; }
  .nav-group > summary:hover { color:var(--accent); }
  .nav-list { font-size:0.82em; margin:2px 0 8px; }
  .nav-empty { color:var(--fg-muted); font-style:italic; padding:2px 0; }
  .nav-item { color:var(--fg); padding:2px 0; line-height:1.25; border-bottom:1px solid var(--border); }
  .nav-item { cursor:pointer; }
  .nav-item:hover { background:var(--panel-2); }
  .nav-item.active { background:var(--accent-soft); color:var(--accent); }
  .nav-table { width:100%; border-collapse:collapse; font-size:0.82em; margin-top:0; }
  .nav-table th { text-align:left; color:var(--fg-muted); font-weight:normal; border-bottom:1px solid var(--border); padding:2px 8px 2px 0; }
  .nav-table td { padding:2px 8px 2px 0; border-bottom:none; white-space:nowrap; }
  .nav-table .num { text-align:right; font-variant-numeric:tabular-nums; }
  .nav-subhead { color:var(--fg-muted); font-size:0.78em; margin:6px 0 2px; border-bottom:1px solid var(--border); }
  /* Hoehe wird per JS (fitWaterfallHeight) exakt auf die rechte Spalte gesetzt. */
  .wf-viewport { overflow-y:auto; border:1px solid var(--canvas-border); background:var(--canvas-bg); }
  .wf-scroll-inner { display:flex; align-items:flex-start; }
  /* T-1409: die Flaeche hinter Gutter/Plot/Achse durchgehend dunkel, damit jedes
     Diagramm als geschlossenes Rechteck steht (Gutter ist kuerzer als der Stapel). */
  .canvas-wrap { display:flex; align-items:flex-start; gap:4px; background:var(--canvas-bg); }
  .gutter { flex-shrink:0; background:var(--canvas-bg); display:block; }
  canvas.plot { display:block; background:var(--canvas-bg); }
  canvas.bordered { border:1px solid var(--canvas-border); }
  #tooltip { position:fixed; background:var(--panel); color:var(--fg); border:1px solid var(--border-strong);
             box-shadow:var(--shadow); padding:4px 8px;
             font-size:0.8em; pointer-events:none; display:none; }
  /* Katalog-Streifen unter dem Spektrogramm: Spacer = Gutter (66) + 4 px wie
     der Datenteil der Achse in drawSpectroFreqAxis, Canvas = 720 - 4. */
  .known-row { display:flex; align-items:flex-start; background:var(--canvas-bg); }
  .known-row .spacer { flex:0 0 70px; }
  .hover-tip { position:fixed; pointer-events:none; z-index:1000;
              background:var(--panel); color:var(--fg); border:1px solid var(--border-strong);
              box-shadow:var(--shadow);
              border-radius:3px; padding:2px 6px; font-size:0.8em;
              display:none; }
  #binLabel { color:var(--accent); font-size:0.9em; margin:8px 0 6px; }

  #transport { margin:14px 0 0; font-size:0.9em; display:flex; gap:10px; align-items:center; flex-wrap:wrap;
               border-top:1px solid var(--border); padding-top:12px; }
  #transport .tbtn { min-width:36px; height:30px; padding:0; display:inline-flex; align-items:center; justify-content:center; }
  #transport .tbtn .glyph { width:14px; height:14px; }
  #frameSlider { flex:1 1 300px; min-width:260px; }
  #posLabel { color:var(--fg-muted); font-variant-numeric:tabular-nums; font-size:0.85em; }
</style>
</head>
<body>
/*HEADER*/

<div id="view-list" class="view">
  <h2>Recordings</h2>
  <div class="toolbar">
    <input type="text" id="searchInput" placeholder="Search in file / label ...">
  </div>
  <table>
    <thead><tr id="listHead"></tr></thead>
    <tbody id="recListBody"></tbody>
  </table>

  <h2>Open path</h2>
  <div class="toolbar">
    <input type="text" id="pathInput" placeholder="e.g. records/20260817_130000_48004kHz_USB.specbin">
    <button id="openPathBtn">Open</button>
  </div>
  <div id="pathError"></div>
</div>

<div id="view-player" class="view">
  <div class="toolbar"><button id="backToListBtn" class="primary">&laquo; Back to list</button></div>
  <div id="recHeader"></div>
  <div id="recMeta"></div>
  <div id="recHint"></div>

  <div id="scaleRow">
    <label>Scale min: <span id="scaleMinLabel">-10 dB&micro;V</span>
      <input type="range" id="scaleMin" min="-40" max="120" step="1" value="-10"></label>
    <label>Scale max: <span id="scaleMaxLabel">70 dB&micro;V</span>
      <input type="range" id="scaleMax" min="-40" max="120" step="1" value="70"></label>
    <button id="autoScaleBtn" title="Optimize the waterfall scale from the data">Auto</button>
    <label><input type="checkbox" id="gridToggle"> Frequency grid</label>
    <span class="hint">affects the waterfall only; spectrum and level are scaled to the signal maximum</span>
  </div>

  <div class="player-grid">
    <!-- NAVIGATOR: schmale Befund-Leiste. Schritt 1 = nur Geruest/Platzhalter;
         Befuellung aus dem Sidecar-JSON und die Klick-Sprung-Handler folgen in
         Schritt 2. Native <details> = einklappbar ohne JavaScript. -->
    <div class="nav-col" id="navCol">
      <h2>Findings navigator <span class="hint">(auto analysis; click jumps)</span></h2>
      <details class="nav-group" open>
        <summary>Continuous carriers</summary>
        <div class="nav-list" id="navCarriers"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div>
      </details>
      <details class="nav-group" open>
        <summary>Bursty channels</summary>
        <div class="nav-list" id="navBursts"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div>
      </details>
      <details class="nav-group" open>
        <summary>Strongest events</summary>
        <div class="nav-list" id="navEvents"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div>
      </details>
      <details class="nav-group">
        <summary>Comb</summary>
        <div class="nav-list" id="navComb"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div>
      </details>
      <details class="nav-group">
        <summary>Periodicity</summary>
        <div class="nav-list" id="navPeriodic"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div>
      </details>
      <details class="nav-group" open>
        <summary>Note</summary>
        <textarea id="noteText" maxlength="4000" rows="5" placeholder="Free text about this recording ..."></textarea>
        <div class="note-hint" id="noteHint">&nbsp;</div>
      </details>
    </div>
    <!-- LINKS: scrollbarer Wasserfall -->
    <div class="wf-col">
      <h2>Waterfall overview <span class="hint">(click/drag = time+frequency; magenta = gap)</span></h2>
      <div class="wf-viewport" id="spectroViewport">
        <div class="wf-scroll-inner">
          <canvas id="spectroGutter" class="gutter" width="66" height="600"></canvas>
          <canvas id="spectroCanvas" class="plot" width="720" height="600"></canvas>
        </div>
      </div>
      <canvas id="spectroFreqAxis" class="plot bordered" width="790" height="18"></canvas>
      <div class="known-row"><div class="spacer"></div>
        <canvas id="known-w" class="plot bordered" width="716" height="96"></canvas></div>
    </div>

    <!-- RECHTS: Spektrum + Pegelverlauf -->
    <div class="side-col" id="sideCol">
      <h2>Spectrum of the current frame <span class="hint">(cyan = frame, gray = peak hold; click/drag = frequency)</span></h2>
      <div class="canvas-wrap">
        <canvas id="specGutter" class="gutter" width="66" height="220"></canvas>
        <div>
          <canvas id="specCanvas" class="plot bordered" width="560" height="220"></canvas>
          <canvas id="specFreqAxis" class="plot bordered" width="560" height="18"></canvas>
          <canvas id="known-sp" class="plot bordered" width="560" height="96"></canvas>
        </div>
      </div>
      <div id="binLabel"></div>
      <h2>Level over time at the selected frequency <span class="hint">(true t axis; click/drag = time)</span></h2>
      <div class="canvas-wrap">
        <canvas id="binGutter" class="gutter" width="66" height="180"></canvas>
        <div>
          <canvas id="binCanvas" class="plot bordered" width="560" height="180"></canvas>
          <canvas id="binTimeAxis" class="plot bordered" width="560" height="18"></canvas>
        </div>
      </div>
    </div>
  </div>

  <div id="transport">
    <!-- T-1402: Bedienglyphen als Inline-SVG (fill:currentColor), nie als Emoji-faehiges Unicode -->
    <button class="tbtn primary" id="btnStart" title="First frame"><svg class="glyph" viewBox="0 0 16 16"><path d="M2 2h2v12H2zM14 2 6 8l8 6z"/></svg></button>
    <button class="tbtn primary" id="btnPrev" title="Previous frame"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 2 1 8l7 6zM15 2 8 8l7 6z"/></svg></button>
    <button class="tbtn primary" id="btnPlay" title="Play/Pause"><svg class="glyph" viewBox="0 0 16 16"><path d="M3 2l11 6-11 6z"/></svg></button>
    <button class="tbtn primary" id="btnNext" title="Next frame"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 2l7 6-7 6zM1 2l7 6-7 6z"/></svg></button>
    <button class="tbtn primary" id="btnEnd" title="Last frame"><svg class="glyph" viewBox="0 0 16 16"><path d="M12 2h2v12h-2zM2 2l8 6-8 6z"/></svg></button>
    <label>Speed:
      <select id="speedSel">
        <option value="1">1/s</option>
        <option value="2">2/s</option>
        <option value="5">5/s</option>
        <option value="10" selected>10/s</option>
        <option value="20">20/s</option>
        <option value="50">50/s</option>
      </select>
    </label>
    <input type="range" id="frameSlider" min="0" max="0" step="1" value="0">
    <span id="posLabel">-</span>
  </div>
</div>

<div id="tooltip"></div>
<script>
// Fester Tab-Name (Fensterdisziplin: 1 Tab pro Server, siehe waterfall.py openServerTab).
window.name = 'eb200_spectrum';
function el(id) { return document.getElementById(id); }

const KNOWN_STRIPS = ['known-w', 'known-sp'];   // Streifen-Canvases dieser Seite (vor dem geteilten Block)
// >>> KNOWN_STRIP_BLOCK_BEGIN (wortgleich in allen Servern, Referenz: waterfall.py, siehe CLAUDE.md)
// Katalog bekannter Frequenzen (known_freq/*.json via eb200_core/known_freq.py,
// Endpunkt /api/known_freq). EINMAL geladen; die Detailstufe entscheidet sich
// bei jedem Redraw allein aus der Pixeldichte des angezeigten Bereichs:
//   Band-Balken -> Segmente -> Kanal-Ticks -> Kanal-Ticks mit Label.
// Streifen-Layout (CANVAS_W x KNOWN_STRIP_H = 96). Drei Reihen von oben nach
// unten: Reihe 1 FNV-Zuweisungsebene - Regelwerk (was DARF hier senden, z. B.
// FNV_AT_2026.json; Balken y 0..16, Marker/Ticks y 16..26), Reihe 2
// Dienstebene - beobachtete/bekannte Nutzung (alle uebrigen Baender), Reihe 3
// Kanaele. Die y-Werte stehen seit T-129a NICHT mehr fest, sondern werden je
// Darstellung gerechnet (drawKnownStrip): eine Reihe, die im sichtbaren
// Bereich nichts enthaelt, kostet keine Hoehe, und der frei werdende Platz
// faellt an die Dienstebene. Praktisch heisst das: unter 30 MHz gibt es im
// ganzen Katalog kein Zuweisungsband und keine FNV-Einzelfrequenz (Messung
// 09/2026), dort beginnt die Dienstebene bei y 0 und hat 59 statt 30 px.
// Ueberlappende Dienst-Baender verteilen sich auf bis zu KNOWN_BAND_LANES
// Spuren statt sich in Katalogreihenfolge zu uebermalen. Die Spurhoehe gilt
// EINHEITLICH fuer die ganze Darstellung (T-129b - sonst erscheint ein Band
// ohne Nachbarn doppelt so hoch wie seine Nachbarn und wirkt wichtiger) und
// betraegt mindestens KNOWN_LANE_H; passen dann nicht alle Spuren, teilen
// sich die ueberzaehligen Baender die letzte Spur. Die Schriftgroesse folgt
// der Spurhoehe (knownFontFor).
// Region (station.json, 'land-*'-Tags) ist reine Anzeigefilterung der
// Kanalreihe - Katalog, Scan-Set-Upload und Editor bleiben vollstaendig.
// Dieser Block wird in den Analyse-Servern wortgleich inline gefuehrt (kein
// gemeinsames statisches JS - Architekturentscheidung wie bei den Nice-Ticks).
// ---------------------------------------------------------------------------
let knownCatalog = null;       // {bands, channels} - channels nach freq_hz sortiert
let knownEnabled = true;
let knownRegionEnabled = true;         // Regionsfilter an/aus (Toggle nur in waterfall.py)
let knownRegionTags = new Set();       // aus station.json (d.region.tags), leer = Filter wirkungslos
const knownLastRange = {};     // letzter Bereich je Streifen-Canvas-ID (fuer Toggle-Redraw)
const knownLastHeight = {};    // T-505f: zuletzt belegte Hoehe je Streifen (Canvas-Pixel)
// T-133: zuletzt gerechnete Reihengrenzen je Streifen, in Canvas-Pixeln von
// oben. Seit T-129a stehen sie nicht mehr fest - welche Reihe wo liegt, haengt
// davon ab, welche im sichtbaren Bereich ueberhaupt etwas enthaelt. Der
// Tooltip schlaegt hier nach, ueber welcher Reihe der Zeiger steht.
const knownLastRows = {};
// T-505f: belegte Hoehe merken und die Host-Seite informieren. Der Hook ist
// optional - nur waterfall.py definiert knownStripResized (schneidet dort den
// Uebersichts-Streifen zu); alle anderen Server ignorieren die Meldung.
function knownStripMeldeHoehe(id, hoehe) {
  knownLastHeight[id] = hoehe;
  if (typeof knownStripResized === 'function') knownStripResized(id, hoehe);
}
const KNOWN_SEG_PX = 60;       // Segment mind. so breit -> Segmente statt Bandtitel
const KNOWN_TICK_PX = 6;       // Kanalabstand mind. so breit -> Ticks
const KNOWN_LABEL_PX = 60;     // Kanalabstand mind. so breit -> Ticks mit Label
const KNOWN_STRIP_H = 96;      // Gesamthoehe - MUSS zum Hoehenattribut der sechs Streifen-Canvases passen
const KNOWN_ROW_GAP = 3;       // Luft zwischen zwei belegten Reihen
const KNOWN_FNV_H = 26;        // Reihe 1 (FNV-Zuweisungsebene), wenn belegt: Balken y 0..16, Ticks y 16..26
const KNOWN_BAND_H = 30;       // Reihe 2 (Dienstebene): Hoehe bei genau EINER Spur
const KNOWN_LANE_H = 14;       // Reihe 2: Spurhoehe ab zwei Spuren - zugleich die Untergrenze (T-129b)
const KNOWN_BAND_LANES = 3;    // Reihe 2: hoechstens so viele Spuren; weitere Baender teilen sich die letzte Spur
const KNOWN_CHAN_H = 34;       // Reihe 3 (Kanaele): Hoehe
// Schriften des Streifens. KNOWN_LABEL_PX stieg mit der Schrift von 45 auf 60:
// bei 12 px braucht ein typisches Label (bis 8 Zeichen, 4048 von 5892) rund
// 50 px, bei 45 px Kanalabstand haette die Kollisionspruefung die Mehrzahl der
// Labels ohnehin wieder verworfen.
const KNOWN_FONT_GROSS = '14px sans-serif';   // Dienstspur ab 20 px Hoehe
const KNOWN_FONT_BASIS = '12px sans-serif';   // Regelfall: FNV-Reihe, Kanalreihe, Spur ab 13 px
const KNOWN_FONT_KLEIN = '9px sans-serif';    // gedraengte Spur unter 13 px (drei Baender uebereinander)

// Schrift zur Spurhoehe. Eine 12-px-Schrift in einer 9-px-Spur ragt in die
// Nachbarspur - genau der Effekt, der vor T-129 im 40-m-Bereich wie ein
// Uebermalen aussah.
function knownFontFor(rowH) {
  if (rowH >= 20) return KNOWN_FONT_GROSS;
  if (rowH >= 13) return KNOWN_FONT_BASIS;
  return KNOWN_FONT_KLEIN;
}
// Rueckfall-Palette: gedeckte Farben fuer Dateien OHNE eigenen Eintrag im
// Dateikopf. Sie bleibt bewusst zurueckhaltend - wer keine Farbe waehlt, soll
// nicht auffallen. Die Auswahl in :8010 speist sich dagegen aus
// CATALOG_COLORS (eb200_core/known_freq.py), seit T-132c 39 Farben von sehr
// dunkel bis sehr hell.
const KNOWN_PALETTE = ['#3a5a8a', '#5a3a7a', '#2f6b5a', '#7a5a2a', '#6a3a3a', '#3a6a6a', '#5a5a2a'];

function knownColor(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return KNOWN_PALETTE[h % KNOWN_PALETTE.length];
}

// Farbe eines Bandes (T-132): ausdruecklich im Dateikopf gesetzt (meta.color)
// oder als Rueckfall gehasht - seit T-132 ueber den DIENST statt ueber die
// Band-ID, damit wenigstens alle Baender einer Datei zusammengehoerig
// aussehen. Befund 09/2026: die DAB-Bloecke waren untereinander verschieden,
// waehrend das 70-cm-Band zufaellig dieselbe Farbe wie DVB-T2 traf.
function knownBandColor(b) {
  return b.color || knownColor(b.service || b.id);
}

// Schriftfarbe zu einer Balkenfarbe (T-132c). Die Palette reicht seit T-132c
// von sehr dunkel bis sehr hell, ein fest verdrahtetes Weiss waere auf
// sonnengelb oder mint unlesbar - dasselbe gilt fuer jede helle Farbe aus dem
// RGB-Waehler. `alpha` ist die Deckkraft, mit der der Balken gezeichnet wird:
// die FNV-Reihe legt ihre Balken mit 0.55 auf Schwarz, der Untergrund unter
// dem Titel ist dort also dunkler als der eingestellte Wert.
// Zwei Werte sind gerechnet, nicht geschaetzt: die Schwelle 0.179 ist der
// Punkt, an dem reines Weiss und reines Schwarz denselben Kontrast liefern -
// oberhalb gewinnt Schwarz, unterhalb Weiss. Und die Schrift ist #fff/#000
// statt des sonst ueblichen #eee/#111, weil sonnengelb und limette in der
// FNV-Reihe (bei 0.55 Deckkraft) genau in die Mitte fallen: mit #eee blieben
// sie bei 4,05 bzw. 4,28, mit #fff erreichen sie 4,74 und 5,01. So traegt
// JEDE der 39 Farben in BEIDEN Ebenen mindestens Kontrast 4,7.
function knownTextColor(hexwert, alpha) {
  const roh = [1, 3, 5].map(i => parseInt(hexwert.substr(i, 2), 16) / 255 * (alpha || 1));
  const lin = roh.map(x => x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4));
  return (0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]) > 0.179 ? '#000' : '#fff';
}

fetch('/api/known_freq').then(r => r.json()).then(d => {
  knownCatalog = d;
  knownRegionTags = new Set((d.region && d.region.tags) || []);
  if (d.errors && d.errors.length) console.warn('[known_freq]', d.errors);
  // Der Region-Schalter existiert nur in waterfall.py (Toolbar neben
  // knownToggle); die anderen Server filtern nach station.json ohne eigenen
  // Schalter. Ohne region_tags (Filter serverseitig ohnehin wirkungslos)
  // bleibt das Label dort versteckt. label.tbtn setzt display:inline-flex -
  // das hidden-Attribut wird von CSS ueberstimmt (Befund), darum display
  // direkt setzen statt hidden.
  const regionToggle = el('knownRegionToggle');
  if (regionToggle && knownRegionTags.size === 0) regionToggle.closest('label').style.display = 'none';
  redrawKnownStrips();
}).catch(err => console.warn('[known_freq] Katalog nicht ladbar:', err));

// Kanaele im Bereich per Binaersuche (Liste ist serverseitig sortiert).
function knownChannelsInRange(loHz, hiHz) {
  if (!knownCatalog) return [];
  const ch = knownCatalog.channels;
  let lo = 0, hi = ch.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (ch[m].freq_hz < loHz) lo = m + 1; else hi = m; }
  const out = [];
  for (let i = lo; i < ch.length && ch[i].freq_hz <= hiHz; i++) out.push(ch[i]);
  return out;
}

// Benannte Kanaele verdraengen Rasterkanaele auf derselben Frequenz.
function knownDedupe(chans) {
  const named = new Set(chans.filter(c => !c.raster).map(c => c.freq_hz));
  return chans.filter(c => !c.raster || !named.has(c.freq_hz));
}

// FNV-Einzelfrequenzen (Tag 'fnv', siehe known_freq/FNV_AT_2026.json
// defaults.tags) gehoeren zur FNV-Ebene (Reihe 1, eigene Ticks) - eine
// eigene Kanalreihe waere bei den vielen Einzelfrequenzen der Anlage 2 zu
// dicht und wuerde die eigentlichen Sender/Kanaele verdraengen.
function knownFnvChannels(loHz, hiHz) {
  return knownChannelsInRange(loHz, hiHz).filter(c => (c.tags || []).includes('fnv'));
}

// Kanaele fuer Kanalreihe und Tooltip-Kanalfall: ohne FNV-Einzelfrequenzen
// (siehe oben; der Tooltip zeigt sie stattdessen als FNV-Zeile, knownInfoAt),
// mit optionalem Regionsfilter. Ein Kanal ohne 'land-*'-Tag (Flugfunk,
// Amateurfunk, Nachbarlaender, OE3GAS) bleibt IMMER sichtbar; ein Kanal MIT
// 'land-*'-Tag nur, wenn eines seiner Tags in knownRegionTags steht. Reine
// Anzeigefilterung - Katalog, Scan-Set-Upload und Editor bleiben vollstaendig.
function knownVisibleChannels(loHz, hiHz) {
  let chans = knownChannelsInRange(loHz, hiHz).filter(c => !(c.tags || []).includes('fnv'));
  if (knownRegionEnabled && knownRegionTags.size > 0) {
    chans = chans.filter(c => {
      const landTags = (c.tags || []).filter(t => t.indexOf('land-') === 0);
      return !landTags.length || landTags.some(t => knownRegionTags.has(t));
    });
  }
  return chans;
}

// Kuerzt text bis er in avail (px, bereits um den Rand bereinigt) passt und
// haengt KNOWN_ELLIPSIS an; unter 12 px Breite gibt es keinen Titel mehr
// (Platz reicht nicht einmal fuer ein gekuerztes Wort plus Ellipse).
function knownFitText(ctx, text, avail) {
  if (avail < 12) return '';
  if (ctx.measureText(text).width <= avail) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ctx.measureText(text.slice(0, mid) + KNOWN_ELLIPSIS).width <= avail) lo = mid; else hi = mid - 1;
  }
  return lo > 0 ? text.slice(0, lo) + KNOWN_ELLIPSIS : KNOWN_ELLIPSIS;
}

// Reihe 1 (y 0..KNOWN_FNV_H): FNV-Zuweisungsebene - Regelwerk, nicht die
// beobachtete Realitaet (Reihe 2). globalAlpha 0.55 laesst den Rahmen
// bewusst zuruecktreten, damit die Dienstebene optisch fuehrend bleibt.
// Bandtitel wird IMMER gezeigt (knownFitText kuerzt statt wegzulassen).
// Segmente ersetzen den Titel nicht, sondern erscheinen als schmaler,
// hellerer Marker an der Unterkante; ihr Titel erscheint NICHT in der Reihe
// (Bandtitel und Segmenttitel wuerden sich in 9 px ueberlagern - Befund
// 200-230 MHz), sondern im Tooltip (knownTextAt).
function drawKnownFnvRow(ctx, w, loHz, hiHz, pxPerHz, xOf) {
  const baender = knownCatalog.bands.filter(b => b.layer === 'zuweisung' && b.stop_hz >= loHz && b.start_hz <= hiHz);
  ctx.textAlign = 'center';
  for (const b of baender) {
    const x0 = Math.max(0, xOf(b.start_hz)), x1 = Math.min(w, xOf(b.stop_hz));
    const bw = Math.max(1, x1 - x0);
    const farbe = knownBandColor(b);
    ctx.globalAlpha = 0.55;
    ctx.fillStyle = farbe;
    ctx.fillRect(x0, 0, bw, 16);
    ctx.globalAlpha = 1;

    const titel = knownFitText(ctx, b.title, bw - 6);
    if (titel) {
      ctx.fillStyle = knownTextColor(farbe, 0.55);
      ctx.fillText(titel, (x0 + x1) / 2, 8);
    }

    const segs = b.segments || [];
    if (!segs.length) continue;
    for (const s of segs) {
      const sx0 = Math.max(0, xOf(s.start_hz)), sx1 = Math.min(w, xOf(s.stop_hz));
      if (sx1 <= x0 || sx0 >= x1) continue;
      const sw = Math.max(1, sx1 - sx0);
      // T-132: Segmentmarker in der Bandfarbe statt in Weiss. Der Balken
      // darueber liegt bei globalAlpha 0.55, der Marker bei 1 - dieselbe
      // Farbe wirkt dadurch heller und der Marker gehoert sichtbar zum Band.
      ctx.fillStyle = farbe;
      ctx.fillRect(sx0, 18, sw, 4);
    }
  }

  // FNV-Einzelfrequenzen als Ticks ohne Label (siehe knownFnvChannels).
  ctx.strokeStyle = '#4ee';
  ctx.lineWidth = 2;
  let fnvTicks = 0;
  for (const c of knownFnvChannels(loHz, hiHz)) {
    const x = Math.round(xOf(c.freq_hz)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 16); ctx.lineTo(x, 26); ctx.stroke();
    fnvTicks++;
  }
  ctx.lineWidth = 1;
  return baender.length > 0 || fnvTicks > 0;
}

// Ein Band der Dienstebene zeichnen (Balken bzw. Segmente, wie vor T-113) -
// y0/rowH kommen von drawKnownServiceRow (volle Hoehe oder eine von zwei
// Spuren bei Ueberlappung).
function knownDrawServiceBox(ctx, w, bx, y0, rowH, pxPerHz, xOf) {
  const b = bx.band, x0 = bx.x0, x1 = bx.x1;
  const segs = b.segments || [];
  const useSegs = segs.length > 0 && segs.every(s => (s.stop_hz - s.start_hz) * pxPerHz >= KNOWN_SEG_PX);
  const boxes = useSegs
    ? segs.map(s => ({ x0: Math.max(0, xOf(s.start_hz)), x1: Math.min(w, xOf(s.stop_hz)), title: s.title }))
    : [{ x0, x1, title: b.title }];
  const farbe = knownBandColor(b);
  ctx.fillStyle = farbe;
  ctx.fillRect(x0, y0, Math.max(1, x1 - x0), rowH);
  ctx.strokeStyle = 'rgba(255,255,255,0.35)';
  for (const box of boxes) {
    if (box.x1 <= 0 || box.x0 >= w) continue;
    ctx.beginPath(); ctx.moveTo(Math.round(box.x0) + 0.5, y0); ctx.lineTo(Math.round(box.x0) + 0.5, y0 + rowH); ctx.stroke();
    const avail = box.x1 - box.x0 - 6;
    if (avail > 0 && ctx.measureText(box.title).width <= avail) {
      ctx.fillStyle = knownTextColor(farbe); ctx.textAlign = 'center';
      ctx.fillText(box.title, (box.x0 + box.x1) / 2, y0 + rowH / 2 + 0.5);
      ctx.fillStyle = farbe;
    }
  }
}

// Reihe 2 (Dienstebene, y0 und verfuegbare Hoehe kommen seit T-129a von
// drawKnownStrip): alle Baender ausser der FNV-Zuweisungsebene (layer
// 'dienst' oder fehlend) - die beobachtete Realitaet. Befund vor T-113:
// ueberlappende Baender uebermalten sich in Katalogreihenfolge vollstaendig
// (fnv-470000 loeschte dvbt2-uhf samt K-Segmenten). Seither werden sie zu
// Clustern gruppiert und auf Spuren verteilt. Seit T-129b gilt die Spurzahl
// EINHEITLICH fuer die ganze Darstellung und richtet sich nach dem
// dichtesten Cluster (knownLaneLayout) - vorher bekam ein Cluster ohne
// Ueberlappung die volle Hoehe, waehrend seine Nachbarn sich zwei Spuren
// teilten, was wie eine Gewichtung aussah. Passen bei der Untergrenze
// KNOWN_LANE_H nicht alle benoetigten Spuren in die verfuegbare Hoehe,
// teilen sich die ueberzaehligen Baender die LETZTE Spur (Uebermalen dort
// akzeptiert - der seltene Rest-Fall).
// Wie viele Spuren braeuchte dieser Cluster, wenn der Platz unbegrenzt waere?
// Erstpassungs-Zuteilung: jedes Band kommt in die erste Spur, in die es passt.
// T-131: Sammelbalken fuer eine Kanalgruppe, die fuer einzelne Ticks zu dicht
// ist. Ohne ihn sieht "zu dicht" genauso aus wie "hier gibt es nichts" -
// waehrend der Tooltip die Kanaele weiterhin kennt und anfahrbar macht. Der
// Balken sagt: hier liegen Kanaele, fuer Einzelmarker muss man tiefer zoomen.
// Benannte und Rasterkanaele bekommen zwei uebereinanderliegende Baender,
// damit sie sich nicht gegenseitig verdecken.
function knownDrawDenseBar(ctx, punkte, yTop, xOf) {
  const x0 = xOf(punkte[0].freq_hz);
  const x1 = xOf(punkte[punkte.length - 1].freq_hz);
  ctx.globalAlpha = 0.45;
  ctx.fillStyle = punkte[0].raster ? '#888' : '#8fd';
  ctx.fillRect(x0, yTop + (punkte[0].raster ? 4 : 0), Math.max(1, x1 - x0), 3);
  ctx.globalAlpha = 1;
}

function knownLaneDemand(cl) {
  const enden = [];
  for (const bx of cl) {
    const lane = enden.findIndex(e => bx.x0 >= e);
    if (lane < 0) enden.push(bx.x1); else enden[lane] = bx.x1;
  }
  return enden.length;
}

// Dieselbe Zuteilung, aber auf `spuren` begrenzt: alles darueber kommt in die
// letzte Spur und darf sich dort ueberlappen (der seltene Rest-Fall).
function knownLaneOf(cl, spuren) {
  const enden = [], spurVon = [];
  for (const bx of cl) {
    let lane = enden.findIndex(e => bx.x0 >= e);
    if (lane < 0) {
      if (enden.length < spuren) { lane = enden.length; enden.push(-Infinity); }
      else lane = spuren - 1;
    }
    enden[lane] = Math.max(enden[lane], bx.x1);
    spurVon.push(lane);
  }
  return spurVon;
}

// Spurzahl und Spurhoehe fuer die GANZE Darstellung (T-129b). Massgeblich ist
// der dichteste Cluster: unterschiedlich hohe Balken nebeneinander lesen sich
// als Gewichtung, die es nicht gibt. Untergrenze ist KNOWN_LANE_H - lieber
// teilen sich zwei Baender die letzte Spur, als dass die Schrift unlesbar
// wird. Eine einzelne Spur bekommt die grosszuegiger bemessene KNOWN_BAND_H.
function knownLaneLayout(clusters, verfuegbar) {
  let bedarf = 1;
  for (const cl of clusters) bedarf = Math.max(bedarf, knownLaneDemand(cl));
  const passt = verfuegbar >= 2 * KNOWN_LANE_H + 1
    ? Math.floor((verfuegbar + 1) / (KNOWN_LANE_H + 1)) : 1;
  const spuren = Math.max(1, Math.min(bedarf, KNOWN_BAND_LANES, passt));
  const rowH = spuren === 1 ? Math.min(KNOWN_BAND_H, verfuegbar) : KNOWN_LANE_H;
  return { spuren, rowH, hoehe: spuren * rowH + (spuren - 1) };
}

// Reihe 2 (Dienstebene) ab y0, hoechstens `verfuegbar` px hoch. Rueckgabe ist
// die tatsaechlich belegte Hoehe, 0 wenn im Bereich kein Band liegt.
function drawKnownServiceRow(ctx, w, loHz, hiHz, pxPerHz, xOf, y0, verfuegbar) {
  const sichtbar = knownCatalog.bands.filter(b => b.layer !== 'zuweisung' && b.stop_hz >= loHz && b.start_hz <= hiHz);
  const boxen = sichtbar.map(b => ({
    band: b,
    x0: Math.max(0, xOf(b.start_hz)),
    x1: Math.min(w, xOf(b.stop_hz)),
  }));
  if (!boxen.length) return 0;

  // Cluster ueberlappender Boxen (Bandliste ist nach start_hz sortiert, xOf
  // ist monoton - eine Sweep-Zusammenfassung genuegt).
  const clusters = [];
  let cluster = [], clusterEnd = -Infinity;
  for (const bx of boxen) {
    if (cluster.length && bx.x0 >= clusterEnd) { clusters.push(cluster); cluster = []; clusterEnd = -Infinity; }
    cluster.push(bx);
    clusterEnd = Math.max(clusterEnd, bx.x1);
  }
  if (cluster.length) clusters.push(cluster);

  const layout = knownLaneLayout(clusters, verfuegbar);
  ctx.font = knownFontFor(layout.rowH);
  for (const cl of clusters) {
    const spurVon = knownLaneOf(cl, layout.spuren);
    cl.forEach((bx, i) => knownDrawServiceBox(
      ctx, w, bx, y0 + spurVon[i] * (layout.rowH + 1), layout.rowH, pxPerHz, xOf));
  }
  return layout.hoehe;
}

function drawKnownStrip(ctx, canvas, loHz, hiHz) {
  const w = canvas.width, h = canvas.height, span = hiHz - loHz;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  if (!knownEnabled || !knownCatalog || !(span > 0)) return 0;
  const pxPerHz = w / span;
  const xOf = f => (f - loHz) * pxPerHz;
  ctx.font = KNOWN_FONT_BASIS; ctx.textBaseline = 'middle';

  // T-129a: erst erheben, dann Platz zuteilen, dann zeichnen. Ob Reihe 1 etwas
  // enthaelt, muss VOR dem Zeichnen feststehen - drawKnownFnvRow meldet es erst
  // hinterher, und die Reihen darunter haengen daran. Die Erhebung ist derselbe
  // Filter, den die Funktion selbst verwendet.
  const fnvDa = knownCatalog.bands.some(b => b.layer === 'zuweisung'
      && b.stop_hz >= loHz && b.start_hz <= hiHz)
    || knownFnvChannels(loHz, hiHz).length > 0;
  if (fnvDa) drawKnownFnvRow(ctx, w, loHz, hiHz, pxPerHz, xOf);
  const bandY = fnvDa ? KNOWN_FNV_H + KNOWN_ROW_GAP : 0;
  const dienstH = drawKnownServiceRow(ctx, w, loHz, hiHz, pxPerHz, xOf, bandY,
    KNOWN_STRIP_H - bandY - KNOWN_ROW_GAP - KNOWN_CHAN_H);
  const belegtH = dienstH > 0 ? bandY + dienstH : (fnvDa ? KNOWN_FNV_H : 0);

  // T-133: die Grenzen dieser Darstellung merken, BEVOR die Kanalreihe ueber
  // ihren Inhalt entscheidet - auch ein leerer Kanalbereich ist ein Bereich,
  // in dem der Zeiger stehen kann, und "dort ist nichts" ist die richtige
  // Auskunft. Die Werte sind Canvas-Pixel von oben.
  const yTop = dienstH > 0 ? bandY + dienstH + KNOWN_ROW_GAP : bandY;
  const yUnten = yTop + KNOWN_CHAN_H;
  knownLastRows[canvas.id] = { fnvBis: fnvDa ? KNOWN_FNV_H : 0, bandVon: bandY,
                               bandBis: bandY + dienstH, chanVon: yTop, chanBis: yUnten };

  // -- Kanalreihe --
  // Benannte Kanaele und Rasterkanaele beurteilen ihre Dichte GETRENNT: ein
  // dichtes Raster (z. B. 12,5-kHz-Relaiskanaele) darf die wenigen benannten
  // Kanaele im selben Bereich nicht mit unterdruecken.
  const chans = knownDedupe(knownVisibleChannels(loHz, hiHz));
  if (!chans.length) return belegtH;
  ctx.font = KNOWN_FONT_BASIS;      // T-129: die Dienstebene kann sie umgestellt haben
  const gruppen = [chans.filter(c => !c.raster), chans.filter(c => c.raster)];
  let chanRow = false;
  ctx.textAlign = 'center';
  let lastRight = -Infinity;
  for (const grp of gruppen) {
    if (!grp.length) continue;
    // Kanaele auf EXAKT derselben Frequenz zu einem Zeichenpunkt buendeln,
    // BEVOR die Dichte beurteilt wird. Sie landen ohnehin auf demselben
    // Pixel - ihr Abstand 0 sagt ueber die Lesbarkeit nichts aus und hat im
    // Dichtemass nichts verloren. Unter dem frueheren Minimum-Kriterium (bis
    // T-131) war er sogar toedlich: ein einziger doppelt belegter Punkt
    // drueckte das Mass auf 0 und schob die GESAMTE Gruppe unter
    // KNOWN_TICK_PX - im vollen UKW-Band verschwanden so alle Ticks, weil
    // fuenf Frequenzen doppelt belegt sind (in- und auslaendischer Sender).
    // Diesem Fall haelt der Median von sich aus stand; das Buendeln bleibt
    // trotzdem richtig, weil es die Null-Abstaende aus der Abstandsliste
    // heraushaelt (sie zoegen den Median unnoetig nach unten) und zwei Ticks
    // auf demselben Pixel verhindert.
    // Das Label eines gebuendelten Punktes fuehrt beide Namen mit " · ".
    // Der Tooltip zeigt dagegen weiterhin nur EINEN Treffer: attachKnownTooltip
    // waehlt mit best/bestD den naechstgelegenen Kanal, bei gleicher Frequenz
    // also den in der Katalogreihenfolge ersten. Wer beide Namen sehen will,
    // zoomt so weit hinein, dass Labels erscheinen.
    const punkte = [];
    for (const c of grp) {
      const letzter = punkte[punkte.length - 1];
      if (letzter && letzter.freq_hz === c.freq_hz) {
        letzter.label += KNOWN_SEP + c.label;
        letzter.n++;
      } else {
        punkte.push({ freq_hz: c.freq_hz, label: c.label, raster: c.raster, n: 1 });
      }
    }
    // T-131: Massstab der Dichte ist der MEDIAN der Abstaende, nicht das
    // Minimum. Mit dem Minimum loeschten zwei Eintraege wenige hundert Hertz
    // auseinander die GANZE Gruppe: im IFPAN-Zoom blieb der Streifen leer,
    // obwohl der Tooltip die Kanaele weiter kannte (Befund 09/2026 mit den
    // UDXF-Logs auf Kurzwelle - bei 160 kHz Span 2,81 px kleinster gegen
    // 22,50 px mittleren Abstand). Dichte Gruppen bleiben unterdrueckt, weil
    // dort auch der Median unter KNOWN_TICK_PX liegt (Flugfunkraster, alle
    // benannten Kanaele ueber 1-30 MHz) - der Tick-Zaun, den das alte
    // Kriterium verhindern sollte, kommt also nicht zurueck.
    const diffs = [];
    for (let i = 1; i < punkte.length; i++) diffs.push(punkte[i].freq_hz - punkte[i - 1].freq_hz);
    diffs.sort((a, b) => a - b);
    const mitte = diffs.length >> 1;
    const medianHz = !diffs.length ? Infinity
      : (diffs.length % 2 ? diffs[mitte] : (diffs[mitte - 1] + diffs[mitte]) / 2);
    const medianPx = medianHz === Infinity ? Infinity : medianHz * pxPerHz;
    if (medianPx < KNOWN_TICK_PX) {
      knownDrawDenseBar(ctx, punkte, yTop, xOf);
      chanRow = true;
      continue;                                 // zu dicht: nur der Sammelbalken
    }
    const withLabels = medianPx >= KNOWN_LABEL_PX;
    chanRow = true;
    const tickH = withLabels ? 9 : 14;
    // Ausduennung: auch in einer zugelassenen Gruppe koennen einzelne Paare eng
    // liegen. Solche Ticks werden ausgelassen statt uebereinandergezeichnet -
    // als KRITERIUM waere das Ausduennen falsch (im Flugfunkraster entstuenden
    // so 95 % Zaunbreite), innerhalb einer duenn besetzten Gruppe ist es genau
    // richtig.
    let lastTick = -Infinity;
    for (const c of punkte) {
      const x = Math.round(xOf(c.freq_hz)) + 0.5;
      if (x - lastTick < KNOWN_TICK_PX) continue;
      lastTick = x;
      // Mehrere sichtbare Sender auf derselben Frequenz (nach Regionsfilter,
      // z. B. in- und auslaendischer UKW-Sender) heben sich als dickerer,
      // bernsteinfarbener Tick ab statt im gebuendelten Label unterzugehen.
      if (c.n > 1) { ctx.lineWidth = 3; ctx.strokeStyle = '#fd8'; }
      else { ctx.lineWidth = 1; ctx.strokeStyle = c.raster ? '#888' : '#8fd'; }
      ctx.beginPath(); ctx.moveTo(x, yTop); ctx.lineTo(x, yTop + tickH); ctx.stroke();
      ctx.lineWidth = 1;
      // T-131: jedes Label wird versucht, die Kollisionspruefung darunter
      // entscheidet. Frueher unterdrueckte die Gruppenentscheidung die
      // Beschriftung als Ganzes - bei 100 kHz Span im IFPAN blieb deshalb
      // jeder Tick namenlos, obwohl nebeneinander mehrere Labels Platz gehabt
      // haetten. Die Gruppenentscheidung waehlt jetzt nur noch die Tickhoehe
      // (kurzer Tick, wo mit Beschriftung zu rechnen ist).
      const tw = ctx.measureText(c.label).width;
      if (x - tw / 2 < lastRight + 4) continue;  // Ueberlappung: Label auslassen, Tick bleibt
      ctx.fillStyle = c.raster ? '#aaa' : '#8fd';
      ctx.fillText(c.label, x, yUnten - 8);
      lastRight = x + tw / 2;
    }
  }
  return chanRow ? yUnten : belegtH;
}

function redrawKnownStrips() {
  for (const id of KNOWN_STRIPS) {
    const r = knownLastRange[id];
    if (r) knownStripMeldeHoehe(id, drawKnownStrip(el(id).getContext('2d'), el(id), r[0], r[1]));
  }
}
// Einstiegspunkt fuer die Host-Seite: Bereich merken (fuer Toggle/Tooltip) und zeichnen.
function updateKnownStrip(id, loHz, hiHz) {
  knownLastRange[id] = [loHz, hiHz];
  knownStripMeldeHoehe(id, drawKnownStrip(el(id).getContext('2d'), el(id), loHz, hiHz));
}

// Trennzeichen " · " ohne Escape-Sequenz im Quelltext (wird beim Kopieren sonst aufgeloest).
const KNOWN_SEP = ' ' + String.fromCharCode(183) + ' ';
// Ellipse fuer knownFitText - selber Grund wie bei KNOWN_SEP: kein Escape im Quelltext.
const KNOWN_ELLIPSIS = String.fromCharCode(8230);

// Naechstgelegener sichtbarer Kanal (<= 5 px) plus alle sichtbaren Kanaele
// auf EXAKT derselben Frequenz (Mehrfachbelegung, z. B. in- und
// auslaendischer UKW-Sender - siehe drawKnownStrip). null, wenn keiner in
// Reichweite liegt. Gemeinsame Grundlage fuer knownLinesAt und knownTextAt.
function knownNearestChannels(fHz, pxPerHz) {
  if (!knownCatalog || !knownEnabled || !(pxPerHz > 0)) return null;
  const tol = 5 / pxPerHz;
  const kandidaten = knownDedupe(knownVisibleChannels(fHz - tol, fHz + tol));
  let best = null, bestD = Infinity;
  for (const c of kandidaten) {
    const d = Math.abs(c.freq_hz - fHz);
    if (d < bestD) { best = c; bestD = d; }
  }
  return best ? { best, gleiche: kandidaten.filter(c => c.freq_hz === best.freq_hz) } : null;
}

// Alle Angaben zu einer Frequenz, roh (fuer knownRenderTip UND die Text-
// Funktionen unten). Kanalfall (naechster sichtbarer Kanal <= 5 px bestimmt
// freq_hz): channels = alle sichtbaren Kanaele exakt auf freq_hz
// (Katalogreihenfolge, gleiche[0] ist stets der naechstgelegene - siehe
// knownNearestChannels), hidden = wie viele weitere Kanaele auf derselben
// Frequenz NUR der Regionsfilter ausblendet (0 wenn der Filter nichts
// ausblendet), bands = []. Bandfall (kein Kanal in Reichweite): channels = [],
// freq_hz = null, hidden = 0, bands = je beruehrtes Band ein Eintrag
// {title, seg, fnv} (FNV-Zuweisungsbaender zuerst). Liegt eine FNV-Einzel-
// frequenz (Tick in Reihe 1, knownFnvChannels) <= 5 px entfernt, steht sie
// als ERSTE Bandzeile {title: label, seg: "f MHz", fnv: true} voran (T-118 -
// Befund 232-238 MHz: Ticks ohne jede Auskunft, weil knownVisibleChannels
// sie fuer die Kanalreihe bewusst ausschliesst). Kein Treffer -> null.
// Welche Reihe liegt bei yPx (Canvas-Pixel von oben)? 'fnv', 'dienst',
// 'kanal' oder null, wenn dort nichts gezeichnet wurde. Die Luecken zwischen
// den Reihen werden bewusst der jeweils oberen zugeschlagen, damit beim
// Hinuebergleiten nichts flackert; eine nicht gezeichnete Reihe hat
// Ausdehnung 0 und faellt damit von selbst heraus.
function knownReiheBei(canvasId, yPx) {
  const r = knownLastRows[canvasId];
  if (!r) return null;
  if (r.fnvBis > 0 && yPx < r.bandVon) return 'fnv';
  if (r.bandBis > r.bandVon && yPx < r.chanVon) return 'dienst';
  if (yPx < r.chanBis) return 'kanal';
  return null;
}

// `reihe` (T-133) schraenkt die Auskunft auf die Reihe unter dem Zeiger ein.
// Ohne Angabe gilt die alte Vorfahrt - erst der naechste Kanal, sonst die
// Baender -, die knownLinesAt und knownTextAt brauchen: die Hover-Anzeigen der
// Wasserfaelle haben keine Zeigerhoehe.
function knownInfoAt(fHz, pxPerHz, reihe) {
  if (!knownCatalog || !knownEnabled || !(pxPerHz > 0)) return null;
  if (reihe === undefined || reihe === 'kanal') {
    const treffer = knownNearestChannels(fHz, pxPerHz);
    if (treffer) {
      const { best, gleiche } = treffer;
      const ohneRegion = knownDedupe(knownChannelsInRange(best.freq_hz, best.freq_hz)
        .filter(c => !(c.tags || []).includes('fnv')));
      return { freq_hz: best.freq_hz, channels: gleiche, reihe: reihe || 'kanal',
               hidden: ohneRegion.length - gleiche.length, bands: [] };
    }
    if (reihe === 'kanal') return null;   // in der Kanalreihe gibt es keine Baender
  }
  const nurZuweisung = reihe === 'fnv';
  const nurDienst = reihe === 'dienst';
  const baender = knownCatalog.bands
    .filter(b => b.start_hz <= fHz && fHz <= b.stop_hz
      && !(nurZuweisung && b.layer !== 'zuweisung')
      && !(nurDienst && b.layer === 'zuweisung'))
    .sort((a, b2) => (a.layer === 'zuweisung' ? 0 : 1) - (b2.layer === 'zuweisung' ? 0 : 1));
  // FNV-Einzelfrequenz in Reichweite? Toleranz wie knownNearestChannels. Sie
  // gehoert zur Zuweisungsebene und entfaellt deshalb in der Dienstreihe.
  let fnvBest = null, fnvD = Infinity;
  if (!nurDienst) {
    const fnvTol = 5 / pxPerHz;
    for (const c of knownFnvChannels(fHz - fnvTol, fHz + fnvTol)) {
      const d = Math.abs(c.freq_hz - fHz);
      if (d < fnvD) { fnvBest = c; fnvD = d; }
    }
  }
  if (!baender.length && !fnvBest) return null;
  const bands = baender.map(b => {
    const s = (b.segments || []).find(s => s.start_hz <= fHz && fHz <= s.stop_hz);
    return { title: b.title, seg: s ? s.title : null, fnv: b.layer === 'zuweisung' };
  });
  if (fnvBest) bands.unshift({ title: fnvBest.label, seg: (fnvBest.freq_hz / 1e6).toFixed(5) + ' MHz', fnv: true });
  return { freq_hz: null, channels: [], hidden: 0, bands, reihe: reihe || null };
}

// Zeilen des Streifen-Tooltips zu einer Frequenz (Text-Fassung, siehe
// knownRenderTip fuer die grafische Fassung). Kanaele: bei Mehrfachbelegung
// eine Kopfzeile "n stations on X.XXXXX MHz", danach je Kanal eine Zeile
// "label · demod bw kHz · service"; bei genau einem Treffer eine Zeile wie
// bisher (label · MHz · demod bw · service). Sonst ALLE Baender, die die
// Frequenz beruehren, je Band eine eigene Zeile (FNV-Zuweisungsbaender
// zuerst, Praefix "FNV: "). Kein Treffer -> [].
function knownLinesAt(fHz, pxPerHz) {
  const info = knownInfoAt(fHz, pxPerHz);
  if (!info) return [];
  if (info.channels.length) {
    if (info.channels.length > 1) {
      const kopf = info.channels.length + ' stations on ' + (info.freq_hz / 1e6).toFixed(5) + ' MHz';
      return [kopf, ...info.channels.map(c =>
        c.label + KNOWN_SEP + c.demod + ' ' + (c.bw_hz / 1000) + ' kHz' + KNOWN_SEP + c.service)];
    }
    const c = info.channels[0];
    return [c.label + KNOWN_SEP + (c.freq_hz / 1e6).toFixed(5) + ' MHz' + KNOWN_SEP
      + c.demod + ' ' + (c.bw_hz / 1000) + ' kHz' + KNOWN_SEP + c.service];
  }
  return info.bands.map(b => {
    const text = b.seg ? b.title + KNOWN_SEP + b.seg : b.title;
    return b.fnv ? 'FNV: ' + text : text;
  });
}

// Einzeiliger Katalogtext zu einer Frequenz fuer die Hover-Anzeigen der
// Wasserfaelle (koennen keinen Zeilenumbruch darstellen) - baut auf denselben
// Daten wie knownLinesAt auf. Kanalfall: bei Mehrfachbelegung ersetzt
// "(+n-1)" im Label die ausgeschriebene Liste, die uebrigen Felder (Frequenz/
// Demod/Service) kommen vom naechstgelegenen Kanal; die einzelnen Sender
// sieht man erst im Streifen-Tooltip (knownRenderTip). Bandfall: Zeilen aus
// knownLinesAt mit ' | ' verbunden (KNOWN_SEP bleibt innerhalb eines Bandes).
// Kein Treffer -> ''.
function knownTextAt(fHz, pxPerHz) {
  const info = knownInfoAt(fHz, pxPerHz);
  if (!info) return '';
  if (info.channels.length) {
    const c = info.channels[0];
    const label = info.channels.length > 1 ? c.label + ' (+' + (info.channels.length - 1) + ')' : c.label;
    return label + KNOWN_SEP + (c.freq_hz / 1e6).toFixed(5) + ' MHz' + KNOWN_SEP
      + c.demod + ' ' + (c.bw_hz / 1000) + ' kHz' + KNOWN_SEP + c.service;
  }
  return knownLinesAt(fHz, pxPerHz).join(' | ');
}

// Eigener heller Streifen-Tooltip (bewusst NICHT das geteilte #tooltip - das
// bleibt dem Wasserfall-Hover, siehe attachTooltip weiter unten im Host).
// Raster mit ORS-Feldern statt Fliesstext, oberhalb des Streifens im
// Spektrum platziert, per Klick anheftbar (Textmarkierung/Kopieren). CSS als
// normaler String ohne Backslash (CLAUDE.md); Element einmalig je Seite
// angelegt (der Block laeuft auf vier Seiten, je genau einmal).
const KNOWN_TIP_CSS =
  '#knownTip { position:fixed; z-index:50; display:none; background:#f3efe4;'
  + ' color:#1c1c1c; border:1px solid #8a8570; border-radius:3px;'
  + ' padding:6px 9px; font:12px/1.35 sans-serif; box-shadow:0 2px 8px'
  + ' rgba(0,0,0,0.45); pointer-events:none; max-width:560px; }'
  + ' #knownTip.pinned { pointer-events:auto; user-select:text;'
  + ' border-color:#3a6a6a; box-shadow:0 0 0 2px rgba(58,106,106,0.35),'
  + ' 0 2px 8px rgba(0,0,0,0.45); }'
  + ' #knownTip .kt-head { font-weight:600; margin-bottom:4px; }'
  + ' #knownTip .kt-grid { display:grid; grid-template-columns:auto auto auto'
  + ' auto auto; column-gap:12px; row-gap:1px; }'
  + ' #knownTip .kt-grid span { white-space:nowrap; }'
  + ' #knownTip .kt-r { text-align:right; }'
  + ' #knownTip .kt-foot { margin-top:4px; color:#5a5a5a; font-size:11px; }'
  + ' #knownTip .kt-note { white-space:pre-wrap; overflow-wrap:break-word;'
  + ' color:#333; }'
  + ' #knownTip .kt-band { margin-top:4px; color:#333; }'
  + ' #knownTip .kt-fnv { color:#3a5a8a; }';

let knownTipEl = document.getElementById('knownTip');
if (!knownTipEl) {
  const knownTipStyle = document.createElement('style');
  knownTipStyle.id = 'knownTipStyle';
  knownTipStyle.textContent = KNOWN_TIP_CSS;
  document.body.appendChild(knownTipStyle);
  knownTipEl = document.createElement('div');
  knownTipEl.id = 'knownTip';
  document.body.appendChild(knownTipEl);
}

// Schluessel des zuletzt aufgebauten Inhalts - Neuaufbau des DOM nur bei
// Aenderung, sonst zieht attachKnownTooltip nur die Position nach.
let knownTipKey = null;
let knownTipPinned = false;

function knownTipKeyOf(info) {
  const vorsatz = (info.reihe || '') + ':';
  if (info.channels.length) return vorsatz + 'c:' + info.freq_hz;
  return vorsatz + 'b:' + info.bands.map(b => b.title + KNOWN_SEP + (b.seg || '')).join('||');
}

// Baut #knownTip per createElement/textContent neu auf (kein innerHTML).
function knownRenderTip(info) {
  const key = knownTipKeyOf(info);
  if (key === knownTipKey) return;
  knownTipKey = key;
  while (knownTipEl.firstChild) knownTipEl.removeChild(knownTipEl.firstChild);

  if (info.channels.length) {
    const kopf = document.createElement('div');
    kopf.className = 'kt-head';
    kopf.textContent = info.channels.length > 1
      ? info.channels.length + ' stations on ' + (info.freq_hz / 1e6).toFixed(5) + ' MHz'
      : info.channels[0].label + KNOWN_SEP + (info.freq_hz / 1e6).toFixed(5) + ' MHz';
    knownTipEl.appendChild(kopf);

    const grid = document.createElement('div');
    grid.className = 'kt-grid';
    for (const c of info.channels) {
      // Kanaele ohne jedes ORS-Zusatzfeld (Flugfunk, Amateurfunk): Spalte 1
      // bleibt das Label (programm || label greift ohnehin), Spalte 2 zeigt
      // den Dienst statt eines leeren Standorts.
      const hatZusatz = c.site !== undefined || c.programm !== undefined
        || c.bundesland !== undefined || c.pol !== undefined || c.erp_w !== undefined;
      const zellen = [
        c.programm || c.label,
        hatZusatz ? (c.site || '') : c.service,
        c.bundesland || '',
        c.pol || '',
        c.erp_w !== undefined ? (c.erp_w >= 1000 ? (c.erp_w / 1000) + ' kW' : c.erp_w + ' W') : '',
      ];
      zellen.forEach((text, i) => {
        const zelle = document.createElement('span');
        if (i === 4) zelle.className = 'kt-r';
        zelle.textContent = text;
        grid.appendChild(zelle);
      });
    }
    knownTipEl.appendChild(grid);

    // Fusszeile "demod bw kHz · service" nur, wenn ALLE Kanaele der Gruppe
    // dieselben Werte tragen - sonst waere eine gemeinsame Zeile irrefuehrend.
    // T-127: traegt der naechstgelegene Kanal eine Notiz, tritt sie an die
    // Stelle des Dienstes - der steht bei Kanaelen ohne ORS-Zusatzfelder
    // schon in Spalte 2 des Rasters. Bei ORS-Kanaelen zeigt Spalte 2 den
    // Standort, dort bleibt der Dienst stehen und die Notiz kommt dahinter.
    // Ohne Notiz bleibt die Zeile Wort fuer Wort wie bisher.
    const erste = info.channels[0];
    const einheitlich = info.channels.every(c =>
      c.demod === erste.demod && c.bw_hz === erste.bw_hz && c.service === erste.service);
    if (einheitlich) {
      const zeigtDienst = info.channels.some(c => c.site !== undefined
        || c.programm !== undefined || c.bundesland !== undefined
        || c.pol !== undefined || c.erp_w !== undefined);
      const notiz = (erste.notes || '').trim();
      const fussTeile = [erste.demod + ' ' + (erste.bw_hz / 1000) + ' kHz'];
      if (zeigtDienst || !notiz) fussTeile.push(erste.service);
      if (notiz) fussTeile.push(info.channels.length > 1 ? erste.label + ': ' + notiz : notiz);
      const fuss = document.createElement('div');
      fuss.className = notiz ? 'kt-foot kt-note' : 'kt-foot';
      fuss.textContent = fussTeile.join(KNOWN_SEP);
      knownTipEl.appendChild(fuss);
    }
    if (info.hidden > 0) {
      const versteckt = document.createElement('div');
      versteckt.className = 'kt-foot';
      versteckt.textContent = '+' + info.hidden + ' outside the region';
      knownTipEl.appendChild(versteckt);
    }
  }

  for (const b of info.bands) {
    const zeile = document.createElement('div');
    zeile.className = b.fnv ? 'kt-band kt-fnv' : 'kt-band';
    const text = b.seg ? b.title + KNOWN_SEP + b.seg : b.title;
    zeile.textContent = b.fnv ? 'FNV: ' + text : text;
    knownTipEl.appendChild(zeile);
  }
}

// Oberhalb des Streifens im Spektrum platziert (rect = Streifen-Canvas);
// Breite/Hoehe erst nach dem Rendern messbar. top haengt am Streifen, nicht
// an der Maus - nur bei zu wenig Platz darueber Fallback unter den Streifen.
// left folgt der Maus, auf den sichtbaren Bereich geklemmt.
function knownPositionTip(rect, clientX) {
  const w = knownTipEl.offsetWidth, h = knownTipEl.offsetHeight;
  let left = clientX - w / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  let top = rect.top - h - 8;
  if (top < 8) top = rect.bottom + 8;
  knownTipEl.style.left = left + 'px';
  knownTipEl.style.top = top + 'px';
}

// Hover: die Hoehe des Zeigers bestimmt die Reihe (T-133), knownInfoAt
// darin die Kanalgruppe bzw. die Baender, knownRenderTip zeigt sie im hellen
// Raster oberhalb des Streifens. Angeheftet (knownTipPinned)
// aendern Mausbewegung/Verlassen nichts mehr - siehe die Klick-/Escape-
// Listener unten.
function attachKnownTooltip(canvasId) {
  const canvas = el(canvasId);
  canvas.addEventListener('mousemove', e => {
    if (knownTipPinned) return;
    const range = knownLastRange[canvasId];
    if (!range || !knownCatalog || !knownEnabled) { knownTipEl.style.display = 'none'; knownTipKey = null; return; }
    const rect = canvas.getBoundingClientRect();
    const xPx = (e.clientX - rect.left) / rect.width * canvas.width;
    const yPx = (e.clientY - rect.top) / rect.height * canvas.height;
    const [loHz, hiHz] = range, pxPerHz = canvas.width / (hiHz - loHz);
    const fHz = loHz + xPx / pxPerHz;
    // T-133: auskunftspflichtig ist nur die Reihe unter dem Zeiger. Steht er
    // ausserhalb jeder gezeichneten Reihe, gibt es nichts zu zeigen.
    const wo = knownReiheBei(canvasId, yPx);
    if (!wo) { knownTipEl.style.display = 'none'; knownTipKey = null; return; }
    const info = knownInfoAt(fHz, pxPerHz, wo);
    if (!info) { knownTipEl.style.display = 'none'; knownTipKey = null; return; }
    knownRenderTip(info);
    knownTipEl.style.display = 'block';
    knownPositionTip(rect, e.clientX);
  });
  canvas.addEventListener('mouseleave', () => {
    if (!knownTipPinned) knownTipEl.style.display = 'none';
  });
  // Anheften: Klick auf den Streifen bei sichtbarem Tooltip. Geloest wird
  // ueber die document-Listener unten (ausserhalb von #knownTip, oder Escape).
  canvas.addEventListener('click', () => {
    if (knownTipEl.style.display === 'block') {
      knownTipPinned = true;
      knownTipEl.classList.add('pinned');
    }
  });
}
for (const id of KNOWN_STRIPS) attachKnownTooltip(id);

// Loesen des angehefteten Tooltips: Klick ausserhalb von #knownTip (Capture-
// Phase, damit der pinnende Klick selbst nicht sofort wieder loest - der
// pinnende Klick setzt knownTipPinned erst danach im Bubble/Target-Handler
// oben) oder Escape. Klick INNERHALB bleibt wirkungslos (Textmarkierung).
document.addEventListener('click', e => {
  if (!knownTipPinned || knownTipEl.contains(e.target)) return;
  knownTipPinned = false;
  knownTipEl.classList.remove('pinned');
  knownTipEl.style.display = 'none';
}, true);
document.addEventListener('keydown', e => {
  if (knownTipPinned && e.key === 'Escape') {
    knownTipPinned = false;
    knownTipEl.classList.remove('pinned');
    knownTipEl.style.display = 'none';
  }
});
// <<< KNOWN_STRIP_BLOCK_END

async function fetchJSON(url) {
  const res = await fetch(url);
  let data = null;
  try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
  if (!res.ok) {
    const msg = errText(data, res.status);
    throw new Error(msg);
  }
  return data;
}

function showView(name) {
  ['list', 'player'].forEach(v => {
    el('view-' + v).style.display = (v === name) ? 'block' : 'none';
  });
}

// dieselbe Farbmapping-Logik wie im Live-Wasserfall (waterfall.py) und in
// analyze_sessions.py - blau (leise) ueber gruen/gelb nach rot (laut).
function levelToColorRGB(v, lo, hi) {
  let t = (v - lo) / (hi - lo);
  t = Math.max(0, Math.min(1, t));
  const stops = [
    [0.00, 10, 10, 60], [0.25, 20, 90, 200], [0.50, 40, 200, 120],
    [0.75, 240, 220, 40], [1.00, 230, 30, 30],
  ];
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, r0, g0, b0] = stops[i], [t1, r1, g1, b1] = stops[i + 1];
    if (t >= t0 && t <= t1) {
      const f = (t - t0) / (t1 - t0);
      return [r0 + f * (r1 - r0) | 0, g0 + f * (g1 - g0) | 0, b0 + f * (b1 - b0) | 0];
    }
  }
  return [230, 30, 30];
}

// base64 -> Int16Array (Level sind ROH, Deci-dBuV; /10 erst im Anzeigepfad)
function decodeLevels(b64) {
  const bin = atob(b64);
  const buf = new ArrayBuffer(bin.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i);
  return new Int16Array(buf);
}

// ------------------------------------------------------------------ Liste
// sortierbar, durchsuchbar, mit editierbarem Kurztext je Aufnahme.

// Rohdaten plus Sortier-/Suchzustand; die Tabelle wird daraus jederzeit neu
// aufgebaut (renderRecordingTable) - Sortierung und Suche laufen clientseitig.
let recordingItems = [];
let pendingJobDeep = null;
let sortKey = 'date';   // COLUMNS[].key
let sortDir = -1;       // 1 = aufsteigend, -1 = absteigend (Start: neueste zuerst)
let searchTerm = '';    // Suchbegriff, bereits kleingeschrieben

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
// ISO 8601 (UTC, endet auf +00:00 oder Z) in Datum bzw. Uhrzeit zerlegen -
// bewusst OHNE Zeitzonen-Umrechnung, damit die UTC-Zeit der Aufnahme erhalten
// bleibt (Aufnahmen speichern UTC, so wie bisher in der Liste gezeigt).
function isoDateUtc(iso) {
  if (!iso) return '-';
  const t = iso.indexOf('T');
  return t > 0 ? iso.slice(0, t) : iso;
}
function isoTimeUtc(iso) {
  if (!iso) return '-';
  const t = iso.indexOf('T');
  if (t < 0) return '-';
  let s = iso.slice(t + 1);
  const plus = s.indexOf('+');
  if (plus >= 0) s = s.slice(0, plus);
  s = s.replace('Z', '');
  if (s.length >= 8) s = s.slice(0, 8);   // nur HH:MM:SS
  return s + ' UTC';
}
function labelInputHTML(it) {
  return '<input type="text" maxlength="128" data-path="' + esc(it.path) +
         '" value="' + esc(it.label || '') + '">';
}

// Job-Badge (Long-Run-Aufnahmen, siehe list_recordings()/job-Feld): Kurztext
// (leer -> job_id) + Teil-Name aus dem Dateinamen (..._part01.specbin ->
// "part01"; Dateien von vor T-1821a heissen ..._teil01.specbin und werden
// ebenso gelesen), plus ein Hinweis, falls dies die gerade noch beschriebene
// aktuelle Teildatei ist (Sidecar running:true, siehe it.running). Ad-hoc-
// Aufnahmen haben kein it.job -> leere Zelle, unveraendert.
function jobBadgeHTML(it) {
  if (!it.job) return '';
  const m = (it.name || '').match(/_(?:teil|part)(\\d+)\\.specbin$/);
  const teilText = m ? 'part' + m[1] : '';
  const jobText = esc(it.job.label || it.job.job_id || '?');
  const title = esc([labelOf('art', it.job.type), labelOf('zustand', it.job.state)].filter(Boolean).join(' / '));
  const running = it.running ? ' <span class="job-running">' + esc(labelOf('zustand', 'laeuft')) + '</span>' : '';
  return '<span class="job-badge" title="' + title + '">' + jobText +
    (teilText ? ' &middot; ' + esc(teilText) : '') + '</span>' + running;
}

// Spaltendefinition: Kopftext, Sortier-Zugriff (num=true -> numerisch) und
// Zellen-Renderer je Spalte an einer Stelle. Datum/Beginn/Ende aus dem ISO-
// String (UTC); die technischen Spalten bleiben erhalten.
const COLUMNS = [
  { key:'date',   label:'Date',    num:false,
    sort: it => it.start_iso || '',
    cell: it => isoDateUtc(it.start_iso) },
  { key:'begin',  label:'Start',   num:false,
    sort: it => it.start_iso || '',
    cell: it => isoTimeUtc(it.start_iso) },
  { key:'end',    label:'End',     num:false,
    sort: it => it.stop_iso || '',
    cell: it => isoTimeUtc(it.stop_iso) },
  // Technische Spalten rechtsbuendig (cls 'num' gilt fuer th und td, siehe
  // buildHead); Mittenfrequenz zusaetzlich hellgruen (cls 'freq').
  { key:'center', label:'Center frequency', num:true, cls:'num freq',
    sort: it => it.center_hz || 0,
    cell: it => (it.center_hz != null ? (it.center_hz / 1e6).toFixed(4) + ' MHz' : '-') },
  { key:'span',   label:'Span',     num:true,  cls:'num',
    sort: it => it.span_hz || 0,
    cell: it => (it.span_hz != null ? (it.span_hz / 1e3).toFixed(0) + ' kHz' : '-') },
  { key:'bw',     label:'BW',       num:true,  cls:'num',
    sort: it => it.bw_hz || 0,
    cell: it => (it.bw_hz != null ? (it.bw_hz / 1e3).toFixed(1) + ' kHz' : '-') },
  { key:'demod',  label:'Demod',    num:false, cls:'num',
    sort: it => it.demod || '',
    cell: it => esc(it.demod || '-') },
  { key:'bins',   label:'Bins',     num:true,  cls:'num',
    sort: it => it.n_window || 0,
    cell: it => (it.n_window != null ? it.n_window : '-') },
  { key:'frames', label:'Frames',   num:true,  cls:'num',
    sort: it => it.frames_written || 0,
    cell: it => (it.frames_written != null ? it.frames_written : '-') },
  { key:'job',    label:'Job',      num:false,
    sort: it => (it.job ? (it.job.label || it.job.job_id || '') : '').toLowerCase(),
    cell: it => jobBadgeHTML(it) },
  { key:'name',   label:'File',    num:false,
    sort: it => (it.name || '').toLowerCase(),
    cell: it => esc(it.name) },
  { key:'label',  label:'Label', num:false, cls:'label-cell',
    sort: it => (it.label || '').toLowerCase(),
    cell: it => labelInputHTML(it) },
  { key:'actions', label:'Action',  num:false, cls:'actions-cell',
    sort: () => 0,
    cell: it => '<button class="deleteBtn danger" data-path="' + esc(it.path) + '">Delete</button>' +
      '<span class="delete-error"></span>' },
];

// Kopfzeile mit Sortierpfeil. Klick: erst aufsteigend, erneuter Klick kehrt um.
function buildHead() {
  const head = el('listHead');
  head.innerHTML = '';
  COLUMNS.forEach(col => {
    const th = document.createElement('th');
    th.className = 'sortable' + (col.cls ? ' ' + col.cls : '');
    let txt = col.label;
    if (sortKey === col.key) txt += ' <span class="arrow">' + (sortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
    th.innerHTML = txt;
    th.onclick = () => {
      if (sortKey === col.key) sortDir = -sortDir;
      else { sortKey = col.key; sortDir = 1; }
      renderRecordingTable();
    };
    head.appendChild(th);
  });
}

async function loadRecordingList() {
  buildHead();
  const tbody = el('recListBody');
  tbody.innerHTML = '<tr><td colspan="' + COLUMNS.length + '">loading ...</td></tr>';
  try {
    recordingItems = await fetchJSON('/api/recordings');
    renderRecordingTable();
    if (pendingJobDeep) {
      const jid = pendingJobDeep.toLowerCase();
      pendingJobDeep = null;
      const parts = recordingItems.filter(it => it.valid !== false && it.job &&
        (it.job.job_id || '').toLowerCase() === jid);
      if (parts.length === 1) openRecording(parts[0].path);
    }
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="' + COLUMNS.length +
      '" class="err">Error: ' + e.message + '</td></tr>';
  }
}

// Baut die Tabelle aus recordingItems unter Beruecksichtigung von Suche und
// Sortierung neu auf. Ungueltige Aufnahmen bleiben sichtbar und stehen am Ende.
function renderRecordingTable() {
  buildHead();
  const tbody = el('recListBody');
  tbody.innerHTML = '';

  let rows = recordingItems.slice();
  if (searchTerm) {
    rows = rows.filter(it =>
      (it.name || '').toLowerCase().includes(searchTerm) ||
      (it.label || '').toLowerCase().includes(searchTerm) ||
      (it.job && ((it.job.label || '').toLowerCase().includes(searchTerm) ||
                  (it.job.job_id || '').toLowerCase().includes(searchTerm))));
  }
  const col = COLUMNS.find(c => c.key === sortKey) || COLUMNS[0];
  rows.sort((a, b) => {
    if (!a.valid && !b.valid) return 0;
    if (!a.valid) return 1;
    if (!b.valid) return -1;
    const va = col.sort(a), vb = col.sort(b);
    const cmp = col.num ? (va - vb) : cmpText(va, vb);
    return cmp * sortDir;
  });

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="' + COLUMNS.length + '">no matching entries</td></tr>';
    return;
  }

  rows.forEach(it => {
    const tr = document.createElement('tr');
    if (!it.valid) {
      // Dateiname steht nicht mehr in Spalte 1, daher eine Zelle ueber die volle Breite.
      tr.innerHTML = '<td colspan="' + COLUMNS.length + '" class="err">' +
        esc(it.name) + ' — ' + esc(it.reason || 'invalid') + '</td>';
      tbody.appendChild(tr);
      return;
    }
    tr.className = 'rec-row';
    tr.innerHTML = COLUMNS.map(c =>
      '<td' + (c.cls ? ' class="' + c.cls + '"' : '') + '>' + c.cell(it) + '</td>').join('');
    tr.onclick = () => openRecording(it.path);
    tbody.appendChild(tr);

    // Kurztext-Feld verdrahten: Klick nicht an die Zeile durchreichen.
    const input = tr.querySelector('input[data-path]');
    if (input) {
      input.onclick = ev => ev.stopPropagation();
      input.onkeydown = ev => { if (ev.key === 'Enter') input.blur(); };
      input.onchange = () => saveLabel(input, it);
    }

    // Loesch-Knopf verdrahten: Klick nicht an die Zeile durchreichen,
    // Bestaetigung per confirm(), Fehlertext (z.B. 409 bei laufender/
    // unvollstaendiger Aufnahme oder Long-Run-Teildatei) erscheint sichtbar
    // in der Zeile.
    const delBtn = tr.querySelector('button.deleteBtn');
    if (delBtn) {
      delBtn.onclick = ev => {
        ev.stopPropagation();
        if (!confirm('Delete recording "' + it.name + '" permanently? ' +
            '(including findings, label and note - cannot be undone)')) return;
        deleteRecording(delBtn, it);
      };
    }
  });
}

// Kurztext speichern (POST /api/label). Erfolg -> kurzer gruener Rahmen.
async function saveLabel(input, it) {
  const text = input.value;
  try {
    const res = await fetch('/api/label', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: it.path, text: text }),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
    if (!res.ok) throw new Error(errText(data, res.status));
    it.label = (data && data.label != null) ? data.label : text;
    input.value = it.label;
    input.classList.add('saved');
    setTimeout(() => input.classList.remove('saved'), 800);
  } catch (e) {
    input.classList.add('err');
    input.title = 'Saving failed: ' + e.message;
  }
}

// Aufnahme komplett loeschen (POST /api/delete). Erfolg -> Liste neu laden;
// Schutzverstoss (409) -> Fehlertext in der Zeile.
async function deleteRecording(btn, it) {
  const errSpan = btn.parentElement.querySelector('.delete-error');
  errSpan.textContent = '';
  try {
    const res = await fetch('/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: it.path }),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
    if (!res.ok) throw new Error(errText(data, res.status));
    await loadRecordingList();
  } catch (e) {
    errSpan.textContent = e.message;
  }
}

// Suchfeld: bei jeder Eingabe filtern (rein clientseitig).
el('searchInput').oninput = e => {
  searchTerm = e.target.value.trim().toLowerCase();
  renderRecordingTable();
};

el('openPathBtn').onclick = () => {
  const p = el('pathInput').value.trim();
  if (p) openRecording(p);
};
el('backToListBtn').onclick = () => { stopPlayback(); showView('list'); };

// ------------------------------------------------------------------ Player-Zustand

let R = null;          // Kopfdaten (recording_meta)
let M = null;          // Int16Array (nFrames * nWindow), row-major, roh
let peak = null;       // Int16Array(nWindow): Peak-Hold ueber alle Frames
let cur = 0;           // aktueller Frame-Index
let selBin = 0;        // gewaehlter Bin (Ein-Bin-ueber-Zeit)
let scaleMin = -10, scaleMax = 70;   // Wasserfall-Farbskala (Auto + Regler)
let ampMin = -20, ampMax = 20;       // feste y-Achse fuer Spektrum/Pegel (aus Daten)
let playing = false, playTimer = null, fps = 10;
let spectroCache = null;   // Offscreen-Canvas: Spektrogramm+Luecken vorgezeichnet
let wfHeight = 600;        // volle Pixelhoehe des Wasserfall-Canvas (>= nFrames)
let scaleDebounce = null;  // verzoegert den teuren Cache-Neubau beim Skala-Ziehen

async function openRecording(path) {
  el('pathError').textContent = '';
  el('noteText').value = '';   // keine Notiz der vorigen Aufnahme stehen lassen
  el('noteHint').textContent = ' ';
  try {
    stopPlayback();
    R = await fetchJSON('/api/recording?path=' + encodeURIComponent(path));
    const fr = await fetchJSON('/api/frames?path=' + encodeURIComponent(path));
    M = decodeLevels(fr.levels_b64);
    // Falls capped: R.times/n_frames beziehen sich auf die volle Aufnahme,
    // die Matrix aber nur auf die ersten n. Auf die gelieferten Frames kappen.
    R.n_frames = fr.n_frames;
    R.times = fr.times;
    R.seqs = fr.seqs;
    R.capped = fr.capped;
    R.total_frames = fr.total_frames;
    computePeakHold();
    computeAmpRange();       // feste y-Achse der rechten Displays (Signalmaximum)
    autoScaleWaterfall();    // Wasserfall-Farbskala datengetrieben vorbelegen
    cur = 0;
    selBin = Math.floor(R.n_window / 2);
    el('frameSlider').max = String(R.n_frames - 1);
    el('frameSlider').value = '0';
    renderHeader();
    showView('player');
    // Layout erst nach dem Sichtbarschalten vermessen: die Viewport-Hoehe wird
    // exakt auf die rechte Spalte gesetzt, dann die interne Wasserfallhoehe
    // (>= Viewport, mind. 1 Pixelzeile je Frame). rAF stellt sicher, dass das
    // Layout bereits steht.
    requestAnimationFrame(() => { fitWaterfallHeight(); sizeWaterfall(); renderAll(); });
    loadBefund(path);   // Auto-Analyse-Befund asynchron nachladen (blockiert den Wasserfall nicht)
    loadNote(path);     // gespeicherte Notiz zu dieser Aufnahme nachladen (nicht blockierend)
  } catch (e) {
    el('pathError').textContent = 'Error: ' + e.message;
    showView('list');
  }
}

// ---- Befund-Navigator: Auto-Analyse-Befund laden und in die Leiste rendern ----

// Setzt in allen fuenf Gruppen denselben Platzhaltertext.
function befundPlaceholder(text) {
  ['navCarriers', 'navBursts', 'navEvents', 'navComb', 'navPeriodic'].forEach(id => {
    el(id).innerHTML = '<div class="nav-empty">' + text + '</div>';
  });
}

// Laedt den Befund asynchron (Lazy-Fill kann beim ersten Oeffnen dauern) und
// rendert ihn; blockiert den Wasserfall NICHT (nicht awaited im Aufrufer).
async function loadBefund(path) {
  befundPlaceholder('&ndash; analyzing &ndash;');
  try {
    const rep = await fetchJSON('/api/befund?path=' + encodeURIComponent(path));
    renderBefund(rep);
  } catch (e) {
    befundPlaceholder('analysis failed');
  }
}

// ---- Notiz je Aufnahme: mehrzeiliger Freitext, getrennt vom Kurztext und
// vom regenerierbaren Befund-Sidecar gespeichert (siehe NOTES im Server). ----

async function loadNote(path) {
  const ta = el('noteText');
  ta.dataset.path = path;
  try {
    const data = await fetchJSON('/api/note?path=' + encodeURIComponent(path));
    ta.value = data.note || '';
    el('noteHint').textContent = ' ';
  } catch (e) {
    el('noteHint').textContent = 'note could not be loaded';
  }
}

async function saveNote() {
  const ta = el('noteText');
  const path = ta.dataset.path;
  if (!path) return;
  try {
    const res = await fetch('/api/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path, note: ta.value }),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
    if (!res.ok) throw new Error(errText(data, res.status));
    ta.value = (data && data.note != null) ? data.note : ta.value;
    ta.classList.add('saved');
    setTimeout(() => ta.classList.remove('saved'), 1200);
    el('noteHint').textContent = 'Saved ' + isoTime(new Date());
  } catch (e) {
    el('noteHint').textContent = 'Saving failed';
  }
}

el('noteText').onchange = saveNote;

function fmtMHz(hz) { return (hz / 1e6).toFixed(4); }

// Eine Befundzeile; data-freq (Hz) / data-t (s) tragen die Sprungkoordinaten
// fuer die spaetere Klick-Verdrahtung (hier noch ohne Handler).
function befundRow(text, freqHz, tOffset) {
  const d = document.createElement('div');
  d.className = 'nav-item';
  d.textContent = text;
  if (freqHz != null) d.dataset.freq = String(freqHz);
  if (tOffset != null) d.dataset.t = String(tOffset);
  return d;
}

function fillGroup(id, rows) {
  const box = el(id);
  box.innerHTML = '';
  if (!rows.length) { box.innerHTML = '<div class="nav-empty">&ndash; none &ndash;</div>'; return; }
  rows.forEach(r => box.appendChild(r));
}

// Rendert den Befund (erstes/einziges Segment) in die fuenf Gruppen.
// Kopfzeile-Zaehler in der jeweiligen <summary> setzen (immer mit (n), auch (0)).
function setCount(listId, label, n) {
  const det = el(listId).closest('details');
  if (det) { const sum = det.querySelector('summary'); if (sum) sum.textContent = label + ' (' + n + ')'; }
}

// Zahlenformatierer fuer buendige Spalten (feste Nachkommastellen).
function fmt1(x) { return Number(x).toFixed(1); }
function fmt2(x) { return Number(x).toFixed(2); }
function fmt3(x) { return Number(x).toFixed(3); }

function navCell(text, cls) {
  const td = document.createElement('td');
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

// Baut eine Tabelle mit Kopfzeile; jede Datenzeile ist klickbar (tr.nav-item mit
// data-freq/data-t). headers = [{label, cls}], rows = [{freq, tOff, cells:[{t, cls}]}].
function navTable(headers, rows) {
  const table = document.createElement('table');
  table.className = 'nav-table';
  const thead = document.createElement('thead');
  const htr = document.createElement('tr');
  headers.forEach(h => {
    const th = document.createElement('th');
    th.textContent = h.label;
    if (h.cls) th.className = h.cls;
    htr.appendChild(th);
  });
  thead.appendChild(htr);
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  rows.forEach(r => {
    const tr = document.createElement('tr');
    tr.className = 'nav-item';
    if (r.freq != null) tr.dataset.freq = String(r.freq);
    if (r.tOff != null) tr.dataset.t = String(r.tOff);
    r.cells.forEach(c => tr.appendChild(navCell(c.t, c.cls)));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}

function fillTable(id, headers, rows, emptyText) {
  const box = el(id);
  box.innerHTML = '';
  if (!rows.length) {
    box.innerHTML = '<div class="nav-empty">' + (emptyText || '&ndash; none &ndash;') + '</div>';
    return;
  }
  box.appendChild(navTable(headers, rows));
}

function renderBefund(report) {
  const segs = (report && report.segments) ? report.segments : [];
  if (!segs.length) {
    ['navCarriers', 'navBursts', 'navEvents', 'navComb', 'navPeriodic'].forEach(id =>
      el(id).innerHTML = '<div class="nav-empty">&ndash; no findings &ndash;</div>');
    setCount('navCarriers', 'Continuous carriers', 0);
    setCount('navBursts', 'Bursty channels', 0);
    setCount('navEvents', 'Strongest events', 0);
    setCount('navComb', 'Comb', 0);
    setCount('navPeriodic', 'Periodicity', 0);
    return;
  }
  const s = segs[0];

  // Dauertraeger: MHz | dBuV | duty
  const carriers = s.carriers || [];
  setCount('navCarriers', 'Continuous carriers', carriers.length);
  fillTable('navCarriers',
    [{label: 'MHz', cls: 'num'}, {label: 'dBµV', cls: 'num'}, {label: 'duty', cls: 'num'}],
    carriers.map(c => ({freq: c.f_peak_hz, tOff: null, cells: [
      {t: fmtMHz(c.f_peak_hz), cls: 'num'}, {t: fmt1(c.median_dbuv), cls: 'num'}, {t: fmt1(c.duty_pct) + '%', cls: 'num'}]})));

  // Bursthafte Kanaele: MHz | Peak dBuV | duty
  const bursts = s.bursts || [];
  setCount('navBursts', 'Bursty channels', bursts.length);
  fillTable('navBursts',
    [{label: 'MHz', cls: 'num'}, {label: 'Peak dBµV', cls: 'num'}, {label: 'duty', cls: 'num'}],
    bursts.map(b => ({freq: b.f_peak_hz, tOff: null, cells: [
      {t: fmtMHz(b.f_peak_hz), cls: 'num'}, {t: fmt1(b.peak_dbuv), cls: 'num'}, {t: fmt1(b.duty_pct) + '%', cls: 'num'}]})));

  // Staerkste Ereignisse: MHz | dBuV | Zeit
  const events = s.strongest_events || [];
  setCount('navEvents', 'Strongest events', events.length);
  fillTable('navEvents',
    [{label: 'MHz', cls: 'num'}, {label: 'dBµV', cls: 'num'}, {label: 'Time', cls: 'num'}],
    events.map(ev => ({freq: ev.f_hz, tOff: ev.t_offset_s, cells: [
      {t: fmtMHz(ev.f_hz), cls: 'num'}, {t: fmt1(ev.peak_dbuv), cls: 'num'}, {t: 't+' + fmt1(ev.t_offset_s) + ' s', cls: 'num'}]})));

  // Kamm: beschreibende Textzeilen (keine Tabelle). verdict ist seit T-1829 ein
  // Code aus session_analyze.py; ein unbekannter Wert wird unveraendert gezeigt.
  const COMB_VERDICT = {
    possible_artifact: 'very regular comb across the whole span -> possible measurement/scalloping artifact, verify off-air',
    likely_channels: 'comb present, less regular -> likely real channelized activity'
  };
  const comb = s.comb || {};
  setCount('navComb', 'Comb', comb.present ? 1 : 0);
  if (comb.present) {
    const box = el('navComb'); box.innerHTML = '';
    box.appendChild(befundRow('~' + comb.spacing_khz + ' kHz  (regularity ' + comb.regularity + ')', null, null));
    box.appendChild(befundRow(COMB_VERDICT[comb.verdict] || comb.verdict || '', null, null));
  } else {
    fillGroup('navComb', []);
  }

  // Periodik: periodische Emitter (MHz | BW | Periode ms | +/- | Wahrsch.),
  // je Emitter eine Klassifikationszeile darunter; danach Band-Residuen
  // (Lags, die kein Emitter erklaert) wie bisher als Periode|r.
  const emitters = s.periodic_emitters || [];
  const resid = (s.autocorr_band_residual || s.autocorr_band || []).filter(p => p[1] >= 0.30);
  setCount('navPeriodic', 'Periodicity', emitters.length + resid.length);
  const pbox = el('navPeriodic'); pbox.innerHTML = '';
  if (!emitters.length && !resid.length) {
    pbox.innerHTML = '<div class="nav-empty">no fixed period</div>';
  } else {
    if (emitters.length) {
      const h = document.createElement('div'); h.className = 'nav-subhead'; h.textContent = 'Emitter'; pbox.appendChild(h);
      emitters.forEach(e => {
        pbox.appendChild(navTable(
          [{label: 'MHz', cls: 'num'}, {label: 'BW kHz', cls: 'num'}, {label: 'T ms', cls: 'num'}, {label: '±', cls: 'num'}, {label: 'p', cls: 'num'}],
          [{freq: e.f_center_hz, tOff: null, cells: [
            {t: fmtMHz(e.f_center_hz), cls: 'num'}, {t: fmt1(e.bw_hz / 1000), cls: 'num'},
            {t: fmt1(e.period_ms), cls: 'num'}, {t: fmt1(e.jitter_ms), cls: 'num'}, {t: fmt2(e.confidence), cls: 'num'}]}]));
        pbox.appendChild(befundRow(
          labelOf('level', e.level) + ' · ' + labelOf('bw_class', e.bw_class) + ' · ' + labelOf('period_class', e.period_class) + ' · on ' + fmt1(e.on_ms) + ' ms · ' + e.n_cycles + ' ' + pluralWort(e.n_cycles, 'cycle', 'cycles'),
          e.f_center_hz, null));
      });
    }
    if (resid.length) {
      const h = document.createElement('div'); h.className = 'nav-subhead'; h.textContent = 'Band (remainder)'; pbox.appendChild(h);
      pbox.appendChild(navTable(
        [{label: 'Period [s]', cls: 'num'}, {label: 'r', cls: 'num'}],
        resid.map(p => ({freq: null, tOff: null, cells: [{t: fmt2(p[0]), cls: 'num'}, {t: fmt3(p[1]), cls: 'num'}]}))));
    }
  }
}

// Klick auf eine Befundzeile: auf ihre Frequenz (und bei Ereignissen auf ihre
// Zeit) im Wasserfall positionieren. Nutzt dieselben Bausteine wie der
// Wasserfall-Scrubber (selBin / setCur / renderFrame).
function jumpToBefund(item) {
  if (!R) return;
  const hasFreq = item.dataset.freq != null && item.dataset.freq !== '';
  const hasT = item.dataset.t != null && item.dataset.t !== '';
  if (!hasFreq && !hasT) return;

  // Zeile hervorheben (nur eine aktiv)
  document.querySelectorAll('#navCol .nav-item.active').forEach(n => n.classList.remove('active'));
  item.classList.add('active');

  if (hasFreq) {
    const freq = parseFloat(item.dataset.freq);
    selBin = Math.max(0, Math.min(R.n_window - 1,
                                  Math.round((freq - R.bin0_hz) / R.step_hz)));
  }
  if (hasT) {
    // naechstliegenden Frame zur Ziel-Zeit suchen (wie der Pegelverlauf-Scrubber)
    const tTarget = R.times[0] + parseFloat(item.dataset.t);
    let best = 0, bestD = Infinity;
    for (let k = 0; k < R.n_frames; k++) {
      const d = Math.abs(R.times[k] - tTarget);
      if (d < bestD) { bestD = d; best = k; }
    }
    setCur(best, true);        // Zeit setzen + Wasserfall dorthin scrollen
  } else {
    renderFrame(false);        // nur Frequenz: neu zeichnen, Zeit bleibt
  }
}

function sizeWaterfall() {
  const vp = el('spectroViewport');
  // Breite: der Datenteil nutzt die freie Fensterbreite neben allem anderen.
  // T-1409a: seit T-1402 sind die drei Spalten Karten (je 12 px Innenabstand
  // links/rechts + 1 px Rahmen) und das Body-Padding betraegt 20 px statt 16 -
  // die alte Konstante (983) rechnete das Spektrogramm um ~52 px zu breit und
  // schob die rechte Spalte aus dem Fenster. Zusammensetzung jetzt:
  //    40  Body-Padding links+rechts (BASE_CSS)
  //   260  Befund-Navigator (border-box)
  //    32  zwei Spaltenluecken je 16
  //    26  Karte der Wasserfall-Spalte (2x12 Innenabstand + 2x1 Rahmen)
  //    21  Viewport-Rahmen + Scrollbalken
  //   656  rechte Spalte: 66 Gutter + 4 Luecke + 560 Plot + 26 Karte
  // Mindestens der bisherige Wert (720), Vielfache von 4. Achse und Katalog-
  // Streifen folgen mit; der resize-Handler ruft diese Funktion ohnehin auf,
  // die Breite passt sich also ohne Neuladen an (Cache-Neubau in renderAll).
  const SIDE_PX = 40 + 260 + 32 + 26 + 21 + 656;
  const gutterW = el('spectroGutter').width;
  const dataW = Math.max(720, Math.min(3200,
    Math.floor((window.innerWidth - SIDE_PX - gutterW) / 4) * 4));
  el('spectroCanvas').width = dataW;
  el('spectroFreqAxis').width = gutterW + dataW;
  el('known-w').width = dataW - 4;
  const viewH = Math.max(200, vp.clientHeight);
  wfHeight = Math.max(R.n_frames, viewH);
  el('spectroCanvas').height = wfHeight;
  el('spectroGutter').height = wfHeight;
}

// Setzt die Hoehe des scrollbaren Wasserfall-Viewports: vom Viewport-Oberrand
// bis kurz vor den Fensterboden (siehe BELOW_PX). Die Breite setzt sizeWaterfall().
function fitWaterfallHeight() {
  const vp = el('spectroViewport');
  const vpTop = vp.getBoundingClientRect().top;
  // Bisher: Unterkante = Pegel-Zeitachse der rechten Spalte. Jetzt: bis kurz
  // vor den Fensterboden, abzueglich Frequenzachse, Katalog-Streifen und
  // Player-Leiste unter dem Viewport (BELOW_PX - einzige Stellschraube).
  // Die rechte Spalte behaelt ihre bisherige Hoehe.
  const BELOW_PX = 150;
  const bottom = window.innerHeight - BELOW_PX;
  vp.style.height = Math.max(200, Math.round(bottom - vpTop)) + 'px';
}

// Perzentile der geladenen Matrix (Rueckgabe in dBµV). Arbeitet auf einem
// Subsample - das reicht fuer eine stabile Skalenwahl und bleibt auch bei
// grossen Aufnahmen schnell. fracs sind Anteile 0..1, Ergebnis in Reihenfolge.
function percentiles(fracs) {
  const stepN = Math.max(1, Math.floor(M.length / 60000));
  const s = [];
  for (let i = 0; i < M.length; i += stepN) s.push(M[i]);
  s.sort((a, b) => a - b);
  return fracs.map(f => {
    const idx = Math.max(0, Math.min(s.length - 1, Math.floor(f * (s.length - 1))));
    return s[idx] / 10;
  });
}

// Wasserfall-Farbskala aus den Daten: unterer Wert nahe dem Rauschsockel
// (30. Perzentil -> Rauschen wird dunkel), oberer Wert an den Signalspitzen
// (99,5. Perzentil). Setzt scaleMin/scaleMax samt Reglern/Labels.
function autoScaleWaterfall() {
  const [lo, hi] = percentiles([0.30, 0.995]);
  scaleMin = Math.max(-40, Math.min(120, Math.floor(lo)));
  scaleMax = Math.max(-40, Math.min(120, Math.ceil(hi)));
  if (scaleMax - scaleMin < 10) scaleMax = Math.min(120, scaleMin + 10);
  el('scaleMin').value = String(scaleMin);
  el('scaleMax').value = String(scaleMax);
  el('scaleMinLabel').textContent = scaleMin + ' dBµV';
  el('scaleMaxLabel').textContent = scaleMax + ' dBµV';
}

// Feste y-Achse fuer Spektrum + Pegelverlauf, ENTKOPPELT von der Wasserfall-
// Skala: Obergrenze = Signalmaximum der Aufnahme (Peak-Hold-Maximum; hoeher zu
// skalieren braechte nur leeren Raum oben), Untergrenze = unteres Perzentil
// (Rauschsockel). Einmal pro Aufnahme bestimmt, damit die Achse beim Abspielen
// nicht zappelt.
function computeAmpRange() {
  let pmax = -32768;
  for (let j = 0; j < R.n_window; j++) if (peak[j] > pmax) pmax = peak[j];
  ampMax = Math.ceil(pmax / 10);
  const [lo] = percentiles([0.01]);
  ampMin = Math.floor(lo);
  if (ampMax - ampMin < 10) ampMin = ampMax - 10;
}

function computePeakHold() {
  const nw = R.n_window, nf = R.n_frames;
  peak = new Int16Array(nw);
  for (let j = 0; j < nw; j++) peak[j] = -32768;
  for (let k = 0; k < nf; k++) {
    const base = k * nw;
    for (let j = 0; j < nw; j++) {
      const v = M[base + j];
      if (v > peak[j]) peak[j] = v;
    }
  }
}

function renderHeader() {
  const m = R.meta || {};
  el('recHeader').textContent =
    (m.center_hz / 1e6).toFixed(4) + ' MHz, Span ' + (m.span_hz / 1e3).toFixed(0) +
    ' kHz, BW ' + (m.bw_hz / 1e3).toFixed(1) + ' kHz, ' + (m.demod || '?');
  const dur = (R.times.length > 1) ? (R.times[R.times.length - 1] - R.times[0]) : 0;
  el('recMeta').textContent =
    R.n_frames + ' ' + pluralWort(R.n_frames, 'Frame', 'Frames') + ', ' + R.n_window + ' ' + pluralWort(R.n_window, 'Bin', 'Bins') + ', ' +
    (R.freq_lo_hz / 1e6).toFixed(4) + '-' + (R.freq_hi_hz / 1e6).toFixed(4) + ' MHz, ' +
    'Duration ' + dur.toFixed(1) + ' s, Start ' + (m.start_iso || '-');
  const hints = [];
  // m.running: Sidecar-Stand beim OEFFNEN (siehe recording_meta()/rec.meta) -
  // eine Job-Aufnahme, die zum Ladezeitpunkt noch lief (Checkpoint-Sidecar,
  // running:true). Der Reader liest nur den Stand von damals, kein Live-
  // Nachladen - deshalb der Zusatz "state at load time".
  if (m.running) hints.push('recording in progress - state at load time');
  if (R.capped) hints.push('only the first ' + R.n_frames + ' of ' + R.total_frames +
                           ' ' + pluralWort(R.total_frames, 'frame', 'frames') + ' loaded (cap)');
  if (R.truncated) hints.push('file was incomplete (truncated)');
  if (R.gaps && R.gaps.length) {
    const lost = R.gaps.reduce((a, g) => a + (g.missing > 0 ? g.missing : 0), 0);
    hints.push(R.gaps.length + ' ' + pluralWort(R.gaps.length, 'gap', 'gaps') + ', ~' + lost + ' ' + pluralWort(lost, 'packet', 'packets') + ' lost');
  }
  el('recHint').textContent = hints.join('  |  ');
}

// ------------------------------------------------------------------ Zeichnen

// Spektrogramm einmal in ein Offscreen-Canvas backen (Farben haengen an der
// Skala -> nur bei Lade-/Skalenwechsel neu). Danach blenden wir pro Frame nur
// das gecachte Bild + den beweglichen Zeit-Cursor ein -> fluessige Wiedergabe.
function buildSpectroCache() {
  const w = el('spectroCanvas').width, h = wfHeight;
  spectroCache = document.createElement('canvas');
  spectroCache.width = w; spectroCache.height = h;
  const ctx = spectroCache.getContext('2d');
  const img = ctx.createImageData(w, h);
  const nf = R.n_frames, nw = R.n_window;
  for (let y = 0; y < h; y++) {
    const k = Math.min(nf - 1, Math.floor(y * nf / h));
    const base = k * nw;
    for (let x = 0; x < w; x++) {
      const j = Math.min(nw - 1, Math.floor(x * nw / w));
      const col = levelToColorRGB(M[base + j] / 10, scaleMin, scaleMax);
      const p = (y * w + x) * 4;
      img.data[p] = col[0]; img.data[p + 1] = col[1]; img.data[p + 2] = col[2]; img.data[p + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  // Luecken als statische magenta Linien in den Cache backen.
  ctx.strokeStyle = 'rgb(255,0,200)';
  (R.gaps || []).forEach(g => {
    const y = Math.floor(g.at_frame / nf * h);
    ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(w, y + 0.5); ctx.stroke();
  });
}

function drawSpectro() {
  const c = el('spectroCanvas'), ctx = c.getContext('2d');
  ctx.drawImage(spectroCache, 0, 0);
  if (gridEnabled) drawFreqGridOverlay(ctx, c.width, c.height, R.freq_lo_hz, R.freq_hi_hz);
  // beweglicher Zeit-Cursor (aktueller Frame) als weisse Linie.
  const y = Math.floor(cur / Math.max(1, R.n_frames - 1) * (c.height - 1));
  ctx.strokeStyle = '#fff'; ctx.beginPath();
  ctx.moveTo(0, y + 0.5); ctx.lineTo(c.width, y + 0.5); ctx.stroke();
  // Frequenz-Cursor (gewaehlter Bin) als duenne senkrechte Linie.
  const x = Math.floor(selBin / Math.max(1, R.n_window - 1) * (c.width - 1));
  ctx.strokeStyle = 'rgba(255,255,255,0.4)'; ctx.beginPath();
  ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, c.height); ctx.stroke();
}

// haelt den Zeit-Cursor im sichtbaren Bereich des Viewports (nur bei
// Transport-Bewegung; beim Ziehen im Wasserfall selbst NICHT, sonst zappelt es)
function ensureCursorVisible() {
  const vp = el('spectroViewport');
  const y = cur / Math.max(1, R.n_frames - 1) * (wfHeight - 1);
  const top = vp.scrollTop, bot = top + vp.clientHeight;
  if (y < top + 16 || y > bot - 16) {
    vp.scrollTop = Math.max(0, y - vp.clientHeight / 2);
  }
}

// Spektrum-Linienplot (Pegel ueber Frequenz) fuer Frame cur + Peak-Hold.
function drawSpectrum() {
  const c = el('specCanvas'), ctx = c.getContext('2d');
  const w = c.width, h = c.height, nw = R.n_window;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  drawDbGrid(ctx, w, h);
  const yOf = v => h - (v - ampMin) / (ampMax - ampMin) * h;
  const xOf = j => (nw <= 1 ? 0 : j / (nw - 1) * w);
  // Peak-Hold (grau) zuerst, damit die aktuelle Kurve darueber liegt.
  ctx.strokeStyle = '#888'; ctx.beginPath();
  for (let j = 0; j < nw; j++) {
    const y = yOf(peak[j] / 10);
    if (j === 0) ctx.moveTo(xOf(j), y); else ctx.lineTo(xOf(j), y);
  }
  ctx.stroke();
  // aktueller Frame (cyan)
  const base = cur * nw;
  ctx.strokeStyle = '#3cf'; ctx.beginPath();
  for (let j = 0; j < nw; j++) {
    const y = yOf(M[base + j] / 10);
    if (j === 0) ctx.moveTo(xOf(j), y); else ctx.lineTo(xOf(j), y);
  }
  ctx.stroke();
  // Frequenz-Cursor (gewaehlter Bin)
  const x = xOf(selBin);
  ctx.strokeStyle = 'rgba(140,255,220,0.7)'; ctx.beginPath();
  ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, h); ctx.stroke();
}

// Pegelverlauf des gewaehlten Bins ueber die ECHTE Zeitachse (times).
function drawBinSeries() {
  const c = el('binCanvas'), ctx = c.getContext('2d');
  const w = c.width, h = c.height, nf = R.n_frames;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  drawDbGrid(ctx, w, h);
  const t0 = R.times[0], t1 = R.times[nf - 1];
  const span = (t1 > t0) ? (t1 - t0) : 1;
  const yOf = v => h - (v - ampMin) / (ampMax - ampMin) * h;
  const xOf = k => (R.times[k] - t0) / span * w;
  ctx.strokeStyle = '#8fd'; ctx.beginPath();
  for (let k = 0; k < nf; k++) {
    const v = M[k * R.n_window + selBin] / 10;
    if (k === 0) ctx.moveTo(xOf(k), yOf(v)); else ctx.lineTo(xOf(k), yOf(v));
  }
  ctx.stroke();
  // Zeit-Cursor beim aktuellen Frame
  const x = xOf(cur);
  ctx.strokeStyle = '#fff'; ctx.beginPath();
  ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, h); ctx.stroke();
}

// waagerechte dBµV-Gitterlinien (fuer Spektrum/Bin)
function drawDbGrid(ctx, w, h) {
  ctx.strokeStyle = '#222';
  const stepDb = niceStep(ampMax - ampMin);
  const first = Math.ceil(ampMin / stepDb) * stepDb;
  for (let v = first; v <= ampMax; v += stepDb) {
    const y = h - (v - ampMin) / (ampMax - ampMin) * h;
    ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(w, y + 0.5); ctx.stroke();
  }
}

function niceStep(range) {
  if (range <= 20) return 5;
  if (range <= 50) return 10;
  if (range <= 120) return 20;
  return 40;
}

function drawDbGutter(canvasId) {
  const c = el(canvasId), ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = '#aaa'; ctx.font = '9px sans-serif'; ctx.textAlign = 'right';
  const stepDb = niceStep(ampMax - ampMin);
  const first = Math.ceil(ampMin / stepDb) * stepDb;
  for (let v = first; v <= ampMax; v += stepDb) {
    const y = h - (v - ampMin) / (ampMax - ampMin) * h;
    ctx.fillText(v + '', w - 3, y + 3);
  }
}

// -- Nice-Ticks: dieselbe Achsenteilung wie in waterfall.py (niceStepFor/
// computeNiceTicks). Kleinste "runde" Schrittweite aus {1,2,2.5,5}*10^k, die
// >= span/10 ist; Ticks ab ceil(start/step)*step. So liegen die Labels auf
// runden Werten (z. B. 35000/40000/45000 kHz) statt auf krummen Siebteln.
function niceStepFor(rawStep) {
  const multipliers = [1, 2, 2.5, 5, 10];
  const exponent = Math.floor(Math.log10(rawStep));
  for (let e = exponent - 1; e <= exponent + 1; e++) {
    for (const m of multipliers) {
      const candidate = m * Math.pow(10, e);
      if (candidate >= rawStep - 1e-9) return candidate;
    }
  }
  return Math.pow(10, exponent + 1);
}
function niceTicksHz(startHz, stopHz) {
  const span = stopHz - startHz;
  if (!(span > 0)) return [];
  const step = niceStepFor(span / 10);
  const ticks = [];
  for (let f = Math.ceil(startHz / step) * step; f <= stopHz + 1e-6; f += step) {
    ticks.push(Math.round(f));
  }
  return ticks;
}
function fmtAxisMHz(freqHz) { return (freqHz / 1e6).toFixed(4); }

// Gemeinsamer Achsenzeichner: Datenbereich [x0 .. x0+dataW] auf dem Canvas,
// Bereichsgrenzen gedimmt an den Raendern, Nice-Ticks dazwischen.
function drawNiceFreqAxis(ctx, x0, dataW, h, loHz, hiHz) {
  const span = hiHz - loHz;
  ctx.font = '9px sans-serif';
  if (!(span > 0)) return;
  // T-2007: ein Tick-Label erscheint nur, wenn es neben den Randlabels Platz
  // hat - gemessen mit measureText statt eines festen Randabstands; das
  // Randlabel hat Vorrang. Gerechnet relativ zum Datenbereich (xr).
  const loLabel = fmtAxisMHz(loHz), hiLabel = fmtAxisMHz(hiHz);
  ctx.strokeStyle = '#555'; ctx.fillStyle = '#777';
  ctx.textAlign = 'left';  ctx.fillText(loLabel, x0 + 2, h - 2);
  ctx.textAlign = 'right'; ctx.fillText(hiLabel, x0 + dataW - 2, h - 2);
  const LABEL_GAP_PX = 6;
  const linksBis = 2 + ctx.measureText(loLabel).width + LABEL_GAP_PX;
  const rechtsAb = dataW - 2 - ctx.measureText(hiLabel).width - LABEL_GAP_PX;
  ctx.fillStyle = '#aaa'; ctx.textAlign = 'center';
  for (const f of niceTicksHz(loHz, hiHz)) {
    const xr = ((f - loHz) / span) * dataW;
    const label = fmtAxisMHz(f);
    const halb = ctx.measureText(label).width / 2;
    if (xr - halb < linksBis || xr + halb > rechtsAb) continue;
    const x = x0 + xr;
    ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, 4); ctx.stroke();
    ctx.fillText(label, x, h - 2);
  }
}

function drawFreqAxis(canvasId) {
  const c = el(canvasId), ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  drawNiceFreqAxis(ctx, 0, w, h, R.freq_lo_hz, R.freq_hi_hz);
}

// Frequenzraster als OVERLAY ueber den gecachten Wasserfall (Halo-Linie:
// 3 px dunkler Saum + 1 px helle Mitte). Wird in drawSpectro NACH dem
// Cache-Blit und VOR den Cursorn gezeichnet - der Cache bleibt unberuehrt.
let gridEnabled = false;
function drawFreqGridOverlay(ctx, w, h, loHz, hiHz) {
  const span = hiHz - loHz;
  if (!(span > 0)) return;
  for (const f of niceTicksHz(loHz, hiHz)) {
    const x = Math.round(((f - loHz) / span) * w) + 0.5;
    ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,0.45)';
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.lineWidth = 1; ctx.strokeStyle = 'rgba(255,255,255,0.85)';
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  ctx.lineWidth = 1;
}

// Frequenzachse fuer den Wasserfall: der Wasserfall-Canvas (720) ist um die
// Gutter-Breite (66) nach rechts versetzt -> die Achse deckt beides ab und
// beschriftet nur den 720er-Datenteil.
function drawSpectroFreqAxis() {
  const c = el('spectroFreqAxis'), ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  const gutter = el('spectroGutter').width, dataW = el('spectroCanvas').width;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  // Datenteil beginnt hinter dem Gutter (+4 px wie bisher, Rahmen/Abstand).
  drawNiceFreqAxis(ctx, gutter + 4, dataW - 4, h, R.freq_lo_hz, R.freq_hi_hz);
}

// Zeit-Gutter fuer das Spektrogramm aus den ECHTEN t-Werten je Zeile - jetzt
// ueber die volle (scrollende) Hoehe, Beschriftung etwa alle 60 px.
function drawSpectroGutter() {
  const c = el('spectroGutter'), ctx = c.getContext('2d');
  const w = c.width, h = c.height, nf = R.n_frames;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = '#aaa'; ctx.font = '9px sans-serif'; ctx.textAlign = 'right';
  const t0 = R.times[0];
  const nLabels = Math.max(2, Math.min(nf, Math.floor(h / 60)));
  for (let i = 0; i < nLabels; i++) {
    const frac = nLabels <= 1 ? 0 : i / (nLabels - 1);
    const k = Math.min(nf - 1, Math.floor(frac * (nf - 1)));
    const y = Math.floor(frac * (h - 1));
    const yy = Math.max(8, Math.min(h - 2, y + 4));
    ctx.fillText('+' + (R.times[k] - t0).toFixed(1) + 's', w - 3, yy);
  }
}

function drawBinTimeAxis() {
  const c = el('binTimeAxis'), ctx = c.getContext('2d');
  const w = c.width, h = c.height, nf = R.n_frames;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#555'; ctx.fillStyle = '#aaa'; ctx.font = '9px sans-serif';
  const t0 = R.times[0], t1 = R.times[nf - 1];
  const nTicks = 7;
  for (let i = 0; i < nTicks; i++) {
    const frac = i / (nTicks - 1);
    const x = frac * w;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 4); ctx.stroke();
    ctx.textAlign = i === 0 ? 'left' : (i === nTicks - 1 ? 'right' : 'center');
    ctx.fillText('+' + ((t1 - t0) * frac).toFixed(1) + 's', x, h - 2);
  }
}

// Alles neu (Cache abhaengig von Skala) - bei Laden und Skalenwechsel.
function renderAll() {
  buildSpectroCache();
  drawSpectroGutter();
  drawSpectroFreqAxis();
  drawFreqAxis('specFreqAxis');
  updateKnownStrip('known-w', R.freq_lo_hz, R.freq_hi_hz);
  updateKnownStrip('known-sp', R.freq_lo_hz, R.freq_hi_hz);
  drawDbGutter('specGutter');
  drawDbGutter('binGutter');
  drawBinTimeAxis();
  renderFrame(true);
}

// Nur das, was sich pro Frame/Bin aendert - guenstig genug fuer Wiedergabe.
// autoscroll=true haelt den Zeit-Cursor im Viewport (Transport); beim Ziehen
// im Wasserfall wird es mit false aufgerufen.
function renderFrame(autoscroll) {
  drawSpectro();
  drawSpectrum();
  drawBinSeries();
  updatePosLabel();
  updateBinLabel();
  el('frameSlider').value = String(cur);
  if (autoscroll) ensureCursorVisible();
}

function updatePosLabel() {
  const t0 = R.times[0], t = R.times[cur];
  const rel = t - t0;
  const mm = Math.floor(rel / 60), ss = (rel - mm * 60);
  const wall = isoTime(new Date(t * 1000));
  el('posLabel').textContent =
    'Frame ' + cur + '/' + (R.n_frames - 1) +
    '   +' + mm + ':' + ss.toFixed(1).padStart(4, '0') +
    '   ' + wall + '   seq ' + R.seqs[cur];
}

function updateBinLabel() {
  const f = R.bin0_hz + selBin * R.step_hz;
  const v = M[cur * R.n_window + selBin] / 10;
  el('binLabel').textContent =
    'Bin ' + selBin + ' = ' + (f / 1e6).toFixed(5) + ' MHz   |   current ' +
    v.toFixed(1) + ' dBµV   |   peak hold ' + (peak[selBin] / 10).toFixed(1) + ' dBµV';
}

// ------------------------------------------------------------------ Transport

function setCur(k, autoscroll) {
  cur = Math.max(0, Math.min(R.n_frames - 1, k | 0));
  renderFrame(autoscroll);
}

function stepPlay() {
  if (!playing) return;
  if (cur >= R.n_frames - 1) { stopPlayback(); return; }
  setCur(cur + 1, true);
  playTimer = setTimeout(stepPlay, 1000 / fps);
}

function startPlayback() {
  if (playing || !R) return;
  if (cur >= R.n_frames - 1) cur = 0;   // vom Anfang, wenn am Ende
  playing = true;
  el('btnPlay').innerHTML = '<svg class="glyph" viewBox="0 0 16 16"><path d="M3 2h4v12H3zM9 2h4v12H9z"/></svg>';  // Pause-Glyph (SVG, T-1402)
  playTimer = setTimeout(stepPlay, 1000 / fps);
}

function stopPlayback() {
  playing = false;
  if (playTimer) { clearTimeout(playTimer); playTimer = null; }
  const b = el('btnPlay'); if (b) b.innerHTML = '<svg class="glyph" viewBox="0 0 16 16"><path d="M3 2l11 6-11 6z"/></svg>';  // Play-Glyph (SVG, T-1402)
}

el('btnPlay').onclick = () => { playing ? stopPlayback() : startPlayback(); };
el('btnPrev').onclick = () => { stopPlayback(); setCur(cur - 1, true); };
el('btnNext').onclick = () => { stopPlayback(); setCur(cur + 1, true); };
el('btnStart').onclick = () => { stopPlayback(); setCur(0, true); };
el('btnEnd').onclick = () => { stopPlayback(); setCur(R.n_frames - 1, true); };
el('speedSel').onchange = e => { fps = parseInt(e.target.value, 10); };
el('frameSlider').oninput = e => { stopPlayback(); setCur(parseInt(e.target.value, 10), true); };

// Skala-Regler: der Wasserfall-Cache ist bei langen Aufnahmen teuer, daher den
// Neubau nach dem letzten Zug leicht verzoegern (die Beschriftung reagiert
// sofort). renderAll baut den Cache und zeichnet alles neu.
function onScaleInput() {
  if (scaleDebounce) clearTimeout(scaleDebounce);
  scaleDebounce = setTimeout(() => { if (R) renderAll(); }, 120);
}
el('scaleMin').oninput = e => {
  scaleMin = parseFloat(e.target.value);
  el('scaleMinLabel').textContent = scaleMin + ' dBµV';
  onScaleInput();
};
el('scaleMax').oninput = e => {
  scaleMax = parseFloat(e.target.value);
  el('scaleMaxLabel').textContent = scaleMax + ' dBµV';
  onScaleInput();
};
el('autoScaleBtn').onclick = () => { if (R) { autoScaleWaterfall(); renderAll(); } };
// Frequenzraster ein/aus: nur das Spektrogramm neu blitten (Cache bleibt).
el('gridToggle').onchange = e => {
  gridEnabled = e.target.checked;
  if (R) renderFrame(false);
};

// Delegierter Klick auf die Navigator-Leiste: gilt auch fuer die dynamisch
// gerenderten Befundzeilen (kein erneutes Verdrahten nach jedem Render).
el('navCol').addEventListener('click', ev => {
  const item = ev.target.closest('.nav-item');
  if (item) jumpToBefund(item);
});

// Fenstergroesse geaendert -> Viewport-Hoehe an die rechte Spalte anpassen und
// den Wasserfall neu aufbauen (verzoegert, da der Cache-Neubau teuer ist).
let resizeDebounce = null;
window.addEventListener('resize', () => {
  if (!R || el('view-player').style.display !== 'block') return;
  if (resizeDebounce) clearTimeout(resizeDebounce);
  resizeDebounce = setTimeout(() => { fitWaterfallHeight(); sizeWaterfall(); renderAll(); }, 200);
});

// ------------------------------------------------------------------ Klick + Drag-Scrubbing

// Gemeinsame Geste fuer alle drei Canvas: pointerdown waehlt die Position,
// pointermove fuehrt sie waehrend gedrueckter Taste nach (Scrubbing),
// pointerup loest. setPointerCapture haelt die Geste auch dann fest, wenn der
// Zeiger den Canvas-Rand verlaesst; die Fraktionen werden auf 0..1 geklemmt.
// handler(xf, yf) bekommt die geklemmte relative Position im Canvas.
function makeScrubber(canvasId, handler) {
  const c = el(canvasId);
  let active = false;
  function pos(e) {
    const r = c.getBoundingClientRect();
    const xf = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const yf = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    handler(xf, yf);
  }
  c.addEventListener('pointerdown', e => {
    if (!R) return;
    active = true;
    c.setPointerCapture(e.pointerId);
    stopPlayback();
    pos(e);
    e.preventDefault();
  });
  c.addEventListener('pointermove', e => { if (active) pos(e); });
  const end = e => {
    if (!active) return;
    active = false;
    try { c.releasePointerCapture(e.pointerId); } catch (_) { /* egal */ }
  };
  c.addEventListener('pointerup', end);
  c.addEventListener('pointercancel', end);
}

// Wasserfall: y -> Zeit (Frame), x -> Frequenz (Bin). Kein Auto-Scroll waehrend
// des Ziehens, sonst springt der Viewport unter der Maus weg.
makeScrubber('spectroCanvas', (xf, yf) => {
  selBin = Math.round(xf * (R.n_window - 1));
  setCur(Math.round(yf * (R.n_frames - 1)), false);
});
// Spektrum: nur x -> Frequenz (Bin).
makeScrubber('specCanvas', (xf) => {
  selBin = Math.max(0, Math.min(R.n_window - 1, Math.round(xf * (R.n_window - 1))));
  renderFrame(false);
});
// Pegelverlauf: x -> Zeit; naechstliegenden Frame zur angeklickten Zeit suchen.
makeScrubber('binCanvas', (xf) => {
  const t0 = R.times[0], t1 = R.times[R.n_frames - 1];
  const tClick = t0 + xf * (t1 - t0);
  let best = 0, bestD = Infinity;
  for (let k = 0; k < R.n_frames; k++) {
    const d = Math.abs(R.times[k] - tClick);
    if (d < bestD) { bestD = d; best = k; }
  }
  setCur(best, false);
});

// ---- Frequenz-/Pegel-Anzeige an der Mausposition (Wasserfall + Spektrum) ----
const hoverTip = document.createElement('div');
hoverTip.className = 'hover-tip';
document.body.appendChild(hoverTip);

// Tooltip an der Maus platzieren und in den sichtbaren Bereich klemmen
// (T-1413). Seit dem Umbruch ist die Box mehrzeilig; ohne Klemmung liefe sie
// am rechten oder unteren Fensterrand hinaus. Gemessen wird NACH dem Setzen
// von Text und display, sonst sind offsetWidth/offsetHeight noch 0.
function placeTip(box, clientX, clientY) {
  const w = box.offsetWidth, h = box.offsetHeight;
  let left = clientX + 12, top = clientY + 12;
  if (left + w > window.innerWidth - 8) left = Math.max(8, clientX - 12 - w);
  if (top + h > window.innerHeight - 8) top = Math.max(8, clientY - 12 - h);
  box.style.left = left + 'px';
  box.style.top = top + 'px';
}

// Bindet Maus-Hover an ein Canvas: textFn(xf, yf) liefert den Anzeigetext
// (leer -> Tooltip aus). Laeuft unabhaengig vom Klick-/Drag-Scrubber.
function attachHover(canvasId, textFn) {
  const c = el(canvasId);
  c.addEventListener('mousemove', e => {
    const r = c.getBoundingClientRect();
    const xf = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const yf = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    const txt = textFn(xf, yf);
    if (!txt) { hoverTip.style.display = 'none'; return; }
    hoverTip.textContent = txt;
    hoverTip.style.display = 'block';
    placeTip(hoverTip, e.clientX, e.clientY);
  });
  c.addEventListener('mouseleave', () => { hoverTip.style.display = 'none'; });
}

// x -> Bin/Frequenz; Pegel aus M. Wasserfall: Zeile aus y; Spektrum: Frame cur.
function hoverReadout(xf, yf, useRowFromY) {
  if (!R || !M) return '';
  const bin = Math.max(0, Math.min(R.n_window - 1, Math.round(xf * (R.n_window - 1))));
  const f = (R.bin0_hz + bin * R.step_hz) / 1e6;
  const row = useRowFromY
    ? Math.max(0, Math.min(R.n_frames - 1, Math.round(yf * (R.n_frames - 1))))
    : cur;
  const v = M[row * R.n_window + bin] / 10;
  // Katalogtext (bekannter Kanal/Band) anhaengen; Pixeldichte des Spektrogramms.
  const known = knownTextAt(R.bin0_hz + bin * R.step_hz, el('spectroCanvas').width / (R.n_window * R.step_hz));
  return f.toFixed(5) + ' MHz: ' + v.toFixed(1) + ' dBµV' + (known ? KNOWN_SEP + known : '');
}

attachHover('spectroCanvas', (xf, yf) => hoverReadout(xf, yf, true));
attachHover('specCanvas', (xf, yf) => hoverReadout(xf, yf, false));

// Tastatur: Leertaste = Play/Pause, Pfeile = Einzelschritt.
// Ausnahme: Fokus in einem Eingabefeld (Notiz-Textarea, Kurztext-Input,
// Selects) - dann gehen alle Tasten an das Feld, der Player bleibt
// unberuehrt. Klick ausserhalb blurt das Feld nativ, danach steuern die
// Tasten wieder den Player. (Fix: Leertaste/Pfeile waren im Notizfeld
// nicht tippbar, weil preventDefault sie schluckte.)
document.addEventListener('keydown', e => {
  const t = e.target;
  if (t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.tagName === 'SELECT')) return;
  if (el('view-player').style.display !== 'block') return;
  if (e.code === 'Space') { e.preventDefault(); playing ? stopPlayback() : startPlayback(); }
  else if (e.code === 'ArrowRight') { stopPlayback(); setCur(cur + 1, true); }
  else if (e.code === 'ArrowLeft') { stopPlayback(); setCur(cur - 1, true); }
});

showView('list');
loadRecordingList();
// Deep-Link aus waterfall.py (T-803); der Pfad laeuft serverseitig ohnehin
// durch resolve_safe_path, clientseitig keine zusaetzliche Pruefung noetig.
const deepPath = new URLSearchParams(location.search).get('path');
if (deepPath) openRecording(deepPath);
// Deep-Link aus manage_jobs.py (:8050): ?job=<job_id> belegt das Suchfeld
// vor - der bestehende Filter in renderRecordingTable() matcht bereits
// gegen it.job.job_id, es ist kein eigener Filtermechanismus noetig. Der
// Nutzer sieht den Filter im Suchfeld und hebt ihn durch Leeren wieder auf.
// Laeuft VOR Aufloesung des fetch in loadRecordingList() (single-threaded),
// wirkt also schon auf das erste Rendering der Liste.
// Seit T-1105: hat der Job genau EINE Teildatei, wird sie direkt geoeffnet
// (siehe loadRecordingList); mehrere Teile -> gefilterte Liste wie bisher.
const jobDeep = new URLSearchParams(location.search).get('job');
if (jobDeep) {
  pendingJobDeep = jobDeep.trim();
  searchTerm = jobDeep.trim().toLowerCase();
  el('searchInput').value = jobDeep.trim();
}
</script>
</body>
</html>
"""

# T-1402: Theme-Bausteine einsetzen (Tokens + Grundregeln, Head-Skript fuer das
# Theme-Cookie, Kopfleiste statt <h1>). Transport-/Play-/Pause-Glyphen sind
# Inline-SVG (Markup und startPlayback/stopPlayback), damit Windows keine Emoji rendert.
HTML_PAGE = (HTML_PAGE.replace("/*THEME_CSS*/", THEME_CSS + BASE_CSS)
                      .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                      .replace("/*HEADER*/", header_html("Spectrum analysis")))


# ---------------------------------------------------------------------------
# HTTP-Handler
# ---------------------------------------------------------------------------

def _error_payload(exc) -> dict:
    """Fehlerantwort aus einer Ausnahme (T-1802c2).

    Traegt die Ausnahme einen sprachneutralen code (eb200_core.recording_index),
    gehen code und params an den Client, der daraus den Text formuliert. Alle
    uebrigen Ausnahmen (ValueError aus mscan_job, KeyError, ...) liefern wie
    bisher error mit str(exc) - der Rueckfall bleibt, bis auch sie umgestellt sind.
    """
    code = getattr(exc, "code", None)
    if code:
        return {"error_code": code, "error_params": getattr(exc, "params", {}) or {}}
    return {"error": str(exc)}


class Handler(http.server.BaseHTTPRequestHandler):
    known_catalog = None            # Catalog aus eb200_core.known_freq, siehe main()

    def log_message(self, fmt, *args):
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(HTML_PAGE)
            elif parsed.path == "/api/known_freq":
                # Katalog bekannter Frequenzen (siehe Catalog.to_client_json), vom
                # Frontend einmalig geholt. Rein lesend. Wortgleich zu waterfall.py.
                # load_catalog_cached() statt der beim Start geladenen Instanz,
                # damit Katalog-Edits (block_admin.py) ohne Neustart ankommen.
                # load_station_config() ebenso JE REQUEST (Region ist reine
                # Anzeigefilterung, wirkt so ohne Neustart wie Katalog-Edits).
                # load_view_config() genauso (T-126): die in :8010 abgewaehlten
                # Katalogdateien fallen hier aus bands/channels heraus.
                if self.known_catalog is None:
                    self._send_json({"version": 1, "bands": [], "channels": [],
                                     "files": [], "errors": ["catalog not loaded"],
                                     "region": {"tags": []},
                                     "view": {"total": 0, "hidden": 0}})
                else:
                    self._send_json(load_catalog_cached().to_client_json(
                        load_station_config(), load_view_config()))

            elif parsed.path == "/api/recordings":
                self._send_json(list_recordings())
            elif parsed.path == "/api/recording":
                self._handle_api_recording(parse_qs(parsed.query))
            elif parsed.path == "/api/frames":
                self._handle_api_frames(parse_qs(parsed.query))
            elif parsed.path == "/api/befund":
                self._handle_api_befund(parse_qs(parsed.query))
            elif parsed.path == "/api/note":
                self._handle_api_note(parse_qs(parsed.query))
            else:
                self._send_json({"error": "unknown endpoint"}, status=404)
        except Exception:
            # Sicherheitsnetz - jeder erwartete Fehlerfall wird bereits gezielt
            # in den _handle_*-Methoden abgefangen; das hier greift nur bei
            # wirklich unerwarteten Problemen und verhindert einen Traceback
            # beim Client.
            self._send_json({"error": ERR_INTERNAL}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/label":
                self._handle_set_label()
            elif parsed.path == "/api/note":
                self._handle_set_note()
            elif parsed.path == "/api/delete":
                self._handle_delete()
            else:
                self._send_json({"error": "unknown endpoint"}, status=404)
        except Exception:
            self._send_json({"error": ERR_INTERNAL}, status=500)

    def _handle_delete(self) -> None:
        # Aufnahme komplett loeschen (.specbin + Sidecars), Label/Notiz
        # leeren. Pfad kommt aus dem JSON-Body und MUSS wie jeder Zugriff
        # durch die Sandbox; Schutzregeln siehe delete_recording().
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 8192:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_path = payload["path"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        try:
            result = delete_recording(resolved)
        except RecordingLockedError as exc:
            self._send_json(_error_payload(exc), status=409)
            return
        except RecordingIsLongRunPartError as exc:
            self._send_json(_error_payload(exc), status=409)
            return
        self._send_json(result)

    def _handle_set_label(self) -> None:
        # Kurztext setzen. Der Pfad kommt aus dem JSON-Body und MUSS - wie jeder
        # Lesezugriff - durch die Sandbox (LAN-Bindung). Der Speicher liegt
        # getrennt von der rein lesenden Aufnahme (siehe LabelStore).
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 8192:      # Kurztext ist winzig; Deckel gegen Missbrauch
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_path = payload["path"]
            text = payload.get("text", "")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        stored = LABELS.set(to_relative(resolved), text)
        self._send_json({"ok": True, "label": stored})

    def _resolve_or_403(self, params: dict):
        """Gemeinsame Vorstufe: path-Parameter durch die Sandbox schicken.
        Gibt den aufgeloesten Pfad zurueck oder None (nach 403-Antwort)."""
        raw_path = params.get("path", [""])[0]
        try:
            return resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return None

    def _payload_or_error(self, resolved: str, builder) -> None:
        """Ruft builder(resolved) auf und uebersetzt die drei Reader-Fehler in
        die passenden HTTP-Statuscodes (gemeinsam fuer /recording und /frames)."""
        try:
            self._send_json(builder(resolved))
        except FileNotFoundError:
            self._send_json({"error": ERR_NOT_FOUND}, status=404)
        except IncompleteRecordingError:
            self._send_json({"error": ERR_INCOMPLETE}, status=422)
        except InvalidRecordingError:
            self._send_json({"error": ERR_INVALID_REC}, status=422)

    def _handle_api_befund(self, params: dict) -> None:
        """GET /api/befund?path=... - Befund neben der Aufnahme sicherstellen
        (Lazy-Fill: erzeugen, falls noch nicht vorhanden) und ausliefern."""
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        self._payload_or_error(resolved, recording_befund)

    def _handle_api_note(self, params: dict) -> None:
        """GET /api/note?path=... - gespeicherte Notiz zu einer Aufnahme
        liefern (leerer String, falls keine vorhanden)."""
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        rel = to_relative(resolved)
        self._send_json({"path": rel, "note": NOTES.get(rel)})

    def _handle_set_note(self) -> None:
        # Notiz setzen. Der Pfad kommt aus dem JSON-Body und MUSS - wie jeder
        # Lesezugriff - durch die Sandbox. Speicher getrennt vom Kurztext
        # (LABELS) und vom regenerierbaren Befund-Sidecar (siehe NOTES oben).
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 32768:      # 4000 Zeichen koennen UTF-8/JSON-escaped mehr Bytes sein
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_path = payload["path"]
            note = payload.get("note", "")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        rel = to_relative(resolved)
        stored = NOTES.set(rel, note)
        self._send_json({"path": rel, "note": stored})

    def _handle_api_recording(self, params: dict) -> None:
        resolved = self._resolve_or_403(params)
        if resolved is not None:
            self._payload_or_error(resolved, recording_meta)

    def _handle_api_frames(self, params: dict) -> None:
        resolved = self._resolve_or_403(params)
        if resolved is not None:
            self._payload_or_error(resolved, recording_frames)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status: int = 200) -> None:
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

def main() -> int:
    # Katalog bekannter Frequenzen (known_freq/*.json) EINMAL beim Start laden.
    # load_catalog() wirft nie - defekte Dateien landen als Text in .errors.
    Handler.known_catalog = load_catalog()
    kc = Handler.known_catalog
    print(f"[known_freq] {len(kc.channels)} Kanaele, {len(kc.bands)} Baender "
          f"aus {len(kc.files)} Dateien, {len(kc.errors)} Fehler")
    for err in kc.errors:
        print(f"[known_freq] WARNUNG: {err}", file=sys.stderr)
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"[analyze-spectrum] Server laeuft auf http://{HTTP_HOST}:{HTTP_PORT}, "
          f"Wurzel: {SPECTRUM_ROOT}")
    register_server("analyze_spectrum", HTTP_PORT, {"root": str(SPECTRUM_ROOT)})
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