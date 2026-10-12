#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/ui_lang_sicht.py

Sichtbarkeitsmaske fuer tools/check_ui_lang.py (T-1826a, Meilenstein 18).

check_ui_lang.py sucht deutsche Woerter in den HTML/CSS/JS-Bloecken der Server.
Bis T-1826a lief die Suche ueber den ganzen Block, also auch ueber Code:
JavaScript-Variablen (skala, pegel), CSS-Klassen (spektrum-off), Tag- und
Attributnamen. Diese Treffer sind kein Anzeigetext und waren nie abarbeitbar.

maske(text, art) liefert einen gleich langen Text, in dem alles, was im Browser
NICHT als Text erscheinen kann, durch Leerzeichen ersetzt ist. Zeilenumbrueche
bleiben stehen, damit die Zeilenrechnung des Aufrufers exakt bleibt.
Sichtbar bleiben:

- HTML: Textknoten und die Werte der Attribute in ANZEIGE_ATTRIBUTE
  (title, placeholder, aria-label, alt, label). Tag-Namen, alle uebrigen
  Attribute samt Wert (class, id, href, data-*, value ...) werden maskiert.
- <script>...</script> bzw. art "js": nur der Inhalt von String- und
  Template-Literalen; darin gilt wieder die HTML-Regel (JS baut Markup als
  Zeichenkette). Bezeichner, Schluesselwoerter, Zahlen, regulaere Ausdruecke
  und die ${...}-Ausdruecke eines Template-Literals sind Code.
- <style>...</style> bzw. art "css": nur der Inhalt von String-Literalen
  (content:'...').

Kommentare hat der Aufrufer vorher schon maskiert (_maskiere_kommentare).
Die Maske ist eine Annaeherung, kein Parser: HTML-Bruchstuecke in
JS-Zeichenketten ('<a href="' + url + '">Text</a>') werden je Stueck
behandelt - ein offenes Tag am Ende und ein Tag-Rest am Anfang eines Stuecks
gelten als Markup. Im Zweifel bleibt Text SICHTBAR: ein Fehlbefund ist
harmlos, ein verschluckter deutscher Text nicht.

Nur Standardbibliothek. Selbsttest: python tools/ui_lang_sicht.py --selftest
"""

import re
import sys

ANZEIGE_ATTRIBUTE = ("title", "placeholder", "aria-label", "alt", "label")

_TAG_RE = re.compile(r"<[^<>]*>")
_TAG_OFFEN_RE = re.compile(r"<[A-Za-z/!][^<>]*$")
_TAG_REST_RE = re.compile(r"^[^<>]*>")
_ATTR_RE = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*("([^"]*)"|'([^']*)')""")
_ATTR_OFFEN_RE = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(["'])([^"'<>]*)$""")
_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>", re.IGNORECASE)

# Ein JS-Stringliteral, das nur aus einem kleingeschriebenen Bezeichner
# besteht ('zuweisung', 'alle', 'pane-kanaele', '-neu', 'art='), oder ein Pfad
# ('/api/befund?path='), ist ein Code, Schluessel oder Pfad - kein Anzeigetext.
# Anzeigetext ist ein Satz, ein Wort in Sentence case oder enthaelt Leerzeichen.
_CODE_TOKEN_RE = re.compile(r"^-?[a-z][a-z0-9_-]*=?$")
_PFAD_RE = re.compile(r"^/[A-Za-z0-9_/?.=&-]*$")
_CONSOLE_RE = re.compile(r"(?<![A-Za-z0-9_$.])console\s*\.\s*[A-Za-z]+\s*\(")

# Zeichen, nach denen ein '/' einen regulaeren Ausdruck beginnt, keine Division.
_REGEX_VORGAENGER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_SCHLUESSELWOERTER = ("return", "typeof", "case", "do", "else", "in", "of", "void")


def _leer(zeichen, von, bis):
    for j in range(von, bis):
        if zeichen[j] != "\n":
            zeichen[j] = " "


def _attribute_freigeben(text, von, bis, sicht):
    """Innerhalb eines maskierten Tag-Bereichs [von, bis) die Werte der
    Anzeige-Attribute wieder sichtbar machen."""
    stueck = text[von:bis]
    for t in _ATTR_RE.finditer(stueck):
        if t.group(1).lower() in ANZEIGE_ATTRIBUTE:
            wert_start = t.start(2) + 1
            wert_ende = t.end(2) - 1
            for j in range(von + wert_start, von + wert_ende):
                sicht[j] = True
    t = _ATTR_OFFEN_RE.search(stueck)
    if t and t.group(1).lower() in ANZEIGE_ATTRIBUTE:
        for j in range(von + t.start(3), von + t.end(3)):
            sicht[j] = True


def _html_stueck(text, von, bis, sicht):
    """HTML-Regel auf text[von:bis] anwenden: sicht[j] = False fuer Markup."""
    stueck = text[von:bis]
    bereiche = []
    for t in _TAG_RE.finditer(stueck):
        bereiche.append((t.start(), t.end()))
    t = _TAG_OFFEN_RE.search(stueck)
    if t and not any(a <= t.start() < b for a, b in bereiche):
        bereiche.append((t.start(), t.end()))
    t = _TAG_REST_RE.search(stueck)
    if t and not any(a <= t.start() < b for a, b in bereiche):
        # Rest eines Tags aus dem vorigen Stueck ('" class="x">Text') -
        # nur dann, wenn vor dem '>' ein Anfuehrungszeichen steht.
        rest = stueck[:t.end() - 1].rstrip()
        if rest.endswith(('"', "'")):
            bereiche.append((t.start(), t.end()))
    for a, b in bereiche:
        for j in range(von + a, von + b):
            sicht[j] = False
        _attribute_freigeben(text, von + a, von + b, sicht)


def _string_ende(text, i, quote):
    """Index des schliessenden quote ab i (i zeigt hinter das oeffnende),
    oder -1. Ein Zeilenumbruch beendet ein '..'/".."-Literal ohne Treffer."""
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i
        if c == "\n":
            return -1
        i += 1
    return -1


def _regex_moeglich(text, i):
    k = i - 1
    while k >= 0 and text[k] in " \t\n":
        k -= 1
    if k < 0:
        return True
    if text[k] in _REGEX_VORGAENGER:
        return True
    for wort in _REGEX_SCHLUESSELWOERTER:
        anfang = k - len(wort) + 1
        if anfang >= 0 and text[anfang:k + 1] == wort and (anfang == 0 or not (text[anfang - 1].isalnum() or text[anfang - 1] in "_$")):
            return True
    return False


def _regex_ende(text, i):
    """i zeigt auf das oeffnende '/'. Index des schliessenden '/' oder -1."""
    n = len(text)
    j = i + 1
    in_klasse = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return -1
        if in_klasse:
            if c == "]":
                in_klasse = False
        elif c == "[":
            in_klasse = True
        elif c == "/":
            return j
        j += 1
    return -1


def _js(text, von, bis, sicht, bis_klammer=False):
    """JS-Regel: alles unsichtbar ausser String-/Template-Inhalten. Mit
    bis_klammer endet der Lauf an der passenden '}' (Template-Ausdruck) und
    liefert deren Index."""
    i = von
    tiefe = 0
    while i < bis:
        c = text[i]
        if c in "'\"":
            ende = _string_ende(text, i + 1, c)
            if ende == -1 or ende >= bis:
                i += 1
                continue
            inhalt = text[i + 1:ende]
            if not (_CODE_TOKEN_RE.match(inhalt) or _PFAD_RE.match(inhalt)):
                for j in range(i + 1, ende):
                    sicht[j] = True
                _html_stueck(text, i + 1, ende, sicht)
            i = ende + 1
            continue
        if c == "c" and _CONSOLE_RE.match(text, i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_$.")):
            # console.log/warn/...(...) ist Log, kein Anzeigetext: der ganze
            # Aufruf bleibt unsichtbar (Strings darin werden uebersprungen).
            t = _CONSOLE_RE.match(text, i)
            i = _klammer_ende(text, t.end(), bis)
            continue
        if c == "`":
            i = _template(text, i + 1, bis, sicht)
            continue
        if c == "/" and _regex_moeglich(text, i):
            ende = _regex_ende(text, i)
            if ende != -1 and ende < bis:
                i = ende + 1
                continue
        if bis_klammer:
            if c == "{":
                tiefe += 1
            elif c == "}":
                if tiefe == 0:
                    return i
                tiefe -= 1
        i += 1
    return bis


def _klammer_ende(text, i, bis):
    """i zeigt hinter eine oeffnende '('. Index hinter der passenden ')',
    Strings und Template-Literale werden dabei uebersprungen."""
    tiefe = 0
    while i < bis:
        c = text[i]
        if c in "'\"":
            ende = _string_ende(text, i + 1, c)
            i = (ende + 1) if ende != -1 else i + 1
            continue
        if c == "`":
            leer = [False] * len(text)
            i = _template(text, i + 1, bis, leer)
            continue
        if c == "(":
            tiefe += 1
        elif c == ")":
            if tiefe == 0:
                return i + 1
            tiefe -= 1
        i += 1
    return bis


def _template(text, i, bis, sicht):
    """i zeigt hinter das oeffnende Backtick. Liefert den Index hinter dem
    schliessenden Backtick (oder bis)."""
    stueck_start = i
    while i < bis:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            for j in range(stueck_start, i):
                sicht[j] = True
            _html_stueck(text, stueck_start, i, sicht)
            return i + 1
        if c == "$" and i + 1 < bis and text[i + 1] == "{":
            for j in range(stueck_start, i):
                sicht[j] = True
            _html_stueck(text, stueck_start, i, sicht)
            ende = _js(text, i + 2, bis, sicht, bis_klammer=True)
            i = ende + 1
            stueck_start = i
            continue
        i += 1
    return bis


def _css(text, von, bis, sicht):
    i = von
    while i < bis:
        c = text[i]
        if c in "'\"":
            ende = _string_ende(text, i + 1, c)
            if ende != -1 and ende < bis:
                for j in range(i + 1, ende):
                    sicht[j] = True
                i = ende + 1
                continue
        i += 1


def _html(text, sicht):
    """Ganzer HTML-Text: Textknoten sichtbar, Tags nach HTML-Regel,
    <script>/<style>-Inhalte nach JS- bzw. CSS-Regel."""
    n = len(text)
    pos = 0
    while pos < n:
        t = _BLOCK_RE.search(text, pos)
        ende_text = t.start() if t else n
        for j in range(pos, ende_text):
            sicht[j] = True
        _html_stueck(text, pos, ende_text, sicht)
        if not t:
            break
        for j in range(t.start(), t.end()):
            sicht[j] = False
        name = t.group(1).lower()
        schluss = re.compile(r"</" + name + r"\s*>", re.IGNORECASE).search(text, t.end())
        inhalt_ende = schluss.start() if schluss else n
        if name == "script":
            _js(text, t.end(), inhalt_ende, sicht)
        else:
            _css(text, t.end(), inhalt_ende, sicht)
        if not schluss:
            break
        for j in range(schluss.start(), schluss.end()):
            sicht[j] = False
        pos = schluss.end()


def maske(text, art="html"):
    """text mit Leerzeichen an allen nicht sichtbaren Stellen (siehe
    Moduldoku). art: 'html' (Standard), 'js' oder 'css'."""
    sicht = [False] * len(text)
    if art == "js":
        _js(text, 0, len(text), sicht)
    elif art == "css":
        _css(text, 0, len(text), sicht)
    else:
        _html(text, sicht)
    zeichen = list(text)
    for j, ist_sichtbar in enumerate(sicht):
        if not ist_sichtbar and zeichen[j] != "\n":
            zeichen[j] = " "
    return "".join(zeichen)


def art_fuer_name(name):
    """Art aus dem Namen einer Modulkonstante: *JS* -> js, *CSS* -> css,
    sonst html. Ein Name ohne Hinweis (None) gilt als html."""
    if not name:
        return "html"
    if "JS" in name:
        return "js"
    if "CSS" in name:
        return "css"
    return "html"


def _selftest():
    faelle = [
        ("S1", "html", '<td class="spektrum-off">Spectrum</td>', ["Spectrum"], ["spektrum", "class", "td"]),
        ("S2", "html", '<button title="Kurztext speichern" id="saveBtn">Save</button>', ["Kurztext speichern", "Save"], ["saveBtn", "button"]),
        ("S3", "html", "<script>const skala = levelScale(rows); el('x').textContent = 'keine Aufnahmen';</script>", ["keine Aufnahmen"], ["skala", "levelScale", "textContent"]),
        ("S4", "js", "const t = `${n} Kanaele ${pluralWort(n, 'Kanal', 'Kanaele')}`;", ["Kanaele", "Kanal"], ["pluralWort", "const"]),
        ("S5", "js", "rows.push('<span class=\"x\" title=\"' + esc(t) + '\">' + name + '</span>');", [], ["span", "rows", "esc", "title", "class"]),
        ("S6", "css", ".spektrum-off { color:red; } .k::before { content:'Neu '; }", ["Neu"], ["spektrum", "color"]),
        ("S7", "js", "const re = /_(?:teil|part)(\\d+)'/; const s = 'Teil';", ["Teil"], ["teil|part"]),
        ("S8", "html", "<p>Zeile eins\nZeile zwei</p>", ["Zeile eins", "Zeile zwei"], []),
        ("S9", "js", "x = a / b; y = 'Sichtbar bleibt'; z = c / d;", ["Sichtbar bleibt"], ["a / b"]),
        ("S10", "html", '<input placeholder="Suche ..." value="laeuft">', ["Suche ..."], ["laeuft"]),
        ("S12", "js", "if (b.layer === 'zuweisung') x = 'Zuweisung'; fetchJSON('/api/befund?path=' + p);", ["Zuweisung"], ["zuweisung", "befund"]),
        ("S13", "js", "console.warn('[known_freq] Katalog nicht ladbar:', err); el.textContent = 'nicht geladen';", ["nicht geladen"], ["Katalog", "ladbar"]),
    ]
    alle_ok = True
    for kennung, art, text, sichtbar, unsichtbar in faelle:
        m = maske(text, art)
        ok = len(m) == len(text) and m.count("\n") == text.count("\n")
        ok = ok and all(s in m for s in sichtbar) and not any(u in m for u in unsichtbar)
        print("{0} {1}: {2!r} -> {3!r}".format(kennung, "ok" if ok else "FEHLGESCHLAGEN", text, " ".join(m.split())))
        alle_ok = alle_ok and ok
    ok = art_fuer_name("LABELS_JS") == "js" and art_fuer_name("COMMON_CSS") == "css" and art_fuer_name("HTML_PAGE") == "html" and art_fuer_name(None) == "html"
    print("S11 {0}: art_fuer_name".format("ok" if ok else "FEHLGESCHLAGEN"))
    alle_ok = alle_ok and ok
    print("Selbsttest OK" if alle_ok else "Selbsttest FEHLGESCHLAGEN")
    return 0 if alle_ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
