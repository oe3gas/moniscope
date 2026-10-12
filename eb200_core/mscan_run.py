#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/mscan_run.py

Lauf-Logik fuer MSCAN-Jobs (PLAN.md T-1005) - buendelt ALLES, was ohne
angeschlossenes Geraet testbar ist: Lauf-Parameter aus einer MSCAN-Job-
Definition (eb200_core/mscan_job.py) ableiten, SCPI-Befehlsstrings fuer
Programmieren/Readback/Scan-Steuerung/Teardown aufbauen (reine Strings, keine
Verbindung), das MScan-UDP-Datagramm parsen und eine zeitgesteuerte
Segmentmaschine + einen WAV-Recorder betreiben. Dieses Modul importiert
NICHTS aus waterfall.py oder tools/, und waterfall.py/tools/mscan_probe.py
bleiben unveraendert - die Einbindung (DeviceController._run_mscan_loop(),
Scheduler, Banner, Start-Dialog in :8050) liegt dort, ruft aber ausschliesslich
die Funktionen/Klassen HIER auf.

Session B (T-1005, waterfall.py-Einbindung): MscanJobRecorder traegt
zusaetzlich die Attribute phase ("programmieren"|"scan"|"ende") und
programmed (Anzahl bereits programmierter Speicherplaetze) - beide werden
NICHT von diesem Modul selbst gesetzt, sondern vom Controller-Loop
(_run_mscan_loop() in waterfall.py) waehrend des Laufs; current_stats()
gibt sie unveraendert weiter (fuer Banner/job_active()).

Hardwarefakten: docs/EB200_Geraeteprofil.md, Abschnitt MSCAN. Vorlagen:
tools/mscan_probe.py (Befehlsaufbau Phase C/E, parse_mscan, Zyklusende-
Marker, Readback-Parser - die Logik wurde von dort HIERHER uebernommen,
das Werkzeug selbst bleibt unveraendert), eb200_core/job_runner.py
(Recorder-Muster: record/current_stats/_maybe_heartbeat/close/failed/
extra_files), eb200_core/audio_wav.py (WavAudioWriter), eb200_core/
job_store.py (heartbeat/Manifest), eb200_core/mscan_job.py (resolve_snapshot,
Params).

Schnittstelle zum aeusseren Generic-Attribute-Header (parse_mscan_items,
Teil C): waterfall.py hat fuer DSCAN bereits den Zwei-Schritt-Ablauf
"parse_datagram() liefert (tag, payload)-Paare, dann wird PRO Attribut-Typ
das Trace-Attribut (n_items/reserved/opt_len/selector_flags per
FMT_TRACE_ATTR_HDR) entpackt" (siehe parse_dscan_trace() dort). Diese
Trace-Attribut-Klammer bleibt bewusst bei waterfall.py bzw. dessen
Aufrufer in Session B - parse_mscan_items() nimmt deshalb NICHT die rohe
Payload, sondern schon die entpackten Bestandteile (selector_flags,
optionaler-Header-Bytes, Rest-Daten, n_items) entgegen. Ein Aufrufer in
waterfall.py wuerde also schreiben:
    n_items, _res, opt_len, sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, payload, 0)
    opt_header = payload[LEN_TRACE_ATTR_HDR:LEN_TRACE_ATTR_HDR + opt_len]
    data = payload[LEN_TRACE_ATTR_HDR + opt_len:]
    result = mscan_run.parse_mscan_items(sel_flags, opt_header, data, n_items)
Damit bleibt die generische Rahmung (EB200-Header, Generic-Attribute,
Trace-Attribut-Kopf) EINMAL in waterfall.py (dort fuer DSCAN/IFPAN ohnehin
vorhanden) und dieses Modul kennt nur die MSCAN-spezifische Semantik
(Item-Gruppierung, Zyklusende-Marker, OptionalHeader-Felder).

Manifest-Feld "results": job_store.heartbeat() hat seit Session B einen
eigenen Parameter results=None, der bei Angabe manifest["results"] setzt
(oberste Manifest-Ebene, siehe eb200_core/job_store.py) - MscanJobRecorder.
close() uebergibt results() darueber, NICHT mehr als verschachtelten Wert
innerhalb von stats (fruehere Session-A-Fassung).

Nachtrag 3 (T-1005 B, Hardwarebefund: -200 "Execution error" durch
TRAC:UDP:DELete auf einen noch nicht registrierten UdpPath bei frischem
Serverstart): scan_setup_commands() wurde in zwei Funktionen geteilt -
scan_cleanup_paths_commands() traegt NUR die beiden DELete-Befehle (eine
Vorab-Raeumung, deren Fehler der Aufrufer bewusst ignoriert, siehe
waterfall.py _run_mscan_loop() Phase d), scan_setup_commands() alle
uebrigen Befehle OHNE DELete (deren Fehler weiterhin ein Startfehler
sind).

Nachtrag 4 (T-1005 B, erster erfolgreicher Hardware-Lauf): recordings[]
traegt jetzt start_at (Wanduhrzeit, aus t0_wall/t0_mono zurueckgerechnet),
den Dateinamen OHNE Verzeichnisanteil (file, os.path.basename) sowie
sowohl den eigenen Zyklenzaehler (cycle) als auch den - empirisch
unzuverlaessigen - Geraete-Header-Wert (device_cycle). MscanJobRecorder.
current_stats() gibt zusaetzlich audio_orphan/unknown_freq/
discarded_short aus (Laufliste/Banner, siehe manage_jobs.py).

Nachtrag 6 (T-1006, Hardwarebefund 2026-08-27): audio_orphan ist bei jedem
Lauf zu ~22-23 Datagrammen ein Startburst des Audio-Pfads VOR dem ersten
MScan-Item, kein Verlust. Der Zaehler wird daher zusaetzlich in
audio_orphan_pre (vor dem ersten Item), audio_orphan_post (nach close())
und audio_orphan_mid (der Rest, echtes Warnsignal) aufgeteilt; der
Gesamtzaehler audio_orphan bleibt fuer bestehende Manifeste/Banner erhalten.

Nachtrag 7 (T-1019, Pegel-Inventur): Die Inventur kann seit T-1019 auch
ueber mehrere Durchgaenge laufen (params["stop_after"] == "runtime" ->
COUNt INF, Ende ueber Laufende/Stopp wie bei der Ueberwachung) und ganz
ohne Ton arbeiten (params["record_audio"] is False). Ohne Ton werden weder
der Audio-Fernmodus noch der AUD-UdpPath eingeschaltet (siehe
scan_setup_commands/scan_teardown_commands) und statt MscanJobRecorder
laeuft MscanLevelRecorder mit MscanLevelInventory (Teil E2): der zaehlt je
Kanal die Squelch-Oeffnungen oberhalb von params["level_active_dbuv"] und
liefert am Ende eine Liste ALLER Kanaele mit aktiv/nicht aktiv, Treffern
und der Aufschluesselung je Durchgang - Dateien entstehen dabei keine.
make_recorder() waehlt anhand von record_audio zwischen beiden Recordern.

Regeln: nur Python-Standardbibliothek, kein Import aus waterfall.py oder
tools/. Deutsche Kommentare. Konsolenausgaben ASCII.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
from datetime import datetime

import numpy as np

# Erlaubt sowohl "python -m eb200_core.mscan_run" als auch den direkten
# Skriptaufruf "python eb200_core/mscan_run.py" (Selbsttest-Konvention
# dieses Projekts, siehe eb200_core/job_runner.py): beim Direktaufruf fehlt
# das Repo-Root im Suchpfad fuer die eb200_core-Importe.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.audio_wav import WavAudioWriter

MAX_CHANNELS = 1000   # MEM0..MEM999


# ---------------------------------------------------------------------------
# Teil A - Lauf-Parameter
# ---------------------------------------------------------------------------

def build_run_params(job_def: dict, snapshot: list) -> dict:
    """Baut die eingefrorenen Lauf-Parameter aus einer MSCAN-Job-Definition
    (eb200_core/mscan_job.py) und ihrem aufgeloesten Kanal-Snapshot
    (mscan_job.resolve_snapshot()). mem = Index 0..N-1 in Snapshot-
    Reihenfolge (spaeter MEM<mem> beim Programmieren). Wirft ValueError bei
    leerer Liste oder mehr als MAX_CHANNELS Kanaelen; squelch_dbuv None wird
    auf 20 dBuV ersetzt (Projekt-Default, siehe tools/mscan_probe.py)."""
    if not snapshot:
        raise ValueError("empty channel list - at least one channel required")
    if len(snapshot) > MAX_CHANNELS:
        raise ValueError(
            f"{len(snapshot)} channels - at most {MAX_CHANNELS} allowed "
            f"(MEM0..MEM{MAX_CHANNELS - 1})"
        )

    params = job_def.get("params", {})
    channels = []
    for i, ch in enumerate(snapshot):
        squelch_dbuv = ch.get("squelch_dbuv")
        if squelch_dbuv is None:
            squelch_dbuv = 20
        channels.append({
            "mem": i,
            "id": ch.get("id"),
            "label": ch.get("label"),
            "freq_hz": ch.get("freq_hz"),
            "demod": ch.get("demod"),
            "bw_hz": ch.get("bw_hz"),
            "squelch_dbuv": squelch_dbuv,
            "service": ch.get("service"),
            "file": ch.get("file"),
            "tags": ch.get("tags"),
        })

    return {
        "mscan_job_id": job_def.get("id"),
        "title": job_def.get("title"),
        "mode": params.get("mode", "ueberwachung"),
        # T-1019: Vorgaben fuer Mehrfachdurchgang und Pegel-Inventur. Die
        # Defaults hier entsprechen dem Verhalten vor T-1019, damit ein
        # Lauf aus einer alten Job-Datei unveraendert laeuft.
        "stop_after": params.get("stop_after", "cycle"),
        "record_audio": params.get("record_audio", True) is not False,
        "level_active_dbuv": params.get("level_active_dbuv"),
        "dwell_s": params.get("dwell_s"),
        "hold_s": params.get("hold_s", 2.0),
        "squelch_from_memory": params.get("squelch_from_memory", True),
        "min_recording_s": params.get("min_recording_s", 1.0),
        "channels": channels,
    }


def run_label(job_def: dict) -> str:
    """Kurzbeschriftung fuer job_store.create_job(label=...) - Muster
    'MSCAN <ID> <Titel>'."""
    return f"MSCAN {job_def.get('id')} {job_def.get('title')}"


# ---------------------------------------------------------------------------
# Teil B - SCPI-Befehle (reine Strings, keine Verbindung)
# ---------------------------------------------------------------------------

MEMORY_CLEAR_COMMAND = "MEMory:CLEar MEM0,MAXimum"

_ANTENNA_FIELD_RE = re.compile(r"^\(@(\d+)\)$")


def memory_write_command(mem: int, ch: dict, antenna: int) -> str:
    """Programmierbefehl fuer einen Speicherplatz (Muster
    tools/mscan_probe.py run_phase_c()): ATT 0, ATTA 1, SQUC 1, AFC 0,
    ACT 1 - feste Werte siehe Auftrag/Handbuch."""
    squelch_dbuv = ch.get("squelch_dbuv")
    if squelch_dbuv is None:
        squelch_dbuv = 20
    return (
        f"MEMory:CONTents MEM{mem},{int(ch['freq_hz'])},{int(squelch_dbuv)},"
        f"{ch['demod']},{int(ch['bw_hz'])},(@{antenna}),0,1,1,0,1"
    )


def _parse_antenna_field(field: str) -> int:
    """Zerlegt ein IEEE-488.2-Definite-Length-Block-Feld ('#1<n><payload>',
    z. B. '#14(@1)': '#' + eine Ziffer fuer die Laenge der Laengenangabe,
    dann die Laengenangabe selbst, dann der Blockinhalt) und liefert die
    Antennennummer aus dem Blockinhalt '(@n)'. ValueError bei Fehlform."""
    field = field.strip()
    if len(field) < 2 or field[0] != "#" or not field[1].isdigit():
        raise ValueError(f"unerwartetes Antennenfeld-Format: {field!r}")
    n_digits = int(field[1])
    pos = 2
    length_str = field[pos:pos + n_digits]
    if len(length_str) != n_digits or not length_str.isdigit():
        raise ValueError(f"unerwartetes Antennenfeld-Format: {field!r}")
    length = int(length_str)
    pos += n_digits
    payload = field[pos:pos + length]
    if len(payload) != length:
        raise ValueError(f"unerwartetes Antennenfeld-Format: {field!r}")
    m = _ANTENNA_FIELD_RE.match(payload)
    if not m:
        raise ValueError(f"unerwartetes Antennen-Payload: {payload!r}")
    return int(m.group(1))


def parse_memory_readback(text: str) -> dict:
    """Zerlegt die Antwort von MEMory:CONTents? <name> (Handbuch:
    'F,THR,DEM,BW,#1<n>(@a),ATT,ATTA,SQUC,AFC,ACT') in ihre Felder. F/THR/BW
    werden als reine Zahl erwartet (Nachtrag 2, tools/mscan_probe.py). act
    wird zu int (1 bei '1'/'ON', sonst 0). ValueError bei Fehlform."""
    parts = [p.strip() for p in text.strip().split(",")]
    if len(parts) != 10:
        raise ValueError(f"MEMory:CONTents?-Antwort hat {len(parts)} statt 10 Felder: {text!r}")
    freq_hz, thr, demod, bw_hz, ant_field, att, atta, squc, afc, act = parts
    try:
        freq_hz_i = int(round(float(freq_hz)))
        thr_i = int(round(float(thr)))
        bw_hz_i = int(round(float(bw_hz)))
    except ValueError as exc:
        raise ValueError(f"MEMory:CONTents? reply: number not parsable: {text!r}") from exc
    antenna = _parse_antenna_field(ant_field)
    act_i = 1 if act.strip().upper() in ("1", "ON") else 0
    return {
        "freq_hz": freq_hz_i, "thr": thr_i, "demod": demod, "bw_hz": bw_hz_i,
        "antenna": antenna, "att": att, "atta": atta, "squc": squc, "afc": afc,
        "act": act_i,
    }


def memory_matches(ch: dict, rb: dict) -> list[str]:
    """Vergleicht den Soll-Kanal (ch, wie in build_run_params()['channels'])
    gegen die Readback-Werte (rb, aus parse_memory_readback()). Frequenz
    exakt, DEM gleich (Gross-/Kleinschreibung egal), BW gleich, ACT == 1.
    Liefert die Liste der Abweichungen - leer bedeutet: OK."""
    diffs: list[str] = []
    if int(rb["freq_hz"]) != int(ch["freq_hz"]):
        diffs.append(f"Frequenz: erwartet {ch['freq_hz']}, Geraet {rb['freq_hz']}")
    if str(rb["demod"]).strip().upper() != str(ch["demod"]).strip().upper():
        diffs.append(f"DEM: erwartet {ch['demod']}, Geraet {rb['demod']}")
    if int(rb["bw_hz"]) != int(ch["bw_hz"]):
        diffs.append(f"BW: erwartet {ch['bw_hz']}, Geraet {rb['bw_hz']}")
    if rb["act"] != 1:
        diffs.append(f"ACT: erwartet 1, Geraet {rb['act']}")
    return diffs


def scan_cleanup_paths_commands(local_ip: str, mscan_port: int, audio_port: int) -> list[str]:
    """Vorab-Raeumung VOR scan_setup_commands() (Nachtrag 3): TRAC:UDP:DELete
    auf beiden UdpPaths - raeumt CMOS-Reste eines frueheren Laufs/Absturzes
    weg (Annex F: UdpPath-Registrierungen ueberleben einen Geraete-Neustart
    NICHT automatisch, koennen aber nach einem Absturz dieses Werkzeugs
    stehen bleiben). Ein -200 "Execution error" auf einem noch NICHT
    registrierten Pfad (z. B. frischer Serverstart) ist dabei der
    Normalfall, KEIN Startfehler - der Aufrufer (waterfall.py
    _run_mscan_loop() Phase d) ignoriert Fehler hier bewusst und loggt sie
    nur."""
    return [
        f'TRAC:UDP:DELete "{local_ip}",{mscan_port}',
        f'TRAC:UDP:DELete "{local_ip}",{audio_port}',
    ]


def scan_setup_commands(params: dict, local_ip: str, mscan_port: int,
                         audio_port: int, audio_mode: int) -> list[str]:
    """Baut die Befehlsfolge fuer Phase E (Scan-Aufbau) - reine Strings,
    keine Verbindung. Reihenfolge siehe Auftrag: erst Grundzustand/Format,
    dann Scan-Parameter, dann Audio-Fernmodus (muss VOR der AUD-Tag-
    Registrierung stehen, siehe docs/EB200_Geraeteprofil.md Abschnitt
    MSCAN), dann UDP-Pfade MSC/AUD, zuletzt MEM999 + FREQ:MODE MSC.
    scan_start_command() (INIT:CONM) ist bewusst ein EIGENER Schritt, kein
    Teil dieser Liste. Die TRAC:UDP:DELete-Vorabraeumung ist seit Nachtrag 3
    NICHT mehr Teil dieser Liste, siehe scan_cleanup_paths_commands()."""
    mode = params.get("mode", "ueberwachung")
    stop_after = params.get("stop_after", "cycle")
    dwell_s = params.get("dwell_s")
    hold_s = params.get("hold_s", 2.0)
    squelch_from_memory = params.get("squelch_from_memory", True)
    record_audio = params.get("record_audio", True) is not False

    # T-1019: COUNt 1 nur noch bei der Inventur mit stop_after "cycle". Eine
    # Inventur mit stop_after "runtime" laeuft wie die Ueberwachung mit
    # COUNt INF und endet ueber Laufende/Stopp - der Zyklusende-Marker
    # kommt dabei je Runde und traegt die Durchgangszaehlung.
    count_arg = "1" if (mode == "inventur" and stop_after == "cycle") else "INF"
    dwell_arg = "INFinity" if dwell_s is None else f"{dwell_s}"
    squelch_arg = "MEM" if squelch_from_memory else "NONE"

    cmds = [
        "ABORt",
        "FREQ:MODE CW",
        "FORMat:MEMory ASCii",
        f"MSCan:COUNt {count_arg}",
        f"MSCan:DWELl {dwell_arg}",
        f"MSCan:HOLD:TIME {hold_s}",
        'MSCan:CONTrol "STOP:SIGN"',
        f"OUTPut:SQUelch:CONTrol {squelch_arg}",
        "MSCan:DIRection UP",
    ]
    # T-1019: Ohne Tonaufnahme bleiben Audio-Fernmodus und AUD-UdpPath
    # ungenutzt - beides wird gar nicht erst eingeschaltet (der Startburst
    # des Audio-Pfads entfaellt damit ebenfalls). Der MSC-Pfad traegt
    # Frequenz und Pegel, mehr braucht die Pegel-Inventur nicht.
    if record_audio:
        cmds.append(f"SYST:AUD:REM:MOD {audio_mode}")
    cmds.append(f'TRAC:UDP:TAG:ON "{local_ip}",{mscan_port},MSC')
    cmds.append(
        f'TRAC:UDP:FLAG:ON "{local_ip}",{mscan_port},"VOLTage:AC","FREQuency:RX","SQUelch","OPTional"')
    if record_audio:
        cmds.append(f'TRAC:UDP:TAG:ON "{local_ip}",{audio_port},AUD')
    cmds.append("MSCan:CHANnel MEM999")
    cmds.append("FREQ:MODE MSC")
    return cmds


def scan_start_command() -> str:
    """Eigener Schritt NACH scan_setup_commands() (siehe dort)."""
    return "INIT:CONM"


def scan_teardown_commands(local_ip: str, mscan_port: int, audio_port: int,
                            record_audio: bool = True) -> list[str]:
    """Rueckbau-Befehlsfolge fuer Phase G (Muster tools/mscan_probe.py
    run_phase_g()): Scan anhalten, beide UDP-Pfade abmelden, Audio-
    Fernmodus zuruecksetzen, Squelch-Steuerung/Frequenzmodus zuruecksetzen.
    T-1019: Bei record_audio False wurde der AUD-Pfad nie registriert - die
    beiden AUD-Befehle entfallen dann, weil TAG:OFF/DELete auf einen
    unbekannten Pfad -200 "Execution error" liefert (Muster Nachtrag 3).
    SYST:AUD:REM:MOD 0 bleibt in JEDEM Fall stehen: es stellt den
    Geraetezustand her, auch wenn dieser Lauf ihn nicht veraendert hat.
    Das Vorgabeargument haelt bestehende Aufrufer unveraendert."""
    cmds = [
        "ABORt",
        'MSCan:CONTrol:OFF "STOP:SIGN"',
        f'TRAC:UDP:TAG:OFF "{local_ip}",{mscan_port},MSC',
        f'TRAC:UDP:DELete "{local_ip}",{mscan_port}',
    ]
    if record_audio:
        cmds.append(f'TRAC:UDP:TAG:OFF "{local_ip}",{audio_port},AUD')
        cmds.append(f'TRAC:UDP:DELete "{local_ip}",{audio_port}')
    cmds.extend([
        "SYST:AUD:REM:MOD 0",
        "OUTPut:SQUelch:CONTrol NONE",
        "FREQ:MODE CW",
    ])
    return cmds


# ---------------------------------------------------------------------------
# Teil C - Datagramm-Parser (MScan-Nutzlast)
# ---------------------------------------------------------------------------

MSCAN_TAG = 201

FLAG_LEVEL = 0x01                       # short, dBuV*10 wie DSCAN
FLAG_FREQUENCY = 0x00020000             # unsigned long, Hz
FLAG_SIGNAL_GREATER_SQUELCH = 0x40000000
FLAG_OPTIONAL_HEADER = 0x80000000

# Annex F, hardware-verifiziert (siehe tools/mscan_probe.py Nachtrag 2):
# 5 SIGNED short-Felder, 10 Byte.
FMT_MSCAN_OPT_HDR = ">hhhhh"
LEN_MSCAN_OPT_HDR = struct.calcsize(FMT_MSCAN_OPT_HDR)

# Zyklusende-Marker (EMPIRISCH, siehe docs/EB200_Geraeteprofil.md Abschnitt
# MSCAN / tools/mscan_probe.py): Item mit freq_hz == 0 und Rohpegel >= 2000.
MARKER_RAW_LEVEL_MIN = 2000


def parse_mscan_items(flags: int, opt_header: bytes, data: bytes, n_items: int) -> dict:
    """Zerlegt die MSCAN-Trace-Daten EINES MSC-Generic-Attributs in Items
    (siehe Moduldoku fuer die gewaehlte Schnittstelle zum aeusseren
    Generic-/Trace-Attribut-Header). data enthaelt die Trace-Werte
    GRUPPIERT nach Flag (erst n_items LEVEL-shorts, dann n_items
    FREQUENCY-ulongs) - Annex F, hardware-verifiziert. opt_header sind die
    rohen OptionalHeader-Bytes (kann leer sein, wenn FLAG_OPTIONAL_HEADER
    nicht gesetzt ist oder das Geraet keinen mitschickt).

    Liefert {"header": {...} | None, "items": [{freq_hz, level_dbuv,
    raw_level, is_marker}, ...], "signal_greater_squelch": bool}."""
    header = None
    if (flags & FLAG_OPTIONAL_HEADER) and len(opt_header) >= LEN_MSCAN_OPT_HDR:
        cycle_count, hold_time, dwell_time, direction_up, stop_signal = struct.unpack_from(
            FMT_MSCAN_OPT_HDR, opt_header, 0)
        header = {
            "cycle_count": cycle_count, "hold_time": hold_time, "dwell_time": dwell_time,
            "direction_up": direction_up, "stop_signal": stop_signal,
        }

    pos = 0
    raw_levels: list[int] = []
    if flags & FLAG_LEVEL:
        need = n_items * 2
        if pos + need > len(data):
            raise ValueError("MSCAN datagram too short for LEVEL values")
        raw_levels = list(struct.unpack_from(f">{n_items}h", data, pos))
        pos += need

    freqs: list[int] = []
    if flags & FLAG_FREQUENCY:
        need = n_items * 4
        if pos + need > len(data):
            raise ValueError("MSCAN datagram too short for FREQUENCY values")
        freqs = list(struct.unpack_from(f">{n_items}I", data, pos))
        pos += need

    items = []
    for i in range(n_items):
        raw_level = raw_levels[i] if i < len(raw_levels) else None
        level_dbuv = raw_level / 10.0 if raw_level is not None else None
        freq_hz = freqs[i] if i < len(freqs) else None
        is_marker = freq_hz == 0 and raw_level is not None and raw_level >= MARKER_RAW_LEVEL_MIN
        items.append({
            "freq_hz": freq_hz, "level_dbuv": level_dbuv, "raw_level": raw_level,
            "is_marker": is_marker,
        })

    return {
        "header": header, "items": items,
        "signal_greater_squelch": bool(flags & FLAG_SIGNAL_GREATER_SQUELCH),
    }


# ---------------------------------------------------------------------------
# Teil D - Segmentmaschine (zeitgesteuert, ohne Sockets)
# ---------------------------------------------------------------------------

# Rueckfall-Schwelle, falls kein 28-Byte-Audio-Marker ankommt (siehe
# docs/EB200_Geraeteprofil.md Abschnitt MSCAN): eine Luecke seit dem
# letzten Audio-Datagramm groesser als das schliesst das offene Segment.
DEFAULT_GAP_S = 0.5

# T-1018: Zuschlag auf hold_s fuer die wirksame Lueckenschwelle. Waehrend der
# Hold-Zeit steht der Empfaenger nachweislich auf dem Kanal und der Squelch
# faellt nicht ab - eine Sprechpause ist dort KEIN Grund zum Schliessen. Die
# feste Schwelle von 0,5 s hat Aufnahmen mitten in der Aussendung zerschnitten
# (Befund longrun_20260909_184414: sieben Luecken zwischen 0,69 und 1,94 s,
# alle im Fenster zwischen gap_s und hold_s).
GAP_EXTRA_S = 0.5

# T-1017: Squelch-Einschaltknall. Der EB200 liefert bei jeder Squelch-Oeffnung
# einen breitbandigen Knall von ~65 ms (2026-09-09 an vier Aufnahmen im
# Spektrogramm vermessen, ueber die Aufnahmen hinweg bemerkenswert konstant).
# Davor steht Stille wechselnder Laenge (31-128 ms), erst danach Nutzsignal.
# Beides wird verworfen: Stille datagrammweise, der Knall byte-genau (ein
# Datagramm ist 30 ms, der Knall laenger - datagrammweise waere zu grob).
LEAD_IN_SILENCE_PEAK = 512    # Spitzenwert (16 Bit), darunter gilt ein Datagramm als Stille
PLOP_SKIP_BYTES = 2240        # 70 ms bei 32000 B/s, mit Reserve auf die gemessenen 65 ms
PLOP_GAP_S = 0.25             # Stille darueber = neue Squelch-Oeffnung = neuer Knall
LEAD_IN_MAX_BYTES = 32000     # Notbremse: 1 s; ein Segment aus reiner Stille frisst sich nicht fest

# T-1014a: Fortsetzungsfenster. Marker, Zyklusende, ein Item auf einem ANDEREN
# Kanal und die Lueckenregel beenden ein Segment nur VORLAEUFIG - es bleibt mit
# offener Datei vorgemerkt, bis dieses Fenster ablaeuft. Trifft in der
# Zwischenzeit Audio ein (Ton in der Hold-Zeit kommt OHNE begleitendes Item) oder
# ein Item auf DEMSELBEN Kanal (naechster Rundlauf), wird dieselbe Datei
# fortgeschrieben statt eine neue anzulegen. Belege: Messreihe 2026-09-08,
# docs/EB200_Geraeteprofil.md Abschnitt "Audio-Bilanz aus Produktionslaeufen".
RESUME_WINDOW_MIN_S = 2.0     # Untergrenze, deckt hold_s 0 mit einem Rundlauf ab
RESUME_WINDOW_EXTRA_S = 1.0   # Zuschlag auf hold_s (Rundlauf bei 61 Kanaelen: 0,7-1,2 s)
MAX_PENDING = 8               # gleichzeitig vorgemerkte Segmente (offene Dateien)


class MscanSegmenter:
    """Ordnet MScan-Items, Audio-Datagramme und Zyklusende-Markern
    Aufnahme-Segmente zu - rein zeitgesteuert (Parameter t sind
    Aufrufer-Zeitstempel, z. B. time.monotonic() seit Scan-Start in
    Session B), OHNE eigenen Socket-/Geraetezugriff. Ein offenes Segment
    entsteht mit dem MScan-Item (Squelch-Oeffnung). Marker, Zyklusende, ein
    Item auf einem anderen Kanal und die Lueckenregel (> gap_s, siehe
    tick()) beenden es seit T-1014a nur VORLAEUFIG: das Segment bleibt mit
    offener Datei vorgemerkt, bis das Fortsetzungsfenster
    (self.resume_window_s) ablaeuft. Erst dann wird endgueltig geschlossen
    und min_recording_s entscheidet ueber Behalten oder Verwerfen. Audio
    ohne offenes Segment setzt das ZULETZT vorgemerkte Segment fort, aber
    seit T-1014c nur, solange es GEPARKT ist - also solange der Empfaenger
    nachweislich noch auf dem Kanal steht (kein Zyklusende, kein Item auf
    einem anderen Kanal seit dem Vormerken). Sonst stammt der Ton von
    woanders: MSC- und AUD-Pfad sind getrennte UDP-Stroeme mit eigener
    Latenz, der Einschaltknall des naechsten Kanals trifft vor dessen Item
    ein. Ein Item auf demselben Kanal setzt immer fort - beides schreibt
    in DIESELBE Datei weiter. duration_s eines zusammengesetzten Segments
    ist die Spanne vom ersten bis zum letzten Audio-Datagramm ueber ALLE
    Teile; sie kann die reine Abspieldauer der WAV um die stillen Luecken
    dazwischen uebersteigen. Vorgemerkte Segmente erscheinen NICHT in
    results() - Heartbeat-Zwischenstaende zeigen sie erst nach dem
    Fensterende.

    open_sink/close_sink binden die eigentliche Dateischreibung ein (Muster
    Dependency Injection, damit dieses Modul selbst KEINE Datei-/
    Geraeteabhaengigkeit hat und per Selbsttest mit Fake-Sinks pruefbar
    bleibt - siehe MscanJobRecorder fuer die echte Anbindung):
      open_sink(channel, t_start, level_dbuv, cycle) -> sink mit write(bytes)
      close_sink(sink, keep: bool) -> None (keep False = Datei verwerfen)

    Bewusste Abweichung von der im Auftrag genannten Parameterreihenfolge:
    ein Default-Argument (gap_s) kann in Python nicht VOR den
    Pflichtargumenten open_sink/close_sink stehen - open_sink/close_sink
    wurden deshalb vor gap_s einsortiert (funktional identisch)."""

    def __init__(self, params: dict, channel_lookup: dict, min_recording_s: float,
                 open_sink, close_sink, gap_s: float = DEFAULT_GAP_S,
                 t0_wall: float = 0.0, t0_mono: float = 0.0) -> None:
        self.params = params
        self.channel_lookup = channel_lookup
        self.min_recording_s = min_recording_s
        self.open_sink = open_sink
        self.close_sink = close_sink
        self.gap_s = gap_s
        # T-1018: gap_s bleibt die Rueckfall-Basis fuer den Fall hold_s = 0;
        # wirksam ist die groessere der beiden Schwellen. Getrennt gehalten,
        # damit der uebergebene Parameter seine Bedeutung behaelt und in
        # Selbsttests unveraendert verwendet werden kann.
        self.gap_effective_s = max(gap_s, (self.params.get("hold_s") or 0.0) + GAP_EXTRA_S)
        # T-1017: Vorlauf-Erkennung, im Betrieb die Modulkonstanten. Als
        # Attribute gehalten, damit Selbsttests sie auf 0 setzen koennen - ihre
        # Datagramme sind 2 Byte gross und wuerden vom Knall-Ueberspringen
        # (2240 Byte) vollstaendig aufgezehrt. Beide 0 schaltet die Erkennung
        # ab (siehe _trim_lead_in).
        self.lead_in_silence_peak = LEAD_IN_SILENCE_PEAK
        self.plop_skip_bytes = PLOP_SKIP_BYTES
        # Nachtrag 4 (T-1005 B): t0_wall (time.time()) und t0_mono
        # (time.monotonic()), BEIDE im selben Moment erfasst - der
        # Segmenter kennt sonst nur Monotonic-Zeit (t-Parameter der
        # on_*-Methoden) und kann daraus ohne diesen Referenzpunkt keine
        # Wanduhrzeit fuer recordings[].start_at bilden (siehe
        # _finalize()).
        self.t0_wall = t0_wall
        self.t0_mono = t0_mono

        # T-1014a: Fortsetzungsfenster aus hold_s der Laufparameter. params
        # traegt im Selbsttest nur {"mode": ...}, deshalb .get() mit 0.0.
        hold_s = self.params.get("hold_s") or 0.0
        self.resume_window_s = max(RESUME_WINDOW_MIN_S, hold_s + RESUME_WINDOW_EXTRA_S)

        self._current: dict | None = None
        # vorgemerkte Segmente je Frequenz (Datei bleibt offen) und die
        # Frequenz des zuletzt vorgemerkten - nur dieses eine setzt Audio
        # ohne Item fort.
        self._pending: dict = {}
        self._last_pending_freq = None
        self._audio_resumed = 0
        self._finished_records: list[dict] = []
        self._cycles = 0
        self._audio_orphan = 0
        self._audio_orphan_pre = 0
        self._audio_orphan_mid = 0
        self._audio_orphan_post = 0
        self._item_seen = False
        self._closed = False
        self._unknown_freq = 0
        self._discarded_short = 0
        self.finished = False

    # -- Ereignisse -------------------------------------------------------

    def on_item(self, t: float, freq_hz: int, level_dbuv: float | None, cycle: int | None) -> None:
        """Ein MScan-Item (Squelch-Oeffnung) auf freq_hz. Gleiche Frequenz
        wie das offene Segment -> nur Pegel-Maximum nachfuehren, KEIN neues
        Segment. Andere Frequenz -> laufendes Segment vormerken. Gibt es
        fuer freq_hz ein vorgemerktes Segment im Fortsetzungsfenster, wird
        DIESES fortgesetzt (dieselbe Datei, T-1014a - der naechste Rundlauf
        trifft denselben Kanal waehrend derselben Aussendung); sonst wird
        ein neues Segment geoeffnet."""
        self._item_seen = True
        self._expire_pending(t)
        if self._current is not None and self._current["freq_hz"] == freq_hz:
            self._note_level(self._current, level_dbuv)
            return
        if self._current is not None:
            self._provisional_close(t)

        # T-1014c: Ein Item auf einem anderen Kanal belegt, dass der
        # Empfaenger dort steht. Vor dem Nachschlagen aufheben - die
        # item-basierte Fortsetzung unten braucht den Parkstatus nicht, sie
        # ist durch die genannte Frequenz beweisbar sicher.
        self._unpark_all()
        vorgemerkt = self._pending.pop(freq_hz, None)
        if vorgemerkt is not None:
            if self._last_pending_freq == freq_hz:
                self._last_pending_freq = None
            self._current = vorgemerkt
            self._note_level(vorgemerkt, level_dbuv)
            self._audio_resumed += 1
            return

        channel = self.channel_lookup.get(freq_hz)
        if channel is None:
            self._unknown_freq += 1
            mhz = "?" if freq_hz is None else f"{freq_hz / 1e6:.4f}"
            channel = {
                "id": None, "mem": None, "label": f"unknown {mhz} MHz",
                "freq_hz": freq_hz, "demod": None, "bw_hz": None,
                "squelch_dbuv": None, "service": None, "file": None, "tags": None,
            }

        sink = self.open_sink(channel, t, level_dbuv, cycle)
        if sink is None:
            return   # Sink-Aufbau fehlgeschlagen (siehe MscanJobRecorder) - kein Segment offen

        self._current = {
            "channel": channel, "freq_hz": freq_hz, "t_start": t,
            "t_first_audio": None, "t_last_audio": None,
            "level_max_dbuv": level_dbuv, "device_cycle": cycle, "bytes": 0, "sink": sink,
            # T-1017: Vorlauf-Zustand. "silence" = warte auf das erste laute
            # Datagramm, "skip" = verwerfe noch lead_skip_left Bytes Knall,
            # "done" = normal schreiben. Wird nach jeder Stille > PLOP_GAP_S
            # erneut scharfgeschaltet (siehe _arm_lead_in()).
            "lead_state": "silence", "lead_skip_left": 0, "lead_dropped": 0,
            # T-1017: Zeitpunkt des zuletzt EMPFANGENEN Audio-Datagramms,
            # unabhaengig davon, ob es gebucht wurde. t_last_audio taugt als
            # Gap-Referenz nicht: es steht waehrend des Ueberspringens still,
            # wodurch die gemessene Luecke weiter waechst und die Erkennung
            # sich endlos neu scharfschaltet.
            "t_last_seen": None,
        }

    def on_audio(self, t: float, pcm: bytes) -> None:
        """Audio-Datagramm. Ohne offenes Segment wird zuerst versucht, das
        zuletzt vorgemerkte Segment fortzusetzen (T-1014a: Ton in der
        Hold-Zeit kommt OHNE begleitendes Item). Erst wenn das nicht
        moeglich ist, zaehlt audio_orphan."""
        if self._current is None:
            self._expire_pending(t)
            self._resume_last_pending()
        # T-1017: Stille im laufenden Segment bedeutet, dass der Squelch
        # zugefallen ist; das naechste Audio bringt einen neuen
        # Einschaltknall mit. Ausloeser ist bewusst die Stille und nicht die
        # Fortsetzung - seit T-1018 bleibt das Segment bei Sprechpausen offen,
        # eine Fortsetzung findet dort gar nicht statt.
        if self._current is not None:
            letztes = self._current.get("t_last_seen")
            if letztes is not None and (t - letztes) > PLOP_GAP_S:
                self._arm_lead_in(self._current)
            self._current["t_last_seen"] = t
        if self._current is None:
            self._audio_orphan += 1
            if self._closed:
                self._audio_orphan_post += 1
            elif not self._item_seen:
                self._audio_orphan_pre += 1
            else:
                self._audio_orphan_mid += 1
            return
        # T-1017: Vorlauf abschneiden, BEVOR irgendetwas gebucht wird. Ein
        # vollstaendig verworfenes Datagramm (Stille oder Knall) darf weder
        # t_first_audio setzen noch bytes hochzaehlen: t_first_audio ist der
        # erste BEHALTENE Abtastwert, sonst waere duration_s um den Vorlauf zu
        # lang und die Tonbilanz (T-1014b) schief.
        pcm = self._trim_lead_in(self._current, pcm)
        if not pcm:
            return
        if self._current["t_first_audio"] is None:
            self._current["t_first_audio"] = t
        self._current["t_last_audio"] = t
        self._current["bytes"] += len(pcm)
        self._current["sink"].write(pcm)

    def on_audio_marker(self, t: float) -> None:
        """28-Byte-Audio-Datagramm (nur Header, kein Nutzsignal) - das
        empirische Segmentende-Signal (siehe docs/EB200_Geraeteprofil.md
        Abschnitt MSCAN). Seit T-1014a wird das Segment dadurch nur
        VORGEMERKT: der Marker folgt ~Hold-Zeit nach dem letzten
        Nutzdaten-Datagramm und trifft damit auch Sprechpausen mitten in
        einer Aussendung."""
        self._expire_pending(t)
        if self._current is not None:
            self._provisional_close(t)

    def on_cycle_end(self, t: float, cycle: int | None) -> None:
        """Zyklusende-Marker-Item (freq_hz == 0, Rohpegel >= 2000). Merkt
        ein offenes Segment vor (T-1014a) und zaehlt einen Zyklus. Im Modus
        inventur endet der Lauf damit (finished = True, siehe
        mscan_job.MODES) - dann kann nichts mehr fortsetzen, alle
        vorgemerkten Segmente werden sofort abgeschlossen, und zwar VOR dem
        Hochzaehlen: sie gehoeren zum abgelaufenen Zyklus. T-1019: Das gilt
        nur fuer stop_after "cycle" - eine Inventur mit stop_after "runtime"
        laeuft wie die Ueberwachung weiter, bis Laufende/Stopp sie
        beendet."""
        self._expire_pending(t)
        if self._current is not None:
            self._provisional_close(t)
        # T-1014c: Ein Zyklusende belegt, dass der Empfaenger einen vollen
        # Rundlauf gemacht hat - er steht auf keinem vorgemerkten Kanal mehr.
        self._unpark_all()
        if (self.params.get("mode") == "inventur"
                and self.params.get("stop_after", "cycle") == "cycle"):
            self.finished = True
            self._finalize_all()
        self._cycles += 1

    def tick(self, t: float) -> None:
        """Zeitgesteuerte Pruefungen: (1) abgelaufene Fortsetzungsfenster
        endgueltig schliessen, (2) Rueckfall-Lueckenregel - eine Luecke
        seit dem letzten Audio-Datagramm groesser als gap_effective_s merkt das
        offene Segment vor (falls schon Audio floss; ohne Audio ist eine
        Luecke nicht messbar). Die Lueckenregel schliesst seit T-1014a NUR
        VORLAEUFIG - gap_s (0,5 s) ist kuerzer als das Fortsetzungsfenster,
        ein endgueltiger Schluss wuerde die Hold-Zeit-Fortsetzung
        verhindern."""
        self._expire_pending(t)
        if self._current is None:
            return
        letztes_audio = self._current["t_last_audio"]
        if letztes_audio is not None and (t - letztes_audio) > self.gap_effective_s:
            self._provisional_close(t)

    def close(self) -> None:
        """Schliesst das offene UND alle vorgemerkten Segmente endgueltig
        (Laufende, siehe MscanJobRecorder.close())."""
        self._closed = True
        if self._current is not None:
            seg = self._current
            self._current = None
            self._finalize(seg)
        self._finalize_all()

    # -- Intern -------------------------------------------------------------

    def _arm_lead_in(self, seg: dict) -> None:
        """T-1017: Vorlauf-Erkennung scharfschalten - am Segmentanfang und
        nach jeder Stille > PLOP_GAP_S. Danach werden erst stille Datagramme
        verworfen, dann PLOP_SKIP_BYTES Einschaltknall."""
        seg["lead_state"] = "silence"
        seg["lead_skip_left"] = 0
        seg["lead_dropped"] = 0

    def _trim_lead_in(self, seg: dict, pcm: bytes) -> bytes:
        """T-1017: Liefert den zu schreibenden Teil eines Audio-Datagramms.
        Stufe 1 verwirft stille Datagramme (Spitzenwert < LEAD_IN_SILENCE_PEAK),
        Stufe 2 ab dem ersten lauten Datagramm PLOP_SKIP_BYTES byte-genau.
        LEAD_IN_MAX_BYTES begrenzt beides, damit ein Segment aus reiner Stille
        oder eine zu hoch gesetzte Schwelle nicht endlos verwirft.
        Die Abtastwerte sind big-endian 16 Bit (Annex F, Audio-Modus 6)."""
        if self.plop_skip_bytes <= 0 and self.lead_in_silence_peak <= 0:
            seg["lead_state"] = "done"     # Erkennung abgeschaltet (Selbsttests)
            return pcm
        zustand = seg.get("lead_state", "done")
        if zustand == "done":
            return pcm

        if seg["lead_dropped"] >= LEAD_IN_MAX_BYTES:
            seg["lead_state"] = "done"
            return pcm

        if zustand == "silence":
            gerade = len(pcm) - (len(pcm) % 2)
            spitze = 0
            if gerade:
                werte = np.frombuffer(pcm[:gerade], dtype=">i2")
                spitze = int(np.abs(werte.astype(">i4")).max())
            if spitze < self.lead_in_silence_peak:
                seg["lead_dropped"] += len(pcm)
                return b""
            seg["lead_state"] = "skip"
            seg["lead_skip_left"] = self.plop_skip_bytes

        weg = min(seg["lead_skip_left"], len(pcm))
        seg["lead_skip_left"] -= weg
        seg["lead_dropped"] += weg
        if seg["lead_skip_left"] <= 0:
            seg["lead_state"] = "done"
        return pcm[weg:]

    def _note_level(self, seg: dict, level_dbuv) -> None:
        """Pegel-Maximum eines Segments nachfuehren (mehrfach genutzt,
        seit T-1014a auch beim Fortsetzen)."""
        if level_dbuv is None:
            return
        bisher = seg["level_max_dbuv"]
        seg["level_max_dbuv"] = level_dbuv if bisher is None else max(bisher, level_dbuv)

    def _provisional_close(self, t: float) -> None:
        """Segment vormerken statt schliessen (T-1014a): die Datei bleibt
        offen, bis _expire_pending() das Fortsetzungsfenster ablaufen sieht
        oder Audio/ein Item auf demselben Kanal es fortsetzt. MAX_PENDING
        begrenzt die Zahl gleichzeitig offener Dateien - laeuft sie ueber,
        wird das aelteste vorgemerkte Segment abgeschlossen."""
        seg = self._current
        self._current = None
        seg["t_pending"] = t
        # T-1014c: "geparkt" heisst - der Empfaenger steht nachweislich noch
        # auf diesem Kanal. Marker und Lueckenregel aendern daran nichts (die
        # Hold-Zeit laeuft), ein Zyklusende oder ein Item auf einem anderen
        # Kanal schon: dann zieht er weiter, und ankommender Ton gehoert
        # woanders hin. Nur geparkte Segmente setzt Audio OHNE Item fort.
        seg["parked"] = True
        alt = self._pending.pop(seg["freq_hz"], None)
        if alt is not None and alt is not seg:
            self._finalize(alt)
        self._pending[seg["freq_hz"]] = seg
        self._last_pending_freq = seg["freq_hz"]
        while len(self._pending) > MAX_PENDING:
            aeltestes = min(self._pending, key=lambda f: self._pending[f]["t_pending"])
            self._finalize(self._pending.pop(aeltestes))
            if self._last_pending_freq == aeltestes:
                self._last_pending_freq = None

    def _resume_last_pending(self) -> None:
        """Setzt das zuletzt vorgemerkte Segment fort (Aufrufer: on_audio,
        nachdem _expire_pending() gelaufen ist)."""
        freq = self._last_pending_freq
        if freq is None:
            return
        seg = self._pending.pop(freq, None)
        self._last_pending_freq = None
        if seg is None:
            return
        if not seg.get("parked"):
            # T-1014c: Empfaenger ist weitergezogen - der Ton stammt von einem
            # anderen Kanal (typisch: der Einschaltknall des naechsten, dessen
            # Item ueber den getrennten MSC-Pfad noch unterwegs ist). Zurueck
            # in die Vormerkung, der Aufrufer zaehlt audio_orphan.
            self._pending[seg["freq_hz"]] = seg
            return
        self._current = seg
        self._audio_resumed += 1

    def _unpark_all(self) -> None:
        """T-1014c: Der Empfaenger hat den Kanal verlassen (Zyklusende oder
        Item auf einem anderen Kanal). Vorgemerkte Segmente bleiben offen und
        koennen weiterhin per ITEM auf ihrer eigenen Frequenz fortgesetzt
        werden - aber nicht mehr blind durch ankommendes Audio."""
        for seg in self._pending.values():
            seg["parked"] = False
        self._last_pending_freq = None

    def _expire_pending(self, t: float) -> None:
        """Schliesst alle vorgemerkten Segmente endgueltig, deren
        Fortsetzungsfenster abgelaufen ist. Wird zu Beginn JEDES Ereignisses
        aufgerufen, damit der Ablauf nicht von tick() allein abhaengt."""
        abgelaufen = [f for f, seg in self._pending.items()
                      if (t - seg["t_pending"]) > self.resume_window_s]
        for f in abgelaufen:
            self._finalize(self._pending.pop(f))
            if self._last_pending_freq == f:
                self._last_pending_freq = None

    def _finalize_all(self) -> None:
        """Alle vorgemerkten Segmente endgueltig schliessen (Laufende,
        Inventur-Zyklusende)."""
        for f in list(self._pending):
            self._finalize(self._pending.pop(f))
        self._last_pending_freq = None

    def _finalize(self, seg: dict) -> None:
        # duration_s ist die Spanne erstes bis letztes Audio-Datagramm ueber
        # ALLE Teile eines zusammengesetzten Segments (T-1014a) - erst darauf
        # wirkt min_recording_s.
        if seg["t_first_audio"] is None:
            duration_s = 0.0
        else:
            duration_s = seg["t_last_audio"] - seg["t_first_audio"]
        keep = duration_s >= self.min_recording_s
        self.close_sink(seg["sink"], keep)
        if not keep:
            self._discarded_short += 1
            return
        # start_at (Nachtrag 4): Wanduhrzeit des Segmentbeginns aus dem
        # Monotonic-Zeitstempel t_start ueber den einmalig erfassten
        # (t0_wall, t0_mono)-Referenzpunkt zurueckgerechnet.
        start_at = datetime.fromtimestamp(
            self.t0_wall + (seg["t_start"] - self.t0_mono)
        ).astimezone().isoformat(timespec="seconds")
        file_path = getattr(seg["sink"], "path", None)
        self._finished_records.append({
            "channel_id": seg["channel"].get("id"),
            "label": seg["channel"].get("label"),
            "mem": seg["channel"].get("mem"),
            "freq_hz": seg["freq_hz"],
            "t_start": seg["t_start"],
            "start_at": start_at,
            "duration_s": duration_s,
            "level_max_dbuv": seg["level_max_dbuv"],
            # Eigener Zaehler (self._cycles, siehe on_cycle_end) ist die
            # Quelle fuer "cycle" - der Geraete-Header-Wert (Annex F
            # cycleCount, hier device_cycle) blieb im Hardware-Lauf ab
            # einem Punkt konstant bei 1001 stehen (empirisch beobachtet,
            # Handbuch/Annex F belegen keinen Zaehlmechanismus) und taugt
            # deshalb nicht als verlaessliche Zyklusnummer.
            "cycle": self._cycles,
            "device_cycle": seg["device_cycle"],
            "bytes": seg["bytes"],
            # nur der Dateiname (os.path.basename) - relativ zu job_dir
            # wie manifest.files.
            "file": os.path.basename(file_path) if file_path else None,
        })

    # -- Ergebnis -------------------------------------------------------------

    def results(self) -> dict:
        """Aggregiert die behaltenen Aufnahmen je Kanal (sortiert nach
        Treffern absteigend). Nur Kanaele mit mindestens einer behaltenen
        Aufnahme erscheinen in 'channels' (siehe Auftrag)."""
        per_channel: dict = {}
        for rec in self._finished_records:
            key = rec["channel_id"] if rec["channel_id"] is not None else f"unbekannt:{rec['freq_hz']}"
            entry = per_channel.setdefault(key, {
                "channel_id": rec["channel_id"], "label": rec["label"], "freq_hz": rec["freq_hz"],
                "mem": rec["mem"], "hits": 0, "total_s": 0.0, "level_max_dbuv": None,
                "recordings": [],
            })
            entry["hits"] += 1
            entry["total_s"] += rec["duration_s"]
            if rec["level_max_dbuv"] is not None:
                bisher = entry["level_max_dbuv"]
                entry["level_max_dbuv"] = rec["level_max_dbuv"] if bisher is None else max(bisher, rec["level_max_dbuv"])
            entry["recordings"].append({
                "file": rec["file"], "start_at": rec["start_at"], "t_start": rec["t_start"],
                "duration_s": rec["duration_s"], "level_max_dbuv": rec["level_max_dbuv"],
                "cycle": rec["cycle"], "device_cycle": rec["device_cycle"], "bytes": rec["bytes"],
            })

        channels = sorted(per_channel.values(), key=lambda e: e["hits"], reverse=True)
        return {
            "channels": channels,
            "cycles": self._cycles,
            "audio_orphan": self._audio_orphan,
            "audio_orphan_pre": self._audio_orphan_pre,
            "audio_orphan_mid": self._audio_orphan_mid,
            "audio_orphan_post": self._audio_orphan_post,
            "audio_resumed": self._audio_resumed,
            "unknown_freq": self._unknown_freq,
            "discarded_short": self._discarded_short,
        }


# ---------------------------------------------------------------------------
# Teil E - Recorder (Muster IfpanJobRecorder, siehe eb200_core/job_runner.py)
# ---------------------------------------------------------------------------

class _WavSink:
    """Duenne Bruecke zwischen MscanSegmenter (erwartet sink.write(bytes))
    und WavAudioWriter (bietet write_frames() fuer big-endian PCM). Haelt
    zusaetzlich path fest, das MscanSegmenter._finalize() fuer den
    'file'-Eintrag in results() ausliest."""

    def __init__(self, writer: WavAudioWriter) -> None:
        self._writer = writer
        self.path = writer.path

    def write(self, pcm: bytes) -> None:
        self._writer.write_frames(pcm)

    def close(self) -> None:
        self._writer.close()


class MscanJobRecorder:
    """Recorder fuer einen aktiven MSCAN-Job (Muster job_runner.
    IfpanJobRecorder): nimmt ueber record_item/record_audio/
    record_audio_marker/record_cycle_end/tick Ereignisse entgegen, reicht
    sie an einen MscanSegmenter durch und schreibt behaltene Segmente als
    WAV-Dateien (WavAudioWriter) im Job-Verzeichnis. job_store wird nur
    fuer heartbeat() gebraucht (Duck-Typing, wie beim Vorbild)."""

    def __init__(self, job_store, manifest: dict, audio_remote_mode: int,
                 t0_wall: float, t0_mono: float) -> None:
        self.job_store = job_store
        self.job_id = manifest["job_id"]
        self.job_dir = manifest["job_dir"]
        self.audio_remote_mode = audio_remote_mode
        self.run_params = manifest.get("params", {})
        self.heartbeat_s = 30.0

        channel_lookup = {ch["freq_hz"]: ch for ch in self.run_params.get("channels", [])}
        min_recording_s = self.run_params.get("min_recording_s", 1.0)
        self.segmenter = MscanSegmenter(
            self.run_params, channel_lookup, min_recording_s,
            self._open_sink, self._close_sink, t0_wall=t0_wall, t0_mono=t0_mono,
        )

        self._file_seq = 0
        self._items = 0
        self._segments = 0
        self._bytes = 0
        self._files: list[str] = []
        self._last_heartbeat_t: float | None = None

        self.failed: str | None = None
        self.finished = False
        self.extra_files: list = []
        self._closed = False

        # T-1005 Session B: vom Controller-Loop gesetzt (_run_mscan_loop),
        # current_stats() gibt beide unveraendert weiter - fuer Banner/
        # job_active() (Fortschritt waehrend "programmieren").
        self.phase: str = "programmieren"
        self.programmed: int = 0

    # -- Sink-Anbindung (an MscanSegmenter durchgereicht) ------------------

    def _open_sink(self, channel: dict, t_start: float, level_dbuv, cycle):
        self._file_seq += 1
        mem = channel.get("mem")
        mem_txt = f"{mem:04d}" if mem is not None else "unbk"
        ts = datetime.now().strftime("%H%M%S")
        filename = f"mscan_{self.job_id}_{mem_txt}_{ts}_{self._file_seq:03d}.wav"
        path = os.path.join(self.job_dir, filename)
        try:
            writer = WavAudioWriter(path, self.audio_remote_mode, meta={
                "freq_hz": channel.get("freq_hz"), "mode": channel.get("demod"),
                "bw_hz": channel.get("bw_hz"), "channel_id": channel.get("id"),
                "label": channel.get("label"), "cycle": cycle,
            })
        except OSError as exc:
            self.failed = f"cannot create WAV file: {exc!r}"
            return None
        self._segments += 1
        return _WavSink(writer)

    def _close_sink(self, sink, keep: bool) -> None:
        if sink is None:
            return
        sink.close()
        if keep:
            self._files.append(os.path.basename(sink.path))
            return
        for suffix in ("", ".json"):
            p = sink.path + suffix
            if os.path.isfile(p):
                os.remove(p)

    # -- Ereignisse (reine Durchreichung an den Segmenter) -----------------

    def mark_scan_start(self, t: float) -> None:
        """T-1019h: ohne Wirkung. Die Tonaufnahme braucht keinen eigenen
        Nullpunkt - ihre Zeitstempel sind Wanduhrzeiten je Segment. Die
        Methode existiert nur, damit der Controller-Loop beide Recorder
        gleich behandeln kann."""
        return

    def record_item(self, t: float, freq_hz: int, level_dbuv, cycle) -> None:
        if self._closed or self.failed is not None:
            return
        try:
            self._items += 1
            self.segmenter.on_item(t, freq_hz, level_dbuv, cycle)
        except Exception as exc:   # noqa: BLE001 - Aufzeichnung darf nie abstuerzen
            self.failed = f"unexpected error in record_item: {exc!r}"
        self._maybe_heartbeat(t)

    def record_audio(self, t: float, pcm: bytes) -> None:
        if self._closed or self.failed is not None:
            return
        try:
            self.segmenter.on_audio(t, pcm)
            self._bytes += len(pcm)
        except Exception as exc:   # noqa: BLE001
            self.failed = f"unexpected error in record_audio: {exc!r}"
        self._maybe_heartbeat(t)

    def record_audio_marker(self, t: float) -> None:
        if self._closed or self.failed is not None:
            return
        try:
            self.segmenter.on_audio_marker(t)
        except Exception as exc:   # noqa: BLE001
            self.failed = f"unexpected error in record_audio_marker: {exc!r}"
        self._maybe_heartbeat(t)

    def record_cycle_end(self, t: float, cycle) -> None:
        if self._closed or self.failed is not None:
            return
        try:
            self.segmenter.on_cycle_end(t, cycle)
            if self.segmenter.finished:
                self.finished = True
        except Exception as exc:   # noqa: BLE001
            self.failed = f"unexpected error in record_cycle_end: {exc!r}"
        self._maybe_heartbeat(t)

    def tick(self, t: float) -> None:
        if self._closed or self.failed is not None:
            return
        try:
            self.segmenter.tick(t)
        except Exception as exc:   # noqa: BLE001
            self.failed = f"unexpected error in tick: {exc!r}"
        self._maybe_heartbeat(t)

    # -- Statistik/Heartbeat/Abschluss --------------------------------------

    def current_stats(self) -> dict:
        results = self.segmenter.results()
        recordings = sum(len(ch["recordings"]) for ch in results["channels"])
        return {
            "phase": self.phase, "programmed": self.programmed,
            "cycles": results["cycles"], "items": self._items, "segments": self._segments,
            "recordings": recordings, "bytes": self._bytes,
            "active_channels": len(results["channels"]),
            # fuer Laufliste/Banner (T-1005 Session B Nachtrag 4): Audio
            # ohne offenes Segment, unbekannte Frequenz, zu kurz verworfen.
            "audio_orphan": results["audio_orphan"],
            "audio_orphan_pre": results["audio_orphan_pre"],
            "audio_orphan_mid": results["audio_orphan_mid"],
            "audio_orphan_post": results["audio_orphan_post"],
            "unknown_freq": results["unknown_freq"],
            "discarded_short": results["discarded_short"],
            "files": list(self._files) + list(self.extra_files),
        }

    def _maybe_heartbeat(self, t: float) -> None:
        if self._last_heartbeat_t is not None and (t - self._last_heartbeat_t) < self.heartbeat_s:
            return
        self._last_heartbeat_t = t
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=stats, files=list(stats["files"]))

    def close(self) -> dict:
        """Idempotent: schliesst ein evtl. offenes Segment, uebergibt
        results() an job_store.heartbeat(results=...) (oberste Manifest-
        Ebene manifest["results"], siehe eb200_core/job_store.py) und
        schreibt ein letztes Heartbeat."""
        if self._closed:
            return self.current_stats()
        self._closed = True
        self.segmenter.close()
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=stats, files=list(stats["files"]),
                                  results=self.segmenter.results())
        return stats


# ---------------------------------------------------------------------------
# Teil E2 - Pegel-Inventur ohne Ton (T-1019)
# ---------------------------------------------------------------------------

# Obergrenze der Zyklus-Aufschluesselung je Kanal (results()[...]
# ["per_cycle"]): eine Inventur mit stop_after "runtime" laeuft ueber
# beliebig viele Durchgaenge, und jedes Tripel landet im Manifest. Ab dieser
# Zahl unterschiedlicher Durchgaenge je Kanal wird nicht mehr
# aufgeschluesselt (hits/cycles_active zaehlen weiter), und die Zeile traegt
# per_cycle_truncated True.
MAX_PER_CYCLE = 500


class MscanLevelInventory:
    """Pegel-Inventur (T-1019): zaehlt je Kanal die Squelch-Oeffnungen,
    ohne eine einzige Datei anzulegen. Das Geraet liefert je Oeffnung EIN
    MScan-Item mit Frequenz und Pegel (docs/EB200_Geraeteprofil.md,
    Abschnitt MSCAN: ohne Signal kommt kein Item) - genau diese Items sind
    hier die Aktivitaetsnachweise. params["level_active_dbuv"] hebt die
    Schwelle ueber die Rauschsperre des Speicherplatzes hinaus an: ein Item
    darunter zaehlt als below_threshold, nicht als Treffer.

    Bewusst KEIN Gegenstueck zu MscanSegmenter: ohne Ton gibt es keine
    Segmente, keine offenen Dateien, keine Fortsetzungsfenster und keine
    Lueckenregel - deshalb auch keine zeitgesteuerte Logik. tick() bleibt
    trotzdem vorhanden, damit der Controller-Loop beide Auswertungen gleich
    bedienen kann.

    Durchgangszaehlung: self._cycles zaehlt die Zyklusende-Marker, die das
    Geraet auch bei COUNt INF je Runde schickt. Ein Treffer wird dem
    Durchgang zugeordnet, der beim Eintreffen des Items laeuft - die
    Zaehlung beginnt also bei 0.

    T-1019f: Je Durchgang wird neben der Trefferzahl auch das
    Pegelmaximum gefuehrt - results() liefert deshalb
    [Durchgang, Treffer, max_dbuv]-TRIPEL statt der frueheren Paare. Das
    Maximum bezieht sich nur auf die Treffer DIESES Durchgangs; Oeffnungen
    unter der Schwelle zaehlen wie bisher ausschliesslich in
    below_threshold. Es ist None, wenn das Geraet zu allen Treffern eines
    Durchgangs keinen Pegel mitgeschickt hat (in der Praxis nicht
    beobachtet, aber level_dbuv ist laut parse_mscan_items() optional).
    Laeufe von VOR T-1019f tragen weiterhin Paare - Konsumenten muessen
    beide Laengen vertragen und bei Laenge 2 auf die Pegelfarbe
    verzichten."""

    def __init__(self, params: dict, channel_lookup: dict,
                 t0_wall: float = 0.0, t0_mono: float = 0.0) -> None:
        self.params = params
        self.channel_lookup = channel_lookup
        schwelle = params.get("level_active_dbuv")
        self.level_active_dbuv = None if schwelle is None else float(schwelle)
        # Muster MscanSegmenter: Monotonic-Zeitstempel der on_*-Methoden ueber
        # den einmalig erfassten Referenzpunkt in Wanduhrzeit umrechnen.
        self.t0_wall = t0_wall
        self.t0_mono = t0_mono

        self._per_channel: dict = {}
        self._cycles = 0
        self._items = 0
        self._below = 0
        self._unknown_freq = 0
        self._audio_ignored = 0
        # T-1019f Stufe 3a: Sekunden-Offset seit Laufbeginn je abgeschlossenem
        # Durchgang. Index i = Ende des Durchgangs i, also genau die Achse,
        # die eine Verlaufsdarstellung braucht. Ohne diese Liste bliebe nur
        # die Naeherung "Laufdauer durch Zyklenzahl", und die truegt: ein
        # Durchgang ueber viele belegte Kanaele dauert laenger als einer ueber
        # stille (Dwell und Hold je Treffer, siehe docs/EB200_Geraeteprofil.md
        # Abschnitt MSCAN).
        self._cycle_ends_s: list = []
        # T-1019h: Nullpunkt der Zeitachse. Der Recorder entsteht VOR dem
        # Programmieren der Speicherplaetze (bei 300 Kanaelen rund 75-85 s,
        # siehe docs/EB200_Geraeteprofil.md: 210-280 ms je Platz) - waere
        # t0_mono der Nullpunkt, erschiene der erste Durchgang um diese
        # Spanne zu lang. Der Aufrufer meldet den Scan-Start ueber
        # mark_scan_start(); bis dahin bleibt t0_mono die Notfall-Basis.
        self._scan_start_mono: float | None = None
        self.finished = False

    # -- Intern -------------------------------------------------------------

    def _wall_iso(self, t: float) -> str:
        return datetime.fromtimestamp(
            self.t0_wall + (t - self.t0_mono)
        ).astimezone().isoformat(timespec="seconds")

    def _entry(self, freq_hz) -> dict:
        """Zeile zu freq_hz, bei Bedarf angelegt. Eine Frequenz ausserhalb
        des Kanal-Snapshots bekommt eine Ersatzbeschriftung (Muster
        MscanSegmenter.on_item) und zaehlt einmalig unknown_freq hoch."""
        eintrag = self._per_channel.get(freq_hz)
        if eintrag is not None:
            return eintrag
        channel = self.channel_lookup.get(freq_hz)
        if channel is None:
            self._unknown_freq += 1
            mhz = "?" if freq_hz is None else f"{freq_hz / 1e6:.4f}"
            channel = {"id": None, "mem": None, "label": f"unknown {mhz} MHz"}
        eintrag = {
            "channel_id": channel.get("id"), "label": channel.get("label"),
            "mem": channel.get("mem"), "freq_hz": freq_hz,
            "hits": 0, "below_threshold": 0, "cycles_active": 0,
            "level_max_dbuv": None, "level_last_dbuv": None,
            "first_at": None, "last_at": None,
            "_cycle_counts": {}, "_last_cycle": None,
        }
        self._per_channel[freq_hz] = eintrag
        return eintrag

    def _row(self, eintrag: dict) -> dict:
        """Ergebniszeile aus einem internen Eintrag: interne Felder (fuehrender
        Unterstrich) fallen weg, per_cycle wird zur sortierten Liste aus
        [Durchgang, Treffer, max_dbuv]-Tripeln (JSON-stabil, anders als
        int-Schluessel in einem Objekt; T-1019f - vorher Paare ohne
        Pegel)."""
        zaehler = eintrag["_cycle_counts"]
        zeile = {k: v for k, v in eintrag.items() if not k.startswith("_")}
        zeile["active"] = eintrag["hits"] > 0
        zeile["per_cycle"] = [[c, zaehler[c][0], zaehler[c][1]] for c in sorted(zaehler)]
        zeile["per_cycle_truncated"] = len(zaehler) >= MAX_PER_CYCLE
        return zeile

    # -- Ereignisse ---------------------------------------------------------

    def on_item(self, t: float, freq_hz: int, level_dbuv: float | None,
                cycle: int | None = None) -> None:
        """Ein MScan-Item auf freq_hz. Oberhalb der Schwelle ein Treffer,
        darunter nur below_threshold. cycle (Geraete-Header) wird bewusst
        ignoriert - er blieb im Hardware-Lauf konstant stehen (siehe
        MscanSegmenter._finalize()); massgeblich ist der eigene Zaehler."""
        self._items += 1
        eintrag = self._entry(freq_hz)
        if (self.level_active_dbuv is not None
                and (level_dbuv is None or level_dbuv < self.level_active_dbuv)):
            eintrag["below_threshold"] += 1
            self._below += 1
            return
        eintrag["hits"] += 1
        if level_dbuv is not None:
            bisher = eintrag["level_max_dbuv"]
            eintrag["level_max_dbuv"] = level_dbuv if bisher is None else max(bisher, level_dbuv)
            eintrag["level_last_dbuv"] = level_dbuv
        zeit = self._wall_iso(t)
        if eintrag["first_at"] is None:
            eintrag["first_at"] = zeit
        eintrag["last_at"] = zeit
        if eintrag["_last_cycle"] != self._cycles:
            eintrag["cycles_active"] += 1
            eintrag["_last_cycle"] = self._cycles
        # T-1019f: je Durchgang [Treffer, Pegelmaximum]. Ein schon
        # angelegter Durchgang waechst immer weiter - MAX_PER_CYCLE
        # begrenzt nur, wie viele VERSCHIEDENE Durchgaenge je Kanal
        # aufgeschluesselt werden.
        zaehler = eintrag["_cycle_counts"]
        vorhanden = zaehler.get(self._cycles)
        if vorhanden is not None:
            vorhanden[0] += 1
            if level_dbuv is not None and (vorhanden[1] is None or level_dbuv > vorhanden[1]):
                vorhanden[1] = level_dbuv
        elif len(zaehler) < MAX_PER_CYCLE:
            zaehler[self._cycles] = [1, level_dbuv]

    def mark_scan_start(self, t: float) -> None:
        """T-1019h: Beginn des eigentlichen Scans (Monotonic-Zeitstempel des
        Aufrufers, gesetzt nach INIT:CONM). Ab hier zaehlt die Zeitachse der
        Durchgaenge; die Spanne davor ist die Programmierdauer und wird in
        results() eigens ausgewiesen. Mehrfachaufrufe sind wirkungslos - der
        erste gilt."""
        if self._scan_start_mono is None:
            self._scan_start_mono = t

    def on_audio(self, t: float, pcm: bytes) -> None:
        """Ohne registrierten AUD-Pfad darf hier nichts ankommen - ein
        Restbestand aus einem vorherigen Lauf wird gezaehlt und verworfen,
        nie geschrieben."""
        self._audio_ignored += 1

    def on_audio_marker(self, t: float) -> None:
        self._audio_ignored += 1

    def on_cycle_end(self, t: float, cycle: int | None = None) -> None:
        """Zyklusende-Marker. Anders als beim Segmenter gibt es nichts
        abzuschliessen - es wird nur der Durchgang hochgezaehlt und bei
        stop_after "cycle" der Lauf beendet. T-1019f Stufe 3a: zuvor wird
        der Sekunden-Offset des ABGESCHLOSSENEN Durchgangs vermerkt (erst
        anhaengen, dann hochzaehlen - sonst zeigte der Eintrag auf den
        naechsten)."""
        # Dieselbe Rechnung wie in _wall_iso(), nur ohne Umweg ueber die
        # Wanduhr: t ist der Monotonic-Zeitstempel des Aufrufers.
        basis = self._scan_start_mono if self._scan_start_mono is not None else self.t0_mono
        self._cycle_ends_s.append(round(t - basis, 2))
        self._cycles += 1
        if (self.params.get("mode") == "inventur"
                and self.params.get("stop_after", "cycle") == "cycle"):
            self.finished = True

    def tick(self, t: float) -> None:
        """Ohne offene Dateien gibt es zeitgesteuert nichts zu tun - die
        Methode existiert nur der einheitlichen Schnittstelle wegen."""
        return

    def close(self) -> None:
        """Gegenstueck zu MscanSegmenter.close() - hier ohne Wirkung."""
        return

    # -- Ergebnis -----------------------------------------------------------

    def results(self) -> dict:
        """ALLE Kanaele des Laufs, aktive wie stille (anders als
        MscanSegmenter.results(), das nur Kanaele mit Aufnahme kennt) - die
        Aussage "Kanal nicht aktiv" ist bei einer Inventur genauso ein
        Ergebnis wie der Treffer. Reihenfolge: erst aktive Kanaele nach
        Treffern absteigend, dann die stillen nach Speicherplatz - so steht
        das Ergebnis oben und die Vollzaehligkeit bleibt trotzdem sichtbar.
        kind == "level" unterscheidet die Auswertung fuer :8060 von der
        Tonaufnahme-Auswertung (dort gibt es kein kind)."""
        zeilen = []
        for freq_hz, ch in self.channel_lookup.items():
            eintrag = self._per_channel.get(freq_hz)
            if eintrag is None:
                zeilen.append({
                    "channel_id": ch.get("id"), "label": ch.get("label"),
                    "mem": ch.get("mem"), "freq_hz": freq_hz,
                    "active": False, "hits": 0, "below_threshold": 0,
                    "cycles_active": 0, "level_max_dbuv": None,
                    "level_last_dbuv": None, "first_at": None, "last_at": None,
                    "per_cycle": [], "per_cycle_truncated": False,
                })
            else:
                zeilen.append(self._row(eintrag))
        for freq_hz, eintrag in self._per_channel.items():
            if freq_hz not in self.channel_lookup:
                zeilen.append(self._row(eintrag))

        zeilen.sort(key=lambda z: (
            0 if z["active"] else 1,
            -z["hits"],
            z["mem"] if z["mem"] is not None else MAX_CHANNELS,
            z["freq_hz"] or 0,
        ))
        aktiv = sum(1 for z in zeilen if z["active"])
        return {
            "kind": "level",
            "channels": zeilen,
            "cycles": self._cycles,
            # T-1019f Stufe 3a: Zeitachse der Durchgaenge. Laeufe von vorher
            # tragen den Schluessel nicht - Konsumenten fallen dann auf die
            # Naeherung ueber die Laufdauer zurueck.
            "cycle_ends_s": list(self._cycle_ends_s),
            # T-1019h: Dauer des Programmierens der Speicherplaetze, also die
            # Spanne zwischen Recorder-Start und Scan-Start. None, wenn der
            # Aufrufer sie nicht gemeldet hat.
            "programming_s": (None if self._scan_start_mono is None
                               else round(self._scan_start_mono - self.t0_mono, 2)),
            "active_channels": aktiv,
            "silent_channels": len(zeilen) - aktiv,
            "items": self._items,
            "below_threshold": self._below,
            "unknown_freq": self._unknown_freq,
            "audio_ignored": self._audio_ignored,
            "level_active_dbuv": self.level_active_dbuv,
        }


class MscanLevelRecorder:
    """Recorder-Gegenstueck zu MscanJobRecorder fuer die Pegel-Inventur
    (record_audio False). Gleiche aeussere Schnittstelle - record_item/
    record_audio/record_audio_marker/record_cycle_end/tick/current_stats/
    close sowie die Attribute failed/finished/extra_files/phase/programmed
    -, damit der Controller-Loop beide ohne Fallunterscheidung bedienen
    kann; die Auswahl trifft make_recorder(). Es entstehen KEINE Dateien:
    das Ergebnis steht ausschliesslich im Manifest (job_store.heartbeat(
    results=...), oberste Ebene manifest["results"] wie bei
    MscanJobRecorder.close())."""

    def __init__(self, job_store, manifest: dict, t0_wall: float, t0_mono: float) -> None:
        self.job_store = job_store
        self.job_id = manifest["job_id"]
        self.job_dir = manifest["job_dir"]
        self.run_params = manifest.get("params", {})
        self.heartbeat_s = 30.0

        channel_lookup = {ch["freq_hz"]: ch for ch in self.run_params.get("channels", [])}
        self.inventory = MscanLevelInventory(self.run_params, channel_lookup,
                                             t0_wall=t0_wall, t0_mono=t0_mono)

        self._items = 0
        self._last_heartbeat_t: float | None = None

        self.failed: str | None = None
        self.finished = False
        self.extra_files: list = []
        self._closed = False

        self.phase: str = "programmieren"
        self.programmed: int = 0

    # -- Ereignisse (reine Durchreichung an die Inventur) ------------------

    def mark_scan_start(self, t: float) -> None:
        """T-1019h: an die Inventur durchgereicht (siehe dort). Beide Recorder
        haben die Methode, damit der Controller-Loop sie ohne
        Fallunterscheidung aufrufen kann."""
        self.inventory.mark_scan_start(t)

    def record_item(self, t: float, freq_hz: int, level_dbuv, cycle) -> None:
        if self._closed or self.failed is not None:
            return
        try:
            self._items += 1
            self.inventory.on_item(t, freq_hz, level_dbuv, cycle)
        except Exception as exc:   # noqa: BLE001 - Aufzeichnung darf nie abstuerzen
            self.failed = f"unexpected error in record_item: {exc!r}"
        self._maybe_heartbeat(t)

    def record_audio(self, t: float, pcm: bytes) -> None:
        if self._closed or self.failed is not None:
            return
        self.inventory.on_audio(t, pcm)

    def record_audio_marker(self, t: float) -> None:
        if self._closed or self.failed is not None:
            return
        self.inventory.on_audio_marker(t)

    def record_cycle_end(self, t: float, cycle) -> None:
        if self._closed or self.failed is not None:
            return
        try:
            self.inventory.on_cycle_end(t, cycle)
            if self.inventory.finished:
                self.finished = True
        except Exception as exc:   # noqa: BLE001
            self.failed = f"unexpected error in record_cycle_end: {exc!r}"
        self._maybe_heartbeat(t)

    def tick(self, t: float) -> None:
        if self._closed or self.failed is not None:
            return
        self._maybe_heartbeat(t)

    # -- Statistik/Heartbeat/Abschluss --------------------------------------

    def current_stats(self) -> dict:
        """Dieselben Schluessel wie MscanJobRecorder.current_stats(), damit
        Banner und Laufliste unveraendert bleiben - die Ton-Zaehler stehen
        fest auf 0, weil es hier keinen Ton gibt. below_threshold kommt als
        einziger Schluessel hinzu (Consumer lesen durchweg mit .get()/|| 0)."""
        results = self.inventory.results()
        return {
            "phase": self.phase, "programmed": self.programmed,
            "cycles": results["cycles"], "items": self._items,
            "segments": 0, "recordings": 0, "bytes": 0,
            "active_channels": results["active_channels"],
            "audio_orphan": 0, "audio_orphan_pre": 0,
            "audio_orphan_mid": 0, "audio_orphan_post": 0,
            "unknown_freq": results["unknown_freq"],
            "discarded_short": 0,
            "below_threshold": results["below_threshold"],
            "files": list(self.extra_files),
        }

    def _maybe_heartbeat(self, t: float) -> None:
        if self._last_heartbeat_t is not None and (t - self._last_heartbeat_t) < self.heartbeat_s:
            return
        self._last_heartbeat_t = t
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=stats, files=list(stats["files"]))

    def close(self) -> dict:
        """Idempotent, Muster MscanJobRecorder.close(): Ergebnis an
        job_store.heartbeat(results=...) uebergeben und ein letztes
        Heartbeat schreiben."""
        if self._closed:
            return self.current_stats()
        self._closed = True
        self.inventory.close()
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=stats, files=list(stats["files"]),
                                  results=self.inventory.results())
        return stats


def make_recorder(job_store, manifest: dict, audio_remote_mode: int,
                  t0_wall: float, t0_mono: float):
    """Waehlt den Recorder anhand von manifest["params"]["record_audio"]:
    False -> MscanLevelRecorder (Pegel-Inventur ohne Ton), sonst der
    bisherige MscanJobRecorder. Einziger Einstiegspunkt fuer den
    Controller-Loop in waterfall.py - die Fallunterscheidung steht damit an
    genau EINER Stelle."""
    params = manifest.get("params", {})
    if params.get("record_audio", True) is False:
        return MscanLevelRecorder(job_store, manifest, t0_wall, t0_mono)
    return MscanJobRecorder(job_store, manifest, audio_remote_mode, t0_wall, t0_mono)


# ---------------------------------------------------------------------------
# Teil F - Selbsttest (python -m eb200_core.mscan_run --selftest)
# ---------------------------------------------------------------------------

def _s1_build_run_params() -> None:
    job_def = {
        "id": "ABCDEF", "title": "Test",
        "params": {"mode": "ueberwachung", "dwell_s": None, "hold_s": 2.0,
                   "squelch_from_memory": True, "min_recording_s": 1.0},
    }
    snapshot = [
        {"id": "C1", "label": "Kanal 1", "freq_hz": 100_000_000, "demod": "FM",
         "bw_hz": 15000, "squelch_dbuv": None, "service": "x", "file": "f.json", "tags": []},
        {"id": "C2", "label": "Kanal 2", "freq_hz": 101_000_000, "demod": "FM",
         "bw_hz": 15000, "squelch_dbuv": 30, "service": "x", "file": "f.json", "tags": []},
        {"id": "C3", "label": "Kanal 3", "freq_hz": 102_000_000, "demod": "AM",
         "bw_hz": 6000, "squelch_dbuv": 10, "service": "x", "file": "f.json", "tags": []},
    ]
    rp = build_run_params(job_def, snapshot)
    assert [c["mem"] for c in rp["channels"]] == [0, 1, 2], rp["channels"]
    assert rp["channels"][0]["squelch_dbuv"] == 20, rp["channels"][0]
    assert rp["channels"][1]["squelch_dbuv"] == 30, rp["channels"][1]
    assert rp["mscan_job_id"] == "ABCDEF" and rp["title"] == "Test", rp

    zu_viel = [dict(snapshot[0]) for _ in range(MAX_CHANNELS + 1)]
    try:
        build_run_params(job_def, zu_viel)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet: mehr als MAX_CHANNELS Kanaele")

    try:
        build_run_params(job_def, [])
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet: leere Kanalliste")

    assert run_label(job_def) == "MSCAN ABCDEF Test", run_label(job_def)
    print("S1 ok")


def _s2_memory_commands() -> None:
    ch = {"freq_hz": 98_500_000, "squelch_dbuv": 34, "demod": "FM", "bw_hz": 100_000}
    cmd = memory_write_command(5, ch, antenna=1)
    assert cmd == "MEMory:CONTents MEM5,98500000,34,FM,100000,(@1),0,1,1,0,1", cmd

    rb = parse_memory_readback("98500000,34,FM,100000,#14(@1),0,1,1,0,1")
    assert rb["freq_hz"] == 98_500_000, rb
    assert rb["thr"] == 34, rb
    assert rb["demod"] == "FM", rb
    assert rb["bw_hz"] == 100_000, rb
    assert rb["antenna"] == 1, rb
    assert rb["act"] == 1, rb

    assert memory_matches(ch, rb) == [], memory_matches(ch, rb)
    abweichend = dict(ch, freq_hz=99_000_000)
    diffs = memory_matches(abweichend, rb)
    assert diffs and "Frequenz" in diffs[0], diffs

    try:
        parse_memory_readback("1,2,FM")
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet: zu wenige Felder")
    print("S2 ok")


def _s3_scan_setup_commands() -> None:
    params_inv = {"mode": "inventur", "dwell_s": 3.0, "hold_s": 0.0, "squelch_from_memory": True}
    cmds = scan_setup_commands(params_inv, "10.0.0.5", 17300, 17301, 6)
    assert "MSCan:COUNt 1" in cmds, cmds
    assert 'MSCan:CONTrol "STOP:SIGN"' in cmds, cmds
    assert "MSCan:DWELl 3.0" in cmds, cmds
    assert cmds.index("MSCan:CHANnel MEM999") < cmds.index("FREQ:MODE MSC"), cmds
    assert "SYST:AUD:REM:MOD 6" in cmds, cmds
    assert "OUTPut:SQUelch:CONTrol MEM" in cmds, cmds

    params_ueb = {"mode": "ueberwachung", "dwell_s": None, "hold_s": 2.0, "squelch_from_memory": False}
    cmds2 = scan_setup_commands(params_ueb, "10.0.0.5", 17300, 17301, 6)
    assert "MSCan:COUNt INF" in cmds2, cmds2
    assert "MSCan:DWELl INFinity" in cmds2, cmds2
    assert "OUTPut:SQUelch:CONTrol NONE" in cmds2, cmds2

    # Nachtrag 3: scan_setup_commands() traegt KEIN DELete mehr - das ist
    # jetzt ausschliesslich scan_cleanup_paths_commands() vorbehalten.
    assert not any("DELete" in c for c in cmds), cmds
    assert not any("DELete" in c for c in cmds2), cmds2
    cleanup = scan_cleanup_paths_commands("10.0.0.5", 17300, 17301)
    assert cleanup == [
        'TRAC:UDP:DELete "10.0.0.5",17300',
        'TRAC:UDP:DELete "10.0.0.5",17301',
    ], cleanup

    assert scan_start_command() == "INIT:CONM"
    teardown = scan_teardown_commands("10.0.0.5", 17300, 17301)
    assert teardown[0] == "ABORt" and teardown[-1] == "FREQ:MODE CW", teardown
    assert "SYST:AUD:REM:MOD 0" in teardown, teardown
    print("S3 ok")


def _build_synthetic_mscan_data(levels_raw, freqs) -> bytes:
    data = b""
    if levels_raw:
        data += struct.pack(f">{len(levels_raw)}h", *levels_raw)
    if freqs:
        data += struct.pack(f">{len(freqs)}I", *freqs)
    return data


def _s4_parse_mscan_items() -> None:
    opt_header = struct.pack(FMT_MSCAN_OPT_HDR, 3, 20, 30, 1, 0)
    data = _build_synthetic_mscan_data([453, 2500], [98_500_000, 0])
    flags = FLAG_LEVEL | FLAG_FREQUENCY | FLAG_OPTIONAL_HEADER | FLAG_SIGNAL_GREATER_SQUELCH
    result = parse_mscan_items(flags, opt_header, data, 2)

    assert result["header"] == {
        "cycle_count": 3, "hold_time": 20, "dwell_time": 30,
        "direction_up": 1, "stop_signal": 0,
    }, result["header"]
    assert result["signal_greater_squelch"] is True, result

    items = result["items"]
    assert len(items) == 2, items
    assert items[0]["freq_hz"] == 98_500_000, items[0]
    assert abs(items[0]["level_dbuv"] - 45.3) < 1e-9, items[0]
    assert items[0]["is_marker"] is False, items[0]
    assert items[1]["freq_hz"] == 0, items[1]
    assert items[1]["raw_level"] == 2500, items[1]
    assert items[1]["is_marker"] is True, items[1]

    ohne_opt = parse_mscan_items(FLAG_LEVEL | FLAG_FREQUENCY, b"", data, 2)
    assert ohne_opt["header"] is None, ohne_opt
    print("S4 ok")


class _FakeSink:
    """Fake-Sink fuer den Segmenter-Selbsttest (S5-S7, S9-S12): sammelt
    geschriebene Bytes im Speicher statt eine echte Datei anzulegen. path
    traegt einen synthetischen VOLLEN Pfad (mit Verzeichnisanteil) - S5 prueft
    daran die os.path.basename()-Kuerzung in _finalize(). written sammelt ueber
    ALLE Teile eines fortgesetzten Segments hinweg (T-1014a) und belegt in
    S9/S10/S12, dass Fortsetzungen in DIESELBE Datei laufen."""
    _counter = 0

    def __init__(self) -> None:
        self.written = b""
        self.closed_keep: bool | None = None
        _FakeSink._counter += 1
        self.path = os.path.join("fake_job_dir", f"segment_{_FakeSink._counter:03d}.wav")

    def write(self, pcm: bytes) -> None:
        self.written += pcm


def _make_segmenter(mode: str, min_recording_s: float, channel_lookup: dict,
                     gap_s: float = DEFAULT_GAP_S, t0_wall: float = 0.0, t0_mono: float = 0.0,
                     hold_s: float = 0.0,
                     lead_in_silence_peak: int = 0, plop_skip_bytes: int = 0):
    closed_sinks: list = []

    def open_sink(channel, t_start, level_dbuv, cycle):
        return _FakeSink()

    def close_sink(sink, keep):
        sink.closed_keep = keep
        closed_sinks.append(sink)

    seg = MscanSegmenter({"mode": mode, "hold_s": hold_s}, channel_lookup, min_recording_s,
                          open_sink, close_sink, gap_s=gap_s, t0_wall=t0_wall, t0_mono=t0_mono)
    # T-1017: Vorlauf-Erkennung im Selbsttest standardmaessig AUS - die
    # Datagramme der uebrigen Tests sind 2 Byte gross und wuerden vom
    # Knall-Ueberspringen vollstaendig aufgezehrt. S16 uebergibt die echten
    # Werte ausdruecklich.
    seg.lead_in_silence_peak = lead_in_silence_peak
    seg.plop_skip_bytes = plop_skip_bytes
    return seg, closed_sinks


def _s5_segmenter_ueberwachung() -> None:
    import time

    freq_a, freq_b = 100_000_000, 101_000_000
    lookup = {
        freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0},
        freq_b: {"id": "B", "label": "Kanal B", "freq_hz": freq_b, "mem": 1},
    }
    t0_wall = time.time()
    seg, _closed = _make_segmenter("ueberwachung", min_recording_s=1.0, channel_lookup=lookup,
                                    t0_wall=t0_wall, t0_mono=0.0)

    # Audio ohne Item -> audio_orphan.
    seg.on_audio(-1.0, b"\x00\x00")

    # Item A -> Audio 0.0..3.0 s -> Marker -> Segment 3.0 s behalten.
    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio(3.0, b"\x00\x00")
    seg.on_audio_marker(5.0)

    # Item B -> Audio 0.4 s -> Marker -> zu kurz, verworfen.
    seg.on_item(10.0, freq_b, 20.0, cycle=1)
    seg.on_audio(10.0, b"\x00\x00")
    seg.on_audio(10.4, b"\x00\x00")
    seg.on_audio_marker(10.6)
    seg.on_audio(10.7, b"\x00\x00")

    # Item A erneut -> Audio 2 s -> KEIN Marker, Luecke via tick() schliesst.
    seg.on_item(20.0, freq_a, 35.0, cycle=1)
    seg.on_audio(20.0, b"\x00\x00")
    seg.on_audio(22.0, b"\x00\x00")
    seg.tick(22.2)   # Luecke 0.2 s < gap_s 0.5 s -> bleibt offen
    seg.tick(23.0)   # Luecke 1.0 s > gap_effective_s (hold_s 0 -> 0,5 s) -> schliesst
    seg.close()
    seg.on_audio(30.0, b"\x00\x00")

    # T-1014a: das Audio bei t=10.7 (nach dem Marker bei 10.6) setzt das
    # vorgemerkte Segment B fort, statt als audio_orphan_mid zu zaehlen -
    # mid faellt damit von 1 auf 0, orphan gesamt von 3 auf 2 (Vorlauf und
    # Nachlauf bleiben: vor dem ersten Item bzw. nach close() gibt es nichts
    # fortzusetzen). B bleibt mit 0,7 s unter min_recording_s und wird beim
    # Fensterende (tick 22.2) verworfen.
    results = seg.results()
    assert results["audio_orphan"] == 2, results
    assert results["audio_orphan_pre"] == 1, results
    assert results["audio_orphan_mid"] == 0, results
    assert results["audio_orphan_post"] == 1, results
    assert results["audio_resumed"] == 1, results
    assert results["discarded_short"] == 1, results

    kanaele = {c["channel_id"]: c for c in results["channels"]}
    assert "B" not in kanaele, kanaele
    assert kanaele["A"]["hits"] == 2, kanaele["A"]
    assert abs(kanaele["A"]["total_s"] - 5.0) < 1e-9, kanaele["A"]

    # Nachtrag 4: start_at-Format (ISO lokal MIT Offset, aus t0_wall/
    # t0_mono=0.0 zurueckgerechnet - t_start ist hier direkt die
    # Sekundenzahl seit t0_wall) und file als reiner Dateiname (kein
    # Verzeichnisanteil, os.path.basename in _finalize()).
    rec_a = kanaele["A"]["recordings"]
    assert len(rec_a) == 2, rec_a
    for r in rec_a:
        parsed = datetime.fromisoformat(r["start_at"])
        assert parsed.utcoffset() is not None, r
        assert os.sep not in r["file"] and r["file"].endswith(".wav"), r
    delta_s = (datetime.fromisoformat(rec_a[1]["start_at"])
               - datetime.fromisoformat(rec_a[0]["start_at"])).total_seconds()
    assert abs(delta_s - 20.0) < 1.0, delta_s   # zweites Segment (t=20) - erstes (t=0)
    print("S5 ok")


def _s6_segmenter_inventur() -> None:
    import time

    freqs = [100_000_000, 101_000_000, 102_000_000]
    lookup = {f: {"id": f"K{i}", "label": f"Kanal {i}", "freq_hz": f, "mem": i}
              for i, f in enumerate(freqs)}
    # t0_wall = time.time() (Muster S5) - der Default t0_wall=0.0 (Unix-
    # Epoche) laesst datetime.fromtimestamp() unter Windows je nach
    # Zeitzone mit OSError scheitern.
    seg, _closed = _make_segmenter("inventur", min_recording_s=1.0, channel_lookup=lookup,
                                    t0_wall=time.time(), t0_mono=0.0)

    t = 0.0
    for f in freqs:
        seg.on_item(t, f, 40.0, cycle=1)
        seg.on_audio(t, b"\x00\x00")
        seg.on_audio(t + 3.0, b"\x00\x00")
        seg.on_audio_marker(t + 3.5)
        t += 5.0

    assert seg.finished is False, "finished erst nach dem Zyklusende-Marker-Item"
    seg.on_cycle_end(t, cycle=1)
    assert seg.finished is True, "inventur muss nach on_cycle_end beendet sein"

    results = seg.results()
    assert results["cycles"] == 1, results
    gesamt_aufnahmen = sum(c["hits"] for c in results["channels"])
    assert gesamt_aufnahmen == 3, results

    # Nachtrag 4: recordings[].cycle kommt aus dem EIGENEN Zaehler
    # (self._cycles) - alle drei Segmente schliessen VOR dem einzigen
    # on_cycle_end() ganz am Ende, muessen also cycle == 0 tragen, obwohl
    # device_cycle (konstant uebergeben) == 1 ist - die beiden Felder sind
    # unabhaengig voneinander.
    for c in results["channels"]:
        for r in c["recordings"]:
            assert r["cycle"] == 0, r
            assert r["device_cycle"] == 1, r
    print("S6 ok")


def _s7_segmenter_wechsel() -> None:
    freq_x, freq_y = 200_000_000, 201_000_000
    lookup = {
        freq_x: {"id": "X", "label": "Kanal X", "freq_hz": freq_x, "mem": 0},
        freq_y: {"id": "Y", "label": "Kanal Y", "freq_hz": freq_y, "mem": 1},
    }
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=1.0, channel_lookup=lookup)

    seg.on_item(0.0, freq_y, 30.0, cycle=1)
    assert seg._current["freq_hz"] == freq_y, seg._current
    seg.on_item(1.0, freq_x, 30.0, cycle=1)

    # T-1014a: Y wird nur VORGEMERKT (Datei bleibt offen), nicht geschlossen.
    assert len(closed_sinks) == 0, closed_sinks
    assert freq_y in seg._pending, seg._pending
    assert seg._current is not None and seg._current["freq_hz"] == freq_x, seg._current

    # Erst mit dem Fensterende (Default hold_s 0 -> 2,0 s) wird Y endgueltig
    # geschlossen und mangels Audio verworfen.
    seg.tick(4.0)
    assert len(closed_sinks) == 1, closed_sinks
    assert closed_sinks[0].closed_keep is False, closed_sinks[0].closed_keep
    assert freq_y not in seg._pending, seg._pending
    print("S7 ok")


def _s15_sprechpause_in_der_hold_zeit() -> None:
    """T-1018: Eine Sprechpause von 1,2 s waehrend der Hold-Zeit (2,0 s) darf
    das Segment NICHT schliessen - der Empfaenger steht dort noch. Vor der
    Aenderung schloss die feste Schwelle von 0,5 s, und die Aufnahme musste
    anschliessend per Fortsetzung wieder zusammengeklebt werden."""
    import time

    freq_a = 100_000_000
    lookup = {freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0}}
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=1.0,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=2.0)
    assert abs(seg.gap_effective_s - 2.5) < 1e-9, seg.gap_effective_s

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio(0.5, b"\x00\x00")
    seg.tick(1.0)                      # 0,5 s Stille - frueher schon zu viel
    seg.tick(1.7)                      # 1,2 s Stille, immer noch in der Hold-Zeit
    assert seg._current is not None, "Segment darf in der Hold-Zeit offen bleiben"
    assert not seg._pending, seg._pending
    seg.on_audio(1.7, b"\x00\x00")     # Sprecher setzt fort - KEINE Fortsetzung noetig
    seg.on_audio(2.2, b"\x00\x00")
    seg.tick(5.0)                      # 2,8 s > 2,5 s -> jetzt vormerken
    assert seg._current is None, "nach der Hold-Zeit muss die Luecke greifen"
    seg.close()

    results = seg.results()
    assert results["audio_resumed"] == 0, results        # kein Zusammensetzen
    assert results["audio_orphan_mid"] == 0, results
    assert len(closed_sinks) == 1, closed_sinks
    assert len(closed_sinks[0].written) == 8, closed_sinks[0].written
    rec = results["channels"][0]["recordings"]
    assert len(rec) == 1, rec
    assert abs(rec[0]["duration_s"] - 2.2) < 1e-9, rec[0]
    print("S15 ok")


def _pcm_block(amplitude: int, n_samples: int) -> bytes:
    """Synthetisches PCM fuer die Vorlauf-Tests (big-endian 16 Bit)."""
    return np.full(n_samples, amplitude, dtype=">i2").tobytes()


def _s16_einschaltknall_verworfen() -> None:
    """T-1017: Stille und Einschaltknall werden verworfen, am Segmentanfang
    und nach einer Sprechpause. Aufbau je Oeffnung: zwei stille Datagramme,
    ein lautes (Knall), dann Nutzsignal. Ein Datagramm = 480 Abtastwerte =
    960 Byte = 30 ms; PLOP_SKIP_BYTES 2240 verwirft also das Knall-Datagramm
    ganz und 320 Byte des naechsten."""
    import time

    still = _pcm_block(0, 480)
    laut = _pcm_block(20000, 480)
    nutz = _pcm_block(3000, 480)

    freq_a = 100_000_000
    lookup = {freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0}}
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=0.1,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=2.0,
                                         lead_in_silence_peak=LEAD_IN_SILENCE_PEAK,
                                         plop_skip_bytes=PLOP_SKIP_BYTES)

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.00, still)          # verworfen
    seg.on_audio(0.03, still)          # verworfen
    seg.on_audio(0.06, laut)           # Knall, ganz verworfen (960 von 2240)
    seg.on_audio(0.09, nutz)           # 960 -> insgesamt 1920, weiter verwerfen
    seg.on_audio(0.12, nutz)           # 320 verworfen, 640 geschrieben
    assert seg._current["lead_state"] == "done", seg._current["lead_state"]
    assert len(closed_sinks) == 0, closed_sinks
    seg.on_audio(0.15, nutz)           # voll geschrieben

    # Sprechpause > PLOP_GAP_S: neue Squelch-Oeffnung, neuer Knall
    seg.on_audio(0.60, laut)
    assert seg._current["lead_state"] == "skip", seg._current["lead_state"]
    seg.on_audio(0.63, nutz)
    seg.on_audio(0.66, nutz)           # ab hier wieder Nutzsignal
    seg.close()

    geschrieben = len(closed_sinks[0].written)
    # Erste Oeffnung: 640 + 960 = 1600. Zweite: Knall 960 + 960 verworfen,
    # vom dritten 320 -> 640 geschrieben. Summe 2240.
    assert geschrieben == 2240, geschrieben
    results = seg.results()
    assert results["audio_orphan_mid"] == 0, results
    rec = results["channels"][0]["recordings"]
    assert len(rec) == 1, rec
    # t_first_audio ist der erste BEHALTENE Abtastwert (0,12 s), nicht 0,00 s.
    assert abs(rec[0]["duration_s"] - 0.54) < 1e-9, rec[0]
    print("S16 ok")


def _s13_kein_fremdton_nach_zyklusende() -> None:
    """T-1014c: Nach einem Zyklusende ist der Empfaenger weitergezogen.
    Ankommendes Audio ohne Item darf das vorgemerkte Segment NICHT mehr
    fortsetzen - sonst landet der Einschaltknall des naechsten Kanals in der
    Datei des vorigen (Befund longrun_20260909_092434, Datei 093035_021)."""
    import time

    freq_a = 100_000_000
    lookup = {freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0}}
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=0.1,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=2.0)

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio(0.3, b"\x00\x00")
    seg.on_audio_marker(0.5)           # vorgemerkt, noch geparkt
    assert seg._pending[freq_a]["parked"] is True, seg._pending
    seg.on_cycle_end(0.8, cycle=1)     # Empfaenger zieht weiter
    assert seg._pending[freq_a]["parked"] is False, seg._pending
    seg.on_audio(0.9, b"\x00\x00")     # Fremdton, KEIN Item
    seg.close()

    results = seg.results()
    assert results["audio_resumed"] == 0, results
    assert results["audio_orphan_mid"] == 1, results
    assert len(closed_sinks) == 1, closed_sinks
    assert len(closed_sinks[0].written) == 4, closed_sinks[0].written   # nur die 2 eigenen
    print("S13 ok")


def _s14_fremdes_item_hebt_parkstatus_auf() -> None:
    """T-1014c: Auch ein Item auf einem anderen Kanal belegt, dass der
    Empfaenger weitergezogen ist. Das vorgemerkte Segment bleibt offen und per
    eigenem Item fortsetzbar, aber blindes Audio erreicht es nicht mehr."""
    import time

    freq_a, freq_b = 100_000_000, 101_000_000
    lookup = {
        freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0},
        freq_b: {"id": "B", "label": "Kanal B", "freq_hz": freq_b, "mem": 1},
    }
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=0.1,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=2.0)

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio_marker(0.3)
    seg.on_item(0.6, freq_b, 30.0, cycle=1)      # hebt den Parkstatus von A auf
    assert seg._pending[freq_a]["parked"] is False, seg._pending
    assert seg._last_pending_freq is None, seg._last_pending_freq

    # Item auf A selbst setzt weiterhin fort - das ist die sichere Variante.
    seg.on_item(1.0, freq_a, 41.0, cycle=1)
    assert seg._current["freq_hz"] == freq_a, seg._current
    seg.on_audio(1.0, b"\x00\x00")
    seg.close()

    results = seg.results()
    assert results["audio_resumed"] == 1, results
    kanaele = {c["channel_id"]: c for c in results["channels"]}
    assert len(kanaele["A"]["recordings"]) == 1, kanaele["A"]
    print("S14 ok")


def _s9_hold_fortsetzung() -> None:
    """T-1014a Ursache A: Ton kehrt in der Hold-Zeit zurueck, OHNE dass ein
    neues MScan-Item kommt. Vor der Aenderung: audio_orphan_mid, Segment mit
    1,0 s unter min_recording_s verworfen. Jetzt: dieselbe Datei, 3,0 s."""
    import time

    freq_a = 100_000_000
    lookup = {freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0}}
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=1.0,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=2.0)
    assert abs(seg.resume_window_s - 3.0) < 1e-9, seg.resume_window_s

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio(1.0, b"\x00\x00")
    seg.on_audio_marker(1.2)          # Sprechpause -> nur vorgemerkt
    seg.on_audio(2.0, b"\x00\x00")    # Ton kehrt zurueck, KEIN Item
    seg.on_audio(3.0, b"\x00\x00")
    seg.on_audio_marker(3.2)
    seg.tick(7.0)                     # Fenster abgelaufen -> endgueltig

    results = seg.results()
    assert results["audio_orphan_mid"] == 0, results
    assert results["audio_resumed"] == 1, results
    assert results["discarded_short"] == 0, results
    assert len(closed_sinks) == 1, closed_sinks          # EINE Datei, nicht zwei
    assert closed_sinks[0].closed_keep is True, closed_sinks[0].closed_keep
    assert len(closed_sinks[0].written) == 8, closed_sinks[0].written
    rec = results["channels"][0]["recordings"]
    assert len(rec) == 1, rec
    assert abs(rec[0]["duration_s"] - 3.0) < 1e-9, rec[0]
    print("S9 ok")


def _s10_zyklus_fortsetzung() -> None:
    """T-1014a Ursache B: das Zyklusende schliesst mitten in der Aussendung,
    der naechste Rundlauf trifft denselben Kanal. Vor der Aenderung zwei
    Schnipsel (0,3 s und 0,8 s), beide zu kurz. Jetzt eine Datei, 2,0 s."""
    import time

    freq_a = 100_000_000
    lookup = {freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0}}
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=1.0,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=2.0)

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio(0.3, b"\x00\x00")
    seg.on_cycle_end(0.5, cycle=1)             # nur vorgemerkt
    seg.on_item(1.2, freq_a, 42.0, cycle=1)    # naechster Rundlauf, gleicher Kanal
    seg.on_audio(1.2, b"\x00\x00")
    seg.on_audio(2.0, b"\x00\x00")
    seg.on_audio_marker(2.2)
    seg.tick(6.0)

    results = seg.results()
    assert results["audio_resumed"] == 1, results
    assert results["discarded_short"] == 0, results
    assert len(closed_sinks) == 1, closed_sinks
    assert len(closed_sinks[0].written) == 8, closed_sinks[0].written
    rec = results["channels"][0]["recordings"]
    assert len(rec) == 1, rec
    assert abs(rec[0]["duration_s"] - 2.0) < 1e-9, rec[0]
    assert abs(rec[0]["level_max_dbuv"] - 42.0) < 1e-9, rec[0]
    print("S10 ok")


def _s11_fensterende() -> None:
    """T-1014a: das Fenster endet wirklich. Danach eintreffendes Audio zaehlt
    weiterhin als audio_orphan_mid, das Segment ist endgueltig geschlossen."""
    import time

    freq_a = 100_000_000
    lookup = {freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0}}
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=1.0,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=0.0)
    assert abs(seg.resume_window_s - 2.0) < 1e-9, seg.resume_window_s

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio(0.5, b"\x00\x00")
    seg.on_audio_marker(0.7)
    seg.tick(3.0)                     # 2,3 s > 2,0 s -> endgueltig, 0,5 s verworfen
    seg.on_audio(3.1, b"\x00\x00")    # nichts mehr fortzusetzen

    results = seg.results()
    assert results["discarded_short"] == 1, results
    assert results["audio_orphan_mid"] == 1, results
    assert results["audio_resumed"] == 0, results
    assert len(closed_sinks) == 1, closed_sinks
    assert closed_sinks[0].closed_keep is False, closed_sinks[0].closed_keep
    print("S11 ok")


def _s12_kanalwechsel_im_fenster() -> None:
    """T-1014a: zwei Kanaele wechseln sich ab. A wird beim Item auf B nur
    vorgemerkt und beim naechsten A-Item fortgesetzt; B laeuft unabhaengig
    davon in seine eigene Datei und faellt unter min_recording_s."""
    import time

    freq_a, freq_b = 100_000_000, 101_000_000
    lookup = {
        freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0},
        freq_b: {"id": "B", "label": "Kanal B", "freq_hz": freq_b, "mem": 1},
    }
    seg, closed_sinks = _make_segmenter("ueberwachung", min_recording_s=1.0,
                                         channel_lookup=lookup, t0_wall=time.time(),
                                         t0_mono=0.0, hold_s=2.0)

    seg.on_item(0.0, freq_a, 40.0, cycle=1)
    seg.on_audio(0.0, b"\x00\x00")
    seg.on_audio(0.6, b"\x00\x00")
    seg.on_item(1.0, freq_b, 30.0, cycle=1)    # A vorgemerkt
    seg.on_audio(1.0, b"\x00\x00")
    seg.on_audio(1.6, b"\x00\x00")
    seg.on_item(2.0, freq_a, 41.0, cycle=1)    # B vorgemerkt, A fortgesetzt
    seg.on_audio(2.0, b"\x00\x00")
    seg.on_audio(2.6, b"\x00\x00")
    seg.close()

    results = seg.results()
    assert results["audio_resumed"] == 1, results
    assert results["discarded_short"] == 1, results          # B mit 0,6 s
    assert len(closed_sinks) == 2, closed_sinks              # zwei Dateien angelegt
    kanaele = {c["channel_id"]: c for c in results["channels"]}
    assert "B" not in kanaele, kanaele
    rec_a = kanaele["A"]["recordings"]
    assert len(rec_a) == 1, rec_a
    assert abs(rec_a[0]["duration_s"] - 2.6) < 1e-9, rec_a[0]
    assert rec_a[0]["bytes"] == 8, rec_a[0]                  # 4 Datagramme in EINER Datei
    print("S12 ok")


def _s8_recorder_end_to_end() -> None:
    import tempfile
    import time

    from eb200_core.job_store import JobStore

    with tempfile.TemporaryDirectory() as tmp:
        index_path = os.path.join(tmp, "jobs", "index.json")
        roots_path = os.path.join(tmp, "storage_roots.json")
        records_dir = os.path.join(tmp, "records")
        os.makedirs(records_dir, exist_ok=True)
        with open(roots_path, "w", encoding="utf-8") as fh:
            json.dump([records_dir], fh)
        store = JobStore(index_path=index_path, storage_roots_path=roots_path)

        run_params = {
            "mscan_job_id": "ABCDEF", "title": "Test", "mode": "ueberwachung",
            "dwell_s": None, "hold_s": 2.0, "squelch_from_memory": True,
            "min_recording_s": 1.0,
            "channels": [
                {"mem": 0, "id": "C1", "label": "Kanal 1", "freq_hz": 100_000_000,
                 "demod": "FM", "bw_hz": 15000, "squelch_dbuv": 20, "service": None,
                 "file": None, "tags": None},
                {"mem": 1, "id": "C2", "label": "Kanal 2", "freq_hz": 101_000_000,
                 "demod": "FM", "bw_hz": 15000, "squelch_dbuv": 20, "service": None,
                 "file": None, "tags": None},
            ],
        }
        manifest = store.create_job(
            type="mscan", label="MSCAN ABCDEF Test", params=run_params,
            start_iso="2026-08-26T10:00:00+02:00", end_iso=None,
        )
        store.set_state(manifest["job_id"], "laeuft")

        rec = MscanJobRecorder(store, manifest, audio_remote_mode=6,
                               t0_wall=time.time(), t0_mono=0.0)
        # T-1017: realistischer Verlauf statt reiner Stille - der Segmenter
        # verwirft auf dem Produktionspfad Stille und Einschaltknall. Ein
        # Datagramm ist 1600 Byte = 800 Abtastwerte = 0,05 s.
        stille = _pcm_block(0, 800)
        knall = _pcm_block(20000, 800)
        signal = _pcm_block(3000, 800)

        # Segment 1: Kanal C1. Stille und Knall werden verworfen (T-1017),
        # behalten bleibt 0,10 s bis 1,55 s -> 1,45 s >= min_recording_s.
        rec.record_item(0.0, 100_000_000, 45.0, cycle=1)
        rec.record_audio(0.00, stille)    # verworfen (Stille vor der Oeffnung)
        rec.record_audio(0.05, knall)     # verworfen (1600 von 2240 Byte Knall)
        # Zusammenhaengender Strom im 0,05-s-Takt. Groessere Abstaende wuerden
        # PLOP_GAP_S ueberschreiten und die Erkennung zu Recht neu
        # scharfschalten - echtes Audio kommt alle 30 ms.
        for _i in range(30):
            rec.record_audio(0.10 + _i * 0.05, signal)
        rec.record_audio_marker(1.70)

        # Segment 2: Kanal C2. Nach dem Vorlauf bleiben 0,05 s uebrig - unter
        # min_recording_s, also verworfen (discarded_short).
        rec.record_item(3.0, 101_000_000, 30.0, cycle=1)
        rec.record_audio(3.00, knall)     # ganz verworfen
        rec.record_audio(3.05, signal)    # 640 verworfen, 960 geschrieben
        rec.record_audio(3.10, signal)    # voll geschrieben -> Dauer 0,05 s
        rec.record_audio_marker(3.50)

        stats = rec.close()
        assert stats["recordings"] == 1, stats
        assert stats["segments"] == 2, stats

        job_files = os.listdir(manifest["job_dir"])
        wavs = [n for n in job_files if n.endswith(".wav")]
        sidecars = [n for n in job_files if n.endswith(".wav.json")]
        assert len(wavs) == 1, wavs
        assert sidecars == [wavs[0] + ".json"], (wavs, sidecars)

        stored = store.get(manifest["job_id"])
        results = stored["results"]
        assert "results" not in stored["stats"], stored["stats"]
        assert len(results["channels"]) == 1, results
        assert results["channels"][0]["channel_id"] == "C1", results
        assert results["discarded_short"] == 1, results
    print("S8 ok")


def _s17_pegel_inventur() -> None:
    """T-1019: Pegel-Inventur ueber zwei Durchgaenge - Schwelle, Zaehlung je
    Durchgang, stille Kanaele, unbekannte Frequenz."""
    import time

    freq_a, freq_b, freq_c = 100_000_000, 101_000_000, 102_000_000
    lookup = {
        freq_a: {"id": "A", "label": "Kanal A", "freq_hz": freq_a, "mem": 0},
        freq_b: {"id": "B", "label": "Kanal B", "freq_hz": freq_b, "mem": 1},
        freq_c: {"id": "C", "label": "Kanal C", "freq_hz": freq_c, "mem": 2},
    }
    params = {"mode": "inventur", "stop_after": "runtime", "record_audio": False,
              "level_active_dbuv": 25.0}
    inv = MscanLevelInventory(params, lookup, t0_wall=time.time(), t0_mono=0.0)

    # Durchgang 0: A zweimal ueber der Schwelle, B einmal darunter.
    inv.on_item(0.0, freq_a, 40.0, cycle=1)
    inv.on_item(0.5, freq_a, 32.0, cycle=1)
    inv.on_item(1.0, freq_b, 12.0, cycle=1)
    inv.on_cycle_end(2.0, cycle=1)
    assert inv.finished is False, "stop_after runtime darf am Zyklusende nicht enden"

    # Durchgang 1: A einmal, dazu eine Frequenz ausserhalb des Snapshots.
    inv.on_item(2.5, freq_a, 28.0, cycle=2)
    inv.on_item(2.7, 105_000_000, 50.0, cycle=2)
    inv.on_cycle_end(3.0, cycle=2)

    res = inv.results()
    assert res["kind"] == "level", res
    assert res["cycles"] == 2, res
    # T-1019f Stufe 3a: Offsets der beiden Zyklusenden (t0_mono = 0,0).
    assert res["cycle_ends_s"] == [2.0, 3.0], res
    # T-1019h: ohne mark_scan_start() bleibt t0_mono die Basis und die
    # Programmierdauer unbekannt.
    assert res["programming_s"] is None, res
    assert res["items"] == 5, res
    assert res["below_threshold"] == 1, res
    assert res["unknown_freq"] == 1, res
    assert res["level_active_dbuv"] == 25.0, res
    # 3 Snapshot-Kanaele + 1 unbekannte Frequenz
    assert len(res["channels"]) == 4, res["channels"]

    zeilen = {z["freq_hz"]: z for z in res["channels"]}
    a = zeilen[freq_a]
    assert a["active"] is True and a["hits"] == 3, a
    assert a["cycles_active"] == 2, a
    # T-1019f: Tripel mit dem Pegelmaximum je Durchgang. Durchgang 0 traegt
    # 40,0 (das hoehere der beiden Items), Durchgang 1 die einzelnen 28,0.
    assert a["per_cycle"] == [[0, 2, 40.0], [1, 1, 28.0]], a
    assert a["level_max_dbuv"] == 40.0 and a["level_last_dbuv"] == 28.0, a
    assert a["per_cycle_truncated"] is False, a

    b = zeilen[freq_b]
    assert b["active"] is False and b["hits"] == 0, b
    assert b["below_threshold"] == 1, b
    assert b["level_max_dbuv"] is None, b

    c = zeilen[freq_c]
    assert c["active"] is False and c["hits"] == 0 and c["per_cycle"] == [], c

    unbek = zeilen[105_000_000]
    assert unbek["channel_id"] is None and unbek["hits"] == 1, unbek
    assert unbek["mem"] is None, unbek

    # Reihenfolge: aktive zuerst (nach Treffern), dann stille nach mem.
    assert [z["freq_hz"] for z in res["channels"]] == [
        freq_a, 105_000_000, freq_b, freq_c], [z["freq_hz"] for z in res["channels"]]
    assert res["active_channels"] == 2 and res["silent_channels"] == 2, res

    # Ohne Schwelle zaehlt jede Squelch-Oeffnung.
    inv2 = MscanLevelInventory({"mode": "inventur", "stop_after": "cycle"}, lookup,
                               t0_wall=time.time(), t0_mono=0.0)
    # T-1019h: Scan beginnt erst nach 80 s Programmieren - die Zeitachse zaehlt
    # ab dort, die Spanne davor steht in programming_s.
    inv2.mark_scan_start(80.0)
    inv2.mark_scan_start(99.0)   # Zweitaufruf wirkungslos
    inv2.on_item(80.5, freq_b, 12.0, cycle=1)
    inv2.on_cycle_end(81.0, cycle=1)
    assert inv2.finished is True, "stop_after cycle muss am Zyklusende enden"
    res2 = inv2.results()
    assert res2["channels"][0]["freq_hz"] == freq_b, res2["channels"][0]
    assert res2["channels"][0]["hits"] == 1, res2["channels"][0]
    assert res2["cycle_ends_s"] == [1.0], res2
    assert res2["programming_s"] == 80.0, res2
    assert res2["below_threshold"] == 0, res2
    print("S17 ok")


def _s18_level_recorder_und_setup() -> None:
    """T-1019: Recorder-Auswahl, Manifest-Ergebnis und die Befehlsfolgen ohne
    Ton."""
    import tempfile
    import time

    from eb200_core.job_store import JobStore

    job_def = {
        "id": "ABCDEF", "title": "Pegel",
        "params": {"mode": "inventur", "stop_after": "runtime", "record_audio": False,
                   "level_active_dbuv": 25.0, "dwell_s": 0.2, "hold_s": 0.0,
                   "squelch_from_memory": True, "min_recording_s": 0.5},
    }
    snapshot = [
        {"id": "C1", "label": "Kanal 1", "freq_hz": 100_000_000, "demod": "FM",
         "bw_hz": 15000, "squelch_dbuv": 20, "service": "x", "file": "f.json", "tags": []},
        {"id": "C2", "label": "Kanal 2", "freq_hz": 101_000_000, "demod": "FM",
         "bw_hz": 15000, "squelch_dbuv": 20, "service": "x", "file": "f.json", "tags": []},
    ]
    rp = build_run_params(job_def, snapshot)
    assert rp["stop_after"] == "runtime" and rp["record_audio"] is False, rp
    assert rp["level_active_dbuv"] == 25.0, rp

    # Inventur mit stop_after runtime laeuft mit COUNt INF, ohne Ton fehlen
    # Audio-Fernmodus und AUD-Pfad in Auf- und Rueckbau.
    cmds = scan_setup_commands(rp, "10.0.0.5", 17300, 17301, 6)
    assert "MSCan:COUNt INF" in cmds, cmds
    assert "MSCan:DWELl 0.2" in cmds, cmds
    assert not any("AUD" in c for c in cmds), cmds
    assert not any(c.startswith("SYST:AUD") for c in cmds), cmds
    assert 'TRAC:UDP:TAG:ON "10.0.0.5",17300,MSC' in cmds, cmds
    assert cmds.index("MSCan:CHANnel MEM999") < cmds.index("FREQ:MODE MSC"), cmds

    teardown = scan_teardown_commands("10.0.0.5", 17300, 17301, record_audio=False)
    assert not any(",17301" in c for c in teardown), teardown
    assert "SYST:AUD:REM:MOD 0" in teardown, teardown
    assert teardown[-1] == "FREQ:MODE CW", teardown

    # Gegenprobe: mit Ton bleibt alles wie vor T-1019.
    rp_ton = dict(rp, record_audio=True, stop_after="cycle")
    cmds_ton = scan_setup_commands(rp_ton, "10.0.0.5", 17300, 17301, 6)
    assert "MSCan:COUNt 1" in cmds_ton, cmds_ton
    assert 'TRAC:UDP:TAG:ON "10.0.0.5",17301,AUD' in cmds_ton, cmds_ton
    assert "SYST:AUD:REM:MOD 6" in cmds_ton, cmds_ton

    with tempfile.TemporaryDirectory() as tmp:
        # JobStore-Aufbau wie in _s8_recorder_end_to_end().
        index_path = os.path.join(tmp, "jobs", "index.json")
        roots_path = os.path.join(tmp, "storage_roots.json")
        records_dir = os.path.join(tmp, "records")
        os.makedirs(records_dir, exist_ok=True)
        with open(roots_path, "w", encoding="utf-8") as fh:
            json.dump([records_dir], fh)
        store = JobStore(index_path=index_path, storage_roots_path=roots_path)

        manifest = store.create_job(
            type="mscan", label=run_label(job_def), params=rp,
            start_iso="2026-09-11T20:00:00+02:00", end_iso=None,
        )
        store.set_state(manifest["job_id"], "laeuft")

        rec = make_recorder(store, manifest, 6, t0_wall=time.time(), t0_mono=0.0)
        assert isinstance(rec, MscanLevelRecorder), type(rec)
        rec.phase = "scan"
        rec.record_item(0.0, 100_000_000, 40.0, 1)
        rec.record_item(0.4, 101_000_000, 10.0, 1)   # unter der Schwelle
        rec.record_audio(0.5, b"\x00\x00")           # darf nie in eine Datei laufen
        rec.record_cycle_end(1.0, 1)
        rec.record_item(1.5, 100_000_000, 41.0, 2)
        rec.tick(2.0)
        assert rec.finished is False, "stop_after runtime laeuft weiter"
        stats = rec.close()
        assert rec.failed is None, rec.failed
        assert stats["recordings"] == 0 and stats["bytes"] == 0, stats
        assert stats["files"] == [], stats
        assert stats["active_channels"] == 1, stats
        assert stats["below_threshold"] == 1, stats
        assert stats["cycles"] == 1 and stats["items"] == 3, stats

        # Keine einzige WAV-Datei im Job-Verzeichnis.
        job_files = os.listdir(manifest["job_dir"])
        assert not [n for n in job_files if n.endswith(".wav")], job_files

        gespeichert = store.get(manifest["job_id"])
        ergebnis = gespeichert["results"]
        assert ergebnis["kind"] == "level", ergebnis
        assert len(ergebnis["channels"]) == 2, ergebnis
        assert ergebnis["channels"][0]["channel_id"] == "C1", ergebnis
        assert ergebnis["channels"][0]["active"] is True, ergebnis
        assert ergebnis["channels"][0]["hits"] == 2, ergebnis
        assert ergebnis["channels"][1]["active"] is False, ergebnis

        # Gegenprobe: mit Ton waehlt make_recorder() den bisherigen Recorder.
        # Kein zweiter create_job() - ein zweites Zeitfenster kollidierte mit
        # dem ersten Job ("ein Empfaenger, ein Job", siehe job_store).
        manifest2 = dict(manifest, params=dict(rp, record_audio=True))
        rec2 = make_recorder(store, manifest2, 6, t0_wall=time.time(), t0_mono=0.0)
        assert isinstance(rec2, MscanJobRecorder), type(rec2)
    print("S18 ok")


def run_selftest() -> None:
    print("Selbsttest (mscan_run) ...")
    _s1_build_run_params()
    _s2_memory_commands()
    _s3_scan_setup_commands()
    _s4_parse_mscan_items()
    _s5_segmenter_ueberwachung()
    _s6_segmenter_inventur()
    _s7_segmenter_wechsel()
    _s8_recorder_end_to_end()
    _s9_hold_fortsetzung()
    _s10_zyklus_fortsetzung()
    _s11_fensterende()
    _s12_kanalwechsel_im_fenster()
    _s13_kein_fremdton_nach_zyklusende()
    _s14_fremdes_item_hebt_parkstatus_auf()
    _s15_sprechpause_in_der_hold_zeit()
    _s16_einschaltknall_verworfen()
    _s17_pegel_inventur()
    _s18_level_recorder_und_setup()
    print("\nSelbsttest erfolgreich abgeschlossen.")


def main() -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(description="MSCAN-Lauf-Logik (Session A, ohne Geraet)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        run_selftest()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
