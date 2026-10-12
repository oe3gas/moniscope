#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/job_runner.py

Fuehrt einen Long-Run-Job DATENSEITIG aus (WS3, Abschnitt 11.5) - bekommt
fertige Sweeps/Frames vom jeweiligen Empfangs-Pfad als passiven Abgriff
(Vorbild: eb200_core/session_store.py, Duck-Typing, KEIN Import aus
waterfall.py), schreibt sie in eine rotierende Folge von .specbin-Dateien
im Job-Verzeichnis und meldet periodische Heartbeats an den JobStore. SCPI-
Ansteuerung des Empfaengers und die Zeitplanung (wann ein Job startet/
endet) bleiben in waterfall.py - dieses Modul kennt nur "hier kommt ein
fertiger Sweep/Frame an, verarbeite ihn" und enthaelt KEINEN Geraete-/
Netzwerkzugriff. Daher per Selbsttest ohne Hardware pruefbar. Nur Python-
Standardbibliothek.

Zwei Recorder, je Job-Typ (WS3 11.2: D-SCAN dezimiert per Zeitraster,
IFPAN laeuft bewusst OHNE Dezimierung, volle Framerate):
  - DscanJobRecorder (siehe unten) - dezimiert per DscanMaxholdWriter
    (eb200_core/dscan_store.py) zu Max-Hold-Intervallen.
  - IfpanJobRecorder (siehe unten) - schreibt jeden Frame 1:1 per
    SpectrumBinWriter (eb200_core/spectrum_store.py), identisches Format
    zur bestehenden Ad-hoc-Spektrumaufnahme (eb200-ifpan-bins-v1).

Ablauf einer D-SCAN-Aufzeichnung (siehe DscanJobRecorder):
  Phase "messen" - die ersten zwei Sweeps werden gepuffert, um daraus die
  tatsaechliche Sweep-Rate (dt) zu bestimmen. Ist decimation_s gesetzt,
  ergibt sich daraus der Dezimierungsfaktor k = round(decimation_s / dt)
  (fuer den DscanMaxholdWriter, siehe dscan_store.py); ist decimation_s
  None, ist k = 1 (jeder Sweep wird 1:1 als eigenes Max-Hold-"Intervall"
  aus einem Sub-Sweep geschrieben - WS3 11.2/11.3: kein Sonderfall im
  Schreiber, siehe dscan_store.DscanMaxholdWriter). Danach oeffnet der
  Recorder die erste Ausgabedatei mit diesem k und speist die gepufferten
  Sweeps nach.

  Phase "schreiben" - jeder weitere Sweep geht direkt an den Writer.
  Rotation (rotation_min) schliesst die aktuelle Datei (finale Sidecar)
  und oeffnet die naechste MIT DERSELBEN Geometrie/demselben k - ein
  Long-Run laeuft mit eingefrorenen Parametern, k wird nach der Messphase
  nie wieder neu bestimmt. Heartbeats (alle heartbeat_s Sekunden, Zaehlung
  ueber die SWEEP-Zeit t, nicht die Wanduhr) checkpointen die aktive Datei
  und aktualisieren den JobStore.

  Geometriewechsel mitten im Lauf (Start/Stop/Schritt/Bin-Zahl eines
  Sweeps weicht vom ersten Sweep ab) beendet die Aufzeichnung: das Flag
  self.failed wird gesetzt, die bereits geschriebene Datei sauber
  geschlossen - OHNE eine Exception in den Aufrufer (den Overview-Loop)
  zu werfen. record() faengt zusaetzlich JEDEN unerwarteten Fehler intern
  ab und setzt self.failed statt zu propagieren: der laufende D-SCAN-
  Empfang darf NIE wegen eines Aufzeichnungsproblems abstuerzen.
  waterfall.py fragt self.failed periodisch ab (Scheduler-Loop) und
  bricht den Job dann ordentlich ab (siehe dortige _job_scheduler_loop).
  failed_code traegt einen sprachneutralen Code aus job_store.ABORT_CODES, failed die
  englische Einzelheit; beide werden immer gemeinsam gesetzt.
"""

from __future__ import annotations

import os
import sys

# Erlaubt sowohl "python -m eb200_core.job_runner" als auch den direkten
# Skriptaufruf "python eb200_core/job_runner.py" (Selbsttest-Konvention
# dieses Projekts, siehe Schritt-3-Validierung): beim Direktaufruf fehlt
# das Repo-Root im Suchpfad fuer den nachfolgenden eb200_core-Import.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.dscan_store import DscanMaxholdWriter
from eb200_core.spectrum_store import SpectrumBinWriter


class DscanJobRecorder:
    """
    Ein Recorder je aktivem D-SCAN-Long-Run-Job. Eine Instanz nimmt ueber
    record() fortlaufend Sweeps entgegen und schreibt sie (dezimiert,
    rotiert, mit Heartbeats) in eine Folge von .specbin-Dateien im
    Job-Verzeichnis. job_store wird nur fuer heartbeat() gebraucht (Duck-
    Typing, kein Import von eb200_core.job_store noetig) - Zustandswechsel
    (geplant/laeuft/beendet/...) bleiben Sache von waterfall.py.
    """

    def __init__(self, job_store, job_manifest: dict) -> None:
        self.job_store = job_store
        self.job_id = job_manifest["job_id"]
        self.job_dir = job_manifest["job_dir"]
        self.decimation_s = job_manifest.get("decimation_s")
        # Empfaenger-Parameter aus dem Manifest fuer die Sidecar-Dokumentation
        # (eingefrorene Long-Run-Parameter, WS3 11.5): speed und mtime_ms
        # stehen NICHT in den D-SCAN-Datagrammen und muessen deshalb aus dem
        # Manifest in jede Datei-Sidecar durchgereicht werden. mtime_ms=None
        # bedeutet DEFAULT (Annex E) und wird bewusst mitgeschrieben.
        p = job_manifest.get("params", {})
        self._sidecar_extra = {"speed": p.get("speed"), "mtime_ms": p.get("mtime_ms")}
        self.rotation_s = float(job_manifest.get("rotation_min", 60)) * 60.0
        self.heartbeat_s = 300.0

        # Phase "messen": Sweeps puffern, bis genug t-Werte fuer eine
        # Ratenmessung vorliegen. Phase "schreiben": laufender Betrieb.
        self.phase = "messen"
        self._measure_buffer: list = []   # (sweep_no, t, levels)
        self.sweep_no = 0                 # der Runner IST die Quelle der Sweep-Nummern
        self.k: int | None = None
        self.geometry: dict | None = None

        self._writer: DscanMaxholdWriter | None = None
        self._part_no = 0
        self._file_start_t: float | None = None
        self._last_heartbeat_t: float | None = None
        self._closed_frames = 0           # Summe ueber bereits geschlossene Dateien
        self._closed_bytes = 0

        self.failed: str | None = None
        self.failed_code: str | None = None
        self._closed = False

        self.stats = {
            "sweeps": 0, "frames": 0, "files": [], "bytes": 0,
            "geometry": None, "last_t": None,
        }

    def record(self, update) -> None:
        """
        Nimmt ein DScanUpdate entgegen (Duck-Typing wie session_store.py:
        .complete/.start_hz/.stop_hz/.step_hz/.t/.full_raw_levels). Tut
        nichts bei unfertigem Sweep, bereits beendeter Aufzeichnung oder
        nach einem Fehler (self.failed gesetzt). Wirft NIE eine Ausnahme in
        den Aufrufer - siehe Moduldoku."""
        if self._closed or self.failed is not None:
            return
        if not update.complete or update.full_raw_levels is None:
            return
        try:
            self._handle_sweep(update.start_hz, update.stop_hz, update.step_hz,
                                update.t, update.full_raw_levels)
        except Exception as exc:
            self.failed_code = "recording_error"
            self.failed = f"{exc!r}"
            self._close_current_writer()

    def _handle_sweep(self, start_hz: int, stop_hz: int, step_hz, t: float, levels) -> None:
        n_bins = len(levels)
        if self.geometry is None:
            self.geometry = {"start_hz": start_hz, "stop_hz": stop_hz,
                              "step_hz": step_hz, "n_bins": n_bins}
            self.stats["geometry"] = dict(self.geometry)
        elif (start_hz, stop_hz, step_hz, n_bins) != (
            self.geometry["start_hz"], self.geometry["stop_hz"],
            self.geometry["step_hz"], self.geometry["n_bins"],
        ):
            self.failed_code = "geometry_changed"
            self.failed = (
                f"was {self.geometry['start_hz']}-{self.geometry['stop_hz']} Hz / "
                f"{self.geometry['n_bins']} bins, now {start_hz}-{stop_hz} Hz / {n_bins} bins"
            )
            self._close_current_writer()
            return

        sweep_no = self.sweep_no
        self.sweep_no += 1
        self.stats["sweeps"] += 1
        self.stats["last_t"] = t

        if self.phase == "messen":
            self._measure_buffer.append((sweep_no, t, levels))
            if len(self._measure_buffer) < 2:
                return
            t1 = self._measure_buffer[0][1]
            t2 = self._measure_buffer[1][1]
            dt = t2 - t1
            if self.decimation_s is None or dt <= 0:
                # decimation_s < dt (bzw. dt <= 0 als Entartung) ergibt
                # ebenfalls k=1 - eine Dezimierung kann nicht feiner sein
                # als die tatsaechliche Sweep-Rate, das ist kein Fehlerfall.
                self.k = 1
            else:
                self.k = max(1, min(255, round(self.decimation_s / dt)))
            self._open_next_file(self._measure_buffer[0][1])
            for buffered_no, buffered_t, buffered_levels in self._measure_buffer:
                self._writer.add_sweep(buffered_no, buffered_t, buffered_levels)
            self._measure_buffer = []
            self.phase = "schreiben"
            self._maybe_heartbeat(t)
            return

        # Phase "schreiben".
        if t - self._file_start_t >= self.rotation_s:
            self._rotate_file(t)
        self._writer.add_sweep(sweep_no, t, levels)
        self._maybe_heartbeat(t)

    # -- Dateiverwaltung -----------------------------------------------

    def _next_part_path(self) -> str:
        self._part_no += 1
        filename = f"longrun_{self.job_id}_part{self._part_no:02d}.specbin"
        return os.path.join(self.job_dir, filename)

    def _open_next_file(self, t0: float) -> None:
        path = self._next_part_path()
        self._writer = DscanMaxholdWriter(
            path, start_hz=self.geometry["start_hz"], stop_hz=self.geometry["stop_hz"],
            step_hz=self.geometry["step_hz"], n_bins=self.geometry["n_bins"],
            decimation_k=self.k, extra_meta=self._sidecar_extra,
        )
        self._file_start_t = t0
        self._last_heartbeat_t = t0
        self.stats["files"].append(os.path.basename(path))

    def _rotate_file(self, t: float) -> None:
        # Geometrie und k sind eingefroren (WS3 11.5) - die Rotation oeffnet
        # nur eine neue Datei MIT DERSELBEN Dezimierung, k wird NICHT neu
        # gemessen (sonst waeren Max-Hold-Intervalle ueber Dateigrenzen
        # hinweg nicht mehr miteinander vergleichbar).
        self._close_current_writer()
        self._open_next_file(t)

    def _close_current_writer(self) -> None:
        if self._writer is None:
            return
        self._writer.close()
        self._closed_frames += self._writer.frames_written
        self._closed_bytes += self._writer.frames_written * self._writer.framesize
        self._writer = None

    def current_stats(self) -> dict:
        """Liefert self.stats mit LIVE nachgerechneten frames/bytes (aus dem
        gerade offenen Writer, falls vorhanden, plus der Summe bereits
        geschlossener Dateien). self.stats["frames"]/["bytes"] werden NICHT
        bei jedem Sweep geschrieben (waere unnoetiger Overhead im Hot Path),
        deshalb muss jeder Aufrufer, der einen AKTUELLEN Stand braucht -
        insbesondere waterfall.py.job_active() fuer /api/longrun/status -
        diese Methode aufrufen statt direkt self.stats zu lesen."""
        open_frames = self._writer.frames_written if self._writer is not None else 0
        open_bytes = (self._writer.frames_written * self._writer.framesize
                      if self._writer is not None else 0)
        self.stats["frames"] = self._closed_frames + open_frames
        self.stats["bytes"] = self._closed_bytes + open_bytes
        return self.stats

    def _maybe_heartbeat(self, t: float) -> None:
        if t - self._last_heartbeat_t < self.heartbeat_s:
            return
        self._last_heartbeat_t = t
        if self._writer is not None:
            self._writer.checkpoint()
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=dict(stats), files=list(stats["files"]))

    def close(self) -> dict:
        """Idempotent: schliesst den aktiven Writer (falls vorhanden, mit
        finaler Sidecar), schreibt ein letztes Heartbeat und liefert die
        Endstatistik. Ein wiederholter Aufruf liefert dieselben stats ohne
        weitere Seiteneffekte."""
        if self._closed:
            return self.current_stats()
        self._closed = True
        self._close_current_writer()
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=dict(stats), files=list(stats["files"]))
        return stats


class IfpanRecordFrame:
    """
    Ein einzelner IFPAN-Frame fuer IfpanJobRecorder.record() - traegt
    dieselben Groessen, die der Bin-Recorder-Pfad in waterfall.py auch an
    den bisherigen Ad-hoc-SpectrumBinWriter uebergibt (Geometrie + seq + t
    + Rohwerte). Reiner Daten-Container (Vorbild _FakeUpdate weiter unten,
    nur fuer den Produktivbetrieb statt nur den Selbsttest): job_runner.py
    haengt dadurch NICHT von waterfall.py ab - waterfall.py importiert
    diese Klasse von HIER, nicht umgekehrt.
    """

    def __init__(self, center_hz: int, span_hz: int, bw_hz: int, demod: str,
                 seq: int, t: float, levels) -> None:
        self.center_hz = center_hz
        self.span_hz = span_hz
        self.bw_hz = bw_hz
        self.demod = demod
        self.seq = seq
        self.t = t
        self.levels = levels


class IfpanJobRecorder:
    """
    Ein Recorder je aktivem IFPAN-Long-Run-Job. Wie DscanJobRecorder, aber
    OHNE Messphase/Dezimierung (WS3 11.2: IFPAN laeuft mit VOLLER
    Framerate) - jeder Frame wird 1:1 an einen SpectrumBinWriter
    (eb200_core/spectrum_store.py) weitergereicht: identisches
    Dateiformat (eb200-ifpan-bins-v1) wie die bestehende Ad-hoc-
    Spektrumaufnahme, inkl. deren BW-Fenster-Zuschnitt (siehe
    SpectrumBinWriter/compute_window - bw_hz >= span_hz clampt auf den
    vollen Frame, sonst wird auf das BW-Fenster geschnitten).
    """

    def __init__(self, job_store, job_manifest: dict) -> None:
        self.job_store = job_store
        self.job_id = job_manifest["job_id"]
        self.job_dir = job_manifest["job_dir"]
        self.rotation_s = float(job_manifest.get("rotation_min", 60)) * 60.0
        self.heartbeat_s = 300.0

        self.geometry: dict | None = None

        self._writer: SpectrumBinWriter | None = None
        self._part_no = 0
        self._file_start_t: float | None = None
        self._last_heartbeat_t: float | None = None
        self._closed_frames = 0           # Summe ueber bereits geschlossene Dateien
        self._closed_bytes = 0

        self.failed: str | None = None
        self.failed_code: str | None = None
        self._closed = False

        self.stats = {
            "frames": 0, "files": [], "bytes": 0,
            "geometry": None, "last_t": None,
        }

        # Vom Job-Audio-Thread (waterfall.py, Schritt 4c) befuellte Liste
        # von WAV-Dateinamen - separat von self.stats["files"] (das sind
        # NUR die .specbin-Teile dieses Recorders), aber gemeinsam mit
        # ihnen ans Manifest gemeldet (siehe _maybe_heartbeat()/close()),
        # damit GET /api/longrun/status auch die Audiodateien auflistet.
        self.extra_files: list = []

    def record(self, frame) -> None:
        """
        Nimmt einen IfpanRecordFrame entgegen (Duck-Typing: .center_hz/
        .span_hz/.bw_hz/.demod/.seq/.t/.levels). Wirft NIE eine Ausnahme in
        den Aufrufer (Muster DscanJobRecorder.record())."""
        if self._closed or self.failed is not None:
            return
        try:
            self._handle_frame(frame.center_hz, frame.span_hz, frame.bw_hz,
                                frame.demod, frame.seq, frame.t, frame.levels)
        except Exception as exc:
            self.failed_code = "recording_error"
            self.failed = f"{exc!r}"
            self._close_current_writer()

    def _handle_frame(self, center_hz: int, span_hz: int, bw_hz: int, demod: str,
                       seq: int, t: float, levels) -> None:
        n_full_bins = len(levels)
        if self.geometry is None:
            self.geometry = {"center_hz": center_hz, "span_hz": span_hz,
                              "bw_hz": bw_hz, "demod": demod, "n_full_bins": n_full_bins}
            self.stats["geometry"] = dict(self.geometry)
            self._open_next_file(t)
        elif (center_hz, span_hz, bw_hz, demod, n_full_bins) != (
            self.geometry["center_hz"], self.geometry["span_hz"], self.geometry["bw_hz"],
            self.geometry["demod"], self.geometry["n_full_bins"],
        ):
            self.failed_code = "geometry_changed"
            self.failed = (
                f"was {self.geometry['center_hz']} Hz center / {self.geometry['span_hz']} Hz span / "
                f"{self.geometry['n_full_bins']} bins, now {center_hz} Hz center / "
                f"{span_hz} Hz span / {n_full_bins} bins"
            )
            self._close_current_writer()
            return
        elif t - self._file_start_t >= self.rotation_s:
            self._rotate_file(t)

        self._writer.write_frame(levels, seq, t)
        self.stats["last_t"] = t
        self._maybe_heartbeat(t)

    # -- Dateiverwaltung -----------------------------------------------

    def _next_part_path(self) -> str:
        self._part_no += 1
        filename = f"longrun_{self.job_id}_part{self._part_no:02d}.specbin"
        return os.path.join(self.job_dir, filename)

    def _open_next_file(self, t0: float) -> None:
        path = self._next_part_path()
        self._writer = SpectrumBinWriter(
            path, center_hz=self.geometry["center_hz"], span_hz=self.geometry["span_hz"],
            n_full_bins=self.geometry["n_full_bins"], bw_hz=self.geometry["bw_hz"],
            demod=self.geometry["demod"],
        )
        self._file_start_t = t0
        self._last_heartbeat_t = t0
        self.stats["files"].append(os.path.basename(path))

    def _rotate_file(self, t: float) -> None:
        # Geometrie ist eingefroren (WS3 11.5) - die Rotation oeffnet nur
        # eine neue Datei MIT DERSELBEN Geometrie.
        self._close_current_writer()
        self._open_next_file(t)

    def _close_current_writer(self) -> None:
        if self._writer is None:
            return
        self._writer.close()
        self._closed_frames += self._writer.frames_written
        self._closed_bytes += self._writer.frames_written * self._writer.framesize
        self._writer = None

    def current_stats(self) -> dict:
        """Siehe DscanJobRecorder.current_stats() - frames/bytes werden
        live nachgerechnet, nicht bei jedem Frame geschrieben.

        self.stats["files"] bleibt dabei bewusst NUR die .specbin-Teile
        dieses Recorders (interner Zustand, wird an mehreren Stellen als
        Referenz weitergegeben) - die zurueckgegebene Kopie mischt
        zusaetzlich self.extra_files (WAV-Dateinamen des Job-Audio-Threads,
        siehe waterfall.py) in "files" ein, damit sowohl die LIVE-Ansicht
        (job_active() in waterfall.py) als auch Heartbeat/close() unten
        dieselbe, vollstaendige Dateiliste sehen."""
        open_frames = self._writer.frames_written if self._writer is not None else 0
        open_bytes = (self._writer.frames_written * self._writer.framesize
                      if self._writer is not None else 0)
        self.stats["frames"] = self._closed_frames + open_frames
        self.stats["bytes"] = self._closed_bytes + open_bytes
        result = dict(self.stats)
        result["files"] = list(self.stats["files"]) + list(self.extra_files)
        return result

    def _maybe_heartbeat(self, t: float) -> None:
        if t - self._last_heartbeat_t < self.heartbeat_s:
            return
        self._last_heartbeat_t = t
        if self._writer is not None:
            self._writer.checkpoint()
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=dict(stats), files=list(stats["files"]))

    def close(self) -> dict:
        """Idempotent - siehe DscanJobRecorder.close()."""
        if self._closed:
            return self.current_stats()
        self._closed = True
        self._close_current_writer()
        stats = self.current_stats()
        self.job_store.heartbeat(self.job_id, stats=dict(stats), files=list(stats["files"]))
        return stats


# ---------------------------------------------------------------------------
# Selbsttest - "python job_runner.py" direkt ausfuehren, ohne Geraet/Hardware
# ---------------------------------------------------------------------------

class _FakeUpdate:
    """Leichtgewichtiger Stand-in fuer waterfall.DScanUpdate, nur fuer den
    Selbsttest - job_runner.py bleibt eigenstaendig und importiert
    waterfall.py nicht (siehe record(), Duck-Typing). Identisches Muster
    wie session_store._FakeUpdate."""

    def __init__(self, complete: bool, start_hz: int, stop_hz: int, step_hz,
                 t: float, full_raw_levels) -> None:
        self.complete = complete
        self.start_hz = start_hz
        self.stop_hz = stop_hz
        self.step_hz = step_hz
        self.t = t
        self.full_raw_levels = full_raw_levels


def _self_test() -> None:
    import json
    import tempfile
    from datetime import datetime, timedelta

    from eb200_core.job_store import JobStore
    from eb200_core.spectrum_reader import FORMAT_ID as IFPAN_FORMAT_ID, SpectrumRecording

    with tempfile.TemporaryDirectory() as tmp:
        index_path = os.path.join(tmp, "jobs", "index.json")
        roots_path = os.path.join(tmp, "storage_roots.json")
        records_dir = os.path.join(tmp, "records")
        os.makedirs(records_dir, exist_ok=True)
        with open(roots_path, "w", encoding="utf-8") as f:
            json.dump([records_dir], f)
        store = JobStore(index_path=index_path, storage_roots_path=roots_path)

        def new_job(label, start_iso, decimation_s, rotation_min=60, speed="NORM", mtime_ms=None):
            # Jeder Testjob bekommt ein REGULAERES, geschlossenes Zeitfenster
            # (1 h) statt end_iso=None - ein offenes Ende wuerde per
            # Ueberlappungspruefung (job_store.py) alles zeitlich Spaetere
            # blockieren, was hier nur die Testjobs untereinander stoeren
            # wuerde (fuer DscanJobRecorder selbst spielt end_iso keine Rolle).
            end_iso = (datetime.fromisoformat(start_iso) + timedelta(hours=1)).isoformat(
                timespec="seconds"
            )
            m = store.create_job(
                type="dscan", label=label,
                params={"start_hz": 88_000_000, "stop_hz": 100_000_000,
                        "speed": speed, "mtime_ms": mtime_ms},
                start_iso=start_iso, end_iso=end_iso, decimation_s=decimation_s,
                rotation_min=rotation_min,
            )
            store.set_state(m["job_id"], "laeuft")
            return m

        # --- a) decimation_s=1.0, 0,5-s-Takt -> k=2 -----------------------
        manifest_a = new_job("Test A", "2020-01-01T00:00:00+01:00", decimation_s=1.0)
        rec_a = DscanJobRecorder(store, manifest_a)
        for i in range(8):
            levels = [i * 5, 200 - i]
            rec_a.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, i * 0.5, levels))
        stats_a = rec_a.close()
        assert rec_a.k == 2, rec_a.k
        assert stats_a["sweeps"] == 8, stats_a
        assert stats_a["frames"] == 4, stats_a
        assert len(stats_a["files"]) == 1, stats_a["files"]
        print(f"OK  decimation_s=1.0 bei 0,5s-Takt -> k={rec_a.k}, "
              f"{stats_a['sweeps']} Sweeps -> {stats_a['frames']} Frames")

        part1_path = os.path.join(manifest_a["job_dir"], stats_a["files"][0])
        with SpectrumRecording(part1_path) as spec:
            assert spec.n_frames == 4, spec.n_frames
            expected_max = [[5, 200], [15, 198], [25, 196], [35, 194]]
            expected_seq = [0, 2, 4, 6]
            for idx in range(4):
                seq, _t, levels_arr = spec.frame(idx)
                assert seq == expected_seq[idx], (idx, seq)
                assert list(levels_arr) == expected_max[idx], (idx, list(levels_arr))
                assert spec.counts(idx) == [2, 2], (idx, spec.counts(idx))
            # speed/mtime_ms aus dem Manifest muessen ueber extra_meta in
            # jeder Sidecar landen (WS3 11.5) - Test A laeuft mit speed=NORM/
            # mtime_ms=None (DEFAULT), siehe new_job()-Default.
            side = spec.meta
            assert side["speed"] == "NORM", side
            assert side["mtime_ms"] is None, side
        print("OK  Frame-Inhalte (Max/Zaehler/seq) via SpectrumRecording exakt")
        print("OK  Sidecar dokumentiert speed=NORM/mtime_ms=None (DEFAULT)")

        # --- a2) speed=MTIM/mtime_ms werden in die Sidecar durchgereicht ---
        manifest_a2 = new_job("Test A2", "2020-01-01T12:00:00+01:00", decimation_s=None,
                               speed="MTIM", mtime_ms=250.0)
        rec_a2 = DscanJobRecorder(store, manifest_a2)
        for i in range(2):
            rec_a2.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, i * 1.0, [i, i]))
        stats_a2 = rec_a2.close()
        part_a2_path = os.path.join(manifest_a2["job_dir"], stats_a2["files"][0])
        with SpectrumRecording(part_a2_path) as spec_a2:
            side_a2 = spec_a2.meta
            assert side_a2["speed"] == "MTIM", side_a2
            assert side_a2["mtime_ms"] == 250.0, side_a2
        print("OK  Sidecar dokumentiert speed=MTIM/mtime_ms=250.0")

        # --- b) decimation_s=None -> k=1, Frame je Sweep -------------------
        manifest_b = new_job("Test B", "2020-01-01T02:00:00+01:00", decimation_s=None)
        rec_b = DscanJobRecorder(store, manifest_b)
        for i in range(5):
            rec_b.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, i * 1.0, [i, i]))
        stats_b = rec_b.close()
        assert rec_b.k == 1, rec_b.k
        assert stats_b["frames"] == 5, stats_b
        print(f"OK  decimation_s=None -> k={rec_b.k}, {stats_b['frames']} Frames (1 je Sweep)")

        # --- c) Rotation: zwei Dateien -------------------------------------
        manifest_c = new_job("Test C", "2020-01-01T04:00:00+01:00",
                              decimation_s=None, rotation_min=1)   # 60 s
        rec_c = DscanJobRecorder(store, manifest_c)
        for i in range(8):
            rec_c.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, i * 10.0, [i, i]))
        stats_c = rec_c.close()
        expected_files_c = [
            f"longrun_{manifest_c['job_id']}_part01.specbin",
            f"longrun_{manifest_c['job_id']}_part02.specbin",
        ]
        assert stats_c["files"] == expected_files_c, stats_c["files"]
        assert stats_c["frames"] == 8, stats_c
        for fname in stats_c["files"]:
            with open(os.path.join(manifest_c["job_dir"], fname + ".json"), encoding="utf-8") as f:
                side = json.load(f)
            assert side.get("running", False) is False, side
        manifest_c_final = store.get(manifest_c["job_id"])
        assert manifest_c_final["files"] == expected_files_c, manifest_c_final["files"]
        print(f"OK  Rotation ausgeloest: {stats_c['files']} (je finale Sidecar, Manifest aktuell)")

        # --- d) Heartbeat bei grossem t-Sprung -----------------------------
        manifest_d = new_job("Test D", "2020-01-01T06:00:00+01:00",
                              decimation_s=None, rotation_min=120)
        rec_d = DscanJobRecorder(store, manifest_d)
        rec_d.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 0.0, [1, 1]))
        rec_d.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 1.0, [2, 2]))
        rec_d.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 400.0, [3, 3]))  # Sprung > 300 s

        manifest_d_mid = store.get(manifest_d["job_id"])
        assert manifest_d_mid["heartbeat_iso"] is not None, manifest_d_mid
        active_file_d = f"longrun_{manifest_d['job_id']}_part01.specbin"
        sidecar_path_d = os.path.join(manifest_d["job_dir"], active_file_d + ".json")
        with open(sidecar_path_d, encoding="utf-8") as f:
            side_d = json.load(f)
        assert side_d.get("running") is True, side_d
        print("OK  Heartbeat bei t-Sprung > 300s: heartbeat_iso gesetzt, aktive Sidecar running=true")

        rec_d.close()
        with open(sidecar_path_d, encoding="utf-8") as f:
            side_d_final = json.load(f)
        assert side_d_final.get("running", False) is False, side_d_final
        print("OK  nach close(): Sidecar running entfaellt")

        # --- e) Geometriewechsel mitten im Lauf ----------------------------
        manifest_e = new_job("Test E", "2020-01-01T08:00:00+01:00", decimation_s=None)
        rec_e = DscanJobRecorder(store, manifest_e)
        rec_e.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 0.0, [1, 1]))
        rec_e.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 1.0, [2, 2]))
        assert rec_e.failed is None
        rec_e.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 2.0, [3, 3, 3]))  # andere Bin-Zahl
        assert rec_e.failed_code == "geometry_changed", (rec_e.failed_code, rec_e.failed)
        # weitere Sweeps duerfen KEINEN Fehler mehr ausloesen (No-Op).
        rec_e.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 3.0, [4, 4, 4]))
        stats_e = rec_e.close()
        assert len(stats_e["files"]) == 1, stats_e["files"]
        with open(os.path.join(manifest_e["job_dir"], stats_e["files"][0] + ".json"), encoding="utf-8") as f:
            side_e = json.load(f)
        assert side_e.get("running", False) is False, side_e   # sauber geschlossen
        print(f"OK  Geometriewechsel -> failed_code={rec_e.failed_code!r}, failed={rec_e.failed!r}, Datei sauber geschlossen, kein Absturz")

        # --- f) unfertige Sweeps werden ignoriert --------------------------
        manifest_f = new_job("Test F", "2020-01-01T10:00:00+01:00", decimation_s=None)
        rec_f = DscanJobRecorder(store, manifest_f)
        rec_f.record(_FakeUpdate(False, 88_000_000, 100_000_000, 1000, 0.0, None))
        rec_f.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 1.0, [1, 1]))
        rec_f.record(_FakeUpdate(False, 88_000_000, 100_000_000, 1000, 2.0, None))
        rec_f.record(_FakeUpdate(True, 88_000_000, 100_000_000, 1000, 3.0, [2, 2]))
        stats_f = rec_f.close()
        assert stats_f["sweeps"] == 2, stats_f["sweeps"]
        print(f"OK  unfertige Sweeps (complete=False) ignoriert: sweeps={stats_f['sweeps']}")

        # ===================================================================
        # IfpanJobRecorder - eigener Zeitraum (2021-01-01...), damit die
        # Ueberlappungspruefung nichts mit den D-SCAN-Testjobs oben zu tun hat.
        # bw_hz >= span_hz clampt das SpectrumBinWriter-Fenster auf den vollen
        # Frame (siehe compute_window) - die geschriebenen Werte entsprechen
        # dann exakt den uebergebenen levels, ohne Fenster-Zuschnitt beachten
        # zu muessen.
        # ===================================================================

        def new_ifpan_job(label, start_iso, rotation_min=60):
            end_iso = (datetime.fromisoformat(start_iso) + timedelta(hours=1)).isoformat(
                timespec="seconds"
            )
            m = store.create_job(
                type="ifpan", label=label,
                params={"center_hz": 100_000_000, "span_hz": 25_000, "demod": "FM", "bw_hz": 40_000},
                start_iso=start_iso, end_iso=end_iso, decimation_s=None,
                rotation_min=rotation_min,
            )
            store.set_state(m["job_id"], "laeuft")
            return m

        # --- g) 3 Frames -> part01, Format v1, n_frames=3 ------------------
        manifest_g = new_ifpan_job("IFPAN Test A", "2021-01-01T00:00:00+01:00")
        rec_g = IfpanJobRecorder(store, manifest_g)
        for i in range(3):
            levels = [i * 10 + j for j in range(5)]
            rec_g.record(IfpanRecordFrame(center_hz=100_000_000, span_hz=25_000, bw_hz=40_000,
                                           demod="FM", seq=1000 + i, t=i * 1.0, levels=levels))
        stats_g = rec_g.close()
        assert stats_g["frames"] == 3, stats_g
        assert len(stats_g["files"]) == 1, stats_g["files"]
        part1_g = os.path.join(manifest_g["job_dir"], stats_g["files"][0])
        with SpectrumRecording(part1_g) as spec_g:
            assert spec_g.fmt == IFPAN_FORMAT_ID, spec_g.fmt
            assert spec_g.n_frames == 3, spec_g.n_frames
            seq0, t0, levels0 = spec_g.frame(0)
            assert seq0 == 1000, seq0
            assert abs(t0 - 0.0) < 1e-9, t0
            assert list(levels0) == [0, 1, 2, 3, 4], list(levels0)
            seq2, _t2, levels2 = spec_g.frame(2)
            assert seq2 == 1002, seq2
            assert list(levels2) == [20, 21, 22, 23, 24], list(levels2)
        print(f"OK  IfpanJobRecorder: {stats_g['frames']} Frames -> part01, "
              f"Format {IFPAN_FORMAT_ID!r}, Inhalte exakt")

        # --- h) Rotation ueber t -> zwei Dateien ---------------------------
        manifest_h = new_ifpan_job("IFPAN Test B", "2021-01-01T02:00:00+01:00", rotation_min=1)
        rec_h = IfpanJobRecorder(store, manifest_h)
        for i in range(8):
            rec_h.record(IfpanRecordFrame(center_hz=100_000_000, span_hz=25_000, bw_hz=40_000,
                                           demod="FM", seq=2000 + i, t=i * 10.0, levels=[i, i, i]))
        stats_h = rec_h.close()
        expected_files_h = [
            f"longrun_{manifest_h['job_id']}_part01.specbin",
            f"longrun_{manifest_h['job_id']}_part02.specbin",
        ]
        assert stats_h["files"] == expected_files_h, stats_h["files"]
        assert stats_h["frames"] == 8, stats_h
        for fname in stats_h["files"]:
            with open(os.path.join(manifest_h["job_dir"], fname + ".json"), encoding="utf-8") as f:
                side = json.load(f)
            assert side.get("running", False) is False, side
        manifest_h_final = store.get(manifest_h["job_id"])
        assert manifest_h_final["files"] == expected_files_h, manifest_h_final["files"]
        print(f"OK  IfpanJobRecorder Rotation: {stats_h['files']} (je finale Sidecar, Manifest aktuell)")

        # --- i) Geometriewechsel mitten im Lauf ----------------------------
        manifest_i = new_ifpan_job("IFPAN Test C", "2021-01-01T04:00:00+01:00")
        rec_i = IfpanJobRecorder(store, manifest_i)
        rec_i.record(IfpanRecordFrame(center_hz=100_000_000, span_hz=25_000, bw_hz=40_000,
                                       demod="FM", seq=1, t=0.0, levels=[1, 1, 1]))
        assert rec_i.failed is None
        rec_i.record(IfpanRecordFrame(center_hz=100_000_000, span_hz=25_000, bw_hz=40_000,
                                       demod="FM", seq=2, t=1.0, levels=[1, 1]))  # andere Bin-Zahl
        assert rec_i.failed_code == "geometry_changed", (rec_i.failed_code, rec_i.failed)
        rec_i.record(IfpanRecordFrame(center_hz=100_000_000, span_hz=25_000, bw_hz=40_000,
                                       demod="FM", seq=3, t=2.0, levels=[2, 2]))  # weiterhin No-Op
        stats_i = rec_i.close()
        assert len(stats_i["files"]) == 1, stats_i["files"]
        with open(os.path.join(manifest_i["job_dir"], stats_i["files"][0] + ".json"), encoding="utf-8") as f:
            side_i = json.load(f)
        assert side_i.get("running", False) is False, side_i
        print(f"OK  IfpanJobRecorder Geometriewechsel -> failed_code={rec_i.failed_code!r}, "
              f"failed={rec_i.failed!r}, Datei sauber geschlossen, kein Absturz")

        # --- j) Heartbeat bei grossem t-Sprung ------------------------------
        manifest_j = new_ifpan_job("IFPAN Test D", "2021-01-01T06:00:00+01:00", rotation_min=120)
        rec_j = IfpanJobRecorder(store, manifest_j)
        rec_j.record(IfpanRecordFrame(center_hz=100_000_000, span_hz=25_000, bw_hz=40_000,
                                       demod="FM", seq=1, t=0.0, levels=[1, 1]))
        rec_j.record(IfpanRecordFrame(center_hz=100_000_000, span_hz=25_000, bw_hz=40_000,
                                       demod="FM", seq=2, t=400.0, levels=[2, 2]))  # Sprung > 300 s

        manifest_j_mid = store.get(manifest_j["job_id"])
        assert manifest_j_mid["heartbeat_iso"] is not None, manifest_j_mid
        active_file_j = f"longrun_{manifest_j['job_id']}_part01.specbin"
        sidecar_path_j = os.path.join(manifest_j["job_dir"], active_file_j + ".json")
        with open(sidecar_path_j, encoding="utf-8") as f:
            side_j = json.load(f)
        assert side_j.get("running") is True, side_j
        print("OK  IfpanJobRecorder Heartbeat bei t-Sprung > 300s: heartbeat_iso gesetzt, "
              "aktive Sidecar running=true")

        rec_j.close()
        with open(sidecar_path_j, encoding="utf-8") as f:
            side_j_final = json.load(f)
        assert side_j_final.get("running", False) is False, side_j_final
        print("OK  IfpanJobRecorder nach close(): Sidecar running entfaellt")

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()
