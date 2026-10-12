#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/job_store.py

Verwaltung der Long-Run-Jobs (WS3, Abschnitt 11.5: "Jobmanagement") - ein
Dauer-Recording ist ein Batch-Job, der vollstaendig serverseitig im
Controller laeuft (Browser ist nur Konfigurator/Zuschauer). Dieses Modul
enthaelt NUR die Verwaltung (Manifeste, Index, Zustaende, Ueberlappungs-
pruefung, Absturz-Erholung) - KEINEN Geraete-/Netzwerkzugriff und KEINE
Einbindung in waterfall.py (das ist ein spaeterer Schritt). Reine Byte-/
JSON-Verarbeitung, daher per Selbsttest ohne Hardware pruefbar. Nur
Python-Standardbibliothek.

Datenmodell:
  - Ein MANIFEST je Job (job.json im Job-Verzeichnis
    <storage_root>/longrun_<job_id>/) - die vollstaendige, autoritative
    Beschreibung eines einzelnen Jobs (Parameter, Zustand, Statistik,
    Dateiliste). Bleibt bei der jeweiligen Ablage, ist also auch ohne
    diesen Store bzw. ohne die Uebersicht interpretierbar (Muster wie die
    Sidecar-JSON bei spectrum_store.py/dscan_store.py).
  - Ein zentraler INDEX (jobs/index.json im Repo) - bewusst REDUNDANT zu
    den Manifesten: job_id -> Kurzfassung (job_dir, state, type, start_iso,
    end_iso, label). Die Jobs-Uebersicht funktioniert damit auch, wenn eine
    Ablage-Platte gerade nicht angeschlossen ist (WS3 11.5). Bei jedem
    Zustandswechsel werden Manifest UND Index aktualisiert; ist das
    Manifest nicht erreichbar, wird NUR der Index aktualisiert und das im
    Rueckgabewert vermerkt ("manifest_unreachable": true) statt eine
    Ausnahme zu werfen - die Uebersicht darf durch eine fehlende Platte
    nicht abstuerzen.

Atomares Schreiben (Vorbild: eb200_core/label_store.py): jede JSON-Datei
(Index UND jedes Manifest) wird per Temp-Datei + os.replace geschrieben -
ein Absturz mitten im Schreiben hinterlaesst nie eine halb geschriebene
Datei. Alle Mutationen sind zusaetzlich per Lock serialisiert (mehrere
HTTP-Handler-Threads koennen gleichzeitig zugreifen, wie bei label_store).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from datetime import datetime

# Erlaubt auch den Direktaufruf "python eb200_core/job_store.py": dort fehlt
# sonst das Repo-Root im Suchpfad fuer den paths-Import (T-2002, Weiche wie
# in eb200_core/path_sandbox.py).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core import paths

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Beide unter DATA_ROOT (T-2002, eb200_core/paths.py; ohne --data-dir das
# Repo-Root wie bisher). path_sandbox.py und recording_index.py lesen
# storage_roots.json als <repo_root>/storage_roots.json; ihre Aufrufer
# uebergeben ab T-2003 DATA_ROOT als repo_root - dann ist es dieselbe Datei.
INDEX_PATH_DEFAULT = paths.data_path("jobs", "index.json")
STORAGE_ROOTS_PATH = paths.data_path("storage_roots.json")
JOB_DIR_PREFIX = "longrun_"
JOB_LABEL_MAX_LEN = 200

# Zustaende (WS3 11.5).
ST_GEPLANT = "geplant"
ST_LAEUFT = "laeuft"
ST_BEENDET = "beendet"
ST_GESTOPPT = "gestoppt"
ST_ABGEBROCHEN = "abgebrochen"
ST_VERPASST = "verpasst"

# Abbruchgruende als sprachneutrale Codes (T-1802b). Der Anzeigetext entsteht im
# Client ueber EB200_LABELS; abort_detail traegt die technische Einzelheit.
ABORT_CODES = ("server_restart", "server_stop", "receiver_busy", "recording_error",
               "antenna_unknown", "program_failed", "udp_bind_failed", "scpi_error",
               "bin_record_failed", "stop_while_programming", "unexpected_exit",
               "geometry_changed")

# mscan = Speicherkanal-Scan (T-1005); params traegt mscan_job_id (Definition
# aus eb200_core/mscan_job.py) + Kanal-Snapshot.
JOB_TYPES = ("dscan", "ifpan", "mscan")

# Erlaubte Zustandsuebergaenge (siehe set_state()). Jeder nicht gelistete
# Uebergang (inkl. aus einem Endzustand heraus) ist verboten.
_ALLOWED_TRANSITIONS = {
    # ABGEBROCHEN aus GEPLANT = Startfehler (Programmieren, Socket, SCPI),
    # bevor der Lauf je LAEUFT erreichte - abort_reason traegt die Ursache.
    ST_GEPLANT: frozenset({ST_LAEUFT, ST_GESTOPPT, ST_VERPASST, ST_ABGEBROCHEN}),
    ST_LAEUFT: frozenset({ST_BEENDET, ST_GESTOPPT, ST_ABGEBROCHEN}),
}


# ---------------------------------------------------------------------------
# Freie Hilfsfunktionen (kein Zustand, daher ausserhalb der Klasse)
# ---------------------------------------------------------------------------

def _clean_job_label(text) -> str:
    """Normalisiert einen Job-Kurztext (Muster label_store.clean_label):
    zu str zwingen, Steuerzeichen (inkl. Zeilenumbruch/Tab/DEL) durch
    Leerzeichen ersetzen, Rand-Leerzeichen entfernen, auf JOB_LABEL_MAX_LEN
    Zeichen kuerzen. Leerer (bereinigter) Text loescht die Beschriftung."""
    s = "" if text is None else str(text)
    s = "".join(ch if (ch >= " " and ch != "\x7f") else " " for ch in s)
    return s.strip()[:JOB_LABEL_MAX_LEN]


def _now_iso() -> str:
    """Aktuelle Zeit als ISO-String in lokaler Zeit MIT Offset (nicht UTC) -
    fuer Menschen direkt lesbar in Manifest/Index, wie in der Aufgabe
    vorgegeben."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    """Parst einen ISO-String (inkl. Offset) zu einem timezone-aware
    datetime. Wird fuer Vergleiche gebraucht (Ueberlappung, Vergangenheit/
    Zukunft) - ein reiner String-Vergleich waere bei unterschiedlichen
    UTC-Offsets (z. B. Sommer-/Winterzeit) NICHT zuverlaessig."""
    return datetime.fromisoformat(value)


def _windows_overlap(a_start: str, a_end, b_start: str, b_end) -> bool:
    """Zwei Zeitfenster [a_start, a_end) und [b_start, b_end) ueberlappen,
    wenn a_start < b_end UND b_start < a_end. end=None wirkt als
    "unendlich" (WS3 11.5: ein offen laufender Job - "bis Stopp" -
    kollidiert mit allem, was ab seinem Start beginnt)."""
    a_s = _parse_iso(a_start)
    b_s = _parse_iso(b_start)
    a_e = _parse_iso(a_end) if a_end is not None else datetime.max.replace(tzinfo=a_s.tzinfo)
    b_e = _parse_iso(b_end) if b_end is not None else datetime.max.replace(tzinfo=b_s.tzinfo)
    return a_s < b_e and b_s < a_e


def _atomic_write_json(path: str, data) -> None:
    """Schreibt data als JSON atomar nach path (Temp-Datei + os.replace,
    Muster label_store.py._flush()). Legt das Zielverzeichnis NICHT an -
    das ist Sache des Aufrufers (der es i. d. R. ohnehin schon braucht,
    um dorthin zu schreiben)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _manifest_path(job_dir: str) -> str:
    return os.path.join(job_dir, "job.json")


def _load_manifest(job_dir: str):
    """Laedt das Manifest aus job_dir/job.json. Liefert None (statt eine
    Ausnahme zu werfen), wenn die Datei fehlt, unlesbar oder kaputtes JSON
    ist - typischerweise, weil die Ablage-Platte gerade nicht angeschlossen
    ist. Aufrufer muessen None behandeln (siehe get()/set_state()/
    heartbeat(): manifest_unreachable)."""
    try:
        with open(_manifest_path(job_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _try_write_manifest(manifest: dict) -> bool:
    """Schreibt das Manifest atomar zurueck. Liefert False statt eine
    Ausnahme zu werfen, wenn das Job-Verzeichnis/die Ablage gerade nicht
    erreichbar ist (siehe _load_manifest())."""
    try:
        _atomic_write_json(_manifest_path(manifest["job_dir"]), manifest)
        return True
    except OSError:
        return False


def _index_entry(manifest: dict) -> dict:
    """Extrahiert die redundante Kurzfassung eines Manifests fuer den
    zentralen Index (siehe Moduldoku)."""
    return {
        "job_dir": manifest["job_dir"],
        "state": manifest["state"],
        "type": manifest["type"],
        "start_iso": manifest["start_iso"],
        "end_iso": manifest["end_iso"],
        "label": manifest["label"],
    }


def load_storage_roots(path: str = STORAGE_ROOTS_PATH) -> list:
    """
    Liest path (JSON-Liste absoluter oder repo-relativer Pfade), die
    festlegt, wohin Long-Run-Jobs schreiben duerfen. Fehlt die Datei, ist
    sie nicht lesbar/kein gueltiges JSON oder keine nicht-leere Liste von
    nicht-leeren Strings, gilt der Default ["records"].

    Sicherheitsrelevanz (WS3 11.6): diese Liste soll spaeter zusaetzlich
    die Sandbox-Whitelist der Auswerte-Server (:8020/:8040,
    resolve_safe_path) erweitern - das ist ein SPAETERER Schritt (WS3 11.7,
    Punkt 7) und NICHT Teil dieses Moduls. Hier wird nur GELESEN, wohin
    geschrieben werden darf; storage_roots.json selbst wird von diesem
    Modul nie geschrieben (Pflege ist Sache eines spaeteren Verwaltungs-UI
    bzw. manuell).
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return ["records"]
    if isinstance(raw, list) and raw and all(isinstance(p, str) and p for p in raw):
        return raw
    return ["records"]


def _root_abs(entry: str) -> str:
    """Absoluter Pfad eines storage_roots-Eintrags (T-2002): absolute Eintraege
    bleiben, relative gelten gegen DATA_ROOT statt gegen das
    Arbeitsverzeichnis - dieselbe Regel wie path_sandbox.storage_root_abs()
    mit repo_root=DATA_ROOT."""
    return os.path.abspath(os.path.join(paths.DATA_ROOT, entry))


# ---------------------------------------------------------------------------
# JobStore
# ---------------------------------------------------------------------------

class JobStore:
    """
    Verwaltet Long-Run-Jobs: Anlage (mit Ueberlappungspruefung),
    Zustandswechsel, Lebenszeichen (Heartbeat), Loeschung geplanter Jobs
    und Absturz-Erholung beim Serverstart. Eine Instanz pro Server-Prozess
    (Muster: LabelStore) - alle Methoden sind threadsicher.
    """

    def __init__(self, index_path: str = INDEX_PATH_DEFAULT,
                 storage_roots_path: str = STORAGE_ROOTS_PATH) -> None:
        self._index_path = index_path
        self._storage_roots_path = storage_roots_path
        dir_ = os.path.dirname(index_path)
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        # RLock statt Lock: check_overlap() wird sowohl direkt aufgerufen
        # als auch INTERN aus create_job() heraus, waehrend dieses den Lock
        # bereits haelt - ein einfacher Lock wuerde dabei deadlocken.
        self._lock = threading.RLock()
        self._index: dict = {}
        self._load_index()

    # -- Index-Persistenz ----------------------------------------------

    def _load_index(self) -> None:
        try:
            with open(self._index_path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        self._index = raw if isinstance(raw, dict) else {}

    def _reload_index(self) -> None:
        """
        Liest den Index frisch von der Platte (identische Logik wie
        _load_index(), aber mit eigenem Namen, um den Zweck an der
        Aufrufstelle klar zu machen). MUSS unter dem bestehenden Lock
        aufgerufen werden - kein eigenes Locking hier.

        Grund: mehrere Prozesse (waterfall.py auf :8000, manage_jobs.py auf
        :8050) halten je eine EIGENE JobStore-Instanz auf DERSELBEN
        Index-Datei. Schriebe eine Instanz ihren nur im Konstruktor
        geladenen self._index unveraendert zurueck, wuerden Aenderungen der
        JEWEILS ANDEREN Instanz seit deren letztem Schreiben verloren gehen
        (klassisches Read-Modify-Write-/Lost-Update-Problem). Aufruf am
        Anfang JEDER mutierenden Methode (create_job/set_state/heartbeat/
        delete/recover_on_startup) UND in den Lesemethoden list_jobs()/
        get() - damit auch ein reiner Leser (z. B. die Jobs-Seite beim
        Neuladen) immer den aktuellen Stand sieht, nicht nur den vom
        eigenen Prozessstart.

        Verbleibende Race-Luecke (bewusst akzeptiert): zwischen diesem
        _reload_index() und dem abschliessenden _flush_index_locked()
        derselben Methode liegt kein Datei-Lock auf Betriebssystemebene -
        zwei Prozesse koennten theoretisch im selben Sekundenbruchteil je
        lesen-aendern-schreiben und sich gegenseitig ueberschreiben. Fuer
        eine Einbenutzer-Station (ein Controller, eine Handvoll kurzer
        Jobs-Seiten-Requests, keine hochfrequente Nebenlaeufigkeit) ist das
        hinnehmbar; ein echtes prozessuebergreifendes Datei-Lock waere hier
        Overkill.
        """
        self._load_index()

    def _flush_index_locked(self) -> None:
        """Schreibt den Index atomar. Nennt sich '_locked', weil der
        Aufrufer den Lock bereits halten MUSS (kein eigenes Locking hier -
        vermeidet doppeltes Schreiben bei mehreren Aenderungen in einer
        Transaktion, z. B. in recover_on_startup())."""
        _atomic_write_json(self._index_path, self._index)

    def _alloc_job_id_locked(self) -> str:
        """Neue job_id als YYYYMMDD_HHMMSS; bei Kollision (zwei Anlagen in
        derselben Sekunde) Suffix _2, _3, ... Muss unter Lock aufgerufen
        werden (liest self._index)."""
        base = datetime.now().strftime("%Y%m%d_%H%M%S")
        if base not in self._index:
            return base
        n = 2
        while f"{base}_{n}" in self._index:
            n += 1
        return f"{base}_{n}"

    # -- Anlage -----------------------------------------------------------

    def create_job(self, *, type: str, label: str, params: dict, start_iso: str,
                    end_iso, decimation_s=None, rotation_min: int = 60,
                    storage_root: str | None = None) -> dict:
        """
        Legt einen neuen Long-Run-Job an: validiert Typ/Dezimierung/
        Zeitfenster/Ablage, prueft auf Ueberlappung mit bereits geplanten/
        laufenden Jobs (WS3 11.5: "ein Empfaenger, ein Job" - Konflikte
        werden mit Klartext verweigert, keine Warteschlange), legt das
        Job-Verzeichnis samt Manifest an (Zustand ST_GEPLANT) und traegt
        den Job in den Index ein.

        params wird OPAK behandelt (Inhalt ist Sache des Controllers -
        dscan: start_hz/stop_hz/speed/...; ifpan: center_hz/span_hz/
        demod/bw_hz/audio/...). decimation_s ist NUR fuer type="dscan"
        erlaubt - IFPAN-Long-Runs laufen bewusst OHNE Dezimierung
        (WS3 11.2), ein gesetztes decimation_s bei type="ifpan" ist ein
        Aufrufer-Fehler und wirft ValueError.

        Rueckgabe: das vollstaendige, neu angelegte Manifest-dict.
        """
        if type not in JOB_TYPES:
            raise ValueError(f"unknown job type {type!r}, expected one of {JOB_TYPES}")
        if type in ("ifpan", "mscan") and decimation_s is not None:
            raise ValueError(
                "IFPAN/MSCAN long runs run WITHOUT decimation (WS3 11.2) - "
                "decimation_s must be None"
            )
        if end_iso is not None and _parse_iso(end_iso) <= _parse_iso(start_iso):
            raise ValueError(f"end_iso ({end_iso}) must be after start_iso ({start_iso})")

        roots = load_storage_roots(self._storage_roots_path)
        if storage_root is None:
            storage_root = roots[0]
        elif storage_root not in roots:
            raise ValueError(
                f"storage_root {storage_root!r} is not one of the configured "
                f"storage locations {roots}"
            )

        with self._lock:
            self._reload_index()
            colliding = self.check_overlap(start_iso, end_iso)
            if colliding:
                first_id = colliding[0]
                other = self._index.get(first_id, {})
                raise ValueError(
                    f"Zeitfenster kollidiert mit Job {first_id} "
                    f"({other.get('label', '')!r}, {other.get('start_iso')}.."
                    f"{other.get('end_iso')}) - ein Empfaenger, ein Job (WS3 11.5)"
                )

            job_id = self._alloc_job_id_locked()
            job_dir = os.path.join(_root_abs(storage_root), JOB_DIR_PREFIX + job_id)
            os.makedirs(job_dir, exist_ok=True)

            now = _now_iso()
            manifest = {
                "job_id": job_id,
                "type": type,
                "label": label or "",
                "params": params,
                "decimation_s": decimation_s,
                "rotation_min": rotation_min,
                "start_iso": start_iso,
                "end_iso": end_iso,
                "state": ST_GEPLANT,
                "storage_root": storage_root,
                "job_dir": job_dir,
                "files": [],
                "stats": {},
                "heartbeat_iso": None,
                "abort_reason": None,
                "abort_code": None,
                "abort_detail": None,
                "created_iso": now,
                "ended_iso": None,
            }
            _atomic_write_json(_manifest_path(job_dir), manifest)
            self._index[job_id] = _index_entry(manifest)
            self._flush_index_locked()

        return manifest

    def check_overlap(self, start_iso: str, end_iso, ignore_job_id: str | None = None) -> list:
        """
        Liefert die job_ids aller Jobs in Zustand ST_GEPLANT/ST_LAEUFT,
        deren Zeitfenster das gegebene [start_iso, end_iso) ueberlappt
        (siehe _windows_overlap()). ignore_job_id schliesst einen Job von
        der Pruefung aus. Leere Liste = das Fenster ist frei."""
        with self._lock:
            result = []
            for job_id, entry in self._index.items():
                if job_id == ignore_job_id:
                    continue
                if entry.get("state") not in (ST_GEPLANT, ST_LAEUFT):
                    continue
                if _windows_overlap(start_iso, end_iso, entry.get("start_iso"), entry.get("end_iso")):
                    result.append(job_id)
            return result

    # -- Abfrage ------------------------------------------------------------

    def storage_roots(self) -> list:
        """Konfigurierte Storage-Roots als absolute, per os.path.realpath
        aufgeloeste Pfade - fuer die Lese-Sandbox der Ergebnisansicht
        (manage_jobs.py, T-1006): dieselbe Wurzelmenge, gegen die
        delete_with_data() bereits prueft (siehe dortigen _unter()-Helfer,
        der bleibt unveraendert). Nur lesen, nie schreiben."""
        return [os.path.realpath(_root_abs(r)) for r in load_storage_roots(self._storage_roots_path)]

    def get(self, job_id: str) -> dict:
        """Manifest zu job_id - bevorzugt frisch aus der Manifest-Datei
        gelesen. Ist die Datei nicht erreichbar (Ablage weg), liefert
        stattdessen die Index-Kurzfassung mit "manifest_unreachable": true
        (siehe Moduldoku) statt einer Ausnahme. Wirft KeyError, wenn
        job_id ueberhaupt nicht im Index steht."""
        with self._lock:
            self._reload_index()
            entry = self._index.get(job_id)
            if entry is None:
                raise KeyError(f"unknown job_id {job_id!r}")
            entry_copy = dict(entry)

        manifest = _load_manifest(entry_copy["job_dir"])
        if manifest is not None:
            return manifest
        result = entry_copy
        result["job_id"] = job_id
        result["manifest_unreachable"] = True
        return result

    def list_jobs(self) -> list:
        """Alle Index-Eintraege (Kurzfassung, kein Manifest-Zugriff -
        schnell, funktioniert auch bei nicht angeschlossenen Ablagen),
        sortiert nach start_iso (chronologisch, nicht als String - siehe
        _parse_iso())."""
        with self._lock:
            self._reload_index()
            items = [dict(entry, job_id=job_id) for job_id, entry in self._index.items()]
        items.sort(key=lambda e: _parse_iso(e["start_iso"]))
        return items

    # -- Zustandswechsel ------------------------------------------------

    def set_state(self, job_id: str, new_state: str, *, abort_reason: str | None = None,
                  abort_code: str | None = None, abort_detail: str | None = None) -> dict:
        """
        Wechselt den Zustand von job_id (siehe _ALLOWED_TRANSITIONS fuer
        die erlaubten Uebergaenge, WS3 11.5). Jeder andere Uebergang wirft
        ValueError. Bei jedem Uebergang AUSSER nach ST_LAEUFT wird
        ended_iso gesetzt (alle anderen Zielzustaende sind Endzustaende);
        bei ST_ABGEBROCHEN/ST_VERPASST zusaetzlich abort_reason, abort_code und
        abort_detail. abort_code ist ein sprachneutraler Code aus ABORT_CODES, dessen
        Anzeigetext im Client entsteht (Regel 2 und 3, Entscheidungslog 2026-08-31);
        abort_detail traegt die technische Einzelheit und bleibt unuebersetzt.
        abort_reason ist der alte deutsche Freitext - er bleibt im Schema, damit
        bestehende Manifeste lesbar bleiben, und wird von neuen Abbruechen nicht mehr
        gesetzt.

        Aktualisiert Manifest UND Index. Ist das Manifest gerade nicht
        erreichbar, wird NUR der Index aktualisiert - der Rueckgabewert
        traegt "manifest_unreachable": true, es wird KEINE Ausnahme
        geworfen (die Uebersicht muss auch ohne die jeweilige Platte
        funktionieren)."""
        with self._lock:
            self._reload_index()
            entry = self._index.get(job_id)
            if entry is None:
                raise KeyError(f"unknown job_id {job_id!r}")
            current = entry["state"]
            allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
            if new_state not in allowed:
                raise ValueError(
                    f"state change {current!r} -> {new_state!r} for job "
                    f"{job_id} not allowed (allowed: {sorted(allowed)})"
                )

            manifest = _load_manifest(entry["job_dir"])
            manifest_unreachable = manifest is None
            if manifest is not None:
                manifest["state"] = new_state
                if new_state != ST_LAEUFT:
                    manifest["ended_iso"] = _now_iso()
                if new_state in (ST_ABGEBROCHEN, ST_VERPASST):
                    manifest["abort_reason"] = abort_reason
                    manifest["abort_code"] = abort_code
                    manifest["abort_detail"] = abort_detail
                manifest_unreachable = not _try_write_manifest(manifest)

            entry["state"] = new_state
            self._flush_index_locked()

            result = dict(manifest) if manifest is not None else dict(entry, job_id=job_id)
            result["manifest_unreachable"] = manifest_unreachable
            return result

    def heartbeat(self, job_id: str, stats: dict | None = None, files: list | None = None,
                  results=None) -> dict:
        """
        Lebenszeichen fuer den 5-Minuten-Takt des Job-Controllers (WS3
        10.3, analog zu DscanMaxholdWriter.checkpoint()): setzt
        heartbeat_iso auf jetzt und aktualisiert optional stats/files/
        results im Manifest (der Index traegt kein heartbeat_iso - reine
        Manifest-Information). results ist die OBERSTE Manifest-Ebene
        (manifest["results"]), NICHT Teil von stats - fuer den
        Ergebnis-Index eines MSCAN-Laufs (T-1005, siehe
        eb200_core/mscan_run.py MscanSegmenter.results()/
        MscanJobRecorder.close()); andere Job-Typen nutzen dieses Feld
        nicht. Wie set_state() NIE eine Ausnahme bei nicht erreichbarem
        Manifest, sondern manifest_unreachable=true im Rueckgabewert."""
        with self._lock:
            self._reload_index()
            entry = self._index.get(job_id)
            if entry is None:
                raise KeyError(f"unknown job_id {job_id!r}")

            manifest = _load_manifest(entry["job_dir"])
            manifest_unreachable = manifest is None
            if manifest is not None:
                manifest["heartbeat_iso"] = _now_iso()
                if stats is not None:
                    manifest["stats"] = stats
                if files is not None:
                    manifest["files"] = files
                if results is not None:
                    manifest["results"] = results
                manifest_unreachable = not _try_write_manifest(manifest)

            result = dict(manifest) if manifest is not None else dict(entry, job_id=job_id)
            result["manifest_unreachable"] = manifest_unreachable
            return result

    def update_label(self, job_id: str, label) -> dict:
        """
        Aendert NUR den Kurztext (label) eines Jobs - anders als
        set_state() OHNE Zustandslogik: eine reine Beschriftung ist in
        JEDEM Zustand aenderbar (geplant/laeuft/beendet/...). label wird
        normalisiert (siehe _clean_job_label()); ein leerer (bereinigter)
        Text loescht die Beschriftung.

        Aktualisiert Manifest UND Index (Muster set_state()/heartbeat()).
        Ist das Manifest gerade nicht erreichbar, wird NUR der Index
        aktualisiert - der Rueckgabewert traegt "manifest_unreachable":
        true statt einer Ausnahme.
        """
        cleaned = _clean_job_label(label)
        with self._lock:
            self._reload_index()
            entry = self._index.get(job_id)
            if entry is None:
                raise KeyError(f"unknown job_id {job_id!r}")

            manifest = _load_manifest(entry["job_dir"])
            manifest_unreachable = manifest is None
            if manifest is not None:
                manifest["label"] = cleaned
                manifest_unreachable = not _try_write_manifest(manifest)

            entry["label"] = cleaned
            self._flush_index_locked()

            result = dict(manifest) if manifest is not None else dict(entry, job_id=job_id)
            result["manifest_unreachable"] = manifest_unreachable
            return result

    # -- Loeschen -------------------------------------------------------

    def delete(self, job_id: str) -> None:
        """
        Loescht einen Job vollstaendig (Index-Eintrag + Job-Verzeichnis) -
        NUR erlaubt in ST_GEPLANT/ST_VERPASST (WS3 11.5: geplante Jobs
        sind bis zur Startzeit editier-/loeschbar). Verweigert mit
        ValueError, wenn das Job-Verzeichnis ausser dem Manifest (job.json)
        noch andere Dateien enthaelt - Messdaten werden NIE
        stillschweigend geloescht."""
        with self._lock:
            self._reload_index()
            entry = self._index.get(job_id)
            if entry is None:
                raise KeyError(f"unknown job_id {job_id!r}")
            if entry["state"] not in (ST_GEPLANT, ST_VERPASST):
                raise ValueError(
                    f"job {job_id} is in state {entry['state']!r} - can only be deleted "
                    f"in {ST_GEPLANT!r}/{ST_VERPASST!r}"
                )

            job_dir = entry["job_dir"]
            if os.path.isdir(job_dir):
                other_files = [n for n in os.listdir(job_dir) if n != "job.json"]
                if other_files:
                    raise ValueError(
                        f"job directory {job_dir} contains, besides job.json, "
                        f"{other_files} - NOT deleted (protecting measurement data)"
                    )
                manifest_path = _manifest_path(job_dir)
                if os.path.isfile(manifest_path):
                    os.remove(manifest_path)
                os.rmdir(job_dir)

            del self._index[job_id]
            self._flush_index_locked()

    def delete_with_data(self, job_id: str) -> dict:
        """
        Loescht einen ABGESCHLOSSENEN Job ENDGUELTIG samt aller Dateien im
        Job-Verzeichnis (Aufzeichnungsteile, Sidecars, WAVs, Manifest) -
        das "inkl. Siblings"-Loeschen der Jobs-Uebersicht (:8050).

        Bewusste Abgrenzung zu delete(): delete() schuetzt Messdaten und
        gilt nur fuer geplante/verpasste Jobs; delete_with_data() ist die
        EXPLIZITE Nutzerentscheidung, Messdaten zu verwerfen. Erlaubt nur
        in den Endzustaenden beendet/gestoppt/abgebrochen - nie fuer
        laufende oder geplante Jobs. Sicherheitsnetz: geloescht wird
        ausschliesslich, wenn das Job-Verzeichnis unterhalb einer
        konfigurierten Storage-Root liegt UND der Verzeichnisname mit
        longrun_ beginnt - ein korruptes Manifest kann so nie fremde
        Verzeichnisse treffen.

        Rueckgabe: {"status": "ok", "deleted_files": n} - n zaehlt
        die entfernten Dateien (rekursiv, ohne Verzeichnisse).
        """
        with self._lock:
            self._reload_index()
            entry = self._index.get(job_id)
            if entry is None:
                raise KeyError(f"unknown job_id {job_id!r}")
            if entry["state"] not in (ST_BEENDET, ST_GESTOPPT, ST_ABGEBROCHEN):
                raise ValueError(
                    f"job {job_id} is in state {entry['state']!r} - can only be deleted "
                    f"permanently in {ST_BEENDET!r}/{ST_GESTOPPT!r}/{ST_ABGEBROCHEN!r}"
                )

            job_dir = os.path.abspath(entry["job_dir"])
            roots = [_root_abs(r)
                     for r in load_storage_roots(self._storage_roots_path)]

            def _unter(root: str) -> bool:
                # commonpath wirft ValueError bei Pfaden auf verschiedenen
                # Laufwerken (Windows) - das ist dann schlicht "nicht darunter".
                try:
                    return os.path.commonpath([job_dir, root]) == root
                except ValueError:
                    return False

            if (not any(_unter(r) for r in roots)
                    or not os.path.basename(job_dir).startswith(JOB_DIR_PREFIX)):
                raise ValueError(
                    f"job directory {job_dir} is not under a "
                    f"storage root or is not a {JOB_DIR_PREFIX}* directory "
                    f"- NOT deleted"
                )

            n = 0
            if os.path.isdir(job_dir):
                for _wurzel, _dirs, dateien in os.walk(job_dir):
                    n += len(dateien)
                shutil.rmtree(job_dir)

            del self._index[job_id]
            self._flush_index_locked()
            return {"status": "ok", "deleted_files": n}

    # -- Absturz-Erholung -------------------------------------------------

    def recover_on_startup(self) -> dict:
        """
        Fuer den Serverstart (WS3 11.5): raeumt verwaisten Zustand auf, den
        ein Absturz/Neustart hinterlassen haben kann.
          - Jeder Job in ST_LAEUFT -> ST_ABGEBROCHEN. abort_reason nennt
            den letzten bekannten Heartbeat; ended_iso wird auf diesen
            Heartbeat gesetzt (falls vorhanden, sonst auf jetzt) - der Job
            endete spaetestens dort, auch wenn niemand mehr zusah.
          - Jeder Job in ST_GEPLANT mit start_iso in der Vergangenheit ->
            ST_VERPASST. KEIN stilles Nachholen - der Job bleibt bis zu
            einer expliziten Nutzerentscheidung (Loeschen oder "jetzt
            nachholen", spaetere UI) im Zustand verpasst stehen.

        Rueckgabe: {"abgebrochen": [job_id, ...], "verpasst": [job_id, ...]}.
        """
        with self._lock:
            self._reload_index()
            now = datetime.now().astimezone()
            abgebrochen = []
            verpasst = []
            for job_id, entry in self._index.items():
                if entry["state"] == ST_LAEUFT:
                    manifest = _load_manifest(entry["job_dir"])
                    heartbeat_iso = manifest.get("heartbeat_iso") if manifest else None
                    if manifest is not None:
                        manifest["state"] = ST_ABGEBROCHEN
                        manifest["abort_code"] = "server_restart"
                        manifest["abort_detail"] = heartbeat_iso or None
                        manifest["ended_iso"] = heartbeat_iso or _now_iso()
                        _try_write_manifest(manifest)
                    entry["state"] = ST_ABGEBROCHEN
                    abgebrochen.append(job_id)
                elif entry["state"] == ST_GEPLANT:
                    try:
                        start = _parse_iso(entry["start_iso"])
                    except (TypeError, ValueError):
                        continue
                    if start < now:
                        manifest = _load_manifest(entry["job_dir"])
                        if manifest is not None:
                            manifest["state"] = ST_VERPASST
                            _try_write_manifest(manifest)
                        entry["state"] = ST_VERPASST
                        verpasst.append(job_id)

            if abgebrochen or verpasst:
                self._flush_index_locked()

            return {"abgebrochen": abgebrochen, "verpasst": verpasst}


# ---------------------------------------------------------------------------
# Selbsttest - "python job_store.py" direkt ausfuehren, ohne Geraet/Hardware
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        index_path = os.path.join(tmp, "jobs", "index.json")
        roots_path = os.path.join(tmp, "storage_roots.json")
        records_dir = os.path.join(tmp, "records")
        os.makedirs(records_dir, exist_ok=True)
        with open(roots_path, "w", encoding="utf-8") as f:
            json.dump([records_dir], f)

        store = JobStore(index_path=index_path, storage_roots_path=roots_path)

        # --- a) create_job dscan mit decimation_s -----------------------
        m1 = store.create_job(
            type="dscan", label="Testlauf A",
            params={"start_hz": 88_000_000, "stop_hz": 108_000_000, "speed": "NORM"},
            start_iso="2020-01-15T10:00:00+01:00", end_iso="2020-01-15T12:00:00+01:00",
            decimation_s=15,
        )
        assert m1["state"] == ST_GEPLANT, m1["state"]
        assert os.path.isdir(m1["job_dir"]), m1["job_dir"]
        assert os.path.isfile(os.path.join(m1["job_dir"], "job.json"))
        with open(index_path, encoding="utf-8") as f:
            idx = json.load(f)
        assert m1["job_id"] in idx, idx
        print(f"OK  create_job dscan: job_id={m1['job_id']}, state={m1['state']}, "
              f"job_dir + Index-Eintrag vorhanden")

        # --- b) create_job ifpan mit decimation_s -> ValueError (11.2) --
        try:
            store.create_job(
                type="ifpan", label="X", params={},
                start_iso="2020-01-16T10:00:00+01:00", end_iso="2020-01-16T11:00:00+01:00",
                decimation_s=5,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: IFPAN mit decimation_s")
        print("OK  IFPAN mit decimation_s -> ValueError (WS3 11.2)")

        # --- c) Ueberlappungspruefung -------------------------------------
        try:
            store.create_job(
                type="ifpan", label="Kollision", params={},
                start_iso="2020-01-15T11:00:00+01:00", end_iso="2020-01-15T13:00:00+01:00",
            )
        except ValueError as exc:
            assert m1["job_id"] in str(exc), str(exc)
        else:
            raise AssertionError("ValueError erwartet: ueberlappendes Zeitfenster")
        print("OK  ueberlappendes Zeitfenster -> ValueError mit job_id des Kollisionsjobs")

        m2 = store.create_job(
            type="ifpan", label="Testlauf B", params={"center_hz": 100_000_000},
            start_iso="2020-01-15T13:00:00+01:00", end_iso="2020-01-15T14:00:00+01:00",
        )
        assert m2["state"] == ST_GEPLANT
        print(f"OK  disjunktes Zeitfenster -> job_id={m2['job_id']} angelegt")

        # end_iso=None wirkt als "unendlich" und kollidiert mit ALLEM ab
        # seinem Start - eigener Wegwerf-Job, sofort danach wieder
        # geloescht, damit er spaetere Tests (insbes. den Zukunftsjob in
        # Teil f) nicht dauerhaft blockiert.
        m_open = store.create_job(
            type="dscan", label="Offenes Ende", params={},
            start_iso="2020-01-15T15:00:00+01:00", end_iso=None, decimation_s=30,
        )
        try:
            store.create_job(
                type="ifpan", label="Nach offenem Ende", params={},
                start_iso="2020-01-15T20:00:00+01:00", end_iso="2020-01-15T21:00:00+01:00",
            )
        except ValueError as exc:
            assert m_open["job_id"] in str(exc), str(exc)
        else:
            raise AssertionError("ValueError erwartet: Kollision mit end_iso=None-Job")
        print("OK  end_iso=None kollidiert mit spaeterem Job (wirkt als unendlich)")
        store.delete(m_open["job_id"])

        # regulaerer, geschlossener Job in der Vergangenheit - wird in
        # Teil f) fuer den "verpasst"-Fall von recover_on_startup gebraucht.
        m3 = store.create_job(
            type="dscan", label="Verpasster Job", params={},
            start_iso="2020-01-15T15:00:00+01:00", end_iso="2020-01-15T16:00:00+01:00",
            decimation_s=30,
        )

        # --- d) Zustandsmaschine ------------------------------------------
        store.set_state(m2["job_id"], ST_LAEUFT)
        store.set_state(m2["job_id"], ST_BEENDET)
        got2 = store.get(m2["job_id"])
        assert got2["state"] == ST_BEENDET, got2["state"]
        assert got2["ended_iso"] is not None
        try:
            store.set_state(m2["job_id"], ST_LAEUFT)
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: beendet -> laeuft")
        print("OK  Zustandsmaschine: geplant->laeuft->beendet ok, beendet->laeuft -> ValueError")

        # --- d2) Startfehler: geplant -> abgebrochen (T-1005 B Nachtrag 2) -
        # ein MSCAN-Lauf, der z. B. beim Programmieren scheitert, erreicht
        # nie ST_LAEUFT - siehe _ALLOWED_TRANSITIONS.
        m_start_err = store.create_job(
            type="mscan", label="Startfehler-Test", params={"mscan_job_id": "ABCDEF"},
            start_iso="2020-01-17T10:00:00+01:00", end_iso="2020-01-17T11:00:00+01:00",
        )
        result_err = store.set_state(m_start_err["job_id"], ST_ABGEBROCHEN,
                                      abort_reason="Startfehler: Testgrund")
        assert result_err["state"] == ST_ABGEBROCHEN, result_err
        assert result_err["abort_reason"] == "Startfehler: Testgrund", result_err
        assert result_err["ended_iso"] is not None, result_err
        m_beendet_test = store.create_job(
            type="dscan", label="Geplant->Beendet-Test", params={},
            start_iso="2020-01-18T10:00:00+01:00", end_iso="2020-01-18T11:00:00+01:00",
            decimation_s=10,
        )
        try:
            store.set_state(m_beendet_test["job_id"], ST_BEENDET)
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: geplant -> beendet")
        print("OK  Startfehler: geplant->abgebrochen (mit abort_reason) erlaubt, "
              "geplant->beendet weiterhin ValueError")

        # --- e) heartbeat ---------------------------------------------------
        store.set_state(m1["job_id"], ST_LAEUFT)
        hb = store.heartbeat(m1["job_id"], stats={"frames": 42}, files=["a.specbin"])
        assert hb["heartbeat_iso"] is not None
        assert hb["stats"] == {"frames": 42}, hb["stats"]
        assert hb["files"] == ["a.specbin"], hb["files"]
        print(f"OK  heartbeat: heartbeat_iso={hb['heartbeat_iso']}, stats={hb['stats']}")

        # --- e2) heartbeat(results=...) - T-1005: oberste Manifest-Ebene ---
        hb_results = store.heartbeat(
            m1["job_id"], stats={"frames": 43},
            results={"channels": [{"channel_id": "C1", "hits": 2}], "cycles": 1},
        )
        assert hb_results["results"] == {"channels": [{"channel_id": "C1", "hits": 2}], "cycles": 1}, \
            hb_results.get("results")
        assert hb_results["stats"] == {"frames": 43}, hb_results["stats"]
        reread = store.get(m1["job_id"])
        assert reread["results"]["cycles"] == 1, reread.get("results")
        print(f"OK  heartbeat(results=...): manifest[\"results\"] gesetzt (oberste Ebene, "
              f"nicht in stats), bleibt beim erneuten Lesen erhalten")

        # --- f) recover_on_startup ------------------------------------------
        # m1 ist "laeuft" -> muss abgebrochen werden; m3 ist "geplant" mit
        # Start in der Vergangenheit -> verpasst; ein Job in der Zukunft
        # bleibt unveraendert geplant.
        m4 = store.create_job(
            type="ifpan", label="Zukunft", params={},
            start_iso="2099-01-01T00:00:00+01:00", end_iso="2099-01-01T01:00:00+01:00",
        )
        report = store.recover_on_startup()
        assert m1["job_id"] in report["abgebrochen"], report
        assert m3["job_id"] in report["verpasst"], report
        assert m4["job_id"] not in report["abgebrochen"] and m4["job_id"] not in report["verpasst"]
        assert store.get(m1["job_id"])["state"] == ST_ABGEBROCHEN
        assert store.get(m3["job_id"])["state"] == ST_VERPASST
        assert store.get(m4["job_id"])["state"] == ST_GEPLANT
        print(f"OK  recover_on_startup: abgebrochen={report['abgebrochen']}, "
              f"verpasst={report['verpasst']}, Zukunftsjob unveraendert geplant")

        # --- g) delete -------------------------------------------------------
        m5 = store.create_job(
            type="dscan", label="Loeschbar", params={},
            start_iso="2020-02-01T10:00:00+01:00", end_iso="2020-02-01T11:00:00+01:00",
            decimation_s=10,
        )
        job_dir5 = m5["job_dir"]
        store.delete(m5["job_id"])
        assert not os.path.isdir(job_dir5), job_dir5
        try:
            store.get(m5["job_id"])
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: geloeschter Job")
        print("OK  delete (geplant): Verzeichnis entfernt, Index-Eintrag weg")

        m6 = store.create_job(
            type="dscan", label="Mit Zusatzdatei", params={},
            start_iso="2020-02-02T10:00:00+01:00", end_iso="2020-02-02T11:00:00+01:00",
            decimation_s=10,
        )
        with open(os.path.join(m6["job_dir"], "extra.specbin"), "wb") as f:
            f.write(b"x")
        try:
            store.delete(m6["job_id"])
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: Job-Verzeichnis mit Zusatzdatei")
        print("OK  delete mit Zusatzdatei im Job-Verzeichnis -> ValueError (Messdatenschutz)")

        try:
            store.delete(m2["job_id"])   # ist "beendet"
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: delete im Zustand beendet")
        print("OK  delete im Zustand beendet -> ValueError")

        # --- g2) delete_with_data (endgueltiges Loeschen samt Messdaten) ---
        store.set_state(m6["job_id"], ST_GESTOPPT)
        job_dir6 = m6["job_dir"]
        report6 = store.delete_with_data(m6["job_id"])
        assert report6["deleted_files"] >= 2, report6   # job.json + extra.specbin
        assert not os.path.isdir(job_dir6), job_dir6
        try:
            store.get(m6["job_id"])
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: endgueltig geloeschter Job")
        try:
            store.delete_with_data(m4["job_id"])   # ist "geplant"
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: delete_with_data im Zustand geplant")
        print("OK  delete_with_data: Verzeichnis samt Messdaten weg, "
              "Index-Eintrag weg, geplanter Job verweigert")

        # --- h) Index-Robustheit: Manifest von Hand geloescht -------------
        m7 = store.create_job(
            type="dscan", label="Robustheitstest", params={},
            start_iso="2020-02-03T10:00:00+01:00", end_iso="2020-02-03T11:00:00+01:00",
            decimation_s=10,
        )
        os.remove(os.path.join(m7["job_dir"], "job.json"))

        got7 = store.get(m7["job_id"])
        assert got7.get("manifest_unreachable") is True, got7
        assert got7["state"] == ST_GEPLANT, got7["state"]

        result7 = store.set_state(m7["job_id"], ST_LAEUFT)
        assert result7.get("manifest_unreachable") is True, result7
        with open(index_path, encoding="utf-8") as f:
            idx7 = json.load(f)
        assert idx7[m7["job_id"]]["state"] == ST_LAEUFT, idx7[m7["job_id"]]
        print("OK  fehlendes Manifest: get()/set_state() liefern manifest_unreachable=true, "
              "kein Absturz, Index bleibt konsistent")

        # --- i) update_label: in JEDEM Zustand aenderbar, auch "beendet" ---
        updated = store.update_label(m2["job_id"], "  Neuer Text\nmit Zeilenumbruch  ")
        assert updated["label"] == "Neuer Text mit Zeilenumbruch", updated["label"]
        with open(index_path, encoding="utf-8") as f:
            idx_label = json.load(f)
        assert idx_label[m2["job_id"]]["label"] == "Neuer Text mit Zeilenumbruch", idx_label[m2["job_id"]]
        with open(os.path.join(m2["job_dir"], "job.json"), encoding="utf-8") as f:
            manifest_label = json.load(f)
        assert manifest_label["label"] == "Neuer Text mit Zeilenumbruch", manifest_label
        assert store.get(m2["job_id"])["state"] == ST_BEENDET   # Zustand unveraendert
        print(f"OK  update_label auf 'beendet'-Job: Index UND Manifest tragen "
              f"den neuen Text ({updated['label']!r})")

        emptied = store.update_label(m2["job_id"], "")
        assert emptied["label"] == "", emptied["label"]
        with open(index_path, encoding="utf-8") as f:
            idx_empty = json.load(f)
        assert idx_empty[m2["job_id"]]["label"] == "", idx_empty[m2["job_id"]]
        print("OK  update_label mit leerem Text -> Beschriftung geloescht (leerer String)")

        # --- j) Zwei Instanzen auf demselben Index (Lost-Update-Haertung) --
        # store = Instanz A (bereits vorhanden), store_b = Instanz B - beide
        # auf demselben tmp-Index/tmp-storage_roots, wie im echten Betrieb
        # waterfall.py (:8000) und manage_jobs.py (:8050).
        store_b = JobStore(index_path=index_path, storage_roots_path=roots_path)

        m_a = store.create_job(
            type="dscan", label="Instanz A", params={},
            start_iso="2020-03-01T10:00:00+01:00", end_iso="2020-03-01T11:00:00+01:00",
            decimation_s=10,
        )
        m_other = store.create_job(
            type="dscan", label="Instanz A - anderer Job", params={},
            start_iso="2020-03-02T10:00:00+01:00", end_iso="2020-03-02T11:00:00+01:00",
            decimation_s=10,
        )

        # Instanz B sieht den von A angelegten Job (list_jobs liest dank
        # _reload_index() frisch von der Platte) und loescht ihn.
        ids_seen_by_b = [e["job_id"] for e in store_b.list_jobs()]
        assert m_a["job_id"] in ids_seen_by_b, ids_seen_by_b
        store_b.delete(m_a["job_id"])

        # Instanz A mutiert danach einen ANDEREN Job. Ohne _reload_index()
        # wuerde A ihren VERALTETEN, im Konstruktor gecachten Index (der
        # m_a noch enthaelt) zurueckschreiben und den von B geloeschten Job
        # wiederauferstehen lassen (Lost Update). Mit der Haertung bleibt er
        # geloescht.
        store.heartbeat(m_other["job_id"], stats={"sweeps": 1})

        with open(index_path, encoding="utf-8") as f:
            idx_after = json.load(f)
        assert m_a["job_id"] not in idx_after, idx_after
        assert m_other["job_id"] in idx_after, idx_after
        try:
            store.get(m_a["job_id"])
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: von Instanz B geloeschter Job")
        print("OK  zwei JobStore-Instanzen auf demselben Index: von B geloeschter "
              "Job bleibt nach Mutation durch A geloescht (kein Lost Update)")

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()
