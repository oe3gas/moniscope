#!/usr/bin/env python3
"""
eb200_core/data_seed.py

Erststart einer leeren Datenwurzel (T-2004, Meilenstein 20).

Eine neue Datenwurzel (--data-dir auf einen leeren Ordner) hat noch kein
known_freq/. Ohne Vorlage starteten die Server mit leerem Katalog. Deshalb
kopiert seed_known_freq() beim ersten Start die mitgelieferten Katalogdateien
(*.json aus PROGRAM_ROOT/known_freq) nach DATA_ROOT/known_freq
(Entscheidungslog 2026-10-08).

Regeln:
  - Kopiert wird NUR, wenn DATA_ROOT/known_freq noch gar nicht existiert. Ein
    vorhandenes, auch leeres Verzeichnis bleibt unangetastet - der Betreiber
    kann Kataloge bewusst geloescht haben.
  - Zeigen Daten- und Programmwurzel auf dasselbe Verzeichnis (Start ohne
    --data-dir), passiert nichts.
  - Kopiert werden nur Dateien mit der Endung .json, keine .bak-Sicherungen
    und keine Unterverzeichnisse.
  - Die Kopie entsteht in einem Temp-Verzeichnis neben dem Ziel und wird erst
    am Ende umbenannt: ein abgebrochener Erststart hinterlaesst kein halbes
    known_freq/, der naechste Start versucht es erneut.
  - Fehlt die Quelle, entsteht ein leeres known_freq/ (die Pflegemaske :8010
    kann dann eigene Katalogdateien anlegen).

Aufrufer ist start_eb200.py, einmal vor dem Start der Server (so gibt es
keinen Wettlauf zwischen mehreren Serverprozessen).

Nur Python-Standardbibliothek.

Aufruf:
    python -m eb200_core.data_seed [--data-dir PFAD]   Erststart ausfuehren
    python -m eb200_core.data_seed --selftest
"""

from __future__ import annotations

import os
import shutil
import sys

# Erlaubt auch den Direktaufruf "python eb200_core/data_seed.py": dort fehlt
# sonst das Repo-Root im Suchpfad fuer den paths-Import (Weiche wie in
# eb200_core/path_sandbox.py).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core import paths

KATALOG_DIR = "known_freq"
_TMP_SUFFIX = ".erststart-tmp"


def _gleich(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def seed_known_freq(data_root: str = paths.DATA_ROOT,
                    program_root: str = paths.PROGRAM_ROOT) -> dict:
    """Legt DATA_ROOT/known_freq beim Erststart an und kopiert die
    mitgelieferten Kataloge hinein. Liefert {"angelegt": bool,
    "kopiert": [Dateinamen]}; "angelegt" ist False, wenn nichts zu tun war."""
    ziel = os.path.join(data_root, KATALOG_DIR)
    quelle = os.path.join(program_root, KATALOG_DIR)
    if os.path.exists(ziel) or _gleich(data_root, program_root):
        return {"angelegt": False, "kopiert": []}

    tmp = ziel + _TMP_SUFFIX
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)          # Rest eines abgebrochenen Erststarts
    os.makedirs(tmp)
    kopiert = []
    if os.path.isdir(quelle):
        for name in sorted(os.listdir(quelle)):
            src = os.path.join(quelle, name)
            if name.endswith(".json") and os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, name))
                kopiert.append(name)
    os.replace(tmp, ziel)
    return {"angelegt": True, "kopiert": kopiert}


# ---------------------------------------------------------------------------
# Selbsttest (python -m eb200_core.data_seed --selftest)
# ---------------------------------------------------------------------------

def _selftest() -> None:
    import tempfile

    def ok(bedingung: bool, text: str) -> None:
        if not bedingung:
            raise AssertionError(text)
        print(f"OK  {text}")

    with tempfile.TemporaryDirectory() as tmp:
        prog = os.path.join(tmp, "prog")
        kat = os.path.join(prog, KATALOG_DIR)
        os.makedirs(os.path.join(kat, "altbak"))
        for name, inhalt in (("a.json", "{}"), ("b.json", '{"x": 1}'),
                             ("a.json.bak", "alt"), ("notiz.txt", "x")):
            with open(os.path.join(kat, name), "w", encoding="utf-8") as fh:
                fh.write(inhalt)

        # T1: Erststart kopiert nur *.json
        d1 = os.path.join(tmp, "d1")
        os.mkdir(d1)
        r = seed_known_freq(d1, prog)
        ok(r == {"angelegt": True, "kopiert": ["a.json", "b.json"]}, "T1 Erststart kopiert a.json und b.json")
        ok(sorted(os.listdir(os.path.join(d1, KATALOG_DIR))) == ["a.json", "b.json"],
           "T1 keine .bak, keine Fremddatei, kein Unterverzeichnis")
        with open(os.path.join(d1, KATALOG_DIR, "b.json"), encoding="utf-8") as fh:
            ok(fh.read() == '{"x": 1}', "T1 Inhalt unveraendert")
        ok(not os.path.exists(os.path.join(d1, KATALOG_DIR + _TMP_SUFFIX)), "T1 kein Temp-Verzeichnis uebrig")

        # T2: zweiter Start tut nichts, auch wenn sich die Quelle aendert
        with open(os.path.join(kat, "c.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        r = seed_known_freq(d1, prog)
        ok(r == {"angelegt": False, "kopiert": []}, "T2 vorhandenes known_freq/ bleibt unangetastet")
        ok("c.json" not in os.listdir(os.path.join(d1, KATALOG_DIR)), "T2 keine Nachlieferung")

        # T3: vorhandenes, leeres Verzeichnis bleibt leer
        d3 = os.path.join(tmp, "d3")
        os.makedirs(os.path.join(d3, KATALOG_DIR))
        ok(seed_known_freq(d3, prog)["angelegt"] is False and
           os.listdir(os.path.join(d3, KATALOG_DIR)) == [], "T3 leeres known_freq/ bleibt leer")

        # T4: Daten- gleich Programmwurzel - nichts tun, nichts anlegen
        p4 = os.path.join(tmp, "p4")
        os.mkdir(p4)
        ok(seed_known_freq(p4, p4)["angelegt"] is False and os.listdir(p4) == [],
           "T4 gleiche Wurzel: nichts angelegt")

        # T5: fehlende Quelle - leeres known_freq/
        d5 = os.path.join(tmp, "d5")
        os.mkdir(d5)
        r = seed_known_freq(d5, os.path.join(tmp, "ohne"))
        ok(r == {"angelegt": True, "kopiert": []} and os.listdir(os.path.join(d5, KATALOG_DIR)) == [],
           "T5 ohne Quelle: leeres known_freq/")

        # T6: Rest eines abgebrochenen Erststarts wird ersetzt
        d6 = os.path.join(tmp, "d6")
        os.makedirs(os.path.join(d6, KATALOG_DIR + _TMP_SUFFIX))
        with open(os.path.join(d6, KATALOG_DIR + _TMP_SUFFIX, "halb.json"), "w", encoding="utf-8") as fh:
            fh.write("{")
        r = seed_known_freq(d6, prog)
        ok(r["angelegt"] and "halb.json" not in os.listdir(os.path.join(d6, KATALOG_DIR))
           and not os.path.exists(os.path.join(d6, KATALOG_DIR + _TMP_SUFFIX)),
           "T6 abgebrochener Erststart wird sauber wiederholt")

        # T7: Quelle unveraendert
        ok(sorted(os.listdir(kat)) == ["a.json", "a.json.bak", "altbak", "b.json", "c.json", "notiz.txt"],
           "T7 Quelle unveraendert")

    print("Selbsttest data_seed erfolgreich abgeschlossen.")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Erststart einer leeren Datenwurzel")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument("--data-dir", metavar="PFAD",
                    help="Datenwurzel (wertet eb200_core/paths.py beim Import aus)")
    args = ap.parse_args(argv)
    if args.selftest:
        _selftest()
        return 0
    r = seed_known_freq()
    print(f"Datenwurzel: {paths.DATA_ROOT}")
    print(f"angelegt: {r['angelegt']}, kopiert: {len(r['kopiert'])} Katalogdateien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
