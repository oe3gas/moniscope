#!/usr/bin/env python3
"""
tools/tone_probe.py

Messauftrag M2 (PLAN.md): Wechseln die beiden gemessenen Toene eines FSK-
verdaechtigen Signals einander ab (echtes 2-FSK), oder stehen sie gleichzeitig
(zwei parallele Traeger, keine Tastung)? Ergebnis sind Zahlen - Pearson-
Korrelation und Koinzidenzanteil zweier Pegelzeitreihen -, kein Merkmal und
keine Schwelle.

Eigenstaendiges Werkzeug (Muster tools/afc_probe.py): reines Messwerkzeug,
keine Server-Anbindung, --selftest ohne Aufnahme/Geraet. Anders als
afc_probe.py (das bewusst auf numpy verzichtet, weil es NUR SCPI spricht)
braucht dieses Werkzeug numpy fuer die Pegelberechnung - numpy ist ohnehin
Pflichtabhaengigkeit der Suite (CLAUDE.md).

Liest dieselbe WAV wie tools/eye_diagram.py ueber dessen bereits vorhandene,
gepruefte Bausteine (read_wav_iq/read_sidecar/resolve_fs/tune_to_baseband/
apply_decimation/detect_bursts/freq_offset_and_deviation/moving_average/
zero_crossings/analyze_file) - NUR IMPORTIERT, keine Aenderung an
eye_diagram.py. Eigene Rechnung nur fuer das, was eye_diagram.py nicht
liefert: die Pegelzeitreihe je Ton (Heterodyn auf den Ton, Tiefpass,
Blockmittel in dB), Pearson-Korrelation, Koinzidenzanteil und die
synthetische 2-FSK-Vergleichsfixtur.

Aufruf:
  python tools/tone_probe.py --selftest
  python tools/tone_probe.py records/20260912_202830_7646kHz_IQ.wav ^
      --t0 10 --t1 30 --tune -75 --auto-decimate 750
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

# Erlaubt den Direktaufruf "python tools/tone_probe.py" UND das Importieren
# von eye_diagram.py als Nachbarmodul im selben Verzeichnis (Muster
# tools/eye_diagram.py, dort fuer eb200_core).
if __package__ in (None, ""):
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    if _THIS_DIR not in sys.path:
        sys.path.insert(0, _THIS_DIR)

from eye_diagram import (          # noqa: E402 - Importreihenfolge nach sys.path-Fix
    POSTDET_FILTER_REL,
    analyze_file,
    apply_decimation,
    detect_bursts,
    freq_offset_and_deviation,
    moving_average,
    read_sidecar,
    read_wav_iq,
    resolve_fs,
    suggest_decimation,
    tune_to_baseband,
    zero_crossings,
)

# ---------------------------------------------------------------------------
# Kopf-Konstanten (Defaults aus der T-1710-Erprobung, siehe PLAN.md M2)
# ---------------------------------------------------------------------------

DEFAULT_TONE1_HZ = -228.9
DEFAULT_TONE2_HZ = 209.0
DEFAULT_RESOLUTION_MS = 4.0

# Tiefpass-Grenzfrequenz fuer die Pegelzeitreihe je Ton: deutlich unter dem
# Tonabstand (rund 438 Hz in der Erprobung), damit der jeweils ANDERE Ton
# nach dem Heterodynieren zuverlaessig unterdrueckt wird, aber breit genug,
# um Dynamik auf der geforderten ~4-ms-Zeitskala (Nyquist 125 Hz) noch zu
# zeigen. 150 Hz liegt zwischen beidem.
DEFAULT_LOWPASS_HZ = 150.0

DEFAULT_SYNTH_BAUD = 50.0
# 437,8 Hz ist der in der Erprobung GEMESSENE Tonabstand (siehe
# eye_diagram-Bericht "Tonabstaende (Hz): 437.8"); dev_hz ist die halbe
# Spanne, wie in eye_diagram.freq_offset_and_deviation definiert.
DEFAULT_SYNTH_DEV_HZ = 437.8 / 2.0
DEFAULT_SYNTH_SNR_DB = 10.0
DEFAULT_SYNTH_DUR_S = 20.0


# ---------------------------------------------------------------------------
# Rechenkern (ohne Aufnahme testbar - siehe run_selftest())
# ---------------------------------------------------------------------------

def make_lowpass_kernel(fs: float, cutoff_hz: float, taps: int) -> np.ndarray:
    """Fensterentwurf (Hamming-Fenster, sinc-Kern), auf Summe 1 normiert -
    gleiches Verfahren wie eye_diagram.lowpass_kernel(), hier aber mit einer
    frei waehlbaren Grenzfrequenz in Hz statt an einen Dezimationsfaktor
    gekoppelt (dieses Werkzeug filtert auf einen TONABSTAND, nicht auf eine
    neue Abtastrate)."""
    if taps % 2 == 0:
        taps += 1
    m = np.arange(taps) - (taps - 1) / 2.0
    cutoff_rel = cutoff_hz / (fs / 2.0)
    kernel = np.sinc(cutoff_rel * m) * np.hamming(taps)
    kernel /= kernel.sum()
    return kernel


def level_trace_db(z: np.ndarray, fs: float, tone_hz: float, lowpass_hz: float,
                    resolution_ms: float, taps: int = 201) -> np.ndarray:
    """Pegelzeitreihe (dB) fuer EINEN Ton: auf tone_hz heterodynieren (der
    Ton liegt danach bei 0 Hz), mit einem schmalbandigen Tiefpass filtern
    (unterdrueckt den jeweils anderen Ton und Rauschen ausserhalb des
    Tons), dann in Bloecke von rund resolution_ms Millisekunden mitteln
    (nicht ueberlappend) und die Blockleistung in dB (10*log10) ausgeben.
    Leistungs- statt Amplitudenpegel, wie im uebrigen Werkzeugkasten
    (eye_diagram.compute_contrast_and_flatness: 10*log10(p95/p10))."""
    n = np.arange(len(z), dtype=np.float64)
    z_ton = z * np.exp(-2j * np.pi * float(tone_hz) * n / float(fs))
    kernel = make_lowpass_kernel(fs, lowpass_hz, taps)
    z_gefiltert = np.convolve(z_ton, kernel, mode="same")
    leistung = np.abs(z_gefiltert) ** 2

    block = max(1, round(resolution_ms / 1000.0 * fs))
    n_bloecke = len(leistung) // block
    if n_bloecke == 0:
        return np.empty(0)
    geschnitten = leistung[: n_bloecke * block].reshape(n_bloecke, block)
    leistung_block = geschnitten.mean(axis=1)
    return 10.0 * np.log10(leistung_block + 1e-15)


def pearson_r(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson-Korrelationskoeffizient zweier gleich langer Reihen. 0.0 bei
    < 2 Werten oder Varianz 0 in einer Reihe (kein Zusammenhang bestimmbar,
    Muster tools/afc_probe.py fit_offset_slope)."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a = a[:n]
    b = b[:n]
    std_a = float(np.std(a))
    std_b = float(np.std(b))
    if std_a == 0.0 or std_b == 0.0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def coincidence_fraction(a: np.ndarray, b: np.ndarray) -> float:
    """Anteil der Bloecke, in denen BEIDE Reihen ueber ihrem jeweils
    EIGENEN Median liegen. Bei zwei unabhaengigen Reihen ist das im
    Erwartungswert 0.25 (zwei unabhaengige 50%-Ereignisse); echtes FSK
    (stark antikorreliert) liegt deutlich darunter, zwei durchgehend
    gemeinsam schwankende Traeger (positiv korreliert) deutlich darueber."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    a = a[:n]
    b = b[:n]
    ueber_a = a > np.median(a)
    ueber_b = b > np.median(b)
    return float(np.mean(ueber_a & ueber_b))


def make_fsk2_signal(fs: float, baud: float, dev_hz: float, dur_s: float,
                      snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """Synthetisches 2-FSK-Signal als Vergleichsanker (Phasenakkumulation +
    additives Rauschen, gleiches Verfahren wie eye_diagram._make_fsk_signal,
    hier eigenstaendig nachgebildet statt aus eye_diagram importiert - das
    Werkzeug soll ohne dessen private Testfixturen auskommen)."""
    n = int(round(fs * dur_s))
    sps = fs / baud
    n_symbols = int(math.ceil(n / sps)) + 2
    sym = rng.integers(0, 2, n_symbols)
    levels = np.array([-dev_hz, dev_hz])
    freq_seq = levels[sym]
    idx = np.clip((np.arange(n) / sps).astype(int), 0, n_symbols - 1)
    inst_freq = freq_seq[idx]
    phase = np.cumsum(2.0 * math.pi * inst_freq / fs)
    sig = np.exp(1j * phase)
    snr_lin = 10.0 ** (snr_db / 10.0)
    noise_power = 1.0 / snr_lin
    noise = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) * math.sqrt(noise_power / 2.0)
    return sig + noise


def reproduce_crossings(wav_path: str, t0_s: float, t1_s: float, tune_hz: float,
                         decimate: int, min_baud: float) -> tuple[np.ndarray, dict]:
    """Liefert (positions, result): result = eye_diagram.analyze_file() mit
    denselben Parametern (liefert u.a. baud/T_samples/n_symbols_used wie im
    Bericht), positions = dieselbe Nulldurchgangsfolge, die analyze_file()
    INTERN bildet, aber nicht im Ergebnis-dict zurueckgibt - hier ueber
    dieselben oeffentlichen Bausteine (read_wav_iq/tune_to_baseband/
    apply_decimation/detect_bursts/freq_offset_and_deviation/
    moving_average/zero_crossings) und denselben postdet_window_samples-Wert
    aus result NACHGEBILDET, keine erneute Schaetzung - daher bitgleich."""
    result = analyze_file(wav_path, decimate=decimate, min_baud=min_baud,
                           t0_s=t0_s, t1_s=t1_s, tune_hz=tune_hz)

    framerate_header, z = read_wav_iq(wav_path, t0_s, t1_s)
    sidecar = read_sidecar(wav_path)
    fs = resolve_fs(framerate_header, sidecar)
    z = tune_to_baseband(z, framerate_header, tune_hz)
    z_eff, fs_eff = apply_decimation(z, fs, decimate, min_baud)
    bursts, _mode, _contrast, _flatness, f_full = detect_bursts(z_eff, fs_eff)
    chosen_i = max(range(len(bursts)), key=lambda i: bursts[i][1] - bursts[i][0])
    start, end = bursts[chosen_i]
    f_burst = f_full[start:end - 1]
    _offset_hz, _dev_hz, _peaks, f_korr = freq_offset_and_deviation(f_burst, fs_eff)
    f_det = moving_average(f_korr, result["postdet_window_samples"], circular=False)
    positions = zero_crossings(f_det)
    return positions, result


# ---------------------------------------------------------------------------
# Selbsttest (--selftest, keine Aufnahme noetig)
# ---------------------------------------------------------------------------

def run_selftest() -> bool:
    print("Selbsttest tone_probe (Rechenkern, keine Aufnahme) ...")
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        if cond:
            print(f"  OK  {msg}")
        else:
            print(f"  FEHLER {msg}", file=sys.stderr)
            ok = False

    # S1: Tiefpasskern ist auf Summe 1 normiert (DC-treu).
    kernel = make_lowpass_kernel(4000.0, 150.0, 101)
    check(abs(float(kernel.sum()) - 1.0) < 1e-9, f"S1: Tiefpasskern summiert zu 1 ({kernel.sum():.6f})")

    # S2: level_trace_db erkennt einen getasteten Einzelton (ON/OFF im
    # 100-ms-Takt) korrekt als hohen/niedrigen Pegel wieder.
    fs2 = 4000.0
    dur2 = 4.0
    n2 = int(fs2 * dur2)
    t2 = np.arange(n2) / fs2
    ton2 = 100.0
    an2 = (np.floor(t2 / 0.1).astype(int) % 2 == 0)
    amp2 = np.where(an2, 1.0, 0.02)
    z2 = amp2 * np.exp(1j * 2.0 * math.pi * ton2 * t2)
    rng2 = np.random.default_rng(1)
    z2 = z2 + 0.01 * (rng2.normal(0, 1, n2) + 1j * rng2.normal(0, 1, n2))
    pegel2 = level_trace_db(z2, fs2, ton2, DEFAULT_LOWPASS_HZ, DEFAULT_RESOLUTION_MS)
    block2 = max(1, round(DEFAULT_RESOLUTION_MS / 1000.0 * fs2))
    an_block2 = an2[: (len(an2) // block2) * block2].reshape(-1, block2).mean(axis=1) > 0.5
    n_check2 = min(len(pegel2), len(an_block2))
    check(float(np.mean(pegel2[:n_check2][an_block2[:n_check2]])
                > np.mean(pegel2[:n_check2][~an_block2[:n_check2]]) + 10.0),
          "S2: level_trace_db zeigt getasteten Ton als deutlich hoeheren Pegel in den AN-Bloecken")

    # S3: echtes 2-FSK (Toene wechseln sich ab) -> stark negative Pearson-
    # Korrelation und niedriger Koinzidenzanteil (deutlich unter der
    # 0,25-Basislinie zweier unabhaengiger Reihen).
    rng3 = np.random.default_rng(11)
    dev3 = 200.0
    sig3 = make_fsk2_signal(4000.0, 50.0, dev3, 20.0, 15.0, rng3)
    p1_3 = level_trace_db(sig3, 4000.0, -dev3, DEFAULT_LOWPASS_HZ, DEFAULT_RESOLUTION_MS)
    p2_3 = level_trace_db(sig3, 4000.0, dev3, DEFAULT_LOWPASS_HZ, DEFAULT_RESOLUTION_MS)
    r3 = pearson_r(p1_3, p2_3)
    koinz3 = coincidence_fraction(p1_3, p2_3)
    print(f"    S3: echtes 2-FSK -> r={r3:.3f}, Koinzidenz={koinz3:.3f}")
    check(r3 < -0.4, f"S3: Pearson-r stark negativ bei echtem 2-FSK ({r3:.3f} < -0.4)")
    check(koinz3 < 0.15, f"S3: Koinzidenzanteil klein bei echtem 2-FSK ({koinz3:.3f} < 0.15)")

    # S4: zwei GLEICHZEITIG durchgehende, unabhaengig verrauschte Traeger
    # (keine Tastung) -> Korrelation nahe 0, Koinzidenz nahe der
    # 0,25-Basislinie zweier unabhaengiger Ereignisse.
    rng4 = np.random.default_rng(12)
    fs4 = 4000.0
    dur4 = 20.0
    n4 = int(fs4 * dur4)
    t4 = np.arange(n4) / fs4
    tonA4, tonB4 = -228.9, 209.0
    z4 = (np.exp(1j * 2.0 * math.pi * tonA4 * t4) + np.exp(1j * 2.0 * math.pi * tonB4 * t4))
    z4 = z4 + (10.0 ** (-15.0 / 20.0)) * (rng4.normal(0, 1, n4) + 1j * rng4.normal(0, 1, n4))
    p1_4 = level_trace_db(z4, fs4, tonA4, DEFAULT_LOWPASS_HZ, DEFAULT_RESOLUTION_MS)
    p2_4 = level_trace_db(z4, fs4, tonB4, DEFAULT_LOWPASS_HZ, DEFAULT_RESOLUTION_MS)
    r4 = pearson_r(p1_4, p2_4)
    koinz4 = coincidence_fraction(p1_4, p2_4)
    print(f"    S4: zwei durchgehende Traeger -> r={r4:.3f}, Koinzidenz={koinz4:.3f}")
    check(abs(r4) < 0.3, f"S4: Pearson-r nahe 0 bei zwei durchgehenden Traegern ({r4:.3f})")
    check(0.10 <= koinz4 <= 0.40,
          f"S4: Koinzidenzanteil nahe der 0,25-Basislinie ({koinz4:.3f})")

    # S5: pearson_r/coincidence_fraction ohne Absturz bei entarteten Reihen.
    check(pearson_r(np.array([1.0]), np.array([2.0])) == 0.0, "S5a: pearson_r bei < 2 Werten -> 0.0")
    check(pearson_r(np.array([1.0, 1.0, 1.0]), np.array([2.0, 3.0, 4.0])) == 0.0,
          "S5b: pearson_r bei Varianz 0 -> 0.0")
    check(coincidence_fraction(np.array([]), np.array([])) == 0.0, "S5c: coincidence_fraction bei leeren Reihen -> 0.0")

    # S6: reproduce_crossings liefert bei einer reinen Wechselfolge (jedes
    # Symbol wechselt den Pegel) annaehernd einen Nulldurchgang PRO Symbol -
    # Regressionsschutz fuer die Pipeline-Nachbildung.
    import tempfile
    import wave

    def _write_wav(tmp_dir: str, name: str, z: np.ndarray, framerate: int) -> str:
        path = os.path.join(tmp_dir, name)
        i = np.clip(np.round(z.real * 32768.0), -32768, 32767).astype(np.int16)
        q = np.clip(np.round(z.imag * 32768.0), -32768, 32767).astype(np.int16)
        interleaved = np.empty(2 * len(z), dtype=np.int16)
        interleaved[0::2] = i
        interleaved[1::2] = q
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(framerate)
            w.writeframes(interleaved.tobytes())
        import json
        with open(path + ".json", "w", encoding="utf-8") as f:
            json.dump({"freq_hz": 162475000, "sample_rate_hz": framerate, "is_iq": True}, f)
        return path

    try:
        fs6 = 32000
        rng6 = np.random.default_rng(6)
        sig6_alt = make_fsk2_signal(fs6, 1200.0, 4500.0, 2.0, 20.0, rng6)
        # Wechselfolge statt Zufallsfolge: jedes Symbol kippt den Pegel.
        sps6 = fs6 / 1200.0
        n6 = int(fs6 * 2.0)
        n_sym6 = int(math.ceil(n6 / sps6)) + 2
        sym6 = np.arange(n_sym6) % 2
        levels6 = np.array([-4500.0, 4500.0])
        idx6 = np.clip((np.arange(n6) / sps6).astype(int), 0, n_sym6 - 1)
        phase6 = np.cumsum(2.0 * math.pi * levels6[sym6][idx6] / fs6)
        sig6_alt = np.exp(1j * phase6)
        with tempfile.TemporaryDirectory() as tmp6:
            path6 = _write_wav(tmp6, "t_s6.wav", sig6_alt, fs6)
            positions6, result6 = reproduce_crossings(path6, None, None, 0.0, 1, 20.0)
        n_sym_used6 = result6["n_symbols_used"]
        verhaeltnis6 = len(positions6) / n_sym_used6 if n_sym_used6 else 0.0
        print(f"    S6: Wechselfolge -> {len(positions6)} Nulldurchgaenge / {n_sym_used6} Symbole "
              f"(Verhaeltnis {verhaeltnis6:.2f})")
        check(0.8 <= verhaeltnis6 <= 1.2,
              f"S6: bei reiner Wechselfolge liegt das Verhaeltnis nahe 1 ({verhaeltnis6:.2f})")
    except Exception as exc:
        check(False, f"S6: unerwarteter Fehler ({exc})")

    if ok:
        print("\nSELBSTTEST OK")
    else:
        print("\nSELBSTTEST FEHLGESCHLAGEN", file=sys.stderr)
    return ok


# ---------------------------------------------------------------------------
# SVG (optional, --svg) - reines SVG ohne Fremdbibliothek, Muster
# eye_diagram.write_svg, aber eigene (einfachere) Zwei-Kurven-Darstellung.
# ---------------------------------------------------------------------------

def write_level_svg(path: str, t_s: np.ndarray, pegel1_db: np.ndarray, pegel2_db: np.ndarray,
                     label1: str, label2: str) -> None:
    width, height = 760, 360
    margin_left, margin_right, margin_top, margin_bottom = 60, 20, 20, 40
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    alle = np.concatenate([pegel1_db, pegel2_db])
    y_min, y_max = float(np.min(alle)), float(np.max(alle))
    if y_max <= y_min:
        y_max = y_min + 1.0
    t_min, t_max = float(t_s[0]), float(t_s[-1]) if len(t_s) else 1.0
    if t_max <= t_min:
        t_max = t_min + 1.0

    def x_of(t: float) -> float:
        return margin_left + (t - t_min) / (t_max - t_min) * plot_w

    def y_of(v: float) -> float:
        return margin_top + (1.0 - (v - y_min) / (y_max - y_min)) * plot_h

    def polyline(werte: np.ndarray, farbe: str) -> str:
        punkte = " ".join(f"{x_of(t):.1f},{y_of(v):.1f}" for t, v in zip(t_s, werte))
        return f'<polyline points="{punkte}" fill="none" stroke="{farbe}" stroke-width="1.5"/>'

    teile = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        f'<rect x="{margin_left}" y="{margin_top}" width="{plot_w}" height="{plot_h}" '
        f'fill="none" stroke="black"/>',
        polyline(pegel1_db, "#1f77b4"),
        polyline(pegel2_db, "#d62728"),
        f'<text x="{margin_left}" y="{margin_top - 6}" font-size="12">{label1} (blau) / {label2} (rot), '
        f'{y_min:.0f}..{y_max:.0f} dB</text>',
        f'<text x="{margin_left}" y="{height - 10}" font-size="12">{t_min:.2f}..{t_max:.2f} s</text>',
        "</svg>",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(teile))
        f.write("\n")


# ---------------------------------------------------------------------------
# Hauptmessung und Bericht
# ---------------------------------------------------------------------------

def run_measurement(args: argparse.Namespace) -> int:
    if args.auto_decimate > 0:
        import wave
        with wave.open(args.wav, "rb") as w:
            fs_header = w.getframerate()
        decimate = suggest_decimation(fs_header, args.auto_decimate, args.min_baud)
        print(f"Auto-Dezimation: Bandbreite {args.auto_decimate:.0f} Hz -> N={decimate}")
    else:
        decimate = args.decimate

    framerate_header, z = read_wav_iq(args.wav, args.t0, args.t1)
    sidecar = read_sidecar(args.wav)
    fs = resolve_fs(framerate_header, sidecar)
    z = tune_to_baseband(z, framerate_header, args.tune)
    z_eff, fs_eff = apply_decimation(z, fs, decimate, args.min_baud)
    print(f"Datei: {args.wav}")
    print(f"Abtastrate: {fs} Hz, Dezimation N={decimate}, effektive Abtastrate {fs_eff:.1f} Hz")
    print(f"Zeitfenster: {args.t0}..{args.t1} s, Verstimmung {args.tune:+.1f} Hz")

    pegel1 = level_trace_db(z_eff, fs_eff, args.tone1, args.lowpass_hz, args.resolution_ms)
    pegel2 = level_trace_db(z_eff, fs_eff, args.tone2, args.lowpass_hz, args.resolution_ms)
    r_echt = pearson_r(pegel1, pegel2)
    koinz_echt = coincidence_fraction(pegel1, pegel2)
    print(f"\nPegelzeitreihen: {len(pegel1)} Bloecke a {args.resolution_ms:.1f} ms "
          f"(Tiefpass {args.lowpass_hz:.0f} Hz)")
    print(f"Ton 1 ({args.tone1:+.1f} Hz) / Ton 2 ({args.tone2:+.1f} Hz):")
    print(f"  Pearson-Korrelation: {r_echt:.3f}")
    print(f"  Koinzidenzanteil (beide ueber eigenem Median): {koinz_echt:.3f}")

    rng = np.random.default_rng(1710)
    synth = make_fsk2_signal(fs_eff, args.synth_baud, args.synth_dev_hz, args.synth_dur_s,
                              args.synth_snr_db, rng)
    synth_pegel1 = level_trace_db(synth, fs_eff, -args.synth_dev_hz, args.lowpass_hz, args.resolution_ms)
    synth_pegel2 = level_trace_db(synth, fs_eff, args.synth_dev_hz, args.lowpass_hz, args.resolution_ms)
    r_synth = pearson_r(synth_pegel1, synth_pegel2)
    koinz_synth = coincidence_fraction(synth_pegel1, synth_pegel2)
    print(f"\nVergleichsanker: synthetisches 2-FSK ({args.synth_baud:.0f} Bd, "
          f"{2.0 * args.synth_dev_hz:.1f} Hz Tonabstand, {args.synth_snr_db:.0f} dB SNR, "
          f"{args.synth_dur_s:.0f} s):")
    print(f"  Pearson-Korrelation: {r_synth:.3f}")
    print(f"  Koinzidenzanteil: {koinz_synth:.3f}")

    positions, result = reproduce_crossings(args.wav, args.t0, args.t1, args.tune, decimate, args.min_baud)
    n_symbole = result["n_symbols_used"]
    verhaeltnis = len(positions) / n_symbole if n_symbole else 0.0
    print(f"\nNulldurchgaenge: {len(positions)} gegenueber {n_symbole} ausgewerteten Symbolen "
          f"(Verhaeltnis {verhaeltnis:.2f})")

    if args.svg:
        n_1s = min(len(pegel1), len(pegel2), max(1, round(1.0 / (args.resolution_ms / 1000.0))))
        t_s = np.arange(n_1s) * (args.resolution_ms / 1000.0)
        write_level_svg(args.svg, t_s, pegel1[:n_1s], pegel2[:n_1s],
                         f"{args.tone1:+.1f} Hz", f"{args.tone2:+.1f} Hz")
        print(f"\nSVG geschrieben: {args.svg} (erste {n_1s * args.resolution_ms / 1000.0:.2f} s)")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Messauftrag M2: Tonwechsel (echtes FSK) oder Koexistenz (zwei Traeger)?"
    )
    parser.add_argument("wav", nargs="?", help="Pfad zur Stereo-IQ-WAV (nicht bei --selftest)")
    parser.add_argument("--t0", type=float, default=10.0)
    parser.add_argument("--t1", type=float, default=30.0)
    parser.add_argument("--tune", type=float, default=-75.0)
    parser.add_argument("--decimate", type=int, default=8)
    parser.add_argument("--auto-decimate", type=float, default=750.0,
                         help="Dezimation aus Bandbreite ableiten (Hz), 0 = --decimate direkt verwenden")
    parser.add_argument("--min-baud", type=float, default=20.0)
    parser.add_argument("--tone1", type=float, default=DEFAULT_TONE1_HZ)
    parser.add_argument("--tone2", type=float, default=DEFAULT_TONE2_HZ)
    parser.add_argument("--resolution-ms", type=float, default=DEFAULT_RESOLUTION_MS)
    parser.add_argument("--lowpass-hz", type=float, default=DEFAULT_LOWPASS_HZ)
    parser.add_argument("--synth-baud", type=float, default=DEFAULT_SYNTH_BAUD)
    parser.add_argument("--synth-dev-hz", type=float, default=DEFAULT_SYNTH_DEV_HZ)
    parser.add_argument("--synth-snr-db", type=float, default=DEFAULT_SYNTH_SNR_DB)
    parser.add_argument("--synth-dur-s", type=float, default=DEFAULT_SYNTH_DUR_S)
    parser.add_argument("--svg", default=None, help="Pegelkurven (erste 1 s) als SVG schreiben")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if not args.selftest and not args.wav:
        parser.error("WAV-Datei erforderlich (ausser bei --selftest).")
    return args


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = parse_args()
    if args.selftest:
        return 0 if run_selftest() else 1
    try:
        return run_measurement(args)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
