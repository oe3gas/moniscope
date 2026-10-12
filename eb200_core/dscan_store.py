#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/dscan_store.py

Eigenstaendiger Recorder fuer die Long-Run-D-SCAN-Aufzeichnung (WS3,
Abschnitt 11.3: "Max-Hold-Dezimierung mit Trefferzaehler"). Anders als die
interaktive Session-DB (session_store.py, variable Geometrie, SQLite) ist
ein Long-Run mit EINGEFRORENEN Parametern unterwegs (kein Segmentwechsel) -
damit passt hier dasselbe Flatfile-Muster wie bei der IFPAN-Bin-Aufnahme
(spectrum_store.py), das dieses Modul als Vorbild fuer Aufbau und Stil
uebernimmt: feste Framegroesse, Sidecar-JSON, idempotentes close().

Ziel der Dezimierung: ein 12-24h-D-SCAN waere roh (1 Sweep/1-2 s, tausende
Bins) weder handhabbar noch auswertbar. Statt roher Sweeps wird deshalb pro
Bin/Intervall NUR das Max ueber k Sub-Sweeps gespeichert (kurze Bursts
bleiben sichtbar, anders als beim Mittelwert), PLUS ein Trefferzaehler
(uint8: in wie vielen der k Sub-Sweeps lag der Bin nahe am Intervall-Max) -
das gibt eine grobe Aktivitaetsdauer je Bin und Intervall zurueck, ohne die
volle Zeitaufloesung speichern zu muessen.

Frameformat (Little-Endian, wie spectrum_store.py):
  seq (uint32) + t (double) + n_bins x int16 (Max, Deci-dBuV)
  + n_bins x uint8 (Trefferzaehler je Bin)
  -> framesize = 12 + 3 * n_bins. Frame k liegt deterministisch bei
  k * framesize (O(1)-Seek, siehe spectrum_reader.py, das dieses Format
  als zweites Format neben eb200-ifpan-bins-v1 liest).

seq/t eines Frames sind seq/t des ERSTEN Sub-Sweeps im jeweiligen Intervall
(nicht etwa gemittelt) - das haelt die Zuordnung zu einem konkreten,
tatsaechlich gemessenen Sub-Sweep eindeutig.

Trefferschwelle: ein Bin zaehlt als "aktiv" in einem Sub-Sweep, wenn sein
Wert dort >= (Intervall-Max des Bins - THRESHOLD_DECI_DB) liegt (60
Deci-dB-Einheiten = 6,0 dB, siehe THRESHOLD_DECI_DB). Bei decimation_k == 1
degeneriert das Max-Hold zur reinen Identitaet (ein Sub-Sweep pro Intervall)
und der Trefferzaehler ist dann fuer jeden Bin konstant 1 - das ist
zulaessig (kein Sonderfall im Code) und lediglich der Grenzfall "keine
Dezimierung".

Enthaelt KEINEN Geraete-/Netzwerkzugriff - reine Byte-Verarbeitung (rohe
Sweep-Level-Liste rein, Binaerdatei + Sidecar-JSON raus), daher per
Selbsttest ohne angeschlossene Hardware pruefbar. Nur Standardbibliothek
(struct, json, os, datetime, array).
"""

from __future__ import annotations

import json
import os
import struct
from array import array
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Frameformat (siehe Moduldoku): Praefix seq+t wie spectrum_store.py, danach
# n_bins x int16 (Max) + n_bins x uint8 (Trefferzaehler), alles Little-Endian.
# ---------------------------------------------------------------------------

FORMAT_ID = "eb200-dscan-maxhold-v1"
_PREFIX_FMT = "<Id"                          # seq (uint32 LE) + t (double LE)
_PREFIX_LEN = struct.calcsize(_PREFIX_FMT)   # = 12

# Trefferschwelle: Intervall-Max minus 6,0 dB. Werte sind ROHE Deci-dBuV
# (Geraetewert x10), daher 60 Einheiten statt 6.
THRESHOLD_DECI_DB = 60


class DscanMaxholdWriter:
    """
    Schreibt fortlaufend Max-Hold-Intervalle eines Long-Run-D-SCANs als
    Fixed-Size-Binaerframes. Geometrie (Bereich/Schrittweite/Bin-Zahl) und
    Dezimierung (decimation_k) werden EINMAL im Konstruktor festgelegt -
    ein Long-Run laeuft mit eingefrorenen Parametern, im Unterschied zur
    interaktiven Session-DB (session_store.py), die Segmentwechsel kennt.

    add_sweep() puffert Sub-Sweeps, bis ein Intervall (decimation_k
    Sub-Sweeps) voll ist, und schreibt dann einen Frame. Ein am Ende der
    Aufnahme angefangenes, nicht vollstaendiges Intervall wird in close()
    verworfen (sweeps_dropped_partial) statt als unvollstaendiger/verzerrter
    Frame geschrieben zu werden.
    """

    def __init__(self, path: str, *, start_hz: int, stop_hz: int, step_hz,
                 n_bins: int, decimation_k: int, extra_meta: dict | None = None) -> None:
        if decimation_k < 1:
            raise ValueError(f"decimation_k must be at least 1, is {decimation_k}")
        if decimation_k > 255:
            # Der Trefferzaehler ist uint8 (0..255) - decimation_k ist die
            # maximal moegliche Zaehlerhoehe (alle Sub-Sweeps aktiv).
            raise ValueError(
                f"decimation_k must not exceed 255 (hit counter "
                f"is uint8), is {decimation_k}"
            )

        self.path = path
        self.start_hz = start_hz
        self.stop_hz = stop_hz
        self.step_hz = step_hz
        self.n_bins = n_bins
        self.decimation_k = decimation_k
        self.extra_meta = extra_meta or {}

        self.framesize = _PREFIX_LEN + 3 * n_bins
        # Rein informative Beschreibung des Frame-Layouts fuer die Sidecar -
        # wird NICHT direkt zum Packen/Entpacken verwendet (Max-Ebene und
        # Zaehler-Ebene werden getrennt behandelt, siehe _flush_interval()).
        self._frame_format_info = f"<Id{n_bins}h{n_bins}B"

        self._buffer: list[tuple[int, float, array]] = []  # (sweep_no, t, array('h'))
        self.frames_written = 0
        self.sweeps_seen = 0
        self.sweeps_dropped_partial = 0
        self._first_t: float | None = None
        self._last_t: float | None = None

        self.start_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._closed = False
        self._f = open(path, "wb")

    def add_sweep(self, sweep_no: int, t: float, levels) -> None:
        """
        Nimmt einen einzelnen Sub-Sweep entgegen (levels: Sequenz von n_bins
        rohen int16-Deci-dBuV-Werten). Abweichende Laenge -> ValueError: der
        Long-Run laeuft mit eingefrorener Geometrie, eine falsche Laenge ist
        ein Programmierfehler des Aufrufers, keine erwartbare Betriebslage
        (anders als spectrum_store.write_frame(), das Geometriewechsel bei
        LIVE-Bedienung defensiv nur zaehlt statt abzubrechen).

        Puffert den Sub-Sweep; sobald decimation_k Sub-Sweeps beisammen
        sind, wird das Intervall als EIN Frame geschrieben (siehe
        _flush_interval()) und der Puffer geleert. Nach close() ist der
        Aufruf ein No-Op (defensiv, Race beim Stoppen).
        """
        if self._closed:
            return
        if len(levels) != self.n_bins:
            raise ValueError(
                f"levels has {len(levels)} values, expected {self.n_bins} (n_bins)"
            )

        self.sweeps_seen += 1
        if self._first_t is None:
            self._first_t = t
        self._last_t = t

        self._buffer.append((sweep_no, t, array("h", levels)))
        if len(self._buffer) >= self.decimation_k:
            self._flush_interval()

    def _flush_interval(self) -> None:
        """Bildet aus dem vollen Puffer (decimation_k Sub-Sweeps) einen
        Frame: je Bin das Max ueber alle Sub-Sweeps, dazu je Bin die Anzahl
        der Sub-Sweeps, deren Wert >= (Max - THRESHOLD_DECI_DB) lag. seq/t
        des Frames sind seq/t des ERSTEN Sub-Sweeps im Intervall."""
        seq0, t0, _ = self._buffer[0]
        n = self.n_bins
        max_vals = [0] * n
        counts = bytearray(n)
        for j in range(n):
            col = [entry[2][j] for entry in self._buffer]
            m = max(col)
            max_vals[j] = m
            threshold = m - THRESHOLD_DECI_DB
            counts[j] = sum(1 for v in col if v >= threshold)

        packed = (
            struct.pack(_PREFIX_FMT, seq0 & 0xFFFFFFFF, t0)
            + array("h", max_vals).tobytes()
            + bytes(counts)
        )
        self._f.write(packed)
        self.frames_written += 1
        self._buffer = []

    def checkpoint(self) -> None:
        """
        Zwischenstand fuer den Job-Controller (WS3, Abschnitt 10.3: alle
        5 Minuten) - flusht die Binaerdatei (inkl. fsync, damit ein
        Stromausfall nicht nur im OS-Puffer haengende Daten verliert) und
        schreibt die Sidecar-JSON mit "running": true neu. So bleibt jede
        Aufnahme auch nach einem Absturz mitten im Lauf interpretierbar."""
        self._f.flush()
        os.fsync(self._f.fileno())
        sidecar = self._build_sidecar(running=True)
        with open(self.path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)

    def close(self) -> None:
        """
        Schliesst die Binaerdatei und schreibt die finale Sidecar-JSON.
        Ein am Ende noch angefangenes, nicht vollstaendiges Intervall wird
        VERWORFEN (in sweeps_dropped_partial gezaehlt) statt als verzerrter
        Frame geschrieben zu werden - ein Max-Hold ueber weniger als
        decimation_k Sub-Sweeps waere kein vergleichbarer Intervall-Wert.

        Idempotent (Vorbild session_store.py) - ein zweiter/dritter Aufruf
        ist ein No-Op, wichtig, da close() ueber mehrere unabhaengige Pfade
        erreichbar sein kann (Job-Ende, Stop-Klick, Abbruchpfad)."""
        if self._closed:
            return
        self._closed = True

        if self._buffer:
            self.sweeps_dropped_partial += len(self._buffer)
            self._buffer = []

        self._f.close()
        sidecar = self._build_sidecar(running=False)
        with open(self.path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)

    # -- intern ---------------------------------------------------------

    def _sub_frame_rate_hz(self) -> float | None:
        """Gemessene Sub-Sweep-Rate: sweeps_seen / (letztes t - erstes t).
        None, wenn weniger als 2 Sub-Sweeps gesehen wurden oder die
        Zeitspanne nicht positiv ist (z. B. identische Zeitstempel)."""
        if self.sweeps_seen < 2 or self._first_t is None or self._last_t is None:
            return None
        dt = self._last_t - self._first_t
        if dt <= 0:
            return None
        return self.sweeps_seen / dt

    def _build_sidecar(self, *, running: bool) -> dict:
        """Baut das Sidecar-Dict - gemeinsam fuer checkpoint() (running=True)
        und close() (running=False, Schluessel dann weggelassen). bin0_hz
        traegt bewusst denselben Schluessel wie im IFPAN-Sidecar
        (spectrum_store.py) - so kann spectrum_reader.py die Frequenzachse
        formatunabhaengig ueber bin0_hz/step_hz aufbauen."""
        stop_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sidecar = {
            "format": FORMAT_ID,
            "start_iso": self.start_iso,
            "stop_iso": stop_iso,
            "start_hz": self.start_hz,
            "stop_hz": self.stop_hz,
            "step_hz": self.step_hz,
            "n_bins": self.n_bins,
            "bin0_hz": self.start_hz,
            "decimation_k": self.decimation_k,
            "threshold_deci_db": THRESHOLD_DECI_DB,
            "sub_frame_rate_hz": self._sub_frame_rate_hz(),
            "byte_order": "little",
            "frame_format": self._frame_format_info,
            "framesize_bytes": self.framesize,
            "frames_written": self.frames_written,
            "sweeps_seen": self.sweeps_seen,
            "sweeps_dropped_partial": self.sweeps_dropped_partial,
            "raw_scale": "deci-dBuV (int16, Wert/10 = dBuV)",
            "counter_scale": "Treffer je Intervall; Dauer ~= Zaehler / sub_frame_rate_hz",
        }
        if running:
            sidecar["running"] = True
        if self.extra_meta:
            sidecar.update(self.extra_meta)
        return sidecar


# ---------------------------------------------------------------------------
# Selbsttest - "python dscan_store.py" direkt ausfuehren, ohne Geraet/Hardware
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        # --- a) + b) Max/Zaehler/seq/t ueber drei Intervalle -------------
        # n_bins=6, decimation_k=3. Drei identische Sub-Sweep-Muster (drei
        # Intervalle) demonstrieren je einen Testfall in den Bins 0..2:
        #   Bin 0 (A): Burst nur im 2. Sub-Sweep -> Max=500, Zaehler=1.
        #   Bin 1 (B): konstant hoch (100)       -> Max=100, Zaehler=3.
        #   Bin 2 (C): zwei von drei nahe am Max -> Max=50,  Zaehler=2.
        # Bins 3..5 sind konstant 0 (nicht gezielt geprueft, nur gueltige
        # Fuellwerte fuer die volle Bin-Breite).
        path = os.path.join(tmp_dir, "longrun_test.specbin")
        writer = DscanMaxholdWriter(
            path, start_hz=88_000_000, stop_hz=100_000_000, step_hz=3000,
            n_bins=6, decimation_k=3,
        )
        sub_sweeps = [
            [-300, 100, 50, 0, 0, 0],
            [500, 100, -100, 0, 0, 0],
            [-300, 100, 45, 0, 0, 0],
        ]
        sweep_no = 1000
        t = 100.0
        for _interval in range(3):
            for levels in sub_sweeps:
                writer.add_sweep(sweep_no, t, levels)
                sweep_no += 1
                t += 0.05
        assert writer.frames_written == 3, writer.frames_written
        assert writer.sweeps_seen == 9, writer.sweeps_seen
        assert writer.sweeps_dropped_partial == 0, writer.sweeps_dropped_partial
        print(f"OK  drei volle Intervalle geschrieben: "
              f"{writer.frames_written} Frames, {writer.sweeps_seen} Sub-Sweeps")

        writer.close()

        expected_max = [500, 100, 50, 0, 0, 0]
        expected_counts = [1, 3, 2, 3, 3, 3]
        with open(path, "rb") as f:
            for k in range(3):
                blob = f.read(writer.framesize)
                seq, ft = struct.unpack_from(_PREFIX_FMT, blob, 0)
                max_arr = array("h")
                max_arr.frombytes(blob[_PREFIX_LEN:_PREFIX_LEN + 2 * 6])
                counts = list(blob[_PREFIX_LEN + 2 * 6:_PREFIX_LEN + 3 * 6])
                assert list(max_arr) == expected_max, (k, list(max_arr))
                assert counts == expected_counts, (k, counts)
                assert seq == 1000 + k * 3, (k, seq)
                assert abs(ft - (100.0 + k * 3 * 0.05)) < 1e-9, (k, ft)
        print(f"OK  Max={expected_max} Zaehler={expected_counts} in allen 3 Frames exakt")
        print("OK  seq/t je Frame = seq/t des ersten Sub-Sweeps im Intervall")

        with open(path + ".json", encoding="utf-8") as f:
            side = json.load(f)
        assert side["format"] == FORMAT_ID
        assert side["frames_written"] == 3
        assert side["sweeps_seen"] == 9
        assert side["sweeps_dropped_partial"] == 0
        assert side["framesize_bytes"] == _PREFIX_LEN + 3 * 6 == writer.framesize
        assert side["bin0_hz"] == 88_000_000
        assert "running" not in side, side.get("running")
        print("OK  finale Sidecar plausibel (format/framesize/bin0_hz/sweeps)")

        # --- c) Partial-Intervall: 3*k+1 Sweeps -> 1 verworfener Sweep ---
        path2 = os.path.join(tmp_dir, "partial_test.specbin")
        writer2 = DscanMaxholdWriter(
            path2, start_hz=88_000_000, stop_hz=100_000_000, step_hz=3000,
            n_bins=4, decimation_k=3,
        )
        for i in range(3 * 3 + 1):   # 10 Sub-Sweeps = drei volle Intervalle + 1 Rest
            writer2.add_sweep(2000 + i, 200.0 + i * 0.1, [0, 0, 0, 0])
        writer2.close()
        assert writer2.frames_written == 3, writer2.frames_written
        assert writer2.sweeps_dropped_partial == 1, writer2.sweeps_dropped_partial
        print(f"OK  Partial-Intervall verworfen: frames_written="
              f"{writer2.frames_written}, sweeps_dropped_partial="
              f"{writer2.sweeps_dropped_partial}")

        # --- d) Geometrie-Check: falsche Laenge -> ValueError -------------
        writer3 = DscanMaxholdWriter(
            os.path.join(tmp_dir, "geom_test.specbin"),
            start_hz=88_000_000, stop_hz=100_000_000, step_hz=3000,
            n_bins=4, decimation_k=2,
        )
        try:
            writer3.add_sweep(1, 0.0, [0, 0, 0])   # 3 statt 4 Werte
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError bei falscher levels-Laenge erwartet")
        finally:
            writer3.close()
        print("OK  falsche levels-Laenge -> ValueError")

        # decimation_k > 255 muss schon im Konstruktor abgelehnt werden.
        try:
            DscanMaxholdWriter(
                os.path.join(tmp_dir, "bad_k.specbin"),
                start_hz=0, stop_hz=1, step_hz=1, n_bins=1, decimation_k=256,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError bei decimation_k > 255 erwartet")
        print("OK  decimation_k > 255 -> ValueError (Trefferzaehler ist uint8)")

        # --- e) checkpoint() vs. close(): "running" + sub_frame_rate_hz ---
        path4 = os.path.join(tmp_dir, "checkpoint_test.specbin")
        writer4 = DscanMaxholdWriter(
            path4, start_hz=88_000_000, stop_hz=100_000_000, step_hz=3000,
            n_bins=2, decimation_k=2,
        )
        for i in range(4):
            writer4.add_sweep(3000 + i, 300.0 + i * 0.5, [10, 20])
        writer4.checkpoint()
        with open(path4 + ".json", encoding="utf-8") as f:
            side_running = json.load(f)
        assert side_running.get("running") is True, side_running.get("running")
        assert side_running["frames_written"] == 2, side_running["frames_written"]
        rate_running = side_running["sub_frame_rate_hz"]
        assert rate_running is not None and rate_running > 0, rate_running
        print(f"OK  checkpoint(): Sidecar mit running=true, "
              f"sub_frame_rate_hz={rate_running:.3f} Hz")

        writer4.close()
        with open(path4 + ".json", encoding="utf-8") as f:
            side_final = json.load(f)
        assert side_final.get("running", False) is False, side_final.get("running")
        rate_final = side_final["sub_frame_rate_hz"]
        # 4 Sub-Sweeps ueber 1,5 s Zeitspanne (0.5 s Schritt) -> 4 / 1.5 Hz.
        assert abs(rate_final - (4 / 1.5)) < 1e-6, rate_final
        print(f"OK  close(): running entfaellt, sub_frame_rate_hz={rate_final:.3f} Hz plausibel")

        # --- f) close() zweimal -> kein Fehler (idempotent) ---------------
        writer4.close()
        print("OK  close() zweimal aufgerufen -> kein Fehler (idempotent)")

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()
