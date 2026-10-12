#!/usr/bin/env python3
"""
preparation_worker.py - Aufbereitungs-Worker (T-1603, Meilenstein 16), Port 8090.

Nimmt die Aufbereitung abgeleiteter Artefakte vorweg, statt sie beim ersten
Zugriff traege entstehen zu lassen. Welche Typen das sind, steht seit T-1607
im Artefaktregister ARTIFACTS - cycle() kennt keinen Typ mehr namentlich:

  - "<wav>.audspec"     mono und IQ, audio_spectrum.ensure_audspec.
                        Bei der langen IQ-Aufnahme 146 s - Zeit, die sonst
                        der Betrachter in :8070 absitzt.
  - "<wav>.klasse.json" NUR mono, audio_classify.ensure_klasse. Lief bisher
                        synchron per POST /api/classify beim Oeffnen von
                        :8070; kostet wenig (600 s Audio in 0,07 s), war
                        aber die zweite Wartezeit dort.

Der Worker arbeitet AUSSCHLIESSLICH mit WAV-Aufnahmen (Entscheidung
2026-09-10, OE3GAS): ".befund.json" entstuende aus .specbin und wuerde die
Bestandsaufnahme verdoppeln, bei einem Gewinn von wenigen Sekunden je
Session. Er bleibt der traegen Erzeugung in :8040 ueberlassen.

KEINE eigene Weboberflaeche (Entscheidung 2026-09-07, OE3GAS). Der Server
liefert nur /api/status; angezeigt wird das im Reiter "Aufbereitung" von
:8050, das die Antwort SERVERSEITIG proxyt - genau wie /api/livestatus den
D-SCAN-Controller auf :8000 proxyt (Begruendung dort: ein Browser-fetch()
ueber Portgrenzen waere cross-origin, und kein Server der Suite setzt
CORS-Header). Ist der Worker nicht erreichbar, sagt der Reiter das, statt
eine veraltete Datei anzuzeigen.

Trigger ist eine ABGLEICHSCHLEIFE, keine Zuspielung aus den Aufnahme-
servern: alle SCAN_INTERVAL_S wird geschaut, was fehlt. Eine verlorene
Nachricht gaebe es damit nicht - ein Worker-Neustart, eine per
tools/iq_import.py eingelegte Datei oder ein von Hand geloeschter Cache
werden vom naechsten Durchlauf von selbst eingeholt. Der Aufnahmepfad
bleibt unberuehrt.

Zurueckhaltung (der Kern dieses Moduls)
--------------------------------------
Der Worker faengt NICHTS an, wenn eines davon zutrifft:
  - Eine Aufnahme laeuft (Sidecar running=True). UDP-Verlust am EB200 ist
    teurer als eine spaete Auswertung, und ein Bau saettigt einen Kern.
  - Der Platz reicht nicht (eb200_core/prepare_budget.py: Sockel + Bedarf +
    Aufnahmereserve). Der teure Schaden ist nicht ein fehlender Cache - der
    ist regenerierbar - sondern eine abgebrochene Aufnahme.
  - Die Arbeit wuerde nicht vor der naechsten geplanten Aufnahme fertig
    (Laufzeitvorhersage gegen job_store).
Alle drei Faelle sind "zurueckgestellt" MIT GRUND, kein Fehler: der Auftrag
bleibt liegen und wird beim naechsten Durchlauf erneut geprueft.

Nebenlaeufigkeit: der Bau haelt die Sperrdatei aus T-1601
("<wav>.audspec.lock"). Oeffnet jemand waehrenddessen :8070, baut der
Betrachter NICHT ein zweites Mal, sondern sieht ueber dieselbe Datei den
Fortschritt. Umgekehrt ueberspringt der Worker eine Datei, an der der
Betrachter schon rechnet. Immer nur EIN Bau ZUR SELBEN ZEIT - die Spitze
liegt bei knapp 300 MB (gemessen 276 MB, siehe prepare_budget) - aber
innerhalb eines Durchlaufs so viele nacheinander, wie ins Zeitbudget passen
(CYCLE_BUDGET_S, T-1604).

Journal
-------
logs/preparation_worker.jsonl, eine JSON-Zeile je Zustandsuebergang, sofort
geschrieben und geschlossen (ueberlebt einen harten Abbruch). Es beantwortet
die Frage, die spaeter zaehlt: warum wurde etwas NICHT getan. Begrenzung
ueber die Dateigroesse - bei JOURNAL_MAX_BYTES einmal nach ".1" umbenennen,
die alte ".1" faellt weg. Kein logging-Modul: fuer ein Ereignisjournal mit
fuenf Feldern waere eine Handler-Hierarchie mehr Aufwand als Nutzen, und
kein anderer Server der Suite benutzt es.

Zustandscodes sind ASCII und sprachneutral (Projektregel: Server liefern
Codes und Zahlen, den Anzeigetext bildet die Oberflaeche).

Architektur wie die uebrigen Ein-Datei-Server: http.server, nur Standard-
bibliothek plus numpy ueber audio_spectrum. Die Kernlogik steckt in freien
Funktionen, die nur Pfade und Zahlen entgegennehmen (Muster manage_jobs.py)
- dadurch ist sie ohne laufenden Server und ohne Geraet pruefbar, siehe
"python preparation_worker.py --selftest".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from audio_classify import ensure_klasse, klasse_path_for, load_klasse
from audio_spectrum import (
    AudioSpectrumBuildingError,
    AudioSpectrumBusyError,
    AudioSpectrumUnsupportedError,
    KIND_IQ,
    KIND_MONO,
    LOCK_STALE_S,
    audspec_header,
    ensure_audspec,
    lock_path_for,
    progress_snapshot,
    wav_kind,
)
from eb200_core import prepare_budget
from eb200_core.http_base import QuietThreadingHTTPServer
from eb200_core.job_store import ST_GEPLANT, JobStore
from eb200_core.param_store import register_server
from eb200_core import paths
from eb200_core.path_sandbox import sandbox_roots

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8090
# T-2003: Konfiguration, Journal und Sandbox-Wurzel unter der Datenwurzel
# (eb200_core/paths.py; ohne --data-dir das Repo-Root wie bisher).
DATA_ROOT = paths.DATA_ROOT

CONFIG_PATH = paths.data_path("preparation_worker.json")
JOURNAL_PATH = paths.data_path("logs", "preparation_worker.jsonl")
JOURNAL_MAX_BYTES = 2 * 1024 ** 2

SCAN_INTERVAL_S = 60.0        # Latenz ist gleichgueltig, Bauzeiten liegen darueber
MAX_ATTEMPTS = 3              # danach gilt eine Datei bis zum Neustart als tot

# Zeitbudget je Durchlauf (T-1604). Die urspruengliche Regel "hoechstens EIN
# Bau je Zyklus" war fuer die lange IQ-Aufnahme gedacht, wo nach 146 s die
# Lage ohnehin neu bewertet gehoert. Bei kurzen Mitschnitten ist sie unsinnig:
# der erste Trockenlauf fand 57 offene Auftraege, von denen einer 0,14 s
# kostete - im Minutentakt haette das eine Stunde gedauert. Jetzt wird
# innerhalb des Budgets weitergebaut und VOR JEDEM Bau neu bewertet, damit
# Pausenregel und Platzpruefung unveraendert greifen. Die lange IQ-Aufnahme
# verhaelt sich wie bisher: sie braucht das Budget allein auf.
CYCLE_BUDGET_S = 60.0

# Zustaende eines Auftrags. "deferred" traegt zusaetzlich einen Grundcode
# (prepare_budget.CODE_* oder CODE_RECORDING_ACTIVE/CODE_LOCKED).
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_DONE = "done"
ST_DEFERRED = "deferred"
ST_FAILED = "failed"

CODE_RECORDING_ACTIVE = "recording_active"   # Aufnahme laeuft - Pause
CODE_LOCKED = "locked"                       # anderer Prozess baut bereits
CODE_UNSUPPORTED = "unsupported"             # Kanalzahl/Bittiefe ungestuetzt
CODE_NO_META = "no_meta"                     # weder Sidecar noch WAV-Kopf lesbar
CODE_ERROR = "error"                         # unerwartete Ausnahme

ARTIFACT_AUDSPEC = "audspec"
ARTIFACT_KLASSE = "klasse"

# Wie oft eine unveraenderte Platzlage erneut auf die Konsole darf (T-1610).
# Der Zustand wird bei JEDEM Zyklus erhoben, aber eine Minutenmeldung machte
# das Starter-Sammellog unlesbar. Ein WECHSEL des Codes meldet sich sofort.
SPACE_WARN_REPEAT_S = 3600.0


# ---------------------------------------------------------------------------
# Konfiguration und Journal
# ---------------------------------------------------------------------------

def load_config(path: str = CONFIG_PATH) -> dict:
    """Maschinenlokale Einstellungen (ungetrackt wie storage_roots.json).
    Fehlt die Datei oder ist sie unlesbar, gelten die Vorgaben aus dem Code -
    ein kaputtes JSON darf den Dienst nicht am Start hindern."""
    cfg = {"enabled": True, "scan_interval_s": SCAN_INTERVAL_S,
           "max_attempts": MAX_ATTEMPTS, "cycle_budget_s": CYCLE_BUDGET_S}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for key in cfg:
                if key in data:
                    cfg[key] = data[key]
    except (OSError, ValueError):
        pass
    try:
        cfg["scan_interval_s"] = max(5.0, float(cfg["scan_interval_s"]))
        cfg["cycle_budget_s"] = max(1.0, float(cfg["cycle_budget_s"]))
        cfg["max_attempts"] = max(1, int(cfg["max_attempts"]))
        cfg["enabled"] = bool(cfg["enabled"])
    except (TypeError, ValueError):
        cfg = {"enabled": True, "scan_interval_s": SCAN_INTERVAL_S,
               "max_attempts": MAX_ATTEMPTS, "cycle_budget_s": CYCLE_BUDGET_S}
    return cfg


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def journal_write(event: dict, path: str = JOURNAL_PATH,
                  max_bytes: int = JOURNAL_MAX_BYTES) -> None:
    """Eine JSON-Zeile anhaengen, Datei sofort schliessen. Schlaegt das fehl
    (Platte voll, Ordner weg), wird es geschluckt: ein Journal darf den
    Dienst nie zum Stehen bringen."""
    line = dict(event)
    line.setdefault("ts", _now_iso())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) >= max_bytes:
            backup = path + ".1"
            try:
                os.replace(path, backup)      # alte .1 faellt dabei weg
            except OSError:
                pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def journal_tail(n: int = 50, path: str = JOURNAL_PATH) -> list:
    """Die letzten n Eintraege als Liste (aelteste zuerst) - Grundlage des
    Reiters in :8050. Unlesbare Zeilen werden uebersprungen."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Bestandsaufnahme
# ---------------------------------------------------------------------------

def wav_candidates(roots: list) -> list:
    """Alle *.wav eine Ebene tief in den Ablagewurzeln, sortiert.

    Bewusst NICHT ueber recording_index.list_recordings(): das oeffnet je
    Aufruf Session-Datenbanken und Job-Manifeste und liefert ausserdem
    channels/frames gar nicht mit - beides steht im Sidecar. Nicht rekursiv,
    aus demselben Grund wie dort: records/longrun_*/ gehoert zu Jobs.
    """
    seen = set()
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file() or not entry.name.endswith(".wav"):
                    continue
            except OSError:
                continue
            key = os.path.normcase(os.path.abspath(entry.path))
            if key in seen:
                continue
            seen.add(key)
            out.append(entry.path)
    return out


def _meta_from_wav(wav_path: str, info: dict) -> bool:
    """Ersatzquelle: die WAV-Kopfzeile (T-1604). Sie traegt nframes,
    nchannels, framerate und sampwidth - alles, was die Planung braucht.

    Damit faellt eine ganze Fehlerklasse weg: ueber tools/iq_import.py
    eingelegte Dateien haben kein Sidecar (im ersten Trockenlauf betraf das
    TETRA_393438630Hz_IQ.wav) und waeren sonst dauerhaft "fehlgeschlagen".
    "running" bleibt dabei False, und das ist richtig: ein Sidecar entsteht
    beim Aufnahmestart mit running=True, eine Datei ohne Sidecar ist also
    keine laufende EB200-Aufnahme.
    """
    try:
        with wave.open(wav_path, "rb") as wf:
            info["channels"] = wf.getnchannels()
            info["sample_rate_hz"] = wf.getframerate()
            info["n_frames"] = wf.getnframes()
            sampwidth = wf.getsampwidth()
    except (OSError, wave.Error, EOFError):
        return False
    try:
        info["kind"] = wav_kind(info["channels"], sampwidth)
    except AudioSpectrumUnsupportedError:
        info["code"] = CODE_UNSUPPORTED
        return True
    info["meta_source"] = "wav"
    return True


def candidate_info(wav_path: str) -> dict:
    """Alles, was ueber eine Aufnahme fuer die Planung bekannt sein muss:
    Art, Framezahl, Abtastrate, Kanalzahl und ob sie noch laeuft.

    Erste Quelle ist das Sidecar (<wav>.json, eb200_core/audio_wav.py) -
    dort stehen channels, frames_written, sample_rate_hz und running
    beisammen, und nur dort steht "running". Fehlt oder taugt es nicht,
    uebernimmt seit T-1604 die WAV-Kopfzeile (_meta_from_wav). Erst wenn
    auch die nichts hergibt, gilt die Datei als nicht planbar
    (code=no_meta): ohne Framezahl gibt es keine Groessen- und keine
    Laufzeitvorhersage, und raten waere hier das Gegenteil von "mitdenken".
    """
    info = {"path": wav_path, "kind": None, "n_frames": 0, "sample_rate_hz": 0,
            "channels": 0, "running": False, "code": None, "meta_source": None}
    meta = None
    try:
        with open(wav_path + ".json", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            meta = loaded
    except (OSError, ValueError):
        meta = None

    if meta is not None:
        info["running"] = bool(meta.get("running", False))
        try:
            info["channels"] = int(meta.get("channels"))
            info["sample_rate_hz"] = int(meta.get("sample_rate_hz"))
            info["n_frames"] = int(meta.get("frames_written"))
            info["meta_source"] = "sidecar"
        except (TypeError, ValueError):
            info["meta_source"] = None
        if info["meta_source"] == "sidecar":
            try:
                info["kind"] = wav_kind(info["channels"],
                                        int(meta.get("sampwidth_bytes", 2)))
            except (AudioSpectrumUnsupportedError, TypeError, ValueError):
                info["code"] = CODE_UNSUPPORTED
            return info

    if not _meta_from_wav(wav_path, info):
        info["code"] = CODE_NO_META
    return info


def needs_klasse(wav_path: str) -> bool:
    """Fehlt oder veraltet das Klassifikations-Sidecar? load_klasse() prueft
    Format und wav-Groesse/mtime und liest nur ein paar Kilobyte - anders als
    beim Audspec braucht es hier keine eigene Kopfpruefung."""
    return load_klasse(wav_path) is None


def needs_audspec(wav_path: str) -> bool:
    """Fehlt oder veraltet der Cache? Prueft NUR die Kopfzeile
    (audio_spectrum.audspec_header, T-1603) - load_audspec() wuerde bei
    jedem Durchlauf 31 MB Matrizen einlesen, nur um festzustellen, dass
    nichts zu tun ist."""
    return audspec_header(wav_path) is None


def cleanup_stale_locks(roots: list) -> dict:
    """Verwaiste Sperr- und Temp-Dateien einsammeln (Absturz des Workers
    ODER eines Betrachters). Entfernt werden nur Sperren, deren Heartbeat
    aelter als LOCK_STALE_S ist - eine LEBENDE Sperre bleibt unangetastet,
    auch wenn sie einem fremden Prozess gehoert."""
    removed, kept = [], []
    for wav_path in wav_candidates(roots):
        lock = lock_path_for(wav_path)
        for path in (lock + ".tmp", wav_path + ".audspec.tmp"):
            if os.path.isfile(path):
                try:
                    os.unlink(path)
                    removed.append(path)
                except OSError:
                    pass
        if not os.path.isfile(lock):
            continue
        if progress_snapshot(wav_path) is not None:
            kept.append(lock)                 # lebt - fremder Bau laeuft
            continue
        try:
            os.unlink(lock)
            removed.append(lock)
        except OSError:
            pass
    return {"removed": removed, "kept": kept}


# ---------------------------------------------------------------------------
# Umfeld: laufende und geplante Aufnahmen
# ---------------------------------------------------------------------------

def open_recordings(infos: list) -> list:
    """Beschreibung der laufenden Aufnahmen fuer
    prepare_budget.recording_reserve_bytes(). Ohne bekanntes Ende - das ist
    bei einer von Hand gestarteten Aufnahme der Normalfall - rechnet die
    Reserve mit dem 2-h-Horizont."""
    return [{"sample_rate_hz": i["sample_rate_hz"], "channels": i["channels"],
             "sampwidth_bytes": 2}
            for i in infos if i.get("running") and i.get("sample_rate_hz")]


def seconds_until_next_recording(store, now=None):
    """Sekunden bis zum naechsten GEPLANTEN Long-Run-Job, oder None, wenn
    keiner ansteht oder der Jobspeicher nicht lesbar ist. None heisst
    ausdruecklich "keine Terminschranke", nicht "sofort" - die Platz- und
    Pausenregeln greifen davon unabhaengig."""
    if store is None:
        return None
    now = now or datetime.now(timezone.utc)
    best = None
    try:
        jobs = store.list_jobs()
    except Exception:
        return None
    for job in jobs:
        if job.get("state") != ST_GEPLANT:
            continue
        try:
            start = datetime.fromisoformat(job["start_iso"])
        except (KeyError, TypeError, ValueError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        delta = (start - now).total_seconds()
        if delta < 0:
            continue
        best = delta if best is None else min(best, delta)
    return best


# ---------------------------------------------------------------------------
# Ein Durchlauf
# ---------------------------------------------------------------------------

def _plan_audspec(info: dict, volume_path: str, reserve_bytes: int, until_next) -> dict:
    return prepare_budget.plan_audspec(
        info["kind"], info["n_frames"], volume_path,
        reserve_bytes=reserve_bytes, seconds_until_next_recording=until_next)


def _plan_klasse(info: dict, volume_path: str, reserve_bytes: int, until_next) -> dict:
    """Klassifikation kostet fast nichts (siehe prepare_budget), bekommt aber
    dieselbe Budgetpruefung wie alles andere - eine volle Platte ist eine
    volle Platte, egal wie klein das Artefakt ist."""
    est = prepare_budget.estimate_klasse_seconds(info["n_frames"])
    plan = prepare_budget.check_budget(
        prepare_budget.klasse_nbytes(), est, volume_path,
        reserve_bytes=reserve_bytes, seconds_until_next_recording=until_next)
    plan["kind"] = info["kind"]
    plan["artifact_bytes"] = prepare_budget.klasse_nbytes()
    plan["peak_memory_bytes"] = prepare_budget.peak_memory_bytes(KIND_MONO)
    return plan


def _build_audspec(info: dict) -> None:
    ensure_audspec(info["path"])


def _build_klasse(info: dict) -> None:
    ensure_klasse(info["path"])


# Artefaktregister (T-1607). Jede Zeile beschreibt EINEN abgeleiteten Typ:
# fuer welche Aufnahmearten er gilt, woran man erkennt dass er fehlt, wie
# geplant und wie gebaut wird. cycle() kennt keinen Typ mehr namentlich -
# ein neuer Artefakttyp ist damit ein Eintrag plus zwei Funktionen, kein
# Umbau der Schleife. Die Reihenfolge ist die Baureihenfolge.
#
# Der Worker arbeitet AUSSCHLIESSLICH mit WAV-Aufnahmen (Entscheidung
# 2026-09-10, OE3GAS): .befund.json entstuende aus .specbin und wuerde die
# Bestandsaufnahme verdoppeln, bei einem Gewinn von wenigen Sekunden je
# Session. Er bleibt der Aufbereitung in :8040 ueberlassen.
ARTIFACTS = (
    {"name": ARTIFACT_AUDSPEC, "kinds": (KIND_MONO, KIND_IQ),
     "needs": needs_audspec, "plan": _plan_audspec, "build": _build_audspec,
     "path": lambda w: w + ".audspec"},
    {"name": ARTIFACT_KLASSE, "kinds": (KIND_MONO,),
     "needs": needs_klasse, "plan": _plan_klasse, "build": _build_klasse,
     "path": klasse_path_for},
)


def artifact_by_name(name: str):
    """Registereintrag zu einem Namen, oder None. Nur fuer Selbsttest und
    Diagnose - die Schleife laeuft ueber ARTIFACTS."""
    for art in ARTIFACTS:
        if art["name"] == name:
            return art
    return None


def evaluate(info: dict, artifact: dict, volume_path: str, reserve_bytes: int,
             until_next) -> dict:
    """Darf DIESER Artefakttyp fuer DIESE Aufnahme jetzt gebaut werden?
    Liefert {"state", "code", ...} - im Gutfall state=pending mit code=ok,
    sonst deferred/failed mit Grund. Reine Entscheidung, baut nichts."""
    if info.get("code") == CODE_NO_META:
        return {"state": ST_FAILED, "code": CODE_NO_META}
    if info.get("code") == CODE_UNSUPPORTED:
        return {"state": ST_FAILED, "code": CODE_UNSUPPORTED}
    if info.get("running"):
        return {"state": ST_DEFERRED, "code": CODE_RECORDING_ACTIVE}
    if progress_snapshot(info["path"]) is not None:
        return {"state": ST_DEFERRED, "code": CODE_LOCKED}

    plan = artifact["plan"](info, volume_path, reserve_bytes, until_next)
    plan["state"] = ST_PENDING if plan.get("ok") else ST_DEFERRED
    return plan


def build_one(info: dict, artifact: dict) -> dict:
    """Einen Auftrag ausfuehren. Faengt genau die Faelle ab, die im
    Normalbetrieb vorkommen koennen, und liefert immer einen Zustand -
    eine Ausnahme darf die Schleife nie verlassen."""
    started = time.monotonic()
    try:
        artifact["build"](info)
        return {"state": ST_DONE, "code": "ok",
                "seconds": time.monotonic() - started}
    except AudioSpectrumBuildingError:
        return {"state": ST_DEFERRED, "code": CODE_LOCKED}
    except AudioSpectrumBusyError:
        return {"state": ST_DEFERRED, "code": CODE_RECORDING_ACTIVE}
    except AudioSpectrumUnsupportedError:
        return {"state": ST_FAILED, "code": CODE_UNSUPPORTED}
    except Exception as exc:
        return {"state": ST_FAILED, "code": CODE_ERROR,
                "detail": f"{type(exc).__name__}: {exc}"}


class Worker:
    """Zustand ueber die Durchlaeufe hinweg: Fehlversuche je Datei und die
    Zusammenfassung des letzten Zyklus fuer /api/status.

    Fehlversuche leben NUR im Speicher (Entscheidung 2026-09-07, OE3GAS):
    eine dauerhaft defekte Datei blockiert damit nicht die Schleife,
    erscheint nach einem Neustart aber wieder - das ist die gewollte
    Erinnerungsluecke, keine Nachlaessigkeit.
    """

    def __init__(self, repo_root: str = DATA_ROOT, config=None, store=None,
                 journal_path: str = JOURNAL_PATH):
        self.repo_root = repo_root
        self.config = config if config is not None else load_config()
        self.store = store
        # T-1604: eigener Journalpfad statt der Modulkonstante. Vorher schrieb
        # jeder --selftest-Lauf erfundene Testeintraege in das PRODUKTIVE
        # logs/preparation_worker.jsonl - ausgerechnet in die Datei, die
        # spaeter beantworten soll, warum etwas nicht getan wurde.
        self.journal_path = journal_path
        self.lock = threading.Lock()
        self.attempts = {}
        self.last_cycle = {}
        self.current = None
        self.space = {}                  # letzte Lagebeurteilung (T-1610)
        self._space_code = None          # zuletzt gemeldeter Code
        self._space_said = 0.0           # monotone Zeit der letzten Meldung
        self.started_iso = _now_iso()

    def roots(self) -> list:
        return sandbox_roots(self.repo_root)

    def cycle(self) -> dict:
        """Ein vollstaendiger Durchlauf: sichten, dann innerhalb des
        Zeitbudgets bauen, was gebaut werden darf.

        VOR JEDEM Bau wird das Sidecar neu gelesen und neu bewertet - in
        einem Durchlauf, der eine Minute dauern darf, kann inzwischen eine
        Aufnahme gestartet worden sein. Die Terminschranke schrumpft
        entsprechend mit (T-1604).
        """
        started = time.monotonic()
        budget = self.config["cycle_budget_s"]
        roots = self.roots()
        volume = roots[0] if roots else self.repo_root
        infos = [candidate_info(p) for p in wav_candidates(roots)]
        reserve = prepare_budget.recording_reserve_bytes(open_recordings(infos))
        until_next = seconds_until_next_recording(self.store)

        # T-1610: Platzlage bei JEDEM Zyklus erheben, nicht nur wenn zufaellig
        # ein Auftrag ansteht. Nur so kann die Oberflaeche warnen, BEVOR der
        # erste Auftrag zurueckgestellt wird.
        space = prepare_budget.disk_report(volume, reserve)
        self._announce_space(space)

        summary = {"scanned": len(infos), "todo": 0, "built": 0,
                   "deferred": {}, "failed": 0, "reserve_bytes": reserve,
                   "seconds_until_next_recording": until_next,
                   "budget_exhausted": False, "seconds": 0.0,
                   "space": space, "finished_iso": None}

        # Arbeitsvorrat: je (Aufnahme, Artefakttyp) ein Auftrag. Der
        # Fehlversuchszaehler haengt am PAAR, nicht an der Datei - eine
        # kaputte Klassifikation soll den Audspec nicht mitreissen.
        offen = []
        for info in infos:
            if not info.get("path"):
                continue
            if info.get("code") is not None:
                # Ohne Metadaten ist JEDER Typ blockiert - dafuer genuegt EIN
                # Auftrag, sonst zaehlte dieselbe kaputte Datei mehrfach als
                # fehlgeschlagen.
                schluessel = (info["path"], ARTIFACTS[0]["name"])
                if self.attempts.get(schluessel, 0) >= self.config["max_attempts"]:
                    summary["failed"] += 1
                    continue
                summary["todo"] += 1
                offen.append((info, ARTIFACTS[0]))
                continue
            for art in ARTIFACTS:
                if info.get("kind") not in art["kinds"]:
                    continue
                if not art["needs"](info["path"]):
                    continue
                schluessel = (info["path"], art["name"])
                if self.attempts.get(schluessel, 0) >= self.config["max_attempts"]:
                    summary["failed"] += 1
                    continue
                summary["todo"] += 1
                offen.append((info, art))

        for info, art in offen:
            elapsed = time.monotonic() - started
            if elapsed >= budget:
                summary["budget_exhausted"] = True
                break
            frisch = candidate_info(info["path"])
            rest = None if until_next is None else max(0.0, until_next - elapsed)
            verdict = evaluate(frisch, art, volume, reserve, rest)
            schluessel = (frisch["path"], art["name"])

            if verdict["state"] == ST_DEFERRED:
                code = verdict.get("code", CODE_ERROR)
                summary["deferred"][code] = summary["deferred"].get(code, 0) + 1
                self._record(frisch, art, verdict)
                continue
            if verdict["state"] == ST_FAILED:
                summary["failed"] += 1
                self._bump(schluessel)
                self._record(frisch, art, verdict)
                continue

            with self.lock:
                self.current = {"path": frisch["path"], "kind": frisch["kind"],
                                "artifact": art["name"],
                                "est_seconds": verdict.get("est_seconds"),
                                "started_iso": _now_iso()}
            self._record(frisch, art, {"state": ST_RUNNING, "code": "ok",
                                       "est_seconds": verdict.get("est_seconds")})
            result = build_one(frisch, art)
            with self.lock:
                self.current = None
            if result["state"] == ST_DONE:
                summary["built"] += 1
            elif result["state"] == ST_FAILED:
                summary["failed"] += 1
                self._bump(schluessel)
            else:
                code = result.get("code", CODE_ERROR)
                summary["deferred"][code] = summary["deferred"].get(code, 0) + 1
            self._record(frisch, art, result)

        summary["seconds"] = round(time.monotonic() - started, 3)
        summary["finished_iso"] = _now_iso()
        with self.lock:
            self.last_cycle = summary
            self.space = space
        return summary

    def _announce_space(self, space: dict) -> None:
        """Platzlage ins Journal und auf die Konsole - aber gedrosselt
        (T-1610). Ein WECHSEL des Codes meldet sich sofort, eine
        unveraenderte Lage hoechstens alle SPACE_WARN_REPEAT_S. SPACE_OK
        wird nur beim Wechsel gemeldet, sonst schriebe der Gutfall im
        Stundentakt in ein Journal, das Ausnahmen zeigen soll."""
        code = space.get("code")
        now = time.monotonic()
        wechsel = code != self._space_code
        faellig = (now - self._space_said) >= SPACE_WARN_REPEAT_S
        if not wechsel and not (faellig and code != prepare_budget.SPACE_OK):
            return
        self._space_code = code
        self._space_said = now
        journal_write({"state": "space", "code": code,
                       "free_bytes": space.get("free_bytes"),
                       "floor_bytes": space.get("floor_bytes"),
                       "reserve_bytes": space.get("reserve_bytes"),
                       "available_bytes": space.get("available_bytes")},
                      self.journal_path)
        if code == prepare_budget.SPACE_OK:
            return
        gib = (space.get("available_bytes") or 0) / 1024 ** 3
        wort = ("Platz wird knapp" if code == prepare_budget.SPACE_TIGHT
                else "zu wenig Platz - es wird nichts mehr aufbereitet")
        print(f"[preparation_worker] {wort}: {gib:.1f} GiB verfuegbar "
              f"(frei {(space.get('free_bytes') or 0) / 1024 ** 3:.1f} GiB, "
              f"Sockel {(space.get('floor_bytes') or 0) / 1024 ** 3:.1f} GiB)",
              file=sys.stderr)

    def _bump(self, schluessel) -> None:
        """schluessel ist das Paar (Pfad, Artefaktname) - siehe cycle()."""
        self.attempts[schluessel] = self.attempts.get(schluessel, 0) + 1

    def _record(self, info: dict, artifact: dict, verdict: dict) -> None:
        event = {"file": os.path.basename(info["path"]),
                 "artifact": artifact["name"],
                 "state": verdict.get("state"), "code": verdict.get("code")}
        if info.get("meta_source"):
            event["meta_source"] = info["meta_source"]
        for key in ("est_seconds", "seconds", "artifact_bytes", "free_bytes",
                    "floor_bytes", "need_bytes", "reserve_bytes",
                    "seconds_until_next_recording", "detail"):
            if verdict.get(key) is not None:
                event[key] = verdict[key]
        journal_write(event, self.journal_path)

    def status(self) -> dict:
        """Antwort fuer /api/status - alles, was der Reiter in :8050 zeigt."""
        with self.lock:
            current = dict(self.current) if self.current else None
            last = dict(self.last_cycle)
            space = dict(self.space)
        if not space:
            # Noch kein Zyklus gelaufen (Start, Worker abgeschaltet): frisch
            # erheben, damit der Reiter nie ohne Platzangabe dasteht.
            roots = self.roots()
            space = prepare_budget.disk_report(roots[0] if roots else self.repo_root)
        if current:
            snap = progress_snapshot(current["path"])
            current["progress"] = snap
            current["file"] = os.path.basename(current["path"])
            current.pop("path", None)
        return {"started_iso": self.started_iso, "enabled": self.config["enabled"],
                "scan_interval_s": self.config["scan_interval_s"],
                "space": space,
                "current": current, "last_cycle": last,
                "attempts": len(self.attempts),
                "journal": journal_tail(30, self.journal_path)}

    def run_forever(self) -> None:
        journal_write({"state": "worker_start", "code": "ok",
                       "enabled": self.config["enabled"]}, self.journal_path)
        report = cleanup_stale_locks(self.roots())
        if report["removed"]:
            journal_write({"state": "cleanup", "code": "ok",
                           "removed": len(report["removed"])}, self.journal_path)
            print(f"[preparation_worker] {len(report['removed'])} verwaiste "
                  f"Sperr-/Temp-Datei(en) entfernt")
        while True:
            if self.config["enabled"]:
                try:
                    summary = self.cycle()
                    if summary["built"] or summary["failed"]:
                        print(f"[preparation_worker] Zyklus: {summary['built']} gebaut, "
                              f"{summary['failed']} fehlgeschlagen, "
                              f"{summary['todo']} offen")
                except Exception as exc:      # nie sterben
                    journal_write({"state": ST_FAILED, "code": CODE_ERROR,
                                   "detail": f"{type(exc).__name__}: {exc}"},
                                  self.journal_path)
                    print(f"[preparation_worker] Zyklusfehler: {exc!r}", file=sys.stderr)
            time.sleep(self.config["scan_interval_s"])


# ---------------------------------------------------------------------------
# HTTP - nur Status, keine Oberflaeche
# ---------------------------------------------------------------------------

WORKER = None


class Handler(BaseHTTPRequestHandler):
    server_version = "EB200PreparationWorker/1.0"

    def log_message(self, fmt, *args) -> None:
        pass                                   # Muster QuietThreadingHTTPServer

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/status":
                self._send_json(WORKER.status() if WORKER else {"error": "no worker"})
            else:
                # Bewusst kein HTML: dieser Dienst hat keine Oberflaeche,
                # angezeigt wird er im Reiter "Aufbereitung" von :8050.
                self._send_json({"error": "only /api/status"}, status=404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)


def main() -> int:
    global WORKER
    ap = argparse.ArgumentParser(description="Aufbereitungs-Worker (Port 8090)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument("--data-dir", metavar="PFAD",
                    help="Datenwurzel (wertet eb200_core/paths.py beim Import aus, T-2003)")
    ap.add_argument("--once", action="store_true",
                    help="einen Durchlauf ausfuehren und beenden (ohne Server)")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    try:
        store = JobStore()
    except Exception as exc:
        print(f"[preparation_worker] Jobspeicher nicht lesbar ({exc!r}) - "
              f"Terminschranke bleibt aus", file=sys.stderr)
        store = None

    WORKER = Worker(store=store)
    if args.once:
        print(json.dumps(WORKER.cycle(), indent=2, ensure_ascii=False))
        return 0

    threading.Thread(target=WORKER.run_forever, daemon=True).start()
    httpd = QuietThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    register_server("preparation_worker", HTTP_PORT)
    print(f"[preparation_worker] Status auf http://localhost:{HTTP_PORT}/api/status")
    print(f"[preparation_worker] Journal: {JOURNAL_PATH}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[preparation_worker] beendet")
    finally:
        httpd.server_close()
    return 0


def _self_test() -> None:
    """Selbsttest (python preparation_worker.py --selftest) - ohne Geraet,
    ohne Server, ohne Netz. Geprueft wird die Entscheidungslogik, nicht die
    Rechnung in audio_spectrum (die hat ihren eigenen Selbsttest)."""
    import tempfile
    import wave

    import numpy as np

    def schreibe_wav(path, sekunden, sr=16000, kanaele=1, running=False):
        n = int(sr * sekunden)
        t = np.arange(n) / sr
        sig = 0.3 * np.sin(2 * np.pi * 1000 * t)
        daten = np.zeros(n * kanaele, dtype="<i2")
        for k in range(kanaele):
            daten[k::kanaele] = np.clip(sig * 32767, -32768, 32767).astype("<i2")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(kanaele)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(daten.tobytes())
        with open(path + ".json", "w", encoding="utf-8") as fh:
            json.dump({"sample_rate_hz": sr, "channels": kanaele,
                       "sampwidth_bytes": 2, "frames_written": n,
                       "duration_s": sekunden, "running": running}, fh)
        return path

    with tempfile.TemporaryDirectory() as tmp:
        journal = os.path.join(tmp, "logs", "journal.jsonl")

        # --- T1: Journal -----------------------------------------------------
        journal_write({"state": ST_DONE, "code": "ok", "file": "a.wav"}, journal)
        journal_write({"state": ST_DEFERRED, "code": CODE_RECORDING_ACTIVE}, journal)
        eintraege = journal_tail(10, journal)
        assert len(eintraege) == 2, eintraege
        assert eintraege[0]["state"] == ST_DONE and "ts" in eintraege[0], eintraege[0]
        assert eintraege[1]["code"] == CODE_RECORDING_ACTIVE, eintraege[1]
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write("kaputte Zeile\n")
        assert len(journal_tail(10, journal)) == 2, "kaputte Zeile nicht uebersprungen"
        journal_write({"state": "x"}, journal, max_bytes=1)   # Rotation erzwingen
        assert os.path.isfile(journal + ".1"), "keine Rotation"
        assert len(journal_tail(10, journal)) == 1, "nach Rotation nicht frisch"
        print("T1 ok  (Journal: anhaengen, Zeitstempel, kaputte Zeilen ueberspringen, "
              "Rotation bei Ueberschreitung)")

        # --- T2: Konfiguration ----------------------------------------------
        cfg_path = os.path.join(tmp, "cfg.json")
        vorgabe = load_config(cfg_path)
        assert vorgabe["enabled"] is True and vorgabe["max_attempts"] == MAX_ATTEMPTS
        assert vorgabe["cycle_budget_s"] == CYCLE_BUDGET_S, vorgabe
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"enabled": False, "scan_interval_s": 5, "max_attempts": 7}, fh)
        cfg = load_config(cfg_path)
        assert cfg["enabled"] is False and cfg["max_attempts"] == 7, cfg
        with open(cfg_path, "w", encoding="utf-8") as fh:
            fh.write("{kaputt")
        assert load_config(cfg_path)["enabled"] is True, "kaputte Konfig nicht abgefangen"
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"scan_interval_s": 0.1}, fh)
        assert load_config(cfg_path)["scan_interval_s"] == 5.0, "Untergrenze fehlt"
        assert load_config(cfg_path)["cycle_budget_s"] == CYCLE_BUDGET_S, "Budget-Vorgabe"
        print("T2 ok  (Konfiguration: Vorgaben, Uebernahme, kaputtes JSON, Untergrenze)")

        # --- T3: Bestandsaufnahme -------------------------------------------
        roots = [tmp]
        fertig = schreibe_wav(os.path.join(tmp, "a_fertig.wav"), 2.0)
        laeuft = schreibe_wav(os.path.join(tmp, "b_laeuft.wav"), 2.0, running=True)
        iq = schreibe_wav(os.path.join(tmp, "c_iq.wav"), 2.0, sr=32000, kanaele=2)
        ohne = os.path.join(tmp, "d_ohne.wav")     # kein Sidecar -> WAV-Kopf
        schreibe_wav(ohne, 1.0)
        os.unlink(ohne + ".json")
        kaputt = os.path.join(tmp, "e_kaputt.wav")  # weder Sidecar noch WAV
        with open(kaputt, "wb") as fh:
            fh.write(b"das ist keine RIFF-Datei")
        assert len(wav_candidates(roots)) == 5, wav_candidates(roots)

        i_fertig = candidate_info(fertig)
        assert i_fertig["kind"] == KIND_MONO and i_fertig["running"] is False
        assert i_fertig["n_frames"] == 32000, i_fertig
        assert i_fertig["meta_source"] == "sidecar", i_fertig
        assert candidate_info(laeuft)["running"] is True
        assert candidate_info(iq)["kind"] == KIND_IQ

        # T-1604: ohne Sidecar traegt die WAV-Kopfzeile die Planung. Genau der
        # Fall der ueber tools/iq_import.py eingelegten Dateien.
        i_ohne = candidate_info(ohne)
        assert i_ohne["code"] is None and i_ohne["meta_source"] == "wav", i_ohne
        assert i_ohne["kind"] == KIND_MONO and i_ohne["n_frames"] == 16000, i_ohne
        assert i_ohne["running"] is False, "ohne Sidecar keine laufende Aufnahme"
        assert candidate_info(kaputt)["code"] == CODE_NO_META
        assert needs_audspec(fertig) is True, "ohne Cache muss gebaut werden"

        # Sidecar ohne brauchbare Zahlen -> ebenfalls WAV-Kopf.
        halb = schreibe_wav(os.path.join(tmp, "f_halb.wav"), 1.0)
        with open(halb + ".json", "w", encoding="utf-8") as fh:
            json.dump({"running": False}, fh)
        assert candidate_info(halb)["meta_source"] == "wav", candidate_info(halb)
        os.unlink(halb)
        os.unlink(halb + ".json")
        print("T3 ok  (Kandidaten gefunden; mono/iq/laufend unterschieden; WAV-Kopf "
              "springt bei fehlendem und bei unvollstaendigem Sidecar ein)")

        # --- T4: Reserve und Terminschranke ---------------------------------
        infos = [candidate_info(p) for p in wav_candidates(roots)]
        offen = open_recordings(infos)
        assert len(offen) == 1 and offen[0]["sample_rate_hz"] == 16000, offen
        reserve = prepare_budget.recording_reserve_bytes(offen)
        assert reserve == 32000 * prepare_budget.RECORDING_HORIZON_S, reserve
        assert seconds_until_next_recording(None) is None

        class FakeStore:
            def __init__(self, jobs):
                self._jobs = jobs

            def list_jobs(self):
                return self._jobs

        jetzt = datetime.now(timezone.utc)
        spaeter = jetzt.replace(microsecond=0).isoformat()
        assert seconds_until_next_recording(FakeStore([]), jetzt) is None
        assert seconds_until_next_recording(
            FakeStore([{"state": "beendet", "start_iso": spaeter}]), jetzt) is None

        class KaputterStore:
            def list_jobs(self):
                raise RuntimeError("Index unlesbar")

        assert seconds_until_next_recording(KaputterStore(), jetzt) is None, \
            "kaputter Jobspeicher darf nicht durchschlagen"
        print("T4 ok  (Reserve nur fuer laufende Aufnahmen, 2-h-Horizont; "
              "Terminschranke None bei leerem/kaputtem Jobspeicher)")

        # --- T5: Entscheidung ------------------------------------------------
        audspec = artifact_by_name(ARTIFACT_AUDSPEC)
        klasse = artifact_by_name(ARTIFACT_KLASSE)
        gut = evaluate(candidate_info(fertig), audspec, tmp, 0, None)
        assert gut["state"] == ST_PENDING and gut["code"] == prepare_budget.CODE_OK, gut
        assert evaluate(candidate_info(laeuft), audspec, tmp, 0, None) == \
            {"state": ST_DEFERRED, "code": CODE_RECORDING_ACTIVE}
        assert evaluate(candidate_info(kaputt), audspec, tmp, 0, None)["state"] == ST_FAILED
        assert evaluate(candidate_info(ohne), audspec, tmp, 0, None)["state"] == ST_PENDING, \
            "Datei ohne Sidecar ist seit T-1604 planbar"
        eng = evaluate(candidate_info(iq), audspec, tmp, 0, 1.0)
        assert eng["state"] == ST_DEFERRED and eng["code"] == prepare_budget.CODE_TIME_WINDOW, eng
        viel = evaluate(candidate_info(fertig), audspec, tmp, 10 ** 15, None)
        assert viel["code"] == prepare_budget.CODE_DISK_NEED, viel

        # T-1607: das Register muss beide Typen kennen, und die Klassifikation
        # gilt NUR fuer mono - IQ darf gar nicht erst als Auftrag entstehen.
        assert [a["name"] for a in ARTIFACTS] == [ARTIFACT_AUDSPEC, ARTIFACT_KLASSE]
        assert klasse["kinds"] == (KIND_MONO,) and KIND_IQ in audspec["kinds"]
        assert artifact_by_name("gibt-es-nicht") is None
        k_gut = evaluate(candidate_info(fertig), klasse, tmp, 0, None)
        assert k_gut["state"] == ST_PENDING and k_gut["artifact_bytes"] > 0, k_gut
        assert k_gut["est_seconds"] < gut["est_seconds"], "Klassifikation muss billiger sein"

        # Fremde LEBENDE Sperre -> zurueckgestellt, nicht doppelt gebaut.
        with open(lock_path_for(fertig), "w", encoding="utf-8") as fh:
            json.dump({"pid": 1, "phase": "read", "done_frames": 1,
                       "total_frames": 2, "frac": 0.5,
                       "started_epoch": time.time(),
                       "heartbeat_epoch": time.time()}, fh)
        gesperrt = evaluate(candidate_info(fertig), audspec, tmp, 0, None)
        assert gesperrt == {"state": ST_DEFERRED, "code": CODE_LOCKED}, gesperrt
        print("T5 ok  (pending/recording_active/no_meta/time_window/disk_need/locked; "
              "Register kennt audspec+klasse, klasse nur fuer mono)")

        # --- T6: Aufraeumen ---------------------------------------------------
        bericht = cleanup_stale_locks(roots)
        assert lock_path_for(fertig) in bericht["kept"], bericht
        with open(lock_path_for(fertig), "w", encoding="utf-8") as fh:
            json.dump({"pid": 1, "heartbeat_epoch": time.time() - 3 * LOCK_STALE_S}, fh)
        with open(iq + ".audspec.tmp", "w", encoding="utf-8") as fh:
            fh.write("Rest eines Abbruchs")
        bericht = cleanup_stale_locks(roots)
        assert lock_path_for(fertig) in bericht["removed"], bericht
        assert iq + ".audspec.tmp" in bericht["removed"], bericht
        assert not os.path.exists(lock_path_for(fertig))
        print("T6 ok  (verwaiste Sperre und .tmp entfernt, LEBENDE Sperre bleibt)")

        # --- T7: Durchlauf mit Zeitbudget ------------------------------------
        # T-1604: nicht mehr EIN Bau je Zyklus, sondern so viele, wie ins
        # Budget passen. Die drei baubaren Dateien muessen in EINEM Durchlauf
        # fertig werden - mit der alten Regel haette das drei Minuten gedauert.
        journal7 = os.path.join(tmp, "logs", "t7.jsonl")
        cfg7 = load_config(os.path.join(tmp, "fehlt.json"))
        worker = Worker(repo_root=tmp, config=cfg7, journal_path=journal7)
        worker.roots = lambda: roots          # Sandbox statt echter Ablagen
        zyklus = worker.cycle()
        assert zyklus["scanned"] == 5, zyklus
        # T-1607: fuenf Auftraege, nicht drei - die drei Mono-Dateien bekommen
        # zusaetzlich eine Klassifikation, die IQ-Datei nicht.
        assert zyklus["built"] == 5, ("a/c/d, mono zusaetzlich klassifiziert", zyklus)
        assert zyklus["todo"] == 8, zyklus
        assert zyklus["deferred"].get(CODE_RECORDING_ACTIVE) == 2, zyklus
        assert zyklus["failed"] == 1, zyklus       # e_kaputt.wav
        assert zyklus["budget_exhausted"] is False, zyklus
        assert zyklus["seconds"] > 0.0, zyklus
        assert not needs_audspec(fertig) and not needs_audspec(iq), "nicht gebaut"
        assert not needs_audspec(ohne), "Datei ohne Sidecar wurde nicht gebaut"
        assert not needs_klasse(fertig) and not needs_klasse(ohne), "mono nicht klassifiziert"
        assert not os.path.exists(klasse_path_for(iq)), "IQ darf nicht klassifiziert werden"

        zyklus2 = worker.cycle()
        assert zyklus2["built"] == 0 and zyklus2["todo"] == 3, zyklus2
        assert zyklus2["deferred"].get(CODE_RECORDING_ACTIVE) == 2, zyklus2

        # Das Journal des Testlaufs liegt in der Sandbox, NICHT im echten
        # logs/-Ordner - genau der Fehler, den T-1604 behebt.
        assert os.path.isfile(journal7), "Testjournal nicht angelegt"
        assert journal_tail(50, journal7), "Testjournal leer"
        quellen = {e.get("meta_source") for e in journal_tail(50, journal7)}
        assert "wav" in quellen and "sidecar" in quellen, quellen

        zustand = worker.status()
        assert zustand["current"] is None and zustand["last_cycle"]["scanned"] == 5
        assert isinstance(zustand["journal"], list) and zustand["journal"], zustand
        assert zustand["enabled"] is True
        print("T7 ok  (fuenf Bauten in EINEM Zyklus - drei Audspec plus zwei "
              "Klassifikationen; IQ bleibt unklassifiziert, laufende Aufnahme "
              "zurueckgestellt, unlesbare Datei einmal als Fehler)")

        # --- T8: Zeitbudget und Fehlversuchsgrenze ---------------------------
        cfg8 = dict(cfg7)
        cfg8["cycle_budget_s"] = 1.0
        os.unlink(fertig + ".audspec")
        os.unlink(iq + ".audspec")
        os.unlink(ohne + ".audspec")
        worker8 = Worker(repo_root=tmp, config=cfg8, journal_path=journal7)
        worker8.roots = lambda: roots
        knapp = worker8.cycle()
        assert knapp["seconds"] <= cfg8["cycle_budget_s"] + 5.0, knapp
        assert knapp["built"] >= 1, knapp

        for _ in range(MAX_ATTEMPTS + 2):
            worker.cycle()
        # Der Zaehler haengt am PAAR (Pfad, Artefakt), nicht an der Datei.
        assert worker.attempts[(kaputt, ARTIFACT_AUDSPEC)] >= MAX_ATTEMPTS, worker.attempts
        letzter = worker.cycle()
        assert letzter["failed"] == 1, letzter
        # Die erschoepfte Datei wird nicht mehr angefasst; offen bleibt nur
        # noch die laufende Aufnahme.
        assert letzter["todo"] == 2, letzter
        assert letzter["deferred"].get(CODE_RECORDING_ACTIVE) == 2, letzter
        print(f"T8 ok  (Zeitbudget bricht den Zyklus ab; nach {MAX_ATTEMPTS} Fehlversuchen "
              "wird die Datei nicht mehr angefasst, bleibt aber sichtbar)")

        # --- T9 (T-1610): Platzlage im Zyklus, im Status, gedrosselt ---------
        journal9 = os.path.join(tmp, "logs", "t9.jsonl")
        worker9 = Worker(repo_root=tmp, config=load_config(os.path.join(tmp, "fehlt.json")),
                         journal_path=journal9)
        worker9.roots = lambda: roots
        z9 = worker9.cycle()
        assert "space" in z9, z9
        for feld in ("code", "total_bytes", "free_bytes", "floor_bytes",
                     "reserve_bytes", "available_bytes"):
            assert feld in z9["space"], (feld, z9["space"])
        assert z9["space"]["code"] in (prepare_budget.SPACE_OK,
                                       prepare_budget.SPACE_TIGHT,
                                       prepare_budget.SPACE_LOW), z9["space"]
        assert worker9.status()["space"]["code"] == z9["space"]["code"]

        # Ein frischer Worker hat noch keinen Zyklus - status() muss die Lage
        # trotzdem liefern, damit der Reiter nie ohne Platzangabe dasteht.
        leer9 = Worker(repo_root=tmp, config=worker9.config, journal_path=journal9)
        leer9.roots = lambda: roots
        assert leer9.status()["space"]["total_bytes"] > 0, leer9.status()["space"]

        # Drosselung: derselbe Code meldet sich nicht erneut, ein WECHSEL sofort.
        # Gezaehlt werden NUR space-Eintraege - der Zyklus schreibt daneben
        # weiter Zeilen fuer zurueckgestellte und fehlgeschlagene Dateien.
        def space_zeilen():
            return [e for e in journal_tail(200, journal9) if e.get("state") == "space"]

        vorher = len(space_zeilen())
        worker9.cycle()
        assert len(space_zeilen()) == vorher, "unveraenderte Lage erneut gemeldet"
        worker9._announce_space({"code": prepare_budget.SPACE_TIGHT,
                                 "free_bytes": 1, "floor_bytes": 2,
                                 "reserve_bytes": 0, "available_bytes": -1})
        eintraege = space_zeilen()
        assert eintraege[-1]["code"] == prepare_budget.SPACE_TIGHT, eintraege[-1]
        anzahl = len(eintraege)
        worker9._announce_space({"code": prepare_budget.SPACE_TIGHT,
                                 "free_bytes": 1, "floor_bytes": 2,
                                 "reserve_bytes": 0, "available_bytes": -1})
        assert len(space_zeilen()) == anzahl, "Drosselung greift nicht"
        worker9._space_said -= SPACE_WARN_REPEAT_S + 1.0     # Wiederholung faellig
        worker9._announce_space({"code": prepare_budget.SPACE_TIGHT,
                                 "free_bytes": 1, "floor_bytes": 2,
                                 "reserve_bytes": 0, "available_bytes": -1})
        assert len(space_zeilen()) == anzahl + 1, "Wiederholung fehlt"
        print(f"T9 ok  (Platzlage in jedem Zyklus und im Status, auch ohne Zyklus; "
              f"Wechsel meldet sofort, unveraenderte Lage erst nach "
              f"{SPACE_WARN_REPEAT_S:.0f} s)")

    print("\n[preparation_worker] Selbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    sys.exit(main())
