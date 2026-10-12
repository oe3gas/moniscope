#!/usr/bin/env python3
"""
eb200_core/prepare_budget.py - Vorhersage und Budgetpruefung fuer den
Aufbereitungs-Worker (T-1602, Meilenstein 16).

Beantwortet EINE Frage, bevor der Worker eine Datei anfasst: darf dieser
Auftrag jetzt laufen? Dafuer braucht es drei Vorhersagen und eine Regel.

Vorhersagen
-----------
1. GROESSE des Artefakts. Sie ist nach oben beschraenkt und vorab EXAKT
   berechenbar, nicht geschaetzt: audio_spectrum.build_audspec() dezimiert
   jede Sektion auf hoechstens MAX_ROWS Zeilen, und die Zeilenzahl steht
   ueber _predict_rows_raw() schon vor dem ersten gelesenen Byte fest
   (T-1212). Eine zwanzigstuendige Aufnahme erzeugt denselben Cache wie
   eine zweistuendige, sie rechnet nur laenger:
       IQ   8000 * (32+64+128+256+512+1024+2048) = 32 512 000 Byte (31,0 MiB)
       mono 8000 * (17+33+65+129+257+513+1025)   = 16 312 000 Byte (15,6 MiB)
2. LAUFZEIT aus gemessenem Durchsatz (siehe THROUGHPUT_FRAMES_PER_S).
3. SPEICHERBEDARF des Bauprozesses (siehe PEAK_MEMORY_BYTES) - nicht Teil
   der Platzrechnung, aber der Grund, warum der Worker immer nur EINE Datei
   auf einmal baut.

Regel
-----
Frei sein muss: SOCKEL + BEDARF + AUFNAHMERESERVE.
  - Sockel: wird nie angetastet, groesser aus DISK_FLOOR_BYTES und
    DISK_FLOOR_FRACTION des Volumes.
  - Bedarf: Artefaktgroesse x 2 (build_audspec schreibt erst eine
    Temp-Datei und ersetzt dann atomar) plus TEMP_SLACK_BYTES.
  - Aufnahmereserve: was eine laufende oder geplante Aufnahme noch
    schreiben wird. Der teure Schaden ist NICHT ein fehlender Cache - der
    ist regenerierbar - sondern eine abgebrochene Aufnahme.

Dazu die Zeitregel: keine Arbeit anfangen, die vor der naechsten geplanten
Aufnahme nicht fertig wird (TIME_MARGIN_S Sicherheitsabstand). Sechs Minuten
Rechenlast in die Startminute einer Aufnahme zu legen waere genau der Fall,
den man nicht will - UDP-Verlust ist teurer als eine spaete Auswertung.

Reine Rechenfunktionen, nur Standardbibliothek. Dieses Modul kennt weder
Aufnahmeordner noch job_store noch Sidecars: WELCHE Aufnahmen offen sind und
WANN die naechste startet, ermittelt der Worker (T-1603) und reicht es als
Zahlen herein. Damit ist alles hier ohne Hardware und ohne Dateisystem
pruefbar - bis auf disk_free(), das shutil.disk_usage() kapselt.

KEIN Import aus dem Repo-Root (audio_spectrum.py): eb200_core wird von den
Root-Skripten importiert, nie umgekehrt. Die Zeilenformel ist deshalb hier
nachgebildet; ihre Uebereinstimmung mit dem echten build_audspec() sichert
der Selbsttest gegen zwei ECHTE, gemessene Aufnahmen ab (siehe _self_test).
"""
from __future__ import annotations

import argparse
import shutil
import sys

# Muss zu audio_spectrum.MAX_ROWS / FFT_SIZES passen. Bei einer Aenderung
# dort schlaegt der Selbsttest hier fehl (er prueft gegen echte Header).
MAX_ROWS = 8000
FFT_SIZES = (32, 64, 128, 256, 512, 1024, 2048)   # T-1618

KIND_MONO = "mono"
KIND_IQ = "iq"

# Gemessen am 2026-09-07 (PC, E:, .venv, Python 3.14), jeweils --rebuild:
#   IQ   216 950 201 Frames in 145,8 s -> 1,49 Mio Frames/s, Spitze 276 MB
#   mono   2 410 116 Frames in  1,01 s -> 2,40 Mio Frames/s, Spitze  31 MB
# Die Planungswerte liegen rund 20 % darunter: ein langsamerer Datentraeger
# oder Last durch eine parallele Aufnahme soll die Terminplanung nicht
# sprengen. Unterschaetzter Durchsatz ist die sichere Richtung - er fuehrt
# dazu, dass ein Auftrag verschoben wird, nicht dazu, dass er in eine
# Aufnahme hineinlaeuft. Der Raspberry Pi braucht eigene Werte
# (Meilenstein 15).
THROUGHPUT_FRAMES_PER_S = {KIND_IQ: 1_200_000, KIND_MONO: 1_900_000}
PEAK_MEMORY_BYTES = {KIND_IQ: 290 * 1024 ** 2, KIND_MONO: 40 * 1024 ** 2}
STARTUP_S = 0.3          # Interpreterstart + numpy-Import, gemessen 0,30 s

DISK_FLOOR_BYTES = 5 * 1024 ** 3      # Sockel, absolut
DISK_FLOOR_FRACTION = 0.03            # Sockel, anteilig - der groessere gilt
TEMP_SLACK_BYTES = 32 * 1024 ** 2     # Zuschlag fuer Temp-/Sperrdateien
RECORDING_HORIZON_S = 2 * 3600        # offenes Ende: mit 2 h Rest rechnen
TIME_MARGIN_S = 60.0                  # Abstand zur naechsten Aufnahme

# Ergebniscodes von check_budget() - ASCII und sprachneutral, den
# Anzeigetext bildet die Oberflaeche (Projektregel: Server liefern Codes).
CODE_OK = "ok"
CODE_DISK_FLOOR = "disk_floor"        # Sockel bereits unterschritten
CODE_DISK_NEED = "disk_need"          # Sockel frei, aber Bedarf passt nicht
CODE_TIME_WINDOW = "time_window"      # wuerde in die naechste Aufnahme laufen
CODE_UNKNOWN_KIND = "unknown_kind"    # weder mono noch iq

# Lagebeurteilung des Ablageplatzes (T-1610). Anders als die CODE_*-Werte
# oben gehoert sie zu KEINEM einzelnen Auftrag: sie beschreibt den Zustand
# des Datentraegers und wird bei JEDEM Zyklus erhoben, damit die Oberflaeche
# warnen kann, BEVOR der erste Auftrag zurueckgestellt wird.
SPACE_OK = "space_ok"
SPACE_TIGHT = "space_tight"           # verfuegbar < SPACE_TIGHT_FACTOR * Sockel
SPACE_LOW = "space_low"               # Sockel/Reserve bereits aufgezehrt

SPACE_TIGHT_FACTOR = 5.0

# Klassifikation (.klasse.json, T-1607). Gemessen 2026-09-10 an 600 s mono:
# 0,07 s, also rund 140 Mio Frames/s - audio_classify tastet Fenster ab und
# liest die Datei NICHT vollstaendig, daher der Abstand zum Audspec (Faktor
# 25). Der Planungswert liegt bewusst weit darunter: die Messung stammt von
# einer schnelleren Maschine als dem Zielrechner, und selbst mit 20 Mio
# Frames/s bleibt eine zwoelfstuendige Mono-Aufnahme unter einer Minute.
# Das Sidecar ist rund 1,6 kB gross; KLASSE_NBYTES ist die Obergrenze mit
# reichlich Luft, weil "reasons" je nach Signal laenger ausfallen kann.
KLASSE_FRAMES_PER_S = 20_000_000
KLASSE_NBYTES = 16 * 1024


def n_bins_for(kind: str, n_fft: int) -> int:
    """Bins je Zeile: mono rfft -> n_fft//2 + 1, iq volle FFT -> n_fft
    (Muster audio_spectrum.n_bins_for)."""
    return n_fft if kind == KIND_IQ else n_fft // 2 + 1


def section_rows(n_frames: int, n_fft: int) -> tuple:
    """(rows_raw, decimation, rows) einer Sektion - dieselbe Formel wie
    audio_spectrum._predict_rows_raw() plus _MaxHoldRows: hop = n_fft//2,
    rows_raw = 1 + (n_frames - n_fft)//hop, Gruppengroesse
    k = ceil(rows_raw / MAX_ROWS), rows = ceil(rows_raw / k)."""
    hop = n_fft // 2
    if n_frames < n_fft or hop <= 0:
        return 0, 1, 0
    rows_raw = 1 + (n_frames - n_fft) // hop
    if rows_raw <= MAX_ROWS:
        return rows_raw, 1, rows_raw
    k = -(-rows_raw // MAX_ROWS)
    return rows_raw, k, -(-rows_raw // k)


def audspec_nbytes(kind: str, n_frames: int) -> int:
    """Groesse der fertigen .audspec-Datei in Byte (uint8, eine Zeile je
    Sektion x n_bins; der JSON-Header faellt daneben nicht ins Gewicht und
    ist in TEMP_SLACK_BYTES mit abgedeckt)."""
    return sum(section_rows(n_frames, n_fft)[2] * n_bins_for(kind, n_fft)
               for n_fft in FFT_SIZES)


def audspec_nbytes_max(kind: str) -> int:
    """Obergrenze ueber ALLE Aufnahmelaengen - die Zahl, mit der sich ein
    Ablageplatz auslegen laesst (IQ 31,0 MiB, mono 15,6 MiB).

    Einheiten sind in diesem Modul durchgaengig BYTE; die Anzeige teilt
    durch 1024 (MiB/GiB), nicht durch 1000."""
    return MAX_ROWS * sum(n_bins_for(kind, n_fft) for n_fft in FFT_SIZES)


def estimate_build_seconds(kind: str, n_frames: int) -> float:
    """Erwartete Bauzeit aus dem Planungsdurchsatz plus Startaufwand.
    Unbekannte Art -> langsamster bekannter Wert (fail-slow: lieber
    verschieben als in eine Aufnahme laufen)."""
    rate = THROUGHPUT_FRAMES_PER_S.get(kind, min(THROUGHPUT_FRAMES_PER_S.values()))
    return STARTUP_S + (n_frames / rate if rate else 0.0)


def klasse_nbytes() -> int:
    """Obergrenze des Klassifikations-Sidecars in Byte (T-1607). Anders als
    beim Audspec haengt sie nicht von der Aufnahmelaenge ab - der Sidecar
    traegt Klasse, Punktwert und eine Handvoll Begruendungen."""
    return KLASSE_NBYTES


def estimate_klasse_seconds(n_frames: int) -> float:
    """Erwartete Dauer einer Klassifikation. Nur mono - audio_classify
    lehnt IQ ab (Moduldoku dort). Startaufwand faellt hier NICHT an: der
    Worker importiert das Modul, statt einen Prozess zu starten."""
    return n_frames / KLASSE_FRAMES_PER_S if KLASSE_FRAMES_PER_S else 0.0


def peak_memory_bytes(kind: str) -> int:
    """Erwartete Speicherspitze des Bauprozesses. Nicht Teil der
    Platzrechnung - der Grund, warum immer nur EIN Bau gleichzeitig laeuft."""
    return PEAK_MEMORY_BYTES.get(kind, max(PEAK_MEMORY_BYTES.values()))


def disk_floor_bytes(total_bytes: int) -> int:
    """Sockel: der groessere aus DISK_FLOOR_BYTES und dem Anteil
    DISK_FLOOR_FRACTION am Volume."""
    return max(DISK_FLOOR_BYTES, int(total_bytes * DISK_FLOOR_FRACTION))


def space_status(total_bytes: int, free_bytes: int, reserve_bytes: int = 0) -> dict:
    """Lagebeurteilung des Ablageplatzes aus reinen Zahlen (T-1610).

    "available" ist das, was der Worker tatsaechlich verbrauchen darf:
    frei minus Sockel minus Aufnahmereserve. Es KANN negativ werden - dann
    ist der Sockel bereits angeknabbert, und genau das soll sichtbar sein,
    statt in einer auf Null geklemmten Zahl zu verschwinden.

    Rein rechnend und ohne Dateisystemzugriff, damit die Schwellen ohne
    praeparierten Datentraeger pruefbar sind; disk_report() setzt die
    echten Werte davor.
    """
    floor = disk_floor_bytes(total_bytes)
    available = int(free_bytes) - floor - int(reserve_bytes)
    if available <= 0:
        code = SPACE_LOW
    elif available < SPACE_TIGHT_FACTOR * floor:
        code = SPACE_TIGHT
    else:
        code = SPACE_OK
    return {"code": code, "total_bytes": int(total_bytes),
            "free_bytes": int(free_bytes), "floor_bytes": floor,
            "reserve_bytes": int(reserve_bytes), "available_bytes": available}


def disk_report(volume_path: str, reserve_bytes: int = 0) -> dict:
    """space_status() fuer ein echtes Volume. Schlaegt der Zugriff fehl
    (Laufwerk weg, Rechte), gilt die Lage als SPACE_LOW mit Nullwerten -
    lieber nichts anfangen als auf einem unbekannten Datentraeger bauen."""
    try:
        total, free = disk_free(volume_path)
    except OSError:
        return {"code": SPACE_LOW, "total_bytes": 0, "free_bytes": 0,
                "floor_bytes": 0, "reserve_bytes": int(reserve_bytes),
                "available_bytes": 0}
    return space_status(total, free, reserve_bytes)


def disk_free(path: str) -> tuple:
    """(total, free) in Byte fuer das Volume, auf dem path liegt. Einzige
    Funktion dieses Moduls mit Zugriff auf die Aussenwelt."""
    usage = shutil.disk_usage(path)
    return int(usage.total), int(usage.free)


def recording_bytes_per_s(sample_rate_hz: int, channels: int,
                          sampwidth_bytes: int = 2) -> float:
    """Schreibrate einer laufenden Aufnahme (PCM, ohne Header). 32 kHz IQ
    ergibt 128 000 Byte/s, also gut 7,3 MiB je Minute."""
    return float(sample_rate_hz) * int(channels) * int(sampwidth_bytes)


def recording_reserve_bytes(open_recordings, horizon_s: float = RECORDING_HORIZON_S) -> int:
    """Was laufende/geplante Aufnahmen noch schreiben werden.

    open_recordings ist eine Folge von dicts mit "sample_rate_hz",
    "channels", optional "sampwidth_bytes" und optional "remaining_s".
    Fehlt remaining_s (offenes Ende - der Normalfall bei einer manuell
    gestarteten Aufnahme), wird mit horizon_s gerechnet. Unbrauchbare
    Eintraege werden uebersprungen, nicht als Ausnahme gemeldet: eine
    kaputte Sidecar darf die Budgetpruefung nicht zum Absturz bringen.
    """
    total = 0.0
    for rec in open_recordings or ():
        try:
            rate = recording_bytes_per_s(
                rec["sample_rate_hz"], rec["channels"],
                rec.get("sampwidth_bytes", 2))
            remaining = rec.get("remaining_s")
            remaining = horizon_s if remaining is None else float(remaining)
        except (TypeError, ValueError, KeyError):
            continue
        total += rate * max(0.0, remaining)
    return int(total)


def check_budget(artifact_bytes: int, est_seconds: float, volume_path: str,
                 reserve_bytes: int = 0, seconds_until_next_recording=None) -> dict:
    """Darf dieser Auftrag jetzt laufen?

    Prueft in dieser Reihenfolge: Sockel unterschritten, Bedarf passt
    nicht, Zeitfenster zu knapp. Liefert immer ein vollstaendiges dict -
    auch im Gutfall - damit die Statusseite dieselben Zahlen anzeigen kann,
    die zur Entscheidung gefuehrt haben.
    """
    total_bytes, free_bytes = disk_free(volume_path)
    floor = disk_floor_bytes(total_bytes)
    need = int(artifact_bytes) * 2 + TEMP_SLACK_BYTES

    result = {
        "ok": False, "code": CODE_OK,
        "total_bytes": total_bytes, "free_bytes": free_bytes,
        "floor_bytes": floor, "need_bytes": need,
        "reserve_bytes": int(reserve_bytes),
        "est_seconds": float(est_seconds),
        "seconds_until_next_recording": seconds_until_next_recording,
    }
    if free_bytes < floor:
        result["code"] = CODE_DISK_FLOOR
        return result
    if free_bytes < floor + need + int(reserve_bytes):
        result["code"] = CODE_DISK_NEED
        return result
    if seconds_until_next_recording is not None:
        if float(est_seconds) > float(seconds_until_next_recording) - TIME_MARGIN_S:
            result["code"] = CODE_TIME_WINDOW
            return result
    result["ok"] = True
    return result


def plan_audspec(kind: str, n_frames: int, volume_path: str,
                 reserve_bytes: int = 0, seconds_until_next_recording=None) -> dict:
    """Bequemer Gesamtaufruf fuer den Worker: Groesse und Laufzeit eines
    .audspec vorhersagen und gleich die Budgetpruefung anhaengen. Das
    Ergebnis enthaelt zusaetzlich "artifact_bytes", "est_seconds" und
    "peak_memory_bytes" - genug fuer eine Zeile auf der Statusseite."""
    if kind not in (KIND_MONO, KIND_IQ):
        return {"ok": False, "code": CODE_UNKNOWN_KIND, "kind": kind,
                "artifact_bytes": 0, "est_seconds": 0.0,
                "peak_memory_bytes": 0}
    artifact_bytes = audspec_nbytes(kind, n_frames)
    est_seconds = estimate_build_seconds(kind, n_frames)
    result = check_budget(artifact_bytes, est_seconds, volume_path,
                          reserve_bytes, seconds_until_next_recording)
    result["kind"] = kind
    result["artifact_bytes"] = artifact_bytes
    result["peak_memory_bytes"] = peak_memory_bytes(kind)
    return result


def _self_test() -> None:
    """Selbsttest (python eb200_core/prepare_budget.py --selftest).

    Der Kern ist T1/T2: die Zeilenformel wird gegen die Header ZWEIER
    echter, am 2026-09-07 vermessener Aufnahmen geprueft. Weicht
    audio_spectrum jemals von dieser Formel ab, faellt es hier auf - ohne
    dass dieses Modul den Repo-Root importieren muesste.
    """
    import os
    import tempfile

    # --- T1: echte IQ-Aufnahme (20260907_094655_10149kHz_IQ.wav) ------------
    iq_frames = 216950201
    iq_soll = [                       # n_fft: (rows_raw, decimation, rows)
        (32, 13559386, 1695, 8000), (64, 6779692, 848, 7995),
        (128, 3389845, 424, 7995), (256, 1694922, 212, 7995),
        (512, 847460, 106, 7995), (1024, 423729, 53, 7995),
        (2048, 211864, 27, 7847),
    ]
    for n_fft, rr_soll, dec_soll, rows_soll in iq_soll:
        rr, dec, rows = section_rows(iq_frames, n_fft)
        assert (rr, dec, rows) == (rr_soll, dec_soll, rows_soll), (n_fft, rr, dec, rows)
        assert n_bins_for(KIND_IQ, n_fft) == n_fft, n_fft
    assert audspec_nbytes(KIND_IQ, iq_frames) == 32188736, audspec_nbytes(KIND_IQ, iq_frames)
    print("T1 ok  (IQ-Header 216 950 201 Frames: rows_raw/decimation/rows aller "
          "sieben Sektionen getroffen, .audspec 30,70 MiB)")

    # --- T2: echte Mono-Aufnahme (20260907_222830_9585kHz_AM.wav) -----------
    mono_frames = 2410116
    mono_soll = [
        (32, 150631, 19, 7928), (64, 75315, 10, 7532),
        (128, 37657, 5, 7532), (256, 18828, 3, 6276), (512, 9413, 2, 4707),
        (1024, 4706, 1, 4706), (2048, 2352, 1, 2352),
    ]
    for n_fft, rr_soll, dec_soll, rows_soll in mono_soll:
        rr, dec, rows = section_rows(mono_frames, n_fft)
        assert (rr, dec, rows) == (rr_soll, dec_soll, rows_soll), (n_fft, rr, dec, rows)
        assert n_bins_for(KIND_MONO, n_fft) == n_fft // 2 + 1, n_fft
    assert audspec_nbytes(KIND_MONO, mono_frames) == 7717193, audspec_nbytes(KIND_MONO, mono_frames)
    print("T2 ok  (Mono-Header 2 410 116 Frames: alle fuenf Sektionen getroffen, "
          ".audspec 7,36 MiB; zwei Sektionen ohne Dezimierung)")

    # --- T3: Randfaelle und Obergrenzen -------------------------------------
    assert section_rows(0, 512) == (0, 1, 0)
    assert section_rows(100, 512) == (0, 1, 0), "kuerzer als n_fft -> keine Zeile"
    assert section_rows(512, 512) == (1, 1, 1)
    assert audspec_nbytes(KIND_IQ, 0) == 0
    assert audspec_nbytes_max(KIND_IQ) == 8000 * 4064 == 32512000
    assert audspec_nbytes_max(KIND_MONO) == 8000 * 2039 == 16312000
    # Keine Aufnahmelaenge darf die Obergrenze sprengen - das ist die
    # Zusage, auf der die ganze Platzrechnung steht.
    for frames in (1, 10 ** 4, 10 ** 6, iq_frames, 10 ** 10):
        for kind in (KIND_MONO, KIND_IQ):
            assert audspec_nbytes(kind, frames) <= audspec_nbytes_max(kind), (kind, frames)
    print("T3 ok  (Randfaelle; Obergrenzen IQ 31,01 MiB / mono 15,56 MiB werden "
          "auch bei 10^10 Frames nicht ueberschritten)")

    # --- T4: Laufzeit- und Speichervorhersage -------------------------------
    est_iq = estimate_build_seconds(KIND_IQ, iq_frames)
    est_mono = estimate_build_seconds(KIND_MONO, mono_frames)
    assert 180.0 < est_iq < 182.0, est_iq            # gemessen 145,8 s
    assert 1.5 < est_mono < 1.6, est_mono            # gemessen 1,31 s
    assert est_iq > 145.8 and est_mono > 1.31, "Vorhersage muss konservativ sein"
    assert estimate_build_seconds("unbekannt", 1_200_000) > 0.9, "fail-slow verletzt"
    assert peak_memory_bytes(KIND_IQ) > peak_memory_bytes(KIND_MONO)
    assert peak_memory_bytes("unbekannt") == peak_memory_bytes(KIND_IQ)
    print(f"T4 ok  (Laufzeit IQ {est_iq:.1f} s / mono {est_mono:.2f} s - beide "
          "ueber der Messung, unbekannte Art faellt auf den langsamsten Wert)")

    # --- T5: Sockel und Aufnahmereserve -------------------------------------
    # 1 TB: 3 % = 30 GB, der Anteil gewinnt. 100 GB: 3 % = 3 GB, der
    # absolute Sockel gewinnt. Beide Aeste muessen greifen.
    assert disk_floor_bytes(1000 * 1024 ** 3) == int(1000 * 1024 ** 3 * 0.03)
    assert disk_floor_bytes(100 * 1024 ** 3) == DISK_FLOOR_BYTES, "5 GB muessen greifen"
    iq_rate = recording_bytes_per_s(32000, 2)
    assert iq_rate == 128000.0, iq_rate
    assert abs(iq_rate * 60 / 1024 ** 2 - 7.32) < 0.02, "7,3 MiB/min erwartet"
    offen = [{"sample_rate_hz": 32000, "channels": 2}]
    assert recording_reserve_bytes(offen) == int(128000 * RECORDING_HORIZON_S)
    befristet = [{"sample_rate_hz": 16000, "channels": 1, "remaining_s": 600}]
    assert recording_reserve_bytes(befristet) == 32000 * 600
    assert recording_reserve_bytes([]) == 0 and recording_reserve_bytes(None) == 0
    kaputt = [{"sample_rate_hz": None, "channels": 2}, {"channels": 1},
              {"sample_rate_hz": 16000, "channels": 1, "remaining_s": 60}]
    assert recording_reserve_bytes(kaputt) == 32000 * 60, "kaputte Eintraege nicht uebersprungen"
    print("T5 ok  (Sockel greift absolut wie anteilig; Reserve 128 000 Byte/s bei 32 kHz IQ, "
          "offenes Ende ueber den 2-h-Horizont, kaputte Eintraege uebersprungen)")

    # --- T6: Budgetentscheidung ---------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        total, free = disk_free(tmp)
        assert total > 0 and free >= 0, (total, free)
        floor = disk_floor_bytes(total)

        klein = check_budget(1024, 1.0, tmp)
        erwartet_ok = free >= floor + 2 * 1024 + TEMP_SLACK_BYTES
        assert klein["ok"] is erwartet_ok, klein
        assert klein["total_bytes"] == total and klein["floor_bytes"] == floor, klein

        # Riesiger Bedarf -> disk_need (Sockel ist frei, der Bedarf nicht).
        gross = check_budget(free + total, 1.0, tmp)
        assert gross["ok"] is False and gross["code"] == CODE_DISK_NEED, gross
        assert gross["need_bytes"] == (free + total) * 2 + TEMP_SLACK_BYTES, gross

        # Reserve allein kann den Ausschlag geben.
        mit_reserve = check_budget(1024, 1.0, tmp, reserve_bytes=free + total)
        assert mit_reserve["code"] == CODE_DISK_NEED, mit_reserve

        # Zeitfenster: 200 s Arbeit, 100 s bis zur Aufnahme -> abgelehnt.
        eng = check_budget(1024, 200.0, tmp, seconds_until_next_recording=100.0)
        assert eng["ok"] is False and eng["code"] == CODE_TIME_WINDOW, eng
        # Knapp darunter greift der Sicherheitsabstand ebenfalls.
        knapp = check_budget(1024, 100.0, tmp, seconds_until_next_recording=140.0)
        assert knapp["code"] == CODE_TIME_WINDOW, "TIME_MARGIN_S nicht wirksam"
        weit = check_budget(1024, 100.0, tmp, seconds_until_next_recording=3600.0)
        assert weit["code"] == CODE_OK, weit

        plan = plan_audspec(KIND_IQ, iq_frames, tmp)
        assert plan["artifact_bytes"] == 32188736 and plan["kind"] == KIND_IQ, plan
        assert plan["peak_memory_bytes"] == PEAK_MEMORY_BYTES[KIND_IQ], plan
        schrott = plan_audspec("stereo", 1000, tmp)
        assert schrott["ok"] is False and schrott["code"] == CODE_UNKNOWN_KIND, schrott
    print("T6 ok  (disk_floor/disk_need/time_window/ok unterscheidbar, Reserve wirkt, "
          "TIME_MARGIN_S greift, plan_audspec liefert Groesse/Speicher/Code)")

    # --- T7 (T-1610): Lagebeurteilung des Ablageplatzes ---------------------
    # Schwellen an einem gedachten 1-TiB-Volume: Sockel = 3 % = 30,72 GiB,
    # "eng" ab weniger als dem Fuenffachen, also 153,6 GiB verfuegbar.
    tib = 1024 ** 4
    floor_tib = disk_floor_bytes(tib)
    assert floor_tib == int(tib * DISK_FLOOR_FRACTION), floor_tib

    weit = space_status(tib, tib // 2)
    assert weit["code"] == SPACE_OK, weit
    assert weit["available_bytes"] == tib // 2 - floor_tib, weit

    eng = space_status(tib, floor_tib + int(2.0 * floor_tib))
    assert eng["code"] == SPACE_TIGHT, eng
    knapp_darueber = space_status(tib, floor_tib + int(SPACE_TIGHT_FACTOR * floor_tib) + 1)
    assert knapp_darueber["code"] == SPACE_OK, "Schwelle greift zu frueh"

    leer = space_status(tib, floor_tib)
    assert leer["code"] == SPACE_LOW and leer["available_bytes"] == 0, leer
    darunter = space_status(tib, floor_tib // 2)
    assert darunter["code"] == SPACE_LOW, darunter
    assert darunter["available_bytes"] < 0, "negativer Rest muss sichtbar bleiben"

    # Die Aufnahmereserve allein kann die Lage kippen - genau dafuer ist sie da.
    mit_res = space_status(tib, tib // 2, reserve_bytes=tib // 2 - floor_tib)
    assert mit_res["code"] == SPACE_LOW, mit_res

    with tempfile.TemporaryDirectory() as tmp2:
        echt = disk_report(tmp2)
        assert echt["code"] in (SPACE_OK, SPACE_TIGHT, SPACE_LOW), echt
        assert echt["total_bytes"] > 0 and echt["floor_bytes"] > 0, echt
    fehlt = disk_report(os.path.join("gibt", "es", "nicht"))
    assert fehlt["code"] == SPACE_LOW, "unerreichbares Volume muss SPACE_LOW ergeben"
    print(f"T7 ok  (space_ok/space_tight/space_low an den Schwellen; Reserve kippt die "
          f"Lage; unerreichbares Volume -> {SPACE_LOW})")

    # --- T8 (T-1607): Klassifikation --------------------------------------
    assert klasse_nbytes() == KLASSE_NBYTES
    assert klasse_nbytes() < audspec_nbytes_max(KIND_MONO), "Sidecar muss klein bleiben"
    assert estimate_klasse_seconds(0) == 0.0
    # Zwoelf Stunden mono bei 16 kHz: die Klassifikation darf dafuer keine
    # Minute veranschlagen, sonst waere die Terminschranke unbrauchbar.
    zwoelf_h = 12 * 3600 * 16000
    assert estimate_klasse_seconds(zwoelf_h) < 60.0, estimate_klasse_seconds(zwoelf_h)
    # ... und sie muss deutlich billiger sein als der Audspec derselben Datei.
    assert estimate_klasse_seconds(zwoelf_h) < estimate_build_seconds(KIND_MONO, zwoelf_h) / 5
    print(f"T8 ok  (Klassifikation: Sidecar {KLASSE_NBYTES // 1024} kiB unabhaengig von der "
          f"Laenge, 12 h mono in {estimate_klasse_seconds(zwoelf_h):.1f} s veranschlagt)")

    print("\n[prepare_budget] Selbsttest erfolgreich abgeschlossen.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Vorhersage und Budgetpruefung fuer den Aufbereitungs-Worker")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument("--kind", choices=(KIND_MONO, KIND_IQ), help="Art der Aufnahme")
    ap.add_argument("--frames", type=int, help="Framezahl der Aufnahme")
    ap.add_argument("--volume", default=".", help="Pfad auf dem Zielvolume")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0
    if not args.kind or args.frames is None:
        ap.print_help()
        return 1

    plan = plan_audspec(args.kind, args.frames, args.volume)
    print(f"Art:            {plan['kind']}")
    print(f"Artefakt:       {plan['artifact_bytes'] / 1024 ** 2:.2f} MiB "
          f"(Obergrenze {audspec_nbytes_max(args.kind) / 1024 ** 2:.2f} MiB)")
    print(f"Laufzeit:       {plan['est_seconds']:.1f} s")
    print(f"Speicherspitze: {plan['peak_memory_bytes'] / 1024 ** 2:.0f} MiB")
    print(f"Volume:         {args.volume}")
    print(f"  gesamt        {plan['total_bytes'] / 1024 ** 3:.1f} GiB")
    print(f"  frei          {plan['free_bytes'] / 1024 ** 3:.1f} GiB")
    print(f"  Sockel        {plan['floor_bytes'] / 1024 ** 3:.1f} GiB")
    print(f"  Bedarf        {plan['need_bytes'] / 1024 ** 2:.1f} MiB")
    print(f"Entscheidung:   {'JA' if plan['ok'] else 'NEIN'} ({plan['code']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
