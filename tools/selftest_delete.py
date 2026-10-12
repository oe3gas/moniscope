#!/usr/bin/env python3
"""
tools/selftest_delete.py

Prueft das Loeschen ganzer Sessions/Aufnahmen (T-804) OHNE laufenden Server:
analyze_sessions.py und analyze_spectrum.py werden als Modul importiert
(sys.path-Ergaenzung um die Repo-Wurzel, damit "from eb200_core..." darin
aufgeht - Muster analog tools/selftest_known_edit.py). LABELS/NOTES und
SESSION_ROOT/SPECTRUM_ROOT werden je Test auf ein frisches Temp-Verzeichnis
umgebogen (Modul-Globals, zur Laufzeit aufgeloest - wie
block_admin.DEFAULT_CATALOG_DIR in selftest_known_edit.py): delete_session()/
delete_recording() greifen darueber lesend/schreibend auf den Kurztext-/
Notiz-Speicher zu, ohne die echten *_labels.json/*_notes.json im Repo
anzufassen.

  T1  delete_session: Dummy-.db + -wal (alte mtime) + .befund.json + Label/
      Notiz-Eintrag -> alle Dateien weg, LabelStore-Eintraege leer.
  T2  delete_session Live-Schutz: -wal mit frischer mtime -> SessionLockedError,
      keine Datei angetastet.
  T3  delete_recording: .specbin + .specbin.json (kein running) +
      .befund.json + Label/Notiz-Eintrag -> alle Dateien weg, LabelStore-
      Eintraege leer.
  T4  delete_recording Schutz: running:true -> RecordingLockedError; fehlender
      Sidecar -> RecordingLockedError. Keine Datei angetastet.
  T5  delete_recording Long-Run-Schutz: .specbin in longrun_test/ mit
      job.json daneben -> RecordingIsLongRunPartError. Keine Datei angetastet.

Ausgabe je Test OK/FAIL, Exit-Code 0 nur bei komplett OK.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analyze_sessions as az_sess   # noqa: E402
import analyze_spectrum as az_spec   # noqa: E402
from eb200_core.label_store import LabelStore, clean_note   # noqa: E402


def _rebind_session_stores(tmp: str) -> None:
    az_sess.SESSION_ROOT = tmp
    az_sess.LABELS = LabelStore(os.path.join(tmp, "session_labels.json"))
    az_sess.NOTES = LabelStore(os.path.join(tmp, "session_notes.json"), cleaner=clean_note)


def _rebind_spectrum_stores(tmp: str) -> None:
    az_spec.SPECTRUM_ROOT = tmp
    az_spec.LABELS = LabelStore(os.path.join(tmp, "spectrum_labels.json"))
    az_spec.NOTES = LabelStore(os.path.join(tmp, "spectrum_notes.json"), cleaner=clean_note)


def test_t1() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _rebind_session_stores(tmp)
        db_path = os.path.join(tmp, "s1.db")
        wal_path = db_path + "-wal"
        befund_path = db_path[:-len(".db")] + ".befund.json"
        with open(db_path, "wb") as fh:
            fh.write(b"dummy-db")
        with open(wal_path, "wb") as fh:
            fh.write(b"dummy-wal")
        with open(befund_path, "w", encoding="utf-8") as fh:
            json.dump({"analysis_version": 2}, fh)
        old = time.time() - 3600
        os.utime(wal_path, (old, old))   # nicht "live" (mtime > 10s alt)

        rel = az_sess.to_relative(db_path)
        az_sess.LABELS.set(rel, "Testlauf")
        az_sess.NOTES.set(rel, "Notiz")
        assert az_sess.LABELS.get(rel) == "Testlauf"

        result = az_sess.delete_session(db_path)
        assert set(result["deleted"]) == {"s1.db", "s1.db-wal", "s1.befund.json"}, result
        assert not os.path.isfile(db_path), "db nicht geloescht"
        assert not os.path.isfile(wal_path), "-wal nicht geloescht"
        assert not os.path.isfile(befund_path), "Befund nicht geloescht"
        assert az_sess.LABELS.get(rel) == "", "Kurztext nicht geleert"
        assert az_sess.NOTES.get(rel) == "", "Notiz nicht geleert"

    print("T1 delete_session: OK")


def test_t2() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _rebind_session_stores(tmp)
        db_path = os.path.join(tmp, "s2.db")
        wal_path = db_path + "-wal"
        with open(db_path, "wb") as fh:
            fh.write(b"dummy-db")
        with open(wal_path, "wb") as fh:
            fh.write(b"dummy-wal")   # frische mtime -> "live"

        try:
            az_sess.delete_session(db_path)
            raised = False
        except az_sess.SessionLockedError:
            raised = True
        assert raised, "laufende Session (frische -wal) wurde nicht verweigert"
        assert os.path.isfile(db_path), "db trotz Schutzregel geloescht"
        assert os.path.isfile(wal_path), "-wal trotz Schutzregel geloescht"

    print("T2 delete_session Live-Schutz: OK")


def test_t3() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _rebind_spectrum_stores(tmp)
        rec_path = os.path.join(tmp, "r1.specbin")
        sidecar_path = rec_path + ".json"
        befund_path = rec_path[:-len(".specbin")] + ".befund.json"
        with open(rec_path, "wb") as fh:
            fh.write(b"dummy-specbin")
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            json.dump({"format": "eb200-ifpan-bins-v1"}, fh)   # kein "running"
        with open(befund_path, "w", encoding="utf-8") as fh:
            json.dump({"analysis_version": 2}, fh)

        rel = az_spec.to_relative(rec_path)
        az_spec.LABELS.set(rel, "Testaufnahme")
        az_spec.NOTES.set(rel, "Notiz")

        result = az_spec.delete_recording(rec_path)
        assert set(result["deleted"]) == {"r1.specbin", "r1.specbin.json", "r1.befund.json"}, result
        assert not os.path.isfile(rec_path), "specbin nicht geloescht"
        assert not os.path.isfile(sidecar_path), "Sidecar nicht geloescht"
        assert not os.path.isfile(befund_path), "Befund nicht geloescht"
        assert az_spec.LABELS.get(rel) == "", "Kurztext nicht geleert"
        assert az_spec.NOTES.get(rel) == "", "Notiz nicht geleert"

    print("T3 delete_recording: OK")


def test_t4() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _rebind_spectrum_stores(tmp)

        # a) running:true im Sidecar
        rec_a = os.path.join(tmp, "running.specbin")
        with open(rec_a, "wb") as fh:
            fh.write(b"dummy")
        with open(rec_a + ".json", "w", encoding="utf-8") as fh:
            json.dump({"format": "eb200-ifpan-bins-v1", "running": True}, fh)
        try:
            az_spec.delete_recording(rec_a)
            raised_a = False
        except az_spec.RecordingLockedError:
            raised_a = True
        assert raised_a, "running:true wurde nicht verweigert"
        assert os.path.isfile(rec_a), "Datei trotz Schutzregel geloescht (running)"

        # b) fehlender Sidecar
        rec_b = os.path.join(tmp, "nosidecar.specbin")
        with open(rec_b, "wb") as fh:
            fh.write(b"dummy")
        try:
            az_spec.delete_recording(rec_b)
            raised_b = False
        except az_spec.RecordingLockedError:
            raised_b = True
        assert raised_b, "fehlender Sidecar wurde nicht verweigert"
        assert os.path.isfile(rec_b), "Datei trotz Schutzregel geloescht (kein Sidecar)"

    print("T4 delete_recording Schutz (running/fehlender Sidecar): OK")


def test_t5() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _rebind_spectrum_stores(tmp)
        longrun_dir = os.path.join(tmp, "longrun_test")
        os.makedirs(longrun_dir, exist_ok=True)
        rec_path = os.path.join(longrun_dir, "teil01.specbin")
        with open(rec_path, "wb") as fh:
            fh.write(b"dummy")
        with open(rec_path + ".json", "w", encoding="utf-8") as fh:
            json.dump({"format": "eb200-ifpan-bins-v1"}, fh)
        with open(os.path.join(longrun_dir, "job.json"), "w", encoding="utf-8") as fh:
            json.dump({"job_id": "job-1"}, fh)

        try:
            az_spec.delete_recording(rec_path)
            raised = False
        except az_spec.RecordingIsLongRunPartError:
            raised = True
        assert raised, "Long-Run-Teildatei wurde nicht verweigert"
        assert os.path.isfile(rec_path), "Datei trotz Schutzregel geloescht (Long-Run)"

    print("T5 delete_recording Long-Run-Schutz: OK")


def main() -> int:
    tests = [test_t1, test_t2, test_t3, test_t4, test_t5]
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
