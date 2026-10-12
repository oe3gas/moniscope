#!/usr/bin/env python3
"""
tools/sync_known_block.py - geteilten JS-Block "Katalog-Streifen" abgleichen.

Referenz ist waterfall.py. Zieldateien enthalten die beiden Marker-Zeilen
  // >>> KNOWN_STRIP_BLOCK_BEGIN
  // <<< KNOWN_STRIP_BLOCK_END
Alles von BEGIN bis einschliesslich der END-Zeile wird byteweise durch den
Referenzblock ersetzt; Zeilenenden werden an die Zieldatei angepasst.
Damit entfaellt das Kopieren per Editor (Escape-Sequenzen wie \\u00b7 wurden
dabei schon einmal "aufgeloest").
  python tools/sync_known_block.py ZIEL [ZIEL ...]          -> Block einsetzen/erneuern
  python tools/sync_known_block.py --check ZIEL [ZIEL ...]  -> nur vergleichen, Exit 1 bei Abweichung
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "waterfall.py")
BEGIN = b"// >>> KNOWN_STRIP_BLOCK_BEGIN"
END = b"// <<< KNOWN_STRIP_BLOCK_END"


def block_of(data: bytes):
    """(Start, Ende-exklusiv, Block) - Block inkl. END-Zeile samt Zeilenende."""
    a = data.find(BEGIN)
    b = data.find(END)
    if a < 0 or b < 0 or b < a:
        raise SystemExit("Marker BEGIN/END fehlen oder in falscher Reihenfolge")
    e = data.find(b"\n", b)
    e = len(data) if e < 0 else e + 1
    return a, e, data[a:e]


def eol_of(data: bytes) -> bytes:
    """Dominantes Zeilenende der Datei (CRLF, wenn die Mehrheit der Zeilen so endet)."""
    crlf = data.count(b"\r\n")
    return b"\r\n" if crlf * 2 > data.count(b"\n") else b"\n"


def norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def main() -> int:
    check = "--check" in sys.argv
    targets = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not targets:
        print(__doc__)
        return 2
    with open(REF, "rb") as fh:
        _, _, rblock = block_of(fh.read())
    rc = 0
    for t in targets:
        with open(t, "rb") as fh:
            data = fh.read()
        a, e, tblock = block_of(data)
        same = norm(tblock) == norm(rblock)
        if check:
            print(f"{t}: {'OK' if same else 'ABWEICHUNG'}")
            rc |= int(not same)
        elif same:
            print(f"{t}: bereits identisch")
        else:
            new = norm(rblock).replace(b"\n", eol_of(data))
            with open(t, "wb") as fh:
                fh.write(data[:a] + new + data[e:])
            print(f"{t}: Block eingesetzt")
    return rc


if __name__ == "__main__":
    sys.exit(main())
