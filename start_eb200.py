#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
start_eb200.py - Sammelstarter für die EB200-Weboberflächen, mit Konsolen-
Statusboard.

Startet alle HTTP-Server mit EINEM Befehl:
  - waterfall.py               (Wasserfall,                Port 8000)  im Repo-Root
  - eb200_core/block_admin.py  (Frequenzblock-Verwaltung,  Port 8010)  in eb200_core/
  - analyze_sessions.py        (Session-Auswertung,        Port 8020)  im Repo-Root
  - analyze_spectrum.py        (Spektrum-Auswertung,       Port 8040)  im Repo-Root
  - manage_jobs.py             (Long-Run-Jobs-Uebersicht,  Port 8050)  im Repo-Root
  - analyze_mscan.py           (MSCAN-Ergebnisansicht,     Port 8060)  im Repo-Root
  - analyze_audio.py           (Audio-Auswertung,          Port 8070)  im Repo-Root
  - sdr_waterfall.py           (SDR-Wasserfall,            Port 8030)  im Repo-Root, optional
  - preparation_worker.py      (Aufbereitungs-Worker,      Port 8090)  im Repo-Root

Der Aufbereitungs-Worker ist der einzige Dienst OHNE Weboberflaeche: er
liefert nur /api/status und wird im Reiter "Aufbereitung" von :8050 angezeigt
(T-1609). Fuer das Statusboard aendert das nichts - der Port-Check ist ein
reiner TCP-Verbindungstest und braucht keine HTML-Seite. Der Browser-Autostart
betrifft ohnehin nur :8000.

Alle laufen als eigenständige Kindprozesse (subprocess). Dadurch behält jeder
Server seine eigene Signalbehandlung (Strg+C) und seinen eigenen Port - genau wie
beim getrennten Start in mehreren Fenstern. Neu ist nur: EIN Fenster, EIN Befehl.

Statusboard:
  Auf einem echten Terminal (TTY) unter Windows zeichnet der Starter ein
  periodisch aktualisiertes Statusboard: Zone 1 zeigt je Server Name, Port,
  Status (STARTET/LÄUFT/BEENDET/ÜBERSPRUNGEN), Port-Check-Ergebnis, PID und
  Fehleranzahl. Zone 2 zeigt die letzten Konsolenzeilen aller Server
  chronologisch gemischt. Zone 3 zeigt den zuletzt aufgetretenen Fehlerblock
  (Traceback bzw. auffällige stdout-Zeile) im Klartext, damit Tracebacks nie
  zwischen normalen Statusmeldungen verschwinden.
  Tastatur (nur Windows, nur Statusboard-Betrieb - siehe Plain-Modus unten):
  [1-8] filtert auf einen Server, [a] hebt den Filter auf, [e] zeigt in
  Zone 2 nur Fehler-/stderr-Zeilen, [n]/[p] blättern in Zone 3 durch die
  Fehlerblöcke des angezeigten Servers.
  [r] startet den GEFILTERTEN Server neu (ohne Filter nur ein Hinweis, erst
  [1-8] oder [R] waehlen), [R] startet ALLE Server neu, seriell in
  SERVERS-Reihenfolge (waterfall zuerst, wie beim Start) - siehe
  Neustart (T-306) unten. Waehrend ein Neustart laeuft, werden weitere
  [r]/[R]-Anschlaege ignoriert (Hinweiszeile "Neustart läuft").
  Das Neuzeichnen ersetzt nur den sichtbaren Bildschirm - das Scrollback bleibt
  unangetastet, weshalb zusätzlich JEDE Zeile ungefiltert in eine Logdatei
  unter logs/start_eb200_<Zeitstempel>.log geschrieben wird.

Neustart (T-306):
  [r]/[R] beenden den betroffenen Server (terminate(), bei Zeitueberschreitung
  kill() - siehe _restart()) und starten ihn per _spawn() neu, sobald sein
  Port frei ist (max. 5 s Wartezeit, sonst Abbruch mit Meldung statt eines
  Spawns auf einen weiterhin belegten Port). log/errors bleiben ueber den
  Neustart hinweg erhalten (History sichtbar), eine Trennzeile markiert den
  Schnitt. Der eigentliche Neustart laeuft in einem eigenen Daemon-Thread,
  damit das Board waehrenddessen weiterzeichnet. terminate() ist unter
  Windows TerminateProcess und bildet damit KEIN Strg+C nach (das liefert
  Windows nur an alle Prozesse dieser Konsole gemeinsam) - ein harter Stopp
  ist durch die Heartbeat-Sidecars der Server abgedeckt (siehe _restart()).

Browser-Autostart:
  Im Statusboard-Betrieb oeffnet der Starter nach erfolgreichem Port-Check des
  waterfall-Servers automatisch den Standard-Browser auf http://localhost:8000/
  (eigener Daemon-Thread, wartet dafuer bis zu 15 s). Abschaltbar mit
  --no-browser. Im Plain- bzw. Nicht-TTY-Betrieb (Umleitung, Dienstbetrieb,
  spaeterer Autostart T-303) entfaellt der Browser-Autostart automatisch -
  dort darf sich unbeaufsichtigt kein Browserfenster oeffnen.

Lebenszeit-Kopplung (T-305):
  Der Starter haengt sich beim Start selbst in ein Windows-Job-Objekt mit
  KILL_ON_JOB_CLOSE. Alle Server-Kindprozesse (und deren Kinder, z. B.
  hackrf_transfer aus sdr_waterfall.py) erben die Mitgliedschaft und werden
  vom Kernel beendet, sobald der Starter endet - gleichgueltig wie (Strg+C,
  Task-Manager, Schliessen des Fensters, Absturz). Waisenprozesse auf den
  Ports 8000-8090 (inkl. 8060 und 8090) kann es damit nicht mehr geben. Schlaegt die
  Job-Zuordnung fehl (Starter laeuft bereits in einem fremden Job ohne
  Nesting, vor Windows 8), laeuft alles mit Warnung wie bisher weiter.

Plain-Modus (--plain, oder automatisch ohne TTY, z. B. bei Umleitung in eine
Datei): klassischer Zeilenstrom mit vorangestelltem [name] bzw. [name!] für
stderr-Zeilen - wie beim bisherigen Starter. Auch hier wird eine Logdatei
geschrieben. KEINE Tastaturabfrage in diesem Modus (msvcrt.kbhit() braucht
eine echte Konsole) - damit auch KEIN Neustart per [r]/[R]; ein Neustart
bleibt hier nur ueber ein komplettes Beenden (Strg+C) und einen erneuten
Aufruf moeglich.

Selbsttest (--selftest): startet zwei winzige Dummy-Skripte (kein echter
MoniScope-Server nötig) und prüft Statuswechsel, Fehlererkennung (Traceback-Blöcke,
stdout-Heuristik, WARNUNG-Ausnahme) und Port-Check anhand derselben Klassen-
und Pump-Logik wie im Normalbetrieb.

Arbeitsverzeichnis (cwd):
  Alle Server laufen mit cwd = Repo-Root. block_admin.py liegt zwar in
  eb200_core/, importiert seine Geschwistermodule aber als eb200_core.xxx und
  nimmt beim Direktaufruf das Repo-Root selbst in den Suchpfad auf (T-2000).

Warum ist der SDR-Wasserfall "optional"?
  sdr_waterfall.py setzt vorhandene SDR-Hardware und deren Vendor-CLI voraus
  (hackrf_transfer bzw. airspy_rx; SoapySDR wurde verworfen, siehe
  Entscheidungslog 2026-08). Fehlt die Datei, wird der Dienst still übersprungen
  - die Kern-Server (Wasserfall, Blockverwaltung) laufen unabhängig davon weiter.
  Ist die Datei zwar vorhanden, aber kein Gerät oder kein CLI erreichbar, startet
  der Dienst trotzdem und meldet im Browser schlicht "kein Gerät verfügbar" - das
  ist Sache von sdr_waterfall.py, nicht des Starters. numpy ist KEINE
  Zusatzabhängigkeit dieses Dienstes mehr, sondern Grundvoraussetzung der
  gesamten Suite (siehe requirements.txt und Entscheidungslog 2026-09-03).

Warum stdout und stderr getrennte Pipes (statt stderr=STDOUT)?
  Python schreibt Tracebacks und logging-Ausgaben auf stderr, reine
  print()-Statusmeldungen auf stdout. Diese Trennung liefert die Kanal-
  information kostenlos mit und ist zuverlässiger als eine Mustererkennung
  im gemischten Strom.

Beenden:
  Strg+C - Windows liefert das Ereignis an alle Prozesse dieser Konsole, also
  auch an die Kindprozesse. Jeder Server fährt daraufhin selbst sauber herunter
  (inkl. UDP-Teardown von waterfall.py). Der Starter wartet noch kurz auf
  sauberes Beenden und greift nur bei Nachzüglern hart ein. Beendet sich ein
  Kindprozess von selbst waehrend des Betriebs, laeuft der Starter mit den
  uebrigen Servern weiter (Status BEENDET (Exit n) im Board). Beim Beenden
  druckt der Starter zusätzlich eine Fehlerzusammenfassung je Server im
  Klartext, die im Scrollback stehen bleibt.

Aufruf (aus dem Repo-Root E:\\EB200\\EB200repo):
    python start_eb200.py              Statusboard (bzw. Zeilenstrom ohne TTY)
    python start_eb200.py --plain      erzwingt den klassischen Zeilenstrom
    python start_eb200.py --selftest   Selbsttest mit Dummy-Skripten
    python start_eb200.py --no-browser   ohne Browser-Autostart
  Alternativ start_eb200.bat per Doppelklick starten (cd ins Repo-Root,
  reicht alle Argumente an python start_eb200.py durch).
"""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

# T-2003: Datenwurzel aus --data-dir (eb200_core/paths.py wertet sys.argv beim
# Import selbst aus und bricht bei ungueltiger Angabe mit Exitcode 1 ab).
from eb200_core import paths
from eb200_core.data_seed import seed_known_freq

try:
    import msvcrt  # nur Windows - Tastaturabfrage ohne Enter
except ImportError:
    msvcrt = None

# Repo-Root = das Verzeichnis, in dem diese Datei liegt.
REPO_ROOT = Path(__file__).resolve().parent

# Die zu startenden Server als Liste aus
# (Anzeige-Name, Skriptdatei, Arbeitsverzeichnis, optional?, Port).
# optional=True: fehlt das Skript, wird der Dienst übersprungen statt abzubrechen.
# Neue Server (z. B. später ein WS2-Aufnahmedienst) lassen sich hier einfach ergänzen.
SERVERS = [
    ("waterfall",        REPO_ROOT / "waterfall.py",        REPO_ROOT,  False, 8000),
    ("block_admin",      REPO_ROOT / "eb200_core" / "block_admin.py", REPO_ROOT, False, 8010),
    ("analyze_sessions", REPO_ROOT / "analyze_sessions.py", REPO_ROOT,  False, 8020),
    ("analyze_spectrum", REPO_ROOT / "analyze_spectrum.py", REPO_ROOT,  False, 8040),
    ("manage_jobs",      REPO_ROOT / "manage_jobs.py",      REPO_ROOT,  False, 8050),
    ("analyze_mscan",    REPO_ROOT / "analyze_mscan.py",    REPO_ROOT,  False, 8060),
    ("analyze_audio",    REPO_ROOT / "analyze_audio.py",    REPO_ROOT,  False, 8070),
    ("sdr_waterfall",    REPO_ROOT / "sdr_waterfall.py",    REPO_ROOT,  True,  8030),
    # T-1605: Aufbereitungs-Worker. Nicht optional - die Datei gehoert seit
    # T-1603 fest zum Repo. Kein Eintrag in irgendeiner Navigation: der Dienst
    # hat keine Seite (siehe Moduldoku oben).
    ("preparation_worker", REPO_ROOT / "preparation_worker.py", REPO_ROOT, False, 8090),
]

# Gemeinsamer Lock fuer alles, was von mehreren Threads gelesen/geschrieben wird:
# ServerState-Felder (log/errors/error_count/port_ok/status), UIState-Felder und
# die Logdatei. Pump-Threads, Port-Check-Thread und Hauptthread teilen sich ihn.
LOCK = threading.Lock()


class Status(Enum):
    STARTET = "STARTET"
    LAEUFT = "LAEUFT"
    BEENDET = "BEENDET"
    UEBERSPRUNGEN = "UEBERSPRUNGEN"


@dataclass
class ServerState:
    name: str
    port: int
    script: Path
    cwd: Path
    optional: bool
    proc: Optional[subprocess.Popen] = None
    status: Status = Status.STARTET
    exit_code: Optional[int] = None
    port_ok: bool = False
    log: deque = field(default_factory=lambda: deque(maxlen=500))
    errors: list = field(default_factory=list)  # Liste von Fehlerbloecken (je Block: Liste von Zeilen)
    error_count: int = 0
    # Nur vom stderr-Pump-Thread dieses Servers verwendet (Traceback-Blockbildung).
    _in_traceback: bool = field(default=False, repr=False)
    _err_block: Optional[list] = field(default=None, repr=False)


@dataclass
class UIState:
    filter_server: Optional[str] = None
    only_errors: bool = False
    last_error_server: Optional[str] = None
    zone3_index: int = 0
    dirty: bool = True
    _zone3_target_name: Optional[str] = field(default=None, repr=False)
    # T-306: laeuft gerade ein Neustart ([r]/[R])? Waehrenddessen ignoriert
    # _handle_keys() weitere [r]/[R]-Anschlaege (der Neustart-Thread haelt
    # dieses Feld, nicht LOCK, waehrend er blockierend terminate()/_spawn()
    # aufruft - das Board zeichnet unveraendert weiter).
    restarting: bool = False


# ---------------------------------------------------------------------------
# Fehlererkennung
# ---------------------------------------------------------------------------

# stdout-Heuristik: Wortanfang, case-sensitive (siehe Aufgabenstellung).
_STDOUT_ERROR_RE = re.compile(r"\b(FEHLER|ERROR|Exception|Traceback)\b")
_WARN_RE = re.compile(r"\b(WARNUNG|Warning)\b")


def _create_kill_on_close_job():
    """
    Erzeugt ein Windows-Job-Objekt mit KILL_ON_JOB_CLOSE und liefert das
    Handle (int) oder None bei Fehlschlag/Nicht-Windows. Von der
    Selbst-Zuordnung (_bind_lifetime_to_job) getrennt, damit der Selbsttest
    die Kill-Mechanik an einem EIGENEN Job mit einem Dummy-Kind pruefen
    kann, ohne den Testprozess selbst zu binden.

    WICHTIG: argtypes/restype mit c_void_p sind Pflicht - ohne sie stutzt
    ctypes Handles auf 64-Bit-Windows auf 32 Bit (Default c_int), was
    sporadisch fehlschlaegt.
    """
    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        kernel32.CloseHandle(job)
        return None
    return job


def _bind_lifetime_to_job() -> str:
    """
    T-305: haengt den Starter SELBST in ein KILL_ON_JOB_CLOSE-Job-Objekt.
    Alle danach gestarteten Kinder (Server) und Enkel (hackrf_transfer)
    erben die Mitgliedschaft; stirbt der Starter - egal wie -, schliesst
    der Kernel das letzte Job-Handle und beendet alle Mitglieder. Das
    Handle wird darum bewusst NIE geschlossen: sein Verfall beim
    Prozessende IST der Aufraeummechanismus. Liefert eine Statuszeile
    fuer Konsole/Logdatei; bei Fehlschlag Warnung + heutiges Verhalten.
    """
    if os.name != "nt":
        return "Job-Objekt uebersprungen (kein Windows)"
    job = _create_kill_on_close_job()
    if job is None:
        return ("WARNUNG: Job-Objekt nicht verfuegbar - Kindprozesse "
                "ueberleben eine harte Starter-Beendigung")
    kernel32 = ctypes.windll.kernel32
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        kernel32.CloseHandle(job)
        return ("WARNUNG: Job-Zuordnung fehlgeschlagen (laeuft der Starter "
                "bereits in einem fremden Job?) - Kindprozesse ueberleben "
                "eine harte Starter-Beendigung")
    return "Job-Objekt aktiv: Kindprozesse enden mit dem Starter (KILL_ON_JOB_CLOSE)"


def _feed_error_detector(state: ServerState, channel: str, line: str) -> bool:
    """
    Wertet eine einzelne Konsolenzeile aus und pflegt state.errors/error_count.
    Gibt True zurueck, wenn dabei ein Fehlerblock neu abgeschlossen wurde
    (fuer die Auswahl "zuletzt fehlerhafter Server" in Zone 3).
    Muss unter LOCK aufgerufen werden.
    """
    if channel == "err":
        if state._in_traceback:
            state._err_block.append(line)
            # Block endet an der ersten nicht eingerueckten Zeile, die nicht
            # selbst mit "Traceback" beginnt (chained tracebacks) - diese
            # Zeile (typischerweise "XxxError: ...") gehoert noch zum Block.
            if not line[:1].isspace() and not line.startswith("Traceback"):
                state.errors.append(state._err_block)
                state.error_count += 1
                state._in_traceback = False
                state._err_block = None
                return True
            return False
        if line.startswith("Traceback"):
            state._in_traceback = True
            state._err_block = [line]
            return False
        # Einzelne stderr-Zeile ohne Traceback bildet ihren eigenen Block.
        state.errors.append([line])
        state.error_count += 1
        return True
    else:
        if _STDOUT_ERROR_RE.search(line):
            state.errors.append([line])
            state.error_count += 1
            return True
        return False


def _pump(state: ServerState, channel: str, stream, log_fh, ui: UIState, echo: bool) -> None:
    """
    Liest zeilenweise die Ausgabe eines Kanals (stdout ODER stderr) eines
    Kindprozesses. Schreibt ausschliesslich in state/ui (nie direkt auf die
    Konsole), ausser im Plain-Modus (echo=True), wo zusaetzlich mit
    [name]/[name!]-Praefix direkt ausgegeben wird. Läuft in einem eigenen
    Thread je Server und Kanal.
    """
    prefix = f"[{state.name}]" if channel == "out" else f"[{state.name}!]"
    assert stream is not None
    for raw_line in stream:
        line = raw_line.rstrip("\r\n")
        ts = datetime.now()
        with LOCK:
            state.log.append((ts, channel, line))
            log_fh.write(f"{ts:%Y-%m-%d %H:%M:%S} [{state.name}/{channel}] {line}\n")
            if _feed_error_detector(state, channel, line):
                ui.last_error_server = state.name
            ui.dirty = True
        if echo:
            print(f"{prefix} {line}")
    stream.close()


# ---------------------------------------------------------------------------
# Port-Check
# ---------------------------------------------------------------------------

def _check_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _port_check_loop(states: list[ServerState], ui: UIState, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        for st in states:
            if st.proc is None:
                continue
            ok = _check_port(st.port)
            if ok != st.port_ok:
                with LOCK:
                    st.port_ok = ok
                    _recompute_status(st)
                    ui.dirty = True
        stop_event.wait(2.0)


def _recompute_status(st: ServerState) -> None:
    """Muss unter LOCK aufgerufen werden."""
    if st.proc is None:
        st.status = Status.UEBERSPRUNGEN
    elif st.exit_code is not None:
        st.status = Status.BEENDET
    elif st.port_ok:
        st.status = Status.LAEUFT
    else:
        st.status = Status.STARTET


def _check_children(states: list[ServerState], ui: UIState) -> None:
    for st in states:
        if st.proc is None or st.exit_code is not None:
            continue
        rc = st.proc.poll()
        if rc is not None:
            with LOCK:
                st.exit_code = rc
                _recompute_status(st)
                st.log.append((datetime.now(), "out", f"Prozess beendet (Exit {rc})."))
                ui.dirty = True


# ---------------------------------------------------------------------------
# Darstellung (Statusboard)
# ---------------------------------------------------------------------------

_COLORS = {"red": "31", "green": "32", "yellow": "33", "gray": "90", "cyan": "36"}


def _c(text: str, color: Optional[str]) -> str:
    if not text or color is None:
        return text
    return f"\x1b[{_COLORS[color]}m{text}\x1b[0m"


def _row(segments: list, width: int) -> str:
    """Baut eine Rahmenzeile "│ ... │" aus (text, farbe)-Segmenten, auf
    sichtbare Breite (ohne ANSI-Codes) begrenzt/aufgefuellt."""
    inner = max(0, width - 4)
    rendered = ""
    vis = 0
    for text, color in segments:
        if vis >= inner:
            break
        t = text[: inner - vis]
        vis += len(t)
        rendered += _c(t, color)
    rendered += " " * (inner - vis)
    return "│ " + rendered + " │"


def _framed(left: str, right: str, width: int, title: str) -> str:
    inner = max(0, width - 2)
    label = f"─ {title} " if title else ""
    if len(label) > inner:
        label = label[:inner]
    return left + label + "─" * max(0, inner - len(label)) + right


def _box_top(width: int, title: str) -> str:
    return _framed("┌", "┐", width, title)


def _box_mid(width: int, title: str) -> str:
    return _framed("├", "┤", width, title)


def _box_bottom(width: int) -> str:
    return "└" + "─" * max(0, width - 2) + "┘"


def _status_text(st: ServerState) -> tuple[str, Optional[str]]:
    if st.status == Status.LAEUFT:
        return "● läuft", "green"
    if st.status == Status.STARTET:
        return "◐ startet", "yellow"
    if st.status == Status.BEENDET:
        return f"✖ BEENDET (Exit {st.exit_code})", "red"
    return "– übersprungen (optional)", "gray"


def _port_text(st: ServerState) -> tuple[str, Optional[str]]:
    if st.status == Status.UEBERSPRUNGEN:
        return "", None
    return ("Port OK", "green") if st.port_ok else ("Port –", "red")


def _server_row(st: ServerState, width: int) -> str:
    status_text, status_color = _status_text(st)
    port_text, port_color = _port_text(st)
    pid_text = f"PID {st.proc.pid}" if st.proc is not None else "PID –"
    err_color = "red" if st.error_count > 0 else None
    segs = [
        (f"{st.name:<17}", None),
        (f":{st.port:<6}", None),
        (f"{status_text:<26}", status_color),
        (f"{port_text:<9}", port_color),
        (f"{pid_text:<12}", None),
        (f"{st.error_count} Fehler", err_color),
    ]
    return _row(segs, width)


def _msg_row(entry: tuple, width: int) -> str:
    ts, name, channel, text = entry
    color = "red" if channel == "err" else ("yellow" if _WARN_RE.search(text) else None)
    prefix = f"{ts:%H:%M:%S} {name:<17} "
    return _row([(prefix, None), (text, color)], width)


def _zone3_target(ui: UIState, states: list[ServerState]) -> Optional[ServerState]:
    if ui.filter_server:
        for st in states:
            if st.name == ui.filter_server:
                return st
        return None
    if ui.last_error_server:
        for st in states:
            if st.name == ui.last_error_server:
                return st
    return None


def _zone3_title(ui: UIState, target: Optional[ServerState]) -> str:
    if target is None or not target.errors:
        return "Fehler"
    return f"Fehler ({target.name}) – Block {ui.zone3_index + 1} von {len(target.errors)}"


def _zone3_body(target: Optional[ServerState], ui: UIState, height: int, width: int) -> list:
    rows = []
    if target is None or not target.errors:
        rows.append(_row([("keine Fehler", None)], width))
    else:
        block = target.errors[ui.zone3_index]
        for line in block[:height]:
            rows.append(_row([(line, "red")], width))
    while len(rows) < height:
        rows.append(_row([], width))
    return rows[:height]


# Zwei Zeilen statt einer (T-306): "[1-8] ... [n/p] ..." plus "[r]/[R] ..."
# passt zusammen nicht mehr in 80 Spalten (rein informativ - width kommt aus
# dem echten Terminal, siehe _render(); ein schmaleres Terminal schneidet
# ohnehin per [:width] ab).
_FOOTER1 = ("[1-8] Server filtern  [a] alle  [e] nur Fehler  "
            "[n/p] Fehlerblock vor/zurück")
_FOOTER2 = "[r] Neustart  [R] alle neu  [Strg+C] beenden"


def _render(states: list[ServerState], ui: UIState, log_path: Path) -> None:
    width, height = shutil.get_terminal_size(fallback=(100, 30))
    width = max(40, width)
    err_h = 10 if height >= 30 else 6

    with LOCK:
        server_rows = [_server_row(st, width) for st in states]
        merged = [(ts, st.name, ch, text) for st in states for (ts, ch, text) in st.log]
        target = _zone3_target(ui, states)
        if target is not None and target.errors:
            if ui._zone3_target_name != target.name:
                ui.zone3_index = len(target.errors) - 1
                ui._zone3_target_name = target.name
            ui.zone3_index = max(0, min(ui.zone3_index, len(target.errors) - 1))
        else:
            ui._zone3_target_name = target.name if target is not None else None
        title3 = _zone3_title(ui, target)
        body3 = _zone3_body(target, ui, err_h, width)

    merged.sort(key=lambda e: e[0])
    if ui.filter_server:
        merged = [e for e in merged if e[1] == ui.filter_server]
    if ui.only_errors:
        merged = [e for e in merged if e[2] == "err" or _STDOUT_ERROR_RE.search(e[3])]

    fixed = 1 + len(states) + 1 + 1 + err_h + 1 + 3
    k = max(1, height - fixed)
    msg_rows = [_msg_row(e, width) for e in merged[-k:]]
    while len(msg_rows) < k:
        msg_rows.append(_row([], width))

    title2 = "Letzte Meldungen"
    if ui.filter_server:
        title2 += f" – Filter: {ui.filter_server}"

    out = [_box_top(width, f"MoniScope  {datetime.now():%H:%M:%S}")]
    out.extend(server_rows)
    out.append(_box_mid(width, title2))
    out.extend(msg_rows)
    out.append(_box_mid(width, title3))
    out.extend(body3)
    out.append(_box_bottom(width))
    out.append(_FOOTER1[:width])
    out.append(_FOOTER2[:width])
    out.append(f"Log: {log_path}"[:width])

    frame = "\x1b[H" + "\r\n".join(line + "\x1b[K" for line in out)
    sys.stdout.write(frame)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Tastatur
# ---------------------------------------------------------------------------

def _nav(ui: UIState, states: list[ServerState], delta: int) -> None:
    target = _zone3_target(ui, states)
    if target is None or not target.errors:
        return
    ui.zone3_index = max(0, min(ui.zone3_index + delta, len(target.errors) - 1))
    ui.dirty = True


def _request_restart(ch: str, ui: UIState, states: list[ServerState], log_fh, echo: bool) -> None:
    """
    Reagiert auf [r] (gefilterten Server neu starten) bzw. [R] (alle neu,
    seriell in SERVERS-Reihenfolge - wie beim Start). Muss unter LOCK
    aufgerufen werden (siehe _handle_keys()); der eigentliche Neustart
    laeuft danach in einem EIGENEN Daemon-Thread (_restart() blockiert bis
    zu ~13 s je Server), damit das Board waehrenddessen weiterzeichnet.

    Hinweiszeilen ohne Server-Bezug (kein Filter gewaehlt, Neustart laeuft
    bereits) haben keinen eigenen Kanal - sie haengen an states[0] (immer
    "waterfall", siehe SERVERS): Zone 2 zeigt sie dort mit an, solange KEIN
    Filter aktiv ist, und genau das ist in beiden Faellen hier gegeben.
    """
    if ui.restarting:
        _log_line(states[0], ui, log_fh, "out", "[start_eb200] Neustart laeuft")
        return

    if ch == "R":
        targets = list(states)
    else:
        if not ui.filter_server:
            _log_line(states[0], ui, log_fh, "out",
                      "[start_eb200] Zuerst Server waehlen [1-8] oder [R] fuer alle")
            return
        targets = [st for st in states if st.name == ui.filter_server]

    ui.restarting = True

    def _worker():
        try:
            for st in targets:
                _restart(st, ui, log_fh, echo)
        finally:
            with LOCK:
                ui.restarting = False
                ui.dirty = True

    threading.Thread(target=_worker, daemon=True).start()


def _handle_keys(ui: UIState, states: list[ServerState], log_fh, echo: bool) -> None:
    if msvcrt is None:
        return
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        with LOCK:
            if ch in "12345678":
                idx = int(ch) - 1
                if idx < len(states):
                    ui.filter_server = states[idx].name
                    ui.dirty = True
            elif ch in ("a", "A"):
                ui.filter_server = None
                ui.only_errors = False
                ui.dirty = True
            elif ch in ("e", "E"):
                ui.only_errors = True
                ui.dirty = True
            elif ch in ("n", "N"):
                _nav(ui, states, +1)
            elif ch in ("p", "P"):
                _nav(ui, states, -1)
            elif ch in ("r", "R"):
                _request_restart(ch, ui, states, log_fh, echo)


# ---------------------------------------------------------------------------
# ANSI aktivieren / Board- bzw. Plain-Schleife
# ---------------------------------------------------------------------------

def _ansi_available() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
            return False
        return True
    except Exception:
        return False


def _board_loop(states: list[ServerState], ui: UIState, stop_event: threading.Event,
                 log_path: Path, log_fh) -> None:
    last_render = 0.0
    while not stop_event.is_set():
        # echo=False: das Statusboard laeuft nur, wenn board_mode True ist -
        # das schliesst den Plain-Modus (echo=True) aus (siehe _run()).
        _handle_keys(ui, states, log_fh, echo=False)
        _check_children(states, ui)
        now = time.monotonic()
        if ui.dirty or now - last_render >= 0.5:
            _render(states, ui, log_path)
            ui.dirty = False
            last_render = now
        time.sleep(0.05)


def _plain_wait_loop(states: list[ServerState], stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        for st in states:
            if st.proc is None or st.exit_code is not None:
                continue
            rc = st.proc.poll()
            if rc is not None:
                with LOCK:
                    st.exit_code = rc
                    _recompute_status(st)
                print(f"[start_eb200] {st.name} beendet (Exit {rc}).")
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Normalbetrieb
# ---------------------------------------------------------------------------

def _log_line(st: ServerState, ui: UIState, log_fh, channel: str, text: str) -> None:
    """Traegt EINE Statuszeile in st.log und die Logdatei ein - derselbe Weg,
    den _pump() fuer normale Serverausgaben nutzt (Muster auch in
    _browser_open_when_ready), nur fuer Meldungen, die NICHT aus einem
    Pump-Thread kommen (Neustart-Meldungen, siehe _restart()). Muss unter
    LOCK aufgerufen werden."""
    ts = datetime.now()
    st.log.append((ts, channel, text))
    log_fh.write(f"{ts:%Y-%m-%d %H:%M:%S} [{st.name}/{channel}] {text}\n")
    ui.dirty = True


# T-2013: optionaler Override der EB200-Adresse (--eb200-host), nur fuer den
# Wasserfall - er ist der einzige Server mit Geraeteverbindung.
EB200_HOST_OVERRIDE: Optional[str] = None


def _spawn(st: ServerState, ui: UIState, log_fh, echo: bool) -> list:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    befehl = [sys.executable, str(st.script), "--data-dir", paths.DATA_ROOT]
    if st.name == "waterfall" and EB200_HOST_OVERRIDE:
        befehl += ["--eb200-host", EB200_HOST_OVERRIDE]
    st.proc = subprocess.Popen(
        # T-2003: jeder Server bekommt die Datenwurzel ausdruecklich und absolut.
        befehl,
        cwd=str(st.cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    t_out = threading.Thread(target=_pump, args=(st, "out", st.proc.stdout, log_fh, ui, echo), daemon=True)
    t_err = threading.Thread(target=_pump, args=(st, "err", st.proc.stderr, log_fh, ui, echo), daemon=True)
    t_out.start()
    t_err.start()
    return [t_out, t_err]


def _restart(st: ServerState, ui: UIState, log_fh, echo: bool) -> None:
    """
    Beendet den Server (falls er noch laeuft) und startet ihn per _spawn()
    neu - Kernstueck von [r]/[R] im Statusboard (T-306). Blockiert bis zu
    ~13 s (5 s Terminate-Wartezeit + 5 s Port-Wartezeit + Spawn) und MUSS
    daher aus einem eigenen Thread aufgerufen werden (siehe _handle_keys()),
    nie aus dem Board-Loop selbst.

    terminate() bildet KEIN Strg+C nach: Windows liefert das Strg+C-Ereignis
    an alle Prozesse dieser Konsole gemeinsam, nicht gezielt an EINEN
    Kindprozess - ein gezielter Neustart hat dafuer kein Aequivalent.
    terminate() ist unter Windows TerminateProcess, also ein harter Stopp
    ohne Chance auf einen sauberen Shutdown-Pfad im Server. Das ist
    hinnehmbar: die Server schreiben ihre Sidecars/Manifeste laufend beim
    Heartbeat (siehe eb200_core/job_store.py, eb200_core/audio_wav.py) -
    ein harter Stopp verliert hoechstens den letzten Heartbeat, keinen
    unbemerkten Datenverlust.

    Fuer Status UEBERSPRUNGEN (optionaler Server ohne Skript, siehe _run())
    gibt es nichts zu beenden/starten - nur eine Hinweiszeile.
    """
    if st.status == Status.UEBERSPRUNGEN:
        with LOCK:
            _log_line(st, ui, log_fh, "out",
                      f"[start_eb200] {st.name}: kein Skript vorhanden - kein Neustart moeglich")
        return

    if st.proc is not None and st.proc.poll() is None:
        st.proc.terminate()
        try:
            st.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            st.proc.kill()
            st.proc.wait(timeout=3)

    # Port-Freigabe abwarten (TIME_WAIT bzw. ein fremder Prozess koennte ihn
    # kurzzeitig halten) - max. 5 s in 0,25-s-Schritten, danach Abbruch statt
    # eines Spawns, der sofort wieder auf denselben Port stossen wuerde.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _check_port(st.port):
            break
        time.sleep(0.25)
    else:
        with LOCK:
            _log_line(st, ui, log_fh, "out",
                      f"[start_eb200] {st.name}: Port {st.port} nach 5 s noch belegt - Neustart abgebrochen")
        return

    with LOCK:
        st.proc = None
        st.status = Status.STARTET
        st.exit_code = None
        st.port_ok = False
        st._in_traceback = False
        st._err_block = None
        # log/errors bleiben erhalten (History sichtbar) - eine Trennzeile
        # markiert den Neustart in Zone 2 UND in der Logdatei.
        _log_line(st, ui, log_fh, "out", f"---- Neustart {datetime.now():%H:%M:%S} ----")

    _spawn(st, ui, log_fh, echo)

    with LOCK:
        _log_line(st, ui, log_fh, "out",
                  f"[start_eb200] {st.name} neu gestartet (PID {st.proc.pid})")


def _shutdown(states: list[ServerState], log_path: Path, log_fh) -> None:
    for st in states:
        if st.proc is None:
            continue
        try:
            st.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            st.proc.terminate()
            try:
                st.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                st.proc.kill()
        with LOCK:
            if st.exit_code is None:
                st.exit_code = st.proc.poll()
                _recompute_status(st)

    print("\n[start_eb200] Fehlerzusammenfassung:")
    for st in states:
        if st.proc is None:
            print(f"  {st.name:<17} übersprungen (optional)")
            continue
        print(f"  {st.name:<17} Exit {st.exit_code}   {st.error_count} Fehlerblock(e)")
    print(f"[start_eb200] Logdatei: {log_path}")
    print("[start_eb200] beendet.")
    log_fh.close()


def _browser_open_when_ready(states: list[ServerState], ui: UIState, log_fh) -> None:
    """
    Eigener Daemon-Thread (siehe _run): wartet bis zu 15 s (0,5-s-Schritte)
    auf den Port-Check des waterfall-Servers (Port 8000) und oeffnet dann
    den Standard-Browser auf http://localhost:8000/. Die eine resultierende
    Statuszeile wird ueber denselben Weg wie _check_children eingetragen
    (unter LOCK an st.log anhaengen, ui.dirty = True), zusaetzlich in die
    Logdatei geschrieben - KEIN print(): der Thread laeuft parallel zum
    Statusboard, eine rohe Konsolenausgabe wuerde dessen Zeichnen zerstoeren.
    """
    wf = next((st for st in states if st.name == "waterfall"), None)
    if wf is None:
        return   # defensiv - sollte laut SERVERS nie eintreten

    deadline = time.monotonic() + 15.0
    ready = False
    while time.monotonic() < deadline:
        with LOCK:
            ready = wf.port_ok
        if ready:
            break
        time.sleep(0.5)

    if ready:
        webbrowser.open("http://localhost:8000/")
        text = "Browser geoeffnet: http://localhost:8000/"
    else:
        text = "Browser nicht geoeffnet - Port 8000 nach 15 s nicht erreichbar."
    ts = datetime.now()
    with LOCK:
        wf.log.append((ts, "out", text))
        log_fh.write(f"{ts:%Y-%m-%d %H:%M:%S} [{wf.name}/out] {text}\n")
        ui.dirty = True


def _run(plain: bool, no_browser: bool = False) -> int:
    logs_dir = Path(paths.data_path("logs"))
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / f"start_eb200_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_fh = open(log_path, "w", encoding="utf-8", newline="", buffering=1)

    job_msg = _bind_lifetime_to_job()
    print(f"[start_eb200] {job_msg}")
    log_fh.write(f"[start_eb200] {job_msg}\n")
    print(f"[start_eb200] Datenwurzel: {paths.DATA_ROOT}")
    log_fh.write(f"[start_eb200] Datenwurzel: {paths.DATA_ROOT}\n")
    # T-2004: eine neue Datenwurzel bekommt die mitgelieferten Kataloge als
    # Vorlage - einmal hier, bevor die Server starten (kein Wettlauf).
    erststart = seed_known_freq()
    if erststart["angelegt"]:
        meldung = (f"[start_eb200] Neue Datenwurzel: {len(erststart['kopiert'])} "
                   f"mitgelieferte Katalogdateien nach known_freq/ kopiert")
        print(meldung)
        log_fh.write(meldung + "\n")

    board_mode = (not plain) and _ansi_available()
    ui = UIState()

    print("Starte MoniScope-Weboberflächen ...")
    states: list[ServerState] = []
    for name, script, cwd, optional, port in SERVERS:
        st = ServerState(name=name, port=port, script=script, cwd=cwd, optional=optional)
        if not script.exists():
            if optional:
                st.status = Status.UEBERSPRUNGEN
                print(f"[start_eb200] {name} nicht gefunden - übersprungen (optional).")
                states.append(st)
                continue
            print(f"[start_eb200] FEHLER: {script} nicht gefunden - Abbruch.")
            for done in states:
                if done.proc is not None:
                    done.proc.terminate()
            log_fh.close()
            return 1
        _spawn(st, ui, log_fh, echo=plain)
        states.append(st)
        print(f"[start_eb200] {name} gestartet (PID {st.proc.pid})")

    stop_event = threading.Event()
    threading.Thread(target=_port_check_loop, args=(states, ui, stop_event), daemon=True).start()

    # Browser-Autostart nur im beaufsichtigten Statusboard-Betrieb: --plain
    # und Nicht-TTY (Umleitung, Dienstbetrieb, spaeterer Autostart T-303)
    # bedeuten unbeaufsichtigten Betrieb - dort darf kein Browserfenster
    # aufgehen. board_mode ist genau (not plain) and _ansi_available() und
    # deckt beides ab.
    if board_mode and not no_browser:
        threading.Thread(target=_browser_open_when_ready,
                         args=(states, ui, log_fh), daemon=True).start()

    if board_mode:
        print(f"[start_eb200] Statusboard aktiv. Log: {log_path}")
    print("[start_eb200] Beenden mit Strg+C.\n")

    ansi_ready = False
    if board_mode:
        sys.stdout.write("\x1b[?25l\x1b[2J")
        sys.stdout.flush()
        ansi_ready = True

    try:
        if board_mode:
            _board_loop(states, ui, stop_event, log_path, log_fh)
        else:
            _plain_wait_loop(states, stop_event)
    except KeyboardInterrupt:
        print("\n[start_eb200] Strg+C - warte auf sauberes Herunterfahren aller Server ...")
    finally:
        stop_event.set()
        if ansi_ready:
            sys.stdout.write("\x1b[?25h\n")
            sys.stdout.flush()
        _shutdown(states, log_path, log_fh)

    return 0


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def _selftest() -> int:
    print("[start_eb200] Selbsttest ...")
    tmp = Path(tempfile.mkdtemp(prefix="eb200_selftest_"))
    ok_script = tmp / "dummy_ok.py"
    err_script = tmp / "dummy_err.py"
    ok_script.write_text(
        "import time\n"
        "print('status 1')\n"
        "time.sleep(0.2)\n"
        "print('status 2')\n"
        "time.sleep(0.2)\n"
        "print('status 3')\n"
        "time.sleep(3)\n",
        encoding="utf-8",
    )
    err_script.write_text(
        "print('status 1')\n"
        "print('WARNUNG: test')\n"
        "raise RuntimeError('Selbsttest-Fehler')\n",
        encoding="utf-8",
    )

    logs_dir = Path(paths.data_path("logs"))
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / f"start_eb200_selftest_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_fh = open(log_path, "w", encoding="utf-8", newline="", buffering=1)
    ui = UIState()

    st_ok = ServerState(name="dummy_ok", port=0, script=ok_script, cwd=tmp, optional=False)
    st_err = ServerState(name="dummy_err", port=0, script=err_script, cwd=tmp, optional=False)
    states = [st_ok, st_err]

    threads = []
    for st in states:
        threads += _spawn(st, ui, log_fh, echo=False)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        for st in states:
            if st.exit_code is None:
                rc = st.proc.poll()
                if rc is not None:
                    with LOCK:
                        st.exit_code = rc
                        _recompute_status(st)
        if all(st.exit_code is not None for st in states):
            break
        time.sleep(0.05)

    for t in threads:
        t.join(timeout=1.0)

    # --- T-306: _restart() -------------------------------------------------
    # Eigenes, laenger laufendes Dummy-Skript (1,5 s) statt ok_script/
    # err_script oben - die brauchen wir noch unveraendert fuer ihre eigenen
    # Assertions, und ok_script laeuft schon oben durch (dessen Prozess ist
    # bereits beendet).
    restart_script = tmp / "dummy_restart.py"
    restart_script.write_text(
        "import time\n"
        "print('alive')\n"
        "time.sleep(1.5)\n",
        encoding="utf-8",
    )

    # Fall 1: normaler Neustart (Port 0 wie bei den Dummys oben - _check_port
    # liefert dafuer sofort False, der Port-Wartezeit-Zweig kommt also nicht
    # zum Tragen; das ist gesondert unten mit einem echten Port getestet).
    st_restart = ServerState(name="dummy_restart", port=0, script=restart_script, cwd=tmp, optional=False)
    _spawn(st_restart, ui, log_fh, echo=False)
    time.sleep(0.1)   # Zeit fuer den Start, bevor terminate() greift
    old_pid = st_restart.proc.pid
    _restart(st_restart, ui, log_fh, echo=False)
    new_pid = st_restart.proc.pid

    restart_deadline = time.monotonic() + 5.0
    while time.monotonic() < restart_deadline and st_restart.proc.poll() is None:
        time.sleep(0.05)
    with LOCK:
        if st_restart.exit_code is None:
            rc = st_restart.proc.poll()
            if rc is not None:
                st_restart.exit_code = rc
                _recompute_status(st_restart)

    # Fall 2: Port kuenstlich belegt (eigener Listener-Socket auf demselben
    # Port wie st_blocked) - _restart() muss nach 5 s abbrechen, OHNE den
    # Zustand zu veraendern (der Dummy-Prozess selbst wird trotzdem beendet -
    # terminate() laeuft unbedingt, bevor der Port ueberhaupt geprueft wird).
    occ_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occ_sock.bind(("127.0.0.1", 0))
    occ_port = occ_sock.getsockname()[1]
    # Grosser Backlog: _check_port() verbindet sich wiederholt und schliesst
    # sofort wieder, ohne dass hier jemals accept() aufgerufen wird - mit
    # backlog=1 lief die Warteschlange nach 1-2 Versuchen voll und liess
    # _check_port() faelschlich False liefern (Neustart brach zu frueh ab).
    occ_sock.listen(64)
    st_blocked = ServerState(name="dummy_blocked", port=occ_port, script=restart_script, cwd=tmp, optional=False)
    _spawn(st_blocked, ui, log_fh, echo=False)
    time.sleep(0.1)
    with LOCK:
        status_before_blocked = st_blocked.status
    t_blocked0 = time.monotonic()
    _restart(st_blocked, ui, log_fh, echo=False)
    blocked_elapsed = time.monotonic() - t_blocked0
    occ_sock.close()
    if st_blocked.proc is not None and st_blocked.proc.poll() is None:
        st_blocked.proc.terminate()

    log_fh.close()

    ok = True
    try:
        assert st_ok.status == Status.BEENDET, "dummy_ok: falscher Status"
        assert st_ok.exit_code == 0, "dummy_ok: falscher Exit-Code"
        assert st_ok.error_count == 0, "dummy_ok: unerwartete Fehler"
        out_lines = [e for e in st_ok.log if e[1] == "out"]
        assert len(out_lines) == 3, f"dummy_ok: erwartet 3 stdout-Zeilen, gefunden {len(out_lines)}"

        assert st_err.exit_code == 1, "dummy_err: falscher Exit-Code"
        assert st_err.error_count == 1, f"dummy_err: erwartet 1 Fehlerblock, gefunden {st_err.error_count}"
        block = st_err.errors[0]
        assert block[0].startswith("Traceback"), "dummy_err: Block beginnt nicht mit Traceback"
        assert "RuntimeError: Selbsttest-Fehler" in block[-1], "dummy_err: Block endet nicht mit RuntimeError-Zeile"
        warn_lines = [e for e in st_err.log if "WARNUNG" in e[2]]
        assert len(warn_lines) == 1, "dummy_err: WARNUNG-Zeile nicht gefunden"

        # --- T-306: _restart(), Fall 1 (normaler Neustart) ------------------
        assert new_pid != old_pid, "Neustart: PID unveraendert"
        assert st_restart.status == Status.BEENDET, \
            f"Neustart: erwarteter Status BEENDET, war {st_restart.status}"
        assert st_restart.error_count == 0, "Neustart: unerwartete Fehler nach dem Neustart"
        sep_lines = [e for e in st_restart.log if e[2].startswith("---- Neustart ")]
        assert len(sep_lines) == 1, "Neustart: Trennzeile fehlt oder mehrfach vorhanden"
        restarted_lines = [e for e in st_restart.log if "neu gestartet (PID" in e[2]]
        assert len(restarted_lines) == 1, "Neustart: 'neu gestartet'-Zeile fehlt"

        # --- T-306: _restart(), Fall 2 (Port kuenstlich belegt) -------------
        assert blocked_elapsed >= 4.5, \
            f"Neustart (Port belegt): zu frueh abgebrochen ({blocked_elapsed:.1f} s)"
        assert st_blocked.status == status_before_blocked, \
            "Neustart (Port belegt): Status haette unveraendert bleiben muessen"
        blocked_lines = [e for e in st_blocked.log if "noch belegt - Neustart abgebrochen" in e[2]]
        assert len(blocked_lines) == 1, "Neustart (Port belegt): Abbruch-Meldung fehlt im Log"

        free_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        free_sock.bind(("127.0.0.1", 0))
        free_port = free_sock.getsockname()[1]
        free_sock.close()
        assert _check_port(free_port) is False, "Port-Check: unbelegter Port liefert nicht False"

        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen_sock.bind(("127.0.0.1", 0))
        listen_sock.listen(1)
        listen_port = listen_sock.getsockname()[1]
        assert _check_port(listen_port) is True, "Port-Check: offener Port liefert nicht True"
        listen_sock.close()

        # --- T-305: KILL_ON_JOB_CLOSE an einem eigenen Test-Job beweisen --
        if os.name == "nt":
            test_job = _create_kill_on_close_job()
            assert test_job is not None, "Job-Objekt nicht erzeugbar"
            kernel32 = ctypes.windll.kernel32
            dummy = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                zugeordnet = kernel32.AssignProcessToJobObject(
                    ctypes.c_void_p(test_job), ctypes.c_void_p(int(dummy._handle)))
                assert zugeordnet, "Job-Zuordnung des Dummy-Kinds fehlgeschlagen"
                kernel32.CloseHandle(ctypes.c_void_p(test_job))
                try:
                    dummy.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise AssertionError("Dummy-Kind lebt nach Job-Close weiter")
                print("[start_eb200] Job-Objekt-Test OK (Kind endet mit dem Job-Handle)")
            finally:
                if dummy.poll() is None:
                    dummy.kill()

        assert log_path.exists(), "Logdatei fehlt"
        log_content = log_path.read_text(encoding="utf-8")
        assert "RuntimeError" in log_content, "Logdatei enthält kein RuntimeError"
        assert "---- Neustart " in log_content, "Logdatei enthält keine Neustart-Trennzeile"
        assert "noch belegt - Neustart abgebrochen" in log_content, \
            "Logdatei enthält keine Abbruch-Meldung (Port belegt)"
    except AssertionError as exc:
        print(f"[start_eb200] Selbsttest FEHLGESCHLAGEN: {exc}")
        ok = False

    for st in states + [st_restart, st_blocked]:
        if st.proc is not None and st.proc.poll() is None:
            st.proc.terminate()
    shutil.rmtree(tmp, ignore_errors=True)

    if ok:
        print("[start_eb200] Selbsttest OK")
        return 0
    return 1


def _numpy_missing_hint() -> Optional[list]:
    """Prueft, ob numpy importierbar ist (T-613).

    Liefert None, wenn numpy vorhanden ist, sonst die fertigen Hinweiszeilen.
    Hintergrund: numpy ist seit dem Entscheidungslog-Eintrag 2026-09-03 (T-610)
    Pflichtabhaengigkeit der gesamten Suite - waterfall.py laedt den Auto-Marker
    aus eb200_core/signal_detect.py (T-505). Fehlt numpy, stirbt der Kindprozess
    auf Port 8000 mit einem ImportError-Traceback, den der Starter nur als
    Fehlerblock in Zone 3 anzeigt; die eigentliche Ursache ist dort leicht zu
    uebersehen. Diese Vorpruefung faengt den Fall im Starter selbst ab und nennt
    den Installationsbefehl der laufenden Plattform.

    Rueckgabe bewusst als Zeilenliste statt als ein String mit Zeilenumbruch-
    Escapes - das Projekt vermeidet Backslash-Sequenzen in Python-Strings
    (siehe CLAUDE.md).

    Der Selbsttest (--selftest) braucht kein numpy und wird in main() VOR dieser
    Pruefung abgehandelt.
    """
    try:
        import numpy  # noqa: F401
    except ImportError:
        pass
    else:
        return None
    if sys.platform.startswith("win"):
        befehl = "pip install -r requirements.txt"
    else:
        befehl = "sudo apt install python3-numpy   (alternativ: pip install -r requirements.txt)"
    return [
        "[start_eb200] numpy fehlt - MoniScope kann nicht starten.",
        "  numpy ist Pflichtabhaengigkeit der Suite (siehe requirements.txt);",
        "  ohne numpy bleibt mindestens der Wasserfall auf Port 8000 tot.",
        f"  Installation: {befehl}",
        "  Danach erneut starten. Der Selbsttest (--selftest) laeuft auch ohne numpy.",
    ]


def main() -> int:
    # Eigene stdout/stderr auf UTF-8 zwingen: ohne echtes TTY (Umleitung in eine
    # Datei, IDE-Terminal) verwendet Windows sonst die System-Codepage (z. B.
    # cp1252), die weder die Box-Zeichnung des Boards noch von Kindprozessen
    # dekodierte UTF-8-Zeilen darstellen kann (UnicodeEncodeError).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="MoniScope Sammelstarter mit Konsolen-Statusboard")
    parser.add_argument("--plain", action="store_true",
                         help="Zeilenstrom statt Statusboard (auch automatisch ohne TTY)")
    parser.add_argument("--selftest", action="store_true",
                         help="Selbsttest mit Dummy-Skripten, startet keine echten Server")
    parser.add_argument("--data-dir", metavar="PFAD",
                    help="Datenwurzel (wird absolut an alle Server weitergereicht, T-2003)")
    parser.add_argument("--no-browser", action="store_true",
                         help="Browser nach dem Start nicht automatisch oeffnen")
    parser.add_argument("--eb200-host", metavar="ADRESSE",
                        help="EB200-Adresse fuer diesen Lauf (Vorrang vor device.json, T-2013)")
    args = parser.parse_args()
    global EB200_HOST_OVERRIDE
    EB200_HOST_OVERRIDE = args.eb200_host

    if args.selftest:
        return _selftest()

    # numpy-Vorpruefung (T-613): ohne numpy waere der Start sinnlos, der Fehler
    # wuerde aber erst als Traceback des Kindprozesses auf Port 8000 sichtbar.
    hinweis = _numpy_missing_hint()
    if hinweis is not None:
        for zeile in hinweis:
            print(zeile, file=sys.stderr)
        return 2

    return _run(plain=args.plain, no_browser=args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
