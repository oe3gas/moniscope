#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/session_store.py

Passiver Rohdaten-Recorder für D-SCAN-Sessions (siehe docs/WS1, Abschnitt 3c
"Rohdaten-Session-Store & Offline-Auswertung"). Schreibt fertige D-SCAN-Sweeps
als ROHE int16-Gerätewerte (Deci-dBµV, KEINE Division durch 10) in eine eigene
SQLite-Datei je Session - reines Schreib-Modul: keine Analyse, keine
Visualisierung, kein Zugriff auf frequency_blocks/die „große" DB (siehe
eb200_core/db.py). Die 0,1-dBµV->dBµV-Umrechnung bleibt ausschließlich
Anzeigepfad-Sache (waterfall.py) und findet hier nicht statt.

Design-Entscheidungen:
  - record() wird auf dem UDP-Empfangspfad aufgerufen und darf NIEMALS
    blockieren: sie legt den Sweep nur in eine bounded Queue und kehrt sofort
    zurück. Die eigentliche DB-Arbeit (Segment-Verwaltung, INSERT, Commit)
    läuft in einem eigenen Writer-Thread.
  - Ist die Queue voll (Writer kommt nicht hinterher), wird der Sweep
    VERWORFEN statt zu blockieren - mit gedrosseltem Log (analog
    waterfall.SweepAccumulator._log_leftover), damit der laufende D-SCAN-
    Empfang niemals durch die Aufzeichnung ausgebremst wird.
  - Commits werden gebündelt (Zeilen- UND Zeit-Schwelle), damit nicht jeder
    einzelne Sweep einen fsync auslöst.
  - Segment = zusammenhängender Lauf mit konstantem Raster (Start/Stop/
    Schrittweite unverändert, siehe SweepAccumulator/params_changed in
    waterfall.py - dasselbe Ereignis, das dort die Parameter-Tafel/den
    "Banner" auslöst). Ändert sich das Raster, wird das laufende Segment
    abgeschlossen (es ist ohnehin laufend aktuell, siehe _handle_sweep) und
    ein neues begonnen.
  - Absichtlich eigenständig: record() nimmt ein DScanUpdate rein per
    Duck-Typing entgegen (.complete/.full_raw_levels/.start_hz/.stop_hz/
    .step_hz/.t) - kein Import aus waterfall.py, damit dieses Modul für sich
    genommen (und ohne Gerät) testbar bleibt und später auch von anderer
    Stelle (z. B. einem eigenständigen Recorder-Skript) verwendbar ist.
  - Sichtbarkeit/Betriebslog: record() selbst bleibt STILL (kein Logging im
    Hot Path). Der Writer-Thread meldet stattdessen einen gedrosselten
    Heartbeat (höchstens alle 15 s, nur bei tatsächlich neuen Sweeps seit dem
    letzten Heartbeat), der Hauptthread quittiert Start (Konstruktor) und
    Ende (close(), mit Summen) je einmalig - siehe _maybe_log_heartbeat().
  - Nur Python-Standardbibliothek: sqlite3, array, threading, queue, time, os.
"""

from __future__ import annotations

import os
import queue
import sqlite3
import sys
import threading
import time
from array import array
from typing import Optional

# Größenordnung wie waterfall.CLIENT_QUEUE_SIZE - bewusst als eigener,
# lokaler Wert gepflegt statt importiert, damit dieses Modul unabhängig von
# waterfall.py bleibt (siehe Moduldoku).
QUEUE_MAXSIZE = 20

_SENTINEL = object()  # signalisiert dem Writer-Thread das Ende (siehe close())

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    started_at REAL NOT NULL,
    source     TEXT,
    mode       TEXT NOT NULL DEFAULT 'DSCAN',
    note       TEXT
);
CREATE TABLE IF NOT EXISTS segment (
    id        INTEGER PRIMARY KEY,
    start_hz  INTEGER NOT NULL,
    stop_hz   INTEGER NOT NULL,
    step_hz   INTEGER NOT NULL,
    n_bins    INTEGER NOT NULL,
    t_first   REAL NOT NULL,
    t_last    REAL NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    speed     TEXT,
    mtime_s   REAL,
    -- T-715: Betriebsparameter des Segments. Alle vier stehen NICHT in den
    -- D-SCAN-Datagrammen, kommen vom Aufrufer (siehe record()) und sind
    -- nullable: aeltere Session-DBs haben die Spalten gar nicht, neuere
    -- koennen sie ungesetzt lassen. bw_zoom wird als 0/1 abgelegt.
    bw_hz         INTEGER,
    bw_zoom       INTEGER,
    gain_mode     TEXT,
    gain_mgc_dbuv INTEGER,
    CHECK (start_hz < stop_hz), CHECK (step_hz > 0)
);
CREATE TABLE IF NOT EXISTS sweep (
    id         INTEGER PRIMARY KEY,
    segment_id INTEGER NOT NULL REFERENCES segment(id),
    seq        INTEGER NOT NULL,
    t_unix     REAL NOT NULL,
    levels     BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sweep_segment ON sweep(segment_id, seq);
"""


class SessionStore:
    """
    Eine Instanz entspricht einer Session-Datei. path wird beim ersten Zugriff
    angelegt (Zielverzeichnis inklusive), das Schema idempotent erstellt und
    eine einzelne session-Zeile geschrieben. Danach läuft ein Writer-Thread,
    der über record() eingereichte Sweeps abarbeitet.
    """

    def __init__(self, path: str, source: str, mode: str = "DSCAN") -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        # check_same_thread=False: die Verbindung wird hier im Konstruktor
        # (aufrufender Thread) für Schema/session-Zeile genutzt und danach
        # AUSSCHLIESSLICH vom Writer-Thread (siehe _writer_loop) - beide
        # Zugriffe überlappen sich nie, da der Thread erst am Ende des
        # Konstruktors gestartet wird.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT INTO session (id, started_at, source, mode) VALUES (1, ?, ?, ?)",
            (time.time(), source, mode),
        )
        self._conn.commit()

        self._path = path
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._last_drop_log = 0.0  # Log-Throttling, siehe _drop()
        self._closed = False

        # Segment-Zustand - wird AUSSCHLIESSLICH im Writer-Thread gelesen/
        # geschrieben (siehe _handle_sweep), keine Locks nötig.
        self._cur_segment_id: Optional[int] = None
        self._cur_start_hz: Optional[int] = None
        self._cur_stop_hz: Optional[int] = None
        self._cur_step_hz: Optional[int] = None
        # Ebenfalls Segment-Zustand (Writer-Thread-Eigentum, siehe oben) -
        # speed/mtime_s stehen nicht in den Datagrammen, kommen vom Aufrufer
        # (siehe record()) und loesen bei Aenderung genauso eine neue
        # Segmentgrenze aus wie ein Rasterwechsel (siehe _handle_sweep).
        self._cur_speed: Optional[str] = None
        self._cur_mtime_s: Optional[float] = None
        # T-715: dieselbe Rolle wie _cur_speed/_cur_mtime_s (Writer-Thread-
        # Eigentum, Segmentgrenze bei Aenderung).
        self._cur_bw_hz: Optional[int] = None
        self._cur_bw_zoom: Optional[bool] = None
        self._cur_gain_mode: Optional[str] = None
        self._cur_gain_mgc_dbuv: Optional[int] = None
        self._cur_row_count = 0

        # Betriebslog-Zähler (siehe Moduldoku "Sichtbarkeit/Betriebslog"):
        # _n_sweeps_written/_n_segments sind Writer-Thread-Eigentum (nur dort
        # verändert), _n_dropped ist Hauptthread-Eigentum (nur in _drop()
        # verändert, dort wo record() aufgerufen wird) - kein gemeinsamer
        # Zugriff, daher keine Locks nötig. close() liest alle drei erst NACH
        # dem thread.join() (siehe dort), also garantiert konsistent.
        self._n_sweeps_written = 0
        self._n_segments = 0
        self._n_dropped = 0
        self._last_heartbeat_log = 0.0      # Log-Throttling für den Heartbeat, siehe _maybe_log_heartbeat
        self._last_heartbeat_n_sweeps = 0   # um "nichts Neues seit letztem Heartbeat" zu erkennen

        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()
        print(f"[recorder] Aufzeichnung aktiv -> {path}")

    def record(self, update, speed: Optional[str] = None,
               mtime_s: Optional[float] = None, bw_hz: Optional[int] = None,
               bw_zoom: Optional[bool] = None, gain_mode: Optional[str] = None,
               gain_mgc_dbuv: Optional[int] = None) -> None:
        """
        Nimmt ein DScanUpdate entgegen (Duck-Typing, siehe Moduldoku - nur
        .complete/.full_raw_levels/.start_hz/.stop_hz/.step_hz/.t werden
        gebraucht). Tut nichts bei unfertigem Sweep (complete=False), fehlenden
        Rohwerten, ODER wenn dieser Store bereits geschlossen wird/ist (siehe
        close() - das Flag wird dort VOR dem Leerlaufen der Queue gesetzt,
        damit ein knapp gleichzeitiger record()-Aufruf nie auf eine Queue
        trifft, die gerade abgebaut wird). Blockiert NIE - ist die Queue voll,
        wird der Sweep verworfen (siehe _drop). Loggt selbst NICHTS (Hot Path).

        speed/mtime_s kommen vom AUFRUFER (Controller, siehe waterfall.py) -
        sie stehen NICHT in den D-SCAN-UDP-Datagrammen und lassen sich daher
        nicht aus dem DScanUpdate ableiten. Beide werden unveraendert mit dem
        Sweep in die Queue gereiht und erst im Writer-Thread ausgewertet
        (siehe _handle_sweep) - dort entscheiden sie mit ueber die
        Segmentgrenze. None/None (Default) entspricht "unbekannt" bzw.
        DEFAULT-Messzeit, siehe Modulkonvention.

        bw_hz/bw_zoom/gain_mode/gain_mgc_dbuv (T-715) verhalten sich genauso:
        Aufrufer-Angaben, die nicht im Datagramm stehen, unveraendert in die
        Queue, Auswertung erst im Writer-Thread. Auch sie erzwingen bei
        Aenderung eine Segmentgrenze - ohne das wuerde ein Segment ueber einen
        BW- oder Verstaerkungswechsel hinweg spannen und die Auswertung wuerde
        fuer die halbe Aufzeichnung falsche Betriebsparameter anzeigen.
        None bedeutet durchgehend "unbekannt/nicht gesetzt".
        """
        if self._closed or not update.complete or update.full_raw_levels is None:
            return
        item = (update.start_hz, update.stop_hz, update.step_hz, update.t,
                update.full_raw_levels, speed, mtime_s,
                bw_hz, bw_zoom, gain_mode, gain_mgc_dbuv)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._drop()

    def status(self) -> dict:
        """
        Momentaufnahme der Betriebszähler, threadsicher lesbar (einfache
        Zahlen/Strings - kein Lock nötig, siehe Kommentar bei den Zählern in
        __init__). Für GET /record/status und die POST /record/*-Antworten.
        """
        return {
            "recording": not self._closed,
            "path": self._path,
            "n_sweeps": self._n_sweeps_written,
            "n_segments": self._n_segments,
            "n_dropped": self._n_dropped,
        }

    def _drop(self) -> None:
        self._n_dropped += 1  # Zähler unabhängig von der Log-Drosselung, siehe close()
        # Log-Throttling analog SweepAccumulator._log_leftover - höchstens
        # alle 10 Sekunden eine Meldung, damit die Konsole bei anhaltendem
        # Rückstand nicht zuspammt.
        now = time.monotonic()
        if now - self._last_drop_log > 10.0:
            print("[session_store] Aufzeichnungs-Queue voll - Sweep verworfen "
                  "(Writer kommt nicht hinterher, laufender D-SCAN-Empfang hat "
                  "Vorrang vor der Aufzeichnung)", file=sys.stderr)
            self._last_drop_log = now

    def close(self) -> None:
        """
        Lässt die Queue leerlaufen, committet ein letztes Mal, checkpointed
        das WAL-Journal zurück in die Hauptdatei und schließt die Verbindung.
        Idempotent - beliebig oft aufrufbar (z. B. über mehrere unabhängige
        Abbruchpfade wie Stream-Ende und finally, siehe waterfall.py).
        """
        if self._closed:
            return
        self._closed = True
        self._queue.put(_SENTINEL)   # darf hier blockieren - close() ist kein Hot-Path
        self._thread.join()
        self._conn.commit()
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.close()
        print(f"[recorder] beendet: {self._n_sweeps_written} Sweeps in {self._n_segments} Segmenten, "
              f"{self._n_dropped} verworfen -> {self._path}")

    # -- Writer-Thread --

    def _writer_loop(self) -> None:
        commit_batch = 50          # spätestens nach so vielen Sweeps committen
        commit_interval_s = 1.0    # oder spätestens nach so vielen Sekunden
        pending = 0
        last_commit = time.monotonic()

        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                # kein neuer Sweep, aber ein zeitbasierter Commit könnte
                # trotzdem fällig sein (z. B. bei sehr langsamem Sweep-Takt).
                if pending and (time.monotonic() - last_commit) >= commit_interval_s:
                    self._conn.commit()
                    pending = 0
                    last_commit = time.monotonic()
                continue

            if item is _SENTINEL:
                break

            (start_hz, stop_hz, step_hz, t, raw, speed, mtime_s,
             bw_hz, bw_zoom, gain_mode, gain_mgc_dbuv) = item
            self._handle_sweep(start_hz, stop_hz, step_hz, t, raw, speed, mtime_s,
                               bw_hz, bw_zoom, gain_mode, gain_mgc_dbuv)
            pending += 1
            self._maybe_log_heartbeat()
            now = time.monotonic()
            if pending >= commit_batch or (now - last_commit) >= commit_interval_s:
                self._conn.commit()
                pending = 0
                last_commit = now

        # letzte, noch nicht committete Sweeps vor dem Ende sichern (close()
        # committet zur Sicherheit selbst noch einmal danach).
        self._conn.commit()

    def _maybe_log_heartbeat(self) -> None:
        # Sichtbarkeit ohne den Hot Path (record()) zu belasten: gedrosselter
        # Heartbeat im Writer-Thread, exakt im Stil von
        # SweepAccumulator._log_leftover (time.monotonic-basiertes
        # Log-Throttling). Bleibt still, wenn seit dem letzten Heartbeat
        # keine neuen Sweeps dazugekommen sind.
        if self._n_sweeps_written == self._last_heartbeat_n_sweeps:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_log > 15.0:
            print(f"[recorder] {self._n_sweeps_written} Sweeps, {self._n_segments} Segmente geschrieben")
            self._last_heartbeat_log = now
            self._last_heartbeat_n_sweeps = self._n_sweeps_written

    def _handle_sweep(self, start_hz: int, stop_hz: int, step_hz: int,
                       t: float, raw: list[int], speed: Optional[str] = None,
                       mtime_s: Optional[float] = None, bw_hz: Optional[int] = None,
                       bw_zoom: Optional[bool] = None, gain_mode: Optional[str] = None,
                       gain_mgc_dbuv: Optional[int] = None) -> None:
        # Segmentgrenze bei Raster- ODER Speed-/MTIME-Wechsel: ein Speed-
        # bzw. MTIME-Wechsel aendert den Messcharakter fundamental (Hardware-
        # Sweep mit +-1/4-BW-Unschaerfe je Kanal vs. statische Kanalmessung
        # mit fester Messzeit, dazu jeweils eine voellig andere Sweep-Dauer)
        # - ohne eigene Segmentgrenze waere z. B. die Periodik-Analyse
        # (session_analyze.py, die von gleichmaessigen Sweep-Abstaenden
        # innerhalb eines Segments ausgeht) verfaelscht.
        segment_changed = (
            start_hz != self._cur_start_hz
            or stop_hz != self._cur_stop_hz
            or step_hz != self._cur_step_hz
            or speed != self._cur_speed
            or mtime_s != self._cur_mtime_s
            # T-715: BW/BW-Zoom/Verstaerkung aendern den Messcharakter ebenso
            # wie Speed/MTIME - siehe record()-Docstring.
            or bw_hz != self._cur_bw_hz
            or bw_zoom != self._cur_bw_zoom
            or gain_mode != self._cur_gain_mode
            or gain_mgc_dbuv != self._cur_gain_mgc_dbuv
        )
        if segment_changed:
            # das vorherige Segment ist bereits vollständig - t_last/
            # row_count werden bei jedem Sweep laufend aktuell gehalten
            # (siehe unten), hier nur auf das neue Raster/speed/mtime_s
            # umschalten.
            n_bins = len(raw)
            cursor = self._conn.execute(
                "INSERT INTO segment (start_hz, stop_hz, step_hz, n_bins, t_first, t_last, row_count, "
                "speed, mtime_s, bw_hz, bw_zoom, gain_mode, gain_mgc_dbuv) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
                (start_hz, stop_hz, step_hz, n_bins, t, t, speed, mtime_s,
                 bw_hz, None if bw_zoom is None else int(bw_zoom),
                 gain_mode, gain_mgc_dbuv),
            )
            self._cur_segment_id = cursor.lastrowid
            self._cur_start_hz, self._cur_stop_hz, self._cur_step_hz = start_hz, stop_hz, step_hz
            self._cur_speed, self._cur_mtime_s = speed, mtime_s
            self._cur_bw_hz, self._cur_bw_zoom = bw_hz, bw_zoom
            self._cur_gain_mode, self._cur_gain_mgc_dbuv = gain_mode, gain_mgc_dbuv
            self._cur_row_count = 0
            self._n_segments += 1

        # Werte sind rohe Geräte-int16 (Deci-dBµV), bewusst OHNE /10
        # gespeichert (siehe Moduldoku). Native Endianness - für die
        # Ein-Stations-DB unkritisch; für Portabilität zwischen Rechnern mit
        # unterschiedlicher Endianness wäre eine feste Byte-Reihenfolge plus
        # byteswap() nötig (siehe docs/WS1 Abschnitt 3c.3).
        levels_blob = array('h', raw).tobytes()

        # t_unix ist die Sweep-FERTIGSTELLUNGSZEIT (update.t aus dem
        # SweepAccumulator), NICHT der Zeitpunkt, an dem der Writer-Thread
        # diese Zeile tatsächlich schreibt.
        self._conn.execute(
            "INSERT INTO sweep (segment_id, seq, t_unix, levels) VALUES (?, ?, ?, ?)",
            (self._cur_segment_id, self._cur_row_count, t, levels_blob),
        )
        self._cur_row_count += 1
        self._n_sweeps_written += 1
        self._conn.execute(
            "UPDATE segment SET t_last = ?, row_count = ? WHERE id = ?",
            (t, self._cur_row_count, self._cur_segment_id),
        )


# ---------------------------------------------------------------------------
# Selbsttest - "python session_store.py" direkt ausführen, ohne Gerät/Hardware
# ---------------------------------------------------------------------------

class _FakeUpdate:
    """Leichtgewichtiger Stand-in für waterfall.DScanUpdate, nur für den
    Selbsttest - session_store.py bleibt eigenständig und importiert
    waterfall.py nicht (siehe record(), Duck-Typing)."""

    def __init__(self, complete: bool, start_hz: int, stop_hz: int, step_hz: int,
                 t: float, full_raw_levels: Optional[list[int]]) -> None:
        self.complete = complete
        self.start_hz = start_hz
        self.stop_hz = stop_hz
        self.step_hz = step_hz
        self.t = t
        self.full_raw_levels = full_raw_levels


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "session_test.db")
        store = SessionStore(db_path, source="selftest", mode="DSCAN")

        # Segment 1: drei fertige Sweeps mit gleichem Raster, speed=NORM
        # (mtime_s bleibt None - bei NORM ohnehin bedeutungslos).
        raw_a = [10, 20, 30, 40]
        raw_b = [11, 21, 31, 41]
        raw_c = [12, 22, 32, 42]
        for i, raw in enumerate((raw_a, raw_b, raw_c)):
            store.record(_FakeUpdate(True, 100_000_000, 100_003_000, 1000, time.time() + i, raw),
                         speed="NORM")

        # unfertiger Sweep (complete=False) - darf NICHT gespeichert werden.
        store.record(_FakeUpdate(False, 100_000_000, 100_003_000, 1000, time.time(), None))

        # Segment 2: UNVERAENDERTES Raster, aber speed=MTIM/mtime_s=0.25 -
        # muss trotzdem eine neue Segmentgrenze erzwingen (Kern dieser
        # Aufgabe: Speed/MTIME stehen nicht in den Datagrammen).
        raw_e = [1, 2, 3, 4]
        raw_f = [5, 6, 7, 8]
        for i, raw in enumerate((raw_e, raw_f)):
            store.record(_FakeUpdate(True, 100_000_000, 100_003_000, 1000, time.time() + 20 + i, raw),
                         speed="MTIM", mtime_s=0.25)

        # Segment 3: weiterhin Raster UND speed=MTIM unveraendert, aber
        # mtime_s wechselt 0.25 -> None (DEFAULT) - auch das allein muss
        # eine neue Segmentgrenze erzwingen.
        raw_g = [9, 10, 11, 12]
        raw_h = [13, 14, 15, 16]
        for i, raw in enumerate((raw_g, raw_h)):
            store.record(_FakeUpdate(True, 100_000_000, 100_003_000, 1000, time.time() + 30 + i, raw),
                         speed="MTIM", mtime_s=None)

        # Segment 4: Raster-Wechsel -> erzwingt (wie zuvor) ein weiteres
        # Segment, hier ohne explizite speed/mtime_s (Default None/None).
        raw_d = [50, 60, 70, 80, 90]
        store.record(_FakeUpdate(True, 200_000_000, 200_004_000, 1000, time.time() + 40, raw_d))

        # Segmente 5-8 (T-715): Raster, speed und mtime_s bleiben ab hier
        # UNVERAENDERT gegenueber Segment 4 - jedes Segment entsteht allein
        # durch die Aenderung genau EINES der vier neuen Felder.
        raw_e2 = [51, 61, 71, 81, 91]
        store.record(_FakeUpdate(True, 200_000_000, 200_004_000, 1000, time.time() + 50, raw_e2),
                     bw_hz=12500)                                    # Segment 5: bw_hz
        store.record(_FakeUpdate(True, 200_000_000, 200_004_000, 1000, time.time() + 51, raw_e2),
                     bw_hz=12500, bw_zoom=True)                      # Segment 6: bw_zoom
        store.record(_FakeUpdate(True, 200_000_000, 200_004_000, 1000, time.time() + 52, raw_e2),
                     bw_hz=12500, bw_zoom=True,
                     gain_mode="MGC", gain_mgc_dbuv=40)              # Segment 7: gain_mode
        store.record(_FakeUpdate(True, 200_000_000, 200_004_000, 1000, time.time() + 53, raw_e2),
                     bw_hz=12500, bw_zoom=True,
                     gain_mode="MGC", gain_mgc_dbuv=50)              # Segment 8: gain_mgc_dbuv

        store.close()
        store.close()  # Idempotenz-Check: darf nicht knallen

        conn = sqlite3.connect(db_path)
        segments = conn.execute(
            "SELECT id, start_hz, stop_hz, step_hz, n_bins, row_count, speed, mtime_s, "
            "bw_hz, bw_zoom, gain_mode, gain_mgc_dbuv "
            "FROM segment ORDER BY id"
        ).fetchall()
        assert len(segments) == 8, segments
        assert segments[0][5] == 3, segments[0]   # row_count Segment 1 (NORM)
        assert segments[1][5] == 2, segments[1]   # row_count Segment 2 (MTIM/0.25)
        assert segments[2][5] == 2, segments[2]   # row_count Segment 3 (MTIM/DEFAULT)
        assert segments[3][5] == 1, segments[3]   # row_count Segment 4 (Raster-Wechsel)
        print(f"OK  vier Segmente angelegt, row_count je Segment korrekt: {segments}")

        assert segments[0][6] == "NORM" and segments[0][7] is None, segments[0]
        assert segments[1][6] == "MTIM" and segments[1][7] == 0.25, segments[1]
        assert segments[2][6] == "MTIM" and segments[2][7] is None, segments[2]
        print("OK  Speed-/MTIME-Wechsel erzwingt Segmentgrenze trotz unveraendertem Raster "
              "(Segment 1->2); MTIME-Wechsel bei gleichbleibendem Speed erzwingt "
              "ebenfalls ein neues Segment (Segment 2->3)")

        # T-715: Spaltenindizes 8..11 = bw_hz, bw_zoom, gain_mode, gain_mgc_dbuv
        assert segments[3][8] is None and segments[3][10] is None, segments[3]
        assert segments[4][8] == 12500 and segments[4][9] is None, segments[4]
        assert segments[5][9] == 1, segments[5]
        assert segments[6][10] == "MGC" and segments[6][11] == 40, segments[6]
        assert segments[7][11] == 50, segments[7]
        print("OK  jede der vier Betriebsgroessen (bw_hz, bw_zoom, gain_mode, "
              "gain_mgc_dbuv) erzwingt fuer sich allein eine Segmentgrenze; "
              "bw_zoom liegt als 0/1 in der DB")

        rows_seg1 = conn.execute(
            "SELECT seq, levels FROM sweep WHERE segment_id = ? ORDER BY seq", (segments[0][0],)
        ).fetchall()
        assert [r[0] for r in rows_seg1] == [0, 1, 2]
        decoded_b = array('h')
        decoded_b.frombytes(rows_seg1[1][1])
        assert list(decoded_b) == raw_b, (list(decoded_b), raw_b)
        print(f"OK  levels-BLOB roundtrip exakt: {list(decoded_b)}")

        rows_seg4 = conn.execute(
            "SELECT levels FROM sweep WHERE segment_id = ?", (segments[3][0],)
        ).fetchall()
        decoded_d = array('h')
        decoded_d.frombytes(rows_seg4[0][0])
        assert list(decoded_d) == raw_d
        print(f"OK  viertes Segment (nach Raster-Wechsel) korrekt: {list(decoded_d)}")

        session_row = conn.execute("SELECT source, mode FROM session WHERE id = 1").fetchone()
        assert session_row == ("selftest", "DSCAN")
        print("OK  session-Zeile korrekt")

        conn.close()

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()
