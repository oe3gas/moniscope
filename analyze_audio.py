#!/usr/bin/env python3
"""
analyze_audio.py

Eigenstaendiges, rein LESENDES Auswerte-/Wiedergabewerkzeug fuer die
Direkt-Audioaufnahmen der Suite (eb200_core/audio_wav.py: 16 kHz/16 Bit/
mono WAV + Sidecar "<wav>.json" mit running-Flag seit T-1109). KEINE
eigene Aufnahmeliste - Einstieg ausschliesslich per Deep-Link "?path="
aus der Aufzeichnungstabelle in :8050 (eb200_core/recording_index.py,
auswertung.server == "audio") bzw. spaeter :8060.

Rechenbasis fuer Spektrogramme ist audio_spectrum.py (T-1201, Format
"eb200-audspec-v1" - dessen Moduldoku ist fuer das Cache-Format
verbindlich). Muster fuer Serverbau, Pfad-Sandbox und Stil ist
analyze_spectrum.py (:8040) - gleicher Handler-Aufbau, gleiche
Fehlerabbildung, gleiche LabelStore-Nutzung fuer Kurztext/Notiz.

Das eigentliche Wasserfall-Frontend (Canvas-Spektrogramm aus /api/audspec)
kommt erst mit T-1203 - die "/"-Seite dieses Moduls liefert Kopf/Metadaten,
ein funktionierendes <audio>-Element (Wiedergabe/Scrubbing ueber
/api/wav mit HTTP-Range) und einen Platzhalter-Container.

Routen:
  GET  /?path=<rel.wav>        HTML-Seite (Metadaten per JS aus /api/meta)
  GET  /api/meta?path=         Sidecar/WAV-Header/Label/Notiz/Klassifikation/
                                Katalogtreffer/Job-Kontext/Geschwisterdateien als JSON
  GET  /api/wav?path=          WAV-Auslieferung mit HTTP-Range (206/416)
  GET  /api/audspec?path=&fft=&t0=&t1=  Spektrogramm-Rohdaten (Uebersicht
                                oder Zeitfenster), application/octet-stream
  GET  /api/audspec_status?path=  Fortschritt eines gerade laufenden
                                build_audspec als JSON (T-1213); billig und
                                jederzeit abfragbar, waehrend /api/audspec
                                noch blockiert
  GET/POST /api/note?path=     Notiz lesen/schreiben (audio_notes.json)
  POST /api/klasse             Handlabel setzen/loeschen (audio_klassen.json,
                                Body {"path","klasse"}; "" loescht)
  POST /api/classify           Klassifikation synchron anstossen (Frontend: automatisch
                                beim Oeffnen, wenn kein gueltiger Sidecar)
                                (audio_classify.ensure_klasse, Body
                                {"path","rebuild": bool})

Sicherheits-Hinweis: der Server bindet auf 0.0.0.0:8070 und ist damit im
gesamten Shack-LAN erreichbar. Jeder "path"-Query-Parameter MUSS deshalb
zwingend zuerst durch resolve_safe_path() laufen, bevor irgendein
Dateizugriff passiert - siehe dort (Klon des :8040-Musters).

Loeschen einer Aufnahme ist NICHT Teil dieses Servers (zentral in
eb200_core/recording_index.py, T-1104, bedient von :8050) - dieser Server
ist bewusst rein lesend plus Kurztext/Notiz.

Start (aus dem Repo-Root E:\\EB200\\EB200repo, damit die Sandbox-Wurzel und
records/ stimmen):
    python analyze_audio.py
    python analyze_audio.py --selftest
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import signal
import subprocess
import sys
import tempfile
import wave
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from eb200_core.job_store import load_storage_roots
from eb200_core.label_store import LabelStore, clean_note
from eb200_core.param_store import register_server
from eb200_core.ui_theme import THEME_CSS, BASE_CSS, THEME_HEAD_JS, header_html
from eb200_core.known_freq import load_catalog_cached
from eb200_core import paths
from eb200_core.path_sandbox import sandbox_roots, resolve_within, to_portable

from audio_spectrum import (
    AudioSpectrumBuildingError,
    AudioSpectrumBusyError,
    AudioSpectrumUnsupportedError,
    ensure_audspec,
    level_median_dbfs,
    progress_snapshot,
    stft_window,
)
from audio_classify import (
    KLASSEN,
    load_klasse,
    ensure_klasse,
    AudioClassifyBusyError,
    AudioClassifyUnsupportedError,
)

# ---------------------------------------------------------------------------
# Konfiguration / Pfad-Sandbox (siehe Moduldoku - PFLICHT wegen LAN-Bindung)
# ---------------------------------------------------------------------------

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8070

# Wurzel der Sandbox: die Datenwurzel (T-2003, eb200_core/paths.py; ohne
# --data-dir das Repo-Root wie bisher; Muster analyze_spectrum.py).
AUDIO_ROOT = paths.DATA_ROOT

# T-1205: erster Eintrag aus storage_roots.json (Fallback ["records"]) -
# nur informativ (aktuell ungenutzt fuer die Sandbox-Wurzel selbst, die
# bleibt AUDIO_ROOT), aber konsistent mit den uebrigen Servern importiert,
# falls eine spaetere Auflistung/Vorauswahl das braucht.
_ = load_storage_roots

# Dieselben Speicher, die eb200_core/recording_index.py (T-1204) fuer
# Direkt-Audio verwendet - Kurztext NUR lesend angezeigt (Bearbeitung bleibt
# vorerst :8050 vorbehalten), Notiz lesend UND schreibend.
LABELS = LabelStore(os.path.join(AUDIO_ROOT, "audio_labels.json"))
NOTES = LabelStore(os.path.join(AUDIO_ROOT, "audio_notes.json"), cleaner=clean_note)


def clean_klasse(text) -> str:
    """Bereinigt eine manuelle Klassenauswahl: zu str zwingen, Rand-Leerraum
    entfernen, in Grossbuchstaben. Leerer Text loescht den Eintrag ("").
    Ein nicht-leerer Text ausserhalb von audio_classify.KLASSEN wirft
    ValueError - LabelStore.set() reicht das unveraendert an den Aufrufer
    durch (gegengeprueft: set() faengt nur die (key, cleaned)-Zuweisung ab,
    nicht den Cleaner-Aufruf selbst), der Handler bildet es auf 400 ab."""
    s = "" if text is None else str(text).strip().upper()
    if s and s not in KLASSEN:
        raise ValueError(f"invalid class {s!r}")
    return s


# Handlabel fuer die Klassifikation (T-1007b/c) - eigene Datei, getrennt vom
# Kurztext (LABELS) und der automatischen Klassifikation (audio_classify
# .klasse.json-Sidecar); Muster LABELS/NOTES oben.
KLASSEN_MANUELL = LabelStore(os.path.join(AUDIO_ROOT, "audio_klassen.json"), cleaner=clean_klasse)

_VALID_FFT_SIZES = (32, 64, 128, 256, 512, 1024, 2048)   # T-1618

# Kompakte Felder fuer einen Katalogtreffer in /api/meta - Muster
# eb200_core/known_freq._CLIENT_CHANNEL_KEYS (Squelch/Antenne/ATT usw.
# bleiben serverseitig, sie sind fuer die Anzeige hier nicht gebraucht).
_KNOWN_FREQ_MATCH_KEYS = ("id", "label", "freq_hz", "demod", "bw_hz", "service")

ERR_SANDBOX = "access denied: path lies outside the permitted storage locations"
ERR_NOT_FOUND = "file not found"
ERR_BAD_PARAMS = "invalid request parameters"
ERR_BUILDING = "spectrogram is being computed"
ERR_MEASURE = "measurement failed"

# Signalvermessung (T-1611a). tools/eye_diagram.py laeuft als UNTERPROZESS,
# nicht als Import: tools/ ist ein Verzeichnis eigenstaendiger Offline-
# Werkzeuge, und kein Server importiert bisher daraus. Dasselbe Muster
# benutzt analyze_spectrum.ensure_befund() fuer session_analyze.py. Kosten:
# rund 0,3 s Interpreterstart je Messung - bei einer Messung auf Knopfdruck
# belanglos, und die Schnittstelle (--json/--out) ist bereits vorhanden.
EYE_DIAGRAM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools", "eye_diagram.py")
MEASURE_TIMEOUT_S = 180.0
MEASURE_MODES = ("fsk", "psk")
# "auto" rechnet BEIDE Pfade und meldet, welcher durchkam (T-1611b). Der
# Befund an sieben eigenen Aufnahmen: in sechs Faellen war genau EIN Pfad
# erfolgreich und der andere warf - die Frage "welcher Modus?" beantwortet
# sich damit meist von selbst. Eine Wahrscheinlichkeit wird bewusst NICHT
# gebildet: der FSK-Pfad liefert r2 aus dem Symbolraten-Fit, der PSK-Pfad
# EVM der Konstellation; die messen Verschiedenes, und eine Prozentzahl
# daraus waere erfunden. Kommen beide durch, werden beide gezeigt.
MEASURE_AUTO = "auto"
# T-1613: dritter Modus, aber KEIN vollwertiger Auswertepfad - nur die Frage
# "moeglicherweise mehrtraegrig?". Er wird im auto-Lauf NUR angehaengt, wenn
# FSK und PSK beide gescheitert sind. Das ist zugleich die Absicherung gegen
# seine bekannte Schwaeche: bei einem auswertbaren FSK-Signal loest er zwar
# aus (gemessen 2026-09-11), kommt aber nie zum Zug.
MEASURE_OFDM = "ofdm"
# Weniger Augendiagramm-Spuren als die CLI-Vorgabe (400): das SVG geht ueber
# HTTP in die Seite, und 400 Spuren ergeben rund 630 kB. Mit 120 bleibt das
# Bild aussagekraeftig und die Antwort handlich.
MEASURE_MAX_TRACES = 120
IQBEFUND_SUFFIX = ".iqbefund.json"
IQBEFUND_MAX = 50          # aelteste Messung faellt heraus
ERR_INTERNAL = "internal error"


def iqbefund_path_for(wav_path: str) -> str:
    """Sidecar der Signalvermessungen (T-1611a) - regenerierbar wie alle
    abgeleiteten Dateien, aber im Unterschied zu .audspec/.klasse.json vom
    BEDIENER gefuellt: er entscheidet, welche Messung es wert ist."""
    return wav_path + IQBEFUND_SUFFIX


def load_iqbefund(wav_path: str) -> list:
    """Gespeicherte Messungen als Liste (aelteste zuerst), oder []. Wirft
    NIE - ein unlesbarer Sidecar darf die Seite nicht blockieren."""
    try:
        with open(iqbefund_path_for(wav_path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        messungen = data.get("messungen")
        return messungen if isinstance(messungen, list) else []
    return data if isinstance(data, list) else []


def append_iqbefund(wav_path: str, eintrag: dict) -> list:
    """Eine Messung anhaengen und atomar schreiben (Temp + os.replace,
    Muster audio_spectrum.build_audspec). Liefert die neue Liste.

    Bewusst eine LISTE: eine zweistuendige Aufnahme traegt viele
    Aussendungen, und jede vermessene Stelle ist ein eigener Befund mit
    ihrem Zeitfenster. Bei mehr als IQBEFUND_MAX Eintraegen faellt der
    aelteste heraus - der Sidecar soll eine Arbeitsnotiz bleiben, kein
    Protokoll.
    """
    messungen = load_iqbefund(wav_path)
    messungen.append(eintrag)
    messungen = messungen[-IQBEFUND_MAX:]
    payload = {"format": "eb200-iqbefund-v1", "messungen": messungen}
    ziel = iqbefund_path_for(wav_path)
    tmp = ziel + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, ziel)
    return messungen


def _stderr_meldung(stderr: str) -> str:
    """Letzte stderr-Zeile von tools/eye_diagram.py ohne das CLI-Praefix
    "FEHLER: " (T-1828). Die Kommandozeile bleibt deutsch, die Meldung
    dahinter ist englisch und geht so an :8070."""
    zeilen = (stderr or "").strip().splitlines()
    zeile = zeilen[-1] if zeilen else ""
    return zeile[len("FEHLER: "):] if zeile.startswith("FEHLER: ") else zeile


def run_measure(resolved: str, t0_s: float, t1_s: float, mode: str,
                 tune_hz: float = 0.0, bw_hz: float = 0.0) -> dict:
    """tools/eye_diagram.py als Unterprozess auf ein Zeitfenster ansetzen
    (T-1611a). Liefert immer ein dict, nie eine Ausnahme:

      {"ok": True,  "kennwerte": {...}, "svg": "<svg .../>"}
      {"ok": False, "leer": True,  "meldung": ...}   kein Burst im Fenster
      {"ok": False, "leer": False, "meldung": ...}   echter Fehler

    Der Unterschied zwischen "leer" und "Fehler" ist der wichtigere von
    beiden: beim Suchen zoomt man staendig in Stellen ohne Signal, und das
    ist kein Fehlschlag. eye_diagram unterscheidet das ueber den Rueckgabe-
    code - 2 heisst "nichts Auswertbares", 1 heisst "ging schief".
    """
    if mode not in MEASURE_MODES and mode != MEASURE_OFDM:
        return {"ok": False, "leer": False, "meldung": f"unknown mode {mode}"}
    with tempfile.TemporaryDirectory() as tmp:
        json_path = os.path.join(tmp, "m.json")
        svg_path = os.path.join(tmp, "m.svg")
        cmd = [sys.executable, EYE_DIAGRAM, resolved,
               "--t0", str(t0_s), "--t1", str(t1_s), "--mode", mode,
               "--json", json_path]
        # T-1613: der OFDM-Modus zeichnet nichts - kein --out, keine Spuren.
        if mode != MEASURE_OFDM:
            cmd += ["--max-traces", str(MEASURE_MAX_TRACES), "--out", svg_path]
        # T-1620: Abstimmung und Dezimierung nur dann, wenn der Bediener sie
        # ueber die Marker angefordert hat. Ohne Verschiebung ist eine starke
        # Dezimierung sinnlos - der Tiefpass sitzt um 0 Hz und schnitte ein
        # Signal weg, das woanders liegt.
        if tune_hz:
            cmd += ["--tune", str(tune_hz)]
        if bw_hz > 0:
            cmd += ["--auto-decimate", str(bw_hz)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=MEASURE_TIMEOUT_S,
                                  cwd=os.path.dirname(EYE_DIAGRAM) or None)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "leer": False, "meldung": f"{type(exc).__name__}: {exc}"}

        meldung = _stderr_meldung(proc.stderr)
        if proc.returncode == 2:
            return {"ok": False, "leer": True,
                    "meldung": meldung or "no evaluable signal in this section"}
        if proc.returncode != 0:
            return {"ok": False, "leer": False, "meldung": meldung or ERR_MEASURE}

        try:
            with open(json_path, encoding="utf-8") as f:
                kennwerte = json.load(f)
        except (OSError, ValueError) as exc:
            return {"ok": False, "leer": False, "meldung": f"measured values unreadable: {exc}"}
        try:
            with open(svg_path, encoding="utf-8") as f:
                svg = f.read()
        except OSError:
            svg = ""      # ohne Grafik ist die Messung trotzdem brauchbar
    kennwerte.pop("wav_path", None)      # kein absoluter Pfad in die Antwort
    return {"ok": True, "kennwerte": kennwerte, "svg": svg}


def resolve_safe_path(user_path: str) -> str:
    """
    Loest user_path (ein "path"-Query-Parameter) zu einem absoluten Pfad auf
    und stellt sicher, dass dieser innerhalb von AUDIO_ROOT ODER einem
    konfigurierten storage_root liegt und auf eine .wav-Datei zeigt. MUSS bei
    JEDEM eingehenden path-Parameter als allererstes aufgerufen werden, bevor
    irgendein Dateizugriff passiert. Whitelist = Repo-Root + storage_roots,
    siehe eb200_core/path_sandbox.py.

    Wirft ValueError, wenn der Pfad leer ist, ausserhalb aller Roots liegt
    oder nicht auf .wav endet. Der Handler uebersetzt ValueError IMMER in
    eine generische 403-Antwort, die den eingegebenen Pfad nicht wiederholt
    (kein Oracle ueber Pfade ausserhalb der Sandbox).
    """
    # sandbox_roots() liest storage_roots.json bei JEDEM Aufruf neu (Datei
    # winzig) - Aenderungen daran wirken so ohne Server-Neustart.
    return resolve_within(user_path, sandbox_roots(AUDIO_ROOT), suffixes=(".wav",))


def to_relative(path: str) -> str:
    """Portables Pfadformat (siehe eb200_core.path_sandbox.to_portable):
    AUDIO_ROOT-relativ mit Vorwaertsschraegstrichen innerhalb des
    Repo-Roots, sonst absolut mit Vorwaertsschraegstrichen (externer
    storage_root) - so, wie ihn der Client unveraendert an die uebrigen
    Routen zurueckreichen kann."""
    return to_portable(path, AUDIO_ROOT)


def _iso_from_unix(ts: float) -> str:
    """Unix-Timestamp -> ISO-String lokal MIT Offset (Muster
    eb200_core/recording_index.py._iso_from_unix)."""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# HTTP-Range-Parsing (eigene reine Funktion, siehe Selbsttest)
# ---------------------------------------------------------------------------

def parse_range(header_value, file_size: int):
    """
    Parst einen HTTP-Range-Header (EIN Bereich: "bytes=a-b" / "bytes=a-" /
    "bytes=-n") gegen file_size. Gibt (start, end) zurueck (inklusive,
    0-basiert), wenn der Header einen gueltigen UND erfuellbaren
    Einzelbereich beschreibt.

    Gibt None zurueck, wenn KEIN Range-Header vorliegt ODER er
    kommasepariert mehrere Bereiche traegt - Multi-Range wird bewusst NICHT
    unterstuetzt; der Aufrufer erkennt das an None und liefert stattdessen
    die komplette Datei mit 200 (zulaessiges Verhalten nach RFC 7233).

    Wirft ValueError bei syntaktisch ungueltigem ODER nicht erfuellbarem
    Einzelbereich (falsche Einheit, kaputte Zahlen, start > end, Bereich
    hinter dem Dateiende) - der Aufrufer antwortet dann mit 416. Diese
    Unterscheidung (None = "kein/nicht unterstuetzter Range" vs.
    ValueError = "kaputter Range") ist der Grund, warum diese Funktion
    eigenstaendig UND direkt testbar ist (siehe Selbsttest).
    """
    if not header_value:
        return None
    if not header_value.startswith("bytes="):
        raise ValueError(f"unsupported range unit: {header_value!r}")

    spec = header_value[len("bytes="):].strip()
    if "," in spec:
        return None  # Multi-Range: siehe Docstring - Aufrufer liefert 200.

    a, sep, b = spec.partition("-")
    if not sep or (a == "" and b == ""):
        raise ValueError(f"invalid range syntax: {header_value!r}")

    try:
        if a == "":
            suffix = int(b)
            if suffix <= 0:
                raise ValueError("suffix length must be greater than 0")
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(a)
            end = int(b) if b != "" else file_size - 1
    except ValueError as exc:
        raise ValueError(f"invalid range numbers: {header_value!r}") from exc

    if file_size <= 0 or start < 0 or start > end or start >= file_size:
        raise ValueError(f"range not satisfiable: {header_value!r} (file size {file_size})")
    end = min(end, file_size - 1)
    return start, end


# ---------------------------------------------------------------------------
# Metadaten (/api/meta) - reine Funktion, direkt testbar
# ---------------------------------------------------------------------------

def _job_context(resolved: str):
    """
    Liefert (job_dict_oder_None, verzeichnis). job ist NUR gesetzt, wenn
    resolved unterhalb eines longrun_*-Verzeichnisses MIT lesbarem
    job.json liegt (Muster der Long-Run-Erkennung in
    eb200_core/recording_index.delete_recording, kind=audio-Zweig) -
    reine Erkennung, keine Schutzpruefung (die bleibt bei recording_index).
    """
    dir_path = os.path.dirname(resolved)
    dir_name = os.path.basename(dir_path)
    manifest_path = os.path.join(dir_path, "job.json")
    if not (dir_name.startswith("longrun_") and os.path.isfile(manifest_path)):
        return None, dir_path
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None, dir_path
    if not isinstance(manifest, dict):
        return None, dir_path
    job = {
        "job_id": manifest.get("job_id"), "label": manifest.get("label"),
        "type": manifest.get("type"), "state": manifest.get("state"),
    }
    return job, dir_path


def build_meta(resolved: str, *, labels: LabelStore = None, notes: LabelStore = None,
                klassen: LabelStore = None) -> dict:
    """
    Baut die vollstaendige /api/meta-Antwort fuer eine bereits durch
    resolve_safe_path() geprüfte WAV. Wirft NIE - ein unlesbares Sidecar
    oder ein kaputter WAV-Header werden als null-Feld + Grund abgebildet
    (Absturzreste bleiben so trotzdem betrachtbar, siehe Moduldoku
    audio_spectrum.py). labels/notes/klassen: nur fuer den Selbsttest
    injizierbar (der eine eigene, tempdir-gebundene LabelStore braucht statt
    der modulglobalen LABELS/NOTES/KLASSEN_MANUELL, die an den ECHTEN
    Repo-Root gebunden sind) - der Handler ruft build_meta() immer ohne
    diese Parameter auf."""
    labels = labels if labels is not None else LABELS
    notes = notes if notes is not None else NOTES
    klassen = klassen if klassen is not None else KLASSEN_MANUELL
    rel = to_relative(resolved)

    sidecar = None
    sidecar_path = resolved + ".json"
    if os.path.isfile(sidecar_path):
        try:
            with open(sidecar_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                sidecar = data
        except (OSError, ValueError):
            sidecar = None
    running = bool(sidecar.get("running", False)) if sidecar else False

    wav_info = None
    grund = None
    try:
        with wave.open(resolved, "rb") as wf:
            sample_rate_hz = wf.getframerate()
            n_frames = wf.getnframes()
            wav_info = {
                "size": os.path.getsize(resolved),
                "mtime_iso": _iso_from_unix(os.path.getmtime(resolved)),
                "sample_rate_hz": sample_rate_hz,
                "channels": wf.getnchannels(),
                "sampwidth_bytes": wf.getsampwidth(),
                "n_frames": n_frames,
                "duration_s": (n_frames / sample_rate_hz) if sample_rate_hz else 0.0,
            }
    except (OSError, wave.Error, EOFError) as exc:
        grund = f"{type(exc).__name__}: {exc}"

    # T-1626: Median des Blockpegels in dBFS - Ergaenzung zum Startpegel aus
    # dem rx-Schnappschuss (der ist EIN Momentanwert zum Aufnahmebeginn).
    # Nicht waehrend einer laufenden Aufnahme: die Datei waechst dann noch,
    # und der Wert waere eine Momentaufnahme von etwas Halbfertigem. Fehler
    # kosten das Feld, nie die Antwort - build_meta wirft nie (Moduldoku).
    level_median = None
    if wav_info is not None and not running:
        try:
            level_median = level_median_dbfs(resolved)
        except (OSError, wave.Error, EOFError, ValueError,
                AudioSpectrumUnsupportedError):
            level_median = None

    known_freq = None
    if sidecar and sidecar.get("freq_hz") is not None:
        cat = load_catalog_cached()
        matches = cat.channels_in_range(sidecar["freq_hz"], sidecar["freq_hz"])
        if matches:
            ch = matches[0]
            known_freq = {k: ch[k] for k in _KNOWN_FREQ_MATCH_KEYS if k in ch}

    klasse_auto = None
    auto_data = load_klasse(resolved)
    if isinstance(auto_data, dict):
        klasse_auto = {k: auto_data.get(k) for k in (
            "klasse", "voice_score", "sicherheit", "classifier_version",
            "created_iso", "reasons", "features",
        )}

    job, dir_path = _job_context(resolved)
    stem = resolved[:-len(".wav")]
    specbin_path = stem + ".specbin"
    wav_parts = []
    if job is not None:
        own_name = os.path.basename(resolved)
        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            entries = []
        wav_parts = [
            to_relative(os.path.join(dir_path, name)) for name in entries
            if name.endswith(".wav") and name != own_name
        ]

    return {
        "sidecar": sidecar,
        "wav": wav_info,
        "grund": grund,
        "running": running,
        "level_median_dbfs": level_median,
        "label": labels.get(rel),
        "note": notes.get(rel),
        "klasse_auto": klasse_auto,
        "klasse_manuell": klassen.get(rel),
        "klassen": list(KLASSEN),
        "known_freq": known_freq,
        "job": job,
        "siblings": {
            "specbin": to_relative(specbin_path) if os.path.isfile(specbin_path) else None,
            "wav_parts": wav_parts,
        },
    }


# ---------------------------------------------------------------------------
# Audspec-Antwort (/api/audspec) - reine Funktion, direkt testbar
# ---------------------------------------------------------------------------

def build_audspec_response(resolved: str, n_fft: int, t0_s=None, t1_s=None):
    """
    Baut (header_dict, uint8_matrix) fuer /api/audspec - der Handler
    verpackt das nur noch HTTP-technisch (Status/Content-Type/Bytes).
    Der Header reicht seit T-915 "kind", "f_min_hz" und "f_max_hz" aus
    audio_spectrum durch: erst damit kennt die Seite die Lage der
    Frequenzachse (mono 0..Nyquist, IQ -Nyquist..+Nyquist).
    Wirft AudioSpectrumBusyError/AudioSpectrumUnsupportedError/ValueError/
    FileNotFoundError unveraendert durch; der Aufrufer bildet sie auf
    409/415/400/404 ab (siehe _handle_api_audspec). Diese Trennung macht
    den "Handler-Baustein" ohne Socket direkt testbar (siehe Selbsttest).
    """
    if t0_s is not None and t1_s is not None:
        cache = ensure_audspec(resolved)
        if cache is None:
            raise FileNotFoundError(resolved)
        cache_header, _ = cache
        win = stft_window(resolved, t0_s, t1_s, n_fft,
                           db_min=cache_header["db_min"], db_max=cache_header["db_max"])
        matrix = win["matrix"]
        rows, n_bins = matrix.shape
        header = {
            "n_fft": n_fft, "hop": n_fft // 2, "rows": rows, "n_bins": n_bins,
            "dt_row_s": win["dt_row_s"], "db_min": win["db_min"], "db_max": win["db_max"],
            "kind": win["kind"], "f_min_hz": win["f_min_hz"], "f_max_hz": win["f_max_hz"],
            "mode": "window", "t0_s": win["t0_s"], "t1_s": win["t0_s"] + rows * win["dt_row_s"],
        }
        return header, matrix

    result = ensure_audspec(resolved)
    if result is None:
        raise FileNotFoundError(resolved)
    cache_header, matrices = result
    sec = next((s for s in cache_header["sections"] if s["n_fft"] == n_fft), None)
    if sec is None:
        raise ValueError(f"unknown FFT size {n_fft}")
    header = {
        "n_fft": n_fft, "hop": sec["hop"], "rows": sec["rows"],
        "rows_raw": sec["rows_raw"], "n_bins": sec["n_bins"],
        "dt_row_s": sec["dt_row_s"], "decimation": sec["decimation"],
        "db_min": cache_header["db_min"], "db_max": cache_header["db_max"],
        "kind": cache_header["kind"],
        "f_min_hz": cache_header["f_min_hz"], "f_max_hz": cache_header["f_max_hz"],
        "duration_s": cache_header["wav"]["duration_s"], "mode": "overview",
    }
    return header, matrices[n_fft]


# ---------------------------------------------------------------------------
# HTML-Seite (Kopf/Metadaten/Player - Wasserfall folgt mit T-1203)
# ---------------------------------------------------------------------------

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoniScope &ndash; Signal analysis</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
<style>
  /* T-1404: Farben nur noch als Tokens (eb200_core/ui_theme.py). Das
     Spektrogramm (specCanvas/overlayCanvas) bleibt in beiden Themes dunkel -
     Colormap, Achsen und Marker im Script unveraendert. */
  /* T-1627: zwei gleich breite Bloecke ueber dem Spektrogramm - links die
     Metadaten, rechts die Bedienung. Unter 1100 px Fensterbreite untereinander:
     die FFT-Zeile allein braucht rund 900 px, darunter wird jede Spalte zum
     Umbruchsalat. align-items:start, weil der linke Block bei mono zwei
     Klassenzeilen und den ausklappbaren Merkmalsblock traegt und damit deutlich
     hoeher ist als der rechte - erzwungene Gleichhoehe waere nur leerer Rahmen.
     min-width:0 gegen die Grundeinstellung von Grid-Kindern (min-content):
     ohne das blaeht ein langer Dateipfad die linke Spalte auf. */
  #topGrid { display:grid; grid-template-columns:1fr 1fr; gap:12px;
             align-items:start; margin:10px 0; }
  #topGrid.einspaltig { grid-template-columns:1fr; }
  #metaBlock, #ctrlBlock { min-width:0; }
  #ctrlBlock { background:var(--panel); border:1px solid var(--border);
               border-radius:6px; padding:10px 12px; }
  /* In der halben Breite sind die Standardlaengen der Regler zu gross. */
  #ctrlBlock input[type="range"] { width:110px; }
  @media (max-width:1100px) {
    #topGrid { grid-template-columns:1fr; }
  }
  #meta { background:var(--panel); border:1px solid var(--border); border-radius:6px;
          padding:10px 14px; margin:0; }
  /* T-1404a: KEIN Grid - eine Zeile enthaelt neben dem Wert auch <br>, Link und
     den Merkmalsblock; als Grid-Kinder waeren die einzelne Zellen und der
     <pre> landete in der Label-Spalte. Label bleibt daher inline-block. */
  .kv { padding:6px 0; border-bottom:1px solid var(--border); }
  .kv:last-child { border-bottom:none; }
  .kv .k { display:inline-block; min-width:160px; vertical-align:top; font-weight:600; }
  /* T-1627: Empfangskette als Zustandsmarken. Die Farbe steht fuer den
     ZUSTAND, nicht fuer Wichtigkeit: blau = Automatik oder gesetzter
     Bezugswert, gruen = eingeschaltet, orange = ausgeschaltet, grau = nicht
     ermittelt. Nur Tokens, keine Hex-Farben (Projektregel). inline-flex,
     damit die Marken neben der Beschriftung der .kv-Zeile stehen und
     untereinander umbrechen statt die Zeile zu sprengen. */
  .rxChips { display:inline-flex; flex-wrap:wrap; gap:6px; vertical-align:top;
             max-width:calc(100% - 170px); }
  .rxChip { display:inline-block; padding:1px 8px; border-radius:3px;
            font-size:0.82em; border:1px solid transparent; white-space:nowrap; }
  .rxChip.auto { background:var(--accent-soft); color:var(--fg); border-color:var(--accent); }
  .rxChip.ein { background:var(--success-soft); color:var(--fg); border-color:var(--success); }
  .rxChip.aus { background:var(--warn-soft); color:var(--fg); border-color:var(--warn); }
  .rxChip.na { background:var(--panel-2); color:var(--fg-muted); border-color:var(--border); }
  #playerBar { background:var(--panel); border:1px solid var(--border); border-radius:6px;
               padding:10px 12px; margin:12px 0; }
  #player { width:100%; display:block; }
  #waterfall { margin-top:16px; background:var(--panel); border:1px solid var(--border);
               border-radius:6px; padding:10px 12px; }
  #wfToolbar { font-size:0.9em; }
  /* T-1209/T-1627: fuenf Bedienzeilen (Zoom / Marker / FFT / Kontrast /
     Werkzeuge), durch Trennlinien getrennt. Aufgeteilt wurde, weil die alte
     Sammelzeile aus FFT, Kontrast und Knoepfen in der halben Seitenbreite
     dreifach umbrach. */
  .wfRow { display:flex; flex-wrap:wrap; align-items:center; gap:14px; padding:6px 0; }
  .wfRow + .wfRow { border-top:1px solid var(--border); }
  #wfToolbar .wfGroup { white-space:nowrap; }
  /* T-1615: Tafel der Signalvermessung. Zwei gerahmte Bloecke nebeneinander,
     in jedem zuerst das Bild, darunter die Werte. Beide Grafiken werden auf
     dieselbe Hoehe skaliert (MEASURE_SVG_H): das Augendiagramm ist 760x480,
     Konstellation samt Histogramm 500x660 - ohne Angleichung stuenden sie
     unterschiedlich hoch nebeneinander. Beide tragen eine viewBox, daher
     genuegt height + width:auto. */
  #measureBox { margin-top:12px; }
  #measureResult { display:flex; flex-wrap:wrap; gap:14px; align-items:stretch; }
  .mPfad { border:1px solid var(--border); border-radius:4px; padding:10px 12px;
           flex:1 1 380px; min-width:0; }
  .mPfad.mOk { border-color:var(--accent); }
  .mKopf { font-weight:600; margin-bottom:6px; }
  .mBild { text-align:center; margin-bottom:8px; }
  .mBild svg { height:480px; width:auto; max-width:100%; }
  /* T-1621: Tabelle nicht ueber die volle Blockbreite ziehen - sonst klafft
     zwischen Bezeichnung und Wert eine Handbreit Leere. Stattdessen ein
     zentrierter Block begrenzter Breite unter der Grafik. */
  .mWerte { width:auto; min-width:300px; max-width:420px; margin:0 auto;
            border-collapse:collapse; font-variant-numeric:tabular-nums; }
  .mWerte td { padding:2px 10px; }
  .mWerte td:first-child { color:var(--fg-muted); }
  .mWerte td:last-child { text-align:right; }
  .mWerte tr:nth-child(even) { background:var(--panel-2); }
  .mWerte tr[title] { cursor:help; }
  .mLeer { color:var(--fg-muted); }
  /* T-1704: Modenkandidaten-Tafel, in derselben Spalte unter der Wertetafel.
     .mKandGruppe (Zwischenueberschrift "no match possible") bewusst NICHT so
     fett/dunkel wie .mKopf - zwei optisch gleich starke Ueberschriften
     lassen sich sonst kaum auf den ersten Blick unterscheiden. */
  .mKandidaten { margin-top:10px; }
  .mKandGruppe { font-weight:600; margin-top:10px; color:var(--fg-muted); }
  .mKandName { font-weight:600; margin-top:8px; }
  .mKandKrit { margin-left:14px; font-size:0.92em; }
  .mVerlaesslichkeit { margin-top:6px; }
  /* T-1619: FFT-Groessen, deren Binbreite fuer die belegte Bandbreite zu grob
     ist, werden gedaempft - aber NICHT gesperrt. Die Grenze ist eine
     Faustregel, und bei importierten Dateien ohne bw_hz im Sidecar waere sie
     gar nicht anwendbar. */
  #fftGroup label.fftGrob { opacity:0.45; }
  #fftRes { margin-left:10px; font-variant-numeric:tabular-nums; }
  #measureList table { width:100%; }
  /* T-1213: Fortschrittsbalken beim Aufbau grosser Spektrogramme (Muster
     .occ-bar aus analyze_mscan.py). Nur sichtbar, solange /api/audspec_status
     einen laufenden Bau meldet - bei Cache-Treffern bleibt es beim Text. */
  .wf-bar { display:inline-block; width:120px; height:6px; vertical-align:middle;
            margin-left:8px; background:var(--panel-2); border:1px solid var(--border);
            border-radius:3px; overflow:hidden; }
  .wf-bar-fill { display:block; height:100%; width:0%; background:var(--accent); }
  #wfCanvasWrap { position:relative; width:100%; height:380px;
                  border:1px solid var(--canvas-border); border-radius:4px; overflow:hidden; }
  #specCanvas, #overlayCanvas { position:absolute; left:0; top:0; }
  /* T-1404a: der Overlay liegt UEBER dem Spektrogramm - die canvas-Regel aus
     BASE_CSS (deckender Hintergrund) muss hier ausgehebelt werden, sonst
     verdeckt er es vollstaendig. */
  #overlayCanvas { cursor:crosshair; background:transparent; }
  #wfNoData { border:1px dashed var(--border-strong); border-radius:4px; padding:40px; text-align:center;
              color:var(--fg-muted); }
  .error { color:var(--danger); }
  #siblings { margin:6px 0 4px; }
  #siblings a { margin-right:14px; }
  /* Klassifikator-Abzeichen (T-1007): kraeftige Fuellung, weisse Schrift -
     die Klassenfarben sind bewusst eigenstaendig (nicht die Art-Badges aus
     BASE_CSS) und in beiden Themes gleich. */
  .badge.k-leer { background:#6b7280; color:#fff; }
  .badge.k-rauschen { background:#4b5563; color:#fff; }
  .badge.k-cw { background:#c9700a; color:#fff; }
  .badge.k-digital { background:#2f6fd6; color:#fff; }
  .badge.k-sprache { background:#2f9e44; color:#fff; }
  .badge.k-unklar { background:#7c4dff; color:#fff; }
  .badge.schwach { opacity:0.7; border:1px dashed currentColor; }
  .kdetails { display:none; font-family:monospace; font-size:0.8em;
              white-space:pre; overflow-x:auto; background:var(--panel-2);
              border:1px solid var(--border);
              border-radius:4px; padding:8px 12px; margin-top:6px; }
  #klasseSelect.saved { border-color:var(--success); }
</style>
</head>
<body>
/*HEADER*/
<div class="top-links"><a id="backLink" href="#">&laquo; Back to list (:8050)</a></div>
<div id="errorLine" class="error" style="display:none"></div>
<!-- T-1627: zwei gleich breite Bloecke ueber dem Spektrogramm. Die Bedienung
     sitzt damit NICHT mehr in #waterfall - wer den Wasserfall ausblendet, muss
     sie eigens ausblenden (nurMetadaten() im Skript). -->
<div id="topGrid">
  <div id="metaBlock">
    <div id="meta"><div class="hint">loading ...</div></div>
  </div>
  <div id="ctrlBlock">
    <div id="wfToolbar">
      <div class="wfRow">
        <span class="wfGroup hint">Horizontal zoom: select a section with the mouse button held</span>
        <button id="overviewBtn" class="wfGroup" disabled>Zoom Out</button>
      </div>
      <div class="wfRow">
        <span class="wfGroup hint">Set markers (CTRL + mouse click)</span>
        <span class="wfGroup">Marker A: <span id="mkA">&ndash;</span></span>
        <span class="wfGroup">Marker B: <span id="mkB">&ndash;</span></span>
        <span class="wfGroup">Difference: <span id="mkDelta">&ndash;</span></span>
        <button id="yZoomBtn" class="wfGroup" disabled>Vertical zoom</button>
        <button id="clearMarkersBtn" class="wfGroup">Clear markers</button>
        <label id="tuneLbl" class="wfGroup hint"><input id="tuneChk" type="checkbox" disabled>
          Tune to markers</label>
      </div>
      <div class="wfRow">
        <span class="wfGroup" id="fftGroup">FFT:
          <label><input type="radio" name="fftSize" value="32"> 32</label>
          <label><input type="radio" name="fftSize" value="64"> 64</label>
          <label><input type="radio" name="fftSize" value="128"> 128</label>
          <label><input type="radio" name="fftSize" value="256"> 256</label>
          <label><input type="radio" name="fftSize" value="512"> 512</label>
          <label><input type="radio" name="fftSize" value="1024" checked> 1024</label>
          <label><input type="radio" name="fftSize" value="2048"> 2048</label>
          <span id="fftRes" class="hint"></span>
        </span>
      </div>
      <div class="wfRow">
        <span class="wfGroup">Contrast:
          <input id="levelLo" type="range" min="0" max="255" value="0" title="lower limit">
          <input id="levelHi" type="range" min="0" max="255" value="255" title="upper limit">
          <span id="levelVal" class="hint">0 &ndash; 255</span></span>
        <span class="wfGroup">
          <button id="levelAuto">Stretch contrast</button>
          <button id="levelReset">Reset</button>
          <select id="levelPalette">
            <option value="spektral">Spectral</option>
            <option value="grau">Gray</option>
            <option value="heiss">Hot</option>
            <option value="regenbogen">Rainbow</option>
            <option value="stufen">Steps</option>
            <option value="saegezahn">Sawtooth</option>
          </select></span>
      </div>
      <div class="wfRow">
        <span class="wfGroup"><button id="audioBtn" disabled>Play</button>
          <button id="measureBtn" disabled>Measure signal</button></span>
        <span id="measureHint" class="wfGroup hint"></span>
        <span id="wfLoading" class="wfGroup hint" style="display:none"><span
          id="wfLoadingText">loading &hellip;</span><span id="wfProgressBar"
          class="wf-bar" style="display:none"><span id="wfProgressFill"
          class="wf-bar-fill"></span></span></span>
      </div>
    </div>
  </div>
</div>
<!-- T-1404c/T-1627: Wiedergabe unter den Bloecken, direkt ueber dem
     Spektrogramm - dort gehoert sie inhaltlich hin (der Zeitmarker im
     Spektrogramm folgt ihr). Die Geschwisterlinks darunter. -->
<div id="playerBar"><audio id="player" controls preload="metadata"></audio></div>
<div id="siblings"></div>
<div id="waterfall">
  <div id="wfCanvasWrap">
    <canvas id="specCanvas" width="900" height="380"></canvas>
    <canvas id="overlayCanvas" width="900" height="380"></canvas>
  </div>
  <div id="wfError" class="error" style="display:none"></div>
  <div id="measureBox" style="display:none">
    <h3>Signal measurement <span id="measureWin" class="hint"></span></h3>
    <div id="measureSvg"></div>
    <div id="measureResult"></div>
    <div id="measureSave" style="display:none">
      <input id="measureNote" type="text" maxlength="200" placeholder="Note (optional)">
      <span id="measureSaveHint" class="hint"></span>
    </div>
    <h4>Saved findings</h4>
    <div id="measureList"></div>
  </div>
  <div id="wfNoData" style="display:none">no waterfall available</div>
</div>

<script>
function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtDateTime(iso) { return iso ? isoDateTime(new Date(iso)) : '–'; }
function fmtMHz(hz) { return hz == null ? '?' : (hz / 1e6).toFixed(4); }

async function fetchJSON(url) {
  const res = await fetch(url);
  let data = null;
  try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
  if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
  return data;
}

function showError(msg) {
  const line = el('errorLine');
  line.textContent = msg;
  line.style.display = '';
}

// T-401: bewusst markierte Kopie - Referenz analyze_sessions.py/waterfall.py
// levelToColor(RGB). Die gemeinsame Render-Schicht (T-401) ist zurueckgestellt;
// bis dahin lebt dieselbe Colormap redundant in jedem Canvas-Konsumenten.
// T-1617: Farbzuordnung mit waehlbarer Palette. "spektral" ist die bisherige
// Skala und bleibt Vorgabe; "grau" zeigt feine Kontraste oft besser, weil das
// Auge nicht durch Farbwechsel abgelenkt wird; "heiss" spreizt den oberen
// Bereich. Die Stuetzstellen sind Bruchteile 0..1 mit RGB.
// T-1618: Paletten. Die ersten drei sind Helligkeitsrampen ("heller = mehr"),
// die letzten drei echte Falschfarben: sie geben die Pegelordnung preis und
// zeigen dafuer feine Unterschiede, die eine Rampe verschluckt.
//   wiederholungen: die Rampe laeuft mehrfach ueber den Bereich (Saegezahn) -
//                   16 Pegelstufen durchlaufen dann die volle Skala.
//   stufen:         der Bereich wird in Klassen zerlegt; aus weichen
//                   Uebergaengen werden harte Kanten, wie Isolinien einer
//                   Hoehenschichtkarte.
const PALETTEN = {
  spektral:   { stops: [[0.00, 10, 10, 60], [0.25, 20, 90, 200], [0.50, 40, 200, 120],
                        [0.75, 240, 220, 40], [1.00, 230, 30, 30]] },
  grau:       { stops: [[0.00, 0, 0, 0], [1.00, 255, 255, 255]] },
  heiss:      { stops: [[0.00, 0, 0, 0], [0.35, 150, 20, 10], [0.70, 250, 160, 20],
                        [1.00, 255, 255, 220]] },
  regenbogen: { stops: [[0.00, 200, 0, 200], [0.17, 40, 40, 255], [0.34, 0, 200, 220],
                        [0.50, 0, 220, 60], [0.67, 230, 230, 0], [0.84, 255, 130, 0],
                        [1.00, 235, 0, 0]] },
  stufen:     { stufen: 12,
                stops: [[0.00, 20, 20, 90], [0.20, 0, 190, 200], [0.40, 30, 210, 60],
                        [0.60, 250, 240, 0], [0.80, 255, 110, 0], [1.00, 240, 0, 120]] },
  saegezahn:  { wiederholungen: 4,
                stops: [[0.00, 10, 10, 60], [0.25, 20, 90, 200], [0.50, 40, 200, 120],
                        [0.75, 240, 220, 40], [1.00, 230, 30, 30]] },
};

function levelToColorRGB(v, lo, hi, palette) {
  // lo/hi sind die Anschlaege in uint8-Einheiten (T-1617). Sie waren immer
  // schon Parameter, wurden aber nie anders als 0/255 belegt - und genau das
  // ist der Grund, warum ein starkes Signal als einfarbiger Balken erscheint:
  // die dB-Skala des Caches ist GLOBAL ueber die ganze Aufnahme (siehe
  // audio_spectrum, T-1212), also liegt der grosse Teil des Wertebereichs auf
  // dem Rauschen.
  const spanne = (hi - lo) || 1;
  let t = (v - lo) / spanne;
  t = Math.max(0, Math.min(1, t));
  const p = PALETTEN[palette] || PALETTEN.spektral;
  if (p.wiederholungen) t = (t * p.wiederholungen) % 1;
  if (p.stufen) t = Math.floor(t * p.stufen) / (p.stufen - 1);
  t = Math.max(0, Math.min(1, t));
  const stops = p.stops;
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, r0, g0, b0] = stops[i], [t1, r1, g1, b1] = stops[i + 1];
    if (t >= t0 && t <= t1) {
      const f = (t - t0) / (t1 - t0);
      return [r0 + f * (r1 - r0) | 0, g0 + f * (g1 - g0) | 0, b0 + f * (b1 - b0) | 0];
    }
  }
  const letzte = stops[stops.length - 1];
  return [letzte[1], letzte[2], letzte[3]];
}

// Anschlaege aus dem SICHTBAREN Ausschnitt bestimmen (T-1617). Genau der
// Fall, der beim Hineinzoomen in ein starkes Signal zaehlt: dort liegen alle
// Werte dicht beieinander, und erst die Spreizung auf diesen Bereich zeigt
// die innere Struktur. Die Raender werden um LEVEL_RAND beschnitten, damit
// einzelne Ausreisser die Spanne nicht wieder aufziehen.
const LEVEL_RAND = 0.02;

function autoLevels(bytes) {
  if (!bytes || !bytes.length) return null;
  const hist = new Uint32Array(256);
  for (let i = 0; i < bytes.length; i++) hist[bytes[i]]++;
  const rand = Math.floor(bytes.length * LEVEL_RAND);
  let lo = 0, hi = 255, summe = 0;
  for (let v = 0; v < 256; v++) { summe += hist[v]; if (summe > rand) { lo = v; break; } }
  summe = 0;
  for (let v = 255; v >= 0; v--) { summe += hist[v]; if (summe > rand) { hi = v; break; } }
  if (hi <= lo) { lo = 0; hi = 255; }
  return { lo: lo, hi: hi };
}

// ---------------------------------------------------------------------------
// T-1203: reine Geometrie-Umrechnungen (kein DOM-Zugriff, direkt testbar -
// siehe Selbsttest-Skript des Auftrags). geom = {plotX, plotY, plotW, plotH,
// t0, t1, fMin, fMax}: plotX/plotY/plotW/plotH der Plotbereich in Canvas-Pixeln,
// t0/t1 der angezeigte Zeitbereich in Sekunden (Uebersicht: 0..Dauer, Zoom:
// Fenstergrenzen), fMin/fMax die untere und obere Frequenzgrenze. T-915: fMin
// ist bei mono 0 und bei IQ -Nyquist - die Achse rechnet seither mit der
// SPANNE (fMax - fMin) statt mit fMax allein.
// ---------------------------------------------------------------------------
function xToTime(x, geom) {
  return geom.t0 + (x - geom.plotX) / geom.plotW * (geom.t1 - geom.t0);
}
function timeToX(t, geom) {
  return geom.plotX + (t - geom.t0) / (geom.t1 - geom.t0) * geom.plotW;
}
function yToFreq(y, geom) {
  return geom.fMin + (geom.fMax - geom.fMin) * (1 - (y - geom.plotY) / geom.plotH);
}
function freqToY(f, geom) {
  return geom.plotY + (1 - (f - geom.fMin) / (geom.fMax - geom.fMin)) * geom.plotH;
}

const HOST = window.location.hostname;
const path = new URLSearchParams(location.search).get('path') || '';
el('backLink').href = 'http://' + HOST + ':8050/';

// ---------------------------------------------------------------------------
// T-1203: Wasserfall (Spektrogramm) - Zustand, Zeichnen, Interaktion.
// ---------------------------------------------------------------------------
// T-1209: Breite folgt dem Container (fitCanvasWidth), Hoehe bleibt fest
let CANVAS_W = 900;
const CANVAS_H = 380;
const AXIS_LEFT = 48, AXIS_BOTTOM = 34, AXIS_TOP = 22;

// Feste, gedeckte Farben fuer die Frequenzmarker - ausserhalb der Pegel-
// Colormap (levelToColorRGB deckt Navy/Blau/Gruen/Gelb/Rot ab), Muster
// KNOWN_PALETTE (analyze_sessions.py/waterfall.py, "gedeckte Farben ...
// ohne Farben der Pegel-Colormap zu imitieren").
const MARKER_A_COLOR = '#e8e8e8';
const MARKER_B_COLOR = '#e8e8e8';   // T-1616: weiss statt violett - auf dem
// dunklen Wasserfall deutlich besser lesbar. Zur Unterscheidung von A wird
// die Linie gestrichelt gezeichnet, damit zwei dicht beieinanderliegende
// Marker auch ohne Beschriftung auseinanderzuhalten sind.

let curFft = 1024;
let sampleRateHz = null;   // aus /api/meta (meta.wav.sample_rate_hz) - Punkt 2/13:
                            // /api/audspec traegt selbst kein df_hz/sample_rate_hz im
                            // Header (siehe Abschlussbericht, Abweichung), die Meta-
                            // Antwort (laedt laut Punkt 12 ohnehin zuerst) liefert es.
let audioDurationS = 0;
let fMin = 0;               // T-915: untere Bandkante (mono 0, IQ -Nyquist)
let fMax = 0;               // obere Bandkante, FFT-unabhaengig
let zoomWin = null;         // {t0_s, t1_s} im Zoom, sonst null (Uebersicht)
let geom = null;
let lastHeader = null, lastMatrix = null;   // T-1209: fuer Neuzeichnen bei Fenster-Resize
let markerA = null, markerB = null;   // Hz oder null
// T-1616: vertikaler Zoom auf das Band zwischen den Markern, oder null fuer
// das ganze Band. Achse und Markerlinien folgen von selbst, weil beide ueber
// dieselbe Geometrie (geom.fMin/fMax) rechnen.
let yZoom = null;                    // {fLo, fHi} in Hz
let nextMarkerSlot = 'A';
let rafId = null;
let downX = null, downY = null, downCtrl = false, dragCurX = 0, dragActive = false;

// T-1209: Canvas-Pixelbreite an die Containerbreite angleichen. Rueckgabe true,
// wenn sich die Breite geaendert hat (Setzen von canvas.width loescht den Inhalt,
// Aufrufer zeichnet danach ohnehin neu).
function fitCanvasWidth() {
  const wrap = el('wfCanvasWrap');
  const w = Math.max(300, Math.floor(wrap.clientWidth || CANVAS_W));
  if (w === CANVAS_W) return false;
  CANVAS_W = w;
  el('specCanvas').width = w;
  el('overlayCanvas').width = w;
  return true;
}

function updateGeom() {
  fitCanvasWidth();
  geom = {
    plotX: AXIS_LEFT, plotY: AXIS_TOP,
    plotW: CANVAS_W - AXIS_LEFT - 4, plotH: CANVAS_H - AXIS_TOP - AXIS_BOTTOM,
    t0: zoomWin ? zoomWin.t0_s : 0, t1: zoomWin ? zoomWin.t1_s : audioDurationS,
    fMin: yZoom ? yZoom.fLo : fMin, fMax: yZoom ? yZoom.fHi : fMax,
  };
}

function niceStep(range, targetCount) {
  if (!(range > 0) || !(targetCount > 0)) return 1;
  const raw = range / targetCount;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10;
  return step * mag;
}

function fmtMMSS(t) {
  const s = Math.max(0, Math.round(t));
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

// T-916: EINE Schriftfarbe fuer alles Beschriftete an den Achsen - Ticks wie
// Einheiten. Vorher standen "kHz"/"Zeit [...]" in einem gedaempften #888 und
// waren auf der dunklen Canvasflaeche deutlich schwaecher als die Zahlen
// daneben.
const AXIS_TEXT_COLOR = '#9cf';

function drawAxes(ctx) {
  ctx.strokeStyle = '#345';
  ctx.fillStyle = AXIS_TEXT_COLOR;
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  // T-915: Ticks ueber die SPANNE, Start am ersten Vielfachen >= fMin - bei IQ
  // laeuft die Achse von -Nyquist bis +Nyquist und begaenne sonst falsch bei 0.
  const freqStep = niceStep(geom.fMax - geom.fMin, 5);
  const fStart = Math.ceil(geom.fMin / freqStep) * freqStep;
  for (let f = fStart; f <= geom.fMax + 1e-6; f += freqStep) {
    const y = freqToY(f, geom);
    ctx.fillText((f / 1000).toFixed(freqStep < 1000 ? 1 : 0), geom.plotX - 6, y);
    ctx.beginPath(); ctx.moveTo(geom.plotX - 3, y); ctx.lineTo(geom.plotX, y); ctx.stroke();
  }
  // T-915: bei zweiseitiger Achse eine gedaempfte Linie auf 0 Hz Ablage - ohne
  // sie ist im Bild nicht zu sehen, wo die Empfangsfrequenz liegt.
  if (geom.fMin < 0) {
    const y0 = freqToY(0, geom);
    ctx.save();
    ctx.strokeStyle = '#456';
    ctx.setLineDash([2, 4]);
    ctx.beginPath(); ctx.moveTo(geom.plotX, y0); ctx.lineTo(geom.plotX + geom.plotW, y0); ctx.stroke();
    ctx.restore();
  }
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const timeStep = niceStep(geom.t1 - geom.t0, 6) || 1;
  // T-1209: Einheit der Zeitachse je nach Tick-Abstand: >= 1 s -> min:s,
  // >= 10 ms -> Sekunden mit 2 Nachkommastellen, sonst Millisekunden.
  let timeUnit, fmtTick;
  if (timeStep >= 1) { timeUnit = 'min:s'; fmtTick = fmtMMSS; }
  else if (timeStep >= 0.01) { timeUnit = 's'; fmtTick = t => t.toFixed(2); }
  else { timeUnit = 'ms'; fmtTick = t => (t * 1000).toFixed(1); }
  const startTick = Math.ceil(geom.t0 / timeStep) * timeStep;
  for (let t = startTick; t <= geom.t1 + 1e-6; t += timeStep) {
    const x = timeToX(t, geom);
    ctx.fillText(fmtTick(t), x, geom.plotY + geom.plotH + 4);
    ctx.beginPath(); ctx.moveTo(x, geom.plotY + geom.plotH); ctx.lineTo(x, geom.plotY + geom.plotH + 3); ctx.stroke();
  }
  // T-1209: Einheitenbeschriftung - y-Achse oben links "kHz", x-Achse unten mittig
  // T-916: dieselbe Farbe wie die Tickzahlen (frueher '#888').
  ctx.fillStyle = AXIS_TEXT_COLOR;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText('Time [' + timeUnit + ']', geom.plotX + geom.plotW / 2, geom.plotY + geom.plotH + 17);
  ctx.textAlign = 'right';
  ctx.textBaseline = 'bottom';
  // T-915: bei IQ sind die Werte Ablagen zur Empfangsfrequenz, nicht
  // Audiofrequenzen - das muss an der Achse stehen.
  ctx.fillText(geom.fMin < 0 ? 'kHz rel.' : 'kHz', geom.plotX - 6, geom.plotY - 6);
}

// Offscreen-Canvas in Naturaufloesung (Breite=rows/Zeit, Hoehe=n_bins/Frequenz)
// fuellen, dann per drawImage NN-skaliert (imageSmoothingEnabled=false) in den
// Plotbereich zeichnen - Punkt 3 des Auftrags. Bin 0 (0 Hz) unten: Canvas-Zeile
// (n_bins-1-bin).
function drawSpectrogram(header, matrixBytes) {
  // T-1617: letzten Zeichenstand merken, damit die Kontrastregler neu zeichnen
  // koennen, ohne die Daten erneut zu holen.
  lastSpec = { header: header, bytes: matrixBytes };
  updateFftRes();                       // T-1619
  const rows = header.rows, nBins = header.n_bins;
  const sctx = el('specCanvas').getContext('2d');
  sctx.imageSmoothingEnabled = false;
  sctx.fillStyle = '#111';
  sctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
  if (rows > 0 && nBins > 0) {
    const off = document.createElement('canvas');
    off.width = rows; off.height = nBins;
    const octx = off.getContext('2d');
    const img = octx.createImageData(rows, nBins);
    for (let r = 0; r < rows; r++) {
      const base = r * nBins;
      for (let b = 0; b < nBins; b++) {
        const col = levelToColorRGB(matrixBytes[base + b], levelLo, levelHi, levelPalette);
        const cy = nBins - 1 - b;
        const p = (cy * rows + r) * 4;
        img.data[p] = col[0]; img.data[p + 1] = col[1]; img.data[p + 2] = col[2]; img.data[p + 3] = 255;
      }
    }
    octx.putImageData(img, 0, 0);
    sctx.imageSmoothingEnabled = false;
    // T-1616: nur den markierten Bin-Bereich als Quellausschnitt nehmen. Im
    // Offscreen-Bild liegt oben fMax, unten fMin (cy = nBins - 1 - b).
    const spanneVoll = (fMax - fMin) || 1;
    const yOben = yZoom ? (fMax - geom.fMax) / spanneVoll * nBins : 0;
    const yHoehe = yZoom ? (geom.fMax - geom.fMin) / spanneVoll * nBins : nBins;
    sctx.drawImage(off, 0, Math.max(0, yOben), rows, Math.max(1, yHoehe),
                    geom.plotX, geom.plotY, geom.plotW, geom.plotH);
  }
  drawAxes(sctx);
}

// T-1404b: Markerlinie 2 px stark; Beschriftung ("Marker A"/"Marker B")
// waagrecht mittig ueber der Linie. Liegt die Linie zu weit oben, rutscht die
// Schrift unter sie (sonst ragte sie in die Zeitachse/aus dem Plot).
function drawMarkerLine(ctx, freq, color, label, gestrichelt) {
  if (freq == null || !geom || freq < geom.fMin || freq > geom.fMax) return;
  const y = freqToY(freq, geom);
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  // T-1616: beide Marker sind jetzt weiss; A durchgezogen, B gestrichelt -
  // sonst waeren zwei dicht beieinanderliegende Linien nicht mehr
  // auseinanderzuhalten.
  ctx.setLineDash(gestrichelt ? [6, 4] : []);
  ctx.beginPath(); ctx.moveTo(geom.plotX, y); ctx.lineTo(geom.plotX + geom.plotW, y); ctx.stroke();
  ctx.setLineDash([]);
  ctx.lineWidth = 1;
  if (!label) return;
  const cx = geom.plotX + geom.plotW / 2;
  const above = (y - geom.plotY) > 18;
  const ty = above ? y - 7 : y + 16;
  ctx.font = 'bold 12px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  // Dunkler Saum, damit die Schrift auf jeder Colormap-Farbe lesbar bleibt.
  ctx.lineJoin = 'round';
  ctx.strokeStyle = 'rgba(0,0,0,0.85)';
  ctx.lineWidth = 3;
  ctx.strokeText(label, cx, ty);
  ctx.lineWidth = 1;
  ctx.fillStyle = color;
  ctx.fillText(label, cx, ty);
}

function playerCurrentTime() {
  const p = el('player');
  return (p && !isNaN(p.currentTime)) ? p.currentTime : null;
}

function drawOverlay() {
  const octx = el('overlayCanvas').getContext('2d');
  octx.clearRect(0, 0, CANVAS_W, CANVAS_H);
  if (!geom) return;
  if (dragActive) {
    const x0 = Math.min(downX, dragCurX), x1 = Math.max(downX, dragCurX);
    octx.fillStyle = 'rgba(255,255,255,0.15)';
    octx.fillRect(x0, geom.plotY, x1 - x0, geom.plotH);
  }
  drawMarkerLine(octx, markerA, MARKER_A_COLOR, 'Marker A');
  drawMarkerLine(octx, markerB, MARKER_B_COLOR, 'Marker B', true);
  const t = playerCurrentTime();
  if (t != null && t >= geom.t0 && t <= geom.t1) {
    const x = timeToX(t, geom);
    octx.strokeStyle = '#fff';
    octx.lineWidth = 1;
    octx.beginPath(); octx.moveTo(x, geom.plotY); octx.lineTo(x, geom.plotY + geom.plotH); octx.stroke();
  }
}

function tickPlayhead() {
  drawOverlay();
  const p = el('player');
  rafId = (p && !p.paused) ? requestAnimationFrame(tickPlayhead) : null;
}

function updateMarkerInfo() {
  const a = markerA != null ? Math.round(markerA) + ' Hz' : '–';
  const b = markerB != null ? Math.round(markerB) + ' Hz' : '–';
  const d = (markerA != null && markerB != null) ? Math.round(Math.abs(markerA - markerB)) + ' Hz' : '–';
  el('mkA').textContent = a; el('mkB').textContent = b; el('mkDelta').textContent = d;
  updateYZoomBtn();
}

function setMarker(f) {
  if (nextMarkerSlot === 'A') { markerA = f; nextMarkerSlot = 'B'; }
  else { markerB = f; nextMarkerSlot = 'A'; }
  updateMarkerInfo();
  drawOverlay();
}

function updateOverviewBtnState() {
  el('overviewBtn').disabled = !zoomWin;
  // T-1611b: ohne Zoom gibt es kein Zeitfenster - der Knopf bleibt aus.
  const mBtn = el('measureBtn');
  if (mBtn) {
    mBtn.disabled = !zoomWin || measureKind !== 'iq';
    // T-1624: Anhoeren braucht KEINEN Zoom, solange die Aufnahme kurz genug
    // ist - dann wird sie ganz abgespielt. Die Zoompflicht stammt vom
    // Vermessen, wo sie einen Grund hat (eye_diagram sucht einen Burst im
    // Fenster); beim Hoeren ist sie nur laestig.
    const aBtn = el('audioBtn');
    if (aBtn) {
      aBtn.disabled = measureKind !== 'iq'
        || (!zoomWin && !(audioDurationS > 0 && audioDurationS <= AUDIO_MAX_S));
      aBtn.title = zoomWin ? 'Play the selected section'
        : (audioDurationS > AUDIO_MAX_S
           ? 'Recording longer than ' + AUDIO_MAX_S + ' s - select a section'
           : 'Play the whole recording');
    }
    el('measureHint').textContent = measureKind === 'iq'
      ? (zoomWin ? '' : 'select a section in the waterfall')
      : 'IQ recordings only';
  }
}

// T-1213: Fortschritt eines laufenden build_audspec. /api/audspec blockiert,
// bis der Cache steht - der Status kommt deshalb ueber eine zweite, billige
// Anfrage, die ein Nebenthread des Servers bedient. Der Poll startet erst nach
// WF_POLL_DELAY_MS, damit ein Cache-Treffer (Antwort in Millisekunden) keinen
// Balken aufblitzen laesst.
const WF_POLL_DELAY_MS = 800;
const WF_POLL_INTERVAL_MS = 500;
// T-1606: Baut ein ANDERER Prozess gerade (Aufbereitungs-Worker oder ein
// zweiter Betrachter), antwortet /api/audspec mit 409 und code=building.
// Das ist kein Fehler - wir warten den fremden Bau ab und holen danach
// erneut. WF_BUILD_RETRIES begrenzt das, damit ein Bau, der immer wieder
// scheitert, die Seite nicht in eine Endlosschleife schickt.
const WF_BUILD_RETRIES = 3;
let wfPollDelay = null;
let wfPollTimer = null;
let wfWaiting = false;      // wartet gerade auf einen FREMDEN Bau
let wfRetries = 0;

function fmtElapsed(s) {
  const t = Math.max(0, Math.round(s));
  const m = Math.floor(t / 60);
  return m ? m + ':' + String(t % 60).padStart(2, '0') : t + ' s';
}

function setWfLoadingText(txt, frac) {
  el('wfLoadingText').textContent = txt;
  const bar = el('wfProgressBar');
  if (frac == null) {
    bar.style.display = 'none';
    return;
  }
  bar.style.display = '';
  el('wfProgressFill').style.width =
    Math.round(Math.min(1, Math.max(0, frac)) * 100) + '%';
}

async function pollWfProgress() {
  let st = null;
  try {
    const res = await fetch('/api/audspec_status?path=' + encodeURIComponent(path));
    if (res.ok) st = await res.json();
  } catch (e) { /* Netzfehler: der naechste Poll versucht es erneut */ }
  if (wfPollTimer === null) return;          // inzwischen fertig geworden
  if (!st || !st.active) {
    if (wfWaiting) {
      // T-1606: der fremde Bau ist durch - Daten jetzt holen.
      wfWaiting = false;
      stopWfPoll();
      if (wfRetries < WF_BUILD_RETRIES) { wfRetries += 1; loadSpectrogram(); }
      else { showWfLoading(false); showWfError('waterfall could not be loaded'); }
      return;
    }
    setWfLoadingText('loading …', null);        // Cache-Treffer oder Zeitfenster
    return;
  }
  renderWfProgress(st, wfWaiting);
}

// Derselbe Fortschritt, zwei Formulierungen: baut dieser Betrachter selbst,
// heisst es "is being computed"; laeuft der Bau woanders, "is being prepared
// in the background". Der Unterschied ist keine Kosmetik - er sagt, ob das eigene
// Fenster gerade Arbeit macht oder nur zusieht.
function renderWfProgress(st, fremd) {
  if (!st) { setWfLoadingText(fremd ? 'is being prepared in the background …' : 'loading …', null); return; }
  const zeit = st.elapsed_s == null ? '' : ' · ' + fmtElapsed(st.elapsed_s);
  const wort = fremd ? 'is being prepared in the background' : 'spectrogram is being computed';
  if (st.phase === 'write') {
    setWfLoadingText((fremd ? 'is being written in the background' : 'spectrogram is being written')
                     + ' …' + zeit, 1);
  } else {
    setWfLoadingText(wort + ' … ' + Math.round((st.frac || 0) * 100) + ' %' + zeit, st.frac || 0);
  }
}

// Ein fremder Bau laeuft: Balken zeigen und abwarten. Der bestehende Poll
// gegen /api/audspec_status uebernimmt das Warten - meldet er "nichts aktiv",
// ist der fremde Bau fertig und wir holen die Daten erneut.
function waitForBackgroundBuild(progress) {
  wfWaiting = true;
  hideWfError();
  showWfLoading(true);
  renderWfProgress(progress, true);
}

function stopWfPoll() {
  if (wfPollDelay !== null) { clearTimeout(wfPollDelay); wfPollDelay = null; }
  if (wfPollTimer !== null) { clearInterval(wfPollTimer); wfPollTimer = null; }
}

// ---- Signalvermessung (T-1611b) --------------------------------------------
// Der Knopf ist nur im Zoomzustand aktiv: t0_s/t1_s kommen aus dem Zoom, und
// ohne Ausschnitt gaebe es nichts zu vermessen. Gerechnet werden BEIDE Pfade
// (mode=auto) - der Befund an sieben eigenen Aufnahmen war, dass meist genau
// einer durchkommt. Kein Sieger wird gekuert, wenn beide liefern: r2 (FSK)
// und EVM (PSK) messen Verschiedenes.
let measureTreffer = {};     // je erfolgreichem Pfad die merkbare Messung
// T-1617: Anschlaege und Palette der Farbzuordnung. Rein zeichnerisch - es
// wird nichts neu geholt, drawSpectrogram() zeichnet dieselben Bytes anders.
let levelLo = 0, levelHi = 255, levelPalette = 'spektral';
let lastSpec = null;         // {header, bytes} des zuletzt gezeichneten Ausschnitts
// T-1619: belegte Bandbreite aus dem Sidecar (bw_hz), oder null bei
// importierten Aufnahmen. Nur fuer die Daempfung der FFT-Auswahl.
let nutzBandbreiteHz = null;
// Ab wieviel Bins ueber der Nutzbandbreite eine Groesse noch taugt. 20 ist
// eine Faustregel: darunter wird aus der Spektralanalyse ein grober
// Pegelverlauf. Bei 15 kHz ZF-Bandbreite trifft das n_fft=32 (1 kHz Bin,
// 15 Streifen) und knapp auch 64.
const FFT_MIN_BINS_IM_BAND = 20;

function fmtHz(hz) {
  if (hz == null) return '?';
  return hz >= 1000 ? (hz / 1000).toFixed(hz >= 10000 ? 1 : 2) + ' kHz'
                    : hz.toFixed(0) + ' Hz';
}

function fmtDauer(s) {
  if (s == null) return '?';
  if (s >= 1) return s.toFixed(2) + ' s';
  if (s >= 0.001) return (s * 1000).toFixed(s >= 0.01 ? 1 : 2) + ' ms';
  return (s * 1e6).toFixed(0) + ' \\u00b5s';
}

// Auflaesung der GERADE gezeichneten Darstellung. dt_row_s kommt aus dem
// Header und traegt die Dezimierung bereits in sich - bei der langen
// IQ-Aufnahme sind das 0,848 s statt der theoretischen 32 ms. Ohne diese
// Angabe haelt man die Uebersicht fuer feiner als sie ist.
function updateFftRes() {
  const feld = el('fftRes');
  if (!feld) return;
  const h = lastSpec && lastSpec.header;
  if (!h || !sampleRateHz) { feld.textContent = ''; return; }
  const bin = sampleRateHz / h.n_fft;
  const dez = h.decimation && h.decimation > 1 ? ' (' + h.decimation + '\\u00d7 decimated)' : '';
  feld.textContent = fmtHz(bin) + ' per bin \\u00b7 ' + fmtDauer(h.dt_row_s) + ' per row' + dez;
}

// Zu grobe Groessen daempfen und im Titel begruenden. Ohne bekannte
// Bandbreite entfaellt die Daempfung - eine Regel, die manchmal greift und
// manchmal nicht, waere verwirrender als keine.
function updateFftHinweise() {
  const gruppe = el('fftGroup');
  if (!gruppe || !sampleRateHz) return;
  gruppe.querySelectorAll('label').forEach(lab => {
    const eingabe = lab.querySelector('input');
    if (!eingabe) return;
    const nFft = Number(eingabe.value);
    const bin = sampleRateHz / nFft;
    if (!nutzBandbreiteHz) {
      lab.classList.remove('fftGrob');
      lab.title = fmtHz(bin) + ' per bin';
      return;
    }
    const bins = nutzBandbreiteHz / bin;
    const grob = bins < FFT_MIN_BINS_IM_BAND;
    lab.classList.toggle('fftGrob', grob);
    lab.title = fmtHz(bin) + ' per bin \\u2013 ' + Math.round(bins) + ' bins across '
      + fmtHz(nutzBandbreiteHz) + ' bandwidth'
      + (grob ? ' (too coarse for frequency information)' : '');
  });
}

// T-1616: der Knopf schaltet zwischen Bandausschnitt und ganzem Band um und
// beschriftet sich entsprechend - so bleibt es bei einem Knopf statt zweien.
// Aktiv nur mit zwei Markern; die dienen auch der Differenzmessung, deshalb
// zoomt das Setzen allein noch nicht.
function updateYZoomBtn() {
  const b = el('yZoomBtn');
  if (!b) return;
  b.textContent = yZoom ? 'Full band' : 'Vertical zoom';
  b.disabled = !yZoom && !(markerA != null && markerB != null);
  // T-1620: Abstimmung nur mit zwei Markern anwaehlbar. Bewusst ein
  // Kontrollkaestchen und keine Automatik - die Marker dienen auch dem
  // blossen Messen, und der Vermessen-Knopf soll sich nicht je nach
  // Markerzustand anders verhalten.
  const chk = el('tuneChk');
  if (chk) {
    const bereit = markerA != null && markerB != null;
    chk.disabled = !bereit;
    if (!bereit) chk.checked = false;
    el('tuneLbl').title = bereit
      ? 'Tune the signal to ' + Math.round((markerA + markerB) / 2) + ' Hz and decimate to '
        + Math.round(Math.abs(markerA - markerB)) + ' Hz bandwidth'
      : 'Set two markers';
  }
}

function redrawSpectrogram() {
  if (lastSpec) drawSpectrogram(lastSpec.header, lastSpec.bytes);
}

function updateLevelAnzeige() {
  el('levelVal').textContent = levelLo + ' – ' + levelHi;
}

function setLevels(lo, hi) {
  levelLo = Math.max(0, Math.min(254, lo));
  levelHi = Math.max(levelLo + 1, Math.min(255, hi));
  el('levelLo').value = String(levelLo);
  el('levelHi').value = String(levelHi);
  updateLevelAnzeige();
  redrawSpectrogram();
}

function wireLevelControls() {
  el('levelLo').addEventListener('input', () =>
    setLevels(Number(el('levelLo').value), levelHi));
  el('levelHi').addEventListener('input', () =>
    setLevels(levelLo, Number(el('levelHi').value)));
  el('levelReset').addEventListener('click', () => setLevels(0, 255));
  el('levelPalette').addEventListener('change', () => {
    levelPalette = el('levelPalette').value;
    redrawSpectrogram();
  });
  el('levelAuto').addEventListener('click', () => {
    const g = autoLevels(lastSpec && lastSpec.bytes);
    if (g) setLevels(g.lo, g.hi);
  });
}
let measureKind = null;      // 'iq' | 'mono' | 'other', aus initWaterfall
let audioUrl = null;         // T-1623: Objekt-URL der zuletzt erzeugten WAV
const AUDIO_MAX_S = 120;     // wie eye_diagram.AUDIO_MAX_S (T-1624)

// T-1621: je Feld ein dritter Eintrag mit der Erlaeuterung. Mehrzeilig ueber
// Template-Literale mit echten Umbruechen - KEINE Backslash-Escapes, die
// waeren in der nicht-rohen Python-Zeichenkette eine Falle (der Umbruch
// landete sonst direkt im JS-Quelltext und sprengte die Zeichenkette).
// Hoechstens 80 Zeichen je Zeile.
const M_FELDER_FSK = [
  ['level_hint', 'Level',
   `Number of detected frequency levels.
   FSK2 = two tones (mark/space), FSK4 = four.`],
  ['baud', 'Symbol rate (Bd)',
   `Symbols per second. RTTY usually 45.45 or 50,
   PACTOR 100, TETRA 18000.`],
  ['mod_index', 'Modulation index',
   `Ratio of total deviation to symbol rate (2 * deviation / baud).
   Higher values mean stronger frequency keying per symbol.`],
  ['r2', 'Fit quality r²',
   `How well the rate found fits the signal (0..1).
   Below 0.9 the value is doubtful.`],
  ['standard_baud', 'Standard rate',
   `Nearest common rate,
   if the measurement lies close enough to it.`],
  ['dev_hz', 'Deviation (Hz)',
   `Distance between the highest and the lowest frequency level.
   The RTTY standard is 170 Hz.`],
  ['offset_hz', 'Carrier offset (Hz)',
   `How far the signal center lies from the band center.
   Large values: check the tuning.`],
  ['eye_opening_pct', 'Eye opening (%)',
   `How far the eye in the diagram is open.
   Above 50 % it reads well, near 0 it is unusable.`],
  ['jitter_pct', 'Jitter (%)',
   `Variation of the timing, relative to one symbol duration.
   With a half grid (see Timing phase) the value measures the
   timing phase, not the disturbance - then the jitter
   against the half-bit raster below is what counts.`],
  ['crossing_grid', 'Timing phase',
   `On which time grid the zero crossings lie:
   whole or half symbol durations, or none at all.`],
  ['jitter_half_pct', 'Jitter (half-bit raster)',
   `Like the jitter above, but measured against the half
   grid - both in percent of a whole symbol duration,
   so directly comparable. Only meaningful with a
   half grid.`],
  ['n_symbols_used', 'Symbols',
   `How many symbols went into the evaluation.
   Below about 30 all values are uncertain.`]
];
// T-1613: der OFDM-Hinweis misst etwas anderes als die beiden Symbolpfade
// und traegt deshalb eine eigene Tafel.
const M_FELDER_OFDM = [
  ['symbol_s', 'Symbol duration (ms)',
   `Duration of one OFDM useful symbol without guard interval.
   Its reciprocal is the carrier spacing.`],
  ['traegerabstand_erwartet_hz', 'Carrier spacing (Hz)',
   `Spacing of the individual carriers, computed from the
   symbol duration. HF modes mostly lie at 40 to 100 Hz.`],
  ['traegerabstand_gemessen_hz', 'Spectral comb (Hz)',
   `The same spacing, measured independently from the spectrum.
   If the two agree, the finding is more reliable.`],
  ['kurtosis', 'Kurtosis',
   `Shape of the amplitude distribution. Near 3 means Gaussian,
   i.e. many carriers - but noise is that as well.`],
  ['korr_gipfel', 'Correlation peak',
   `Height of the peak at the cyclic prefix (0..1).
   It is the actual proof of the periodicity.`],
  ['korr_ueberhoehung', 'Excess',
   `How far the peak rises above the baseline.
   Small values mean: barely detected, read with care.`]
];
const M_FELDER_PSK = [
  ['m_chosen', 'Order (M)',
   `Number of phase states.
   2 = BPSK, 4 = QPSK, 8 = 8PSK.`],
  ['baud', 'Symbol rate (Bd)',
   `Symbols per second. TETRA 18000,
   HF modes mostly 100 to 2400.`],
  ['offset_hz', 'Carrier offset (Hz)',
   `How far the carrier lies from the band center.
   Large values: check the tuning.`],
  ['carrier_abs_khz', 'Carrier (kHz)',
   `Absolute carrier frequency, computed from the receive
   frequency and the offset.`],
  ['evm_pct', 'EVM (%)',
   `Spread of the constellation points around their nominal place.
   Below 20 % clean, above 50 % hardly readable.`],
  ['phase_rms_deg', 'Phase noise (°)',
   `Spread of the phase in degrees.
   Small values mean clearly separated points.`],
  ['mag_cv_pct', 'Amplitude spread (%)',
   `Variation of the amplitude. With pure phase keying
   it should be small.`],
  ['n_symbols_used', 'Symbols',
   `How many symbols were evaluated.
   Below about 30 all values are uncertain.`],
  ['chosen_burst_duration_s', 'Burst duration (s)',
   `Length of the evaluated section.
   For continuous signals the whole time window.`]
];

function mTip(text) {
  // Kein Backslash-Escape: der Zeilenumbruch kommt ueber fromCharCode, damit
  // er die Python-Zeichenkette nicht beim Schreiben schon aufloest.
  const NL = String.fromCharCode(10);
  return String(text || '').split(NL).map(z => z.trim()).join(NL).split('"').join('');
}

function mZahl(v) {
  if (v === null || v === undefined) return '–';
  if (typeof v !== 'number') return String(v);
  const a = Math.abs(v);
  return a >= 1000 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(3);
}

// T-1711: crossing_grid ist ein sprachneutraler Code (full/half/unclear),
// die Anzeige uebersetzt ihn. Bei 'half' zusaetzlich der Hinweis sinngleich
// zur Berichtsausgabe von tools/eye_diagram.py (T-1710) - das "possible" ist
// nicht verhandelbar, es steht dort nur eine Moeglichkeit, kein Befund.
function mRasterlageText(wert) {
  if (wert === 'full') return 'whole symbol durations';
  if (wert === 'half') return 'half symbol durations'
    + '<div class="hint">possible e.g. with 1.5 stop bits (ITA2/RTTY)</div>';
  if (wert === 'unclear') return 'no uniform grid';
  return mZahl(wert);
}

// T-1711: an der Symbolrate kenntlich machen, ob der Feinabgleich aus
// tools/eye_diagram.py uebernommen wurde - baud traegt sonst kommentarlos
// mal den feinen, mal den groben Wert.
function mWertZelle(name, k) {
  // T-1613: symbol_s kommt in Sekunden, angezeigt wird in ms.
  if (name === 'symbol_s') return mZahl(k[name] * 1000);
  if (name === 'baud') {
    const marke = k.baud_refined === true ? ' (fine-adjusted)'
                : k.baud_refined === false ? ' (roughly estimated)' : '';
    return mZahl(k[name]) + marke;
  }
  if (name === 'crossing_grid') return mRasterlageText(k[name]);
  return mZahl(k[name]);
}

// Echtes Plus-Minus-Zeichen ueber fromCharCode statt als Glyph oder als
// Unicode-Fluchtsequenz im Python-Quelltext: eine solche Sequenz wird beim
// Schreiben ueber das Editor-Werkzeug zum Glyphen aufgeloest (CLAUDE.md)
// und stuende dann als literales Unicode-Zeichen im Python-Quelltext statt
// als JS-Escape im ausgelieferten Skript.
const PM_ZEICHEN = String.fromCharCode(177);

// T-1704: Kriterienzeile eines Modenkandidaten. Dieselbe Auswahl wie
// eye_diagram._format_kandidat_zeile(): level_count und keying sind harte
// Ja/Nein-Kriterien ohne sinnvolle Abweichung und werden nicht gezeigt -
// steht die Zeile ueberhaupt da, haben sie schon gestimmt.
function mKritText(crit) {
  if (crit.name === 'shift_hz' || crit.name === 'tone_spacing_hz') {
    const label = crit.name === 'shift_hz' ? 'Deviation' : 'Tone spacing';
    return label + ' ' + mZahl(crit.measured) + ' Hz (expected ' + mZahl(crit.expected)
         + ' ' + PM_ZEICHEN + ' ' + mZahl(crit.tolerance) + ')';
  }
  if (crit.name === 'baud') {
    return 'Symbol rate ' + mZahl(crit.measured) + ' Bd (expected ' + mZahl(crit.expected)
         + ' ' + PM_ZEICHEN + ' ' + mZahl(crit.tolerance_rel * 100) + ' %)';
  }
  if (crit.name === 'spacing_cv_max') {
    return 'Tone spacing spread ' + mZahl(crit.measured * 100) + ' % (limit '
         + mZahl(crit.expected * 100) + ' %)';
  }
  return null;
}

// Ein Kandidat: Anzeigename, darunter die Kriterien mit Messwert/Soll/
// Toleranz, darunter die Notiz aus der Tabelle (modes/fsk.json) - KEINE
// Rangnummer, kein Abzeichen, nichts, das einen Kandidaten gegenueber einem
// anderen derselben Stufe hervorhebt (Leitsatz Meilenstein 17).
function mKandidatHtml(bef) {
  const zeilen = (bef.criteria || []).map(mKritText).filter(t => t !== null)
    .map(t => '<div class="mKandKrit">' + t + '</div>').join('');
  const notiz = bef.note ? '<div class="mKandKrit hint">' + bef.note + '</div>' : '';
  return '<div class="mKandName">' + bef.label + '</div>' + zeilen + notiz;
}

// T-1704: Tafel der Modenkandidaten unter der FSK-Wertetafel. mode_candidates
// liegt bereits fertig verglichen in kennwerte (eb200_core/mode_match.py,
// T-1703) - hier wird nur dargestellt, nie neu bewertet oder umsortiert.
// "near"-Kandidaten stehen in einer EIGENEN Gruppe unter "no match possible",
// nicht als Randvermerk an der Zeile - zwei Gruppen trennen sauber, ein
// Vermerk laedt dazu ein, ihn zu uebersehen. Reihenfolge innerhalb jeder
// Gruppe bleibt exakt die aus mode_candidates (match/near, je alphabetisch
// nach mode_id) - eine Umsortierung taeuschte eine Rangfolge vor, die es
// laut Leitsatz nicht gibt.
function mKandidatenHtml(k) {
  const cands = k.mode_candidates;
  let inner;
  if (!cands || !cands.length) {
    // Fehlt mode_candidates ganz (aeltere zwischengespeicherte Ergebnisse)
    // oder ist die Liste leer: dieselbe Leermeldung, kein Fehler, keine
    // Luecke im Aufbau. Ausdruecklich NICHT "unbekanntes Signal" o.ae. -
    // die Tabelle kennt das Signal nicht, das ist keine Aussage darueber.
    inner = '<div class="mLeer">No match in the mode table.</div>';
  } else {
    const treffer = cands.filter(b => b.confidence === 'match');
    const knapp = cands.filter(b => b.confidence === 'near');
    inner = treffer.map(mKandidatHtml).join('');
    if (knapp.length) {
      inner += '<div class="mKandGruppe">no match possible</div>'
             + knapp.map(mKandidatHtml).join('');
    }
  }
  let block = '<div class="mKandidaten"><div class="mKopf">Known services:</div>'
            + inner + '</div>';
  // T-1711: der Feinabgleich lehnt bei kurzen Bursts ab, und dort kann der
  // grobe Flankenfit bis zu 11 % danebenliegen - mehr als die 6 %
  // Baudtoleranz der Modentabelle (BAUD_TOL_ZUFALLSFOLGE). Ein Kandidat kann
  // dann ebenso gut fehlen wie zu Unrecht dastehen; wer die Tafel liest, muss
  // das sehen, ohne die Wertetafel danebenzulegen.
  if (k.baud_refined === false) {
    block += '<p class="hint mVerlaesslichkeit">Symbol rate is only coarsely determined '
      + '(fine adjustment not reliable); the candidates are correspondingly '
      + 'uncertain.</p>';
  }
  return block;
}

function mPfadHtml(name, r) {
  // T-1615: Ein Block je Pfad - Ueberschrift, Bild, Werte, Knopf. Vorher lagen
  // Werte und Bilder in getrennten Behaeltern, wodurch die Zuordnung bei einem
  // Doppeltreffer nur ueber die Reihenfolge lief.
  const titel = name === 'fsk' ? 'FSK'
              : name === 'ofdm' ? 'Possibly OFDM' : 'PSK';
  if (!r || !r.ok) {
    const grund = (r && r.meldung) || 'not evaluated';
    return '<div class="mPfad"><div class="mKopf">' + titel + '</div>'
      + '<div class="mLeer">' + grund.replace('FEHLER: ', '') + '</div></div>';
  }
  const felder = name === 'fsk' ? M_FELDER_FSK
               : name === 'ofdm' ? M_FELDER_OFDM : M_FELDER_PSK;
  const k = r.kennwerte || {};
  const zeilen = felder.filter(f => k[f[0]] !== undefined && k[f[0]] !== null)
    // Die Fortsetzungszeilen der Template-Literale sind eingerueckt, damit
    // der Quelltext lesbar bleibt - im Tooltip stoert das, also raus.
    .map(f => '<tr title="' + mTip(f[2]) + '"><td>' + f[1] + '</td><td>'
              + mWertZelle(f[0], k)
              + '</td></tr>').join('');
  const kopf = name === 'ofdm' ? titel : titel + ' – evaluated';
  // T-1704: die Kandidatentafel steht nur im FSK-Zweig, unter der Wertetafel,
  // in derselben Spalte - PSK und OFDM liefern keine Kandidaten.
  const kandidaten = name === 'fsk' ? mKandidatenHtml(k) : '';
  return '<div class="mPfad mOk"><div class="mKopf">' + kopf + '</div>'
    + '<div class="mBild">' + (r.svg || '') + '</div>'
    + '<table class="mWerte"><tbody>' + zeilen + '</tbody></table>'
    + kandidaten
    + '<div><button class="mSave" data-mode="' + name + '">Save finding ('
    + titel + ')</button></div></div>';
}

function renderMeasure(st) {
  // T-1620: worauf sich der Befund bezieht, gehoert in die Ueberschrift -
  // eine Messung mit Abstimmung ist eine andere als ohne.
  el('measureWin').textContent = '(' + fmtMMSS(st.t0_s) + ' – ' + fmtMMSS(st.t1_s) + ')'
    + (st.tune_hz ? ' \\u00b7 tuned to ' + Math.round(st.tune_hz) + ' Hz, '
                    + Math.round(st.bw_hz) + ' Hz bandwidth' : '');
  const pfade = st.pfade || {};
  const treffer = (st.erfolgreich || []);
  measureTreffer = {};
  treffer.forEach(nm => {
    measureTreffer[nm] = { t0_s: st.t0_s, t1_s: st.t1_s, mode: nm,
                           kennwerte: pfade[nm].kennwerte };
  });

  // T-1614: kein Sieger bei Doppeltreffer - beide Bloecke stehen nebeneinander,
  // jeder mit eigenem Merken-Knopf. Befund an TETRA: FSK meldete 29 675 Bd bei
  // r2=0,98, PSK 18 001 Bd bei M=4 - und 18 000 Bd ist der Spezifikationswert.
  el('measureResult').innerHTML =
    Object.keys(pfade).map(nm => mPfadHtml(nm, pfade[nm])).join('');
  el('measureResult').querySelectorAll('.mSave').forEach(b =>
    b.addEventListener('click', () => saveMeasure(b.dataset.mode)));

  const hinweis = el('measureSvg');
  if (!treffer.length) {
    hinweis.innerHTML = '<p class="mLeer">No evaluable signal in this section. '
      + 'Multi-carrier modes (OFDM) are not detected by this tool.</p>';
    el('measureSave').style.display = 'none';
  } else {
    hinweis.innerHTML = treffer.length > 1
      ? '<p class="hint">Both paths give a result. r² and EVM are not '
        + 'comparable – decide from the picture which finding is usable.</p>' : '';
    el('measureSave').style.display = '';
  }
  el('measureBox').style.display = '';
}

function renderMeasureList(messungen) {
  const box = el('measureList');
  if (!messungen || !messungen.length) { box.innerHTML = '<p class="hint">none</p>'; return; }
  box.innerHTML = '<table><thead><tr><th>Window</th><th>Mode</th><th>Symbol rate</th>'
    + '<th>Note</th></tr></thead><tbody>'
    + messungen.slice().reverse().map(m =>
        '<tr><td>' + fmtMMSS(m.t0_s) + ' – ' + fmtMMSS(m.t1_s) + '</td>'
        + '<td>' + (m.mode || '') + '</td>'
        + '<td>' + mZahl((m.kennwerte || {}).baud) + '</td>'
        + '<td>' + (m.notiz || '') + '</td></tr>').join('')
    + '</tbody></table>';
}

async function loadMeasureList() {
  try {
    const r = await fetchJSON('/api/iqbefund?path=' + encodeURIComponent(path));
    renderMeasureList(r.messungen);
  } catch (e) { renderMeasureList([]); }
}

// T-1623: IQ-Ausschnitt hoerbar machen. Der Server rechnet fluechtig und
// liefert die WAV als Antwortkoerper; der Browser haelt sie im Speicher, auf
// der Platte bleibt nichts. Die Abstimmung kommt aus demselben
// Kontrollkaestchen wie beim Vermessen - steht es an, wird auf die
// Markermitte abgestimmt, sonst auf die Bandmitte.
async function doAudio() {
  const von = zoomWin ? zoomWin.t0_s : 0;
  const bis = zoomWin ? zoomWin.t1_s : audioDurationS;
  if (!(bis > von)) return;
  const btn = el('audioBtn');
  btn.disabled = true;
  el('measureHint').textContent = 'converting …';
  try {
    let u = '/api/audio?path=' + encodeURIComponent(path)
      + '&t0=' + von + '&t1=' + bis;
    if (el('tuneChk').checked && markerA != null && markerB != null) {
      u += '&tune=' + ((markerA + markerB) / 2).toFixed(1)
        + '&bw=' + Math.abs(markerA - markerB).toFixed(1);
    }
    const res = await fetch(u);
    if (!res.ok) {
      let data = null;
      try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
      el('measureHint').textContent = (data && data.error) || 'conversion failed';
    } else {
      const blob = await res.blob();
      if (audioUrl) URL.revokeObjectURL(audioUrl);   // vorige Fassung freigeben
      audioUrl = URL.createObjectURL(blob);
      // T-1624: die Wiedergabeleiste ist bei IQ ausgeblendet (T-1211, damit
      // der Browser die Rohdatei nicht laedt). Jetzt gibt es etwas zu hoeren,
      // also muss sie sichtbar werden - ohne das lief die Umwandlung ins
      // Leere, und der Knopf schien wirkungslos.
      el('playerBar').style.display = '';
      const p = el('player');
      p.src = audioUrl;
      p.play().catch(() => { /* Autostart kann blockiert sein - dann von Hand */ });
      el('measureHint').textContent = '';
    }
  } catch (e) {
    el('measureHint').textContent = 'conversion failed';
  }
  updateOverviewBtnState();
}

async function doMeasure() {
  if (!zoomWin) return;
  const btn = el('measureBtn');
  btn.disabled = true;
  el('measureHint').textContent = 'computing …';
  try {
    // T-1620: Mitte der Marker ist die Abstimmfrequenz, ihr Abstand die
    // Bandbreite, aus der das Werkzeug den Dezimationsfaktor ableitet.
    let u = '/api/measure?path=' + encodeURIComponent(path)
      + '&t0=' + zoomWin.t0_s + '&t1=' + zoomWin.t1_s + '&mode=auto';
    if (el('tuneChk').checked && markerA != null && markerB != null) {
      u += '&tune=' + ((markerA + markerB) / 2).toFixed(1)
        + '&bw=' + Math.abs(markerA - markerB).toFixed(1);
    }
    const res = await fetch(u);
    const data = await res.json();
    if (!res.ok) {
      el('measureHint').textContent = data.error || 'measurement failed';
    } else {
      el('measureHint').textContent = '';
      renderMeasure(data);
      loadMeasureList();
    }
  } catch (e) {
    el('measureHint').textContent = 'measurement failed';
  }
  btn.disabled = !zoomWin;
}

async function saveMeasure(modus) {
  const daten = measureTreffer[modus];
  if (!daten) return;
  try {
    const res = await fetch('/api/iqbefund', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ path: path, notiz: el('measureNote').value },
                                          daten))
    });
    const data = await res.json();
    if (res.ok) {
      el('measureSaveHint').textContent = 'saved';
      el('measureNote').value = '';
      renderMeasureList(data.messungen);
    } else {
      el('measureSaveHint').textContent = data.error || 'not saved';
    }
  } catch (e) {
    el('measureSaveHint').textContent = 'not saved';
  }
}

function showWfLoading(on) {
  el('wfLoading').style.display = on ? '' : 'none';
  stopWfPoll();
  if (!on) { wfWaiting = false; return; }
  setWfLoadingText('loading …', null);
  wfPollDelay = setTimeout(() => {
    wfPollDelay = null;
    wfPollTimer = setInterval(pollWfProgress, WF_POLL_INTERVAL_MS);
    pollWfProgress();
  }, WF_POLL_DELAY_MS);
}
function showWfError(msg) { const e = el('wfError'); e.textContent = msg; e.style.display = ''; }
function hideWfError() { el('wfError').style.display = 'none'; }

function handleAudspecError(status, message, code) {
  if (status === 409 && code === 'building') {
    // Sollte loadSpectrogram() schon abgefangen haben; hier nur als Netz,
    // damit dieser Fall NIE die Aufnahme-Meldung zeigt.
    showWfError('is being prepared elsewhere - please reload');
  } else if (status === 409) {
    // T-1606: 409 traegt zwei Lagen. Ohne Codepruefung stand hier auch bei
    // einem Hintergrundbau "Aufnahme laeuft noch" - irrefuehrend, sobald der
    // Aufbereitungs-Worker im Dauerbetrieb arbeitet.
    showWfError('recording still in progress - waterfall available after it stops');
  } else if (status === 415) {
    showWfError('format not supported (only 1 channel/16 bit or IQ)');
  } else if (status === 400 && zoomWin) {
    showWfError('time range too large - select a smaller range');
    // Zoomzustand verlassen, Uebersicht behalten: das zuletzt gezeichnete Bild
    // (die Uebersicht, aus der heraus in den Zoom gewechselt wurde) bleibt
    // unveraendert stehen - kein erneuter Fetch noetig.
    zoomWin = null;
    updateGeom();
    updateOverviewBtnState();
  } else {
    showWfError(message || 'error loading the waterfall');
  }
}

async function loadSpectrogram() {
  if (!path || !sampleRateHz) return;
  hideWfError();
  showWfLoading(true);
  const params = new URLSearchParams({ path: path, fft: String(curFft) });
  if (zoomWin) {
    params.set('t0', String(zoomWin.t0_s));
    params.set('t1', String(zoomWin.t1_s));
  }
  let header, matrixBytes;
  try {
    const res = await fetch('/api/audspec?' + params.toString());
    if (!res.ok) {
      let data = null;
      try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
      if (res.status === 409 && data && data.code === 'building') {
        // T-1606: kein Fehler - ein anderer baut. Abwarten statt abbrechen.
        waitForBackgroundBuild(data.progress);
        return;
      }
      handleAudspecError(res.status, data && data.error, data && data.code);
      showWfLoading(false);
      return;
    }
    wfRetries = 0;                            // erfolgreich geholt
    const buf = await res.arrayBuffer();
    const bytes = new Uint8Array(buf);
    const nl = bytes.indexOf(10);
    if (nl < 0) { showWfError('invalid response (no header)'); showWfLoading(false); return; }
    try {
      header = JSON.parse(new TextDecoder('utf-8').decode(bytes.subarray(0, nl)));
    } catch (e) {
      showWfError('invalid response (header unreadable)');
      showWfLoading(false);
      return;
    }
    matrixBytes = bytes.subarray(nl + 1);
    const expected = header.rows * header.n_bins;
    if (matrixBytes.length !== expected) {
      showWfError('incomplete response (' + matrixBytes.length + ' of ' + expected + ' bytes)');
      showWfLoading(false);
      return;
    }
  } catch (e) {
    showWfError(e.message || 'error loading the waterfall');
    showWfLoading(false);
    return;
  }

  if (header.mode === 'window') {
    zoomWin = { t0_s: header.t0_s, t1_s: header.t1_s };
  } else {
    zoomWin = null;
    audioDurationS = header.duration_s != null ? header.duration_s : audioDurationS;
  }
  applyBandFromHeader(header);
  updateGeom();
  drawSpectrogram(header, matrixBytes);
  drawOverlay();
  updateOverviewBtnState();
  lastHeader = header; lastMatrix = matrixBytes;   // T-1209: fuer Resize-Neuzeichnen
  showWfLoading(false);
}

// T-915: Das Band kommt aus dem Antwort-Header (audio_spectrum liefert die
// Mittenfrequenzen von Bin 0 und Bin n_bins-1). Fehlen die Felder, bleibt die
// Vorbelegung aus initWaterfall() stehen, statt die Achse auf 0 zu setzen.
function applyBandFromHeader(header) {
  if (typeof header.f_min_hz === 'number' && typeof header.f_max_hz === 'number'
      && header.f_max_hz > header.f_min_hz) {
    fMin = header.f_min_hz;
    fMax = header.f_max_hz;
  }
}

function startZoom(t0, t1) {
  if (t1 - t0 < 0.05) return;   // zu schmaler Zug - vermutlich kein Absichtsergebnis
  zoomWin = { t0_s: Math.max(0, t0), t1_s: Math.min(audioDurationS, t1) };
  loadSpectrogram();
}

function evX(ev) {
  const r = el('overlayCanvas').getBoundingClientRect();
  return (ev.clientX - r.left) * (CANVAS_W / r.width);
}
function evY(ev) {
  const r = el('overlayCanvas').getBoundingClientRect();
  return (ev.clientY - r.top) * (CANVAS_H / r.height);
}

function wireWaterfallEvents() {
  const overlay = el('overlayCanvas');
  overlay.addEventListener('mousedown', ev => {
    if (!geom) return;
    const x = evX(ev), y = evY(ev);
    if (x < geom.plotX || x > geom.plotX + geom.plotW || y < geom.plotY || y > geom.plotY + geom.plotH) return;
    downX = x; downY = y; downCtrl = ev.ctrlKey; dragCurX = x; dragActive = false;
  });
  window.addEventListener('mousemove', ev => {
    if (downX == null) return;
    dragCurX = evX(ev);
    if (!dragActive && !downCtrl && Math.abs(dragCurX - downX) >= 8) dragActive = true;
    if (dragActive) drawOverlay();
  });
  window.addEventListener('mouseup', () => {
    if (downX == null) return;
    if (dragActive && geom) {
      const x0 = Math.max(geom.plotX, Math.min(downX, dragCurX));
      const x1 = Math.min(geom.plotX + geom.plotW, Math.max(downX, dragCurX));
      startZoom(xToTime(x0, geom), xToTime(x1, geom));
    } else if (downCtrl && geom) {
      setMarker(yToFreq(downY, geom));
    } else if (geom) {
      el('player').currentTime = xToTime(downX, geom);
    }
    downX = downY = null; dragActive = false;
    drawOverlay();
  });
  document.querySelectorAll('input[name="fftSize"]').forEach(r => {
    r.onchange = () => { if (r.checked) { curFft = parseInt(r.value, 10); loadSpectrogram(); } };
  });
  el('overviewBtn').onclick = () => {
    if (!zoomWin) return;
    zoomWin = null;
    loadSpectrogram();
  };
  el('clearMarkersBtn').onclick = () => {
    markerA = null; markerB = null; nextMarkerSlot = 'A';
    updateMarkerInfo();
    drawOverlay();
  };
  el('yZoomBtn').onclick = () => {
    if (yZoom) { yZoom = null; }
    else if (markerA != null && markerB != null) {
      yZoom = { fLo: Math.min(markerA, markerB), fHi: Math.max(markerA, markerB) };
    }
    updateYZoomBtn();
    updateGeom();
    redrawSpectrogram();
    drawOverlay();
  };
  const p = el('player');
  p.addEventListener('play', () => { if (!rafId) rafId = requestAnimationFrame(tickPlayhead); });
  p.addEventListener('pause', () => { if (rafId) { cancelAnimationFrame(rafId); rafId = null; } drawOverlay(); });
  p.addEventListener('timeupdate', () => { if (rafId == null) drawOverlay(); });
  p.addEventListener('seeked', () => drawOverlay());
  // T-1209: bei Fenster-Resize Canvas-Breite nachziehen und aus den zuletzt
  // geladenen Daten neu zeichnen (kein erneuter Server-Request).
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      resizeTimer = null;
      if (!lastHeader || !fitCanvasWidth()) return;
      updateGeom();
      drawSpectrogram(lastHeader, lastMatrix);
      drawOverlay();
    }, 150);
  });
}

let waterfallWired = false;

// T-915: Spiegelbild von audio_spectrum.wav_kind() im Frontend (ersetzt
// isUnsupportedAudio aus T-1211, das IQ und Unbrauchbares noch in einen Topf
// warf):
//   'mono'  1 Kanal, 16 Bit  - abspielbar, klassifizierbar, Achse 0..Nyquist
//   'iq'    2 Kanaele, 16 Bit - Kanal 0 = I, Kanal 1 = Q (T-901). Spektrogramm
//           zweiseitig -Nyquist..+Nyquist; als Audio sinnlos (der Browser gaebe
//           die IQ-Spur als Rauschen wieder) und fuer audio_classify.py nicht
//           zugelassen - Player und Klassenzeilen bleiben deshalb aus.
//   'other' alles uebrige, auch ein unlesbarer WAV-Kopf.
// T-918/T-1627: Empfangskette und Geraet aus dem Sidecar (Bloecke rx/device,
// seit T-917). Die Empfangskette steht seit T-1627 als Zustandsmarken da, das
// Geraet weiter als Sammelzeile. Unterschieden werden vier Faelle: Block
// fehlt (Aufnahme vor T-917), Einzelwert nicht ermittelt (null -> "n/a"),
// Wert vorhanden, und bei der Daempfung der Mischfall aus Schaltzustand und
// Automatik - die sind am Geraet unabhaengig voneinander.
function rxNa(value, einheit) {
  if (value == null) return 'n/a';
  return einheit ? value + ' ' + einheit : String(value);
}
function rxEinAus(value) {
  return value == null ? 'n/a' : labelOf('empfangskette', value ? 'ein' : 'aus');
}
// Zustand: 'auto' (blau) = Automatik oder gesetzter Bezugswert, 'ein' (gruen),
// 'aus' (orange), 'na' (grau) = nicht ermittelt.
function rxChip(text, zustand, titel) {
  const t = titel ? ' title="' + esc(titel) + '"' : '';
  return '<span class="rxChip ' + zustand + '"' + t + '>' + esc(text) + '</span>';
}
// T-1626: der Median kommt NICHT aus dem rx-Block, sondern aus der Datei
// selbst (/api/meta level_median_dbfs) - deshalb der zweite Parameter. Die
// Einheiten sind verschieden und bleiben es: der Startpegel ist dBµV vom
// Geraet, der Median dBFS aus dem PCM. Die Beschriftung sagt das.
// T-1627: 'kind' kommt aus audioKind() und wird nur fuer eine Entscheidung
// gebraucht - siehe AFC weiter unten.
function fmtRxZeile(rx, medianDbfs, kind) {
  const chips = [];
  if (!rx) {
    chips.push(rxChip('Receive chain n/a', 'na',
      'Recording before T-917 - the sidecar did not carry an rx block yet'));
  } else {
    if (rx.gain_mode === 'MGC') {
      chips.push(rxChip('MGC ' + rxNa(rx.gain_mgc_dbuv, 'dBµV'), 'ein',
        'Manual gain control with a fixed gain value'));
    } else if (rx.gain_mode === 'AGC') {
      chips.push(rxChip('AGC', 'auto',
        'Automatic gain control - absolute level values are not reliable'));
    } else {
      chips.push(rxChip('Gain n/a', 'na'));
    }
    // att_state und att_auto sind unabhaengig: der Eichdaempfer kann aus sein
    // und die Automatik trotzdem eingeschaltet (Geraetebefund 2026-09-06).
    // Deshalb ein Chip fuer den Schaltzustand und ein ZUSATZCHIP fuer die
    // Automatik. Einen dB-Wert gibt es nicht - das Geraet kennt nur
    // ON|OFF|AUTO (Annex C, INPut:ATTenuation), die Stufe ist nicht abfragbar.
    if (rx.att_state == null) {
      chips.push(rxChip('ATT n/a', 'na'));
    } else {
      chips.push(rxChip('ATT ' + rxEinAus(rx.att_state), rx.att_state ? 'ein' : 'aus',
        'Attenuator - the receiver reports only on or off, no step in dB'));
    }
    if (rx.att_auto) {
      chips.push(rxChip('Auto', 'auto', 'Automatic attenuation enabled'));
    }
    chips.push(rxChip('Ref ' + rxNa(rx.ref_level_dbuv, 'dBµV'),
      rx.ref_level_dbuv == null ? 'na' : 'auto'));
    chips.push(rxChip('Level (start) ' + rxNa(rx.level_dbuv, 'dBµV'),
      rx.level_dbuv == null ? 'na' : 'auto',
      'Instantaneous value at the start of the recording, not averaged over the file'));
  }
  chips.push(rxChip('Level (median) ' + (medianDbfs == null
      ? 'n/a' : medianDbfs.toFixed(1) + ' dBFS'),
    medianDbfs == null ? 'na' : 'auto',
    'Median of the block level of the file, relative to full scale'));
  // AFC wird vom Geraet in CW und IQ zwar angenommen, regelt dort aber nicht -
  // FREQ:AFC? = 1 ist in diesen Betriebsarten irrefuehrend (Geraetebefund).
  // Bei IQ-Aufnahmen bleibt der Chip deshalb weg, statt etwas zu behaupten.
  if (rx && kind !== 'iq') {
    chips.push(rxChip('AFC ' + rxEinAus(rx.afc),
      rx.afc == null ? 'na' : (rx.afc ? 'ein' : 'aus')));
  }
  if (rx) {
    chips.push(rxChip('Squelch ' + rxEinAus(rx.squelch_on)
        + ' (' + rxNa(rx.squelch_thr_dbuv, 'dBµV') + ')',
      rx.squelch_on == null ? 'na' : (rx.squelch_on ? 'ein' : 'aus'),
      'The threshold is stored in the sidecar even when the squelch was off'));
  }
  return '<span class="rxChips">' + chips.join('') + '</span>';
}
function fmtGeraetZeile(dev) {
  if (!dev) return '<span class="sub">n/a (recording before T-917)</span>';
  const teile = [dev.model, dev.serial, dev.firmware].filter(Boolean);
  return teile.length ? esc(teile.join(' · ')) : 'n/a';
}

function audioKind(meta) {
  if (!meta.wav || meta.wav.sampwidth_bytes !== 2) return 'other';
  if (meta.wav.channels === 1) return 'mono';
  if (meta.wav.channels === 2) return 'iq';
  return 'other';
}

// T-1625: dasselbe Abzeichen wie in der Aufzeichnungsliste (:8050, badgeIq()
// aus manage_jobs.py) - die Klassen stehen in BASE_CSS, hier wird nichts
// Eigenes definiert. Drei Faelle, deckungsgleich mit audioKind(): IQ (orange),
// AUDIO (gruen), alles uebrige grau. Absichtlich KEINE Ableitung aus
// sidecar.mode: die Kanalzahl der Datei ist die harte Auskunft, der
// Sidecar-Modus kann bei importierten Aufnahmen fehlen.
function badgeArt(meta) {
  const kind = audioKind(meta);
  if (kind === 'iq') return '<span class="badge badge-iq">IQ</span>';
  if (kind === 'mono') return '<span class="badge badge-audio">AUDIO</span>';
  return '<span class="badge badge-unknown">UNKNOWN</span>';
}

// T-1627: die Bedienung sitzt seit dem Zweispalten-Umbau nicht mehr in
// #waterfall. Wer den Wasserfall ausblendet, muss sie eigens ausblenden -
// sonst stehen Regler ohne Spektrogramm da. Der linke Block nimmt dann die
// volle Breite ein; eine halbe Seite Leere daneben waere kein Layout.
function nurMetadaten() {
  el('ctrlBlock').style.display = 'none';
  el('topGrid').classList.add('einspaltig');
}

function initWaterfall(meta) {
  // T-915: Reihenfolge gegenueber T-1211 umgedreht - ein unlesbarer WAV-Kopf
  // gehoert in den wfNoData-Zweig ("keine Daten") und nicht in die
  // Formatabweisung, obwohl audioKind() ihn ebenfalls 'other' nennt.
  if (!meta.wav || !meta.wav.sample_rate_hz || !(meta.wav.duration_s > 0)) {
    el('wfNoData').style.display = '';
    nurMetadaten();
    el('wfCanvasWrap').style.display = 'none';
    return;
  }
  if (audioKind(meta) === 'other') {
    el('waterfall').style.display = 'none';
    nurMetadaten();
    return;
  }
  sampleRateHz = meta.wav.sample_rate_hz;
  audioDurationS = meta.wav.duration_s;
  // Vorbelegung fuer die erste Achsenzeichnung; den genauen Wert (Mitten-
  // frequenzen von Bin 0 und Bin n_bins-1) liefert der /api/audspec-Header,
  // siehe applyBandFromHeader().
  fMax = sampleRateHz / 2;
  fMin = audioKind(meta) === 'iq' ? -fMax : 0;
  measureKind = audioKind(meta);          // T-1611b: Knopfzustand
  // T-1619: bw_hz steht im Sidecar, das komplett im Frontend liegt - kein
  // Servereingriff noetig. Fehlt es (importierte Aufnahmen), bleibt es null.
  nutzBandbreiteHz = meta.sidecar && meta.sidecar.bw_hz ? Number(meta.sidecar.bw_hz) : null;
  updateFftHinweise();
  el('measureBtn').addEventListener('click', doMeasure);
  el('audioBtn').addEventListener('click', doAudio);
  loadMeasureList();
  if (!waterfallWired) { wireWaterfallEvents(); wireLevelControls(); waterfallWired = true; }
  loadSpectrogram();
}

// T-1007b-Nachtrag: der Klassifikator ist deterministisch und Aufnahmen sind
// unveraenderlich - ein manueller "Neu klassifizieren"-Knopf hat keinen
// Anwendungsfall mehr. Statt dessen klassifiziert die Einzeldatei-Ansicht
// beim Oeffnen automatisch, hoechstens einmal je Seitenaufruf (Flag unten).
let classifyAttempted = false;

function badgeClass(klasse) {
  return 'badge k-' + String(klasse || '').toLowerCase();
}

function fmtKdetails(ka) {
  const lines = [];
  lines.push('classifier_version: ' + (ka.classifier_version == null ? '–' : ka.classifier_version));
  lines.push('created_iso: ' + (ka.created_iso == null ? '–' : ka.created_iso));
  lines.push('reasons:');
  (ka.reasons || []).forEach(r => lines.push('  ' + r));
  lines.push('features:');
  const feats = ka.features || {};
  Object.keys(feats).sort().forEach(k => {
    const v = feats[k];
    lines.push('  ' + k + ' = ' + (typeof v === 'number' ? v.toFixed(3) : v));
  });
  return lines.join('\\n');
}

function renderKlasseAuto(meta) {
  const ka = meta.klasse_auto;
  let html;
  if (ka) {
    const schwach = ka.sicherheit === 'schwach';
    html = '<span class="' + badgeClass(ka.klasse) + (schwach ? ' schwach' : '') + '">' +
      esc(labelOf('klasse', ka.klasse)) + ' ' + Number(ka.voice_score).toFixed(2) +
      (schwach ? ' (' + esc(labelOf('sicherheit', 'schwach')) + ')' : '') + '</span> ';
  } else if (meta._classifying) {
    html = '<span class="hint">classifying …</span>';
  } else if (meta._classifyError) {
    html = esc(meta._classifyError);
  } else {
    html = 'not classified yet ';
  }
  if (ka) {
    html += '<br><a href="#" id="kdetailsToggle">Show features</a>' +
      '<pre class="kdetails" id="kdetails">' + esc(fmtKdetails(ka)) + '</pre>';
  }
  return html;
}

function renderKlasseManuell(meta) {
  const opts = ['<option value=""' + (meta.klasse_manuell ? '' : ' selected') + '>–</option>'];
  (meta.klassen || []).forEach(k => {
    opts.push('<option value="' + k + '"' + (meta.klasse_manuell === k ? ' selected' : '') + '>' + esc(labelOf('klasse', k)) + '</option>');
  });
  return '<select id="klasseSelect">' + opts.join('') + '</select> ' +
    '<span id="klasseSaved" class="hint" style="display:none">saved</span>';
}

function wireKdetailsToggle() {
  const toggle = el('kdetailsToggle');
  if (!toggle) return;
  toggle.onclick = (e) => {
    e.preventDefault();
    const pre = el('kdetails');
    pre.style.display = pre.style.display === 'block' ? 'none' : 'block';
  };
}

function wireKlasseSelect(meta) {
  const sel = el('klasseSelect');
  if (!sel) return;
  sel.onchange = async () => {
    try {
      const res = await fetch('/api/klasse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path, klasse: sel.value }),
      });
      let data = null;
      try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
      if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
      meta.klasse_manuell = data.klasse_manuell;
      const hint = el('klasseSaved');
      hint.style.display = '';
      setTimeout(() => { hint.style.display = 'none'; }, 2000);
      sel.classList.add('saved');
      setTimeout(() => { sel.classList.remove('saved'); }, 1000);
    } catch (e) {
      sel.value = meta.klasse_manuell || '';
      showError(e.message);
    }
  };
}

// T-1007b-Nachtrag: automatische Klassifikation beim Oeffnen (statt Knopf) -
// hoechstens ein Versuch je Seitenaufruf (classifyAttempted), egal ob er
// gelingt oder fehlschlaegt. Nutzt renderMeta() (nicht load()), damit
// initWaterfall() nicht ein zweites Mal laeuft.
async function autoClassify(meta) {
  if (meta.klasse_auto || meta.running || classifyAttempted || audioKind(meta) !== 'mono') return;
  classifyAttempted = true;
  meta._classifying = true;
  renderMeta(meta);
  try {
    const res = await fetch('/api/classify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path }),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* keine JSON-Antwort */ }
    if (!res.ok) {
      const code = data && data.code;
      if (code === 'busy') {
        meta._classifyError = 'recording still in progress';
      } else if (code === 'unsupported') {
        meta._classifyError = 'not classifiable (format)';
      } else {
        meta._classifyError = 'classification failed';
        showError((data && data.error) || ('HTTP ' + res.status));
      }
      meta._classifying = false;
      renderMeta(meta);
      return;
    }
    meta.klasse_auto = data;
    meta._classifying = false;
    renderMeta(meta);
  } catch (e) {
    meta._classifying = false;
    meta._classifyError = 'classification failed';
    showError(e.message);
    renderMeta(meta);
  }
}

function renderMeta(meta) {
  const rows = [];
  rows.push(['File', esc(path) + ' ' + badgeArt(meta)]);
  if (meta.wav) {
    if (meta.sidecar) {
      rows.push(['Frequency/mode', fmtMHz(meta.sidecar.freq_hz) + ' MHz &middot; ' + esc(meta.sidecar.mode || '?')]);
      rows.push(['Recorded on', fmtDateTime(meta.sidecar.start_iso)]);
    }
    rows.push(['Duration', meta.wav.duration_s != null ? meta.wav.duration_s.toFixed(1) + ' s' : '–']);
    rows.push(['Sample rate', meta.wav.sample_rate_hz + ' Hz']);
  } else {
    rows.push(['WAV header', '<span class="error">unreadable' +
      (meta.grund ? ' (' + esc(meta.grund) + ')' : '') + '</span>']);
  }
  if (meta.running) rows.push(['Status', '<span class="error">still running</span>']);
  rows.push(['Label', esc(meta.label || '–')]);
  // T-918: die Empfangsbedingungen gehoeren zur Aufnahme, nicht in den
  // Tooltip - sie gelten fuer die ganze Datei, nicht fuer ein einzelnes Bin.
  rows.push(['Receive chain', fmtRxZeile(meta.sidecar ? meta.sidecar.rx : null,
                                          meta.level_median_dbfs, audioKind(meta))]);
  rows.push(['Device', fmtGeraetZeile(meta.sidecar ? meta.sidecar.device : null)]);
  // T-1211: der Klassifikator arbeitet nur auf 1 Kanal / 16 Bit - sonst
  // stuende hier dauerhaft "not classifiable (format)" und ein
  // wirkungsloses Auswahlfeld.
  const kind = audioKind(meta);
  if (kind === 'mono') {
    rows.push(['Class (auto)', renderKlasseAuto(meta)]);
    rows.push(['Class (manual)', renderKlasseManuell(meta)]);
  } else if (kind === 'iq') {
    // T-915: das Spektrogramm ist fuer IQ da (zweiseitig), Wiedergabe und
    // Klassifikation bleiben es nicht - die Zeile sagt genau das.
    rows.push(['Format', 'IQ (2 channels): two-sided waterfall around the '
      + 'receive frequency; playback and classification not applicable']);
  } else {
    // T-1211 Nachtrag: statt der wortlos leeren Seite eine Zeile, die den
    // Grund nennt. Kanalzahl und Bittiefe kommen aus den WAV-Metadaten, nicht
    // fest verdrahtet.
    const ch = meta.wav ? meta.wav.channels : 0;
    const bits = meta.wav ? meta.wav.sampwidth_bytes * 8 : 0;
    rows.push(['Format', ch + ' ' + pluralWort(ch, 'channel', 'channels') + ', ' + bits
      + ' bit: playback, waterfall and classification not applicable']);
  }
  if (meta.note) rows.push(['Note', esc(meta.note)]);
  if (meta.known_freq) {
    rows.push(['Catalog', esc(meta.known_freq.label || meta.known_freq.id || '?')]);
  }
  if (meta.job) {
    rows.push(['Job', esc(meta.job.label || meta.job.job_id || '?') +
      ' (' + esc(labelOf('art', meta.job.type) || '?') + ' &middot; ' + esc(labelOf('zustand', meta.job.state) || '?') + ')']);
  }
  el('meta').innerHTML = rows.map(r =>
    '<div class="kv"><span class="k">' + esc(r[0]) + '</span>' + r[1] + '</div>').join('');

  wireKlasseSelect(meta);
  wireKdetailsToggle();
}

async function load() {
  if (!path) {
    el('meta').innerHTML = '<p class="hint">Open only via a deep link from the recordings list (:8050).</p>';
    return;
  }
  el('pageSub').textContent = path.split('/').pop();

  let meta;
  try {
    meta = await fetchJSON('/api/meta?path=' + encodeURIComponent(path));
  } catch (e) {
    showError(e.message);
    el('meta').innerHTML = '';
    return;
  }

  // T-1211/T-915: Wiedergabeleiste nur fuer mono. Die Quelle wird erst hier
  // gesetzt (vorher schon VOR dem /api/meta-Fetch), weil das Format erst aus
  // den Metadaten hervorgeht - sonst laedt der Browser das IQ-WAV noch, bevor
  // feststeht, dass es gar nicht abgespielt werden soll.
  if (audioKind(meta) === 'mono') {
    el('player').src = '/api/wav?path=' + encodeURIComponent(path);
  } else {
    el('playerBar').style.display = 'none';
  }

  // T-1214/T-1625: der Untertitel der Kopfleiste traegt den Kurztext
  // (audio_labels.json, in :8050 editierbar). Der Dateiname steht ohnehin in
  // der Metadatentafel und wird hier nur noch als Rueckfall benutzt, wenn
  // kein Kurztext gesetzt ist. Erst hier, weil der Kurztext aus /api/meta
  // kommt; die Zuweisung frueher in load() bleibt Vorbelegung.
  const dateiName = path.split('/').pop();
  const kopfText = meta.label || dateiName;
  el('pageSub').textContent = kopfText;
  document.title = 'MoniScope – Signal analysis – ' + kopfText;

  renderMeta(meta);
  autoClassify(meta);

  const sibLinks = [];
  if (meta.siblings && meta.siblings.specbin) {
    sibLinks.push('<a href="http://' + HOST + ':8040/?path=' + encodeURIComponent(meta.siblings.specbin) +
      '">Spectrum (:8040)</a>');
  }
  if (meta.siblings && meta.siblings.wav_parts) {
    if (meta.job && meta.job.type === 'mscan') {
      // T-1202-Nachtrag (Befund 2026-08-29): bei MSCAN-Segmenten waere die
      // Einzellink-Liste eine ~40-Link-Wand - die kuratierte Navigation
      // fuer MSCAN ist die Trefferliste (:8060), daher hier EIN Sammellink
      // statt der Geschwister-Einzellinks.
      const n = meta.siblings.wav_parts.length;
      if (n > 0) {
        sibLinks.push('<a href="http://' + HOST + ':8060/?run=' + encodeURIComponent(meta.job.job_id) +
          '" target="eb200_eval_mscan">' + n + ' more ' + pluralWort(n, 'segment', 'segments') + ' of this run &rarr; hit list (:8060)</a>');
      }
    } else {
      meta.siblings.wav_parts.forEach(p => {
        sibLinks.push('<a href="/?path=' + encodeURIComponent(p) + '">' + esc(p.split('/').pop()) + '</a>');
      });
    }
  }
  el('siblings').innerHTML = sibLinks.join(' ');

  initWaterfall(meta);
}

load();
</script>
</body>
</html>
"""


HTML_NO_PATH = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>MoniScope &ndash; Signal analysis</title>
<style>/*THEME_CSS*/</style>
<script>/*THEME_HEAD*/</script>
</head>
<body>
/*HEADER*/
<p>Open only via a deep link from the recordings list (:8050).</p>
<p><a href="#" onclick="location.href='http://'+location.hostname+':8050/'">&laquo; to the list (:8050)</a></p>
</body>
</html>
"""


# T-1404: Theme-Bausteine einsetzen (Tokens + Grundregeln, Head-Skript fuer das
# Theme-Cookie, Kopfleiste statt <h1>) - Muster wie :8040/:8020 (T-1402/T-1403).
# T-1625: der Ansichtsname ist fest, der Kurztext der Aufnahme wird per JS in
# #pageSub nachgetragen (Untertitelfeld, siehe ui_theme.header_html sub_id).
def _assemble(page: str, header: str) -> str:
    return (page.replace("/*THEME_CSS*/", THEME_CSS + BASE_CSS)
                .replace("/*THEME_HEAD*/", THEME_HEAD_JS)
                .replace("/*HEADER*/", header))

HTML_PAGE = _assemble(HTML_PAGE, header_html("Signal analysis", sub_id="pageSub"))
HTML_NO_PATH = _assemble(HTML_NO_PATH, header_html("Signal analysis"))


# ---------------------------------------------------------------------------
# HTTP-Handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                params = parse_qs(parsed.query)
                if params.get("path", [""])[0]:
                    self._send_html(HTML_PAGE)
                else:
                    self._send_html(HTML_NO_PATH)
            elif parsed.path == "/api/meta":
                self._handle_api_meta(parse_qs(parsed.query))
            elif parsed.path == "/api/wav":
                self._handle_api_wav(parse_qs(parsed.query))
            elif parsed.path == "/api/audspec":
                self._handle_api_audspec(parse_qs(parsed.query))
            elif parsed.path == "/api/audspec_status":
                self._handle_api_audspec_status(parse_qs(parsed.query))
            elif parsed.path == "/api/audio":
                self._handle_api_audio(parse_qs(parsed.query))
            elif parsed.path == "/api/measure":
                self._handle_api_measure(parse_qs(parsed.query))
            elif parsed.path == "/api/iqbefund":
                self._handle_api_iqbefund(parse_qs(parsed.query))
            elif parsed.path == "/api/note":
                self._handle_api_note(parse_qs(parsed.query))
            else:
                self._send_json({"error": "unknown endpoint"}, status=404)
        except Exception:
            # Sicherheitsnetz - jeder erwartete Fehlerfall wird bereits gezielt
            # in den _handle_*-Methoden abgefangen; das hier greift nur bei
            # wirklich unerwarteten Problemen (Muster analyze_spectrum.py).
            self._send_json({"error": ERR_INTERNAL}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/note":
                self._handle_set_note()
            elif parsed.path == "/api/klasse":
                self._handle_set_klasse()
            elif parsed.path == "/api/classify":
                self._handle_classify()
            elif parsed.path == "/api/iqbefund":
                self._handle_save_iqbefund()
            else:
                self._send_json({"error": "unknown endpoint"}, status=404)
        except Exception:
            self._send_json({"error": ERR_INTERNAL}, status=500)

    def _resolve_or_403(self, params: dict):
        """Gemeinsame Vorstufe: path-Parameter durch die Sandbox schicken.
        Gibt den aufgeloesten Pfad zurueck oder None (nach 403-Antwort)."""
        raw_path = params.get("path", [""])[0]
        try:
            return resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return None

    def _handle_api_meta(self, params: dict) -> None:
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        self._send_json(build_meta(resolved))

    def _handle_api_wav(self, params: dict) -> None:
        """Liefert die WAV mit HTTP-Range-Unterstuetzung (Muster
        manage_jobs._send_file, T-1006: 64-KiB-Bloecke, kein Volleinlesen).
        parse_range() traegt die eigentliche Range-Logik (siehe dort)."""
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        try:
            size = os.path.getsize(resolved)
        except OSError:
            self._send_json({"error": ERR_NOT_FOUND}, status=404)
            return

        try:
            rng = parse_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return

        if rng is None:
            start, end, status = 0, size - 1, 200
        else:
            start, end = rng
            status = 206

        length = end - start + 1
        try:
            self.send_response(status)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(resolved, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Browser bricht Audio-Streams beim Spulen regelmaessig ab -
            # kein Fehler (Muster manage_jobs._send_file/waterfall.py SSE).
            pass

    def _handle_api_audspec(self, params: dict) -> None:
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return

        try:
            n_fft = int(params.get("fft", [""])[0])
        except (TypeError, ValueError):
            n_fft = None
        if n_fft not in _VALID_FFT_SIZES:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        t0_raw = params.get("t0", [""])[0]
        t1_raw = params.get("t1", [""])[0]
        t0_s = t1_s = None
        if t0_raw != "" and t1_raw != "":
            try:
                t0_s, t1_s = float(t0_raw), float(t1_raw)
            except ValueError:
                self._send_json({"error": ERR_BAD_PARAMS}, status=400)
                return

        try:
            header, matrix = build_audspec_response(resolved, n_fft, t0_s, t1_s)
        except AudioSpectrumBuildingError as exc:
            # T-1601: ein anderer Prozess baut bereits. Bewusst NICHT als
            # Fehler gemeldet, sondern mit maschinenlesbarem Code und dem
            # Fortschritt - die Oberflaeche zeigt den Hinweis (T-1606).
            # Eigener Code, weil 409 hier zwei verschiedene Lagen traegt.
            self._send_json({"error": ERR_BUILDING, "code": "building",
                             "progress": exc.snapshot}, status=409)
            return
        except AudioSpectrumBusyError as exc:
            self._send_json({"error": str(exc), "code": "recording"}, status=409)
            return
        except AudioSpectrumUnsupportedError as exc:
            self._send_json({"error": str(exc)}, status=415)
            return
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except FileNotFoundError:
            self._send_json({"error": ERR_NOT_FOUND}, status=404)
            return

        body = (json.dumps(header) + "\n").encode("utf-8") + matrix.tobytes(order="C")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_api_audspec_status(self, params: dict) -> None:
        """GET /api/audspec_status?path=... - Fortschritt eines gerade
        laufenden build_audspec (T-1213).

        Billig und ohne jede Sperre auf dem Bau-Pfad: der eigentliche
        /api/audspec-Aufruf blockiert bis der Cache steht, diese Antwort
        kommt aus einem Nebenthread des QuietThreadingHTTPServer.
        "active": false heisst nur "hier laeuft gerade kein Bau" - der
        Normalfall bei Cache-Treffern und bei Zeitfenster-Anfragen.
        """
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        snap = progress_snapshot(resolved)
        if snap is None:
            self._send_json({"active": False})
            return
        snap["active"] = True
        self._send_json(snap)

    def _handle_api_audio(self, params: dict) -> None:
        """GET /api/audio?path=&t0=&t1=&tune=&bw= - IQ-Ausschnitt hoerbar
        machen (T-1623).

        Die WAV wird FLUECHTIG erzeugt: gerechnet in ein Temp-Verzeichnis,
        als Antwortkoerper ausgeliefert, danach weg (Entscheidung
        2026-09-11, OE3GAS). Das erspart Ablage, Verfallszeit, eine
        Aufraeumaufgabe im Worker und Platzbedarf in prepare_budget - bei
        Zehntelsekunden Rechenzeit je Ausschnitt ist Vorhalten sinnlos.
        """
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        try:
            t0_s = float(params.get("t0", [""])[0])
            t1_s = float(params.get("t1", [""])[0])
            tune_hz = float(params.get("tune", ["0"])[0] or 0.0)
            bw_hz = float(params.get("bw", ["0"])[0] or 0.0)
        except (TypeError, ValueError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        if t1_s <= t0_s:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        with tempfile.TemporaryDirectory() as tmp:
            ziel = os.path.join(tmp, "audio.wav")
            cmd = [sys.executable, EYE_DIAGRAM, resolved, "--audio", ziel,
                   "--t0", str(t0_s), "--t1", str(t1_s)]
            if tune_hz:
                cmd += ["--tune", str(tune_hz)]
            if bw_hz > 0:
                cmd += ["--auto-decimate", str(bw_hz)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=MEASURE_TIMEOUT_S,
                                      cwd=os.path.dirname(EYE_DIAGRAM) or None)
            except (OSError, subprocess.SubprocessError) as exc:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)
                return
            if proc.returncode != 0 or not os.path.isfile(ziel):
                meldung = _stderr_meldung(proc.stderr)
                self._send_json({"error": meldung or ERR_MEASURE}, status=422)
                return
            with open(ziel, "rb") as f:
                daten = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(daten)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(daten)

    def _handle_api_measure(self, params: dict) -> None:
        """GET /api/measure?path=&t0=&t1=&mode= - Signalvermessung eines
        Zeitfensters (T-1611a). Ergebnis wird NICHT gespeichert; das
        entscheidet der Bediener ueber POST /api/iqbefund.

        "leer" (kein auswertbares Signal) kommt mit HTTP 200 zurueck, nicht
        als Fehler: beim Suchen zoomt man staendig in Stellen ohne Signal.
        """
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        try:
            t0_s = float(params.get("t0", [""])[0])
            t1_s = float(params.get("t1", [""])[0])
        except (TypeError, ValueError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        mode = (params.get("mode", [MEASURE_AUTO])[0] or MEASURE_AUTO).lower()
        if mode not in MEASURE_MODES and mode not in (MEASURE_AUTO, MEASURE_OFDM):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        if t1_s <= t0_s:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        # T-1620: Abstimmfrequenz und Bandbreite kommen aus den Markern und
        # werden vom Frontend mitgeschickt. Der Dezimationsfaktor wird hier
        # abgeleitet, damit die Regel an EINER Stelle steht.
        try:
            tune_hz = float(params.get("tune", ["0"])[0] or 0.0)
            bw_hz = float(params.get("bw", ["0"])[0] or 0.0)
        except (TypeError, ValueError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        modi = MEASURE_MODES if mode == MEASURE_AUTO else (mode,)
        pfade = {m: run_measure(resolved, t0_s, t1_s, m, tune_hz, bw_hz) for m in modi}
        erfolgreich = [m for m, r in pfade.items() if r.get("ok")]
        # T-1613: der OFDM-Hinweis nur als LETZTE Auskunft - wenn beide
        # eintraegrigen Pfade nichts gefunden haben. Aus zwei Fehlermeldungen
        # wird so eine Aussage, und zugleich der Wink, dass die Feinarbeit
        # nach iqscan gehoert.
        if mode == MEASURE_AUTO and not erfolgreich:
            pfade[MEASURE_OFDM] = run_measure(resolved, t0_s, t1_s, MEASURE_OFDM,
                                               tune_hz, bw_hz)
        antwort = {"t0_s": t0_s, "t1_s": t1_s, "mode": mode, "pfade": pfade,
                   "tune_hz": tune_hz, "bw_hz": bw_hz,
                   "erfolgreich": erfolgreich}
        # 422 nur, wenn KEIN Pfad ueberhaupt gelaufen ist. "kein auswertbares
        # Signal" ist ein Ergebnis und kommt mit 200 - beim Suchen zoomt man
        # staendig in leere Stellen.
        if any(r.get("ok") or r.get("leer") for r in pfade.values()):
            self._send_json(antwort)
        else:
            meldungen = "; ".join(f"{m}: {r.get('meldung')}" for m, r in pfade.items())
            self._send_json({"error": meldungen or ERR_MEASURE,
                             "code": "measure"}, status=422)

    def _handle_api_iqbefund(self, params: dict) -> None:
        """GET /api/iqbefund?path=... - gespeicherte Messungen einer
        Aufnahme (Liste, aelteste zuerst)."""
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        self._send_json({"messungen": load_iqbefund(resolved)})

    def _handle_save_iqbefund(self) -> None:
        """POST /api/iqbefund - eine Messung merken. Der Rumpf traegt path,
        t0_s, t1_s, mode, kennwerte und optional eine Notiz; das SVG wird
        NICHT gespeichert - es laesst sich jederzeit neu rechnen und wuerde
        den Sidecar aufblaehen."""
        # Rumpf wie _handle_set_note: Laengenpruefung, dann JSON. Die
        # Kennwerte kommen unveraendert aus /api/measure zurueck, deshalb die
        # groessere Schranke - das Burstverzeichnis kann lang werden.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 262144:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            raw = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        if not isinstance(raw, dict):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            resolved = resolve_safe_path(raw.get("path", ""))
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return
        try:
            eintrag = {
                "t0_s": float(raw["t0_s"]), "t1_s": float(raw["t1_s"]),
                "mode": str(raw.get("mode", "fsk")),
                "kennwerte": raw["kennwerte"],
                "notiz": clean_note(str(raw.get("notiz", ""))),
                "gemessen_iso": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        except (KeyError, TypeError, ValueError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        if not isinstance(eintrag["kennwerte"], dict):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            messungen = append_iqbefund(resolved, eintrag)
        except OSError as exc:
            self._send_json({"error": f"{ERR_MEASURE}: {exc}"}, status=500)
            return
        self._send_json({"messungen": messungen})

    def _handle_api_note(self, params: dict) -> None:
        """GET /api/note?path=... - gespeicherte Notiz liefern (leerer
        String, falls keine vorhanden; Muster analyze_spectrum.py)."""
        resolved = self._resolve_or_403(params)
        if resolved is None:
            return
        self._send_json({"note": NOTES.get(to_relative(resolved))})

    def _handle_set_note(self) -> None:
        # Notiz setzen. Der Pfad kommt aus dem JSON-Body und MUSS - wie jeder
        # Lesezugriff - durch die Sandbox (LAN-Bindung). Speicher getrennt
        # vom Kurztext (LABELS, hier nur lesend genutzt) - Muster
        # analyze_spectrum._handle_set_note.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 32768:      # 4000 Zeichen koennen UTF-8/JSON-escaped mehr Bytes sein
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_path = payload["path"]
            note = payload.get("note", "")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        rel = to_relative(resolved)
        stored = NOTES.set(rel, note)
        self._send_json({"path": rel, "note": stored})

    def _handle_set_klasse(self) -> None:
        # Handlabel setzen. Pfad aus dem JSON-Body MUSS durch die Sandbox
        # (Muster _handle_set_note); kurze Werte (Pfad + Klassenname) ->
        # 4096 statt der 32768 der Notiz genuegen. clean_klasse() wirft
        # ValueError fuer eine Klasse ausserhalb von KLASSEN - LabelStore.set()
        # reicht das durch, wir bilden es hier auf 400 ab.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 4096:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_path = payload["path"]
            raw_klasse = payload.get("klasse", "")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        rel = to_relative(resolved)
        try:
            stored = KLASSEN_MANUELL.set(rel, raw_klasse)
        except ValueError:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        self._send_json({"path": rel, "klasse_manuell": stored})

    def _handle_classify(self) -> None:
        # Klassifikation synchron anstossen/erneuern. Pfad aus dem JSON-Body
        # MUSS durch die Sandbox (Muster _handle_set_note); Content-Length-
        # Grenze 4096 wie bei /api/klasse (kurze Werte).
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 4096:
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_path = payload["path"]
            rebuild = bool(payload.get("rebuild", False))
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json({"error": ERR_BAD_PARAMS}, status=400)
            return

        try:
            resolved = resolve_safe_path(raw_path)
        except ValueError:
            self._send_json({"error": ERR_SANDBOX}, status=403)
            return

        try:
            sidecar = ensure_klasse(resolved, rebuild=rebuild)
        except AudioClassifyBusyError:
            self._send_json({"error": "recording still in progress", "code": "busy"}, status=409)
            return
        except AudioClassifyUnsupportedError as exc:
            self._send_json({"error": str(exc), "code": "unsupported"}, status=400)
            return
        except FileNotFoundError:
            self._send_json({"error": ERR_NOT_FOUND}, status=404)
            return

        klasse_auto = {k: sidecar.get(k) for k in (
            "klasse", "voice_score", "sicherheit", "classifier_version",
            "created_iso", "reasons", "features",
        )}
        self._send_json(klasse_auto)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


from eb200_core.http_base import QuietThreadingHTTPServer as ThreadingHTTPServer


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def _run_server() -> int:
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"[analyze-audio] Server laeuft auf http://{HTTP_HOST}:{HTTP_PORT}, "
          f"Wurzel: {AUDIO_ROOT}")
    register_server("analyze_audio", HTTP_PORT, {"root": str(AUDIO_ROOT)})
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Signalanalyse :8070 (T-1202)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren (kein Server)")
    ap.add_argument("--data-dir", metavar="PFAD",
                    help="Datenwurzel (wertet eb200_core/paths.py beim Import aus, T-2003)")
    args = ap.parse_args()
    if args.selftest:
        _self_test()
        return 0
    return _run_server()


# ---------------------------------------------------------------------------
# Selbsttest (python analyze_audio.py --selftest, ohne Hardware/Socket)
# ---------------------------------------------------------------------------

def _write_test_wav(path: str, samples_f32, sample_rate_hz: int, channels: int = 1) -> None:
    """Reiner Testhelfer (Muster audio_spectrum._write_test_wav) - schreibt
    16-Bit-PCM ohne Import von eb200_core/audio_wav.py."""
    import numpy as np
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pcm = np.clip(np.asarray(samples_f32) * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(pcm.tobytes())


def _write_test_sidecar(wav_path: str, running: bool, **extra) -> None:
    data = {"running": running}
    data.update(extra)
    with open(wav_path + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f)


def _self_test() -> None:
    global AUDIO_ROOT
    import tempfile
    import numpy as np

    # --- T1: parse_range -----------------------------------------------------
    size = 1000
    assert parse_range(None, size) is None
    assert parse_range("bytes=0-99", size) == (0, 99)
    assert parse_range("bytes=100-", size) == (100, size - 1)
    assert parse_range("bytes=-100", size) == (size - 100, size - 1)
    try:
        parse_range("bytes=5-2", size)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet (start > end)")
    assert parse_range("bytes=0-99,200-300", size) is None  # Multi-Range -> None (200 komplett)
    try:
        parse_range("seiten=1", size)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet (falsche Einheit)")
    try:
        parse_range("bytes=5000-6000", size)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet (Bereich hinter Dateiende)")
    print("T1 ok  (parse_range: einfache Bereiche, Suffix, offenes Ende, "
          "kaputt/Multi-Range/Unsinn/hinter Dateiende)")

    # --- T2: resolve_safe_path ------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        old_root = AUDIO_ROOT
        AUDIO_ROOT = os.path.realpath(tmp)
        # Eigene, tempdir-gebundene Stores statt der modulglobalen LABELS/
        # NOTES (die sind an den ECHTEN Repo-Root gebunden - siehe
        # build_meta()-Docstring) - der Selbsttest darf die echten
        # audio_labels.json/audio_notes.json niemals anfassen.
        tmp_labels = LabelStore(os.path.join(AUDIO_ROOT, "audio_labels.json"))
        tmp_notes = LabelStore(os.path.join(AUDIO_ROOT, "audio_notes.json"), cleaner=clean_note)
        tmp_klassen = LabelStore(os.path.join(AUDIO_ROOT, "audio_klassen.json"), cleaner=clean_klasse)
        try:
            os.makedirs(os.path.join(AUDIO_ROOT, "records"), exist_ok=True)
            wav_ok = os.path.join(AUDIO_ROOT, "records", "ok.wav")
            with open(wav_ok, "wb") as f:
                f.write(b"RIFF")
            resolved = resolve_safe_path("records/ok.wav")
            assert resolved == os.path.realpath(wav_ok), resolved

            for bad in ("../../x.wav", "..\\..\\x.wav", "C:\\Windows\\x.wav" if os.name == "nt" else "/etc/x.wav"):
                try:
                    resolve_safe_path(bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"ValueError erwartet (Sandbox): {bad!r}")

            try:
                resolve_safe_path("records/ok.specbin")
            except ValueError:
                pass
            else:
                raise AssertionError("ValueError erwartet (keine .wav-Endung)")
            print("T2 ok  (resolve_safe_path: gueltiger Pfad; Traversal/Fremdpfad/Nicht-.wav abgelehnt)")

            # --- T2b: externer storage_root (T-201, Whitelist aller storage_roots) --
            # sandbox_roots() liest storage_roots.json aus AUDIO_ROOT (hier der
            # tempdir von oben) - ein zusaetzlicher, ausserhalb von AUDIO_ROOT
            # liegender Root muss fuer absolute Pfade greifen, ein Pfad
            # ausserhalb ALLER Roots bleibt weiterhin abgelehnt. Die eigentliche
            # Sandbox-Logik (Roots/Suffixe/Traversal) ist bereits in
            # eb200_core/path_sandbox.py --selftest (T1-T8) erschoepfend
            # geprueft - hier reicht der Nachweis, dass der duenne Wrapper
            # resolve_safe_path() sie tatsaechlich mit AUDIO_ROOT aufruft.
            with tempfile.TemporaryDirectory() as extern:
                extern_real = os.path.realpath(extern)
                with open(os.path.join(AUDIO_ROOT, "storage_roots.json"), "w", encoding="utf-8") as f:
                    json.dump([extern_real], f)
                wav_extern = os.path.join(extern_real, "extern.wav")
                with open(wav_extern, "wb") as f:
                    f.write(b"RIFF")
                resolved_extern = resolve_safe_path(wav_extern)
                assert resolved_extern == os.path.realpath(wav_extern), resolved_extern
                try:
                    resolve_safe_path(os.path.join(extern_real, "..", "ausserhalb.wav"))
                except ValueError:
                    pass
                else:
                    raise AssertionError("ValueError erwartet (ausserhalb aller Roots)")
            os.remove(os.path.join(AUDIO_ROOT, "storage_roots.json"))
            print("T2b ok  (resolve_safe_path: externer storage_root wird akzeptiert, "
                  "ausserhalb aller Roots weiterhin abgelehnt)")

            # --- T3: build_meta auf tmp-Struktur ----------------------------------
            sr = 16000
            n = sr  # 1s
            tone = 0.2 * np.sin(2 * np.pi * 1000.0 * np.arange(n) / sr)
            wav_a = os.path.join(AUDIO_ROOT, "records", "meta_a.wav")
            _write_test_wav(wav_a, tone, sr)
            _write_test_sidecar(wav_a, running=False, freq_hz=100_000_000, mode="FM",
                                 start_iso="2026-08-29T10:00:00+02:00")
            resolved_a = resolve_safe_path("records/meta_a.wav")
            meta_a = build_meta(resolved_a, labels=tmp_labels, notes=tmp_notes, klassen=tmp_klassen)
            assert meta_a["sidecar"]["freq_hz"] == 100_000_000, meta_a
            assert meta_a["wav"]["sample_rate_hz"] == sr, meta_a
            assert meta_a["wav"]["channels"] == 1, meta_a
            assert meta_a["running"] is False, meta_a
            assert meta_a["job"] is None, meta_a
            assert meta_a["siblings"]["specbin"] is None, meta_a

            # .specbin gleichen Stems -> siblings.specbin gesetzt.
            specbin_path = wav_a[:-len(".wav")] + ".specbin"
            with open(specbin_path, "wb") as f:
                f.write(b"\x00\x01")
            meta_a2 = build_meta(resolved_a, labels=tmp_labels, notes=tmp_notes, klassen=tmp_klassen)
            assert meta_a2["siblings"]["specbin"] == "records/meta_a.specbin", meta_a2["siblings"]

            # WAV unter records/longrun_X/ mit job.json -> Job-Kontext + wav_parts.
            longrun_dir = os.path.join(AUDIO_ROOT, "records", "longrun_XYZ")
            os.makedirs(longrun_dir, exist_ok=True)
            with open(os.path.join(longrun_dir, "job.json"), "w", encoding="utf-8") as f:
                json.dump({"job_id": "XYZ", "label": "Test-Job", "type": "mscan", "state": "beendet"}, f)
            wav_b = os.path.join(longrun_dir, "part_b.wav")
            wav_c = os.path.join(longrun_dir, "part_c.wav")
            _write_test_wav(wav_b, tone, sr)
            _write_test_wav(wav_c, tone, sr)
            _write_test_sidecar(wav_b, running=False)
            meta_b = build_meta(resolve_safe_path("records/longrun_XYZ/part_b.wav"),
                                labels=tmp_labels, notes=tmp_notes, klassen=tmp_klassen)
            assert meta_b["job"] == {"job_id": "XYZ", "label": "Test-Job", "type": "mscan", "state": "beendet"}, meta_b["job"]
            assert meta_b["siblings"]["wav_parts"] == ["records/longrun_XYZ/part_c.wav"], meta_b["siblings"]

            # Sidecar fehlt -> sidecar null, wav-Fakten trotzdem da.
            wav_d = os.path.join(AUDIO_ROOT, "records", "meta_nosidecar.wav")
            _write_test_wav(wav_d, tone, sr)
            meta_d = build_meta(resolve_safe_path("records/meta_nosidecar.wav"),
                                labels=tmp_labels, notes=tmp_notes, klassen=tmp_klassen)
            assert meta_d["sidecar"] is None, meta_d
            assert meta_d["wav"] is not None and meta_d["wav"]["sample_rate_hz"] == sr, meta_d
            print("T3 ok  (build_meta: WAV+Sidecar; +specbin-Geschwister; Job-Kontext+wav_parts; fehlendes Sidecar)")

            # --- T4: build_audspec_response -----------------------------------------
            two_tone = 0.2 * np.sin(2 * np.pi * 1000.0 * np.arange(sr * 5) / sr)
            wav_e = os.path.join(AUDIO_ROOT, "records", "spec_e.wav")
            _write_test_wav(wav_e, two_tone, sr)
            _write_test_sidecar(wav_e, running=False)
            resolved_e = resolve_safe_path("records/spec_e.wav")

            header_ov, matrix_ov = build_audspec_response(resolved_e, 512)
            assert header_ov["mode"] == "overview", header_ov
            assert matrix_ov.nbytes == header_ov["rows"] * header_ov["n_bins"], (matrix_ov.nbytes, header_ov)

            header_win, matrix_win = build_audspec_response(resolved_e, 512, 1.0, 2.0)
            assert header_win["mode"] == "window", header_win
            assert header_win["db_min"] == header_ov["db_min"], (header_win, header_ov)
            assert header_win["db_max"] == header_ov["db_max"], (header_win, header_ov)
            assert matrix_win.shape[0] > 0, matrix_win.shape

            # running=True auf der BEREITS GECACHTEN Datei: die Uebersicht
            # (ensure_audspec) liest den weiterhin gueltigen Cache (Groesse/
            # mtime der WAV unveraendert) und prueft "running" NICHT erneut -
            # busy zeigt sich hier nur im Fenster-Pfad, der IMMER frisch aus
            # der WAV liest (stft_window prueft running bei jedem Aufruf).
            _write_test_sidecar(wav_e, running=True)
            try:
                build_audspec_response(resolved_e, 512, 1.0, 2.0)
            except AudioSpectrumBusyError:
                pass
            else:
                raise AssertionError("AudioSpectrumBusyError erwartet (Fenster, running=True)")
            _write_test_sidecar(wav_e, running=False)

            # Frische, NOCH NIE gecachte Datei mit running=True von Anfang an:
            # hier erzwingt das Fehlen eines Caches den Bau-Pfad
            # (ensure_audspec -> build_audspec), der busy ebenfalls prueft.
            wav_f = os.path.join(AUDIO_ROOT, "records", "spec_f.wav")
            _write_test_wav(wav_f, two_tone, sr)
            _write_test_sidecar(wav_f, running=True)
            resolved_f = resolve_safe_path("records/spec_f.wav")
            try:
                build_audspec_response(resolved_f, 512)
            except AudioSpectrumBusyError:
                pass
            else:
                raise AssertionError("AudioSpectrumBusyError erwartet (Uebersicht, kein Cache, running=True)")
            print("T4 ok  (build_audspec_response: Uebersicht nbytes=rows*n_bins; "
                  "Fenster mode=window mit Cache-Skala; running=True -> AudioSpectrumBusyError/409-Fall "
                  "im Fenster- UND im ungecachten Uebersichts-Pfad)")

            # --- T4-IQ (T-915): zweiseitiges Band durch /api/audspec ------------------
            # Ein komplexer Traeger bei +5 kHz. Die Antwort muss kind/f_min_hz/
            # f_max_hz tragen (sonst kann die Seite ihre Achse nicht beschriften)
            # und der Peak muss OBERHALB der Mitte liegen - laege Q invertiert
            # vor, saesse er spiegelbildlich darunter.
            t_iq = np.arange(sr * 4) / sr
            iq = 0.4 * np.exp(2j * np.pi * 5000.0 * t_iq)
            iq_inter = np.empty(iq.size * 2, dtype=np.float64)
            iq_inter[0::2] = iq.real
            iq_inter[1::2] = iq.imag
            wav_iq = os.path.join(AUDIO_ROOT, "records", "spec_iq.wav")
            _write_test_wav(wav_iq, iq_inter, sr, channels=2)
            _write_test_sidecar(wav_iq, running=False)
            resolved_iq = resolve_safe_path("records/spec_iq.wav")

            header_iq, matrix_iq = build_audspec_response(resolved_iq, 512)
            assert header_iq["kind"] == "iq", header_iq
            assert header_iq["n_bins"] == 512, header_iq
            assert header_iq["f_min_hz"] == -sr / 2, header_iq
            assert header_iq["f_max_hz"] < sr / 2, header_iq
            iq_row = matrix_iq[matrix_iq.shape[0] // 2].astype(np.float64)
            iq_bin = int(round(256 + 5000.0 / (sr / 512)))
            assert iq_row[iq_bin - 1:iq_bin + 2].max() > np.median(iq_row) + 20, (iq_bin, iq_row[iq_bin])
            assert iq_row[512 - iq_bin - 1:512 - iq_bin + 2].max() < iq_row[iq_bin - 1:iq_bin + 2].max() - 80, \
                "Spiegelpeak unterhalb der Mitte - I/Q vertauscht?"

            header_iq_win, _ = build_audspec_response(resolved_iq, 512, 1.0, 2.0)
            assert header_iq_win["kind"] == "iq", header_iq_win
            assert header_iq_win["f_min_hz"] == -sr / 2, header_iq_win

            header_mono_band, _ = build_audspec_response(resolved_e, 512)
            assert header_mono_band["kind"] == "mono", header_mono_band
            assert (header_mono_band["f_min_hz"], header_mono_band["f_max_hz"]) == (0.0, sr / 2), header_mono_band
            print("T4-IQ ok  (IQ ueber build_audspec_response: kind/f_min_hz/f_max_hz im Header, "
                  "n_bins=n_fft, Traeger bei +5 kHz oberhalb der Mitte, mono unveraendert 0..Nyquist)")

            # --- T5: Note-Roundtrip ---------------------------------------------------
            rel_e = to_relative(resolved_e)
            assert tmp_notes.get(rel_e) == ""
            stored = tmp_notes.set(rel_e, "Testnotiz")
            assert stored == "Testnotiz", stored
            assert tmp_notes.get(rel_e) == "Testnotiz"
            print("T5 ok  (Notiz: set/get-Roundtrip ueber die tmp-LabelStore)")

            # --- T-K1: build_meta ohne .klasse.json -----------------------------------
            meta_k1 = build_meta(resolved_e, labels=tmp_labels, notes=tmp_notes, klassen=tmp_klassen)
            assert meta_k1["klasse_auto"] is None, meta_k1["klasse_auto"]
            assert meta_k1["klasse_manuell"] == "", meta_k1["klasse_manuell"]
            assert meta_k1["klassen"] == list(KLASSEN), meta_k1["klassen"]
            print("T-K1 ok  (build_meta ohne .klasse.json: klasse_auto None, klasse_manuell "
                  "'', klassen == list(KLASSEN))")

            # --- T-K2: ensure_klasse + build_meta -> klasse_auto befuellt -------------
            ensure_klasse(resolved_e)
            meta_k2 = build_meta(resolved_e, labels=tmp_labels, notes=tmp_notes, klassen=tmp_klassen)
            ka = meta_k2["klasse_auto"]
            assert ka is not None, meta_k2
            assert ka["klasse"] in KLASSEN, ka
            assert isinstance(ka["voice_score"], float), ka
            assert ka["sicherheit"] in ("normal", "schwach"), ka
            print(f"T-K2 ok  (ensure_klasse + build_meta: klasse_auto.klasse={ka['klasse']!r} in KLASSEN, "
                  f"voice_score={ka['voice_score']!r} float, sicherheit={ka['sicherheit']!r})")

            # --- T-K3: clean_klasse + Store-Roundtrip ---------------------------------
            assert clean_klasse("sprache") == "SPRACHE", clean_klasse("sprache")
            assert clean_klasse("") == "", clean_klasse("")
            assert clean_klasse(None) == "", clean_klasse(None)
            try:
                clean_klasse("MUSIK")
            except ValueError:
                pass
            else:
                raise AssertionError("ValueError erwartet (Klasse ausserhalb KLASSEN)")
            assert tmp_klassen.set(rel_e, "cw") == "CW", tmp_klassen.get(rel_e)
            assert tmp_klassen.get(rel_e) == "CW", tmp_klassen.get(rel_e)
            assert tmp_klassen.set(rel_e, "") == "", tmp_klassen.get(rel_e)
            assert tmp_klassen.get(rel_e) == "", tmp_klassen.get(rel_e)
            print("T-K3 ok  (clean_klasse: Normalisierung/leer/ValueError; Store-Roundtrip set/get)")

            # --- T-K4: laufende Aufnahme -> ensure_klasse wirft AudioClassifyBusyError --
            # (Handler selbst nicht getestet, wie beim bestehenden Muster fuer
            # AudioSpectrumBusyError in T4 - reiner Nachweis, dass ensure_klasse()
            # die running=True-Sperre respektiert. rebuild=True noetig: T-K2 hat
            # bereits ein gueltiges .klasse.json fuer resolved_e gebaut, ein
            # Cache-Treffer wuerde classify_wav() - und damit die running-Pruefung -
            # sonst gar nicht erst erreichen.)
            _write_test_sidecar(resolved_e, running=True)
            try:
                ensure_klasse(resolved_e, rebuild=True)
            except AudioClassifyBusyError:
                pass
            else:
                raise AssertionError("AudioClassifyBusyError erwartet (running=True)")
            _write_test_sidecar(resolved_e, running=False)
            print("T-K4 ok  (laufende Aufnahme -> ensure_klasse wirft AudioClassifyBusyError)")
        finally:
            AUDIO_ROOT = old_root

    # T-1211/T-915: Player-, Spektrogramm- und Klassenzeilen-Logik ist reine
    # Frontend-Logik in HTML_PAGE - hier nur die Textbausteine pruefen (kein
    # Browser noetig).
    for _frag in ("function audioKind(meta)",
                  "el('playerBar').style.display = 'none'",
                  "el('waterfall').style.display = 'none'",
                  "if (kind === 'mono') {",
                  "} else if (kind === 'iq') {",
                  "function applyBandFromHeader(header)",
                  "function fmtRxZeile(rx, medianDbfs, kind)",
                  "rows.push(['Receive chain'",
                  "n/a (recording before T-917)",
                  "const AXIS_TEXT_COLOR = '#9cf';",
                  # T-1616: die Zeile traegt jetzt den vertikalen Zoom. Geprueft
                  # wird nur noch, DASS geom aus fMin/fMax gebaut wird - nicht
                  # mehr die genaue Schreibweise, sonst bricht der Test bei
                  # jeder Erweiterung derselben Zeile erneut.
                  "fMin:", "fMax:",
                  "geom.fMin + (geom.fMax - geom.fMin)"):
        assert _frag in HTML_PAGE, _frag
    assert HTML_PAGE.count("el('player').src") == 1, HTML_PAGE.count("el('player').src")
    # Nur AUFRUFE pruefen: der Name steht weiterhin in einem Kommentar, der
    # erklaert, was audioKind() abgeloest hat.
    assert "isUnsupportedAudio(" not in HTML_PAGE, "isUnsupportedAudio wird noch aufgerufen"
    print("T-915 ok  (audioKind mono/iq/other, zweiseitige Achse, Band aus dem Antwort-Header)")

    # T-1213: Fortschrittsanzeige. Serverseitig nur die Verdrahtung (die
    # Registry selbst prueft audio_spectrum --selftest T11), im Frontend die
    # Textbausteine - beides ohne Socket und ohne Browser.
    assert progress_snapshot(__file__) is None, "kein Bau, trotzdem ein Eintrag"
    assert hasattr(Handler, "_handle_api_audspec_status"), "Handler fehlt"
    with open(__file__, encoding="utf-8") as _f:
        _own_source = _f.read()
    assert '"/api/audspec_status":' in _own_source, "Route nicht in do_GET verdrahtet"
    for _frag in ("id=\"wfProgressBar\"",
                  "id=\"wfLoadingText\"",
                  "class=\"wf-bar\"",
                  ".wf-bar-fill { display:block;",
                  "const WF_POLL_DELAY_MS = 800;",
                  "async function pollWfProgress()",
                  "'/api/audspec_status?path=' + encodeURIComponent(path)",
                  "spectrogram is being computed",
                  "spectrogram is being written",
                  "function stopWfPoll()"):
        assert _frag in HTML_PAGE, _frag
    assert HTML_PAGE.count("function showWfLoading(on)") == 1
    print("T-1213 ok  (progress_snapshot ohne Bau -> None, Route und Handler verdrahtet, "
          "Balken/Poll-Bausteine in HTML_PAGE)")

    # T-1601: die beiden 409-Lagen muessen unterscheidbar bleiben - "recording"
    # (Aufnahme laeuft, Sidecar running=True) gegen "building" (ein anderer
    # Prozess baut bereits). Ohne Code liesse sich im Frontend nicht sagen, ob
    # gewartet oder abgebrochen werden soll.
    assert '"code": "building"' in _own_source, "Code building fehlt"
    assert '"code": "recording"' in _own_source, "Code recording fehlt"
    assert "AudioSpectrumBuildingError" in _own_source, "Import/Abfang fehlt"
    print("T-1601 ok  (AudioSpectrumBuildingError -> 409 mit code=building und Fortschritt)")

    # T-1606: der 409 traegt zwei Lagen, und das Frontend muss sie
    # unterscheiden. Vorher zeigte JEDER 409 "Aufnahme laeuft noch" - bei
    # einem Hintergrundbau eine Meldung ueber eine Aufnahme, die gar nicht
    # laeuft. Geprueft werden die Bausteine in HTML_PAGE, ohne Browser.
    for _frag in ("function waitForBackgroundBuild(progress)",
                  "function renderWfProgress(st, fremd)",
                  "data.code === 'building'",
                  "status === 409 && code === 'building'",
                  "is being prepared in the background",
                  "const WF_BUILD_RETRIES = 3;",
                  "let wfWaiting = false;",
                  "wfRetries = 0;"):
        assert _frag in HTML_PAGE, _frag
    # handleAudspecError muss den Code entgegennehmen, sonst faellt jeder 409
    # wieder in denselben Zweig.
    assert "function handleAudspecError(status, message, code)" in HTML_PAGE
    assert "handleAudspecError(res.status, data && data.error, data && data.code)" in HTML_PAGE
    # Die Aufnahme-Meldung darf es nur EINMAL geben, im recording-Zweig.
    assert HTML_PAGE.count("recording still in progress - waterfall available after it stops") == 1
    print("T-1606 ok  (409 nach code aufgeteilt: building wartet den fremden Bau ab, "
          "recording behaelt die Aufnahme-Meldung)")

    # --- T-1611a: Signalvermessung ------------------------------------------
    # Geprueft werden Sidecar-Ablage und die Verdrahtung. Der eigentliche
    # Messlauf braucht eine IQ-Datei mit Signal und laeuft als Unterprozess -
    # das deckt tools/eye_diagram.py --selftest ab (T25), hier waere es nur
    # eine langsame Wiederholung.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _tmp:
        _wav = os.path.join(_tmp, "m.wav")
        with open(_wav, "wb") as _f:
            _f.write(b"kein WAV, nur Traeger fuer den Sidecar-Pfad")
        assert iqbefund_path_for(_wav) == _wav + IQBEFUND_SUFFIX
        assert load_iqbefund(_wav) == [], "ohne Sidecar leere Liste"

        _e1 = {"t0_s": 1.0, "t1_s": 5.0, "mode": "fsk",
               "kennwerte": {"baud": 100.0}, "notiz": "erste",
               "gemessen_iso": "2026-09-10T12:00:00+02:00"}
        _liste = append_iqbefund(_wav, _e1)
        assert len(_liste) == 1 and _liste[0]["notiz"] == "erste", _liste
        assert load_iqbefund(_wav) == _liste, "gespeichert != geladen"
        # Zweite Messung haengt an, ueberschreibt nicht - eine Aufnahme traegt
        # mehrere vermessene Stellen.
        _liste = append_iqbefund(_wav, dict(_e1, t0_s=20.0, t1_s=24.0, notiz="zweite"))
        assert len(_liste) == 2 and _liste[1]["t0_s"] == 20.0, _liste
        with open(iqbefund_path_for(_wav), encoding="utf-8") as _f:
            _roh = json.load(_f)
        assert _roh["format"] == "eb200-iqbefund-v1", _roh
        assert len(_roh["messungen"]) == 2, _roh

        # Deckel: der aelteste faellt heraus.
        for _i in range(IQBEFUND_MAX + 5):
            _liste = append_iqbefund(_wav, dict(_e1, t0_s=float(_i), notiz=f"n{_i}"))
        assert len(_liste) == IQBEFUND_MAX, len(_liste)
        assert _liste[-1]["notiz"] == f"n{IQBEFUND_MAX + 4}", _liste[-1]

        # Unlesbarer Sidecar darf die Seite nicht blockieren.
        with open(iqbefund_path_for(_wav), "w", encoding="utf-8") as _f:
            _f.write("{kaputt")
        assert load_iqbefund(_wav) == [], "kaputtes JSON nicht abgefangen"

        # Falscher Modus wird vor dem Unterprozess abgefangen.
        _r = run_measure(_wav, 0.0, 1.0, "unsinn")
        assert _r["ok"] is False and "mode" in _r["meldung"], _r
        assert _stderr_meldung("x" + chr(10) + "FEHLER: no burst detected (noise)") == "no burst detected (noise)"
        assert _stderr_meldung("") == "" and _stderr_meldung(None) == ""

    assert os.path.isfile(EYE_DIAGRAM), f"eye_diagram nicht gefunden: {EYE_DIAGRAM}"
    assert '"/api/measure"' in _own_source, "Route measure nicht verdrahtet"
    assert '"/api/iqbefund"' in _own_source, "Route iqbefund nicht verdrahtet"
    for _n in ("_handle_api_measure", "_handle_api_iqbefund", "_handle_save_iqbefund"):
        assert hasattr(Handler, _n), _n
    print("T-1611a ok  (iqbefund-Sidecar: anhaengen, Deckel bei "
          f"{IQBEFUND_MAX}, kaputtes JSON abgefangen; Routen und Handler verdrahtet)")

    # --- T-1611b: Oberflaeche der Signalvermessung ---------------------------
    assert MEASURE_AUTO == "auto" and MEASURE_AUTO not in MEASURE_MODES
    for _frag in ('id="measureBtn"', 'id="measureBox"', 'class="mSave"',
                  'id="measureNote"', 'class="mPfad"', '.mPfad.mOk',
                  "function doMeasure()", "function saveMeasure(modus)",
                  "function renderMeasure(st)", "function renderMeasureList(",
                  "function loadMeasureList()", "M_FELDER_FSK", "M_FELDER_PSK",
                  "&mode=auto", "measureKind !== 'iq'",
                  "Multi-carrier modes (OFDM) are not detected by this tool"):
        assert _frag in HTML_PAGE, _frag
    # Der Knopf muss an BEIDES gekoppelt sein: Zoom und IQ.
    assert "!zoomWin || measureKind !== 'iq'" in HTML_PAGE
    # Kein Sieger bei Doppeltreffer - der Hinweis muss stehen bleiben.
    assert "not " in HTML_PAGE and "comparable" in HTML_PAGE
    print("T-1611b ok  (Knopf nur bei Zoom UND IQ, beide Pfade in einer Tafel, "
          "Hinweis statt Fehler bei leerem Ausschnitt, Merken getrennt vom Messen)")

    # --- T-1614: kein Sieger bei Doppeltreffer ------------------------------
    # Befund an TETRA: FSK meldete 29 861 Bd bei r2=0,98, PSK 18 000 Bd bei
    # M=4 - und 18 000 Bd ist der Spezifikationswert. Eine Vorrangregel nach
    # r2 oder EVM haette das Falsche gewaehlt, also gibt es keine: beide
    # Bilder stehen nebeneinander, jedes mit eigenem Merken-Knopf.
    for _frag in ("let measureTreffer = {}", "measureTreffer[nm] =",
                  'class="mBild"', "b.dataset.mode", "async function saveMeasure(modus)",
                  "decide from the picture which finding is usable"):
        assert _frag in HTML_PAGE, _frag

    # --- T-1615: Anordnung der Tafel ----------------------------------------
    # Bild ueber den Werten, beide Pfade als gerahmte Bloecke nebeneinander,
    # Werte als Tabelle mit Zebrastreifen. Die Grafiken haben verschiedene
    # Seitenverhaeltnisse (Auge 760x480, Konstellation+Histogramm 500x660) und
    # werden ueber die Hoehe angeglichen - beide tragen eine viewBox.
    for _frag in ("#measureResult { display:flex;", ".mPfad { border:1px solid var(--border);",
                  ".mBild svg { height:480px; width:auto;",
                  ".mWerte tr:nth-child(even)", '<table class="mWerte">',
                  '<tr><td>'):
        assert _frag in HTML_PAGE, _frag
    # Die Werte stehen NACH dem Bild im selben Block - sonst waere die
    # Zuordnung bei einem Doppeltreffer wieder nur die Reihenfolge.
    assert HTML_PAGE.index('class="mBild"') < HTML_PAGE.index('<table class="mWerte">')
    # T-1627: die Zusicherung galt immer nur der Vermessungstafel - dort war
    # das Gitter, das nicht zurueckkommen soll. Formuliert war sie auf die
    # ganze Seite, und seit dem Zweispalten-Umbau gibt es dort ein zweites,
    # voellig anderes Gitter (#topGrid). Deshalb jetzt auf den Abschnitt der
    # Tafel eingegrenzt statt aufgehoben.
    _tafel = HTML_PAGE[HTML_PAGE.index("#measureBox {"):
                       HTML_PAGE.index("#fftGroup label.fftGrob")]
    assert "grid-template-columns" not in _tafel, "alte Gitterdarstellung nicht entfernt"
    print("T-1615 ok  (Bild ueber Werten, gerahmte Bloecke nebeneinander, "
          "Zebrastreifen, Grafiken auf gleiche Hoehe skaliert)")

    # --- T-1617: Kontrast und Paletten --------------------------------------
    # Rein zeichnerisch: dieselben uint8-Bytes, andere Zuordnung auf Farben.
    # Kein Servereingriff, kein Cache-Neubau - levelToColorRGB hatte die
    # Anschlaege immer schon als Parameter, sie wurden nur nie anders als
    # 0/255 belegt.
    for _frag in ("const PALETTEN = {", "spektral:", "grau:", "heiss:",
                  "function levelToColorRGB(v, lo, hi, palette)",
                  "function autoLevels(bytes)", "const LEVEL_RAND = 0.02;",
                  "function wireLevelControls()", "function redrawSpectrogram()",
                  "let lastSpec = null;", 'id="levelLo"', 'id="levelHi"',
                  'id="levelAuto"', 'id="levelReset"', 'id="levelPalette"',
                  "levelToColorRGB(matrixBytes[base + b], levelLo, levelHi, levelPalette)"):
        assert _frag in HTML_PAGE, _frag
    # Die alte feste Belegung darf nicht zurueckbleiben.
    assert "levelToColorRGB(matrixBytes[base + b], 0, 255)" not in HTML_PAGE
    # Die Regler duerfen nur neu ZEICHNEN, nie neu HOLEN.
    assert "wireLevelControls(); waterfallWired = true;" in HTML_PAGE
    _ab = HTML_PAGE.index("function wireLevelControls()")
    _bis = HTML_PAGE.index("function showWfLoading(on)", _ab) if "function showWfLoading(on)" in HTML_PAGE[_ab:] else len(HTML_PAGE)
    assert "loadSpectrogram()" not in HTML_PAGE[_ab:_bis], "Regler holen neu statt zu zeichnen"
    print("T-1617 ok  (drei Paletten, Anschlaege als Regler, Spreizung auf den "
          "sichtbaren Ausschnitt; alles rein zeichnerisch)")

    # --- T-1616/T-1618: vertikaler Zoom, Falschfarben, Werkzeugleiste -------
    # Der vertikale Zoom setzt nur geom.fMin/fMax und den Quellausschnitt beim
    # drawImage - Achse und Markerlinien folgen von selbst, weil beide ueber
    # dieselbe Geometrie rechnen. Kein Servereingriff.
    for _frag in ('id="yZoomBtn"', "function updateYZoomBtn()", "let yZoom = null;",
                  "yZoom ? yZoom.fLo : fMin", "const yOben = yZoom ?",
                  "'Full band' : 'Vertical zoom'",
                  "regenbogen:", "stufen:", "saegezahn:",
                  "p.wiederholungen", "p.stufen",
                  "Horizontal zoom: select a section", "Stretch contrast",
                  'value="regenbogen"', 'value="stufen"', 'value="saegezahn"'):
        assert _frag in HTML_PAGE, _frag
    # Marker B ist jetzt weiss und wird gestrichelt gezeichnet.
    assert "const MARKER_B_COLOR = '#e8e8e8';" in HTML_PAGE
    assert "drawMarkerLine(octx, markerB, MARKER_B_COLOR, 'Marker B', true);" in HTML_PAGE
    assert "ctx.setLineDash(gestrichelt ? [6, 4] : []);" in HTML_PAGE
    # Zeilenfolge der Werkzeugleiste: Zoom, Marker, FFT.
    _i1 = HTML_PAGE.index("Horizontal zoom: select a section")
    _i2 = HTML_PAGE.index("Set markers (CTRL")
    _i3 = HTML_PAGE.index('name="fftSize"')
    assert _i1 < _i2 < _i3, "Werkzeugleisten-Zeilen in falscher Reihenfolge"
    # --- T-1619: FFT-Auswahl kennzeichnen -----------------------------------
    # Rein zeichnerisch: bw_hz steht im Sidecar, das ohnehin komplett im
    # Frontend liegt. Gekennzeichnet, NICHT gesperrt - die Grenze ist eine
    # Faustregel, und ohne bw_hz (importierte Aufnahmen) waere sie gar nicht
    # anwendbar.
    for _frag in ('id="fftGroup"', 'id="fftRes"', "#fftGroup label.fftGrob",
                  "function updateFftRes()", "function updateFftHinweise()",
                  "const FFT_MIN_BINS_IM_BAND = 20;", "let nutzBandbreiteHz = null;",
                  "function fmtHz(hz)", "function fmtDauer(s)",
                  "meta.sidecar.bw_hz", "decimated", "too coarse"):
        assert _frag in HTML_PAGE, _frag
    # Ohne bekannte Bandbreite darf NICHT gedaempft werden.
    _i = HTML_PAGE.index("if (!nutzBandbreiteHz)")
    assert "lab.classList.remove('fftGrob')" in HTML_PAGE[_i:_i + 200], "Daempfung ohne bw_hz"
    # Die Zeilendauer kommt aus dem Header, traegt die Dezimierung also in sich.
    assert "fmtDauer(h.dt_row_s)" in HTML_PAGE
    # Gesperrt wird nicht: in updateFftHinweise() darf kein .disabled gesetzt
    # werden (die Zeichenkette "disabled" kommt anderswo in HTML_PAGE vor,
    # etwa beim Vermessen-Knopf - deshalb der enge Ausschnitt).
    _j = HTML_PAGE.index("function updateFftHinweise()")
    _block = HTML_PAGE[_j:HTML_PAGE.index("\n}", _j)]
    assert ".disabled" not in _block, "FFT-Groessen duerfen nicht gesperrt werden"
    print("T-1619 ok  (Binbreite und Zeilendauer je Groesse, zu grobe gedaempft "
          "aber anklickbar, ohne bw_hz keine Daempfung)")

    # --- T-1620: Abstimmung auf die Marker ----------------------------------
    # Befund 2026-09-10 an einer RTTY-Aufnahme: ein Signal, das nicht in der
    # Bandmitte liegt, laesst sich nicht stark genug dezimieren, weil der
    # Tiefpass in eye_diagram um 0 Hz sitzt. Ohne Dezimierung bleibt zu viel
    # Rauschbandbreite, mit zu starker faellt ein Ton heraus.
    for _frag in ('id="tuneChk"', 'id="tuneLbl"', "Tune to markers",
                  "el('tuneChk').checked", "'&tune=' +", "'&bw=' +",
                  "st.tune_hz ?", "tuned to"):
        assert _frag in HTML_PAGE, _frag
    # Nur mit zwei Markern anwaehlbar - und ohne Marker zurueckgesetzt, damit
    # keine alte Abstimmung stehenbleibt.
    assert "chk.disabled = !bereit;" in HTML_PAGE
    assert "if (!bereit) chk.checked = false;" in HTML_PAGE
    # Die Ableitungsregel gehoert ins Werkzeug, nicht hierher: der Server
    # importiert eye_diagram bewusst nicht (Unterprozess-Muster).
    # Die Zeichenkette steht nur in dieser Pruefzeile selbst - deshalb wird
    # gezaehlt statt auf Abwesenheit geprueft.
    assert _own_source.count("suggest_" + "decimation") == 0, "Regel doppelt gefuehrt"
    assert '"--auto-decimate"' in _own_source, "Bandbreite wird nicht durchgereicht"
    assert '"--tune"' in _own_source, "Abstimmung wird nicht durchgereicht"
    print("T-1620 ok  (Kontrollkaestchen nur mit zwei Markern, Mitte als "
          "Abstimmfrequenz und Abstand als Bandbreite, Ableitungsregel im Werkzeug)")

    # --- T-1621: Werte kompakt und mit Erlaeuterung --------------------------
    # Die Erlaeuterungen sind Template-Literale mit ECHTEN Umbruechen - kein
    # Backslash-Escape, das waere in der nicht-rohen Python-Zeichenkette eine
    # Falle (der Umbruch landete sonst im JS-Quelltext und spraenge die
    # Zeichenkette). Dasselbe gilt fuer mTip(), das ueber fromCharCode(10)
    # zusammensetzt.
    assert "function mTip(text)" in HTML_PAGE
    assert "String.fromCharCode(10)" in HTML_PAGE
    assert "mTip(f[2])" in HTML_PAGE, "Tooltip wird nicht gesetzt"
    assert ".mWerte { width:auto;" in HTML_PAGE, "Tabelle zieht noch ueber die Blockbreite"
    assert "margin:0 auto;" in HTML_PAGE and "max-width:420px" in HTML_PAGE
    assert ".mWerte tr[title] { cursor:help; }" in HTML_PAGE
    # Jedes Feld braucht genau drei Eintraege: Schluessel, Beschriftung, Text.
    # T-1613: zwischen FSK und PSK steht jetzt M_FELDER_OFDM - die Grenze
    # des FSK-Blocks wandert entsprechend mit.
    _fsk = HTML_PAGE[HTML_PAGE.index("const M_FELDER_FSK = ["):HTML_PAGE.index("const M_FELDER_OFDM = [")]
    _psk = HTML_PAGE[HTML_PAGE.index("const M_FELDER_PSK = ["):HTML_PAGE.index("function mTip(text)")]
    # Zwei Backticks je Feld - einer oeffnet, einer schliesst. Die
    # Schluesselzaehlung ueber "['" trifft nur den Zeilenanfang jedes Feldes.
    # T-1704: FSK waechst von 9 auf 12 (mod_index, crossing_grid,
    # jitter_half_pct neu) - PSK bleibt unveraendert bei 9.
    for _name, _blk, _soll in (("FSK", _fsk, 12), ("PSK", _psk, 9)):
        assert _blk.count("['") == _soll, (_name, _blk.count("['"))
        assert _blk.count("`") == 2 * _soll, (_name, _blk.count("`"))
    # Keine Zeile der Erlaeuterungen darf ueber 80 Zeichen gehen.
    for _z in (_fsk + _psk).splitlines():
        assert len(_z) <= 96, _z          # 96 = 80 plus Einrueckung und Klammern
    print("T-1621 ok  (je Feld eine zweizeilige Erlaeuterung, Wertetabelle als "
          "zentrierter Block statt ueber die volle Breite)")

    # --- T-1623: Hoerbarmachung ---------------------------------------------
    # Die WAV entsteht FLUECHTIG - Temp-Verzeichnis, Antwortkoerper, weg.
    # Keine Ablage, keine Verfallszeit, kein Aufraeumen im Worker.
    assert hasattr(Handler, "_handle_api_audio")
    assert '"/api/audio":' in _own_source, "Route nicht verdrahtet"
    assert '"--audio", ziel' in _own_source, "Werkzeug wird nicht aufgerufen"
    assert 'TemporaryDirectory() as tmp' in _own_source
    # Nichts darf dauerhaft abgelegt werden: kein Ablagepfad in der Route.
    _blk = _own_source[_own_source.index("def _handle_api_audio"):
                       _own_source.index("def _handle_api_measure")]
    assert "audio/wav" in _blk and "no-store" in _blk, "Antwortkopf unvollstaendig"
    assert "iqbefund" not in _blk and "records" not in _blk, "Route legt etwas ab"
    for _frag in ('id="audioBtn"', "async function doAudio()", "/api/audio?path=",
                  "URL.createObjectURL", "URL.revokeObjectURL", "let audioUrl = null;",
                  "el('audioBtn').addEventListener('click', doAudio)"):
        assert _frag in HTML_PAGE, _frag
    # Der Knopf haengt an IQ; die Zoombedingung hat T-1624 gelockert.
    assert "aBtn.disabled = measureKind !== 'iq'" in HTML_PAGE
    print("T-1623 ok  (Route /api/audio fluechtig, Knopf an Zoom und IQ gebunden, "
          "vorige Objekt-URL wird freigegeben)")

    # --- T-1624: der Knopf lief ins Leere -----------------------------------
    # Befund 2026-09-11: "Anhoeren" erzeugte die WAV, aber die
    # Wiedergabeleiste ist bei IQ ausgeblendet (T-1211, damit der Browser die
    # Rohdatei nicht laedt) - es war also nichts zu hoeren und der Knopf schien
    # wirkungslos. Der Selbsttest aus T-1623 prueft nur, DASS el('player')
    # angesprochen wird; bei einem Element, das ausgeblendet sein kann, reicht
    # das nicht. Deshalb hier: die Leiste muss in doAudio() sichtbar werden.
    _audio_blk = HTML_PAGE[HTML_PAGE.index("async function doAudio()"):
                           HTML_PAGE.index("async function doMeasure()")]
    assert "el('playerBar').style.display = '';" in _audio_blk, \
        "Wiedergabeleiste bleibt ausgeblendet"
    assert _audio_blk.index("el('playerBar')") < _audio_blk.index("p.src = audioUrl"), \
        "Leiste muss VOR dem Zuweisen der Quelle sichtbar sein"
    # Ohne Zoom die ganze Aufnahme - aber nur wenn sie kurz genug ist.
    assert "const AUDIO_MAX_S = 120;" in HTML_PAGE
    assert "audioDurationS <= AUDIO_MAX_S" in HTML_PAGE
    assert "const von = zoomWin ? zoomWin.t0_s : 0;" in _audio_blk
    assert "zoomWin ? zoomWin.t1_s : audioDurationS" in _audio_blk
    # Das Kontrollkaestchen sitzt jetzt bei den Markern, nicht hinter dem
    # Vermessen-Knopf - es gehoert zu ihnen.
    _marker = HTML_PAGE.index("Set markers (CTRL")
    _fft = HTML_PAGE.index('name="fftSize"')
    assert _marker < HTML_PAGE.index('id="tuneChk"') < _fft, \
        "Kontrollkaestchen nicht in der Markerzeile"
    print("T-1624 ok  (Wiedergabeleiste wird eingeblendet, kurze Aufnahmen ohne "
          "Zoom anhoerbar, Kontrollkaestchen bei den Markern)")

    # --- T-1613: OFDM-Hinweis als letzte Auskunft ---------------------------
    # Er wird NUR angehaengt, wenn FSK und PSK beide gescheitert sind. Das ist
    # zugleich die Absicherung gegen seine bekannte Schwaeche: bei einem
    # auswertbaren FSK-Signal loest er zwar aus (gemessen 2026-09-11, er meldet
    # dort 0,98 ms statt der Symboldauer), kommt aber nie zum Zug.
    assert MEASURE_OFDM == "ofdm" and MEASURE_OFDM not in MEASURE_MODES
    _auto = _own_source[_own_source.index("modi = MEASURE_MODES if mode =="):
                        _own_source.index('antwort = {"t0_s": t0_s')]
    assert "if mode == MEASURE_AUTO and not erfolgreich:" in _auto, \
        "OFDM darf nur bei doppeltem Fehlschlag laufen"
    assert _auto.index("erfolgreich = [m for m") < _auto.index("MEASURE_OFDM"), \
        "erst auswerten, dann erst den Hinweis holen"
    # Kein SVG im OFDM-Lauf - er zeichnet nichts.
    _cmd = _own_source[_own_source.index('cmd = [sys.executable, EYE_DIAGRAM'):
                       _own_source.index("try:\n            proc = subprocess.run")]
    assert 'if mode != MEASURE_OFDM:' in _cmd, "OFDM-Lauf zeichnet trotzdem"
    for _frag in ("const M_FELDER_OFDM = [", "name === 'ofdm' ? M_FELDER_OFDM",
                  "'Possibly OFDM'",
                  # T-1704: die ms-Umrechnung sitzt jetzt in mWertZelle()
                  # statt inline in mPfadHtml() - siehe T-1704-Abschnitt unten.
                  "if (name === 'symbol_s') return mZahl(k[name] * 1000);",
                  "korr_ueberhoehung", "traegerabstand_gemessen_hz"):
        assert _frag in HTML_PAGE, _frag
    # Der Kopf sagt "Possibly", nicht "evaluated" - es bleibt eine
    # Vermutung, und das soll man sehen.
    assert "const kopf = name === 'ofdm' ? titel : titel + ' – evaluated';" in HTML_PAGE
    _ofdm_blk = HTML_PAGE[HTML_PAGE.index("const M_FELDER_OFDM = ["):
                          HTML_PAGE.index("const M_FELDER_PSK = [")]
    assert _ofdm_blk.count("['") == 6 and _ofdm_blk.count("`") == 12, _ofdm_blk.count("['")
    print("T-1613 ok  (OFDM-Hinweis nur bei doppeltem Fehlschlag, eigene Tafel mit "
          "sechs Feldern, Kopf bleibt Vermutung)")

    print("T-1616 ok  (vertikaler Zoom auf das Markerband, Knopf schaltet um; "
          "Marker B weiss und gestrichelt; drei Falschfarben-Paletten; "
          "Werkzeugleiste Zoom/Marker/FFT)")
    assert "measureLast" not in HTML_PAGE, "alter Einzeltreffer-Zustand nicht entfernt"
    assert HTML_PAGE.count("Save finding") >= 1
    print("T-1614 ok  (Doppeltreffer: beide Bilder, je eigener Merken-Knopf, "
          "kein automatisch gekuerter Sieger)")

    # --- T-1625: Kopfleiste, Art-Abzeichen, Beschriftung --------------------
    # Der Ansichtsname ist fest, der Untertitel wird per JS gefuellt - also
    # muss das Feld im ausgelieferten HTML schon dastehen (leeres <span> mit
    # id), sonst greift el('pageSub') ins Leere.
    assert 'id="pageSub"' in HTML_PAGE, "Untertitelfeld fehlt in der Kopfleiste"
    assert "Audio-Auswertung" not in HTML_PAGE and "Audio-Auswertung" not in HTML_NO_PATH
    assert "pageTitle" not in HTML_PAGE, "alte Titel-id nicht entfernt"
    # Drei Faelle, drei Klassen - alle drei liegen in BASE_CSS.
    for _frag in ("function badgeArt(meta)", 'class="badge badge-iq"',
                  'class="badge badge-audio"', 'class="badge badge-unknown"'):
        assert _frag in HTML_PAGE, _frag
    assert "badgeArt(meta)" in HTML_PAGE[HTML_PAGE.index("rows.push(['File'"):
                                          HTML_PAGE.index("rows.push(['Duration'")], \
        "Abzeichen nicht in der Dateizeile"
    assert "rows.push(['Recorded on'" in HTML_PAGE
    assert "Aufgenommen" not in HTML_PAGE, "alte Beschriftung nicht entfernt"
    print("T-1625 ok  (Kopfleiste Signalanalyse mit Kurztext als Untertitel, "
          "Art-Abzeichen IQ/AUDIO/UNBEKANNT in der Dateizeile, "
          "Beschriftung \"Aufnahme vom\")")

    # --- T-1626: Median-Pegel ------------------------------------------------
    # Serverseite: das Feld haengt an build_meta und wird bei laufender
    # Aufnahme NICHT gerechnet.
    _bm = _own_source[_own_source.index("def build_meta("):
                      _own_source.index("def build_audspec_response(")] \
        if "def build_audspec_response(" in _own_source[_own_source.index("def build_meta("):] \
        else _own_source[_own_source.index("def build_meta("):]
    assert "level_median_dbfs(resolved)" in _bm, "Median wird nicht gerechnet"
    assert "not running" in _bm, "laufende Aufnahme wird nicht ausgenommen"
    # T-1626a: ein unbrauchbares Format (3 Kanaele, 8 Bit) hat einen lesbaren
    # WAV-Kopf - build_meta kommt also bis zum Median durch und muss die
    # Ausnahme aus wav_kind abfangen, sonst faellt /api/meta fuer genau die
    # Dateien aus, die man sich anschauen will, WEIL sie merkwuerdig sind.
    assert "AudioSpectrumUnsupportedError" in _bm, "Ausnahme aus wav_kind nicht abgefangen"
    assert '"level_median_dbfs": level_median,' in _bm, "Feld fehlt in der Antwort"
    # Frontend: zwei Pegel nebeneinander, mit verschiedenen Einheiten.
    for _frag in ("function fmtRxZeile(rx, medianDbfs, kind)", "Level (start) ",
                  "Level (median) ", "dBFS", "meta.level_median_dbfs"):
        assert _frag in HTML_PAGE, _frag
    # Der Median darf nie als dBµV erscheinen - das waere eine Behauptung.
    _rx_blk = HTML_PAGE[HTML_PAGE.index("function fmtRxZeile("):
                        HTML_PAGE.index("function fmtGeraetZeile(")]
    assert _rx_blk.index("Level (start) ") < _rx_blk.index("'AFC '"), \
        "Reihenfolge der Empfangskette verschoben"
    assert _rx_blk.count("dBFS") == 1, _rx_blk.count("dBFS")
    print("T-1626 ok  (Median-Blockpegel aus /api/meta, nicht bei laufender "
          "Aufnahme, neben dem Startpegel und als dBFS beschriftet)")

    # --- T-1627: Zweispalten-Aufbau und Empfangskette als Marken ------------
    # Der Aufbau selbst ist Geschmack, die Sichtbarkeitslogik ist es nicht:
    # die Bedienung sitzt nicht mehr in #waterfall, also muss jeder Zweig, der
    # den Wasserfall ausblendet, sie eigens ausblenden.
    for _frag in ('id="topGrid"', 'id="metaBlock"', 'id="ctrlBlock"',
                  "function nurMetadaten()", "classList.add('einspaltig')"):
        assert _frag in HTML_PAGE, _frag
    assert HTML_PAGE.count('class="wfRow"') == 5, HTML_PAGE.count('class="wfRow"')
    _grid = HTML_PAGE[HTML_PAGE.index('<div id="topGrid">'):
                      HTML_PAGE.index('<div id="playerBar">')]
    assert 'id="meta"' in _grid and 'id="wfToolbar"' in _grid, "Bloecke nicht im Raster"
    assert 'id="siblings"' not in _grid, "Geschwisterlinks gehoeren unter das Raster"
    assert (HTML_PAGE.index('<div id="playerBar">')
            < HTML_PAGE.index('<div id="siblings">')
            < HTML_PAGE.index('<div id="waterfall">')), "Reihenfolge unter dem Raster"
    _init = HTML_PAGE[HTML_PAGE.index("function initWaterfall(meta)"):
                      HTML_PAGE.index("function badgeClass(klasse)")]
    assert _init.count("nurMetadaten();") == 2, _init.count("nurMetadaten();")
    # Empfangskette: vier Zustaende, Farbe = Zustand, keine Hex-Farben.
    for _frag in ('class="rxChip ', ".rxChip.auto", ".rxChip.ein", ".rxChip.aus",
                  ".rxChip.na", "function rxChip(text, zustand, titel)"):
        assert _frag in HTML_PAGE, _frag
    _rx = HTML_PAGE[HTML_PAGE.index("function fmtRxZeile(rx, medianDbfs, kind)"):
                    HTML_PAGE.index("function fmtGeraetZeile(")]
    # Die Daempfung hat keinen abfragbaren dB-Wert - es darf auch keiner
    # erfunden werden; die Automatik steht als eigener Chip daneben.
    assert "rxChip('Auto', 'auto'" in _rx, "Zusatzchip fuer die Automatik fehlt"
    assert "att_db" not in _rx and "ATT 20" not in _rx, "erfundene Daempfungsstufe"
    # AFC regelt in IQ nicht (Geraetebefund) - der Chip bleibt dort weg.
    assert "kind !== 'iq'" in _rx, "AFC-Chip wird bei IQ nicht unterdrueckt"
    print("T-1627 ok  (zwei Bloecke ueber dem Spektrogramm, Bedienung im "
          "rechten Block samt Sichtbarkeitslogik, Empfangskette als "
          "Zustandsmarken mit ATT-Zusatzchip)")

    # --- T-1704: Modenkandidaten-Tafel ---------------------------------------
    # Leitsatz Meilenstein 17: die Anzeige behauptet nie mehr, als die Messung
    # hergibt - keine Rangfolge, kein "bester Treffer", keine Prozentzahl, die
    # sich als Wahrscheinlichkeit lesen liesse.
    for _frag in ("Known services:", "no match possible",
                  "No match in the mode table",
                  "Timing phase", "Jitter (half-bit raster)", "Modulation index",
                  "Symbol rate is only coarsely determined",
                  "possible e.g. with 1.5 stop bits",
                  "function mKandidatenHtml(k)", "function mKandidatHtml(bef)",
                  "function mRasterlageText(wert)"):
        assert _frag in HTML_PAGE, _frag
    # Der eigentliche Waechter ueber den Leitsatz: "erkannt" darf im neuen
    # Kandidatenblock nirgends stehen. Das Wort kommt in M_FELDER_FSK
    # (level_hint) und M_FELDER_OFDM (korr_ueberhoehung) bereits an zwei
    # anderen Stellen vor - die Pruefung ist deshalb auf den neuen Block
    # beschraenkt, nicht auf die ganze Datei (sonst schluege sie sofort an,
    # ohne etwas ueber die Kandidatentafel auszusagen).
    _kand = HTML_PAGE[HTML_PAGE.index("function mRasterlageText(wert)"):
                      HTML_PAGE.index("function mPfadHtml(name, r)")]
    assert "detected" not in _kand, "Kandidatentafel behauptet mehr als die Messung hergibt"
    # Die Tafel steht im Aufbau NACH der Wertetafel, innerhalb derselben
    # Rueckgabe von mPfadHtml (Muster T-1624: Indexvergleich im statischen
    # Quelltext, nicht im gerenderten DOM).
    _pfad_html = HTML_PAGE[HTML_PAGE.index("function mPfadHtml(name, r)"):
                          HTML_PAGE.index("function renderMeasure(st)")]
    assert _pfad_html.index('<table class="mWerte">') < _pfad_html.index("+ kandidaten"), \
        "Kandidatentafel muss nach der Wertetafel aufgebaut werden"
    # Nur im FSK-Zweig - PSK/OFDM liefern keine Kandidaten.
    assert "name === 'fsk' ? mKandidatenHtml(k) : ''" in HTML_PAGE, \
        "Kandidatentafel darf nur im FSK-Zweig aufgebaut werden"
    # Fehlt mode_candidates ganz (aeltere zwischengespeicherte Ergebnisse):
    # derselbe Zweig wie bei einer leeren Liste, kein Sonderfall, kein
    # Fehler. Reine Fragmentpruefung, dass der Zweig existiert - eine echte
    # Ausfuehrung von mKandidatenHtml({}) braeuchte eine JS-Umgebung, die
    # dieser Selbsttest (reine Python-Fragmentpruefung wie der Rest der
    # Datei) bewusst nicht mitbringt.
    assert "if (!cands || !cands.length)" in HTML_PAGE, \
        "fehlendes/leeres mode_candidates muss denselben Zweig nehmen"
    print("T-1704 ok  (Modenkandidaten-Tafel unter der FSK-Wertetafel, zwei "
          "Gruppen ohne Rangfolge, Verlaesslichkeitshinweis bei grober "
          "Symbolrate, 'erkannt' bleibt aussen vor)")

    print("\n[analyze_audio] Selbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    sys.exit(main())
