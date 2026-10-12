#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/paths.py

Programm- und Datenwurzel der Suite (T-2001, Meilenstein 20).

  PROGRAM_ROOT  Verzeichnis, in dem der Programmcode liegt (Repo-Root). Von hier
                kommen nur mitgelieferte, gelesene Daten (z. B. modes/fsk.json).
  DATA_ROOT     Wurzel fuer alles Beschreibbare und die Konfiguration des
                Betreibers (Kataloge, Sessions, Aufnahmen, Jobs, Logs, ...).

DATA_ROOT wird ausschliesslich per Kommandozeile gesetzt: --data-dir PFAD oder
--data-dir=PFAD (Entscheidungslog 2026-10-08, keine Umgebungsvariable). Ohne
Argument gilt PROGRAM_ROOT - das bisherige Verhalten der Suite.

Das Modul liest sys.argv beim ERSTEN Import selbst aus und haelt DATA_ROOT
danach als Prozesskonstante. Grund: Modulkonstanten, Importkopien
(from x import KONST) und Parameterdefaults (def f(path=KONST)) schreiben ihren
Wert zur Importzeit fest; eine spaeter gesetzte Wurzel wuerde sie nicht mehr
erreichen. Wer paths importiert, bekommt deshalb immer schon den endgueltigen
Wert. Relative Angaben werden gegen das Arbeitsverzeichnis beim Start
aufgeloest; DATA_ROOT ist immer absolut.

Ein --data-dir, das nicht auf ein bestehendes Verzeichnis zeigt, beendet den
Prozess beim Import mit einer Meldung auf stderr (Exitcode 1) - ein Tippfehler
soll keinen verwaisten Datenordner anlegen.

Die Argumentliste der Server wertet dieses Modul nur fuer --data-dir aus;
Server mit eigenem argparse muessen --data-dir dort zusaetzlich kennen
(T-2003).

Nur Python-Standardbibliothek, keine Importe aus eb200_core.

Selbsttest:
    python -m eb200_core.paths --selftest
"""

from __future__ import annotations

import os
import sys
from typing import Optional

DATA_DIR_ARG = "--data-dir"

PROGRAM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DataDirError(ValueError):
    """Ungueltige --data-dir-Angabe (fehlender Wert, mehrfach, kein Verzeichnis)."""


def _data_dir_from_argv(argv: list[str]) -> Optional[str]:
    """Liefert den Rohwert von --data-dir aus argv, oder None ohne Angabe.

    Erkennt "--data-dir PFAD" und "--data-dir=PFAD". Eine zweite Angabe ist
    ein Fehler, ebenso ein fehlender oder leerer Wert."""
    gefunden: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == DATA_DIR_ARG:
            if i + 1 >= len(argv):
                raise DataDirError(f"{DATA_DIR_ARG} ohne Pfadangabe")
            gefunden.append(argv[i + 1])
            i += 2
            continue
        if a.startswith(DATA_DIR_ARG + "="):
            gefunden.append(a[len(DATA_DIR_ARG) + 1:])
        i += 1
    if not gefunden:
        return None
    if len(gefunden) > 1:
        raise DataDirError(f"{DATA_DIR_ARG} mehrfach angegeben")
    if not gefunden[0].strip():
        raise DataDirError(f"{DATA_DIR_ARG} mit leerer Pfadangabe")
    return gefunden[0]


def resolve_data_root(argv: list[str], default: str = PROGRAM_ROOT) -> str:
    """Bestimmt die Datenwurzel aus argv (ohne Programmnamen).

    Ohne --data-dir: default. Sonst der absolute Pfad der Angabe; er muss ein
    bestehendes Verzeichnis sein, sonst DataDirError."""
    roh = _data_dir_from_argv(argv)
    if roh is None:
        return default
    pfad = os.path.abspath(roh)
    if not os.path.isdir(pfad):
        raise DataDirError(f"{DATA_DIR_ARG}: kein bestehendes Verzeichnis: {pfad}")
    return pfad


try:
    DATA_ROOT = resolve_data_root(sys.argv[1:])
except DataDirError as _e:
    raise SystemExit(f"FEHLER: {_e}")


def data_path(*teile: str) -> str:
    """Pfad unterhalb von DATA_ROOT."""
    return os.path.join(DATA_ROOT, *teile)


def program_path(*teile: str) -> str:
    """Pfad unterhalb von PROGRAM_ROOT."""
    return os.path.join(PROGRAM_ROOT, *teile)


# ---------------------------------------------------------------------------
# Selbsttest (python -m eb200_core.paths --selftest)
# ---------------------------------------------------------------------------

def _selftest() -> None:
    import subprocess
    import tempfile

    def ok(bedingung: bool, text: str) -> None:
        if not bedingung:
            raise AssertionError(text)
        print(f"OK  {text}")

    def fehler(argv: list[str]) -> Optional[str]:
        try:
            resolve_data_root(argv)
        except DataDirError as e:
            return str(e)
        return None

    # T1: Programmwurzel
    ok(os.path.isabs(PROGRAM_ROOT), "T1 PROGRAM_ROOT ist absolut")
    ok(os.path.isfile(os.path.join(PROGRAM_ROOT, "eb200_core", "paths.py")),
       "T1 PROGRAM_ROOT ist das Verzeichnis ueber eb200_core/")
    ok(program_path("modes", "fsk.json") == os.path.join(PROGRAM_ROOT, "modes", "fsk.json"),
       "T1 program_path haengt an PROGRAM_ROOT an")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.abspath(tmp)
        unter = os.path.join(tmp, "unter")
        os.mkdir(unter)
        fehlt = os.path.join(tmp, "gibt_es_nicht")
        datei = os.path.join(tmp, "datei.txt")
        with open(datei, "w", encoding="utf-8") as fh:
            fh.write("x")

        # T2: Formen der Angabe
        ok(resolve_data_root([]) == PROGRAM_ROOT, "T2 ohne Angabe gilt PROGRAM_ROOT")
        ok(resolve_data_root(["--selftest"]) == PROGRAM_ROOT,
           "T2 fremde Argumente aendern nichts")
        ok(resolve_data_root(["--data-dir", tmp]) == tmp, "T2 --data-dir PFAD")
        ok(resolve_data_root([f"--data-dir={tmp}"]) == tmp, "T2 --data-dir=PFAD")
        ok(resolve_data_root(["--plain", "--data-dir", tmp, "--no-browser"]) == tmp,
           "T2 Angabe zwischen anderen Argumenten")
        ok(resolve_data_root(["--data-dir", tmp], default="X") == tmp,
           "T2 Angabe schlaegt den Vorgabewert")
        ok(resolve_data_root([], default="X") == "X", "T2 Vorgabewert ohne Angabe")

        # T3: relative Angabe wird gegen das Arbeitsverzeichnis aufgeloest
        vorher = os.getcwd()
        try:
            os.chdir(tmp)
            ok(resolve_data_root(["--data-dir", "unter"]) == unter,
               "T3 relative Angabe wird absolut")
        finally:
            os.chdir(vorher)

        # T4: Fehlerfaelle
        ok(fehler(["--data-dir", fehlt]) is not None, "T4 nicht vorhandenes Verzeichnis")
        ok(fehler(["--data-dir", datei]) is not None, "T4 Datei statt Verzeichnis")
        ok(fehler(["--data-dir"]) is not None, "T4 fehlender Wert")
        ok(fehler(["--data-dir="]) is not None, "T4 leerer Wert")
        ok(fehler(["--data-dir", tmp, "--data-dir", unter]) is not None,
           "T4 mehrfache Angabe")
        ok(fehler(["--data-dir", tmp, f"--data-dir={tmp}"]) is not None,
           "T4 mehrfache Angabe in beiden Formen")

        # T5: data_path
        ok(data_path("sessions") == os.path.join(DATA_ROOT, "sessions"),
           "T5 data_path haengt an DATA_ROOT an")

        # T6: echter Import in einem Unterprozess (Importzeit-Verhalten)
        def lauf(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, "-c",
                 "import eb200_core.paths as p; print(p.DATA_ROOT)", *args],
                cwd=PROGRAM_ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)

        r = lauf()
        ok(r.returncode == 0 and r.stdout.strip() == PROGRAM_ROOT,
           "T6 Import ohne Angabe: DATA_ROOT = PROGRAM_ROOT")
        r = lauf("--data-dir", tmp)
        ok(r.returncode == 0 and r.stdout.strip() == tmp,
           "T6 Import mit --data-dir: DATA_ROOT = Angabe")
        r = lauf(f"--data-dir={unter}")
        ok(r.returncode == 0 and r.stdout.strip() == unter,
           "T6 Import mit --data-dir=PFAD")
        r = lauf("--data-dir", fehlt)
        ok(r.returncode == 1 and "--data-dir" in r.stderr and r.stdout.strip() == "",
           "T6 Import mit fehlendem Verzeichnis: Exitcode 1, Meldung auf stderr")

        # T7: kein Seiteneffekt - der Selbsttest hat im Temp-Verzeichnis nichts angelegt
        ok(sorted(os.listdir(tmp)) == ["datei.txt", "unter"],
           "T7 keine Dateien oder Verzeichnisse angelegt")

    print("Selbsttest paths erfolgreich abgeschlossen.")


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Programm- und Datenwurzel der Suite")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument(DATA_DIR_ARG, metavar="PFAD",
                    help="Datenwurzel (wird beim Import ausgewertet)")
    args = ap.parse_args(argv)
    if args.selftest:
        _selftest()
        return 0
    print(f"PROGRAM_ROOT = {PROGRAM_ROOT}")
    print(f"DATA_ROOT    = {DATA_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
