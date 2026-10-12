#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/export_public.py - schreibt den Veroeffentlichungsbaum fuer das
oeffentliche Repo oe3gas/moniscope aus diesem Repo heraus (T-2019).

EB200repo ist die Werkstatt, moniscope die Auslieferung. Der Export nimmt den
Inhalt von HEAD (nicht den Arbeitsbaum - Laufzeitzustand und uncommittete
Auftragsdateien bleiben damit zwangslaeufig draussen), laesst die
Werkstattteile weg und legt die nur im oeffentlichen Repo gefuehrten Dateien
aus publish/ darueber.

  python tools/export_public.py --ziel <verzeichnis>
  python tools/export_public.py --ziel <verzeichnis> --pruefen

--pruefen laesst nach dem Schreiben die Selbsttests der Suite gegen den
EXPORTIERTEN Baum laufen. Das ist die eigentliche Absicherung: sie beweist,
dass die Auswahl vollstaendig ist und nichts Weggelassenes gebraucht wird.

Der Export schreibt nur in das Zielverzeichnis. Ein vorhandenes Ziel muss leer
sein oder mit --leeren ausdruecklich geraeumt werden; ein Git-Verzeichnis im
Ziel bleibt dabei stehen, damit der Export in einen bestehenden Klon laufen
kann.

Zeilenenden: der Export schreibt die Blobs byteweise so, wie sie in HEAD
liegen (also mit LF). Das ist richtig so - .gitattributes des Zielrepos setzt
beim Auschecken wieder CRLF, genau wie hier.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Auswahl
# ---------------------------------------------------------------------------

# Verzeichnisse, die nicht mit ausgeliefert werden. Entscheidung 2026-10-12:
# der Entwicklungsprozess (Auftraege, Berichte, Abnahmen, Messreihen) bleibt
# in der Werkstatt, das Ergebnis geht raus.
AUSGESCHLOSSENE_PRAEFIXE = (
    "docs/prompts/",
    "docs/berichte/",
    "docs/abnahme/",
    "docs/messungen/",
    "jobs/",
    ".claude/",
    # publish/ selbst wird nie mitkopiert - seine Dateien kommen ueber
    # UEBERLAGERUNG an ihren Zielort.
    "publish/",
)

# Einzelne Dateien. .gitignore wird durch publish/gitignore.public ersetzt;
# die vier Testskripte sind Streuexperimente ohne Verbraucher im Code.
AUSGESCHLOSSENE_DATEIEN = (
    ".gitignore",
    "docs/known_freq_abgleich.md",
    "docs/known_freq_abgleich_entscheidungen.json",
    "audio_test6.py",
    "audio_test7.py",
    "audio_test8.py",
    "SDR_probe_test.py",
)

# Nur im oeffentlichen Repo gefuehrte Einzeldateien: Quelle -> Ziel.
UEBERLAGERUNG = {
    "publish/LICENSE": "LICENSE",
    "publish/README.md": "README.md",
    "publish/gitignore.public": ".gitignore",
}

# Ganze Verzeichnisse aus publish/: Quellpraefix -> Zielpraefix. Die
# Bildschirmfotos des README liegen in der Werkstatt unter publish/bilder/
# und im oeffentlichen Repo unter bilder/.
UEBERLAGERUNG_VERZEICHNISSE = {
    "publish/bilder/": "bilder/",
}

# Selbsttests fuer --pruefen. Jeder muss mit Exit 0 enden.
PRUEFLAEUFE = (
    ["tools/check_js_blocks.py", "waterfall.py", "analyze_sessions.py",
     "analyze_spectrum.py", "sdr_waterfall.py", "manage_jobs.py",
     "analyze_mscan.py", "analyze_audio.py", "eb200_core/block_admin.py"],
    ["tools/sync_known_block.py", "--check", "analyze_sessions.py",
     "analyze_spectrum.py", "sdr_waterfall.py"],
    ["tools/check_ui_lang.py"],
    ["eb200_core/ui_theme.py", "--selftest"],
    ["tools/selftest_param_sync.py"],
    ["start_eb200.py", "--selftest"],
)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def git(repo: str, *args: str) -> str:
    """Fuehrt git im Repo aus und liefert stdout. Wirft bei Exit != 0."""
    fertig = subprocess.run(["git", "-C", repo, *args],
                            capture_output=True, check=True)
    return fertig.stdout.decode("utf-8", "replace")


def blob(repo: str, pfad: str) -> bytes:
    """Liefert den Inhalt von HEAD:<pfad> als Bytes, ohne Zeilenendenfilter."""
    fertig = subprocess.run(["git", "-C", repo, "show", f"HEAD:{pfad}"],
                            capture_output=True, check=True)
    return fertig.stdout


def auswahl(repo: str) -> list[str]:
    """Die zu exportierenden Pfade aus HEAD, sortiert."""
    roh = git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    behalten = []
    for pfad in roh:
        if pfad.startswith(AUSGESCHLOSSENE_PRAEFIXE):
            continue
        if pfad in AUSGESCHLOSSENE_DATEIEN:
            continue
        behalten.append(pfad)
    return sorted(behalten)


def ziel_raeumen(ziel: str) -> None:
    """Leert das Ziel bis auf .git - ein bestehender Klon bleibt brauchbar."""
    for name in os.listdir(ziel):
        if name == ".git":
            continue
        voll = os.path.join(ziel, name)
        if os.path.isdir(voll) and not os.path.islink(voll):
            shutil.rmtree(voll)
        else:
            os.remove(voll)


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------

def exportieren(repo: str, ziel: str, leeren: bool) -> list[str]:
    if os.path.exists(ziel):
        inhalt = [n for n in os.listdir(ziel) if n != ".git"]
        if inhalt and not leeren:
            raise SystemExit(
                f"Ziel {ziel} ist nicht leer ({len(inhalt)} Eintraege). "
                "Mit --leeren raeumen oder ein anderes Ziel waehlen.")
        if inhalt:
            ziel_raeumen(ziel)
    else:
        os.makedirs(ziel)

    pfade = auswahl(repo)
    for pfad in pfade:
        ablage = os.path.join(ziel, pfad.replace("/", os.sep))
        os.makedirs(os.path.dirname(ablage) or ziel, exist_ok=True)
        with open(ablage, "wb") as f:
            f.write(blob(repo, pfad))

    geschrieben = list(pfade)
    ueberlagert = dict(UEBERLAGERUNG)

    # Verzeichnisse aus publish/ werden erst hier aufgeloest - so bleibt die
    # Zuordnung Quelle->Ziel an einer Stelle, auch fuer die Bilder.
    alle = git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    for quell_praefix, ziel_praefix in UEBERLAGERUNG_VERZEICHNISSE.items():
        for pfad in alle:
            if pfad.startswith(quell_praefix):
                ueberlagert[pfad] = ziel_praefix + pfad[len(quell_praefix):]

    for quelle, nach in sorted(ueberlagert.items()):
        ablage = os.path.join(ziel, nach.replace("/", os.sep))
        os.makedirs(os.path.dirname(ablage) or ziel, exist_ok=True)
        with open(ablage, "wb") as f:
            f.write(blob(repo, quelle))
        geschrieben.append(nach)

    return sorted(geschrieben)


def pruefen(ziel: str) -> int:
    """Laesst die Selbsttests gegen den exportierten Baum laufen."""
    fehler = 0
    for lauf in PRUEFLAEUFE:
        fertig = subprocess.run([sys.executable, *lauf], cwd=ziel,
                                capture_output=True)
        marke = "OK " if fertig.returncode == 0 else "FEHL"
        print(f"  [{marke}] {' '.join(lauf[:2])}")
        if fertig.returncode != 0:
            fehler += 1
            print(fertig.stdout.decode("utf-8", "replace")[-800:])
            print(fertig.stderr.decode("utf-8", "replace")[-800:])
    return fehler


def main(argv: list[str]) -> int:
    hier = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Veroeffentlichungsbaum fuer oe3gas/moniscope schreiben")
    parser.add_argument("--ziel", metavar="PFAD", required=True,
                        help="Zielverzeichnis des Exports")
    parser.add_argument("--repo", metavar="PFAD",
                        default=os.path.dirname(hier),
                        help="Quell-Repo (Vorgabe: dieses Repo)")
    parser.add_argument("--leeren", action="store_true",
                        help="vorhandenes Ziel vor dem Export raeumen (.git bleibt)")
    parser.add_argument("--pruefen", action="store_true",
                        help="Selbsttests gegen den exportierten Baum laufen lassen")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.repo)
    ziel = os.path.abspath(args.ziel)
    if os.path.abspath(repo) == ziel:
        raise SystemExit("Ziel und Quelle duerfen nicht dasselbe Verzeichnis sein.")

    kopf = git(repo, "rev-parse", "--short", "HEAD").strip()
    schmutzig = git(repo, "status", "--porcelain").strip()
    print(f"[export] Quelle {repo} @ {kopf}")
    if schmutzig:
        print(f"[export] Hinweis: {len(schmutzig.splitlines())} Eintraege im "
              "Arbeitsbaum - exportiert wird HEAD, nicht der Arbeitsbaum.")

    geschrieben = exportieren(repo, ziel, args.leeren)
    print(f"[export] {len(geschrieben)} Dateien nach {ziel}")

    if args.pruefen:
        print("[export] Selbsttests gegen den exportierten Baum:")
        fehler = pruefen(ziel)
        if fehler:
            print(f"[export] {fehler} Selbsttest(s) fehlgeschlagen.")
            return 1
        print("[export] alle Selbsttests gruen.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
