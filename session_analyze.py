#!/usr/bin/env python3
"""
session_analyze.py

Auto-Analyse-Modul fuer die Rohdaten-Session-DBs aus
eb200_core/session_store.py (Schema session/segment/sweep). Bildet exakt den
Auswerteweg nach, der sich bei der manuellen Analyse einer 42-50-MHz-Session
bewaehrt hat, und liefert pro Segment einen strukturierten Befund:

  1. Laden der Segment-Matrix (Zeit x Bin) als ROHE int16-Geraetewerte
     (Deci-dBuV). Die Division durch 10 erfolgt AUSSCHLIESSLICH im Report/
     Plot-Pfad - nie beim Rechnen auf den Rohwerten (analog Anzeigepfad).
  2. Sentinel-Maskierung: Bins, die in JEDEM Sweep denselben, unplausibel
     hohen Wert tragen (z. B. der Overshoot-Bin hinter der Stoppfrequenz mit
     konstant 200,0 dBuV / Rohwert 2000), werden erkannt und ausgeblendet.
  3. Rauschgrund je Bin (robustes 20-%-Perzentil).
  4. Dauertraeger: Bins mit Median deutlich ueber dem lokalen Grundpegel.
  5. Bursthafte Kanaele: Tastgrad ueber Grundpegel + Marge, nach Aktivitaet.
  6. Kamm-Erkennung: FFT des entrendeten Mittelspektrums -> dominanter
     Linienabstand; zusaetzlich ein Gleichfoermigkeitsmass, das einen ueber
     den ganzen Span konstanten Kamm als moegliches Mess-/Scalloping-
     Artefakt kennzeichnet (statt ihn als echte Funkkanaele zu verkaufen).
  7. Zeitliche Autokorrelation: Gesamtband-Huellkurve und die staerksten
     Kanaele auf feste Wiederkehrperioden pruefen (Baken-/Poll-Zyklen).
  7a. Periodische Emitter: Zwei-Schwellen-Clustering aktiver Bins, Onset-
      Intervalle, Autokorrelation und Epoch Folding kombiniert zu Periode/Jitter/Wahrscheinlichkeit je Aussendung.
  8. Staerkste Einzelereignisse (Max-Hold-Spitzen mit Frequenz/Pegel/Zeit).

Design:
  - Rein LESEND: alle Verbindungen ueber "file:...?mode=ro" (uri=True).
    Keinerlei Schreibzugriff auf irgendeine Session-DB.
  - Eigenstaendig: kein Import aus waterfall.py/session_store.py/db.py; nur
    das bekannte Schema wird vorausgesetzt. So bleibt das Modul fuer sich
    testbar (siehe _selftest) und spaeter aus analyze_sessions.py heraus
    aufrufbar (als "weitere Analysefunktion").
  - Abhaengigkeiten: numpy Pflicht (Zahlenschieben, FFT, Autokorrelation) -
    wie schon im SDR-Pfad (sdr_waterfall.py) bewusst erlaubt, da reines
    Offline-Werkzeug ausserhalb des stdlib-only-Live-Empfangspfads.
    matplotlib ist OPTIONAL und wird nur fuer --plots gebraucht; ohne
    matplotlib laeuft die komplette Analyse samt JSON/Textreport weiter.
  - Nur Standardbibliothek ausser numpy/(matplotlib): sqlite3, argparse,
    json, os, sys, time, array.

Aufruf:
    python session_analyze.py sessions/session_YYYYMMDD_HHMMSS.db
    python session_analyze.py <db> --json befund.json --plots out/
    python session_analyze.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from array import array

from eb200_core.spectrum_reader import SpectrumRecording

import numpy as np

# ---------------------------------------------------------------------------
# Feste Parameter (bewusst benannt, damit nachvollziehbar/justierbar)
# ---------------------------------------------------------------------------

REQUIRED_TABLES = {"session", "segment", "sweep"}

# Sentinel-Erkennung: ein Bin gilt als Sentinel, wenn er in JEDEM Sweep exakt
# denselben Rohwert traegt UND dieser Wert >= diesem Grenzpegel liegt. 100
# dBuV (= Rohwert 1000) ist off-air praktisch nie ein echter Dauerpegel, so
# dass der Overshoot-Marker (200,0 dBuV) sicher gefasst wird, ohne einen
# echten starken Dauertraeger zu verwerfen.
SENTINEL_MIN_DECI = 1000          # 100,0 dBuV in Deci-dBuV

FLOOR_PCT = 20.0                  # Rauschgrund je Bin = dieses Perzentil
CARRIER_EXCESS_DB = 3.0           # Dauertraeger: Median so weit ueber lokalem Grund
CARRIER_BASELINE_W = 41           # Fensterbreite (Bins) fuer lokalen Grundpegel
ACTIVITY_MARGIN_DB = 6.0          # "aktiv" = Rohpegel ueber Grund + diese Marge
BURST_DUTY_MIN = 0.005            # bursthaft: Tastgrad mind. ...
BURST_DUTY_MAX = 0.60             # ... und hoechstens (darueber -> Dauertraeger)

COMB_MIN_KHZ = 20.0               # plausibler Kamm-Abstandsbereich
COMB_MAX_KHZ = 600.0
COMB_MIN_POWER_REL = 0.15         # Kamm gilt als vorhanden ab dieser relativen
                                  # Spektralleistung des staerksten Kamm-Peaks
COMB_UNIFORMITY_ARTEFACT = 0.60   # ab dieser Gleichfoermigkeit -> Artefakt-Flag

AUTOCORR_MIN_LAG_S = 0.30         # Autokorrelation: kleinster/groesster Lag
AUTOCORR_MAX_LAG_S = 120.0
AUTOCORR_PEAK_MIN_R = 0.30        # ab diesem r gilt eine Periode als "deutlich"

N_TOP_BURSTS = 12
N_TOP_EVENTS = 8
N_TOP_AUTOCORR_CHANNELS = 5

EMIT_UP_DB = 4.5             # Emitter-Clustering: Bin startet Cluster ab floor + ...
EMIT_ALMOST_DB = 3.5         # ... und Cluster darf sich ueber Bins ab floor + ... erstrecken
EMIT_K_SIGMA = 5.0           # adaptive Schwelle: Bursts muessen mind. k * Rausch-
                              # Sigma (MAD je Bin) ueber dem Floor liegen; die festen
                              # dB-Werte oben sind nur noch Untergrenzen (Floor = 20-%-
                              # Perzentil liegt schon ~0,84 Sigma unter dem Rauschmittel,
                              # feste dB-Schwellen allein waeren bei hoher Rauschstreuung
                              # zu niedrig und wuerden staendig von selbst ausgeloest)
EMIT_GAP_BINS = 1            # erlaubte Luecke (Bins) innerhalb eines Clusters
EMIT_MAX_BINS = 16           # Plausibilitaetsdeckel (~120 kHz bei 7,5-kHz-Raster):
                              # breitere Cluster sind Bandhebungen, kein periodischer
                              # Einzel-Emitter - gehoeren zur Band-Autokorrelation
EMIT_MEDIAN_W = 1            # Medianfilter in Zeitrichtung (Sweeps) vor Flankenzaehlung;
                              # 1 = aus - kurze D-SCAN-Bursts dauern oft nur einen Sweep,
                              # Rauschspitzen werden ueber EMIT_MIN_ONSETS und die
                              # Kettenvalidierung (_chain_fraction) ausgesiebt
EMIT_MIN_ONSETS = 3          # Emitter braucht mindestens so viele Einschaltflanken
EMIT_MIN_CYCLES = 3          # Periode muss mindestens so oft in die Dauer passen
EMIT_CHAIN_TOL = 0.15        # Ketten-Validierung: Intervall = Periode +/- 15 %
EMIT_FOLD_NBINS = 16         # Epoch Folding: Phasen-Bins
EMIT_FOLD_REFINE = 0.10      # Folding-Suchfenster +/- 10 % um die Grobperiode
N_TOP_EMITTERS = 8           # max. berichtete periodische Emitter je Segment

# Versionsstempel des Befund-Schemas/-Inhalts. Bei JEDER Aenderung, die den
# Befundinhalt aendert (neue Felder, andere Algorithmen/Parameter), um 1
# erhoehen. MUSS mit EXPECTED_ANALYSIS_VERSION in analyze_sessions.py und
# analyze_spectrum.py uebereinstimmen - die Server regenerieren Sidecars
# mit abweichender/fehlender Version automatisch (Gate-Pruefung im Repo).
# Version 3 (T-1829): comb.verdict ist ein Code statt eines deutschen Satzes.
ANALYSIS_VERSION = 3


# ---------------------------------------------------------------------------
# DB-Zugriff (ausschliesslich lesend)
# ---------------------------------------------------------------------------

class InvalidSessionDBError(Exception):
    """Datei ist keine gueltige Session-DB (Schema fehlt/kaputt)."""


def open_session_ro(path: str) -> sqlite3.Connection:
    """Oeffnet path streng read-only und prueft das Schema."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES <= tables:
            conn.close()
            raise InvalidSessionDBError(path)
    except sqlite3.Error as exc:
        raise InvalidSessionDBError(path) from exc
    return conn


def list_segments(conn: sqlite3.Connection):
    """
    Alle Segmente in Reihenfolge als Liste von dict-Metadaten. Bewusst
    SELECT * (statt einer festen Spaltenliste wie sonst im Modul ueblich):
    macht diese Funktion automatisch tolerant gegenueber Alt-DBs OHNE die
    Spalten speed/mtime_s (fehlen dort schlicht im dict) UND Neu-DBs MIT
    ihnen (siehe eb200_core/session_store.py) - Aufrufer pruefen die
    Existenz per "in seg" (row.keys()-Aequivalent auf dem dict), siehe
    analyze_session()/format_text().
    """
    return [dict(r) for r in conn.execute("SELECT * FROM segment ORDER BY id ASC")]


def load_segment_matrix(conn: sqlite3.Connection, seg: dict):
    """
    Laedt alle Sweeps eines Segments in seq-Reihenfolge und gibt zurueck:
      M    : int16-Matrix [n_sweeps, n_bins], ROHE Deci-dBuV
      t    : float64-Vektor [n_sweeps] mit t_unix
      freq : int64-Vektor  [n_bins] mit Bin-Mittenfrequenzen in Hz
    """
    n_bins = int(seg["n_bins"])
    cur = conn.execute(
        "SELECT t_unix, levels FROM sweep WHERE segment_id = ? ORDER BY seq ASC",
        (seg["id"],))
    ts = []
    blobs = []
    for t_unix, blob in cur:
        ts.append(t_unix)
        blobs.append(blob)
    n = len(blobs)
    M = np.empty((n, n_bins), dtype=np.int16)
    for k, blob in enumerate(blobs):
        a = array('h')
        a.frombytes(blob)
        M[k, :] = np.frombuffer(a.tobytes(), dtype='<i2')
    t = np.asarray(ts, dtype=np.float64)
    freq = int(seg["start_hz"]) + np.arange(n_bins, dtype=np.int64) * int(seg["step_hz"])
    return M, t, freq


# ---------------------------------------------------------------------------
# Analyse-Bausteine (arbeiten auf ROHEN Deci-dBuV, /10 erst im Report)
# ---------------------------------------------------------------------------

def detect_sentinel_bins(M: np.ndarray):
    """
    Findet Bins, die in JEDEM Sweep exakt denselben Wert >= SENTINEL_MIN_DECI
    tragen (Overshoot-/Ungueltig-Marker des Geraets). Gibt eine Bool-Maske
    (True = behalten) und die Liste der Sentinel-Bin-Indizes zurueck.
    """
    if M.shape[0] == 0:
        return np.ones(M.shape[1], dtype=bool), []
    first = M[0, :]
    constant = np.all(M == first[None, :], axis=0)
    sentinel = constant & (first >= SENTINEL_MIN_DECI)
    keep = ~sentinel
    return keep, np.where(sentinel)[0].tolist()


def local_baseline(x: np.ndarray, w: int) -> np.ndarray:
    """Lokaler Grundpegel als gleitender Median (Fensterbreite w, ungerade)."""
    if w % 2 == 0:
        w += 1
    pad = np.pad(x, w // 2, mode="edge")
    from numpy.lib.stride_tricks import sliding_window_view
    return np.median(sliding_window_view(pad, w), axis=1)


def _group_runs(mask: np.ndarray):
    """Zusammenhaengende True-Laeufe in mask als Liste von (start, stop)."""
    idx = np.where(mask)[0]
    out = []
    if idx.size == 0:
        return out
    s = p = idx[0]
    for i in idx[1:]:
        if i == p + 1:
            p = i
        else:
            out.append((int(s), int(p)))
            s = p = i
    out.append((int(s), int(p)))
    return out


def find_carriers(D: np.ndarray, freq: np.ndarray, duty: np.ndarray):
    """
    Dauertraeger: Bins, deren Zeit-Median deutlich (CARRIER_EXCESS_DB) ueber
    dem lokalen Spektralgrund liegt; benachbarte Bins zu Gruppen gefasst.
    D ist bereits in dBuV. Rueckgabe: Liste dicts je Traegergruppe.
    """
    p50 = np.median(D, axis=0)
    base = local_baseline(p50, CARRIER_BASELINE_W)
    excess = p50 - base
    out = []
    for a, b in _group_runs(excess > CARRIER_EXCESS_DB):
        seg = slice(a, b + 1)
        peak = a + int(np.argmax(p50[seg]))
        out.append({
            "f_lo_hz": int(freq[a]), "f_hi_hz": int(freq[b]),
            "f_peak_hz": int(freq[peak]),
            "median_dbuv": round(float(p50[peak]), 1),
            "excess_db": round(float(excess[peak]), 1),
            "duty_pct": round(float(duty[peak] * 100.0), 1),
        })
    out.sort(key=lambda d: -d["median_dbuv"])
    return out


def find_bursts(D: np.ndarray, freq: np.ndarray, floor: np.ndarray,
                carrier_mask: np.ndarray):
    """
    Bursthafte Kanaele: Tastgrad ueber floor+ACTIVITY_MARGIN_DB im Fenster
    [BURST_DUTY_MIN, BURST_DUTY_MAX], Dauertraeger ausgeklammert. Rueckgabe:
    nach Tastgrad absteigend sortierte Liste (bis N_TOP_BURSTS).
    """
    thr = floor + ACTIVITY_MARGIN_DB
    active = D > thr[None, :]
    duty = active.mean(axis=0)
    bursty = (~carrier_mask) & (duty > BURST_DUTY_MIN) & (duty < BURST_DUTY_MAX)
    out = []
    for a, b in _group_runs(bursty):
        seg = slice(a, b + 1)
        peak = a + int(np.argmax(duty[seg]))
        out.append({
            "f_peak_hz": int(freq[peak]),
            "duty_pct": round(float(duty[peak] * 100.0), 1),
            "peak_dbuv": round(float(D[:, peak].max()), 1),
        })
    out.sort(key=lambda d: -d["duty_pct"])
    return out[:N_TOP_BURSTS], duty


# Kamm-Urteil (T-1829): detect_comb schreibt einen sprachneutralen Code nach
# comb["verdict"]; die Seiten :8020/:8040 formulieren daraus selbst. Diese
# Tabelle liefert nur den deutschen Klartext fuer den Textbericht (Konsole).
COMB_VERDICT_TEXT = {
    "none": "kein Kamm",
    "possible_artifact": ("sehr regelmaessiger Kamm ueber den ganzen Span "
                          "-> moegliches Mess-/Scalloping-Artefakt, "
                          "off-air verifizieren"),
    "likely_channels": ("Kamm vorhanden, weniger regelmaessig "
                        "-> eher echte kanalisierte Aktivitaet"),
}


def detect_comb(D: np.ndarray, step_hz: int):
    """
    Kamm-Erkennung ueber die RAEUMLICHE Autokorrelation des entrendeten
    Mittelspektrums (robuster als reines FFT-Peak-Picking: bei einem scharfen
    Kamm dominieren sonst leicht die Oberwellen). Der erste deutliche
    Autokorrelations-Peak im plausiblen Abstandsbereich ist der GRUNDABSTAND
    (kleinster Lag unter den etwa gleich starken Peaks - Oberwellen liegen bei
    Vielfachen davon). Die Peakhoehe dient als Regelmaessigkeitsmass: ein sehr
    regelmaessiger Kamm ueber den ganzen Span wird als moegliches Mess-/
    Scalloping-Artefakt gekennzeichnet, nicht als echte Funkkanaele.
    "verdict" ist ein Code aus COMB_VERDICT_TEXT (none, possible_artifact,
    likely_channels), kein Anzeigetext (T-1829).
    """
    mean_spec = D.mean(axis=0)
    # Slow shape (Bandhuellkurve) per breitem gleitendem Median entfernen,
    # damit die Autokorrelation die Kamm-Welligkeit sieht, nicht die Huellkurve.
    x = mean_spec - local_baseline(mean_spec, 81)
    r = _autocorr(x)                                  # Lag in Bins
    lag_min = max(2, int(round(COMB_MIN_KHZ * 1000.0 / step_hz)))
    lag_max = min(r.size - 2, int(round(COMB_MAX_KHZ * 1000.0 / step_hz)))
    result = {"present": False, "spacing_khz": None,
              "regularity": 0.0, "verdict": "none"}
    if lag_max <= lag_min:
        return result
    band = r[lag_min:lag_max + 1]
    peak_val = float(band.max())
    if peak_val < COMB_MIN_POWER_REL:
        result["regularity"] = round(peak_val, 3)
        return result
    # Kleinsten Lag unter den ~gleich starken lokalen Maxima nehmen = Grundwelle
    fundamental = None
    for lag in range(lag_min, lag_max + 1):
        if (r[lag] >= 0.80 * peak_val
                and r[lag] > r[lag - 1] and r[lag] >= r[lag + 1]):
            fundamental = lag
            break
    if fundamental is None:
        fundamental = lag_min + int(np.argmax(band))
    result["present"] = True
    result["spacing_khz"] = round(fundamental * step_hz / 1000.0, 1)
    result["regularity"] = round(peak_val, 3)
    if peak_val >= COMB_UNIFORMITY_ARTEFACT:
        result["verdict"] = "possible_artifact"
    else:
        result["verdict"] = "likely_channels"
    return result


def _autocorr(x: np.ndarray) -> np.ndarray:
    """Normierte Autokorrelation (Lag 0 == 1.0)."""
    x = x - x.mean()
    s = x.std()
    if s < 1e-9:
        return np.zeros_like(x)
    x = x / s
    r = np.correlate(x, x, mode="full")[x.size - 1:]
    return r / r[0]


def autocorr_top_lags(env: np.ndarray, dt: float):
    """Deutliche lokale Autokorrelations-Maxima im erlaubten Lag-Fenster."""
    r = _autocorr(env)
    lags = np.arange(r.size) * dt
    peaks = []
    for i in range(1, r.size - 1):
        lag = lags[i]
        if lag < AUTOCORR_MIN_LAG_S or lag > AUTOCORR_MAX_LAG_S:
            continue
        if r[i] > r[i - 1] and r[i] >= r[i + 1] and r[i] > 0.08:
            peaks.append((round(float(lag), 2), round(float(r[i]), 3)))
    peaks.sort(key=lambda p: -p[1])
    return peaks[:6]


def _median_filter_time(A: np.ndarray, w: int) -> np.ndarray:
    """
    Gleitender Median entlang Achse 0 (Zeit/Sweeps) einer 2D-Bool/Float-Matrix
    ueber ein Fenster von w Sweeps (w wird bei Bedarf auf ungerade aufgerundet,
    Randpolsterung "edge" wie in local_baseline). Entfernt Einzelausreisser
    (ein Sweep), bevor Flanken gezaehlt werden.
    """
    if w % 2 == 0:
        w += 1
    A = A.astype(np.float64)
    if w <= 1 or A.shape[0] < 2:
        return A
    pad = np.pad(A, ((w // 2, w // 2), (0, 0)), mode="edge")
    from numpy.lib.stride_tricks import sliding_window_view
    windows = sliding_window_view(pad, w, axis=0)
    return np.median(windows, axis=-1)


def _cluster_emitter_bins(duty_up: np.ndarray, duty_almost: np.ndarray,
                           maxhold_db: np.ndarray, cand: np.ndarray, gap: int):
    """
    Zwei-Schwellen-Clustering mit Hysterese ueber die Frequenzachse, nur ueber
    Kandidatenbins (cand: Flanken >= EMIT_MIN_ONSETS und duty_up < BURST_DUTY_MAX).
    Nicht-Kandidaten zaehlen unabhaengig von duty_up/duty_almost als Luecke. Ein
    Lauf beginnt an einem Kandidatenbin mit duty_up > 0, darf sich ueber
    Kandidatenbins mit duty_almost > 0 fortsetzen und bis zu 'gap' Bins ohne
    Kandidatenstatus ueberbruecken. Rueckgabe Liste (start, stop) wie _group_runs.
    Anschliessend wird jedes Cluster im Max-Hold-Profil an tiefen Taelern
    (>= 6 dB zwischen zwei lokalen Maxima) geteilt, damit eng benachbarte
    Einzelkanaele (z. B. 162.45/162.475/162.50 MHz bei 7.5-kHz-Raster) nicht
    zu einem Breitband-Emitter verschmelzen.
    """
    def local_maxima(seg: np.ndarray):
        """Indizes lokaler Maxima; ein Plateau zaehlt als ein Maximum (erster Index)."""
        n = seg.size
        peaks = []
        i = 0
        while i < n:
            j = i
            while j + 1 < n and seg[j + 1] == seg[i]:
                j += 1
            left_lower = (i == 0) or (seg[i] > seg[i - 1])
            right_lower = (j == n - 1) or (seg[j] > seg[j + 1])
            if left_lower and right_lower:
                peaks.append(i)
            i = j + 1
        return peaks

    def split_at_valleys(a: int, b: int):
        """Teilt (a, b) rekursiv an Taelern >= 6 dB zwischen zwei lokalen Maxima."""
        if b <= a:
            return [(a, b)]
        seg = maxhold_db[a:b + 1]
        peaks = local_maxima(seg)
        if len(peaks) < 2:
            return [(a, b)]
        for k in range(len(peaks) - 1):
            p1, p2 = peaks[k], peaks[k + 1]
            if p2 <= p1 + 1:
                continue
            valley = p1 + 1 + int(np.argmin(seg[p1 + 1:p2]))
            depth = float(min(seg[p1], seg[p2]) - seg[valley])
            if depth >= 6.0:
                left = split_at_valleys(a, a + valley)
                right = split_at_valleys(a + valley + 1, b)
                return left + right
        return [(a, b)]

    nb = duty_up.size
    raw = []
    i = 0
    while i < nb:
        if cand[i] and duty_up[i] > 0:
            start = end = i
            j = i + 1
            gap_used = 0
            while j < nb:
                if cand[j] and (duty_almost[j] > 0 or duty_up[j] > 0):
                    end = j
                    gap_used = 0
                    j += 1
                elif gap_used < gap:
                    gap_used += 1
                    j += 1
                else:
                    break
            raw.append((start, end))
            i = end + 1
        else:
            i += 1

    out = []
    for a, b in raw:
        out.extend(split_at_valleys(a, b))
    return out


def _onsets_from_activity(act: np.ndarray, t_rel: np.ndarray):
    """
    Einschaltflanken (Uebergang False->True) und Burstdauern aus einer
    1D-Aktivitaetsreihe (bereits medianfgefiltert). Dauer = Zeit bis zur
    naechsten True->False-Flanke; der letzte, noch offene Burst zaehlt bis
    zum Ende der Reihe. Rueckgabe (onset_times, on_durations) in Sekunden.
    """
    act = act.astype(bool)
    if act.size == 0:
        return np.array([]), np.array([])
    prev = np.concatenate(([False], act[:-1]))
    rising = np.where(act & ~prev)[0]
    onsets = []
    durations = []
    for r in rising:
        j = r
        while j + 1 < act.size and act[j + 1]:
            j += 1
        onsets.append(t_rel[r])
        durations.append(t_rel[j] - t_rel[r])
    return np.asarray(onsets), np.asarray(durations)


def _interval_stats(onsets: np.ndarray):
    """
    Periodenschaetzung aus Onset-Zeiten: period_s = Median der Intervalle,
    jitter_s = Median der absoluten Abweichung vom Median (MAD), n_intervals.
    None bei weniger als 2 Intervallen.
    """
    if onsets.size < 3:
        return None
    iv = np.diff(onsets)
    if iv.size < 2:
        return None
    period = float(np.median(iv))
    jitter = float(np.median(np.abs(iv - period)))
    return {"period_s": period, "jitter_s": jitter, "n_intervals": int(iv.size)}


def _chain_fraction(onsets: np.ndarray, period_s: float, tol: float) -> float:
    """
    SDIF-Stil-Ketten-Validierung: Anteil der Onsets, die Teil einer Kette
    sind. Onset i gehoert zur Kette, wenn |onset[i+1] - onset[i] - k*period|
    <= tol*period fuer ein ganzzahliges k in {1, 2, 3} (k > 1 = verpasster
    Burst toleriert). Rueckgabe Float 0..1.
    """
    n = onsets.size
    if n < 2 or period_s <= 0:
        return 0.0
    diffs = np.diff(onsets)
    matched = np.zeros(n - 1, dtype=bool)
    for i, d in enumerate(diffs):
        for k in (1, 2, 3):
            if abs(d - k * period_s) <= tol * period_s:
                matched[i] = True
                break
    chained = np.zeros(n, dtype=bool)
    chained[:-1] |= matched
    chained[1:] |= matched
    return float(chained.sum()) / n


def _epoch_fold_refine(env: np.ndarray, t_rel: np.ndarray, p0: float,
                        nbins: int, rel_window: float):
    """
    Epoch Folding zur Periodenverfeinerung: fuer ein Raster von 200
    Testperioden zwischen p0*(1-rel_window) und p0*(1+rel_window) wird env
    nach Phase ((t_rel/P) mod 1) in nbins Phasen-Bins gefaltet; Kennzahl je
    Testperiode = Varianz der Bin-Mittel / Gesamtvarianz (Phasenkonzentration,
    0..1). Arbeitet direkt auf den echten Zeitstempeln - kein gleichfoermiges
    Raster noetig, Luecken und ungleiche Abstaende sind unkritisch.
    Rueckgabe (best_period_s, concentration).
    """
    if p0 <= 0 or env.size < nbins:
        return p0, 0.0
    total_var = float(np.var(env))
    if total_var < 1e-12:
        return p0, 0.0
    lo = p0 * (1.0 - rel_window)
    hi = p0 * (1.0 + rel_window)
    if lo <= 0:
        lo = p0 * 0.5
    best_p = p0
    best_conc = -1.0
    for P in np.linspace(lo, hi, 200):
        phase = (t_rel / P) % 1.0
        bin_idx = np.clip((phase * nbins).astype(int), 0, nbins - 1)
        sums = np.bincount(bin_idx, weights=env, minlength=nbins)
        counts = np.bincount(bin_idx, minlength=nbins)
        means = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
        conc = float(np.var(means)) / total_var
        if conc > best_conc:
            best_conc = conc
            best_p = float(P)
    return best_p, round(min(1.0, best_conc), 3)


def _fundamental_from_lags(lags):
    """
    Grundperiode aus einer Liste (lag_s, r) von autocorr_top_lags: kleinster
    Lag unter den Peaks mit r >= 0.8*max_r (Oberwellen-Reduktion wie in
    detect_comb). None bei leerer Liste.
    """
    if not lags:
        return None
    max_r = max(r for _, r in lags)
    candidates = [l for l, r in lags if r >= 0.8 * max_r]
    return min(candidates) if candidates else None


def _robust_sigma_per_bin(D: np.ndarray) -> np.ndarray:
    """
    Robuste Rauschstreuung je Bin ueber die Zeit (MAD-Schaetzer, unempfindlich
    gegen die Bursts selbst): sigma = 1.4826 * median(|D[:,i] - median(D[:,i])|).
    Untergrenze 0,5 dB, damit ein extrem ruhiger Bin die adaptive Schwelle
    nicht auf (fast) 0 dB zusammenfallen laesst.
    """
    med = np.median(D, axis=0)
    sigma = 1.4826 * np.median(np.abs(D - med[None, :]), axis=0)
    return np.maximum(sigma, 0.5)


def find_periodic_emitters(D: np.ndarray, t: np.ndarray, freq: np.ndarray,
                            floor_dbuv: np.ndarray, step_hz: float):
    """
    Periodische Emitter: Zwei-Schwellen-Clustering aktiver Bins ueber die
    Frequenzachse, dann je Cluster Onset-Intervalle, zeitliche Autokorrelation
    und Epoch Folding zu Periode/Jitter/Zyklenzahl/Wahrscheinlichkeit
    kombiniert. Rueckgabe nach 'confidence' absteigend sortierte Liste
    (max. N_TOP_EMITTERS).
    """
    n, nb = D.shape
    t_rel = t - t[0]
    dur = float(t_rel[-1]) if n > 0 else 0.0
    if dur <= 0 or n < 10:
        return []

    # Adaptive Schwellen: die festen dB-Werte oben sind nur noch Untergrenzen,
    # massgeblich ist k * robuste Rauschstreuung (MAD) je Bin - sonst loest
    # reines Rauschen bei hoher Streuung staendig selbst die feste dB-Marge aus.
    floor2d = floor_dbuv[None, :]
    sigma_bin = _robust_sigma_per_bin(D)
    up_margin = np.maximum(EMIT_UP_DB, EMIT_K_SIGMA * sigma_bin)
    almost_margin = np.maximum(EMIT_ALMOST_DB, (EMIT_K_SIGMA - 1.0) * sigma_bin)
    act_up = _median_filter_time((D > floor2d + up_margin[None, :]), EMIT_MEDIAN_W) > 0.5
    act_almost = _median_filter_time((D > floor2d + almost_margin[None, :]), EMIT_MEDIAN_W) > 0.5

    prev = np.vstack([np.zeros((1, nb), dtype=bool), act_up[:-1, :]])
    n_onsets_bin = (act_up & ~prev).sum(axis=0)
    duty_up = act_up.mean(axis=0)
    duty_almost = act_almost.mean(axis=0)

    cand = (n_onsets_bin >= EMIT_MIN_ONSETS) & (duty_up < BURST_DUTY_MAX)
    maxhold_db = D.max(axis=0)
    clusters = _cluster_emitter_bins(duty_up, duty_almost, maxhold_db, cand, EMIT_GAP_BINS)
    # Plausibilitaetsdeckel: ein periodischer Einzel-Emitter ist nie bandbreit -
    # breitere Cluster sind Bandhebungen und gehoeren zur Band-Autokorrelation.
    clusters = [(a, b) for a, b in clusters if (b - a + 1) <= EMIT_MAX_BINS]

    out = []
    for a, b in clusters:
        env = D[:, a:b + 1].max(axis=1)
        act = act_up[:, a:b + 1].any(axis=1)
        onsets, on_dur = _onsets_from_activity(act, t_rel)
        if onsets.size < EMIT_MIN_ONSETS:
            continue
        iv = _interval_stats(onsets)
        if iv is None:
            continue

        idx, dt = resample_uniform(D[:, a:b + 1], t)
        lags = autocorr_top_lags(env[idx], dt)
        p_ac = _fundamental_from_lags(lags)
        r_ac = next((r for l, r in lags if l == p_ac), 0.0) if p_ac is not None else 0.0
        agree = 0.0 if p_ac is None else 1.0 - min(1.0, abs(p_ac - iv["period_s"]) / iv["period_s"])
        if p_ac is not None and abs(p_ac - iv["period_s"]) <= 0.25 * iv["period_s"]:
            p0 = p_ac
        else:
            p0 = iv["period_s"]

        if p0 <= 0 or p0 < 2 * dt:
            continue
        n_cycles_raw = dur / p0
        if n_cycles_raw < EMIT_MIN_CYCLES:
            continue

        cluster_floor = float(floor_dbuv[a:b + 1].mean())
        period_s, conc = _epoch_fold_refine(env - cluster_floor, t_rel, p0,
                                             EMIT_FOLD_NBINS, EMIT_FOLD_REFINE)
        chain = _chain_fraction(onsets, period_s, EMIT_CHAIN_TOL)
        reg = max(0.0, 1.0 - iv["jitter_s"] / period_s) if period_s > 0 else 0.0

        confidence = round(0.30 * chain + 0.25 * reg + 0.20 * conc
                            + 0.15 * max(r_ac, 0.0) + 0.10 * agree, 3)
        if confidence < 0.35:
            continue
        if confidence >= 0.70 and chain >= 0.6:
            level = "sicher"
        elif confidence >= 0.50:
            level = "wahrscheinlich"
        else:
            level = "moeglich"

        n_bins_c = b - a + 1
        bw_hz = n_bins_c * step_hz
        bw_class = "schmal" if n_bins_c <= 1 else ("kanal" if n_bins_c <= 4 else "breit")

        seg_floor = floor_dbuv[a:b + 1]
        w = np.clip(maxhold_db[a:b + 1] - seg_floor, 0.0, None)
        if w.sum() > 0:
            f_center = float(np.sum(freq[a:b + 1] * w) / w.sum())
        else:
            f_center = float(freq[(a + b) // 2])

        if period_s < 1.0:
            period_class = "schnell"
        elif period_s < 30.0:
            period_class = "sekunden"
        else:
            period_class = "minuten"

        out.append({
            "f_center_hz": int(round(f_center)),
            "f_lo_hz": int(freq[a]),
            "f_hi_hz": int(freq[b]),
            "bw_hz": int(round(bw_hz)),
            "n_bins": int(n_bins_c),
            "bw_class": bw_class,
            "peak_dbuv": round(float(maxhold_db[a:b + 1].max()), 1),
            "period_ms": round(period_s * 1000.0, 1),
            "jitter_ms": round(iv["jitter_s"] * 1000.0, 1),
            "on_ms": round(float(np.median(on_dur)) * 1000.0, 1) if on_dur.size else 0.0,
            "n_onsets": int(onsets.size),
            "n_cycles": round(n_cycles_raw, 1),
            "period_class": period_class,
            "r_ac": round(float(max(r_ac, 0.0)), 3),
            "chain": round(float(chain), 3),
            "fold_conc": round(float(conc), 3),
            "confidence": confidence,
            "level": level,
        })

    out.sort(key=lambda e: -e["confidence"])
    return out[:N_TOP_EMITTERS]


def resample_uniform(D: np.ndarray, t: np.ndarray):
    """
    Baut ein uniformes Zeitraster (Median-dt) und indexiert die naechste
    Rohzeile je Rasterpunkt. Rueckgabe (idx, dt) fuer Autokorrelation.
    """
    tt = t - t[0]
    dt = float(np.median(np.diff(tt))) if tt.size > 1 else 1.0
    if dt <= 0:
        dt = 1.0
    grid = np.arange(0.0, tt[-1], dt) if tt.size > 1 else np.array([0.0])
    idx = np.searchsorted(tt, grid).clip(0, D.shape[0] - 1)
    return idx, dt


def strongest_events(D: np.ndarray, t: np.ndarray, freq: np.ndarray):
    """Top-Einzelpegel der Session: je Bin Max ueber Zeit, dann Top-Bins."""
    mx = D.max(axis=0)
    order = np.argsort(-mx)[:N_TOP_EVENTS]
    out = []
    for i in order:
        row = int(np.argmax(D[:, i]))
        out.append({
            "f_hz": int(freq[i]),
            "peak_dbuv": round(float(mx[i]), 1),
            "t_offset_s": round(float(t[row] - t[0]), 1),
        })
    return out


# ---------------------------------------------------------------------------
# Segment- und Session-Analyse
# ---------------------------------------------------------------------------

def analyze_segment(M: np.ndarray, t: np.ndarray, freq: np.ndarray, step_hz: int):
    """Kompletter Auswerteweg fuer EIN Segment. Gibt einen Befund-dict."""
    keep, sentinels = detect_sentinel_bins(M)
    sentinel_freqs = [int(freq[i]) for i in sentinels]
    M = M[:, keep]
    freq = freq[keep]

    n, nb = M.shape
    D = M.astype(np.float32) / 10.0                 # ab hier dBuV

    floor_deci = np.percentile(M, FLOOR_PCT, axis=0)
    floor_dbuv = floor_deci / 10.0
    p50 = np.median(D, axis=0)

    # Dauertraeger-Maske (fuers Ausklammern bei den Bursts)
    base = local_baseline(p50, CARRIER_BASELINE_W)
    carrier_mask = (p50 - base) > CARRIER_EXCESS_DB

    # Tastgrad je Bin (fuer find_carriers-Duty und find_bursts)
    active = D > (floor_dbuv + ACTIVITY_MARGIN_DB)[None, :]
    duty = active.mean(axis=0)

    carriers = find_carriers(D, freq, duty)
    bursts, _ = find_bursts(D, freq, floor_dbuv, carrier_mask)
    comb = detect_comb(D, step_hz)
    events = strongest_events(D, t, freq)

    # Periodische Emitter (Frequenz/Bandbreite/Periode/Wahrscheinlichkeit)
    emitters = find_periodic_emitters(D, t, freq, floor_dbuv, step_hz)

    # Zeitliche Autokorrelation: Gesamtband + staerkste Kanaele
    idx, dt = resample_uniform(D, t)
    band_env = D.mean(axis=1)[idx]
    band_lags = autocorr_top_lags(band_env, dt)
    strong_cols = np.argsort(-D.max(axis=0))[:N_TOP_AUTOCORR_CHANNELS]
    chan_ac = []
    any_period = [l for l, r in band_lags if r >= AUTOCORR_PEAK_MIN_R]
    for c in strong_cols:
        lags = autocorr_top_lags(D[:, c][idx], dt)
        chan_ac.append({"f_hz": int(freq[c]), "top_lags": lags})
        any_period += [l for l, r in lags if r >= AUTOCORR_PEAK_MIN_R]

    band_mean = D.mean()
    return {
        "n_sweeps": int(n),
        "n_bins_used": int(nb),
        "duration_s": round(float(t[-1] - t[0]), 1) if n > 1 else 0.0,
        "sweep_rate_hz": round(float(n / (t[-1] - t[0])), 2) if n > 1 and t[-1] > t[0] else None,
        "median_dt_ms": round(dt * 1000.0, 1),
        "band_mean_dbuv": round(float(band_mean), 2),
        "sentinel_bins_masked_hz": sentinel_freqs,
        "carriers": carriers,
        "bursts": bursts,
        "comb": comb,
        "strongest_events": events,
        "autocorr_band": band_lags,
        "autocorr_channels": chan_ac,
        "periodic_emitters": emitters,
        "autocorr_band_residual": [p for p in band_lags
                                   if not any(abs(p[0] * 1000.0 - e["period_ms"]) <= 0.15 * e["period_ms"]
                                              or abs(p[0] * 1000.0 - 2 * e["period_ms"]) <= 0.15 * e["period_ms"]
                                              or abs(p[0] * 1000.0 - 3 * e["period_ms"]) <= 0.15 * e["period_ms"]
                                              for e in emitters)],
        "periodicity_found": bool(any_period) or bool(emitters),
    }


def analyze_session(path: str):
    """Analysiert alle Segmente einer Session-DB. Gibt den Gesamtbefund."""
    conn = open_session_ro(path)
    try:
        srow = conn.execute(
            "SELECT started_at, source, mode, note FROM session LIMIT 1").fetchone()
        segments = list_segments(conn)
        seg_reports = []
        for seg in segments:
            M, t, freq = load_segment_matrix(conn, seg)
            if M.shape[0] == 0:
                continue
            rep = analyze_segment(M, t, freq, int(seg["step_hz"]))
            rep["segment_id"] = int(seg["id"])
            rep["start_hz"] = int(seg["start_hz"])
            rep["stop_hz"] = int(seg["stop_hz"])
            rep["step_hz"] = int(seg["step_hz"])
            # Nur bei Neu-DBs vorhanden (Alt-DBs kennen diese Spalten nicht,
            # siehe list_segments()) - defensiv per Schluesselpruefung statt
            # .get(), damit ein echtes NULL (mtime_s=DEFAULT) nicht mit
            # "Spalte fehlt" verwechselt wird.
            if "speed" in seg:
                rep["speed"] = seg["speed"]
            if "mtime_s" in seg:
                rep["mtime_s"] = seg["mtime_s"]
            seg_reports.append(rep)
    finally:
        conn.close()
    return {
        "analysis_version": ANALYSIS_VERSION,
        "kind": "session",
        "db_path": os.path.basename(path),
        "started_at": float(srow["started_at"]) if srow else None,
        "source": srow["source"] if srow else None,
        "mode": srow["mode"] if srow else None,
        "n_segments": len(seg_reports),
        "segments": seg_reports,
    }


# ---------------------------------------------------------------------------
# IFPAN-.specbin-Quelle (gleicher Rechenkern, anderer Loader)
# ---------------------------------------------------------------------------

def load_recording_matrix(path: str):
    """
    Laedt eine IFPAN-.specbin-Aufnahme ueber eb200_core.spectrum_reader und
    gibt dieselbe Form wie load_segment_matrix (plus step_hz/meta) zurueck:
      M       : int16-Matrix [n_frames, n_window], ROHE Deci-dBuV
      t       : float64-Vektor [n_frames] mit t_unix je Frame
      freq    : float64-Vektor [n_window] mit Bin-Mittenfrequenzen in Hz
      step_hz : float, Bin-Abstand in Hz (bei IFPAN i. Allg. nicht ganzzahlig)
      meta    : dict der Sidecar-Metadaten (center_hz, span_hz, bw_hz, demod ...)
    """
    rec = SpectrumRecording(path)
    try:
        n = rec.n_frames
        nb = rec.n_window
        M = np.empty((n, nb), dtype=np.int16)
        for k in range(n):
            _, _, levels = rec.frame(k)               # array('h'), roh
            M[k, :] = np.frombuffer(levels.tobytes(), dtype='<i2')
        t = np.asarray(rec.times(), dtype=np.float64)
        freq = rec.bin0_hz + np.arange(nb, dtype=np.float64) * rec.step_hz
        return M, t, freq, float(rec.step_hz), dict(rec.meta)
    finally:
        rec.close()


def analyze_recording(path: str):
    """
    Analysiert eine IFPAN-.specbin-Aufnahme als EIN durchgehendes Segment
    (konstantes Raster). Gleiches Befundschema wie analyze_session (ein Eintrag
    in 'segments'), erweitert um die Aufnahme-Metadaten aus der Sidecar.
    """
    M, t, freq, step_hz, meta = load_recording_matrix(path)
    segments = []
    if M.shape[0] > 0:
        rep = analyze_segment(M, t, freq, step_hz)
        rep["segment_id"] = 1
        rep["start_hz"] = int(round(float(freq[0])))
        rep["stop_hz"] = int(round(float(freq[-1])))
        rep["step_hz"] = step_hz
        segments.append(rep)
    return {
        "analysis_version": ANALYSIS_VERSION,
        "kind": "recording",
        "db_path": os.path.basename(path),
        "started_at": float(t[0]) if M.shape[0] > 0 else None,
        "source": meta.get("demod", ""),
        "mode": "IFPAN",
        "center_hz": meta.get("center_hz"),
        "span_hz": meta.get("span_hz"),
        "bw_hz": meta.get("bw_hz"),
        "n_segments": len(segments),
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Ausgabe: Text + optionale Plots
# ---------------------------------------------------------------------------

def format_text(report: dict) -> str:
    """Menschenlesbare Kurzfassung des Befunds."""
    L = []
    ts = report.get("started_at")
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"
    L.append(f"Session {report['db_path']}  Start {when}  "
             f"Quelle={report.get('source')}  Modus={report.get('mode')}")
    for s in report["segments"]:
        L.append("")
        L.append(f"--- Segment {s['segment_id']}: "
                 f"{s['start_hz']/1e6:.3f}-{s['stop_hz']/1e6:.3f} MHz, "
                 f"Schritt {s['step_hz']} Hz, {s['n_bins_used']} Bins, "
                 f"{s['n_sweeps']} Sweeps ---")
        L.append(f"Dauer {s['duration_s']:.0f} s  Rate {s['sweep_rate_hz']}/s  "
                 f"dt {s['median_dt_ms']} ms  Bandmittel {s['band_mean_dbuv']} dBuV")
        # Nur bei Neu-DBs vorhanden (siehe list_segments()/analyze_session()) -
        # Alt-DBs zeigen diese Zeile schlicht nicht.
        if "speed" in s or "mtime_s" in s:
            mtime_txt = None
            if "mtime_s" in s:
                mtime_s = s["mtime_s"]
                mtime_txt = "MTIME DEFAULT" if mtime_s is None else f"MTIME {mtime_s * 1000:.0f} ms"
            parts = [p for p in (f"Speed {s.get('speed')}" if s.get("speed") else None, mtime_txt) if p]
            if parts:
                L.append("  ".join(parts))
        if s["sentinel_bins_masked_hz"]:
            fs = ", ".join(f"{f/1e6:.4f}" for f in s["sentinel_bins_masked_hz"])
            L.append(f"Sentinel-Bins maskiert (MHz): {fs}")
        c = s["comb"]
        if c["present"]:
            L.append(f"Kamm: ~{c['spacing_khz']} kHz  "
                     f"(Regelmaessigkeit {c['regularity']}) -> "
                     f"{COMB_VERDICT_TEXT.get(c['verdict'], c['verdict'])}")
        else:
            L.append("Kamm: keiner erkannt")
        if s["carriers"]:
            L.append("Dauertraeger (Top):")
            for d in s["carriers"][:6]:
                L.append(f"  {d['f_peak_hz']/1e6:.4f} MHz  med {d['median_dbuv']} dBuV  "
                         f"+{d['excess_db']} dB  duty {d['duty_pct']}%")
        if s["bursts"]:
            L.append("Bursthafte Kanaele (Top):")
            for d in s["bursts"][:6]:
                L.append(f"  {d['f_peak_hz']/1e6:.4f} MHz  duty {d['duty_pct']}%  "
                         f"peak {d['peak_dbuv']} dBuV")
        if s["strongest_events"]:
            e = s["strongest_events"][0]
            L.append(f"Staerkstes Ereignis: {e['f_hz']/1e6:.4f} MHz  "
                     f"{e['peak_dbuv']} dBuV  bei t+{e['t_offset_s']} s")
        if s.get("periodic_emitters"):
            L.append("Periodische Emitter:")
            for e in s["periodic_emitters"][:6]:
                L.append(f"  {e['f_center_hz']/1e6:.4f} MHz  BW {e['bw_hz']/1e3:.1f} kHz ({e['bw_class']})  "
                         f"T {e['period_ms']:.0f} ms +/-{e['jitter_ms']:.0f}  on {e['on_ms']:.0f} ms  "
                         f"{e['n_cycles']} Zyklen  p={e['confidence']} ({e['level']})")
        L.append("Zeitliche Regelmaessigkeit: "
                 + ("feste Periode(n) gefunden" if s["periodicity_found"]
                    else "keine feste Wiederkehrperiode (Aktivitaet sporadisch)"))
    return "\n".join(L)


def render_plots(report: dict, path: str, out_dir: str) -> list:
    """
    Optionale PNGs pro Segment: Spektralprofil (Mittel+Max-Hold) und ein
    sauberer Max-Pool-Wasserfall. Braucht matplotlib; fehlt es, wird eine
    aussagekraeftige Meldung ausgegeben und nichts gerendert.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("[plots] matplotlib nicht verfuegbar - ueberspringe Plots.",
              file=sys.stderr)
        return []

    os.makedirs(out_dir, exist_ok=True)
    written = []
    conn = open_session_ro(path)
    try:
        segments = {int(s["id"]): s for s in list_segments(conn)}
        for s in report["segments"]:
            seg = segments[s["segment_id"]]
            M, t, freq = load_segment_matrix(conn, seg)
            keep, _ = detect_sentinel_bins(M)
            M = M[:, keep]
            freq = freq[keep]
            D = M.astype(np.float32) / 10.0
            fmhz = freq / 1e6
            n = D.shape[0]

            # Spektralprofil
            fig, ax = plt.subplots(figsize=(12, 4.5))
            ax.plot(fmhz, D.max(axis=0), color="#d9822b", lw=0.6, label="Max-Hold")
            ax.plot(fmhz, D.mean(axis=0), color="#2b6cb0", lw=0.8, label="Mittelwert")
            ax.set_xlabel("Frequenz [MHz]"); ax.set_ylabel("Pegel [dBuV]")
            ax.set_title(f"Spektralprofil Segment {s['segment_id']}  "
                         f"{s['start_hz']/1e6:.1f}-{s['stop_hz']/1e6:.1f} MHz")
            ax.grid(alpha=0.25); ax.legend(loc="upper right")
            p1 = os.path.join(out_dir, f"seg{s['segment_id']}_spectrum.png")
            fig.tight_layout(); fig.savefig(p1, dpi=110); plt.close(fig)
            written.append(p1)

            # Max-Pool-Wasserfall (schmale Signale bleiben erhalten)
            target = 600
            g = max(1, -(-n // target))
            rows = [D[i:i + g].max(axis=0) for i in range(0, n, g)]
            W = np.asarray(rows)
            fig, ax = plt.subplots(figsize=(12, 7))
            im = ax.imshow(W, aspect="auto", origin="upper", cmap="viridis",
                           extent=[fmhz[0], fmhz[-1],
                                   (t[-1] - t[0]) / 60.0, 0.0],
                           vmin=-15, vmax=35)
            ax.set_xlabel("Frequenz [MHz]")
            ax.set_ylabel("Zeit ab Segmentstart [min]")
            ax.set_title(f"Wasserfall Segment {s['segment_id']} (Max-Pool)")
            cb = fig.colorbar(im, ax=ax, pad=0.01); cb.set_label("Pegel [dBuV]")
            p2 = os.path.join(out_dir, f"seg{s['segment_id']}_waterfall.png")
            fig.tight_layout(); fig.savefig(p2, dpi=110); plt.close(fig)
            written.append(p2)
    finally:
        conn.close()
    return written


# ---------------------------------------------------------------------------
# Selbsttest (synthetische In-Memory-Session, kein Geraet noetig)
# ---------------------------------------------------------------------------

def _selftest() -> None:
    """
    Baut eine synthetische Session mit bekanntem Inhalt und prueft, dass die
    Analyse Sentinel, Dauertraeger, Kamm und Periodizitaet wie erwartet
    findet. Schreibt in eine temporaere Datei (echte ?mode=ro-Kette).
    """
    import tempfile
    start_hz, step_hz, n_bins = 42_000_000, 7500, 400
    n = 800
    rng = np.random.default_rng(1)
    base = rng.normal(50, 15, size=(n, n_bins))         # Rauschgrund ~5 dBuV
    freq = start_hz + np.arange(n_bins) * step_hz

    # Dauertraeger bei 43,000 MHz (~30 dBuV konstant)
    cbin = int(round((43_000_000 - start_hz) / step_hz))
    base[:, cbin] += 250
    # 100-kHz-Kamm ueber den ganzen Span (konstant -> Artefakt-Charakter)
    for b in range(0, n_bins, int(round(100_000 / step_hz))):
        base[:, b] += 60
    # periodischer Burst bei 44,000 MHz alle 50 Sweeps
    bbin = int(round((44_000_000 - start_hz) / step_hz))
    base[::50, bbin] += 400
    # schwacher Einzelbin-Emitter bei 43,500 MHz alle 33 Sweeps, 2 Sweeps lang
    # (innerhalb des 42,000-44,9925-MHz-Testbands; 45,000 MHz laege ausserhalb
    # der 400 Bins und wurde deshalb hierher verlegt)
    b_narrow = int(round((43_500_000 - start_hz) / step_hz))
    for k0 in range(0, n, 33):
        base[k0:k0 + 2, b_narrow] += 110
    # 3 Bins breiter Emitter bei 43,750 MHz alle 70 Sweeps, 4 Sweeps lang
    # (ebenfalls innerhalb des Bands verlegt, siehe oben; urspruenglich 46,000 MHz)
    b_wide = int(round((43_750_000 - start_hz) / step_hz))
    for k0 in range(0, n, 70):
        base[k0:k0 + 4, b_wide - 1:b_wide + 2] += 300
    # Sentinel-Overshoot im letzten Bin (konstant 200,0 dBuV = 2000)
    base[:, -1] = 2000

    M = np.clip(base, -300, 5000).astype(np.int16)

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        conn = sqlite3.connect(tmp.name)
        conn.executescript("""
            CREATE TABLE session(id INTEGER PRIMARY KEY CHECK(id=1),
                started_at REAL, source TEXT, mode TEXT, note TEXT);
            CREATE TABLE segment(id INTEGER PRIMARY KEY, start_hz INTEGER,
                stop_hz INTEGER, step_hz INTEGER, n_bins INTEGER,
                t_first REAL, t_last REAL, row_count INTEGER);
            CREATE TABLE sweep(id INTEGER PRIMARY KEY, segment_id INTEGER,
                seq INTEGER, t_unix REAL, levels BLOB);
        """)
        t0 = time.time()
        conn.execute("INSERT INTO session VALUES (1,?,?,?,?)",
                     (t0, "selftest", "DSCAN", None))
        conn.execute("INSERT INTO segment VALUES (1,?,?,?,?,?,?,?)",
                     (start_hz, start_hz + (n_bins - 1) * step_hz, step_hz,
                      n_bins, t0, t0 + n * 0.09, n))
        for k in range(n):
            conn.execute("INSERT INTO sweep(segment_id,seq,t_unix,levels) "
                         "VALUES (1,?,?,?)",
                         (k, t0 + k * 0.09, array('h', M[k, :].tolist()).tobytes()))
        conn.commit(); conn.close()

        rep = analyze_session(tmp.name)
        assert rep["analysis_version"] == ANALYSIS_VERSION, "analysis_version fehlt"
        s = rep["segments"][0]
        assert s["sentinel_bins_masked_hz"], "Sentinel nicht erkannt"
        cf = [c["f_peak_hz"] for c in s["carriers"]]
        assert any(abs(f - 43_000_000) <= step_hz for f in cf), "43-MHz-Traeger fehlt"
        assert s["comb"]["present"], "Kamm nicht erkannt"
        assert s["comb"]["verdict"] in COMB_VERDICT_TEXT, "Kamm-Urteil ist kein Code"
        assert abs(s["comb"]["spacing_khz"] - 100.0) < 15.0, "Kammabstand falsch"
        assert s["periodicity_found"], "periodischer Burst nicht erkannt"
        em = s["periodic_emitters"]
        assert len(em) >= 3, f"erwartet >= 3 Emitter, gefunden {len(em)}"
        def _near(f_hz, tol_hz=2 * step_hz):
            return [e for e in em if abs(e["f_center_hz"] - f_hz) <= tol_hz]
        e44 = _near(44_000_000); e45 = _near(43_500_000); e46 = _near(43_750_000)
        assert e44 and e45 and e46, "nicht alle drei Emitter gefunden"
        assert abs(e44[0]["period_ms"] - 50 * 90) < 0.1 * 50 * 90, "Periode 44 MHz falsch"
        assert abs(e45[0]["period_ms"] - 33 * 90) < 0.1 * 33 * 90, "Periode 43.5 MHz falsch"
        assert abs(e46[0]["period_ms"] - 70 * 90) < 0.1 * 70 * 90, "Periode 43.75 MHz falsch"
        assert e45[0]["n_bins"] == 1, "43.5 MHz sollte 1 Bin breit sein"
        assert 2 <= e46[0]["n_bins"] <= 4, "43.75 MHz sollte ~3 Bins breit sein"
        assert e44[0]["level"] == "sicher", "44 MHz sollte 'sicher' sein"
        assert all(e["n_onsets"] >= EMIT_MIN_ONSETS for e in em)
        print(format_text(rep))
        _selftest_recording()
        print("\nSelbsttest bestanden.")
    finally:
        os.unlink(tmp.name)


def _selftest_recording() -> None:
    """
    Synthetische .specbin (+ Sidecar) mit bekanntem Inhalt: prueft, dass
    analyze_recording einen Dauertraeger und eine periodische Aussendung
    findet. Die Datei wird direkt im dokumentierten Format geschrieben
    (Prefix "<Id" = seq uint32 + t double, dann n_window int16 LE), ohne
    Geraet und ohne den Writer.
    """
    import struct
    import tempfile
    n_window = 200
    bin0_hz = 48_000_000.0
    step_hz = 125.0
    n = 400
    rng = np.random.default_rng(2)
    base = rng.normal(40, 8, size=(n, n_window))       # Grund ~4 dBuV
    cbin = 100
    base[:, cbin] += 250                               # Dauertraeger ~29 dBuV
    bbin = 150
    base[::20, bbin] += 600                            # sauberer Burst alle 20 Frames
    M = np.clip(base, -300, 5000).astype('<i2')

    framesize = 12 + 2 * n_window
    d = tempfile.mkdtemp()
    specbin = os.path.join(d, "selftest.specbin")
    t0 = time.time()
    try:
        with open(specbin, "wb") as f:
            for k in range(n):
                f.write(struct.pack("<Id", k & 0xFFFF, t0 + k * 0.09))
                f.write(M[k, :].tobytes())
        meta = {
            "format": "eb200-ifpan-bins-v1",
            "n_window": n_window,
            "framesize_bytes": framesize,
            "bin0_hz": bin0_hz,
            "step_hz": step_hz,
            "center_hz": int(bin0_hz + (n_window // 2) * step_hz),
            "span_hz": int(n_window * step_hz),
            "bw_hz": 30000,
            "demod": "AM",
            "frames_written": n,
        }
        with open(specbin + ".json", "w", encoding="utf-8") as f:
            json.dump(meta, f)

        rep = analyze_recording(specbin)
        assert rep["kind"] == "recording", "kind falsch"
        assert rep["analysis_version"] == ANALYSIS_VERSION, "analysis_version fehlt"
        s = rep["segments"][0]
        target = bin0_hz + cbin * step_hz
        cf = [c["f_peak_hz"] for c in s["carriers"]]
        assert any(abs(f - target) <= 2 * step_hz for f in cf), "Dauertraeger fehlt"
        assert s["periodicity_found"], "periodische Aussendung nicht erkannt"
        em = s["periodic_emitters"]
        assert em, "kein periodischer Emitter in .specbin"
        assert abs(em[0]["period_ms"] - 20 * 90) < 0.1 * 20 * 90, "specbin-Periode falsch"
        print("Selbsttest .specbin bestanden.")
    finally:
        for p in (specbin, specbin + ".json"):
            if os.path.exists(p):
                os.unlink(p)
        os.rmdir(d)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Auto-Analyse fuer D-SCAN-Session-DBs und IFPAN-.specbin-Aufnahmen")
    ap.add_argument("db", nargs="?", help="Pfad zur Session-DB (.db) oder IFPAN-Aufnahme (.specbin)")
    ap.add_argument("--json", metavar="DATEI", help="Befund als JSON schreiben")
    ap.add_argument("--plots", metavar="ORDNER", help="PNG-Plots in ORDNER schreiben")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args(argv)

    if args.selftest:
        _selftest()
        return 0
    if not args.db:
        ap.error("db-Pfad fehlt (oder --selftest verwenden)")

    if args.db.endswith(".specbin"):
        report = analyze_recording(args.db)
    else:
        report = analyze_session(args.db)
    print(format_text(report))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\n[json] Befund geschrieben -> {args.json}")
    if args.plots and report.get("kind") == "session":
        written = render_plots(report, args.db, args.plots)
        for p in written:
            print(f"[plots] {p}")
    elif args.plots:
        print("[plots] Plots gibt es derzeit nur fuer Session-DBs, nicht fuer .specbin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())