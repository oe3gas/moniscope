# WS2 – Audio-Aufnahme (Langzeitmonitoring)

**Kurzbeschreibung:** Der EB200 streamt das demodulierte Audio ausgewählter Signale
per UDP; ein robuster Rekorder speichert es zeitgestempelt für die spätere Auswertung
und Analyse.

---

## 1. Ziel & Nutzen

- Nachvollziehbare, archivierbare Aufnahme ausgewählter Frequenzen über lange Zeit.
- Basis für Offline-Analyse (Anhören, Dekodieren, Vermessen).
- Ergänzt WS1: WS1 findet *dass* etwas aktiv war, WS2 hält *was* fest.

## 2. Umfang

**In Scope**
- Steuerung von Frequenz/Mode/Bandbreite und Start des UDP-Audio-Streams (SCPI).
- Empfang und Parsen der EB200-Audio-Datagramme.
- Speicherung als WAV mit korrektem Timing (Lückenbehandlung).
- Zeit-/frequenzbasierte Dateibenennung und -rotation.
- Zwei Aufnahmemodi: Daueraufnahme und squelch-/pegelgesteuerte Aufnahme.

**Out of Scope** (→ WS3)
- Spektrum-/Wasserfalldarstellung.
- Automatische Dekodierung (kann später aufgesetzt werden).

## 3. Technischer Ansatz

- **Steuerkanal (SCPI):** Frequenz, Mode, Bandbreite setzen; Audioformat wählen
  (`SYSTem:AUDio:REMote:MODe …`); UDP-Ziel setzen (`TRACE:UDP:DEFAULT:TAG:ON …,AUDIO`).
- **Datenkanal (EB200-Protokoll, UDP):** Datagramme mit Common-Header
  (Magic `0x000EB200`, Sequenznummer), AUDIO-Tag `401` und 16-bit-PCM-Payload
  (signed, mono, Big-Endian).
- **WAV-Schreiben:** Big-Endian → Little-Endian byteswappen; Header via `wave`-Modul.
- **Timing:** Sequenznummern auswerten; bei Paketverlust Stille in Höhe der fehlenden
  Samples einfügen, damit die Aufnahme zeitlich nicht verrutscht.

## 4. Aufnahmemodi

| Modus              | Verhalten                                             | Einsatz                     |
|--------------------|-------------------------------------------------------|-----------------------------|
| Daueraufnahme      | schreibt kontinuierlich, rotiert nach Zeit            | belegte Kanäle, Baken       |
| Squelch-gesteuert  | schreibt nur bei offenem Squelch / über Pegelschwelle | sporadischer Verkehr, Utility |

## 5. Datei- & Metadatenkonzept (Vorschlag)

- Namensschema, z. B.: `YYYYMMDD_HHMMSS_<freq>kHz_<mode>.wav`.
- Rotation: neue Datei je Stunde und/oder bei Frequenzwechsel; Größenobergrenze.
- Begleitende Sidecar-Datei (JSON/CSV) je Aufnahme mit Frequenz, Mode, Bandbreite,
  Startzeit, Antenne, gezählten Paketverlusten.

## 6. Geplante Aktivitäten

**Phase A – Basisrekorder**
- UDP-Reader-Thread + Datagramm-Parser (aus dem bereits skizzierten Code).
- WAV-Writer mit Byteswap und Lückenbehandlung.

**Phase B – Betriebsfestigkeit**
- Dateirotation nach Zeit/Größe, sauberes Schließen bei Frequenzwechsel/Stopp.
- Sidecar-Metadaten, Verlust-/Fehlerstatistik.
- Reconnect bei Netzwerkabriss.

**Phase C – Modi & Steuerung**
- Squelch-gesteuerte Aufnahme (mit Vor-/Nachlauf um das Ereignis).
- Konfigdatei (Frequenzen, Modi, Zeitfenster) – idealerweise gemeinsam mit WS1.
- Optionale Übergabe an ein virtuelles Audiogerät (für WSJT-X/fldigi) als Nebenzweig.

**Phase D – Speicher & Dauerbetrieb**
- Speicherbudget/Aufräumstrategie (Ältestes löschen / komprimieren).
- Autostart als Service.

## 7. Deliverables

- `recorder.py` (SCPI-Setup + UDP-Empfang + WAV-Schreiben).
- WAV-Archiv nach Namenskonvention + Sidecar-Metadaten.
- Betriebslog inkl. Paketverlust-Statistik.
- Kurzdoku.

## 8. Abhängigkeiten / Voraussetzungen

- Python mit `numpy` (WAV via Standardmodul `wave`).
- Ausreichend Speicher/Durchsatz für die gewählte Abtastrate.
- Genaue Werte der Audio-Modi (Raten) aus dem Handbuch bestätigen.
- Für das virtuelle Audiogerät: PortAudio/`sounddevice` bzw. OS-seitiges Loopback.

## 9. Offene Entscheidungen (für unser nächstes Gespräch)

- Daueraufnahme, squelch-gesteuert, oder beides pro Kanal wählbar?
- Abtastrate/Format – reicht 8 kHz mono, oder höhere Rate nötig?
- Speicherbudget und Aufbewahrungsdauer.
- Reine Archivierung, oder direkt Weiterreichen an einen Dekoder (Brücke zu späterem WS)?

## 10. Langzeit-Aufnahme (12–24 h): WAV-spezifische Grenzen

*Stand: 2026-08. Gesamtkonzept Langzeit-Recording und Long-Run-Jobs: siehe
WS3, Abschnitte 10 und 11. Wesentlicher Inhalt für das geplante Usermanual.*

- **Datenmenge** (fixer Modus 6: 16 kHz / 16 Bit / mono): 32 kB/s →
  115 MB/h, 1,38 GB/12 h, 2,77 GB/24 h.
- **RIFF-4-GB-Grenze**: WAV hat 32-Bit-Größenfelder. Modus 6 erreicht
  4 GB nach ~37 h – 24 h passen, aber ohne Reserve; manche Player werden
  ab 2 GB unzuverlässig. Höhere Modi sprengen die Grenze früher
  (32 kHz stereo: nach ~9,3 h).
- **`wave`-Modul schreibt den korrekten Header erst in `close()`**: Nach
  einem Absturz stimmt `nframes` nicht und die Sidecar fehlt komplett.
- **Konsequenz**: stündliche Dateirotation (ohnehin Phase B) ist für
  Langzeit-Aufnahmen PFLICHT – entschärft 4-GB-Grenze, Header-Problem und
  begrenzt den Verlust bei Absturz auf max. 1 h. Details und weitere
  Maßnahmen (laufende Sidecar, Flush, Betriebsumgebung): WS3, 10.3.

## 11. Audio-Mithören im IFPAN-Zoom (Live, Browser)

*Stand: umgesetzt und am Gerät validiert (Race-Fix „SOLL statt Einmal-Kommando“, Kreuztest mit WAV-Aufnahme, Span-/Mode-/BW-Wechsel, Reload, Tab-Schließen, Hörprobe).*

**Zweck.** Beim Heranzoomen hört man das demodulierte Audio des EB200 direkt im Browser – zum Identifizieren eines Signals, bevor man eine Aufnahme startet. Es ist kein Archivpfad: Lücken werden bewusst verworfen (siehe Latenzbremse), fürs Archiv bleibt die WAV-Aufnahme (Abschnitt 3–5).

**Bedienung.** Knopf „🔊 Mithören" im Zoom-Kopf ist ein Vorlieben-Toggle (im Browser gemerkt, Standard EIN). Ist er EIN, startet das Mithören automatisch beim Wechsel in den Zoom und endet beim „Zurück". Orange gerahmt („Klick zum Start") = der Browser hat den Audiostart noch nicht freigegeben (Autoplay-Policy, tritt beim extern ausgelösten Zoom aus dem SDR-Wasserfall auf); ein Klick holt die Freigabe nach.

**Technik.**
- Server: `GET /audio/stream` liefert die rohe AUD-Payload (16 kHz / 16 Bit / mono, **Big-Endian wie vom Gerät**, kein WAV-Kopf) als endlose chunked Response über einen `AudioBroker` (gleiches Muster wie `FrameBroker`, bounded Queue, drop-oldest). Kein eigener Schalt-Endpunkt: ein offener `/audio/stream` **ist** der Einschaltwunsch (`monitor_wanted`), der letzte Abriss löst eine Prüfung der Client-Zahl im Controller-Thread aus. `_run_zoom_loop` stellt den Wunsch bei jedem Eintritt her (`_apply_monitor_wish`) – damit überlebt das Mithören Span-/Modulations-/BW-Wechsel und ist unabhängig von der Ankunftsreihenfolge gegenüber `/api/zoom` (ein früherer Entwurf mit separatem Kommando verpuffte in der Übersicht, wenn es vor `/api/zoom` in der Queue lag).
- Browser: Web Audio API (`fetch` + `ReadableStream` → `AudioBuffer`-Kette). Kein `<audio>`-Element, das puffert Endlos-Streams unkontrolliert. Rückstand > 0,4 s → Spielzeiger neu setzen. Erwartete Latenz ~150–300 ms.
- **AUD-Tag wird geteilt.** Aufnahme und Mithören laufen über denselben Display-UdpPath und denselben Tag. `_audio_tag_on()`/`_audio_tag_off()` im `DeviceController` bilden ein Refcount über `recording` + `monitoring`; `SYST:AUD:REM:MOD 0` zusätzlich nur, wenn kein Job-Audio (:18104) läuft. Damit gilt weiterhin „ein Port, ein Tag" (WS3 11.5) – das Mithören fügt keinen Pfad hinzu.
- `/audio/stream` ist lesend und bewusst **nicht** per 423 gesperrt: Mithören während eines IFPAN-Long-Run-Jobs ist erwünscht und berührt den Job-Pfad nicht.
- Reißt der letzte `/audio/stream`-Client ab (Tab zu), stößt der Handler den Monitor-Stop an – kein verwaister AUD-Strom am Gerät.

**Grenzen.** Mehrere Tabs teilen sich den einen Tag: „Aus" in einem Tab stoppt serverseitig alle (Fensterdisziplin, WS3). Kein Lautstärkeregler im Browser – Pegel kommt aus dem Gerät (Annex C `SYST:AUD:VOL` betrifft nur den Gerätelautsprecher, nicht den Remote-Strom).
