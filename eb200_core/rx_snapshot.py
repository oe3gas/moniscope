#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/rx_snapshot.py

Schnappschuss der Empfangsparameter zum Aufnahmestart (T-917). Die Werte
landen im Sidecar jeder Audio-/IQ-Aufnahme und sind die Voraussetzung
dafuer, den Pegel einer IQ-Aufnahme spaeter in dBµV umzurechnen (T-919) -
ohne Verstaerkungsmodus, Daempfung und Referenzpegel ist jede absolute
Pegelangabe geraten.

Zwei Festlegungen, die das ganze Modul praegen:

  1. STRIKT LESEND. Es wird kein einziger setzender SCPI-Befehl abgesetzt,
     auch kein FUNCtion-Wechsel vor DATA?. Der Schnappschuss darf den
     laufenden Betrieb nicht veraendern - eine Aufnahme, die ihre eigenen
     Messbedingungen umstellt, waere wertlos. Kann ein Wert deshalb nicht
     gelesen werden, ist er None.

  2. KEIN EIGENER SCPI-CODE. Der Aufrufer reicht seine vorhandene
     Query-Funktion herein (waterfall.py: self.scpi.query, mscan_run.py:
     dito). Damit bleibt dieses Modul wie der Rest von eb200_core ohne
     Geraete-/Netzwerkzugriff und ohne Hardware per Selbsttest pruefbar.

Werte, die der Aufrufer ohnehin schon fuehrt (waterfall.py kennt
Verstaerkung und Squelch aus seinem eigenen Zustand), werden ueber
'known' hereingegeben und NICHT noch einmal abgefragt: jede vermiedene
Abfrage ist eine Gelegenheit weniger fuer den Antwortversatz auf der
SCPI-Verbindung (siehe _scpi_resync in waterfall.py).

Fehlertoleranz: jede Abfrage steht in ihrem eigenen try/except. Ein
Geraet, das einen Befehl nicht kennt, kostet genau dieses eine Feld -
nie die Aufnahme.

Feldnamen sind ASCII und sprachneutral (Projektregel fuer persistierte
Werte). Bestandsaufnahmen ohne diese Bloecke bleiben gueltig; Lesende
verwenden meta.get("rx") bzw. meta.get("device") und zeigen sonst "n/a".
"""

from __future__ import annotations

# SCPI-Abfragen des Schnappschusses. Reihenfolge = Reihenfolge im Sidecar.
# Die Kommentare nennen die Handbuchstelle (Annex C).
SCPI_ATT_STATE = "INP:ATT:STAT?"      # Annex C: INPut:ATTenuation:STATe
SCPI_ATT_AUTO = "INP:ATT:AUTO?"       # Annex C: INPut:ATTenuation:AUTO
SCPI_REF_LEVEL = "VOLT:AC:RANG?"      # Annex C: [SENSe:]VOLTage:AC:RANGe, dBµV
# Das SENSe:-Praefix ist am Geraet NICHT optional, obwohl Annex C es in
# Klammern setzt: "DATA?" allein liefert eine Leerzeile und den Fehler
# -109 "Missing parameter" in der Geraete-Queue (Befund OE3GAS 2026-09-06,
# Firmware V02.74). Die uebrigen Abfragen dieses Moduls funktionieren in
# Kurzform - nur diese eine nicht. FUNCtion steht dabei bereits auf
# "VOLT:AC" (Parameter-Schnappschuss desselben Tages); wir setzen sie
# trotzdem nicht, siehe Festlegung 1 in der Moduldoku.
SCPI_LEVEL = "SENSe:DATA?"            # Annex C: [SENSe:]DATA?, dBµV
SCPI_AFC = "FREQ:AFC?"                # Annex C: [SENSe:]FREQuency:AFC
SCPI_SQUELCH_STATE = "OUTP:SQU:STAT?"  # Annex C: OUTPut:SQUelch[:STATe]
SCPI_SQUELCH_THR = "OUTP:SQU:THR?"    # Annex C: OUTPut:SQUelch:THReshold, dBµV
SCPI_GAIN_MODE = "GCON:MODE?"         # Annex C: [SENSe:]GCONtrol:MODE
SCPI_GAIN_MGC = "GCON:MGC?"           # Annex C: [SENSe:]GCONtrol[:MGC], dBµV
SCPI_IDN = "*IDN?"                    # Annex C: Seriennummer + Softwarestand
SCPI_OPT = "*OPT?"                    # Annex C: Hardware-/Softwareoptionen

RX_KEYS = (
    "gain_mode", "gain_mgc_dbuv", "att_state", "att_auto",
    "ref_level_dbuv", "level_dbuv", "afc", "squelch_on", "squelch_thr_dbuv",
)
DEVICE_KEYS = ("idn", "manufacturer", "model", "serial", "firmware", "options")


def _as_bool(raw):
    """SCPI-Wahrheitswert -> bool. Das Geraet antwortet je nach Befehl mit
    '1'/'0' oder 'ON'/'OFF'; alles andere ist unauswertbar -> None."""
    text = str(raw).strip().upper()
    if text in ("1", "ON", "TRUE"):
        return True
    if text in ("0", "OFF", "FALSE"):
        return False
    return None


def _as_float(raw):
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def normalize_gain_mode(raw):
    """GCON:MODE? -> "MGC" oder "AGC".

    Das Geraet antwortet laut Handbuch 4.79 mit FIX oder AUTO, NICHT mit
    MGC/AGC (FIXed ist das Synonym fuer MGC, AUTO das fuer AGC). Dieselbe
    Zuordnung wie normalize_device_params() in waterfall.py - hier noch
    einmal, weil eb200_core keinen Server importieren darf. Anders als
    dort liefert eine unauswertbare Antwort None statt "AGC": im Sidecar
    ist "nicht ermittelt" etwas anderes als "AGC", und genau daran haengt
    spaeter die Frage, ob eine Pegelkalibrierung ueberhaupt gueltig ist.
    """
    text = str(raw).strip().upper()
    if text.startswith(("MGC", "FIX")):
        return "MGC"
    if text.startswith(("AGC", "AUTO")):
        return "AGC"
    return None


def _try(query, command):
    """Eine Abfrage, gekapselt: Rueckgabe der Rohantwort oder None. Fehler
    werden bewusst geschluckt - der Schnappschuss ist Beiwerk der Aufnahme
    und darf sie nie verhindern."""
    try:
        return query(command)
    except Exception:
        return None


def build_rx_snapshot(query, known=None):
    """
    Liefert den rx-Block fuer den Sidecar. 'query' ist eine Funktion
    (SCPI-Befehl -> Antwortzeile), 'known' ein dict mit bereits bekannten
    Werten aus RX_KEYS, die dann NICHT abgefragt werden.

    Alle Schluessel aus RX_KEYS sind im Ergebnis enthalten, notfalls mit
    None - so ist im Sidecar unterscheidbar, ob ein Feld fehlt (alte
    Aufnahme, ganzer Block nicht vorhanden) oder nicht ermittelt werden
    konnte (Feld vorhanden, Wert None).
    """
    known = dict(known or {})
    out = {}

    if "gain_mode" in known:
        out["gain_mode"] = known.get("gain_mode")
    else:
        out["gain_mode"] = normalize_gain_mode(_try(query, SCPI_GAIN_MODE))
    if "gain_mgc_dbuv" in known:
        out["gain_mgc_dbuv"] = known.get("gain_mgc_dbuv")
    else:
        out["gain_mgc_dbuv"] = _as_float(_try(query, SCPI_GAIN_MGC))

    out["att_state"] = _as_bool(_try(query, SCPI_ATT_STATE))
    out["att_auto"] = _as_bool(_try(query, SCPI_ATT_AUTO))
    out["ref_level_dbuv"] = _as_float(_try(query, SCPI_REF_LEVEL))
    out["level_dbuv"] = _as_float(_try(query, SCPI_LEVEL))
    out["afc"] = _as_bool(_try(query, SCPI_AFC))

    if "squelch_on" in known:
        out["squelch_on"] = known.get("squelch_on")
    else:
        out["squelch_on"] = _as_bool(_try(query, SCPI_SQUELCH_STATE))
    if "squelch_thr_dbuv" in known:
        out["squelch_thr_dbuv"] = known.get("squelch_thr_dbuv")
    else:
        out["squelch_thr_dbuv"] = _as_float(_try(query, SCPI_SQUELCH_THR))

    return {key: out.get(key) for key in RX_KEYS}


def parse_idn(raw):
    """*IDN? -> (manufacturer, model, serial, firmware). Das Geraet
    antwortet kommasepariert; fehlende Felder werden None."""
    parts = [p.strip() for p in str(raw or "").split(",")]
    parts += [None] * (4 - len(parts)) if len(parts) < 4 else []
    return tuple(p if p else None for p in parts[:4])


def build_device_info(query, idn=None):
    """
    Liefert den device-Block fuer den Sidecar (Provenienz). 'idn' kann die
    bereits beim Verbindungsaufbau gelesene *IDN?-Antwort sein - waterfall.py
    hat sie ohnehin, dann entfaellt die Abfrage.
    """
    idn_raw = idn if idn is not None else _try(query, SCPI_IDN)
    manufacturer, model, serial, firmware = parse_idn(idn_raw)
    options = _try(query, SCPI_OPT)
    return {
        "idn": str(idn_raw).strip() if idn_raw else None,
        "manufacturer": manufacturer,
        "model": model,
        "serial": serial,
        "firmware": firmware,
        "options": str(options).strip() if options else None,
    }


# ---------------------------------------------------------------------------
# Selbsttest - "python -m eb200_core.rx_snapshot --selftest", ohne Geraet
# ---------------------------------------------------------------------------

def _self_test() -> None:
    answers = {
        SCPI_GAIN_MODE: "FIX",
        SCPI_GAIN_MGC: "50",
        SCPI_ATT_STATE: "0",
        SCPI_ATT_AUTO: "ON",
        SCPI_REF_LEVEL: "70",
        SCPI_LEVEL: "42.3",
        SCPI_AFC: "OFF",
        SCPI_SQUELCH_STATE: "1",
        SCPI_SQUELCH_THR: "10",
        SCPI_IDN: "Rohde&Schwarz,EB200,101459,V02.74",
        SCPI_OPT: "SU,DS,R4",
    }
    asked = []

    def query(cmd):
        asked.append(cmd)
        return answers[cmd]

    # --- T1: vollstaendiger Schnappschuss ohne Vorwissen --------------------
    snap = build_rx_snapshot(query)
    assert list(snap.keys()) == list(RX_KEYS), snap
    assert snap["gain_mode"] == "MGC", snap          # FIX -> MGC
    assert snap["gain_mgc_dbuv"] == 50.0, snap
    assert snap["att_state"] is False, snap
    assert snap["att_auto"] is True, snap
    assert snap["ref_level_dbuv"] == 70.0, snap
    assert snap["level_dbuv"] == 42.3, snap
    assert snap["afc"] is False, snap
    assert snap["squelch_on"] is True, snap
    assert snap["squelch_thr_dbuv"] == 10.0, snap
    assert not any(c.rstrip("?").endswith(("ON", "OFF")) for c in asked), asked
    print(f"T1 ok  (Schnappschuss vollstaendig, {len(asked)} Abfragen, "
          f"FIX->MGC, Typen bool/float korrekt)")

    # --- T2: strikt lesend --------------------------------------------------
    # Jede abgesetzte Zeile MUSS eine Abfrage sein (endet auf '?').
    for cmd in asked:
        assert cmd.endswith("?"), f"setzender Befehl im Schnappschuss: {cmd}"
    # Regressionsschutz fuer den Befund vom 2026-09-06: ohne SENSe:-Praefix
    # antwortet das Geraet leer und meldet -109 in die Fehler-Queue.
    assert SCPI_LEVEL.upper().startswith("SENS"), SCPI_LEVEL
    print(f"T2 ok  (alle {len(asked)} Befehle sind Abfragen - kein setzender Befehl; "
          f"Pegelabfrage mit SENSe:-Praefix)")

    # --- T3: 'known' vermeidet Abfragen ------------------------------------
    asked.clear()
    snap2 = build_rx_snapshot(query, known={
        "gain_mode": "AGC", "gain_mgc_dbuv": None,
        "squelch_on": False, "squelch_thr_dbuv": 12.0,
    })
    assert SCPI_GAIN_MODE not in asked and SCPI_SQUELCH_STATE not in asked, asked
    assert snap2["gain_mode"] == "AGC" and snap2["squelch_thr_dbuv"] == 12.0, snap2
    assert snap2["ref_level_dbuv"] == 70.0, snap2
    print(f"T3 ok  ('known' uebernommen, nur noch {len(asked)} Abfragen statt 9)")

    # --- T4: Geraet antwortet nicht -> None statt Absturz -------------------
    def broken(cmd):
        raise OSError("Verbindung weg")

    snap3 = build_rx_snapshot(broken)
    assert list(snap3.keys()) == list(RX_KEYS), snap3
    assert all(value is None for value in snap3.values()), snap3
    dev3 = build_device_info(broken)
    assert all(dev3[key] is None for key in DEVICE_KEYS), dev3
    print("T4 ok  (Query-Fehler -> alle Felder None, kein Absturz)")

    # --- T5: Muell-Antworten ------------------------------------------------
    snap4 = build_rx_snapshot(lambda cmd: "???")
    assert snap4["gain_mode"] is None, snap4       # NICHT "AGC" (siehe Doku)
    assert snap4["att_state"] is None, snap4
    assert snap4["ref_level_dbuv"] is None, snap4
    print("T5 ok  (unauswertbare Antwort -> None, gain_mode faellt NICHT auf AGC zurueck)")

    # --- T6: Provenienz -----------------------------------------------------
    asked.clear()
    dev = build_device_info(query)
    assert dev["serial"] == "101459" and dev["firmware"] == "V02.74", dev
    assert dev["model"] == "EB200" and dev["manufacturer"] == "Rohde&Schwarz", dev
    assert dev["options"] == "SU,DS,R4", dev
    assert list(dev.keys()) == list(DEVICE_KEYS), dev

    asked.clear()
    dev2 = build_device_info(query, idn="Rohde&Schwarz,EB200,101459,V02.74")
    assert SCPI_IDN not in asked, asked
    assert dev2["serial"] == "101459", dev2
    assert parse_idn("Firma,Modell") == ("Firma", "Modell", None, None)
    print("T6 ok  (Provenienz aus *IDN?/*OPT?, vorhandene IDN wird nicht erneut abgefragt)")

    print("\n[rx_snapshot] Selbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _self_test()
    else:
        print(__doc__)
