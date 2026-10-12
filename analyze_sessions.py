#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
analyze_sessions.py

Eigenstaendiges Analyse-Werkzeug fuer die Rohdaten-Session-DBs
aus eb200_core/session_store.py (Schema session/segment/sweep). Durchsucht
sessions/ (bzw. beliebige Pfade innerhalb der Repo-Wurzel) im Browser und
stellt die aufgezeichneten D-SCAN-Segmente als Wasserfall dar.

Kein Schreibzugriff auf irgendeine Session-DB: alle sqlite3-Verbindungen
werden ausschliesslich per "file:...?mode=ro" (uri=True) geoeffnet - siehe
open_session_db(). Geschrieben werden ausschliesslich eigene Metadaten-
Dateien NEBEN den Messdaten (Kurztexte/Notizen via LabelStore, regenerierbare
Befund-Sidecars) - die Session-DBs selbst bleiben unangetastet. Kein Import
aus session_store.py/waterfall.py/db.py noetig (nur das Schema wird
gebraucht), dieses Skript bleibt bewusst eigenstaendig.

Sicherheits-Hinweis: der Server bindet auf 0.0.0.0:8020 und ist damit im
gesamten Shack-LAN erreichbar. Jeder "path"-Query-Parameter MUSS deshalb
zwingend zuerst durch resolve_safe_path() laufen, bevor irgendein
Dateizugriff passiert - siehe dort. Nur Python-Standardbibliothek.

Schutzregeln fuer das Loeschen (Sandbox, Live-Sperre) liegen seit T-1104
zentral in eb200_core/recording_index.py - siehe delete_session().
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from array import array
from urllib.parse import parse_qs, urlparse

from eb200_core.label_store import LabelStore, clean_note
from eb200_core.param_store import register_server
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
from eb200_core.known_freq import (
    load_catalog, load_catalog_cached, load_station_config, load_view_config,
)
from eb200_core.recording_index import (
    delete_recording as _delete_recording, RecordingLockedError as _RecLocked,
)
from eb200_core import paths
from eb200_core.path_sandbox import sandbox_roots, resolve_within, to_portable

# ---------------------------------------------------------------------------
# Konfiguration / Pfad-Sandbox (siehe Moduldoku - PFLICHT wegen LAN-Bindung)
# ---------------------------------------------------------------------------

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8020

# Wurzel der Sandbox: die Datenwurzel (T-2003, eb200_core/paths.py; ohne
# --data-dir das Repo-Root wie bisher). Kein path-Query-Parameter darf jemals
# ausserhalb davon bzw. der storage_roots aufgeloest werden (resolve_safe_path()).
SESSION_ROOT = paths.DATA_ROOT
SESSION_DIR = "sessions"  # Autoliste-Ordner, wie beim Recorder (session_store.py)

# Getrennter, schreibbarer Kurztext-Speicher (eb200_core/label_store.py). Die
# Session-DBs selbst bleiben rein lesend; die frei vergebene Bezeichnung liegt
# daneben in dieser JSON-Datei (Schluessel = SESSION_ROOT-relativer Pfad).
LABELS = LabelStore(os.path.join(SESSION_ROOT, "session_labels.json"))

# Freitext-Notizen je Session-DB (mehrzeilig, getrennt vom Kurztext und vom
# REGENERIERBAREN Befund-Sidecar - eine Notiz im .befund.json waere bei der
# naechsten Versions-Regeneration verloren). Gleicher Schluessel wie LABELS.
NOTES = LabelStore(os.path.join(SESSION_ROOT, "session_notes.json"), cleaner=clean_note)

# Muss mit ANALYSIS_VERSION in session_analyze.py uebereinstimmen; Sidecars
# mit anderer/fehlender Version werden bei Zugriff neu erzeugt.
EXPECTED_ANALYSIS_VERSION = 3

REQUIRED_TABLES = {"session", "segment", "sweep"}

THUMB_TARGET_ROWS = 130     # Ziel-Zeilenzahl fuer /api/segment?mode=thumb
THUMB_TARGET_BINS = 300     # T-806: Ziel-Spaltenzahl fuer mode=thumb. Die
                            # Thumbnail-Canvas sind 160 px (Liste) bzw. 300 px
                            # (Segment-Karte) breit - mehr Bins sieht niemand,
                            # sie kosten nur Cache und Uebertragung.
FULL_ROW_THRESHOLD = 5000   # ab hier wird auch mode=full dezimiert

# T-806: Thumbnail-Cache neben der Session-DB. Regenerierbares Beiwerk wie das
# .befund.json - die Session-DB selbst bleibt unangetastet (Moduldoku). Der
# Sidecar haelt die fertigen mode=thumb-Payloads je Segment-ID; gueltig ist er
# nur zu genau einem Dateistand der DB (siehe db_fingerprint()).
THUMB_CACHE_SUFFIX = ".thumb.json"
THUMB_CACHE_FORMAT = "eb200-sessthumb-v1"

# "live"-Heuristik an EINER Stelle (siehe session_is_live): sie entscheidet
# sowohl ueber das LIVE-Abzeichen in der Liste als auch darueber, ob ein
# Thumbnail ueberhaupt gecacht werden darf.
LIVE_WAL_MAX_AGE_S = 10.0

ERR_SANDBOX = "access denied: path lies outside the permitted storage locations"
ERR_NOT_FOUND = "file not found"
ERR_INVALID_DB = "not a valid session DB"
ERR_BAD_PARAMS = "invalid request parameters"
ERR_SEGMENT_NOT_FOUND = "segment not found"
ERR_INTERNAL = "internal error"


class InvalidSessionDBError(Exception):
    """Datei existiert, ist aber keine gueltige Session-DB (Schema fehlt
    oder die Datei ist beschaedigt/kein SQLite)."""


class SessionLockedError(Exception):
    """Session laeuft moeglicherweise noch (dieselbe 'live'-Heuristik wie
    list_session_files) - siehe delete_session()."""


def _rewrap(ziel_cls, exc):
    """Lokale Ausnahme aus einer Kernausnahme, unter Mitnahme von code/params (T-1802c2)."""
    neu = ziel_cls(str(exc))
    neu.code = getattr(exc, "code", None)
    neu.params = getattr(exc, "params", {}) or {}
    return neu


def resolve_safe_path(user_path: str) -> str:
    """
    Loest user_path (ein "path"-Query-Parameter) zu einem absoluten Pfad auf
    und stellt sicher, dass dieser innerhalb von SESSION_ROOT ODER einem
    konfigurierten storage_root liegt. MUSS bei JEDEM eingehenden
    path-Parameter als allererstes aufgerufen werden, bevor irgendein
    Dateizugriff passiert (siehe Moduldoku). Whitelist = Repo-Root +
    storage_roots, siehe eb200_core/path_sandbox.py.

    Wirft ValueError, wenn der Pfad leer ist, ausserhalb aller Roots liegt,
    oder direkt auf eine WAL-/SHM-Beidatei zeigt (nur die .db-Hauptdatei ist
    als path zulaessig). Der Handler uebersetzt ValueError IMMER in eine
    generische 403-Antwort, die den eingegebenen Pfad nicht wiederholt (kein
    Oracle ueber Pfade ausserhalb der Sandbox).
    """
    # sandbox_roots() liest storage_roots.json bei JEDEM Aufruf neu (Datei
    # winzig) - Aenderungen daran wirken so ohne Server-Neustart.
    return resolve_within(user_path, sandbox_roots(SESSION_ROOT),
                           suffixes=(".db",), reject_suffixes=("-wal", "-shm"))


def to_relative(path: str) -> str:
    """Portables Pfadformat (siehe eb200_core.path_sandbox.to_portable):
    SESSION_ROOT-relativ mit Vorwaertsschraegstrichen innerhalb des
    Repo-Roots, sonst absolut mit Vorwaertsschraegstrichen (externer
    storage_root) - so, wie ihn der Client unveraendert an /api/session bzw.
    /api/segment zurueckreichen kann."""
    return to_portable(path, SESSION_ROOT)


# ---------------------------------------------------------------------------
# Datenzugriff (read-only)
# ---------------------------------------------------------------------------

def open_session_db(resolved_path: str) -> sqlite3.Connection:
    """
    Oeffnet resolved_path AUSSCHLIESSLICH lesend (file:...?mode=ro, uri=True)
    und prueft, dass die Tabellen session/segment/sweep vorhanden sind.

    Wirft FileNotFoundError, wenn die Datei fehlt, InvalidSessionDBError bei
    fehlendem Schema oder einer kaputten/keiner SQLite-Datei. Der Handler
    uebersetzt beides in die passenden HTTP-Fehlerantworten - niemals einen
    Traceback an den Client durchreichen.
    """
    if not os.path.isfile(resolved_path):
        raise FileNotFoundError(resolved_path)
    try:
        conn = sqlite3.connect(f"file:{resolved_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not REQUIRED_TABLES <= tables:
            conn.close()
            raise InvalidSessionDBError(resolved_path)
    except sqlite3.Error as exc:
        raise InvalidSessionDBError(resolved_path) from exc
    return conn


# T-715: optionale Spalten der segment-Tabelle. speed/mtime_s gibt es seit
# laengerem, bw_hz/bw_zoom/gain_mode/gain_mgc_dbuv erst seit T-715 - eine vor
# der Erweiterung aufgezeichnete Session-DB hat sie NICHT. Deshalb nie fest in
# ein SELECT schreiben, sondern hier ermitteln und im JSON mit null auffuellen,
# damit das Frontend immer dieselben Schluessel sieht.
SEGMENT_OPTIONAL_COLUMNS = ("speed", "mtime_s", "bw_hz", "bw_zoom",
                            "gain_mode", "gain_mgc_dbuv")


def segment_extra_columns(conn: sqlite3.Connection) -> list:
    """Die in DIESER DB tatsaechlich vorhandenen Spalten aus
    SEGMENT_OPTIONAL_COLUMNS, in fester Reihenfolge."""
    present = {row[1] for row in conn.execute("PRAGMA table_info(segment)")}
    return [c for c in SEGMENT_OPTIONAL_COLUMNS if c in present]


def segment_row_to_dict(row, extra: list) -> dict:
    """sqlite3.Row -> dict; in dieser DB fehlende optionale Spalten werden auf
    None gesetzt (siehe SEGMENT_OPTIONAL_COLUMNS)."""
    result = dict(row)
    for column in SEGMENT_OPTIONAL_COLUMNS:
        if column not in extra:
            result[column] = None
    return result


def load_segment_rows(conn: sqlite3.Connection, segment_id: int):
    """Laedt alle sweep-Zeilen eines Segments in seq-Reihenfolge; levels
    wird bereits hier zu array('h') dekodiert. Gibt eine Liste von
    (t_unix, array('h')) zurueck."""
    cursor = conn.execute(
        "SELECT t_unix, levels FROM sweep WHERE segment_id = ? ORDER BY seq ASC",
        (segment_id,),
    )
    rows = []
    for t_unix, blob in cursor:
        levels = array('h')
        levels.frombytes(blob)
        rows.append((t_unix, levels))
    return rows


def decimate_max_pool(rows, target_rows: int):
    """
    Fasst rows (Liste von (t_unix, array('h'))) per MAX-Pooling auf
    hoechstens target_rows Ausgabezeilen zusammen: pro Bin wird das Maximum
    ueber die in einer Gruppe zusammengefassten Rohsweeps uebernommen -
    NICHT der Mittelwert - damit kurze/schmale Signale beim Verkleinern
    nicht verschwinden. Der Zeitstempel jeder Ausgabezeile ist der der
    mittleren Rohzeile ihrer Gruppe. Gibt (neue_rows, wurde_dezimiert,
    gruppengroesse) zurueck; ist rows bereits kurz genug, kommt es
    unveraendert zurueck und die Gruppengroesse ist 1. Die Gruppengroesse
    IST der Dezimierungsfaktor, den der Kopf der Session-Analyse nennt -
    sie wird deshalb hier zurueckgegeben und nicht beim Aufrufer aus
    len(rows)/len(out) nachgerechnet (eine Quelle der Wahrheit).
    """
    n = len(rows)
    if n <= target_rows:
        return rows, False, 1

    group_size = -(-n // target_rows)  # ceil(n / target_rows)
    out = []
    for i in range(0, n, group_size):
        chunk = rows[i:i + group_size]
        levels_list = [lv for _, lv in chunk]
        maxed = array('h', (max(vals) for vals in zip(*levels_list)))
        mid_t = chunk[len(chunk) // 2][0]
        out.append((mid_t, maxed))
    return out, True, group_size


def decimate_bins_max(rows, n_bins: int, target_bins: int):
    """
    T-806: Gegenstueck zu decimate_max_pool fuer die BIN-Achse. Fasst je
    Ausgabespalte das Maximum einer Gruppe benachbarter Bins zusammen -
    wieder MAX und nicht der Mittelwert, damit ein einzelner schmaler
    Traeger beim Verkleinern nicht verschwindet.

    Nur fuer mode=thumb gedacht: die Vollansicht braucht die echte Bin-Achse
    (Frequenzachse, Katalogstreifen, Pegelanzeige an der Maus). Gibt
    (neue_rows, neue_bin_zahl, gruppengroesse) zurueck; ist n_bins bereits
    klein genug - oder passt eine Zeile nicht zur angegebenen Bin-Zahl -
    kommt alles unveraendert mit Faktor 1 zurueck.
    """
    if n_bins <= target_bins or n_bins <= 0:
        return rows, n_bins, 1
    if any(len(levels) < n_bins for _, levels in rows):
        return rows, n_bins, 1
    group = -(-n_bins // target_bins)      # ceil(n_bins / target_bins)
    out = []
    for t_unix, levels in rows:
        out.append((t_unix, array('h', (max(levels[i:i + group])
                                        for i in range(0, n_bins, group)))))
    return out, -(-n_bins // group), group


def session_is_live(resolved_db: str) -> bool:
    """T-806: True, wenn eine gleichnamige -wal-Datei existiert und in den
    letzten LIVE_WAL_MAX_AGE_S Sekunden angefasst wurde - die Aufzeichnung
    laeuft dann moeglicherweise noch. EINE Quelle der Wahrheit fuer das
    LIVE-Abzeichen der Liste und fuer die Cache-Sperre in
    store_thumb_cache(). Wirft NIE."""
    try:
        return (time.time() - os.path.getmtime(resolved_db + "-wal")) < LIVE_WAL_MAX_AGE_S
    except OSError:
        return False


def thumb_cache_path_for(resolved_db: str) -> str:
    """Pfad des Thumbnail-Caches: neben der Session-DB, DB-Name im Dateinamen
    (.../<name>.db -> .../<name>.thumb.json). Muster befund_path_for()."""
    return resolved_db[:-len(".db")] + THUMB_CACHE_SUFFIX


def db_fingerprint(resolved_db: str) -> list:
    """[mtime_ns, Groesse] der Session-DB - der Gueltigkeitsschluessel des
    Thumbnail-Caches. Waechst eine Session weiter oder wird eine Datei
    ersetzt, passt der Fingerabdruck nicht mehr und das Thumbnail wird neu
    gerechnet. Bewusst eine LISTE, damit der Vergleich nach dem JSON-Umlauf
    ohne Umwandlung aufgeht. Wirft NIE; bei Problemen [0, 0] - das passt zu
    keinem geschriebenen Cache."""
    try:
        stat = os.stat(resolved_db)
    except OSError:
        return [0, 0]
    return [stat.st_mtime_ns, stat.st_size]


def load_thumb_cache(resolved_db: str, segment_id: int):
    """Gecachtes Thumbnail-Payload eines Segments oder None. Wirft NIE - ein
    unlesbarer, veralteter oder fremder Cache wird stillschweigend ignoriert
    und beim naechsten Schreiben ueberbuegelt."""
    try:
        with open(thumb_cache_path_for(resolved_db), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != THUMB_CACHE_FORMAT:
        return None
    if data.get("db") != db_fingerprint(resolved_db):
        return None
    segments = data.get("segments")
    if not isinstance(segments, dict):
        return None
    payload = segments.get(str(segment_id))
    return payload if isinstance(payload, dict) else None


def store_thumb_cache(resolved_db: str, segment_id: int, payload: dict) -> None:
    """Thumbnail-Payload in den Cache neben der DB legen; atomar ueber Temp +
    os.replace (Muster analyze_audio.append_iqbefund). Passt der bereits
    gespeicherte Fingerabdruck nicht mehr zum aktuellen Dateistand, wird die
    Datei frisch aufgebaut statt ergaenzt - sonst stuenden Thumbnails zweier
    Dateistaende nebeneinander.

    Eine LAUFENDE Session wird nicht gecacht: ihre DB waechst weiter, der
    Fingerabdruck waere binnen Sekunden veraltet, und die Datei wuerde bei
    jedem Seitenaufruf neu geschrieben.

    Wirft NIE - ein nicht beschreibbarer Ablageort (schreibgeschuetzter
    storage_root, volle Platte) darf die Anzeige nicht kosten; dann wird das
    Thumbnail eben weiterhin je Aufruf gerechnet.
    """
    if session_is_live(resolved_db):
        return
    fingerprint = db_fingerprint(resolved_db)
    ziel = thumb_cache_path_for(resolved_db)
    try:
        with open(ziel, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    if (not isinstance(data, dict)
            or data.get("format") != THUMB_CACHE_FORMAT
            or data.get("db") != fingerprint
            or not isinstance(data.get("segments"), dict)):
        data = {"format": THUMB_CACHE_FORMAT, "db": fingerprint, "segments": {}}
    data["segments"][str(segment_id)] = payload
    tmp = ziel + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, ziel)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def list_session_files():
    """
    Listet gueltige *.db-Dateien in SESSION_DIR (nicht rekursiv; WAL-/SHM-
    Beidateien tragen keine .db-Endung und werden dadurch bereits automatisch
    ausgeblendet). Fuer jede Datei ein kompakter Steckbrief per
    Aggregat-Query auf segment (bewusst NICHT COUNT(*) FROM sweep - zu
    teuer). Eine defekte/ungueltige Datei fliegt NICHT die ganze Antwort um,
    sondern erscheint nur als {"valid": false, "name": ...}.
    """
    result = []
    session_dir_abs = os.path.join(SESSION_ROOT, SESSION_DIR)
    if not os.path.isdir(session_dir_abs):
        return result

    for entry in sorted(os.scandir(session_dir_abs), key=lambda e: e.name):
        if not entry.is_file() or not entry.name.endswith(".db"):
            continue
        full_path = entry.path

        try:
            conn = open_session_db(full_path)
        except (FileNotFoundError, InvalidSessionDBError):
            result.append({"valid": False, "name": entry.name})
            continue

        try:
            session_row = conn.execute(
                "SELECT started_at, source, mode FROM session LIMIT 1"
            ).fetchone()
            agg = conn.execute(
                "SELECT SUM(row_count), COUNT(*), MIN(t_first), MAX(t_last), "
                "MIN(start_hz), MAX(stop_hz), MIN(id) FROM segment"
            ).fetchone()
        except sqlite3.Error:
            result.append({"valid": False, "name": entry.name})
            continue
        finally:
            conn.close()

        # "live"-Heuristik: seit T-806 in session_is_live() - dieselbe Pruefung
        # sperrt den Thumbnail-Cache, deshalb nur noch eine Stelle.
        live = session_is_live(full_path)

        result.append({
            "valid": True,
            "name": entry.name,
            "path": to_relative(full_path),
            "label": LABELS.get(to_relative(full_path)),
            "started_at": session_row["started_at"] if session_row else None,
            "source": session_row["source"] if session_row else None,
            "mode": session_row["mode"] if session_row else None,
            "row_count": agg[0] or 0,
            "n_segments": agg[1] or 0,
            "t_first": agg[2],
            "t_last": agg[3],
            "start_hz": agg[4],
            "stop_hz": agg[5],
            "first_segment_id": agg[6],
            "live": live,
        })
    return result


# ---------------------------------------------------------------------------
# Lazy-Fill: Befund-JSON neben der Session-DB (bei Bedarf per Subprozess erzeugt)
# ---------------------------------------------------------------------------

def befund_path_for(resolved_db: str) -> str:
    """Pfad des Befund-JSON: neben der Session-DB, DB-Name im Dateinamen
    (.../<name>.db -> .../<name>.befund.json)."""
    return resolved_db[:-len(".db")] + ".befund.json"


def ensure_befund(resolved_db: str) -> str:
    """
    Stellt den Befund-JSON sicher (Lazy-Fill). Fehlt er, wird er JETZT per
    Subprozess erzeugt: session_analyze.py im selben venv (sys.executable),
    cwd = Repo-Root, damit "from eb200_core ..." im Kind aufgeht. numpy bleibt
    im Kindprozess; dieser Server importiert kein numpy. Der Befund deckt ALLE
    Segmente der Session ab. Gibt den Pfad des Befund-JSON zurueck.
    Fehlerabbildung passt zu open_session_db:
      FileNotFoundError     -> 404 (DB fehlt)
      InvalidSessionDBError -> 422 (Analyse fehlgeschlagen)
    Vorhandene Sidecars werden nur akzeptiert, wenn ihre analysis_version zu
    EXPECTED_ANALYSIS_VERSION passt; sonst Neuberechnung.
    """
    if not os.path.isfile(resolved_db):
        raise FileNotFoundError(resolved_db)
    out = befund_path_for(resolved_db)
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
        [sys.executable, analyzer, resolved_db, "--json", out],
        cwd=paths.PROGRAM_ROOT, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(out):
        # keinen kaputten Teil-Cache zuruecklassen
        if os.path.isfile(out):
            try:
                os.unlink(out)
            except OSError:
                pass
        raise InvalidSessionDBError(proc.stderr.strip() or "analysis failed")
    return out


# ---------------------------------------------------------------------------
# Loeschen ganzer Sessions (T-804) - Messdaten-Invariante erweitert: das
# Entfernen KOMPLETTER Datensaetze ist erlaubt, das Veraendern von Inhalten
# bleibt verboten (siehe PLAN.md Entscheidungslog).
# ---------------------------------------------------------------------------

def delete_session(resolved_db: str) -> dict:
    """
    Loescht eine komplette Session (DB + WAL/SHM-Beidateien + Befund-Sidecar)
    und leert die zugehoerigen Label-/Notiz-Eintraege. Schutzregeln (Sandbox,
    Live-Heuristik) liegen seit T-1104 ZENTRAL in eb200_core/recording_index.
    delete_recording() - diese Funktion ist nur noch ein duenner Adapter, der
    dessen RecordingLockedError in die hier erwartete SessionLockedError
    uebersetzt, damit der HTTP-Handler unveraendert bleibt.

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
        result = _delete_recording(None, "session", to_relative(resolved_db), repo_root=SESSION_ROOT)
    except _RecLocked as exc:
        raise _rewrap(SessionLockedError, exc) from exc
    rel = to_relative(resolved_db)
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
<title>MoniScope Session analysis</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  /* T-1403: Farben nur noch als Tokens (eb200_core/ui_theme.py); Kopfleiste,
     Knoepfe, Felder, Tabellen liefert BASE_CSS. Canvas-Flaechen (Wasserfall,
     Thumbnails, Frequenzachse, Zeit-Gutter, Katalogstreifen) bleiben in beiden
     Themes schwarz - ihre Zeichenfarben stehen unveraendert im Script. */
  h2 { margin:22px 0 6px; }
  .link-btn { color:var(--accent); cursor:pointer; }
  button:disabled { background:var(--panel-2); color:var(--fg-faint); border-color:var(--border); cursor:default; }
  .delete-error { color:var(--danger); font-size:0.75em; margin-left:6px; }
  input[type=text] { width:360px; }
  #pathError { color:var(--danger); font-size:0.85em; margin-top:4px; }
  table { width:100%; margin-top:10px; }
  /* T-807: die Sessionliste fuellt nicht mehr die volle Fensterbreite - auf
     einem breiten Bildschirm reisst das die Zeilen auseinander. width:auto
     laesst die Tabelle genau so breit werden, wie ihr Inhalt braucht; ohne
     margin:auto steht sie damit von selbst linksbuendig. Bewusst ueber die
     id und nicht ueber die table-Regel, damit .nav-table unberuehrt bleibt. */
  #sessionList { width:auto; max-width:100%; }
  /* Zahlenspalten rechtsbuendig, Ziffern auf gleicher Breite - wortgleiches
     Muster wie #listHead th.num in analyze_spectrum.py (:8040). */
  #listHead th.num, tr.session-row td.num { text-align:right; font-variant-numeric:tabular-nums; }
  th .arrow { color:var(--accent); margin-left:4px; }
  /* T-807: der Kurztext steht in einem mehrzeiligen Feld, dessen Hoehe der des
     Thumbnails entspricht (Canvas 50 px + je 1 px Rahmen). Der gespeicherte
     Wert bleibt einzeilig - clean_label() in eb200_core/label_store.py
     entfernt Steuerzeichen inklusive Zeilenumbruch -, das Feld bricht den
     Text lediglich um. font-family:inherit, sonst faende der Browser die
     Monospace-Voreinstellung fuer textarea. */
  td.label-cell textarea { padding:2px 4px; width:220px; height:52px; font-size:1em;
                           font-family:inherit; box-sizing:border-box;
                           resize:vertical; vertical-align:top; }
  td.label-cell textarea.saved { border-color:var(--success); }
  td.label-cell textarea.err { border-color:var(--danger); }
  #noteText { width:100%; min-height:70px; resize:vertical; margin:4px 0; }
  #noteText.saved { border-color:var(--success); }
  .note-hint { color:var(--fg-muted); font-size:0.75em; min-height:1em; }
  #searchInput { width:320px; }
  tr.session-row { cursor:pointer; }
  tr.session-row:hover { background:var(--panel-2); }
  .live-badge { color:var(--success); font-weight:bold; font-size:0.75em; margin-left:6px; }
  #segmentCards { display:flex; flex-wrap:wrap; gap:12px; margin-top:12px; }
  .seg-card { background:var(--panel); border:1px solid var(--border); border-radius:6px;
              padding:10px; width:320px; cursor:pointer; }
  .seg-card:hover { border-color:var(--accent); }
  .seg-title { color:var(--accent); font-size:0.9em; font-weight:600; margin-bottom:4px; }
  .seg-sub { color:var(--fg-muted); font-size:0.78em; margin-bottom:6px; }
  .seg-thumb { display:block; background:var(--canvas-bg); border:1px solid var(--canvas-border); }
  .list-thumb { display:block; background:var(--canvas-bg); border:1px solid var(--canvas-border); margin-top:4px; }
  #sessionMeta { font-size:0.9em; color:var(--fg-muted); margin:8px 0; }
  #segHeader { color:var(--accent); font-size:1.05em; font-weight:600; margin-bottom:4px; }
  #segHint { color:var(--warn); font-size:0.82em; margin-bottom:8px; }
  .full-canvas-row { display:flex; align-items:flex-start; }

  /* Segment-Ansicht zweispaltig: Befund-Navigator links, Wasserfall rechts -
     beide als Karte (Muster :8040, T-1402). */
  .seg-grid { display:flex; gap:16px; align-items:flex-start; }
  .full-canvas-row { background:var(--panel); border:1px solid var(--border); border-radius:6px; padding:10px 12px; }
  .nav-col { flex:0 0 260px; max-width:260px; box-sizing:border-box;
             align-self:flex-start; position:sticky; top:8px;
             max-height:calc(100vh - 16px); overflow-y:auto;
             border:1px solid var(--border); border-radius:6px; background:var(--panel); padding:10px 12px; }
  .nav-col h2 { margin-top:0; font-size:1em; }
  .nav-group { margin:4px 0; border-bottom:1px solid var(--border); }
  .nav-group > summary { cursor:pointer; color:var(--fg); font-weight:600; font-size:0.92em; padding:5px 0; user-select:none; }
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
  #timeGutter { flex-shrink:0; margin-right:4px; background:var(--canvas-bg); }
  #fullCanvas, #freqAxis, #known-f { display:block; background:var(--canvas-bg); border:1px solid var(--canvas-border); }
  #tooltip { position:fixed; background:var(--panel); color:var(--fg); border:1px solid var(--border-strong);
             box-shadow:var(--shadow); padding:4px 8px;
             font-size:0.8em; pointer-events:none; display:none; }
  /* Scrollbarer Wasserfall-Viewport: fullCanvas wird so hoch wie noetig
     (1 Pixelzeile je Ausgabesweep, mind. Viewport-Hoehe) und laeuft hier
     vertikal scrollbar. Der Zeit-Gutter scrollt im selben Inneren mit; die
     Frequenzachse (seg-axis-row) bleibt fest darunter. Der 74px-Platzhalter
     (Gutter 70 + margin-right 4) haelt die Achse unter dem fullCanvas. */
  .seg-wf-viewport { display:inline-block; overflow-y:auto; overflow-x:hidden; background:var(--canvas-bg); }
  .seg-wf-inner { display:flex; align-items:flex-start; }
  .seg-axis-row { display:flex; align-items:flex-start; margin-top:4px; }
  .seg-axis-spacer { flex:0 0 74px; }
  .hover-tip { position:fixed; pointer-events:none; z-index:1000;
              background:var(--panel); color:var(--fg); border:1px solid var(--border-strong);
              box-shadow:var(--shadow);
              border-radius:3px; padding:2px 6px; font-size:0.8em;
              display:none; }
  #scaleRow { margin:10px 0; font-size:0.9em; display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
  #scaleRow label { display:flex; align-items:center; gap:8px; }
  #scaleRow input[type=range] { width:280px; }
  #analysisSection { margin-top:20px; border:1px dashed var(--border-strong); padding:12px; color:var(--fg-muted); }
  .view { display:none; }
  .toolbar { margin:8px 0 10px; }
</style>
</head>
<body>
/*HEADER*/

<div id="view-list" class="view">
  <h2>Sessions</h2>
  <div class="toolbar">
    <input type="text" id="searchInput" placeholder="Search in file / label ...">
  </div>
  <table id="sessionList">
    <thead><tr id="listHead"></tr></thead>
    <tbody id="sessionListBody"></tbody>
  </table>

  <h2>Open path</h2>
  <div class="toolbar">
    <input type="text" id="pathInput" placeholder="e.g. sessions/session_....db">
    <button id="openPathBtn">Open</button>
  </div>
  <div id="pathError"></div>
</div>

<div id="view-session" class="view">
  <div class="toolbar"><button id="backToListBtn" class="primary">&laquo; Back to list</button></div>
  <h2>Session</h2>
  <div id="sessionMeta"></div>
  <div id="segmentCards"></div>
</div>

<div id="view-segment" class="view">
  <div class="toolbar"><button id="backToSessionBtn" class="primary">&laquo; Back to overview</button></div>
  <div id="segHeader"></div>
  <div id="segHint"></div>
  <div id="scaleRow">
    <label>Scale min: <span id="fullScaleMinLabel">-30 dB&micro;V</span>
      <input type="range" id="fullScaleMin" min="-40" max="120" step="1" value="-30"></label>
    <label>Scale max: <span id="fullScaleMaxLabel">60 dB&micro;V</span>
      <input type="range" id="fullScaleMax" min="-40" max="120" step="1" value="60"></label>
    <button id="autoScaleSegBtn" title="Set the scale automatically from the data">Auto</button>
    <label><input type="checkbox" id="gridToggle"> Frequency grid</label>
  </div>
  <div class="seg-grid">
    <!-- NAVIGATOR: schmale Befund-Leiste. Schritt 1 = nur Geruest/Platzhalter;
         Abruf/Render und Klick-Markierung folgen in eigenen Schritten. -->
    <div class="nav-col" id="navCol">
      <h2>Findings navigator <span class="hint">(auto analysis; click marks)</span></h2>
      <details class="nav-group" open><summary>Continuous carriers</summary>
        <div class="nav-list" id="navCarriers"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div></details>
      <details class="nav-group" open><summary>Bursty channels</summary>
        <div class="nav-list" id="navBursts"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div></details>
      <details class="nav-group" open><summary>Strongest events</summary>
        <div class="nav-list" id="navEvents"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div></details>
      <details class="nav-group"><summary>Comb</summary>
        <div class="nav-list" id="navComb"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div></details>
      <details class="nav-group"><summary>Periodicity</summary>
        <div class="nav-list" id="navPeriodic"><div class="nav-empty">&ndash; not loaded yet &ndash;</div></div></details>
      <details class="nav-group" open><summary>Note</summary>
        <textarea id="noteText" maxlength="4000" rows="5" placeholder="Free text about this session ..."></textarea>
        <div class="note-hint" id="noteHint">&nbsp;</div></details>
    </div>
    <div class="full-canvas-row">
      <div>
        <div class="seg-wf-viewport" id="segViewport">
          <div class="seg-wf-inner">
            <canvas id="timeGutter" width="70" height="600"></canvas>
            <canvas id="fullCanvas" width="900" height="600"></canvas>
          </div>
        </div>
        <div class="seg-axis-row">
          <div class="seg-axis-spacer"></div>
          <canvas id="freqAxis" width="900" height="20"></canvas>
        </div>
        <!-- Katalog-Streifen (bekannte Baender/Kanaele aus known_freq/), JS-Block
             wortgleich aus waterfall.py (tools/sync_known_block.py). -->
        <div class="seg-axis-row">
          <div class="seg-axis-spacer"></div>
          <canvas id="known-f" width="900" height="96"></canvas>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="tooltip"></div>
<script>
// Fester Tab-Name (Fensterdisziplin: 1 Tab pro Server, siehe waterfall.py openServerTab).
window.name = 'eb200_sessions';
function el(id) { return document.getElementById(id); }

const KNOWN_STRIPS = ['known-f'];   // Streifen-Canvases dieser Seite (vor dem geteilten Block)
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
  ['list', 'session', 'segment'].forEach(v => {
    el('view-' + v).style.display = (v === name) ? 'block' : 'none';
  });
}

// dieselbe Farbmapping-Logik wie im Live-Wasserfall (waterfall.py)
function levelToColor(v, lo, hi) {
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
      return `rgb(${r0+f*(r1-r0)|0},${g0+f*(g1-g0)|0},${b0+f*(b1-b0)|0})`;
    }
  }
  return 'rgb(230,30,30)';
}

// wie levelToColor, aber als [r,g,b]-Array fuer putImageData (Cache-Aufbau in
// buildSegCache - deutlich schneller als tausende fillRect je Zeile).
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

function decodeLevels(data) {
  const bin = atob(data.levels_b64);
  const buf = new ArrayBuffer(bin.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i);
  return new Int16Array(buf);
}

// ---- Ebene 1: Session-Liste (sortierbar, durchsuchbar, mit Kurztext) ----

// Rohdaten der Liste plus aktueller Sortier-/Suchzustand. Die Tabelle wird aus
// diesem Zustand jederzeit neu aufgebaut (renderSessionTable) - Sortierung und
// Suche laufen damit rein clientseitig ohne erneuten Serverabruf.
let sessionItems = [];
let sortKey = 'date';   // aktuelles Sortierfeld (COLUMNS[].key)
let sortDir = -1;       // 1 = aufsteigend, -1 = absteigend (Start: neueste zuerst)
let searchTerm = '';    // Suchbegriff, bereits kleingeschrieben

// kleiner HTML-Escaper fuer Datei-/Kurztext (nur Anzeige)
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function fmtDate(tUnix) { return (tUnix != null) ? isoDate(new Date(tUnix * 1000)) : '-'; }
function fmtTime(tUnix) { return (tUnix != null) ? isoTime(new Date(tUnix * 1000)) : '-'; }
// T-805: Frequenzspanne mit der Breite in Klammern (fmtBreite, adaptiv
// MHz/kHz). Bei start == stop entfaellt die Klammer - "(0 kHz)" waere keine
// Auskunft.
function fmtSpan(a, b) {
  if (a == null || b == null) return '-';
  const txt = (a / 1e6).toFixed(3) + '-' + (b / 1e6).toFixed(3) + ' MHz';
  return (b > a) ? (txt + ' (' + fmtBreite(b - a) + ')') : txt;
}
// --- T-715: Textbausteine der Segmentanzeige ---
// Zahl mit hoechstens drei Nachkommastellen, ohne nachlaufende Nullen.
// Dezimaltrennzeichen bleibt der Punkt (Rest der Seite ebenso).
function trimNum(v) { return String(Math.round(v * 1000) / 1000); }
// Breitenangabe adaptiv (Muster formatBreite in waterfall.py, T-707):
// ab 1 MHz in MHz, darunter in kHz. Eingabe in Hz.
function fmtBreite(hz) {
  if (!(hz > 0)) return '–';
  return (hz >= 1e6) ? (trimNum(hz / 1e6) + ' MHz') : (trimNum(hz / 1e3) + ' kHz');
}
// Empfaenger-Bandbreite immer in kHz; null bei Sessions vor T-715.
function fmtBandbreite(hz) { return (hz > 0) ? (trimNum(hz / 1e3) + ' kHz') : '–'; }
// Dauer adaptiv: unter 1 min "5 s", unter 1 h "2:05 min", darueber "1:02:05 h".
function fmtDauer(sec) {
  if (!(sec >= 0)) return '–';
  const s = Math.round(sec);
  if (s < 60) return s + ' s';
  const m = Math.floor(s / 60), rs = s % 60;
  const rss = (rs < 10 ? '0' : '') + rs;
  if (m < 60) return m + ':' + rss + ' min';
  const h = Math.floor(m / 60), rm = m % 60;
  return h + ':' + (rm < 10 ? '0' : '') + rm + ':' + rss + ' h';
}
// Betriebszeile (BW-Zoom, Verstaerkung). Liefert '' fuer Sessions, die vor
// T-715 aufgezeichnet wurden - dort fehlen die Spalten, die Zeile entfaellt
// dann ersatzlos statt "unbekannt" zu behaupten.
function fmtSegBetrieb(seg) {
  const teile = [];
  if (seg.bw_zoom != null) teile.push('BW-Zoom: ' + (seg.bw_zoom ? 'on' : 'off'));
  if (seg.gain_mode === 'AGC') {
    teile.push('AGC: auto');
  } else if (seg.gain_mode === 'MGC') {
    teile.push(seg.gain_mgc_dbuv != null
      ? ('MGC: ' + seg.gain_mgc_dbuv + ' dBµV') : 'AGC: off');
  }
  return teile.join(' · ');
}
// Kurztext-Eingabefeld (nur Feldinhalt; das <td> setzt der Tabellenaufbau ueber
// cls). Inhalt/data-path werden escaped.
// T-807: textarea statt input - nur wegen der Hoehe (sie soll der des
// Thumbnails entsprechen) und damit laengerer Text umbricht statt zu
// scrollen. Der Wert bleibt einzeilig: maxlength 128 wie bisher
// (MAX_LABEL_LEN), und Enter speichert, statt eine Zeile einzufuegen (siehe
// Verdrahtung in renderSessionTable).
function labelInputHTML(it) {
  return '<textarea maxlength="128" data-path="' + esc(it.path) + '">' +
         esc(it.label || '') + '</textarea>';
}

// T-805: Dauer einer Session aus t_first/t_last. Beide koennen bei einer
// leeren oder abgebrochenen Session fehlen - Rueckgabe dann null ("keine
// Angabe"), nicht 0.
function sessDauer(it) {
  return (it.t_first != null && it.t_last != null && it.t_last >= it.t_first)
    ? (it.t_last - it.t_first) : null;
}

// Spaltendefinition: Kopftext, Sortier-Zugriff (num=true -> numerisch) und
// Zellen-Renderer je Spalte an einer Stelle. 'date'/'begin' sortieren beide
// chronologisch ueber t_first (started_at als Rueckfall).
// Reihenfolge seit T-805: Datei mit Spektrogramm, direkt danach der Kurztext
// (er gehoert zur Wiedererkennung der Aufzeichnung, nicht ans Zeilenende),
// erst dahinter die Messwerte. Segmente und Sweeps stehen in zwei eigenen,
// getrennt sortierbaren Spalten - eine gemeinsame Spalte liess sich nur nach
// einem der beiden Werte ordnen.
const COLUMNS = [
  { key:'name',  label:'File',             num:false,
    sort: it => (it.name || '').toLowerCase(),
    cell: it => esc(it.name) + (it.live ? ' <span class="live-badge">LIVE</span>' : '') +
      (it.first_segment_id != null
        ? '<canvas class="list-thumb" width="160" height="50" data-path="' + esc(it.path) +
          '" data-segid="' + it.first_segment_id + '"></canvas>'
        : '') },
  { key:'label', label:'Label',          num:false, cls:'label-cell',
    sort: it => (it.label || '').toLowerCase(),
    cell: it => labelInputHTML(it) },
  { key:'date',  label:'Date',             num:true,
    sort: it => (it.t_first != null ? it.t_first : it.started_at) || 0,
    cell: it => fmtDate(it.t_first != null ? it.t_first : it.started_at) },
  { key:'begin', label:'Start',            num:true,
    sort: it => (it.t_first != null ? it.t_first : it.started_at) || 0,
    cell: it => fmtTime(it.t_first != null ? it.t_first : it.started_at) },
  { key:'end',   label:'End',              num:true,
    sort: it => it.t_last || 0,
    cell: it => fmtTime(it.t_last) },
  { key:'dauer', label:'Duration',             num:true,
    sort: it => sessDauer(it) || 0,
    cell: it => { const d = sessDauer(it); return (d != null) ? fmtDauer(d) : '–'; } },
  { key:'span',  label:'Frequency span',    num:true,
    sort: it => it.start_hz || 0,
    cell: it => fmtSpan(it.start_hz, it.stop_hz) },
  // T-807: cls 'num' gilt fuer th und td (siehe buildHead und die CSS-Regel
  // #listHead th.num) - Zahlen stehen rechtsbuendig.
  { key:'segs',  label:'Segments',          num:true, cls:'num',
    sort: it => it.n_segments || 0,
    cell: it => String(it.n_segments) },
  { key:'sweeps', label:'Sweeps',           num:true, cls:'num',
    sort: it => it.row_count || 0,
    cell: it => String(it.row_count) },
  { key:'actions', label:'Action',          num:false, cls:'actions-cell',
    sort: () => 0,
    cell: it => '<button class="deleteBtn danger" data-path="' + esc(it.path) + '">Delete</button>' +
      '<span class="delete-error"></span>' },
];

// Kopfzeile mit Sortierpfeil aufbauen. Klick auf eine Spalte: erst aufsteigend,
// erneuter Klick auf dieselbe Spalte kehrt die Richtung um.
function buildHead() {
  const head = el('listHead');
  head.innerHTML = '';
  COLUMNS.forEach(col => {
    const th = document.createElement('th');
    // T-807: cls der Spalte auch auf die Kopfzelle (Muster buildHead in
    // analyze_spectrum.py) - sonst stuende nur die Zelle rechtsbuendig, die
    // Ueberschrift daneben aber links.
    th.className = 'sortable' + (col.cls ? ' ' + col.cls : '');
    let txt = col.label;
    if (sortKey === col.key) txt += ' <span class="arrow">' + (sortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
    th.innerHTML = txt;
    th.onclick = () => {
      if (sortKey === col.key) sortDir = -sortDir;
      else { sortKey = col.key; sortDir = 1; }
      renderSessionTable();
    };
    head.appendChild(th);
  });
}

async function loadSessionList() {
  buildHead();
  const tbody = el('sessionListBody');
  tbody.innerHTML = '<tr><td colspan="' + COLUMNS.length + '">loading ...</td></tr>';
  try {
    sessionItems = await fetchJSON('/api/sessions');
    renderSessionTable();
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="' + COLUMNS.length +
      '" class="err">Error: ' + e.message + '</td></tr>';
  }
}

// Baut die Tabelle aus sessionItems unter Beruecksichtigung von Suche und
// Sortierung neu auf. Ungueltige Dateien bleiben sichtbar (ueber den Dateinamen
// durchsuchbar) und stehen immer am Ende.
function renderSessionTable() {
  buildHead();
  const tbody = el('sessionListBody');
  tbody.innerHTML = '';

  let rows = sessionItems.slice();
  if (searchTerm) {
    rows = rows.filter(it =>
      (it.name || '').toLowerCase().includes(searchTerm) ||
      (it.label || '').toLowerCase().includes(searchTerm));
  }
  const col = COLUMNS.find(c => c.key === sortKey) || COLUMNS[0];
  rows.sort((a, b) => {
    if (!a.valid && !b.valid) return 0;
    if (!a.valid) return 1;              // ungueltige immer ans Ende
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
      tr.innerHTML = '<td>' + esc(it.name) + '</td><td colspan="' + (COLUMNS.length - 1) +
        '" class="err">invalid/damaged session DB</td>';
      tbody.appendChild(tr);
      return;
    }
    tr.className = 'session-row';
    tr.innerHTML = COLUMNS.map(c =>
      '<td' + (c.cls ? ' class="' + c.cls + '"' : '') + '>' + c.cell(it) + '</td>').join('');
    // Direktsprung: bei genau 1 Segment fuehrt der Klick sofort in die
    // Analyse-Ansicht (kein Umweg mehr ueber die Segment-Karten-Uebersicht).
    // Bei mehreren Segmenten bleibt der bisherige Weg ueber openSession()
    // unveraendert, da dort erst das gewuenschte Segment gewaehlt werden muss.
    tr.onclick = () => {
      if (it.n_segments === 1 && it.first_segment_id != null) {
        segmentReturnView = 'list';
        openSegment(it.path, { id: it.first_segment_id });
      } else {
        segmentReturnView = 'session';
        openSession(it.path);
      }
    };
    tbody.appendChild(tr);

    // Kurztext-Feld verdrahten: Klick nicht an die Zeile durchreichen,
    // Speichern bei Verlassen/Enter.
    // T-807: das Feld ist jetzt eine textarea. Enter behaelt seine bisherige
    // Bedeutung "speichern" - preventDefault verhindert den Zeilenumbruch,
    // den der Server ohnehin wieder entfernen wuerde (clean_label).
    const input = tr.querySelector('textarea[data-path]');
    if (input) {
      input.onclick = ev => ev.stopPropagation();
      input.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); } };
      input.onchange = () => saveLabel(input, it);
    }

    // Loesch-Knopf verdrahten: Klick nicht an die Zeile durchreichen,
    // Bestaetigung per confirm(), Fehlertext (z.B. 409 bei laufender
    // Session) erscheint sichtbar in der Zeile (nicht nur als Tooltip).
    const delBtn = tr.querySelector('button.deleteBtn');
    if (delBtn) {
      delBtn.onclick = ev => {
        ev.stopPropagation();
        if (!confirm('Delete session "' + it.name + '" permanently? ' +
            '(including findings, label and note - cannot be undone)')) return;
        deleteSession(delBtn, it);
      };
    }

    // Thumbnail unterhalb des Dateinamens: erst laden, wenn die Zeile
    // tatsaechlich sichtbar wird (IntersectionObserver, wie bei den
    // Segment-Karten in buildSegmentCard).
    const thumbCanvas = tr.querySelector('canvas.list-thumb');
    if (thumbCanvas) {
      const thumbObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            loadThumb(thumbCanvas.dataset.path, thumbCanvas.dataset.segid, thumbCanvas);
            thumbObserver.disconnect();
          }
        });
      });
      thumbObserver.observe(thumbCanvas);
    }
  });
}

// Kurztext speichern (POST /api/label). Erfolg -> kurzer gruener Rahmen; der
// server-bereinigte Wert wird uebernommen.
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

// Session komplett loeschen (POST /api/delete). Erfolg -> Liste neu laden;
// Schutzverstoss (409, z.B. laufende Session) -> Fehlertext in der Zeile.
async function deleteSession(btn, it) {
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
    await loadSessionList();
  } catch (e) {
    errSpan.textContent = e.message;
  }
}

// Suchfeld: bei jeder Eingabe filtern (rein clientseitig).
el('searchInput').oninput = e => {
  searchTerm = e.target.value.trim().toLowerCase();
  renderSessionTable();
};

el('openPathBtn').onclick = () => {
  const p = el('pathInput').value.trim();
  if (!p) return;
  openSession(p);
};

async function openSession(path) {
  el('pathError').textContent = '';
  try {
    const data = await fetchJSON('/api/session?path=' + encodeURIComponent(path));
    renderSession(path, data);
    showView('session');
  } catch (e) {
    el('pathError').textContent = 'Error: ' + e.message;
  }
}

el('backToListBtn').onclick = () => showView('list');

// ---- Ebene 1: Segment-Karten einer Session ----

let currentSessionPath = null;
let segmentReturnView = 'session';   // wohin der "Zurueck"-Knopf aus der Analyse-Ansicht
                                      // fuehrt: 'session' (klassischer Weg ueber die
                                      // Segment-Karten) oder 'list' (Direktsprung aus der
                                      // Liste bei genau 1 Segment)
const THUMB_SCALE_MIN = -30, THUMB_SCALE_MAX = 60;

function renderSession(path, data) {
  currentSessionPath = path;
  const s = data.session || {};
  el('sessionMeta').textContent =
    path + ' - Start: ' + (s.started_at ? isoDateTime(new Date(s.started_at * 1000)) : '-') +
    ' - Source: ' + (s.source || '-') + ' - Mode: ' + (s.mode || '-') +
    ' - ' + data.segments.length + ' segments';
  const container = el('segmentCards');
  container.innerHTML = '';
  data.segments.forEach(seg => container.appendChild(buildSegmentCard(path, seg)));
}

function buildSegmentCard(path, seg) {
  const card = document.createElement('div');
  card.className = 'seg-card';
  const dauer = (seg.t_last > seg.t_first) ? (seg.t_last - seg.t_first) : 0;
  const rate = (dauer > 0) ? (seg.row_count / dauer) : 0;
  const betrieb = fmtSegBetrieb(seg);
  card.innerHTML =
    '<div class="seg-title">' + (seg.start_hz / 1e3).toFixed(0) + '-' + (seg.stop_hz / 1e3).toFixed(0) +
    ' kHz (' + fmtBreite(seg.stop_hz - seg.start_hz) + ')</div>' +
    '<div class="seg-sub">' + fmtTime(seg.t_first) + ' - ' + fmtTime(seg.t_last) +
    ' (' + fmtDauer(dauer) + ')</div>' +
    '<div class="seg-sub">Step size ' + seg.step_hz + ' Hz (bandwidth: ' +
    fmtBandbreite(seg.bw_hz) + ', ' + seg.n_bins + ' bins)</div>' +
    (betrieb ? ('<div class="seg-sub">' + betrieb + '</div>') : '') +
    '<div class="seg-sub">' + seg.row_count + ' ' + pluralWort(seg.row_count, 'Sweep', 'Sweeps') + ', ' + rate.toFixed(2) + ' Sweeps/s</div>' +
    '<canvas class="seg-thumb" width="300" height="120"></canvas>';
  card.onclick = () => { segmentReturnView = 'session'; openSegment(path, seg); };

  const canvas = card.querySelector('canvas');
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        loadThumb(path, seg.id, canvas);
        observer.disconnect();
      }
    });
  });
  observer.observe(canvas);
  return card;
}

async function loadThumb(path, segId, canvas) {
  try {
    const data = await fetchJSON('/api/segment?path=' + encodeURIComponent(path) + '&id=' + segId + '&mode=thumb');
    drawMatrix(canvas, data, THUMB_SCALE_MIN, THUMB_SCALE_MAX);
  } catch (e) {
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#222'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#c66'; ctx.font = '10px sans-serif';
    ctx.fillText('Error: ' + e.message, 4, 14);
  }
}

// zeichnet die (ggf. bereits server-seitig dezimierte) Pegelmatrix als
// Wasserfall-Canvas - Werte sind 0,1-dBuV (Rohwert / 10 fuers Mapping).
function drawMatrix(canvas, data, lo, hi) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  const nRows = data.n_rows, nBins = data.n_bins;
  if (nRows === 0 || nBins === 0) return;
  const levels = decodeLevels(data);
  for (let y = 0; y < h; y++) {
    const row = Math.min(nRows - 1, Math.floor(y * nRows / h));
    const base = row * nBins;
    for (let x = 0; x < w; x++) {
      const bin = Math.min(nBins - 1, Math.floor(x * nBins / w));
      const v = levels[base + bin] / 10;
      ctx.fillStyle = levelToColor(v, lo, hi);
      ctx.fillRect(x, y, 1, 1);
    }
  }
}

// ---- Ebene 2: Segment-Vollansicht ----

let fullScaleMin = -30, fullScaleMax = 60;
let currentFullData = null;
let segCache = null;         // Offscreen-Canvas: eingefaerbte Pegelmatrix (Farbe haengt an der Skala)
let segScaleDebounce = null; // verzoegert den Cache-Neubau beim Ziehen der Skalenregler

// ---- Frequenz-/Pegel-Anzeige an der Mausposition (Wasserfall) ----
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

// x -> Frequenz (linear ueber start_hz..stop_hz); Pegel aus der dekodierten
// Matrix, einmal je Segment gecached auf currentFullData._levels.
function hoverReadoutSeg(xf, yf) {
  const d = currentFullData;
  if (!d || d.n_bins <= 0 || d.n_rows <= 0) return '';
  const f = (d.start_hz + xf * (d.stop_hz - d.start_hz)) / 1e6;
  if (!d._levels) d._levels = decodeLevels(d);
  const bin = Math.min(d.n_bins - 1, Math.floor(xf * d.n_bins));
  const row = Math.min(d.n_rows - 1, Math.floor(yf * d.n_rows));
  const v = d._levels[row * d.n_bins + bin] / 10;
  // Katalogtext (bekannter Kanal/Band) anhaengen; Pixeldichte des Wasserfalls.
  const known = knownTextAt(d.start_hz + xf * (d.stop_hz - d.start_hz), el('fullCanvas').width / (d.stop_hz - d.start_hz));
  return f.toFixed(5) + ' MHz: ' + v.toFixed(1) + ' dBµV' + (known ? KNOWN_SEP + known : '');
}

attachHover('fullCanvas', hoverReadoutSeg);
let befundMarker = null;   // {freqHz, tOffset} oder null (Klick-Markierung)

async function openSegment(path, seg) {
  showView('segment');
  el('segHeader').textContent = 'loading ...';
  el('segHint').textContent = '';
  el('noteText').value = '';   // keine Notiz der vorigen Session stehen lassen
  el('noteHint').textContent = ' ';
  try {
    const data = await fetchJSON('/api/segment?path=' + encodeURIComponent(path) + '&id=' + seg.id + '&mode=full');
    currentFullData = data;
    renderFullSegment(data);
    loadBefund(path, seg.id);   // Auto-Analyse-Befund fuer dieses Segment nachladen (nicht blockierend)
    loadNote(path);             // gespeicherte Notiz zu dieser Session nachladen (nicht blockierend)
  } catch (e) {
    el('segHeader').textContent = 'Error: ' + e.message;
  }
}

function renderFullSegment(data) {
  befundMarker = null;   // Markierung beim Oeffnen eines Segments zuruecksetzen
  // Einzeilig (Platz im Kopf), aber gleiche Bausteine und gleiche Schreibweise
  // wie die Segmentkarten - siehe fmtBreite/fmtBandbreite/fmtSegBetrieb.
  const betrieb = fmtSegBetrieb(data);
  el('segHeader').textContent =
    (data.start_hz / 1e3).toFixed(0) + '-' + (data.stop_hz / 1e3).toFixed(0) +
    ' kHz (' + fmtBreite(data.stop_hz - data.start_hz) + ') · step size ' +
    data.step_hz + ' Hz (bandwidth: ' + fmtBandbreite(data.bw_hz) + ', ' +
    data.n_bins + ' bins)' + (betrieb ? (' · ' + betrieb) : '') +
    ' · ' + data.n_rows + ' rows';
  el('segHint').textContent = data.decimated
    ? 'Too many sweeps to show in full. ' + data.n_rows_raw +
      ' ' + pluralWort(data.n_rows_raw, 'sweep', 'sweeps') + ', decimation by a factor of ' + data.decimation
    : '';
  data._levels = decodeLevels(data);       // einmal dekodieren (Auto-Skala, Cache, Hover)
  autoScaleSegWaterfall(data);             // Skala datengetrieben vorbelegen
  // Layout erst nach dem Sichtbarschalten vermessen (view-segment ist bereits
  // sichtbar): Viewport-Hoehe setzen, Canvas-Groesse (Breite aus der Fenster-
  // breite, Hoehe 1 Zeile/Sweep), Cache faerben, dann zeichnen. rAF stellt
  // sicher, dass das Layout bereits steht.
  requestAnimationFrame(() => {
    fitSegWaterfallHeight();
    sizeSegWaterfall(data);
    buildSegCache(data);
    drawFullView(data);
  });
}

// ---- Befund-Navigator: Auto-Analyse-Befund laden und rendern ----

// Setzt in allen fuenf Gruppen denselben Platzhaltertext.
function befundPlaceholder(text) {
  ['navCarriers', 'navBursts', 'navEvents', 'navComb', 'navPeriodic'].forEach(id => {
    el(id).innerHTML = '<div class="nav-empty">' + text + '</div>';
  });
}

// Laedt den Session-Befund (deckt alle Segmente ab) und rendert den Eintrag des
// offenen Segments. Asynchron, blockiert den Wasserfall NICHT.
async function loadBefund(path, segId) {
  befundPlaceholder('&ndash; analyzing &ndash;');
  try {
    const rep = await fetchJSON('/api/befund?path=' + encodeURIComponent(path));
    renderBefund(rep, segId);
  } catch (e) {
    befundPlaceholder('analysis failed');
  }
}

// ---- Notiz je Session-DB: mehrzeiliger Freitext, getrennt vom Kurztext und
// vom regenerierbaren Befund-Sidecar gespeichert (siehe NOTES im Server). ----

async function loadNote(path) {
  const ta = el('noteText');
  ta.dataset.path = path;
  try {
    const data = await fetchJSON('/api/note?path=' + encodeURIComponent(path));
    ta.value = data.note || '';
    el('noteHint').textContent = ' ';
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

// Eine Befundzeile; data-freq (Hz) / data-t (s, segment-lokal) tragen die
// Koordinaten fuer die spaetere Klick-Markierung (hier noch ohne Handler).
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

// Rendert den Befund-Eintrag zum offenen Segment (segment_id == segId).
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

function renderBefund(report, segId) {
  const segs = (report && report.segments) ? report.segments : [];
  const s = segs.find(x => x.segment_id === segId);
  if (!s) {
    ['navCarriers', 'navBursts', 'navEvents', 'navComb', 'navPeriodic'].forEach(id =>
      el(id).innerHTML = '<div class="nav-empty">&ndash; no findings for this segment &ndash;</div>');
    setCount('navCarriers', 'Continuous carriers', 0);
    setCount('navBursts', 'Bursty channels', 0);
    setCount('navEvents', 'Strongest events', 0);
    setCount('navComb', 'Comb', 0);
    setCount('navPeriodic', 'Periodicity', 0);
    return;
  }

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

// ---- Auto-Skalierung + scrollbarer Wasserfall (Segment-Vollansicht) ----

// Perzentile der dekodierten Pegelmatrix (Rueckgabe in dBuV). Arbeitet auf
// einem Subsample - reicht fuer eine stabile Skalenwahl und bleibt auch bei
// grossen Segmenten schnell. Gleiche Logik wie in analyze_spectrum.py.
function segPercentiles(levels, fracs) {
  const stepN = Math.max(1, Math.floor(levels.length / 60000));
  const s = [];
  for (let i = 0; i < levels.length; i += stepN) s.push(levels[i]);
  s.sort((a, b) => a - b);
  return fracs.map(f => {
    const idx = Math.max(0, Math.min(s.length - 1, Math.floor(f * (s.length - 1))));
    return s[idx] / 10;
  });
}

// Wasserfall-Farbskala datengetrieben vorbelegen: unterer Wert nahe dem
// Rauschsockel (30. Perzentil -> Rauschen wird dunkel), oberer Wert an den
// Signalspitzen (99,5. Perzentil). Setzt fullScaleMin/Max samt Reglern/Labels.
function autoScaleSegWaterfall(data) {
  const levels = data._levels || (data._levels = decodeLevels(data));
  const [lo, hi] = segPercentiles(levels, [0.30, 0.995]);
  fullScaleMin = Math.max(-40, Math.min(120, Math.floor(lo)));
  fullScaleMax = Math.max(-40, Math.min(120, Math.ceil(hi)));
  if (fullScaleMax - fullScaleMin < 10) fullScaleMax = Math.min(120, fullScaleMin + 10);
  el('fullScaleMin').value = String(fullScaleMin);
  el('fullScaleMax').value = String(fullScaleMax);
  el('fullScaleMinLabel').textContent = fullScaleMin + ' dBµV';
  el('fullScaleMaxLabel').textContent = fullScaleMax + ' dBµV';
}

// Hoehe des scrollbaren Viewports: fuellt vom Viewport-Oberrand bis kurz vor
// den Fensterboden. Danach sizeSegWaterfall() fuer die interne Canvas-Groesse.
function fitSegWaterfallHeight() {
  const vp = el('segViewport');
  const vpTop = vp.getBoundingClientRect().top;
  // Unter dem Viewport liegen Frequenzachse (seg-axis-row, ~24 px) und
  // Katalog-Streifen (~40 px) - beide muessen ohne Scrollen sichtbar bleiben.
  const BELOW_PX = 48 + 64;
  vp.style.height = Math.max(300, Math.round(window.innerHeight - vpTop - BELOW_PX)) + 'px';
}

// Interne Canvas-Groesse der Segment-Vollansicht.
//
// Breite (T-720): der Wasserfall nutzt die freie Fensterbreite statt der
// bisherigen festen 900 px. Frequenzachse (freqAxis) und Katalog-Streifen
// (known-f) bekommen denselben Wert - beide sitzen hinter demselben 74-px-
// Spacer (.seg-axis-spacer) wie der Zeit-Gutter, deshalb fluchten sie nur bei
// gleicher Breite. Muster und Grenzen wie sizeWaterfall() in
// analyze_spectrum.py (T-1409a). Zusammensetzung von SEG_SIDE_PX - einzige
// Stellschraube, nach Layout-Aenderungen nachrechnen:
//    40  Body-Padding links+rechts (BASE_CSS)
//   260  Befund-Navigator (.nav-col, border-box)
//    16  Spaltenluecke (.seg-grid gap)
//    26  Karte der Wasserfall-Spalte (2x12 Innenabstand + 2x1 Rahmen)
//    74  Zeit-Gutter (70) + dessen margin-right (4)
//     2  linker/rechter Rahmen des fullCanvas
//    17  vertikaler Scrollbalken des Viewports
//
// Hoehe: 1 Pixelzeile je Ausgabesweep (mind. Viewport-Hoehe). So wird nichts
// mehr in eine feste 600px-Hoehe gequetscht; bei mehr Zeilen als Viewport-
// Hoehe entsteht ein vertikaler Scrollbalken. Der Zeit-Gutter bekommt dieselbe
// Hoehe und scrollt im selben Inneren mit.
//
// Das Setzen von canvas.width leert die Zeichenflaeche - unkritisch, weil
// beide Aufrufer unmittelbar buildSegCache() + drawFullView() nachziehen.
const SEG_SIDE_PX = 40 + 260 + 16 + 26 + 74 + 2 + 17;
function sizeSegWaterfall(data) {
  const vp = el('segViewport');
  const w = Math.max(720, Math.min(3200,
    Math.floor((window.innerWidth - SEG_SIDE_PX) / 4) * 4));
  el('fullCanvas').width = w;
  el('freqAxis').width = w;
  el('known-f').width = w;
  const viewH = Math.max(200, vp.clientHeight);
  const h = Math.max(data.n_rows, viewH);
  el('fullCanvas').height = h;
  el('timeGutter').height = h;
}

// Faerbt die gesamte Pegelmatrix EINMAL in einen Offscreen-Cache (putImageData
// statt Millionen einzelner fillRect - schnell genug auch fuer sehr hohe
// Canvas). Der Cache haengt an Skala + Canvas-Groesse: neu bauen nur bei Oeffnen,
// Skalenwechsel oder Groessenaenderung. drawFullView blendet danach nur noch
// den Cache + Marker ein.
function buildSegCache(data) {
  const w = el('fullCanvas').width, h = el('fullCanvas').height;
  segCache = document.createElement('canvas');
  segCache.width = w; segCache.height = h;
  const ctx = segCache.getContext('2d');
  const img = ctx.createImageData(w, h);
  const nRows = data.n_rows, nBins = data.n_bins;
  if (nRows === 0 || nBins === 0) { ctx.putImageData(img, 0, 0); return; }
  const levels = data._levels || (data._levels = decodeLevels(data));
  const lo = fullScaleMin, hi = fullScaleMax;
  for (let y = 0; y < h; y++) {
    const row = Math.min(nRows - 1, Math.floor(y * nRows / h));
    const base = row * nBins;
    for (let x = 0; x < w; x++) {
      const bin = Math.min(nBins - 1, Math.floor(x * nBins / w));
      const col = levelToColorRGB(levels[base + bin] / 10, lo, hi);
      const p = (y * w + x) * 4;
      img.data[p] = col[0]; img.data[p + 1] = col[1]; img.data[p + 2] = col[2]; img.data[p + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
}

// Skalenregler-Eingabe: Cache-Neubau ist bei hohen Canvas teuer -> nach dem
// letzten Zug leicht verzoegern (die Labels reagieren sofort).
function onSegScaleInput() {
  if (segScaleDebounce) clearTimeout(segScaleDebounce);
  segScaleDebounce = setTimeout(() => {
    if (currentFullData) { buildSegCache(currentFullData); drawFullView(currentFullData); }
  }, 120);
}

// haelt eine Zeit-Markierung im sichtbaren Bereich des Viewports (nur bei Klick
// auf eine Befundzeile, nicht beim Regler-Ziehen).
function scrollSegToMarker() {
  if (!befundMarker || befundMarker.tOffset == null || !currentFullData) return;
  const data = currentFullData, c = el('fullCanvas'), vp = el('segViewport');
  if (!data.t_unix || data.n_rows <= 0) return;
  const target = data.t_unix[0] + befundMarker.tOffset;
  let best = 0, bestD = Infinity;
  for (let k = 0; k < data.n_rows; k++) {
    const dd = Math.abs(data.t_unix[k] - target);
    if (dd < bestD) { bestD = dd; best = k; }
  }
  const y = Math.floor((best + 0.5) * c.height / data.n_rows);
  const top = vp.scrollTop, bot = top + vp.clientHeight;
  if (y < top + 16 || y > bot - 16) vp.scrollTop = Math.max(0, y - vp.clientHeight / 2);
}

// Zeichnet nur noch den gecachten Wasserfall + Achsen + Klick-Marker (guenstig,
// auch bei sehr hohem Canvas). Der teure Neuaufbau steckt in buildSegCache.
function drawFullView(data) {
  const ctx = el('fullCanvas').getContext('2d');
  if (segCache) ctx.drawImage(segCache, 0, 0);
  if (gridEnabled) drawFreqGridOverlay(ctx, el('fullCanvas').width, el('fullCanvas').height, data.start_hz, data.stop_hz);
  drawFreqAxis(el('freqAxis'), data);
  updateKnownStrip('known-f', data.start_hz, data.stop_hz);
  drawTimeGutter(el('timeGutter'), data);
  drawBefundMarker();
}

// Zeichnet die Klick-Markierung auf den Wasserfall: senkrechte Linie an der
// Frequenz, waagrechte an der Zeit (nur Ereignisse). Geometrie wie drawMatrix.
// Darstellung: weiss gestrichelt (3 px) ueber schwarzer Kontur (5 px, durch-
// gezogen) - eine duenne rote Linie ging auf dem roten Ende der Farbskala unter.
function drawBefundMarker() {
  if (!befundMarker || !currentFullData) return;
  const data = currentFullData;
  const c = el('fullCanvas'), ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  let mx = null, my = null;
  if (befundMarker.freqHz != null && data.n_bins > 0) {
    const bin = Math.max(0, Math.min(data.n_bins - 1,
      Math.round((befundMarker.freqHz - data.start_hz) / data.step_hz)));
    mx = Math.floor((bin + 0.5) * w / data.n_bins) + 0.5;
  }
  if (befundMarker.tOffset != null && data.t_unix && data.n_rows > 0) {
    const target = data.t_unix[0] + befundMarker.tOffset;
    let best = 0, bestD = Infinity;
    for (let k = 0; k < data.n_rows; k++) {
      const dd = Math.abs(data.t_unix[k] - target);
      if (dd < bestD) { bestD = dd; best = k; }
    }
    my = Math.floor((best + 0.5) * h / data.n_rows) + 0.5;
  }
  if (mx == null && my == null) return;
  ctx.save();
  // Durchgang 1: schwarze Kontur, durchgezogen, breiter als die Strichlinie
  ctx.setLineDash([]);
  ctx.strokeStyle = 'rgba(0,0,0,0.85)'; ctx.lineWidth = 5;
  ctx.beginPath();
  if (mx != null) { ctx.moveTo(mx, 0); ctx.lineTo(mx, h); }
  if (my != null) { ctx.moveTo(0, my); ctx.lineTo(w, my); }
  ctx.stroke();
  // Durchgang 2: weisse Strichlinie darueber
  ctx.setLineDash([9, 6]);
  ctx.strokeStyle = 'rgba(255,255,255,0.95)'; ctx.lineWidth = 3;
  ctx.beginPath();
  if (mx != null) { ctx.moveTo(mx, 0); ctx.lineTo(mx, h); }
  if (my != null) { ctx.moveTo(0, my); ctx.lineTo(w, my); }
  ctx.stroke();
  ctx.restore();   // stellt Strichmuster, Breite und Farbe fuer andere Zeichner wieder her
}

// Klick auf eine Befundzeile: Markierung setzen und Wasserfall neu zeichnen.
function jumpToBefund(item) {
  if (!currentFullData) return;
  const hasFreq = item.dataset.freq != null && item.dataset.freq !== '';
  const hasT = item.dataset.t != null && item.dataset.t !== '';
  if (!hasFreq && !hasT) return;
  document.querySelectorAll('#navCol .nav-item.active').forEach(n => n.classList.remove('active'));
  item.classList.add('active');
  befundMarker = {
    freqHz: hasFreq ? parseFloat(item.dataset.freq) : null,
    tOffset: hasT ? parseFloat(item.dataset.t) : null
  };
  drawFullView(currentFullData);   // Matrix + Achsen + Marker neu zeichnen
  scrollSegToMarker();             // bei Zeit-Sprung in die Sichtbarkeit scrollen
}

// Delegierter Klick auf die Navigator-Leiste (gilt fuer dynamische Zeilen).
el('navCol').addEventListener('click', ev => {
  const item = ev.target.closest('.nav-item');
  if (item) jumpToBefund(item);
});

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
function fmtAxisMHz(freqHz) { return (freqHz / 1e6).toFixed(3) + ' MHz'; }

function drawFreqAxis(canvas, data) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const lo = data.start_hz, hi = data.stop_hz, span = hi - lo;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  ctx.font = '10px sans-serif';
  if (!(span > 0)) return;
  // Bereichsgrenzen gedimmt an den Raendern, Nice-Ticks dazwischen (dieselben
  // Positionen wie das Raster-Overlay, siehe drawFreqGridOverlay).
  // T-2007: ein Tick-Label erscheint nur, wenn es neben den Randlabels Platz
  // hat - gemessen mit measureText statt eines festen Randabstands; das
  // Randlabel hat Vorrang (Muster tickLabelFrei in waterfall.py).
  const loLabel = fmtAxisMHz(lo), hiLabel = fmtAxisMHz(hi);
  ctx.strokeStyle = '#555'; ctx.fillStyle = '#777';
  ctx.textAlign = 'left';  ctx.fillText(loLabel, 2, h - 3);
  ctx.textAlign = 'right'; ctx.fillText(hiLabel, w - 2, h - 3);
  const LABEL_GAP_PX = 6;
  const linksBis = 2 + ctx.measureText(loLabel).width + LABEL_GAP_PX;
  const rechtsAb = w - 2 - ctx.measureText(hiLabel).width - LABEL_GAP_PX;
  ctx.fillStyle = '#aaa'; ctx.textAlign = 'center';
  for (const f of niceTicksHz(lo, hi)) {
    const x = ((f - lo) / span) * w;
    const label = fmtAxisMHz(f);
    const halb = ctx.measureText(label).width / 2;
    if (x - halb < linksBis || x + halb > rechtsAb) continue;
    ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, 5); ctx.stroke();
    ctx.fillText(label, x, h - 3);
  }
}

// Frequenzraster als OVERLAY ueber den gecachten Wasserfall: "Halo"-Linie
// (3 px dunkler Saum + 1 px helle Mitte), auf jedem Untergrund erkennbar und
// ohne eine Farbe der Pegel-Colormap zu imitieren. Wird NACH dem Cache-Blit
// gezeichnet - der Cache bleibt unberuehrt, Ausschalten = einfach nicht
// mehr zeichnen. Tick-Positionen = dieselben wie in drawFreqAxis.
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

// Zeit-Gutter aus den ECHTEN t_unix-Werten je Ausgabezeile (nicht dem
// Zeilenindex) - Sweep-Abstaende sind ungleichmaessig.
function drawTimeGutter(canvas, data) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  const nRows = data.n_rows;
  if (nRows === 0) return;
  ctx.fillStyle = '#aaa'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
  const nLabels = Math.max(2, Math.min(nRows, Math.floor(h / 60)));
  for (let i = 0; i < nLabels; i++) {
    const frac = nLabels <= 1 ? 0 : i / (nLabels - 1);
    const rowIdx = Math.min(nRows - 1, Math.floor(frac * (nRows - 1)));
    const y = Math.floor(frac * (h - 1));
    ctx.fillText(isoTime(new Date(data.t_unix[rowIdx] * 1000)), w - 4, y + 4);
  }
}

el('fullScaleMin').oninput = e => {
  fullScaleMin = parseFloat(e.target.value);
  el('fullScaleMinLabel').textContent = fullScaleMin + ' dBµV';
  onSegScaleInput();
};
el('fullScaleMax').oninput = e => {
  fullScaleMax = parseFloat(e.target.value);
  el('fullScaleMaxLabel').textContent = fullScaleMax + ' dBµV';
  onSegScaleInput();
};
el('autoScaleSegBtn').onclick = () => {
  if (!currentFullData) return;
  autoScaleSegWaterfall(currentFullData);
  buildSegCache(currentFullData);
  drawFullView(currentFullData);
};

// Frequenzraster ein/aus: nur neu blitten (Cache bleibt), kein Neuaufbau.
el('gridToggle').onchange = e => {
  gridEnabled = e.target.checked;
  if (currentFullData) drawFullView(currentFullData);
};

el('backToSessionBtn').onclick = () => {
  if (segmentReturnView === 'list') { showView('list'); return; }
  if (currentSessionPath) openSession(currentSessionPath);
  else showView('list');
};

// Fenstergroesse geaendert -> in der Segment-Ansicht Viewport-Hoehe und
// Canvas-Breite anpassen und den Wasserfall neu aufbauen (verzoegert, da der
// Cache-Neubau teuer ist).
let segResizeDebounce = null;
window.addEventListener('resize', () => {
  if (!currentFullData || el('view-segment').style.display !== 'block') return;
  if (segResizeDebounce) clearTimeout(segResizeDebounce);
  segResizeDebounce = setTimeout(() => {
    fitSegWaterfallHeight();
    sizeSegWaterfall(currentFullData);
    buildSegCache(currentFullData);
    drawFullView(currentFullData);
  }, 200);
});

showView('list');
loadSessionList();
// Deep-Link aus waterfall.py (T-802); der Pfad laeuft serverseitig ohnehin
// durch resolve_safe_path, clientseitig keine zusaetzliche Pruefung noetig.
const deepPath = new URLSearchParams(location.search).get('path');
if (deepPath) openSession(deepPath);
</script>
</body>
</html>
"""

# T-1403: Theme-Bausteine einsetzen (Tokens + Grundregeln, Head-Skript fuer das
# Theme-Cookie, Kopfleiste statt <h1>) - Muster wie :8040 (T-1402).
HTML_PAGE = (HTML_PAGE.replace("/*THEME_CSS*/", THEME_CSS + BASE_CSS)
                      .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                      .replace("/*HEADER*/", header_html("Session analysis")))


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

            elif parsed.path == "/api/sessions":
                self._send_json(list_session_files())
            elif parsed.path == "/api/session":
                self._handle_api_session(parse_qs(parsed.query))
            elif parsed.path == "/api/segment":
                self._handle_api_segment(parse_qs(parsed.query))
            elif parsed.path == "/api/befund":
                self._handle_api_befund(parse_qs(parsed.query))
            elif parsed.path == "/api/note":
                self._handle_api_note(parse_qs(parsed.query))
            else:
                self._send_json({"error": "unknown endpoint"}, status=404)
        except Exception:
            # Sicherheitsnetz - jeder erwartete Fehlerfall wird bereits
            # gezielt oben abgefangen; das hier greift nur bei wirklich
            # unerwarteten Problemen und verhindert einen Traceback beim
            # Client (siehe Teil D).
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
        # Session komplett loeschen (DB + Beidateien + Sidecars), Label/
        # Notiz leeren. Pfad kommt aus dem JSON-Body und MUSS wie jeder
        # Zugriff durch die Sandbox; Schutzregel siehe delete_session().
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
            result = delete_session(resolved)
        except SessionLockedError as exc:
            self._send_json(_error_payload(exc), status=409)
            return
        self._send_json(result)

    def _handle_set_label(self) -> None:
        # Kurztext setzen. Der Pfad kommt aus dem JSON-Body und MUSS - wie jeder
        # Lesezugriff - durch die Sandbox (LAN-Bindung). Der Speicher liegt
        # getrennt von der rein lesenden Session-DB (siehe LabelStore).
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

    def _handle_api_session(self, params: dict) -> None:
        raw_path = params.get("path", [""])[0]
        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        try:
            conn = open_session_db(resolved)
        except FileNotFoundError:
            self._send_json({"error": ERR_NOT_FOUND}, status=404)
            return
        except InvalidSessionDBError:
            self._send_json({"error": ERR_INVALID_DB}, status=422)
            return

        try:
            session_row = conn.execute(
                "SELECT started_at, source, mode, note FROM session LIMIT 1"
            ).fetchone()
            extra = segment_extra_columns(conn)
            # cols entsteht ausschliesslich aus festen Bezeichnerlisten (siehe
            # SEGMENT_OPTIONAL_COLUMNS) - keine Nutzereingabe, kein Injektionspfad.
            cols = ", ".join(["id", "start_hz", "stop_hz", "step_hz", "n_bins",
                              "t_first", "t_last", "row_count"] + extra)
            segment_rows = conn.execute(
                f"SELECT {cols} FROM segment ORDER BY id ASC"
            ).fetchall()
        finally:
            conn.close()

        self._send_json({
            "session": dict(session_row) if session_row else {},
            "segments": [segment_row_to_dict(r, extra) for r in segment_rows],
        })

    def _handle_api_befund(self, params: dict) -> None:
        """GET /api/befund?path=... - Befund neben der Session-DB sicherstellen
        (Lazy-Fill: erzeugen, falls noch nicht vorhanden) und ausliefern. Der
        Befund deckt alle Segmente ab; das Frontend waehlt sein Segment selbst."""
        raw_path = params.get("path", [""])[0]
        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return
        try:
            befund = ensure_befund(resolved)
        except FileNotFoundError:
            self._send_json({"error": ERR_NOT_FOUND}, status=404)
            return
        except InvalidSessionDBError:
            self._send_json({"error": ERR_INVALID_DB}, status=422)
            return
        with open(befund, encoding="utf-8") as fh:
            self._send_json(json.load(fh))

    def _handle_api_note(self, params: dict) -> None:
        """GET /api/note?path=... - gespeicherte Notiz zu einer Session-DB
        liefern (leerer String, falls keine vorhanden)."""
        raw_path = params.get("path", [""])[0]
        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
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

    def _handle_api_segment(self, params: dict) -> None:
        raw_path = params.get("path", [""])[0]
        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        try:
            segment_id = int(params.get("id", [""])[0])
        except (ValueError, TypeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        mode = params.get("mode", ["thumb"])[0]
        if mode not in ("thumb", "full"):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        # T-806: Thumbnails kommen - wenn vorhanden und gueltig - aus dem
        # Sidecar neben der DB. Damit entfaellt beim Aufbau der Session-Liste
        # das Lesen SAEMTLICHER Sweeps jeder Session; genau das war der
        # Engpass, der mit der Zahl der Aufzeichnungen waechst. Der Pfad ist
        # zu diesem Zeitpunkt bereits durch resolve_safe_path() gelaufen.
        # mode=full wird nicht gecacht: eine Vollansicht wird je Session
        # hoechstens einmal geoeffnet, und ihr Payload ist um Groessenordnungen
        # groesser.
        if mode == "thumb":
            cached = load_thumb_cache(resolved, segment_id)
            if cached is not None:
                self._send_json(cached)
                return

        try:
            conn = open_session_db(resolved)
        except FileNotFoundError:
            self._send_json({"error": ERR_NOT_FOUND}, status=404)
            return
        except InvalidSessionDBError:
            self._send_json({"error": ERR_INVALID_DB}, status=422)
            return

        try:
            extra = segment_extra_columns(conn)
            # siehe C2: cols aus festen Bezeichnerlisten, keine Nutzereingabe.
            cols = ", ".join(["start_hz", "stop_hz", "step_hz", "n_bins", "row_count",
                              "t_first", "t_last"] + extra)
            segment = conn.execute(
                f"SELECT {cols} FROM segment WHERE id = ?",
                (segment_id,),
            ).fetchone()
            if segment is None:
                self._send_json({"error": ERR_SEGMENT_NOT_FOUND}, status=404)
                return
            rows = load_segment_rows(conn, segment_id)
        finally:
            conn.close()

        target_rows = THUMB_TARGET_ROWS if mode == "thumb" else FULL_ROW_THRESHOLD
        n_rows_raw = len(rows)
        out_rows, decimated, decimation = decimate_max_pool(rows, target_rows)

        # T-806: fuer Thumbnails zusaetzlich die Bin-Achse zusammenfassen. Die
        # Vollansicht bleibt unangetastet - dort haengen Frequenzachse,
        # Katalogstreifen und die Pegelanzeige an der Maus an der echten
        # Bin-Zahl.
        n_bins_raw = segment["n_bins"]
        bin_decimation = 1
        n_bins_thumb = n_bins_raw
        if mode == "thumb":
            out_rows, n_bins_thumb, bin_decimation = decimate_bins_max(
                out_rows, n_bins_raw, THUMB_TARGET_BINS)

        combined = array('h')
        t_unix_list = []
        for t_unix, levels in out_rows:
            combined.extend(levels)
            t_unix_list.append(t_unix)

        # Segment-Stammdaten (inkl. der optionalen T-715-Felder) unveraendert
        # durchreichen, Messdaten daruebersetzen.
        payload = segment_row_to_dict(segment, extra)
        payload.update({
            "n_rows": len(out_rows),
            "t_unix": t_unix_list,
            "levels_b64": base64.b64encode(combined.tobytes()).decode("ascii"),
            "decimated": decimated,
            # Rohzeilenzahl und Faktor fuer den Hinweis im Kopf der
            # Session-Analyse (renderFullSegment). n_rows_raw ist die
            # tatsaechlich geladene Sweepzahl - NICHT segment.row_count,
            # das die laufende Aufzeichnung fortschreibt und abweichen kann.
            "n_rows_raw": n_rows_raw,
            "decimation": decimation,
        })
        # T-806: bei bin-dezimierten Thumbnails MUSS n_bins die Zahl der
        # tatsaechlich gelieferten Spalten nennen - drawMatrix() rechnet damit
        # den Zeilenversatz in der Pegelmatrix aus. Die Bin-Zahl der
        # Aufzeichnung steht daneben; die Segment-Karten zeigen sie ohnehin aus
        # /api/session und nicht von hier.
        payload["n_bins_raw"] = n_bins_raw
        payload["bin_decimation"] = bin_decimation
        if mode == "thumb":
            payload["n_bins"] = n_bins_thumb
            store_thumb_cache(resolved, segment_id, payload)
        self._send_json(payload)

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
    print(f"[analyze-session] Server laeuft auf http://{HTTP_HOST}:{HTTP_PORT}, Wurzel: {SESSION_ROOT}")
    register_server("analyze_sessions", HTTP_PORT, {"root": str(SESSION_ROOT)})
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
