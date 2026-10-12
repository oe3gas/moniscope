#!/usr/bin/env python3
"""
eb200_core/recording_index.py

Lese-Aggregator "Aufzeichnungen" (T-1102, Meilenstein 11): liefert EINE Liste
aller Aufzeichnungen von MoniScope mit einheitlichem Zeilenschema, egal ob
sie aus einem Long-Run-Job (eb200_core/job_store.py: dscan/ifpan/mscan),
einer Direkt-Session (sessions/*.db, eb200_core/session_store.py-Schema)
oder einer Direkt-Spektrumaufnahme (records/*.specbin + Sidecar,
eb200_core/spectrum_store.py bzw. dscan_store.py) stammen.

Rein LESEND - keine Mutation, kein Loeschen (das ist T-1104), keine
HTTP-Anbindung (das ist T-1103). Kein Import aus analyze_sessions.py,
analyze_spectrum.py oder waterfall.py (Repo-Root-Skripte importieren
eb200_core, nie umgekehrt) - die paar dafuer noetigen Kleinhelfer
(to_relative, Session-DB oeffnen, Live-Heuristik) sind hier nachgebildet,
mit Kommentar auf das jeweilige Vorbild.

Datenherkunft (siehe list_recordings()):
  - direkt/dscan:  sessions/*.db (nicht rekursiv)
  - direkt/ifpan:  records/*.specbin auf oberster Ebene (nicht rekursiv;
                   records/longrun_*/ gehoert zu Jobs und wird NICHT
                   gelistet). DscanMaxholdWriter (dscan_store.py) schreibt
                   ausschliesslich in records/longrun_*/ - eine
                   Direkt-Spektrumaufnahme mit format=eb200-dscan-maxhold-v1
                   kann strukturell nicht vorkommen; taucht sie trotzdem
                   auf, wird sie fail-closed als ungueltig behandelt.
  - direkt/audio:  records/*.wav (+ .wav.json, + .wav.klasse.json - regenerierbar,
                   siehe audio_classify.py) auf oberster Ebene.
  - longrun/*:     eb200_core.job_store.JobStore (Index + Manifest je Job).
                   Auswertung: dscan UND ifpan verlinken auf Server "spectrum"
                   (:8040, ?job=) - Long-Run-DSCAN schreibt wie ifpan Maxhold-
                   Flatfiles (dscan_store.py, spectrum_reader-Format), die
                   NUR analyze_spectrum.py lesen kann; :8020 kennt nur
                   SQLite-Session-DBs (siehe _job_row(), T-1105).

Robustheit (WICHTIG): eine defekte Datei, ein unlesbares Sidecar oder ein
unerreichbares Job-Manifest darf list_recordings() NIE zum Absturz bringen -
jede Quelle ist einzeln try/except-geschuetzt, eine defekte Einzeldatei
erscheint nur als Zeile mit valid=False statt die Antwort zu sprengen
(Muster: analyze_sessions.list_session_files()/analyze_spectrum.
_list_specbin_dir()). sqlite wird ausschliesslich per "file:...?mode=ro"
(uri=True) geoeffnet, die Verbindung wird immer geschlossen. Kein os.walk -
beide Direkt-Ordner werden genau eine Ebene tief gelesen.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

from eb200_core import paths
from eb200_core.job_store import ST_GEPLANT, ST_LAEUFT, load_storage_roots
from eb200_core.label_store import LabelStore, clean_note
from eb200_core.path_sandbox import (
    storage_root_abs as _resolve_storage_root, to_portable as _to_relative,
    sandbox_roots, resolve_within,
)
from eb200_core.sigmf_export import SigmfRunningError, write_sigmf

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Sidecar-Formatkennungen (siehe eb200_core/spectrum_reader.py FORMAT_ID/
# FORMAT_ID_DSCAN) - hier als reine String-Konstanten nachgebildet, um den
# Import von spectrum_reader.py (und darueber evtl. numpy) zu vermeiden.
_SPECTRUM_FORMAT_IFPAN = "eb200-ifpan-bins-v1"
_SPECTRUM_FORMAT_DSCAN = "eb200-dscan-maxhold-v1"

_REQUIRED_SESSION_TABLES = {"session", "segment", "sweep"}

# Zustaende, in denen ein Loeschen (T-1104) gesperrt bleibt - fuer Jobs
# "geplant"/"laeuft" (siehe job_store.py), fuer Session/Spektrum kommt nur
# "laeuft" vor (siehe _duration_s/Live-Heuristik unten).
_DELETE_BLOCKED_STATES = (ST_LAEUFT, ST_GEPLANT)

# Vollstaendiges Zeilenschema (siehe Moduldoku/Aufgabe T-1102) - fuer den
# Selbsttest (Schema-Vollstaendigkeit) UND als Referenz beim Lesen.
# "ablage" (Nachtrag T-201): portabler Pfad der Storage-Root ueber
# eb200_core.path_sandbox.to_portable (Alias _to_relative) - "records" fuer
# den Repo-relativen Standard-Root, sonst der absolute Pfad mit "/"
# (z. B. "D:/eb200-test") fuer einen externen storage_roots-Eintrag. Gilt
# fuer alle Herkuenfte AUSSER Direkt-Session (dort weiterhin session_dir
# bzw. None bei einer defekten Datei - sessions/ ist kein storage_root).
ROW_KEYS = frozenset({
    "rec_id", "art", "herkunft", "label", "start_iso", "end_iso", "dauer_s",
    "zustand", "abort_reason", "abort_code", "abort_detail", "inhalt", "dateien", "bytes", "ablage",
    "job_id", "manifest_unreachable", "valid", "auswertung", "loesch",
})


class RecordingError(Exception):
    """Basis fuer die Loesch-/Aenderungsfehler dieses Moduls (T-1802c).

    Traegt einen sprachneutralen Code und benannte Parameter statt eines
    deutschen Satzes; der Anzeigetext entsteht im Client (Regel 2 und 3,
    Entscheidungslog 2026-08-31). str(exc) liefert den Code, damit ein
    Handler, der noch str(exc) durchreicht, etwas Brauchbares sieht.
    """

    def __init__(self, code: str, **params) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


class RecordingLockedError(RecordingError):
    """Aufzeichnung laeuft moeglicherweise noch - nichts geloescht."""


# Fehlercodes dieses Moduls (T-1802c). Anzeigetext im Client, siehe
# EB200_LABELS in eb200_core/ui_theme.py.
ERR_OUTSIDE_STORAGE = "outside_storage"
ERR_WRONG_EXTENSION = "wrong_extension"
ERR_LONGRUN_PART = "longrun_part"
ERR_MAY_BE_RUNNING = "may_be_running"
ERR_INCOMPLETE = "incomplete"
ERR_NO_SIDECAR = "no_sidecar"
ERR_SIDECAR_UNREADABLE = "sidecar_unreadable"
ERR_WRONG_ORPHAN_DIR = "wrong_orphan_dir"
ERR_NO_STORAGE_ROOT = "no_storage_root"
ERR_KNOWN_JOB_DIR = "known_job_dir"
ERR_RECENTLY_WRITTEN = "recently_written"
ERR_UNKNOWN_KIND = "unknown_kind"
ERR_SIGMF_IQ_ONLY = "sigmf_iq_only"
ERR_NOT_FOUND = "not_found"
ERR_ORPHAN_NO_LABEL = "orphan_no_label"
ERR_SIGMF_EXPORT = "sigmf_export_failed"
ERR_NO_INDEX_ENTRY = "no_index_entry"
ERR_UNEXPECTED_FORMAT = "unexpected_format"
ERR_SESSION_DB_UNREADABLE = "session_db_unreadable"


class RecordingNotDeletableError(RecordingError, ValueError):
    """Pfad/Regel verletzt (Sandbox, falsche Endung, Long-Run-Teil, Zustand) - nichts geloescht."""


# ---------------------------------------------------------------------------
# Kleinhelfer (T-201: _to_relative/_resolve_storage_root sind seit hier
# Import-Aliase auf eb200_core.path_sandbox - siehe Import oben. Vorher
# eigene Nachbildungen von analyze_sessions.to_relative()/analyze_spectrum.
# resolve_safe_path(); _to_relative lieferte fuer Pfade AUSSERHALB von root
# ein "../"-Relativpfad (unter Windows bei anderem Laufwerk sogar
# ValueError) - _to_relative alias to_portable() liefert dort stattdessen
# den absoluten Pfad, siehe path_sandbox.to_portable-Docstring.)
# ---------------------------------------------------------------------------

def _parse_iso(value):
    """Parst einen ISO-String (mit Offset) zu einem tz-aware datetime;
    None/leer/unparsbar -> None statt Ausnahme (Muster job_store._parse_iso,
    hier defensiv statt strikt, da die Eingabe aus Fremddateien stammt)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _iso_from_unix(ts):
    """Unix-Timestamp (Session t_first/t_last) -> ISO-String lokal MIT
    Offset (Muster job_store._now_iso(), nur fuer einen gegebenen Zeitpunkt
    statt "jetzt")."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _duration_s(start_dt, end_dt, running: bool):
    """end-start in Sekunden; laeuft die Aufzeichnung noch, zaehlt bis
    jetzt. None, wenn kein Start bekannt ist oder (nicht laufend) kein Ende."""
    if start_dt is None:
        return None
    if running:
        end_dt = datetime.now().astimezone()
    elif end_dt is None:
        return None
    return (end_dt - start_dt).total_seconds()


def _open_session_db_ro(path: str) -> sqlite3.Connection:
    """Oeffnet eine Session-DB read-only und prueft das Schema - Nachbildung
    von analyze_sessions.open_session_db() (siehe Moduldoku): file:...
    ?mode=ro, Pruefung auf session/segment/sweep. Wirft (wie dort) bei
    fehlendem Schema bzw. kaputter Datei; der Aufrufer faengt das ab."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not _REQUIRED_SESSION_TABLES <= tables:
            raise ValueError(f"session DB schema incomplete: {path}")
    except Exception:
        conn.close()
        raise
    return conn


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _delete_allowed(zustand: str) -> bool:
    """Gemeinsame Regel fuer alle drei Herkuenfte (siehe Aufgabe T-1102):
    gesperrt NUR bei laeuft/geplant - eine defekte Datei bleibt loeschbar
    (das ist T-1104, hier wird nur das Recht in die Zeile geschrieben)."""
    return zustand not in _DELETE_BLOCKED_STATES


def _all_storage_roots(repo_root: str, record_dir: str) -> list:
    """
    Alle Ablage-Wurzeln fuer Direkt-Aufnahmen (Nachtrag T-201): die
    konfigurierten storage_roots.json-Eintraege, dann zusaetzlich record_dir
    (falls storage_roots.json es nicht ohnehin schon enthaelt) - Dedup ueber
    den normalisierten Pfad, da beide auf denselben Ordner zeigen koennen.
    Absolut, Reihenfolge stabil (konfigurierte Roots zuerst, dann
    record_dir). Gemeinsame Grundlage fuer _spectrum_rows()/_audio_rows()/
    _orphan_rows() - vorher durchsuchte nur _orphan_rows() alle Roots,
    Direkt-Spektrum/-Audio sahen ausschliesslich record_dir.
    """
    try:
        configured = load_storage_roots(os.path.join(repo_root, "storage_roots.json"))
    except Exception:
        configured = ["records"]
    roots = {}
    for r in list(configured) + [record_dir]:
        abs_r = _resolve_storage_root(repo_root, r)
        roots[os.path.normcase(os.path.normpath(abs_r))] = abs_r
    return list(roots.values())


# ---------------------------------------------------------------------------
# Direkt-Sessions (sessions/*.db, session_store-Schema)
# ---------------------------------------------------------------------------

def _invalid_session_row(rel: str, name: str, full_path: str,
                         grund_code: str | None = None, grund_detail: str | None = None) -> dict:
    return {
        "rec_id": f"session:{rel}", "art": "dscan", "herkunft": "direkt",
        "label": name, "start_iso": None, "end_iso": None, "dauer_s": None,
        "zustand": "unbekannt", "abort_reason": None,
        "abort_code": grund_code, "abort_detail": grund_detail, "inhalt": {},
        "dateien": 1, "bytes": _file_size(full_path), "ablage": None,
        "job_id": None, "manifest_unreachable": False, "valid": False,
        "auswertung": {"server": "sessions", "query": None},
        "loesch": {"kind": "session", "ref": rel, "erlaubt": _delete_allowed("unbekannt")},
    }


def _remove_if_exists(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _session_row(full_path: str, repo_root: str, session_dir: str, labels: LabelStore) -> dict:
    rel = _to_relative(full_path, repo_root)
    name = os.path.basename(full_path)

    # "live"-Heuristik ZUERST, VOR dem eigenen Read-Only-Open: eine WAL-DB
    # read-only zu oeffnen legt -wal/-shm neu an, falls sie fehlen (SQLite
    # braucht sie fuer eine konsistente Lesesicht) - danach geprueft, wuerde
    # der eigene Lesezugriff die Heuristik verfaelschen und JEDE laengst
    # abgeschlossene Session faelschlich als "laeuft" zeigen.
    wal_path = full_path + "-wal"
    shm_path = full_path + "-shm"
    wal_existed_before = os.path.isfile(wal_path)
    live = False
    if wal_existed_before:
        try:
            live = (time.time() - os.path.getmtime(wal_path)) < 10.0
        except OSError:
            live = False

    try:
        return _session_row_inner(full_path, rel, name, session_dir, labels, live)
    finally:
        # Von uns selbst neu angelegte -wal/-shm wieder entfernen (siehe
        # oben) - sonst wuerde bereits der NAECHSTE Aufruf (z. B. 5s-Poll
        # wie in manage_jobs.py) diese frische Datei sehen und die Session
        # dauerhaft faelschlich als "laeuft" zeigen. Nur entfernen, was wir
        # selbst angelegt haben: existierte -wal vorher schon (echter,
        # gerade laufender Schreiber), bleibt es unangetastet.
        if not wal_existed_before:
            _remove_if_exists(wal_path)
            _remove_if_exists(shm_path)


def _session_row_inner(full_path: str, rel: str, name: str, session_dir: str,
                        labels: LabelStore, live: bool) -> dict:
    try:
        conn = _open_session_db_ro(full_path)
    except Exception as exc:
        return _invalid_session_row(rel, name, full_path,
                                    ERR_SESSION_DB_UNREADABLE, f"{type(exc).__name__}: {exc}")

    try:
        try:
            agg = conn.execute(
                "SELECT MIN(start_hz), MAX(stop_hz), COUNT(*), SUM(row_count), "
                "MIN(t_first), MAX(t_last) FROM segment"
            ).fetchone()
            # Sessions vor Einfuehrung von segment.speed haben die Spalte
            # nicht (Altbestand) - bleibt gueltig, speed dann None statt
            # eines sqlite3.OperationalError (no such column: speed).
            seg_cols = {row[1] for row in conn.execute("PRAGMA table_info(segment)")}
            speed_sql = "speed" if "speed" in seg_cols else "NULL AS speed"
            first_seg = conn.execute(
                f"SELECT step_hz, {speed_sql} FROM segment ORDER BY id ASC LIMIT 1"
            ).fetchone()
        except sqlite3.Error as exc:
            return _invalid_session_row(rel, name, full_path,
                                        ERR_SESSION_DB_UNREADABLE, f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()

    start_hz, stop_hz, n_segments, sweeps, t_first, t_last = agg
    step_hz, speed = first_seg if first_seg else (None, None)

    start_iso = _iso_from_unix(t_first)
    end_iso = _iso_from_unix(t_last)
    zustand = "laeuft" if live else "beendet"
    dauer = _duration_s(_parse_iso(start_iso), _parse_iso(end_iso), live)

    return {
        "rec_id": f"session:{rel}", "art": "dscan", "herkunft": "direkt",
        "label": labels.get(rel), "start_iso": start_iso, "end_iso": end_iso,
        "dauer_s": dauer, "zustand": zustand, "abort_reason": None,
        "abort_code": None, "abort_detail": None,
        "inhalt": {
            "start_hz": start_hz, "stop_hz": stop_hz, "step_hz": step_hz,
            "speed": speed, "sweeps": sweeps or 0, "n_segments": n_segments or 0,
        },
        "dateien": 1, "bytes": _file_size(full_path), "ablage": session_dir,
        "job_id": None, "manifest_unreachable": False, "valid": True,
        "auswertung": {"server": "sessions", "query": f"path={rel}"},
        "loesch": {"kind": "session", "ref": rel, "erlaubt": _delete_allowed(zustand)},
    }


def _session_rows(repo_root: str, session_dir: str, labels: LabelStore) -> list:
    rows = []
    abs_dir = os.path.join(repo_root, session_dir)
    if not os.path.isdir(abs_dir):
        return rows
    try:
        entries = sorted(os.scandir(abs_dir), key=lambda e: e.name)
    except OSError:
        return rows
    for entry in entries:
        try:
            is_file = entry.is_file()
        except OSError:
            continue
        if not is_file or not entry.name.endswith(".db"):
            continue
        rows.append(_session_row(entry.path, repo_root, session_dir, labels))
    return rows


# ---------------------------------------------------------------------------
# Direkt-Spektren (records/*.specbin, oberste Ebene - siehe Moduldoku)
# ---------------------------------------------------------------------------

def _invalid_spectrum_row(rel: str, name: str, full_path: str, root_abs: str, repo_root: str,
                          grund_code: str | None = None, grund_detail: str | None = None) -> dict:
    # Fail-closed wie analyze_spectrum.delete_recording(): fehlendes/
    # unlesbares Sidecar zaehlt als "laeuft noch", nicht als loeschbar.
    return {
        "rec_id": f"spectrum:{rel}", "art": "ifpan", "herkunft": "direkt",
        "label": name, "start_iso": None, "end_iso": None, "dauer_s": None,
        "zustand": "laeuft", "abort_reason": None,
        "abort_code": grund_code, "abort_detail": grund_detail, "inhalt": {},
        "dateien": 1, "bytes": _file_size(full_path), "ablage": _to_relative(root_abs, repo_root),
        "job_id": None, "manifest_unreachable": False, "valid": False,
        "auswertung": {"server": "spectrum", "query": None},
        "loesch": {"kind": "spectrum", "ref": rel, "erlaubt": _delete_allowed("laeuft")},
    }


def _spectrum_row(full_path: str, repo_root: str, root_abs: str, labels: LabelStore) -> dict:
    rel = _to_relative(full_path, repo_root)
    name = os.path.basename(full_path)
    sidecar_path = full_path + ".json"

    meta = None
    grund_code, grund_detail = ERR_NO_SIDECAR, None
    if os.path.isfile(sidecar_path):
        try:
            with open(sidecar_path, encoding="utf-8") as f:
                meta = json.load(f)
            grund_code, grund_detail = ERR_SIDECAR_UNREADABLE, "not a JSON object"
        except (OSError, ValueError) as exc:
            meta = None
            grund_code, grund_detail = ERR_SIDECAR_UNREADABLE, f"{type(exc).__name__}: {exc}"
    if not isinstance(meta, dict):
        return _invalid_spectrum_row(rel, name, full_path, root_abs, repo_root, grund_code, grund_detail)

    fmt = meta.get("format")
    if fmt not in (_SPECTRUM_FORMAT_IFPAN, _SPECTRUM_FORMAT_DSCAN):
        return _invalid_spectrum_row(rel, name, full_path, root_abs, repo_root,
                                     ERR_UNEXPECTED_FORMAT, f"format={fmt!r}")

    # Strukturell erwartet ist hier IMMER ifpan (siehe Moduldoku:
    # DscanMaxholdWriter schreibt nur unterhalb records/longrun_*/) - der
    # dscan-Zweig ist reine Verteidigung gegen eine Datei, die nicht
    # hierher gehoert.
    art = "ifpan" if fmt == _SPECTRUM_FORMAT_IFPAN else "dscan"
    running = bool(meta.get("running", False))
    start_iso = meta.get("start_iso")
    end_iso = meta.get("stop_iso")
    zustand = "laeuft" if running else "beendet"
    dauer = _duration_s(_parse_iso(start_iso), _parse_iso(end_iso), running)

    if art == "ifpan":
        inhalt = {
            "center_hz": meta.get("center_hz"), "span_hz": meta.get("span_hz"),
            "bw_hz": meta.get("bw_hz"), "demod": meta.get("demod"),
            "frames": meta.get("frames_written"),
        }
    else:
        inhalt = {
            "start_hz": meta.get("start_hz"), "stop_hz": meta.get("stop_hz"),
            "step_hz": meta.get("step_hz"), "speed": None,
            "sweeps": meta.get("sweeps_seen"), "n_segments": 1,
        }

    return {
        "rec_id": f"spectrum:{rel}", "art": art, "herkunft": "direkt",
        "label": labels.get(rel), "start_iso": start_iso, "end_iso": end_iso,
        "dauer_s": dauer, "zustand": zustand, "abort_reason": None,
        "abort_code": None, "abort_detail": None,
        "inhalt": inhalt, "dateien": 1, "bytes": _file_size(full_path),
        "ablage": _to_relative(root_abs, repo_root), "job_id": None, "manifest_unreachable": False,
        "valid": True, "auswertung": {"server": "spectrum", "query": f"path={rel}"},
        "loesch": {"kind": "spectrum", "ref": rel, "erlaubt": _delete_allowed(zustand)},
    }


def _spectrum_rows(repo_root: str, record_dir: str, labels: LabelStore) -> list:
    # Nachtrag T-201: alle storage_roots durchsuchen, nicht nur record_dir
    # (Signatur bleibt unveraendert - record_dir geht in _all_storage_roots ein).
    rows = []
    for root_abs in _all_storage_roots(repo_root, record_dir):
        if not os.path.isdir(root_abs):
            continue
        try:
            entries = sorted(os.scandir(root_abs), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                is_file = entry.is_file()
            except OSError:
                continue
            # nicht rekursiv: records/longrun_*/ sind Verzeichnisse und enden
            # nie auf .specbin - sie werden hier automatisch nicht erfasst.
            if not is_file or not entry.name.endswith(".specbin"):
                continue
            rows.append(_spectrum_row(entry.path, repo_root, root_abs, labels))
    return rows


# ---------------------------------------------------------------------------
# Direkt-Audio (records/*.wav, oberste Ebene - siehe Moduldoku)
# ---------------------------------------------------------------------------

def _invalid_audio_row(rel: str, name: str, full_path: str, root_abs: str, repo_root: str,
                       grund_code: str | None = None, grund_detail: str | None = None) -> dict:
    # Fail-closed wie beim Spektrum-Zweig: fehlendes/unlesbares Sidecar
    # zaehlt als "laeuft noch", nicht als loeschbar - das Sidecar entsteht
    # erst in WavAudioWriter.close() (siehe eb200_core/audio_wav.py).
    return {
        "rec_id": f"audio:{rel}", "art": "audio", "herkunft": "direkt",
        "label": name, "start_iso": None, "end_iso": None, "dauer_s": None,
        "zustand": "laeuft", "abort_reason": None,
        "abort_code": grund_code, "abort_detail": grund_detail, "inhalt": {},
        "dateien": 1, "bytes": _file_size(full_path), "ablage": _to_relative(root_abs, repo_root),
        "job_id": None, "manifest_unreachable": False, "valid": False,
        "auswertung": {"server": "audio", "query": None},
        "loesch": {"kind": "audio", "ref": rel, "erlaubt": _delete_allowed("laeuft")},
    }


def _audio_row(full_path: str, repo_root: str, root_abs: str, labels: LabelStore) -> dict:
    rel = _to_relative(full_path, repo_root)
    name = os.path.basename(full_path)
    sidecar_path = full_path + ".json"

    meta = None
    grund_code, grund_detail = ERR_NO_SIDECAR, None
    if os.path.isfile(sidecar_path):
        try:
            with open(sidecar_path, encoding="utf-8") as f:
                meta = json.load(f)
            grund_code, grund_detail = ERR_SIDECAR_UNREADABLE, "not a JSON object"
        except (OSError, ValueError) as exc:
            meta = None
            grund_code, grund_detail = ERR_SIDECAR_UNREADABLE, f"{type(exc).__name__}: {exc}"
    if not isinstance(meta, dict):
        return _invalid_audio_row(rel, name, full_path, root_abs, repo_root, grund_code, grund_detail)

    # T-1109: WavAudioWriter schreibt das Sidecar ab Aufnahmestart mit
    # running=True (Muster SpectrumBinWriter) und aktualisiert es final in
    # close() mit running=False - eine laufende Aufnahme hat damit echte
    # Metadaten (freq_hz/demod/... schon bekannt), keinen Absturzrest-
    # Fallback noetig. Bestandsdateien ohne das Feld -> running=False.
    running = bool(meta.get("running", False))
    start_iso = meta.get("start_iso")
    if running:
        end_iso = None
        zustand = "laeuft"
        dauer = _duration_s(_parse_iso(start_iso), None, True)
    else:
        end_iso = meta.get("stop_iso")
        zustand = "beendet"
        dauer = meta.get("duration_s")
        if dauer is None:
            dauer = _duration_s(_parse_iso(start_iso), _parse_iso(end_iso), False)

    stem = full_path[:-len(".wav")] if full_path.endswith(".wav") else full_path
    specbin_path = stem + ".specbin"
    specbin_rel = _to_relative(specbin_path, repo_root) if os.path.isfile(specbin_path) else None

    inhalt = {
        "freq_hz": meta.get("freq_hz"), "demod": meta.get("mode"),
        "bw_hz": meta.get("bw_hz"), "sample_rate_hz": meta.get("sample_rate_hz"),
        "is_iq": bool(meta.get("is_iq", False)), "specbin_rel": specbin_rel,
    }

    return {
        "rec_id": f"audio:{rel}", "art": "audio", "herkunft": "direkt",
        "label": labels.get(rel), "start_iso": start_iso, "end_iso": end_iso,
        "dauer_s": dauer, "zustand": zustand, "abort_reason": None,
        "abort_code": None, "abort_detail": None,
        "inhalt": inhalt, "dateien": 1, "bytes": _file_size(full_path),
        "ablage": _to_relative(root_abs, repo_root), "job_id": None, "manifest_unreachable": False,
        "valid": True, "auswertung": {"server": "audio", "query": f"path={rel}"},
        "loesch": {"kind": "audio", "ref": rel, "erlaubt": _delete_allowed(zustand)},
    }


def _audio_rows(repo_root: str, record_dir: str, labels: LabelStore) -> list:
    # Nachtrag T-201: alle storage_roots durchsuchen, nicht nur record_dir
    # (Signatur bleibt unveraendert - record_dir geht in _all_storage_roots ein).
    rows = []
    for root_abs in _all_storage_roots(repo_root, record_dir):
        if not os.path.isdir(root_abs):
            continue
        try:
            entries = sorted(os.scandir(root_abs), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                is_file = entry.is_file()
            except OSError:
                continue
            # nicht rekursiv: records/longrun_*/ sind Verzeichnisse und enden
            # nie auf .wav - sie werden hier automatisch nicht erfasst.
            if not is_file or not entry.name.endswith(".wav"):
                continue
            rows.append(_audio_row(entry.path, repo_root, root_abs, labels))
    return rows


# ---------------------------------------------------------------------------
# Long-Run-Jobs (eb200_core/job_store.py)
# ---------------------------------------------------------------------------

def _job_row(manifest: dict, mscan_store, repo_root: str) -> dict:
    job_id = manifest.get("job_id")
    job_type = manifest.get("type")
    state = manifest.get("state")
    params = manifest.get("params") or {}
    stats = manifest.get("stats") or {}
    files = manifest.get("files") or []

    ended_iso = manifest.get("ended_iso")
    end_iso = ended_iso if ended_iso is not None else manifest.get("end_iso")
    running = state == ST_LAEUFT
    dauer = _duration_s(_parse_iso(manifest.get("start_iso")), _parse_iso(end_iso), running)

    inhalt = {}
    auswertung = {"server": None, "query": None}

    if job_type == "dscan":
        geometry = stats.get("geometry") or {}
        inhalt = {
            "start_hz": params.get("start_hz"), "stop_hz": params.get("stop_hz"),
            "step_hz": geometry.get("step_hz"), "speed": params.get("speed"),
            "sweeps": stats.get("sweeps"), "n_segments": 1,
        }
        # Long-Run-DSCAN schreibt Maxhold-Flatfiles (dscan_store.py, Format
        # eb200-dscan-maxhold-v1), die NUR spectrum_reader/analyze_spectrum (:8040)
        # liest - :8020 kennt ausschliesslich SQLite-Session-DBs. Deshalb wie bei
        # ifpan ueber ?job=, :8040 listet die Teildateien des Jobs (T-1105).
        auswertung = {"server": "spectrum", "query": f"job={job_id}"}

    elif job_type == "ifpan":
        inhalt = {
            "center_hz": params.get("center_hz"), "span_hz": params.get("span_hz"),
            "bw_hz": params.get("bw_hz"), "demod": params.get("demod"),
            "frames": stats.get("frames"),
        }
        auswertung = {"server": "spectrum", "query": f"job={job_id}"}

    elif job_type == "mscan":
        channels = params.get("channels") or []
        mscan_job_id = params.get("mscan_job_id")
        mscan_title = None
        if mscan_store is not None and mscan_job_id:
            try:
                mscan_title = mscan_store.get(mscan_job_id).get("title")
            except (KeyError, ValueError):
                mscan_title = None
        inhalt = {
            "mode": params.get("mode"), "n_channels": len(channels),
            "recordings": stats.get("recordings"), "active_channels": stats.get("active_channels"),
            "cycles": stats.get("cycles"), "mscan_job_id": mscan_job_id,
            "mscan_title": mscan_title,
        }
        auswertung = {"server": "mscan", "query": f"run={job_id}"}

    # Nachtrag T-201: "ablage" einheitlich als portabler Pfad (vorher nur
    # der basename - lieferte fuer gleichnamige externe Roots auf
    # verschiedenen Laufwerken denselben, nicht unterscheidbaren Text).
    storage_root_raw = manifest.get("storage_root")
    ablage = _to_relative(_resolve_storage_root(repo_root, storage_root_raw), repo_root) if storage_root_raw else ""

    return {
        "rec_id": f"job:{job_id}", "art": job_type, "herkunft": "longrun",
        "label": manifest.get("label") or "", "start_iso": manifest.get("start_iso"),
        "end_iso": end_iso, "dauer_s": dauer, "zustand": state,
        "abort_reason": manifest.get("abort_reason"),
        "abort_code": manifest.get("abort_code"), "abort_detail": manifest.get("abort_detail"),
        "inhalt": inhalt,
        "dateien": len(files), "bytes": stats.get("bytes", 0) or 0,
        "ablage": ablage,
        "job_id": job_id, "manifest_unreachable": bool(manifest.get("manifest_unreachable", False)),
        "valid": True, "auswertung": auswertung,
        "loesch": {"kind": "job", "ref": job_id, "erlaubt": _delete_allowed(state)},
    }


def _job_rows(job_store, mscan_store, repo_root: str) -> list:
    rows = []
    try:
        entries = job_store.list_jobs()
    except Exception:
        return rows
    for entry in entries:
        job_id = entry.get("job_id")
        try:
            manifest = job_store.get(job_id)
        except Exception:
            # Index kannte den Job gerade noch (list_jobs()), get() scheitert
            # trotzdem (Race mit einer anderen Instanz o.ae.) - Index-
            # Kurzfassung als Notbehelf, damit die Zeile nicht verschwindet.
            manifest = dict(entry)
            manifest.setdefault("params", {})
            manifest.setdefault("stats", {})
            manifest.setdefault("files", [])
            manifest.setdefault("abort_reason", None)
            manifest.setdefault("abort_code", None)
            manifest.setdefault("abort_detail", None)
            manifest.setdefault("ended_iso", None)
            manifest.setdefault("storage_root", None)
            manifest["manifest_unreachable"] = True
        rows.append(_job_row(manifest, mscan_store, repo_root))
    return rows


# ---------------------------------------------------------------------------
# Verwaiste Long-Run-Verzeichnisse (T-1104): longrun_*-Ordner unterhalb einer
# Storage-Root ohne (mehr) passenden Eintrag in jobs/index.json - z. B. nach
# manuellem Aufraeumen des Index oder einem Absturz vor dem ersten Heartbeat.
# _resolve_storage_root ist seit T-201 der Import-Alias auf
# eb200_core.path_sandbox.storage_root_abs (siehe Import oben).
# ---------------------------------------------------------------------------

def _oldest_file_mtime(dir_path: str):
    oldest = None
    try:
        entries = os.scandir(dir_path)
    except OSError:
        return None
    with entries:
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if oldest is None or mtime < oldest:
                oldest = mtime
    return oldest


def _scan_dir_stats(dir_path: str):
    """(Dateianzahl, Bytes) einer Verzeichnisebene (nicht rekursiv)."""
    n, total = 0, 0
    try:
        entries = os.scandir(dir_path)
    except OSError:
        return 0, 0
    with entries:
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                n += 1
                total += entry.stat().st_size
            except OSError:
                continue
    return n, total


def _first_specbin_json(dir_path: str):
    """Erste *.specbin.json-Sidecar (alphabetisch) in dir_path, oder None."""
    try:
        names = sorted(e.name for e in os.scandir(dir_path) if e.name.endswith(".specbin.json"))
    except OSError:
        return None
    if not names:
        return None
    try:
        with open(os.path.join(dir_path, names[0]), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _first_specbin_eval(dir_path: str, repo_root: str) -> dict:
    """auswertung fuer eine verwaiste Zeile: erste .specbin -> Spektrum-Link,
    sonst kein Server (siehe Aufgabe T-1104)."""
    try:
        names = sorted(e.name for e in os.scandir(dir_path) if e.name.endswith(".specbin"))
    except OSError:
        names = []
    if not names:
        return {"server": None, "query": None}
    rel = _to_relative(os.path.join(dir_path, names[0]), repo_root)
    return {"server": "spectrum", "query": f"path={rel}"}


def _orphan_row(dir_path: str, repo_root: str, storage_root_abs: str, job_ids: set, mscan_store):
    name = os.path.basename(dir_path)
    suffix = name[len("longrun_"):]
    manifest = None
    manifest_path = os.path.join(dir_path, "job.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            manifest = None
    if not isinstance(manifest, dict):
        manifest = None
    manifest_job_id = manifest.get("job_id") if manifest else None

    if suffix in job_ids or (manifest_job_id and manifest_job_id in job_ids):
        return None   # kein Waisenkind - echter (evtl. nur unerreichbarer) Job

    rel_dir = _to_relative(dir_path, repo_root)
    n_files, n_bytes = _scan_dir_stats(dir_path)

    if manifest is not None:
        jr = _job_row(manifest, mscan_store, repo_root)
        inhalt, start_iso = jr["inhalt"], jr["start_iso"]
        end_iso, dauer_s = jr["end_iso"], jr["dauer_s"]
        art = manifest.get("type") or "unbekannt"
        label = manifest.get("label") or name
    else:
        sidecar = _first_specbin_json(dir_path)
        if sidecar is None:
            art = "unbekannt"
        else:
            art = "dscan" if sidecar.get("format") == _SPECTRUM_FORMAT_DSCAN else "ifpan"
        inhalt = {}
        start_iso = _iso_from_unix(_oldest_file_mtime(dir_path))
        end_iso = None
        dauer_s = None
        label = name

    return {
        "rec_id": f"orphan:{rel_dir}", "art": art, "herkunft": "longrun",
        "label": label, "start_iso": start_iso, "end_iso": end_iso, "dauer_s": dauer_s,
        "zustand": "verwaist", "abort_reason": None,
        "abort_code": ERR_NO_INDEX_ENTRY, "abort_detail": None,
        "inhalt": inhalt, "dateien": n_files, "bytes": n_bytes,
        "ablage": _to_relative(storage_root_abs, repo_root), "job_id": manifest_job_id,
        "manifest_unreachable": False, "valid": True,
        "auswertung": _first_specbin_eval(dir_path, repo_root),
        "loesch": {"kind": "orphan", "ref": rel_dir, "erlaubt": True},
    }


def _orphan_rows(job_store, repo_root: str, record_dir: str, mscan_store) -> list:
    try:
        job_ids = {e["job_id"] for e in job_store.list_jobs()}
    except Exception:
        return []

    rows = []
    for root_abs in _all_storage_roots(repo_root, record_dir):
        if not os.path.isdir(root_abs):
            continue
        try:
            entries = sorted(os.scandir(root_abs), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if not is_dir or not entry.name.startswith("longrun_"):
                continue
            row = _orphan_row(entry.path, repo_root, root_abs, job_ids, mscan_store)
            if row is not None:
                rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Oeffentliche API
# ---------------------------------------------------------------------------

def _sort_key(row: dict):
    dt = _parse_iso(row.get("start_iso"))
    if dt is None:
        return (1, 0.0)
    return (0, -dt.timestamp())


def list_recordings(job_store, *, repo_root: str = paths.DATA_ROOT, session_dir: str = "sessions",
                     record_dir: str = "records", mscan_store=None) -> list:
    """
    Liefert ALLE Aufzeichnungen (Direkt-Sessions, Direkt-Spektren, Long-Run-
    Jobs, verwaiste Long-Run-Verzeichnisse - T-1104) als Liste von Zeilen im
    einheitlichen Schema (siehe ROW_KEYS/Moduldoku), sortiert nach start_iso
    absteigend (neueste zuerst, Zeilen ohne Start am Ende). Wirft NIE eine
    Ausnahme - jede der fuenf Quellen ist einzeln abgesichert; eine defekte
    Einzeldatei erscheint als valid=False-Zeile statt die ganze Antwort zu
    gefaehrden.
    """
    repo_root = os.path.abspath(repo_root)
    session_labels = LabelStore(os.path.join(repo_root, "session_labels.json"))
    spectrum_labels = LabelStore(os.path.join(repo_root, "spectrum_labels.json"))
    audio_labels = LabelStore(os.path.join(repo_root, "audio_labels.json"))

    rows = []
    try:
        rows.extend(_session_rows(repo_root, session_dir, session_labels))
    except Exception:
        pass
    try:
        rows.extend(_spectrum_rows(repo_root, record_dir, spectrum_labels))
    except Exception:
        pass
    try:
        rows.extend(_audio_rows(repo_root, record_dir, audio_labels))
    except Exception:
        pass
    try:
        rows.extend(_job_rows(job_store, mscan_store, repo_root))
    except Exception:
        pass
    try:
        rows.extend(_orphan_rows(job_store, repo_root, record_dir, mscan_store))
    except Exception:
        pass

    rows.sort(key=_sort_key)
    return rows


def summarize(rows: list) -> dict:
    """Zaehlwerke fuer die Filter-Chips (T-1103/T-1104): Gesamtzahl, Verteilung
    je Art/Herkunft/Zustand, Gesamt-Bytes."""
    je_art: dict = {}
    je_herkunft: dict = {}
    je_zustand: dict = {}
    total_bytes = 0
    for r in rows:
        art = r.get("art")
        herkunft = r.get("herkunft")
        zustand = r.get("zustand")
        je_art[art] = je_art.get(art, 0) + 1
        je_herkunft[herkunft] = je_herkunft.get(herkunft, 0) + 1
        je_zustand[zustand] = je_zustand.get(zustand, 0) + 1
        total_bytes += r.get("bytes") or 0
    return {"gesamt": len(rows), "je_art": je_art, "je_herkunft": je_herkunft,
            "je_zustand": je_zustand, "bytes": total_bytes}


# ---------------------------------------------------------------------------
# Zentrales Loeschen (T-1104) - bildet die bisher je Analyse-Server
# verstreuten Schutzregeln (analyze_sessions.delete_session(), analyze_
# spectrum.delete_recording(), JobStore.delete_with_data()) HIER zentral
# nach; die Analyse-Server rufen seither nur noch delete_recording() auf.
# ---------------------------------------------------------------------------

def _resolve_within(repo_root: str, rel: str) -> str:
    """Loest rel (ein clientseitig gelieferter Pfad, relativ zum Repo-Root
    ODER absolut in einem konfigurierten storage_root) zu einem absoluten
    Pfad auf. Nachtrag T-201: vorher wurde NUR repo_root selbst zugelassen -
    Loeschen/Umbenennen einer Aufnahme in einem externen storage_root war
    damit unmoeglich (der orphan-Zweig unten pruefte zwar zusaetzlich gegen
    die konfigurierten Roots, kam dabei aber nie an, weil diese Funktion
    externe Pfade schon vorher ablehnte). Jetzt Whitelist = Repo-Root +
    storage_roots wie bei den Auswerte-Servern (siehe
    eb200_core/path_sandbox.py). Suffixregeln bleiben Sache der Aufrufer
    (je kind eigene, spezifischere Fehlermeldung vor dem Aufruf hier).
    Wirft RecordingNotDeletableError bei einem Sandbox-Verstoss (Pfad
    ausserhalb aller Roots)."""
    repo_root = os.path.abspath(repo_root)
    try:
        return resolve_within(rel, sandbox_roots(repo_root))
    except ValueError:
        raise RecordingNotDeletableError(ERR_OUTSIDE_STORAGE, ref=rel)


def delete_recording(job_store, kind: str, ref: str, *, repo_root: str = paths.DATA_ROOT) -> dict:
    """
    Einheitliches, endgueltiges Loeschen EINER Aufzeichnung ueber alle
    Herkuenfte hinweg (T-1104). job_store wird nur fuer kind "job"/"orphan"
    gebraucht (sonst darf None uebergeben werden). Wirft RecordingLockedError
    (Aufzeichnung laeuft/ist unvollstaendig - nichts geloescht) oder
    RecordingNotDeletableError (Sandbox-/Regelverstoss - nichts geloescht);
    fuer kind="job" reicht ValueError/KeyError aus JobStore.delete_with_data()
    unveraendert durch. Loggt selbst nichts - der Aufrufer entscheidet.
    """
    repo_root = os.path.abspath(repo_root)

    if kind == "job":
        result = job_store.delete_with_data(ref)
        return {"deleted": [], "kind": "job",
                "deleted_files": result.get("deleted_files", 0)}

    if kind == "session":
        if not ref.endswith(".db"):
            raise RecordingNotDeletableError(ERR_WRONG_EXTENSION, kind="session", expected=".db")
        resolved = _resolve_within(repo_root, ref)
        wal_path = resolved + "-wal"
        if os.path.isfile(wal_path) and (time.time() - os.path.getmtime(wal_path)) < 10.0:
            raise RecordingLockedError(ERR_MAY_BE_RUNNING, kind="session")

        deleted = []
        # T-806: der Thumbnail-Cache faellt wie das .befund.json mit - beides
        # ist regenerierbares Beiwerk. Die Endung muss zu THUMB_CACHE_SUFFIX in
        # analyze_sessions.py passen; ein Import von dort kommt nicht in Frage
        # (eb200_core haengt nie an einem Server-Modul).
        for path in (resolved, wal_path, resolved + "-shm",
                     resolved[:-len(".db")] + ".befund.json",
                     resolved[:-len(".db")] + ".thumb.json"):
            if os.path.isfile(path):
                os.unlink(path)
                deleted.append(os.path.basename(path))
        rel = _to_relative(resolved, repo_root)
        LabelStore(os.path.join(repo_root, "session_labels.json")).set(rel, "")
        LabelStore(os.path.join(repo_root, "session_notes.json"), cleaner=clean_note).set(rel, "")
        return {"deleted": deleted, "kind": "session"}

    if kind == "spectrum":
        if not ref.endswith(".specbin"):
            raise RecordingNotDeletableError(ERR_WRONG_EXTENSION, kind="spectrum", expected=".specbin")
        resolved = _resolve_within(repo_root, ref)
        dir_path = os.path.dirname(resolved)
        dir_name = os.path.basename(dir_path)
        if dir_name.startswith("longrun_") or os.path.isfile(os.path.join(dir_path, "job.json")):
            raise RecordingNotDeletableError(ERR_LONGRUN_PART)

        sidecar_path = resolved + ".json"
        if os.path.isfile(sidecar_path):
            try:
                with open(sidecar_path, encoding="utf-8") as f:
                    meta = json.load(f)
                if bool(meta.get("running", False)):
                    raise RecordingLockedError(ERR_INCOMPLETE)
            except (OSError, ValueError):
                # Sidecar kaputt: wie "kein Sidecar" behandeln (naechster
                # Zweig) - nur loeschbar, wenn die .specbin seit > 60s nicht
                # mehr geschrieben wurde, statt dauerhaft gesperrt zu bleiben
                # (Abweichung vom bisherigen fail-closed-fuer-immer, siehe
                # Aufgabe T-1104: sonst bleiben ungueltige Reste ewig liegen).
                if (time.time() - os.path.getmtime(resolved)) < 60.0:
                    raise RecordingLockedError(ERR_SIDECAR_UNREADABLE)
        else:
            if (time.time() - os.path.getmtime(resolved)) < 60.0:
                raise RecordingLockedError(ERR_NO_SIDECAR)

        deleted = []
        for path in (resolved, sidecar_path, resolved[:-len(".specbin")] + ".befund.json"):
            if os.path.isfile(path):
                os.unlink(path)
                deleted.append(os.path.basename(path))
        rel = _to_relative(resolved, repo_root)
        LabelStore(os.path.join(repo_root, "spectrum_labels.json")).set(rel, "")
        LabelStore(os.path.join(repo_root, "spectrum_notes.json"), cleaner=clean_note).set(rel, "")
        return {"deleted": deleted, "kind": "spectrum"}

    if kind == "audio":
        if not ref.endswith(".wav"):
            raise RecordingNotDeletableError(ERR_WRONG_EXTENSION, kind="audio", expected=".wav")
        resolved = _resolve_within(repo_root, ref)
        dir_path = os.path.dirname(resolved)
        dir_name = os.path.basename(dir_path)
        if dir_name.startswith("longrun_") or os.path.isfile(os.path.join(dir_path, "job.json")):
            raise RecordingNotDeletableError(ERR_LONGRUN_PART)

        sidecar_path = resolved + ".json"
        if not os.path.isfile(sidecar_path):
            if (time.time() - os.path.getmtime(resolved)) < 60.0:
                raise RecordingLockedError(ERR_NO_SIDECAR)
        else:
            try:
                with open(sidecar_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                # Kaputtes Sidecar: wie "kein Sidecar" behandeln (siehe
                # Spektrum-Zweig oben) - nur loeschbar, wenn die WAV seit
                # > 60s nicht mehr geschrieben wurde.
                if (time.time() - os.path.getmtime(resolved)) < 60.0:
                    raise RecordingLockedError(ERR_SIDECAR_UNREADABLE)
            else:
                # T-1109: running=True heisst normalerweise "laeuft noch" -
                # BEWUSST ABWEICHEND vom Spektrum-Zweig oben (der bei
                # running=True dauerhaft sperrt) gilt hier ein 60s-Escape:
                # eine WAV, die seit > 60s nicht mehr geschrieben wurde,
                # ist ein Absturzrest mit stehengebliebenem Flag statt
                # einer echten laufenden Aufnahme - sonst bliebe ein
                # abgestuerzter Rest fuer immer gesperrt (T-1104-Rationale).
                if (bool(meta.get("running", False))
                        and (time.time() - os.path.getmtime(resolved)) < 60.0):
                    raise RecordingLockedError(ERR_MAY_BE_RUNNING)

        # T-922a: die SigMF-Nebendateien gehoeren mit weg. Sie sind zwar
        # regenerierbar, aber eine .sigmf-meta, die auf eine geloeschte WAV
        # zeigt, ist schlimmer als gar keine - sie behauptet eine Aufnahme,
        # die es nicht mehr gibt. Der Stamm ist der WAV-Pfad ohne ".wav"
        # (Muster _audio_row oben, dort fuer .specbin).
        stem = resolved[:-len(".wav")]
        deleted = []
        for path in (resolved, sidecar_path, resolved + ".audspec", resolved + ".klasse.json",
                     stem + ".sigmf-meta", stem + ".sigmf-data"):
            if os.path.isfile(path):
                os.unlink(path)
                deleted.append(os.path.basename(path))
        rel = _to_relative(resolved, repo_root)
        LabelStore(os.path.join(repo_root, "audio_labels.json")).set(rel, "")
        LabelStore(os.path.join(repo_root, "audio_notes.json"), cleaner=clean_note).set(rel, "")
        LabelStore(os.path.join(repo_root, "audio_klassen.json")).set(rel, "")
        return {"deleted": deleted, "kind": "audio"}

    if kind == "orphan":
        resolved_dir = _resolve_within(repo_root, ref)
        name = os.path.basename(resolved_dir)
        if not name.startswith("longrun_"):
            raise RecordingNotDeletableError(ERR_WRONG_ORPHAN_DIR)

        try:
            configured = load_storage_roots(os.path.join(repo_root, "storage_roots.json"))
        except Exception:
            configured = ["records"]
        roots = [_resolve_storage_root(repo_root, r) for r in configured]
        roots.append(_resolve_storage_root(repo_root, "records"))

        def _unter_root(p: str) -> bool:
            for root in roots:
                root_abs = os.path.abspath(root)
                try:
                    if os.path.commonpath([p, root_abs]) == root_abs:
                        return True
                except ValueError:
                    continue
            return False

        if not _unter_root(resolved_dir):
            raise RecordingNotDeletableError(ERR_NO_STORAGE_ROOT)

        manifest = None
        manifest_path = os.path.join(resolved_dir, "job.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
            except (OSError, ValueError):
                manifest = None
        manifest_job_id = manifest.get("job_id") if isinstance(manifest, dict) else None
        suffix = name[len("longrun_"):]
        try:
            job_ids = {e["job_id"] for e in job_store.list_jobs()}
        except Exception:
            job_ids = set()
        if suffix in job_ids or (manifest_job_id and manifest_job_id in job_ids):
            raise RecordingNotDeletableError(ERR_KNOWN_JOB_DIR)

        try:
            entries = list(os.scandir(resolved_dir))
        except OSError:
            entries = []
        now = time.time()
        for entry in entries:
            try:
                if (now - entry.stat().st_mtime) < 60.0:
                    raise RecordingLockedError(ERR_RECENTLY_WRITTEN)
            except OSError:
                continue

        deleted = [e.name for e in entries]
        shutil.rmtree(resolved_dir)
        return {"deleted": deleted, "kind": "orphan"}

    raise RecordingNotDeletableError(ERR_UNKNOWN_KIND, kind=kind)


def export_recording_sigmf(kind: str, ref: str, *, with_data: bool = False,
                           repo_root: str = paths.DATA_ROOT) -> dict:
    """
    SigMF-Export EINER Aufzeichnung (T-922a). Pendant zu
    delete_recording(): dieselbe Adressierung ueber kind/ref, dieselbe
    Sandbox, dieselben Fehlerklassen - damit die Oberflaeche fuer beide
    Wege denselben Umgang hat und nicht zwei Fehlermodelle kennen muss.

    Nur kind="audio" und nur IQ-Aufnahmen. Alles andere ist ein
    RecordingNotDeletableError; die Klasse heisst nach ihrem ersten
    Zweck, meint hier aber allgemein "diese Aufzeichnung ist dafuer
    nicht zulaessig". Eine laufende Aufnahme wird mit
    RecordingLockedError abgewiesen (HTTP 409 wie beim Loeschen).

    Die Zulaessigkeitspruefung sitzt HIER und nicht nur in der
    Oberflaeche: die Knoepfe in :8050 werden bei Nicht-IQ-Zeilen zwar
    gar nicht erst gezeigt, ein Aufruf des Endpunkts von Hand muss
    trotzdem abprallen.

    Die eigentliche Arbeit macht eb200_core/sigmf_export.write_sigmf();
    hier steht nur Aufloesung und Zulaessigkeit. Kein job_store-Parameter
    - anders als delete_recording braucht der Export keinen.
    """
    if kind != "audio":
        raise RecordingNotDeletableError(ERR_SIGMF_IQ_ONLY, kind=kind)
    if not ref.endswith(".wav"):
        raise RecordingNotDeletableError(ERR_WRONG_EXTENSION, kind="audio", expected=".wav")

    repo_root = os.path.abspath(repo_root)
    resolved = _resolve_within(repo_root, ref)
    if not os.path.isfile(resolved):
        raise RecordingNotDeletableError(ERR_NOT_FOUND, ref=ref)

    try:
        result = write_sigmf(resolved, with_data=with_data)
    except SigmfRunningError as exc:
        raise RecordingLockedError(ERR_SIGMF_EXPORT, detail=str(exc))
    except FileNotFoundError as exc:
        raise RecordingNotDeletableError(ERR_SIDECAR_UNREADABLE, detail=str(exc))
    except (ValueError, KeyError) as exc:
        raise RecordingNotDeletableError(ERR_SIGMF_EXPORT, detail=str(exc))

    # Nach aussen gehen die Dateinamen UND der Ablageort, beides im
    # portablen Format dieses Moduls (_to_relative = to_portable aus
    # eb200_core/path_sandbox.py): innerhalb des Repos relativ mit "/",
    # bei einem externen storage_root absolut - ein "../"-Pfad ueber
    # Laufwerksgrenzen waere weder bildbar noch aussagekraeftig.
    # T-922c: der Ort kam nachtraeglich dazu. Die urspruengliche
    # Festlegung "nur Namen, keine Pfade" war zu streng - bei mehreren
    # Storage-Roots ist "geschrieben" ohne "wohin" keine Auskunft, und
    # dieselbe Angabe steht ohnehin schon in der Spalte "Ablage".
    return {
        "kind": "audio",
        "ncd": result["ncd"],
        "dir": _to_relative(os.path.dirname(result["meta_path"]), repo_root),
        "meta_name": os.path.basename(result["meta_path"]),
        "data_name": (os.path.basename(result["data_path"])
                      if result["data_path"] else None),
        "header_bytes": result["header_bytes"],
        "sample_bytes": result["sample_bytes"],
        "stale_data_name": (os.path.basename(result["stale_data"])
                            if result.get("stale_data") else None),
    }


def set_recording_label(job_store, kind: str, ref: str, text, *, repo_root: str = paths.DATA_ROOT) -> dict:
    """
    Einheitliches Setzen des Kurztexts EINER Aufzeichnung ueber alle
    Herkuenfte hinweg (T-1110), Pendant zu delete_recording(). kind="job"
    nutzt denselben Pfad wie der bestehende /api/joblabel-Handler
    (JobStore.update_label(), ref=job_id). kind in
    ("session", "spectrum", "audio") loest ref ueber _resolve_within() in
    der Sandbox auf (gleiche Endungspruefung wie im jeweiligen
    delete_recording()-Zweig) und setzt ueber LabelStore - Kuerzung/
    Bereinigung des Texts uebernimmt dabei jeweils die Zielschicht
    (JobStore._clean_job_label bzw. label_store.clean_label), hier also
    keine doppelte Normalisierung. kind="orphan" hat keinen Kurztext
    (verwaiste Verzeichnisse tragen keine Metadaten) - wirft RecordingNotDeletableError.
    """
    repo_root = os.path.abspath(repo_root)

    if kind == "job":
        return job_store.update_label(ref, text)

    if kind == "session":
        if not ref.endswith(".db"):
            raise RecordingNotDeletableError(ERR_WRONG_EXTENSION, kind="session", expected=".db")
        resolved = _resolve_within(repo_root, ref)
        rel = _to_relative(resolved, repo_root)
        saved = LabelStore(os.path.join(repo_root, "session_labels.json")).set(rel, text)
        return {"kind": "session", "label": saved}

    if kind == "spectrum":
        if not ref.endswith(".specbin"):
            raise RecordingNotDeletableError(ERR_WRONG_EXTENSION, kind="spectrum", expected=".specbin")
        resolved = _resolve_within(repo_root, ref)
        rel = _to_relative(resolved, repo_root)
        saved = LabelStore(os.path.join(repo_root, "spectrum_labels.json")).set(rel, text)
        return {"kind": "spectrum", "label": saved}

    if kind == "audio":
        if not ref.endswith(".wav"):
            raise RecordingNotDeletableError(ERR_WRONG_EXTENSION, kind="audio", expected=".wav")
        resolved = _resolve_within(repo_root, ref)
        rel = _to_relative(resolved, repo_root)
        saved = LabelStore(os.path.join(repo_root, "audio_labels.json")).set(rel, text)
        return {"kind": "audio", "label": saved}

    if kind == "orphan":
        raise RecordingNotDeletableError(ERR_ORPHAN_NO_LABEL)

    raise RecordingNotDeletableError(ERR_UNKNOWN_KIND, kind=kind)


# ---------------------------------------------------------------------------
# Selbsttest (python -m eb200_core.recording_index --selftest, ohne Hardware)
# ---------------------------------------------------------------------------

class _FakeUpdate:
    """Leichtgewichtiger Stand-in fuer waterfall.DScanUpdate, nur fuer den
    Selbsttest (Muster eb200_core.session_store._FakeUpdate) - dieses Modul
    importiert waterfall.py nicht."""

    def __init__(self, complete, start_hz, stop_hz, step_hz, t, full_raw_levels):
        self.complete = complete
        self.start_hz = start_hz
        self.stop_hz = stop_hz
        self.step_hz = step_hz
        self.t = t
        self.full_raw_levels = full_raw_levels


def _write_specbin(record_dir: str, name: str, sidecar: dict | None) -> str:
    """Legt eine winzige .specbin-Datei (ein paar Bytes, Inhalt wird von
    list_recordings() nie gelesen) plus optionalem Sidecar an. sidecar=None
    laesst die Sidecar-Datei bewusst weg (T3, drittes Beispiel)."""
    os.makedirs(record_dir, exist_ok=True)
    path = os.path.join(record_dir, name)
    with open(path, "wb") as f:
        f.write(b"\x00\x01\x02\x03")
    if sidecar is not None:
        with open(path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f)
    return path


def _write_wav(record_dir: str, name: str, sidecar: dict | None) -> str:
    """Legt eine winzige WAV-Datei (ein paar Bytes, der Code hier parst die
    WAV nie) plus optionalem Sidecar an - kein Import von audio_wav.py
    noetig (Muster _write_specbin, sidecar=None laesst die Sidecar-Datei
    bewusst weg)."""
    os.makedirs(record_dir, exist_ok=True)
    path = os.path.join(record_dir, name)
    with open(path, "wb") as f:
        f.write(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    if sidecar is not None:
        with open(path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f)
    return path


def _self_test() -> None:
    import tempfile

    from eb200_core.job_store import JobStore, ST_BEENDET
    from eb200_core.mscan_job import MscanJobStore
    from eb200_core.session_store import SessionStore

    with tempfile.TemporaryDirectory() as tmp:
        session_dir = os.path.join(tmp, "sessions")
        record_dir = os.path.join(tmp, "records")
        index_path = os.path.join(tmp, "jobs", "index.json")
        roots_path = os.path.join(tmp, "storage_roots.json")
        os.makedirs(record_dir, exist_ok=True)
        with open(roots_path, "w", encoding="utf-8") as f:
            json.dump([record_dir], f)
        store = JobStore(index_path=index_path, storage_roots_path=roots_path)
        mscan_store = MscanJobStore(os.path.join(tmp, "jobs_mscan"))

        # --- T1: leere Ordner, leerer JobStore ------------------------------
        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        assert rows == [], rows
        print("T1 ok")

        # --- T2: zwei Direkt-Sessions ----------------------------------------
        db_a = os.path.join(session_dir, "session_a.db")
        sess_a = SessionStore(db_a, source="selftest", mode="DSCAN")
        raw1 = [10, 20, 30, 40]
        raw2 = [11, 21, 31, 41]
        sess_a.record(_FakeUpdate(True, 100_000_000, 100_003_000, 1000, time.time(), raw1), speed="NORM")
        sess_a.record(_FakeUpdate(True, 100_000_000, 100_003_000, 1000, time.time() + 1, raw2), speed="NORM")
        # zweites Segment (Raster-Wechsel) - insgesamt 2 Segmente, 3 Sweeps.
        raw3 = [1, 2, 3]
        sess_a.record(_FakeUpdate(True, 200_000_000, 200_003_000, 1000, time.time() + 2, raw3), speed="NORM")
        sess_a.close()

        # Label fuer session_a setzen (LabelStore-Uebernahme pruefen).
        rel_a = "sessions/session_a.db"
        LabelStore(os.path.join(tmp, "session_labels.json")).set(rel_a, "Testlauf A")

        db_b = os.path.join(session_dir, "session_b.db")
        sess_b = SessionStore(db_b, source="selftest", mode="DSCAN")
        sess_b.record(_FakeUpdate(True, 50_000_000, 50_001_000, 1000, time.time(), [5, 6]), speed="NORM")
        sess_b.close()
        # frischer -wal-Touch -> zustand "laeuft" (Live-Heuristik).
        wal_b = db_b + "-wal"
        with open(wal_b, "wb") as f:
            f.write(b"\x00")

        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        sess_rows = {r["rec_id"]: r for r in rows if r["rec_id"].startswith("session:")}
        assert len(sess_rows) == 2, sess_rows
        row_a = sess_rows[f"session:{rel_a}"]
        assert row_a["art"] == "dscan" and row_a["herkunft"] == "direkt", row_a
        assert row_a["label"] == "Testlauf A", row_a["label"]
        assert row_a["inhalt"]["start_hz"] == 100_000_000, row_a["inhalt"]
        assert row_a["inhalt"]["stop_hz"] == 200_003_000, row_a["inhalt"]
        assert row_a["inhalt"]["sweeps"] == 3, row_a["inhalt"]
        assert row_a["inhalt"]["n_segments"] == 2, row_a["inhalt"]
        assert row_a["zustand"] == "beendet", row_a["zustand"]
        assert row_a["loesch"]["erlaubt"] is True, row_a["loesch"]

        rel_b = "sessions/session_b.db"
        row_b = sess_rows[f"session:{rel_b}"]
        assert row_b["zustand"] == "laeuft", row_b["zustand"]
        assert row_b["loesch"]["erlaubt"] is False, row_b["loesch"]
        print("T2 ok")

        # --- T3: Direkt-Spektrum ---------------------------------------------
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        _write_specbin(record_dir, "spec_done.specbin", {
            "format": "eb200-ifpan-bins-v1", "start_iso": now_iso, "stop_iso": now_iso,
            "center_hz": 100_000_000, "span_hz": 200_000, "bw_hz": 9000, "demod": "AM",
            "frames_written": 42,
        })
        _write_specbin(record_dir, "spec_running.specbin", {
            "format": "eb200-ifpan-bins-v1", "start_iso": now_iso, "stop_iso": now_iso,
            "center_hz": 100_000_000, "span_hz": 200_000, "bw_hz": 9000, "demod": "AM",
            "frames_written": 5, "running": True,
        })
        _write_specbin(record_dir, "spec_no_sidecar.specbin", None)

        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        spec_rows = {r["rec_id"]: r for r in rows if r["rec_id"].startswith("spectrum:")}
        assert len(spec_rows) == 3, spec_rows

        r_done = spec_rows["spectrum:records/spec_done.specbin"]
        assert r_done["zustand"] == "beendet" and r_done["valid"] is True, r_done
        assert r_done["inhalt"]["frames"] == 42, r_done["inhalt"]

        r_run = spec_rows["spectrum:records/spec_running.specbin"]
        assert r_run["zustand"] == "laeuft" and r_run["valid"] is True, r_run

        r_missing = spec_rows["spectrum:records/spec_no_sidecar.specbin"]
        assert r_missing["zustand"] == "laeuft", r_missing["zustand"]
        assert r_missing["valid"] is False, r_missing
        assert r_missing["label"] == "spec_no_sidecar.specbin", r_missing["label"]
        print("T3 ok")

        # --- T4: Long-Run dscan/ifpan/mscan -----------------------------------
        m_dscan = store.create_job(
            type="dscan", label="Long-Run DSCAN", params={"start_hz": 88_000_000, "stop_hz": 108_000_000, "speed": "NORM"},
            start_iso="2020-01-15T10:00:00+01:00", end_iso="2020-01-15T11:00:00+01:00", decimation_s=15,
        )
        store.set_state(m_dscan["job_id"], ST_LAEUFT)
        store.set_state(m_dscan["job_id"], ST_BEENDET)
        store.heartbeat(m_dscan["job_id"],
                         stats={"sweeps": 10, "frames": 10, "bytes": 1234, "files": ["teil01.specbin"],
                                "geometry": {"start_hz": 88_000_000, "stop_hz": 108_000_000, "step_hz": 15000, "n_bins": 1000}},
                         files=["teil01.specbin"])

        m_ifpan = store.create_job(
            type="ifpan", label="Long-Run IFPAN", params={"center_hz": 100_000_000, "span_hz": 200_000, "bw_hz": 9000, "demod": "AM"},
            start_iso="2020-01-15T12:00:00+01:00", end_iso="2020-01-15T13:00:00+01:00",
        )
        store.set_state(m_ifpan["job_id"], ST_LAEUFT)
        store.set_state(m_ifpan["job_id"], ST_BEENDET)
        store.heartbeat(m_ifpan["job_id"], stats={"frames": 500, "bytes": 5678}, files=["teil01.specbin"])

        mdef = mscan_store.create("Test Air Com", params={"mode": "ueberwachung"})
        m_mscan = store.create_job(
            type="mscan", label=f"MSCAN {mdef['id']}",
            params={"mscan_job_id": mdef["id"], "mode": "ueberwachung",
                    "channels": [{"id": "C1"}, {"id": "C2"}]},
            start_iso="2020-01-15T14:00:00+01:00", end_iso="2020-01-15T15:00:00+01:00",
        )
        store.set_state(m_mscan["job_id"], ST_LAEUFT)
        store.set_state(m_mscan["job_id"], ST_BEENDET)
        store.heartbeat(m_mscan["job_id"],
                         stats={"recordings": 7, "active_channels": 2, "cycles": 3, "bytes": 999},
                         files=["a.wav"])

        # zweiter mscan-Lauf mit unbekannter Definition -> mscan_title None.
        m_mscan2 = store.create_job(
            type="mscan", label="MSCAN verwaist",
            params={"mscan_job_id": "ZZZZZZ", "mode": "inventur", "channels": [{"id": "C1"}]},
            start_iso="2020-01-15T16:00:00+01:00", end_iso="2020-01-15T17:00:00+01:00",
        )
        store.set_state(m_mscan2["job_id"], ST_LAEUFT)
        store.set_state(m_mscan2["job_id"], ST_BEENDET)
        store.heartbeat(m_mscan2["job_id"], stats={"recordings": 0, "active_channels": 0, "cycles": 1}, files=[])

        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        job_rows = {r["job_id"]: r for r in rows if r["herkunft"] == "longrun"}

        rd = job_rows[m_dscan["job_id"]]
        assert rd["art"] == "dscan", rd
        assert rd["inhalt"]["start_hz"] == 88_000_000 and rd["inhalt"]["step_hz"] == 15000, rd["inhalt"]
        assert rd["inhalt"]["n_segments"] == 1, rd["inhalt"]
        assert rd["auswertung"] == {"server": "spectrum", "query": f"job={m_dscan['job_id']}"}, rd["auswertung"]

        ri = job_rows[m_ifpan["job_id"]]
        assert ri["art"] == "ifpan" and ri["inhalt"]["frames"] == 500, ri
        assert ri["auswertung"] == {"server": "spectrum", "query": f"job={m_ifpan['job_id']}"}, ri["auswertung"]

        rm = job_rows[m_mscan["job_id"]]
        assert rm["art"] == "mscan", rm
        assert rm["inhalt"]["n_channels"] == 2, rm["inhalt"]
        assert rm["inhalt"]["mscan_job_id"] == mdef["id"], rm["inhalt"]
        assert rm["inhalt"]["mscan_title"] == "Test Air Com", rm["inhalt"]
        assert rm["auswertung"] == {"server": "mscan", "query": f"run={m_mscan['job_id']}"}, rm["auswertung"]

        rm2 = job_rows[m_mscan2["job_id"]]
        assert rm2["inhalt"]["mscan_title"] is None, rm2["inhalt"]
        print("T4 ok")

        # --- T5: records/longrun_X/ wird NICHT als Direkt-Spektrum gelistet --
        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        spec_ids = {r["rec_id"] for r in rows if r["rec_id"].startswith("spectrum:")}
        assert not any("longrun_" in rid for rid in spec_ids), spec_ids
        print("T5 ok")

        # --- T6: Sortierung -----------------------------------------------
        starts = [r["start_iso"] for r in rows if r["start_iso"] is not None]
        assert starts == sorted(starts, reverse=True), starts
        assert rows[-1]["start_iso"] is None, rows[-1]
        print("T6 ok")

        # --- T7: defekte Session-DB -----------------------------------------
        broken_db = os.path.join(session_dir, "broken.db")
        with open(broken_db, "w", encoding="utf-8") as f:
            f.write("das ist keine SQLite-Datei")
        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        broken_row = next(r for r in rows if r["rec_id"] == "session:sessions/broken.db")
        assert broken_row["valid"] is False, broken_row
        assert broken_row["zustand"] == "unbekannt", broken_row
        assert broken_row["label"] == "broken.db", broken_row
        assert broken_row["abort_reason"] is None, broken_row
        assert broken_row["abort_code"] == ERR_SESSION_DB_UNREADABLE, broken_row
        print("T7 ok")

        # --- T8: summarize() --------------------------------------------------
        summary = summarize(rows)
        assert summary["gesamt"] == len(rows), summary
        assert sum(summary["je_art"].values()) == len(rows), summary
        assert sum(summary["je_herkunft"].values()) == len(rows), summary
        assert summary["bytes"] >= 0, summary
        print("T8 ok")

        # --- T9: Schema-Vollstaendigkeit --------------------------------------
        for r in rows:
            assert set(r.keys()) == ROW_KEYS, (r["rec_id"], set(r.keys()) ^ ROW_KEYS)
        print("T9 ok")

        # --- T10: Altbestand-Session ohne segment.speed/mtime_s --------------
        old_db = os.path.join(session_dir, "session_old_schema.db")
        conn = sqlite3.connect(old_db)
        try:
            conn.executescript("""
                CREATE TABLE session (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    started_at REAL NOT NULL, source TEXT,
                    mode TEXT NOT NULL DEFAULT 'DSCAN', note TEXT
                );
                CREATE TABLE segment (
                    id INTEGER PRIMARY KEY, start_hz INTEGER NOT NULL,
                    stop_hz INTEGER NOT NULL, step_hz INTEGER NOT NULL,
                    n_bins INTEGER NOT NULL, t_first REAL NOT NULL,
                    t_last REAL NOT NULL, row_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE sweep (
                    id INTEGER PRIMARY KEY,
                    segment_id INTEGER NOT NULL REFERENCES segment(id),
                    seq INTEGER NOT NULL, t_unix REAL NOT NULL, levels BLOB NOT NULL
                );
            """)
            conn.execute("INSERT INTO session (id, started_at, source, mode) VALUES (1, ?, 'alt', 'DSCAN')",
                         (time.time(),))
            conn.execute(
                "INSERT INTO segment (id, start_hz, stop_hz, step_hz, n_bins, t_first, t_last, row_count) "
                "VALUES (1, 100000000, 100003000, 1000, 3, ?, ?, 1)",
                (time.time(), time.time()))
            conn.execute(
                "INSERT INTO sweep (segment_id, seq, t_unix, levels) VALUES (1, 0, ?, ?)",
                (time.time(), b"\x00\x01"))
            conn.commit()
        finally:
            conn.close()

        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        old_row = next(r for r in rows if r["rec_id"] == "session:sessions/session_old_schema.db")
        assert old_row["valid"] is True, old_row
        assert old_row["inhalt"]["speed"] is None, old_row["inhalt"]
        assert old_row["zustand"] == "beendet", old_row["zustand"]
        print("T10 ok")

        # --- T11: verwaiste Long-Run-Verzeichnisse ---------------------------
        orphan_abc_dir = os.path.join(record_dir, "longrun_ABC")
        os.makedirs(orphan_abc_dir, exist_ok=True)
        with open(os.path.join(orphan_abc_dir, "job.json"), "w", encoding="utf-8") as f:
            json.dump({
                "job_id": "ABC", "type": "ifpan", "label": "Verwaist ABC", "state": "beendet",
                "params": {"center_hz": 1_000_000, "span_hz": 2_000}, "stats": {}, "files": [],
                "start_iso": "2020-01-01T00:00:00+01:00", "end_iso": None, "ended_iso": None,
                "abort_reason": None, "storage_root": record_dir, "job_dir": orphan_abc_dir,
            }, f)
        _write_specbin(orphan_abc_dir, "teil01.specbin", {"format": "eb200-ifpan-bins-v1"})

        orphan_def_dir = os.path.join(record_dir, "longrun_DEF")
        os.makedirs(orphan_def_dir, exist_ok=True)
        _write_specbin(orphan_def_dir, "teil01.specbin", {"format": "eb200-dscan-maxhold-v1"})

        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        orphan_rows = {r["rec_id"]: r for r in rows if r["zustand"] == "verwaist"}
        assert len(orphan_rows) == 2, orphan_rows.keys()
        abc = orphan_rows["orphan:records/longrun_ABC"]
        assert abc["art"] == "ifpan" and abc["herkunft"] == "longrun", abc
        assert abc["loesch"] == {"kind": "orphan", "ref": "records/longrun_ABC", "erlaubt": True}, abc["loesch"]
        def_row = orphan_rows["orphan:records/longrun_DEF"]
        assert def_row["art"] == "dscan", def_row
        job_dirnames = {f"orphan:records/longrun_{jid}"
                        for jid in (m_dscan["job_id"], m_ifpan["job_id"], m_mscan["job_id"])}
        assert not (job_dirnames & set(orphan_rows)), job_dirnames & set(orphan_rows)
        summary_check = summarize(rows)
        assert summary_check["je_zustand"].get("verwaist") == 2, summary_check["je_zustand"]
        print("T11 ok")

        # --- T12: delete_recording kind=session -------------------------------
        db_c = os.path.join(session_dir, "session_c.db")
        with open(db_c, "wb") as f:
            f.write(b"dummy")
        rel_c = "sessions/session_c.db"
        LabelStore(os.path.join(tmp, "session_labels.json")).set(rel_c, "Zu loeschen")
        delete_recording(store, "session", rel_c, repo_root=tmp)
        assert not os.path.isfile(db_c)
        assert LabelStore(os.path.join(tmp, "session_labels.json")).get(rel_c) == ""

        db_d = os.path.join(session_dir, "session_d.db")
        with open(db_d, "wb") as f:
            f.write(b"dummy")
        with open(db_d + "-wal", "wb") as f:
            f.write(b"dummy")   # frische mtime -> "live"
        try:
            delete_recording(store, "session", "sessions/session_d.db", repo_root=tmp)
        except RecordingLockedError:
            pass
        else:
            raise AssertionError("RecordingLockedError erwartet (frische -wal)")
        assert os.path.isfile(db_d)

        try:
            delete_recording(store, "session", "../x.db", repo_root=tmp)
        except RecordingNotDeletableError:
            pass
        else:
            raise AssertionError("RecordingNotDeletableError erwartet (Sandbox)")
        print("T12 ok")

        # --- T13: delete_recording kind=spectrum ------------------------------
        spec_running = _write_specbin(record_dir, "running.specbin",
                                       {"format": "eb200-ifpan-bins-v1", "running": True})
        try:
            delete_recording(store, "spectrum", "records/running.specbin", repo_root=tmp)
        except RecordingLockedError:
            pass
        else:
            raise AssertionError("RecordingLockedError erwartet (running)")
        assert os.path.isfile(spec_running)

        spec_old = _write_specbin(record_dir, "old_nosidecar.specbin", None)
        old_t = time.time() - 120
        os.utime(spec_old, (old_t, old_t))
        delete_recording(store, "spectrum", "records/old_nosidecar.specbin", repo_root=tmp)
        assert not os.path.isfile(spec_old)

        _write_specbin(orphan_abc_dir, "teil02.specbin", {"format": "eb200-ifpan-bins-v1"})
        try:
            delete_recording(store, "spectrum", "records/longrun_ABC/teil02.specbin", repo_root=tmp)
        except RecordingNotDeletableError:
            pass
        else:
            raise AssertionError("RecordingNotDeletableError erwartet (Long-Run-Teil)")
        print("T13 ok")

        # --- T14: delete_recording kind=orphan ---------------------------------
        # DEF ist verwaist, aber die Dateien aus T11 sind < 60s alt -> gesperrt.
        try:
            delete_recording(store, "orphan", "records/longrun_DEF", repo_root=tmp)
        except RecordingLockedError:
            pass
        else:
            raise AssertionError("RecordingLockedError erwartet (frische Dateien)")
        assert os.path.isdir(orphan_def_dir)

        old_t = time.time() - 120
        for name in os.listdir(orphan_def_dir):
            os.utime(os.path.join(orphan_def_dir, name), (old_t, old_t))
        delete_recording(store, "orphan", "records/longrun_DEF", repo_root=tmp)
        assert not os.path.isdir(orphan_def_dir)

        try:
            delete_recording(store, "orphan", f"records/longrun_{m_dscan['job_id']}", repo_root=tmp)
        except RecordingNotDeletableError:
            pass
        else:
            raise AssertionError("RecordingNotDeletableError erwartet (echter Job im Index)")
        print("T14 ok")

        # --- T15: delete_recording kind=job (delegiert an delete_with_data) ---
        delete_recording(store, "job", m_mscan2["job_id"], repo_root=tmp)
        try:
            store.get(m_mscan2["job_id"])
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: Job wurde geloescht")

        m_geplant_t15 = store.create_job(
            type="dscan", label="Geplant fuer T15", params={"start_hz": 1, "stop_hz": 2, "speed": "NORM"},
            start_iso="2099-01-01T00:00:00+01:00", end_iso="2099-01-01T01:00:00+01:00", decimation_s=10,
        )
        try:
            delete_recording(store, "job", m_geplant_t15["job_id"], repo_root=tmp)
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: geplanter Job nicht per delete_with_data loeschbar")
        print("T15 ok")

        # --- T16: Direkt-Audio, gueltiges Sidecar -----------------------------
        now_iso2 = datetime.now().astimezone().isoformat(timespec="seconds")
        wav_done = _write_wav(record_dir, "audio_done.wav", {
            "start_iso": now_iso2, "stop_iso": now_iso2, "freq_hz": 100_000_000,
            "mode": "FM", "bw_hz": 9000, "audio_remote_mode": 3,
            "sample_rate_hz": 48000, "channels": 1, "sampwidth_bytes": 2,
            "frames_written": 48000, "duration_s": 1.0, "is_iq": False,
        })
        rel_audio_done = "records/audio_done.wav"
        audio_labels_path = os.path.join(tmp, "audio_labels.json")
        LabelStore(audio_labels_path).set(rel_audio_done, "Testaufnahme")

        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        audio_rows = {r["rec_id"]: r for r in rows if r["rec_id"].startswith("audio:")}
        assert len(audio_rows) == 1, audio_rows
        r_audio_done = audio_rows[f"audio:{rel_audio_done}"]
        assert r_audio_done["art"] == "audio" and r_audio_done["herkunft"] == "direkt", r_audio_done
        assert r_audio_done["label"] == "Testaufnahme", r_audio_done["label"]
        assert r_audio_done["zustand"] == "beendet" and r_audio_done["valid"] is True, r_audio_done
        assert r_audio_done["inhalt"]["freq_hz"] == 100_000_000, r_audio_done["inhalt"]
        assert r_audio_done["inhalt"]["demod"] == "FM", r_audio_done["inhalt"]
        assert r_audio_done["inhalt"]["bw_hz"] == 9000, r_audio_done["inhalt"]
        assert r_audio_done["inhalt"]["sample_rate_hz"] == 48000, r_audio_done["inhalt"]
        assert r_audio_done["inhalt"]["is_iq"] is False, r_audio_done["inhalt"]
        assert r_audio_done["inhalt"]["specbin_rel"] is None, r_audio_done["inhalt"]
        assert r_audio_done["auswertung"] == {"server": "audio", "query": f"path={rel_audio_done}"}, r_audio_done["auswertung"]
        assert set(r_audio_done.keys()) == ROW_KEYS, set(r_audio_done.keys()) ^ ROW_KEYS
        print("T16 ok")

        # --- T17: Direkt-Audio ohne Sidecar, frische mtime ---------------------
        wav_running = _write_wav(record_dir, "audio_running.wav", None)
        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        r_running = next(r for r in rows if r["rec_id"] == "audio:records/audio_running.wav")
        assert r_running["zustand"] == "laeuft", r_running["zustand"]
        assert r_running["valid"] is False, r_running
        assert r_running["loesch"]["erlaubt"] is False, r_running["loesch"]
        print("T17 ok")

        # --- T18: Direkt-Audio mit passendem .specbin --------------------------
        _write_wav(record_dir, "audio_with_spec.wav", {
            "start_iso": now_iso2, "stop_iso": now_iso2, "freq_hz": 50_000_000,
            "mode": "AM", "bw_hz": 6000, "audio_remote_mode": 1,
            "sample_rate_hz": 24000, "channels": 1, "sampwidth_bytes": 2,
            "frames_written": 24000, "duration_s": 1.0, "is_iq": False,
        })
        _write_specbin(record_dir, "audio_with_spec.specbin", {"format": "eb200-ifpan-bins-v1"})
        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        r_with_spec = next(r for r in rows if r["rec_id"] == "audio:records/audio_with_spec.wav")
        assert r_with_spec["inhalt"]["specbin_rel"] == "records/audio_with_spec.specbin", r_with_spec["inhalt"]
        print("T18 ok")

        # --- T19: delete_recording kind=audio (Fall T16) -----------------------
        with open(wav_done + ".audspec", "w", encoding="utf-8") as f:
            f.write("dummy")
        with open(wav_done + ".klasse.json", "w", encoding="utf-8") as f:
            f.write("dummy")
        audio_notes_path = os.path.join(tmp, "audio_notes.json")
        audio_klassen_path = os.path.join(tmp, "audio_klassen.json")
        LabelStore(audio_notes_path, cleaner=clean_note).set(rel_audio_done, "eine Notiz")
        LabelStore(audio_klassen_path).set(rel_audio_done, "CW")
        result19 = delete_recording(store, "audio", rel_audio_done, repo_root=tmp)
        assert not os.path.isfile(wav_done)
        assert not os.path.isfile(wav_done + ".json")
        assert not os.path.isfile(wav_done + ".audspec")
        assert not os.path.isfile(wav_done + ".klasse.json")
        assert set(result19["deleted"]) == {
            "audio_done.wav", "audio_done.wav.json", "audio_done.wav.audspec",
            "audio_done.wav.klasse.json"}, result19
        assert result19["kind"] == "audio", result19
        assert LabelStore(audio_labels_path).get(rel_audio_done) == ""
        assert LabelStore(audio_notes_path, cleaner=clean_note).get(rel_audio_done) == ""
        assert LabelStore(audio_klassen_path).get(rel_audio_done) == ""
        print("T19 ok")

        # --- T20: delete_recording kind=audio, frische WAV ohne Sidecar --------
        try:
            delete_recording(store, "audio", "records/audio_running.wav", repo_root=tmp)
        except RecordingLockedError:
            pass
        else:
            raise AssertionError("RecordingLockedError erwartet (frische WAV ohne Sidecar)")
        assert os.path.isfile(wav_running)
        print("T20 ok")

        # --- T21: delete_recording kind=audio, Long-Run-Verzeichnis ------------
        wav_longrun = _write_wav(orphan_abc_dir, "segment01.wav", {
            "start_iso": now_iso2, "stop_iso": now_iso2, "freq_hz": 1, "mode": "FM",
            "bw_hz": 1, "audio_remote_mode": 1, "sample_rate_hz": 1, "channels": 1,
            "sampwidth_bytes": 2, "frames_written": 1, "duration_s": 0.1, "is_iq": False,
        })
        try:
            delete_recording(store, "audio", "records/longrun_ABC/segment01.wav", repo_root=tmp)
        except RecordingNotDeletableError:
            pass
        else:
            raise AssertionError("RecordingNotDeletableError erwartet (Long-Run-Teil)")
        assert os.path.isfile(wav_longrun)
        print("T21 ok")

        # --- T22: summarize() enthaelt Art "audio" ------------------------------
        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        summary_audio = summarize(rows)
        assert "audio" in summary_audio["je_art"], summary_audio["je_art"]
        print("T22 ok")

        # --- T23: Direkt-Audio, Sidecar running=True, frische WAV (T-1109) -----
        wav_live = _write_wav(record_dir, "audio_live.wav", {
            "start_iso": now_iso2, "stop_iso": None, "freq_hz": 70_000_000,
            "mode": "FM", "bw_hz": 12500, "audio_remote_mode": 6,
            "sample_rate_hz": 16000, "channels": 1, "sampwidth_bytes": 2,
            "frames_written": 1000, "duration_s": 0.0625, "is_iq": False,
            "running": True,
        })
        rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                record_dir="records", mscan_store=mscan_store)
        r_live = next(r for r in rows if r["rec_id"] == "audio:records/audio_live.wav")
        assert r_live["zustand"] == "laeuft", r_live["zustand"]
        assert r_live["valid"] is True, r_live
        assert r_live["end_iso"] is None, r_live
        assert r_live["loesch"]["erlaubt"] is False, r_live["loesch"]
        try:
            delete_recording(store, "audio", "records/audio_live.wav", repo_root=tmp)
        except RecordingLockedError:
            pass
        else:
            raise AssertionError("RecordingLockedError erwartet (running=True, frische WAV)")
        assert os.path.isfile(wav_live)
        print("T23 ok")

        # --- T24: running=True, WAV-mtime kuenstlich > 60s alt (Absturzrest) ---
        old_t = time.time() - 120
        os.utime(wav_live, (old_t, old_t))
        result24 = delete_recording(store, "audio", "records/audio_live.wav", repo_root=tmp)
        assert not os.path.isfile(wav_live)
        assert result24["kind"] == "audio", result24
        print("T24 ok")

        # --- T25: set_recording_label kind=job (Muster /api/joblabel) ----------
        result25 = set_recording_label(store, "job", m_geplant_t15["job_id"], "Neuer Kurztext", repo_root=tmp)
        assert result25["label"] == "Neuer Kurztext", result25
        assert store.get(m_geplant_t15["job_id"])["label"] == "Neuer Kurztext"
        print("T25 ok")

        # --- T26: set_recording_label kind=session ------------------------------
        db_e = os.path.join(session_dir, "session_e.db")
        with open(db_e, "wb") as f:
            f.write(b"dummy")
        result26 = set_recording_label(store, "session", "sessions/session_e.db", "  Session-Kurztext  ", repo_root=tmp)
        assert result26 == {"kind": "session", "label": "Session-Kurztext"}, result26
        assert LabelStore(os.path.join(tmp, "session_labels.json")).get("sessions/session_e.db") == "Session-Kurztext"
        print("T26 ok")

        # --- T27: set_recording_label kind=spectrum ------------------------------
        _write_specbin(record_dir, "label_test.specbin", None)
        result27 = set_recording_label(store, "spectrum", "records/label_test.specbin", "Spektrum-Kurztext", repo_root=tmp)
        assert result27 == {"kind": "spectrum", "label": "Spektrum-Kurztext"}, result27
        assert LabelStore(os.path.join(tmp, "spectrum_labels.json")).get("records/label_test.specbin") == "Spektrum-Kurztext"
        print("T27 ok")

        # --- T28: set_recording_label kind=audio ------------------------------
        wav_e = os.path.join(record_dir, "label_test.wav")
        with open(wav_e, "wb") as f:
            f.write(b"RIFF....WAVEfmt ")
        result28 = set_recording_label(store, "audio", "records/label_test.wav", "Audio-Kurztext", repo_root=tmp)
        assert result28 == {"kind": "audio", "label": "Audio-Kurztext"}, result28
        assert LabelStore(os.path.join(tmp, "audio_labels.json")).get("records/label_test.wav") == "Audio-Kurztext"
        print("T28 ok")

        # --- T29: set_recording_label Sandbox-Verletzung / falsche Endung -------
        try:
            set_recording_label(store, "session", "../ausserhalb.db", "x", repo_root=tmp)
        except RecordingNotDeletableError:
            pass
        else:
            raise AssertionError("RecordingNotDeletableError erwartet (Sandbox)")
        try:
            set_recording_label(store, "spectrum", "records/label_test.specbinx", "x", repo_root=tmp)
        except RecordingNotDeletableError:
            pass
        else:
            raise AssertionError("RecordingNotDeletableError erwartet (falsche Endung)")
        print("T29 ok")

        # --- T30: set_recording_label kind=orphan ---------------------------------
        try:
            set_recording_label(store, "orphan", "records/longrun_XYZ", "x", repo_root=tmp)
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet (orphan hat keinen Kurztext)")
        print("T30 ok")

        # --- R1: _invalid_*_row liefern bei fehlendem Sidecar abort_code/abort_reason (T-1802c1) ---
        for bauer, kwargs in (
            (_invalid_session_row, dict(rel="x", name="x", full_path="x")),
            (_invalid_spectrum_row, dict(rel="x", name="x", full_path="x", root_abs=tmp, repo_root=tmp)),
            (_invalid_audio_row, dict(rel="x", name="x", full_path="x", root_abs=tmp, repo_root=tmp)),
        ):
            row = bauer(**kwargs, grund_code=ERR_NO_SIDECAR, grund_detail=None)
            assert row["abort_code"] == ERR_NO_SIDECAR, (bauer.__name__, row)
            assert row["abort_reason"] is None, (bauer.__name__, row)
        print("R1 ok")

        # --- R2: RecordingLockedError traegt code und params, str(exc) ist der Code (T-1802c1) ---
        exc_r2 = RecordingLockedError(ERR_MAY_BE_RUNNING, kind="session")
        assert exc_r2.code == ERR_MAY_BE_RUNNING, exc_r2.code
        assert exc_r2.params == {"kind": "session"}, exc_r2.params
        assert str(exc_r2) == ERR_MAY_BE_RUNNING, str(exc_r2)
        print("R2 ok")

        # --- T31/T32: externer storage_root (Nachtrag T-201) --------------------
        # extern liegt AUSSERHALB von tmp/repo_root - vorher fuer Direkt-Spektrum/
        # -Audio unerreichbar (nur record_dir wurde durchsucht) und fuer Loeschen
        # sogar dann noch abgelehnt worden, wenn er in storage_roots.json stand
        # (_resolve_within liess bis dahin nur repo_root selbst zu). fremd bleibt
        # bewusst NICHT konfiguriert (Gegenprobe: ausserhalb aller Roots).
        with tempfile.TemporaryDirectory() as extern, tempfile.TemporaryDirectory() as fremd:
            extern_norm = os.path.normpath(extern)
            extern_portable = extern_norm.replace(os.sep, "/")
            # extern zweimal eingetragen - Dedup-Pruefung (T31 erwartet trotzdem
            # genau EINE Zeile je Datei); record_dir bleibt zusaetzlich konfiguriert.
            with open(roots_path, "w", encoding="utf-8") as f:
                json.dump([record_dir, extern_norm, extern_norm], f)

            wav_x = _write_wav(extern_norm, "extern.wav",
                               {"running": False, "freq_hz": 100_000_000, "duration_s": 1.0})
            _write_specbin(extern_norm, "extern.specbin", {
                "format": "eb200-ifpan-bins-v1", "start_iso": now_iso, "stop_iso": now_iso,
                "center_hz": 100_000_000, "span_hz": 200_000, "bw_hz": 9000, "demod": "AM",
                "frames_written": 7,
            })
            wav_x_rel = wav_x.replace(os.sep, "/")
            spec_x_rel = os.path.join(extern_norm, "extern.specbin").replace(os.sep, "/")

            rows = list_recordings(store, repo_root=tmp, session_dir="sessions",
                                    record_dir="records", mscan_store=mscan_store)
            audio_x_rows = [r for r in rows if r["rec_id"] == f"audio:{wav_x_rel}"]
            spec_x_rows = [r for r in rows if r["rec_id"] == f"spectrum:{spec_x_rel}"]
            assert len(audio_x_rows) == 1, (audio_x_rows, [r["rec_id"] for r in rows])
            assert len(spec_x_rows) == 1, (spec_x_rows, [r["rec_id"] for r in rows])
            assert audio_x_rows[0]["ablage"] == extern_portable, audio_x_rows[0]
            assert spec_x_rows[0]["ablage"] == extern_portable, spec_x_rows[0]
            assert audio_x_rows[0]["loesch"]["ref"] == wav_x_rel, audio_x_rows[0]["loesch"]
            assert spec_x_rows[0]["loesch"]["ref"] == spec_x_rel, spec_x_rows[0]["loesch"]
            print("T31 ok  (externer storage_root gelistet, ablage/ref portabel, "
                  "Root doppelt eingetragen -> keine doppelten Zeilen)")

            # --- T32: delete_recording im externen Root, dann ausserhalb aller Roots
            result32 = delete_recording(store, "audio", wav_x_rel, repo_root=tmp)
            assert set(result32["deleted"]) == {"extern.wav", "extern.wav.json"}, result32
            assert not os.path.isfile(wav_x)
            assert not os.path.isfile(wav_x + ".json")

            fremd_wav = _write_wav(fremd, "fremd.wav", {"running": False})
            try:
                delete_recording(store, "audio", fremd_wav.replace(os.sep, "/"), repo_root=tmp)
            except RecordingNotDeletableError:
                pass
            else:
                raise AssertionError("RecordingNotDeletableError erwartet (ausserhalb aller Roots)")
            print("T32 ok  (delete_recording loescht im externen Root, "
                  "lehnt ausserhalb aller Roots weiterhin ab)")

        # storage_roots.json fuer eventuelle weitere Aufrufe zuruecksetzen.
        with open(roots_path, "w", encoding="utf-8") as f:
            json.dump([record_dir], f)

    print("\n[recording_index] Selbsttest erfolgreich abgeschlossen.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Lese-Aggregator Aufzeichnungen (T-1102)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()
    if args.selftest:
        _self_test()
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
