#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/known_freq_abgleich.py

Gleicht die Baender aller anderen known_freq/*.json-Dateien gegen die
FNV-Referenzdatei (Frequenznutzungsplan Oesterreich, Anlage 2, 30 MHz -
3 GHz) ab. Ziel: die FNV-Datei ist die einzige Quelle fuer nationale
Zuweisungsbaender in diesem Bereich - Baender anderer Dateien, die sie
bereits abdeckt, werden entfernt oder (wenn sie zusaetzliche Struktur
tragen oder ausserhalb 30 MHz-3 GHz liegen) nach fnv_ergaenzungen.json
verschoben. Kanaleintraege (entries) werden von diesem Werkzeug NIE
veraendert - weder im Trockenlauf noch mit --apply.

ABWEICHUNG VOM AUFTRAG (siehe Abschlussbericht der Session, die dieses
Werkzeug angelegt hat): der Auftrag nennt als Referenzdatei
known_freq/fnv_at_30mhz_3ghz.json. Diese Datei existiert im Repo nicht;
vorhanden ist known_freq/FNV_AT_2026.json mit inhaltlich identischer
Rolle (meta.service "Frequenznutzung Oesterreich (FNV Anlage 2)",
meta.source nennt die Frequenznutzungsverordnung 2023, 30 MHz-3 GHz,
Struktur bands/segments). Nach Rueckfrage wurde diese Datei als Referenz
freigegeben, der Name ist daher unten als FNV_FILENAME hinterlegt statt
wie im Auftrag woertlich benannt. Ebenso nennt der Auftrag im Abschnitt
Kanal-Doppelungen "ors_ukw_oesterreich.json"; die tatsaechlich vorhandene
Datei heisst known_freq/ORS_UKW_AT.json. Das Werkzeug hardcodet keinen
Dateinamen fuer die Kanal-Doppelungen-Pruefung (sie gruppiert generisch
ueber alle geladenen Kanaele), die Abweichung wirkt sich daher nicht auf
das Ergebnis aus.

Klassifikation je Band B (siehe classify_band()):
  AUSSERHALB  B liegt (ganz oder teilweise) ausserhalb 30 MHz-3 GHz
              -> Vorschlag VERSCHIEBEN nach fnv_ergaenzungen.json
  DOPPELT     ein einzelnes FNV-Band/-Segment deckt B vollstaendig ab,
              B hat keine eigenen Segmente oder alle sind (start,stop)-
              gleich mit einem FNV-Element -> Vorschlag LOESCHEN
  FEINER      wie DOPPELT abgedeckt, aber mindestens ein Segment von B
              hat keine (start,stop)-Entsprechung in der FNV
              -> Vorschlag VERSCHIEBEN (Band samt Segmenten)
  UEBERSPANNT B liegt in 30 MHz-3 GHz, kein einzelnes FNV-Element deckt
              es ab, aber die Vereinigung aneinandergrenzender FNV-
              Baender tut es -> Vorschlag PRUEFEN
  TEILWEISE   ueberlappt die FNV nur teilweise oder gar nicht
              -> Vorschlag PRUEFEN

Toleranz bei allen Grenzvergleichen: 0 Hz (exakt). Die FNV nennt bei
Frequenzteilbereichen die Mittenfrequenzen der Eckkanaele statt der
Bandkanten - ein 12,5-kHz-Versatz ist deshalb bei TEILWEISE haeufig; der
Bericht nennt dort zusaetzlich den kleinsten Kantenversatz in Hz.

CLI:
  python tools/known_freq_abgleich.py             Trockenlauf, Bericht
                                                    nach docs/known_freq_
                                                    abgleich.md + stdout
  python tools/known_freq_abgleich.py --apply      fuehrt LOESCHEN/
                                                    VERSCHIEBEN aus,
                                                    PRUEFEN bleibt liegen
  python tools/known_freq_abgleich.py --selftest   Selbsttest, tempfaehig
  python tools/known_freq_abgleich.py --dir PFAD   abweichendes Katalog-
                                                    verzeichnis (Muster
                                                    known_freq.py --dir)
  python tools/known_freq_abgleich.py
      --entscheidungen PFAD.json                   ersetzt die Werkzeug-
                                                    Vorschlaege durch eine
                                                    dokumentierte Entscheidung
                                                    je Band-id (siehe
                                                    apply_entscheidungen());
                                                    kombinierbar mit --apply
                                                    - dort NUR ausgefuehrt,
                                                    wenn die Entscheidungsdatei
                                                    vollstaendig und gueltig
                                                    ist (sonst Exit 2, nichts
                                                    geschrieben - siehe
                                                    validate_entscheidungen())

Konsolenausgaben sind bewusst ASCII/cp1252-sicher (kein Mikro-/Delta-
Zeichen) - Muster eb200_core/known_freq.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# T-2000: Repo-Root statt eb200_core/ in den Suchpfad, Importe mit Paketpraefix.
# block_admin.py importiert seine Geschwistermodule als eb200_core.xxx; ein
# nackter Import haette known_freq ein zweites Mal geladen.
sys.path.insert(0, ROOT)

from eb200_core import known_freq   # noqa: E402
from eb200_core import block_admin   # noqa: E402

FNV_FILENAME = "FNV_AT_2026.json"          # siehe Abweichung im Moduldocstring
ERGAENZUNG_FILENAME = "fnv_ergaenzungen.json"
REPORT_PATH = os.path.join(ROOT, "docs", "known_freq_abgleich.md")

FREQ_LOW_HZ = 30_000_000
FREQ_HIGH_HZ = 3_000_000_000

KLASSE_VORSCHLAG = {
    "AUSSERHALB": "VERSCHIEBEN",
    "DOPPELT": "LOESCHEN",
    "FEINER": "VERSCHIEBEN",
    "UEBERSPANNT": "PRUEFEN",
    "TEILWEISE": "PRUEFEN",
}


class MissingFnvError(Exception):
    """Vorbedingung verletzt: die FNV-Referenzdatei fehlt im Katalogordner."""


# ---------------------------------------------------------------------------
# Rohdaten laden (bewusst NICHT ueber known_freq.load_catalog - die
# Dateien muessen 1:1 zurueckgeschrieben werden koennen, unbekannte
# Schluessel eingeschlossen; load_catalog normalisiert/verwirft sowas).
# ---------------------------------------------------------------------------

def load_raw_files(catalog_dir: str) -> tuple[dict, list]:
    """Liest alle *.json im Katalogordner als Roh-JSON. Liefert
    (name -> dict, Liste von Ladefehlertexten). Eine kaputte Datei wird
    uebersprungen (Fehlertext in der Liste), der Rest laedt weiter -
    gleiche Nie-fatal-Haltung wie known_freq.load_catalog()."""
    files: dict = {}
    errors: list = []
    try:
        namen = sorted(n for n in os.listdir(catalog_dir) if n.endswith(".json"))
    except OSError as exc:
        errors.append(f"Katalogverzeichnis nicht lesbar: {exc}")
        return files, errors
    for name in namen:
        pfad = os.path.join(catalog_dir, name)
        try:
            with open(pfad, encoding="utf-8") as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict):
                raise ValueError("oberste Ebene ist kein Objekt")
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: nicht lesbar - {exc}")
            continue
        files[name] = doc
    return files, errors


# ---------------------------------------------------------------------------
# Referenzliste R aus der FNV-Datei
# ---------------------------------------------------------------------------

def build_reference(fnv_doc: dict) -> list:
    """R = flache Liste aller FNV-Baender UND ihrer Segmente, je als dict
    mit start_hz/stop_hz/title/band_id/band_title/level ('band'/'segment')."""
    R = []
    for b in fnv_doc.get("bands") or []:
        if not isinstance(b, dict):
            continue
        start, stop = b.get("start_hz"), b.get("stop_hz")
        if not isinstance(start, int) or not isinstance(stop, int):
            continue
        bid = b.get("id", "")
        btitle = str(b.get("title", bid))
        R.append({"start_hz": start, "stop_hz": stop, "title": btitle,
                   "band_id": bid, "band_title": btitle, "level": "band"})
        for seg in b.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            s_start, s_stop = seg.get("start_hz"), seg.get("stop_hz")
            if not isinstance(s_start, int) or not isinstance(s_stop, int):
                continue
            R.append({"start_hz": s_start, "stop_hz": s_stop,
                       "title": str(seg.get("title", "")),
                       "band_id": bid, "band_title": btitle, "level": "segment"})
    return R


def _merge_band_chains(R: list) -> list:
    """Aneinandergrenzende/ueberlappende FNV-Baender (NUR Bandebene, keine
    Segmente - siehe UEBERSPANNT-Definition) zu Ketten zusammengefasst.
    Toleranz 0 Hz: ein Band schliesst sich nur an, wenn sein start_hz <=
    dem bisherigen chain-stop_hz ist. Liefert Liste von
    (chain_start, chain_stop, [(start,stop,band_id,band_title), ...])."""
    baender = sorted({(r["start_hz"], r["stop_hz"], r["band_id"], r["band_title"])
                       for r in R if r["level"] == "band"})
    chains = []
    cur_start = cur_stop = None
    cur_members: list = []
    for start, stop, bid, title in baender:
        if cur_start is None:
            cur_start, cur_stop, cur_members = start, stop, [(start, stop, bid, title)]
        elif start <= cur_stop:
            cur_stop = max(cur_stop, stop)
            cur_members.append((start, stop, bid, title))
        else:
            chains.append((cur_start, cur_stop, cur_members))
            cur_start, cur_stop, cur_members = start, stop, [(start, stop, bid, title)]
    if cur_start is not None:
        chains.append((cur_start, cur_stop, cur_members))
    return chains


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------

def _best_match(covering: list) -> dict:
    """Engste (kleinste) Uebereinstimmung zuerst - meist ein Segment statt
    des ganzen Bandes, das ist die aussagekraeftigere Angabe im Bericht."""
    return min(covering, key=lambda r: (r["stop_hz"] - r["start_hz"], r["band_id"], r["title"]))


def _min_offset_hz(B: dict, R: list) -> int | None:
    """Kleinster Kantenversatz (Hz) zwischen B und irgendeinem r in R -
    alle vier Kantenkombinationen (start/start, stop/stop, stop/start,
    start/stop), damit ein "B beginnt exakt dort, wo ein FNV-Band endet,
    nur 12,5 kHz versetzt"-Fall gefunden wird. Nur fuer den Bericht bei
    TEILWEISE gedacht; None, wenn R leer ist."""
    b_start, b_stop = B["start_hz"], B["stop_hz"]
    best = None
    for r in R:
        for cand in (abs(r["start_hz"] - b_start), abs(r["stop_hz"] - b_stop),
                      abs(r["stop_hz"] - b_start), abs(r["start_hz"] - b_stop)):
            if best is None or cand < best:
                best = cand
    return best


def classify_band(B: dict, R: list, chains: list) -> tuple:
    """Liefert (klasse, treffer) - treffer ist eine Liste von r-Dicts
    (band_id/band_title/title/start_hz/stop_hz), die die Klassifikation
    begruenden (leer bei AUSSERHALB/TEILWEISE)."""
    b_start, b_stop = B["start_hz"], B["stop_hz"]

    if b_stop <= FREQ_LOW_HZ or b_start >= FREQ_HIGH_HZ:
        return "AUSSERHALB", []

    covering = [r for r in R if r["start_hz"] <= b_start and b_stop <= r["stop_hz"]]
    if covering:
        segs = B.get("segments") or []
        if not segs:
            return "DOPPELT", [_best_match(covering)]
        exact_pairs = {(r["start_hz"], r["stop_hz"]) for r in R}
        fehlend = [s for s in segs
                   if (s.get("start_hz"), s.get("stop_hz")) not in exact_pairs]
        if not fehlend:
            return "DOPPELT", [_best_match(covering)]
        return "FEINER", [_best_match(covering)]

    for chain_start, chain_stop, members in chains:
        if len(members) > 1 and chain_start <= b_start and b_stop <= chain_stop:
            beteiligt = [{"band_id": m[2], "band_title": m[3], "title": m[3],
                          "start_hz": m[0], "stop_hz": m[1], "level": "band"}
                         for m in members if m[1] > b_start and m[0] < b_stop]
            return "UEBERSPANNT", beteiligt

    return "TEILWEISE", []


# ---------------------------------------------------------------------------
# Analyse-Orchestrierung
# ---------------------------------------------------------------------------

def analyze(catalog_dir: str, fnv_filename: str = FNV_FILENAME,
           erg_filename: str = ERGAENZUNG_FILENAME) -> dict:
    """Laedt den Katalogordner und klassifiziert alle Baender ausserhalb
    der FNV-Datei und der Ergaenzungsdatei. Wirft MissingFnvError, wenn
    fnv_filename fehlt (Vorbedingung des Auftrags)."""
    files, load_errors = load_raw_files(catalog_dir)
    if fnv_filename not in files:
        raise MissingFnvError(
            f"Vorbedingung verletzt: {fnv_filename} fehlt in {catalog_dir}")

    fnv_doc = files[fnv_filename]
    R = build_reference(fnv_doc)
    chains = _merge_band_chains(R)

    results = []
    for fname, doc in files.items():
        if fname in (fnv_filename, erg_filename):
            continue
        for b in doc.get("bands") or []:
            if not isinstance(b, dict):
                continue
            if not isinstance(b.get("start_hz"), int) or not isinstance(b.get("stop_hz"), int):
                load_errors.append(f"{fname}: Band '{b.get('id')}' ohne gueltige start_hz/stop_hz - uebersprungen")
                continue
            klasse, treffer = classify_band(b, R, chains)
            offset = _min_offset_hz(b, R) if klasse == "TEILWEISE" else None
            results.append({"file": fname, "band": b, "klasse": klasse,
                            "vorschlag": KLASSE_VORSCHLAG[klasse],
                            "treffer": treffer, "offset_hz": offset})

    # Kanal-Doppelungen: ueber den echten Loader (Raster expandiert), rein
    # informativ - siehe Moduldocstring. load_catalog ist rein lesend.
    cat = known_freq.load_catalog(catalog_dir)
    freq_groups = defaultdict(list)
    for ch in cat.channels:
        freq_groups[ch["freq_hz"]].append(ch)
    kanal_doppelungen = {f: chs for f, chs in freq_groups.items()
                         if len({c["file"] for c in chs}) > 1}

    return {
        "catalog_dir": catalog_dir,
        "fnv_filename": fnv_filename,
        "erg_filename": erg_filename,
        "files": files,
        "load_errors": load_errors,
        "R": R,
        "results": results,
        "kanal_doppelungen": kanal_doppelungen,
        "catalog_errors": cat.errors,
    }


# ---------------------------------------------------------------------------
# Entscheidungsdatei (--entscheidungen): dokumentierte Band-id -> {"aktion":
# "LOESCHEN"|"VERSCHIEBEN"|"BLEIBT", "ziel": "<dateiname>"} (ziel nur bei
# VERSCHIEBEN, dort Pflicht) ersetzt die Werkzeug-Vorschlaege vollstaendig.
# ---------------------------------------------------------------------------

def apply_entscheidungen(results: list, entscheidungen: dict) -> None:
    """Mutiert results in-place: 'werkzeug' traegt den urspruenglichen
    Werkzeug-Vorschlag, 'vorschlag'/'ziel' kommen aus der Entscheidungsdatei.
    Fehlt fuer ein Band eine Entscheidung, bleibt 'vorschlag' auf dem
    Werkzeug-Vorschlag stehen UND 'entscheidung_fehlt' wird gesetzt - das
    genuegt fuer den Bericht; die harte Abbruchpruefung fuer --apply macht
    validate_entscheidungen()."""
    for r in results:
        bid = r["band"]["id"]
        r["werkzeug"] = r["vorschlag"]
        d = entscheidungen.get(bid)
        if d is None or not isinstance(d, dict):
            r["entscheidung_fehlt"] = True
            continue
        aktion = d.get("aktion")
        if aktion in ("LOESCHEN", "VERSCHIEBEN", "BLEIBT"):
            r["vorschlag"] = aktion
        r["ziel"] = d.get("ziel") if aktion == "VERSCHIEBEN" else None


def validate_entscheidungen(entscheidungen: dict, results: list, files: dict,
                            erg_filename: str) -> list:
    """Liefert eine Liste von Problemtexten (leer = gueltig). Prueft: (a)
    jedes klassifizierte Band hat eine Entscheidung, (b) jede Entscheidung
    nennt eine bekannte Band-id, (c) VERSCHIEBEN hat ein ziel und das ziel
    existiert (erg_filename zaehlt als existent - --apply legt sie bei
    Bedarf an)."""
    problems = []
    known_ids = {r["band"]["id"] for r in results}
    for bid in entscheidungen:
        if bid not in known_ids:
            problems.append(f"Entscheidung nennt unbekannte Band-id '{bid}' (nicht klassifiziert)")
    for r in results:
        bid = r["band"]["id"]
        d = entscheidungen.get(bid)
        if d is None or not isinstance(d, dict):
            problems.append(f"Keine Entscheidung fuer Band '{bid}' ({r['file']})")
            continue
        aktion = d.get("aktion")
        if aktion not in ("LOESCHEN", "VERSCHIEBEN", "BLEIBT"):
            problems.append(f"Band '{bid}': ungueltige aktion {aktion!r}")
            continue
        if aktion == "VERSCHIEBEN":
            ziel = d.get("ziel")
            if not ziel:
                problems.append(f"Band '{bid}': VERSCHIEBEN ohne ziel")
            elif ziel != erg_filename and ziel not in files:
                problems.append(f"Band '{bid}': ziel '{ziel}' existiert nicht")
    return problems


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------

def _mhz(hz: int) -> str:
    return f"{hz / 1e6:.4f}"


def _treffer_text(treffer: list) -> str:
    if not treffer:
        return "-"
    return "; ".join(f"{t['band_id']} ({t['title'] or t['band_title']})" for t in treffer)


def build_report(a: dict, applied: dict | None = None, zeige_werkzeug: bool = False) -> str:
    results = a["results"]
    files = a["files"]
    fnv_filename = a["fnv_filename"]
    erg_filename = a["erg_filename"]

    je_klasse = defaultdict(int)
    for r in results:
        je_klasse[r["klasse"]] += 1

    lines = []
    lines.append("# Known-Freq-Abgleich gegen FNV")
    lines.append("")
    lines.append(f"Referenzdatei: `{fnv_filename}` "
                 f"(siehe Werkzeug-Docstring: Abweichung vom urspruenglichen "
                 f"Dateinamen `fnv_at_30mhz_3ghz.json` aus dem Auftrag)")
    lines.append(f"Katalogverzeichnis: `{a['catalog_dir']}`")
    lines.append("")
    andere_dateien = sorted(f for f in files if f not in (fnv_filename, erg_filename))
    lines.append(f"- Dateien (ohne FNV/Ergaenzung): {len(andere_dateien)}")
    lines.append(f"- Baender gesamt klassifiziert: {len(results)}")
    for klasse in ("AUSSERHALB", "DOPPELT", "FEINER", "UEBERSPANNT", "TEILWEISE"):
        lines.append(f"  - {klasse}: {je_klasse.get(klasse, 0)}")
    if a["load_errors"]:
        lines.append(f"- Ladefehler: {len(a['load_errors'])}")
        for e in a["load_errors"]:
            lines.append(f"  - {e}")
    if a["catalog_errors"]:
        lines.append(f"- load_catalog()-Fehler (nur Info, betrifft Kanal-Doppelungen-Abschnitt): {len(a['catalog_errors'])}")
        for e in a["catalog_errors"]:
            lines.append(f"  - {e}")
    lines.append("")

    lines.append("## Baender je Datei")
    lines.append("")
    if zeige_werkzeug:
        lines.append("| Datei | Band-id | Titel | Bereich MHz | Segmente | Klasse | Vorschlag | Werkzeug | FNV-Treffer |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
    else:
        lines.append("| Datei | Band-id | Titel | Bereich MHz | Segmente | Klasse | Vorschlag | FNV-Treffer |")
        lines.append("|---|---|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda r: (r["file"], r["band"]["start_hz"])):
        b = r["band"]
        bereich = f"{_mhz(b['start_hz'])}-{_mhz(b['stop_hz'])}"
        zusatz = ""
        if r["klasse"] == "TEILWEISE" and r["offset_hz"] is not None:
            zusatz = f" (kleinster Versatz {r['offset_hz']} Hz)"
        vorschlag = r["vorschlag"] + (" (FEHLT)" if r.get("entscheidung_fehlt") else "")
        if zeige_werkzeug:
            lines.append(f"| {r['file']} | {b.get('id','')} | {b.get('title','')} | "
                         f"{bereich} | {len(b.get('segments') or [])} | {r['klasse']}{zusatz} | "
                         f"{vorschlag} | {r.get('werkzeug','')} | {_treffer_text(r['treffer'])} |")
        else:
            lines.append(f"| {r['file']} | {b.get('id','')} | {b.get('title','')} | "
                         f"{bereich} | {len(b.get('segments') or [])} | {r['klasse']}{zusatz} | "
                         f"{vorschlag} | {_treffer_text(r['treffer'])} |")
    lines.append("")

    lines.append("## Dateien nach Anwendung")
    lines.append("")
    lines.append("Zeigt den Stand, den `--apply` herstellen wuerde (Trockenlauf nimmt nichts vor).")
    lines.append("")
    lines.append("| Datei | Kanaele (unveraendert) | Baender vorher | Baender nachher | leer danach |")
    lines.append("|---|---|---|---|---|")
    by_file = defaultdict(list)
    for r in results:
        by_file[r["file"]].append(r)
    for fname in andere_dateien:
        doc = files[fname]
        entries_n = len(doc.get("entries") or [])
        bands_vorher = len(doc.get("bands") or [])
        entfernt = sum(1 for r in by_file.get(fname, []) if r["vorschlag"] in ("LOESCHEN", "VERSCHIEBEN"))
        bands_nachher = bands_vorher - entfernt
        leer = "ja" if (entries_n == 0 and bands_nachher == 0) else "nein"
        lines.append(f"| {fname} | {entries_n} | {bands_vorher} | {bands_nachher} | {leer} |")
    lines.append("")

    lines.append("## Kanal-Doppelungen (Information, keine Aktion)")
    lines.append("")
    lines.append("Kanaele aus verschiedenen Dateien auf derselben Frequenz - laut "
                 "docs/WS3 - Wasserfall.md Abschnitt 14.2 teils gewollt (in- und "
                 "auslaendischer Sender auf derselben UKW-Frequenz). Keine Aktion, "
                 "auch nicht mit --apply.")
    lines.append("")
    if not a["kanal_doppelungen"]:
        lines.append("Keine gefunden.")
    else:
        lines.append("| Frequenz MHz | Dateien | Kanaele |")
        lines.append("|---|---|---|")
        for freq in sorted(a["kanal_doppelungen"]):
            chs = a["kanal_doppelungen"][freq]
            dateien = ", ".join(sorted({c["file"] for c in chs}))
            kanaele = ", ".join(f"{c['id']} ({c['file']})" for c in chs)
            lines.append(f"| {_mhz(freq)} | {dateien} | {kanaele} |")
    lines.append("")

    if applied is not None:
        lines.append("## Ausgefuehrt")
        lines.append("")
        if not applied["executed"]:
            lines.append("Keine LOESCHEN/VERSCHIEBEN-Vorschlaege vorhanden.")
        else:
            for line in applied["executed"]:
                lines.append(f"- {line}")
        if applied["deleted_files"]:
            lines.append("")
            lines.append("Entfernte (leer gewordene) Dateien:")
            for fname in applied["deleted_files"]:
                lines.append(f"- {fname}")
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# --apply
# ---------------------------------------------------------------------------

def _eol_of(data: bytes) -> bytes:
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf * 2 > data.count(b"\n") else b"\n"


def _write_known_json(path: str, doc: dict) -> None:
    """Schreibt eine Katalogdatei zurueck: .bak der Vorversion (falls
    vorhanden), atomar via .tmp + os.replace, Zeilenende der Zieldatei
    beibehalten (Neuanlage: CRLF) - Muster eb200_core/block_admin.py
    _write_known_file()."""
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


def do_apply(a: dict) -> dict:
    """Fuehrt NUR LOESCHEN/VERSCHIEBEN aus (PRUEFEN/BLEIBT bleibt unangetastet).
    VERSCHIEBEN zielt auf r['ziel'] (von apply_entscheidungen() gesetzt) oder
    - ohne --entscheidungen bzw. wenn kein ziel gesetzt ist - auf
    fnv_ergaenzungen.json (bisheriges Default-Verhalten). Diese wird NUR
    angelegt, wenn tatsaechlich ein VERSCHIEBEN dorthin zeigt. Mutiert
    a['files'] in-place; jede beruehrte Datei (Quelle wie Ziel) wird genau
    einmal, erst NACHDEM alle Verschiebungen im Speicher nachvollzogen sind,
    zurueckgeschrieben - sonst wuerde eine Quelle, die zugleich Ziel einer
    spaeteren Verschiebung ist, mit veraltetem Stand geschrieben. Leer
    gewordene Dateien werden ueber block_admin.delete_known_file() entfernt.
    Liefert {'executed': [...], 'deleted_files': [...]}."""
    catalog_dir = a["catalog_dir"]
    files = a["files"]
    fnv_filename = a["fnv_filename"]
    erg_filename = a["erg_filename"]
    results = a["results"]

    def ziel_fuer(r: dict) -> str:
        return r.get("ziel") or erg_filename

    verschiebe_ziele = {ziel_fuer(r) for r in results if r["vorschlag"] == "VERSCHIEBEN"}
    if erg_filename in verschiebe_ziele and erg_filename not in files:
        andere = sorted(f for f in files if f != fnv_filename)
        files[erg_filename] = {
            "version": 1,
            "meta": {"service": "Ergaenzungen zur FNV",
                     "source": "Zusammengefuehrt aus " + ", ".join(andere)},
            "defaults": {},
            "bands": [],
            "entries": [],
        }

    by_file = defaultdict(list)
    for r in results:
        if r["vorschlag"] in ("LOESCHEN", "VERSCHIEBEN"):
            by_file[r["file"]].append(r)

    executed = []
    touched = set()
    for fname, items in sorted(by_file.items()):
        doc = files[fname]
        bands = doc.get("bands") or []
        remove_ids = set()
        for r in sorted(items, key=lambda r: r["band"]["id"]):
            bid = r["band"]["id"]
            remove_ids.add(bid)
            if r["vorschlag"] == "VERSCHIEBEN":
                ziel = ziel_fuer(r)
                moved = dict(r["band"])
                moved["moved_from"] = fname
                if not isinstance(files[ziel].get("bands"), list):
                    files[ziel]["bands"] = []
                files[ziel]["bands"].append(moved)
                touched.add(ziel)
                executed.append(f"VERSCHIEBEN {fname}:{bid} -> {ziel}")
            else:
                executed.append(f"LOESCHEN {fname}:{bid}")
        doc["bands"] = [b for b in bands if b.get("id") not in remove_ids]
        touched.add(fname)

    for fname in sorted(touched):
        _write_known_json(os.path.join(catalog_dir, fname), files[fname])

    deleted_files = []
    for fname, doc in list(files.items()):
        if fname in (fnv_filename, erg_filename):
            continue
        if not (doc.get("entries") or []) and not (doc.get("bands") or []):
            try:
                block_admin.delete_known_file(os.path.join(catalog_dir, fname))
                deleted_files.append(fname)
            except ValueError:
                pass

    return {"executed": executed, "deleted_files": deleted_files}


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

    with tempfile.TemporaryDirectory() as tmp:
        # -- FNV-Referenz: zwei aneinandergrenzende Baender (fuer
        # UEBERSPANNT) plus ein Band mit zwei Segmenten (fuer
        # DOPPELT/FEINER/TEILWEISE-Kanten). --------------------------------
        fnv_doc = {
            "version": 1, "meta": {"service": "Test-FNV"}, "defaults": {},
            "bands": [
                {"id": "fnv-a", "title": "Band A", "start_hz": 40_000_000, "stop_hz": 50_000_000},
                {"id": "fnv-b", "title": "Band B", "start_hz": 50_000_000, "stop_hz": 60_000_000},
                {"id": "fnv-c", "title": "Band C", "start_hz": 100_000_000, "stop_hz": 110_000_000,
                 "segments": [
                     {"title": "C1", "start_hz": 100_000_000, "stop_hz": 102_000_000},
                     {"title": "C2", "start_hz": 105_000_000, "stop_hz": 108_000_000},
                 ]},
            ],
            "entries": [],
        }
        _write_json(os.path.join(tmp, FNV_FILENAME), fnv_doc)

        # t1.json: DOPPELT + FEINER, keine entries -> muss nach --apply
        # leer sein und entfernt werden.
        b_doppelt = {"id": "t1-doppelt", "title": "Doppelt", "start_hz": 100_500_000, "stop_hz": 101_500_000}
        b_feiner = {"id": "t1-feiner", "title": "Feiner", "start_hz": 100_000_000, "stop_hz": 108_000_000,
                    "segments": [
                        {"title": "match", "start_hz": 100_000_000, "stop_hz": 102_000_000},
                        {"title": "extra", "start_hz": 103_000_000, "stop_hz": 104_000_000},
                    ]}
        t1_doc = {"version": 1, "meta": {"service": "T1"}, "defaults": {},
                  "bands": [b_doppelt, b_feiner], "entries": []}
        _write_json(os.path.join(tmp, "t1.json"), t1_doc)

        # t2.json: AUSSERHALB, plus eine entries-Kanal - muss nach --apply
        # bestehen bleiben (entries != 0), Band verschoben.
        b_ausserhalb = {"id": "t2-ausserhalb", "title": "Ausserhalb", "start_hz": 20_000_000, "stop_hz": 25_000_000}
        t2_doc = {"version": 1, "meta": {"service": "T2"}, "defaults": {"demod": "FM", "bw_hz": 15000},
                  "bands": [b_ausserhalb],
                  "entries": [{"kind": "channel", "id": "t2-ch1", "label": "Kanal2", "freq_hz": 70_000_000}]}
        _write_json(os.path.join(tmp, "t2.json"), t2_doc)

        # t3.json: UEBERSPANNT + TEILWEISE (12,5-kHz-Versatz), plus eine
        # entries-Kanal - beide Baender bleiben (PRUEFEN), Datei bleibt.
        b_ueberspannt = {"id": "t3-ueberspannt", "title": "Ueberspannt", "start_hz": 45_000_000, "stop_hz": 55_000_000}
        b_teilweise = {"id": "t3-teilweise", "title": "Teilweise", "start_hz": 110_012_500, "stop_hz": 115_000_000}
        t3_doc = {"version": 1, "meta": {"service": "T3"}, "defaults": {"demod": "FM", "bw_hz": 15000},
                  "bands": [b_ueberspannt, b_teilweise],
                  "entries": [{"kind": "channel", "id": "t3-ch1", "label": "Kanal3", "freq_hz": 80_000_000}]}
        _write_json(os.path.join(tmp, "t3.json"), t3_doc)

        # -- Analyse + Klassifikation -----------------------------------
        a = analyze(tmp)
        by_id = {r["band"]["id"]: r for r in a["results"]}

        check("T1 DOPPELT", by_id["t1-doppelt"]["klasse"] == "DOPPELT" and
              by_id["t1-doppelt"]["vorschlag"] == "LOESCHEN", by_id["t1-doppelt"])
        check("T2 FEINER", by_id["t1-feiner"]["klasse"] == "FEINER" and
              by_id["t1-feiner"]["vorschlag"] == "VERSCHIEBEN", by_id["t1-feiner"])
        check("T3 AUSSERHALB", by_id["t2-ausserhalb"]["klasse"] == "AUSSERHALB" and
              by_id["t2-ausserhalb"]["vorschlag"] == "VERSCHIEBEN", by_id["t2-ausserhalb"])
        ueberspannt = by_id["t3-ueberspannt"]
        beteiligte_ids = {t["band_id"] for t in ueberspannt["treffer"]}
        check("T4 UEBERSPANNT", ueberspannt["klasse"] == "UEBERSPANNT" and
              ueberspannt["vorschlag"] == "PRUEFEN" and
              beteiligte_ids == {"fnv-a", "fnv-b"}, (ueberspannt["klasse"], beteiligte_ids))
        teilweise = by_id["t3-teilweise"]
        check("T5 TEILWEISE", teilweise["klasse"] == "TEILWEISE" and
              teilweise["vorschlag"] == "PRUEFEN" and
              teilweise["offset_hz"] == 12_500, teilweise)

        # -- Bericht muss ohne Ausnahme aufbaubar sein ----------------------
        try:
            build_report(a)
            check("T6 Bericht aufbaubar", True)
        except Exception as exc:   # noqa: BLE001 - Selbsttest will jeden Fehler sehen
            check("T6 Bericht aufbaubar", False, str(exc))

        # -- vor --apply: Schnappschuesse fuer den spaeteren Vergleich -----
        vor_t2_entries = json.loads(json.dumps(t2_doc["entries"]))
        vor_t3_entries = json.loads(json.dumps(t3_doc["entries"]))
        vor_t3_bands = json.loads(json.dumps(t3_doc["bands"]))

        applied = do_apply(a)

        check("T7 t1.json entfernt", not os.path.isfile(os.path.join(tmp, "t1.json")) and
              not os.path.isfile(os.path.join(tmp, "t1.json.bak")))
        check("T8 t1.json in deleted_files", "t1.json" in applied["deleted_files"], applied["deleted_files"])

        with open(os.path.join(tmp, "t2.json"), encoding="utf-8") as fh:
            t2_nach = json.load(fh)
        check("T9 t2 Kanaele unveraendert", t2_nach["entries"] == vor_t2_entries, t2_nach["entries"])
        check("T10 t2 Band entfernt", t2_nach["bands"] == [])

        with open(os.path.join(tmp, "t3.json"), encoding="utf-8") as fh:
            t3_nach = json.load(fh)
        check("T11 t3 Kanaele unveraendert", t3_nach["entries"] == vor_t3_entries, t3_nach["entries"])
        check("T12 t3 PRUEFEN-Baender unveraendert", t3_nach["bands"] == vor_t3_bands, t3_nach["bands"])

        erg_path = os.path.join(tmp, ERGAENZUNG_FILENAME)
        check("T13 fnv_ergaenzungen.json angelegt", os.path.isfile(erg_path))
        with open(erg_path, encoding="utf-8") as fh:
            erg_doc = json.load(fh)
        erg_ids = {b["id"] for b in erg_doc.get("bands", [])}
        check("T14 verschobene Band-ids unveraendert",
              erg_ids == {"t1-feiner", "t2-ausserhalb"}, erg_ids)
        moved_from = {b["id"]: b.get("moved_from") for b in erg_doc["bands"]}
        check("T15 moved_from korrekt",
              moved_from == {"t1-feiner": "t1.json", "t2-ausserhalb": "t2.json"}, moved_from)

        cat = known_freq.load_catalog(tmp)
        check("T16 load_catalog ohne Fehler nach --apply", cat.errors == [], cat.errors)

    # -- T17: --entscheidungen mit ziel auf eine bestehende (nicht die
    # Ergaenzungs-)Datei - Band muss dort ankommen, Quelle verliert es, und
    # fnv_ergaenzungen.json wird NICHT angelegt (siehe Auftrag Teil A). -------
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(os.path.join(tmp, FNV_FILENAME), {
            "version": 1, "meta": {"service": "Test-FNV"}, "defaults": {},
            "bands": [], "entries": [],
        })
        u1_band = {"id": "u1-band", "title": "Ausserhalb", "start_hz": 20_000_000, "stop_hz": 25_000_000}
        _write_json(os.path.join(tmp, "u1.json"), {
            "version": 1, "meta": {"service": "U1"}, "defaults": {},
            "bands": [u1_band], "entries": [],
        })
        u2_band_vorhanden = {"id": "u2-vorhanden", "title": "Schon da", "start_hz": 1_000_000, "stop_hz": 2_000_000}
        _write_json(os.path.join(tmp, "u2.json"), {
            "version": 1, "meta": {"service": "U2"}, "defaults": {},
            "bands": [u2_band_vorhanden], "entries": [],
        })

        a17 = analyze(tmp)
        entscheidungen17 = {"u1-band": {"aktion": "VERSCHIEBEN", "ziel": "u2.json"},
                           "u2-vorhanden": {"aktion": "BLEIBT"}}
        apply_entscheidungen(a17["results"], entscheidungen17)
        problems17 = validate_entscheidungen(entscheidungen17, a17["results"], a17["files"], a17["erg_filename"])
        check("T17a Entscheidungsdatei gueltig (ziel existiert)", problems17 == [], problems17)

        applied17 = do_apply(a17)

        check("T17b Quelle (leer geworden) entfernt",
              "u1.json" in applied17["deleted_files"] and not os.path.isfile(os.path.join(tmp, "u1.json")),
              applied17["deleted_files"])

        with open(os.path.join(tmp, "u2.json"), encoding="utf-8") as fh:
            u2_nach = json.load(fh)
        u2_ids = {b["id"] for b in u2_nach["bands"]}
        check("T17c Ziel behaelt Bestand + bekommt das verschobene Band",
              u2_ids == {"u2-vorhanden", "u1-band"}, u2_ids)
        moved17 = next(b for b in u2_nach["bands"] if b["id"] == "u1-band")
        check("T17d moved_from korrekt gesetzt", moved17.get("moved_from") == "u1.json", moved17)

        check("T17e fnv_ergaenzungen.json NICHT angelegt (ziel war u2.json, nicht die Ergaenzungsdatei)",
              not os.path.isfile(os.path.join(tmp, ERGAENZUNG_FILENAME)))

    # -- T18: --apply mit --entscheidungen verweigert bei fehlender
    # Entscheidung (Exit 2, NICHTS geschrieben) - ueber die echte CLI
    # (subprocess), damit auch der Exit-Code selbst geprueft ist. -----------
    with tempfile.TemporaryDirectory() as tmp:
        import subprocess

        _write_json(os.path.join(tmp, FNV_FILENAME), {
            "version": 1, "meta": {"service": "Test-FNV"}, "defaults": {},
            "bands": [], "entries": [],
        })
        v1_band = {"id": "v1-band", "title": "Ausserhalb", "start_hz": 20_000_000, "stop_hz": 25_000_000}
        _write_json(os.path.join(tmp, "v1.json"), {
            "version": 1, "meta": {"service": "V1"}, "defaults": {},
            "bands": [v1_band], "entries": [{"kind": "channel", "id": "v1-ch1",
                                             "label": "K", "freq_hz": 70_000_000,
                                             "demod": "FM", "bw_hz": 15000}],
        })
        entscheidungen_path = os.path.join(tmp, "entscheidungen.json")
        _write_json(entscheidungen_path, {})   # absichtlich leer - v1-band fehlt

        vorher = {}
        for name in sorted(os.listdir(tmp)):
            if name.endswith(".json"):
                with open(os.path.join(tmp, name), "rb") as fh:
                    vorher[name] = fh.read()

        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--apply",
             "--dir", tmp, "--entscheidungen", entscheidungen_path],
            capture_output=True, text=True)
        check("T18a Exit 2 bei fehlender Entscheidung", proc.returncode == 2, proc.returncode)

        nachher = {}
        for name in sorted(os.listdir(tmp)):
            if name.endswith(".json"):
                with open(os.path.join(tmp, name), "rb") as fh:
                    nachher[name] = fh.read()
        check("T18b alle Dateien byte-gleich (nichts geschrieben)", vorher == nachher,
              (sorted(vorher), sorted(nachher)))

    print("[known_freq_abgleich] Selbsttest " + ("OK" if ok_all else "FAIL"))
    return ok_all


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # cp1252-Konsole, Muster known_freq.py

    ap = argparse.ArgumentParser(description="Known-Freq-Katalog gegen die FNV abgleichen")
    ap.add_argument("--apply", action="store_true", help="LOESCHEN/VERSCHIEBEN ausfuehren")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument("--dir", default=None, help="abweichendes Katalogverzeichnis")
    ap.add_argument("--entscheidungen", default=None,
                    help="Entscheidungsdatei (Band-id -> {aktion,ziel}), ersetzt die Werkzeug-Vorschlaege")
    args = ap.parse_args()

    if args.selftest:
        return 0 if _self_test() else 1

    entscheidungen = None
    if args.entscheidungen:
        try:
            with open(args.entscheidungen, encoding="utf-8") as fh:
                entscheidungen = json.load(fh)
            if not isinstance(entscheidungen, dict):
                raise ValueError("oberste Ebene muss ein Objekt sein (Band-id -> Entscheidung)")
        except (OSError, ValueError) as exc:
            print(f"ABBRUCH: Entscheidungsdatei nicht lesbar - {exc}")
            return 1

    catalog_dir = args.dir or known_freq.DEFAULT_CATALOG_DIR
    try:
        a = analyze(catalog_dir)
    except MissingFnvError as exc:
        print(f"ABBRUCH: {exc}")
        return 1

    if entscheidungen is not None:
        apply_entscheidungen(a["results"], entscheidungen)

    if args.apply:
        if entscheidungen is not None:
            problems = validate_entscheidungen(entscheidungen, a["results"], a["files"], a["erg_filename"])
            if problems:
                print("ABBRUCH (--entscheidungen unvollstaendig/fehlerhaft, nichts geschrieben):")
                for p in problems:
                    print(f"  - {p}")
                return 2
        applied = do_apply(a)
    else:
        applied = None

    text = build_report(a, applied, zeige_werkzeug=(entscheidungen is not None))

    print(text)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
