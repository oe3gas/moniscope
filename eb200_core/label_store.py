#!/usr/bin/env python3
"""
eb200_core/label_store.py

Winziger, threadsicherer JSON-Speicher (Schluessel -> Kurztext) fuer frei
vergebene Bezeichnungen ("Kurztext") zu Session-DBs bzw. IFPAN-Aufnahmen.
Die eigentlichen Mess-Dateien (.db / .specbin + Sidecar) bleiben dadurch
UNANGETASTET und rein lesend - die Beschriftung liegt komplett getrennt in
einer eigenen JSON-Datei. Nur Python-Standardbibliothek.

Schluessel ist der (Root-relative) Pfad der Datei mit Vorwaertsschraegstrichen,
Wert der Kurztext (max. MAX_LABEL_LEN Zeichen; Steuerzeichen inkl. Zeilenumbruch
werden zu Leerzeichen normalisiert, Rand-Leerzeichen entfernt). Leerer Text
loescht den Eintrag.

Schreiben ist atomar (Temp-Datei + os.replace) und per Lock gegen die
gleichzeitig laufenden Threading-HTTP-Handler serialisiert. Eine fehlende oder
kaputte JSON-Datei fuehrt NIE zu einer Ausnahme, sondern zu einem leeren Start.

Die Pfad-Sandbox (resolve_safe_path) liegt bewusst NICHT hier, sondern im
jeweiligen Server: der Schluessel wird dort bereits validiert uebergeben.
"""

from __future__ import annotations

import json
import os
import threading

MAX_LABEL_LEN = 128
MAX_NOTE_LEN = 4000


def clean_note(text) -> str:
    """Normalisiert eine mehrzeilige Notiz: zu str zwingen, CRLF/CR zu LF,
    Steuerzeichen AUSSER Zeilenumbruch durch Leerzeichen ersetzen,
    Rand-Leerraum entfernen und auf MAX_NOTE_LEN Zeichen kuerzen."""
    s = "" if text is None else str(text)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch if (ch >= " " or ch == "\n") and ch != "\x7f" else " " for ch in s)
    return s.strip()[:MAX_NOTE_LEN]


def clean_label(text) -> str:
    """Normalisiert einen Kurztext: zu str zwingen, Steuerzeichen (inkl.
    Zeilenumbruch, Tab, DEL) durch Leerzeichen ersetzen, Rand-Leerzeichen
    entfernen und auf MAX_LABEL_LEN Zeichen kuerzen."""
    s = "" if text is None else str(text)
    s = "".join(ch if (ch >= " " and ch != "\x7f") else " " for ch in s)
    return s.strip()[:MAX_LABEL_LEN]


class LabelStore:
    """JSON-gestuetzter Kurztext-Speicher. Eine Instanz je Server (mit eigener
    Datei). Alle Methoden sind threadsicher. Ueber den cleaner-Parameter auch
    fuer mehrzeilige Notizen nutzbar (clean_note)."""

    def __init__(self, json_path: str, cleaner=clean_label) -> None:
        self._path = os.path.abspath(json_path)
        self._lock = threading.Lock()
        self._data = {}
        self._cleaner = cleaner
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            # defensiv: nur str-Schluessel mit nicht-leerem, bereinigtem Wert
            self._data = {str(k): self._cleaner(v)
                          for k, v in raw.items() if self._cleaner(v)}
        else:
            self._data = {}

    def get(self, key: str) -> str:
        with self._lock:
            return self._data.get(key, "")

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)

    def set(self, key: str, text) -> str:
        """Setzt/aktualisiert den Kurztext zu key. Leerer (bereinigter) Text
        loescht den Eintrag. Gibt den tatsaechlich gespeicherten Text zurueck
        und schreibt die JSON-Datei atomar neu."""
        cleaned = self._cleaner(text)
        with self._lock:
            if cleaned:
                self._data[str(key)] = cleaned
            else:
                self._data.pop(str(key), None)
            self._flush()
        return cleaned

    def _flush(self) -> None:
        # atomar: erst in Temp-Datei schreiben, dann per os.replace ersetzen
        # (auf demselben Verzeichnis/Dateisystem = atomarer Rename).
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, self._path)


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "labels.json")
        store = LabelStore(path)
        assert store.get("a") == "", "leerer Start erwartet"

        assert store.set("sessions/x.db", "Testlauf 40m") == "Testlauf 40m"
        assert store.get("sessions/x.db") == "Testlauf 40m"

        # Steuerzeichen -> Leerzeichen, Rand-Leerzeichen weg
        assert store.set("k", "  a\nb\tc  ") == "a b c"

        # Kuerzung auf MAX_LABEL_LEN
        assert len(store.set("k2", "x" * 200)) == MAX_LABEL_LEN

        # leeren -> loeschen
        assert store.set("k", "") == ""
        assert store.get("k") == ""

        # Persistenz ueber Neuoeffnen
        store2 = LabelStore(path)
        assert store2.get("sessions/x.db") == "Testlauf 40m"
        assert store2.get("k2") == "x" * MAX_LABEL_LEN

        # Notiz-Modus (cleaner=clean_note): eigene Datei, Zeilenumbrueche
        # bleiben erhalten (nur CR/CRLF -> LF normalisiert)
        path2 = os.path.join(tmp, "notes.json")
        note_store = LabelStore(path2, cleaner=clean_note)
        assert note_store.set("n", "a\r\nb\rc") == "a\nb\nc", "Zeilenumbrueche nicht erhalten"

        # Tab (Steuerzeichen ausser Zeilenumbruch) -> Leerzeichen
        assert note_store.set("n2", "a\tb") == "a b", "Tab nicht ersetzt"

        # Kuerzung auf MAX_NOTE_LEN
        assert len(note_store.set("n3", "x" * 5000)) == MAX_NOTE_LEN, "Kuerzung auf MAX_NOTE_LEN falsch"

        # leeren -> loeschen
        assert note_store.set("n", "") == "", "leerer Text soll loeschen"
        assert note_store.get("n") == ""

        # Persistenz ueber Neuoeffnen (cleaner erneut angeben)
        note_store2 = LabelStore(path2, cleaner=clean_note)
        assert note_store2.get("n3") == "x" * MAX_NOTE_LEN, "Notiz-Persistenz fehlgeschlagen"

        # kaputte Datei -> leerer Start, keine Ausnahme
        with open(path, "w", encoding="utf-8") as f:
            f.write("{kaputt")
        store3 = LabelStore(path)
        assert store3.all() == {}

    print("[label_store] Selbsttest OK")


if __name__ == "__main__":
    _self_test()
