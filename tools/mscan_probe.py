#!/usr/bin/env python3
"""
tools/mscan_probe.py

Hardware-Pruefung fuer MSCAN (PLAN.md T-1004): programmiert eine kleine Zahl
EB200-Speicherplaetze (MEM0..MEMn-1) aus einer MSCAN-Job-Definition
(eb200_core/mscan_job.py) oder 5 festen UKW-Wien-Kanaelen (--demo), prueft per
Readback, ob Frequenz/Demodulation/Bandbreite/Squelch korrekt uebernommen
wurden, startet MSCAN (SENSe:MSCan:...) und wertet den resultierenden
MScan-UDP-Datagrammstrom aus - insbesondere, ob sich besuchte Speicherplaetze
ueber FREQuency:RX im Datagramm eindeutig einem Kanal zuordnen lassen (das
ist die Kern-Alternative zu SCPI-Polling fuer T-1005, siehe PLAN.md
Entscheidungslog).

Eigenstaendiges Werkzeug nach dem Vorbild von validate_tag_streams.py:
  - Muss laufen koennen, waehrend waterfall.py GESTOPPT ist (D-SCAN/IFPAN-
    Betrieb und MSCAN-Betrieb belegen denselben Empfaenger exklusiv) - siehe
    waterfall_running(). KEIN Import von waterfall.py.
  - Eigener, minimaler ScpiClient (Muster waterfall.ScpiClient bzw.
    validate_tag_streams.ScpiClient), keine gemeinsame Transport-Klasse.
  - eb200_core/scpi_verify.py WIRD importiert (Transport-Protokoll passt
    exakt - anders als bei validate_tag_streams.py, das vor scpi_verify.py
    entstand und die Vergleichslogik noch selbst mitbrachte).
  - eb200_core/mscan_job.py und eb200_core/known_freq.py WERDEN importiert
    (Job-Definition/Katalog lesen, siehe --job). KEIN Import von
    eb200_core/param_store.py - der Laufpruefung dient ein echter TCP-
    Verbindungsversuch (siehe waterfall_running()), keine Registrierung.
  - KEINE Aenderung an waterfall.py oder eb200_core/ - nur lesender Import
    bestehender Module.

GEKLAERT (Nachtrag 2, EB200-Handbuch Kap. MEMory/SENSe:MSCan + Annex F,
VERBINDLICH - nicht mehr geraten): der erste Hardware-Lauf (Nachtrag 1)
scheiterte an vier falschen Annahmen der Vorfassung dieses Werkzeugs; die
folgenden vier Punkte sind jetzt durch die Handbuch-/Annex-F-Fakten ersetzt:
  1. MSCAN-OptionalHeader-Layout: 10 Byte, 5 Felder je 2 Byte (short):
     cycleCount, holdTime, dwellTime, directionUp, stopSignal (Annex F).
     Vorfassung hatte die richtige Groesse/Feldzahl schon erraten, aber als
     UNSIGNED short - laut Annex F sind es SIGNED short (FMT_MSCAN_OPT_HDR).
  2. SelectorFlags (Annex F): LEVEL 0x01 (short, dBuV*10 wie DSCAN), OFFSET
     0x02, FSTRENGTH 0x04, CHANNEL 0x00010000 (unsigned short), FREQUENCY
     0x00020000 (unsigned long, Hz - NICHT 0x02 wie in der Vorfassung
     angenommen), SWAP 0x20000000, SIGNAL_GREATER_SQUELCH 0x40000000 (NICHT
     0x04 wie angenommen), OPTIONAL_HEADER 0x80000000. Trace-Daten stehen
     gruppiert nach Flag (erst alle LEVEL-Werte, dann alle FREQUENCY-Werte),
     NICHT verschachtelt wie in der Vorfassung angenommen - siehe parse_mscan().
     MSCAN-Tag (Annex F) = 201 (MSCAN_TAG), nicht die frei geratene 601.
  3. MEMory:CONTents:MPAR <name>,ON|OFF (Handbuch) - kein "<i>,ACT ON" wie in
     der Vorfassung geraten (Fehlercode -128 beim ersten Hardware-Lauf). Wird
     nur noch fuer --via-rx (Rueckfalloption) gebraucht; der neue Standardweg
     (direktes MEMory:CONTents, siehe run_phase_c()) aktiviert ACT gleich mit.
  4. SENSe:MSCan:CONTrol "STOP:SIGN" (Handbuch) - NICHT das im ersten
     Hardware-Lauf gesendete "Weiterlaufen"-Synonym (Fehlercode -113, kein
     gueltiger SCPI-Knoten). Aus: MSCan:CONTrol:OFF "STOP:SIGN". Abfrage:
     MSCan:CONTrol?.

Verbleibende bewusste Implementierungsentscheidung (kein offener Fakt, nur
eine Wahl zwischen zwei hardware-validierten Mustern): TRAC:UDP:TAG/FLAG fuer
MSC verwendet die DEF:TAG:ON/DEF:FLAG:ON-Form (Muster waterfall.py/
validate_tag_streams.py, dort fuer eine PRIMAERE, alleinstehende Registrierung
hardware-validiert) statt der im urspruenglichen Auftrag knapper geschriebenen
Form ohne DEF:/:ON-Suffix - der hier verwendete Port traegt immer nur die
eine, definierende Registrierung.

Nachtrag 3 (Hardware-Lauf 2, erfolgreich - Programmieren/Readback/Datagramme
OK, drei neue Befunde):
  - Readback-Format bestaetigt: MEMory:CONTents? liefert F/THR/BW als reine
    Zahl (kein Einheitensuffix), Antennenfeld korrekt als Definite-Length-
    Block geparst (Lauf 2: "#14(@0)"). Die vormalige OPEN_QUESTION dazu
    entfaellt.
  - Startverhalten: INIT:CONM setzt den Scan an der ZULETZT AKTIVEN Position
    fort statt bei MEM0 zu beginnen - MEM0 wurde in Lauf 2 dadurch nie
    besucht (Scan begann bei MEM1). Neue Option --start (Default chan999)
    springt vorher explizit auf MEM999, sodass die Aufwaertsfortsetzung bei
    MEM0 beginnt; --start imm nutzt stattdessen INITiate:IMMediate (Annex C:
    "Start scan upwards from start frequency"); --start conm ist das
    bisherige Verhalten (siehe run_phase_e()).
  - Zyklusende-Marker (EMPIRISCH aus Lauf 2, NICHT im Handbuch/Annex F
    belegt): am Ende eines Scan-Zyklus kommt ein Item mit freq_hz == 0 und
    Rohpegel >= 2000 - kein Messwert, sondern ein Marker (siehe
    _is_cycle_end_marker(), OPEN_QUESTIONS).

Nachtrag 4 (--with-audio lieferte 0 Audio-Datagramme): Ursache war der
fehlende Audio-Fernmodus - SYSTem:AUDio:REMote:MODe war nicht gesetzt.
run_phase_e() sendet bei --with-audio jetzt SYST:AUD:REM:MOD <n> (Default 6
= 16 kHz/16 Bit/mono, siehe waterfall.AUDIO_REMOTE_MODE) VOR der AUD-Tag-
Registrierung und liest per SYST:AUD:REM:MOD? zurueck; --audio-mode
ueberschreibt den Default. run_phase_g() setzt SYST:AUD:REM:MOD 0 nach
TAG:OFF wieder zurueck. Bei Modus 6 werden 32000 B/s bei offenem Squelch
erwartet (16000 Samples/s * 2 Byte); der Bericht rechnet die tatsaechliche
Bitrate aus und ordnet das Audio-Byte-Volumen je MScan-Item dem Fenster bis
zum jeweils naechsten Item zu (siehe print_final_report()).

Nachtrag 5 (Ueberwachungslauf: MScan-Items kommen nur bei Squelch-Oeffnung -
die vormalige Pausen-Heuristik _audio_during_gap_check() traf systematisch
das Dwell-Fenster NACH einem Item und meldete faelschlich Audio bei
geschlossenem Squelch): ersetzt durch eine Segmentierung (_segment_audio(),
siehe dort) - aufeinanderfolgende Audio-Datagramme mit Abstand
<= AUDIO_SEGMENT_GAP_S zum Vorgaenger bilden ein Segment; jedem Segment wird
per _assign_segment_item() das letzte MScan-Item mit t <= Segmentstart +
AUDIO_ITEM_ASSIGN_SLACK_S zugeordnet (oder keines). Segmente OHNE
zugeordnetes Item sind der tatsaechliche Beleg fuer Audio bei geschlossenem
Squelch (siehe print_final_report()). Das vormalige Item-Fenster-Volumen
(Nachtrag 4) bleibt zusaetzlich im Bericht - es beantwortet eine andere
Frage (wie viele Audio-Bytes fallen in den Zeitraum je Kanalbesuch), keine
Squelch-Aussage.

Jede Phase druckt Ueberschrift, gesendete Kommandos, Antworten und das
drain_errors()-Ergebnis - bei einem Fehlschlag soll aus der Konsolenausgabe
allein hervorgehen, WAS dem Geraet geschickt wurde und WAS zurueckkam.

Aufruf:
  python tools/mscan_probe.py --selftest                 # nur Parser, kein Geraet
  python tools/mscan_probe.py --demo --clear-all          # 5 UKW-Kanaele, echtes Geraet
  python tools/mscan_probe.py --job ABCDEF --clear-all --seconds 30

Nur Python-Standardbibliothek ausser den eb200_core-Importen. Konsolen-
ausgaben sind ASCII (cp1252-Konsole, Muster known_freq.py/mscan_job.py).
"""

from __future__ import annotations

import argparse
import re
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass

# Erlaubt den Direktaufruf "python tools/mscan_probe.py" (Selbsttest-
# Konvention dieses Projekts, siehe eb200_core/job_runner.py): ohne diesen
# Eintrag fehlt das Repo-Root im Suchpfad fuer die eb200_core-Importe.
if __package__ in (None, ""):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.scpi_verify import cmp_bw, cmp_enum, cmp_freq, cmp_int, drain_errors, wait_opc
from eb200_core.mscan_job import DEFAULT_PARAMS, INVENTUR_DEFAULTS, MscanJobStore, resolve_snapshot
from eb200_core.known_freq import load_catalog_cached

# ---------------------------------------------------------------------------
# Offene Punkte (siehe Moduldoku oben) - am Ende jedes Laufs mit ausgegeben.
# Die vier zuvor hier gelisteten Annahmen (OptionalHeader-Layout, Flag-Werte,
# MPAR-Syntax, Scan-Steuerungs-Syntax) sowie Tag-Nummer und Bestandsaufnahme
# sind seit Nachtrag 2 GEKLAERT; das Readback-Format (Nachtrag 2 offen
# gelassen) und die Mehr-Item-Auswertung (vormals nur erstes Paar) sind seit
# Nachtrag 3 ebenfalls GEKLAERT bzw. behoben (siehe Moduldoku oben) und
# daher hier entfernt.
# ---------------------------------------------------------------------------

OPEN_QUESTIONS = [
    "Zyklusende-Marker (freq_hz == 0 UND Rohpegel >= 2000, siehe "
    "_is_cycle_end_marker()) ist aus Hardware-Lauf 2 EMPIRISCH beobachtet - "
    "im Handbuch/Annex F kein dokumentierter Marker-Mechanismus gefunden. "
    "Handbuchbeleg offen.",
]


# ---------------------------------------------------------------------------
# Kopf-Konstanten
# ---------------------------------------------------------------------------

EB200_IP_DEFAULT = "10.0.0.55"     # wie waterfall.EB200_IP
SCPI_PORT = 5555

SO_RCVBUF_BYTES = 1_048_576
SETTLE_S = 1.0    # kurze Anlaufzeit nach TAG:ON, bis das Geraet den Tag bedient
GAP_ENDS_INVENTUR_S = 5.0   # inventur: Lauf endet nach so vielen Sekunden Stille (siehe Auftrag)

# Zyklusende-Marker (Nachtrag 3, aus Hardware-Lauf 2 empirisch beobachtet,
# Handbuchbeleg offen - siehe OPEN_QUESTIONS/_is_cycle_end_marker()): ein Item
# mit freq_hz == 0 und Rohpegel >= 2000 (= level_dbuv >= 200.0, da level_dbuv
# = Rohpegel/10) ist ein Marker, kein Messwert.
MARKER_LEVEL_DBUV_MIN = 200.0

UKW_WIEN_DEMO = [
    {"id": "demo-886", "label": "88.6 MHz", "freq_hz": 88_600_000,
     "demod": "FM", "bw_hz": 150_000, "squelch_dbuv": 20},
    {"id": "demo-899", "label": "89.9 MHz", "freq_hz": 89_900_000,
     "demod": "FM", "bw_hz": 150_000, "squelch_dbuv": 20},
    {"id": "demo-911", "label": "91.1 MHz", "freq_hz": 91_100_000,
     "demod": "FM", "bw_hz": 150_000, "squelch_dbuv": 20},
    {"id": "demo-920", "label": "92.0 MHz", "freq_hz": 92_000_000,
     "demod": "FM", "bw_hz": 150_000, "squelch_dbuv": 20},
    {"id": "demo-958", "label": "95.8 MHz", "freq_hz": 95_800_000,
     "demod": "FM", "bw_hz": 150_000, "squelch_dbuv": 20},
]


# ---------------------------------------------------------------------------
# Protokollkonstanten (Annex F) - Muster waterfall.py/validate_tag_streams.py
# ---------------------------------------------------------------------------

MAGIC_NUMBER = 0x000EB200

# Annex F, verifiziert (Nachtrag 2) - Live-Auswertung (run_phase_f) filtert
# jetzt nach dieser Nummer, fremde Tags werden gezaehlt statt verworfen.
MSCAN_TAG = 201

# Annex F SelectorFlags, verifiziert (Nachtrag 2). OFFSET/FSTRENGTH/CHANNEL/
# SWAP werden von diesem Werkzeug nicht ausgewertet (kein Anzeigebedarf),
# stehen aber vollstaendig hier, falls spaeter gebraucht.
FLAG_LEVEL = 0x01                       # short, dBuV*10 wie DSCAN
FLAG_OFFSET = 0x02                      # nicht ausgewertet
FLAG_FSTRENGTH = 0x04                   # nicht ausgewertet
FLAG_CHANNEL = 0x00010000               # unsigned short, nicht ausgewertet
FLAG_FREQUENCY = 0x00020000             # unsigned long, Hz
FLAG_SWAP = 0x20000000                  # nicht ausgewertet
FLAG_SIGNAL_GREATER_SQUELCH = 0x40000000
FLAG_OPTIONAL_HEADER = 0x80000000

FMT_EB200_HEADER = ">IHHH6s"          # magic, minor, major, sequence_number, reserved
LEN_EB200_HEADER = struct.calcsize(FMT_EB200_HEADER)

FMT_GENERIC_ATTR_HDR = ">HH"          # tag, length
LEN_GENERIC_ATTR_HDR = struct.calcsize(FMT_GENERIC_ATTR_HDR)

FMT_TRACE_ATTR_HDR = ">hBBI"          # n_items, reserved, opt_header_len, selector_flags
LEN_TRACE_ATTR_HDR = struct.calcsize(FMT_TRACE_ATTR_HDR)

# Annex F, verifiziert (Nachtrag 2): MScanTrace-OptionalHeader, 10 Byte, 5
# SIGNED short-Felder: cycleCount, holdTime, dwellTime, directionUp, stopSignal.
FMT_MSCAN_OPT_HDR = ">hhhhh"          # cycleCount, holdTime, dwellTime, directionUp, stopSignal
LEN_MSCAN_OPT_HDR = struct.calcsize(FMT_MSCAN_OPT_HDR)

MSCAN_MEM_SLOTS = 1000   # MEM0..MEM999 (Bestandsaufnahme Phase B, Hochrechnung Phase C)


def parse_datagram(raw: bytes) -> list[tuple[int, bytes]]:
    """Zerlegt ein EB200-UDP-Datagramm in (tag, payload_bytes)-Paare (Muster
    waterfall.py.parse_datagram - anders als validate_tag_streams.py wird
    hier die Payload selbst mitgeliefert, da parse_mscan() sie braucht)."""
    if len(raw) < LEN_EB200_HEADER:
        raise ValueError("Datagramm zu kurz fuer EB200-Header")
    magic = struct.unpack_from(">I", raw, 0)[0]
    if magic != MAGIC_NUMBER:
        raise ValueError(f"unerwartete Magic Number {magic:#x}")
    attrs: list[tuple[int, bytes]] = []
    offset = LEN_EB200_HEADER
    while offset + LEN_GENERIC_ATTR_HDR <= len(raw):
        tag, length = struct.unpack_from(FMT_GENERIC_ATTR_HDR, raw, offset)
        offset += LEN_GENERIC_ATTR_HDR
        attrs.append((tag, raw[offset:offset + length]))
        offset += length
    return attrs


def datagram_seq(raw: bytes) -> int:
    """sequence_number aus dem EB200-Header (Offset 8, uint16 Big-Endian),
    zaehlt PRO UdpPath fortlaufend hoch und laeuft bei 65536 ueber (Muster
    waterfall.py.datagram_seq)."""
    if len(raw) < LEN_EB200_HEADER:
        raise ValueError("Datagramm zu kurz fuer sequence_number")
    return struct.unpack_from(">H", raw, 8)[0]


@dataclass
class MscanResult:
    """Ergebnis von parse_mscan(). levels_dbuv/freqs_hz sind PARALLELE Listen
    (je ein Eintrag pro Item, siehe Trace-Layout in parse_mscan) - die
    Convenience-Properties level_dbuv/freq_hz liefern nur das jeweils ERSTE
    Item (der erwartete Normalfall n_items==1 je besuchtem Kanal, siehe
    OPEN_QUESTIONS). Die fuenf cycle_count..stop_condition-Felder kommen aus
    dem (optionalen) MSCAN-OptionalHeader (Annex F, verifiziert)."""
    levels_dbuv: list[float]
    freqs_hz: list[int]
    signal_greater_squelch: bool
    has_optional_header: bool
    cycle_count: int | None = None
    hold_time: int | None = None
    dwell_time: int | None = None
    scan_direction: int | None = None
    stop_condition: int | None = None

    @property
    def level_dbuv(self) -> float | None:
        return self.levels_dbuv[0] if self.levels_dbuv else None

    @property
    def freq_hz(self) -> int | None:
        return self.freqs_hz[0] if self.freqs_hz else None


def parse_mscan(payload: bytes) -> MscanResult:
    """Zerlegt die Payload EINES MSC-GenericAttribute (also payload_bytes aus
    parse_datagram(), NICHT das ganze Datagramm) in Level/Frequenz/Header-
    Felder. Layout gemaess Annex F, verifiziert (Nachtrag 2, siehe Moduldoku):
    Trace-Daten stehen GRUPPIERT nach Flag - erst n_items LEVEL-Werte (je
    short), dann n_items FREQUENCY-Werte (je unsigned long) - NICHT
    verschachtelt wie in der Vorfassung angenommen."""
    if len(payload) < LEN_TRACE_ATTR_HDR:
        raise ValueError("MSCAN-Attribut zu kurz fuer Trace-Attribut-Kopf")
    n_items, _reserved, opt_len, sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, payload, 0)
    pos = LEN_TRACE_ATTR_HDR

    has_opt = bool(sel_flags & FLAG_OPTIONAL_HEADER) and opt_len > 0
    cycle_count = hold_time = dwell_time = scan_direction = stop_condition = None
    if has_opt:
        if opt_len < LEN_MSCAN_OPT_HDR or pos + LEN_MSCAN_OPT_HDR > len(payload):
            raise ValueError(f"MSCAN-OptionalHeader zu kurz (opt_len={opt_len})")
        cycle_count, hold_time, dwell_time, scan_direction, stop_condition = \
            struct.unpack_from(FMT_MSCAN_OPT_HDR, payload, pos)
    pos += opt_len

    levels_dbuv: list[float] = []
    if sel_flags & FLAG_LEVEL:
        need = n_items * 2
        if pos + need > len(payload):
            raise ValueError("MSCAN-Attribut zu kurz fuer LEVEL-Werte")
        raw_levels = struct.unpack_from(f">{n_items}h", payload, pos)
        levels_dbuv = [v / 10.0 for v in raw_levels]
        pos += need

    freqs_hz: list[int] = []
    if sel_flags & FLAG_FREQUENCY:
        need = n_items * 4
        if pos + need > len(payload):
            raise ValueError("MSCAN-Attribut zu kurz fuer FREQUENCY-Werte")
        freqs_hz = list(struct.unpack_from(f">{n_items}I", payload, pos))
        pos += need

    return MscanResult(
        levels_dbuv=levels_dbuv, freqs_hz=freqs_hz,
        signal_greater_squelch=bool(sel_flags & FLAG_SIGNAL_GREATER_SQUELCH),
        has_optional_header=has_opt,
        cycle_count=cycle_count, hold_time=hold_time, dwell_time=dwell_time,
        scan_direction=scan_direction, stop_condition=stop_condition,
    )


def _is_cycle_end_marker(freq_hz: int | None, level_dbuv: float | None) -> bool:
    """Ein MSCAN-Item mit freq_hz == 0 und Rohpegel >= 2000 (level_dbuv >=
    MARKER_LEVEL_DBUV_MIN) ist ein Zyklusende-Marker, KEIN Messwert - aus
    Hardware-Lauf 2 empirisch beobachtet, im Handbuch/Annex F nicht belegt
    (siehe OPEN_QUESTIONS). Beide Bedingungen sind noetig: freq_hz == 0
    allein (z. B. ein leerer/undefinierter Platz) ist KEIN Marker."""
    return freq_hz == 0 and level_dbuv is not None and level_dbuv >= MARKER_LEVEL_DBUV_MIN


# ---------------------------------------------------------------------------
# Minimaler SCPI-Client (Muster: waterfall.py ScpiClient / validate_tag_streams.py)
# ---------------------------------------------------------------------------

class ScpiClient:
    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
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


def determine_local_ip(peer_ip: str) -> str:
    """Ermittelt die lokale IP, ueber die peer_ip erreichbar waere, ohne
    tatsaechlich Daten zu senden (Muster waterfall.py/validate_tag_streams.py)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer_ip, 1))
        return probe.getsockname()[0]
    finally:
        probe.close()


def open_udp_socket(port: int) -> socket.socket:
    """Oeffnet + bindet einen UDP-Empfangssocket - dient zugleich als
    Bindbarkeits-Check (Muster validate_tag_streams.open_udp_socket): ein
    fehlgeschlagener bind() deutet meist auf einen anderen, den Port bereits
    belegenden Prozess hin."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_BYTES)
    except OSError:
        pass
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        sock.close()
        raise RuntimeError(
            f"Port {port} ist nicht bindbar ({exc}). Belegt ein anderer Prozess "
            "diesen Port auf diesem Rechner?"
        ) from exc
    sock.settimeout(0.5)
    return sock


def waterfall_running(port: int = 8000, timeout: float = 0.5) -> bool:
    """Prueft per echtem TCP-Verbindungsversuch, ob auf localhost:port gerade
    ein HTTP-Server lauscht (typischerweise waterfall.py) - harter Beweis,
    unabhaengig von jedem gespeicherten Zustand. Erfolg -> True (Socket wird
    sofort wieder geschlossen, es wird nichts gesendet); jeder Verbindungs-
    fehler (abgelehnt, Timeout, ...) -> False.

    BEWUSST KEIN param_store-Check mehr (fruehere Fassung dieser Funktion):
    register_server() (eb200_core/param_store.py) hat KEIN Gegenstueck, das
    den Eintrag beim Beenden wieder entfernt - weder ein normales Beenden
    noch ein harter Abbruch/Kill raeumen app.waterfall.http_port auf. Der
    Eintrag ueberlebt also unveraendert sowohl einen Kill als auch einen
    Neustart der Station, selbst wenn waterfall.py laengst nicht mehr laeuft
    - eine Pruefung darauf waere nach JEDEM Server-Stopp falsch-positiv
    (dieses Werkzeug wuerde dauerhaft faelschlich abbrechen). Der echte
    Verbindungsversuch hat dieses Problem nicht: er ist wahr genau dann,
    wenn dort gerade tatsaechlich etwas lauscht."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Kanalliste / Parameter aufloesen
# ---------------------------------------------------------------------------

def resolve_channels(args: argparse.Namespace) -> list[dict]:
    """--demo: 5 feste UKW-Wien-Kanaele. --job: Job per MscanJobStore laden,
    auf die ersten --max-channels kuerzen (VOR dem Aufloesen, spart unnoetige
    Katalog-Lookups bei grossen Jobs) und per resolve_snapshot() gegen den
    aktuellen Katalog aufloesen (siehe eb200_core/mscan_job.py). Wirft
    KeyError/ValueError bei unbekannter job_id bzw. fehlender Kanal-ID -
    Aufrufer (main()) wandelt das in eine Fehlermeldung + Exit 1 um."""
    if args.demo:
        return UKW_WIEN_DEMO[: args.max_channels]
    store = MscanJobStore()
    job = dict(store.get(args.job))
    job["channels"] = job["channels"][: args.max_channels]
    catalog = load_catalog_cached()
    return resolve_snapshot(job, catalog)


def resolve_dwell_hold(args: argparse.Namespace) -> tuple[float | None, float]:
    """Dwell/Hold aus --dwell/--hold, sonst je nach --mode aus
    mscan_job.INVENTUR_DEFAULTS bzw. mscan_job.DEFAULT_PARAMS (siehe Auftrag)."""
    defaults = INVENTUR_DEFAULTS if args.mode == "inventur" else DEFAULT_PARAMS
    dwell = args.dwell if args.dwell is not None else defaults["dwell_s"]
    hold = args.hold if args.hold is not None else defaults["hold_s"]
    return dwell, hold


# ---------------------------------------------------------------------------
# Selbsttest (--selftest, Phase 0, kein Geraet noetig)
# ---------------------------------------------------------------------------

def build_synthetic_datagram(seq: int, attrs: list[tuple[int, bytes]]) -> bytes:
    """Baut ein synthetisches EB200-Datagramm (Muster validate_tag_streams.py)."""
    header = struct.pack(FMT_EB200_HEADER, MAGIC_NUMBER, 1, 0, seq, b"\x00" * 6)
    body = b""
    for tag, payload in attrs:
        body += struct.pack(FMT_GENERIC_ATTR_HDR, tag, len(payload)) + payload
    return header + body


def build_mscan_payload(*, levels_dbuv: list[float] | None = None,
                        freqs_hz: list[int] | None = None,
                        signal_greater_squelch: bool = False,
                        header_fields: tuple[int, int, int, int, int] | None = None,
                        n_items: int | None = None) -> bytes:
    """Baut die Payload-Bytes EINES MSC-GenericAttribute fuer den Selbsttest -
    Trace-Daten GRUPPIERT nach Flag (erst alle LEVEL-, dann alle FREQUENCY-
    Werte, siehe parse_mscan), NICHT als (level,freq)-Paare. header_fields=None
    -> kein OptionalHeader (opt_len 0); sonst ein 5-Tupel (cycleCount,
    holdTime, dwellTime, directionUp, stopSignal)."""
    levels_dbuv = levels_dbuv or []
    freqs_hz = freqs_hz or []
    sel_flags = 0
    opt_bytes = b""
    if header_fields is not None:
        sel_flags |= FLAG_OPTIONAL_HEADER
        opt_bytes = struct.pack(FMT_MSCAN_OPT_HDR, *header_fields)
    trace_bytes = b""
    if levels_dbuv:
        sel_flags |= FLAG_LEVEL
        trace_bytes += struct.pack(f">{len(levels_dbuv)}h", *(round(v * 10) for v in levels_dbuv))
    if freqs_hz:
        sel_flags |= FLAG_FREQUENCY
        trace_bytes += struct.pack(f">{len(freqs_hz)}I", *freqs_hz)
    if signal_greater_squelch:
        sel_flags |= FLAG_SIGNAL_GREATER_SQUELCH
    if n_items is None:
        n_items = max(len(levels_dbuv), len(freqs_hz), 1)
    header = struct.pack(FMT_TRACE_ATTR_HDR, n_items, 0, len(opt_bytes), sel_flags)
    return header + opt_bytes + trace_bytes


def run_selftest() -> None:
    print("Parser-Selbsttest (MSCAN) ...")

    # 1) volles Datagramm (Muster Auftrag Nachtrag 2): Tag 201, OptionalHeader
    #    (10 Byte), Flags 0xC0020001 (LEVEL|FREQUENCY|SIGNAL_GREATER_SQUELCH|
    #    OPTIONAL_HEADER), 2 Items: 2 Level-shorts, dann 2 Frequenz-ulongs.
    payload1 = build_mscan_payload(
        levels_dbuv=[45.3, 12.7], freqs_hz=[98_500_000, 99_100_000],
        signal_greater_squelch=True, header_fields=(3, 20, 30, 1, 0),
    )
    raw1 = build_synthetic_datagram(1, [(MSCAN_TAG, payload1)])
    attrs1 = parse_datagram(raw1)
    assert len(attrs1) == 1 and attrs1[0][0] == MSCAN_TAG, attrs1
    assert datagram_seq(raw1) == 1, datagram_seq(raw1)
    n_items_chk, _res_chk, opt_len_chk, sel_flags_chk = struct.unpack_from(
        FMT_TRACE_ATTR_HDR, attrs1[0][1], 0)
    assert n_items_chk == 2, n_items_chk
    assert opt_len_chk == LEN_MSCAN_OPT_HDR == 10, opt_len_chk
    assert sel_flags_chk == 0xC0020001, hex(sel_flags_chk)
    r1 = parse_mscan(attrs1[0][1])
    assert r1.levels_dbuv == [45.3, 12.7], r1.levels_dbuv
    assert r1.freqs_hz == [98_500_000, 99_100_000], r1.freqs_hz
    assert r1.level_dbuv == 45.3 and r1.freq_hz == 98_500_000, r1   # Convenience: erstes Item
    assert r1.signal_greater_squelch is True, r1
    assert r1.has_optional_header is True, r1
    assert (r1.cycle_count, r1.hold_time, r1.dwell_time, r1.scan_direction, r1.stop_condition) \
        == (3, 20, 30, 1, 0), r1
    print("  OK  volles Datagramm (Tag 201, Flags 0xC0020001, 2 Items gruppiert "
          "LEVEL dann FREQUENCY, OptionalHeader 5 Felder) korrekt")

    # 2) Datagramm OHNE OptionalHeader (opt_len 0) - Header-Felder muessen
    #    None sein, level/freq bleiben lesbar (n_items == 1).
    payload2 = build_mscan_payload(levels_dbuv=[12.0], freqs_hz=[100_000_000], header_fields=None)
    raw2 = build_synthetic_datagram(2, [(MSCAN_TAG, payload2)])
    attrs2 = parse_datagram(raw2)
    r2 = parse_mscan(attrs2[0][1])
    assert r2.has_optional_header is False, r2
    assert r2.cycle_count is None and r2.stop_condition is None, r2
    assert r2.freq_hz == 100_000_000 and abs(r2.level_dbuv - 12.0) < 1e-6, r2
    print("  OK  Datagramm ohne OptionalHeader: Header-Felder None, level/freq weiterhin lesbar")

    # 3) kaputte Magic Number -> ValueError
    bad = bytearray(raw1)
    bad[0:4] = struct.pack(">I", 0xDEADBEEF)
    try:
        parse_datagram(bytes(bad))
    except ValueError:
        pass
    else:
        raise AssertionError("kaputte Magic Number wurde nicht als Fehler erkannt")
    print("  OK  kaputte Magic Number -> ValueError")

    # 4) zu kurzes MSCAN-Attribut (OptionalHeader mittendrin abgeschnitten) -> ValueError
    try:
        parse_mscan(payload1[:LEN_TRACE_ATTR_HDR + 2])
    except ValueError:
        pass
    else:
        raise AssertionError("zu kurzer OptionalHeader wurde nicht als Fehler erkannt")
    print("  OK  zu kurzes MSCAN-Attribut (OptionalHeader abgeschnitten) -> ValueError")

    # 5) zu kurzes Gesamt-Datagramm (nur die ersten 4 Byte) -> ValueError
    try:
        parse_datagram(raw1[:4])
    except ValueError:
        pass
    else:
        raise AssertionError("zu kurzes Datagramm wurde nicht als Fehler erkannt")
    print("  OK  zu kurzes Datagramm -> ValueError")

    # 6) fremder Tag im selben Port-Strom -> muss von der Live-Auswertung
    #    zaehlbar bleiben (Phase F filtert jetzt nach MSCAN_TAG, siehe dort).
    raw3 = build_synthetic_datagram(3, [(999, b"\x00\x01")])
    attrs3 = parse_datagram(raw3)
    assert attrs3[0][0] == 999 and attrs3[0][0] != MSCAN_TAG, attrs3
    print("  OK  fremder Tag (!= 201) bleibt als eigenstaendiges Attribut erkennbar")

    # 7) 2-Item-Datagramm mit Zyklusende-Marker im 2. Item (Nachtrag 3,
    #    Befund Lauf 2): Item 0 ein normaler Kanalwert, Item 1 der Marker
    #    (freq_hz == 0, Rohpegel >= 2000 -> level_dbuv >= MARKER_LEVEL_DBUV_MIN).
    payload7 = build_mscan_payload(
        levels_dbuv=[33.5, 250.0], freqs_hz=[99_500_000, 0],
        header_fields=(5, 20, 30, 1, 0),
    )
    raw7 = build_synthetic_datagram(7, [(MSCAN_TAG, payload7)])
    r7 = parse_mscan(parse_datagram(raw7)[0][1])
    assert r7.levels_dbuv == [33.5, 250.0] and r7.freqs_hz == [99_500_000, 0], r7
    assert not _is_cycle_end_marker(r7.freqs_hz[0], r7.levels_dbuv[0]), "Item 0 ist kein Marker"
    assert _is_cycle_end_marker(r7.freqs_hz[1], r7.levels_dbuv[1]), "Item 1 muss als Marker erkannt werden"
    print("  OK  2-Item-Datagramm: Item 0 normaler Kanalwert, Item 1 Zyklusende-Marker erkannt")

    # 8) Marker-Erkennung braucht BEIDE Bedingungen (freq==0 UND Rohpegel>=2000) -
    #    je nur eine davon ist KEIN Marker (nicht raten, nur pruefen).
    assert not _is_cycle_end_marker(0, 15.0), "freq=0 mit niedrigem Pegel ist kein Marker"
    assert not _is_cycle_end_marker(100_000_000, 250.0), "hoher Pegel ohne freq=0 ist kein Marker"
    assert not _is_cycle_end_marker(0, None), "freq=0 ohne Pegelwert ist kein Marker"
    print("  OK  Marker-Erkennung nur bei freq==0 UND Rohpegel>=2000 (beide Bedingungen noetig)")

    print("\nParser-Selbsttest OK.")


# ---------------------------------------------------------------------------
# Phasen A-G
# ---------------------------------------------------------------------------

def run_phase_a(ip: str, scpi_port: int, ignore_running: bool = False) -> ScpiClient:
    print("=== Phase A: Verbindung ===")
    if waterfall_running():
        msg = "waterfall.py laeuft (Port 8000 antwortet) - bitte stoppen."
        if not ignore_running:
            raise RuntimeError(msg)
        print(f"  WARNUNG: {msg} --ignore-running gesetzt, mache trotzdem weiter.")
    else:
        print("  waterfall.py (:8000) nicht aktiv - OK")
    scpi = ScpiClient(ip, scpi_port)
    idn = scpi.query("*IDN?")
    print(f"  *IDN? -> {idn}")
    errors = drain_errors(scpi)
    print(f"  drain_errors -> {errors if errors else '(leer)'}")
    return scpi


def run_phase_b(scpi: ScpiClient, skip_inventory: bool = False) -> None:
    print("=== Phase B: Grundzustand ===")
    for cmd in ("ABORt", "FREQ:MODE CW", "OUTP:SQU:CONT NONE", "FORMat:MEMory ASCii"):
        scpi.send(cmd)
        print(f"  send {cmd}")
    errors = drain_errors(scpi)
    print(f"  drain_errors -> {errors if errors else '(leer)'}")

    if skip_inventory:
        print("  Bestandsaufnahme uebersprungen (--skip-inventory).")
        return

    print(f"  Bestandsaufnahme vorhandener Speicherplaetze "
          f"(MEMory:CONTents:MPAR? MEM0..MEM{MSCAN_MEM_SLOTS - 1}) ...")
    t0 = time.monotonic()
    active = 0
    query_errors = 0
    for i in range(MSCAN_MEM_SLOTS):
        try:
            resp = scpi.query(f"MEMory:CONTents:MPAR? MEM{i}")
        except Exception:
            query_errors += 1
            continue
        if resp.strip() in ("1", "ON"):
            active += 1
    dt = time.monotonic() - t0
    print(f"  {active} von {MSCAN_MEM_SLOTS} Speicherplaetzen aktiv (ACT=1), "
          f"{query_errors} Abfragefehler - Gesamtdauer {dt:.1f} s "
          f"({dt / MSCAN_MEM_SLOTS * 1000:.1f} ms/Abfrage)")


# ---------------------------------------------------------------------------
# MEMory:CONTents - Parsen von Definite-Length-Bloecken (Antennenfeld) und
# der vollstaendigen Feldliste (Handbuch, verifiziert Nachtrag 2).
# ---------------------------------------------------------------------------

_ANTENNA_FIELD_RE = re.compile(r"#(\d)(\d+)\(@(\d+)\)")


def _parse_antenna_field(field: str) -> int:
    """Zerlegt ein IEEE-488.2-Definite-Length-Block-Feld wie '#14(@1)' (aus
    MEMory:CONTents?, Antennenfeld) und liefert die Antennennummer (hier: 1).
    '#1' = ein Ziffernzeichen fuer die folgende Laengenangabe, '4' = Laenge
    des Blockinhalts in Byte, '(@1)' = der eigentliche Blockinhalt."""
    m = _ANTENNA_FIELD_RE.match(field.strip())
    if not m:
        raise ValueError(f"unerwartetes Antennenfeld-Format: {field!r}")
    return int(m.group(3))


def _parse_memory_contents(resp: str) -> dict:
    """Zerlegt die Antwort von MEMory:CONTents? <name> in ihre 10 Felder
    (Handbuch: 'F,THR,DEM,BW,#14(@1),ATT,ATTA,SQUC,AFC,ACT', Query-Antwort
    ohne den fuehrenden Namen). F/THR/BW werden als reine Zahl erwartet (wie
    FREQ?/BAND? im uebrigen Code) - siehe OPEN_QUESTIONS zu dieser Annahme."""
    parts = [p.strip() for p in resp.strip().split(",")]
    if len(parts) != 10:
        raise ValueError(f"MEMory:CONTents?-Antwort hat {len(parts)} statt 10 Felder: {resp!r}")
    freq_hz, thr, dem, bw_hz, ant_field, att, atta, squc, afc, act = parts
    return {
        "freq_hz": int(round(float(freq_hz))),
        "thr": int(round(float(thr))),
        "dem": dem,
        "bw_hz": int(round(float(bw_hz))),
        "ant": _parse_antenna_field(ant_field),
        "att": att, "atta": atta, "squc": squc, "afc": afc, "act": act,
    }


def run_phase_c(scpi: ScpiClient, channels: list[dict], via_rx: bool = False) -> dict:
    print("=== Phase C: Speicher programmieren ===")
    scpi.send("MEMory:CLEar MEM0,MAXimum")
    wait_opc(scpi)
    errors = drain_errors(scpi)
    print(f"  MEMory:CLEar MEM0,MAXimum -> drain_errors {errors if errors else '(leer)'}")

    if via_rx:
        return _run_phase_c_via_rx(scpi, channels)

    # Antennennummer einmalig aus dem aktuellen RX-Zustand lesen (Feld 5) und
    # fuer alle Plaetze uebernehmen (Handbuch: MEMory:CONTents? RX liefert
    # den aktuellen Empfaengerzustand, ACT dort ist undefiniert/irrelevant).
    ant = _parse_memory_contents(scpi.query("MEMory:CONTents? RX"))["ant"]
    print(f"  Antenne aus MEMory:CONTents? RX uebernommen: (@{ant})")

    per_channel_errors: dict[int, list[str]] = {}
    durations_s: list[float] = []
    for i, ch in enumerate(channels):
        t0 = time.monotonic()
        squelch = ch.get("squelch_dbuv")
        if squelch is None:
            squelch = 20
        # ATT 0, ATTA 1, SQUC 1, AFC 0, ACT 1 (siehe Auftrag Nachtrag 2).
        cmd = (f"MEMory:CONTents MEM{i},{int(ch['freq_hz'])},{int(squelch)},"
               f"{ch['demod']},{int(ch['bw_hz'])},(@{ant}),0,1,1,0,1")
        scpi.send(cmd)
        wait_opc(scpi)
        errs = drain_errors(scpi)
        dt = time.monotonic() - t0
        durations_s.append(dt)
        if errs:
            per_channel_errors[i] = errs
        print(f"  MEM{i} <- {ch.get('label', ch['id'])} ({ch['freq_hz'] / 1e6:.4f} MHz, "
              f"{ch['demod']}, {int(ch['bw_hz'])} Hz, Squelch {int(squelch)} dBuV) - "
              f"{dt * 1000:.0f} ms - Fehler: {errs if errs else '(keine)'}")

    mean_s = sum(durations_s) / len(durations_s) if durations_s else 0.0
    print(f"  Mittelwert je Kanal: {mean_s * 1000:.1f} ms - Hochrechnung 1000 Kanaele: "
          f"{mean_s * 1000:.1f} s")
    return {"errors": per_channel_errors, "mean_s": mean_s, "n": len(channels)}


def _run_phase_c_via_rx(scpi: ScpiClient, channels: list[dict]) -> dict:
    """Rueckfalloption --via-rx: alter RX-Umweg (Abstimmen + MEMory:COPY
    RX,MEMn + MEMory:CONTents:MPAR MEMn,ON - korrigierte Syntax gegenueber
    der Vorfassung, siehe Moduldoku)."""
    per_channel_errors: dict[int, list[str]] = {}
    durations_s: list[float] = []
    for i, ch in enumerate(channels):
        t0 = time.monotonic()
        squelch = ch.get("squelch_dbuv")
        if squelch is None:
            squelch = 20
        for cmd in (
            f"FREQ {int(ch['freq_hz'])}",
            f"DEM {ch['demod']}",
            f"BAND {int(ch['bw_hz'])}",
            f"OUTP:SQU:THR {int(squelch)}",
        ):
            scpi.send(cmd)
        wait_opc(scpi)
        scpi.send(f"MEMory:COPY RX,MEM{i}")
        wait_opc(scpi)
        scpi.send(f"MEMory:CONTents:MPAR MEM{i},ON")
        wait_opc(scpi)
        errs = drain_errors(scpi)
        dt = time.monotonic() - t0
        durations_s.append(dt)
        if errs:
            per_channel_errors[i] = errs
        print(f"  MEM{i} <- {ch.get('label', ch['id'])} (--via-rx) ({ch['freq_hz'] / 1e6:.4f} MHz, "
              f"{ch['demod']}, {int(ch['bw_hz'])} Hz, Squelch {int(squelch)} dBuV) - "
              f"{dt * 1000:.0f} ms - Fehler: {errs if errs else '(keine)'}")

    mean_s = sum(durations_s) / len(durations_s) if durations_s else 0.0
    print(f"  Mittelwert je Kanal: {mean_s * 1000:.1f} ms - Hochrechnung 1000 Kanaele: "
          f"{mean_s * 1000:.1f} s")
    return {"errors": per_channel_errors, "mean_s": mean_s, "n": len(channels)}


def run_phase_d(scpi: ScpiClient, channels: list[dict]) -> list[dict]:
    """Liest jeden programmierten Platz direkt per MEMory:CONTents?/MPAR?
    zurueck - KEIN MEMory:COPY mehr noetig (das galt nur fuer den alten
    RX-Umweg, siehe --via-rx): MEMory:CONTents? fragt den gespeicherten
    Platz unabhaengig davon ab, wie er programmiert wurde."""
    print("=== Phase D: Readback ===")
    rows = []
    for i, ch in enumerate(channels):
        squelch = ch.get("squelch_dbuv")
        if squelch is None:
            squelch = 20
        results: list[tuple[str, bool, str]] = []

        try:
            parsed = _parse_memory_contents(scpi.query(f"MEMory:CONTents? MEM{i}"))
        except Exception as exc:   # noqa: BLE001 - Query kann vielfaeltig scheitern (Timeout, Parsefehler, ...)
            results.append(("MEMory:CONTents?", False, f"Antwort nicht auswertbar: {exc}"))
            parsed = None

        if parsed is not None:
            ok, det = cmp_freq(10.0)(int(ch["freq_hz"]), str(parsed["freq_hz"]))
            results.append(("FREQ", ok, det))
            ok, det = cmp_int(int(squelch), str(parsed["thr"]))
            results.append(("THR", ok, det))
            ok, det = cmp_enum(ch["demod"], parsed["dem"])
            results.append(("DEM", ok, det))
            ok, det = cmp_bw()(int(ch["bw_hz"]), str(parsed["bw_hz"]))
            results.append(("BAND", ok, det))
            ok = parsed["act"].strip().upper() in ("ON", "1")
            results.append(("ACT", ok, f"erwartet ON/1, Geraet {parsed['act']!r}"))

        try:
            mpar_resp = scpi.query(f"MEMory:CONTents:MPAR? MEM{i}").strip()
            ok = mpar_resp in ("1", "ON")
            results.append(("MPAR?", ok, f"erwartet '1', Geraet {mpar_resp!r}"))
        except Exception as exc:
            results.append(("MPAR?", False, f"Query fehlgeschlagen: {exc!r}"))

        verdict = "OK" if all(ok for _n, ok, _d in results) else "ABWEICHUNG"
        print(f"  MEM{i} ({ch.get('label', ch['id'])}): {verdict}")
        for name, ok, det in results:
            print(f"    [{'OK' if ok else 'ABWEICHUNG':<11}] {name}: {det}")
        rows.append({"index": i, "channel": ch, "results": results, "verdict": verdict})
    return rows


def run_phase_e(scpi: ScpiClient, local_ip: str, port: int, audio_port: int | None,
                mode: str, dwell: float | None, hold: float, start: str = "chan999",
                audio_mode: int = 6) -> str | None:
    """start (Nachtrag 3, Befund Lauf 2 - siehe Moduldoku): steuert, WIE der
    Scan gestartet wird.
      conm    : bisheriges Verhalten - INIT:CONM setzt an der zuletzt aktiven
                Position fort (Lauf 2: das war MEM1, MEM0 wurde uebersprungen).
      imm     : INITiate:IMMediate (Annex C: "Start scan upwards from start
                frequency").
      chan999 : MSCan:CHANnel MEM999 vor dem Start, danach wie conm - die
                Aufwaertsfortsetzung beginnt dadurch garantiert bei MEM0.

    audio_mode (Nachtrag 4, siehe Moduldoku): SYSTem:AUDio:REMote:MODe wird
    bei audio_port != None VOR der AUD-Tag-Registrierung gesetzt - ohne das
    kamen 0 Audio-Datagramme (Ursache des Nachtrag-4-Befunds)."""
    print("=== Phase E: MSCAN starten ===")
    # DEF:-Form fuer die (einzige, primaere) MSC-Registrierung - siehe
    # Moduldoku ("Verbleibende bewusste Implementierungsentscheidung").
    scpi.send(f'TRAC:UDP:DEF:TAG:ON "{local_ip}",{port},MSC')
    scpi.send(f'TRAC:UDP:DEF:FLAG:ON "{local_ip}",{port},"VOLT:AC","FREQ:RX","SQU","OPT"')
    aud_mode_resp: str | None = None
    if audio_port is not None:
        # Nachtrag 4: Audio-Fernmodus MUSS vor der AUD-Tag-Registrierung
        # gesetzt sein (Default 6 = 16 kHz/16 Bit/mono, siehe
        # waterfall.AUDIO_REMOTE_MODE) - sonst 0 Audio-Datagramme.
        scpi.send(f"SYST:AUD:REM:MOD {audio_mode}")
        try:
            aud_mode_resp = scpi.query("SYST:AUD:REM:MOD?")
        except Exception as exc:
            aud_mode_resp = f"(Fehler: {exc!r})"
        print(f"  SYST:AUD:REM:MOD {audio_mode} -> SYST:AUD:REM:MOD? {aud_mode_resp}")
        # One Port, One Tag: AUD auf einem EIGENEN Port, kein FLAG (nur
        # zaehlen, nicht dekodieren - siehe Auftrag).
        scpi.send(f'TRAC:UDP:TAG:ON "{local_ip}",{audio_port},AUD')

    count_arg = "1" if mode == "inventur" else "INF"
    dwell_arg = "INF" if dwell is None else f"{dwell}"
    scpi.send(f"MSC:COUNt {count_arg}")
    scpi.send(f"MSC:DWEL {dwell_arg}")
    scpi.send(f"MSC:HOLD:TIME {hold}")
    # Handbuch, verifiziert (Nachtrag 2): SENSe:MSCan:CONTrol, NICHT das
    # "Weiterlaufen"-Synonym der Vorfassung (Fehlercode -113 im ersten Lauf).
    scpi.send('MSC:CONT "STOP:SIGN"')
    scpi.send("OUTP:SQU:CONT MEM")
    scpi.send("MSC:DIR UP")

    if start == "chan999":
        # Befund Lauf 2: INIT:CONM setzt an der zuletzt aktiven Position fort
        # - explizit auf den letzten Platz springen, damit die Aufwaerts-
        # fortsetzung garantiert bei MEM0 beginnt.
        scpi.send("MSCan:CHANnel MEM999")

    scpi.send("FREQ:MODE MSC")
    errors = drain_errors(scpi)
    print(f"  drain_errors -> {errors if errors else '(leer)'}")

    for q in ("MSCan:CHANnel?", "MSC:COUN?", "MSC:DWEL?", "MSC:HOLD:TIME?",
              "MSC:CONT?", "OUTP:SQU:CONT?", "FREQ:MODE?"):
        try:
            resp = scpi.query(q)
        except Exception as exc:
            resp = f"(Fehler: {exc!r})"
        print(f"  {q} -> {resp}")

    if start == "imm":
        scpi.send("INITiate:IMMediate")
    else:
        scpi.send("INIT:CONM")
    print(f"  MSCAN gestartet: start={start}, mode={mode}, COUNt={count_arg}, "
          f"DWELl={dwell_arg}, HOLD={hold}, Port={port}" +
          (f", Audio-Port={audio_port}" if audio_port else ""))
    return aud_mode_resp


def run_phase_f(sock_msc: socket.socket, sock_aud, seconds: float, mode: str) -> dict:
    """Empfaengt Datagramme fuer maximal `seconds`, iteriert dabei ueber ALLE
    Items eines Datagramms (je Item eine Zeile + Aggregation - Nachtrag 3,
    Befund Lauf 2: vormals wurde nur das erste Item ausgewertet). Items mit
    freq_hz == 0 und Rohpegel >= 2000 sind Zyklusende-Marker (siehe
    _is_cycle_end_marker()), KEIN Messwert - sie tauchen NICHT in per_freq
    auf.

    Bei mode='inventur' endet die Phase VORZEITIG, sobald ein Zyklusende-
    Marker empfangen wurde (Nachtrag 3) - die vormalige 5-s-Stille-Heuristik
    (GAP_ENDS_INVENTUR_S) bleibt als RUECKFALL, falls aus irgendeinem Grund
    kein Marker ankommt."""
    print(f"=== Phase F: Empfang ({seconds:.0f} s) ===")
    read_socks = [sock_msc] + ([sock_aud] if sock_aud else [])
    t_start = time.monotonic()
    deadline = t_start + seconds
    last_msc_dgram_t = t_start
    seen_cycle = False
    marker_seen_inventur = False

    datagrams = 0
    parse_errors = 0
    per_freq: dict[int, dict] = {}
    last_seq = None
    seq_gaps = 0
    cycle_log: list[tuple[float, int | None]] = []
    opt_header_seen = False
    aud_count = 0
    aud_bytes = 0
    msc_times: list[float] = []
    aud_times: list[float] = []
    aud_sizes: list[int] = []          # parallel zu aud_times (Bericht: Bytes je Item-Fenster)
    item_log: list[tuple[float, int | None]] = []   # (t_rel, freq_hz) je NICHT-Marker-Item, chronologisch
    foreign_tags: dict[int, int] = {}       # Tag != MSCAN_TAG -> Anzahl
    multi_item_datagrams = 0                # n_items > 1
    cycle_end_markers = 0                   # siehe _is_cycle_end_marker()
    first_visited_freq: int | None = None   # erstes NICHT-Marker-Item (Auftrag Punkt 1)

    while True:
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            print("  Ende: --seconds erreicht")
            break
        if mode == "inventur" and seen_cycle and (now - last_msc_dgram_t) >= GAP_ENDS_INVENTUR_S:
            print(f"  Ende: {GAP_ENDS_INVENTUR_S:.0f} s ohne MScan-Datagramm nach "
                  "mindestens einem Zyklus (Rueckfall - kein Zyklusende-Marker gesehen)")
            break
        ready, _, _ = select.select(read_socks, [], [], min(remaining, 0.5))
        for sock in ready:
            try:
                raw, _addr = sock.recvfrom(65536)
            except OSError:
                continue
            t_rel = now - t_start
            if sock_aud is not None and sock is sock_aud:
                aud_count += 1
                aud_bytes += len(raw)
                aud_times.append(t_rel)
                aud_sizes.append(len(raw))
                continue

            datagrams += 1
            try:
                attrs = parse_datagram(raw)
                seq = datagram_seq(raw)
            except ValueError:
                parse_errors += 1
                continue
            if last_seq is not None:
                delta = (seq - last_seq) % 65536
                if delta > 1:
                    seq_gaps += delta - 1
            last_seq = seq

            for tag, pl in attrs:
                if tag != MSCAN_TAG:
                    foreign_tags[tag] = foreign_tags.get(tag, 0) + 1
                    continue
                try:
                    r = parse_mscan(pl)
                except ValueError:
                    parse_errors += 1
                    continue
                if len(r.freqs_hz) > 1 or len(r.levels_dbuv) > 1:
                    multi_item_datagrams += 1
                last_msc_dgram_t = now
                msc_times.append(t_rel)
                if r.has_optional_header:
                    opt_header_seen = True
                    cycle_log.append((t_rel, r.cycle_count))
                    if r.cycle_count is not None and r.cycle_count >= 1:
                        seen_cycle = True

                # Alle Items dieses Datagramms durchgehen (Nachtrag 3, Punkt 3) -
                # levels_dbuv/freqs_hz sind parallele Listen (siehe parse_mscan);
                # fehlt eine der beiden ganz, wird trotzdem mindestens 1 Zeile
                # gedruckt (z. B. ein Datagramm ohne LEVEL/FREQUENCY-Flag).
                n_line_items = max(len(r.levels_dbuv), len(r.freqs_hz), 1)
                for item_idx in range(n_line_items):
                    level = r.levels_dbuv[item_idx] if item_idx < len(r.levels_dbuv) else None
                    freq = r.freqs_hz[item_idx] if item_idx < len(r.freqs_hz) else None

                    if _is_cycle_end_marker(freq, level):
                        cycle_end_markers += 1
                        print(f"  t+{t_rel:07.3f}  seq={seq:5d}  ZYKLUSENDE (cycle={r.cycle_count})")
                        if mode == "inventur":
                            marker_seen_inventur = True
                        continue

                    if first_visited_freq is None and freq is not None:
                        first_visited_freq = freq
                    item_log.append((t_rel, freq))

                    freq_txt = "?" if freq is None else f"{freq / 1e6:.4f} MHz"
                    level_txt = "?" if level is None else f"{level:.1f} dBuV"
                    line = f"  t+{t_rel:07.3f}  seq={seq:5d}  freq={freq_txt:>12}  level={level_txt:>10}  "
                    line += (f"cycle={r.cycle_count} hold={r.hold_time} dwell={r.dwell_time} "
                            f"dir={r.scan_direction} stop={r.stop_condition}"
                            if r.has_optional_header else "kein OptHeader")
                    print(line)

                    if freq is not None:
                        agg = per_freq.setdefault(freq, {"n": 0, "min": None, "max": None,
                                                          "first": t_rel, "last": t_rel})
                        agg["n"] += 1
                        agg["last"] = t_rel
                        if level is not None:
                            agg["min"] = level if agg["min"] is None else min(agg["min"], level)
                            agg["max"] = level if agg["max"] is None else max(agg["max"], level)

        if marker_seen_inventur:
            print("  Ende: Zyklusende-Marker empfangen (inventur)")
            break

    elapsed_s = time.monotonic() - t_start
    return {
        "datagrams": datagrams, "parse_errors": parse_errors, "per_freq": per_freq,
        "seq_gaps": seq_gaps, "opt_header_seen": opt_header_seen, "cycle_log": cycle_log,
        "aud_count": aud_count, "aud_bytes": aud_bytes,
        "msc_times": msc_times, "aud_times": aud_times, "aud_sizes": aud_sizes,
        "item_log": item_log, "elapsed_s": elapsed_s,
        "foreign_tags": foreign_tags, "multi_item_datagrams": multi_item_datagrams,
        "cycle_end_markers": cycle_end_markers, "first_visited_freq": first_visited_freq,
    }


def run_phase_g(scpi: ScpiClient | None, local_ip: str, port: int, audio_port: int | None) -> None:
    """Rueckbau, wird per finally IMMER ausgefuehrt (Muster teardown() in
    validate_tag_streams.py) - jeder Einzelschritt gegen Fehler abgesichert,
    damit ein fehlgeschlagener Schritt die uebrigen nicht verhindert."""
    print("=== Phase G: Teardown ===")
    if scpi is None:
        print("  keine SCPI-Verbindung aufgebaut - nichts zurueckzusetzen")
        return
    for cmd in ("ABORt", "FREQ:MODE CW", "OUTP:SQU:CONT NONE"):
        try:
            scpi.send(cmd)
        except Exception as exc:
            print(f"  [teardown] {cmd} fehlgeschlagen: {exc!r}", file=sys.stderr)
    try:
        scpi.send('MSC:CONT:OFF "STOP:SIGN"')
    except Exception as exc:
        print(f"  [teardown] MSC:CONT:OFF fehlgeschlagen: {exc!r}", file=sys.stderr)
    try:
        scpi.send(f'TRAC:UDP:DEF:TAG:OFF "{local_ip}",{port},MSC')
        scpi.send(f'TRAC:UDP:DEF:FLAG:OFF "{local_ip}",{port},"VOLT:AC","FREQ:RX","SQU","OPT"')
    except Exception as exc:
        print(f"  [teardown] TAG/FLAG:OFF MSC fehlgeschlagen: {exc!r}", file=sys.stderr)
    if audio_port is not None:
        try:
            scpi.send(f'TRAC:UDP:TAG:OFF "{local_ip}",{audio_port},AUD')
        except Exception as exc:
            print(f"  [teardown] TAG:OFF AUD fehlgeschlagen: {exc!r}", file=sys.stderr)
        try:
            # Nachtrag 4: Audio-Fernmodus wieder aus - Gegenstueck zu
            # SYST:AUD:REM:MOD <n> aus run_phase_e().
            scpi.send("SYST:AUD:REM:MOD 0")
        except Exception as exc:
            print(f"  [teardown] SYST:AUD:REM:MOD 0 fehlgeschlagen: {exc!r}", file=sys.stderr)
    try:
        scpi.send(f'TRAC:UDP:DELete "{local_ip}",{port}')
        if audio_port is not None:
            scpi.send(f'TRAC:UDP:DELete "{local_ip}",{audio_port}')
    except Exception as exc:
        print(f"  [teardown] DELete fehlgeschlagen: {exc!r}", file=sys.stderr)
    try:
        errors = drain_errors(scpi)
        print(f"  drain_errors -> {errors if errors else '(leer)'}")
    except Exception as exc:
        print(f"  [teardown] drain_errors fehlgeschlagen: {exc!r}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------

# Nachtrag 5: Ersatz fuer die vormalige Pausen-Heuristik (siehe Moduldoku) -
# Audio-Datagramme mit Abstand <= AUDIO_SEGMENT_GAP_S zum Vorgaenger bilden
# ein Segment; einem Segment wird das letzte MScan-Item mit
# t <= Segmentstart + AUDIO_ITEM_ASSIGN_SLACK_S zugeordnet (Auftrag).
AUDIO_SEGMENT_GAP_S = 0.5
AUDIO_ITEM_ASSIGN_SLACK_S = 0.2


def _segment_audio(aud_times: list[float], aud_sizes: list[int]) -> list[dict]:
    """Fasst Audio-Datagramme (parallele Listen aud_times/aud_sizes, siehe
    run_phase_f()) zu Segmenten zusammen: ein Datagramm gehoert zum laufenden
    Segment, wenn sein Abstand zum bisher letzten Datagramm des Segments
    <= AUDIO_SEGMENT_GAP_S ist, sonst beginnt ein neues Segment."""
    segments: list[dict] = []
    for t, size in zip(aud_times, aud_sizes):
        if segments and t - segments[-1]["end"] <= AUDIO_SEGMENT_GAP_S:
            seg = segments[-1]
            seg["end"] = t
            seg["n"] += 1
            seg["bytes"] += size
        else:
            segments.append({"start": t, "end": t, "n": 1, "bytes": size})
    return segments


def _assign_segment_item(seg_start: float, item_log: list[tuple[float, int | None]]) -> int | None:
    """Liefert die Frequenz des letzten MScan-Items mit
    t <= seg_start + AUDIO_ITEM_ASSIGN_SLACK_S (item_log ist chronologisch,
    siehe run_phase_f()), sonst None ('-' im Bericht)."""
    limit = seg_start + AUDIO_ITEM_ASSIGN_SLACK_S
    freq: int | None = None
    for t_item, f in item_log:
        if t_item > limit:
            break
        freq = f
    return freq


def print_final_report(channels: list[dict], phase_c_result, phase_d_rows,
                       phase_f_result, args: argparse.Namespace,
                       audio_mode_resp: str | None = None) -> None:
    print()
    print("=" * 70)
    print("ERGEBNISBERICHT")
    print("=" * 70)

    if phase_c_result is None:
        print("Kein --clear-all angegeben - Phasen C-F wurden nicht ausgefuehrt.")
        return

    print()
    print(f"Programmieren: {phase_c_result['n']} Kanaele, "
          f"{len(phase_c_result['errors'])} mit Fehler, "
          f"Mittel {phase_c_result['mean_s'] * 1000:.1f} ms/Kanal, "
          f"Hochrechnung 1000 Kanaele: {phase_c_result['mean_s'] * 1000:.1f} s")
    for i, errs in phase_c_result["errors"].items():
        print(f"  MEM{i}: {errs}")

    print()
    all_readback_ok = all(row["verdict"] == "OK" for row in phase_d_rows)
    print(f"Readback: alle OK: {'ja' if all_readback_ok else 'nein'}")
    for row in phase_d_rows:
        if row["verdict"] != "OK":
            label = row["channel"].get("label", row["channel"]["id"])
            print(f"  MEM{row['index']} ({label}): ABWEICHUNG")
            for name, ok, det in row["results"]:
                if not ok:
                    print(f"    {name}: {det}")

    print()
    programmed_freqs = {int(ch["freq_hz"]) for ch in channels}
    seen_freqs = set(phase_f_result["per_freq"])
    fremd = seen_freqs - programmed_freqs
    fehlend = programmed_freqs - seen_freqs
    print(f"Datagramme: {phase_f_result['datagrams']} "
          f"({phase_f_result['parse_errors']} Parse-Fehler), "
          f"Luecken: {phase_f_result['seq_gaps']}, "
          f"{len(seen_freqs)} verschiedene Frequenzen gesehen")
    if phase_f_result["foreign_tags"]:
        fremd_tags = ", ".join(f"{tag}={n}" for tag, n in sorted(phase_f_result["foreign_tags"].items()))
        print(f"  Fremde Tags auf dem MSC-Port (!= {MSCAN_TAG}): {fremd_tags}")
    if phase_f_result["multi_item_datagrams"]:
        print(f"  Mehr-Item-Datagramme (n_items > 1): {phase_f_result['multi_item_datagrams']} "
              "(alle Items ausgewertet, siehe Zeilen/Aggregation oben)")
    if phase_f_result["first_visited_freq"] is not None:
        print(f"  Erster besuchter Platz: {phase_f_result['first_visited_freq'] / 1e6:.4f} MHz")
    for freq in sorted(phase_f_result["per_freq"]):
        agg = phase_f_result["per_freq"][freq]
        min_s = "?" if agg["min"] is None else f"{agg['min']:.1f}"
        max_s = "?" if agg["max"] is None else f"{agg['max']:.1f}"
        print(f"  {freq / 1e6:.4f} MHz: n={agg['n']}, Pegel {min_s}..{max_s} dBuV, "
              f"gesehen t+{agg['first']:.1f}s..t+{agg['last']:.1f}s")
    print(f"Verdict 'alle gesehenen Frequenzen aus den programmierten Plaetzen': "
          f"{'ja' if not fremd else 'nein'}")
    if fremd:
        print(f"  Fremde Frequenzen (moegliche fremde aktive Speicherplaetze): "
              f"{sorted(f / 1e6 for f in fremd)}")
    if args.mode == "inventur":
        print(f"Verdict 'alle programmierten Plaetze besucht': {'ja' if not fehlend else 'nein'}")
        if fehlend:
            print(f"  Fehlende Plaetze: {sorted(f / 1e6 for f in fehlend)}")
    else:
        besucht = len(programmed_freqs) - len(fehlend)
        print(f"Plaetze mit Aktivitaet: {besucht} von {len(programmed_freqs)}")
        if fehlend:
            print("  Stille Plaetze (kein Datagramm bedeutet keine Squelch-Oeffnung, "
                  f"NICHT 'nicht besucht'): {sorted(f / 1e6 for f in fehlend)}")

    print()
    print(f"OptionalHeader vorhanden: {'ja' if phase_f_result['opt_header_seen'] else 'nein'}")
    print(f"Zyklusende-Marker (freq=0, Rohpegel>=2000): {phase_f_result['cycle_end_markers']}"
          + (" Zyklen" if args.mode == "ueberwachung" else ""))
    if args.mode == "inventur":
        if phase_f_result["cycle_log"]:
            zyklen = sorted({c for _t, c in phase_f_result["cycle_log"] if c is not None})
            ende_t = phase_f_result["cycle_log"][-1][0]
            print(f"  cycleCount-Verlauf (gesehene Werte): {zyklen}")
            print(f"  Zeitpunkt des letzten Datagramms mit OptionalHeader: t+{ende_t:.1f}s")
        else:
            print("  keine Header-Daten fuer cycleCount verfuegbar (kein OptionalHeader gesehen)")

    zyklen = phase_f_result["cycle_end_markers"]
    laufzeit_s = phase_f_result["elapsed_s"]
    n_kanaele = len(channels)
    if zyklen > 0 and laufzeit_s > 0 and n_kanaele > 0:
        print(f"  Zyklen/s: {zyklen / laufzeit_s:.4f}  mittlere Zeit je Kanalbesuch: "
              f"{laufzeit_s / (zyklen * n_kanaele):.3f} s (Laufzeit {laufzeit_s:.1f} s / "
              f"({zyklen} Zyklen * {n_kanaele} Kanaele))")
    else:
        print("  Zyklen/s bzw. mittlere Zeit je Kanalbesuch nicht auswertbar "
              "(kein vollstaendiger Zyklus gesehen)")

    if args.with_audio:
        print()
        print(f"Audio: {phase_f_result['aud_count']} Datagramme, "
              f"{phase_f_result['aud_bytes']} Bytes waehrend des Scans")
        if audio_mode_resp is not None:
            print(f"  SYST:AUD:REM:MOD? -> {audio_mode_resp}")
        aud_times = phase_f_result["aud_times"]
        aud_sizes = phase_f_result["aud_sizes"]
        if aud_sizes:
            groessen = sorted(aud_sizes)
            mitte = len(groessen) // 2
            median = (groessen[mitte] if len(groessen) % 2
                      else (groessen[mitte - 1] + groessen[mitte]) / 2)
            print(f"  Datagrammgroesse: min {groessen[0]} B, median {median:.0f} B, "
                  f"max {groessen[-1]} B")
        else:
            print("  Datagrammgroesse: nicht auswertbar (keine Audio-Datagramme)")
        if len(aud_times) >= 2:
            span_s = aud_times[-1] - aud_times[0]
            rate_bps = phase_f_result["aud_bytes"] / span_s if span_s > 0 else 0.0
            zeile = f"  Audio-Bitrate: {rate_bps:.0f} B/s ueber {span_s:.1f} s"
            if args.audio_mode == 6:
                zeile += " (erwartet Modus 6 bei offenem Squelch: 32000 B/s)"
            print(zeile)
        else:
            print("  Audio-Bitrate: nicht auswertbar (weniger als 2 Audio-Datagramme)")

        item_log = phase_f_result["item_log"]
        segments = _segment_audio(aud_times, aud_sizes)
        if segments:
            print(f"  Audio-Segmente (Abstand <= {AUDIO_SEGMENT_GAP_S:.1f} s zum Vorgaenger "
                  "bildet dasselbe Segment - ersetzt die vormalige Pausen-Heuristik, siehe "
                  "Nachtrag 5 in der Moduldoku):")
            print(f"  {'Start t+':>10}  {'Dauer s':>8}  {'Datagr.':>8}  {'Bytes':>10}  "
                  f"{'B/s':>8}  zugeordnetes MScan-Item")
            unassigned = 0
            for seg in segments:
                dauer = seg["end"] - seg["start"]
                rate = seg["bytes"] / dauer if dauer > 0 else 0.0
                freq = _assign_segment_item(seg["start"], item_log)
                if freq is None:
                    unassigned += seg["n"]
                item_txt = "-" if freq is None else f"{freq / 1e6:.4f} MHz"
                print(f"  {seg['start']:>10.2f}  {dauer:>8.2f}  {seg['n']:>8}  "
                      f"{seg['bytes']:>10}  {rate:>8.0f}  {item_txt}")
            print(f"  Audio ausserhalb von Segmenten mit zugeordnetem Item: {unassigned} Datagramme")
        else:
            print("  keine Audio-Segmente (keine Audio-Datagramme empfangen)")
        if item_log:
            print("  Audio-Byte-Volumen je MScan-Item (Fenster bis zum jeweils naechsten Item):")
            print(f"  {'Frequenz':>14}  {'Fenster s':>10}  {'Bytes':>10}  {'B/s':>8}")
            for idx, (t_item, freq) in enumerate(item_log):
                t_next = item_log[idx + 1][0] if idx + 1 < len(item_log) else args.seconds
                window_s = t_next - t_item
                vol_bytes = sum(sz for t_a, sz in zip(aud_times, aud_sizes) if t_item <= t_a < t_next)
                rate = vol_bytes / window_s if window_s > 0 else 0.0
                freq_txt = "?" if freq is None else f"{freq / 1e6:.4f} MHz"
                print(f"  {freq_txt:>14}  {window_s:>10.2f}  {vol_bytes:>10}  {rate:>8.0f}")
        else:
            print("  keine MScan-Items fuer die Audio-Fenster-Zuordnung verfuegbar")

    print()
    print("Offene Punkte/Unsicherheiten:")
    for punkt in OPEN_QUESTIONS:
        print(f"  - {punkt}")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip", default=EB200_IP_DEFAULT, help="EB200-IP (Default wie waterfall.EB200_IP)")
    parser.add_argument("--job", default=None, help="MSCAN-Job-ID (eb200_core/mscan_job.py)")
    parser.add_argument("--demo", action="store_true", help="5 feste UKW-Wien-Kanaele statt --job")
    parser.add_argument("--max-channels", type=int, default=5)
    parser.add_argument("--mode", choices=("inventur", "ueberwachung"), default="inventur")
    parser.add_argument("--start", choices=("conm", "imm", "chan999"), default="chan999",
                        help="Startverhalten Phase E - siehe run_phase_e() (Default chan999: "
                             "MSCan:CHANnel MEM999 vor dem Start, damit der Scan bei MEM0 beginnt)")
    parser.add_argument("--dwell", type=float, default=None, help="Sekunden, Default je Modus")
    parser.add_argument("--hold", type=float, default=None, help="Sekunden, Default je Modus")
    parser.add_argument("--seconds", type=float, default=60.0, help="Empfangsdauer Phase F")
    parser.add_argument("--clear-all", action="store_true",
                        help="PFLICHT fuer Phase C - MEMory:CLEar vor dem Programmieren")
    parser.add_argument("--via-rx", action="store_true",
                        help="Phase C ueber den alten RX-Umweg statt direktem MEMory:CONTents")
    parser.add_argument("--skip-inventory", action="store_true",
                        help="Bestandsaufnahme (MEMory:CONTents:MPAR? MEM0..MEM999) in Phase B ueberspringen")
    parser.add_argument("--with-audio", action="store_true",
                        help="zusaetzlich AUDio-Tag auf --audio-port (nur zaehlen)")
    parser.add_argument("--port", type=int, default=17300, help="UDP-Port fuer MSC")
    parser.add_argument("--audio-port", type=int, default=17301, help="UDP-Port fuer AUD (--with-audio)")
    parser.add_argument("--audio-mode", type=int, default=6,
                        help="SYST:AUDio:REMote:MODe vor der AUD-Tag-Registrierung (--with-audio, "
                             "Default 6 = 16 kHz/16 Bit/mono wie waterfall.AUDIO_REMOTE_MODE)")
    parser.add_argument("--selftest", action="store_true", help="nur Parser-Selbsttest, kein Geraet")
    parser.add_argument("--ignore-running", action="store_true",
                        help="Phase A trotz erkanntem waterfall.py (Port 8000 antwortet) fortsetzen")
    args = parser.parse_args()

    if args.selftest:
        run_selftest()
        return 0

    if bool(args.job) == bool(args.demo):
        print("FEHLER: genau eines von --job/--demo angeben.", file=sys.stderr)
        return 1

    try:
        channels = resolve_channels(args)
    except (KeyError, ValueError) as exc:
        print(f"FEHLER beim Aufloesen der Kanalliste: {exc}", file=sys.stderr)
        return 1
    if not channels:
        print("FEHLER: keine Kanaele zum Programmieren.", file=sys.stderr)
        return 1
    quelle = "--demo" if args.demo else f"--job {args.job}"
    print(f"{len(channels)} Kanaele aufgeloest (Quelle: {quelle})")

    dwell, hold = resolve_dwell_hold(args)
    local_ip = determine_local_ip(args.ip)
    print(f"Lokale IP: {local_ip}  Ziel: {args.ip}:{SCPI_PORT}  Modus: {args.mode}  "
          f"Start: {args.start}  Dwell: {'INF' if dwell is None else dwell}  Hold: {hold}  "
          f"clear-all: {'ja' if args.clear_all else 'nein (Phasen C-F entfallen)'}")

    try:
        sock_msc = open_udp_socket(args.port)
    except RuntimeError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    sock_aud = None
    if args.with_audio:
        try:
            sock_aud = open_udp_socket(args.audio_port)
        except RuntimeError as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            sock_msc.close()
            return 1

    scpi: ScpiClient | None = None
    phase_c_result = None
    phase_d_rows = None
    phase_f_result = None
    audio_mode_resp = None
    try:
        try:
            scpi = run_phase_a(args.ip, SCPI_PORT, args.ignore_running)
        except (RuntimeError, OSError) as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 1

        run_phase_b(scpi, args.skip_inventory)
        if not args.clear_all:
            print()
            print("Ohne --clear-all endet der Lauf hier: ohne MEMory:CLEar wuerden "
                  "eventuell vorhandene, fremde aktive Speicherplaetze mitgescannt "
                  "und die Zuordnung der Datagramme zu den hier programmierten "
                  "Kanaelen verfaelschen. Mit --clear-all erneut aufrufen, um "
                  "Phasen C-F auszufuehren.")
            return 0

        phase_c_result = run_phase_c(scpi, channels, args.via_rx)
        phase_d_rows = run_phase_d(scpi, channels)
        audio_mode_resp = run_phase_e(scpi, local_ip, args.port,
                   args.audio_port if args.with_audio else None,
                   args.mode, dwell, hold, args.start, args.audio_mode)
        time.sleep(SETTLE_S)
        phase_f_result = run_phase_f(sock_msc, sock_aud, args.seconds, args.mode)
    finally:
        run_phase_g(scpi, local_ip, args.port, args.audio_port if args.with_audio else None)
        if scpi is not None:
            scpi.close()
        sock_msc.close()
        if sock_aud is not None:
            sock_aud.close()

    if phase_f_result is not None:
        print_final_report(channels, phase_c_result, phase_d_rows, phase_f_result, args,
                           audio_mode_resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
