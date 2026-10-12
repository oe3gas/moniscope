#!/usr/bin/env python3
"""
audio_spectrum.py

STFT-Modul für Audio-Spektrogramme aus den WAV-Aufnahmen der Suite
(eb200_core/audio_wav.py: 16 kHz/16 Bit/mono, Sidecar "<wav>.json" mit
running-Flag seit T-1109). Rechenbasis für den kommenden Audio-Viewer
:8070 (T-1202/T-1203) sowie später für die Sprach-Nachklassifikation
(T-1007, konsumiert stft_db() direkt).

Bewusst im Repo-Root (NICHT eb200_core) - numpy ist hier erlaubt
(Meilenstein-12-Entscheidung), eb200_core/ bleibt stdlib-only.

Zwei Betriebsarten (T-914), unterschieden allein an der Kanalzahl der WAV:
  - "mono" (1 Kanal, 16 Bit): reelles Signal, rfft, n_bins = n_fft//2 + 1,
    Frequenzachse 0 .. Nyquist.
  - "iq" (2 Kanäle, 16 Bit): Kanal 0 = I, Kanal 1 = Q (Projektannahme,
    hardware-bestätigt T-901). Volle FFT mit fftshift, n_bins = n_fft,
    ZWEISEITIGE Frequenzachse -Nyquist .. +Nyquist relativ zur
    Empfangsfrequenz. Bin 0 ist damit die UNTERE Bandkante, nicht 0 Hz -
    die Zeilenordnung der Matrix bleibt unverändert (Bin 0 unten), nur ihre
    Bedeutung wandert. Alles außerhalb dieser beiden Fälle bleibt
    AudioSpectrumUnsupportedError.

Dateiformat "eb200-audspec-v2" (Cache-Datei "<voller WAV-Pfad>.audspec",
Suffix-Anhängung wie beim Sidecar - Festlegung aus T-1204):
  - Zeile 1: JSON-Header (UTF-8, mit "\\n" abgeschlossen):
      {"format": "eb200-audspec-v2",
       "kind": "mono" | "iq",
       "f_min_hz": <float>, "f_max_hz": <float>,
       "wav": {"size": <bytes>, "mtime": <float>, "sample_rate_hz": ...,
                "channels": 1, "n_frames": ..., "duration_s": ...},
       "db_min": <float>, "db_max": <float>,
       "sections": [{"n_fft": 512, "hop": 256, "rows_raw": ..., "rows": ...,
                      "n_bins": ..., "dt_row_s": ..., "decimation": <k>,
                      "offset": <bytes>, "nbytes": ...}, ... je EINE Section
                      für alle FFT_SIZES]}
  - danach die fünf uint8-Matrizen (zeilenweise, C-Ordnung) direkt
    hintereinander; "offset" ist relativ zum ersten Byte NACH dem
    Header-Newline (siehe load_audspec: base = f.tell() direkt nach der
    Header-Zeile).
  - Skala: uint8 0..255 linear zwischen db_min..db_max; db_min/db_max
    robust als 1- und 99,9-Perzentil über die Rohwerte ALLER FÜNF
    FFT-Größen gemeinsam (VOR der Dezimierung), Werte außerhalb
    geclippt - EINE globale Skala über alle fünf FFT-Größen, damit der
    FFT-Umschalter im Viewer nicht umskaliert. Die Perzentile stammen
    seit T-1212 aus einem mitlaufenden HISTOGRAMM (_HIST_BINS Klassen
    über _HIST_DB_MIN.._HIST_DB_MAX, 0,05 dB je Klasse) statt aus einer
    Sammlung aller Rohwerte - dieselbe Aussage, ohne die Rohmatrizen im
    Speicher halten zu müssen.
  - "dt_row_s" je Section ist die Zeit pro GESPEICHERTER (dezimierter)
    Zeile (= rohes hop/sample_rate_hz * decimation), nicht die rohe
    Frame-Schrittweite - der Konsument kann Zeile i direkt auf
    i * dt_row_s abbilden, ohne "decimation" gesondert verrechnen zu
    müssen (das Feld bleibt zur Anzeige/Diagnose trotzdem im Header).
  - "f_min_hz"/"f_max_hz" sind die MITTENFREQUENZEN von Bin 0 und Bin
    n_bins-1 (mono: 0 .. sample_rate/2; iq: -sample_rate/2 ..
    +sample_rate/2 - df). Der Konsument braucht damit die Kanalzahl nicht
    zu kennen, um seine Achse zu beschriften.
  - Gültigkeit beim Laden: wav.size UND wav.mtime müssen zur Datei
    passen, format-String muss stimmen, "kind" muss bekannt sein, UND die
    sections-Liste muss genau die n_fft-Werte aus FFT_SIZES abdecken
    (T-1208: ein Cache aus der Zeit vor 128/256 deckt sie nicht mehr
    ab) - sonst gilt der
    Cache als ungültig (Aufrufer baut neu). Die Datei ist REGENERIERBAR,
    Sidecar-Regel wie .befund.json (siehe eb200_core/recording_index.py).

Robustheit: Die WAV wird BLOCKWEISE gelesen (wave-Modul, 1 Mio Frames je
Block mit Überlapp-Carry von n_fft - hop Samples), NIE komplett in den
Speicher geladen - eine Stunden-Aufnahme würde sonst mehrere hundert MB
belegen (siehe Entscheidungslog: Browser-decodeAudioData scheidet aus
demselben Grund für den Viewer aus).

Seit T-1212 liest build_audspec() die Datei in EINEM einzigen Durchlauf
für alle FFT_SIZES (_stft_stream): je Block werden alle STFTs
gerechnet, jede FFT-Größe führt dabei ihren eigenen Überlapp-Carry. Die
Rohzeilen werden SOFORT max-hold-dezimiert (_MaxHoldRows) und in das
Skalen-Histogramm eingezählt; eine volle Rohmatrix entsteht nie. Vorher
lag je FFT-Größe die komplette Rohmatrix im Speicher - bei IQ sind das
rows_raw * n_bins = 2 * n_frames float32, also für jede der fünf Größen
gleich viel: eine knapp zweistündige IQ-Aufnahme (32 kHz, 217 Mio Frames)
belegte damit rund 8,7 GB allein für raw_by_fft, plus derselben Menge
noch einmal für die Perzentilbildung. Nach dem Umbau sind es MAX_ROWS *
n_bins float32 je Größe (zusammen gut 100 MB) plus dem Arbeitsspeicher
eines Blocks. Der Nebeneffekt: ein Fünftel der bisherigen Lese-I/O.

Start:
    python audio_spectrum.py <datei.wav> [--rebuild]
    python audio_spectrum.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import wave

import numpy as np

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

AUDSPEC_FORMAT = "eb200-audspec-v2"
KIND_MONO = "mono"
KIND_IQ = "iq"
# T-1618: 32 und 64 ergaenzt. Ob sie taugen, haengt an der ABTASTRATE, nicht
# an der FFT: bei 32 kHz ergibt n_fft=32 eine Binbreite von 1 kHz - ueber eine
# ZF-Bandbreite von 15 kHz sind das fuenfzehn Streifen, also kaum noch eine
# Spektralanalyse. Bei den HackRF-Aufnahmen mit 200 kHz sind es 6,25 kHz Bin
# bei 160 us Zeilendauer, und ein TETRA-Symbol dauert bei 18 000 Bd rund
# 56 us - dort ist die grobe FFT genau das richtige Werkzeug. Die Auswahl
# wird deshalb in :8070 gekennzeichnet, nicht gesperrt (T-1619).
FFT_SIZES = (32, 64, 128, 256, 512, 1024, 2048)
MAX_ROWS = 8000          # Zeilen-Obergrenze der Übersicht je Section (Max-Hold)
MAX_WINDOW_ROWS = 20000  # Zeilen-Obergrenze für ein stft_window()-Zeitfenster
_BLOCK_FRAMES = 1_000_000
_EPS = 1e-12             # verhindert log10(0) bei Stille

# Histogramm für die globale dB-Skala (T-1212). Der Wertebereich ist
# analytisch begrenzt: nach unten durch 10*log10(_EPS) = -120 dB, nach oben
# durch |X| <= n_fft bei Samples <= 1, also 10*log10(2048^2) = 66 dB. Die
# Ränder liegen bewusst außerhalb; _hist_accumulate klemmt trotzdem, damit
# ein unerwarteter Wert nicht danebengreift.
_HIST_DB_MIN = -125.0
_HIST_DB_MAX = 75.0
_HIST_BINS = 4000        # 0,05 dB je Klasse über den gesamten Bereich


class AudioSpectrumBusyError(Exception):
    """Aufnahme läuft noch (Sidecar running=True) - Cache wird nicht gebaut."""


class AudioSpectrumUnsupportedError(Exception):
    """WAV-Format wird von diesem Modul nicht unterstützt (nur 1 Kanal
    16 Bit = mono und 2 Kanäle 16 Bit = IQ; alles andere, insbesondere
    8-Bit- und Mehrkanalaufnahmen, bleibt ungestützt)."""


class AudioSpectrumBuildingError(Exception):
    """Ein ANDERER Lauf baut dieses Spektrogramm gerade (Sperrdatei, siehe
    _lock_acquire) - Aufbereitungs-Worker oder ein zweiter Viewer.

    Kein Fehler im eigentlichen Sinn: der Aufrufer soll warten und den
    Fortschritt anzeigen, statt denselben Bau ein zweites Mal zu starten
    (das kostete bei einer langen IQ-Aufnahme sechs Minuten und mehrere
    hundert MB doppelt).

    snapshot: progress_snapshot()-Dict des laufenden Baus, oder None, wenn
    die Sperre nicht lesbar war.
    """

    def __init__(self, message: str, snapshot=None):
        super().__init__(message)
        self.snapshot = snapshot


# ---------------------------------------------------------------------------
# Kernfunktion (Konsument später T-1007)
# ---------------------------------------------------------------------------

def stft_db(samples, sample_rate_hz: int, n_fft: int, hop: int):
    """
    Kurzzeit-Fourier-Transformation über samples (1-D, Werte im Bereich
    -1..1 wie bei normalisiertem PCM). Hann-Fenster, Leistungs-dB
    10*log10(|X|^2 + eps) als float32-Matrix. KEINE Quantisierung hier -
    das ist Sache von build_audspec()/stft_window() (siehe Moduldoku,
    globale Skala).

    Zwei Fälle, unterschieden am dtype von samples (T-914):
      reell   -> rfft,               n_bins = n_fft//2 + 1, Bin b =  b*df
      komplex -> fft + fftshift,     n_bins = n_fft,        Bin b = (b - n_fft//2)*df
    Die Fallunterscheidung sitzt bewusst HIER und nicht in einer zweiten
    Funktion: Fensterung, Blocklogik und dB-Bildung sind identisch, und
    jeder Aufrufer mit reellem Eingang (auch audio_classify.py) verhält
    sich unverändert.

    Liefert (db, dt_row_s, df_hz). Weniger als n_fft Samples -> leere
    Matrix (0 Zeilen) statt Fehler, dt_row_s/df_hz bleiben trotzdem
    plausibel (aus sample_rate_hz/n_fft/hop berechnet).
    """
    samples = np.asarray(samples)
    is_complex = np.iscomplexobj(samples)
    samples = samples.astype(np.complex128 if is_complex else np.float64)
    n = len(samples)
    n_bins = n_fft if is_complex else n_fft // 2 + 1
    dt_row_s = hop / sample_rate_hz if sample_rate_hz else 0.0
    df_hz = sample_rate_hz / n_fft if n_fft else 0.0
    if n < n_fft:
        return np.empty((0, n_bins), dtype=np.float32), dt_row_s, df_hz

    # sliding_window_view liefert alle n-n_fft+1 moeglichen Fenster als
    # (ueberlappende) View ohne Kopie; [::hop] waehlt daraus die
    # tatsaechlich gebrauchten Zeilen - identische Zeilenzahl wie
    # 1 + (n - n_fft) // hop (siehe Moduldoku Blocklogik/Carry).
    windows = np.lib.stride_tricks.sliding_window_view(samples, n_fft)[::hop]
    hann = np.hanning(n_fft)
    if is_complex:
        spectrum = np.fft.fftshift(np.fft.fft(windows * hann, axis=1), axes=1)
    else:
        spectrum = np.fft.rfft(windows * hann, axis=1)
    power = np.abs(spectrum) ** 2
    db = (10.0 * np.log10(power + _EPS)).astype(np.float32)
    return db, dt_row_s, df_hz


# ---------------------------------------------------------------------------
# Interne Helfer
# ---------------------------------------------------------------------------

def _check_not_running(wav_path: str) -> None:
    """Wirft AudioSpectrumBusyError, wenn das Sidecar existiert, lesbar
    ist UND running=True traegt (T-1109). Fehlendes/unlesbares Sidecar
    ist KEIN Fehler - Absturzrest-WAVs bleiben analysierbar (Moduldoku)."""
    sidecar_path = wav_path + ".json"
    if not os.path.isfile(sidecar_path):
        return
    try:
        with open(sidecar_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(meta, dict) and bool(meta.get("running", False)):
        raise AudioSpectrumBusyError(f"recording still in progress: {wav_path}")


def wav_kind(channels: int, sampwidth: int) -> str:
    """Betriebsart aus dem WAV-Kopf (T-914): KIND_MONO fuer 1 Kanal/16 Bit,
    KIND_IQ fuer 2 Kanaele/16 Bit (Kanal 0 = I, Kanal 1 = Q). Alles andere
    wirft AudioSpectrumUnsupportedError. Ersetzt das fruehere
    _check_supported(), das Stereo pauschal abgelehnt hat."""
    if sampwidth == 2 and channels == 1:
        return KIND_MONO
    if sampwidth == 2 and channels == 2:
        return KIND_IQ
    raise AudioSpectrumUnsupportedError(
        f"unsupported WAV format: {channels} channel(s), "
        f"{sampwidth * 8}-bit (supported are 1 channel / 16 bit = mono "
        f"and 2 channels / 16 bit = IQ)"
    )


def n_bins_for(kind: str, n_fft: int) -> int:
    """Bins je Zeile: IQ hat das doppelte Band (zweiseitig), mono das
    einseitige rfft-Raster."""
    return n_fft if kind == KIND_IQ else n_fft // 2 + 1


def band_hz(kind: str, sample_rate_hz: int, n_fft: int):
    """(f_min_hz, f_max_hz) als MITTENFREQUENZEN von Bin 0 und Bin
    n_bins-1 - siehe Moduldoku. Fuer IQ ist die obere Kante um genau ein
    Bin kleiner als +Nyquist (die Bin-Mitten liegen bei (b - n_fft//2)*df)."""
    df = sample_rate_hz / n_fft if n_fft else 0.0
    if kind == KIND_IQ:
        return -sample_rate_hz / 2.0, sample_rate_hz / 2.0 - df
    return 0.0, sample_rate_hz / 2.0


def _predict_rows_raw(n_frames: int, n_fft: int, hop: int) -> int:
    """Zeilenzahl der vollen (nicht dezimierten) STFT-Matrix, BEVOR ein
    Byte gelesen wird: 1 + (n_frames - n_fft) // hop, sonst 0 - genau die
    Zeilenzahl, die die blockweise Verarbeitung mit Ueberlapp-Carry
    liefert (siehe Moduldoku Blocklogik). Erst dadurch steht die
    Dezimierung schon vor dem Lesedurchlauf fest (T-1212)."""
    if n_frames < n_fft or hop <= 0:
        return 0
    return 1 + (n_frames - n_fft) // hop


class _MaxHoldRows:
    """Max-Hold-Dezimierung IM DURCHLAUF (T-1212, loest _decimate_max ab).

    Gruppengroesse k und Zielzeilenzahl stehen aus _predict_rows_raw()
    schon vor dem Lesen fest; jede eintreffende Rohzeile wird sofort in
    ihre Zielzeile (Rohindex // k) hineinmaximiert. Damit liegt nie die
    volle Rohmatrix im Speicher, sondern nur MAX_ROWS * n_bins float32.

    Semantik identisch zum frueheren _decimate_max (Muster
    analyze_sessions.decimate_max_pool): Gruppen zu je k aufeinander-
    folgenden Rohzeilen, pro Bin das MAXIMUM - NICHT der Mittelwert,
    damit kurze Laute beim Verkleinern nicht verschwinden; die letzte
    Gruppe darf kuerzer sein. Bei rows_raw <= target_rows ist k = 1 und
    das Ergebnis die unveraenderte Rohmatrix.

    push() darf mit beliebig geschnittenen Teilmatrizen gefuettert
    werden - Gruppengrenzen duerfen mitten in einem Block liegen.
    """

    def __init__(self, rows_raw: int, n_bins: int, target_rows: int):
        if rows_raw <= 0:
            self.k = 1
            rows_out = 0
        elif rows_raw <= target_rows:
            self.k = 1
            rows_out = rows_raw
        else:
            self.k = -(-rows_raw // target_rows)  # ceil(rows_raw / target_rows)
            rows_out = -(-rows_raw // self.k)
        self.out = np.full((rows_out, n_bins), -np.inf, dtype=np.float32)
        self.n_raw = 0

    def push(self, chunk) -> None:
        """Uebernimmt Rohzeilen in Lesereihenfolge (float32-Matrix)."""
        m = int(chunk.shape[0])
        if m == 0 or self.out.shape[0] == 0:
            self.n_raw += m
            return
        k = self.k
        last = self.out.shape[0] - 1
        pos = 0
        off = self.n_raw % k
        if off:                                   # angebrochene Gruppe auffuellen
            take = min(k - off, m)
            g = min(self.n_raw // k, last)
            np.maximum(self.out[g], chunk[:take].max(axis=0), out=self.out[g])
            pos += take
            self.n_raw += take
        full = (m - pos) // k
        if full:                                  # vollstaendige Gruppen am Stueck
            g0 = min(self.n_raw // k, last)
            n_take = min(full, last + 1 - g0)
            if n_take:
                grouped = chunk[pos:pos + n_take * k].reshape(n_take, k, -1).max(axis=1)
                np.maximum(self.out[g0:g0 + n_take], grouped,
                            out=self.out[g0:g0 + n_take])
            pos += full * k
            self.n_raw += full * k
        rest = m - pos
        if rest:                                  # angebrochene Gruppe am Ende
            g = min(self.n_raw // k, last)
            np.maximum(self.out[g], chunk[pos:].max(axis=0), out=self.out[g])
            self.n_raw += rest

    def result(self):
        """(dezimierte float32-Matrix, rows_raw, decimation). Kamen weniger
        Rohzeilen als vorhergesagt (verkuerzte Datei), werden die
        ueberzaehligen Zielzeilen abgeschnitten - rows passt damit immer
        zu rows_raw."""
        rows_out = -(-self.n_raw // self.k) if self.n_raw else 0
        return self.out[:rows_out], self.n_raw, self.k


def _quantize(matrix, db_min: float, db_max: float):
    """Skaliert eine float-dB-Matrix linear auf uint8 0..255 zwischen
    db_min..db_max, Werte ausserhalb werden geclippt."""
    if matrix.size == 0:
        return matrix.astype(np.uint8)
    span = db_max - db_min
    scale = 255.0 / span if span > 0 else 0.0
    scaled = (matrix.astype(np.float64) - db_min) * scale
    return np.clip(np.round(scaled), 0, 255).astype(np.uint8)


def _robust_scale(values):
    """1./99,9.-Perzentil als (db_min, db_max); degeneriert (z. B. reine
    Stille -> ueberall derselbe Wert) faengt +1.0 dB Spanne ab, damit
    _quantize() nicht durch Null teilt.

    Bleibt der Weg fuer stft_window() (ein begrenztes Zeitfenster liegt
    ohnehin als Matrix vor); build_audspec() nutzt seit T-1212 statt-
    dessen _robust_scale_hist()."""
    if values.size == 0:
        return 0.0, 1.0
    db_min = float(np.percentile(values, 1.0))
    db_max = float(np.percentile(values, 99.9))
    if db_max <= db_min:
        db_max = db_min + 1.0
    return db_min, db_max


def _hist_accumulate(counts, values) -> None:
    """Zaehlt dB-Werte in counts (Laenge _HIST_BINS) ein - Grundlage der
    globalen Skala, ohne alle Rohwerte zu sammeln (T-1212). Werte
    ausserhalb _HIST_DB_MIN.._HIST_DB_MAX landen in der jeweiligen
    Randklasse; wegen der analytischen Grenzen (siehe Konstanten) kann
    das praktisch nicht vorkommen."""
    if values.size == 0:
        return
    scale = _HIST_BINS / (_HIST_DB_MAX - _HIST_DB_MIN)
    idx = ((values.ravel().astype(np.float64) - _HIST_DB_MIN) * scale).astype(np.int64)
    np.clip(idx, 0, _HIST_BINS - 1, out=idx)
    counts += np.bincount(idx, minlength=_HIST_BINS)


def _robust_scale_hist(counts):
    """1./99,9.-Perzentil aus dem Histogramm (gleiche Aussage wie
    _robust_scale, nur ohne die Rohwerte im Speicher). Rueckgabe sind
    Klassenmitten, der Fehler bleibt damit unter einer halben Klassen-
    breite (0,025 dB). Degeneriert (alles in einer Klasse, z. B. reine
    Stille) faengt +1.0 dB Spanne ab."""
    total = int(counts.sum())
    if total == 0:
        return 0.0, 1.0
    width = (_HIST_DB_MAX - _HIST_DB_MIN) / _HIST_BINS
    cum = np.cumsum(counts)
    lo_i = min(int(np.searchsorted(cum, total * 0.01, side="left")), _HIST_BINS - 1)
    hi_i = min(int(np.searchsorted(cum, total * 0.999, side="left")), _HIST_BINS - 1)
    db_min = _HIST_DB_MIN + (lo_i + 0.5) * width
    db_max = _HIST_DB_MIN + (hi_i + 0.5) * width
    if db_max <= db_min:
        db_max = db_min + 1.0
    return float(db_min), float(db_max)


def _frames_to_samples(raw: bytes, kind: str):
    """Rohe 16-Bit-Frames -> Samplereihe fuer stft_db(): mono reell,
    IQ komplex aus Kanal 0 (I) und Kanal 1 (Q). Ungerade Restbytes eines
    IQ-Blocks koennen nicht auftreten (readframes liefert ganze Frames)."""
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if kind == KIND_IQ:
        pairs = pcm.reshape(-1, 2)
        return pairs[:, 0] + 1j * pairs[:, 1]
    return pcm


def _stft_stream(wf: wave.Wave_read, sample_rate_hz: int, n_frames: int,
                  kind: str = KIND_MONO, progress_cb=None):
    """EIN Lesedurchlauf ueber die gesamte, bereits geoeffnete WAV fuer
    ALLE FFT_SIZES (T-1212, loest _stft_full ab - das las die Datei
    einmal je Groesse und hielt jede Rohmatrix vollstaendig im Speicher).

    Je Block werden alle fuenf STFTs gerechnet und sofort in ihren
    _MaxHoldRows-Sammler gegeben; parallel wandern dieselben Rohwerte in
    das Histogramm fuer die globale Skala. Nach dem Durchlauf existiert
    KEINE volle Rohmatrix mehr - nur die dezimierten Matrizen und das
    Histogramm.

    Jede FFT-Groesse fuehrt ihren eigenen Ueberlapp-Carry (n_fft - hop
    Samples), weil hop von n_fft abhaengt: inhaltlich dieselbe Blocklogik
    wie zuvor, nur fuenffach nebeneinander statt fuenfmal nacheinander.

    progress_cb(done_frames, total_frames) wird - falls uebergeben - nach
    jedem gelesenen Block aufgerufen (Konsument folgt mit T-1213).

    Liefert (state, counts) mit state[n_fft] = {"acc", "dt_row_s",
    "df_hz", "hop"} und counts als int64-Histogramm der Laenge
    _HIST_BINS.
    """
    dtype = np.complex128 if kind == KIND_IQ else np.float64
    state = {}
    for n_fft in FFT_SIZES:
        hop = n_fft // 2
        state[n_fft] = {
            "hop": hop,
            "carry": np.empty(0, dtype=dtype),
            "acc": _MaxHoldRows(_predict_rows_raw(n_frames, n_fft, hop),
                                 n_bins_for(kind, n_fft), MAX_ROWS),
            "dt_row_s": hop / sample_rate_hz if sample_rate_hz else 0.0,
            "df_hz": sample_rate_hz / n_fft if n_fft else 0.0,
        }
    counts = np.zeros(_HIST_BINS, dtype=np.int64)

    wf.setpos(0)
    done = 0
    while True:
        raw = wf.readframes(_BLOCK_FRAMES)
        if not raw:
            break
        block = _frames_to_samples(raw, kind)
        done += int(block.size)
        for n_fft in FFT_SIZES:
            st = state[n_fft]
            carry = st["carry"]
            samples = np.concatenate([carry, block]) if carry.size else block
            db_chunk, _, _ = stft_db(samples, sample_rate_hz, n_fft, st["hop"])
            n_rows = db_chunk.shape[0]
            if n_rows:
                _hist_accumulate(counts, db_chunk)
                st["acc"].push(db_chunk)
                # .copy(): sonst haelt der Carry als View den ganzen Block
                # am Leben - genau das soll dieser Umbau vermeiden.
                st["carry"] = samples[n_rows * st["hop"]:].copy()
            else:
                st["carry"] = samples.copy()
        if progress_cb is not None:
            progress_cb(min(done, n_frames), n_frames)
    return state, counts


# ---------------------------------------------------------------------------
# Cache bauen/laden
# ---------------------------------------------------------------------------

# Stichprobe fuer level_median_dbfs(). 4096 Frames sind bei 16 kHz 256 ms und
# bei 32 kHz 128 ms - lang genug, dass ein Block nicht in eine einzelne
# Schwingung faellt, kurz genug, dass Tastpausen als eigene Bloecke auftauchen.
# 512 Bloecke sind hoechstens 2 M Frames, also unabhaengig von der Dateilaenge
# ein Zehntelsekunden-Geschaeft.
LEVEL_MEDIAN_BLOCK_FRAMES = 4096
LEVEL_MEDIAN_MAX_BLOCKS = 512


def level_median_dbfs(wav_path: str, block_frames: int = LEVEL_MEDIAN_BLOCK_FRAMES,
                      max_blocks: int = LEVEL_MEDIAN_MAX_BLOCKS):
    """
    Median des Blockpegels der Aufnahme in dBFS, oder None.

    BEZUG IST DIE VOLLAUSSTEUERUNG DER DATEI, NICHT dBµV. Der Rueckgabewert
    ist 10*log10(mittlere Leistung) je Block, Median ueber die Bloecke - bei
    mono ueber die reellen Samples, bei IQ ueber |I + jQ|. Ein Vollausschlag-
    Sinus liegt damit bei -3 dBFS, ein vollausgesteuerter IQ-Traeger bei
    0 dBFS. Eine Umrechnung in dBµV ist bewusst NICHT eingebaut: das Geraet
    liefert im Audio-/IQ-Stream keinen Pegel mit (Geraeteprofil, kein
    OptionalHeader), und eine gepruefte PCM→dBµV-Bruecke gibt es nicht
    (T-919). Bei AGC-Aufnahmen kann es sie auch nicht geben.

    Gelesen wird stichprobenweise: hoechstens max_blocks gleichmaessig ueber
    die Datei verteilte Bloecke zu block_frames Frames, per setpos()
    angesprungen. Damit haengt die Laufzeit an der Stichprobe, nicht an der
    Dateilaenge.

    None bei leerer Datei oder einer Datei, die kuerzer als ein Block ist.
    Ein unbrauchbares Format wirft dagegen AudioSpectrumUnsupportedError aus
    wav_kind(): was brauchbar ist, entscheidet im Modul genau eine Stelle,
    und die wirft. Ein zweiter, stiller Pfad daneben waere Doppelspurigkeit,
    die spaeter auseinanderlaeuft - der urspruenglich vorgesehene Rueckfall
    auf None war toter Code (Befund T-1626a). Dateisystem- und WAV-Fehler
    werden ebenfalls NICHT geschluckt; der Aufrufer entscheidet (build_meta
    in analyze_audio.py bildet beides auf None ab).
    """
    with wave.open(wav_path, "rb") as wf:
        kind = wav_kind(wf.getnchannels(), wf.getsampwidth())
        n_frames = int(wf.getnframes())
        block = min(int(block_frames), n_frames)
        if block <= 0:
            return None
        n_total = n_frames // block
        if n_total <= 0:
            return None
        n_take = min(n_total, int(max_blocks))
        werte = []
        for i in range(n_take):
            start = (i * n_total // n_take) * block
            wf.setpos(start)
            raw = wf.readframes(block)
            if not raw:
                continue
            samples = _frames_to_samples(raw, kind)
            if samples.size == 0:
                continue
            leistung = float(np.mean(np.abs(samples) ** 2))
            werte.append(10.0 * math.log10(max(leistung, _EPS)))
    if not werte:
        return None
    return float(np.median(werte))


def build_audspec(wav_path: str, progress_cb=None) -> dict:
    """
    Berechnet den Audspec-Cache für wav_path und schreibt ihn atomar
    (Temp-Datei + os.replace, Muster eb200_core/job_store.py). Liest die
    WAV seit T-1212 in EINEM Durchlauf für alle FFT-Größen und dezimiert
    dabei im Durchlauf (siehe Moduldoku/_stft_stream). Wirft
    AudioSpectrumBusyError bzw. AudioSpectrumUnsupportedError VOR jeder
    Rechenarbeit (siehe _check_not_running/wav_kind). Gibt den
    geschriebenen Header zurück.

    progress_cb(done_frames, total_frames) wird - falls übergeben - nach
    jedem gelesenen Block aufgerufen. Das Cache-Format bleibt davon
    unberührt ("eb200-audspec-v2", Konsument folgt mit T-1213).
    """
    _check_not_running(wav_path)
    with wave.open(wav_path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        kind = wav_kind(channels, sampwidth)
        sample_rate_hz = wf.getframerate()
        n_frames = wf.getnframes()
        duration_s = n_frames / sample_rate_hz if sample_rate_hz else 0.0

        state, counts = _stft_stream(wf, sample_rate_hz, n_frames, kind, progress_cb)

    # Globale Skala UEBER DIE ROHWERTE ALLER FFT-GROESSEN gemeinsam (siehe
    # Moduldoku) - das Histogramm wurde VOR der Dezimierung gefuellt, damit
    # Kurzpegel nicht durch Max-Hold-Gruppen verwaschen werden, bevor die
    # Skala feststeht.
    db_min, db_max = _robust_scale_hist(counts)

    sections = []
    matrices = []
    offset = 0
    for n_fft in FFT_SIZES:
        st = state[n_fft]
        decimated, rows_raw, decimation = st["acc"].result()
        n_bins = n_bins_for(kind, n_fft)
        quant = _quantize(decimated, db_min, db_max)
        nbytes = int(quant.nbytes)
        sections.append({
            "n_fft": n_fft, "hop": st["hop"], "rows_raw": rows_raw,
            "rows": int(quant.shape[0]), "n_bins": n_bins,
            "dt_row_s": st["dt_row_s"] * decimation, "decimation": decimation,
            "offset": offset, "nbytes": nbytes,
        })
        matrices.append(quant)
        offset += nbytes

    # Ein Band fuer alle Sections: f_min/f_max haengen nur von kind und der
    # Abtastrate ab, nicht von n_fft - die halbe Bin-Breite Unterschied an der
    # oberen IQ-Kante waere im Header je Section Ballast (siehe band_hz).
    f_min_hz, f_max_hz = band_hz(kind, sample_rate_hz, max(FFT_SIZES))
    header = {
        "format": AUDSPEC_FORMAT,
        "kind": kind,
        "f_min_hz": f_min_hz,
        "f_max_hz": f_max_hz,
        "wav": {
            "size": os.path.getsize(wav_path), "mtime": os.path.getmtime(wav_path),
            "sample_rate_hz": sample_rate_hz, "channels": channels,
            "n_frames": n_frames, "duration_s": duration_s,
        },
        "db_min": db_min, "db_max": db_max,
        "sections": sections,
    }

    cache_path = wav_path + ".audspec"
    tmp = cache_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
        for m in matrices:
            f.write(m.tobytes(order="C"))
    os.replace(tmp, cache_path)
    return header


def audspec_header(wav_path: str):
    """Kopfzeile des Caches, falls vorhanden UND gültig - sonst None.

    Genau die Prüfung aus load_audspec(), aber OHNE die Matrizen zu lesen
    (T-1603): der Aufbereitungs-Worker fragt im Minutentakt für jede
    Aufnahme "muss hier etwas getan werden?", und load_audspec() zöge dafür
    jedes Mal bis zu 31 MB ein, nur um festzustellen, dass nichts zu tun
    ist. Geprüft werden format-String, kind, wav.size UND wav.mtime gegen
    die aktuelle Datei sowie die vollständige sections-Menge.
    """
    try:
        with open(wav_path + ".audspec", "rb") as f:
            header, _ = _read_header(f, wav_path)
            return header
    except (OSError, ValueError, KeyError):
        return None


def _read_header(f, wav_path: str):
    """(header, base) aus einem geöffneten Cache lesen und prüfen, oder
    (None, 0). base ist die Position direkt nach der Header-Zeile, auf die
    sich die section-offsets beziehen."""
    header_line = f.readline()
    if not header_line:
        return None, 0
    try:
        header = json.loads(header_line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, 0
    if not isinstance(header, dict) or header.get("format") != AUDSPEC_FORMAT:
        return None, 0
    # T-914: Bestands-Caches im v1-Format fallen ueber den format-String
    # heraus und werden von ensure_audspec() neu gebaut (regenerierbar).
    if header.get("kind") not in (KIND_MONO, KIND_IQ):
        return None, 0
    wav_meta = header.get("wav")
    if not isinstance(wav_meta, dict):
        return None, 0
    try:
        actual_size = os.path.getsize(wav_path)
        actual_mtime = os.path.getmtime(wav_path)
    except OSError:
        return None, 0
    if wav_meta.get("size") != actual_size:
        return None, 0
    try:
        if abs(float(wav_meta.get("mtime")) - actual_mtime) > 1e-6:
            return None, 0
    except (TypeError, ValueError):
        return None, 0

    # T-1208: sections muss GENAU die aktuelle FFT_SIZES-Menge abdecken -
    # ein Bestands-Cache mit weniger Sektionen (z. B. nur 512/1024/2048 vor
    # Einfuehrung von 128/256) gilt als veraltet, ensure_audspec() baut ihn
    # dann neu (automatische Migration). Pruefung VOR den Matrizen.
    section_ffts = {sec["n_fft"] for sec in header.get("sections", [])}
    if section_ffts != set(FFT_SIZES):
        return None, 0
    return header, f.tell()


def load_audspec(wav_path: str):
    """
    Lädt den Audspec-Cache, falls vorhanden UND gültig (Prüfung siehe
    audspec_header). Gibt (header, {n_fft: uint8-Matrix}) zurück oder None.
    """
    try:
        with open(wav_path + ".audspec", "rb") as f:
            header, base = _read_header(f, wav_path)
            if header is None:
                return None
            matrices = {}
            for sec in header.get("sections", []):
                n_fft = sec["n_fft"]
                rows = sec["rows"]
                n_bins = sec["n_bins"]
                f.seek(base + sec["offset"])
                blob = f.read(sec["nbytes"])
                if len(blob) != sec["nbytes"]:
                    return None
                matrices[n_fft] = np.frombuffer(blob, dtype=np.uint8).reshape(rows, n_bins)
    except (OSError, ValueError, KeyError):
        return None
    return header, matrices


# ---------------------------------------------------------------------------
# Sperr- und Fortschrittsdatei laufender Builds (T-1601)
# ---------------------------------------------------------------------------
#
# build_audspec() laeuft bei langen Aufnahmen minutenlang; die HTTP-Anfrage,
# die ihn ausgeloest hat, blockiert so lange. Seit T-1601 kann ausserdem der
# Aufbereitungs-Worker denselben Bau angestossen haben. Damit werden zwei
# Dinge gebraucht, die die prozesslokale Registry aus T-1213 nicht leisten
# konnte: der Fortschritt muss PROZESSUEBERGREIFEND sichtbar sein, und zwei
# Prozesse duerfen dieselbe Datei nicht doppelt bauen (das kostete bei der
# langen IQ-Aufnahme sechs Minuten und mehrere hundert MB doppelt).
#
# Beides loest EINE Datei neben der Aufnahme, "<wav>.audspec.lock":
#   - Sie ist die Sperre: angelegt mit O_CREAT|O_EXCL, damit genau ein Lauf
#     gewinnt - atomar auch ueber Prozessgrenzen hinweg, anders als ein Dict
#     im Speicher.
#   - Sie ist zugleich die Fortschrittsdatei: nach jedem gelesenen Block neu
#     geschrieben (Temp + os.replace, Muster build_audspec), mit Phase,
#     Framezahlen und Heartbeat.
#
# Verwaiste Sperren (Absturz, Stromausfall) werden ueber das ALTER des
# Heartbeats erkannt, nicht ueber eine PID-Pruefung: die ist unter Windows
# und Linux verschieden, und der Pi-Port steht an (Meilenstein 15). Ist der
# Heartbeat aelter als LOCK_STALE_S, gilt die Sperre als tot und darf
# uebernommen werden.
#
# Nur ensure_audspec() sperrt. Ein direkter build_audspec()-Aufruf (CLI,
# --rebuild) laeuft ohne Sperre - dort steht die Konsole zur Verfuegung, und
# ein erzwungener Neubau soll sich nicht an seiner eigenen Sperre stossen.

LOCK_SUFFIX = ".audspec.lock"
LOCK_STALE_S = 60.0      # aelterer Heartbeat -> Sperre gilt als verwaist

PHASE_READ = "read"      # Lesedurchlauf laeuft (frac = gelesene Frames)
PHASE_WRITE = "write"    # Durchlauf fertig, Quantisieren/Schreiben laeuft


def lock_path_for(wav_path: str) -> str:
    """Pfad der Sperr-/Fortschrittsdatei - Suffix-Anhaengung an den vollen
    WAV-Pfad wie beim Cache (wav_path + ".audspec") und beim Sidecar."""
    return wav_path + LOCK_SUFFIX


def _lock_read(wav_path: str):
    """Rohinhalt der Sperrdatei als dict - oder None, wenn sie fehlt,
    unlesbar ist oder verwaist (Heartbeat aelter als LOCK_STALE_S)."""
    path = lock_path_for(wav_path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        beat = float(data["heartbeat_epoch"])
    except (KeyError, TypeError, ValueError):
        # Fehlender/kaputter Heartbeat: mtime als Ersatz, damit eine halb
        # geschriebene Datei nicht unbegrenzt als frische Sperre gilt.
        try:
            beat = os.path.getmtime(path)
        except OSError:
            return None
    if time.time() - beat > LOCK_STALE_S:
        return None
    return data


def progress_snapshot(wav_path: str):
    """Momentaufnahme eines laufenden build_audspec() zu wav_path, oder
    None, wenn dort gerade nichts gebaut wird (Cache-Treffer, fertig,
    abgebrochen, verwaiste Sperre). Reine Lesefunktion ohne Seiteneffekt und
    seit T-1601 PROZESSUEBERGREIFEND - der Bau kann im Worker laufen.

    Felder: "phase" (PHASE_READ/PHASE_WRITE - ASCII und sprachneutral, den
    Anzeigetext bildet die Oberflaeche), "done_frames", "total_frames",
    "frac" (0.0..1.0), "elapsed_s" und "pid" des bauenden Prozesses.
    """
    data = _lock_read(wav_path)
    if data is None:
        return None
    try:
        started = float(data.get("started_epoch") or 0.0)
        return {
            "phase": str(data.get("phase") or PHASE_READ),
            "done_frames": int(data.get("done_frames") or 0),
            "total_frames": int(data.get("total_frames") or 0),
            "frac": float(data.get("frac") or 0.0),
            "elapsed_s": max(0.0, time.time() - started) if started else 0.0,
            "pid": int(data.get("pid") or 0),
        }
    except (TypeError, ValueError):
        # Unsinnige Werte in der Sperrdatei: wie "kein Bau" behandeln, nicht
        # den Aufrufer mit einer Ausnahme belasten (reine Lesefunktion).
        return None


class _BuildLock:
    """Haelt die Sperrdatei fuer die Dauer eines Baulaufs und schreibt den
    Fortschritt hinein.

    heartbeat() passt genau auf die progress_cb-Signatur aus T-1212
    (done_frames, total_frames) und wird unveraendert dorthin gereicht -
    build_audspec() weiss von der Sperre nichts und bleibt unangetastet.
    """

    def __init__(self, wav_path: str, fd: int):
        self.wav_path = wav_path
        self.path = lock_path_for(wav_path)
        self.started = time.time()
        os.close(fd)                     # nur die Belegung zaehlte
        self._write(PHASE_READ, 0, 0, 0.0)

    def _write(self, phase: str, done: int, total: int, frac: float) -> None:
        payload = {
            "pid": os.getpid(), "phase": phase,
            "done_frames": int(done), "total_frames": int(total),
            "frac": float(frac), "started_epoch": self.started,
            "heartbeat_epoch": time.time(),
        }
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self.path)
        except OSError:
            # Ein misslungener Heartbeat darf den Bau NICHT abbrechen - der
            # Cache ist wichtiger als seine Fortschrittsanzeige. Die Sperre
            # altert dann und wird notfalls uebernommen.
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def heartbeat(self, done_frames: int, total_frames: int) -> None:
        frac = (done_frames / total_frames) if total_frames else 0.0
        frac = min(1.0, max(0.0, frac))
        self._write(PHASE_WRITE if frac >= 1.0 else PHASE_READ,
                    done_frames, total_frames, frac)

    def release(self) -> None:
        """Sperre freigeben - immer aus einem finally-Zweig heraus, damit
        auch ein Fehlschlag keine Sperre hinterlaesst."""
        for path in (self.path + ".tmp", self.path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _lock_acquire(wav_path: str) -> "_BuildLock":
    """Sperre belegen. Haelt sie bereits ein anderer, LEBENDER Lauf, wirft
    die Funktion AudioSpectrumBuildingError samt dessen Schnappschuss. Eine
    verwaiste Sperre wird entfernt und uebernommen - genau EIN Nachfass-
    versuch, damit ein Wettlauf zweier Uebernehmer nicht in einer Schleife
    endet, sondern der Zweite sauber mit BuildingError herauskommt."""
    path = lock_path_for(wav_path)
    for attempt in (0, 1):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            snap = progress_snapshot(wav_path)
            if snap is not None:
                raise AudioSpectrumBuildingError(
                    f"spectrogram is already being computed: {wav_path}", snap)
            if attempt == 0:
                try:
                    os.unlink(path)          # verwaist -> uebernehmen
                except OSError:
                    pass
                continue
            raise AudioSpectrumBuildingError(
                f"lock file cannot be acquired: {path}", None)
        except OSError as exc:
            raise AudioSpectrumBuildingError(
                f"lock file cannot be created: {path} ({exc})", None)
        return _BuildLock(wav_path, fd)


def ensure_audspec(wav_path: str):
    """load_audspec(); bei ungültigem/fehlendem Cache build_audspec() +
    erneutes load_audspec() (das frisch Geschriebene muss laden).

    Der Baulauf haelt seit T-1601 die Sperr-/Fortschrittsdatei
    (lock_path_for(), progress_snapshot()). Ein Cache-Treffer belegt sie
    NICHT - die Oberflaeche soll keinen Balken fuer etwas zeigen, das gar
    nicht stattfindet. Baut bereits ein anderer Prozess (Aufbereitungs-
    Worker, zweiter Viewer), wirft die Funktion AudioSpectrumBuildingError,
    statt denselben Bau ein zweites Mal zu starten. Der finally-Zweig gibt
    die Sperre auch bei Busy/Unsupported/Lesefehler wieder frei.
    """
    result = load_audspec(wav_path)
    if result is not None:
        return result
    lock = _lock_acquire(wav_path)
    try:
        build_audspec(wav_path, progress_cb=lock.heartbeat)
    finally:
        lock.release()
    return load_audspec(wav_path)


# ---------------------------------------------------------------------------
# Zeitfenster in voller Auflösung (Drill-down, kein Max-Hold)
# ---------------------------------------------------------------------------

def stft_window(wav_path: str, t0_s: float, t1_s: float, n_fft: int,
                 db_min: float | None = None, db_max: float | None = None) -> dict:
    """
    STFT-Matrix eines Zeitfensters [t0_s, t1_s) in VOLLER Auflösung (kein
    Max-Hold) - Drill-down fuer den Viewer (T-1202). Seekt per
    wave.setpos direkt zur passenden Frame-Position statt die Datei ab
    Anfang zu lesen. db_min/db_max: wenn übergeben (T-1202 reicht i. d. R.
    die Cache-Skala durch), werden sie verwendet; sonst wird fensterlokal
    robust skaliert (siehe _robust_scale).

    t0_s/t1_s werden auf die Dateidauer geclippt; liegt das Fenster
    komplett ausserhalb (z. B. t0_s > Dateidauer), kommt eine leere
    Matrix zurück statt eines Fehlers. Mehr als MAX_WINDOW_ROWS Zeilen
    (bei feiner Aufloesung schnell erreicht) -> ValueError, BEVOR
    gelesen wird (teure Operation vermeiden).
    """
    _check_not_running(wav_path)
    hop = n_fft // 2
    with wave.open(wav_path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        kind = wav_kind(channels, sampwidth)
        sample_rate_hz = wf.getframerate()
        n_frames_total = wf.getnframes()
        file_duration_s = n_frames_total / sample_rate_hz if sample_rate_hz else 0.0

        t0_s = max(0.0, min(t0_s, file_duration_s))
        t1_s = max(t0_s, min(t1_s, file_duration_s))

        start_frame = int(t0_s * sample_rate_hz)
        end_frame = min(n_frames_total, int(math.ceil(t1_s * sample_rate_hz)) + n_fft)
        n_read = max(0, end_frame - start_frame)

        n_rows_est = 1 + (n_read - n_fft) // hop if n_read >= n_fft else 0
        if n_rows_est > MAX_WINDOW_ROWS:
            raise ValueError(
                f"time range too large: {n_rows_est} rows (> {MAX_WINDOW_ROWS}) "
                f"- select a smaller range or a coarser n_fft"
            )

        wf.setpos(start_frame)
        raw = wf.readframes(n_read)

    samples = _frames_to_samples(raw, kind)
    db, dt_row_s, df_hz = stft_db(samples, sample_rate_hz, n_fft, hop)

    if db_min is None or db_max is None:
        db_min, db_max = _robust_scale(db)

    quant = _quantize(db, db_min, db_max)
    f_min_hz, f_max_hz = band_hz(kind, sample_rate_hz, n_fft)
    return {
        "matrix": quant, "dt_row_s": dt_row_s, "df_hz": df_hz,
        "db_min": db_min, "db_max": db_max, "kind": kind,
        "f_min_hz": f_min_hz, "f_max_hz": f_max_hz,
        "t0_s": start_frame / sample_rate_hz if sample_rate_hz else 0.0,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_overview(wav_path: str, header: dict) -> None:
    wav_meta = header["wav"]
    print(f"Datei: {wav_path}")
    print(f"  {wav_meta['sample_rate_hz']} Hz, {wav_meta['channels']} Kanal(e), "
          f"{wav_meta['n_frames']} Frames, {wav_meta['duration_s']:.2f} s")
    print(f"  Art: {header['kind']}, Band: {header['f_min_hz'] / 1000:.3f} .. "
          f"{header['f_max_hz'] / 1000:.3f} kHz")
    print(f"  Skala: {header['db_min']:.1f} .. {header['db_max']:.1f} dB")
    for sec in header["sections"]:
        print(f"  n_fft={sec['n_fft']:5d}  rows_raw={sec['rows_raw']:7d}  "
              f"rows={sec['rows']:6d}  decimation={sec['decimation']:3d}  "
              f"n_bins={sec['n_bins']:4d}  dt_row_s={sec['dt_row_s']:.4f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Audio-Spektrogramm-Cache (T-1201)")
    ap.add_argument("wav", nargs="?", help="Pfad zu einer .wav-Datei")
    ap.add_argument("--rebuild", action="store_true", help="Cache neu berechnen statt zu laden")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausführen")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    if not args.wav:
        ap.print_help()
        return 1

    try:
        if args.rebuild:
            header = build_audspec(args.wav)
        else:
            header, _ = ensure_audspec(args.wav)
    except (AudioSpectrumBusyError, AudioSpectrumUnsupportedError,
            AudioSpectrumBuildingError) as exc:
        print(f"Fehler: {exc}")
        return 1

    _print_overview(args.wav, header)
    return 0


# ---------------------------------------------------------------------------
# Selbsttest (python audio_spectrum.py --selftest, ohne Hardware)
# ---------------------------------------------------------------------------

def _write_test_wav(path: str, samples_f32, sample_rate_hz: int, channels: int = 1) -> None:
    """Schreibt samples_f32 (-1..1, float) als 16-Bit-PCM-WAV - reiner
    Testhelfer, importiert bewusst NICHT eb200_core/audio_wav.py (dieses
    Modul soll unabhaengig von eb200_core pruefbar bleiben)."""
    pcm = np.clip(np.asarray(samples_f32) * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(pcm.tobytes())


def _write_test_iq_wav(path: str, samples_complex, sample_rate_hz: int) -> None:
    """Schreibt eine komplexe Samplereihe als 16-Bit-Stereo-WAV,
    Kanal 0 = Realteil (I), Kanal 1 = Imaginaerteil (Q) - genau die
    Ablage von eb200_core/audio_wav.py im IQ-Betrieb (T-901)."""
    z = np.asarray(samples_complex)
    inter = np.empty(z.size * 2, dtype=np.float64)
    inter[0::2] = z.real
    inter[1::2] = z.imag
    pcm = np.clip(inter * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(pcm.tobytes())


def _write_test_sidecar(wav_path: str, running: bool) -> None:
    with open(wav_path + ".json", "w", encoding="utf-8") as f:
        json.dump({"running": running}, f)


def _self_test() -> None:
    import tempfile
    import time

    with tempfile.TemporaryDirectory() as tmp:
        sample_rate_hz = 16000

        # --- T1: Zweiton-WAV, 10s, 1000 Hz + 3000 Hz --------------------------
        duration_s = 10.0
        n = int(sample_rate_hz * duration_s)
        t = np.arange(n) / sample_rate_hz
        two_tone = 0.3 * np.sin(2 * np.pi * 1000.0 * t) + 0.3 * np.sin(2 * np.pi * 3000.0 * t)
        wav_path = os.path.join(tmp, "two_tone.wav")
        _write_test_wav(wav_path, two_tone, sample_rate_hz)
        _write_test_sidecar(wav_path, running=False)

        header = build_audspec(wav_path)
        assert header["format"] == AUDSPEC_FORMAT, header
        assert header["kind"] == KIND_MONO, header
        assert (header["f_min_hz"], header["f_max_hz"]) == (0.0, sample_rate_hz / 2), header
        assert len(header["sections"]) == len(FFT_SIZES), header["sections"]
        for sec in header["sections"]:
            # T-1618: mit n_fft=32 liefert die kurze Testdatei mehr als MAX_ROWS
            # Rohzeilen, dort greift die Dezimierung. Geprueft wird deshalb der
            # Zusammenhang, nicht mehr "keine Dezimierung".
            assert sec["rows"] == -(-sec["rows_raw"] // sec["decimation"]), sec
            assert sec["rows"] <= MAX_ROWS, sec

        loaded = load_audspec(wav_path)
        assert loaded is not None
        loaded_header, matrices = loaded
        assert set(matrices.keys()) == set(FFT_SIZES), matrices.keys()

        # Peak-Bins in der Uebersicht UND per stft_window pruefen.
        for n_fft in FFT_SIZES:
            n_bins = n_fft // 2 + 1
            bin1 = round(1000.0 / (sample_rate_hz / n_fft))
            bin3 = round(3000.0 / (sample_rate_hz / n_fft))
            mat = matrices[n_fft]
            assert mat.shape[1] == n_bins, (n_fft, mat.shape)
            mid_row = mat[mat.shape[0] // 2].astype(np.float64)
            med = np.median(mid_row)
            peak1 = mid_row[max(0, bin1 - 1):bin1 + 2].max()
            peak3 = mid_row[max(0, bin3 - 1):bin3 + 2].max()
            assert peak1 > med + 20, (n_fft, "peak1", peak1, med)
            assert peak3 > med + 20, (n_fft, "peak3", peak3, med)

            win = stft_window(wav_path, 2.0, 4.0, n_fft)
            wmat = win["matrix"]
            assert wmat.shape[0] > 0, (n_fft, wmat.shape)
            wmid = wmat[wmat.shape[0] // 2].astype(np.float64)
            wmed = np.median(wmid)
            wpeak1 = wmid[max(0, bin1 - 1):bin1 + 2].max()
            wpeak3 = wmid[max(0, bin3 - 1):bin3 + 2].max()
            assert wpeak1 > wmed + 20, (n_fft, "stft_window peak1", wpeak1, wmed)
            assert wpeak3 > wmed + 20, (n_fft, "stft_window peak3", wpeak3, wmed)
        print(f"T1 ok  (Zweiton-WAV: {len(FFT_SIZES)} Sections, keine Dezimierung, "
              "Peaks in Uebersicht+stft_window)")

        # --- T2: Roundtrip + Cache-Invalidierung per mtime ----------------------
        assert loaded_header["db_min"] < loaded_header["db_max"]
        for n_fft in FFT_SIZES:
            sec = next(s for s in loaded_header["sections"] if s["n_fft"] == n_fft)
            assert matrices[n_fft].shape == (sec["rows"], sec["n_bins"]), (n_fft, matrices[n_fft].shape, sec)

        future = time.time() + 5
        os.utime(wav_path, (future, future))
        assert load_audspec(wav_path) is None, "Cache haette durch veraenderte mtime ungueltig werden muessen"
        rebuilt_header, rebuilt_matrices = ensure_audspec(wav_path)
        assert rebuilt_header["wav"]["mtime"] == os.path.getmtime(wav_path)
        assert set(rebuilt_matrices.keys()) == set(FFT_SIZES)
        print("T2 ok  (Roundtrip; mtime-Aenderung invalidiert Cache; ensure_audspec baut neu)")

        # --- T3: Dezimierung bei langer (stiller) WAV ---------------------------
        long_duration_s = 600.0
        n_long = int(sample_rate_hz * long_duration_s)
        silence = np.zeros(n_long, dtype=np.float32)
        long_wav_path = os.path.join(tmp, "long_silence.wav")
        _write_test_wav(long_wav_path, silence, sample_rate_hz)
        _write_test_sidecar(long_wav_path, running=False)

        long_header = build_audspec(long_wav_path)
        for sec in long_header["sections"]:
            assert sec["rows_raw"] > MAX_ROWS, sec
            assert sec["rows"] <= MAX_ROWS, sec
            assert sec["decimation"] == -(-sec["rows_raw"] // MAX_ROWS), sec
        print("T3 ok  (600s-WAV: rows_raw > 8000 in allen Sections, rows <= 8000, decimation korrekt)")

        # --- T4: running=True im Sidecar -> AudioSpectrumBusyError -------------
        _write_test_sidecar(long_wav_path, running=True)
        try:
            build_audspec(long_wav_path)
        except AudioSpectrumBusyError:
            pass
        else:
            raise AssertionError("AudioSpectrumBusyError erwartet (running=True)")
        try:
            stft_window(long_wav_path, 0.0, 1.0, 512)
        except AudioSpectrumBusyError:
            pass
        else:
            raise AssertionError("AudioSpectrumBusyError erwartet (stft_window, running=True)")
        _write_test_sidecar(long_wav_path, running=False)
        print("T4 ok  (running=True -> AudioSpectrumBusyError in build_audspec UND stft_window)")

        # --- T5: 3-Kanal-WAV -> AudioSpectrumUnsupportedError ------------------
        # (Stereo ist seit T-914 der IQ-Fall und damit KEIN Fehler mehr -
        # geprueft wird jetzt die naechste unzulaessige Kanalzahl.)
        multi_path = os.path.join(tmp, "dreikanal.wav")
        multi_samples = np.zeros(sample_rate_hz, dtype=np.float32)
        _write_test_wav(multi_path, np.repeat(multi_samples, 3), sample_rate_hz, channels=3)
        try:
            build_audspec(multi_path)
        except AudioSpectrumUnsupportedError:
            pass
        else:
            raise AssertionError("AudioSpectrumUnsupportedError erwartet (3 Kanaele)")
        assert wav_kind(1, 2) == KIND_MONO and wav_kind(2, 2) == KIND_IQ
        print("T5 ok  (3-Kanal-WAV -> AudioSpectrumUnsupportedError; wav_kind mono/iq)")

        # --- T6: stft_window ausserhalb der Dateidauer / Zeilenlimit -----------
        outside = stft_window(wav_path, 100.0, 105.0, 512)  # WAV ist nur 10s lang
        assert outside["matrix"].shape[0] == 0, outside["matrix"].shape
        try:
            stft_window(long_wav_path, 0.0, 400.0, 512)  # >20000 Zeilen bei n_fft=512
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet (Zeitfenster > 20000 Zeilen)")
        print("T6 ok  (Fenster ausserhalb Dateidauer -> leer ohne Absturz; zu grosses Fenster -> ValueError)")

        # --- T7: Cache-Migration (T-1208) - Bestands-Cache mit nur 3 der 5
        # FFT_SIZES gilt als veraltet, ensure_audspec baut ihn vollstaendig
        # neu. Handkonstruierte .audspec-Datei: gueltige format/size/mtime,
        # sections nur fuer 512/1024/2048 - die Headerzeile reicht (kein
        # Binaerteil), da die Sektionspruefung VOR dem Matrix-Lesen greift.
        cache_path = wav_path + ".audspec"
        stale_header = {
            "format": AUDSPEC_FORMAT, "kind": KIND_MONO,
            "f_min_hz": 0.0, "f_max_hz": sample_rate_hz / 2,
            "wav": {"size": os.path.getsize(wav_path), "mtime": os.path.getmtime(wav_path),
                    "sample_rate_hz": sample_rate_hz, "channels": 1,
                    "n_frames": n, "duration_s": duration_s},
            "db_min": 0.0, "db_max": 1.0,
            "sections": [{"n_fft": nf} for nf in (512, 1024, 2048)],
        }
        with open(cache_path, "wb") as f:
            f.write((json.dumps(stale_header, ensure_ascii=False) + "\n").encode("utf-8"))
        assert load_audspec(wav_path) is None, "Cache mit nur 3 Sections haette ungueltig sein muessen"
        migrated_header, migrated_matrices = ensure_audspec(wav_path)
        assert set(migrated_matrices.keys()) == set(FFT_SIZES), migrated_matrices.keys()
        assert len(migrated_header["sections"]) == len(FFT_SIZES), migrated_header["sections"]
        print("T7 ok  (Cache-Migration: Bestands-Cache mit nur 512/1024/2048 -> load_audspec None, "
              "ensure_audspec baut alle 5 Sections neu)")

        # --- T8: IQ-WAV, zweiseitiges Band, Vorzeichen der Ablage --------------
        # Zwei komplexe Traeger: +5000 Hz und -3000 Hz. Bei richtiger
        # Vorzeichenkonvention (Kanal 0 = I, Kanal 1 = Q) und fftshift liegen
        # die Peaks bei Bin n_fft//2 + f/df - LAEGE Q invertiert vor, waeren
        # beide Peaks gespiegelt; genau das faengt dieser Test ab.
        iq_sr = 32000
        iq_dur_s = 6.0
        n_iq = int(iq_sr * iq_dur_s)
        t_iq = np.arange(n_iq) / iq_sr
        iq = (0.35 * np.exp(2j * np.pi * 5000.0 * t_iq)
              + 0.35 * np.exp(-2j * np.pi * 3000.0 * t_iq))
        iq_path = os.path.join(tmp, "traeger_iq.wav")
        _write_test_iq_wav(iq_path, iq, iq_sr)
        _write_test_sidecar(iq_path, running=False)

        iq_header, iq_matrices = ensure_audspec(iq_path)
        assert iq_header["kind"] == KIND_IQ, iq_header
        assert iq_header["f_min_hz"] == -iq_sr / 2, iq_header
        assert iq_header["f_max_hz"] < iq_sr / 2, iq_header
        for sec in iq_header["sections"]:
            assert sec["n_bins"] == sec["n_fft"], sec
        # T-1618: nur die Groessen mit ausreichender Aufloesung. Die Probe
        # verlangt, dass +5 kHz, -3 kHz und der Spiegel bei -5 kHz getrennt
        # daliegen; bei n_fft=32 (1 kHz je Bin bei 32 kHz Abtastrate) laufen
        # Traeger und Fensterflanke ineinander - das ist kein Fehler, sondern
        # genau die Grenze, die die Kennzeichnung in :8070 sichtbar macht.
        for n_fft in [f for f in FFT_SIZES if iq_sr / f <= 300.0]:
            df = iq_sr / n_fft
            mat = iq_matrices[n_fft]
            assert mat.shape[1] == n_fft, (n_fft, mat.shape)
            mid_row = mat[mat.shape[0] // 2].astype(np.float64)
            med = np.median(mid_row)
            bin_plus = int(round(n_fft // 2 + 5000.0 / df))
            bin_minus = int(round(n_fft // 2 - 3000.0 / df))
            peak_plus = mid_row[bin_plus - 1:bin_plus + 2].max()
            peak_minus = mid_row[bin_minus - 1:bin_minus + 2].max()
            assert peak_plus > med + 20, (n_fft, "peak +5 kHz", peak_plus, med)
            assert peak_minus > med + 20, (n_fft, "peak -3 kHz", peak_minus, med)
            # Spiegelprobe: an der gespiegelten Stelle (-5 kHz) steht nur die
            # Fensterflanke der beiden Traeger, nie ein eigener Peak. Bezug ist
            # bewusst der Peak und nicht der Median - die Hann-Flanke liegt bei
            # n_fft=128 (250 Hz je Bin) rund 20 Zaehler ueber dem Median, ein
            # gespiegelter Traeger laege dagegen beim Peakwert.
            mirror = int(round(n_fft // 2 - 5000.0 / df))
            assert mid_row[mirror - 1:mirror + 2].max() < peak_plus - 80, \
                (n_fft, "Spiegel bei -5 kHz", mid_row[mirror], peak_plus)
        print(f"T8 ok  (IQ-WAV: kind=iq, n_bins=n_fft, Traeger bei +5 kHz und -3 kHz "
              f"seitenrichtig, Band {iq_header['f_min_hz'] / 1000:.1f} .. "
              f"{iq_header['f_max_hz'] / 1000:.1f} kHz)")

        # --- T9: stft_window auf derselben IQ-Datei ---------------------------
        iq_win = stft_window(iq_path, 1.0, 3.0, 1024,
                             db_min=iq_header["db_min"], db_max=iq_header["db_max"])
        assert iq_win["kind"] == KIND_IQ, iq_win["kind"]
        assert iq_win["matrix"].shape[1] == 1024, iq_win["matrix"].shape
        assert iq_win["f_min_hz"] == -iq_sr / 2, iq_win["f_min_hz"]
        wrow = iq_win["matrix"][iq_win["matrix"].shape[0] // 2].astype(np.float64)
        wmed = np.median(wrow)
        wbin = int(round(512 + 5000.0 / (iq_sr / 1024)))
        assert wrow[wbin - 1:wbin + 2].max() > wmed + 20, ("stft_window IQ", wrow[wbin], wmed)
        print("T9 ok  (stft_window auf IQ: kind/Band durchgereicht, Traeger im Zoom wiedergefunden)")

        # --- T10 (T-1212): Streaming-Dezimierung, Histogramm-Skala, Zeilen-
        # vorhersage. Reine Rechenpruefungen ohne Datei - die drei Bausteine
        # ersetzen _decimate_max/_robust_scale im Build-Pfad und muessen
        # dieselben Werte liefern wie die frueheren Ein-Schritt-Varianten.
        rng10 = np.random.default_rng(20261212)
        ref10 = rng10.normal(0.0, 10.0, size=(1234, 17)).astype(np.float32)
        for target10 in (1234, 500, 200, 8, 1):
            acc10 = _MaxHoldRows(1234, 17, target10)
            pos10 = 0
            for step10 in (1, 5, 61, 400, 767, 10000):
                if pos10 >= 1234:
                    break
                end10 = min(1234, pos10 + step10)
                acc10.push(ref10[pos10:end10])
                pos10 = end10
            got10, rows_raw10, k10 = acc10.result()
            assert rows_raw10 == 1234, (target10, rows_raw10)
            assert k10 == (1 if 1234 <= target10 else -(-1234 // target10)), (target10, k10)
            n_full10 = 1234 // k10
            parts10 = []
            if n_full10:
                parts10.append(ref10[:n_full10 * k10].reshape(n_full10, k10, -1).max(axis=1))
            if 1234 - n_full10 * k10:
                parts10.append(ref10[n_full10 * k10:].max(axis=0, keepdims=True))
            want10 = np.concatenate(parts10, axis=0) if len(parts10) > 1 else parts10[0]
            assert got10.shape == want10.shape, (target10, got10.shape, want10.shape)
            assert np.array_equal(got10, want10), (target10, float(np.abs(got10 - want10).max()))
            assert got10.shape[0] <= max(1, target10), (target10, got10.shape)

        vals10 = rng10.normal(-40.0, 12.0, size=200000).astype(np.float32)
        counts10 = np.zeros(_HIST_BINS, dtype=np.int64)
        _hist_accumulate(counts10, vals10[:70000])
        _hist_accumulate(counts10, vals10[70000:])
        assert int(counts10.sum()) == vals10.size, int(counts10.sum())
        hist_min10, hist_max10 = _robust_scale_hist(counts10)
        width10 = (_HIST_DB_MAX - _HIST_DB_MIN) / _HIST_BINS
        assert abs(hist_min10 - float(np.percentile(vals10, 1.0))) <= 2 * width10, hist_min10
        assert abs(hist_max10 - float(np.percentile(vals10, 99.9))) <= 2 * width10, hist_max10
        assert _robust_scale_hist(np.zeros(_HIST_BINS, dtype=np.int64)) == (0.0, 1.0)

        for sec in long_header["sections"]:
            pred10 = _predict_rows_raw(long_header["wav"]["n_frames"], sec["n_fft"], sec["hop"])
            assert pred10 == sec["rows_raw"], (sec["n_fft"], pred10, sec["rows_raw"])
        assert _predict_rows_raw(100, 512, 256) == 0
        print("T10 ok  (_MaxHoldRows deckungsgleich mit der Ein-Schritt-Dezimierung bei "
              "5 Gruppengroessen und wechselnden Blockschnitten; Histogramm-Perzentile "
              f"innerhalb {2 * width10:.2f} dB; _predict_rows_raw trifft rows_raw aller Sections)")

        # --- T11 (T-1601): Sperr- und Fortschrittsdatei -------------------------
        # Geprueft werden Belegen/Freigeben, der Fortschrittsinhalt, die
        # Uebernahme einer verwaisten Sperre und dass ensure_audspec() in
        # BEIDEN Ausgaengen freigibt - nach erfolgreichem Bau wie nach einem
        # Fehler. Alles deterministisch, ohne Thread und ohne zweiten Prozess.
        lock_file = lock_path_for(wav_path)
        assert progress_snapshot(wav_path) is None, "Sperre sollte fehlen"
        assert not os.path.exists(lock_file), lock_file

        lock11 = _lock_acquire(wav_path)
        assert os.path.isfile(lock_file), "Sperrdatei nicht angelegt"
        snap11 = progress_snapshot(wav_path)
        assert snap11 is not None and snap11["phase"] == PHASE_READ, snap11
        assert snap11["frac"] == 0.0 and snap11["pid"] == os.getpid(), snap11
        assert snap11["elapsed_s"] >= 0.0, snap11

        lock11.heartbeat(250, 1000)
        snap11 = progress_snapshot(wav_path)
        assert snap11["frac"] == 0.25 and snap11["done_frames"] == 250, snap11
        assert snap11["total_frames"] == 1000 and snap11["phase"] == PHASE_READ, snap11
        lock11.heartbeat(1000, 1000)
        assert progress_snapshot(wav_path)["phase"] == PHASE_WRITE, progress_snapshot(wav_path)

        # Zweiter Belegungsversuch auf eine LEBENDE Sperre -> BuildingError
        # mit Schnappschuss (genau der Fall Worker gegen Viewer).
        try:
            _lock_acquire(wav_path)
        except AudioSpectrumBuildingError as exc11:
            assert exc11.snapshot is not None and exc11.snapshot["phase"] == PHASE_WRITE, exc11.snapshot
        else:
            raise AssertionError("AudioSpectrumBuildingError erwartet (Sperre belegt)")
        # ensure_audspec() darf bei belegter Sperre NICHT selbst bauen. Der
        # Cache muss dafuer weg sein, sonst greift der Cache-Treffer davor.
        os.remove(wav_path + ".audspec")
        try:
            ensure_audspec(wav_path)
        except AudioSpectrumBuildingError:
            pass
        else:
            raise AssertionError("ensure_audspec haette BuildingError werfen muessen")

        lock11.release()
        assert not os.path.exists(lock_file), "Sperre nicht freigegeben"
        assert progress_snapshot(wav_path) is None

        # Verwaiste Sperre: Heartbeat kuenstlich altern lassen -> gilt als tot,
        # progress_snapshot meldet nichts, _lock_acquire uebernimmt.
        with open(lock_file, "w", encoding="utf-8") as fh11:
            json.dump({"pid": 1, "phase": PHASE_READ, "done_frames": 5,
                       "total_frames": 10, "frac": 0.5,
                       "started_epoch": time.time() - 3 * LOCK_STALE_S,
                       "heartbeat_epoch": time.time() - 2 * LOCK_STALE_S}, fh11)
        assert progress_snapshot(wav_path) is None, "verwaiste Sperre gilt als lebend"
        lock11 = _lock_acquire(wav_path)
        assert progress_snapshot(wav_path)["pid"] == os.getpid(), "Uebernahme misslungen"
        lock11.release()

        # Unlesbarer Inhalt darf nicht durchschlagen (reine Lesefunktion).
        with open(lock_file, "w", encoding="utf-8") as fh11:
            fh11.write("kein JSON")
        assert progress_snapshot(wav_path) is None
        os.unlink(lock_file)

        ensure_audspec(wav_path)                # Cache oben entfernt -> baut jetzt
        assert not os.path.exists(lock_file), "Sperre nach erfolgreichem Bau geblieben"
        try:
            ensure_audspec(multi_path)          # 3 Kanaele -> Unsupported
        except AudioSpectrumUnsupportedError:
            pass
        else:
            raise AssertionError("AudioSpectrumUnsupportedError erwartet (3 Kanaele)")
        assert not os.path.exists(lock_path_for(multi_path)), "Sperre nach Fehler geblieben"
        print("T11 ok  (Sperrdatei: Belegen/Freigeben, Fortschritt read/write, "
              "BuildingError bei belegter Sperre, Uebernahme verwaister Sperren, "
              "ensure_audspec gibt nach Erfolg UND nach Fehler frei)")

        # --- T12: Median-Blockpegel (T-1626) --------------------------------
        # Gerechnet wird gegen die geschlossene Form, nicht gegen einen
        # frueheren Messwert: ein Sinus der Amplitude a hat die mittlere
        # Leistung a^2/2, ein komplexer Traeger der Amplitude a die Leistung
        # a^2. Die Toleranz von 0,2 dB deckt die 16-Bit-Quantisierung der
        # Testdatei ab.
        med_sr = 16000
        med_n = med_sr * 4
        med_t = np.arange(med_n) / med_sr
        med_mono_path = os.path.join(tmp, "median_mono.wav")
        _write_test_wav(med_mono_path, 0.5 * np.sin(2 * np.pi * 1000.0 * med_t), med_sr)
        med_mono = level_median_dbfs(med_mono_path)
        soll_mono = 10.0 * math.log10(0.5 ** 2 / 2.0)
        assert abs(med_mono - soll_mono) < 0.2, (med_mono, soll_mono)

        med_iq_path = os.path.join(tmp, "median_iq.wav")
        _write_test_iq_wav(med_iq_path, 0.5 * np.exp(2j * np.pi * 1000.0 * med_t), med_sr)
        med_iq = level_median_dbfs(med_iq_path)
        soll_iq = 10.0 * math.log10(0.5 ** 2)
        assert abs(med_iq - soll_iq) < 0.2, (med_iq, soll_iq)

        # Der Median ist gegen kurze Ausreisser unempfindlich - genau darum
        # steht er neben dem Startpegel: eine Sekunde Vollaussteuerung in vier
        # Sekunden Signal darf ihn nicht verschieben.
        med_spitze = 0.5 * np.sin(2 * np.pi * 1000.0 * med_t)
        med_spitze[:med_sr] = 0.99 * np.sin(2 * np.pi * 1000.0 * med_t[:med_sr])
        med_spitze_path = os.path.join(tmp, "median_spitze.wav")
        _write_test_wav(med_spitze_path, med_spitze, med_sr)
        assert abs(level_median_dbfs(med_spitze_path) - soll_mono) < 0.2

        # Zwei verschiedene Aussagen, nicht eine: ein unbrauchbares Format
        # WIRFT (Muster T5 weiter oben), eine zu kurze Datei liefert None.
        try:
            level_median_dbfs(multi_path)
        except AudioSpectrumUnsupportedError:
            pass
        else:
            raise AssertionError("AudioSpectrumUnsupportedError erwartet (3 Kanaele)")
        med_kurz_path = os.path.join(tmp, "median_kurz.wav")
        _write_test_wav(med_kurz_path, np.zeros(0), med_sr)
        assert level_median_dbfs(med_kurz_path) is None, "leere Datei muss None geben"
        print("T12 ok  (Median-Blockpegel dBFS: mono -9,0 / IQ -6,0 wie gerechnet, "
              "unempfindlich gegen eine Sekunde Vollaussteuerung, "
              "Ausnahme bei 3 Kanaelen, None bei leerer Datei)")

    print("\n[audio_spectrum] Selbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    sys.exit(main())
