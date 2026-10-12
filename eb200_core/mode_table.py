#!/usr/bin/env python3
"""
eb200_core/mode_table.py

Laedt die FSK-Kandidatentabelle aus modes/fsk.json (Meilenstein 17, T-1702).
Reine Datenhaltung und Schemapruefung - kein Vergleich mit gemessenen
Kennwerten und keine Deutung. Der Vergleich (match_fsk) kommt aus
eb200_core/mode_match.py (T-1703) und loest dort auch die defaults auf;
dieses Modul liefert die Tabelle unveraendert, wie sie in der Datei steht -
sonst staenden zwei Wahrheiten ueber aufgeloeste Werte im Speicher.

Muster fuer Pfadaufloesung, "Laden wirft nie" und Selbsttestaufbau:
eb200_core/known_freq.py. numpy wird hier nicht gebraucht.

Schema modes/fsk.json
----------------------
Oberste Ebene: format (exakt FORMAT_TOKEN), table_version (positive ganze
Zahl), defaults (optionales Objekt mit Toleranzen), modes (nichtleere Liste).

defaults gilt fuer jede Zeile, die den Wert nicht selbst setzt - die
Aufloesung macht T-1703 beim Vergleich, nicht dieses Modul. near_factor
gehoert in die Daten, weil er zur Tabellenversion gehoert (Konfidenzstufe
"near" = innerhalb des Zweifachen der Toleranz), wird hier aber nur wie jeder
andere Default-Wert geprueft, nicht ausgewertet.

Jede Zeile in modes: id (ASCII-Slug, eindeutig, eingefroren sobald
ausgeliefert - Umbenennen verboten, nur stilllegen und neu anlegen) und label
(Anzeigetext) sind Pflicht. Mindestens eines von level_count, shift_hz, baud,
tone_spacing_hz, spacing_cv_max, keying muss gesetzt sein - eine Zeile ohne
Kriterium traefe auf jede Messung zu. shift_hz ist der GESAMTHUB (=2*dev_hz,
aeusserer Peakabstand), NICHT der Tonabstand bei mehr als zwei Pegeln - siehe
tools/eye_diagram.py tone_metrics()/freq_offset_and_deviation(). keying ist
sprachneutral ("keyed"/"continuous"); die Messung liefert heute
"getastet"/"dauersignal", die Abbildung macht T-1703. Unbekannte Felder auf
beiden Ebenen sind ein Fehler, damit Tippfehler auffallen statt still zu
wirken.
"""
from __future__ import annotations

import json
import os
import re
import sys

# Erlaubt auch den Direktaufruf "python eb200_core/mode_table.py": dort fehlt
# sonst das Repo-Root im Suchpfad fuer den paths-Import (T-2002, Weiche wie
# in eb200_core/path_sandbox.py).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core import paths

# Standard-Tabellenpfad: <PROGRAM_ROOT>/modes/fsk.json - mitgelieferte, nur
# gelesene Programmdaten, daher PROGRAM_ROOT und nicht DATA_ROOT (T-2002).
DEFAULT_TABLE_PATH = paths.program_path("modes", "fsk.json")

FORMAT_TOKEN = "eb200-modes-fsk-v1"

# Erlaubte Zeichen einer Mode-ID. Sprachneutral und dateinamen-/URL-tauglich,
# gleiche Absicht wie _ID_RE in known_freq.py, hier aber ausdruecklich nur
# Kleinbuchstaben - Grossbuchstaben oder Umlaute sind ein Fehler, kein Fallback.
_ID_RE = re.compile(r"^[a-z0-9_]+$")

_TOP_LEVEL_FIELDS = {"format", "table_version", "defaults", "modes"}
_DEFAULTS_FIELDS = {"shift_tol_hz", "baud_tol_rel", "spacing_cv_max", "near_factor"}
_ROW_FIELDS = {
    "id", "label", "level_count", "shift_hz", "shift_tol_hz", "baud",
    "baud_tol_rel", "tone_spacing_hz", "spacing_cv_max", "keying", "note",
}
# Mindestens eines davon muss eine Zeile tragen (siehe Moduldoku).
_CRITERIA_FIELDS = ("level_count", "shift_hz", "baud", "tone_spacing_hz",
                     "spacing_cv_max", "keying")
# Toleranzen und Frequenzen: alle positive Zahlen, auf Zeilen- UND Default-Ebene.
_POSITIVE_NUMBER_FIELDS = ("shift_hz", "shift_tol_hz", "baud", "baud_tol_rel",
                           "tone_spacing_hz", "spacing_cv_max", "near_factor")
_KEYING_VALUES = ("keyed", "continuous")


def _is_number(value) -> bool:
    """True fuer int/float, NICHT fuer bool (bool ist in Python eine
    int-Unterklasse - True/False waeren sonst stillschweigend 1/0)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _check_positive_numbers(obj: dict, fields, where: str, errors: list[str]) -> None:
    for f in fields:
        if f in obj:
            v = obj[f]
            if not _is_number(v) or v <= 0:
                errors.append(f"{where}: {f} muss eine positive Zahl sein (gefunden {v!r})")


def validate_fsk_table(obj) -> list[str]:
    """Prueft ein bereits per json.load() gelesenes Objekt gegen das Schema
    von modes/fsk.json. Leere Liste heisst gueltig. Reine Funktion, wirft nie -
    der Selbsttest prueft sie ohne Datei."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["oberste Ebene ist kein Objekt"]

    unknown_top = set(obj) - _TOP_LEVEL_FIELDS
    if unknown_top:
        errors.append(f"unbekannte Felder auf oberster Ebene: {sorted(unknown_top)}")

    if obj.get("format") != FORMAT_TOKEN:
        errors.append(f"format muss '{FORMAT_TOKEN}' sein (gefunden {obj.get('format')!r})")

    if not _is_positive_int(obj.get("table_version")):
        errors.append(f"table_version muss eine positive ganze Zahl sein "
                      f"(gefunden {obj.get('table_version')!r})")

    if "defaults" in obj:
        defaults = obj["defaults"]
        if not isinstance(defaults, dict):
            errors.append("defaults muss ein Objekt sein")
        else:
            unknown_def = set(defaults) - _DEFAULTS_FIELDS
            if unknown_def:
                errors.append(f"unbekannte Felder in defaults: {sorted(unknown_def)}")
            _check_positive_numbers(defaults, _DEFAULTS_FIELDS, "defaults", errors)

    modes = obj.get("modes")
    if not isinstance(modes, list) or not modes:
        errors.append("modes muss eine nichtleere Liste sein")
        return errors     # Zeilenpruefung ohne Liste ergibt keinen Sinn

    seen_ids: set[str] = set()
    for i, row in enumerate(modes):
        where = f"modes[{i}]"
        if not isinstance(row, dict):
            errors.append(f"{where}: ist kein Objekt")
            continue

        unknown_row = set(row) - _ROW_FIELDS
        if unknown_row:
            errors.append(f"{where}: unbekannte Felder {sorted(unknown_row)}")

        rid = row.get("id")
        if not isinstance(rid, str) or not rid or not _ID_RE.match(rid):
            errors.append(f"{where}: id ungueltig (erlaubt: a-z 0-9 _), gefunden {rid!r}")
            kennung = where
        else:
            kennung = rid
            if rid in seen_ids:
                errors.append(f"{kennung}: id doppelt vergeben")
            seen_ids.add(rid)

        label = row.get("label")
        if not isinstance(label, str) or not label:
            errors.append(f"{kennung}: label fehlt oder ist leer")

        if not any(f in row for f in _CRITERIA_FIELDS):
            errors.append(f"{kennung}: kein Vergleichskriterium gesetzt "
                          f"({', '.join(_CRITERIA_FIELDS)})")

        _check_positive_numbers(row, _POSITIVE_NUMBER_FIELDS, kennung, errors)

        if "level_count" in row and not (_is_positive_int(row["level_count"])
                                         and row["level_count"] > 1):
            errors.append(f"{kennung}: level_count muss eine ganze Zahl > 1 sein "
                          f"(gefunden {row['level_count']!r})")

        if "keying" in row and row["keying"] not in _KEYING_VALUES:
            errors.append(f"{kennung}: keying muss 'keyed' oder 'continuous' sein "
                          f"(gefunden {row['keying']!r}) - sprachneutrale Werte sind Pflicht")

    return errors


def load_fsk_table(path: str | None = None):
    """Laedt und prueft modes/fsk.json. Liefert (tabelle, fehler) und WIRFT
    NIE: eine fehlende, unlesbare oder schema-ungueltige Datei ist ein
    gueltiges Ergebnis (keine Kandidaten in der Anzeige), keine Stoerung der
    Messung. Bei Erfolg ist fehler == [] und tabelle das unveraenderte, per
    json.load() gelesene Objekt - keine Defaults hineinkopiert, keine Werte
    normiert (macht T-1703 beim Vergleich)."""
    pfad = path or DEFAULT_TABLE_PATH
    try:
        with open(pfad, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except OSError as exc:
        return None, [f"{pfad}: nicht lesbar ({exc})"]
    except ValueError as exc:
        return None, [f"{pfad}: kein gueltiges JSON ({exc})"]

    fehler = validate_fsk_table(obj)
    if fehler:
        return None, fehler
    return obj, []


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def _valid_obj() -> dict:
    """Minimales gueltiges Objekt als Ausgangspunkt fuer die Fehlerfaelle -
    jeder Test mutiert seine eigene frische Kopie."""
    return {
        "format": FORMAT_TOKEN,
        "table_version": 1,
        "defaults": {
            "shift_tol_hz": 15.0,
            "baud_tol_rel": 0.06,
            "spacing_cv_max": 0.05,
            "near_factor": 2.0,
        },
        "modes": [
            {"id": "a", "label": "A", "shift_hz": 170.0, "baud": 45.45},
            {"id": "b", "label": "B", "shift_hz": 425.0, "baud": 50.0},
        ],
    }


def _self_test_real() -> None:
    """Teil A: die ausgelieferte Tabelle modes/fsk.json."""
    tabelle, fehler = load_fsk_table()
    assert fehler == [], "Tabellenfehler:\n  " + "\n  ".join(fehler)
    assert tabelle is not None
    assert len(tabelle["modes"]) == 12, f"{len(tabelle['modes'])} Zeilen statt 12"

    ids = [m["id"] for m in tabelle["modes"]]
    assert len(ids) == len(set(ids)), "doppelte id in der ausgelieferten Tabelle"
    for mid in ids:
        assert _ID_RE.match(mid), f"kein ASCII-Slug: {mid!r}"

    for row in tabelle["modes"]:
        assert any(f in row for f in _CRITERIA_FIELDS), \
            f"{row['id']}: kein Vergleichskriterium"


def _self_test_fehlerfaelle() -> None:
    """Teil B: jeder Fehlerfall als eigene Fixture im Speicher, ohne Datei."""
    def check_fehler(obj: dict, erwartet_substr: str, bezeichnung: str) -> None:
        fehler = validate_fsk_table(obj)
        assert fehler, f"{bezeichnung}: keine Fehlermeldung, obwohl erwartet"
        assert any(erwartet_substr in e for e in fehler), \
            f"{bezeichnung}: keine Meldung enthaelt {erwartet_substr!r} ({fehler})"

    o = _valid_obj()
    o["format"] = "eb200-modes-fsk-v0"
    check_fehler(o, "format", "falsches format")

    o = _valid_obj()
    del o["table_version"]
    check_fehler(o, "table_version", "table_version fehlt")

    o = _valid_obj()
    o["modes"] = []
    check_fehler(o, "modes", "modes leer")

    o = _valid_obj()
    o["modes"][1]["id"] = "a"
    check_fehler(o, "doppelt", "doppelte id")

    o = _valid_obj()
    o["modes"][0]["id"] = "Ae"
    check_fehler(o, "id ungueltig", "id mit Grossbuchstaben")

    o = _valid_obj()
    o["modes"][0]["id"] = "aeä"      # Umlaut
    check_fehler(o, "id ungueltig", "id mit Umlaut")

    o = _valid_obj()
    o["modes"][0] = {"id": "x", "label": "X"}
    check_fehler(o, "kriterium", "Zeile ohne Kriterium")

    o = _valid_obj()
    o["modes"][0]["shift_hz"] = -170.0
    check_fehler(o, "shift_hz", "negative shift_hz")

    o = _valid_obj()
    o["modes"][0]["baud"] = 0
    check_fehler(o, "baud", "baud = 0")

    o = _valid_obj()
    o["modes"][0]["unsinn"] = 1
    check_fehler(o, "unbekannte Felder", "unbekanntes Feld in einer Zeile")
    # Muss die Zeile benennen, nicht die oberste Ebene.
    fehler = validate_fsk_table(o)
    assert not any("oberster Ebene" in e for e in fehler), fehler

    o = _valid_obj()
    o["unsinn"] = 1
    check_fehler(o, "unbekannte Felder auf oberster Ebene", "unbekanntes Feld oben")

    o = _valid_obj()
    o["modes"][0]["keying"] = "getastet"
    check_fehler(o, "keying", "keying mit deutschem Wert")


def _self_test_datei() -> None:
    """Teil C: kaputte bzw. fehlende Datei auf der Platte - load_fsk_table
    wirft nie."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        kaputt = os.path.join(tmp, "kaputt.json")
        with open(kaputt, "w", encoding="utf-8") as fh:
            fh.write("das ist kein JSON")
        tabelle, fehler = load_fsk_table(kaputt)
        assert tabelle is None and fehler, "kaputtes JSON nicht abgefangen"

        fehlend = os.path.join(tmp, "nicht_da.json")
        tabelle, fehler = load_fsk_table(fehlend)
        assert tabelle is None and fehler, "fehlende Datei nicht abgefangen"


def _self_test() -> None:
    _self_test_real()
    _self_test_fehlerfaelle()
    _self_test_datei()
    print("[mode_table] Selbsttest OK")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="FSK-Kandidatentabelle (modes/fsk.json)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument("--path", default=None, help="abweichender Tabellenpfad")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    tabelle, fehler = load_fsk_table(args.path)
    if fehler:
        print("FEHLER:")
        for f in fehler:
            print(f"  {f}")
        return 1
    print(f"format={tabelle['format']} table_version={tabelle['table_version']} "
          f"Zeilen={len(tabelle['modes'])}")
    for row in tabelle["modes"]:
        print(f"  {row['id']:<26} {row['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
