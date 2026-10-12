#!/usr/bin/env python3
"""
eb200_core/mscan_job.py

Verwaltet MSCAN-JOB-DEFINITIONEN (PLAN.md Meilenstein 10) - welche
Katalog-Kanaele (eb200_core/known_freq.py) in ein MSCAN-Speicherprogramm
(MEM0..MEM999) aufgenommen werden sollen und mit welchen Scan-Parametern
(DWELL/HOLD/Squelch-from-Memory/Mindest-Aufnahmedauer). Eine Job-Definition
ist NICHT der Lauf selbst: jeder tatsaechliche Scan-Durchlauf wird spaeter
ein regulaerer job_store-Job vom Typ "mscan" (eigene Zeitstempel-ID,
Manifest, Ergebnis-Index) - das ist ein SPAETERER Schritt (PLAN.md T-1005)
und NICHT Teil dieses Moduls. Hier steckt NUR die Verwaltung der
wiederverwendbaren Definition: Anlegen, Bearbeiten, Kanalliste pflegen,
Validieren, Kanal-Snapshot fuer einen Lauf aufloesen. Kein Geraete-/
Netzwerkzugriff, keine UI, keine Aenderung an job_store.py/manage_jobs.py.
Reine Byte-/JSON-Verarbeitung, daher per Selbsttest ohne Hardware pruefbar.
Nur Python-Standardbibliothek.

Datenmodell:
  Ein Job ist ein Dict mit den Schluesseln id/title/notes/created_at/
  modified_at/last_run_at/channels/params (siehe new_job()). channels ist
  eine Liste von Katalog-Kanal-IDs (Catalog.by_id, siehe known_freq.py) in
  der Reihenfolge, in der sie hinzugefuegt wurden - keine Duplikate,
  hoechstens MAX_CHANNELS (die Anzahl der Speicherplaetze MEM0..MEM999).
  params uebernimmt fehlende Schluessel aus DEFAULT_PARAMS (siehe
  validate_params()).

Ablage:
  Eine Datei je Job unter jobs/mscan/<ID>.json - id ist eine 6-stellige
  Zufalls-ID aus JOB_ID_ALPHABET, verwechslungsarm ohne 0/O/1/I (anders als
  Long-Run-Jobs in job_store.py, deren Zeitstempel-ID praktisch
  kollisionsfrei ist, koennten hier mehrere Definitionen in dichter Folge
  entstehen - siehe new_job_id()). Schreibdisziplin: .bak der Vorversion
  (Muster eb200_core/block_admin.py._write_known_file) kombiniert mit
  atomarem Schreiben per Temp-Datei + os.replace (Muster
  eb200_core/job_store.py._atomic_write_json). Alle Mutationen unter
  threading.Lock (Muster JobStore).

Regeln: nur Python-Standardbibliothek. Konsolenausgaben ASCII (cp1252-
Konsole, siehe known_freq.py).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
from collections import Counter
from datetime import datetime

# Erlaubt sowohl "python -m eb200_core.mscan_job" als auch den direkten
# Skriptaufruf "python eb200_core/mscan_job.py" (Selbsttest-Konvention
# dieses Projekts, siehe eb200_core/job_runner.py): beim Direktaufruf fehlt
# das Repo-Root im Suchpfad fuer den known_freq-Import im Selbsttest.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core import paths


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Standard-Ablageverzeichnis: <DATA_ROOT>/jobs/mscan (T-2002, eb200_core/paths.py;
# ohne --data-dir das Repo-Root wie bisher).
MSCAN_JOBS_DIR = paths.data_path("jobs", "mscan")

# Verwechslungsarmes Alphabet fuer Job-IDs: kein 0/O, kein 1/I.
JOB_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOB_ID_LEN = 6
MAX_CHANNELS = 1000            # MEM0..MEM999
TITLE_MAX_LEN = 200

# Job-Modus: "inventur" = ein einziger Scan-Zyklus (SENSe:MSCan:COUNt 1), der
# Lauf endet von selbst - Dwell MUSS endlich sein, sonst haelt ein
# Dauertraeger den Squelch offen und der Scan bleibt haengen. "ueberwachung"
# = fortlaufender Scan (SENSe:MSCan:COUNt INFinity), der Lauf endet ueber
# Endzeit/Stopp des Laufs (Laufdauer ist Sache des Laufs - job_store
# start_iso/end_iso - NICHT dieser Definition). Das tatsaechliche Setzen von
# COUNt ist ein spaeterer Schritt (PLAN.md T-1005) - hier nur Datenmodell,
# Validierung, UI.
MODES = ("inventur", "ueberwachung")

# T-1019: Ende-Bedingung der Inventur. "cycle" = ein einziger Durchgang
# (SENSe:MSCan:COUNt 1, bisheriges Verhalten), "runtime" = fortlaufende
# Durchgaenge (COUNt INF), das Ende kommt wie bei der Ueberwachung ueber
# Endzeit/Stopp des LAUFS. Die Laufdauer selbst bleibt Sache des Laufs
# (job_store start_iso/end_iso) und steht bewusst NICHT in dieser
# Definition. Nur fuer mode "inventur" ausgewertet - bei "ueberwachung"
# gilt ohnehin COUNt INF, der Wert wird dort ignoriert (deshalb auch keine
# Kreuzpruefung mode/stop_after in validate_params()).
STOP_AFTER = ("cycle", "runtime")

DEFAULT_PARAMS = {
    "mode": "ueberwachung",         # "inventur" (COUNt 1 oder INF, siehe stop_after) | "ueberwachung" (COUNt INF)
    "stop_after": "cycle",          # nur inventur: "cycle" = ein Durchgang | "runtime" = bis Laufende/Stopp
    "dwell_s": None,                # None = INFinity (SENSe:MSCan:DWELl)
    "hold_s": 2.0,                  # SENSe:MSCan:HOLD:TIME, 0..10 s
    "squelch_from_memory": True,    # OUTPut:SQUelch:CONTrol MEMory
    "min_recording_s": 1.0,         # Aufnahmen kuerzer als das werden verworfen;
                                     # "aktiv" = mindestens eine Aufnahme >= diesem Wert
    "record_audio": True,           # False = Pegel-Inventur: kein AUD-Pfad, keine WAV-Dateien
    "level_active_dbuv": None,      # None = jede Squelch-Oeffnung zaehlt als Aktivitaet;
                                     # Zahl = ein Item gilt erst ab diesem Pegel als Aktivitaet
}

# Vorschlagswerte fuer die UI beim Umschalten auf inventur (siehe
# manage_jobs.py) - die Server-Seite erzwingt sie NICHT, sie prueft nur die
# inventur-Regeln in validate_params() (dwell_s endlich und > 0).
INVENTUR_DEFAULTS = {"dwell_s": 3.0, "hold_s": 0.0, "min_recording_s": 0.5}

# T-1019: Vorschlagswerte fuer die Pegel-Inventur (record_audio False).
# Ziel ist ein schneller Rundlauf: ein stiller Kanalbesuch kostet ~25 ms
# (docs/EB200_Geraeteprofil.md, Abschnitt MSCAN), ein belegter Kanal haelt
# den Scan dagegen dwell_s lang fest - kurzes Dwell und Hold 0 halten die
# Zykluszeit auch bei vielen belegten Kanaelen klein. min_recording_s ist
# ohne Ton bedeutungslos, bleibt aber gesetzt, damit ein Zurueckschalten
# auf Tonaufnahme einen brauchbaren Wert vorfindet.
PEGEL_INVENTUR_DEFAULTS = {"dwell_s": 0.2, "hold_s": 0.0, "min_recording_s": 0.5,
                           "record_audio": False, "stop_after": "runtime"}

# Bewusst grosszuegiger als JOB_ID_ALPHABET (erlaubt A-Z komplett, also auch
# O/I) - die Regex prueft nur das FORMAT bestehender IDs, nicht ob sie aus
# dem Erzeugungs-Alphabet stammen koennten.
_JOB_ID_RE = re.compile(r"^[A-Z2-9]{6}$")


# ---------------------------------------------------------------------------
# Freie Hilfsfunktionen (kein Zustand, daher ausserhalb der Klasse)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Aktuelle Zeit als ISO-String in lokaler Zeit MIT Offset (nicht UTC),
    Sekundenaufloesung - Muster eb200_core/job_store.py._now_iso() (bewusst
    eigenstaendig dupliziert, dieses Modul bleibt unabhaengig)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_title(text) -> str:
    """Normalisiert einen Job-Titel (Muster job_store._clean_job_label): zu
    str zwingen, Steuerzeichen (inkl. Zeilenumbruch/Tab/DEL) durch
    Leerzeichen ersetzen, Rand-Leerzeichen entfernen, auf TITLE_MAX_LEN
    Zeichen kuerzen."""
    s = "" if text is None else str(text)
    s = "".join(ch if (ch >= " " and ch != "\x7f") else " " for ch in s)
    return s.strip()[:TITLE_MAX_LEN]


def new_job_id(existing: set) -> str:
    """Neue 6-stellige Zufalls-ID aus JOB_ID_ALPHABET (secrets.choice, also
    kryptografisch gut gemischt - nicht aus Sicherheitsgruenden noetig,
    sondern weil es die einfachste Quelle unverzerrter Zufallszeichen aus
    der Standardbibliothek ist). Wuerfelt bei einer Kollision mit existing
    erneut; nach 100 erfolglosen Versuchen (bei einem nahezu vollen
    ID-Raum, praktisch nie im Normalbetrieb) RuntimeError statt einer
    Endlosschleife."""
    for _ in range(100):
        cid = "".join(secrets.choice(JOB_ID_ALPHABET) for _ in range(JOB_ID_LEN))
        if cid not in existing:
            return cid
    raise RuntimeError(
        f"could not generate a new collision-free job ID after 100 attempts "
        f"({len(existing)} existing IDs)"
    )


def new_job(title, notes="") -> dict:
    """Neues Job-Dict im Speicher (noch nicht abgelegt) - siehe
    MscanJobStore.create() fuer die tatsaechliche Anlage inkl. Schreiben.
    channels ist leer, params eine Kopie von DEFAULT_PARAMS. Die id wird
    hier bereits per new_job_id(set()) vergeben; ein Store ersetzt sie bei
    Bedarf durch eine gegen den eigenen Bestand kollisionsfreie ID."""
    now = _now_iso()
    return {
        "id": new_job_id(set()),
        "title": clean_title(title),
        "notes": "" if notes is None else str(notes),
        "created_at": now,
        "modified_at": now,
        "last_run_at": None,
        "channels": [],
        "params": dict(DEFAULT_PARAMS),
    }


def validate_params(params) -> list[str]:
    """Prueft ein params-dict (Schluessel wie DEFAULT_PARAMS). Nur
    VORHANDENE Schluessel werden inhaltlich geprueft - ein Zusammenfuehren
    mit DEFAULT_PARAMS ist Sache des Aufrufers (siehe MscanJobStore.create/
    add_channels/remove_channels). Unbekannte Schluessel sind ein Fehler.
    Liefert eine Fehlerliste, leer bedeutet: gueltig."""
    errors: list[str] = []
    if not isinstance(params, dict):
        return [f"params must be an object, found {type(params).__name__}"]

    for key in sorted(set(params) - set(DEFAULT_PARAMS)):
        errors.append(f"unknown parameter {key!r}")

    def _is_number(v) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    dwell_ok = True
    if "dwell_s" in params:
        v = params["dwell_s"]
        if v is not None and not (_is_number(v) and 0 <= v <= 10):
            errors.append(f"dwell_s must be None or a number between 0 and 10, found {v!r}")
            dwell_ok = False
    if "hold_s" in params:
        v = params["hold_s"]
        if not (_is_number(v) and 0 <= v <= 10):
            errors.append(f"hold_s must be a number between 0 and 10, found {v!r}")
    if "squelch_from_memory" in params:
        v = params["squelch_from_memory"]
        if not isinstance(v, bool):
            errors.append(f"squelch_from_memory must be bool, found {v!r}")
    # T-1019: stop_after/record_audio/level_active_dbuv. stop_after wird
    # NICHT gegen mode geprueft (siehe STOP_AFTER): bei "ueberwachung" ist
    # der Wert bedeutungslos, ein Umschalten des Modus soll deshalb nie
    # eine Fehlermeldung ueber einen unbenutzten Parameter ausloesen.
    if "stop_after" in params:
        if params["stop_after"] not in STOP_AFTER:
            errors.append("stop_after must be one of: cycle, runtime")
    if "record_audio" in params:
        v = params["record_audio"]
        if not isinstance(v, bool):
            errors.append(f"record_audio must be bool, found {v!r}")
    if "level_active_dbuv" in params:
        v = params["level_active_dbuv"]
        if v is not None and not (_is_number(v) and -30 <= v <= 150):
            errors.append(
                f"level_active_dbuv must be None or a number between -30 and 150 (dBuV), found {v!r}")
    min_rec_ok = True
    if "min_recording_s" in params:
        v = params["min_recording_s"]
        if not (_is_number(v) and v > 0):
            errors.append(f"min_recording_s must be a number greater than 0, found {v!r}")
            min_rec_ok = False

    mode_ok = True
    if "mode" in params:
        if params["mode"] not in MODES:
            errors.append("mode must be one of: inventur, ueberwachung")
            mode_ok = False

    # inventur-Regeln: nur pruefen, wenn mode und dwell_s je fuer sich schon
    # gueltig sind (siehe mode_ok/dwell_ok oben) und dwell_s ueberhaupt
    # angegeben wurde (siehe Docstring: nur VORHANDENE Schluessel pruefen).
    if mode_ok and dwell_ok and params.get("mode") == "inventur" and "dwell_s" in params:
        dwell_s = params["dwell_s"]
        if dwell_s is None:
            errors.append(
                "inventur: dwell_s must not be unlimited (continuous carriers keep "
                "the squelch open, the scan would hang)"
            )
        elif dwell_s <= 0:
            errors.append("inventur: dwell_s must be greater than 0")

    # Nur pruefen, wenn beide Werte vorhanden UND je fuer sich schon gueltig
    # sind (siehe dwell_ok/min_rec_ok oben) - ein bereits gemeldeter
    # Einzelfehler soll nicht durch eine verwirrende Zweitmeldung ueber den
    # Vergleich der beiden Werte ueberdeckt werden.
    # T-1019: Ohne Tonaufnahme (record_audio False) entfaellt der Vergleich
    # ganz - min_recording_s wirkt nur auf WAV-Segmente, und eine schnelle
    # Pegel-Inventur will gerade ein sehr kurzes Dwell.
    if (dwell_ok and min_rec_ok and "dwell_s" in params and "min_recording_s" in params
            and params.get("record_audio", True) is not False):
        dwell_s = params["dwell_s"]
        min_recording_s = params["min_recording_s"]
        if dwell_s is not None and dwell_s < min_recording_s:
            errors.append(
                f"dwell_s ({dwell_s}) is less than min_recording_s ({min_recording_s}): "
                f"the scan would move on before a recording reaches the min. duration"
            )

    return errors


def validate_job(job, catalog) -> list[str]:
    """Prueft ein vollstaendiges Job-Dict: id-Format, nichtleerer Titel
    (nach clean_title), channels (Liste von str, jede ID im Katalog
    vorhanden - sofern catalog gegeben, sonst entfaellt diese Pruefung -,
    keine Duplikate, hoechstens MAX_CHANNELS Eintraege) und params (siehe
    validate_params()). Liefert eine Fehlerliste, leer bedeutet: gueltig."""
    errors: list[str] = []
    if not isinstance(job, dict):
        return ["job must be an object"]

    jid = job.get("id")
    if not isinstance(jid, str) or not _JOB_ID_RE.match(jid):
        errors.append(
            f"invalid id {jid!r} (expected {JOB_ID_LEN} uppercase letters/digits)"
        )

    if not clean_title(job.get("title")):
        errors.append("title must not be empty")

    channels = job.get("channels")
    if not isinstance(channels, list):
        errors.append(f"channels must be a list, found {type(channels).__name__}")
    else:
        for i, cid in enumerate(channels):
            if not isinstance(cid, str):
                errors.append(f"channels[{i}] is not a string: {cid!r}")
        str_channels = [c for c in channels if isinstance(c, str)]

        for cid, n in Counter(str_channels).items():
            if n > 1:
                errors.append(f"channel ID {cid!r} appears {n}x in channels")

        if catalog is not None:
            for cid in str_channels:
                if catalog.by_id(cid) is None:
                    errors.append(f"unknown channel ID {cid!r} (not in the catalog)")

        if len(channels) > MAX_CHANNELS:
            errors.append(
                f"channels has {len(channels)} entries, at most "
                f"{MAX_CHANNELS} allowed (MEM0..MEM{MAX_CHANNELS - 1})"
            )

    errors.extend(validate_params(job.get("params")))
    return errors


def _fill_param_defaults(job: dict) -> dict:
    """Ergaenzt fehlende Schluessel in job['params'] aus DEFAULT_PARAMS - fuer
    Job-Dateien, die vor der Einfuehrung eines neuen Parameters geschrieben
    wurden (z. B. 'mode', siehe MODES). create() mergt params bereits selbst
    (eigene dict(DEFAULT_PARAMS)-Basis) - diese Funktion schliesst dieselbe
    Luecke fuer bereits abgelegte Jobs beim LESEN (get()/list_jobs()).
    Aendert job['params'] IN-PLACE und schreibt NICHTS zurueck - das
    passiert erst beim naechsten save()/add_channels()/remove_channels().
    Liefert job zurueck (Aufrufer-Bequemlichkeit)."""
    params = job.get("params")
    if not isinstance(params, dict):
        job["params"] = dict(DEFAULT_PARAMS)
        return job
    for key, default in DEFAULT_PARAMS.items():
        params.setdefault(key, default)
    return job


def resolve_snapshot(job, catalog) -> list[dict]:
    """Loest job['channels'] gegen catalog zu einer Liste vollstaendiger
    Kanal-Snapshots auf (feste Schluesselmenge id/label/freq_hz/demod/
    bw_hz/squelch_dbuv/antenna/service/file/tags, fehlende optionale Felder
    -> None), in der Reihenfolge von job['channels']. Dieser Snapshot ist
    das, was ein spaeterer Lauf (job_store-Job vom Typ mscan) einfriert -
    Ergebnisse bleiben so auch nach Katalogaenderungen interpretierbar.
    Wirft KeyError (mit der ID im Text), wenn eine Kanal-ID nicht im
    Katalog steht - der Aufrufer muss vorher validate_job() erfolgreich
    durchlaufen haben."""
    keys = ("id", "label", "freq_hz", "demod", "bw_hz", "squelch_dbuv",
            "antenna", "service", "file", "tags")
    out = []
    for cid in job["channels"]:
        ch = catalog.by_id(cid)
        if ch is None:
            raise KeyError(f"channel ID {cid!r} not in the catalog - validate the job first")
        out.append({k: ch.get(k) for k in keys})
    return out


# ---------------------------------------------------------------------------
# MscanJobStore
# ---------------------------------------------------------------------------

class MscanJobStore:
    """
    Verwaltet MSCAN-Job-Definitionen: Anlage, Speichern, Kanalliste pflegen
    (Toggle-Semantik: bereits enthaltene IDs bei add_channels() still
    ueberspringen), Loeschen. Eine Instanz pro Server-Prozess (Muster:
    JobStore in job_store.py) - alle Mutationen sind threadsicher. Jeder
    Job liegt als eigene Datei <jobs_dir>/<ID>.json (siehe Moduldoku).
    """

    def __init__(self, jobs_dir: str = MSCAN_JOBS_DIR) -> None:
        self._jobs_dir = jobs_dir
        os.makedirs(jobs_dir, exist_ok=True)
        self._lock = threading.Lock()
        self.errors: list[str] = []

    # -- Pfade / Datei-IO ---------------------------------------------------

    def _path(self, job_id: str) -> str:
        """Dateipfad zu job_id. Prueft das ID-FORMAT per Regex, BEVOR ein
        Pfad gebaut wird (Sandbox: eine ID wie '../x' darf nie in einen
        Dateipfad ausserhalb von jobs_dir muenden) - wirft dafuer
        ValueError, nicht KeyError (siehe get())."""
        if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
            raise ValueError(f"invalid job_id {job_id!r}")
        return os.path.join(self._jobs_dir, f"{job_id}.json")

    def _write(self, job: dict) -> None:
        """Schreibt job atomar nach <jobs_dir>/<ID>.json: existiert die
        Datei bereits, wird sie zuerst nach <ID>.json.bak kopiert (Muster
        block_admin._write_known_file), danach per Temp-Datei + os.replace
        ersetzt (Muster job_store._atomic_write_json). Muss unter self._lock
        aufgerufen werden."""
        path = self._path(job["id"])
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                old = fh.read()
            with open(path + ".bak", "wb") as fh:
                fh.write(old)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(job, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def _existing_ids(self) -> set:
        try:
            names = os.listdir(self._jobs_dir)
        except OSError:
            return set()
        return {n[:-len(".json")] for n in names if n.endswith(".json")}

    # -- Abfrage --------------------------------------------------------

    def list_jobs(self) -> list:
        """Alle abgelegten Jobs, sortiert nach modified_at absteigend
        (neuester zuerst). Eine kaputte Datei wird uebersprungen und der
        Fehlertext in self.errors gesammelt statt eine Ausnahme zu werfen -
        eine einzelne beschaedigte Job-Datei darf die Uebersicht nicht
        lahmlegen (Muster known_freq.load_catalog)."""
        with self._lock:
            self.errors = []
            try:
                names = sorted(n for n in os.listdir(self._jobs_dir) if n.endswith(".json"))
            except OSError:
                names = []
            jobs = []
            for name in names:
                path = os.path.join(self._jobs_dir, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        job = json.load(fh)
                    if not isinstance(job, dict):
                        raise ValueError("top level is not an object")
                except (OSError, ValueError) as exc:
                    self.errors.append(f"{name}: unreadable - {exc}")
                    continue
                jobs.append(_fill_param_defaults(job))
            jobs.sort(key=lambda j: j.get("modified_at") or "", reverse=True)
            return jobs

    def get(self, job_id: str) -> dict:
        """Job-Dict zu job_id. Wirft ValueError bei ungueltigem Format der
        ID (siehe _path(), Sandbox), KeyError, wenn keine Datei zu einer
        formal gueltigen ID existiert. Fehlende params-Schluessel (Altbestand
        vor einem neuen Parameter, siehe _fill_param_defaults()) werden NUR
        im Rueckgabewert ergaenzt - die Datei bleibt unveraendert, bis der
        naechste save()/add_channels()/remove_channels() sie zurueckschreibt."""
        path = self._path(job_id)
        try:
            with open(path, encoding="utf-8") as fh:
                job = json.load(fh)
        except OSError:
            raise KeyError(f"unknown job_id {job_id!r}")
        return _fill_param_defaults(job)

    # -- Anlage / Aenderung -----------------------------------------------

    def create(self, title, notes: str = "", params: dict | None = None,
               catalog=None) -> dict:
        """Legt einen neuen Job an: ID kollisionsfrei gegen den vorhandenen
        Bestand (new_job_id(), nicht das id-Feld aus new_job()), params
        ueber DEFAULT_PARAMS gemergt, validiert (catalog darf None sein -
        dann entfaellt nur die Kanalpruefung, unkritisch bei der noch
        leeren Kanalliste einer Neuanlage) und geschrieben."""
        with self._lock:
            job = new_job(title, notes)
            job["id"] = new_job_id(self._existing_ids())
            merged = dict(DEFAULT_PARAMS)
            if params:
                merged.update(params)
            job["params"] = merged

            errors = validate_job(job, catalog)
            if errors:
                raise ValueError("\n".join(errors))

            self._write(job)
            return job

    def save(self, job: dict, catalog=None) -> dict:
        """Speichert einen bestehenden Job: validiert, setzt modified_at
        neu, schreibt. Bei Validierungsfehlern wird NICHTS geschrieben,
        ValueError traegt alle Meldungen (Zeilenumbruch-getrennt)."""
        errors = validate_job(job, catalog)
        if errors:
            raise ValueError("\n".join(errors))
        with self._lock:
            job = dict(job)
            job["modified_at"] = _now_iso()
            self._write(job)
            return job

    def add_channels(self, job_id: str, ids, catalog=None) -> dict:
        """Haengt neue Kanal-IDs in Reihenfolge an ids an - bereits
        enthaltene werden still uebersprungen (Toggle-Semantik: nochmaliges
        Auswaehlen eines schon gewaehlten Kanals im Browser darf keinen
        Fehler werfen). Wuerde MAX_CHANNELS dadurch ueberschritten, wird
        NICHTS geschrieben; ValueError nennt die aktuelle Kanalzahl."""
        with self._lock:
            job = self.get(job_id)
            vorhanden = set(job["channels"])
            # gesehen faengt sowohl schon vorhandene als auch innerhalb von
            # ids selbst wiederholte IDs ab (z. B. ids=["T1","T1"]).
            neu = []
            gesehen = set(vorhanden)
            for cid in ids:
                if cid not in gesehen:
                    neu.append(cid)
                    gesehen.add(cid)

            geplante_zahl = len(job["channels"]) + len(neu)
            if geplante_zahl > MAX_CHANNELS:
                raise ValueError(
                    f"channels would have {geplante_zahl} entries, at most "
                    f"{MAX_CHANNELS} allowed (currently {len(job['channels'])})"
                )

            job["channels"] = job["channels"] + neu
            errors = validate_job(job, catalog)
            if errors:
                raise ValueError("\n".join(errors))

            job["modified_at"] = _now_iso()
            self._write(job)
            return job

    def remove_channels(self, job_id: str, ids, catalog=None) -> dict:
        """Entfernt die gegebenen Kanal-IDs aus job['channels'] - IDs, die
        gar nicht enthalten sind, werden still ignoriert."""
        with self._lock:
            job = self.get(job_id)
            entfernen = set(ids)
            job["channels"] = [cid for cid in job["channels"] if cid not in entfernen]

            errors = validate_job(job, catalog)
            if errors:
                raise ValueError("\n".join(errors))

            job["modified_at"] = _now_iso()
            self._write(job)
            return job

    def set_last_run(self, job_id: str, iso: str) -> dict:
        """Setzt last_run_at auf iso - bewusst NICHT modified_at (ein Lauf
        aendert die Job-DEFINITION nicht, siehe Moduldoku)."""
        with self._lock:
            job = self.get(job_id)
            job["last_run_at"] = iso
            self._write(job)
            return job

    def delete(self, job_id: str) -> None:
        """Entfernt die Job-Datei samt .bak/.tmp. Wirft KeyError, wenn die
        Job-Datei nicht existiert (eine ungueltige ID wirft weiterhin
        ValueError, siehe _path())."""
        with self._lock:
            path = self._path(job_id)
            if not os.path.isfile(path):
                raise KeyError(f"unknown job_id {job_id!r}")
            os.remove(path)
            for suffix in (".bak", ".tmp"):
                extra = path + suffix
                if os.path.isfile(extra):
                    os.remove(extra)


# ---------------------------------------------------------------------------
# Selbsttest - "python -m eb200_core.mscan_job --selftest" ohne Hardware
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile
    import time

    from eb200_core.known_freq import load_catalog

    with tempfile.TemporaryDirectory() as tmp:
        # -- synthetischer Katalog: 3 Einzelkanaele + ein 5er-Raster -------
        catalog_dir = os.path.join(tmp, "known_freq")
        os.makedirs(catalog_dir, exist_ok=True)
        with open(os.path.join(catalog_dir, "test.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "version": 1,
                "meta": {"service": "Test"},
                "defaults": {"demod": "FM", "bw_hz": 15000},
                "bands": [],
                "entries": [
                    {"kind": "channel", "id": "T1", "label": "Test 1", "freq_hz": 100_000_000},
                    {"kind": "channel", "id": "T2", "label": "Test 2", "freq_hz": 101_000_000},
                    {"kind": "channel", "id": "T3", "label": "Test 3", "freq_hz": 102_000_000},
                    {"kind": "raster", "id_prefix": "R", "label_template": "R{n}",
                     "start_hz": 200_000_000, "stop_hz": 200_040_000, "step_hz": 10_000,
                     "enabled": True},
                ],
            }, fh, ensure_ascii=False)
        cat = load_catalog(catalog_dir)
        assert cat.errors == [], cat.errors
        assert len(cat.channels) == 8, len(cat.channels)
        raster_id = next(c["id"] for c in cat.channels if c["id"].startswith("R-"))
        print(f"Katalog geladen: {len(cat.channels)} Kanaele (erwartet 8)")

        jobs_dir = os.path.join(tmp, "jobs", "mscan")
        store = MscanJobStore(jobs_dir=jobs_dir)

        # -- T1: new_job_id Format/Alphabet ---------------------------------
        for _ in range(1000):
            cid = new_job_id(set())
            assert _JOB_ID_RE.match(cid), cid
            assert not (set(cid) & set("0O1I")), cid
        print("T1 ok")

        # -- T2: create ------------------------------------------------------
        job1 = store.create("  Test\tJob  ", catalog=cat)
        assert job1["title"] == "Test Job", job1["title"]
        assert job1["channels"] == [], job1["channels"]
        assert job1["params"] == DEFAULT_PARAMS, job1["params"]
        assert job1["created_at"] == job1["modified_at"], job1
        assert job1["last_run_at"] is None, job1["last_run_at"]
        job1_id = job1["id"]
        assert os.path.isfile(os.path.join(jobs_dir, f"{job1_id}.json"))
        print("T2 ok")

        # -- T3: add_channels mit Duplikat, modified_at wandert -------------
        time.sleep(1.1)   # Sekundenaufloesung von _now_iso()
        job1 = store.add_channels(job1_id, ["T1", "T2", "T1"], catalog=cat)
        assert job1["channels"] == ["T1", "T2"], job1["channels"]
        assert job1["modified_at"] > job1["created_at"], job1
        print("T3 ok")

        # -- T4: Raster-Kanal-ID hinzufuegen ----------------------------------
        job1 = store.add_channels(job1_id, [raster_id], catalog=cat)
        assert job1["channels"] == ["T1", "T2", raster_id], job1["channels"]
        print("T4 ok")

        # -- T5: unbekannte ID -> ValueError, Datei unveraendert --------------
        vorher = open(os.path.join(jobs_dir, f"{job1_id}.json"), "rb").read()
        try:
            store.add_channels(job1_id, ["NIX"], catalog=cat)
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: unbekannte Kanal-ID")
        nachher = open(os.path.join(jobs_dir, f"{job1_id}.json"), "rb").read()
        assert vorher == nachher, "Datei durfte sich bei fehlgeschlagenem add_channels nicht aendern"
        print("T5 ok")

        # -- T6: MAX_CHANNELS (temporaer verkleinert) -------------------------
        global MAX_CHANNELS
        alt_max = MAX_CHANNELS
        MAX_CHANNELS = 3
        vorher = open(os.path.join(jobs_dir, f"{job1_id}.json"), "rb").read()
        try:
            store.add_channels(job1_id, ["T3"], catalog=cat)
        except ValueError as exc:
            assert "3" in str(exc), str(exc)
        else:
            raise AssertionError("ValueError erwartet: MAX_CHANNELS ueberschritten")
        nachher = open(os.path.join(jobs_dir, f"{job1_id}.json"), "rb").read()
        assert vorher == nachher, "Datei durfte sich bei ueberschrittenem MAX_CHANNELS nicht aendern"
        MAX_CHANNELS = alt_max
        print("T6 ok")

        # -- T7: remove_channels, unbekannte ID still ignoriert --------------
        job1 = store.remove_channels(job1_id, ["T2", "NIX"], catalog=cat)
        assert job1["channels"] == ["T1", raster_id], job1["channels"]
        print("T7 ok")

        # -- T8: validate_params ---------------------------------------------
        assert validate_params({"dwell_s": 11}), "dwell_s 11 muss einen Fehler ergeben"
        assert validate_params({"hold_s": -1}), "hold_s -1 muss einen Fehler ergeben"
        assert validate_params({"squelch_from_memory": "ja"}), \
            "squelch_from_memory 'ja' muss einen Fehler ergeben"
        assert validate_params({"min_recording_s": 0}), \
            "min_recording_s 0 muss einen Fehler ergeben"
        assert validate_params({"nix_da": 1}), "unbekannter Schluessel muss einen Fehler ergeben"
        assert validate_params({"dwell_s": None}) == [], "dwell_s None muss gueltig sein"
        assert validate_params({"dwell_s": 0.5, "min_recording_s": 1.0}), \
            "dwell_s 0.5 < min_recording_s 1.0 muss einen Fehler ergeben"
        assert validate_params({"dwell_s": 1.0, "min_recording_s": 1.0}) == [], \
            "dwell_s == min_recording_s muss gueltig sein (gleich ist erlaubt)"
        assert validate_params({"mode": "x"}), "unbekannter mode muss einen Fehler ergeben"
        assert validate_params({"mode": "inventur", "dwell_s": None}), \
            "inventur mit dwell_s None muss einen Fehler ergeben"
        assert validate_params({"mode": "inventur", "dwell_s": 3.0, "min_recording_s": 0.5}) == [], \
            "inventur mit dwell_s 3.0/min_recording_s 0.5 muss gueltig sein"
        assert validate_params({"mode": "ueberwachung", "dwell_s": None}) == [], \
            "ueberwachung mit dwell_s None muss gueltig sein"
        # T-1019
        assert validate_params({"stop_after": "runtime"}) == [], \
            "stop_after runtime muss gueltig sein"
        assert validate_params({"stop_after": "eins"}), \
            "unbekanntes stop_after muss einen Fehler ergeben"
        assert validate_params({"record_audio": "nein"}), \
            "record_audio 'nein' muss einen Fehler ergeben"
        assert validate_params({"level_active_dbuv": None}) == [], \
            "level_active_dbuv None muss gueltig sein"
        assert validate_params({"level_active_dbuv": 25}) == [], \
            "level_active_dbuv 25 muss gueltig sein"
        assert validate_params({"level_active_dbuv": 200}), \
            "level_active_dbuv 200 muss einen Fehler ergeben"
        assert validate_params({"dwell_s": 0.2, "min_recording_s": 1.0,
                                "record_audio": False}) == [], \
            "ohne Tonaufnahme darf dwell_s < min_recording_s sein"
        assert validate_params({"dwell_s": 0.2, "min_recording_s": 1.0,
                                "record_audio": True}), \
            "mit Tonaufnahme muss dwell_s < min_recording_s ein Fehler bleiben"
        print("T8 ok")

        # -- T9: .bak traegt den Inhalt VOR dem Speichern ---------------------
        vorher = open(os.path.join(jobs_dir, f"{job1_id}.json"), "rb").read()
        job1["notes"] = "geaendert"
        job1 = store.save(job1, catalog=cat)
        bak_path = os.path.join(jobs_dir, f"{job1_id}.json.bak")
        assert os.path.isfile(bak_path), bak_path
        with open(bak_path, "rb") as fh:
            bak_inhalt = fh.read()
        assert bak_inhalt == vorher, "job.json.bak muss den Stand VOR dem Speichern tragen"
        print("T9 ok")

        # -- T10: resolve_snapshot ---------------------------------------------
        snapshot = resolve_snapshot(job1, cat)
        assert len(snapshot) == len(job1["channels"]), snapshot
        assert [s["id"] for s in snapshot] == job1["channels"], snapshot
        erwartete_schluessel = {"id", "label", "freq_hz", "demod", "bw_hz",
                                "squelch_dbuv", "antenna", "service", "file", "tags"}
        for s in snapshot:
            assert set(s) == erwartete_schluessel, s
            assert isinstance(s["freq_hz"], int), s
        print("T10 ok")

        # -- T11: set_last_run aendert NICHT modified_at ------------------------
        modified_vorher = job1["modified_at"]
        job1 = store.set_last_run(job1_id, "2026-08-25T12:00:00+02:00")
        assert job1["last_run_at"] == "2026-08-25T12:00:00+02:00", job1["last_run_at"]
        assert job1["modified_at"] == modified_vorher, job1["modified_at"]
        print("T11 ok")

        # -- T12: list_jobs, Sortierung, kaputte Datei -------------------------
        time.sleep(1.1)
        job2 = store.create("Zweiter Job", catalog=cat)
        gelistet = store.list_jobs()
        assert len(gelistet) == 2, gelistet
        assert gelistet[0]["id"] == job2["id"], gelistet   # neuester zuerst

        with open(os.path.join(jobs_dir, "ZZZZZZ.json"), "w", encoding="utf-8") as fh:
            fh.write("{")
        gelistet = store.list_jobs()
        assert len(gelistet) == 2, gelistet
        assert len(store.errors) == 1, store.errors
        print("T12 ok")

        # -- T13: get() - ungueltiges Format vs. unbekannte ID -----------------
        try:
            store.get("../x")
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError erwartet: ungueltiges ID-Format")
        try:
            store.get("ABCDEF")
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: unbekannte, aber formal gueltige ID")
        print("T13 ok")

        # -- T14: delete -----------------------------------------------------
        store.delete(job1_id)
        assert not os.path.isfile(os.path.join(jobs_dir, f"{job1_id}.json"))
        assert not os.path.isfile(os.path.join(jobs_dir, f"{job1_id}.json.bak"))
        try:
            store.get(job1_id)
        except KeyError:
            pass
        else:
            raise AssertionError("KeyError erwartet: geloeschter Job")
        print("T14 ok")

        # -- T15: Altbestand ohne "mode" - get() ergaenzt nur im Speicher -----
        alt_id = "ZZZZZY"
        alt_path = os.path.join(jobs_dir, f"{alt_id}.json")
        alt_params = {k: v for k, v in DEFAULT_PARAMS.items() if k != "mode"}
        alt_job = {
            "id": alt_id, "title": "Altbestand", "notes": "",
            "created_at": "2020-01-01T00:00:00+01:00",
            "modified_at": "2020-01-01T00:00:00+01:00",
            "last_run_at": None, "channels": [], "params": alt_params,
        }
        with open(alt_path, "w", encoding="utf-8") as fh:
            json.dump(alt_job, fh, ensure_ascii=False, indent=2, sort_keys=True)
        vorher = open(alt_path, "rb").read()
        geladen = store.get(alt_id)
        assert geladen["params"]["mode"] == "ueberwachung", geladen["params"]
        nachher = open(alt_path, "rb").read()
        assert vorher == nachher, "get() darf die Datei nicht zurueckschreiben"
        print("T15 ok")

        # -- T16: Pegel-Inventur-Parameter (T-1019) --------------------------
        job3 = store.create("Pegel-Inventur", params=dict(
            PEGEL_INVENTUR_DEFAULTS, mode="inventur", level_active_dbuv=25.0),
            catalog=cat)
        assert job3["params"]["mode"] == "inventur", job3["params"]
        assert job3["params"]["stop_after"] == "runtime", job3["params"]
        assert job3["params"]["record_audio"] is False, job3["params"]
        assert job3["params"]["level_active_dbuv"] == 25.0, job3["params"]
        assert job3["params"]["squelch_from_memory"] is True, job3["params"]
        gelesen = store.get(job3["id"])
        assert gelesen["params"] == job3["params"], gelesen["params"]

        # Altbestand ohne die neuen Schluessel: get() ergaenzt sie aus
        # DEFAULT_PARAMS, das Verhalten bleibt damit das bisherige.
        alt2_id = "ZZZZZX"
        alt2_path = os.path.join(jobs_dir, f"{alt2_id}.json")
        alt2_params = {k: v for k, v in DEFAULT_PARAMS.items()
                       if k not in ("stop_after", "record_audio", "level_active_dbuv")}
        with open(alt2_path, "w", encoding="utf-8") as fh:
            json.dump({"id": alt2_id, "title": "Altbestand 2", "notes": "",
                       "created_at": "2020-01-01T00:00:00+01:00",
                       "modified_at": "2020-01-01T00:00:00+01:00",
                       "last_run_at": None, "channels": [], "params": alt2_params},
                      fh, ensure_ascii=False, indent=2, sort_keys=True)
        geladen2 = store.get(alt2_id)
        assert geladen2["params"]["stop_after"] == "cycle", geladen2["params"]
        assert geladen2["params"]["record_audio"] is True, geladen2["params"]
        assert geladen2["params"]["level_active_dbuv"] is None, geladen2["params"]
        print("T16 ok")

    print("\n[mscan_job] Selbsttest erfolgreich abgeschlossen.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    # cp1252-Konsole: reconfigure vermeidet UnicodeEncodeError bei
    # Diakritika in Fehlertexten (Muster known_freq.py).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(description="MSCAN-Job-Definitionen")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
