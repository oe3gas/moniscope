#!/usr/bin/env python3
"""
eb200_core/sigmf_export.py

SigMF-Export fuer IQ-Aufnahmen (T-921). Erzeugt aus einer bestehenden
IQ-WAV samt Sidecar (<wav>.json, geschrieben von eb200_core/audio_wav.py)
eine SigMF-Metadatei. Gegenstueck zum bereits vorhandenen SigMF-LESER in
tools/iq_import.py (read_sigmf_meta) - damit ist der Kreis zu: was wir
einlesen koennen, koennen wir jetzt auch herausgeben.

Zwei Betriebsarten, beide mit demselben Metadateninhalt:

  NCD (Vorgabe, "Non-Conforming Dataset"). Es entsteht NUR die Datei
  <name>.sigmf-meta neben der WAV. Sie verweist ueber core:dataset auf
  die WAV und nennt in core:header_bytes den Byte-Versatz der Nutzdaten.
  Kein Byte Nutzdaten wird kopiert - eine 4-GB-Aufnahme bleibt 4 GB.
  Die Spezifikation nennt so eine Aufnahme schema-valide, aber
  ausdruecklich NICHT "SigMF Compliant": ein Werkzeug, das stumpf
  <name>.sigmf-data oeffnet, findet nichts.

  Datenexport (with_data=True). Zusaetzlich entsteht <name>.sigmf-data
  mit den reinen Sample-Bytes. Das ist ein reiner Kopiervorgang ab dem
  Datenversatz: unser 16-Bit-Stereo-LE-WAV mit I = Kanal 0 (links) und
  Q = Kanal 1 (rechts) IST bereits das, was SigMF "ci16_le" nennt
  (interleaved I,Q,I,Q als Little-Endian-int16). Es wird nichts
  gerechnet und nichts umsortiert. Preis ist der doppelte Platzbedarf.

Drei Festlegungen, die das Modul praegen:

  1. NUR IQ. Demoduliertes Mono-Audio waere als "ri16_le" zwar
     darstellbar, in SigMF aber zweckentfremdet. Fehlt is_iq im Sidecar
     oder steht es auf false, ist das ein ValueError - nicht eine
     stillschweigend andere Ausgabe. Die Pruefung sitzt HIER und nicht
     nur in der Oberflaeche.

  2. KEIN GERAETE- UND KEIN HTTP-ZUGRIFF. Wie der Rest von eb200_core:
     Eingabe sind zwei Dateien, Ausgabe sind ein oder zwei Dateien.
     Damit ist das Modul ohne Hardware pruefbar
     ("python -m eb200_core.sigmf_export --selftest").

  3. DIE METADATEI IST REGENERIERBAR. Sie enthaelt keinen Zustand, der
     nicht in Sidecar und WAV steht, und wird deshalb still
     ueberschrieben - dieselbe Haltung wie bei <name>.thumb.json und
     den .audspec-Caches. Kein Nachfragen, kein Wartungsschalter.

Alles, was der Sidecar fuehrt und SigMF im Kern nicht kennt -
Demodulationsart, Bandbreite, AUDIO_REMOTE_MODE und vor allem der
rx-Block aus eb200_core/rx_snapshot.py (T-917), an dem jede spaetere
Pegelaussage haengt -, steht unter dem eigenen Namensraum "moniscope:".
SigMF verlangt dafuer einen Eintrag in core:extensions; er steht dort
mit optional=true, weil ein fremdes Werkzeug die Aufnahme auch ohne
Kenntnis des Namensraums vollstaendig auswerten koennen muss. Felder
ohne Wert entfallen dabei ganz, statt als null dazustehen (T-921c) -
die Unterscheidung "Feld fehlt" gegen "erhoben, aber leer" bleibt dem
Sidecar vorbehalten, aus dem diese Datei jederzeit neu entsteht.

Feldnamen sind ASCII und sprachneutral (Projektregel fuer persistierte
Werte).
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile

# Fassung der SigMF-Spezifikation, gegen die diese Metadaten gebaut sind.
# Bewusst eine Konstante an einer Stelle: die Spezifikation zaehlt in
# Nebenversionen aufwaerts kompatibel, "1.2.0" ist der Stand, den die
# verbreiteten Werkzeuge sicher validieren.
SIGMF_VERSION = "1.2.0"

# Eigener Namensraum fuer alles, was SigMF im Kern nicht kennt.
MONISCOPE_NS = "moniscope"
MONISCOPE_EXT_VERSION = "1.0.0"

# Unser einziger Ausgabe-Datentyp: komplexe int16-Paare, Little-Endian,
# interleaved I,Q. Entspricht 1:1 dem Inhalt einer IQ-WAV dieses Projekts.
SIGMF_DATATYPE_IQ = "ci16_le"

META_SUFFIX = ".sigmf-meta"
DATA_SUFFIX = ".sigmf-data"

# Kopierblock fuer den Datenexport. 4 MiB: gross genug, dass der
# Kopiervorgang plattengebunden bleibt, klein genug, dass der
# Speicherbedarf unabhaengig von der Aufnahmelaenge konstant ist
# (dieselbe Haltung wie der Streaming-Umbau in audio_spectrum.py).
COPY_CHUNK_BYTES = 4 * 1024 * 1024


class SigmfRunningError(RuntimeError):
    """
    Die Aufnahme laeuft noch (Sidecar running=true), Export abgelehnt
    (T-922a). Eigene Klasse statt ValueError, weil der Aufrufer die
    beiden Faelle unterscheiden muss: "laeuft noch" ist ein spaeter
    wiederholbarer Zustand, "keine IQ-Aufnahme" ist es nicht.
    eb200_core/recording_index.py uebersetzt das in
    RecordingLockedError und damit in HTTP 409, genau wie beim Loeschen.
    """


def wav_data_span(path: str):
    """
    Liefert (offset, declared_length) des data-Chunks einer RIFF/WAVE-
    Datei: Byte-Versatz der ersten Nutzdaten und die im Header
    angegebene Laenge.

    Bewusst ein eigener RIFF-Leser statt des wave-Moduls: wave gibt den
    Datenversatz nicht heraus, und genau der ist als core:header_bytes
    die ganze Pointe der NCD-Variante. Der Versatz wird GELESEN und
    nicht auf 44 geraten - unsere eigenen Aufnahmen tragen einen
    kanonischen 44-Byte-Kopf, importierte Fremdaufnahmen
    (tools/iq_import.py) koennen zusaetzliche Chunks wie LIST oder fact
    davor fuehren.
    """
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"not a RIFF/WAVE file: {path}")
        while True:
            chunk_head = f.read(8)
            if len(chunk_head) < 8:
                raise ValueError(f"RIFF/WAVE ohne data-Chunk: {path}")
            chunk_id, chunk_len = struct.unpack("<4sI", chunk_head)
            if chunk_id == b"data":
                return f.tell(), chunk_len
            # RIFF polstert Chunks auf gerade Laenge.
            f.seek(chunk_len + (chunk_len & 1), os.SEEK_CUR)


def data_span_on_disk(path: str):
    """
    (offset, length, trailing) fuer den data-Chunk, gegen die
    tatsaechliche Dateigroesse geprueft.

    Die Laengenangabe im Header ist die erste Groesse, die bei einer
    abgebrochenen Aufnahme nicht mehr stimmt - der Writer traegt sie
    erst beim Schliessen nach. Steht dort 0 oder mehr, als die Datei
    hergibt, gilt der Rest der Datei als Nutzdaten und trailing ist 0:
    lieber ein angebrochenes Sample am Ende als eine Metadatei, die auf
    Daten zeigt, die es nicht gibt.
    """
    offset, declared = wav_data_span(path)
    rest = max(0, os.path.getsize(path) - offset)
    if declared <= 0 or declared > rest:
        return offset, rest, 0
    return offset, declared, rest - declared


def read_sidecar(wav_path: str) -> dict:
    """
    Liest <wav>.json. Fehlt die Datei, ist das ein Abbruch: ohne Sidecar
    sind Mittenfrequenz und Abtastrate unbekannt, und eine SigMF-
    Metadatei ohne core:sample_rate ist keine.
    """
    with open(wav_path + ".json", encoding="utf-8") as f:
        return json.load(f)


def require_iq(sidecar: dict, wav_path: str) -> None:
    """
    Wirft ValueError, wenn die Aufnahme keine verwertbare IQ-Aufnahme
    ist. Siehe Festlegung 1 der Moduldoku: das ist die eigentliche
    Durchsetzung des "nur bei IQ"; die Oberflaeche blendet den Knopf
    zusaetzlich aus, ersetzt diese Pruefung aber nicht.
    """
    if not sidecar.get("is_iq"):
        raise ValueError(f"not an IQ recording (is_iq missing or false): {wav_path}")
    if sidecar.get("channels") != 2:
        raise ValueError(
            f"IQ verlangt 2 Kanaele, Sidecar nennt {sidecar.get('channels')!r}: {wav_path}")
    if sidecar.get("sampwidth_bytes") != 2:
        raise ValueError(
            f"only 16-bit recordings can be exported as {SIGMF_DATATYPE_IQ}, "
            f"sidecar states {sidecar.get('sampwidth_bytes')!r} bytes: {wav_path}")
    if not sidecar.get("sample_rate_hz"):
        raise ValueError(f"Sidecar ohne sample_rate_hz: {wav_path}")


def as_rfc3339(iso_text):
    """
    Zeitstempel des Sidecars -> RFC3339 mit "Z".

    Der Writer schreibt datetime.isoformat() und damit "+00:00"; SigMF
    verlangt fuer core:datetime die Z-Schreibweise. Ein fehlender oder
    leerer Wert bleibt None - das Feld entfaellt dann, statt eine
    erfundene Zeit zu tragen.
    """
    if not iso_text:
        return None
    text = str(iso_text).strip()
    if text.endswith("+00:00"):
        return text[: -len("+00:00")] + "Z"
    return text


def describe_hw(device):
    """
    core:hw ist in SigMF ein Freitext-String, der device-Block des
    Sidecars ist ein Dict. Hier die lesbare Kurzform; die Einzelfelder
    (Seriennummer, Firmware, Optionen) bleiben verlustfrei unter
    moniscope:device erhalten.
    """
    if not isinstance(device, dict):
        return None
    teile = []
    marke = " ".join(str(device[k]).strip()
                     for k in ("manufacturer", "model") if device.get(k))
    if marke:
        teile.append(marke)
    if device.get("serial"):
        teile.append(f"S/N {device['serial']}")
    if device.get("firmware"):
        teile.append(f"FW {device['firmware']}")
    return ", ".join(teile) or None


def build_sigmf_meta(sidecar: dict, *, dataset_name=None,
                     header_bytes: int = 0, trailing_bytes: int = 0) -> dict:
    """
    Baut das SigMF-Metadaten-Dict.

    dataset_name gesetzt -> NCD-Variante: core:dataset nennt die WAV,
    core:header_bytes im Capture-Segment den Datenversatz,
    core:trailing_bytes global den Rest hinter den Nutzdaten.
    dataset_name None -> konformer Export; die Nutzdaten liegen dann
    headerlos in der .sigmf-data, der Versatz ist 0 und beide Felder
    entfallen.

    core:sample_rate ist die KOMPLEXE Abtastrate und damit genau
    sample_rate_hz des Sidecars, ohne Faktor 2: ein WAV-Frame
    (links+rechts) ist ein komplexes Sample. Das ist die Stelle, an der
    sich ein SigMF-Export am leichtesten um den Faktor 2 vertut.
    """
    glob = {
        "core:datatype": SIGMF_DATATYPE_IQ,
        "core:sample_rate": float(sidecar["sample_rate_hz"]),
        "core:version": SIGMF_VERSION,
        "core:recorder": "MoniScope",
        "core:extensions": [
            {"name": MONISCOPE_NS, "version": MONISCOPE_EXT_VERSION, "optional": True},
        ],
    }
    hw = describe_hw(sidecar.get("device"))
    if hw:
        glob["core:hw"] = hw
    if dataset_name:
        glob["core:dataset"] = dataset_name
        glob["core:trailing_bytes"] = int(trailing_bytes)

    # Alles ohne Kern-Entsprechung unter eigenem Namensraum. Die Werte
    # werden NICHT umgerechnet und NICHT umbenannt - sie stehen genau so
    # da wie im Sidecar, damit ein Rueckweg moeglich bleibt.
    #
    # Felder ohne Wert entfallen ganz, statt als null dazustehen
    # (Entscheidung T-921c, Befund aus der Erprobung an einer Aufnahme
    # vom 2026-09-01, die aelter ist als der rx-Block aus T-917).
    # Dieselbe Haltung wie bei core:hw weiter oben. Die Unterscheidung
    # "Feld fehlt" gegen "erhoben, aber leer" bleibt dort, wo sie
    # gebraucht wird: im Sidecar. Ein fehlendes moniscope:rx heisst in
    # dieser Datei schlicht "liegt nicht vor".
    for _ziel, _quelle in (("demod", "mode"),
                           ("bw_hz", "bw_hz"),
                           ("audio_remote_mode", "audio_remote_mode"),
                           ("start_iso", "start_iso"),
                           ("stop_iso", "stop_iso"),
                           ("duration_s", "duration_s"),
                           ("frames_written", "frames_written"),
                           ("rx", "rx"),
                           ("device", "device")):
        _wert = sidecar.get(_quelle)
        if _wert is not None:
            glob[f"{MONISCOPE_NS}:{_ziel}"] = _wert

    capture = {"core:sample_start": 0}
    if sidecar.get("freq_hz") is not None:
        capture["core:frequency"] = float(sidecar["freq_hz"])
    stamp = as_rfc3339(sidecar.get("start_iso"))
    if stamp:
        capture["core:datetime"] = stamp
    if header_bytes:
        capture["core:header_bytes"] = int(header_bytes)

    return {"global": glob, "captures": [capture], "annotations": []}


def _write_json_atomic(path: str, payload: dict) -> None:
    """
    Metadatei ueber eine Nebendatei schreiben und dann umbenennen. Ein
    abgebrochener Schreibvorgang hinterlaesst damit die alte Metadatei
    statt einer halben neuen - dieselbe Ueberlegung wie beim Sidecar,
    nur hier durchfuehrbar, weil die Datei klein und in einem Zug
    fertig ist.
    """
    ordner = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _copy_samples(wav_path: str, offset: int, length: int, ziel: str) -> int:
    """Kopiert length Bytes ab offset aus der WAV in die Datendatei.
    Liefert die Zahl tatsaechlich geschriebener Bytes."""
    geschrieben = 0
    with open(wav_path, "rb") as src, open(ziel, "wb") as dst:
        src.seek(offset)
        rest = length
        while rest > 0:
            block = src.read(min(COPY_CHUNK_BYTES, rest))
            if not block:
                break
            dst.write(block)
            geschrieben += len(block)
            rest -= len(block)
    return geschrieben


def write_sigmf(wav_path: str, *, with_data: bool = False, out_dir=None) -> dict:
    """
    Schreibt die SigMF-Dateien zu einer IQ-WAV und liefert ein Dict mit
    den geschriebenen Pfaden und den wichtigsten Kennwerten.

    with_data=False (Vorgabe): NCD, nur <name>.sigmf-meta NEBEN der WAV.
    out_dir ist dann NICHT erlaubt - die Spezifikation verlangt die
    Datendatei im selben Verzeichnis wie die Metadatei, und die
    Datendatei ist hier die WAV selbst.

    with_data=True: <name>.sigmf-data (Kopie der Sample-Bytes) und die
    zugehoerige konforme <name>.sigmf-meta, wahlweise in out_dir.

    Bestehende Dateien werden still ueberschrieben (Festlegung 3).
    """
    if out_dir and not with_data:
        raise ValueError("out_dir is only allowed together with with_data=True "
                         "(NCD requires the metadata file next to the WAV)")

    sidecar = read_sidecar(wav_path)
    require_iq(sidecar, wav_path)
    # T-922a: waehrend der Aufnahme sind die Laengenangabe im RIFF-Kopf
    # und das Dateiende in Bewegung. data_span_on_disk() wuerde zwar
    # etwas liefern (der Fallback nimmt den Rest der Datei), aber die
    # Metadatei beschriebe einen Zustand, den es Sekunden spaeter nicht
    # mehr gibt. Lieber ablehnen und spaeter wiederholen.
    if sidecar.get("running"):
        raise SigmfRunningError(
            f"recording still in progress (running=true), export refused: {wav_path}")
    offset, length, trailing = data_span_on_disk(wav_path)

    stamm = os.path.splitext(os.path.basename(wav_path))[0]
    ziel_ordner = out_dir or (os.path.dirname(os.path.abspath(wav_path)) or ".")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    meta_path = os.path.join(ziel_ordner, stamm + META_SUFFIX)

    # T-922a: eine .sigmf-data aus einem frueheren --data-Lauf, die neben
    # einer NCD-Metadatei liegen bleibt, ist eine ZWEITE Datenquelle.
    # sigmf-python warnt in dem Fall ausdruecklich und entscheidet sich
    # fuer core:dataset (Befund der Erprobung 2026-09-14). Wir melden das
    # nur - geloescht wird nichts, was der Benutzer selbst angelegt hat.
    stale_data = None
    if not with_data:
        _kandidat = os.path.join(ziel_ordner, stamm + DATA_SUFFIX)
        if os.path.isfile(_kandidat):
            stale_data = _kandidat

    ergebnis = {
        "meta_path": meta_path,
        "data_path": None,
        "ncd": not with_data,
        "header_bytes": offset,
        "sample_bytes": length,
        "trailing_bytes": trailing,
        "stale_data": stale_data,
    }

    if with_data:
        data_path = os.path.join(ziel_ordner, stamm + DATA_SUFFIX)
        kopiert = _copy_samples(wav_path, offset, length, data_path)
        meta = build_sigmf_meta(sidecar)
        ergebnis["data_path"] = data_path
        ergebnis["sample_bytes"] = kopiert
    else:
        meta = build_sigmf_meta(
            sidecar,
            dataset_name=os.path.basename(wav_path),
            header_bytes=offset,
            trailing_bytes=trailing,
        )

    _write_json_atomic(meta_path, meta)
    return ergebnis


# ---------------------------------------------------------------------------
# Selbsttest - "python -m eb200_core.sigmf_export --selftest", ohne Geraet
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import wave

    sample_rate = 48000
    n_frames = 1000

    with tempfile.TemporaryDirectory() as tmp_dir:
        wav_path = os.path.join(tmp_dir, "probe.wav")
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(struct.pack(f"<{n_frames * 2}h",
                                      *([1000, -1000] * n_frames)))

        sidecar = {
            "start_iso": "2026-09-14T10:11:12+00:00",
            "stop_iso": "2026-09-14T10:11:13+00:00",
            "freq_hz": 10031000,
            "mode": "IQ",
            "bw_hz": 15000,
            "audio_remote_mode": 1,
            "sample_rate_hz": sample_rate,
            "channels": 2,
            "sampwidth_bytes": 2,
            "frames_written": n_frames,
            "duration_s": round(n_frames / sample_rate, 3),
            "is_iq": True,
            "rx": {"gain_mode": "MGC", "gain_mgc_dbuv": 15.0, "att_state": False,
                   "att_auto": None, "ref_level_dbuv": 40.0, "level_dbuv": 22.5,
                   "afc": False, "squelch_on": False, "squelch_thr_dbuv": None},
            "device": {"idn": "Rohde&Schwarz,EB200,101459,02.74",
                       "manufacturer": "Rohde&Schwarz", "model": "EB200",
                       "serial": "101459", "firmware": "02.74",
                       "options": "EB200SU,EB200DS,EB200R4"},
            "running": False,
        }
        with open(wav_path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)

        # --- T1: Datenversatz wird gelesen, nicht geraten -------------------
        offset, length, trailing = data_span_on_disk(wav_path)
        assert offset == 44, offset
        assert length == n_frames * 4, (length, n_frames)
        assert trailing == 0, trailing
        print(f"OK  T1 data_span_on_disk: Versatz {offset}, Nutzdaten {length} Byte, "
              f"{trailing} Byte Rest")

        # --- T2: NCD schreibt genau EINE Datei ------------------------------
        vorher = set(os.listdir(tmp_dir))
        res = write_sigmf(wav_path)
        nachher = set(os.listdir(tmp_dir))
        assert nachher - vorher == {"probe" + META_SUFFIX}, nachher - vorher
        assert res["ncd"] is True and res["data_path"] is None, res
        print("OK  T2 NCD: nur probe.sigmf-meta entstanden, keine Datenkopie")

        with open(res["meta_path"], encoding="utf-8") as f:
            meta = json.load(f)
        g = meta["global"]
        c = meta["captures"][0]

        # --- T3: Kernfelder ------------------------------------------------
        assert g["core:datatype"] == "ci16_le", g
        assert g["core:sample_rate"] == float(sample_rate), g
        assert g["core:dataset"] == "probe.wav", g
        assert g["core:trailing_bytes"] == 0, g
        assert c["core:header_bytes"] == 44, c
        assert c["core:frequency"] == 10031000.0, c
        assert c["core:sample_start"] == 0, c
        print("OK  T3 Kernfelder: datatype, sample_rate, dataset, header_bytes, frequency")

        # --- T4: Abtastrate OHNE Faktor 2 -----------------------------------
        # Ein WAV-Frame (links+rechts) ist EIN komplexes Sample. Wer hier
        # verdoppelt, bekommt eine Aufnahme halber Dauer und doppelter
        # Bandbreite - der haeufigste stille Fehler beim SigMF-Export.
        assert g["core:sample_rate"] == sidecar["sample_rate_hz"], g
        erwartete_samples = length // 4
        assert erwartete_samples == n_frames, (erwartete_samples, n_frames)
        print("OK  T4 komplexe Abtastrate unveraendert uebernommen (kein Faktor 2)")

        # --- T5: RFC3339 mit Z ---------------------------------------------
        assert c["core:datetime"] == "2026-09-14T10:11:12Z", c
        assert as_rfc3339(None) is None
        assert as_rfc3339("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"
        print("OK  T5 core:datetime als RFC3339 mit Z")

        # --- T6: Namensraum vollstaendig und angemeldet ----------------------
        ext = g["core:extensions"]
        assert ext == [{"name": "moniscope", "version": MONISCOPE_EXT_VERSION,
                        "optional": True}], ext
        assert g["moniscope:demod"] == "IQ", g
        assert g["moniscope:bw_hz"] == 15000, g
        assert g["moniscope:audio_remote_mode"] == 1, g
        assert g["moniscope:rx"]["gain_mode"] == "MGC", g
        assert g["moniscope:device"]["serial"] == "101459", g
        assert g["core:hw"] == "Rohde&Schwarz EB200, S/N 101459, FW 02.74", g
        print("OK  T6 Namensraum moniscope: vollstaendig, in core:extensions angemeldet")

        # --- T7: Datenexport, byteidentisch ---------------------------------
        res2 = write_sigmf(wav_path, with_data=True)
        assert res2["data_path"] is not None and os.path.exists(res2["data_path"])
        assert os.path.getsize(res2["data_path"]) == length, res2
        with open(wav_path, "rb") as f:
            f.seek(offset)
            roh = f.read(length)
        with open(res2["data_path"], "rb") as f:
            kopie = f.read()
        assert kopie == roh, "sigmf-data ist nicht byteidentisch zum WAV-Datenbereich"
        with open(res2["meta_path"], encoding="utf-8") as f:
            meta2 = json.load(f)
        assert "core:dataset" not in meta2["global"], meta2["global"]
        assert "core:header_bytes" not in meta2["captures"][0], meta2["captures"][0]
        print("OK  T7 Datenexport byteidentisch, konforme Metadatei ohne NCD-Felder")

        # --- T8: Zweitlauf ueberschreibt still -------------------------------
        res3 = write_sigmf(wav_path)
        assert os.path.exists(res3["meta_path"])
        with open(res3["meta_path"], encoding="utf-8") as f:
            assert json.load(f)["global"]["core:dataset"] == "probe.wav"
        print("OK  T8 Zweitlauf ueberschreibt die Metadatei still")

        # --- T9: out_dir nur mit --data --------------------------------------
        try:
            write_sigmf(wav_path, out_dir=os.path.join(tmp_dir, "raus"))
        except ValueError:
            print("OK  T9 out_dir ohne Datenexport -> ValueError")
        else:
            raise AssertionError("ValueError erwartet: out_dir ohne with_data")

        # --- T10: Mono-Audio wird abgelehnt -----------------------------------
        mono_path = os.path.join(tmp_dir, "mono.wav")
        with wave.open(mono_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<100h", *([500] * 100)))
        mono_side = dict(sidecar)
        mono_side.update({"is_iq": False, "channels": 1, "mode": "FM",
                          "sample_rate_hz": 16000})
        with open(mono_path + ".json", "w", encoding="utf-8") as f:
            json.dump(mono_side, f, indent=2, ensure_ascii=False)
        try:
            write_sigmf(mono_path)
        except ValueError:
            print("OK  T10 Mono-Audio (is_iq false) -> ValueError, keine Datei")
        else:
            raise AssertionError("ValueError erwartet: Mono-Audio ist nicht exportierbar")
        assert not os.path.exists(os.path.join(tmp_dir, "mono" + META_SUFFIX))

        # --- T11: WAV mit zusaetzlichem Chunk vor data -------------------------
        # Belegt, dass der Versatz gelesen und nicht auf 44 geraten wird.
        fremd_path = os.path.join(tmp_dir, "fremd.wav")
        fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 2, sample_rate,
                          sample_rate * 4, 4, 16)
        listchunk = struct.pack("<4sI", b"LIST", 10) + b"INFOtest\x00\x00"
        nutz = struct.pack("<8h", *([1, -1] * 4))
        datachunk = struct.pack("<4sI", b"data", len(nutz)) + nutz
        koerper = b"WAVE" + fmt + listchunk + datachunk
        with open(fremd_path, "wb") as f:
            f.write(struct.pack("<4sI", b"RIFF", len(koerper)) + koerper)
        f_off, f_len, f_trail = data_span_on_disk(fremd_path)
        assert f_off == 12 + 24 + 18 + 8, f_off
        assert f_len == len(nutz) and f_trail == 0, (f_len, f_trail)
        print(f"OK  T11 zusaetzlicher LIST-Chunk: Versatz {f_off} statt 44 erkannt")

        # --- T12: Bestandsaufnahme ohne rx/device (aelter als T-917) --------
        # Solche Aufnahmen gibt es wirklich - die Bahrain-Aufnahmen vom
        # 2026-09-01 sind aelter als der rx-Schnappschuss. Ihre leeren
        # Felder duerfen nicht als null in der Metadatei stehen (T-921c).
        alt_path = os.path.join(tmp_dir, "alt.wav")
        with wave.open(alt_path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(struct.pack("<8h", *([7, -7] * 4)))
        alt_side = {k: v for k, v in sidecar.items()
                    if k not in ("rx", "device", "stop_iso")}
        alt_side["frames_written"] = 4
        with open(alt_path + ".json", "w", encoding="utf-8") as f:
            json.dump(alt_side, f, indent=2, ensure_ascii=False)
        with open(write_sigmf(alt_path)["meta_path"], encoding="utf-8") as f:
            alt_glob = json.load(f)["global"]
        for _fehlt in ("moniscope:rx", "moniscope:device",
                       "moniscope:stop_iso", "core:hw"):
            assert _fehlt not in alt_glob, (_fehlt, sorted(alt_glob))
        assert alt_glob["moniscope:demod"] == "IQ", alt_glob
        assert alt_glob["moniscope:bw_hz"] == 15000, alt_glob
        assert alt_glob["core:sample_rate"] == float(sample_rate), alt_glob
        # Die eigentliche Zusicherung: nirgends ein null.
        assert not any(v is None for v in alt_glob.values()), alt_glob
        print("OK  T12 Bestandsaufnahme ohne rx/device: Felder entfallen, kein null")

        # --- T13: laufende Aufnahme wird abgewiesen --------------------------
        lauf_path = os.path.join(tmp_dir, "laeuft.wav")
        with wave.open(lauf_path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(struct.pack("<8h", *([3, -3] * 4)))
        lauf_side = dict(sidecar)
        lauf_side.update({"running": True, "stop_iso": None, "frames_written": 4})
        with open(lauf_path + ".json", "w", encoding="utf-8") as f:
            json.dump(lauf_side, f, indent=2, ensure_ascii=False)
        try:
            write_sigmf(lauf_path)
        except SigmfRunningError:
            print("OK  T13 laufende Aufnahme -> SigmfRunningError, keine Metadatei")
        else:
            raise AssertionError("SigmfRunningError erwartet: running=true")
        assert not os.path.exists(os.path.join(tmp_dir, "laeuft" + META_SUFFIX))

        # --- T14: liegengebliebene .sigmf-data wird gemeldet ------------------
        # Reihenfolge wie im Alltag: erst --data, dann NCD. Die Datendatei
        # bleibt liegen und muss im Ergebnis auftauchen, ohne geloescht zu
        # werden.
        write_sigmf(wav_path, with_data=True)
        res_stale = write_sigmf(wav_path)
        assert res_stale["stale_data"] is not None, res_stale
        assert os.path.basename(res_stale["stale_data"]) == "probe" + DATA_SUFFIX, res_stale
        assert os.path.isfile(res_stale["stale_data"]), "die Datendatei wurde geloescht"
        os.unlink(res_stale["stale_data"])
        assert write_sigmf(wav_path)["stale_data"] is None
        print("OK  T14 liegengebliebene .sigmf-data gemeldet und NICHT geloescht")

    print("\nSelbsttest erfolgreich abgeschlossen.")


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="SigMF-Export fuer IQ-Aufnahmen (Modulaufruf; die "
                    "Kommandozeile fuer den Alltag ist tools/sigmf_export.py)")
    ap.add_argument("wav", nargs="?", help="Pfad zur IQ-WAV")
    ap.add_argument("--data", action="store_true",
                    help="zusaetzlich .sigmf-data schreiben (spec-konform, "
                         "doppelter Platzbedarf)")
    ap.add_argument("--out-dir", default=None,
                    help="Zielverzeichnis, nur zusammen mit --data")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args(argv)

    if args.selftest:
        _self_test()
        return 0
    if not args.wav:
        ap.error("Pfad zur IQ-WAV fehlt (oder --selftest verwenden)")

    res = write_sigmf(args.wav, with_data=args.data, out_dir=args.out_dir)
    print(res["meta_path"])
    if res["data_path"]:
        print(res["data_path"])
    return 0


if __name__ == "__main__":
    sys.exit(_main())
