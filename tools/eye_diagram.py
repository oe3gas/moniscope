#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/eye_diagram.py

Berechnet Augendiagramm und Symbolparameter (Frequenzoffset, Hub, Symbolrate,
Taktphase, Augenoeffnung, Jitter, Pegelanzahl) aus einer vom IFPAN-Zoom
aufgezeichneten Stereo-IQ-WAV (Kanal 0 = I, Kanal 1 = Q - Projektannahme,
Hardware-Validierung siehe PLAN.md T-901). Reines Offline-Werkzeug, kein
Geraetezugriff, kein Server.

numpy ist erlaubt (tools/ ist nicht eb200_core/, numpy ist ohnehin
Pflichtabhaengigkeit) - kein eigener FFT-/Array-Nachbau wie in wav2sdr.py.
Keine weiteren Fremdbibliotheken; die Grafik wird als reines SVG ohne externe
Referenzen direkt geschrieben (kein matplotlib).

Sidecar <wav>.json (falls vorhanden): freq_hz, sample_rate_hz, is_iq. Meldet
die Sidecar is_iq=false, bricht das Werkzeug ab. Fehlt die Sidecar, wird nur
gewarnt und die Abtastrate aus dem WAV-Header uebernommen.

Burst-Erkennung (zweistufig, siehe PLAN.md T-902 Entscheidungslog): Stufe A
(Amplitudenkontrast) erkennt getastete Signale ueber den Abstand zwischen dem
10. und 95. Perzentil der geglaetteten Augenblicksleistung (Schwelle =
geometrisches Mittel beider Perzentile, ab 6 dB Kontrast). Bei weniger als
6 dB Kontrast entscheidet Stufe B ueber die Flatness (normierte Entropie) des
Histogramms der unkorrigierten Momentanfrequenz: Rauschen verteilt die
Momentanfrequenz nahezu gleichmaessig (Flatness nahe 1), jedes modulierte
Dauersignal konzentriert sie auf wenige Bins. Ein reiner Amplitudenschwellwert
kann bei durchgehend gleichfoermiger Leistung nicht zwischen "Dauersignal" und
"nur Rauschen" unterscheiden - numerisch nachgewiesen an T4 (siehe --selftest).

Frequenzoffset und Hub kommen NICHT aus dem Median von f (der ist bei einer
bimodalen/multimodalen Verteilung strukturell verzerrt, sobald die Symbole
nicht exakt 50/50 verteilt sind - ebenfalls an T1 numerisch nachgewiesen),
sondern aus den Peaklagen des (geglaetteten) Histogramms der unkorrigierten
Momentanfrequenz: offset = Mitte zwischen dem aeussersten linken und rechten
akzeptierten Peak, dev = deren halber Abstand. Die Peakanzahl liefert
zugleich die Pegelanzahl (2 -> FSK2, 4 -> FSK4).

Die Symbolrate kommt aus einer Geradenanpassung an die fallende Flanke der
Autokorrelation von f_korr (Bereich 0.25 <= R <= 0.75, extrapoliert auf die
Nullstelle) statt aus einem Perzentil der Nulldurchgangsabstaende oder einem
Autokorrelations-Maximum - beide frueheren Ansaetze sind an synthetischen
Testfaellen nachweislich gescheitert (siehe --selftest T9/T10 und
Entscheidungslog). Fuer eine echte Zufallsfolge streut das Ergebnis auch ohne
jedes Rauschen um bis zu einige Prozent (Eigenschaft der konkreten
Bitfolgen-Laufstatistik, siehe BAUD_TOL_ZUFALLSFOLGE); eine reine Wechselfolge
(Praeambel) liefert dagegen die halbe Periode und wird ueber R(2*T_fit) < -0.5
erkannt und korrigiert (BAUD_TOL_WECHSELFOLGE).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import wave

import numpy as np

# Erlaubt den Direktaufruf "python tools/eye_diagram.py" (Muster
# tools/afc_probe.py): ohne diesen Eintrag fehlt das Repo-Root im Suchpfad
# fuer den eb200_core-Import (T-1703).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.framing import character_stats, detect_async_framing
from eb200_core.mode_match import match_fsk
from eb200_core.mode_table import load_fsk_table

STANDARD_BAUDS = (512, 1200, 2400, 4800)
# Obergrenze fuer das gelesene Zeitfenster (T-1608). read_wav_iq() liest sonst
# die GANZE Datei nach complex128: die 113-Minuten-Aufnahme mit 217 Mio Frames
# braeuchte 868 MB Rohpuffer, 3,5 GB float64-Zwischenstufe und 3,5 GB
# Ergebnis - Spitze ueber 8 GB. Das Werkzeug war fuer kurze IFPAN-Zoom-
# Mitschnitte gedacht, in denen das nie auffiel. Mit einem Fenster von 120 s
# bei 32 kHz IQ bleiben es rund 120 MB.
MAX_WINDOW_S = 120.0

# Obergrenze fuer die gezeichneten Konstellationspunkte (T-1614). Befund
# 2026-09-10: write_svg_psk zeichnete einen <circle> JE SYMBOL ohne Grenze -
# 360 001 Symbole ergaben 27 MB SVG, das in :8070 als ebenso viele DOM-Knoten
# in der Seite landete. Der FSK-Pfad war ueber max_traces laengst gedeckelt,
# der PSK-Pfad nie. Jenseits einiger tausend Punkte liegen die Marker ohnehin
# uebereinander; die Auswahl ist gleichmaessig ueber die Symbolfolge verteilt,
# damit sie repraesentativ bleibt und nicht nur den Anfang des Bursts zeigt.
MAX_CONST_POINTS = 2000

N_ZERO_HIST_BINS = 201
N_CLOCK_PHASES = 64
N_EYE_POINTS = 128

# Schwelle Stufe B (Flatness der Momentanfrequenz-Verteilung):
# Gemessen 2026-09-01 ueber 20 Seeds je Fixture: FSK2 0.719-0.722, FSK4
# 0.878-0.880, Rauschen 0.999. Schwelle mittig in der Luecke.
# Die Kennzahl steigt mit der Zahl der Modulationsstufen; bei mehr als vier
# Stufen ist der Abstand nach oben kleiner. Deshalb Schwelle bewusst nahe am
# Rauschband und nicht mittig zwischen FSK4 und Rauschen gewaehlt - Kopfraum
# fuer hoeherstufige Signale geht vor.
FLATNESS_THR = 0.94

# T-1622: Schwelle fuer die Momentanfrequenz IN DEN LUECKEN zwischen den
# Bursts. Darueber gilt die Pause als leer (echtes Tasten), darunter steht
# dort weiter Modulation (gefadetes Dauersignal). Messwerte und Herleitung
# siehe _luecken_sind_leer().
BURST_GAP_FLATNESS = 0.97
BURST_GAP_MIN_SAMPLES = 200

# Zufallsfolgen: intrinsische Streuung ca. +-2.5 % (1 Sigma), unabhaengig von
# Rauschen und Burstlaenge, da die Autokorrelationsflanke von der Lauflaengen-
# statistik der konkreten Bitfolge abhaengt (nicht von einer Schaetzschwaeche -
# verschwindet nachweislich nicht bei SNR 200 dB und nicht bei mehr Symbolen).
# Gemessen 2026-09-01, 512/1200/2400 Bd, je 10 Seeds, max. Abweichung 5.53 %.
# Gilt auch fuer das Standardraster-Hinweisfeld (512/1200/2400/4800 Bd).
BAUD_TOL_ZUFALLSFOLGE = 0.06

# Wechselfolgen (Praeambeln) sind deterministisch, die obige Streuung entfaellt
# (gemessen 0.0065 % nach der Verdopplungskorrektur in estimate_symbol_duration).
BAUD_TOL_WECHSELFOLGE = 0.01

# Schwelle Schaerfe (PSK-Pfad, M-te-Potenz-Linie in P1 und Huellkurvenlinie in
# P3, PLAN.md T-904): logarithmisch skaliert statt fest (Messauftrag M1/M1b).
# Die Kennzahl "staerkster Bin / Mittelwert aller Betraege" folgt fuer
# Rauschen naeherungsweise der Extremwertstatistik von N unabhaengigen
# periodogrammartigen Werten - deren Erwartungswert waechst mit ln(N). Eine
# feste Schwelle waere damit von der Aufnahmedauer abhaengig: eine zunaechst
# angenommene feste Schwelle von 3.0 wurde an einer 5-s-Rauschaufnahme
# (N=160000) widerlegt (gemessene Rauschwerte 3.86-4.00, ueber der Schwelle).
# Deshalb wird die Schwelle an den Rauscherwartungswert gekoppelt:
# schwelle = SCHAERFE_FAKTOR * ln(N), N = Anzahl der in die jeweilige FFT
# eingehenden Samples. Gemessen 2026-09-01 (T14-T17b, 5 s und 30 s Rauschen,
# siehe Abschlussbericht M1b, nach Behebung eines Clipping-Artefakts in der
# Rauschfixture - volle Amplitude riss bei N>=160000 vereinzelt ins 16-Bit-
# WAV-Clipping und taeuschte selbst Schaerfewerte um 67 vor):
# kleinste Signalreserve Faktor 2.4 (T16, s_8=116.03 gegen Schwelle 47.93),
# groesster Rauschabstand Faktor 12 (T17, s_4=3.985 gegen Schwelle 47.93).
SCHAERFE_FAKTOR = 4.0

# Nachdetektionsfilter fuer Taktlage, Augendiagramm, Augenoeffnung und Jitter
# (T-1709). NICHT fuer Burst-Erkennung, Histogramm-Peaks oder Symbolraten-
# schaetzung - deren Schwellen (FLATNESS_THR, BURST_GAP_FLATNESS,
# BAUD_TOL_ZUFALLSFOLGE/-WECHSELFOLGE) sind an UNGEFILTERTEN Daten gemessen,
# und eine Mittelung wuerde die Autokorrelationsflanke mit einem Dreieck
# falten und T_fit systematisch verschieben.
#
# Befund 2026-09-12 an records/20260912_202830_7646kHz_IQ.wav (fs_eff=4000,
# 81,2 Samples/Symbol): r2=0,999994 (Symbolrate praktisch fehlerfrei), aber
# Augenoeffnung nur 2,3 %, Jitter 28,6 % - ein einzelner, ungemittelter Wert
# je Symbolmitte greift bei so hoher Ueberabtastung vor allem Rauschen ab.
#
# postdet_window_samples = max(1, round(POSTDET_FILTER_REL * T_samples)).
# Gemessen (eye_opening_pct, Mittel ueber zehn Seeds ausser T1/T11 - dort wie
# im uebrigen Selbsttest einzelseedig):
#
#   rel    W(T~27) eyeT1   W(T=40) eyeT11   W(T=80) eyeFixtureB  eyeRauschen
#   0.0    1       70,0 %  1       96,1 %   1       74,2 %       0,18 %
#   0.125  3       89,9 %  5       99,0 %   10      93,4 %       0,05 %
#   0.25   7       95,8 %  10      99,7 %   20      96,2 %       0,09 %
#   0.5    13      97,8 %  20      100,1 %  40      97,6 %       0,05 %
#   0.75   20      98,4 %  30      100,3 %  60      98,0 %       0,05 %
#   1.0    27      91,7 %  40      96,7 %   80      96,6 %       0,03 %
#
# (T1 = FSK2 fs=32000 Bd=1200 Hub=4500 Hz SNR=20dB; T11 = FSK2 fs_eff=2000
# Bd=50 Hub=225 Hz SNR=20dB, wie im Selbsttest. Fixture B = derselbe
# Selbsttest-Signalpfad wie T11, aber fs=32000 dezimiert um 8 auf
# fs_eff=4000 (Muster T32/--auto-decimate) bei SNR=10dB - der reale
# Ueberabtastungsfall, mit FEST vorgegebenem baud=50 statt Schaetzung, damit
# die Autokorrelations-Baudschaetzung nicht als zweite Stoergroesse
# hineinspielt (--selftest T33 nutzt dafuer baud_override). ERSTER Entwurf
# erzeugte das Ueberabtastungssignal direkt bei fs=4000 OHNE Dezimations-
# Tiefpass: das Rauschen blieb dabei breitbandig bis fs_eff/2 und liess die
# Pegelerkennung bei 10 dB SNR in 6-8 Scheinpegel statt 2 zerfallen - kein
# Nachdetektionsfilter-Befund, sondern ein Konstruktionsfehler der Fixture,
# durch die Dezimation ueber apply_decimation() behoben. Rauschen = reines
# Rauschen auf demselben Weg (fs=32000 dezimiert um 8), das
# Abbruchkriterium.)
#
# rel>=0.5 scheidet trotz besserer Augenoeffnung aus: bei der Mindestforderung
# von vier Samples je Symbol ergaebe rel=0.5 bereits W=2 statt der
# geforderten Identitaet W=1 (--selftest T33). rel=1.0 verengt das Auge bei
# allen drei Signalfixturen wieder (z. B. 91,7 % gegen 98,4 % bei T1) - das
# Fenster ueberlappt dort schon ins Nachbarsymbol. Von den beiden
# verbleibenden zulaessigen Werten liefert 0.125 fuer den Ueberabtastungsfall
# nur 93,4 % bei einer Bestmarke von 98,0 % (rel=0.75) - ein Abstand von
# 4,7 Punkten, bei T1 sogar 8,5 Punkte (89,9 % gegen 98,4 %). 0.25 liegt bei
# allen drei Signalfixturen innerhalb weniger Punkte des jeweiligen
# Bestwerts (T1 95,8 % gegen 98,4 %, T11 99,7 % gegen 100,3 %, Fixture B
# 96,2 % gegen 98,0 %) und haelt die Rauschfixture bei 0,09 % - klar unter
# jeder brauchbaren Schwelle.
#
# Jitter verhaelt sich NICHT monoton wie erhofft: bei T1/T11 (SNR 20 dB)
# bleibt er praktisch unveraendert (~1 % bzw. ~0,03 %), bei der stark
# verrauschten Ueberabtastungs-Fixture B (SNR 10 dB) STEIGT er dagegen schon
# bei der kleinsten Filterung sprunghaft (unter 1 % ungefiltert auf rund 32 %
# bei rel=0.125, rund 35 % bei rel=0.25) und bleibt dort - reproduzierbar
# ueber zehn Seeds und ebenso an der echten Aufnahme (dort rund 28,6 % nahezu
# unabhaengig vom Fenster). Vermutlich gibt die verbreiterte, bei niedrigem
# Stoerabstand aber weiterhin verrauschte Flanke der linearen
# Nulldurchgangs-Interpolation mehr Spielraum als eine schmale, klare Flanke.
# Das ist ein eigener Befund (Abschlussbericht T-1709), kein Grund, am Filter
# zu drehen: die Symbolmitte (Augenoeffnung, Kandidatenvergleich in
# eb200_core/mode_match.py) ist davon nicht betroffen.
POSTDET_FILTER_REL = 0.25

# Nulldurchgangsraster (T-1710): Zyklisches Histogramm der Nulldurchgangslagen
# relativ zum erkannten Taktraster (siehe crossing_grid()). Anzahl Bins wie
# N_CLOCK_PHASES - dieselbe Aufloesung reicht, um ein Halbbit-Raster (Peak bei
# 0,5) von einem Vollbit-Raster (ein Peak) zu trennen.
N_CROSSING_HIST_BINS = 64

# Gemessen (--selftest T34, PLAN.md T-1710): Zufallspositionen (rng.uniform)
# liefern ab n_cross=7 Kreuzungen in 20000 Versuchen KEINE falsche "full"/
# "half"-Einstufung mehr (n_cross=4: 0.67 %, n_cross=6: 0.09 %, n_cross=7..9:
# 0.00 %, vereinzelte Rundungsartefakte ab n_cross=8 bei ~0.005 %).
# CROSSING_MIN_COUNT=10 liegt mit Marge oberhalb dieses Uebergangs.
CROSSING_MIN_COUNT = 10

# Gemessen: die 1,5-Stoppbit-Fixtur (T34.3) liefert ueber 90 Kombinationen
# (SNR 20/10/6 dB, je 30 Seeds) einen zyklischen Peakabstand von EXAKT 0,5 -
# eine notwendige Folge der Bin-Quantisierung (64 Bins, Abstand 32 Bins ist
# rechnerisch immer genau 0,5). CROSSING_HALF_TOL gibt zwei Bin-Breiten Marge
# fuer weniger ideale (nicht synthetische) Aufnahmen.
CROSSING_HALF_TOL = 2.0 / N_CROSSING_HIST_BINS

# Mindestanteil der Nulldurchgaenge je Peak, damit zwei Haeufungen als
# Halbbit-Raster gelten (Vorgabe PLAN.md T-1710: ein Nebenmaximum aus
# Rauschen darf nicht als halbes Raster durchgehen).
CROSSING_PEAK_MIN_SHARE = 1.0 / 3.0

# Feinabgleich der Symbolrate ueber die Nulldurchgangslagen (T-1711, siehe
# refine_symbol_rate()). Gemessen (--selftest T35, PLAN.md T-1711):
#
# Echte Signale (FSK2, 50/100/1200 Bd, SNR 20/10/6/3 dB, mehrere Seeds, lang
# genug fuer einen verlaesslichen Flankenfit - mind. einige hundert
# Nulldurchgaenge): R2 immer >= 0.968, Randabstand (bester minus zweitbester
# lokaler Peak) immer >= 0.727 - unabhaengig vom Stoerabstand.
#
# Zufallspositionen (rng.uniform, 300 Versuche je Stufe, deckt sowohl
# Rauschen als auch zu kurze Bursts ab): mit wachsender Anzahl faellt die
# per Zufall erreichbare Bestmarke rasch:
#   n=5:  R2 bis 0.998 (!), Randabstand bis 0.08  - bei so wenigen Punkten
#         kann JEDE Periode zufaellig passen, das Mass ist wertlos.
#   n=10: R2 bis 0.944, Randabstand bis 0.21
#   n=20: R2 bis 0.736, Randabstand bis 0.19
#   n=40: R2 bis 0.560, Randabstand bis 0.14
#   n=60: R2 bis 0.425, Randabstand bis 0.13
#   n=100: R2 bis 0.357, Randabstand bis 0.11
#   n=200: R2 bis 0.262, Randabstand bis 0.08
# Kurze ECHTE Bursts (2-8 s, 50 Bd, wenige Dutzend bis wenige hundert
# Nulldurchgaenge) zeigen zusaetzlich, dass der grobe Flankenfit selbst bei
# so wenig Material bis zu 11 % danebenliegen kann - mehr als
# BAUD_TOL_ZUFALLSFOLGE (6 %). Der Suchbereich enthaelt die wahre Periode
# dann gar nicht erst; auch das drueckt R2 und Randabstand nach unten und
# wird durch dieselben Schwellen sauber abgefangen (kein Sonderfall noetig).
#
# BAUD_FINE_MIN_CROSSINGS=40 liegt oberhalb der Zone, in der Zufall allein
# schon eine hohe Bestmarke erreichen kann (n<=10). BAUD_FINE_R2_MIN=0.85 und
# BAUD_FINE_PEAK_MARGIN=0.3 liegen mittig zwischen der bei n=40 noch per
# Zufall erreichbaren Obergrenze (R2 0.56 / Randabstand 0.14) und der bei
# echten, ausreichend langen Signalen gemessenen Untergrenze (R2 0.968 /
# Randabstand 0.727).
BAUD_FINE_MIN_CROSSINGS = 40
BAUD_FINE_R2_MIN = 0.85
BAUD_FINE_PEAK_MARGIN = 0.3


# ---------------------------------------------------------------------------
# Lesen (WAV + Sidecar)
# ---------------------------------------------------------------------------

def read_wav_iq(path: str, t0_s: float | None = None, t1_s: float | None = None):
    """Liest eine Stereo-16-Bit-WAV. Liefert (framerate, z) mit z als
    komplexem numpy-Array (I + j*Q) / 32768.0. Wirft ValueError bei falschem
    Kanal-/Bitformat.

    Sind t0_s und t1_s gesetzt, wird NUR dieses Zeitfenster gelesen (T-1608,
    ueber wave.setpos()) - Voraussetzung dafuer, das Werkzeug auf lange
    Aufnahmen anzuwenden, ohne mehrere GB zu belegen (siehe MAX_WINDOW_S).
    Das Fenster wird auf die Dateilaenge geklemmt; laenger als MAX_WINDOW_S
    ist ein ValueError und keine stille Kuerzung - wer 20 Minuten anfordert,
    soll das erfahren und nicht kommentarlos 2 Minuten bekommen.

    Ohne Fenster bleibt das Verhalten unveraendert (ganze Datei), damit die
    bestehenden Aufrufer und der Selbsttest nichts merken.
    """
    with wave.open(path, "rb") as w:
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        nframes = w.getnframes()
        if nchannels != 2:
            raise ValueError(f"expected 2 channels (stereo I/Q), file has {nchannels}")
        if sampwidth != 2:
            raise ValueError(f"expected 16 bit, file has {sampwidth * 8} bit")
        if t0_s is None or t1_s is None:
            raw = w.readframes(nframes)
        else:
            if t1_s <= t0_s:
                raise ValueError(f"empty time window: t0={t0_s}, t1={t1_s}")
            if (t1_s - t0_s) > MAX_WINDOW_S:
                raise ValueError(
                    f"time window too large: {t1_s - t0_s:.1f} s, at most "
                    f"{MAX_WINDOW_S:.0f} s")
            start = max(0, int(t0_s * framerate))
            stop = min(nframes, int(t1_s * framerate))
            if start >= nframes or stop <= start:
                raise ValueError(
                    f"time window lies outside the recording "
                    f"({nframes / framerate:.1f} s)")
            w.setpos(start)
            raw = w.readframes(stop - start)
    raw_i16 = np.frombuffer(raw, dtype="<i2")
    n_complex = len(raw_i16) // 2
    iq = raw_i16[: n_complex * 2].reshape(-1, 2).astype(np.float64)
    z = (iq[:, 0] + 1j * iq[:, 1]) / 32768.0
    return framerate, z


def read_sidecar(wav_path: str):
    sidecar_path = wav_path + ".json"
    if not os.path.exists(sidecar_path):
        return None
    with open(sidecar_path, encoding="utf-8") as f:
        return json.load(f)


def resolve_fs(framerate_header: int, sidecar) -> int:
    if sidecar is None:
        print("WARNUNG: keine Sidecar-Datei gefunden, Abtastrate aus WAV-Header uebernommen", file=sys.stderr)
        return framerate_header
    if sidecar.get("is_iq") is False:
        raise ValueError("sidecar reports is_iq=false - not an IQ recording")
    sample_rate = sidecar.get("sample_rate_hz")
    return int(sample_rate) if sample_rate else framerate_header


# ---------------------------------------------------------------------------
# Dezimation (Anti-Aliasing-Tiefpass + Unterabtastung, siehe PLAN.md T-903)
# ---------------------------------------------------------------------------

def lowpass_kernel(n: int) -> np.ndarray:
    """FIR-Tiefpass fuer die Dezimation um den ganzzahligen Faktor n:
    Grenzfrequenz 0.4 * (fs/n) / 2, Fensterlaenge 8*n+1, Hamming-Fenster,
    sinc-Kern auf Summe 1 normiert."""
    taps = 8 * n + 1
    m = np.arange(taps) - (taps - 1) / 2.0
    cutoff_rel = 0.4 / n  # (0.4 * (fs/n)/2) / (fs/2), kuerzt sich aus fs
    kernel = np.sinc(cutoff_rel * m) * np.hamming(taps)
    kernel /= kernel.sum()
    return kernel


def tune_to_baseband(z: np.ndarray, fs: int, tune_hz: float) -> np.ndarray:
    """Verschiebt das Signal um -tune_hz, legt also die Stelle tune_hz auf
    0 Hz (T-1620).

    Ohne das laesst sich ein Signal, das NICHT in der Bandmitte liegt, nicht
    stark genug dezimieren: der Tiefpass in apply_decimation() sitzt um 0 Hz
    und schnitte es weg. Befund 2026-09-10 an einer RTTY-Aufnahme
    (20260910_224408_11038kHz_IQ.wav, 32 kHz, Traeger bei +293 Hz, Hub
    189 Hz): bei --decimate 64 (500 Hz effektiv, Durchlass +-250 Hz) fiel die
    Mark-Frequenz heraus, bei --decimate 16 blieben 2 kHz Rauschbandbreite
    auf 190 Hz Signal - Flatness 0,982 gegen Schwelle 0,94, also als Rauschen
    eingestuft. Nach der Verschiebung passen beide Toene in einen schmalen
    Durchlass, und das Rauschband schrumpft um denselben Faktor.
    """
    if not tune_hz:
        return z
    n = np.arange(len(z), dtype=np.float64)
    return z * np.exp(-2j * np.pi * float(tune_hz) * n / float(fs))


def suggest_decimation(fs: int, bandbreite_hz: float, min_baud: float,
                        reserve: float = 1.5) -> int:
    """Groesster Dezimationsfaktor (Zweierpotenz), bei dem bandbreite_hz noch
    mit Reserve in den Durchlassbereich passt (T-1620).

    Der Tiefpass laesst 0,4 * fs_eff / 2 = 0,2 * fs_eff nach jeder Seite
    durch. Die halbe Signalbandbreite muss hineinpassen, also
    fs_eff >= bandbreite_hz * 2,5 * reserve. Zweierpotenzen, weil der
    Kern mit 8n+1 Taps sonst unnoetig lang wird. Mindestens 4 Samples je
    Symbol bleiben gewahrt (dieselbe Bedingung wie in apply_decimation).
    """
    if bandbreite_hz <= 0:
        return 1
    fs_min = max(bandbreite_hz * 2.5 * reserve, 4.0 * min_baud)
    n = 1
    while fs / (n * 2) >= fs_min:
        n *= 2
    return n


def apply_decimation(z: np.ndarray, fs: int, n: int, min_baud: float, _skip_lowpass: bool = False):
    """Dezimiert das komplexe IQ-Signal z um den ganzzahligen Faktor n VOR
    der Momentanfrequenzberechnung. Liefert (z_eff, fs_eff). _skip_lowpass
    ist ein interner Testpfad fuer den Aliasing-Nachweis (--selftest T13)
    und ueber die Kommandozeile nicht erreichbar. Wirft ValueError bei
    n < 1, RuntimeError, wenn die effektive Abtastrate fs/n die geforderten
    4 Samples je Symbol bei min_baud unterschreitet."""
    if n < 1:
        raise ValueError(f"--decimate must be an integer >= 1 (given: {n})")
    fs_eff = fs / n if n > 1 else fs
    if fs_eff < 4.0 * min_baud:
        raise RuntimeError(
            f"decimation too strong: effective sample rate fs/N={fs_eff:.2f} Hz, "
            f"required >= 4 * min-baud = {4.0 * min_baud:.2f} Hz (min-baud={min_baud})"
        )
    if n == 1:
        return z, fs_eff
    if _skip_lowpass:
        z_eff = z[::n]
    else:
        kernel = lowpass_kernel(n)
        z_eff = np.convolve(z, kernel, mode="same")[::n]
    return z_eff, fs_eff


# ---------------------------------------------------------------------------
# Momentanfrequenz, Glaettung, Burst-Erkennung
# ---------------------------------------------------------------------------

def instantaneous_freq_hz(z: np.ndarray, fs: int) -> np.ndarray:
    prod = z[1:] * np.conj(z[:-1])
    return np.angle(prod) * fs / (2.0 * math.pi)


def moving_average(x: np.ndarray, window: int, circular: bool = False) -> np.ndarray:
    """circular=True fuer zyklische Groessen (z.B. Phasenhistogramm ueber
    [-pi, pi), PLAN.md T-904): Bin 0 und Bin N-1 sind Nachbarn statt durch
    Nullen getrennt. Default False (FSK-Pfad, linearer Frequenzbereich)
    unveraendert."""
    window = max(1, int(window))
    if window <= 1:
        return x.astype(np.float64, copy=True)
    kernel = np.ones(window, dtype=np.float64) / window
    if circular:
        pad = window // 2
        x_ext = np.concatenate([x[-pad:], x, x[:pad]]) if pad > 0 else x
        smoothed = np.convolve(x_ext, kernel, mode="same")
        return smoothed[pad:pad + len(x)] if pad > 0 else smoothed
    return np.convolve(x, kernel, mode="same")


def contiguous_regions(mask: np.ndarray, min_len: int):
    regions = []
    n = len(mask)
    i = 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= min_len:
                regions.append((i, j))
            i = j
        else:
            i += 1
    return regions


def histogram_flatness(f: np.ndarray, fs: int, bins: int = N_ZERO_HIST_BINS) -> float:
    edges = np.linspace(-fs / 4.0, fs / 4.0, bins + 1)
    hist, _ = np.histogram(f, bins=edges)
    total = hist.sum()
    if total <= 0:
        return 1.0
    q = hist[hist > 0] / total
    entropy = -np.sum(q * np.log(q))
    return float(entropy / math.log(bins))


def compute_contrast_and_flatness(z: np.ndarray, fs: int):
    """Liefert (contrast_db, flatness, p_smooth, p10, p95, f_full). Von
    detect_bursts UND vom Selbsttest (T6/T7, Regressionsschutz der Schwelle)
    genutzt - deshalb unabhaengig von der Burst-Entscheidung gehalten."""
    p = np.abs(z) ** 2
    window = max(1, round(0.005 * fs))
    p_smooth = moving_average(p, window)
    p10 = float(np.percentile(p_smooth, 10))
    p95 = float(np.percentile(p_smooth, 95))
    contrast_db = 10.0 * math.log10(p95 / p10) if p10 > 0 else float("inf")
    f_full = instantaneous_freq_hz(z, fs)
    flatness = histogram_flatness(f_full, fs)
    return contrast_db, flatness, p_smooth, p10, p95, f_full


def _luecken_sind_leer(p_smooth: np.ndarray, f_full: np.ndarray, bursts, fs: int) -> bool:
    """Ist zwischen den Bursts wirklich nichts? (T-1622)

    Amplitudenkontrast allein unterscheidet TASTEN nicht von FADING. Befund
    2026-09-10 an einer RTTY-Aufnahme (20260910_224408_11038kHz_IQ.wav):
    8,98 dB Kontrast fuehrten zu drei "Bursts" von je 130 ms aus mehreren
    Sekunden Dauersignal - bei 45 Bd bleiben daraus drei Symbole, und die
    Folge war eine Kette unbrauchbarer Werte (zehn Pegel statt zwei,
    Augenoeffnung 0,0 %, ein Hub aus zwei Ausreissern).

    Der Pegel in den Luecken taugt NICHT als Kriterium: bei 10 dB
    Stoerabstand liegt der Rauschboden eines echt getasteten Signals bei
    10 % der Burstleistung, also im selben Bereich wie ein Fading-Einbruch.
    Entschieden wird stattdessen ueber die MOMENTANFREQUENZ in den Luecken -
    bei einer echten Tastpause ist dort nur Rauschen, bei einem gefadeten
    Dauersignal steht die Modulation weiter.

    Kalibriert 2026-09-11 ueber zwei Seeds, drei Fadingtiefen (0,5/0,75/0,9)
    und die Stoerabstaende 20/10/6 dB:
        gefadetes Dauersignal   Flatness in den Luecken 0,745 .. 0,950
        echt getastet           0,988 .. 0,996
    Die Schwelle liegt mittig in der Luecke. Unterhalb von etwa 6 dB
    Stoerabstand traegt die Unterscheidung nicht mehr (bei 3 dB erreicht auch
    das Fading 0,974) - dort ist das Signal aber ohnehin zu schwach fuer eine
    Symbolanalyse.
    """
    belegt = np.zeros(len(p_smooth), dtype=bool)
    for anfang, ende in bursts:
        belegt[anfang:ende] = True
    n = min(len(f_full), len(belegt))
    luecke = ~belegt[:n]
    if luecke.sum() < BURST_GAP_MIN_SAMPLES:
        return True          # zu wenig Luecke fuer eine Aussage
    return histogram_flatness(f_full[:n][luecke], fs) >= BURST_GAP_FLATNESS


def detect_bursts(z: np.ndarray, fs: int):
    """Liefert (bursts, mode, contrast_db, flatness, f_full). bursts: Liste
    (start, end) in Samples (end exklusiv), mode: 'getastet' oder
    'dauersignal'. Wirft RuntimeError (Aufrufer -> Exit 2), wenn kein Burst
    gefunden wird."""
    contrast_db, flatness, p_smooth, p10, p95, f_full = compute_contrast_and_flatness(z, fs)
    min_len = max(1, round(0.1 * fs))

    if contrast_db >= 6.0:
        thr = math.sqrt(p10 * p95)
        bursts = contiguous_regions(p_smooth > thr, min_len)
        if not bursts:
            raise RuntimeError("no burst of sufficient length")
        if _luecken_sind_leer(p_smooth, f_full, bursts, fs):
            return bursts, "getastet", contrast_db, flatness, f_full
        return [(0, len(z))], "dauersignal", contrast_db, flatness, f_full

    if flatness <= FLATNESS_THR:
        return [(0, len(z))], "dauersignal", contrast_db, flatness, f_full

    raise RuntimeError("no burst detected (noise)")


# ---------------------------------------------------------------------------
# Frequenzoffset und Hub (Histogramm-Peaks der unkorrigierten Momentanfrequenz)
# ---------------------------------------------------------------------------

def plateau_local_maxima(counts, circular: bool = False) -> list[int]:
    """Lokale Maxima wie beim strikten Nachbarvergleich, aber ein Plateau aus
    zwei oder mehr exakt gleichen Nachbarbins wird zu einem Lauf zusammen-
    gefasst; der Lauf gilt als Maximum, wenn sein Wert strikt groesser ist
    als der Wert des vorhergehenden und des nachfolgenden Laufs. Zurueck-
    gegeben wird die untere Laufmitte (lauf_start + (lauf_laenge - 1) // 2).
    Ein Lauf der Laenge 1 reduziert sich exakt auf den strikten Vergleich
    (Rueckwaertskompatibilitaet, siehe --selftest T19).

    circular=False (linear): ein Lauf, der Bin 0 oder Bin n-1 beruehrt, hat
    keinen vollstaendigen Nachbarn und wird verworfen (wie bisher).
    circular=True (zyklisch): Bin 0 und Bin n-1 gelten als benachbart. Sind
    alle Werte gleich, gibt es kein Maximum (leere Liste). Sonst wird ab
    einem Bin s mit counts[s] != counts[(s - 1) % n] zyklisch in Laeufe
    zerlegt, damit kein Lauf ueber die Array-Grenze hinweg gespalten wird;
    die zurueckgegebenen Indizes beziehen sich auf das unrotierte Array
    (PLAN.md T-906)."""
    n = len(counts)
    if n == 0:
        return []

    def runs_of(values) -> list[tuple[int, int]]:
        out = []
        i = 0
        m = len(values)
        while i < m:
            j = i
            while j + 1 < m and values[j + 1] == values[i]:
                j += 1
            out.append((i, j - i + 1))
            i = j + 1
        return out

    if not circular:
        runs = runs_of(counts)
        result = []
        for k in range(len(runs)):
            start, length = runs[k]
            if start == 0 or start + length == n:
                continue
            val = counts[start]
            prev_val = counts[runs[k - 1][0]]
            next_val = counts[runs[k + 1][0]]
            if val > prev_val and val > next_val:
                result.append(start + (length - 1) // 2)
        return result

    if all(counts[i] == counts[0] for i in range(n)):
        return []
    s = next(i for i in range(n) if counts[i] != counts[(i - 1) % n])
    rotated = [counts[(s + k) % n] for k in range(n)]
    runs = runs_of(rotated)
    num_runs = len(runs)
    result = []
    for k in range(num_runs):
        start, length = runs[k]
        val = rotated[start]
        prev_val = rotated[runs[(k - 1) % num_runs][0]]
        next_val = rotated[runs[(k + 1) % num_runs][0]]
        if val > prev_val and val > next_val:
            mid_rotated = start + (length - 1) // 2
            result.append((s + mid_rotated) % n)
    return result


def find_histogram_peaks(counts: np.ndarray, min_rel_height: float = 0.20, min_bin_distance: int = 5,
                          circular: bool = False):
    """Groesste Peaks zuerst akzeptiert; ein Kandidat wird verworfen, wenn er
    weniger als min_bin_distance Bins von einem bereits akzeptierten Peak
    entfernt liegt (verhindert Doppelzaehlung eines breiten Clusters).
    circular=True fuer zyklische Groessen (z.B. Phasenhistogramm ueber
    [-pi, pi), PLAN.md T-904): Bin 0 und Bin N-1 gelten als benachbart, sonst
    wird ein Cluster genau am Rand faelschlich in zwei Peaks gespalten.
    Default False (FSK-Pfad, linearer Frequenzbereich) unveraendert. Plateaus
    aus exakt gleichen Nachbarbins werden als ein Peak in der Laufmitte
    erkannt (PLAN.md T-906)."""
    n = len(counts)
    local_max = plateau_local_maxima(counts, circular)
    if not local_max:
        return []
    max_height = max(counts[i] for i in local_max)
    threshold = min_rel_height * max_height
    candidates = sorted((i for i in local_max if counts[i] >= threshold), key=lambda i: counts[i], reverse=True)
    accepted: list[int] = []
    for i in candidates:
        if circular:
            ok = all(min(abs(i - a), n - abs(i - a)) >= min_bin_distance for a in accepted)
        else:
            ok = all(abs(i - a) >= min_bin_distance for a in accepted)
        if ok:
            accepted.append(i)
    accepted.sort()
    return accepted


def freq_offset_and_deviation(f_burst: np.ndarray, fs: int):
    """Liefert (offset_hz, dev_hz, peak_freqs_hz, f_korr). peak_freqs_hz
    aufsteigend sortiert - ihre Anzahl ist zugleich die Pegelanzahl. Wirft
    RuntimeError, wenn weniger als 2 Peaks akzeptiert werden (Aufrufer ->
    Exit 2)."""
    edges = np.linspace(-fs / 4.0, fs / 4.0, N_ZERO_HIST_BINS + 1)
    hist, _ = np.histogram(f_burst, bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2.0
    smoothed = moving_average(hist.astype(np.float64), 3)
    peak_idx = find_histogram_peaks(smoothed)
    if len(peak_idx) < 2:
        raise RuntimeError("only one level detected, no FSK character")
    peak_freqs = sorted(float(centers[i]) for i in peak_idx)
    p_min, p_max = peak_freqs[0], peak_freqs[-1]
    offset_hz = (p_min + p_max) / 2.0
    dev_hz = (p_max - p_min) / 2.0
    f_korr = f_burst - offset_hz
    return offset_hz, dev_hz, peak_freqs, f_korr


def tone_metrics(peak_freqs_hz, dev_hz: float, baud: float) -> dict:
    """Modulationsindex und Tonabstaende aus bereits vorhandenen Kennwerten
    (T-1701). Reine Rechnung, keine Deutung - Kandidatenvergleich und
    Modenname kommen erst in T-1702/T-1703 ueber eine eigene Tabelle.

    dev_hz ist der HALBE AEUSSERE Peakabstand (siehe freq_offset_and_deviation):
    bei mehr als zwei Pegeln also die halbe Gesamtspanne, nicht der Abstand
    zweier benachbarter Toene. mod_index = 2*dev_hz/baud verwendet bewusst
    diese Gesamtspanne - eine Verwechslung mit dem Tonabstand waere in
    T-1703 teuer (falsche Tabellentreffer).

    tone_spacings_hz sind die Abstaende ZWISCHEN benachbarten Peaks
    (Laenge level_count - 1), peak_freqs_hz wird als aufsteigend sortiert
    vorausgesetzt (Garantie von freq_offset_and_deviation)."""
    mod_index = (2.0 * dev_hz / baud) if baud > 0 else None
    spacings = [b - a for a, b in zip(peak_freqs_hz, peak_freqs_hz[1:])]
    if spacings:
        mean = float(np.mean(spacings))
        std = float(np.std(spacings))
        cv = (std / mean) if mean > 0 else 0.0
    else:
        mean = 0.0
        cv = 0.0
    return {
        "mod_index": mod_index,
        "tone_spacings_hz": spacings,
        "tone_spacing_mean_hz": mean,
        "tone_spacing_cv": cv,
    }


def zero_crossings(f_korr: np.ndarray) -> np.ndarray:
    """Nur noch fuer die Jitter-Kennzahl gebraucht, nicht mehr fuer die
    Symbolraten-Schaetzung (siehe estimate_symbol_duration)."""
    signs = np.sign(f_korr)
    if np.any(signs == 0):
        last = 0.0
        filled = signs.copy()
        for i in range(len(filled)):
            if filled[i] == 0:
                filled[i] = last
            else:
                last = filled[i]
        signs = filled
    change_idx = np.where(np.diff(signs) != 0)[0]
    y0 = f_korr[change_idx]
    y1 = f_korr[change_idx + 1]
    denom = y1 - y0
    denom_safe = np.where(denom == 0, 1.0, denom)
    frac = np.clip(-y0 / denom_safe, 0.0, 1.0)
    return change_idx + frac


def nearest_standard_baud(baud: float):
    best = None
    best_rel = None
    for std in STANDARD_BAUDS:
        rel = abs(baud - std) / std
        if rel <= BAUD_TOL_ZUFALLSFOLGE and (best_rel is None or rel < best_rel):
            best, best_rel = std, rel
    return best


# ---------------------------------------------------------------------------
# Symbolrate: Geradenanpassung an die Autokorrelationsflanke
# ---------------------------------------------------------------------------

def autocorr_normalized(s: np.ndarray) -> np.ndarray:
    n = len(s)
    nfft = 1
    while nfft < 2 * n:
        nfft *= 2
    S = np.fft.rfft(s, n=nfft)
    ac_full = np.fft.irfft(S * np.conj(S), n=nfft)
    ac = ac_full[:n]
    return ac / ac[0]


def autocorr_interp(ac: np.ndarray, tau: float) -> float:
    i0 = int(math.floor(tau))
    frac = tau - i0
    if i0 < 0:
        return float(ac[0])
    if i0 + 1 >= len(ac):
        return float(ac[-1])
    return float(ac[i0] * (1.0 - frac) + ac[i0 + 1] * frac)


def estimate_symbol_duration(f_korr: np.ndarray, fs: int, min_baud: float = 20.0):
    """Liefert (T, T_fit, r2, case, r_2tfit, lag_limit). T ist der finale
    Schaetzwert (bei erkannter Wechselfolge = 2*T_fit), T_fit die reine
    Geradenextrapolation der Autokorrelationsflanke auf ihre Nullstelle.
    lag_limit = fs / min_baud begrenzt den abgesuchten Lag-Bereich (PLAN.md
    T-903, ersetzt die vormals unbegrenzte Suche). Wirft RuntimeError bei zu
    kurzer/steigender Flanke (Aufrufer -> Exit 2)."""
    s = f_korr - np.mean(f_korr)
    n = len(s)
    ac = autocorr_normalized(s)

    lag_limit = max(1, min(n - 1, int(fs / min_baud)))

    mask = (ac[1:lag_limit + 1] >= 0.25) & (ac[1:lag_limit + 1] <= 0.75)
    qualifying = np.where(mask)[0] + 1
    if len(qualifying) == 0:
        raise RuntimeError("autocorrelation slope too short, symbol rate cannot be determined")
    lo = int(qualifying[0])
    run = [lo]
    lag = lo + 1
    while lag <= lag_limit and 0.25 <= ac[lag] <= 0.75:
        run.append(lag)
        lag += 1
    if len(run) < 4:
        raise RuntimeError("autocorrelation slope too short, symbol rate cannot be determined")

    taus = np.array(run, dtype=np.float64)
    Rs = ac[run]
    A = np.vstack([np.ones_like(taus), taus]).T
    coeffs, *_ = np.linalg.lstsq(A, Rs, rcond=None)
    a, b = coeffs
    if b >= 0:
        raise RuntimeError("no falling autocorrelation slope")
    T_fit = float(-a / b)
    pred = a + b * taus
    ss_res = float(np.sum((Rs - pred) ** 2))
    ss_tot = float(np.sum((Rs - np.mean(Rs)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    r_2tfit = autocorr_interp(ac, 2.0 * T_fit) if 2.0 * T_fit < n - 1 else None
    if r_2tfit is not None and r_2tfit < -0.5:
        return 2.0 * T_fit, T_fit, r2, "wechselfolge", r_2tfit, lag_limit
    return T_fit, T_fit, r2, "zufallsfolge", r_2tfit, lag_limit


def find_clock_phase(f_korr: np.ndarray, T: float, n_phases: int = N_CLOCK_PHASES) -> float:
    n = len(f_korr)
    idx_all = np.arange(n)
    best_phase = 0.0
    best_score = -1.0
    for k in range(n_phases):
        phase = k * T / n_phases
        positions = np.arange(phase, n - 1, T)
        if len(positions) == 0:
            continue
        values = np.interp(positions, idx_all, f_korr)
        score = float(np.mean(np.abs(values)))
        if score > best_score:
            best_score = score
            best_phase = float(phase)
    return best_phase


# ---------------------------------------------------------------------------
# Augendiagramm, Kennzahlen
# ---------------------------------------------------------------------------

def build_eye_traces(f_korr: np.ndarray, T: float, centers_all: np.ndarray, max_traces: int,
                      n_points: int = N_EYE_POINTS):
    n = len(f_korr)
    idx_all = np.arange(n)
    if len(centers_all) == 0:
        return np.empty((0, n_points)), np.array([])
    if len(centers_all) > max_traces:
        pick = np.unique(np.round(np.linspace(0, len(centers_all) - 1, max_traces)).astype(int))
        plotted_centers = centers_all[pick]
    else:
        plotted_centers = centers_all
    offsets = np.linspace(-T, T, n_points)
    traces = np.empty((len(plotted_centers), n_points))
    for row, c in enumerate(plotted_centers):
        traces[row] = np.interp(c + offsets, idx_all, f_korr)
    return traces, plotted_centers


def eye_opening(values_at_symbol: np.ndarray, dev_hz: float):
    above = values_at_symbol[values_at_symbol > 0]
    below = values_at_symbol[values_at_symbol < 0]
    if len(above) == 0 or len(below) == 0:
        return 0.0, 0.0
    opening_hz = float(np.min(above) - np.max(below))
    opening_pct = 100.0 * opening_hz / (2.0 * dev_hz) if dev_hz > 0 else 0.0
    return opening_hz, opening_pct


def jitter_percent(positions: np.ndarray, T: float) -> float:
    if len(positions) == 0 or T <= 0:
        return 0.0
    mod = np.mod(positions, T)
    return float(100.0 * np.std(mod) / T)


def crossing_grid(positions: np.ndarray, T: float, phase: float) -> dict:
    """Deutet den Jitter statt ihn nur zu messen (T-1710, Befund T-1709: ein
    Jitter, der den Nachdetektionsfilter uebersteht, ist kein Rauschen).

    r_i = ((positions_i - phase) mod T) / T, in [0, 1) - anders als
    jitter_percent() (dort ohne Phasenbezug, mod T direkt auf die absolute
    Sample-Position) wird hier gegen die erkannte Taktlage gemessen: nur so
    lassen sich 0 (volles Bit-Raster) und 0,5 (halbes Bit-Raster, z. B. ITA2
    mit 1,5 Stoppbits) als feste Orte unterscheiden.

    Zyklisches Histogramm ueber N_CROSSING_HIST_BINS Bins (Bin 0 und Bin
    N-1 sind Nachbarn - eine Haeufung bei r~0 darf nicht am Rand in zwei
    Peaks zerfallen), geglaettet mit moving_average(circular=True) und
    ausgewertet mit find_histogram_peaks(circular=True) - beide bestehen seit
    T-906, hier nur neu verdrahtet.

    Rueckgabe: crossing_peaks (aufsteigend, r in [0,1)), crossing_grid
    ("full"/"half"/"unclear", sprachneutral), crossing_concentration (Anteil
    der Nulldurchgaenge im Peakbin plus je einem Nachbarbin je Peak, ohne
    Doppelzaehlung bei ueberlappenden Bereichen)."""
    n = len(positions)
    if n < CROSSING_MIN_COUNT or T <= 0:
        return {"crossing_peaks": [], "crossing_grid": "unclear", "crossing_concentration": 0.0}

    r = np.mod((positions - phase) / T, 1.0)
    edges = np.linspace(0.0, 1.0, N_CROSSING_HIST_BINS + 1)
    hist, _ = np.histogram(r, bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2.0
    smoothed = moving_average(hist.astype(np.float64), 3, circular=True)
    peak_idx = find_histogram_peaks(smoothed, circular=True)

    total = int(hist.sum())
    nb = N_CROSSING_HIST_BINS

    def share(i: int) -> float:
        if total <= 0:
            return 0.0
        return float(hist[(i - 1) % nb] + hist[i] + hist[(i + 1) % nb]) / total

    covered: set[int] = set()
    for i in peak_idx:
        covered.update({(i - 1) % nb, i, (i + 1) % nb})
    concentration = float(sum(hist[j] for j in covered)) / total if total > 0 else 0.0

    crossing_peaks = [float(centers[i]) for i in peak_idx]

    if len(peak_idx) == 1:
        grid = "full"
    elif len(peak_idx) == 2:
        d = abs(centers[peak_idx[0]] - centers[peak_idx[1]])
        cyclic_d = min(d, 1.0 - d)
        if (abs(cyclic_d - 0.5) <= CROSSING_HALF_TOL
                and share(peak_idx[0]) >= CROSSING_PEAK_MIN_SHARE
                and share(peak_idx[1]) >= CROSSING_PEAK_MIN_SHARE):
            grid = "half"
        else:
            grid = "unclear"
    else:
        grid = "unclear"

    return {"crossing_peaks": crossing_peaks, "crossing_grid": grid, "crossing_concentration": concentration}


def circular_resultant(positions: np.ndarray, T: float, harmonic: int = 1) -> float:
    """Betrag der zirkularen Resultierenden der gegebenen Harmonischen ueber
    positions, normiert auf [0, 1] (1 = alle Lagen exakt auf einem
    gemeinsamen Raster der Periode T/harmonic). T-1711 nutzt harmonic=2 in
    refine_symbol_rate() - siehe dort, warum die ERSTE Harmonische ein
    Halbbit-Raster faelschlich ausloescht."""
    n = len(positions)
    if n == 0 or T <= 0:
        return 0.0
    theta = 2.0 * math.pi * harmonic * positions / T
    return float(abs(np.sum(np.exp(1j * theta))) / n)


def refine_symbol_rate(positions: np.ndarray, T_coarse: float, n_symbols_approx: float,
                        tol: float = BAUD_TOL_ZUFALLSFOLGE) -> dict:
    """Feinabgleich der Symbolperiode auf den bereits vorhandenen
    Nulldurchgangslagen (T-1711). Der Flankenfit (estimate_symbol_duration)
    liefert die Rate auf BAUD_TOL_ZUFALLSFOLGE genau (1-6 %) - das reicht,
    um die Rate zu IDENTIFIZIEREN, nicht aber, um ueber tausende Symbole
    ausgerichtet zu bleiben (Befund PLAN.md T-1711: 1,53 % Fehler ergaben bei
    985 Symbolen rund 15 volle Periodendrift, Augenoeffnung 3,1 % statt
    94,0 % bei vorgegebener Rate).

    Kandidaten T durchsucht das Intervall [T_coarse*(1-tol), T_coarse*(1+tol)]
    mit einer Schrittweite fein genug, dass die Phasendrift ueber die ganze
    Aufnahme unter einem Achtel Zyklus bleibt: dT/T < 1/(8*n_symbols_approx).
    Der Suchbereich ist AN DIE GROBE SCHAETZUNG GEBUNDEN und nicht beliebig
    erweiterbar: R2(T) hat Nebenmaxima bei T/2, 2T und weiteren Vielfachen
    der wahren Periode (Alias der Harmonischen), nur eine hinreichend genaue
    grobe Schaetzung haelt die Suche in der Umgebung der richtigen Periode.

    Ausgewertet wird die ZWEITE Harmonische (circular_resultant(..., 2)):
    ein Halbbit-Raster (zwei um T/2 versetzte Populationen, siehe T-1710)
    loescht sich in der ERSTEN Harmonischen zu R1~0 aus - die Populationen
    liegen dort gegenphasig -, obwohl das Raster perfekt ist. In der zweiten
    Harmonischen liegen beide Faelle (ganzes wie halbes Raster) gleich gut
    bei R2~1: eine Verschiebung um T/2 aendert das Argument 2*pi*2*x/T um
    genau 2*pi, also nichts. Ohne die zweite Harmonische waere der
    Halbbit-Fall dieser Aufgabe (--selftest T35.2) unsichtbar.

    Angenommen wird der Feinwert NUR, wenn er sich belegen laesst - siehe
    BAUD_FINE_MIN_CROSSINGS/BAUD_FINE_R2_MIN/BAUD_FINE_PEAK_MARGIN. Sonst
    kein stiller Ruckfall: das Ergebnis nennt reject_reason, der Aufrufer
    behaelt T_coarse.

    Rueckgabe: {"T_fine": float|None, "r2": float|None, "accepted": bool,
    "reject_reason": str|None}. r2 ist auch bei Ablehnung gesetzt (ausser bei
    zu wenigen Nulldurchgaengen oder ohne erkennbares Maximum), damit der
    Bericht den gefundenen Wert nennen kann."""
    n = len(positions)
    if n < BAUD_FINE_MIN_CROSSINGS or T_coarse <= 0:
        return {"T_fine": None, "r2": None, "accepted": False, "reject_reason": "zu_wenige_nulldurchgaenge"}

    max_rel_step = 1.0 / (8.0 * max(1.0, n_symbols_approx))
    n_points = max(3, int(math.ceil((2.0 * tol) / max_rel_step)) + 1)
    Ts = np.linspace(T_coarse * (1.0 - tol), T_coarse * (1.0 + tol), n_points)
    r2_values = np.array([circular_resultant(positions, float(t), harmonic=2) for t in Ts])

    local_max_idx = plateau_local_maxima(r2_values, circular=False)
    if not local_max_idx:
        return {"T_fine": None, "r2": float(np.max(r2_values)), "accepted": False,
                "reject_reason": "kein_eindeutiges_maximum"}

    local_max_idx.sort(key=lambda i: r2_values[i], reverse=True)
    best_idx = local_max_idx[0]
    best_r2 = float(r2_values[best_idx])
    second_r2 = float(r2_values[local_max_idx[1]]) if len(local_max_idx) > 1 else 0.0
    margin = best_r2 - second_r2

    # Parabelverfeinerung durch die drei besten Stuetzstellen um best_idx.
    if 0 < best_idx < len(Ts) - 1:
        y0, y1, y2 = r2_values[best_idx - 1], r2_values[best_idx], r2_values[best_idx + 1]
        nenner = y0 - 2.0 * y1 + y2
        delta = 0.5 * (y0 - y2) / nenner if nenner != 0 else 0.0
        delta = max(-1.0, min(1.0, delta))
        schritt = float(Ts[1] - Ts[0])
        T_peak = float(Ts[best_idx]) + delta * schritt
    else:
        T_peak = float(Ts[best_idx])

    if best_r2 < BAUD_FINE_R2_MIN:
        return {"T_fine": T_peak, "r2": best_r2, "accepted": False, "reject_reason": "r2_zu_niedrig"}
    if margin < BAUD_FINE_PEAK_MARGIN:
        return {"T_fine": T_peak, "r2": best_r2, "accepted": False, "reject_reason": "maximum_nicht_eindeutig"}

    return {"T_fine": T_peak, "r2": best_r2, "accepted": True, "reject_reason": None}


# ---------------------------------------------------------------------------
# Analyse-Orchestrierung
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Hoerbarmachung (T-1623)
# ---------------------------------------------------------------------------
#
# Ein IQ-Mitschnitt laesst sich nicht direkt abspielen: er ist komplex und
# zweiseitig, eine Audiodatei reell und einseitig. Die Umwandlung ist dieselbe
# Rechnung, die ein Empfaenger im SSB-Betrieb macht - ins Basisband schieben,
# tiefpassfiltern, Realteil nehmen.
#
# Der SIDETONE-Versatz ist der Kniff: verschoben wird nicht auf 0 Hz, sondern
# so, dass die Signalmitte bei AUDIO_SIDETONE_HZ landet. Ein Traeger wird
# damit zu einem hoerbaren Ton statt zu Gleichspannung. Das ist der
# CW-Betrieb, und es passt zugleich fuer alles andere auf Kurzwelle: ein
# RTTY-Signal mit 170 Hz Hub liegt dann bei 615 und 785 Hz - genau die
# vertrauten Toene (Entscheidung 2026-09-11, OE3GAS: CW als einzige
# Betriebsart, Sprache wird ohnehin nicht in IQ aufgenommen).

AUDIO_RATE_HZ = 8000        # reicht fuer alles bis 4 kHz, klein in der Datei
AUDIO_SIDETONE_HZ = 700.0   # uebliche Mithoertonlage
AUDIO_MAX_S = 120.0         # wie MAX_WINDOW_S - laengeres waere unhandlich


def iq_to_audio(wav_path: str, t0_s: float | None = None, t1_s: float | None = None,
                 tune_hz: float = 0.0, bandbreite_hz: float = 0.0,
                 sidetone_hz: float = AUDIO_SIDETONE_HZ) -> tuple:
    """Macht ein IQ-Zeitfenster hoerbar. Liefert (rate, int16-Array).

    tune_hz ist die Mitte des interessierenden Bandes; sie landet auf
    sidetone_hz. bandbreite_hz steuert den Tiefpass - 0 heisst "kein
    zusaetzliches Filtern", dann bleibt das volle Band stehen.
    """
    fs, z = read_wav_iq(wav_path, t0_s, t1_s)
    if not len(z):
        raise RuntimeError("empty time window")
    if len(z) / fs > AUDIO_MAX_S:
        raise ValueError(f"section too long: {len(z) / fs:.1f} s, at most "
                          f"{AUDIO_MAX_S:.0f} s")

    # Erst auf 0 Hz schieben, dann filtern - der Tiefpass sitzt um die Mitte.
    z = tune_to_baseband(z, fs, tune_hz)
    if bandbreite_hz > 0:
        n_dez = suggest_decimation(fs, bandbreite_hz, 20.0)
        n_dez = min(n_dez, max(1, fs // AUDIO_RATE_HZ))
        if n_dez > 1:
            z, fs = apply_decimation(z, fs, n_dez, 20.0)
            fs = int(round(fs))

    # Auf die Zielrate bringen. Ganzzahlig dezimieren, solange es geht; der
    # Rest wird linear abgetastet - fuer's Hoeren voellig ausreichend.
    if fs > AUDIO_RATE_HZ:
        n_rest = max(1, int(fs // AUDIO_RATE_HZ))
        if n_rest > 1:
            z, fs = apply_decimation(z, fs, n_rest, 20.0)
            fs = int(round(fs))
    if fs != AUDIO_RATE_HZ:
        n_ziel = max(1, int(round(len(z) * AUDIO_RATE_HZ / fs)))
        idx = np.linspace(0, len(z) - 1, n_ziel)
        z = np.interp(idx, np.arange(len(z)), z.real) + \
            1j * np.interp(idx, np.arange(len(z)), z.imag)
        fs = AUDIO_RATE_HZ

    # Sidetone aufpraegen und Realteil nehmen - das ist die SSB-Demodulation.
    n = np.arange(len(z), dtype=np.float64)
    audio = np.real(z * np.exp(2j * np.pi * float(sidetone_hz) * n / fs))

    spitze = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if spitze > 0:
        audio = audio / spitze * 0.89        # Reserve gegen Uebersteuern
    return fs, np.clip(audio * 32767.0, -32768, 32767).astype("<i2")


def write_audio_wav(path: str, rate: int, samples: np.ndarray) -> None:
    """16-Bit-Mono-WAV schreiben (T-1623)."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(samples.tobytes())


# ---------------------------------------------------------------------------
# OFDM-Hinweis (T-1613)
# ---------------------------------------------------------------------------
#
# Kein eigener Auswertepfad, sondern eine ZUSATZAUSKUNFT fuer den Fall, dass
# FSK und PSK beide scheitern. Beide setzen einen einzelnen Traeger voraus,
# bis hinunter zur Momentanfrequenz; mehrtraegrige Verfahren (HFDL, Robust
# Packet, PACTOR IV, STANAG 4285) fallen dort zwangslaeufig durch. Aus zwei
# Fehlermeldungen wird so eine Aussage - und ein Hinweis darauf, dass die
# Feinarbeit nach iqscan gehoert.
#
# Drei Merkmale, aber NICHT gleichwertig:
#
#  1. Autokorrelation auf das zyklische Praefix. Jedes OFDM-Symbol traegt vorn
#     eine Kopie seines eigenen Endes, deshalb zeigt R(lag) einen Gipfel bei
#     der Nutzsymboldauer. Das ist das EINZIGE Merkmal mit Beweiskraft und
#     daher notwendige Bedingung: ohne Gipfel kein Hinweis.
#  2. Traegerabstand aus dem gemittelten Spektrum, als RUECKFALL: reisst das
#     globale Maximum die Schwelle nicht, wird ein vom Kamm benannter
#     Kandidat geprueft. NICHT umgekehrt - ein frueherer Entwurf liess den
#     Kamm immer entscheiden und ging schief, sobald er danebenlag.
#  3. Kurtosis der Momentanamplitude. OFDM mit vielen Traegern ist im
#     Zeitbereich nahezu gaussisch. NUR Bestaetigung - Rauschen ist das auch,
#     und ein "moeglicherweise OFDM" ueber einem leeren Band waere schlimmer
#     als gar keine Aussage.
#
# Die Aussage bleibt ausdruecklich Vermutung: auch ein eintraegriges Verfahren
# mit periodischer Praeambel erzeugt einen Gipfel.

OFDM_T_MIN_S = 0.0005     # kuerzeste gepruefte Nutzsymboldauer
OFDM_T_MAX_S = 0.100      # laengste gepruefte Nutzsymboldauer
OFDM_PEAK_FACTOR = 6.0    # Ueberhoehung ueber der Grundlinie, in MAD-Einheiten
OFDM_MIN_PEAK = 0.02      # ... und absolut mindestens so viel
OFDM_SPACING_TOL = 0.35   # zulaessige relative Abweichung Kamm gegen Gipfel


def _autokorr_profil(z: np.ndarray, fs: int):
    """R(lag) = |sum z[n] conj(z[n+lag])| / sum|z|^2 ueber den Bereich
    plausibler Nutzsymboldauern. Normiert, damit die Schwelle nicht von der
    Aussteuerung abhaengt."""
    lag_min = max(1, int(OFDM_T_MIN_S * fs))
    lag_max = min(len(z) // 2, int(OFDM_T_MAX_S * fs))
    if lag_max <= lag_min + 2:
        return None, None
    energie = float(np.sum(np.abs(z) ** 2))
    if energie <= 0:
        return None, None
    lags = np.arange(lag_min, lag_max)
    werte = np.empty(len(lags), dtype=np.float64)
    for i, lag in enumerate(lags):
        werte[i] = abs(np.vdot(z[:-lag], z[lag:])) / energie
    return lags, werte


def _gleitender_median(x: np.ndarray, w: int) -> np.ndarray:
    """Grundlinie fuer die Gipfelsuche: gleitender Median der Fensterbreite w
    (ungerade aufgerundet). Breit genug, um die Eigenkorrelation zu erfassen,
    und unempfindlich gegen den schmalen Praefix-Gipfel selbst."""
    w = max(3, w | 1)
    rand = w // 2
    erweitert = np.concatenate([np.full(rand, x[0]), x, np.full(rand, x[-1])])
    fenster = np.lib.stride_tricks.sliding_window_view(erweitert, w)
    return np.median(fenster, axis=1)


def _traegerabstand_hz(z: np.ndarray, fs: int, n_fft: int = 4096):
    """Traegerabstand aus dem gemittelten Betragsspektrum: bei einem Kamm
    zeigt dessen Autokorrelation einen Gipfel beim Traegerabstand. Liefert
    None, wenn kein Kamm erkennbar ist."""
    if len(z) < 2 * n_fft:
        return None
    bloecke = len(z) // n_fft
    spek = np.zeros(n_fft, dtype=np.float64)
    fenster = np.hanning(n_fft)
    for b in range(bloecke):
        stueck = z[b * n_fft:(b + 1) * n_fft] * fenster
        spek += np.abs(np.fft.fftshift(np.fft.fft(stueck))) ** 2
    spek /= bloecke
    mitte = spek - spek.mean()
    if not np.any(mitte):
        return None
    korr = np.correlate(mitte, mitte, mode="full")[len(mitte) - 1:]
    korr = korr / (korr[0] if korr[0] else 1.0)
    such = korr[2:min(len(korr), n_fft // 4)]
    if not len(such):
        return None
    idx = int(np.argmax(such)) + 2
    if korr[idx] < 0.15:
        return None
    return idx * fs / n_fft


def ofdm_hinweis(z: np.ndarray, fs: int):
    """Pruefung auf mehrtraegrige Struktur (T-1613). Liefert ein dict oder
    None - und None heisst: KEIN Hinweis, egal was die Bestaetigungsmerkmale
    sagen. Ohne Autokorrelationsgipfel gibt es keine Aussage."""
    lags, werte = _autokorr_profil(z, fs)
    if lags is None:
        return None

    # Der Praefix-Gipfel ist SCHMAL (Breite etwa n_cp), die Eigenkorrelation
    # eines bandbegrenzten Signals dagegen breit und bei kleinen Lags gross.
    # Ohne Abzug dieser Grundlinie gewinnt immer der untere Rand des
    # Suchbereichs - ein erster Entwurf fand bei synthetischem OFDM durchweg
    # 0,5 ms statt der 22 ms Symboldauer.
    grund = _gleitender_median(werte, max(5, len(werte) // 8))
    rest = werte - grund
    streu = float(np.median(np.abs(rest - np.median(rest)))) * 1.4826
    if streu <= 0:
        return None

    # Der Kamm dient als RUECKFALL, nicht als Vorrang. Erst das globale
    # Maximum: reisst es die Schwelle, ist es der Gipfel. Nur wenn nicht, wird
    # ein vom Traegerabstand benannter Kandidat geprueft. Die umgekehrte
    # Reihenfolge (Kamm entscheidet immer) war ein frueherer Entwurf und ging
    # schief, sobald der Kamm danebenlag: bei 200 Hz Traegerabstand lieferte
    # er 23 Hz und lenkte auf einen falschen Kandidaten - 1 von 6 Treffern
    # statt 6 von 6.
    schwelle = max(OFDM_PEAK_FACTOR * streu, OFDM_MIN_PEAK)
    abstand_gemessen = _traegerabstand_hz(z, fs)
    i = int(np.argmax(rest))
    if rest[i] < schwelle:
        if not abstand_gemessen or abstand_gemessen <= 0:
            return None
        soll_lag = fs / abstand_gemessen
        kandidaten = [k for k in range(1, len(rest) - 1)
                      if rest[k] >= rest[k - 1] and rest[k] >= rest[k + 1]
                      and abs(lags[k] - soll_lag) / soll_lag <= OFDM_SPACING_TOL]
        if not kandidaten:
            return None
        i = max(kandidaten, key=lambda k: rest[k])
        if rest[i] < 0.5 * schwelle:
            return None

    gipfel = float(werte[i])
    ueberhoehung = float(rest[i])

    t_u_s = float(lags[i]) / fs
    abstand_erwartet = 1.0 / t_u_s
    passt = None
    if abstand_gemessen is not None and abstand_erwartet > 0:
        passt = abs(abstand_gemessen - abstand_erwartet) / abstand_erwartet <= OFDM_SPACING_TOL

    betrag = np.abs(z)
    mittel = float(betrag.mean())
    streu_b = float(betrag.std())
    kurtosis = (float(np.mean((betrag - mittel) ** 4)) / (streu_b ** 4)
                if streu_b > 0 else None)

    return {
        "symbol_s": t_u_s,
        "traegerabstand_erwartet_hz": abstand_erwartet,
        "traegerabstand_gemessen_hz": abstand_gemessen,
        "abstand_passt": passt,
        "korr_gipfel": gipfel,
        "korr_ueberhoehung": ueberhoehung,
        "kurtosis": kurtosis,
    }


def analyze_ofdm_file(wav_path: str, t0_s: float | None = None,
                       t1_s: float | None = None, tune_hz: float = 0.0) -> dict:
    """OFDM-Pruefung fuer eine Datei bzw. ein Zeitfenster. Wirft RuntimeError
    (Aufrufer -> Exit 2), wenn kein Gipfel gefunden wird - dieselbe Lage wie
    "nichts Auswertbares" in den beiden anderen Pfaden."""
    fs, z = read_wav_iq(wav_path, t0_s, t1_s)
    z = tune_to_baseband(z, fs, tune_hz)
    hinweis = ofdm_hinweis(z, fs)
    if hinweis is None:
        raise RuntimeError("no multi-carrier structure detectable")
    hinweis["fs_hz"] = fs
    hinweis["n_samples"] = int(len(z))
    return hinweis


def _mode_candidates_for(result: dict) -> list[dict]:
    """Laedt die FSK-Kandidatentabelle (modes/fsk.json) und vergleicht sie
    gegen result (T-1703). Schlaegt das Laden fehl - Datei fehlt, ist kaputt
    oder verletzt das Schema -, ist das Ergebnis eine leere Liste und die
    Meldungen gehen nach stderr: eine defekte Tabelle darf eine gueltige
    Messung nicht scheitern lassen, sie zeigt dann eben keine Kandidaten."""
    tabelle, fehler = load_fsk_table()
    if fehler:
        for f in fehler:
            print(f"WARNUNG: Modentabelle nicht geladen - {f}", file=sys.stderr)
        return []
    return match_fsk(result, tabelle)


def analyze_file(wav_path: str, burst_index: int | None = None, baud_override: float | None = None,
                  max_traces: int = 400, decimate: int = 1, min_baud: float = 20.0,
                  _test_skip_lowpass: bool = False,
                  t0_s: float | None = None, t1_s: float | None = None,
                  tune_hz: float = 0.0, postdet_filter_rel: float | None = None) -> dict:
    # t0_s/t1_s (T-1608): nur dieses Zeitfenster auswerten. Der Rest der
    # Auswertung sieht das Fenster als ganze Aufnahme - Burstsuche, Offset
    # und Symbolrate beziehen sich also auf den Ausschnitt.
    framerate_header, z = read_wav_iq(wav_path, t0_s, t1_s)
    sidecar = read_sidecar(wav_path)
    fs = resolve_fs(framerate_header, sidecar)
    freq_hz = sidecar.get("freq_hz") if sidecar else None

    z = tune_to_baseband(z, framerate_header, tune_hz)          # T-1620
    z_eff, fs_eff = apply_decimation(z, fs, decimate, min_baud, _skip_lowpass=_test_skip_lowpass)

    # T-1701a: Breite eines Histogramm-Bins der Momentanfrequenz (siehe
    # freq_offset_and_deviation). Die Peaklagen sind Bin-MITTEN, 2*dev_hz ist
    # eine Differenz zweier solcher Lagen - der Quantisierungsfehler darauf
    # betraegt also bis zu einem ganzen Bin. eb200_core/mode_match.py (T-1703)
    # weitet die absoluten Frequenztoleranzen genau um diesen Betrag auf.
    freq_bin_hz = (fs_eff / 2.0) / N_ZERO_HIST_BINS

    bursts, mode, contrast_db, flatness, f_full = detect_bursts(z_eff, fs_eff)
    burst_durations_s = [(e - s) / fs_eff for s, e in bursts]

    if burst_index is None:
        chosen_i = max(range(len(bursts)), key=lambda i: bursts[i][1] - bursts[i][0])
    else:
        if not (0 <= burst_index < len(bursts)):
            raise IndexError(f"Burst-Index {burst_index} ausserhalb des gueltigen Bereichs (0..{len(bursts) - 1})")
        chosen_i = burst_index

    start, end = bursts[chosen_i]
    f_burst = f_full[start:end - 1]

    offset_hz, dev_hz, peak_freqs, f_korr = freq_offset_and_deviation(f_burst, fs_eff)

    baud_override_used = baud_override is not None
    if baud_override_used:
        baud_coarse = float(baud_override)
        T_fit = None
        r2 = None
        symbolrate_case = None
        r_2tfit = None
        lag_limit_samples = None
    else:
        T_est, T_fit, r2, symbolrate_case, r_2tfit, lag_limit_samples = estimate_symbol_duration(
            f_korr, fs_eff, min_baud)
        baud_coarse = fs_eff / T_est

    if fs_eff / baud_coarse < 4:
        raise RuntimeError("too few samples per symbol")
    T_coarse = fs_eff / baud_coarse

    # T-1709: Nachdetektionsfilter fuer Taktlage, Augendiagramm, Augenoeffnung
    # und Jitter - NICHT fuer Burst-Erkennung, Histogramm-Peaks oder
    # Symbolratenschaetzung (siehe Kommentar an POSTDET_FILTER_REL). Bei der
    # Mindestforderung von vier Samples je Symbol ist W=1 und f_det damit
    # elementweise identisch mit f_korr (moving_average() mit window<=1).
    # Die Fensterbreite bleibt an T_coarse gebunden, auch wenn T-1711 die
    # Rate anschliessend feinjustiert - eine Justierung innerhalb
    # +-BAUD_TOL_ZUFALLSFOLGE aendert ein gerundetes Fenster fast nie um
    # mehr als ein Sample und rechtfertigt keinen zweiten Filterdurchlauf.
    rel = POSTDET_FILTER_REL if postdet_filter_rel is None else postdet_filter_rel
    postdet_window_samples = max(1, round(rel * T_coarse))
    f_det = moving_average(f_korr, postdet_window_samples, circular=False)
    positions = zero_crossings(f_det)

    # T-1711: Feinabgleich der Symbolrate auf den bereits vorhandenen
    # Nulldurchgangslagen (siehe refine_symbol_rate()) - NUR ohne --baud-
    # Vorgabe. Wer eine Rate vorgibt, will sie auch benutzt sehen; ein
    # Feinabgleich obendrauf wuerde die Vorgabe unbemerkt unterlaufen.
    if baud_override_used:
        baud_fine_r2 = None
        baud_refined = False
        baud_fine_reject_reason = None
        baud = baud_coarse
        T = T_coarse
    else:
        n_symbols_approx = len(f_det) / T_coarse
        feinabgleich = refine_symbol_rate(positions, T_coarse, n_symbols_approx)
        baud_fine_r2 = feinabgleich["r2"]
        baud_refined = feinabgleich["accepted"]
        baud_fine_reject_reason = feinabgleich["reject_reason"]
        if baud_refined:
            T = feinabgleich["T_fine"]
            baud = fs_eff / T
        else:
            baud = baud_coarse
            T = T_coarse

    # Alles ab hier - Kandidatenvergleich (tone_metrics), Taktphase,
    # Augendiagramm, Augenoeffnung, Jitter, Nulldurchgangsraster - rechnet
    # mit dem ENDGUELTIGEN baud/T (fein, falls uebernommen, sonst grob).
    tones = tone_metrics(peak_freqs, dev_hz, baud)

    standard_baud = nearest_standard_baud(baud)
    phase = find_clock_phase(f_det, T)

    n = len(f_det)
    idx_all = np.arange(n)
    centers_all = np.arange(phase, n - 1, T)
    valid = (centers_all - T >= 0) & (centers_all + T <= n - 1)
    centers_all = centers_all[valid]
    values_at_symbol = np.interp(centers_all, idx_all, f_det) if len(centers_all) else np.array([])

    traces, plotted_centers = build_eye_traces(f_det, T, centers_all, max_traces)

    opening_hz, opening_pct = eye_opening(values_at_symbol, dev_hz)
    jitter_pct = jitter_percent(positions, T)

    # T-1710: deutet den Jitter statt ihn nur zu messen. jitter_half_pct
    # verwendet dieselbe Funktion wie jitter_pct, nur gegen ein Raster von
    # T/2 statt T - jitter_pct selbst bleibt unveraendert (kein Ersetzen,
    # kein Umrechnen).
    #
    # T-1710a: jitter_percent() normiert auf die RASTERWEITE, der Aufruf mit
    # T/2 lieferte also Prozent einer halben Symboldauer. Neben jitter_pct
    # (Prozent einer ganzen) gelesen war das irrefuehrend - gemessen 25,1
    # gegen 2,608, was wie 1:10 aussieht und 1:19 ist. Der Faktor 0,5 bringt
    # beide Werte auf dieselbe Bezugsgroesse.
    crossing = crossing_grid(positions, T, phase)
    jitter_half_pct = (0.5 * jitter_percent(positions, T / 2.0)
                       if crossing["crossing_grid"] == "half" else None)

    # T-1715: Rahmensuche auf dem Halbbit-Raster. Die Abtastpunkte liegen
    # bewusst NICHT bei phase + k*T/2 - das traefe abwechselnd Symbolmitte
    # und Symbolgrenze. Um T/4 versetzt liegt jeder Punkt in der Mitte eines
    # Halbbits, und zwar auch fuer ein um ein halbes Bit verschobenes
    # Zeichen (1,5 Stoppbits).
    #
    # Die Suche laeuft NUR bei belastbarer Symbolrate. Mit der groben
    # Schaetzung (bis 11 % Fehler bei kurzen Bursts, PLAN.md T-1711) wandert
    # das Bitraster ueber die Aufnahme davon, und die Start/Stopp-Bedingung
    # misst dann Zufall - derselbe Befund, der T-1710 zunaechst in die Irre
    # fuehrte.
    if baud_refined or baud_override_used:
        halfbit_centers = np.arange(phase - T / 4.0, n - 1, T / 2.0)
        halfbit_centers = halfbit_centers[halfbit_centers >= 0]
        bits_half = [int(v > 0.0) for v in np.interp(halfbit_centers, idx_all, f_det)]
        framing = detect_async_framing(bits_half)
    else:
        bits_half = []
        framing = {"frame_halfbits": None, "frame_bits": None, "interpretations": [],
                   "polarity": None, "phase_halfbits": None, "score": None,
                   "margin": None, "n_frames": 0, "accepted": False,
                   "reject_reason": "symbolrate_nicht_feinabgeglichen", "scores": []}

    # T-1716: Zeichenvorrat der gueltigen Rahmen. Erklaert unter anderem den
    # Start/Stopp-Trefferanteil - Leerlauf (Dauer-LTRS) laesst Rahmen
    # durchfallen, ohne dass am Empfang etwas fehlt.
    chars = character_stats(bits_half, framing)

    level_count = len(peak_freqs)
    if level_count == 2:
        level_hint = "FSK2"
    elif level_count == 4:
        level_hint = "FSK4"
    else:
        level_hint = "unklar"
    hub_label = "Hub" if level_count == 2 else "Hub (aeusserer Pegelabstand)"

    result = {
        "wav_path": wav_path,
        "fs_hz": fs,
        "fs_eff_hz": fs_eff,
        "decimate": decimate,
        "min_baud": min_baud,
        "lag_limit_samples": lag_limit_samples,
        "freq_hz": freq_hz,
        "mode": mode,
        "contrast_db": contrast_db,
        "flatness": flatness,
        "bursts": bursts,
        "burst_durations_s": burst_durations_s,
        "chosen_burst_index": chosen_i,
        "chosen_burst_duration_s": burst_durations_s[chosen_i],
        "peak_freqs_hz": peak_freqs,
        "offset_hz": offset_hz,
        "dev_hz": dev_hz,
        "hub_label": hub_label,
        "baud": baud,
        "baud_coarse": baud_coarse,
        "baud_fine_r2": baud_fine_r2,
        "baud_refined": baud_refined,
        "baud_fine_reject_reason": baud_fine_reject_reason,
        "baud_fine_n_crossings": len(positions),
        "baud_override_used": baud_override_used,
        "T_fit": T_fit,
        "T_samples": T,
        "r2": r2,
        "symbolrate_case": symbolrate_case,
        "standard_baud": standard_baud,
        "phase_samples": phase,
        "eye_opening_hz": opening_hz,
        "eye_opening_pct": opening_pct,
        "jitter_pct": jitter_pct,
        "crossing_peaks": crossing["crossing_peaks"],
        "crossing_grid": crossing["crossing_grid"],
        "crossing_concentration": crossing["crossing_concentration"],
        "jitter_half_pct": jitter_half_pct,
        "framing_frame_halfbits": framing["frame_halfbits"],
        "framing_frame_bits": framing["frame_bits"],
        "framing_interpretations": framing["interpretations"],
        "framing_polarity": framing["polarity"],
        "framing_score": framing["score"],
        "framing_margin": framing["margin"],
        "framing_n_frames": framing["n_frames"],
        "framing_accepted": framing["accepted"],
        "framing_reject_reason": framing["reject_reason"],
        "chars_n": chars["n_chars"],
        "chars_fail_share": chars["fail_share"],
        "chars_fail_run_max": chars["fail_run_max"],
        "chars_fail_share_in_runs": chars["fail_share_in_runs"],
        "chars_code_shares": chars["code_shares"],
        "chars_top_codes": chars["top_codes"],
        "chars_halfbit_mismatch_share": chars["halfbit_mismatch_share"],
        "chars_ita2_hint": chars["ita2_hint"],
        "postdet_window_samples": postdet_window_samples,
        "level_count": level_count,
        "level_hint": level_hint,
        "traces": traces,
        "n_symbols_used": len(centers_all),
        "mod_index": tones["mod_index"],
        "tone_spacings_hz": tones["tone_spacings_hz"],
        "tone_spacing_mean_hz": tones["tone_spacing_mean_hz"],
        "tone_spacing_cv": tones["tone_spacing_cv"],
        "freq_bin_hz": freq_bin_hz,
    }
    result["mode_candidates"] = _mode_candidates_for(result)
    return result


# ---------------------------------------------------------------------------
# PSK-Pfad: Traegerversatz, Modulationsordnung, Symbolrate, Konstellation,
# EVM (PLAN.md T-904). Unabhaengig vom FSK-Pfad oben - dieser bleibt
# unveraendert und ist weiterhin Default (--mode fsk).
# ---------------------------------------------------------------------------

def detect_bursts_psk(z: np.ndarray, fs: int):
    """Burst-Erkennung im PSK-Modus: NUR Stufe A (Amplitudenkontrast), siehe
    PLAN.md T-904. Die FSK-Stufe B (Flatness der Momentanfrequenz) ist FSK-
    spezifisch: ein PSK-Signal hat an jeder Symbolgrenze einen Phasensprung
    und damit eine rauschaehnlich verteilte Momentanfrequenz - Stufe B wuerde
    es faelschlich als Rauschen verwerfen. Deshalb ABSICHTLICH kein Fallback
    auf Stufe B: liegt der Kontrast unter 6 dB, gilt die gesamte Aufnahme als
    EIN Dauersignal-Burst, es gibt keinen Abbruch wegen "Rauschen" wie im
    FSK-Pfad. Diese Auslassung ist kein Versehen - nicht "reparieren"."""
    p = np.abs(z) ** 2
    window = max(1, round(0.005 * fs))
    p_smooth = moving_average(p, window)
    p10 = float(np.percentile(p_smooth, 10))
    p95 = float(np.percentile(p_smooth, 95))
    contrast_db = 10.0 * math.log10(p95 / p10) if p10 > 0 else float("inf")
    min_len = max(1, round(0.1 * fs))

    if contrast_db >= 6.0:
        thr = math.sqrt(p10 * p95)
        bursts = contiguous_regions(p_smooth > thr, min_len)
        if not bursts:
            raise RuntimeError("no burst of sufficient length")
        return bursts, "getastet", contrast_db

    return [(0, len(z))], "dauersignal", contrast_db


def spectral_centroid_hz(z_burst: np.ndarray, fs: int) -> float:
    Z = np.fft.fft(z_burst)
    power = np.abs(Z) ** 2
    freqs = np.fft.fftfreq(len(z_burst), d=1.0 / fs)
    total = power.sum()
    if total <= 0:
        return 0.0
    return float(np.sum(freqs * power) / total)


def carrier_candidate(z_burst: np.ndarray, fs: int, m: int):
    """Liefert (f_m, sharpness) fuer die M-te-Potenz-Methode: w = (z/|z|)**M
    amplitudenbereinigt (AGC/Fading soll nicht stoeren), staerkster FFT-Bin
    von w ist f_m, Schaerfe = Betrag des staerksten Bins / Mittelwert aller
    Betraege. |z| == 0 wird auf 0 abgebildet (Beitrag verschwindet)."""
    mag = np.abs(z_burst)
    safe = mag > 0
    unit = np.zeros_like(z_burst)
    unit[safe] = z_burst[safe] / mag[safe]
    w = unit ** m
    W = np.fft.fft(w)
    mags = np.abs(W)
    idx = int(np.argmax(mags))
    mean_mag = float(np.mean(mags))
    sharpness = float(mags[idx] / mean_mag) if mean_mag > 0 else 0.0
    freqs = np.fft.fftfreq(len(w), d=1.0 / fs)
    f_m = float(freqs[idx])
    return f_m, sharpness


def detect_psk_carrier(z_burst: np.ndarray, fs: int):
    """Traegerversatz und Modulationsordnung ueber die M-te-Potenz-Methode
    (M in 2, 4, 8, siehe PLAN.md T-904 P1). Liefert (m_chosen, offset_hz,
    sharpness_by_m, offset_by_m, threshold, n). M-fache Mehrdeutigkeit je M
    wird ueber die Naehe zum Leistungsschwerpunkt von z_burst aufgeloest.
    Ordnungswahl: das kleinste M, dessen Schaerfe mindestens die Haelfte der
    groessten gemessenen Schaerfe erreicht (bei BPSK liefert auch M=4/M=8
    eine Linie, aber schwaecher - M=2 ist physikalisch richtig; die Schwelle
    unten entscheidet nur ueber Abbruch ja/nein, nicht ueber die Ordnung).
    Wirft RuntimeError, wenn keine der drei Schaerfen die logarithmisch
    skalierte Schwelle SCHAERFE_FAKTOR * ln(N) uebersteigt (N = Burstlaenge
    in Samples, siehe SCHAERFE_FAKTOR-Kommentar; Aufrufer -> Exit 2)."""
    n = len(z_burst)
    threshold = SCHAERFE_FAKTOR * math.log(n)
    centroid_hz = spectral_centroid_hz(z_burst, fs)
    sharpness_by_m = {}
    offset_by_m = {}
    for m in (2, 4, 8):
        f_m, sharpness = carrier_candidate(z_burst, fs, m)
        candidates = [f_m / m + k * fs / m for k in range(m)]
        best = min(candidates, key=lambda c: abs(c - centroid_hz))
        sharpness_by_m[m] = sharpness
        offset_by_m[m] = best

    max_sharpness = max(sharpness_by_m.values())
    if max_sharpness <= threshold:
        raise RuntimeError("no PSK structure detectable")

    m_chosen = None
    for m in (2, 4, 8):
        if sharpness_by_m[m] >= 0.5 * max_sharpness:
            m_chosen = m
            break

    return m_chosen, offset_by_m[m_chosen], sharpness_by_m, offset_by_m, threshold, n


def correct_carrier(z_burst: np.ndarray, fs: int, offset_hz: float) -> np.ndarray:
    n = np.arange(len(z_burst))
    return z_burst * np.exp(-1j * 2.0 * math.pi * offset_hz * n / fs)


def estimate_baud_from_envelope(z_korr: np.ndarray, fs: int, min_baud: float):
    """Symbolrate aus der Huellkurve (PLAN.md T-904 P3): wurzel-Nyquist-
    gefiltertes PSK hat Amplitudeneinbrueche an den Symbolgrenzen, daher eine
    spektrale Linie bei der Symbolrate. Bei streng konstanter Huellkurve
    (kein Roll-off, kein Pulsformungsfilter) entfaellt diese Linie - dann
    schlaegt die Schaetzung bewusst ueber der logarithmisch skalierten
    Schwelle SCHAERFE_FAKTOR * ln(N) fehl (N = Burstlaenge in Samples, siehe
    SCHAERFE_FAKTOR-Kommentar). Liefert (baud, sharpness, threshold, n).
    Wirft RuntimeError bei zu flacher Huellkurve."""
    a = np.abs(z_korr)
    a = a - np.mean(a)
    n = len(a)
    threshold = SCHAERFE_FAKTOR * math.log(n)
    A = np.fft.rfft(a)
    mags = np.abs(A)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    band = (freqs >= min_baud) & (freqs <= fs / 4.0)
    mean_mag = float(np.mean(mags))
    if not np.any(band):
        raise RuntimeError("symbol rate cannot be determined (envelope too flat)")
    band_mags = mags[band]
    band_freqs = freqs[band]
    idx = int(np.argmax(band_mags))
    baud = float(band_freqs[idx])
    sharpness = float(band_mags[idx] / mean_mag) if mean_mag > 0 else 0.0
    if sharpness <= threshold:
        raise RuntimeError("symbol rate cannot be determined (envelope too flat)")
    return baud, sharpness, threshold, n


def find_clock_phase_psk(z_korr: np.ndarray, T: float, n_phases: int = 32) -> float:
    n = len(z_korr)
    idx_all = np.arange(n)
    real_i = z_korr.real
    imag_i = z_korr.imag
    best_phase = 0.0
    best_score = -1.0
    for k in range(n_phases):
        phase = k * T / n_phases
        positions = np.arange(phase, n - 1, T)
        if len(positions) == 0:
            continue
        vr = np.interp(positions, idx_all, real_i)
        vi = np.interp(positions, idx_all, imag_i)
        score = float(np.mean(np.abs(vr + 1j * vi)))
        if score > best_score:
            best_score = score
            best_phase = float(phase)
    return best_phase


def build_constellation(z_korr: np.ndarray, T: float, phase: float):
    """Symbolwerte an den Abtaststellen, auf RMS-Betrag 1 normiert (PLAN.md
    T-904 P5). Liefert (symbols_norm, centers)."""
    n = len(z_korr)
    idx_all = np.arange(n)
    centers = np.arange(phase, n - 1, T)
    valid = (centers >= 0) & (centers <= n - 1)
    centers = centers[valid]
    real_i = np.interp(centers, idx_all, z_korr.real)
    imag_i = np.interp(centers, idx_all, z_korr.imag)
    symbols = real_i + 1j * imag_i
    rms = float(np.sqrt(np.mean(np.abs(symbols) ** 2))) if len(symbols) else 0.0
    symbols_norm = symbols / rms if rms > 0 else symbols
    return symbols_norm, centers


def phase_histogram_peaks(symbols: np.ndarray, bins: int = 72):
    """Phasenhistogramm ueber [-pi, pi) und Peakzahl nach denselben Regeln
    wie im FSK-Pfad (mindestens 20 % der Maximalhoehe, hier 2 statt 5 Bins
    Mindestabstand - 72 statt 201 Bins). Anders als das FSK-Frequenzhistogramm
    ist der Phasenwinkel ZYKLISCH: Bin 0 (nahe -pi) und der letzte Bin (nahe
    +pi) sind physikalisch benachbart, ein Cluster genau bei +-pi wuerde ohne
    circular=True in zwei Peaks gespalten (numerisch nachgewiesen an T14/T15,
    siehe --selftest). Diese Peakzahl ist eine zweite, unabhaengige Schaetzung
    der Modulationsordnung (PLAN.md T-904 P5)."""
    angles = np.angle(symbols)
    edges = np.linspace(-math.pi, math.pi, bins + 1)
    hist, _ = np.histogram(angles, bins=edges)
    smoothed = moving_average(hist.astype(np.float64), 3, circular=True)
    peak_idx = find_histogram_peaks(smoothed, min_rel_height=0.20, min_bin_distance=2, circular=True)
    return len(peak_idx), hist


def compute_evm(symbols: np.ndarray, m: int) -> float:
    """EVM in Prozent (PLAN.md T-904 P5). Die globale Phasendrehung wird ueber
    den Mittelwert von symbol**M geschaetzt (M-te-Potenz-Methode wie in P1,
    entfernt die M-fache Modulationsmehrdeutigkeit) statt per Gittersuche -
    Standardschaetzer fuer genau dieses Problem. Danach mittlerer
    quadratischer Abstand zu den naechstgelegenen der M idealen
    Konstellationspunkte (Einheitskreis, gleiche Skala wie die auf RMS=1
    normierten Symbole, daher EVM = 100 * sqrt(MSE) ohne weitere Division)."""
    if len(symbols) == 0:
        return 0.0
    mean_pow = np.mean(symbols ** m)
    phi0 = math.atan2(mean_pow.imag, mean_pow.real) / m if abs(mean_pow) > 0 else 0.0
    k = np.arange(m)
    ideal = np.exp(1j * (2.0 * math.pi * k / m + phi0))
    dist2 = np.abs(symbols[:, None] - ideal[None, :]) ** 2
    mse = float(np.mean(np.min(dist2, axis=1)))
    return 100.0 * math.sqrt(mse)


def compute_symbol_quality(symbols: np.ndarray, m: int) -> tuple[float, float]:
    """EVM fasst Betrags- und Phasenfehler in einem Skalar zusammen und kann
    beide nicht trennen. Auf dem Einheitskreis wiegt ein Phasenfehler
    schwerer als ein gleich grosser Betragsfehler (Abstand 2*sin(theta/2)
    gegen delta_r), weshalb eine Konstellation mit konstantem Radius, aber
    breiter Phasenstreuung schlechter bewertet wird als eine radial
    verschmierte mit enger Phase. Beobachtet 2026-09-01 an HFDL Bahrain:
    MGC-Aufnahme EVM 51,17 %, AGC-Aufnahme 46,84 % bei optisch
    aufgeraeumterer MGC-Konstellation.

    Liefert (mag_cv_pct, phase_rms_deg): mag_cv_pct ist die Standard-
    abweichung von |symbols| geteilt durch deren Mittelwert (in Prozent,
    ordnungsunabhaengig - misst reine Amplitudenschwankung, also den Effekt
    einer nachregelnden AGC). phase_rms_deg ist der Effektivwert des
    Phasenabstands jedes Symbols zur naechstgelegenen der M idealen
    Phasenlagen (dieselbe globale Phase phi0 wie in compute_evm, M-te-Potenz-
    Methode), auf -pi..+pi zurueckgefuehrt und in Grad umgerechnet."""
    if len(symbols) == 0:
        return 0.0, 0.0
    mag = np.abs(symbols)
    mag_mean = float(np.mean(mag))
    mag_cv_pct = 100.0 * float(np.std(mag)) / mag_mean if mag_mean > 0 else 0.0

    mean_pow = np.mean(symbols ** m)
    phi0 = math.atan2(mean_pow.imag, mean_pow.real) / m if abs(mean_pow) > 0 else 0.0
    k = np.arange(m)
    ideal_phase = 2.0 * math.pi * k / m + phi0
    dphi = np.angle(symbols)[:, None] - ideal_phase[None, :]
    dphi = (dphi + math.pi) % (2.0 * math.pi) - math.pi
    nearest_dphi = np.min(np.abs(dphi), axis=1)
    phase_rms_deg = math.degrees(math.sqrt(float(np.mean(nearest_dphi ** 2))))
    return mag_cv_pct, phase_rms_deg


def analyze_file_psk(wav_path: str, burst_index: int | None = None, baud_override: float | None = None,
                      decimate: int = 1, min_baud: float = 20.0,
                      t0_s: float | None = None, t1_s: float | None = None,
                      tune_hz: float = 0.0) -> dict:
    framerate_header, z = read_wav_iq(wav_path, t0_s, t1_s)
    z = tune_to_baseband(z, framerate_header, tune_hz)          # T-1620
    sidecar = read_sidecar(wav_path)
    fs = resolve_fs(framerate_header, sidecar)
    freq_hz = sidecar.get("freq_hz") if sidecar else None

    z_eff, fs_eff = apply_decimation(z, fs, decimate, min_baud)

    bursts, mode, contrast_db = detect_bursts_psk(z_eff, fs_eff)
    burst_durations_s = [(e - s) / fs_eff for s, e in bursts]

    if burst_index is None:
        chosen_i = max(range(len(bursts)), key=lambda i: bursts[i][1] - bursts[i][0])
    else:
        if not (0 <= burst_index < len(bursts)):
            raise IndexError(f"Burst-Index {burst_index} ausserhalb des gueltigen Bereichs (0..{len(bursts) - 1})")
        chosen_i = burst_index

    start, end = bursts[chosen_i]
    z_burst = z_eff[start:end]

    m_chosen, offset_hz, sharpness_by_m, offset_by_m, carrier_threshold, carrier_n = detect_psk_carrier(
        z_burst, fs_eff)
    z_korr = correct_carrier(z_burst, fs_eff, offset_hz)

    baud_override_used = baud_override is not None
    if baud_override_used:
        baud = float(baud_override)
        envelope_sharpness = None
        envelope_threshold = None
        envelope_n = None
    else:
        baud, envelope_sharpness, envelope_threshold, envelope_n = estimate_baud_from_envelope(
            z_korr, fs_eff, min_baud)

    if fs_eff / baud < 4:
        raise RuntimeError("too few samples per symbol")
    T = fs_eff / baud

    phase = find_clock_phase_psk(z_korr, T)
    symbols, centers = build_constellation(z_korr, T, phase)

    phase_peak_count, phase_hist = phase_histogram_peaks(symbols)
    order_mismatch = phase_peak_count != m_chosen

    evm_pct = compute_evm(symbols, m_chosen)
    mag_cv_pct, phase_rms_deg = compute_symbol_quality(symbols, m_chosen)

    freq_abs_hz = freq_hz + offset_hz if freq_hz is not None else None

    return {
        "wav_path": wav_path,
        "fs_hz": fs,
        "fs_eff_hz": fs_eff,
        "decimate": decimate,
        "min_baud": min_baud,
        "freq_hz": freq_hz,
        "freq_abs_hz": freq_abs_hz,
        "mode": mode,
        "contrast_db": contrast_db,
        "bursts": bursts,
        "burst_durations_s": burst_durations_s,
        "chosen_burst_index": chosen_i,
        "chosen_burst_duration_s": burst_durations_s[chosen_i],
        "sharpness_by_m": sharpness_by_m,
        "offset_by_m": offset_by_m,
        "carrier_threshold": carrier_threshold,
        "carrier_n": carrier_n,
        "m_chosen": m_chosen,
        "offset_hz": offset_hz,
        "baud": baud,
        "baud_override_used": baud_override_used,
        "envelope_sharpness": envelope_sharpness,
        "envelope_threshold": envelope_threshold,
        "envelope_n": envelope_n,
        "T_samples": T,
        "phase_samples": phase,
        "n_symbols_used": len(symbols),
        "phase_peak_count": phase_peak_count,
        "order_mismatch": order_mismatch,
        "evm_pct": evm_pct,
        "mag_cv_pct": mag_cv_pct,
        "phase_rms_deg": phase_rms_deg,
        "symbols": symbols,
        "phase_hist": phase_hist,
    }


# ---------------------------------------------------------------------------
# Ausgabe (Textbericht, SVG, JSON)
# ---------------------------------------------------------------------------

def _format_kandidat_zeile(befund: dict) -> str:
    """Eine Zeile der Kandidatenanzeige (T-1703). Nur die quantitativen
    Kriterien (shift_hz/tone_spacing_hz/baud/spacing_cv_max) werden gezeigt -
    level_count und keying sind exakte Ja/Nein-Kriterien ohne Abweichung, die
    sich sinnvoll anzeigen liesse; steht die Zeile ueberhaupt im Ergebnis,
    haben sie bereits gestimmt."""
    teile = []
    for crit in befund["criteria"]:
        name = crit["name"]
        if name in ("shift_hz", "tone_spacing_hz"):
            label = "Hub" if name == "shift_hz" else "Tonabstand"
            teile.append(f"{label} {crit['measured']:.1f} Hz "
                        f"(Soll {crit['expected']:.1f} +-{crit['tolerance']:.1f})")
        elif name == "baud":
            teile.append(f"Symbolrate {crit['measured']:.2f} Bd "
                        f"(Soll {crit['expected']:.2f} +-{crit['tolerance_rel'] * 100.0:.1f} %)")
        elif name == "spacing_cv_max":
            teile.append(f"Tonabstands-Streuung {crit['measured'] * 100.0:.1f} % "
                        f"(Grenze {crit['expected'] * 100.0:.1f} %)")
    zusatz = " (knapp)" if befund["confidence"] == "near" else ""
    return f"  {befund['label']}{zusatz}  " + ", ".join(teile)


def print_report(result: dict) -> None:
    print(f"Datei: {result['wav_path']}")
    print(f"Abtastrate: {result['fs_hz']} Hz")
    if result["decimate"] > 1:
        print(f"Dezimation: N={result['decimate']}, effektive Abtastrate: {result['fs_eff_hz']:.2f} Hz")
    if result["freq_hz"] is not None:
        print(f"Frequenz (Sidecar): {result['freq_hz'] / 1e6:.4f} MHz")
    print(f"Amplitudenkontrast: {result['contrast_db']:.2f} dB, Flatness Momentanfrequenz: {result['flatness']:.4f}")
    if result["mode"] == "dauersignal":
        print("Dauersignal, keine Tastung erkannt")
    print(f"Gefundene Bursts: {len(result['bursts'])}")
    for i, dur in enumerate(result["burst_durations_s"]):
        marker = " <-- ausgewertet" if i == result["chosen_burst_index"] else ""
        print(f"  Burst {i}: Dauer {dur:.3f} s{marker}")
    peaks_str = ", ".join(f"{p:+.1f}" for p in result["peak_freqs_hz"])
    print(f"Pegel-Peaks (Hz): {peaks_str} ({len(result['peak_freqs_hz'])} erkannt)")
    print(f"Frequenzoffset: {result['offset_hz']:+.1f} Hz")
    print(f"{result['hub_label']}: {result['dev_hz']:.1f} Hz")
    if result["baud_override_used"]:
        quelle = "vorgegeben"
    elif result["baud_refined"]:
        quelle = "geschaetzt, feinabgeglichen"
    else:
        quelle = "geschaetzt"
    print(f"Symbolrate ({quelle}): {result['baud']:.2f} Baud, "
          f"{result['fs_eff_hz'] / result['baud']:.2f} Samples/Symbol")
    if not result["baud_override_used"]:
        abweichung_pct = 100.0 * abs(result["baud_coarse"] - result["baud"]) / result["baud"]
        print(f"  grob aus Flankenfit: {result['baud_coarse']:.2f} Baud (Abweichung {abweichung_pct:.2f} %)")
        print(f"  T (Flankenfit): {result['T_fit']:.4f} Samples, T (final): {result['T_samples']:.4f} Samples")
        print(f"  Bestimmtheitsmass Flankenfit: r2={result['r2']:.6f}")
        print(f"  Lag-Bereich Autokorrelation: 1..{result['lag_limit_samples']} Samples "
              f"(min-baud={result['min_baud']:.2f} Bd)")
        if result["r2"] < 0.9:
            print("  Hinweis: Flankenanpassung unsicher")
        if result["symbolrate_case"] == "wechselfolge":
            print("Symbolrate aus Wechselfolge geschaetzt, Unsicherheit unter 1 %.")
        else:
            print("Symbolrate aus Zufallsfolge geschaetzt, typische Unsicherheit ca. 3 %.")
        # T-1711: Feinabgleich auf den Nulldurchgangslagen (siehe
        # refine_symbol_rate()) - kein stiller Ruckfall, eine Ablehnung wird
        # immer ausgewiesen statt kommentarlos die grobe Schaetzung zu nutzen.
        if result["baud_refined"]:
            print(f"  Feinabgleich: R2={result['baud_fine_r2']:.2f} ueber "
                  f"{result['baud_fine_n_crossings']} Nulldurchgaengen")
        elif result["baud_fine_r2"] is not None:
            print(f"  Feinabgleich nicht belastbar (R2={result['baud_fine_r2']:.2f}), "
                  f"grobe Schaetzung beibehalten.")
        else:
            print("  Feinabgleich nicht belastbar (zu wenige Nulldurchgaenge), "
                  "grobe Schaetzung beibehalten.")
    if result["standard_baud"] is not None:
        print(f"  naechstes Standardraster: {result['standard_baud']} Baud")
    if result["mod_index"] is not None:
        print(f"Modulationsindex: {result['mod_index']:.2f} (2*Hub / Symbolrate)")
    spacings_str = ", ".join(f"{s:.1f}" for s in result["tone_spacings_hz"])
    print(f"Tonabstaende (Hz): {spacings_str} (Mittel {result['tone_spacing_mean_hz']:.1f}, "
          f"Streuung {100.0 * result['tone_spacing_cv']:.1f} %)")
    print(f"Frequenzaufloesung (Histogramm-Bin): {result['freq_bin_hz']:.2f} Hz")
    w = result["postdet_window_samples"]
    if w > 1:
        rel_anzeige = w / result["T_samples"] if result["T_samples"] else 0.0
        print(f"Nachdetektionsfilter: {w} Samples ({rel_anzeige:.2g} * T)")
    else:
        print("Nachdetektionsfilter: keiner (zu wenige Samples je Symbol)")
    print(f"Augenoeffnung: {result['eye_opening_hz']:.1f} Hz ({result['eye_opening_pct']:.1f} % von 2*Hub)")
    print(f"Jitter: {result['jitter_pct']:.2f} % von T")
    peaks = result["crossing_peaks"]
    n_peaks = len(peaks)
    if n_peaks:
        wort = "Haeufung" if n_peaks == 1 else "Haeufungen"
        orte = " und ".join(f"{p:.2f}" for p in peaks) if n_peaks <= 2 else ", ".join(f"{p:.2f}" for p in peaks)
        print(f"Nulldurchgaenge: {n_peaks} {wort} bei {orte} T "
              f"(Konzentration {result['crossing_concentration']:.2f})")
    else:
        print(f"Nulldurchgaenge: keine Haeufung erkennbar "
              f"(Konzentration {result['crossing_concentration']:.2f})")
    if result["crossing_grid"] == "half":
        print("  Halbbit-Raster - moeglich z. B. bei 1,5 Stoppbits (ITA2/RTTY)")
        print(f"  Jitter gegen Halbbit-Raster: {result['jitter_half_pct']:.1f} % von T")
    if result["framing_accepted"]:
        print(f"Rahmenstruktur: {result['framing_frame_bits']:.1f} Bit je Zeichen "
              f"({result['framing_frame_halfbits']} Halbbit), Polaritaet "
              f"{result['framing_polarity']}")
        for deutung in result["framing_interpretations"]:
            print(f"  Kennwerte passen zu: {deutung}")
        print(f"  Start/Stopp-Treffer {result['framing_score'] * 100:.1f} % ueber "
              f"{result['framing_n_frames']} Rahmen, Abstand zur naechsten "
              f"Rahmenlaenge {result['framing_margin'] * 100:.1f} Punkte")
    else:
        print(f"Rahmenstruktur: asynchron nicht nachweisbar "
              f"({result['framing_reject_reason']})")
    if result["chars_n"] > 0:
        shares = result["chars_code_shares"]
        anzeige_reihenfolge = (("00100", "Space"), ("11111", "LTRS"), ("11011", "FIGS"),
                               ("00010", "CR"), ("01000", "LF"), ("00000", "NUL"))
        zeile = ", ".join(f"{label} {shares[code] * 100:.1f} %" for code, label in anzeige_reihenfolge)
        print(f"Zeichenvorrat ({result['chars_n']} von {result['framing_n_frames']} Rahmen gueltig):")
        print(f"  {zeile}")
        if result["chars_ita2_hint"]:
            print("  Haeufigkeiten passen zu ITA2")
        top_zeile = ", ".join(f"{c['code']} {c['share'] * 100:.1f} %" for c in result["chars_top_codes"])
        print(f"  Haeufigste Codes: {top_zeile}")
        print(f"  Uneinige Halbbits: {result['chars_halfbit_mismatch_share'] * 100:.1f} % der Datenbits")
        print(f"Rahmenausfaelle: {result['chars_fail_share'] * 100:.1f} %, laengster Lauf "
              f"{result['chars_fail_run_max']} Rahmen, {result['chars_fail_share_in_runs'] * 100:.1f} % "
              f"der Ausfaelle in Laeufen >= 3")
    print(f"Pegelanzahl: {result['level_count']} ({result['level_hint']})")
    print(f"Symbole ausgewertet: {result['n_symbols_used']} (im Augendiagramm dargestellt: {len(result['traces'])})")
    if result["mode_candidates"]:
        print("Kennwerte passen zu:")
        for befund in result["mode_candidates"]:
            print(_format_kandidat_zeile(befund))
    else:
        print("Keine Uebereinstimmung in der Modentabelle.")


def write_svg(path: str, traces: np.ndarray, dev_hz: float) -> None:
    width, height = 760, 480
    margin_left, margin_right, margin_top, margin_bottom = 70, 20, 20, 50
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    y_extent = dev_hz * 1.5 if dev_hz > 0 else 1.0
    if len(traces):
        y_extent = max(y_extent, float(np.max(np.abs(traces))) * 1.1)
    if y_extent <= 0:
        y_extent = 1.0

    def x_to_px(x_sym: float) -> float:
        return margin_left + (x_sym + 1.0) / 2.0 * plot_w

    def y_to_px(y_hz: float) -> float:
        return margin_top + (1.0 - (y_hz + y_extent) / (2.0 * y_extent)) * plot_h

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect x="{margin_left}" y="{margin_top}" width="{plot_w}" height="{plot_h}" '
        f'fill="none" stroke="#333333" stroke-width="1"/>',
    ]

    y0 = y_to_px(0.0)
    lines.append(f'<line x1="{margin_left}" y1="{y0:.1f}" x2="{margin_left + plot_w}" y2="{y0:.1f}" '
                 f'stroke="#999999" stroke-width="1"/>')
    for rail in (dev_hz, -dev_hz):
        yr = y_to_px(rail)
        lines.append(f'<line x1="{margin_left}" y1="{yr:.1f}" x2="{margin_left + plot_w}" y2="{yr:.1f}" '
                     f'stroke="#999999" stroke-width="1" stroke-dasharray="4,3"/>')

    n_points = traces.shape[1] if len(traces) else N_EYE_POINTS
    offsets_sym = np.linspace(-1.0, 1.0, n_points)
    for trace in traces:
        pts = " ".join(f"{x_to_px(x):.1f},{y_to_px(y):.1f}" for x, y in zip(offsets_sym, trace))
        lines.append(f'<polyline points="{pts}" stroke="#c8820f" stroke-opacity="0.12" '
                     f'stroke-width="1" fill="none"/>')

    for x_sym, label in ((-1.0, "-1"), (0.0, "0"), (1.0, "+1")):
        px = x_to_px(x_sym)
        lines.append(f'<text x="{px:.1f}" y="{margin_top + plot_h + 20}" font-size="12" '
                     f'fill="#333333" text-anchor="middle">{label}</text>')
    lines.append(f'<text x="{margin_left + plot_w / 2:.1f}" y="{height - 10}" font-size="12" '
                 f'fill="#333333" text-anchor="middle">Symboldauern</text>')

    for y_hz, label in ((y_extent, f"{y_extent / 1000.0:.1f}"), (0.0, "0"), (-y_extent, f"{-y_extent / 1000.0:.1f}")):
        py = y_to_px(y_hz)
        lines.append(f'<text x="{margin_left - 8}" y="{py + 4:.1f}" font-size="12" '
                     f'fill="#333333" text-anchor="end">{label}</text>')
    lines.append(f'<text x="14" y="{margin_top + plot_h / 2:.1f}" font-size="12" fill="#333333" '
                 f'text-anchor="middle" transform="rotate(-90 14 {margin_top + plot_h / 2:.1f})">kHz</text>')

    lines.append("</svg>")
    content = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def print_report_psk(result: dict) -> None:
    print(f"Datei: {result['wav_path']}")
    print(f"Abtastrate: {result['fs_hz']} Hz")
    if result["decimate"] > 1:
        print(f"Dezimation: N={result['decimate']}, effektive Abtastrate: {result['fs_eff_hz']:.2f} Hz")
    if result["freq_hz"] is not None:
        print(f"Frequenz (Sidecar): {result['freq_hz'] / 1e6:.4f} MHz")
    print(f"Amplitudenkontrast: {result['contrast_db']:.2f} dB")
    if result["mode"] == "dauersignal":
        print("Dauersignal, keine Tastung erkannt")
    print(f"Gefundene Bursts: {len(result['bursts'])}")
    for i, dur in enumerate(result["burst_durations_s"]):
        marker = " <-- ausgewertet" if i == result["chosen_burst_index"] else ""
        print(f"  Burst {i}: Dauer {dur:.3f} s{marker}")

    s_by_m = result["sharpness_by_m"]
    print(f"Schaerfe M-te Potenz: s_2={s_by_m[2]:.2f} s_4={s_by_m[4]:.2f} s_8={s_by_m[8]:.2f} "
          f"(Schwelle {result['carrier_threshold']:.2f}, N={result['carrier_n']})")
    print(f"Gewaehlte Modulationsordnung: M={result['m_chosen']}")
    print(f"Traegerversatz: {result['offset_hz']:+.1f} Hz")
    if result["freq_abs_hz"] is not None:
        print(f"Absolutfrequenz Traeger: {result['freq_abs_hz'] / 1e3:.3f} kHz")

    quelle = "vorgegeben" if result["baud_override_used"] else "geschaetzt"
    print(f"Symbolrate ({quelle}): {result['baud']:.2f} Baud, "
          f"{result['fs_eff_hz'] / result['baud']:.2f} Samples/Symbol")
    if not result["baud_override_used"]:
        print(f"  Huellkurven-Schaerfe: {result['envelope_sharpness']:.2f} "
              f"(Schwelle {result['envelope_threshold']:.2f}, N={result['envelope_n']})")

    print(f"Phasenhistogramm-Peaks (unabhaengige Ordnungsschaetzung): {result['phase_peak_count']}")
    if result["order_mismatch"]:
        print(f"  WARNUNG: Phasenhistogramm-Peaks ({result['phase_peak_count']}) weicht von M "
              f"({result['m_chosen']}) ab")
    print(f"EVM: {result['evm_pct']:.2f} %")
    print(f"Betragsstreuung: {result['mag_cv_pct']:.2f} %")
    print(f"Phasenfehler (RMS): {result['phase_rms_deg']:.2f} Grad")
    print(f"Symbole ausgewertet: {result['n_symbols_used']}")


def write_svg_psk(path: str, symbols: np.ndarray, phase_hist: np.ndarray,
                   max_points: int = MAX_CONST_POINTS) -> None:
    """Konstellation und Phasenhistogramm als SVG. Es werden hoechstens
    max_points Symbole gezeichnet (gleichmaessig ausgewaehlt, siehe
    MAX_CONST_POINTS) - das Bild ist davon nicht zu unterscheiden, die
    Dateigroesse um Groessenordnungen kleiner."""
    width = 500
    plot_size = 440
    margin = 30
    hist_height = 120
    hist_gap = 40
    total_height = margin + plot_size + hist_gap + hist_height + margin
    extent = 1.5

    def x_to_px(x: float) -> float:
        return margin + (x + extent) / (2.0 * extent) * plot_size

    def y_to_px(y: float) -> float:
        return margin + (1.0 - (y + extent) / (2.0 * extent)) * plot_size

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_height}" '
        f'viewBox="0 0 {width} {total_height}">',
        f'<rect x="0" y="0" width="{width}" height="{total_height}" fill="#ffffff"/>',
        f'<rect x="{margin}" y="{margin}" width="{plot_size}" height="{plot_size}" '
        f'fill="none" stroke="#333333" stroke-width="1"/>',
    ]

    x0 = x_to_px(0.0)
    y0 = y_to_px(0.0)
    lines.append(f'<line x1="{margin}" y1="{y0:.1f}" x2="{margin + plot_size}" y2="{y0:.1f}" '
                 f'stroke="#999999" stroke-width="1"/>')
    lines.append(f'<line x1="{x0:.1f}" y1="{margin}" x2="{x0:.1f}" y2="{margin + plot_size}" '
                 f'stroke="#999999" stroke-width="1"/>')

    r_px = plot_size / (2.0 * extent)
    lines.append(f'<circle cx="{x0:.1f}" cy="{y0:.1f}" r="{r_px:.1f}" fill="none" '
                 f'stroke="#999999" stroke-width="1" stroke-dasharray="4,3"/>')

    gezeichnet = symbols
    if max_points and len(symbols) > max_points:
        pick = np.unique(np.round(np.linspace(0, len(symbols) - 1, max_points)).astype(int))
        gezeichnet = symbols[pick]
    for sym in gezeichnet:
        px = x_to_px(float(sym.real))
        py = y_to_px(float(sym.imag))
        lines.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.0" fill="#c8820f" fill-opacity="0.25"/>')

    _hinweis = ("" if len(gezeichnet) == len(symbols)
                else f" - {len(gezeichnet)} von {len(symbols)} gezeigt")
    lines.append(f'<text x="{margin + plot_size / 2:.1f}" y="{margin + plot_size + 16}" font-size="12" '
                 f'fill="#333333" text-anchor="middle">Konstellation (I/Q, normiert)'
                 f'{_hinweis}</text>')

    hist_top = margin + plot_size + hist_gap
    hist_max = float(phase_hist.max()) if len(phase_hist) and phase_hist.max() > 0 else 1.0
    nbins = len(phase_hist)
    bar_w = plot_size / nbins if nbins else plot_size
    lines.append(f'<rect x="{margin}" y="{hist_top}" width="{plot_size}" height="{hist_height}" '
                 f'fill="none" stroke="#333333" stroke-width="1"/>')
    for i, count in enumerate(phase_hist):
        h = (count / hist_max) * hist_height
        bx = margin + i * bar_w
        by = hist_top + hist_height - h
        lines.append(f'<rect x="{bx:.2f}" y="{by:.2f}" width="{max(bar_w - 0.3, 0.2):.2f}" height="{h:.2f}" '
                     f'fill="#3a6ea5" fill-opacity="0.7"/>')
    lines.append(f'<text x="{margin + plot_size / 2:.1f}" y="{hist_top + hist_height + 16}" font-size="12" '
                 f'fill="#333333" text-anchor="middle">Phasenhistogramm (72 Bins, -pi..+pi)</text>')

    lines.append("</svg>")
    content = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def build_json_dict(result: dict) -> dict:
    fs = result["fs_hz"]
    fs_eff = result["fs_eff_hz"]
    return {
        "wav_path": result["wav_path"],
        "fs_hz": fs,
        "fs_eff_hz": fs_eff,
        "decimate": result["decimate"],
        "min_baud": result["min_baud"],
        "lag_limit_samples": result["lag_limit_samples"],
        "freq_hz": result["freq_hz"],
        "mode": result["mode"],
        "contrast_db": result["contrast_db"],
        "flatness": result["flatness"],
        "bursts": [
            {"start_s": s / fs_eff, "end_s": e / fs_eff, "duration_s": (e - s) / fs_eff}
            for s, e in result["bursts"]
        ],
        "chosen_burst_index": result["chosen_burst_index"],
        "peak_freqs_hz": result["peak_freqs_hz"],
        "offset_hz": result["offset_hz"],
        "dev_hz": result["dev_hz"],
        "hub_label": result["hub_label"],
        "baud": result["baud"],
        "baud_coarse": result["baud_coarse"],
        "baud_fine_r2": result["baud_fine_r2"],
        "baud_refined": result["baud_refined"],
        "baud_override_used": result["baud_override_used"],
        "T_fit_samples": result["T_fit"],
        "T_samples": result["T_samples"],
        "r2": result["r2"],
        "symbolrate_case": result["symbolrate_case"],
        "standard_baud": result["standard_baud"],
        "samples_per_symbol": fs_eff / result["baud"],
        "eye_opening_hz": result["eye_opening_hz"],
        "eye_opening_pct": result["eye_opening_pct"],
        "jitter_pct": result["jitter_pct"],
        "crossing_peaks": result["crossing_peaks"],
        "crossing_grid": result["crossing_grid"],
        "crossing_concentration": result["crossing_concentration"],
        "jitter_half_pct": result["jitter_half_pct"],
        "framing_frame_halfbits": result["framing_frame_halfbits"],
        "framing_frame_bits": result["framing_frame_bits"],
        "framing_interpretations": result["framing_interpretations"],
        "framing_polarity": result["framing_polarity"],
        "framing_score": result["framing_score"],
        "framing_margin": result["framing_margin"],
        "framing_n_frames": result["framing_n_frames"],
        "framing_accepted": result["framing_accepted"],
        "framing_reject_reason": result["framing_reject_reason"],
        "chars_n": result["chars_n"],
        "chars_fail_share": result["chars_fail_share"],
        "chars_fail_run_max": result["chars_fail_run_max"],
        "chars_fail_share_in_runs": result["chars_fail_share_in_runs"],
        "chars_code_shares": result["chars_code_shares"],
        "chars_top_codes": result["chars_top_codes"],
        "chars_halfbit_mismatch_share": result["chars_halfbit_mismatch_share"],
        "chars_ita2_hint": result["chars_ita2_hint"],
        "postdet_window_samples": result["postdet_window_samples"],
        "level_count": result["level_count"],
        "level_hint": result["level_hint"],
        "n_symbols_used": result["n_symbols_used"],
        "mod_index": result["mod_index"],
        "tone_spacings_hz": result["tone_spacings_hz"],
        "tone_spacing_mean_hz": result["tone_spacing_mean_hz"],
        "tone_spacing_cv": result["tone_spacing_cv"],
        "freq_bin_hz": result["freq_bin_hz"],
        "mode_candidates": result["mode_candidates"],
    }


def write_json(path: str, result: dict) -> None:
    data = build_json_dict(result)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)
        f.write("\n")


def build_json_dict_psk(result: dict) -> dict:
    freq_abs_hz = result["freq_abs_hz"]
    return {
        "wav_path": result["wav_path"],
        "fs_hz": result["fs_hz"],
        "fs_eff_hz": result["fs_eff_hz"],
        "decimate": result["decimate"],
        "freq_hz": result["freq_hz"],
        "contrast_db": result["contrast_db"],
        "burst_count": len(result["bursts"]),
        "chosen_burst_index": result["chosen_burst_index"],
        "chosen_burst_duration_s": result["chosen_burst_duration_s"],
        "sharpness_by_m": {str(k): v for k, v in result["sharpness_by_m"].items()},
        "carrier_threshold": result["carrier_threshold"],
        "carrier_n": result["carrier_n"],
        "m_chosen": result["m_chosen"],
        "offset_hz": result["offset_hz"],
        "carrier_abs_khz": freq_abs_hz / 1e3 if freq_abs_hz is not None else None,
        "baud": result["baud"],
        "baud_override_used": result["baud_override_used"],
        "envelope_sharpness": result["envelope_sharpness"],
        "envelope_threshold": result["envelope_threshold"],
        "envelope_n": result["envelope_n"],
        "T_samples": result["T_samples"],
        "phase_samples": result["phase_samples"],
        "n_symbols_used": result["n_symbols_used"],
        "phase_peak_count": result["phase_peak_count"],
        "order_mismatch": result["order_mismatch"],
        "evm_pct": result["evm_pct"],
        "mag_cv_pct": result["mag_cv_pct"],
        "phase_rms_deg": result["phase_rms_deg"],
    }


def _json_native(value):
    """Wandelt numpy-Skalare/Arrays und verschachtelte Strukturen in reine
    Python-Typen fuer json.dump; Dict-Schluessel werden zu Strings (JSON
    kennt keine Zahlenschluessel)."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return _json_native(value.tolist())
    if isinstance(value, dict):
        return {str(k): _json_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_native(v) for v in value]
    return value


def write_json_psk(path: str, result: dict) -> None:
    data = _json_native(build_json_dict_psk(result))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# --suggest: Parametervorschlag ohne Auswertung (PLAN.md T-903)
# ---------------------------------------------------------------------------

def run_suggest(wav_path: str, t0_s: float | None = None,
                t1_s: float | None = None) -> int:
    """Gibt Abtastrate, Burstuebersicht und ein grobes Frequenzhistogramm bei
    N=1 aus, sowie einen abgeleiteten Vorschlag fuer --decimate. Fuehrt KEINE
    Symbolraten-/Augendiagramm-Auswertung durch - reines Hilfsmittel, der
    Vorschlag wird nie selbsttaetig angewandt.

    t0_s/t1_s (T-1608a): nur dieses Zeitfenster lesen. Wie bei analyze_file()
    sieht der Rest der Uebersicht das Fenster als ganze Aufnahme - die
    Burstnummerierung zaehlt also im Ausschnitt, nicht in der Datei. Ohne
    Fenster bleibt das Verhalten unveraendert (ganze Datei), damit
    --suggest ohne --t0/--t1 weiterhin den Gesamtueberblick gibt."""
    framerate_header, z = read_wav_iq(wav_path, t0_s, t1_s)
    sidecar = read_sidecar(wav_path)
    fs = resolve_fs(framerate_header, sidecar)
    freq_hz = sidecar.get("freq_hz") if sidecar else None

    print(f"Datei: {wav_path}")
    print(f"Abtastrate: {fs} Hz")
    if freq_hz is not None:
        print(f"Frequenz (Sidecar): {freq_hz / 1e6:.4f} MHz")

    bursts, mode, contrast_db, flatness, f_full = detect_bursts(z, fs)
    burst_durations_s = [(e - s) / fs for s, e in bursts]
    print(f"Amplitudenkontrast: {contrast_db:.2f} dB, Flatness Momentanfrequenz: {flatness:.4f}")
    if mode == "dauersignal":
        print("Dauersignal, keine Tastung erkannt")
    print(f"Gefundene Bursts: {len(bursts)}")
    chosen_i = max(range(len(bursts)), key=lambda i: bursts[i][1] - bursts[i][0])
    for i, dur in enumerate(burst_durations_s):
        marker = " <-- laengster" if i == chosen_i else ""
        print(f"  Burst {i}: Dauer {dur:.3f} s{marker}")

    start, end = bursts[chosen_i]
    f_burst = f_full[start:end - 1]

    edges = np.linspace(-fs / 4.0, fs / 4.0, N_ZERO_HIST_BINS + 1)
    hist, _ = np.histogram(f_burst, bins=edges)
    centers = (edges[:-1] + edges[1:]) / 2.0

    order = np.argsort(hist)[::-1]
    top_idx = order[hist[order] > 0][:10]
    max_count = int(hist.max()) if len(hist) else 0
    print("Frequenzhistogramm (10 staerkste Bins, N=1):")
    for i in top_idx:
        rel = hist[i] / max_count if max_count > 0 else 0.0
        print(f"  {centers[i]:+9.1f} Hz : {int(hist[i]):6d} ({rel * 100:5.1f} %)")

    smoothed = moving_average(hist.astype(np.float64), 3)
    peak_idx = find_histogram_peaks(smoothed)
    if len(peak_idx) == 0:
        print("Kein signifikanter Peak gefunden, kein --decimate-Vorschlag moeglich")
        return 0
    peak_freqs = [float(centers[i]) for i in peak_idx]
    outer_peak = max(abs(p) for p in peak_freqs)
    print(f"Signifikante Peaks (Hz): {', '.join(f'{p:+.1f}' for p in peak_freqs)}")

    if outer_peak <= 0:
        print("Vorschlag --decimate: kein sinnvoller Wert ableitbar (Peak bei 0 Hz)")
        return 0

    n_suggest = max(1, int(math.floor(0.1 * fs / outer_peak)))
    fs_eff_suggest = fs / n_suggest
    print(f"Vorschlag --decimate {n_suggest} (fs_eff={fs_eff_suggest:.1f} Hz, "
          f"aeusserster Peak {outer_peak:.1f} Hz innerhalb 0.4*fs_eff/4={0.4 * fs_eff_suggest / 4:.1f} Hz)")
    print("Hinweis: Vorschlag ist ein Hilfsmittel, keine Automatik - wird nie selbsttaetig angewandt.")
    return 0


# ---------------------------------------------------------------------------
# Selbsttest - "python tools/eye_diagram.py --selftest", ohne Hardware/Server
# ---------------------------------------------------------------------------

def _make_fsk_signal(fs: int, baud: float, levels_hz, dur_s: float, offset_hz: float, rng, snr_db: float = 20.0,
                      pattern: str | None = None, level_probs=None):
    n = int(fs * dur_s)
    sps = fs / baud
    n_symbols = int(math.ceil(n / sps)) + 2
    levels = np.array(levels_hz, dtype=np.float64)
    if pattern == "alternating":
        sym = np.arange(n_symbols) % len(levels)
    elif level_probs is not None:
        sym = rng.choice(len(levels), size=n_symbols, p=level_probs)
    else:
        sym = rng.integers(0, len(levels), n_symbols)
    freq_seq = levels[sym]
    idx = np.clip((np.arange(n) / sps).astype(int), 0, n_symbols - 1)
    inst_freq = freq_seq[idx] + offset_hz
    phase = np.cumsum(2.0 * math.pi * inst_freq / fs)
    sig = np.exp(1j * phase)
    snr_lin = 10.0 ** (snr_db / 10.0)
    noise_power = 1.0 / snr_lin
    noise = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) * math.sqrt(noise_power / 2.0)
    return sig + noise


def _make_fsk_signal_from_symbols(fs: int, sym_rate: float, levels_hz, symbols, offset_hz: float, rng,
                                   snr_db: float = 20.0) -> np.ndarray:
    """Wie _make_fsk_signal, aber mit vorgegebener statt zufaellig erzeugter
    Symbolfolge (T-1710, Fixtur fuer asynchron gerahmte Bitstroeme aus
    _make_async_frames). sym_rate ist die Rate der UEBERGEBENEN Folge - bei
    Halbbit-Werten aus _make_async_frames also 2*baud, nicht baud."""
    sps = fs / sym_rate
    n_symbols = len(symbols)
    n = int(round(n_symbols * sps))
    levels = np.array(levels_hz, dtype=np.float64)
    sym = np.asarray(symbols, dtype=int)
    freq_seq = levels[sym]
    idx = np.clip((np.arange(n) / sps).astype(int), 0, n_symbols - 1)
    inst_freq = freq_seq[idx] + offset_hz
    phase = np.cumsum(2.0 * math.pi * inst_freq / fs)
    sig = np.exp(1j * phase)
    snr_lin = 10.0 ** (snr_db / 10.0)
    noise_power = 1.0 / snr_lin
    noise = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) * math.sqrt(noise_power / 2.0)
    return sig + noise


def _make_async_frames(n_frames: int, data_bits: int = 5, stop_bits: float = 1.0, rng=None) -> list[float]:
    """Symbolfolge eines asynchron gerahmten Bitstroms auf HALBBIT-Raster
    (T-1710): je Rahmen ein Startbit (0.0, Space), data_bits Zufallsbits, dann
    stop_bits Stoppbits (1.0, Mark) - jedes Bit als zwei gleiche
    Halbbit-Eintraege. Bei stop_bits=1.5 ist ein Rahmen 15 Halbbit lang; das
    ist auf Vollbit-Raster nicht darstellbar und genau der Punkt der Uebung.

    Ueber die Aufgabe hinaus gebraucht: die spaetere Rahmenerkennung baut auf
    dieser Symbolfolge auf (siehe PLAN.md T-1710)."""
    if rng is None:
        rng = np.random.default_rng()
    stop_halfbits = int(round(2.0 * stop_bits))
    halfbits: list[float] = []
    for _ in range(n_frames):
        halfbits.extend([0.0, 0.0])
        for _ in range(data_bits):
            bit = float(rng.integers(0, 2))
            halfbits.extend([bit, bit])
        halfbits.extend([1.0] * stop_halfbits)
    return halfbits


def _rrc_kernel(fs: int, baud: float, rolloff: float, span_symbols: int = 8) -> np.ndarray:
    """Wurzel-Nyquist-Filterkern (Root-Raised-Cosine), energienormiert. Nur
    fuer den PSK-Selbsttest (T14-T16, PLAN.md T-904)."""
    sps = fs / baud
    n_taps = int(round(span_symbols * sps))
    if n_taps % 2 == 0:
        n_taps += 1
    t = (np.arange(n_taps) - (n_taps - 1) / 2.0) / fs
    tsym = 1.0 / baud
    h = np.zeros(n_taps)
    for i, tt in enumerate(t):
        if abs(tt) < 1e-12:
            h[i] = 1.0 - rolloff + 4.0 * rolloff / math.pi
        elif rolloff > 0 and abs(abs(4.0 * rolloff * tt / tsym) - 1.0) < 1e-8:
            h[i] = (rolloff / math.sqrt(2.0)) * (
                (1.0 + 2.0 / math.pi) * math.sin(math.pi / (4.0 * rolloff))
                + (1.0 - 2.0 / math.pi) * math.cos(math.pi / (4.0 * rolloff))
            )
        else:
            num = (math.sin(math.pi * tt / tsym * (1.0 - rolloff))
                   + 4.0 * rolloff * tt / tsym * math.cos(math.pi * tt / tsym * (1.0 + rolloff)))
            den = math.pi * tt / tsym * (1.0 - (4.0 * rolloff * tt / tsym) ** 2)
            h[i] = num / den
    h /= math.sqrt(float(np.sum(h ** 2)))
    return h


def _make_psk_signal(fs: int, baud: float, m: int, dur_s: float, offset_hz: float, rng,
                      snr_db: float = 20.0, rolloff: float = 0.35) -> np.ndarray:
    """Synthetisches M-PSK-Signal, wurzel-Nyquist-gefiltert, mit Traeger-
    versatz und Rauschen. Nur fuer den PSK-Selbsttest (T14-T16, PLAN.md
    T-904)."""
    sps = fs / baud
    n = int(fs * dur_s)
    kernel = _rrc_kernel(fs, baud, rolloff)
    half_k = len(kernel) // 2
    margin_symbols = int(math.ceil(half_k / sps)) + 2
    n_symbols = int(math.ceil(n / sps)) + 2 * margin_symbols
    sym_idx = rng.integers(0, m, n_symbols)
    ideal = np.exp(1j * 2.0 * math.pi * sym_idx / m)

    total_len = int(round(n_symbols * sps)) + len(kernel)
    impulses = np.zeros(total_len, dtype=complex)
    positions = np.round(np.arange(n_symbols) * sps).astype(int) + half_k
    positions = np.clip(positions, 0, total_len - 1)
    impulses[positions] = ideal

    shaped = np.convolve(impulses, kernel, mode="full")
    start = int(round(margin_symbols * sps)) + half_k
    sig = shaped[start:start + n]
    if len(sig) < n:
        sig = np.pad(sig, (0, n - len(sig)))

    t = np.arange(n) / fs
    sig = sig * np.exp(1j * 2.0 * math.pi * offset_hz * t)

    sig_power = float(np.mean(np.abs(sig) ** 2))
    snr_lin = 10.0 ** (snr_db / 10.0)
    noise_power = sig_power / snr_lin
    noise = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) * math.sqrt(noise_power / 2.0)
    return sig + noise


def _make_psk_signal_perturbed(fs: int, baud: float, m: int, dur_s: float, offset_hz: float, rng,
                                phase_noise_deg: float = 0.0, mag_noise_frac: float = 0.0,
                                snr_db: float = 20.0, rolloff: float = 0.35) -> np.ndarray:
    """Wie _make_psk_signal, zusaetzlich mit Stoerung der idealen Symbolpunkte
    vor der Pulsformung: gaussscher Phasenfehler (phase_noise_deg, Grad
    Standardabweichung) und/oder multiplikativer Betragsfehler
    (mag_noise_frac, relative Standardabweichung). Nur fuer den Selbsttest
    T21-T23 (PLAN.md T-907)."""
    sps = fs / baud
    n = int(fs * dur_s)
    kernel = _rrc_kernel(fs, baud, rolloff)
    half_k = len(kernel) // 2
    margin_symbols = int(math.ceil(half_k / sps)) + 2
    n_symbols = int(math.ceil(n / sps)) + 2 * margin_symbols
    sym_idx = rng.integers(0, m, n_symbols)
    phase_noise = np.radians(rng.normal(0.0, phase_noise_deg, n_symbols))
    mag_noise = 1.0 + rng.normal(0.0, mag_noise_frac, n_symbols)
    ideal = mag_noise * np.exp(1j * (2.0 * math.pi * sym_idx / m + phase_noise))

    total_len = int(round(n_symbols * sps)) + len(kernel)
    impulses = np.zeros(total_len, dtype=complex)
    positions = np.round(np.arange(n_symbols) * sps).astype(int) + half_k
    positions = np.clip(positions, 0, total_len - 1)
    impulses[positions] = ideal

    shaped = np.convolve(impulses, kernel, mode="full")
    start = int(round(margin_symbols * sps)) + half_k
    sig = shaped[start:start + n]
    if len(sig) < n:
        sig = np.pad(sig, (0, n - len(sig)))

    t = np.arange(n) / fs
    sig = sig * np.exp(1j * 2.0 * math.pi * offset_hz * t)

    sig_power = float(np.mean(np.abs(sig) ** 2))
    snr_lin = 10.0 ** (snr_db / 10.0)
    noise_power = sig_power / snr_lin
    noise = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) * math.sqrt(noise_power / 2.0)
    return sig + noise


def _write_temp_wav(tmp_dir: str, name: str, z: np.ndarray, framerate: int) -> str:
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
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump({"freq_hz": 162475000, "sample_rate_hz": framerate, "is_iq": True}, f)
    return path


def _self_test() -> bool:
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        if cond:
            print(f"OK  {msg}")
        else:
            print(f"FEHLER {msg}", file=sys.stderr)
            ok = False

    fs = 32000

    with tempfile.TemporaryDirectory() as tmp:
        # T1: FSK2 mit stillem Rahmen (0,3 s je Seite, Amplitudenfaktor 0,02) -> Stufe A
        rng1 = np.random.default_rng(101)
        burst1 = _make_fsk_signal(fs, 1200, (-4500.0, 4500.0), 2.0, 0.0, rng1)
        n_silence = int(0.3 * fs)
        silence = 0.02 * (rng1.normal(0, 1, n_silence) + 1j * rng1.normal(0, 1, n_silence)) / math.sqrt(2)
        t1_signal = np.concatenate([silence, burst1, silence])
        t1_path = _write_temp_wav(tmp, "t1.wav", t1_signal, fs)
        r1 = None
        try:
            r1 = analyze_file(t1_path)
            check(len(r1["bursts"]) == 1, f"T1: genau 1 Burst gefunden ({len(r1['bursts'])})")
            check(abs(r1["chosen_burst_duration_s"] - 2.0) <= 0.2,
                  f"T1: Burstdauer im 10%-Toleranzband (gemessen {r1['chosen_burst_duration_s']:.3f} s)")
            check(abs(r1["offset_hz"]) < 50.0, f"T1: |Offset| < 50 Hz (gemessen {r1['offset_hz']:+.1f} Hz)")
            check(abs(r1["baud"] - 1200.0) / 1200.0 <= BAUD_TOL_ZUFALLSFOLGE,
                  f"T1: Baud innerhalb {BAUD_TOL_ZUFALLSFOLGE * 100:.0f}% ({r1['baud']:.1f})")
            check(abs(r1["dev_hz"] - 4500.0) / 4500.0 <= 0.05, f"T1: Hub auf 5% genau ({r1['dev_hz']:.1f} Hz)")
            check(r1["level_count"] == 2, f"T1: Pegelanzahl = 2 ({r1['level_count']})")
        except Exception as exc:
            check(False, f"T1: unerwarteter Fehler ({exc})")

        # T2: FSK2 mit 3 kHz Offset, durchgehend (kein Rahmen) -> Stufe B
        rng2 = np.random.default_rng(202)
        t2_signal = _make_fsk_signal(fs, 1200, (-4500.0, 4500.0), 2.0, 3000.0, rng2)
        t2_path = _write_temp_wav(tmp, "t2.wav", t2_signal, fs)
        try:
            r2 = analyze_file(t2_path)
            check(r2["mode"] == "dauersignal", f"T2: als Dauersignal erkannt (mode={r2['mode']})")
            check(abs(r2["offset_hz"] - 3000.0) <= 100.0, f"T2: Offset auf 100 Hz genau ({r2['offset_hz']:.1f} Hz)")
            check(abs(r2["dev_hz"] - 4500.0) / 4500.0 <= 0.05,
                  f"T2: Hub weiterhin auf 5% genau ({r2['dev_hz']:.1f} Hz)")
        except Exception as exc:
            check(False, f"T2: unerwarteter Fehler ({exc})")

        # T3: FSK4, durchgehend (kein Rahmen) -> Stufe B
        rng3 = np.random.default_rng(303)
        d = 1944.0
        t3_signal = _make_fsk_signal(fs, 2400, (-3 * d, -d, d, 3 * d), 2.0, 0.0, rng3)
        t3_path = _write_temp_wav(tmp, "t3.wav", t3_signal, fs)
        try:
            r3 = analyze_file(t3_path)
            check(r3["mode"] == "dauersignal", f"T3: als Dauersignal erkannt (mode={r3['mode']})")
            check(r3["level_count"] == 4, f"T3: Pegelanzahl = 4 ({r3['level_count']})")
        except Exception as exc:
            check(False, f"T3: unerwarteter Fehler ({exc})")

        # T4: reines Rauschen, kein Burst
        rng4 = np.random.default_rng(404)
        n4 = int(2.0 * fs)
        noise_only = (rng4.normal(0, 1, n4) + 1j * rng4.normal(0, 1, n4)) / math.sqrt(2)
        t4_path = _write_temp_wav(tmp, "t4.wav", noise_only, fs)
        try:
            analyze_file(t4_path)
            check(False, "T4: erwarteter Abbruch blieb aus")
        except RuntimeError as exc:
            check("noise" in str(exc), f"T4: sauberer Abbruch mit Rauschen-Meldung ({exc})")
        except Exception as exc:
            check(False, f"T4: unerwartete Ausnahme statt RuntimeError ({exc})")

        # T5: SVG-Erzeugung aus T1
        if r1 is not None:
            try:
                svg_path = os.path.join(tmp, "t1_out.eye.svg")
                write_svg(svg_path, r1["traces"], r1["dev_hz"])
                check(os.path.exists(svg_path), "T5: SVG-Datei existiert")
                with open(svg_path, encoding="utf-8") as f:
                    content = f.read()
                check(content.startswith("<svg"), "T5: SVG beginnt mit '<svg'")
                check(content.count("<polyline") >= 100,
                      f"T5: mindestens 100 Polylines ({content.count('<polyline')})")
            except Exception as exc:
                check(False, f"T5: unerwarteter Fehler ({exc})")
        else:
            check(False, "T5: uebersprungen, T1 ist fehlgeschlagen")

        # T6 + T7: Flatness-Kennzahl - Ordnungstest und Regressionsschutz der Schwelle
        try:
            _, flat_t2, *_ = compute_contrast_and_flatness(t2_signal, fs)
            _, flat_t3, *_ = compute_contrast_and_flatness(t3_signal, fs)
            _, flat_t4, *_ = compute_contrast_and_flatness(noise_only, fs)
            print(f"  Flatness gemessen: T2={flat_t2:.4f} T3={flat_t3:.4f} T4={flat_t4:.4f} (Schwelle {FLATNESS_THR})")
            check(flat_t4 > flat_t2, f"T6: Flatness Rauschen > Flatness FSK2 ({flat_t4:.4f} > {flat_t2:.4f})")
            check(flat_t2 < FLATNESS_THR, f"T7a: Flatness(T2) < FLATNESS_THR ({flat_t2:.4f} < {FLATNESS_THR})")
            check(flat_t3 < FLATNESS_THR, f"T7b: Flatness(T3) < FLATNESS_THR ({flat_t3:.4f} < {FLATNESS_THR})")
            check(flat_t4 > FLATNESS_THR, f"T7c: Flatness(T4) > FLATNESS_THR ({flat_t4:.4f} > {FLATNESS_THR})")
        except Exception as exc:
            check(False, f"T6/T7: unerwarteter Fehler ({exc})")

        # T8: FSK2 mit unausgeglichener Symbolfolge (80% Einsen/+4500, 20% Nullen/-4500)
        # - Regressionsschutz gegen den Median-Offset-Fehler (siehe Entscheidungslog)
        try:
            rng8 = np.random.default_rng(808)
            t8_signal = _make_fsk_signal(fs, 1200, (-4500.0, 4500.0), 2.0, 0.0, rng8, snr_db=30.0,
                                          level_probs=(0.2, 0.8))
            t8_path = _write_temp_wav(tmp, "t8.wav", t8_signal, fs)
            r8 = analyze_file(t8_path)
            check(abs(r8["offset_hz"]) < 50.0, f"T8: |Offset| < 50 Hz bei 80/20-Verteilung ({r8['offset_hz']:+.1f} Hz)")
        except Exception as exc:
            check(False, f"T8: unerwarteter Fehler ({exc})")

        # T9: Baud-Schaetzung fuer FSK2 bei 512/1200/2400 Bd, je 10 Seeds, Zufallsfolge
        print(f"  T9-Toleranz (Zufallsfolge): {BAUD_TOL_ZUFALLSFOLGE * 100:.0f}%")
        for baud_nominal in (512, 1200, 2400):
            max_err = 0.0
            errs = []
            try:
                for seed in range(10):
                    rng9 = np.random.default_rng(2000 + seed)
                    sig9 = _make_fsk_signal(fs, baud_nominal, (-4500.0, 4500.0), 2.0, 0.0, rng9)
                    path9 = _write_temp_wav(tmp, f"t9_{baud_nominal}_{seed}.wav", sig9, fs)
                    r9 = analyze_file(path9)
                    err = (r9["baud"] - baud_nominal) / baud_nominal * 100.0
                    errs.append(err)
                    max_err = max(max_err, abs(err))
                print(f"  T9 baud={baud_nominal}: errors%=" + ", ".join(f"{e:+.3f}" for e in errs))
                check(max_err / 100.0 <= BAUD_TOL_ZUFALLSFOLGE,
                      f"T9 baud={baud_nominal}: max|Fehler|={max_err:.3f}% <= {BAUD_TOL_ZUFALLSFOLGE * 100:.0f}%")
            except Exception as exc:
                check(False, f"T9 baud={baud_nominal}: unerwarteter Fehler ({exc})")

        # T10: reine Wechselfolge (Praeambel), rauscharm - Regressionstest der Verdopplungskorrektur
        try:
            rng10 = np.random.default_rng(3000)
            t10_signal = _make_fsk_signal(fs, 1200, (-4500.0, 4500.0), 2.0, 0.0, rng10, snr_db=30.0,
                                           pattern="alternating")
            t10_path = _write_temp_wav(tmp, "t10.wav", t10_signal, fs)
            r10 = analyze_file(t10_path)
            err10 = abs(r10["baud"] - 1200.0) / 1200.0
            print(f"  T10: baud={r10['baud']:.4f} case={r10['symbolrate_case']} T_fit={r10['T_fit']:.4f}")
            check(err10 <= BAUD_TOL_WECHSELFOLGE,
                  f"T10: Baud innerhalb {BAUD_TOL_WECHSELFOLGE * 100:.0f}% (Fehler {err10 * 100:.4f}%)")
            check(r10["symbolrate_case"] == "wechselfolge",
                  f"T10: Wechselfolge erkannt (case={r10['symbolrate_case']})")
        except Exception as exc:
            check(False, f"T10: unerwarteter Fehler ({exc})")

        # T11: Schmalband-FSK2 (RTTY-aehnlich), 50 Bd, +-225 Hz Hub, Zufallsfolge,
        # mit --decimate 16 --min-baud 20 - Regressionstest fuer T-903
        t11_signal = None
        t11_path = None
        try:
            rng11 = np.random.default_rng(4000)
            t11_signal = _make_fsk_signal(fs, 50.0, (-225.0, 225.0), 60.0, 0.0, rng11, snr_db=20.0)
            t11_path = _write_temp_wav(tmp, "t11.wav", t11_signal, fs)
            r11 = analyze_file(t11_path, decimate=16, min_baud=20.0)
            print(f"  T11: fs_eff={r11['fs_eff_hz']:.1f} Hz dev_hz={r11['dev_hz']:.1f} "
                  f"baud={r11['baud']:.3f} level_count={r11['level_count']}")
            check(abs(r11["dev_hz"] - 225.0) / 225.0 <= 0.05,
                  f"T11: Hub innerhalb 5% ({r11['dev_hz']:.1f} Hz)")
            check(abs(r11["baud"] - 50.0) / 50.0 <= BAUD_TOL_ZUFALLSFOLGE,
                  f"T11: Baud innerhalb {BAUD_TOL_ZUFALLSFOLGE * 100:.0f}% ({r11['baud']:.3f})")
            check(r11["level_count"] == 2, f"T11: Pegelanzahl = 2 ({r11['level_count']})")
        except Exception as exc:
            check(False, f"T11: unerwarteter Fehler ({exc})")

        # T12: dasselbe Schmalbandsignal als reine Wechselfolge
        try:
            rng12 = np.random.default_rng(4001)
            t12_signal = _make_fsk_signal(fs, 50.0, (-225.0, 225.0), 60.0, 0.0, rng12, snr_db=20.0,
                                           pattern="alternating")
            t12_path = _write_temp_wav(tmp, "t12.wav", t12_signal, fs)
            r12 = analyze_file(t12_path, decimate=16, min_baud=20.0)
            err12 = abs(r12["baud"] - 50.0) / 50.0
            print(f"  T12: baud={r12['baud']:.4f} case={r12['symbolrate_case']}")
            check(err12 <= BAUD_TOL_WECHSELFOLGE,
                  f"T12: Baud innerhalb {BAUD_TOL_WECHSELFOLGE * 100:.0f}% (Fehler {err12 * 100:.4f}%)")
            check(r12["symbolrate_case"] == "wechselfolge",
                  f"T12: Wechselfolge erkannt (case={r12['symbolrate_case']})")
        except Exception as exc:
            check(False, f"T12: unerwarteter Fehler ({exc})")

        # T13: Aliasing-Nachweis mit diskretem Stoerer - T11-Basissignal plus
        # unmodulierter komplexer Traeger bei +3400 Hz, gleiche Amplitude wie
        # das Nutzsignal (SIR = 0 dB). Bei --decimate 16 (fs_eff=2000 Hz,
        # Nyquist +-1000 Hz) faltet 3400 Hz auf -600 Hz - klar im Nutzband,
        # aber weder auf 0 noch auf einer der Rails bei +-225 Hz. Regulaerer
        # Pfad (mit Tiefpass) muss den Stoerer entfernen und wie T11 abschneiden;
        # interner Testpfad ohne Tiefpass (_test_skip_lowpass, ueber die
        # Kommandozeile nicht erreichbar) muss deutlich abweichen.
        if t11_signal is not None:
            t_arr = np.arange(len(t11_signal)) / fs
            carrier = np.exp(1j * 2.0 * math.pi * 3400.0 * t_arr)
            t13_signal = t11_signal + carrier
            t13_path = _write_temp_wav(tmp, "t13.wav", t13_signal, fs)

            r13_lp = None
            try:
                r13_lp = analyze_file(t13_path, decimate=16, min_baud=20.0)
                print(f"  T13 (mit Tiefpass): offset_hz={r13_lp['offset_hz']:+.1f} dev_hz={r13_lp['dev_hz']:.1f} "
                      f"baud={r13_lp['baud']:.3f} level_count={r13_lp['level_count']}")
                check(abs(r13_lp["dev_hz"] - 225.0) / 225.0 <= 0.05,
                      f"T13 (mit Tiefpass): Hub innerhalb 5% ({r13_lp['dev_hz']:.1f} Hz)")
                check(abs(r13_lp["baud"] - 50.0) / 50.0 <= BAUD_TOL_ZUFALLSFOLGE,
                      f"T13 (mit Tiefpass): Baud innerhalb {BAUD_TOL_ZUFALLSFOLGE * 100:.0f}% "
                      f"({r13_lp['baud']:.3f})")
                check(r13_lp["level_count"] == 2,
                      f"T13 (mit Tiefpass): Pegelanzahl = 2 ({r13_lp['level_count']})")
            except Exception as exc:
                check(False, f"T13 (mit Tiefpass): unerwarteter Fehler ({exc})")

            try:
                r13_nolp = analyze_file(t13_path, decimate=16, min_baud=20.0, _test_skip_lowpass=True)
                dev_err_nolp = abs(r13_nolp["dev_hz"] - 225.0) / 225.0
                print(f"  T13 (ohne Tiefpass): offset_hz={r13_nolp['offset_hz']:+.1f} "
                      f"dev_hz={r13_nolp['dev_hz']:.1f} (Fehler {dev_err_nolp * 100:.1f}%) "
                      f"baud={r13_nolp['baud']:.3f} level_count={r13_nolp['level_count']}")
                significant = dev_err_nolp > 0.20 or r13_nolp["level_count"] != 2
                check(significant,
                      "T13 (ohne Tiefpass): Ergebnis weicht deutlich vom korrekten ab (Aliasing-Nachweis, "
                      "Stoerer bei -600 Hz nach Faltung)")
            except RuntimeError as exc:
                print(f"  T13 (ohne Tiefpass): sauberer Abbruch (Exit 2): {exc}")
                check(True, "T13 (ohne Tiefpass): sauberer Abbruch gilt als deutliche Abweichung")
            except Exception as exc:
                check(False, f"T13 (ohne Tiefpass): unerwartete Ausnahme ({exc})")
        else:
            check(False, "T13: uebersprungen, T11 ist fehlgeschlagen")

        # T14-T17: PSK-Pfad (--mode psk), wurzel-Nyquist-gefiltert (Roll-off
        # 0.35), fs=32000, 1800 Bd, Traegerversatz +440 Hz, 5 s, SNR 20 dB
        # (PLAN.md T-904)
        psk_fs = 32000
        psk_baud = 1800.0
        psk_offset = 440.0

        def _check_psk(label: str, m: int, path: str):
            try:
                r = analyze_file_psk(path)
                s_by_m = r["sharpness_by_m"]
                print(f"  {label}: N={r['carrier_n']} Schwelle={r['carrier_threshold']:.2f} "
                      f"M={r['m_chosen']} s_2={s_by_m[2]:.2f} s_4={s_by_m[4]:.2f} s_8={s_by_m[8]:.2f} "
                      f"huellkurve_schaerfe={r['envelope_sharpness']:.2f} "
                      f"(Schwelle {r['envelope_threshold']:.2f}) versatz={r['offset_hz']:+.1f} Hz "
                      f"baud={r['baud']:.2f} phasenpeaks={r['phase_peak_count']} evm={r['evm_pct']:.1f}% "
                      f"Entscheidung=Auswertung")
                check(r["m_chosen"] == m, f"{label}: M={m} erkannt ({r['m_chosen']})")
                check(abs(r["offset_hz"] - psk_offset) <= 20.0,
                      f"{label}: Versatz innerhalb 20 Hz ({r['offset_hz']:+.1f} Hz)")
                check(abs(r["baud"] - psk_baud) / psk_baud <= 0.01,
                      f"{label}: Baud innerhalb 1% ({r['baud']:.2f})")
                check(r["phase_peak_count"] == m, f"{label}: Phasenpeaks = {m} ({r['phase_peak_count']})")
                return r
            except Exception as exc:
                check(False, f"{label}: unerwarteter Fehler ({exc})")
                return None

        rng14 = np.random.default_rng(5000)
        t14_signal = _make_psk_signal(psk_fs, psk_baud, 2, 5.0, psk_offset, rng14, snr_db=20.0)
        t14_path = _write_temp_wav(tmp, "t14.wav", t14_signal, psk_fs)
        _check_psk("T14 BPSK", 2, t14_path)

        rng15 = np.random.default_rng(5001)
        t15_signal = _make_psk_signal(psk_fs, psk_baud, 4, 5.0, psk_offset, rng15, snr_db=20.0)
        t15_path = _write_temp_wav(tmp, "t15.wav", t15_signal, psk_fs)
        _check_psk("T15 QPSK", 4, t15_path)

        rng16 = np.random.default_rng(6000)
        t16_signal = _make_psk_signal(psk_fs, psk_baud, 8, 5.0, psk_offset, rng16, snr_db=20.0)
        t16_path = _write_temp_wav(tmp, "t16.wav", t16_signal, psk_fs)
        _check_psk("T16 8-PSK", 8, t16_path)

        # T17: Rauschen ohne Signal. Erneute Messung mit 30 s (T17b) prueft die
        # ln(N)-Skalierung selbst - Messauftrag M1b: wenn die Kopplung an N
        # richtig konstruiert ist, muss auch die lange Aufnahme abbrechen.
        # Amplitude auf 0.2 skaliert: bei voller Amplitude (wie in T4/T9 mit
        # kurzer Dauer unauffaellig) reisst die 16-Bit-WAV bei N=960000
        # Samples einzelne Ausreisser der Gauss-Verteilung ins Clipping (bis
        # zu 3.6 statt <=1.0), was im M-te-Potenz-Spektrum starke
        # Stoerlinien erzeugt und die Schaerfe-Messung verfaelscht (an T17b
        # numerisch nachgewiesen: s_4 sprang durch Clipping von 4.2 auf 67).
        def _check_psk_noise(label: str, dur_s: float, seed: int):
            rng = np.random.default_rng(seed)
            n = int(dur_s * psk_fs)
            noise = 0.2 * (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)) / math.sqrt(2)
            threshold = SCHAERFE_FAKTOR * math.log(n)
            s_by_m = {m: carrier_candidate(noise, psk_fs, m)[1] for m in (2, 4, 8)}
            max_s = max(s_by_m.values())
            entscheidung = "Abbruch" if max_s <= threshold else "Auswertung"
            print(f"  {label}: N={n} Schwelle={threshold:.2f} s_2={s_by_m[2]:.3f} s_4={s_by_m[4]:.3f} "
                  f"s_8={s_by_m[8]:.3f} huellkurve_schaerfe=n/a (Abbruch vor P3) Entscheidung={entscheidung}")
            path = _write_temp_wav(tmp, f"{label.split()[0].lower()}.wav", noise, psk_fs)
            try:
                analyze_file_psk(path)
                check(False, f"{label}: erwarteter Abbruch blieb aus")
            except RuntimeError as exc:
                print(f"  {label}: sauberer Abbruch (Exit 2): {exc}")
                check(True, f"{label}: sauberer Abbruch mit RuntimeError ({exc})")
            except Exception as exc:
                check(False, f"{label}: unerwartete Ausnahme statt RuntimeError ({type(exc).__name__}: {exc})")

        _check_psk_noise("T17", 5.0, 5003)
        _check_psk_noise("T17b", 30.0, 5004)

        # T18: FSK2-Signal (aus T1) im PSK-Modus - haelt nur fest, was bei
        # falscher Modusangabe passiert (kein Korrektheitsanspruch, nur kein
        # Absturz)
        if t1_path is not None:
            try:
                r18 = analyze_file_psk(t1_path)
                print(f"  T18 (FSK-Signal im PSK-Modus): kein Abbruch - M={r18['m_chosen']} "
                      f"baud={r18['baud']:.2f} evm={r18['evm_pct']:.1f}%")
                check(True, "T18: kein Absturz (Ergebnis geliefert, siehe Protokoll)")
            except RuntimeError as exc:
                print(f"  T18 (FSK-Signal im PSK-Modus): sauberer Abbruch: {exc}")
                check(True, "T18: kein Absturz (sauberer Abbruch, siehe Protokoll)")
            except Exception as exc:
                check(False, f"T18: unerwarteter Absturz ({type(exc).__name__}: {exc})")
        else:
            check(False, "T18: uebersprungen, T1 ist fehlgeschlagen")

        # T19: Einheitstests der Peaksuche (plateau_local_maxima), ohne
        # Signalerzeugung, direkt auf Arrays (PLAN.md T-906)
        check(plateau_local_maxima([0, 1, 3, 3, 1, 0], circular=False) == [2],
              "T19a: linear, gerades Plateau -> Index 2")
        check(plateau_local_maxima([0, 1, 3, 3, 3, 1, 0], circular=False) == [3],
              "T19b: linear, ungerades Plateau -> Index 3")
        check(plateau_local_maxima([0, 1, 3, 3, 7, 1, 0], circular=False) == [4],
              "T19c: linear, Schulter -> nur Index 4 (das 3er-Plateau zaehlt nicht als Peak)")

        def _old_strict_local_max(counts, circular):
            n = len(counts)
            if circular:
                return [i for i in range(n)
                        if counts[i] > counts[(i - 1) % n] and counts[i] > counts[(i + 1) % n]]
            return [i for i in range(1, n - 1)
                    if counts[i] > counts[i - 1] and counts[i] > counts[i + 1]]

        def _has_neighbor_plateau(arr, circular):
            n = len(arr)
            if any(arr[i] == arr[i + 1] for i in range(n - 1)):
                return True
            return circular and arr[-1] == arr[0]

        def _runs_of_reference(values):
            n = len(values)
            runs = []
            i = 0
            while i < n:
                j = i
                while j + 1 < n and values[j + 1] == values[i]:
                    j += 1
                runs.append((i, j - i + 1))
                i = j + 1
            return runs

        def _extra_index_is_genuine_plateau_peak(arr, idx, circular):
            """Unabhaengige Nachrechnung (bildet Laeufe separat, ruft
            plateau_local_maxima NICHT auf): liegt idx in einem Lauf
            gleicher Werte, dessen Wert strikt groesser ist als der Wert des
            vorhergehenden und des nachfolgenden Laufs?"""
            n = len(arr)
            if not circular:
                runs = _runs_of_reference(arr)
                for k, (start, length) in enumerate(runs):
                    if start <= idx < start + length:
                        if start == 0 or start + length == n:
                            return False
                        val = arr[start]
                        prev_val = arr[runs[k - 1][0]]
                        next_val = arr[runs[k + 1][0]]
                        return val > prev_val and val > next_val
                return False
            if all(v == arr[0] for v in arr):
                return False
            s = next(i for i in range(n) if arr[i] != arr[(i - 1) % n])
            rotated = [arr[(s + k) % n] for k in range(n)]
            runs = _runs_of_reference(rotated)
            num_runs = len(runs)
            rot_idx = (idx - s) % n
            for k, (start, length) in enumerate(runs):
                if start <= rot_idx < start + length:
                    val = rotated[start]
                    prev_val = rotated[runs[(k - 1) % num_runs][0]]
                    next_val = rotated[runs[(k + 1) % num_runs][0]]
                    return val > prev_val and val > next_val
            return False

        rng19 = np.random.default_rng(7000)
        t19d_ok = True
        n_no_plateau = 0
        n_with_plateau = 0
        n_extra_peaks_total = 0
        for trial in range(200):
            arr = rng19.integers(0, 1001, 201).tolist()
            for circ in (False, True):
                got = plateau_local_maxima(arr, circular=circ)
                expected = _old_strict_local_max(arr, circular=circ)
                if not _has_neighbor_plateau(arr, circ):
                    n_no_plateau += 1
                    if got != expected:
                        t19d_ok = False
                        print(f"  T19d-1: Abweichung (plateaufrei) bei trial={trial} circular={circ}: "
                              f"plateau_local_maxima={got} alt={expected} array={arr}",
                              file=sys.stderr)
                    continue
                n_with_plateau += 1
                missing = [i for i in expected if i not in got]
                extra = [i for i in got if i not in expected]
                if missing:
                    t19d_ok = False
                    print(f"  T19d-2: von der alten Logik gefundener Peak fehlt bei trial={trial} "
                          f"circular={circ}: fehlend={missing} plateau_local_maxima={got} alt={expected} "
                          f"array={arr}", file=sys.stderr)
                for idx in extra:
                    if not _extra_index_is_genuine_plateau_peak(arr, idx, circ):
                        t19d_ok = False
                        print(f"  T19d-2: zusaetzlicher Index {idx} ist kein echtes Plateau-Maximum bei "
                              f"trial={trial} circular={circ}: plateau_local_maxima={got} alt={expected} "
                              f"array={arr}", file=sys.stderr)
                n_extra_peaks_total += len(extra)
        print(f"  T19d: plateaufreie Arrays/Richtungen={n_no_plateau} mit Plateau={n_with_plateau} "
              f"zusaetzliche Peaks insgesamt={n_extra_peaks_total}")
        check(t19d_ok, "T19d: Rueckwaertskompatibilitaet (T19d-1 plateaufrei exakt, T19d-2 mit Plateau "
                        "nur echte zusaetzliche Maxima, keine verlorenen)")

        t19e_counts = [9, 7, 5, 3, 1, 3, 5, 9]
        # Bins 7 und 0 bilden zyklisch ein Plateau (beide 9), symmetrisch
        # monoton fallend zur Mitte (Bin 4 = 1) -> einziges lokales Maximum.
        # plateau_local_maxima rotiert intern auf den ersten Wertwechsel
        # (Bin 1, da counts[0]==counts[7]), der Lauf {6,7} hat in rotierter
        # Zaehlung Laenge 2 mit unterer Mitte 6; zurueckgerechnet auf den
        # unrotierten Index: (1 + 6) % 8 = 7.
        check(plateau_local_maxima(t19e_counts, circular=True) == [7],
              "T19e: zyklisch, Plateau ueber die Array-Grenze -> Index 7")

        check(plateau_local_maxima([4, 4, 4, 4, 4, 4, 4, 4], circular=True) == [],
              "T19f: alle Werte gleich, zyklisch -> keine Peaks")
        check(plateau_local_maxima([4, 4, 4, 4, 4, 4], circular=False) == [],
              "T19g: alle Werte gleich, linear -> keine Peaks")

        # T20: Regression am urspruenglichen Fundfall (PLAN.md T-906) - Seed
        # 5002 lieferte bei T-904 wegen eines Plateaus im Phasenhistogramm 7
        # statt 8 Peaks und wurde deshalb gegen 6000 getauscht (T16). T16
        # behaelt Seed 6000, T20 deckt den urspruenglichen Seed ab.
        rng20 = np.random.default_rng(5002)
        t20_signal = _make_psk_signal(psk_fs, psk_baud, 8, 5.0, psk_offset, rng20, snr_db=20.0)
        t20_path = _write_temp_wav(tmp, "t20.wav", t20_signal, psk_fs)
        _check_psk("T20 8-PSK (Seed 5002)", 8, t20_path)

        # T21-T23: Kennzahltrennung Betragsstreuung/Phasenfehler (PLAN.md
        # T-907). Absolute Schwellen sind fuer diese beiden Kennzahlen
        # ungeeignet, weil der Rauschboden des Fixtures bei SNR 20 dB bereits
        # mag_cv_pct 14,1-14,7 % und phase_rms_deg 3,09-3,14 Grad erzeugt
        # (gemessen 2026-09-02, 10 Seeds) und sich quadratisch mit der
        # gezielten Stoerung addiert. Geprueft wird deshalb die selektive
        # Wirkung gegen eine mitgemessene Baseline: fuer 10 Seeds je drei
        # Auspraegungen desselben Fixtures (BPSK, 1800 Bd, fs=32000, 5 s,
        # wurzel-Nyquist, SNR 20 dB) mit identischem Seed - B (unveraendert),
        # P (zusaetzlich Phasenrauschen 10 Grad Standardabweichung), A
        # (zusaetzlich multiplikatives Betragsrauschen 20 %).
        t21_ok = True
        t22_ok = True
        b_evm, b_mag, b_phase = [], [], []
        p_evm, p_mag, p_phase = [], [], []
        a_evm, a_mag, a_phase = [], [], []
        for seed in range(5010, 5020):
            rng_b = np.random.default_rng(seed)
            sig_b = _make_psk_signal_perturbed(psk_fs, psk_baud, 2, 5.0, psk_offset, rng_b)
            r_b = analyze_file_psk(_write_temp_wav(tmp, f"t21b_{seed}.wav", sig_b, psk_fs))

            rng_p = np.random.default_rng(seed)
            sig_p = _make_psk_signal_perturbed(psk_fs, psk_baud, 2, 5.0, psk_offset, rng_p,
                                                phase_noise_deg=10.0)
            r_p = analyze_file_psk(_write_temp_wav(tmp, f"t21p_{seed}.wav", sig_p, psk_fs))

            rng_a = np.random.default_rng(seed)
            sig_a = _make_psk_signal_perturbed(psk_fs, psk_baud, 2, 5.0, psk_offset, rng_a,
                                                mag_noise_frac=0.20)
            r_a = analyze_file_psk(_write_temp_wav(tmp, f"t21a_{seed}.wav", sig_a, psk_fs))

            b_evm.append(r_b["evm_pct"]); b_mag.append(r_b["mag_cv_pct"]); b_phase.append(r_b["phase_rms_deg"])
            p_evm.append(r_p["evm_pct"]); p_mag.append(r_p["mag_cv_pct"]); p_phase.append(r_p["phase_rms_deg"])
            a_evm.append(r_a["evm_pct"]); a_mag.append(r_a["mag_cv_pct"]); a_phase.append(r_a["phase_rms_deg"])

            if not (r_p["phase_rms_deg"] > 2.0 * r_b["phase_rms_deg"]):
                t21_ok = False
                print(f"  T21: FEHLER Seed {seed}: phase_rms_deg(P)={r_p['phase_rms_deg']:.2f} nicht > "
                      f"2*phase_rms_deg(B)={2.0 * r_b['phase_rms_deg']:.2f}")
            if not (r_p["mag_cv_pct"] < 1.3 * r_b["mag_cv_pct"]):
                t21_ok = False
                print(f"  T21: FEHLER Seed {seed}: mag_cv_pct(P)={r_p['mag_cv_pct']:.2f} nicht < "
                      f"1.3*mag_cv_pct(B)={1.3 * r_b['mag_cv_pct']:.2f}")
            if not (r_a["mag_cv_pct"] > 1.5 * r_b["mag_cv_pct"]):
                t22_ok = False
                print(f"  T22: FEHLER Seed {seed}: mag_cv_pct(A)={r_a['mag_cv_pct']:.2f} nicht > "
                      f"1.5*mag_cv_pct(B)={1.5 * r_b['mag_cv_pct']:.2f}")
            if not (r_a["phase_rms_deg"] < 1.3 * r_b["phase_rms_deg"]):
                t22_ok = False
                print(f"  T22: FEHLER Seed {seed}: phase_rms_deg(A)={r_a['phase_rms_deg']:.2f} nicht < "
                      f"1.3*phase_rms_deg(B)={1.3 * r_b['phase_rms_deg']:.2f}")

        check(t21_ok, "T21: Phasenstoerung wirkt selektiv (phase_rms_deg(P) > 2*B und mag_cv_pct(P) < "
                       "1.3*B, alle 10 Seeds)")
        check(t22_ok, "T22: Betragsstoerung wirkt selektiv (mag_cv_pct(A) > 1.5*B und phase_rms_deg(A) < "
                       "1.3*B, alle 10 Seeds)")

        print(f"  T23 (Trennschaerfe) B: evm={min(b_evm):.2f}-{max(b_evm):.2f}% "
              f"mag_cv={min(b_mag):.2f}-{max(b_mag):.2f}% phase_rms={min(b_phase):.2f}-{max(b_phase):.2f} Grad")
        print(f"  T23 (Trennschaerfe) P: evm={min(p_evm):.2f}-{max(p_evm):.2f}% "
              f"mag_cv={min(p_mag):.2f}-{max(p_mag):.2f}% phase_rms={min(p_phase):.2f}-{max(p_phase):.2f} Grad")
        print(f"  T23 (Trennschaerfe) A: evm={min(a_evm):.2f}-{max(a_evm):.2f}% "
              f"mag_cv={min(a_mag):.2f}-{max(a_mag):.2f}% phase_rms={min(a_phase):.2f}-{max(a_phase):.2f} Grad")
        mag_b_mean = float(np.mean(b_mag))
        mag_a_mean = float(np.mean(a_mag))
        expected_quad = math.sqrt(mag_b_mean ** 2 + 20.0 ** 2)
        print(f"  T23: quadratische Erwartung sqrt(mag_cv_pct(B)^2 + 20^2) = {expected_quad:.2f} % "
              f"gegen gemessenes mag_cv_pct(A) (Mittel) = {mag_a_mean:.2f} %")
        check(True, "T23: Protokollausgabe der vier Kennzahlen je Auspraegung (kein Schwellwert), EVM "
                     "allein unterscheidet P und A nicht, die neuen Kennzahlen schon")

        # T24: --json im PSK-Modus (PLAN.md T-908) - T14-Fixture (BPSK)
        # erneut auswerten, JSON schreiben und pruefen: lesbar, zentrale
        # Schluessel vorhanden, rekursiv keine numpy-Typen, Rohdatenfelder
        # (symbols, phase_hist) fehlen.
        def _assert_no_numpy(value, path="root"):
            if isinstance(value, dict):
                for k, v in value.items():
                    _assert_no_numpy(v, f"{path}.{k}")
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    _assert_no_numpy(v, f"{path}[{i}]")
            elif not isinstance(value, (float, int, str, bool, type(None))):
                raise TypeError(f"Fremdtyp bei {path}: {type(value).__name__}")

        try:
            r24 = analyze_file_psk(t14_path)
            t24_json_path = os.path.join(tmp, "t24.json")
            write_json_psk(t24_json_path, r24)
            check(os.path.exists(t24_json_path), "T24: JSON-Datei existiert")
            with open(t24_json_path, encoding="utf-8") as f:
                t24_data = json.load(f)
            check(True, "T24: JSON-Datei ist mit json.load lesbar")
            required_keys = ("m_chosen", "offset_hz", "baud", "evm_pct")
            missing = [k for k in required_keys if k not in t24_data]
            check(not missing, f"T24: Schluessel {required_keys} vorhanden (fehlend: {missing})")
            try:
                _assert_no_numpy(t24_data)
                check(True, "T24: kein Wert ist ein numpy-Typ (rekursiv geprueft)")
            except TypeError as exc:
                check(False, f"T24: numpy-Typ gefunden ({exc})")
            check("symbols" not in t24_data and "phase_hist" not in t24_data,
                  "T24: Rohdatenfelder symbols/phase_hist fehlen")
        except Exception as exc:
            check(False, f"T24: unerwarteter Fehler ({exc})")

    # --- T25 (T-1608): Zeitfenster lesen ------------------------------------
    # Der Ausschnitt muss dasselbe liefern wie eine Datei, die NUR aus diesem
    # Ausschnitt besteht - sonst waere die Fenstermessung in :8070 etwas
    # anderes als eine Messung an einem Zuschnitt.
    with tempfile.TemporaryDirectory() as tmp:
        fs25 = 8000
        rng25 = np.random.default_rng(25)
        z_still = np.zeros(4 * fs25, dtype=complex)
        z_mitte = _make_fsk_signal(fs25, 100.0, (-500.0, 500.0), 4.0, 0.0, rng25)
        t25_path = _write_temp_wav(tmp, "t25.wav",
                                    np.concatenate([z_still, z_mitte, z_still]), fs25)

        fr_voll, z_voll = read_wav_iq(t25_path)
        check(len(z_voll) == 12 * fs25, f"T25: ganze Datei {len(z_voll)} Samples")
        fr_win, z_win = read_wav_iq(t25_path, 4.0, 8.0)
        check(fr_win == fs25, "T25: Abtastrate unveraendert")
        check(len(z_win) == 4 * fs25, f"T25: Fenster {len(z_win)} statt {4 * fs25}")
        check(np.array_equal(z_win, z_voll[4 * fs25:8 * fs25]),
              "T25: Fensterinhalt deckungsgleich mit dem Ausschnitt der ganzen Datei")

        for t0v, t1v, was in ((5.0, 5.0, "leeres Fenster"),
                               (0.0, MAX_WINDOW_S + 1.0, "Fenster ueber MAX_WINDOW_S"),
                               (100.0, 101.0, "Fenster hinter dem Dateiende")):
            try:
                read_wav_iq(t25_path, t0v, t1v)
                check(False, f"T25: {was} haette ValueError werfen muessen")
            except ValueError:
                check(True, f"T25: {was} -> ValueError")

        # Ueber das Dateiende hinaus wird geklemmt, nicht abgelehnt.
        _, z_clip = read_wav_iq(t25_path, 10.0, 30.0)
        check(len(z_clip) == 2 * fs25, f"T25: geklemmt auf {len(z_clip)} Samples")

        # Und die Auswertung des Fensters findet die Symbolrate wieder - in
        # der ganzen Datei wuerde die Ruhe davor und danach stoeren.
        try:
            r25 = analyze_file(t25_path, t0_s=4.0, t1_s=8.0)
            check(abs(r25["baud"] - 100.0) / 100.0 < 0.15,
                  f"T25: Symbolrate im Fenster {r25['baud']:.1f} statt 100")
        except Exception as exc:
            check(False, f"T25: Auswertung im Fenster fehlgeschlagen ({exc})")

    # --- T26 (T-1614): Deckel fuer die Konstellationspunkte -----------------
    # 360 001 Symbole ergaben 27 MB SVG - je Symbol ein <circle>, ohne Grenze.
    # Der Deckel muss greifen, das Bild aber unveraendert gross bleiben.
    with tempfile.TemporaryDirectory() as tmp:
        rng26 = np.random.default_rng(26)
        n26 = 50000
        winkel = rng26.integers(0, 4, size=n26) * (np.pi / 2) + np.pi / 4
        sym26 = np.exp(1j * winkel) * (1.0 + 0.05 * rng26.normal(size=n26))
        hist26 = np.histogram(np.angle(sym26 ** 4), bins=N_ZERO_HIST_BINS)[0]

        gross = os.path.join(tmp, "alle.svg")
        klein = os.path.join(tmp, "gedeckelt.svg")
        write_svg_psk(gross, sym26, hist26, max_points=0)
        write_svg_psk(klein, sym26, hist26)
        n_gross = os.path.getsize(gross)
        n_klein = os.path.getsize(klein)
        check(n_gross > 3_000_000, f"T26: ungedeckelt {n_gross} Byte (erwartet > 3 MB)")
        check(n_klein < 300_000, f"T26: gedeckelt {n_klein} Byte (erwartet < 300 kB)")
        check(n_klein * 8 < n_gross, "T26: Deckel bringt weniger als Faktor 8")

        with open(klein, encoding="utf-8") as f26:
            inhalt = f26.read()
        check(inhalt.count("<circle") <= MAX_CONST_POINTS + 5,
              f"T26: {inhalt.count('<circle')} Kreise trotz Deckel {MAX_CONST_POINTS}")
        check(f"von {n26} gezeigt" in inhalt, "T26: Hinweis auf die Auswahl fehlt")

        # Wenige Symbole: kein Deckel, kein Hinweis.
        wenig = os.path.join(tmp, "wenig.svg")
        write_svg_psk(wenig, sym26[:50], hist26)
        with open(wenig, encoding="utf-8") as f26:
            inhalt_wenig = f26.read()
        check("gezeigt" not in inhalt_wenig, "T26: Hinweis obwohl nichts weggelassen wurde")
        check(inhalt_wenig.count("<circle") >= 50, "T26: zu wenige Punkte gezeichnet")

    # --- T27 (T-1620): Abstimmung und Dezimationsvorschlag ------------------
    # Befund 2026-09-10 an 20260910_224408_11038kHz_IQ.wav (RTTY, 32 kHz,
    # Traeger +293 Hz, Hub 189 Hz): ohne Verschiebung laesst sich nicht stark
    # genug dezimieren, weil der Tiefpass um 0 Hz sitzt. Bei --decimate 16
    # blieben 2 kHz Rauschbandbreite auf 190 Hz Signal (Flatness 0,982 gegen
    # Schwelle 0,94, also als Rauschen eingestuft), bei --decimate 64 fiel die
    # Mark-Frequenz aus dem Durchlass.
    fs27 = 32000
    t27 = np.arange(fs27) / fs27
    z27 = np.exp(2j * np.pi * 293.5 * t27)
    verschoben = tune_to_baseband(z27, fs27, 293.5)
    rest_hz = float(np.angle(np.vdot(verschoben[:-1], verschoben[1:]))) * fs27 / (2 * np.pi)
    check(abs(rest_hz) < 0.01, f"T27: nach Verschiebung {rest_hz:.4f} Hz (Rest praktisch null)")
    check(np.allclose(tune_to_baseband(z27, fs27, 0.0), z27),
          "T27: tune_hz=0 laesst das Signal unveraendert")
    # Rueckverschiebung muss das Original wiederherstellen.
    zurueck = tune_to_baseband(verschoben, fs27, -293.5)
    check(np.allclose(zurueck, z27, atol=1e-9), "T27: Verschiebung umkehrbar")

    # Der Vorschlag muss die halbe Bandbreite in den Durchlass (0,2 * fs_eff)
    # legen und nie unter 4 Samples je Symbol fallen.
    for bw27 in (200.0, 400.0, 2400.0, 15000.0):
        n27 = suggest_decimation(fs27, bw27, 20.0)
        fs_eff27 = fs27 / n27
        check(n27 >= 1 and (n27 & (n27 - 1)) == 0, f"T27: N={n27} ist Zweierpotenz")
        # Passt das Signal nicht einmal ungedeckelt in den Durchlass, bleibt
        # N=1 das Beste - dann greift die Bedingung nicht.
        if n27 > 1:
            check(0.2 * fs_eff27 >= bw27 / 2.0,
                  f"T27: bw={bw27:.0f} Hz passt in +-{0.2 * fs_eff27:.0f} Hz")
        check(fs_eff27 >= 4.0 * 20.0,
              f"T27: fs_eff {fs_eff27:.0f} Hz reicht fuer min_baud=20")
    check(suggest_decimation(fs27, 200.0, 20.0) == 32,
          "T27: 200 Hz Bandbreite -> N=32 (1000 Hz effektiv)")
    check(suggest_decimation(fs27, 25000.0, 20.0) == 1,
          "T27: breites Signal -> keine Dezimation")
    check(suggest_decimation(fs27, 0.0, 20.0) == 1, "T27: Bandbreite 0 -> N=1")

    # Praxisnachweis: RTTY-artiges Signal bei +293 Hz. Ohne Verschiebung ist
    # die Momentanfrequenz rauschartig, mit Verschiebung und Dezimation nicht.
    with tempfile.TemporaryDirectory() as tmp27:
        rng27 = np.random.default_rng(27)
        z_sig = _make_fsk_signal(fs27, 45.45, (-85.0, 85.0), 3.0, 293.5, rng27, snr_db=6.0)
        pfad27 = _write_temp_wav(tmp27, "t27.wav", z_sig, fs27)
        _, z_roh = read_wav_iq(pfad27)
        _, flach_roh, _, _, _, _ = compute_contrast_and_flatness(z_roh, fs27)
        n_vor = suggest_decimation(fs27, 170.0, 20.0)
        z_abg, fs_abg = apply_decimation(tune_to_baseband(z_roh, fs27, 293.5),
                                          fs27, n_vor, 20.0)
        _, flach_abg, _, _, _, _ = compute_contrast_and_flatness(z_abg, int(fs_abg))
        check(flach_abg < flach_roh,
              f"T27: Flatness sinkt, roh {flach_roh:.3f} -> abgestimmt {flach_abg:.3f}")
        check(flach_abg <= FLATNESS_THR,
              f"T27: abgestimmt {flach_abg:.3f} liegt unter der Schwelle {FLATNESS_THR}")

    # --- T28 (T-1623): Hoerbarmachung ---------------------------------------
    # Die Zusage: ein Traeger an der abgestimmten Stelle muss danach als Ton
    # bei AUDIO_SIDETONE_HZ wiederzufinden sein. Der Sidetone-Versatz ist der
    # Grund, warum dasselbe Verfahren fuer CW und fuer RTTY/PSK31 taugt - ein
    # Traeger wird zum hoerbaren Ton statt zu Gleichspannung.
    with tempfile.TemporaryDirectory() as tmp28:
        fs28 = 32000
        rng28 = np.random.default_rng(28)
        t28 = np.arange(fs28 * 3) / fs28
        z28 = (np.exp(2j * np.pi * 1500.0 * t28)
               + 0.02 * (rng28.normal(size=len(t28)) + 1j * rng28.normal(size=len(t28))))
        pfad28 = _write_temp_wav(tmp28, "t28.wav", z28, fs28)

        rate28, audio28 = iq_to_audio(pfad28, tune_hz=1500.0, bandbreite_hz=500.0)
        check(rate28 == AUDIO_RATE_HZ, f"T28: Zielrate {rate28} Hz")
        check(audio28.dtype == np.dtype("<i2"), f"T28: 16 Bit ({audio28.dtype})")
        check(abs(len(audio28) / rate28 - 3.0) < 0.05,
              f"T28: Dauer {len(audio28) / rate28:.2f} s bleibt erhalten")

        spek28 = np.abs(np.fft.rfft(audio28.astype(np.float64) * np.hanning(len(audio28))))
        f_peak = float(np.fft.rfftfreq(len(audio28), 1.0 / rate28)[int(np.argmax(spek28))])
        check(abs(f_peak - AUDIO_SIDETONE_HZ) < 15.0,
              f"T28: Traeger landet bei {f_peak:.0f} Hz statt {AUDIO_SIDETONE_HZ:.0f}")

        spitze28 = float(np.max(np.abs(audio28))) / 32767.0
        check(0.8 < spitze28 < 0.95, f"T28: Aussteuerung {spitze28:.2f} mit Reserve")

        # Ein Zeitfenster wird beachtet, und ohne Abstimmung landet der
        # Traeger NICHT auf dem Sidetone - das zeigt, dass tune_hz wirkt.
        _, audio_fenster = iq_to_audio(pfad28, t0_s=1.0, t1_s=2.0, tune_hz=1500.0,
                                        bandbreite_hz=500.0)
        check(abs(len(audio_fenster) / AUDIO_RATE_HZ - 1.0) < 0.05,
              "T28: Zeitfenster wird beachtet")
        _, audio_ohne = iq_to_audio(pfad28, tune_hz=0.0)
        spek_ohne = np.abs(np.fft.rfft(audio_ohne.astype(np.float64)
                                        * np.hanning(len(audio_ohne))))
        f_ohne = float(np.fft.rfftfreq(len(audio_ohne), 1.0 / AUDIO_RATE_HZ)[
            int(np.argmax(spek_ohne))])
        check(abs(f_ohne - AUDIO_SIDETONE_HZ) > 100.0,
              f"T28: ohne Abstimmung liegt der Ton woanders ({f_ohne:.0f} Hz)")

        # Zu langer Ausschnitt wird abgelehnt, nicht still gekuerzt.
        try:
            iq_to_audio(pfad28, t0_s=0.0, t1_s=AUDIO_MAX_S + 10.0)
            check(False, "T28: zu langer Ausschnitt haette ValueError werfen muessen")
        except ValueError:
            check(True, "T28: zu langer Ausschnitt -> ValueError")

        ziel28 = os.path.join(tmp28, "out.wav")
        write_audio_wav(ziel28, rate28, audio28)
        with wave.open(ziel28, "rb") as w28:
            check(w28.getnchannels() == 1 and w28.getsampwidth() == 2,
                  "T28: mono, 16 Bit geschrieben")
            check(w28.getframerate() == AUDIO_RATE_HZ, "T28: Rate im Dateikopf")
            check(w28.getnframes() == len(audio28), "T28: alle Samples geschrieben")

    # --- T29 (T-1622): Tasten gegen Fading ----------------------------------
    # Das Gegenstueck zur FLATNESS_THR-Kalibrierung: zwei Seeds, drei
    # Fadingtiefen, drei Stoerabstaende. Gemessen wurde die Flatness der
    # Momentanfrequenz IN DEN LUECKEN - gefadetes Dauersignal 0,745..0,950,
    # echt getastet 0,988..0,996. Die Schwelle liegt mittig dazwischen.
    fs29 = 2000
    t29 = np.arange(6 * fs29) / fs29
    for seed29 in (22, 77):
        rng29 = np.random.default_rng(seed29)
        for snr29 in (20, 10, 6):
            z29 = _make_fsk_signal(fs29, 45.45, (-100.0, 100.0), 6.0, 0.0, rng29,
                                    snr_db=snr29)
            # Gefadetes Dauersignal: Amplitude schwankt, gesendet wird durchgehend.
            for tiefe29 in (0.5, 0.75, 0.9):
                huelle = 1.0 - tiefe29 + tiefe29 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.8 * t29)) ** 2
                _, mode29, kontrast29, _, _ = detect_bursts(z29 * huelle, fs29)
                check(mode29 == "dauersignal",
                      f"T29: Fading t={tiefe29} snr={snr29} -> {mode29} "
                      f"(Kontrast {kontrast29:.1f} dB)")

            # Echt getastet: in den Pausen steht NUR der Rauschboden.
            zk29 = z29.copy()
            amp29 = 10 ** (-snr29 / 20.0)
            for k29 in range(0, len(zk29), fs29):
                a29 = min(k29 + 400, len(zk29))
                b29 = min(k29 + fs29, len(zk29))
                if b29 > a29:
                    zk29[a29:b29] = amp29 * (rng29.normal(size=b29 - a29)
                                              + 1j * rng29.normal(size=b29 - a29)) / math.sqrt(2)
            bursts29, mode29, _, _, _ = detect_bursts(zk29, fs29)
            check(mode29 == "getastet",
                  f"T29: getastet snr={snr29} seed={seed29} -> {mode29}")
            check(len(bursts29) >= 3,
                  f"T29: getastet snr={snr29} liefert {len(bursts29)} Bursts")

    # Zu kurze Luecke: keine Aussage moeglich, also bleibt es beim Tasten.
    p_kurz = np.ones(1000)
    f_kurz = np.zeros(1000)
    check(_luecken_sind_leer(p_kurz, f_kurz, [(0, 950)], fs29),
          "T29: zu wenig Luecke -> Entscheidung bleibt beim Tasten")
    check(BURST_GAP_FLATNESS > 0.95 and BURST_GAP_FLATNESS < 0.988,
          f"T29: Schwelle {BURST_GAP_FLATNESS} liegt in der gemessenen Luecke")

    # --- T30 (T-1613): OFDM-Hinweis -----------------------------------------
    # Gemessen 2026-09-11 ueber sechs Seeds je Fall (siehe Moduldoku). Hier
    # laeuft ein Seed je Traegerabstand - die volle Matrix waere im
    # Selbsttest zu teuer, die Messwerte stehen im Kommentar.
    def _ofdm_probe(rng30, fs30, n_traeger, abstand_hz, cp, dauer_s, snr_db=25.0):
        n_u = int(round(fs30 / abstand_hz))
        n_cp = int(round(n_u * cp))
        anzahl = int(dauer_s * fs30 / (n_u + n_cp))
        aktiv = np.arange(-n_traeger // 2, n_traeger // 2)
        stuecke = []
        for _ in range(anzahl):
            X = np.zeros(n_u, dtype=complex)
            X[aktiv] = ((rng30.integers(0, 4, len(aktiv)) * 2 - 3)
                        + 1j * (rng30.integers(0, 4, len(aktiv)) * 2 - 3))
            x = np.fft.ifft(X) * n_u / math.sqrt(len(aktiv))
            stuecke.append(np.concatenate([x[-n_cp:], x]))
        z30 = np.concatenate(stuecke)
        z30 = z30 / math.sqrt(float(np.mean(np.abs(z30) ** 2)))
        p30 = 10 ** (-snr_db / 10.0)
        return z30 + math.sqrt(p30 / 2) * (rng30.normal(size=len(z30))
                                            + 1j * rng30.normal(size=len(z30)))

    fs30 = 48000
    for abstand30, traeger30, cp30 in ((45.0, 48, 0.25), (100.0, 32, 0.2),
                                        (200.0, 24, 0.25), (400.0, 16, 0.25)):
        z30 = _ofdm_probe(np.random.default_rng(100), fs30, traeger30, abstand30, cp30, 2.0)
        h30 = ofdm_hinweis(z30, fs30)
        check(h30 is not None, f"T30: OFDM {abstand30} Hz wird erkannt")
        if h30:
            fehler30 = abs(h30["symbol_s"] - 1.0 / abstand30) * abstand30
            check(fehler30 < 0.05,
                  f"T30: Symboldauer bei {abstand30} Hz auf {fehler30 * 100:.1f} % genau")
            check(h30["kurtosis"] is not None, f"T30: Kurtosis bei {abstand30} Hz geliefert")

    # Keine Fehlalarme - das ist wichtiger als jeder Treffer. Ein
    # "moeglicherweise OFDM" ueber einem leeren Band waere schlimmer als gar
    # keine Aussage, deshalb ist der Autokorrelationsgipfel notwendige
    # Bedingung und die beiden anderen Merkmale nur Bestaetigung.
    rng30 = np.random.default_rng(200)
    t30 = np.arange(fs30 * 2) / fs30
    rausch30 = (rng30.normal(size=len(t30)) + 1j * rng30.normal(size=len(t30))) / math.sqrt(2)
    check(ofdm_hinweis(rausch30, fs30) is None, "T30: Rauschen -> kein Hinweis")
    check(ofdm_hinweis(np.exp(2j * np.pi * 3000 * t30) + 0.05 * rausch30, fs30) is None,
          "T30: Dauertraeger -> kein Hinweis")
    # FSK2 loest den Hinweis AUS - gemessen 2026-09-11 bei 48 kHz und 100 Bd
    # meldet er 0,98 ms, also nicht einmal die Symboldauer (10 ms), sondern
    # einen Nebengipfel. Das ist eine echte Schwaeche der Kennzahl und wird
    # hier festgehalten statt wegdefiniert. Sie ist hinnehmbar, WEIL der
    # Hinweis nur angeboten wird, wenn FSK und PSK beide gescheitert sind -
    # bei einem auswertbaren FSK-Signal kommt er nie zum Zug. Sollte sich das
    # aendern, faellt es hier auf.
    fsk30 = _make_fsk_signal(fs30, 100.0, (-500.0, 500.0), 2.0, 0.0,
                              np.random.default_rng(201))
    h_fsk30 = ofdm_hinweis(fsk30, fs30)
    check(h_fsk30 is None or abs(h_fsk30["symbol_s"] - 0.01) > 0.002,
          "T30: FSK2 - der Hinweis trifft jedenfalls nicht die Symboldauer")
    check(ofdm_hinweis(np.zeros(1000, dtype=complex), fs30) is None,
          "T30: leeres Signal -> kein Hinweis")

    # --- T31 (T-1701): Modulationsindex und Tonabstaende ---------------------
    # tone_metrics() ist reine Rechnung aus bereits vorhandenen Werten - die
    # ersten vier Faelle pruefen sie direkt, ohne WAV-Datei.
    tm31a = tone_metrics([-4500.0, 4500.0], 4500.0, 1200.0)
    check(abs(tm31a["mod_index"] - 7.5) < 1e-9, f"T31a: mod_index = 7.5 ({tm31a['mod_index']})")
    check(tm31a["tone_spacings_hz"] == [9000.0],
          f"T31a: ein Tonabstand von 9000 Hz ({tm31a['tone_spacings_hz']})")
    check(tm31a["tone_spacing_mean_hz"] == 9000.0, f"T31a: Mittel 9000 Hz ({tm31a['tone_spacing_mean_hz']})")
    check(tm31a["tone_spacing_cv"] == 0.0, f"T31a: Streuung exakt 0 bei einem Abstand ({tm31a['tone_spacing_cv']})")

    tm31b = tone_metrics([-3000.0, -1000.0, 1000.0, 3000.0], 3000.0, 1200.0)
    check(tm31b["tone_spacings_hz"] == [2000.0, 2000.0, 2000.0],
          f"T31b: drei gleiche Abstaende von 2000 Hz ({tm31b['tone_spacings_hz']})")
    check(tm31b["tone_spacing_cv"] == 0.0,
          f"T31b: Streuung exakt 0 bei Gleichabstand ({tm31b['tone_spacing_cv']})")

    tm31c = tone_metrics([-3000.0, -1000.0, 1000.0, 5000.0], 5000.0, 1200.0)
    check(tm31c["tone_spacing_cv"] > 0.1,
          f"T31c: Streuung > 0.1 bei Ungleichabstand ({tm31c['tone_spacing_cv']:.4f})")

    tm31d = tone_metrics([-4500.0, 4500.0], 4500.0, 0.0)
    check(tm31d["mod_index"] is None, "T31d: baud=0 -> mod_index ist None statt ZeroDivisionError")

    # T31e: Ende zu Ende ueber die FSK2-Fixture aus T1 (1200 Bd, +-4500 Hz)
    if r1 is not None:
        check(r1["mod_index"] is not None
              and abs(r1["mod_index"] - 7.5) / 7.5 <= BAUD_TOL_ZUFALLSFOLGE,
              f"T31e: mod_index innerhalb {BAUD_TOL_ZUFALLSFOLGE * 100:.0f}% von 7.5 ({r1['mod_index']})")
        check(r1["tone_spacing_cv"] == 0.0,
              f"T31e: FSK2 hat nur einen Abstand, Streuung 0 ({r1['tone_spacing_cv']})")
    else:
        check(False, "T31e: uebersprungen, T1 ist fehlgeschlagen")

    # T31f: FSK4-Fixture wie T3 (gleiche Parameter: 2400 Bd, +-1944/+-5832 Hz).
    # Eigener Lauf mit eigenem Temp-Verzeichnis statt t3_path/r3 wiederzu-
    # verwenden - deren Temp-Verzeichnis ist zu diesem Zeitpunkt im Selbsttest
    # laengst geschlossen (bzw. r3 undefiniert, wenn T3 selbst fehlschlaegt).
    try:
        with tempfile.TemporaryDirectory() as tmp31f:
            rng31f = np.random.default_rng(303)
            d31f = 1944.0
            sig31f = _make_fsk_signal(fs, 2400, (-3 * d31f, -d31f, d31f, 3 * d31f), 2.0, 0.0, rng31f)
            path31f = _write_temp_wav(tmp31f, "t31f.wav", sig31f, fs)
            r31f = analyze_file(path31f)
        check(len(r31f["tone_spacings_hz"]) == 3,
              f"T31f: drei Tonabstaende bei FSK4 ({len(r31f['tone_spacings_hz'])})")
        check(r31f["tone_spacing_cv"] < 0.15,
              f"T31f: Streuung < 15% bei nahezu gleichabstaendigem FSK4 ({r31f['tone_spacing_cv']:.4f})")
    except Exception as exc:
        check(False, f"T31f: unerwarteter Fehler ({exc})")

    # T31g: Regressionsschutz - alle bisherigen Schluessel des Ergebnis-dicts
    # bleiben vorhanden. Hart hinterlegt, damit ein Wegfall hier auffaellt und
    # nicht erst als kaputte Wertetafel in :8070.
    if r1 is not None:
        erwartete_schluessel = {
            "wav_path", "fs_hz", "fs_eff_hz", "decimate", "min_baud", "lag_limit_samples",
            "freq_hz", "mode", "contrast_db", "flatness", "bursts", "burst_durations_s",
            "chosen_burst_index", "chosen_burst_duration_s", "peak_freqs_hz", "offset_hz",
            "dev_hz", "hub_label", "baud", "baud_override_used", "T_fit", "T_samples", "r2",
            "symbolrate_case", "standard_baud", "phase_samples", "eye_opening_hz",
            "eye_opening_pct", "jitter_pct", "level_count", "level_hint", "traces",
            "n_symbols_used",
        }
        fehlend = erwartete_schluessel - set(r1.keys())
        check(not fehlend, f"T31g: keine bisherigen Schluessel weggefallen (fehlend: {fehlend})")
    else:
        check(False, "T31g: uebersprungen, T1 ist fehlgeschlagen")

    # --- T32 (T-1701a): freq_bin_hz ------------------------------------------
    # Eigener Lauf mit decimate=8 bei fs=32000 -> fs_eff=4000 (derselbe Wert
    # wie in der T-1701a-Erprobung an einer realen Aufnahme), damit die Formel
    # (fs_eff/2)/N_ZERO_HIST_BINS direkt am Ergebnis-dict geprueft wird statt
    # nur am freistehenden Ausdruck.
    try:
        with tempfile.TemporaryDirectory() as tmp32:
            rng32 = np.random.default_rng(3200)
            sig32 = _make_fsk_signal(fs, 50.0, (-225.0, 225.0), 60.0, 0.0, rng32, snr_db=20.0)
            path32 = _write_temp_wav(tmp32, "t32.wav", sig32, fs)
            r32 = analyze_file(path32, decimate=8, min_baud=20.0)
        check(abs(r32["fs_eff_hz"] - 4000.0) < 1e-6,
              f"T32: fs_eff=4000 Hz wie erwartet ({r32['fs_eff_hz']})")
        check(abs(r32["freq_bin_hz"] - 9.95) < 0.01,
              f"T32: freq_bin_hz bei fs_eff=4000 auf 0.01 Hz genau 9.95 ({r32['freq_bin_hz']:.4f})")
    except Exception as exc:
        check(False, f"T32: unerwarteter Fehler ({exc})")

    # freq_bin_hz muss auch in den bestehenden Ende-zu-Ende-Fixtures stehen
    # und positiv sein (Regressionsschutz).
    if r1 is not None:
        check(r1["freq_bin_hz"] > 0,
              f"T32: freq_bin_hz in T1 vorhanden und positiv ({r1['freq_bin_hz']})")
    else:
        check(False, "T32: uebersprungen, T1 ist fehlgeschlagen")

    # --- T33 (T-1709): Nachdetektionsfilter ----------------------------------
    # 1. Fensterlaenge: bei der Mindestforderung von vier Samples je Symbol
    #    filtert POSTDET_FILTER_REL nicht (W=1), bei T=81.23 (reale Erprobung)
    #    ergibt sich W=round(rel*T). W=1 heisst f_det ist elementweise
    #    identisch mit f_korr (moving_average() mit window<=1).
    w_t4 = max(1, round(POSTDET_FILTER_REL * 4.0))
    check(w_t4 == 1, f"T33: T=4 Samples -> W=1 ({w_t4})")
    w_real = max(1, round(POSTDET_FILTER_REL * 81.23))
    check(w_real == round(POSTDET_FILTER_REL * 81.23),
          f"T33: T=81.23 -> W=round(rel*T) ({w_real})")
    f_test33 = np.array([1.0, -2.0, 3.0, -4.0, 5.0])
    check(np.array_equal(moving_average(f_test33, 1, circular=False), f_test33),
          "T33: W=1 -> f_det elementweise identisch mit f_korr")

    # 2. Gewinn bei hoher Ueberabtastung: dieselbe Fixture wie im Kommentar an
    # POSTDET_FILTER_REL - T11-Signalpfad (fs=32000, 50 Bd, Hub 225 Hz), aber
    # per --decimate 8 auf fs_eff=4000 gebracht (SNR=10 dB, der reale
    # Ueberabtastungsfall) UND baud_override=50.0, damit die Baudschaetzung
    # nicht als zweite Stoergroesse in den Vergleich hineinspielt - die
    # Aufgabe misst die Wirkung des Nachdetektionsfilters, nicht der
    # Autokorrelation.
    rng33b = np.random.default_rng(9000)
    sig33b = _make_fsk_signal(32000, 50.0, (-225.0, 225.0), 60.0, 0.0, rng33b, snr_db=10.0)
    with tempfile.TemporaryDirectory() as tmp33b:
        path33b = _write_temp_wav(tmp33b, "t33b.wav", sig33b, 32000)
        r33b_ohne = analyze_file(path33b, decimate=8, min_baud=20.0, baud_override=50.0,
                                  postdet_filter_rel=0.0)
        r33b_mit = analyze_file(path33b, decimate=8, min_baud=20.0, baud_override=50.0,
                                 postdet_filter_rel=POSTDET_FILTER_REL)
    print(f"  T33b: ohne Filter eye={r33b_ohne['eye_opening_pct']:.2f}% jitter={r33b_ohne['jitter_pct']:.2f}%  "
          f"mit Filter eye={r33b_mit['eye_opening_pct']:.2f}% jitter={r33b_mit['jitter_pct']:.2f}%")
    check(r33b_mit["eye_opening_pct"] > r33b_ohne["eye_opening_pct"] + 15.0,
          f"T33b: Augenoeffnung deutlich groesser mit Filter "
          f"({r33b_ohne['eye_opening_pct']:.2f}% -> {r33b_mit['eye_opening_pct']:.2f}%)")
    # Jitter wird bei dieser stark verrauschten Ueberabtastungs-Fixture NICHT
    # kleiner, sondern reproduzierbar GROESSER (Befund T-1709, siehe Kommentar
    # an POSTDET_FILTER_REL - ueber zehn Seeds bestaetigt, ebenso an der
    # echten Aufnahme). Bewusst KEINE Erwartung "kleiner": das waere eine
    # erfundene Verbesserung. Stattdessen nur ein plausibler Wertebereich,
    # damit ein spaeterer Regressionsbruch trotzdem auffaellt.
    check(0.0 <= r33b_mit["jitter_pct"] <= 100.0,
          f"T33b: Jitter bleibt ein plausibler Prozentwert ({r33b_mit['jitter_pct']:.2f}%)")

    # 3. Kein erfundenes Auge: reines Rauschen auf demselben Weg wie Fixture B
    # (fs=32000, --decimate 8 -> fs_eff=4000), T/Hub-Referenzskala 50 Bd /
    # 225 Hz - die wichtigste Pruefung der Aufgabe.
    rng33n = np.random.default_rng(404)
    n33n = int(60.0 * 32000)
    noise33n = (rng33n.normal(0, 1, n33n) + 1j * rng33n.normal(0, 1, n33n)) / math.sqrt(2)
    z_eff33n, fs_eff33n = apply_decimation(noise33n, 32000, 8, 20.0)
    f_korr33n = instantaneous_freq_hz(z_eff33n, fs_eff33n)
    T33n = fs_eff33n / 50.0
    w33n = max(1, round(POSTDET_FILTER_REL * T33n))
    f_det33n = moving_average(f_korr33n, w33n, circular=False)
    phase33n = find_clock_phase(f_det33n, T33n)
    idx_all33n = np.arange(len(f_det33n))
    centers33n = np.arange(phase33n, len(f_det33n) - 1, T33n)
    valid33n = (centers33n - T33n >= 0) & (centers33n + T33n <= len(f_det33n) - 1)
    centers33n = centers33n[valid33n]
    values33n = np.interp(centers33n, idx_all33n, f_det33n) if len(centers33n) else np.array([])
    _, opening_pct33n = eye_opening(values33n, 225.0)
    check(opening_pct33n < 5.0,
          f"T33: Rauschfixture erzeugt kein brauchbares Auge ({opening_pct33n:.3f}% < 5%)")

    # 4. Abschottung: alles, was eine gemessene Schwelle traegt, bleibt
    # bitgleich zwischen gefiltert und ungefiltert (dieselbe Fixture wie 2.).
    for feld in ("contrast_db", "flatness", "offset_hz", "dev_hz", "baud", "r2", "mode"):
        check(r33b_ohne[feld] == r33b_mit[feld],
              f"T33: {feld} bitgleich mit/ohne Filter ({r33b_ohne[feld]!r})")
    check(r33b_ohne["peak_freqs_hz"] == r33b_mit["peak_freqs_hz"],
          f"T33: peak_freqs_hz bitgleich mit/ohne Filter ({r33b_ohne['peak_freqs_hz']!r})")

    # 5. Kennwert vorhanden (Regressionsschutz).
    if r1 is not None:
        check(r1["postdet_window_samples"] >= 1,
              f"T33: postdet_window_samples in T1 vorhanden und >=1 ({r1['postdet_window_samples']})")
    else:
        check(False, "T33: uebersprungen, T1 ist fehlgeschlagen")

    # --- T34 (T-1710): Nulldurchgangsraster -----------------------------------
    # Fixtur: asynchron gerahmter Bitstrom (ITA2-aehnlich, _make_async_frames)
    # auf Halbbit-Raster, 50 Bd, 425 Hz Hub (= dev_hz), 20 dB SNR, --decimate 8
    # (fs=32000 -> fs_eff=4000, T=80 Samples/Symbol - wie Fixture B in T-1709).
    # baud_override=50.0 noetig: die feste Start-/Stoppbit-Wiederholung alle
    # 7-8 Bit praegt der Autokorrelation ein eigenes Muster auf, das die
    # Flankenanpassung (estimate_symbol_duration) ohne Vorgabe scheitern
    # laesst - anders als bei einer echten Aufnahme mit unregelmaessigem
    # Zeicheninhalt (T-1703-Erprobung, dort ohne baud_override erfolgreich).
    T34_FS = 32000
    T34_BAUD = 50.0
    T34_DEV_HZ = 425.0
    T34_LEVELS = (-T34_DEV_HZ, T34_DEV_HZ)
    T34_N_FRAMES = 400
    T34_DATA_BITS = 5

    def _t34_signal(stop_bits, seed, snr_db=20.0):
        rng = np.random.default_rng(seed)
        halfbits = _make_async_frames(T34_N_FRAMES, data_bits=T34_DATA_BITS, stop_bits=stop_bits, rng=rng)
        sig = _make_fsk_signal_from_symbols(T34_FS, 2.0 * T34_BAUD, T34_LEVELS, halfbits, 0.0, rng, snr_db=snr_db)
        return sig, rng

    def _t34_analyze(sig, name, tmpdir):
        path = _write_temp_wav(tmpdir, name, sig, T34_FS)
        return analyze_file(path, decimate=8, min_baud=20.0, baud_override=T34_BAUD)

    r_t34_1 = None
    with tempfile.TemporaryDirectory() as tmp34:
        # 1. 1,0 Stoppbit -> Rahmenlaenge 7 T (ganzzahlig) -> "full"
        try:
            sig_t34_1, _ = _t34_signal(1.0, 9101)
            r_t34_1 = _t34_analyze(sig_t34_1, "t34_1.wav", tmp34)
            print(f"  T34.1: peaks={r_t34_1['crossing_peaks']} grid={r_t34_1['crossing_grid']} "
                  f"conc={r_t34_1['crossing_concentration']:.3f}")
            check(r_t34_1["crossing_grid"] == "full", f"T34.1: full erkannt ({r_t34_1['crossing_grid']})")
            check(len(r_t34_1["crossing_peaks"]) == 1, f"T34.1: genau ein Peak ({r_t34_1['crossing_peaks']})")
            check(r_t34_1["jitter_half_pct"] is None, "T34.1: jitter_half_pct ist None bei full")
        except Exception as exc:
            check(False, f"T34.1: unerwarteter Fehler ({exc})")

        # 2. 2,0 Stoppbits -> Rahmenlaenge 8 T (ganzzahlig) -> ebenfalls "full"
        try:
            sig_t34_2, _ = _t34_signal(2.0, 9201)
            r_t34_2 = _t34_analyze(sig_t34_2, "t34_2.wav", tmp34)
            check(r_t34_2["crossing_grid"] == "full",
                  f"T34.2: full erkannt bei 2,0 Stoppbits ({r_t34_2['crossing_grid']})")
            check(len(r_t34_2["crossing_peaks"]) == 1, f"T34.2: genau ein Peak ({r_t34_2['crossing_peaks']})")
        except Exception as exc:
            check(False, f"T34.2: unerwarteter Fehler ({exc})")

        # 3. 1,5 Stoppbits -> Rahmenlaenge 7,5 T (nicht ganzzahlig) -> "half"
        try:
            sig_t34_3, _ = _t34_signal(1.5, 9301)
            r_t34_3 = _t34_analyze(sig_t34_3, "t34_3.wav", tmp34)
            peaks_t34_3 = r_t34_3["crossing_peaks"]
            print(f"  T34.3: peaks={peaks_t34_3} grid={r_t34_3['crossing_grid']} "
                  f"jitter={r_t34_3['jitter_pct']:.2f}% jitter_half={r_t34_3['jitter_half_pct']}")
            check(r_t34_3["crossing_grid"] == "half", f"T34.3: half erkannt ({r_t34_3['crossing_grid']})")
            check(len(peaks_t34_3) == 2, f"T34.3: genau zwei Peaks ({peaks_t34_3})")
            if len(peaks_t34_3) == 2:
                d_t34_3 = abs(peaks_t34_3[0] - peaks_t34_3[1])
                cyclic_t34_3 = min(d_t34_3, 1.0 - d_t34_3)
                check(abs(cyclic_t34_3 - 0.5) <= CROSSING_HALF_TOL,
                      f"T34.3: Peakabstand nahe 0,5 ({cyclic_t34_3:.4f})")
            check(r_t34_3["jitter_half_pct"] is not None
                  and r_t34_3["jitter_half_pct"] < r_t34_3["jitter_pct"] / 10.0,
                  f"T34.3: Jitter gegen Halbbit-Raster deutlich kleiner "
                  f"({r_t34_3['jitter_pct']:.2f}% -> {r_t34_3['jitter_half_pct']})")
        except Exception as exc:
            check(False, f"T34.3: unerwarteter Fehler ({exc})")

        # 4a. Statischer Zweiwegekanal (0,3 T Verzoegerung, 0,6 Amplitude,
        # feste Phase) - NEGATIVBEFUND (siehe Entscheidungslog PLAN.md
        # T-1710): gemessen ueber acht Seeds und die relativen Phasen
        # 0/45/90/135/180 Grad verschiebt sich nur die Peaklage, die
        # Konzentration bleibt bei 1,000. Ein statischer Kanal mit nur einem
        # Symbol Erinnerung (Verzoegerung < T) erzeugt bei einem binaeren,
        # symmetrischen FSK-Signal fuer JEDEN Uebergang dieselbe Interferenz-
        # geometrie - das verschiebt den Nulldurchgang deterministisch, ohne
        # ihn zu verschmieren. Ein einzelner verschobener Peak ist demnach
        # KEIN Hinweis auf einen gestoerten Kanal.
        try:
            delay_t34 = int(round(0.3 * T34_FS / T34_BAUD))
            pad_t34 = np.zeros(delay_t34, dtype=complex)
            sig_t34_clean, rng_t34_4a = _t34_signal(1.0, 9101, snr_db=200.0)
            delayed_t34 = np.concatenate([pad_t34, sig_t34_clean])[:len(sig_t34_clean)]
            multipath_t34 = sig_t34_clean + 0.6 * delayed_t34
            leistung_t34 = float(np.mean(np.abs(multipath_t34) ** 2))
            rauschleistung_t34 = leistung_t34 / (10.0 ** (20.0 / 10.0))
            rauschen_t34 = (rng_t34_4a.normal(0, 1, len(multipath_t34))
                            + 1j * rng_t34_4a.normal(0, 1, len(multipath_t34))) * math.sqrt(rauschleistung_t34 / 2.0)
            r_t34_4a = _t34_analyze(multipath_t34 + rauschen_t34, "t34_4a.wav", tmp34)
            print(f"  T34.4a (statischer Mehrweg): peaks={r_t34_4a['crossing_peaks']} "
                  f"conc={r_t34_4a['crossing_concentration']:.3f} "
                  f"(Fall 1: peaks={r_t34_1['crossing_peaks']} conc={r_t34_1['crossing_concentration']:.3f})")
            if r_t34_1 is not None:
                check(r_t34_4a["crossing_peaks"] != r_t34_1["crossing_peaks"],
                      "T34.4a: Peaklage verschiebt sich gegenueber Fall 1")
            check(r_t34_4a["crossing_concentration"] >= 0.95,
                  f"T34.4a: Konzentration bleibt trotz Mehrweg hoch, Negativbefund "
                  f"({r_t34_4a['crossing_concentration']:.3f})")
        except Exception as exc:
            check(False, f"T34.4a: unerwarteter Fehler ({exc})")

        # 4b. Zeitvarianter Zweiwegekanal (Doppler auf dem verzoegerten Pfad,
        # exp(2j*pi*f_d*t)) - ebenfalls NEGATIVBEFUND: Reihe f_d in
        # {0; 0,1; 0,5; 2,0} Hz (0 Hz = Anker, identisch zu 4a), je drei
        # Seeds. Gemessen (PLAN.md T-1710): die Konzentration faellt auch bei
        # 2,0 Hz nicht messbar unter den Fall-1-Wert. crossing_concentration
        # trennt damit "stabiles Raster" von "zeitveraenderlichem Kanal",
        # nicht allgemein "diskret" von "verschmiert" - kein Grund, die
        # Parameter weiter zu drehen. Die Einstufung full/half/unclear haengt
        # an den Peaks, nicht an der Konzentration, und ist davon unberuehrt.
        try:
            konz_t34_4b = []
            for fd_t34 in (0.0, 0.1, 0.5, 2.0):
                werte_t34_4b = []
                for seed_off_t34 in range(3):
                    sig_c_t34, rng_t34_4b = _t34_signal(1.0, 9400 + seed_off_t34, snr_db=200.0)
                    delayed_t34_4b = np.concatenate([pad_t34, sig_c_t34])[:len(sig_c_t34)]
                    t_t34_4b = np.arange(len(sig_c_t34)) / T34_FS
                    doppler_t34_4b = np.exp(1j * 2.0 * math.pi * fd_t34 * t_t34_4b)
                    multi_t34_4b = sig_c_t34 + 0.6 * delayed_t34_4b * doppler_t34_4b
                    leist_t34_4b = float(np.mean(np.abs(multi_t34_4b) ** 2))
                    rausch_p_t34_4b = leist_t34_4b / (10.0 ** (20.0 / 10.0))
                    rausch_t34_4b = (rng_t34_4b.normal(0, 1, len(multi_t34_4b))
                                     + 1j * rng_t34_4b.normal(0, 1, len(multi_t34_4b))) \
                        * math.sqrt(rausch_p_t34_4b / 2.0)
                    r_t34_4b = _t34_analyze(multi_t34_4b + rausch_t34_4b, f"t34_4b_{fd_t34}_{seed_off_t34}.wav", tmp34)
                    werte_t34_4b.append(r_t34_4b["crossing_concentration"])
                konz_t34_4b.append((fd_t34, werte_t34_4b))
                print(f"  T34.4b: f_d={fd_t34} Hz -> Konzentration {werte_t34_4b}")
            check(all(v >= 0.95 for _, werte in konz_t34_4b for v in werte),
                  "T34.4b: Konzentration bleibt bei allen getesteten f_d >= 0,95 "
                  "(Negativbefund, siehe Kommentar an crossing_grid)")
        except Exception as exc:
            check(False, f"T34.4b: unerwarteter Fehler ({exc})")

        # 5. Rauschen -> "unclear", keine Deutung. Muster wie der T33-
        # Rauschencheck: direkt ueber crossing_grid(), detect_bursts() wuerde
        # reines Rauschen ohnehin ablehnen (RuntimeError).
        try:
            rng_t34_5 = np.random.default_rng(9500)
            n_t34_5 = int(60.0 * T34_FS)
            rauschen_t34_5 = (rng_t34_5.normal(0, 1, n_t34_5)
                              + 1j * rng_t34_5.normal(0, 1, n_t34_5)) / math.sqrt(2.0)
            z_eff_t34_5, fs_eff_t34_5 = apply_decimation(rauschen_t34_5, T34_FS, 8, 20.0)
            f_korr_t34_5 = instantaneous_freq_hz(z_eff_t34_5, fs_eff_t34_5)
            T_t34_5 = fs_eff_t34_5 / T34_BAUD
            w_t34_5 = max(1, round(POSTDET_FILTER_REL * T_t34_5))
            f_det_t34_5 = moving_average(f_korr_t34_5, w_t34_5, circular=False)
            positions_t34_5 = zero_crossings(f_det_t34_5)
            phase_t34_5 = find_clock_phase(f_det_t34_5, T_t34_5)
            r_t34_5 = crossing_grid(positions_t34_5, T_t34_5, phase_t34_5)
            check(r_t34_5["crossing_grid"] == "unclear",
                  f"T34.5: Rauschen -> unclear ({r_t34_5['crossing_grid']}, peaks={r_t34_5['crossing_peaks']})")
        except Exception as exc:
            check(False, f"T34.5: unerwarteter Fehler ({exc})")

        # 6. Abschottung: dev_hz/offset_hz/baud/jitter_pct/eye_opening_pct/
        # mode_candidates unveraendert mit und ohne crossing_grid - manuelle
        # Parallelrechnung ueber dieselben oeffentlichen Bausteine wie
        # analyze_file(), aber OHNE den crossing_grid()-Aufruf (Fixtur aus
        # T34.1).
        try:
            if r_t34_1 is None:
                raise RuntimeError("T34.1 fehlgeschlagen, Fixtur fehlt")
            path_t34_6 = _write_temp_wav(tmp34, "t34_6.wav", sig_t34_1, T34_FS)
            fr_t34_6, z_t34_6 = read_wav_iq(path_t34_6)
            sidecar_t34_6 = read_sidecar(path_t34_6)
            fs_t34_6 = resolve_fs(fr_t34_6, sidecar_t34_6)
            z_eff_t34_6, fs_eff_t34_6 = apply_decimation(z_t34_6, fs_t34_6, 8, 20.0)
            bursts_t34_6, _, _, _, f_full_t34_6 = detect_bursts(z_eff_t34_6, fs_eff_t34_6)
            chosen_t34_6 = max(range(len(bursts_t34_6)), key=lambda i: bursts_t34_6[i][1] - bursts_t34_6[i][0])
            start_t34_6, end_t34_6 = bursts_t34_6[chosen_t34_6]
            f_burst_t34_6 = f_full_t34_6[start_t34_6:end_t34_6 - 1]
            offset_t34_6, dev_t34_6, peaks_t34_6, f_korr_t34_6 = freq_offset_and_deviation(f_burst_t34_6, fs_eff_t34_6)
            baud_t34_6 = T34_BAUD
            T_t34_6 = fs_eff_t34_6 / baud_t34_6
            tones_t34_6 = tone_metrics(peaks_t34_6, dev_t34_6, baud_t34_6)
            w_t34_6 = max(1, round(POSTDET_FILTER_REL * T_t34_6))
            f_det_t34_6 = moving_average(f_korr_t34_6, w_t34_6, circular=False)
            positions_t34_6 = zero_crossings(f_det_t34_6)
            phase_t34_6 = find_clock_phase(f_det_t34_6, T_t34_6)
            n_t34_6 = len(f_det_t34_6)
            idx_t34_6 = np.arange(n_t34_6)
            centers_t34_6 = np.arange(phase_t34_6, n_t34_6 - 1, T_t34_6)
            valid_t34_6 = (centers_t34_6 - T_t34_6 >= 0) & (centers_t34_6 + T_t34_6 <= n_t34_6 - 1)
            centers_t34_6 = centers_t34_6[valid_t34_6]
            values_t34_6 = np.interp(centers_t34_6, idx_t34_6, f_det_t34_6) if len(centers_t34_6) else np.array([])
            _, opening_pct_t34_6 = eye_opening(values_t34_6, dev_t34_6)
            jitter_pct_t34_6 = jitter_percent(positions_t34_6, T_t34_6)
            level_count_t34_6 = len(peaks_t34_6)
            hub_label_t34_6 = "Hub" if level_count_t34_6 == 2 else "Hub (aeusserer Pegelabstand)"
            result_t34_6 = {
                "dev_hz": dev_t34_6, "offset_hz": offset_t34_6, "baud": baud_t34_6,
                "peak_freqs_hz": peaks_t34_6, "hub_label": hub_label_t34_6,
                "mod_index": tones_t34_6["mod_index"], "tone_spacings_hz": tones_t34_6["tone_spacings_hz"],
                "tone_spacing_mean_hz": tones_t34_6["tone_spacing_mean_hz"],
                "tone_spacing_cv": tones_t34_6["tone_spacing_cv"], "level_count": level_count_t34_6,
            }
            mode_candidates_t34_6 = _mode_candidates_for(result_t34_6)
            check(dev_t34_6 == r_t34_1["dev_hz"], f"T34.6: dev_hz bitgleich ({dev_t34_6} == {r_t34_1['dev_hz']})")
            check(offset_t34_6 == r_t34_1["offset_hz"],
                  f"T34.6: offset_hz bitgleich ({offset_t34_6} == {r_t34_1['offset_hz']})")
            check(baud_t34_6 == r_t34_1["baud"], f"T34.6: baud bitgleich ({baud_t34_6} == {r_t34_1['baud']})")
            check(jitter_pct_t34_6 == r_t34_1["jitter_pct"],
                  f"T34.6: jitter_pct bitgleich ({jitter_pct_t34_6} == {r_t34_1['jitter_pct']})")
            check(opening_pct_t34_6 == r_t34_1["eye_opening_pct"],
                  f"T34.6: eye_opening_pct bitgleich ({opening_pct_t34_6} == {r_t34_1['eye_opening_pct']})")
            check(mode_candidates_t34_6 == r_t34_1["mode_candidates"], "T34.6: mode_candidates bitgleich")
        except Exception as exc:
            check(False, f"T34.6: unerwarteter Fehler ({exc})")

    # --- T35 (T-1711): Feinabgleich der Symbolrate ----------------------------
    # 1. Genauigkeit: synthetisches FSK2 bei 50/100/1200 Bd, je drei Seeds.
    # baud (nach Feinabgleich) muss auf 0,1 % genau sein, baud_coarse darf wie
    # bisher bis BAUD_TOL_ZUFALLSFOLGE (6 %) abweichen - Identifikation (grob)
    # gegen Ausrichtung (fein) ist die Kernzusage der Aufgabe.
    t35_faelle = (
        (50.0, 200.0, 20.0, 8, 15.0),
        (100.0, 400.0, 10.0, 8, 15.0),
        (1200.0, 4500.0, 2.0, 1, 20.0),
    )
    for baud35, dev35, dur35, decim35, snr35 in t35_faelle:
        try:
            err_fein_max = 0.0
            err_grob_max = 0.0
            n_abgeglichen = 0
            with tempfile.TemporaryDirectory() as tmp35a:
                for seed35 in range(3):
                    rng35 = np.random.default_rng(15000 + int(baud35) + seed35)
                    sig35 = _make_fsk_signal(32000, baud35, (-dev35, dev35), dur35, 0.0, rng35, snr_db=snr35)
                    path35 = _write_temp_wav(tmp35a, f"t35a_{baud35}_{seed35}.wav", sig35, 32000)
                    r35 = analyze_file(path35, decimate=decim35, min_baud=20.0)
                    err_fein_max = max(err_fein_max, abs(r35["baud"] - baud35) / baud35)
                    err_grob_max = max(err_grob_max, abs(r35["baud_coarse"] - baud35) / baud35)
                    if r35["baud_refined"]:
                        n_abgeglichen += 1
            print(f"  T35.1: {baud35:.0f} Bd - Fehler fein max {err_fein_max * 100.0:.4f} %, "
                  f"Fehler grob max {err_grob_max * 100.0:.2f} %, feinabgeglichen {n_abgeglichen}/3")
            check(n_abgeglichen == 3, f"T35.1: {baud35:.0f} Bd - Feinabgleich in allen drei Seeds uebernommen")
            check(err_fein_max <= 0.001,
                  f"T35.1: {baud35:.0f} Bd - Fehler fein <= 0,1 % ({err_fein_max * 100.0:.4f} %)")
            check(err_grob_max <= BAUD_TOL_ZUFALLSFOLGE,
                  f"T35.1: {baud35:.0f} Bd - Fehler grob <= {BAUD_TOL_ZUFALLSFOLGE * 100:.0f} % "
                  f"({err_grob_max * 100.0:.2f} %)")
        except Exception as exc:
            check(False, f"T35.1: {baud35:.0f} Bd - unerwarteter Fehler ({exc})")

    # 2. Halbbit-Raster: Regressionsschutz fuer die zweite Harmonische - mit
    # h=1 muss der 1,5-Stoppbit-Fall (Fixtur aus T-1710) nachweislich
    # scheitern (R1~0, die zwei um T/2 versetzten Populationen loeschen sich
    # aus), mit h=2 (wie in refine_symbol_rate() verwendet) gelingt der
    # Feinabgleich genauso gut wie beim 1,0-Stoppbit-Fall.
    T35_FS = 32000
    T35_BAUD = 50.0
    T35_DEV_HZ = 425.0
    T35_LEVELS = (-T35_DEV_HZ, T35_DEV_HZ)
    T35_N_FRAMES = 400
    T35_DATA_BITS = 5

    def _t35_signal(stop_bits, seed):
        rng = np.random.default_rng(seed)
        halfbits = _make_async_frames(T35_N_FRAMES, data_bits=T35_DATA_BITS, stop_bits=stop_bits, rng=rng)
        return _make_fsk_signal_from_symbols(T35_FS, 2.0 * T35_BAUD, T35_LEVELS, halfbits, 0.0, rng, snr_db=20.0)

    try:
        with tempfile.TemporaryDirectory() as tmp35b:
            for stop_bits35, label35 in ((1.0, "1,0 Stoppbit"), (1.5, "1,5 Stoppbits")):
                sig35b = _t35_signal(stop_bits35, 15101)
                path35b = _write_temp_wav(tmp35b, f"t35b_{stop_bits35}.wav", sig35b, T35_FS)
                # positions/T_coarse ueber dieselben oeffentlichen Bausteine
                # nachgebildet wie in analyze_file() (Muster reproduce_crossings
                # aus tools/tone_probe.py, Messauftrag M2), um H1 gegen H2 am
                # bekannten T direkt zu vergleichen.
                fr35b, z35b = read_wav_iq(path35b)
                sidecar35b = read_sidecar(path35b)
                fs35b = resolve_fs(fr35b, sidecar35b)
                z_eff35b, fs_eff35b = apply_decimation(z35b, fs35b, 8, 20.0)
                bursts35b, _m35b, _c35b, _f35b, f_full35b = detect_bursts(z_eff35b, fs_eff35b)
                chosen35b = max(range(len(bursts35b)), key=lambda i: bursts35b[i][1] - bursts35b[i][0])
                start35b, end35b = bursts35b[chosen35b]
                f_burst35b = f_full35b[start35b:end35b - 1]
                _o35b, _d35b, _p35b, f_korr35b = freq_offset_and_deviation(f_burst35b, fs_eff35b)
                T_coarse35b = fs_eff35b / T35_BAUD
                w35b = max(1, round(POSTDET_FILTER_REL * T_coarse35b))
                f_det35b = moving_average(f_korr35b, w35b, circular=False)
                positions35b = zero_crossings(f_det35b)
                r1_35b = circular_resultant(positions35b, T_coarse35b, harmonic=1)
                r2_35b = circular_resultant(positions35b, T_coarse35b, harmonic=2)
                fein35b = refine_symbol_rate(positions35b, T_coarse35b, len(positions35b) * 2.0)
                print(f"  T35.2 ({label35}): R1(h=1)={r1_35b:.3f}  R2(h=2)={r2_35b:.3f}  "
                      f"Feinabgleich (h=2) accepted={fein35b['accepted']} r2={fein35b['r2']:.3f}")
                if stop_bits35 == 1.0:
                    check(r1_35b > 0.9, f"T35.2 (1,0 Stoppbit): R1 hoch, volles Raster ({r1_35b:.3f})")
                else:
                    check(r1_35b < 0.1,
                          f"T35.2 (1,5 Stoppbits): R1 (h=1) loescht sich aus, nahe 0 ({r1_35b:.3f})")
                check(r2_35b > 0.9, f"T35.2 ({label35}): R2 (h=2) hoch in beiden Faellen ({r2_35b:.3f})")
                check(fein35b["accepted"], f"T35.2 ({label35}): Feinabgleich mit h=2 gelingt")
                check(abs(fein35b["T_fine"] - T_coarse35b) / T_coarse35b < 1e-3,
                      f"T35.2 ({label35}): T_fine nahe an der vorgegebenen Rate")
    except Exception as exc:
        check(False, f"T35.2: unerwarteter Fehler ({exc})")

    # 3. Wirkung: lange, hoch ueberabgetastete Fixtur (T-1709 Fixture B: 50 Bd,
    # Hub 225 Hz, 60 s, decimate 8 -> rund 3000 Symbole) - Augenoeffnung MIT
    # Feinabgleich deutlich hoeher als OHNE (baud_override auf den grob
    # geschaetzten Wert erzwingt denselben Pfad wie vor T-1711). Gemessen ueber
    # fuenf Seeds: eye_mit 95,6-100,3 %, eye_ohne 0,2-0,5 % - die Schranke
    # unten (+50 Punkte) liegt weit unter dem gemessenen Abstand.
    try:
        with tempfile.TemporaryDirectory() as tmp35c:
            rng35c = np.random.default_rng(15300)
            sig35c = _make_fsk_signal(32000, 50.0, (-225.0, 225.0), 60.0, 0.0, rng35c, snr_db=10.0)
            path35c = _write_temp_wav(tmp35c, "t35c.wav", sig35c, 32000)
            r35c_mit = analyze_file(path35c, decimate=8, min_baud=20.0)
            r35c_ohne = analyze_file(path35c, decimate=8, min_baud=20.0, baud_override=r35c_mit["baud_coarse"])
        print(f"  T35.3: n_symbols={r35c_mit['n_symbols_used']}  "
              f"eye_mit={r35c_mit['eye_opening_pct']:.2f}%  eye_ohne={r35c_ohne['eye_opening_pct']:.2f}%")
        check(r35c_mit["n_symbols_used"] >= 800, f"T35.3: mindestens 800 Symbole ({r35c_mit['n_symbols_used']})")
        check(r35c_mit["baud_refined"], "T35.3: Feinabgleich uebernommen")
        check(r35c_mit["eye_opening_pct"] > r35c_ohne["eye_opening_pct"] + 50.0,
              f"T35.3: Augenoeffnung mit Feinabgleich deutlich hoeher "
              f"({r35c_ohne['eye_opening_pct']:.2f}% -> {r35c_mit['eye_opening_pct']:.2f}%)")
    except Exception as exc:
        check(False, f"T35.3: unerwarteter Fehler ({exc})")

    # 4. Ablehnung bei Rauschen: Zufallspositionen (n=200, oberhalb
    # BAUD_FINE_MIN_CROSSINGS), aber ohne jede Periodizitaet. Direkt gegen
    # refine_symbol_rate() geprueft (Muster: die Rauschenchecks in T-1710/
    # T-1709 pruefen ebenfalls direkt gegen die Kernfunktion, nicht ueber
    # analyze_file() - reines Rauschen faellt schon bei detect_bursts durch).
    # R2 bleibt weit unter BAUD_FINE_R2_MIN (siehe Kommentar an den
    # Konstanten: per Zufall erreichbare Obergrenze bei n=200 gemessen 0,262).
    try:
        rng35d = np.random.default_rng(15400)
        positions35d = rng35d.uniform(0, 80000, 200)
        fein35d = refine_symbol_rate(positions35d, 80.0, 1000.0)
        print(f"  T35.4: Rauschen (n={len(positions35d)}) -> accepted={fein35d['accepted']} "
              f"r2={fein35d['r2']:.3f} reject_reason={fein35d['reject_reason']}")
        check(fein35d["accepted"] is False, "T35.4: Feinabgleich bei Rauschen abgelehnt")
        check(fein35d["r2"] is not None and fein35d["r2"] < BAUD_FINE_R2_MIN,
              f"T35.4: R2 unter der Schwelle ({fein35d['r2']:.3f} < {BAUD_FINE_R2_MIN})")
        check(fein35d["reject_reason"] is not None, "T35.4: Ablehnungsgrund gesetzt")
    except Exception as exc:
        check(False, f"T35.4: unerwarteter Fehler ({exc})")

    # 5. Ablehnung bei zu wenigen Nulldurchgaengen: kurzer Burst unter
    # BAUD_FINE_MIN_CROSSINGS - abgelehnt, r2 bleibt None (die Suche laeuft
    # gar nicht erst an), grobe Schaetzung bleibt. Erst direkt gegen
    # refine_symbol_rate(), dann Ende-zu-Ende ueber analyze_file() mit einem
    # 0,5-s-Burst bei 50 Bd (rund 23 Symbole, deutlich unter der Mindestzahl).
    try:
        rng35e = np.random.default_rng(15500)
        positions35e = rng35e.uniform(0, 800, BAUD_FINE_MIN_CROSSINGS - 1)
        fein35e = refine_symbol_rate(positions35e, 80.0, 20.0)
        check(fein35e["accepted"] is False, "T35.5: refine_symbol_rate bei zu wenigen Nulldurchgaengen abgelehnt")
        check(fein35e["r2"] is None, "T35.5: r2 ist None - die Suche lief gar nicht erst an")
        check(fein35e["reject_reason"] == "zu_wenige_nulldurchgaenge", "T35.5: Ablehnungsgrund benannt")
    except Exception as exc:
        check(False, f"T35.5: unerwarteter Fehler ({exc})")

    try:
        with tempfile.TemporaryDirectory() as tmp35e:
            rng35e2 = np.random.default_rng(15501)
            sig35e2 = _make_fsk_signal(32000, 50.0, (-225.0, 225.0), 0.5, 0.0, rng35e2, snr_db=20.0)
            path35e2 = _write_temp_wav(tmp35e, "t35e2.wav", sig35e2, 32000)
            r35e2 = analyze_file(path35e2, decimate=8, min_baud=20.0)
        print(f"  T35.5: kurzer Burst ({r35e2['n_symbols_used']} Symbole) -> "
              f"baud_refined={r35e2['baud_refined']} baud={r35e2['baud']:.3f} "
              f"baud_coarse={r35e2['baud_coarse']:.3f}")
        check(r35e2["baud_refined"] is False, "T35.5: Ende-zu-Ende - kurzer Burst wird nicht feinabgeglichen")
        check(r35e2["baud"] == r35e2["baud_coarse"], "T35.5: Ende-zu-Ende - baud bleibt die grobe Schaetzung")
    except Exception as exc:
        check(False, f"T35.5: unerwarteter Fehler (Ende-zu-Ende, {exc})")

    # 6. Vorgabe gewinnt: mit --baud (baud_override) laeuft kein Feinabgleich.
    try:
        with tempfile.TemporaryDirectory() as tmp35f:
            rng35f = np.random.default_rng(15600)
            sig35f = _make_fsk_signal(32000, 50.0, (-225.0, 225.0), 20.0, 0.0, rng35f, snr_db=15.0)
            path35f = _write_temp_wav(tmp35f, "t35f.wav", sig35f, 32000)
            r35f = analyze_file(path35f, decimate=8, min_baud=20.0, baud_override=123.456)
        check(r35f["baud_fine_r2"] is None, "T35.6: baud_fine_r2 ist None bei --baud")
        check(r35f["baud_refined"] is False, "T35.6: baud_refined ist False bei --baud")
        check(r35f["baud"] == 123.456, f"T35.6: baud ist exakt die Vorgabe ({r35f['baud']})")
    except Exception as exc:
        check(False, f"T35.6: unerwarteter Fehler ({exc})")

    # 7. Abschottung: contrast_db/flatness/offset_hz/dev_hz/peak_freqs_hz/mode
    # sind mit und ohne Feinabgleich bitgleich - der Abgleich sitzt hinter
    # diesen Stufen (Burst-Erkennung, Histogramm-Peaks der Momentanfrequenz).
    try:
        with tempfile.TemporaryDirectory() as tmp35g:
            rng35g = np.random.default_rng(15700)
            sig35g = _make_fsk_signal(32000, 50.0, (-225.0, 225.0), 20.0, 0.0, rng35g, snr_db=15.0)
            path35g = _write_temp_wav(tmp35g, "t35g.wav", sig35g, 32000)
            r35g_mit = analyze_file(path35g, decimate=8, min_baud=20.0)
            r35g_ohne = analyze_file(path35g, decimate=8, min_baud=20.0, baud_override=r35g_mit["baud_coarse"])
        for feld35 in ("contrast_db", "flatness", "offset_hz", "dev_hz", "mode"):
            check(r35g_mit[feld35] == r35g_ohne[feld35],
                  f"T35.7: {feld35} bitgleich mit/ohne Feinabgleich ({r35g_mit[feld35]!r})")
        check(r35g_mit["peak_freqs_hz"] == r35g_ohne["peak_freqs_hz"],
              f"T35.7: peak_freqs_hz bitgleich mit/ohne Feinabgleich ({r35g_mit['peak_freqs_hz']!r})")
    except Exception as exc:
        check(False, f"T35.7: unerwarteter Fehler ({exc})")

    # ------------------------------------------------------------------
    # T36: Asynchrone Rahmenerkennung (T-1715, eb200_core/framing.py)
    # ------------------------------------------------------------------

    # 1. Reine Suche, ohne Signal: _make_async_frames() direkt in
    # detect_async_framing(), keine WAV-Datei noetig (framing.py ist reine
    # stdlib-Funktion, siehe eb200_core/framing.py).
    try:
        rng36a = np.random.default_rng(16100)
        halfbits36a = _make_async_frames(200, data_bits=5, stop_bits=1.5, rng=rng36a)
        f36a = detect_async_framing([int(v) for v in halfbits36a])
        print(f"  T36.1: frame_halfbits={f36a['frame_halfbits']} score={f36a['score']:.3f} "
              f"polarity={f36a['polarity']}")
        check(f36a["accepted"], "T36.1: reine Suche ohne Signal akzeptiert")
        check(f36a["frame_halfbits"] == 15, f"T36.1: Rahmenlaenge 15 Halbbit ({f36a['frame_halfbits']})")
        check(f36a["score"] > 0.99, f"T36.1: Score nahe 1,0 ({f36a['score']:.3f})")
        check(f36a["polarity"] == "normal", f"T36.1: Polaritaet normal ({f36a['polarity']})")
    except Exception as exc:
        check(False, f"T36.1: unerwarteter Fehler ({exc})")

    # 2. Alle drei ITA2-Laengen: 1,0/1,5/2,0 Stoppbits -> 14/15/16. Keine darf
    # mit einer anderen verwechselt werden.
    try:
        laengen36b = set()
        for stop_bits36b, erwartet36b in ((1.0, 14), (1.5, 15), (2.0, 16)):
            rng36b = np.random.default_rng(16200 + erwartet36b)
            halfbits36b = _make_async_frames(200, data_bits=5, stop_bits=stop_bits36b, rng=rng36b)
            f36b = detect_async_framing([int(v) for v in halfbits36b])
            check(f36b["accepted"], f"T36.2: {stop_bits36b} Stoppbits akzeptiert")
            check(f36b["frame_halfbits"] == erwartet36b,
                  f"T36.2: {stop_bits36b} Stoppbits -> {erwartet36b} Halbbit ({f36b['frame_halfbits']})")
            laengen36b.add(f36b["frame_halfbits"])
        check(len(laengen36b) == 3, f"T36.2: alle drei Laengen verschieden ({laengen36b})")
    except Exception as exc:
        check(False, f"T36.2: unerwarteter Fehler ({exc})")

    # 3. ASCII: 7 Datenbits, 1 Stoppbit -> 18 Halbbit.
    try:
        rng36c = np.random.default_rng(16300)
        halfbits36c = _make_async_frames(200, data_bits=7, stop_bits=1.0, rng=rng36c)
        f36c = detect_async_framing([int(v) for v in halfbits36c])
        check(f36c["accepted"], "T36.3: ASCII 7N1 akzeptiert")
        check(f36c["frame_halfbits"] == 18, f"T36.3: Rahmenlaenge 18 Halbbit ({f36c['frame_halfbits']})")
    except Exception as exc:
        check(False, f"T36.3: unerwarteter Fehler ({exc})")

    # 4. Polaritaet: dieselbe Folge invertiert -> gleiche Rahmenlaenge,
    # polarity=="inverted".
    try:
        rng36d = np.random.default_rng(16400)
        halfbits36d = _make_async_frames(200, data_bits=5, stop_bits=1.5, rng=rng36d)
        bits36d_inv = [1 - int(v) for v in halfbits36d]
        f36d = detect_async_framing(bits36d_inv)
        check(f36d["accepted"], "T36.4: invertierte Folge akzeptiert")
        check(f36d["frame_halfbits"] == 15, f"T36.4: Rahmenlaenge unveraendert ({f36d['frame_halfbits']})")
        check(f36d["polarity"] == "inverted", f"T36.4: Polaritaet invertiert erkannt ({f36d['polarity']})")
    except Exception as exc:
        check(False, f"T36.4: unerwarteter Fehler ({exc})")

    # 5. Zufallsbits -> abgelehnt. Die wichtigste Pruefung der Aufgabe.
    try:
        rng36e = np.random.default_rng(16500)
        bits36e = [int(v) for v in rng36e.integers(0, 2, 200 * 15)]
        f36e = detect_async_framing(bits36e)
        print(f"  T36.5: Zufall -> accepted={f36e['accepted']} reject_reason={f36e['reject_reason']}")
        check(f36e["accepted"] is False, "T36.5: Zufallsbits abgelehnt")
        check(f36e["reject_reason"] is not None, "T36.5: Ablehnungsgrund gesetzt")
    except Exception as exc:
        check(False, f"T36.5: unerwarteter Fehler ({exc})")

    # 6. Bitfehler (5%): Rahmenlaenge bleibt richtig, Score messbar kleiner
    # als im sauberen Fall.
    try:
        rng36f = np.random.default_rng(16600)
        halfbits36f = _make_async_frames(200, data_bits=5, stop_bits=1.5, rng=rng36f)
        bits36f = [int(v) for v in halfbits36f]
        flips36f = rng36f.random(len(bits36f)) < 0.05
        bits36f_err = [1 - b if flip else b for b, flip in zip(bits36f, flips36f)]
        f36f = detect_async_framing(bits36f_err)
        print(f"  T36.6: 5% Bitfehler -> score={f36f['score']:.3f} (sauber nahe 1,0)")
        check(f36f["accepted"], "T36.6: trotz Bitfehlern akzeptiert")
        check(f36f["frame_halfbits"] == 15, f"T36.6: Rahmenlaenge unveraendert ({f36f['frame_halfbits']})")
        check(f36f["score"] < 0.99, f"T36.6: Score messbar kleiner als sauber ({f36f['score']:.3f})")
    except Exception as exc:
        check(False, f"T36.6: unerwarteter Fehler ({exc})")

    # 7. Zu wenige Rahmen: 10 Rahmen -> reject_reason=="zu_wenige_rahmen".
    try:
        rng36g = np.random.default_rng(16700)
        halfbits36g = _make_async_frames(10, data_bits=5, stop_bits=1.5, rng=rng36g)
        f36g = detect_async_framing([int(v) for v in halfbits36g])
        check(f36g["accepted"] is False, "T36.7: 10 Rahmen abgelehnt")
        check(f36g["reject_reason"] == "zu_wenige_rahmen",
              f"T36.7: Ablehnungsgrund zu_wenige_rahmen ({f36g['reject_reason']})")
    except Exception as exc:
        check(False, f"T36.7: unerwarteter Fehler ({exc})")

    # 8. Ende zu Ende: _make_async_frames(150, stop_bits=1.5) ueber
    # _make_fsk_signal_from_symbols zu einem FSK2-Signal, mit analyze_file()
    # ausgewertet - Rahmensuche und Nulldurchgangsraster muessen dasselbe
    # sagen.
    try:
        with tempfile.TemporaryDirectory() as tmp36h:
            rng36h = np.random.default_rng(16803)
            halfbits36h = _make_async_frames(150, data_bits=5, stop_bits=1.5, rng=rng36h)
            sig36h = _make_fsk_signal_from_symbols(32000, 2.0 * 50.0, (-425.0, 425.0),
                                                    halfbits36h, 0.0, rng36h, snr_db=20.0)
            path36h = _write_temp_wav(tmp36h, "t36h.wav", sig36h, 32000)
            r36h = analyze_file(path36h, decimate=8, min_baud=20.0)
        print(f"  T36.8: frame_halfbits={r36h['framing_frame_halfbits']} "
              f"crossing_grid={r36h['crossing_grid']} n_frames={r36h['framing_n_frames']}")
        check(r36h["framing_accepted"], "T36.8: Ende-zu-Ende - Rahmensuche akzeptiert")
        check(r36h["framing_frame_halfbits"] == 15,
              f"T36.8: Rahmenlaenge 15 Halbbit ({r36h['framing_frame_halfbits']})")
        check(r36h["crossing_grid"] == "half",
              f"T36.8: Nulldurchgangsraster halb ({r36h['crossing_grid']})")
    except Exception as exc:
        check(False, f"T36.8: unerwarteter Fehler ({exc})")

    # 9. Grobe Symbolrate sperrt: kurzer Burst, bei dem baud_refined False ist
    # (Muster T35.5) - framing_reject_reason muss
    # "symbolrate_nicht_feinabgeglichen" sein, kein Rueckfall auf die grobe
    # Schaetzung.
    try:
        with tempfile.TemporaryDirectory() as tmp36i:
            rng36i = np.random.default_rng(16900)
            sig36i = _make_fsk_signal(32000, 50.0, (-225.0, 225.0), 0.5, 0.0, rng36i, snr_db=20.0)
            path36i = _write_temp_wav(tmp36i, "t36i.wav", sig36i, 32000)
            r36i = analyze_file(path36i, decimate=8, min_baud=20.0)
        print(f"  T36.9: baud_refined={r36i['baud_refined']} "
              f"framing_reject_reason={r36i['framing_reject_reason']}")
        check(r36i["baud_refined"] is False, "T36.9: kurzer Burst nicht feinabgeglichen (Vorbedingung)")
        check(r36i["framing_accepted"] is False, "T36.9: Rahmensuche ohne Feinabgleich abgelehnt")
        check(r36i["framing_reject_reason"] == "symbolrate_nicht_feinabgeglichen",
              f"T36.9: Ablehnungsgrund benannt ({r36i['framing_reject_reason']})")
    except Exception as exc:
        check(False, f"T36.9: unerwarteter Fehler ({exc})")

    # 10. Abschottung: dev_hz/offset_hz/baud/eye_opening_pct/jitter_pct/
    # crossing_grid/mode_candidates sind mit und ohne Rahmensuche bitgleich -
    # T-1715 fuegt hinzu, es aendert nichts. Die Rahmensuche liest nur
    # (idx_all/f_det/positions/T/phase), sie mutiert nichts, was diese Felder
    # speist - zwei unabhaengige Laeufe derselben Aufnahme muessen dafuer
    # bitgleiche Ergebnisse liefern.
    try:
        with tempfile.TemporaryDirectory() as tmp36j:
            rng36j = np.random.default_rng(17000)
            sig36j = _make_fsk_signal(32000, 50.0, (-225.0, 225.0), 60.0, 0.0, rng36j, snr_db=10.0)
            path36j = _write_temp_wav(tmp36j, "t36j.wav", sig36j, 32000)
            r36j_1 = analyze_file(path36j, decimate=8, min_baud=20.0)
            r36j_2 = analyze_file(path36j, decimate=8, min_baud=20.0)
        for feld36j in ("dev_hz", "offset_hz", "baud", "eye_opening_pct", "jitter_pct", "crossing_grid"):
            check(r36j_1[feld36j] == r36j_2[feld36j],
                  f"T36.10: {feld36j} bitgleich zwischen zwei Laeufen ({r36j_1[feld36j]!r})")
        check(r36j_1["mode_candidates"] == r36j_2["mode_candidates"],
              "T36.10: mode_candidates bitgleich zwischen zwei Laeufen")
    except Exception as exc:
        check(False, f"T36.10: unerwarteter Fehler ({exc})")

    # ------------------------------------------------------------------
    # T37: Alphabet-Indizien und Zeichenvorrat (T-1716, character_stats())
    # ------------------------------------------------------------------

    # 7. Ende zu Ende ueber die bestehende 1,5-Stoppbit-Fixture (Muster
    # T36.8): chars_n > 0, chars_code_shares gefuellt, alle neuen
    # Schluessel vorhanden.
    try:
        with tempfile.TemporaryDirectory() as tmp37a:
            rng37a = np.random.default_rng(16803)
            halfbits37a = _make_async_frames(150, data_bits=5, stop_bits=1.5, rng=rng37a)
            sig37a = _make_fsk_signal_from_symbols(32000, 2.0 * 50.0, (-425.0, 425.0),
                                                    halfbits37a, 0.0, rng37a, snr_db=20.0)
            path37a = _write_temp_wav(tmp37a, "t37a.wav", sig37a, 32000)
            r37a = analyze_file(path37a, decimate=8, min_baud=20.0)
        print(f"  T37.7: chars_n={r37a['chars_n']} framing_n_frames={r37a['framing_n_frames']} "
              f"fail_share={r37a['chars_fail_share']:.3f}")
        check(r37a["chars_n"] > 0, f"T37.7: Zeichenvorrat nicht leer ({r37a['chars_n']})")
        check(len(r37a["chars_code_shares"]) == 6,
              f"T37.7: alle sechs Bezugscodes vorhanden ({len(r37a['chars_code_shares'])})")
        for feld37a in ("chars_fail_share", "chars_fail_run_max", "chars_fail_share_in_runs",
                        "chars_top_codes", "chars_halfbit_mismatch_share", "chars_ita2_hint"):
            check(feld37a in r37a, f"T37.7: Schluessel {feld37a} vorhanden")
    except Exception as exc:
        check(False, f"T37.7: unerwarteter Fehler ({exc})")

    # 8. Abschottung: dev_hz/offset_hz/baud/eye_opening_pct/jitter_pct/
    # crossing_grid/mode_candidates UND alle framing_* sind mit und ohne die
    # neue Auswertung (character_stats) bitgleich - T-1716 fuegt hinzu, es
    # aendert nichts. Zwei unabhaengige Laeufe derselben Aufnahme muessen
    # dafuer bitgleiche Ergebnisse liefern (Muster T36.10).
    try:
        with tempfile.TemporaryDirectory() as tmp37b:
            rng37b = np.random.default_rng(16803)
            halfbits37b = _make_async_frames(150, data_bits=5, stop_bits=1.5, rng=rng37b)
            sig37b = _make_fsk_signal_from_symbols(32000, 2.0 * 50.0, (-425.0, 425.0),
                                                    halfbits37b, 0.0, rng37b, snr_db=20.0)
            path37b = _write_temp_wav(tmp37b, "t37b.wav", sig37b, 32000)
            r37b_1 = analyze_file(path37b, decimate=8, min_baud=20.0)
            r37b_2 = analyze_file(path37b, decimate=8, min_baud=20.0)
        felder37b = ("dev_hz", "offset_hz", "baud", "eye_opening_pct", "jitter_pct", "crossing_grid",
                     "framing_frame_halfbits", "framing_frame_bits", "framing_polarity",
                     "framing_score", "framing_margin", "framing_n_frames", "framing_accepted",
                     "framing_reject_reason")
        for feld37b in felder37b:
            check(r37b_1[feld37b] == r37b_2[feld37b],
                  f"T37.8: {feld37b} bitgleich zwischen zwei Laeufen ({r37b_1[feld37b]!r})")
        check(r37b_1["mode_candidates"] == r37b_2["mode_candidates"],
              "T37.8: mode_candidates bitgleich zwischen zwei Laeufen")
        check(r37b_1["framing_interpretations"] == r37b_2["framing_interpretations"],
              "T37.8: framing_interpretations bitgleich zwischen zwei Laeufen")
    except Exception as exc:
        check(False, f"T37.8: unerwarteter Fehler ({exc})")

    # --- T38 (T-1608a): --suggest beachtet das Zeitfenster ------------------
    # Der Befund dahinter: run_suggest las immer die ganze Datei. Die Probe
    # baut dieselbe Fixtur wie T25 - Rauschen, Burst, Rauschen - und laesst
    # run_suggest einmal ueber alles und einmal ueber die ersten 3,5 s laufen.
    # Entscheidend ist NICHT, was die Burstsuche im Fenster findet, sondern
    # dass die beiden Ausgaben ueberhaupt verschieden sind: gleiche Datei,
    # gleiche Parameter, einziger Unterschied ist das Fenster - waere es
    # wirkungslos, waeren beide Ausgaben zeichengleich. Ein erster Anlauf
    # verliess sich darauf, dass ein Rauschfenster einen Fehler wirft; das
    # tut es nicht, detect_bursts stuft es als Dauersignal ein.
    with tempfile.TemporaryDirectory() as tmp:
        fs38 = 8000
        rng38 = np.random.default_rng(38)
        z38_ruhe = (rng38.normal(0.0, 1e-4, 4 * fs38)
                    + 1j * rng38.normal(0.0, 1e-4, 4 * fs38))
        z38_mitte = _make_fsk_signal(fs38, 100.0, (-500.0, 500.0), 4.0, 0.0, rng38)
        t38_path = _write_temp_wav(tmp, "t38.wav",
                                    np.concatenate([z38_ruhe, z38_mitte, z38_ruhe]), fs38)
        puffer38_voll, puffer38_fenster = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(puffer38_voll):
                rc38_voll = run_suggest(t38_path)
            with contextlib.redirect_stdout(puffer38_fenster):
                rc38_fenster = run_suggest(t38_path, 0.0, 3.5)
        except Exception as exc:
            rc38_voll = rc38_fenster = None
            check(False, f"T38: unerwarteter Fehler ({exc})")
        aus38_voll = puffer38_voll.getvalue()
        aus38_fenster = puffer38_fenster.getvalue()
        check(rc38_voll == 0 and rc38_fenster == 0,
              f"T38: beide Laeufe kommen durch (rc {rc38_voll}/{rc38_fenster})")
        check(aus38_voll != aus38_fenster,
              "T38: Fensterlauf und Ganzdatei-Lauf liefern verschiedene Ausgaben - "
              "waere das Fenster wirkungslos, waeren sie zeichengleich")
        dauern38 = [float(z.split("Dauer")[1].split("s")[0])
                    for z in aus38_fenster.splitlines()
                    if z.strip().startswith("Burst ") and "Dauer" in z]
        check(bool(dauern38) and max(dauern38) <= 3.51,
              f"T38: kein Burst im Fenster laenger als das Fenster ({dauern38})")

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
    parser.add_argument("--out", help="Ziel-SVG (Default: <wav> mit Endung .eye.svg)")
    parser.add_argument("--json", help="Kennzahlen zusaetzlich als JSON schreiben")
    parser.add_argument("--baud", type=float, help="Symbolrate vorgeben statt aus der Autokorrelation zu schaetzen")
    parser.add_argument("--burst", type=int, help="i-ten gefundenen Burst auswerten (0-basiert), Default: laengster")
    parser.add_argument("--max-traces", type=int, default=400, help="maximale Anzahl Augendiagramm-Spuren (Default 400)")
    parser.add_argument("--decimate", type=int, default=1,
                         help="Dezimationsfaktor N (ganzzahlig >= 1) mit Anti-Aliasing-Tiefpass vor der "
                              "Momentanfrequenzberechnung, fuer Schmalbandsignale (Default 1 = keine Dezimation)")
    parser.add_argument("--min-baud", type=float, default=20.0,
                         help="angenommene untere Baudraten-Grenze fuer die Dezimationspruefung und den "
                              "Autokorrelations-Lag-Bereich (Default 20.0)")
    parser.add_argument("--max-points", type=int, default=MAX_CONST_POINTS,
                         help=f"hoechstens so viele Konstellationspunkte zeichnen "
                              f"(Vorgabe {MAX_CONST_POINTS}, 0 = alle)")
    parser.add_argument("--audio", metavar="ZIEL.WAV",
                         help="IQ-Ausschnitt als hoerbare 16-Bit-Mono-WAV schreiben "
                              "(T-1623, CW-Betrieb mit Mithoerton) und beenden")
    parser.add_argument("--auto-decimate", type=float, default=0.0, metavar="BW_HZ",
                         help="Dezimationsfaktor aus dieser Signalbandbreite ableiten "
                              "(Hz, T-1620) statt ihn mit --decimate vorzugeben; "
                              "ueberschreibt --decimate")
    parser.add_argument("--tune", type=float, default=0.0,
                         help="Signal um diesen Betrag nach unten verschieben, bevor "
                              "dezimiert wird (Hz, T-1620) - noetig, wenn das Signal nicht "
                              "in der Bandmitte liegt")
    parser.add_argument("--t0", type=float,
                         help="Anfang des auszuwertenden Zeitfensters in Sekunden (T-1608); "
                              "nur zusammen mit --t1")
    parser.add_argument("--t1", type=float,
                         help=f"Ende des Zeitfensters in Sekunden, hoechstens "
                              f"{MAX_WINDOW_S:.0f} s nach --t0")
    parser.add_argument("--suggest", action="store_true",
                         help="nur Abtastrate, Burstuebersicht, grobes Frequenzhistogramm und einen "
                              "--decimate-Vorschlag ausgeben, keine Auswertung; beachtet --t0/--t1")
    parser.add_argument("--mode", choices=("fsk", "psk", "ofdm"), default="fsk",
                         help="Auswertepfad: fsk (Default, Augendiagramm/Hub/Pegelanzahl) oder "
                              "psk (Konstellation/Traegerversatz/Modulationsordnung)")
    parser.add_argument("--selftest", action="store_true", help="Selbsttest ohne Hardware/Aufnahme ausfuehren")
    args = parser.parse_args()

    if args.selftest:
        return 0 if _self_test() else 1

    if not args.wav:
        parser.error("WAV-Datei erforderlich (ausser bei --selftest)")

    if (args.t0 is None) != (args.t1 is None):
        parser.error("--t0 und --t1 nur gemeinsam")

    # T-1620: die Ableitungsregel steht HIER und nicht im Server - der
    # importiert dieses Werkzeug bewusst nicht, sondern ruft es als
    # Unterprozess auf (analyze_audio, Muster ensure_befund).
    if args.mode == "ofdm":
        # T-1613: dritter Modus, aber KEIN vollwertiger Auswertepfad - nur die
        # Pruefung auf mehrtraegrige Struktur. Kein SVG, keine Symbolanalyse.
        try:
            befund = analyze_ofdm_file(args.wav, args.t0, args.t1, tune_hz=args.tune)
        except (ValueError, RuntimeError, wave.Error, FileNotFoundError) as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 2 if isinstance(exc, RuntimeError) else 1
        print(f"Moeglicherweise OFDM: Symboldauer {befund['symbol_s'] * 1000:.2f} ms, "
              f"Traegerabstand {befund['traegerabstand_erwartet_hz']:.1f} Hz")
        if befund["traegerabstand_gemessen_hz"]:
            print(f"  Kamm im Spektrum: {befund['traegerabstand_gemessen_hz']:.1f} Hz "
                  f"({'passt' if befund['abstand_passt'] else 'weicht ab'})")
        if befund["kurtosis"] is not None:
            print(f"  Kurtosis der Amplitude: {befund['kurtosis']:.2f} "
                  f"(bei vielen Traegern nahe 3)")
        print(f"  Autokorrelationsgipfel: {befund['korr_gipfel']:.3f}, "
              f"Ueberhoehung {befund['korr_ueberhoehung']:.3f}")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(befund, f, ensure_ascii=False, indent=2)
            print(f"JSON geschrieben: {args.json}")
        return 0

    if args.audio:
        # T-1623: eigener, kurzer Weg - keine Burst- oder Symbolanalyse.
        try:
            rate, samples = iq_to_audio(args.wav, args.t0, args.t1,
                                         tune_hz=args.tune,
                                         bandbreite_hz=args.auto_decimate)
            write_audio_wav(args.audio, rate, samples)
        except (ValueError, RuntimeError, wave.Error, FileNotFoundError, OSError) as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 1
        print(f"Audio geschrieben: {args.audio} ({len(samples) / rate:.1f} s, {rate} Hz)")
        return 0

    if args.auto_decimate > 0:
        with wave.open(args.wav, "rb") as _w:
            _fs = _w.getframerate()
        args.decimate = suggest_decimation(_fs, args.auto_decimate, args.min_baud)
        print(f"Auto-Dezimation: Bandbreite {args.auto_decimate:.0f} Hz "
              f"-> N={args.decimate}, effektiv {_fs / args.decimate:.1f} Hz")

    if args.suggest:
        try:
            return run_suggest(args.wav, args.t0, args.t1)
        except (ValueError, wave.Error, FileNotFoundError, IndexError) as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 1
        except RuntimeError as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 2

    if args.mode == "psk":
        try:
            result = analyze_file_psk(args.wav, burst_index=args.burst, baud_override=args.baud,
                                       decimate=args.decimate, min_baud=args.min_baud,
                                       t0_s=args.t0, t1_s=args.t1, tune_hz=args.tune)
        except (ValueError, wave.Error, FileNotFoundError, IndexError) as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 1
        except RuntimeError as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 2

        print_report_psk(result)

        out_path = args.out or os.path.splitext(args.wav)[0] + ".psk.svg"
        write_svg_psk(out_path, result["symbols"], result["phase_hist"],
                       max_points=args.max_points)
        print(f"SVG geschrieben: {out_path}")

        if args.json:
            write_json_psk(args.json, result)
            print(f"JSON geschrieben: {args.json}")

        return 0

    try:
        result = analyze_file(args.wav, burst_index=args.burst, baud_override=args.baud, max_traces=args.max_traces,
                               decimate=args.decimate, min_baud=args.min_baud,
                               t0_s=args.t0, t1_s=args.t1, tune_hz=args.tune)
    except (ValueError, wave.Error, FileNotFoundError, IndexError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2

    print_report(result)

    out_path = args.out or os.path.splitext(args.wav)[0] + ".eye.svg"
    write_svg(out_path, result["traces"], result["dev_hz"])
    print(f"SVG geschrieben: {out_path}")

    if args.json:
        write_json(args.json, result)
        print(f"JSON geschrieben: {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
