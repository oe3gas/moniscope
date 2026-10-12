#!/usr/bin/env python3
"""
tools/known_freq_wien_merge.py

Loest die Wiener UKW-Doppelungen zwischen known_freq/ORS_UKW_AT.json und
known_freq/UKW_BC.json bzw. known_freq/ukw_wien.json auf, ohne eine einzige
Kanal-id zu verlieren.

Ausgangslage: ORS_UKW_AT.json (volle ORS-Senderliste, IDs ors-ukw-*) und die
beiden alten Handlisten UKW_BC.json/ukw_wien.json (IDs bc-wien-*, fm-wien-*,
bc_*, Radio_NOE) fuehren 14 Wiener Sender doppelt - einmal unter der neuen
ORS-id, einmal unter der alten. Die ORS-ids sind neu und werden nirgends
referenziert; die alten ids koennen dagegen bereits in Scan-Sets (jobs/mscan/
*.json) oder Aufzeichnungen (records/*/job.json) stehen - beide Faelle kommen
im Katalog tatsaechlich vor (siehe --apply-Bericht, Abschnitt Grep-Treffer).
Deshalb erbt der ORS-Eintrag die alte id (Umbenennung), nicht umgekehrt: die
alte id bleibt fuer jeden Verweis gueltig, der ORS-Eintrag traegt danach
zusaetzlich site/bundesland/programm/pol/erp_w aus der ORS-Liste. Der alte
Eintrag wird anschliessend geloescht (Kanal existiert nur noch einmal).

Zusaetzlich (ausserhalb der Zuordnungstabelle): CZ_RadioZurnal steht in
UKW_BC.json, gehoert dort inhaltlich aber nicht hin (tschechischer Sender in
einer Wiener Senderliste) - wandert unveraendert nach ukw_cz.json.

Sonderfall bc_FM4: der Alt-Eintrag traegt einen Tippfehler (98,9 MHz statt
98,8 MHz - siehe ORS_FMHZ_OVERRIDE unten). Die uebliche Vorpruefung
"freq_hz Alt == freq_hz ORS" wird fuer dieses eine Paar durch zwei getrennte
Prüfungen ersetzt (Alt == 98,9 MHz, ORS == 98,8 MHz) statt einer Gleichheit.

CLI:
  python tools/known_freq_wien_merge.py            Trockenlauf, Bericht nach
                                                      stdout, schreibt nichts
  python tools/known_freq_wien_merge.py --apply     fuehrt die Uebernahme aus
  python tools/known_freq_wien_merge.py --selftest  Selbsttest, tempfaehig
  python tools/known_freq_wien_merge.py --dir PFAD  abweichendes Katalog-
                                                      verzeichnis (Muster
                                                      known_freq.py --dir)

Vorpruefung (Trockenlauf UND vor --apply) - bei jedem Verstoss Exit 2, nichts
wird geschrieben:
  - jede Alt-id existiert in ihrer Alt-Datei, jede ORS-id existiert in
    ORS_UKW_AT.json
  - freq_hz Alt == freq_hz ORS (Sonderfall bc_FM4 siehe oben)
  - keine Alt-id existiert bereits in ORS_UKW_AT.json
  - CZ_RadioZurnal existiert in UKW_BC.json und noch nicht in ukw_cz.json
Der Repo-weite Grep nach jeder Alt-id ausserhalb known_freq/ ist KEINE
Abbruchbedingung - Treffer werden nur berichtet (sie sind der Grund fuer die
id-Uebernahme).

Schreibdisziplin wie tools/known_freq_abgleich.py: .bak der Vorversion,
atomar (.tmp + os.replace), UTF-8, ensure_ascii=False, indent=2,
Schluesselreihenfolge der bestehenden Dicts bleibt erhalten (json.load haelt
seit Python 3.7 die Reihenfolge der Quelldatei; neue Schluessel landen ans
Ende, ein bestehender Schluessel wird beim Ueberschreiben nicht verschoben).

Abweichung vom Auftrag: der Auftrag verlangt den Grep "ueber alle Textdateien,
.git ausgenommen". known_freq/ selbst ist ausgenommen (dort stehen die ids
selbst - kein Fund dort waere ueberraschend). Zusaetzlich werden .venv/ und
__pycache__/ uebersprungen (Drittanbieter-/Build-Artefakte, keine Projekttexte)
sowie EB200_sources.txt (CLAUDE.md: nie lesen). Das Ergebnis fuer die 14
Alt-ids aendert sich dadurch nicht - EB200_sources.txt ist ein reiner Export
und haette ohnehin nur denselben Kommentar/Selftest-Text aus
eb200_core/known_freq.py gespiegelt, den der Grep dort ebenfalls findet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_DIR = os.path.join(ROOT, "known_freq")

ORS_FILENAME = "ORS_UKW_AT.json"
BC_FILENAME = "UKW_BC.json"
WIEN_FILENAME = "ukw_wien.json"
CZ_FILENAME = "ukw_cz.json"

CZ_MOVE_ID = "CZ_RadioZurnal"

# Verzeichnisse, die der Repo-weite Grep uebergeht - siehe Abweichung im
# Moduldocstring. known_freq/ ist der Katalog selbst (dort stehen die ids
# per Definition), .git/.venv/__pycache__ sind kein Projekttext.
_GREP_SKIP_DIRS = {".git", ".venv", "__pycache__", "known_freq"}
# CLAUDE.md: EB200_sources.txt ist ein generierter Export - nie lesen.
_GREP_SKIP_FILES = {"EB200_sources.txt"}

# ---------------------------------------------------------------------------
# Zuordnungstabelle Alt-id -> ORS-id (siehe Moduldocstring)
# ---------------------------------------------------------------------------

PAIRS = [
    {"old_id": "bc-wien-radiowien", "old_file": BC_FILENAME, "old_mhz": 89.9,
     "ors_id": "ors-ukw-89900-wien-1-kahlenberg"},
    {"old_id": "bc-wien-oe1", "old_file": BC_FILENAME, "old_mhz": 92.0,
     "ors_id": "ors-ukw-92000-wien-1-kahlenberg"},
    {"old_id": "fm-wien-burgenland", "old_file": WIEN_FILENAME, "old_mhz": 94.7,
     "ors_id": "ors-ukw-94700-wien-1-kahlenberg"},
    {"old_id": "bc-wien-noe", "old_file": BC_FILENAME, "old_mhz": 97.9,
     "ors_id": "ors-ukw-97900-wien-1-kahlenberg"},
    {"old_id": "bc-wien-oe3", "old_file": BC_FILENAME, "old_mhz": 99.9,
     "ors_id": "ors-ukw-99900-wien-1-kahlenberg"},
    {"old_id": "fm-wien-oe24", "old_file": WIEN_FILENAME, "old_mhz": 102.5,
     "ors_id": "ors-ukw-102500-wien-1-kahlenberg"},
    {"old_id": "bc-wien-fm4", "old_file": BC_FILENAME, "old_mhz": 103.8,
     "ors_id": "ors-ukw-103800-wien-1-kahlenberg"},
    {"old_id": "bc-wien-kronehit", "old_file": BC_FILENAME, "old_mhz": 105.8,
     "ors_id": "ors-ukw-105800-wien-1-kahlenberg"},
    {"old_id": "fm-wien-maria", "old_file": WIEN_FILENAME, "old_mhz": 99.5,
     "ors_id": "ors-ukw-99500-wien-innere-stadt"},
    {"old_id": "fm-wien-welle1", "old_file": WIEN_FILENAME, "old_mhz": 102.1,
     "ors_id": "ors-ukw-102100-wien-innere-stadt"},
    {"old_id": "bc-wien-oe3-1", "old_file": BC_FILENAME, "old_mhz": 89.4,
     "ors_id": "ors-ukw-89400-st-poelten-jauerling"},
    {"old_id": "bc_OE1", "old_file": BC_FILENAME, "old_mhz": 97.0,
     "ors_id": "ors-ukw-97000-st-poelten-jauerling"},
    {"old_id": "Radio_NOE", "old_file": BC_FILENAME, "old_mhz": 91.5,
     "ors_id": "ors-ukw-91500-st-poelten-jauerling"},
    # Sonderfall: Alt-Eintrag hat den Tippfehler 98,9 MHz, die ORS-Liste (und
    # der tatsaechliche Sender) 98,8 MHz - siehe Moduldocstring. ors_mhz
    # ueberschreibt den sonst aus old_mhz abgeleiteten Erwartungswert fuer
    # die ORS-Seite der Pruefung.
    {"old_id": "bc_FM4", "old_file": BC_FILENAME, "old_mhz": 98.9,
     "ors_id": "ors-ukw-98800-st-poelten-jauerling", "ors_mhz": 98.8},
]


def _hz(mhz: float) -> int:
    return round(mhz * 1_000_000)


def _ors_mhz(pair: dict) -> float:
    return pair.get("ors_mhz", pair["old_mhz"])


# ---------------------------------------------------------------------------
# Laden/Schreiben - Rohformat (wie known_freq_abgleich.py: unbekannte
# Schluessel bleiben erhalten, load_catalog() waere hier ungeeignet, da es
# normalisiert/verwirft)
# ---------------------------------------------------------------------------

def _load_required_files(catalog_dir: str) -> tuple[dict, list[str]]:
    """Laedt genau die vier fuer dieses Werkzeug relevanten Dateien. Liefert
    (name -> dict, Fehlertexte). Eine fehlende/kaputte Datei fehlt im ersten
    Rueckgabewert und erzeugt einen Fehlertext - die Vorpruefung meldet das
    dann pro betroffenem Paar."""
    files: dict = {}
    errors: list = []
    for name in (ORS_FILENAME, BC_FILENAME, WIEN_FILENAME, CZ_FILENAME):
        path = os.path.join(catalog_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict):
                raise ValueError("oberste Ebene ist kein Objekt")
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: nicht lesbar - {exc}")
            continue
        files[name] = doc
    return files, errors


def _eol_of(data: bytes) -> bytes:
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf * 2 > data.count(b"\n") else b"\n"


def _write_known_json(path: str, doc: dict) -> None:
    """Schreibt eine Katalogdatei zurueck: .bak der Vorversion, atomar via
    .tmp + os.replace, Zeilenende der Zieldatei beibehalten (Neuanlage:
    CRLF) - identisches Muster zu tools/known_freq_abgleich.py."""
    eol = b"\r\n"
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            old = fh.read()
        with open(path + ".bak", "wb") as fh:
            fh.write(old)
        eol = _eol_of(old)
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    data = text.encode("utf-8").replace(b"\n", eol)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Repo-weiter Grep nach den Alt-ids (nur Bericht, siehe Moduldocstring)
# ---------------------------------------------------------------------------

def repo_grep(alt_ids: list[str]) -> dict[str, list[str]]:
    """Durchsucht das gesamte Repo byteweise nach jeder Alt-id (ausser den in
    _GREP_SKIP_DIRS/_GREP_SKIP_FILES genannten Ausnahmen). Liefert
    id -> sortierte Liste repo-relativer Fundstellen (mit '/' als Trenner,
    plattformunabhaengig)."""
    patterns = {aid: aid.encode("utf-8") for aid in alt_ids}
    hits: dict = {aid: [] for aid in alt_ids}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _GREP_SKIP_DIRS]
        for fname in filenames:
            if fname in _GREP_SKIP_FILES:
                continue
            path = os.path.join(dirpath, fname)
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for aid, pat in patterns.items():
                if pat in data:
                    hits[aid].append(rel)
    for aid in hits:
        hits[aid].sort()
    return hits


# ---------------------------------------------------------------------------
# Vorpruefung
# ---------------------------------------------------------------------------

def precheck(files: dict) -> tuple[list[str], dict]:
    """Prueft alle Vorbedingungen aus dem Moduldocstring. Liefert
    (Problemtexte, Kontext). Kontext enthaelt je Paar die bereits gefundenen
    Eintraege (alte Referenzen auf dieselben dict-Objekte wie in files!), damit
    do_apply() sie nicht erneut suchen muss - leer/unvollstaendig bei
    Problemen mit genau diesem Paar."""
    problems: list = []
    ctx: dict = {"pairs": [], "cz": {}}

    ors_doc = files.get(ORS_FILENAME)
    if ors_doc is None:
        problems.append(f"{ORS_FILENAME} fehlt oder ist nicht lesbar")
        ors_by_id = {}
    else:
        ors_entries = ors_doc.get("entries") or []
        ors_by_id = {e.get("id"): e for e in ors_entries if isinstance(e, dict)}

    for pair in PAIRS:
        old_id = pair["old_id"]
        old_file = pair["old_file"]
        ors_id = pair["ors_id"]
        info: dict = {"pair": pair}

        old_doc = files.get(old_file)
        old_entry = None
        old_index = None
        if old_doc is None:
            problems.append(f"{old_id}: Alt-Datei {old_file} fehlt oder ist nicht lesbar")
        else:
            old_entries = old_doc.get("entries") or []
            for i, e in enumerate(old_entries):
                if isinstance(e, dict) and e.get("id") == old_id:
                    old_entry, old_index = e, i
                    break
            if old_entry is None:
                problems.append(f"{old_id}: nicht in {old_file} gefunden")

        ors_entry = ors_by_id.get(ors_id)
        if ors_entry is None and ors_doc is not None:
            problems.append(f"{ors_id}: nicht in {ORS_FILENAME} gefunden")

        if old_id in ors_by_id:
            problems.append(f"{old_id}: existiert bereits in {ORS_FILENAME} - "
                            f"Uebernahme wuerde eine bestehende id ueberschreiben")

        if old_entry is not None and ors_entry is not None:
            old_freq = old_entry.get("freq_hz")
            ors_freq = ors_entry.get("freq_hz")
            exp_old = _hz(pair["old_mhz"])
            exp_ors = _hz(_ors_mhz(pair))
            if old_freq != exp_old:
                problems.append(f"{old_id}: freq_hz {old_freq!r} != erwartet "
                                f"{exp_old} ({pair['old_mhz']} MHz)")
            if ors_freq != exp_ors:
                problems.append(f"{ors_id}: freq_hz {ors_freq!r} != erwartet "
                                f"{exp_ors} ({_ors_mhz(pair)} MHz)")

        info["old_entry"] = old_entry
        info["old_index"] = old_index
        info["old_doc"] = old_doc
        info["ors_entry"] = ors_entry
        ctx["pairs"].append(info)

    bc_doc = files.get(BC_FILENAME)
    cz_doc = files.get(CZ_FILENAME)
    cz_old_entry = None
    cz_old_index = None
    if bc_doc is None:
        problems.append(f"{CZ_MOVE_ID}: Alt-Datei {BC_FILENAME} fehlt oder ist nicht lesbar")
    else:
        bc_entries = bc_doc.get("entries") or []
        for i, e in enumerate(bc_entries):
            if isinstance(e, dict) and e.get("id") == CZ_MOVE_ID:
                cz_old_entry, cz_old_index = e, i
                break
        if cz_old_entry is None:
            problems.append(f"{CZ_MOVE_ID}: nicht in {BC_FILENAME} gefunden")
    if cz_doc is None:
        problems.append(f"{CZ_MOVE_ID}: Zieldatei {CZ_FILENAME} fehlt oder ist nicht lesbar")
    elif any(isinstance(e, dict) and e.get("id") == CZ_MOVE_ID for e in cz_doc.get("entries") or []):
        problems.append(f"{CZ_MOVE_ID}: existiert bereits in {CZ_FILENAME}")

    ctx["cz"] = {"old_entry": cz_old_entry, "old_index": cz_old_index,
                "bc_doc": bc_doc, "cz_doc": cz_doc}

    return problems, ctx


# ---------------------------------------------------------------------------
# --apply
# ---------------------------------------------------------------------------

def do_apply(files: dict, ctx: dict) -> list[str]:
    """Fuehrt die Uebernahme + die CZ_RadioZurnal-Verschiebung aus (Vorpruefung
    muss vorher erfolgreich gewesen sein - siehe main()). Mutiert files
    in-place, schreibt aber noch NICHTS auf Platte (das macht main()
    hinterher fuer jede tatsaechlich beruehrte Datei genau einmal). Liefert
    eine Liste von Protokollzeilen fuer den Bericht."""
    log: list = []

    for info in ctx["pairs"]:
        pair = info["pair"]
        old_id = pair["old_id"]
        old_doc = info["old_doc"]
        old_index = info["old_index"]
        old_entry = info["old_entry"]
        ors_entry = info["ors_entry"]

        old_label = old_entry.get("label")
        old_notes = old_entry.get("notes")
        old_tags = old_entry.get("tags")

        # Per Objekt-Identitaet entfernen, nicht per Index: mehrere Paare
        # koennen dieselbe Alt-Datei treffen, ein index-basiertes Loeschen
        # waere nach der ersten Entfernung fuer die naechsten Paare falsch.
        old_doc["entries"] = [e for e in old_doc["entries"] if e is not old_entry]

        ors_entry["id"] = old_id
        ors_entry["legacy_id_from"] = pair["old_file"]
        if old_notes:
            bisherige = ors_entry.get("notes")
            ors_entry["notes"] = f"{bisherige} | {old_notes}" if bisherige else old_notes
        if old_tags:
            vereinigt = list(ors_entry.get("tags") or [])
            for t in old_tags:
                if t not in vereinigt:
                    vereinigt.append(t)
            ors_entry["tags"] = vereinigt
        ors_entry["legacy_label"] = old_label

        log.append(f"{pair['ors_id']} -> id={old_id} "
                   f"(legacy_id_from={pair['old_file']}, legacy_label={old_label!r})")

    cz = ctx["cz"]
    cz["bc_doc"]["entries"] = [e for e in cz["bc_doc"]["entries"] if e is not cz["old_entry"]]
    cz["cz_doc"].setdefault("entries", []).append(cz["old_entry"])
    log.append(f"{CZ_MOVE_ID}: {BC_FILENAME} -> {CZ_FILENAME} (unveraendert)")

    return log


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------

def build_report(files: dict, ctx: dict, grep_hits: dict, applied_log: list | None) -> str:
    lines = []
    lines.append("Wiener UKW-Doppelungen ORS_UKW_AT.json <-> UKW_BC.json/ukw_wien.json")
    lines.append("")
    lines.append(f"{'Alt-id':<20} {'Alt-Datei':<14} {'Alt-MHz':>8} {'ORS-MHz':>8}  "
                 f"{'ORS-Label':<32} Grep-Treffer (ausserhalb known_freq/)")
    for info in ctx["pairs"]:
        pair = info["pair"]
        ors_entry = info["ors_entry"]
        ors_label = ors_entry.get("label", "") if ors_entry else "(nicht gefunden)"
        treffer = grep_hits.get(pair["old_id"], [])
        treffer_txt = ", ".join(treffer) if treffer else "-"
        lines.append(f"{pair['old_id']:<20} {pair['old_file']:<14} "
                     f"{pair['old_mhz']:>8.1f} {_ors_mhz(pair):>8.1f}  "
                     f"{ors_label:<32} {treffer_txt}")
    lines.append("")
    cz_old = ctx["cz"]["old_entry"]
    lines.append(f"Zusatz: {CZ_MOVE_ID} ({cz_old.get('label') if cz_old else '?'}) "
                f"{BC_FILENAME} -> {CZ_FILENAME}")
    lines.append("")

    if applied_log is not None:
        lines.append("Ausgefuehrt:")
        for line in applied_log:
            lines.append(f"  - {line}")
    else:
        lines.append("Trockenlauf - nichts geschrieben. --apply fuehrt aus.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def _write_json(path: str, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)


def _self_test() -> bool:
    import tempfile

    ok_all = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok_all
        if cond:
            print(f"{name} OK")
        else:
            ok_all = False
            print(f"{name} FAIL {detail}")

    # -- T1: eine normale Paarung + der Sonderfall + CZ-Verschiebung, alles
    # in einem synthetischen Katalogordner mit genau den vier benoetigten
    # Dateien. --------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        ors_doc = {
            "version": 1, "meta": {"service": "ORS"}, "defaults": {},
            "bands": [],
            "entries": [
                {"kind": "channel", "id": "ors-a", "label": "A ORS", "freq_hz": 90_000_000,
                 "tags": ["ukw", "broadcast"], "notes": "ORS-Notiz", "site": "X"},
                {"kind": "channel", "id": "ors-fm4", "label": "FM4 ORS", "freq_hz": 98_800_000,
                 "tags": ["ukw"], "notes": "ORS-FM4-Notiz"},
                {"kind": "channel", "id": "unrelated", "label": "U", "freq_hz": 200_000_000},
            ],
        }
        _write_json(os.path.join(tmp, ORS_FILENAME), ors_doc)

        bc_doc = {
            "version": 1, "meta": {"service": "BC"}, "defaults": {},
            "bands": [],
            "entries": [
                {"kind": "channel", "id": "old-a", "label": "A Alt", "freq_hz": 90_000_000,
                 "tags": ["alt"], "notes": "Alt-Notiz"},
                {"kind": "channel", "id": "old-fm4", "label": "FM4 Alt", "freq_hz": 98_900_000},
                {"kind": "channel", "id": CZ_MOVE_ID, "label": "CZ", "freq_hz": 90_700_000},
                {"kind": "channel", "id": "bleibt", "label": "Bleibt", "freq_hz": 88_000_000},
            ],
        }
        _write_json(os.path.join(tmp, BC_FILENAME), bc_doc)

        _write_json(os.path.join(tmp, WIEN_FILENAME), {
            "version": 1, "meta": {"service": "Wien"}, "defaults": {},
            "bands": [], "entries": [],
        })
        _write_json(os.path.join(tmp, CZ_FILENAME), {
            "version": 1, "meta": {"service": "CZ"}, "defaults": {},
            "bands": [], "entries": [],
        })

        test_pairs = [
            {"old_id": "old-a", "old_file": BC_FILENAME, "old_mhz": 90.0, "ors_id": "ors-a"},
            {"old_id": "old-fm4", "old_file": BC_FILENAME, "old_mhz": 98.9, "ors_id": "ors-fm4",
             "ors_mhz": 98.8},
        ]

        global PAIRS
        echte_pairs = PAIRS
        PAIRS = test_pairs
        try:
            files, load_errors = _load_required_files(tmp)
            check("T1 Dateien geladen", load_errors == [], load_errors)
            problems, ctx = precheck(files)
            check("T2 keine Vorpruefungsprobleme", problems == [], problems)

            applied_log = do_apply(files, ctx)
            check("T3 Log hat 3 Zeilen (2 Paare + CZ)", len(applied_log) == 3, applied_log)

            ors_ids_nachher = {e["id"] for e in files[ORS_FILENAME]["entries"]}
            check("T4 ORS traegt jetzt beide Alt-ids",
                  {"old-a", "old-fm4", "unrelated"} == ors_ids_nachher, ors_ids_nachher)

            entry_a = next(e for e in files[ORS_FILENAME]["entries"] if e["id"] == "old-a")
            check("T5 notes verkettet", entry_a["notes"] == "ORS-Notiz | Alt-Notiz", entry_a)
            check("T6 tags vereinigt (ORS zuerst)",
                  entry_a["tags"] == ["ukw", "broadcast", "alt"], entry_a)
            check("T7 legacy_id_from gesetzt", entry_a["legacy_id_from"] == BC_FILENAME, entry_a)
            check("T8 legacy_label gesetzt", entry_a["legacy_label"] == "A Alt", entry_a)
            check("T9 freq_hz unveraendert (vom ORS-Eintrag)", entry_a["freq_hz"] == 90_000_000, entry_a)

            entry_fm4 = next(e for e in files[ORS_FILENAME]["entries"] if e["id"] == "old-fm4")
            check("T10 Sonderfall: ORS-freq_hz bleibt 98.8 MHz trotz Alt 98.9",
                  entry_fm4["freq_hz"] == 98_800_000, entry_fm4)

            bc_ids_nachher = {e["id"] for e in files[BC_FILENAME]["entries"]}
            check("T11 BC verliert alte ids + CZ, behaelt Rest",
                  bc_ids_nachher == {"bleibt"}, bc_ids_nachher)

            cz_ids_nachher = {e["id"] for e in files[CZ_FILENAME]["entries"]}
            check("T12 CZ_RadioZurnal in ukw_cz.json angekommen",
                  cz_ids_nachher == {CZ_MOVE_ID}, cz_ids_nachher)

            # -- T13: erneuter precheck() auf dem mutierten Stand muss jetzt
            # "existiert bereits in ORS" melden (Wiederholungsschutz). -------
            problems2, _ = precheck(files)
            check("T13 zweiter Lauf auf demselben Stand meldet Doppel-Uebernahme",
                  any("existiert bereits in" in p for p in problems2), problems2)
        finally:
            PAIRS = echte_pairs

    # -- T14: fehlende Alt-id -> Vorpruefung schlaegt fehl, nichts wird
    # veraendert (das prueft main() ueber die echte CLI, damit auch Exit 2
    # und "nichts geschrieben" wirklich stimmen). ----------------------------
    with tempfile.TemporaryDirectory() as tmp:
        import subprocess

        _write_json(os.path.join(tmp, ORS_FILENAME), {
            "version": 1, "meta": {}, "defaults": {}, "bands": [],
            "entries": [{"kind": "channel", "id": pair["ors_id"], "label": "X",
                        "freq_hz": _hz(_ors_mhz(pair))} for pair in PAIRS],
        })
        _write_json(os.path.join(tmp, BC_FILENAME), {
            "version": 1, "meta": {}, "defaults": {}, "bands": [], "entries": [],
        })
        _write_json(os.path.join(tmp, WIEN_FILENAME), {
            "version": 1, "meta": {}, "defaults": {}, "bands": [], "entries": [],
        })
        _write_json(os.path.join(tmp, CZ_FILENAME), {
            "version": 1, "meta": {}, "defaults": {}, "bands": [], "entries": [],
        })

        vorher = {}
        for name in sorted(os.listdir(tmp)):
            with open(os.path.join(tmp, name), "rb") as fh:
                vorher[name] = fh.read()

        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--apply", "--dir", tmp],
            capture_output=True, text=True)
        check("T14a Exit 2 bei fehlenden Alt-Eintraegen", proc.returncode == 2, proc.returncode)

        nachher = {}
        for name in sorted(os.listdir(tmp)):
            with open(os.path.join(tmp, name), "rb") as fh:
                nachher[name] = fh.read()
        check("T14b alle Dateien byte-gleich (nichts geschrieben)", vorher == nachher,
              (sorted(vorher), sorted(nachher)))

    print("[known_freq_wien_merge] Selbsttest " + ("OK" if ok_all else "FAIL"))
    return ok_all


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # cp1252-Konsole, Muster known_freq.py

    ap = argparse.ArgumentParser(
        description="Wiener UKW-Doppelungen ORS_UKW_AT.json <-> UKW_BC.json/ukw_wien.json aufloesen")
    ap.add_argument("--apply", action="store_true", help="Uebernahme ausfuehren")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument("--dir", default=None, help="abweichendes Katalogverzeichnis")
    args = ap.parse_args()

    if args.selftest:
        return 0 if _self_test() else 1

    catalog_dir = args.dir or CATALOG_DIR
    files, load_errors = _load_required_files(catalog_dir)
    problems, ctx = precheck(files)
    problems = load_errors + problems

    if problems:
        print("ABBRUCH (Vorpruefung fehlgeschlagen, nichts geschrieben):")
        for p in problems:
            print(f"  - {p}")
        return 2

    alt_ids = [pair["old_id"] for pair in PAIRS]
    grep_hits = repo_grep(alt_ids)

    if args.apply:
        applied_log = do_apply(files, ctx)
        touched = {ORS_FILENAME, BC_FILENAME, WIEN_FILENAME, CZ_FILENAME}
        for name in sorted(touched):
            _write_known_json(os.path.join(catalog_dir, name), files[name])
    else:
        applied_log = None

    print(build_report(files, ctx, grep_hits, applied_log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
