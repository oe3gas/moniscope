#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/classify_eval.py - Automatik (audio_classify) vs. Handlabel: Konfusions-
matrix, Trefferquoten, Merkmalswerte der Fehlklassifikationen und Perzentile
je Label/Merkmal - Kalibrierhilfe fuer audio_classify.THRESHOLDS (T-1007c).

Aufruf:
    python tools/classify_eval.py [--labels audio_klassen.json] [--chunks 3,5]
        [--dir <verzeichnis>] [--json <ausgabe>] [--selftest]

Eingabe ist eine Label-Datei im LabelStore-Format (Pfad -> Klassenkurztext,
siehe audio_klassen.json: Schluessel Root-relativ zu AUDIO_ROOT mit
Vorwaertsschraegstrichen, absolute Pfade werden ebenfalls akzeptiert). Ohne
--dir werden genau die in der Label-Datei genannten Dateien ausgewertet. Mit
--dir wird stattdessen das Verzeichnis rekursiv nach *.wav durchsucht; Dateien
ohne passenden Eintrag in der Label-Datei werden uebersprungen und gezaehlt.

Ganzdatei-Bewertung ruft classify_wav() auf (kein Sidecar-Schreiben,
ensure_klasse wird nicht verwendet). Chunk-Modus zerlegt jede Datei
blockweise (wave-Modul) in ueberlappungsfreie Stuecke von je L Sekunden (fuer
jeden Wert aus --chunks), Rest < L wird verworfen; jedes Stueck durchlaeuft
compute_features()/classify_features() einzeln - das bildet die tatsaechliche
Segmentlaenge im Betrieb nach. Stuecke mit rms_dbfs unter
THRESHOLDS["leer_rms_dbfs"] werden aus der Wertung genommen (Zaehler
"ausgeblendet (Pegel)"), ausser das Dateilabel ist LEER. Zusaetzlich werden
Stuecke mit rms_dbfs unter (Median-rms_dbfs aller Stuecke der Datei - 9 dB)
ausgeblendet (Zaehler "ausgeblendet (Sendepause)"), ausser das Dateilabel ist
LEER oder RAUSCHEN - erkennt Sendepausen innerhalb einer sonst aktiven
Aufnahme (z. B. CW-Wortpausen, RTTY-/PTT-Luecken).

Kein Gate-Werkzeug: Exit 0 auch bei schlechter Trefferquote (reine
Auswertehilfe fuer die manuelle Kalibrierung), Exit 2 nur bei einem
Werkzeugfehler (Label-Datei fehlt/kaputt, keine auswertbare Datei, ...).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from audio_classify import compute_features, classify_features, classify_wav, THRESHOLDS

KLASSEN = ["LEER", "RAUSCHEN", "CW", "DIGITAL", "SPRACHE", "UNKLAR"]


class ClassifyEvalError(Exception):
    """Werkzeugfehler (Exit 2) - keine Kalibrieraussage moeglich."""


# ---------------------------------------------------------------------------
# Label-Datei + Pfadaufloesung
# ---------------------------------------------------------------------------

def _load_labels(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise ClassifyEvalError(f"Label-Datei nicht lesbar: {path} ({exc})") from exc
    except ValueError as exc:
        raise ClassifyEvalError(f"Label-Datei ist kein gueltiges JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise ClassifyEvalError(f"Label-Datei {path} ist kein JSON-Objekt")
    return data


def _resolve_wav_path(key: str) -> str:
    if os.path.isabs(key):
        return os.path.normpath(key)
    return os.path.normpath(os.path.join(ROOT, *key.split("/")))


def _to_label_key(wav_path) -> str:
    abs_path = os.path.normpath(os.path.abspath(str(wav_path)))
    try:
        rel = os.path.relpath(abs_path, ROOT)
    except ValueError:
        return abs_path
    if rel.startswith(".."):
        return abs_path
    return rel.replace(os.sep, "/")


def _collect_entries(labels: dict, dir_filter: str | None):
    """(entries, n_unlabeled) - entries ist eine Liste (label_key, label, wav_path)."""
    if dir_filter is None:
        entries = [(key, klasse, _resolve_wav_path(key)) for key, klasse in labels.items()]
        return entries, 0

    wav_files = sorted(Path(dir_filter).rglob("*.wav"))
    entries = []
    n_unlabeled = 0
    for wav_path in wav_files:
        key = _to_label_key(wav_path)
        klasse = labels.get(key)
        if klasse is None:
            n_unlabeled += 1
            continue
        entries.append((key, klasse, str(wav_path)))
    return entries, n_unlabeled


# ---------------------------------------------------------------------------
# Ganzdatei-Bewertung
# ---------------------------------------------------------------------------

_GANZDATEI_KOPF = (
    f"{'Datei':<42} {'Label':<9} {'Klasse':<9} {'score':>5} {'syl':>4} "
    f"{'mod_hz':>6} {'prom':>5} {'flat':>4} {'n_l':>3} {'flux':>4} "
    f"{'per':>4} {'key':>4} {'occ_bw':>6} {'fsk':>5}"
)


def _ganzdatei_zeile(key: str, label: str, res: dict) -> str:
    feats = res["features"]
    kurz = key if len(key) <= 42 else "..." + key[-39:]
    return (
        f"{kurz:<42} {label:<9} {res['klasse']:<9} {res['voice_score']:5.3f} "
        f"{feats['syllable_ratio']:4.2f} {feats['mod_peak_hz']:6.1f} "
        f"{feats['mod_prominence']:5.2f} {feats['flatness']:4.2f} "
        f"{feats['n_lines']:3d} {feats['flux']:4.2f} "
        f"{feats['peak_energy_ratio']:4.2f} {feats['keying_rate']:4.1f} "
        f"{feats['occ_bw_hz']:6.0f} {feats['fsk_corr']:5.2f}"
    )


def _run_ganzdatei(entries):
    """Gibt (zeilen_text, json_rows, n_fehler) zurueck."""
    zeilen = [_GANZDATEI_KOPF]
    json_rows = []
    n_fehler = 0
    for key, label, wav_path in entries:
        try:
            res = classify_wav(wav_path)
        except Exception as exc:  # Werkzeug soll robust ueber viele Dateien laufen
            n_fehler += 1
            zeilen.append(f"{key:<42} FEHLER: {exc}")
            continue
        zeilen.append(_ganzdatei_zeile(key, label, res))
        feats = res["features"]
        json_rows.append({
            "file": key, "label": label, "klasse": res["klasse"],
            "score": res["voice_score"], "syl": feats["syllable_ratio"],
            "mod_hz": feats["mod_peak_hz"], "prom": feats["mod_prominence"],
            "flat": feats["flatness"], "n_lines": feats["n_lines"], "flux": feats["flux"],
            "per": feats["peak_energy_ratio"], "key": feats["keying_rate"],
            "occ_bw": feats["occ_bw_hz"], "fsk": feats["fsk_corr"],
        })
    return zeilen, json_rows, n_fehler


# ---------------------------------------------------------------------------
# Chunk-Modus
# ---------------------------------------------------------------------------

def _iter_chunks(wav_path: str, chunk_s: float):
    """(offset_s, samples, sample_rate_hz) je vollem Stueck von chunk_s
    Sekunden, blockweise ueber das wave-Modul gelesen, Rest < chunk_s
    verworfen."""
    with wave.open(wav_path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        if channels != 1 or sampwidth != 2:
            raise ValueError(
                f"nicht unterstuetztes WAV-Format: {channels} Kanal(e), {sampwidth * 8}-Bit"
            )
        sample_rate_hz = wf.getframerate()
        chunk_n = int(round(chunk_s * sample_rate_hz))
        if chunk_n <= 0 or sample_rate_hz <= 0:
            return
        offset_s = 0.0
        while True:
            raw = wf.readframes(chunk_n)
            n_read = len(raw) // 2
            if n_read < chunk_n:
                break
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
            yield offset_s, samples, sample_rate_hz
            offset_s += chunk_s


def _percentile_stats(values) -> dict:
    arr = np.asarray(values, dtype=np.float64)
    return {
        "min": float(np.min(arr)), "p10": float(np.percentile(arr, 10)),
        "median": float(np.median(arr)), "p90": float(np.percentile(arr, 90)),
        "max": float(np.max(arr)),
    }


def _run_chunk_length(entries, chunk_s: float) -> dict:
    confusion: dict = {}
    n_ausgeblendet_pegel = 0
    n_ausgeblendet_sendepause = 0
    n_chunks_total = 0
    misklass = []
    feature_values: dict = {}  # label -> merkmal -> [werte]
    n_sprache_treffer = 0
    n_sprache_schwach = 0

    for key, label, wav_path in entries:
        try:
            chunk_iter = list(_iter_chunks(wav_path, chunk_s))
        except (OSError, ValueError) as exc:
            print(f"uebersprungen ({exc}): {key}", file=sys.stderr)
            continue

        chunk_data = []
        for offset_s, samples, sample_rate_hz in chunk_iter:
            features = compute_features(samples, sample_rate_hz)
            chunk_data.append((offset_s, features))
        n_chunks_total += len(chunk_data)
        if not chunk_data:
            continue

        datei_rms_median = float(np.median([f["rms_dbfs"] for _, f in chunk_data]))
        sendepause_schwelle = datei_rms_median - 9.0

        for offset_s, features in chunk_data:
            if features["rms_dbfs"] < THRESHOLDS["leer_rms_dbfs"] and label != "LEER":
                n_ausgeblendet_pegel += 1
                continue
            if (features["rms_dbfs"] < sendepause_schwelle
                    and label not in ("LEER", "RAUSCHEN")):
                n_ausgeblendet_sendepause += 1
                continue

            klasse, _score, reasons, sicherheit = classify_features(features, chunk_s)

            confusion.setdefault(label, {k: 0 for k in KLASSEN})
            confusion[label][klasse] = confusion[label].get(klasse, 0) + 1

            feature_values.setdefault(label, {})
            for m, v in features.items():
                feature_values[label].setdefault(m, []).append(v)

            if klasse == label == "SPRACHE":
                n_sprache_treffer += 1
                if sicherheit == "schwach":
                    n_sprache_schwach += 1

            if klasse != label:
                misklass.append({
                    "file": key, "offset_s": offset_s, "label": label,
                    "klasse": klasse, "sicherheit": sicherheit,
                    "reasons": reasons, "features": features,
                })

    hit_rate = {}
    unklar_rate = {}
    n_treffer_gesamt = 0
    n_gesamt = 0
    for label, row in confusion.items():
        row_total = sum(row.values())
        n_gesamt += row_total
        treffer = row.get(label, 0)
        n_treffer_gesamt += treffer
        hit_rate[label] = treffer / row_total if row_total else 0.0
        unklar_rate[label] = row.get("UNKLAR", 0) / row_total if row_total else 0.0
    hit_rate["GESAMT"] = n_treffer_gesamt / n_gesamt if n_gesamt else 0.0

    perzentile = {
        label: {m: _percentile_stats(vals) for m, vals in merkmale.items() if vals}
        for label, merkmale in feature_values.items()
    }

    schwach_anteil_sprache = n_sprache_schwach / n_sprache_treffer if n_sprache_treffer else None

    return {
        "chunk_s": chunk_s,
        "n_chunks_total": n_chunks_total,
        "n_ausgeblendet_pegel": n_ausgeblendet_pegel,
        "n_ausgeblendet_sendepause": n_ausgeblendet_sendepause,
        "confusion": confusion,
        "hit_rate": hit_rate,
        "unklar_rate": unklar_rate,
        "misclassifications": misklass,
        "percentiles": perzentile,
        "n_sprache_treffer": n_sprache_treffer,
        "n_sprache_schwach": n_sprache_schwach,
        "schwach_anteil_sprache": schwach_anteil_sprache,
    }


# ---------------------------------------------------------------------------
# Textausgabe Chunk-Ergebnis
# ---------------------------------------------------------------------------

def _print_chunk_result(result: dict) -> None:
    chunk_s = result["chunk_s"]
    print(f"\n=== Chunk-Laenge {chunk_s:g} s ===")
    print(
        f"Stuecke gesamt: {result['n_chunks_total']}  "
        f"ausgeblendet (Pegel): {result['n_ausgeblendet_pegel']}  "
        f"ausgeblendet (Sendepause): {result['n_ausgeblendet_sendepause']}"
    )

    labels_sorted = sorted(result["confusion"])
    kopf = f"{'Label':<10}" + "".join(f"{k:>10}" for k in KLASSEN)
    print("\nKonfusionsmatrix (Zeile=Label, Spalte=Klasse):")
    print(kopf)
    for label in labels_sorted:
        row = result["confusion"][label]
        print(f"{label:<10}" + "".join(f"{row.get(k, 0):>10}" for k in KLASSEN))

    print("\nTrefferquote:")
    for label in labels_sorted:
        print(f"  {label:<10} {result['hit_rate'][label] * 100:5.1f} %")
    print(f"  {'GESAMT':<10} {result['hit_rate']['GESAMT'] * 100:5.1f} %")

    print("\nUNKLAR-Quote je Label:")
    for label in labels_sorted:
        print(f"  {label:<10} {result['unklar_rate'][label] * 100:5.1f} %")

    if result["n_sprache_treffer"]:
        print(
            f"\nAnteil 'schwach' unter den SPRACHE-Treffern: "
            f"{result['schwach_anteil_sprache'] * 100:5.1f} % "
            f"({result['n_sprache_schwach']}/{result['n_sprache_treffer']})"
        )
    else:
        print("\nAnteil 'schwach' unter den SPRACHE-Treffern: keine SPRACHE-Treffer")

    print(f"\nFehlklassifikationen ({len(result['misclassifications'])}):")
    for m in result["misclassifications"]:
        print(
            f"  {m['file']} @ {m['offset_s']:.1f}s  Label={m['label']} -> "
            f"Klasse={m['klasse']} (sicherheit={m['sicherheit']})"
        )
        for grund in m["reasons"]:
            print(f"      {grund}")
        feats = ", ".join(f"{k}={v}" for k, v in m["features"].items())
        print(f"      Merkmale: {feats}")

    print("\nPerzentile je Label/Merkmal (min / p10 / median / p90 / max):")
    for label in labels_sorted:
        merkmale = result["percentiles"].get(label, {})
        print(f"  {label}:")
        for m in sorted(merkmale):
            stats = merkmale[m]
            print(
                f"    {m:<18} {stats['min']:8.3f} {stats['p10']:8.3f} "
                f"{stats['median']:8.3f} {stats['p90']:8.3f} {stats['max']:8.3f}"
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_chunks(arg: str):
    werte = []
    for teil in arg.split(","):
        teil = teil.strip()
        if not teil:
            continue
        werte.append(float(teil))
    return werte


def run_evaluation(labels: dict, dir_filter, chunk_lengths) -> dict:
    entries, n_unlabeled = _collect_entries(labels, dir_filter)
    if not entries:
        raise ClassifyEvalError("keine auswertbaren (gelabelten) Dateien gefunden")

    ganzdatei_zeilen, ganzdatei_json, n_fehler = _run_ganzdatei(entries)

    chunk_results = [_run_chunk_length(entries, L) for L in chunk_lengths]

    return {
        "n_labels": len(labels),
        "n_entries": len(entries),
        "n_unlabeled_skipped": n_unlabeled,
        "n_ganzdatei_fehler": n_fehler,
        "ganzdatei_zeilen": ganzdatei_zeilen,
        "ganzdatei_json": ganzdatei_json,
        "chunk_results": chunk_results,
    }


def _print_report(report: dict) -> None:
    print(
        f"Labels: {report['n_labels']}  ausgewertete Dateien: {report['n_entries']}  "
        f"ohne Label uebersprungen: {report['n_unlabeled_skipped']}"
    )
    if report["n_ganzdatei_fehler"]:
        print(f"Ganzdatei-Fehler: {report['n_ganzdatei_fehler']}", file=sys.stderr)

    print("\n=== Ganzdatei-Bewertung ===")
    for zeile in report["ganzdatei_zeilen"]:
        print(zeile)

    for result in report["chunk_results"]:
        _print_chunk_result(result)


def main() -> int:
    ap = argparse.ArgumentParser(description="Klassifikator vs. Handlabel (T-1007c)")
    ap.add_argument("--labels", default="audio_klassen.json", help="Label-Datei (LabelStore-Format)")
    ap.add_argument("--chunks", default="", help="Kommagetrennte Chunk-Laengen in Sekunden, z.B. 3,5")
    ap.add_argument("--dir", default=None, help="nur Dateien unterhalb dieses Verzeichnisses")
    ap.add_argument("--json", default=None, help="zusaetzlich als JSON in diese Datei schreiben")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    try:
        labels = _load_labels(args.labels)
        chunk_lengths = _parse_chunks(args.chunks) if args.chunks else []
        report = run_evaluation(labels, args.dir, chunk_lengths)
    except ClassifyEvalError as exc:
        print(f"Werkzeugfehler: {exc}", file=sys.stderr)
        return 2

    _print_report(report)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return 0


# ---------------------------------------------------------------------------
# Selbsttest (python tools/classify_eval.py --selftest, ohne Hardware)
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile

    from audio_classify import _make_cw_signal, _make_sprache_signal, _write_test_wav

    with tempfile.TemporaryDirectory() as tmp:
        sr = 16000
        rng = np.random.default_rng(4242)

        cw = _make_cw_signal(sr, 12.0, rng)
        p_cw = os.path.join(tmp, "eval_cw.wav")
        _write_test_wav(p_cw, cw, sr)

        sprache = _make_sprache_signal(sr, 12.0, rng)
        p_sprache = os.path.join(tmp, "eval_sprache.wav")
        _write_test_wav(p_sprache, sprache, sr)

        labels = {p_cw: "CW", p_sprache: "SPRACHE"}

        report = run_evaluation(labels, None, [3.0])
        assert report["n_entries"] == 2, report["n_entries"]

        result = report["chunk_results"][0]
        assert result["n_chunks_total"] == 8, result["n_chunks_total"]

        confusion = result["confusion"]
        assert set(confusion) <= {"CW", "SPRACHE"}, confusion
        for label, row in confusion.items():
            assert set(row) == set(KLASSEN), (label, row)
            assert sum(row.values()) == 4, (label, row)

        json.dumps(result["misclassifications"])
        json.dumps(report["ganzdatei_json"])

        print(
            f"Selftest-Report: n_entries={report['n_entries']} "
            f"n_chunks_total={result['n_chunks_total']} confusion={confusion}"
        )

    print("Selftest OK")


if __name__ == "__main__":
    sys.exit(main())
