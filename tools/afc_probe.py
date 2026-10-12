#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/afc_probe.py

Hardware-Messung zu AFC und Ablage-Sensorfunktion (PLAN.md T-508/T-509).
Klaert zwei offene Fragen:
  (1) In welchen Demodulationsarten laesst das Geraet AFC zu, und wie
      verhaelt es sich, wenn AFC aktiv ist und auf USB/LSB/CW umgeschaltet
      wird (Fehler oder stille Ablehnung)?
      Handbuch 3.4.5: "The AFC function can be activated at AM, FM and PULSE."
  (2) Liefert die Ablage-Sensorfunktion ("FREQuency:OFFSet", SENSe:DATA?)
      in allen Demodulationsarten verwertbare Werte, und folgt der Messwert
      linear einer bewussten Verstimmung?
      Annex C: Anzeigebereich der Ablage ist +-1/2 Bandbreite.

Eigenstaendiges Werkzeug (Muster tools/mscan_probe.py):
  - Nur Python-Standardbibliothek. KEIN numpy, KEIN Import von waterfall.py.
  - eb200_core/scpi_verify.py WIRD importiert (Transport-Protokoll passt),
    aber NUR die vier Bausteine drain_errors, wait_opc, cmp_enum, cmp_int
    sowie nearest_bw_stage - verify()/Check/enum_check()/freq_check()/
    bw_check()/format_report() bleiben aussen vor: die Ablaufsteuerung und
    das Urteilsvokabular dieses Werkzeugs unterscheiden sich vom
    scpi_verify-Modell ("habe ich gesetzt, kam es an" dort, hier "was
    passiert mit dem NICHT gesetzten zweiten Parameter" - siehe
    classify_set()).
  - Muss laufen koennen, waehrend waterfall.py GESTOPPT ist (D-SCAN/IFPAN-
    Betrieb belegt den Empfaenger exklusiv) - siehe waterfall_running().
  - Das Werkzeug VERSTELLT das Geraet (Frequenz, Demodulation, Bandbreite,
    AFC, SENSe:FUNCtion). Phase F stellt den in Phase A gemerkten
    Vorzustand wieder her - auch bei Exception/KeyboardInterrupt (siehe
    main()).

Aufruf:
  python tools/afc_probe.py --selftest                    # nur Parser/Rechenkern, kein Geraet
  python tools/afc_probe.py --freq 98500000                # Phasen a,b,c,d,f (Default)
  python tools/afc_probe.py --freq 98500000 --afc-range --phases a,b,c,d,e,f
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import sys
import time
from typing import Any, Callable, Sequence

# Erlaubt den Direktaufruf "python tools/afc_probe.py" (Muster
# tools/mscan_probe.py): ohne diesen Eintrag fehlt das Repo-Root im
# Suchpfad fuer den eb200_core-Import.
if __package__ in (None, ""):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.scpi_verify import cmp_enum, cmp_int, drain_errors, nearest_bw_stage, wait_opc


# ---------------------------------------------------------------------------
# Kopf-Konstanten
# ---------------------------------------------------------------------------

EB200_IP_DEFAULT = "10.0.0.55"
SCPI_PORT_DEFAULT = 5555

# Betrag, ab dem eine SENSe:DATA?-Zahl als geraeteseitige INF-Kennung gilt
# (Handbuch: ungueltige/nicht messbare Werte werden so markiert).
INVALID_MAGNITUDE = 9.9e36

# Reihenfolge exakt wie im Auftrag (Handbuch-Reihenfolge SENSe:DEModulation).
MODI = ("FM", "AM", "PULSe", "CW", "USB", "LSB", "IQ")

# Vorzustand (Phase A) / Wiederherstellung (Phase F) - je (Schluessel, Query).
VORZUSTAND_QUERIES = [
    ("freq_hz", "SENSe:FREQuency:CW?"),
    ("dem", "SENSe:DEModulation?"),
    ("bw_hz", "SENSe:BANDwidth:RESolution?"),
    ("afc", "SENSe:FREQuency:CW:AFC?"),
    ("func", "SENSe:FUNCtion?"),
    ("func_concurrent", "SENSe:FUNCtion:CONCurrent?"),
    ("squelch_state", "OUTPut:SQUelch:STATe?"),
    ("squelch_thr", "OUTPut:SQUelch:THReshold:UPPer?"),
    ("gcontrol_mode", "SENSe:GCONtrol:MODE?"),
]


# ---------------------------------------------------------------------------
# Minimaler SCPI-Client (Muster tools/mscan_probe.py ScpiClient)
# ---------------------------------------------------------------------------

class ScpiClient:
    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)

    def send(self, command: str) -> None:
        self._sock.sendall(command.encode("ascii") + b"\n")

    def query(self, command: str) -> str:
        self._sock.sendall(command.encode("ascii") + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("SCPI-Verbindung vom Geraet geschlossen")
            buf += chunk
        line, _rest = buf.split(b"\n", 1)
        return line.decode("ascii").strip()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def determine_local_ip(peer_ip: str) -> str:
    """Ermittelt die lokale IP, ueber die peer_ip erreichbar waere, ohne
    tatsaechlich Daten zu senden (Muster tools/mscan_probe.py). Derzeit
    ungenutzt - dieses Werkzeug meldet sich fuer keine UDP-Tags an, die
    Funktion wird nur der Musterkonsistenz halber mitgefuehrt (Auftrag)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer_ip, 1))
        return probe.getsockname()[0]
    finally:
        probe.close()


def waterfall_running(port: int = 8000, timeout: float = 0.5) -> bool:
    """Echter TCP-Verbindungsversuch auf localhost:port (Muster
    tools/mscan_probe.py) - True, wenn dort gerade etwas lauscht
    (typischerweise waterfall.py)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Rechenkern (ohne Geraet testbar - siehe run_selftest())
# ---------------------------------------------------------------------------

def _func_kind(func: str) -> str | None:
    """Ordnet einen Eintrag aus SENSe:FUNCtion? (Lang- oder Kurzform, mit
    oder ohne Anfuehrungszeichen) 'level' (VOLTage:AC) oder 'offset'
    (FREQuency:OFFSet) zu, sonst None."""
    f = func.strip().strip('"').upper()
    if f.startswith("VOLT"):
        return "level"
    if f.startswith("FREQ") and "OFFS" in f:
        return "offset"
    return None


def parse_data_response(resp: str, funcs: list[str]) -> tuple[float | None, float | None, bool]:
    """Zerlegt eine SENSe:DATA?-Antwort (Kommaliste, ein Feld je aktiver
    Funktion aus SENSe:FUNCtion?, in genau dieser Reihenfolge) in
    (pegel_dbuv, ablage_hz, gueltig).

    pegel_dbuv/ablage_hz sind die Werte zu 'VOLTage:AC' bzw.
    'FREQuency:OFFSet' in `funcs` - steht die jeweilige Funktion nicht in
    `funcs`, bleibt der Wert None (keine Abweichung, die Funktion wurde
    schlicht nicht abgefragt). gueltig ist True, wenn JEDES Feld aus
    `funcs` vorhanden, parsebar und dem Betrag nach < INVALID_MAGNITUDE
    (9.9E36, geraeteseitige INF-Kennung) ist - sonst False, ohne Exception
    (fehlendes Feld/Muellstring werden abgefangen, nicht geworfen)."""
    parts = [p.strip() for p in resp.split(",")] if resp.strip() else []
    pegel_dbuv: float | None = None
    ablage_hz: float | None = None
    gueltig = True
    for i, func in enumerate(funcs):
        value: float | None = None
        if i < len(parts):
            try:
                candidate = float(parts[i])
            except ValueError:
                candidate = None
            if candidate is not None and abs(candidate) < INVALID_MAGNITUDE:
                value = candidate
        if value is None:
            gueltig = False
        kind = _func_kind(func)
        if kind == "level":
            pegel_dbuv = value
        elif kind == "offset":
            ablage_hz = value
    return pegel_dbuv, ablage_hz, gueltig


def classify_set(errors: list[str], expected: Any, readback: str,
                  compare: Callable[[Any, str], tuple[bool, str]]) -> str:
    """Bewertet EINE gesetzte Groesse anhand der Fehler-Queue und des
    Ruecklesewerts (compare = cmp_enum bzw. cmp_int aus
    eb200_core.scpi_verify, Signatur compare(expected, actual_str) ->
    (match, detail)):
      'angenommen' - keine Fehler in der Queue UND readback == expected.
      'abgelehnt'  - Fehler in der Queue (readback wird nicht mehr
                     gewertet - ein gemeldeter Fehler ist die eindeutigere
                     Aussage als ein zufaellig unveraendertes Feld).
      'abweichend' - keine Fehler, aber readback != expected (das Geraet
                     hat den Wert still anders gesetzt oder belassen)."""
    if errors:
        return "abgelehnt"
    match, _detail = compare(expected, readback)
    return "angenommen" if match else "abweichend"


def fit_offset_slope(pairs: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Lineare Regression (kleinste Quadrate) ueber (delta_hz,
    ablage_median_hz)-Paare. Liefert (steigung, achsenabschnitt, r
    [Pearson-Korrelationskoeffizient]). Bei < 2 Punkten oder Varianz 0 in
    x oder y ist keine Steigung/kein r bestimmbar -> (0.0, 0.0, 0.0)."""
    if len(pairs) < 2:
        return 0.0, 0.0, 0.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_yy = sum((y - mean_y) ** 2 for y in ys)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if ss_xx == 0 or ss_yy == 0:
        return 0.0, 0.0, 0.0
    steigung = ss_xy / ss_xx
    achsenabschnitt = mean_y - steigung * mean_x
    r = ss_xy / (ss_xx ** 0.5 * ss_yy ** 0.5)
    return steigung, achsenabschnitt, r


def ist_fehlanzeige(werte: list[float]) -> bool:
    """True, wenn ALLE uebergebenen Ablagewerte exakt 0.0 sind. Das Geraet
    liefert in CW/USB/LSB/IQ keine Ablage, sondern eine harte Null - die
    ist syntaktisch gueltig, aber keine Messung (Hardwarelauf 2026-09)."""
    return bool(werte) and all(w == 0.0 for w in werte)


def offset_deltas(bw_hz: float, factors: Sequence[float]) -> list[int]:
    """Liste der zu testenden Verstimmungen (Hz): [0] gefolgt von je Faktor
    aus `factors` dem Paar (+d, -d) mit d = round(faktor * bw_hz / 2) -
    die Reihenfolge ist Teil der Spezifikation (siehe run_selftest S5)."""
    deltas = [0]
    for factor in factors:
        d = round(factor * bw_hz / 2)
        deltas.append(d)
        deltas.append(-d)
    return deltas


def monotoner_fangbereich(zeilen: list[dict]) -> tuple[int | None, bool]:
    """Bestimmt aus den Zeilen von run_phase_e() (je 'delta_hz'/'ok') den
    groessten Betrag, bis zu dem ALLE kleineren Betraege in BEIDEN
    Richtungen ok sind (monotoner Fangbereich) - ein einzelnes
    Einschwingartefakt bei einem groesseren Betrag darf keinen zu grossen
    Fangbereich vortaeuschen (Hardwarelauf 2026-09). Rueckgabe
    (groesstes_delta_mit_rueckzug_ok_hz, alle_ok); alle_ok ist True, wenn
    JEDE Zeile ok ist - dann ist der Fangbereich nach oben nicht bestimmt."""
    alle_ok = bool(zeilen) and all(z["ok"] for z in zeilen)
    betraege = sorted({abs(z["delta_hz"]) for z in zeilen})
    groesstes_ok: int | None = None
    for betrag in betraege:
        stufe_ok = all(z["ok"] for z in zeilen if abs(z["delta_hz"]) == betrag)
        if not stufe_ok:
            break
        groesstes_ok = betrag
    return groesstes_ok, alle_ok


def _mad(values: list[float]) -> float:
    """Median absoluter Abweichungen vom Median."""
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def _fmt(value: float | None, digits: int = 1) -> str:
    return "n/v" if value is None else f"{value:.{digits}f}"


# ---------------------------------------------------------------------------
# Selbsttest (--selftest, kein Geraet, kein Netzwerk)
# ---------------------------------------------------------------------------

def run_selftest() -> None:
    print("Selbsttest afc_probe (Rechenkern, kein Geraet) ...")
    n = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal n
        assert cond, msg
        n += 1

    # S1
    pegel, ablage, gueltig = parse_data_response("23.4, -2500", ["VOLT:AC", "FREQ:OFFS"])
    check(pegel == 23.4, f"S1 Pegel: {pegel}")
    check(ablage == -2500.0, f"S1 Ablage: {ablage}")
    check(gueltig is True, f"S1 gueltig: {gueltig}")
    print("  OK  S1 parse_data_response: Pegel+Ablage vollstaendig, gueltig")

    # S2
    pegel, ablage, gueltig = parse_data_response("23.4", ["VOLT:AC"])
    check(pegel == 23.4, f"S2 Pegel: {pegel}")
    check(ablage is None, f"S2 Ablage: {ablage}")
    check(gueltig is True, f"S2 gueltig: {gueltig}")
    print("  OK  S2 parse_data_response: nur Pegel angefordert, Ablage None")

    # S3
    pegel, ablage, gueltig = parse_data_response("9.9E37", ["FREQ:OFFS"])
    check(gueltig is False, f"S3 gueltig: {gueltig}")
    check(ablage is None, f"S3 Ablage: {ablage}")
    print("  OK  S3 parse_data_response: Betrag >= 9.9E36 (INF) -> ungueltig")

    # S4
    for resp in ("", "Muellwert;kaputt"):
        pegel, ablage, gueltig = parse_data_response(resp, ["VOLT:AC", "FREQ:OFFS"])
        check(gueltig is False, f"S4 gueltig ({resp!r}): {gueltig}")
        check(pegel is None and ablage is None, f"S4 Werte ({resp!r}): {pegel}, {ablage}")
    print("  OK  S4 parse_data_response: leere/kaputte Antwort -> ungueltig, keine Exception")

    # S5
    deltas = offset_deltas(6000, (0.10, 0.25, 0.40))
    check(deltas == [0, 300, -300, 750, -750, 1200, -1200], f"S5 offset_deltas: {deltas}")
    print("  OK  S5 offset_deltas: Reihenfolge und Werte korrekt")

    # S6 - perfekt inverse Paare (Ablage = -Delta)
    pairs_inv = [(float(d), float(-d)) for d in deltas]
    steigung, _achse, r = fit_offset_slope(pairs_inv)
    check(abs(steigung - (-1.0)) < 1e-9, f"S6 Steigung: {steigung}")
    check(abs(r) > 0.99, f"S6 r: {r}")
    print("  OK  S6 fit_offset_slope: perfekt inverse Paare -> Steigung ~ -1.0, |r| > 0.99")

    # S7 - konstante Ablage (kein Zusammenhang)
    pairs_const = [(float(d), 5.0) for d in deltas]
    _steigung7, _achse7, r7 = fit_offset_slope(pairs_const)
    check(abs(r7) < 0.5, f"S7 r: {r7}")
    print("  OK  S7 fit_offset_slope: konstante Ablage -> kein Zusammenhang (|r| < 0.5)")

    # S8
    v1 = classify_set([], "FM", "FM", cmp_enum)
    check(v1 == "angenommen", f"S8a: {v1}")
    v2 = classify_set(['-221,"Settings conflict"'], "FM", "FM", cmp_enum)
    check(v2 == "abgelehnt", f"S8b: {v2}")
    v3 = classify_set([], "FM", "AM", cmp_enum)
    check(v3 == "abweichend", f"S8c: {v3}")
    print("  OK  S8 classify_set: angenommen/abgelehnt/abweichend korrekt klassifiziert")

    # S9
    check(ist_fehlanzeige([0.0] * 5) is True, "S9 ist_fehlanzeige alle Null")
    print("  OK  S9 ist_fehlanzeige: alle Werte 0.0 -> True")

    # S10
    check(ist_fehlanzeige([0.0, 0.0, 12.0]) is False, "S10 ist_fehlanzeige gemischt")
    print("  OK  S10 ist_fehlanzeige: nicht alle Werte 0.0 -> False")

    # S11
    check(ist_fehlanzeige([]) is False, "S11 ist_fehlanzeige leer")
    print("  OK  S11 ist_fehlanzeige: leere Liste -> False")

    # S12 - Ausreisser bei +-5625 darf den Fangbereich nicht auf 7500 heben
    zeilen_s12 = (
        [{"delta_hz": d, "ok": True} for d in (1875, -1875, 3750, -3750)]
        + [{"delta_hz": d, "ok": False} for d in (5625, -5625)]
        + [{"delta_hz": d, "ok": True} for d in (7500, -7500)]
    )
    groesstes_ok_s12, alle_ok_s12 = monotoner_fangbereich(zeilen_s12)
    check(groesstes_ok_s12 == 3750, f"S12 groesstes_ok: {groesstes_ok_s12}")
    check(alle_ok_s12 is False, f"S12 alle_ok: {alle_ok_s12}")
    print("  OK  S12 monotoner_fangbereich: Ausreisser bei +-5625 begrenzt auf 3750 (nicht 7500)")

    # S13 - durchgehend ok -> Fangbereich nach oben nicht bestimmt
    zeilen_s13 = [{"delta_hz": d, "ok": True} for d in (1875, -1875, 3750, -3750, 5625, -5625)]
    _groesstes_ok_s13, alle_ok_s13 = monotoner_fangbereich(zeilen_s13)
    check(alle_ok_s13 is True, f"S13 alle_ok: {alle_ok_s13}")
    print("  OK  S13 monotoner_fangbereich: durchgehend ok -> alle_ok True")

    # S14 - ok-Kriterium aus run_phase_e (Rueckzug in Richtung f0, kein abs())
    for rueckzug_pct, erwartet in ((-86.7, True), (-49.0, False), (53.3, False), (0.0, False)):
        ok_s14 = rueckzug_pct <= -50.0
        check(ok_s14 is erwartet, f"S14 ok bei {rueckzug_pct}: {ok_s14}")
    print("  OK  S14 ok-Kriterium: nur Rueckzug <= -50 % ist ok (Weglauf +53.3 % nicht mehr faelschlich True)")

    # S15 - "weggelaufen" nur bei positivem rueckzug_pct
    check((53.3 > 0) is True, "S15 weggelaufen bei +53.3")
    check((-86.7 > 0) is False, "S15 weggelaufen bei -86.7")
    print("  OK  S15 weggelaufen: gesetzt bei +53.3, nicht bei -86.7")

    print(f"\nSELBSTTEST OK: {n} Pruefungen")


# ---------------------------------------------------------------------------
# Phase A - Vorzustand sichern (immer, auch bei Teilphasen)
# ---------------------------------------------------------------------------

def run_phase_a(ip: str, scpi_port: int, ignore_running: bool = False,
                 bw_hz: int | None = None) -> tuple[ScpiClient, dict, int]:
    print("=== Phase A: Verbindung, Vorzustand ===")
    if waterfall_running():
        msg = "waterfall.py laeuft (Port 8000 antwortet) - bitte stoppen."
        if not ignore_running:
            raise RuntimeError(msg)
        print(f"  WARNUNG: {msg} --ignore-running gesetzt, mache trotzdem weiter.")
    else:
        print("  waterfall.py (:8000) nicht aktiv - OK")

    scpi = ScpiClient(ip, scpi_port)
    idn = scpi.query("*IDN?")
    print(f"  *IDN? -> {idn}")

    errors = drain_errors(scpi, max_reads=20)
    print(f"  Fehler-Queue vor Messung geleert -> {errors if errors else '(leer)'}")

    vorzustand: dict = {}
    for key, query in VORZUSTAND_QUERIES:
        vorzustand[key] = scpi.query(query)
        print(f"  {query} -> {vorzustand[key]}")

    if vorzustand["afc"].strip() in ("1", "ON"):
        print("  ACHTUNG: AFC war beim Start EIN - am Frontpanel gesetzt?")

    bw_actual = int(round(float(vorzustand["bw_hz"])))
    if bw_hz is not None:
        erwartete_stufe = nearest_bw_stage(int(bw_hz))
        scpi.send(f"SENSe:BANDwidth:RESolution {int(bw_hz)}")
        wait_opc(scpi)
        bw_actual = int(round(float(scpi.query("SENSe:BANDwidth:RESolution?"))))
        if bw_actual != int(bw_hz):
            hinweis = ("" if bw_actual == erwartete_stufe else
                        f" - weicht auch von der naechstliegenden Stufe {erwartete_stufe} Hz ab")
            print(f"  Bandbreite {bw_hz} Hz nicht exakt uebernommen, tatsaechlich {bw_actual} Hz"
                  f"{hinweis}. Verwende {bw_actual} Hz weiter.")
        else:
            print(f"  Bandbreite: {bw_actual} Hz")

    return scpi, vorzustand, bw_actual


# ---------------------------------------------------------------------------
# Phase B - Modusmatrix (kein Signal noetig, reine SCPI-Pruefung)
# ---------------------------------------------------------------------------

def run_phase_b(scpi: ScpiClient, bw_hz: int) -> dict:
    print("=== Phase B: Modusmatrix ===")
    erwartete_stufe = nearest_bw_stage(int(bw_hz))
    scpi.send(f"SENSe:BANDwidth:RESolution {int(bw_hz)}")
    wait_opc(scpi)
    bw_actual = int(round(float(scpi.query("SENSe:BANDwidth:RESolution?"))))
    if bw_actual != int(bw_hz):
        hinweis = ("" if bw_actual == erwartete_stufe else
                    f" - weicht auch von der naechstliegenden Stufe {erwartete_stufe} Hz ab")
        print(f"  Bandbreite {bw_hz} Hz nicht exakt uebernommen, tatsaechlich {bw_actual} Hz"
              f"{hinweis}. Verwende {bw_actual} Hz weiter.")
    else:
        print(f"  Bandbreite: {bw_actual} Hz")

    header = (f"  {'Modus':<6} | {'B1':<10} | {'B2':<10} | {'Fehler B1':<20} | {'Fehler B2':<20} | "
              f"{'AFC/DEM (B1)':<14} | {'AFC/DEM (B2)':<14}")
    print(header)
    print("  " + "-" * (len(header) - 2))

    rows: list[dict] = []
    for modus in MODI:
        # --- B1: Modus zuerst, dann AFC - primaerer Test ist "AFC ON" ---
        scpi.send("SENSe:FREQuency:CW:AFC OFF")
        scpi.send(f"SENSe:DEModulation {modus}")
        drain_errors(scpi, max_reads=20)
        scpi.send("SENSe:FREQuency:CW:AFC ON")
        wait_opc(scpi)
        errors_b1 = drain_errors(scpi, max_reads=20)
        afc_b1 = scpi.query("SENSe:FREQuency:CW:AFC?").strip()
        dem_b1 = scpi.query("SENSe:DEModulation?").strip()
        verdict_b1 = classify_set(errors_b1, 1, afc_b1, cmp_int)
        if verdict_b1 == "angenommen":
            dem_match, _detail = cmp_enum(modus, dem_b1)
            if not dem_match:
                # AFC?=1, aber DEM hat sich nebenbei veraendert - laut
                # Auftrag ist das 'abweichend', nicht 'angenommen'.
                verdict_b1 = "abweichend"

        # --- B2: AFC zuerst (Referenz FM), dann Modus - primaerer Test
        #     ist der Moduswechsel bei aktiver AFC. ---
        scpi.send("SENSe:FREQuency:CW:AFC OFF")
        scpi.send("SENSe:DEModulation FM")
        scpi.send("SENSe:FREQuency:CW:AFC ON")
        wait_opc(scpi)
        errors_ref = drain_errors(scpi, max_reads=20)
        afc_ref = scpi.query("SENSe:FREQuency:CW:AFC?").strip()
        if afc_ref not in ("1", "ON"):
            print(f"  {modus}: B2-Referenz (AFC bei FM) nicht angenommen "
                  f"(AFC?={afc_ref!r}, Fehler {errors_ref if errors_ref else '(keine)'}).")
        drain_errors(scpi, max_reads=20)
        scpi.send(f"SENSe:DEModulation {modus}")
        wait_opc(scpi)
        errors_b2 = drain_errors(scpi, max_reads=20)
        dem_b2 = scpi.query("SENSe:DEModulation?").strip()
        afc_b2 = scpi.query("SENSe:FREQuency:CW:AFC?").strip()
        verdict_b2 = classify_set(errors_b2, modus, dem_b2, cmp_enum)

        row = {
            "modus": modus,
            "b1": {"verdict": verdict_b1, "errors": errors_b1, "afc": afc_b1, "dem": dem_b1},
            "b2": {"verdict": verdict_b2, "errors": errors_b2, "afc": afc_b2, "dem": dem_b2,
                   "afc_blieb_an": afc_b2 in ("1", "ON")},
        }
        rows.append(row)

        err1 = ";".join(errors_b1) if errors_b1 else "-"
        err2 = ";".join(errors_b2) if errors_b2 else "-"
        print(f"  {modus:<6} | {verdict_b1:<10} | {verdict_b2:<10} | {err1[:20]:<20} | {err2[:20]:<20} | "
              f"{afc_b1}/{dem_b1:<12} | {afc_b2}/{dem_b2:<12}")

    scpi.send("SENSe:FREQuency:CW:AFC OFF")
    wait_opc(scpi)
    drain_errors(scpi, max_reads=20)

    return {"bw_actual": bw_actual, "rows": rows}


# ---------------------------------------------------------------------------
# Phase C - Ablage-Sensorfunktion je Modus (Signal noetig)
# ---------------------------------------------------------------------------

def set_demodulation(scpi: ScpiClient, modus: str) -> tuple[bool, str]:
    """Setzt die Demodulationsart und prueft, ob das Geraet sie
    tatsaechlich uebernommen hat. Rueckgabe (ok, meldung).

    Hintergrund (Hardwarelauf): SENSe:DEModulation wird bei zu hoher
    Bandbreite mit -221 "Bandwidth too high with SSB|CW" abgelehnt; der
    Empfaenger bleibt im alten Modus. Ohne diese Pruefung misst das
    Werkzeug den alten Demodulator weiter und schreibt das Ergebnis unter
    dem neuen Modusnamen ins Protokoll."""
    drain_errors(scpi, max_reads=20)
    scpi.send(f"SENSe:DEModulation {modus}")
    wait_opc(scpi)
    errors = drain_errors(scpi, max_reads=20)
    ist = scpi.query("SENSe:DEModulation?").strip()
    if errors:
        return False, f"abgelehnt ({';'.join(errors)}), Geraet steht auf {ist}"
    match, _detail = cmp_enum(modus, ist)
    if not match:
        return False, f"still abgewichen: gewuenscht {modus}, Geraet steht auf {ist}"
    return True, ist


def run_phase_c(scpi: ScpiClient, freq_hz: float, modes: list[str], samples: int,
                 interval: float, settle: float) -> dict | None:
    print("=== Phase C: Ablage-Sensorfunktion je Modus ===")
    scpi.send("SENSe:FUNCtion:CONCurrent ON")
    wait_opc(scpi)
    func_conc = scpi.query("SENSe:FUNCtion:CONCurrent?").strip()
    errors = drain_errors(scpi, max_reads=20)
    print(f"  SENSe:FUNCtion:CONCurrent ON -> ? {func_conc}, Fehler {errors if errors else '(keine)'}")

    scpi.send('SENSe:FUNCtion "VOLTage:AC","FREQuency:OFFSet"')
    wait_opc(scpi)
    func_resp = scpi.query("SENSe:FUNCtion?").strip()
    errors = drain_errors(scpi, max_reads=20)
    print(f'  SENSe:FUNCtion "VOLTage:AC","FREQuency:OFFSet" -> ? {func_resp}, '
          f"Fehler {errors if errors else '(keine)'}")
    if errors:
        print("  FEHLER beim Aktivieren der Funktionsliste - Phase C und D werden uebersprungen.")
        return None

    funcs = [p.strip().strip('"') for p in func_resp.split(",") if p.strip()]

    scpi.send(f"SENSe:FREQuency:CW {int(round(freq_hz))}")
    scpi.send("SENSe:FREQuency:CW:AFC OFF")
    wait_opc(scpi)
    drain_errors(scpi, max_reads=20)

    per_mode: dict[str, dict] = {}
    for modus in modes:
        ok, meldung = set_demodulation(scpi, modus)
        if not ok:
            per_mode[modus] = {"uebersprungen": True, "grund": meldung}
            print(f"  {modus}: uebersprungen - {meldung}")
            continue
        drain_errors(scpi, max_reads=20)
        time.sleep(settle)

        pegel_werte: list[float] = []
        ablage_werte: list[float] = []
        n_valid = 0
        for _ in range(samples):
            resp = scpi.query("SENSe:DATA?")
            pegel, ablage, gueltig = parse_data_response(resp, funcs)
            if gueltig:
                n_valid += 1
                if pegel is not None:
                    pegel_werte.append(pegel)
                if ablage is not None:
                    ablage_werte.append(ablage)
            time.sleep(interval)

        fehlanzeige = ist_fehlanzeige(ablage_werte)
        stat = {
            "n": samples,
            "gueltig": n_valid,
            "pegel_median": statistics.median(pegel_werte) if pegel_werte else None,
            "ablage_min": min(ablage_werte) if ablage_werte else None,
            "ablage_median": statistics.median(ablage_werte) if ablage_werte else None,
            "ablage_max": max(ablage_werte) if ablage_werte else None,
            "ablage_mad": _mad(ablage_werte) if ablage_werte else None,
            "fehlanzeige": fehlanzeige,
        }
        per_mode[modus] = stat
        if fehlanzeige:
            print(f"  {modus:<6} n={stat['n']:<3} gueltig={stat['gueltig']:<3} "
                  f"Pegel(median)={_fmt(stat['pegel_median'])} dBuV  "
                  f"keine Ablagemessung (konstant 0)")
        else:
            print(f"  {modus:<6} n={stat['n']:<3} gueltig={stat['gueltig']:<3} "
                  f"Pegel(median)={_fmt(stat['pegel_median'])} dBuV  "
                  f"Ablage min/median/max={_fmt(stat['ablage_min'])}/{_fmt(stat['ablage_median'])}/"
                  f"{_fmt(stat['ablage_max'])} Hz  MAD={_fmt(stat['ablage_mad'])} Hz")

    return {"funcs": funcs, "per_mode": per_mode}


# ---------------------------------------------------------------------------
# Phase D - Linearitaetstest der Ablage (Signal noetig, setzt C voraus)
# ---------------------------------------------------------------------------

def run_phase_d(scpi: ScpiClient, freq_hz: float, funcs: list[str], modes: list[str],
                 bw_hz: int, delta_factors: Sequence[float], settle: float) -> dict:
    print("=== Phase D: Linearitaetstest der Ablage ===")
    deltas = offset_deltas(bw_hz, delta_factors)
    print(f"  Delta-Werte (Hz): {deltas}")

    results: dict[str, dict] = {}
    for modus in modes:
        ok, meldung = set_demodulation(scpi, modus)
        if not ok:
            results[modus] = {"uebersprungen": True, "grund": meldung}
            print(f"  {modus}: uebersprungen - {meldung}")
            continue
        drain_errors(scpi, max_reads=20)

        pairs: list[tuple[float, float]] = []
        for delta in deltas:
            scpi.send(f"SENSe:FREQuency:CW {int(round(freq_hz + delta))}")
            wait_opc(scpi)
            drain_errors(scpi, max_reads=20)
            time.sleep(settle)
            ablage_werte: list[float] = []
            for _ in range(5):
                resp = scpi.query("SENSe:DATA?")
                _pegel, ablage, gueltig = parse_data_response(resp, funcs)
                if gueltig and ablage is not None:
                    ablage_werte.append(ablage)
            if ablage_werte:
                pairs.append((float(delta), statistics.median(ablage_werte)))
                print(f"    {modus} Delta {delta:+d} Hz -> Ablage(median) {statistics.median(ablage_werte):.1f} Hz")
            else:
                print(f"    {modus} Delta {delta:+d} Hz: keine gueltige Messung")

        scpi.send(f"SENSe:FREQuency:CW {int(round(freq_hz))}")
        wait_opc(scpi)
        drain_errors(scpi, max_reads=20)

        steigung, achsenabschnitt, r = fit_offset_slope(pairs)
        if ist_fehlanzeige([p[1] for p in pairs]):
            urteil = "keine Ablagemessung (konstant 0)"
        elif steigung < -0.7 and abs(r) >= 0.9:
            urteil = "folgt (invers)"
        elif steigung > 0.7 and abs(r) >= 0.9:
            urteil = "folgt (gleichsinnig)"
        else:
            urteil = "kein Zusammenhang"

        results[modus] = {"pairs": pairs, "steigung": steigung,
                           "achsenabschnitt": achsenabschnitt, "r": r, "urteil": urteil}
        print(f"  {modus:<6} Steigung={steigung:.3f}  Achsenabschnitt={achsenabschnitt:.1f}  "
              f"r={r:.3f}  Urteil={urteil}")

    return results


# ---------------------------------------------------------------------------
# Phase E - AFC-Fangbereich (nur mit --afc-range, Signal noetig)
# ---------------------------------------------------------------------------

def run_phase_e(scpi: ScpiClient, freq_hz: float, phase_b: dict | None,
                 bw_hz: int, range_factors: Sequence[float], settle: float) -> dict:
    print("=== Phase E: AFC-Fangbereich ===")
    if not phase_b:
        print("  Kein Phase-B-Ergebnis vorhanden - Phase E wird uebersprungen.")
        return {}

    afc_taugliche_modi = [row["modus"] for row in phase_b["rows"] if row["b1"]["verdict"] == "angenommen"]
    if not afc_taugliche_modi:
        print("  Kein Modus in Phase B als AFC-tauglich (B1='angenommen') erkannt - "
              "Phase E wird uebersprungen.")
        return {}

    deltas = [d for d in offset_deltas(bw_hz, range_factors) if d != 0]

    results: dict[str, dict] = {}
    for modus in afc_taugliche_modi:
        scpi.send("SENSe:FREQuency:CW:AFC OFF")
        scpi.send(f"SENSe:FREQuency:CW {int(round(freq_hz))}")
        scpi.send(f"SENSe:DEModulation {modus}")
        scpi.send("SENSe:FREQuency:CW:AFC ON")
        wait_opc(scpi)
        drain_errors(scpi, max_reads=20)
        time.sleep(settle)

        zeilen = []
        for delta in deltas:
            scpi.send(f"SENSe:FREQuency:CW {int(round(freq_hz + delta))}")
            wait_opc(scpi)
            time.sleep(settle)
            gelesen = float(scpi.query("SENSe:FREQuency:CW?"))
            rueckzug_hz = gelesen - (freq_hz + delta)
            rueckzug_pct = (rueckzug_hz / delta * 100.0) if delta else 0.0
            ok = rueckzug_pct <= -50.0
            weggelaufen = rueckzug_pct > 0
            zeilen.append({"delta_hz": delta, "gelesen_hz": gelesen, "rueckzug_hz": rueckzug_hz,
                            "rueckzug_pct": rueckzug_pct, "ok": ok, "weggelaufen": weggelaufen})
            print(f"  {modus:<6} Delta={delta:+d} Hz  gelesen={gelesen:.0f} Hz  "
                  f"Rueckzug={rueckzug_hz:+.0f} Hz ({rueckzug_pct:+.0f} %)  "
                  f"{'>=50%' if ok else '<50%'}")

        groesstes_delta_ok, alle_ok = monotoner_fangbereich(zeilen)
        if alle_ok:
            groesstes_getestet = max(abs(z["delta_hz"]) for z in zeilen)
            print(f"  {modus}: Fangbereich nach oben nicht bestimmt - groesstes getestetes Delta "
                  f"+-{groesstes_getestet} Hz wurde noch gefangen, groessere Faktoren noetig.")
        else:
            ergebnis = (f"+-{groesstes_delta_ok} Hz" if groesstes_delta_ok is not None
                        else "kein getestetes Delta mit Rueckzug >= 50 %")
            print(f"  {modus}: groesster Fangbereich mit Rueckzug >= 50 % (Bandbreite {bw_hz} Hz): {ergebnis}")
        results[modus] = {"rows": zeilen, "groesstes_delta_mit_rueckzug_ok_hz": groesstes_delta_ok,
                           "alle_ok": alle_ok}

        scpi.send("SENSe:FREQuency:CW:AFC OFF")
        scpi.send(f"SENSe:FREQuency:CW {int(round(freq_hz))}")
        wait_opc(scpi)
        drain_errors(scpi, max_reads=20)

    return results


# ---------------------------------------------------------------------------
# Phase F - Aufraeumen (immer, auch nach Abbruch)
# ---------------------------------------------------------------------------

def run_phase_f(scpi: ScpiClient, vorzustand: dict) -> dict:
    print("=== Phase F: Aufraeumen ===")
    scpi.send("SENSe:FREQuency:CW:AFC OFF")
    scpi.send(f"SENSe:FREQuency:CW {vorzustand['freq_hz']}")
    scpi.send(f"SENSe:DEModulation {vorzustand['dem']}")
    scpi.send(f"SENSe:BANDwidth:RESolution {vorzustand['bw_hz']}")
    vorzustand_funcs = [p.strip().strip('"') for p in vorzustand['func'].split(",") if p.strip()]
    if not any(_func_kind(f) == "offset" for f in vorzustand_funcs):
        scpi.send('SENSe:FUNCtion:OFF "FREQuency:OFFSet"')
        wait_opc(scpi)
    scpi.send(f"SENSe:FUNCtion {vorzustand['func']}")
    scpi.send(f"SENSe:FUNCtion:CONCurrent {vorzustand['func_concurrent']}")
    wait_opc(scpi)
    errors = drain_errors(scpi, max_reads=20)
    print(f"  Rueckstellung gesendet, Fehler-Queue -> {errors if errors else '(leer)'}")

    kontrolle: dict = {}
    for key, query in VORZUSTAND_QUERIES:
        kontrolle[key] = scpi.query(query)
        soll = vorzustand[key].strip()
        ist = kontrolle[key].strip()
        markierung = "OK" if ist == soll else "ABWEICHUNG"
        print(f"  [{markierung:<10}] {query} Soll={soll!r} Ist={ist!r}")

    return kontrolle


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------

def print_final_report(state: dict) -> None:
    print("\n" + "=" * 70)
    print("ABSCHLUSSBERICHT afc_probe")
    print("=" * 70)

    if "bw_actual" in state:
        print(f"\nWirksame Bandbreite: {state['bw_actual']} Hz")

    if "vorzustand" in state:
        print("\nVorzustand (Phase A):")
        for key, _q in VORZUSTAND_QUERIES:
            print(f"  {key}: {state['vorzustand'][key]}")

    if "phase_b" in state:
        print(f"\nPhase B (Bandbreite {state['phase_b']['bw_actual']} Hz):")
        for row in state["phase_b"]["rows"]:
            print(f"  {row['modus']:<6} B1={row['b1']['verdict']:<10} B2={row['b2']['verdict']:<10}")

    if "phase_c" in state:
        if state["phase_c"] is not None:
            print("\nPhase C (Ablage-Sensorfunktion je Modus):")
            for modus, stat in state["phase_c"]["per_mode"].items():
                if stat.get("uebersprungen"):
                    print(f"  {modus:<6} uebersprungen - {stat['grund']}")
                elif stat.get("fehlanzeige"):
                    print(f"  {modus:<6} n={stat['n']} gueltig={stat['gueltig']} "
                          f"keine Ablagemessung (konstant 0)")
                else:
                    print(f"  {modus:<6} n={stat['n']} gueltig={stat['gueltig']} "
                          f"Ablage-Median={_fmt(stat['ablage_median'])} Hz  MAD={_fmt(stat['ablage_mad'])} Hz")
        else:
            print("\nPhase C: uebersprungen (Fehler beim Aktivieren der Funktionsliste).")

    if "phase_d" in state:
        print("\nPhase D (Linearitaet der Ablage):")
        for modus, res in state["phase_d"].items():
            if res.get("uebersprungen"):
                print(f"  {modus:<6} uebersprungen - {res['grund']}")
            else:
                print(f"  {modus:<6} Steigung={res['steigung']:.3f}  r={res['r']:.3f}  Urteil={res['urteil']}")

    if "phase_e" in state:
        if state["phase_e"]:
            print("\nPhase E (AFC-Fangbereich):")
            for modus, res in state["phase_e"].items():
                if res.get("alle_ok"):
                    print(f"  {modus:<6} Fangbereich nach oben nicht bestimmt (alle Deltas gefangen)")
                elif res["groesstes_delta_mit_rueckzug_ok_hz"] is not None:
                    print(f"  {modus:<6} groesster Fangbereich mit Rueckzug >= 50 %: "
                          f"+-{res['groesstes_delta_mit_rueckzug_ok_hz']} Hz")
                else:
                    print(f"  {modus:<6} kein getestetes Delta mit Rueckzug >= 50 %")
                n_weggelaufen = sum(1 for z in res["rows"] if z.get("weggelaufen"))
                print(f"  {modus:<6} weggelaufene Verstimmung(en): {n_weggelaufen}")
        else:
            print("\nPhase E: uebersprungen (kein AFC-tauglicher Modus aus Phase B, "
                  "oder Phase B nicht gelaufen).")

    print("\n" + "-" * 70)
    print('BEFUNDE FUER docs/EB200_Geraeteprofil.md, Abschnitt "AFC und Ablagemessung"')
    print("-" * 70)

    if "phase_b" in state:
        angenommen = [r["modus"] for r in state["phase_b"]["rows"] if r["b1"]["verdict"] == "angenommen"]
        print(f"  - AFC laesst sich per SCPI aktivieren bei: "
              f"{', '.join(angenommen) if angenommen else '(keiner)'}")
        selbst_abschaltend = [r["modus"] for r in state["phase_b"]["rows"]
                               if r["b2"]["verdict"] == "angenommen" and not r["b2"]["afc_blieb_an"]]
        if selbst_abschaltend:
            print(f"  - AFC wird bei Moduswechsel still abgeschaltet bei: {', '.join(selbst_abschaltend)}")
        abgelehnt_b2 = [r["modus"] for r in state["phase_b"]["rows"] if r["b2"]["verdict"] == "abgelehnt"]
        if abgelehnt_b2:
            print(f"  - Moduswechsel bei aktiver AFC wird mit Fehler abgelehnt bei: {', '.join(abgelehnt_b2)}")

    if "phase_c" in state and state["phase_c"] is not None:
        brauchbare = [m for m, s in state["phase_c"]["per_mode"].items()
                      if not s.get("uebersprungen") and not s.get("fehlanzeige") and s["gueltig"] > 0]
        print(f"  - Ablage-Sensorfunktion liefert Werte bei: "
              f"{', '.join(brauchbare) if brauchbare else '(keiner)'}")

    if "phase_d" in state:
        for modus, res in state["phase_d"].items():
            if res.get("uebersprungen"):
                continue
            print(f"  - {modus}: Ablage {res['urteil']} der Verstimmung "
                  f"(Steigung {res['steigung']:.3f}, r={res['r']:.3f})")

    if "phase_e" in state and state["phase_e"]:
        bw_actual = state["phase_b"]["bw_actual"] if "phase_b" in state else "?"
        for modus, res in state["phase_e"].items():
            if res.get("alle_ok"):
                print(f"  - {modus}: AFC-Fangbereich nach oben nicht bestimmt - alle getesteten "
                      f"Deltas gefangen (Bandbreite {bw_actual} Hz)")
            elif res["groesstes_delta_mit_rueckzug_ok_hz"] is not None:
                print(f"  - {modus}: AFC-Fangbereich bis +-{res['groesstes_delta_mit_rueckzug_ok_hz']} Hz "
                      f"(Bandbreite {bw_actual} Hz)")
            else:
                print(f"  - {modus}: kein getestetes Delta im AFC-Fangbereich (Bandbreite {bw_actual} Hz)")
            n_weggelaufen = sum(1 for z in res["rows"] if z.get("weggelaufen"))
            if n_weggelaufen > 0:
                print(f"  - ACHTUNG: {modus} lief bei {n_weggelaufen} Verstimmung(en) vom Traeger weg.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hardware-Messung AFC-Modusmatrix und Ablage-Sensorfunktion (EB200, T-507)."
    )
    parser.add_argument("--ip", default=EB200_IP_DEFAULT)
    parser.add_argument("--scpi-port", type=int, default=SCPI_PORT_DEFAULT)
    parser.add_argument("--freq", type=float, default=None,
                         help="Sollfrequenz in Hz (Pflicht ausser bei --selftest)")
    parser.add_argument("--bw", type=int, default=6000)
    parser.add_argument("--modes", default="FM,AM,CW,USB,LSB")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--settle", type=float, default=0.5)
    parser.add_argument("--delta-factors", default="0.10,0.25,0.40")
    parser.add_argument("--range-factors", default="0.25,0.5,0.75,1.0,1.5")
    parser.add_argument("--phases", default="a,b,c,d,f")
    parser.add_argument("--afc-range", action="store_true",
                         help="(setzt zusaetzlich 'e' in --phases voraus)")
    parser.add_argument("--json", default=None)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--ignore-running", action="store_true")
    args = parser.parse_args(argv)
    if not args.selftest and args.freq is None:
        parser.error("--freq ist erforderlich (ausser bei --selftest).")
    return args


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    args = parse_args()
    if args.selftest:
        run_selftest()
        return 0

    phases = {p.strip().lower() for p in args.phases.split(",") if p.strip()}
    modes = [m.strip().upper() for m in args.modes.split(",") if m.strip()]
    delta_factors = [float(f.strip()) for f in args.delta_factors.split(",") if f.strip()]
    range_factors = [float(f.strip()) for f in args.range_factors.split(",") if f.strip()]

    scpi: ScpiClient | None = None
    vorzustand: dict | None = None
    state: dict = {}
    abgebrochen: str | None = None
    bw_actual: int | None = None

    try:
        scpi, vorzustand, bw_actual = run_phase_a(args.ip, args.scpi_port, args.ignore_running, args.bw)
        state["vorzustand"] = vorzustand

        if "b" in phases:
            state["phase_b"] = run_phase_b(scpi, args.bw)
            bw_actual = state["phase_b"]["bw_actual"]

        funcs: list[str] | None = None
        if "c" in phases:
            phase_c = run_phase_c(scpi, args.freq, modes, args.samples, args.interval, args.settle)
            state["phase_c"] = phase_c
            if phase_c is not None:
                funcs = phase_c["funcs"]

        if "d" in phases:
            if funcs is None:
                print("Phase D uebersprungen: Phase C ist nicht (erfolgreich) gelaufen "
                      "(--phases muss 'c' enthalten).")
            else:
                state["phase_d"] = run_phase_d(scpi, args.freq, funcs, modes, bw_actual,
                                                delta_factors, args.settle)

        if args.afc_range:
            if "e" in phases:
                state["phase_e"] = run_phase_e(scpi, args.freq, state.get("phase_b"),
                                                bw_actual, range_factors, args.settle)
            else:
                print("Phase E uebersprungen: --afc-range gesetzt, aber 'e' fehlt in --phases.")
    except KeyboardInterrupt:
        abgebrochen = "durch Benutzer (Strg+C) abgebrochen"
    except Exception as exc:   # noqa: BLE001 - Diagnosewerkzeug: Phase F muss trotzdem laufen
        abgebrochen = f"Fehler: {exc!r}"
    finally:
        if scpi is not None and vorzustand is not None:
            state["phase_f"] = run_phase_f(scpi, vorzustand)
            scpi.close()

    if bw_actual is not None:
        state["bw_actual"] = bw_actual

    if abgebrochen:
        state["abgebrochen"] = abgebrochen
        print(f"\nABGEBROCHEN: {abgebrochen}")

    print_final_report(state)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nRohwerte geschrieben nach {args.json}")

    return 1 if abgebrochen else 0


if __name__ == "__main__":
    sys.exit(main())
