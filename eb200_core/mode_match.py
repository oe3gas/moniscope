#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/mode_match.py

Vergleicht gemessene Kennwerte (Ergebnis-dict von tools/eye_diagram.py,
FSK-Pfad) gegen die Kandidatentabelle aus eb200_core/mode_table.py
(Meilenstein 17, T-1703). Reine Vergleichsfunktion: kein Datei-, Netz- oder
Zeitzugriff, kein numpy, kein Zustand - deshalb ohne WAV-Datei vollstaendig
mit Kennwerte-dicts von Hand pruefbar (siehe --selftest).

Leitsatz des Meilensteins (PLAN.md): die Anzeige behauptet nie mehr, als die
Messung hergibt. Deshalb gibt es hier KEINE Rangfolge zwischen Treffern und
KEIN Feld, das sich als Wahrscheinlichkeit lesen liesse (kein score, rank,
best, probability, keine Prozentangabe einer "Trefferguete"). Ein Befund
traegt nur die gemessene Abweichung und die Toleranz, gegen die sie geprueft
wurde - mehrere Treffer nebeneinander sind der Normalfall, keiner "gewinnt".

Aufloesung der defaults (T-1702 hat das beim Laden bewusst offen gelassen):
passiert HIER, je Zeile und je Kriterium, nicht in mode_table.py - sonst
staenden zwei Wahrheiten ueber aufgeloeste Werte im Speicher.

Frequenztoleranzen (shift_hz, tone_spacing_hz) werden um freq_bin_hz
aufgeweitet (T-1701a): die Peaklagen aus tools/eye_diagram.py sind
Bin-Mitten des Momentanfrequenz-Histogramms, jede daraus abgeleitete
Frequenzdifferenz traegt also einen Quantisierungsfehler von bis zu einem
ganzen Bin. Fehlt freq_bin_hz in den Kennwerten (aeltere Aufrufer), wird 0.0
angenommen - keine Aufweitung, nicht "nicht auswertbar": freq_bin_hz ist ein
Toleranzparameter, kein Messwert, dessen Fehlen ein Kriterium unauswertbar
machen wuerde.

keying-Abbildung (deutsche Messwerte -> sprachneutrale Tabellenwerte) steht
NUR hier: die Tabelle bleibt sprachneutral (T-1702), die Messung behaelt ihre
gewachsenen Werte "getastet"/"dauersignal" (ID-Stabilitaetsregel).
"""
from __future__ import annotations

import os
import sys

# Erlaubt sowohl "python -m eb200_core.mode_match" als auch den direkten
# Skriptaufruf "python eb200_core/mode_match.py --selftest" (Selbsttest-
# Konvention dieses Projekts, siehe eb200_core/path_sandbox.py): beim
# Direktaufruf fehlt sonst das Repo-Root im Suchpfad fuer den mode_table-Import.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reihenfolge ist zugleich die Pruefreihenfolge je Zeile - level_count zuerst,
# weil es das billigste und haerteste Kriterium ist.
_CRITERIA_FIELDS = ("level_count", "shift_hz", "baud", "tone_spacing_hz",
                    "spacing_cv_max", "keying")

# Harte Kriterien: stimmen sie nicht, faellt die Zeile GANZ heraus, auch fuer
# "near". Ein Zweipegelsignal darf nie als "beinahe DMR" erscheinen.
_HARD_CRITERIA = ("level_count", "keying")

# Abbildung Messwert (tools/eye_diagram.py, detect_bursts()) -> Tabellenwert
# (modes/fsk.json, sprachneutral). Nur hier, siehe Moduldoku.
_KEYING_MAP = {"getastet": "keyed", "dauersignal": "continuous"}


def _get_measured(name: str, kennwerte: dict):
    """Liefert den fuer ein Kriterium gebrauchten Messwert oder None, wenn er
    fehlt bzw. sich nicht auswerten laesst. shift_hz vergleicht gegen den
    GESAMTHUB (2*dev_hz, siehe tools/eye_diagram.py tone_metrics()), nicht
    gegen dev_hz selbst."""
    if name == "level_count":
        return kennwerte.get("level_count")
    if name == "shift_hz":
        dev_hz = kennwerte.get("dev_hz")
        return None if dev_hz is None else 2.0 * dev_hz
    if name == "baud":
        return kennwerte.get("baud")
    if name == "tone_spacing_hz":
        return kennwerte.get("tone_spacing_mean_hz")
    if name == "spacing_cv_max":
        return kennwerte.get("tone_spacing_cv")
    if name == "keying":
        return _KEYING_MAP.get(kennwerte.get("mode"))
    return None


def _check_criterion(name: str, row: dict, defaults: dict, measured, bin_widen: float,
                     near_factor: float):
    """Prueft ein einzelnes Kriterium. Liefert (stufe, info): stufe ist
    "match", "near" oder None (ausserhalb auch der doppelten Toleranz).
    info ist das Detaildict fuer den Befund - Feldnamen ASCII, kein score/
    rank/best/probability."""
    if name == "level_count":
        soll = row["level_count"]
        info = {"name": name, "measured": measured, "expected": soll}
        return ("match" if measured == soll else None), info

    if name == "keying":
        soll = row["keying"]
        info = {"name": name, "measured": measured, "expected": soll}
        return ("match" if measured == soll else None), info

    if name in ("shift_hz", "tone_spacing_hz"):
        soll = row[name]
        # tone_spacing_hz teilt sich die Toleranz mit shift_hz - beides sind
        # absolute Frequenzabstaende aus demselben Histogramm.
        tol = row.get("shift_tol_hz", defaults.get("shift_tol_hz"))
        tol_eff = tol + bin_widen
        abweichung = abs(measured - soll)
        info = {"name": name, "measured": measured, "expected": soll,
                "tolerance": tol_eff, "deviation": abweichung}
        if abweichung <= tol_eff:
            return "match", info
        if abweichung <= tol_eff * near_factor:
            return "near", info
        return None, info

    if name == "baud":
        soll = row["baud"]
        tol_rel = row.get("baud_tol_rel", defaults.get("baud_tol_rel"))
        abweichung_rel = abs(measured - soll) / soll
        info = {"name": name, "measured": measured, "expected": soll,
                "tolerance_rel": tol_rel, "deviation_rel": abweichung_rel}
        if abweichung_rel <= tol_rel:
            return "match", info
        if abweichung_rel <= tol_rel * near_factor:
            return "near", info
        return None, info

    if name == "spacing_cv_max":
        grenze = row["spacing_cv_max"]
        info = {"name": name, "measured": measured, "expected": grenze}
        # Einseitig: der Grenzwert selbst ist die Toleranz, near_factor
        # weitet ihn auf statt eine zweite Groesse aufzuweiten.
        if measured <= grenze:
            return "match", info
        if measured <= grenze * near_factor:
            return "near", info
        return None, info

    raise ValueError(f"unknown criterion {name!r}")


def _match_row(row: dict, defaults: dict, kennwerte: dict):
    """Liefert einen Befund fuer eine Tabellenzeile oder None (kein Treffer
    ODER Zeile nicht auswertbar - beides gleich zu behandeln, siehe
    Moduldoku)."""
    criteria_present = [c for c in _CRITERIA_FIELDS if c in row]
    if not criteria_present:
        return None      # T-1702 validiert das beim Laden bereits weg

    near_factor = defaults.get("near_factor", 1.0)
    bin_widen = kennwerte.get("freq_bin_hz") or 0.0

    stufen = []
    infos = []
    for name in criteria_present:
        measured = _get_measured(name, kennwerte)
        if measured is None:
            return None      # Kennwert fehlt -> ganze Zeile uebersprungen
        stufe, info = _check_criterion(name, row, defaults, measured, bin_widen, near_factor)
        if stufe is None and name in _HARD_CRITERIA:
            return None      # hartes Kriterium verfehlt -> auch kein "near"
        stufen.append(stufe)
        infos.append(info)

    if any(s is None for s in stufen):
        return None
    confidence = "match" if all(s == "match" for s in stufen) else "near"
    return {
        "mode_id": row["id"],
        "label": row["label"],
        "confidence": confidence,
        "criteria": infos,
        "note": row.get("note", ""),
    }


def match_fsk(kennwerte: dict, tabelle: dict | None) -> list[dict]:
    """Vergleicht kennwerte (Ergebnis-dict von tools/eye_diagram.py, FSK-
    Pfad) gegen tabelle (Ergebnis von eb200_core.mode_table.load_fsk_table()).

    Wirft nie. tabelle=None, eine leere modes-Liste oder kein Treffer liefern
    alle dieselbe gueltige Antwort: eine leere Liste.

    Sortierung: erst alle "match", dann alle "near", INNERHALB jeder Gruppe
    alphabetisch nach mode_id. Bewusst NICHT nach Guete/Abweichung sortiert -
    das schmuggelte eine Rangfolge zwischen gleichrangigen Kandidaten ein,
    die es laut Leitsatz des Meilensteins nicht gibt: mehrere Treffer
    nebeneinander sind der Normalfall, keiner "gewinnt". Wer das hier
    "verbessert" und nach Abweichung sortiert, unterlaeuft genau das.
    """
    if not tabelle:
        return []
    modes = tabelle.get("modes")
    if not isinstance(modes, list) or not modes:
        return []
    defaults = tabelle.get("defaults") or {}

    treffer = []
    for row in modes:
        befund = _match_row(row, defaults, kennwerte)
        if befund is not None:
            treffer.append(befund)

    reihenfolge = {"match": 0, "near": 1}
    treffer.sort(key=lambda b: (reihenfolge[b["confidence"]], b["mode_id"]))
    return treffer


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

_VERBOTENE_FELDER = {"score", "rank", "best", "probability"}


def _self_test() -> None:
    from eb200_core.mode_table import load_fsk_table

    tabelle, fehler = load_fsk_table()
    assert fehler == [], f"Tabelle laedt nicht: {fehler}"

    # M1: die echte Erprobungsmessung (T-1701/T-1701a) gegen die ausgelieferte
    # Tabelle - genau zwei Treffer, beide "match", 425 vor 450 (alphabetisch,
    # zufaellig auch numerisch aufsteigend).
    k1 = {"level_count": 2, "dev_hz": 218.9, "baud": 49.25,
          "tone_spacing_cv": 0.0, "freq_bin_hz": 9.95, "mode": "dauersignal"}
    r1 = match_fsk(k1, tabelle)
    assert [b["mode_id"] for b in r1] == ["rtty_kommerziell_425_50", "rtty_kommerziell_450_50"], r1
    assert all(b["confidence"] == "match" for b in r1), r1

    # M2: Amateur-RTTY, exakt auf den Tabellenwerten - genau ein Treffer.
    k2 = {"level_count": 2, "dev_hz": 85.0, "baud": 45.4,
          "tone_spacing_cv": 0.0, "freq_bin_hz": 9.95, "mode": "dauersignal"}
    r2 = match_fsk(k2, tabelle)
    assert [b["mode_id"] for b in r2] == ["rtty_amateur_170_4545"], r2

    # M3: Kennwerte, die zu keiner Zeile passen - leere Liste, keine Ausnahme.
    k3 = {"level_count": 2, "dev_hz": 150.0, "baud": 300.0,
          "tone_spacing_cv": 0.0, "freq_bin_hz": 9.95, "mode": "dauersignal"}
    assert match_fsk(k3, tabelle) == []

    # M4: keine Tabelle -> leere Liste.
    assert match_fsk(k1, None) == []

    # M5: eine Zeile fordert tone_spacing_hz, die Kennwerte liefern es nicht
    # (aeltere Messung ohne T-1705-Kammdetektor) - Zeile wird uebersprungen,
    # erscheint auch nicht als Verfehlung.
    tabelle5 = {
        "format": "eb200-modes-fsk-v1", "table_version": 1,
        "defaults": {"shift_tol_hz": 15.0, "baud_tol_rel": 0.06,
                     "spacing_cv_max": 0.05, "near_factor": 2.0},
        "modes": [{"id": "braucht_kamm", "label": "X", "tone_spacing_hz": 250.0}],
    }
    k5 = {"level_count": 8, "dev_hz": 875.0, "baud": 125.0,
          "tone_spacing_cv": 0.02, "freq_bin_hz": 9.95, "mode": "dauersignal"}
    assert "tone_spacing_mean_hz" not in k5
    assert match_fsk(k5, tabelle5) == []

    # M6: hartes Kriterium. Zweipegel-Kennwerte mit DMR-nahen Hub-/Baudwerten
    # (dmr_4800: level_count=4, shift_hz=3888, baud=4800) - kein Befund, auch
    # nicht "near", weil level_count nicht passt.
    k6 = {"level_count": 2, "dev_hz": 1944.0, "baud": 4800.0,
          "tone_spacing_cv": 0.0, "freq_bin_hz": 9.95, "mode": "dauersignal"}
    r6 = match_fsk(k6, tabelle)
    assert not any(b["mode_id"] == "dmr_4800" for b in r6), r6

    # M7/M8: near und die Wirkung der Bin-Aufweitung (T-1701a) am selben Fall.
    # rtty_kommerziell_425_50 (shift_tol_hz=15, near_factor=2.0 aus defaults):
    # Hub 445 Hz -> Abweichung 20 Hz von 425 Hz.
    #   ohne Aufweitung (freq_bin_hz=0.0):   tol_eff=15.0   -> ausserhalb der
    #     einfachen (20>15), innerhalb der doppelten (20<=30) -> "near"
    #   mit Aufweitung   (freq_bin_hz=9.95): tol_eff=24.95  -> 20<=24.95
    #     -> "match"
    # level_count und baud sind in beiden Faellen exakt, damit ausschliesslich
    # shift_hz die Gesamtstufe bestimmt.
    k78_basis = {"level_count": 2, "dev_hz": 222.5, "baud": 50.0,
                "tone_spacing_cv": 0.0, "mode": "dauersignal"}

    k7 = dict(k78_basis, freq_bin_hz=0.0)
    r7 = match_fsk(k7, tabelle)
    treffer7 = [b for b in r7 if b["mode_id"] == "rtty_kommerziell_425_50"]
    assert len(treffer7) == 1 and treffer7[0]["confidence"] == "near", r7

    k8 = dict(k78_basis, freq_bin_hz=9.95)
    r8 = match_fsk(k8, tabelle)
    treffer8 = [b for b in r8 if b["mode_id"] == "rtty_kommerziell_425_50"]
    assert len(treffer8) == 1 and treffer8[0]["confidence"] == "match", r8

    # M9: keine Rangfelder, Sortierung alphabetisch je Konfidenzstufe.
    for befund in r1 + r2 + r6 + treffer7 + treffer8:
        verbotene = _VERBOTENE_FELDER & set(befund)
        assert not verbotene, f"{befund['mode_id']}: verbotene Felder {verbotene}"
    ids_r1 = [b["mode_id"] for b in r1]
    assert ids_r1 == sorted(ids_r1), f"M9: nicht alphabetisch sortiert: {ids_r1}"

    print("[mode_match] Selbsttest OK")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Vergleich gemessener FSK-Kennwerte gegen modes/fsk.json")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
