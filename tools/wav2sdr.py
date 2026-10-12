#!/usr/bin/env python3
"""
tools/wav2sdr.py

Konvertiert eine vom IFPAN-Zoom aufgezeichnete Stereo-IQ-WAV (siehe
waterfall.py DeviceController._start_recording, DEM IQ -> Audio-Remote-
Mode 1 = 32 kHz/16 Bit/2 Kanaele; Kanal 0 = I, Kanal 1 = Q - Projektannahme,
Hardware-Validierung siehe PLAN.md T-901) in gaengige SDR-Werkzeugformate.
Reine Python-Standardbibliothek (wave, struct, json, argparse, os, shutil,
math, datetime, tempfile - dazu re/cmath fuer Dateinamen-Parsing bzw. FFT,
ebenfalls Standardbibliothek).

Frequenzquelle: Sidecar-JSON (<wav>.json, von eb200_core/audio_wav.py
geschrieben) - genutzte Felder freq_hz, sample_rate_hz, start_iso, is_iq.
Fehlt die Sidecar, wird die Frequenz aus dem Dateinamen geparst (Muster
"..._<n>kHz_...", wie von _start_recording vergeben); ist auch das nicht
moeglich, bricht das Tool mit einer klaren Fehlermeldung ab.

Zeitstempel (start_iso) fuer --hdsdr/--sigmf werden NUR aus der Sidecar
uebernommen (UTC, siehe eb200_core/audio_wav.py) - der Zeitstempel im
Dateinamen ist LOKALE Zeit ohne Zeitzoneninfo und wuerde bei einer
Umrechnung nach UTC einen falschen Versatz einbauen. Fehlt die Sidecar,
brechen --hdsdr/--sigmf deshalb mit klarer Meldung ab (--cs16/--cf32/
--analyze brauchen keinen Zeitstempel und funktionieren auch ohne Sidecar).

Exportformate (beliebig kombinierbar):
  --hdsdr  Kopie (frisch geschriebene WAV, respektiert --swap-iq) als
           HDSDR_<JJJJMMTT>_<HHMMSS>Z_<freq_kHz>kHz_RF.wav im Zielverzeichnis
  --cs16   interleaved int16 LE I/Q roh (<name>.cs16)
  --cf32   dito float32 LE, normiert auf [-1, 1) (<name>.cf32)
  --sigmf  <name>.sigmf-data (ci16_le) + <name>.sigmf-meta

--out gibt das Zielverzeichnis fuer ALLE Exporte vor (Default: neben der
Quelldatei). --swap-iq vertauscht I und Q bei allen Exporten UND bei
--analyze (Spiegelkorrektur, falls Teil B eine vertauschte Kanalzuordnung
ergibt). --analyze schaetzt die dominante Frequenz (mit Vorzeichen; positiv
= oberhalb der Mitte) und den Pegel des DC-Bins relativ zum Peak, ueber
eine eigene, numpy-freie FFT (Cooley-Tukey, Laenge = groesste Zweierpotenz
<= min(vorhandene Samples, 2 s)). --selftest erzeugt eine synthetische
Stereo-IQ-WAV und prueft alle Exportpfade sowie --analyze.
"""
from __future__ import annotations

import argparse
import cmath
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import wave
from datetime import datetime, timezone

FREQ_FROM_NAME_RE = re.compile(r"_(\d+)kHz_")


# ---------------------------------------------------------------------------
# Lesen (WAV + Sidecar)
# ---------------------------------------------------------------------------

def read_wav_iq(path: str):
    """Liest eine Stereo-16-Bit-WAV vollstaendig ein. Liefert
    (sample_rate_hz, samples) mit samples als flacher Liste int16-Werte
    [i0, q0, i1, q1, ...]. Wirft ValueError bei falschem Kanal-/Bitformat."""
    with wave.open(path, "rb") as w:
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        nframes = w.getnframes()
        if nchannels != 2:
            raise ValueError(f"Erwarte 2 Kanaele (Stereo I/Q), Datei hat {nchannels}")
        if sampwidth != 2:
            raise ValueError(f"Erwarte 16 Bit, Datei hat {sampwidth * 8} Bit")
        raw = w.readframes(nframes)
    n_samples = len(raw) // 2
    samples = list(struct.unpack(f"<{n_samples}h", raw[:n_samples * 2]))
    return framerate, samples


def read_sidecar(wav_path: str):
    sidecar_path = wav_path + ".json"
    if not os.path.exists(sidecar_path):
        return None
    with open(sidecar_path, encoding="utf-8") as f:
        return json.load(f)


def resolve_freq_hz(wav_path: str, sidecar) -> int:
    if sidecar and sidecar.get("freq_hz") is not None:
        return int(sidecar["freq_hz"])
    m = FREQ_FROM_NAME_RE.search(os.path.basename(wav_path))
    if m:
        return int(m.group(1)) * 1000
    raise ValueError(
        f"Frequenz weder in Sidecar ({os.path.basename(wav_path)}.json) noch "
        f"im Dateinamen (Muster '..._<n>kHz_...') gefunden"
    )


def require_start_iso(sidecar, flag_name: str) -> str:
    start_iso = sidecar.get("start_iso") if sidecar else None
    if not start_iso:
        raise SystemExit(
            f"FEHLER {flag_name}: Sidecar fehlt oder ohne start_iso - "
            f"Zeitstempel nicht ermittelbar (siehe Docstring: Dateiname "
            f"traegt nur lokale Zeit ohne Zeitzone)."
        )
    return start_iso


def swap_iq_samples(samples: list[int]) -> list[int]:
    swapped = list(samples)
    swapped[0::2], swapped[1::2] = samples[1::2], samples[0::2]
    return swapped


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def write_wav_stereo16(path: str, framerate: int, samples: list[int]) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def export_hdsdr(samples: list[int], framerate: int, freq_hz: int, start_iso: str, out_dir: str) -> str:
    dt = datetime.fromisoformat(start_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    freq_khz = round(freq_hz / 1000)
    fname = f"HDSDR_{dt:%Y%m%d}_{dt:%H%M%S}Z_{freq_khz}kHz_RF.wav"
    dest = os.path.join(out_dir, fname)
    write_wav_stereo16(dest, framerate, samples)
    return dest


def export_cs16(samples: list[int], out_path: str) -> None:
    with open(out_path, "wb") as f:
        f.write(struct.pack(f"<{len(samples)}h", *samples))


def export_cf32(samples: list[int], out_path: str) -> None:
    floats = [s / 32768.0 for s in samples]
    with open(out_path, "wb") as f:
        f.write(struct.pack(f"<{len(floats)}f", *floats))


def export_sigmf(samples: list[int], framerate: int, freq_hz: int, start_iso: str, base_path: str):
    data_path = base_path + ".sigmf-data"
    meta_path = base_path + ".sigmf-meta"
    with open(data_path, "wb") as f:
        f.write(struct.pack(f"<{len(samples)}h", *samples))
    meta = {
        "global": {
            "core:datatype": "ci16_le",
            "core:sample_rate": framerate,
            "core:version": "1.0.0",
        },
        "captures": [
            {"core:sample_start": 0, "core:frequency": freq_hz, "core:datetime": start_iso},
        ],
        "annotations": [],
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return data_path, meta_path


# ---------------------------------------------------------------------------
# Analyse (FFT ohne numpy)
# ---------------------------------------------------------------------------

def fft(x: list[complex]) -> list[complex]:
    """Iterative radix-2 Cooley-Tukey-FFT, Laenge MUSS Zweierpotenz sein."""
    n = len(x)
    if n & (n - 1) != 0:
        raise ValueError("FFT-Laenge muss eine Zweierpotenz sein")
    a = list(x)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        w = cmath.exp(-2j * math.pi / length)
        half = length // 2
        for start in range(0, n, length):
            wn = 1 + 0j
            for k in range(half):
                u = a[start + k]
                v = a[start + k + half] * wn
                a[start + k] = u + v
                a[start + k + half] = u - v
                wn *= w
        length *= 2
    return a


def analyze(samples: list[int], framerate: int, max_seconds: float = 2.0) -> dict:
    """Dominante Frequenz (mit Vorzeichen, positiv = oberhalb der Mitte) und
    DC-Bin-Pegel relativ zum Peak, ueber die groesste Zweierpotenz-Laenge
    <= min(vorhandene komplexe Samples, max_seconds)."""
    n_complex = len(samples) // 2
    max_n = int(framerate * max_seconds)
    limit = min(n_complex, max_n)
    n = 1
    while n * 2 <= limit:
        n *= 2
    if n < 2:
        raise ValueError("zu wenige Samples fuer eine Analyse (mind. 2 komplexe Samples noetig)")

    iq = [complex(samples[2 * i], samples[2 * i + 1]) for i in range(n)]
    spectrum = fft(iq)
    mags = [abs(v) for v in spectrum]
    peak_bin = max(range(n), key=lambda k: mags[k])
    signed_bin = peak_bin - n if peak_bin >= n // 2 else peak_bin
    bin_hz = framerate / n
    dominant_hz = signed_bin * bin_hz

    dc_mag = mags[0]
    peak_mag = mags[peak_bin]
    if peak_mag > 0 and dc_mag > 0:
        dc_rel_db = 20 * math.log10(dc_mag / peak_mag)
    else:
        dc_rel_db = float("-inf")

    return {
        "n_samples": n,
        "duration_s": n / framerate,
        "dominant_hz": dominant_hz,
        "dc_rel_db": dc_rel_db,
    }


# ---------------------------------------------------------------------------
# Selbsttest - "python tools/wav2sdr.py --selftest", ohne Hardware/Server
# ---------------------------------------------------------------------------

def _self_test() -> bool:
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        if cond:
            print(f"OK  {msg}")
        else:
            print(f"FAIL {msg}", file=sys.stderr)
            ok = False

    with tempfile.TemporaryDirectory() as tmp:
        framerate = 32000
        duration_s = 2.0
        tone_hz = 1000.0
        n = int(framerate * duration_s)
        amplitude = 16000
        samples: list[int] = []
        for i in range(n):
            phase = 2 * math.pi * tone_hz * i / framerate
            samples.append(int(round(amplitude * math.cos(phase))))  # I
            samples.append(int(round(amplitude * math.sin(phase))))  # Q

        wav_path = os.path.join(tmp, "20260101_120000_97500kHz_IQ.wav")
        write_wav_stereo16(wav_path, framerate, samples)
        start_iso = "2026-01-01T12:00:00+00:00"
        sidecar = {
            "start_iso": start_iso, "freq_hz": 97_500_000,
            "mode": "IQ", "channels": 2,
        }
        with open(wav_path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f)

        read_framerate, read_samples = read_wav_iq(wav_path)
        check(read_framerate == framerate, f"Samplerate gelesen ({read_framerate} Hz)")
        check(len(read_samples) == n * 2, f"Sample-Anzahl gelesen ({len(read_samples)} int16-Werte)")

        sidecar_read = read_sidecar(wav_path)
        freq_hz = resolve_freq_hz(wav_path, sidecar_read)
        check(freq_hz == 97_500_000, f"Frequenz aus Sidecar ({freq_hz} Hz)")

        freq_from_name = resolve_freq_hz(wav_path, None)
        check(freq_from_name == 97_500_000, f"Frequenz aus Dateinamen ohne Sidecar ({freq_from_name} Hz)")

        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir, exist_ok=True)
        base_path = os.path.join(out_dir, "test")

        hdsdr_path = export_hdsdr(read_samples, read_framerate, freq_hz, start_iso, out_dir)
        check(os.path.basename(hdsdr_path) == "HDSDR_20260101_120000Z_97500kHz_RF.wav",
              f"HDSDR-Dateiname korrekt ({os.path.basename(hdsdr_path)})")
        with wave.open(hdsdr_path, "rb") as wf:
            check(wf.getnframes() == n, "HDSDR-WAV Frame-Anzahl konsistent")

        cs16_path = base_path + ".cs16"
        export_cs16(read_samples, cs16_path)
        check(os.path.getsize(cs16_path) == len(read_samples) * 2,
              f"cs16-Dateigroesse konsistent ({os.path.getsize(cs16_path)} Byte)")

        cf32_path = base_path + ".cf32"
        export_cf32(read_samples, cf32_path)
        check(os.path.getsize(cf32_path) == len(read_samples) * 4,
              f"cf32-Dateigroesse konsistent ({os.path.getsize(cf32_path)} Byte)")

        data_path, meta_path = export_sigmf(read_samples, read_framerate, freq_hz, start_iso, base_path)
        check(os.path.getsize(data_path) == len(read_samples) * 2, "sigmf-data Dateigroesse konsistent")
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        check(meta["global"]["core:datatype"] == "ci16_le", "sigmf-meta core:datatype")
        check(meta["global"]["core:sample_rate"] == framerate, "sigmf-meta core:sample_rate")
        check(meta["global"]["core:version"] == "1.0.0", "sigmf-meta core:version")
        check(meta["captures"][0]["core:frequency"] == freq_hz, "sigmf-meta core:frequency")
        check(meta["captures"][0]["core:datetime"] == start_iso, "sigmf-meta core:datetime")

        result = analyze(read_samples, read_framerate)
        check(abs(result["dominant_hz"] - tone_hz) <= 2.0,
              f"--analyze erkennt +{tone_hz:.0f} Hz (gemessen {result['dominant_hz']:+.1f} Hz)")

        swapped = swap_iq_samples(read_samples)
        result_swapped = analyze(swapped, read_framerate)
        check(abs(result_swapped["dominant_hz"] - (-tone_hz)) <= 2.0,
              f"--swap-iq kehrt Vorzeichen um (gemessen {result_swapped['dominant_hz']:+.1f} Hz)")

    if ok:
        print("SELFTEST OK")
    else:
        print("SELFTEST FEHLGESCHLAGEN", file=sys.stderr)
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", nargs="?", help="Pfad zur Stereo-IQ-WAV (nicht bei --selftest)")
    parser.add_argument("--out", help="Zielverzeichnis fuer alle Exporte (Default: neben der Quelldatei)")
    parser.add_argument("--hdsdr", action="store_true", help="Kopie als HDSDR_..._RF.wav")
    parser.add_argument("--cs16", action="store_true", help="interleaved int16 LE I/Q roh (.cs16)")
    parser.add_argument("--cf32", action="store_true", help="interleaved float32 LE I/Q, normiert (.cf32)")
    parser.add_argument("--sigmf", action="store_true", help=".sigmf-data (ci16_le) + .sigmf-meta")
    parser.add_argument("--swap-iq", action="store_true", help="I und Q bei allen Exporten/--analyze vertauschen")
    parser.add_argument("--analyze", action="store_true", help="dominante Frequenz + DC-Bin-Pegel schaetzen")
    parser.add_argument("--selftest", action="store_true", help="Selbsttest ohne Hardware/Server ausfuehren")
    args = parser.parse_args()

    if args.selftest:
        return 0 if _self_test() else 1

    if not args.wav:
        parser.error("WAV-Datei erforderlich (ausser bei --selftest)")

    try:
        framerate, samples = read_wav_iq(args.wav)
    except (ValueError, wave.Error, FileNotFoundError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1

    if args.swap_iq:
        samples = swap_iq_samples(samples)

    sidecar = read_sidecar(args.wav)
    try:
        freq_hz = resolve_freq_hz(args.wav, sidecar)
    except ValueError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1

    out_dir = args.out or os.path.dirname(os.path.abspath(args.wav))
    os.makedirs(out_dir, exist_ok=True)
    base_path = os.path.join(out_dir, os.path.splitext(os.path.basename(args.wav))[0])

    did_something = False
    try:
        if args.hdsdr:
            start_iso = require_start_iso(sidecar, "--hdsdr")
            dest = export_hdsdr(samples, framerate, freq_hz, start_iso, out_dir)
            print(f"HDSDR-Kopie: {dest}")
            did_something = True

        if args.cs16:
            dest = base_path + ".cs16"
            export_cs16(samples, dest)
            print(f"cs16: {dest}")
            did_something = True

        if args.cf32:
            dest = base_path + ".cf32"
            export_cf32(samples, dest)
            print(f"cf32: {dest}")
            did_something = True

        if args.sigmf:
            start_iso = require_start_iso(sidecar, "--sigmf")
            data_path, meta_path = export_sigmf(samples, framerate, freq_hz, start_iso, base_path)
            print(f"SigMF: {data_path}, {meta_path}")
            did_something = True
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.analyze:
        try:
            result = analyze(samples, framerate)
        except ValueError as exc:
            print(f"FEHLER --analyze: {exc}", file=sys.stderr)
            return 1
        print(f"Analyse ueber {result['n_samples']} Samples ({result['duration_s']:.3f} s):")
        print(f"  dominante Frequenz: {result['dominant_hz']:+.1f} Hz (relativ zur Mitte)")
        print(f"  DC-Bin-Pegel relativ zum Peak: {result['dc_rel_db']:.1f} dB")
        did_something = True

    if not did_something:
        parser.error("Keine Aktion gewaehlt (--hdsdr/--cs16/--cf32/--sigmf/--analyze/--selftest)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
