#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/check_ui_lang.py

Gate fuer die Uebersetzung der WebGUI (Meilenstein 18, T-1805): findet deutsche
Reste in den HTML/JS-Bloecken der acht Server sowie in eb200_core/ui_theme.py
(ast-basiert, keine Textsuche auf der Rohdatei). Stufe A (Markup-Strings und
UI-Konstanten) ist gate-relevant und bestimmt den Rueckgabewert; Stufe B ist der
Arbeitsvorrat der uebrigen Strings (u.a. bewusst deutsch bleibende Log- und
Fehlertexte) und geht nicht in den Rueckgabewert ein.

Gesucht wird seit T-1826a nur noch in dem, was im Browser als Text erscheinen
kann (tools/ui_lang_sicht.py): HTML-Textknoten, die Werte von title, placeholder,
aria-label, alt und label, im JavaScript der Inhalt von String- und
Template-Literalen, im CSS der Inhalt von String-Literalen. Bezeichner,
CSS-Klassen, Tag- und Attributnamen, console-Aufrufe sowie Literale, die nur
aus einem kleingeschriebenen Bezeichner oder einem Pfad bestehen ('zuweisung',
'/api/befund?path='), sind Code und zaehlen nicht. lang="de" und das
de-Gebietsschema werden weiter im ganzen Code gesucht. Die Stufe (A/B) und die
Zaehler "maskiert"/"ausgenommen" bleiben wie bisher.

Kommentare in CSS/JS/HTML werden vor der Suche maskiert (T-1805b). Zeilenweise
davor erkannt (T-1805c) wird ein Ausschlussbereich zwischen einer Zeile mit
'ui-lang: ignore-begin' und der naechsten Zeile mit 'ui-lang: ignore-end':
der Bereich wird komplett ausgeblendet, auch der Code dazwischen, nicht nur
Kommentare. Gedacht fuer JS-Objekte, deren Schluessel selbst persistierte
deutsche Datenwerte sind (derzeit ausschliesslich LABELS_JS/EB200_LABELS in
eb200_core/ui_theme.py, T-1801a) - AUSDRUECKLICH NICHT dafuer, eine Stelle zu
verstecken, die man gerade nicht uebersetzen will. Ein Bereich ohne passendes
Gegenstueck oder eine Verschachtelung ist ein harter Fehler: die betroffene
Datei wird gar nicht geprueft, Rueckgabewert mindestens 1.

Aufruf:
    python tools/check_ui_lang.py [Datei ...]   Prueft; 0 wenn Stufe A leer ist, sonst 1
    python tools/check_ui_lang.py --liste       zusaetzlich jede Fundstelle einzeln
    python tools/check_ui_lang.py --stufe-b     auch den Arbeitsvorrat der Stufe B auflisten
    python tools/check_ui_lang.py --basis       schreibt/aktualisiert tools/ui_lang_basis.json
    python tools/check_ui_lang.py --selftest    Selbsttest, Rueckgabewert 0/1
"""

import argparse
import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from ui_lang_sicht import art_fuer_name, maske

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
    "eb200_core/ui_theme.py",
]

GLOSSAR_PFAD = REPO_ROOT / "docs" / "Glossar-EN.md"
BASIS_PFAD = REPO_ROOT / "tools" / "ui_lang_basis.json"

# Stufe-A-Kriterium (T-1805): Markup oder eine beliebige HTML-Entity.
MARKUP_RE = re.compile(r"<\s*[a-zA-Z/!]")
ENTITY_RE = re.compile(r"&[a-zA-Z#][a-zA-Z0-9]*;")

# Befundarten (schmaler als die Stufe-A-Entity-Regel: nur deutsche Umlaut-Entities).
UMLAUT_RE = re.compile(r"[äöüÄÖÜß]")
UMLAUT_ENTITY_RE = re.compile(r"&(auml|ouml|uuml|Auml|Ouml|Uuml|szlig|#0*228|#0*246|#0*252|#0*223);")
LANG_DE_RE = re.compile(r'lang\s*=\s*"de"')
LOCALE_RE = re.compile(r"de-AT|de-DE|localeCompare\([^)]*,\s*'de'\)")


_IGNORE_BEGIN = "ui-lang: ignore-begin"
_IGNORE_ENDE = "ui-lang: ignore-end"


def _maskiere_ausschlussbereiche(value):
    """Erkennt ZEILENWEISE im rohen Text (vor der Kommentarmaskierung, denn
    die Markierungen stehen selbst in Kommentaren) einen Ausschlussbereich
    zwischen einer Zeile mit 'ui-lang: ignore-begin' und der naechsten Zeile
    mit 'ui-lang: ignore-end' und maskiert ihn vollstaendig - vom Anfang der
    Begin-Zeile bis zum Ende der End-Zeile, unabhaengig von der
    Kommentarsyntax (Block-, Zeilen- oder HTML-Kommentar wirken gleich).

    Gedacht fuer Bereiche, die persistierte deutsche Datenwerte als Code
    enthalten (derzeit ausschliesslich EB200_LABELS in
    eb200_core/ui_theme.py, T-1801a) - AUSDRUECKLICH NICHT dafuer, Stellen zu
    verstecken, die man gerade nicht uebersetzen will. Ersetzt ab T-1805c die
    einfachere String-Suche aus T-1801a (die nur '/* ... */' fand).

    Gibt (maskierter_text, fehler) zurueck. fehler ist eine Liste von
    (relative_zeile, meldung) fuer unpaarige oder verschachtelte
    Markierungen; ist sie nicht leer, bleibt der Text UNVERAENDERT - die
    aufrufende Stelle prueft die Datei dann gar nicht (siehe
    datei_analysieren): ein unbemerkt zu grosser Ausschluss ist schlimmer als
    ein hart gemeldeter Fehler."""
    zeilen = value.split("\n")
    fehler = []
    offen_ab = None
    bereiche = []
    for idx, zeile in enumerate(zeilen):
        hat_begin = _IGNORE_BEGIN in zeile
        hat_ende = _IGNORE_ENDE in zeile
        if hat_begin and offen_ab is not None:
            fehler.append((idx, "verschachteltes 'ui-lang: ignore-begin' (offener Bereich seit Zeile {0})".format(offen_ab + 1)))
            continue
        if hat_begin:
            offen_ab = idx
            continue
        if hat_ende and offen_ab is None:
            fehler.append((idx, "'ui-lang: ignore-end' ohne vorangehendes ignore-begin"))
            continue
        if hat_ende:
            bereiche.append((offen_ab, idx))
            offen_ab = None
    if offen_ab is not None:
        fehler.append((offen_ab, "'ui-lang: ignore-begin' ohne zugehoeriges ignore-end"))

    if fehler:
        return value, fehler
    if not bereiche:
        return value, []

    zeichen = list(value)
    start_offsets = []
    pos = 0
    for zeile in zeilen:
        start_offsets.append(pos)
        pos += len(zeile) + 1

    for start_idx, ende_idx in bereiche:
        von = start_offsets[start_idx]
        bis = start_offsets[ende_idx] + len(zeilen[ende_idx])
        for j in range(von, bis):
            if zeichen[j] != "\n":
                zeichen[j] = " "
    return "".join(zeichen), []


def _maskiere_kommentare(value):
    """Ersetzt CSS-/JS-Bloeckkommentare (/* */), JS-Zeilenkommentare (//) und
    HTML-Kommentare (<!-- -->) zeichenweise durch Leerzeichen - Laenge und
    Zeilenumbrueche bleiben erhalten, damit Offset- und Zeilenrechnung exakt
    bleiben (T-1805b). Stringliterale werden nicht erkannt/uebersprungen: ein
    '//' zaehlt nur dann als Kommentarbeginn, wenn das unmittelbar
    vorangehende Nicht-Leerzeichen kein ':' ist (Schutz gegen '://' in URLs)."""
    zeichen = list(value)
    n = len(value)
    i = 0

    def maskiere_bereich(start, ende):
        for j in range(start, ende):
            if zeichen[j] != "\n":
                zeichen[j] = " "

    while i < n:
        if value.startswith("/*", i):
            ende = value.find("*/", i + 2)
            bis = ende + 2 if ende != -1 else n
            maskiere_bereich(i, bis)
            i = bis
            continue
        if value.startswith("<!--", i):
            ende = value.find("-->", i + 4)
            bis = ende + 3 if ende != -1 else n
            maskiere_bereich(i, bis)
            i = bis
            continue
        if value.startswith("//", i):
            k = i - 1
            while k >= 0 and value[k] in " \t":
                k -= 1
            vorangehend = value[k] if k >= 0 else ""
            if vorangehend != ":":
                zeilenende = value.find("\n", i)
                bis = zeilenende if zeilenende != -1 else n
                maskiere_bereich(i, bis)
                i = bis
                continue
        i += 1
    return "".join(zeichen)


def _veraenderte_relative_zeilen(original, veraendert):
    """Relative Zeilennummern (0-basiert), auf denen sich original und
    veraendert unterscheiden - fuer die Statistiken 'ausgenommen: n Zeilen'
    und 'maskiert: n Zeilen'. Da die Ausschlussmaskierung VOR der
    Kommentarmaskierung laeuft, zaehlt eine bereits ausgenommene Zeile hier
    nicht doppelt: die Kommentarmaskierung sieht dort nur noch Leerzeichen
    und findet nichts mehr zu veraendern (T-1805c)."""
    zeilen = set()
    zeile = 0
    for a, b in zip(original, veraendert):
        if a == "\n":
            zeile += 1
            continue
        if a != b:
            zeilen.add(zeile)
    return zeilen


def zeilen_offset(value, bis_offset):
    """Anzahl Zeilenumbrueche in value[:bis_offset] - fuer die Umrechnung
    von Offset innerhalb des String-Werts auf eine Zeilenverschiebung.
    Wortgleich uebernommen aus tools/check_js_blocks.py (T-1805a)."""
    return value.count("\n", 0, bis_offset)


# UI-Konstanten auf Modulebene: Name enthaelt eines dieser Woerter.
UI_KONSTANTE_WOERTER = ["HTML", "CSS", "JS", "PAGE", "BLOCK", "FORM", "TEMPLATE", "STRIP", "HEAD"]

# Funktionen, deren Koerper nie geprueft wird (Selbsttests, CLI-Einstieg).
AUSSCHLUSS_PRAEFIXE = ("_self_test",)
AUSSCHLUSS_NAMEN_EXAKT = ("main",)
AUSSCHLUSS_PRAEFIXE_ZUSAETZLICH = ("_cmd_",)

STOPPWORT_LISTE = (
    "der die das den dem des ein eine einen einem einer eines und oder nicht kein keine "
    "keinen fuer für mit von vom beim bei zum zur aus auf ist sind war wird werden wurde "
    "muss müssen darf kann koennen können soll sollen noch schon bereits nur auch wenn "
    "dann sonst alle allen jede jeder jedes mehr weniger ohne gegen ueber über unter "
    "zwischen bitte hier dort damit dabei dazu sowie bzw. usw. z.B. siehe"
).split()


def _funktionsstack_ausgeschlossen(stapel):
    for name in stapel:
        if name.startswith(AUSSCHLUSS_PRAEFIXE) or name in AUSSCHLUSS_NAMEN_EXAKT or name.startswith(AUSSCHLUSS_PRAEFIXE_ZUSAETZLICH):
            return True
    return False


def _wortmuster(wort):
    escaped = re.escape(wort)
    anfang = r"\b" if wort[0].isalnum() else ""
    ende = r"\b" if wort[-1].isalnum() else ""
    return anfang + escaped + ende


STOPPWORT_RE = re.compile("|".join(_wortmuster(w) for w in STOPPWORT_LISTE), re.IGNORECASE)


def _ist_trennzeile(zeile):
    z = zeile.strip()
    return bool(z) and "-" in z and set(z) <= set("|-: ")


def _begriff_bereinigen(text):
    """Backticks und HTML-Entities entfernen, dann alles ab der ersten '('
    abschneiden und erneut strippen - fuer Deutsch- UND Englisch-Spalte
    gleichermassen (T-1805d). 'Kanal (Scan-Schritt)' -> 'Kanal',
    'Symbolrate (Bd)' -> 'Symbolrate': der Zusatz in Klammern kommt im Code
    nie in dieser Form vor und machte den Begriff bisher unsichtbar."""
    begriff = text.strip()
    begriff = begriff.replace("`", "")
    begriff = re.sub(r"&[a-zA-Z#][a-zA-Z0-9]*;", "", begriff)
    begriff = begriff.split("(", 1)[0]
    return begriff.strip()


def glossar_einlesen(pfad=GLOSSAR_PFAD):
    """Liest nur Tabellen, deren erste Kopfzelle exakt 'Deutsch' lautet.
    Abschnitt 4 (Kopfzelle 'Feld') und 'Schreibweisen' (Kopfzelle 'Regel')
    werden bewusst uebersprungen: dort stehen persistierte Datenwerte, die
    im Code bleiben muessen und kein Befund sind.

    Ein deutscher Begriff wird NICHT in die Suchliste aufgenommen, wenn er
    (nach demselben Abschneiden der Klammerzusaetze) einer Variante der
    Englisch-Spalte derselben Zeile entspricht, ohne Beachtung der
    Gross-/Kleinschreibung (T-1805d): ein Wort, das in beiden Sprachen gleich
    lautet, kann keinen Uebersetzungsbedarf anzeigen - ein Fund darauf waere
    nie abarbeitbar (Beispiel: 'Filter', vgl. CSS filter:brightness(...)).
    Die Englisch-Spalte selbst bleibt unberuehrt, sie ist weiterhin der
    erlaubte Wortschatz."""
    if not pfad.is_file():
        print("WARNUNG: {0} nicht gefunden - Art 'glossar' wird uebersprungen.".format(pfad), file=sys.stderr)
        return None

    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    begriffe = set()
    zeilen_gezaehlt = 0
    gleichlaute_uebersprungen = 0
    englisch_woerter = set()
    i = 0
    n = len(zeilen)
    while i < n:
        zeile = zeilen[i].strip()
        if zeile.startswith("|") and zeile.endswith("|") and i + 1 < n and _ist_trennzeile(zeilen[i + 1]):
            kopfzellen = [z.strip() for z in zeile.strip("|").split("|")]
            ist_deutsch = bool(kopfzellen) and kopfzellen[0] == "Deutsch"
            i += 2
            while i < n:
                folgezeile = zeilen[i]
                gestrippt = folgezeile.strip()
                if not (gestrippt.startswith("|") and gestrippt.endswith("|")):
                    break
                if ist_deutsch:
                    zellen = [z.strip() for z in gestrippt.strip("|").split("|")]
                    if len(zellen) >= 2:
                        zeilen_gezaehlt += 1
                        englisch_varianten = {
                            _begriff_bereinigen(v).lower() for v in zellen[1].split(",")
                        }
                        # ganze Englisch-Zelle, auch der Teil in Klammern
                        englisch_woerter.update(re.findall("[a-z][a-z-]*", zellen[1].lower()))
                        for variante in zellen[0].split(","):
                            begriff = _begriff_bereinigen(variante)
                            if len(begriff) < 3:
                                continue
                            if any(c in begriff for c in "<{>"):
                                continue
                            if begriff.lower() in englisch_varianten:
                                gleichlaute_uebersprungen += 1
                                continue
                            begriffe.add(begriff)
                i += 1
            continue
        i += 1

    # T-1826a: ein deutscher Begriff, der als Wort im englischen Wortschatz
    # IRGENDEINER Zeile steht ('Modulation' in 'modulation index', 'Raster' in
    # 'jitter (half-bit raster)'), kann im
    # englischen Text keinen Uebersetzungsbedarf anzeigen.
    englisch_uebersprungen = {b for b in begriffe if b.lower() in englisch_woerter}
    begriffe -= englisch_uebersprungen

    print(
        "Glossar: {0} Suchbegriffe aus {1} Zeilen ({2} gleichlautend uebersprungen, "
        "{3} als englisches Wort)".format(
            len(begriffe), zeilen_gezaehlt, gleichlaute_uebersprungen, len(englisch_uebersprungen)
        )
    )
    return begriffe


def glossar_regex(begriffe):
    if not begriffe:
        return None
    teile = []
    for wort in sorted(begriffe, key=len, reverse=True):
        teile.append(_wortmuster(wort))
    return re.compile("|".join(teile), re.IGNORECASE)


def _funde_je_art(text, glossar_re, code_text=None):
    """Liefert (art, start, ende, wort) fuer JEDEN Treffer im Text (nicht nur
    einen Boolean pro Art) - Grundlage fuer die Verortung je Fundstelle
    (T-1805a). Bei umlaut/entity ist 'wort' das Zeichen bzw. die Entity, bei
    stoppwort/glossar das Wort in Kleinschreibung, bei lang/locale das
    gefundene Muster unveraendert."""
    ergebnisse = []
    for treffer in UMLAUT_RE.finditer(text):
        ergebnisse.append(("umlaut", treffer.start(), treffer.end(), treffer.group(0)))
    for treffer in UMLAUT_ENTITY_RE.finditer(text):
        ergebnisse.append(("entity", treffer.start(), treffer.end(), treffer.group(0)))
    for treffer in STOPPWORT_RE.finditer(text):
        ergebnisse.append(("stoppwort", treffer.start(), treffer.end(), treffer.group(0).lower()))
    if glossar_re is not None:
        for treffer in glossar_re.finditer(text):
            ergebnisse.append(("glossar", treffer.start(), treffer.end(), treffer.group(0).lower()))
    if code_text is None:
        code_text = text
    for treffer in LANG_DE_RE.finditer(code_text):
        ergebnisse.append(("lang", treffer.start(), treffer.end(), treffer.group(0)))
    for treffer in LOCALE_RE.finditer(code_text):
        ergebnisse.append(("locale", treffer.start(), treffer.end(), treffer.group(0)))
    return ergebnisse


def ist_stufe_a(text, ist_ui_konstante):
    if ist_ui_konstante:
        return True
    if MARKUP_RE.search(text):
        return True
    if ENTITY_RE.search(text):
        return True
    return False


def _ausschnitt(text, start, ende):
    """Bis zu 30 Zeichen vor und nach dem Treffer [start:ende), Zeilenumbrueche
    und Tabs zu Leerzeichen geglaettet, abgeschnittene Seiten mit '...' markiert
    (T-1805a - der Treffer selbst ist immer enthalten)."""
    vorn = max(0, start - 30)
    hinten = min(len(text), ende + 30)
    stueck = text[vorn:hinten]
    stueck = re.sub(r"[\t\n]+", " ", stueck)
    stueck = re.sub(r" {2,}", " ", stueck).strip()
    praefix = "…" if vorn > 0 else ""
    suffix = "…" if hinten < len(text) else ""
    return praefix + stueck + suffix


def _docstring_ids(baum):
    """Erster Ausdruck von Module/ClassDef/FunctionDef/AsyncFunctionDef, falls
    ein String-Literal - unabhaengig vom Funktionsstack immer ausgeschlossen."""
    ids = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(knoten, "body", None)
            if body:
                erster = body[0]
                if isinstance(erster, ast.Expr) and isinstance(erster.value, ast.Constant) and isinstance(erster.value.value, str):
                    ids.add(id(erster.value))
    return ids


def _ausschluss_call_ids(baum):
    """Strings, die direktes Argument von print(...) sind, sowie alle Strings
    innerhalb von add_argument/add_parser/ArgumentParser-Aufrufen (argparse-Hilfe)."""
    print_ids = set()
    argparse_ids = set()
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        ziel = knoten.func
        name = ziel.id if isinstance(ziel, ast.Name) else (ziel.attr if isinstance(ziel, ast.Attribute) else None)
        if name == "print":
            for arg in knoten.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    print_ids.add(id(arg))
                elif isinstance(arg, ast.JoinedStr):
                    print_ids.add(id(arg))
        elif name in ("add_argument", "add_parser", "ArgumentParser"):
            for unter in ast.walk(knoten):
                if unter is knoten:
                    continue
                if isinstance(unter, ast.Constant) and isinstance(unter.value, str):
                    argparse_ids.add(id(unter))
                elif isinstance(unter, ast.JoinedStr):
                    argparse_ids.add(id(unter))
    return print_ids, argparse_ids


def _ui_konstante_ids(baum):
    """Strings (direkt oder ueber eine Zuweisung) in einer Modulebene-Konstante,
    deren Name eines der UI_KONSTANTE_WOERTER als Teil traegt. Liefert
    id -> Name der Konstante (T-1826a: der Name bestimmt die Art der
    Sichtbarkeitsmaske, *JS* -> js, *CSS* -> css)."""
    ids = {}
    for stmt in baum.body:
        ziel_namen = []
        wert = None
        if isinstance(stmt, ast.Assign):
            wert = stmt.value
            ziel_namen = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            wert = stmt.value
            ziel_namen = [stmt.target.id]
        if wert is None or not ziel_namen:
            continue
        if any(any(wort in name for wort in UI_KONSTANTE_WOERTER) for name in ziel_namen):
            for unter in ast.walk(wert):
                if isinstance(unter, ast.Constant) and isinstance(unter.value, str):
                    ids[id(unter)] = ziel_namen[0]
                elif isinstance(unter, ast.JoinedStr):
                    ids[id(unter)] = ziel_namen[0]
    return ids


class _StringSammler(ast.NodeVisitor):
    """Sammelt (zeile, text, ist_ui_konstante) fuer alle nicht ausgeschlossenen
    String-Konstanten. JoinedStr (f-String) ruft bewusst KEIN generic_visit auf:
    die Literalteile werden einzeln geprueft, aber nicht als eigene
    String-Konstanten doppelt gezaehlt."""

    def __init__(self, docstring_ids, print_ids, argparse_ids, ui_konstante_ids):
        self.docstring_ids = docstring_ids
        self.print_ids = print_ids
        self.argparse_ids = argparse_ids
        self.ui_konstante_ids = ui_konstante_ids
        self.funktionsstapel = []
        self.treffer = []

    def visit_FunctionDef(self, node):
        self.funktionsstapel.append(node.name)
        self.generic_visit(node)
        self.funktionsstapel.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Constant(self, node):
        if not isinstance(node.value, str):
            return
        if id(node) in self.docstring_ids or id(node) in self.print_ids or id(node) in self.argparse_ids:
            return
        if _funktionsstack_ausgeschlossen(self.funktionsstapel):
            return
        self.treffer.append((node.lineno, node.value, id(node) in self.ui_konstante_ids,
                             art_fuer_name(self.ui_konstante_ids.get(id(node)))))

    def visit_JoinedStr(self, node):
        if id(node) in self.print_ids or id(node) in self.argparse_ids:
            return
        if _funktionsstack_ausgeschlossen(self.funktionsstapel):
            return
        ist_ui = id(node) in self.ui_konstante_ids
        art = art_fuer_name(self.ui_konstante_ids.get(id(node)))
        for teil in node.values:
            if isinstance(teil, ast.Constant) and isinstance(teil.value, str):
                self.treffer.append((teil.lineno, teil.value, ist_ui, art))


class Fundstelle:
    def __init__(self, relpfad, zeile, art, stufe, wort, ausschnitt):
        self.relpfad = relpfad
        self.zeile = zeile
        self.art = art
        self.stufe = stufe
        self.wort = wort
        self.ausschnitt = ausschnitt


class Dateiergebnis:
    def __init__(self, relpfad):
        self.relpfad = relpfad
        self.stufe_a = 0
        self.stufe_b = 0
        self.stufe_a_zeilen = set()
        self.stufe_b_zeilen = set()
        self.maskierte_zeilen = set()
        self.ausgenommene_zeilen = set()
        self.markierungsfehler = []
        self.fundstellen = []
        self.werkzeugfehler = None


def datei_analysieren(pfad, glossar_re):
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

    docstring_ids = _docstring_ids(baum)
    print_ids, argparse_ids = _ausschluss_call_ids(baum)
    ui_konstante_ids = _ui_konstante_ids(baum)

    sammler = _StringSammler(docstring_ids, print_ids, argparse_ids, ui_konstante_ids)
    sammler.visit(baum)

    gesehen = set()
    for zeile_basis, text, ist_ui, art_sicht in sammler.treffer:
        ausgenommen, fehler = _maskiere_ausschlussbereiche(text)
        if fehler:
            for rel_zeile, meldung in fehler:
                ergebnis.markierungsfehler.append((zeile_basis + rel_zeile, meldung))
            continue

        for rel_zeile in _veraenderte_relative_zeilen(text, ausgenommen):
            ergebnis.ausgenommene_zeilen.add(zeile_basis + rel_zeile)

        maskiert = _maskiere_kommentare(ausgenommen)

        for rel_zeile in _veraenderte_relative_zeilen(ausgenommen, maskiert):
            ergebnis.maskierte_zeilen.add(zeile_basis + rel_zeile)

        stufe = "A" if ist_stufe_a(maskiert, ist_ui) else "B"
        sichtbar = maske(maskiert, art_sicht)
        for art, start, ende, wort in _funde_je_art(sichtbar, glossar_re, maskiert):
            zeile = zeile_basis + zeilen_offset(maskiert, start)
            schluessel = (zeile, art, wort)
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
            fund = Fundstelle(str(relpfad), zeile, art, stufe, wort, _ausschnitt(maskiert, start, ende))
            ergebnis.fundstellen.append(fund)
            if stufe == "A":
                ergebnis.stufe_a += 1
                ergebnis.stufe_a_zeilen.add(zeile)
            else:
                ergebnis.stufe_b += 1
                ergebnis.stufe_b_zeilen.add(zeile)

    if ergebnis.markierungsfehler:
        # "keine Pruefung dieser Datei": ein unpaariger/verschachtelter
        # Marker macht die gesamte Datei unbewertbar, nicht nur den
        # betroffenen String - sonst waeren Teilergebnisse irrefuehrend.
        ergebnis.stufe_a = 0
        ergebnis.stufe_b = 0
        ergebnis.stufe_a_zeilen = set()
        ergebnis.stufe_b_zeilen = set()
        ergebnis.maskierte_zeilen = set()
        ergebnis.ausgenommene_zeilen = set()
        ergebnis.fundstellen = []
        return ergebnis

    ergebnis.fundstellen.sort(key=lambda f: f.zeile)
    return ergebnis


def _kurzhash():
    try:
        lauf = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return lauf.stdout.strip()
    except Exception:
        return None


def _basis_laden():
    if not BASIS_PFAD.is_file():
        return None
    try:
        return json.loads(BASIS_PFAD.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _basis_schreiben(ergebnisse):
    daten = {
        "format": "eb200-uilang-v1",
        "erhoben": date.today().isoformat(),
        "commit": _kurzhash(),
        "dateien": {
            e.relpfad: {
                "stufe_a": e.stufe_a,
                "stufe_a_zeilen": len(e.stufe_a_zeilen),
                "stufe_b": e.stufe_b,
                "stufe_b_zeilen": len(e.stufe_b_zeilen),
                "maskiert_zeilen": len(e.maskierte_zeilen),
                "ausgenommen_zeilen": len(e.ausgenommene_zeilen),
            }
            for e in ergebnisse
            if e.werkzeugfehler is None and not e.markierungsfehler
        },
    }
    text = json.dumps(daten, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    BASIS_PFAD.write_text(text, encoding="utf-8", newline="\r\n")


def _selftest_quelle():
    """Baut den synthetischen Quelltext zeilenweise auf und liefert dazu die
    Startzeilen der T12-T16-Bloecke - vermeidet von Hand gezaehlte Magiczahlen
    bei mehrzeiligen Konstanten (T-1805a)."""
    zeilen = []

    def add(zeile_text):
        zeilen.append(zeile_text)

    add('"""Modul-Docstring mit Umlaut: Übersicht."""')  # 1
    add("")  # 2
    add('HTML_PAGE = "<h1>Übersicht</h1>"')  # 3
    add('HTML_ENTITY = "<p>&uuml;ber</p>"')  # 4
    add('HTML_LANG = \'<html lang="de">\'')  # 5
    add('HTML_FSTRING = f"<div>{x} fuer alles</div>"')  # 6
    add('KLARTEXT = "Dies ist ein deutscher Beispieltext ohne Markup und ohne Sonderzeichen"')  # 7
    add('NUR_LAEUFT = "laeuft"')  # 8
    add("")  # 9
    add("def normal():")  # 10
    add('    """Docstring mit Umlaut: Grösse."""')  # 11
    add('    print("Laeuft nicht")')  # 12
    add("    return 1")  # 13
    add("")  # 14
    add("def _self_test():")  # 15
    add('    geheim = "Innerer Text mit Umlaut: Prüfung"')  # 16
    add("    return geheim")  # 17
    add("")  # 18

    positionen = {}

    positionen["html_multi"] = len(zeilen) + 1
    add('HTML_MULTI = """Übersicht')
    add("zeile zwei")
    add("Grösse")
    add("zeile vier")
    add('Prüfung"""')

    add("")
    positionen["stopp_zweimal"] = len(zeilen) + 1
    add('STOPP_ZWEIMAL = "fuer Antenne fuer Kabel"')

    positionen["stopp_zwei"] = len(zeilen) + 1
    add('STOPP_ZWEI = "mit ohne Antenne"')

    add("")
    positionen["html_gross"] = len(zeilen) + 1
    add('HTML_GROSS = """')
    for i in range(200):
        add("Zeile {0}: Übersicht für die Messung ohne Markup".format(i))
    add('"""')

    add("")
    positionen["html_style"] = len(zeilen) + 1
    add('HTML_STYLE = "<style>/* Grundregeln fuer die Tabellen */</style>"')

    positionen["html_script_line"] = len(zeilen) + 1
    add('HTML_SCRIPT_LINE = """<script>// Zusaetze dieser Seite')
    add('var x=1;</script>"""')

    positionen["html_url"] = len(zeilen) + 1
    add('HTML_URL = \'<a href="https://example.com/die/seite">Öffnen</a>\'')

    positionen["html_comment_html"] = len(zeilen) + 1
    add('HTML_COMMENT_HTML = "<!-- Hinweis fuer die Wartung -->"')

    add("")
    positionen["html_nach_kommentar"] = len(zeilen) + 1
    add('HTML_NACH_KOMMENTAR = """/* Zeile eins')
    add("Zeile zwei")
    add("Zeile drei */")
    add('Übersicht"""')

    add("")
    positionen["css_content"] = len(zeilen) + 1
    add('CSS_CONTENT = \'content:"Long-Run-Job anlegen"\'')

    add("")
    positionen["html_ignore_bereich"] = len(zeilen) + 1
    add('HTML_IGNORE_BEREICH = """/* ui-lang: ignore-begin - persistierte Codes */')
    add("  var TABELLE = {zuweisung:'Allocation',dienst:'Service',ein:'On',aus:'Off'};")
    add("  /* ui-lang: ignore-end */")
    add('  Übersicht"""')

    add("")
    positionen["html_ignore_mit_kommentar"] = len(zeilen) + 1
    add('HTML_IGNORE_MIT_KOMMENTAR = """/* ui-lang: ignore-begin */')
    add("  // ein Kommentar mit fuer und aus")
    add("  var X = {ein:'On'};")
    add("  /* ui-lang: ignore-end */")
    add('  Prüfung"""')

    add("")
    positionen["html_code"] = len(zeilen) + 1
    add('HTML_CODE = "<script>const skala = levelScale(); var k = ' + "'zuweisung'" + ';</script>"')
    positionen["html_titel"] = len(zeilen) + 1
    add("HTML_TITEL = '" + '<span class="spektrum-off" title="Übersicht">x</span>' + "'")
    positionen["html_konsole"] = len(zeilen) + 1
    add('HTML_KONSOLE = "<script>console.warn(' + "'Katalog nicht ladbar'" + ');</script>"')

    return "\n".join(zeilen) + "\n", positionen


def selftest():
    tmp_dir = Path(tempfile.mkdtemp(prefix="check_ui_lang_selftest_"))
    try:
        quelltext, positionen = _selftest_quelle()
        synth_datei = tmp_dir / "synthetisch.py"
        synth_datei.write_text(quelltext, encoding="utf-8")

        glossar_begriffe = glossar_einlesen()
        glossar_re = glossar_regex(glossar_begriffe) if glossar_begriffe else None
        ergebnis = datei_analysieren(synth_datei, glossar_re)

        def _bei_zeile(zeile):
            return [f for f in ergebnis.fundstellen if f.zeile == zeile]

        erwartungen = []
        erwartungen.append(("T1", "Modul-Docstring mit Umlaut ohne Befund", not _bei_zeile(1)))
        erwartungen.append(("T2", "Kommentare tauchen im AST nicht auf (Absicht dokumentiert)", True))

        t3 = _bei_zeile(3)
        erwartungen.append(("T3", "HTML_PAGE genau ein Stufe-A-Befund der Art umlaut",
                             len(t3) == 1 and t3[0].art == "umlaut" and t3[0].stufe == "A"))

        t4 = _bei_zeile(4)
        erwartungen.append(("T4", "HTML_ENTITY Befund der Art entity",
                             len(t4) == 1 and t4[0].art == "entity" and t4[0].stufe == "A"))

        t5 = _bei_zeile(5)
        erwartungen.append(("T5", "HTML_LANG Befund der Art lang",
                             len(t5) == 1 and t5[0].art == "lang" and t5[0].stufe == "A"))

        erwartungen.append(("T6", "String in _self_test() ohne Befund", not _bei_zeile(16)))
        erwartungen.append(("T7", "print(...)-Argument ohne Befund", not _bei_zeile(12)))

        t6 = _bei_zeile(6)
        erwartungen.append(("T8", "f-String-Literalteil in HTML-Konstante, Art stoppwort",
                             len(t6) == 1 and t6[0].art == "stoppwort" and t6[0].stufe == "A"))

        t7 = _bei_zeile(7)
        erwartungen.append(("T9", "deutscher Text ohne Markup/UI-Konstante landet in Stufe B",
                             len(t7) >= 1 and all(f.stufe == "B" for f in t7)))

        erwartungen.append(("T10", "'laeuft' (persistierter Datenwert) ohne glossar-Befund",
                             not any(f.art == "glossar" for f in _bei_zeile(8))))

        leer_datei = tmp_dir / "leer.py"
        leer_datei.write_text(
            'KLARTEXT = "Dies ist ein deutscher Beispieltext ohne Markup"\n', encoding="utf-8"
        )
        with redirect_stdout(io.StringIO()):
            rc_mit_befund = main([str(synth_datei)])
            rc_ohne_befund = main([str(leer_datei)])

        erwartungen.append(("T11", "Rueckgabewert 1 bei mindestens einem Stufe-A-Befund", rc_mit_befund == 1))
        erwartungen.append(("T11", "Rueckgabewert 0 bei leerer Stufe A", rc_ohne_befund == 0))

        start_multi = positionen["html_multi"]
        umlaut_zeilen_multi = sorted({f.zeile for f in ergebnis.fundstellen if f.art == "umlaut" and start_multi <= f.zeile < start_multi + 5})
        erwartungen.append(("T12", "HTML_MULTI: drei Umlaut-Zeilen mit Abstand 2 und 2",
                             umlaut_zeilen_multi == [start_multi, start_multi + 2, start_multi + 4]))

        zeile_zweimal = positionen["stopp_zweimal"]
        t13 = [f for f in ergebnis.fundstellen if f.zeile == zeile_zweimal and f.art == "stoppwort"]
        erwartungen.append(("T13", "dasselbe Stoppwort zweimal in einer Zeile erzeugt genau einen Fund",
                             len(t13) == 1 and t13[0].wort == "fuer"))

        zeile_zwei = positionen["stopp_zwei"]
        t14 = [f for f in ergebnis.fundstellen if f.zeile == zeile_zwei and f.art == "stoppwort"]
        erwartungen.append(("T14", "zwei verschiedene Stoppwoerter in einer Zeile erzeugen zwei Funde",
                             len(t14) == 2 and {f.wort for f in t14} == {"mit", "ohne"}))

        erwartungen.append(("T15", "Ausschnitt eines Funds enthaelt das gefundene Wort",
                             len(t13) == 1 and t13[0].wort in t13[0].ausschnitt.lower()))

        start_gross = positionen["html_gross"]
        t16 = [f for f in ergebnis.fundstellen if f.zeile >= start_gross and f.zeile < positionen["html_style"] - 1]
        erwartungen.append(("T16", "200-zeilige deutsche HTML-Konstante erzeugt > 50 Funde (Regression)",
                             len(t16) > 50))

        erwartungen.append(("T17", "CSS-Blockkommentar in <style> erzeugt keinen Fund",
                             not _bei_zeile(positionen["html_style"])))

        start_script = positionen["html_script_line"]
        erwartungen.append(("T18", "JS-Zeilenkommentar in <script> erzeugt keinen Fund",
                             not _bei_zeile(start_script) and not _bei_zeile(start_script + 1)))

        t19 = _bei_zeile(positionen["html_url"])
        erwartungen.append(("T19", "'://' in URL wird nicht als Kommentar behandelt, Text danach wird gefunden",
                             any(f.art == "umlaut" for f in t19)))

        erwartungen.append(("T20", "HTML-Kommentar <!-- --> erzeugt keinen Fund",
                             not _bei_zeile(positionen["html_comment_html"])))

        zeile_nach_kommentar = positionen["html_nach_kommentar"] + 3
        t21 = _bei_zeile(zeile_nach_kommentar)
        erwartungen.append(("T21", "Text nach mehrzeiligem Kommentarblock wird gefunden, Zeile stimmt",
                             len(t21) == 1 and t21[0].art == "umlaut"))

        t22 = _bei_zeile(positionen["css_content"])
        erwartungen.append(("T22", "CSS-Stringliteral (kein Kommentar) wird weiterhin geprueft",
                             any(f.art == "glossar" and "anlegen" in f.wort for f in t22)))

        start_ignore = positionen["html_ignore_bereich"]
        ignoriert_leer = not _bei_zeile(start_ignore) and not _bei_zeile(start_ignore + 1) and not _bei_zeile(start_ignore + 2)
        t23_danach = _bei_zeile(start_ignore + 3)
        erwartungen.append(("T23", "Ausschlussbereich mit deutschen Woertern erzeugt keinen Fund, Text ausserhalb bleibt geprueft",
                             ignoriert_leer and any(f.art == "umlaut" for f in t23_danach)))

        fehler_datei = tmp_dir / "fehler.py"
        fehler_datei.write_text(
            'HTML_FEHLER = "<!-- ui-lang: ignore-begin - kein Ende -->"\n', encoding="utf-8"
        )
        stderr_puffer = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr_puffer):
            rc_fehler = main([str(fehler_datei)])
        erwartungen.append(("T24", "ignore-begin ohne ignore-end erzeugt Fehler und Rueckgabewert 1",
                             rc_fehler == 1 and "FEHLER" in stderr_puffer.getvalue()))

        erwartungen.append(("T25", "Fund nach mehrzeiligem Ausschlussbereich hat die richtige Zeilennummer",
                             len(t23_danach) == 1 and t23_danach[0].zeile == start_ignore + 3))

        erwartete_ausgenommene = {start_ignore, start_ignore + 1, start_ignore + 2}
        erwartungen.append(("T26", "'ausgenommen: n Zeilen' zaehlt den Bereich einschliesslich beider Markierungszeilen",
                             erwartete_ausgenommene <= ergebnis.ausgenommene_zeilen))

        start_ignore_kommentar = positionen["html_ignore_mit_kommentar"]
        t27_innen = (_bei_zeile(start_ignore_kommentar) + _bei_zeile(start_ignore_kommentar + 1)
                     + _bei_zeile(start_ignore_kommentar + 2) + _bei_zeile(start_ignore_kommentar + 3))
        t27_danach = _bei_zeile(start_ignore_kommentar + 4)
        erwartungen.append(("T27", "Ausschlussbereich mit eingebettetem Kommentar: kein Fehler, keine Doppelzaehlung",
                             not ergebnis.markierungsfehler and not t27_innen and any(f.art == "umlaut" for f in t27_danach)))

        synth_glossar = tmp_dir / "glossar_test.md"
        synth_glossar.write_text(
            "| Deutsch | English |\n"
            "|---|---|\n"
            "| Kanal (Scan-Schritt) | channel |\n"
            "| Segment | segment |\n"
            "| Filter, Suche, Sortieren | Filter, Search, Sort |\n",
            encoding="utf-8",
        )
        with redirect_stdout(io.StringIO()) as glossar_puffer:
            test_begriffe = glossar_einlesen(synth_glossar)
        glossar_meldung = glossar_puffer.getvalue()
        test_re = glossar_regex(test_begriffe)

        erwartungen.append(("T28", "'Kanal (Scan-Schritt) | channel' ergibt Suchbegriff 'Kanal', findet Text damit",
                             "Kanal" in test_begriffe and bool(test_re.search("der Kanal ist frei"))))

        erwartungen.append(("T29", "'Segment | segment' erzeugt keinen Suchbegriff und keinen glossar-Fund",
                             "Segment" not in test_begriffe
                             and not any(art == "glossar" for art, _, _, _ in _funde_je_art("Dieses Segment ist neu", test_re))))

        erwartungen.append(("T30", "'Filter, Suche, Sortieren | Filter, Search, Sort' liefert Suche/Sortieren, nicht Filter",
                             "Suche" in test_begriffe and "Sortieren" in test_begriffe and "Filter" not in test_begriffe))

        erwartungen.append(("T31", "gemeldete Zahl uebersprungener Gleichlaute stimmt (Filter, Segment = 2)",
                             "(2 gleichlautend uebersprungen," in glossar_meldung))

        erwartungen.append(("T32", "JS-Bezeichner und Code-Literal ('zuweisung') erzeugen keinen Fund",
                             not _bei_zeile(positionen["html_code"])))

        t33 = _bei_zeile(positionen["html_titel"])
        erwartungen.append(("T33", "title-Wert wird geprueft, CSS-Klasse nicht",
                             any(f.art == "umlaut" for f in t33) and not any(f.art == "glossar" for f in t33)))

        erwartungen.append(("T34", "console-Aufruf erzeugt keinen Fund",
                             not _bei_zeile(positionen["html_konsole"])))

        synth_glossar2 = tmp_dir / "glossar_test2.md"
        synth_glossar2.write_text(chr(10).join([
            "| Deutsch | English |",
            "|---|---|",
            "| Demodulation, Modulation | demodulation, demod |",
            "| Modulationsindex | modulation index |",
        ]) + chr(10), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as glossar_puffer2:
            test_begriffe2 = glossar_einlesen(synth_glossar2)
        erwartungen.append(("T35", "Begriff, der ein englisches Wort des Glossars ist ('Modulation'), faellt heraus",
                             test_begriffe2 == {"Modulationsindex"}
                             and "1 als englisches Wort" in glossar_puffer2.getvalue()))

        alle_ok = True
        for kennung, beschreibung, ok in erwartungen:
            status = "ok" if ok else "FEHLGESCHLAGEN"
            print("{0} {1}: {2}".format(kennung, status, beschreibung))
            alle_ok = alle_ok and ok

        if alle_ok:
            print("Selftest OK")
            return 0
        print("Selftest FEHLGESCHLAGEN", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gate fuer deutsche Reste in der WebGUI (Meilenstein 18)")
    parser.add_argument("dateien", nargs="*", help="zu pruefende .py-Dateien (relativ zum Repo-Root)")
    parser.add_argument("--liste", action="store_true", help="jede Fundstelle einzeln ausgeben")
    parser.add_argument("--stufe-b", action="store_true", dest="stufe_b", help="Arbeitsvorrat der Stufe B mit auflisten")
    parser.add_argument("--basis", action="store_true", help="tools/ui_lang_basis.json schreiben/aktualisieren")
    parser.add_argument("--selftest", action="store_true", help="Werkzeug an einer synthetischen Datei selbst testen")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    glossar_begriffe = glossar_einlesen()
    glossar_re = glossar_regex(glossar_begriffe) if glossar_begriffe else None

    dateiliste = args.dateien if args.dateien else STANDARDDATEIEN
    pfade = [(REPO_ROOT / d) for d in dateiliste]

    ergebnisse = [datei_analysieren(p, glossar_re) for p in pfade]

    basis = None if args.basis else _basis_laden()

    for ergebnis in ergebnisse:
        if ergebnis.werkzeugfehler is not None:
            print("FEHLER: {0}: {1}".format(ergebnis.relpfad, ergebnis.werkzeugfehler), file=sys.stderr)
            continue

        if ergebnis.markierungsfehler:
            for zeile, meldung in ergebnis.markierungsfehler:
                print("FEHLER: {0}:{1}: {2}".format(ergebnis.relpfad, zeile, meldung), file=sys.stderr)
            continue

        zusatz = ""
        if basis is not None:
            eintrag = basis.get("dateien", {}).get(ergebnis.relpfad)
            if eintrag is not None:
                alte_summe = eintrag.get("stufe_a", 0) + eintrag.get("stufe_b", 0)
                diff = (ergebnis.stufe_a + ergebnis.stufe_b) - alte_summe
                zusatz = " ({0:+d})".format(diff)

        print("{0}  A: {1} in {2} Zeilen   B: {3} in {4} Zeilen   maskiert: {5} Zeilen   ausgenommen: {6} Zeilen{7}".format(
            ergebnis.relpfad,
            ergebnis.stufe_a, len(ergebnis.stufe_a_zeilen),
            ergebnis.stufe_b, len(ergebnis.stufe_b_zeilen),
            len(ergebnis.maskierte_zeilen),
            len(ergebnis.ausgenommene_zeilen),
            zusatz,
        ))

        if args.liste:
            for fund in ergebnis.fundstellen:
                if fund.stufe == "B" and not args.stufe_b:
                    continue
                print("{0}:{1}: {2} [{3}] {4}".format(fund.relpfad, fund.zeile, fund.art, fund.stufe, fund.ausschnitt))

    gueltige = [e for e in ergebnisse if e.werkzeugfehler is None and not e.markierungsfehler]
    gesamt_a = sum(e.stufe_a for e in gueltige)
    gesamt_b = sum(e.stufe_b for e in gueltige)
    print("Gesamt: Stufe A {0}, Stufe B {1}".format(gesamt_a, gesamt_b))

    if args.basis:
        _basis_schreiben(ergebnisse)
        print("Basiswert geschrieben: {0}".format(BASIS_PFAD))

    if any(e.werkzeugfehler is not None for e in ergebnisse):
        return 2
    if any(e.markierungsfehler for e in ergebnisse):
        return 1
    return 1 if gesamt_a > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
