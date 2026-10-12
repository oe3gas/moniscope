#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/selftest_param_sync.py

Prueft die T-703-Normalisierung (Geraeteparameter-Sync) OHNE laufenden
Server/Hardware: normalize_device_params() und params_changed() werden aus
waterfall.py als Modul importiert (sys.path-Ergaenzung um die Repo-Wurzel,
Muster analog tools/selftest_delete.py).

  T1  normalize_device_params: Lang-/Kurzform der Speed-Antwort (Annex C:
      LOW | NORMal | HIGH | MTIMe) beide auf die Kurzform LOW/NORM/HIGH/MTIM
      normalisiert; bereits kurze Antworten bleiben unveraendert.
  T2  normalize_device_params: BW-Antwortstring (auch mit Nachkommastellen)
      -> int Hz.
  T3  normalize_device_params: Squelch-Antwort 1/ON -> True, 0/OFF -> False
      (Gross-/Kleinschreibung und Leerraum werden toleriert).
  T4  normalize_device_params: Schwellen-Antwortstring -> float (inkl.
      negativer Werte).
  T5  normalize_device_params: Demodulations-Antwort wird gross geschrieben
      und getrimmt uebernommen.
  T6  params_changed: old=None -> True (erster Poll, kein Vergleich moeglich).
  T7  params_changed: identische normalisierte Dicts -> False.
  T8  params_changed: Aenderung in GENAU EINEM Feld wird je Einzelfeld
      erkannt (dem/band_hz/squelch_on/squelch_thr/speed einzeln durchprobiert).
  T9  normalize_device_params: Gain-Modus-Antwort MGC/mgc/FIX/FIXed -> "MGC"
      (das Geraet antwortet auf GCON:MODE? mit FIX|AUTO, Handbuch 4.79),
      AGC/AUTO/leer -> "AGC" (PLAN.md T-905, korrigiert in T-711).
  T10 normalize_device_params: MGC-Pegel-Antwortstring -> int, bei nicht
      auswertbarer Antwort None (PLAN.md T-905).
  T11 normalize_device_params: AFC-Antwort 1/ON -> True, 0/leer/fehlender
      Schluessel -> False (PLAN.md T-510).
  T12 normalize_device_params: freq_hz-Antwortstring -> int Hz, leer und
      fehlender Schluessel -> None (PLAN.md T-510).
  T13 normalize_device_params: freq_hz wird auf 100 Hz gerundet (PLAN.md
      T-510).
  T14 normalize_device_params: afc_wirksam nur bei afc_on UND Demodulation
      in FM/AM/PULS wahr (PLAN.md T-510).

Ausgabe je Test OK/FAIL, Exit-Code 0 nur bei komplett OK.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from waterfall import normalize_device_params, params_changed  # noqa: E402


def _raw(dem="FM", band_hz="120000", squelch_on="1", squelch_thr="2.0", speed="NORM",
         gain_mode="AGC", gain_mgc="50", afc_on="0", freq_hz="98500000") -> dict:
    return {"dem": dem, "band_hz": band_hz, "squelch_on": squelch_on,
            "squelch_thr": squelch_thr, "speed": speed,
            "gain_mode": gain_mode, "gain_mgc": gain_mgc,
            "afc_on": afc_on, "freq_hz": freq_hz}


def test_t1() -> None:
    # Langform -> Kurzform
    assert normalize_device_params(_raw(speed="NORMal"))["speed"] == "NORM"
    assert normalize_device_params(_raw(speed="MTIMe"))["speed"] == "MTIM"
    # bereits Kurzform bleibt unveraendert
    assert normalize_device_params(_raw(speed="LOW"))["speed"] == "LOW"
    assert normalize_device_params(_raw(speed="NORM"))["speed"] == "NORM"
    assert normalize_device_params(_raw(speed="HIGH"))["speed"] == "HIGH"
    assert normalize_device_params(_raw(speed="MTIM"))["speed"] == "MTIM"
    # Gross-/Kleinschreibung und Leerraum toleriert
    assert normalize_device_params(_raw(speed=" normal "))["speed"] == "NORM"
    print("T1 normalize_device_params Speed Lang-/Kurzform: OK")


def test_t2() -> None:
    assert normalize_device_params(_raw(band_hz="120000"))["band_hz"] == 120000
    assert normalize_device_params(_raw(band_hz="9000.0"))["band_hz"] == 9000
    assert isinstance(normalize_device_params(_raw(band_hz="600"))["band_hz"], int)
    print("T2 normalize_device_params BW-String -> int Hz: OK")


def test_t3() -> None:
    for on_value in ("1", "ON", "on", " 1 "):
        assert normalize_device_params(_raw(squelch_on=on_value))["squelch_on"] is True, on_value
    for off_value in ("0", "OFF", "off", ""):
        assert normalize_device_params(_raw(squelch_on=off_value))["squelch_on"] is False, off_value
    print("T3 normalize_device_params Squelch 1/ON->True, 0/OFF->False: OK")


def test_t4() -> None:
    assert normalize_device_params(_raw(squelch_thr="2.0"))["squelch_thr"] == 2.0
    assert normalize_device_params(_raw(squelch_thr="-5"))["squelch_thr"] == -5.0
    assert isinstance(normalize_device_params(_raw(squelch_thr="10"))["squelch_thr"], float)
    print("T4 normalize_device_params Schwelle -> float: OK")


def test_t5() -> None:
    assert normalize_device_params(_raw(dem=" fm "))["dem"] == "FM"
    assert normalize_device_params(_raw(dem="AM"))["dem"] == "AM"
    print("T5 normalize_device_params Demodulation Grossbuchstaben-Kurzform: OK")


def test_t6() -> None:
    new = normalize_device_params(_raw())
    assert params_changed(None, new) is True
    print("T6 params_changed old=None -> True: OK")


def test_t7() -> None:
    a = normalize_device_params(_raw())
    b = normalize_device_params(_raw())
    assert a is not b   # zwei unabhaengige Dicts, kein Identitaetsvergleich
    assert params_changed(a, b) is False
    print("T7 params_changed identische Dicts -> False: OK")


def test_t8() -> None:
    base = normalize_device_params(_raw())
    variants = {
        "dem": _raw(dem="AM"),
        "band_hz": _raw(band_hz="9000"),
        "squelch_on": _raw(squelch_on="0"),
        "squelch_thr": _raw(squelch_thr="20.0"),
        "speed": _raw(speed="HIGH"),
    }
    for field, raw in variants.items():
        changed = normalize_device_params(raw)
        assert params_changed(base, changed) is True, f"Aenderung in '{field}' nicht erkannt"
    print("T8 params_changed erkennt Aenderung je Einzelfeld: OK")


def test_t9() -> None:
    # Das Geraet antwortet auf GCON:MODE? mit FIX oder AUTO (Handbuch 4.79);
    # FIXed ist das Synonym fuer MGC, AUTO das fuer AGC.
    for value in ("MGC", "mgc", "FIX", "FIXed", "fix"):
        assert normalize_device_params(_raw(gain_mode=value))["gain_mode"] == "MGC", value
    for value in ("AGC", "AUTO", "auto", ""):
        assert normalize_device_params(_raw(gain_mode=value))["gain_mode"] == "AGC", value
    print("T9 normalize_device_params Gain-Modus MGC/FIX -> MGC, AGC/AUTO/leer -> AGC: OK")


def test_t10() -> None:
    assert normalize_device_params(_raw(gain_mgc="50"))["gain_mgc"] == 50
    assert normalize_device_params(_raw(gain_mgc="50.0"))["gain_mgc"] == 50
    assert normalize_device_params(_raw(gain_mgc="-30"))["gain_mgc"] == -30
    assert normalize_device_params(_raw(gain_mgc="110"))["gain_mgc"] == 110
    assert normalize_device_params(_raw(gain_mgc="Unsinn"))["gain_mgc"] is None
    print("T10 normalize_device_params MGC-Pegel -> int, bei Fehler None: OK")


def test_t11() -> None:
    for on_value in ("1", "ON", "on"):
        assert normalize_device_params(_raw(afc_on=on_value))["afc_on"] is True, on_value
    for off_value in ("0", "OFF", "", None):
        raw = _raw(afc_on=off_value) if off_value is not None else _raw()
        if off_value is None:
            del raw["afc_on"]
        assert normalize_device_params(raw)["afc_on"] is False, off_value
    print("T11 normalize_device_params AFC 1/ON->True, 0/leer/fehlend->False: OK")


def test_t12() -> None:
    assert normalize_device_params(_raw(freq_hz="125001500"))["freq_hz"] == 125001500
    assert normalize_device_params(_raw(freq_hz=""))["freq_hz"] is None
    raw = _raw()
    del raw["freq_hz"]
    assert normalize_device_params(raw)["freq_hz"] is None
    print("T12 normalize_device_params freq_hz-String -> int Hz, sonst None: OK")


def test_t13() -> None:
    assert normalize_device_params(_raw(freq_hz="125001543"))["freq_hz"] == 125001500
    print("T13 normalize_device_params freq_hz auf 100 Hz gerundet: OK")


def test_t14() -> None:
    for dem in ("FM", "AM", "PULS"):
        assert normalize_device_params(_raw(afc_on="1", dem=dem))["afc_wirksam"] is True, dem
    for dem in ("CW", "IQ", "USB"):
        assert normalize_device_params(_raw(afc_on="1", dem=dem))["afc_wirksam"] is False, dem
    assert normalize_device_params(_raw(afc_on="0", dem="FM"))["afc_wirksam"] is False
    print("T14 normalize_device_params afc_wirksam nur bei afc_on und FM/AM/PULS: OK")


def main() -> int:
    tests = [test_t1, test_t2, test_t3, test_t4, test_t5, test_t6, test_t7, test_t8,
             test_t9, test_t10, test_t11, test_t12, test_t13, test_t14]
    ok = True
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            print(f"{t.__name__} FAIL: {exc}")
            ok = False
        except Exception as exc:
            print(f"{t.__name__} FAIL (Ausnahme): {exc!r}")
            ok = False
    if ok:
        print("Selbsttest OK")
        return 0
    print("Selbsttest FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
