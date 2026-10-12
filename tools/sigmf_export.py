#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
tools/sigmf_export.py

Kommandozeile zum SigMF-Export einer IQ-Aufnahme (T-921). Die gesamte
Logik liegt in eb200_core/sigmf_export.py - hier steht nur die
Argumentbehandlung und die Ausgabe, damit dieselbe Funktion von Hand
und spaeter aus der Aufzeichnungsliste (:8050, T-922) aufgerufen werden
kann, ohne dass es zwei Wahrheiten gibt.

Aufruf von ueberall her moeglich - das Repo-Root haengt sich das Skript
selbst in den Suchpfad (Muster tools/eye_diagram.py); der Pfad zur WAV
darf absolut oder relativ sein:

  python tools\\sigmf_export.py E:\\...\\aufnahme.wav
      -> aufnahme.sigmf-meta neben der WAV. Non-Conforming Dataset:
         verweist per core:dataset auf die WAV, kopiert kein Byte
         Nutzdaten. Fuer die eigene Weiterverarbeitung der richtige Weg.

  python tools\\sigmf_export.py E:\\...\\aufnahme.wav --data
      -> zusaetzlich aufnahme.sigmf-data. Spec-konform und damit auch
         fuer fremde Werkzeuge selbsterklaerend, kostet aber den
         doppelten Plattenplatz. Der Weg fuer Aufnahmen, die das Haus
         verlassen.

  ... --data --out-dir E:\\Export
      -> legt beide Dateien dort ab statt neben der WAV.

Nur IQ-Aufnahmen. Demoduliertes Mono-Audio wird mit einer Meldung
abgelehnt, nicht stillschweigend anders exportiert.
"""

from __future__ import annotations

import argparse
import os
import sys

# Erlaubt den Direktaufruf "python tools/sigmf_export.py" (Muster
# tools/eye_diagram.py, tools/afc_probe.py): beim Skriptaufruf steht in
# sys.path[0] das tools-Verzeichnis und NICHT das Arbeitsverzeichnis -
# ohne diesen Eintrag fehlt das Repo-Root im Suchpfad fuer den
# eb200_core-Import, gleichgueltig von wo aus aufgerufen wird.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core.sigmf_export import SigmfRunningError, write_sigmf


def _mib(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MiB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Schreibt SigMF-Metadaten zu einer IQ-Aufnahme des EB200.")
    ap.add_argument("wav", help="Pfad zur IQ-WAV (der Sidecar <wav>.json muss daneben liegen)")
    ap.add_argument("--data", action="store_true",
                    help="zusaetzlich .sigmf-data schreiben (spec-konform, "
                         "doppelter Platzbedarf)")
    ap.add_argument("--out-dir", default=None,
                    help="Zielverzeichnis fuer beide Dateien; nur zusammen mit --data")
    args = ap.parse_args(argv)

    try:
        res = write_sigmf(args.wav, with_data=args.data, out_dir=args.out_dir)
    except SigmfRunningError as exc:
        print(f"Abgelehnt: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2

    if res["ncd"]:
        print(f"Metadatei : {res['meta_path']}")
        print(f"Datensatz : {args.wav} (nicht kopiert, Versatz "
              f"{res['header_bytes']} Byte, {_mib(res['sample_bytes'])} Nutzdaten)")
        print("Hinweis   : Non-Conforming Dataset - schema-valide, aber nicht "
              "\"SigMF Compliant\". Fuer die Weitergabe --data verwenden.")
        if res.get("stale_data"):
            print(f"Achtung   : {os.path.basename(res['stale_data'])} liegt daneben - "
                  "zwei Datenquellen, SigMF-Werkzeuge warnen und waehlen "
                  "core:dataset. Loeschen oder --data verwenden.")
    else:
        print(f"Metadatei : {res['meta_path']}")
        print(f"Datendatei: {res['data_path']} ({_mib(res['sample_bytes'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
