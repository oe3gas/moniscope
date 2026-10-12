#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/check_js_blocks.py

Reproduzierbares Gate fuer eingebettetes JavaScript: findet alle
<script>...</script>-Bloecke in String-Konstanten der angegebenen
.py-Module (ast-basiert, keine Textsuche auf der Rohdatei) und prueft
jeden Block einzeln per `node --check`. Fehler werden auf die Zeile in
der .py-Quelldatei zurueckgerechnet.

Aufruf:
    python tools/check_js_blocks.py [Datei ...]
    python tools/check_js_blocks.py --selftest
"""

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STANDARDDATEIEN = [
    "waterfall.py",
    "analyze_sessions.py",
    "analyze_spectrum.py",
    "manage_jobs.py",
    "analyze_mscan.py",
    "analyze_audio.py",
    "sdr_waterfall.py",
    "eb200_core/block_admin.py",
]

# Nicht-greedy ueber DOTALL: erfasst auch mehrzeilige Bloecke; das oeffnende
# Tag darf Attribute tragen (z.B. src=... fuer externe Skripte).
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
SRC_ATTR_RE = re.compile(r"\bsrc\s*=", re.IGNORECASE)

# Node meldet Syntaxfehler als "<pfad>:<zeile>" in der ersten Zeile von
# stderr, gefolgt von Kontext, Caret-Zeile und "<Fehlerart>: <Meldung>".
NODE_LOCATION_RE = re.compile(r"^(.*):(\d+)$")
NODE_MESSAGE_RE = re.compile(r"^(\w+Error): (.*)$")


class ScriptFinder(ast.NodeVisitor):
    """Sammelt String-Konstanten (fuer die Script-Suche) getrennt von
    JoinedStr-Knoten (f-Strings), deren Literalteile werden NICHT als
    eigene String-Konstanten mitgezaehlt (sonst doppelte/kaputte Treffer)."""

    def __init__(self):
        self.string_nodes = []
        self.joinedstr_hits = []

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            self.string_nodes.append(node)

    def visit_JoinedStr(self, node):
        for teil in node.values:
            if isinstance(teil, ast.Constant) and isinstance(teil.value, str):
                if "<script" in teil.value.lower():
                    self.joinedstr_hits.append(node)
                    break
        # Bewusst KEIN generic_visit(node): die Constant-Fragmente eines
        # f-Strings sollen nicht zusaetzlich als eigenstaendige
        # String-Konstanten in string_nodes landen.


def zeilen_offset(value, bis_offset):
    """Anzahl Zeilenumbrueche in value[:bis_offset] - fuer die Umrechnung
    von Offset innerhalb des String-Werts auf eine Zeilenverschiebung."""
    return value.count("\n", 0, bis_offset)


def node_fehler_auswerten(stderr_text):
    """Zerlegt die stderr-Ausgabe von `node --check` in (zeile, meldung).
    zeile ist die Zeile INNERHALB der geprueften Temp-Datei (1-basiert),
    meldung ist z.B. 'SyntaxError: Unexpected token ...'."""
    zeilen = stderr_text.splitlines()
    zeile = None
    if zeilen:
        treffer = NODE_LOCATION_RE.match(zeilen[0])
        if treffer:
            zeile = int(treffer.group(2))
    meldung = None
    for ln in zeilen:
        treffer = NODE_MESSAGE_RE.match(ln.strip())
        if treffer:
            meldung = "{0}: {1}".format(treffer.group(1), treffer.group(2))
            break
    if meldung is None:
        rest = stderr_text.strip().splitlines()
        meldung = rest[-1] if rest else "unbekannter node-Fehler"
    return zeile, meldung


class Dateiergebnis:
    def __init__(self, relpfad):
        self.relpfad = relpfad
        self.geprueft = 0
        self.fehler = 0
        self.extern = 0
        self.uebersprungen = 0
        self.fehlerzeilen = []
        self.werkzeugfehler = None  # Text, falls Datei/AST nicht verarbeitbar


def datei_pruefen(pfad, node_pfad, tmp_dir):
    relpfad = pfad.relative_to(REPO_ROOT) if pfad.is_relative_to(REPO_ROOT) else pfad
    ergebnis = Dateiergebnis(str(relpfad))

    if not pfad.is_file():
        ergebnis.werkzeugfehler = "Datei nicht gefunden: {0}".format(pfad)
        return ergebnis

    quelltext = pfad.read_text(encoding="utf-8")
    try:
        baum = ast.parse(quelltext, filename=str(pfad))
    except SyntaxError as exc:
        ergebnis.werkzeugfehler = "nicht parsebar ({0})".format(exc)
        return ergebnis

    finder = ScriptFinder()
    finder.visit(baum)

    modul = pfad.stem
    lfd = 0

    for knoten in finder.string_nodes:
        wert = knoten.value
        for treffer in SCRIPT_RE.finditer(wert):
            attrs, inhalt = treffer.group(1), treffer.group(2)
            lfd += 1
            if SRC_ATTR_RE.search(attrs):
                ergebnis.extern += 1
                continue

            start_zeile_quelle = knoten.lineno + zeilen_offset(wert, treffer.start(2))

            js_pfad = tmp_dir / "{0}__{1}.js".format(modul, lfd)
            js_pfad.write_bytes(inhalt.encode("utf-8"))

            ergebnis.geprueft += 1
            lauf = subprocess.run(
                [node_pfad, "--check", str(js_pfad)],
                capture_output=True,
                text=True,
            )
            if lauf.returncode != 0:
                ergebnis.fehler += 1
                node_zeile, meldung = node_fehler_auswerten(lauf.stderr)
                if node_zeile is not None:
                    quellzeile = start_zeile_quelle + (node_zeile - 1)
                else:
                    quellzeile = start_zeile_quelle
                ergebnis.fehlerzeilen.append("{0}:{1}: {2}".format(relpfad, quellzeile, meldung))

    for knoten in finder.joinedstr_hits:
        ergebnis.uebersprungen += 1
        ergebnis.fehlerzeilen.append(
            "{0}:{1}: uebersprungen (f-string)".format(relpfad, knoten.lineno)
        )

    return ergebnis


def dateien_pruefen(dateien, node_pfad, tmp_dir, quiet):
    ergebnisse = []
    for pfad in dateien:
        ergebnis = datei_pruefen(pfad, node_pfad, tmp_dir)
        ergebnisse.append(ergebnis)

        if ergebnis.werkzeugfehler is not None:
            print("FEHLER: {0}: {1}".format(ergebnis.relpfad, ergebnis.werkzeugfehler), file=sys.stderr)
            continue

        for zeile in ergebnis.fehlerzeilen:
            print(zeile)

        if not quiet:
            print(
                "{0}: {1} Bloecke geprueft, {2} Fehler, {3} extern, {4} uebersprungen".format(
                    ergebnis.relpfad, ergebnis.geprueft, ergebnis.fehler, ergebnis.extern, ergebnis.uebersprungen
                )
            )

    return ergebnisse


def summenzeile(ergebnisse):
    dateien = len(ergebnisse)
    geprueft = sum(e.geprueft for e in ergebnisse)
    fehler = sum(e.fehler for e in ergebnisse)
    extern = sum(e.extern for e in ergebnisse)
    uebersprungen = sum(e.uebersprungen for e in ergebnisse)
    werkzeugfehler = sum(1 for e in ergebnisse if e.werkzeugfehler is not None)
    text = "Gesamt: {0} Dateien, {1} Bloecke geprueft, {2} Fehler, {3} extern, {4} uebersprungen".format(
        dateien, geprueft, fehler, extern, uebersprungen
    )
    if werkzeugfehler:
        text += ", {0} Werkzeugfehler".format(werkzeugfehler)
    return text


def exitcode_bestimmen(ergebnisse):
    if any(e.werkzeugfehler is not None for e in ergebnisse):
        return 2
    if any(e.fehler > 0 for e in ergebnisse):
        return 1
    return 0


SELFTEST_QUELLTEXT = (
    "# hier steht <script> als Wort\n"
    "T1 = \"\"\"\n"
    "<script>\n"
    "function ok() { return 1; }\n"
    "</script>\n"
    "\"\"\"\n"
    "\n"
    "T2 = \"\"\"\n"
    "<script>\n"
    "function broken() {\n"
    "  const x = ;\n"
    "}\n"
    "</script>\n"
    "\"\"\"\n"
    "\n"
    "T4 = \"\"\"\n"
    "<script src=\"x.js\"></script>\n"
    "\"\"\"\n"
)


def selftest(node_pfad):
    tmp_dir = Path(tempfile.mkdtemp(prefix="check_js_blocks_selftest_"))
    try:
        synth_datei = tmp_dir / "synthetisch.py"
        synth_datei.write_text(SELFTEST_QUELLTEXT, encoding="utf-8")

        block_tmp_dir = Path(tempfile.mkdtemp(prefix="check_js_blocks_selftest_js_", dir=tmp_dir))
        ergebnis = datei_pruefen(synth_datei, node_pfad, block_tmp_dir)

        erwartungen = []
        erwartungen.append(("geprueft == 2", ergebnis.geprueft == 2, ergebnis.geprueft))
        erwartungen.append(("fehler == 1", ergebnis.fehler == 1, ergebnis.fehler))
        erwartungen.append(("extern == 1", ergebnis.extern == 1, ergebnis.extern))
        erwartungen.append(("uebersprungen == 0", ergebnis.uebersprungen == 0, ergebnis.uebersprungen))

        # T2s "const x = ;" steht auf Zeile 11 der synthetischen Datei.
        erwartete_fehlerzeile = 11
        gefundene_zeile = None
        for zeile in ergebnis.fehlerzeilen:
            # re.search statt re.match: Windows-Pfade wie "C:\..." tragen
            # selbst einen Doppelpunkt (Laufwerksbuchstabe), der keine
            # Ziffern-Zeilennummer traegt und darum uebersprungen wird.
            treffer = re.search(r":(\d+): ", zeile)
            if treffer:
                gefundene_zeile = int(treffer.group(1))
                break
        erwartungen.append(
            ("Fehlerzeile == {0}".format(erwartete_fehlerzeile), gefundene_zeile == erwartete_fehlerzeile, gefundene_zeile)
        )

        alle_ok = all(ok for _, ok, _ in erwartungen)

        for beschreibung, ok, ist in erwartungen:
            status = "ok" if ok else "FEHLGESCHLAGEN (ist={0})".format(ist)
            print("Selftest {0}: {1}".format(beschreibung, status))

        if alle_ok:
            print("Selftest OK")
            return 0
        else:
            print("Selftest FEHLGESCHLAGEN", file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="node --check-Gate fuer eingebettete <script>-Bloecke")
    parser.add_argument("dateien", nargs="*", help="zu pruefende .py-Dateien (relativ zum Repo-Root)")
    parser.add_argument("--keep", action="store_true", help="Temp-Verzeichnis mit den extrahierten .js-Dateien behalten")
    parser.add_argument("--quiet", action="store_true", help="nur Fehlerzeilen und Summenzeile ausgeben")
    parser.add_argument("--selftest", action="store_true", help="Werkzeug an einer synthetischen Datei selbst testen")
    args = parser.parse_args(argv)

    node_pfad = shutil.which("node")
    if node_pfad is None:
        print("FEHLER: node wurde nicht gefunden (PATH pruefen).", file=sys.stderr)
        return 2

    if args.selftest:
        return selftest(node_pfad)

    dateiliste = args.dateien if args.dateien else STANDARDDATEIEN
    pfade = [(REPO_ROOT / d) for d in dateiliste]

    tmp_dir = Path(tempfile.mkdtemp(prefix="check_js_blocks_"))
    try:
        ergebnisse = dateien_pruefen(pfade, node_pfad, tmp_dir, args.quiet)
        print(summenzeile(ergebnisse))
        if args.keep:
            print("Temp-Verzeichnis behalten: {0}".format(tmp_dir))
        return exitcode_bestimmen(ergebnisse)
    finally:
        if not args.keep:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
