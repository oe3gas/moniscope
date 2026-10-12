#!/usr/bin/env python3
"""
tools/iq_import.py

Konvertiert Fremd-IQ-Aufnahmen (HackRF/Airspy/SDR#/SigMF, Rohformate cs8/
cs16/cf32 oder Stereo-WAV) in das MoniScope-IQ-Format (16-Bit-Stereo-WAV,
Kanal 0 = I, Kanal 1 = Q, plus Sidecar-JSON), damit tools/eye_diagram.py
unveraendert darauf laufen kann.

Der IQ-Weg des EB200 ist auf 32 kHz Abtastrate und 15 kHz ZF-Bandbreite
begrenzt (Annex I). Breitbandige Verfahren (z. B. TETRA, 25 kHz/18 kBd) sind
darueber nicht erfassbar. HackRF/Airspy liefern IQ mit Megahertz-Raten;
dieses Werkzeug schneidet daraus per Mischer+Tiefpass+Dezimation einen
Ausschnitt heraus.

Reiner Dateikonverter, offline - es wird KEIN Geraet angesteuert.
tools/eye_diagram.py wird von diesem Werkzeug nicht veraendert.

numpy ist erlaubt und erwuenscht (wie in eye_diagram.py), keine weiteren
Fremdbibliotheken.

Speicherschonend: Eingangsdateien koennen mehrere Gigabyte gross sein, daher
wird ausschliesslich blockweise gelesen/verarbeitet (siehe DEFAULT_BLOCK_
SAMPLES) - die Datei liegt nie vollstaendig im Speicher. Mischerphase und
Tiefpass-Filterzustand werden dabei ueber Blockgrenzen hinweg fortgefuehrt
(siehe Klasse BlockPipeline) - ein Zuruecksetzen je Block waere falsch
(Phasenspruenge bzw. Stoerstellen an jeder Blockgrenze).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import wave
from datetime import datetime, timezone

import numpy as np

DEFAULT_BLOCK_SAMPLES = 1_000_000  # komplexe Samples je Verarbeitungsblock
KNOWN_FORMATS = ("cs8", "cs16", "cf32", "wav", "sigmf")

# Rohformate (V1): dtype des Kanals (I bzw. Q einzeln) und Normierungsteiler
# auf den Wertebereich +-1.0 (cs8 durch 128, cs16 durch 32768, cf32
# unveraendert - siehe Aufgabenstellung T-909).
RAW_FORMAT_SPECS = {
    "cs8": (np.dtype("<i1"), 128.0),
    "cs16": (np.dtype("<i2"), 32768.0),
    "cf32": (np.dtype("<f4"), 1.0),
}

# SigMF core:datatype -> unser Rohformat. Nur die drei oben unterstuetzten
# Kodierungen; alles andere ist ein sauberer Abbruch (siehe read_sigmf_meta).
SIGMF_DATATYPE_MAP = {
    "ci8": "cs8",
    "ci16": "cs16", "ci16_le": "cs16",
    "cf32": "cf32", "cf32_le": "cf32",
}

_EXTENSION_FORMAT_MAP = {
    ".cs8": "cs8", ".c8": "cs8", ".int8": "cs8",
    ".cs16": "cs16", ".s16": "cs16",
    ".cf32": "cf32", ".fc32": "cf32", ".cfile": "cf32",
    ".wav": "wav",
    ".sigmf-meta": "sigmf", ".sigmf-data": "sigmf",
}


def guess_format(path: str) -> str | None:
    """Automatik-Vorschlag anhand der Dateiendung. Massgeblich ist immer
    --format, falls angegeben - dies ist nur der Vorschlag bei dessen
    Fehlen."""
    lower = path.lower()
    for ext, fmt in _EXTENSION_FORMAT_MAP.items():
        if lower.endswith(ext):
            return fmt
    return None


# ---------------------------------------------------------------------------
# Blockweises Lesen (V1: lesen, nach komplexem float64 wandeln, normieren)
# ---------------------------------------------------------------------------

def iter_raw_blocks(path: str, fmt: str, block_samples: int):
    """Liest eine rohe IQ-Datei (cs8/cs16/cf32) blockweise, liefert normierte
    komplexe float64-Arrays."""
    dtype, scale = RAW_FORMAT_SPECS[fmt]
    bytes_per_complex = dtype.itemsize * 2
    block_bytes = block_samples * bytes_per_complex
    with open(path, "rb") as f:
        while True:
            raw = f.read(block_bytes)
            if not raw:
                return
            n_components = len(raw) // dtype.itemsize
            if n_components < 2:
                return
            arr = np.frombuffer(raw, dtype=dtype, count=n_components).astype(np.float64)
            n_complex = len(arr) // 2
            iq = arr[: n_complex * 2].reshape(-1, 2)
            yield (iq[:, 0] + 1j * iq[:, 1]) / scale


def iter_wav_blocks(path: str, block_samples: int):
    """Liest eine Stereo-16-Bit-WAV (Format 'wav': SDR#/SDRuno/HDSDR)
    blockweise, liefert normierte komplexe float64-Arrays."""
    with wave.open(path, "rb") as w:
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        if nchannels != 2:
            raise ValueError(f"Erwarte Stereo-WAV (I/Q), Datei hat {nchannels} Kanal/Kanaele")
        if sampwidth != 2:
            raise ValueError(f"Erwarte 16 Bit, Datei hat {sampwidth * 8} Bit")
        while True:
            raw = w.readframes(block_samples)
            if not raw:
                return
            raw_i16 = np.frombuffer(raw, dtype="<i2")
            n_complex = len(raw_i16) // 2
            if n_complex == 0:
                return
            iq = raw_i16[: n_complex * 2].reshape(-1, 2).astype(np.float64)
            yield (iq[:, 0] + 1j * iq[:, 1]) / 32768.0


def read_sigmf_meta(meta_path: str) -> dict:
    """Liest eine SigMF-Metadatei (.sigmf-meta, JSON). Liefert sample_rate_hz,
    freq_hz (aus der ersten capture, falls vorhanden), raw_format (unser
    cs8/cs16/cf32-Aequivalent zu core:datatype) und datetime_iso (aus
    core:datetime der ersten capture, falls vorhanden)."""
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    glob = meta.get("global", {})
    captures = meta.get("captures") or []
    sample_rate = glob.get("core:sample_rate")
    if sample_rate is None:
        raise ValueError(f"SigMF-Metadatei ohne core:sample_rate: {meta_path}")
    datatype = glob.get("core:datatype")
    raw_fmt = SIGMF_DATATYPE_MAP.get(datatype)
    if raw_fmt is None:
        raise ValueError(f"SigMF core:datatype '{datatype}' wird nicht unterstuetzt "
                          f"(unterstuetzt: {sorted(SIGMF_DATATYPE_MAP)})")
    freq_hz = captures[0].get("core:frequency") if captures else None
    datetime_iso = captures[0].get("core:datetime") if captures else None
    return {
        "sample_rate_hz": float(sample_rate),
        "freq_hz": float(freq_hz) if freq_hz is not None else None,
        "raw_format": raw_fmt,
        "datetime_iso": datetime_iso,
    }


def sigmf_data_path_for(meta_path: str) -> str:
    if meta_path.endswith(".sigmf-meta"):
        return meta_path[: -len(".sigmf-meta")] + ".sigmf-data"
    return os.path.splitext(meta_path)[0] + ".sigmf-data"


# ---------------------------------------------------------------------------
# Tiefpass-Entwurf (V3)
# ---------------------------------------------------------------------------

def design_lowpass_taps(in_rate: float, out_rate: float, decimate: int) -> np.ndarray:
    """FIR-Tiefpass vor der Dezimation (Aliasing-Schutz, V3). Grenzfrequenz
    0.4 * out_rate / 2, Fensterlaenge 8*decimate+1 (immer ungerade),
    Hamming-Fenster, Koeffizienten auf Summe 1 normiert."""
    cutoff_hz = 0.4 * out_rate / 2.0
    n_taps = 8 * decimate + 1
    m = (n_taps - 1) / 2.0
    n = np.arange(n_taps, dtype=np.float64) - m
    fc_norm = cutoff_hz / in_rate
    h = np.sinc(2.0 * fc_norm * n)
    h *= np.hamming(n_taps)
    total = float(np.sum(h))
    if total == 0.0:
        raise ValueError("Tiefpass-Koeffizienten summieren sich zu 0 - unplausible Parameter")
    return (h / total).astype(np.float64)


# ---------------------------------------------------------------------------
# Blockweise Verarbeitung: Mischer (V2) + Tiefpass/Dezimation (V3)
# ---------------------------------------------------------------------------

class BlockPipeline:
    """Fuehrt Mischer (V2) und Tiefpass+Dezimation (V3) blockweise mit ueber
    Blockgrenzen hinweg fortgefuehrtem Zustand aus.

    Mischerphase (V2, FEHLERANFAELLIGSTE STELLE): _mixer_phase beginnt bei
    jedem process()-Aufruf NICHT wieder bei t=0, sondern wird am Ende jedes
    Blocks weitergefuehrt (Akkumulation modulo 2*pi, klassische NCO-Bauart).
    Ein Zuruecksetzen je Block wuerde an jeder Blockgrenze einen
    Phasensprung erzeugen und die Konstellation zerstoeren.

    Filterzustand (V3, ZWEITE FEHLERANFAELLIGE STELLE): _filter_state haelt
    die letzten len(taps)-1 (bereits gemischten) Samples des VORHERIGEN
    Blocks (Overlap-Save). Jeder neue Block wird vor der Faltung um dieses
    Overlap verlaengert, sonst fehlen dem FIR an jeder Blockgrenze die
    Werte der vorangehenden Samples und es entstehen Stoerstellen.

    Die Dezimationsphase wird ueber einen fortlaufenden Eingangs-Sample-
    zaehler (_n_processed) bestimmt statt ueber einen Rest je Block, damit
    beliebige Blockgroessen exakt dieselbe Teilfolge auswaehlen wie eine
    Verarbeitung in einem einzigen Block (siehe --selftest S3). Da Mischer
    und Faltung bei unterschiedlicher Blockeinteilung in anderer Reihenfolge
    aufsummiert werden, ist das Ergebnis nur bis auf Gleitkomma-Rundung
    identisch - siehe die Toleranz "unter 2 LSB" in S3.
    """

    def __init__(self, in_rate: float, freq_shift_hz: float, decimate: int, taps: np.ndarray):
        self.in_rate = in_rate
        self.freq_shift_hz = freq_shift_hz
        self.decimate = decimate
        self.taps = taps
        self._mixer_phase = 0.0
        self._filter_state = np.zeros(len(taps) - 1, dtype=np.complex128)
        self._n_processed = 0

    def process(self, block: np.ndarray) -> np.ndarray:
        n = len(block)
        if n == 0:
            return np.zeros(0, dtype=np.complex128)

        if self.freq_shift_hz != 0.0:
            t = np.arange(n, dtype=np.float64) / self.in_rate
            mixer = np.exp(1j * (2.0 * math.pi * self.freq_shift_hz * t + self._mixer_phase))
            block = block * mixer
            phase_step = 2.0 * math.pi * self.freq_shift_hz * n / self.in_rate
            self._mixer_phase = (self._mixer_phase + phase_step) % (2.0 * math.pi)

        n_state = len(self.taps) - 1
        extended = np.concatenate([self._filter_state, block]) if n_state else block
        filtered = np.convolve(extended, self.taps, mode="valid")
        if n_state:
            self._filter_state = extended[-n_state:]

        offset = (-self._n_processed) % self.decimate
        decimated = filtered[offset::self.decimate]
        self._n_processed += n
        return decimated


def _prescan_peak(open_blocks, in_rate: float, freq_shift_hz: float, decimate: int,
                   taps: np.ndarray, out_rate: float, scan_seconds: float) -> float:
    """Vorabmessung des Spitzenwerts (V5) ueber ca. scan_seconds Sekunden
    Ausgangsdauer statt eines vollen zweiten Durchlaufs durch womoeglich
    gigabytegrosse Eingangsdateien. Eigene BlockPipeline-Instanz, damit der
    fortgefuehrte Zustand des eigentlichen Schreibdurchlaufs unberuehrt
    bleibt."""
    target = max(1, int(math.ceil(scan_seconds * out_rate)))
    pipeline = BlockPipeline(in_rate, freq_shift_hz, decimate, taps)
    peak = 0.0
    collected = 0
    for block in open_blocks():
        out = pipeline.process(block)
        if len(out):
            block_peak = float(np.max(np.abs(out)))
            if block_peak > peak:
                peak = block_peak
            collected += len(out)
        if collected >= target:
            break
    return peak


# ---------------------------------------------------------------------------
# Konvertierung
# ---------------------------------------------------------------------------

def convert(args, block_samples: int = DEFAULT_BLOCK_SAMPLES, skip_lowpass: bool = False) -> int:
    """Fuehrt die Konvertierung nach den Kopfdaten in args aus. args ist ein
    argparse.Namespace mit den Feldern input, format, in_rate, in_freq,
    tune, decimate, min_out_rate, scan_seconds, out, info (siehe main()).
    block_samples/skip_lowpass sind interne Testparameter fuer --selftest
    S3 (Blockgrenzen) bzw. S5 (Aliasing-Nachweis ohne Tiefpass) und werden
    von der CLI nicht gesetzt. Wirft FileNotFoundError/ValueError bei
    Eingabefehlern (sauberer Abbruch, siehe main())."""
    if not os.path.exists(args.input):
        raise FileNotFoundError(args.input)

    fmt = args.format or guess_format(args.input)
    if fmt is None:
        raise ValueError(f"Format nicht erkennbar, bitte --format angeben ({', '.join(KNOWN_FORMATS)})")
    if args.format is None:
        print(f"Format automatisch erkannt: {fmt} (an der Dateiendung)")

    sigmf_data_path = None
    sigmf_raw_fmt = None
    in_freq = args.in_freq
    datetime_iso = None

    if fmt == "wav":
        with wave.open(args.input, "rb") as w:
            nchannels = w.getnchannels()
            sampwidth = w.getsampwidth()
            in_rate = float(w.getframerate())
            total_samples = w.getnframes()
        if nchannels != 2:
            raise ValueError(f"Erwarte Stereo-WAV (I/Q), Datei hat {nchannels} Kanal/Kanaele")
        if sampwidth != 2:
            raise ValueError(f"Erwarte 16 Bit, Datei hat {sampwidth * 8} Bit")
    elif fmt == "sigmf":
        meta_path = args.input if args.input.endswith(".sigmf-meta") \
            else os.path.splitext(args.input)[0] + ".sigmf-meta"
        if not os.path.exists(meta_path):
            raise FileNotFoundError(meta_path)
        sigmf = read_sigmf_meta(meta_path)
        sigmf_data_path = sigmf_data_path_for(meta_path)
        if not os.path.exists(sigmf_data_path):
            raise FileNotFoundError(sigmf_data_path)
        sigmf_raw_fmt = sigmf["raw_format"]
        in_rate = sigmf["sample_rate_hz"]
        if in_freq is None:
            in_freq = sigmf["freq_hz"]
        datetime_iso = sigmf["datetime_iso"]
        bytes_per_complex = RAW_FORMAT_SPECS[sigmf_raw_fmt][0].itemsize * 2
        total_samples = os.path.getsize(sigmf_data_path) // bytes_per_complex
    else:  # cs8 / cs16 / cf32
        if args.in_rate is None:
            raise ValueError("--in-rate ist bei rohen Formaten (cs8/cs16/cf32) zwingend")
        in_rate = args.in_rate
        bytes_per_complex = RAW_FORMAT_SPECS[fmt][0].itemsize * 2
        total_samples = os.path.getsize(args.input) // bytes_per_complex

    if args.decimate is not None:
        if args.decimate < 1:
            raise ValueError("--decimate muss >= 1 sein")
        decimate = args.decimate
    else:
        decimate = max(1, int(in_rate // args.min_out_rate))
    out_rate = in_rate / decimate
    print(f"Dezimationsfaktor: {decimate} (in_rate={in_rate:.1f} Hz -> out_rate={out_rate:.2f} Hz)")

    if args.info:
        duration_s = total_samples / in_rate if in_rate else 0.0
        print(f"Format: {fmt}")
        print(f"Eingangsrate: {in_rate:.1f} Hz")
        if in_freq is not None:
            print(f"Eingangsfrequenz: {in_freq / 1e6:.6f} MHz")
        print(f"Geschaetzte Eingangs-Samples: {total_samples}")
        print(f"Dauer: {duration_s:.2f} s")
        print(f"Vorschlag --decimate: {decimate} (out_rate {out_rate:.2f} Hz)")
        return 0

    if args.tune is not None and in_freq is None:
        raise ValueError("--tune erfordert --in-freq (oder eine SigMF-Metadatei mit core:frequency)")

    freq_shift_hz = 0.0
    out_freq_hz = in_freq
    if args.tune is not None:
        # V2: delta so gewaehlt, dass das gewuenschte Signal (bei tune)
        # danach in der Mitte liegt - siehe BlockPipeline-Docstring.
        freq_shift_hz = in_freq - args.tune
        out_freq_hz = args.tune

    taps = design_lowpass_taps(float(in_rate), out_rate, decimate)
    eff_taps = np.array([1.0], dtype=np.float64) if skip_lowpass else taps

    def open_blocks():
        if fmt == "wav":
            return iter_wav_blocks(args.input, block_samples)
        if fmt == "sigmf":
            return iter_raw_blocks(sigmf_data_path, sigmf_raw_fmt, block_samples)
        return iter_raw_blocks(args.input, fmt, block_samples)

    peak = _prescan_peak(open_blocks, in_rate, freq_shift_hz, decimate, eff_taps, out_rate,
                          args.scan_seconds)
    scale = (0.8 * 32767.0) / peak if peak > 0 else 1.0
    print(f"Gemessener Spitzenwert (Vorabmessung {args.scan_seconds:.1f} s): {peak:.4f}")
    print(f"Skalierungsfaktor: {scale:.4f}")

    out_path = args.out or (os.path.splitext(args.input)[0] + "_iq.wav")

    pipeline = BlockPipeline(in_rate, freq_shift_hz, decimate, eff_taps)
    clipped = 0
    total_written = 0
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(max(1, int(round(out_rate))))
        for block in open_blocks():
            out = pipeline.process(block)
            if len(out) == 0:
                continue
            scaled = out * scale
            i_r = np.round(scaled.real)
            q_r = np.round(scaled.imag)
            clipped += int(np.sum((np.abs(i_r) > 32767) | (np.abs(q_r) > 32767)))
            i16 = np.clip(i_r, -32768, 32767).astype(np.int16)
            q16 = np.clip(q_r, -32768, 32767).astype(np.int16)
            interleaved = np.empty(2 * len(out), dtype=np.int16)
            interleaved[0::2] = i16
            interleaved[1::2] = q16
            w.writeframes(interleaved.tobytes())
            total_written += len(out)

    duration_out_s = total_written / out_rate if out_rate else 0.0
    clip_pct = 100.0 * clipped / total_written if total_written else 0.0
    print(f"Samples geschrieben: {total_written} ({duration_out_s:.2f} s)")
    print(f"Geclippte Samples: {clipped} ({clip_pct:.4f} %)")
    if clip_pct > 0.01:
        print(f"WARNUNG: {clip_pct:.4f} % der Samples geclippt (Grenze 0.01 %)", file=sys.stderr)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base_iso = datetime_iso or now_iso
    sidecar = {
        "start_iso": base_iso,
        "stop_iso": base_iso,
        "freq_hz": out_freq_hz,
        "mode": "IQ",
        "bw_hz": out_rate / 2.0,
        "audio_remote_mode": None,
        "sample_rate_hz": max(1, int(round(out_rate))),
        "channels": 2,
        "sampwidth_bytes": 2,
        "frames_written": total_written,
        "duration_s": round(duration_out_s, 3),
        "is_iq": True,
        "running": False,
        # Herkunftsgruppe, damit die Datei nicht mit einer echten
        # EB200-Aufnahme verwechselt werden kann (siehe Aufgabenstellung
        # T-909, Abschnitt SIDECAR).
        "source": {
            "tool": "iq_import",
            "input_format": fmt,
            "input_rate_hz": in_rate,
            "input_freq_hz": in_freq,
            "decimate": decimate,
            "tune_hz": args.tune,
            "scale_factor": scale,
            "clipped_samples": clipped,
        },
    }
    with open(out_path + ".json", "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, ensure_ascii=False)
    print(f"WAV geschrieben: {out_path}")
    print(f"Sidecar geschrieben: {out_path}.json")
    return 0


# ---------------------------------------------------------------------------
# Selbsttest - "python tools/iq_import.py --selftest", ohne Geraet/Eingabedatei
# ---------------------------------------------------------------------------

def _write_raw_fixture(path: str, fmt: str, z: np.ndarray) -> None:
    """Schreibt eine synthetische komplexe Folge z als rohe cs8/cs16/cf32-
    Datei - Umkehrung von iter_raw_blocks/RAW_FORMAT_SPECS, nur fuer
    --selftest."""
    dtype, scale = RAW_FORMAT_SPECS[fmt]
    if fmt == "cf32":
        i = z.real.astype(dtype)
        q = z.imag.astype(dtype)
    else:
        info = np.iinfo(dtype)
        i = np.clip(np.round(z.real * scale), info.min, info.max).astype(dtype)
        q = np.clip(np.round(z.imag * scale), info.min, info.max).astype(dtype)
    interleaved = np.empty(2 * len(z), dtype=dtype)
    interleaved[0::2] = i
    interleaved[1::2] = q
    with open(path, "wb") as f:
        f.write(interleaved.tobytes())


def _synth_fsk_baseband(fs: float, baud: float, dev_hz: float, dur_s: float, rng,
                         amplitude: float = 0.7) -> np.ndarray:
    """Synthetisches 2-FSK-Basisbandsignal (Mitte bei 0 Hz), nur fuer
    --selftest."""
    n = int(fs * dur_s)
    sps = fs / baud
    n_symbols = int(math.ceil(n / sps)) + 2
    sym = rng.integers(0, 2, n_symbols)
    freq_seq = np.where(sym == 0, -dev_hz, dev_hz).astype(np.float64)
    idx = np.clip((np.arange(n) / sps).astype(int), 0, n_symbols - 1)
    inst_freq = freq_seq[idx]
    phase = np.cumsum(2.0 * math.pi * inst_freq / fs)
    return amplitude * np.exp(1j * phase)


def _read_wav_int16_iq(path: str):
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype="<i2")
    return a[0::2], a[1::2]


def _self_test() -> bool:
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        status = "OK " if cond else "FEHLER"
        print(f"{status} {msg}")
        if not cond:
            ok = False

    def ns(**kwargs):
        base = dict(input=None, format=None, in_rate=None, in_freq=None, tune=None,
                    decimate=None, min_out_rate=32000.0, scan_seconds=5.0, out=None,
                    info=False, selftest=False)
        base.update(kwargs)
        return argparse.Namespace(**base)

    with tempfile.TemporaryDirectory() as tmp:
        # S1: cs8-Roundtrip - 2-FSK, 1200 Bd, +-4500 Hz Hub, erzeugt bei
        # 2 MHz, als cs8 geschrieben, mit --decimate 62 importiert
        # (out_rate ~32258 Hz). Pruefung ueber tools/eye_diagram.py per
        # Python-Import (kein Subprozess).
        fs1 = 2_000_000.0
        baud1 = 1200.0
        dev1 = 4500.0
        decimate1 = 62
        rng1 = np.random.default_rng(9001)
        sig1 = _synth_fsk_baseband(fs1, baud1, dev1, 1.0, rng1)
        cs8_path = os.path.join(tmp, "s1.cs8")
        _write_raw_fixture(cs8_path, "cs8", sig1)
        out1 = os.path.join(tmp, "s1_iq.wav")
        try:
            rc1 = convert(ns(input=cs8_path, format="cs8", in_rate=fs1, decimate=decimate1,
                             scan_seconds=1.0, out=out1))
            check(rc1 == 0, "S1: convert() liefert 0")
            check(os.path.exists(out1), "S1: Ausgabedatei existiert")
            check(os.path.exists(out1 + ".json"), "S1: Sidecar existiert")
            with open(out1 + ".json", encoding="utf-8") as f:
                sc1 = json.load(f)
            check(sc1["is_iq"] is True and sc1["running"] is False and sc1["mode"] == "IQ",
                  "S1: Sidecar-Grundfelder korrekt (is_iq/running/mode)")
            expected_rate1 = int(round(fs1 / decimate1))
            check(sc1["sample_rate_hz"] == expected_rate1,
                  f"S1: sample_rate_hz korrekt ({sc1['sample_rate_hz']} == {expected_rate1})")
            check(sc1["source"]["tool"] == "iq_import" and sc1["source"]["input_format"] == "cs8",
                  "S1: Herkunftsgruppe 'source' korrekt")

            import eye_diagram as ed  # lokaler Import, nur fuer den Selbsttest
            r1 = ed.analyze_file(out1)
            print(f"  S1: dev_hz={r1['dev_hz']:.1f} baud={r1['baud']:.2f} "
                  f"level_count={r1['level_count']}")
            check(abs(r1["dev_hz"] - dev1) / dev1 <= 0.05, f"S1: Hub innerhalb 5% ({r1['dev_hz']:.1f} Hz)")
            check(abs(r1["baud"] - baud1) / baud1 <= ed.BAUD_TOL_ZUFALLSFOLGE,
                  f"S1: Baud innerhalb {ed.BAUD_TOL_ZUFALLSFOLGE * 100:.0f}% ({r1['baud']:.2f})")
        except Exception as exc:
            check(False, f"S1: unerwarteter Fehler ({exc})")

        # S2: cs16 und cf32 - verkuerzte Pruefung, nur Formatlesen und
        # Sidecar-Korrektheit.
        fs2 = 100_000.0
        decimate2 = 4
        rng2 = np.random.default_rng(9002)
        for fmt2 in ("cs16", "cf32"):
            sig2 = _synth_fsk_baseband(fs2, 200.0, 500.0, 0.3, rng2)
            path2 = os.path.join(tmp, f"s2.{fmt2}")
            _write_raw_fixture(path2, fmt2, sig2)
            out2 = os.path.join(tmp, f"s2_{fmt2}_iq.wav")
            try:
                rc2 = convert(ns(input=path2, format=fmt2, in_rate=fs2, decimate=decimate2,
                                 scan_seconds=1.0, out=out2))
                check(rc2 == 0, f"S2 ({fmt2}): convert() liefert 0")
                check(os.path.exists(out2) and os.path.exists(out2 + ".json"),
                      f"S2 ({fmt2}): Ausgabedatei und Sidecar existieren")
                with open(out2 + ".json", encoding="utf-8") as f:
                    sc2 = json.load(f)
                check(sc2["channels"] == 2 and sc2["sampwidth_bytes"] == 2 and sc2["is_iq"] is True,
                      f"S2 ({fmt2}): Sidecar-Formatfelder korrekt")
                check(sc2["source"]["input_format"] == fmt2,
                      f"S2 ({fmt2}): source.input_format korrekt")
                with wave.open(out2, "rb") as w:
                    check(w.getnchannels() == 2 and w.getsampwidth() == 2,
                          f"S2 ({fmt2}): Ausgabedatei ist lesbare 16-Bit-Stereo-WAV")
            except Exception as exc:
                check(False, f"S2 ({fmt2}): unerwarteter Fehler ({exc})")

        # S3 (wichtigster Test): dasselbe Signal einmal mit sehr kleiner und
        # einmal mit sehr grosser Blockgroesse konvertieren - Nachweis, dass
        # Mischerphase (V2) und Filterzustand (V3) korrekt ueber Block-
        # grenzen fortgefuehrt werden. scan_seconds absichtlich groesser als
        # die Signaldauer, damit die Amplitudenvorabmessung in beiden
        # Faellen die GESAMTE Datei erfasst - sonst waeren unterschiedliche
        # Skalierungsfaktoren eine zweite, hier ungewollte Fehlerquelle.
        fs3 = 200_000.0
        dur3 = 0.2
        decimate3 = 5
        n3 = int(fs3 * dur3)
        rng3 = np.random.default_rng(9003)
        z3 = (rng3.normal(0.0, 1.0, n3) + 1j * rng3.normal(0.0, 1.0, n3)) * 0.2
        cf32_path3 = os.path.join(tmp, "s3.cf32")
        _write_raw_fixture(cf32_path3, "cf32", z3)
        out3_small = os.path.join(tmp, "s3_small_iq.wav")
        out3_big = os.path.join(tmp, "s3_big_iq.wav")
        try:
            common3 = dict(input=cf32_path3, format="cf32", in_rate=fs3, in_freq=433_000_000.0,
                            tune=433_012_345.0, decimate=decimate3, scan_seconds=10.0)
            convert(ns(out=out3_small, **common3), block_samples=17)
            convert(ns(out=out3_big, **common3), block_samples=10_000_000)
            i_small, q_small = _read_wav_int16_iq(out3_small)
            i_big, q_big = _read_wav_int16_iq(out3_big)
            check(len(i_small) == len(i_big),
                  f"S3: gleiche Sample-Anzahl (klein={len(i_small)}, gross={len(i_big)})")
            n_cmp = min(len(i_small), len(i_big))
            diff_i = np.abs(i_small[:n_cmp].astype(int) - i_big[:n_cmp].astype(int))
            diff_q = np.abs(q_small[:n_cmp].astype(int) - q_big[:n_cmp].astype(int))
            max_diff = int(max(diff_i.max(initial=0), diff_q.max(initial=0)))
            print(f"  S3: max. Abweichung kleine/grosse Bloecke = {max_diff} LSB (n={n_cmp})")
            check(max_diff < 2, f"S3: max. Abweichung unter 2 LSB (gemessen {max_diff})")
        except Exception as exc:
            check(False, f"S3: unerwarteter Fehler ({exc})")

        # S4: Verschiebung - Signal bei +300 kHz von der Mitte erzeugen, mit
        # passendem --tune importieren. Erwartung: eye_diagram misst danach
        # einen Frequenzoffset nahe null.
        fs4 = 1_000_000.0
        dur4 = 1.0
        decimate4 = 25
        shift4 = 300_000.0
        in_freq4 = 433_000_000.0
        tune4 = in_freq4 + shift4
        rng4 = np.random.default_rng(9004)
        base4 = _synth_fsk_baseband(fs4, 1200.0, 4500.0, dur4, rng4, amplitude=0.6)
        t4 = np.arange(len(base4)) / fs4
        z4 = base4 * np.exp(1j * 2.0 * math.pi * shift4 * t4)
        cf32_path4 = os.path.join(tmp, "s4.cf32")
        _write_raw_fixture(cf32_path4, "cf32", z4)
        out4 = os.path.join(tmp, "s4_iq.wav")
        try:
            convert(ns(input=cf32_path4, format="cf32", in_rate=fs4, in_freq=in_freq4,
                       tune=tune4, decimate=decimate4, scan_seconds=1.0, out=out4))
            import eye_diagram as ed  # bereits oben importiert, hier fuer S4 isoliert erneut
            r4 = ed.analyze_file(out4)
            print(f"  S4: gemessener Offset={r4['offset_hz']:+.1f} Hz")
            check(abs(r4["offset_hz"]) < 100.0,
                  f"S4: |Offset| < 100 Hz nach --tune ({r4['offset_hz']:+.1f} Hz)")
        except Exception as exc:
            check(False, f"S4: unerwarteter Fehler ({exc})")

        # S5: Aliasing-Nachweis analog T13 in eye_diagram.py - Stoerton
        # ausserhalb des Zielbands, einmal mit und einmal ohne Tiefpass
        # (interner Testpfad skip_lowpass). Ohne Tiefpass muss das Ergebnis
        # deutlich vom wahren Hub abweichen.
        fs5 = 200_000.0
        baud5 = 100.0
        dev5 = 225.0
        decimate5 = 40
        out_rate5 = fs5 / decimate5  # 5000 Hz
        interferer_hz5 = 15_300.0  # aliasiert ohne Tiefpass auf +300 Hz (15300 mod 5000)
        dur5 = 3.0
        rng5 = np.random.default_rng(9005)
        fsk5 = _synth_fsk_baseband(fs5, baud5, dev5, dur5, rng5, amplitude=0.4)
        t5 = np.arange(len(fsk5)) / fs5
        z5 = fsk5 + 0.6 * np.exp(1j * 2.0 * math.pi * interferer_hz5 * t5)
        cf32_path5 = os.path.join(tmp, "s5.cf32")
        _write_raw_fixture(cf32_path5, "cf32", z5)
        out5_lp = os.path.join(tmp, "s5_lp_iq.wav")
        out5_nolp = os.path.join(tmp, "s5_nolp_iq.wav")
        try:
            common5 = dict(input=cf32_path5, format="cf32", in_rate=fs5, decimate=decimate5,
                            scan_seconds=5.0)
            convert(ns(out=out5_lp, **common5))
            convert(ns(out=out5_nolp, **common5), skip_lowpass=True)

            import eye_diagram as ed
            r5_lp = ed.analyze_file(out5_lp, decimate=1, min_baud=20.0)
            dev_err_lp = abs(r5_lp["dev_hz"] - dev5) / dev5
            print(f"  S5 (mit Tiefpass): dev_hz={r5_lp['dev_hz']:.1f} (Fehler {dev_err_lp * 100:.1f}%) "
                  f"level_count={r5_lp['level_count']}")
            check(dev_err_lp <= 0.20 and r5_lp["level_count"] == 2,
                  f"S5 (mit Tiefpass): Ergebnis nahe am wahren Hub ({r5_lp['dev_hz']:.1f} Hz)")

            try:
                r5_nolp = ed.analyze_file(out5_nolp, decimate=1, min_baud=20.0)
                dev_err_nolp = abs(r5_nolp["dev_hz"] - dev5) / dev5
                print(f"  S5 (ohne Tiefpass): dev_hz={r5_nolp['dev_hz']:.1f} "
                      f"(Fehler {dev_err_nolp * 100:.1f}%) level_count={r5_nolp['level_count']}")
                significant = dev_err_nolp > 0.20 or r5_nolp["level_count"] != 2
                if not significant:
                    check(False, "S5: STOP - ohne Tiefpass weicht das Ergebnis NICHT deutlich ab "
                                  f"(dev_hz={r5_nolp['dev_hz']:.1f}, Fehler {dev_err_nolp * 100:.1f}%) "
                                  "- Filterkette pruefen")
                else:
                    check(True, "S5: ohne Tiefpass weicht das Ergebnis deutlich ab (Aliasing-Nachweis)")
            except RuntimeError as exc:
                print(f"  S5 (ohne Tiefpass): sauberer Abbruch: {exc}")
                check(True, "S5: ohne Tiefpass sauberer Abbruch gilt als deutliche Abweichung")
        except Exception as exc:
            check(False, f"S5: unerwarteter Fehler ({exc})")

        # S6: Fehlerfaelle - jeweils sauberer Abbruch mit Meldung, kein
        # Traceback. Aufruf ueber main() mit expliziter argv-Liste (kein
        # Subprozess), damit der echte CLI-Fehlerpfad (inkl. argparse)
        # geprueft wird.
        def _run_main_expect_fail(argv: list, label: str) -> bool:
            try:
                rc = main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                print(f"  {label}: argparse-Abbruch (SystemExit {code})")
                return code != 0
            except Exception as exc:
                print(f"  {label}: unerwartete Ausnahme statt sauberem Abbruch: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
                return False
            print(f"  {label}: main() liefert rc={rc}")
            return rc != 0

        tiny_cs8 = os.path.join(tmp, "s6.cs8")
        _write_raw_fixture(tiny_cs8, "cs8", np.zeros(100, dtype=complex))
        missing_path = os.path.join(tmp, "s6_fehlt.cs8")

        check(_run_main_expect_fail([tiny_cs8, "--format", "cs8"], "S6a"),
              "S6a: rohes Format ohne --in-rate bricht sauber ab")
        check(_run_main_expect_fail([tiny_cs8, "--format", "bogus", "--in-rate", "1000"], "S6b"),
              "S6b: unbekanntes --format bricht sauber ab")
        check(_run_main_expect_fail([missing_path, "--format", "cs8", "--in-rate", "1000"], "S6c"),
              "S6c: nicht existierende Datei bricht sauber ab")
        check(_run_main_expect_fail(
            [tiny_cs8, "--format", "cs8", "--in-rate", "1000", "--tune", "1000000"], "S6d"),
              "S6d: --tune ohne --in-freq bricht sauber ab")

    if ok:
        print("SELFTEST OK")
    else:
        print("SELFTEST FEHLGESCHLAGEN", file=sys.stderr)
    return ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", nargs="?", help="Pfad zur Eingangsdatei (roh, WAV oder SigMF .sigmf-meta)")
    p.add_argument("--format", choices=KNOWN_FORMATS,
                   help="Eingangsformat (sonst Automatik anhand der Dateiendung)")
    p.add_argument("--in-rate", type=float, help="Abtastrate der Eingangsdatei in Hz "
                                                   "(zwingend bei cs8/cs16/cf32)")
    p.add_argument("--in-freq", type=float, help="Mittenfrequenz der Eingangsdatei in Hz")
    p.add_argument("--tune", type=float, help="gewuenschte Zielmittenfrequenz in Hz (erfordert --in-freq)")
    p.add_argument("--decimate", type=int, help="Dezimationsfaktor (sonst automatisch anhand "
                                                  "--min-out-rate)")
    p.add_argument("--min-out-rate", type=float, default=32000.0,
                   help="Mindest-Ausgangsrate fuer die automatische Dezimationswahl (Default 32000)")
    p.add_argument("--scan-seconds", type=float, default=5.0,
                   help="Vorabmessung fuer die Amplitudenskalierung, Ausgangsdauer in Sekunden "
                        "(Default 5)")
    p.add_argument("--out", help="Ziel-WAV (Default: <Eingang> mit Endung _iq.wav im selben "
                                  "Verzeichnis)")
    p.add_argument("--info", action="store_true",
                   help="nur Kopfdaten/Dezimationsvorschlag ausgeben, nicht konvertieren")
    p.add_argument("--selftest", action="store_true", help="Selbsttest ohne Geraet/Eingabedatei ausfuehren")
    return p


def main(argv: list | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.selftest:
        return 0 if _self_test() else 1

    if not args.input:
        parser.error("Eingangsdatei erforderlich (ausser bei --selftest)")

    try:
        return convert(args)
    except FileNotFoundError as exc:
        print(f"FEHLER: Datei nicht gefunden: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
