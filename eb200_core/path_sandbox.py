#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/path_sandbox.py

Gemeinsame Pfad-Sandbox fuer alle Auswerte-Server (:8020/:8040/:8070), die
"path"-Query-Parameter aus dem LAN entgegennehmen (T-201, WS3 11.7 Punkt 7).

Sicherheitsziel: jeder path-Parameter darf NUR auf Dateien innerhalb des
Repo-Roots ODER eines in storage_roots.json konfigurierten Roots aufloesen,
und nur mit dem je Server erlaubten Suffix. Relative Pfade werden gegen das
Repo-Root aufgeloest; absolute Pfade sind zulaessig, wenn sie in einem der
Roots liegen. Fehler bleiben generische ValueError (der Handler uebersetzt
sie IMMER in eine 403-Antwort ohne Pfad-Echo - kein Oracle ueber Pfade
ausserhalb der Sandbox).

Vorher (bis T-201): jeder Server hatte eine eigene, wortgleiche Kopie von
resolve_safe_path()/to_relative(), geprueft nur gegen die eigene Wurzel
(SESSION_ROOT/SPECTRUM_ROOT/AUDIO_ROOT) - eine Aufnahme in einem externen
storage_roots-Eintrag (T-1205) war damit fuer die Viewer unerreichbar. Dieses
Modul verallgemeinert die Pruefung auf eine Liste von Roots (Repo-Root immer
zuerst) und liefert zusaetzlich ein PORTABLES Pfadformat (to_portable):
relativ mit "/" innerhalb des Repo-Roots, sonst absolut mit "/" - "../" ueber
das Repo-Root hinaus kommt dabei nie vor.

Nur Python-Standardbibliothek.
"""

from __future__ import annotations

import os
import sys

# Erlaubt sowohl "python -m eb200_core.path_sandbox" als auch den direkten
# Skriptaufruf "python eb200_core/path_sandbox.py --selftest" (Selbsttest-
# Konvention dieses Projekts, siehe eb200_core/mscan_job.py): beim
# Direktaufruf fehlt sonst das Repo-Root im Suchpfad fuer den job_store-Import.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.job_store import load_storage_roots


def storage_root_abs(repo_root: str, entry: str) -> str:
    """
    Absoluter Pfad eines storage_roots-Eintrags: absolute Eintraege bleiben
    (normalisiert) wie sie sind, relative werden gegen repo_root aufgeloest.
    Gleiche Semantik wie eb200_core.recording_index._resolve_storage_root
    (dort ohne normpath, aber jeder Aufrufer dort normalisiert das Ergebnis
    selbst weiter ueber realpath/abspath/normpath, bevor es verglichen wird -
    das zusaetzliche normpath hier aendert daher kein sichtbares Verhalten).
    """
    if os.path.isabs(entry):
        return os.path.normpath(entry)
    return os.path.normpath(os.path.join(repo_root, entry))


def sandbox_roots(repo_root: str, storage_roots_path: str | None = None) -> list:
    """
    Whitelist fuer resolve_within(): Repo-Root IMMER an Index 0, gefolgt von
    den storage_roots.json-Eintraegen (storage_root_abs + realpath), je
    Server-Request neu gelesen (Datei ist winzig; Aenderungen an
    storage_roots.json wirken damit ohne Server-Neustart). Duplikate
    (z. B. ein Eintrag, der auf den Repo-Root selbst zeigt) werden ueber
    normcase erkannt und nur einmal aufgenommen; die Reihenfolge der
    uebrigen Eintraege bleibt stabil (wie in storage_roots.json).
    """
    repo_root_real = os.path.realpath(repo_root)
    roots = [repo_root_real]
    seen = {os.path.normcase(repo_root_real)}

    path = storage_roots_path or os.path.join(repo_root, "storage_roots.json")
    for entry in load_storage_roots(path):
        abs_entry = os.path.realpath(storage_root_abs(repo_root, entry))
        key = os.path.normcase(abs_entry)
        if key in seen:
            continue
        seen.add(key)
        roots.append(abs_entry)
    return roots


def resolve_within(user_path: str, roots: list, *, suffixes: tuple = (),
                    reject_suffixes: tuple = ()) -> str:
    """
    Loest user_path zu einem absoluten Pfad auf und stellt sicher, dass
    dieser in EINEM der roots liegt (Verallgemeinerung der bisherigen
    resolve_safe_path()-Klone der drei Auswerte-Server). Ein leerer Pfad ist
    ungueltig; ein relativer Pfad wird gegen roots[0] (immer der Repo-Root,
    siehe sandbox_roots) aufgeloest, ein absoluter Pfad direkt uebernommen -
    er muss dann in einem der roots liegen, sonst schlaegt die Pruefung
    unten fehl.

    Der Root-Vergleich laeuft zuerst (nicht die Endungspruefung): ein Pfad
    ausserhalb jeder Sandbox soll IMMER denselben generischen Fehler
    liefern, unabhaengig von seiner Endung - sonst liesse sich aus der Art
    des Fehlers (Endung ok, aber Sandbox-Fehler vs. umgekehrt) erschliessen,
    ob eine Datei mit dieser Endung ausserhalb der Sandbox existiert (Oracle
    ueber Dateien, die der Aufrufer gar nicht sehen darf). reject_suffixes
    (z. B. WAL-/SHM-Beidateien) wird vor suffixes geprueft, weil eine
    Beidatei nie als eigenstaendiger path-Parameter gemeint sein kann, auch
    wenn ihr Name zufaellig auf ein erlaubtes suffix passen wuerde.

    Wirft ValueError bei leerem Pfad, ausserhalb aller Roots, verbotener
    Endung (reject_suffixes) oder fehlender Endung (suffixes, falls
    angegeben). Gibt den aufgeloesten (realpath) Pfad zurueck.
    """
    if not user_path:
        raise ValueError("path is missing")

    candidate = user_path if os.path.isabs(user_path) else os.path.join(roots[0], user_path)
    resolved = os.path.realpath(candidate)

    # Windows-Dateisystem ist standardmaessig case-insensitiv - fuer den
    # Sandbox-Vergleich daher beide Seiten kleinschreiben, sonst koennte ein
    # Pfad mit abweichender Gross-/Kleinschreibung faelschlich durchrutschen.
    cmp_resolved = resolved.lower() if os.name == "nt" else resolved
    inside = False
    for root in roots:
        cmp_root = root.lower() if os.name == "nt" else root
        try:
            # commonpath wirft selbst ValueError, wenn resolved auf einem
            # anderen Laufwerk liegt als root - das ist ebenfalls kein
            # Treffer fuer DIESEN Root, kein Absturz.
            if os.path.commonpath([cmp_root, cmp_resolved]) == cmp_root:
                inside = True
                break
        except ValueError:
            continue
    if not inside:
        raise ValueError("path outside the sandbox")

    if reject_suffixes and resolved.endswith(tuple(reject_suffixes)):
        raise ValueError("file extension not allowed")
    if suffixes and not resolved.endswith(tuple(suffixes)):
        raise ValueError("file extension not allowed")

    return resolved


def to_portable(path: str, repo_root: str) -> str:
    """
    Portables Pfadformat fuer Antworten/Speicherschluessel (LabelStore &co.):
    liegt path innerhalb von repo_root, ein repo_root-relativer Pfad mit "/"
    (wie bisher to_relative()); sonst der ABSOLUTE Pfad mit "/" - fuer Roots
    auf einem anderen Laufwerk oder ausserhalb des Repos ist ein "../"-Pfad
    weder aussagekraeftig noch (bei anderem Laufwerk unter Windows)
    ueberhaupt bildbar (os.path.relpath wirft dort ValueError). "../" ueber
    das Repo-Root hinaus kommt in einem von to_portable gelieferten Pfad
    NIE vor. Ersetzt die drei to_relative() der Auswerte-Server und
    eb200_core.recording_index._to_relative().
    """
    root_real = os.path.realpath(repo_root)
    cmp_root = root_real.lower() if os.name == "nt" else root_real
    cmp_path = path.lower() if os.name == "nt" else path
    try:
        inside = os.path.commonpath([cmp_root, cmp_path]) == cmp_root
    except ValueError:
        inside = False
    if inside:
        rel = os.path.relpath(path, root_real)
        return rel.replace(os.sep, "/")
    return path.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Selbsttest - "python eb200_core/path_sandbox.py --selftest" ohne Hardware
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as extern:
        repo_real = os.path.realpath(repo)
        extern_real = os.path.realpath(extern)

        os.makedirs(os.path.join(repo_real, "sessions"), exist_ok=True)
        os.makedirs(extern_real, exist_ok=True)
        with open(os.path.join(repo_real, "sessions", "a.db"), "w", encoding="utf-8") as fh:
            fh.write("x")
        extern_file = os.path.join(extern_real, "b.db")
        with open(extern_file, "w", encoding="utf-8") as fh:
            fh.write("x")

        with open(os.path.join(repo_real, "storage_roots.json"), "w", encoding="utf-8") as fh:
            json.dump([extern_real], fh)

        roots = sandbox_roots(repo_real)

        # -- T1: relativer Pfad im Repo, absoluter Pfad im externen Root ----
        r1 = resolve_within("sessions/a.db", roots)
        assert r1 == os.path.realpath(os.path.join(repo_real, "sessions", "a.db")), r1
        r2 = resolve_within(extern_file, roots)
        assert r2 == os.path.realpath(extern_file), r2
        print("T1 ok")

        # -- T2: "../" aus dem Repo heraus in ein Nicht-Root -----------------
        try:
            resolve_within("../ausserhalb.db", roots)
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet (ausserhalb aller Roots)")
        print("T2 ok")

        # -- T3: Gross-/Kleinschreibung des Roots (nur nt) -------------------
        if os.name == "nt":
            varied = os.path.join(repo_real.upper(), "sessions", "a.db")
            r3 = resolve_within(varied, roots)
            assert r3 == r1, r3
            print("T3 ok")
        else:
            print("T3 uebersprungen (kein nt)")

        # -- T4: reject_suffixes/suffixes ------------------------------------
        try:
            resolve_within("sessions/a.db-wal", roots, reject_suffixes=("-wal", "-shm"))
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet (reject_suffixes)")
        try:
            resolve_within("sessions/a.db", roots, suffixes=(".wav",))
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet (suffixes, falsche Endung)")
        print("T4 ok")

        # -- T5: leerer Pfad --------------------------------------------------
        try:
            resolve_within("", roots)
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet (leerer Pfad)")
        print("T5 ok")

        # -- T6: to_portable ---------------------------------------------------
        rel = to_portable(r1, repo_real)
        assert rel == "sessions/a.db", rel
        abs_out = to_portable(r2, repo_real)
        assert abs_out == r2.replace(os.sep, "/"), (abs_out, r2)
        print("T6 ok")

        # -- T7: sandbox_roots - Index 0, externer Root, Duplikat ------------
        assert roots[0] == repo_real, roots
        assert extern_real in roots, roots
        with open(os.path.join(repo_real, "storage_roots.json"), "w", encoding="utf-8") as fh:
            json.dump([extern_real, repo_real], fh)   # Repo-Root nochmal als Eintrag
        roots_dup = sandbox_roots(repo_real)
        assert roots_dup.count(repo_real) == 1, roots_dup
        assert len(roots_dup) == 2, roots_dup
        print("T7 ok")

        # -- T8: anderes Laufwerk (nur nt, nur wenn eines existiert) ---------
        other_drive = None
        if os.name == "nt":
            for letter in "DEFGH":
                probe = f"{letter}:/"
                if os.path.exists(probe) and os.path.realpath(probe).lower() != os.path.realpath("C:/").lower():
                    other_drive = probe
                    break
        if other_drive:
            try:
                resolve_within(other_drive + "irgendwas.db", roots)
            except ValueError:
                pass
            else:
                raise AssertionError("ValueError erwartet (anderes Laufwerk, kein Root)")
            print("T8 ok")
        else:
            print("T8 uebersprungen (kein zweites Laufwerk gefunden)")

    print("Selbsttest OK")


def main() -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(description="Gemeinsame Pfad-Sandbox der Auswerte-Server")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
