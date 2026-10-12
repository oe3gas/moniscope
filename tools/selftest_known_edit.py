#!/usr/bin/env python3
"""
tools/selftest_known_edit.py

Prueft den Known-Frequencies-Editor (T-105) OHNE laufenden Server: eigene
Temp-Verzeichnisse, known_freq.py und block_admin.py als Modul importiert
(sys.path-Ergaenzung um eb200_core/, wie block_admin.py selbst seine
Nachbarmodule db.py/param_store.py flach importiert).

  T1  load_catalog_cached: zweimaliger Aufruf liefert dasselbe Objekt (is);
      nach Aenderung einer Datei (mtime/Inhalt) ein neues mit geaendertem
      Inhalt.
  T2  Validierungslogik (block_admin.validate_candidate): Kandidat mit
      doppelter id gegen eine zweite Datei wird abgelehnt; gueltiger
      Kandidat wird akzeptiert. block_admin.DEFAULT_CATALOG_DIR wird dafuer
      auf ein Temp-Verzeichnis umgebogen (Modul-Global, zur Laufzeit
      aufgeloest) und danach zurueckgesetzt.
  T3  Schreibdisziplin (block_admin._write_known_file): Save-Roundtrip
      erhaelt unbekannte Felder und den EOL-Stil (CRLF) der Ausgangsdatei,
      legt eine .bak-Datei an.
  T4  build_channel_entry (Vererbung/Override): keine gesetzten optionalen
      Felder -> keine entsprechenden Schluessel; eine auf "geerbt"
      zurueckgestellte Auswahl entfernt einen zuvor gesetzten Schluessel
      wieder; ein gesetzter Wert landet als Override; unbekannte Schluessel
      des existing-Eintrags bleiben erhalten.
  T5  build_raster_entry + _raster_preview: festes Raster und ICAO 8,33
      erzeugen die erwartete Kanalzahl (5 bzw. 15); Schema-Wahl entfernt
      step_hz/label_template; enabled=aus landet im Eintrag, die Vorschau
      zeigt die Kanaele trotzdem; Vererbungs-Ruecknahme entfernt den
      Override-Schluessel.
  T6  build_band_entry (Band/Segmente): gueltiges Band mit Segmenten wird
      uebernommen (title leer -> Schluessel entfaellt); leere Segmentliste
      entfernt den segments-Schluessel, unbekannte Schluessel bleiben
      erhalten; Segment ausserhalb des Bandes und stop_hz == start_hz
      werden abgelehnt (Loader-Regel: streng groesser).
  T7  build_file_head (Datei-Kopf meta/defaults): Neuanlage uebernimmt nur
      gesetzte meta-Textfelder und schreibt die Notiz als note; Bearbeitung
      behaelt den vorhandenen Notiz-Schluessel (notes bleibt notes),
      unbekannte meta-/defaults-Schluessel und die entries-Liste (Identitaet)
      bleiben erhalten, Ruecknahme eines defaults-Feldes entfernt den
      Schluessel; leerer Dienstname und ungueltiger bw_hz werden abgelehnt.
  T8  delete_known_file: leere Datei wird samt .bak/.tmp entfernt; eine
      Datei mit noch vorhandenen entries wird abgelehnt (ValueError) und
      bleibt bestehen.
  T9  CSV-Import (T-121) vom Text bis zur geschriebenen Datei, ohne Server:
      parse_catalog_csv -> build_file_head -> validate_candidate ->
      _write_known_file, danach load_catalog auf dem Temp-Verzeichnis; das
      Anhaengen an eine bestehende Datei erhaelt deren Eintraege, gleiche id
      ohne Ersetzen-Haken wird erkannt, mit Ersetzen aktualisiert der Import
      den vorhandenen Eintrag.

Ausgabe je Test OK/FAIL, Exit-Code 0 nur bei komplett OK.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# T-2000: Repo-Root statt eb200_core/ in den Suchpfad, Importe mit Paketpraefix.
# block_admin.py importiert seine Geschwistermodule als eb200_core.xxx; ein
# nackter Import haette known_freq ein zweites Mal geladen.
sys.path.insert(0, ROOT)

from eb200_core import known_freq   # noqa: E402
from eb200_core import block_admin   # noqa: E402


def _write_json(path: str, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)


def test_t1() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(os.path.join(tmp, "a.json"), {
            "version": 1, "meta": {"service": "A"},
            "entries": [{"kind": "channel", "id": "a-1", "label": "Eins",
                        "freq_hz": 100_000_000, "demod": "FM", "bw_hz": 15000}],
        })
        cat1 = known_freq.load_catalog_cached(tmp)
        cat2 = known_freq.load_catalog_cached(tmp)
        assert cat1 is cat2, "unveraenderte Signatur liefert nicht dasselbe Objekt"
        assert len(cat1.channels) == 1 and cat1.channels[0]["id"] == "a-1"

        # Datei aendern (Inhalt UND Groesse, robust gegen mtime-Aufloesung).
        time.sleep(0.02)
        _write_json(os.path.join(tmp, "a.json"), {
            "version": 1, "meta": {"service": "A"},
            "entries": [
                {"kind": "channel", "id": "a-1", "label": "Eins",
                 "freq_hz": 100_000_000, "demod": "FM", "bw_hz": 15000},
                {"kind": "channel", "id": "a-2", "label": "Zwei",
                 "freq_hz": 101_000_000, "demod": "FM", "bw_hz": 15000},
            ],
        })
        cat3 = known_freq.load_catalog_cached(tmp)
        assert cat3 is not cat1, "geaenderte Datei liefert weiterhin das alte Objekt"
        assert len(cat3.channels) == 2, "geaenderter Inhalt kommt nicht an"
        ids = {c["id"] for c in cat3.channels}
        assert ids == {"a-1", "a-2"}, ids

    print("T1 load_catalog_cached: OK")


def test_t2() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_json(os.path.join(tmp, "a.json"), {
            "version": 1, "meta": {"service": "A"},
            "entries": [{"kind": "channel", "id": "dup", "label": "A",
                        "freq_hz": 100_000_000, "demod": "FM", "bw_hz": 15000}],
        })
        original_dir = block_admin.DEFAULT_CATALOG_DIR
        block_admin.DEFAULT_CATALOG_DIR = tmp
        try:
            kandidat_dup = {
                "version": 1, "meta": {"service": "B"},
                "entries": [{"kind": "channel", "id": "dup", "label": "B",
                            "freq_hz": 200_000_000, "demod": "AM", "bw_hz": 6000}],
            }
            fehler = block_admin.validate_candidate("b.json", kandidat_dup)
            assert fehler, "doppelte id gegen zweite Datei wurde nicht abgelehnt"

            kandidat_ok = {
                "version": 1, "meta": {"service": "B"},
                "entries": [{"kind": "channel", "id": "b-1", "label": "B1",
                            "freq_hz": 200_000_000, "demod": "AM", "bw_hz": 6000}],
            }
            fehler2 = block_admin.validate_candidate("b.json", kandidat_ok)
            assert not fehler2, f"gueltiger Kandidat wurde abgelehnt: {fehler2}"
        finally:
            block_admin.DEFAULT_CATALOG_DIR = original_dir

    print("T2 Validierungslogik: OK")


def test_t3() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "roundtrip.json")
        original = {
            "version": 1,
            "meta": {"service": "Test", "custom_unknown_field": "bleibt"},
            "defaults": {"demod": "FM", "bw_hz": 15000},
            "bands": [],
            "entries": [
                {"kind": "channel", "id": "keep-me", "label": "Bleibt",
                 "freq_hz": 100_000_000, "custom_entry_field": "auch das bleibt"},
                {"kind": "channel", "id": "change-me", "label": "Alt",
                 "freq_hz": 200_000_000},
            ],
        }
        text = json.dumps(original, ensure_ascii=False, indent=2)
        with open(path, "wb") as fh:
            fh.write(text.encode("utf-8").replace(b"\n", b"\r\n"))

        doc = block_admin._load_known_dict(path)
        idx = block_admin._find_channel_entry(doc, "change-me")
        assert idx is not None, "change-me nicht gefunden"
        doc["entries"][idx] = {"kind": "channel", "id": "change-me",
                               "label": "Neu", "freq_hz": 201_000_000}
        block_admin._write_known_file(path, doc)

        with open(path, "rb") as fh:
            written = fh.read()
        crlf = written.count(b"\r\n")
        lf_only = written.count(b"\n") - crlf
        assert lf_only == 0 and crlf > 0, "EOL-Stil (CRLF) nicht erhalten"

        doc2 = json.loads(written.decode("utf-8"))
        assert doc2["meta"]["custom_unknown_field"] == "bleibt", "unbekanntes meta-Feld verloren"
        keep = next(e for e in doc2["entries"] if e["id"] == "keep-me")
        assert keep.get("custom_entry_field") == "auch das bleibt", \
            "unbekanntes Kanal-Feld verloren"
        changed = next(e for e in doc2["entries"] if e["id"] == "change-me")
        assert changed["label"] == "Neu" and changed["freq_hz"] == 201_000_000, changed

        assert os.path.isfile(path + ".bak"), ".bak wurde nicht angelegt"

    print("T3 Schreibdisziplin: OK")


def test_t4() -> None:
    # a) Neuer Kanal ohne gesetzte optionale Felder -> keine entsprechenden
    #    Schluessel im Eintrag (Vererbung aus den Datei-defaults bleibt frei).
    form_minimal = {
        "id": "t4-a", "label": "Testkanal", "freq_hz": "100000000",
        "demod": "", "bw_hz": "", "squelch_dbuv": "", "antenna": "",
        "tags": "", "notes": "", "enabled": "on",
    }
    entry = block_admin.build_channel_entry(form_minimal, existing=None)
    assert entry == {"kind": "channel", "id": "t4-a", "label": "Testkanal",
                     "freq_hz": 100_000_000}, entry

    # b) existing-Eintrag mit gesetztem demod und einem unbekannten
    #    Schluessel; Formular laesst demod leer ("geerbt") -> Schluessel
    #    wird wieder entfernt, unbekannter Schluessel bleibt erhalten.
    existing = {"kind": "channel", "id": "t4-b", "label": "Alt",
               "freq_hz": 100_000_000, "demod": "FM",
               "custom_field": "bleibt"}
    form_clear_demod = {
        "id": "t4-b", "label": "Alt", "freq_hz": "100000000",
        "demod": "", "bw_hz": "", "squelch_dbuv": "", "antenna": "",
        "tags": "", "notes": "", "enabled": "on",
    }
    entry2 = block_admin.build_channel_entry(form_clear_demod, existing)
    assert "demod" not in entry2, entry2
    assert entry2.get("custom_field") == "bleibt", "unbekannter Schluessel verloren"

    # c) ein gesetzter Wert landet als expliziter Override im Eintrag.
    form_set_demod = dict(form_clear_demod, demod="AM", bw_hz="6000")
    entry3 = block_admin.build_channel_entry(form_set_demod, existing)
    assert entry3["demod"] == "AM" and entry3["bw_hz"] == 6000, entry3
    assert entry3.get("custom_field") == "bleibt", "unbekannter Schluessel verloren"

    print("T4 build_channel_entry (Vererbung/Override): OK")


def test_t5() -> None:
    # Festes Raster: 100.000-100.100 MHz, 25 kHz -> 5 Kanaele
    form = {"id_prefix": "t5", "start_hz": "100000000", "stop_hz": "100100000",
            "step_scheme": "", "step_hz": "25000", "label_template": "{khz:.0f}",
            "enabled": "on", "demod": "FM", "bw_hz": "15000",
            "squelch_dbuv": "", "antenna": "", "tags": "", "notes": ""}
    entry = block_admin.build_raster_entry(form)
    assert entry["kind"] == "raster" and entry["step_hz"] == 25000
    assert "step_scheme" not in entry and "squelch_dbuv" not in entry
    assert "enabled" not in entry
    doc = {"version": 1, "meta": {"service": "T5"}, "entries": [entry]}
    n, zeilen, fehler = block_admin._raster_preview("t5.json", doc, "t5")
    assert n == 5, f"erwartet 5 Kanaele, bekommen {n}"
    assert not fehler, f"unerwartete Raster-Fehler: {fehler}"
    assert zeilen and zeilen[0].startswith("100000"), zeilen

    # ICAO 8,33: 118.000-118.100 MHz -> 5 Basen x 3 = 15 Kanaele;
    # step_hz/label_template werden trotz gesetzter Formularfelder entfernt
    form_air = dict(form, id_prefix="t5a", start_hz="118000000",
                    stop_hz="118100000", step_scheme="airband_8k33")
    entry_air = block_admin.build_raster_entry(form_air)
    assert entry_air["step_scheme"] == "airband_8k33"
    assert "step_hz" not in entry_air and "label_template" not in entry_air
    doc_air = {"version": 1, "meta": {"service": "T5"}, "entries": [entry_air]}
    n_air, _, fehler_air = block_admin._raster_preview("t5a.json", doc_air, "t5a")
    assert n_air == 15, f"erwartet 15 Kanaele, bekommen {n_air}"
    assert not fehler_air, f"unerwartete Raster-Fehler: {fehler_air}"

    # enabled aus: Eintrag traegt enabled=False, Vorschau zeigt trotzdem Kanaele
    form_off = dict(form, enabled="")
    entry_off = block_admin.build_raster_entry(form_off)
    assert entry_off["enabled"] is False
    doc_off = {"version": 1, "meta": {"service": "T5"}, "entries": [entry_off]}
    n_off, _, _ = block_admin._raster_preview("t5.json", doc_off, "t5")
    assert n_off == 5, "Vorschau muss auch bei enabled=aus die Kanaele zeigen"

    # Vererbung: bw-Override zuruecknehmen entfernt den Schluessel
    form2 = dict(form, bw_hz="")
    entry2 = block_admin.build_raster_entry(form2, entry)
    assert "bw_hz" not in entry2 and entry2["demod"] == "FM"
    print("T5 Raster-Editor: OK")


def test_t6() -> None:
    segs = json.dumps([
        {"title": "SSB", "start_hz": 14100000, "stop_hz": 14350000},
        {"title": "", "start_hz": 14000000, "stop_hz": 14100000},
    ])
    form = {"id": "20m", "title": "", "start_hz": "14000000",
            "stop_hz": "14350000", "segments_json": segs}
    band = block_admin.build_band_entry(form)
    assert band["id"] == "20m" and "title" not in band
    assert [s["start_hz"] for s in band["segments"]] == [14100000, 14000000]

    # Titel gesetzt, leere Segmentliste -> segments-Schluessel entfaellt,
    # unbekannte Schluessel bleiben erhalten
    form2 = dict(form, title="20-m-Band", segments_json="[]")
    band2 = block_admin.build_band_entry(form2, dict(band, custom="bleibt"))
    assert band2["title"] == "20-m-Band"
    assert "segments" not in band2
    assert band2.get("custom") == "bleibt", "unbekannter Schluessel verloren"

    # Segment ausserhalb des Bandes -> abgelehnt
    seg_bad = json.dumps([{"start_hz": 13900000, "stop_hz": 14100000}])
    try:
        block_admin.build_band_entry(dict(form, segments_json=seg_bad))
        raise AssertionError("Segment ausserhalb des Bandes nicht abgelehnt")
    except ValueError:
        pass

    # stop_hz == start_hz -> abgelehnt (Loader-Regel: streng groesser)
    try:
        block_admin.build_band_entry(dict(form, stop_hz="14000000"))
        raise AssertionError("stop_hz == start_hz nicht abgelehnt")
    except ValueError:
        pass
    print("T6 build_band_entry (Band/Segmente): OK")


def test_t7() -> None:
    # Neuanlage: nur gesetzte meta-Textfelder, Notiz -> note, defaults aus
    # den vererbbaren Formularfeldern.
    form = {"service": "Test", "source": "Q", "meta_note": "n",
            "demod": "FM", "bw_hz": "15000", "tags": "a, b"}
    doc = block_admin.build_file_head(form)
    assert doc["meta"] == {"service": "Test", "source": "Q", "note": "n"}, doc["meta"]
    assert doc["defaults"] == {"demod": "FM", "bw_hz": 15000, "tags": ["a", "b"]}, doc["defaults"]
    assert doc["bands"] == [] and doc["entries"] == []

    # Bearbeitung: vorhandener Notiz-Schluessel (notes) bleibt notes,
    # unbekannte Schluessel und die entries-Liste (Identitaet) bleiben
    # erhalten, Ruecknahme eines defaults-Feldes entfernt den Schluessel.
    existing = {
        "meta": {"service": "x", "notes": "alt", "custom": 1},
        "defaults": {"antenna": "discone", "custom": 2},
        "bands": [],
        "entries": [{"kind": "channel"}],
    }
    form2 = {"service": "x", "meta_note": "neu", "antenna": ""}
    doc2 = block_admin.build_file_head(form2, existing=existing)
    assert doc2["meta"]["notes"] == "neu", doc2["meta"]
    assert "note" not in doc2["meta"]
    assert doc2["meta"]["custom"] == 1
    assert "antenna" not in doc2["defaults"]
    assert doc2["defaults"]["custom"] == 2
    assert doc2["entries"] is existing["entries"]

    try:
        block_admin.build_file_head({"service": "", "meta_note": ""})
        raise AssertionError("leerer Dienstname nicht abgelehnt")
    except ValueError:
        pass

    try:
        block_admin.build_file_head({"service": "x", "bw_hz": "1234"})
        raise AssertionError("ungueltiger bw_hz nicht abgelehnt")
    except ValueError:
        pass
    print("T7 build_file_head (Datei-Kopf meta/defaults): OK")


def test_t8() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skeleton = {"version": 1, "meta": {"service": "T8"}, "defaults": {},
                    "bands": [], "entries": []}
        path = os.path.join(tmp, "t8.json")
        _write_json(path, skeleton)
        with open(path + ".bak", "w", encoding="utf-8") as fh:
            fh.write("x")
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write("x")
        block_admin.delete_known_file(path)
        assert not os.path.exists(path)
        assert not os.path.exists(path + ".bak")
        assert not os.path.exists(path + ".tmp")

        doc_mit_eintrag = {"version": 1, "meta": {"service": "T8"}, "defaults": {},
                            "bands": [], "entries": [{"kind": "channel"}]}
        path2 = os.path.join(tmp, "t8b.json")
        _write_json(path2, doc_mit_eintrag)
        try:
            block_admin.delete_known_file(path2)
            raise AssertionError("Datei mit entries nicht abgelehnt")
        except ValueError:
            pass
        assert os.path.isfile(path2)
    print("T8 delete_known_file: OK")


def test_t9() -> None:
    csv_text = (
        "typ;id;label;freq_hz;start_hz;stop_hz;title;demod;bw_hz\r\n"
        "channel;t9-a;A;118500000;;;;;\r\n"
        "band;t9-b;;;118000000;137000000;T9-Band;;\r\n"
    )
    entries, bands, fehler = block_admin.parse_catalog_csv(csv_text)
    assert not fehler, fehler
    assert len(entries) == 1 and len(bands) == 1, (entries, bands)

    with tempfile.TemporaryDirectory() as tmp:
        original_dir = block_admin.DEFAULT_CATALOG_DIR
        block_admin.DEFAULT_CATALOG_DIR = tmp
        try:
            # Neue Datei: defaults aus dem Formular, Eintraege aus der CSV
            doc = block_admin.build_file_head({
                "service": "T9", "demod": "AM", "bw_hz": "9000",
            })
            doc["entries"].extend(entries)
            doc["bands"].extend(bands)
            assert not block_admin.validate_candidate("t9.json", doc)
            pfad = os.path.join(tmp, "t9.json")
            block_admin._write_known_file(pfad, doc)

            cat = known_freq.load_catalog(tmp)
            assert not cat.errors, cat.errors
            kanal = cat.by_id("t9-a")
            assert kanal is not None and kanal["freq_hz"] == 118_500_000, kanal
            assert kanal["demod"] == "AM" and kanal["bw_hz"] == 9000, kanal
            assert [b["id"] for b in cat.bands] == ["t9-b"], cat.bands

            # Anhaengen: bestehende Eintraege bleiben, neue kommen dazu
            csv2 = ("typ;id;label;freq_hz\r\n"
                    "channel;t9-c;C;119000000\r\n")
            entries2, bands2, fehler2 = block_admin.parse_catalog_csv(csv2)
            assert not fehler2 and not bands2, (fehler2, bands2)
            doc2 = block_admin._load_known_dict(pfad)
            assert block_admin._find_channel_entry(doc2, "t9-a") is not None
            doc2["entries"].extend(entries2)
            assert not block_admin.validate_candidate("t9.json", doc2)
            block_admin._write_known_file(pfad, doc2)
            cat2 = known_freq.load_catalog(tmp)
            assert {c["id"] for c in cat2.channels} == {"t9-a", "t9-c"}, cat2.channels
            assert os.path.isfile(pfad + ".bak"), "kein .bak beim Anhaengen"

            # Gleiche id: ohne Ersetzen erkennbar, mit Ersetzen aktualisiert
            csv3 = ("typ;id;label;freq_hz\r\n"
                    "channel;t9-a;A neu;118600000\r\n")
            entries3, _b3, fehler3 = block_admin.parse_catalog_csv(csv3)
            assert not fehler3, fehler3
            doc3 = block_admin._load_known_dict(pfad)
            idx = block_admin._find_channel_entry(doc3, entries3[0]["id"])
            assert idx is not None, "vorhandene id nicht erkannt"
            doc3["entries"][idx] = entries3[0]
            assert not block_admin.validate_candidate("t9.json", doc3)
            block_admin._write_known_file(pfad, doc3)
            cat3 = known_freq.load_catalog(tmp)
            assert cat3.by_id("t9-a")["freq_hz"] == 118_600_000, cat3.by_id("t9-a")
            assert len(cat3.channels) == 2, cat3.channels
        finally:
            block_admin.DEFAULT_CATALOG_DIR = original_dir

    print("T9 CSV-Import (Zerlegung bis geschriebene Datei): OK")


def main() -> int:
    tests = [test_t1, test_t2, test_t3, test_t4, test_t5, test_t6, test_t7,
             test_t8, test_t9]
    ok = True
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            print(f"{t.__name__} FAIL: {exc}")
            ok = False
        except Exception as exc:
            print(f"{t.__name__} FAIL (Ausnahme): {exc!r}")
            ok = False
    if ok:
        print("Selbsttest OK")
        return 0
    print("Selbsttest FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
