#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
waterfall.py

Interaktive Live-Wasserfalldarstellung für das EB200 (OE3GAS-Setup) mit
der "Lupe" aus WS3, Abschnitt 3a: breite D-SCAN-Übersicht, per Klick
zoomt der Browser in eine feine IFPAN-Ansicht der angeklickten Stelle.

Architektur:
  - Ein einziger Hintergrund-Thread (DeviceController) steuert das EB200
    per SCPI und liest per UDP entweder D-SCAN- oder IFPAN-Datagramme,
    abhängig vom aktuellen Modus ("overview" oder "zoom"). Da Gerät und
    UDP-Port gemeinsam genutzt werden, kann jeweils nur einer der beiden
    Modi aktiv sein - genau wie am echten Frontpanel.
  - Moduswechsel kommen als Kommandos vom Browser über zwei kleine
    HTTP-Endpunkte (/api/zoom, /api/overview) und werden über eine
    Queue an den Controller-Thread übergeben.
  - Neue Spektren (Typ "dscan" oder "ifpan") werden per Server-Sent-
    Events (SSE, /events) an den Browser gestreamt - keine externen
    Pakete nötig (kein Flask, kein WebSocket-Paket).
  - Der Browser zeigt zwei Ansichten: Übersicht (D-SCAN, breiter
    rollierender Wasserfall über den gesamten Bereich) und Zoom (IFPAN,
    feiner Wasserfall um die angeklickte Frequenz). Klick auf die
    Übersicht -> Zoom; "Zurück"-Button -> wieder Übersicht.
  - Ziehen (Drag) statt Klicken in der D-SCAN-Übersicht markiert - NUR bei
    aktivem SDR-Ergänzungsmodus (sdrToggle) - einen Frequenzbereich und
    öffnet den separat laufenden SDR-Wasserfall (sdr_waterfall.py, eigener
    Port) darauf; ohne SDR-Toggle ist Ziehen dort wirkungslos. Im
    IFPAN-Zoom schwenkt Ziehen stattdessen immer die Mittenfrequenz. Die
    frühere Übergabe eines gezogenen Bereichs an die Frequenzblock-
    Verwaltung (block_admin.py) ist 2026-08 ersatzlos entfallen. Reiner
    Klick (keine nennenswerte Mausbewegung) verhält sich weiterhin wie
    bisher (Zoom bzw. Neuzentrierung).

Wichtige Annahme (nicht aus den vorliegenden Annexen ableitbar, da die
genaue Aufteilung eines D-SCAN-Sweeps auf mehrere UDP-Datagramme nicht
dokumentiert ist - siehe Anforderungsliste.md, fehlende Interface-
Description 4052.2000.01 SB):
  Es wird angenommen, dass aufeinanderfolgende D-SCAN-Datagramme mit
  identischem start/stop/step im OptionalHeader lückenlos aneinander-
  gereihte Ausschnitte EINES Sweeps sind (SweepAccumulator sammelt sie,
  bis die erwartete Gesamtpunktzahl erreicht ist). Sollte sich das als
  falsch herausstellen, äußert sich das durch sichtbar "zerstückelte"
  oder nie fertig werdende Übersichts-Frames - bitte dann melden.

Gleiche Annahme wie zuvor: LEVEL-Rohwerte in 0,1-dBµV-Schritten
(raw / 10.0), nicht durch die Annexe bestätigt.
"""

from __future__ import annotations

import faulthandler
import http.server
import json
import os
import queue
import select
import shutil
import signal
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse, parse_qs

from eb200_core.scpi_verify import (
    verify, freq_check, enum_check, bw_check, format_report, nearest_bw_stage,
    drain_errors, wait_opc,
)
from eb200_core.audio_wav import WavAudioWriter, wav_params_for_mode
from eb200_core.rx_snapshot import build_device_info, build_rx_snapshot
from eb200_core.spectrum_store import SpectrumBinWriter
from eb200_core.session_store import SessionStore
from eb200_core.job_store import (
    JobStore, ST_GEPLANT, ST_LAEUFT, ST_BEENDET, ST_GESTOPPT, ST_ABGEBROCHEN,
    load_storage_roots,
)
from eb200_core.job_runner import DscanJobRecorder, IfpanJobRecorder, IfpanRecordFrame
from eb200_core import paths
from eb200_core.path_sandbox import to_portable
from eb200_core.mscan_run import (
    make_recorder, MSCAN_TAG, MEMORY_CLEAR_COMMAND, memory_write_command,
    parse_memory_readback, memory_matches, scan_cleanup_paths_commands, scan_setup_commands,
    scan_start_command, scan_teardown_commands, parse_mscan_items,
)
from eb200_core.mscan_job import MscanJobStore
from eb200_core.param_store import ParamStore, snapshot_eb200, register_server, DEFAULT_STORE_PATH
from eb200_core.known_freq import (
    load_catalog, load_catalog_cached, load_station_config, load_view_config,
)
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
from eb200_core.sched_health import SchedulerHealth, dump_threads
from eb200_core.signal_detect import SignalDetector  # T-505: Auto-Marker

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# T-2013: Geraeteadresse aus device.json in der Datenwurzel (Betreiber-
# konfiguration wie storage_roots.json; Felder eb200_host, scpi_port,
# local_ip). Vorrang hat --eb200-host auf der Kommandozeile, fehlt beides,
# gilt die Vorgabe. Gelesen wird EINMAL beim Start - eine auf der Statusseite
# gespeicherte Adresse wirkt erst nach einem Neustart von :8000.
DEVICE_JSON = "device.json"
EB200_IP_VORGABE = "10.0.0.55"
SCPI_PORT_VORGABE = 5555


def lade_device_config(pfad: Optional[str] = None) -> dict:
    """Liest device.json; liefert immer ein vollstaendiges dict mit
    eb200_host, scpi_port, local_ip (None = automatisch) und source
    ("default" oder "device.json"). Eine fehlende Datei ist der Normalfall,
    eine unlesbare wird gemeldet und durch die Vorgabe ersetzt - der Server
    startet in jedem Fall."""
    pfad = pfad or paths.data_path(DEVICE_JSON)
    cfg = {"eb200_host": EB200_IP_VORGABE, "scpi_port": SCPI_PORT_VORGABE,
           "local_ip": None, "source": "default"}
    try:
        with open(pfad, encoding="utf-8") as f:
            roh = json.load(f)
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as exc:
        print(f"[device] {pfad} nicht lesbar ({exc}) - Vorgabe {cfg['eb200_host']}")
        return cfg
    if not isinstance(roh, dict):
        print(f"[device] {pfad} ist kein JSON-Objekt - Vorgabe {cfg['eb200_host']}")
        return cfg
    geprueft, fehler = pruefe_device_eingabe(roh)
    if geprueft is None:
        print(f"[device] {pfad}: {fehler} - Vorgabe {cfg['eb200_host']}")
        return cfg
    cfg.update(geprueft)
    cfg["source"] = "device.json"
    return cfg


def pruefe_device_eingabe(roh: dict) -> tuple[Optional[dict], Optional[str]]:
    """Prueft Adresse, Port und lokale Adresse; liefert (werte, None) oder
    (None, fehlercode). Fehlercodes sind sprachneutral (CLAUDE.md, Regel 3)."""
    host = str(roh.get("eb200_host") or "").strip()
    if not host or len(host) > 253 or any(c.isspace() for c in host):
        return None, "device.invalid_host"
    try:
        port = int(roh.get("scpi_port", SCPI_PORT_VORGABE))
    except (TypeError, ValueError):
        return None, "device.invalid_port"
    if not 1 <= port <= 65535:
        return None, "device.invalid_port"
    lokal = str(roh.get("local_ip") or "").strip()
    if len(lokal) > 253 or any(c.isspace() for c in lokal):
        return None, "device.invalid_local_ip"
    return {"eb200_host": host, "scpi_port": port, "local_ip": lokal or None}, None


def speichere_device_config(werte: dict, pfad: Optional[str] = None) -> str:
    """Schreibt device.json ueber eine Temp-Datei und os.replace (nie halb
    geschrieben). Liefert den Pfad."""
    pfad = pfad or paths.data_path(DEVICE_JSON)
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"eb200_host": werte["eb200_host"], "scpi_port": werte["scpi_port"],
                   "local_ip": werte.get("local_ip")}, f, indent=2)
        print(file=f)
    os.replace(tmp, pfad)
    return pfad


def argv_wert(argv: list, name: str) -> Optional[str]:
    """Wert von "--name WERT" oder "--name=WERT" aus argv, sonst None."""
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1].strip() or None
        if a.startswith(name + "="):
            return a[len(name) + 1:].strip() or None
    return None


DEVICE_CFG = lade_device_config()
if argv_wert(sys.argv[1:], "--eb200-host"):
    DEVICE_CFG["eb200_host"] = argv_wert(sys.argv[1:], "--eb200-host")
    DEVICE_CFG["source"] = "--eb200-host"
EB200_IP = DEVICE_CFG["eb200_host"]
SCPI_PORT = DEVICE_CFG["scpi_port"]

LOCAL_IP: Optional[str] = DEVICE_CFG["local_ip"]    # None = automatisch ermitteln
UDP_PORT = 18102                   # eigener Port für diese Version
UDP_PORT_REC = 18103               # eigener UdpPath NUR fuer die Bin-Aufnahme
                                   # (getrennter Sequenzzaehler, siehe spectrum_store.py)
# Eigener UdpPath NUR fuer das optionale Job-Audio (WS3 11.5 "ein Port, ein
# Tag" - siehe validate_tag_streams.py: eine sequence_number, die sich
# mehrere Tags auf einem Pfad teilen, macht Luecken-/Verlusterkennung
# unzuverlaessig; ein eigener Ein-Tag-Pfad macht sie hier ERSTMALS
# zuverlaessig moeglich, siehe IfpanJobRecorder/_run_job_audio_loop).
UDP_PORT_JOB_AUDIO = 18104
# Eigener UdpPath NUR fuer den MSC-Tag eines MSCAN-Jobs (T-1005 Session B,
# siehe DeviceController._run_mscan_loop()) - "Ein Port, ein Tag": MSC-Tag
# exklusiv; Audio des Laufs auf UDP_PORT_JOB_AUDIO (derselbe Pfad wie beim
# IFPAN-Job-Audio, siehe job_audio_payload()).
UDP_PORT_JOB_MSCAN = 18105

# Audio-Mithoeren im IFPAN-Zoom (siehe AudioBroker und /audio/stream):
# rohe AUD-Payload (16 kHz / 16 Bit / mono, Big-Endian wie vom Geraet) wird
# an Browser-Clients gestreamt. Eigene, kleine Queue-Tiefe: bei ~33 Datagrammen
# pro Sekunde entspricht MONITOR_QUEUE_SIZE rund einer halben Sekunde Puffer -
# laeuft ein Client nach, wird das AELTESTE Paket verworfen (Latenzbremse, nicht
# Datensicherheit - fuers Archiv gibt es die WAV-Aufnahme).
MONITOR_QUEUE_SIZE = 16

# D-SCAN-Übersicht (Weg A) - hier: UKW-Rundfunkband
DSCAN_START_HZ = 88_000_000
DSCAN_STOP_HZ = 100_000_000
DSCAN_SPEED = "NORM"                # LOW | NORM | HIGH

# Parameter-Memory (eb200_core/param_store.py): gemeinsamer JSON-Speicher
# aller Server. Der Wasserfall legt beim Start eine EB200-Inventur ab
# (Gerätezustand VOR dem ersten eigenen Set-Kommando) und spiegelt seine
# Laufzeitparameter unter app.waterfall.* - als Anzeige-Quelle für die
# geplante Status-Page, NICHT als Konfigurationsquelle (die D-SCAN-
# Startwerte kommen weiterhin aus dem Gerät, siehe
# _read_dscan_config_from_device).
PARAM_STORE_PATH = DEFAULT_STORE_PATH
SNAPSHOT_ON_START = True            # False = Start ohne Geräte-Inventur
SNAPSHOT_START_DELAY_S = 3.0        # Wartezeit nach Start, bis die Hintergrund-Inventur beginnt
SNAPSHOT_QUERY_PAUSE_S = 0.05       # Pause zwischen den Queries (Entlastung des Geräts)

# T-703 (Geräteparameter-Sync): MOD/BW/Speed/Squelch werden in KEINEM
# UDP-OptionalHeader geliefert (hardware-validiert 2026-08, siehe
# docs/EB200_Geraeteprofil.md, Annex-F-Befunde) - einzige Quelle ist SCPI.
# Kompromiss aus Reaktionszeit auf Frontpanel-Änderungen und SCPI-Last;
# der Zugriffsweg (eigene, zweite SCPI-Verbindung neben dem Hauptkanal) ist
# über den Hintergrund-Snapshot bereits als sweep-neutral validiert
# (Δ ≤ 0,02 s bei 50-ms-Queries während aktivem D-SCAN).
PARAM_POLL_INTERVAL_S = 2.0
# Wartezeit vor einem Reconnect-Versuch nach Verbindungsverlust auf dieser
# Zweitverbindung - fest (nicht wachsend), damit kein staendiges Neuverbinden
# einen der zehn TCP-Client-Slots des Geraets flattern laesst.
PARAM_POLL_RECONNECT_BACKOFF_S = 5.0

# BW-ZOOM im D-SCAN (Annex C: FREQ:DSC:RES:AUTO). Startwert der IF-Bandbreite,
# die im BW-ZOOM den Span koppelt. Werteliste = IFPAN_BW_OPTIONS_HZ (dieselben
# 12 ZF-Filterstufen des Geräts).
DSCAN_BW_HZ_DEFAULT = 9000

SWEEP_WATCHDOG_S = 20.0              # Sweep-Puffer verwerfen, falls er so
                                     # lange nicht komplett wird (verlorene
                                     # UDP-Pakete o. ä.)

# T-719: getrennte, kuerzere Frist fuer den Fall "ueberhaupt kein Datagramm".
# Das ist ein anderer Befund als ein unvollstaendiger Sweep: das Geraet sendet
# gar nicht (Scan am Frontpanel abgebrochen, Konfiguration abgelehnt, UdpPath
# weg), und darauf soll die Uebersicht schnell reagieren. SWEEP_WATCHDOG_S
# bleibt bei 20 s, weil es faktisch eine maximale SWEEPDAUER ist - ein
# gesunder LOW-Sweep ueber einen weiten Bereich mit schmaler BW laeuft laenger
# als 10 s und darf nicht zyklisch verworfen werden. Auch bei Speed MTIM
# unbedenklich: das erste Datagramm kommt dort nach spaetestens einer
# Kanalmessung, 10 s voellige Stille sind also auch da schon abnormal.
SWEEP_SILENCE_S = 10.0

# Kleinste D-SCAN-Spanne, die das Geraet annimmt. NICHT im Handbuch
# dokumentiert - empirisch per Bisektion (S/N 101.459, V02.74): 20 kHz OK,
# 10 kHz abgelehnt mit -221,"Settings conflict;Span too low", unabhaengig
# von Richtung und IF-Bandbreite (auch bei 600 Hz und 9 kHz). Siehe
# docs/EB200_Geraeteprofil.md. Bei Ablehnung behaelt das Geraet den alten
# Wert des abgelehnten Kommandos -> der Sweep liefe still mit falschem
# Bereich. Deshalb hier vorab pruefen und dem Nutzer sagen, was er statt-
# dessen tun kann (IFPAN-Zoom ist fuer schmale Ausschnitte gedacht).
DSCAN_MIN_SPAN_HZ = 20_000

# Rücklese-Verifikation nach Parameteränderungen (siehe eb200_core/
# scpi_verify.py, WS1 Abschnitt "SCPI-Verifikation"). Bei problematischer
# Firmware (z. B. wenn eine Query-Kurzform nicht unterstützt wird) hier
# abschaltbar, ohne den restlichen D-SCAN-Konfigpfad anzufassen.
VERIFY_SCPI = True

# IFPAN-Zoom (Weg B) - Breite des Ausschnitts nach einem Klick in die
# Übersicht, per Schieberegler in der Zoom-Ansicht verstellbar. Gültige
# Stufen laut Annex C: ±12.5/25/50/100/250/500 kHz Rollkey-Werte, d. h.
# folgende Gesamtbreiten:
ALLOWED_IFPAN_SPANS_HZ = [25_000, 50_000, 100_000, 200_000, 500_000, 1_000_000]
ZOOM_SPAN_HZ_DEFAULT = 200_000  # deckt z. B. einen UKW-Kanal (US-Raster) samt Nachbarkanälen ab

# Modulation und Bandbreite im IFPAN-Zoom, per Auswahlliste in der Zoom-
# Ansicht verstellbar (siehe [SENSe:]DEModulation bzw. [SENSe:]BANDwidth in
# Annex C). Werte bewusst identisch zu block_admin.py gehalten (MODE_OPTIONS
# bzw. BANDWIDTH_OPTIONS_HZ dort), damit Auswahl im Wasserfall 1:1 in die
# Frequenzblock-Verwaltung übernommen werden kann.
IFPAN_MODE_OPTIONS = ["AM", "FM", "USB", "LSB", "CW", "IQ"]
IFPAN_MODE_DEFAULT = "AM"

# Die zwölf festen ZF-Filterbandbreiten des EB200 (Annex C, packed_struct-
# Kodierung der Speicherplätze: 0=0.15kHz ... 11=150kHz) - andere Werte sind
# am Gerät nicht einstellbar.
IFPAN_BW_OPTIONS_HZ = [150, 300, 600, 1500, 2400, 6000, 9000, 15000, 30000, 50000, 120000, 150000]
IFPAN_BW_HZ_DEFAULT = 9000  # Annahme: gängiger Startwert für Utility/Airband-Beobachtung

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8000

# Port der separat laufenden Frequenzblock-Verwaltung (block_admin.py).
# Wird nur im Browser-JS verwendet (Link-Ziel für die Bereichsauswahl),
# hier als Konstante gepflegt, damit beide Skripte dieselbe Portnummer
# konsistent verwenden.
BLOCK_ADMIN_PORT = 8010

RECV_TIMEOUT_S = 0.5                # UDP-recv-Timeout (Reaktionszeit auf Kommandos/Stop)
CLIENT_QUEUE_SIZE = 20

# T-716: Der SweepAccumulator setzt Sweeps rein durch Aneinanderhaengen der
# Chunks zusammen - der D-SCAN-OptionalHeader (Annex F) liefert KEINE Position
# des Chunks im Sweep. Nach einem ABOR liegen aber noch Datagramme des
# abgebrochenen Sweeps im UDP-Empfangspuffer; das erste davon wuerde als
# Sweep-Anfang gelten und die Phase des gesamten folgenden Wasserfalls
# verschieben (Befund OE3GAS 2026-09-05: Wasserfall rueckt beim Verstellen des
# MGC-Reglers seitlich weg und bleibt dort). DRAIN_SETTLE_S laesst noch
# fliegende Pakete ankommen, bevor geleert wird; DRAIN_MAX_DATAGRAMS deckelt
# die Schleife, falls wider Erwarten doch etwas sendet.
DRAIN_SETTLE_S = 0.2
DRAIN_MAX_DATAGRAMS = 20000

# Audio-Aufnahme im IFPAN-Zoom (siehe eb200_core/audio_wav.py). Nur EIN fester
# Modus für dieses Projekt - 16 kHz/16 Bit/mono ist für Sprach-/Utility-
# Mithören ausreichend und hält die WAV-Dateien klein.
AUDIO_REMOTE_MODE = 6  # SYSTem:AUDio:REMote:MODe 6 = 16 kHz, 16 Bit, mono
AUDIO_REMOTE_MODE_IQ = 1  # Mode 1 = 32 kHz, 16 Bit, 2 Kanäle - nur bei DEM IQ:
                          # die beiden PCM-Kanäle transportieren dann I und Q
                          # (Rohsamples statt NF, siehe Annex F / DEM-Enum 6)
wav_params_for_mode(AUDIO_REMOTE_MODE)  # früh validieren, nicht erst beim ersten Aufnahme-Start
wav_params_for_mode(AUDIO_REMOTE_MODE_IQ)  # dito für den IQ-Modus

# Zielverzeichnis für WAV-Aufnahmen, absolut: relative storage_roots-Einträge
# gelten gegen die Datenwurzel (T-2003, eb200_core/paths.py), nicht gegen das
# Arbeitsverzeichnis. Wird bei Bedarf beim ersten Aufnahme-Start angelegt
# (siehe DeviceController._start_recording).
# T-1205: Zielverzeichnis = erster Eintrag aus storage_roots.json
# (gleiche Regel wie die Long-Run-Jobs). Ohne/mit defekter Datei liefert
# load_storage_roots() ["records"] - Verhalten dann exakt wie bisher.
# Auswertung einmalig beim Start; Aenderung der Datei erfordert Neustart.
RECORD_DIR = os.path.join(paths.DATA_ROOT, load_storage_roots()[0])

# Rohdaten-Session-Aufzeichnung des D-SCAN-Pegelverlaufs (siehe docs/WS1
# Abschnitt 3c, eb200_core/session_store.py) - läuft NICHT automatisch beim
# Programmstart, sondern wird zur Laufzeit per /record/start bzw. /record/stop
# umgeschaltet (siehe DeviceController.start_session()/stop_session()).
# Passiver Abgriff, steuert/verändert nichts am D-SCAN-Ablauf.
SESSION_DIR = "sessions"  # Zielordner unter DATA_ROOT (T-2003), wird bei Bedarf angelegt

# ---------------------------------------------------------------------------
# Protokollkonstanten (Annex F)
# ---------------------------------------------------------------------------

MAGIC_NUMBER = 0x000EB200
TAG_DSCAN = 301
TAG_IFPAN = 501
TAG_AUDIO = 401
FLAG_LEVEL = 0x01
FLAG_OPTIONAL_HEADER = 0x80000000

FMT_EB200_HEADER = ">IHHH6s"
LEN_EB200_HEADER = struct.calcsize(FMT_EB200_HEADER)

FMT_GENERIC_ATTR_HDR = ">HH"
LEN_GENERIC_ATTR_HDR = struct.calcsize(FMT_GENERIC_ATTR_HDR)

FMT_TRACE_ATTR_HDR = ">hBBI"
LEN_TRACE_ATTR_HDR = struct.calcsize(FMT_TRACE_ATTR_HDR)

FMT_DSCAN_OPT_HDR = ">IIIIhhh"        # start/stop/step/markFrequency, bwZoom, refLevel, newStepScheme
LEN_DSCAN_OPT_HDR = struct.calcsize(FMT_DSCAN_OPT_HDR)

FMT_IFPAN_OPT_HDR = ">IIhhI"          # frequency, spanFrequency, reserved, averageType, measureTime
LEN_IFPAN_OPT_HDR = struct.calcsize(FMT_IFPAN_OPT_HDR)

# Audio-OptionalHeader (Annex F): short audio_mode; short frame_len; unsigned
# long frequency; unsigned long bandwidth; unsigned short demodulation; char
# sDemodulation[8]. Dessen Gesamtlänge (14 Byte ohne / 22 Byte mit
# sDemodulation) variiert je nach Firmware - deshalb wird beim Herausschneiden
# der Nutzdaten NICHT über eine feste Struct-Länge gegangen, sondern über das
# im Trace-Attribut-Kopf mitgelieferte optional_header_length (siehe
# audio_payload()). Hier werden nur die ersten beiden - immer vorhandenen -
# Felder gebraucht, um frame_len zu lesen.
FMT_AUDIO_OPT_HDR_PREFIX = ">hh"      # audio_mode, frame_len


# T-505: Default-Abstand der Auto-Marker-Schwelle ueber dem geschaetzten Rauschflur.
DETECT_DELTA_DB_DEFAULT = 15.0   # T-505d: Fallback bei manuellem Delta (Hardware-Validierung 2026-08-30)


@dataclass
class DScanChunk:
    """Ein einzelnes empfangenes D-SCAN-Datagramm (kann ein Teil-Ausschnitt
    des gesamten konfigurierten Sweeps sein)."""
    start_hz: int
    stop_hz: int
    step_hz: int
    levels_dbuv: list[float]
    # NEU (additiv, WS1 Abschnitt 3c): dieselben Pegel als rohe, unveränderte
    # int16-Gerätewerte (Deci-dBµV) - für den passiven Session-Recorder
    # (eb200_core/session_store.py). levels_dbuv bleibt unverändert die
    # Grundlage für Anzeige/Wasserfall.
    raw_levels: list[int]


@dataclass
class DScanUpdate:
    """Ergebnis eines einzelnen add_chunk()-Aufrufs am SweepAccumulator.

    Wird für JEDEN empfangenen D-SCAN-Chunk erzeugt (nicht erst am
    Sweep-Ende), damit die Darstellung kontinuierlich mitwachsen kann:
    'offset'/'segment_levels' beschreiben genau den neu hinzugekommenen
    Ausschnitt (zum inkrementellen Zeichnen). Ist der Sweep mit diesem
    Chunk komplett geworden, ist zusätzlich 'full_levels' gesetzt (der
    gesamte, fertige Sweep - für Achsen/Meta/Übergabe an Folgeschritte).
    """
    complete: bool
    t: float
    start_hz: int
    stop_hz: int
    step_hz: int
    total: int                       # erwartete Gesamtpunktzahl des Sweeps
    offset: int                      # Startindex des neuen Segments im Sweep
    segment_levels: list[float]      # nur die NEUEN Werte dieses Chunks
    full_levels: Optional[list[float]] = None  # nur gesetzt wenn complete=True
    # nur gesetzt wenn complete=True - exakte Kennzahlen des soeben fertig
    # gewordenen Sweeps, fürs Frontend zur Aktivitätszeile (keine Controller-
    # Konsolenausgabe mehr je Sweep, siehe _run_overview_loop).
    sweep_elapsed_s: Optional[float] = None
    udp_packets: Optional[int] = None
    # NEU (additiv, WS1 Abschnitt 3c): rohe int16-Gerätewerte des fertigen
    # Sweeps, nur gesetzt wenn complete=True - ausschließlich für den passiven
    # Session-Recorder (eb200_core/session_store.py), berührt die Anzeige
    # nicht. full_levels bleibt unverändert die Grundlage für Wasserfall/UI.
    full_raw_levels: Optional[list[int]] = None
    # T-505: NEU erkannte Signale dieses fertigen Sweeps (SignalDetector.process),
    # nur bei complete=True; gehen im selben SSE-Frame an den Browser (Auto-Marker).
    detect_events: Optional[list[dict]] = None
    detect_delta: Optional[float] = None   # T-505d: aktuell wirksames Delta (auto ggf. neu bestimmt)


@dataclass
class IfPanFrame:
    t: float
    center_hz: int
    span_hz: int
    levels_dbuv: list[float]


# ---------------------------------------------------------------------------
# Datagramm-Parsing (Annex F)
# ---------------------------------------------------------------------------

def parse_datagram(raw: bytes):
    if len(raw) < LEN_EB200_HEADER:
        raise ValueError("Datagramm zu kurz")
    magic = struct.unpack_from(">I", raw, 0)[0]
    if magic != MAGIC_NUMBER:
        raise ValueError(f"unerwartete Magic Number {magic:#x}")
    attrs = []
    offset = LEN_EB200_HEADER
    while offset + LEN_GENERIC_ATTR_HDR <= len(raw):
        tag, length = struct.unpack_from(FMT_GENERIC_ATTR_HDR, raw, offset)
        offset += LEN_GENERIC_ATTR_HDR
        attrs.append((tag, raw[offset:offset + length]))
        offset += length
    return attrs


def datagram_seq(raw: bytes) -> int:
    """
    Liest die sequence_number aus dem EB200Header (Annex F): Offset 8, uint16
    Big-Endian (Header = magic:uint32, minor:uint16, major:uint16,
    sequence_number:uint16, reserved:6). Sie zaehlt PRO UdpPath fortlaufend hoch
    und laeuft bei 65536 ueber - die Lueckenerkennung (Sprung != 1) muss modulo
    65536 rechnen. Wird fuer den Bin-Recorder auf dem eigenen Aufnahme-UdpPath
    gebraucht (dort zaehlt sie ausschliesslich IFPAN-Pakete -> lueckenlos).
    """
    if len(raw) < LEN_EB200_HEADER:
        raise ValueError("Datagramm zu kurz fuer sequence_number")
    return struct.unpack_from(">H", raw, 8)[0]


def parse_dscan_trace(data: bytes) -> DScanChunk:
    n_items, _res, opt_len, sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, data, 0)
    pos = LEN_TRACE_ATTR_HDR
    if opt_len == 0:
        raise ValueError("DSCAN ohne OptionalHeader")
    start_hz, stop_hz, step_hz, _mark, _bwz, _ref, _nss = struct.unpack_from(
        FMT_DSCAN_OPT_HDR, data, pos
    )
    pos += opt_len
    if not (sel_flags & FLAG_LEVEL):
        raise ValueError("DSCAN ohne LEVEL-Daten")
    raw_levels = struct.unpack_from(f">{n_items}h", data, pos)
    return DScanChunk(
        start_hz=start_hz, stop_hz=stop_hz, step_hz=step_hz,
        levels_dbuv=[v / 10.0 for v in raw_levels],
        raw_levels=list(raw_levels),
    )


def parse_ifpan_trace(data: bytes) -> IfPanFrame:
    n_items, _res, opt_len, sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, data, 0)
    pos = LEN_TRACE_ATTR_HDR
    if opt_len == 0:
        raise ValueError("IFPAN ohne OptionalHeader")
    center_hz, span_hz, _res2, _avg_type, _meas_time = struct.unpack_from(
        FMT_IFPAN_OPT_HDR, data, pos
    )
    pos += opt_len
    if not (sel_flags & FLAG_LEVEL):
        raise ValueError("IFPAN ohne LEVEL-Daten")
    raw_levels = struct.unpack_from(f">{n_items}h", data, pos)
    return IfPanFrame(
        t=time.time(), center_hz=center_hz, span_hz=span_hz,
        levels_dbuv=[v / 10.0 for v in raw_levels],
    )


def parse_ifpan_raw(data: bytes):
    """
    Schlanke Variante von parse_ifpan_trace fuer den Bin-Recorder (siehe
    eb200_core/spectrum_store.py): liefert die Geometrie und die ROHEN int16-
    Geraetewerte (Deci-dBµV) OHNE die /10-Umrechnung und ohne den anzeige-
    orientierten IfPanFrame. Bewusst als eigene Funktion NEBEN parse_ifpan_trace
    gehalten, damit der funktionierende Live-Anzeigepfad unangetastet bleibt.

    Rueckgabe: (center_hz, span_hz, raw_levels) mit raw_levels als list[int].
    """
    n_items, _res, opt_len, sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, data, 0)
    pos = LEN_TRACE_ATTR_HDR
    if opt_len == 0:
        raise ValueError("IFPAN ohne OptionalHeader")
    center_hz, span_hz, _res2, _avg_type, _meas_time = struct.unpack_from(
        FMT_IFPAN_OPT_HDR, data, pos
    )
    pos += opt_len
    if not (sel_flags & FLAG_LEVEL):
        raise ValueError("IFPAN ohne LEVEL-Daten")
    raw_levels = list(struct.unpack_from(f">{n_items}h", data, pos))
    return center_hz, span_hz, raw_levels


def audio_payload(data: bytes) -> bytes:
    """
    Schneidet aus einem AUDIO-Attribut (Tag 401) die reinen Nutzdaten heraus
    (rohe 16-Bit-PCM-Samples, Big-Endian - siehe eb200_core/audio_wav.py fürs
    Byteswapping/Schreiben). Nutzt denselben generischen Trace-Attribut-Kopf
    wie D-SCAN/IFPAN (FMT_TRACE_ATTR_HDR), aus dem number_of_frames und
    optional_header_length gelesen werden; aus dem Audio-OptionalHeader selbst
    wird nur frame_len gebraucht (die ersten beiden Felder, siehe
    FMT_AUDIO_OPT_HDR_PREFIX - Rest des OptHdr wird hier nicht ausgewertet).

    Die Nutzdaten beginnen ERST NACH dem kompletten OptionalHeader
    (LEN_TRACE_ATTR_HDR + optional_header_length) und sind
    frame_len * number_of_frames Bytes lang - bewusst über opt_len geschnitten,
    NICHT über eine feste Header-Länge, da opt_len je nach Firmware variiert
    (14 Byte ohne / 22 Byte mit sDemodulation-Feld).
    """
    number_of_frames, _reserved, opt_len, _sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, data, 0)
    pos = LEN_TRACE_ATTR_HDR
    if opt_len == 0:
        raise ValueError("AUDIO ohne OptionalHeader")
    _audio_mode, frame_len = struct.unpack_from(FMT_AUDIO_OPT_HDR_PREFIX, data, pos)
    pos += opt_len
    n_bytes = frame_len * number_of_frames
    return data[pos:pos + n_bytes]


def job_audio_payload(data: bytes) -> bytes:
    """
    Wie audio_payload(), aber für den Job-Audio-Pfad (WS3 11.5, eigener
    UdpPath UDP_PORT_JOB_AUDIO, per PLAINER TAG:ON registriert statt der
    DEF:-Form des Display-Pfads - siehe _start_job_audio()).

    Per Gerätetest verifiziert (Schritt 4c): auf diesem sekundären Pfad
    liefert das Gerät KEIN Audio-OptionalHeader (optional_header_length ==
    0, anders als auf dem primären Display-Pfad, wo audio_payload() oben
    zutrifft) - die rohen 16-Bit-PCM-Samples (Big-Endian) folgen dann
    UNMITTELBAR nach dem generischen 8-Byte-Trace-Attribut-Kopf, ihre Länge
    ergibt sich aus number_of_frames * 2 Byte (kein frame_len verfügbar,
    Audio ist immer 16-Bit-Mono in diesem Projekt, siehe AUDIO_REMOTE_MODE;
    IQ ist im Job-Pfad deshalb gesperrt, siehe POST /api/job).
    Liefert das Gerät doch einmal einen OptionalHeader (opt_len != 0, z. B.
    künftige Firmware), wird wie im Display-Pfad über audio_payload()
    geparst - robust gegen beide Varianten.
    """
    if len(data) < LEN_TRACE_ATTR_HDR:
        raise ValueError("AUDIO-Attribut kürzer als der Trace-Attribut-Kopf")
    number_of_frames, _reserved, opt_len, _sel_flags = struct.unpack_from(FMT_TRACE_ATTR_HDR, data, 0)
    if opt_len != 0:
        return audio_payload(data)
    n_bytes = number_of_frames * 2
    return data[LEN_TRACE_ATTR_HDR:LEN_TRACE_ATTR_HDR + n_bytes]


# ---------------------------------------------------------------------------
# SweepAccumulator: setzt D-SCAN-Chunks zu vollständigen Sweeps zusammen.
# Bewusst als eigenständige, reine Klasse gehalten (kein Socket-Zugriff),
# damit sie sich ohne echtes Gerät testen lässt.
# ---------------------------------------------------------------------------

class SweepAccumulator:
    def __init__(self) -> None:
        self._buffer: list[float] = []
        # NEU (additiv, WS1 Abschnitt 3c): zweiter Puffer parallel zum
        # Float-Puffer, für dieselben Werte als rohe int16-Gerätewerte -
        # durchläuft an JEDER Stelle exakt dieselbe Reset-/Extend-/Slicing-
        # Logik wie self._buffer (siehe add_chunk), damit full_raw_levels
        # garantiert zu full_levels passt (full_levels[i] == full_raw_levels[i] / 10.0).
        self._raw_buffer: list[int] = []
        self._start_hz: Optional[int] = None
        self._stop_hz: Optional[int] = None
        self._step_hz: Optional[int] = None
        self._expected_total: Optional[int] = None
        self._last_leftover_log = 0.0  # für Log-Throttling (time.monotonic())

    def add_chunk(self, chunk: DScanChunk) -> DScanUpdate:
        params_changed = (
            self._start_hz != chunk.start_hz
            or self._stop_hz != chunk.stop_hz
            or self._step_hz != chunk.step_hz
        )
        if params_changed:
            # Erster Chunk oder neue Sweep-Parameter -> Puffer neu beginnen.
            self._buffer = []
            self._raw_buffer = []
            self._start_hz, self._stop_hz, self._step_hz = (
                chunk.start_hz, chunk.stop_hz, chunk.step_hz
            )
            self._expected_total = round(
                (self._stop_hz - self._start_hz) / self._step_hz
            ) + 1

        offset = len(self._buffer)
        self._buffer.extend(chunk.levels_dbuv)
        self._raw_buffer.extend(chunk.raw_levels)

        if len(self._buffer) >= self._expected_total:
            full_levels = self._buffer[: self._expected_total]
            full_raw_levels = self._raw_buffer[: self._expected_total]
            leftover_count = len(self._buffer) - self._expected_total
            # Nur der Teil dieses Chunks, der noch in den aktuellen Sweep
            # gehört, wird als "neues Segment" gemeldet (fürs inkrementelle
            # Zeichnen der letzten Teil-Zeile) - der Überschuss gehört
            # (fälschlicherweise gerundet) zum nächsten Sweep und wird
            # verworfen, siehe Kommentar unten zum Drift-Problem.
            segment = chunk.levels_dbuv[: self._expected_total - offset]
            update = DScanUpdate(
                complete=True, t=time.time(),
                start_hz=self._start_hz, stop_hz=self._stop_hz, step_hz=self._step_hz,
                total=self._expected_total, offset=offset, segment_levels=segment,
                full_levels=full_levels, full_raw_levels=full_raw_levels,
            )
            # WICHTIG: überzählige Samples werden verworfen, NICHT in den
            # nächsten Sweep übernommen. Wenn (stop-start)/step nicht
            # glatt aufgeht (z. B. 20 MHz / 60 kHz = 333,33...), weicht
            # unsere Schätzung "expected_total" leicht von der tatsäch-
            # lichen Punktzahl ab, die das Gerät pro Sweep liefert. Würde
            # der Rest mitgeschleppt, würde sich dieser kleine Versatz von
            # Sweep zu Sweep aufaddieren - sichtbar als diagonal
            # "wandernder" Wasserfall (beobachtet bei Bereichen, die nicht
            # glatt durch die Schrittweite teilbar sind, z. B. 20 MHz bei
            # 60 kHz Schritt; bei exakt teilbaren Bereichen wie 60 MHz trat
            # der Effekt folgerichtig nicht auf). Durch das Verwerfen bleibt
            # der Achsenversatz konstant (höchstens ±1 Kanal) statt zu
            # wachsen.
            self._buffer = []
            self._raw_buffer = []
            if leftover_count:
                self._log_leftover(leftover_count)
            return update

        return DScanUpdate(
            complete=False, t=time.time(),
            start_hz=self._start_hz, stop_hz=self._stop_hz, step_hz=self._step_hz,
            total=self._expected_total, offset=offset, segment_levels=chunk.levels_dbuv,
        )

    def _log_leftover(self, count: int) -> None:
        # Log-Throttling: höchstens alle 10 Sekunden eine Meldung, damit
        # die Konsole bei kontinuierlichem Betrieb nicht zuspammt.
        now = time.monotonic()
        if now - self._last_leftover_log > 10.0:
            print(f"[accumulator] {count} Restsample(s) pro Sweep verworfen "
                  f"(Sweeplänge nicht glatt durch Schrittweite teilbar, "
                  f"tatsächliche Gerätesweeplänge weicht leicht von der "
                  f"Schätzung ab - unkritisch, verhindert Sweep-zu-Sweep-Drift)")
            self._last_leftover_log = now

    def reset(self) -> None:
        self.__init__()


def _selftest_sweep_accumulator() -> None:
    """
    Eigenständiger Selbsttest für SweepAccumulator, ohne Gerät/Hardware
    ausführbar. Prüft die additive Roh-Erweiterung (WS1 Abschnitt 3c):
      (a) ein aus mehreren Chunks zusammengesetzter Sweep liefert in
          full_raw_levels exakt die eingespeisten int16-Rohwerte,
      (b) full_levels ist Wert für Wert identisch zu
          [v / 10.0 for v in full_raw_levels] - die Roh-Erweiterung verändert
          die bestehende Float-Anzeige-Pipeline an keiner Stelle.
    """
    start_hz, step_hz = 100_000_000, 1000
    n_total = 10
    stop_hz = start_hz + (n_total - 1) * step_hz

    # bewusst inkl. Randwerte (int16-Extreme, negative, Null), auf drei Chunks
    # unterschiedlicher Größe verteilt - wie beim echten Gerät kommen die
    # Rohwerte in mehreren UDP-Datagrammen nacheinander an.
    raw_values = [123, -45, 6789, -32768, 32767, 0, 1, -1, 999, -999]
    assert len(raw_values) == n_total

    def make_chunk(raw_slice: list[int]) -> DScanChunk:
        return DScanChunk(
            start_hz=start_hz, stop_hz=stop_hz, step_hz=step_hz,
            levels_dbuv=[v / 10.0 for v in raw_slice], raw_levels=raw_slice,
        )

    accumulator = SweepAccumulator()
    update = None
    for raw_slice in (raw_values[0:4], raw_values[4:7], raw_values[7:10]):
        update = accumulator.add_chunk(make_chunk(raw_slice))

    assert update is not None and update.complete
    assert update.full_raw_levels == raw_values, (update.full_raw_levels, raw_values)
    assert update.full_levels == [v / 10.0 for v in update.full_raw_levels]
    print("OK  SweepAccumulator: full_raw_levels exakt, full_levels konsistent zu full_raw_levels/10")
    print("\nSelbsttest SweepAccumulator erfolgreich abgeschlossen.")


# ---------------------------------------------------------------------------
# SCPI-Client
# ---------------------------------------------------------------------------

class ScpiClient:
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)

    def send(self, command: str) -> None:
        self._sock.sendall(command.encode("ascii") + b"\n")

    def query(self, command: str) -> str:
        """Kommando senden und die erste Antwortzeile zurückgeben (gestrippt).

        Liest in einer kleinen Schleife in einen Puffer, bis dieser ein
        Zeilenende (b"\\n") enthält - die TCP-Verbindung liefert nur
        Kommando-Antworten (die eigentlichen Messdaten laufen separat über
        UDP), ein etwaiger Rest nach der ersten Zeile wird daher verworfen.
        Das bestehende Socket-Timeout gilt unverändert; läuft es ab, kommt
        das als Exception hoch (vom Aufrufer, z. B. scpi_verify, als
        QUERY_ERROR zu behandeln).
        """
        self._sock.sendall(command.encode("ascii") + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("SCPI-Verbindung vom Gerät geschlossen")
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


# T-703 (Geraeteparameter-Sync): Lang-/Kurzform-Antworten des Geraets auf
# FREQ:DSC:SPEed? beide akzeptieren (Annex C: LOW | NORMal | HIGH | MTIMe).
_DSCAN_SPEED_SHORT = {
    "LOW": "LOW",
    "NORM": "NORM", "NORMAL": "NORM",
    "HIGH": "HIGH",
    "MTIM": "MTIM", "MTIME": "MTIM",
}


def normalize_device_params(raw: dict) -> dict:
    """
    Wandelt die rohen SCPI-Antwortstrings (DEM?, BAND?, OUTP:SQU:STAT?,
    OUTP:SQU:THR?, FREQ:DSC:SPE?, GCON:MODE?, GCON:MGC?) in normalisierte
    Python-Typen um (T-703). Reine Funktion, kein SCPI-Zugriff - eigenstaendig
    testbar (siehe tools/selftest_param_sync.py). Erwartet 'raw' mit den
    Schluesseln 'dem', 'band_hz', 'squelch_on', 'squelch_thr', 'speed',
    'gain_mode', 'gain_mgc' (jeweils die rohe Antwortzeile als str); liefert
    dieselben Schluessel mit normalisierten Werten:
      dem          -> str, Grossbuchstaben-Kurzform (z. B. "FM")
      band_hz      -> int (Hz)
      squelch_on   -> bool ("1"/"ON" -> True, sonst False)
      squelch_thr  -> float
      speed        -> str, eine von LOW/NORM/HIGH/MTIM (Lang- und
                      Kurzform-Antworten des Geraets werden beide erkannt)
      gain_mode    -> str, "MGC" oder "AGC" (MGC/FIX/FIXed -> "MGC", alles
                      andere inkl. AUTO/leer -> "AGC", siehe Kommentar unten)
      gain_mgc     -> int (dBµV) oder None bei nicht auswertbarer Antwort
      afc_on       -> bool ("1"/"ON" -> True, sonst und bei fehlendem
                      Schluessel False)
      freq_hz      -> int (Hz) oder None bei fehlender/unlesbarer Antwort
                      (Muster gain_mgc), auf 100 Hz gerundet
      afc_wirksam  -> bool: afc_on UND dem in ("FM","AM","PULS","PULSE").
                      Abgeleitet, NICHT vom Geraet gelesen: das Geraet nimmt
                      AFC auch in CW und IQ an, regelt dort aber nicht
                      (docs/EB200_Geraeteprofil.md, Abschnitt "AFC und
                      Ablagemessung"); eine Anzeige allein aus FREQ:AFC?
                      waere irrefuehrend.
    """
    speed_raw = str(raw.get("speed", "")).strip().upper()
    gain_mode_raw = str(raw.get("gain_mode", "")).strip().upper()
    try:
        gain_mgc = int(float(raw.get("gain_mgc")))
    except (TypeError, ValueError):
        gain_mgc = None
    dem = str(raw.get("dem", "")).strip().upper()
    afc_on = str(raw.get("afc_on", "")).strip().upper() in ("1", "ON")
    try:
        # Auf 100 Hz gerundet: die AFC regelt ohnehin nur in 100-Hz-Schritten
        # (Geraeteprofil). Ohne diese Rundung wuerde jede minimale AFC-
        # Nachfuehrung ueber params_changed() ein eigenes SSE-Event ausloesen.
        freq_hz = int(round(float(raw.get("freq_hz")) / 100.0)) * 100
    except (TypeError, ValueError):
        freq_hz = None
    return {
        "dem": dem,
        "band_hz": int(round(float(raw.get("band_hz")))),
        "squelch_on": str(raw.get("squelch_on", "")).strip().upper() in ("1", "ON"),
        "squelch_thr": float(raw.get("squelch_thr")),
        "speed": _DSCAN_SPEED_SHORT.get(speed_raw, speed_raw),
        # T-711: Das Geraet antwortet auf GCON:MODE? laut Handbuch 4.79 mit
        # FIX oder AUTO - NICHT mit MGC/AGC. FIXed ist dabei das Synonym fuer
        # MGC ("control is determined by MGC value"), AUTO das fuer AGC
        # (Handbuch 4.13/Annex C: MGC|FIXed bzw. AGC|AUTO). Die fruehere
        # Zuordnung FIXed -> "AGC" war falsch: sie meldete ein im MGC
        # stehendes Geraet als AGC zurueck, worauf die Oberflaeche das
        # Bedienelement bei jedem Poll wieder auf AGC zurueckstellte.
        "gain_mode": "MGC" if gain_mode_raw in ("MGC", "FIX", "FIXED") else "AGC",
        "gain_mgc": gain_mgc,
        "afc_on": afc_on,
        "freq_hz": freq_hz,
        "afc_wirksam": afc_on and dem in ("FM", "AM", "PULS", "PULSE"),
    }


def params_changed(old: Optional[dict], new: dict) -> bool:
    """True, wenn 'old' None ist oder sich mindestens ein Feld von 'new'
    gegenueber 'old' unterscheidet (T-703, reine Funktion)."""
    if old is None:
        return True
    return any(old.get(key) != value for key, value in new.items())


# Einzeilige Kurzfassung eines scpi_verify-Reports fuer die Browser-Meldung:
# NUR die nicht-OK-Checks plus die Fehler-Queue-Eintraege. Die vollstaendige,
# mehrzeilige Fassung (format_report) bleibt der Konsole vorbehalten.
# Genutzte Felder (eb200_core/scpi_verify.py): VerifyReport.results/.errors,
# CheckResult.ok/.name/.kind/.expected/.actual.
def _notice_from_report(report) -> str:
    teile = [f"{r.name}: {r.kind} (expected {r.expected}, got {r.actual})"
             for r in report.results if not r.ok]
    teile.extend(report.errors)
    text = "; ".join(teile) if teile else "no details"
    return text if len(text) <= 200 else text[:197] + "..."


# ---------------------------------------------------------------------------
# Broker: verteilt neue Frames (D-SCAN und IFPAN, unterschieden per "type")
# an alle verbundenen Browser-Clients.
# ---------------------------------------------------------------------------

class FrameBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=CLIENT_QUEUE_SIZE)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish_dscan_update(self, update: DScanUpdate) -> None:
        payload = {
            "type": "dscan" if update.complete else "dscan_partial",
            "t": update.t,
            "start_hz": update.start_hz, "stop_hz": update.stop_hz,
            "step_hz": update.step_hz, "total": update.total,
            "offset": update.offset, "segment": update.segment_levels,
        }
        if update.complete:
            payload["levels"] = update.full_levels
            payload["elapsed_s"] = update.sweep_elapsed_s
            payload["packets"] = update.udp_packets
            payload["detect"] = update.detect_events or []   # T-505
            payload["detect_delta"] = update.detect_delta   # T-505d
        self._publish(payload)

    def publish_notice(self, text: str, level: str = "warn") -> None:
        """Kurzer Hinweis an alle Browser-Clients (Verifikations-Befund,
        Watchdog). Wird in #meta-o angezeigt. level: 'warn' | 'info'."""
        self._publish({"type": "notice", "level": level, "text": text, "t": time.time()})

    def publish_device_params(self, params: dict) -> None:
        """T-703: normalisierter Geraetezustand (MOD/BW/Squelch/Speed) aus
        dem Parameter-Polling (siehe DeviceController._run_param_poll_loop) -
        einzige Quelle, da kein UDP-OptionalHeader diese Felder liefert
        (hardware-validiert, docs/EB200_Geraeteprofil.md)."""
        self._publish({"type": "device_params", "t": time.time(), **params})

    def publish_ifpan(self, frame: IfPanFrame, demod: str, bw_hz: int) -> None:
        # T-703: demod/bw_hz stammen aus dem Controller-Zustand (nach dem
        # Rueckleseabgleich in _configure_ifpan) und werden mit JEDEM Frame
        # mitgeliefert - so koennen auch spaet verbundene oder neu geladene
        # Browser-Clients ihre Auswahllisten nachziehen (die Selects werden
        # beim Seitenaufbau statisch mit den Defaults gerendert).
        self._publish({
            "type": "ifpan", "t": frame.t,
            "center_hz": frame.center_hz, "span_hz": frame.span_hz,
            "demod": demod, "bw_hz": bw_hz,
            "levels": frame.levels_dbuv,
        })

    def _publish(self, payload: dict) -> None:
        msg = json.dumps(payload)
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except queue.Empty:
                    pass


# ---------------------------------------------------------------------------
# AudioBroker: verteilt rohe AUD-Payload (Mithoeren) an /audio/stream-Clients.
# Gleiches Muster wie FrameBroker (bounded Queue je Client, drop-oldest bei
# Ueberlauf), aber fuer bytes statt JSON - Audio geht NICHT ueber /events,
# das waere base64-aufgeblaeht und muesste im Browser erst wieder aus dem
# JSON geloest werden.
# ---------------------------------------------------------------------------

def _peer_disconnected(sock: socket.socket) -> bool:
    """
    Prueft NICHT-blockierend, ob die HTTP-Gegenseite die Verbindung
    geschlossen hat. Ein /audio/stream-Client sendet nach den Request-
    Headern nie wieder Daten - wird der Socket lesbar, ist das entweder
    EOF (b"" = sauberer FIN) oder ein anstehender Fehler (RST): beides
    heisst "Client weg". MSG_PEEK konsumiert nichts, falls doch je ein
    Byte anlaege. Noetig, weil write()-Fehler als Abbruch-Erkennung nur
    funktionieren, solange Audio fliesst (siehe /audio/stream).
    """
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


class AudioBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=MONITOR_QUEUE_SIZE)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> int:
        """Entfernt den Client und liefert die Zahl der VERBLEIBENDEN Clients
        (der Handler stoesst bei 0 den Monitor-Stop an, siehe /audio/stream)."""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
            return len(self._subscribers)

    def client_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, pcm_be: bytes) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(pcm_be)
            except queue.Full:
                try:
                    q.get_nowait()          # aeltestes Paket opfern: Latenz vor Vollstaendigkeit
                    q.put_nowait(pcm_be)
                except queue.Empty:
                    pass


# ---------------------------------------------------------------------------
# DeviceController: ein Thread, zwei Modi (overview=D-SCAN, zoom=IFPAN).
# ---------------------------------------------------------------------------

class DeviceController(threading.Thread):
    def __init__(self, eb200_ip: str, local_ip: str, udp_port: int,
                 broker: FrameBroker, audio_broker: "AudioBroker", stop_event: threading.Event):
        super().__init__(daemon=True)
        self.eb200_ip = eb200_ip
        self.local_ip = local_ip
        self.udp_port = udp_port
        self.broker = broker
        self.audio_broker = audio_broker
        self.stop_event = stop_event
        self.commands: queue.Queue = queue.Queue()
        self.scpi: Optional[ScpiClient] = None
        self.udp_sock: Optional[socket.socket] = None
        # veränderlicher D-SCAN-Bereich und -Geschwindigkeit, per
        # /api/range bzw. /api/speed zur Laufzeit umschaltbar (Startwerte
        # aus der Konfiguration oben im Skript)
        self.dscan_start_hz = DSCAN_START_HZ
        self.dscan_stop_hz = DSCAN_STOP_HZ
        self.dscan_speed = DSCAN_SPEED
        # Parameter-Memory, siehe PARAM_STORE_PATH. Wird NUR aus dem
        # Controller-Thread beschrieben (run-Schleife), nie aus HTTP-Handlern.
        self.param_store = ParamStore(PARAM_STORE_PATH)
        self._snapshot_lock = threading.Lock()
        self._snapshot_running = False
        # T-703: letzter per SCPI-Polling ermittelter Geraetezustand (MOD/BW/
        # Squelch/Speed) - Poll-Thread schreibt (_set_last_device_params),
        # der /events-Handler liest ihn fuer den Initial-Push neuer SSE-
        # Verbindungen (get_last_device_params). Eigener kleiner Lock, da
        # zwei unterschiedliche Threads (Poll-Thread, HTTP-Handler-Threads)
        # zugreifen.
        self._device_params_lock = threading.Lock()
        self._last_device_params: Optional[dict] = None
        # MEASURE TIME in Sekunden (Handbuch: 0.5 ms..900 s) - wirkt am
        # Geraet NUR bei Speed MTIM (MTIME PER CHANNEL); bei LOW/NORM/HIGH
        # wird MEAS:TIME nicht gesendet, da global auch andere
        # Messfunktionen betroffen waeren. Startwert DEFAULT, wird in
        # _read_dscan_config_from_device vom Geraet ueberschrieben.
        # None = DEFAULT (Geraet waehlt die Messzeit BW-/detektorabhaengig
        # nach der Annex-E-Tabelle; MEAS:TIME? liefert dann den String DEF).
        # Ein Float ist eine explizit gesetzte Messzeit in Sekunden.
        self.dscan_mtime_s: Optional[float] = None
        # Detektor (PEAK/AVG/FAST) - bestimmt bei MTIME=DEFAULT, welche
        # Spalte der Annex-E-Tabelle das Frontend als Schaetzung anzeigt.
        # Wird in _read_dscan_config_from_device vom Geraet ueberschrieben.
        self.dscan_detector = "PEAK"
        self.zoom_span_hz = ZOOM_SPAN_HZ_DEFAULT
        # Modulation/Bandbreite im IFPAN-Zoom, per /api/mode bzw. /api/bw
        # zur Laufzeit umschaltbar (siehe request_mode/request_bw unten).
        self.ifpan_mode = IFPAN_MODE_DEFAULT
        self.ifpan_bw_hz = IFPAN_BW_HZ_DEFAULT
        # Squelch im IFPAN-Zoom (Annex C: OUTP:SQU / OUTP:SQU:THR), per
        # /api/squelch zur Laufzeit umschaltbar. Geraete-Default 10 dBµV.
        self.squelch_on = False
        self.squelch_thr_dbuv = 10
        # Verstaerkungsregelung im IFPAN-Zoom (Annex C: GCON:MODE /
        # GCON:MGC), per /api/gain zur Laufzeit umschaltbar. Amplitudentreue
        # Aufnahmen (z. B. fuer Konstellationsmessung, PLAN.md T-904)
        # verlangen MGC, weil eine nachregelnde AGC die Amplitudeninformation
        # im IQ-Datenstrom verschiebt.
        self.gain_mode = "AGC"        # "AGC" oder "MGC"
        self.gain_mgc_dbuv = 50       # nur wirksam bei gain_mode == "MGC"
        # T-712: Zaehler der vom Sweep-Watchdog ausgeloesten D-SCAN-Neuaufbauten.
        # Wird bei jedem vollstaendigen Sweep auf 0 zurueckgesetzt; dient nur
        # der Protokollierung (wie oft musste sich die Uebersicht erholen).
        self._dscan_watchdog_rebuilds = 0
        # T-505: Signaldetektion auf fertigen D-SCAN-Sweeps (Auto-Marker im
        # Wasserfall). Konfiguration per /api/detect, Merkliste per /api/detect/clear.
        self.detector = SignalDetector(delta_db=DETECT_DELTA_DB_DEFAULT)
        # BW-ZOOM-Zustand im D-SCAN, per /api/dscan_bwzoom bzw. /api/dscan_bw
        # zur Laufzeit umschaltbar.
        self.dscan_bw_zoom = False
        self.dscan_bw_hz = DSCAN_BW_HZ_DEFAULT
        # Audio-Aufnahme im IFPAN-Zoom (siehe _start_recording/_stop_recording),
        # per /api/record zur Laufzeit umschaltbar.
        self.recording = False
        self.audio_writer: Optional[WavAudioWriter] = None
        # Audio-Mithoeren im IFPAN-Zoom (siehe _start_monitoring/_stop_monitoring),
        # geschaltet durch die Praesenz von /audio/stream-Clients. Teilt sich den
        # AUD-Tag auf dem Display-UdpPath mit der WAV-Aufnahme - die SCPI-Schaltung
        # liegt deshalb in _audio_tag_on()/_audio_tag_off() (Refcount ueber
        # recording+monitoring).
        # monitoring = IST-Zustand (Tag am Geraet an); monitor_wanted = SOLL,
        # abgeleitet aus der Praesenz von /audio/stream-Clients. Getrennt, weil
        # Kommandos in der Uebersicht verpuffen koennen (Race mit /api/zoom)
        # und ein Zoom-Neustart (Span/Mode/BW) den IST-Zustand zuruecksetzt -
        # _run_zoom_loop stellt deshalb bei JEDEM Eintritt das SOLL her.
        self.monitoring = False
        self.monitor_wanted = False
        # IFPAN-Bin-Aufnahme (Spektrum-Verlauf im BW-Fenster) auf EIGENEM
        # UdpPath (UDP_PORT_REC) mit eigenem Socket + Empfangs-Thread, damit die
        # sequence_number dort ausschliesslich IFPAN-Pakete zaehlt (lueckenlos,
        # unabhaengig von Audio auf dem Display-Pfad). Start/Stop laeuft ueber
        # die commands-Queue (SCPI-Pfadregistrierung gehoert dem Zoom-Loop-
        # Thread), siehe request_spectrum_record_*/_start_spectrum_recording.
        self.spectrum_recording = False
        self._bin_sock: Optional[socket.socket] = None
        self._bin_thread: Optional[threading.Thread] = None
        self._bin_stop: Optional[threading.Event] = None
        # T-803 (Teil D): Pfad der zuletzt geschlossenen Ad-hoc-Bin-Aufnahme
        # (gesetzt in _run_bin_recorder_loop beim writer.close(), zurueckgesetzt
        # bei jedem Start) sowie Signal fuer den Abschluss der Stop-Verarbeitung
        # im Loop-Thread - siehe stop_spectrum_recording_and_wait().
        self._last_spectrum_path: Optional[str] = None
        self._spectrum_stop_event = threading.Event()
        # Optionales Job-Audio (WS3 11.5, params.audio bei IFPAN-Jobs) -
        # eigener UdpPath/Socket/Thread, analog zur Bin-Aufnahme oben,
        # siehe _start_job_audio()/_run_job_audio_loop().
        self._job_audio_sock: Optional[socket.socket] = None
        self._job_audio_thread: Optional[threading.Thread] = None
        self._job_audio_stop: Optional[threading.Event] = None
        # Rohdaten-Session-Aufzeichnung des D-SCAN-Pegelverlaufs (siehe
        # start_session()/stop_session() unten, /record/start|stop|status).
        # Anders als die WAV-Audio-Aufnahme oben rein PASSIV und OHNE SCPI-
        # Bezug - deshalb per direktem Lock statt über die commands-Queue
        # gesteuert (kein Umweg über den Zoom-Loop-Thread nötig).
        self._session_recorder: Optional[SessionStore] = None
        self._session_lock = threading.Lock()
        # Long-Run-Jobs (WS3 Abschnitt 11.5): job_store verwaltet Manifeste/
        # Index/Zustaende (eb200_core/job_store.py), _job_recorder ist der
        # aktive DscanJobRecorder (eb200_core/job_runner.py, passiver
        # Sweep-Abgriff wie _session_recorder oben), _active_job die Kopie
        # des Manifests, die die Empfaenger-Sperre im HTTP-Handler steuert
        # (siehe job_active()). Scheduler-Thread wird in run() gestartet.
        self.job_store = JobStore()
        self._job_recorder: Optional[DscanJobRecorder] = None
        self._active_job: Optional[dict] = None
        self._job_lock = threading.Lock()
        # Scheduler-Diagnostik (T-206): Herzschlag von _job_scheduler_loop,
        # ausgewertet von _sched_watchdog_loop und /api/longrun/status.
        self.sched_health = SchedulerHealth()
        # MSCAN-Jobs (T-1005 Session B): eigener Store fuer die Job-
        # DEFINITIONEN (eb200_core/mscan_job.py, getrennt vom Long-Run-
        # JobStore oben), set_last_run() am Laufende. _mscan_final_state
        # traegt den vom Scheduler/HTTP-Handler gewuenschten Endzustand
        # (final_state, abort_code, abort_detail) fuer den Controller-Loop
        # (_run_mscan_loop(), siehe _stop_active_job()) - der eigentliche
        # Rueckbau darf NUR im Controller-Thread laufen (SCPI).
        self.mscan_store = MscanJobStore()
        self._mscan_final_state: Optional[tuple] = None
        # T-708: zuletzt eingestellte IFPAN-Mittenfrequenz. _run_zoom_loop
        # fuehrt sie bei Eintritt und bei jeder Feinabstimmung ("tune",
        # inline verarbeitet) nach; run() liest sie nach Loop-Ende zurueck,
        # damit ein Zoom-NEUSTART (z. B. Span-/Mode-/BW-Wechsel) nicht auf
        # die veraltete Mittenfrequenz des urspruenglichen Zoom-Klicks
        # zurueckfaellt.
        self.zoom_center_hz: Optional[int] = None

    # -- öffentliche API für die HTTP-Handler --
    def request_zoom(self, center_hz: int) -> None:
        self.commands.put(("zoom", center_hz))

    def request_tune(self, center_hz: int) -> None:
        # T-708: Feinabstimmung - wird im Zoom INLINE verarbeitet (nur
        # FREQ, kein IFPAN-Neuaufbau); ausserhalb des Zooms folgenlos.
        # Kein serverseitiges Koaleszieren noetig: der Browser drosselt
        # selbst und sendet hoechstens alle ~120 ms den letzten Zielwert.
        self.commands.put(("tune", center_hz))

    def request_overview(self) -> None:
        self.commands.put(("overview", None))

    def request_range(self, start_hz: int, stop_hz: int) -> None:
        self.commands.put(("range", (start_hz, stop_hz)))

    def request_speed(self, speed: str) -> None:
        self.commands.put(("speed", speed))

    def request_mtime(self, mtime_s: Optional[float]) -> None:
        self.commands.put(("mtime", mtime_s))

    def request_span(self, span_hz: int) -> None:
        self.commands.put(("span", span_hz))

    def request_mode(self, mode: str) -> None:
        self.commands.put(("mode", mode))

    def request_bw(self, bw_hz: int) -> None:
        self.commands.put(("bw", bw_hz))

    def request_squelch(self, on: bool, thr_dbuv: int) -> None:
        self.commands.put(("squelch", (on, thr_dbuv)))

    def request_gain(self, mode: str, mgc_dbuv: int) -> None:
        self.commands.put(("gain", (mode, mgc_dbuv)))

    def request_dscan_bw_zoom(self, on: bool) -> None:
        self.commands.put(("dscan_bwzoom", on))

    def request_dscan_bw(self, bw_hz: int) -> None:
        self.commands.put(("dscan_bw", bw_hz))

    def request_record_start(self) -> None:
        self.commands.put(("record_start", None))

    def request_record_stop(self) -> None:
        self.commands.put(("record_stop", None))

    def request_monitor_start(self) -> None:
        # SOLL setzen UND Kommando schicken: das Flag wirkt beim naechsten
        # Zoom-Eintritt, das Kommando sofort, falls der Zoom schon laeuft.
        self.monitor_wanted = True
        self.commands.put(("monitor_start", None))

    def request_monitor_check(self) -> None:
        """Vom /audio/stream-Handler, wenn ein Client abreisst: das Kommando
        prueft ERST im Controller-Thread, ob wirklich kein Hoerer mehr da ist
        (ein Seiten-Reload schliesst den alten Stream oft erst NACH dem
        Oeffnen des neuen - ein sofortiges Stop traefe den neuen Client)."""
        self.commands.put(("monitor_check", None))

    def request_spectrum_record_start(self) -> None:
        self.commands.put(("spectrum_start", None))

    def request_spectrum_record_stop(self) -> None:
        # Event VOR dem Enqueue loeschen (noch synchron im aufrufenden Thread) -
        # verhindert ein Race mit einem Loop-Thread, der die Verarbeitung
        # schneller abschliesst, als der Aufrufer stop_spectrum_recording_and_wait()
        # erreicht (siehe dort, T-803 Teil D).
        self._spectrum_stop_event.clear()
        self.commands.put(("spectrum_stop", None))

    def stop_spectrum_recording_and_wait(self, timeout: float = 2.0) -> dict:
        """
        Stoppt die laufende Ad-hoc-Bin-Aufnahme und wartet BEGRENZT (Standard
        2 s, Muster LIVESTATUS_TIMEOUT_S in manage_jobs.py) auf den Abschluss
        der Kommando-Verarbeitung im Loop-Thread (_stop_spectrum_recording),
        damit der finale .specbin-Pfad (samt Sidecar-JSON, erst nach
        writer.close() vollstaendig) fuer den Auswertung-Link (T-803) bereit-
        steht. Laeuft das Timeout ab, kehrt die Methode trotzdem zurueck - der
        Link ist reiner Komfort, kein Funktionsbestandteil; ohne rechtzeitigen
        Pfad liefert die Antwort einfach kein specbin_path (Client zeigt dann
        keinen Link an, siehe /api/record-spectrum). Direkt aus dem HTTP-
        Handler-Thread aufrufbar, analog zu start_session()/stop_session().
        """
        self.request_spectrum_record_stop()
        self._spectrum_stop_event.wait(timeout=timeout)
        result = {"ok": True}
        if self._last_spectrum_path:
            result["specbin_path"] = self._last_spectrum_path.replace(os.sep, "/")
        return result

    def request_ifpan_job_start(self, manifest: dict) -> None:
        self.commands.put(("job_ifpan_start", manifest))

    def request_ifpan_job_cleanup(self) -> None:
        self.commands.put(("job_ifpan_cleanup", None))

    # -- Rohdaten-Session-Aufzeichnung (siehe eb200_core/session_store.py) --
    # Rein passiver Abgriff ohne SCPI-Bezug - deshalb DIREKT aufrufbar (kein
    # Umweg über die commands-Queue/den Loop-Thread wie bei request_record_*
    # oben, das ist dort nötig, weil die WAV-Audio-Aufnahme SCPI im Zoom-Loop-
    # Thread senden muss). start_session()/stop_session() werden unmittelbar
    # aus dem HTTP-Handler-Thread heraus aufgerufen (siehe /record/start|stop).
    def start_session(self) -> dict:
        """Startet die Session-Aufzeichnung, falls noch keine läuft (sonst
        No-Op - liefert den Status der bereits laufenden Aufzeichnung)."""
        with self._session_lock:
            if self._session_recorder is not None:
                return self._session_recorder.status()
            session_path = os.path.join(
                paths.DATA_ROOT, SESSION_DIR, f"session_{time.strftime('%Y%m%d_%H%M%S')}.db"
            )
            recorder = SessionStore(session_path, source="waterfall", mode="DSCAN")
            self._session_recorder = recorder
            return recorder.status()

    def stop_session(self) -> dict:
        """Beendet eine laufende Session-Aufzeichnung. Nimmt die Referenz
        ZUERST unter Lock heraus, ruft close() ERST DANACH außerhalb des
        Locks auf - ein länger dauernder WAL-Checkpoint (siehe
        SessionStore.close()) soll den Lock nicht blockieren."""
        with self._session_lock:
            recorder = self._session_recorder
            self._session_recorder = None
        if recorder is None:
            return {"recording": False}
        recorder.close()
        status = recorder.status()  # nach close(): recording=False, Zähler final
        # T-802: Datenwurzel-relativer Pfad mit Vorwaertsschraegstrichen fuer den
        # Deep-Link zu :8020 (Muster to_relative() in analyze_spectrum.py).
        # status()["path"] ist seit T-2003 absolut (DATA_ROOT/SESSION_DIR);
        # to_portable() liefert daraus wieder "sessions/<datei>.db".
        status["db_path"] = to_portable(os.path.abspath(status["path"]), paths.DATA_ROOT)
        return status

    def session_status(self) -> dict:
        """Für GET /record/status - Status der aktuell laufenden Aufzeichnung,
        oder {"recording": False}, falls keine läuft."""
        with self._session_lock:
            recorder = self._session_recorder
        return recorder.status() if recorder is not None else {"recording": False}

    # -- Long-Run-Jobs (WS3 11.5, siehe eb200_core/job_store.py, job_runner.py) --

    def job_active(self) -> Optional[dict]:
        """Threadsichere Kopie der Manifest-Eckdaten des aktuell laufenden
        Long-Run-Jobs, oder None, wenn keiner aktiv ist. EINE Quelle sowohl
        für die Empfänger-Sperre im HTTP-Handler als auch für GET
        /api/longrun/status (Konsistenz zwischen Sperre und Anzeige)."""
        with self._job_lock:
            manifest = self._active_job
            recorder = self._job_recorder
        if manifest is None:
            return None
        # current_stats() statt recorder.stats direkt lesen: frames/bytes
        # werden intern nur bei Heartbeat/close() geschrieben, current_stats()
        # rechnet sie live nach (siehe eb200_core/job_runner.py).
        stats = dict(recorder.current_stats()) if recorder is not None else dict(manifest.get("stats", {}))
        if recorder is None and manifest["type"] == "mscan":
            # Zeitfenster zwischen Scheduler (_start_mscan_job setzt
            # _active_job) und Loop-Eintritt (_run_mscan_loop legt den
            # Recorder erst dort an, siehe Nachtrag 1 T-1005 B) - der Job
            # gilt schon als aktiv (Sperre), es gibt aber noch keine
            # Recorder-Stats. Ohne "phase" wuerde das Banner faelschlich
            # den letzten else-Zweig ("finishing ...") zeigen.
            stats["phase"] = "wartet"
        params = manifest.get("params", {})
        return {
            "job_id": manifest["job_id"],
            "type": manifest["type"],
            "label": manifest["label"],
            "start_hz": params.get("start_hz"),
            "stop_hz": params.get("stop_hz"),
            # Nur für type == "ifpan" gesetzt (None bei "dscan") - für die
            # Banner-Anzeige in der Zoom-Ansicht (Schritt 4c).
            "center_hz": params.get("center_hz"),
            "span_hz": params.get("span_hz"),
            "demod": params.get("demod"),
            # Nur fuer type == "mscan" gesetzt (None/0 bei dscan/ifpan,
            # deren params-Dict diese Schluessel nicht kennt) - T-1005
            # Session B. Die Recorder-Stats (phase/programmed/cycles/
            # items/recordings/active_channels/bytes) stecken bereits in
            # "stats" (siehe MscanJobRecorder.current_stats()).
            "mscan_job_id": params.get("mscan_job_id"),
            "title": params.get("title"),
            "mode": params.get("mode"),
            "channel_count": len(params.get("channels", [])),
            "start_iso": manifest["start_iso"],
            "end_iso": manifest["end_iso"],
            "stats": stats,
            "files": list(stats.get("files", manifest.get("files", []))),
        }

    def longrun_status(self) -> dict:
        """Für GET /api/longrun/status."""
        active = self.job_active()
        return {"active": active is not None, "job": active,
                "scheduler": self.sched_health.snapshot()}

    def _start_dscan_job(self, manifest: dict) -> None:
        """
        Startet die Ausführung eines D-SCAN-Long-Run-Jobs (WS3 11.5, wird
        aus _job_scheduler_loop heraus aufgerufen). Wendet die
        eingefrorenen Empfänger-Parameter an und legt den
        DscanJobRecorder an; ab hier greift die Empfänger-Sperre im
        HTTP-Handler (job_active() liefert nicht mehr None).

        request_overview()/request_range()/request_speed() laufen über die
        BESTEHENDE commands-Queue - genau dieselben Requests, die auch ein
        Browser auslösen würde. Die Empfänger-Sperre sitzt bewusst NUR im
        HTTP-Handler (Schritt 5), NICHT hier im Controller: der Controller
        muss seine eigenen, durch den Job ausgelösten Requests jederzeit
        stellen können, auch während "sein eigener" Job als aktiv gilt.
        """
        with self._job_lock:
            self._active_job = manifest

        params = manifest["params"]
        self.request_overview()
        self.request_range(int(params["start_hz"]), int(params["stop_hz"]))
        if params.get("speed"):
            self.request_speed(params["speed"])
        if params.get("speed") == "MTIM":
            # mtime_ms None -> DEFAULT (request_mtime(None) sendet
            # MEAS:TIME DEF, siehe _configure_dscan).
            mtime_ms = params.get("mtime_ms")
            self.request_mtime(mtime_ms / 1000.0 if mtime_ms is not None else None)

        recorder = DscanJobRecorder(self.job_store, manifest)
        with self._job_lock:
            self._job_recorder = recorder

        self.job_store.set_state(manifest["job_id"], ST_LAEUFT)
        print(f"[jobs] Job {manifest['job_id']} gestartet ({manifest['label']!r}, "
              f"{params.get('start_hz')}-{params.get('stop_hz')} Hz)")

    def _start_ifpan_job(self, manifest: dict) -> None:
        """
        Startet die Ausführung eines IFPAN-Long-Run-Jobs (WS3 11.2/11.5,
        Schritt 4c; wird wie _start_dscan_job aus _job_scheduler_loop
        heraus aufgerufen - also aus dem Scheduler-Thread, NICHT dem
        Controller-Thread).

        Anders als _start_dscan_job darf hier KEIN SCPI direkt gesendet
        werden (Zoom-Konfiguration + Tag-Registrierung brauchen mehrere,
        aufeinander aufbauende SCPI-Befehle im Controller-Thread) - deshalb
        wird nur self._active_job gesetzt (das reicht, damit job_active()
        sofort die Empfänger-Sperre greifen lässt) und die eigentliche
        Ausführung als EIN Kommando über die Queue an den Controller-Thread
        übergeben (siehe _start_ifpan_job_scpi(), aufgerufen aus
        _run_zoom_loop). request_mode/bw/span/zoom laufen VOR
        request_ifpan_job_start in derselben Queue - FIFO-Reihenfolge
        garantiert, dass _configure_ifpan (ausgelöst durch den zoom-
        Request) bereits die neuen ifpan_mode/ifpan_bw_hz/zoom_span_hz-
        Werte sieht, wenn job_ifpan_start verarbeitet wird.
        """
        with self._job_lock:
            self._active_job = manifest

        params = manifest["params"]
        self.request_mode(params["demod"])
        self.request_bw(int(params["bw_hz"]))
        self.request_span(int(params["span_hz"]))
        self.request_zoom(int(params["center_hz"]))
        self.request_ifpan_job_start(manifest)

    def _start_ifpan_job_scpi(self, manifest: dict) -> None:
        """
        Controller-Thread-Teil von _start_ifpan_job() (Kommando
        "job_ifpan_start", verarbeitet in _run_zoom_loop NACH
        _configure_ifpan mit den bereits übernommenen eingefrorenen
        Parametern). Legt den IfpanJobRecorder an und startet die
        Bin-Aufnahme auf UDP_PORT_REC mit diesem Recorder als Datensenke
        (statt des Ad-hoc-SpectrumBinWriter) - _start_bin_recording()
        übernimmt SCPI/Socket/Thread unverändert (siehe dort), der einzige
        Unterschied ist job_recorder=recorder statt None.

        Scheitert die Bin-Aufnahme (Port belegt o. Ä.), wird der Job
        sofort als abgebrochen markiert und _active_job/_job_recorder
        zurückgerollt - kein halb gestarteter Job, der die Empfänger-
        Sperre auf Dauer hält, ohne je Daten zu schreiben.
        """
        params = manifest["params"]
        recorder = IfpanJobRecorder(self.job_store, manifest)
        with self._job_lock:
            self._job_recorder = recorder

        ok = self._start_bin_recording(int(params["bw_hz"]), params["demod"], job_recorder=recorder)
        if not ok:
            with self._job_lock:
                self._active_job = None
                self._job_recorder = None
            self.job_store.set_state(manifest["job_id"], ST_ABGEBROCHEN,
                                      abort_code="bin_record_failed")
            print(f"[jobs] IFPAN-Job {manifest['job_id']} abgebrochen: "
                  f"Bin-Aufnahme fehlgeschlagen", file=sys.stderr)
            return

        # Job-Audio (optional, WS3 11.5): Fehler hier duerfen den Job NICHT
        # abbrechen (die Bins sind der Hauptzweck) - siehe _start_job_audio().
        if params.get("audio"):
            self._start_job_audio(manifest, recorder)

        self.job_store.set_state(manifest["job_id"], ST_LAEUFT)
        print(f"[jobs] IFPAN-Job {manifest['job_id']} gestartet ({manifest['label']!r}, "
              f"{params.get('center_hz')} Hz ± {params.get('span_hz')}/2 Hz, {params.get('demod')})")

    def _start_mscan_job(self, manifest: dict) -> None:
        """
        Startet die Ausfuehrung eines MSCAN-Long-Run-Jobs (T-1005 Session B,
        wird wie _start_dscan_job()/_start_ifpan_job() aus
        _job_scheduler_loop heraus aufgerufen - also aus dem Scheduler-
        Thread, NICHT dem Controller-Thread).

        Anders als bei D-SCAN/IFPAN passiert HIER noch KEIN SCPI: das
        gesamte Programmieren der Speicherplaetze UND der Scan selbst
        laufen als EIGENER Controller-Modus (_run_mscan_loop(), siehe
        run()) - dafuer wird nur self._active_job gesetzt (das reicht,
        damit job_active() sofort die Empfaenger-Sperre greifen laesst)
        und "job_mscan_start" ueber die commands-Queue an den Controller-
        Thread uebergeben.

        NACHTRAG 1 (T-1005 B): self._job_recorder bleibt hier ABSICHTLICH
        None - waehrend die Uebersicht/der Zoom noch laeuft (bis das
        Kommando im Controller-Thread ankommt), liest deren Sweep-Pfad
        _job_recorder ebenfalls und ging bisher von einem DscanJobRecorder
        aus (AttributeError 'MscanJobRecorder' object has no attribute
        'record', Controller-Thread stirbt). Der MscanJobRecorder wird
        deshalb erst in _run_mscan_loop() selbst angelegt (Controller-
        Thread, siehe dort) - job_active() liefert bis dahin ueber
        _job_recorder is None + type mscan einen Wartezustand (phase
        "wartet", siehe dort).
        """
        with self._job_lock:
            self._active_job = manifest
        self.commands.put(("job_mscan_start", manifest))

    def _cleanup_ifpan_job_scpi(self) -> None:
        """
        Controller-Thread-Teil des IFPAN-Job-Stopps (Kommando
        "job_ifpan_cleanup", ausgelöst aus _stop_active_job()). Räumt SCPI-
        seitig auf (Bin-Aufnahme-Tag + ggf. Job-Audio-Tag) und schickt das
        Gerät zurück in die D-SCAN-Übersicht. Die NICHT-SCPI-Anteile
        (Threads/Sockets/Recorder.close()) sind zu diesem Zeitpunkt
        bereits synchron in _stop_active_job() erledigt (siehe dort) -
        hier bleibt nur noch der Geräte-Rückbau.

        spectrum_recording wird an dieser Stelle bereits False sein (siehe
        _stop_bin_recording_local()) - das TAG:OFF/DELete unten muss daher
        UNBEDINGT ausgeführt werden, nicht über die spectrum_recording-
        Prüfung von _stop_spectrum_recording() (die würde hier fälschlich
        No-Op sein).
        """
        try:
            self.scpi.send(f'TRAC:UDP:TAG:OFF "{self.local_ip}",{UDP_PORT_REC},IFP')
            self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{UDP_PORT_REC}')
        except Exception as exc:
            print(f"[controller] Aufräumen der Bin-Aufnahme fehlgeschlagen: {exc!r}", file=sys.stderr)
        self._stop_job_audio_scpi()
        self.request_overview()

    def _stop_active_job(self, final_state: str, abort_code: Optional[str] = None,
                         abort_detail: Optional[str] = None) -> None:
        """Beendet den aktuell aktiven Long-Run-Job. Nimmt die Referenzen
        ZUERST unter Lock heraus, ruft recorder.close() ERST DANACH
        außerhalb des Locks auf (Muster stop_session: ein länger dauernder
        Datei-/Sidecar-Abschluss soll job_active()/die HTTP-Sperre nicht
        blockieren). Wird aus dem Scheduler-Thread ODER dem HTTP-Handler-
        Thread aufgerufen (longrun_stop()) - NIE aus dem Controller-Thread.

        Für type == "ifpan" (Schritt 4c): die NICHT-SCPI-Anteile
        (Empfangs-Threads/Sockets von Bin-Aufnahme + Job-Audio) werden HIER
        SYNCHRON gestoppt (das ist reines Python, aus jedem Thread sicher,
        siehe _stop_bin_recording_local()/_stop_job_audio_local()) - so
        sieht recorder.close() garantiert keine nachlaufenden record()-
        Aufrufe mehr. Der SCPI-Rückbau (Tags aus, Gerät zurück in die
        Übersicht) darf dagegen NUR im Controller-Thread laufen und wird
        deshalb asynchron über request_ifpan_job_cleanup() angestoßen.

        Für type == "mscan" (T-1005 Session B): HIER passiert GAR NICHTS
        ausser dem Anstossen - anders als bei ifpan gibt es keine vom
        Controller-Loop unabhaengigen Empfangs-Threads/Sockets, die schon
        synchron gestoppt werden koennten (Programmieren UND Scan laufen
        komplett INNERHALB von _run_mscan_loop() im Controller-Thread).
        _active_job/_job_recorder bleiben deshalb ABSICHTLICH gesetzt (die
        423-Sperre bleibt aktiv) - "job_mscan_stop" weckt den Loop, der
        SCPI-Rueckbau, recorder.close(), job_store.set_state() UND das
        Loeschen von _active_job/_job_recorder passieren erst dort (finally
        von _run_mscan_loop()). Sperre darf erst nach dem Geraete-Rueckbau
        fallen, nicht schon hier.
        """
        with self._job_lock:
            manifest = self._active_job
            recorder = self._job_recorder
            if manifest is not None and manifest["type"] != "mscan":
                self._active_job = None
                self._job_recorder = None
        if manifest is None:
            return
        if manifest["type"] == "mscan":
            self._mscan_final_state = (final_state, abort_code, abort_detail)
            self.commands.put(("job_mscan_stop", (final_state, abort_code, abort_detail)))
            return
        if manifest["type"] == "ifpan":
            self._stop_bin_recording_local()
            self._stop_job_audio_local()
        stats = recorder.close() if recorder is not None else {}
        self.job_store.set_state(manifest["job_id"], final_state,
                                 abort_code=abort_code, abort_detail=abort_detail)
        if manifest["type"] == "ifpan":
            self.request_ifpan_job_cleanup()
        print(f"[jobs] Job {manifest['job_id']} beendet -> {final_state} "
              f"({stats.get('frames', 0)} Frames)")

    def longrun_stop(self) -> dict:
        """Für POST /api/longrun/stop: beendet den aktiven Job ordentlich
        (ST_GESTOPPT). Kein aktiver Job -> {"active": False} (kein
        Fehlerfall - ein Stop-Klick auf eine bereits leere Übersicht ist
        normal, kein Ausnahmezustand)."""
        with self._job_lock:
            manifest = self._active_job
        if manifest is None:
            return {"active": False}
        job_id = manifest["job_id"]
        self._stop_active_job(ST_GESTOPPT)
        return {"active": False, "job_id": job_id}

    def _job_scheduler_loop(self) -> None:
        """
        Daemon-Thread, 30-s-Takt (WS3 11.5): startet fällige geplante Jobs
        (D-SCAN ODER IFPAN, siehe _start_dscan_job()/_start_ifpan_job()),
        beendet den aktiven Job bei Erreichen von end_iso oder bricht ihn
        bei einem Aufzeichnungsfehler ab (recorder.failed). Das Ende-/
        Abbruch-Handling ist für beide Typen IDENTISCH (_stop_active_job()
        verzweigt selbst nach manifest["type"], siehe dort).
        Meldet sich bei sched_health (T-206) mit Phasennamen, damit der
        Wächter-Thread einen Hänger von normaler Untätigkeit unterscheiden
        kann (siehe _sched_watchdog_loop).
        """
        while not self.stop_event.is_set():
            self.sched_health.tick_start("check")
            try:
                with self._job_lock:
                    active_manifest = self._active_job
                    recorder = self._job_recorder

                if active_manifest is not None:
                    if recorder is not None and recorder.failed:
                        self.sched_health.tick_start("stop_aborted", active_manifest["job_id"])
                        self._stop_active_job(ST_ABGEBROCHEN,
                                              abort_code=recorder.failed_code or "recording_error",
                                              abort_detail=recorder.failed)
                    else:
                        end_iso = active_manifest.get("end_iso")
                        if end_iso is not None and \
                                datetime.fromisoformat(end_iso) <= datetime.now().astimezone():
                            self.sched_health.tick_start("stop_finished", active_manifest["job_id"])
                            self._stop_active_job(ST_BEENDET)
                else:
                    now = datetime.now().astimezone()
                    for entry in self.job_store.list_jobs():
                        if entry["state"] != ST_GEPLANT:
                            continue
                        if datetime.fromisoformat(entry["start_iso"]) > now:
                            continue
                        manifest = self.job_store.get(entry["job_id"])
                        if entry["type"] == "dscan":
                            self.sched_health.tick_start("start_dscan", entry["job_id"])
                            self._start_dscan_job(manifest)
                        elif entry["type"] == "ifpan":
                            self.sched_health.tick_start("start_ifpan", entry["job_id"])
                            self._start_ifpan_job(manifest)
                        elif entry["type"] == "mscan":
                            self.sched_health.tick_start("start_mscan", entry["job_id"])
                            self._start_mscan_job(manifest)
                        else:
                            print(f"[jobs] unbekannter Job-Typ {entry['type']!r} "
                                  f"({entry['job_id']}) übersprungen", file=sys.stderr)
                            continue
                        break   # ein Job pro Durchlauf - "ein Empfänger, ein Job"
            except Exception as exc:
                self.sched_health.error("scheduler_exception", repr(exc))
                print(f"[jobs] Scheduler-Fehler: {exc!r}", file=sys.stderr)

            # snapshot() VOR tick_end(): in_tick_s zaehlt nur, solange der
            # Tick noch laeuft (siehe SchedulerHealth) - nach tick_end() waere
            # die gerade beendete Tick-Dauer sonst schon wieder None.
            snap = self.sched_health.snapshot()
            langsam = self.sched_health.tick_end()
            if langsam:
                print(f"[jobs] Scheduler-Takt langsam: {snap['in_tick_s']:.1f} s "
                      f"in Phase {snap['phase']}", file=sys.stderr)

            self.stop_event.wait(30.0)

    def _sched_watchdog_loop(self) -> None:
        """
        Daemon-Thread (T-206): prueft alle 30 s per sched_health.should_dump(),
        ob der Scheduler-Thread (_job_scheduler_loop) seit freeze_s ohne
        Herzschlag ist, und schreibt dann EINMAL je Freeze-Episode einen
        Thread-Dump nach logs/freeze_<Zeitstempel>.txt (dump_threads,
        faulthandler.dump_traceback). Hilft NICHT, wenn faulthandler selbst
        blockiert ist (z. B. ein GIL-Deadlock, der auch den Dump verhindert) -
        dafuer stehen zusaetzlich faulthandler.enable() (Fatal-Fehler ->
        stderr, siehe main()) und der manuelle Endpunkt /api/debug/threads
        (siehe do_GET) bereit.
        """
        while not self.stop_event.is_set():
            self.stop_event.wait(30.0)
            snap = self.sched_health.snapshot()
            if self.sched_health.should_dump():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = paths.data_path("logs", f"freeze_{ts}.txt")
                header = (f"Scheduler-Freeze erkannt {ts}: age_s={snap['age_s']:.1f} "
                          f"Phase={snap['phase']!r} ticks={snap['ticks']} "
                          f"slow_ticks={snap['slow_ticks']} last_tick_iso={snap['last_tick_iso']} "
                          f"last_error={snap['last_error']!r}")
                dump_threads(path, header)
                print(f"[jobs] Scheduler ohne Lebenszeichen seit {snap['age_s']:.1f} s "
                      f"(Phase {snap['phase']}) - Thread-Dump: {path}", file=sys.stderr)

    # -- SCPI-Konfiguration je Modus --
    def _delete_udp_path(self) -> None:
        # Räumt einen ggf. noch vorhandenen UdpPath auf diesem Port weg,
        # bevor der nächste Modus registriert wird (CMOS-Persistenz, Annex F).
        self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{self.udp_port}')

    def _drain_udp(self, anlass: str) -> int:
        """
        T-716: Leert den UDP-Empfangspuffer und gibt die Zahl der verworfenen
        Datagramme zurueck. Darf NUR an Stellen aufgerufen werden, an denen
        alles im Puffer per Definition veraltet ist - konkret zwischen ABOR
        und INIT:IMM (siehe _configure_dscan). Ein Aufruf im laufenden Sweep
        wuerde gueltige Messdaten wegwerfen.

        Der Socket wird dafuer kurz auf nicht-blockierend gestellt und danach
        unbedingt (finally) auf RECV_TIMEOUT_S zurueckgesetzt - ein hier
        haengengebliebener Timeout wuerde die Empfangsschleife blockieren.
        """
        if self.udp_sock is None:
            return 0
        # Nach ABOR koennen noch Pakete unterwegs sein (Geraet, Switch, NIC).
        # Ohne diese kurze Wartezeit landen sie erst NACH dem Leeren im Puffer
        # und der Versatz bliebe genau so bestehen wie zuvor.
        time.sleep(DRAIN_SETTLE_S)
        verworfen = 0
        self.udp_sock.settimeout(0.0)
        try:
            while verworfen < DRAIN_MAX_DATAGRAMS:
                try:
                    self.udp_sock.recvfrom(65535)
                except (BlockingIOError, socket.timeout):
                    break
                except OSError as exc:
                    print(f"[controller] UDP-Puffer leeren abgebrochen ({exc!r})",
                          file=sys.stderr)
                    break
                verworfen += 1
        finally:
            self.udp_sock.settimeout(RECV_TIMEOUT_S)
        if verworfen:
            # Bewusst KEINE Drosselung: die Zeile erscheint nur bei einem
            # Neuaufbau, nicht im laufenden Betrieb - und sie ist der Beleg
            # dafuer, dass der Versatz genau hier entstanden waere.
            print(f"[controller] {verworfen} veraltete D-SCAN-Datagramm(e) vor "
                  f"{anlass} verworfen (haetten den Sweep-Anfang verschoben)")
        return verworfen

    def _configure_dscan(self) -> None:
        self._delete_udp_path()
        # Demodulation IMMER auf FM zurücksetzen (*RST-Default), BEVOR die
        # D-SCAN-Parameter gesetzt werden: Bei SSB-Betriebsarten (CW/LSB/USB)
        # lehnt das Gerät jede Bandbreite > 9 kHz mit -221 "Settings conflict;
        # Bandwidth too high with SSB" ab (Manual, SENSe:DEModulation) - der
        # Rücksprung aus einem SSB-IFPAN-Zoom ließ so BAND und in der Folge
        # auch STAR/STOP auf Altwerten hängen. Bedingungslos (nicht nur bei
        # self.ifpan_mode in CW/LSB/USB), damit auch eine am Frontpanel
        # gewählte SSB-Demodulation den Sweep nie blockieren kann. Die
        # Zoom-Auswahl bleibt erhalten: _configure_ifpan() setzt
        # DEM {self.ifpan_mode} beim nächsten Zoom ohnehin neu.
        self.scpi.send("DEM FM")
        # BW-ZOOM immer aus einem definierten Zustand aufbauen: zuerst
        # entkoppeln (RES:AUTO OFF), damit die folgenden STAR/STOP sicher
        # greifen. So speichert das Gerät beim späteren Einschalten stets
        # den vollen Bereich als "alten" Span (der beim Ausschalten
        # wiederhergestellt wird) - unabhängig vom vorherigen BW-ZOOM-Zustand.
        self.scpi.send("FREQ:DSC:RES:AUTO OFF")
        self.scpi.send(f"FREQ:DSC:STAR {self.dscan_start_hz}")
        self.scpi.send(f"FREQ:DSC:STOP {self.dscan_stop_hz}")
        self.scpi.send(f"FREQ:DSC:SPE {self.dscan_speed}")

        # MEASURE TIME nur bei MTIM senden: der Parameter ist am Geraet
        # GLOBAL (betrifft auch andere Messfunktionen, Handbuch 3.18.3.1);
        # beim Hardware-Sweep (LOW/NORM/HIGH) wird er ignoriert und soll
        # deshalb auch nicht veraendert werden. Abweichende Werte von
        # DEFAULT setzen laut Handbuch einen ZF-Teil mit DDC2 voraus - der
        # Readback unten deckt eine stille Ablehnung auf.
        if self.dscan_speed == "MTIM":
            if self.dscan_mtime_s is None:
                # DEFAULT: Geraet waehlt die Messzeit selbst (Annex E).
                self.scpi.send("MEAS:TIME DEF")
            else:
                self.scpi.send(f"MEAS:TIME {self.dscan_mtime_s}")
            if VERIFY_SCPI and self.dscan_mtime_s is not None:
                try:
                    rb = float(self.scpi.query("MEAS:TIME?"))
                    # relative Toleranz 1 % + absolut 0.1 ms gegen Geraete-
                    # Quantisierung; kein scpi_verify-Check, da dessen
                    # Vergleicher auf Frequenz/BW/Enum zugeschnitten sind.
                    if abs(rb - self.dscan_mtime_s) > max(0.0001, 0.01 * self.dscan_mtime_s):
                        print(f"[controller] MEAS:TIME abweichend: gesetzt "
                              f"{self.dscan_mtime_s} s, gelesen {rb} s")
                except Exception as exc:
                    print(f"[controller] MEAS:TIME-Readback übersprungen ({exc!r})",
                          file=sys.stderr)
        else:
            # Invariante: eine numerische MEASURE TIME darf NUR bei Speed
            # MTIM am Geraet stehen. MEAS:TIME ist global (Handbuch
            # 3.18.3.1) - ein bei MTIM gesetzter Wert wuerde sonst nach dem
            # Rueckschalten auf LOW/NORM/HIGH stillschweigend in andere
            # Messfunktionen hineinwirken. Deshalb bei jedem Hardware-Sweep
            # aktiv auf DEFAULT zuruecksetzen (idempotent, kein Readback
            # noetig - DEF ist immer gueltig, auch ohne DDC2).
            self.scpi.send("MEAS:TIME DEF")

        # Annex J: bei D-SCAN entsteht alle ~halbe IF-Bandbreite ein
        # Messpunkt - die BW ist damit der Tempo-/Auflösungsregler des
        # Sweeps (größere BW -> größere Schritte -> weniger Punkte pro
        # Sweep -> schnellerer Sweep), UNABHÄNGIG von BW-ZOOM. Deshalb immer
        # setzen, nicht mehr nur innerhalb des BW-ZOOM-Zweigs.
        self.scpi.send(f"BAND {self.dscan_bw_hz}")

        # BW-ZOOM: Gerät koppelt zusätzlich den Span an die IF-Bandbreite
        # (ein Kanal = halbe BW) rund um den Marker. Marker hier auf die
        # Mitte des eingestellten Bereichs setzen - einfacher, immer
        # gültiger Default.
        if self.dscan_bw_zoom:
            marker_hz = (self.dscan_start_hz + self.dscan_stop_hz) // 2
            self.scpi.send(f"FREQ:DSC:MARK {marker_hz}")
            self.scpi.send("FREQ:DSC:RES:AUTO ON")

        # Verifikation nur OHNE BW-ZOOM (Gerät rechnet den schmalen Bereich
        # bei aktivem BW-ZOOM selbst aus -> Rücklese-Check würde Fehlalarme melden).
        if VERIFY_SCPI and not self.dscan_bw_zoom:
            # Rücklese-Verifikation der drei D-SCAN-Parameter (Set wird von
            # verify() nochmals mitgeschickt, siehe scpi_verify.Check). Bei
            # Abweichung NICHT fatal - nur loggen und den Sweep trotzdem
            # starten. Zusätzlich try/except um verify() selbst: ein SCPI-
            # Timeout beim Rücklesen darf den Live-Wasserfall nie zum
            # Absturz bringen (scpi_verify fängt nur Fehler beim finalen
            # Query je Check ab, nicht bei der Fehler-Queue-Abfrage).
            checks = [
                freq_check("D-SCAN Start", "FREQ:DSC:STAR", self.dscan_start_hz),
                freq_check("D-SCAN Stop",  "FREQ:DSC:STOP", self.dscan_stop_hz),
                enum_check("D-SCAN Speed", "FREQ:DSC:SPE",  self.dscan_speed),
                # BW wird auf eine der zwölf ZF-Filterstufen quantisiert -
                # cmp_bw() rundet den erwarteten Wert entsprechend, bevor
                # verglichen wird (siehe scpi_verify.nearest_bw_stage).
                bw_check("D-SCAN BW", "BAND", self.dscan_bw_hz),
            ]
            try:
                report = verify(self.scpi, checks)
                if not report.all_ok:
                    print("[controller] " + format_report(report).replace("\n", "\n[controller] "))
                    # Denselben Befund in den Browser - in der Konsole allein
                    # blieb ein abgelehntes Set (z. B. "Span too low") un-
                    # bemerkt, waehrend der Sweep still mit dem alten Bereich
                    # weiterlief. Kompakt: nur die nicht-OK-Zeilen.
                    self.broker.publish_notice(
                        "Device did not accept the parameters: " + _notice_from_report(report),
                        level="warn")
            except Exception as exc:
                print(f"[controller] SCPI-Verifikation übersprungen ({exc!r})", file=sys.stderr)

        self.scpi.send(f'TRAC:UDP:DEF:TAG:ON "{self.local_ip}",{self.udp_port},DSC')
        self.scpi.send(f'TRAC:UDP:DEF:FLAG:ON "{self.local_ip}",{self.udp_port},"VOLT:AC","OPT"')
        self.scpi.send("FREQ:MODE DSC")
        # T-711: Verstaerkungsregelung ERST NACH "FREQ:MODE DSC" und VOR
        # "INIT:IMM" setzen - dieselbe Reihenfolge wie in _configure_ifpan()
        # (dort: FREQ:MODE CW, dann Einstellungen, dann Feed/Start). In
        # T-710 stand der Aufruf noch vor der Betriebsartumschaltung; am
        # Geraet kam die Umschaltung nicht an (Befund OE3GAS 2026-09-03).
        self._apply_gain()
        if VERIFY_SCPI:
            # Ruecklese-Beleg fuer die Verstaerkung. GCON:MODE? antwortet
            # laut Handbuch 4.79 mit FIX oder AUTO (nicht MGC/AGC), deshalb
            # kein enum_check aus scpi_verify, sondern ein eigener Vergleich.
            # Nur bei Abweichung melden - sonst stuende bei jedem Bereichs-,
            # BW- oder Speed-Wechsel eine Zeile in der Konsole. Nicht fatal:
            # ein Query-Fehler darf den Sweep-Aufbau nie blockieren (Muster
            # MEAS:TIME-Readback weiter oben).
            try:
                rb_mode = self.scpi.query("GCON:MODE?").strip().upper()
                soll_raw = "FIX" if self.gain_mode == "MGC" else "AUTO"
                abweichung = None
                if not rb_mode.startswith(soll_raw):
                    abweichung = f"mode read {rb_mode}, expected {soll_raw}"
                elif self.gain_mode == "MGC":
                    rb_mgc = int(float(self.scpi.query("GCON:MGC?")))
                    if rb_mgc != self.gain_mgc_dbuv:
                        abweichung = (f"MGC read {rb_mgc} dBuV, "
                                      f"set {self.gain_mgc_dbuv} dBuV")
                if abweichung is not None:
                    print(f"[controller] Verstaerkung nicht uebernommen: {abweichung}")
                    self.broker.publish_notice(
                        "Gain control not accepted: " + abweichung,
                        level="warn")
            except Exception as exc:
                print(f"[controller] GCON-Readback uebersprungen ({exc!r})", file=sys.stderr)
        # T-716: letzter Moment, in dem der Empfangspuffer garantiert nur
        # Altdaten enthaelt - ABOR ist raus, der UdpPath gerade neu
        # registriert, der neue Sweep beginnt erst mit INIT:IMM.
        self._drain_udp("INIT:IMM")
        self.scpi.send("INIT:IMM")

    def _stop_dscan(self) -> None:
        self.scpi.send("ABOR")

    def _configure_ifpan(self, center_hz: int) -> None:
        self._delete_udp_path()
        self.scpi.send("FREQ:MODE CW")
        self.scpi.send(f"FREQ {center_hz}")
        self.scpi.send(f"FREQ:SPAN {self.zoom_span_hz}")
        # Modulation und Bandbreite gemäß aktueller Auswahl in der Zoom-
        # Ansicht setzen (Annex C: [SENSe:]DEModulation, [SENSe:]BANDwidth).
        # Beide Änderungen setzen die Pegeldetektoren zurück (siehe Annex E) -
        # unkritisch hier, da IFPAN ohnehin kontinuierlich neu misst.
        if self.ifpan_mode == "IQ" and self.ifpan_bw_hz > 15000:
            # IQ läuft mit 32 kHz komplexer Abtastung (Mode 1) - eine breitere
            # ZF würde Anteile hineinfalten. 15 kHz ist die größte passende
            # der 12 festen Filterstufen.
            print(f"[controller] DEM IQ: BW {self.ifpan_bw_hz} Hz -> 15000 Hz begrenzt.")
            self.ifpan_bw_hz = 15000
        self.scpi.send(f"DEM {self.ifpan_mode}")
        self.scpi.send(f"BAND {self.ifpan_bw_hz}")
        # T-703: Rueckleseabgleich - DEM?/BAND? liefern den tatsaechlichen
        # Geraetezustand (BAND wird auf die 12 ZF-Filterstufen quantisiert;
        # ein abgelehntes Set - z. B. "Bandwidth too high with SSB" - bliebe
        # sonst unbemerkt). Controller-Zustand nachziehen, damit
        # publish_ifpan() die authentischen Werte an die Browser verteilt.
        # Nicht-fatal: ein Query-Fehler darf den Zoom-Aufbau nie blockieren
        # (gleiches Muster wie der MEAS:TIME-Readback in _configure_dscan).
        try:
            rb_mode = self.scpi.query("DEM?").strip().upper()
            if rb_mode:
                self.ifpan_mode = rb_mode
        except Exception as exc:
            print(f"[controller] DEM?-Readback übersprungen ({exc!r})",
                  file=sys.stderr)
        try:
            rb_bw = int(round(float(self.scpi.query("BAND?"))))
            if rb_bw > 0:
                self.ifpan_bw_hz = rb_bw
        except Exception as exc:
            print(f"[controller] BAND?-Readback übersprungen ({exc!r})",
                  file=sys.stderr)
        self._apply_squelch()   # SOLL bei jedem Zoom-Eintritt herstellen, wie _apply_monitor_wish
        self._apply_gain()      # SOLL bei jedem Zoom-Eintritt herstellen, wie _apply_squelch
        self.scpi.send("DISP:MENU IFPAN")
        self.scpi.send(f'TRAC:UDP:DEF:TAG:ON "{self.local_ip}",{self.udp_port},IFP')
        self.scpi.send(f'TRAC:UDP:DEF:FLAG:ON "{self.local_ip}",{self.udp_port},"VOLT:AC","OPT"')
        self.scpi.send("TRAC:FEED:CONT IFPAN,ALW")

    def _apply_squelch(self) -> None:
        """Setzt Squelch-Schwelle und Ein/Aus am Geraet gemaess aktuellem
        Zustand. Laeuft im Loop-Thread -> SCPI erlaubt; wirkt auf den
        Audio-/AF-Pfad, daher nur im Zoom hoerbar."""
        self.scpi.send(f"OUTP:SQU:THR {self.squelch_thr_dbuv}")
        self.scpi.send(f"OUTP:SQU {'ON' if self.squelch_on else 'OFF'}")

    def _apply_gain(self) -> None:
        """Setzt die Verstaerkungsregelung am Geraet gemaess aktuellem
        Zustand. Laeuft im Loop-Thread -> SCPI erlaubt. Reihenfolge wichtig:
        erst der MGC-Pegel, dann die Umschaltung, damit beim Wechsel nach
        MGC nicht kurzzeitig ein alter Pegel wirkt."""
        if self.gain_mode == "MGC":
            self.scpi.send(f"GCON:MGC {self.gain_mgc_dbuv}")
            self.scpi.send("GCON:MODE MGC")
        else:
            self.scpi.send("GCON:MODE AGC")

    def _stop_ifpan(self) -> None:
        self.scpi.send("TRAC:FEED:CONT IFPAN,NEV")
        self.scpi.send("DISP:MENU DEFAULT")

    def _current_audio_mode(self) -> int:
        """Audio-Remote-Modus passend zur aktuellen Demodulation: bei IQ der
        Zweikanal-Modus (I/Q auf den beiden PCM-Kanälen), sonst der normale
        Mono-Modus. Zentrale Stelle - _audio_tag_on() und _start_recording()
        müssen zwingend denselben Wert sehen, sonst passt der WAV-Header
        nicht zum Datenstrom."""
        return AUDIO_REMOTE_MODE_IQ if self.ifpan_mode == "IQ" else AUDIO_REMOTE_MODE

    # -- AUD-Tag auf dem Display-UdpPath: von Aufnahme UND Mithoeren geteilt --
    def _audio_tag_on(self) -> None:
        """
        Schaltet Audio-Remote-Modus und den AUD-Tag auf dem Display-UdpPath
        ein - aber nur beim ERSTEN Nutzer. WAV-Aufnahme (self.recording) und
        Mithoeren (self.monitoring) teilen sich Tag und Pfad (kein SWAP-Flag,
        siehe _start_recording); doppeltes TAG:ON waere am Geraet zwar
        folgenlos, doppeltes TAG:OFF wuerde dem jeweils anderen aber den
        Strom abdrehen. Der Aufrufer setzt sein Flag ERST NACH diesem Aufruf.
        """
        if self.recording or self.monitoring:
            return  # Tag laeuft bereits fuer den anderen Nutzer
        self.scpi.send(f"SYST:AUD:REM:MOD {self._current_audio_mode()}")
        self.scpi.send(f'TRAC:UDP:DEF:TAG:ON "{self.local_ip}",{self.udp_port},AUD')

    def _audio_tag_off(self) -> None:
        """
        Gegenstueck zu _audio_tag_on(): Tag erst abschalten, wenn weder
        Aufnahme noch Mithoeren ihn brauchen. Der Aufrufer setzt sein Flag
        VOR diesem Aufruf auf False. MOD 0 zusaetzlich nur, wenn kein Job-
        Audio (eigener UdpPath UDP_PORT_JOB_AUDIO) laeuft - der Remote-Modus
        ist geraeteweit, ein Mithoeren-Aus darf ein laufendes Long-Run-Audio
        nicht stumm schalten (Spiegelbild der Pruefung in _stop_job_audio_scpi).
        """
        if self.recording or self.monitoring:
            return  # der andere Nutzer braucht den Tag noch
        self.scpi.send(f'TRAC:UDP:DEF:TAG:OFF "{self.local_ip}",{self.udp_port},AUD')
        if self._job_audio_thread is None:
            self.scpi.send("SYST:AUD:REM:MOD 0")

    # -- Audio-Mithoeren (nur im IFPAN-Zoom, siehe _run_zoom_loop) --
    def _apply_monitor_wish(self) -> None:
        """SOLL -> IST. Idempotent; vom Zoom-Loop beim Eintritt und bei den
        monitor_*-Kommandos gerufen. monitor_check setzt das SOLL vorher
        anhand der tatsaechlichen Client-Zahl (siehe _run_zoom_loop)."""
        if self.monitor_wanted:
            self._start_monitoring()
        else:
            self._stop_monitoring()

    def _start_monitoring(self) -> None:
        """Startet das Mithoeren: AUD-Tag sicherstellen, Flag setzen. Ab dann
        reicht der Zoom-Loop jede AUD-Payload an den AudioBroker weiter."""
        if self.ifpan_mode == "IQ":
            # IQ-Rohsamples sind keine NF - der Browser-Mithoerpfad (fest
            # 16 kHz mono, siehe X-Audio-Format) würde nur Rauschen liefern
            # und mit falscher Kanalzahl obendrein. Wunsch verwerfen, damit
            # _apply_monitor_wish() nicht bei jedem Loop erneut anläuft.
            self.monitor_wanted = False
            print("[controller] Mithoeren bei DEM IQ nicht moeglich - uebersprungen.")
            return
        if self.monitoring:
            return
        self._audio_tag_on()
        self.monitoring = True
        print("[controller] Mithoeren gestartet.")

    def _stop_monitoring(self) -> None:
        """Beendet das Mithoeren. Idempotent (Stop-Klick, Zurueck-Button,
        letzter Stream-Client weg, Loop-Ende/finally)."""
        if not self.monitoring:
            return
        self.monitoring = False
        self._audio_tag_off()
        print("[controller] Mithoeren gestoppt.")

    # -- Audio-Aufnahme (nur im IFPAN-Zoom, siehe _run_zoom_loop) --
    def _start_recording(self, center_hz: int) -> None:
        """
        Startet die Audio-Aufnahme der aktuellen IFPAN-Mittenfrequenz als WAV-
        Datei (siehe eb200_core/audio_wav.py). Schaltet den AUDIO-Tag auf
        DEMSELBEN UdpPath zu wie D-SCAN/IFPAN (kein SWAP-Flag - das würde die
        IFPAN-Leveldaten auf diesem Path zerstören, siehe Annex F).

        Fehler beim Öffnen der WAV-Datei (z. B. Verzeichnis nicht anlegbar)
        werden geloggt, dürfen den Wasserfall aber NICHT zum Absturz bringen -
        self.recording bleibt in dem Fall False, der Zoom läuft normal ohne
        Aufnahme weiter.
        """
        if self.recording:
            return  # bereits aktiv - kein doppeltes Öffnen (z. B. Doppel-Klick)

        # T-917: Empfangsparameter EINMAL zum Aufnahmestart lesen (strikt
        # lesend, siehe eb200_core/rx_snapshot.py). Verstaerkung und Squelch
        # fuehrt der Controller ohnehin selbst - sie werden hereingereicht
        # statt abgefragt, das spart vier Abfragen auf der SCPI-Leitung.
        # gain_mgc_dbuv ist nur im MGC-Betrieb aussagekraeftig; bei AGC steht
        # dort bewusst None statt eines Wertes, der nicht gilt.
        # Eigener try-Block: ein misslungener Schnappschuss darf die Aufnahme
        # NICHT verhindern (Muster GCON-Readback in _apply_gain).
        rx_snapshot = None
        device_info = None
        try:
            rx_snapshot = build_rx_snapshot(self.scpi.query, known={
                "gain_mode": self.gain_mode,
                "gain_mgc_dbuv": self.gain_mgc_dbuv if self.gain_mode == "MGC" else None,
                "squelch_on": self.squelch_on,
                "squelch_thr_dbuv": self.squelch_thr_dbuv,
            })
            device_info = build_device_info(self.scpi.query)
        except Exception as exc:
            print(f"[controller] Empfangsparameter nicht erfasst ({exc!r}) - "
                  f"Aufnahme laeuft ohne rx/device im Sidecar", file=sys.stderr)

        try:
            os.makedirs(RECORD_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            freq_khz = round(center_hz / 1000)
            filename = f"{ts}_{freq_khz}kHz_{self.ifpan_mode}.wav"
            path = os.path.join(RECORD_DIR, filename)
            self.audio_writer = WavAudioWriter(
                path, audio_remote_mode=self._current_audio_mode(),
                meta={"freq_hz": center_hz, "mode": self.ifpan_mode, "bw_hz": self.ifpan_bw_hz,
                      "rx": rx_snapshot, "device": device_info},
            )
        except Exception as exc:
            print(f"[controller] Aufnahme konnte nicht gestartet werden: {exc!r}", file=sys.stderr)
            self.audio_writer = None
            return

        self._audio_tag_on()
        self.recording = True
        print(f"[controller] Aufnahme gestartet: {path}")

    def _stop_recording(self) -> None:
        """
        Beendet eine laufende Audio-Aufnahme (SCPI-Rückbau + WAV/Sidecar
        schließen). Idempotent - beliebig oft aufrufbar, da über mehrere
        unabhängige Pfade erreichbar (expliziter Stop-Klick, "Zurück"-Button,
        Loop-Ende/finally, siehe _run_zoom_loop).
        """
        if not self.recording:
            return
        self.recording = False
        self._audio_tag_off()
        if self.audio_writer is not None:
            self.audio_writer.close()
            self.audio_writer = None
        print("[controller] Aufnahme gestoppt.")

    # -- IFPAN-Bin-Aufnahme (eigener UdpPath, siehe eb200_core/spectrum_store.py) --
    def _open_spectrum_writer(self, center_hz, span_hz, n_full_bins, bw_hz, demod):
        """Legt Zielpfad an und erzeugt den SpectrumBinWriter (Dateiname wie bei
        WAV: Zeit_Freq_Mode, Endung .specbin). Wird vom Recorder-Thread beim
        ERSTEN Frame aufgerufen, damit die Geometrie authentisch aus dem
        Datagramm stammt."""
        os.makedirs(RECORD_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        freq_khz = round(center_hz / 1000)
        fname = f"{ts}_{freq_khz}kHz_{demod}.specbin"
        path = os.path.join(RECORD_DIR, fname)
        writer = SpectrumBinWriter(
            path, center_hz=center_hz, span_hz=span_hz,
            n_full_bins=n_full_bins, bw_hz=bw_hz, demod=demod,
        )
        print(f"[controller] Spektrum-Aufnahme schreibt: {path}")
        return writer

    def _run_bin_recorder_loop(self, sock, stop_event, bw_hz, demod, job_recorder=None):
        """
        Empfangs-Thread der Bin-Aufnahme auf dem eigenen UdpPath (UDP_PORT_REC).
        Macht KEIN SCPI - liest nur Datagramme, parst IFPAN (parse_ifpan_raw +
        datagram_seq). Laeuft bis stop_event gesetzt ist bzw. der Socket
        von _stop_bin_recording_local() geschlossen wird (OSError -> Ausstieg).

        job_recorder=None (Standard): Ad-hoc-Spektrumaufnahme - der
        Ad-hoc-SpectrumBinWriter wird lazy beim ersten Frame erzeugt und im
        finally geschlossen (unveraendertes Verhalten).
        job_recorder=<IfpanJobRecorder>: Long-Run-Job (WS3 11.2/11.5,
        Schritt 4c) - JEDER Frame geht STATTDESSEN an job_recorder.record(),
        derselbe Parser/dieselbe Empfangsschleife wie im Ad-hoc-Fall (kein
        zweiter Weg). Ad-hoc-Spektrumaufnahme ist waehrend eines Jobs per
        423 gesperrt (_LOCKED_GET_PATHS) - beide Modi laufen daher nie
        gleichzeitig auf demselben UdpPath.
        """
        writer = None
        try:
            while not stop_event.is_set():
                try:
                    raw, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break  # Socket wurde beim Stoppen geschlossen
                try:
                    seq = datagram_seq(raw)
                    attrs = parse_datagram(raw)
                except ValueError as exc:
                    print(f"[controller] Bin-Datagramm ignoriert: {exc}", file=sys.stderr)
                    continue
                for tag, data in attrs:
                    if tag != TAG_IFPAN:
                        continue
                    try:
                        center_hz, span_hz, raw_levels = parse_ifpan_raw(data)
                    except ValueError as exc:
                        print(f"[controller] Bin-IFPAN ignoriert: {exc}", file=sys.stderr)
                        continue
                    if job_recorder is not None:
                        job_recorder.record(IfpanRecordFrame(
                            center_hz=center_hz, span_hz=span_hz, bw_hz=bw_hz, demod=demod,
                            seq=seq, t=time.time(), levels=raw_levels,
                        ))
                        continue
                    if writer is None:
                        try:
                            writer = self._open_spectrum_writer(
                                center_hz, span_hz, len(raw_levels), bw_hz, demod
                            )
                        except Exception as exc:
                            print(f"[controller] Spektrum-Writer-Start fehlgeschlagen: {exc!r}",
                                  file=sys.stderr)
                            return  # finally: writer None -> nichts zu schliessen
                    writer.write_frame(raw_levels, seq, time.time())
        finally:
            if writer is not None:
                writer.close()
                # T-803 (Teil D): erst NACH close() ist die Sidecar-JSON final
                # auf der Platte - vorher waere der Pfad fuer den Auswertung-
                # Link (:8040) verfrueht.
                self._last_spectrum_path = writer.path
                print("[controller] Spektrum-Datei geschlossen.")

    def _start_bin_recording(self, bw_hz, demod, job_recorder=None) -> bool:
        """
        Startet die IFPAN-Bin-Aufnahme auf einem EIGENEN UdpPath (UDP_PORT_REC):
        registriert dort per SCPI den IFP-Tag MIT der plainen (nicht
        DEF:-praefigierten) Form - siehe validate_tag_streams.py: DEF:TAG:ON
        ist nur fuer die ERSTE, definierende Registrierung sicher (hier
        self.udp_port via _configure_ifpan), ein zweiter DEF:-Aufruf auf
        einem ANDEREN Port wuerde die Zuteilung verschieben statt sie zu
        ergaenzen. Oeffnet einen eigenen Socket und startet
        _run_bin_recorder_loop. Laeuft (wie _start_recording) im
        Zoom-Loop-Thread -> SCPI hier erlaubt.

        job_recorder: siehe _run_bin_recorder_loop() - None fuer die
        Ad-hoc-Aufnahme (bisheriges Verhalten ueber _start_spectrum_recording),
        ein IfpanJobRecorder fuer einen Long-Run-Job (siehe
        _start_ifpan_job_scpi()).

        Liefert True bei Erfolg, False bei Fehler (wird geloggt; der
        Aufrufer entscheidet ueber die Reaktion - Ad-hoc bricht die Aufnahme
        einfach ab, ein Job markiert sich als abgebrochen, siehe dort).
        """
        if self.spectrum_recording:
            return False  # bereits aktiv - kein Doppelstart
        try:
            # Aufnahme-UdpPath sauber neu aufbauen, dann IFP-Tag + LEVEL-Flags
            # auf UDP_PORT_REC registrieren (FEED:CONT IFPAN,ALW ist bereits
            # global aktiv -> das Geraet verteilt an beide Pfade).
            self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{UDP_PORT_REC}')
            self.scpi.send(f'TRAC:UDP:TAG:ON "{self.local_ip}",{UDP_PORT_REC},IFP')
            self.scpi.send(f'TRAC:UDP:FLAG:ON "{self.local_ip}",{UDP_PORT_REC},"VOLT:AC","OPT"')
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", UDP_PORT_REC))
            sock.settimeout(RECV_TIMEOUT_S)
        except Exception as exc:
            print(f"[controller] Bin-Aufnahme konnte nicht starten: {exc!r}", file=sys.stderr)
            try:
                self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{UDP_PORT_REC}')
            except Exception:
                pass
            return False

        self._bin_stop = threading.Event()
        self._bin_sock = sock
        self._bin_thread = threading.Thread(
            target=self._run_bin_recorder_loop,
            args=(sock, self._bin_stop, bw_hz, demod, job_recorder),
            name="bin-recorder", daemon=True,
        )
        self._bin_thread.start()
        self.spectrum_recording = True
        # Pfad der VORHERIGEN Aufnahme verwerfen - bricht die neue Aufnahme ab,
        # bevor ein Frame ankommt (writer bleibt None), soll kein stale Link
        # auf die alte Datei zeigen (T-803 Teil D).
        self._last_spectrum_path = None
        print(f"[controller] Bin-Aufnahme aktiv (UdpPath {UDP_PORT_REC}, BW {bw_hz} Hz, "
              f"{demod}{', Long-Run-Job' if job_recorder is not None else ''}).")
        return True

    def _start_spectrum_recording(self, center_hz: int) -> None:
        """
        Ad-hoc-Spektrumaufnahme (Aufnahme-Knopf in der Zoom-Ansicht) -
        duenner Wrapper um _start_bin_recording() ohne Job-Bezug
        (job_recorder=None, Writer wird lazy beim ersten Frame erzeugt).
        Laeuft im Zoom-Loop-Thread -> SCPI erlaubt.
        """
        bw_hz = self.ifpan_bw_hz     # Momentaufnahme fuer den Writer-Sidecar;
        demod = self.ifpan_mode      # center_hz kommt authentisch aus 1. Frame
        self._start_bin_recording(bw_hz, demod, job_recorder=None)

    def _stop_bin_recording_local(self) -> None:
        """
        NICHT-SCPI-Teil des Bin-Aufnahme-Stopps: stoppt Empfangs-Thread und
        Socket (reine Python-Objekte, sicher aus JEDEM Thread aufrufbar -
        anders als SCPI, das ausschliesslich im Controller-Thread laufen
        darf, siehe Moduldoku _run_zoom_loop). Der SCPI-Rueckbau
        (TAG:OFF/DELete auf UDP_PORT_REC) ist NICHT Teil dieser Methode -
        siehe _stop_spectrum_recording() (Ad-hoc, controller-thread-lokal)
        bzw. _cleanup_ifpan_job_scpi() (Long-Run-Job, per Kommando-Queue).
        Idempotent (leere/None-Felder sind ein No-Op).
        """
        if self._bin_stop is not None:
            self._bin_stop.set()
        if self._bin_sock is not None:
            try:
                self._bin_sock.close()  # bricht ein blockierendes recvfrom auf
            except Exception:
                pass
        if self._bin_thread is not None:
            self._bin_thread.join(timeout=2.0)
        self._bin_thread = None
        self._bin_sock = None
        self._bin_stop = None
        self.spectrum_recording = False

    def _stop_spectrum_recording(self) -> None:
        """
        Beendet eine laufende Ad-hoc-Bin-Aufnahme komplett (Thread/Socket +
        SCPI-Rueckbau). Idempotent (u. a. aus dem finally von
        _run_zoom_loop). Laeuft im Zoom-Loop-Thread -> SCPI erlaubt.
        """
        if not self.spectrum_recording:
            # Nichts zu tun - Signal trotzdem setzen, sonst wartet
            # stop_spectrum_recording_and_wait() sinnlos bis zum Timeout.
            self._spectrum_stop_event.set()
            return
        self._stop_bin_recording_local()
        # SCPI-Rueckbau NUR des Aufnahmepfads (Display-Pfad bleibt unberuehrt).
        self.scpi.send(f'TRAC:UDP:TAG:OFF "{self.local_ip}",{UDP_PORT_REC},IFP')
        self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{UDP_PORT_REC}')
        print("[controller] Bin-Aufnahme beendet.")
        self._spectrum_stop_event.set()

    # -- Job-Audio (eigener UdpPath, NUR AUD-Tag, WS3 11.5 "ein Port, ein Tag") --
    def _run_job_audio_loop(self, sock, stop_event, manifest, recorder) -> None:
        """
        Empfangs-Thread des optionalen IFPAN-Job-Audios auf dem eigenen
        UdpPath UDP_PORT_JOB_AUDIO. Macht KEIN SCPI - liest nur Datagramme,
        parst sie mit parse_datagram + job_audio_payload() (NICHT
        audio_payload() - auf diesem sekundären, per plainer TAG:ON
        registrierten Pfad liefert das Gerät kein Audio-OptionalHeader,
        siehe dortigen Docstring, per Gerätetest verifiziert). Anders als
        auf dem Display-Pfad trägt dieser Pfad AUSSCHLIESSLICH
        den AUD-Tag ("ein Port, ein Tag") - deshalb zählt datagram_seq() HIER
        zum ERSTEN MAL lückenlos NUR AUDIO-Pakete, sodass echte Lücken
        (verlorene Datagramme) zuverlässig über Stille aufgefüllt werden
        können (auf dem gemeinsamen Display-Pfad wäre die seq durch IFPAN-
        Pakete verfälscht - dort wird deshalb bewusst nicht gegapfüllt).

        Rotiert alle rotation_min Minuten (Manifest-Feld, DEFAULT 60 -
        identisch zum Rotationstakt der Bins, siehe
        IfpanJobRecorder.rotation_s in eb200_core/job_runner.py) auf eine
        neue WAV-Datei (longrun_<job_id>_part<nn>.wav). BUGFIX (Schritt
        4c-Nachschliff): vorher war hier fest 3600 s einprogrammiert, ganz
        unabhaengig vom Manifest - bei kurzen Testlaeufen (z. B. 150 s mit
        rotation_min=1) griff die Rotation dadurch nie, obwohl die
        Pruefung selbst pro AUD-Datagramm (dichter Strom, ~33/s) durchaus
        erreichbar war. Dateinamen gehen in recorder.extra_files (siehe
        IfpanJobRecorder, wird beim naechsten Heartbeat/close() ans
        Manifest gemeldet). Fehler werden NIE in den Aufrufer geworfen
        (recorder.stats["audio_error"] wird gesetzt, Konsole geloggt) -
        Audio ist ein Zusatz, die Bins sind der Hauptzweck des Jobs und
        laufen unabhaengig weiter.

        Zeitbasis fuer Rotation UND Watchdog: time.monotonic() (Empfangs-
        zeit der Datagramme relativ zum Thread-Start) statt time.time() -
        unempfindlich gegen Systemuhr-Spruenge waehrend eines lang
        laufenden Jobs. Ein Dateiwechsel setzt got_any_datagram NICHT
        zurueck (bleibt nach dem ersten Datagramm dauerhaft True) - der
        Watchdog prueft nur "seit Thread-Start je etwas empfangen", nicht
        "seit der letzten Datei" - eine Rotation wird also NIE als
        Datenausfall fehlinterpretiert.
        """
        writer = None
        part_no = 0
        last_seq = None
        rotation_s = float(manifest.get("rotation_min", 60)) * 60.0
        file_start_t = None
        loop_start_t = time.monotonic()
        got_any_datagram = False
        NO_DATA_TIMEOUT_S = 15.0  # siehe Watchdog unten

        def open_next_file(t0: float):
            nonlocal writer, part_no, file_start_t, last_seq
            part_no += 1
            filename = f"longrun_{manifest['job_id']}_part{part_no:02d}.wav"
            path = os.path.join(manifest["job_dir"], filename)
            params = manifest["params"]
            writer = WavAudioWriter(
                path, audio_remote_mode=AUDIO_REMOTE_MODE,
                meta={"freq_hz": params.get("center_hz"), "mode": params.get("demod"),
                      "bw_hz": params.get("bw_hz")},
            )
            file_start_t = t0
            # Seq-Kontinuitaet gilt NUR je Datei - jede neue Datei beginnt
            # ihre Luecken-/Stille-Fuellung frisch (siehe last_seq-Nutzung
            # unten), sonst wuerde der erste Datagramm-Sprung nach einem
            # Dateiwechsel faelschlich als Paketverlust gewertet.
            last_seq = None
            recorder.extra_files.append(filename)

        try:
            try:
                open_next_file(time.monotonic())
            except Exception as exc:
                recorder.stats["audio_error"] = f"WAV start failed: {exc!r}"
                print(f"[controller] Job-Audio-Start fehlgeschlagen: {exc!r}", file=sys.stderr)
                return

            while not stop_event.is_set():
                try:
                    raw, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    # Watchdog (Annex F liefert Registrierungsfehler NICHT
                    # als SCPI-Fehlerantwort - ScpiClient.send() ist "fire
                    # and forget", siehe dortige Doku): bleibt dieser Pfad
                    # laenger als NO_DATA_TIMEOUT_S ganz ohne Datagramm, wird
                    # das EINMALIG als audio_error vermerkt, statt still eine
                    # leere WAV-Datei zu hinterlassen - macht ein am Geraet
                    # abgelehntes/ignoriertes TAG:ON (statt einer Python-
                    # Exception) im Manifest sichtbar.
                    if not got_any_datagram and time.monotonic() - loop_start_t > NO_DATA_TIMEOUT_S:
                        recorder.stats["audio_error"] = (
                            f"no audio datagrams within {NO_DATA_TIMEOUT_S:.0f} s "
                            f"on UdpPath {UDP_PORT_JOB_AUDIO}"
                        )
                        print(f"[controller] Job-Audio: {recorder.stats['audio_error']}", file=sys.stderr)
                        loop_start_t = float("inf")  # nur einmal melden
                    continue
                except OSError:
                    break  # Socket wurde beim Stoppen geschlossen
                got_any_datagram = True
                try:
                    seq = datagram_seq(raw)
                    attrs = parse_datagram(raw)
                except ValueError as exc:
                    print(f"[controller] Job-Audio-Datagramm ignoriert: {exc}", file=sys.stderr)
                    continue
                for tag, data in attrs:
                    if tag != TAG_AUDIO:
                        continue
                    try:
                        payload = job_audio_payload(data)
                    except ValueError as exc:
                        print(f"[controller] Job-Audio-Payload ignoriert: {exc}", file=sys.stderr)
                        continue
                    if last_seq is not None and payload:
                        gap = (seq - last_seq - 1) % 65536
                        # Grosse Luecken (Reconnect/langer Ausfall) NICHT mit
                        # ggf. minutenlanger Stille auffuellen - defensiv
                        # gedeckelt, ein "harter" Ausfall bleibt als hoerbare
                        # Kuerzung sichtbar statt als riesige Stille-Datei.
                        if 0 < gap < 1000:
                            writer.write_frames(b"\x00" * (gap * len(payload)))
                    last_seq = seq
                    writer.write_frames(payload)
                    now = time.monotonic()
                    if now - file_start_t >= rotation_s:
                        # Wechsel ZWISCHEN zwei Datagrammen: WAV sauber
                        # schliessen (schreibt den WAV-Header/die
                        # Sidecar-JSON final, siehe audio_wav.py), dann
                        # sofort die naechste Datei oeffnen und in
                        # extra_files melden.
                        writer.close()
                        open_next_file(now)
        except Exception as exc:
            recorder.stats["audio_error"] = f"unexpected error: {exc!r}"
            print(f"[controller] Job-Audio-Fehler: {exc!r}", file=sys.stderr)
        finally:
            if writer is not None:
                writer.close()

    def _start_job_audio(self, manifest: dict, recorder) -> None:
        """
        Startet die optionale Job-Audio-Aufnahme (params["audio"] == true,
        WS3 11.5) auf dem EIGENEN UdpPath UDP_PORT_JOB_AUDIO - NUR der
        AUD-Tag (Regel "ein Port, ein Tag": DEF:TAG:ON darf nur die
        JEWEILS ERSTE Registrierung eines neuen Pfads vornehmen, jede
        weitere fremde Registrierung würde sonst die vorherige stören -
        siehe validate_tag_streams.py-Befund; hier ist es die erste
        Registrierung DIESES Pfads, aber die plaine Form genügt dafür
        genauso wie beim Bin-Aufnahme-Pfad in _start_bin_recording() und
        vermeidet jede Interferenz mit den bereits laufenden DEF:-
        Registrierungen der anderen beiden Pfade). KEIN SWAP-Flag
        (Byteswap passiert im WavAudioWriter, siehe audio_wav.py).

        Läuft im Controller-Thread (aufgerufen aus _start_ifpan_job_scpi(),
        selbst nur über die commands-Queue erreichbar) - SCPI hier erlaubt.

        Fehler (z. B. Portkonflikt) werden NICHT nach oben geworfen: der
        Job läuft ohne Audio weiter, recorder.stats["audio_error"] hält den
        Grund fest (siehe job_active()/Manifest).
        """
        try:
            self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{UDP_PORT_JOB_AUDIO}')
            self.scpi.send("SYST:AUD:REM:MOD " + str(AUDIO_REMOTE_MODE))
            self.scpi.send(f'TRAC:UDP:TAG:ON "{self.local_ip}",{UDP_PORT_JOB_AUDIO},AUD')
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", UDP_PORT_JOB_AUDIO))
            sock.settimeout(RECV_TIMEOUT_S)
        except Exception as exc:
            recorder.stats["audio_error"] = f"job audio start failed: {exc!r}"
            print(f"[controller] Job-Audio konnte nicht gestartet werden: {exc!r}", file=sys.stderr)
            try:
                self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{UDP_PORT_JOB_AUDIO}')
            except Exception:
                pass
            return

        self._job_audio_stop = threading.Event()
        self._job_audio_sock = sock
        self._job_audio_thread = threading.Thread(
            target=self._run_job_audio_loop,
            args=(sock, self._job_audio_stop, manifest, recorder),
            name="job-audio", daemon=True,
        )
        self._job_audio_thread.start()
        print(f"[controller] Job-Audio aktiv (UdpPath {UDP_PORT_JOB_AUDIO}).")

    def _stop_job_audio_local(self) -> None:
        """
        NICHT-SCPI-Teil des Job-Audio-Stopps (Thread/Socket) - sicher aus
        jedem Thread aufrufbar, siehe _stop_bin_recording_local(). Der
        SCPI-Rueckbau ist NICHT Teil dieser Methode, siehe
        _stop_job_audio_scpi(). Idempotent (None-Felder sind ein No-Op).
        """
        if self._job_audio_stop is not None:
            self._job_audio_stop.set()
        if self._job_audio_sock is not None:
            try:
                self._job_audio_sock.close()
            except Exception:
                pass
        if self._job_audio_thread is not None:
            self._job_audio_thread.join(timeout=2.0)
        self._job_audio_thread = None
        self._job_audio_sock = None
        self._job_audio_stop = None

    def _stop_job_audio_scpi(self) -> None:
        """
        SCPI-Teil des Job-Audio-Stopps (Controller-Thread-only, aufgerufen
        aus _cleanup_ifpan_job_scpi()). Deregistriert den AUD-Tag auf
        UDP_PORT_JOB_AUDIO; MOD 0 nur, wenn nicht GLEICHZEITIG die
        Display-Pfad-Aufnahme oder das Mithoeren aktiv ist (self.recording / self.monitoring) - defensive
        Prüfung, obwohl das aktuell durch die Empfänger-Sperre während
        eines Jobs bereits ausgeschlossen ist (kein Ad-hoc-Aufnahme-Start
        möglich, solange ein Job läuft). No-Op, wenn kein Job-Audio-Pfad
        aktiv war (kein 'if' nötig - TAG:OFF/DELete auf einem nicht
        registrierten Pfad ist am Gerät folgenlos).
        """
        try:
            self.scpi.send(f'TRAC:UDP:TAG:OFF "{self.local_ip}",{UDP_PORT_JOB_AUDIO},AUD')
            self.scpi.send(f'TRAC:UDP:DELete "{self.local_ip}",{UDP_PORT_JOB_AUDIO}')
        except Exception as exc:
            print(f"[controller] Aufräumen des Job-Audios fehlgeschlagen: {exc!r}", file=sys.stderr)
        if not self.recording and not self.monitoring:
            try:
                self.scpi.send("SYST:AUD:REM:MOD 0")
            except Exception as exc:
                print(f"[controller] AUD:REM:MOD-Rueckbau fehlgeschlagen: {exc!r}", file=sys.stderr)

    def _sweep_watchdog_s(self) -> float:
        """
        Basiszeit fuer den Sweep-Watchdog (siehe SWEEP_WATCHDOG_S in
        _run_overview_loop): der Watchdog wird NUR beim Sweep-Start bzw.
        bei Sweep-Ende neu gesetzt, NICHT bei jedem einzelnen empfangenen
        Chunk zwischendurch (Audit "zeitbasierte Verwerfungslogik") - er
        ist damit faktisch eine MAXIMALE Sweep-Dauer, keine "seit dem
        letzten Paket"-Pruefung. Der feste Wert SWEEP_WATCHDOG_S (20 s)
        passt fuer LOW/NORM/HIGH (Sweeps dauern dort typischerweise
        Sekundenbruchteile), wuerde bei Speed MTIM aber einen laenger
        dauernden, voellig gesunden Sweep faelschlich als Paketverlust
        werten und den Puffer immer wieder verwerfen, bevor er je fertig
        werden kann - deshalb hier an eine Schaetzung der tatsaechlich zu
        erwartenden Sweep-Dauer gekoppelt (Kanalzahl x (Messzeit +
        Abstimmzuschlag), Muster der JS-Schaetzung in updateMtimeInfo()).

        WICHTIG (T-719): der hier berechnete Wert ist nur die EINE von zwei
        Fristen. Er deckt den Fall "Sweep wird nicht fertig" ab (Pakete sind
        gekommen, der Puffer bleibt unvollstaendig). Der Fall "ueberhaupt
        kein Datagramm" wird in _run_overview_loop() getrennt und deutlich
        frueher behandelt, mit der festen Schwelle SWEEP_SILENCE_S (10 s);
        diese Funktion wird dafuer NICHT herangezogen, auch nicht bei MTIM.
        """
        if self.dscan_speed != "MTIM":
            return SWEEP_WATCHDOG_S
        try:
            n_channels = max(1, round(
                (self.dscan_stop_hz - self.dscan_start_hz) / (self.dscan_bw_hz / 2)
            ))
        except Exception:
            n_channels = 1
        # DEFAULT (None): die tatsaechliche Messzeit haengt von BW/Detektor
        # ab (Annex E, hoechster Tabellenwert 500 ms bei BW 150 Hz/PEAK) -
        # konservativ mit 1 s je Kanal rechnen, damit ein (noch) unbekannter
        # DEFAULT-Wert den Watchdog nie zu knapp bemisst.
        mtime_s = self.dscan_mtime_s if self.dscan_mtime_s is not None else 1.0
        estimate_s = n_channels * (mtime_s + 0.05)
        # Grosszuegiger Sicherheitsfaktor (2x) gegen Abstimm-/Verlust-
        # Schwankungen, nach oben gedeckelt (1 h) - auch ein MTIM-Sweep soll
        # sich im Extremfall (z. B. echter Geraeteausfall) irgendwann als
        # haengend erkennen lassen, statt den Watchdog faktisch abzuschalten.
        return max(SWEEP_WATCHDOG_S, min(3600.0, estimate_s * 2))

    # -- die drei Empfangs-Schleifen --
    def _run_overview_loop(self):
        """Läuft, bis ein Kommando eintrifft oder stop_event gesetzt wird.
        Gibt das auslösende Kommando zurück, oder None bei stop_event."""
        self._configure_dscan()
        print(f"[controller] Übersicht (D-SCAN) läuft: "
              f"{self.dscan_start_hz/1e6:.3f}-{self.dscan_stop_hz/1e6:.3f} MHz")
        accumulator = SweepAccumulator()
        watchdog_deadline = time.monotonic() + self._sweep_watchdog_s()

        # Diagnose: Dauer und Paketzahl je Sweep messen, um Empfänger-,
        # LAN- und Software-Anteil an der Sweep-Zeit unterscheiden zu
        # können (siehe Rückfrage von OE3GAS zur Geschwindigkeit bei
        # großen Bereichen). Punkte/Sekunde sollte bei Empfänger-limitierter
        # Sweep-Zeit über verschiedene Bereichsgrößen etwa konstant bleiben;
        # fällt sie deutlich, wenn der Bereich größer wird, deutet das auf
        # Software-/Netzwerk-Overhead statt reiner Hardware-Sweepzeit hin.
        sweep_start_time = time.monotonic()
        udp_packets_this_sweep = 0

        cmd = None  # zuletzt ermitteltes Rueckgabe-Kommando, siehe finally unten
        try:
            while True:
                if self.stop_event.is_set():
                    return None
                try:
                    cmd = self.commands.get_nowait()
                except queue.Empty:
                    cmd = None
                else:
                    if cmd[0] == "job_mscan_stop":
                        # MSCAN-Lauf ist bereits vorbei (Selbstabbau in
                        # _run_mscan_loop()) - ein verspaetetes Stopp-
                        # Kommando (Race mit dem natuerlichen Laufende)
                        # hier folgenlos verwerfen (Muster
                        # job_ifpan_cleanup im Zoom-Loop).
                        continue
                    # T-711: "gain" wird NICHT mehr inline verarbeitet,
                    # sondern wie range/speed/BW zurueckgegeben - der
                    # Sweep wird ueber das finally (ABOR) beendet und von
                    # _configure_dscan() mit der neuen Verstaerkung neu
                    # aufgebaut. Im laufenden Scan hatte das Geraet die
                    # Umschaltung nicht uebernommen (Befund OE3GAS).
                    return cmd
                try:
                    raw, _addr = self.udp_sock.recvfrom(65535)
                except socket.timeout:
                    # T-719: zwei Fristen. Kam noch KEIN einziges Datagramm,
                    # genuegt SWEEP_SILENCE_S (10 s) - das Geraet sendet nicht
                    # und der Neuaufbau soll rasch anlaufen. Sind schon Pakete
                    # da, gilt weiterhin watchdog_deadline als maximale
                    # Sweepdauer, damit lange gesunde Sweeps nicht verworfen
                    # werden.
                    if (time.monotonic() > watchdog_deadline
                            or (udp_packets_this_sweep == 0
                                and time.monotonic() - sweep_start_time > SWEEP_SILENCE_S)):
                        # Zwei grundverschiedene Faelle mit zwei Texten: NULL
                        # Pakete im ganzen Fenster heisst, das Geraet sendet
                        # nicht (Konfiguration abgelehnt, falscher Modus,
                        # UdpPath weg) - Paketverlust sieht anders aus (Puffer
                        # teilweise gefuellt, dann Stille). Bereich und BW
                        # mitloggen, damit der Befund ohne Rueckfrage
                        # einzuordnen ist.
                        elapsed = time.monotonic() - sweep_start_time
                        where = (f"{self.dscan_start_hz/1e6:.4f}-{self.dscan_stop_hz/1e6:.4f} MHz, "
                                 f"BW {self.dscan_bw_hz/1e3:g} kHz, Speed {self.dscan_speed}")
                        if udp_packets_this_sweep == 0:
                            # T-712/T-719: voellige Stille heisst, das Geraet
                            # sendet nicht mehr - der Scan ist abgebrochen
                            # (z. B. durch Bedienung am Frontpanel) oder die
                            # Konfiguration wurde abgelehnt. Bisher wurde hier
                            # nur der Puffer verworfen, obwohl die Meldung
                            # "Starte neu" lautete: die Uebersicht blieb
                            # danach dauerhaft stehen, es half nur ein
                            # Serverneustart (Befund OE3GAS 2026-09-03).
                            # Jetzt wird der Aufbau tatsaechlich wiederholt:
                            # ("overview", None) verlaesst die Schleife, das
                            # finally schickt ABOR, run() ruft
                            # _run_overview_loop() sofort neu auf und die
                            # laeuft ueber _configure_dscan() samt UdpPath-
                            # Neuanmeldung und INIT:IMM.
                            #
                            # cmd VOR dem return setzen: das finally
                            # entscheidet anhand von cmd, ob stop_session()
                            # laeuft. Ohne diese Zeile stuende cmd noch auf
                            # None (Queue leer) und jeder Erholungsversuch
                            # wuerde eine laufende Session-Aufzeichnung
                            # beenden.
                            #
                            # Fehler-Queue mitprotokollieren: sie sagt, WARUM
                            # das Geraet aufgehoert hat zu senden (drain_errors
                            # ist tolerant, ein Timeout beim Auslesen bricht
                            # nur ab und wirft nicht).
                            self._dscan_watchdog_rebuilds += 1
                            fehler = drain_errors(self.scpi)
                            fehlertext = "; ".join(fehler) if fehler else "leer"
                            print(f"[controller] Sweep-Watchdog: seit {elapsed:.0f} s KEIN D-SCAN-"
                                  f"Datagramm ({where}) - Geraet sendet nicht. "
                                  f"Fehler-Queue: {fehlertext}. "
                                  f"Neuaufbau #{self._dscan_watchdog_rebuilds}.",
                                  file=sys.stderr)
                            self.broker.publish_notice(
                                f"No sweep for {elapsed:.0f} s ({where}) - "
                                f"rebuilding D-SCAN "
                                f"(attempt {self._dscan_watchdog_rebuilds}).", level="warn")
                            cmd = ("overview", None)
                            return cmd
                        else:
                            print(f"[controller] Sweep-Watchdog: Sweep nach {elapsed:.0f} s "
                                  f"unvollstaendig ({udp_packets_this_sweep} Pakete, {where}) - "
                                  f"Paketverlust? Verwerfe Puffer, starte neu.", file=sys.stderr)
                        accumulator.reset()
                        watchdog_deadline = time.monotonic() + self._sweep_watchdog_s()
                        sweep_start_time = time.monotonic()
                        udp_packets_this_sweep = 0
                    continue
                try:
                    for tag, data in parse_datagram(raw):
                        if tag == TAG_DSCAN:
                            chunk = parse_dscan_trace(data)
                            udp_packets_this_sweep += 1
                            update = accumulator.add_chunk(chunk)
                            # Rohdaten-Session-Aufzeichnung (siehe start_session()/
                            # stop_session(), /record/start|stop): einzelner
                            # Attribut-Read, KEIN Lock auf dem Hot Path - record()
                            # selbst ist ein No-Op bei unfertigem Sweep und tolerant
                            # gegenüber einem gerade schließenden Store.
                            rec = self._session_recorder
                            if rec is not None:
                                # T-715: BW/BW-Zoom/Verstaerkung mitschreiben - reine
                                # Attribut-Reads wie speed/mtime_s, Hot Path unveraendert.
                                rec.record(update, speed=self.dscan_speed,
                                           mtime_s=self.dscan_mtime_s,
                                           bw_hz=self.dscan_bw_hz,
                                           bw_zoom=self.dscan_bw_zoom,
                                           gain_mode=self.gain_mode,
                                           gain_mgc_dbuv=self.gain_mgc_dbuv)
                            # Long-Run-Job-Aufzeichnung (WS3 11.5,
                            # eb200_core/job_runner.py): Referenz unter
                            # _job_lock lesen (Muster session_status()),
                            # record() ausserhalb des Locks aufrufen -
                            # ebenfalls ein No-Op bei unfertigem Sweep, wirft
                            # nie eine Ausnahme in diesen Hot Path.
                            with self._job_lock:
                                job_rec = self._job_recorder
                            # Nur der D-SCAN-Recorder verarbeitet Sweeps; ein anderer Typ
                            # (IFPAN/MSCAN) darf hier nie durchlaufen - siehe Nachtrag T-1005 B.
                            if isinstance(job_rec, DscanJobRecorder):
                                job_rec.record(update)
                            if update.complete:
                                # Kennzahlen VOR dem Publizieren in update
                                # eintragen, damit sie im selben Frame beim
                                # Browser ankommen (Aktivitätszeile, siehe
                                # FrameBroker.publish_dscan_update).
                                now = time.monotonic()
                                update.sweep_elapsed_s = now - sweep_start_time
                                update.udp_packets = udp_packets_this_sweep
                                # T-505: Detektion NACH den Kennzahlen, VOR dem Publizieren -
                                # Ereignisse gehoeren in denselben Frame wie die Sweep-Zeile.
                                # Nie eine Ausnahme in den Hot Path werfen.
                                try:
                                    update.detect_events = self.detector.process(
                                        update.full_levels, update.start_hz,
                                        update.stop_hz, update.step_hz, update.t)
                                    update.detect_delta = self.detector.delta_db   # T-505d
                                except Exception as exc:
                                    print(f"[detect] Fehler in SignalDetector: {exc}", file=sys.stderr)
                            self.broker.publish_dscan_update(update)
                            if update.complete:
                                # Sweep-Kennzahlen stehen in update (Aktivitaetszeile
                                # im Browser), keine Konsolenzeile je Sweep mehr.
                                # T-712: ein vollstaendiger Sweep beweist, dass
                                # die Uebersicht wieder laeuft - Erholungszaehler
                                # zuruecksetzen und den Erfolg genau einmal
                                # protokollieren.
                                if self._dscan_watchdog_rebuilds:
                                    print(f"[controller] D-SCAN laeuft wieder nach "
                                          f"{self._dscan_watchdog_rebuilds} Neuaufbau/-ten.")
                                    self._dscan_watchdog_rebuilds = 0
                                sweep_start_time = now
                                udp_packets_this_sweep = 0
                                watchdog_deadline = time.monotonic() + self._sweep_watchdog_s()
                except ValueError as exc:
                    print(f"[controller] Datagramm ignoriert: {exc}", file=sys.stderr)
        finally:
            # _stop_dscan() (SCPI ABOR) unbedingt bei JEDEM Verlassen - der
            # laufende Sweep wird ohnehin verworfen, egal aus welchem Grund
            # die Schleife endet (naechster Aufruf von _configure_dscan()
            # baut ihn neu auf).
            #
            # self.stop_session() dagegen NUR bei echtem Verlassen der
            # Uebersicht (App-Ende: cmd is None, oder Wechsel in den IFPAN-
            # Zoom: cmd[0]=="zoom") - ein reiner Parameterwechsel innerhalb
            # des Overview-Modus (Speed/Bereich/BW/BW-ZOOM/MTIME/...) laesst
            # _run_overview_loop() ebenfalls per return verlassen und sofort
            # neu aufrufen (siehe run()), OHNE dass sich mode aendert. Ohne
            # diese Unterscheidung wuerde JEDER Parameterwechsel die laufende
            # Rohdaten-Session-Aufzeichnung sofort beenden - die
            # Segmentgrenzen-Logik in session_store.py (neues Segment bei
            # Speed-/MTIME-Wechsel INNERHALB einer Aufzeichnung) waere dann
            # nie erreichbar. idempotent (No-Op ohne laufende Aufzeichnung).
            if cmd is None or cmd[0] == "zoom":
                self.stop_session()
            self._stop_dscan()

    def _run_zoom_loop(self, center_hz: int):
        self.zoom_center_hz = center_hz   # T-708: siehe __init__
        self._configure_ifpan(center_hz)
        # Mithoeren-SOLL herstellen - deckt sowohl das Race mit /api/zoom ab
        # (monitor_start kam evtl. schon in der Uebersicht an) als auch den
        # Neustart dieses Loops nach Span/Mode/BW-Wechsel (finally hat den
        # IST-Zustand zurueckgesetzt, der Client-Stream lebt aber weiter).
        self._apply_monitor_wish()
        print(f"[controller] Zoom (IFPAN) läuft: {center_hz/1e6:.4f} MHz "
              f"± {self.zoom_span_hz/2/1e3:.1f} kHz")
        try:
            while True:
                if self.stop_event.is_set():
                    return None
                try:
                    cmd = self.commands.get_nowait()
                except queue.Empty:
                    cmd = None
                if cmd is not None:
                    kind, _value = cmd
                    # record_start/record_stop werden HIER INLINE verarbeitet
                    # (nicht über die return-basierte Kommandoweitergabe an
                    # run(), wie alle anderen Kommandos) - der Zoom-Loop läuft
                    # dabei einfach weiter, statt neu aufgebaut zu werden.
                    if kind == "record_start":
                        self._start_recording(center_hz)
                        continue
                    elif kind == "record_stop":
                        self._stop_recording()
                        continue
                    elif kind == "monitor_start":
                        self._apply_monitor_wish()
                        continue
                    elif kind == "monitor_check":
                        # Letzter Stream-Client weg? Erst JETZT nachsehen -
                        # bei einem Reload haengt der neue Client meist schon.
                        if self.audio_broker.client_count() == 0:
                            self.monitor_wanted = False
                        self._apply_monitor_wish()
                        continue
                    elif kind == "spectrum_start":
                        self._start_spectrum_recording(center_hz)
                        continue
                    elif kind == "spectrum_stop":
                        self._stop_spectrum_recording()
                        continue
                    elif kind == "job_ifpan_start":
                        self._start_ifpan_job_scpi(_value)
                        continue
                    elif kind == "job_ifpan_cleanup":
                        self._cleanup_ifpan_job_scpi()
                        continue
                    elif kind == "job_mscan_stop":
                        # Siehe _run_overview_loop(): verspaetetes Stopp-
                        # Kommando nach bereits abgeschlossenem MSCAN-Lauf,
                        # folgenlos verwerfen.
                        continue
                    elif kind == "squelch":
                        # wirkt sofort, Loop laeuft weiter.
                        self.squelch_on, self.squelch_thr_dbuv = _value
                        self._apply_squelch()
                        continue
                    elif kind == "gain":
                        # wirkt sofort, Loop laeuft weiter.
                        self.gain_mode, self.gain_mgc_dbuv = _value
                        self._apply_gain()
                        continue
                    elif kind == "tune":
                        # T-708: Feinabstimmung INLINE (Muster record_*):
                        # nur FREQ setzen - IFPAN-Feed, UdpPath und Tags
                        # bleiben stehen; ein voller Zoom-Neustart wuerde
                        # bei jedem 1-Hz-Schritt Feed und Anzeige abreissen.
                        # Waehrend WAV-/Specbin-Aufnahme verworfen (Sidecar
                        # bzw. Dateigeometrie tragen die Mittenfrequenz;
                        # /api/tune weist bereits mit 409 ab - dies faengt
                        # nur das Rennen zwischen HTTP-Pruefung und der
                        # Verarbeitung hier ab).
                        if self.recording or self.spectrum_recording:
                            print("[controller] Feinabstimmung verworfen - Aufnahme laeuft.")
                            continue
                        if _value != center_hz:
                            center_hz = _value
                            self.zoom_center_hz = _value
                            self.scpi.send(f"FREQ {_value}")
                        continue
                    else:
                        return cmd
                try:
                    raw, _addr = self.udp_sock.recvfrom(65535)
                except socket.timeout:
                    continue
                try:
                    for tag, data in parse_datagram(raw):
                        if tag == TAG_IFPAN:
                            frame = parse_ifpan_trace(data)
                            self.broker.publish_ifpan(
                                frame, self.ifpan_mode, self.ifpan_bw_hz)
                        elif tag == TAG_AUDIO:
                            if self.audio_writer is None and not self.monitoring:
                                continue  # Tag laeuft nach, niemand braucht die Daten
                            pcm_be = audio_payload(data)
                            if self.audio_writer is not None:
                                self.audio_writer.write_frames(pcm_be)
                            if self.monitoring:
                                self.audio_broker.publish(pcm_be)
                except ValueError as exc:
                    print(f"[controller] Datagramm ignoriert: {exc}", file=sys.stderr)
        finally:
            self._stop_spectrum_recording()  # laufende Bin-Aufnahme sauber beenden (eigener UdpPath)
            self._stop_monitoring()  # Mithoeren endet mit dem Zoom (Tag-Refcount, siehe _audio_tag_off)
            self._stop_recording()  # schließt eine laufende WAV sauber, egal über welchen Ausstieg
            self._stop_ifpan()

    def _scpi_resync(self, tag: str) -> bool:
        """
        Synchronisationsanker (T-1005 B Nachtrag 5): eine Query, deren
        Antwort erst NACH Ablauf des Socket-Timeouts eintrifft, hinterlaesst
        die verspaetete Antwort im Empfangspuffer der TCP-Verbindung -
        ScpiClient.query() liest bei jedem Aufruf nur eine frische lokale
        Zeile, draint aber nie liegengebliebene Altdaten (siehe Bericht) -
        die naechste Query liest dann faelschlich die ALTE Antwort (Befund:
        "Readback MEM0 ... Antwort hat 1 statt 10 Felder: '1'" war die
        Antwort eines vorherigen MPAR?).

        Sendet *IDN? und liest Antworten, bis eine mit "ROHDE&SCHWARZ"
        beginnt (max. 10 Versuche) - jede verworfene Zeile wird geloggt.
        True, sobald die IDN-Antwort da ist (Leitung synchron); False nach
        10 erfolglosen Versuchen oder einem Verbindungsfehler.
        """
        for _ in range(10):
            try:
                resp = self.scpi.query("*IDN?")
            except Exception as exc:
                print(f"[jobs] MSCAN-Lauf: SCPI-Resync ({tag}) abgebrochen "
                      f"(Verbindungsfehler): {exc!r}", file=sys.stderr)
                return False
            if resp.startswith("ROHDE&SCHWARZ"):
                return True
            print(f"[jobs] MSCAN-Lauf: SCPI-Resync ({tag}) verwirft veraltete Antwort: {resp!r}",
                  file=sys.stderr)
        print(f"[jobs] MSCAN-Lauf: SCPI-Resync ({tag}) nach 10 Versuchen ohne IDN-Antwort",
              file=sys.stderr)
        return False

    def _query_expect(self, cmd: str, check) -> tuple[bool, str]:
        """
        Sendet cmd und prueft die Antwort mit check(antwort) -> bool
        (Nachtrag 5, Muster fuer MPAR?: Antwort in ("0","1"); CONTents?:
        genau 10 kommagetrennte Felder). Schlaegt die Pruefung fehl (oder
        wirft query() eine Ausnahme, z. B. Timeout), synchronisiert EINMAL
        (_scpi_resync) und wiederholt cmd GENAU EINMAL - erst danach gilt
        die Abfrage als endgueltig gescheitert.

        Rueckgabe (True, antwort) bei Erfolg (erster oder wiederholter
        Versuch), sonst (False, "<erste antwort> / <zweite antwort>") -
        BEIDE Rohantworten im Text, damit ein kuenftiger Befund sofort
        zuzuordnen ist.
        """
        try:
            first = self.scpi.query(cmd)
        except Exception as exc:
            first = f"(Fehler: {exc!r})"
        else:
            if check(first):
                return True, first
        self._scpi_resync(cmd)
        try:
            second = self.scpi.query(cmd)
        except Exception as exc:
            second = f"(Fehler: {exc!r})"
        else:
            if check(second):
                return True, second
        return False, f"{first!r} / {second!r}"

    def _run_mscan_loop(self, manifest: dict) -> Optional[tuple]:
        """
        MSCAN-Lauf (T-1005 Session B): programmiert die Speicherplaetze,
        startet den Scan und wertet MSC-/AUD-Datagramme aus, bis der Lauf
        endet - siehe docs/EB200_Geraeteprofil.md, Abschnitt MSCAN, fuer
        die Hardwarebefunde und tools/mscan_probe.py fuer das urspruengliche
        Vorbild der Befehlsfolge (Phasen C-G, dort UNVERAENDERT). Laeuft im
        Controller-Thread (aufgerufen aus run()) - SCPI und ein eigener
        MSC-/AUD-Socket sind hier erlaubt. Wie _run_overview_loop()/
        _run_zoom_loop() Rueckgabe: Folge-Kommando oder None bei
        stop_event - IMMER ("overview", None) ausser bei echtem
        Server-Stopp (siehe unten).

        NACHTRAG 1 (T-1005 B): der Recorder wird ERST HIER (nach der
        Vorbedingungspruefung a, vor Phase b) angelegt - NICHT mehr in
        _start_mscan_job() (Scheduler-Thread). Solange die Uebersicht/der
        Zoom noch laeuft (das Kommando "job_mscan_start" wartet in der
        commands-Queue), darf self._job_recorder dort NIE auf einen
        MSCAN-Recorder zeigen: deren Sweep-/Frame-Pfad geht von einem
        DscanJobRecorder bzw. IfpanJobRecorder aus (siehe
        isinstance-Absicherung in _run_overview_loop()).

        NACHTRAG 7 (T-1019b): WELCHER Recorder entsteht, entscheidet
        make_recorder() anhand von params["record_audio"] -
        MscanJobRecorder mit Ton, MscanLevelRecorder fuer die reine
        Pegel-Inventur (eb200_core/mscan_run.py, Teil E2). Beide haben
        dieselbe aeussere Schnittstelle, die Empfangsschleife unten
        unterscheidet sie nicht. Ohne Ton wird zusaetzlich der AUD-Socket
        gar nicht erst geoeffnet: das Geraet bekommt weder den
        Audio-Fernmodus noch den AUD-UdpPath gesetzt (scan_setup_commands),
        es kaeme also ohnehin nichts an.
        """
        job_id = manifest["job_id"]
        params = manifest["params"]
        channels = params["channels"]
        n = len(channels)

        # a) Vorbedingung: MSCAN belegt Speicher UND Empfaenger exklusiv -
        # eine parallel laufende Session-/Spektrum-Aufnahme oder Mithoeren
        # (alle drei IFPAN-/D-SCAN-spezifisch) wuerden mit einem MSCAN-Lauf
        # kollidieren bzw. sofort sinnlos werden. Hier ist noch KEIN SCPI
        # gesendet, kein Socket geoeffnet und (siehe Nachtrag 1) noch KEIN
        # Recorder angelegt - Abbruch ohne das finally unten, nur
        # Zustand/Sperre aufraeumen.
        with self._session_lock:
            session_active = self._session_recorder is not None
        if session_active or self.spectrum_recording or self.monitoring:
            # abort_detail als Code (T-1821b); Anzeige ueber EB200_LABELS.busy_by
            grund = ("session_recording" if session_active else
                     "spectrum_recording" if self.spectrum_recording else
                     "monitoring")
            abort_detail = grund
            print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase a: receiver_busy ({abort_detail})", file=sys.stderr)
            self.job_store.set_state(job_id, ST_ABGEBROCHEN,
                                     abort_code="receiver_busy", abort_detail=abort_detail)
            with self._job_lock:
                self._active_job = None
                self._job_recorder = None
            return ("overview", None)

        # Recorder erst jetzt anlegen (siehe Nachtrag 1 oben) - ab hier
        # sieht auch job_active()/die Uebersicht einen MSCAN-Recorder;
        # welcher es ist, entscheidet make_recorder() (T-1019b).
        # t0_wall/t0_mono (Nachtrag 4): im selben Moment erfasstes (Wanduhr-,
        # Monotonic-)Referenzpaar, aus dem der Segmenter recordings[].start_at
        # zurueckrechnet (eb200_core/mscan_run.py) - deshalb reicht Phase e
        # unten ab jetzt den ABSOLUTEN time.monotonic()-Wert als t durch,
        # nicht mehr "seit Schleifenstart vergangene Sekunden" (Differenzen/
        # Vergleiche im Segmenter - duration_s, gap_s - bleiben davon
        # unberuehrt, siehe Bericht).
        t0_wall = time.time()
        t0_mono = time.monotonic()
        # T-1019b: record_audio False = Pegel-Inventur ohne Ton. EINMAL hier
        # ausgewertet und danach als lokale Wahrheit benutzt (Sockets,
        # Rueckbau, Log) - params kommt aus dem eingefrorenen Manifest und
        # aendert sich waehrend des Laufs nicht.
        record_audio = params.get("record_audio", True) is not False
        recorder = make_recorder(self.job_store, manifest, AUDIO_REMOTE_MODE,
                                 t0_wall=t0_wall, t0_mono=t0_mono)
        with self._job_lock:
            self._job_recorder = recorder

        final_state: Optional[str] = None
        abort_code: Optional[str] = None
        abort_detail: Optional[str] = None
        msc_sock: Optional[socket.socket] = None
        aud_sock: Optional[socket.socket] = None

        # Jeder Abbruch in Phase b/c/d (VOR SENSe:MSCan-Start/ST_LAEUFT
        # unten) setzt final_state IMMER auf ST_ABGEBROCHEN, NIE auf
        # ST_BEENDET/ST_GESTOPPT (Nachtrag 2: ein Lauf, der nie lief, ist
        # nicht "beendet" - das gilt auch fuer ein waehrend des
        # Programmierens eingetroffenes job_mscan_stop, dessen eigener
        # Zielzustand deshalb an diesen Stellen NICHT uebernommen wird),
        # und druckt "Startfehler in Phase <x>: <grund>" auf stderr NOCH
        # VOR dem finally-Block.
        try:
            # b) Phase "programmieren" (Muster tools/mscan_probe.py
            # run_phase_c(), Befehle/Parser aus eb200_core/mscan_run.py).
            recorder.phase = "programmieren"
            self.scpi.send("ABORt")
            self.scpi.send("FREQ:MODE CW")
            self.scpi.send("FORMat:MEMory ASCii")

            ok, resp = self._query_expect("MEMory:CONTents? RX",
                                          lambda r: len(r.strip().split(",")) == 10)
            if not ok:
                final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "antenna_unknown", f"MEMory:CONTents? RX -> {resp}"
                print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                return ("overview", None)
            try:
                antenna = parse_memory_readback(resp)["antenna"]
            except Exception as exc:
                final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "antenna_unknown", f"MEMory:CONTents? RX -> {exc!r}"
                print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                return ("overview", None)

            self.scpi.send(MEMORY_CLEAR_COMMAND)
            wait_opc(self.scpi)

            for i, ch in enumerate(channels):
                self.scpi.send(memory_write_command(i, ch, antenna))
                # *OPC? nach JEDEM Platz (Nachtrag 5, vormals nur alle 10):
                # kostet nichts zusaetzlich (das Geraet braucht ohnehin
                # ~200-500 ms/Platz, siehe docs/EB200_Geraeteprofil.md) und
                # haelt die SCPI-Verbindung synchron - Hochrechnung bleibt
                # ~4 min fuer 1000 Plaetze.
                wait_opc(self.scpi)

                # Zwischen den Kanaelen: stop_event/Kommandos pruefen - die
                # 423-Sperre (job_active()) haelt Browser-Aktionen ohnehin
                # fern, ein trotzdem eintreffendes FREMDES Kommando wird
                # daher folgenlos verworfen (nur "job_mscan_stop" wirkt).
                if self.stop_event.is_set():
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "server_stop", None
                    print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                    return None
                try:
                    cmd = self.commands.get_nowait()
                except queue.Empty:
                    pass
                else:
                    kind, value = cmd
                    if kind == "job_mscan_stop":
                        _requested_state, _stop_code, _stop_detail = value
                        final_state = ST_ABGEBROCHEN
                        abort_code = _stop_code or "stop_while_programming"
                        abort_detail = _stop_detail
                        print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})",
                              file=sys.stderr)
                        return ("overview", None)

                if (i + 1) % 50 == 0:
                    recorder.programmed = i + 1
                    print(f"[jobs] MSCAN-Lauf {job_id}: {i + 1}/{n} Plaetze programmiert")
            recorder.programmed = n

            # Synchronisationsanker (Nachtrag 5) vor dem Readback: der
            # letzte Schreibbefehl ist mit *OPC? oben bereits bestaetigt -
            # trotzdem sicherstellen, dass keine verspaetete Altantwort im
            # Empfangspuffer liegt, bevor MPAR?/CONTents? gelesen werden
            # (siehe _scpi_resync()/Bericht).
            self._scpi_resync("vor Readback")

            # Readback: MPAR? fuer ALLE Plaetze (muss aktiv sein), voller
            # Feldabgleich (memory_matches) nur fuer die ersten 5 und den
            # letzten Platz (Stichprobe - 1000 volle Reads waeren unnoetig
            # langsam, siehe Zeitbedarf in docs/EB200_Geraeteprofil.md).
            # _query_expect() prueft/wiederholt jede Abfrage einzeln
            # (Nachtrag 5) statt sie blind zu uebernehmen.
            for i in range(n):
                ok, mpar = self._query_expect(f"MEMory:CONTents:MPAR? MEM{i}",
                                              lambda r: r.strip() in ("0", "1"))
                if not ok:
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "program_failed", f"MPAR? MEM{i} implausible: {mpar}"
                    print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                    return ("overview", None)
                if mpar.strip() not in ("1", "ON"):
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "program_failed", f"MEM{i} not activated (MPAR? -> {mpar!r})"
                    print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                    return ("overview", None)

            pruef_indices = list(range(min(5, n)))
            if n > 5:
                pruef_indices.append(n - 1)
            for i in pruef_indices:
                ok, resp = self._query_expect(f"MEMory:CONTents? MEM{i}",
                                              lambda r: len(r.strip().split(",")) == 10)
                if not ok:
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "program_failed", f"readback MEM{i} implausible: {resp}"
                    print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                    return ("overview", None)
                try:
                    rb = parse_memory_readback(resp)
                except Exception as exc:
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "program_failed", f"readback MEM{i} not parsable: {exc!r}"
                    print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                    return ("overview", None)
                diffs = memory_matches(channels[i], rb)
                if diffs:
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "program_failed", f"MEM{i} differs: {'; '.join(diffs)}"
                    print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase b: {abort_code} ({abort_detail})", file=sys.stderr)
                    return ("overview", None)

            # c) Sockets fuer MSC (eigener Port) und AUD (Muster
            # _start_job_audio(): eigener Port, PLAINE TAG:ON-Registrierung).
            # T-1019b: der AUD-Socket entsteht nur mit Tonaufnahme - ohne ihn
            # bleibt aud_sock None, und die Empfangsschleife wartet unten nur
            # auf den MSC-Socket.
            try:
                msc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                msc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                msc_sock.bind(("0.0.0.0", UDP_PORT_JOB_MSCAN))
                msc_sock.setblocking(False)
                if record_audio:
                    aud_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    aud_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    aud_sock.bind(("0.0.0.0", UDP_PORT_JOB_AUDIO))
                    aud_sock.setblocking(False)
            except OSError as exc:
                final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "udp_bind_failed", f"{exc!r}"
                print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase c: {abort_code} ({abort_detail})", file=sys.stderr)
                return ("overview", None)

            # d) Scan-Aufbau + Start (eb200_core/mscan_run.
            # scan_cleanup_paths_commands/scan_setup_commands/
            # scan_start_command - Reihenfolge/Inhalt dort, NICHT hier
            # dupliziert).
            #
            # Vorab-Raeumung (Nachtrag 3, Hardwarebefund): TRAC:UDP:DELete
            # auf beiden UdpPaths raeumt CMOS-Reste eines frueheren Laufs
            # weg - ein -200 "Execution error" auf einem noch NICHT
            # registrierten Pfad (frischer Serverstart) ist dabei der
            # Normalfall, KEIN Startfehler: nur loggen, nicht abbrechen.
            for cmd_str in scan_cleanup_paths_commands(self.local_ip, UDP_PORT_JOB_MSCAN,
                                                        UDP_PORT_JOB_AUDIO):
                self.scpi.send(cmd_str)
            cleanup_errors = drain_errors(self.scpi)
            print(f"[jobs] MSCAN-Lauf {job_id}: UdpPath-Vorabraeumung: "
                  f"{cleanup_errors if cleanup_errors else 'keine Reste'}")

            # Eigentlicher Scan-Aufbau: JEDER Fehler hier ist ein
            # Startfehler. Fehlerpuffer nach JEDEM einzelnen Befehl lesen
            # (~4 ms/Abfrage, bei den ca. 14 Befehlen unerheblich) - damit
            # der Abbruchgrund den sendenden Befehl nennt und ein
            # kuenftiger -200 sofort zuzuordnen ist.
            for cmd_str in scan_setup_commands(params, self.local_ip, UDP_PORT_JOB_MSCAN,
                                                UDP_PORT_JOB_AUDIO, AUDIO_REMOTE_MODE):
                self.scpi.send(cmd_str)
                errors = drain_errors(self.scpi)
                if errors:
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "scpi_error", f"{cmd_str} -> {errors}"
                    print(f"[jobs] MSCAN-Lauf {job_id} Startfehler in Phase d: {abort_code} ({abort_detail})",
                          file=sys.stderr)
                    return ("overview", None)
            self.scpi.send(scan_start_command())
            recorder.phase = "scan"
            # T-1019h: Ab hier laeuft der Scan - der Nullpunkt der Zeitachse
            # einer Pegel-Inventur (beim Tonlauf wirkungslos, siehe
            # eb200_core/mscan_run.py). Die Spanne davor ist die
            # Programmierdauer der Speicherplaetze.
            recorder.mark_scan_start(time.monotonic())
            self.job_store.set_state(job_id, ST_LAEUFT)
            # T-1019b: Betriebsart vollstaendig protokollieren - der Modus
            # allein sagt seit stop_after/record_audio nicht mehr, was der
            # Lauf tut.
            ton_txt = "mit Ton" if record_audio else "nur Pegel"
            schwelle = params.get("level_active_dbuv")
            schwelle_txt = "" if schwelle is None else f", Schwelle {schwelle} dBuV"
            print(f"[jobs] MSCAN-Lauf {job_id} gestartet ({manifest['label']!r}, "
                  f"{n} Kanaele, Modus {params.get('mode')}/{params.get('stop_after')}, "
                  f"{ton_txt}{schwelle_txt})")

            # e) Empfangsschleife. t = now (ABSOLUTER time.monotonic()-Wert,
            # siehe t0_mono oben) statt "seit Schleifenstart vergangene
            # Sekunden" - noetig, damit der Segmenter daraus per t0_wall/
            # t0_mono eine Wanduhrzeit fuer recordings[].start_at bilden
            # kann. last_end_check_t bleibt unabhaengig davon einfach der
            # Monotonic-Wert bei Schleifenstart.
            last_end_check_t = time.monotonic()
            while True:
                now = time.monotonic()
                t = now

                if self.stop_event.is_set():
                    final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "server_stop", None
                    return None

                try:
                    cmd = self.commands.get_nowait()
                except queue.Empty:
                    pass
                else:
                    kind, value = cmd
                    if kind == "job_mscan_stop":
                        final_state, abort_code, abort_detail = value
                        return ("overview", None)
                    # sonstige Kommandos: 423-Sperre haelt den Browser
                    # ohnehin fern, ein trotzdem eingetroffenes Kommando
                    # wird folgenlos verworfen.

                if recorder.failed:
                    final_state, abort_code, abort_detail = (
                        ST_ABGEBROCHEN, recorder.failed_code or "recording_error", recorder.failed)
                    return ("overview", None)
                if recorder.finished:
                    final_state, abort_code, abort_detail = ST_BEENDET, None, None
                    return ("overview", None)

                if now - last_end_check_t >= 5.0:
                    last_end_check_t = now
                    end_iso = manifest.get("end_iso")
                    if end_iso is not None and datetime.fromisoformat(end_iso) <= datetime.now().astimezone():
                        final_state, abort_code, abort_detail = ST_BEENDET, None, None
                        return ("overview", None)

                # T-1019b: ohne Tonaufnahme gibt es keinen AUD-Socket -
                # select() bekommt dann nur den MSC-Socket (ein None in der
                # Liste wuerde sofort mit einem Fehler abbrechen).
                lese_sockets = [msc_sock] if aud_sock is None else [msc_sock, aud_sock]
                ready, _, _ = select.select(lese_sockets, [], [], 0.25)
                for sock in ready:
                    try:
                        raw, _addr = sock.recvfrom(65535)
                    except OSError:
                        continue
                    try:
                        attrs = parse_datagram(raw)
                    except ValueError as exc:
                        print(f"[controller] MSCAN-Datagramm ignoriert: {exc}", file=sys.stderr)
                        continue

                    if sock is msc_sock:
                        for tag, payload in attrs:
                            if tag != MSCAN_TAG:
                                continue
                            try:
                                n_items, _res, opt_len, flags = struct.unpack_from(
                                    FMT_TRACE_ATTR_HDR, payload, 0)
                                opt = payload[LEN_TRACE_ATTR_HDR:LEN_TRACE_ATTR_HDR + opt_len]
                                data = payload[LEN_TRACE_ATTR_HDR + opt_len:]
                                result = parse_mscan_items(flags, opt, data, n_items)
                            except (struct.error, ValueError) as exc:
                                print(f"[controller] MSCAN-Item-Parsefehler: {exc}", file=sys.stderr)
                                continue
                            header = result["header"]
                            cycle = header["cycle_count"] if header is not None else None
                            for item in result["items"]:
                                if item["is_marker"]:
                                    recorder.record_cycle_end(t, cycle)
                                else:
                                    recorder.record_item(t, item["freq_hz"], item["level_dbuv"], cycle)
                    else:   # aud_sock - Muster _run_job_audio_loop()
                        for tag, data in attrs:
                            if tag != TAG_AUDIO:
                                continue
                            try:
                                number_of_frames, _res, _opt_len, _flags = struct.unpack_from(
                                    FMT_TRACE_ATTR_HDR, data, 0)
                            except struct.error as exc:
                                print(f"[controller] Job-Audio-Attribut (MSCAN) ignoriert: {exc}",
                                      file=sys.stderr)
                                continue
                            if number_of_frames == 0:
                                # 28-Byte-Marker (nur Header, kein Nutzsignal) -
                                # empirisches Segmentende, siehe
                                # docs/EB200_Geraeteprofil.md Abschnitt MSCAN.
                                recorder.record_audio_marker(t)
                            else:
                                try:
                                    audio_pcm = job_audio_payload(data)
                                except ValueError as exc:
                                    print(f"[controller] Job-Audio-Payload (MSCAN) ignoriert: {exc}",
                                          file=sys.stderr)
                                    continue
                                recorder.record_audio(t, audio_pcm)

                recorder.tick(t)
        finally:
            recorder.phase = "ende"
            for cmd_str in scan_teardown_commands(self.local_ip, UDP_PORT_JOB_MSCAN,
                                                   UDP_PORT_JOB_AUDIO,
                                                   record_audio=record_audio):
                try:
                    self.scpi.send(cmd_str)
                except Exception as exc:
                    print(f"[controller] MSCAN-Teardown-Befehl {cmd_str!r} fehlgeschlagen: {exc!r}",
                          file=sys.stderr)
            if msc_sock is not None:
                msc_sock.close()
            if aud_sock is not None:
                aud_sock.close()
            # Jeder der folgenden drei Aufraeumschritte bekommt ein EIGENES
            # try/except (Nachtrag 2): ein Fehler in einem Schritt darf die
            # naechsten nicht ueberspringen, und der Controller-Thread darf
            # hier NIE sterben (sonst haengt der Empfaenger dauerhaft
            # gesperrt, ohne dass je wieder etwas passiert).
            try:
                stats = recorder.close()
            except Exception as exc:
                stats = {}
                print(f"[jobs] MSCAN-Lauf {job_id}: recorder.close() fehlgeschlagen: {exc!r}",
                      file=sys.stderr)
            if final_state is None:
                # Sollte durch die Empfangsschleife immer gesetzt sein -
                # Sicherheitsnetz gegen einen unerwarteten Ausstiegspfad
                # (z. B. eine hier nicht abgefangene Ausnahme), damit der
                # Job nie unbestimmt "laeuft" stehen bleibt.
                final_state, abort_code, abort_detail = ST_ABGEBROCHEN, "unexpected_exit", None
            try:
                self.job_store.set_state(job_id, final_state,
                                         abort_code=abort_code, abort_detail=abort_detail)
            except Exception as exc:
                print(f"[jobs] MSCAN-Lauf {job_id}: set_state -> {final_state!r} "
                      f"fehlgeschlagen: {exc!r}", file=sys.stderr)
            try:
                self.mscan_store.set_last_run(params["mscan_job_id"], manifest["start_iso"])
            except Exception as exc:
                print(f"[jobs] set_last_run fuer MSCAN-Job {params.get('mscan_job_id')} "
                      f"fehlgeschlagen: {exc!r}", file=sys.stderr)
            with self._job_lock:
                self._active_job = None
                self._job_recorder = None
            print(f"[jobs] MSCAN-Lauf {job_id} beendet -> {final_state} "
                  f"({stats.get('recordings', 0)} Aufnahmen auf "
                  f"{stats.get('active_channels', 0)} Kanaelen)")

    def request_snapshot(self, delay_s: float = 0.0, do_inventory: bool = True,
                          start_poll_loop: bool = False) -> bool:
        """Startet eine EB200-Inventur im Hintergrund (eigener Thread, eigene
        SCPI-Verbindung). Läuft bereits eine, wird False zurückgegeben und
        nichts gestartet - es gibt nie zwei gleichzeitig.

        do_inventory=False ueberspringt die einmalige Voll-Inventur (fuer den
        Fall SNAPSHOT_ON_START=False); start_poll_loop=True laesst denselben
        Thread/dieselbe Verbindung im Anschluss NAHTLOS in die Dauerschleife
        des T-703-Parameter-Pollings uebergehen (_run_param_poll_loop) statt
        sich zu schliessen - siehe dort. NUR der Start-Aufruf in run() setzt
        start_poll_loop=True; der manuelle Re-Inventur-Endpunkt
        (/api/params/snapshot -> request_snapshot() ohne Argumente) bleibt
        unveraendert ein einmaliger Vorgang mit eigener, kurzlebiger
        Verbindung - so bleiben in Dauerbetrieb genau zwei SCPI-Verbindungen
        zum Geraet bestehen (Hauptkanal self.scpi + diese eine
        Zweitverbindung), keine dritte kommt permanent hinzu."""
        with self._snapshot_lock:
            if self._snapshot_running:
                return False
            self._snapshot_running = True
        threading.Thread(target=self._run_snapshot_background,
                          args=(delay_s, do_inventory, start_poll_loop),
                          daemon=True, name="eb200-snapshot").start()
        return True

    def snapshot_running(self) -> bool:
        with self._snapshot_lock:
            return self._snapshot_running

    def _run_snapshot_background(self, delay_s: float, do_inventory: bool = True,
                                  start_poll_loop: bool = False) -> None:
        if delay_s > 0:
            time.sleep(delay_s)
        client = None
        if do_inventory:
            t0 = time.monotonic()
            try:
                client = ScpiClient(self.eb200_ip, SCPI_PORT)
                snap = snapshot_eb200(client, pause_s=SNAPSHOT_QUERY_PAUSE_S)
                archive = self.param_store.put_eb200_snapshot(snap)
                m = snap["meta"]
                print(f"[params] EB200-Inventur (Hintergrund): {m['n_ok']} OK, "
                      f"{m['n_failed']} Fehler, {time.monotonic() - t0:.1f} s -> {archive}")
            except Exception as exc:
                print(f"[params] EB200-Inventur fehlgeschlagen ({exc!r})", file=sys.stderr)
        with self._snapshot_lock:
            self._snapshot_running = False

        if start_poll_loop:
            # Dieselbe (ggf. gerade erst geoeffnete) Verbindung fuer das
            # T-703-Dauerpolling weiterverwenden statt sie zu schliessen.
            self._run_param_poll_loop(client)
        elif client is not None:
            client.close()

    def _set_last_device_params(self, params: dict) -> None:
        with self._device_params_lock:
            self._last_device_params = params

    def get_last_device_params(self) -> Optional[dict]:
        """Fuer den Initial-Push neuer /events-Verbindungen (T-703): letzter
        per Parameter-Polling ermittelter Geraetezustand, oder None, solange
        noch kein Poll erfolgreich war."""
        with self._device_params_lock:
            return dict(self._last_device_params) if self._last_device_params is not None else None

    def _run_param_poll_loop(self, client: Optional[ScpiClient]) -> None:
        """
        T-703: fragt alle PARAM_POLL_INTERVAL_S DEM?/BAND?/OUTP:SQU:STAT?/
        OUTP:SQU:THR?/FREQ:DSC:SPE?/GCON:MODE?/GCON:MGC? ab - REIN LESEND. Ueber diese Verbindung
        laufen NIE Setz-Kommandos; alles Schreibende bleibt auf dem
        Hauptkanal self.scpi ueber self.commands (Serialisierung dort ueber
        die Command-Queue). Laeuft bis self.stop_event gesetzt ist.

        Bei Aenderung (oder beim ersten erfolgreichen Poll) SSE-Event
        "device_params" an alle Browser-Clients (self.broker.
        publish_device_params); der jeweils letzte Stand wird zusaetzlich
        gehalten (_set_last_device_params) fuer den Initial-Push neuer
        /events-Verbindungen (behebt den T-703-Reload-Fall).

        Verbindungsverlust laesst den Thread NICHT sterben: bei Fehler/
        Timeout wird die Verbindung geschlossen und nach
        PARAM_POLL_RECONNECT_BACKOFF_S neu aufgebaut (kein staendiges
        Neuverbinden, das einen der zehn TCP-Client-Slots des Geraets
        flattern liesse). Eine Meldung erscheint nur beim Zustandswechsel
        erreichbar<->nicht erreichbar, kein Log pro Fehlversuch; der letzte
        bekannte Parameterstand bleibt waehrend eines Ausfalls gueltig
        (kein SSE-Spam).
        """
        last_params: Optional[dict] = None
        reachable = client is not None

        while not self.stop_event.is_set():
            if client is None:
                try:
                    client = ScpiClient(self.eb200_ip, SCPI_PORT)
                except Exception:
                    if reachable:
                        print("[params] Parameter-Polling: EB200 nicht erreichbar "
                              "(Reconnect-Versuche laufen weiter)", file=sys.stderr)
                        reachable = False
                    self.stop_event.wait(PARAM_POLL_RECONNECT_BACKOFF_S)
                    continue

            try:
                raw = {
                    "dem": client.query("DEM?"),
                    "band_hz": client.query("BAND?"),
                    "squelch_on": client.query("OUTP:SQU:STAT?"),
                    "squelch_thr": client.query("OUTP:SQU:THR?"),
                    "gain_mode": client.query("GCON:MODE?"),
                    "gain_mgc": client.query("GCON:MGC?"),
                    "speed": client.query("FREQ:DSC:SPE?"),
                    # T-510: AFC-Zustand und tatsaechliche Empfangsfrequenz.
                    # Die AFC verstellt die Frequenz selbsttaetig; der zuletzt
                    # GESETZTE Wert ist dann nicht mehr der gueltige. Beide
                    # Queries sind rein lesend (Handbuch 4.64/4.66).
                    "afc_on": client.query("FREQ:AFC?"),
                    "freq_hz": client.query("FREQ?"),
                }
            except Exception as exc:
                client.close()
                client = None
                if reachable:
                    print(f"[params] Parameter-Polling: EB200 nicht erreichbar ({exc!r}), "
                          f"Reconnect-Versuche laufen weiter", file=sys.stderr)
                    reachable = False
                self.stop_event.wait(PARAM_POLL_RECONNECT_BACKOFF_S)
                continue

            if not reachable:
                print("[params] Parameter-Polling: EB200 wieder erreichbar")
                reachable = True
            try:
                new_params = normalize_device_params(raw)
            except (TypeError, ValueError) as exc:
                # Unplausible/leere Antwort (z. B. waehrend eines Modus-
                # wechsels) - Zyklus still ueberspringen, letzter Stand
                # bleibt gueltig.
                print(f"[params] Parameter-Polling: Antwort nicht auswertbar ({exc!r})",
                      file=sys.stderr)
                self.stop_event.wait(PARAM_POLL_INTERVAL_S)
                continue
            self._set_last_device_params(new_params)
            if params_changed(last_params, new_params):
                self.broker.publish_device_params(new_params)
                last_params = new_params

            self.stop_event.wait(PARAM_POLL_INTERVAL_S)

        if client is not None:
            client.close()

    def _mirror_runtime_params(self) -> None:
        """Spiegelt die aktuellen Laufzeitparameter des Wasserfalls nach
        app.waterfall.* im Parameter-Memory (ein Schreibvorgang). Reine
        Anzeige-Quelle für die Status-Page; nicht-fatal."""
        try:
            self.param_store.set_many({
                "app.waterfall.http_port": HTTP_PORT,
                "app.waterfall.eb200_ip": self.eb200_ip,
                "app.waterfall.dscan_start_hz": self.dscan_start_hz,
                "app.waterfall.dscan_stop_hz": self.dscan_stop_hz,
                "app.waterfall.dscan_speed": self.dscan_speed,
                "app.waterfall.dscan_bw_hz": self.dscan_bw_hz,
                "app.waterfall.updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
        except Exception as exc:
            print(f"[params] Spiegeln der Laufzeitparameter fehlgeschlagen ({exc!r})",
                  file=sys.stderr)

    def _read_dscan_config_from_device(self) -> None:
        """
        Übernimmt die D-SCAN-Startkonfiguration beim Programmstart vom GERÄT
        (CMOS-persistente Einstellungen, Annex C/F) statt fix aus den Skript-
        Konstanten: Start-/Stopfrequenz, Speed und IF-Bandbreite. So startet
        der Wasserfall mit dem Bereich, der zuletzt am Gerät bzw. in der
        letzten Sitzung eingestellt war - das ersetzt zugleich weitgehend den
        Backlog-Punkt "letzten Bereich über Neustarts persistieren" (der
        Merker ist das Gerät selbst). Jeder Wert einzeln abgesichert: schlägt
        eine Query fehl oder ist das Ergebnis unplausibel, bleibt der
        jeweilige Skript-Default (DSCAN_*) stehen - der Start darf daran
        niemals scheitern.
        """
        try:
            start_hz = int(round(float(self.scpi.query("FREQ:DSC:STAR?"))))
            stop_hz = int(round(float(self.scpi.query("FREQ:DSC:STOP?"))))
            # Plausibilität wie im /api/range-Handler (Annex C-Grenzen).
            if 10_000 <= start_hz < stop_hz <= 3_000_000_000:
                self.dscan_start_hz = start_hz
                self.dscan_stop_hz = stop_hz
            else:
                print(f"[controller] Geräte-Bereich unplausibel "
                      f"({start_hz}-{stop_hz} Hz) - Skript-Defaults bleiben.")
        except Exception as exc:
            print(f"[controller] D-SCAN-Bereich nicht vom Gerät lesbar "
                  f"({exc!r}) - Skript-Defaults bleiben.", file=sys.stderr)
        try:
            speed = self.scpi.query("FREQ:DSC:SPE?").strip().upper()
            if speed in ("LOW", "NORM", "HIGH", "MTIM"):
                self.dscan_speed = speed
        except Exception as exc:
            print(f"[controller] D-SCAN-Speed nicht vom Gerät lesbar "
                  f"({exc!r}) - Skript-Default bleibt.", file=sys.stderr)
        try:
            # MEASURE TIME (Sekunden). "DEF" (DEFAULT-Zustand) -> None,
            # ein sonstiger nicht als Float parsbarer Wert laesst den
            # bisherigen Skript-Stand (Default None) unveraendert stehen -
            # bewusst tolerant, der Start darf daran nie scheitern.
            raw = self.scpi.query("MEAS:TIME?").strip().upper()
            if raw.startswith("DEF"):
                self.dscan_mtime_s = None
            else:
                mtime_s = float(raw)
                if 0.0005 <= mtime_s <= 900.0:
                    self.dscan_mtime_s = mtime_s
        except Exception as exc:
            print(f"[controller] MEASURE TIME nicht vom Gerät lesbar "
                  f"({exc!r}) - Skript-Default bleibt.", file=sys.stderr)
        try:
            # Detektor (PEAK/AVG/FAST) - bestimmt bei MTIME=DEFAULT die
            # Spalte der Annex-E-Tabelle, die das Frontend anzeigt.
            det = self.scpi.query("DET?").strip().upper()
            if det in ("PEAK", "AVG", "FAST"):
                self.dscan_detector = det
        except Exception as exc:
            print(f"[controller] Detektor nicht vom Gerät lesbar "
                  f"({exc!r}) - Default PEAK bleibt.", file=sys.stderr)
        try:
            # BAND ist am Gerät EIN gemeinsamer Parameter (D-SCAN wie IFPAN);
            # auf die nächste der 12 ZF-Stufen rasten, damit der Wert exakt
            # einer <option> des BW-Selects im Frontend entspricht.
            self.dscan_bw_hz = nearest_bw_stage(
                int(round(float(self.scpi.query("BAND?")))))
        except Exception as exc:
            print(f"[controller] IF-Bandbreite nicht vom Gerät lesbar "
                  f"({exc!r}) - Skript-Default bleibt.", file=sys.stderr)
        print(f"[controller] Startkonfiguration vom Gerät: "
              f"{self.dscan_start_hz/1e6:.3f}-{self.dscan_stop_hz/1e6:.3f} MHz, "
              f"Speed {self.dscan_speed}, BW {self.dscan_bw_hz} Hz")

    def run(self) -> None:
        print(f"[controller] verbinde SCPI zu {self.eb200_ip}:{SCPI_PORT} ...")
        self.scpi = ScpiClient(self.eb200_ip, SCPI_PORT)

        # Startkonfiguration vom Gerät übernehmen (statt fixer Skript-Werte) -
        # MUSS vor dem ersten _configure_dscan()/Frontend-Abruf geschehen.
        self._read_dscan_config_from_device()

        # EB200-Inventur ins Parameter-Memory - im HINTERGRUND mit eigener
        # SCPI-Verbindung (das Gerät erlaubt mehrere Remote-Clients, Handbuch
        # 4.x "list socket"), damit der Sweep nicht auf ~70 Queries wartet.
        # Start verzögert um SNAPSHOT_START_DELAY_S, damit der erste Sweep
        # sicher läuft, bevor die Leitung zusätzlich belegt wird. Derselbe
        # Thread/dieselbe Verbindung geht im Anschluss nahtlos in die
        # T-703-Dauerschleife des Parameter-Pollings über (start_poll_loop) -
        # SNAPSHOT_ON_START steuert dabei NUR die einmalige Voll-Inventur
        # (do_inventory), das Polling läuft unabhängig davon immer.
        self.request_snapshot(delay_s=SNAPSHOT_START_DELAY_S, do_inventory=SNAPSHOT_ON_START,
                               start_poll_loop=True)
        self._mirror_runtime_params()

        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.bind(("0.0.0.0", self.udp_port))
        self.udp_sock.settimeout(RECV_TIMEOUT_S)

        # Long-Run-Jobs (WS3 11.5): verwaisten Zustand eines evtl.
        # vorherigen Absturzes/Neustarts aufraeumen, BEVOR der Scheduler
        # anfaengt, geplante Jobs zu starten - sonst koennte ein Job als
        # "laeuft" gelten, obwohl gerade erst neu hochgefahren wird.
        recover_report = self.job_store.recover_on_startup()
        if recover_report["abgebrochen"] or recover_report["verpasst"]:
            print(f"[jobs] Absturz-Erholung: abgebrochen={recover_report['abgebrochen']}, "
                  f"verpasst={recover_report['verpasst']}")
        else:
            print("[jobs] Absturz-Erholung: keine verwaisten Jobs")
        threading.Thread(target=self._job_scheduler_loop, daemon=True, name="job-scheduler").start()
        threading.Thread(target=self._sched_watchdog_loop, daemon=True, name="sched-watchdog").start()

        mode = "overview"
        zoom_center_hz = None
        mscan_manifest = None
        try:
            while True:
                if mode == "overview":
                    cmd = self._run_overview_loop()
                elif mode == "zoom":
                    cmd = self._run_zoom_loop(zoom_center_hz)
                    # T-708: die inline verarbeitete Feinabstimmung kann die
                    # Mittenfrequenz verschoben haben - fuer einen Zoom-
                    # NEUSTART (Span/Mode/BW) den aktuellen Stand uebernehmen.
                    if self.zoom_center_hz is not None:
                        zoom_center_hz = self.zoom_center_hz
                else:
                    # mode == "mscan": _run_mscan_loop() liefert wie die
                    # anderen beiden Loops entweder ein Folge-Kommando
                    # (immer ("overview", None) - ein MSCAN-Lauf fuehrt nie
                    # direkt in den IFPAN-Zoom) oder None bei stop_event.
                    cmd = self._run_mscan_loop(mscan_manifest)

                if cmd is None:
                    break  # stop_event wurde gesetzt
                kind, value = cmd
                if kind == "zoom":
                    mode, zoom_center_hz = "zoom", value
                elif kind == "overview":
                    mode = "overview"
                elif kind == "job_mscan_start":
                    mode, mscan_manifest = "mscan", value
                elif kind == "range":
                    # Neuer D-SCAN-Bereich: übernehmen und (auch aus dem
                    # Zoom heraus) zur Übersicht zurückkehren, damit der
                    # neue Bereich sofort sichtbar wird.
                    self.dscan_start_hz, self.dscan_stop_hz = value
                    mode = "overview"
                    self._mirror_runtime_params()
                elif kind == "speed":
                    self.dscan_speed = value
                    mode = "overview"
                    self._mirror_runtime_params()
                elif kind == "mtime":
                    self.dscan_mtime_s = float(value) if value is not None else None
                    # Nur neu konfigurieren, wenn MTIME ueberhaupt wirkt - bei
                    # LOW/NORM/HIGH aendert sich am Geraet nichts, ein Sweep-
                    # Neustart waere reine Stoerung.
                    if self.dscan_speed == "MTIM":
                        mode = "overview"
                elif kind == "span":
                    # IFPAN-Span geändert: Wert übernehmen, Modus NICHT
                    # erzwingen (im Gegensatz zu range/speed) - der Regler
                    # sitzt in der Zoom-Ansicht, betrifft also nur IFPAN.
                    # War man bereits im Zoom, startet dieser mit der
                    # gleichen Mittenfrequenz, aber neuer Breite neu.
                    self.zoom_span_hz = value
                elif kind == "mode":
                    # Modulation geändert - wie bei span: nur übernehmen,
                    # Modus nicht erzwingen. Wirkt erst beim (Re-)Start von
                    # _configure_ifpan (z. B. durch den nächsten Zoom-Klick
                    # oder eine Neuzentrierung).
                    self.ifpan_mode = value
                elif kind == "bw":
                    self.ifpan_bw_hz = value
                elif kind == "squelch":
                    # kein SCPI noetig - Audio laeuft nur im Zoom; wirkt beim
                    # naechsten Zoom-Eintritt ueber _configure_ifpan.
                    self.squelch_on, self.squelch_thr_dbuv = value
                elif kind == "gain":
                    # T-711: Wert uebernehmen; die Uebersicht wird durch die
                    # Rueckkehr aus _run_overview_loop() ohnehin sofort neu
                    # aufgebaut (mode ist unveraendert "overview"), und
                    # _configure_dscan() setzt die Verstaerkung dann in der
                    # richtigen Reihenfolge am Geraet. Aus dem Zoom-Loop
                    # kommt "gain" nie hier an - der verarbeitet es inline.
                    self.gain_mode, self.gain_mgc_dbuv = value
                elif kind == "dscan_bwzoom":
                    # BW-ZOOM ein/aus: wie speed/range Übersicht erzwingen,
                    # damit die D-SCAN-Konfiguration sofort neu aufgebaut wird.
                    self.dscan_bw_zoom = value
                    mode = "overview"
                elif kind == "dscan_bw":
                    # BW im BW-ZOOM ändern: übernehmen und Übersicht neu
                    # aufbauen (koppelt den Span an die neue BW).
                    self.dscan_bw_hz = value
                    mode = "overview"
                elif kind in ("monitor_start", "monitor_check"):
                    # In der Uebersicht gibt es nichts zu schalten - der Wunsch
                    # steckt in monitor_wanted und wird beim naechsten Zoom-
                    # Eintritt von _run_zoom_loop umgesetzt (_apply_monitor_wish).
                    # monitor_check in der Uebersicht: SOLL anhand der Client-
                    # Zahl nachziehen, damit ein spaeterer Zoom nicht mit einem
                    # verwaisten Wunsch startet.
                    if kind == "monitor_check" and self.audio_broker.client_count() == 0:
                        self.monitor_wanted = False
                elif kind in ("record_start", "record_stop", "spectrum_start", "spectrum_stop"):
                    # Landet hier nur, wenn das Kommando in der Übersicht kam
                    # (im Zoom wird es bereits INLINE in _run_zoom_loop
                    # verarbeitet, siehe dort) - Aufnahme ist nur im IFPAN-
                    # Zoom sinnvoll, ein Overview-Klick bleibt bewusst folgenlos.
                    pass
                elif kind == "tune":
                    # T-708: Feinabstimmung ausserhalb des Zooms (Race beim
                    # Verlassen der Zoom-Ansicht) - bewusst folgenlos, wie
                    # record_* oben.
                    pass
                elif kind == "job_ifpan_cleanup":
                    # Anders als record_*/spectrum_* oben MUSS dieses Kommando
                    # auch dann wirken, wenn es in der Übersicht ankommt (z. B.
                    # weil der Job schon zuvor beendet wurde/abgebrochen ist) -
                    # der Long-Run-Job muss in jedem Fall sauber abgeräumt
                    # werden (Tags aus, Gerät zurück in die Übersicht).
                    self._cleanup_ifpan_job_scpi()
        finally:
            self._delete_udp_path()
            self.scpi.close()
            self.udp_sock.close()
            print("[controller] beendet, Gerät aufgeräumt.")


# ---------------------------------------------------------------------------
# HTML/JS-Frontend
# ---------------------------------------------------------------------------

def _format_khz_label(hz: int) -> str:
    """Kleine, eigenständige kHz-Formatierung nur für die <option>-Labels
    hier im Wasserfall (Komma als Tausender-, Punkt als Dezimaltrennzeichen,
    T-1803a; ohne überflüssige Nullen) - bewusst unabhängig von
    block_admin.py gehalten, da beide Skripte eigenständig lauffähig
    bleiben sollen (kein gemeinsames Modul importiert)."""
    khz = hz / 1000
    s = f"{khz:,.3f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _build_ifpan_mode_options_html() -> str:
    parts = []
    for value in IFPAN_MODE_OPTIONS:
        sel = " selected" if value == IFPAN_MODE_DEFAULT else ""
        parts.append(f'<option value="{value}"{sel}>{value}</option>')
    return "\n        ".join(parts)


def _build_bw_options_html(selected_hz: int) -> str:
    parts = []
    for hz in IFPAN_BW_OPTIONS_HZ:
        sel = " selected" if hz == selected_hz else ""
        parts.append(f'<option value="{hz}"{sel}>{_format_khz_label(hz)} kHz</option>')
    return "\n        ".join(parts)


STATUS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>EB200 Status</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  /* T-1407a: Status-Seite auf Tokens. Grundregeln (body, Knoepfe, Kopfleiste,
     Tabellen) kommen aus BASE_CSS; hier nur die Zusaetze dieser Seite. */
  h1 { font-size:1.15em; font-weight:600; color:var(--fg); margin:14px 0 10px; }
  h3 { font-size:0.9em; font-weight:600; color:var(--fg-muted); margin:12px 0 4px; }
  #topbar { display:flex; align-items:center; gap:16px; font-size:0.85em; margin-bottom:8px; }
  #topbar a { text-decoration:none; }
  #topbar a:hover { text-decoration:underline; }
  #actions { display:flex; align-items:center; gap:12px; font-size:0.85em; margin-bottom:12px; }
  #stand { color:var(--fg-muted); }
  #device-meta { font-size:0.85em; color:var(--fg-muted); margin-bottom:10px; line-height:1.5; }
  details.group { margin:6px 0; border:1px solid var(--border); border-radius:6px;
                  background:var(--panel); padding:0 10px; }
  details.group summary { cursor:pointer; padding:8px 0; color:var(--fg); font-weight:600; font-size:0.9em; }
  details.group[open] summary { border-bottom:1px solid var(--border); }
  table { width:100%; font-size:0.85em; margin:6px 0; }
  table td { padding:4px 8px; vertical-align:top; }
  td.q { color:var(--fg-muted); white-space:nowrap; width:1%; }
  td.err { color:var(--danger); }
  #app table, #store table { max-width:900px; }
  /* T-2013: Block "Connection" - Geraeteadresse ansehen, testen, speichern. */
  #conn { border:1px solid var(--border); border-radius:6px; background:var(--panel);
          padding:8px 12px; max-width:900px; font-size:0.85em; }
  #conn .conn-row { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin:6px 0; }
  #conn label { display:inline-flex; align-items:center; gap:6px; }
  #conn input[type=text] { width:13em; }
  #conn input[type=number] { width:6em; }
  #connInfo, #connMsg { color:var(--fg-muted); }
  #connMsg.err { color:var(--danger); }
  #connMsg.ok { color:var(--success); }
</style>
</head>
<body>
/*HEADER*/
<div id="topbar">
  <a href="/">&larr; Waterfall</a>
</div>
<div id="actions">
  <button onclick="load()">Reload parameter file</button>
  <button id="snapBtn" onclick="triggerSnapshot()">Re-read from EB200</button>
  <span id="stand"></span>
</div>

<h2>Connection</h2>
<div id="conn">
  <div class="conn-row">
    <label title="IP address or host name of the EB200 (LAN option EB200R4)">EB200 address <input type="text" id="connHost" spellcheck="false"></label>
    <label title="SCPI port of the EB200, normally 5555">SCPI port <input type="number" id="connPort" min="1" max="65535"></label>
    <label title="Address of this PC that the EB200 sends its data to. Leave empty to determine it automatically; set it only if the PC has several network interfaces.">Local address <input type="text" id="connLocal" placeholder="automatic" spellcheck="false"></label>
  </div>
  <div class="conn-row">
    <button id="connTestBtn" title="Open a short separate connection to the address above and query *IDN?; the running session is not affected">Test connection</button>
    <button id="connSaveBtn" title="Save the address to device.json in the data folder; it takes effect after restarting the waterfall server (:8000)">Save</button>
    <span id="connMsg"></span>
  </div>
  <div id="connInfo"></div>
</div>

<h2>EB200 parameter values</h2>
<div id="device"></div>

<h2>Application</h2>
<div id="app"></div>

<h2>Parameter store</h2>
<div id="store"></div>

<script>
function flatten(obj, prefix, out) {
  for (const k of Object.keys(obj)) {
    const v = obj[k];
    const path = prefix ? prefix + '.' + k : k;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      flatten(v, path, out);
    } else {
      out.push([path, v]);
    }
  }
}

function cell(text, cls) {
  const td = document.createElement('td');
  if (cls) td.className = cls;
  td.textContent = (text === null || text === undefined) ? '' : String(text);
  return td;
}

function renderDevice(eb200) {
  const root = document.getElementById('device');
  const isFirstBuild = root.childElementCount === 0;
  const openSet = new Set();
  root.querySelectorAll('details.group[open]').forEach(function(d) {
    openSet.add(d.dataset.group);
  });
  root.textContent = '';
  const meta = eb200.meta || null;
  const metaDiv = document.createElement('div');
  metaDiv.id = 'device-meta';
  if (!meta) {
    metaDiv.textContent = 'No inventory yet.';
    root.appendChild(metaDiv);
    return;
  }
  const tParts = String(meta.taken_at || '').split('T');
  const metaLines = [
    'As of: ' + (tParts[0] || '') + '    ' + (tParts[1] || ''),
    'IDN: ' + meta.idn,
    'OPT: ' + meta.opt + '  |  ' + meta.n_ok + ' OK, ' + meta.n_failed + ' ' + pluralWort(meta.n_failed, 'error', 'errors'),
    'Parameter transfer time: ' + meta.duration_s + ' s'
  ];
  for (const line of metaLines) {
    const d = document.createElement('div');
    d.textContent = line;
    metaDiv.appendChild(d);
  }
  root.appendChild(metaDiv);

  const groups = eb200.groups || {};
  const params = eb200.params || {};
  const errors = eb200.errors || {};
  const defaultOpen = new Set(['rx', 'dscan']);

  for (const g of Object.keys(groups).sort()) {
    const det = document.createElement('details');
    det.className = 'group';
    det.dataset.group = g;
    det.open = isFirstBuild ? defaultOpen.has(g) : openSet.has(g);
    const sum = document.createElement('summary');
    sum.textContent = g;
    det.appendChild(sum);

    const table = document.createElement('table');
    for (const q of groups[g]) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(q, 'q'));
      if (Object.prototype.hasOwnProperty.call(errors, q)) {
        tr.appendChild(cell(errors[q], 'err'));
      } else {
        tr.appendChild(cell(params[q]));
      }
      table.appendChild(tr);
    }
    det.appendChild(table);
    root.appendChild(det);
  }
}

function renderApp(app) {
  const root = document.getElementById('app');
  root.textContent = '';
  const topKeys = Object.keys(app);
  if (topKeys.length === 0) {
    const p = document.createElement('p');
    p.textContent = 'No entries yet';
    root.appendChild(p);
    return;
  }
  topKeys.sort((a, b) => {
    if (a === 'waterfall') return -1;
    if (b === 'waterfall') return 1;
    return a < b ? -1 : a > b ? 1 : 0;
  });
  for (const top of topKeys) {
    const h = document.createElement('h3');
    h.textContent = top;
    root.appendChild(h);
    const rows = [];
    flatten(app[top], '', rows);
    const table = document.createElement('table');
    for (const pair of rows) {
      const tr = document.createElement('tr');
      tr.appendChild(cell(pair[0], 'q'));
      tr.appendChild(cell(pair[1]));
      table.appendChild(tr);
    }
    root.appendChild(table);
  }
}

function renderStore(data) {
  const root = document.getElementById('store');
  root.textContent = '';
  const meta = data.meta || {};
  const rows = [
    ['Schema version', meta.schema],
    ['Last written', meta.updated_at],
    ['File', data._store_path],
  ];
  const table = document.createElement('table');
  for (const pair of rows) {
    const tr = document.createElement('tr');
    tr.appendChild(cell(pair[0], 'q'));
    tr.appendChild(cell(pair[1]));
    table.appendChild(tr);
  }
  root.appendChild(table);
}

// T-2013: Block "Connection". Fehler kommen vom Server als Code
// (device.invalid_host usw.), der Satz dazu entsteht hier.
const CONN_TEXTE = {
  'device.invalid_host': 'Invalid EB200 address.',
  'device.invalid_port': 'Invalid SCPI port (1-65535).',
  'device.invalid_local_ip': 'Invalid local address.',
  'device.unreachable': 'EB200 not reachable: ',
  'device.write_failed': 'Could not save: '
};
function connMeldung(text, art) {
  const m = document.getElementById('connMsg');
  m.textContent = text;
  m.className = art || '';
}
function connFehler(d) {
  const basis = CONN_TEXTE[d.code] || ('Error: ' + d.code);
  return basis + ((d.params && d.params.detail) ? d.params.detail : '');
}
function connEingabe() {
  return {
    eb200_host: document.getElementById('connHost').value.trim(),
    scpi_port: parseInt(document.getElementById('connPort').value, 10),
    local_ip: document.getElementById('connLocal').value.trim()
  };
}
function loadConn() {
  fetch('/api/device').then(function(r) { return r.json(); }).then(function(d) {
    document.getElementById('connHost').value = d.configured.eb200_host;
    document.getElementById('connPort').value = d.configured.scpi_port;
    document.getElementById('connLocal').value = d.configured.local_ip || '';
    const a = d.active;
    document.getElementById('connInfo').textContent =
      'In use: ' + a.eb200_host + ':' + a.scpi_port + ', local address ' + a.local_ip
      + ' (source: ' + a.source + '). Settings file: ' + d.file;
  }).catch(function() { connMeldung('Connection settings could not be loaded.', 'err'); });
}
document.getElementById('connTestBtn').onclick = function() {
  connMeldung('Testing ...');
  fetch('/api/device/test', {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify(connEingabe())})
    .then(function(r) { return r.json(); })
    .then(function(d) { if (d.ok) connMeldung('Reached: ' + d.idn, 'ok'); else connMeldung(connFehler(d), 'err'); })
    .catch(function() { connMeldung('Test failed: no answer from the waterfall server.', 'err'); });
};
document.getElementById('connSaveBtn').onclick = function() {
  fetch('/api/device', {method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(connEingabe())})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) { connMeldung('Saved. Takes effect after restarting the waterfall server (:8000).', 'ok'); loadConn(); }
      else connMeldung(connFehler(d), 'err');
    })
    .catch(function() { connMeldung('Save failed: no answer from the waterfall server.', 'err'); });
};
loadConn();

function load() {
  fetch('/api/params').then(function(r) { return r.json(); }).then(function(data) {
    const snapBtn = document.getElementById('snapBtn');
    if (data.error) {
      document.getElementById('device').textContent = data.error;
      document.getElementById('app').textContent = '';
      document.getElementById('store').textContent = '';
    } else {
      renderDevice(data.eb200 || {});
      renderApp(data.app || {});
      renderStore(data);
      if (data._snapshot_running) {
        snapBtn.disabled = true;
        const metaDiv = document.getElementById('device-meta');
        if (metaDiv) {
          const d = document.createElement('div');
          d.textContent = 'Inventory running ...';
          metaDiv.appendChild(d);
        }
      } else {
        snapBtn.disabled = false;
      }
    }
    document.getElementById('stand').textContent = '';
  }).catch(function(err) {
    document.getElementById('stand').textContent = 'Failed to load: ' + err;
  });
}

function triggerSnapshot() {
  fetch('/api/params/snapshot', {method: 'POST'}).then(function(r) { return r.json(); })
    .then(function(res) {
      document.getElementById('stand').textContent =
        res.started ? 'Inventory started ...' : 'Inventory already running ...';
      setTimeout(load, 8000);
    });
}

load();
setInterval(load, 30000);
</script>
</body>
</html>
"""

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; Waterfall</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  /* T-1407: Farben nur noch als Tokens (eb200_core/ui_theme.py). Grundregeln
     (body, Knoepfe, Felder, Kopfleiste, Tabellen) kommen aus BASE_CSS; hier
     stehen nur noch die :8000-eigenen Regeln.
     ZWEI SORTEN Flaechen sind zu unterscheiden:
       - Bedienflaechen (Panel, Toolbar, Skala-Leiste) folgen dem Theme;
       - Flaechen AUF dem schwarzen Spektrum (Sweep-Kasten, Markerliste,
         Zoom-Infokasten, Ziehauswahl, BW-Streifen) bleiben in BEIDEN Themes
         dunkel - dafuer die Tokens --canvas-overlay-*. */
  #meta { font-size:0.85em; color:var(--fg-muted); margin-bottom:8px; }
  canvas { border:1px solid var(--canvas-border); cursor:crosshair; }
  .known-wrap { overflow:hidden; } /* T-505f: schneidet leere Katalog-Reihen ab */
  /* IFPAN-Zoom laesst sich per Ziehen schwenken (SDR#/GQRX-Gefuehl) -
     grab/grabbing statt Fadenkreuz; waehrend des Schwenkens setzt
     attachSelection den Cursor direkt auf 'grabbing' (siehe dort). */
  #spec-z, #wf-z { cursor: grab; }
  #controls { margin:10px 0; font-size:0.85em; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  /* Inhaltsbreite = Breite des Wasserfall-Blocks (Zeit-Gutter + Canvas-Spalte
     + Farbskala). Dadurch brechen die Bedienleisten IMMER an derselben Stelle
     um und der gesamte Inhalt wird per margin:auto horizontal im Browser-
     fenster zentriert. Rueckfallwerte - beide werden beim Laden aus der
     Fensterbreite ueberschrieben (siehe CANVAS_W / applyCanvasWidth im Script). */
  :root { --content-width: 1040px; --canvas-width: 900px; }
  #headerBar, #toolbar, #overviewView, #zoomView, #scaleBar, #controls {
    max-width: var(--content-width);
    margin-left: auto;
    margin-right: auto;
  }
  #controls label { display:flex; gap:8px; align-items:center; }
  /* Breitere Farbskalen-Regler fuer feinere Einstellung */
  #scaleMin, #scaleMax { width: 320px; }
  input[type=number] { width:60px; }
  button.flash { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  /* Einheitliche Aufnahme-Toggle-Knoepfe (Session/Audio/Spektrum): Ruhezustand
     wie die uebrigen Knoepfe, NUR der Record-Punkt (.rec-dot) ist rot. Laufend
     (.active) = gefuellt rot, weisse Schrift, weisser Stop-Glyph. Die
     verstrichene Aufnahmezeit steht in .rec-time (siehe renderRecBtn). */
  button.rec-btn.active { background:var(--danger); border-color:var(--danger); color:#fff; }
  button.rec-btn.active:hover { background:var(--danger); filter:brightness(0.92); }
  button.rec-btn .rec-dot { color:var(--danger); }       /* roter Record-Punkt (Ruhe) */
  button.rec-btn.active .rec-dot { color:#fff; }         /* Stop-Glyph weiss auf Rot */
  button.rec-btn .rec-time { font-variant-numeric: tabular-nums; opacity:0.85; }
  /* Mithoeren-Toggle: kein Aufnahme-Knopf, deshalb eigene Farbe - Ruhe wie
     die Standard-Knoepfe, aktiv gruen gefuellt; .blocked = Browser hat den
     Audiostart noch nicht freigegeben (Autoplay-Policy), Klick loest ihn aus. */
  /* T-1409: eigenes, in beiden Themes gleiches Gruen - das helle --success des
     Dark Themes war als Flaeche zu grell. Dunkle Schrift auf mittlerem Gruen. */
  button.mon-btn.active { background:#2f9e44; border-color:#2f9e44; color:#06210f; font-weight:600; }
  button.mon-btn.active:hover { background:#2f9e44; filter:brightness(1.08); }
  button.mon-btn.blocked { border-color:var(--warn); color:var(--warn); }
  /* Waehrend der Spektrum-Bin-Aufnahme werden die geometrieveraendernden
     Bedienelemente gesperrt; der Zoom-Canvas wird per pointer-events
     stillgelegt (kappt Drag/Klick/Tooltip zugleich, sauber reversibel). */
  select:disabled, input:disabled { opacity:0.4; cursor:not-allowed; }
  #spec-z.rec-lock, #wf-z.rec-lock,
  #spec-o.rec-lock, #wf-o.rec-lock { pointer-events:none; cursor:not-allowed; }
  /* Long-Run-Konfigurationsmaske (WS3 11.4/11.5), zunaechst versteckt
     (siehe longrunBtn.onclick). */
  #longrunPanel { display:none; margin:10px 0; padding:10px 14px; background:var(--panel);
                   border:1px solid var(--border-strong); border-radius:6px; max-width: var(--content-width); }
  #longrunPanel .row { display:flex; gap:16px; align-items:center; flex-wrap:wrap; margin:12px 0; }
  #longrunPanel label { display:flex; gap:6px; align-items:center; font-size:0.85em; }
  /* T-705: drei semantische Bloecke (Empfaenger / Job-Aufnahme / Speicherung)
     als umrandete Fieldsets. Negativer Seitenrand = Rahmen sitzt ausserhalb
     der Inhaltsspalte, damit die Felder buendig unter der Typ-Zeile stehen.
     .lk = erstes Label einer Zeile mit fester Breite -> Eingabefelder links
     buendig. */
  #longrunPanel fieldset { border:1px solid var(--border); border-radius:4px;
                           margin:10px -13px; padding:2px 12px 6px; }
  #longrunPanel legend { color:var(--fg-muted); font-size:0.75em; letter-spacing:0.06em;
                         padding:0 6px; }
  /* T-1812b: 7,5 em reichten fuer "Dezimierung", nicht fuer "Storage location".
     Englische Beschriftungen sind haeufig laenger - erste Stelle, an der das
     Layout nachgibt statt das Wort. */
  #longrunPanel .lk { display:inline-block; width:9.5em; }
  #lrBudget { font-size:0.85em; }
  /* Status-Banner waehrend eines aktiven Long-Run-Jobs - auffaellig, damit die
     Empfaenger-Sperre nicht uebersehen wird. T-1410: als Karte im Stil der
     uebrigen Flaechen, mit farbiger Kante links und einem Abzeichen statt des
     fruheren Fliesstexts in Warnfarbe; der Stopp-Knopf sitzt rechts. */
  #longrunBanner { display:none; align-items:center; gap:12px; margin:10px 0;
                    padding:8px 14px; background:var(--panel); color:var(--fg);
                    border:1px solid var(--border); border-left:4px solid var(--danger);
                    border-radius:6px; font-size:0.9em; max-width: var(--content-width); }
  #longrunBanner.on { display:flex; }
  #longrunBanner .lrb-badge { flex:0 0 auto; display:inline-flex; align-items:center; gap:6px;
                    background:var(--danger); color:#fff; border-radius:12px;
                    padding:3px 12px; font-weight:600; white-space:nowrap; }
  #lrbText { flex:1 1 auto; }
  #longrunBanner button { flex:0 0 auto; margin-left:0; }
  #tooltip { position:fixed; background:var(--panel); color:var(--fg); border:1px solid var(--border-strong);
             box-shadow:var(--shadow); padding:4px 8px;
             font-size:0.8em; pointer-events:none; display:none; }
  /* Ziehauswahl (Drag) fuer die Frequenzbereichs-Uebergabe an den SDR-Wasserfall
     (sdr_waterfall.py, nur bei aktivem SDR-Toggle) - siehe attachSelection().
     Liegt AUF dem Spektrum, bleibt daher in beiden Themes hell auf dunkel. */
  .canvas-wrap { position:relative; }
  .sel-overlay {
    position:absolute; top:0; bottom:0; left:0; width:0;
    background:rgba(255,255,255,0.18); border-left:1px solid #fff; border-right:1px solid #fff;
    pointer-events:none; display:none;
  }
  /* Frequenzbeschriftung der Ziehauswahl (Start-Stop in MHz) als Leiste am
     unteren Rand des Rahmens; Text kommt aus data-label, das attachSelection
     waehrend des Ziehens setzt. Leer = keine Leiste. */
  .sel-overlay::after {
    content: attr(data-label);
    position:absolute; left:0; right:0; bottom:0;
    background:rgba(40,110,220,0.85); color:#fff; font-size:0.85em;
    text-align:center; padding:3px 0; white-space:nowrap; overflow:hidden;
  }
  .sel-overlay[data-label=""]::after, .sel-overlay:not([data-label])::after { display:none; }
  /* Elevator-Scroll-Historie im D-SCAN-Wasserfall (nur Uebersicht).
     wf-outer-o ordnet Zeit-Gutter (time-o) und die eigentliche Canvas-Spalte
     (canvasWrapO, gestapelt: spec-o / axis-o / known-o / wf-o) nebeneinander
     an. Das Zeit-Gutter bekommt einen oberen Rand in Hoehe ALLES DESSEN, was
     ueber dem Wasserfall liegt, damit es exakt neben wf-o zu liegen kommt. */
  .wf-outer-o { display:flex; align-items:flex-start; }
  /* Der Gutter beginnt auf Hoehe von wf-o, siehe Stapel in canvasWrapO:
     spec-o + axis-o + known-o. Der Wert ist empirisch eingestellt (eine
     Summandenrechnung aus den Canvas-Attributen plus Rahmen geht nicht auf)
     und wandert bei jeder Aenderung der Streifenhoehe um genau dieselbe
     Differenz mit - T-129: Streifen 55 -> 96 px, also 183 -> 224 px.
     Gegenprobe: bei leerem Streifen stellt knownStripResized() 126 px ein. */
  /* T-1409: Der Versatz ueber dem Gutter ist ein INNENABSTAND (frueher margin) -
     so faerbt der Canvas-Hintergrund die Flaeche bis zur Oberkante des Spektrums
     mit ein und die Canvas-Spalte bildet ein geschlossenes Rechteck.
     knownStripResized() rechnet entsprechend mit paddingTop. */
  #time-o { padding-top:224px; margin-top:0; flex-shrink:0; background:var(--canvas-bg); }
  /* wf-scroll ist ein nativer overflow-y-Container, der als reiner
     "Elevator" dient: wf-o selbst bleibt dank position:sticky optisch immer
     an derselben Stelle stehen (wir malen bei jedem Scroll-Event den
     passenden Ausschnitt aus dem Offscreen-Historienpuffer hinein), der
     unsichtbare Spacer darunter erzeugt nur die Scrollbalken-Reichweite.
     Breite/Hoehe = Canvas + 2px, damit der 1px-Rand von wf-o (aus der
     allgemeinen canvas-Regel) exakt hineinpasst und nichts abgeschnitten
     wird - die sichtbare Canvas-Flaeche bleibt CANVAS_W x 300. Die Klick-/
     Drag-/Tooltip-Mathematik bleibt davon unberuehrt: sie rechnet durchweg
     mit getBoundingClientRect().width und Bruchteilen, nie mit 900. */
  /* T-2011a (BUG-0009): der Scrollbalken liegt INNERHALB von .wf-scroll und
     deckte bisher den rechten Rand von wf-o ab, sobald Historie da war. Jetzt
     immer vorhanden (overflow-y:scroll) und die Breite um ihn erweitert;
     --wf-scrollbar misst applyCanvasWidth(). */
  .wf-scroll { position:relative; width:calc(var(--canvas-width, 900px) + 2px + var(--wf-scrollbar, 0px));
               height:calc(var(--wf-height, 300px) + 2px); overflow-y:scroll; overflow-x:hidden; }
  #wf-o { position:sticky; top:0; left:0; display:block; }
  .wf-spacer { width:1px; height:0; }
  /* Aktivitaetszeile im Sweep-Kasten - zeigt den zuletzt FERTIGEN Sweep als
     Text (Punkte/Dauer/Rate) und den LAUFENDEN als Fortschrittsbalken.
     T-1812e1: die feste Breite liegt jetzt auf der ZAHL, rechtsbuendig, nicht
     mehr auf dem ganzen Feld. Zweck ist unveraendert - die Labels duerfen nicht
     springen, wenn sich die Stellenzahl aendert (481 -> 16001), und die Rate
     aendert sich bei jedem Sweep. Vorher war das Feld aber deutlich breiter als
     sein Inhalt, und die Differenz stand als Luecke HINTER dem Label. Jetzt ist
     das Feld so breit wie sein Inhalt, und der Ausgleich liegt als schmaler
     Vorlauf VOR der Zahl. */
  #activity-o { display:flex; align-items:center; gap:10px; font-size:0.85em; margin-bottom:8px; white-space:nowrap; }
  .fld { display:inline-block; text-align:left; white-space:nowrap; }
  .num { font-variant-numeric: tabular-nums; }
  #activity-text .fld + .fld { margin-left:0.4em; }
  #activity-text .num { display:inline-block; text-align:right; }
  #activity-text #act-points { width:3.4em; }
  #activity-text #act-secs { width:2.8em; }
  #activity-text #act-rate { width:4.0em; }
  /* REC-Statusabzeichen der Rohdaten-Session-Aufzeichnung - nur sichtbar,
     solange eine Aufzeichnung laeuft (siehe updateRecBadge). */
  #rec-badge { display:none; font-weight:bold; }
  /* T-1410: dieselbe Information als Zeile im Einstellungs-Panel, direkt unter
     dem Session-Knopf. Ebenfalls nur waehrend einer Aufzeichnung sichtbar. */
  #rec-status { display:none; color:var(--danger); font-weight:600;
                font-variant-numeric:tabular-nums; }
  /* IFPAN-Zoom: Bandbreiten-Streifen ueber dem Wasserfall (zeigt, wie eng die
     gewaehlte BW relativ zum Span liegt) - liegt AUF dem Spektrum. */
  #bw-band-z {
    position:absolute; left:0; width:0; display:none; pointer-events:none;
    background:rgba(255,255,255,0.12);
    border-left:1px solid rgba(255,255,255,0.35);
    border-right:1px solid rgba(255,255,255,0.35);
  }
  /* Mittenfrequenz in der Kopfleiste, nur in der Zoom-Ansicht sichtbar
     (body.zoom wird in startZoom/backBtn gesetzt). */
  /* T-721: doppelte Schriftgroesse und fett. Die Zentrierung uebernimmt ein
     zweites .spacer-Element LINKS der Anzeige (siehe headerBar-Markup) -
     bewusst nicht position:absolute, damit die Anzeige bei schmalem Fenster
     die rechten Bedienelemente nicht ueberlappt. min-width haelt die Mitte
     ruhig, wenn die Stellenzahl vor dem Komma wechselt; text-align:center
     zentriert den Text im reservierten Feld. inline-block statt inline, weil
     min-width auf inline-Elementen wirkungslos ist; flex:0 0 auto verhindert,
     dass der Flex-Container die Ziffernzeile bei Platzmangel staucht. */
  #centerFreqBig { display:none; font-size:2.5em; font-weight:700; color:var(--head-fg); margin:0;
                   font-variant-numeric:tabular-nums; line-height:1.1; flex:0 0 auto;
                   min-width:13ch; text-align:center; white-space:nowrap; }
  body.zoom #centerFreqBig { display:inline-block; }
  /* Kopfleiste in der Zoom-Ansicht flacher (Grundwert padding:10px 20px aus
     BASE_CSS), damit die groessere Ziffernzeile die Gesamthoehe nicht
     unnoetig aufblaeht. */
  body.zoom #headerBar { padding-top:6px; padding-bottom:6px; }
  /* T-722: Direkteingabe der Mittenfrequenz. Das Eingabefeld tritt an die
     Stelle der Anzeige (gleiche Schriftgroesse und Breite, damit die
     Kopfleiste beim Umschalten nicht springt); darunter steht das Echo mit
     dem interpretierten Wert. Die beiden Sichtbarkeitsregeln tragen zwei
     Klassen und schlagen damit die einklassige Regel aus T-721. */
  #freqEdit { display:none; flex:0 0 auto; flex-direction:column; align-items:center; }
  body.zoom.freqedit #freqEdit { display:inline-flex; }
  body.zoom.freqedit #centerFreqBig { display:none; }
  body.zoom #centerFreqBig { cursor:pointer; }
  #freqInput { font-family:inherit; font-size:2.5em; font-weight:700; line-height:1.1;
               text-align:center; font-variant-numeric:tabular-nums; width:13ch;
               padding:0 4px; margin:0; color:var(--head-fg); background:transparent;
               border:none; border-bottom:2px solid var(--head-fg); }
  #freqInput:focus { outline:none; }
  #freqEcho { font-size:0.75em; color:var(--head-muted); white-space:nowrap; }
  /* T-1813d1: eigenes Token statt --danger. Das Echo steht in der Kopfleiste,
     die in BEIDEN Themes dunkel ist - --danger ist fuer helle Flaechen gedacht
     und dort nicht lesbar. */
  #freqEcho.bad { color:var(--head-danger); }
  body.zoom #viewSub { display:none; }
  /* S-Meter im IFPAN-Zoom: max. Pegel im BW-Fenster als Balken. Der Gradient
     liegt auf dem Outer (voller Skalenverlauf gruen->gelb->rot); der Mask-Div
     verdeckt von rechts den NICHT erreichten Teil -> breitenunabhaengig.
     Die Maske traegt die Seitenfarbe, damit der verdeckte Teil verschwindet. */
  #smeter-z { display:flex; align-items:flex-start; gap:12px; font-size:0.85em; margin:6px 0; }
  #smeter-plabel { color:var(--fg-muted); line-height:22px; }
  #smeter-stack { display:inline-block; }
  #smeter-bar-outer { position:relative; width:430px; height:22px; border:1px solid var(--border-strong);
    background:linear-gradient(to right,#2a2,#cc2 60%,#e33 90%); overflow:hidden; }
  #smeter-bar-mask { position:absolute; right:0; top:0; bottom:0; width:100%; background:var(--bg); }
  #smeter-peak-marker { position:absolute; top:0; bottom:0; width:2px; background:var(--fg);
    left:0; transform:translateX(-1px); display:none; }
  #smeter-axis { display:block; width:430px; height:36px; }
  #smeter-axis line { stroke:var(--fg-muted); }
  #smeter-axis line.maj { stroke:var(--fg); }
  #smeter-axis text { fill:var(--fg-muted); font-size:11px; font-family:sans-serif; }
  #smeter-axis text.unit { fill:var(--fg-faint); }
  #smeter-max { color:var(--accent); font-size:1.15em; line-height:22px; font-variant-numeric:tabular-nums;
    cursor:pointer; min-width:9em; } /* Klick setzt den Hoechstwert zurueck */

  /* =====================================================================
     Kopfleiste, Toolbar, seitliches Einstellungs-Panel (Overlay ueber dem
     linken Canvas-Rand) und Skala-Leiste. DOM-erhaltend: alle Element-IDs
     unveraendert. Die Grundregeln von #headerBar liefert BASE_CSS; hier nur
     die :8000-Zusaetze (Mittenfrequenz, Resize-Hinweis, REC-Abzeichen).
     ===================================================================== */
  #headerBar { margin-bottom:8px; }
  /* Panel-Umschalter in der Toolbar: aktiv = Panel offen */
  #panelToggle.active, #navToggle.active, #detectCfgBtn.active {
    background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  /* Navigationsmenue (T-1010): Aufklapp-Knopf "Ansichten" neben "Einstellungen"
     mit Direktlinks zu den anderen Suite-Servern. Host wird im Script aus
     location.hostname gebildet (siehe initNavMenu). */
  #navMenu { position:relative; }
  #navList { position:absolute; top:100%; left:0; z-index:60; margin-top:4px;
             display:grid; grid-template-columns:1fr; gap:6px; padding:8px;
             background:var(--panel-2); border:1px solid var(--border-strong); border-radius:6px;
             box-shadow:var(--shadow); min-width:260px; }
  #navList[hidden] { display:none; }
  #navList a.nav-item { display:flex; flex-direction:column; align-items:flex-start; gap:2px;
             background:var(--btn-bg); color:var(--btn-fg); border:1px solid var(--btn-border);
             border-radius:4px; padding:6px 10px;
             text-decoration:none; cursor:pointer; user-select:none; }
  #navList a.nav-item:hover { background:var(--btn-hover); }
  #navList a.nav-item .l2 { font-size:0.8em; color:var(--fg-muted); }

  /* Toolbar: Aktionen auf die Darstellung. Checkbox-Toggles (Raster/Marker)
     bleiben Checkboxen (Script nutzt .checked/onchange), werden aber als
     Knoepfe mit Aktivzustand gezeichnet (:has()). */
  #toolbar, #toolbarZ { display:flex; align-items:center; gap:8px; flex-wrap:nowrap;
             font-size:0.85em; margin:0 0 8px 0; }
  #toolbar .sep, #toolbarZ .sep { width:1px; height:1.6em; background:var(--border-strong); margin:0 6px; }
  #toolbar label.tbtn, #toolbarZ label.tbtn { display:inline-flex; align-items:center; gap:6px; margin:0;
             background:var(--btn-bg); color:var(--btn-fg); border:1px solid var(--btn-border);
             border-radius:4px; padding:4px 10px;
             cursor:pointer; user-select:none; }
  #toolbar label.tbtn:hover, #toolbarZ label.tbtn:hover { background:var(--btn-hover); }
  #toolbar label.tbtn input, #toolbarZ label.tbtn input { display:none; }
  #toolbar label.tbtn:has(input:checked),
  #toolbarZ label.tbtn:has(input:checked) { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  /* T-707: die vier Toggles (Raster/Marker/Katalog/Regional) gleich hoch und
     gleich breit. Feste Hoehe, weil die Glyphen sonst die Zeilenhoehe je Label
     unterschiedlich treiben; feste Breite + zentriert. */
  #toolbar label.tbtn, #toolbarZ label.tbtn, #toolbar button.tbtn-w { box-sizing:border-box; width:8.6em;
             height:2.1em; padding:0 6px; justify-content:center; line-height:1; }
  #toolbar #helpBtn, #toolbarZ #helpBtnZ { margin-left:auto; }
  /* T-505d: alle Toolbar-Knoepfe gleich hoch wie die Toggles; Aktivzustand fuer
     Schaltflaechen (Markerliste eingeblendet) in der Toggle-Farbe. */
  #toolbar button, #toolbarZ button { box-sizing:border-box; height:2.1em; line-height:1; }
  #toolbar button.active { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  /* T-505e: Auto-Marker-Einstellungen als Popover unter dem Toolbar-Knopf
     (Muster #navMenu/#navList). Jede Zeile ein Grid mit festen Spalten
     (Bezeichner, Regler, Wert, Zusatz), damit alles linksbuendig steht. */
  #detectMenu { position:relative; display:inline-flex; align-items:center; gap:2px; }
  #detectCfgBtn { width:1.8em; padding:0; }
  #detectCfg { position:absolute; top:100%; left:0; z-index:60; margin-top:4px;
               background:var(--panel-2); border:1px solid var(--border-strong); border-radius:6px;
               box-shadow:var(--shadow); padding:8px 10px;
               min-width:460px; text-align:left; white-space:nowrap; }
  #detectCfg[hidden] { display:none; }
  #detectCfg .cfg-row { display:grid; grid-template-columns:7em 170px 6.5em auto;
                        align-items:center; gap:10px; margin:4px 0; }
  #detectCfg .cfg-row .k { color:var(--fg-muted); }
  #detectCfg .cfg-row .v { text-align:left; }
  #detectCfg input[type=range] { width:170px; margin:0; }
  #detectCfg label { display:inline-flex; align-items:center; gap:4px; margin:0; cursor:pointer; }
  #detectCfg .info { color:var(--fg-muted); }
  /* T-2011: Raster-Einstellungen als Popover am Grid-Knopf, Muster #detectMenu. */
  #gridMenu { position:relative; display:inline-flex; align-items:center; gap:2px; }
  #gridCfgBtn { width:1.8em; padding:0; }
  #gridCfg { position:absolute; top:100%; left:0; z-index:60; margin-top:4px;
             background:var(--panel-2); border:1px solid var(--border-strong); border-radius:6px;
             box-shadow:var(--shadow); padding:8px 10px;
             min-width:400px; text-align:left; white-space:nowrap; }
  #gridCfg[hidden] { display:none; }
  #gridCfg .cfg-row { display:grid; grid-template-columns:6.5em 150px 4em auto;
                      align-items:center; gap:10px; margin:4px 0; }
  #gridCfg .cfg-row .k { color:var(--fg-muted); }
  #gridCfg input[type=range] { width:150px; margin:0; }
  #gridCfg label { display:inline-flex; align-items:center; gap:4px; margin:0; cursor:pointer; }

  /* Canvas-Bereich als Bezugsrahmen fuer das Overlay-Panel */
  #overviewStage { position:relative; }

  /* ---------------------------------------------------------------------
     Overlays AUF dem Spektrum: bleiben in beiden Themes dunkel, damit sie
     auf der schwarzen Flaeche lesbar sind (--canvas-overlay-*, T-1407).
     --------------------------------------------------------------------- */
  /* Sweep-Info-Kasten: #meta-o + #activity-o als Overlay oben rechts im
     Spektrum (canvasWrapO ist position:relative). pointer-events:none, damit
     Klick/Drag/Tooltip auf spec-o unveraendert durchgehen. */
  #sweepBox { position:absolute; top:6px; right:6px; z-index:10; pointer-events:none;
              background:var(--canvas-overlay-bg); color:var(--canvas-overlay-fg);
              border:1px solid var(--canvas-overlay-border); border-radius:4px;
              padding:6px 10px; font-size:0.8em; line-height:1.5em; max-width:420px; }
  #sweepBox #meta-o { margin:0; }
  #sweepBox #activity-o { margin:0; gap:8px; color:var(--canvas-overlay-fg); }
  /* T-505b: Ereignisliste der Auto-Marker - Overlay unterhalb des Sweep-Kastens */
  #detectList { position:absolute; top:72px; right:6px; z-index:10; display:none;
                background:var(--canvas-overlay-bg); color:var(--canvas-overlay-fg);
                border:1px solid var(--canvas-overlay-border); border-radius:4px;
                padding:6px 8px; font-size:0.8em; max-height:60%; width:300px;
                overflow:auto; }
  /* T-1409: Kopfzeile als durchgehende Leiste ueber die volle Breite (Rand des
     Overlays per negativem Aussenabstand ueberdeckt), klebt beim Scrollen oben. */
  #detectList .head { display:flex; justify-content:space-between; align-items:center;
                       gap:6px; margin:-6px -8px 6px; padding:6px 8px;
                       background:var(--canvas-overlay-border);
                       border-radius:3px 3px 0 0; position:sticky; top:-6px; z-index:1; }
  #detectList .head #detectListTitle { font-weight:600; }
  #detectList table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
  #detectList th, #detectList td { padding:1px 6px; text-align:right; white-space:nowrap;
                                   border-bottom:none; color:var(--canvas-overlay-fg); }
  #detectList th { font-weight:normal; border-bottom:1px solid var(--canvas-overlay-border); }
  #detectList td.f { color:#ffd400; }   /* Frequenzspalte = Farbe der Auto-Marker */
  #detectList button { padding:2px 8px; height:auto; }
  #detectList button:disabled { opacity:0.45; cursor:default; }

  /* ---------------------------------------------------------------------
     Zoom-Ansicht im gleichen Raster wie die Uebersicht.
     --------------------------------------------------------------------- */
  #zoomStage { position:relative; }
  /* Seitliches Panel: schwebt ueber dem linken Rand des Wasserfall-Blocks -
     Bedienflaeche, folgt daher dem Theme (nicht --canvas-overlay-*). Offen
     waehrend des Einstellens, klappt nach "Apply" automatisch zu
     (siehe Script setPanelOpen). Zoom-Panel erbt ueber dieselbe Klasse. */
  .side-panel { position:absolute; left:0; top:0; z-index:20; width:330px;
                background:var(--overlay-bg); color:var(--fg);
                border:1px solid var(--border-strong); border-radius:6px;
                padding:10px 14px 12px 14px; font-size:0.85em;
                box-shadow:var(--shadow); }
  .side-panel.collapsed { display:none; }
  .side-panel h3 { font-size:0.8em; font-weight:600; letter-spacing:0.06em;
                   color:var(--fg-muted); margin:18px 0 6px 0; }
  .side-panel h3:first-child { margin-top:0; }
  .side-panel .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:4px 0; }
  .side-panel label { display:flex; gap:6px; align-items:center; }
  .side-panel input[type=number] { width:70px; }
  .side-panel .info { color:var(--fg-muted); }
  .side-panel .panel-head { display:flex; align-items:center; justify-content:space-between; }
  .side-panel .panel-head button { padding:0 8px; line-height:1.4em; }
  /* T-706: Trennlinie vor dem Block "Aufnahme" (Zoom-Panel) */
  .side-panel h3.block-sep { border-top:1px solid var(--border); padding-top:12px; margin-top:20px; }
  /* T-706: Aufnahme-Knoepfe alle gleich breit (halbe Zeile, gap 10px aus .row) */
  .side-panel .rec-row button { flex:0 0 calc(50% - 5px); box-sizing:border-box;
                                min-width:0; white-space:nowrap; overflow:hidden;
                                text-overflow:ellipsis; }
  /* T-706: Hinweis bei unzulaessiger Kombination Modulation/ZF-Bandbreite */
  .side-panel .bw-hint { color:var(--warn); font-size:0.9em; margin:2px 0 0 0; }
  .side-panel .bw-hint[hidden] { display:none; }
  /* T-707: #sidePanel (Uebersicht) in vier umrandete Bloecke, Optik wie die
     Long-Run-Maske. Die Einklapp-Taste sitzt absolut oben rechts, da der erste
     Block keine h3-Kopfzeile mehr hat. */
  .side-panel fieldset { border:1px solid var(--border); border-radius:4px;
                         margin:8px 0; padding:2px 10px 8px; min-width:0; }
  .side-panel fieldset:first-of-type { margin-top:2px; margin-right:34px; }
  .side-panel legend { color:var(--fg-muted); font-size:0.8em; letter-spacing:0.06em;
                       padding:0 6px; }
  .side-panel .panel-head.floating { position:absolute; top:8px; right:12px; }
  /* T-2008: D-SCAN-Einstellungen breiter und je Block zweispaltig (links
     Eingaben, rechts Aktion oder Anzeige); Blockueberschriften groesser und
     in der Akzentfarbe (Light = Schrift der Outline-Knoepfe, Dark hellblau).
     Nur #sidePanel - die Zoom-Tafel #sidePanelZ bleibt unveraendert. */
  /* T-2008a (Entwurf OE3GAS 2026-10-09): die sechs Bloecke als Raster 2 x 3 -
     Scan range | Bandwidth & speed, BW-ZOOM | Gain, Extra zoom with SDR |
     Recording; in dieser Reihenfolge stehen die fieldsets ohnehin im Markup.
     Oben eine Kopfzeile mit der Einklapp-Taste und einer Trennlinie. In den
     Bloecken BW/Speed, BW-ZOOM und Recording untereinander statt nebeneinander.
     Eigenes .collapsed noetig: die ID-Regel mit display:grid schlaegt sonst
     .side-panel.collapsed. */
  #sidePanel { width:540px; display:grid; grid-template-columns:1fr 1fr; gap:12px 14px;
               align-items:stretch; padding-top:46px; }
  /* T-2008b: Vorschau und Dwell-Zeile brechen an der eingefuegten Zeilenmarke um. */
  #sidePanel #bwZoomPreview, #sidePanel #mtimeInfo { white-space:pre-line; }
  #sidePanel.collapsed { display:none; }
  #sidePanel::before { content:''; position:absolute; left:0; right:0; top:38px;
                       border-top:1px solid var(--border); }
  #sidePanel fieldset, #sidePanel fieldset:first-of-type { margin:0; padding:4px 12px 10px; }
  #sidePanel legend { color:var(--accent); font-size:1.05em; font-weight:600;
                      letter-spacing:0.02em; }
  #sidePanel .cols2 { display:grid; grid-template-columns:1fr auto; gap:6px 12px;
                      align-items:center; margin:4px 0; }
  #sidePanel .cols2.lead, #sidePanel .cols2.even { grid-template-columns:1fr; justify-items:start; }
  #sidePanel #dscanBwLabel, #sidePanel #dscanSpeedLabel { width:100%; justify-content:space-between; }
  #sidePanel #dscanBwSelect, #sidePanel #speedSelect { width:7.5em; }
  #sidePanel .cols2.even > button { width:100%; box-sizing:border-box; white-space:nowrap; }
  #sidePanel .cols2 > .col { display:flex; flex-direction:column; gap:4px; min-width:0; }
  #sidePanel .cols2 > .col > label { justify-content:space-between; }
  #sidePanel .cols2 > .stretch { align-self:stretch; }
  /* (d): S-Meter fest in der Zoom-Aktionsleiste, rechts neben "Pause".
     Maske/Peak-Marker rechnen in Prozent, die Achse ist ein SVG mit viewBox -
     die schmalere Breite skaliert deshalb ohne Script-Eingriff. */
  #toolbarZ #smeter-z { margin:0 0 0 8px; gap:8px; }
  #toolbarZ #smeter-bar-outer, #toolbarZ #smeter-axis { width:260px; }
  #toolbarZ #smeter-plabel { line-height:22px; }
  #toolbarZ #smeter-max { min-width:8.5em; }
  #sidePanelZ #spanSlider { width:200px; }
  /* Zoom: Canvas-Stapel + Farbskala nebeneinander (kein Zeit-Gutter) */
  .wf-outer-z { display:flex; align-items:flex-start; }
  #sweepBoxZ { position:absolute; top:6px; right:6px; z-index:10; pointer-events:none;
               background:var(--canvas-overlay-bg); color:var(--canvas-overlay-fg);
               border:1px solid var(--canvas-overlay-border); border-radius:4px;
               padding:6px 10px; font-size:0.8em; line-height:1.5em; max-width:420px; }
  #sweepBoxZ #meta-z { margin:0; }

  /* Hinweis nach Fenstergroessen-Aenderung (siehe resize-Handler im Script)
     und Aufnahme-Indikator - beide sitzen in der dunklen Kopfleiste und
     tragen deshalb feste, kopfleistentaugliche Farben. */
  #resizeHint { display:none; align-items:center; gap:8px; font-size:0.8em;
                color:#fd8; background:rgba(255,255,255,0.10); border:1px solid #fd8;
                border-radius:4px; padding:3px 10px; }
  #headerBar #rec-badge { color:#fff; background:#a22; border:1px solid #f55;
                          border-radius:12px; padding:3px 12px; font-size:0.85em;
                          font-weight:normal; font-variant-numeric:tabular-nums; }

  /* Farbskala rechts neben dem Wasserfall: eigener Canvas (Balken + Ticks),
     buendig mit wf-o (gleicher oberer Versatz wie der Zeit-Gutter #time-o).
     Beschriftung folgt dem Theme - siehe drawColorbar (T-1407a). */
  .colorbar { margin-top:183px; margin-left:6px; flex-shrink:0;
              border:none; background:transparent; cursor:default; }

  /* Long-Run-Maske als modaler Dialog: Sichtbarkeit weiterhin per
     style.display aus dem Script; Position/Abdunklung rein CSS. Der
     100vmax-Schatten erzeugt die Hintergrund-Abdunklung ohne Extra-DOM. */
  #longrunPanel { position:fixed; left:50%; top:50%; transform:translate(-50%,-50%);
                  z-index:50; width:min(860px, 92vw); margin:0;
                  box-shadow:0 0 0 100vmax rgba(0,0,0,0.45), 0 12px 48px rgba(0,0,0,0.35);
                  /* openLongrunPanel() haengt das Panel unter <body> (eigener
                     Top-Level-Stacking-Context, sonst deckelt der Sweep-Kasten
                     mit z-index:10 das z-index:50 hier). pointer-events:auto
                     bleibt als Absicherung, falls der Knoten je wieder in
                     einen pointer-events:none-Container wandert. */
                  pointer-events:auto; }
  #longrunPanel::before { content:"Create long-run job"; display:block; color:var(--fg);
                          font-size:1.15em; font-weight:600; letter-spacing:0.04em;
                          margin-bottom:8px; }

  /* Skala-Leiste unten (Inhalt = bisheriges #controls) */
  #scaleBar { margin-top:10px; border-top:1px solid var(--border); padding-top:8px; }
  #scaleBar .bar-row { display:flex; align-items:center; gap:16px; flex-wrap:wrap; font-size:0.85em; }
  #controls { margin:0; }
</style>
</head>
<body>
<!-- GUI-Redesign (a): Kopfzeile statt h1. Der Status-Link behaelt Ziel und
     Tab-Name; panelToggle oeffnet/schliesst das seitliche Einstellungs-Panel. -->
<div id="headerBar">
  <span class="brand">MoniScope</span>
  <span class="sep"></span>
  <span class="view-name" id="viewName">D-SCAN</span>
  <span class="view-sub" id="viewSub">Wideband scan</span>
  <!-- T-721: Spacer-Paar links und rechts der Frequenzanzeige. Der rechte
       Spacer steht bereits vor dem Status-Link; beide teilen sich den freien
       Raum, dadurch sitzt die Anzeige mittig. In der D-SCAN-Ansicht ist
       centerFreqBig ausgeblendet - die beiden Spacer wirken dann wie einer,
       das Layout der Uebersicht bleibt unveraendert. -->
  <span class="spacer"></span>
  <div id="centerFreqBig" title="Center frequency in MHz. The digits after the decimal point are shown in groups of three: first group = kHz part, second group = Hz part (3.626 075 MHz equals 3626075 Hz). Click or press f to enter a frequency directly. Fine tuning: mouse wheel = 1 Hz, +Shift = 10 Hz, +Ctrl = 100 Hz, +Shift+Ctrl = 1 kHz"></div>
  <!-- T-722: Eingabefeld an derselben Flex-Position wie die Anzeige. Es ist
       dauerhaft im DOM und wird nur ueber body.freqedit ein- und
       ausgeblendet - so bleiben Listener und Feldwert erhalten. -->
  <div id="freqEdit">
    <input id="freqInput" type="text" inputmode="decimal" autocomplete="off" spellcheck="false" title="Enter a center frequency. Without a unit, MHz applies; the suffixes Hz, kHz, MHz, GHz and the short forms k, M, G are also allowed. Enter confirms, Esc cancels.">
    <span id="freqEcho"></span>
  </div>
  <span class="spacer"></span>
  <a href="/status" target="eb200_status" class="status-link" title="Parameter memory: device inventory and runtime parameters">&#9679; EB200 Status</a>
  <button id="themeToggle" type="button" onclick="toggleTheme()">&#9790; Dark</button>
  <span id="resizeHint">Window size changed – display reloads automatically …</span>
  <span id="rec-badge"></span>
</div>

<!-- Gemeinsame Position (nicht innerhalb einer der beiden Ansichten) -
     der Long-Run-Banner ist bei aktivem Job unabhaengig davon sichtbar,
     ob gerade die Uebersicht oder die Zoom-Ansicht angezeigt wird
     (WS3 11.5, Schritt 4c: IFPAN-Jobs laufen in der Zoom-Ansicht). -->
<div id="longrunBanner">
  <span class="lrb-badge"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 2.4A5.6 5.6 0 108 13.6 5.6 5.6 0 008 2.4z"/></svg> Long Run</span>
  <span id="lrbText"></span>
  <button id="lrbStop" class="danger">&#9632; Stop</button>
</div>

<!-- Gemeinsame Position wie der Banner oben (Schritt 5b, WS3 11.4): das
     Panel wird sowohl aus der Uebersicht (longrunBtn, Typ dscan) als auch
     aus der Zoom-Ansicht (longrunBtnZ, Typ ifpan) geoeffnet - lrType steuert
     sichtbar/umschaltbar, welche Feldzeile (lrRowDscanGeom/lrRowIfpanGeom)
     angezeigt wird, siehe applyLrType(). -->
<div id="longrunPanel">
  <div class="row">
    <label><span class="lk">Type</span>
      <select id="lrType">
        <option value="dscan">D-SCAN</option>
        <option value="ifpan">IFPAN</option>
      </select>
    </label>
    <label>Label <input type="text" id="lrLabel" placeholder="Name (recommended for the job overview)"></label>
  </div>
  <fieldset><legend>Receiver (EB200)</legend>
  <div class="row" id="lrRowDscanGeom">
    <label><span class="lk">Start (MHz)</span><input type="text" id="lrStart"></label>
    <label>Stop (MHz) <input type="text" id="lrStop"></label>
    <label>Speed
      <select id="lrSpeed">
        <option value="LOW">LOW</option>
        <option value="NORM">NORM</option>
        <option value="HIGH">HIGH</option>
        <option value="MTIM">MTIME/CHANNEL</option>
      </select>
    </label>
    <label id="lrMtimeLabel" style="display:none">MTIME (ms, empty = DEFAULT)
      <input type="number" id="lrMtime" min="0.5" max="900000" step="0.5" style="width:6em">
    </label>
  </div>
  <div class="row" id="lrRowIfpanGeom" style="display:none">
    <label><span class="lk">Center (MHz)</span><input type="text" id="lrCenter"></label>
    <label>Span
      <select id="lrSpan">
      </select>
    </label>
    <label>Demodulation
      <select id="lrDemod">
        __IFPAN_MODE_OPTIONS_HTML__
      </select>
    </label>
    <label>IF BW
      <select id="lrBw">
        __IFPAN_BW_OPTIONS_HTML__
      </select>
    </label>
  </div>
  </fieldset>
  <fieldset><legend>Job / recording</legend>
  <div class="row">
    <label><span class="lk">Start</span><input type="datetime-local" id="lrStartTime"></label>
    <label>End
      <select id="lrDuration">
        <option value="3600">1 h</option>
        <option value="10800">3 h</option>
        <option value="21600">6 h</option>
        <option value="43200">12 h</option>
        <option value="86400">24 h</option>
        <option value="custom">End time...</option>
      </select>
    </label>
    <label id="lrEndTimeLabel" style="display:none">End time <input type="datetime-local" id="lrEndTime"></label>
  </div>
  <!-- T-704: Zeile 4 = Dezimierung (D-SCAN) bzw. Framerate-Hinweis (IFPAN)
       + Rotation; Zeile 5 = Ablage + Budget. -->
  <div class="row">
    <label id="lrRowDecimation"><span class="lk">Decimation</span>
      <select id="lrDecimation">
        <option value="">none</option>
        <option value="5">5 s</option>
        <option value="15">15 s</option>
        <option value="30">30 s</option>
        <option value="60">60 s</option>
      </select>
      <span id="lrIfpanRateHint" class="info" style="display:none">full frame rate, no decimation</span>
    </label>
    <label>Rotation (min) <input type="number" id="lrRotation" value="60" min="1"></label>
  </div>
  <div class="row" id="lrRowAudio" style="display:none">
    <label><input type="checkbox" id="lrAudio"> Record audio in parallel (WAV)</label>
  </div>
  </fieldset>
  <fieldset><legend>Storage</legend>
  <div class="row">
    <label><span class="lk">Storage location</span><select id="lrStorage"></select></label>
    <span id="lrBudget"></span>
  </div>
  </fieldset>
  <div class="row">
    <button id="lrCreate">Create job</button>
    <button id="lrCancel">Cancel</button>
  </div>
</div>

<div id="overviewView">
  <!-- GUI-Redesign (a): Toolbar = Aktionen auf die Darstellung. Raster und
       Klick-Marker bleiben Checkboxen (Script: .checked/onchange), das
       Aussehen als Toggle-Knopf kommt aus CSS label.tbtn:has(input:checked).
       Der fruehere Hinweistext (#hint) steckt jetzt im title des [?]-Knopfs. -->
  <div id="toolbar">
    <button id="panelToggle" class="active" title="Show/hide settings"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 5.2A2.8 2.8 0 108 10.8 2.8 2.8 0 008 5.2zm6.4 4L13 10.1l.5 1.6-1.3 1.3-1.6-.5-.9 1.4H8l-.9-1.4-1.6.5-1.3-1.3.5-1.6L3 9.2V7.4l1.4-.9-.5-1.6L5.2 3.6l1.6.5.9-1.4h1.8l.9 1.4 1.6-.5 1.3 1.3-.5 1.6 1.4.9z"/></svg> Settings &#9662;</button>
    <div id="navMenu">
      <button id="navToggle" title="Open other MoniScope views (new tab)">&#9776; Views &#9662;</button>
      <div id="navList" hidden>
        <a class="nav-item" data-port="8010" target="eb200_catalog"><span class="l1">Frequency catalog</span><span class="l2">:8010</span></a>
        <a class="nav-item" data-port="8020" target="eb200_sessions"><span class="l1">Session analysis</span><span class="l2">:8020</span></a>
        <a class="nav-item" data-port="8040" target="eb200_spectrum"><span class="l1">Spectrum analysis</span><span class="l2">:8040</span></a>
        <a class="nav-item" data-port="8050" target="eb200_jobs"><span class="l1">Recordings</span><span class="l2">:8050</span></a>
      </div>
    </div>
    <span class="sep"></span>
    <button id="jumpLiveBtn" title="Pin the view back to the live edge (newest row)"><svg class="glyph" viewBox="0 0 16 16"><path d="M2 2h2v12H2zM14 2 6 8l8 6z"/></svg> Jump to live</button>
    <button id="pauseBtn"><svg class="glyph" viewBox="0 0 16 16"><path d="M3 2h4v12H3zM9 2h4v12H9z"/></svg> Pause</button>
    <button id="exportJpgBtn"><svg class="glyph" viewBox="0 0 16 16"><path d="M1 3h14v10H1zm1.6 8.4h10.8L10 7.2l-2.4 3-1.6-2z"/></svg> Save as JPEG</button>
    <button id="clearWfBtn" title="Clear the waterfall history and restart with the current parameter header"><svg class="glyph" viewBox="0 0 16 16"><path d="M6 1h4l.6 1H14v2H2V2h3.4zM3.5 5h9l-.7 10H4.2z"/></svg> Clear</button>
    <span class="sep"></span>
    <!-- T-2011: Grid mit Einstellungs-Popover (Muster detectMenu). -->
    <div id="gridMenu"><label class="tbtn" title="Show/hide the grid in the current waterfall segment; older segments keep their setting"><input type="checkbox" id="gridToggle"> &#8862; Grid</label><button id="gridCfgBtn" title="Grid settings">&#9662;</button>
      <div id="gridCfg" hidden>
        <div class="cfg-row"><span class="k">Opacity</span><input type="range" id="gridAlpha" min="10" max="100" step="5" value="60"><span class="v"><span id="gridAlphaLabel">60</span> %</span><span></span></div>
        <div class="cfg-row"><span class="k">Color</span><select id="gridColor"><option value="white">White</option><option value="black">Black</option><option value="cyan">Cyan</option><option value="magenta">Magenta</option></select><span class="v"></span><label><input type="checkbox" id="gridDash"> dashed</label></div>
        <div class="cfg-row"><span class="k">Time lines</span><select id="gridTimeStep"><option value="auto">Auto</option><option value="10">10 s</option><option value="30">30 s</option><option value="60">1 min</option><option value="300">5 min</option><option value="off">Off</option></select><span class="v">Label</span><select id="gridTimeLabel"><option value="left">Left</option><option value="center">Center</option><option value="right">Right</option></select></div>
      </div>
    </div>
    <label class="tbtn" title="Place click markers"><input type="checkbox" id="markToggle" checked> Click marker</label>
    <div id="detectMenu"><label class="tbtn" title="Auto marker: the server places one marker per frequency as soon as the level exceeds the estimated noise floor by delta dB"><input type="checkbox" id="autoMarkToggle" checked> Auto marker</label><button id="detectCfgBtn" title="Auto marker settings">&#9662;</button>
      <!-- T-505e: Einstellungen des Auto-Markers (IDs unveraendert aus dem frueheren Seitenpanel-Block) -->
      <div id="detectCfg" hidden>
        <div class="cfg-row"><span class="k">&Delta; threshold</span><input type="range" id="detectDelta" min="3" max="30" step="1" value="15"><span class="v"><span id="detectDeltaLabel">15</span> dB</span><label title="Derive delta from the noise spread of the first sweeps; moving the slider switches to manual"><input type="checkbox" id="detectAutoToggle" checked> automatic</label></div>
        <div class="cfg-row"><span class="k">Min. duration</span><input type="range" id="detectMin" min="1" max="10" step="1" value="3"><span class="v"><span id="detectMinLabel">3</span> sweeps</span><span id="detectMinSecs" class="info"></span></div>
        <div class="cfg-row"><span class="k"></span><label><input type="checkbox" id="detectChipToggle" checked> Marker with frequency</label><span class="v"></span><span id="detectCount" class="info">0 markers</span></div>
        <div class="cfg-row"><span class="k">Label spacing</span><input type="range" id="detectGap" min="0" max="20" step="1" value="4"><span class="v"><span id="detectGapLabel">4</span> px</span><span class="info">between marker labels</span></div>
      </div>
    </div>
    <button id="detectListBtn" class="tbtn-w" title="Show/hide marker list">Marker list</button>
    <button id="detectRestartBtn" class="tbtn-w" title="Restart auto marker">Restart</button>
    <span class="sep"></span>
    <label class="tbtn" title="Show the catalog of known bands and channels (known_freq/)"><input type="checkbox" id="knownToggle" checked> Catalog<span id="knownCount"></span></label>
    <label class="tbtn" title="Show only stations of your own region (station.json)"><input type="checkbox" id="knownRegionToggle" checked> Regional</label>
    <button id="helpBtn" title="Clicking zooms into the clicked frequency via IFPAN. With the SDR toggle active, dragging (hold the mouse button and move) selects a frequency range and opens the SDR waterfall on it; without the SDR toggle, dragging has no effect.">?</button>
  </div>

  <div id="overviewStage">
  <!-- Seitliches Einstellungs-Panel (Overlay ueber dem linken Canvas-Rand).
       Alle Elemente sind die bisherigen (IDs unveraendert), nur neu gruppiert;
       T-2008: zweispaltige Bloecke, eigener Block BW-ZOOM mit Fenster-Vorschau.
       Klappt nach "Apply" automatisch zu (Script setPanelOpen). -->
  <div id="sidePanel" class="side-panel">
    <!-- T-707: vier umrandete Bloecke (fieldset/legend), Einklapp-Taste
         schwebt oben rechts (CSS .panel-head.floating). -->
    <div class="panel-head floating">
      <button id="panelClose" title="Collapse panel">&#8722;</button>
    </div>
    <fieldset><legend>Scan range</legend>
    <!-- T-2008: links Start/Stop untereinander, rechts "Apply" ueber beide Zeilen. -->
    <div class="cols2">
      <div class="col">
        <label>Start (MHz) <input type="number" id="dscanStart" value="88" step="0.1"></label>
        <label>Stop (MHz) <input type="number" id="dscanStop" value="100" step="0.1"></label>
      </div>
      <button id="applyRangeBtn" class="stretch">Apply</button>
    </div>

    </fieldset>
    <fieldset><legend>Bandwidth &amp; speed</legend>
    <div class="row">
      <label id="dscanBwLabel" title="IF bandwidth = channel spacing of the D-SCAN; larger = faster, coarser sweep">BW
        <select id="dscanBwSelect">
          __DSCAN_BW_OPTIONS_HTML__
        </select>
      </label>
      <label id="dscanSpeedLabel" title="D-SCAN speed (Annex C: [SENSe:]FREQuency:DSCan:SPEed)">Speed
        <select id="speedSelect">
          <option value="LOW">LOW</option>
          <option value="NORM" selected>NORMAL</option>
          <option value="HIGH">HIGH</option>
          <option value="MTIM">MTIME</option>
        </select>
      </label>
    </div>
    <div class="row">
      <label id="mtimeLabel" style="display:none" title="Measurement time per channel (only effective with speed MTIM, manual 3.18.3.1); sweep duration = number of channels x MTIME + tuning time">MTIME (ms)
        <input type="number" id="mtimeInput" value="100" min="0.5" max="900000" step="0.5" style="width:6em">
      </label>
    </div>
    <div class="row info">
      <span id="mtimeInfo"></span>
      <span id="sweepEstimate"></span>
      <span id="sweepProgress" style="display:none;"></span>
    </div>

    </fieldset>
    <!-- T-2008: eigener Block BW-ZOOM - links der Schalter, rechts die
         Vorschau des Zoom-Fensters (updateBwZoomPreview im Script). -->
    <fieldset><legend>BW-ZOOM</legend>
    <div class="cols2 lead">
      <button id="bwZoomBtn" title="Switches the narrow span around the marker on or off; the BW selection above is unaffected">Enable BW-ZOOM</button>
      <span id="bwZoomPreview" class="info" title="Estimate: 95 x BW around the middle of the scan range. The info box in the spectrum shows the values reported by the receiver."></span>
    </div>
    </fieldset>
    <!-- T-710: Verstaerkungsregelung auch in der D-SCAN-Uebersicht. Gleiche
         Bedienung wie im IFPAN-Zoom (Annex C GCON:MODE/GCON:MGC), dieselben
         Grenzen -30..110 dBuV; MGC haelt die Pegel eines Sweeps und
         aufeinanderfolgender Sweeps vergleichbar. -->
    <fieldset><legend>Gain</legend>
    <div class="row">
      <label title="MGC freezes the gain at the set level - only then are the levels within one sweep and across consecutive sweeps comparable.">
        Control
        <select id="dscanGainMode">
          <option value="AGC">AGC</option>
          <option value="MGC">MGC</option>
        </select>
      </label>
    </div>
    <div class="row">
      <label><span id="dscanGainMgcLabel">50 dBµV</span>
        <input type="range" id="dscanGainMgc" min="-30" max="110" step="1" value="50" disabled>
      </label>
    </div>

    </fieldset>
    <fieldset><legend>Extra zoom with SDR</legend>
    <div class="row">
      <label title="Use an SDR for extra zoom on the center frequency of a range dragged in the overview."><input type="checkbox" id="sdrToggle"> Select SDR</label>
      <select id="sdrDeviceSelect" style="display:none"></select>
    </div>
    <div id="sdrError" class="err" style="display:none;margin-bottom:4px"></div>


    </fieldset>
    <fieldset><legend>Recording</legend>
    <div class="cols2 even">
      <!-- Session-Aufnahme als Aufnahmeknopf wie Audio/Spektrum im Zoom
           (CSS .rec-btn: Ruhe = blauer Rahmen + roter Punkt, aktiv = rot).
           Das statische Markup entspricht exakt dem Ruhezustand, den
           paintSessionBtn(false) erzeugt (siehe Script). -->
      <button id="sessionBtn" class="rec-btn" title="Record the running D-SCAN sweeps into a session file for later review in Session analysis."><span class="rec-dot"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 2.4A5.6 5.6 0 108 13.6 5.6 5.6 0 008 2.4z"/></svg></span> Session recording</button>
      <button id="longrunBtn" title="Set up an unattended long-term recording: receiver settings, start and end time, storage location.">Long Run setup</button>
    </div>
    <!-- T-1410: zweite Statusanzeige direkt am Bedienelement. Die Pille in der
         Kopfleiste (#rec-badge) bleibt bestehen - sie ist auch bei
         eingeklapptem Panel sichtbar. Beide speist updateRecBadge(). -->
    <div class="row"><span id="rec-status"></span></div>
    </fieldset>
  </div>

  <div class="wf-outer-o">
    <!-- Zeit-Gutter: eigene schmale Canvas links neben dem Wasserfall,
         zeigt alle 30s eine dünne Linie + HH:MM:SS-Label (Overlay, siehe
         drawTimeOverlay() im Script - NICHT Teil des Pixelpuffers). -->
    <canvas id="time-o" width="64" height="300"></canvas>
    <div class="canvas-wrap" id="canvasWrapO">
      <!-- GUI-Redesign (b): Sweep-Info-Kasten als Overlay oben rechts im
           Spektrum. Inhalt/IDs unveraendert (meta-o, activity-o, Balken);
           rec-badge ist in den Header gewandert. -->
      <!-- T-505b: Ereignisliste der Auto-Marker (Zeit / MHz / dBuV), neueste oben -->
      <div id="detectList">
        <div class="head">
          <span id="detectListTitle">Marker list (0)</span>
          <span>
            <button id="detectCopyBtn" title="Copy table tab-separated to the clipboard" disabled>Copy</button>
            <button id="detectCsvBtn" title="Download table as CSV (semicolon)" disabled>CSV</button>
            <button id="detectCatalogBtn" title="Send the marker list to the frequency catalog (:8010) - selection and duplicate check happen there" disabled>Catalog</button>
          </span>
        </div>
        <table>
          <thead><tr><th>Time</th><th>MHz</th><th>dB&micro;V</th></tr></thead>
          <tbody id="detectListBody"></tbody>
        </table>
      </div>
      <div id="sweepBox">
        <div id="meta-o">waiting for first frame ...</div>
        <div id="activity-o">
          <span id="activity-text">Sweep: <span class="fld"><span id="act-points" class="num">–</span> points in</span><span class="fld"><span id="act-secs" class="num">–</span> s (</span><span class="fld"><span id="act-rate" class="num">–</span> points/s)</span></span>
        </div>
      </div>
      <canvas id="spec-o" width="900" height="120"></canvas>
      <canvas id="axis-o" width="900" height="20"></canvas>
      <!-- Katalog-Streifen: bekannte Baender/Segmente (obere Reihe) und
           Kanaele (untere Reihe) aus known_freq/, Detailstufe nach Pixeldichte
           (siehe drawKnownStrip). -->
      <div id="knownWrapO" class="known-wrap"><!-- T-505f -->
      <canvas id="known-o" width="900" height="96"></canvas>
      </div>
      <!-- wf-scroll ist der native Elevator-Scrollbalken für die lange
           Wasserfall-Historie (siehe CSS .wf-scroll). wf-o selbst bleibt die
           CANVAS_W x 300-Sichtfläche - Klick/Drag/Tooltip hängen wie
           bisher direkt an ihr. -->
      <div class="wf-scroll" id="wfScrollO">
        <canvas id="wf-o" width="900" height="300"></canvas>
        <div class="wf-spacer" id="wfSpacerO"></div>
      </div>
      <div id="overlay-o" class="sel-overlay"></div>
    </div>
    <!-- GUI-Redesign (b): Farbskala (dBuV -> Farbe) rechts neben dem
         Wasserfall, gezeichnet von drawColorbar() mit den jeweils WIRKSAMEN
         Skalengrenzen (Auto oder Regler). 70px = 14px Balken + Ticks/Text. -->
    <canvas id="colorbar-o" class="colorbar" width="70" height="300"></canvas>
  </div>
  </div><!-- /overviewStage -->
</div>

<div id="zoomView" style="display:none">
  <!-- GUI-Redesign (c): Zoom-Toolbar (Aktionen) - Raster/Marker gibt es hier
       nicht; der fruehere Hinweistext (#hint-z) steckt im [?]-Knopf. -->
  <div id="toolbarZ" class="toolbar-like">
    <button id="panelToggleZ" class="active" title="Show/hide settings"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 5.2A2.8 2.8 0 108 10.8 2.8 2.8 0 008 5.2zm6.4 4L13 10.1l.5 1.6-1.3 1.3-1.6-.5-.9 1.4H8l-.9-1.4-1.6.5-1.3-1.3.5-1.6L3 9.2V7.4l1.4-.9-.5-1.6L5.2 3.6l1.6.5.9-1.4h1.8l.9 1.4 1.6-.5 1.3 1.3-.5 1.6 1.4.9z"/></svg> Settings &#9662;</button>
    <span class="sep"></span>
    <button id="backBtn">&larr; Back to overview</button>
    <button id="pauseBtnZ"><svg class="glyph" viewBox="0 0 16 16"><path d="M3 2h4v12H3zM9 2h4v12H9z"/></svg> Pause</button>
    <div id="smeter-z">
      <span id="smeter-plabel">Level</span>
      <div id="smeter-stack">
        <div id="smeter-bar-outer">
          <div id="smeter-bar-mask"></div>
          <div id="smeter-peak-marker"></div>
        </div>
        <svg id="smeter-axis" viewBox="0 0 430 36" xmlns="http://www.w3.org/2000/svg">
          <line class="maj" x1="1" y1="0" x2="1" y2="8"/>
          <line class="maj" x1="71.7" y1="0" x2="71.7" y2="8"/>
          <line class="maj" x1="143.3" y1="0" x2="143.3" y2="8"/>
          <line class="maj" x1="215" y1="0" x2="215" y2="8"/>
          <line class="maj" x1="286.7" y1="0" x2="286.7" y2="8"/>
          <line class="maj" x1="358.3" y1="0" x2="358.3" y2="8"/>
          <line class="maj" x1="429" y1="0" x2="429" y2="8"/>
          <line x1="35.8" y1="0" x2="35.8" y2="4"/>
          <line x1="107.5" y1="0" x2="107.5" y2="4"/>
          <line x1="179.2" y1="0" x2="179.2" y2="4"/>
          <line x1="250.8" y1="0" x2="250.8" y2="4"/>
          <line x1="322.5" y1="0" x2="322.5" y2="4"/>
          <line x1="394.2" y1="0" x2="394.2" y2="4"/>
          <text x="1" y="21" text-anchor="start">-40</text>
          <text x="71.7" y="21" text-anchor="middle">-20</text>
          <text x="143.3" y="21" text-anchor="middle">0</text>
          <text x="215" y="21" text-anchor="middle">20</text>
          <text x="286.7" y="21" text-anchor="middle">40</text>
          <text x="358.3" y="21" text-anchor="middle">60</text>
          <text x="429" y="21" text-anchor="end">80</text>
          <text class="unit" x="215" y="34" text-anchor="middle">dBµV</text>
        </svg>
      </div>
      <span id="smeter-max" title="Click: reset peak value">Max –</span>
    </div>
    <button id="helpBtnZ" title="Dragging pans the center frequency. Clicking jumps to the clicked position.">?</button>
  </div>

  <div id="zoomStage">
  <!-- Zoom-Panel: gleiche Optik wie #sidePanel (Klasse .side-panel), vom
       selben panelToggle im Header bedient (setPanelOpen). -->
  <div id="sidePanelZ" class="side-panel">
    <div class="panel-head">
      <h3>Span</h3>
      <button id="panelCloseZ" title="Collapse panel">&#8722;</button>
    </div>
    <div class="row">
      <label><span id="spanLabel">200 kHz</span>
        <input type="range" id="spanSlider" min="0" max="5" step="1" value="3">
      </label>
    </div>

    <h3>Demodulation</h3>
    <div class="row">
      <label>Demod
        <select id="modeSelect">
          __IFPAN_MODE_OPTIONS_HTML__
        </select>
      </label>
      <label>Bandwidth
        <select id="bwSelect">
          __IFPAN_BW_OPTIONS_HTML__
        </select>
      </label>
    </div>

    <!-- T-706: Hinweis, wenn Modulation und ZF-Bandbreite nicht zusammenpassen
         (siehe checkModeBwCombo im Script) -->
    <div id="bwHint" class="bw-hint" hidden></div>
    <div id="afcHint" class="bw-hint" hidden></div>

    <h3>Squelch</h3>
    <div class="row">
      <label><input type="checkbox" id="squelchOn"> Squelch on</label>
    </div>
    <div class="row">
      <label><span id="squelchLabel">10 dBµV</span>
        <input type="range" id="squelchThr" min="-30" max="110" step="1" value="10">
      </label>
    </div>
    <div class="row">
      <label title="Threshold follows the scale-min slider of the zoom view; everything below scale min is muted.">
        <input type="checkbox" id="squelchFollow"> follows scale min
      </label>
    </div>

    <h3>Gain</h3>
    <div class="row">
      <label title="MGC freezes the gain at the set level and is required for amplitude-accurate IQ recordings.">
        Control
        <select id="gainMode">
          <option value="AGC">AGC</option>
          <option value="MGC">MGC</option>
        </select>
      </label>
    </div>
    <div class="row">
      <label><span id="gainMgcLabel">50 dBµV</span>
        <input type="range" id="gainMgc" min="-30" max="110" step="1" value="50">
      </label>
    </div>

    <!-- T-706: Trennlinie (block-sep), drei Zeilen, alle Knoepfe gleich breit (rec-row) -->
    <h3 class="block-sep">Recording</h3>
    <div class="row rec-row">
      <button id="monitorBtn" class="mon-btn" title="Monitor demodulated audio in the browser (setting is remembered)"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 2 4.6 5H1.6v6h3L8 14zm2.6 2.2a5 5 0 010 7.6l-1-1.2a3.4 3.4 0 000-5.2z"/></svg> Monitor</button>
    </div>
    <div class="row rec-row">
      <button id="recordBtn" class="rec-btn"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 2.4A5.6 5.6 0 108 13.6 5.6 5.6 0 008 2.4z"/></svg> Audio</button>
      <button id="spectrumBtn" class="rec-btn"><svg class="glyph" viewBox="0 0 16 16"><path d="M8 2.4A5.6 5.6 0 108 13.6 5.6 5.6 0 008 2.4z"/></svg> Spectrum</button>
    </div>
    <div class="row rec-row">
      <button id="longrunBtnZ" title="Set up an unattended long-term recording: receiver settings, start and end time, storage location.">Long Run setup</button>
    </div>
  </div><!-- /sidePanelZ -->

  <div class="wf-outer-z">
  <div class="canvas-wrap" id="canvasWrapZ">
    <!-- (c): IFPAN-Infozeile als Overlay oben rechts im Spektrum (wie sweepBox) -->
    <div id="sweepBoxZ">
      <div id="meta-z">waiting for first frame ...</div>
    </div>
    <canvas id="spec-z" width="900" height="120"></canvas>
    <canvas id="axis-z" width="900" height="20"></canvas>
    <canvas id="known-z" width="900" height="96"></canvas>
    <canvas id="wf-z" width="900" height="300"></canvas>
    <!-- zeigt die eingestellte IF-Bandbreite als hellen, mittig um center_hz
         zentrierten Streifen über wf-z (siehe updateBwBandZ) -
         pointer-events:none, damit Klick/Drag auf wf-z unverändert bleiben. -->
    <div id="bw-band-z"></div>
    <div id="overlay-z" class="sel-overlay"></div>
  </div>
  <canvas id="colorbar-z" class="colorbar" width="70" height="300"></canvas>
  </div><!-- /wf-outer-z -->
  </div><!-- /zoomStage -->
</div>

<!-- GUI-Redesign (a): Farbskalen-Regler als einklappbare Leiste am unteren
     Rand. #controls bleibt als Container erhalten (IDs unveraendert). -->
<div id="scaleBar">
  <div class="bar-row">
    <div id="controls">
      <label>Scale min: <span id="scaleMinLabel">-30 dBµV</span>
        <input type="range" id="scaleMin" min="-40" max="120" step="1" value="-30">
      </label>
      <label>Scale max: <span id="scaleMaxLabel">60 dBµV</span>
        <input type="range" id="scaleMax" min="-40" max="120" step="1" value="60">
      </label>
      <label><input type="checkbox" id="autoScale" checked> Auto</label>
    </div>
  </div>
</div>
<div id="tooltip"></div>

<script>
const el = id => document.getElementById(id);

// T-1407b: Bedienglyphen als Inline-SVG (fill:currentColor ueber .glyph aus
// BASE_CSS). Unicode-Zeichen wie U+23F8 (Pause), U+25B6 (Wiedergabe),
// U+1F5BC (Bild), U+1F5D1 (Papierkorb), U+1F50A (Lautsprecher), U+2699
// (Zahnrad), U+23FA/U+23F9 (Record/Stop) rendert Windows als farbige Emoji.
// Frueh deklariert, weil paintSessionBtn/renderRecBtn schon beim Laden zeichnen.
const SVG_PAUSE = '<svg class="glyph" viewBox="0 0 16 16"><path d="M3 2h4v12H3zM9 2h4v12H9z"/></svg>';
const SVG_PLAY  = '<svg class="glyph" viewBox="0 0 16 16"><path d="M3 2l11 6-11 6z"/></svg>';
const SVG_SPK   = '<svg class="glyph" viewBox="0 0 16 16"><path d="M8 2 4.6 5H1.6v6h3L8 14zm2.6 2.2a5 5 0 010 7.6l-1-1.2a3.4 3.4 0 000-5.2z"/></svg>';
const SVG_REC   = '<svg class="glyph" viewBox="0 0 16 16"><path d="M8 2.4A5.6 5.6 0 108 13.6 5.6 5.6 0 008 2.4z"/></svg>';
const SVG_STOP  = '<svg class="glyph" viewBox="0 0 16 16"><path d="M3 3h10v10H3z"/></svg>';

// ---------------------------------------------------------------------------
// GUI-Redesign (a): seitliches Einstellungs-Panel und Skala-Leiste ein-/
// ausklappen. Reine Sichtbarkeitslogik - keine Aenderung an Geraetezustand.
// Das Panel schliesst sich nach "Apply" von selbst, damit es
// Klick/Drag auf dem darunterliegenden Canvas nicht dauerhaft verdeckt.
// ---------------------------------------------------------------------------
// (c): EIN Offen/Zu-Zustand fuer beide Panels (Uebersicht + Zoom); der
// Ansichtswechsel (startZoom/backBtn) laesst ihn unangetastet.
function setPanelOpen(open) {
  el('sidePanel').classList.toggle('collapsed', !open);
  el('sidePanelZ').classList.toggle('collapsed', !open);
  el('panelToggle').classList.toggle('active', open);
  el('panelToggleZ').classList.toggle('active', open);
}
// (d): je ein Umschalter pro Aktionsleiste, gemeinsamer Zustand.
const togglePanels = () => setPanelOpen(el('sidePanel').classList.contains('collapsed'));
el('panelToggle').onclick = togglePanels;
el('panelToggleZ').onclick = togglePanels;
el('panelClose').onclick = () => setPanelOpen(false);
el('panelCloseZ').onclick = () => setPanelOpen(false);
el('applyRangeBtn').addEventListener('click', () => setPanelOpen(false));

// ---------------------------------------------------------------------------
// Navigationsmenue "Ansichten" (T-1010): Direktlinks zu :8010/:8020/:8040/:8050.
// :8060/:8070 bewusst NICHT im Menue - reine Deep-Link-Server
// (T-1107-Revision 2026-08-29).
// Ziel-URLs entstehen zur Laufzeit aus location.hostname + data-port (wie bei
// openSdrWaterfall), damit die Seite auch remote/Pi ohne festen Host laeuft.
// Reine Sichtbarkeitslogik; Klick ausserhalb oder Escape schliesst das Menue.
// ---------------------------------------------------------------------------
(function initNavMenu() {
  const menu = el('navMenu'), toggle = el('navToggle'), list = el('navList');
  list.querySelectorAll('a.nav-item[data-port]').forEach(a => {
    a.href = `http://${location.hostname}:${a.dataset.port}/`;
  });
  const setNavOpen = open => { list.hidden = !open; toggle.classList.toggle('active', open); };
  toggle.onclick = ev => { ev.stopPropagation(); setNavOpen(list.hidden); };
  list.addEventListener('click', () => setNavOpen(false));
  document.addEventListener('click', ev => { if (!menu.contains(ev.target)) setNavOpen(false); });
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape') setNavOpen(false); });
})();
// ---------------------------------------------------------------------------
// Anzeigebreite (CANVAS_W)
//
// Bis 08/2026 waren alle Canvas fest 900 px breit. Auf grossen Schirmen
// verschenkt das Frequenzaufloesung: ein D-SCAN mit 30 000 Punkten legt dann
// 33 Punkte auf eine Spalte, schmale Traeger verschwinden im Max-Hold. Die
// Breite wird deshalb EINMAL beim Laden aus der Fensterbreite bestimmt.
//
// Bewusst nicht dynamisch: der Wasserfall ist ein gebrannter Pixelpuffer
// (Achsstreifen und Parameter-Tafeln sind hineingemalt). Eine Aenderung im
// Betrieb muesste die Historie skalieren (unscharf, gebrannte Schrift
// unlesbar) oder verwerfen. Wer das Fenster aendert, laedt neu - die
// Historie ist rein clientseitig und beginnt dabei ohnehin von vorn.
//
// window.innerWidth liefert CSS-Pixel, nicht Geraetepixel. Auf einem 4K-Schirm
// mit 150 % Skalierung sind das rund 2560 statt 3840 - genau richtig, denn ein
// Canvas-Pixel soll einem CSS-Pixel entsprechen (sonst waere die Zuordnung
// Sweep-Punkt -> Spalte nicht mehr ehrlich).
// ---------------------------------------------------------------------------
const CANVAS_W_MIN = 900;    // bisheriger Wert, zugleich Untergrenze
const CANVAS_W_MAX = 3200;   // Speicherbremse: 3200 x 6000 x 4 B = 77 MB Puffer
const TIME_GUTTER_W = 64;    // Breite von #time-o
const COLORBAR_W = 70;       // Breite von canvas.colorbar
const CANVAS_W = (() => {
  // T-2011a: 46 = Raender, Seiten-Scrollbalken und Scrollbalken des Elevators
  const roh = window.innerWidth - TIME_GUTTER_W - COLORBAR_W - 46;
  const quantisiert = Math.floor(roh / 4) * 4;   // Vielfache von 4 - ruhigere Blit-Geometrie
  return Math.max(CANVAS_W_MIN, Math.min(CANVAS_W_MAX, quantisiert));
})();
const START_INNER_WIDTH = window.innerWidth;

// Hoehe analog zur Breite: EINMAL beim Laden aus der Fensterhoehe. Gemessen
// wird die Oberkante von wf-o (darueber liegen Kopf, Toolbar, Spektrum, Achse,
// Katalog-Streifen); unten bleibt Platz fuer Skala-Leiste und Controls. Wie
// bei der Breite bewusst nicht dynamisch - Neuladen bei Aenderung (der
// resize-Hinweis beruecksichtigt jetzt auch die Hoehe). wf-z bekommt dieselbe
// Hoehe; der Stapel darueber ist im Zoom praktisch gleich hoch.
const VIEWPORT_ROWS_MIN = 300;    // bisheriger Wert, zugleich Untergrenze
const VIEWPORT_ROWS_MAX = 1500;   // Obergrenze (HISTORY_ROWS = 6000 bleibt)
const BOTTOM_RESERVE_PX = 110;    // Skala-Leiste + Controls + Raender unter wf-o
const VIEWPORT_ROWS = (() => {
  const top = el('wf-o').getBoundingClientRect().top + window.scrollY;
  const roh = Math.floor(window.innerHeight - top - BOTTOM_RESERVE_PX);
  return Math.max(VIEWPORT_ROWS_MIN, Math.min(VIEWPORT_ROWS_MAX, roh));
})();
const START_INNER_HEIGHT = window.innerHeight;

// Breite auf alle Canvas und die CSS-Variablen uebertragen. MUSS laufen, bevor
// irgendetwas gezeichnet wird: das Setzen von canvas.width leert den Canvas und
// setzt den Kontextzustand zurueck.
(function applyCanvasWidth() {
  for (const id of ['spec-o', 'axis-o', 'known-o', 'wf-o',
                    'spec-z', 'axis-z', 'known-z', 'wf-z']) {
    el(id).width = CANVAS_W;
  }
  // Hoehe: Sichtflaechen, Zeit-Gutter und Farbskalen folgen VIEWPORT_ROWS.
  for (const id of ['wf-o', 'wf-z', 'time-o', 'colorbar-o', 'colorbar-z']) {
    el(id).height = VIEWPORT_ROWS;
  }
  const wurzel = document.documentElement.style;
  wurzel.setProperty('--canvas-width', CANVAS_W + 'px');
  wurzel.setProperty('--wf-height', VIEWPORT_ROWS + 'px');
  // T-2011a (BUG-0009): Breite des Elevator-Scrollbalkens messen (0 bei
  // ueberlagernden Scrollbalken) und .wf-scroll sowie die Inhaltsbreite darum
  // erweitern - sonst deckt er den rechten Rand des Wasserfalls ab.
  const scrollbarPx = Math.max(0, el('wfScrollO').offsetWidth - el('wfScrollO').clientWidth);
  wurzel.setProperty('--wf-scrollbar', scrollbarPx + 'px');
  wurzel.setProperty('--content-width',
    (TIME_GUTTER_W + CANVAS_W + 2 + scrollbarPx + COLORBAR_W + 8) + 'px');   // +2 = Canvas-Rahmen
})();

// Fenster deutlich geaendert -> Anzeige selbsttaetig neu laden. CANVAS_W und
// VIEWPORT_ROWS werden nur beim Laden berechnet, ein Reload ist deshalb der
// einzige Weg zur passenden Geometrie. Der Timer entprellt das Ziehen am
// Fensterrand: geladen wird erst rund 0,8 s nach der LETZTEN Groessenaenderung,
// bis dahin zeigt #resizeHint die bevorstehende Aktion an. Zieht der Nutzer
// wieder unter die Schwelle zurueck, wird der Timer verworfen. Die
// Wasserfall-Historie im Browser geht beim Reload verloren - bewusst in Kauf
// genommen (Entscheidung 2026-09-04, ersetzt den frueheren Hinweis-Knopf).
let resizeReloadTimer = null;
window.addEventListener('resize', () => {
  const geaendert = Math.abs(window.innerWidth - START_INNER_WIDTH) > 100
                 || Math.abs(window.innerHeight - START_INNER_HEIGHT) > 100;
  if (resizeReloadTimer) { clearTimeout(resizeReloadTimer); resizeReloadTimer = null; }
  el('resizeHint').style.display = geaendert ? 'inline-flex' : 'none';
  if (!geaendert) { return; }
  resizeReloadTimer = setTimeout(() => { location.reload(); }, 800);
});

const specO = el('spec-o'), wfO = el('wf-o'), axisO = el('axis-o'), overlayO = el('overlay-o');
const specZ = el('spec-z'), wfZ = el('wf-z'), axisZ = el('axis-z'), overlayZ = el('overlay-z');
const ctxSpecO = specO.getContext('2d'), ctxWfO = wfO.getContext('2d'), ctxAxisO = axisO.getContext('2d');
const ctxSpecZ = specZ.getContext('2d'), ctxWfZ = wfZ.getContext('2d'), ctxAxisZ = axisZ.getContext('2d');
const timeO = el('time-o'), ctxTimeO = timeO.getContext('2d');
const wfScrollO = el('wfScrollO'), wfSpacerO = el('wfSpacerO');

let mode = 'overview';           // 'overview' | 'zoom'
// Nach "zurueck zur Uebersicht" treffen oft noch nachlaufende IFPAN-Frames ein,
// bis der EB200 wirklich auf D-SCAN umgeschaltet hat. Solange dieses Flag gesetzt
// ist, unterdrueckt der ifpan-Handler die automatische Zoom-Umschaltung, damit
// die Uebersicht nicht sofort wieder in den Zoom zurueckspringt. Freigabe beim
// ersten eintreffenden D-SCAN-Frame.
let awaitingOverview = false;
// Server-Hinweis (SSE-Typ "notice") haelt #meta-o kurz fest, damit ihn der
// naechste Sweep nicht sofort ueberschreibt - siehe evtSource.onmessage.
let noticeHoldUntil = 0;
let paused = false;
let latestO = null, latestZ = null;

// Port des separat laufenden SDR-Wasserfalls (sdr_waterfall.py, siehe dort) -
// nur hier im Browser-JS gebraucht, Client greift immer ueber location.hostname
// zu (nicht auf einen fest einprogrammierten Hostnamen).
const SDR_WATERFALL_PORT = 8030;

// Zustand des SDR-Ergaenzungsmodus (Punkt C, Teil C): active/device werden
// vom sdrToggle-Handler gesetzt, attachSelection() fragt sie im bestehenden
// Drag-Zweig der Uebersicht ab (siehe dort) - ohne aktiven SDR-Modus hat ein Ziehen dort keine Wirkung.
let sdrMode = { active: false, device: null };
let sdrWin = null;   // Referenz auf das SDR-Wasserfall-Fenster (Wiederverwendung)

// -- Fensterdisziplin fuer die Nachbar-Server (:8010/:8020/:8040) ------------
//
// Pro Server soll immer nur ein Browser-Tab existieren. Dazu bekommt jeder
// Tab einen festen Namen (benannter Browsing-Kontext): window.open() mit
// demselben Namen verwendet einen bereits offenen Tab wieder und navigiert
// ihn auf die neue URL (= Refresh), statt einen weiteren zu oeffnen. Das
// funktioniert auch ueber Ports hinweg (Cross-Origin), solange der Zieltab
// seinen Namen kennt - die Server-Seiten setzen daher beim Laden selbst
// window.name (siehe block_admin.py, analyze_sessions.py, analyze_spectrum.py).
// Zusaetzlich merken wir uns die Fensterreferenz, um .focus() aufrufen zu
// koennen; der Wasserfall (:8000) selbst ist von der Regel ausgenommen.
const serverWins = {};   // name -> Window-Referenz

function openServerTab(name, url) {
  const w = serverWins[name];
  if (w && !w.closed) {
    w.location.href = url;   // bestehenden Tab neu laden
    try { w.focus(); } catch (e) { /* Cross-Origin: focus kann verweigert werden */ }
    return w;
  }
  // Kein Referenz-Treffer: window.open mit Namen - der Browser verwendet
  // einen gleichnamigen Tab (auch manuell geoeffnet) wieder oder legt ihn an.
  const nw = window.open(url, name);
  if (nw) { serverWins[name] = nw; try { nw.focus(); } catch (e) {} }
  return nw;
}
let scaleMin = -30, scaleMax = 60, autoScale = true;
// zuletzt von der Auto-Skalierung ermittelte Grenzen (fuer Uebernahme in die Regler)
let lastAutoBounds = [scaleMin, scaleMax];
// [loRounded, hiRounded] der letzten Colorbar-Zeichnung (Cache gegen Flackern).
// MUSS hier oben stehen: loadScaleState('o') laeuft beim Skriptstart und ruft
// drawColorbar() auf - drawColorbar ist als function-Deklaration gehoistet,
// eine spaeter deklarierte let-Bindung laege dann aber in der Temporal Dead
// Zone und wuerde den restlichen Script-Lauf abbrechen (T-701-Hotfix).
let colorbarLast = null;

// T-701: getrennte Farbskalen-Zustaende je Ansicht (Uebersicht/Zoom), damit
// ein Ansichtswechsel nicht die manuell gesetzten Regler der jeweils anderen
// Ansicht ueberschreibt. 'o' = D-SCAN-Uebersicht, 'z' = IFPAN-Zoom. Die
// Globals (scaleMin/scaleMax/autoScale/lastAutoBounds) bleiben der Zustand
// der GERADE AKTIVEN Ansicht - scaleState haelt zusaetzlich den Zustand der
// jeweils anderen (bzw. zuletzt verlassenen) Ansicht vor.
const WF_SCALE_STORAGE_KEY = 'eb200_wf_scale_v1';   // gemeinsamer localStorage-Schluessel (Lesen/Schreiben)
let scaleState = {
  o: { min: -30, max: 60, auto: true, autoBounds: [-30, 60] },
  z: { min: -30, max: 60, auto: true, autoBounds: [-30, 60] },
};
let activeScaleView = 'o';

// Globals (scaleMin/scaleMax/autoScale/lastAutoBounds) nach scaleState[view]
// spiegeln und den kompletten Zustand nach localStorage schreiben.
function saveScaleState(view) {
  scaleState[view] = { min: scaleMin, max: scaleMax, auto: autoScale, autoBounds: lastAutoBounds };
  try {
    localStorage.setItem(WF_SCALE_STORAGE_KEY, JSON.stringify(scaleState));
  } catch (e) { /* localStorage nicht verfuegbar - Persistenz entfaellt, Betrieb laeuft weiter */ }
}

// scaleState[view] in die Globals UND in die Regler/Labels/Checkbox
// uebernehmen; Farbskala danach mit den WIRKSAMEN Grenzen (Auto -> die
// gemerkten autoBounds, sonst min/max) neu zeichnen.
function loadScaleState(view) {
  const s = scaleState[view];
  scaleMin = s.min; scaleMax = s.max; autoScale = s.auto; lastAutoBounds = s.autoBounds;
  el('scaleMin').value = s.min;
  el('scaleMax').value = s.max;
  el('scaleMinLabel').textContent = s.min + ' dBµV';
  el('scaleMaxLabel').textContent = s.max + ' dBµV';
  el('autoScale').checked = s.auto;
  const wirksam = s.auto ? s.autoBounds : [s.min, s.max];
  drawColorbar(wirksam[0], wirksam[1]);
}

// Ansichtswechsel: Zustand der bisherigen Ansicht sichern, dann den der
// neuen Ansicht laden. Kein Effekt, wenn bereits aktiv (z. B. mehrfacher
// externer Zoom-Trigger).
function switchScaleView(view) {
  if (view !== activeScaleView) {
    saveScaleState(activeScaleView);
    activeScaleView = view;
    loadScaleState(view);
    syncSquelchToScaleMin();   // T-702: ggf. an den neu geladenen Skala-min-Wert anpassen
  }
}

// Persistenz ueber Seiten-Reload: beim Skriptstart aus localStorage laden.
// Bei Fehlern (kein Eintrag, kaputtes JSON, localStorage gesperrt) bleiben
// die obigen Defaults bestehen - nie fatal.
try {
  const wfScaleGespeichert = localStorage.getItem(WF_SCALE_STORAGE_KEY);
  if (wfScaleGespeichert) {
    const wfScaleGeladen = JSON.parse(wfScaleGespeichert);
    if (wfScaleGeladen && wfScaleGeladen.o && wfScaleGeladen.z) scaleState = wfScaleGeladen;
  }
} catch (e) { /* localStorage nicht verfuegbar oder kaputtes JSON - Defaults bleiben */ }
loadScaleState('o');

// ---------------------------------------------------------------------------
// NEU: Elevator-Scroll-Historie für den D-SCAN-Übersichts-Wasserfall.
// wf-o (CANVAS_W x 300, sichtbar) ist nur noch ein Fenster/Viewport auf einen
// viel größeren Offscreen-Pixelpuffer (CANVAS_W x HISTORY_ROWS). Zeile 0 des
// Puffers ist stets die aktuelle/neueste Wasserfallzeile; ältere Zeilen
// wandern beim Scrollen des Puffers zu höheren Zeilenindizes. Der sichtbare
// Ausschnitt [scrollRow .. scrollRow+VIEWPORT_ROWS) wird bei jeder Änderung
// neu in wf-o geblittet (siehe renderViewportO()).
// ---------------------------------------------------------------------------
const HISTORY_ROWS = 6000;   // Tiefe der gehaltenen Wasserfall-Historie (px = Zeilen)
// VIEWPORT_ROWS (= wf-o.height): siehe Hoehenbestimmung bei CANVAS_W weiter oben.
const BAND_PX = 18;          // Höhe der schwarzen Parameter-Tafel (Punkt 3)
const AXIS_PX = 16;          // Höhe eines gebrannten Frequenz-Achsstreifens (Punkt B)
const SEP_PX = 4;            // Höhe der weißen Trennlinie zwischen Parameter-Tafel und Vorher-Achse

const historyCanvas = document.createElement('canvas');
historyCanvas.width = CANVAS_W;   // siehe CANVAS_W oben
historyCanvas.height = HISTORY_ROWS;
const ctxHistory = historyCanvas.getContext('2d');
ctxHistory.fillStyle = '#000';
ctxHistory.fillRect(0, 0, historyCanvas.width, historyCanvas.height);

let usedRows = 0;      // Anzahl bisher gefüllter Zeilen im Puffer (<= HISTORY_ROWS)
let scrollRow = 0;     // aktuell angezeigte oberste Pufferzeile (0 = Live-Rand)
let timeMarks = [];    // { y, label } - alle 30s-Zeitmarken, wandern mit dem Puffer mit
let lastTimeMarkBucket = null;  // floor(t/30) der letzten gesetzten Zeitmarke

// ---------------------------------------------------------------------------
// NEU: Frequenzreferenz (Achsstreifen + laufendes Raster), gebrannt in den
// Historienpuffer. segmentStartHz/segmentStopHz beschreiben den Bereich des
// aktuell OBERSTEN (=neuesten, wachsenden) Segments; seine Nice-Ticks
// (computeNiceTicks) traegt seit T-2010 der Eintrag segmente[0] - neu
// berechnet bei jedem eingefügten Header. Startwert aus den Eingabe-
// feldern, passend zum Server-Default-Bereich.
// ---------------------------------------------------------------------------
let segmentStartHz = Math.round(parseFloat(el('dscanStart').value) * 1e6);
let segmentStopHz  = Math.round(parseFloat(el('dscanStop').value) * 1e6);

// T-2010: Segmentliste des Uebersichts-Wasserfalls. Ein Segment ist der
// Zeilenbereich [oben, unten) des Historienpuffers zwischen zwei
// Parameterwechseln, mit seinem Frequenzbereich und den passenden Nice-Ticks.
// segmente[0] ist das neueste und das einzige offene Segment: es waechst an
// der Oberkante (Zeile 0 bleibt bei ihm), alle anderen wandern mit dem Puffer
// nach unten (siehe shiftHistory). Die Kopfzeilen eines Parameterwechsels
// gehoeren zu keinem Segment. grid: Raster in diesem Segment an/aus, ein
// neues Segment uebernimmt den Zustand des vorigen (Grundlage fuer T-2011).
let segmente = [];
function segmentNeu(startHz, stopHz, grid) {
  if (segmente.length) segmente[0].offen = false;
  segmente.unshift({ oben: 0, unten: 0, startHz, stopHz,
                     ticks: computeNiceTicks(startHz, stopHz), grid: !!grid, offen: true });
}
segmentNeu(segmentStartHz, segmentStopHz, false);

// pendingHeader ersetzt das frühere pendingAnnotation: statt nur eines
// Texts wird jetzt der komplette "Segmentwechsel" vorgehalten (Text +
// neuer Bereich + ob es der allererste Header überhaupt ist - siehe
// Punkt C der Aufgabenstellung).
let pendingHeader = null;   // null | { text, newStartHz, newStopHz, isInitial }
let firstHeaderDone = false; // wird beim allerersten fertigen Sweep einmalig gesetzt
// T-505: Auto-Marker - Chip (Frequenzbeschriftung) ein/aus, Zaehler, Farbe (gelb,
// zur Unterscheidung von den weissen Klick-Markern).
let autoMarkChip = true;
let autoMarkCount = 0;
// T-2012: Rechtecke aller gebrannten Frequenz-Chips {x, y, w, h, text, stufe,
// extra} in Pufferkoordinaten - wandern in shiftHistory mit, damit ein neuer
// Chip einem alten ausweichen kann. labelAbstandPx: Mindestabstand zwischen
// zwei Chips (Regler "Label spacing" im Auto-marker-Menue).
let chipRechtecke = [];
let labelAbstandPx = 4;
const autoMarkEvents = []; // T-505b: gebrannte Ereignisse {t, f_hz, level, floor}, in Markierungsreihenfolge
const AUTO_MARK_COLOR = '#ffd400';
// T-2011: Raster als Overlay je Segment (zeichneRasterOverlay). Ein/Aus steht
// je Segment in segmente[i].grid; hier nur die Darstellung, im Browser
// gespeichert (localStorage, Schluessel eb200_wf_grid_v1).
const GRID_FARBEN = { white: '#ffffff', black: '#000000', cyan: '#00e5ff', magenta: '#ff3df2' };
const GRID_CFG_KEY = 'eb200_wf_grid_v1';
let gridCfg = { alpha: 60, color: 'white', dash: false, timeStep: 'auto', timeLabel: 'left' };
try {
  const gespeichert = JSON.parse(localStorage.getItem(GRID_CFG_KEY) || 'null');
  if (gespeichert && typeof gespeichert === 'object') gridCfg = Object.assign(gridCfg, gespeichert);
} catch (e) { /* localStorage nicht verfuegbar oder kaputtes JSON - Vorgaben bleiben */ }
// Zeitstempel fuer die waagrechten Rasterlinien: eine Marke {y, bucket} je
// angefangene 10 s (feinstes waehlbares Intervall); wandert wie timeMarks mit.
let rasterZeiten = [];
let lastRasterBucket = null;

// NEU: Parameter-Teile des ZULETZT gebrannten Banners - Referenz für den
// Vergleich in paintAnnotationBand (geänderte Parameter -> fett/gelb).
// null = noch kein Banner geschrieben -> beim ersten Banner nichts markieren.
let lastBannerParts = null;

// Anfangszustand einmal rendern (Puffer ist noch leer/schwarz) - renderViewportO
// und drawTimeOverlay sind als function-Deklarationen weiter unten im Skript
// definiert, aber wie in JS üblich bereits jetzt aufrufbar (Hoisting).
renderViewportO();

// EIN Pausezustand fuer beide Ansichten (paused gatet den EventSource-
// Handler global); die zwei Knoepfe (Uebersicht/Zoom) werden synchron
// beschriftet.
function togglePause() {
  paused = !paused;
  // (d): mit Icon - Wiedergabe-Dreieck beim Fortsetzen, Pause-Balken sonst.
  const text = paused ? SVG_PLAY + ' Resume' : SVG_PAUSE + ' Pause';
  el('pauseBtn').innerHTML = text;
  el('pauseBtnZ').innerHTML = text;
}
el('pauseBtn').onclick = togglePause;
el('pauseBtnZ').onclick = togglePause;
el('scaleMin').oninput = e => {
  scaleMin = parseFloat(e.target.value);
  el('scaleMinLabel').textContent = scaleMin + ' dBµV';
  if (!autoScale) drawColorbar(scaleMin, scaleMax);   // auch ohne laufende Frames (Pause)
  saveScaleState(activeScaleView);   // T-701: Persistenz bei jeder Aenderung
};
el('scaleMin').onchange = () => {
  // T-702: Squelch-Kopplung erst beim Loslassen anwenden (kein Netzwerk-
  // Request bei jedem Zwischenschritt); nur relevant, wenn gerade die
  // Zoom-Ansicht aktiv ist.
  if (activeScaleView === 'z') syncSquelchToScaleMin();
};
el('scaleMax').oninput = e => {
  scaleMax = parseFloat(e.target.value);
  el('scaleMaxLabel').textContent = scaleMax + ' dBµV';
  if (!autoScale) drawColorbar(scaleMin, scaleMax);
  saveScaleState(activeScaleView);   // T-701: Persistenz bei jeder Aenderung
};
// schreibt Grenzwerte (gerundet, auf Reglerbereich -40..120 begrenzt) in beide Regler
function applyBoundsToSliders(lo, hi) {
  lo = Math.max(-40, Math.min(120, Math.round(lo)));
  hi = Math.max(-40, Math.min(120, Math.round(hi)));
  scaleMin = lo; scaleMax = hi;
  el('scaleMin').value = lo; el('scaleMax').value = hi;
  el('scaleMinLabel').textContent = lo + ' dBµV';
  el('scaleMaxLabel').textContent = hi + ' dBµV';
  saveScaleState(activeScaleView);   // T-701: Persistenz bei jeder Aenderung
  syncSquelchToScaleMin();   // T-702: idempotent, wirkt nur bei aktivem squelchFollow
}
el('autoScale').onchange = e => {
  autoScale = e.target.checked;
  // aktuell ermittelte Auto-Werte in die min/max-Regler uebernehmen
  // (nahtloser Uebergang beim spaeteren Umschalten auf manuell)
  applyBoundsToSliders(lastAutoBounds[0], lastAutoBounds[1]);
  saveScaleState(activeScaleView);   // T-701: Persistenz bei jeder Aenderung
};

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

function drawSpectrum(ctx, canvas, levels) {
  const w = canvas.width, h = canvas.height;
  // 'levels' ist hier immer die VOLLSTÄNDIGE, an Ort und Stelle
  // überschriebene Anzeige (Mischung aus altem und bereits neu
  // eingetroffenem Sweep, siehe dscanDisplayLevels weiter unten) - daher
  // ist ein Clear+Redraw hier korrekt und zeigt trotzdem immer nur EINE
  // durchgehende Kurve, kein Aufhäufen mehrerer Sweeps übereinander.
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  const defined = [];
  levels.forEach((v, i) => { if (v !== null && v !== undefined) defined.push(i); });
  if (defined.length === 0) return;
  const values = defined.map(i => levels[i]);
  // Spektrum-Trace immer an die tatsächlichen Pegel anpassen, damit hohe
  // Spitzen nie oben abgeschnitten werden. Bewusst ENTKOPPELT von
  // scaleMin/scaleMax und der Auto-Checkbox - diese steuern nur noch die
  // Farbskala des Wasserfalls.
  // Kein Spread - siehe minMaxDefined() (Argument-Stack ab ~122 000 Werten).
  const mm = minMaxDefined(values);
  const dataMin = mm.lo, dataMax = mm.hi;
  const range = Math.max(dataMax - dataMin, 1);   // Division durch 0 vermeiden
  const lo = dataMin - range * 0.05;              // etwas Luft unten
  const hi = dataMax + range * 0.08;              // etwas mehr Luft oben
  const n = levels.length;
  ctx.strokeStyle = '#8fd'; ctx.beginPath();
  let prevIdx = null;
  defined.forEach(i => {
    const x = (i / (n - 1)) * w;
    const y = h - ((levels[i] - lo) / (hi - lo)) * h;
    // noch nie gesetzte Punkte (nur ganz zu Anfang, vor dem ersten
    // kompletten Sweep möglich) nicht verbinden, sondern als Lücke
    // behandeln, statt querfeldein zu interpolieren.
    if (prevIdx === null || i !== prevIdx + 1) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
    prevIdx = i;
  });
  ctx.stroke();
}

// malt ein neu eingetroffenes Segment (offset..offset+segment.length) direkt
// in Zeile 0 (die "gerade laufende" Wasserfallzeile) - OHNE zu scrollen.
// So füllt sich die oberste Zeile während des Sweeps kontinuierlich, statt
// erst am Ende komplett zu erscheinen.
function paintPartialRow(ctx, canvas, offset, segment, total, lo, hi) {
  if (segment.length === 0) return;
  const w = canvas.width;
  const xStart = Math.floor((offset / total) * w);
  const xEnd = Math.floor(((offset + segment.length) / total) * w);
  for (let x = xStart; x < xEnd; x++) {
    let idx = Math.floor((x / w) * total) - offset;
    idx = Math.max(0, Math.min(segment.length - 1, idx));
    ctx.fillStyle = levelToColor(segment[idx], lo, hi);
    ctx.fillRect(x, 0, 1, 1);
  }
}

// verschiebt das bestehende Wasserfallbild um eine Zeile nach unten
// (Zeile 0 - die gerade fertiggestellte Sweep-Zeile - wird dadurch Teil
// der Historie). Zeile 0 selbst wird danach separat geleert.
function scrollWaterfallDown(ctx, canvas) {
  const w = canvas.width, h = canvas.height;
  ctx.drawImage(canvas, 0, 0, w, h - 1, 0, 1, w, h - 1);
}

function clearTopRow(ctx, canvas) {
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, 1);
}

// -- Historienpuffer-Helfer (nur D-SCAN-Übersicht, siehe Konstanten oben) --

// verschiebt den GESAMTEN Historienpuffer um 'rows' Zeilen nach unten (analog
// scrollWaterfallDown, aber für eine beliebige Zeilenzahl - gebraucht für die
// normale 1-Zeilen-Verschiebung je fertigem Sweep UND für die BAND_PX-hohe
// Parameter-Tafel aus Punkt 3). Ist der Puffer voll, fallen die untersten
// (ältesten) 'rows' Zeilen dabei automatisch weg.
function scrollHistoryDownBy(rows) {
  const w = historyCanvas.width, h = historyCanvas.height;
  ctxHistory.drawImage(historyCanvas, 0, 0, w, h - rows, 0, rows, w, h - rows);
}

function clearHistoryTopRows(rows) {
  ctxHistory.fillStyle = '#000';
  ctxHistory.fillRect(0, 0, historyCanvas.width, rows);
}

// Verschiebt den Puffer um 'rows' Zeilen nach unten UND hält dabei
// Zeitmarken, gefüllte Zeilenzahl (usedRows) und die aktuell betrachtete
// Scroll-Position (scrollRow) konsistent:
//  - Zeitmarken wandern im Gleichtakt mit dem Pixelinhalt mit (y += rows).
//  - Steht man am Live-Rand (scrollRow===0), bleibt man dort (Auto-Follow) -
//    neue Zeilen erscheinen weiterhin oben im Bild.
//  - Ist man in die Historie gescrollt (scrollRow>0), wird scrollRow um
//    dieselbe Zeilenzahl erhöht, damit die betrachtete Stelle optisch stehen
//    bleibt, statt nach oben "wegzureißen".
function shiftHistory(rows) {
  scrollHistoryDownBy(rows);
  for (const mark of timeMarks) mark.y += rows;
  timeMarks = timeMarks.filter(mark => mark.y < HISTORY_ROWS);
  // T-2010: Segmentgrenzen wandern mit; das offene Segment waechst nur unten.
  for (const s of segmente) {
    if (!s.offen) s.oben += rows;
    s.unten = Math.min(HISTORY_ROWS, s.unten + rows);
  }
  segmente = segmente.filter(s => s.oben < HISTORY_ROWS);
  for (const m of rasterZeiten) m.y += rows;   // T-2011
  rasterZeiten = rasterZeiten.filter(m => m.y < HISTORY_ROWS);
  for (const r of chipRechtecke) r.y += rows;   // T-2012
  chipRechtecke = chipRechtecke.filter(r => r.y < HISTORY_ROWS);
  usedRows = Math.min(HISTORY_ROWS, usedRows + rows);
  if (scrollRow !== 0) {
    scrollRow = Math.min(scrollRow + rows, Math.max(0, usedRows - VIEWPORT_ROWS));
  }
}

// passt die Spacer-Höhe im Scroll-Container an die bisher gefüllte
// Zeilenzahl an - dadurch wächst der Scrollbalken erst, sobald tatsächlich
// Historie zum Anschauen vorhanden ist.
function updateSpacerHeight() {
  const totalRows = Math.max(VIEWPORT_ROWS, usedRows);
  wfSpacerO.style.height = (totalRows - VIEWPORT_ROWS) + 'px';
}

// blittet den zu scrollRow passenden Ausschnitt des Historienpuffers in die
// sichtbare wf-o-Canvas und zeichnet danach die 30s-Zeitmarken-Overlays
// (dünne Linie im Wasserfall + HH:MM:SS-Label im Zeit-Gutter) darüber. Die
// Overlays werden bewusst NICHT in den Puffer gebrannt, sondern bei jedem
// Render neu aus timeMarks gezeichnet (siehe Punkt 2 der Aufgabe).
function renderViewportO() {
  ctxWfO.drawImage(historyCanvas, 0, scrollRow, historyCanvas.width, VIEWPORT_ROWS,
                    0, 0, wfO.width, VIEWPORT_ROWS);
  zeichneRasterOverlay(ctxWfO, 0, 0, scrollRow, scrollRow + VIEWPORT_ROWS, wfO.width);   // T-2011
  drawTimeOverlay();
}

function drawTimeOverlay() {
  ctxTimeO.fillStyle = '#000';
  ctxTimeO.fillRect(0, 0, timeO.width, timeO.height);
  ctxTimeO.strokeStyle = '#666';
  ctxTimeO.fillStyle = '#aaa';
  ctxTimeO.font = '10px sans-serif';
  ctxTimeO.textAlign = 'left';
  ctxTimeO.textBaseline = 'middle';

  ctxWfO.strokeStyle = 'rgba(255,255,255,0.35)';
  ctxWfO.lineWidth = 1;

  for (const mark of timeMarks) {
    const y = mark.y - scrollRow;
    if (y < 0 || y >= VIEWPORT_ROWS) continue;   // außerhalb des sichtbaren Fensters
    ctxWfO.beginPath();
    ctxWfO.moveTo(0, y + 0.5);
    ctxWfO.lineTo(wfO.width, y + 0.5);
    ctxWfO.stroke();

    ctxTimeO.beginPath();
    ctxTimeO.moveTo(timeO.width - 6, y + 0.5);
    ctxTimeO.lineTo(timeO.width, y + 0.5);
    ctxTimeO.stroke();
    ctxTimeO.fillText(mark.label, 2, y);
  }
}

// HH:MM:SS - gleiche Konvention wie updateMetaOverview/updateMetaZoom weiter
// unten (isoTime, feste Schreibweise seit T-1803b).
function formatClock(tEpochSeconds) {
  return isoTime(new Date(tEpochSeconds * 1000));
}

// setzt die schwarze Parameter-Tafel in BAND_PX Zeilen des Puffers, beginnend
// bei yOffset (Default 0). Die Zeilen [yOffset..yOffset+BAND_PX) müssen zuvor
// per shiftHistory() frei gemacht worden sein. parts ist die Liste aus
// composeParameterAnnotation() - jeder Teil wird gegen den zuletzt gebrannten
// Banner (lastBannerParts) verglichen: hat sich sein Text geändert, wird er
// fett/gelb statt weiß/normal gezeichnet. Die Trennzeichen · dazwischen
// bleiben immer weiß/normal.
function paintAnnotationBand(parts, yOffset = 0) {
  ctxHistory.fillStyle = '#000';
  ctxHistory.fillRect(0, yOffset, historyCanvas.width, BAND_PX);
  ctxHistory.textAlign = 'left';
  ctxHistory.textBaseline = 'middle';
  const y = yOffset + Math.floor(BAND_PX / 2);
  const SEP = ' · ';
  let x = 4;

  // Pfeil nach oben: markiert diese Zeile als ab-jetzt-gültige Parameter, in
  // Richtung des neuen/nachrückenden Wasserfalls (der oberhalb entsteht).
  // Immer weiß, unabhängig davon, ob einzelne Parameter sich geändert haben.
  ctxHistory.fillStyle = '#fff';
  ctxHistory.font = '12px sans-serif';
  const ARROW_UP = '↑ ';
  ctxHistory.fillText(ARROW_UP, x, y);
  x += ctxHistory.measureText(ARROW_UP).width;

  parts.forEach((part, i) => {
    // Trennzeichen zwischen den Teilen immer weiß/normal.
    if (i > 0) {
      ctxHistory.fillStyle = '#fff';
      ctxHistory.font = '12px sans-serif';
      ctxHistory.fillText(SEP, x, y);
      x += ctxHistory.measureText(SEP).width;
    }
    // Vergleich mit dem zuletzt gebrannten Banner (nach key). Beim allerersten
    // Banner ist lastBannerParts noch null -> kein Teil gilt als geändert.
    const prev = lastBannerParts ? lastBannerParts.find(p => p.key === part.key) : null;
    const changed = !!prev && prev.text !== part.text;
    if (changed) {
      ctxHistory.fillStyle = '#ff0';               // gelb: geänderter Parameter
      ctxHistory.font = 'bold 12px sans-serif';    // zusätzlich fett
    } else {
      ctxHistory.fillStyle = '#fff';
      ctxHistory.font = '12px sans-serif';
    }
    ctxHistory.fillText(part.text, x, y);
    x += ctxHistory.measureText(part.text).width;
  });
  // dieses Banner wird zur Vergleichsreferenz für das nächste.
  lastBannerParts = parts;
}

// brennt eine schmale weiße Trennlinie (SEP_PX Zeilen) in den Historienpuffer -
// dient als optischer Trenner zwischen der Parameter-Tafel und der darunter
// liegenden Vorher-Achse (alter Frequenzbereich), damit die beiden Blöcke
// klar auseinandergehalten werden können.
function paintSeparatorLine(yOffset) {
  ctxHistory.fillStyle = '#fff';
  ctxHistory.fillRect(0, yOffset, historyCanvas.width, SEP_PX);
}

// NEU: brennt ein kleines Fadenkreuz + die zugehörige (an IFPAN übergebene)
// Frequenz an der angeklickten Stelle dauerhaft in den Historienpuffer.
// Da direkt in historyCanvas gezeichnet wird, wandert der Marker beim Scrollen
// automatisch mit dem Wasserfall mit und erscheint unverändert im JPG-Export
// (der ebenfalls aus historyCanvas rendert). Bewusst KEIN Overlay, sondern fest
// eingebrannt (Anforderung: "fix").
// T-505: opts (optional) = { chip: false -> kein Frequenz-Chip, color: Kreuzfarbe }.
function paintClickMarker(xPix, bufferRow, freqHz, opts) {
  const cx = Math.max(0, Math.min(historyCanvas.width - 1, Math.round(xPix)));
  const cy = Math.max(0, Math.min(HISTORY_ROWS - 1, Math.round(bufferRow)));
  const ARM = 7;   // Länge eines Fadenkreuz-Arms in px
  const GAP = 3;   // freier Mittelbereich, damit der Klickpunkt sichtbar bleibt

  // Fadenkreuz zweifach: erst dicke schwarze Kontur (Lesbarkeit über hellen
  // Signalstreifen), dann dünn weiß darüber.
  function strokeCross(color, width) {
    ctxHistory.strokeStyle = color;
    ctxHistory.lineWidth = width;
    ctxHistory.beginPath();
    ctxHistory.moveTo(cx - ARM, cy + 0.5); ctxHistory.lineTo(cx - GAP, cy + 0.5);
    ctxHistory.moveTo(cx + GAP, cy + 0.5); ctxHistory.lineTo(cx + ARM, cy + 0.5);
    ctxHistory.moveTo(cx + 0.5, cy - ARM); ctxHistory.lineTo(cx + 0.5, cy - GAP);
    ctxHistory.moveTo(cx + 0.5, cy + GAP); ctxHistory.lineTo(cx + 0.5, cy + ARM);
    ctxHistory.stroke();
  }
  strokeCross('#000', 3);
  strokeCross((opts && opts.color) || '#fff', 1);
  if (opts && opts.chip === false) return;

  // T-2012: Frequenz-Chip ohne Ueberlappung. Gesucht wird eine freie Lage
  // gegen chipRechtecke (alle bereits gebrannten Chips, wandern mit dem
  // Puffer): erst rechts, dann links auf Hoehe des Kreuzes, dann gestaffelt
  // eine oder zwei Stufen darueber/darunter mit Fuehrungslinie, dann mit
  // kuerzerer Beschriftung ("97.9200", ".9200"). Ist auch dann kein Platz,
  // zaehlt der naechstgelegene Chip den Marker mit ("+1"). Das Kreuz steht in
  // jedem Fall an der exakten Frequenz. Kasten deckend schwarz, heller Rand.
  ctxHistory.font = '11px sans-serif';
  ctxHistory.textAlign = 'left';
  ctxHistory.textBaseline = 'middle';
  const padX = 4, chipH = 15;
  const mhz = (freqHz / 1e6).toFixed(4);
  const texte = [mhz + ' MHz', mhz, mhz.slice(mhz.indexOf('.'))];
  const farbe = (opts && opts.color) || '#fff';
  const frei = r => !chipRechtecke.some(o =>
    r.x < o.x + o.w + labelAbstandPx && r.x + r.w + labelAbstandPx > o.x &&
    r.y < o.y + o.h + labelAbstandPx && r.y + r.h + labelAbstandPx > o.y);
  function zeichneChip(r, text) {
    r.w = Math.max(r.w, Math.ceil(ctxHistory.measureText(text).width) + 2 * padX);
    r.x = Math.max(0, Math.min(r.x, historyCanvas.width - r.w));
    ctxHistory.fillStyle = '#000';
    ctxHistory.fillRect(r.x, r.y, r.w, r.h);
    ctxHistory.strokeStyle = 'rgba(255,255,255,0.85)';
    ctxHistory.lineWidth = 1;
    ctxHistory.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
    ctxHistory.fillStyle = '#fff';
    ctxHistory.fillText(text, r.x + padX, r.y + r.h / 2 + 0.5);
  }

  let platz = null;
  for (const text of texte) {
    const w = Math.ceil(ctxHistory.measureText(text).width) + 2 * padX;
    for (const stufe of [0, 1, -1, 2, -2]) {
      for (const rechts of [true, false]) {
        const x = Math.max(0, Math.min(historyCanvas.width - w, rechts ? cx + ARM + 3 : cx - ARM - 3 - w));
        const y = Math.max(0, Math.min(HISTORY_ROWS - chipH,
          cy - Math.floor(chipH / 2) + stufe * (chipH + labelAbstandPx + 1)));
        const r = { x, y, w, h: chipH, text, stufe };
        if (frei(r)) { platz = r; break; }
      }
      if (platz) break;
    }
    if (platz) break;
  }

  if (!platz) {
    // Buendeln: der naechstgelegene Chip zaehlt diesen Marker mit.
    let naechster = null, abstand2 = Infinity;
    for (const o of chipRechtecke) {
      const d2 = (o.x + o.w / 2 - cx) ** 2 + (o.y + o.h / 2 - cy) ** 2;
      if (d2 < abstand2) { abstand2 = d2; naechster = o; }
    }
    if (!naechster) return;
    naechster.extra = (naechster.extra || 0) + 1;
    zeichneChip(naechster, naechster.text + ' +' + naechster.extra);
    return;
  }

  if (platz.stufe !== 0) {
    // Fuehrungslinie vom Kreuz zur naechsten Chipkante, Kontur wie das Kreuz
    const zielX = platz.x > cx ? platz.x : platz.x + platz.w;
    const zielY = platz.y + chipH / 2;
    for (const [c, lw] of [['#000', 3], [farbe, 1]]) {
      ctxHistory.strokeStyle = c;
      ctxHistory.lineWidth = lw;
      ctxHistory.beginPath();
      ctxHistory.moveTo(cx + 0.5, cy + 0.5);
      ctxHistory.lineTo(zielX, zielY);
      ctxHistory.stroke();
    }
  }
  chipRechtecke.push(platz);
  zeichneChip(platz, platz.text);
}

// NEU: rechnet Klickposition + Frequenz in Pufferkoordinaten um und brennt den
// Marker. Gate über die Checkbox #markToggle. yFracOrNull===null => an den
// aktuell obersten sichtbaren Rand (Klick im Spektrum-Streifen).
function placeClickMarker(xFrac, yFracOrNull, freqHz) {
  if (!el('markToggle').checked) return;
  const xPix = Math.round(xFrac * historyCanvas.width);
  let bufferRow = (yFracOrNull === null)
    ? scrollRow
    : scrollRow + Math.round(yFracOrNull * VIEWPORT_ROWS);
  // Zeile 0 ist die gerade wachsende Live-Zeile (wird von paintPartialRow
  // laufend überschrieben) — Marker frühestens in Zeile 1 setzen, sonst würde
  // er sofort wieder überschrieben. Nach oben auf HISTORY_ROWS-1 begrenzen.
  bufferRow = Math.max(1, Math.min(HISTORY_ROWS - 1, bufferRow));
  paintClickMarker(xPix, bufferRow, freqHz);
  renderViewportO();  // bei sichtbarer Übersicht sofort anzeigen
}

// -- Frequenzreferenz: Nice-Ticks, Achsstreifen, laufendes Raster (Punkte A/B/D) --

// findet die kleinste "runde" Zahl aus {1,2,2.5,5}·10^k, die >= rawStep ist -
// Standard-Algorithmus für angenehm lesbare Achsenteilungen (analog D3
// "nice ticks"), hier bewusst einfach und geradlinig statt generisch gehalten.
function niceStepFor(rawStep) {
  const multipliers = [1, 2, 2.5, 5, 10];
  const exponent = Math.floor(Math.log10(rawStep));
  for (let e = exponent - 1; e <= exponent + 1; e++) {
    for (const m of multipliers) {
      const candidate = m * Math.pow(10, e);
      if (candidate >= rawStep - 1e-9) return candidate;
    }
  }
  return Math.pow(10, exponent + 1);  // Sicherheitsnetz, praktisch nie erreicht
}

// rundet einen Frequenzwert in Hz auf eine kurze, lesbare Beschriftung:
// ab 1 MHz in MHz (ohne nachlaufende Nullen), darunter in kHz.
function formatNiceTickLabel(freqHz) {
  if (freqHz >= 1e6) {
    let s = (freqHz / 1e6).toFixed(6).replace(/0+$/, '').replace(/\\.$/, '');
    return s + ' MHz';
  }
  let s = (freqHz / 1e3).toFixed(3).replace(/0+$/, '').replace(/\\.$/, '');
  return s + ' kHz';
}

// T-2007: prueft, ob ein mittig gesetztes Tick-Label neben den beiden
// Randlabels (Start links-, Stop rechtsbuendig, je 2 px Einzug) Platz hat -
// mit der GEMESSENEN Textbreite statt eines festen Randabstands. Der feste
// Abstand war schmaler als ein langes Randlabel ("142.825 MHz") plus die
// halbe Breite des Nachbarlabels, so schob sich "144 MHz" hinein. Das
// Randlabel hat Vorrang. ctx muss die Schrift der Beschriftung schon tragen.
const AXIS_LABEL_GAP_PX = 6;
function tickLabelFrei(ctx, x, label, w, startLabel, stopLabel) {
  const halb = ctx.measureText(label).width / 2;
  const linksBis = 2 + ctx.measureText(startLabel).width + AXIS_LABEL_GAP_PX;
  const rechtsAb = w - 2 - ctx.measureText(stopLabel).width - AXIS_LABEL_GAP_PX;
  return (x - halb) >= linksBis && (x + halb) <= rechtsAb;
}

// liefert ~10 gleichmäßig verteilte, "rund" beschriftete Ticks über
// [startHz..stopHz] - gemeinsame Basis sowohl für die gebrannten
// Achsstreifen (paintFreqAxisStrip) als auch für das Raster-Overlay
// (zeichneRasterOverlay, je Segment über segmente[i].ticks).
function computeNiceTicks(startHz, stopHz) {
  const span = stopHz - startHz;
  if (!(span > 0)) return [];
  const rawStep = span / 10;
  const niceStep = niceStepFor(rawStep);
  const firstTick = Math.ceil(startHz / niceStep) * niceStep;
  const ticks = [];
  for (let f = firstTick; f <= stopHz + 1e-6; f += niceStep) {
    const freqHz = Math.round(f);
    const x = ((freqHz - startHz) / span) * CANVAS_W;
    ticks.push({ freqHz, x, label: formatNiceTickLabel(freqHz) });
  }
  return ticks;
}

// malt einen gebrannten Frequenz-Achsstreifen (AXIS_PX Zeilen, schwarz mit
// weißer Schrift) in den Historienpuffer: ~10 runde Nice-Ticks (aus
// computeNiceTicks) PLUS die exakten Scan-Grenzen (Start linksbündig bei
// x=0, Stop rechtsbündig bei x=CANVAS_W). Nice-Ticks nahe am Rand werden
// unterdrückt, damit sie sich nicht mit den Grenz-Labels überlappen.
function paintFreqAxisStrip(yOffset, startHz, stopHz) {
  ctxHistory.fillStyle = '#000';
  ctxHistory.fillRect(0, yOffset, historyCanvas.width, AXIS_PX);
  ctxHistory.strokeStyle = '#fff';
  ctxHistory.fillStyle = '#fff';
  ctxHistory.font = '10px sans-serif';
  ctxHistory.textBaseline = 'top';

  const ticks = computeNiceTicks(startHz, stopHz);
  // T-2007: Randlabels zuerst festlegen; ein Tick-Label erscheint nur, wenn
  // es daneben Platz hat (gemessene Breite, tickLabelFrei).
  const startLabel = formatNiceTickLabel(startHz);
  const stopLabel = formatNiceTickLabel(stopHz);
  for (const tick of ticks) {
    if (!tickLabelFrei(ctxHistory, tick.x, tick.label, historyCanvas.width, startLabel, stopLabel)) continue;
    ctxHistory.beginPath();
    ctxHistory.moveTo(tick.x + 0.5, yOffset);
    ctxHistory.lineTo(tick.x + 0.5, yOffset + 3);
    ctxHistory.stroke();
    ctxHistory.textAlign = 'center';
    ctxHistory.fillText(tick.label, tick.x, yOffset + 4);
  }

  ctxHistory.textAlign = 'left';
  ctxHistory.fillText(startLabel, 2, yOffset + 4);
  ctxHistory.textAlign = 'right';
  ctxHistory.fillText(stopLabel, historyCanvas.width - 2, yOffset + 4);
}

// T-2011: Intervall der waagrechten Rasterlinien in Sekunden, null = aus.
// Auto: kleinstes Intervall mit mindestens 40 Zeilen Abstand - geschaetzt aus
// dem Zeilenabstand der beiden neuesten 10-s-Marken (Sweeps sind verschieden
// lang, eine feste Zeilenzahl je Zeit gibt es nicht).
function rasterZeitSchritt() {
  if (gridCfg.timeStep === 'off') return null;
  if (gridCfg.timeStep !== 'auto') return parseInt(gridCfg.timeStep, 10) || null;
  if (rasterZeiten.length < 2) return 60;
  const n = rasterZeiten.length;
  const zeilenJe10s = Math.max(1, rasterZeiten[n - 2].y - rasterZeiten[n - 1].y);
  for (const s of [10, 30, 60, 300, 900]) if (zeilenJe10s * s / 10 >= 40) return s;
  return 900;
}

// T-2011: zeichnet das Raster als Overlay - nur in Segmenten mit grid, je
// Segment mit dessen eigenen Nice-Ticks; nichts wird in den Pixelpuffer
// gebrannt, Ausschalten heisst nicht zeichnen. Linien mit dunkler Kontur
// (3 px) unter der farbigen Linie (1 px), damit sie auf jedem Untergrund des
// Wasserfalls stehen (Muster drawFreqGridOverlay in analyze_sessions.py).
// ctx/x0/y0/breite: Zielflaeche; vonRow/bisRow: dargestellter Pufferbereich.
// Genutzt vom Viewport (renderViewportO) und vom JPEG-Export.
function zeichneRasterOverlay(ctx, x0, y0, vonRow, bisRow, breite) {
  const aktiv = segmente.filter(s => s.grid && s.unten > vonRow && s.oben < bisRow);
  if (!aktiv.length) return;
  const sx = breite / CANVAS_W;
  const farbe = GRID_FARBEN[gridCfg.color] || GRID_FARBEN.white;
  const kontur = gridCfg.color === 'black' ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  const alpha = Math.max(0.1, Math.min(1, gridCfg.alpha / 100));
  const schritt = rasterZeitSchritt();
  const linie = (xa, ya, xb, yb) => {
    ctx.lineWidth = 3; ctx.strokeStyle = kontur;
    ctx.beginPath(); ctx.moveTo(xa, ya); ctx.lineTo(xb, yb); ctx.stroke();
    ctx.lineWidth = 1; ctx.strokeStyle = farbe;
    ctx.beginPath(); ctx.moveTo(xa, ya); ctx.lineTo(xb, yb); ctx.stroke();
  };
  ctx.save();
  ctx.font = '10px sans-serif';
  ctx.textBaseline = 'middle';
  for (const s of aktiv) {
    const a = Math.max(s.oben, vonRow), b = Math.min(s.unten, bisRow);
    const ya = y0 + (a - vonRow), yb = y0 + (b - vonRow);
    ctx.globalAlpha = alpha;
    ctx.setLineDash(gridCfg.dash ? [4, 4] : []);
    for (const tick of s.ticks) {
      const x = x0 + Math.round(tick.x * sx) + 0.5;
      linie(x, ya, x, yb);
    }
    if (schritt === null) continue;
    for (const m of rasterZeiten) {
      if (m.y < a || m.y >= b || (m.bucket * 10) % schritt !== 0) continue;
      const y = y0 + (m.y - vonRow) + 0.5;
      ctx.globalAlpha = alpha;
      ctx.setLineDash(gridCfg.dash ? [4, 4] : []);
      linie(x0, y, x0 + breite, y);
      // Uhrzeit voll deckend auf dunklem Kasten, Lage nach gridCfg.timeLabel
      const text = formatClock(m.bucket * 10);
      const tw = ctx.measureText(text).width;
      const tx = gridCfg.timeLabel === 'right' ? x0 + breite - tw - 6
               : gridCfg.timeLabel === 'center' ? x0 + (breite - tw) / 2 : x0 + 6;
      ctx.globalAlpha = 1;
      ctx.fillStyle = 'rgba(0,0,0,0.75)';
      ctx.fillRect(tx - 3, y - 7, tw + 6, 14);
      ctx.fillStyle = gridCfg.color === 'black' ? '#ffffff' : farbe;
      ctx.fillText(text, tx, y);
    }
  }
  ctx.restore();
}

// setzt den vollständigen aktuellen D-SCAN-Parametersatz zusammen (genau die
// vier laut Aufgabenstellung geforderten Felder) - wird bei jeder der vier
// relevanten Parameteränderungen neu zusammengestellt und als Text in
// pendingHeader abgelegt, siehe applyRangeBtn/speedSelect/bwZoomBtn/dscanBwSelect
// weiter unten.
// T-1411: Der Bereich der Tafel kommt NICHT mehr aus den Eingabefeldern.
// Bei aktivem BW-ZOOM rechnet das Geraet den Bereich selbst aus (schmaler
// Ausschnitt um den Marker) - die Felder zeigen weiterhin 88/108 und die
// Tafel behauptete dann einen Bereich, der weder zur Achse noch zu den Daten
// passt. Ohne Argumente gilt jetzt der zuletzt gebrannte Segmentbereich;
// applyRangeBtn und der Initial-Header uebergeben ihre bekannten Werte, und
// wo der neue Bereich erst vom Geraet kommt, setzt ihn das Brennen ein
// (rangeFromFrame, siehe SSE-Handler).
function composeParameterAnnotation(startHzArg, stopHzArg) {
  const startHz = startHzArg != null ? startHzArg : segmentStartHz;
  const stopHz = stopHzArg != null ? stopHzArg : segmentStopHz;
  const speed = dscanSpeeds[dscanSpeedIndex];
  const bwHz = parseInt(el('dscanBwSelect').value);
  // NEU: Parameter-Tafel als Liste einzelner Teile (statt eines Strings). key
  // dient nur dem Vergleich mit dem zuletzt geschriebenen Banner, text ist die
  // Anzeige. Geänderte Teile werden in paintAnnotationBand fett/gelb gezeichnet.
  // T-717: Verstaerkung gehoert in die Tafel - ein Pegelsprung im Wasserfall
  // laesst sich sonst spaeter nicht mehr zuordnen. Quelle sind die Bedien-
  // elemente des D-SCAN-Panels; paintGainUi haelt sie mit dem Zoom-Paar und
  // dem Geraetestand synchron.
  const gainMode = el('dscanGainMode').value;
  const parts = [
    { key: 'range',  text: `Range ${(startHz / 1e6).toFixed(3)}–${(stopHz / 1e6).toFixed(3)} MHz` },
    { key: 'bw',     text: `BW ${formatKHz(bwHz)}` },
    { key: 'speed',  text: `Speed ${speed}` },
    { key: 'bwzoom', text: `BW-ZOOM ${dscanBwZoom ? 'on' : 'off'}` },
    { key: 'gain',   text: gainMode === 'MGC'
                       ? `MGC ${el('dscanGainMgc').value} dBµV` : 'AGC' },
  ];
  // Nur bei MTIM relevant - bei anderen Speeds bewusst KEIN Teil (statt
  // "MTIME -"), damit die Tafel kompakt bleibt. key bleibt stabil 'mtime',
  // damit der Diff-Vergleich (fett/gelb) beim Wechsel greift.
  if (speed === 'MTIM') {
    const mtimeVal = el('mtimeInput').value.trim();
    parts.push({ key: 'mtime', text: mtimeVal === '' ? 'MTIME DEFAULT' : `MTIME ${parseFloat(mtimeVal)} ms` });
  }
  return parts;
}

// Kosmetik: leert den kompletten Historienpuffer der D-SCAN-Übersicht und
// beginnt sofort mit einem frischen Start-Header - so sieht der Wasserfall
// nach dem Löschen exakt so aus wie nach dem allerersten Sweep: oben der
// Frequenz-Achsstreifen, darunter die Parameter-Tafel mit den aktuell
// gültigen Werten, links im Zeit-Gutter der Zeitstempel des Löschens.
// Der Header wird bewusst SOFORT gebrannt (nicht erst mit dem nächsten
// fertigen Sweep), damit der Canvas nach dem Klick nicht leer bleibt.
function clearWaterfallHistory() {
  if (mode !== 'overview') return;   // Button existiert nur in der Übersicht

  // Pixelpuffer und alle daran hängenden Zustände zurücksetzen
  ctxHistory.fillStyle = '#000';
  ctxHistory.fillRect(0, 0, historyCanvas.width, historyCanvas.height);
  usedRows = 0;
  scrollRow = 0;
  timeMarks = [];
  lastTimeMarkBucket = null;
  segmente = [];   // T-2010: neu angelegt nach dem Start-Header unten
  rasterZeiten = [];   // T-2011
  lastRasterBucket = null;
  chipRechtecke = [];   // T-2012

  // Steht noch ein Segmentwechsel an (Parameter geändert, aber noch kein
  // Sweep fertig), wird dessen neuer Bereich direkt übernommen - die neue
  // Tafel zeigt ohnehin schon die aktuellen Eingabefelder. Danach ist der
  // anstehende Header erledigt.
  if (pendingHeader !== null) {
    // T-1411: Steht ein Header an, dessen Bereich erst vom Geraet kommt
    // (BW-ZOOM), gilt der zuletzt ANGEZEIGTE Bereich - die Felder taugen
    // dafuer nicht.
    if (pendingHeader.rangeFromFrame && dscanDisplayStartHz !== null) {
      segmentStartHz = dscanDisplayStartHz;
      segmentStopHz = dscanDisplayStopHz;
    } else {
      segmentStartHz = pendingHeader.newStartHz;
      segmentStopHz = pendingHeader.newStopHz;
    }
    pendingHeader = null;
  }

  // Start-Header wie beim Initial-Fall im SSE-Handler: Achsstreifen oben,
  // Parameter-Tafel darunter, keine Vorher-Achse. lastBannerParts vorher
  // auf null, damit kein Parameter gelb als "geändert" erscheint.
  lastBannerParts = null;
  shiftHistory(AXIS_PX + BAND_PX);
  paintFreqAxisStrip(0, segmentStartHz, segmentStopHz);
  paintAnnotationBand(composeParameterAnnotation(), AXIS_PX);
  segmentNeu(segmentStartHz, segmentStopHz, el('gridToggle').checked);   // T-2010
  // Der nächste fertige Sweep darf keinen zweiten Initial-Header erzeugen.
  firstHeaderDone = true;

  // Zeitstempel des Löschens: Zeitmarke am oberen Rand des Headers, wird
  // von drawTimeOverlay() als HH:MM:SS links im Zeit-Gutter gezeichnet und
  // wandert danach wie jede andere Marke mit dem Puffer mit. Bucket merken,
  // damit der nächste Sweep nicht sofort eine zweite Marke setzt.
  const nowS = Date.now() / 1000;
  timeMarks.push({ y: 0, label: formatClock(nowS) });
  lastTimeMarkBucket = Math.floor(nowS / 30);

  updateSpacerHeight();
  wfScrollO.scrollTop = 0;
  renderViewportO();
}

// Punkt 4: exportiert die KOMPLETTE gehaltene Historie (nicht nur den
// sichtbaren Viewport) als JPG - Zeit-Gutter, Wasserfall-Pixelpuffer (inkl.
// eingebrannter Parameter-Tafeln), 30s-Zeitlinien/-Labels, Frequenzachse und
// eine Kopfzeile mit dem aktuellen Bereich.
function formatDateStampForFilename(d) {
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_`
       + `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function exportWaterfallJpg() {
  if (mode !== 'overview') {
    alert('Waterfall export is only possible in the overview (D-SCAN).');
    return;
  }
  if (usedRows === 0) {
    alert('No waterfall data to export yet.');
    return;
  }
  const gutterW = timeO.width;
  const wfW = historyCanvas.width;
  const rows = usedRows;
  const headerH = 20;
  const axisH = axisO.height;

  const exportCanvas = document.createElement('canvas');
  exportCanvas.width = gutterW + wfW;
  exportCanvas.height = headerH + rows + axisH;
  const ctx = exportCanvas.getContext('2d');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, exportCanvas.width, exportCanvas.height);

  // Kopfzeile mit aktuellem Bereich
  ctx.fillStyle = '#8fd';
  ctx.font = '12px sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  const headerText = (dscanDisplayStartHz !== null)
    ? `MoniScope D-SCAN waterfall  ${(dscanDisplayStartHz / 1e6).toFixed(3)}-${(dscanDisplayStopHz / 1e6).toFixed(3)} MHz`
    : 'MoniScope D-SCAN waterfall';
  ctx.fillText(headerText, 4, headerH / 2);

  // kompletter Wasserfall-Pixelpuffer (nur der bisher genutzte Teil)
  ctx.drawImage(historyCanvas, 0, 0, wfW, rows, gutterW, headerH, wfW, rows);
  // T-2011: Raster der Segmente mit grid, wie im Viewport
  zeichneRasterOverlay(ctx, gutterW, headerH, 0, rows, wfW);

  // 30s-Zeitlinien + Labels für die gesamte exportierte Historie einbrennen
  ctx.strokeStyle = 'rgba(255,255,255,0.35)';
  ctx.fillStyle = '#aaa';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  for (const mark of timeMarks) {
    if (mark.y >= rows) continue;
    const y = headerH + mark.y;
    ctx.beginPath();
    ctx.moveTo(gutterW, y + 0.5);
    ctx.lineTo(gutterW + wfW, y + 0.5);
    ctx.stroke();
    ctx.fillText(mark.label, 2, y);
  }

  // Frequenzachse unten (bestehenden Helfer drawAxis wiederverwenden)
  if (dscanDisplayStartHz !== null) {
    const axisTmp = document.createElement('canvas');
    axisTmp.width = wfW;
    axisTmp.height = axisH;
    drawAxis(axisTmp.getContext('2d'), axisTmp, dscanDisplayStartHz, dscanDisplayStopHz);
    ctx.drawImage(axisTmp, gutterW, headerH + rows);
  }

  const a = document.createElement('a');
  a.href = exportCanvas.toDataURL('image/jpeg', 0.92);
  a.download = `MoniScope_waterfall_${formatDateStampForFilename(new Date())}.jpg`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// Kleinster und groesster gesetzter Wert eines Pegel-Arrays.
//
// WARUM KEINE SPREAD-SCHREIBWEISE: Math.min(...array) uebergibt jedes Element
// als eigenes Funktionsargument. Ab etwa 122 000 Elementen sprengt das in
// Chrome den Argument-Stack ("RangeError: Maximum call stack size exceeded").
// Ein D-SCAN mit 15-kHz-Raster erreicht das ab rund 1,85 GHz Spanne -
// 130-2400 MHz liefert 151 334 Punkte. Der Fehler flog bis in
// evtSource.onmessage hoch und beendete dort die Frame-Verarbeitung: der
// Wasserfall lief weiter, das Spektrum blieb auf dem letzten funktionierenden
// Bereich stehen und die Auto-Farbskala aktualisierte nicht mehr.
// Die Schleife hat konstanten Stackbedarf und ist bei dieser Groesse auch
// schneller als der Spread.
// null/undefined werden uebersprungen (noch nie gesetzte Punkte vor dem
// ersten vollstaendigen Sweep). Ergebnis bei ausschliesslich leeren Werten:
// null - der Aufrufer entscheidet dann selbst.
function minMaxDefined(values) {
  let lo = Infinity, hi = -Infinity, anzahl = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === null || v === undefined) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    anzahl++;
  }
  return anzahl === 0 ? null : { lo, hi, anzahl };
}

function currentScaleBounds(levels, view) {
  const mm = minMaxDefined(levels);
  // T-701: Zustand der ANGEFRAGTEN Ansicht (nicht zwingend die gerade aktive) -
  // fuer die aktive Ansicht sind die Globals massgeblich (dort landen auch
  // Regler-Aenderungen sofort), fuer die andere der gemerkte scaleState.
  const zustand = (view === activeScaleView)
    ? { min: scaleMin, max: scaleMax, auto: autoScale }
    : scaleState[view];
  let bounds;
  if (mm === null) {
    bounds = [zustand.min, zustand.max];
  } else if (zustand.auto) {
    const lo = mm.lo - 3;
    const hi = mm.hi + 3;
    scaleState[view].autoBounds = [lo, hi];   // fuer Uebernahme beim Umschalten merken
    if (view === activeScaleView) lastAutoBounds = [lo, hi];
    bounds = [lo, hi];
  } else {
    bounds = [zustand.min, zustand.max];
  }
  // GUI-Redesign (b): Farbskala folgt den WIRKSAMEN Grenzen (je Frame) - nur
  // fuer die AKTIVE Ansicht zeichnen, sonst wuerden im Hintergrund
  // weiterlaufende Frames der anderen Ansicht deren Farbskala ueberschreiben.
  if (view === activeScaleView) drawColorbar(bounds[0], bounds[1]);
  return bounds;
}

// ---------------------------------------------------------------------------
// GUI-Redesign (b): Farbskala rechts neben dem Wasserfall. Zeichnet in alle
// Canvas mit class="colorbar" (Uebersicht jetzt, Zoom in Schritt c) einen
// 14px-Balken aus levelToColor() von hi (oben) bis lo (unten) plus fuenf
// Ticks. levelToColor liefert bereits einen fertigen CSS-Farbstring, der
// direkt als fillStyle taugt - kein Zerlegen in r/g/b noetig.
// Zeichnet nur neu, wenn sich die gerundeten Grenzen geaendert haben (im
// Auto-Modus springen sie je Frame leicht - ohne Cache wuerde es flackern
// und unnoetig Rechenzeit kosten).
// ---------------------------------------------------------------------------
function drawColorbar(lo, hi) {
  const loR = Math.round(lo), hiR = Math.round(hi);
  if (colorbarLast && colorbarLast[0] === loR && colorbarLast[1] === hiR) return;
  colorbarLast = [loR, hiR];
  document.querySelectorAll('canvas.colorbar').forEach(cv => {
    const ctx = cv.getContext('2d');
    const W = cv.width, H = cv.height;
    const barX = 2, barW = 14;
    ctx.clearRect(0, 0, W, H);
    // Balken: Zeile y entspricht Pegel hi - (y/H)*(hi-lo)
    for (let y = 0; y < H; y++) {
      const v = hiR - (y / (H - 1)) * (hiR - loR);
      ctx.fillStyle = levelToColor(v, loR, hiR);
      ctx.fillRect(barX, y, barW, 1);
    }
    // T-1407a: Rahmen und Beschriftung liegen NEBEN der schwarzen Flaeche (der
    // Colorbar-Canvas ist transparent) - feste Grauwerte waeren im hellen Design
    // fast unsichtbar. Farben deshalb zur Zeichenzeit aus den Tokens lesen.
    const cs = getComputedStyle(document.body);
    const tokRahmen = (cs.getPropertyValue('--border-strong') || '#555').trim();
    const tokText = (cs.getPropertyValue('--fg') || '#bbb').trim();
    ctx.strokeStyle = tokRahmen;
    ctx.strokeRect(barX + 0.5, 0.5, barW, H - 1);
    // Ticks: 5 Stuetzstellen, Text rechts vom Balken
    ctx.fillStyle = tokText;
    ctx.font = '11px sans-serif';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) {
      const t = i / 4;
      const y = Math.round(t * (H - 1));
      const v = Math.round(hiR - t * (hiR - loR));
      ctx.fillRect(barX + barW, y, 4, 1);   // Tick in Textfarbe (fillStyle oben)
      const ty = i === 0 ? y + 6 : (i === 4 ? y - 6 : y);
      ctx.fillText(String(v), barX + barW + 7, ty);
    }
  });
}
// Erstzeichnung beim Laden uebernimmt seit T-701 loadScaleState('o') am
// Skriptanfang (colorbarLast wurde dafuer nach oben verlegt). Ein weiterer
// Aufruf hier wuerde bei aus localStorage geladenem Auto-Zustand die dort
// bereits gezeichneten wirksamen autoBounds wieder ueberschreiben.

// T-1407a: Beim Umschalten Hell/Dunkel (Ereignis aus ui_theme.THEME_HEAD_JS)
// muss die Farbskala neu gezeichnet werden - sonst behielte sie Rahmen und
// Beschriftung des alten Themes. colorbarLast zuruecksetzen, weil drawColorbar
// bei unveraenderten Grenzen sonst sofort zurueckkehrt (Flacker-Schutz).
document.addEventListener('themechange', () => {
  const grenzen = colorbarLast;
  colorbarLast = null;
  if (grenzen) drawColorbar(grenzen[0], grenzen[1]);
});

// formatiert eine Frequenz in kHz mit sinnvoller Nachkommastellenzahl:
// grobe Übersicht (viele kHz) ohne Dezimalstelle, feiner Zoom mit einer.
function formatKHz(freqHz) {
  const khz = freqHz / 1000;
  const decimals = khz >= 10000 ? 0 : 1;
  return khz.toFixed(decimals) + ' kHz';
}

// ---------------------------------------------------------------------------
// Streifen-Canvases DIESER Seite. Host-Konfiguration, steht bewusst VOR dem
// geteilten Block - der Block selbst kennt keine Canvas-IDs.
const KNOWN_STRIPS = ['known-o', 'known-z'];
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

// Frequenzachse unter dem Spektrum (axis-o / axis-z).
// (d): Bis Schritt (c) teilte diese Achse den Bereich in 7 GLEICHE Abschnitte
// und beschriftete in kHz - dabei entstanden krumme Werte (130000 / 508333 /
// 886667 ... kHz) und bei GHz-Spannen unlesbar lange Zahlen. Jetzt dieselben
// Nice-Ticks wie die gebrannten Achsstreifen im Historienpuffer
// (niceStepFor/computeNiceTicks) und dieselbe Beschriftung
// (formatNiceTickLabel, schaltet ab 1 MHz auf MHz). Die exakten
// Bereichsgrenzen stehen weiterhin gedimmt an den Raendern; Ticks, deren
// Label mit einem Randlabel kollidieren wuerde, werden unterdrueckt - seit
// T-2007 mit der gemessenen Textbreite (tickLabelFrei) statt eines festen
// Randabstands, der fuer lange Randlabels ("142.825 MHz") zu knapp war.
function drawAxis(ctx, canvas, freqStartHz, freqStopHz) {
  const w = canvas.width, h = canvas.height;
  const span = freqStopHz - freqStartHz;
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h);
  ctx.font = '10px sans-serif';
  if (!(span > 0)) return;
  // Bereichsgrenzen (exakt, gedimmt) an den Raendern
  const startLabel = formatNiceTickLabel(freqStartHz);
  const stopLabel = formatNiceTickLabel(freqStopHz);
  ctx.strokeStyle = '#555'; ctx.fillStyle = '#777';
  ctx.textAlign = 'left';  ctx.fillText(startLabel, 2, h - 3);
  ctx.textAlign = 'right'; ctx.fillText(stopLabel, w - 2, h - 3);
  // runde Ticks dazwischen
  ctx.fillStyle = '#aaa'; ctx.textAlign = 'center';
  const step = niceStepFor(span / 10);
  for (let f = Math.ceil(freqStartHz / step) * step; f <= freqStopHz + 1e-6; f += step) {
    const freqHz = Math.round(f);
    const x = ((freqHz - freqStartHz) / span) * w;
    const label = formatNiceTickLabel(freqHz);
    if (!tickLabelFrei(ctx, x, label, w, startLabel, stopLabel)) continue;
    ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, 5); ctx.stroke();
    ctx.fillText(label, x, h - 3);
  }
}

function freqFromXOverview(frame, xFrac) {
  return frame.start_hz + xFrac * (frame.stop_hz - frame.start_hz);
}

// liefert den aktuell ANGEZEIGTEN Übersichts-Bereich (mitgeführt in
// dscanDisplayStartHz/-StopHz, siehe dort) statt latestO, das nur bei einem
// kompletten Sweep aktualisiert wird - sonst zeigen Tooltip/Drag während
// eines laufenden Sweeps noch den vorherigen Bereich. Fallback auf latestO,
// solange noch kein dscan_partial/dscan-Frame den Anzeigebereich gesetzt hat.
function currentOverviewFrame() {
  if (dscanDisplayStartHz === null || dscanDisplayLevels.length === 0) {
    return latestO;
  }
  return { start_hz: dscanDisplayStartHz, stop_hz: dscanDisplayStopHz, levels: dscanDisplayLevels };
}

function freqFromXZoom(frame, xFrac) {
  return frame.center_hz - frame.span_hz / 2 + xFrac * frame.span_hz;
}

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

function attachTooltip(canvas, getFrame, freqFromX) {
  canvas.addEventListener('mousemove', e => {
    const frame = getFrame();
    if (!frame) return;
    const rect = canvas.getBoundingClientRect();
    const xFrac = (e.clientX - rect.left) / rect.width;
    const n = frame.levels.length;
    const idx = Math.max(0, Math.min(n - 1, Math.floor(xFrac * n)));
    const freqHz = freqFromX(frame, xFrac);
    const tip = el('tooltip');
    const lvl = frame.levels[idx];
    const lvlText = (lvl === null || lvl === undefined) ? '--' : lvl.toFixed(1);
    tip.textContent = `${(freqHz/1e6).toFixed(4)} MHz: ${lvlText} dBµV`;
    tip.style.display = 'block';
    placeTip(tip, e.clientX, e.clientY);
  });
  canvas.addEventListener('mouseleave', () => el('tooltip').style.display = 'none');
}
attachTooltip(specO, currentOverviewFrame, freqFromXOverview);
attachTooltip(wfO, currentOverviewFrame, freqFromXOverview);
attachTooltip(specZ, () => latestZ, freqFromXZoom);
attachTooltip(wfZ, () => latestZ, freqFromXZoom);

// -- Bereichsauswahl (Drag), Schwenken (Zoom-Pan) und Zoom-Klick, vereint in
// einer Funktion -------------------------------------------------------
//
// Ein einfacher Klick (keine nennenswerte Mausbewegung) verhält sich wie
// bisher: Übersicht -> IFPAN-Zoom auf die angeklickte Frequenz, bzw. im
// Zoom eine neue Mittenfrequenz (onClick-Callback, unverändert startZoom).
//
// Ein Ziehen (Drag, Mausbewegung über DRAG_THRESHOLD_PX) markiert in der
// D-SCAN-Übersicht einen Frequenzbereich und öffnet - NUR bei aktivem SDR-
// Ergänzungsmodus (sdrToggle) - den SDR-Wasserfall mit diesem Bereich (siehe
// openSdrWaterfall). Ohne SDR-Modus ist ein Ziehen in der Übersicht wirkungslos
// (auch kein Klick-Zoom beim Loslassen). Im IFPAN-Zoom (panConfig gesetzt)
// schwenkt Ziehen immer die Mittenfrequenz (SDR#/GQRX-artig). Die frühere
// Übergabe eines Bereichs an die Frequenzblock-Verwaltung (block_admin.py
// /new?start=..&stop=..) ist 2026-08 ersatzlos entfallen.
const DRAG_THRESHOLD_PX = 4;

// oeffnet den separat laufenden SDR-Wasserfall (sdr_waterfall.py) mit dem
// gezogenen Bereich als Mitte/Spanne vorausgefuellt - Gegenstueck zu
// einziger Abnehmer der Ziehauswahl in der Uebersicht (siehe attachSelection).
function openSdrWaterfall(freqLowHz, freqHighHz) {
  const center = (freqLowHz + freqHighHz) / 2;
  const span = freqHighHz - freqLowHz;
  const url = `http://${location.hostname}:${SDR_WATERFALL_PORT}/?device=${sdrMode.device}`
    + `&center=${Math.round(center)}&span=${Math.round(span)}`;
  // Bestehendes SDR-Wasserfall-Fenster wiederverwenden, falls noch offen:
  // ueber die gespeicherte Referenz (zuverlaessiger als die Namens-
  // Wiederverwendung von window.open, die bei Popups/Cross-Origin oft ein
  // neues Fenster oeffnet). Sonst ein neues eigenstaendiges Fenster oeffnen.
  if (sdrWin && !sdrWin.closed) {
    sdrWin.location.href = url;   // auf neuen Bereich navigieren (SDR laedt neu)
    sdrWin.focus();
  } else {
    sdrWin = window.open(url, 'sdr_waterfall', 'popup,width=1280,height=820');
  }
}

// Toggle "Select SDR" (T-707, vormals "Ergaenze mit SDR-Wasserfall"): laedt beim Aktivieren die Geraeteliste
// vom SDR-Dienst (Port 8030) und befuellt sdrDeviceSelect; nicht verfuegbare
// Geraete bleiben in der Liste sichtbar, aber disabled + rot markiert. Ist der
// SDR-Dienst selbst nicht erreichbar, bleibt der Toggle aus und ein sichtbarer
// Fehler erscheint - der Rest der Uebersicht bleibt dabei unveraendert nutzbar.
el('sdrToggle').onchange = async e => {
  const enabling = e.target.checked;
  el('sdrError').style.display = 'none';
  if (!enabling) {
    sdrMode.active = false;
    sdrMode.device = null;
    el('sdrDeviceSelect').style.display = 'none';
    return;
  }
  e.target.checked = false;   // erst nach erfolgreichem Laden der Liste aktivieren
  try {
    const res = await fetch(`http://${location.hostname}:${SDR_WATERFALL_PORT}/api/sdr/devices`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const devices = await res.json();
    const select = el('sdrDeviceSelect');
    select.innerHTML = '';
    devices.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.key;
      opt.textContent = d.label + (d.available ? '' : ' (not available)');
      opt.disabled = !d.available;
      if (!d.available) opt.classList.add('err');
      select.appendChild(opt);
    });
    const firstAvailable = devices.find(d => d.available);
    if (firstAvailable) select.value = firstAvailable.key;
    select.style.display = '';
    e.target.checked = true;
    sdrMode.active = true;
    sdrMode.device = select.value || null;
  } catch (err) {
    el('sdrError').textContent = `SDR service (port ${SDR_WATERFALL_PORT}) not reachable`;
    el('sdrError').style.display = 'block';
    sdrMode.active = false;
    sdrMode.device = null;
  }
};
el('sdrDeviceSelect').onchange = e => { sdrMode.device = e.target.value || null; };

function attachSelection(canvas, overlay, getFrame, freqFromX, onClick, markCb, panConfig) {
  let dragging = false;
  let isDrag = false;
  let startClientX = 0;
  let startXFrac = 0;
  // setzt die Schwenk-Vorschau (CSS-Transform) und den "grabbing"-Cursor
  // zurück - bei jedem Loslassen/Abbrechen aufgerufen, no-op ohne panConfig.
  function resetPanPreview() {
    if (!panConfig) return;
    panConfig.previewEls.forEach(el => { el.style.transform = ''; });
    canvas.style.cursor = '';
  }

  canvas.addEventListener('mousedown', e => {
    dragging = true;
    isDrag = false;
    startClientX = e.clientX;
    const rect = canvas.getBoundingClientRect();
    startXFrac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    if (panConfig) canvas.style.cursor = 'grabbing';
  });

  canvas.addEventListener('mousemove', e => {
    if (!dragging) return;
    if (Math.abs(e.clientX - startClientX) < DRAG_THRESHOLD_PX) return;
    isDrag = true;

    if (panConfig) {
      // Schwenken: rein visuelle Vorschau, specZ/wfZ (previewEls) gemeinsam
      // per CSS-Transform mitschieben - KEIN SCPI/Retune während der
      // Bewegung, das passiert erst einmalig in finishDrag beim Loslassen.
      const dxPx = e.clientX - startClientX;
      panConfig.previewEls.forEach(el => { el.style.transform = `translateX(${dxPx}px)`; });
      return;
    }

    // Bereichsauswahl nur in der Übersicht und nur bei aktivem SDR-Modus -
    // sonst gibt es keinen Abnehmer, also auch keine Markierung. isDrag ist
    // bereits gesetzt, damit das Loslassen nicht als Klick (Zoom) zählt.
    if (!sdrMode.active || !sdrMode.device) return;
    const rect = canvas.getBoundingClientRect();
    const curXFrac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const x1 = Math.min(startXFrac, curXFrac) * rect.width;
    const x2 = Math.max(startXFrac, curXFrac) * rect.width;
    overlay.style.display = 'block';
    overlay.style.left = x1 + 'px';
    overlay.style.width = (x2 - x1) + 'px';
    // GUI-Redesign (b): markierten Bereich als Text am unteren Rahmenrand
    // (CSS .sel-overlay::after liest data-label). Nur Anzeige - die
    // massgebliche Berechnung fuer die Uebergabe bleibt in finishDrag.
    const frameNow = getFrame();
    if (frameNow) {
      const fa = freqFromX(frameNow, startXFrac), fb = freqFromX(frameNow, curXFrac);
      overlay.dataset.label = `${(Math.min(fa, fb) / 1e6).toFixed(3)} – ${(Math.max(fa, fb) / 1e6).toFixed(3)} MHz`;
    } else {
      overlay.dataset.label = '';
    }
  });

  function finishDrag(e) {
    if (!dragging) return;
    dragging = false;
    overlay.style.display = 'none';
    const frame = getFrame();
    if (!frame) { resetPanPreview(); return; }

    const rect = canvas.getBoundingClientRect();
    const curXFrac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));

    if (isDrag && panConfig) {
      // Schwenk beendet - jetzt genau EINMAL neu abstimmen. Nach rechts
      // ziehen lässt den Inhalt dem Cursor nach rechts folgen -> die
      // Mittenfrequenz SINKT (Google-Maps-/SDR-Logik), daher das Minus.
      const dxFrac = (e.clientX - startClientX) / rect.width;
      const neueMitteHz = Math.round(frame.center_hz - dxFrac * frame.span_hz);
      resetPanPreview();
      panConfig.onPan(neueMitteHz);   // == startZoom, leert die Canvas ohnehin
      return;
    }

    if (!isDrag) {
      // normaler Klick - bisheriges Zoom-/Neuzentrierungs-Verhalten
      const freqHz = Math.round(freqFromX(frame, curXFrac));
      // NEU: optionalen Klick-Marker setzen (nur in der Übersicht übergeben;
      // im Zoom ist markCb undefined). Vertikale Klickposition relativ zum
      // angeklickten Canvas aus dem mouseup-Event ableiten.
      if (markCb) {
        const curYFrac = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
        markCb(curXFrac, curYFrac, freqHz);
      }
      onClick(freqHz);
      return;
    }

    // Drag beendet - nur der Übersichts-Drag (kein panConfig) bei aktivem
    // SDR-Modus hat eine Wirkung: SDR-Wasserfall auf den Bereich öffnen.
    if (panConfig || !sdrMode.active || !sdrMode.device) return;
    const freqA = freqFromX(frame, startXFrac);
    const freqB = freqFromX(frame, curXFrac);
    const freqLow = Math.min(freqA, freqB);
    const freqHigh = Math.max(freqA, freqB);
    if (freqHigh - freqLow < 1) return;   // keine nennenswerte Ausdehnung
    openSdrWaterfall(freqLow, freqHigh);
  }

  canvas.addEventListener('mouseup', finishDrag);
  canvas.addEventListener('mouseleave', () => {
    // Auswahl bzw. Schwenk abbrechen, wenn die Maus den Canvas während des
    // Ziehens verlässt, statt eine unvollständige/verzerrte Auswahl zu
    // übernehmen bzw. ohne Retune (Schwenk-Vorschau wird nur zurückgesetzt).
    if (dragging) {
      dragging = false;
      overlay.style.display = 'none';
      resetPanPreview();
    }
  });
}

// Klick im Spektrum-Streifen (spec-o, 120px) hat keine sinnvolle Wasserfall-
// Zeile -> Marker an den obersten sichtbaren Rand (yFrac=null).
attachSelection(specO, overlayO, currentOverviewFrame, freqFromXOverview, startZoom,
                (xFrac, yFrac, freqHz) => placeClickMarker(xFrac, null, freqHz));
// Klick im Wasserfall (wf-o) -> Marker an die tatsächlich angeklickte Zeile.
attachSelection(wfO, overlayO, currentOverviewFrame, freqFromXOverview, startZoom,
                (xFrac, yFrac, freqHz) => placeClickMarker(xFrac, yFrac, freqHz));
// dieselbe Logik auch in der Zoom-Ansicht: ein Klick setzt eine neue
// Mittenfrequenz (IFPAN wird dorthin neu zentriert, über startZoom - siehe
// dort). Ziehen SCHWENKT die Mittenfrequenz (panConfig, siehe attachSelection).
// panConfig: onPan == startZoom (leert specZ/wfZ ohnehin selbst), previewEls
// sind beide Zoom-Canvas gemeinsam - beim Schwenken visuell im Gleichlauf
// verschoben, siehe attachSelection.
const zoomPanConfig = { onPan: startZoom, previewEls: [specZ, wfZ] };
attachSelection(specZ, overlayZ, () => latestZ, freqFromXZoom, startZoom,
                undefined, zoomPanConfig);
attachSelection(wfZ, overlayZ, () => latestZ, freqFromXZoom, startZoom,
                undefined, zoomPanConfig);

// Uebersetzt eine 423-Antwort (Empfaenger durch einen Long-Run-Job gesperrt,
// siehe waterfall.py _LOCKED_GET_PATHS/_LOCKED_POST_PATHS) in eine
// verstaendliche Meldung in #meta-o. Reicht die Response unveraendert durch
// (Promise-Chain bleibt nutzbar). Die gesperrten Bedienelemente selbst
// zeigen den Zustand bereits per disabled/rec-lock (siehe setLongrunLock) -
// diese Funktion faengt nur den Sonderfall ab, dass trotzdem ein Request
// durchkommt (z. B. ein knapp gleichzeitiger Klick).
function reportIfLocked(response) {
  if (response.status === 423) {
    el('meta-o').textContent = 'Receiver locked - long-run job running';
  } else if (response.status === 400) {
    // Server lehnt einen Parameter VOR dem Geraet ab (z. B. Spanne unter
    // DSCAN_MIN_SPAN_HZ) und liefert den Grund als {"error": ...}. Ohne
    // diese Anzeige stuende in #meta-o weiter "Setting new range ...",
    // waehrend der alte Sweep unveraendert weiterlaeuft.
    // Wie bei den Server-Hinweisen kurz festhalten: der naechste Sweep-Frame
    // kommt sonst binnen Millisekunden und ueberschreibt die Begruendung,
    // bevor sie jemand lesen kann (bei schmalen Bereichen ~50 ms gemessen).
    const zeigen = t => { el('meta-o').textContent = t; noticeHoldUntil = Date.now() + 15000; };
    response.clone().json().then(j => {
      zeigen('Rejected: ' + (j && j.error ? j.error : 'invalid input'));
    }).catch(() => zeigen('Rejected: invalid input'));
  }
  return response;
}

// T-721: einheitliche Darstellung der Mittenfrequenz in der Kopfleiste -
// immer sechs Nachkommastellen (Hz-genau, damit Feinabstimm-Schritte
// sichtbar bleiben) und Dreiergruppierung der Nachkommastellen: die erste
// Gruppe sind die kHz-, die zweite die Hz-Stellen. Trennzeichen ist ein
// normales Leerzeichen; bei font-variant-numeric:tabular-nums hat es genau
// Ziffernbreite, das Feld bleibt also ruhig. Vorkommastellen bleiben
// ungruppiert (hoechstens vier Stellen bis 3 GHz).
function formatCenterMHz(hz) {
  const s = (hz / 1e6).toFixed(6);
  const punkt = s.indexOf('.');
  const ganz = s.slice(0, punkt);
  const frac = s.slice(punkt + 1);
  return ganz + '.' + frac.slice(0, 3) + ' ' + frac.slice(3) + ' MHz';
}

// T-1813d1 (BUG-0005): eine einzige Stelle entscheidet, ob die Mittenfrequenz
// veraendert werden darf. Vorher pruefte das nur das Mausrad (tuneWheel) und
// die Direkteingabe (freqInputGesperrt) - Klick und Schwenk im Zoom liefen
// ueber attachSelection/startZoom daran vorbei und verstimmten den Empfaenger
// mitten in einer laufenden Aufnahme. Der Server kann das nicht abfangen: eine
// lokale Audio-/Spektrum-Aufnahme kennt nur der Browser.
function abstimmenGesperrt(was) {
  if (longrunLocked) {
    tuneShowNotice(was + ' locked - long-run job running');
    return true;
  }
  if (audioRec.isActive() || spectrumRec.isActive()) {
    tuneShowNotice(was + ' locked - recording running');
    return true;
  }
  return false;
}

function startZoom(freqHz) {
  // T-1813d1: Umstimmen INNERHALB des Zooms ist gesperrt, solange eine Aufnahme
  // laeuft. Der Einstieg AUS der Uebersicht bleibt erlaubt - dort laeuft keine
  // Zoom-Aufnahme. Die Abfrage steht vor der Zuweisung, weil mode danach in
  // jedem Fall 'zoom' ist.
  if (mode === 'zoom' && abstimmenGesperrt('Tuning')) return;
  mode = 'zoom';
  latestZ = null;
  el('overviewView').style.display = 'none';
  el('zoomView').style.display = 'block';
  // (c): Header auf Zoom umschalten (zeigt centerFreqBig, blendet viewSub aus)
  document.body.classList.add('zoom');
  el('viewName').textContent = 'Zoom (IFPAN)';
  switchScaleView('z');   // T-701: eigener Farbskalen-Zustand der Zoom-Ansicht
  resetSMeter();
  el('meta-z').textContent = `Zooming to ${(freqHz/1e6).toFixed(4)} MHz ...`;
  // Mittenfrequenz sofort groß anzeigen, noch bevor das erste ifpan-Frame
  // eintrifft - updateMetaZoom() überschreibt sie danach laufend. T-721:
  // dieselbe Formatierung wie dort (sechs Stellen, gruppiert), sonst
  // springt die Feldbreite beim ersten eintreffenden Frame sichtbar.
  el('centerFreqBig').textContent = formatCenterMHz(freqHz);
  // Spektrum/Wasserfall der Zoom-Ansicht leeren: die Frequenzachse ändert
  // sich mit der neuen Mittenfrequenz, alte Zeilen würden sonst nicht mehr
  // zur neuen Achse passen.
  ctxSpecZ.fillStyle = '#000'; ctxSpecZ.fillRect(0, 0, specZ.width, specZ.height);
  ctxWfZ.fillStyle = '#000'; ctxWfZ.fillRect(0, 0, wfZ.width, wfZ.height);
  fetch(`/api/zoom?freq=${freqHz}`).then(reportIfLocked);
  tuneReset();   // T-708: alter Feinabstimm-Zielwert gilt fuer den neuen Zoom nicht mehr
  monitor.autoStart();   // Nutzergeste liegt vor (Klick) - Audio darf starten
}

// ---- T-708/T-709: Feinabstimmung der IFPAN-Mittenfrequenz ----------------
// Das Mausrad verstimmt die Mittenfrequenz in kleinen Schritten, ohne den
// Zoom neu aufzubauen (Endpoint /api/tune, der Controller sendet inline nur
// FREQ). Schrittweite je Modifier: Rad = 1 Hz, +Shift = 10 Hz,
// +Strg = 100 Hz, +Shift+Strg = 1 kHz. Die urspruengliche Pfeiltasten-
// Steuerung mit Halte-Beschleunigung wurde mit T-709 wieder entfernt
// (nicht gebraucht). Der Client zeigt den Zielwert sofort optimistisch an
// und sendet gedrosselt hoechstens alle TUNE_SEND_MIN_MS den jeweils
// LETZTEN Zielwert (trailing throttle) - so entsteht bei schnellem Radeln
// kein SCPI-Rueckstau im Controller. Waehrend einer laufenden Audio-/
// Spektrum-Aufnahme ist die Feinabstimmung gesperrt - clientseitig
// (tuneWheel) UND serverseitig (409 von /api/tune).
const TUNE_SEND_MIN_MS = 120;
const TUNE_MIN_HZ = 10000, TUNE_MAX_HZ = 3000000000;   // Geraetegrenzen 10 kHz..3 GHz
let tuneTargetHz = null;      // lokal gefuehrter Zielwert (null = Geraetestand gilt)
let tuneHoldUntilMs = 0;      // solange: centerFreqBig nicht vom (alten) Frame ueberschreiben
let tuneSendTimer = null, tuneLastSentHz = null, tuneLastSendMs = 0;
let noticeHoldUntilZ = 0;     // Meldungs-Halte fuer #meta-z (Pendant zu noticeHoldUntil)
// Frame-getriebene Verschiebung der Wasserfall-Historie (siehe ifpan-Handler)
let wfZPrevCenterHz = null, wfZPrevSpanHz = null, wfZShiftAccPx = 0;

function tuneReset() {
  tuneTargetHz = null;
  tuneHoldUntilMs = 0;
  if (tuneSendTimer !== null) { clearTimeout(tuneSendTimer); tuneSendTimer = null; }
}

function tuneShowNotice(text) {
  el('meta-z').textContent = text;
  noticeHoldUntilZ = Date.now() + 5000;
}

function sendTuneNow() {
  tuneSendTimer = null;
  if (tuneTargetHz === null || tuneTargetHz === tuneLastSentHz) return;
  tuneLastSentHz = tuneTargetHz;
  tuneLastSendMs = Date.now();
  fetch(`/api/tune?freq=${tuneTargetHz}`).then(r => {
    if (r.status === 423) {
      tuneShowNotice('Fine tuning locked - long-run job running');
    } else if (r.status === 409 || r.status === 400) {
      r.clone().json().then(j => tuneShowNotice('Rejected: ' + (j && j.error ? j.error : 'invalid input')))
       .catch(() => tuneShowNotice('Rejected: invalid input'));
    }
    // T-709: Abgelehnte oder fehlgeschlagene Anfrage -> Zielwert verwerfen,
    // sonst basieren weitere Rad-Schritte auf einem Phantomwert, den das
    // Geraet nie uebernommen hat, und die Anzeige laeuft beliebig weit von
    // der Realitaet weg (die Frames fuehren centerFreqBig danach wieder).
    if (r.status !== 200) tuneReset();
    return r;
  }).catch(() => tuneReset());
}

function scheduleTuneSend() {
  if (tuneSendTimer !== null) return;   // trailing: laufender Timer nimmt den neuen Zielwert mit
  const wait = Math.max(0, TUNE_SEND_MIN_MS - (Date.now() - tuneLastSendMs));
  tuneSendTimer = setTimeout(sendTuneNow, wait);
}

function applyTuneStep(deltaHz) {
  // Bezugswert: laufender Zielwert, sonst die Mittenfrequenz des letzten
  // Frames. Ohne erstes Frame (Zoom baut gerade auf) gibt es keinen
  // verlaesslichen Bezug - Schritt verwerfen.
  const basisHz = (tuneTargetHz !== null) ? tuneTargetHz
                : (latestZ !== null ? latestZ.center_hz : null);
  if (basisHz === null) return;
  tuneTargetHz = Math.max(TUNE_MIN_HZ, Math.min(TUNE_MAX_HZ, Math.round(basisHz + deltaHz)));
  tuneHoldUntilMs = Date.now() + 1500;
  // Optimistische Anzeige, 6 Nachkommastellen (Hz-genau) - sonst waeren
  // 1/10-Hz-Schritte unsichtbar. updateMetaZoom() zieht nach, sobald das
  // Geraet die neue Frequenz in den Frames bestaetigt. Stellenzahl und
  // Gruppierung liefert seit T-721 formatCenterMHz().
  el('centerFreqBig').textContent = formatCenterMHz(tuneTargetHz);
  scheduleTuneSend();
}

function tuneStepBase(ev) {   // 1 / 10 / 100 / 1000 Hz je Modifier
  if (ev.shiftKey && ev.ctrlKey) return 1000;
  return ev.ctrlKey ? 100 : (ev.shiftKey ? 10 : 1);
}

function tuneWheel(ev) {
  if (mode !== 'zoom') return;
  ev.preventDefault();   // Seite nicht scrollen; Strg+Rad darf nicht den Browser-Zoom ausloesen
  if (ev.deltaY === 0) return;
  // T-709: Sperre schon CLIENTSEITIG durchsetzen - sonst folgt die optimistische
  // Anzeige dem Rad, obwohl der Server jede Anfrage abweist, und es entsteht der
  // Eindruck, die Abstimmung liefe weiter (Hardware-Befund T-708).
  // T-1813d1: gemeinsame Pruefung, deckt jetzt auch den Long-Run-Job ab.
  if (abstimmenGesperrt('Fine tuning')) return;
  applyTuneStep((ev.deltaY < 0 ? 1 : -1) * tuneStepBase(ev));   // Rad nach oben = Frequenz hoch
}
specZ.addEventListener('wheel', tuneWheel, { passive: false });
wfZ.addEventListener('wheel', tuneWheel, { passive: false });

// ---- T-722: Direkteingabe der Mittenfrequenz ------------------------------
// Klick auf die grosse Anzeige oder Taste f (auch Enter) blendet an ihrer
// Stelle ein Eingabefeld ein. Enter uebernimmt, Esc und Fokusverlust
// verwerfen. Die Einheit wird bewusst nicht geraten: ohne Endung gilt MHz,
// und das Echo unter dem Feld zeigt waehrend des Tippens den tatsaechlich
// interpretierten Wert - damit ist die Mehrdeutigkeit sichtbar statt
// wegdefiniert. Der uebernommene Wert geht je nach Abstand zwei Wege:
// innerhalb des halben Spans als Feinabstimmung ueber applyTuneStep (die
// Wasserfall-Historie bleibt erhalten und wird von der frame-getriebenen
// Shift-Logik mitgezogen), darueber hinaus als vollstaendiger Zoom-Neuaufbau
// ueber startZoom. Gesperrt ist die Eingabe waehrend eines Long-Run-Jobs und
// waehrend einer Audio-/Spektrum-Aufnahme - BEIDE Wege, nicht nur die
// Feinabstimmung: ein Frequenzsprung mitten in einer Aufnahme entwertet die
// Datei genauso wie eine Verstimmung (Entscheidung OE3GAS 2026-09-06).
// longrunLocked/audioRec/spectrumRec werden erst weiter unten deklariert -
// unkritisch, da diese Funktionen ausschliesslich per Event laufen (gleiches
// Muster wie tuneWheel oben).
function parseFreqInput(text) {
  // Leerzeichen (auch die Gruppierungsluecken der Anzeige) entfallen,
  // Komma gilt wie Punkt. Einheit aus der Endung, sonst MHz.
  let s = String(text).trim().toLowerCase().replace(/\\s+/g, '').replace(/,/g, '.');
  if (s === '') return null;
  let faktor = 1e6;
  const einheiten = [['ghz', 1e9], ['mhz', 1e6], ['khz', 1e3], ['hz', 1],
                     ['g', 1e9], ['m', 1e6], ['k', 1e3]];
  for (const [endung, f] of einheiten) {
    if (s.endsWith(endung)) { faktor = f; s = s.slice(0, -endung.length); break; }
  }
  if (!/^(\\d+(\\.\\d*)?|\\.\\d+)$/.test(s)) return null;
  const hz = Math.round(parseFloat(s) * faktor);
  return Number.isFinite(hz) ? hz : null;
}

function freqInputGesperrt() {
  return abstimmenGesperrt('Frequency input');
}

function updateFreqEcho() {
  const echo = el('freqEcho');
  const roh = el('freqInput').value.trim();
  if (roh === '') {
    echo.textContent = 'without a unit, MHz applies';
    echo.classList.remove('bad');
    return;
  }
  const hz = parseFreqInput(roh);
  if (hz === null) {
    echo.textContent = 'not a valid number';
    echo.classList.add('bad');
    return;
  }
  if (hz < TUNE_MIN_HZ || hz > TUNE_MAX_HZ) {
    echo.textContent = 'outside 10 kHz .. 3 GHz';
    echo.classList.add('bad');
    return;
  }
  echo.textContent = '→ ' + formatCenterMHz(hz);
  echo.classList.remove('bad');
}

// Bezugswert fuer Vorbelegung und Wegentscheidung: laufender Feinabstimm-
// Zielwert, sonst die Mittenfrequenz des letzten Frames.
function freqBezugHz() {
  return (tuneTargetHz !== null) ? tuneTargetHz
       : (latestZ !== null ? latestZ.center_hz : null);
}

function openFreqInput() {
  if (mode !== 'zoom') return;
  if (document.body.classList.contains('freqedit')) return;
  if (freqInputGesperrt()) return;
  const basisHz = freqBezugHz();
  const inp = el('freqInput');
  // Ungruppiert vorbelegen - so laesst sich der Wert ohne Umwege
  // ueberschreiben oder stellenweise korrigieren.
  inp.value = (basisHz !== null) ? (basisHz / 1e6).toFixed(6) : '';
  document.body.classList.add('freqedit');
  updateFreqEcho();
  inp.focus();
  inp.select();
}

// Idempotent: entfernt zuerst die Klasse, damit der blur-Handler beim
// folgenden blur() nicht ein zweites Mal hineinlaeuft.
function closeFreqInput() {
  if (!document.body.classList.contains('freqedit')) return;
  document.body.classList.remove('freqedit');
  el('freqInput').blur();
}

function commitFreqInput() {
  const hz = parseFreqInput(el('freqInput').value);
  if (hz === null || hz < TUNE_MIN_HZ || hz > TUNE_MAX_HZ) {
    // Feld bleibt offen, das Echo benennt den Grund bereits.
    return;
  }
  // Zwischen Oeffnen und Enter kann eine Aufnahme gestartet worden sein.
  if (freqInputGesperrt()) { closeFreqInput(); return; }
  const basisHz = freqBezugHz();
  const spanHz = (latestZ !== null) ? latestZ.span_hz : null;
  closeFreqInput();
  if (basisHz !== null && spanHz !== null && Math.abs(hz - basisHz) <= spanHz / 2) {
    applyTuneStep(hz - basisHz);
  } else {
    startZoom(hz);
  }
}

el('centerFreqBig').addEventListener('click', openFreqInput);
el('freqInput').addEventListener('input', updateFreqEcho);
el('freqInput').addEventListener('blur', closeFreqInput);
el('freqInput').addEventListener('keydown', ev => {
  // stopPropagation haelt Esc vom globalen Handler fern, der sonst
  // zusaetzlich das Navigationsmenue schliessen wuerde.
  if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); commitFreqInput(); }
  else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closeFreqInput(); }
});

// Tastatur-Einstieg. Nur in der Zoom-Ansicht und nur, wenn der Fokus nicht
// ohnehin in einem Bedienelement steht - sonst wuerde ein f im Kurztext-Feld
// oder ein Enter auf einem Knopf die Eingabe aufziehen.
document.addEventListener('keydown', ev => {
  if (mode !== 'zoom') return;
  if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
  const ae = document.activeElement;
  if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'SELECT' || ae.tagName === 'TEXTAREA'
             || ae.tagName === 'BUTTON' || ae.isContentEditable)) return;
  if (ev.key === 'f' || ev.key === 'F' || ev.key === 'Enter') {
    ev.preventDefault();
    openFreqInput();
  }
});

// Kurzes visuelles Feedback: Button blitzt beim Klick hell auf, damit ein
// erfolgter Tastendruck erkennbar ist - auch wenn (bei unveraenderten
// Parametern) sonst nichts passiert.
function flashButton(id) {
  const b = el(id);
  if (!b) return;
  b.classList.add('flash');
  setTimeout(() => b.classList.remove('flash'), 180);
}

// T-703 (Teil C/D): "kein Regler-Kampf" - haelt syncDeviceParams() davon ab,
// ein Bedienelement zu ueberschreiben, das der Nutzer GERADE bedient (Fokus)
// oder in den letzten LOCAL_GUARD_MS lokal geaendert hat. Jeder lokale
// onchange-/oninput-Handler eines betroffenen Elements ruft
// markLocalInteraction(id) auf.
const LOCAL_GUARD_MS = 2000;
const _localInteractionTs = {};
function markLocalInteraction(id) {
  _localInteractionTs[id] = Date.now();
}
function recentlyTouched(id) {
  if (document.activeElement === el(id)) return true;
  return (Date.now() - (_localInteractionTs[id] || 0)) < LOCAL_GUARD_MS;
}

el('applyRangeBtn').onclick = () => {
  flashButton('applyRangeBtn');
  const startHz = Math.round(parseFloat(el('dscanStart').value) * 1e6);
  const stopHz = Math.round(parseFloat(el('dscanStop').value) * 1e6);
  if (!(startHz < stopHz)) {
    alert('Start must be lower than stop.');
    return;
  }
  // Punkt 2: Bereich unveraendert -> nichts tun. Kein neuer pendingHeader,
  // kein /api/range, damit kein ueberfluessiges Parameter-Banner in den
  // Wasserfall gebrannt wird. Der Flash oben signalisiert den erkannten
  // Tastendruck trotzdem. segmentStartHz/segmentStopHz beschreiben den
  // aktuell laufenden Bereich.
  if (startHz === segmentStartHz && stopHz === segmentStopHz) {
    el('meta-o').textContent = `Range unchanged (${el('dscanStart').value}-${el('dscanStop').value} MHz) - nothing to do.`;
    return;
  }
  el('meta-o').textContent = `Setting new range ${el('dscanStart').value}-${el('dscanStop').value} MHz ...`;
  noticeHoldUntil = Date.now() + 3000;   // (c): 3 s stehen lassen, sonst vom naechsten Frame ueberschrieben
  // Punkt C: beim nächsten fertigen Sweep einen Header (Achsstreifen +
  // Parameter-Tafel + Vorher-Achse) einfügen. Der Bereich ändert sich hier
  // tatsächlich - newStart/StopHz sind die neu eingegebenen Werte.
  pendingHeader = { text: composeParameterAnnotation(startHz, stopHz), newStartHz: startHz, newStopHz: stopHz, isInitial: false };
  fetch(`/api/range?start=${startHz}&stop=${stopHz}`).then(reportIfLocked).then(r => {
    if (!r.ok) pendingHeader = null;   // abgelehnt: kein Banner fuer einen Bereich, der nie aktiv wird
  });
  updateMtimeInfo();
};

// Ein Button zykelt durch die vier D-SCAN-Geschwindigkeitsstufen
// (LOW -> NORM -> HIGH -> MTIM -> LOW ...) und wendet die Wahl sofort an.
// MTIM = MTIME PER CHANNEL (Annex J): kein Hardware-Sweep, sondern
// schrittweises Abstimmen wie im F-SCAN mit definierter Messzeit je Kanal
// (MEASURE TIME am Geraet); Sweep-Dauer haengt direkt von MEAS:TIME ab.
const dscanSpeeds = ['LOW', 'NORM', 'HIGH', 'MTIM'];
let dscanSpeedIndex = 1; // Start: NORM, passend zum Server-Default

// Annex E: DEFAULT-Messzeiten in ms je ZF-Bandbreite und Detektor. Gilt nur
// fuer statische Messung (LEVEL, F-SCAN, D-SCAN in Stellung MTIM) - beim
// Hardware-Sweep (LOW/NORM/HIGH) wirkt KEINE Messzeit (Annex J: gleitendes
// Filter, Abtastung alle BW/2).
const MTIME_DEFAULT_MS = {
  150000: { PEAK: 2,   AVG: 3,   FAST: 0.3 },
  120000: { PEAK: 2,   AVG: 3,   FAST: 0.3 },
  50000:  { PEAK: 4,   AVG: 3,   FAST: 0.3 },
  30000:  { PEAK: 5,   AVG: 3,   FAST: 0.3 },
  15000:  { PEAK: 10,  AVG: 3,   FAST: 0.3 },
  9000:   { PEAK: 20,  AVG: 6,   FAST: 0.6 },
  6000:   { PEAK: 30,  AVG: 12,  FAST: 0.6 },
  2400:   { PEAK: 60,  AVG: 25,  FAST: 2.2 },
  1500:   { PEAK: 100, AVG: 50,  FAST: 2.2 },
  600:    { PEAK: 200, AVG: 100, FAST: 9 },
  300:    { PEAK: 400, AVG: 100, FAST: 18 },
  150:    { PEAK: 500, AVG: 200, FAST: 18 },
};

// Detektor vom Geraet (per /api/dscan_config, Fallback PEAK = laengste und
// damit konservativste DEFAULT-Messzeit).
let dscanDetector = 'PEAK';

// Zuletzt gemessene Sweep-Dauer (Sekunden) - fuer die Verweildauer-Anzeige
// bei LOW/NORM/HIGH; wird im SSE-Handler bei fertigen Sweeps aktualisiert.
let lastSweepElapsedS = null;
// T-2006: Punktzahl DESSELBEN Sweeps (frame.total). Die Verweildauer teilt die
// gemessene Dauer durch die tatsaechlich gesweepten Punkte - nicht durch die
// Kanalzahl aus den Eingabefeldern: die beschreibt bei BW-ZOOM den vollen
// Bereich, waehrend das Geraet nur das schmale Zoom-Fenster sweept.
let lastSweepPoints = null;

// Abstimm-/Einschwingzuschlag je Kanal im MTIM-Modus (Sekunden). NICHT im
// Handbuch dokumentiert - grobe Annahme, per Hardware-Test kalibrierbar
// (gemessene Sweep-Dauer / Kanalzahl minus MTIME vergleichen).
const MTIM_TUNE_OVERHEAD_S = 0.005;

function channelCount() {
  const startHz = parseFloat(el('dscanStart').value) * 1e6;
  const stopHz = parseFloat(el('dscanStop').value) * 1e6;
  const bwHz = parseInt(el('dscanBwSelect').value);
  if (!(stopHz > startHz) || !(bwHz > 0)) return null;
  return Math.round((stopHz - startHz) / (bwHz / 2));
}

function effectiveMtimeMs() {
  // null = DEFAULT (leeres Feld) -> Tabellenwert fuer aktuelle BW/Detektor.
  const v = el('mtimeInput').value.trim();
  if (v !== '') return parseFloat(v);
  const bwHz = parseInt(el('dscanBwSelect').value);
  const row = MTIME_DEFAULT_MS[bwHz];
  return row ? row[dscanDetector] : null;
}

function formatDuration(s) {
  if (s < 60) return s.toFixed(1) + ' s';
  if (s < 3600) return Math.floor(s / 60) + ' min ' + Math.round(s % 60) + ' s';
  return (s / 3600).toFixed(1) + ' h';
}

// T-2008: Vorschau des BW-ZOOM-Fensters, schon BEVOR man es einschaltet. Das
// Fenster rechnet das Geraet selbst (FREQ:DSC:RES:AUTO); nachgebildet nach
// Handbuch 3.18.3: Kanalabstand BW/2 und 190 Linien ergeben 95 x BW, mittig um
// den Marker, den der Server auf die Bereichsmitte setzt (ganzzahlig
// abgerundet wie dort). Belegt am Geraet: BW 150 kHz, 120-179.9 MHz ergab
// 142.825-157.075 MHz. Die Info-Box im Spektrum zeigt weiter die Geraetewerte.
// Der Faktor steht bewusst in der Funktion: updateMtimeInfo() ruft sie, und
// eine Modul-const waere vor ihrer Zeile noch nicht initialisiert.
function bwZoomWindow(startHz, stopHz, bwHz) {
  const SPAN_FAKTOR = 95;
  if (!(stopHz > startHz) || !(bwHz > 0)) return null;
  const markerHz = Math.floor((startHz + stopHz) / 2);
  const halbHz = SPAN_FAKTOR * bwHz / 2;
  return { startHz: markerHz - halbHz, stopHz: markerHz + halbHz };
}
function updateBwZoomPreview() {
  const fenster = bwZoomWindow(Math.round(parseFloat(el('dscanStart').value) * 1e6),
                               Math.round(parseFloat(el('dscanStop').value) * 1e6),
                               parseInt(el('dscanBwSelect').value));
  // T-2008b: "(width ...)" in eine eigene Zeile (CSS white-space:pre-line)
  el('bwZoomPreview').textContent = fenster
    ? `Zoom window ≈ ${(fenster.startHz / 1e6).toFixed(3)}-${(fenster.stopHz / 1e6).toFixed(3)} MHz`
      + String.fromCharCode(10)
      + `(width ${((fenster.stopHz - fenster.startHz) / 1e6).toFixed(3)} MHz)`
    : '';
}
// Die Vorschau folgt den Bereichsfeldern schon beim Tippen, nicht erst nach "Apply".
el('dscanStart').addEventListener('input', updateBwZoomPreview);
el('dscanStop').addEventListener('input', updateBwZoomPreview);

// Aktualisiert Messzeit-Info und Sweep-Dauer-Schaetzung - bei jeder
// Aenderung von Speed, MTIME, BW oder Bereich aufzurufen.
function updateMtimeInfo() {
  updateBwZoomPreview();   // T-2008: dieselben Ausloeser (BW, Bereich)
  const speed = dscanSpeeds[dscanSpeedIndex];
  const info = el('mtimeInfo');
  const est = el('sweepEstimate');
  const n = channelCount();

  if (speed !== 'MTIM') {
    // Hardware-Sweep: keine Messzeit, stattdessen empirische Verweildauer
    // je Kanal aus der zuletzt gemessenen Sweep-Dauer.
    if (lastSweepElapsedS != null && lastSweepPoints > 0) {
      // T-2008b: "(measured from sweep)" in eine eigene Zeile (CSS white-space:pre-line)
      info.textContent = `Dwell time/channel ≈ ${(lastSweepElapsedS / lastSweepPoints * 1000).toFixed(2)} ms`
        + String.fromCharCode(10) + '(measured from sweep)';
    } else {
      // T-2006: vor dem ersten fertigen Sweep ein Strich statt einer leeren Zeile.
      info.textContent = 'Dwell time/channel: —';
    }
    est.textContent = '';
    return;
  }

  const mtMs = effectiveMtimeMs();
  const isDefault = el('mtimeInput').value.trim() === '';
  if (mtMs == null) {
    info.textContent = 'Measurement time: DEFAULT';
    est.textContent = '';
    return;
  }
  info.textContent = isDefault
    ? `Measurement time: DEFAULT (≈ ${mtMs} ms at BW ${formatKHz(parseInt(el('dscanBwSelect').value))}, ${dscanDetector})`
    : `Measurement time: ${mtMs} ms`;

  if (!n) { est.textContent = ''; return; }
  const estS = n * (mtMs / 1000 + MTIM_TUNE_OVERHEAD_S);
  est.textContent = `≈ Sweep duration: ${formatDuration(estS)} (${n} ${pluralWort(n, 'channel', 'channels')})`;
  est.className = estS > 600 ? 'err' : (estS > 60 ? 'warn' : 'info');
}

// Schwelle, ab der eine Bestaetigung verlangt wird, bevor MTIM-Parameter
// angewendet werden - kein hartes Limit (lange Sweeps koennen gewollt sein,
// z. B. Impulsstoerer-Messung), nur ein Schutz gegen versehentliche
// Extremwerte, bei denen das UI minutenlang "eingefroren" wirkt.
const MTIM_CONFIRM_THRESHOLD_S = 600;

function confirmLongSweep() {
  const n = channelCount();
  const mtMs = effectiveMtimeMs();
  if (!n || mtMs == null) return true;
  const estS = n * (mtMs / 1000 + MTIM_TUNE_OVERHEAD_S);
  if (estS <= MTIM_CONFIRM_THRESHOLD_S) return true;
  return confirm(`The sweep will take approximately ${formatDuration(estS)} `
    + `(${n} ${pluralWort(n, 'channel', 'channels')} × ${mtMs} ms). The waterfall grows accordingly slowly, `
    + `but the controls stay usable at all times. Continue?`);
}

// MTIME-Feld nur zeigen, wenn die aktuelle Speed-Stufe MTIM ist - bei den
// Hardware-Sweep-Stufen (LOW/NORM/HIGH) ignoriert das Geraet MEAS:TIME,
// ein sichtbares Feld waere dort irrefuehrend.
function updateMtimeVisibility() {
  el('mtimeLabel').style.display =
    dscanSpeeds[dscanSpeedIndex] === 'MTIM' ? 'inline' : 'none';
  updateMtimeInfo();
}

el('mtimeInput').onchange = e => {
  const v = e.target.value.trim();
  if (v === '') {
    // Leeres Feld = DEFAULT (Geraet waehlt BW-/detektorabhaengig, Annex E).
    pendingHeader = { text: composeParameterAnnotation(), newStartHz: segmentStartHz, newStopHz: segmentStopHz, isInitial: false };
    fetch('/api/mtime?value=DEF');
    updateMtimeInfo();
    return;
  }
  const ms = parseFloat(v);
  if (!(ms >= 0.5 && ms <= 900000)) {
    alert('MTIME must be between 0.5 ms and 900000 ms (empty = DEFAULT).');
    return;
  }
  if (!confirmLongSweep()) { return; }
  pendingHeader = { text: composeParameterAnnotation(), newStartHz: segmentStartHz, newStopHz: segmentStopHz, isInitial: false };
  fetch(`/api/mtime?value=${ms}`);
  updateMtimeInfo();
};

// T-703 (Teil D): Dropdown statt Zyklus-Button - direkte Auswahl der vier
// Stufen. markLocalInteraction() haelt syncDeviceParams() fuer
// LOCAL_GUARD_MS davon ab, die Auswahl waehrend/kurz nach einer
// Nutzerbedienung zu ueberschreiben ("kein Regler-Kampf").
el('speedSelect').onchange = e => {
  markLocalInteraction('speedSelect');
  const prevSpeedIndex = dscanSpeedIndex;
  const newSpeed = e.target.value;
  dscanSpeedIndex = dscanSpeeds.indexOf(newSpeed);
  updateMtimeVisibility();
  if (newSpeed === 'MTIM' && !confirmLongSweep()) {
    // Abgebrochen - Wechsel rueckgaengig machen, Geraet unveraendert lassen.
    dscanSpeedIndex = prevSpeedIndex;
    e.target.value = dscanSpeeds[dscanSpeedIndex];
    updateMtimeVisibility();
    return;
  }
  // Bereich bleibt unverändert (nur Speed betroffen) - newStart/StopHz daher
  // aus dem aktuell laufenden Segment übernehmen.
  pendingHeader = { text: composeParameterAnnotation(), newStartHz: segmentStartHz, newStopHz: segmentStopHz, isInitial: false };
  fetch(`/api/speed?value=${newSpeed}`);
};

// Einmalig beim Laden: Startwerte vom Server holen (der sie beim Start vom
// Gerät übernommen hat, siehe _read_dscan_config_from_device) und die im
// HTML fest hinterlegten Defaults der Bedienelemente überschreiben. Fehler
// hier sind unkritisch (dann bleiben die HTML-Defaults stehen, die Frames
// selbst tragen den echten Bereich ohnehin im OptionalHeader).
fetch('/api/dscan_config').then(r => r.json()).then(cfg => {
  el('dscanStart').value = parseFloat((cfg.start_hz / 1e6).toFixed(3));
  el('dscanStop').value = parseFloat((cfg.stop_hz / 1e6).toFixed(3));
  const spIdx = dscanSpeeds.indexOf(cfg.speed);
  if (spIdx >= 0) {
    dscanSpeedIndex = spIdx;
    el('speedSelect').value = cfg.speed;
  }
  el('mtimeInput').value = cfg.mtime_ms != null ? cfg.mtime_ms : '';
  if (cfg.detector) dscanDetector = cfg.detector;
  updateMtimeVisibility();
  const bwSel = el('dscanBwSelect');
  if (bwSel) bwSel.value = String(cfg.bw_hz);
  updateBwZoomPreview();   // T-2008: die BW vom Geraet steht erst jetzt fest
  // T-702: Squelch-Startzustand vom Geraet uebernehmen (squelchFollow bleibt
  // immer AUS - reiner Frontend-Zustand, nicht persistiert).
  const sqOn = el('squelchOn');
  if (sqOn) sqOn.checked = !!cfg.squelch_on;
  const sqThr = el('squelchThr');
  if (sqThr && cfg.squelch_thr_dbuv != null) {
    sqThr.value = cfg.squelch_thr_dbuv;
    el('squelchLabel').textContent = cfg.squelch_thr_dbuv + ' dBµV';
  }
}).catch(() => {});

// BW-ZOOM im D-SCAN umschalten. Die BW selbst (dscanBwSelect) ist davon
// UNABHÄNGIG immer bedienbar - sie steuert den Kanalabstand/das Sweep-Tempo
// (Annex J) und wirkt auch ohne BW-ZOOM. BW-ZOOM schaltet nur noch den
// eigentlichen Zoom (schmaler Span um den Marker) ein/aus.
let dscanBwZoom = false;
el('bwZoomBtn').onclick = () => {
  flashButton('bwZoomBtn');
  dscanBwZoom = !dscanBwZoom;
  // T-714: Der Knopf traegt die AKTION, nicht den Zustand - die Beschriftung
  // sagt also, was ein Klick bewirkt. Den Zustand selbst nennt die
  // Parameter-Tafel ueber dem Wasserfall ("BW-ZOOM on"/"BW-ZOOM off").
  el('bwZoomBtn').textContent = dscanBwZoom ? 'Disable BW-ZOOM' : 'Enable BW-ZOOM';
  // T-1411: Den neuen Bereich kennt erst das Geraet - rangeFromFrame laesst
  // ihn beim Brennen aus dem ersten fertigen Sweep uebernehmen.
  pendingHeader = { text: composeParameterAnnotation(), newStartHz: segmentStartHz, newStopHz: segmentStopHz,
                    isInitial: false, rangeFromFrame: true };
  fetch(`/api/dscan_bwzoom?value=${dscanBwZoom ? 'on' : 'off'}`);
};
el('dscanBwSelect').onchange = e => {
  // T-1411: bei aktivem BW-ZOOM haengt der Span an der BW - der Bereich
  // aendert sich also mit und kommt wie dort aus dem naechsten Sweep.
  pendingHeader = { text: composeParameterAnnotation(), newStartHz: segmentStartHz, newStopHz: segmentStopHz,
                    isInitial: false, rangeFromFrame: dscanBwZoom };
  fetch(`/api/dscan_bw?value=${e.target.value}`);
  updateMtimeInfo();
};

// ---------------------------------------------------------------------------
// Long-Run-Jobs (WS3 11.4/11.5): Konfigurationsmaske + Status-Banner. Reine
// D-SCAN-Maske - der IFPAN-Zweig fehlt noch (Schritt 4c), ebenso die
// serverseitige Platzpruefung (aktuell nur clientseitige Schaetzung unten).
// ---------------------------------------------------------------------------

// Sperrt waehrend eines aktiven Long-Run-Jobs dieselben geometrieveraendernden
// Bedienelemente wie setGeometryLock() im Zoom, hier fuer die Uebersicht:
// Bereich/Speed/BW-ZOOM/BW, den Long-Run-Knopf selbst, das Session-Recording,
// die Verstaerkungsregelung (T-710) sowie Klick/Drag auf beiden
// Uebersichts-Canvas (rec-lock, Muster Zoom).
let longrunLocked = false;   // T-710: von paintGainUi mitgelesen
function setLongrunLock(locked) {
  longrunLocked = locked;
  el('dscanStart').disabled = locked;
  el('dscanStop').disabled = locked;
  el('applyRangeBtn').disabled = locked;
  el('speedSelect').disabled = locked;
  el('mtimeInput').disabled = locked;
  el('bwZoomBtn').disabled = locked;
  el('dscanBwSelect').disabled = locked;
  el('longrunBtn').disabled = locked;
  el('sessionBtn').disabled = locked;
  // T-710: Verstaerkung mitsperren - eine Umschaltung waehrend eines
  // Long-Run-Jobs wuerde die aufgezeichneten Pegel unvergleichbar machen.
  // Beim Entsperren entscheidet wieder der Modus ueber den MGC-Regler,
  // deshalb die Regler ueber paintGainUi statt direkt setzen.
  el('gainMode').disabled = locked;
  el('dscanGainMode').disabled = locked;
  paintGainUi(el('gainMode').value, null);
  specO.classList.toggle('rec-lock', locked);
  wfO.classList.toggle('rec-lock', locked);
  if (locked) closeLongrunPanel();
}

// Ablagen (/api/storage) in lrStorage einfuellen - je Option "path (free: x GB)",
// nicht erreichbare Ablagen als disabled-Option mit Hinweis.
async function loadStorageRoots() {
  const sel = el('lrStorage');
  sel.innerHTML = '';
  try {
    const roots = await (await fetch('/api/storage')).json();
    roots.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.root;
      if (r.exists && r.free_bytes != null) {
        const gb = r.free_bytes / (1024 ** 3);
        opt.textContent = `${r.root} (free: ${gb.toFixed(1)} GB)`;
        opt.dataset.free = String(r.free_bytes);
      } else {
        opt.textContent = `${r.root} (not reachable)`;
        opt.disabled = true;
      }
      sel.appendChild(opt);
    });
  } catch (e) {
    // /api/storage nicht erreichbar - Panel bleibt nutzbar, storage_root
    // faellt beim Absenden einfach weg (Server verwendet dann seinen Default).
  }
}

// Dauer des geplanten Jobs in Sekunden - aus dem Presets-Wert (bereits in
// Sekunden) oder aus lrStartTime/lrEndTime bei "End time...".
function getLongrunDurationS() {
  const dur = el('lrDuration').value;
  if (dur !== 'custom') return parseFloat(dur);
  const startVal = el('lrStartTime').value;
  const endVal = el('lrEndTime').value;
  if (!startVal || !endVal) return null;
  const diffS = (new Date(endVal) - new Date(startVal)) / 1000;
  return diffS > 0 ? diffS : null;
}

// Formatiert ein Date als Wert fuer <input type="datetime-local">
// (YYYY-MM-DDTHH:MM, lokale Zeit - das Feld selbst kennt keine Zeitzone).
function toDatetimeLocalValue(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
         `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Wandelt ein aus einem datetime-local-Wert konstruiertes Date (daher in
// LOKALER Zeit) in einen ISO-String MIT lokalem UTC-Offset - das Format, das
// der Server erwartet (datetime.fromisoformat in job_store.py). datetime-local
// liefert KEINEN Offset; new Date(...).toISOString() wuerde nach UTC
// konvertieren - ohne diese Funktion wuerde der Server die Zeit falsch
// (verschoben um die lokale Zeitzone) interpretieren.
function toIsoWithOffset(date) {
  const pad = n => String(n).padStart(2, '0');
  const offsetMin = -date.getTimezoneOffset();
  const sign = offsetMin >= 0 ? '+' : '-';
  const abs = Math.abs(offsetMin);
  const offsetStr = `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
         `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${offsetStr}`;
}

// Budgetzeile (Schritt 5b): dispatcht nach lrType auf den passenden Zweig -
// dscan (unveraendert) rechnet aus der laufenden Sweep-Rate der Uebersicht,
// ifpan (neu) aus der live gemessenen bzw. dokumentierten IFPAN-Framerate
// (siehe updateLongrunBudgetIfpan unten).
function updateLongrunBudget() {
  if (el('lrType').value === 'ifpan') {
    updateLongrunBudgetIfpan();
  } else {
    updateLongrunBudgetDscan();
  }
}

// Budgetzeile: nutzt dieselben Werte, die updateSweepStats() bereits in
// #act-points ("points" = n_bins des aktuellen D-SCAN-Sweeps) und #act-secs
// (Sweep-Dauer) anzeigt - keine eigene Messung, siehe dortige Zuweisung.
function updateLongrunBudgetDscan() {
  const box = el('lrBudget');
  const nBins = parseInt(el('act-points').textContent, 10);
  const sweepIntervalS = parseFloat(el('act-secs').textContent);
  if (!Number.isFinite(nBins) || !Number.isFinite(sweepIntervalS) || sweepIntervalS <= 0) {
    box.textContent = 'no sweep rate measured yet - estimate follows after the next sweep';
    box.style.color = '';
    el('lrCreate').disabled = false;
    return;
  }

  const durationS = getLongrunDurationS();
  if (durationS == null || durationS <= 0) {
    box.textContent = '';
    el('lrCreate').disabled = false;
    return;
  }

  const decimVal = el('lrDecimation').value;
  const decimationS = decimVal ? parseFloat(decimVal) : sweepIntervalS;
  const effectiveIntervalS = Math.max(decimationS, sweepIntervalS);
  const frames = durationS / effectiveIntervalS;
  const bytes = frames * (12 + 3 * nBins);
  const mb = bytes / (1024 * 1024);
  const hours = durationS / 3600;

  const opt = el('lrStorage').selectedOptions[0];
  const freeBytes = opt ? parseFloat(opt.dataset.free) : NaN;

  if (Number.isFinite(freeBytes)) {
    const overBudget = bytes > 0.9 * freeBytes;
    box.textContent = `~ ${mb.toFixed(1)} MB for ${hours.toFixed(1)} h`;
    box.className = overBudget ? 'err' : 'ok';
    el('lrCreate').disabled = overBudget;
  } else {
    box.textContent = `~ ${mb.toFixed(1)} MB for ${hours.toFixed(1)} h`;
    box.style.color = '';
    el('lrCreate').disabled = false;
  }
}

// Budgetzeile ifpan (Schritt 5b, WS3 11.4/10.1): konservativ mit dem VOLLEN
// IFPAN-Frame gerechnet (IFPAN_FULL_BINS Bins), unabhaengig von der aktuell
// gewaehlten BW (die croppt serverseitig nur einen Ausschnitt, siehe
// SpectrumBinWriter/compute_window - das Budget soll den worst case zeigen,
// nicht den Idealfall). Framerate: measuredIfpanFps() (siehe dort, gemessen
// aus den live eintreffenden IFPAN-Frames, solange die Zoom-Ansicht laeuft)
// oder ersatzweise IFPAN_FPS_DEFAULT (20 fps, dokumentierter Planungswert
// WS3 10.1). Bei lrAudio zusaetzlich 32000 Byte/s (Modus 6: 16 kHz x 16 Bit
// x mono = 32000 Byte/s exakt - eine echte Dezimalgroesse aus der Samplerate,
// keine binaere KiB-Naeherung). Rest (Anzeige/90-%-Pruefung) analog zum
// dscan-Zweig - bewusst in echten Dezimal-MB (Bytes/1e6) statt der dortigen
// binären Bytes/1024² angezeigt, das ist fuer Bitraten-Angaben die
// gebraeuchlichere Konvention und deckt sich mit der Planungsrechnung aus
// dem Konzept (WS3 11.4: ~140-150 MB/h ohne, +~115 MB/h mit Audio bei 20 fps).
const IFPAN_FULL_BINS = 1001;
function updateLongrunBudgetIfpan() {
  const box = el('lrBudget');
  const durationS = getLongrunDurationS();
  if (durationS == null || durationS <= 0) {
    box.textContent = '';
    el('lrCreate').disabled = false;
    return;
  }

  const measuredFps = measuredIfpanFps();
  const fps = measuredFps || IFPAN_FPS_DEFAULT;
  let bytesPerS = (12 + 2 * IFPAN_FULL_BINS) * fps;
  if (el('lrAudio').checked) bytesPerS += 32000;
  const bytes = bytesPerS * durationS;
  const mb = bytes / 1e6;
  const hours = durationS / 3600;
  const fpsNote = measuredFps
    ? `measured ${fps.toFixed(1)} fps`
    : `planning value ${fps} fps`;

  const opt = el('lrStorage').selectedOptions[0];
  const freeBytes = opt ? parseFloat(opt.dataset.free) : NaN;

  if (Number.isFinite(freeBytes)) {
    const overBudget = bytes > 0.9 * freeBytes;
    box.textContent = `~ ${mb.toFixed(1)} MB for ${hours.toFixed(1)} h (${fpsNote})`;
    box.className = overBudget ? 'err' : 'ok';
    el('lrCreate').disabled = overBudget;
  } else {
    box.textContent = `~ ${mb.toFixed(1)} MB for ${hours.toFixed(1)} h (${fpsNote})`;
    box.style.color = '';
    el('lrCreate').disabled = false;
  }
}

// Eigenes Zustandsflag statt einer Abfrage von element.style.display: das
// Panel ist per CSS-Regel (#longrunPanel { display:none; ... }) verdeckt,
// NICHT per Inline-style-Attribut - .style.display ist deshalb bis zum
// ERSTEN JS-Zugriff ein leerer String, kein 'none'. Ein Vergleich dagegen
// ist damit ein fragiler Zustandsindikator; das Flag ist robust und dient
// als EINZIGE Quelle der Wahrheit fuer offen/zu (longrunBtn UND lrCancel
// nutzen dieselben zwei Funktionen).
let longrunPanelOpen = false;

function closeLongrunPanel() {
  el('longrunPanel').style.display = 'none';
  longrunPanelOpen = false;
}

// Blendet je lrType die passende Feldzeile ein/aus (dscan: Start/Stop/Speed
// + Dezimierung: ifpan: Mitte/Span/Demod/BW/Audio + Hinweistext statt
// Dezimierung, WS3 11.2) und belegt die typspezifischen Felder aus dem
// jeweils aktuellen Kontext neu - wird sowohl beim Oeffnen (openLongrunPanel)
// als auch bei manuellem Umschalten von lrType aufgerufen (Schritt 5b,
// Punkt 4).
function applyLrType() {
  const isIfpan = el('lrType').value === 'ifpan';
  el('lrRowDscanGeom').style.display = isIfpan ? 'none' : 'flex';
  el('lrRowIfpanGeom').style.display = isIfpan ? 'flex' : 'none';
  el('lrDecimation').style.display = isIfpan ? 'none' : '';
  el('lrRowAudio').style.display = isIfpan ? 'flex' : 'none';
  el('lrIfpanRateHint').style.display = isIfpan ? 'inline' : 'none';
  // dscan-only wie lrSpeed/lrDecimation - zusaetzlich nur bei Speed MTIM
  // sichtbar (Handbuch 3.18.3.1), siehe lrSpeed.onchange.
  el('lrMtimeLabel').style.display = (!isIfpan && el('lrSpeed').value === 'MTIM') ? 'flex' : 'none';

  if (isIfpan) {
    // Mittenfrequenz aus dem zuletzt empfangenen Zoom-Frame (latestZ) -
    // laeuft die Zoom-Ansicht bereits, ist das die tatsaechliche aktuelle
    // Mitte. Ohne bisherigen Frame (z. B. Panel aus der Uebersicht heraus
    // auf ifpan umgeschaltet, ohne dass je gezoomt wurde) Fallback auf die
    // Mitte des aktuellen D-SCAN-Bereichs. Span/Demod/BW kommen direkt aus
    // den Zoom-Reglern - die behalten ihren Wert unabhaengig davon, welche
    // Ansicht gerade sichtbar ist.
    const centerHz = latestZ ? latestZ.center_hz
      : Math.round((parseFloat(el('dscanStart').value) + parseFloat(el('dscanStop').value)) / 2 * 1e6);
    el('lrCenter').value = (centerHz / 1e6).toFixed(4);
    el('lrSpan').value = String(ifpanSpans[parseInt(el('spanSlider').value, 10)]);
    el('lrDemod').value = el('modeSelect').value;
    el('lrBw').value = el('bwSelect').value;
  } else {
    // Vorbelegung aus dem TATSAECHLICH laufenden Segment (segmentStartHz/
    // segmentStopHz), nicht aus den Eingabefeldern - die koennten von einer
    // noch nicht uebernommenen Aenderung abweichen. Nur wenn noch kein
    // Segment bekannt ist (0/null), auf die Eingabefelder zurueckfallen.
    if (segmentStartHz) {
      el('lrStart').value = (segmentStartHz / 1e6).toFixed(4);
      el('lrStop').value = (segmentStopHz / 1e6).toFixed(4);
    } else {
      el('lrStart').value = el('dscanStart').value;
      el('lrStop').value = el('dscanStop').value;
    }
    el('lrSpeed').value = dscanSpeeds[dscanSpeedIndex];
  }
  updateLongrunBudget();
}

// Haelt lrEndTime konsistent zu lrStartTime: bei force=true oder wenn das
// Ende leer ist bzw. nicht nach dem Start liegt, wird Start + 1 h gesetzt.
// Liefert true, wenn das Ende (danach) nach dem Start liegt.
function ensureLrEndAfterStart(force) {
  const startVal = el('lrStartTime').value;
  if (!startVal) return false;
  const start = new Date(startVal);
  const endVal = el('lrEndTime').value;
  const endOk = endVal && new Date(endVal) > start;
  if (force || !endOk) {
    const end = new Date(start.getTime() + 3600 * 1000);
    el('lrEndTime').value = toDatetimeLocalValue(end);
  }
  el('lrEndTime').style.borderColor = '';
  return true;
}

function openLongrunPanel(type) {
  el('lrLabel').value = '';
  el('lrType').value = type;
  const now = new Date();
  now.setSeconds(0, 0);
  el('lrStartTime').value = toDatetimeLocalValue(now);
  el('lrDuration').value = '21600';
  // Endzeitpunkt vorbelegen (Start + 1 h), damit bei "End time..." kein
  // leeres bzw. veraltetes Feld steht - Tippfehler im Monat waren die
  // haeufigste Ursache fuer "end_iso must be after start_iso".
  ensureLrEndAfterStart(true);
  el('lrEndTimeLabel').style.display = 'none';
  el('lrDecimation').value = '';
  el('lrRotation').value = '60';
  el('lrAudio').checked = false;
  applyLrType();
  loadStorageRoots().then(updateLongrunBudget);
  // Panel ist position:fixed - die DOM-Position bestimmt nur den Stacking-
  // Context. Das fruehere insertBefore vor #meta-o/#meta-z haengte den
  // Dialog IN den Sweep-Kasten (#sweepBox/#sweepBoxZ, z-index:10): dessen
  // Stacking-Context deckelte das z-index:50 des Dialogs, das Seiten-Panel
  // (.side-panel, z-index:20) verdeckte ihn samt Abdunklung. Direkt unter
  // <body> gilt z-index:50 wieder auf oberster Ebene - der Dialog liegt
  // damit UEBER dem Einstellungs-Panel. appendChild verschiebt denselben
  // Knoten, alle Handler/IDs bleiben unveraendert.
  document.body.appendChild(el('longrunPanel'));
  el('longrunPanel').style.display = 'block';
  longrunPanelOpen = true;
}

el('longrunBtn').onclick = () => {
  // Umschalter: offenes Panel -> schliessen (wie lrCancel), sonst oeffnen
  // und neu vorbelegen (Uebersicht -> Typ dscan).
  if (longrunPanelOpen) {
    closeLongrunPanel();
    return;
  }
  openLongrunPanel('dscan');
};

el('longrunBtnZ').onclick = () => {
  // Wie longrunBtn oben, aber aus der Zoom-Ansicht heraus -> Typ ifpan.
  // Dasselbe Panel (gemeinsame Position ausserhalb beider Ansichten, siehe
  // HTML-Kommentar) - EIN Umschalter unabhaengig davon, ueber welchen der
  // beiden Knoepfe es geoeffnet wurde.
  if (longrunPanelOpen) {
    closeLongrunPanel();
    return;
  }
  openLongrunPanel('ifpan');
};

el('lrType').onchange = applyLrType;

el('lrDuration').onchange = () => {
  el('lrEndTimeLabel').style.display = el('lrDuration').value === 'custom' ? 'flex' : 'none';
  updateLongrunBudget();
};
el('lrDecimation').onchange = updateLongrunBudget;
el('lrStorage').onchange = updateLongrunBudget;
el('lrEndTime').onchange = () => {
  // Rot markieren, wenn Ende nicht nach Start liegt (Server wuerde ablehnen).
  const s = el('lrStartTime').value, e = el('lrEndTime').value;
  el('lrEndTime').classList.toggle('err', !!(s && e && new Date(e) <= new Date(s)));
  updateLongrunBudget();
};
el('lrStartTime').onchange = () => {
  // Start verschoben -> Ende nur nachziehen, wenn es sonst vor dem Start laege.
  ensureLrEndAfterStart(false);
  updateLongrunBudget();
};
el('lrSpan').onchange = updateLongrunBudget;
el('lrDemod').onchange = updateLongrunBudget;
el('lrBw').onchange = updateLongrunBudget;
el('lrAudio').onchange = updateLongrunBudget;

el('lrSpeed').onchange = () => {
  // MTIME wirkt nur bei Speed MTIM (Handbuch 3.18.3.1) - Feld sonst
  // verstecken, damit kein wirkungsloser Wert erfasst wird.
  el('lrMtimeLabel').style.display = el('lrSpeed').value === 'MTIM' ? 'flex' : 'none';
  updateLongrunBudget();
};

el('lrCancel').onclick = () => { closeLongrunPanel(); };

el('lrCreate').onclick = () => {
  const startTimeVal = el('lrStartTime').value;
  if (!startTimeVal) { alert('Start time missing.'); return; }

  const isIfpan = el('lrType').value === 'ifpan';
  let payload;
  if (isIfpan) {
    // ifpan-Payload (Schritt 5b, Punkt 6): center_hz/span_hz/demod/bw_hz/
    // audio - decimation_s wird bewusst NICHT gesendet (WS3 11.2, volle
    // Framerate; der Server lehnt es fuer type=ifpan ohnehin mit 400 ab,
    // siehe _handle_longrun_create).
    const centerHz = Math.round(parseFloat(el('lrCenter').value) * 1e6);
    if (!Number.isFinite(centerHz) || centerHz <= 0) {
      alert('Center frequency missing or invalid.'); return;
    }
    payload = {
      type: 'ifpan',
      label: el('lrLabel').value,
      center_hz: centerHz,
      span_hz: parseInt(el('lrSpan').value, 10),
      demod: el('lrDemod').value,
      bw_hz: parseInt(el('lrBw').value, 10),
      audio: el('lrAudio').checked,
      start_iso: toIsoWithOffset(new Date(startTimeVal)),
      rotation_min: parseInt(el('lrRotation').value, 10) || 60,
    };
  } else {
    const startHz = Math.round(parseFloat(el('lrStart').value) * 1e6);
    const stopHz = Math.round(parseFloat(el('lrStop').value) * 1e6);
    if (!(startHz < stopHz)) { alert('Start must be lower than stop.'); return; }
    payload = {
      type: 'dscan',
      label: el('lrLabel').value,
      start_hz: startHz,
      stop_hz: stopHz,
      speed: el('lrSpeed').value,
      // null = DEFAULT (Geraet waehlt die Messzeit BW-/detektorabhaengig);
      // nur bei Speed MTIM ueberhaupt relevant, sonst immer null.
      mtime_ms: (el('lrSpeed').value === 'MTIM' && el('lrMtime').value.trim() !== '')
        ? parseFloat(el('lrMtime').value) : null,
      start_iso: toIsoWithOffset(new Date(startTimeVal)),
      rotation_min: parseInt(el('lrRotation').value, 10) || 60,
    };
    const decimVal = el('lrDecimation').value;
    if (decimVal) payload.decimation_s = parseFloat(decimVal);
  }
  if (el('lrStorage').value) payload.storage_root = el('lrStorage').value;

  if (el('lrDuration').value === 'custom') {
    const endTimeVal = el('lrEndTime').value;
    if (!endTimeVal) { alert('End time missing.'); return; }
    if (new Date(endTimeVal) <= new Date(startTimeVal)) {
      el('lrEndTime').classList.add('err');
      alert('End time (' + isoDateTime(new Date(endTimeVal)) +
            ') must be after the start (' + isoDateTime(new Date(startTimeVal)) + ').');
      return;
    }
    payload.end_iso = toIsoWithOffset(new Date(endTimeVal));
  } else {
    payload.duration_s = parseFloat(el('lrDuration').value);
  }

  fetch('/api/longrun/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(async r => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${r.status}`);
      }
      return r.json();
    })
    .then(manifest => {
      closeLongrunPanel();
      const startMs = new Date(manifest.start_iso).getTime();
      if (Math.abs(startMs - Date.now()) <= 30000) {
        el('meta-o').textContent = 'Job created - starting within 30 s (scheduler tick)';
      } else {
        el('meta-o').textContent = `Job created - scheduled start ${isoDateTime(new Date(manifest.start_iso))}`;
      }
    })
    .catch(err => alert(err.message));
};

// ---- Status-Banner: Polling unabhaengig vom SSE-Stream (5 s Takt) --------
let longrunWasActive = false;

function formatDurationMs(ms) {
  const totalMin = Math.max(0, Math.floor(ms / 60000));
  const h = Math.floor(totalMin / 60), m = totalMin % 60;
  return h > 0 ? `${h} h ${m} min` : `${m} min`;
}

function pollLongrunStatus() {
  fetch('/api/longrun/status')
    .then(r => r.json())
    .then(data => {
      const banner = el('longrunBanner');
      if (data.active) {
        const job = data.job;
        const stats = job.stats || {};
        const startDate = new Date(job.start_iso);
        const sinceStr = isoHM(startDate);
        const durStr = formatDurationMs(Date.now() - startDate.getTime());
        // Restlaufzeit: bei gesetztem Ende ein Countdown (mm:ss bzw. h:mm:ss ueber
        // formatDurationMs), bei "until stopped" die bisherige Laufdauer.
        const endMs = job.end_iso ? new Date(job.end_iso).getTime() : null;
        let timeStr;
        if (endMs) {
          const endDate = new Date(endMs);
          const sameDay = endDate.toDateString() === new Date().toDateString();
          const endClock = isoHM(endDate);
          const endLabel = sameDay ? endClock : `${isoDate(endDate)} ${endClock}`;
          timeStr = `runs until ${endLabel} (${formatDurationMs(Math.max(0, endMs - Date.now()))} left)`;
        } else {
          timeStr = `running since ${sinceStr} (${durStr}) until stopped`;
        }
        const mb = ((stats.bytes || 0) / (1024 * 1024)).toFixed(1);
        const nFiles = (job.files || []).length;
        // Geometrie-Teil des Bannertexts je Job-Typ (dscan: Bereich in MHz,
        // ifpan: Mitte ± Span/2 + Modulation) - der Rest ist identisch.
        let geomStr, countStr;
        if (job.type === 'ifpan') {
          const centerMHz = (job.center_hz / 1e6).toFixed(4);
          const spanKHz = (job.span_hz / 1e3).toFixed(0);
          geomStr = `${centerMHz} MHz ± ${spanKHz / 2} kHz (${job.demod})`;
          countStr = `${stats.frames || 0} ${pluralWort(stats.frames || 0, 'frame', 'frames')}`;
        } else if (job.type === 'mscan') {
          geomStr = `MSCAN ${job.title} (${job.mscan_job_id}) – ${labelOf('mode', job.mode)} – ${job.channel_count} ${pluralWort(job.channel_count, 'channel', 'channels')}`;
          if (stats.phase === 'wartet') {
            countStr = 'waiting for controller';
          } else if (stats.phase === 'programmieren') {
            countStr = `programming ${stats.programmed || 0}/${job.channel_count}`;
          } else if (stats.phase === 'scan') {
            countStr = `${stats.cycles || 0} ${pluralWort(stats.cycles || 0, 'cycle', 'cycles')} – ${stats.recordings || 0} ${pluralWort(stats.recordings || 0, 'recording', 'recordings')} on ${stats.active_channels || 0} ${pluralWort(stats.active_channels || 0, 'channel', 'channels')}`;
          } else {
            countStr = 'finishing ...';
          }
        } else {
          const startMHz = (job.start_hz / 1e6).toFixed(3);
          const stopMHz = (job.stop_hz / 1e6).toFixed(3);
          geomStr = `${startMHz}-${stopMHz} MHz`;
          countStr = `${stats.sweeps || 0} ${pluralWort(stats.sweeps || 0, 'sweep', 'sweeps')} - ${stats.frames || 0} ${pluralWort(stats.frames || 0, 'frame', 'frames')}`;
        }
        // T-1410: das Abzeichen links traegt "Long Run" (samt Aufnahme-Glyph als
        // SVG - U+23FA wuerde Windows als Emoji rendern), hier nur noch die
        // Angaben zum Job, mit Mittelpunkt als Trenner wie in den Listen.
        el('lrbText').textContent =
          `'${job.label}' · ${geomStr} · ${timeStr} · ${countStr} · ${mb} MB · ${nFiles} ${pluralWort(nFiles, 'file', 'files')}` +
          (stats.audio_error ? ` · Audio error: ${stats.audio_error}` : '');
        banner.classList.add('on');
        setLongrunLock(true);
        setGeometryLock(true, { lockRecordButtons: true });
        longrunWasActive = true;
      } else {
        banner.classList.remove('on');
        if (longrunWasActive) {
          setLongrunLock(false);
          setGeometryLock(false, { lockRecordButtons: true });
          const host = window.location.hostname;
          el('meta-o').innerHTML =
            `Long run finished - analysis: <a href="http://${host}:8040/" target="eb200_spectrum">http://${host}:8040/</a>`;
        }
        longrunWasActive = false;
      }
    })
    .catch(() => { /* Fetch-Fehler: Banner unveraendert lassen, kein Flackern */ });
}

el('lrbStop').onclick = () => {
  el('lrbStop').disabled = true;
  fetch('/api/longrun/stop', { method: 'POST' })
    .then(() => pollLongrunStatus())
    .catch(() => {})
    .finally(() => { el('lrbStop').disabled = false; });
};

setInterval(pollLongrunStatus, 5000);
pollLongrunStatus();

// T-2011: Grid schaltet das Raster-Overlay im AKTUELLEN Segment (segmente[0])
// ein oder aus - sofort im ganzen Segment sichtbar. Aeltere Segmente behalten
// ihren Zustand, ein neues Segment uebernimmt ihn (segmentNeu).
el('gridToggle').onchange = e => {
  segmente[0].grid = e.target.checked;
  requestPaint('o');
};
// Katalog-Streifen ein/aus - rein client-seitig, wirkt sofort auf beide Streifen.
// Ausgeschaltet klappt der Streifen komplett ein (display:none), statt nur
// schwarz zu bleiben - Platzverschwendung bei ungenutztem Katalog vermeiden.
el('knownToggle').onchange = e => {
  knownEnabled = e.target.checked;
  for (const id of KNOWN_STRIPS) el(id).style.display = knownEnabled ? '' : 'none';
  redrawKnownStrips();
};
// Start-Zustand aus dem Checkbox-Wert herstellen (Toggle ist "checked" -
// nur zur Vollstaendigkeit, falls der Browser den Zustand wiederherstellt).
for (const id of KNOWN_STRIPS) el(id).style.display = el('knownToggle').checked ? '' : 'none';
// Regionsfilter ein/aus (station.json, 'land-*'-Tags) - rein client-seitig,
// wie knownToggle. Nur waterfall.py hat diesen Schalter; die anderen Server
// filtern ohne Toggle nach station.json.
el('knownRegionToggle').onchange = e => {
  knownRegionEnabled = e.target.checked;
  redrawKnownStrips();
};

// Rohdaten-Session-Aufzeichnung (D-SCAN, siehe docs/WS1 Abschnitt 3c). Anders
// als gridToggle/markToggle KEIN rein client-seitiges Flag: jede Umschaltung
// ist ein echter Server-Aufruf (/record/start bzw. /record/stop), der Toggle
// spiegelt nur den vom Server bestätigten Zustand wider.
let sessionPollTimer = null;

// aktualisiert das REC-Abzeichen aus einem /record/status-Antwortobjekt
// ({recording, path, n_sweeps, n_segments, n_dropped} oder {recording:false}).
function updateRecBadge(status) {
  // T-1410: zwei Anzeigen mit demselben Inhalt - Pille in der Kopfleiste
  // (immer sichtbar) und Zeile unter dem Session-Knopf (nur bei offenem Panel).
  const badge = el('rec-badge'), line = el('rec-status');
  if (status && status.recording) {
    badge.textContent = `● REC - ${status.n_sweeps} ${pluralWort(status.n_sweeps, 'sweep', 'sweeps')}`;
    badge.style.display = 'inline';
    line.textContent = `● Recording - ${status.n_sweeps} ${pluralWort(status.n_sweeps, 'sweep', 'sweeps')}`;
    line.style.display = 'block';
  } else {
    badge.style.display = 'none';
    line.style.display = 'none';
  }
}

function pollSessionStatus() {
  fetch('/record/status')
    .then(r => r.json())
    .then(updateRecBadge)
    .catch(() => {});   // ein einzelner verpasster Poll ist unkritisch
}

function startSessionPolling() {
  stopSessionPolling();   // kein doppeltes Intervall, falls schon eines läuft
  sessionPollTimer = setInterval(pollSessionStatus, 2500);
}

function stopSessionPolling() {
  if (sessionPollTimer !== null) {
    clearInterval(sessionPollTimer);
    sessionPollTimer = null;
  }
}

// Session-Aufnahmeknopf (ersetzt die fruehere Checkbox sessionToggle).
// Darstellung wie die Audio-/Spektrum-Knoepfe im Zoom (CSS .rec-btn):
// Ruhe = Record-Punkt rot, aktiv = Knopf rot mit Stop-Glyph. Die Server-
// Logik (/record/start|stop|status) ist unveraendert; sessionActive ist die
// einzige Quelle der Wahrheit fuer den Knopfzustand. Glyphen seit T-1407b als
// Inline-SVG (SVG_REC/SVG_STOP, am Skriptanfang deklariert).
let sessionActive = false;

function paintSessionBtn(active) {
  sessionActive = active;
  const btn = el('sessionBtn');
  btn.classList.toggle('active', active);
  btn.innerHTML = `<span class="rec-dot">${active ? SVG_STOP : SVG_REC}</span> Session recording`;
}

el('sessionBtn').onclick = () => {
  if (!sessionActive) {
    fetch('/record/start', { method: 'POST' })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(status => {
        paintSessionBtn(true);
        updateRecBadge(status);
        startSessionPolling();
      })
      .catch(err => {
        // Start fehlgeschlagen (z. B. Session-Datei nicht anlegbar) - Knopf
        // im Ruhezustand lassen und knapp informieren, statt einen falschen
        // "läuft"-Zustand vorzutäuschen.
        paintSessionBtn(false);
        updateRecBadge(null);
        alert('Session recording could not be started: ' + err.message);
      });
  } else {
    stopSessionPolling();
    paintSessionBtn(false);
    fetch('/record/stop', { method: 'POST' })
      .then(r => r.json())
      .then(status => {
        updateRecBadge(status);
        // T-802: Deep-Link zur Auswertung der soeben geschlossenen Session-DB
        // in :8020, Muster ist die bestehende Long-Run-Ende-Zeile oben.
        if (status && status.db_path) {
          const host = window.location.hostname;
          el('meta-o').innerHTML =
            `Session finished - analysis: <a href="http://${host}:8020/?path=${encodeURIComponent(status.db_path)}" target="eb200_sessions">http://${host}:8020/</a>`;
        }
      })
      .catch(() => updateRecBadge(null));
  }
};

// Initial-Sync beim Laden der Seite: Toggle-Stellung und Abzeichen aus dem
// TATSÄCHLICHEN Server-Zustand setzen - ein Reload während laufender
// Aufnahme oder ein zweiter Browser-Tab zeigen so sofort den korrekten Stand.
fetch('/record/status')
  .then(r => r.json())
  .then(status => {
    paintSessionBtn(!!(status && status.recording));
    updateRecBadge(status);
    if (status && status.recording) startSessionPolling();
  })
  .catch(() => {});

// Punkt 1: "Jump to live" pinnt die Ansicht wieder auf den
// Live-Rand (scrollRow 0, oberste/neueste Zeile).
el('jumpLiveBtn').onclick = () => {
  scrollRow = 0;
  wfScrollO.scrollTop = 0;
  renderViewportO();
};

// echter, ziehbarer Scrollbalken: der native overflow-y:auto-Container
// liefert scrollTop, daraus leiten wir scrollRow ab und blitten neu.
wfScrollO.addEventListener('scroll', () => {
  const maxScrollRow = Math.max(0, usedRows - VIEWPORT_ROWS);
  scrollRow = Math.min(Math.round(wfScrollO.scrollTop), maxScrollRow);
  renderViewportO();
});

// T-505f: Uebersichts-Katalogstreifen auf die belegten Reihen zuschneiden.
// drawKnownStrip meldet die belegte Hoehe (0/18/33/55 px) ueber den Hook aus
// dem geteilten Block; der Wrapper (#knownWrapO, overflow:hidden) schneidet
// den Canvas unten ab und der Zeit-Gutter (#time-o) rueckt um dieselbe
// Differenz nach - so bleibt er buendig zum Wasserfall. Der Zoom-Streifen
// (known-z) behaelt seine feste Hoehe (eigenes Layout).
let knownGutterM0 = null;
function knownStripResized(id, hoehe) {
  // T-126: Sobald der Uebersichtsstreifen gezeichnet wird, steht knownCatalog.
  // Zeigt an, wenn in :8010 Katalogdateien abgewaehlt sind - sonst sucht man
  // fehlende Baender im Wasserfall statt in der Katalogpflege. Bewusst hier
  // und nicht im geteilten KNOWN_STRIP_BLOCK (wortgleich in fuenf Servern).
  const kv = knownCatalog && knownCatalog.view;
  const kcEl = el('knownCount');
  if (kcEl) kcEl.textContent = (kv && kv.hidden > 0)
    ? ' (' + (kv.total - kv.hidden) + '/' + kv.total + ')' : '';
  if (id !== 'known-o') return;
  const wrap = el('knownWrapO'), gutter = el('time-o'), canvas = el('known-o');
  // T-1409: Versatz steckt jetzt im paddingTop (siehe CSS #time-o).
  if (knownGutterM0 === null) knownGutterM0 = parseFloat(getComputedStyle(gutter).paddingTop);
  const voll = canvas.height + 2; // Canvas + 1px Rand oben/unten
  const ziel = hoehe > 0 ? Math.min(voll, hoehe + 2) : 0;
  wrap.style.height = ziel + 'px';
  gutter.style.paddingTop = (knownGutterM0 - (voll - ziel)) + 'px';
}

// T-505: Auto-Marker. Ereignisse kommen mit dem 'dscan'-Frame (frame.detect,
// Liste {bin, f_hz, level, floor, t}); x aus dem Bin-Index wie in paintPartialRow
// (Bin-Mitte), y = Zeile 0 (fertiger Sweep). Serverseitige Merkliste: Toggle aus
// stoppt das Markieren am Server, damit Signale nicht "verbraucht" werden.
function paintAutoMarkers(frame) {
  const events = frame.detect;
  if (!events || events.length === 0) return;
  if (!el('autoMarkToggle').checked) return;
  // T-2012: staerkster Marker zuerst, damit er die bevorzugte Label-Lage bekommt;
  // Zaehler und Markerliste behalten die Reihenfolge des Servers.
  const nachPegel = events.slice().sort((a, b) => b.level - a.level);
  for (const ev of nachPegel) {
    const xPix = ((ev.bin + 0.5) / frame.total) * historyCanvas.width;
    // T-505e: bestaetigt nach ev.streak Sweeps -> Onset liegt (streak-1) Zeilen tiefer.
    const onsetRow = Math.max(0, (ev.streak || 1) - 1);
    paintClickMarker(xPix, onsetRow, ev.f_hz, { chip: autoMarkChip, color: AUTO_MARK_COLOR });
  }
  for (const ev of events) {
    autoMarkCount += 1;
    autoMarkEvents.push(ev);   // T-505b
  }
  el('detectCount').textContent = autoMarkCount + ' ' + pluralWort(autoMarkCount, 'marker', 'markers');
  renderDetectList();
}

// T-505b: Ereignisliste (Overlay #detectList). Quelle ist autoMarkEvents - also genau
// das, was in den Canvas gebrannt wurde (nicht die Server-Merkliste), damit Tabelle
// und Bild immer uebereinstimmen. Zeilenumbruch/Tab ueber fromCharCode, da in
// HTML_PAGE keine Backslash-Escapes moeglich sind.
const DETECT_CRLF = String.fromCharCode(13, 10);
const DETECT_TAB = String.fromCharCode(9);

function detectRowsText(sep) {
  const lines = ['Time' + sep + 'MHz' + sep + 'dBuV'];
  for (const ev of autoMarkEvents) {
    lines.push(formatClock(ev.t) + sep + (ev.f_hz / 1e6).toFixed(4) + sep + ev.level.toFixed(1));
  }
  return lines.join(DETECT_CRLF) + DETECT_CRLF;
}

function renderDetectList() {
  const rows = [];
  for (let i = autoMarkEvents.length - 1; i >= 0; i--) {
    const ev = autoMarkEvents[i];
    rows.push('<tr><td>' + formatClock(ev.t) + '</td><td class="f">' + (ev.f_hz / 1e6).toFixed(4) +
      '</td><td>' + ev.level.toFixed(1) + '</td></tr>');
  }
  el('detectListBody').innerHTML = rows.join('');
  el('detectListTitle').textContent = 'Marker list (' + autoMarkEvents.length + ')';
  const leer = autoMarkEvents.length === 0;
  el('detectCatalogBtn').disabled = leer;
  el('detectCopyBtn').disabled = leer;
  el('detectCsvBtn').disabled = leer;
}

function copyDetectList() {
  const text = detectRowsText(DETECT_TAB);
  // Clipboard-API nur im sicheren Kontext (https/localhost). Im LAN per http
  // greift der textarea/execCommand-Weg.
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => flashButton('detectCopyBtn'));
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); flashButton('detectCopyBtn'); } catch (e) {}
  document.body.removeChild(ta);
}

function downloadDetectCsv() {
  const blob = new Blob([detectRowsText(';')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'automarker_' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
  flashButton('detectCsvBtn');
}

// T-506: Markerliste an den Frequenzkatalog (:8010, Vorschaumaske aus
// T-1015) uebergeben. Formular-POST statt fetch: :8010 ist ein anderer
// Port, ein regulaerer Formular-Absturz kommt ohne CORS-Freigabe aus, ein
// fetch()-Aufruf ueber die Portgrenze braeuchte sie. POST statt ?data= in
// der URL, weil eine lange Markerliste die URL-Laengengrenze sprengen
// wuerde. Zieladresse ueber location.hostname statt fest 'localhost', damit
// es auch bei Fernzugriff (Pi/Notebook) funktioniert. Es wird IMMER die
// komplette Liste uebergeben - Auswahl, Frequenzkorrektur und
// Duplikatentscheidung passieren auf der Vorschaumaske selbst (keine
// Checkboxen hier im Overlay).
function sendDetectToCatalog() {
  if (autoMarkEvents.length === 0) return;
  function hiddenField(form, name, value) {
    const f = document.createElement('input');
    f.type = 'hidden';
    f.name = name;
    f.value = value;
    form.appendChild(f);
  }
  const form = document.createElement('form');
  form.method = 'post';
  form.action = 'http://' + location.hostname + ':8010/known/marker-import/save';
  form.target = 'eb200_catalog';   // gleiches Fenster wie der Navigationseintrag Frequenzkatalog
  form.style.display = 'none';
  hiddenField(form, 'data', detectRowsText(DETECT_TAB));
  hiddenField(form, 'action', 'preview');
  document.body.appendChild(form);
  form.submit();
  document.body.removeChild(form);
  flashButton('detectCatalogBtn');
}

el('detectListBtn').onclick = () => {
  const box = el('detectList');
  const show = box.style.display !== 'block';
  box.style.display = show ? 'block' : 'none';
  el('detectListBtn').classList.toggle('active', show); // T-505d
};
el('detectCopyBtn').onclick = copyDetectList;
el('detectCsvBtn').onclick = downloadDetectCsv;
el('detectCatalogBtn').onclick = sendDetectToCatalog;
el('detectRestartBtn').onclick = () => {
  flashButton('detectRestartBtn');
  clearAutoMarkers();   // Canvas bleibt, nur Merkliste/Zaehler/Liste von vorn
};

// T-2011: Popover mit den Raster-Einstellungen am Grid-Knopf (Muster
// initDetectMenu). Jede Aenderung wird sofort gezeichnet und gespeichert.
(function initGridMenu() {
  const menu = el('gridMenu'), toggle = el('gridCfgBtn'), box = el('gridCfg');
  const setOpen = open => { box.hidden = !open; toggle.classList.toggle('active', open); };
  toggle.onclick = ev => { ev.stopPropagation(); setOpen(box.hidden); };
  document.addEventListener('click', ev => { if (!menu.contains(ev.target)) setOpen(false); });
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape') setOpen(false); });
  const uebernehmen = () => {
    try { localStorage.setItem(GRID_CFG_KEY, JSON.stringify(gridCfg)); } catch (e) { /* ohne Speicher weiter */ }
    el('gridAlphaLabel').textContent = gridCfg.alpha;
    requestPaint('o');
  };
  el('gridAlpha').value = gridCfg.alpha;
  el('gridAlphaLabel').textContent = gridCfg.alpha;
  el('gridColor').value = gridCfg.color;
  el('gridDash').checked = !!gridCfg.dash;
  el('gridTimeStep').value = gridCfg.timeStep;
  el('gridTimeLabel').value = gridCfg.timeLabel;
  el('gridAlpha').oninput = e => { gridCfg.alpha = parseInt(e.target.value, 10); uebernehmen(); };
  el('gridColor').onchange = e => { gridCfg.color = e.target.value; uebernehmen(); };
  el('gridDash').onchange = e => { gridCfg.dash = e.target.checked; uebernehmen(); };
  el('gridTimeStep').onchange = e => { gridCfg.timeStep = e.target.value; uebernehmen(); };
  el('gridTimeLabel').onchange = e => { gridCfg.timeLabel = e.target.value; uebernehmen(); };
})();

// T-505e: Popover mit den Auto-Marker-Einstellungen unter dem Toolbar-Knopf
// (Muster initNavMenu). Klick ausserhalb oder Escape schliesst.
(function initDetectMenu() {
  const menu = el('detectMenu'), toggle = el('detectCfgBtn'), box = el('detectCfg');
  const setOpen = open => { box.hidden = !open; toggle.classList.toggle('active', open); };
  toggle.onclick = ev => { ev.stopPropagation(); setOpen(box.hidden); };
  document.addEventListener('click', ev => { if (!menu.contains(ev.target)) setOpen(false); });
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape') setOpen(false); });
})();

// T-505e: Mindestdauer zusaetzlich in Sekunden, aus der gemessenen Sweepdauer.
function updateDetectMinSecs() {
  const n = parseInt(el('detectMin').value, 10);
  el('detectMinSecs').textContent = (lastSweepElapsedS != null)
    ? ('≈ ' + (n * lastSweepElapsedS).toFixed(1) + ' s') : '';
}

// T-505d: Delta-Rueckmeldung vom Server (frame.detect_delta). Bei aktivem
// "automatic" hat der Server das Delta aus den ersten Sweeps bestimmt -
// Regler und Label nachziehen, OHNE es zurueckzusenden (kein Echo).
function syncDetectDelta(frame) {
  if (frame.detect_delta == null) return;
  if (!el('detectAutoToggle').checked) return;
  const v = String(Math.round(frame.detect_delta));
  if (el('detectDelta').value !== v) {
    el('detectDelta').value = v;
    el('detectDeltaLabel').textContent = v;
  }
}

function sendDetectConfig() {
  const on = el('autoMarkToggle').checked ? 1 : 0;
  const delta = parseInt(el('detectDelta').value, 10);
  const auto = el('detectAutoToggle').checked ? 1 : 0;
  // T-505d: bei "automatic" das Server-Delta nicht ueberschreiben.
  let q = '/api/detect?on=' + on + '&auto=' + auto;
  if (!auto) q += '&delta=' + delta;
  q += '&min=' + parseInt(el('detectMin').value, 10); // T-505e
  fetch(q);
}

function clearAutoMarkers() {
  autoMarkCount = 0;
  autoMarkEvents.length = 0; // T-505b
  renderDetectList();
  el('detectCount').textContent = '0 markers';
  fetch('/api/detect/clear');
}

el('autoMarkToggle').onchange = sendDetectConfig;
el('detectDelta').oninput = e => { el('detectDeltaLabel').textContent = e.target.value; };
el('detectDelta').onchange = () => { el('detectAutoToggle').checked = false; sendDetectConfig(); }; // T-505d
el('detectAutoToggle').onchange = sendDetectConfig;
el('detectMin').oninput = e => { el('detectMinLabel').textContent = e.target.value; updateDetectMinSecs(); }; // T-505e
el('detectMin').onchange = sendDetectConfig;
el('detectChipToggle').onchange = e => { autoMarkChip = e.target.checked; };
// T-2012: wirkt auf kuenftige Chips; gebrannte bleiben, wo sie sind.
el('detectGap').oninput = e => {
  labelAbstandPx = parseInt(e.target.value, 10);
  el('detectGapLabel').textContent = labelAbstandPx;
};
// Seitenaufbau = frischer Canvas: Server-Merkliste leeren, Regler-Zustand melden.
clearAutoMarkers();
sendDetectConfig();

el('clearWfBtn').onclick = () => {
  flashButton('clearWfBtn');
  clearWaterfallHistory();
  clearAutoMarkers(); // T-505
};

el('exportJpgBtn').onclick = () => {
  flashButton('exportJpgBtn');
  exportWaterfallJpg();
};

// IFPAN-Span per Schieberegler: nur die laut Annex C gültigen Stufen
// (±12.5/25/50/100/250/500 kHz Rollkey, d. h. folgende Gesamtbreiten) -
// muss mit ALLOWED_IFPAN_SPANS_HZ im Backend übereinstimmen.
const ifpanSpans = [25000, 50000, 100000, 200000, 500000, 1000000];
function formatSpanLabel(hz) {
  return hz >= 1000 ? `${hz / 1000} kHz` : `${hz} Hz`;
}

// lrSpan-Optionen (Long-Run-Panel, Schritt 5b) aus derselben ifpanSpans-
// Liste generieren wie der Span-Regler oben - vermeidet eine dritte Kopie
// der Werte im HTML-Template (die Liste muss ohnehin exakt mit
// ALLOWED_IFPAN_SPANS_HZ im Backend übereinstimmen, siehe Kommentar dort).
ifpanSpans.forEach(hz => {
  const opt = document.createElement('option');
  opt.value = String(hz);
  opt.textContent = formatSpanLabel(hz);
  el('lrSpan').appendChild(opt);
});

// Rollierender Puffer der letzten IFPAN-Frame-Ankunftszeiten (Client-Empfang)
// - liefert die tatsächlich gemessene Framerate für die ifpan-Budgetzeile
// (Schritt 5b, Punkt 5), solange die Zoom-Ansicht Daten empfängt. WS3 10.1
// nennt 20 fps als Planungswert - der wird nur verwendet, wenn (noch) keine
// Messung vorliegt (measuredIfpanFps() liefert dann null).
let ifpanFrameTimes = [];
const IFPAN_FPS_DEFAULT = 20;
const IFPAN_FPS_WINDOW_S = 5;
function measuredIfpanFps() {
  const now = Date.now();
  ifpanFrameTimes = ifpanFrameTimes.filter(t => now - t <= IFPAN_FPS_WINDOW_S * 1000);
  if (ifpanFrameTimes.length < 2) return null;
  const spanS = (ifpanFrameTimes[ifpanFrameTimes.length - 1] - ifpanFrameTimes[0]) / 1000;
  return spanS > 0 ? (ifpanFrameTimes.length - 1) / spanS : null;
}

el('spanSlider').oninput = e => {
  // Label live aktualisieren, während gezogen wird - aber noch nicht
  // anwenden, um /api/span nicht bei jedem Zwischenschritt aufzurufen.
  el('spanLabel').textContent = formatSpanLabel(ifpanSpans[parseInt(e.target.value)]);
};
el('spanSlider').onchange = e => {
  // Erst beim Loslassen des Reglers tatsächlich anwenden.
  const hz = ifpanSpans[parseInt(e.target.value)];
  fetch(`/api/span?value=${hz}`);
  resetSMeter();
};

// Modulation/Bandbreite im IFPAN-Zoom: bei Auswahl sofort am Gerät setzen
// (wirkt beim nächsten Zoom-Aufbau, siehe DeviceController._configure_ifpan)
// (keine weitere Verwendung im Browser seit Wegfall der Block-Übergabe 2026-08).
// T-703: Zoom-Selects (Modulation/Bandbreite) mit dem je ifpan-Frame vom
// Controller mitgelieferten Geraetezustand synchronisieren. Noetig, weil die
// Selects beim Seitenaufbau statisch mit den Server-Defaults gerendert
// werden - nach einem Reload zeigten sie sonst AM/9 kHz, waehrend das Geraet
// z. B. mit FM/120 kHz weiterlaeuft (Controller-Zustand ueberdauert die
// Seite). Programmatisches Setzen von .value feuert KEIN onchange -> keine
// /api/mode- bzw. /api/bw-Rueckkopplung. DOM nur bei Abweichung anfassen
// (laeuft pro Frame, ~Framerate des IFPAN-Streams). Unbekannte Werte (z. B.
// eine am Frontpanel gewaehlte Demodulation ohne passende Option) werden
// ignoriert - die Selects bleiben dann unveraendert.
function syncZoomSettings(frame) {
  if (frame.demod) {
    const sel = el('modeSelect');
    if (sel.value !== frame.demod
        && [...sel.options].some(o => o.value === frame.demod)) {
      sel.value = frame.demod;
      checkModeBwCombo();
    }
  }
  if (frame.bw_hz) {
    const sel = el('bwSelect');
    const v = String(frame.bw_hz);
    if (sel.value !== v && [...sel.options].some(o => o.value === v)) {
      sel.value = v;
      updateBwBandZ();   // BW-Streifen sofort auf die neue Wahl nachziehen
      checkModeBwCombo();
      resetSMeter();     // Peak-Fenster (peakInBwWindow) hat sich geaendert
    }
  }
}

// T-703 (Teil B/C/D): periodischer Geraetezustand aus dem SCPI-Polling
// (device_params-SSE-Events, ~alle PARAM_POLL_INTERVAL_S bzw. einmalig
// initial bei Verbindungsaufbau) - deckt den Reload-/Uebersichts-Fall ab,
// den syncZoomSettings() oben NICHT abdeckt (die laeuft nur pro ifpan-Frame,
// also nur waehrend der Zoom aktiv ist). Mod/BW werden IMMER uebernommen
// (kein Nutzerkonflikt moeglich - reine Anzeige/Auswahl ohne Regler-
// Zwischenzustand); Squelch und Speed dagegen nur, wenn der Nutzer das
// jeweilige Element nicht gerade bedient (Fokus oder lokale Aenderung vor
// weniger als LOCAL_GUARD_MS - siehe recentlyTouched) - kein "Regler-Kampf"
// zwischen SSE-Push und laufender Eingabe.
// T-510: letzter bekannter AFC-Zustand, von checkModeBwCombo() mitgelesen
// (Warnung bei USB/LSB-Wahl mit aktiver AFC).
let afcOn = false, afcWirksam = false;
function syncDeviceParams(p) {
  if (p.dem) {
    const sel = el('modeSelect');
    if (sel.value !== p.dem && [...sel.options].some(o => o.value === p.dem)) {
      sel.value = p.dem;
      checkModeBwCombo();
    }
  }
  if (p.band_hz) {
    const sel = el('bwSelect');
    const v = String(p.band_hz);
    if (sel.value !== v && [...sel.options].some(o => o.value === v)) {
      sel.value = v;
      updateBwBandZ();
      resetSMeter();
      checkModeBwCombo();
    }
  }
  if (!recentlyTouched('squelchOn') && !recentlyTouched('squelchThr')) {
    const sqOn = el('squelchOn');
    if (typeof p.squelch_on === 'boolean' && sqOn.checked !== p.squelch_on) {
      sqOn.checked = p.squelch_on;
    }
    const sqThr = el('squelchThr');
    if (typeof p.squelch_thr === 'number' && parseFloat(sqThr.value) !== p.squelch_thr) {
      sqThr.value = p.squelch_thr;
      el('squelchLabel').textContent = p.squelch_thr + ' dBµV';
    }
  }
  // T-710: Guard ueber BEIDE Bedienpaare - sonst ueberschreibt der SSE-Push
  // den gerade bedienten Regler der jeweils anderen Ansicht.
  if (!GAIN_MODE_IDS.some(id => recentlyTouched(id))
      && !GAIN_MGC_IDS.some(id => recentlyTouched(id))) {
    const gainMode = (typeof p.gain_mode === 'string') ? p.gain_mode : el('gainMode').value;
    const gainMgc = (typeof p.gain_mgc === 'number'
                     && parseFloat(el('gainMgc').value) !== p.gain_mgc) ? p.gain_mgc : null;
    paintGainUi(gainMode, gainMgc);
  }
  if (p.speed && !recentlyTouched('speedSelect')) {
    const sel = el('speedSelect');
    if (sel.value !== p.speed && [...sel.options].some(o => o.value === p.speed)) {
      sel.value = p.speed;
      dscanSpeedIndex = dscanSpeeds.indexOf(p.speed);
      updateMtimeVisibility();
    }
  }
  if (typeof p.afc_on === 'boolean') {
    afcOn = p.afc_on;
    afcWirksam = !!p.afc_wirksam;
    checkModeBwCombo();
  }
}

// T-706: Plausibilitaet Modulation/ZF-Bandbreite. Annex B meldet
// "Bandwidth too high with SSB" bzw. "... with CW" (nur bis 9 kHz); IQ ist
// in _configure_ifpan auf 15 kHz begrenzt. Rein informativ: der Request geht
// trotzdem raus, das Geraet lehnt beim naechsten Zoom-Aufbau ab und der
// T-703-Readback zieht die Selects nach - dann verschwindet der Hinweis.
// Aufruf aus beiden onchange-Handlern sowie aus syncZoomSettings /
// syncDeviceParams (Wechsel am Frontpanel).
const IFPAN_BW_MAX_HZ = { USB: 9000, LSB: 9000, CW: 9000, IQ: 15000 };
// T-1813d1: checkModeBwCombo() laeuft auch aus syncZoomSettings/syncDeviceParams,
// also bei jedem eintreffenden Frame. Der Readback zieht die Selects zurueck, die
// Bedingung ist nicht mehr erfuellt und der Hinweis verschwand, bevor man ihn
// lesen konnte. Gleiche Loesung wie noticeHoldUntil fuer #meta-o.
let bwHintHoldUntil = 0;
function checkModeBwCombo() {
  const mode = el('modeSelect').value;
  const bwHz = parseInt(el('bwSelect').value);
  const maxHz = IFPAN_BW_MAX_HZ[mode];
  const hint = el('bwHint');
  if (maxHz && bwHz > maxHz) {
    hint.textContent = (bwHz / 1000) + ' kHz not possible with ' + mode + ' (max. '
      + (maxHz / 1000) + ' kHz)';
    hint.hidden = false;
    bwHintHoldUntil = Date.now() + 8000;
  } else if (Date.now() >= bwHintHoldUntil) {
    hint.hidden = true;
  }
  // T-510: AFC-Anzeige. Die SSB-Warnung hat Vorrang vor der Zustandsanzeige
  // aus syncDeviceParams - deshalb hier als eigener Zweig VOR der
  // Zustandsanzeige, beide ueber dieselben Modulvariablen afcOn/afcWirksam.
  const afcHint = el('afcHint');
  if ((mode === 'USB' || mode === 'LSB') && afcOn) {
    afcHint.textContent = 'AFC is on - USB/LSB is rejected by the device '
      + '(-221 No AFC with SSB). Switch AFC off at the front panel.';
    afcHint.hidden = false;
  } else if (afcOn) {
    afcHint.textContent = afcWirksam ? 'AFC active' : ('AFC on, has no effect in ' + mode);
    afcHint.hidden = false;
  } else {
    afcHint.hidden = true;
  }
}
checkModeBwCombo();   // Startzustand (Selects sind statisch vorbelegt)

el('modeSelect').onchange = e => {
  checkModeBwCombo();
  fetch(`/api/mode?value=${encodeURIComponent(e.target.value)}`);
};
el('bwSelect').onchange = e => {
  fetch(`/api/bw?value=${e.target.value}`);
  checkModeBwCombo();
  // sofortige Reaktion des BW-Streifens auf die neue Wahl, mit dem
  // zuletzt bekannten Span (die Wahl selbst wirkt am Gerät erst beim
  // nächsten Zoom-Aufbau, siehe Kommentar an updateBwBandZ).
  updateBwBandZ();
  resetSMeter();
};

// T-702: Squelch im IFPAN-Zoom (Annex C) - wie modeSelect/bwSelect: bei
// Änderung sofort am Gerät setzen.
function sendSquelch() {
  markLocalInteraction('squelchOn');
  markLocalInteraction('squelchThr');
  const on = el('squelchOn').checked ? 1 : 0;
  const thr = el('squelchThr').value;
  fetch(`/api/squelch?on=${on}&thr=${thr}`).then(reportIfLocked);
}
el('squelchThr').oninput = e => {
  // Label live aktualisieren, während gezogen wird - wie beim spanSlider
  // nicht bei jedem Zwischenschritt senden. markLocalInteraction() bereits
  // hier (nicht erst bei onchange), damit die T-703-Geraeteabgleichung den
  // Regler waehrend des Ziehens nicht "zurueckreisst".
  markLocalInteraction('squelchThr');
  el('squelchLabel').textContent = e.target.value + ' dBµV';
};
el('squelchThr').onchange = () => {
  // Erst beim Loslassen des Reglers tatsächlich anwenden.
  sendSquelch();
};
el('squelchOn').onchange = () => {
  sendSquelch();
};

// Squelch-Schwelle an den Skala-min-Regler der Zoom-Ansicht koppeln (nur bei
// aktivem squelchFollow): alles unterhalb Skala min wird stummgeschaltet.
// Idempotent - sendet /api/squelch nur, wenn sich der Reglerwert dadurch
// tatsächlich ändert.
function syncSquelchToScaleMin() {
  if (!el('squelchFollow').checked) return;
  const minZ = (activeScaleView === 'z') ? scaleMin : scaleState.z.min;
  const clamped = Math.max(-30, Math.min(110, Math.round(minZ)));
  if (parseInt(el('squelchThr').value, 10) !== clamped) {
    el('squelchThr').value = clamped;
    el('squelchLabel').textContent = clamped + ' dBµV';
    sendSquelch();
  }
}
el('squelchFollow').onchange = e => {
  if (e.target.checked) {
    el('squelchThr').disabled = true;
    syncSquelchToScaleMin();
  } else {
    el('squelchThr').disabled = false;   // letzter Wert bleibt stehen
  }
};

// T-905/T-710: Verstaerkungsregelung (AGC/MGC, Annex C). Es gibt ZWEI
// Bedienpaare fuer denselben Geraetezustand: gainMode/gainMgc im IFPAN-Zoom
// und dscanGainMode/dscanGainMgc im D-SCAN-Panel. Jede Aenderung wird sofort
// gesendet (wie beim Squelch) und in das jeweils andere Paar gespiegelt -
// sonst zeigte die andere Ansicht nach einem Ansichtswechsel einen alten
// Stand, bis der naechste SSE-Push (syncDeviceParams) eintrifft.
const GAIN_MODE_IDS = ['gainMode', 'dscanGainMode'];
const GAIN_MGC_IDS = ['gainMgc', 'dscanGainMgc'];

// Beide Bedienpaare auf denselben Stand bringen. mgc === null laesst den
// Pegel unangetastet (reiner Moduswechsel). Der MGC-Regler ist gesperrt,
// solange AGC laeuft ODER ein Long-Run-Job den Empfaenger belegt
// (longrunLocked, siehe setLongrunLock).
function paintGainUi(mode, mgc) {
  GAIN_MODE_IDS.forEach(id => { if (el(id).value !== mode) el(id).value = mode; });
  GAIN_MGC_IDS.forEach(id => { el(id).disabled = (mode === 'AGC') || longrunLocked; });
  if (mgc !== null) {
    el('gainMgc').value = mgc;
    el('gainMgcLabel').textContent = mgc + ' dBµV';
    el('dscanGainMgc').value = mgc;
    el('dscanGainMgcLabel').textContent = mgc + ' dBµV';
  }
}

// T-717: Parameter heisst modeArg, NICHT mode - die globale Variable mode
// haelt die Ansicht ('overview'|'zoom') und wuerde von einem Parameter
// gleichen Namens verdeckt; die Abfrage unten pruefte dann den
// Verstaerkungsmodus statt der Ansicht.
function sendGain(modeArg, mgc) {
  GAIN_MODE_IDS.forEach(id => markLocalInteraction(id));
  GAIN_MGC_IDS.forEach(id => markLocalInteraction(id));
  // Eine Verstaerkungsaenderung beendet den laufenden Sweep und baut ihn neu
  // auf (T-711) - genau wie Bereich/BW/Speed. Deshalb hier dasselbe Muster:
  // Tafel vormerken, gebrannt wird sie mit dem naechsten fertigen Sweep.
  // Nur bei echter Bedienung, NICHT beim Ruecklesen ueber syncDeviceParams
  // (das ruft paintGainUi direkt auf und kommt hier nie vorbei).
  if (mode === 'overview') {
    pendingHeader = { text: composeParameterAnnotation(), newStartHz: segmentStartHz,
                      newStopHz: segmentStopHz, isInitial: false };
  }
  fetch(`/api/gain?mode=${modeArg}&mgc=${mgc}`).then(reportIfLocked);
}

// Handler fuer ein Bedienpaar; zweimal aufgerufen (Zoom, D-SCAN-Panel).
function bindGainControls(modeId, mgcId) {
  el(mgcId).oninput = e => {
    // Label live aktualisieren, während gezogen wird - wie beim Squelch-Regler
    // nicht bei jedem Zwischenschritt senden. Das Gegenstueck zieht mit.
    GAIN_MGC_IDS.forEach(id => markLocalInteraction(id));
    paintGainUi(el(modeId).value, e.target.value);
  };
  el(mgcId).onchange = () => {
    // Erst beim Loslassen des Reglers tatsächlich anwenden.
    sendGain(el(modeId).value, el(mgcId).value);
  };
  el(modeId).onchange = () => {
    paintGainUi(el(modeId).value, null);
    sendGain(el(modeId).value, el(mgcId).value);
  };
}
bindGainControls('gainMode', 'gainMgc');
bindGainControls('dscanGainMode', 'dscanGainMgc');

// Startzustand: der Server rendert die Selects mit dem Default AGC; damit die
// MGC-Regler beider Ansichten dazu passen, einmal initial durchmalen (danach
// uebernimmt syncDeviceParams den Geraetestand).
paintGainUi(el('gainMode').value, null);

// Audio-Aufnahme im IFPAN-Zoom: Aufzeichnung auf die Platte (siehe
// DeviceController._start_recording/_stop_recording), hier wird nur der
// An/Aus-Zustand umgeschaltet. Das Live-Mithoeren im Browser ist davon
// unabhaengig (siehe monitor weiter oben) - beide teilen sich nur den
// AUD-Tag auf dem Display-UdpPath (Refcount im Controller).
// Media-Glyphen fuer die einheitlichen Aufnahme-Knoepfe (siehe CSS .rec-btn).
// T-1407b: Inline-SVG statt U+23FA/U+23F9 - Windows rendert die als Emoji.
const GLYPH_REC = SVG_REC;
const GLYPH_STOP = SVG_STOP;

// Audio-Aufnahme (WAV) im IFPAN-Zoom. Bleibt waehrend einer Spektrum-Bin-
// Aufnahme BEDIENBAR - Audio laeuft auf dem Display-Pfad, die Bins auf einem
// eigenen UdpPath (getrennte Sequenzzaehler), sie stoeren sich nicht.
// Darstellung eines Aufnahme-Knopfs: der Glyph steht in einem eigenen
// <span class="rec-dot"> (bleibt im Ruhezustand rot, waehrend Rahmen/Schrift
// blau sind, siehe CSS), dann das Label und - waehrend der Aufnahme - die
// verstrichene Zeit MM:SS in <span class="rec-time">.
function fmtElapsed(ms) {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60), s = total % 60;
  const pad = v => String(v).padStart(2, '0');
  return `${pad(m)}:${pad(s)}`;
}
function renderRecBtn(btn, active, label, startMs) {
  const glyph = active ? GLYPH_STOP : GLYPH_REC;
  const time = active ? ` <span class="rec-time">${fmtElapsed(Date.now() - startMs)}</span>` : '';
  btn.innerHTML = `<span class="rec-dot">${glyph}</span> ${label}${time}`;
}

// Verwaltet einen Aufnahme-Knopf: Umschalten, Server-Aufruf (?state=on|off),
// Sekunden-Timer fuer die Zeitanzeige und ein optionaler onToggle-Callback
// (z. B. die Geometrie-Sperre der Spektrum-Aufnahme). Kapselt seinen Zustand,
// damit es keine losen recording/spectrumRecording-Flags mehr braucht.
function makeRecButton(btnId, label, url, onToggle, onResponse) {
  const btn = el(btnId);
  let active = false, startMs = 0, timer = null;
  function paint() { renderRecBtn(btn, active, label, startMs); }
  function set(a) {
    active = a;
    btn.classList.toggle('active', active);
    if (active) {
      startMs = Date.now();
      timer = setInterval(paint, 1000);
    } else if (timer) {
      clearInterval(timer);
      timer = null;
    }
    paint();
    if (onToggle) onToggle(active);
    // onResponse (T-803, Teil D): optionaler Callback fuer die JSON-Antwort -
    // bisher wurde die Antwort nirgends ausgewertet (fire-and-forget).
    const req = fetch(`${url}?state=${active ? 'on' : 'off'}`);
    if (onResponse) {
      req.then(r => r.json()).then(onResponse).catch(() => {});
    }
  }
  btn.onclick = () => set(!active);
  paint();
  return { set, isActive: () => active };
}

// Waehrend der Spektrum-Bin-Aufnahme (IFPAN-BW-Fenster, siehe
// eb200_core/spectrum_store.py) werden die GEOMETRIEVERAENDERNDEN
// Bedienelemente gesperrt (Span/Modulation/BW/Zurueck) und Drag/Klick auf dem
// Zoom-Canvas stillgelegt, damit die aufgenommene Fenster-Geometrie konstant
// bleibt. Der Audio-Knopf bleibt bewusst frei (getrennter UdpPath).
//
// lockRecordButtons (Schritt 4c, Long-Run-Jobs): zusaetzlich Audio-/Spektrum-
// Aufnahme-Knopf selbst sperren (/api/record, /api/record-spectrum sind
// waehrend eines Jobs per 423 gesperrt, siehe _LOCKED_GET_PATHS - das hier ist
// nur die visuelle Entsprechung). NICHT der Standardfall: der Aufruf aus
// makeRecButton()/spectrumRec's eigenem onToggle (Ad-hoc-Bin-Aufnahme) laesst
// dieses Flag bewusst weg, sonst wuerde die Aufnahme ihren EIGENEN Stop-Knopf
// sperren, sobald sie startet.
function setGeometryLock(locked, { lockRecordButtons = false } = {}) {
  el('spanSlider').disabled = locked;
  el('modeSelect').disabled = locked;
  el('bwSelect').disabled = locked;
  el('backBtn').disabled = locked;
  specZ.classList.toggle('rec-lock', locked);
  wfZ.classList.toggle('rec-lock', locked);
  if (lockRecordButtons) {
    el('recordBtn').disabled = locked;
    el('spectrumBtn').disabled = locked;
  }
}

// Audio-Mithoeren (Web Audio API). Holt /audio/stream als endlosen
// Bytestrom (rohe 16-Bit-Big-Endian-Mono-PCM, 16 kHz - siehe AUDIO_REMOTE_MODE)
// und plant die Stuecke lueckenlos hintereinander. Kein <audio>-Element:
// das wuerde einen Endlos-Stream unkontrolliert puffern und Sekunden
// nachlaufen. Rueckstand > MONITOR_MAX_LAG_S -> Spielzeiger neu setzen
// (altes Audio verwerfen). Der AudioContext darf laut Autoplay-Policy erst
// nach einer Nutzergeste klingen - der Zoom-Klick in der Uebersicht ist
// eine; beim extern ausgeloesten Zoom (SDR-Wasserfall :8030) bleibt er ggf.
// 'suspended', der Knopf zeigt das (.blocked) und ein Klick holt es nach.
// Vorliebe (an/aus) liegt in localStorage, Standard AN.
// Kein eigener Schalt-Endpunkt: offener /audio/stream = an, geschlossen = aus.
const MONITOR_RATE_HZ = 16000;
const MONITOR_MAX_LAG_S = 0.4;
const MONITOR_PREF_KEY = 'eb200.monitor';
const monitor = (() => {
  const btn = el('monitorBtn');
  let ctx = null, nextT = 0, abort = null, running = false, carry = null;
  let pref = localStorage.getItem(MONITOR_PREF_KEY) !== '0';   // Standard AN

  function paint() {
    const blocked = running && ctx && ctx.state !== 'running';
    btn.classList.toggle('active', running && !blocked);
    btn.classList.toggle('blocked', !!blocked);
    btn.innerHTML = blocked ? SVG_SPK + ' Monitor <small>(click to start)</small>'
                            : SVG_SPK + ' Monitor';
    btn.title = pref ? 'Monitoring is on (starts automatically on zoom)'
                     : 'Monitoring is off';
  }

  function ensureCtx() {
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: MONITOR_RATE_HZ }); }
      catch (e) { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      ctx.onstatechange = paint;
    }
    if (ctx.state === 'suspended') ctx.resume().then(paint, paint);
    return ctx;
  }

  // Ein Stueck Big-Endian-PCM in einen AudioBuffer wandeln und an die
  // laufende Zeitachse anhaengen. Chunk-Grenzen koennen ein Sample teilen:
  // ein ungerades Rest-Byte wird in 'carry' fuers naechste Stueck behalten.
  function feed(u8) {
    if (carry) { const m = new Uint8Array(carry.length + u8.length); m.set(carry); m.set(u8, carry.length); u8 = m; carry = null; }
    const n = u8.length >> 1;
    if (u8.length & 1) carry = u8.slice(n * 2);
    if (n === 0) return;
    const dv = new DataView(u8.buffer, u8.byteOffset, n * 2);
    const buf = ctx.createBuffer(1, n, MONITOR_RATE_HZ);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = dv.getInt16(i * 2, false) / 32768;   // false = Big-Endian
    const now = ctx.currentTime;
    if (nextT < now + 0.02 || nextT > now + MONITOR_MAX_LAG_S) nextT = now + 0.05;   // Unterlauf ODER Rueckstand: neu aufsetzen
    const src = ctx.createBufferSource();
    src.buffer = buf; src.connect(ctx.destination); src.start(nextT);
    nextT += buf.duration;
  }

  async function pump(signal) {
    let resp;
    try { resp = await fetch('/audio/stream', { signal, cache: 'no-store' }); }
    catch (e) { return; }
    const reader = resp.body.getReader();
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done || signal.aborted) break;
        if (ctx.state === 'running') feed(value);   // suspended: Daten verwerfen statt aufstauen
      }
    } catch (e) { /* AbortError beim Stop - gewollt */ }
  }

  function start() {
    if (running) { ensureCtx(); paint(); return; }
    running = true; carry = null; nextT = 0;
    ensureCtx();
    abort = new AbortController();
    pump(abort.signal);   // das Oeffnen von /audio/stream IST der Einschaltwunsch (Server-Seite)
    paint();
  }

  function stop() {
    if (!running) return;
    running = false;
    if (abort) { abort.abort(); abort = null; }   // Stream schliessen = Ausschaltwunsch (Server prueft Client-Zahl)
    paint();
  }

  // Klick: schaltet die VORLIEBE um und setzt sie sofort um. Sonderfall:
  // laeuft es bereits, ist aber vom Browser blockiert (.blocked), gilt der
  // Klick als Freigabe-Geste, nicht als Ausschalten.
  btn.onclick = () => {
    if (running && ctx && ctx.state !== 'running') { ensureCtx(); paint(); return; }
    pref = !pref;
    localStorage.setItem(MONITOR_PREF_KEY, pref ? '1' : '0');
    if (pref) start(); else stop();
  };

  // Vom Zoom-Eintritt gerufen: nur starten, wenn die Vorliebe AN ist.
  function autoStart() { if (pref) start(); else paint(); }

  paint();
  return { start, stop, autoStart, isRunning: () => running };
})();

const audioRec = makeRecButton('recordBtn', 'Audio', '/api/record', null);
const spectrumRec = makeRecButton('spectrumBtn', 'Spectrum', '/api/record-spectrum', setGeometryLock, status => {
  // T-803 (Teil D): Deep-Link zur Auswertung der soeben geschlossenen
  // .specbin-Datei in :8040, Muster ist die Long-Run-Ende-Zeile (#meta-o).
  // Bleibt status.specbin_path leer (Timeout beim Warten auf den Loop-Thread,
  // siehe stop_spectrum_recording_and_wait), wird schlicht kein Link gezeigt.
  if (status && status.specbin_path) {
    const host = window.location.hostname;
    el('meta-z').innerHTML =
      `Recording finished - analysis: <a href="http://${host}:8040/?path=${encodeURIComponent(status.specbin_path)}" target="eb200_spectrum">http://${host}:8040/</a>`;
  }
});

el('backBtn').onclick = () => {
  mode = 'overview';
  awaitingOverview = true;   // nachlaufende IFPAN-Frames nicht mehr auto-zoomen
  el('zoomView').style.display = 'none';
  el('overviewView').style.display = 'block';
  // (c): Header zurueck auf Uebersicht
  document.body.classList.remove('zoom');
  el('viewName').textContent = 'D-SCAN';
  switchScaleView('o');   // T-701: eigener Farbskalen-Zustand der Uebersicht
  el('bw-band-z').style.display = 'none';
  resetSMeter();
  // Server stoppt eine laufende Aufnahme ohnehin serverseitig über den
  // finally-Pfad von _run_zoom_loop - hier nur den Button visuell
  // zurücksetzen, damit er beim nächsten Zoom nicht fälschlich "aktiv" zeigt.
  // Server stoppt eine laufende Aufnahme ohnehin ueber den finally-Pfad von
  // _run_zoom_loop; hier nur den Sichtzustand zuruecksetzen. set(false) stoppt
  // zugleich die Zeitanzeige-Timer und loest die Geometrie-Sperre. Nur bei
  // tatsaechlich laufender Aufnahme aufrufen (spart einen leeren state=off).
  if (audioRec.isActive()) audioRec.set(false);
  if (spectrumRec.isActive()) spectrumRec.set(false);
  // Server stoppt das Mithoeren ebenfalls ueber den finally-Pfad von
  // _run_zoom_loop; hier Stream abbrechen und Sichtzustand zuruecksetzen
  // (state=off wird trotzdem geschickt - idempotent, kostet nichts).
  monitor.stop();
  setGeometryLock(false);   // sicherheitshalber, falls der Zustand je divergiert
  fetch('/api/overview');
};

// Bereichstext der Übersicht: Start-Stop in MHz PLUS Breite in kHz - eine
// gemeinsame Stelle für beide Aufrufer (kompletter Sweep und Partial), damit
// die Formatierung nicht auseinanderläuft. Deckt normalen D-SCAN UND aktiven
// BW-ZOOM automatisch ab, da start_hz/stop_hz stets aus dem OptionalHeader
// der Geräte-Datagramme kommen (bei BW-ZOOM also bereits der schmale,
// gerätegerechnete Bereich).
// T-707: Breitenangabe mit passender Einheit. Vorher toLocaleString('de-DE')
// in kHz -> 16000 kHz erschien als "16.000 kHz" (Tausenderpunkt liest sich
// als Dezimalkomma). Ab 1 MHz in MHz mit 3 Nachkommastellen (gleiche Form wie
// die Start-/Stopp-Angabe), darunter kHz mit einer Nachkommastelle.
function formatBreite(hz) {
  if (hz >= 1e6) return (hz / 1e6).toFixed(3) + ' MHz';
  return (hz / 1e3).toFixed(1) + ' kHz';
}
function overviewRangeText(f) {
  const breiteHz = f.stop_hz - f.start_hz;
  return `${(f.start_hz/1e6).toFixed(3)}-${(f.stop_hz/1e6).toFixed(3)} MHz `
       + `(width ${formatBreite(breiteHz)})`;
}
function updateMetaOverview(f) {
  if (Date.now() < noticeHoldUntil) return;   // laufenden Server-Hinweis nicht ueberschreiben
  // Schrittweite/Punkte/Sweep-Zeitstempel stehen bereits in der Aktivitäts-
  // zeile darunter (#activity-o, siehe updateSweepStats) - hier bewusst nur
  // noch der Bereich, identisch zu updateMetaOverviewPartial, damit #meta-o
  // am Sweep-Ende nicht redundant wird.
  el('meta-o').textContent = overviewRangeText(f);
}
function updateMetaOverviewPartial(f) {
  if (Date.now() < noticeHoldUntil) return;   // laufenden Server-Hinweis nicht ueberschreiben
  // Der Fortschritt eines laufenden Sweeps steht (bei MTIM, wo ein Sweep
  // Minuten dauern kann) als Text in #sweepProgress im Panel - hier bleibt
  // nur noch der Bereich stehen, damit #meta-o waehrend des Sweeps nicht
  // staendig "hin und her springt".
  el('meta-o').textContent = overviewRangeText(f);
}

// Text der Aktivitätszeile: zeigt IMMER den zuletzt FERTIGEN Sweep (nur aus
// dem 'dscan'-complete-Zweig aufgerufen) - dieselben Kennzahlen, die der
// Controller frueher je Sweep auf die Konsole geschrieben hat (keine
// Konsolenzeile mehr, siehe DScanUpdate). Bewusst NICHT bei
// jedem Paket aktualisiert, sonst würden die Zahlen während des laufenden
// Sweeps ständig "zappeln"; die festen .fld-Breiten (siehe CSS) sorgen
// zusätzlich dafür, dass sich die Labels nicht verschieben, wenn sich die
// Stellenzahl einer Zahl ändert. Kein Padding im String nötig - die feste
// Positionierung kommt allein aus den .fld-Breiten.
function updateSweepStats(frame) {
  el('act-points').textContent = frame.total;
  el('act-secs').textContent = frame.elapsed_s.toFixed(2);
  el('act-rate').textContent = frame.elapsed_s > 0 ? Math.round(frame.total / frame.elapsed_s) : '∞';
}
// ---- S-Meter: max. Pegel im BW-Fenster, mit Hoechstwert-Halt --------------
// Feste, absolute dBµV-Skala (siehe Achse im HTML). Bei Bedarf anpassen -
// dann auch die Achsenbeschriftung im SVG mitziehen.
const SMETER_MIN_DBUV = -40;      // linkes Balken-/Achsenende
const SMETER_MAX_DBUV = 80;       // rechtes Balken-/Achsenende
const SMETER_SMOOTH = 0.2;        // Traegheit der Balken-Nadel (0..1, klein=ruhiger)

let smeterDisplay = null;   // geglaettete Balken-Nadel (aktuelle Staerke)
let smeterPeak = null;      // gehaltener Hoechstwert - steigt nur, faellt nie

function resetSMeter() {
  smeterDisplay = null; smeterPeak = null;
  el('smeter-bar-mask').style.width = '100%';
  el('smeter-peak-marker').style.display = 'none';
  el('smeter-max').textContent = 'Max –';
}

// Maximaler Pegel (dBµV) im Fenster [center-BW/2 .. center+BW/2]. IFPAN ist
// linear und zentriert: levels[i] laeuft von center-span/2 bis center+span/2.
// Faellt kein Bin ins Fenster (sehr schmale BW), wird der mittlere Bin genommen.
function peakInBwWindow(frame) {
  const n = frame.levels.length;
  if (!n) return null;
  const half = parseInt(el('bwSelect').value) / 2;
  const startHz = frame.center_hz - frame.span_hz / 2;
  const stepHz = frame.span_hz / (n - 1);
  let peak = null;
  for (let i = 0; i < n; i++) {
    const v = frame.levels[i];
    if (v === null || v === undefined) continue;
    if (Math.abs((startHz + i * stepHz) - frame.center_hz) <= half) {
      if (peak === null || v > peak) peak = v;
    }
  }
  if (peak === null) {
    const mid = frame.levels[Math.floor(n / 2)];
    peak = (mid === null || mid === undefined) ? null : mid;
  }
  return peak;
}

function updateSMeter(frame) {
  const peak = peakInBwWindow(frame);
  if (peak === null) return;
  // Balken-Nadel: symmetrisch geglaettet, folgt der Aktivitaet ohne zu zappeln.
  smeterDisplay = (smeterDisplay === null) ? peak
                : smeterDisplay + (peak - smeterDisplay) * SMETER_SMOOTH;
  // Hoechstwert-Halt: nur nach oben, kein Abfall. Reset per Klick auf die
  // Max-Anzeige bzw. automatisch bei Zoom-/BW-/Span-Wechsel.
  if (smeterPeak === null || peak > smeterPeak) smeterPeak = peak;

  const frac = v => Math.max(0, Math.min(1, (v - SMETER_MIN_DBUV) / (SMETER_MAX_DBUV - SMETER_MIN_DBUV)));
  el('smeter-bar-mask').style.width = ((1 - frac(smeterDisplay)) * 100) + '%';
  const marker = el('smeter-peak-marker');
  marker.style.display = 'block';
  marker.style.left = (frac(smeterPeak) * 100) + '%';
  el('smeter-max').textContent = `Max ${smeterPeak.toFixed(1)} dBµV`;
}
// Klick auf die Max-Anzeige setzt den gehaltenen Hoechstwert zurueck.
el('smeter-max').onclick = resetSMeter;
// ---------------------------------------------------------------------------

function updateMetaZoom(f) {
  const ts = isoTime(new Date(f.t * 1000));
  // Analog zur Übersicht: Anfang-Endfrequenz des IFPAN-Fensters plus Breite
  // in kHz (aus center/span abgeleitet - IFPAN ist stets zentriert).
  const zStartMHz = (f.center_hz - f.span_hz / 2) / 1e6;
  const zStopMHz = (f.center_hz + f.span_hz / 2) / 1e6;
  // T-708: solange eine Sperr-/Fehlermeldung der Feinabstimmung steht,
  // #meta-z nicht sofort vom naechsten Frame ueberschreiben lassen
  // (gleiche Idee wie noticeHoldUntil fuer #meta-o).
  if (Date.now() >= noticeHoldUntilZ) {
    el('meta-z').textContent = `${(f.center_hz/1e6).toFixed(4)} MHz, `
      + `${zStartMHz.toFixed(4)}-${zStopMHz.toFixed(4)} MHz `
      + `(width ${formatBreite(f.span_hz)}), `
      + `${f.levels.length} points, last frame ${ts}`;
  }
  // Mittenfrequenz zusätzlich groß über den Reglern anzeigen. T-708: sechs
  // Nachkommastellen (Hz-genau), damit 1/10-Hz-Feinabstimm-Schritte sichtbar
  // sind; solange ein optimistischer Zielwert angezeigt wird (Radeln laeuft),
  // nicht vom noch alten Frame ueberschreiben. Formatierung seit T-721
  // zentral in formatCenterMHz().
  if (Date.now() >= tuneHoldUntilMs) {
    el('centerFreqBig').textContent = formatCenterMHz(f.center_hz);
  }
}

// zeichnet den hellen BW-Streifen über wf-z: Breite = eingestellte
// Bandbreite (bwSelect) relativ zum aktuellen Span, mittig um center_hz
// (= die Bildmitte, x=CANVAS_W/2, da IFPAN stets zentriert dargestellt
// wird). Zeigt so auf einen Blick, wie eng die BW im Vergleich zum Span
// liegt - unabhängig davon, dass die gewählte BW am Gerät erst beim
// nächsten Zoom-Aufbau tatsächlich wirkt (siehe _configure_ifpan): für die
// "wie eng"-Voranzeige ist genau das gewollt.
function updateBwBandZ() {
  const band = el('bw-band-z');
  if (!latestZ) return;
  const bwHz = parseInt(el('bwSelect').value);
  const widthPx = Math.max(2, Math.min(CANVAS_W, (bwHz / latestZ.span_hz) * CANVAS_W));
  const leftPx = Math.max(0, Math.min(CANVAS_W, CANVAS_W / 2 - widthPx / 2));
  band.style.display = 'block';
  band.style.left = leftPx + 'px';
  band.style.width = widthPx + 'px';
  // vertikal exakt über wf-z legen - robust gegen den Canvas-Rahmen, statt
  // die Höhe/Position hart zu verdrahten.
  band.style.top = wfZ.offsetTop + 'px';
  band.style.height = wfZ.offsetHeight + 'px';
}

// hält die bisher in diesem (noch laufenden) D-SCAN-Sweep eingetroffenen
// dscanDisplayLevels ist die aktuell angezeigte Kurve über den GESAMTEN
// Sweep-Bereich - persistent über mehrere Sweeps hinweg. Jeder neue Sweep
// überschreibt sie Punkt für Punkt an exakt derselben Stelle (offset),
// statt Werte anzuhängen. Dadurch zeigt die Anzeige zu jedem Zeitpunkt
// genau EINE durchgehende Kurve: bereits neu gescannte Abschnitte zeigen
// den aktuellen Wert, noch nicht erreichte Abschnitte zeigen weiterhin
// den Wert des letzten kompletten Sweeps (keine Lücke, kein "Reset auf
// leer"), bis auch sie überschrieben werden.
let dscanDisplayLevels = [];
let dscanDisplayTotal = null;
let dscanProgressCount = 0;   // Fortschritt NUR für die Prozent-Anzeige (Balken)
// Startzeitpunkt des laufenden Sweeps (Client-Empfang) - steuert NUR die
// Sichtbarkeit von #sweepProgress (erst nach 5 s, gegen Flackern bei
// schnellen LOW/NORM/HIGH-Sweeps); die bestehende Balkenanzeige
// (updateSweepStats) bleibt davon unberuehrt und laeuft wie bisher immer mit.
let dscanSweepStartMs = null;
// mitgeführter, aktuell ANGEZEIGTER Frequenzbereich der Übersicht, damit
// Tooltip/Drag auch während eines laufenden Sweeps stimmen.
let dscanDisplayStartHz = null;
let dscanDisplayStopHz  = null;

// ---- Anzeige-Scheduler: Datenempfang und Zeichnen entkoppelt -------------
// Bisher zeichnete evtSource.onmessage bei JEDEM Datagramm Spektrum und
// Wasserfall-Ausschnitt komplett neu - bei D-SCAN-Teilpaketen und IFPAN-
// Frames weit oefter, als der Monitor Bilder zeigt. Da Browser gleich-
// sinnige Tabs (Status-Seite, Analyse-Server) in denselben Renderer-Prozess
// mit EINEM Haupt-Thread legen, blockierte das deren Bedienung
// (Hardware-Befund 2026-08-22: <details>-Aufklappen bis 8 s verzoegert).
//
// Regel: Alles, was ZUSTAND fortschreibt (Puffer, historyCanvas/wfZ-Zeilen,
// Header, Zeitmarken, S-Meter-Spitzenwert), bleibt im onmessage-Handler -
// das laeuft auch im verdeckten Tab zuverlaessig weiter, weil EventSource
// nicht gedrosselt wird. Die Historie ist damit IMMER lueckenlos.
// Nur das SICHTBARE Malen (Spektrum, Viewport-Blit, im Zoom Achse/Streifen/
// Meta) laeuft hier: hoechstens einmal pro Bildschirmframe und nicht oefter
// als alle PAINT_MIN_INTERVAL_MS. Im verdeckten Tab haelt der Browser
// requestAnimationFrame an; die Dirty-Flags bleiben gesetzt, beim
// Sichtbarwerden zeigt das erste Frame sofort den aktuellen Stand.
const PAINT_MIN_INTERVAL_MS = 50;   // ~20 Bilder/s - fuer einen wachsenden Sweep voellig ausreichend
let paintDirtyO = false, paintDirtyZ = false, paintPending = false, lastPaintMs = 0;

function requestPaint(which) {
  if (which === 'o') paintDirtyO = true; else paintDirtyZ = true;
  if (!paintPending) { paintPending = true; requestAnimationFrame(paintFrame); }
}
function paintFrame(nowMs) {
  if (nowMs - lastPaintMs < PAINT_MIN_INTERVAL_MS) { requestAnimationFrame(paintFrame); return; }
  paintPending = false;
  lastPaintMs = nowMs;
  if (paintDirtyO) { paintDirtyO = false; if (mode === 'overview') paintOverviewNow(); }
  if (paintDirtyZ) { paintDirtyZ = false; if (mode === 'zoom' && latestZ !== null) paintZoomNow(latestZ); }
}
function paintOverviewNow() {
  if (!dscanDisplayLevels) return;
  drawSpectrum(ctxSpecO, specO, dscanDisplayLevels);
  renderViewportO();
}
function paintZoomNow(frame) {
  drawSpectrum(ctxSpecZ, specZ, frame.levels);
  drawAxis(ctxAxisZ, axisZ, frame.center_hz - frame.span_hz / 2, frame.center_hz + frame.span_hz / 2);
  updateKnownStrip('known-z', frame.center_hz - frame.span_hz / 2, frame.center_hz + frame.span_hz / 2);
  updateMetaZoom(frame);
  updateBwBandZ();
}

const evtSource = new EventSource('/events');
evtSource.onmessage = ev => {
  if (paused) return;
  const frame = JSON.parse(ev.data);

  if (frame.type === 'notice') {
    // Server-Hinweis (Verifikation/Watchdog) - in der Meta-Zeile zeigen
    // und NICHT vom naechsten Sweep-Ende sofort wieder ueberschreiben
    // lassen: updateMetaOverview() prueft noticeHoldUntil.
    el('meta-o').textContent = (frame.level === 'warn' ? '⚠ ' : '') + frame.text;
    noticeHoldUntil = Date.now() + 15000;
    return;
  }

  if (frame.type === 'device_params') {
    // T-703: periodischer SCPI-Poll-Stand bzw. Initial-Push bei
    // Verbindungsaufbau - deckt auch die Uebersicht/den Reload-Fall ab
    // (syncZoomSettings oben laeuft nur pro ifpan-Frame, also nur im Zoom).
    syncDeviceParams(frame);
    return;
  }

  if (frame.type === 'dscan_partial') {
    if (mode !== 'overview') return;
    dscanDisplayStartHz = frame.start_hz;
    dscanDisplayStopHz  = frame.stop_hz;
    if (dscanDisplayTotal !== frame.total) {
      // Bereich/Auflösung geändert - alte Werte sind nicht mehr gültig,
      // Anzeige-Puffer komplett neu anlegen (zunächst leer). Die gerade
      // entstehende Wasserfallzeile (Zeile 0 des Historienpuffers)
      // ebenfalls sofort leeren, statt bis zum nächsten Sweep-Ende zu
      // warten.
      dscanDisplayLevels = new Array(frame.total).fill(null);
      dscanDisplayTotal = frame.total;
      clearHistoryTopRows(1);
      renderViewportO();
    }
    if (frame.offset === 0) {
      // Neuer Sweep beginnt - nur den Fortschrittszähler zurücksetzen.
      // dscanDisplayLevels bewusst NICHT leeren: der letzte komplette
      // Sweep bleibt sichtbar, bis der neue ihn Stück für Stück
      // überschreibt.
      dscanProgressCount = 0;
      dscanSweepStartMs = Date.now();
    }
    for (let i = 0; i < frame.segment.length; i++) {
      dscanDisplayLevels[frame.offset + i] = frame.segment[i];
    }
    dscanProgressCount += frame.segment.length;

    const [lo, hi] = currentScaleBounds(dscanDisplayLevels, 'o');
    // Live-Zeile wächst in Zeile 0 des Historienpuffers (statt direkt in
    // wf-o) - danach den sichtbaren Ausschnitt neu blitten, damit man am
    // Live-Rand den Sweep weiterhin live wachsen sieht.
    paintPartialRow(ctxHistory, historyCanvas, frame.offset, frame.segment, frame.total, lo, hi);
    requestPaint('o');   // sichtbares Malen entkoppelt, siehe Anzeige-Scheduler
    updateMetaOverviewPartial(frame);
    // Eigene Text-Fortschrittsanzeige (v. a. fuer MTIM gedacht, wo ein
    // Sweep Minuten dauern kann) - erst nach 5 s sichtbar, sonst wuerde sie
    // bei jedem normalen (Sekundenbruchteile) LOW/NORM/HIGH-Sweep nur
    // aufblitzen und wieder verschwinden.
    if (dscanSweepStartMs != null && Date.now() - dscanSweepStartMs > 5000) {
      const pct = Math.min(100, (dscanProgressCount / frame.total) * 100);
      el('sweepProgress').textContent = `Sweep: ${pct.toFixed(0)} %`;
      el('sweepProgress').style.display = 'inline';
    } else {
      el('sweepProgress').style.display = 'none';
    }

  } else if (frame.type === 'dscan') {
    latestO = frame;
    awaitingOverview = false;   // EB200 zurueck in D-SCAN -> Auto-Zoom wieder erlauben
    dscanDisplayStartHz = frame.start_hz;
    dscanDisplayStopHz  = frame.stop_hz;
    if (mode === 'overview') {
      if (dscanDisplayTotal !== frame.total) {
        dscanDisplayLevels = new Array(frame.total).fill(null);
        dscanDisplayTotal = frame.total;
      }
      if (frame.offset === 0) {
        // Sonderfall: der gesamte Sweep passte in ein einziges Paket, das
        // 'dscan' (komplett) ist gleichzeitig auch der Sweep-Start -
        // Fortschrittszähler wie im dscan_partial-Zweig zurücksetzen.
        dscanProgressCount = 0;
      }
      // letztes Segment dieses Sweeps noch einschreiben (kam nie als
      // eigenes "dscan_partial") - sonst fehlt der allerletzte
      // Teilausschnitt am rechten Rand jeder historischen Zeile.
      for (let i = 0; i < frame.segment.length; i++) {
        dscanDisplayLevels[frame.offset + i] = frame.segment[i];
      }
      const [lo, hi] = currentScaleBounds(frame.levels, 'o');
      // Zeile 0 des Historienpuffers fertigstellen (= der gerade
      // abgeschlossene Sweep).
      paintPartialRow(ctxHistory, historyCanvas, frame.offset, frame.segment, frame.total, lo, hi);
      paintAutoMarkers(frame);   // T-505: Zeile 0 = soeben fertiger Sweep
      syncDetectDelta(frame);    // T-505d
      updateDetectMinSecs();     // T-505e
      drawAxis(ctxAxisO, axisO, frame.start_hz, frame.stop_hz);
      updateKnownStrip('known-o', frame.start_hz, frame.stop_hz);
      updateMetaOverview(frame);
      // Sweep fertig: Text-Fortschrittsanzeige wieder ausblenden, zuletzt
      // gemessene Sweep-Dauer fuer die Verweildauer-Anzeige bei LOW/NORM/
      // HIGH uebernehmen (siehe updateMtimeInfo()) und die Messzeit-/
      // Sweep-Dauer-Info neu berechnen (wirkt sich nur bei Speed != MTIM
      // aus, dort haengt der Text direkt an lastSweepElapsedS).
      el('sweepProgress').style.display = 'none';
      dscanSweepStartMs = null;
      if (frame.elapsed_s != null) {
        lastSweepElapsedS = frame.elapsed_s;
        lastSweepPoints = frame.total;   // T-2006: Punktzahl desselben Sweeps
      }
      updateMtimeInfo();

      // allererster fertiger Sweep überhaupt: erzwingt einen initialen
      // Header (Achsstreifen + Parameter-Tafel, ohne Vorher-Achse), auch
      // wenn noch keine Parameteränderung stattgefunden hat.
      if (!firstHeaderDone) {
        pendingHeader = { text: composeParameterAnnotation(frame.start_hz, frame.stop_hz),
                          newStartHz: frame.start_hz, newStopHz: frame.stop_hz, isInitial: true };
        firstHeaderDone = true;
      }

      // Punkt C: falls ein Header ansteht, VOR der gerade fertigen Zeile
      // einfügen - dadurch landet der gerade abgeschlossene Sweep unterhalb
      // (älter), der als nächstes startende Sweep oberhalb (neuer) des
      // Headers. Reihenfolge von oben (=neu) nach unten (=alt):
      // [neue Daten] · Neu-Achse · Parameter-Tafel · Trennlinie · Vorher-Achse · [alte Daten].
      if (pendingHeader !== null) {
        const h = pendingHeader;
        // T-1411: Bei BW-ZOOM (und BW-Wechsel mit aktivem Zoom) stand der neue
        // Bereich beim Klick noch nicht fest - hier ist er da (frame.start_hz/
        // stop_hz kommen aus dem OptionalHeader des Geraets). Achsstreifen UND
        // Bereichszeile der Tafel werden damit gezeichnet, sonst behauptet die
        // Historie einen Bereich, den die Daten nicht haben.
        const nStartHz = h.rangeFromFrame ? frame.start_hz : h.newStartHz;
        const nStopHz = h.rangeFromFrame ? frame.stop_hz : h.newStopHz;
        const hText = h.rangeFromFrame
          ? h.text.map(p => p.key === 'range'
              ? { key: 'range', text: `Range ${(nStartHz / 1e6).toFixed(3)}–${(nStopHz / 1e6).toFixed(3)} MHz` }
              : p)
          : h.text;
        // T-2010: der Kopf gehoert nicht mehr zum alten Segment - vor dem
        // Verschieben schliessen, damit es nur noch mitwandert.
        segmente[0].offen = false;
        if (h.isInitial) {
          // erstes Segment überhaupt - keine Vorher-Achse, da noch keine
          // ältere Historie existiert, daher auch keine Trennlinie nötig.
          shiftHistory(AXIS_PX + BAND_PX);
          paintFreqAxisStrip(0, nStartHz, nStopHz);
          paintAnnotationBand(hText, AXIS_PX);
        } else {
          shiftHistory(2 * AXIS_PX + BAND_PX + SEP_PX);
          paintFreqAxisStrip(0, nStartHz, nStopHz);
          paintAnnotationBand(hText, AXIS_PX);
          paintSeparatorLine(AXIS_PX + BAND_PX);
          paintFreqAxisStrip(AXIS_PX + BAND_PX + SEP_PX, segmentStartHz, segmentStopHz);
        }
        segmentStartHz = nStartHz;
        segmentStopHz = nStopHz;
        segmentNeu(nStartHz, nStopHz, segmente[0].grid);   // T-2010: neues offenes Segment
        pendingHeader = null;
      }

      // normale Weiterschaltung um eine Zeile: der eben fertiggestellte
      // Sweep (bzw. der Header darüber) wandert einen Schritt in die
      // Historie, Zeile 0 wird für den nächsten Sweep frei.
      shiftHistory(1);
      clearHistoryTopRows(1);

      // Zeitmarken-Modell (Punkt 2): alle 30s der Echtzeit eine Marke ganz
      // oben anlegen (dort, wo die neue Zeile gerade entsteht).
      const bucket = Math.floor(frame.t / 30);
      if (lastTimeMarkBucket === null || bucket !== lastTimeMarkBucket) {
        timeMarks.push({ y: 0, label: formatClock(frame.t) });
        lastTimeMarkBucket = bucket;
      }
      // T-2011: Zeitstempel fuer die waagrechten Rasterlinien, je 10 s einer.
      const rasterBucket = Math.floor(frame.t / 10);
      if (rasterBucket !== lastRasterBucket) {
        rasterZeiten.push({ y: 0, bucket: rasterBucket });
        lastRasterBucket = rasterBucket;
      }

      updateSpacerHeight();
      wfScrollO.scrollTop = scrollRow;
      requestPaint('o');   // sichtbares Malen entkoppelt, siehe Anzeige-Scheduler
      // Text mit den exakten, vom Controller mitgelieferten Kennzahlen
      // dieses Sweeps aktualisieren.
      updateSweepStats(frame);
      // dscanDisplayLevels bleibt erhalten - Ausgangsbasis für den
      // nächsten Sweep, der sie wieder Stück für Stück überschreibt.
    }

  } else if (frame.type === 'ifpan') {
    latestZ = frame;
    syncZoomSettings(frame);   // T-703: Selects mit Geraetezustand abgleichen
    // Framerate-Messung fuers Long-Run-Budget (Schritt 5b) - unabhaengig
    // davon, ob GERADE die Zoom-Ansicht sichtbar ist (siehe measuredIfpanFps).
    ifpanFrameTimes.push(Date.now());
    // IFPAN-Frame, obwohl noch in der Uebersicht -> Zoom wurde EXTERN
    // ausgeloest (z.B. Klick im SDR-Wasserfall auf :8030). Dieselbe
    // Ansichts-Umschaltung wie in startZoom hier nachholen.
    if (mode !== 'zoom' && !awaitingOverview) {
      mode = 'zoom';
      el('overviewView').style.display = 'none';
      el('zoomView').style.display = 'block';
      // (c): Header ebenso umschalten wie in startZoom - dieser Zweig faengt
      // den EXTERN ausgeloesten Zoom ab (z. B. Klick im SDR-Wasserfall),
      // sonst stuende der Header weiter auf "D-SCAN".
      document.body.classList.add('zoom');
      el('viewName').textContent = 'Zoom (IFPAN)';
      switchScaleView('z');   // T-701: eigener Farbskalen-Zustand der Zoom-Ansicht
      resetSMeter();
      ctxSpecZ.fillStyle = '#000'; ctxSpecZ.fillRect(0, 0, specZ.width, specZ.height);
      ctxWfZ.fillStyle = '#000'; ctxWfZ.fillRect(0, 0, wfZ.width, wfZ.height);
      // Keine Nutzergeste in DIESEM Tab -> AudioContext bleibt ggf.
      // 'suspended'; der Knopf zeigt das (.blocked), ein Klick holt es nach.
      monitor.autoStart();
    }
    if (mode === 'zoom') {
      const [lo, hi] = currentScaleBounds(frame.levels, 'z');
      // Zustand fortschreiben (muss jeden Frame laufen, auch im verdeckten
      // Tab): wfZ ist zugleich Historie und Anzeige, daher hier scrollen und
      // die neue Zeile schreiben; S-Meter-Spitzenwert braucht jeden Frame.
      // T-708: Bei geaenderter Mittenfrequenz die vorhandene Wasserfall-
      // Historie frequenzrichtig unter die neue Achse schieben. Die
      // Geraete-Frames tragen die exakte Mittenfrequenz im OptionalHeader -
      // der Versatz ist damit selbstsynchronisierend, unabhaengig von
      // Drosselung oder externen Ausloesern. Bruchteil-Pixel werden
      // aufsummiert (1-Hz-Schritte sind bei 1 MHz Span nur ~0,001 px).
      // Ein Sprung um mehr als den halben Span ist kein Feinabstimm-
      // Schritt, sondern ein neuer Zoom -> Kontext zuruecksetzen statt
      // schieben (die Canvas wurde in startZoom bzw. im Extern-Zweig oben
      // ohnehin geleert).
      if (wfZPrevCenterHz !== null && frame.span_hz === wfZPrevSpanHz
          && frame.center_hz !== wfZPrevCenterHz) {
        const deltaHz = frame.center_hz - wfZPrevCenterHz;
        if (Math.abs(deltaHz) > frame.span_hz / 2) {
          tuneReset();
          wfZShiftAccPx = 0;
        } else {
          wfZShiftAccPx += -deltaHz * wfZ.width / frame.span_hz;
          const ganzPx = Math.trunc(wfZShiftAccPx);
          if (ganzPx !== 0) {
            wfZShiftAccPx -= ganzPx;
            ctxWfZ.drawImage(wfZ, ganzPx, 0);   // Self-Blit: Quelle wird laut Spec vorher eingefroren
            ctxWfZ.fillStyle = '#000';
            if (ganzPx > 0) ctxWfZ.fillRect(0, 0, ganzPx, wfZ.height);
            else ctxWfZ.fillRect(wfZ.width + ganzPx, 0, -ganzPx, wfZ.height);
          }
        }
      }
      if (frame.span_hz !== wfZPrevSpanHz) wfZShiftAccPx = 0;
      wfZPrevCenterHz = frame.center_hz;
      wfZPrevSpanHz = frame.span_hz;
      // Zielwert vom Geraet bestaetigt und nichts mehr unterwegs ->
      // zurueck auf Geraetestand (Frames fuehren die Anzeige wieder).
      if (tuneTargetHz !== null && frame.center_hz === tuneTargetHz && tuneSendTimer === null) {
        tuneTargetHz = null;
      }
      scrollWaterfallDown(ctxWfZ, wfZ);
      paintPartialRow(ctxWfZ, wfZ, 0, frame.levels, frame.levels.length, lo, hi);
      updateSMeter(frame);
      // Spektrum, Achse, Katalog-Streifen, Meta, BW-Band: entkoppelt.
      requestPaint('z');
    }
  }
};
evtSource.onerror = () => { el('meta-o').textContent = 'Connection to the server lost ...'; };
</script>
</body>
</html>
""".replace("BLOCK_ADMIN_PORT", str(BLOCK_ADMIN_PORT)) \
   .replace("__IFPAN_MODE_OPTIONS_HTML__", _build_ifpan_mode_options_html()) \
   .replace("__IFPAN_BW_OPTIONS_HTML__", _build_bw_options_html(IFPAN_BW_HZ_DEFAULT)) \
   .replace("__DSCAN_BW_OPTIONS_HTML__", _build_bw_options_html(DSCAN_BW_HZ_DEFAULT))


# Mutierende Endpunkte, die waehrend eines aktiven Long-Run-Jobs gesperrt
# sind (WS3 11.5, Punkt 5): der Empfaenger ist dann durch den Job belegt.
# Lesende Endpunkte (Stream, Status, Listen) bleiben bewusst AUSSERHALB
# dieser Mengen - die Uebersicht muss auch waehrend eines Jobs funktionieren.
_LOCKED_GET_PATHS = {
    "/api/zoom", "/api/overview", "/api/range", "/api/speed", "/api/mtime", "/api/span",
    "/api/mode", "/api/bw", "/api/dscan_bwzoom", "/api/dscan_bw", "/api/squelch",
    "/api/gain",
    "/api/record-spectrum",
    # /api/record (WAV-Ad-hoc-Aufnahme) war bisher nicht gelistet, weil sie
    # nur ueber die Zoom-Ansicht erreichbar war und /api/zoom bereits
    # gesperrt ist. Seit Schritt 4c (IFPAN-Long-Run-Jobs) kann der
    # Controller die Zoom-Ansicht aber SELBST aufbauen (ohne /api/zoom) -
    # damit waere /api/record waehrend eines Jobs sonst erreichbar.
    "/api/record",
    # T-708: Feinabstimmung - der Empfaenger gehoert waehrend eines
    # Long-Run-Jobs dem Job.
    "/api/tune",
}
_LOCKED_POST_PATHS = {"/record/start", "/record/stop"}

# T-1407: Theme-Bausteine in die Hauptseite einsetzen (Tokens + Grundregeln,
# Head-Skript fuer das Theme-Cookie). Die Kopfleiste dieser Seite ist eine
# eigene (Mittenfrequenz, Resize-Hinweis, REC-Abzeichen) - sie bekommt nur den
# Umschalter dazu, header_html() wird hier bewusst NICHT verwendet.
# T-1407a: die Status-Seite dagegen nutzt die Standard-Kopfleiste; ihr
# Status-Link zeigt auf die Seite selbst und entfaellt daher (status_href="").
HTML_PAGE = (HTML_PAGE.replace("/*THEME_CSS*/", THEME_CSS + BASE_CSS)
                      .replace("/*THEME_HEAD*/", THEME_HEAD_JS))
STATUS_HTML = (STATUS_HTML.replace("/*THEME_CSS*/", THEME_CSS + BASE_CSS)
                          .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                          .replace("/*HEADER*/", header_html("EB200 Status", status_href="/status")))


class Handler(http.server.BaseHTTPRequestHandler):
    broker: FrameBroker = None
    audio_broker: AudioBroker = None
    controller: DeviceController = None
    known_catalog = None            # Catalog aus eb200_core.known_freq, siehe main()

    def log_message(self, fmt, *args):
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path in _LOCKED_GET_PATHS:
            active = self.controller.job_active()
            if active is not None:
                self._send_json(
                    {"error": "Receiver locked: long-run job running",
                     "job_id": active["job_id"]},
                    status=423,
                )
                return

        if parsed.path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = self.broker.subscribe()
            try:
                # T-703: letzten bekannten Geraetezustand sofort an neue
                # Verbindungen schicken - behebt den Reload-Fall (Anzeige
                # zeigte sonst die statischen HTML-Defaults, bis der naechste
                # Poll-Zyklus oder - nur im Zoom - der naechste ifpan-Frame
                # kam).
                last_params = self.controller.get_last_device_params()
                if last_params is not None:
                    init_msg = json.dumps({"type": "device_params", "t": time.time(), **last_params})
                    self.wfile.write(f"data: {init_msg}\n\n".encode("utf-8"))
                    self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15.0)
                        self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                self.broker.unsubscribe(q)

        elif parsed.path == "/audio/stream":
            # Audio-Mithoeren: rohe AUD-Payload (16 kHz / 16 Bit / mono,
            # BIG-Endian wie vom Geraet - der Browser dreht die Bytes selbst,
            # siehe JS monitor) als endlose chunked Response. Kein WAV-Kopf:
            # ein <audio>-Element wuerde ein Endlos-WAV unkontrolliert puffern
            # und Sekunden nachlaufen; die Web Audio API im Browser plant die
            # Chunks selbst und verwirft bei Rueckstand (Latenzbremse).
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Audio-Format", "pcm_s16be; rate=16000; channels=1")  # fest, siehe AUDIO_REMOTE_MODE
            self.end_headers()
            q = self.audio_broker.subscribe()
            # Ein Hoerer ist da -> das IST der Einschaltwunsch (kein separates
            # /api/monitor mehr: ein getrenntes Kommando konnte in der
            # Uebersicht verpuffen, wenn es vor /api/zoom in der Queue lag).
            self.controller.request_monitor_start()
            try:
                while True:
                    try:
                        chunk = q.get(timeout=5.0)
                    except queue.Empty:
                        # Kein Audio (z. B. Mithoeren serverseitig aus).
                        # Ohne fliessende Chunks gibt es keine write()-
                        # Fehler - ein vom Browser abgebrochener Stream
                        # (AbortController, Reload, Tab zu) bliebe sonst
                        # UNBEMERKT als Zombie-Client haengen: client_count()
                        # faellt nie auf 0, monitor_check kann das Mithoeren
                        # nie mehr abschalten, jede Re-Aktivierung stapelt
                        # einen weiteren Stream. Deshalb hier aktiv pruefen.
                        if _peer_disconnected(self.connection):
                            break
                        continue
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                remaining = self.audio_broker.unsubscribe(q)
                if remaining == 0:
                    # Letzter Hoerer weg (Tab zu, Reload, Netz weg): Controller
                    # prueft im eigenen Thread nach, ob wirklich niemand mehr
                    # hoert, und raeumt dann den AUD-Tag am Geraet ab.
                    self.controller.request_monitor_check()

        elif parsed.path == "/api/zoom":
            params = parse_qs(parsed.query)
            try:
                freq_hz = int(float(params["freq"][0]))
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_zoom(freq_hz)
            self._send_json_ok()

        elif parsed.path == "/api/tune":
            # T-708: Feinabstimmung der IFPAN-Mittenfrequenz - nur FREQ,
            # kein Zoom-Neuaufbau (siehe tune-Zweig in _run_zoom_loop).
            # Waehrend laufender WAV- oder Specbin-Aufnahme abgewiesen:
            # Sidecar bzw. Dateigeometrie tragen die Mittenfrequenz, ein
            # Wechsel mitten in der Datei wuerde die Aufnahme entwerten.
            params = parse_qs(parsed.query)
            try:
                freq_hz = int(float(params["freq"][0]))
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            if not (10_000 <= freq_hz <= 3_000_000_000):
                self._send_json({"error": "frequency must be between 10 kHz and 3 GHz"}, status=400)
                return
            if self.controller.recording or self.controller.spectrum_recording:
                # T-709: Log-Zeile zur Diagnose - beim T-708-Hardwaretest
                # blieb die Sperre scheinbar wirkungslos; hiermit ist in der
                # Konsole nachvollziehbar, ob dieser Zweig anspricht.
                print("[http] /api/tune abgewiesen (409) - Aufnahme laeuft.")
                self._send_json({"error": "fine tuning locked - recording in progress"}, status=409)
                return
            self.controller.request_tune(freq_hz)
            self._send_json_ok()

        elif parsed.path == "/api/overview":
            self.controller.request_overview()
            self._send_json_ok()

        elif parsed.path == "/api/params":
            # Parameter-Memory komplett - as_dict() liest die Datei bei jedem
            # Aufruf frisch, daher sind auch Eintraege anderer Server-Prozesse
            # sichtbar. Read-only; es gibt bewusst keinen Schreib-Endpoint.
            try:
                data = self.controller.param_store.as_dict()
                data["_store_path"] = PARAM_STORE_PATH
                data["_snapshot_running"] = self.controller.snapshot_running()
                self._send_json(data)
            except Exception as exc:
                self._send_json({"error": f"parameter store unreadable: {exc!r}"}, status=500)

        elif parsed.path == "/api/device":
            # T-2013: gespeicherte (device.json, frisch gelesen) und aktive
            # Geraeteadresse; die aktive gilt bis zum naechsten Neustart.
            self._send_json({
                "configured": lade_device_config(),
                "active": {"eb200_host": EB200_IP, "scpi_port": SCPI_PORT,
                           "local_ip": self.controller.local_ip,
                           "source": DEVICE_CFG["source"]},
                "file": paths.data_path(DEVICE_JSON),
            })

        elif parsed.path == "/status":
            body = STATUS_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/known_freq":
            # Katalog bekannter Frequenzen, kompakt (siehe Catalog.to_client_json).
            # Vom Frontend EINMALIG beim Laden geholt, Detailstufe entscheidet der
            # Client selbst. Rein lesend, bewusst NICHT in _LOCKED_GET_PATHS.
            # load_catalog_cached() statt der beim Start geladenen Instanz, damit
            # Katalog-Edits (block_admin.py) ohne Neustart hier ankommen.
            # load_station_config() ebenso JE REQUEST (winzige Datei, Region ist
            # reine Anzeigefilterung) - wirkt so ohne Neustart wie Katalog-Edits.
            # load_view_config() genauso (T-126): die in :8010 abgewaehlten
            # Katalogdateien fallen hier aus bands/channels heraus - das ist die
            # Ansichtsauswahl fuer D-SCAN und IFPAN.
            if self.known_catalog is None:
                self._send_json({"version": 1, "bands": [], "channels": [],
                                 "files": [], "errors": ["catalog not loaded"],
                                 "region": {"tags": []},
                                 "view": {"total": 0, "hidden": 0}})
            else:
                self._send_json(load_catalog_cached().to_client_json(
                    load_station_config(), load_view_config()))

        elif parsed.path == "/api/dscan_config":
            # Aktuelle D-SCAN-Konfiguration des Controllers (beim Start vom
            # Gerät übernommen, siehe _read_dscan_config_from_device). Vom
            # Frontend EINMALIG beim Laden geholt, damit Eingabefelder,
            # Speed-Button und BW-Select den echten Zustand zeigen statt der
            # fest im HTML stehenden Defaults. Rein lesend (nur GIL-atomare
            # Attribut-Reads), daher nicht Long-Run-gesperrt.
            self._send_json({
                "start_hz": self.controller.dscan_start_hz,
                "stop_hz": self.controller.dscan_stop_hz,
                "speed": self.controller.dscan_speed,
                "mtime_ms": (round(self.controller.dscan_mtime_s * 1000.0, 3)
                             if self.controller.dscan_mtime_s is not None else None),
                "detector": self.controller.dscan_detector,
                "bw_hz": self.controller.dscan_bw_hz,
                "bw_zoom": self.controller.dscan_bw_zoom,
                # Squelch ist eigentlich eine Zoom-Einstellung, wird hier aber
                # pragmatisch mitgeliefert - der Endpunkt wird vom Frontend
                # ohnehin einmalig beim Laden geholt und ist nicht Long-Run-
                # gesperrt.
                "squelch_on": self.controller.squelch_on,
                "squelch_thr_dbuv": self.controller.squelch_thr_dbuv,
                "gain_mode": self.controller.gain_mode,
                "gain_mgc_dbuv": self.controller.gain_mgc_dbuv,
            })

        elif parsed.path == "/api/range":
            params = parse_qs(parsed.query)
            try:
                start_hz = int(float(params["start"][0]))
                stop_hz = int(float(params["stop"][0]))
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            # Grenzen laut Annex C (Startfrequenz 10 kHz..~3 GHz,
            # Stopfrequenz ~24.25 kHz..3 GHz) - grob geprüft, das Gerät
            # meldet bei ungültigen Werten selbst per SCPI-Fehler.
            if not (10_000 <= start_hz < stop_hz <= 3_000_000_000):
                self._send_json({"error": "range must be between 10 kHz and 3 GHz with start < stop"}, status=400)
                return
            if stop_hz - start_hz < DSCAN_MIN_SPAN_HZ:
                # Geraet wuerde mit "Span too low" ablehnen und still den
                # alten Bereich weiterfahren - siehe DSCAN_MIN_SPAN_HZ.
                self._send_json({
                    "error": (f"span {(stop_hz - start_hz)/1e3:.1f} kHz too small - "
                              f"the device requires at least {DSCAN_MIN_SPAN_HZ/1e3:.0f} kHz "
                              f"(\"Span too low\"). For such narrow sections use the "
                              f"IFPAN zoom (click in the spectrum)."),
                }, status=400)
                return
            self.controller.request_range(start_hz, stop_hz)
            self._send_json_ok()

        elif parsed.path == "/api/speed":
            params = parse_qs(parsed.query)
            value = params.get("value", [""])[0].upper()
            if value not in ("LOW", "NORM", "HIGH", "MTIM"):
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_speed(value)
            self._send_json_ok()

        elif parsed.path == "/api/mtime":
            params = parse_qs(parsed.query)
            value_raw = params.get("value", [""])[0].strip().upper()
            if value_raw == "DEF":
                self.controller.request_mtime(None)
                self._send_json_ok()
                return
            try:
                mtime_ms = float(params["value"][0])
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            # Handbuch-Grenzen (0.5 ms .. 900 s); UI liefert Millisekunden,
            # der Controller arbeitet in Sekunden (SCPI-Einheit).
            if not (0.5 <= mtime_ms <= 900_000.0):
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_mtime(mtime_ms / 1000.0)
            self._send_json_ok()

        elif parsed.path == "/api/span":
            params = parse_qs(parsed.query)
            try:
                span_hz = int(float(params["value"][0]))
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            if span_hz not in ALLOWED_IFPAN_SPANS_HZ:
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_span(span_hz)
            self._send_json_ok()

        elif parsed.path == "/api/mode":
            params = parse_qs(parsed.query)
            value = params.get("value", [""])[0].upper()
            if value not in IFPAN_MODE_OPTIONS:
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_mode(value)
            self._send_json_ok()

        elif parsed.path == "/api/bw":
            params = parse_qs(parsed.query)
            try:
                bw_hz = int(float(params["value"][0]))
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            if bw_hz not in IFPAN_BW_OPTIONS_HZ:
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_bw(bw_hz)
            self._send_json_ok()

        elif parsed.path == "/api/squelch":
            params = parse_qs(parsed.query)
            try:
                on = int(params["on"][0])
                thr = int(float(params["thr"][0]))
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            # Annex C: Squelch-Schwelle -30..110 dBµV.
            if not (-30 <= thr <= 110):
                self._send_json({"error": "squelch threshold must be between -30 and 110 dBµV"}, status=400)
                return
            self.controller.request_squelch(on == 1, thr)
            self._send_json_ok()

        elif parsed.path == "/api/gain":
            params = parse_qs(parsed.query)
            mode = params.get("mode", [""])[0].strip().upper()
            if mode not in ("AGC", "MGC"):
                self.send_response(400)
                self.end_headers()
                return
            mgc = self.controller.gain_mgc_dbuv
            if mode == "MGC":
                try:
                    mgc = int(float(params["mgc"][0]))
                except (KeyError, ValueError, IndexError):
                    self.send_response(400)
                    self.end_headers()
                    return
                # Annex C: MGC-Pegel -30..110 dBµV.
                if not (-30 <= mgc <= 110):
                    self.send_response(400)
                    self.end_headers()
                    return
            self.controller.request_gain(mode, mgc)
            self._send_json_ok()

        elif parsed.path == "/api/detect":
            # T-505: Auto-Marker konfigurieren. ?on=0|1 &delta=<dB>, beide optional.
            params = parse_qs(parsed.query)
            try:
                on = int(params["on"][0]) if "on" in params else None
                delta = float(params["delta"][0]) if "delta" in params else None
                auto = int(params["auto"][0]) if "auto" in params else None
                mins = int(params["min"][0]) if "min" in params else None   # T-505e
            except (ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            if delta is not None and not (1.0 <= delta <= 60.0):
                self._send_json({"error": "delta must be between 1 and 60 dB"}, status=400)
                return
            self.controller.detector.configure(
                enabled=(None if on is None else on == 1), delta_db=delta,
                auto_delta=(None if auto is None else auto == 1),
                min_sweeps=(None if mins is None else max(1, min(10, mins))))
            self._send_json(self.controller.detector.status())

        elif parsed.path == "/api/detect/clear":
            self.controller.detector.clear_marked()
            self._send_json(self.controller.detector.status())

        elif parsed.path == "/api/dscan_bwzoom":
            params = parse_qs(parsed.query)
            value = params.get("value", [""])[0].lower()
            if value not in ("on", "off"):
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_dscan_bw_zoom(value == "on")
            self._send_json_ok()

        elif parsed.path == "/api/dscan_bw":
            params = parse_qs(parsed.query)
            try:
                bw_hz = int(float(params["value"][0]))
            except (KeyError, ValueError, IndexError):
                self.send_response(400)
                self.end_headers()
                return
            if bw_hz not in IFPAN_BW_OPTIONS_HZ:
                self.send_response(400)
                self.end_headers()
                return
            self.controller.request_dscan_bw(bw_hz)
            self._send_json_ok()

        elif parsed.path == "/api/record":
            params = parse_qs(parsed.query)
            state = params.get("state", [""])[0].lower()
            if state not in ("on", "off"):
                self.send_response(400)
                self.end_headers()
                return
            if state == "on":
                self.controller.request_record_start()
            else:
                self.controller.request_record_stop()
            self._send_json_ok()

        elif parsed.path == "/api/record-spectrum":
            params = parse_qs(parsed.query)
            state = params.get("state", [""])[0].lower()
            if state not in ("on", "off"):
                self.send_response(400)
                self.end_headers()
                return
            if state == "on":
                self.controller.request_spectrum_record_start()
                self._send_json_ok()
            else:
                # T-803 (Teil D): stop_spectrum_recording_and_wait() wartet
                # begrenzt auf den finalen .specbin-Pfad, siehe dort.
                self._send_json(self.controller.stop_spectrum_recording_and_wait())

        elif parsed.path == "/record/status":
            # Status der Rohdaten-Session-Aufzeichnung (siehe
            # eb200_core/session_store.py, DeviceController.session_status()).
            self._send_json(self.controller.session_status())

        elif parsed.path == "/api/debug/threads":
            # Manueller Thread-Dump (T-206) - fuer den Fall, dass der
            # Waechter-Thread selbst nicht zum Zug kommt (faulthandler im
            # Prozess blockiert, siehe _sched_watchdog_loop). Rein lesend,
            # bewusst NICHT in _LOCKED_GET_PATHS.
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_dump_path = paths.data_path("logs", f"threads_{ts}.txt")
            dump_threads(debug_dump_path, f"Manueller Thread-Dump {ts} (/api/debug/threads)")
            with open(debug_dump_path, encoding="utf-8") as fh:
                debug_dump_text = fh.read()
            self._send_json({"path": debug_dump_path, "text": debug_dump_text})

        elif parsed.path == "/api/longrun/status":
            # Long-Run-Job-Uebersicht (WS3 11.5) - bewusst lesend, NICHT in
            # _LOCKED_GET_PATHS, die Uebersicht muss waehrend eines
            # laufenden Jobs funktionieren.
            self._send_json(self.controller.longrun_status())

        elif parsed.path == "/api/storage":
            # Konfigurierte Ablagen fuer die Long-Run-Konfigurationsmaske
            # (WS3 11.4/11.5) - rein lesend, NICHT in _LOCKED_GET_PATHS.
            # Freien Platz nur ermitteln, wenn die Ablage ueberhaupt
            # existiert (shutil.disk_usage wirft sonst); jeder andere Fehler
            # (z. B. Netzlaufwerk kurz weg) liefert defensiv free_bytes=null
            # statt eines 500ers - die Maske zeigt die Ablage dann als
            # "not reachable" an.
            result = []
            for root in load_storage_roots():
                # "root" bleibt der konfigurierte Eintrag (create_job vergleicht
                # ihn woertlich); geprueft wird der absolute Pfad (T-2003).
                root_abs = os.path.join(paths.DATA_ROOT, root)
                exists = os.path.isdir(root_abs)
                free_bytes = None
                if exists:
                    try:
                        free_bytes = shutil.disk_usage(root_abs).free
                    except OSError:
                        free_bytes = None
                result.append({"root": root, "exists": exists, "free_bytes": free_bytes})
            self._send_json(result)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path in _LOCKED_POST_PATHS:
            active = self.controller.job_active()
            if active is not None:
                self._send_json(
                    {"error": "Receiver locked: long-run job running",
                     "job_id": active["job_id"]},
                    status=423,
                )
                return

        if parsed.path == "/record/start":
            # Fehler beim Start (z. B. Session-Datei nicht anlegbar) -> HTTP
            # 500 mit knapper JSON-Fehlermeldung. start_session() setzt
            # self._session_recorder erst NACH erfolgreichem Anlegen des
            # SessionStore, ein Fehler davor hinterlässt also KEINEN Recorder.
            try:
                status = self.controller.start_session()
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json(status)

        elif parsed.path == "/record/stop":
            self._send_json(self.controller.stop_session())

        elif parsed.path == "/api/longrun/create":
            self._handle_longrun_create()

        elif parsed.path == "/api/device":
            self._handle_device_save()

        elif parsed.path == "/api/device/test":
            self._handle_device_test()

        elif parsed.path == "/api/params/snapshot":
            started = self.controller.request_snapshot()
            self._send_json({"started": started, "running": self.controller.snapshot_running()})

        elif parsed.path == "/api/longrun/stop":
            # bewusst NICHT in _LOCKED_POST_PATHS - waehrend eines aktiven
            # Jobs muss genau dieser Endpunkt erreichbar bleiben, um ihn
            # ueberhaupt beenden zu koennen.
            self._send_json(self.controller.longrun_stop())

        else:
            self.send_response(404)
            self.end_headers()

    def _read_json_body(self) -> Optional[dict]:
        """T-2013: JSON-Koerper lesen (Muster _handle_longrun_create);
        None nach bereits gesendeter 400-Antwort."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 4096:
            self._send_json({"ok": False, "code": "request.invalid"}, status=400)
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"ok": False, "code": "request.invalid_json"}, status=400)
            return None
        if not isinstance(payload, dict):
            self._send_json({"ok": False, "code": "request.invalid_json"}, status=400)
            return None
        return payload

    def _handle_device_save(self) -> None:
        """T-2013: Geraeteadresse pruefen und nach device.json schreiben.
        Wirkt erst nach einem Neustart von :8000 - der laufende Controller
        bleibt bewusst unberuehrt (Entscheidung OE3GAS 2026-10-09)."""
        payload = self._read_json_body()
        if payload is None:
            return
        werte, code = pruefe_device_eingabe(payload)
        if werte is None:
            self._send_json({"ok": False, "code": code}, status=400)
            return
        try:
            pfad = speichere_device_config(werte)
        except OSError as exc:
            self._send_json({"ok": False, "code": "device.write_failed",
                             "params": {"detail": str(exc)}}, status=500)
            return
        print(f"[device] gespeichert: {werte['eb200_host']}:{werte['scpi_port']} -> {pfad}")
        self._send_json({"ok": True, "restart_required": True, "file": pfad})

    def _handle_device_test(self) -> None:
        """T-2013: kurze, eigene SCPI-Verbindung zur angegebenen Adresse,
        *IDN? abfragen, schliessen. Der EB200 nimmt bis zu 10 TCP-Clients an
        (Geraeteprofil) - die laufende Verbindung des Controllers bleibt
        unberuehrt. Bewusst nur TCP-Verbindung plus *IDN?, nichts Schreibendes."""
        payload = self._read_json_body()
        if payload is None:
            return
        werte, code = pruefe_device_eingabe(payload)
        if werte is None:
            self._send_json({"ok": False, "code": code}, status=400)
            return
        client = None
        try:
            client = ScpiClient(werte["eb200_host"], werte["scpi_port"], timeout=3.0)
            idn = client.query("*IDN?")
        except (OSError, ConnectionError, UnicodeDecodeError) as exc:
            self._send_json({"ok": False, "code": "device.unreachable",
                             "params": {"detail": str(exc)}})
            return
        finally:
            if client is not None:
                client.close()
        self._send_json({"ok": True, "idn": idn})

    def _handle_longrun_create(self) -> None:
        """
        POST /api/longrun/create - legt einen neuen Long-Run-Job an (Muster:
        /api/label in analyze_sessions.py - JSON-Body). Felder: type
        ("dscan"), label, start_hz, stop_hz, speed, start_iso (fehlt ->
        jetzt), end_iso ODER duration_s (genau eines von beiden),
        decimation_s (optional), rotation_min (optional), storage_root
        (optional). Die Maske aus Schritt 5 nutzt diesen Endpunkt; bis
        dahin z. B. per curl bedienbar (siehe WS3 11.5/Schritt 4).
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 65536:
            self._send_json({"error": "invalid request"}, status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "invalid JSON"}, status=400)
            return

        end_iso = payload.get("end_iso")
        duration_s = payload.get("duration_s")
        if (end_iso is None) == (duration_s is None):
            self._send_json(
                {"error": "specify exactly one of end_iso or duration_s"}, status=400
            )
            return

        start_iso = payload.get("start_iso") or datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        if end_iso is None:
            try:
                end_iso = (
                    datetime.fromisoformat(start_iso) + timedelta(seconds=float(duration_s))
                ).isoformat(timespec="seconds")
            except (TypeError, ValueError):
                self._send_json({"error": "invalid duration_s"}, status=400)
                return

        job_type = payload.get("type", "dscan")
        decimation_s = payload.get("decimation_s")

        if job_type == "ifpan":
            # WS3 11.2: IFPAN-Long-Run laeuft bewusst OHNE Dezimierung (volle
            # Framerate) - job_store.create_job() prueft das ohnehin, die
            # HTTP-Fehlermeldung hier ist nur die sprechendere Variante davon.
            if decimation_s is not None:
                self._send_json(
                    {"error": "IFPAN long runs run without decimation (WS3 11.2) - "
                              "decimation_s must not be set"},
                    status=400,
                )
                return
            try:
                span_hz = int(payload["span_hz"])
            except (KeyError, TypeError, ValueError):
                self._send_json({"error": "span_hz is missing or invalid"}, status=400)
                return
            if span_hz not in ALLOWED_IFPAN_SPANS_HZ:
                self._send_json(
                    {"error": f"span_hz must be one of: {ALLOWED_IFPAN_SPANS_HZ}"}, status=400
                )
                return
            demod = payload.get("demod", IFPAN_MODE_DEFAULT)
            if demod not in IFPAN_MODE_OPTIONS:
                self._send_json({"error": f"demod must be one of: {IFPAN_MODE_OPTIONS}"}, status=400)
                return
            if demod == "IQ" and bool(payload.get("audio", False)):
                # Der Job-Audio-Pfad (eigener UdpPath, job_audio_payload) ist
                # fest mono - IQ dort wäre stiller Datenmüll. Spätere Ausbaustufe.
                self._send_json({"error": "IQ demodulation with job audio is not supported"}, status=400)
                return
            try:
                bw_hz = int(payload.get("bw_hz", IFPAN_BW_HZ_DEFAULT))
            except (TypeError, ValueError):
                self._send_json({"error": "invalid bw_hz"}, status=400)
                return
            if bw_hz not in IFPAN_BW_OPTIONS_HZ:
                self._send_json({"error": f"bw_hz must be one of: {IFPAN_BW_OPTIONS_HZ}"}, status=400)
                return
            try:
                params = {
                    "center_hz": int(payload["center_hz"]),
                    "span_hz": span_hz,
                    "demod": demod,
                    "bw_hz": bw_hz,
                    "audio": bool(payload.get("audio", False)),
                }
            except (KeyError, TypeError, ValueError):
                self._send_json({"error": "center_hz is missing or invalid"}, status=400)
                return
        else:
            try:
                params = {
                    "start_hz": int(payload["start_hz"]),
                    "stop_hz": int(payload["stop_hz"]),
                    "speed": payload.get("speed", DSCAN_SPEED),
                    # None = DEFAULT-Messzeit; nur bei speed == "MTIM"
                    # wirksam (siehe _start_dscan_job / request_mtime).
                    "mtime_ms": payload.get("mtime_ms"),
                }
            except (KeyError, TypeError, ValueError):
                self._send_json({"error": "start_hz/stop_hz are missing or invalid"}, status=400)
                return
            if params["mtime_ms"] is not None:
                try:
                    mtime_ms = float(params["mtime_ms"])
                except (TypeError, ValueError):
                    self._send_json({"error": "invalid mtime_ms"}, status=400)
                    return
                if not (0.5 <= mtime_ms <= 900_000.0):
                    self._send_json(
                        {"error": "mtime_ms must be between 0.5 ms and 900000 ms (empty = DEFAULT)"},
                        status=400,
                    )
                    return
                params["mtime_ms"] = mtime_ms

        try:
            manifest = self.controller.job_store.create_job(
                type=job_type,
                label=payload.get("label", ""),
                params=params,
                start_iso=start_iso,
                end_iso=end_iso,
                decimation_s=decimation_s,
                rotation_min=payload.get("rotation_min", 60),
                storage_root=payload.get("storage_root"),
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json(manifest)

    def _send_json_ok(self) -> None:
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: dict, status: int = 200) -> None:
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
    # Scheduler-Diagnostik (T-206): faulthandler faengt fatale Fehler
    # (Segfault, unbehandeltes C-Signal) ab und schreibt einen Thread-Dump
    # nach stderr, BEVOR der Prozess stirbt - unabhaengig vom Waechter-Thread
    # (_sched_watchdog_loop), der bei einem echten Deadlock selbst nicht mehr
    # zum Zug kommen kann.
    faulthandler.enable()
    local_ip = LOCAL_IP or determine_local_ip(EB200_IP)
    broker = FrameBroker()
    audio_broker = AudioBroker()
    stop_event = threading.Event()

    controller = DeviceController(
        eb200_ip=EB200_IP, local_ip=local_ip, udp_port=UDP_PORT,
        broker=broker, audio_broker=audio_broker, stop_event=stop_event,
    )
    controller.start()

    Handler.broker = broker
    Handler.audio_broker = audio_broker
    Handler.controller = controller
    # Katalog bekannter Frequenzen (known_freq/*.json) EINMAL beim Start laden.
    # load_catalog() wirft nie - defekte Dateien landen als Text in .errors.
    # Ein leerer/kaputter Katalog darf den Server nie am Start hindern.
    Handler.known_catalog = load_catalog()
    kc = Handler.known_catalog
    print(f"[known_freq] {len(kc.channels)} Kanaele, {len(kc.bands)} Baender "
          f"aus {len(kc.files)} Dateien, {len(kc.errors)} Fehler")
    for err in kc.errors:
        print(f"[known_freq] WARNUNG: {err}", file=sys.stderr)
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)

    print(f"[http] Waterfall verfügbar unter http://{local_ip}:{HTTP_PORT}/ "
          f"(auch von anderen Geräten im LAN erreichbar)")
    register_server("waterfall", HTTP_PORT)
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
        stop_event.set()
        controller.join(timeout=5.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())