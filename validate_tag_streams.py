#!/usr/bin/env python3
"""
validate_tag_streams.py

Eigenstaendiges Diagnose-Werkzeug zur Hardware-Validierung von WS3 Abschnitt
11.7 (Long-Run-Jobs): koennen auf dem EB200 gleichzeitig ZWEI IFPAN-UdpPaths
(Port A + Port B) bedient werden, und bleibt ein zusaetzlich auf Port A
aktivierter AUDIO-Tag additiv zum IFP-Tag (statt ihn zu verdraengen)?

Drei Messphasen (siehe run_phase_a/b/c):
  A - Basislinie:  IFP nur auf Port A
  B - zwei Pfade:  IFP zusaetzlich auf Port B  -> IFPAN-Doppelverteilung?
  C - Audio dazu:  AUD zusaetzlich auf Port A (kein SWAP) -> bleibt IFP auf
                   A/B unbeeintraechtigt, laeuft AUD additiv auf demselben
                   Pfad wie IFP?

Bewusst OHNE jeden Import aus waterfall.py: dieses Skript muss laufen
koennen, waehrend waterfall.py GESTOPPT ist (sonst konkurrieren beide um
dieselben UdpPaths/Ports auf dem Geraet), und darf keine Seiteneffekte aus
dessen Modul-Level-Code (HTTP-Server-Setup, globale Zustaende) mitziehen.
Die Datagramm-Parser-Logik (EB200-Common-Header, GenericAttributes, Annex F)
ist deshalb hier in minimaler Form eigenstaendig nachgebaut - inhaltlich
identisch zu waterfall.py (gleiche Header-Struct, gleiche Tag-Nummern),
aber ein vollstaendig unabhaengiges Modul.

Nur Python-Standardbibliothek.

Wichtige, NICHT abschliessend geklaerte Annahme (Annex F dokumentiert es
nicht eindeutig, vgl. aehnliche Disclaimer in waterfall.py): ob die
sequence_number im EB200-Header GEMEINSAM je UdpPath zaehlt (ueber alle dort
gleichzeitig aktiven Tags hinweg) oder JE TAG getrennt, ist unklar. Bei nur
einem aktiven Tag je Port ist die Luecken-/Verlust-Zaehlung (PortStats.lost)
verlaesslich; bei mehreren gleichzeitig aktiven Tags auf demselben Port
(Phase C, Port A: IFP+AUD) ist sie es NICHT zwangslaeufig - der Bericht weist
das explizit aus. Die beiden Kern-Verdicts der Hardware-Validierung haengen
NICHT von dieser Zaehlung ab, nur von den unzweideutigen Datagramm-/
Tag-Zaehlern je Phase.

Aufruf:
  python validate_tag_streams.py                 # echter Lauf gegen das Geraet
  python validate_tag_streams.py --parse-selftest # nur Parser pruefen, kein Geraet noetig

Voraussetzung fuer den echten Lauf: EB200 unter EB200_IP erreichbar,
waterfall.py NICHT gleichzeitig aktiv (belegt sonst eigene UdpPaths/Ports
auf demselben Rechner und wuerde die Messung verfaelschen bzw. den bind()
auf PORT_A/PORT_B verhindern).
"""

from __future__ import annotations

import argparse
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Kopf-Konstanten (vom User anpassbar)
# ---------------------------------------------------------------------------

EB200_IP = "10.0.0.55"     # Ziel-Geraet
SCPI_PORT = 5555           # SCPI-TCP-Steuerport (Annex F)
LOCAL_IP = None            # None = automatisch ermitteln (Muster waterfall.py)

PORT_A = 18110             # "Display-Pfad"-Aequivalent (IFP, spaeter +AUD)
PORT_B = 18111             # "Aufnahme-Pfad"-Aequivalent (nur IFP)

FREQ_HZ = 89_900_000       # starker Ortssender einstellen (FM-Rundfunk),
                            # damit IFPAN sicher Daten liefert - anpassen!
DEMOD = "FM"
BW_HZ = 150_000

AUDIO_REMOTE_MODE = 6      # wie waterfall.py: 16 kHz / 16 Bit / mono
PHASE_S = 20.0              # Messdauer je Phase
SO_RCVBUF_BYTES = 1_048_576

# ---------------------------------------------------------------------------
# Protokollkonstanten (Annex F) - eigenstaendiger Nachbau nach dem Vorbild in
# waterfall.py (parse_datagram/datagram_seq/MAGIC_NUMBER/TAG_*). Bewusst KEIN
# Import von dort - siehe Moduldoku.
# ---------------------------------------------------------------------------

MAGIC_NUMBER = 0x000EB200
TAG_IFPAN = 501
TAG_AUDIO = 401
TAG_NAMES = {TAG_IFPAN: "IFP", TAG_AUDIO: "AUD"}

FMT_EB200_HEADER = ">IHHH6s"          # magic, minor, major, sequence_number, reserved
LEN_EB200_HEADER = struct.calcsize(FMT_EB200_HEADER)

FMT_GENERIC_ATTR_HDR = ">HH"          # tag, length
LEN_GENERIC_ATTR_HDR = struct.calcsize(FMT_GENERIC_ATTR_HDR)


def parse_datagram(raw: bytes) -> list[tuple[int, int]]:
    """Zerlegt ein EB200-UDP-Datagramm in seine GenericAttributes.

    Liefert je Attribut nur (tag, payload_laenge) - die Payload selbst wird
    NICHT dekodiert, da fuer die reine Stream-Diagnose (Zaehlen von
    Datagrammen/Tags/Bytes, siehe measure()) nicht gebraucht wird. Wirft
    ValueError bei zu kurzem Datagramm oder falscher Magic Number.
    """
    if len(raw) < LEN_EB200_HEADER:
        raise ValueError("Datagramm zu kurz fuer EB200-Header")
    magic = struct.unpack_from(">I", raw, 0)[0]
    if magic != MAGIC_NUMBER:
        raise ValueError(f"unerwartete Magic Number {magic:#x}")
    attrs: list[tuple[int, int]] = []
    offset = LEN_EB200_HEADER
    while offset + LEN_GENERIC_ATTR_HDR <= len(raw):
        tag, length = struct.unpack_from(FMT_GENERIC_ATTR_HDR, raw, offset)
        offset += LEN_GENERIC_ATTR_HDR
        attrs.append((tag, length))
        offset += length
    return attrs


def datagram_seq(raw: bytes) -> int:
    """sequence_number aus dem EB200-Header (Offset 8, uint16 Big-Endian) -
    zaehlt PRO UdpPath fortlaufend hoch und laeuft bei 65536 ueber (siehe
    waterfall.py.datagram_seq); die Luecken-Auswertung in PortStats.lost()
    rechnet entsprechend modulo 65536."""
    if len(raw) < LEN_EB200_HEADER:
        raise ValueError("Datagramm zu kurz fuer sequence_number")
    return struct.unpack_from(">H", raw, 8)[0]


# ---------------------------------------------------------------------------
# Minimaler SCPI-Client (Muster: waterfall.py ScpiClient)
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
    tatsaechlich Daten zu senden (Muster: waterfall.py determine_local_ip)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer_ip, 1))
        return probe.getsockname()[0]
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# UDP-Empfangssockets
# ---------------------------------------------------------------------------

def open_udp_socket(port: int) -> socket.socket:
    """Oeffnet + bindet einen UDP-Empfangssocket auf dem gegebenen Port.

    Dient zugleich als Bindbarkeits-Check (Schritt 0, Punkt 6): schlaegt
    bind() fehl (typischerweise "Adresse bereits verwendet"), ist der Port
    wahrscheinlich schon belegt - meist durch ein noch laufendes
    waterfall.py (das auf demselben Rechner eigene UdpPaths/Ports nutzt)
    oder einen anderen Prozess. Das Skript bricht dann VOR jedem SCPI-
    Zugriff mit einer klaren Meldung ab, statt den Geraete-Zustand
    (SCPI-Tag-Registrierungen) nur unvollstaendig zu veraendern.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_BYTES)
    except OSError:
        pass  # nicht auf jeder Plattform einstellbar - unkritisch fuer die Messung
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        sock.close()
        raise RuntimeError(
            f"Port {port} ist nicht bindbar ({exc}). Laeuft waterfall.py noch "
            "(oder ein anderer Prozess), der Netzwerk-Ports auf diesem Rechner "
            "belegt? Dieses Skript braucht PORT_A/PORT_B exklusiv und muss "
            "laufen, waehrend waterfall.py GESTOPPT ist."
        ) from exc
    sock.settimeout(0.5)
    return sock


# ---------------------------------------------------------------------------
# Mess-Kern
# ---------------------------------------------------------------------------

@dataclass
class PortStats:
    """Messergebnis eines Ports ueber eine Phase (siehe measure())."""
    datagrams: int = 0
    tag_count: dict[int, int] = field(default_factory=dict)   # tag -> Anzahl
    tag_bytes: dict[int, int] = field(default_factory=dict)   # tag -> Bytes (Attribut-Kopf + Payload)
    tag_seqs: dict[int, list[int]] = field(default_factory=dict)  # tag -> seq-Folge in Empfangsreihenfolge
    parse_errors: int = 0

    def lost(self, tag: int) -> int:
        """Anzahl verlorener Datagramme DIESES Tags aus Luecken in dessen
        eigener sequence_number-Folge (modulo 65536, siehe datagram_seq()).
        Ein Sprung von genau 1 ist normal; Sprung > 1 zaehlt (Sprung - 1)
        Datagramme als verloren; Sprung 0 (Duplikat) zaehlt nicht als
        Verlust.

        WICHTIGE, NICHT ABSCHLIESSEND GEKLAERTE ANNAHME (Annex F dokumentiert
        das nicht eindeutig - vgl. die aehnlichen Disclaimer in waterfall.py):
        ist unklar, ob sequence_number GEMEINSAM je UdpPath zaehlt (ueber
        alle dort gleichzeitig aktiven Tags hinweg) oder JE TAG getrennt.
        Bei nur einem aktiven Tag auf dem Port (Phase A, Phase B, Port B in
        Phase C) macht das keinen Unterschied - lost() ist dort verlaesslich.
        Bei MEHREREN gleichzeitig aktiven Tags auf demselben Port (Port A in
        Phase C: IFP+AUD) liefert die getrennte Zaehlung je nach tatsaechlichem
        Zaehler-Scope entweder das korrekte Ergebnis (Scope = je Tag) oder ein
        durch Interleaving verzerrtes Artefakt (Scope = gemeinsam je Pfad) -
        SIEHE die Warnung, die format_report() in diesem Fall mit ausgibt.
        Die beiden Kern-Verdicts (siehe verdict_double_distribution/
        verdict_audio_additive) haengen NICHT von lost() ab, nur von den
        unzweideutigen Datagramm-/Tag-Zaehlern."""
        seqs = self.tag_seqs.get(tag, [])
        if len(seqs) < 2:
            return 0
        total = 0
        for prev, cur in zip(seqs, seqs[1:]):
            delta = (cur - prev) % 65536
            if delta > 1:
                total += delta - 1
        return total

    def lost_total(self) -> int:
        """Summe des Verlusts ueber alle auf diesem Port gesehenen Tags -
        bei mehreren gleichzeitig aktiven Tags mit derselben Unsicherheit
        behaftet wie lost() (siehe dortiger Docstring)."""
        return sum(self.lost(tag) for tag in self.tag_seqs)


def measure(socks_by_port: dict[int, socket.socket], duration_s: float) -> dict[int, PortStats]:
    """Liest fuer duration_s Sekunden von allen Sockets in socks_by_port und
    sammelt je Port eine PortStats-Instanz.

    Nutzt select.select() ueber ALLE Sockets gemeinsam (aus der Stdlib),
    statt die Ports nacheinander mit kurzem Timeout abzufragen - so
    verhungert bei ungleicher Datenrate kein Port hinter dem anderen.
    """
    stats: dict[int, PortStats] = {port: PortStats() for port in socks_by_port}
    sock_to_port = {sock: port for port, sock in socks_by_port.items()}
    read_socks = list(socks_by_port.values())
    deadline = time.monotonic() + duration_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select(read_socks, [], [], min(remaining, 0.5))
        for sock in ready:
            try:
                raw, _addr = sock.recvfrom(65536)
            except OSError:
                continue
            port = sock_to_port[sock]
            st = stats[port]
            st.datagrams += 1
            try:
                attrs = parse_datagram(raw)
                seq = datagram_seq(raw)
            except ValueError:
                st.parse_errors += 1
                continue
            for tag, length in attrs:
                st.tag_count[tag] = st.tag_count.get(tag, 0) + 1
                # Attribut-Kopf (4 B) + Payload als Naeherung fuer die vom
                # jeweiligen Tag verursachte Netzwerklast; der gemeinsame
                # 16-B-Common-Header wird bewusst nicht auf Tags verteilt.
                st.tag_bytes[tag] = st.tag_bytes.get(tag, 0) + LEN_GENERIC_ATTR_HDR + length
                st.tag_seqs.setdefault(tag, []).append(seq)
    return stats


# ---------------------------------------------------------------------------
# SCPI-Ablauf: Setup / TAG-Schaltung / Teardown (Muster: waterfall.py
# DeviceController._configure_ifpan / _start_recording)
# ---------------------------------------------------------------------------

def setup_device(scpi: ScpiClient, local_ip: str) -> None:
    """Einmaliges Setup vor Phase A: Empfaenger auf FREQ_HZ/DEMOD/BW_HZ
    einstellen, Audio-Fernbedienung aus, beide Ports defensiv von Resten
    frueherer Laeufe befreien, IFPAN-Feed global freigeben."""
    # FREQ:MODE CW verlaesst einen ggf. noch aktiven D-SCAN-Sweep-Modus -
    # ohne diesen Schritt misst der Empfaenger u. U. gar nicht auf FREQ_HZ
    # (siehe waterfall.py._configure_ifpan, dort derselbe Schritt vor FREQ).
    scpi.send("FREQ:MODE CW")
    scpi.send(f"FREQ {FREQ_HZ}")
    scpi.send(f"DEM {DEMOD}")
    scpi.send(f"BAND {BW_HZ}")
    scpi.send("SYST:AUD:REM:MOD 0")
    scpi.send(f'TRAC:UDP:DELete "{local_ip}",{PORT_A}')
    scpi.send(f'TRAC:UDP:DELete "{local_ip}",{PORT_B}')
    scpi.send("TRAC:FEED:CONT IFPAN,ALW")


def tag_on_ifp_primary(scpi: ScpiClient, local_ip: str, port: int) -> None:
    """Registriert den IFP-Tag als DEFINIERENDE (erste) Registrierung auf
    port, inkl. LEVEL-Flags - identisch zum Muster in
    waterfall.py._configure_ifpan (dort self.udp_port, der einzige/primaere
    Pfad).

    WICHTIG (per echtem Geraetelauf verifiziert, siehe Moduldoku oben):
    TRAC:UDP:DEF:TAG:ON darf NUR fuer die ERSTE, definierende Registrierung
    verwendet werden - ein zweiter DEF:TAG:ON-Aufruf auf einem ANDEREN Port
    verdraengt/verschiebt die Zuteilung, statt sie zu ergaenzen. Ein
    zweiter, gleichzeitig bestehender Pfad muss stattdessen ueber
    tag_on_ifp_secondary() (plaine Form, ohne DEF:) hinzugefuegt werden -
    exakt das Muster, das waterfall.py fuer UDP_PORT_REC (die parallele
    Bin-Aufnahme neben dem Display-Pfad) verwendet."""
    scpi.send(f'TRAC:UDP:DEF:TAG:ON "{local_ip}",{port},IFP')
    scpi.send(f'TRAC:UDP:DEF:FLAG:ON "{local_ip}",{port},"VOLT:AC","OPT"')


def tag_on_ifp_secondary(scpi: ScpiClient, local_ip: str, port: int) -> None:
    """Fuegt den IFP-Tag als ZUSAETZLICHEN, coexistierenden Pfad hinzu -
    plaine (nicht DEF:-praefigierte) Form, identisch zu
    waterfall.py._start_spectrum_recording (dort UDP_PORT_REC neben dem
    bereits laufenden Display-Pfad). Nur diese Form laesst die zuerst per
    tag_on_ifp_primary() registrierte Zuteilung unberuehrt (siehe dortiger
    Docstring und Moduldoku)."""
    scpi.send(f'TRAC:UDP:TAG:ON "{local_ip}",{port},IFP')
    scpi.send(f'TRAC:UDP:FLAG:ON "{local_ip}",{port},"VOLT:AC","OPT"')


def tag_on_aud(scpi: ScpiClient, local_ip: str, port: int) -> None:
    """Registriert den AUD-Tag zusaetzlich auf port - KEIN SWAP-Flag, KEIN
    eigenes FLAG:ON: identisch zu waterfall.py._start_recording, wo der
    Audio-Tag additiv auf denselben, bereits IFP-getaggten (primaeren!)
    Port gelegt wird (ein SWAP wuerde die IFPAN-Leveldaten auf diesem Pfad
    verdraengen). Die DEF:-Form ist hier korrekt, weil port die primaere,
    zuvor per tag_on_ifp_primary() definierte Registrierung ist - dieselbe
    Konstellation wie in waterfall.py (nur EIN Pfad, self.udp_port)."""
    scpi.send(f"SYST:AUD:REM:MOD {AUDIO_REMOTE_MODE}")
    scpi.send(f'TRAC:UDP:DEF:TAG:ON "{local_ip}",{port},AUD')


def teardown(scpi: ScpiClient, local_ip: str) -> None:
    """Rueckbau, wird per finally IMMER ausgefuehrt: TAG:OFF (IFP und AUD,
    defensiv - ein nie gesetzter Tag abzuschalten ist ungefaehrlich) und
    DELete fuer beide Ports, danach SYST:AUD:REM:MOD 0. Jeder Einzelschritt
    ist gegen Fehler abgesichert, damit ein fehlgeschlagener Schritt die
    uebrigen nicht verhindert.

    TAG:OFF spiegelt dieselbe DEF:/plain-Unterscheidung wie beim Einschalten:
    Port A (primaer) ueber DEF:TAG:OFF (Muster: waterfall.py._stop_recording
    fuer AUD auf self.udp_port), Port B (sekundaer) ueber die plaine Form
    (Muster: waterfall.py._stop_spectrum_recording fuer UDP_PORT_REC)."""
    for tag in ("IFP", "AUD"):
        try:
            scpi.send(f'TRAC:UDP:DEF:TAG:OFF "{local_ip}",{PORT_A},{tag}')
        except Exception as exc:
            print(f"[teardown] DEF:TAG:OFF {tag}@{PORT_A} fehlgeschlagen: {exc!r}", file=sys.stderr)
    try:
        scpi.send(f'TRAC:UDP:TAG:OFF "{local_ip}",{PORT_B},IFP')
    except Exception as exc:
        print(f"[teardown] TAG:OFF IFP@{PORT_B} fehlgeschlagen: {exc!r}", file=sys.stderr)
    for port in (PORT_A, PORT_B):
        try:
            scpi.send(f'TRAC:UDP:DELete "{local_ip}",{port}')
        except Exception as exc:
            print(f"[teardown] DELete {port} fehlgeschlagen: {exc!r}", file=sys.stderr)
    try:
        scpi.send("SYST:AUD:REM:MOD 0")
    except Exception as exc:
        print(f"[teardown] SYST:AUD:REM:MOD 0 fehlgeschlagen: {exc!r}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Messphasen
# ---------------------------------------------------------------------------

SETTLE_S = 1.0  # kurze Anlaufzeit nach TAG:ON, bis das Geraet den Tag tatsaechlich bedient


def run_phase_a(scpi: ScpiClient, local_ip: str, sockets: dict[int, socket.socket]) -> dict[int, PortStats]:
    print(f"=== Phase A: Basislinie (IFP nur auf Port {PORT_A}) ===")
    tag_on_ifp_primary(scpi, local_ip, PORT_A)
    time.sleep(SETTLE_S)
    return measure(sockets, PHASE_S)


def run_phase_b(scpi: ScpiClient, local_ip: str, sockets: dict[int, socket.socket]) -> dict[int, PortStats]:
    print(f"=== Phase B: zwei Pfade (IFP zusaetzlich auf Port {PORT_B}) ===")
    tag_on_ifp_secondary(scpi, local_ip, PORT_B)
    time.sleep(SETTLE_S)
    return measure(sockets, PHASE_S)


def run_phase_c(scpi: ScpiClient, local_ip: str, sockets: dict[int, socket.socket]) -> dict[int, PortStats]:
    print(f"=== Phase C: additiv Audio (zusaetzlich AUD auf Port {PORT_A}) ===")
    tag_on_aud(scpi, local_ip, PORT_A)
    time.sleep(SETTLE_S)
    return measure(sockets, PHASE_S)


# ---------------------------------------------------------------------------
# Ergebnisbericht
# ---------------------------------------------------------------------------

def format_report(phase_name: str, stats: dict[int, PortStats], duration_s: float) -> str:
    lines = [f"--- {phase_name} ---",
             f"{'Port':>7}  {'Tag':<6}  {'Datagr.':>8}  {'Datagr./s':>10}  {'kB/s':>8}  {'verloren':>9}"]
    for port in sorted(stats):
        st = stats[port]
        if not st.tag_count:
            lines.append(f"{port:>7}  {'-':<6}  {0:>8}  {0.0:>10.2f}  {0.0:>8.2f}  {0:>9}")
            continue
        for tag in sorted(st.tag_count):
            name = TAG_NAMES.get(tag, str(tag))
            count = st.tag_count[tag]
            byt = st.tag_bytes.get(tag, 0)
            lines.append(
                f"{port:>7}  {name:<6}  {count:>8}  {count / duration_s:>10.2f}  "
                f"{byt / 1024.0 / duration_s:>8.2f}  {st.lost(tag):>9}"
            )
        if len(st.tag_count) > 1:
            lines.append(
                f"  Warnung: Port {port} traegt hier mehrere Tags gleichzeitig "
                f"({', '.join(TAG_NAMES.get(t, str(t)) for t in sorted(st.tag_count))}) - "
                "die 'verloren'-Spalte ist fuer diesen Port in dieser Phase NICHT "
                "zuverlaessig interpretierbar (ungeklaerter seq-Scope je Tag vs. "
                "je UdpPath, siehe PortStats.lost()). Datagramme/Datagr./s/kB/s "
                "bleiben unzweideutig."
            )
        if st.parse_errors:
            lines.append(f"  Hinweis: {st.parse_errors} unparsebare Datagramme auf Port {port}")
    return "\n".join(lines)


def verdict_double_distribution(stats_b: dict[int, PortStats]) -> str:
    st_a, st_b = stats_b[PORT_A], stats_b[PORT_B]
    rate_a = st_a.tag_count.get(TAG_IFPAN, 0) / PHASE_S
    rate_b = st_b.tag_count.get(TAG_IFPAN, 0) / PHASE_S
    # "Doppelverteilung" heisst: BEIDE Pfade liefern IFP - nicht nur einer
    # (ein reines Verschieben von A nach B waere KEINE Doppelverteilung).
    ok = rate_a > 0 and rate_b > 0
    diff_pct = abs(rate_a - rate_b) / rate_a * 100 if rate_a > 0 else float("nan")
    return (f"IFPAN-Doppelverteilung: {'JA' if ok else 'NEIN'} "
            f"(Port {PORT_A}: {rate_a:.2f} Datagr./s, Port {PORT_B}: {rate_b:.2f} Datagr./s, "
            f"Abweichung {diff_pct:.1f} %)")


def verdict_audio_additive(stats_b: dict[int, PortStats], stats_c: dict[int, PortStats]) -> str:
    st_a_b = stats_b[PORT_A]
    st_a_c = stats_c[PORT_A]
    st_b_c = stats_c[PORT_B]
    ifp_rate_b = st_a_b.tag_count.get(TAG_IFPAN, 0) / PHASE_S
    ifp_rate_c = st_a_c.tag_count.get(TAG_IFPAN, 0) / PHASE_S
    ifp_rate_b_c = st_b_c.tag_count.get(TAG_IFPAN, 0) / PHASE_S   # Port B in Phase C, zur Kontrolle
    aud_count_c = st_a_c.tag_count.get(TAG_AUDIO, 0)
    additive = ifp_rate_c > 0 and aud_count_c > 0
    drop_pct = (ifp_rate_b - ifp_rate_c) / ifp_rate_b * 100 if ifp_rate_b > 0 else float("nan")
    return (f"AUD additiv zu IFP auf demselben Pfad: {'JA' if additive else 'NEIN'} "
            f"(Port {PORT_A}: IFP {ifp_rate_c:.2f} Datagr./s, AUD {aud_count_c} Datagramme "
            f"in {PHASE_S:.0f} s, Port {PORT_B} IFP unveraendert {ifp_rate_b_c:.2f} Datagr./s); "
            f"IFPAN-Rateneinbruch auf Port {PORT_A} durch Audio: {drop_pct:.1f} %")


def gap_hint(all_stats: dict[str, dict[int, PortStats]]) -> str | None:
    details = []
    for phase_name, stats in all_stats.items():
        for port, st in stats.items():
            lost = st.lost_total()
            if lost > 0:
                details.append(f"{phase_name}/Port {port}: {lost}")
    if not details:
        return None
    return "Hinweis: Datagramm-Luecken erkannt - " + ", ".join(details)


# ---------------------------------------------------------------------------
# Parser-Selbsttest (--parse-selftest, kein Geraet noetig)
# ---------------------------------------------------------------------------

def build_synthetic_datagram(seq: int, attrs: list[tuple[int, bytes]]) -> bytes:
    """Baut ein synthetisches EB200-Datagramm (Common-Header + beliebig
    viele GenericAttributes) fuer den Parser-Selbsttest."""
    header = struct.pack(FMT_EB200_HEADER, MAGIC_NUMBER, 1, 0, seq, b"\x00" * 6)
    body = b""
    for tag, payload in attrs:
        body += struct.pack(FMT_GENERIC_ATTR_HDR, tag, len(payload)) + payload
    return header + body


def run_parse_selftest() -> None:
    print("Parser-Selbsttest ...")

    # 1) gueltiges Datagramm mit zwei GenericAttributes (IFP + AUD)
    raw = build_synthetic_datagram(42, [(TAG_IFPAN, b"\x00" * 10), (TAG_AUDIO, b"\x01" * 4)])
    attrs = parse_datagram(raw)
    assert attrs == [(TAG_IFPAN, 10), (TAG_AUDIO, 4)], f"unerwartete Attribute: {attrs}"
    assert datagram_seq(raw) == 42, f"unerwartete seq: {datagram_seq(raw)}"

    # 2) sequence_number-Grenzfall (65535, kurz vor dem Ueberlauf)
    raw2 = build_synthetic_datagram(65535, [(TAG_IFPAN, b"\x00" * 2)])
    assert datagram_seq(raw2) == 65535, f"unerwartete seq: {datagram_seq(raw2)}"

    # 3) kaputte Magic Number -> muss verworfen werden (ValueError)
    bad = bytearray(raw)
    bad[0:4] = struct.pack(">I", 0xDEADBEEF)
    try:
        parse_datagram(bytes(bad))
    except ValueError:
        pass
    else:
        raise AssertionError("kaputte Magic Number wurde nicht als Fehler erkannt")

    # 4) zu kurzes Datagramm (nur die ersten 4 Byte) -> muss verworfen werden
    try:
        parse_datagram(raw[:4])
    except ValueError:
        pass
    else:
        raise AssertionError("zu kurzes Datagramm wurde nicht als Fehler erkannt")

    # 5) Lueckenauswertung inkl. Rollover ueber 65536 hinweg
    st = PortStats()
    st.tag_seqs[TAG_IFPAN] = [65534, 65535, 0, 1, 5]  # normaler Rollover, dann eine Luecke von 3
    assert st.lost(TAG_IFPAN) == 3, f"unerwartete Verlustzahl: {st.lost(TAG_IFPAN)}"
    assert st.lost_total() == 3, f"unerwartete Gesamt-Verlustzahl: {st.lost_total()}"

    # 6) getrennte Tag-Folgen duerfen sich NICHT gegenseitig als Luecke
    # missverstehen (Regressionstest fuer den in der Moduldoku beschriebenen
    # Interleaving-Fehler bei gepoolter Zaehlung ueber mehrere Tags).
    st2 = PortStats()
    st2.tag_seqs[TAG_IFPAN] = [10, 11, 12, 13]
    st2.tag_seqs[TAG_AUDIO] = [500, 501, 502, 503]
    assert st2.lost(TAG_IFPAN) == 0 and st2.lost(TAG_AUDIO) == 0 and st2.lost_total() == 0, (
        f"unerwartete Verlustzahlen bei getrennten Tag-Folgen: "
        f"IFP={st2.lost(TAG_IFPAN)} AUD={st2.lost(TAG_AUDIO)} total={st2.lost_total()}"
    )

    print("Parser-Selbsttest OK: Magic-Erkennung, seq (inkl. Rollover-Grenzfall), "
          "Tag-Iteration, Verwurf bei kaputter Magic/zu kurzem Datagramm, "
          "Luecken-Zaehlung pro Tag (inkl. Interleaving-Regressionstest).")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--parse-selftest", action="store_true",
        help="Nur den Datagramm-Parser gegen synthetische Bytes pruefen, kein Geraetezugriff.",
    )
    args = parser.parse_args()

    if args.parse_selftest:
        run_parse_selftest()
        return 0

    local_ip = LOCAL_IP or determine_local_ip(EB200_IP)
    print(f"Lokale IP: {local_ip}  Ziel: {EB200_IP}:{SCPI_PORT}  Frequenz: {FREQ_HZ / 1e6:.4f} MHz")

    try:
        sock_a = open_udp_socket(PORT_A)
    except RuntimeError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    try:
        sock_b = open_udp_socket(PORT_B)
    except RuntimeError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        sock_a.close()
        return 1
    sockets = {PORT_A: sock_a, PORT_B: sock_b}

    try:
        scpi = ScpiClient(EB200_IP, SCPI_PORT)
    except OSError as exc:
        print(f"FEHLER: SCPI-Verbindung zu {EB200_IP}:{SCPI_PORT} fehlgeschlagen: {exc}", file=sys.stderr)
        sock_a.close()
        sock_b.close()
        return 1

    all_stats: dict[str, dict[int, PortStats]] = {}
    try:
        setup_device(scpi, local_ip)
        all_stats["Phase A (Basislinie)"] = run_phase_a(scpi, local_ip, sockets)
        all_stats["Phase B (zwei Pfade)"] = run_phase_b(scpi, local_ip, sockets)
        all_stats["Phase C (additiv Audio)"] = run_phase_c(scpi, local_ip, sockets)
    finally:
        teardown(scpi, local_ip)
        scpi.close()
        sock_a.close()
        sock_b.close()

    print()
    print("=" * 70)
    print("ERGEBNISBERICHT")
    print("=" * 70)
    for phase_name, stats in all_stats.items():
        print()
        print(format_report(phase_name, stats, PHASE_S))

    print()
    print(verdict_double_distribution(all_stats["Phase B (zwei Pfade)"]))
    print(verdict_audio_additive(all_stats["Phase B (zwei Pfade)"], all_stats["Phase C (additiv Audio)"]))
    hint = gap_hint(all_stats)
    if hint:
        print()
        print(hint)

    return 0


if __name__ == "__main__":
    sys.exit(main())
