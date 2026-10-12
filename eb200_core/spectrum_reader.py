#!/usr/bin/env python3
"""
eb200_core/spectrum_reader.py

Rein LESENDER Gegenpart zu eb200_core/spectrum_store.py: liest eine
IFPAN-Bin-Aufnahme (.specbin + .specbin.json) zurueck und stellt sie als
zeitindizierte Serie (Zeit × Bin) bereit. Grundlage fuer das Auswerte-/
Wiedergabe-Tool ("Videorekorder": Frame fuer Frame vor/zurueck, ein fester
Bin ueber die Zeit, Spektrogramm-Uebersicht).

Das Format ist selbsttragend: die gesamte Geometrie (Mitte/Span/Bandbreite,
Bin-Achse, Framegroesse) steht in der Sidecar-JSON; die reine Binaerdatei
enthaelt nur gleich grosse Frames OHNE Dateikopf. Daher liegt Frame k
deterministisch bei k * framesize -> wahlfreier Zugriff (Seek) in O(1),
genau das, was das Scrubbing braucht.

Wichtige Konventionen (siehe spectrum_store.py):
  - Frameformat little-endian: seq (uint32) + t (double) + n_window × int16.
  - Die int16-Level sind ROHE Deci-dBµV; physikalischer Pegel = wert / 10.
    Diese /10-Umrechnung ist Sache DIESES Auswertepfads (Methoden mit
    dbuv=True); die Rohwerte bleiben unangetastet abrufbar (dbuv=False).
  - seq zaehlt auf dem Aufnahme-UdpPath ausschliesslich IFPAN-Pakete und ist
    16-bit (Ueberlauf bei 65536). Aufeinanderfolgende Frames sollten seq um
    genau 1 erhoehen; jeder groessere Sprung (modulo 65536) sind verlorene
    UDP-Pakete, KEINE Signalpause -> gaps().
  - Die Zeitachse kommt IMMER aus den echten t-Werten (nicht aequidistant
    annehmen) -> times().

Zweites Format (WS3, Abschnitt 11.3/11.7): FORMAT_ID_DSCAN
("eb200-dscan-maxhold-v1", siehe dscan_store.py) - dieselbe Grundidee
(feste Framegroesse, Sidecar-JSON, O(1)-Seek), aber Long-Run-D-SCAN statt
IFPAN: statt roher Level traegt jeder Frame ein Max-Hold-Intervall (Max je
Bin ueber decimation_k Sub-Sweeps) PLUS eine Trefferzaehler-Ebene je Bin
(nur ueber counts() zugaenglich, siehe dort). SpectrumRecording erkennt das
Format an der Sidecar automatisch (self.fmt) und stellt fuer beide dieselbe
frame()/freq_axis()/gaps()-API bereit; formatspezifische Details (Zaehler-
Ebene, seq-Schrittweite in gaps()) sind unten je Methode dokumentiert.

Nur Python-Standardbibliothek (json, struct, array). close() bzw. der
Context-Manager geben das Datei-Handle wieder frei.
"""

from __future__ import annotations

import json
import os
import struct
from array import array

FORMAT_ID = "eb200-ifpan-bins-v1"
FORMAT_ID_DSCAN = "eb200-dscan-maxhold-v1"    # siehe dscan_store.py, WS3 11.3

_PREFIX_FMT = "<Id"                        # seq (uint32 LE) + t (double LE)
_PREFIX_LEN = struct.calcsize(_PREFIX_FMT)   # = 12
_SEQ_MASK = 0xFFFF                          # Geraet zaehlt 16-bit (Annex F)


class SpectrumRecording:
    """
    Oeffnet eine Aufnahme (Pfad der .specbin-Datei; die Sidecar .specbin.json
    liegt daneben) und stellt wahlfreien Zugriff auf die Frames bereit.

    Nutzung:
        with SpectrumRecording("records/....specbin") as rec:
            seq, t, levels = rec.frame(0)           # roh (int16, array('h'))
            _, _, dbuv    = rec.frame(0, dbuv=True) # Pegel in dBµV (list[float])
            achse         = rec.freq_axis()         # Frequenz je Bin (Hz)
            j             = rec.bin_nearest(48_004_000)
            ts, lv        = rec.bin_series(j, dbuv=True)   # ein Bin ueber Zeit
            luecken       = rec.gaps()              # verlorene Pakete (aus seq)
    """

    def __init__(self, specbin_path: str) -> None:
        self.path = specbin_path
        sidecar_path = specbin_path + ".json"
        with open(sidecar_path, encoding="utf-8") as f:
            self.meta = json.load(f)

        fmt = self.meta.get("format")
        if fmt not in (FORMAT_ID, FORMAT_ID_DSCAN):
            raise ValueError(
                f"unexpected format {fmt!r}, expected {FORMAT_ID!r} or "
                f"{FORMAT_ID_DSCAN!r} (sidecar: {sidecar_path})"
            )
        self.fmt = fmt
        is_dscan = fmt == FORMAT_ID_DSCAN

        # n_window bleibt intern der einheitliche Name fuer "Bins je Frame" -
        # das dscan-Sidecar traegt denselben Wert unter dem Schluessel
        # "n_bins" statt "n_window" (siehe dscan_store.py).
        self.n_window = int(self.meta["n_bins"] if is_dscan else self.meta["n_window"])
        self.framesize = int(self.meta["framesize_bytes"])
        # Quer-Check: framesize MUSS zur Geometrie passen. v1: Praefix +
        # n_window*int16 (nur Pegel-Ebene). dscan: Praefix + n_window*int16
        # (Max-Ebene) + n_window*uint8 (Trefferzaehler-Ebene, siehe
        # dscan_store.py._flush_interval()). Schuetzt vor einem
        # inkonsistenten Sidecar, bevor irgendein Seek rechnet.
        expected = _PREFIX_LEN + (3 if is_dscan else 2) * self.n_window
        if self.framesize != expected:
            raise ValueError(
                f"framesize_bytes {self.framesize} does not match n_window "
                f"{self.n_window} (expected {expected})"
            )

        # Geometrie fuer die Frequenzachse (step_hz ist i. Allg. NICHT ganzzahlig).
        # bin0_hz/step_hz liefern BEIDE Formate unter denselben Schluesseln -
        # dscan_store.py setzt bin0_hz bewusst gleich start_hz (siehe dort).
        self.bin0_hz = float(self.meta["bin0_hz"])
        self.step_hz = float(self.meta["step_hz"])
        # center_hz/span_hz/bw_hz gibt es nur im IFPAN-Sidecar (ein festes
        # Fenster um eine Mittenfrequenz) - ein D-SCAN hat Start/Stop statt
        # Mitte/Span, daher fehlen diese Schluessel im dscan-Sidecar
        # (deshalb .get() statt Pflicht-Zugriff).
        self.center_hz = self.meta.get("center_hz")
        self.span_hz = self.meta.get("span_hz")
        self.bw_hz = self.meta.get("bw_hz")
        self.demod = self.meta.get("demod", "")
        if is_dscan:
            self.decimation_k = int(self.meta["decimation_k"])

        self._f = open(specbin_path, "rb")
        self._f.seek(0, os.SEEK_END)
        file_bytes = self._f.tell()

        # Frame-Zahl primaer aus der Dateigroesse bestimmen; die Sidecar-Angabe
        # frames_written ist der Sollwert. Ein Abbruch/Absturz beim Schreiben
        # (finally nicht erreicht) kann eine unvollstaendige Datei hinterlassen
        # -> defensiv den kleineren Wert nehmen und die Diskrepanz merken
        # (truncated), statt ueber das Dateiende hinaus zu lesen.
        n_from_file = file_bytes // self.framesize
        n_from_meta = int(self.meta.get("frames_written", n_from_file))
        self.n_frames = min(n_from_file, n_from_meta)
        self.truncated = (n_from_file != n_from_meta)

    # -- wahlfreier Zugriff -------------------------------------------------

    def _check_index(self, k: int) -> None:
        if not (0 <= k < self.n_frames):
            raise IndexError(f"frame {k} outside 0..{self.n_frames - 1}")

    def prefix(self, k: int):
        """(seq, t_unix) von Frame k - liest nur die 12-Byte-Kopfdaten und
        ueberspringt die Level (guenstig fuer reine Zeit-/seq-Achsen)."""
        self._check_index(k)
        self._f.seek(k * self.framesize)
        seq, t = struct.unpack(_PREFIX_FMT, self._f.read(_PREFIX_LEN))
        return seq, t

    def frame(self, k: int, dbuv: bool = False):
        """
        Frame k per O(1)-Seek -> (seq, t_unix, levels).
        levels ist eine array('h') aus Deci-dBµV-Rohwerten; mit dbuv=True
        stattdessen eine list[float] in dBµV (Rohwert / 10).
        """
        self._check_index(k)
        self._f.seek(k * self.framesize)
        blob = self._f.read(self.framesize)
        seq, t = struct.unpack_from(_PREFIX_FMT, blob, 0)
        levels = array("h")
        levels.frombytes(blob[_PREFIX_LEN:_PREFIX_LEN + 2 * self.n_window])
        if dbuv:
            return seq, t, [v / 10.0 for v in levels]
        return seq, t, levels

    def iter_frames(self, dbuv: bool = False):
        """Generator ueber alle Frames in Aufnahmereihenfolge -> (seq, t, levels).
        Speicherschonend fuer die Spektrogramm-/Wasserfall-Gesamtansicht: der
        Aufrufer baut die Matrix Zeile fuer Zeile auf, statt alles im RAM zu
        halten."""
        for k in range(self.n_frames):
            yield self.frame(k, dbuv=dbuv)

    def counts(self, k: int):
        """
        Trefferzaehler-Ebene von Frame k - NUR fuer das dscan-Maxhold-Format
        (siehe dscan_store.py, FORMAT_ID_DSCAN) verfuegbar. Liefert je Bin,
        in wie vielen der decimation_k Sub-Sweeps dieses Intervalls der Wert
        nahe am Intervall-Max lag (0..decimation_k, als list[int]).

        Wirft ValueError bei einer v1-IFPAN-Aufnahme - die hat keine
        Trefferzaehler-Ebene, dort ist frame() bereits die vollstaendige
        Nutzlast."""
        if self.fmt != FORMAT_ID_DSCAN:
            raise ValueError(
                f"counts() is only available for recordings in format {FORMAT_ID_DSCAN!r}, "
                f"this recording has format {self.fmt!r}"
            )
        self._check_index(k)
        self._f.seek(k * self.framesize)
        blob = self._f.read(self.framesize)
        off = _PREFIX_LEN + 2 * self.n_window
        return list(blob[off:off + self.n_window])

    # -- Achsen -------------------------------------------------------------

    def freq_axis(self):
        """Frequenz (Hz) je gespeichertem Bin: bin0_hz + j*step_hz. step_hz ist
        Fliesskomma -> die Achse ist i. Allg. nicht ganzzahlig."""
        return [self.bin0_hz + j * self.step_hz for j in range(self.n_window)]

    def freq_of_bin(self, j: int) -> float:
        """Frequenz (Hz) eines einzelnen Bins j."""
        return self.bin0_hz + j * self.step_hz

    def bin_nearest(self, freq_hz: float) -> int:
        """Index des Bins, dessen Frequenz freq_hz am naechsten liegt (geklemmt
        auf 0..n_window-1). Fuer die Ein-Bin-ueber-die-Zeit-Ansicht: eine feste
        Frequenz waehlen und den passenden Bin treffen."""
        j = round((freq_hz - self.bin0_hz) / self.step_hz)
        return max(0, min(self.n_window - 1, int(j)))

    def times(self):
        """Alle t_unix-Werte (echte Empfangszeiten) in Aufnahmereihenfolge."""
        return [self.prefix(k)[1] for k in range(self.n_frames)]

    def seqs(self):
        """Alle seq-Werte in Aufnahmereihenfolge (16-bit, kann ueberlaufen)."""
        return [self.prefix(k)[0] for k in range(self.n_frames)]

    # -- Ein-Bin-ueber-die-Zeit --------------------------------------------

    def bin_series(self, j: int, dbuv: bool = False):
        """
        Pegelverlauf EINES festen Bins j ueber die ganze Aufnahme.
        -> (times, levels): times = echte t_unix-Werte, levels = Deci-dBµV
        (bzw. dBµV bei dbuv=True). Liest je Frame nur bis einschliesslich des
        gesuchten Bins (ein Seek, ein Read pro Frame) - der Rest des Frames
        wird nicht angefasst.
        """
        if not (0 <= j < self.n_window):
            raise IndexError(f"bin {j} outside 0..{self.n_window - 1}")
        off = _PREFIX_LEN + 2 * j            # Byte-Offset dieses Bins im Frame
        need = off + 2                       # bis einschliesslich der 2 Bin-Bytes
        times = []
        levels = []
        for k in range(self.n_frames):
            self._f.seek(k * self.framesize)
            blob = self._f.read(need)
            t = struct.unpack_from(_PREFIX_FMT, blob, 0)[1]
            (raw,) = struct.unpack_from("<h", blob, off)
            times.append(t)
            levels.append(raw / 10.0 if dbuv else raw)
        return times, levels

    # -- Lueckenerkennung ---------------------------------------------------

    def gaps(self):
        """
        Sucht Luecken aus der seq-Folge - Bedeutung von seq und erwarteter
        Schrittweite haengen vom Format ab (siehe self.fmt):

          v1 (IFPAN): seq ist 16-Bit (Ueberlauf bei 65536, daher Differenz
          modulo _SEQ_MASK) und sollte zwischen Frames um genau 1 steigen -
          ein groesserer Sprung bedeutet verlorene UDP-Pakete.

          dscan (Max-Hold): seq ist die D-SCAN-Sweep-Nummer als uint32 OHNE
          Maske (siehe dscan_store.py) und sollte zwischen zwei Frames um
          genau decimation_k steigen (ein Frame = ein Intervall aus
          decimation_k Sub-Sweeps). Ein groesserer Sprung bedeutet fehlende
          SUB-SWEEPS zwischen zwei Intervallen (missing = Differenz -
          decimation_k). Fehlende Sub-Sweeps INNERHALB eines bereits
          geschriebenen Intervalls sind hier NICHT sichtbar - sie zeigen
          sich hoechstens an einem ungewoehnlich gedehnten t_before/t_after.

        Liefert eine Liste von dicts:
          at_frame  - Index des Frames NACH der Luecke
          prev_seq  - seq des vorherigen Frames
          seq       - seq dieses Frames
          missing   - vermutete Zahl verlorener Pakete/Sub-Sweeps
                      (Differenz - erwartete Schrittweite)
          t_before  - t_unix des vorherigen Frames
          t_after   - t_unix dieses Frames
        Leere Liste = lueckenlos.
        """
        is_dscan = self.fmt == FORMAT_ID_DSCAN
        expected_step = self.decimation_k if is_dscan else 1
        result = []
        prev_seq = None
        prev_t = None
        for k in range(self.n_frames):
            seq, t = self.prefix(k)
            if prev_seq is not None:
                delta = (seq - prev_seq) if is_dscan else (seq - prev_seq) & _SEQ_MASK
                if delta != expected_step:
                    result.append({
                        "at_frame": k,
                        "prev_seq": prev_seq,
                        "seq": seq,
                        "missing": delta - expected_step,
                        "t_before": prev_t,
                        "t_after": t,
                    })
            prev_seq = seq
            prev_t = t
        return result

    # -- Lebenszyklus -------------------------------------------------------

    def close(self) -> None:
        """Gibt das Datei-Handle frei. Idempotent (mehrfach aufrufbar)."""
        if self._f is not None:
            self._f.close()
            self._f = None

    def __enter__(self) -> "SpectrumRecording":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Selbsttest - "python spectrum_reader.py" direkt ausfuehren, ohne Geraet/Datei
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        specbin = os.path.join(tmp, "20260817_120000_48000kHz_USB.specbin")

        # --- kleine, VOLLSTAENDIG bekannte Aufnahme synthetisieren ---
        # Geometrie: span 10 kHz auf 11 vollen Bins -> step 1000 Hz. Fenster
        # (i_lo=3..i_hi=7) -> 5 gespeicherte Bins, bin0 = 48 MHz - 5 kHz + 3 kHz.
        n_window = 5
        step_hz = 1000.0
        bin0_hz = 48_000_000 - 5_000 + 3 * 1000          # = 47_998_000
        frame_fmt = f"<Id{n_window}h"
        framesize = struct.calcsize(frame_fmt)           # 12 + 10 = 22

        # 4 Frames. seq springt nach Frame 2 von 1002 auf 1005 (2 Pakete weg).
        # t bewusst NICHT aequidistant, um die "echte t-Achse" zu pruefen.
        seqs_in = [1000, 1001, 1002, 1005]
        times_in = [100.0, 100.5, 101.2, 101.9]
        # Level je Frame k, Bin j: k*100 + j*10 -> jeder Wert eindeutig.
        with open(specbin, "wb") as f:
            for k in range(4):
                levels = [k * 100 + j * 10 for j in range(n_window)]
                f.write(struct.pack(frame_fmt, seqs_in[k], times_in[k], *levels))

        sidecar = {
            "format": FORMAT_ID,
            "start_iso": "2026-08-17T12:00:00+00:00",
            "stop_iso": "2026-08-17T12:00:02+00:00",
            "center_hz": 48_000_000, "span_hz": 10_000, "bw_hz": 5_000,
            "demod": "USB", "n_full_bins": 11,
            "i_lo": 3, "i_hi": 7, "n_window": n_window,
            "bin0_hz": bin0_hz, "step_hz": step_hz,
            "byte_order": "little", "frame_format": frame_fmt,
            "framesize_bytes": framesize, "frames_written": 4, "n_skipped": 0,
            "raw_scale": "deci-dBuV (int16, Wert/10 = dBuV)",
        }
        with open(specbin + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)

        # --- 1) Oeffnen + Kopfdaten ---
        with SpectrumRecording(specbin) as rec:
            assert rec.n_frames == 4, rec.n_frames
            assert rec.n_window == 5, rec.n_window
            assert rec.framesize == 22, rec.framesize
            assert rec.step_hz == 1000.0, rec.step_hz
            assert rec.bin0_hz == 47_998_000.0, rec.bin0_hz
            assert rec.truncated is False
            print(f"OK  geoeffnet: {rec.n_frames} Frames, {rec.n_window} Bins, "
                  f"framesize {rec.framesize} B, demod {rec.demod}")

            # --- 2) O(1)-Seek: erster und letzter Frame roh ---
            seq0, t0, lv0 = rec.frame(0)
            assert (seq0, t0) == (1000, 100.0), (seq0, t0)
            assert list(lv0) == [0, 10, 20, 30, 40], list(lv0)
            seq3, t3, lv3 = rec.frame(3)
            assert (seq3, t3) == (1005, 101.9), (seq3, t3)
            assert list(lv3) == [300, 310, 320, 330, 340], list(lv3)
            print(f"OK  Seek Frame 0 und 3 exakt: {list(lv0)} / {list(lv3)}")

            # --- 3) dBµV-Umrechnung (Rohwert / 10) ---
            _, _, db0 = rec.frame(0, dbuv=True)
            assert db0 == [0.0, 1.0, 2.0, 3.0, 4.0], db0
            print(f"OK  dBµV-Umrechnung: {db0}")

            # --- 4) Frequenzachse und Bin-Zuordnung ---
            axis = rec.freq_axis()
            assert axis[0] == 47_998_000.0 and axis[4] == 48_002_000.0, axis
            assert rec.bin_nearest(48_000_050) == 2, rec.bin_nearest(48_000_050)
            assert rec.bin_nearest(0) == 0 and rec.bin_nearest(9e9) == 4
            print(f"OK  Frequenzachse {axis[0]:.0f}..{axis[-1]:.0f} Hz, "
                  f"bin_nearest ok")

            # --- 5) Ein-Bin-ueber-die-Zeit (Bin 2) ---
            ts, lv = rec.bin_series(2)
            assert ts == times_in, ts
            assert lv == [20, 120, 220, 320], lv
            _, lvd = rec.bin_series(2, dbuv=True)
            assert lvd == [2.0, 12.0, 22.0, 32.0], lvd
            print(f"OK  bin_series(2): {lv}  (dBµV {lvd})")

            # --- 6) echte Zeit-/seq-Achsen ---
            assert rec.times() == times_in, rec.times()
            assert rec.seqs() == seqs_in, rec.seqs()
            print(f"OK  times/seqs aus echten Werten: t={rec.times()}")

            # --- 7) Lueckenerkennung: genau eine Luecke, 2 Pakete weg ---
            g = rec.gaps()
            assert len(g) == 1, g
            assert g[0]["at_frame"] == 3 and g[0]["prev_seq"] == 1002 \
                and g[0]["seq"] == 1005 and g[0]["missing"] == 2, g[0]
            print(f"OK  Luecke erkannt: {g[0]['missing']} Pakete verloren "
                  f"vor Frame {g[0]['at_frame']}")

            # --- 8) Indexgrenzen wehren sich ---
            for bad in (rec.frame, lambda k: rec.prefix(k)):
                try:
                    bad(4)
                except IndexError:
                    pass
                else:
                    raise AssertionError("IndexError erwartet")
            print("OK  Frame-Index ausserhalb -> IndexError")

            # --- 9) counts() ist NUR fuer das dscan-Format zulaessig ---
            try:
                rec.counts(0)
            except ValueError:
                pass
            else:
                raise AssertionError("ValueError erwartet: counts() auf v1-Aufnahme")
            print("OK  counts() auf v1-Aufnahme -> ValueError")

        # --- NEU: zweites Format (dscan-Maxhold, siehe dscan_store.py) -----
        # Synthetische Aufnahme analog zum v1-Aufbau oben: 5 Bins,
        # decimation_k=4, 3 Frames; seq springt zwischen Frame 1 und 2 von
        # 104 auf 112 (4 Sub-Sweeps fehlen zwischen zwei Intervallen).
        dscan_path = os.path.join(tmp, "longrun_88000kHz.specbin")
        n_bins_dscan = 5
        decimation_k = 4
        dscan_step_hz = 3000.0
        dscan_bin0_hz = 88_000_000
        dscan_frame_fmt = f"<Id{n_bins_dscan}h{n_bins_dscan}B"
        dscan_framesize = _PREFIX_LEN + 3 * n_bins_dscan          # 12 + 15 = 27

        seqs_dscan = [100, 104, 112]
        times_dscan = [200.0, 200.4, 201.2]
        with open(dscan_path, "wb") as f:
            for k in range(3):
                max_vals = [k * 10 + j for j in range(n_bins_dscan)]
                counts = [1, 2, 3, 4, 1]
                f.write(struct.pack(_PREFIX_FMT, seqs_dscan[k], times_dscan[k]))
                f.write(array("h", max_vals).tobytes())
                f.write(bytes(counts))

        sidecar_dscan = {
            "format": FORMAT_ID_DSCAN,
            "start_iso": "2026-08-19T10:00:00+00:00",
            "stop_iso": "2026-08-19T10:00:02+00:00",
            "start_hz": 88_000_000, "stop_hz": 100_000_000, "step_hz": dscan_step_hz,
            "n_bins": n_bins_dscan,
            "bin0_hz": dscan_bin0_hz,
            "decimation_k": decimation_k,
            "threshold_deci_db": 60,
            "sub_frame_rate_hz": 20.0,
            "byte_order": "little", "frame_format": dscan_frame_fmt,
            "framesize_bytes": dscan_framesize,
            "frames_written": 3, "sweeps_seen": 12, "sweeps_dropped_partial": 0,
            "raw_scale": "deci-dBuV (int16, Wert/10 = dBuV)",
            "counter_scale": "Treffer je Intervall; Dauer ~= Zaehler / sub_frame_rate_hz",
        }
        with open(dscan_path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar_dscan, f, indent=2, ensure_ascii=False)

        with SpectrumRecording(dscan_path) as rec2:
            assert rec2.fmt == FORMAT_ID_DSCAN, rec2.fmt
            assert rec2.n_frames == 3, rec2.n_frames
            assert rec2.n_window == n_bins_dscan, rec2.n_window
            assert rec2.framesize == 27, rec2.framesize
            assert rec2.decimation_k == decimation_k, rec2.decimation_k
            print(f"OK  dscan geoeffnet: {rec2.n_frames} Frames, {rec2.n_window} Bins, "
                  f"framesize {rec2.framesize} B")

            seq0, t0, lv0 = rec2.frame(0)
            assert (seq0, t0) == (100, 200.0), (seq0, t0)
            assert list(lv0) == [0, 1, 2, 3, 4], list(lv0)
            c0 = rec2.counts(0)
            assert c0 == [1, 2, 3, 4, 1], c0
            print(f"OK  dscan frame()/counts(): Max={list(lv0)} Zaehler={c0}")

            axis2 = rec2.freq_axis()
            assert axis2[0] == 88_000_000.0 and axis2[1] == 88_003_000.0, axis2
            print(f"OK  dscan Frequenzachse ab {axis2[0]:.0f} Hz (bin0_hz/step_hz)")

            g2 = rec2.gaps()
            assert len(g2) == 1, g2
            assert g2[0]["at_frame"] == 2 and g2[0]["prev_seq"] == 104 \
                and g2[0]["seq"] == 112 and g2[0]["missing"] == 4, g2[0]
            print(f"OK  dscan Luecke erkannt: {g2[0]['missing']} Sub-Sweeps fehlen "
                  f"vor Frame {g2[0]['at_frame']}")

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()