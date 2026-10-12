#!/usr/bin/env python3
"""
eb200_core/audio_wav.py

Eigenständiger WAV-Writer für die demodulierten AUDIO-UDP-Nutzdaten des EB200
(Annex F, GenericAttribute-Tag AUDIO=401). Enthält KEINEN Geräte-/Netzwerkzugriff
- reine Byte-Verarbeitung (Payload rein, WAV-Datei + Sidecar-JSON raus) - und ist
damit ohne angeschlossene Hardware per Selbsttest prüfbar (siehe unten).

Design-Entscheidung ("ein Writer, zwei Nutzer"): sowohl waterfall.py (IFPAN-Zoom,
Aufnahme-Knopf) als auch das spätere WS2-recorder.py sollen denselben Writer
verwenden - deshalb bewusst ohne jede Abhängigkeit vom SCPI-/UDP-Code der beiden
Aufrufer, nur Standardbibliothek (wave, struct, json, os, datetime).

Wichtige technische Fakten (Annex F + Handbuch, siehe waterfall.py-Kommentare):
  - Die AUDIO-Nutzdaten kommen vom Gerät als 16-Bit-signed-PCM in BIG-ENDIAN.
    WAV-Dateien erwarten LITTLE-ENDIAN - write_frames() byteswappt deshalb jedes
    Sample, statt am Gerät ein SWAP-Flag zu setzen (das würde die IFPAN-Daten auf
    demselben UDP-Path zerstören, siehe waterfall.py).
  - SYSTem:AUDio:REMote:MODe legt Samplerate/Bittiefe/Kanalzahl fest - die Tabelle
    dazu steht in AUDIO_REMOTE_MODES. Nur die sechs 16-Bit-Modi werden unterstützt
    (8-Bit-WAV bräuchte eine unsigned-Konvertierung, die hier nicht gebraucht wird);
    andere Modi liefern ein ValueError.

close() schreibt neben die WAV-Datei eine Sidecar-JSON (gleicher Dateiname +
".json") mit Start-/Stopzeit, Frequenz/Modulation/Bandbreite (aus den beim
Öffnen übergebenen Metadaten), seit T-917 zusätzlich den Empfangsparametern
und der Geräteprovenienz (Blöcke "rx"/"device", vom Aufrufer über meta
hereingegeben - dieser Writer fragt NICHTS am Gerät ab, siehe oben),
sowie den tatsächlich geschriebenen Audio-Parametern
- damit eine Aufnahme auch ohne den ursprünglichen Aufrufer-Kontext nachvollziehbar
bleibt. close() ist idempotent (mehrfach aufrufbar, z. B. über verschiedene
Abbruchpfade wie "Zurück", Verbindungsende oder expliziten Stop-Klick).
"""

from __future__ import annotations

import json
import os
import struct
import wave
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# SYSTem:AUDio:REMote:MODe -> (sample_rate_hz, bits, channels)
# ---------------------------------------------------------------------------

AUDIO_REMOTE_MODES = {
    1:  (32000, 16, 2),
    2:  (32000, 16, 1),
    3:  (32000, 8, 2),
    4:  (32000, 8, 1),
    5:  (16000, 16, 2),
    6:  (16000, 16, 1),
    7:  (16000, 8, 2),
    8:  (16000, 8, 1),
    9:  (8000, 16, 2),
    10: (8000, 16, 1),
    11: (8000, 8, 2),
    12: (8000, 8, 1),
}


def wav_params_for_mode(mode: int) -> tuple[int, int, int]:
    """
    Übersetzt einen SYSTem:AUDio:REMote:MODe-Wert in WAV-Parameter
    (sample_rate_hz, sampwidth_bytes, channels).

    Wirft ValueError bei unbekanntem Modus ODER bei einem der sechs 8-Bit-Modi
    (3, 4, 7, 8, 11, 12) - diese bräuchten eine unsigned-Byte-Konvertierung
    (8-Bit-WAV ist per Spezifikation unsigned, das Gerät liefert aber wohl
    signed 8-Bit), die dieser Writer bewusst nicht implementiert.
    """
    if mode not in AUDIO_REMOTE_MODES:
        raise ValueError(f"unknown SYSTem:AUDio:REMote:MODe value: {mode}")
    sample_rate_hz, bits, channels = AUDIO_REMOTE_MODES[mode]
    if bits != 16:
        raise ValueError(
            f"AUDIO_REMOTE_MODE {mode} is a {bits}-bit mode - only the "
            f"16-bit modes (1,2,5,6,9,10) are supported by this writer"
        )
    return sample_rate_hz, bits // 8, channels


# ---------------------------------------------------------------------------
# WAV-Writer
# ---------------------------------------------------------------------------

class WavAudioWriter:
    """
    Schreibt fortlaufend AUDIO-UDP-Payload (16-Bit BE-PCM) als WAV-Datei
    (16-Bit LE-PCM) auf die Platte. Öffnet die Datei sofort im Konstruktor,
    schließt sie erst in close() (und schreibt dabei die Sidecar-JSON).
    """

    def __init__(self, path: str, audio_remote_mode: int, meta: dict) -> None:
        sample_rate_hz, sampwidth_bytes, channels = wav_params_for_mode(audio_remote_mode)

        self.path = path
        self.audio_remote_mode = audio_remote_mode
        self.meta = dict(meta)  # Kopie - Änderungen am Original des Aufrufers sollen nicht durchschlagen
        self.sample_rate_hz = sample_rate_hz
        self.sampwidth_bytes = sampwidth_bytes
        self.channels = channels
        self.frames_written = 0
        self.start_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._closed = False

        self._wav = wave.open(path, "wb")
        self._wav.setnchannels(channels)
        self._wav.setsampwidth(sampwidth_bytes)
        self._wav.setframerate(sample_rate_hz)

        # Sidecar ab Aufnahmestart mit running=True (T-1109, Muster
        # SpectrumBinWriter: running-Flag ab Start) - eine laufende
        # Aufnahme ist damit in recording_index.py sichtbar/lesbar statt
        # erst nach close() zu existieren. Scheitert das Schreiben (z. B.
        # volle Platte, Rechte), bricht die Aufnahme NICHT ab - dieselbe
        # Haltung wie bei write_frames() gegenueber einem einzelnen
        # fehlerhaften Paket: eine Nebendatei darf die Hauptaufnahme nicht
        # gefaehrden.
        try:
            self._write_sidecar(True)
        except OSError:
            pass

    def write_frames(self, be_pcm: bytes) -> None:
        """
        Nimmt ein Stück rohe AUDIO-Payload (16-Bit signed, Big-Endian) entgegen,
        byteswappt sie nach Little-Endian und schreibt sie in die WAV-Datei.

        Ein ungerades Rest-Byte (kaputtes/abgeschnittenes letztes Sample, sollte
        bei korrekt geschnittener Payload nicht vorkommen) wird defensiv
        verworfen, statt einen Fehler zu werfen - eine Aufnahme soll wegen eines
        einzelnen fehlerhaften Pakets nicht abbrechen.
        """
        if self._closed:
            return  # nach close() nichts mehr schreiben (defensiv, z. B. Race beim Stoppen)

        n_samples = len(be_pcm) // 2
        if n_samples == 0:
            return
        usable_len = n_samples * 2  # verwirft ein etwaiges ungerades Rest-Byte

        samples = struct.unpack(f">{n_samples}h", be_pcm[:usable_len])
        le_bytes = struct.pack(f"<{n_samples}h", *samples)
        self._wav.writeframes(le_bytes)

        # "Frames" im WAV-Sinn = Sample-Gruppen über alle Kanäle hinweg (bei
        # Mono identisch zur Sample-Anzahl, bei Stereo halbiert).
        self.frames_written += n_samples // self.channels

    def _write_sidecar(self, running: bool) -> None:
        """
        Baut und schreibt die Sidecar-JSON ("<wav-pfad>.json") - gemeinsam
        fuer den Aufnahmestart (running=True, siehe __init__) und close()
        (running=False). Feldnamen/-reihenfolge der bisherigen Sidecars
        bleiben erhalten, "running" kommt NEU als letztes Feld hinzu -
        Bestandsdateien ohne das Feld bleiben gueltig, Lesende nutzen
        meta.get("running", False). Solange running, gibt es noch kein
        Ende: stop_iso bleibt None; frames_written/duration_s zeigen den
        jeweils aktuellen Zwischenstand.
        """
        stop_iso = None if running else datetime.now(timezone.utc).isoformat(timespec="seconds")
        duration_s = self.frames_written / self.sample_rate_hz if self.sample_rate_hz else 0.0

        sidecar = {
            "start_iso": self.start_iso,
            "stop_iso": stop_iso,
            "freq_hz": self.meta.get("freq_hz"),
            "mode": self.meta.get("mode"),
            "bw_hz": self.meta.get("bw_hz"),
            "audio_remote_mode": self.audio_remote_mode,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "sampwidth_bytes": self.sampwidth_bytes,
            "frames_written": self.frames_written,
            "duration_s": round(duration_s, 3),
            # IQ-Kennzeichnung für Weiterverarbeitung (tools/wav2sdr.py):
            # Kanalzuordnung I=Kanal 0 (links), Q=Kanal 1 (rechts) ist die
            # Projektannahme - Hardware-Validierung siehe PLAN.md T-901.
            "is_iq": (str(self.meta.get("mode", "")).upper() == "IQ"
                      and self.channels == 2),
            # T-917: Empfangsparameter und Geraeteprovenienz zum Aufnahmestart
            # (Aufbau siehe eb200_core/rx_snapshot.py). Der Writer REICHT NUR
            # DURCH - kein SCPI-Zugriff, das bleibt Sache des Aufrufers.
            # Fehlt der Block, steht None statt eines leeren Dicts: "nicht
            # erhoben" (Bestandsaufnahme, Aufrufer ohne Geraet) ist etwas
            # anderes als "erhoben, aber alle Werte leer".
            "rx": self.meta.get("rx"),
            "device": self.meta.get("device"),
            "running": running,
        }
        with open(self.path + ".json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)

    def close(self) -> None:
        """
        Schließt die WAV-Datei und schreibt die finale Sidecar-JSON daneben
        (running=False). Idempotent - ein zweiter/dritter Aufruf ist ein
        No-Op (wichtig, da close() über mehrere unabhängige Pfade
        erreichbar ist, siehe waterfall.py: expliziter Stop-Klick,
        "Zurück"-Button, Loop-Ende).
        """
        if self._closed:
            return
        self._closed = True
        self._wav.close()
        self._write_sidecar(False)


# ---------------------------------------------------------------------------
# Selbsttest - "python audio_wav.py" direkt ausführen, ohne Gerät/Hardware
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import math
    import tempfile

    mode = 6  # Projekt-Default: 16 kHz / 16 Bit / mono
    sample_rate_hz, sampwidth_bytes, channels = wav_params_for_mode(mode)

    duration_s = 0.25
    n_frames = int(sample_rate_hz * duration_s)
    tone_hz = 440.0
    amplitude = 8000

    # ~0,25s Sinuston als BE-PCM erzeugen (so, wie es auch vom Gerät käme).
    samples = []
    for i in range(n_frames):
        value = int(amplitude * math.sin(2 * math.pi * tone_hz * i / sample_rate_hz))
        samples.extend([value] * channels)
    be_pcm = struct.pack(f">{len(samples)}h", *samples)

    with tempfile.TemporaryDirectory() as tmp_dir:
        wav_path = os.path.join(tmp_dir, "selftest.wav")
        writer = WavAudioWriter(
            wav_path, audio_remote_mode=mode,
            meta={"freq_hz": 100_000_000, "mode": "FM", "bw_hz": 9000},
        )
        # T-1109: direkt nach dem Konstruktor existiert das Sidecar bereits
        # mit running=True und offenem Ende (stop_iso None) - eine laufende
        # Aufnahme ist damit sofort interpretierbar, nicht erst nach close().
        sidecar_path = wav_path + ".json"
        assert os.path.exists(sidecar_path)
        with open(sidecar_path, encoding="utf-8") as f:
            sidecar_running = json.load(f)
        assert sidecar_running["running"] is True, sidecar_running
        assert sidecar_running["stop_iso"] is None, sidecar_running
        print("OK  Sidecar ab Aufnahmestart vorhanden: running=True, stop_iso=None")

        # in zwei Häppchen schreiben, um write_frames() über mehrere Aufrufe
        # hinweg zu testen (so wie es später bei echten UDP-Paketen passiert).
        half = len(be_pcm) // 2
        writer.write_frames(be_pcm[:half])
        writer.write_frames(be_pcm[half:])
        writer.close()
        writer.close()  # Idempotenz-Check: darf nicht knallen

        with wave.open(wav_path, "rb") as check:
            assert check.getframerate() == sample_rate_hz
            assert check.getsampwidth() == sampwidth_bytes
            assert check.getnchannels() == channels
            assert check.getnframes() == n_frames, (check.getnframes(), n_frames)
            print(f"OK  WAV geschrieben und geprüft: {check.getnframes()} Frames, "
                  f"{sample_rate_hz} Hz, {sampwidth_bytes * 8}-Bit, {channels} Kanal(e)")

        with open(sidecar_path, encoding="utf-8") as f:
            sidecar = json.load(f)
        assert sidecar["frames_written"] == n_frames
        assert sidecar["freq_hz"] == 100_000_000
        # T-917: ohne rx/device im meta stehen beide Bloecke als None im
        # Sidecar - die Schluessel EXISTIEREN aber, damit Lesende nicht
        # zwischen "Feld fehlt" und "Wert None" raten muessen.
        assert "rx" in sidecar and sidecar["rx"] is None, sidecar
        assert "device" in sidecar and sidecar["device"] is None, sidecar
        assert sidecar["running"] is False, sidecar
        assert sidecar["stop_iso"] is not None, sidecar
        print(f"OK  Sidecar-JSON vorhanden und plausibel: {sidecar}")

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()
