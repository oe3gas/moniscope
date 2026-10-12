#!/usr/bin/env python3
"""
eb200_core/mscan_view.py

Ergebnisansicht EINES MSCAN-Laufs (PLAN.md T-1006/T-1106a) - reine,
handler-unabhaengige Funktionen (Muster manage_jobs.py: build_*/handle_*),
NUR Python-Standardbibliothek. Seit T-1106a Wohnsitz von
build_mscan_run_result()/resolve_run_file(), die urspruenglich (T-1006) in
manage_jobs.py lebten - Konsument ist jetzt analyze_mscan.py (:8060), das
diese Funktionen ueber /api/run bzw. /api/wav aufruft. manage_jobs.py
(:8050) braucht sie nicht mehr (dort bleiben nur die MSCAN-Definitionsseiten
und die Job-Verwaltung).

T-1106a NEU in build_mscan_run_result(): jede recordings[]-Zeile traegt
zusaetzlich "spektrum_path" fuer den "Spektrum"-Link je Aufnahme auf
:8070 (analyze_audio.py) - siehe dort. Nur gesetzt, wenn das Job-
Verzeichnis real unterhalb des Repo-Roots liegt (:8070 sandboxed bislang
nur den Repo-Root, siehe analyze_audio.AUDIO_ROOT - eine Ablage
ausserhalb ist erst mit T-201 auswertbar), sonst None.
"""

from __future__ import annotations

import os

from eb200_core import paths
from eb200_core.job_store import JobStore


def build_mscan_run_result(store: JobStore, run_id: str) -> dict:
    """Fuer GET /api/mscan/run?id=<run_id> (T-1006): Ergebnisansicht EINES
    MSCAN-Laufs - Kopfdaten, aktive Kanaele (angereichert um demod/bw_hz/
    service/file aus dem eingefrorenen Kanal-Snapshot, siehe
    mscan_run.build_run_params()) und stille Kanaele (im Snapshot, aber
    ohne Treffer). Wirft KeyError bei unbekannter run_id (store.get()),
    ValueError wenn run_id kein MSCAN-Lauf ist oder die Ablage gerade
    nicht erreichbar ist (manifest_unreachable) - der Handler uebersetzt
    das in 404 bzw. 400.

    T-1019d: Ein Lauf der Pegel-Inventur (manifest["results"]["kind"] ==
    "level", siehe eb200_core/mscan_run.MscanLevelInventory) hat KEINE
    Aufnahmen und fuehrt ALLE Kanaele im Ergebnis, aktive wie stille. Damit
    beide Laufarten dieselbe Form bekommen, werden sie hier aufgeteilt:
    aktive Kanaele nach "channels", stille nach "silent_channels". Die
    Pegel-Felder (hits, cycles_active, per_cycle, level_max_dbuv,
    below_threshold) bleiben an der jeweiligen Kanalzeile. run["kind"]
    traegt die Unterscheidung ins Frontend; bei einem Tonlauf ist sie
    None."""
    manifest = store.get(run_id)
    if manifest.get("type") != "mscan":
        raise ValueError(f"run {run_id} is not an MSCAN run")
    if manifest.get("manifest_unreachable"):
        raise ValueError(f"storage location of run {run_id} not reachable")

    params = manifest.get("params") or {}
    snapshot = params.get("channels") or []
    results = manifest.get("results") or {}
    stats = manifest.get("stats") or {}
    snapshot_by_id = {ch.get("id"): ch for ch in snapshot if ch.get("id") is not None}

    # T-1106a: Repo-Root-relativer Pfad fuer den :8070-Link (job_dir_rel
    # bleibt None, wenn job_dir nicht real unterhalb des Repo-Roots liegt -
    # Vergleich wie in resolve_run_file(), nur gegen den Repo-Root statt
    # gegen store.storage_roots()).
    job_dir = manifest.get("job_dir")
    job_dir_rel = None
    if job_dir:
        repo_root = paths.DATA_ROOT
        job_dir_real = os.path.realpath(job_dir)
        repo_root_real = os.path.realpath(repo_root)
        cmp_job = job_dir_real.lower() if os.name == "nt" else job_dir_real
        cmp_root = repo_root_real.lower() if os.name == "nt" else repo_root_real
        try:
            under_repo_root = os.path.commonpath([cmp_job, cmp_root]) == cmp_root
        except ValueError:
            under_repo_root = False
        if under_repo_root:
            job_dir_rel = os.path.relpath(job_dir, repo_root).replace(os.sep, "/")

    def _spektrum_path(file_name):
        if job_dir_rel is None or not file_name:
            return None  # ausserhalb Repo-Root: :8070-Sandbox erst nach T-201
        return job_dir_rel + "/" + file_name

    # T-1019d: siehe Moduldoku - bei kind "level" stehen die stillen Kanaele
    # schon IM Ergebnis und werden hier herausgetrennt, statt sie wie beim
    # Tonlauf aus den im Ergebnis FEHLENDEN Snapshot-IDs zu erschliessen.
    kind = results.get("kind")
    result_channels = []
    seen_ids = set()
    level_silent = []
    for ch in results.get("channels") or []:
        cid = ch.get("channel_id")
        snap = snapshot_by_id.get(cid)
        if kind == "level" and not ch.get("active"):
            level_silent.append({
                "mem": ch.get("mem"), "id": cid, "label": ch.get("label"),
                "freq_hz": ch.get("freq_hz"),
                "demod": snap.get("demod") if snap else None,
                "bw_hz": snap.get("bw_hz") if snap else None,
                "file": snap.get("file") if snap else None,
                # Anders als beim Tonlauf ist "still" hier eine gemessene
                # Aussage: below_threshold zaehlt die Oeffnungen, die nur an
                # der Schwelle gescheitert sind (0 = wirklich nichts gehoert).
                "below_threshold": ch.get("below_threshold", 0),
            })
            continue
        seen_ids.add(cid)
        entry = dict(ch)
        entry["demod"] = snap.get("demod") if snap else None
        entry["bw_hz"] = snap.get("bw_hz") if snap else None
        entry["service"] = snap.get("service") if snap else None
        entry["file"] = snap.get("file") if snap else None
        entry["recordings"] = [
            dict(rec, spektrum_path=_spektrum_path(rec.get("file")))
            for rec in ch.get("recordings") or []
        ]
        result_channels.append(entry)

    if kind == "level":
        silent_channels = level_silent
    else:
        silent_channels = [
            {"mem": ch.get("mem"), "id": ch.get("id"), "label": ch.get("label"),
             "freq_hz": ch.get("freq_hz"), "demod": ch.get("demod"), "bw_hz": ch.get("bw_hz"),
             "file": ch.get("file")}
            for ch in snapshot if ch.get("id") not in seen_ids
        ]
    silent_channels.sort(key=lambda c: (c.get("mem") is None, c.get("mem")))

    run_params = {
        "mscan_job_id": params.get("mscan_job_id"), "title": params.get("title"),
        "mode": params.get("mode"), "dwell_s": params.get("dwell_s"),
        "hold_s": params.get("hold_s"), "min_recording_s": params.get("min_recording_s"),
        "squelch_from_memory": params.get("squelch_from_memory"),
        # T-1019d: Ohne diese drei Werte kann die Ansicht nicht sagen, WIE
        # erhoben wurde - ein Durchgang oder fortlaufend, mit Ton oder nur
        # Pegel, und ab welcher Schwelle ein Kanal als aktiv galt. Bei
        # Laeufen von vor T-1019 sind sie None; das Frontend blendet sie dann
        # aus (Muster audio_resumed, T-1014b).
        "stop_after": params.get("stop_after"),
        "record_audio": params.get("record_audio"),
        "level_active_dbuv": params.get("level_active_dbuv"),
    }
    run = {
        "run_id": run_id, "state": manifest.get("state"),
        "start_iso": manifest.get("start_iso"), "end_iso": manifest.get("end_iso"),
        "ended_iso": manifest.get("ended_iso"), "abort_reason": manifest.get("abort_reason"),
        "abort_code": manifest.get("abort_code"), "abort_detail": manifest.get("abort_detail"),
        "job_dir": manifest.get("job_dir"), "label": manifest.get("label") or "",
        "params": run_params, "stats": stats,
        "cycles": results.get("cycles", stats.get("cycles", 0)),
        "audio_orphan": results.get("audio_orphan"),
        "audio_orphan_pre": results.get("audio_orphan_pre"),
        "audio_orphan_mid": results.get("audio_orphan_mid"),
        "audio_orphan_post": results.get("audio_orphan_post"),
        # T-1014b: Zahl der Segment-Fortsetzungen (T-1014a). Der Segmenter
        # schreibt sie seit T-1014a ins Manifest, hier fehlte nur die
        # Durchreichung an die Ergebnisansicht. Bei Laeufen von vor T-1014a
        # None - das Frontend blendet den Wert dann aus.
        "audio_resumed": results.get("audio_resumed"),
        # T-1019d: kind unterscheidet die Pegel-Inventur ("level") vom
        # Tonlauf (None) - daran waehlt das Frontend seine Darstellung. Die
        # drei Zaehler daneben ergeben nur bei "level" einen Wert.
        "kind": results.get("kind"),
        # T-1019f Stufe 3a: Zeitachse der Durchgaenge (Sekunden seit
        # Laufbeginn je abgeschlossenem Durchgang). None bei Laeufen, die sie
        # noch nicht mitgeschrieben haben - das Frontend naehert dann ueber
        # die Laufdauer.
        "cycle_ends_s": results.get("cycle_ends_s"),
        # T-1019h: Programmierdauer der Speicherplaetze (Sekunden). Bei
        # Laeufen, die sie nicht mitgeschrieben haben, None.
        "programming_s": results.get("programming_s"),
        "items": results.get("items"),
        "below_threshold": results.get("below_threshold"),
        "level_active_dbuv": results.get("level_active_dbuv"),
        "channel_count": len(snapshot),
    }
    return {
        "run": run, "channels": result_channels, "silent_channels": silent_channels,
        "files": manifest.get("files") or [],
    }


def resolve_run_file(store: JobStore, run_id: str, name: str) -> str:
    """Lese-Sandbox fuer /api/mscan/wav (T-1006): anders als
    resolve_safe_path() (analyze_sessions.py, reiner Wurzelvergleich) hier
    zusaetzlich eine WHITELIST - name muss in der Datei- oder
    Aufnahmeliste DES MANIFESTS stehen; eine auf der Platte vorhandene,
    aber nicht gelistete Datei wird abgelehnt, selbst wenn sie im
    Job-Verzeichnis liegt. Wirft ValueError bei jedem Regelverstoss (der
    Handler uebersetzt das IMMER generisch in 403, ohne name zu
    wiederholen), KeyError bei unbekannter run_id (store.get()),
    FileNotFoundError wenn die Datei trotz Whitelist-Eintrag fehlt."""
    if (not name or name != os.path.basename(name) or "/" in name or "\\" in name
            or ".." in name or not name.lower().endswith(".wav")):
        raise ValueError("invalid file name")

    manifest = store.get(run_id)
    if manifest.get("type") != "mscan" or manifest.get("manifest_unreachable"):
        raise ValueError(f"run {run_id} not accessible")

    results = manifest.get("results") or {}
    listed = set(manifest.get("files") or [])
    for ch in results.get("channels") or []:
        for rec in ch.get("recordings") or []:
            if rec.get("file"):
                listed.add(rec["file"])
    if name not in listed:
        raise ValueError("file not listed in the manifest")

    job_dir = os.path.realpath(manifest["job_dir"])
    job_dir_cmp = job_dir.lower() if os.name == "nt" else job_dir
    under_root = False
    for root in store.storage_roots():
        root_cmp = root.lower() if os.name == "nt" else root
        try:
            if os.path.commonpath([job_dir_cmp, root_cmp]) == root_cmp:
                under_root = True
                break
        except ValueError:
            continue
    if not under_root or not os.path.basename(job_dir).startswith("longrun_"):
        raise ValueError("job directory outside the storage locations")

    path = os.path.realpath(os.path.join(job_dir, name))
    path_cmp = path.lower() if os.name == "nt" else path
    if os.path.commonpath([job_dir_cmp, path_cmp]) != job_dir_cmp:
        raise ValueError("path outside the job directory")

    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return path


# ---------------------------------------------------------------------------
# Selbsttest - "python -m eb200_core.mscan_view --selftest", ohne Server/Geraet
# ---------------------------------------------------------------------------

def run_selftest() -> None:
    import shutil
    import tempfile

    print("Selbsttest (mscan_view) ...")

    with tempfile.TemporaryDirectory() as tmp:
        index_path = os.path.join(tmp, "jobs", "index.json")
        records_dir = os.path.join(tmp, "records")   # bewusst AUSSERHALB des Repo-Roots
        os.makedirs(records_dir, exist_ok=True)

        repo_root = paths.DATA_ROOT
        # Zweite Ablage-Wurzel REAL unterhalb des Repo-Roots, nur fuer den
        # spektrum_path-Fall "gesetzt" (V3) - danach wieder entfernt.
        under_root_dir = tempfile.mkdtemp(dir=repo_root, prefix="_mscan_view_selftest_")
        try:
            roots_path = os.path.join(tmp, "storage_roots.json")
            with open(roots_path, "w", encoding="utf-8") as f:
                import json
                json.dump([records_dir, under_root_dir], f)
            store = JobStore(index_path=index_path, storage_roots_path=roots_path)

            channels_snapshot = [
                {"mem": 0, "id": "T1", "label": "Test T1", "freq_hz": 100_000_000,
                 "demod": "FM", "bw_hz": 15000, "service": None, "file": None},
                {"mem": 1, "id": "T2", "label": "Test T2", "freq_hz": 101_000_000,
                 "demod": "FM", "bw_hz": 15000, "service": None, "file": None},
            ]

            # --- Fremd-Job (kein MSCAN) fuer den ValueError-Fall ---------------
            other_job = store.create_job(
                type="dscan", label="Kein MSCAN",
                params={"start_hz": 88_000_000, "stop_hz": 90_000_000, "speed": "NORM"},
                start_iso="2020-01-01T10:00:00+01:00", end_iso="2020-01-01T11:00:00+01:00",
                decimation_s=5,
            )

            # --- MSCAN-Lauf mit Ergebnissen (job_dir per tmp AUSSERHALB) --------
            run_manifest = store.create_job(
                type="mscan", label="V-Test",
                params={"mscan_job_id": "DEF1", "title": "V-Test", "mode": "ueberwachung",
                        "dwell_s": None, "hold_s": None, "min_recording_s": None,
                        "squelch_from_memory": None, "channels": channels_snapshot},
                start_iso="2096-01-01T10:00:00+01:00", end_iso="2096-01-01T10:15:00+01:00",
            )
            run_id = run_manifest["job_id"]
            job_dir = store.get(run_id)["job_dir"]
            with open(os.path.join(job_dir, "a.wav"), "wb") as f:
                f.write(b"\x00" * 44)
            with open(os.path.join(job_dir, "fremd.wav"), "wb") as f:
                f.write(b"\x00" * 44)
            # b.wav bewusst NICHT anlegen (gelistet, aber fehlt).

            store.heartbeat(run_id, stats={"cycles": 3, "recordings": 2, "active_channels": 1},
                             files=["a.wav"],
                             results={
                                 "channels": [{
                                     "channel_id": "T1", "label": "Test T1", "freq_hz": 100_000_000,
                                     "mem": 0, "hits": 2, "total_s": 7.5, "level_max_dbuv": 41.0,
                                     "recordings": [
                                         {"file": "a.wav", "start_at": "2096-01-01T10:00:05+01:00",
                                          "duration_s": 3.5, "level_max_dbuv": 40.0, "cycle": 1,
                                          "device_cycle": 1, "bytes": 44},
                                         {"file": "b.wav", "start_at": "2096-01-01T10:00:20+01:00",
                                          "duration_s": 4.0, "level_max_dbuv": 41.0, "cycle": 2,
                                          "device_cycle": 2, "bytes": 88},
                                     ],
                                 }],
                                 "cycles": 3,
                                 "audio_orphan": 23, "audio_orphan_pre": 23,
                                 "audio_orphan_mid": 0, "audio_orphan_post": 0,
                                 "audio_resumed": 4,
                             })

            # --- V1: build_mscan_run_result -------------------------------------
            res = build_mscan_run_result(store, run_id)
            assert res["run"]["run_id"] == run_id, res["run"]
            assert res["run"]["params"]["mode"] == "ueberwachung", res["run"]["params"]
            assert res["run"]["cycles"] == 3, res["run"]
            assert res["run"]["audio_orphan_pre"] == 23, res["run"]
            assert res["run"]["audio_resumed"] == 4, res["run"]
            assert res["run"]["channel_count"] == 2, res["run"]
            assert len(res["channels"]) == 1, res["channels"]
            assert res["channels"][0]["demod"] == "FM", res["channels"][0]
            assert res["channels"][0]["bw_hz"] == 15000, res["channels"][0]
            assert len(res["channels"][0]["recordings"]) == 2, res["channels"][0]
            assert [c["id"] for c in res["silent_channels"]] == ["T2"], res["silent_channels"]

            try:
                build_mscan_run_result(store, other_job["job_id"])
            except ValueError:
                pass
            else:
                raise AssertionError("ValueError erwartet: build_mscan_run_result auf dscan-Job")
            try:
                build_mscan_run_result(store, "unbekannt-id")
            except KeyError:
                pass
            else:
                raise AssertionError("KeyError erwartet: unbekannte run_id")
            print("V1 ok  (build_mscan_run_result: Kanaele/Snapshot-Anreicherung/silent_channels/Fehlerfaelle)")

            # --- V2: spektrum_path == None (job_dir per tmp ausserhalb Repo-Root)
            recs = res["channels"][0]["recordings"]
            assert all(rec["spektrum_path"] is None for rec in recs), recs
            print("V2 ok  (spektrum_path None, wenn job_dir ausserhalb des Repo-Roots liegt)")

            # --- V3: resolve_run_file (vormals MS8) ------------------------------
            a_path = resolve_run_file(store, run_id, "a.wav")
            assert os.path.realpath(a_path) == os.path.realpath(os.path.join(job_dir, "a.wav")), a_path

            try:
                resolve_run_file(store, run_id, "b.wav")
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("FileNotFoundError erwartet: b.wav gelistet, aber fehlt")

            try:
                resolve_run_file(store, run_id, "fremd.wav")
            except ValueError:
                pass
            else:
                raise AssertionError("ValueError erwartet: fremd.wav existiert, ist aber nicht gelistet")

            for bad_name in ("job.json", "../a.wav", "sub\\a.wav", "A.WAV ", ""):
                try:
                    resolve_run_file(store, run_id, bad_name)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"ValueError erwartet fuer Dateiname {bad_name!r}")

            roots = store.storage_roots()
            assert len(roots) == 2, roots
            assert os.path.realpath(records_dir) in roots, roots
            print("V3 ok  (resolve_run_file: Whitelist staerker als Wurzelvergleich, Sandbox-Faelle)")

            # --- V4: spektrum_path gesetzt (job_dir real unter Repo-Root) --------
            run_manifest2 = store.create_job(
                type="mscan", label="V-Test-Root",
                params={"mscan_job_id": "DEF1", "title": "V-Test-Root", "mode": "ueberwachung",
                        "dwell_s": None, "hold_s": None, "min_recording_s": None,
                        "squelch_from_memory": None, "channels": channels_snapshot},
                start_iso="2097-01-01T10:00:00+01:00", end_iso="2097-01-01T10:15:00+01:00",
                storage_root=under_root_dir,
            )
            run_id2 = run_manifest2["job_id"]
            job_dir2 = store.get(run_id2)["job_dir"]
            store.heartbeat(run_id2, stats={"cycles": 1, "recordings": 1, "active_channels": 1},
                             files=["c.wav"],
                             results={
                                 "channels": [{
                                     "channel_id": "T1", "label": "Test T1", "freq_hz": 100_000_000,
                                     "mem": 0, "hits": 1, "total_s": 2.0, "level_max_dbuv": 30.0,
                                     "recordings": [
                                         {"file": "c.wav", "start_at": "2097-01-01T10:00:05+01:00",
                                          "duration_s": 2.0, "level_max_dbuv": 30.0, "cycle": 1,
                                          "device_cycle": 1, "bytes": 44},
                                     ],
                                 }],
                                 "cycles": 1,
                                 "audio_orphan": 0, "audio_orphan_pre": 0,
                                 "audio_orphan_mid": 0, "audio_orphan_post": 0,
                             })
            res2 = build_mscan_run_result(store, run_id2)
            expected_rel = os.path.relpath(job_dir2, repo_root).replace(os.sep, "/")
            got = res2["channels"][0]["recordings"][0]["spektrum_path"]
            assert got == expected_rel + "/c.wav", (got, expected_rel)
            # T-1014b: dieser Lauf traegt bewusst kein audio_resumed im
            # Manifest - so wie alle Laeufe von vor T-1014a. Die
            # Ergebnisansicht muss dafuer None liefern; das Frontend blendet
            # die Fortsetzungen im Tooltip der Tonbilanz dann aus.
            assert res2["run"]["audio_resumed"] is None, res2["run"]
            print("V4 ok  (spektrum_path gesetzt, wenn job_dir real unter dem Repo-Root liegt)")

            # --- V5: Pegel-Inventur (kind "level", T-1019d) --------------------
            # Eigenes Zeitfenster (2095), damit es mit V1 (2096) und V4 (2097)
            # nicht kollidiert ("ein Empfaenger, ein Job").
            run_manifest3 = store.create_job(
                type="mscan", label="V5-Pegel",
                params={"mscan_job_id": "DEF3", "title": "V5-Pegel", "mode": "inventur",
                        "stop_after": "runtime", "record_audio": False,
                        "level_active_dbuv": 25.0, "dwell_s": 0.2, "hold_s": 0.0,
                        "min_recording_s": 0.5, "squelch_from_memory": True,
                        "channels": channels_snapshot},
                start_iso="2095-01-01T10:00:00+01:00", end_iso="2095-01-01T10:20:00+01:00",
            )
            run_id3 = run_manifest3["job_id"]
            store.heartbeat(run_id3, stats={"cycles": 12, "recordings": 0,
                                             "active_channels": 1, "bytes": 0},
                             files=[],
                             results={
                                 "kind": "level",
                                 "channels": [
                                     {"channel_id": "T1", "label": "Test T1",
                                      "freq_hz": 100_000_000, "mem": 0, "active": True,
                                      "hits": 5, "below_threshold": 1, "cycles_active": 4,
                                      "level_max_dbuv": 41.0, "level_last_dbuv": 33.0,
                                      "first_at": "2095-01-01T10:00:05+01:00",
                                      "last_at": "2095-01-01T10:18:00+01:00",
                                      # T-1019f: Tripel [Durchgang, Treffer,
                                      # max_dbuv] wie sie neue Laeufe liefern.
                                      "per_cycle": [[0, 2, 38.5], [3, 1, 27.0],
                                                     [7, 1, 41.0], [11, 1, 33.0]],
                                      "per_cycle_truncated": False},
                                     {"channel_id": "T2", "label": "Test T2",
                                      "freq_hz": 101_000_000, "mem": 1, "active": False,
                                      "hits": 0, "below_threshold": 3, "cycles_active": 0,
                                      "level_max_dbuv": None, "level_last_dbuv": None,
                                      "first_at": None, "last_at": None,
                                      "per_cycle": [], "per_cycle_truncated": False},
                                 ],
                                 "cycles": 12, "active_channels": 1, "silent_channels": 1,
                                 "cycle_ends_s": [1.5, 3.0, 4.5, 6.0, 7.5, 9.0,
                                                   10.5, 12.0, 13.5, 15.0, 16.5, 18.0],
                                 "items": 9, "below_threshold": 4, "unknown_freq": 0,
                                 "audio_ignored": 0, "level_active_dbuv": 25.0,
                             })
            res3 = build_mscan_run_result(store, run_id3)
            assert res3["run"]["kind"] == "level", res3["run"]
            assert res3["run"]["below_threshold"] == 4, res3["run"]
            assert res3["run"]["level_active_dbuv"] == 25.0, res3["run"]
            assert res3["run"]["items"] == 9, res3["run"]
            assert len(res3["run"]["cycle_ends_s"]) == 12, res3["run"]
            assert res3["run"]["cycle_ends_s"][0] == 1.5, res3["run"]
            # Gegenprobe unten (res, Tonlauf): der Schluessel fehlt dort.
            assert res3["run"]["params"]["stop_after"] == "runtime", res3["run"]["params"]
            assert res3["run"]["params"]["record_audio"] is False, res3["run"]["params"]
            assert res3["run"]["params"]["level_active_dbuv"] == 25.0, res3["run"]["params"]
            # Aktive Kanaele in channels, stille in silent_channels - trotz
            # vollstaendiger Kanalliste im Ergebnis.
            assert [c["channel_id"] for c in res3["channels"]] == ["T1"], res3["channels"]
            assert res3["channels"][0]["hits"] == 5, res3["channels"][0]
            assert res3["channels"][0]["per_cycle"] == [[0, 2, 38.5], [3, 1, 27.0],
                                                        [7, 1, 41.0], [11, 1, 33.0]], \
                res3["channels"][0]
            assert res3["channels"][0]["recordings"] == [], res3["channels"][0]
            # Snapshot-Anreicherung greift auch hier.
            assert res3["channels"][0]["demod"] == "FM", res3["channels"][0]
            assert [c["id"] for c in res3["silent_channels"]] == ["T2"], res3["silent_channels"]
            assert res3["silent_channels"][0]["below_threshold"] == 3, res3["silent_channels"][0]
            assert res3["silent_channels"][0]["bw_hz"] == 15000, res3["silent_channels"][0]

            # Gegenprobe: der Tonlauf aus V1 traegt weiterhin kein kind und
            # bildet silent_channels wie bisher aus den fehlenden Snapshot-IDs.
            assert res["run"]["kind"] is None, res["run"]
            assert res["run"]["cycle_ends_s"] is None, res["run"]
            assert res["run"]["params"]["record_audio"] is None, res["run"]["params"]
            print("V5 ok  (kind level: aktive/stille Kanaele getrennt, Pegel-Felder und "
                  "Lauf-Parameter durchgereicht)")
        finally:
            shutil.rmtree(under_root_dir, ignore_errors=True)

    print("\n[mscan_view] Selbsttest erfolgreich abgeschlossen.")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="MSCAN-Ergebnisansicht - Kernlogik (T-1106a)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren (kein Server)")
    args = ap.parse_args()

    if args.selftest:
        run_selftest()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
