#!/usr/bin/env python3
"""
sdr_waterfall.py

Eigenstaendiger SDR-Wasserfall als Zwischenstufe im Ablauf D-SCAN -> SDR-
Wasserfall -> IFPAN: die D-SCAN-Uebersicht (waterfall.py, Port 8000)
markiert per Drag einen Frequenzbereich, dieser Dienst zeigt darin einen
lueckenlosen FFT-Wasserfall eines angeschlossenen SDR-Geraets, ein Klick
darin uebergibt die angeklickte Frequenz an IFPAN (waterfall.py,
/api/zoom). Laeuft komplett unabhaengig vom EB200-Geraet und wird separat
gestartet (siehe WS1 Abschnitt 4 / start_eb200.py fuer die uebrigen
Dienste - dieser hier bewusst NICHT darin aufgenommen).

Scope AUSSCHLIESSLICH die in SDR_DEVICES gelisteten Geraete: HackRF One,
Airspy. Angesteuert werden sie NICHT ueber die SoapySDR-Python-Bindings
(auf dieser Maschine scheiterte deren make() zuverlaessig mit "no match",
obwohl die nativen Vendor-CLIs funktionieren), sondern jeweils direkt ueber
ihr natives Vendor-CLI als Subprozess (siehe NativeCliSource) - dessen
stdout liefert das rohe IQ.

Architektur (bewusst analog zu waterfall.py gehalten, aber eigenstaendig -
kein Import von dort, kein Zugriff auf den EB200-Controller):
  - Reines http.server (Python-Standardbibliothek) fuer HTTP/SSE, wie in
    waterfall.py. numpy wird NUR hier gebraucht, nicht in den anderen
    Diensten - sonst ausschliesslich Stdlib (inkl. subprocess fuer die
    Geraete-CLIs).
  - NativeCliSource ist ein duenner Adapter, der ein Vendor-CLI als
    Subprozess startet und dessen stdout als rohes IQ liest - einziger
    Ort, der direkt mit der Hardware spricht.
  - Ein SdrWorker-Thread liest IQ-Bloecke, rechnet daraus FFT-Wasserfall-
    zeilen und veroeffentlicht sie ueber einen FrameBroker (SSE, /events) -
    exakt das gleiche Verteil-Muster wie in waterfall.py. SdrManager laesst
    wie beim EB200-Controller immer nur EINEN Worker gleichzeitig laufen
    (ein Geraet kann ohnehin nur einmal geoeffnet werden); /api/sdr/start
    beendet einen laufenden Worker deshalb immer zuerst.
  - Die HTML-Seite (Teil B) ist bewusst eine EIGENE, in sich geschlossene
    Seite (kein Import aus waterfall.py) - sie bildet dessen bewaehrtes
    Render-Muster (Offscreen-History-Canvas fuer den rollenden Wasserfall,
    Colormap, Frequenzachsen-Streifen, Zeit-Gutter) nur nach.

WICHTIG: die angezeigten Pegel sind RELATIVE dBFS-Werte (dB unterhalb des
digitalen Vollausschlags), NICHT kalibriert wie die dBµV-Werte des EB200 -
im UI entsprechend beschriftet, siehe HTML_PAGE.

Abhaengigkeiten (NUR fuer dieses Modul, in keinem anderen Dienst noetig):
  - numpy (einzige externe Bibliothek - keine SoapySDR-Bindings mehr).
  - hackrf_transfer.exe (HackRF-Tools) fuer key=hackrf - Pfad in SDR_DEVICES
    (cmd=...) an die tatsaechliche Installation anpassen.
  - airspy_rx.exe (airspy_tools) fuer key=airspy - Pfad UND -a/-t-Flags in
    SDR_DEVICES (cmd=.../args=...) an die tatsaechliche Installation und das
    konkrete Geraetemodell anpassen (siehe Kommentar dort).
Fehlt das CLI eines Geraets (falscher Pfad, nicht installiert), stuerzt
dieses Skript NICHT ab - probe()/die Endpunkte melden das Geraet dann
lediglich als nicht verfuegbar, siehe NativeCliSource.probe().

Sicherheits-Hinweis: der Server bindet wie waterfall.py auf 0.0.0.0 und ist
damit im gesamten Shack-LAN erreichbar. Nur Python-Standardbibliothek fuer
HTTP/SSE; die Geraete-CLIs werden ausschliesslich lesend (stdout) verwendet
(keine Firmware-Schreibzugriffe o. ae.).

Start:
    python sdr_waterfall.py
    -> wird von waterfall.py (Port 8000) per Toggle "Ergaenze mit SDR-
       Wasserfall" verlinkt; eigenstaendig auch direkt aufrufbar unter
       http://localhost:8030/?device=<key>&center=<Hz>&span=<Hz>
"""

from __future__ import annotations

import http.server
import json
import queue
import signal
import sys
import threading
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import os
import shutil
import subprocess

import numpy as np

from eb200_core.param_store import register_server
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
from eb200_core.known_freq import (
    load_catalog, load_catalog_cached, load_station_config, load_view_config,
)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

HTTP_HOST = "0.0.0.0"
SDR_HTTP_PORT = 8030

DISPLAY_BINS = 900          # Anzeigebreite, wie beim EB200-Wasserfall (900 Bins)
FRAME_FPS = 15               # Ziel-Bildrate der SSE-Frames (siehe SdrWorker.run)
OPEN_RETRIES = 3             # Airspy/Windows-libusb: make() kann nach einem
OPEN_RETRY_DELAY_S = 0.3     # vorherigen enumerate/close kurz "no match" liefern
CLIENT_QUEUE_SIZE = 20       # bounded Queue je SSE-Client, wie in waterfall.py

FFT_SIZE = 65536             # zugleich Groesse eines gelesenen IQ-Blocks - ein
                              # Block ergibt genau eine FFT, keine Ueberlappung
FFT_AVERAGE_COUNT = 4        # ueber so viele aufeinanderfolgende FFTs gemittelt
                              # (auf den bereits in dB gewandelten Werten -
                              # keine exakte Leistungsmittelung, aber fuer eine
                              # ruhigere Anzeige ausreichend und einfach)
DB_FLOOR = -180.0            # Boden gegen -inf bei log10(0)

STREAM_TIMEOUT_RETRIES = 50  # so viele aufeinanderfolgende readStream-Timeouts
                              # (je 200ms) werden toleriert, bevor der Worker
                              # den Stream als gestoert meldet (siehe read())

# Geraete-Config: EIN Eintrag je unterstuetztem SoapySDR-Geraet. sample_rates
# muss zum konkreten Geraetemodell passen (z. B. Airspy R2 vs. Mini haben
# unterschiedliche zulaessige Raten) - bei anderer Hardware hier anpassen.
SDR_DEVICES = [
    dict(
        key="hackrf", label="HackRF One",
        band_min_hz=1_000_000, band_max_hz=6_000_000_000,
        sample_rates=[2_000_000, 4_000_000, 8_000_000, 10_000_000, 16_000_000, 20_000_000],
        # Natives CLI. {center}=Hz, {rate}=Hz werden zur Laufzeit ersetzt.
        cmd=r"C:\HackRF\bin\hackrf_transfer.exe",
        args=["-r", "-", "-f", "{center}", "-s", "{rate}", "-a", "0", "-l", "24", "-g", "20"],
        iq_dtype="int8",     # hackrf_transfer: interleaved signed 8-bit IQ
    ),
    dict(
        key="airspy", label="Airspy",
        band_min_hz=24_000_000, band_max_hz=1_800_000_000,
        sample_rates=[2_500_000, 10_000_000],
        # PFAD und -a/-t-Flags an dein airspy_rx anpassen! -f ist in MHz.
        cmd=r"C:\airspy\airspy_rx.exe",
        args=["-r", "-", "-f", "{center_mhz}", "-a", "{rate}", "-t", "0"],
        iq_dtype="float32",  # airspy_rx -t 0 = FLOAT32 IQ
    ),
]


def _device_by_key(key: str) -> Optional[dict]:
    return next((d for d in SDR_DEVICES if d["key"] == key), None)


# ---------------------------------------------------------------------------
# NativeCliSource: treibt ein SDR ueber sein natives Vendor-CLI als Subprozess
# an und liest rohes IQ von dessen stdout - einziger Ort mit Geraetezugriff.
# ---------------------------------------------------------------------------

class NativeCliSource:
    """Treibt ein SDR ueber sein natives Vendor-CLI als Subprozess an und liest
    rohes IQ von dessen stdout. Umgeht die SoapySDR-Python-Bindings komplett -
    einziger Ort mit Geraetezugriff."""

    def __init__(self, device_cfg: dict) -> None:
        self._cfg = device_cfg
        self._proc = None
        if device_cfg["iq_dtype"] == "int8":
            self._dtype, self._scale = np.int8, 127.0
        elif device_cfg["iq_dtype"] == "int16":
            self._dtype, self._scale = np.int16, 32767.0
        elif device_cfg["iq_dtype"] == "float32":
            self._dtype, self._scale = np.float32, 1.0
        else:
            raise ValueError(f"Unbekannter iq_dtype: {device_cfg['iq_dtype']}")

    @staticmethod
    def probe(device_cfg: dict) -> bool:
        """Verfuegbar = das CLI ist vorhanden. Beruehrt das Geraet NICHT (keine
        Races) - ob es angesteckt ist, zeigt sich beim Oeffnen."""
        cmd = device_cfg.get("cmd")
        if not cmd:
            return False
        return os.path.isfile(cmd) or shutil.which(cmd) is not None

    def open(self, center_hz: float, rate_hz: float, gain: float, agc: bool) -> None:
        subs = {
            "center": str(int(center_hz)),
            "center_mhz": f"{center_hz / 1e6:.6f}",
            "rate": str(int(rate_hz)),
        }
        args = [a.format(**subs) for a in self._cfg["args"]]
        self._proc = subprocess.Popen(
            [self._cfg["cmd"], *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def read(self, n: int) -> np.ndarray:
        """Liest genau n komplexe Samples vom stdout-Pipe des CLI."""
        itemsize = np.dtype(self._dtype).itemsize
        need = n * 2 * itemsize          # I und Q je Sample
        buf = bytearray()
        while len(buf) < need:
            chunk = self._proc.stdout.read(need - len(buf))
            if not chunk:
                raise RuntimeError("SDR-CLI hat den Datenstrom beendet")
            buf.extend(chunk)
        raw = np.frombuffer(bytes(buf), dtype=self._dtype).astype(np.float32) / self._scale
        return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None


# ---------------------------------------------------------------------------
# FrameBroker: SSE-Verteilung, exakt das gleiche Muster wie in waterfall.py
# (bewusst hier dupliziert statt importiert - dieser Dienst soll komplett
# eigenstaendig vom EB200-Controller-Modul bleiben).
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

    def publish_sdr(self, center_hz: float, span_hz: float, rate_hz: float,
                     levels: list[int]) -> None:
        msg = json.dumps({
            "type": "sdr", "center_hz": center_hz, "span_hz": span_hz,
            "rate_hz": rate_hz, "t": time.time(), "levels": levels,
        })
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
# SdrWorker: eigener Thread je laufender Aufnahme - liest IQ-Bloecke,
# rechnet FFT-Wasserfallzeilen und veroeffentlicht sie ueber den Broker.
# ---------------------------------------------------------------------------

class SdrWorker(threading.Thread):
    def __init__(self, source: NativeCliSource, device_cfg: dict,
                 center_hz: float, rate_hz: float,
                 broker: FrameBroker, stop_event: threading.Event) -> None:
        super().__init__(daemon=True)
        self._source = source
        self._device_cfg = device_cfg
        self.center_hz = center_hz
        self.rate_hz = rate_hz
        self._broker = broker
        self._stop_event = stop_event
        self.error: Optional[str] = None

    def run(self) -> None:
        try:
            # gain/agc sind bei NativeCliSource nur noch Kompatibilitaets-
            # Parameter (das eigentliche Gain ist bei den native-CLI-Geraeten
            # bereits fest in SDR_DEVICES[...]["args"] kodiert, z. B. "-g 20"
            # bei hackrf_transfer) - .get() statt [...], da die neuen
            # SDR_DEVICES-Eintraege kein "gain"-Feld mehr fuehren.
            self._source.open(self.center_hz, self.rate_hz,
                               self._device_cfg.get("gain", 0), self._device_cfg.get("agc", False))
        except Exception as exc:
            self.error = f"SDR-Oeffnen fehlgeschlagen: {exc}"
            print(f"[sdr] {self.error}", file=sys.stderr)
            return

        window = np.hanning(FFT_SIZE).astype(np.float32)
        avg_buffer: list[np.ndarray] = []
        min_interval_s = 1.0 / FRAME_FPS
        last_emit = 0.0

        try:
            while not self._stop_event.is_set():
                iq = self._source.read(FFT_SIZE)

                spectrum = np.fft.fftshift(np.fft.fft(iq * window))
                mag = np.abs(spectrum)
                with np.errstate(divide="ignore"):
                    mag_db = 20.0 * np.log10(mag)
                mag_db = np.maximum(mag_db, DB_FLOOR)  # Boden gegen -inf bei mag==0

                avg_buffer.append(mag_db)
                if len(avg_buffer) > FFT_AVERAGE_COUNT:
                    avg_buffer.pop(0)
                mag_db = np.mean(avg_buffer, axis=0)

                # Mitten-Bin (DC-Spike, SDR-typischer Mischer-Artefakt am
                # Nulldurchgang) durch den Nachbarwert ersetzen.
                mid = len(mag_db) // 2
                mag_db[mid] = mag_db[mid - 1]

                now = time.monotonic()
                if now - last_emit < min_interval_s:
                    continue
                last_emit = now

                levels = _downsample_max(mag_db, DISPLAY_BINS)
                levels_int = [int(round(v)) for v in levels]
                self._broker.publish_sdr(self.center_hz, self.rate_hz, self.rate_hz, levels_int)

        except Exception as exc:
            self.error = f"SDR-Stream-Fehler: {exc}"
            print(f"[sdr] {self.error}", file=sys.stderr)
        finally:
            self._source.close()


def _downsample_max(values: np.ndarray, out_bins: int) -> np.ndarray:
    """Reduziert 'values' (FFT_SIZE Bins) auf genau out_bins Anzeigewerte,
    je Ausgabe-Bin per Maximum - so bleiben schmale Signalspitzen sichtbar,
    statt beim Mitteln zwischen zwei Anzeigepixeln zu verschwinden. Setzt
    FFT_SIZE >> out_bins voraus (hier: 65536 vs. 900), sonst waeren
    einzelne Ausgabe-Bins leer."""
    n = len(values)
    edges = np.linspace(0, n, out_bins + 1).astype(int)
    return np.array([np.max(values[edges[i]:edges[i + 1]]) for i in range(out_bins)])


# ---------------------------------------------------------------------------
# SdrManager: laesst wie der EB200-Controller immer nur EINEN Worker
# gleichzeitig laufen (die Hardware kann ohnehin nur einmal geoeffnet
# werden) - start() beendet einen laufenden Worker deshalb immer zuerst.
# ---------------------------------------------------------------------------

class SdrManager:
    def __init__(self, broker: FrameBroker) -> None:
        self._broker = broker
        self._lock = threading.Lock()
        self._worker: Optional[SdrWorker] = None
        self._stop_event: Optional[threading.Event] = None

    def start(self, device_cfg: dict, center_hz: float, rate_hz: float) -> None:
        self.stop()
        stop_event = threading.Event()
        source = NativeCliSource(device_cfg)
        worker = SdrWorker(source, device_cfg, center_hz, rate_hz, self._broker, stop_event)
        with self._lock:
            self._worker = worker
            self._stop_event = stop_event
        worker.start()

    def stop(self) -> None:
        with self._lock:
            worker, stop_event = self._worker, self._stop_event
            self._worker = None
            self._stop_event = None
        if worker is not None:
            stop_event.set()
            worker.join(timeout=3.0)


# ---------------------------------------------------------------------------
# HTML_PAGE (Teil B) - eigenstaendige SDR-Wasserfall-Seite, kein Import,
# bildet nur das Render-Muster aus waterfall.py nach (siehe Moduldoku).
# ---------------------------------------------------------------------------

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SDR waterfall</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  /* T-1406: Farben nur noch als Tokens (eb200_core/ui_theme.py). Die
     Canvas-Spalte (Spektrum, Wasserfall, Achse, Zeit-Gutter, Katalogstreifen)
     bleibt in beiden Themes schwarz - Zeichenfarben unveraendert im Script. */
  canvas { border:1px solid var(--canvas-border); cursor:crosshair; }
  :root { --content-width: 966px; }
  h1, #meta-s, .wf-outer-s, #hint-s, #errorBox { max-width: var(--content-width); margin-left:auto; margin-right:auto; }
  h1 { font-size:1.15em; font-weight:600; color:var(--fg); margin:14px auto 10px; }
  #meta-s { font-size:0.9em; color:var(--fg-muted); margin-bottom:8px; }
  #hint-s { font-size:0.85em; color:var(--fg-muted); margin-top:8px; }
  #tooltip { position:fixed; background:var(--panel); color:var(--fg); border:1px solid var(--border-strong);
             box-shadow:var(--shadow); padding:4px 8px;
             font-size:0.8em; pointer-events:none; display:none; }
  .canvas-wrap { position:relative; }
  /* Zeit-Gutter neben der Canvas-Spalte, wie time-o in waterfall.py - oberer
     Rand in Hoehe von spec-s (120px), damit es neben wf-s zu liegen kommt. */
  .wf-outer-s { display:flex; align-items:flex-start; }
  #time-s { margin-top:120px; flex-shrink:0; }
  #errorBox { display:none; background:var(--danger-soft); border:1px solid var(--danger); color:var(--danger);
              border-radius:6px; padding:8px 12px; margin-bottom:8px; font-size:0.9em; }
  #toast { position:fixed; left:50%; bottom:24px; transform:translateX(-50%);
           background:var(--panel); border:1px solid var(--accent); color:var(--accent);
           border-radius:6px; box-shadow:var(--shadow); padding:6px 14px;
           font-size:0.9em; display:none; }
</style>
</head>
<body>
/*HEADER*/
<h1><span id="deviceLabel">?</span> &ndash; relative (dBFS, uncalibrated)</h1>
<div id="errorBox"></div>
<div id="meta-s">waiting for the first frame ...</div>
<div class="wf-outer-s">
  <canvas id="time-s" width="64" height="300"></canvas>
  <div class="canvas-wrap" id="canvasWrapS">
    <canvas id="spec-s" width="900" height="120"></canvas>
    <canvas id="wf-s" width="900" height="300"></canvas>
    <canvas id="axis-s" width="900" height="20"></canvas>
    <!-- Katalog-Streifen (bekannte Baender/Kanaele aus known_freq/), Block wortgleich
         aus waterfall.py. Liegt UNTER der Achse, damit das Zeit-Gutter (margin-top
         120px = spec-s) unveraendert neben wf-s bleibt. -->
    <canvas id="known-s" width="900" height="96"></canvas>
  </div>
</div>
<div id="hint-s">Clicking in the waterfall hands the clicked frequency to IFPAN
  (port 8000) as the zoom target. This window keeps running unchanged.</div>
<div id="toast"></div>
<div id="tooltip"></div>

<script>
const el = id => document.getElementById(id);
const specS = el('spec-s'), wfS = el('wf-s'), axisS = el('axis-s'), timeS = el('time-s');
const ctxSpecS = specS.getContext('2d'), ctxWfS = wfS.getContext('2d');
const ctxAxisS = axisS.getContext('2d'), ctxTimeS = timeS.getContext('2d');

// Ziel des Klick-Handoffs an IFPAN - EB200-Controller (waterfall.py) laeuft
// immer auf Port 8000, unabhaengig von diesem Dienst hier.
const EB200_ZOOM_URL = `http://${location.hostname}:8000/api/zoom`;

// Parameter aus der eigenen URL (von waterfall.py beim Oeffnen mitgegeben,
// siehe openSdrWaterfall dort): ?device=&center=&span=
const params = new URLSearchParams(location.search);
const deviceKey = params.get('device');
let centerHz = parseFloat(params.get('center'));
let spanHz = parseFloat(params.get('span'));
let rateHz = NaN;

el('deviceLabel').textContent = deviceKey || '(no device)';

function showError(text) {
  const box = el('errorBox');
  box.textContent = text;
  box.style.display = 'block';
}

function updateMetaFromStatus(status) {
  let text = `Center ${(status.center_hz / 1e6).toFixed(4)} MHz &middot; `
    + `Span ${(status.span_hz / 1e3).toFixed(1)} kHz &middot; `
    + `Rate ${(status.rate_hz / 1e6).toFixed(3)} MSps`;
  if (status.clamped) text += ' &middot; span limited to the maximum sample rate';
  el('meta-s').innerHTML = text;
}

// ---------------------------------------------------------------------------
// Offscreen-History-Canvas fuer den rollenden Wasserfall (Nachbildung des
// Musters aus waterfall.py, hier ohne Scrollback: die Puffergroesse
// entspricht genau der sichtbaren Flaeche, jede neue Zeile schiebt den
// Puffer um eine Zeile nach unten - siehe scrollHistoryDown()).
// ---------------------------------------------------------------------------
const historyCanvas = document.createElement('canvas');
historyCanvas.width = 900;
historyCanvas.height = 300;
const ctxHistory = historyCanvas.getContext('2d');
ctxHistory.fillStyle = '#000';
ctxHistory.fillRect(0, 0, historyCanvas.width, historyCanvas.height);

function scrollHistoryDown() {
  const w = historyCanvas.width, h = historyCanvas.height;
  ctxHistory.drawImage(historyCanvas, 0, 0, w, h - 1, 0, 1, w, h - 1);
}

// dieselbe Colormap wie in waterfall.py (levelToColor).
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

function paintRow(levels, lo, hi) {
  for (let x = 0; x < historyCanvas.width; x++) {
    ctxHistory.fillStyle = levelToColor(levels[x], lo, hi);
    ctxHistory.fillRect(x, 0, 1, 1);
  }
}

// dieselbe Skalen-Logik wie currentScaleBounds in waterfall.py, hier immer
// automatisch (keine manuellen Regler noetig - unkalibrierte dBFS-Anzeige).
function currentScaleBounds(levels) {
  const lo = Math.min(...levels) - 3;
  const hi = Math.max(...levels) + 3;
  return [lo, hi];
}

function drawSpectrum(levels) {
  const w = specS.width, h = specS.height;
  ctxSpecS.fillStyle = '#000'; ctxSpecS.fillRect(0, 0, w, h);
  const dataMin = Math.min(...levels), dataMax = Math.max(...levels);
  const range = Math.max(dataMax - dataMin, 1);
  const lo = dataMin - range * 0.05, hi = dataMax + range * 0.08;
  ctxSpecS.strokeStyle = '#8fd'; ctxSpecS.beginPath();
  levels.forEach((v, i) => {
    const x = (i / (levels.length - 1)) * w;
    const y = h - ((v - lo) / (hi - lo)) * h;
    if (i === 0) ctxSpecS.moveTo(x, y); else ctxSpecS.lineTo(x, y);
  });
  ctxSpecS.stroke();
}

// dieselbe Achsen-Logik wie drawAxis (IFPAN-Zoom) in waterfall.py.
function formatKHz(freqHz) {
  const khz = freqHz / 1000;
  const decimals = khz >= 10000 ? 0 : 1;
  return khz.toFixed(decimals) + ' kHz';
}
// -- Nice-Ticks: dieselbe Achsenteilung wie in waterfall.py (niceStepFor/
// computeNiceTicks). Kleinste "runde" Schrittweite aus {1,2,2.5,5}*10^k, die
// >= span/10 ist; Ticks ab ceil(start/step)*step. So liegen die Labels auf
// runden Werten (z. B. 35000/40000/45000 kHz) statt auf krummen Siebteln.
function niceStepFor(rawStep) {
  const multipliers = [1, 2, 2.5, 5, 10];
  const exponent = Math.floor(Math.log10(rawStep));
  for (let e = exponent - 1; e <= exponent + 1; e++) {
    for (const m of multipliers) {
      const candidate = m * Math.pow(10, e);
      if (candidate >= rawStep - 1e-9) return candidate;
    }
  }
  return Math.pow(10, exponent + 1);
}
function niceTicksHz(startHz, stopHz) {
  const span = stopHz - startHz;
  if (!(span > 0)) return [];
  const step = niceStepFor(span / 10);
  const ticks = [];
  for (let f = Math.ceil(startHz / step) * step; f <= stopHz + 1e-6; f += step) {
    ticks.push(Math.round(f));
  }
  return ticks;
}
function drawAxis(freqStartHz, freqStopHz) {
  const w = axisS.width, h = axisS.height;
  const span = freqStopHz - freqStartHz;
  ctxAxisS.fillStyle = '#000'; ctxAxisS.fillRect(0, 0, w, h);
  ctxAxisS.font = '10px sans-serif';
  if (!(span > 0)) return;
  // Bereichsgrenzen gedimmt an den Raendern, Nice-Ticks dazwischen; Ticks,
  // deren Label mit einem Randlabel kollidieren wuerde, werden unterdrueckt.
  // T-2007: Kollision mit der gemessenen Textbreite (measureText) statt eines
  // festen Randabstands - der war fuer lange Randlabels zu knapp. Das
  // Randlabel hat Vorrang (Muster tickLabelFrei in waterfall.py).
  const startLabel = formatKHz(freqStartHz);
  const stopLabel = formatKHz(freqStopHz);
  ctxAxisS.strokeStyle = '#555'; ctxAxisS.fillStyle = '#777';
  ctxAxisS.textAlign = 'left';  ctxAxisS.fillText(startLabel, 2, h - 3);
  ctxAxisS.textAlign = 'right'; ctxAxisS.fillText(stopLabel, w - 2, h - 3);
  const LABEL_GAP_PX = 6;
  const linksBis = 2 + ctxAxisS.measureText(startLabel).width + LABEL_GAP_PX;
  const rechtsAb = w - 2 - ctxAxisS.measureText(stopLabel).width - LABEL_GAP_PX;
  ctxAxisS.fillStyle = '#aaa'; ctxAxisS.textAlign = 'center';
  for (const f of niceTicksHz(freqStartHz, freqStopHz)) {
    const x = ((f - freqStartHz) / span) * w;
    const label = formatKHz(f);
    const halb = ctxAxisS.measureText(label).width / 2;
    if (x - halb < linksBis || x + halb > rechtsAb) continue;
    ctxAxisS.beginPath(); ctxAxisS.moveTo(x + 0.5, 0); ctxAxisS.lineTo(x + 0.5, 5); ctxAxisS.stroke();
    ctxAxisS.fillText(label, x, h - 3);
  }
}

// Zeit-Gutter (Nachbildung von drawTimeOverlay/time-o in waterfall.py):
// jede Zeile schiebt bestehende Marken um 1 nach unten, alle 30s kommt eine
// neue Marke bei Zeile 0 dazu; Marken, die unten aus dem Puffer laufen,
// werden verworfen.
let timeMarks = [];
let lastTimeBucket = null;
function advanceTimeGutter(tEpochSeconds) {
  timeMarks.forEach(m => m.row++);
  timeMarks = timeMarks.filter(m => m.row < historyCanvas.height);
  const bucket = Math.floor(tEpochSeconds / 30);
  if (bucket !== lastTimeBucket) {
    lastTimeBucket = bucket;
    timeMarks.push({ row: 0, label: isoTime(new Date(tEpochSeconds * 1000)) });
  }
  ctxTimeS.fillStyle = '#000'; ctxTimeS.fillRect(0, 0, timeS.width, timeS.height);
  ctxTimeS.strokeStyle = '#666'; ctxTimeS.fillStyle = '#aaa';
  ctxTimeS.font = '10px sans-serif'; ctxTimeS.textAlign = 'left'; ctxTimeS.textBaseline = 'middle';
  for (const m of timeMarks) {
    ctxTimeS.beginPath();
    ctxTimeS.moveTo(timeS.width - 6, m.row + 0.5);
    ctxTimeS.lineTo(timeS.width, m.row + 0.5);
    ctxTimeS.stroke();
    ctxTimeS.fillText(m.label, 2, m.row);
  }
}

// ---------------------------------------------------------------------------
// Start-Sequenz: /api/sdr/start beim Laden aufrufen, danach SSE konsumieren.
// ---------------------------------------------------------------------------
async function startSdr() {
  if (!deviceKey || !isFinite(centerHz) || !isFinite(spanHz)) {
    showError('No device or no center/span given in the URL (?device=&center=&span=).');
    return;
  }
  try {
    const res = await fetch(`/api/sdr/start?device=${encodeURIComponent(deviceKey)}`
      + `&center=${centerHz}&span=${spanHz}`);
    if (!res.ok) {
      showError(await res.text());
      return;
    }
    const status = await res.json();
    centerHz = status.center_hz; spanHz = status.span_hz; rateHz = status.rate_hz;
    updateMetaFromStatus(status);
  } catch (err) {
    showError('SDR service not reachable: ' + err);
  }
}
startSdr();

// Streifen-Canvases DIESER Seite (Host-Konfiguration, steht VOR dem geteilten Block).
const KNOWN_STRIPS = ['known-s'];
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

const evtSource = new EventSource('/events');
evtSource.onmessage = e => {
  const frame = JSON.parse(e.data);
  if (frame.type !== 'sdr') return;
  centerHz = frame.center_hz; spanHz = frame.span_hz; rateHz = frame.rate_hz;

  const [lo, hi] = currentScaleBounds(frame.levels);
  drawSpectrum(frame.levels);
  scrollHistoryDown();
  paintRow(frame.levels, lo, hi);
  ctxWfS.drawImage(historyCanvas, 0, 0);
  drawAxis(centerHz - spanHz / 2, centerHz + spanHz / 2);
  updateKnownStrip('known-s', centerHz - spanHz / 2, centerHz + spanHz / 2);
  advanceTimeGutter(frame.t);
};
evtSource.onerror = () => { el('meta-s').textContent = 'Connection to the SDR service lost ...'; };

// ---------------------------------------------------------------------------
// Klick im Wasserfall -> Frequenz an IFPAN uebergeben (Zoom-Handoff).
// ---------------------------------------------------------------------------
let toastTimer = null;
function showToast(text) {
  const t = el('toast');
  t.textContent = text;
  t.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.style.display = 'none'; }, 2000);
}

wfS.addEventListener('click', e => {
  const rect = wfS.getBoundingClientRect();
  const xFrac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const freqHz = Math.round((centerHz - spanHz / 2) + xFrac * spanHz);
  fetch(`${EB200_ZOOM_URL}?freq=${freqHz}`, { mode: 'no-cors' });
  showToast(`Handed over to IFPAN: ${(freqHz / 1e6).toFixed(4)} MHz`);
  // MoniScope-Hauptfenster (:8000) in den Vordergrund holen, damit der IFPAN
  // sofort sichtbar ist. Bevorzugt das oeffnende Fenster fokussieren (der
  // SDR-Wasserfall wurde von der :8000-Seite via window.open geoeffnet);
  // sonst per festem Fensternamen ein :8000-Fenster oeffnen/fokussieren.
  if (window.opener && !window.opener.closed) {
    window.opener.focus();
  } else {
    window.open(`http://${location.hostname}:8000/`, 'eb200_main');
  }
});
</script>
</body>
</html>
"""


# T-1406: Theme-Bausteine einsetzen (Tokens + Grundregeln, Head-Skript fuer das
# Theme-Cookie, Kopfleiste statt <h1>) - Muster wie :8040/:8020 (T-1402/T-1403).
# Der Geraetename (#deviceLabel) bleibt in der Seitenueberschrift, weil ihn das
# Script beim ersten Frame setzt.
HTML_PAGE = (HTML_PAGE.replace("/*THEME_CSS*/", THEME_CSS + BASE_CSS)
                      .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                      .replace("/*HEADER*/", header_html("SDR waterfall")))


# ---------------------------------------------------------------------------
# HTTP-Handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    broker: FrameBroker = None
    manager: SdrManager = None
    known_catalog = None            # Catalog aus eb200_core.known_freq, siehe main()

    def log_message(self, fmt, *args):
        pass

    def end_headers(self) -> None:
        # CORS: die D-SCAN-Seite (waterfall.py, Port 8000) ruft diese
        # Endpunkte per fetch() aus einem ANDEREN Origin (Port) auf - ohne
        # diesen Header verwirft der Browser die Antwort, obwohl der Server
        # korrekt geantwortet hat (Fehlerbild: faelschlich "SDR-Dienst nicht
        # erreichbar" im Frontend). Zentral hier gesetzt, damit er auf JEDER
        # Antwort dieses Handlers erscheint. "*" ist fuer diese LAN-Station
        # unkritisch (nur Geraeteliste/Status, keine sensiblen Daten) - bei
        # Bedarf spaeter auf die konkrete 8000-Herkunft einschraenkbar.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        # Kein Preflight bei einfachen GETs ohne Zusatzheader noetig, aber
        # das haelt den Dienst robust fuer spaetere Erweiterungen (z. B.
        # eigene Request-Header) - Antwort traegt dieselben CORS-Header
        # (siehe end_headers()).
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

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

        elif parsed.path == "/api/known_freq":
            # Katalog bekannter Frequenzen, kompakt (siehe Catalog.to_client_json).
            # Vom Frontend EINMALIG beim Laden geholt, Detailstufe entscheidet der
            # Client selbst. Rein lesend. Wortgleich zu waterfall.py.
            # load_catalog_cached() statt der beim Start geladenen Instanz, damit
            # Katalog-Edits (block_admin.py) ohne Neustart hier ankommen.
            # load_station_config() ebenso JE REQUEST (Region ist reine
            # Anzeigefilterung, wirkt so ohne Neustart wie Katalog-Edits).
            # load_view_config() genauso (T-126): die in :8010 abgewaehlten
            # Katalogdateien fallen hier aus bands/channels heraus.
            if self.known_catalog is None:
                self._send_json({"version": 1, "bands": [], "channels": [],
                                 "files": [], "errors": ["catalog not loaded"],
                                 "region": {"tags": []},
                                 "view": {"total": 0, "hidden": 0}})
            else:
                self._send_json(load_catalog_cached().to_client_json(
                    load_station_config(), load_view_config()))

        elif parsed.path == "/api/sdr/devices":
            result = []
            for d in SDR_DEVICES:
                entry = dict(d)
                entry["available"] = NativeCliSource.probe(d)
                result.append(entry)
            self._send_json(result)

        elif parsed.path == "/api/sdr/start":
            self._handle_start(parse_qs(parsed.query))

        elif parsed.path == "/api/sdr/stop":
            self.manager.stop()
            self._send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

    def _handle_start(self, params: dict) -> None:
        device_key = params.get("device", [""])[0]
        device_cfg = _device_by_key(device_key)
        # KEIN probe() hier: enumerate() oeffnet/schliesst das Geraet kurz, ein
        # unmittelbar folgendes make() scheitert auf Windows/libusb (Airspy) mit
        # "no match". Verfuegbarkeit ist im Dropdown (/api/sdr/devices) bereits
        # geprueft; das echte open() im Worker beweist sie ohnehin.
        if device_cfg is None:
            self._send_text(409, f"unknown device '{device_key}'")
            return

        try:
            center_hz = float(params["center"][0])
            span_hz = float(params["span"][0])
        except (KeyError, ValueError, IndexError):
            self._send_text(400, "center/span are missing or invalid")
            return

        if not (device_cfg["band_min_hz"] <= center_hz - span_hz / 2
                and center_hz + span_hz / 2 <= device_cfg["band_max_hz"]):
            self._send_text(409, f"range outside the receive band of {device_cfg['label']} "
                                  f"({device_cfg['band_min_hz']/1e6:g}-{device_cfg['band_max_hz']/1e6:g} MHz)")
            return

        sample_rates = device_cfg["sample_rates"]
        max_rate = max(sample_rates)
        clamped = False
        if span_hz > max_rate:
            span_hz = max_rate
            clamped = True
        rate_hz = next((r for r in sample_rates if r >= span_hz), max_rate)

        self.manager.start(device_cfg, center_hz, rate_hz)
        self._send_json({
            "ok": True, "center_hz": center_hz, "rate_hz": rate_hz,
            "span_hz": rate_hz, "clamped": clamped,
        })

    def _send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status: int = 200) -> None:
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
    broker = FrameBroker()
    manager = SdrManager(broker)

    Handler.broker = broker
    Handler.manager = manager
    # Katalog bekannter Frequenzen (known_freq/*.json) EINMAL beim Start laden.
    # load_catalog() wirft nie - defekte Dateien landen als Text in .errors.
    Handler.known_catalog = load_catalog()
    kc = Handler.known_catalog
    print(f"[known_freq] {len(kc.channels)} Kanaele, {len(kc.bands)} Baender "
          f"aus {len(kc.files)} Dateien, {len(kc.errors)} Fehler")
    for err in kc.errors:
        print(f"[known_freq] WARNUNG: {err}", file=sys.stderr)
    httpd = ThreadingHTTPServer((HTTP_HOST, SDR_HTTP_PORT), Handler)

    print(f"[http] SDR-Wasserfall verfuegbar unter http://localhost:{SDR_HTTP_PORT}/ "
          f"(auch von anderen Geraeten im LAN erreichbar)")
    register_server("sdr_waterfall", SDR_HTTP_PORT)
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
        manager.stop()
        httpd.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
