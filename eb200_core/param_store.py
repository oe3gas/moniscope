#!/usr/bin/env python3
"""
eb200_core/param_store.py

Parameter-Memory: ein einziger JSON-Speicher fuer

  - allgemeine App-Konfiguration ("app"-Namespace, dotted keys, z. B.
    "app.waterfall.dscan_start_hz") - wird spaeter aus den HTTP-Handler-
    Threads der Weboberflaechen (waterfall.py & Co.) gelesen/geschrieben.
  - eine EB200-Parameter-Inventur per SCPI-Query ("eb200"-Namespace) - eine
    Bestandsaufnahme, welche Annex-C-Parameter das konkrete Geraet tatsaech-
    lich beantwortet, als Grundlage fuer eine spaetere Status-Page.

Dieses Modul ist Schritt 1 (Speichern): ParamStore als allgemeiner Speicher
(Teil A) und snapshot_eb200() als EB200-spezifische Inventur-Funktion
(Teil B). Anzeige/Status-Page ist bewusst NICHT Teil dieses Moduls.

Dateiformat (data/params/param_store.json):
    {
      "meta": {"schema": 1, "updated_at": "2026-08-20T14:03:11+02:00"},
      "eb200": { ... zuletzt gespeicherter snapshot_eb200()-Snapshot ... },
      "app":   { "waterfall": {"dscan_start_hz": 88000000, ...}, ... }
    }
Jeder Snapshot wird zusaetzlich unverändert als Archivdatei abgelegt unter
data/params/eb200_snapshots/eb200_<YYYYMMDD_HHMMSS>.json (Zeitstempel aus
dem Snapshot selbst) - der Store haelt nur den JEWEILS LETZTEN Snapshot,
die Archivdateien sind der Verlauf. DEFAULT_STORE_PATH liegt unter der
Datenwurzel (eb200_core/paths.py, T-2002) statt cwd-relativ, damit alle
Server dieselbe Datei treffen, unabhaengig davon, aus welchem
Arbeitsverzeichnis sie laufen.

Mehrere Schreiber (mehrere Server-Prozesse auf derselben Store-Datei):
  ParamStore haelt bewusst KEINEN RAM-Cache - die Datei auf der Platte ist
  die einzige Wahrheit, jeder Zugriff (auch get()) laedt sie frisch. Ein
  RAM-Cache wuerde genau dann veraltete Werte liefern, wenn ein anderer
  Prozess (z. B. waterfall.py und analyze_sessions.py gleichzeitig)
  zwischenzeitlich geschrieben hat. Schreibvorgaenge sind zusaetzlich zum
  Thread-Lock ueber eine Lock-Datei (_file_lock) prozessuebergreifend
  serialisiert, damit sich zwei Prozesse beim Lesen-Aendern-Schreiben nicht
  gegenseitig ueberbuegeln. Konsequenz fuer Aufrufer: get() liest jedes Mal
  neu von der Platte - wer in einer Schleife mehrere Werte aus demselben
  Namespace braucht, sollte einmal get_namespace() aufrufen statt get()
  wiederholt, sonst wird unnoetig oft die Datei geoeffnet.

Design-Entscheidungen (bewusst einfach, nur Standardbibliothek):
  - Jeder Schreibvorgang atomar (erst "<pfad>.tmp" schreiben, dann
    os.replace) - ein Absturz mitten im Schreiben darf die Datei nie
    kaputt zuruecklassen.
  - Ein gemeinsamer threading.Lock schuetzt alle Lese-/Schreibpfade
    innerhalb EINES Prozesses (siehe waterfall.py-Threading-Konvention);
    die Lock-Datei schuetzt zusaetzlich zwischen mehreren Prozessen.
  - Transport-Schnittstelle identisch zu eb200_core.scpi_verify.Transport
    (send()/query()), damit derselbe ScpiClient/FakeTransport-Stil ueberall
    im Projekt funktioniert. Dieses Modul importiert absichtlich NICHTS aus
    anderen Projektdateien (auch nicht aus scpi_verify.py) - es bringt eine
    eigene, minimale ScpiClient-Kopie mit (identische query()-Logik wie in
    waterfall.py) und bleibt so eigenstaendig testbar.

Aufruf als eigenstaendiges Skript:
    python -m eb200_core.param_store --selftest
    python -m eb200_core.param_store snapshot --host 10.0.0.55 [--port 5555] [--store PFAD]
    python -m eb200_core.param_store show  [--store PFAD]
    python -m eb200_core.param_store set   app.test.key 123   [--store PFAD]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from typing import Any, Optional, Protocol

# Erlaubt auch den Direktaufruf "python eb200_core/param_store.py": dort fehlt
# sonst das Repo-Root im Suchpfad fuer den paths-Import (T-2002, Weiche wie
# in eb200_core/path_sandbox.py).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core import paths

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
# Unter DATA_ROOT (T-2002, nicht cwd-relativ): ein relativer Pfad wuerde bei
# einem Server mit abweichendem Arbeitsverzeichnis eine zweite, falsche
# Store-Datei erzeugen. So treffen alle Server dieselbe Datei.
DEFAULT_STORE_PATH = paths.data_path("data", "params", "param_store.json")

# Prozessuebergreifende Sperre (siehe ParamStore._file_lock)
LOCK_TIMEOUT_S = 5.0    # maximale Wartezeit auf die Lock-Datei
LOCK_POLL_S = 0.02      # Abfrageintervall beim Warten
LOCK_STALE_S = 30.0     # aeltere Lock-Dateien gelten als verwaist

# Windows-Eigenheit: os.replace() auf eine Datei, die ein anderer Prozess
# gerade zum Lesen offen hat, wirft eine Sharing Violation (PermissionError,
# errno 13). Das Fenster ist wenige Millisekunden gross (Startup-Race beim
# gleichzeitigen register_server aller Server) - ein kurzer Retry genuegt.
REPLACE_RETRIES = 5     # Versuche fuer os.replace bei PermissionError
REPLACE_RETRY_S = 0.05  # Pause zwischen zwei Versuchen

SNAPSHOT_SUBDIR = "eb200_snapshots"

# Passend zu waterfall.py (SCPI_PORT).
SCPI_PORT = 5555

# Anzahl der SYSTem:ERRor?-Abfragen, bevor drain_error_queue abbricht
# (Schutz gegen eine nie leer werdende Queue) - Muster aus scpi_verify.py.
MAX_ERROR_READS = 32

# Anzahl der *OPC?-Versuche beim Resync nach einem Query-Timeout, siehe
# _resync_after_timeout.
RESYNC_MAX_LINES = 4


# ---------------------------------------------------------------------------
# Transport-Schnittstelle (identisch zu eb200_core.scpi_verify.Transport)
# ---------------------------------------------------------------------------

class Transport(Protocol):
    """Minimal benoetigte SCPI-Schnittstelle: send() ohne Antwort,
    query() sendet UND liefert eine gestrippte Antwortzeile zurueck."""

    def send(self, command: str) -> None: ...
    def query(self, command: str) -> str: ...


class ScpiClient:
    """Minimaler SCPI-TCP-Client fuer die CLI dieses Moduls.

    Gleiche query()-Logik wie waterfall.ScpiClient: senden, dann in einer
    kleinen Schleife lesen, bis der Puffer ein Zeilenende (b"\\n") enthaelt -
    die TCP-Verbindung liefert nur Kommando-Antworten (Messdaten laufen
    separat ueber UDP), ein etwaiger Rest nach der ersten Zeile wird daher
    verworfen.
    """

    def __init__(self, host: str, port: int = SCPI_PORT, timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)

    def send(self, command: str) -> None:
        self._sock.sendall(command.encode("ascii") + b"\n")

    def query(self, command: str) -> str:
        self._sock.sendall(command.encode("ascii") + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("SCPI-Verbindung vom Geraet geschlossen")
            buf += chunk
        line, _rest = buf.split(b"\n", 1)
        return line.decode("ascii").strip()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Zeitstempel-Hilfen (bewusst ohne datetime-Modul, siehe Randbedingungen)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """ISO-8601-Zeitstempel mit lokaler UTC-Offset, z. B.
    "2026-08-20T14:03:11+02:00" - per Hand aus time.* zusammengesetzt, da
    dieses Modul bewusst ohne das datetime-Modul auskommt."""
    now = time.localtime()
    if time.daylight and now.tm_isdst > 0:
        offset_s = -time.altzone
    else:
        offset_s = -time.timezone
    sign = "+" if offset_s >= 0 else "-"
    offset_s = abs(offset_s)
    oh, om = divmod(offset_s // 60, 60)
    return time.strftime("%Y-%m-%dT%H:%M:%S", now) + f"{sign}{int(oh):02d}:{int(om):02d}"


def _stamp_from_taken_at(taken_at: str) -> str:
    """Wandelt einen "taken_at"-Zeitstempel (Format wie _now_iso()) in
    YYYYMMDD_HHMMSS fuer Archiv-Dateinamen um. Bei unerwartetem Format
    (z. B. leerer String) faellt die Funktion auf die aktuelle Uhrzeit
    zurueck - ein Snapshot soll nie an einem Dateinamen scheitern."""
    try:
        y, mo, d = taken_at[0:10].split("-")
        h, mi, s = taken_at[11:19].split(":")
        if len(y) == 4 and len(mo) == 2 and len(d) == 2 and len(h) == 2 and len(mi) == 2 and len(s) == 2:
            return f"{y}{mo}{d}_{h}{mi}{s}"
    except Exception:
        pass
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


# ---------------------------------------------------------------------------
# Teil A - ParamStore (allgemeiner Speicher)
# ---------------------------------------------------------------------------

def _atomic_write_json(path: str, data: Any) -> None:
    """Schreibt `data` als JSON atomar nach `path` (erst .tmp, dann
    os.replace) und legt dabei fehlende Verzeichnisse an.

    os.replace wird bei PermissionError bis zu REPLACE_RETRIES-mal
    wiederholt: unter Windows wirft os.replace eine Sharing Violation,
    wenn ein anderer Prozess die Zieldatei gerade zum Lesen offen hat
    (die Lesepfade von ParamStore nehmen bewusst keine Datei-Sperre).
    Nach dem letzten Fehlversuch wird die .tmp-Datei entfernt und die
    Exception weitergereicht - der Aufrufer entscheidet (register_server
    ist z. B. bewusst nie fatal)."""
    dirpath = os.path.dirname(path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            if attempt == REPLACE_RETRIES - 1:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            time.sleep(REPLACE_RETRY_S)


class ParamStore:
    """Allgemeiner Key-Value-Speicher mit dotted keys, persistiert als eine
    JSON-Datei. Jeder Schreibvorgang schreibt sofort und atomar; ein
    gemeinsamer Lock schuetzt alle Zugriffe (siehe Modul-Docstring)."""

    def __init__(self, path: str = DEFAULT_STORE_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()          # Threads innerhalb EINES Prozesses
        self._lock_path = path + ".lock"       # Prozesse untereinander, siehe _file_lock
        # Bewusst KEIN RAM-Cache: mehrere Server-Prozesse schreiben in dieselbe
        # Datei, daher ist die Datei die einzige Wahrheit und jeder Zugriff
        # laedt frisch (siehe Modul-Docstring "Mehrere Schreiber").

    # -- internes Laden/Schreiben (Aufrufer muss self._lock bereits halten) --

    def _load(self) -> dict:
        """Laedt die Datei frisch von der Platte (legt sie bei Bedarf an).
        Aufrufer muss self._lock halten; fuer Schreibpfade zusaetzlich
        _file_lock (sonst kann ein anderer Prozess zwischen Laden und
        Schreiben dazwischenfunken)."""
        return self._load_or_init()

    @contextlib.contextmanager
    def _file_lock(self):
        """Prozessuebergreifende Sperre ueber eine exklusiv angelegte
        Lock-Datei (os.O_CREAT | os.O_EXCL ist auf Windows und POSIX atomar).
        Wartet in kurzen Schritten bis LOCK_TIMEOUT_S, danach TimeoutError.
        Eine Lock-Datei, die aelter als LOCK_STALE_S ist, gilt als Leiche
        eines abgestuerzten Prozesses und wird entfernt."""
        # T-2003: in einer frischen Datenwurzel fehlt data/params/ noch - ohne
        # dieses Verzeichnis scheitert schon das Anlegen der Lock-Datei
        # (FileNotFoundError, register_server meldet dann nur einen Fehler).
        os.makedirs(os.path.dirname(os.path.abspath(self._lock_path)), exist_ok=True)
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                break
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self._lock_path)
                    if age > LOCK_STALE_S:
                        os.unlink(self._lock_path)
                        continue
                except OSError:
                    pass  # Lock gerade von anderem Prozess entfernt -> neu versuchen
                if time.monotonic() > deadline:
                    raise TimeoutError(f"ParamStore: lock file {self._lock_path} "
                                       f"not free after {LOCK_TIMEOUT_S}s")
                time.sleep(LOCK_POLL_S)
        try:
            yield
        finally:
            try:
                os.unlink(self._lock_path)
            except OSError:
                pass

    def _load_or_init(self) -> dict:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        data = {"meta": {"schema": SCHEMA_VERSION, "updated_at": _now_iso()}, "eb200": {}, "app": {}}
        _atomic_write_json(self._path, data)
        return data

    def _write(self, data: dict) -> None:
        data["meta"]["updated_at"] = _now_iso()
        _atomic_write_json(self._path, data)

    @staticmethod
    def _set_in(data: dict, key: str, value: Any) -> None:
        """Setzt `value` unter dem dotted key `key`, legt fehlende
        Zwischenebenen als dict an (bestehende Nicht-dict-Werte auf dem Weg
        werden dabei ueberschrieben - dotted keys gewinnen)."""
        parts = key.split(".")
        node = data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    @staticmethod
    def _validate_serializable(key: str, value: Any) -> None:
        try:
            json.dumps(value)
        except TypeError as exc:
            raise TypeError(f"value for key '{key}' is not JSON-serializable: {exc}") from exc

    # -- oeffentliche API --
    # Lesepfade: Thread-Lock + frisch laden (keine Datei-Sperre noetig, da
    # _atomic_write_json per os.replace immer eine vollstaendige Datei
    # hinterlaesst). Schreibpfade: Thread-Lock + Datei-Sperre + laden +
    # aendern + schreiben, damit sich mehrere Prozesse nicht ueberbuegeln.

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._load()
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, key: str, value: Any) -> None:
        self.set_many({key: value})

    def set_many(self, mapping: dict) -> None:
        for key, value in mapping.items():
            self._validate_serializable(key, value)
        with self._lock, self._file_lock():
            data = self._load()
            for key, value in mapping.items():
                self._set_in(data, key, value)
            self._write(data)

    def get_namespace(self, ns: str) -> dict:
        with self._lock:
            node = self._load().get(ns, {})
            return json.loads(json.dumps(node)) if isinstance(node, dict) else {}

    def as_dict(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._load()))

    def put_eb200_snapshot(self, snap: dict) -> str:
        """Ersetzt den "eb200"-Namespace durch `snap` und schreibt zusaetzlich
        eine Archivdatei data/params/eb200_snapshots/eb200_<Zeitstempel>.json
        (Zeitstempel aus snap["meta"]["taken_at"]). Gibt den Archivpfad zurueck."""
        taken_at = snap.get("meta", {}).get("taken_at", "")
        stamp = _stamp_from_taken_at(taken_at)
        base_dir = os.path.dirname(self._path) or "."
        archive_path = os.path.join(base_dir, SNAPSHOT_SUBDIR, f"eb200_{stamp}.json")

        with self._lock, self._file_lock():
            data = self._load()
            data["eb200"] = snap
            self._write(data)

        _atomic_write_json(archive_path, snap)

        return archive_path


# ---------------------------------------------------------------------------
# Lebenszeichen eines Servers (gemeinsames Schluesselschema fuer alle Server)
# ---------------------------------------------------------------------------

def register_server(name: str, port: int, extra: Optional[dict] = None,
                    store_path: str = DEFAULT_STORE_PATH) -> None:
    """Schreibt app.<name>.{started_at, http_port, pid} (+ optionale extra-
    Schluessel) ins Parameter-Memory - ein Schreibvorgang. Gedacht fuer den
    Aufruf einmal in main() jedes Servers, direkt vor serve_forever().
    NIE fatal: ein Server darf nicht scheitern, nur weil das Memory gerade
    nicht beschreibbar ist (z. B. Lock-Timeout) - dann nur eine Meldung."""
    values = {
        f"app.{name}.started_at": _now_iso(),
        f"app.{name}.http_port": int(port),
        f"app.{name}.pid": os.getpid(),
    }
    for k, v in (extra or {}).items():
        values[f"app.{name}.{k}"] = v
    try:
        ParamStore(store_path).set_many(values)
    except Exception as exc:
        print(f"[params] Registrierung von '{name}' fehlgeschlagen ({exc!r})", file=sys.stderr)


# ---------------------------------------------------------------------------
# Teil B - EB200-Snapshot
# ---------------------------------------------------------------------------
#
# Query-Tabelle im Langform-Stil (ohne optionale Klammern), gruppiert nach
# Themenblock. Quelle: Annex C des EB200-Handbuchs (SCPI-Kommandoreferenz),
# Gruppeneinteilung angelehnt an Tabelle 4-11 "Change bit allocation" (dort
# sind die gleichen Themenbloecke - Empfaenger, Antenne, D-SCAN, F-SCAN,
# M-SCAN, Audio, ... - als Aenderungs-Bitgruppen aufgefuehrt).

EB200_QUERIES: list[tuple[str, str]] = [
    # system: Geraeteidentifikation und Firmware-/Softwarestand (Annex C).
    ("system", "*IDN?"),
    ("system", "*OPT?"),
    ("system", "SYSTem:VERSion?"),
    ("system", "DIAGnostic:INFO:SVERsion?"),
    ("system", "DIAGnostic:INFO:SDATe?"),

    # rx: Empfaenger-Grundeinstellungen - Frequenz, Demodulation, Bandbreite,
    # Verstaerkung, Rauschsperre, Messmodus (Annex C, Tabelle 4-11 Gruppe RX).
    ("rx", "SENSe:FREQuency:CW?"),
    ("rx", "SENSe:DEModulation?"),
    ("rx", "SENSe:BANDwidth:RESolution?"),
    ("rx", "SENSe:DETector:FUNCtion?"),
    ("rx", "SENSe:FREQuency:CW:AFC?"),
    ("rx", "SENSe:GCONtrol:MGC?"),
    ("rx", "SENSe:GCONtrol:MODE?"),
    ("rx", "INPut:ATTenuation:STATe?"),
    ("rx", "INPut:ATTenuation:AUTO?"),
    ("rx", "OUTPut:SQUelch:STATe?"),
    ("rx", "OUTPut:SQUelch:THReshold:UPPer?"),
    ("rx", "OUTPut:SQUelch:CONTrol?"),
    ("rx", "SENSe:FUNCtion?"),
    ("rx", "SENSe:FUNCtion:COUNt?"),
    ("rx", "SENSe:FREQuency:MODE?"),
    ("rx", "MEASure:TIME?"),
    ("rx", "MEASure:MODE?"),
    ("rx", "SENSe:VOLTage:AC:RANGe?"),
    ("rx", "MEMory:CONTents? RX"),

    # antenna: Antennenumschaltung/-korrektur (Annex C, Tabelle 4-11 Gruppe ANT).
    ("antenna", "ROUTe:CLOSe:STATe?"),
    ("antenna", "ROUTe:PATH:CATalog?"),
    ("antenna", "SENSe:CORRection:ANTenna?"),
    ("antenna", "OUTPut:AUXMode?"),

    # ifpan: ZF-Panorama (IFPAN/Zoom-Modus im Wasserfall) (Annex C).
    ("ifpan", "SENSe:FREQuency:SPAN?"),
    # Handbuch 4.5 (Tabelle 4-11 nennt faelschlich CALC:IFPAN:TYPE?, Geraet: -113).
    ("ifpan", "CALCulate:IFPan:AVERage:TYPE?"),
    ("ifpan", "CALCulate:IFPan:AVERage:TIME?"),

    # dscan: Frequenzbereichs-Scan (D-SCAN/Uebersicht im Wasserfall) (Annex C,
    # Tabelle 4-11 Gruppe DSCAN).
    ("dscan", "SENSe:FREQuency:DSCan:STARt?"),
    ("dscan", "SENSe:FREQuency:DSCan:STOP?"),
    ("dscan", "SENSe:FREQuency:DSCan:CENTer?"),
    ("dscan", "SENSe:FREQuency:DSCan:SPAN?"),
    ("dscan", "SENSe:FREQuency:DSCan:MARKer?"),
    ("dscan", "SENSe:FREQuency:DSCan:SPEed?"),
    ("dscan", "SENSe:DSCan:COUNt?"),
    ("dscan", "SENSe:FREQuency:DSCan:RESolution:AUTO?"),
    ("dscan", "CALCulate:DSCan:AVERage:TYPE?"),

    # fscan: Frequenzlisten-Scan (Annex C, Tabelle 4-11 Gruppe FSCAN).
    ("fscan", "SENSe:FREQuency:STARt?"),
    ("fscan", "SENSe:FREQuency:STOP?"),
    ("fscan", "SENSe:SWEep:STEP?"),
    ("fscan", "SENSe:SWEep:COUNt?"),
    ("fscan", "SENSe:SWEep:DWELl?"),
    ("fscan", "SENSe:SWEep:HOLD:TIME?"),
    ("fscan", "SENSe:SWEep:DIRection?"),
    ("fscan", "SENSe:SWEep:CONTrol?"),

    # mscan: Speicherkanal-Scan (Annex C, Tabelle 4-11 Gruppe MSCAN).
    ("mscan", "SENSe:MSCan:COUNt?"),
    ("mscan", "SENSe:MSCan:DWELl?"),
    ("mscan", "SENSe:MSCan:HOLD:TIME?"),
    ("mscan", "SENSe:MSCan:DIRection?"),
    ("mscan", "SENSe:MSCan:CONTrol?"),

    # audio: NF-Wiedergabe/Rufton (Annex C, Tabelle 4-11 Gruppe AUDIO).
    ("audio", "SYSTem:AUDio:VOLume?"),
    ("audio", "SYSTem:AUDio:BALance?"),
    ("audio", "SYSTem:SPEaker:STATe?"),
    ("audio", "SYSTem:AUDio:REMote:MODE?"),
    ("audio", "OUTPut:TONE:STATe?"),
    ("audio", "OUTPut:TONE:THReshold?"),
    ("audio", "OUTPut:TONE:CONTrol?"),

    # udp: UDP-Datagramm-Konfiguration. Annex F (Datagram Communication) liegt
    # nicht im Repo - Query wie im Handbuch-Beispielstil belassen, Ergebnis
    # wird empirisch bewertet (siehe errors-Block im Snapshot).
    ("udp", "TRACe:UDP?"),

    # display: Bildschirmdarstellung des Geraets (Annex C).
    ("display", "DISPlay:MENU?"),
    ("display", "DISPlay:ENABle?"),
    ("display", "DISPlay:BRIGhtness?"),
    ("display", "DISPlay:BRIGhtness:DWELl?"),

    # format: Datenformat fuer Rueckgabewerte/Speicher (Annex C).
    ("format", "FORMat:BORDer?"),
    ("format", "FORMat:MEMory?"),
    ("format", "FORMat:SREGister?"),

    # misc: Referenzoszillator, Zusatzbus, Tastensperre (Annex C).
    ("misc", "ROSCillator:SOURce?"),
    ("misc", "OUTPut:BYTaux:STATe?"),
    ("misc", "SYSTem:KLOCk?"),
]


def _error_code(reply: str) -> int:
    """Fehlercode aus einer SYSTem:ERRor?-Antwort (z. B. '-222,"Data out of
    range"'); der Code steht am Zeilenanfang vor dem ersten Komma."""
    head = reply.strip().split(",", 1)[0]
    try:
        return int(head)
    except ValueError:
        return -1  # unerwartetes Format -> als "Fehler vorhanden" werten


def drain_error_queue(transport: Transport, max_reads: int = MAX_ERROR_READS) -> list[str]:
    """SYSTem:ERRor? in Schleife abfragen, bis '0,"No error"' kommt. Muster
    aus scpi_verify.drain_errors, hier mit der Langform-Query. Gibt die
    Liste der tatsaechlichen Fehlermeldungen zurueck (ohne den
    abschliessenden '0,...'-Eintrag). Bricht nach max_reads ab, damit eine
    dauerhaft nicht leere Queue nicht blockiert."""
    errors: list[str] = []
    for _ in range(max_reads):
        try:
            reply = transport.query("SYSTem:ERRor?")
        except Exception:
            break
        if _error_code(reply) == 0:
            break
        errors.append(reply.strip())
    return errors


def _resync_after_timeout(transport: Transport) -> None:
    """Nach einem Query-Timeout kann die Antwort noch verspaetet eintreffen
    und wuerde dann von der NAECHSTEN Query als deren Antwort gelesen -
    alle Folgewerte waeren um eins verschoben. Daher: *OPC? senden und so
    lange Zeilen lesen, bis die Antwort "1" kommt (alles davor ist die
    verspaetete Altantwort). Kommt auch darauf nichts, ist die Verbindung
    tot -> ConnectionError, der Snapshot bricht sauber ab."""
    for _ in range(RESYNC_MAX_LINES):
        try:
            reply = transport.query("*OPC?")
        except (TimeoutError, OSError) as exc:
            raise ConnectionError(f"Resync nach Timeout fehlgeschlagen: {exc!r}") from exc
        if reply.strip() == "1":
            return
    raise ConnectionError("resync after timeout: no *OPC? reply within "
                          f"{RESYNC_MAX_LINES} lines")


def snapshot_eb200(
    transport: Transport,
    queries: list[tuple[str, str]] = EB200_QUERIES,
    drain_errors: bool = True,
    pause_s: float = 0.0,
) -> dict:
    """Fragt jede Query aus `queries` einzeln ab und liefert eine EB200-
    Parameter-Inventur (siehe Modul-Docstring fuer das Rueckgabeformat).

    Ablauf je Query:
      1. transport.query(q) -> Rohantwort, keine Umrechnung/Typisierung.
      2. Danach einmal SYSTem:ERRor? - Antwort != "0,..." heisst, die Query
         ist fehlgeschlagen (z. B. Kurzform nicht unterstuetzt): params[q]
         wird None, errors[q] enthaelt den Fehlertext.
      3. Wirft die Query selbst eine Exception (Timeout o. ae.), gilt das
         ebenso als Fehler dieser einen Query - der Snapshot laeuft weiter.
         Eine ConnectionError (Verbindung vom Geraet geschlossen) wird
         dagegen durchgereicht: dann ist der ganze Snapshot hinfaellig,
         nicht nur eine Query.
    Ein Snapshot bricht also nie wegen einer einzelnen unbekannten/abgelehnten
    Query ab - genau das ist der Zweck: eine Inventur, welche Queries das
    konkrete Geraet tatsaechlich beantwortet.

    pause_s > 0 fuer Hintergrund-Inventuren parallel zum Betrieb - entlastet
    den Kommandointerpreter des Geraets; die CLI nutzt 0.
    """
    t0 = time.monotonic()
    taken_at = _now_iso()

    if drain_errors:
        drain_error_queue(transport)

    groups: dict[str, list[str]] = {}
    params: dict[str, Any] = {}
    errors: dict[str, str] = {}
    resyncs = 0

    for group, q in queries:
        groups.setdefault(group, []).append(q)

        try:
            raw = transport.query(q)
        except ConnectionError:
            raise
        except Exception as exc:
            params[q] = None
            errors[q] = f"Query fehlgeschlagen: {exc!r}"
            _resync_after_timeout(transport)
            resyncs += 1
            if pause_s > 0:
                time.sleep(pause_s)
            continue

        try:
            err_reply = transport.query("SYSTem:ERRor?")
        except ConnectionError:
            raise
        except Exception:
            # Fehler-Queue nicht abfragbar: der Rohwert wurde trotzdem
            # gelesen, wird ungeprueft uebernommen.
            params[q] = raw
            if pause_s > 0:
                time.sleep(pause_s)
            continue

        if _error_code(err_reply) != 0:
            params[q] = None
            errors[q] = err_reply.strip()
        else:
            params[q] = raw

        if pause_s > 0:
            time.sleep(pause_s)

    n_failed = len(errors)
    n_ok = len(queries) - n_failed

    return {
        "meta": {
            "taken_at": taken_at,
            "duration_s": round(time.monotonic() - t0, 3),
            "idn": params.get("*IDN?"),
            "opt": params.get("*OPT?"),
            "n_ok": n_ok,
            "n_failed": n_failed,
            "resyncs": resyncs,
            "source": "param_store.snapshot_eb200",
        },
        "groups": groups,
        "params": params,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Teil C - CLI
# ---------------------------------------------------------------------------

def _cmd_snapshot(args: argparse.Namespace) -> int:
    client = ScpiClient(args.host, args.port)
    try:
        snap = snapshot_eb200(client)
    finally:
        client.close()

    store = ParamStore(args.store)
    archive_path = store.put_eb200_snapshot(snap)

    meta = snap["meta"]
    print(f"Snapshot: {meta['n_ok']} OK, {meta['n_failed']} Fehler, "
          f"{meta['resyncs']} Resync(s), Dauer {meta['duration_s']:.2f} s")
    print(f"IDN: {meta['idn']}")
    print(f"OPT: {meta['opt']}")
    print(f"Archiv: {archive_path}")
    if snap["errors"]:
        print("Fehlgeschlagene Queries:")
        for q in sorted(snap["errors"]):
            print(f"  {q}: {snap['errors'][q]}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    store = ParamStore(args.store)
    data = store.as_dict()

    eb200 = data.get("eb200") or {}
    meta = eb200.get("meta")
    if not meta:
        print("Kein EB200-Snapshot im Store.")
    else:
        print(f"EB200-Snapshot vom {meta.get('taken_at')} "
              f"({meta.get('n_ok')} OK, {meta.get('n_failed')} Fehler, "
              f"IDN: {meta.get('idn')})")
        groups = eb200.get("groups", {})
        params = eb200.get("params", {})
        errors = eb200.get("errors", {})
        for group in sorted(groups):
            print(f"[{group}]")
            for q in groups[group]:
                if q in errors:
                    print(f"  {q} -> FEHLER: {errors[q]}")
                else:
                    print(f"  {q} = {params.get(q)}")

    app = data.get("app") or {}
    print("[app]")
    print(json.dumps(app, ensure_ascii=False, indent=2))
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    store = ParamStore(args.store)
    try:
        value = json.loads(args.value)
    except (json.JSONDecodeError, ValueError):
        value = args.value  # Fallback: als String uebernehmen
    store.set(args.key, value)
    print(f"{args.key} = {value!r}")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="param_store",
        description="EB200 Parameter-Memory: allgemeiner Konfigurationsspeicher "
                    "plus EB200-Parameter-Inventur per SCPI.",
    )
    parser.add_argument("--selftest", action="store_true",
                        help="Selbsttest ohne Geraet (FakeTransport, temporaeres Verzeichnis)")

    sub = parser.add_subparsers(dest="command")

    p_snap = sub.add_parser("snapshot", help="EB200-Parameter per SCPI abfragen und im Store ablegen")
    p_snap.add_argument("--host", required=True)
    p_snap.add_argument("--port", type=int, default=SCPI_PORT)
    p_snap.add_argument("--store", default=DEFAULT_STORE_PATH)

    p_show = sub.add_parser("show", help="Store-Inhalt kompakt ausgeben")
    p_show.add_argument("--store", default=DEFAULT_STORE_PATH)

    p_set = sub.add_parser("set", help="Einzelnen Key setzen (JSON-Literal, sonst als String)")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_set.add_argument("--store", default=DEFAULT_STORE_PATH)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.selftest:
        _selftest()
        return 0
    if args.command == "snapshot":
        return _cmd_snapshot(args)
    if args.command == "show":
        return _cmd_show(args)
    if args.command == "set":
        return _cmd_set(args)

    parser.print_help()
    return 1


# ---------------------------------------------------------------------------
# Selbsttest mit simuliertem Geraet (python -m eb200_core.param_store --selftest)
# ---------------------------------------------------------------------------

class _FakeParamTransport:
    """Simuliert ein EB200 fuer snapshot_eb200(): beantwortet jede Query mit
    einer deterministischen Zeichenkette. Fuer die in `fail_queries`
    genannten Queries wird zusaetzlich ein Fehler-Queue-Eintrag hinterlegt,
    der bei der naechsten SYSTem:ERRor?-Abfrage einmal ausgeliefert wird -
    genau das Muster, das snapshot_eb200() je Query prueft.

    `timeout_query` simuliert zusaetzlich einen Query-Timeout: beim ersten
    Aufruf mit genau diesem Kommando wirft query() eine TimeoutError, statt
    zu antworten. Der darauf folgende *OPC?-Aufruf (Resync, siehe
    _resync_after_timeout) liefert erst die "verspaetete" Antwort "STALE"
    und erst beim naechsten *OPC?-Aufruf die korrekte "1" - genau das
    Verhalten, das der Resync abfangen muss."""

    def __init__(self, fail_queries: set[str], timeout_query: Optional[str] = None) -> None:
        self._fail_queries = set(fail_queries)
        self._pending_error: Optional[str] = None
        self._timeout_query = timeout_query
        self._timed_out = False
        self._stale_pending = False

    def send(self, command: str) -> None:
        pass

    def query(self, command: str) -> str:
        cmd = command.strip()
        if cmd == self._timeout_query and not self._timed_out:
            self._timed_out = True
            self._stale_pending = True
            raise TimeoutError("simulierter Query-Timeout")
        if cmd == "*OPC?":
            if self._stale_pending:
                self._stale_pending = False
                return "STALE"
            return "1"
        if cmd == "SYSTem:ERRor?":
            if self._pending_error is not None:
                err = self._pending_error
                self._pending_error = None
                return err
            return '0,"No error"'
        if cmd in self._fail_queries:
            self._pending_error = '-113,"Undefined header"'
            return ""  # Geraet liefert trotzdem irgendeine Antwort auf die Query selbst
        return f"ANTWORT:{cmd}"


def _selftest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store_path = os.path.join(tmp, "param_store.json")

        store = ParamStore(store_path)
        store.set("app.waterfall.dscan_start_hz", 88_000_000)
        assert store.get("app.waterfall.dscan_start_hz") == 88_000_000
        assert isinstance(store.get("app"), dict)
        assert isinstance(store.get("app.waterfall"), dict)
        print("OK  set/get mit dotted keys inkl. Zwischenebenen")

        assert store.get("app.does.not.exist", "fallback") == "fallback"
        assert store.get("app.does.not.exist") is None
        print("OK  get mit Default fuer fehlende Keys")

        store.set_many({"app.a.x": 1, "app.a.y": 2, "app.b.z": 3})
        assert store.get("app.a.x") == 1
        assert store.get("app.a.y") == 2
        assert store.get("app.b.z") == 3
        print("OK  set_many mit mehreren Keys (ein Schreibvorgang)")

        store2 = ParamStore(store_path)
        assert store2.get("app.waterfall.dscan_start_hz") == 88_000_000
        assert store2.get("app.a.y") == 2
        print("OK  Persistenz ueber neues ParamStore-Objekt auf gleichem Pfad")

        assert not os.path.exists(store_path + ".tmp")
        print("OK  keine .tmp-Datei nach set (atomarer Schreibvorgang)")

        try:
            store.set("app.bad", object())
        except TypeError as exc:
            assert "app.bad" in str(exc), "Key-Name fehlt in der Exception"
        else:
            raise AssertionError("TypeError bei nicht serialisierbarem Wert erwartet")
        print("OK  nicht serialisierbarer Wert wirft TypeError mit Key-Name")

        ns = store.get_namespace("app")
        assert ns["waterfall"]["dscan_start_hz"] == 88_000_000
        ns["waterfall"]["dscan_start_hz"] = 0  # Kopie darf Original nicht veraendern
        assert store.get("app.waterfall.dscan_start_hz") == 88_000_000
        full = store.as_dict()
        assert set(["meta", "eb200", "app"]).issubset(full.keys())
        print("OK  get_namespace liefert unabhaengige Kopie, as_dict das Gesamtbild")

        # Zwei Store-Objekte auf derselben Datei (Stellvertreter fuer zwei
        # Server-Prozesse): Schreiben ueber A darf Schreiben ueber B nicht
        # ueberbuegeln - genau der Fehler, den der RAM-Cache gehabt haette.
        store_a = ParamStore(store_path)
        store_b = ParamStore(store_path)
        store_a.set("app.server_a.x", 1)
        store_b.set("app.server_b.y", 2)
        store_a.set("app.server_a.z", 3)
        assert store_b.get("app.server_a.x") == 1
        assert store_a.get("app.server_b.y") == 2
        assert store_b.get("app.server_a.z") == 3
        print("OK  zwei Store-Objekte auf einer Datei: keine Schreibverluste")

        # Lock-Datei wird nach jedem Schreibvorgang wieder entfernt
        assert not os.path.exists(store_path + ".lock")
        print("OK  Lock-Datei nach set entfernt")

        # verwaiste Lock-Datei (aelter als LOCK_STALE_S) blockiert nicht
        with open(store_path + ".lock", "w") as f:
            f.write("99999")
        old = time.time() - LOCK_STALE_S - 10
        os.utime(store_path + ".lock", (old, old))
        store_a.set("app.after_stale", True)
        assert store_b.get("app.after_stale") is True
        print("OK  verwaiste Lock-Datei wird erkannt und entfernt")

        # frische fremde Lock-Datei fuehrt nach LOCK_TIMEOUT_S zu TimeoutError
        # (Timeout dafuer kurz setzen, sonst dauert der Selbsttest 5 s)
        global LOCK_TIMEOUT_S
        saved_timeout = LOCK_TIMEOUT_S
        LOCK_TIMEOUT_S = 0.2
        try:
            with open(store_path + ".lock", "w") as f:
                f.write("99999")
            try:
                store_a.set("app.blocked", 1)
            except TimeoutError:
                pass
            else:
                raise AssertionError("TimeoutError bei belegter Lock-Datei erwartet")
        finally:
            LOCK_TIMEOUT_S = saved_timeout
            if os.path.exists(store_path + ".lock"):
                os.unlink(store_path + ".lock")
        print("OK  belegte Lock-Datei -> TimeoutError nach LOCK_TIMEOUT_S")

        fail_queries = {"TRACe:UDP?", "OUTPut:BYTaux:STATe?"}
        transport = _FakeParamTransport(fail_queries)
        snap = snapshot_eb200(transport)
        total = len(EB200_QUERIES)
        assert snap["meta"]["n_ok"] == total - len(fail_queries)
        assert snap["meta"]["n_failed"] == len(fail_queries)
        for q in fail_queries:
            assert snap["params"][q] is None
            assert snap["errors"][q] == '-113,"Undefined header"'
        for _group, q in EB200_QUERIES:
            if q not in fail_queries:
                assert snap["params"][q] == f"ANTWORT:{q}"
                assert q not in snap["errors"]
        print("OK  snapshot_eb200: n_ok/n_failed korrekt, fehlgeschlagene Queries "
              "mit None/Fehlertext, restliche Queries roh als String")

        transport_paused = _FakeParamTransport(fail_queries)
        snap_paused = snapshot_eb200(transport_paused, pause_s=0.001)
        assert snap_paused["params"] == snap["params"]
        assert snap_paused["errors"] == snap["errors"]
        assert snap_paused["meta"]["n_ok"] == snap["meta"]["n_ok"]
        assert snap_paused["meta"]["n_failed"] == snap["meta"]["n_failed"]
        print("OK  snapshot_eb200 mit pause_s=0.001 liefert dasselbe Ergebnis wie ohne Pause")

        timeout_query = "SENSe:DEModulation?"
        idx = next(i for i, (_g, q) in enumerate(EB200_QUERIES) if q == timeout_query)
        next_query = EB200_QUERIES[idx + 1][1]
        transport2 = _FakeParamTransport(set(), timeout_query=timeout_query)
        snap2 = snapshot_eb200(transport2)
        assert snap2["params"][timeout_query] is None
        assert "Query fehlgeschlagen" in snap2["errors"][timeout_query]
        assert snap2["params"][next_query] == f"ANTWORT:{next_query}"
        assert snap2["meta"]["resyncs"] == 1
        print("OK  Resync nach Timeout: Folgequery erhaelt korrekten Wert (nicht STALE), "
              "meta.resyncs == 1")

        archive_path = store.put_eb200_snapshot(snap)
        assert os.path.exists(archive_path)
        assert SNAPSHOT_SUBDIR in archive_path
        with open(archive_path, "r", encoding="utf-8") as f:
            archived = json.load(f)
        assert archived["meta"]["n_ok"] == snap["meta"]["n_ok"]

        store3 = ParamStore(store_path)
        assert store3.get("eb200.meta.n_ok") == total - len(fail_queries)
        print("OK  put_eb200_snapshot legt Archivdatei an, Store enthaelt den Snapshot unter eb200")

        register_server("testsrv", 8099, {"root": "x"}, store_path=store_path)
        srv = ParamStore(store_path).get_namespace("app").get("testsrv", {})
        assert "started_at" in srv
        assert srv["http_port"] == 8099
        assert srv["pid"] == os.getpid()
        assert srv["root"] == "x"
        print("OK  register_server: started_at/http_port/pid + extra-Schluessel gesetzt")

        # Windows-Sharing-Violation simulieren: os.replace scheitert erst
        # 3-mal mit PermissionError, dann klappt es - der Retry in
        # _atomic_write_json muss das transparent ueberstehen.
        global REPLACE_RETRY_S
        saved_retry_s = REPLACE_RETRY_S
        REPLACE_RETRY_S = 0.001
        real_replace = os.replace
        calls = {"n": 0}

        def _flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError(13, "Permission denied")
            return real_replace(src, dst)

        try:
            os.replace = _flaky_replace
            store.set("app.retry.test", 42)
        finally:
            os.replace = real_replace
        assert store.get("app.retry.test") == 42
        assert calls["n"] == 4, f"erwartet 4 replace-Aufrufe, waren {calls['n']}"
        print("OK  os.replace-Retry: 3 PermissionError ueberstanden, Wert geschrieben")

        # Dauerhafter Fehler: nach REPLACE_RETRIES Versuchen fliegt die
        # Exception, die .tmp-Datei wird aufgeraeumt.
        def _broken_replace(src, dst):
            raise PermissionError(13, "Permission denied")

        try:
            os.replace = _broken_replace
            try:
                store.set("app.retry.fail", 1)
            except PermissionError:
                pass
            else:
                raise AssertionError("PermissionError nach letztem Retry erwartet")
        finally:
            os.replace = real_replace
            REPLACE_RETRY_S = saved_retry_s
        assert not os.path.exists(store_path + ".tmp"), ".tmp-Datei nach finalem Fehlschlag nicht entfernt"
        assert store.get("app.retry.test") == 42, "bestehende Daten nach Fehlschlag beschaedigt"
        print("OK  dauerhafter PermissionError -> Exception, .tmp aufgeraeumt, Bestand intakt")

    print("Selbsttest param_store erfolgreich abgeschlossen.")


if __name__ == "__main__":
    sys.exit(main())
