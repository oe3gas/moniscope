#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/spectrum_store.py

Eigenständiger Recorder für den IFPAN-Bin-Verlauf innerhalb des BANDBREITEN-
FENSTERS (nicht des ganzen Spans). Schreibt pro IFPAN-Frame nur die Bins, die
in das aktuelle BW-Fenster um die Mittenfrequenz fallen, als ROHE int16-
Gerätewerte (Deci-dBµV, KEINE Division durch 10 - die 0,1-dBµV->dBµV-
Umrechnung bleibt Sache des Auswerte-/Anzeigepfads). Ziel ist eine zeit-
indizierte Serie (Zeit × Bin), die sich später wie ein Bandgerät vor/zurück
spulen lässt ("Videorekorder").

Enthält KEINEN Geräte-/Netzwerkzugriff - reine Byte-Verarbeitung (rohe Level-
Liste rein, Binärdatei + Sidecar-JSON raus) - und ist damit ohne angeschlossene
Hardware per Selbsttest prüfbar (siehe unten). Nur Standardbibliothek
(struct, json, os, datetime).

Design-Entscheidungen (mit Gerhard abgestimmt):
  - Flatfile + Sidecar-JSON (Muster wie audio_wav.py), KEIN SQLite: die
    Aufnahme betrifft genau EIN festes Frequenzfenster mit konstanter Geometrie,
    also ist eine selbsttragende Capture-Datei die einfachste und playback-
    freundlichste Form.
  - FESTE Framegröße: seq (uint32 LE) + t (double LE) + n_window × int16 (LE).
    Dadurch liegt Frame k deterministisch bei k * framesize - Springen, Vor-
    und Zurückspulen beim späteren Playback ist ein reiner O(1)-Offset, ohne
    Index, ohne Datenbank. Die Binärdatei enthält NUR Frames (kein Kopf); alle
    Metadaten stehen im Sidecar-JSON (framesize, n_window, Frequenzachse ...).
  - Little-Endian in der Datei (nicht die Big-Endian-Geräteordnung): der
    Konsument ist host-seitiges Python auf Intel, LE ist dort nativ und lässt
    sich mit array('h').frombytes() direkt ohne Byteswap einlesen.
  - Die geräteseitige sequence_number (Annex F, EB200Header) wird pro Frame
    mitgeschrieben, damit das spätere Playback UDP-VERLUSTE als solche erkennt
    (Sprung > 1) und nicht als Signalpause fehldeutet. Sie ist 16-Bit und läuft
    bei 65536 über - die Lückenerkennung muss modulo rechnen (nicht Sache dieses
    Writers, nur der Auswertung).

close() schreibt neben die Binärdatei eine Sidecar-JSON (gleicher Dateiname +
".json") mit Start-/Stopzeit, kompletter Fenster-Geometrie (Mitte, Span, BW,
i_lo/i_hi, bin0_hz, step_hz, n_window), Demodulation und dem Frameformat -
damit eine Aufnahme auch ohne den ursprünglichen Aufrufer-Kontext (und ohne
dieses Modul) interpretierbar bleibt. close() ist idempotent (mehrfach
aufrufbar, z. B. über verschiedene Abbruchpfade wie "Zurück", Verbindungsende
oder expliziten Stop-Klick).
"""

from __future__ import annotations

import json
import os
import struct
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Frameformat (siehe Moduldoku): seq (uint32) + t (double) + n_window × int16,
# alles Little-Endian. n_window steht erst zur Konstruktionszeit fest, deshalb
# wird das vollständige Format-/Größen-Feld dort gebildet.
# ---------------------------------------------------------------------------

FORMAT_ID = "eb200-ifpan-bins-v1"
_FRAME_PREFIX_FMT = "<Id"                       # seq (uint32 LE), t (double LE)
_LEN_FRAME_PREFIX = struct.calcsize(_FRAME_PREFIX_FMT)


def compute_window(center_hz: int, span_hz: int, n_full_bins: int, bw_hz: int):
    """
    Rechnet aus der IFPAN-Geometrie (Mitte, Span, Bin-Zahl des VOLLEN Frames)
    und der IF-Bandbreite den mittigen Bin-Ausschnitt aus, der ins BW-Fenster
    fällt. Liefert (i_lo, i_hi, n_window, step_hz, bin0_hz):

      - i_lo/i_hi : inklusiver Indexbereich in den vollen Frame (0..n-1)
      - n_window  : Anzahl gespeicherter Bins (= i_hi - i_lo + 1)
      - step_hz   : Bin-Abstand = span / (n-1)  (Fließkomma, i. Allg. nicht ganz)
      - bin0_hz   : Frequenz des ersten gespeicherten Bins (Index i_lo)

    Ist bw_hz >= span_hz, wird auf den ganzen Frame geclamped (i_lo=0,
    i_hi=n-1). Wirft ValueError bei entarteten Eingaben (n < 2, span <= 0).
    """
    if n_full_bins < 2:
        raise ValueError(f"n_full_bins must be at least 2, is {n_full_bins}")
    if span_hz <= 0:
        raise ValueError(f"span_hz must be greater than 0, is {span_hz}")

    step_hz = span_hz / (n_full_bins - 1)
    half = bw_hz / 2.0
    # Offset eines Bins vom Span-Anfang = i*step_hz; die Mitte liegt bei span/2.
    i_lo = max(0, round((span_hz / 2 - half) / step_hz))
    i_hi = min(n_full_bins - 1, round((span_hz / 2 + half) / step_hz))
    if i_hi < i_lo:            # kann bei absurd kleiner BW passieren -> mind. 1 Bin
        i_hi = i_lo
    n_window = i_hi - i_lo + 1
    start_hz = center_hz - span_hz / 2
    bin0_hz = start_hz + i_lo * step_hz
    return i_lo, i_hi, n_window, step_hz, bin0_hz


class SpectrumBinWriter:
    """
    Schreibt fortlaufend die BW-Fenster-Bins der IFPAN-Frames als Fixed-Size-
    Binärframes auf die Platte. Öffnet die Datei sofort im Konstruktor, schließt
    sie erst in close() (und schreibt dabei die Sidecar-JSON). Die Fenster-
    Geometrie wird EINMAL im Konstruktor aus dem ersten Frame festgelegt (Mitte/
    Span/Bin-Zahl); Frames mit abweichender Bin-Zahl werden defensiv verworfen
    (Geometriewechsel - sollte bei gesperrter Bedienung nicht vorkommen, siehe
    waterfall.py, wird aber gezählt).
    """

    def __init__(self, path: str, center_hz: int, span_hz: int,
                 n_full_bins: int, bw_hz: int, demod: str) -> None:
        i_lo, i_hi, n_window, step_hz, bin0_hz = compute_window(
            center_hz, span_hz, n_full_bins, bw_hz
        )

        self.path = path
        self.center_hz = center_hz
        self.span_hz = span_hz
        self.n_full_bins = n_full_bins
        self.bw_hz = bw_hz
        self.demod = demod

        self.i_lo = i_lo
        self.i_hi = i_hi
        self.n_window = n_window
        self.step_hz = step_hz
        self.bin0_hz = bin0_hz

        # Vollständiges Frameformat steht jetzt fest (n_window bekannt).
        self.frame_fmt = f"<Id{n_window}h"
        self.framesize = struct.calcsize(self.frame_fmt)

        self.frames_written = 0
        self.n_skipped = 0            # Frames mit abweichender Geometrie (Annex F)
        self.start_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._closed = False

        self._f = open(path, "wb")

    def write_frame(self, raw_levels_full, seq: int, t: float) -> None:
        """
        Nimmt die ROHEN int16-Level des VOLLEN IFPAN-Frames entgegen, schneidet
        das BW-Fenster heraus (i_lo..i_hi) und schreibt einen Fixed-Size-Frame.

        Frames, deren Bin-Zahl nicht zur beim Öffnen festgelegten Geometrie
        passt (Span/Bin-Zahl hat sich geändert), werden VERWORFEN statt einen
        Fehler zu werfen - eine Aufnahme soll wegen eines einzelnen abweichenden
        Pakets nicht abbrechen (bei gesperrter Bedienung sollte das ohnehin nie
        auftreten). Nach close() ist der Aufruf ein No-Op (defensiv, Race beim
        Stoppen).
        """
        if self._closed:
            return
        if len(raw_levels_full) != self.n_full_bins:
            self.n_skipped += 1
            return

        window = raw_levels_full[self.i_lo:self.i_hi + 1]
        # seq als uint32 maskieren (Gerät liefert 16-Bit, Headroom im Feld);
        # int16-Werte defensiv auf den gültigen Bereich klemmen, damit ein
        # Ausreißer struct.error nicht zum Abbruch der Aufnahme führt.
        packed = struct.pack(
            self.frame_fmt,
            seq & 0xFFFFFFFF,
            t,
            *(max(-32768, min(32767, int(v))) for v in window),
        )
        self._f.write(packed)
        self.frames_written += 1

    def _build_sidecar(self, *, running: bool) -> dict:
        """Baut das Sidecar-Dict - gemeinsam fuer checkpoint() (running=True)
        und close() (running=False, Schluessel dann weggelassen)."""
        stop_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sidecar = {
            "format": FORMAT_ID,
            "start_iso": self.start_iso,
            "stop_iso": stop_iso,
            "center_hz": self.center_hz,
            "span_hz": self.span_hz,
            "bw_hz": self.bw_hz,
            "demod": self.demod,
            "n_full_bins": self.n_full_bins,
            "i_lo": self.i_lo,
            "i_hi": self.i_hi,
            "n_window": self.n_window,
            "bin0_hz": self.bin0_hz,
            "step_hz": self.step_hz,
            "byte_order": "little",
            "frame_format": self.frame_fmt,
            "framesize_bytes": self.framesize,
            "frames_written": self.frames_written,
            "n_skipped": self.n_skipped,
            "raw_scale": "deci-dBuV (int16, Wert/10 = dBuV)",
        }
        if running:
            sidecar["running"] = True
        return sidecar

    def checkpoint(self) -> None:
        """
        Zwischenstand fuer den Job-Controller (WS3 10.3, alle 5 Minuten) -
        exakt nach dem Vorbild DscanMaxholdWriter.checkpoint()
        (eb200_core/dscan_store.py): flusht die Binaerdatei (inkl. fsync,
        damit ein Stromausfall nicht nur im OS-Puffer haengende Daten
        verliert) und schreibt die Sidecar-JSON mit "running": true neu,
        mit dem bisherigen Zwischenstand (frames_written/n_skipped). So
        bleibt auch eine IFPAN-Long-Run-Aufnahme (eb200_core/job_runner.py,
        IfpanJobRecorder) nach einem Absturz mitten im Lauf interpretierbar.
        close() bleibt UNVERAENDERT - finale Sidecar ohne "running".
        """
        self._f.flush()
        os.fsync(self._f.fileno())
        sidecar = self._build_sidecar(running=True)
        with open(self.path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)

    def close(self) -> None:
        """
        Schließt die Binärdatei und schreibt die Sidecar-JSON daneben.
        Idempotent - ein zweiter/dritter Aufruf ist ein No-Op (wichtig, da
        close() über mehrere unabhängige Pfade erreichbar ist, siehe
        waterfall.py: expliziter Stop-Klick, "Zurück"-Button, Loop-Ende).
        """
        if self._closed:
            return
        self._closed = True
        self._f.close()
        sidecar = self._build_sidecar(running=False)
        with open(self.path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Selbsttest - "python spectrum_store.py" direkt ausführen, ohne Gerät/Hardware
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import math
    import tempfile
    from array import array

    # --- 1) Fenster-Berechnung: Span 25 kHz, BW 15 kHz, 1001 Bins ---
    center = 48_003_700
    span = 25_000
    n_full = 1001
    bw = 15_000
    i_lo, i_hi, n_window, step_hz, bin0_hz = compute_window(center, span, n_full, bw)
    assert step_hz == 25.0, step_hz
    # 15 kHz / 25 Hz = 600 Schritte -> 601 Bins, mittig um Bin 500 (200..800)
    assert (i_lo, i_hi, n_window) == (200, 800, 601), (i_lo, i_hi, n_window)
    assert bin0_hz == (center - span / 2) + 200 * 25.0, bin0_hz
    print(f"OK  Fenster: Bins {i_lo}..{i_hi} ({n_window} Stück), "
          f"step={step_hz} Hz, bin0={bin0_hz:.1f} Hz")

    # --- 2) BW >= Span -> voller Frame (Clamp) ---
    c_lo, c_hi, c_n, _, _ = compute_window(center, span, n_full, bw_hz=40_000)
    assert (c_lo, c_hi, c_n) == (0, n_full - 1, n_full), (c_lo, c_hi, c_n)
    print(f"OK  BW>=Span geclamped auf vollen Frame: {c_n} Bins")

    # --- 3) Schreiben + Fixed-Size-Seek beim Zurücklesen ---
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "20260816_200635_48004kHz_USB.specbin")
        writer = SpectrumBinWriter(path, center, span, n_full, bw, demod="USB")

        n_frames = 40
        # Je Frame ein voller int16-Level-Vektor: Rauschsockel + wandernde
        # Gauß-Beule, damit sich Frames unterscheiden und der Seek prüfbar ist.
        frames_ref = []
        for k in range(n_frames):
            levels = []
            peak_bin = 400 + k  # Beule wandert von Bin 400 aufwärts
            for i in range(n_full):
                base = -300 + int(50 * math.sin(i / 30.0))         # ~ -30 dBµV Sockel
                bump = int(600 * math.exp(-((i - peak_bin) ** 2) / (2 * 8.0 ** 2)))
                levels.append(base + bump)
            frames_ref.append(levels)
            writer.write_frame(levels, seq=1000 + k, t=1_700_000_000.0 + k * 0.03)

        # Ein Frame mit falscher Bin-Zahl muss verworfen werden (Geometriewechsel).
        writer.write_frame([0] * (n_full - 5), seq=9999, t=1_700_000_099.0)
        assert writer.n_skipped == 1, writer.n_skipped

        # --- checkpoint(): Sidecar mit running=true, Datei bleibt offen ---
        writer.checkpoint()
        with open(path + ".json", encoding="utf-8") as f:
            side_running = json.load(f)
        assert side_running.get("running") is True, side_running
        assert side_running["frames_written"] == n_frames, side_running["frames_written"]
        print("OK  checkpoint(): Sidecar mit running=true, aktueller Zwischenstand")

        writer.close()
        writer.close()  # Idempotenz-Check: darf nicht knallen

        with open(path + ".json", encoding="utf-8") as f:
            side_final = json.load(f)
        assert "running" not in side_final, side_final
        print("OK  close(): Sidecar running entfaellt")

        # Sidecar prüfen
        with open(path + ".json", encoding="utf-8") as f:
            side = json.load(f)
        assert side["format"] == FORMAT_ID
        assert side["frames_written"] == n_frames, side["frames_written"]
        assert side["n_window"] == n_window
        assert side["n_skipped"] == 1
        framesize = side["framesize_bytes"]
        assert framesize == _LEN_FRAME_PREFIX + 2 * n_window, framesize
        print(f"OK  Sidecar plausibel: {n_frames} Frames, framesize={framesize} B, "
              f"n_window={side['n_window']}")

        # O(1)-Seek: Frame 25 gezielt herauslesen und mit der Referenz vergleichen
        k = 25
        with open(path, "rb") as f:
            f.seek(k * framesize)
            blob = f.read(framesize)
        seq, t = struct.unpack_from("<Id", blob, 0)
        got = array("h")
        got.frombytes(blob[_LEN_FRAME_PREFIX:])
        assert seq == 1000 + k, seq
        assert abs(t - (1_700_000_000.0 + k * 0.03)) < 1e-6, t
        expected_window = frames_ref[k][i_lo:i_hi + 1]
        assert list(got) == expected_window, "Fenster-Roundtrip fehlgeschlagen"
        # Spot-Check der physikalischen Umrechnung: max-Bin in dBµV
        max_dbuv = max(got) / 10.0
        print(f"OK  Seek Frame {k}: seq={seq}, {len(got)} Bins, "
              f"Spitze {max_dbuv:.1f} dBµV - Roundtrip exakt")

        # Dateigröße muss exakt frames_written * framesize sein (reine Frames)
        assert os.path.getsize(path) == n_frames * framesize, os.path.getsize(path)
        print("OK  Dateigröße = frames_written * framesize (reine Frames, kein Kopf)")

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()