#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/test_opt_header.py

Eigenstaendiges Diagnose-Skript (nur Python-Standardbibliothek, KEINE
Aenderung an waterfall.py o. ae.): prueft am echten EB200, welche
UdpPath-Registrierung einen OptionalHeader liefert und welche Felder darin
sichtbar sind (Annex F).

Nutzt eigene Test-UDP-Ports 18110 (Audio) / 18111 (IFPAN) / 18112 (DSCAN) -
die produktiven Pfade 18102/18103 von waterfall.py werden NICHT angefasst.
Die UdpPath-Tabelle hat nur 4 Eintraege (Index 0..3, siehe
docs/EB200_Geraeteprofil.md); jede Testphase registriert ihren Pfad und
deregistriert ihn am Phasenende wieder, damit hoechstens ein Test-Slot
gleichzeitig belegt ist.

Testphasen (Annex F / Annex C, Kommandos wie in waterfall.py verifiziert):
  TP-1  Audio ohne "OPT"        -> erwartet opt_len == 0
  TP-2  Audio MIT "OPT"         -> Kernfrage: opt_len > 0? Felder vs. SCPI
  TP-3  Aenderungsverfolgung Audio-Header (nur wenn TP-2 opt_len > 0)
  TP-4  IFPAN mit "OPT"         -> Struktur-/Feldvergleich
  TP-5  DSCAN mit "OPT"         -> bwZoom-Verhalten bei BW-ZOOM ON/OFF
  TP-6  Squelch-Sichtbarkeit    -> Schwelle nicht im Header, SelectorFlag-Bit
  TP-7  Zusatzbefunde           -> minor_version_number, Datagrammrate

Voraussetzung: waterfall.py und alle laufenden Aufnahmen sind gestoppt,
das Skript braucht das Geraet exklusiv (Backup/Restore von FREQ, Demodulation,
Bandbreite, Squelch, Audio-Remote-Mode via try/finally).

Ausgabe je Testpunkt: "TP-n <Name>: OK/FEHLER/BEFUND - <Details>", am Ende
eine Zusammenfassungstabelle.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Konfiguration (Default-IP identisch zur Konfigurationsquelle von
# waterfall.py: EB200_IP = "10.0.0.55", SCPI_PORT = 5555).
# ---------------------------------------------------------------------------

DEFAULT_EB200_IP = "10.0.0.55"
SCPI_PORT = 5555

TEST_PORT_AUDIO = 18110
TEST_PORT_IFPAN = 18111
TEST_PORT_DSCAN = 18112

RECV_TIMEOUT_S = 3.0
FRAMES_PER_PHASE = 5      # "einige Datagramme" je Phase
MAX_RAW_PACKETS = 60      # Sicherheitsdeckel je Empfangsschleife

# Minimaler D-SCAN-Bereich (empirisch, siehe docs/EB200_Geraeteprofil.md:
# unter ca. 20 kHz lehnt das Geraet mit -221 "Span too low" ab).
DSCAN_TEST_START_HZ = 100_000_000
DSCAN_TEST_STOP_HZ = 100_020_000
DSCAN_TEST_BW_HZ = 9000

# ---------------------------------------------------------------------------
# Protokollkonstanten (Annex F)
# ---------------------------------------------------------------------------

MAGIC_NUMBER = 0x000EB200
TAG_DSCAN = 301
TAG_IFPAN = 501
TAG_AUDIO = 401

FMT_EB200_HEADER = ">IHHH6s"   # magic, minor, major, sequence, reserved[6]
LEN_EB200_HEADER = struct.calcsize(FMT_EB200_HEADER)

FMT_GENERIC_ATTR_HDR = ">HH"   # tag, length
LEN_GENERIC_ATTR_HDR = struct.calcsize(FMT_GENERIC_ATTR_HDR)

FMT_TRACE_ATTR_HDR = ">hBBI"   # n_items, reserved, optional_header_length, selectorFlags
LEN_TRACE_ATTR_HDR = struct.calcsize(FMT_TRACE_ATTR_HDR)

# Audio-OptionalHeader: 14 Byte ohne / 22 Byte MIT sDemodulation (ab
# minor_version_number 0x26).
FMT_AUDIO_OPT_14 = ">hhIIH"        # audio_mode, frame_len, frequency, bandwidth, demodulation
LEN_AUDIO_OPT_14 = struct.calcsize(FMT_AUDIO_OPT_14)
FMT_AUDIO_OPT_22 = ">hhIIH8s"      # + sDemodulation[8]
LEN_AUDIO_OPT_22 = struct.calcsize(FMT_AUDIO_OPT_22)

FMT_IFPAN_OPT = ">IIhhI"           # frequency, spanFrequency, reserved, averageType, measureTime
LEN_IFPAN_OPT = struct.calcsize(FMT_IFPAN_OPT)

FMT_DSCAN_OPT = ">IIIIhhh"         # start/stop/step/markFrequency, bwZoom, referenceLevel, newStepScheme
LEN_DSCAN_OPT = struct.calcsize(FMT_DSCAN_OPT)

DEMOD_ENUM = {0: "FM", 1: "AM", 2: "PULS", 3: "CW", 4: "USB", 5: "LSB", 6: "IQ"}

FLAG_LEVEL = 0x01
FLAG_SWAP = 0x20000000
FLAG_SQUELCH = 0x40000000
FLAG_OPTIONAL_HEADER = 0x80000000

RESULTS: list[str] = []


# ---------------------------------------------------------------------------
# SCPI-Client (Minimalvariante, Muster wie waterfall.py ScpiClient)
# ---------------------------------------------------------------------------

class ScpiClient:
    def __init__(self, host: str, port: int, timeout: float = 5.0):
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
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer_ip, 1))
        return probe.getsockname()[0]
    finally:
        probe.close()


def safe_query(scpi: ScpiClient, cmd: str, cast=str, default=None):
    try:
        val = scpi.query(cmd)
        return cast(val) if cast else val
    except Exception as exc:
        print(f"[backup] Query {cmd} fehlgeschlagen: {exc!r} - Default {default!r} wird verwendet",
              file=sys.stderr)
        return default


def safe_send(scpi: ScpiClient, cmd: str) -> None:
    try:
        scpi.send(cmd)
    except Exception as exc:
        print(f"[restore] Kommando fehlgeschlagen: {cmd} ({exc!r})", file=sys.stderr)


# ---------------------------------------------------------------------------
# UdpPath-Verwaltung (Annex F)
# ---------------------------------------------------------------------------

def udp_register(scpi: ScpiClient, ip: str, port: int, tag: str, flags: Optional[list[str]] = None) -> None:
    scpi.send(f'TRAC:UDP:TAG:ON "{ip}",{port},{tag}')
    if flags:
        flag_list = ",".join(f'"{f}"' for f in flags)
        scpi.send(f'TRAC:UDP:FLAG:ON "{ip}",{port},{flag_list}')


def udp_add_flags(scpi: ScpiClient, ip: str, port: int, flags: list[str]) -> None:
    flag_list = ",".join(f'"{f}"' for f in flags)
    scpi.send(f'TRAC:UDP:FLAG:ON "{ip}",{port},{flag_list}')


def udp_deregister(scpi: ScpiClient, ip: str, port: int) -> None:
    scpi.send(f'TRAC:UDP:DELete "{ip}",{port}')


def dump_udp_table(scpi: ScpiClient, label: str) -> None:
    print(f"\n--- UdpPath-Tabelle ({label}) ---")
    try:
        idx_min = scpi.query("TRAC:UDP? MIN")
        idx_max = scpi.query("TRAC:UDP? MAX")
        idx_def = scpi.query("TRAC:UDP? DEF")
        print(f"  MIN={idx_min}  MAX={idx_max}  DEF={idx_def}")
        n_max = int(idx_max.strip())
    except Exception as exc:
        print(f"  Fehler bei TRAC:UDP? MIN/MAX/DEF: {exc!r}", file=sys.stderr)
        return
    for i in range(0, n_max + 1):
        try:
            entry = scpi.query(f"TRAC:UDP? {i}")
        except Exception as exc:
            entry = f"(Query-Fehler: {exc!r})"
        print(f"  [{i}] {entry}")


# ---------------------------------------------------------------------------
# Geraetezustand sichern/wiederherstellen
# ---------------------------------------------------------------------------

@dataclass
class DeviceState:
    freq_mode: Optional[str] = None
    freq_hz: Optional[int] = None
    dem: Optional[str] = None
    band_hz: Optional[int] = None
    squelch_on: Optional[bool] = None
    squelch_thr: Optional[float] = None
    audio_mode: Optional[int] = None


def backup_state(scpi: ScpiClient) -> DeviceState:
    state = DeviceState()
    state.freq_mode = safe_query(scpi, "FREQ:MODE?", str, "CW")
    state.freq_hz = safe_query(scpi, "FREQ?", lambda v: int(round(float(v))), None)
    state.dem = safe_query(scpi, "DEM?", lambda v: v.strip().upper(), "FM")
    state.band_hz = safe_query(scpi, "BAND?", lambda v: int(round(float(v))), None)
    state.squelch_on = safe_query(scpi, "OUTP:SQU?", lambda v: v.strip().upper() in ("1", "ON"), None)
    state.squelch_thr = safe_query(scpi, "OUTP:SQU:THR?", lambda v: float(v), None)
    state.audio_mode = safe_query(scpi, "SYST:AUD:REM:MOD?", lambda v: int(round(float(v))), 0)
    print(f"[backup] {state}")
    return state


def restore_state(scpi: ScpiClient, state: DeviceState) -> None:
    if state.freq_mode:
        safe_send(scpi, f"FREQ:MODE {state.freq_mode}")
    if state.dem:
        safe_send(scpi, f"DEM {state.dem}")
    if state.band_hz is not None:
        safe_send(scpi, f"BAND {state.band_hz}")
    if state.freq_hz is not None:
        safe_send(scpi, f"FREQ {state.freq_hz}")
    if state.squelch_thr is not None:
        safe_send(scpi, f"OUTP:SQU:THR {state.squelch_thr}")
    if state.squelch_on is not None:
        safe_send(scpi, f"OUTP:SQU {'ON' if state.squelch_on else 'OFF'}")
    safe_send(scpi, f"SYST:AUD:REM:MOD {state.audio_mode or 0}")
    print("[restore] Ursprungszustand (FREQ/DEM/BAND/Squelch/Audio-Remote-Mode) gesendet.")


@dataclass
class DscanState:
    start_hz: Optional[int] = None
    stop_hz: Optional[int] = None
    speed: Optional[str] = None
    marker_hz: Optional[int] = None
    res_auto: Optional[str] = None


def backup_dscan_state(scpi: ScpiClient) -> DscanState:
    st = DscanState()
    st.start_hz = safe_query(scpi, "FREQ:DSC:STAR?", lambda v: int(round(float(v))), None)
    st.stop_hz = safe_query(scpi, "FREQ:DSC:STOP?", lambda v: int(round(float(v))), None)
    st.speed = safe_query(scpi, "FREQ:DSC:SPE?", lambda v: v.strip().upper(), None)
    st.marker_hz = safe_query(scpi, "FREQ:DSC:MARK?", lambda v: int(round(float(v))), None)
    st.res_auto = safe_query(scpi, "FREQ:DSC:RES:AUTO?", lambda v: v.strip().upper(), None)
    return st


def restore_dscan_state(scpi: ScpiClient, st: DscanState) -> None:
    # ABOR zuerst (D-SCAN aus diesem Test sicher stoppen), dann Altwerte
    # zurueckschreiben - waterfall.py liest die D-SCAN-Startwerte beim
    # eigenen Programmstart vom Geraet (_read_dscan_config_from_device),
    # ein liegen gelassener Testbereich wuerde sonst dort als neuer
    # Default uebernommen.
    safe_send(scpi, "ABOR")
    if st.res_auto:
        safe_send(scpi, f"FREQ:DSC:RES:AUTO {st.res_auto}")
    if st.start_hz is not None:
        safe_send(scpi, f"FREQ:DSC:STAR {st.start_hz}")
    if st.stop_hz is not None:
        safe_send(scpi, f"FREQ:DSC:STOP {st.stop_hz}")
    if st.speed:
        safe_send(scpi, f"FREQ:DSC:SPE {st.speed}")
    if st.marker_hz is not None:
        safe_send(scpi, f"FREQ:DSC:MARK {st.marker_hz}")


# ---------------------------------------------------------------------------
# Datagramm-Parsing (Annex F)
# ---------------------------------------------------------------------------

def parse_datagram(raw: bytes):
    if len(raw) < LEN_EB200_HEADER:
        raise ValueError("Datagramm zu kurz")
    magic, minor, major, seq, _reserved = struct.unpack_from(FMT_EB200_HEADER, raw, 0)
    if magic != MAGIC_NUMBER:
        raise ValueError(f"unerwartete Magic Number {magic:#x}")
    header = {"minor_version": minor, "major_version": major, "sequence": seq}
    attrs = []
    offset = LEN_EB200_HEADER
    while offset + LEN_GENERIC_ATTR_HDR <= len(raw):
        tag, length = struct.unpack_from(FMT_GENERIC_ATTR_HDR, raw, offset)
        offset += LEN_GENERIC_ATTR_HDR
        attrs.append((tag, raw[offset:offset + length]))
        offset += length
    return header, attrs


def parse_trace_header(data: bytes):
    n_items, _res, opt_len, sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, data, 0)
    return n_items, opt_len, sel_flags


def describe_audio_opt(data: bytes, opt_len: int) -> dict:
    pos = LEN_TRACE_ATTR_HDR
    chunk = data[pos:pos + opt_len]
    info: dict = {}
    if opt_len >= LEN_AUDIO_OPT_14:
        audio_mode, frame_len, frequency, bandwidth, demod = struct.unpack_from(FMT_AUDIO_OPT_14, chunk, 0)
        info.update(audio_mode=audio_mode, frame_len=frame_len, frequency_hz=frequency,
                    bandwidth_hz=bandwidth, demodulation=demod,
                    demodulation_text=DEMOD_ENUM.get(demod, f"?{demod}"))
        rest = chunk[LEN_AUDIO_OPT_14:]
        if len(rest) >= 8:
            info["sDemodulation"] = rest[:8].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            extra = rest[8:]
        else:
            extra = rest
        if extra:
            info["extra_hex"] = extra.hex()
    elif opt_len > 0:
        info["raw_hex"] = chunk.hex()
        info["warning"] = f"opt_len={opt_len} kleiner als Mindestgroesse {LEN_AUDIO_OPT_14}"
    return info


def describe_ifpan_opt(data: bytes, opt_len: int) -> dict:
    pos = LEN_TRACE_ATTR_HDR
    chunk = data[pos:pos + opt_len]
    info: dict = {}
    if opt_len >= LEN_IFPAN_OPT:
        frequency, span, reserved, avg_type, meas_time = struct.unpack_from(FMT_IFPAN_OPT, chunk, 0)
        info.update(frequency_hz=frequency, span_hz=span, reserved=reserved,
                    average_type=avg_type, measure_time=meas_time)
        extra = chunk[LEN_IFPAN_OPT:]
        if extra:
            info["extra_hex"] = extra.hex()
    elif opt_len > 0:
        info["raw_hex"] = chunk.hex()
        info["warning"] = f"opt_len={opt_len} kleiner als Soll-Struktur {LEN_IFPAN_OPT}"
    return info


def describe_dscan_opt(data: bytes, opt_len: int) -> dict:
    pos = LEN_TRACE_ATTR_HDR
    chunk = data[pos:pos + opt_len]
    info: dict = {}
    if opt_len >= LEN_DSCAN_OPT:
        start, stop, step, mark, bw_zoom, ref_level, new_step = struct.unpack_from(FMT_DSCAN_OPT, chunk, 0)
        info.update(start_hz=start, stop_hz=stop, step_hz=step, mark_hz=mark,
                    bw_zoom=bw_zoom, reference_level=ref_level, new_step_scheme=new_step)
        extra = chunk[LEN_DSCAN_OPT:]
        if extra:
            info["extra_hex"] = extra.hex()
    elif opt_len > 0:
        info["raw_hex"] = chunk.hex()
        info["warning"] = f"opt_len={opt_len} kleiner als Soll-Struktur {LEN_DSCAN_OPT}"
    return info


# ---------------------------------------------------------------------------
# UDP-Empfang
# ---------------------------------------------------------------------------

def open_test_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(RECV_TIMEOUT_S)
    return sock


def drain_socket(sock: socket.socket, drain_timeout: float = 0.05) -> int:
    """Verwirft alle bereits im OS-Puffer wartenden Datagramme (kurzer
    Timeout statt RECV_TIMEOUT_S). Noetig vor einer "Nachher"-Messung nach
    einer SCPI-Aenderung: sonst stammen die zuerst gelesenen Pakete noch von
    VOR der Aenderung (in Flight/im Kernel-Puffer), was eine Aenderung
    faelschlich als "nicht sichtbar" erscheinen liesse. Stellt den
    urspruenglichen Timeout danach wieder her. Gibt die Anzahl verworfener
    Pakete zurueck."""
    old_timeout = sock.gettimeout()
    sock.settimeout(drain_timeout)
    drained = 0
    try:
        while True:
            try:
                sock.recvfrom(65535)
                drained += 1
            except socket.timeout:
                break
    finally:
        sock.settimeout(old_timeout)
    return drained


def receive_frames(sock: socket.socket, expected_tag: int, count: int = FRAMES_PER_PHASE):
    """Liest bis zu 'count' zum Tag passende Attribute, hoechstens
    MAX_RAW_PACKETS Rohpakete oder bis RECV_TIMEOUT_S ohne neues Paket
    verstreicht. Liefert (frames, stats); frames = Liste (header, opt_len,
    sel_flags, attr_data); stats = Paketzahl/Rate fuer TP-7."""
    frames = []
    total_packets = 0
    t_start = None
    t_last = None
    for _ in range(MAX_RAW_PACKETS):
        try:
            raw, _addr = sock.recvfrom(65535)
        except socket.timeout:
            break
        now = time.monotonic()
        if t_start is None:
            t_start = now
        t_last = now
        total_packets += 1
        try:
            header, attrs = parse_datagram(raw)
        except ValueError:
            continue
        for tag, data in attrs:
            if tag == expected_tag and len(data) >= LEN_TRACE_ATTR_HDR:
                n_items, opt_len, sel_flags = parse_trace_header(data)
                frames.append((header, opt_len, sel_flags, data))
        if len(frames) >= count:
            break
    rate = None
    if t_start is not None and t_last is not None and t_last > t_start:
        rate = total_packets / (t_last - t_start)
    return frames, {"total_packets": total_packets, "rate_hz": rate}


def receive_until(sock: socket.socket, expected_tag: int, predicate, describe_fn=describe_audio_opt,
                   max_attempts: int = 40):
    """Liest Frames einzeln, bis 'predicate(info)' zutrifft oder
    max_attempts/RECV_TIMEOUT_S erschoepft sind. Fuer TP-3 (Audio) und
    TP-5 (DSCAN, describe_fn=describe_dscan_opt): Latenz in Anzahl
    Datagrammen bis zur Sichtbarkeit einer Aenderung."""
    attempts = 0
    for _ in range(max_attempts):
        try:
            raw, _addr = sock.recvfrom(65535)
        except socket.timeout:
            break
        attempts += 1
        try:
            header, attrs = parse_datagram(raw)
        except ValueError:
            continue
        for tag, data in attrs:
            if tag == expected_tag and len(data) >= LEN_TRACE_ATTR_HDR:
                n_items, opt_len, sel_flags = parse_trace_header(data)
                if opt_len == 0:
                    continue
                info = describe_fn(data, opt_len)
                if predicate(info):
                    return info, attempts
    return None, attempts


def report(tp_id: str, name: str, verdict: str, detail: str) -> None:
    line = f"{tp_id} {name}: {verdict} - {detail}"
    print(line)
    RESULTS.append(line)


def fmt_hex32(v: int) -> str:
    return f"0x{v & 0xFFFFFFFF:08X}"


# ---------------------------------------------------------------------------
# Testphasen
# ---------------------------------------------------------------------------

def run_audio_block(scpi: ScpiClient, local_ip: str, args) -> dict:
    """TP-1, TP-2, TP-3. Liefert {'opt_len_positive': bool, 'minor_version': int|None}."""
    findings = {"opt_len_positive": False, "minor_version": None}
    scpi.send("FREQ:MODE CW")
    scpi.send(f"SYST:AUD:REM:MOD {args.audio_mode}")
    sock = open_test_socket(TEST_PORT_AUDIO)
    try:
        # -- TP-1: Audio ohne OPT --------------------------------------
        udp_register(scpi, local_ip, TEST_PORT_AUDIO, "AUD")
        frames, stats = receive_frames(sock, TAG_AUDIO)
        if not frames:
            report("TP-1", "Audio ohne OPT", "FEHLER",
                   f"keine AUDIO-Datagramme empfangen (Pakete gesamt: {stats['total_packets']})")
        else:
            _hdr, opt_len, sel_flags, _data = frames[0]
            if findings["minor_version"] is None:
                findings["minor_version"] = frames[0][0]["minor_version"]
            if opt_len == 0:
                report("TP-1", "Audio ohne OPT", "OK",
                       f"opt_len=0 wie erwartet, selectorFlags={fmt_hex32(sel_flags)}, "
                       f"Rate={stats['rate_hz']:.1f} Hz" if stats['rate_hz'] else
                       f"opt_len=0 wie erwartet, selectorFlags={fmt_hex32(sel_flags)}")
            else:
                report("TP-1", "Audio ohne OPT", "FEHLER",
                       f"opt_len={opt_len} statt erwartet 0, selectorFlags={fmt_hex32(sel_flags)}")

        # -- TP-2: Audio MIT OPT ----------------------------------------
        udp_add_flags(scpi, local_ip, TEST_PORT_AUDIO, ["OPT"])
        frames, stats = receive_frames(sock, TAG_AUDIO)
        if not frames:
            report("TP-2", "Audio MIT OPT", "FEHLER",
                   f"keine AUDIO-Datagramme empfangen (Pakete gesamt: {stats['total_packets']})")
        else:
            _hdr, opt_len, sel_flags, data = frames[0]
            findings["minor_version"] = frames[0][0]["minor_version"]
            rate_txt = f", Rate={stats['rate_hz']:.1f} Hz" if stats["rate_hz"] else ""
            if opt_len == 0:
                report("TP-2", "Audio MIT OPT", "BEFUND",
                       f"Geraet liefert trotz Flag \"OPT\" opt_len=0, selectorFlags={fmt_hex32(sel_flags)}{rate_txt}"
                       f" - kein Audio-OptionalHeader auf diesem Pfad verfuegbar")
            else:
                info = describe_audio_opt(data, opt_len)
                findings["opt_len_positive"] = True
                rb_freq = safe_query(scpi, "FREQ?", lambda v: int(round(float(v))), None)
                rb_band = safe_query(scpi, "BAND?", lambda v: int(round(float(v))), None)
                rb_dem = safe_query(scpi, "DEM?", lambda v: v.strip().upper(), None)
                mism = []
                if rb_freq is not None and "frequency_hz" in info and abs(info["frequency_hz"] - rb_freq) > 1:
                    mism.append(f"FREQ header={info['frequency_hz']} vs SCPI={rb_freq}")
                if rb_band is not None and "bandwidth_hz" in info and abs(info["bandwidth_hz"] - rb_band) > 1:
                    mism.append(f"BAND header={info['bandwidth_hz']} vs SCPI={rb_band}")
                if rb_dem and "demodulation_text" in info and not (
                        info["demodulation_text"].upper().startswith(rb_dem[:2])
                        or rb_dem.upper().startswith(info["demodulation_text"][:2])):
                    mism.append(f"DEM header={info.get('demodulation_text')} vs SCPI={rb_dem}")
                verdict = "FEHLER" if mism else "OK"
                detail = (f"opt_len={opt_len}, selectorFlags={fmt_hex32(sel_flags)}, felder={info}{rate_txt}")
                if mism:
                    detail += " | Abweichungen: " + "; ".join(mism)
                report("TP-2", "Audio MIT OPT", verdict, detail)

        # -- TP-3: Aenderungsverfolgung (nur wenn TP-2 opt_len > 0) ------
        if not findings["opt_len_positive"]:
            report("TP-3", "Aenderungsverfolgung Audio-Header", "BEFUND",
                   "uebersprungen - TP-2 lieferte opt_len=0, keine Header zum Verfolgen")
        else:
            orig_dem = safe_query(scpi, "DEM?", lambda v: v.strip().upper(), "FM")
            orig_band = safe_query(scpi, "BAND?", lambda v: int(round(float(v))), 9000)
            latencies = []
            for demod, bw in (("FM", 120_000), ("AM", 9_000)):
                scpi.send(f"DEM {demod}")
                scpi.send(f"BAND {bw}")

                def matches(info, demod=demod, bw=bw):
                    return (info.get("demodulation_text") == demod
                            and info.get("bandwidth_hz") is not None
                            and abs(info["bandwidth_hz"] - bw) <= 1)

                info, attempts = receive_until(sock, TAG_AUDIO, matches)
                if info is None:
                    report("TP-3", f"Wechsel auf {demod}/{bw} Hz", "FEHLER",
                           f"Aenderung nach {attempts} Datagrammen nicht sichtbar")
                else:
                    latencies.append((demod, bw, attempts))
                    report("TP-3", f"Wechsel auf {demod}/{bw} Hz", "OK",
                           f"sichtbar nach {attempts} Datagramm(en): {info}")
            scpi.send(f"DEM {orig_dem}")
            scpi.send(f"BAND {orig_band}")
            report("TP-3", "Ursprungswerte wiederhergestellt", "OK",
                   f"DEM={orig_dem}, BAND={orig_band}")

            if args.interactive:
                input("\nTP-3 interaktiv: Jetzt MOD/BW am Geraet von Hand aendern, "
                      "dann Enter druecken ...")

                def any_frame(info):
                    return bool(info)

                info, attempts = receive_until(sock, TAG_AUDIO, any_frame)
                if info is None:
                    report("TP-3", "Frontpanel-Fall", "FEHLER", "kein Datagramm nach der Pause empfangen")
                else:
                    report("TP-3", "Frontpanel-Fall", "BEFUND",
                           f"Header nach manueller Aenderung (nach {attempts} Datagramm(en)): {info}")
    finally:
        udp_deregister(scpi, local_ip, TEST_PORT_AUDIO)
        sock.close()
        safe_send(scpi, "SYST:AUD:REM:MOD 0")
    return findings


def run_ifpan_block(scpi: ScpiClient, local_ip: str, args) -> dict:
    findings = {"ok": False}
    sock = open_test_socket(TEST_PORT_IFPAN)
    try:
        udp_register(scpi, local_ip, TEST_PORT_IFPAN, "IFP", flags=["VOLT:AC", "OPT"])
        frames, stats = receive_frames(sock, TAG_IFPAN)
        if not frames:
            report("TP-4", "IFPAN mit OPT", "FEHLER",
                   f"keine IFPAN-Datagramme empfangen (Pakete gesamt: {stats['total_packets']})")
        else:
            _hdr, opt_len, sel_flags, data = frames[0]
            info = describe_ifpan_opt(data, opt_len)
            rate_txt = f", Rate={stats['rate_hz']:.1f} Hz" if stats["rate_hz"] else ""
            if opt_len == 0:
                report("TP-4", "IFPAN mit OPT", "FEHLER",
                       f"kein OptionalHeader trotz Flag OPT, selectorFlags={fmt_hex32(sel_flags)}{rate_txt}")
            elif opt_len == LEN_IFPAN_OPT:
                findings["ok"] = True
                report("TP-4", "IFPAN mit OPT", "OK",
                       f"opt_len={opt_len} (Soll {LEN_IFPAN_OPT}), felder={info}{rate_txt}")
                report("TP-4", "IFPan-Header Struktur", "BEFUND",
                       "IFPan-Header enthaelt KEIN MOD/BW/Speed (nur frequency, spanFrequency, "
                       "reserved, averageType, measureTime) - Annex F bestaetigt")
            else:
                findings["ok"] = opt_len >= LEN_IFPAN_OPT
                report("TP-4", "IFPAN mit OPT", "BEFUND",
                       f"opt_len={opt_len} weicht von Soll-Struktur {LEN_IFPAN_OPT} ab, felder={info}{rate_txt}")
    finally:
        udp_deregister(scpi, local_ip, TEST_PORT_IFPAN)
        sock.close()
    return findings


def run_dscan_block(scpi: ScpiClient, local_ip: str, args) -> dict:
    findings = {"bwzoom_tracks": None}
    dscan_backup = backup_dscan_state(scpi)
    sock = open_test_socket(TEST_PORT_DSCAN)
    try:
        scpi.send("DEM FM")
        scpi.send("FREQ:DSC:RES:AUTO OFF")
        scpi.send(f"FREQ:DSC:STAR {DSCAN_TEST_START_HZ}")
        scpi.send(f"FREQ:DSC:STOP {DSCAN_TEST_STOP_HZ}")
        scpi.send("FREQ:DSC:SPE NORM")
        scpi.send(f"BAND {DSCAN_TEST_BW_HZ}")
        udp_register(scpi, local_ip, TEST_PORT_DSCAN, "DSC", flags=["VOLT:AC", "OPT"])
        scpi.send("FREQ:MODE DSC")
        scpi.send("INIT:IMM")

        frames, stats = receive_frames(sock, TAG_DSCAN)
        if not frames:
            report("TP-5", "DSCAN mit OPT", "FEHLER",
                   f"keine DSCAN-Datagramme empfangen (Pakete gesamt: {stats['total_packets']})")
        else:
            _hdr, opt_len, sel_flags, data = frames[0]
            info = describe_dscan_opt(data, opt_len)
            rate_txt = f", Rate={stats['rate_hz']:.1f} Hz" if stats["rate_hz"] else ""
            if opt_len == 0:
                report("TP-5", "DSCAN mit OPT", "FEHLER",
                       f"kein OptionalHeader trotz Flag OPT, selectorFlags={fmt_hex32(sel_flags)}{rate_txt}")
            else:
                report("TP-5", "DSCAN mit OPT", "OK" if opt_len == LEN_DSCAN_OPT else "BEFUND",
                       f"opt_len={opt_len} (Soll {LEN_DSCAN_OPT}), felder={info}{rate_txt}")
                bw_zoom_before = info.get("bw_zoom")

                # BW-ZOOM einschalten und pruefen, ob bwZoom im Header folgt.
                # Vor dem "Nachher"-Capture: im OS-Puffer wartende Alt-Pakete
                # verwerfen (sonst stammen die zuerst gelesenen Frames noch
                # von VOR der Umschaltung) und eine Einschwingpause
                # abwarten, dann datagrammweise auf die Aenderung warten
                # (statt nur das allererste Frame danach zu pruefen) -
                # liefert zugleich die Latenz in Anzahl Datagrammen.
                marker_hz = (DSCAN_TEST_START_HZ + DSCAN_TEST_STOP_HZ) // 2
                scpi.send(f"FREQ:DSC:MARK {marker_hz}")
                scpi.send("FREQ:DSC:RES:AUTO ON")
                drained = drain_socket(sock)
                settle_s = 0.5
                time.sleep(settle_s)

                def bwzoom_changed(info, baseline=bw_zoom_before):
                    return info.get("bw_zoom") is not None and info.get("bw_zoom") != baseline

                info2, attempts = receive_until(sock, TAG_DSCAN, bwzoom_changed,
                                                 describe_fn=describe_dscan_opt, max_attempts=60)
                if info2 is None:
                    findings["bwzoom_tracks"] = False
                    report("TP-5", "BW-ZOOM Nachlauf", "BEFUND",
                           f"bwZoom blieb nach {drained} verworfenen Alt-Paketen, {settle_s}s Pause und "
                           f"{attempts} weiteren Datagrammen unveraendert bei {bw_zoom_before}")
                else:
                    findings["bwzoom_tracks"] = True
                    report("TP-5", "BW-ZOOM Nachlauf", "OK",
                           f"bwZoom vorher={bw_zoom_before}, nachher={info2.get('bw_zoom')} "
                           f"(sichtbar nach {drained} verworfenen Alt-Paketen, {settle_s}s Pause und "
                           f"{attempts} Datagramm(en)), felder={info2}")
                scpi.send("FREQ:DSC:RES:AUTO OFF")
    finally:
        scpi.send("ABOR")
        udp_deregister(scpi, local_ip, TEST_PORT_DSCAN)
        sock.close()
        restore_dscan_state(scpi, dscan_backup)
    return findings


def run_squelch_block(scpi: ScpiClient, local_ip: str, args) -> None:
    orig_thr = safe_query(scpi, "OUTP:SQU:THR?", lambda v: float(v), None)
    try:
        new_thr = (orig_thr + 5.0) if orig_thr is not None else 20.0
        scpi.send(f"OUTP:SQU:THR {new_thr}")
        rb = safe_query(scpi, "OUTP:SQU:THR?", lambda v: float(v), None)
        report("TP-6", "Squelch-Schwelle per SCPI geaendert", "OK",
               f"gesetzt={new_thr}, Rueckgelesen={rb}")
        report("TP-6", "Squelch nicht im Header", "BEFUND",
               "Kein Audio-/IFPan-/DSCan-OptionalHeader (Annex F) enthaelt ein Squelch-Schwellenfeld "
               "- die Schwelle ist ausschliesslich per SCPI (OUTP:SQU:THR?) abrufbar")

        sock = open_test_socket(TEST_PORT_IFPAN)
        try:
            udp_register(scpi, local_ip, TEST_PORT_IFPAN, "IFP", flags=["VOLT:AC", "OPT", "SQU"])
            frames, stats = receive_frames(sock, TAG_IFPAN, count=1)
            if not frames:
                report("TP-6", "SQUelch-Flag selectorFlags-Bit", "FEHLER",
                       "keine IFPAN-Datagramme mit SQU-Flag empfangen")
            else:
                _hdr, opt_len, sel_flags, _data = frames[0]
                bit_set = bool(sel_flags & FLAG_SQUELCH)
                report("TP-6", "SQUelch-Flag selectorFlags-Bit", "BEFUND",
                       f"selectorFlags={fmt_hex32(sel_flags)}, Bit 0x40000000 "
                       f"(SIGNAL_GREATER_SQUELCH) {'gesetzt' if bit_set else 'NICHT gesetzt'} "
                       f"- keine tiefere Auswertung der Payload durchgefuehrt")
        finally:
            udp_deregister(scpi, local_ip, TEST_PORT_IFPAN)
            sock.close()
    finally:
        if orig_thr is not None:
            safe_send(scpi, f"OUTP:SQU:THR {orig_thr}")


def print_summary(audio_findings: dict, ifpan_findings: dict, dscan_findings: dict) -> None:
    audio_ok = audio_findings.get("opt_len_positive")
    bwzoom_tracks = dscan_findings.get("bwzoom_tracks")

    def audio_col(present: bool) -> str:
        return "ja" if present and audio_ok else ("nein" if audio_ok is False else "n/a (TP-2 opt_len=0)")

    rows = [
        ("BW", "ja" if audio_ok else "nein (opt_len=0)", "nein", "nein", "nur SCPI: BAND?"),
        ("MOD", "ja" if audio_ok else "nein (opt_len=0)", "nein", "nein", "nur SCPI: DEM?"),
        ("FREQ", "ja" if audio_ok else "nein (opt_len=0)", "ja (frequency)", "nein (nur Start/Stop/Step/Mark)",
         "-"),
        ("Span", "nein", "ja (spanFrequency)", "nein (aus Stop-Start ableitbar)", "-"),
        ("Speed", "nein", "nein", "nein", "nur SCPI: FREQ:DSC:SPE?"),
        ("BW-Zoom", "nein", "nein",
         "ja (bwZoom)" + (", live nachgezogen" if bwzoom_tracks else ", Nachlauf nicht bestaetigt"), "-"),
        ("SQL-Schwelle", "nein", "nein", "nein", "nur SCPI: OUTP:SQU:THR?"),
    ]
    print("\n--- Zusammenfassung: Parameter -> sichtbar in ---")
    header = f"{'Parameter':<14}{'Audio-Header':<24}{'IFPAN-Header':<20}{'DSCAN-Header':<32}{'nur SCPI'}"
    print(header)
    print("-" * len(header))
    for name, a, i, d, s in rows:
        print(f"{name:<14}{a:<24}{i:<20}{d:<32}{s}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_EB200_IP,
                         help=f"EB200-IP (Default wie waterfall.py: {DEFAULT_EB200_IP})")
    parser.add_argument("--local-ip", default=None,
                         help="Lokale Empfangs-IP (Default: automatisch per verbundenem UDP-Socket ermittelt)")
    parser.add_argument("--audio-mode", type=int, default=6,
                         help="SYSTem:AUDio:REMote:MODe fuer TP-1..TP-3 (Default 6 = 16 kHz/16 Bit/mono, "
                              "wie waterfall.py AUDIO_REMOTE_MODE)")
    parser.add_argument("--interactive", action="store_true",
                         help="TP-3: zusaetzliche Pause fuer manuelle Frontpanel-Aenderung")
    args = parser.parse_args()

    local_ip = args.local_ip or determine_local_ip(args.host)
    print(f"EB200={args.host}:{SCPI_PORT}  lokale IP={local_ip}")
    print(f"Test-Ports: Audio={TEST_PORT_AUDIO}  IFPAN={TEST_PORT_IFPAN}  DSCAN={TEST_PORT_DSCAN}")

    scpi = ScpiClient(args.host, SCPI_PORT)
    try:
        dump_udp_table(scpi, "vor Testbeginn")
        state = backup_state(scpi)
        try:
            audio_findings = run_audio_block(scpi, local_ip, args)
            ifpan_findings = run_ifpan_block(scpi, local_ip, args)
            dscan_findings = run_dscan_block(scpi, local_ip, args)
            run_squelch_block(scpi, local_ip, args)

            minor_version = audio_findings.get("minor_version")
            if minor_version is not None:
                supports_sdemod = minor_version >= 0x26
                report("TP-7", "minor_version_number", "OK" if supports_sdemod else "BEFUND",
                       f"minor_version_number={minor_version:#04x} "
                       f"({'>= 0x26, sDemodulation verfuegbar' if supports_sdemod else '< 0x26, KEIN sDemodulation-Feld'})")
            else:
                report("TP-7", "minor_version_number", "BEFUND", "konnte nicht ermittelt werden (kein Audio-Frame)")

            print_summary(audio_findings, ifpan_findings, dscan_findings)
        finally:
            restore_state(scpi, state)
        dump_udp_table(scpi, "nach Testende")
    finally:
        scpi.close()

    print("\n--- Alle Testpunkte ---")
    for line in RESULTS:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
