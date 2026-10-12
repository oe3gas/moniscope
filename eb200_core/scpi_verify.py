#!/usr/bin/env python3
"""
eb200_core/scpi_verify.py

Zentrale SCPI-Verifikation: nach dem Setzen von Parametern (z. B. D-SCAN-Bereich,
Bandbreite, Demodulation) wird per Query zurückgelesen und geprüft, ob das EB200
die Einstellung tatsächlich übernommen hat. Grundprinzip (siehe WS1, Abschnitt
"SCPI-Verifikation"):

    Fehler-Queue leeren -> Set-Kommandos senden -> *OPC? (auf Ausführung warten)
    -> Fehler-Queue einsammeln -> Parameter zurücklesen -> vergleichen -> Urteil.

Warum kein naiver Gleichheitsvergleich `readback == gesendet`? Auf dem EB200
liefert das laufend Fehlalarme:
  - Frequenzen werden auf ein internes Raster gerastet (88,000000 MHz -> evtl.
    87,999985 MHz). -> Toleranzvergleich statt Gleichheit.
  - Bandbreite kennt nur 12 feste ZF-Filterstufen. -> "nächste erlaubte Stufe".
  - Enum-Werte kommen als Kurzform zurück (POSitive -> "POS", NORMal -> "NORM").
    -> Normalisierung vor dem Vergleich.
Erst die Kombination aus Fehler-Queue UND Toleranzvergleich erlaubt ein
belastbares Urteil:
  OK          Wert innerhalb Toleranz zurückgelesen (Rasterung ist normal).
  CLAMPED     Gerät hat einen anderen Wert übernommen, aber KEINEN Fehler
              gemeldet (z. B. auf Bereichsgrenze begrenzt).
  REJECTED    Set wurde abgelehnt (Eintrag in der Fehler-Queue), Altwert bleibt.
  QUERY_ERROR Rücklesen/Parsen fehlgeschlagen (Query nicht unterstützt, Timeout,
              unlesbare Antwort). Nützlich, um offene Annahmen zu erkennen
              (z. B. ob eine bestimmte Query-Kurzform vom Gerät akzeptiert wird).

Design-Entscheidungen (bewusst einfach, nur Standardbibliothek):
  - Kein eigener Socket. Das Modul arbeitet auf einem "Transport" mit
    send()/query() (siehe Protokoll `Transport` unten). Dadurch entkoppelt,
    testbar und in mehreren Skripten wiederverwendbar (Wasserfall, WS1-Scanner).
  - Vergleichslogik als kleine, austauschbare Funktionen (Enum / Frequenz mit
    Toleranz / BW-Stufe / Ganzzahl). Ein `Check` bündelt Set-Kommando,
    Query-Kommando, Erwartungswert und Vergleichsfunktion.
  - Batch-fähig: `verify()` bekommt eine Liste von Checks, sendet erst alle Sets,
    wartet einmal mit *OPC?, liest dann alle Werte zurück. Ein Round-Trip-Paket
    statt N einzelner.

Wichtige Ehrlichkeit zur Fehler-Queue: SYST:ERR? ist FIFO und lässt sich nicht
eindeutig einem einzelnen Set-Kommando zuordnen. Deshalb gilt: liegen nach dem
Batch Fehler vor UND ein Check schlägt den Vergleich fehl, wird er als REJECTED
gewertet (sonst CLAMPED). Für saubere Zuordnung Checks einzeln verifizieren.

Aufruf als eigenständiges Skript (python scpi_verify.py) führt einen Selbsttest
mit einem simulierten Gerät (FakeTransport) aus - ganz ohne EB200. Er zeigt je
ein Beispiel für OK, CLAMPED und REJECTED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, Sequence

# ---------------------------------------------------------------------------
# Konfiguration / Konstanten
# ---------------------------------------------------------------------------

# Die zwölf festen ZF-Filterbandbreiten des EB200 (identisch zu waterfall.py /
# block_admin.py - Annex C, Kodierung der Speicherplätze 0=0,15 kHz ... 11=150 kHz).
BW_STAGES_HZ = [150, 300, 600, 1500, 2400, 6000, 9000, 15000, 30000, 50000, 120000, 150000]

# Standard-Toleranz für Frequenzvergleiche in Hz. Grober, sicherer Startwert:
# das exakte Tuning-Raster des EB200 ist nicht dokumentiert und wird gerade durch
# das Rücklesen empirisch sichtbar. Bei Bedarf anpassen (z. B. auf ein halbes
# beobachtetes Raster). Zu klein -> falsche CLAMPED-Meldungen; zu groß ->
# echte Abweichungen werden übersehen.
DEFAULT_FREQ_TOL_HZ = 10.0

# Anzahl der SYST:ERR?-Abfragen, bevor drain_errors abbricht (Schutz gegen
# eine nie leer werdende Queue).
MAX_ERROR_READS = 32

# Urteilskategorien (siehe Modul-Docstring).
VERDICT_OK = "OK"
VERDICT_CLAMPED = "CLAMPED"
VERDICT_REJECTED = "REJECTED"
VERDICT_QUERY_ERROR = "QUERY_ERROR"


# ---------------------------------------------------------------------------
# Transport-Schnittstelle
# ---------------------------------------------------------------------------

class Transport(Protocol):
    """Minimal benötigte SCPI-Schnittstelle.

    Ein passender Transport muss zwei Methoden bereitstellen:
      send(command)  -> Kommando senden, keine Antwort erwartet.
      query(command) -> Kommando senden UND eine Zeile Antwort zurückgeben
                        (fertig gestrippt, ohne abschließendes '\\n').

    Der `ScpiClient` in waterfall.py hat bisher nur send(); er muss um query()
    ergänzt werden (siehe Integrations-Prompt). Für Tests dient FakeTransport.
    """

    def send(self, command: str) -> None: ...
    def query(self, command: str) -> str: ...


# ---------------------------------------------------------------------------
# Vergleichsfunktionen
# ---------------------------------------------------------------------------
#
# Signatur einheitlich: compare(expected, actual_str) -> (match: bool, detail: str)
#   expected     der gewünschte Wert (Typ passend zur Vergleichsart)
#   actual_str   die rohe Query-Antwort des Geräts (String)
#   Rückgabe     (True/False, menschenlesbarer Detailtext für's Log)
# Wirft der Vergleich eine Exception (z. B. unlesbare Antwort), behandelt
# verify() das als QUERY_ERROR.

def cmp_enum(expected: str, actual_str: str) -> tuple[bool, str]:
    """Enum-/Textvergleich, tolerant gegenüber Lang-/Kurzform und Groß/Klein.

    Beispiele: "AM" == "AM"; "POSitive" ~ "POS"; "NORMal" ~ "NORM".
    Match, wenn beide (getrimmt, groß) gleich sind ODER einer Präfix des anderen
    ist - das deckt die SCPI-Kurzform-Rückgabe ab. Der Detailtext zeigt beide
    Werte, damit man beim Klären offener Annahmen sieht, welche Schreibweise das
    Gerät zurückgibt.
    """
    exp = expected.strip().upper()
    act = actual_str.strip().upper()
    match = exp == act or exp.startswith(act) or act.startswith(exp)
    return match, f"erwartet '{expected}', Gerät '{actual_str.strip()}'"


def cmp_int(expected: int, actual_str: str) -> tuple[bool, str]:
    """Ganzzahliger Exaktvergleich (z. B. Zähler, diskrete Einstellungen)."""
    actual = int(round(float(actual_str)))
    return actual == int(expected), f"erwartet {int(expected)}, Gerät {actual}"


def cmp_freq(tol_hz: float = DEFAULT_FREQ_TOL_HZ) -> Callable[[float, str], tuple[bool, str]]:
    """Fabrik: Frequenzvergleich mit Toleranz (Rasterung wird toleriert)."""
    def _compare(expected: float, actual_str: str) -> tuple[bool, str]:
        actual = float(actual_str)
        diff = actual - float(expected)
        match = abs(diff) <= tol_hz
        # "Delta" statt des griechischen Delta-Zeichens (U+0394): dieses ist
        # in cp1252 nicht darstellbar und ließ print() auf der Windows-Konsole mit
        # UnicodeEncodeError abbrechen - der gesamte Report ging dadurch als
        # "SCPI-Verifikation übersprungen" verloren. "±" und Umlaute sind
        # cp1252-sicher und bleiben.
        return match, (f"erwartet {int(expected)} Hz, Gerät {int(actual)} Hz, "
                       f"Delta {diff:+.0f} Hz (Toleranz ±{int(tol_hz)} Hz)")
    return _compare


def nearest_bw_stage(requested_hz: int, allowed_hz: Sequence[int] = BW_STAGES_HZ) -> int:
    """Nächstliegende der erlaubten ZF-Bandbreitenstufen zu `requested_hz`."""
    return min(allowed_hz, key=lambda stage: abs(stage - requested_hz))


def cmp_bw(allowed_hz: Sequence[int] = BW_STAGES_HZ) -> Callable[[int, str], tuple[bool, str]]:
    """Fabrik: Bandbreitenvergleich gegen die 12 festen Stufen.

    Der Erwartungswert wird auf die nächste erlaubte Stufe abgebildet; Match,
    wenn das Gerät genau diese Stufe zurückliefert. So ist es egal, ob man 9000
    oder 8500 anfordert - erwartet und geprüft wird die real einstellbare Stufe.
    """
    def _compare(expected: int, actual_str: str) -> tuple[bool, str]:
        target = nearest_bw_stage(int(expected), allowed_hz)
        actual = int(round(float(actual_str)))
        return actual == target, (f"angefordert {int(expected)} Hz -> Stufe {target} Hz, "
                                  f"Gerät {actual} Hz")
    return _compare


# ---------------------------------------------------------------------------
# Check- und Ergebnis-Datentypen
# ---------------------------------------------------------------------------

@dataclass
class Check:
    """Ein einzelner zu verifizierender Parameter.

    name        Klartext für Logs (z. B. "D-SCAN Startfrequenz").
    query_cmd   SCPI-Query zum Rücklesen (z. B. "FREQ:DSC:STAR?").
    expected    Erwartungswert (Typ passend zur compare-Funktion).
    compare     Vergleichsfunktion (cmp_enum, cmp_freq(...), cmp_bw(...), ...).
    set_cmd     Optionales Set-Kommando (z. B. "FREQ:DSC:STAR 88000000").
                None = nur prüfen, nicht setzen - praktisch zum Verifizieren von
                Default-/Startwerten (offene Annahmen), ohne etwas zu ändern.
    """
    name: str
    query_cmd: str
    expected: object
    compare: Callable[..., tuple[bool, str]]
    set_cmd: Optional[str] = None


@dataclass
class CheckResult:
    """Ergebnis eines einzelnen Checks."""
    name: str
    kind: str            # VERDICT_OK | _CLAMPED | _REJECTED | _QUERY_ERROR
    expected: object
    actual: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.kind == VERDICT_OK


@dataclass
class VerifyReport:
    """Gesamtergebnis eines verify()-Laufs."""
    results: List[CheckResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)   # eingesammelte Fehler-Queue-Einträge
    opc_ok: bool = True                                # hat *OPC? sauber "1" gemeldet?

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.results)


# ---------------------------------------------------------------------------
# Low-Level-Bausteine (Fehler-Queue, Synchronisation)
# ---------------------------------------------------------------------------

def _error_code(reply: str) -> int:
    """Fehlercode aus einer SYST:ERR?-Antwort (z. B. '-222,"Data out of range"')."""
    head = reply.strip().split(",", 1)[0]
    try:
        return int(head)
    except ValueError:
        # Unerwartetes Format -> als "Fehler vorhanden" behandeln (Code != 0).
        return -1


def drain_errors(transport: Transport, max_reads: int = MAX_ERROR_READS) -> List[str]:
    """SYST:ERR? in Schleife abfragen, bis '0,"No error"' kommt.

    Gibt die Liste der tatsächlichen Fehlermeldungen zurück (ohne den
    abschließenden '0,"No error"'-Eintrag). Bricht nach max_reads ab, damit eine
    dauerhaft nicht leere Queue das Programm nicht blockiert.
    """
    errors: List[str] = []
    for _ in range(max_reads):
        try:
            reply = transport.query("SYST:ERR?")
        except Exception:
            # Queue-Abfrage fehlgeschlagen (Timeout o. ä.): abbrechen statt den
            # Aufrufer (z. B. den Live-Wasserfall-Thread) zum Absturz zu bringen.
            # Bereits gesammelte Fehler bleiben erhalten; ein evtl. weiterer wird
            # hier verpasst - das kleinere Übel gegenüber einem Crash. Bewusst
            # KEIN synthetischer Fehlereintrag, sonst würde ein reiner
            # Drain-Timeout in verify() fälschlich als REJECTED gewertet.
            break
        if _error_code(reply) == 0:
            break
        errors.append(reply.strip())
    return errors


def wait_opc(transport: Transport) -> bool:
    """*OPC? abfragen: True, wenn das Gerät '1' meldet (alle Kommandos fertig).

    Fehlgeschlagene/abweichende Antworten -> False (Verifikation läuft trotzdem
    weiter, das Rücklesen ist auch dann aussagekräftig).
    """
    try:
        return transport.query("*OPC?").strip() == "1"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Kern: verify()
# ---------------------------------------------------------------------------

def verify(
    transport: Transport,
    checks: Sequence[Check],
    *,
    drain_first: bool = True,
    use_opc: bool = True,
    poll_errors: bool = True,
) -> VerifyReport:
    """Setzt (falls set_cmd vorhanden) und verifiziert eine Liste von Checks.

    Ablauf:
      1. drain_first: Fehler-Queue vorab leeren (sauberer Ausgangszustand).
      2. Alle set_cmd senden.
      3. use_opc: mit *OPC? auf Ausführung warten (kein blindes sleep).
      4. poll_errors: Fehler-Queue einsammeln (Batch-Ebene).
      5. Jeden Parameter zurücklesen und vergleichen -> Urteil je Check.

    Urteilsbildung je Check:
      Vergleich passt                       -> OK
      Vergleich passt nicht, Fehler in Queue-> REJECTED
      Vergleich passt nicht, keine Fehler   -> CLAMPED
      Rücklesen/Parsen wirft Exception      -> QUERY_ERROR
    """
    report = VerifyReport()

    if drain_first:
        # Vorhandene Alt-Fehler verwerfen, damit sie nicht unserem Batch
        # zugeschrieben werden.
        drain_errors(transport)

    for chk in checks:
        if chk.set_cmd is not None:
            transport.send(chk.set_cmd)

    if use_opc:
        report.opc_ok = wait_opc(transport)

    if poll_errors:
        report.errors = drain_errors(transport)

    for chk in checks:
        try:
            actual = transport.query(chk.query_cmd)
            match, detail = chk.compare(chk.expected, actual)
        except Exception as exc:  # noqa: BLE001 - bewusst breit: Query kann auf viele Arten scheitern
            report.results.append(CheckResult(
                name=chk.name, kind=VERDICT_QUERY_ERROR, expected=chk.expected,
                actual="", detail=f"Query/Parsen fehlgeschlagen: {exc!r}",
            ))
            continue

        if match:
            kind = VERDICT_OK
        elif report.errors:
            kind = VERDICT_REJECTED
        else:
            kind = VERDICT_CLAMPED

        report.results.append(CheckResult(
            name=chk.name, kind=kind, expected=chk.expected,
            actual=actual.strip(), detail=detail,
        ))

    return report


# ---------------------------------------------------------------------------
# Komfort-Konstruktoren (reduzieren Tippfehler bei den SCPI-Kommandos)
# ---------------------------------------------------------------------------
# `base` ist das Kommando ohne Wert/Fragezeichen, z. B. "FREQ:DSC:STAR".
# set_cmd wird zu "<base> <value>", query_cmd zu "<base>?".

def freq_check(name: str, base: str, value_hz: int, *,
               tol_hz: float = DEFAULT_FREQ_TOL_HZ, do_set: bool = True) -> Check:
    return Check(
        name=name, query_cmd=f"{base}?", expected=int(value_hz),
        compare=cmp_freq(tol_hz),
        set_cmd=f"{base} {int(value_hz)}" if do_set else None,
    )


def bw_check(name: str, base: str, value_hz: int, *,
             allowed_hz: Sequence[int] = BW_STAGES_HZ, do_set: bool = True) -> Check:
    return Check(
        name=name, query_cmd=f"{base}?", expected=int(value_hz),
        compare=cmp_bw(allowed_hz),
        set_cmd=f"{base} {int(value_hz)}" if do_set else None,
    )


def enum_check(name: str, base: str, value: str, *, do_set: bool = True) -> Check:
    return Check(
        name=name, query_cmd=f"{base}?", expected=value, compare=cmp_enum,
        set_cmd=f"{base} {value}" if do_set else None,
    )


# ---------------------------------------------------------------------------
# Log-Hilfe
# ---------------------------------------------------------------------------

def format_report(report: VerifyReport) -> str:
    """Mehrzeilige, gut lesbare Zusammenfassung für Konsole/Logfile."""
    lines: List[str] = []
    status = "alle OK" if report.all_ok else "ABWEICHUNGEN"
    lines.append(f"SCPI-Verifikation: {status} "
                 f"(*OPC?={'ok' if report.opc_ok else 'FEHLT'}, "
                 f"{len(report.errors)} Fehler-Queue-Eintrag/e)")
    for r in report.results:
        lines.append(f"  [{r.kind:<11}] {r.name}: {r.detail}")
    for e in report.errors:
        lines.append(f"  Fehler-Queue: {e}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Selbsttest mit simuliertem Gerät (python scpi_verify.py)
# ---------------------------------------------------------------------------

class _FakeTransport:
    """Simuliert ein EB200 gerade so weit, dass OK/CLAMPED/REJECTED entstehen.

    - Frequenzen werden auf 5 Hz gerastet (im Toleranzband -> OK).
    - Frequenzen > 3 GHz werden abgelehnt (Fehler-Queue-Eintrag, Altwert bleibt)
      -> REJECTED.
    - FREQ:DSC:STOP wird bei > 3 GHz stillschweigend auf 3 GHz begrenzt
      (kein Fehler) -> CLAMPED.
    - Bandbreite rastet auf die nächste der 12 Stufen.
    - *OPC? -> "1", SYST:ERR? liefert FIFO aus der Fehler-Queue.
    """

    RASTER_HZ = 5
    MAX_FREQ_HZ = 3_000_000_000

    def __init__(self) -> None:
        self._state = {
            "FREQ:DSC:STAR": 85_000_000,
            "FREQ:DSC:STOP": 110_000_000,
            "FREQ": 98_500_000,
            "BAND": 9000,
            "DEM": "AM",
        }
        self._errq: List[str] = []

    # -- send: Set-Kommandos interpretieren --
    def send(self, command: str) -> None:
        parts = command.strip().split(None, 1)
        if len(parts) != 2:
            return
        base, value = parts[0], parts[1]

        if base == "BAND":
            self._state["BAND"] = nearest_bw_stage(int(value))
            return
        if base == "DEM":
            self._state["DEM"] = value.strip().upper()
            return
        if base in ("FREQ:DSC:STAR", "FREQ:DSC:STOP", "FREQ"):
            f = int(value)
            if base == "FREQ:DSC:STOP" and f > self.MAX_FREQ_HZ:
                # stilles Clamping ohne Fehler
                self._state[base] = self.MAX_FREQ_HZ
            elif f > self.MAX_FREQ_HZ:
                # Ablehnung mit Fehler-Queue-Eintrag, Altwert bleibt
                self._errq.append('-222,"Data out of range"')
            else:
                # normale Rasterung
                self._state[base] = round(f / self.RASTER_HZ) * self.RASTER_HZ
            return

    # -- query: Rücklesen, *OPC?, SYST:ERR? --
    def query(self, command: str) -> str:
        cmd = command.strip()
        if cmd == "*OPC?":
            return "1"
        if cmd == "SYST:ERR?":
            return self._errq.pop(0) if self._errq else '0,"No error"'
        if cmd.endswith("?"):
            base = cmd[:-1]
            if base in self._state:
                return str(self._state[base])
        return '0,"No error"'


def _selftest() -> None:
    dev = _FakeTransport()

    # Batch 1: gültige Sets + ein still begrenzter Wert. Da dieser Batch KEINEN
    # Fehler in die Queue schreibt, wird das stille Begrenzen korrekt als
    # CLAMPED erkannt (nicht als REJECTED).
    batch1 = [
        freq_check("D-SCAN Start", "FREQ:DSC:STAR", 88_000_003),   # 5-Hz-Raster -> OK
        bw_check("Bandbreite", "BAND", 9000),                       # exakte Stufe -> OK
        enum_check("Demodulation", "DEM", "AM"),                    # Kurzform -> OK
        freq_check("D-SCAN Stop", "FREQ:DSC:STOP", 4_000_000_000),  # still auf 3 GHz -> CLAMPED
    ]
    report1 = verify(dev, batch1)
    print(format_report(report1))

    # Batch 2: bewusst ungültiges Set einzeln verifizieren. Der Fehler in der
    # Queue lässt sich hier eindeutig diesem Check zuordnen -> REJECTED. Genau
    # deshalb: für belastbare Zuordnung fehleranfällige Sets einzeln prüfen.
    batch2 = [
        freq_check("CW-Frequenz (ungültig)", "FREQ", 5_000_000_000),  # > 3 GHz -> REJECTED
    ]
    report2 = verify(dev, batch2)
    print()
    print(format_report(report2))

    # Kleine Plausibilitätsprüfung der erwarteten Urteile.
    kinds = {r.name: r.kind for r in list(report1.results) + list(report2.results)}
    assert kinds["D-SCAN Start"] == VERDICT_OK
    assert kinds["Bandbreite"] == VERDICT_OK
    assert kinds["Demodulation"] == VERDICT_OK
    assert kinds["D-SCAN Stop"] == VERDICT_CLAMPED
    assert kinds["CW-Frequenz (ungültig)"] == VERDICT_REJECTED
    print("\nSelbsttest bestanden: OK/CLAMPED/REJECTED wie erwartet.")


if __name__ == "__main__":
    _selftest()