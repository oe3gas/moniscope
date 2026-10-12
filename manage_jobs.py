#!/usr/bin/env python3
"""
manage_jobs.py

Jobs-Seite fuer die Long-Run-Jobs (WS3, Abschnitt 11.5) - Port 8050. Zeigt
laufende/geplante/abgeschlossene Long-Run-Jobs (eb200_core/job_store.py) und
erlaubt Loeschen (geplant/verpasst) sowie "Jetzt nachholen" (verpasst -> neuer
Job mit uebernommenen Parametern).

WICHTIG: Diese Seite ist Fenster und Fernbedienung, NIE Ausfuehrer. Der
eigentliche Long-Run-Job (SCPI, Scheduler, DscanJobRecorder) laeuft
ausschliesslich im D-SCAN-Controller (waterfall.py, Port 8000) - siehe
WATERFALL_BASE unten. Stopp und Live-Statistik eines LAUFENDEN Jobs laufen
SERVERSEITIG UEBER DIESEN SERVER (Proxy), nicht als Browser-Direktzugriff:
  - GET  /api/livestatus - proxyt WATERFALL_BASE/api/longrun/status
  - GET  /api/prepstatus - proxyt PREPWORKER_BASE/api/status (T-1609)
  - POST /api/stopjob    - proxyt WATERFALL_BASE/api/longrun/stop
Grund: ein direkter Browser-fetch() von :8050 gegen :8000 waere ein
CROSS-ORIGIN-Request (anderer Port = andere Origin); waterfall.py setzt
KEINE CORS-Header (bewusst - der Controller kennt diese Seite nicht und soll
sie auch nicht kennen muessen), der Browser wuerde die Antwort also
blockieren. Der Proxy hier laeuft SERVERSEITIG per urllib.request (Timeout
2 s) - zwischen zwei Python-Prozessen gibt es kein CORS-Problem, das
Frontend spricht dadurch ausschliesslich SAME-ORIGIN mit diesem Server
(:8050). Ist der Controller nicht erreichbar (Timeout, Verbindung
abgelehnt, Controller laeuft nicht), liefert /api/livestatus
{"reachable": false} statt eines Fehlers - die Laufend-Gruppe zeigt dann
schlicht "Controller (:8000) nicht erreichbar" statt zu blockieren.
GET /api/recordings (T-1103) liefert die vereinheitlichte Aufzeichnungsliste aus
eb200_core/recording_index.py; die Tabelle auf "/" haengt seit T-1103 daran, nicht mehr an /api/jobs.

Architektur wie die uebrigen Ein-Datei-Tools (analyze_sessions.py,
eb200_core/block_admin.py): http.server, eingebettetes HTML/JS, nur Python-
Standardbibliothek. Die Kernlogik (Anreicherung der Jobliste, Loeschen,
Nachholen, Kurztext aendern) steckt in freien Funktionen (build_jobs_view/
handle_delete/handle_retry/handle_label), die NUR einen JobStore
entgegennehmen - der HTTP-Handler ruft sie nur noch auf. Dadurch ist sie ohne
laufenden Server pruefbar (siehe "python manage_jobs.py --selftest").

Die MSCAN-Definitionsliste liegt seit T-1101 direkt auf "/" (Abschnitt
#definitionen); "/mscan" leitet dorthin um.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import shutil
import signal
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from eb200_core.job_store import JobStore, ST_GEPLANT, ST_LAEUFT, ST_VERPASST, ST_BEENDET, ST_GESTOPPT, ST_ABGEBROCHEN
from eb200_core.mscan_job import (
    DEFAULT_PARAMS, MAX_CHANNELS, MscanJobStore, clean_title, resolve_snapshot, validate_params,
)
from eb200_core.mscan_run import build_run_params, run_label
from eb200_core.known_freq import load_catalog_cached
from eb200_core.param_store import register_server
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
from eb200_core.recording_index import (
    list_recordings, summarize, delete_recording, set_recording_label,
    export_recording_sigmf,
    RecordingLockedError, RecordingNotDeletableError,
)

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8050

# Siehe Moduldoku - Stopp/Live-Status eines laufenden Jobs laufen IMMER
# hierueber, NIE lokal in diesem Skript. Fest auf localhost, da Controller
# und Jobs-Seite auf derselben Station laufen (wie :8020/:8040 neben :8000).
WATERFALL_BASE = "http://localhost:8000"
# T-1609: Der Aufbereitungs-Worker (:8090) hat KEINE eigene Oberflaeche - er
# liefert nur /api/status und wird hier im Reiter "Aufbereitung" angezeigt.
# Derselbe Proxy-Grund wie beim Controller: ein Browser-fetch() ueber
# Portgrenzen waere cross-origin, und kein Server der Suite setzt CORS-Header.
PREPWORKER_BASE = "http://localhost:8090"

# Timeout fuer die serverseitigen Proxy-Aufrufe gegen den Controller (siehe
# Moduldoku) - kurz genug, dass ein nicht erreichbarer Controller die
# Jobs-Seite nicht spuerbar haengen laesst (Polling alle 5 s).
LIVESTATUS_TIMEOUT_S = 2.0


def _proxy_get_longrun_status() -> dict:
    """Serverseitiger Proxy fuer GET /api/livestatus (siehe Moduldoku - CORS-
    Umgehung). Liefert bei Erfolg {"reachable": True, **antwort_von_waterfall}
    (also inkl. "active"/"job"), bei JEDEM Fehler (Timeout, Verbindung
    abgelehnt, ungueltiges JSON) {"reachable": False} - wirft NIE eine
    Ausnahme, damit /api/livestatus selbst immer 200 liefert."""
    try:
        with urllib.request.urlopen(
            WATERFALL_BASE + "/api/longrun/status", timeout=LIVESTATUS_TIMEOUT_S
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"reachable": False}
    result = {"reachable": True}
    result.update(data)
    return result


def _proxy_get_prepare_status() -> dict:
    """Serverseitiger Proxy fuer GET /api/prepstatus (T-1609). Wie
    _proxy_get_longrun_status() NIE eine Ausnahme: laeuft der Worker nicht,
    kommt {"reachable": False}, und der Reiter schreibt "Worker nicht
    erreichbar" statt eine veraltete Anzeige stehen zu lassen."""
    try:
        with urllib.request.urlopen(
            PREPWORKER_BASE + "/api/status", timeout=LIVESTATUS_TIMEOUT_S
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"reachable": False}
    result = {"reachable": True}
    result.update(data)
    return result


def _proxy_stop_longrun_job() -> dict:
    """Serverseitiger Proxy fuer POST /api/stopjob (siehe Moduldoku). Wie
    _proxy_get_longrun_status() NIE eine Ausnahme - {"reachable": False} bei
    jedem Fehler, das Frontend laedt bei Bedarf einfach /api/jobs neu."""
    try:
        req = urllib.request.Request(WATERFALL_BASE + "/api/longrun/stop", method="POST")
        with urllib.request.urlopen(req, timeout=LIVESTATUS_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"reachable": False}
    result = {"reachable": True}
    result.update(data)
    return result


# ---------------------------------------------------------------------------
# Kernlogik (handler-unabhaengig - siehe Moduldoku, _self_test deckt dies ab)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# T-1814a: Fehler mit sprachneutralem Code. Muster: RecordingError in
# eb200_core/recording_index.py (T-1802c). _error_payload liest code und params
# und schickt beides an den Browser; errText() formuliert daraus den Text aus
# EB200_LABELS.err_code. Die Ableitung von ValueError bleibt, weil alle
# Fangstellen (_mscan_call, _handle_mutation, _handle_label) ValueError fangen -
# so aendert sich am Kontrollfluss nichts.
# ---------------------------------------------------------------------------
ERR_JSON_OBJECT_EXPECTED = "json_object_expected"
ERR_KIND_AND_REF_REQUIRED = "kind_and_ref_required"
ERR_TITLE_REQUIRED = "title_required"
ERR_RETRY_NOT_MISSED = "retry_not_missed"
ERR_MSCAN_DELETE_BLOCKED = "mscan_delete_blocked"
ERR_MSCAN_NO_CHANNELS = "mscan_no_channels"
ERR_MSCAN_MISSING_CHANNELS = "mscan_missing_channels"
ERR_INVALID_REQUEST = "invalid_request"


class JobsError(ValueError):
    """Fehler der Job-/MSCAN-Logik mit Code und Parametern statt Klartext."""

    def __init__(self, code, **params):
        super().__init__(code)
        self.code = code
        self.params = params


def build_jobs_view(store: JobStore) -> list:
    """
    Baut die /api/jobs-Antwort: list_jobs() (schnelle Index-Kurzfassung:
    job_dir/state/type/start_iso/end_iso/label) je Job angereichert um die
    Manifest-Eckdaten aus get() (params/decimation_s/rotation_min/
    storage_root/stats/files/abort_reason/ended_iso/manifest_unreachable).
    get() faellt bei nicht erreichbarem Manifest auf die Index-Kurzfassung
    zurueck (siehe job_store.py) - diese Funktion wirft daher nie wegen
    einer abgehaengten Ablage.
    """
    result = []
    for entry in store.list_jobs():
        manifest = store.get(entry["job_id"])
        item = dict(entry)
        item["params"] = manifest.get("params", {})
        item["decimation_s"] = manifest.get("decimation_s")
        item["rotation_min"] = manifest.get("rotation_min")
        item["storage_root"] = manifest.get("storage_root")
        item["stats"] = manifest.get("stats", {})
        item["files"] = manifest.get("files", [])
        item["abort_reason"] = manifest.get("abort_reason")
        item["abort_code"] = manifest.get("abort_code")
        item["abort_detail"] = manifest.get("abort_detail")
        item["ended_iso"] = manifest.get("ended_iso")
        item["manifest_unreachable"] = manifest.get("manifest_unreachable", False)
        result.append(item)
    return result


def build_recordings_view(store: JobStore, mscan_store) -> dict:
    """Fuer GET /api/recordings (T-1103): alle Aufzeichnungen (Direkt-Session,
    Direkt-Spektrum, Long-Run dscan/ifpan/mscan) im einheitlichen Zeilenschema
    von eb200_core/recording_index.py plus Zaehlwerke fuer die Filter-Chips.
    list_recordings() wirft nie - diese Funktion daher auch nicht."""
    rows = list_recordings(store, mscan_store=mscan_store)
    return {"rows": rows, "summary": summarize(rows)}


def handle_delete(store: JobStore, job_id: str) -> dict:
    """Fuer POST /api/delete. store.delete() wirft ValueError bei falschem
    Zustand bzw. KeyError bei unbekannter job_id - der Handler uebersetzt
    beides in HTTP 400 mit Klartext."""
    store.delete(job_id)
    return {"status": "ok"}


def handle_delete_recording(store: JobStore, payload) -> dict:
    """Fuer POST /api/deleterecording (T-1104): einheitliches Loeschen inkl.
    Daten fuer alle Herkuenfte (job/session/spectrum/orphan) ueber
    eb200_core/recording_index.delete_recording(). Sperren (laeuft noch)
    und Regelverstoesse (Sandbox, Long-Run-Teil) werden vom Handler in
    HTTP 409 bzw. 400 uebersetzt."""
    if not isinstance(payload, dict):
        raise JobsError(ERR_JSON_OBJECT_EXPECTED)
    kind = payload.get("kind")
    ref = payload.get("ref")
    if not kind or not ref:
        raise JobsError(ERR_KIND_AND_REF_REQUIRED)
    return delete_recording(store, kind, ref)


def handle_sigmf_export(store: JobStore, payload) -> dict:
    """Fuer POST /api/sigmfexport (T-922a): SigMF-Metadatei zu einer
    IQ-Aufnahme schreiben, wahlweise samt kopierter .sigmf-data. store
    wird nicht gebraucht (der Export kennt keine Jobs) und steht nur der
    einheitlichen Handler-Signatur wegen in der Liste - Muster
    handle_delete_recording(). Sperren (laeuft noch) und
    Regelverstoesse (keine IQ-Aufnahme, Sandbox) uebersetzt der
    Aufrufer in HTTP 409 bzw. 400."""
    if not isinstance(payload, dict):
        raise JobsError(ERR_JSON_OBJECT_EXPECTED)
    kind = payload.get("kind")
    ref = payload.get("ref")
    if not kind or not ref:
        raise JobsError(ERR_KIND_AND_REF_REQUIRED)
    return export_recording_sigmf(kind, ref, with_data=bool(payload.get("with_data")))


def handle_reclabel(store: JobStore, payload) -> dict:
    """Fuer POST /api/reclabel (T-1110): Kurztext-Aenderung fuer ALLE
    Aufzeichnungsarten (job/session/spectrum/audio) ueber
    eb200_core/recording_index.set_recording_label() - Pendant zu
    handle_delete_recording(). /api/joblabel bleibt fuer Laufend/Geplant
    unveraendert bestehen (dort ist ref immer schon die job_id)."""
    if not isinstance(payload, dict):
        raise JobsError(ERR_JSON_OBJECT_EXPECTED)
    kind = payload.get("kind")
    ref = payload.get("ref")
    if not kind or not ref:
        raise JobsError(ERR_KIND_AND_REF_REQUIRED)
    return set_recording_label(store, kind, ref, payload.get("label", ""))


def handle_delete_data(store: JobStore, job_id: str) -> dict:
    """Fuer POST /api/deletedata - endgueltiges Loeschen eines
    abgeschlossenen Jobs SAMT Messdaten (JobStore.delete_with_data,
    Begruendung und Schutzregeln dort). ValueError/KeyError -> HTTP 400
    wie bei den uebrigen Mutationen."""
    return store.delete_with_data(job_id)


def handle_label(store: JobStore, job_id: str, label) -> dict:
    """Fuer POST /api/joblabel - Kurztext in JEDEM Zustand aenderbar (siehe
    JobStore.update_label()). store.update_label() wirft KeyError bei
    unbekannter job_id - der Handler uebersetzt das in HTTP 400."""
    return store.update_label(job_id, label)


def handle_retry(store: JobStore, job_id: str) -> dict:
    """
    Fuer POST /api/retry - NUR fuer Zustand ST_VERPASST zulaessig: legt via
    create_job() einen NEUEN Job mit uebernommenen Parametern an
    (type/label/params/decimation_s/rotation_min/storage_root), start_iso
    = jetzt, Ende = urspruengliche DAUER ab jetzt (Original mit end_iso
    None -> auch der neue Job end_iso=None, "bis Stopp" bleibt "bis
    Stopp"). Das Original bleibt UNVERAENDERT als "verpasst" stehen
    (Historie, WS3 11.5) - retry loescht/ersetzt es nicht.

    Wirft ValueError bei falschem Ausgangszustand ODER wenn create_job()
    selbst ablehnt (typischerweise eine Zeitfenster-Ueberlappung mit einem
    inzwischen angelegten Job) - der Handler uebersetzt das in HTTP 400.
    """
    original = store.get(job_id)
    if original.get("state") != ST_VERPASST:
        raise JobsError(ERR_RETRY_NOT_MISSED, job_id=job_id,
                        state=original.get("state"))
    now = datetime.now().astimezone()
    start_iso = now.isoformat(timespec="seconds")
    orig_end = original.get("end_iso")
    if orig_end is None:
        new_end_iso = None
    else:
        duration = datetime.fromisoformat(orig_end) - datetime.fromisoformat(original["start_iso"])
        new_end_iso = (now + duration).isoformat(timespec="seconds")
    return store.create_job(
        type=original["type"],
        label=original.get("label", ""),
        params=original.get("params", {}),
        start_iso=start_iso,
        end_iso=new_end_iso,
        decimation_s=original.get("decimation_s"),
        rotation_min=original.get("rotation_min", 60),
        storage_root=original.get("storage_root"),
    )


# ---------------------------------------------------------------------------
# MSCAN-Job-Verwaltung (T-1003) - Kernlogik, siehe eb200_core/mscan_job.py
# fuer die Job-DEFINITIONEN selbst. Analog zu build_jobs_view/handle_* oben:
# freie Funktionen, die nur einen MscanJobStore (und ggf. den JobStore fuer
# Laufzaehlung/Loeschsperre) entgegennehmen - dadurch ohne laufenden Server
# pruefbar (siehe Abschnitt "MSCAN" im Selbsttest).
# ---------------------------------------------------------------------------

def _mscan_run_counts(store: JobStore) -> dict:
    """Zaehlt je MSCAN-Job-DEFINITION (mscan_job_id aus params), wie viele
    job_store-Laeufe (type == 'mscan') dafuer existieren - fuer die Spalte
    'Laeufe' in build_mscan_jobs_view() und die Loeschsperre in
    handle_mscan_delete(). Kaputte/fehlende params (z. B.
    manifest_unreachable, siehe JobStore.get()) werden still ignoriert,
    kein Fehler."""
    counts: dict = {}
    for entry in store.list_jobs():
        if entry.get("type") != "mscan":
            continue
        manifest = store.get(entry["job_id"])
        mscan_job_id = (manifest.get("params") or {}).get("mscan_job_id")
        if not mscan_job_id:
            continue
        counts[mscan_job_id] = counts.get(mscan_job_id, 0) + 1
    return counts


def build_mscan_jobs_view(mscan_store: MscanJobStore, store: JobStore) -> dict:
    """
    Baut die /api/mscan/jobs-Antwort: Uebersicht aller MSCAN-Job-
    DEFINITIONEN (NICHT deren Laeufe), angereichert um channel_count und
    run_count (Anzahl job_store-Laeufe, siehe _mscan_run_counts()).

    Rueckgabe {"jobs": [...], "errors": [...]} - errors kommt direkt aus
    mscan_store.errors (kaputte Job-Dateien, siehe MscanJobStore.list_jobs())
    und bleibt bewusst ein EIGENES Feld statt in die Liste gemischt zu
    werden, damit die Jobliste defekte Dateien separat anzeigen kann.
    """
    run_counts = _mscan_run_counts(store)
    jobs = mscan_store.list_jobs()
    view = [{
        "id": job["id"],
        "title": job["title"],
        "notes": job["notes"],
        "mode": (job.get("params") or {}).get("mode", "ueberwachung"),
        "created_at": job["created_at"],
        "modified_at": job["modified_at"],
        "last_run_at": job["last_run_at"],
        "channel_count": len(job["channels"]),
        "run_count": run_counts.get(job["id"], 0),
    } for job in jobs]
    return {"jobs": view, "errors": list(mscan_store.errors)}


def build_mscan_job_detail(mscan_store: MscanJobStore, catalog, job_id: str,
                            store: JobStore) -> dict:
    """
    Baut die /api/mscan/job-Antwort: vollstaendiges Job-Dict plus
    aufgeloeste Kanalliste. Bewusst NICHT resolve_snapshot() aus
    mscan_job.py benutzt - die bricht bei einer fehlenden Kanal-ID mit
    KeyError ab (siehe dortige Doku: 'Aufrufer muss vorher validiert
    haben'). Hier ist eine seit der Auswahl geaenderte Katalogdatei aber
    ein reiner ANZEIGEFALL, kein Fehler: fehlende Kanaele werden als
    {"id": id, "missing": True} markiert, damit sie in der Detailseite
    trotzdem sichtbar und entfernbar bleiben.

    ABWEICHUNG vom Aufgabentext: die dort skizzierte Signatur
    (mscan_store, catalog, job_id) enthielt keinen store-Parameter, obwohl
    der geforderte Rueckgabewert ein run_count traegt, das nur ueber den
    JobStore ermittelbar ist (siehe _mscan_run_counts()) - store wurde
    deshalb als vierter Parameter ergaenzt.

    Rueckgabe: {"job": job, "channels": [...], "max_channels": MAX_CHANNELS,
    "run_count": n}. Wirft ValueError/KeyError wie MscanJobStore.get().
    """
    job = mscan_store.get(job_id)
    channels = []
    for cid in job["channels"]:
        ch = catalog.by_id(cid)
        if ch is None:
            channels.append({"id": cid, "missing": True})
        else:
            channels.append({
                "id": ch["id"], "label": ch["label"], "freq_hz": ch["freq_hz"],
                "demod": ch["demod"], "bw_hz": ch["bw_hz"],
                "service": ch.get("service"), "file": ch.get("file"),
                "tags": ch.get("tags"), "missing": False,
            })
    run_count = _mscan_run_counts(store).get(job_id, 0)
    return {"job": job, "channels": channels, "max_channels": MAX_CHANNELS,
            "run_count": run_count}


def handle_mscan_create(mscan_store: MscanJobStore, catalog, payload) -> dict:
    """Fuer POST /api/mscan/create - {"title": ..., "notes": ...}. title ist
    Pflicht (nach clean_title() nichtleer, sonst ValueError), notes
    optional. Rueckgabe: der neu angelegte Job (siehe
    MscanJobStore.create())."""
    title = clean_title(payload.get("title"))
    if not title:
        raise JobsError(ERR_TITLE_REQUIRED)
    return mscan_store.create(title, payload.get("notes", ""), catalog=catalog)


def handle_mscan_update(mscan_store: MscanJobStore, catalog, payload) -> dict:
    """
    Fuer POST /api/mscan/update - {"job_id": ..., "title": ..., "notes": ...,
    "params": ...} (title/notes/params optional - ein fehlendes Feld bleibt
    unveraendert). params wird seit T-1019 in die vorhandenen Parameter
    GEMERGT statt sie vollstaendig zu ersetzen: die Detailseite schickt nur
    die Werte, die sie selbst anzeigt - ein Parameter, den sie (noch) nicht
    kennt, etwa record_audio oder level_active_dbuv der Pegel-Inventur,
    fiele beim Ersetzen still auf den Vorgabewert zurueck und der Job
    wechselte beim blossen Titel-Speichern die Betriebsart.
    validate_params() laeuft ohnehin innerhalb von MscanJobStore.save().
    Rueckgabe: der gespeicherte Job.
    """
    job = mscan_store.get(payload["job_id"])
    if "title" in payload:
        job["title"] = clean_title(payload["title"])
    if "notes" in payload:
        notes = payload["notes"]
        job["notes"] = "" if notes is None else str(notes)
    if "params" in payload:
        # get() hat fehlende Schluessel bereits aus DEFAULT_PARAMS ergaenzt
        # (mscan_job._fill_param_defaults), die Basis ist also vollstaendig.
        # Kein dict -> unveraendert durchreichen, damit validate_params()
        # die Fehlermeldung liefert und nicht hier eine TypeError entsteht.
        neue_params = payload["params"]
        if isinstance(neue_params, dict) and isinstance(job.get("params"), dict):
            job["params"] = {**job["params"], **neue_params}
        else:
            job["params"] = neue_params
    return mscan_store.save(job, catalog=catalog)


def handle_mscan_channels(mscan_store: MscanJobStore, catalog, payload) -> dict:
    """
    Fuer POST /api/mscan/channels - {"job_id": ..., "remove": [...],
    "add": [...]} (beide Listen optional). ERST remove, DANN add: der
    Kanalwaehler (Session B) schickt einen Diff pro Anfrage, diese
    Reihenfolge verhindert, dass ein Entfernen ein Hinzufuegen aus
    DERSELBEN Anfrage rueckgaengig macht. catalog wird IMMER mitgegeben -
    ohne ihn entfaellt die Kanal-Pruefung in mscan_job.validate_job().
    Rueckgabe: der gespeicherte Job.
    """
    job_id = payload["job_id"]
    job = mscan_store.get(job_id)   # wirft KeyError bei unbekannter ID
    remove_ids = payload.get("remove") or []
    add_ids = payload.get("add") or []
    if remove_ids:
        job = mscan_store.remove_channels(job_id, remove_ids, catalog=catalog)
    if add_ids:
        job = mscan_store.add_channels(job_id, add_ids, catalog=catalog)
    return job


def handle_mscan_delete(mscan_store: MscanJobStore, store: JobStore, payload) -> dict:
    """
    Fuer POST /api/mscan/delete - {"job_id": ...}. Loeschen ist NUR
    erlaubt, wenn KEIN job_store-Lauf (type == 'mscan' mit params.
    mscan_job_id == job_id) im Zustand ST_GEPLANT/ST_LAEUFT existiert -
    sonst ValueError mit Klartext (Zustand/Anzahl der blockierenden
    Laeufe).

    ABWEICHUNG vom Aufgabentext: die dort skizzierte Signatur
    (mscan_store, payload) enthielt keinen store-Parameter, obwohl die
    geforderte Loeschsperre nur ueber den JobStore pruefbar ist ('Dafuer
    store mitgeben' im Aufgabentext) - store wurde deshalb als zweiter
    Parameter ergaenzt.

    Rueckgabe {"deleted": job_id}.
    """
    job_id = payload["job_id"]
    mscan_store.get(job_id)   # wirft ValueError/KeyError bei ungueltiger/unbekannter ID
    blockierend = []
    for entry in store.list_jobs():
        if entry.get("type") != "mscan" or entry.get("state") not in (ST_GEPLANT, ST_LAEUFT):
            continue
        manifest = store.get(entry["job_id"])
        if (manifest.get("params") or {}).get("mscan_job_id") == job_id:
            blockierend.append(entry["job_id"])
    if blockierend:
        raise JobsError(ERR_MSCAN_DELETE_BLOCKED, job_id=job_id,
                        runs=len(blockierend), ids=", ".join(blockierend))
    mscan_store.delete(job_id)
    return {"deleted": job_id}


def handle_mscan_start(mscan_store: MscanJobStore, store: JobStore, catalog, payload) -> dict:
    """
    Fuer POST /api/mscan/start (T-1005 Session B) - startet einen LAUF
    (job_store-Job vom Typ mscan) fuer eine MSCAN-Job-DEFINITION. payload:
    {"job_id": ..., "start_iso": ... (fehlt/None -> jetzt), "end_iso": ...
    ODER "duration_min": ...} - end_iso/duration_min sind bei Modus
    ueberwachung je optional (beide fehlen -> "bis Stopp", end_iso=None).

    Bei Modus inventur entscheidet seit T-1019c params["stop_after"]:
      "cycle"   - ein einziger Durchgang. end_iso/duration_min werden
                  IGNORIERT (der Lauf endet regulaer am Zyklusende-Marker,
                  siehe docs/EB200_Geraeteprofil.md Abschnitt MSCAN) -
                  trotzdem wird ein SICHERHEITSENDE gesetzt, falls der
                  Marker ausbleibt: start + max(10 min, 3 * Kanalzahl *
                  (Dwell + 0,1 s)).
      "runtime" - fortlaufende Durchgaenge bis Laufende/Stopp. Hier gilt
                  dieselbe Regel wie bei der Ueberwachung: end_iso oder
                  duration_min bestimmen das Ende, fehlen beide, laeuft der
                  Lauf "bis Stopp". Ein Sicherheitsende waere hier falsch -
                  es wuerde die gewuenschte Erhebungsdauer ueberschreiben.

    Fehlende Kanal-IDs (seit der Auswahl aus dem Katalog verschwunden)
    werden VOR resolve_snapshot() geprueft und als EINE ValueError mit der
    vollstaendigen Liste gemeldet (resolve_snapshot() selbst wirft nur
    KeyError fuer die ERSTE fehlende ID). build_run_params() liefert
    zusaetzlich ValueError bei leerer Kanalliste (hier schon vorher
    abgefangen, fuer die passendere Fehlermeldung) bzw. > MAX_CHANNELS
    (durch MscanJobStore.add_channels() bereits ausgeschlossen).

    Rueckgabe: {"run_id": ..., "start_iso": ..., "end_iso": ...}.
    Ueberlappungs-ValueError von JobStore.create_job() wird UNVERAENDERT
    durchgereicht (Klartext nennt bereits die kollidierende job_id).
    """
    job_id = payload["job_id"]
    job_def = mscan_store.get(job_id)   # wirft ValueError/KeyError bei ungueltiger/unbekannter ID
    if not job_def["channels"]:
        raise JobsError(ERR_MSCAN_NO_CHANNELS, job_id=job_id)

    missing = [cid for cid in job_def["channels"] if catalog.by_id(cid) is None]
    if missing:
        raise JobsError(ERR_MSCAN_MISSING_CHANNELS, count=len(missing),
                        ids=", ".join(missing))
    snapshot = resolve_snapshot(job_def, catalog)
    run_params = build_run_params(job_def, snapshot)
    label = run_label(job_def)

    start_iso = payload.get("start_iso") or datetime.now().astimezone().isoformat(timespec="seconds")
    params = job_def["params"]

    # T-1019c: Das Sicherheitsende gilt NUR fuer die Inventur mit einem
    # einzigen Durchgang. Eine Inventur mit stop_after "runtime" hat wie die
    # Ueberwachung kein natuerliches Ende - dort ist die Laufdauer die
    # Vorgabe des Anwenders und darf nicht ueberschrieben werden.
    if params["mode"] == "inventur" and params.get("stop_after", "cycle") == "cycle":
        n = len(run_params["channels"])
        estimate_s = 3 * n * (params["dwell_s"] + 0.1)
        safety_s = max(600.0, estimate_s)
        end_iso = (datetime.fromisoformat(start_iso) + timedelta(seconds=safety_s)).isoformat(
            timespec="seconds")
    else:
        end_iso_payload = payload.get("end_iso")
        duration_min = payload.get("duration_min")
        if end_iso_payload:
            end_iso = end_iso_payload
        elif duration_min is not None:
            end_iso = (datetime.fromisoformat(start_iso) + timedelta(minutes=float(duration_min))
                       ).isoformat(timespec="seconds")
        else:
            end_iso = None   # "bis Stopp"

    manifest = store.create_job(
        type="mscan", label=label, params=run_params,
        start_iso=start_iso, end_iso=end_iso, decimation_s=None,
    )
    return {"run_id": manifest["job_id"], "start_iso": manifest["start_iso"],
            "end_iso": manifest["end_iso"]}


def build_mscan_runs(store: JobStore, mscan_job_id: str) -> list:
    """
    Fuer GET /api/mscan/runs?id=<mscan_job_id> (T-1005 Session B) - alle
    job_store-Laeufe (type == 'mscan') dieser Job-DEFINITION, sortiert nach
    start_iso ABSTEIGEND (neuester zuerst - anders als list_jobs()/
    build_jobs_view(), die chronologisch aufsteigend sortieren: die
    Laufliste einer Detailseite soll den letzten Lauf oben zeigen).

    Je Lauf: run_id, state, start_iso, end_iso, stats (die fuer die
    Laufliste relevanten Felder cycles/recordings/active_channels/phase/
    audio_orphan - die volle Recorder-Momentaufnahme liefert bereits
    /api/livestatus fuer den GERADE aktiven Lauf), results_channels
    (Anzahl Kanaele mit mindestens einer behaltenen Aufnahme, 0 vor/ohne
    Ergebnis), abort_reason. audio_orphan (T-1005 Session B Nachtrag 4):
    Audio-Datagramme ohne offenes Segment (MscanSegmenter.on_audio()) -
    Spalte "verwaist" in der Laufliste. audio_orphan_pre/_mid/_post (T-1006):
    Aufteilung des Zaehlers in Startburst/echtes Warnsignal/Nachlauf - bei
    aelteren Manifesten ohne diese Felder liefert stats.get() None statt 0,
    damit die Laufliste "-" statt einer irrefuehrenden 0 zeigt. ended_iso
    (reales Ende, T-1006) und job_dir ergaenzen end_iso (geplantes Ende)
    fuer die Laufliste bzw. den Deep-Link zur Ergebnisansicht.
    """
    runs = []
    for entry in store.list_jobs():
        if entry.get("type") != "mscan":
            continue
        manifest = store.get(entry["job_id"])
        if (manifest.get("params") or {}).get("mscan_job_id") != mscan_job_id:
            continue
        stats = manifest.get("stats") or {}
        results = manifest.get("results") or {}
        runs.append({
            "run_id": entry["job_id"],
            "state": entry["state"],
            "start_iso": entry["start_iso"],
            "end_iso": entry["end_iso"],
            "stats": {
                "phase": stats.get("phase"),
                "cycles": stats.get("cycles", 0),
                "recordings": stats.get("recordings", 0),
                "active_channels": stats.get("active_channels", 0),
                "audio_orphan": stats.get("audio_orphan", 0),
                "audio_orphan_pre": stats.get("audio_orphan_pre", None),
                "audio_orphan_mid": stats.get("audio_orphan_mid", None),
                "audio_orphan_post": stats.get("audio_orphan_post", None),
            },
            # T-1019d: Bei der Pegel-Inventur (kind "level") stehen ALLE
            # Kanaele im Ergebnis, auch die stillen - len() waere dort immer
            # die volle Kanalzahl und damit als "Kanaele mit Treffern"
            # schlicht falsch. results["active_channels"] zaehlt nur die
            # aktiven (eb200_core/mscan_run.MscanLevelInventory.results()).
            "results_channels": (results.get("active_channels", 0)
                                  if results.get("kind") == "level"
                                  else len(results.get("channels", []))),
            "ended_iso": manifest.get("ended_iso"),
            "job_dir": entry.get("job_dir"),
            "abort_reason": manifest.get("abort_reason"),
            "abort_code": manifest.get("abort_code"),
            "abort_detail": manifest.get("abort_detail"),
        })
    runs.sort(key=lambda r: r["start_iso"], reverse=True)
    return runs


# ---------------------------------------------------------------------------
# HTML/JS (eingebettet, wie bei den uebrigen Ein-Datei-Tools)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Gemeinsame CSS-Regeln/JS-Helfer aller drei Seiten (/, /mscan, /mscan/job) -
# jede Seite enthaelt <style>/*COMMON_CSS*/</style>, <script>/*THEME_HEAD*/</script>
# bzw. <script>/*COMMON_JS*/</script> als eigenen Block VOR ihrem seiten-
# spezifischen <style>/<script>; die Platzhalter werden am Modulende (siehe
# dort) per .replace() eingesetzt.
# T-1400: Farben kommen ausschliesslich aus eb200_core/ui_theme.py (Tokens +
# Grundregeln); hier stehen nur noch die :8050-eigenen Regeln, ohne Hex-Werte.
# Kopfzeile, Knoepfe, Felder, Tabellen, Reiter und Badges liefert BASE_CSS.
# ---------------------------------------------------------------------------

COMMON_CSS = THEME_CSS + BASE_CSS + """
  table { width:auto; }
  .arrow { font-size:0.8em; }
  #searchInput { width:280px; margin:8px 0; }
  .empty { color:var(--fg-muted); font-style:italic; padding:8px 0; }
"""

COMMON_JS = """
function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
  if (!res.ok) throw new Error(errText(data, res.status));
  return data;
}
function fmtDateTime(iso) { return iso ? isoDateTime(new Date(iso)) : '-'; }
"""

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; Recordings</title>
<style>/*COMMON_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  .job-row { padding:6px 0; border-bottom:1px solid var(--border); }
  .badge-missed { background:var(--danger-soft); color:var(--danger); padding:1px 6px; border-radius:3px; font-size:0.8em; margin-left:6px; }
  .abort-reason { color:var(--danger); font-size:0.82em; margin-top:2px; }
  .unreachable { color:var(--danger); }
  #runningBox, #plannedBox { border:1px solid var(--border); border-radius:6px; padding:8px 12px; background:var(--panel); }
  .editable-label { cursor:pointer; border-bottom:1px dotted var(--border-strong); }
  .editable-label:hover { border-bottom-color:var(--accent); color:var(--accent); }
  .label-empty { color:var(--fg-muted); font-style:italic; }
  .label-input { width:220px; }
  /* T-1103: Aufzeichnungen - Art-Badges, Filter-Chips, Zweizeiler
     (T-1400: Badge-Farben aus BASE_CSS, Chips als Rahmen-Pillen) */
  .rec-count { font-size:0.8em; color:var(--fg-muted); margin-left:8px; }
  /* T-1609: Reiter "Aufbereitung". Der Balken zeigt den belegten Anteil des
     Volumes; der Sockel ist als zweiter, gedaempfter Abschnitt sichtbar,
     damit erkennbar bleibt, dass er nicht zur Verfuegung steht. */
  .prep-space { display:flex; height:14px; border:1px solid var(--border);
                border-radius:3px; overflow:hidden; max-width:520px; margin:6px 0; }
  .prep-used { background:var(--accent); }
  .prep-floor { background:var(--border); }
  .prep-free { background:var(--panel-2); }
  .prep-num { font-variant-numeric:tabular-nums; }
  .prep-warn { color:var(--warn); font-weight:600; }
  .prep-bad { color:var(--err); font-weight:600; }
  .prep-sep td { background:var(--panel-2); color:var(--fg-muted);
                 font-size:0.85em; text-align:center; }
  #recFilters { display:flex; flex-wrap:wrap; align-items:center; gap:14px; margin:6px 0 10px 0; }
  .chip-group { display:inline-flex; gap:4px; }
  .chip { background:var(--panel); color:var(--fg); border:1px solid var(--border-strong); padding:2px 9px; border-radius:10px;
          font-size:0.8em; cursor:pointer; user-select:none; }
  .chip:hover { background:var(--panel-2); }
  .chip.active { background:var(--accent-soft); color:var(--accent); border-color:var(--accent); }
  .chip .n { opacity:0.7; margin-left:4px; }
  .sub { display:block; font-size:0.78em; color:var(--fg-muted); margin-top:2px; }
  .invalid { color:var(--danger); }
  .state-orphan { color:var(--warn); }
  button.delDataBtn:disabled { opacity:0.35; cursor:not-allowed; }
  button.sigmfBtn:disabled, button.sigmfDataBtn:disabled { opacity:0.35; cursor:not-allowed; }
  /* T-1101: Abschnitt MSCAN-Scan-Definitionen (vormals eigene Seite /mscan) */
  #createBox { margin:10px 0; padding-bottom:14px; border-bottom:1px solid var(--border); }
  #createBox input[type=text] { width:280px; }
  td.notiz { max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  #errorsBox { color:var(--danger); margin-top:14px; }
  #errorsBox div { margin:2px 0; }
  /* Scheduler-Diagnostik (T-206): Warnzeile in runningJobBox, wenn der
     Long-Run-Scheduler (waterfall.py) laenger als 90 s ohne Herzschlag ist.
     .hint.warn ist eigenstaendig (Modifikator der BASE_CSS-Klasse .hint),
     Farben = Warn-Tokens (Muster fuer Warnhinweise suite-weit). */
  .hint.warn { color:var(--warn); background:var(--warn-soft); border:1px solid var(--warn);
               border-radius:4px; padding:3px 8px; margin-bottom:6px;
               font-size:0.85em; }
</style>
</head>
<body>
/*HEADER_PAGE*/
<div class="top-links">
  <a id="linkDscan" href="#" target="eb200_main">&larr; D-SCAN waterfall</a>
  <a id="linkEval" href="#" target="eb200_spectrum">&larr; Spectrum analysis</a>
</div>

<h2>Running</h2>
<div id="runningBox">
<div id="runningJobBox"><div class="empty">loading ...</div></div>
<div id="runningDirectBox"></div>
</div>

<h2>Scheduled</h2>
<div id="plannedBox"><div class="empty">loading ...</div></div>

<div id="tabBar" class="tabbar">
  <button class="tab active" data-tab="aufzeichnungen">Recordings</button>
  <button class="tab" data-tab="definitionen">MSCAN scan definitions</button>
  <button class="tab" data-tab="aufbereitung">Preparation <span id="prepBadge"></span></button>
</div>
<section id="aufzeichnungen" class="tab-pane active">
<h2>Recordings <span id="recCount" class="rec-count"></span></h2>
<div id="recFilters">
  <span class="chip-group" id="chipsArt"></span>
  <span class="chip-group" id="chipsHerkunft"></span>
  <input type="text" id="searchInput" placeholder="Search in label ...">
</div>
<table>
  <thead><tr id="recHead"></tr></thead>
  <tbody id="recBody"></tbody>
</table>
</section>

<section id="definitionen" class="tab-pane">
<div id="createBox">
  <input type="text" id="newTitle" placeholder="Title of the new scan definition" maxlength="200">
  <button id="createBtn">New definition</button>
</div>
<input type="text" id="mscanSearchInput" placeholder="Filter ID / title / note ...">
<table>
  <thead><tr id="mscanHead"></tr></thead>
  <tbody id="mscanBody"></tbody>
</table>
<div id="errorsBox"></div>
</section>

<section id="aufbereitung" class="tab-pane">
<h2>Preparation <span id="prepState" class="rec-count"></span></h2>
<div id="prepSpace"></div>
<div id="prepCurrent"></div>
<h3>Last cycle</h3>
<div id="prepCycle"></div>
<h3>History</h3>
<table>
  <thead><tr><th>Time</th><th>File</th><th>Result</th><th>Duration</th><th>estimated</th><th>Source</th></tr></thead>
  <tbody id="prepBody"></tbody>
</table>
</section>

<script>/*COMMON_JS*/</script>
<script>
const HOST = window.location.hostname;

function fmtMHz(hz) { return hz == null ? '?' : (hz / 1e6).toFixed(3); }
function fmtMB(bytes) { return ((bytes || 0) / (1024 * 1024)).toFixed(1) + ' MB'; }

// Bereichstext fuer ALLE DREI Gruppen (Laufend/Geplant/Abgeschlossen,
// Anzeige-Bugfix): dscan liest params.start_hz/stop_hz (wie zuvor
// fmtRange), ifpan liest STATTDESSEN params.center_hz/span_hz - dort sind
// start_hz/stop_hz stets null (weder waterfall.py.job_active() noch
// JobStore.create_job() setzen sie fuer type=ifpan, siehe dortige
// params-Struktur), die alte fmtRange zeigte deshalb faelschlich "-".
// job muss .type und .params tragen (Laufend-Aufrufer bauen params dafuer
// aus den flachen Feldern von /api/livestatus, siehe renderRunning()).
// Fehlende Werte -> "?" (bewusst nicht "-", um von "kein Bereich bekannt"
// klar zu unterscheiden).
function jobRangeText(job) {
  const params = (job && job.params) || {};
  if (job && job.type === 'ifpan') {
    if (params.center_hz == null || params.span_hz == null) return '?';
    const spanHz = params.span_hz;
    const spanText = spanHz < 1e6 ? (spanHz / 1e3) + ' kHz' : (spanHz / 1e6) + ' MHz';
    return (params.center_hz / 1e6).toFixed(4) + ' MHz / Span ' + spanText;
  }
  if (params.start_hz == null) return '?';
  return fmtMHz(params.start_hz) + '-' + fmtMHz(params.stop_hz) + ' MHz';
}

// Umfangstext (Frames/MB/Dateien) fuer den Laufend-Kasten - die
// Abgeschlossen-Tabelle zeigt dieselben Werte seit T-108 in eigenen,
// numerisch sortierbaren Spalten (siehe recColumns()).
// dscan zeigt zusaetzlich Sweeps; ifpan hat keine sinnvolle Sweep-Zahl
// (IfpanJobRecorder.stats in eb200_core/job_runner.py kennt gar kein
// "sweeps"-Feld, WS3 11.2 kennt keine Sweeps) und laesst sie daher weg.
// stats.files enthaelt bei ifpan auch die WAV-Dateinamen (siehe
// IfpanJobRecorder.extra_files) - die Dateizahl stimmt damit automatisch.
// mscan hat weder Sweeps noch Frames (Kanalscan statt Sweep-Erfassung) und
// zeigt stattdessen die Zahl der behaltenen Voice-Aufnahmen (T-110 Nachtrag 2).
function jobStatsText(stats, type) {
  stats = stats || {};
  const nFiles = (stats.files || []).length;
  if (type === 'mscan') {
    const n = (stats.recordings != null) ? stats.recordings : nFiles;
    return fmtMB(stats.bytes) + ' – ' + n + ' ' + pluralWort(n, 'voice recording', 'voice recordings');
  }
  const tail = (stats.frames || 0) + ' ' + pluralWort(stats.frames || 0, 'frame', 'frames') + ' – ' + fmtMB(stats.bytes) + ' – ' + nFiles + ' ' + pluralWort(nFiles, 'file', 'files');
  return type === 'ifpan' ? tail : (stats.sweeps || 0) + ' ' + pluralWort(stats.sweeps || 0, 'sweep', 'sweeps') + ' – ' + tail;
}

// Klickbarer Kurztext fuer alle drei Gruppen (Laufend/Geplant/
// Abgeschlossen): Klick verwandelt die Anzeige in ein Eingabefeld,
// Enter/Blur speichert (danach Liste neu laden), Escape verwirft die
// Aenderung ohne zu speichern. Muster: die editierbaren Kurztexte in
// analyze_sessions.py/analyze_spectrum.py (Enter blurt, onchange
// speichert) - hier um Klick-zum-Bearbeiten und Escape erweitert, weil
// der Kurztext hier normalerweise als reiner Text angezeigt wird statt
// als Dauer-Eingabefeld. Liefert ein <span>, das der Aufrufer an die
// gewuenschte Stelle haengt.
// saveFn (T-1110): optionaler zweiter Parameter, (text) => Promise -
// speichert den neuen Kurztext. Default (Laufend/Geplant, unveraendert)
// speichert per /api/joblabel ueber item.job_id; fuer Direkt-Aufzeichnungen
// (Session/Spektrum/Audio) uebergibt der Aufrufer eine Funktion, die
// /api/reclabel mit {kind, ref} anspricht (siehe renderRecordings()).
function makeEditableLabel(item, saveFn) {
  const save = saveFn || (text => fetchJSON('/api/joblabel', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: item.job_id, label: text }),
  }));
  const span = document.createElement('span');
  span.className = 'editable-label';
  span.title = 'Click to edit';
  if (item.label) {
    span.textContent = item.label;
  } else {
    span.textContent = '(no label)';
    span.classList.add('label-empty');
  }
  span.onclick = () => {
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 200;
    input.value = item.label || '';
    input.className = 'label-input';
    span.replaceWith(input);
    input.focus();
    input.select();
    labelEditActive = true;

    let settled = false;
    const doSave = () => {
      if (settled) return;
      settled = true;
      labelEditActive = false;
      save(input.value).then(loadJobs).catch(e => { alert(e.message); loadJobs(); });
    };
    const doCancel = () => {
      if (settled) return;
      settled = true;
      labelEditActive = false;
      input.replaceWith(span);
    };
    input.onblur = doSave;
    input.onkeydown = ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
      else if (ev.key === 'Escape') { doCancel(); }
    };
  };
  return span;
}

el('linkDscan').href = 'http://' + HOST + ':8000/';
el('linkEval').href = 'http://' + HOST + ':8040/';

let allJobs = [];
let allRecs = [];
let recShowAll = false;
let recSummary = {};
let filterArt = 'alle';
let filterHerkunft = 'alle';
let searchTerm = '';
let sortKey = 'start';
let sortDir = -1;
// Konsolidierter Leerzustand der Laufend-Box (T-1110): "kein laufender
// Job" darf nur erscheinen, wenn WEDER ein Long-Run-Job (renderRunning,
// async gegen /api/livestatus) NOCH eine laufende Direktaufzeichnung
// (renderRunningDirect, synchron aus /api/recordings) aktiv ist - siehe
// updateRunningEmptyState().
let runningJobActive = false;
let runningDirectActive = false;
// Scheduler-Diagnostik (T-206): zuletzt gerenderte Warnzeile (leer = keine
// Warnung), von renderRunning() aus status.scheduler gesetzt und von
// updateRunningEmptyState() gelesen - gilt unabhaengig von runningJobActive,
// deshalb ein eigenes Modul-Feld statt lokal in renderRunning().
let schedWarnHtml = '';
// T-1110 Nachbesserung: waehrend ein Kurztext-Eingabefeld offen ist, pausiert
// loadJobs() (siehe dort) - sonst reisst der 5-s-Poll das Feld per innerHTML-
// Neuaufbau weg, bevor der Nutzer fertig tippen kann.
let labelEditActive = false;

async function loadJobs() {
  if (labelEditActive) return;   // kein Re-Render waehrend Kurztext-Edit
  try {
    allJobs = await fetchJSON('/api/jobs');
    const rec = await fetchJSON('/api/recordings');
    allRecs = rec.rows || [];
    recSummary = rec.summary || {};
  } catch (e) {
    el('plannedBox').innerHTML = '<div class="empty err">Error: ' + esc(e.message) + '</div>';
    el('recBody').innerHTML = '';
    return;
  }
  renderPlanned();
  renderRecordings();
  renderRunning();   // eigener Fetch gegen /api/livestatus - siehe Moduldoku
}

// Konsolidierter Leerzustand der Laufend-Box (T-1110): wird nach JEDEM der
// beiden Renderer (renderRunning async, renderRunningDirect sync) erneut
// ausgewertet, damit sich die beiden unabhaengigen Render-Pfade nicht
// gegenseitig widersprechen - "kein laufender Job" nur, wenn WIRKLICH
// weder ein Long-Run-Job noch eine Direktaufzeichnung laeuft.
function updateRunningEmptyState() {
  if (!runningJobActive && !runningDirectActive) {
    el('runningJobBox').innerHTML = schedWarnHtml + '<div class="empty">no job running</div>';
  } else if (!runningJobActive) {
    el('runningJobBox').innerHTML = schedWarnHtml;
  }
}

// ---- Laufend: Live-Statistik ueber den /api/livestatus-Proxy (nicht aus /api/jobs) --
async function renderRunning() {
  const box = el('runningJobBox');
  let status;
  try {
    // Same-Origin-Aufruf gegen DIESEN Server (:8050) - der server-seitige
    // Proxy /api/livestatus umgeht das Browser-CORS-Problem gegen :8000
    // (siehe Moduldoku). Ein Fetch-Fehler hier waere ein echter Fehler
    // dieses Servers, kein Cross-Origin-Fall mehr.
    status = await fetchJSON('/api/livestatus');
  } catch (e) {
    runningJobActive = false;
    box.innerHTML = '<div class="empty err">Error: ' + esc(e.message) + '</div>';
    return;
  }
  if (!status.reachable) {
    runningJobActive = false;
    box.innerHTML = '<div class="empty">Controller (:8000) not reachable</div>';
    return;
  }
  // Scheduler-Diagnostik (T-206): Warnzeile gilt unabhaengig von
  // status.active - ein haengender Scheduler betrifft auch die Phase ohne
  // aktiven Job (er wuerde ja gerade KEINEN geplanten Job mehr starten).
  const sch = status.scheduler;
  schedWarnHtml = (sch && sch.age_s !== null && sch.age_s > 90)
    ? '<div class="hint warn">No sign of life from the scheduler for ' + Math.round(sch.age_s) + ' s '
      + '(phase: ' + esc(labelOrDash('sched_phase', sch.phase)) + (sch.phase_arg ? ' ' + esc(sch.phase_arg) : '') + ')</div>'
    : '';
  if (!status.active) {
    runningJobActive = false;
    updateRunningEmptyState();
    return;
  }
  runningJobActive = true;
  const job = status.job;
  const stats = job.stats || {};
  // job.start_hz/stop_hz/center_hz/span_hz kommen flach von /api/livestatus
  // (siehe waterfall.py DeviceController.job_active()) - jobRangeText will
  // sie unter .params, deshalb hier nachgebaut (wie zuvor schon fuer
  // fmtRange, nur um center_hz/span_hz ergaenzt).
  const div = document.createElement('div');
  div.innerHTML =
    badge(job.type) + ' <b class="label-slot"></b> (' + esc(job.type) + ') &ndash; ' +
    jobRangeText({ type: job.type, params: {
      start_hz: job.start_hz, stop_hz: job.stop_hz,
      center_hz: job.center_hz, span_hz: job.span_hz,
    } }) + ' &ndash; since ' +
    fmtDateTime(job.start_iso) + ' &ndash; runs until ' +
    (job.end_iso ? fmtDateTime(job.end_iso) : 'stopped') + '<br>' +
    jobStatsText(stats, job.type) +
    ' <button id="stopBtn" class="danger">Stop</button>';
  div.querySelector('.label-slot').appendChild(makeEditableLabel(job));
  box.innerHTML = schedWarnHtml;
  box.appendChild(div);
  el('stopBtn').onclick = () => {
    if (!confirm('Job has been running since ' + fmtDateTime(job.start_iso) + ' - really stop it?')) return;
    // /api/stopjob ist derselbe Same-Origin-Proxy wie /api/livestatus -
    // reicht die Antwort des Controllers durch, daher hier auf .json()
    // warten (kein Cross-Origin-Sonderfall mehr, siehe Moduldoku).
    fetchJSON('/api/stopjob', { method: 'POST' })
      .catch(() => {})
      .finally(() => loadJobs());
  };
}

// ---- Geplant (inkl. "verpasst") --------------------------------------
function renderPlanned() {
  const box = el('plannedBox');
  const jobs = allJobs.filter(j => j.state === 'geplant' || j.state === 'verpasst');
  if (!jobs.length) { box.innerHTML = '<div class="empty">no scheduled jobs</div>'; return; }
  jobs.sort((a, b) => new Date(a.start_iso) - new Date(b.start_iso));
  box.innerHTML = '';
  jobs.forEach(j => {
    const row = document.createElement('div');
    row.className = 'job-row';
    const missedBadge = j.state === 'verpasst' ? ' <span class="badge-missed">MISSED</span>' : '';
    row.innerHTML =
      badge(j.type) + ' <b class="label-slot"></b>' + missedBadge + ' (' + esc(j.type) + ') &ndash; ' +
      jobRangeText(j) + ' &ndash; ' + fmtDateTime(j.start_iso) + ' to ' +
      (j.end_iso ? fmtDateTime(j.end_iso) : 'open (until stopped)') +
      ' <button class="delBtn danger">Delete</button>' +
      (j.state === 'verpasst' ? ' <button class="retryBtn">Retry now</button>' : '');
    row.querySelector('.label-slot').appendChild(makeEditableLabel(j));
    row.querySelector('.delBtn').onclick = () => {
      if (!confirm('Really delete job "' + (j.label || j.job_id) + '"?')) return;
      fetchJSON('/api/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: j.job_id }),
      }).then(loadJobs).catch(e => alert(e.message));
    };
    const retryBtn = row.querySelector('.retryBtn');
    if (retryBtn) {
      retryBtn.onclick = () => {
        fetchJSON('/api/retry', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: j.job_id }),
        }).then(loadJobs).catch(e => alert(e.message));
      };
    }
    box.appendChild(row);
  });
}

// ---- Abgeschlossen: sortierbare Tabelle + Textsuche ueber label -------
// ---- Aufzeichnungen (T-1103): einheitliche Liste aus /api/recordings ----
// Quelle: eb200_core/recording_index.py. Eine Zeile je Aufzeichnung, egal ob
// Direkt-Session, Direkt-Spektrum oder Long-Run-Job; art-spezifische Werte
// stehen in row.inhalt und werden hier zu EINEM Einzeiler formatiert.
// T-1801b: die Anzeigetexte stehen in EB200_LABELS (eb200_core/ui_theme.py),
// hier bleibt nur die Reihenfolge der Art-Chips und -Spalten.
const ART_ORDER = ['dscan', 'ifpan', 'mscan', 'audio'];
// Die Tabelle zeigt NUR abgeschlossene Aufzeichnungen - laufende und geplante
// Jobs stehen oben in Laufend/Geplant (aus /api/jobs + /api/livestatus).
const REC_DONE_STATES = ['beendet', 'gestoppt', 'abgebrochen', 'unbekannt', 'verwaist'];
// Auswertungs-Server je recording_index.auswertung.server (Ports siehe
// PLAN.md View-Baum). mscan: seit T-1106a eigener Server (:8060, analyze_mscan.py).
const EVAL_PORT = { sessions: 8020, spectrum: 8040, mscan: 8060, audio: 8070 };

function fmtDauer(s) {
  if (s == null) return '-';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return h + ' h ' + String(m).padStart(2, '0') + ' min';
  if (m) return m + ' min ' + String(sec).padStart(2, '0') + ' s';
  return sec + ' s';
}
function fmtKHz(hz) { return hz == null ? '?' : (hz >= 1e6 ? (hz / 1e6).toFixed(3) + ' MHz' : (hz / 1e3).toFixed(1) + ' kHz'); }
function fmtN(n) { return n == null ? '?' : Number(n).toLocaleString('en-US'); }

function badge(art) {
  const key = ART_ORDER.indexOf(art) >= 0 ? art : 'unknown';
  return '<span class="badge badge-' + key + '">' + esc(labelOf('art', art) || '?') + '</span>';
}

// T-1112: IQ ist KEINE eigene Art, sondern ein Merkmal von AUDIO-Zeilen
// (inhalt.is_iq aus eb200_core/recording_index.py). Deshalb ein ZUSATZ-
// Badge neben dem AUDIO-Badge statt eines weiteren Eintrags in ART_ORDER -
// ein zusaetzlicher Art-Wert wuerde die Summenprobe je_art in
// recording_index.summarize() brechen. Der Filterwert 'iq' der Art-
// Chipgruppe lebt aus demselben Grund nur im Frontend (renderChips,
// renderRecordings, parseFilterParams).
function isIqRec(r) {
  return r.art === 'audio' && !!(r.inhalt && r.inhalt.is_iq);
}
function badgeIq(r) {
  return isIqRec(r) ? ' <span class="badge badge-iq">IQ</span>' : '';
}

function recInhaltText(r) {
  const i = r.inhalt || {};
  if (!r.valid) return '<span class="invalid">File invalid or incomplete</span>';
  if (r.art === 'dscan') {
    return fmtMHz(i.start_hz) + '–' + fmtMHz(i.stop_hz) + ' MHz · ' + fmtKHz(i.step_hz) +
      (i.speed ? ' · ' + esc(i.speed) : '') + ' · ' + fmtN(i.sweeps) + ' ' + pluralWort(i.sweeps, 'sweep', 'sweeps') +
      (i.n_segments > 1 ? ' · ' + i.n_segments + ' ' + pluralWort(i.n_segments, 'segment', 'segments') : '');
  }
  if (r.art === 'ifpan') {
    return fmtMHz(i.center_hz) + ' MHz ±' + fmtKHz(i.span_hz != null ? i.span_hz / 2 : null) +
      ' · ' + esc(i.demod || '?') + ' ' + fmtKHz(i.bw_hz) + ' · ' + fmtN(i.frames) + ' ' + pluralWort(i.frames, 'frame', 'frames');
  }
  if (r.art === 'mscan') {
    const mode = esc(labelOf('mode', i.mode) || '?');
    return mode + ' · ' + fmtN(i.n_channels) + ' ' + pluralWort(i.n_channels, 'channel', 'channels') + ' · ' + fmtN(i.recordings) + ' ' + pluralWort(i.recordings, 'recording', 'recordings') + ' on ' +
      fmtN(i.active_channels) + ' ' + pluralWort(i.active_channels, 'channel', 'channels') + (i.cycles != null ? ' · ' + fmtN(i.cycles) + ' ' + pluralWort(i.cycles, 'cycle', 'cycles') : '');
  }
  if (r.art === 'audio') {
    return fmtMHz(i.freq_hz) + ' MHz · ' + esc(i.demod || '?') + ' ' + fmtKHz(i.bw_hz) +
      ' · ' + (i.sample_rate_hz != null ? (i.sample_rate_hz / 1000) : '?') + ' kHz' +
      (i.is_iq ? ' · IQ' : '') + (i.specbin_rel ? ' · +spectrum' : '');
  }
  return esc(JSON.stringify(i));
}

function recEvalUrl(r) {
  const a = r.auswertung || {};
  if (!a.server) return null;
  const port = EVAL_PORT[a.server];
  if (!port || !a.query) return null;
  return 'http://' + HOST + ':' + port + '/?' + a.query.replace(/=(.*)$/, (m, v) => '=' + encodeURIComponent(v));
}

// ---- Spaltensaetze der Aufzeichnungstabelle (T-1114) -------------------
// Bis T-1113 war der Spaltensatz EINE feste, globale Liste. Seit T-1114
// haengt er am Art-Chip (filterArt): "Alle" bleibt unveraendert, die
// vier Art-Filter zeigen statt des Sammel-Einzeilers "Inhalt" die
// Kennwerte ihrer Art in eigenen, sortierbaren Spalten; die Spalte
// "Aufnahmen" (Dateizahl, T-1111) entfaellt dort - bei MSCAN tritt an
// ihre Stelle die Zahl der behaltenen Aufnahmen aus inhalt.recordings.
//
// Die gemeinsamen Spalten stehen einmal als benannte Konstanten,
// recColumns() setzt sie mit den art-eigenen Spalten zusammen - es wird
// KEINE Zellfunktion kopiert. Quelle aller art-eigenen Werte bleibt
// r.inhalt aus eb200_core/recording_index.py; das Backend ist
// unveraendert (Muster T-1112: Art-Ansichten leben rein im Frontend).

// Zellen-/Sortierhelfer fuer die art-eigenen Spalten. Ungueltige Zeilen
// (r.valid === false) trugen bisher den Satz "Datei ungueltig oder
// unvollstaendig" in der Inhalt-Spalte; die gibt es in den Art-Ansichten
// nicht mehr, deshalb zeigt dort JEDE art-eigene Spalte ein rotes "?".
function artCell(fn) {
  return r => r.valid ? fn(r.inhalt || {}) : '<span class="invalid">?</span>';
}
function artSortNum(fn) {
  return r => { const v = fn(r.inhalt || {}); return v == null ? Number.MAX_SAFE_INTEGER : v; };
}
function artSortText(fn) {
  return r => String(fn(r.inhalt || {}) || '').toLowerCase();
}

// --- gemeinsame Spalten (Reihenfolge setzt recColumns()) ---------------
const COL_ART = { key: 'art', label: 'Kind', sortable: true,
  sort: r => (r.art || '') + (r.herkunft || ''),
  cell: r => badge(r.art) + badgeIq(r) + '<span class="sub">' + esc(labelOf('herkunft', r.herkunft)) + '</span>' };
const COL_LABEL = { key: 'label', label: 'Label', sortable: true,
  sort: r => (r.label || '').toLowerCase(),
  cell: () => '' };   // in renderRecordings gefuellt (editierbar nur bei Long-Run)
const COL_START = { key: 'start', label: 'Start / duration', sortable: true,
  sort: r => new Date(r.start_iso).getTime() || 0,
  cell: r => fmtDateTime(r.start_iso) + '<span class="sub">' + fmtDauer(r.dauer_s) + '</span>' };
const COL_INHALT = { key: 'inhalt', label: 'Content', sortable: true,
  sort: r => { const i = r.inhalt || {};
    const f = i.start_hz != null ? i.start_hz : (i.center_hz != null ? i.center_hz : null);
    return f == null ? Number.MAX_SAFE_INTEGER : f; },
  cell: r => recInhaltText(r) };
const COL_ZUSTAND = { key: 'zustand', label: 'State', sortable: true,
  sort: r => labelOf('zustand', r.zustand),
  cell: r => (r.zustand === 'verwaist' ? '<span class="state-orphan">' + esc(labelOf('zustand', r.zustand)) + '</span>' : esc(labelOf('zustand', r.zustand))) +
    (abortText(r)
      ? '<div class="abort-reason" title="' + esc(abortText(r)) + '">' + esc(abortText(r)) + '</div>' : '') };
const COL_AUFNAHMEN = { key: 'aufnahmen', label: 'Recordings', sortable: true, cls: 'num',   // T-1111: Dateizahl als eigene Spalte
  sort: r => r.dateien || 0,
  cell: r => fmtN(r.dateien) };
const COL_UMFANG = { key: 'umfang', label: 'Size', sortable: true, cls: 'num',
  sort: r => r.bytes || 0,
  cell: r => fmtMB(r.bytes) };
const COL_ABLAGE = { key: 'ablage', label: 'Storage location', sortable: false,
  cell: r => r.manifest_unreachable ? '<span class="unreachable">not reachable</span>' : esc(r.ablage || '-') };
const COL_EVAL = { key: 'eval', label: '', sortable: false,
  cell: r => { const u = recEvalUrl(r);
    return u ? '<a href="' + u + '" target="eb200_eval_' + esc(r.art) + '">Analysis</a>' : ''; } };
const COL_DEL = { key: 'del', label: '', sortable: false,
  cell: r => { const ok = r.loesch && r.loesch.erlaubt;
    const titles = {
      job: 'Permanently delete the job including its measurement data',
      session: 'Permanently delete the recording including sidecars and notes',
      spectrum: 'Permanently delete the recording including sidecars and notes',
      orphan: 'Delete the orphaned long-run directory and its contents',
      audio: 'Permanently delete the recording including sidecar, spectrum cache and notes',
    };
    const title = !ok ? 'Cannot be deleted (running or scheduled)' : (titles[r.loesch.kind] || 'Delete permanently');
    return '<button class="delDataBtn danger" title="' + esc(title) + '"' + (ok ? '' : ' disabled') + '>Delete</button>'; } };

// --- T-922b: SigMF-Export ---------------------------------------------
// Eigene Spalte hinter "Loeschen", gefuellt NUR in IQ-Zeilen (isIqRec,
// T-1112). Nicht-IQ-Zeilen bekommen eine leere Zelle - Muster COL_EVAL,
// das bei Aufzeichnungen ohne Auswerteseite ebenfalls leer bleibt,
// statt die Spalte je Art wegzuschalten.
//
// Zwei Knoepfe statt eines mit Auswahl: der Unterschied zwischen beiden
// ist der Plattenplatz, und Plattenplatz laesst sich benennen, nicht
// andeuten. "SigMF" schreibt die Metadatei neben die WAV und kopiert
// kein Byte Nutzdaten; "SigMF+Daten" legt zusaetzlich die .sigmf-data an
// und verdoppelt damit den Platzbedarf der Aufnahme.
//
// Laufende Aufnahmen sind gesperrt: waehrend geschrieben wird, sind
// Laengenangabe und Dateiende in Bewegung (der Server weist sie mit
// HTTP 409 ohnehin ab, siehe T-922a - hier nur, damit der Knopf gar
// nicht erst einlaedt).
const SIGMF_WARN_BYTES = 500 * 1024 * 1024;
const COL_SIGMF = { key: 'sigmf', label: '', sortable: false,
  cell: r => {
    if (!isIqRec(r)) return '';
    const laeuft = r.zustand === 'laeuft';
    const gesperrt = 'Recording in progress - export only after it ends';
    const t1 = laeuft ? gesperrt : 'Write the SigMF metadata file next to the WAV (copies no data)';
    const t2 = laeuft ? gesperrt : 'Also write .sigmf-data (' + fmtMB(r.bytes) + ' copy)';
    const d = laeuft ? ' disabled' : '';
    return '<button class="sigmfBtn" title="' + esc(t1) + '"' + d + '>SigMF</button> ' +
           '<button class="sigmfDataBtn" title="' + esc(t2) + '"' + d + '>SigMF+data</button>';
  } };

// Ein Weg fuer beide Knoepfe - der einzige Unterschied ist das Flag
// with_data und die Rueckfrage davor. Die Schwelle wird aus r.bytes
// gerechnet, das die Zeile ohnehin fuehrt; ein zusaetzlicher Weg zum
// Server, nur um die Groesse zu erfahren, waere verschenkt.
//
// kind/ref kommen aus r.loesch. Das Feld heisst nach seinem ersten
// Zweck, ist aber schlicht die Adresse der Aufzeichnung - dieselbe, die
// /api/deleterecording und /api/sigmfexport beide erwarten. Ein eigenes
// Feld dafuer haette die Zeilenform in eb200_core/recording_index.py
// geaendert; das Backend bleibt bewusst unangetastet (Muster T-1112).
function sigmfExport(r, withData) {
  if (withData && r.bytes > SIGMF_WARN_BYTES) {
    if (!confirm('The data export copies ' + fmtMB(r.bytes) + '. The recording will then take up '
        + 'about twice as much space. Continue?')) return;
  }
  fetchJSON('/api/sigmfexport', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: r.loesch.kind, ref: r.loesch.ref, with_data: withData }),
  }).then(res => {
    let text = res.meta_name + ' written';
    text += res.ncd
      ? ' (no data copy, ' + fmtMB(res.sample_bytes) + ' stay in the WAV)'
      : ' together with ' + res.data_name + ' (' + fmtMB(res.sample_bytes) + ' copied)';
    // T-922c: der Ablageort gehoert dazu - bei mehreren Storage-Roots ist
    // "geschrieben" ohne "wohin" keine Auskunft. Format wie in der Spalte
    // "Ablage" der Tabelle (portabel, siehe path_sandbox.to_portable).
    text += '\\n\\nStorage location: ' + res.dir;
    if (res.stale_data_name) {
      text += '\\n\\nWarning: ' + res.stale_data_name + ' sits next to it. Two data sources - '
            + 'SigMF tools warn and pick the WAV. Delete it or use "SigMF+data".';
    }
    alert(text);
  }).catch(e => alert(e.message));
}

// --- DSCAN: Bereich, Schrittweite ("BW"), Speed, Sweeps, Segmente ------
// Die Schrittweite heisst in der Spalte bewusst "BW" - sie IST die
// Aufloesungsbandbreite des Sweeps (Muster analyze_sessions.py:
// "Schrittweite <n> Hz (Bandbreite: ...)"). n_segments wird jetzt IMMER
// gezeigt, nicht mehr nur bei > 1 wie im alten Inhalt-Einzeiler.
const ART_COLUMNS_DSCAN = [
  { key: 'dscan_fmin', label: 'F (min)', sortable: true, cls: 'num',
    sort: artSortNum(i => i.start_hz),
    cell: artCell(i => fmtMHz(i.start_hz) + ' MHz') },
  { key: 'dscan_fmax', label: 'F max', sortable: true, cls: 'num',
    sort: artSortNum(i => i.stop_hz),
    cell: artCell(i => fmtMHz(i.stop_hz) + ' MHz') },
  { key: 'dscan_bw', label: 'BW', sortable: true, cls: 'num',
    sort: artSortNum(i => i.step_hz),
    cell: artCell(i => fmtKHz(i.step_hz)) },
  { key: 'dscan_speed', label: 'Speed', sortable: true,
    sort: artSortText(i => i.speed),
    cell: artCell(i => esc(i.speed || '-')) },
  { key: 'dscan_sweeps', label: 'Sweeps', sortable: true, cls: 'num',
    sort: artSortNum(i => i.sweeps),
    cell: artCell(i => fmtN(i.sweeps)) },
  { key: 'dscan_segmente', label: 'Segments', sortable: true, cls: 'num',
    sort: artSortNum(i => i.n_segments),
    cell: artCell(i => i.n_segments == null ? '-' : fmtN(i.n_segments)) },
];

// --- IFPAN: Mittenfrequenz, Span, Demod, Frames ------------------------
// Span zeigt die HALBE Spanne mit Vorzeichen ("±100.0 kHz"), genau wie
// der alte Inhalt-Einzeiler; sortiert wird nach der vollen span_hz.
// Demod traegt die Bandbreite als Unterzeile - sie bekommt bewusst keine
// eigene Spalte, darf aber auch nicht verschwinden.
const ART_COLUMNS_IFPAN = [
  { key: 'ifpan_center', label: 'Center frequency', sortable: true, cls: 'num',
    sort: artSortNum(i => i.center_hz),
    cell: artCell(i => fmtMHz(i.center_hz) + ' MHz') },
  { key: 'ifpan_span', label: 'Span', sortable: true, cls: 'num',
    sort: artSortNum(i => i.span_hz),
    cell: artCell(i => '±' + fmtKHz(i.span_hz != null ? i.span_hz / 2 : null)) },
  { key: 'ifpan_demod', label: 'Demod', sortable: true,
    sort: artSortText(i => i.demod),
    cell: artCell(i => esc(i.demod || '?') + '<span class="sub">' + fmtKHz(i.bw_hz) + '</span>') },
  { key: 'ifpan_frames', label: 'Frames', sortable: true, cls: 'num',
    sort: artSortNum(i => i.frames),
    cell: artCell(i => fmtN(i.frames)) },
];

// --- MSCAN: Modus, Kanaele, aktive Kanaele, Aufnahmen, Zyklen ----------
// "Aufnahmen" ist hier inhalt.recordings (behaltene Voice-Aufnahmen des
// Laufs), NICHT die Dateizahl r.dateien der Spalte COL_AUFNAHMEN - ein
// Pegel-Inventurlauf hat gar keine Dateien, aber sehr wohl Kanaele und
// Zyklen (siehe eb200_core/mscan_run.MscanLevelInventory).
const ART_COLUMNS_MSCAN = [
  { key: 'mscan_mode', label: 'Mode', sortable: true,
    sort: artSortText(i => i.mode),
    cell: artCell(i => esc(labelOf('mode', i.mode) || '?')) },
  { key: 'mscan_kanaele', label: 'Channels', sortable: true, cls: 'num',
    sort: artSortNum(i => i.n_channels),
    cell: artCell(i => fmtN(i.n_channels)) },
  { key: 'mscan_aktiv', label: 'active ch.', sortable: true, cls: 'num',
    sort: artSortNum(i => i.active_channels),
    cell: artCell(i => fmtN(i.active_channels)) },
  { key: 'mscan_aufnahmen', label: 'Recordings', sortable: true, cls: 'num',
    sort: artSortNum(i => i.recordings),
    cell: artCell(i => fmtN(i.recordings)) },
  { key: 'mscan_zyklen', label: 'Cycles', sortable: true, cls: 'num',
    sort: artSortNum(i => i.cycles),
    cell: artCell(i => i.cycles == null ? '-' : fmtN(i.cycles)) },
];

// --- AUDIO/IQ: Mittenfrequenz, Aufnahmebandbreite, Abtastrate ----------
// Der Satz gilt fuer BEIDE Art-Chips 'audio' und 'iq' - IQ ist kein
// eigener Art-Wert, sondern das Merkmal inhalt.is_iq einer AUDIO-Zeile
// (T-1112). Demod haengt als Unterzeile an Rec.BW, "+Spektrum"
// (inhalt.specbin_rel) als Unterzeile an Sampling; beides stand bisher
// im Inhalt-Einzeiler und bekommt keine eigene Spalte.
const ART_COLUMNS_AUDIO = [
  { key: 'audio_freq', label: 'Center frequency', sortable: true, cls: 'num',
    sort: artSortNum(i => i.freq_hz),
    cell: artCell(i => fmtMHz(i.freq_hz) + ' MHz') },
  { key: 'audio_recbw', label: 'Rec.BW', sortable: true, cls: 'num',
    sort: artSortNum(i => i.bw_hz),
    cell: artCell(i => fmtKHz(i.bw_hz) + '<span class="sub">' + esc(i.demod || '?') + '</span>') },
  { key: 'audio_sampling', label: 'Sampling', sortable: true, cls: 'num',
    sort: artSortNum(i => i.sample_rate_hz),
    cell: artCell(i => (i.sample_rate_hz != null ? (i.sample_rate_hz / 1000) + ' kHz' : '?') +
      (i.specbin_rel ? '<span class="sub">+spectrum</span>' : '')) },
];

// Spaltensatz zum aktuellen Art-Chip. "Alle" (und jeder unbekannte Wert)
// liefert den Satz von vor T-1114, inklusive Inhalt und Aufnahmen.
function recColumns() {
  const kopf = [COL_ART, COL_LABEL, COL_START];
  const fuss = [COL_ZUSTAND, COL_UMFANG, COL_ABLAGE, COL_EVAL, COL_DEL, COL_SIGMF];
  if (filterArt === 'dscan') return kopf.concat(ART_COLUMNS_DSCAN, fuss);
  if (filterArt === 'ifpan') return kopf.concat(ART_COLUMNS_IFPAN, fuss);
  if (filterArt === 'mscan') return kopf.concat(ART_COLUMNS_MSCAN, fuss);
  if (filterArt === 'audio' || filterArt === 'iq') return kopf.concat(ART_COLUMNS_AUDIO, fuss);
  return kopf.concat([COL_INHALT, COL_ZUSTAND, COL_AUFNAHMEN, COL_UMFANG, COL_ABLAGE, COL_EVAL, COL_DEL, COL_SIGMF]);
}

// Die Spalte, nach der tatsaechlich sortiert wird. sortKey bleibt ueber
// einen Filterwechsel hinweg stehen (damit die Sortierung beim Zurueck-
// schalten wieder greift), kann im neuen Satz aber fehlen - dann faellt
// die Sortierung auf "Start / Dauer" zurueck, wie bisher.
function recSortColumn(cols) {
  return cols.find(c => c.key === sortKey && c.sortable) || cols.find(c => c.key === 'start');
}

function chip(group, value, label, n, active) {
  return '<span class="chip' + (active ? ' active' : '') + '" data-group="' + group + '" data-value="' + value + '">' +
    esc(label) + (n != null ? '<span class="n">' + n + '</span>' : '') + '</span>';
}

function renderChips() {
  const jeArt = recSummary.je_art || {};
  const jeHk = recSummary.je_herkunft || {};
  const arts = ART_ORDER.filter(a => jeArt[a] != null).concat(Object.keys(jeArt).filter(a => ART_ORDER.indexOf(a) < 0));
  const artChips = arts.map(a => chip('art', a, labelOf('art', a), jeArt[a], filterArt === a));
  // T-1112: IQ-Chip haengt hinter den Art-Chips (Zaehlung ueber dieselbe
  // Zeilenmenge wie recSummary, deshalb aus allRecs statt aus je_art).
  // Bleibt sichtbar, solange er aktiv ist - sonst waere ein Filter ohne
  // Treffer nicht mehr abwaehlbar (Muster Verwaist-Chip).
  const iqCount = allRecs.filter(isIqRec).length;
  if (iqCount > 0 || filterArt === 'iq') artChips.push(chip('art', 'iq', 'IQ', iqCount, filterArt === 'iq'));
  el('chipsArt').innerHTML = chip('art', 'alle', 'All', recSummary.gesamt, filterArt === 'alle') +
    artChips.join('');
  const jeZustand = recSummary.je_zustand || {};
  let herkunftChips = ['direkt', 'longrun'].map(h => chip('herkunft', h, labelOf('herkunft', h), jeHk[h] || 0, filterHerkunft === h));
  if (jeZustand.verwaist > 0) {
    herkunftChips = herkunftChips.concat(chip('herkunft', 'verwaist', labelOf('zustand', 'verwaist'), jeZustand.verwaist, filterHerkunft === 'verwaist'));
  }
  el('chipsHerkunft').innerHTML = chip('herkunft', 'alle', 'All', null, filterHerkunft === 'alle') + herkunftChips.join('');
  document.querySelectorAll('#recFilters .chip').forEach(c => {
    c.onclick = () => {
      const g = c.dataset.group, v = c.dataset.value;
      if (g === 'art') filterArt = v; else if (g === 'herkunft') filterHerkunft = v;
      recShowAll = false;
      updateFilterHash();
      renderRecordings();
    };
  });
}

// T-1114: Spaltensatz (cols) und die tatsaechlich sortierende Spalte
// (activeKey) kommen von aussen. Der Pfeil haengt an activeKey statt an
// sortKey - nach einem Filterwechsel kann sortKey auf eine Spalte zeigen,
// die es im neuen Satz nicht gibt; dann stuende der Pfeil nirgends,
// waehrend real nach "Start / Dauer" sortiert wird. Der Klick setzt
// sortKey ebenfalls gegen activeKey um, sonst drehte er nur die Richtung
// einer gar nicht sichtbaren Spalte.
function buildRecHead(cols, activeKey) {
  const head = el('recHead');
  head.innerHTML = '';
  cols.forEach(col => {
    const th = document.createElement('th');
    if (col.cls) th.classList.add(col.cls);
    let txt = col.label;
    if (col.sortable) {
      th.classList.add('sortable');
      if (activeKey === col.key) txt += ' <span class="arrow">' + (sortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
      th.onclick = () => {
        sortDir = (activeKey === col.key) ? -sortDir : 1;
        sortKey = col.key;
        renderRecordings();
      };
    }
    th.innerHTML = txt;
    head.appendChild(th);
  });
}

// Laufende DIREKT-Aufzeichnungen (Audio/Spektrum/Session) in einer eigenen
// Zeilenliste innerhalb von runningBox, neben runningJobBox (T-1109, seit
// T-1110 gemeinsam im Rahmen von runningBox). Eigener Container statt in
// runningJobBox einzuhaengen, damit renderRunning() (eigener Fetch gegen
// /api/livestatus, ersetzt sein innerHTML komplett) und diese Anzeige sich
// nicht gegenseitig ueberschreiben - der Leerzustand wird stattdessen ueber
// updateRunningEmptyState() konsolidiert.
// KEIN Stopp/Loeschen hier - Stoppen bleibt im Zoom (:8000).
function renderRunningDirect(list) {
  const box = el('runningDirectBox');
  runningDirectActive = list.length > 0;
  if (!list.length) {
    box.innerHTML = '';
    updateRunningEmptyState();
    return;
  }
  box.innerHTML = list.map(r => {
    const kurztext = r.label || (r.loesch && r.loesch.ref ? r.loesch.ref.split('/').pop() : r.rec_id);
    return '<div>' + badge(r.art) + badgeIq(r) + ' ' + esc(kurztext) + ' – running since ' + fmtDateTime(r.start_iso) +
      ' – ' + recInhaltText(r) + ' <span class="sub">Stop it in the waterfall (:8000)</span></div>';
  }).join('');
  updateRunningEmptyState();
}

function renderRecordings() {
  renderChips();
  // T-1114: der Spaltensatz haengt am Art-Chip, deshalb EINMAL je Aufbau
  // ermitteln und an Kopf, Zellen und die beiden colspan-Zeilen
  // durchreichen - ein zweiter recColumns()-Aufruf koennte auseinander-
  // laufen, sobald jemand filterArt zwischendurch setzt.
  const cols = recColumns();
  const col = recSortColumn(cols);
  buildRecHead(cols, col.key);
  const tbody = el('recBody');
  tbody.innerHTML = '';

  const doneAll = allRecs.filter(r => REC_DONE_STATES.includes(r.zustand));
  renderRunningDirect(allRecs.filter(r => !REC_DONE_STATES.includes(r.zustand) && r.herkunft === 'direkt'));
  let rows = allRecs.slice();
  if (filterArt === 'iq') rows = rows.filter(isIqRec);
  else if (filterArt !== 'alle') rows = rows.filter(r => r.art === filterArt);
  if (filterHerkunft === 'verwaist') rows = rows.filter(r => r.zustand === 'verwaist');
  else if (filterHerkunft !== 'alle') rows = rows.filter(r => r.herkunft === filterHerkunft);
  rows = rows.filter(r => REC_DONE_STATES.includes(r.zustand));
  if (searchTerm) rows = rows.filter(r => (r.label || '').toLowerCase().includes(searchTerm));
  el('recCount').textContent = rows.length + ' of ' + doneAll.length;

  rows.sort((a, b) => (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * sortDir);

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="' + cols.length + '" class="empty">no recordings for this filter</td></tr>';
    return;
  }
  const LIMIT = 25;
  const truncated = !recShowAll && rows.length > LIMIT;
  const shown = truncated ? rows.slice(0, LIMIT) : rows;
  shown.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = cols.map(c => {
      const cls = c.key === 'label' ? 'label-slot' : (c.cls || '');
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + c.cell(r) + '</td>';
    }).join('');
    const labelSlot = tr.querySelector('.label-slot');
    labelSlot.innerHTML = '';
    if (r.herkunft === 'longrun' && r.job_id) {
      labelSlot.appendChild(makeEditableLabel({ job_id: r.job_id, label: r.label }));
    } else if (r.loesch && r.loesch.kind && r.loesch.kind !== 'orphan') {
      const kind = r.loesch.kind, ref = r.loesch.ref;
      labelSlot.appendChild(makeEditableLabel({ label: r.label }, text => fetchJSON('/api/reclabel', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: kind, ref: ref, label: text }),
      })));
    } else {
      const span = document.createElement('span');
      span.textContent = r.label || '';
      span.className = r.label ? '' : 'label-empty';
      if (!r.label) span.textContent = 'no label';
      span.title = 'Edit the label in the corresponding analysis view';
      labelSlot.appendChild(span);
    }
    if (r.art === 'mscan' && r.inhalt && r.inhalt.mscan_job_id) {
      const sub = document.createElement('span');
      sub.className = 'sub';
      sub.innerHTML = 'Definition <a href="/mscan/job?id=' + encodeURIComponent(r.inhalt.mscan_job_id) + '">' +
        esc(r.inhalt.mscan_job_id) + '</a>' + (r.inhalt.mscan_title ? ' · ' + esc(r.inhalt.mscan_title) : ' · (deleted)');
      labelSlot.appendChild(sub);
    }
    const delBtn = tr.querySelector('.delDataBtn');
    if (delBtn && !delBtn.disabled) {
      delBtn.onclick = () => {
        if (!confirm('Delete "' + (r.label || r.loesch.ref) + '" PERMANENTLY?\\n' +
              fmtN(r.dateien) + ' ' + pluralWort(r.dateien, 'file', 'files') + ', ' + fmtMB(r.bytes) +
              ' of measurement data will be deleted with it - there is no recycle bin.')) return;
        const kind = r.loesch.kind;
        const req = kind === 'job'
          ? fetchJSON('/api/deletedata', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ job_id: r.job_id }),
            })
          : fetchJSON('/api/deleterecording', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ kind: kind, ref: r.loesch.ref }),
            });
        req.then(loadJobs).catch(e => alert(e.message));
      };
    }
    // T-922b: beide SigMF-Knoepfe haengen am selben Weg, nur das Flag
    // unterscheidet sie. Kein !disabled-Test noetig wie beim Loeschen -
    // ein disabled-Knopf loest ohnehin kein click aus.
    const sigmfBtn = tr.querySelector('.sigmfBtn');
    if (sigmfBtn) sigmfBtn.onclick = () => sigmfExport(r, false);
    const sigmfDataBtn = tr.querySelector('.sigmfDataBtn');
    if (sigmfDataBtn) sigmfDataBtn.onclick = () => sigmfExport(r, true);
    tbody.appendChild(tr);
  });
  if (truncated) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="' + cols.length + '" class="empty"><a href="#" id="recShowAll">show ' +
      (rows.length - LIMIT) + ' more</a></td>';
    tr.querySelector('#recShowAll').onclick = ev => { ev.preventDefault(); recShowAll = true; renderRecordings(); };
    tbody.appendChild(tr);
  }
}

el('searchInput').oninput = e => {
  searchTerm = e.target.value.trim().toLowerCase();
  recShowAll = false;
  renderRecordings();
};

loadJobs();
setInterval(loadJobs, 5000);

// ---- MSCAN-Scan-Definitionen (T-1101: vormals eigene Seite /mscan) ----
let allMscanJobs = [];
let mscanErrors = [];
let mscanSearchTerm = '';
let mscanSortKey = 'modified_at';
let mscanSortDir = -1;

// Alle Spalten ausser der Aktionsspalte sind sortierbar (Muster
// recColumns()/buildRecHead auf "/"), mit eigenen Variablen
// mscanSortKey/mscanSortDir statt der dortigen sortKey/sortDir.
const MSCAN_COLUMNS = [
  { key: 'id', label: 'ID', sortable: true,
    sort: j => j.id,
    cell: j => '<a href="/mscan/job?id=' + encodeURIComponent(j.id) + '">' + esc(j.id) + '</a>' },
  { key: 'title', label: 'Title', sortable: true,
    sort: j => (j.title || '').toLowerCase(),
    cell: j => esc(j.title) },
  { key: 'mode', label: 'Mode', sortable: true,
    sort: j => j.mode || '',
    cell: j => esc(labelOf('mode', j.mode)) },
  { key: 'notes', label: 'Note', sortable: true,
    sort: j => (j.notes || '').toLowerCase(),
    cell: j => {
      const notes = j.notes || '';
      const kurz = notes.length > 60 ? notes.slice(0, 60) + '…' : notes;
      return '<span title="' + esc(notes) + '">' + esc(kurz) + '</span>';
    } },
  { key: 'channel_count', label: 'Channels', sortable: true, cls: 'num',
    sort: j => j.channel_count,
    cell: j => j.channel_count },
  { key: 'created_at', label: 'Created', sortable: true,
    sort: j => new Date(j.created_at).getTime(),
    cell: j => fmtDateTime(j.created_at) },
  { key: 'modified_at', label: 'Modified', sortable: true,
    sort: j => new Date(j.modified_at).getTime(),
    cell: j => fmtDateTime(j.modified_at) },
  { key: 'last_run_at', label: 'Last run', sortable: true,
    sort: j => j.last_run_at ? new Date(j.last_run_at).getTime() : 0,
    cell: j => j.last_run_at ? fmtDateTime(j.last_run_at) : '-' },
  { key: 'run_count', label: 'Runs', sortable: true, cls: 'num',
    sort: j => j.run_count,
    cell: j => j.run_count },
  { key: 'edit', label: '', sortable: false,
    cell: j => '<button class="editBtn">Edit</button>' },
  { key: 'del', label: '', sortable: false,
    cell: j => '<button class="delBtn danger">Delete</button>' },
];

function buildMscanHead() {
  const head = el('mscanHead');
  head.innerHTML = '';
  MSCAN_COLUMNS.forEach(col => {
    const th = document.createElement('th');
    if (col.cls) th.classList.add(col.cls);
    let txt = col.label;
    if (col.sortable) {
      th.classList.add('sortable');
      if (mscanSortKey === col.key) txt += ' <span class="arrow">' + (mscanSortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
      th.onclick = () => {
        if (mscanSortKey === col.key) mscanSortDir = -mscanSortDir; else { mscanSortKey = col.key; mscanSortDir = 1; }
        renderMscan();
      };
    }
    th.innerHTML = txt;
    head.appendChild(th);
  });
}

function renderMscan() {
  buildMscanHead();
  const tbody = el('mscanBody');
  tbody.innerHTML = '';

  let rows = allMscanJobs;
  if (mscanSearchTerm) {
    rows = rows.filter(j =>
      (j.id || '').toLowerCase().includes(mscanSearchTerm) ||
      (j.title || '').toLowerCase().includes(mscanSearchTerm) ||
      (j.notes || '').toLowerCase().includes(mscanSearchTerm));
  }
  const col = MSCAN_COLUMNS.find(c => c.key === mscanSortKey && c.sortable) || MSCAN_COLUMNS.find(c => c.sortable);
  rows = rows.slice().sort((a, b) => (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * mscanSortDir);

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="' + MSCAN_COLUMNS.length + '" class="empty">no MSCAN jobs</td></tr>';
  } else {
    rows.forEach(j => {
      const tr = document.createElement('tr');
      tr.innerHTML = MSCAN_COLUMNS.map(c => {
        const cls = c.key === 'notes' ? 'notiz' : (c.cls || '');
        return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + c.cell(j) + '</td>';
      }).join('');
      const editBtn = tr.querySelector('.editBtn');
      if (editBtn) {
        editBtn.onclick = () => { location.href = '/mscan/job?id=' + encodeURIComponent(j.id); };
      }
      const delBtn = tr.querySelector('.delBtn');
      if (delBtn) {
        delBtn.onclick = () => {
          if (!confirm('Really delete MSCAN job "' + j.id + ' – ' + (j.title || '') + '"?')) return;
          fetchJSON('/api/mscan/delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: j.id }),
          }).then(loadMscanJobs).catch(e => alert(e.message));
        };
      }
      tbody.appendChild(tr);
    });
  }

  const errBox = el('errorsBox');
  errBox.innerHTML = mscanErrors.map(e => '<div>' + esc(e) + '</div>').join('');
}

async function loadMscanJobs() {
  let data;
  try {
    data = await fetchJSON('/api/mscan/jobs');
  } catch (e) {
    el('mscanBody').innerHTML = '<tr><td class="empty err">Error: ' + esc(e.message) + '</td></tr>';
    return;
  }
  allMscanJobs = data.jobs;
  mscanErrors = data.errors || [];
  renderMscan();
}

el('mscanSearchInput').oninput = e => {
  mscanSearchTerm = e.target.value.trim().toLowerCase();
  renderMscan();
};

el('createBtn').onclick = () => {
  const title = el('newTitle').value;
  fetchJSON('/api/mscan/create', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title }),
  }).then(job => { location.href = '/mscan/job?id=' + encodeURIComponent(job.id); })
    .catch(e => alert(e.message));
};

loadMscanJobs();
setInterval(loadMscanJobs, 5000);

// T-1101: Auf-/Zuklappzustand des Definitionsabschnitts je Browser merken.
// Reiter Aufzeichnungen / MSCAN-Definitionen (T-1103 Nachtrag). Der Hash
// (#aufzeichnungen / #definitionen) ist die einzige Zustandsquelle - damit
// funktionieren /#definitionen (Redirect von /mscan, Ruecksprung aus der
// Definitionsseite) und Lesezeichen ohne weitere Speicherung.
// T-1110: der Hash traegt zusaetzlich die Filter-Chips als Anhang
// "?art=<v>&herkunft=<v>" (nur bei Wert != 'alle') - hashTab() liefert
// den reinen Tab-Namen (Teil vor "?"), parseFilterParams() die Chip-Werte.
function hashTab() {
  return location.hash.slice(1).split('?')[0];
}
function parseFilterParams(hash) {
  const result = { art: 'alle', herkunft: 'alle' };
  const qIdx = hash.indexOf('?');
  if (qIdx === -1) return result;
  hash.slice(qIdx + 1).split('&').forEach(pair => {
    const eq = pair.indexOf('=');
    const k = eq === -1 ? pair : pair.slice(0, eq);
    const v = eq === -1 ? '' : pair.slice(eq + 1);
    if (k === 'art' && (ART_ORDER.indexOf(v) >= 0 || v === 'iq')) result.art = v;
    if (k === 'herkunft' && ['direkt', 'longrun', 'verwaist'].includes(v)) result.herkunft = v;
  });
  return result;
}
function buildHash(tab, art, herkunft) {
  const parts = [];
  if (art !== 'alle') parts.push('art=' + art);
  if (herkunft !== 'alle') parts.push('herkunft=' + herkunft);
  return '#' + tab + (parts.length ? '?' + parts.join('&') : '');
}
// T-1110 Nachbesserung: sessionStorage-Fallback fuer den Fall, dass :8050
// OHNE Hash neu geladen wird (Rueckkehr ueber den "Zurueck zur Liste"-Link
// von :8070, der keinen Hash traegt) - sonst gingen die Filter-Chips dabei
// verloren, weil sie sonst nur im Hash leben. Traegt der Hash Parameter,
// gewinnt weiterhin der Hash (Lesezeichen bleiben aussagekraeftig).
function applyHashFilters() {
  if (location.hash.indexOf('?') === -1) {
    let stored = {};
    try {
      stored = JSON.parse(sessionStorage.getItem('eb200_recfilter') || '{}') || {};
    } catch (e) { stored = {}; }
    const parts = [];
    if (stored.art) parts.push('art=' + stored.art);
    if (stored.herkunft) parts.push('herkunft=' + stored.herkunft);
    const params = parseFilterParams('?' + parts.join('&'));
    filterArt = params.art;
    filterHerkunft = params.herkunft;
    updateFilterHash();
    return;
  }
  const params = parseFilterParams(location.hash);
  filterArt = params.art;
  filterHerkunft = params.herkunft;
}
function updateFilterHash() {
  history.replaceState(null, '', buildHash(hashTab() || 'aufzeichnungen', filterArt, filterHerkunft));
  try {
    sessionStorage.setItem('eb200_recfilter', JSON.stringify({ art: filterArt, herkunft: filterHerkunft }));
  } catch (e) { /* Storage gesperrt - Filter leben dann nur im Hash */ }
}
// ---- Reiter "Aufbereitung" (T-1609) ----------------------------------------
// Angezeigt wird die Antwort des Workers (:8090) ueber den Same-Origin-Proxy
// /api/prepstatus. Gepollt wird NUR, solange der Reiter sichtbar ist - der
// Worker schlaeft die meiste Zeit, und eine Anfrage je 5 s an einen
// unsichtbaren Reiter waere reine Last.
const PREP_CODE_TEXT = {
  recording_active: 'recording in progress',
  locked: 'being built elsewhere',
  disk_floor: 'below the reserved floor',
  disk_need: 'not enough space',
  time_window: 'time window too short',
  no_meta: 'no metadata',
  unsupported: 'format not supported',
  error: 'error',
  ok: 'ok'
};
const PREP_SPACE_TEXT = {
  space_ok: '', space_tight: 'space is getting tight',
  space_low: 'too little space \\u2013 nothing more will be prepared'
};
let prepTimer = null;

function fmtGiB(b) { return ((b || 0) / (1024 * 1024 * 1024)).toFixed(1) + ' GiB'; }
function fmtSek(s) { return s == null ? '' : Number(s).toFixed(2) + ' s'; }
function prepText(code) { return PREP_CODE_TEXT[code] || code || '?'; }

function renderPrepSpace(sp) {
  const box = el('prepSpace');
  if (!sp || !sp.total_bytes) { box.textContent = ''; return; }
  const belegt = sp.total_bytes - sp.free_bytes;
  const pct = v => (100 * v / sp.total_bytes).toFixed(1) + '%';
  const verf = Math.max(0, sp.available_bytes || 0);
  const warnKlasse = sp.code === 'space_low' ? 'prep-bad'
                   : sp.code === 'space_tight' ? 'prep-warn' : '';
  const hinweis = PREP_SPACE_TEXT[sp.code] || '';
  box.innerHTML =
    '<div class="prep-space">'
    + '<div class="prep-used" style="width:' + pct(belegt) + '"></div>'
    + '<div class="prep-floor" style="width:' + pct(sp.floor_bytes) + '"></div>'
    + '<div class="prep-free" style="width:' + pct(Math.max(0, verf)) + '"></div>'
    + '</div><span class="prep-num">' + fmtGiB(sp.free_bytes) + ' free · floor '
    + fmtGiB(sp.floor_bytes) + ' · reserve ' + fmtGiB(sp.reserve_bytes)
    + ' · available ' + fmtGiB(sp.available_bytes) + '</span>'
    + (hinweis ? ' <span class="' + warnKlasse + '">' + hinweis + '</span>' : '');
}

// Journal: running/done gehoeren zur selben Datei und werden zu EINER Zeile
// zusammengefasst - geschaetzte Dauer aus der running-Zeile, tatsaechliche aus
// der done-Zeile. Nebeneinander zeigen sie, wo die Vorhersage danebenliegt.
function prepRows(journal) {
  const zeilen = [];
  const offen = {};
  (journal || []).forEach(e => {
    if (e.state === 'worker_start') { zeilen.push({ sep: 'Worker started', ts: e.ts }); return; }
    if (e.state === 'space') { zeilen.push({ sep: (e.code === 'space_ok' ? 'space OK' : PREP_SPACE_TEXT[e.code]) || 'Space status: ' + e.code, ts: e.ts }); return; }
    if (e.state === 'running') { offen[e.file] = e; return; }
    const start = offen[e.file];
    delete offen[e.file];
    zeilen.push({ ts: e.ts, file: e.file, state: e.state, code: e.code,
                  seconds: e.seconds, est: (start || {}).est_seconds,
                  quelle: e.meta_source || (start || {}).meta_source });
  });
  Object.values(offen).forEach(e => zeilen.push({
    ts: e.ts, file: e.file, state: 'running', code: e.code,
    est: e.est_seconds, quelle: e.meta_source }));
  return zeilen.reverse();
}

function renderPrepJournal(journal) {
  const body = el('prepBody');
  const zeilen = prepRows(journal);
  if (!zeilen.length) { body.innerHTML = '<tr><td colspan="6" class="empty">nothing has happened yet</td></tr>'; return; }
  body.innerHTML = zeilen.map(z => {
    const zeit = (z.ts || '').replace('T', ' ').replace('+00:00', '');
    if (z.sep) return '<tr class="prep-sep"><td>' + zeit + '</td><td colspan="5">' + z.sep + '</td></tr>';
    const klasse = z.state === 'failed' ? 'prep-bad' : z.state === 'deferred' ? 'prep-warn' : '';
    const text = z.state === 'done' ? 'prepared'
               : z.state === 'running' ? 'running'
               : z.state === 'deferred' ? 'deferred: ' + prepText(z.code)
               : 'failed: ' + prepText(z.code);
    return '<tr><td>' + zeit + '</td><td>' + (z.file || '') + '</td>'
      + '<td class="' + klasse + '">' + text + '</td>'
      + '<td class="prep-num">' + fmtSek(z.seconds) + '</td>'
      + '<td class="prep-num">' + fmtSek(z.est) + '</td>'
      + '<td>' + (z.quelle || '') + '</td></tr>';
  }).join('');
}

async function loadPrepare() {
  let st = null;
  try { st = await fetchJSON('/api/prepstatus'); } catch (e) { st = null; }
  const badge = el('prepBadge');
  if (!st || !st.reachable) {
    el('prepState').textContent = '\\u2013 worker not reachable';
    el('prepSpace').textContent = '';
    el('prepCurrent').textContent = '';
    el('prepCycle').textContent = '';
    el('prepBody').innerHTML = '<tr><td colspan="6" class="empty">Worker not reachable</td></tr>';
    badge.textContent = '';
    return;
  }
  el('prepState').textContent = st.enabled === false
    ? '\\u2013 disabled' : '\\u2013 interval ' + (st.scan_interval_s || 0) + ' s';
  renderPrepSpace(st.space);
  badge.textContent = (st.space && st.space.code !== 'space_ok') ? ' \\u26a0' : '';
  badge.className = st.space && st.space.code === 'space_low' ? 'prep-bad' : 'prep-warn';

  const cur = st.current;
  if (cur) {
    const pr = cur.progress;
    const pct = pr && pr.frac != null ? ' \\u2013 ' + Math.round(pr.frac * 100) + '%' : '';
    el('prepCurrent').innerHTML = '<b>building now:</b> ' + (cur.file || '') + pct;
  } else {
    el('prepCurrent').textContent = 'nothing being built';
  }

  const c = st.last_cycle || {};
  const zur = Object.entries(c.deferred || {})
    .map(([k, v]) => v + '\\u00d7 ' + prepText(k)).join(', ');
  el('prepCycle').innerHTML = '<span class="prep-num">'
    + (c.scanned || 0) + ' scanned · ' + (c.todo || 0) + ' pending · '
    + (c.built || 0) + ' prepared · ' + (c.failed || 0) + ' failed · '
    + fmtSek(c.seconds) + '</span>'
    + (zur ? '<br><span class="prep-warn">deferred: ' + zur + '</span>' : '')
    + (c.budget_exhausted ? '<br><span class="prep-warn">time budget exhausted</span>' : '');
  renderPrepJournal(st.journal);
}

function prepPolling(an) {
  if (prepTimer !== null) { clearInterval(prepTimer); prepTimer = null; }
  if (!an) return;
  loadPrepare();
  prepTimer = setInterval(loadPrepare, 5000);
}

function showTab(name) {
  if (!el(name) || !el(name).classList.contains('tab-pane')) name = 'aufzeichnungen';
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === name));
  document.querySelectorAll('#tabBar .tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  const target = buildHash(name, filterArt, filterHerkunft);
  if (location.hash !== target) history.replaceState(null, '', target);
  prepPolling(name === 'aufbereitung');   // T-1609: nur im sichtbaren Reiter
}
document.querySelectorAll('#tabBar .tab').forEach(b => { b.onclick = () => showTab(b.dataset.tab); });
window.addEventListener('hashchange', () => { applyHashFilters(); showTab(hashTab()); });
applyHashFilters();
showTab(hashTab() || 'aufzeichnungen');
</script>
</body>
</html>
"""


HTML_MSCAN_DETAIL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; MSCAN job</title>
<style>/*COMMON_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  #saveRow { margin:12px 0 4px; }
  .field-row { margin:10px 0; }
  .field-row label { display:block; font-size:0.85em; color:var(--label); margin-bottom:3px; }
  .field-row input[type=text], .field-row textarea { width:400px; }
  .field-row input[type=number] { width:100px; }
  .hint { font-size:0.78em; margin-top:2px; }
  /* T-1115: Die Einstellungen sind eine Lesespalte - ohne Breitenbegrenzung
     laufen die Hinweistexte auf einem breiten Bildschirm einzeilig durch.
     Begrenzt wird NUR der Job-Reiter; Kanal- und Lauftabelle brauchen die
     volle Breite. */
  #pane-job { max-width:820px; }
  #pane-job .field-row { margin:0; padding:14px 0; border-bottom:1px solid var(--border); }  /* T-1111, T-1115 */
  #pane-job .field-row:last-child { border-bottom:none; }
  /* T-1115: Ueberschrift einer Einstellung - hebt sich von den Optionstexten
     der Radios/Checkboxen (inline-Labels) und von den Hinweisen ab. */
  .field-row label.row-title { font-size:1.02em; font-weight:700; color:var(--fg); margin-bottom:6px; }
  td.freq-cell { font-weight:700; }  /* T-1115: Farbe bleibt --fg bzw. --danger in missing-Zeilen */
  .meta-line { color:var(--fg-muted); font-size:0.82em; margin:8px 0 16px; }
  .missing-row { color:var(--danger); }
  #errorLine { color:var(--danger); margin:8px 0; }
  #startRunMsg { margin-left:10px; }
  #runInventurHint { margin:6px 0; }
</style>
</head>
<body>
/*HEADER_DETAIL*/
<div class="top-links">
  <a href="/#definitionen">Recordings / definitions</a>
  <a href="/">Long-run jobs (/)</a>
</div>
<div id="errorLine" style="display:none"></div>

<div id="saveRow">
  <button id="saveBtn">Save</button>
  <span id="saveMsg" style="margin:0 10px;"></span>
  <button id="saveAndListBtn">Save and back to list</button>
</div>

<div id="detailTabs" class="tabbar">
  <button class="tab active" data-pane="pane-job">Job</button>
  <button class="tab" data-pane="pane-kanaele" id="tabKanaele">Channels</button>
  <button class="tab" data-pane="pane-laeufe">Runs</button>
</div>
<section id="pane-job" class="tab-pane active">

<div class="field-row">
  <label class="row-title" for="titleInput">Title</label>
  <input type="text" id="titleInput" maxlength="200">
</div>
<div class="field-row">
  <label class="row-title" for="notesInput">Note</label>
  <textarea id="notesInput" rows="3"></textarea>
</div>
<div class="meta-line" id="metaLine"></div>

<div class="field-row">
  <label class="row-title">Mode</label>
  <label style="display:inline"><input type="radio" name="mode" id="modeInventur"> Inventory &ndash; survey occupancy (listening sample or level only per channel)</label><br>
  <label style="display:inline"><input type="radio" name="mode" id="modeUeberwachung"> Surveillance &ndash; continuous until end of run or stop (COUNt INF); every activity is recorded</label>
  <div class="hint" id="modeHint"></div>
</div>
<div class="field-row" id="inventurStopRow" style="display:none">
  <label class="row-title">End of the inventory</label>
  <label style="display:inline"><input type="radio" name="stopAfter" id="stopCycle"> one cycle &ndash; each channel once (COUNt 1)</label><br>
  <label style="display:inline"><input type="radio" name="stopAfter" id="stopRuntime"> until end of run or stop &ndash; continuous cycles (COUNt INF)</label>
  <div class="hint">One cycle ends by itself at the end-of-cycle marker. Until end of run: the duration is set when the run is started, and each cycle is counted separately &ndash; this shows how often a channel was active over time.</div>
</div>
<div class="field-row">
  <label class="row-title" for="dwellInput">Dwell time (s)</label>
  <input type="number" id="dwellInput" min="0" max="10" step="0.1">
  <label style="display:inline"><input type="checkbox" id="dwellInf"> unlimited (INF)</label>
  <div class="hint">Maximum dwell time per channel while a signal is present (0..10 s). Unlimited: the scan stays as long as the signal is present; the hold time takes over afterwards. Recommended: unlimited.</div>
</div>
<div class="field-row">
  <label class="row-title" for="holdInput">Hold time (s)</label>
  <input type="number" id="holdInput" min="0" max="10" step="0.1">
  <div class="hint">Hold time (0..10 s): the scan stays on the channel this long after the signal ends before moving on. Bridges pauses in speech.</div>
</div>
<div class="field-row">
  <label style="display:inline"><input type="checkbox" id="squelchInput"> Squelch from the channel setting</label>
  <div class="hint">Use the squelch stored in each memory slot instead of one global threshold.</div>
</div>
<div class="field-row">
  <label style="display:inline"><input type="checkbox" id="levelOnlyInput"> levels only (no audio recording)</label>
  <div class="hint">No WAV files are created; what is counted per channel is how often the squelch opens. This is considerably faster: visiting a silent channel costs about 25 ms, an occupied one the dwell time. A short dwell time and hold 0 are recommended.</div>
</div>
<div class="field-row">
  <label class="row-title" for="levelActiveInput">Active threshold (dB&micro;V)</label>
  <input type="number" id="levelActiveInput" min="-30" max="150" step="1" placeholder="empty: every opening counts">
  <div class="hint">A channel counts as active only from this level upwards. The memory slot's squelch has to be low enough for that &ndash; below it the device reports nothing that the threshold could examine.</div>
</div>
<div class="field-row">
  <label class="row-title" for="minRecInput">Min. recording duration (s)</label>
  <input type="number" id="minRecInput" min="0.1" step="0.1">
  <div class="hint">Recordings shorter than this are discarded; a channel counts as active only from a recording &ge; this value. The dwell time must be unlimited or at least as long, otherwise the scan moves on before a recording can count.</div>
</div>

</section>

<section id="pane-kanaele" class="tab-pane">
<div id="estimateLine" class="hint" style="display:none"></div>
<button id="pickBtn">Add channels</button>
<input type="text" id="channelSearchInput" placeholder="Filter label / file / tags ...">
<button id="removeSelectedBtn" class="danger">Remove selected</button>
<table>
  <thead><tr id="channelsHead"></tr></thead>
  <tbody id="channelsBody"></tbody>
</table>

</section>

<section id="pane-laeufe" class="tab-pane">
<div class="field-row">
  <label>Start</label>
  <label style="display:inline"><input type="radio" name="runStart" id="runStartNow" checked> now</label>
  <label style="display:inline"><input type="radio" name="runStart" id="runStartAt"> at time</label>
  <input type="datetime-local" id="runStartTime" disabled>
</div>
<div class="field-row" id="runEndRow">
  <label>End</label>
  <label style="display:inline"><input type="radio" name="runEnd" id="runEndDuration" checked> Duration (min)</label>
  <input type="number" id="runDurationInput" min="1" value="60" style="width:80px">
  <label style="display:inline"><input type="radio" name="runEnd" id="runEndAt"> at time</label>
  <input type="datetime-local" id="runEndTime" disabled>
  <label style="display:inline"><input type="radio" name="runEnd" id="runEndOpen"> until stopped</label>
</div>
<div class="hint" id="runInventurHint" style="display:none"></div>
<button id="startRunBtn">Start run</button>
<span id="startRunMsg"></span>

<table>
  <thead><tr id="runsHead"></tr></thead>
  <tbody id="runsBody"></tbody>
</table>
</section>

<script>/*COMMON_JS*/</script>
<script>
const HOST = window.location.hostname;
const jobId = new URLSearchParams(location.search).get('id') || '';
document.title = 'MoniScope – MSCAN job ' + jobId;

let currentJob = null;
let currentChannels = [];
let maxChannels = 1000;
let channelSearchTerm = '';
let channelSortKey = 'label';
let channelSortDir = 1;

function fmtMHz4(hz) { return hz == null ? '?' : (hz / 1e6).toFixed(4); }
// EB200-Filterstufen reichen bis 0,15 kHz hinab - Ganzzahl-Rundung wuerde
// 1,5 -> "2" und 0,15 -> "0" liefern. Nachkommastellen nur, wo noetig.
function fmtKHz(hz) {
  if (hz == null) return '?';
  const k = hz / 1e3;
  return Number.isInteger(k) ? String(k) : k.toFixed(k < 1 ? 2 : 1);
}

function showError(msg) {
  const box = el('errorLine');
  if (!msg) { box.style.display = 'none'; box.textContent = ''; return; }
  box.style.display = 'block';
  box.textContent = msg;
}

async function loadDetail() {
  if (!jobId) { showError('No job ID in the URL (?id=...).'); return; }
  let data;
  try {
    data = await fetchJSON('/api/mscan/job?id=' + encodeURIComponent(jobId));
  } catch (e) {
    showError(e.message);
    return;
  }
  showError('');
  currentJob = data.job;
  currentChannels = data.channels;
  maxChannels = data.max_channels;

  el('pageTitle').textContent = 'MSCAN job ' + currentJob.id + ' – ' + (currentJob.title || '');
  document.title = 'MoniScope – MSCAN job ' + currentJob.id;
  el('titleInput').value = currentJob.title || '';
  el('notesInput').value = currentJob.notes || '';
  el('metaLine').textContent =
    'Created: ' + fmtDateTime(currentJob.created_at) +
    '  ·  Modified: ' + fmtDateTime(currentJob.modified_at) +
    '  ·  Last run: ' + (currentJob.last_run_at ? fmtDateTime(currentJob.last_run_at) : '-');

  const p = currentJob.params || {};
  el('modeInventur').checked = p.mode === 'inventur';
  el('modeUeberwachung').checked = p.mode !== 'inventur';   // Default ueberwachung
  el('dwellInf').checked = p.dwell_s == null;
  el('dwellInput').value = p.dwell_s == null ? '' : p.dwell_s;
  el('dwellInput').disabled = p.dwell_s == null;
  el('holdInput').value = p.hold_s;
  el('squelchInput').checked = !!p.squelch_from_memory;
  el('minRecInput').value = p.min_recording_s;
  // T-1019c: Vorgabewerte entsprechen dem Verhalten vor T-1019, damit ein
  // Job ohne diese Parameter (Altbestand) unveraendert angezeigt wird.
  el('stopRuntime').checked = p.stop_after === 'runtime';
  el('stopCycle').checked = p.stop_after !== 'runtime';
  el('levelOnlyInput').checked = p.record_audio === false;
  el('levelActiveInput').value = p.level_active_dbuv == null ? '' : p.level_active_dbuv;
  updateModeHint();
  updateInventurRows();

  el('tabKanaele').textContent = 'Channels (' + currentChannels.length + ' / ' + maxChannels + ')';

  renderChannels();
  updateEstimate();
  updateRunFormForMode();
  loadRuns();
}

el('dwellInf').onchange = () => {
  el('dwellInput').disabled = el('dwellInf').checked;
  updateEstimate();
};
el('dwellInput').oninput = updateEstimate;

function updateModeHint() {
  el('modeHint').textContent = el('modeInventur').checked
    ? 'Dwell must be finite (broadcast carriers never close the squelch). Hold 0 recommended.'
    : 'Dwell unlimited recommended; hold bridges pauses in speech.';
}

// T-1019c: Die Ende-Wahl gibt es nur bei der Inventur (die Überwachung laeuft
// ohnehin mit COUNt INF, siehe eb200_core/mscan_job.py STOP_AFTER). Ohne
// Tonaufnahme ist die Mindestdauer bedeutungslos - das Feld wird gesperrt
// statt versteckt, damit der gespeicherte Wert sichtbar bleibt.
function updateInventurRows() {
  el('inventurStopRow').style.display = el('modeInventur').checked ? '' : 'none';
  el('minRecInput').disabled = el('levelOnlyInput').checked;
}

// Umschalten auf "nur Pegel": ein langes Dwell haelt den Rundlauf unnoetig
// auf, wenn ohnehin nur der Pegel zaehlt. Vorschlagswert 0,2 s NUR eintragen,
// wenn der aktuelle Wert dafuer ungeeignet ist (Muster onModeChanged() -
// unbegrenzt, ungueltig oder laenger als 1 s). Ausschalten aendert nichts.
el('levelOnlyInput').onchange = () => {
  if (el('levelOnlyInput').checked) {
    const dwell = parseFloat(el('dwellInput').value);
    if (el('dwellInf').checked || !(dwell > 0) || dwell > 1) {
      el('dwellInf').checked = false;
      el('dwellInput').disabled = false;
      el('dwellInput').value = 0.2;
    }
  }
  updateInventurRows();
  updateEstimate();
};

// Umschalten auf inventur: Dwell-INF abwaehlen/Feld aktivieren, Vorschlagswerte
// NUR eintragen, wenn das jeweilige Feld dadurch ungueltig waere (siehe
// INVENTUR_DEFAULTS in eb200_core/mscan_job.py - Hold bleibt bewusst
// unveraendert). Umschalten auf ueberwachung aendert KEINE Werte.
function onModeChanged() {
  if (el('modeInventur').checked) {
    el('dwellInf').checked = false;
    el('dwellInput').disabled = false;
    let dwell = parseFloat(el('dwellInput').value);
    if (!(dwell > 0)) {
      dwell = 3;
      el('dwellInput').value = dwell;
    }
    const minRec = parseFloat(el('minRecInput').value);
    if (!(minRec <= dwell)) {
      el('minRecInput').value = 0.5;
    }
  }
  updateModeHint();
  updateInventurRows();
  updateEstimate();
}
el('modeInventur').onchange = onModeChanged;
el('modeUeberwachung').onchange = onModeChanged;

// Obere Schaetzung (alle Kanaele belegt): n*(0.1 s Umschaltung + Dwell) -
// nur bei inventur sinnvoll (ueberwachung hat keine feste Laufzeit, die
// haengt am Lauf selbst, siehe Moduldoku eb200_core/mscan_job.py).
function updateEstimate() {
  const line = el('estimateLine');
  if (!el('modeInventur').checked) { line.style.display = 'none'; return; }
  const dwell = parseFloat(el('dwellInput').value);
  const n = currentChannels.length;
  if (!(dwell > 0) || !n) { line.style.display = 'none'; return; }
  const totalS = n * (0.1 + dwell);
  const zeit = totalS < 60 ? '< 1 min' : Math.round(totalS / 60) + ' min';
  line.textContent = 'One cycle: approx. ' + zeit + ' (' + n + ' ' + pluralWort(n, 'channel', 'channels') + ' × dwell ' + dwell +
    ' s when a signal is present, + ~0.1 s per channel change)';
  line.style.display = 'block';
}

// Speichern-Feedback (saveMsg) ist BEWUSST getrennt von showError()/#errorLine:
// #errorLine ist fuer Ladefehler beim Oeffnen der Seite (loadDetail), saveMsg
// nur fuer das Ergebnis eines Speichern-Versuchs, direkt neben den Buttons.
let saveMsgTimer = null;

function setSaveMsg(text, isError) {
  const span = el('saveMsg');
  if (saveMsgTimer) { clearTimeout(saveMsgTimer); saveMsgTimer = null; }
  span.textContent = text;
  span.classList.toggle('err', !!isError);
  span.classList.toggle('ok', !isError);
  if (!isError) {
    // Erfolg blendet nach 4 s wieder aus; ein Fehler bleibt stehen, bis der
    // naechste Versuch (egal ob wieder Fehler oder Erfolg) ihn ersetzt.
    saveMsgTimer = setTimeout(() => { span.textContent = ''; }, 4000);
  }
}

function saveJob() {
  const params = {
    mode: el('modeInventur').checked ? 'inventur' : 'ueberwachung',
    dwell_s: el('dwellInf').checked ? null : parseFloat(el('dwellInput').value),
    hold_s: parseFloat(el('holdInput').value),
    squelch_from_memory: el('squelchInput').checked,
    min_recording_s: parseFloat(el('minRecInput').value),
    // T-1019c: leeres Schwellwertfeld bedeutet "keine zusaetzliche Schwelle"
    // (null), nicht 0 dBuV - parseFloat('') waere NaN und damit ungueltig.
    stop_after: el('stopRuntime').checked ? 'runtime' : 'cycle',
    record_audio: !el('levelOnlyInput').checked,
    level_active_dbuv: el('levelActiveInput').value === ''
      ? null : parseFloat(el('levelActiveInput').value),
  };
  return fetchJSON('/api/mscan/update', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      job_id: jobId, title: el('titleInput').value, notes: el('notesInput').value, params: params,
    }),
  });
}

// Beide Speichern-Buttons waehrend des laufenden POST gesperrt
// (Doppelklick-Schutz) - unabhaengig davon, welcher der beiden geklickt wurde.
function withSaveButtonsDisabled(action) {
  el('saveBtn').disabled = true;
  el('saveAndListBtn').disabled = true;
  const reenable = () => { el('saveBtn').disabled = false; el('saveAndListBtn').disabled = false; };
  return action().then(v => { reenable(); return v; }, e => { reenable(); throw e; });
}

el('saveBtn').onclick = () => {
  withSaveButtonsDisabled(saveJob)
    .then(() => { setSaveMsg('Saved ' + isoTime(new Date()), false); return loadDetail(); })
    .catch(e => setSaveMsg(e.message, true));
};

el('saveAndListBtn').onclick = () => {
  withSaveButtonsDisabled(saveJob)
    .then(() => { location.href = '/#definitionen'; })
    .catch(e => setSaveMsg(e.message, true));
};

el('pickBtn').onclick = () => { location.href = '/mscan/pick?id=' + encodeURIComponent(jobId); };

// Kanaltabelle: fehlende Kataloge (missing=true, Katalog hat sich seit der
// Auswahl geaendert) zeigen Label/Frequenz als "?" und bleiben entfernbar
// (siehe build_mscan_job_detail in manage_jobs.py) - kein Fehlerfall.
const CHANNEL_COLUMNS = [
  { key: 'sel', label: '', sortable: false,
    cell: () => '<input type="checkbox" class="chSel">' },
  { key: 'label', label: 'Label', sortable: true,
    sort: c => (c.label || '').toLowerCase(),
    cell: c => c.missing ? '? (missing from the catalog)' : esc(c.label) },
  { key: 'freq_hz', label: 'Frequency (MHz)', sortable: true, cls: 'num freq-cell',
    sort: c => c.freq_hz || 0,
    cell: c => c.missing ? '?' : fmtMHz4(c.freq_hz) },
  { key: 'demod', label: 'Demod', sortable: true,
    sort: c => c.demod || '',
    cell: c => c.missing ? '' : esc(c.demod) },
  { key: 'bw_hz', label: 'BW (kHz)', sortable: true, cls: 'num',
    sort: c => c.bw_hz || 0,
    cell: c => c.missing ? '' : fmtKHz(c.bw_hz) },
  { key: 'file', label: 'Catalog file', sortable: true,
    sort: c => c.file || '',
    cell: c => c.missing ? '' : esc(c.file) },
  { key: 'tags', label: 'Tags', sortable: false,
    cell: c => c.missing ? '' : esc((c.tags || []).join(', ')) },
  { key: 'del', label: '', sortable: false,
    cell: () => '<button class="chDelBtn danger">Remove</button>' },
];

function buildChannelsHead() {
  const head = el('channelsHead');
  head.innerHTML = '';
  CHANNEL_COLUMNS.forEach(col => {
    const th = document.createElement('th');
    // T-1115: cls kann mehrere Klassen tragen ('num freq-cell') - classList.add
    // wirft bei einem String mit Leerzeichen (InvalidCharacterError).
    if (col.cls) col.cls.split(' ').forEach(k => th.classList.add(k));
    let txt = col.label;
    if (col.sortable) {
      th.classList.add('sortable');
      if (channelSortKey === col.key) txt += ' <span class="arrow">' + (channelSortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
      th.onclick = () => {
        if (channelSortKey === col.key) channelSortDir = -channelSortDir; else { channelSortKey = col.key; channelSortDir = 1; }
        renderChannels();
      };
    }
    th.innerHTML = txt;
    head.appendChild(th);
  });
}

function renderChannels() {
  buildChannelsHead();
  const tbody = el('channelsBody');
  tbody.innerHTML = '';

  let rows = currentChannels;
  if (channelSearchTerm) {
    rows = rows.filter(c => {
      const hay = ((c.label || '') + ' ' + (c.file || '') + ' ' + (c.tags || []).join(' ')).toLowerCase();
      return hay.includes(channelSearchTerm);
    });
  }
  const col = CHANNEL_COLUMNS.find(c => c.key === channelSortKey && c.sortable) || CHANNEL_COLUMNS.find(c => c.sortable);
  rows = rows.slice().sort((a, b) => (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * channelSortDir);

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="' + CHANNEL_COLUMNS.length + '" class="empty">no channels</td></tr>';
    return;
  }
  rows.forEach(c => {
    const tr = document.createElement('tr');
    if (c.missing) tr.classList.add('missing-row');
    tr.innerHTML = CHANNEL_COLUMNS.map(col => {
      const cls = col.cls || '';
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + col.cell(c) + '</td>';
    }).join('');
    tr.querySelector('.chSel').dataset.id = c.id;
    tr.querySelector('.chDelBtn').onclick = () => removeChannels([c.id]);
    tbody.appendChild(tr);
  });
}

function removeChannels(ids) {
  fetchJSON('/api/mscan/channels', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, remove: ids }),
  }).then(loadDetail).catch(e => showError(e.message));
}

el('removeSelectedBtn').onclick = () => {
  const ids = Array.from(document.querySelectorAll('.chSel:checked')).map(cb => cb.dataset.id);
  if (!ids.length) return;
  removeChannels(ids);
};

el('channelSearchInput').oninput = e => {
  channelSearchTerm = e.target.value.trim().toLowerCase();
  renderChannels();
};

// ---- Läufe (T-1005 Session B): Start-Formular + Laufliste ----------------

let currentRuns = [];
let runsSortKey = 'start_iso';
let runsSortDir = -1;
let runsPollTimer = null;

const RUN_COLUMNS = [
  { key: 'start_iso', label: 'Start', sortable: true,
    sort: r => r.start_iso || '',
    cell: r => '<a href="' + ('http://' + location.hostname + ':8060/?run=' + esc(encodeURIComponent(r.run_id))) +
      '" target="eb200_eval_mscan">' + fmtDateTime(r.start_iso) + '</a>' },
  { key: 'end_iso', label: 'End', sortable: true,
    title: 'actual end; for running or scheduled runs the planned end',
    sort: r => r.ended_iso || r.end_iso || '',
    cell: r => r.ended_iso ? fmtDateTime(r.ended_iso) : (r.end_iso ? fmtDateTime(r.end_iso) : 'until stopped') },
  { key: 'state', label: 'State', sortable: true,
    sort: r => labelOf('zustand', r.state), cell: r => esc(labelOf('zustand', r.state)) },
  { key: 'phase', label: 'Phase', sortable: true,
    sort: r => labelOf('phase', (r.stats || {}).phase), cell: r => esc(labelOrDash('phase', (r.stats || {}).phase)) },
  { key: 'cycles', label: 'Cycles', sortable: true, cls: 'num',
    sort: r => (r.stats || {}).cycles || 0, cell: r => (r.stats || {}).cycles || 0 },
  { key: 'recordings', label: 'Recordings', sortable: true, cls: 'num',
    sort: r => (r.stats || {}).recordings || 0, cell: r => (r.stats || {}).recordings || 0 },
  { key: 'active_channels', label: 'active channels', sortable: true, cls: 'num',
    sort: r => (r.stats || {}).active_channels || 0, cell: r => (r.stats || {}).active_channels || 0 },
  { key: 'audio_orphan', label: 'orphaned', sortable: true, cls: 'num',
    title: 'Audio without an open segment between two segments (before and after in the tooltip)',
    sort: r => { const s = r.stats || {};
      return (s.audio_orphan_mid != null ? s.audio_orphan_mid : s.audio_orphan) || 0; },
    cell: r => { const s = r.stats || {};
      const shown = s.audio_orphan_mid != null ? s.audio_orphan_mid : (s.audio_orphan || 0);
      const pre = s.audio_orphan_pre != null ? s.audio_orphan_pre : '–';
      const mid = s.audio_orphan_mid != null ? s.audio_orphan_mid : '–';
      const post = s.audio_orphan_post != null ? s.audio_orphan_post : '–';
      const total = s.audio_orphan != null ? s.audio_orphan : '–';
      return '<span title="before ' + pre + ' · between ' + mid + ' · after ' + post +
        ' · total ' + total + '">' + shown + '</span>'; } },
  { key: 'job_dir', label: 'Folder', sortable: false,
    cell: r => { if (!r.job_dir) return '-';
      const bs = String.fromCharCode(92);
      const idx = Math.max(r.job_dir.lastIndexOf('/'), r.job_dir.lastIndexOf(bs));
      const base = idx >= 0 ? r.job_dir.slice(idx + 1) : r.job_dir;
      return '<code title="' + esc(r.job_dir) + '">' + esc(base) + '</code>'; } },
  { key: 'abort_reason', label: 'Abort reason', sortable: false,
    cell: r => esc(abortText(r)) },
  { key: 'stop', label: '', sortable: false,
    cell: r => {
      // Stopp fuer laufende/geplante Laeufe (wie bisher); Loeschen fuer
      // alle uebrigen Zustaende - zwei Wege wie in der Aufzeichnungen-
      // Tabelle auf "/": geplant/verpasst ohne Messdaten via /api/delete,
      // Endzustaende inkl. Messdaten via /api/deletedata (siehe deleteRun).
      const btns = [];
      if (r.state === 'laeuft' || r.state === 'geplant')
        btns.push('<button class="stopRunBtn danger">Stop</button>');
      if (r.state === 'geplant' || r.state === 'verpasst')
        btns.push('<button class="delRunBtn danger" title="Delete run (no measurement data present)">Delete</button>');
      if (r.state === 'beendet' || r.state === 'gestoppt' || r.state === 'abgebrochen')
        btns.push('<button class="delRunBtn danger" title="Delete run and its measurement data permanently">Delete</button>');
      return btns.join(' ');
    } },
];

function buildRunsHead() {
  const head = el('runsHead');
  head.innerHTML = '';
  RUN_COLUMNS.forEach(col => {
    const th = document.createElement('th');
    if (col.cls) th.classList.add(col.cls);
    if (col.title) th.title = col.title;
    let txt = col.label;
    if (col.sortable) {
      th.classList.add('sortable');
      if (runsSortKey === col.key) txt += ' <span class="arrow">' + (runsSortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
      th.onclick = () => {
        if (runsSortKey === col.key) runsSortDir = -runsSortDir; else { runsSortKey = col.key; runsSortDir = 1; }
        renderRuns();
      };
    }
    th.innerHTML = txt;
    head.appendChild(th);
  });
}

// Stopp laeuft ueber denselben Same-Origin-Proxy wie auf der Long-Run-
// Jobs-Seite (/api/stopjob -> waterfall.py /api/longrun/stop) - EIN
// direkter Browser-fetch() gegen :8000 waere Cross-Origin und wuerde ohne
// CORS-Header (waterfall.py setzt keine, siehe Moduldoku dieser Datei)
// vom Browser blockiert. "ein Empfaenger, ein Job" - der Proxy stoppt
// immer den GERADE aktiven Job, hier also genau diesen Lauf.
function stopRun() {
  if (!confirm('Really stop this run?')) return;
  fetchJSON('/api/stopjob', { method: 'POST' }).catch(() => {}).finally(loadRuns);
}

// Lauf loeschen - derselbe Backend-Weg wie die Aufzeichnungen-Tabelle
// auf "/": Endzustaende -> /api/deletedata (JobStore.delete_with_data,
// entfernt das komplette longrun_-Verzeichnis inkl. Segment-WAVs,
// Storage-Root-Sicherheitsnetz dort); geplant/verpasst -> /api/delete
// (JobStore.delete, es existieren keine Messdaten). Die MSCAN-Job-
// DEFINITION bleibt davon unberuehrt (und umgekehrt: Loeschen der
// Definition laesst Laeufe samt Aufnahmen bestehen, siehe
// handle_mscan_delete()).
function deleteRun(r) {
  const s = r.stats || {};
  const endstate = (r.state === 'beendet' || r.state === 'gestoppt' || r.state === 'abgebrochen');
  const msg = endstate
    ? ('Delete run from ' + fmtDateTime(r.start_iso) + ' PERMANENTLY? – ' +
       ((s.recordings || 0)) + ' ' + pluralWort(s.recordings || 0, 'recording', 'recordings') + ' and every file in the run folder will be deleted with it – there is no recycle bin.')
    : ('Delete run from ' + fmtDateTime(r.start_iso) + '? (no measurement data present)');
  if (!confirm(msg)) return;
  fetchJSON(endstate ? '/api/deletedata' : '/api/delete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: r.run_id }),
  }).then(loadRuns).catch(e => alert(e.message));
}

function renderRuns() {
  buildRunsHead();
  const tbody = el('runsBody');
  tbody.innerHTML = '';
  if (!currentRuns.length) {
    tbody.innerHTML = '<tr><td colspan="' + RUN_COLUMNS.length + '" class="empty">no runs</td></tr>';
    return;
  }
  const col = RUN_COLUMNS.find(c => c.key === runsSortKey && c.sortable) || RUN_COLUMNS.find(c => c.sortable);
  const rows = currentRuns.slice().sort((a, b) =>
    (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * runsSortDir);
  rows.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = RUN_COLUMNS.map(c => {
      const cls = c.cls || '';
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + c.cell(r) + '</td>';
    }).join('');
    const stopBtn = tr.querySelector('.stopRunBtn');
    if (stopBtn) stopBtn.onclick = stopRun;
    const delRunBtn = tr.querySelector('.delRunBtn');
    if (delRunBtn) delRunBtn.onclick = () => deleteRun(r);
    tbody.appendChild(tr);
  });
}

// Auto-Refresh alle 5 s, NUR solange ein Lauf laeuft/geplant ist (Muster
// pollLongrunStatus() in waterfall.py) - ein abgeschlossener Job aendert
// sich nicht mehr, staendiges Nachladen waere reine Serverlast.
async function loadRuns() {
  try {
    currentRuns = await fetchJSON('/api/mscan/runs?id=' + encodeURIComponent(jobId));
  } catch (e) {
    currentRuns = [];
  }
  renderRuns();
  const anyActive = currentRuns.some(r => r.state === 'laeuft' || r.state === 'geplant');
  if (anyActive && !runsPollTimer) {
    runsPollTimer = setInterval(loadRuns, 5000);
  } else if (!anyActive && runsPollTimer) {
    clearInterval(runsPollTimer);
    runsPollTimer = null;
  }
}

function toIsoWithOffset(date) {
  const pad = n => String(n).padStart(2, '0');
  const offsetMin = -date.getTimezoneOffset();
  const sign = offsetMin >= 0 ? '+' : '-';
  const abs = Math.abs(offsetMin);
  const offsetStr = `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
         `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${offsetStr}`;
}

// T-1019c: Nur die Inventur mit EINEM Durchgang endet von selbst und bekommt
// deshalb das Sicherheitsende statt einer Dauer. Mit stop_after 'runtime'
// gelten dieselben Ende-Felder wie bei der Ueberwachung - genauso wertet
// handle_mscan_start() den Payload aus.
function einDurchgangGewaehlt() {
  const p = (currentJob && currentJob.params) || {};
  return p.mode === 'inventur' && (p.stop_after || 'cycle') === 'cycle';
}

function updateRunFormForMode() {
  const isInventur = einDurchgangGewaehlt();
  el('runEndRow').style.display = isInventur ? 'none' : '';
  const hint = el('runInventurHint');
  if (!isInventur) { hint.style.display = 'none'; return; }
  const dwell = currentJob.params.dwell_s;
  const n = currentChannels.length;
  const estimateMin = (dwell && n) ? Math.max(1, Math.round(n * (dwell + 0.1) / 60)) : null;
  hint.style.display = 'block';
  hint.textContent = (estimateMin
    ? `Ends after one cycle, estimated ~${estimateMin} min`
    : 'Ends after one cycle') +
    ' (safety end, in case the end-of-cycle marker does not arrive).';
}

el('runStartNow').onchange = () => { el('runStartTime').disabled = true; };
el('runStartAt').onchange = () => { el('runStartTime').disabled = false; };
el('runEndDuration').onchange = () => { el('runDurationInput').disabled = false; el('runEndTime').disabled = true; };
el('runEndAt').onchange = () => { el('runDurationInput').disabled = true; el('runEndTime').disabled = false; };
el('runEndOpen').onchange = () => { el('runDurationInput').disabled = true; el('runEndTime').disabled = true; };

let startRunMsgTimer = null;
function setStartRunMsg(text, isError) {
  const span = el('startRunMsg');
  if (startRunMsgTimer) { clearTimeout(startRunMsgTimer); startRunMsgTimer = null; }
  span.textContent = text;
  span.classList.toggle('err', !!isError);
  span.classList.toggle('ok', !isError);
  if (!isError) {
    startRunMsgTimer = setTimeout(() => { span.textContent = ''; }, 8000);
  }
}

el('startRunBtn').onclick = () => {
  const payload = { job_id: jobId };
  if (el('runStartAt').checked) {
    if (!el('runStartTime').value) { setStartRunMsg('Start time is missing.', true); return; }
    payload.start_iso = toIsoWithOffset(new Date(el('runStartTime').value));
  }
  // T-1019c: dieselbe Bedingung wie im Formular - sonst waeren die Felder
  // zwar sichtbar, ihr Wert wuerde aber nie mitgeschickt.
  const isInventur = einDurchgangGewaehlt();
  if (!isInventur) {
    if (el('runEndDuration').checked) {
      payload.duration_min = parseFloat(el('runDurationInput').value);
    } else if (el('runEndAt').checked) {
      if (!el('runEndTime').value) { setStartRunMsg('End time is missing.', true); return; }
      payload.end_iso = toIsoWithOffset(new Date(el('runEndTime').value));
    }
    // runEndOpen: weder end_iso noch duration_min gesetzt -> "bis Stopp".
  }
  el('startRunBtn').disabled = true;
  fetchJSON('/api/mscan/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(res => {
    setStartRunMsg('Run ' + res.run_id + ' scheduled for ' + fmtDateTime(res.start_iso) +
      ' – the waterfall server will start it within 30 s', false);
    loadRuns();
  }).catch(e => {
    setStartRunMsg(e.message, true);
  }).finally(() => { el('startRunBtn').disabled = false; });
};

function showDetailPane(name) {
  document.querySelectorAll('#detailTabs .tab').forEach(b =>
    b.classList.toggle('active', b.dataset.pane === name));
  ['pane-job', 'pane-kanaele', 'pane-laeufe'].forEach(id =>
    el(id).classList.toggle('active', id === name));
}
document.querySelectorAll('#detailTabs .tab').forEach(b => {
  b.onclick = () => showDetailPane(b.dataset.pane);
});

loadDetail();
</script>
</body>
</html>
"""


HTML_MSCAN_PICK = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; Select channels</title>
<style>/*COMMON_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  #pickHeader { position:sticky; top:0; background:var(--bg); padding:10px 0; border-bottom:1px solid var(--border);
                z-index:10; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  #pickHeader .counter { font-size:1em; }
  #pickHeader .counter.full { color:var(--danger); }
  .dirty-note { color:var(--warn); font-size:0.85em; }
  #applyError, #maxWarning { color:var(--danger); font-size:0.85em; }
  #loadError { color:var(--danger); margin:8px 0; }
  #filterBar { margin:12px 0; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  #filterResult { color:var(--fg-muted); font-size:0.85em; margin:6px 0; }
  #missingBox { color:var(--danger); margin:6px 0; }
  tr.pick-row { cursor:pointer; }
  tr.pick-row.chosen td { background:var(--success-soft-strong); }
</style>
</head>
<body>
/*HEADER_PICK*/
<div class="top-links">
  <a id="backLink" href="/mscan/job">Back to job</a>
  <a href="/#definitionen">Recordings / definitions</a>
</div>
<div id="loadError" style="display:none"></div>

<div id="pickHeader">
  <span class="counter" id="counterText">Selected: 0 / 0</span>
  <span class="dirty-note" id="dirtyNote"></span>
  <button id="applyBtn" disabled>Apply channels</button>
  <button id="discardBtn">Discard</button>
  <a id="cancelLink" href="/mscan/job">Cancel</a>
  <span id="applyError"></span>
  <span id="maxWarning"></span>
</div>

<div id="filterBar">
  <select id="fileFilter"></select>
  <select id="bandFilter"></select>
  <select id="tagFilter"></select>
  <input type="text" id="textFilter" placeholder="Search label / ID / catalog file ...">
  <label><input type="checkbox" id="onlySelected"> only selected</label>
  <button id="filterOnBtn">Select filtered</button>
  <button id="filterOffBtn">Deselect filtered</button>
</div>
<div id="filterResult"></div>
<div id="missingBox"></div>

<table>
  <thead><tr id="pickHead"></tr></thead>
  <tbody id="pickBody"></tbody>
</table>

<script>/*COMMON_JS*/</script>
<script>
const HOST = window.location.hostname;
const jobId = new URLSearchParams(location.search).get('id') || '';
el('backLink').href = '/mscan/job?id=' + encodeURIComponent(jobId);
el('cancelLink').href = '/mscan/job?id=' + encodeURIComponent(jobId);

let jobData = null;
let catalogData = null;
// original/selected halten Kanal-IDs (Strings), nicht Kanal-Objekte - der
// Katalog kann Tausende Kanaele enthalten, die Auswahl selbst bleibt klein.
let original = new Set();
let selected = new Set();
let MAX = 1000;
let dirty = false;

let pickSortKey = 'freq_hz';
let pickSortDir = 1;

let fileFilterVal = '';
let bandFilterVal = '';
let tagFilterVal = '';
let textFilterVal = '';
let onlySelectedVal = false;

function showLoadError(msg) {
  const box = el('loadError');
  if (!msg) { box.style.display = 'none'; box.textContent = ''; return; }
  box.style.display = 'block';
  box.textContent = msg;
}

// dirty ueber Groesse + Teilmenge statt teurem Set-Vergleich per JSON -
// bei bis zu MAX=1000 Eintraegen faellt das nicht ins Gewicht, bleibt aber
// bewusst einfach (kein zusaetzliches Sortieren/Serialisieren noetig).
function computeDirty() {
  if (selected.size !== original.size) return true;
  for (const id of selected) { if (!original.has(id)) return true; }
  return false;
}

function updateHeader() {
  dirty = computeDirty();
  const counter = el('counterText');
  counter.textContent = 'Selected: ' + selected.size + ' / ' + MAX;
  counter.classList.toggle('full', selected.size === MAX);
  const note = el('dirtyNote');
  if (dirty) {
    let a = 0, r = 0;
    selected.forEach(id => { if (!original.has(id)) a++; });
    original.forEach(id => { if (!selected.has(id)) r++; });
    const teile = [];
    if (a) teile.push(a + ' new');
    if (r) teile.push(r + ' removed');
    note.textContent = teile.length ? '(' + teile.join(', ') + ' – not applied yet)' : '';
  } else {
    note.textContent = '';
  }
  el('applyBtn').disabled = !dirty;
}

function renderMissing() {
  // Kanaele, die im Job stehen (original), aber nicht (mehr) im Katalog -
  // build_mscan_job_detail liefert sie bereits mit missing=true markiert
  // (siehe manage_jobs.py). Hier bewusst NICHT selbst gegen catalogData
  // nachgeschlagen, um die Logik nicht zu duplizieren. Sie tauchen in der
  // Tabelle unten nicht auf (sind nicht im Katalog) und werden NICHT
  // automatisch aus selected entfernt - nur explizit ueber die Job-Seite.
  const missing = (jobData.channels || []).filter(c => c.missing);
  el('missingBox').textContent = missing.length
    ? missing.length + ' selected ' + pluralWort(missing.length, 'channel is', 'channels are') + ' missing from the catalog (remove them on the job page)'
    : '';
}

async function loadPick() {
  if (!jobId) { showLoadError('No job ID in the URL (?id=...).'); return; }
  let jd, cd;
  try {
    [jd, cd] = await Promise.all([
      fetchJSON('/api/mscan/job?id=' + encodeURIComponent(jobId)),
      fetchJSON('/api/mscan/catalog'),
    ]);
  } catch (e) {
    showLoadError(e.message);
    return;
  }
  showLoadError('');
  jobData = jd;
  catalogData = cd;
  MAX = jd.max_channels;
  original = new Set(jd.job.channels);
  selected = new Set(original);

  const heading = 'Select channels – ' + jd.job.id + ' ' + (jd.job.title || '');
  el('pageTitle').textContent = heading;
  document.title = 'MoniScope – ' + heading;

  buildFilterOptions();
  updateHeader();
  renderMissing();
  renderPick();
}

function buildFilterOptions() {
  const fileSel = el('fileFilter');
  fileSel.innerHTML = '<option value="">Catalog file/service: all</option>';
  (catalogData.files || []).slice()
    .sort((a, b) => cmpText((a.service || '') + a.name, (b.service || '') + b.name))
    .forEach(f => {
    const opt = document.createElement('option');
    opt.value = f.name;
    opt.textContent = (f.service || '') + ' – ' + f.name;
    fileSel.appendChild(opt);
  });

  const bandSel = el('bandFilter');
  bandSel.innerHTML = '<option value="">Band: all</option>';
  (catalogData.bands || []).slice().sort((a, b) => a.start_hz - b.start_hz).forEach(b => {
    const opt = document.createElement('option');
    opt.value = b.id;
    opt.textContent = b.title + ' (' + (b.start_hz / 1e6).toFixed(3) + '–' + (b.stop_hz / 1e6).toFixed(3) + ' MHz)';
    bandSel.appendChild(opt);
  });

  const tags = new Set();
  (catalogData.channels || []).forEach(c => (c.tags || []).forEach(t => tags.add(t)));
  const tagSel = el('tagFilter');
  tagSel.innerHTML = '<option value="">Tag: all</option>';
  Array.from(tags).sort((a, b) => cmpText(a, b)).forEach(t => {
    const opt = document.createElement('option');
    opt.value = t;
    opt.textContent = t;
    tagSel.appendChild(opt);
  });
}

function bandById(id) {
  return (catalogData.bands || []).find(b => b.id === id);
}

// Filter kombinieren sich per UND - jeder Filter, der gesetzt ist, muss
// zusaetzlich zutreffen.
function passesFilter(c) {
  if (fileFilterVal && c.file !== fileFilterVal) return false;
  if (bandFilterVal) {
    const band = bandById(bandFilterVal);
    if (!band || c.freq_hz < band.start_hz || c.freq_hz > band.stop_hz) return false;
  }
  if (tagFilterVal && !(c.tags || []).includes(tagFilterVal)) return false;
  if (textFilterVal) {
    const hay = ((c.label || '') + ' ' + (c.id || '') + ' ' + (c.file || '')).toLowerCase();
    if (!hay.includes(textFilterVal)) return false;
  }
  if (onlySelectedVal && !selected.has(c.id)) return false;
  return true;
}

// Sortier-Muster wie MSCAN_COLUMNS/buildMscanHead auf "/" - eigene
// Variablen pickSortKey/pickSortDir statt der dortigen mscanSortKey/-Dir.
const PICK_COLUMNS = [
  { key: 'sel', label: '', sortable: false },
  { key: 'label', label: 'Label', sortable: true, sort: c => (c.label || '').toLowerCase() },
  { key: 'freq_hz', label: 'Frequency (MHz)', sortable: true, cls: 'num', sort: c => c.freq_hz || 0 },
  { key: 'demod', label: 'Demod', sortable: true, sort: c => c.demod || '' },
  { key: 'bw_hz', label: 'BW (kHz)', sortable: true, cls: 'num', sort: c => c.bw_hz || 0 },
  { key: 'service', label: 'Service', sortable: true, sort: c => (c.service || '').toLowerCase() },
  { key: 'file', label: 'Catalog file', sortable: true, sort: c => (c.file || '').toLowerCase() },
  { key: 'tags', label: 'Tags', sortable: false },
  { key: 'raster', label: 'Channel group', sortable: true, sort: c => c.raster ? 1 : 0 },
];

function buildPickHead() {
  const head = el('pickHead');
  head.innerHTML = '';
  PICK_COLUMNS.forEach(col => {
    const th = document.createElement('th');
    if (col.cls) th.classList.add(col.cls);
    if (col.key === 'sel') {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.id = 'pickAllCb';
      cb.title = 'Select all visible';
      cb.onchange = () => { if (cb.checked) selectVisible(); else deselectVisible(); };
      th.appendChild(cb);
      head.appendChild(th);
      return;
    }
    let txt = col.label;
    if (col.sortable) {
      th.classList.add('sortable');
      if (pickSortKey === col.key) txt += ' <span class="arrow">' + (pickSortDir > 0 ? '&#9650;' : '&#9660;') + '</span>';
      th.onclick = () => {
        if (pickSortKey === col.key) pickSortDir = -pickSortDir; else { pickSortKey = col.key; pickSortDir = 1; }
        renderPick();
      };
    }
    th.innerHTML = txt;
    head.appendChild(th);
  });
}

function visibleChannels() {
  const rows = (catalogData.channels || []).filter(passesFilter);
  const col = PICK_COLUMNS.find(c => c.key === pickSortKey && c.sortable) || PICK_COLUMNS.find(c => c.sortable);
  rows.sort((a, b) => (col.sort(a) > col.sort(b) ? 1 : col.sort(a) < col.sort(b) ? -1 : 0) * pickSortDir);
  return rows;
}

let maxWarningTimer = null;
function showMaxWarning() {
  const span = el('maxWarning');
  span.textContent = 'Maximum ' + MAX + ' reached';
  if (maxWarningTimer) clearTimeout(maxWarningTimer);
  maxWarningTimer = setTimeout(() => { span.textContent = ''; }, 3000);
}

// Liefert true, wenn der Toggle tatsaechlich stattgefunden hat - false bei
// einem versuchten Hinzufuegen ueber MAX hinaus (Checkbox bleibt aus).
function toggleChannel(id) {
  if (selected.has(id)) {
    selected.delete(id);
    return true;
  }
  if (selected.size >= MAX) {
    showMaxWarning();
    return false;
  }
  selected.add(id);
  return true;
}

function onRowToggle(c, tr, cb) {
  const ok = toggleChannel(c.id);
  cb.checked = selected.has(c.id);
  tr.classList.toggle('chosen', selected.has(c.id));
  if (ok) updateHeader();
}

function renderPick() {
  buildPickHead();
  const tbody = el('pickBody');
  tbody.innerHTML = '';

  const rows = visibleChannels();
  el('filterResult').textContent = rows.length + ' ' + pluralWort(rows.length, 'channel', 'channels') + ' visible (of ' + (catalogData.channels || []).length + ')';

  const frag = document.createDocumentFragment();
  rows.forEach(c => {
    const tr = document.createElement('tr');
    tr.className = 'pick-row';
    if (selected.has(c.id)) tr.classList.add('chosen');

    const tdSel = document.createElement('td');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selected.has(c.id);
    tdSel.appendChild(cb);
    tr.appendChild(tdSel);

    const tdLabel = document.createElement('td');
    tdLabel.textContent = c.label || '';
    tr.appendChild(tdLabel);

    const tdFreq = document.createElement('td');
    tdFreq.className = 'num';
    tdFreq.textContent = c.freq_hz == null ? '' : (c.freq_hz / 1e6).toFixed(4);
    tr.appendChild(tdFreq);

    const tdDemod = document.createElement('td');
    tdDemod.textContent = c.demod || '';
    tr.appendChild(tdDemod);

    const tdBw = document.createElement('td');
    tdBw.className = 'num';
    tdBw.textContent = c.bw_hz == null ? '' : (c.bw_hz / 1e3).toFixed(0);
    tr.appendChild(tdBw);

    const tdService = document.createElement('td');
    tdService.textContent = c.service || '';
    tr.appendChild(tdService);

    const tdFile = document.createElement('td');
    tdFile.textContent = c.file || '';
    tr.appendChild(tdFile);

    const tdTags = document.createElement('td');
    tdTags.textContent = (c.tags || []).join(', ');
    tr.appendChild(tdTags);

    const tdRaster = document.createElement('td');
    tdRaster.textContent = c.raster ? 'R' : '';
    if (c.raster) tdRaster.title = 'channel generated from a channel group';
    tr.appendChild(tdRaster);

    // stopPropagation auf der Checkbox: sonst toggelt ein Klick auf sie
    // ZWEIMAL (einmal ueber den click-Handler der Checkbox selbst, einmal
    // ueber den Zeilen-Klick, der als naechstes bubbelt).
    cb.onclick = e => { e.stopPropagation(); onRowToggle(c, tr, cb); };
    tr.onclick = () => onRowToggle(c, tr, cb);

    frag.appendChild(tr);
  });
  tbody.appendChild(frag);

  const pickAllCb = document.getElementById('pickAllCb');
  if (pickAllCb) {
    const n = rows.length;
    const nSel = rows.filter(c => selected.has(c.id)).length;
    pickAllCb.checked = n > 0 && nSel === n;
    pickAllCb.indeterminate = nSel > 0 && nSel < n;
  }
}

el('fileFilter').onchange = e => { fileFilterVal = e.target.value; renderPick(); };
el('bandFilter').onchange = e => { bandFilterVal = e.target.value; renderPick(); };
el('tagFilter').onchange = e => { tagFilterVal = e.target.value; renderPick(); };
el('textFilter').oninput = e => { textFilterVal = e.target.value.trim().toLowerCase(); renderPick(); };
el('onlySelected').onchange = e => { onlySelectedVal = e.target.checked; renderPick(); };

function selectVisible() {
  const rows = visibleChannels();
  let skipped = 0;
  rows.forEach(c => {
    if (selected.has(c.id)) return;
    if (selected.size >= MAX) { skipped++; return; }
    selected.add(c.id);
  });
  if (skipped > 0) showMaxWarning();
  updateHeader();
  renderPick();
}

function deselectVisible() {
  visibleChannels().forEach(c => selected.delete(c.id));
  updateHeader();
  renderPick();
}

el('filterOnBtn').onclick = () => selectVisible();

el('filterOffBtn').onclick = () => deselectVisible();

el('discardBtn').onclick = () => {
  selected = new Set(original);
  updateHeader();
  renderPick();
};

el('applyBtn').onclick = () => {
  if (!dirty) return;
  const add = Array.from(selected).filter(id => !original.has(id));
  const remove = Array.from(original).filter(id => !selected.has(id));
  el('applyBtn').disabled = true;
  el('discardBtn').disabled = true;
  el('applyError').textContent = '';
  fetchJSON('/api/mscan/channels', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, add: add, remove: remove }),
  }).then(() => {
    dirty = false;   // window.onbeforeunload prueft dirty bei jedem Aufruf
    location.href = '/mscan/job?id=' + encodeURIComponent(jobId);
  }).catch(e => {
    el('applyError').textContent = e.message;
    el('discardBtn').disabled = false;
    updateHeader();   // setzt applyBtn.disabled wieder passend zu dirty
  });
};

// Registriert EINMAL - prueft dirty bei jedem Unload-Versuch neu (Tab
// schliessen, Adresse eintippen, Abbrechen-Link, erfolgreiches
// Uebernehmen). Nach erfolgreichem Uebernehmen wird dirty=false gesetzt
// (oben), danach warnt dieser Handler bei der eigenen Weiterleitung nicht.
window.onbeforeunload = () => {
  if (dirty) return 'There are unsaved changes to the channel selection.';
};

loadPick();
</script>
</body>
</html>
"""


HTML_MSCAN_IMPORT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; Channels into MSCAN job</title>
<style>/*COMMON_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  #importHeading { margin:14px 0 8px; }
  #importMissingBox { color:var(--danger); margin:6px 0; }
  #importError { color:var(--danger); font-size:0.85em; margin-left:10px; }
  .ziel-row { margin:10px 0; display:flex; align-items:center; gap:8px; }
  .ziel-row input[type=text] { margin-left:6px; }
  .ziel-row select { margin-left:6px; }
</style>
</head>
<body>
/*HEADER_IMPORT*/
<div class="top-links">
  <a href="/#definitionen">Recordings / definitions</a>
</div>

<h2 id="importHeading">Apply channels</h2>
<div id="importMissingBox"></div>

<table>
  <thead><tr><th>id</th><th>Label</th><th>MHz</th></tr></thead>
  <tbody id="importBody"></tbody>
</table>

<div class="ziel-row">
  <label><input type="radio" name="ziel" value="neu" checked> new job</label>
  <input type="text" id="newTitle" value="">
</div>
<div class="ziel-row">
  <label><input type="radio" name="ziel" value="bestehend"> existing job</label>
  <select id="targetJobSelect"></select>
</div>

<p>
  <button id="applyBtn">Apply channels</button>
  <button id="cancelBtn" type="button">Cancel</button>
  <span id="importError"></span>
</p>

<script>/*COMMON_JS*/</script>
<script>
// Platzhalter mit gueltigem Vorgabewert: die Rohvorlage muss fuer sich
// genommen gueltiges JavaScript sein, weil tools/check_js_blocks.py sie VOR
// jeder Ersetzung prueft. Ersetzt wird daher der laengere Text einschliesslich
// des []. Faellt die Ersetzung aus, bleibt eine leere Liste stehen und die
// Seite zeigt "Apply 0 channels", statt zu brechen.
const IMPORT_ITEMS = [] /*IMPORT_ITEMS*/;
const IMPORT_MISSING = [] /*IMPORT_MISSING*/;

function renderImportItems() {
  const heading = 'Apply ' + IMPORT_ITEMS.length + ' ' + pluralWort(IMPORT_ITEMS.length, 'channel', 'channels');
  el('importHeading').textContent = heading;
  document.title = 'MoniScope – ' + heading;

  const tbody = el('importBody');
  tbody.innerHTML = '';
  const frag = document.createDocumentFragment();
  IMPORT_ITEMS.forEach(it => {
    const tr = document.createElement('tr');

    const tdId = document.createElement('td');
    tdId.textContent = it.id;
    tr.appendChild(tdId);

    const tdLabel = document.createElement('td');
    tdLabel.textContent = it.label || '';
    tr.appendChild(tdLabel);

    const tdFreq = document.createElement('td');
    tdFreq.className = 'num';
    tdFreq.textContent = it.freq_hz == null ? '' : (it.freq_hz / 1e6).toFixed(4);
    tr.appendChild(tdFreq);

    frag.appendChild(tr);
  });
  tbody.appendChild(frag);

  el('importMissingBox').textContent = IMPORT_MISSING.length
    ? IMPORT_MISSING.length + ' ' + pluralWort(IMPORT_MISSING.length, 'unknown ID', 'unknown IDs') + ' not in the catalog - skipped: ' + IMPORT_MISSING.join(', ')
    : '';
}

async function loadJobOptions() {
  let data;
  try {
    data = await fetchJSON('/api/mscan/jobs');
  } catch (e) {
    return;   // Auswahlliste bleibt leer, "new job" ist ohnehin voreingestellt
  }
  const sel = el('targetJobSelect');
  sel.innerHTML = '';
  (data.jobs || []).forEach(j => {
    const opt = document.createElement('option');
    opt.value = j.id;
    opt.textContent = (j.title || j.id) + ' (' + j.id + ', ' + j.channel_count + ' ' + pluralWort(j.channel_count, 'channel', 'channels') + ')';
    sel.appendChild(opt);
  });
}

function updateZielUI() {
  const neu = document.querySelector('input[name="ziel"]:checked').value === 'neu';
  el('newTitle').disabled = !neu;
  el('targetJobSelect').disabled = neu;
}
document.querySelectorAll('input[name="ziel"]').forEach(r => { r.onchange = updateZielUI; });
updateZielUI();

const heute = new Date().toISOString().slice(0, 10);
el('newTitle').value = 'Marker ' + heute;

el('cancelBtn').onclick = () => { location.href = '/#definitionen'; };

el('applyBtn').onclick = () => {
  const ids = IMPORT_ITEMS.map(it => it.id);
  if (ids.length === 0) {
    el('importError').textContent = 'No valid channels to apply.';
    return;
  }
  const ziel = document.querySelector('input[name="ziel"]:checked').value;
  el('applyBtn').disabled = true;
  el('importError').textContent = '';

  function addChannels(jobId) {
    return fetchJSON('/api/mscan/channels', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId, add: ids }),
    }).then(() => { location.href = '/mscan/job?id=' + encodeURIComponent(jobId); });
  }

  let step;
  if (ziel === 'neu') {
    step = fetchJSON('/api/mscan/create', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: el('newTitle').value }),
    }).then(job => addChannels(job.id));
  } else {
    step = addChannels(el('targetJobSelect').value);
  }
  step.catch(e => {
    el('importError').textContent = e.message;
    el('applyBtn').disabled = false;
  });
};

renderImportItems();
loadJobOptions();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Seiten zusammensetzen (COMMON_JS/COMMON_CSS/THEME_HEAD/HEADER_* einsetzen) -
# am Modulende, nachdem alle Templates sowie COMMON_JS/COMMON_CSS definiert
# sind. T-1400: THEME_HEAD_JS liest das Theme-Cookie im <head>; die Kopfleiste
# (header_html) ersetzt das fruehere <h1> samt Status-Link.
# ---------------------------------------------------------------------------
def _assemble(page: str, header: str) -> str:
    return (page.replace("/*COMMON_CSS*/", COMMON_CSS)
                .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                .replace("/*COMMON_JS*/", COMMON_JS)
                .replace("/*HEADER_PAGE*/", header)
                .replace("/*HEADER_DETAIL*/", header)
                .replace("/*HEADER_PICK*/", header)
                .replace("/*HEADER_IMPORT*/", header))

HTML_PAGE = _assemble(HTML_PAGE, header_html("Recordings"))
HTML_MSCAN_DETAIL = _assemble(HTML_MSCAN_DETAIL, header_html("MSCAN job", view_id="pageTitle"))
HTML_MSCAN_PICK = _assemble(HTML_MSCAN_PICK, header_html("Select channels", view_id="pageTitle"))
HTML_MSCAN_IMPORT = _assemble(HTML_MSCAN_IMPORT, header_html("Channels into MSCAN job"))


# ---------------------------------------------------------------------------
# HTTP-Handler
# ---------------------------------------------------------------------------

def _error_payload(exc) -> dict:
    """Fehlerantwort aus einer Ausnahme (T-1802c2).

    Traegt die Ausnahme einen sprachneutralen code (eb200_core.recording_index),
    gehen code und params an den Client, der daraus den Text formuliert. Alle
    uebrigen Ausnahmen (ValueError aus mscan_job, KeyError, ...) liefern wie
    bisher error mit str(exc) - der Rueckfall bleibt, bis auch sie umgestellt sind.
    """
    code = getattr(exc, "code", None)
    if code:
        return {"error_code": code, "error_params": getattr(exc, "params", {}) or {}}
    return {"error": str(exc)}


class Handler(http.server.BaseHTTPRequestHandler):
    store: JobStore = None
    mscan_store: MscanJobStore = None

    def log_message(self, fmt, *args):
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML_PAGE)
        elif parsed.path == "/mscan":
            # T-1101: Definitionsliste liegt jetzt auf "/" - alte Lesezeichen umleiten.
            self.send_response(302)
            self.send_header("Location", "/#definitionen")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif parsed.path == "/mscan/job":
            # Die Job-ID liegt im Query-String (?id=...) - ausgewertet vom
            # Browser-JS dieser Seite (siehe HTML_MSCAN_DETAIL), NICHT hier:
            # die statische Seite ist fuer jede ID identisch, /api/mscan/job
            # liefert die eigentlichen Daten.
            self._send_html(HTML_MSCAN_DETAIL)
        elif parsed.path == "/mscan/import":
            self._handle_mscan_import(parse_qs(parsed.query).get("ids", [""])[0])
        elif parsed.path == "/mscan/pick":
            # Die Job-ID liegt wie bei /mscan/job im Query-String (?id=...) -
            # ausgewertet vom Browser-JS (siehe HTML_MSCAN_PICK), nicht hier.
            self._send_html(HTML_MSCAN_PICK)
        elif parsed.path == "/api/jobs":
            self._send_json(build_jobs_view(self.store))
        elif parsed.path == "/api/recordings":
            self._send_json(build_recordings_view(self.store, self.mscan_store))
        elif parsed.path == "/api/prepstatus":
            self._send_json(_proxy_get_prepare_status())
        elif parsed.path == "/api/livestatus":
            # Same-Origin-Proxy gegen den Controller (siehe Moduldoku,
            # Befund 1) - umgeht das Browser-CORS-Problem gegen :8000.
            self._send_json(_proxy_get_longrun_status())
        elif parsed.path == "/api/mscan/jobs":
            self._send_json(build_mscan_jobs_view(self.mscan_store, self.store))
        elif parsed.path == "/api/mscan/job":
            params = parse_qs(parsed.query)
            job_id = params.get("id", [""])[0]
            try:
                result = build_mscan_job_detail(self.mscan_store, load_catalog_cached(),
                                                 job_id, self.store)
            except ValueError as exc:
                self._send_json(_error_payload(exc), status=400)
                return
            except KeyError as exc:
                self._send_json(_error_payload(exc), status=404)
                return
            self._send_json(result)
        elif parsed.path == "/api/mscan/catalog":
            # Fuer den Kanalwaehler (Session B) - hier schon bereitgestellt.
            self._send_json(load_catalog_cached().to_client_json())
        elif parsed.path == "/api/mscan/runs":
            params = parse_qs(parsed.query)
            mscan_job_id = params.get("id", [""])[0]
            self._send_json(build_mscan_runs(self.store, mscan_job_id))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/delete":
            self._handle_mutation(handle_delete)
        elif parsed.path == "/api/deletedata":
            self._handle_mutation(handle_delete_data)
        elif parsed.path == "/api/deleterecording":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                result = handle_delete_recording(self.store, payload)
            except RecordingLockedError as exc:
                self._send_json(_error_payload(exc), status=409)
                return
            except (RecordingNotDeletableError, ValueError, KeyError) as exc:
                self._send_json(_error_payload(exc), status=400)
                return
            self._send_json(result)
        elif parsed.path == "/api/sigmfexport":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                result = handle_sigmf_export(self.store, payload)
            except RecordingLockedError as exc:
                self._send_json(_error_payload(exc), status=409)
                return
            except (RecordingNotDeletableError, ValueError, KeyError) as exc:
                self._send_json(_error_payload(exc), status=400)
                return
            self._send_json(result)
        elif parsed.path == "/api/retry":
            self._handle_mutation(handle_retry)
        elif parsed.path == "/api/joblabel":
            self._handle_label()
        elif parsed.path == "/api/reclabel":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                result = handle_reclabel(self.store, payload)
            except (RecordingNotDeletableError, ValueError, KeyError) as exc:
                self._send_json(_error_payload(exc), status=400)
                return
            self._send_json(result)
        elif parsed.path == "/api/stopjob":
            # Same-Origin-Proxy gegen den Controller (siehe Moduldoku).
            self._send_json(_proxy_stop_longrun_job())
        elif parsed.path == "/api/mscan/create":
            self._handle_mscan_create()
        elif parsed.path == "/api/mscan/update":
            self._handle_mscan_update()
        elif parsed.path == "/api/mscan/channels":
            self._handle_mscan_channels()
        elif parsed.path == "/api/mscan/delete":
            self._handle_mscan_delete()
        elif parsed.path == "/api/mscan/start":
            self._handle_mscan_start()
        elif parsed.path == "/mscan/import":
            self._handle_mscan_import(self._read_form_field("ids"))
        else:
            self.send_response(404)
            self.end_headers()

    def _read_json_body(self, max_len: int = 8192):
        """Liest und parst den JSON-Body. Liefert das payload-dict, oder
        None (nachdem bereits eine 400-Antwort gesendet wurde) bei
        ungueltiger Anfrage - gemeinsame Grundlage fuer alle POST-Handler
        dieser Seite (Muster /api/label in analyze_sessions.py)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > max_len:
            self._send_json({"error_code": ERR_INVALID_REQUEST}, status=400)
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error_code": ERR_INVALID_REQUEST}, status=400)
            return None

    def _handle_mutation(self, func) -> None:
        """Liest {"job_id": ...} aus dem JSON-Body, ruft func(store, job_id)
        auf und uebersetzt ValueError/KeyError in HTTP 400 mit Klartext -
        gemeinsame Fehlerbehandlung fuer /api/delete und /api/retry."""
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            job_id = payload["job_id"]
        except (KeyError, TypeError):
            self._send_json({"error_code": ERR_INVALID_REQUEST}, status=400)
            return
        try:
            result = func(self.store, job_id)
        except (ValueError, KeyError) as exc:
            self._send_json(_error_payload(exc), status=400)
            return
        self._send_json(result)

    def _handle_label(self) -> None:
        """POST /api/joblabel - {"job_id": ..., "label": ...}. Wie
        _handle_mutation(), aber mit dem zusaetzlichen Feld label (das
        fehlen darf - dann wird die Beschriftung geloescht, siehe
        JobStore.update_label())."""
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            job_id = payload["job_id"]
        except (KeyError, TypeError):
            self._send_json({"error_code": ERR_INVALID_REQUEST}, status=400)
            return
        try:
            result = handle_label(self.store, job_id, payload.get("label", ""))
        except (ValueError, KeyError) as exc:
            self._send_json(_error_payload(exc), status=400)
            return
        self._send_json(result)

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, page: str) -> None:
        """Liefert eine der drei eingebetteten Seiten (HTML_PAGE/
        HTML_MSCAN_DETAIL/HTML_MSCAN_PICK) aus - vorher inline im
        "/"-Zweig von do_GET(), jetzt gemeinsame Hilfsmethode fuer alle
        Seiten-Routen."""
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- MSCAN-Job-Verwaltung (T-1003) -----------------------------------

    def _mscan_call(self, func, *args) -> None:
        """Ruft func(*args) auf und uebersetzt ValueError/KeyError in HTTP
        400 mit Klartext - gemeinsame Fehlerbehandlung fuer alle
        /api/mscan/*-Mutationen (Muster _handle_mutation/_handle_label)."""
        try:
            result = func(*args)
        except (ValueError, KeyError) as exc:
            self._send_json(_error_payload(exc), status=400)
            return
        self._send_json(result)

    def _handle_mscan_create(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        self._mscan_call(handle_mscan_create, self.mscan_store, load_catalog_cached(), payload)

    def _handle_mscan_update(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        self._mscan_call(handle_mscan_update, self.mscan_store, load_catalog_cached(), payload)

    def _handle_mscan_channels(self) -> None:
        # max_len erhoeht (1000 Kanal-IDs moeglich, siehe MAX_CHANNELS in
        # eb200_core/mscan_job.py) - der Default 8192 reicht dafuer nicht.
        payload = self._read_json_body(65536)
        if payload is None:
            return
        self._mscan_call(handle_mscan_channels, self.mscan_store, load_catalog_cached(), payload)

    def _handle_mscan_delete(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        self._mscan_call(handle_mscan_delete, self.mscan_store, self.store, payload)

    def _handle_mscan_start(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        self._mscan_call(handle_mscan_start, self.mscan_store, self.store,
                          load_catalog_cached(), payload)

    def _read_form_field(self, name: str, max_len: int = 65536) -> str:
        """
        Liest den urlencodierten POST-Koerper (Content-Length, Obergrenze
        max_len) und liefert das genannte Feld oder "" - Gegenstueck zu
        _read_json_body fuer /mscan/import (T-1016a/b): die Seite wird per
        normalem Formular-POST von :8010 aufgerufen (Portgrenze ohne
        CORS-Freigabe, POST statt ?ids= in der URL wegen der
        URL-Laengengrenze bei vielen Kanal-IDs) - kein JSON-Body, daher
        parse_qs statt json.loads. Anders als _read_json_body wird bei
        fehlendem/zu grossem Koerper KEINE Fehlerantwort gesendet, sondern
        still "" geliefert - _handle_mscan_import behandelt eine leere
        ids_raw wie eine leere Uebergabe (0 Kanaele, 0 unbekannte IDs).
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > max_len:
            return ""
        try:
            raw = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            return ""
        return parse_qs(raw).get(name, [""])[0]

    def _handle_mscan_import(self, ids_raw: str) -> None:
        """
        GET /mscan/import?ids=... und POST /mscan/import (Formular-Feld
        "ids", vom Marker-Import auf :8010 - T-1015/T-1016b) loesen
        Katalog-Kanal-IDs auf und liefern die Uebernahmemaske
        (HTML_MSCAN_IMPORT) mit den Ergebnissen eingesetzt. Reine Anzeige -
        das eigentliche Uebernehmen (Job anlegen/waehlen, Kanaele
        hinzufuegen) laeuft client-seitig ueber die vorhandenen
        /api/mscan/create und /api/mscan/channels (siehe HTML_MSCAN_IMPORT).
        """
        seen = set()
        ids = []
        for part in ids_raw.split(","):
            cid = part.strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            ids.append(cid)
        ids = ids[:MAX_CHANNELS]

        # Schutz vor Einschleusung in das eingebettete JSON (IMPORT_ITEMS/
        # IMPORT_MISSING landen als roher Text im HTML) - dasselbe Muster
        # wie die Katalog-Kanal-id selbst (eb200_core/known_freq._ID_RE).
        ids = [cid for cid in ids if re.match(r"^[A-Za-z0-9._-]+$", cid)]

        catalog = load_catalog_cached()
        items = []
        missing = []
        for cid in ids:
            ch = catalog.by_id(cid)
            if ch is None:
                missing.append(cid)
            else:
                items.append({"id": ch["id"], "label": ch["label"], "freq_hz": ch["freq_hz"]})

        page = (HTML_MSCAN_IMPORT
                .replace("[] /*IMPORT_ITEMS*/", json.dumps(items))
                .replace("[] /*IMPORT_MISSING*/", json.dumps(missing)))
        self._send_html(page)


from eb200_core.http_base import QuietThreadingHTTPServer as ThreadingHTTPServer


# ---------------------------------------------------------------------------
# Selbsttest - "python manage_jobs.py --selftest", ohne Server/Geraet
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        index_path = os.path.join(tmp, "jobs", "index.json")
        roots_path = os.path.join(tmp, "storage_roots.json")
        records_dir = os.path.join(tmp, "records")
        os.makedirs(records_dir, exist_ok=True)
        with open(roots_path, "w", encoding="utf-8") as f:
            json.dump([records_dir], f)
        store = JobStore(index_path=index_path, storage_roots_path=roots_path)

        # --- Jobs in verschiedenen Zustaenden anlegen ----------------------
        m_geplant = store.create_job(
            type="dscan", label="Geplant",
            params={"start_hz": 88_000_000, "stop_hz": 100_000_000, "speed": "NORM"},
            start_iso="2099-01-01T10:00:00+01:00", end_iso="2099-01-01T11:00:00+01:00",
            decimation_s=10,
        )
        m_laeuft = store.create_job(
            type="dscan", label="Laeuft",
            params={"start_hz": 100_000_000, "stop_hz": 110_000_000, "speed": "NORM"},
            start_iso="2020-01-01T10:00:00+01:00", end_iso="2020-01-01T12:00:00+01:00",
            decimation_s=5,
        )
        store.set_state(m_laeuft["job_id"], "laeuft")
        store.heartbeat(m_laeuft["job_id"], stats={"sweeps": 5, "frames": 1, "bytes": 100}, files=["a.specbin"])

        m_beendet = store.create_job(
            type="dscan", label="Beendet",
            params={"start_hz": 120_000_000, "stop_hz": 130_000_000, "speed": "NORM"},
            start_iso="2020-02-01T10:00:00+01:00", end_iso="2020-02-01T11:00:00+01:00",
            decimation_s=5,
        )
        store.set_state(m_beendet["job_id"], "laeuft")
        store.set_state(m_beendet["job_id"], "beendet")

        m_verpasst = store.create_job(
            type="dscan", label="Verpasst",
            params={"start_hz": 140_000_000, "stop_hz": 150_000_000, "speed": "HIGH"},
            start_iso="2020-03-01T10:00:00+01:00", end_iso="2020-03-01T12:00:00+01:00",
            decimation_s=15, rotation_min=30,
        )
        store.set_state(m_verpasst["job_id"], "verpasst")

        # --- ifpan-Job (Anzeige-Bugfix Jobs-Seite): eigenes Jahr, um
        # Zeitfenster-Kollisionen mit den obigen dscan-Jobs zu vermeiden
        # (Muster: eb200_core/job_runner.py-Selbsttest). Prueft, dass
        # build_jobs_view() die ifpan-Params (center_hz/span_hz statt
        # start_hz/stop_hz) UNVERAENDERT durchreicht - genau das fehlende
        # Feld war die Ursache des Anzeige-Bugs (Frontend las bislang immer
        # start_hz/stop_hz, bei ifpan stets None -> "-"/"0 Sweeps").
        m_ifpan = store.create_job(
            type="ifpan", label="IFPAN-Laeuft",
            params={"center_hz": 93_060_000, "span_hz": 200_000, "demod": "FM",
                    "bw_hz": 150_000, "audio": True},
            start_iso="2021-01-01T10:00:00+01:00", end_iso="2021-01-01T11:00:00+01:00",
        )
        store.set_state(m_ifpan["job_id"], "laeuft")
        store.heartbeat(m_ifpan["job_id"], stats={"frames": 42, "bytes": 12345},
                         files=["a.specbin", "a.wav"])

        # --- /api/jobs-Datenaufbau (Anreicherung) --------------------------
        view = build_jobs_view(store)
        by_id = {j["job_id"]: j for j in view}
        assert len(view) == 5, view
        assert by_id[m_laeuft["job_id"]]["stats"]["sweeps"] == 5, by_id[m_laeuft["job_id"]]
        assert by_id[m_laeuft["job_id"]]["files"] == ["a.specbin"], by_id[m_laeuft["job_id"]]
        assert by_id[m_geplant["job_id"]]["params"]["start_hz"] == 88_000_000
        assert by_id[m_verpasst["job_id"]]["state"] == "verpasst"
        assert by_id[m_beendet["job_id"]]["ended_iso"] is not None
        ifpan_view = by_id[m_ifpan["job_id"]]
        assert ifpan_view["type"] == "ifpan"
        assert ifpan_view["params"]["center_hz"] == 93_060_000, ifpan_view["params"]
        assert ifpan_view["params"]["span_hz"] == 200_000, ifpan_view["params"]
        assert ifpan_view["params"].get("start_hz") is None, ifpan_view["params"]
        assert ifpan_view["files"] == ["a.specbin", "a.wav"], ifpan_view["files"]
        assert "sweeps" not in ifpan_view["stats"], ifpan_view["stats"]
        print(f"OK  build_jobs_view: {len(view)} Jobs, stats/files/params/ended_iso korrekt angereichert")
        print("OK  ifpan-Job: params (center_hz/span_hz statt start_hz/stop_hz) unveraendert "
              "durchgereicht - Frontend-Formatierung (jobRangeText/jobStatsText) baut direkt darauf auf")

        # T-1103: /api/recordings-Sicht - nie eine Ausnahme, Schema vollstaendig
        rec_view = build_recordings_view(store, mscan_store=None)
        assert set(rec_view) == {"rows", "summary"}, rec_view.keys()
        assert rec_view["summary"]["gesamt"] == len(rec_view["rows"])
        from eb200_core.recording_index import ROW_KEYS
        for row in rec_view["rows"]:
            assert set(row) == ROW_KEYS, set(row) ^ ROW_KEYS
        print("T-1103 build_recordings_view ok")

        # T-1104: handle_delete_recording - Validierung vor delete_recording()
        try:
            handle_delete_recording(store, {"kind": "x", "ref": "y"})
        except (RecordingNotDeletableError, ValueError):
            pass
        else:
            raise AssertionError("RecordingNotDeletableError/ValueError erwartet: unbekanntes kind")
        try:
            handle_delete_recording(store, {})
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: kind/ref fehlen")
        print("T-1104 handle_delete_recording ok")

        # --- delete: geplant ok, beendet -> ValueError (400 im Handler) ---
        handle_delete(store, m_geplant["job_id"])
        assert len(store.list_jobs()) == 4
        print("OK  delete auf 'geplant' erfolgreich")

        try:
            handle_delete(store, m_beendet["job_id"])
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: delete auf 'beendet'")
        print("OK  delete auf 'beendet' -> ValueError (Handler uebersetzt in 400)")

        # --- retry: verpasst -> neuer geplanter Job, Start=jetzt, Dauer uebernommen ---
        before = datetime.now().astimezone()
        new_manifest = handle_retry(store, m_verpasst["job_id"])
        after = datetime.now().astimezone()
        assert new_manifest["state"] == "geplant", new_manifest
        assert new_manifest["job_id"] != m_verpasst["job_id"]
        assert new_manifest["params"] == m_verpasst["params"], (new_manifest["params"], m_verpasst["params"])
        assert new_manifest["decimation_s"] == m_verpasst["decimation_s"]
        assert new_manifest["rotation_min"] == m_verpasst["rotation_min"]
        new_start = datetime.fromisoformat(new_manifest["start_iso"])
        # +-1s Toleranz: new_manifest["start_iso"] ist auf ganze Sekunden
        # gerundet (isoformat(timespec="seconds") in job_store.py), before/
        # after tragen dagegen Mikrosekunden - ein Vergleich ohne Toleranz
        # kann daher selbst bei korrektem Ablauf knapp danebenliegen.
        assert before - timedelta(seconds=1) <= new_start <= after + timedelta(seconds=1), \
            (before, new_start, after)
        orig_duration = datetime.fromisoformat(m_verpasst["end_iso"]) - datetime.fromisoformat(m_verpasst["start_iso"])
        new_duration = datetime.fromisoformat(new_manifest["end_iso"]) - new_start
        assert abs((new_duration - orig_duration).total_seconds()) < 1, (new_duration, orig_duration)
        assert store.get(m_verpasst["job_id"])["state"] == "verpasst"   # Original bleibt Historie
        print(f"OK  retry auf 'verpasst': neuer Job {new_manifest['job_id']} geplant, "
              f"Start=jetzt, Dauer uebernommen ({new_duration}), Original bleibt verpasst")

        try:
            handle_retry(store, m_beendet["job_id"])
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: retry auf 'beendet'")
        print("OK  retry auf 'beendet' -> ValueError (Handler uebersetzt in 400)")

        # --- joblabel: setzen, aendern, leeren (auch auf 'beendet') -------
        r1 = handle_label(store, m_beendet["job_id"], "Erster Text")
        assert r1["label"] == "Erster Text", r1
        assert store.get(m_beendet["job_id"])["label"] == "Erster Text"
        r2 = handle_label(store, m_beendet["job_id"], "  Geaendert\t \n")
        assert r2["label"] == "Geaendert", r2
        r3 = handle_label(store, m_beendet["job_id"], "")
        assert r3["label"] == "", r3
        assert store.get(m_beendet["job_id"])["label"] == "", store.get(m_beendet["job_id"])
        print("OK  handle_label auf 'beendet'-Job: setzen/aendern/leeren korrekt (Index+Manifest)")

        try:
            handle_label(store, "unbekannte_id_12345", "X")
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: handle_label auf unbekannte job_id")
        print("OK  handle_label auf unbekannte job_id -> KeyError (Handler uebersetzt in 400)")

        # =====================================================================
        # MSCAN (T-1003): build_mscan_*/handle_mscan_* aus einem synthetischen
        # Katalog (T1/T2/T3, Format wie known_freq/*.json - Muster
        # eb200_core.mscan_job._self_test). Der Long-Run-JobStore von oben
        # (store) wird fuer die Laufzaehlung/Loeschsperre (M6) mitbenutzt -
        # der MscanJobStore ist ein eigener, unabhaengiger Store.
        # =====================================================================
        from eb200_core.known_freq import load_catalog

        mscan_catalog_dir = os.path.join(tmp, "known_freq_mscan")
        os.makedirs(mscan_catalog_dir, exist_ok=True)
        mscan_catalog_path = os.path.join(mscan_catalog_dir, "test.json")

        def _write_mscan_catalog(ids):
            with open(mscan_catalog_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1, "meta": {"service": "Test"},
                    "defaults": {"demod": "FM", "bw_hz": 15000}, "bands": [],
                    "entries": [
                        {"kind": "channel", "id": cid, "label": f"Test {cid}",
                         "freq_hz": 100_000_000 + i * 1_000_000}
                        for i, cid in enumerate(ids)
                    ],
                }, f, ensure_ascii=False)

        _write_mscan_catalog(["T1", "T2", "T3"])
        mscan_catalog = load_catalog(mscan_catalog_dir)
        assert mscan_catalog.errors == [], mscan_catalog.errors
        assert len(mscan_catalog.channels) == 3, mscan_catalog.channels

        mscan_jobs_dir = os.path.join(tmp, "jobs", "mscan")
        mscan_store = MscanJobStore(jobs_dir=mscan_jobs_dir)

        # --- M1: handle_mscan_create -----------------------------------------
        job = handle_mscan_create(mscan_store, mscan_catalog, {"title": "  A  "})
        assert job["title"] == "A", job["title"]
        mscan_job_id = job["id"]
        try:
            handle_mscan_create(mscan_store, mscan_catalog, {"title": "   "})
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: leerer Titel")
        print("OK  M1 handle_mscan_create: Titel bereinigt ('A'), leerer Titel -> ValueError")

        # --- M2: handle_mscan_channels - remove VOR add in EINER Anfrage -----
        job = handle_mscan_channels(mscan_store, mscan_catalog,
                                     {"job_id": mscan_job_id, "add": ["T1", "T2"]})
        assert job["channels"] == ["T1", "T2"], job["channels"]
        job = handle_mscan_channels(mscan_store, mscan_catalog,
                                     {"job_id": mscan_job_id, "remove": ["T1"], "add": ["T1", "T3"]})
        assert job["channels"] == ["T2", "T1", "T3"], job["channels"]
        print(f"OK  M2 handle_mscan_channels: remove vor add in derselben Anfrage "
              f"-> channels={job['channels']}")

        # --- M3: unbekannte Kanal-ID -> ValueError, Kanalliste unveraendert --
        vorher = mscan_store.get(mscan_job_id)["channels"]
        try:
            handle_mscan_channels(mscan_store, mscan_catalog, {"job_id": mscan_job_id, "add": ["NIX"]})
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: unbekannte Kanal-ID")
        assert mscan_store.get(mscan_job_id)["channels"] == vorher
        print("OK  M3 handle_mscan_channels mit unbekannter ID -> ValueError, Kanalliste unveraendert")

        # --- M4: build_mscan_job_detail - resolved vs. missing ----------------
        detail = build_mscan_job_detail(mscan_store, mscan_catalog, mscan_job_id, store)
        assert len(detail["channels"]) == 3, detail["channels"]
        assert all(c["missing"] is False for c in detail["channels"]), detail["channels"]
        assert detail["max_channels"] == MAX_CHANNELS

        _write_mscan_catalog(["T1", "T2"])   # T3 faellt aus dem Katalog
        mscan_catalog2 = load_catalog(mscan_catalog_dir)
        assert mscan_catalog2.errors == [], mscan_catalog2.errors
        detail2 = build_mscan_job_detail(mscan_store, mscan_catalog2, mscan_job_id, store)
        missing = [c for c in detail2["channels"] if c["missing"]]
        assert len(missing) == 1 and missing[0]["id"] == "T3", detail2["channels"]
        print("OK  M4 build_mscan_job_detail: 3 Kanaele resolved (missing=False); "
              "nach Entfernen von T3 aus dem Katalog genau 1 Eintrag missing=True")

        # --- M5: handle_mscan_update - params validieren ----------------------
        # Bewusst der URSPRUENGLICHE Katalog (alle 3 Kanaele noch gueltig) -
        # M5 prueft die PARAMETER-Validierung, nicht die Kanal-Validierung
        # aus M4; mscan_catalog2 (ohne T3) wuerde save() unabhaengig von
        # hold_s immer scheitern lassen (Job traegt weiterhin die Kanal-ID
        # T3 aus M2) und den eigentlichen Pruefzweck von M5 verdecken.
        try:
            handle_mscan_update(mscan_store, mscan_catalog, {
                "job_id": mscan_job_id,
                "params": {"dwell_s": None, "hold_s": 11, "squelch_from_memory": True,
                           "min_recording_s": 1.0},
            })
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: hold_s 11")
        updated = handle_mscan_update(mscan_store, mscan_catalog, {
            "job_id": mscan_job_id,
            "params": {"dwell_s": None, "hold_s": 3.0, "squelch_from_memory": False,
                       "min_recording_s": 2.0},
        })
        assert updated["params"]["dwell_s"] is None, updated["params"]
        assert updated["params"]["hold_s"] == 3.0, updated["params"]
        # T-1019: nicht mitgeschickte Parameter ueberleben das Speichern.
        # Die Detailseite kennt record_audio/level_active_dbuv (noch) nicht;
        # beim frueheren vollstaendigen Ersetzen waere ein Pegel-Inventur-Job
        # beim naechsten Titel-Speichern still auf Tonaufnahme zurueckgefallen.
        mscan_store.save(dict(mscan_store.get(mscan_job_id), params=dict(
            mscan_store.get(mscan_job_id)["params"],
            record_audio=False, level_active_dbuv=25.0, stop_after="runtime")),
            catalog=mscan_catalog)
        updated = handle_mscan_update(mscan_store, mscan_catalog, {
            "job_id": mscan_job_id, "title": "Nur der Titel",
            "params": {"dwell_s": None, "hold_s": 3.0, "squelch_from_memory": False,
                       "min_recording_s": 2.0},
        })
        assert updated["params"]["record_audio"] is False, updated["params"]
        assert updated["params"]["level_active_dbuv"] == 25.0, updated["params"]
        assert updated["params"]["stop_after"] == "runtime", updated["params"]
        print("OK  M5 handle_mscan_update: hold_s=11 -> ValueError; gueltige params "
              "gespeichert (dwell_s None weiterhin erlaubt); nicht gesendete "
              "Parameter bleiben erhalten (T-1019)")

        # --- M6: build_mscan_jobs_view (run_count) / Loeschsperre -------------
        view = build_mscan_jobs_view(mscan_store, store)
        entry = next(j for j in view["jobs"] if j["id"] == mscan_job_id)
        assert entry["run_count"] == 0, entry

        run_manifest = store.create_job(
            type="mscan", label="Lauf", params={"mscan_job_id": mscan_job_id},
            start_iso="2099-01-01T10:00:00+01:00", end_iso="2099-01-01T11:00:00+01:00",
        )
        view = build_mscan_jobs_view(mscan_store, store)
        entry = next(j for j in view["jobs"] if j["id"] == mscan_job_id)
        assert entry["run_count"] == 1, entry

        try:
            handle_mscan_delete(mscan_store, store, {"job_id": mscan_job_id})
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: Loeschen bei geplantem Lauf")

        store.set_state(run_manifest["job_id"], "gestoppt")
        handle_mscan_delete(mscan_store, store, {"job_id": mscan_job_id})
        view = build_mscan_jobs_view(mscan_store, store)
        assert view["jobs"] == [], view["jobs"]
        print("OK  M6 build_mscan_jobs_view/handle_mscan_delete: run_count 0->1, "
              "Loeschen bei geplantem Lauf -> ValueError, nach 'gestoppt' erfolgreich, Liste leer")

        # --- M7: kaputte Job-Datei -> errors, gueltige Jobs bleiben korrekt ---
        job_a = handle_mscan_create(mscan_store, mscan_catalog2, {"title": "Job A"})
        with open(os.path.join(mscan_jobs_dir, "ZZZZZZ.json"), "w", encoding="utf-8") as f:
            f.write("{")
        view = build_mscan_jobs_view(mscan_store, store)
        assert len(view["errors"]) == 1, view["errors"]
        assert [j["id"] for j in view["jobs"]] == [job_a["id"]], view["jobs"]
        print("OK  M7 build_mscan_jobs_view: kaputte Datei -> 1 Fehler in errors, "
              "gueltige Jobs bleiben korrekt gelistet")

        # =====================================================================
        # T-1005 Session B: handle_mscan_start / build_mscan_runs (Lauf
        # starten + Laufliste). mscan_catalog (T1/T2/T3, alle gueltig) wird
        # hier erneut verwendet - mscan_catalog2 (M4/M7, ohne T3) wuerde
        # den 3-Kanal-Aufbau von MS1 verhindern.
        # =====================================================================
        run_job = handle_mscan_create(mscan_store, mscan_catalog, {"title": "Run-Test"})
        run_job_id = run_job["id"]
        handle_mscan_channels(mscan_store, mscan_catalog,
                               {"job_id": run_job_id, "add": ["T1", "T2", "T3"]})

        # --- MS1: ueberwachung (Default), duration_min=30 ---------------------
        # eigenes Zeitfenster (2097) - "jetzt" (start_iso weggelassen) wuerde
        # mit dem "retry"-Testjob von oben (ebenfalls ab "jetzt") kollidieren.
        started1 = handle_mscan_start(mscan_store, store, mscan_catalog,
                                       {"job_id": run_job_id, "start_iso": "2097-01-01T10:00:00+01:00",
                                        "duration_min": 30})
        manifest_ms1 = store.get(started1["run_id"])
        assert manifest_ms1["type"] == "mscan", manifest_ms1
        assert len(manifest_ms1["params"]["channels"]) == 3, manifest_ms1["params"]
        start_ms1 = datetime.fromisoformat(manifest_ms1["start_iso"])
        end_ms1 = datetime.fromisoformat(manifest_ms1["end_iso"])
        assert abs((end_ms1 - start_ms1).total_seconds() - 30 * 60) < 1, (start_ms1, end_ms1)
        print("OK  MS1 handle_mscan_start (ueberwachung, duration_min=30): "
              "type=mscan, params.channels 3, end_iso = start+30min")

        # --- MS2: inventur ohne Ende -> Sicherheitsende (>= start+10min) ------
        # eigenes, weit entferntes Zeitfenster (2099) - keine Kollision mit
        # dem "jetzt"-Fenster von MS1.
        handle_mscan_update(mscan_store, mscan_catalog, {
            "job_id": run_job_id,
            "params": {"mode": "inventur", "dwell_s": 3.0, "hold_s": 0.0,
                       "squelch_from_memory": True, "min_recording_s": 0.5},
        })
        started2 = handle_mscan_start(mscan_store, store, mscan_catalog,
                                       {"job_id": run_job_id, "start_iso": "2099-01-01T10:00:00+01:00"})
        manifest_ms2 = store.get(started2["run_id"])
        start_ms2 = datetime.fromisoformat(manifest_ms2["start_iso"])
        end_ms2 = datetime.fromisoformat(manifest_ms2["end_iso"])
        assert end_ms2 is not None, manifest_ms2
        assert (end_ms2 - start_ms2).total_seconds() >= 600, (start_ms2, end_ms2)
        print(f"OK  MS2 handle_mscan_start (inventur, kein Ende angegeben): Sicherheitsende "
              f"gesetzt, {end_ms2 - start_ms2} >= 10min")

        # --- MS2b: inventur + stop_after runtime -> Dauer gilt (T-1019c) ------
        # Eigenes Zeitfenster (2096), damit weder MS1 ("jetzt"), MS2/MS4 (2099)
        # noch MS5/MS6 (2098) kollidieren. 45 min ist bewusst weit weg vom Sicherheitsende
        # (bei 3 Kanaelen/Dwell 3,0 s greift dort die Untergrenze 10 min) -
        # ein zurueckgebliebenes Sicherheitsende faellt damit sofort auf.
        handle_mscan_update(mscan_store, mscan_catalog, {
            "job_id": run_job_id, "params": {"stop_after": "runtime"},
        })
        started2b = handle_mscan_start(mscan_store, store, mscan_catalog,
                                        {"job_id": run_job_id,
                                         "start_iso": "2096-01-01T10:00:00+01:00",
                                         "duration_min": 45})
        manifest_ms2b = store.get(started2b["run_id"])
        start_ms2b = datetime.fromisoformat(manifest_ms2b["start_iso"])
        end_ms2b = datetime.fromisoformat(manifest_ms2b["end_iso"])
        assert abs((end_ms2b - start_ms2b).total_seconds() - 45 * 60) < 1, (start_ms2b, end_ms2b)
        assert manifest_ms2b["params"]["stop_after"] == "runtime", manifest_ms2b["params"]
        # Zurueck auf "cycle": MS4 prueft die Ueberlappung gegen den
        # DETERMINISTISCH berechneten Sicherheitsende-Lauf aus MS2.
        handle_mscan_update(mscan_store, mscan_catalog, {
            "job_id": run_job_id, "params": {"stop_after": "cycle"},
        })
        print("OK  MS2b handle_mscan_start (inventur, stop_after runtime, duration_min=45): "
              "end_iso = start+45min statt Sicherheitsende")

        # --- MS3: leere Kanalliste -> ValueError -------------------------------
        empty_job = handle_mscan_create(mscan_store, mscan_catalog, {"title": "Leer"})
        try:
            handle_mscan_start(mscan_store, store, mscan_catalog, {"job_id": empty_job["id"]})
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: MSCAN-Start ohne Kanaele")
        print("OK  MS3 handle_mscan_start mit leerer Kanalliste -> ValueError")

        # --- MS4: zweiter Start im selben Zeitfenster -> ValueError -----------
        # (Ueberlappung mit dem MS2-Lauf, gleicher start_iso -> gleiches,
        # deterministisch berechnetes Sicherheitsende).
        try:
            handle_mscan_start(mscan_store, store, mscan_catalog,
                                {"job_id": run_job_id, "start_iso": "2099-01-01T10:00:00+01:00"})
        except ValueError as exc:
            assert started2["run_id"] in str(exc), str(exc)
        else:
            raise AssertionError("ValueError erwartet: ueberlappender MSCAN-Lauf")
        print("OK  MS4 handle_mscan_start: zweiter Start im selben Fenster -> "
              "ValueError (Ueberlappung, nennt den kollidierenden Lauf)")

        # --- MS5: build_mscan_runs liefert 1 Eintrag, stats/results_channels 0 -
        ms5_job = handle_mscan_create(mscan_store, mscan_catalog, {"title": "MS5"})
        handle_mscan_channels(mscan_store, mscan_catalog, {"job_id": ms5_job["id"], "add": ["T1"]})
        ms5_started = handle_mscan_start(
            mscan_store, store, mscan_catalog,
            {"job_id": ms5_job["id"], "start_iso": "2098-01-01T10:00:00+01:00", "duration_min": 15},
        )
        runs = build_mscan_runs(store, ms5_job["id"])
        assert len(runs) == 1, runs
        assert runs[0]["run_id"] == ms5_started["run_id"], runs
        assert runs[0]["stats"]["cycles"] == 0, runs
        assert runs[0]["stats"]["audio_orphan"] == 0, runs
        assert runs[0]["results_channels"] == 0, runs
        print("OK  MS5 build_mscan_runs: 1 Eintrag, stats/audio_orphan/results_channels default 0")

        # --- MS6: build_mscan_runs liefert die neuen Felder (T-1006) -----------
        assert runs[0]["ended_iso"] is None, runs[0]
        assert runs[0]["job_dir"].endswith("longrun_" + ms5_started["run_id"]), runs[0]["job_dir"]
        assert runs[0]["stats"]["audio_orphan_mid"] is None, runs[0]["stats"]
        print("OK  MS6 build_mscan_runs liefert ended_iso/job_dir/audio_orphan_pre-mid-post (T-1006)")

        # --- MS6b: results_channels bei kind "level" (T-1019d) ----------------
        # Ein Pegel-Inventur-Ergebnis fuehrt ALLE Kanaele, auch die stillen -
        # gezaehlt werden duerfen nur die aktiven.
        store.heartbeat(ms5_started["run_id"],
                         stats={"cycles": 4, "recordings": 0, "active_channels": 1},
                         files=[],
                         results={"kind": "level", "active_channels": 1,
                                   "channels": [{"channel_id": "T1", "active": True, "hits": 3},
                                                {"channel_id": "T2", "active": False, "hits": 0},
                                                {"channel_id": "T3", "active": False, "hits": 0}]})
        runs_level = build_mscan_runs(store, ms5_job["id"])
        assert runs_level[0]["results_channels"] == 1, runs_level[0]
        print("OK  MS6b build_mscan_runs: results_channels zaehlt bei kind 'level' nur "
              "die aktiven Kanaele (T-1019d)")

        # MS7/MS8 (Ergebnisansicht eines MSCAN-Laufs bzw. dessen Datei-
        # Sandbox) sind mit T-1106a nach eb200_core/mscan_view.py umgezogen
        # (siehe dortiger Selbsttest, "python -m eb200_core.mscan_view
        # --selftest") - Luecke in der Nummerierung hier bewusst belassen
        # statt die folgenden Faelle umzunummerieren.

    # T-922a: der Export-Handler prueft die Nutzlast, BEVOR irgendein
    # Pfad angefasst wird. Keine der vier Nutzlasten darf bis zur Platte
    # durchkommen - deshalb ist store=None hier unschaedlich (Muster
    # handle_delete_recording, das store ebenfalls nicht braucht).
    for _bad in (None, "text", {}, {"kind": "audio"}, {"ref": "x.wav"}):
        try:
            handle_sigmf_export(None, _bad)
        except (ValueError, RecordingNotDeletableError):
            pass
        else:
            raise AssertionError(f"Ablehnung erwartet fuer Nutzlast {_bad!r}")
    # Zulaessige Nutzlast, aber falsche Art: muss ebenfalls abprallen,
    # und zwar SPEZIFISCH (nicht als generischer ValueError).
    try:
        handle_sigmf_export(None, {"kind": "dscan", "ref": "x.wav"})
    except RecordingNotDeletableError:
        print("OK  T-922a handle_sigmf_export: leere und artfremde Nutzlasten abgewiesen")
    else:
        raise AssertionError("RecordingNotDeletableError erwartet fuer kind=dscan")

    # T-1112: IQ-Badge und IQ-Filterchip sind reine Frontend-Logik in
    # HTML_PAGE - hier nur die Textbausteine pruefen (kein Browser noetig).
    # ".badge-iq {" stammt aus BASE_CSS (eb200_core/ui_theme.py) und belegt
    # zugleich, dass die Farbtokens der Suite mitgezogen wurden.
    for _frag in ("function isIqRec(r)", "badge badge-iq", "chip('art', 'iq', 'IQ'",
                  "filterArt === 'iq'", ".badge-iq {"):
        assert _frag in HTML_PAGE, _frag
    print("OK  T-1112 IQ-Badge: Zusatz-Badge, Filter-Chip und CSS-Klasse in HTML_PAGE vorhanden")

    # T-1114: der Spaltensatz der Aufzeichnungstabelle haengt am Art-Filter -
    # reine Frontend-Logik in HTML_PAGE, hier nur die Textbausteine pruefen
    # (kein Browser noetig), Muster T-1112 oben.
    for _frag in ("function recColumns()", "function recSortColumn(cols)",
                  "function artCell(fn)", "function artSortNum(fn)",
                  "function artSortText(fn)", "function buildRecHead(cols, activeKey)",
                  "const ART_COLUMNS_DSCAN = [", "const ART_COLUMNS_IFPAN = [",
                  "const ART_COLUMNS_MSCAN = [", "const ART_COLUMNS_AUDIO = [",
                  "buildRecHead(cols, col.key)"):
        assert _frag in HTML_PAGE, _frag
    # Die feste Spaltenliste von vor T-1114 darf es nicht mehr geben; der
    # AUDIO-Satz muss BEIDE Art-Chips bedienen (IQ ist kein eigener Art-Wert,
    # siehe T-1112).
    assert "REC_COLUMNS" not in HTML_PAGE
    assert "filterArt === 'audio' || filterArt === 'iq'" in HTML_PAGE
    print("OK  T-1114 Spaltensatz je Art-Filter: recColumns() mit DSCAN-/IFPAN-/"
          "MSCAN-/AUDIO-Spalten in HTML_PAGE vorhanden")

    # T-922b: die SigMF-Knoepfe sind reine Frontend-Logik in HTML_PAGE -
    # hier nur die Textbausteine pruefen (kein Browser noetig), Muster
    # T-1112/T-1114 oben.
    for _frag in ("const COL_SIGMF = {", "function sigmfExport(r, withData)",
                  "const SIGMF_WARN_BYTES =", "'/api/sigmfexport'",
                  'class="sigmfBtn"', 'class="sigmfDataBtn"',
                  "COL_EVAL, COL_DEL, COL_SIGMF]",
                  "Storage location: ' + res.dir"):
        assert _frag in HTML_PAGE, _frag
    # Die Knoepfe erscheinen NUR in IQ-Zeilen; sonst bleibt die Zelle
    # leer (Muster COL_EVAL). Und die Spalte muss in BEIDEN Spaltensaetzen
    # stehen - im fuss-Array und in der ausgeschriebenen Liste fuer "Alle",
    # sonst fehlt sie ausgerechnet in der Standardansicht.
    assert "if (!isIqRec(r)) return '';" in HTML_PAGE
    assert HTML_PAGE.count("COL_DEL, COL_SIGMF]") == 2, HTML_PAGE.count("COL_DEL, COL_SIGMF]")
    print("OK  T-922b SigMF-Knoepfe: Spalte in beiden Spaltensaetzen, Schwelle "
          "und Endpunktaufruf in HTML_PAGE vorhanden")

    # T-1019c Teil 2: Parameter-Tab und Lauf-Formular sind reine
    # Frontend-Logik in HTML_MSCAN_DETAIL - hier nur die Textbausteine
    # pruefen (kein Browser noetig), Muster T-1112 oben.
    for _frag in ('id="inventurStopRow"', 'id="stopCycle"', 'id="stopRuntime"',
                  'id="levelOnlyInput"', 'id="levelActiveInput"',
                  "function updateInventurRows()", "function einDurchgangGewaehlt()",
                  "stop_after: el('stopRuntime').checked ? 'runtime' : 'cycle'",
                  "record_audio: !el('levelOnlyInput').checked"):
        assert _frag in HTML_MSCAN_DETAIL, _frag
    # Die alte, allein an mode haengende Bedingung darf es nicht mehr geben -
    # sie wuerde die Ende-Felder auch bei stop_after 'runtime' ausblenden.
    assert "currentJob.params.mode === 'inventur'" not in HTML_MSCAN_DETAIL
    print("OK  T-1019c Teil 2: stop_after-Wahl, Pegel-Felder und "
          "einDurchgangGewaehlt() in HTML_MSCAN_DETAIL vorhanden")

    print("\nSelbsttest erfolgreich abgeschlossen.")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Long-Run-Jobs-Uebersicht (WS3 11.5)")
    parser.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren (kein Server)")
    parser.add_argument("--data-dir", metavar="PFAD",
                    help="Datenwurzel (wertet eb200_core/paths.py beim Import aus, T-2003)")
    args = parser.parse_args()

    if args.selftest:
        _self_test()
        return 0

    Handler.store = JobStore()
    Handler.mscan_store = MscanJobStore()
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"[manage-jobs] Server laeuft auf http://{HTTP_HOST}:{HTTP_PORT}")
    register_server("manage_jobs", HTTP_PORT)
    print("Beenden mit Strg+C")

    def handle_sigint(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, handle_sigint)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[main] fahre herunter ...")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
