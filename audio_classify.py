#!/usr/bin/env python3
"""
audio_classify.py

Nachklassifikation von WAV-Aufnahmen (eb200_core/audio_wav.py: 16 kHz/16
Bit/mono, Sidecar "<wav>.json" mit running-Flag seit T-1109) in eine von
sechs Klassen plus einen kontinuierlichen Sprach-Score. Design E1-E10
(abgestimmt 2026-08-30):

  E1  Modul generisch (WAV-Pfad rein, Ergebnis raus); erster Konsument
      spaeter :8060 (T-1007d).
  E2  Ausgabe: klasse in {LEER, RAUSCHEN, CW, DIGITAL, SPRACHE, UNKLAR}
      UND voice_score 0..1. Keine Klasse MUSIK - UNKLAR ist der ehrliche
      Rueckfall, wenn keine Kaskadenstufe eindeutig zutrifft.
  E3  Rechenbasis audio_spectrum.stft_db() mit n_fft 512, hop 256 bei
      16 kHz. KEIN Zugriff auf den .audspec-Cache (dessen Max-Hold
      zerstoert die Huellkurve, die M3/M4/M5 brauchen). Segmente unter
      1,5 s bekommen nur die LEER/RAUSCHEN-Vorpruefung, sonst UNKLAR.
  E4  Merkmale M1 Flachheit, M2 Tonlinien, M3 Modulationsspektrum der
      Huellkurve, M4 Pausenanteil, M5 Bimodalitaet der Huellkurve, M6
      spektraler Fluss. M7 (Nulldurchgangsrate) wurde gestrichen.
  E5  Kaskade LEER -> RAUSCHEN -> CW -> DIGITAL -> SPRACHE -> UNKLAR
      ("billig vor teuer, sicher vor unsicher"; CW vor SPRACHE, weil
      CW-Tastung bei 20-25 WPM die Silbenrate ueberlappt - die
      Entscheidung faellt dann ueber M2/Tonlinien statt ueber M3).
  E6  Startschwellen aus synthetischen Signalen (--selftest); Kalibrierung
      an realen Segmenten folgt in T-1007c ueber Handlabels aus :8070.
  E7  Sidecar "<voller WAV-Pfad>.klasse.json", regenerierbar, Gueltigkeit
      ueber wav.size/mtime wie beim .audspec-Cache. Nie ins Manifest.
  E8  Ausloesung: CLI-Batch (dieser Task) und spaeter :8060 (T-1007d) -
      NICHT im Laufprozess (eb200_core ist stdlib-only, dieses
      numpy-Modul liegt darum im Repo-Root wie audio_spectrum.py).
  E10 Format-Tag "eb200-audioclass-v1" (konsistent zu den bestehenden
      Cache-Tags eb200-audspec-v1/eb200-ifpan-bins-v*/eb200-dscan-*).

Aenderungen v2 (2026-08-30, T-1007c, ausgeloest durch den Realdatenlauf
T-1007a: 12 von 17 SPRACHE-Faellen wurden UNKLAR, weil der argmax des
Modulationsspektrums auf Fading/AGC statt auf die Silbenrate fiel):
  1. M1 Flachheit: Welch-Mittelung ueber Bloecke von 8 aufeinanderfolgenden
     Frames (lineare Leistung gemittelt, Rest < 8 Frames verworfen; < 8
     Frames insgesamt -> ein Block ueber den Mittelwert aller Frames),
     Flachheit je Block, Median ueber die Bloecke. rauschen_flatness_min
     0.55 -> 0.80 (die theoretische Flachheit von weissem Rauschen im
     Einzelframe-Periodogramm, e^-gamma ~= 0.56, lag zu nah an der alten
     Schwelle).
  2. M2 Praesenz: Peak-Suche auf dem zeitlichen Mittel der linearen
     Leistung (in dB umgerechnet) statt auf dem Median je Bin. Praesenz
     einer Peak-Bin = Anteil der AKTIVEN Frames (Bandleistung >= p90 -
     pause_drop_db, dieselbe Definition wie M4), in denen die Bin ihren
     eigenen lokalen Median (+/-10 Bins, je Frame) um >= line_excess_db/2
     ueberschreitet. line_presence_min 0.08 -> 0.60 ("+/-3 dB um den
     Median" war die falsche Praesenzdefinition fuer getastete Signale).
  3. M3 Detrend: gleitendes Mittel ueber 1.0 s (Kantenwiederholung an den
     Raendern) statt globalem Mittelwert von der dB-Huellkurve abgezogen,
     damit Fading/AGC-Anteile das Modulationsspektrum nicht mehr
     dominieren. Neues Merkmal syllable_ratio (Energieanteil im
     sprache_mod_hz-Band am gesamten 1-20-Hz-Band); die SPRACHE-Stufe
     greift jetzt zusaetzlich, wenn syllable_ratio >= syllable_ratio_min
     (0.45), auch bei mod_peak_hz ausserhalb von sprache_mod_hz.
  4. _voice_score: der Sprachband-Faktor von s_mod ist jetzt das Maximum
     aus dem bisherigen Sprachband-Faktor und dem syllable_ratio-Faktor
     (clip(syllable_ratio/syllable_ratio_min, 0, 1)), statt nur dem
     Sprachband-Faktor.
  5. RAUSCHEN-Stufe (nachtraeglich freigegeben nach Gate-2-Befund zu T12):
     flatness >= rauschen_flatness_min UND (mod_prominence <
     mod_prominence_min ODER syllable_ratio < syllable_ratio_min) ->
     RAUSCHEN. Rauschen ist primaer spektral flach; Modulation allein
     (Squelch, Fading, Burst) macht es nicht zu Sprache - erst ein
     Silbenanteil im sprache_mod_hz-Band. Die Prominenz-Klausel bleibt
     fuer den unmodulierten Fall, die syllable_ratio-Klausel faengt den
     modulierten Fall (Regressionsfall T12: Rauschburst mit exakt
     periodischer AM - mod_prominence weit ueber der Schwelle, aber
     syllable_ratio klar unter syllable_ratio_min).

Aenderungen v3 (2026-08-30, T-1007c-Nachtrag, ausgeloest durch Gate-4-Befund
mit 13 Direktaufnahmen: reale CW-Aufnahmen zeigen 2-3 Spektrallinien
(Keying-Seitenbaender), was die alte n_lines==1-Bedingung an DIGITAL verlor;
eine RAUSCHEN-Datei mit steilem USB-Filterrolloff ausserhalb 100-3800 Hz
erschien wegen BAND_HZ als "nicht flach"; reale Digitalmodi haben oft
n_lines==0 UND eine fuer die alte flatness>=0.4-Klausel viel zu niedrige
Flachheit):
  1. Belegtes Band (Grundlage fuer M1 und M6, sowie M3/M4/M5 ueber die
     gemeinsame Groesse band_power_db): zeitliches Mittel der linearen
     Leistung je Bin -> dB, ueber 5 Bins geglaettet; Bins innerhalb
     100-3800 Hz, die >= Maximum - 20 dB liegen, spannen [erster, letzter]
     Bin auf; auf mindestens 1000 Hz Breite symmetrisch um den
     Bandmittelpunkt aufgeweitet, Raender auf 100-3800 Hz begrenzt. Neue
     Merkmale occ_lo_hz/occ_hi_hz/occ_bw_hz. M2 (Linienerkennung, inkl.
     der Leistungsnormierung fuer line_energy_ratio) bleibt auf dem vollen
     100-3800-Hz-Band - nur M1/M3/M4/M6 wechseln auf das belegte Band.
  2. Neue Merkmale: peak_energy_ratio (Leistungsanteil in +/-1 Bin um das
     Maximum des zeitlichen Leistungsmittels am belegten Band, unabhaengig
     von der Linien-Stabilitaet - im Gegensatz zu line_energy_ratio) und
     keying_rate (Zustandswechsel aktiv/Pause je Sekunde, Hysterese: aktiv
     ab p90 - pause_drop_db, Pause ab dort - 6 dB weiter).
  3. THRESHOLDS: flux_sprache_min/flux_digital_max entfernt (Fluss misst
     eher die Empfangsbandbreite (AM ~6 kHz vs. USB ~2,4 kHz) als die
     Klasse und fliesst in keiner Kaskadenbedingung mehr ein, bleibt aber
     als Merkmal erhalten). Neu: digital_flatness_max (0.005),
     cw_peak_ratio_min (0.50), cw_keying_rate_min (2.0),
     syllable_ratio_weak_min (0.20). Geaendert: pause_ratio_cw_min
     0.20 -> 0.10.
  4. Kaskade: CW jetzt ueber dominante Linie + Tastrate
     (peak_energy_ratio >= cw_peak_ratio_min UND bimodality UND
     pause_ratio UND keying_rate >= cw_keying_rate_min) statt ueber
     n_lines==1 - reale CW-Aufnahmen mit Keying-Seitenbaendern (n_lines
     2-3) werden so nicht mehr an DIGITAL verloren. DIGITAL greift bei
     flatness <= digital_flatness_max (sehr schmalbandige/tonale reale
     Digitalmodi) ODER n_lines >= 2 ODER (dominante Linie ohne CW-Tastung/
     -Pausenmuster, z. B. Traeger/PSK). SPRACHE zweistufig: Stufe 6 wie
     v2, Stufe 6b faengt schwache Faelle (kein Liniensignal, nicht flach,
     mod_prominence hoch, aber syllable_ratio nur >= syllable_ratio_weak_min
     statt syllable_ratio_min) mit Ergebnisfeld sicherheit="schwach"
     (sonst "normal").
  5. _voice_score: s_flux entfernt, neu s_tonal = clip(1 -
     peak_energy_ratio/cw_peak_ratio_min, 0, 1) als vierter Faktor des
     geometrischen Mittels (weiterhin ** 0.25). Bei sicherheit=="schwach"
     wird der Score zusaetzlich mit 0.7 multipliziert.
  6. Sidecar: neues Feld "sicherheit" ("normal"/"schwach").
  7. keying_rate neu definiert (Nachtrag 2, statt 6-dB-Hysterese): direkt
     aus der M4-Pausenmaske (Pause wenn Bandleistung < p90 -
     pause_drop_db), Laeufe < 2 Frames werden dem Nachbarlauf zugeschlagen
     (Entprellung); keying_rate = Anzahl Pause->aktiv-Wechsel je Sekunde
     Fensterdauer. cw_keying_rate_min neu kalibriert (Startwert 1.0 statt
     0.4, da sich die Merkmalsskala durch die Neudefinition verschiebt).
  8. Neues Merkmal fsk_corr (Nachtrag 2): bis zu 8 staerkste
     Kandidatenlinien im belegten Band (>= 10 dB ueber lokalem Median,
     +/-10 Bins); fuer jedes Paar mit 100-1000 Hz Abstand Pearson-
     Korrelation der beiden dB-Zeitreihen (Bin +/-1 gemittelt); fsk_corr =
     Minimum ueber alle Paare (starke Antikorrelation zwischen zwei
     wechselnden FSK-Toenen), 1.0 wenn < 2 Kandidaten oder kein Paar im
     Abstandsfenster liegt. DIGITAL greift zusaetzlich bei fsk_corr <=
     fsk_corr_max (-0.4), Grund nennt das Frequenzpaar. Aggregation ueber
     mehrere Fenster: Minimum, fsk_pair_lo_hz/fsk_pair_hi_hz stammen aus
     demselben Fenster wie das Minimum.
  9. Kaskade (Nachtrag 2): Bedingung "n_lines == 0" in SPRACHE-Stufe 6/6b
     entfernt - peak_energy_ratio < cw_peak_ratio_min bleibt als alleiniger
     Tonalitaets-Schutz (n_lines==0 blockierte reale, nicht dominant-tonale
     Digitalsignale mit vereinzelten schwachen Linien faelschlich).
     rauschen_flatness_min 0.80 -> 0.75, syllable_ratio_weak_min
     0.20 -> 0.18 (Gate-4-Kalibrierung, siehe Entscheidungslog PLAN.md).

Aenderungen v4 (T-1822, Meilenstein 18): nur die Begruendungen (reasons) sind
englisch formuliert; Kaskade, Schwellen und Merkmale sind unveraendert. Der
Versionswechsel macht alte Sidecars mit deutschen Begruendungen ungueltig - sie
werden beim naechsten Oeffnen bzw. von der Aufbereitung neu klassifiziert.

Sidecar-Format "eb200-audioclass-v1" (Datei "<voller WAV-Pfad>.klasse.json",
Suffix-Anhaengung wie beim .audspec-Cache):
  {"format": "eb200-audioclass-v1", "classifier_version": "eb200-audioclass-v1",
   "created_iso": "<lokale ISO-Zeit>",
   "wav": {"size": <bytes>, "mtime": <float>, "sample_rate_hz": ...,
           "duration_s": ...},
   "klasse": "SPRACHE", "voice_score": 0.0, "reasons": ["..."],
   "windows": <n>, "analyzed_s": <float>,
   "features": {... aggregierte M1-M6 + rms_dbfs ...},
   "thresholds": {... Kopie von THRESHOLDS zum Zeitpunkt der Klassifikation,
                  damit T-1007c nachvollziehen kann, womit klassifiziert
                  wurde ...}}
  Gueltig, wenn format UND classifier_version == CLASSIFIER_VERSION UND
  wav.size/wav.mtime zur aktuellen Datei passen (sonst None - Aufrufer
  baut neu). Schreiben atomar (Temp-Datei + os.replace, Muster
  audio_spectrum.build_audspec/eb200_core/job_store.py).

Start:
    python audio_classify.py <datei.wav | verzeichnis> [--rebuild] [--json]
    python audio_classify.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

import audio_spectrum

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

CLASSIFIER_VERSION = "eb200-audioclass-v4"

KLASSEN = ("LEER", "RAUSCHEN", "CW", "DIGITAL", "SPRACHE", "UNKLAR")

N_FFT, HOP = 512, 256
MIN_DURATION_S = 1.5
BAND_HZ = (100, 3800)            # Auswerteband fuer M1/M3/M4/M6
LINE_BAND_HZ = (100, 4000)       # Suchband fuer M2
WINDOW_S = 10.0                  # Analysefenster
MAX_WINDOWS = 6                  # lange Dateien: bis zu 6 Fenster ueber die Dauer verteilt

THRESHOLDS = {
    "leer_rms_dbfs": -55.0,        # darunter LEER
    "rauschen_flatness_min": 0.75, # M1 Welch-Block-Median >= -> Rauschkandidat
    "mod_prominence_min": 3.5,     # M3 Peak/Median im 1-20-Hz-Band
    "sprache_mod_hz": (2.5, 8.0),  # M3 Peaklage
    "line_excess_db": 15.0,        # M2: Bin liegt >= so viel ueber lokalem Median (+/-10 Bins)
    "line_presence_min": 0.60,     # M2: Anteil aktiver Frames, in denen die Linie steht
    "bimodal_bc_min": 0.555,       # M5 Sarle-Bimodalitaetskoeffizient
    "pause_ratio_cw_min": 0.10,    # M4 fuer CW
    "pause_drop_db": 20.0,         # M4: Frame gilt als Pause wenn < p90 - drop
    "syllable_ratio_min": 0.45,    # M3: Energieanteil im sprache_mod_hz-Band am 1-20-Hz-Band
    "digital_flatness_max": 0.005, # M1: sehr schmalbandig/tonal -> DIGITAL
    "cw_peak_ratio_min": 0.50,     # peak_energy_ratio: dominante Einzellinie
    "cw_keying_rate_min": 1.0,     # keying_rate (Wechsel/s), Startwert nach Neudefinition (A1)
    "syllable_ratio_weak_min": 0.18,  # M3: schwacher SPRACHE-Weg (Stufe 6b)
    "fsk_corr_max": -0.4,          # fsk_corr <= -> DIGITAL (FSK-Tonpaar-Antikorrelation)
}

_EPS = 1e-12


class AudioClassifyBusyError(Exception):
    """Aufnahme laeuft noch (Sidecar running=True) - Klassifikation wird
    nicht gebaut."""


class AudioClassifyUnsupportedError(Exception):
    """WAV-Format wird nicht unterstuetzt (nur 1 Kanal, 16 Bit)."""


# ---------------------------------------------------------------------------
# Sidecar-Pfad + interne Pruefungen (Muster audio_spectrum, bewusst
# NACHGEBAUT statt importiert - dieses Modul bleibt eigenstaendig, siehe E1)
# ---------------------------------------------------------------------------

def klasse_path_for(wav_path: str) -> str:
    return wav_path + ".klasse.json"


def _check_not_running(wav_path: str) -> None:
    sidecar_path = wav_path + ".json"
    if not os.path.isfile(sidecar_path):
        return
    try:
        with open(sidecar_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(meta, dict) and bool(meta.get("running", False)):
        raise AudioClassifyBusyError(f"recording still in progress: {wav_path}")


def _check_supported(channels: int, sampwidth: int) -> None:
    if channels != 1 or sampwidth != 2:
        raise AudioClassifyUnsupportedError(
            f"unsupported WAV format: {channels} channel(s), "
            f"{sampwidth * 8}-bit (only 1 channel / 16 bit supported)"
        )


# ---------------------------------------------------------------------------
# Merkmale (M1-M6 + rms_dbfs) auf EINEM Fenster
# ---------------------------------------------------------------------------

def _band_mask(freqs, band_hz):
    return (freqs >= band_hz[0]) & (freqs <= band_hz[1])


def _moving_average_detrend(values, window_n: int):
    """Zieht ein gleitendes Mittel (Fensterbreite window_n, Raender per
    Kantenwiederholung) von values ab - entfernt Fading/AGC-Anteile, die
    ein globaler Mittelwertabzug nicht erfasst."""
    n = values.size
    window_n = min(max(1, window_n), n)
    if window_n <= 1:
        return values - np.mean(values)
    pad_left = window_n // 2
    pad_right = window_n - 1 - pad_left
    padded = np.pad(values, (pad_left, pad_right), mode="edge")
    kernel = np.ones(window_n) / window_n
    trend = np.convolve(padded, kernel, mode="valid")
    return values - trend


def _modulation_spectrum(band_power_db, dt_row_s: float):
    """M3: Huellkurve (Bandleistung je Frame in dB, gleitendes Mittel ueber
    1.0 s abgezogen, Hann, rfft). Peak/Prominence im Bereich 1-20 Hz sowie
    syllable_ratio = Energie(|X|^2) im sprache_mod_hz-Band / Energie im
    1-20-Hz-Band."""
    n_frames = band_power_db.size
    if n_frames < 4 or dt_row_s <= 0:
        return 0.0, 0.0, 0.0
    window_n = max(1, round(1.0 / dt_row_s))
    envelope = _moving_average_detrend(band_power_db, window_n)
    spectrum = np.fft.rfft(envelope * np.hanning(n_frames))
    mag = np.abs(spectrum)
    mod_freqs = np.arange(mag.size) / (n_frames * dt_row_s)
    sel = (mod_freqs >= 1.0) & (mod_freqs <= 20.0)
    if not np.any(sel):
        return 0.0, 0.0, 0.0
    sel_mag = mag[sel]
    sel_freqs = mod_freqs[sel]
    peak_idx = int(np.argmax(sel_mag))
    peak_val = float(sel_mag[peak_idx])
    med_val = float(np.median(sel_mag))
    prominence = peak_val / med_val if med_val > 0 else 0.0

    sprache_lo, sprache_hi = THRESHOLDS["sprache_mod_hz"]
    syl_sel_rel = (sel_freqs >= sprache_lo) & (sel_freqs <= sprache_hi)
    energy_all = float(np.sum(sel_mag ** 2))
    energy_syl = float(np.sum(sel_mag[syl_sel_rel] ** 2))
    syllable_ratio = energy_syl / energy_all if energy_all > 0 else 0.0

    return float(sel_freqs[peak_idx]), prominence, syllable_ratio


def _bimodality_coefficient(values):
    """M5: Sarle-Koeffizient BC = (g1^2+1) / (g2 + 3(n-1)^2/((n-2)(n-3)))
    ueber values (hier: Bandleistung je Frame in dB). g1 = Schiefe, g2 =
    Exzess-Kurtosis (Momentenschaetzer, wie in der klassischen
    BC-Definition ueblich)."""
    n = values.size
    if n < 4:
        return 0.0
    mean = np.mean(values)
    std = np.std(values)
    if std < 1e-9:
        return 0.0
    z = (values - mean) / std
    g1 = float(np.mean(z ** 3))
    g2 = float(np.mean(z ** 4) - 3.0)
    denom = g2 + 3.0 * ((n - 1) ** 2) / ((n - 2) * (n - 3))
    if denom <= 0:
        return 0.0
    return float((g1 ** 2 + 1.0) / denom)


def _spectral_flux(db_band):
    """M6: je Frame L2-Norm der Differenz normierter dB-Spektren (Band, je
    Frame auf Mittelwert 0 / Std 1 gebracht) zum Vorframe, geteilt durch
    sqrt(n_bins); Median ueber alle Frame-Paare."""
    n_frames, n_bins = db_band.shape
    if n_frames < 2 or n_bins == 0:
        return 0.0
    mean = np.mean(db_band, axis=1, keepdims=True)
    std = np.std(db_band, axis=1, keepdims=True)
    std = np.where(std < 1e-9, 1e-9, std)
    norm = (db_band - mean) / std
    diff = norm[1:] - norm[:-1]
    flux_per_frame = np.sqrt(np.sum(diff ** 2, axis=1)) / math.sqrt(n_bins)
    return float(np.median(flux_per_frame))


def _line_detect(db_full, power_full, band_mask, line_mask, active_mask):
    """M2: n_lines, line_energy_ratio, presence_debug. Peak-Suche auf dem
    zeitlichen Mittel der linearen Leistung (in dB), Praesenz je
    Peak-Kandidat = Anteil der AKTIVEN Frames (active_mask, dieselbe
    Definition wie M4), in denen die Bin ihren eigenen lokalen Median
    (+/-10 Bins, je Frame) um >= line_excess_db/2 ueberschreitet. Arbeitet
    in der Bin-Indexierung des LINE_BAND-Ausschnitts (line_idx), die
    Leistungsbilanz erfolgt danach ueber die absoluten Bin-Indizes im BAND
    (siehe Moduldoku Merkmale). presence_debug ist eine Liste von
    (Bin-Index im Linienband, Praesenz) fuer alle Peak-Kandidaten -
    Kalibrier-/Testhilfe (siehe Selbsttest T3)."""
    line_idx = np.nonzero(line_mask)[0]
    if line_idx.size == 0:
        return 0, 0.0, []

    db_line = db_full[:, line_idx]
    power_line = power_full[:, line_idx]
    mean_power_line = np.mean(power_line, axis=0)
    mean_db = 10.0 * np.log10(np.maximum(mean_power_line, _EPS))
    n_line_bins = mean_db.size

    local_med = np.empty(n_line_bins)
    is_local_max = np.zeros(n_line_bins, dtype=bool)
    for j in range(n_line_bins):
        lo10, hi10 = max(0, j - 10), min(n_line_bins, j + 11)
        local_med[j] = np.median(mean_db[lo10:hi10])
        lo2, hi2 = max(0, j - 2), min(n_line_bins, j + 3)
        is_local_max[j] = mean_db[j] >= mean_db[lo2:hi2].max()

    is_peak = (mean_db >= local_med + THRESHOLDS["line_excess_db"]) & is_local_max

    stable = np.zeros(n_line_bins, dtype=bool)
    presence_debug = []
    n_active = int(np.sum(active_mask))
    if n_active > 0:
        db_line_active = db_line[active_mask]
        half_excess = THRESHOLDS["line_excess_db"] / 2.0
        for j in np.nonzero(is_peak)[0]:
            lo10, hi10 = max(0, j - 10), min(n_line_bins, j + 11)
            frame_local_med = np.median(db_line_active[:, lo10:hi10], axis=1)
            within = (db_line_active[:, j] - frame_local_med) >= half_excess
            presence = float(np.mean(within))
            presence_debug.append((j, presence))
            if presence >= THRESHOLDS["line_presence_min"]:
                stable[j] = True

    n_lines = int(np.sum(stable))

    mean_power_full = np.mean(power_full, axis=0)
    total_band_power = float(np.sum(mean_power_full[band_mask]))
    line_energy_ratio = 0.0
    if total_band_power > 0 and n_lines:
        covered = np.zeros(mean_power_full.shape[0], dtype=bool)
        for j in np.nonzero(stable)[0]:
            abs_bin = line_idx[j]
            lo1, hi1 = max(0, abs_bin - 1), min(mean_power_full.shape[0], abs_bin + 2)
            covered[lo1:hi1] = True
        covered &= band_mask
        line_energy_ratio = float(np.sum(mean_power_full[covered]) / total_band_power)

    return n_lines, line_energy_ratio, presence_debug


def _occupied_band(power_full, freqs, band_hz):
    """Belegtes Band (v3): zeitliches Mittel der linearen Leistung je Bin
    (in dB), ueber 5 Bins geglaettet. Bins innerhalb band_hz, die >=
    Maximum - 20 dB liegen, spannen [erster, letzter] Bin auf; auf
    mindestens 1000 Hz Breite symmetrisch um den Bandmittelpunkt
    aufgeweitet, Raender auf band_hz begrenzt. Rueckgabe:
    (occ_lo_hz, occ_hi_hz, occ_mask)."""
    cand_mask = _band_mask(freqs, band_hz)
    cand_idx = np.nonzero(cand_mask)[0]
    if cand_idx.size == 0:
        return float(band_hz[0]), float(band_hz[1]), cand_mask

    mean_power = np.mean(power_full[:, cand_idx], axis=0)
    mean_db = 10.0 * np.log10(np.maximum(mean_power, _EPS))

    smooth_n = min(5, mean_db.size)
    pad_left = smooth_n // 2
    pad_right = smooth_n - 1 - pad_left
    padded = np.pad(mean_db, (pad_left, pad_right), mode="edge")
    kernel = np.ones(smooth_n) / smooth_n
    smoothed_db = np.convolve(padded, kernel, mode="valid")

    threshold = float(np.max(smoothed_db)) - 20.0
    above = np.nonzero(smoothed_db >= threshold)[0]
    first_rel, last_rel = int(above[0]), int(above[-1])

    lo_hz = float(freqs[cand_idx[first_rel]])
    hi_hz = float(freqs[cand_idx[last_rel]])

    if hi_hz - lo_hz < 1000.0:
        mid_hz = (lo_hz + hi_hz) / 2.0
        lo_hz = mid_hz - 500.0
        hi_hz = mid_hz + 500.0

    lo_hz = max(float(band_hz[0]), lo_hz)
    hi_hz = min(float(band_hz[1]), hi_hz)
    if hi_hz < lo_hz:
        lo_hz, hi_hz = float(band_hz[0]), float(band_hz[1])

    occ_mask = (freqs >= lo_hz) & (freqs <= hi_hz)
    return lo_hz, hi_hz, occ_mask


def _peak_energy_ratio(power_occ) -> float:
    """Anteil der Leistung in +/-1 Bin um das Maximum des zeitlichen Mittels
    der linearen Leistung an der Gesamtleistung im belegten Band -
    unabhaengig von der Linien-Stabilitaet (im Gegensatz zu
    line_energy_ratio, siehe M2/_line_detect)."""
    if power_occ.shape[1] == 0:
        return 0.0
    mean_power = np.mean(power_occ, axis=0)
    total = float(np.sum(mean_power))
    if total <= 0:
        return 0.0
    peak_idx = int(np.argmax(mean_power))
    lo, hi = max(0, peak_idx - 1), min(mean_power.shape[0], peak_idx + 2)
    return float(np.sum(mean_power[lo:hi]) / total)


def _debounce_runs(state, min_len: int = 2):
    """Fasst Zustandslaeufe < min_len mit dem Nachbarlauf zusammen (erster
    Lauf -> naechster, sonst -> vorheriger), bis alle Laeufe >= min_len sind
    oder nur noch einer uebrig ist. state: 1-D bool-Array."""
    runs = []
    for val in state:
        val = bool(val)
        if runs and runs[-1][0] == val:
            runs[-1][1] += 1
        else:
            runs.append([val, 1])

    def _coalesce():
        i = 0
        while i < len(runs) - 1:
            if runs[i][0] == runs[i + 1][0]:
                runs[i][1] += runs[i + 1][1]
                del runs[i + 1]
            else:
                i += 1

    changed = True
    while changed and len(runs) > 1:
        _coalesce()
        if len(runs) <= 1:
            break
        changed = False
        for i, (_val, length) in enumerate(runs):
            if length < min_len:
                target = i + 1 if i == 0 else i - 1
                runs[target][1] += length
                del runs[i]
                changed = True
                break

    result = np.empty(sum(length for _, length in runs), dtype=bool)
    pos = 0
    for val, length in runs:
        result[pos:pos + length] = val
        pos += length
    return result


def _keying_rate(band_power_db, dt_row_s: float, pause_threshold: float) -> float:
    """Zustandsfolge Pause/aktiv aus der M4-Pausenmaske (Pause wenn
    Bandleistung < pause_threshold, dieselbe Schwelle wie pause_ratio);
    Laeufe < 2 Frames werden dem Nachbarlauf zugeschlagen (Entprellung).
    keying_rate = Anzahl Pause->aktiv-Wechsel je Sekunde Fensterdauer."""
    n = band_power_db.size
    if n < 2 or dt_row_s <= 0:
        return 0.0
    aktiv = band_power_db >= pause_threshold
    state = _debounce_runs(aktiv, min_len=2)
    n_on = int(np.sum((~state[:-1]) & state[1:]))
    duration_s = n * dt_row_s
    return n_on / duration_s if duration_s > 0 else 0.0


def _fsk_corr(db_occ, power_occ, freqs_occ):
    """fsk_corr: Kandidatenlinien = lokale Maxima des zeitlichen Mittel-dB-
    Spektrums im belegten Band, >= 10 dB ueber dem lokalen Median (+/-10
    Bins), maximal die 8 staerksten. Fuer jedes Kandidatenpaar mit
    Frequenzabstand 100-1000 Hz: Pearson-Korrelation der beiden dB-
    Zeitreihen (Bin +/-1 gemittelt). Rueckgabe (fsk_corr, pair_lo_hz,
    pair_hi_hz): fsk_corr ist das Minimum ueber alle Paare (starke
    Antikorrelation -> FSK-Tonwechsel), 1.0 UND 0.0/0.0 wenn < 2 Kandidaten
    oder kein Paar im Abstandsfenster liegt."""
    n_bins = power_occ.shape[1]
    if n_bins == 0:
        return 1.0, 0.0, 0.0

    mean_power = np.mean(power_occ, axis=0)
    mean_db = 10.0 * np.log10(np.maximum(mean_power, _EPS))

    local_med = np.empty(n_bins)
    is_local_max = np.zeros(n_bins, dtype=bool)
    for j in range(n_bins):
        lo10, hi10 = max(0, j - 10), min(n_bins, j + 11)
        local_med[j] = np.median(mean_db[lo10:hi10])
        lo2, hi2 = max(0, j - 2), min(n_bins, j + 3)
        is_local_max[j] = mean_db[j] >= mean_db[lo2:hi2].max()

    cand_idx = np.nonzero((mean_db >= local_med + 10.0) & is_local_max)[0]
    if cand_idx.size < 2:
        return 1.0, 0.0, 0.0

    if cand_idx.size > 8:
        staerkste = np.argsort(mean_db[cand_idx])[::-1][:8]
        cand_idx = np.sort(cand_idx[staerkste])

    def _line_series(idx):
        lo, hi = max(0, idx - 1), min(n_bins, idx + 2)
        return np.mean(db_occ[:, lo:hi], axis=1)

    min_corr = 1.0
    pair_lo_hz = 0.0
    pair_hi_hz = 0.0
    found = False
    for a in range(len(cand_idx)):
        for b in range(a + 1, len(cand_idx)):
            idx_a, idx_b = int(cand_idx[a]), int(cand_idx[b])
            freq_a, freq_b = float(freqs_occ[idx_a]), float(freqs_occ[idx_b])
            if not (100.0 <= abs(freq_b - freq_a) <= 1000.0):
                continue
            sa, sb = _line_series(idx_a), _line_series(idx_b)
            if np.std(sa) < 1e-9 or np.std(sb) < 1e-9:
                corr = 0.0
            else:
                corr = float(np.corrcoef(sa, sb)[0, 1])
            if not found or corr < min_corr:
                min_corr = corr
                pair_lo_hz, pair_hi_hz = sorted((freq_a, freq_b))
            found = True

    if not found:
        return 1.0, 0.0, 0.0
    return min_corr, pair_lo_hz, pair_hi_hz


def compute_features(samples, sample_rate_hz: int) -> dict:
    """Merkmale M1-M6 + rms_dbfs auf EINEM Fenster (samples: 1-D, -1..1)."""
    samples = np.asarray(samples, dtype=np.float64)
    rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-6))

    db, dt_row_s, df_hz = audio_spectrum.stft_db(samples, sample_rate_hz, N_FFT, HOP)
    n_frames, n_bins = db.shape
    if n_frames == 0:
        return {
            "rms_dbfs": rms_dbfs, "flatness": 0.0, "n_lines": 0,
            "line_energy_ratio": 0.0, "mod_peak_hz": 0.0, "mod_prominence": 0.0,
            "pause_ratio": 0.0, "bimodality": 0.0, "flux": 0.0,
            "syllable_ratio": 0.0, "peak_energy_ratio": 0.0, "keying_rate": 0.0,
            "occ_lo_hz": 0.0, "occ_hi_hz": 0.0, "occ_bw_hz": 0.0,
            "fsk_corr": 1.0, "fsk_pair_lo_hz": 0.0, "fsk_pair_hi_hz": 0.0,
        }

    freqs = np.arange(n_bins) * df_hz
    band_mask = _band_mask(freqs, BAND_HZ)
    line_mask = _band_mask(freqs, LINE_BAND_HZ)

    db64 = db.astype(np.float64)
    power = 10.0 ** (db64 / 10.0)

    occ_lo_hz, occ_hi_hz, occ_mask = _occupied_band(power, freqs, BAND_HZ)
    occ_bw_hz = occ_hi_hz - occ_lo_hz

    occ_power = power[:, occ_mask]
    n_frames_occ = occ_power.shape[0]
    if n_frames_occ >= 8:
        n_blocks = n_frames_occ // 8
        blocks = occ_power[: n_blocks * 8].reshape(n_blocks, 8, -1)
        block_power = np.mean(blocks, axis=1)
    else:
        block_power = np.mean(occ_power, axis=0, keepdims=True)
    log_block_power = np.log(np.maximum(block_power, _EPS))
    gmean = np.exp(np.mean(log_block_power, axis=1))
    amean = np.mean(block_power, axis=1)
    flatness_per_block = np.where(amean > 0, gmean / amean, 0.0)
    flatness = float(np.median(flatness_per_block))

    occ_power_sum = np.sum(occ_power, axis=1)
    band_power_db = 10.0 * np.log10(np.maximum(occ_power_sum, _EPS))

    p90 = float(np.percentile(band_power_db, 90))
    pause_threshold = p90 - THRESHOLDS["pause_drop_db"]
    pause_ratio = float(np.mean(band_power_db < pause_threshold))
    active_mask = band_power_db >= pause_threshold

    mod_peak_hz, mod_prominence, syllable_ratio = _modulation_spectrum(band_power_db, dt_row_s)
    bimodality = _bimodality_coefficient(band_power_db)
    flux = _spectral_flux(db64[:, occ_mask])
    n_lines, line_energy_ratio, _presence_debug = _line_detect(
        db64, power, band_mask, line_mask, active_mask
    )
    peak_energy_ratio = _peak_energy_ratio(occ_power)
    keying_rate = _keying_rate(band_power_db, dt_row_s, pause_threshold)
    fsk_corr, fsk_pair_lo_hz, fsk_pair_hi_hz = _fsk_corr(db64[:, occ_mask], occ_power, freqs[occ_mask])

    return {
        "rms_dbfs": rms_dbfs, "flatness": flatness, "n_lines": n_lines,
        "line_energy_ratio": line_energy_ratio, "mod_peak_hz": mod_peak_hz,
        "mod_prominence": mod_prominence, "pause_ratio": pause_ratio,
        "bimodality": bimodality, "flux": flux, "syllable_ratio": syllable_ratio,
        "peak_energy_ratio": peak_energy_ratio, "keying_rate": keying_rate,
        "occ_lo_hz": occ_lo_hz, "occ_hi_hz": occ_hi_hz, "occ_bw_hz": occ_bw_hz,
        "fsk_corr": fsk_corr, "fsk_pair_lo_hz": fsk_pair_lo_hz, "fsk_pair_hi_hz": fsk_pair_hi_hz,
    }


# ---------------------------------------------------------------------------
# Kaskade + voice_score
# ---------------------------------------------------------------------------

def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _voice_score(features: dict) -> float:
    t = THRESHOLDS
    mod_prominence = features["mod_prominence"]
    mod_peak_hz = features["mod_peak_hz"]
    syllable_ratio = features["syllable_ratio"]
    flatness = features["flatness"]
    n_lines = features["n_lines"]
    peak_energy_ratio = features["peak_energy_ratio"]

    in_sprachband = t["sprache_mod_hz"][0] <= mod_peak_hz <= t["sprache_mod_hz"][1]
    band_factor = 1.0 if in_sprachband else 0.3
    syl_factor = _clip01(syllable_ratio / t["syllable_ratio_min"])
    s_mod = _clip01((mod_prominence - 1.0) / (t["mod_prominence_min"] * 2.0 - 1.0))
    s_mod *= max(band_factor, syl_factor)
    s_flat = _clip01(1.0 - flatness / t["rauschen_flatness_min"])
    s_tonal = _clip01(1.0 - peak_energy_ratio / t["cw_peak_ratio_min"])
    if n_lines == 0:
        s_line = 1.0
    elif n_lines == 1:
        s_line = 0.3
    else:
        s_line = 0.1

    score = (s_mod * s_flat * s_line * s_tonal) ** 0.25
    return round(float(score), 3)


def classify_features(features: dict, duration_s: float):
    """Kaskade LEER -> RAUSCHEN -> CW -> DIGITAL -> SPRACHE -> UNKLAR (siehe
    Moduldoku E5/Aenderungen v3). Gibt (klasse, voice_score, reasons,
    sicherheit) zurueck; sicherheit ist "schwach" nur fuer den zweiten,
    schwaecheren SPRACHE-Weg (Stufe 6b), sonst "normal"."""
    t = THRESHOLDS
    reasons = []

    rms_dbfs = features["rms_dbfs"]
    flatness = features["flatness"]
    n_lines = features["n_lines"]
    mod_peak_hz = features["mod_peak_hz"]
    mod_prominence = features["mod_prominence"]
    pause_ratio = features["pause_ratio"]
    bimodality = features["bimodality"]
    syllable_ratio = features["syllable_ratio"]
    peak_energy_ratio = features["peak_energy_ratio"]
    keying_rate = features["keying_rate"]
    fsk_corr = features["fsk_corr"]
    fsk_pair_lo_hz = features["fsk_pair_lo_hz"]
    fsk_pair_hi_hz = features["fsk_pair_hi_hz"]

    if rms_dbfs < t["leer_rms_dbfs"]:
        reasons.append(
            f"rms_dbfs={rms_dbfs:.1f} < leer_rms_dbfs={t['leer_rms_dbfs']:.1f} -> LEER"
        )
        return KLASSEN[0], 0.0, reasons, "normal"

    if duration_s < MIN_DURATION_S:
        if flatness >= t["rauschen_flatness_min"]:
            reasons.append(
                f"too short ({duration_s:.2f}s < {MIN_DURATION_S}s), but "
                f"flatness={flatness:.2f} >= rauschen_flatness_min -> RAUSCHEN"
            )
            klasse = KLASSEN[1]
        else:
            reasons.append(
                f"too short ({duration_s:.2f}s < {MIN_DURATION_S}s) for a "
                f"reliable cascade decision -> UNKLAR"
            )
            klasse = KLASSEN[5]
        return klasse, _voice_score(features), reasons, "normal"

    if flatness >= t["rauschen_flatness_min"] and (
        mod_prominence < t["mod_prominence_min"] or syllable_ratio < t["syllable_ratio_min"]
    ):
        weg = (
            f"mod_prominence={mod_prominence:.2f} < mod_prominence_min" if mod_prominence < t["mod_prominence_min"]
            else f"syllable_ratio={syllable_ratio:.2f} < syllable_ratio_min"
        )
        reasons.append(f"flatness={flatness:.2f} >= rauschen_flatness_min AND {weg} -> RAUSCHEN")
        return KLASSEN[1], _voice_score(features), reasons, "normal"

    if (peak_energy_ratio >= t["cw_peak_ratio_min"] and bimodality >= t["bimodal_bc_min"]
            and pause_ratio >= t["pause_ratio_cw_min"] and keying_rate >= t["cw_keying_rate_min"]):
        reasons.append(
            f"peak_energy_ratio={peak_energy_ratio:.2f} >= cw_peak_ratio_min, "
            f"bimodality={bimodality:.3f} >= bimodal_bc_min, "
            f"pause_ratio={pause_ratio:.2f} >= pause_ratio_cw_min, "
            f"keying_rate={keying_rate:.2f} >= cw_keying_rate_min -> CW"
        )
        return KLASSEN[2], _voice_score(features), reasons, "normal"

    if flatness <= t["digital_flatness_max"]:
        reasons.append(f"flatness={flatness:.4f} <= digital_flatness_max -> DIGITAL")
        return KLASSEN[3], _voice_score(features), reasons, "normal"
    if n_lines >= 2:
        reasons.append(f"n_lines={n_lines} >= 2 -> DIGITAL")
        return KLASSEN[3], _voice_score(features), reasons, "normal"
    if peak_energy_ratio >= t["cw_peak_ratio_min"] and bimodality < t["bimodal_bc_min"]:
        reasons.append(
            f"peak_energy_ratio={peak_energy_ratio:.2f} >= cw_peak_ratio_min, "
            f"bimodality={bimodality:.3f} < bimodal_bc_min (carrier/PSK) -> DIGITAL"
        )
        return KLASSEN[3], _voice_score(features), reasons, "normal"
    if fsk_corr <= t["fsk_corr_max"]:
        reasons.append(
            f"fsk_corr={fsk_corr:.2f} <= fsk_corr_max "
            f"(pair {fsk_pair_lo_hz:.0f}/{fsk_pair_hi_hz:.0f} Hz) -> DIGITAL"
        )
        return KLASSEN[3], _voice_score(features), reasons, "normal"

    sprache_lo, sprache_hi = t["sprache_mod_hz"]
    in_sprachband = sprache_lo <= mod_peak_hz <= sprache_hi
    via_syllable = syllable_ratio >= t["syllable_ratio_min"]
    if (peak_energy_ratio < t["cw_peak_ratio_min"]
            and mod_prominence >= t["mod_prominence_min"] and (in_sprachband or via_syllable)):
        weg = (
            f"mod_peak_hz={mod_peak_hz:.1f} in sprache_mod_hz" if in_sprachband
            else f"syllable_ratio={syllable_ratio:.2f} >= syllable_ratio_min"
        )
        reasons.append(
            f"peak_energy_ratio={peak_energy_ratio:.2f} < cw_peak_ratio_min, "
            f"mod_prominence={mod_prominence:.2f} >= min, {weg} -> SPRACHE"
        )
        return KLASSEN[4], _voice_score(features), reasons, "normal"

    if (peak_energy_ratio < t["cw_peak_ratio_min"]
            and mod_prominence >= t["mod_prominence_min"]
            and syllable_ratio >= t["syllable_ratio_weak_min"]):
        reasons.append(
            f"weak: not tonal, not flat, low syllable ratio "
            f"(syllable_ratio={syllable_ratio:.2f} >= syllable_ratio_weak_min) -> SPRACHE"
        )
        score = round(_voice_score(features) * 0.7, 3)
        return KLASSEN[4], score, reasons, "schwach"

    reasons.append("no cascade stage conclusive -> UNKLAR")
    return KLASSEN[5], _voice_score(features), reasons, "normal"


# ---------------------------------------------------------------------------
# WAV-Lesen, Fensterung, Aggregation
# ---------------------------------------------------------------------------

def _window_starts(duration_s: float):
    if duration_s < WINDOW_S:
        return [0.0], duration_s
    natural_n = int(duration_s // WINDOW_S)
    n_windows = min(MAX_WINDOWS, max(1, natural_n))
    if n_windows == 1:
        return [0.0], WINDOW_S
    return list(np.linspace(0.0, duration_s - WINDOW_S, n_windows)), WINDOW_S


def _aggregate_features(per_window: list) -> dict:
    agg = {}
    min_fsk_idx = int(np.argmin([w["fsk_corr"] for w in per_window]))
    for key in per_window[0]:
        values = [w[key] for w in per_window]
        if key == "rms_dbfs":
            agg[key] = float(np.max(values))
        elif key == "n_lines":
            agg[key] = int(round(float(np.median(values))))
        elif key == "fsk_corr":
            agg[key] = float(np.min(values))
        elif key in ("fsk_pair_lo_hz", "fsk_pair_hi_hz"):
            agg[key] = float(per_window[min_fsk_idx][key])
        else:
            agg[key] = float(np.median(values))
    return agg


def classify_wav(wav_path: str) -> dict:
    """Liest die WAV blockweise (je Fenster ein gezielter Seek+Read, wie
    audio_spectrum.stft_window - nie die ganze Datei auf einmal), fasst
    die Fenster zusammen und wendet die Kaskade an."""
    _check_not_running(wav_path)
    with wave.open(wav_path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        _check_supported(channels, sampwidth)
        sample_rate_hz = wf.getframerate()
        n_frames_total = wf.getnframes()
        duration_s = n_frames_total / sample_rate_hz if sample_rate_hz else 0.0

        starts, window_len_s = _window_starts(duration_s)

        per_window = []
        analyzed_s = 0.0
        for start_s in starts:
            start_frame = min(int(round(start_s * sample_rate_hz)), n_frames_total)
            end_s = min(duration_s, start_s + window_len_s)
            n_read = max(0, int(round((end_s - start_s) * sample_rate_hz)))
            wf.setpos(start_frame)
            raw = wf.readframes(n_read)
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
            per_window.append(compute_features(samples, sample_rate_hz))
            analyzed_s += len(samples) / sample_rate_hz if sample_rate_hz else 0.0

    aggregated = _aggregate_features(per_window)
    klasse, voice_score, reasons, sicherheit = classify_features(aggregated, duration_s)

    return {
        "klasse": klasse, "voice_score": voice_score, "reasons": reasons,
        "sicherheit": sicherheit,
        "windows": len(starts), "analyzed_s": analyzed_s,
        "features": aggregated,
        "sample_rate_hz": sample_rate_hz, "duration_s": duration_s,
    }


# ---------------------------------------------------------------------------
# Sidecar laden/bauen (E7)
# ---------------------------------------------------------------------------

def load_klasse(wav_path: str):
    path = klasse_path_for(wav_path)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("format") != CLASSIFIER_VERSION or data.get("classifier_version") != CLASSIFIER_VERSION:
        return None
    wav_meta = data.get("wav")
    if not isinstance(wav_meta, dict):
        return None
    try:
        actual_size = os.path.getsize(wav_path)
        actual_mtime = os.path.getmtime(wav_path)
    except OSError:
        return None
    if wav_meta.get("size") != actual_size:
        return None
    try:
        if abs(float(wav_meta.get("mtime")) - actual_mtime) > 1e-6:
            return None
    except (TypeError, ValueError):
        return None
    return data


def ensure_klasse(wav_path: str, rebuild: bool = False) -> dict:
    """load_klasse(); bei fehlendem/ungueltigem Sidecar oder rebuild=True
    classify_wav() + atomar schreiben (Muster audio_spectrum.build_audspec:
    Temp-Datei + os.replace)."""
    if not rebuild:
        cached = load_klasse(wav_path)
        if cached is not None:
            return cached

    result = classify_wav(wav_path)
    sidecar = {
        "format": CLASSIFIER_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        "created_iso": datetime.now().astimezone().isoformat(timespec="seconds"),
        "wav": {
            "size": os.path.getsize(wav_path),
            "mtime": os.path.getmtime(wav_path),
            "sample_rate_hz": result["sample_rate_hz"],
            "duration_s": result["duration_s"],
        },
        "klasse": result["klasse"],
        "voice_score": result["voice_score"],
        "reasons": result["reasons"],
        "sicherheit": result["sicherheit"],
        "windows": result["windows"],
        "analyzed_s": result["analyzed_s"],
        "features": result["features"],
        "thresholds": THRESHOLDS,
    }

    path = klasse_path_for(wav_path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return sidecar


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _iter_wav_files(pfad: str):
    p = Path(pfad)
    if p.is_file():
        return [p]
    return sorted(p.rglob("*.wav"))


_TABELLEN_KOPF = (
    f"{'Datei':<42} {'Dauer':>7} {'Klasse':<9} {'score':>5} {'n_l':>3} "
    f"{'mod_hz':>6} {'prom':>5} {'syl':>4} {'flat':>4} {'flux':>4} {'pause':>5} "
    f"{'per':>4} {'key':>4} {'fsk':>5}"
)


def _tabellen_zeile(rel: str, sidecar: dict) -> str:
    feats = sidecar["features"]
    dauer_s = sidecar["wav"]["duration_s"]
    kurz = rel if len(rel) <= 42 else "..." + rel[-39:]
    return (
        f"{kurz:<42} {dauer_s:6.1f}s {sidecar['klasse']:<9} "
        f"{sidecar['voice_score']:5.3f} {feats['n_lines']:3d} "
        f"{feats['mod_peak_hz']:6.1f} {feats['mod_prominence']:5.2f} "
        f"{feats['syllable_ratio']:4.2f} "
        f"{feats['flatness']:4.2f} {feats['flux']:4.2f} {feats['pause_ratio']:5.2f} "
        f"{feats['peak_energy_ratio']:4.2f} {feats['keying_rate']:4.1f} "
        f"{feats['fsk_corr']:5.2f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Audio-Nachklassifikation (T-1007a)")
    ap.add_argument("pfad", nargs="?", help="WAV-Datei oder Verzeichnis (rekursiv)")
    ap.add_argument("--rebuild", action="store_true", help="Sidecar neu berechnen statt zu laden")
    ap.add_argument("--json", action="store_true", help="eine JSON-Zeile je Datei statt Tabelle")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    if not args.pfad:
        ap.print_help()
        return 1

    wav_files = _iter_wav_files(args.pfad)
    if not wav_files:
        print(f"Keine .wav-Dateien unter {args.pfad} gefunden.", file=sys.stderr)
        return 1

    verteilung: dict = {}
    zeilen = []
    for wav_path in wav_files:
        rel = str(wav_path)
        try:
            sidecar = ensure_klasse(rel, rebuild=args.rebuild)
        except AudioClassifyBusyError:
            print(f"uebersprungen (laeuft noch): {rel}", file=sys.stderr)
            continue
        except AudioClassifyUnsupportedError as exc:
            print(f"uebersprungen ({exc}): {rel}", file=sys.stderr)
            continue

        verteilung[sidecar["klasse"]] = verteilung.get(sidecar["klasse"], 0) + 1

        if args.json:
            print(json.dumps({"wav": rel, **{
                k: sidecar[k] for k in
                ("klasse", "voice_score", "reasons", "windows", "analyzed_s", "features")
            }}, ensure_ascii=False))
        else:
            zeilen.append(_tabellen_zeile(rel, sidecar))

    if not args.json:
        print(_TABELLEN_KOPF)
        for zeile in zeilen:
            print(zeile)

    print()
    print("Klassenverteilung:")
    for klasse in sorted(verteilung):
        print(f"  {klasse}: {verteilung[klasse]}")

    return 0


# ---------------------------------------------------------------------------
# Selbsttest (python audio_classify.py --selftest, ohne Hardware)
# ---------------------------------------------------------------------------

def _write_test_wav(path: str, samples_f32, sample_rate_hz: int, channels: int = 1,
                     sampwidth: int = 2) -> None:
    """Reiner Testhelfer - importiert bewusst NICHT eb200_core/audio_wav.py
    (siehe audio_spectrum._write_test_wav: dieses Modul soll unabhaengig
    von eb200_core pruefbar bleiben)."""
    samples_f32 = np.asarray(samples_f32)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate_hz)
        if sampwidth == 2:
            pcm = np.clip(samples_f32 * 32767.0, -32768, 32767).astype("<i2")
        else:
            pcm = np.clip((samples_f32 * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        wf.writeframes(pcm.tobytes())


def _write_test_sidecar(wav_path: str, running: bool) -> None:
    with open(wav_path + ".json", "w", encoding="utf-8") as f:
        json.dump({"running": running}, f)


def _print_features(label: str, features: dict) -> None:
    print(
        f"    {label}: rms={features['rms_dbfs']:.1f}dBFS flat={features['flatness']:.4f} "
        f"n_lines={features['n_lines']} line_ratio={features['line_energy_ratio']:.2f} "
        f"mod_hz={features['mod_peak_hz']:.1f} prom={features['mod_prominence']:.2f} "
        f"syl={features['syllable_ratio']:.2f} "
        f"pause={features['pause_ratio']:.2f} bimod={features['bimodality']:.3f} "
        f"flux={features['flux']:.2f} per={features['peak_energy_ratio']:.2f} "
        f"key={features['keying_rate']:.2f} occ=[{features['occ_lo_hz']:.0f}-{features['occ_hi_hz']:.0f}]Hz "
        f"fsk={features['fsk_corr']:.2f} fsk_pair=[{features['fsk_pair_lo_hz']:.0f}/"
        f"{features['fsk_pair_hi_hz']:.0f}]Hz"
    )


def _bandpass_noise_frame(n: int, sample_rate_hz: int, lo_hz: float, hi_hz: float, rng) -> np.ndarray:
    """Weisses Rauschen, im Frequenzbereich [lo_hz, hi_hz] gleichverteilte
    Zufallsphase, ausserhalb Null (via irfft) - reiner Testsignal-Helfer."""
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    mag = ((freqs >= lo_hz) & (freqs <= hi_hz)).astype(np.float64)
    phase = rng.uniform(0.0, 2.0 * np.pi, size=freqs.size)
    frame = np.fft.irfft(mag * np.exp(1j * phase), n=n)
    rms = float(np.sqrt(np.mean(frame ** 2)))
    return frame / rms if rms > 1e-9 else frame


def _make_sprache_signal(sample_rate_hz: int, duration_s: float, rng) -> np.ndarray:
    """T1: bandbegrenztes Rauschen mit 3-Hz-wanderndem Bandschwerpunkt
    (600..2000 Hz, +/-400 Hz Breite), 4-Hz-Raised-Cosine-Huelle (Tiefe
    0,9), ~20% zufaellige Pausen 150-400 ms."""
    n = int(sample_rate_hz * duration_s)
    frame_len = 320  # 20 ms bei 16 kHz
    chunks = []
    t0 = 0
    while t0 < n:
        cur = min(frame_len, n - t0)
        t_center = (t0 + cur / 2.0) / sample_rate_hz
        center_hz = 1300.0 + 700.0 * math.sin(2.0 * math.pi * 3.0 * t_center)
        chunks.append(_bandpass_noise_frame(cur, sample_rate_hz, center_hz - 400.0, center_hz + 400.0, rng))
        t0 += cur
    signal = np.concatenate(chunks)[:n]

    t = np.arange(n) / sample_rate_hz
    envelope = 1.0 - 0.9 * 0.5 * (1.0 - np.cos(2.0 * np.pi * 4.0 * t))
    signal = signal * envelope

    paused_s = 0.0
    used = np.zeros(n, dtype=bool)
    target_paused_s = 0.20 * duration_s
    guard = 0
    while paused_s < target_paused_s and guard < 200:
        guard += 1
        pause_len_s = rng.uniform(0.150, 0.400)
        pause_len_n = int(pause_len_s * sample_rate_hz)
        if pause_len_n >= n:
            break
        start = int(rng.uniform(0, n - pause_len_n))
        sl = slice(start, start + pause_len_n)
        if used[sl].any():
            continue
        # Sanfte Daempfung statt hartem Nullsetzen: ein abrupter Sprung auf
        # exakt 0 erzeugt eine so tiefe dB-Flanke, dass sie im
        # Modulationsspektrum breitbandig Energie einstreut und den
        # periodischen 4-Hz-Peak (M3) unter Rauschen begraebt.
        signal[sl] *= 0.05
        used[sl] = True
        paused_s += pause_len_s

    rms = float(np.sqrt(np.mean(signal ** 2)))
    if rms > 1e-9:
        signal = signal * (0.3 / rms)
    return signal


def _make_cw_signal(sample_rate_hz: int, duration_s: float, rng) -> np.ndarray:
    """T2: 700-Hz-Ton, Pseudo-Morse (dit 60 ms, dah 180 ms, Zeichenabstand
    180 ms, Wortabstand 420 ms, Element-Innenabstand 60 ms nach
    internationaler Morse-Konvention - 1/3/3/7 Einheiten zu je 60 ms),
    5-ms-Flanken, weisses Rauschen -30 dB."""
    n = int(sample_rate_hz * duration_s)
    unit_s = 0.060
    keying = np.zeros(n, dtype=np.float64)
    pos_s = 0.0
    elements_seit_wort = 0
    while pos_s < duration_s:
        n_elemente = rng.integers(1, 5)
        for i in range(n_elemente):
            # Dah-Uebergewicht (0,7 statt 0,5): kurze Dits liefern bei
            # N_FFT=512/HOP=256 kaum reine "Vollfenster"-Frames (die
            # 5-ms-Flanken dominieren einen 60-ms-Dit), was die
            # Median-Praesenzpruefung von M2 unterschaetzt - mit mehr Dahs
            # bleibt der Duty-Cycle real und "zufaellig", aber die
            # Ton-Erkennung wird reproduzierbar stabil (siehe Gate-3-Report).
            ist_dah = rng.random() < 0.7
            elem_len_s = 0.180 if ist_dah else 0.060
            start = int(pos_s * sample_rate_hz)
            ende = min(n, int((pos_s + elem_len_s) * sample_rate_hz))
            keying[start:ende] = 1.0
            pos_s += elem_len_s
            if i < n_elemente - 1:
                pos_s += unit_s
        elements_seit_wort += 1
        if elements_seit_wort >= 3 and rng.random() < 0.4:
            pos_s += 0.420
            elements_seit_wort = 0
        else:
            pos_s += 0.180
    keying = keying[:n] if keying.size >= n else np.pad(keying, (0, n - keying.size))

    # 5-ms-Flanken per Faltung mit einem Hann-Ramp-Kern (glaettet jede
    # 0/1-Flanke symmetrisch, ohne die Mittellagen zu veraendern).
    ramp_n = max(1, int(0.005 * sample_rate_hz))
    kernel = np.hanning(2 * ramp_n)
    kernel = kernel / kernel.sum()
    keying_smooth = np.convolve(keying, kernel, mode="same")
    keying_smooth = np.clip(keying_smooth, 0.0, 1.0)

    t = np.arange(n) / sample_rate_hz
    tone = np.sin(2.0 * np.pi * 700.0 * t) * keying_smooth
    tone_rms = float(np.sqrt(np.mean(tone ** 2))) or 1.0
    noise = rng.normal(0.0, 1.0, size=n)
    noise_rms = float(np.sqrt(np.mean(noise ** 2))) or 1.0
    noise = noise * (tone_rms * (10.0 ** (-30.0 / 20.0)) / noise_rms)

    signal = tone + noise
    rms = float(np.sqrt(np.mean(signal ** 2)))
    if rms > 1e-9:
        signal = signal * (0.3 / rms)
    return signal


def _make_rtty_signal(sample_rate_hz: int, duration_s: float, rng) -> np.ndarray:
    """T3: 2-Ton-FSK 1275/1445 Hz, 45,45 Bd, Zufallsbits, phasenkontinuierlich."""
    n = int(sample_rate_hz * duration_s)
    baud = 45.45
    bit_len_n = int(round(sample_rate_hz / baud))
    n_bits = n // bit_len_n + 1
    bits = rng.integers(0, 2, size=n_bits)
    freqs = np.where(bits == 1, 1445.0, 1275.0)
    freq_per_sample = np.repeat(freqs, bit_len_n)[:n]
    phase = 2.0 * np.pi * np.cumsum(freq_per_sample) / sample_rate_hz
    signal = np.sin(phase)
    rms = float(np.sqrt(np.mean(signal ** 2)))
    return signal * (0.3 / rms) if rms > 1e-9 else signal


def _make_carrier_signal(sample_rate_hz: int, duration_s: float, freq_hz: float, rng) -> np.ndarray:
    """Unmodulierter Traeger + winziger Rauschboden (-50 dB relativ). Ein
    mathematisch perfekt periodisches, rauschfreies Signal erzeugt bei der
    16-Bit-PCM-Quantisierung einen deterministischen, voll stabilen
    Stoeranteil bei der 3. Harmonischen (n_lines faelschlich 2 statt 1) -
    ein realistischer Rauschboden (wie ihn jede echte Aufnahme hat)
    verhindert das strukturell, ohne die Schwellen weiter aufzuweichen."""
    n = int(sample_rate_hz * duration_s)
    t = np.arange(n) / sample_rate_hz
    signal = np.sin(2.0 * np.pi * freq_hz * t)
    rms = float(np.sqrt(np.mean(signal ** 2)))
    signal = signal * (0.3 / rms) if rms > 1e-9 else signal
    noise = rng.normal(0.0, 1.0, size=n)
    noise_rms = float(np.sqrt(np.mean(noise ** 2))) or 1.0
    signal = signal + noise * (0.3 * (10.0 ** (-50.0 / 20.0)) / noise_rms)
    return signal


def _make_cw_signal_fast(sample_rate_hz: int, duration_s: float, unit_s: float, rng) -> np.ndarray:
    """Wie _make_cw_signal (T2), aber mit parametrierbarer Zeiteinheit (T15:
    30 WPM, dit=40 ms -> unit_s=0.040) - eigenstaendige Kopie, damit
    _make_cw_signal (T2, 20 WPM) unveraendert bleibt."""
    n = int(sample_rate_hz * duration_s)
    keying = np.zeros(n, dtype=np.float64)
    pos_s = 0.0
    elements_seit_wort = 0
    while pos_s < duration_s:
        n_elemente = rng.integers(1, 5)
        for i in range(n_elemente):
            ist_dah = rng.random() < 0.7
            elem_len_s = unit_s * 3.0 if ist_dah else unit_s
            start = int(pos_s * sample_rate_hz)
            ende = min(n, int((pos_s + elem_len_s) * sample_rate_hz))
            keying[start:ende] = 1.0
            pos_s += elem_len_s
            if i < n_elemente - 1:
                pos_s += unit_s
        elements_seit_wort += 1
        if elements_seit_wort >= 3 and rng.random() < 0.4:
            pos_s += unit_s * 7.0
            elements_seit_wort = 0
        else:
            pos_s += unit_s * 3.0
    keying = keying[:n] if keying.size >= n else np.pad(keying, (0, n - keying.size))

    ramp_n = max(1, int(0.005 * sample_rate_hz))
    kernel = np.hanning(2 * ramp_n)
    kernel = kernel / kernel.sum()
    keying_smooth = np.convolve(keying, kernel, mode="same")
    keying_smooth = np.clip(keying_smooth, 0.0, 1.0)

    t = np.arange(n) / sample_rate_hz
    tone = np.sin(2.0 * np.pi * 700.0 * t) * keying_smooth
    tone_rms = float(np.sqrt(np.mean(tone ** 2))) or 1.0
    noise = rng.normal(0.0, 1.0, size=n)
    noise_rms = float(np.sqrt(np.mean(noise ** 2))) or 1.0
    noise = noise * (tone_rms * (10.0 ** (-30.0 / 20.0)) / noise_rms)

    signal = tone + noise
    rms = float(np.sqrt(np.mean(signal ** 2)))
    if rms > 1e-9:
        signal = signal * (0.3 / rms)
    return signal


def _make_ft8_signal(sample_rate_hz: int, duration_s: float, gap_s: float, rng) -> np.ndarray:
    """T14: FT8-Surrogat - 8-FSK, Tonabstand 6,25 Hz, Symbol 160 ms, Basiston
    1500 Hz; gap_s Sekunden Stille in der Mitte (Zyklusgrenze), je zur
    Haelfte davor/danach aktive Symbole, phasenkontinuierlich je Block."""
    symbol_s = 0.160
    symbol_n = int(round(symbol_s * sample_rate_hz))
    active_s = max(0.0, duration_s - gap_s)
    n_symbols_half = max(1, int(round((active_s / 2.0) / symbol_s)))

    def _tones_block(n_symbols):
        tone_idx = rng.integers(0, 8, size=n_symbols)
        freqs = 1500.0 + 6.25 * tone_idx
        phase = 0.0
        chunks = []
        t = np.arange(symbol_n) / sample_rate_hz
        for f in freqs:
            chunks.append(np.sin(2.0 * np.pi * f * t + phase))
            phase = (phase + 2.0 * np.pi * f * symbol_n / sample_rate_hz) % (2.0 * np.pi)
        return np.concatenate(chunks)

    block1 = _tones_block(n_symbols_half)
    block2 = _tones_block(n_symbols_half)
    gap = np.zeros(int(round(gap_s * sample_rate_hz)))
    signal = np.concatenate([block1, gap, block2])

    n_total = int(round(duration_s * sample_rate_hz))
    signal = signal[:n_total] if signal.size >= n_total else np.pad(signal, (0, n_total - signal.size))

    rms = float(np.sqrt(np.mean(signal ** 2)))
    return signal * (0.3 / rms) if rms > 1e-9 else signal


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sr = 16000
        rng = np.random.default_rng(1234)

        # --- T1: Sprach-Surrogat -------------------------------------------
        sprache = _make_sprache_signal(sr, 6.0, rng)
        p1 = os.path.join(tmp, "t1_sprache.wav")
        _write_test_wav(p1, sprache, sr)
        r1 = classify_wav(p1)
        _print_features("T1", r1["features"])
        assert r1["klasse"] == "SPRACHE", (r1["klasse"], r1["reasons"], r1["features"])
        assert r1["voice_score"] >= 0.6, (r1["voice_score"], r1["features"])
        print(f"T1 ok  (Sprach-Surrogat -> SPRACHE, voice_score={r1['voice_score']:.3f} >= 0.6)")

        # --- T2: CW ----------------------------------------------------------
        cw = _make_cw_signal(sr, 6.0, rng)
        p2 = os.path.join(tmp, "t2_cw.wav")
        _write_test_wav(p2, cw, sr)
        r2 = classify_wav(p2)
        _print_features("T2", r2["features"])
        assert r2["klasse"] == "CW", (r2["klasse"], r2["reasons"], r2["features"])
        print("T2 ok  (Pseudo-Morse 700 Hz -> CW)")

        # --- T3: RTTY ----------------------------------------------------------
        rtty = _make_rtty_signal(sr, 6.0, rng)
        p3 = os.path.join(tmp, "t3_rtty.wav")
        _write_test_wav(p3, rtty, sr)
        r3 = classify_wav(p3)
        _print_features("T3", r3["features"])
        assert r3["klasse"] == "DIGITAL", (r3["klasse"], r3["reasons"], r3["features"])
        assert r3["features"]["n_lines"] == 2, r3["features"]

        db_t3, dt_row_t3, df_hz_t3 = audio_spectrum.stft_db(rtty, sr, N_FFT, HOP)
        freqs_t3 = np.arange(db_t3.shape[1]) * df_hz_t3
        band_mask_t3 = _band_mask(freqs_t3, BAND_HZ)
        line_mask_t3 = _band_mask(freqs_t3, LINE_BAND_HZ)
        power_t3 = 10.0 ** (db_t3.astype(np.float64) / 10.0)
        _, _, occ_mask_t3 = _occupied_band(power_t3, freqs_t3, BAND_HZ)
        occ_power_sum_t3 = np.sum(power_t3[:, occ_mask_t3], axis=1)
        occ_power_db_t3 = 10.0 * np.log10(np.maximum(occ_power_sum_t3, _EPS))
        p90_t3 = float(np.percentile(occ_power_db_t3, 90))
        active_mask_t3 = occ_power_db_t3 >= (p90_t3 - THRESHOLDS["pause_drop_db"])
        _, _, presence_debug_t3 = _line_detect(
            db_t3.astype(np.float64), power_t3, band_mask_t3, line_mask_t3, active_mask_t3
        )
        presences_t3 = sorted(p for _, p in presence_debug_t3)
        print(f"    T3 line_presence (alle Peak-Kandidaten): {[round(p, 3) for p in presences_t3]}")
        assert len(presences_t3) >= 2 and all(
            p >= THRESHOLDS["line_presence_min"] for p in presences_t3[-2:]
        ), presences_t3
        print(f"    T3 Grund: {r3['reasons']}")
        print(f"    T3 fsk_corr={r3['features']['fsk_corr']:.2f} "
              f"fsk_pair={r3['features']['fsk_pair_lo_hz']:.0f}/{r3['features']['fsk_pair_hi_hz']:.0f} Hz")
        assert r3["features"]["fsk_corr"] <= THRESHOLDS["fsk_corr_max"], r3["features"]
        print("T3 ok  (RTTY 1275/1445 Hz -> DIGITAL ueber Flachheit oder Linien, n_lines == 2, "
              "line_presence beider Toene >= 0.60, fsk_corr <= fsk_corr_max)")

        # --- T4: unmodulierter Traeger ------------------------------------------
        carrier = _make_carrier_signal(sr, 6.0, 1000.0, rng)
        p4 = os.path.join(tmp, "t4_carrier.wav")
        _write_test_wav(p4, carrier, sr)
        r4 = classify_wav(p4)
        _print_features("T4", r4["features"])
        assert r4["klasse"] == "DIGITAL", (r4["klasse"], r4["reasons"], r4["features"])
        assert r4["features"]["n_lines"] == 1, r4["features"]
        print("T4 ok  (unmodulierter 1000-Hz-Traeger -> DIGITAL, n_lines == 1)")

        # --- T5: weisses Rauschen -20 dBFS ---------------------------------------
        n5 = int(sr * 6.0)
        white = rng.normal(0.0, 1.0, size=n5)
        white = white * (10.0 ** (-20.0 / 20.0) / (float(np.sqrt(np.mean(white ** 2))) or 1.0))
        p5 = os.path.join(tmp, "t5_rauschen.wav")
        _write_test_wav(p5, white, sr)
        r5 = classify_wav(p5)
        _print_features("T5", r5["features"])
        assert r5["klasse"] == "RAUSCHEN", (r5["klasse"], r5["reasons"], r5["features"])
        assert r5["features"]["flatness"] >= 0.85, r5["features"]
        print("T5 ok  (weisses Rauschen -20 dBFS -> RAUSCHEN, flatness >= 0.85)")

        # --- T6: Stille --------------------------------------------------------
        n6 = int(sr * 6.0)
        silence = rng.normal(0.0, 1.0, size=n6)
        silence = silence * (10.0 ** (-80.0 / 20.0) / (float(np.sqrt(np.mean(silence ** 2))) or 1.0))
        p6 = os.path.join(tmp, "t6_stille.wav")
        _write_test_wav(p6, silence, sr)
        r6 = classify_wav(p6)
        _print_features("T6", r6["features"])
        assert r6["klasse"] == "LEER", (r6["klasse"], r6["reasons"], r6["features"])
        assert r6["voice_score"] == 0.0, r6["voice_score"]
        print("T6 ok  (Stille -80 dBFS -> LEER, voice_score == 0)")

        # --- T7: T1-Signal, aber nur 0,8 s ---------------------------------------
        kurz = _make_sprache_signal(sr, 0.8, rng)
        p7 = os.path.join(tmp, "t7_kurz.wav")
        _write_test_wav(p7, kurz, sr)
        r7 = classify_wav(p7)
        _print_features("T7", r7["features"])
        assert r7["klasse"] == "UNKLAR", (r7["klasse"], r7["reasons"], r7["features"])
        assert any("too short" in reason for reason in r7["reasons"]), r7["reasons"]
        print("T7 ok  (0,8s Sprach-Surrogat -> UNKLAR, Grund 'too short')")

        # --- T8: running=True -> AudioClassifyBusyError -------------------------
        _write_test_sidecar(p5, running=True)
        try:
            ensure_klasse(p5)
        except AudioClassifyBusyError:
            pass
        else:
            raise AssertionError("AudioClassifyBusyError erwartet (running=True)")
        _write_test_sidecar(p5, running=False)
        print("T8 ok  (running=True neben T5 -> AudioClassifyBusyError)")

        # --- T9: Sidecar-Roundtrip + mtime-Invalidierung ------------------------
        _write_test_sidecar(p2, running=False)
        first = ensure_klasse(p2)
        second = ensure_klasse(p2)
        assert second["created_iso"] == first["created_iso"], (first["created_iso"], second["created_iso"])
        future = os.path.getmtime(p2) + 5
        os.utime(p2, (future, future))
        assert load_klasse(p2) is None, "Cache haette durch veraenderte mtime ungueltig werden muessen"
        print("T9 ok  (ensure_klasse zweimal: kein Neubau, created_iso identisch; mtime-Aenderung -> load_klasse None)")

        # --- T10: Stereo-WAV -> AudioClassifyUnsupportedError -------------------
        stereo_samples = np.zeros(sr, dtype=np.float64)
        stereo_path = os.path.join(tmp, "t10_stereo.wav")
        _write_test_wav(stereo_path, np.repeat(stereo_samples, 2), sr, channels=2)
        try:
            ensure_klasse(stereo_path)
        except AudioClassifyUnsupportedError:
            pass
        else:
            raise AssertionError("AudioClassifyUnsupportedError erwartet (Stereo)")
        print("T10 ok  (Stereo-WAV -> AudioClassifyUnsupportedError)")

        # --- T11: T1-Signal + langsames Fading (Regressionsfall T-1007a) -------
        sprache_fading = _make_sprache_signal(sr, 6.0, rng)
        t11 = np.arange(sprache_fading.size) / sr
        fading_env = 1.0 - 0.5 * 0.5 * (1.0 - np.cos(2.0 * np.pi * 1.2 * t11))
        sprache_fading = sprache_fading * fading_env
        p11 = os.path.join(tmp, "t11_fading.wav")
        _write_test_wav(p11, sprache_fading, sr)
        r11 = classify_wav(p11)
        _print_features("T11", r11["features"])
        assert r11["klasse"] == "SPRACHE", (r11["klasse"], r11["reasons"], r11["features"])
        print("T11 ok  (Sprach-Surrogat + 1,2-Hz-Fading Tiefe 0,5 -> SPRACHE, Regressionsfall T-1007a)")

        # --- T12: T5-Rauschen + 1,5-Hz-AM (Rauschburst, Regressionsfall) -------
        white_burst = rng.normal(0.0, 1.0, size=n5)
        white_burst = white_burst * (10.0 ** (-20.0 / 20.0) / (float(np.sqrt(np.mean(white_burst ** 2))) or 1.0))
        t12 = np.arange(white_burst.size) / sr
        burst_env = 1.0 - 0.6 * 0.5 * (1.0 - np.cos(2.0 * np.pi * 1.5 * t12))
        white_burst = white_burst * burst_env
        p12 = os.path.join(tmp, "t12_rauschburst.wav")
        _write_test_wav(p12, white_burst, sr)
        r12 = classify_wav(p12)
        _print_features("T12", r12["features"])
        assert r12["klasse"] == "RAUSCHEN", (r12["klasse"], r12["reasons"], r12["features"])
        print("T12 ok  (Rauschen + 1,5-Hz-AM Tiefe 0,6 (Rauschburst) -> RAUSCHEN, nicht SPRACHE)")

        # --- T13: bandbegrenztes (USB-)Rauschen 300-2700 Hz ---------------------
        n13 = int(sr * 6.0)
        usb_noise = _bandpass_noise_frame(n13, sr, 300.0, 2700.0, rng)
        usb_noise = usb_noise * (10.0 ** (-20.0 / 20.0) / (float(np.sqrt(np.mean(usb_noise ** 2))) or 1.0))
        p13 = os.path.join(tmp, "t13_usb_rauschen.wav")
        _write_test_wav(p13, usb_noise, sr)
        r13 = classify_wav(p13)
        _print_features("T13", r13["features"])
        assert r13["klasse"] == "RAUSCHEN", (r13["klasse"], r13["reasons"], r13["features"])
        assert r13["features"]["flatness"] >= 0.85, r13["features"]
        print("T13 ok  (bandbegrenztes USB-Rauschen 300-2700 Hz -> RAUSCHEN, flatness >= 0.85 "
              "dank belegtem Band)")

        # --- T14: FT8-Surrogat mit Zyklusluecke ----------------------------------
        ft8 = _make_ft8_signal(sr, 6.0, 1.5, rng)
        p14 = os.path.join(tmp, "t14_ft8.wav")
        _write_test_wav(p14, ft8, sr)
        r14 = classify_wav(p14)
        _print_features("T14", r14["features"])
        print(f"    T14 Grund: {r14['reasons']}")
        assert r14["klasse"] == "DIGITAL", (r14["klasse"], r14["reasons"], r14["features"])
        assert r14["features"]["keying_rate"] < THRESHOLDS["cw_keying_rate_min"], r14["features"]
        print("T14 ok  (FT8-Surrogat 8-FSK + 1,5s Zyklusluecke -> DIGITAL, nicht CW, "
              f"keying_rate={r14['features']['keying_rate']:.2f} < cw_keying_rate_min)")

        # --- T15: CW bei 30 WPM (dit=40 ms) --------------------------------------
        cw_fast = _make_cw_signal_fast(sr, 6.0, 0.040, rng)
        p15 = os.path.join(tmp, "t15_cw_30wpm.wav")
        _write_test_wav(p15, cw_fast, sr)
        r15 = classify_wav(p15)
        _print_features("T15", r15["features"])
        assert r15["klasse"] == "CW", (r15["klasse"], r15["reasons"], r15["features"])
        assert r15["features"]["keying_rate"] >= THRESHOLDS["cw_keying_rate_min"], r15["features"]
        print("T15 ok  (Pseudo-Morse 700 Hz, 30 WPM -> CW, "
              f"keying_rate={r15['features']['keying_rate']:.2f} >= cw_keying_rate_min)")

        # --- T16: RTTY + Rauschen bei 0 dB SNR -----------------------------------
        rtty16 = _make_rtty_signal(sr, 6.0, rng)
        noise16 = rng.normal(0.0, 1.0, size=rtty16.size)
        sig_rms16 = float(np.sqrt(np.mean(rtty16 ** 2))) or 1.0
        noise_rms16 = float(np.sqrt(np.mean(noise16 ** 2))) or 1.0
        noise16 = noise16 * (sig_rms16 / noise_rms16)  # gleiche Leistung -> 0 dB SNR
        mix16 = rtty16 + noise16
        rms16 = float(np.sqrt(np.mean(mix16 ** 2)))
        if rms16 > 1e-9:
            mix16 = mix16 * (0.3 / rms16)
        p16 = os.path.join(tmp, "t16_rtty_noise.wav")
        _write_test_wav(p16, mix16, sr)
        r16 = classify_wav(p16)
        _print_features("T16", r16["features"])
        print(f"    T16 Grund: {r16['reasons']}")
        assert r16["klasse"] == "DIGITAL", (r16["klasse"], r16["reasons"], r16["features"])
        print("T16 ok  (RTTY + Rauschen bei 0 dB SNR -> DIGITAL)")

        # --- T17: Sprache -> kein FSK-Fehlalarm -----------------------------------
        sprache17 = _make_sprache_signal(sr, 6.0, rng)
        p17 = os.path.join(tmp, "t17_sprache_fsk.wav")
        _write_test_wav(p17, sprache17, sr)
        r17 = classify_wav(p17)
        _print_features("T17", r17["features"])
        assert r17["features"]["fsk_corr"] > THRESHOLDS["fsk_corr_max"], r17["features"]
        print("T17 ok  (Sprach-Surrogat -> fsk_corr > fsk_corr_max, kein FSK-Fehlalarm)")

    print("\n[audio_classify] Selbsttest erfolgreich abgeschlossen.")
    print("Selftest OK")


if __name__ == "__main__":
    sys.exit(main())
