# Erhobene Fakten mit Fundstelle

Stand: 2026-08-29, erhoben gegen den Quellenexport dieses Standes.

Zweck: Prüfgrundlage. Wenn ein neuer Quellenstand vorliegt, wird gegen diese
Liste geprüft, statt alles neu zu suchen. Fundstellen nennen **Datei plus
Bezeichner**, nie Zeilennummern — die veralten sofort.

---

## 1. Server und Ports

`start_eb200.py`, Liste `SERVERS`:

| Port | Datei | optional |
|---|---|---|
| 8000 | `waterfall.py` | nein |
| 8010 | `eb200_core/block_admin.py` | nein |
| 8020 | `analyze_sessions.py` | nein |
| 8030 | `sdr_waterfall.py` | **ja** |
| 8040 | `analyze_spectrum.py` | nein |
| 8050 | `manage_jobs.py` | nein |
| 8060 | `analyze_mscan.py` | nein |
| 8070 | `analyze_audio.py` | nein |

Alle Server: `HTTP_HOST = "0.0.0.0"`, keine Anmeldung.

Startoptionen `start_eb200.py`: `--plain`, `--no-browser`, `--selftest`, `--data-dir PFAD`
(T-2003/T-2004: Datenwurzel für Kataloge, Aufnahmen, Sessions, Jobs, Logs und
Konfiguration; ohne Angabe der Programmordner. Ein nicht vorhandener Ordner bricht den
Start ab. Eine neue Datenwurzel bekommt beim ersten Start die mitgelieferten Kataloge
nach `known_freq/` kopiert, `eb200_core/data_seed.py`).
Tastatur nur bei echter Konsole (`msvcrt`, Windows): `[1]`–`[8]`, `[a]`,
`[e]`, `[n]`, `[p]`, `[r]`, `[R]`, `[Strg+C]`. Protokoll unter
`<Datenwurzel>/logs/start_eb200_<Zeitstempel>.log`. Lebenszeit-Kopplung über ein
Windows-Job-Objekt.

## 2. Navigation zwischen den Ansichten

- Ansichten-Menü in `waterfall.py` (`navList`, `data-port`): 8010, 8020,
  8040, 8050. Nicht enthalten: 8030, 8060, 8070.
- `:8050` Kopfzeile (`top-links`): Links auf `:8000` und `:8040`.
- `:8060` Kopfzeile: drei Links — `:8050/#definitionen`, die zugehörige
  Definition, `:8050/`.
- `:8070` Kopfzeile: ein Link, „zurück zur Liste (:8050)".
- `:8000` → `:8030` per Ziehen bei aktivem SDR-Schalter; `:8030` → `:8000`
  per Klick (Frequenz an IFPAN, Hauptfenster wird nach vorn geholt).
- `:8000` → `:8020` als Direktlink nach dem Stoppen einer Session.
- `:8060` → `:8070` je Segment („Spektrum"-Link); ausgegraut, wenn `job_dir`
  nicht unterhalb des Repo-Roots liegt (`eb200_core/mscan_view.py`,
  `build_mscan_run_result`) — siehe Befund A10.
- Fensterdisziplin: fester `window.name` je Server (`eb200_main`,
  `eb200_sessions`, `eb200_spectrum`, `eb200_jobs`, …), ein Tab je Server.

## 3. Gerätekopplung

`waterfall.py`, Konstanten am Modulkopf:

- `EB200_IP = "10.0.0.55"`, `SCPI_PORT = 5555`, `LOCAL_IP = None`
  (automatische Ermittlung).
- `DSCAN_MIN_SPAN_HZ = 20_000` — kleinere Spannen werden serverseitig
  abgelehnt, bevor das Gerät gefragt wird.
- `VERIFY_SCPI = True` — Rücklese-Verifikation nach Parameteränderungen
  (`eb200_core/scpi_verify.py`).
- `PARAM_POLL_INTERVAL_S = 2.0`, `PARAM_POLL_RECONNECT_BACKOFF_S = 5.0` —
  zweite SCPI-Verbindung für den Parameterabgleich; fester Backoff, damit
  kein Flattern einen der zehn Client-Slots belegt.

UDP-Zielports (getrennt je Datenstrom, damit Lückenerkennung zuverlässig
bleibt): 18102 Anzeige, 18103 Spektrum-Aufnahme, 18104 Job-Audio, 18105
MSCAN.

Gerätegrenzen: höchstens 10 gleichzeitige TCP-Steuerverbindungen, höchstens
4 UDP-Ziele.

## 4. Modi und ihre Nebenwirkungen

- Frequenzmodi: `DSC` (D-SCAN), `MSC` (M-SCAN), `CW` (Grundzustand, Basis für
  IFPAN).
- BW-Zoom: `FREQ:DSC:RES:AUTO ON`, Marker auf Bereichsmitte. Ausschalten
  stellt den vorherigen Bereich wieder her.
- Demodulation wird beim Aufbau des D-SCAN bedingungslos auf FM gesetzt
  (Begründung im Quelltext: SSB verträgt keine Bandbreiten über 9 kHz).
- Messzeit (`MEAS:TIME`) ist global; wird bei jedem Speed außer `MTIM` aktiv
  auf `DEFAULT` zurückgesetzt.
- Squelch ist am Gerät global, nicht auf den Zoom beschränkt.
- Sperre während eines Long-Run-Jobs: HTTP 423 auf den verändernden
  Endpunkten (`zoom`, `overview`, `range`, `speed`, `mtime`, `span`, `mode`,
  `bw`, `dscan_bwzoom`, `dscan_bw`, `squelch`, `tune`, `record`,
  `record-spectrum`). Lesende Endpunkte bleiben offen.
- MSCAN-Vorbedingung: kein `session_active`, kein `spectrum_recording`, kein
  `monitoring`. Verstoß → Lauf endet sofort als `ST_ABGEBROCHEN` mit
  Begründung. Siehe Befund A5.
- Job-Anlage lehnt überlappende Zeitfenster ab.

## 5. IFPAN

- Zulässige Spannen (`ALLOWED_IFPAN_SPANS_HZ`, im Frontend `ifpanSpans`):
  25, 50, 100, 200, 500 kHz und 1 MHz. Beide Listen müssen übereinstimmen.
- Demodulationsarten (`IFPAN_MODE_OPTIONS`, Vorgabe `AM`): `AM`, `FM`,
  `USB`, `LSB`, `CW`, `IQ` — **kein `PULSE`**, anders als `DEMOD_TOKENS` im
  Katalog (korrigiert 2026-09-01, siehe Befund A13).
- ZF-Bandbreiten (zwölf Stufen, `IFPAN_BW_OPTIONS_HZ`): 0,15 · 0,3 · 0,6 ·
  1,5 · 2,4 · 6 · 9 · 15 · 30 · 50 · 120 · 150 kHz.
- Squelch-Regler: −30 bis 110 dBµV; Option „folgt Skala min"
  (`squelchFollow`), Anwendung erst beim Loslassen des Reglers.
- S-Meter: Skala −40 bis 80 dBµV, Höchstwertmarke, Klick setzt zurück.
- Feinabstimmung per Mausrad: 1 Hz, Shift 10 Hz, Strg 100 Hz,
  Shift+Strg 1 kHz. Ziehen schwenkt die Mittenfrequenz.
- Hinweiszeile `bwHint` bei unpassender Kombination aus Modulation und
  Bandbreite.

## 6. Auto-Marker (D-SCAN)

`eb200_core/signal_detect.py`, Klasse `SignalDetector`; Endpunkte
`/api/detect` und `/api/detect/clear`.

- Δ-Schwelle 3 bis 30 dB, wahlweise automatisch aus der Rauschstreuung der
  ersten Sweeps (MAD-basiert).
- Mindestdauer 1 bis 10 Sweeps.
- `LEVEL_VALID_MAX_DBUV = 150.0` — Pegel darüber sind Gerätemarker, keine
  Messwerte, und nehmen nie an der Detektion teil (beobachtet: Rohwert 2000
  = 200,0 dBµV im letzten Bin des Bereichs).
- Markerliste mit Zeit, MHz, dBµV; Kopieren (Tab-getrennt) und CSV
  (Semikolon).
- **Importiert numpy auf Modulebene** — siehe Befund A1.

## 7. Ablage

- `SESSION_DIR = "sessions"` in `waterfall.py` und `analyze_sessions.py` —
  CWD-relativ, also im Repo-Root.
- `RECORD_DIR = load_storage_roots()[0]` in `waterfall.py` und
  `analyze_spectrum.py`.
- `load_storage_roots()` in `eb200_core/job_store.py`: liest
  `storage_roots.json` bei jedem Aufruf neu, Vorgabe `["records"]`, wird von
  keinem Programm geschrieben.
- Pfad-Sandbox: `eb200_core/path_sandbox.py`, `resolve_within()` gegen
  `sandbox_roots(<ROOT>)` = Repo-Root plus `storage_roots`. Verstoß → 403
  ohne Echo des Pfades.
- Parameter-Memory: `data/params/param_store.json`
  (`eb200_core/param_store.py`, `DEFAULT_STORE_PATH`).

### Dateiarten und ihre Erzeuger

| Dateiart | Erzeugt von | Ablageort | Nachbaubar |
|---|---|---|---|
| `session_JJJJMMTT_HHMMSS.db` | :8000, Session-Aufnahme im D-SCAN | `sessions/` | nein |
| `JJJJMMTT_HHMMSS_<kHz>_<Mode>.wav` + `.wav.json` | :8000, Audio im IFPAN-Zoom | Ablage-Wurzel | nein |
| `JJJJMMTT_HHMMSS_<kHz>_<Mode>.specbin` + `.specbin.json` | :8000, Spektrum im IFPAN-Zoom | Ablage-Wurzel | nein |
| `longrun_<Lauf-ID>_partNN.specbin` (bis T-1821a `_teilNN`) | Job-Runner, D-SCAN- oder IFPAN-Long-Run | `longrun_<Lauf-ID>/` | nein |
| `longrun_<Lauf-ID>_partNN.wav` (bis T-1821a `_teilNN`) | Job-Runner, Audio eines IFPAN-Long-Runs | `longrun_<Lauf-ID>/` | nein |
| `mscan_<Lauf-ID>_<MEM>_<Zeit>_<Nr>.wav` | Job-Runner, ein Segment je Squelch-Öffnung | `longrun_<Lauf-ID>/` | nein |
| `job.json` | Job-Store beim Anlegen des Laufs | `longrun_<Lauf-ID>/` | nein |
| `jobs/index.json` | Job-Store | `jobs/` | nein |
| `jobs/mscan/<ID>.json` | :8050, MSCAN-Definition | `jobs/mscan/` | nein |
| `known_freq/*.json` (+ `.bak`) | :8010, Pflegemaske | `known_freq/` | nein |
| `<name>.befund.json` | :8020 / :8040 bei der Auswertung | neben der Messdatei | **ja** |
| `<name>.wav.audspec` | :8070 beim ersten Öffnen | neben der WAV-Datei | **ja** |
| `<name>.klasse.json` | :8070, Audio-Klassifikation | neben der WAV-Datei | **ja** |
| `session_labels.json`, `spectrum_labels.json`, `audio_labels.json` | :8020 / :8040 / :8050 / :8070, Kurztexte | Repo-Wurzel | nein |
| `session_notes.json`, `spectrum_notes.json`, `audio_notes.json` | dieselben Server, längere Notizen | Repo-Wurzel | nein |
| `audio_klassen.json` | :8070, Handlabels | Repo-Wurzel | nein |
| `data/params/param_store.json` | alle Server | `data/params/` | ja |
| `logs/start_eb200_*.log` | Starter | `logs/` | nein |
| `logs/freeze_*.txt` | :8000, Scheduler ohne Lebenszeichen | `logs/` | nein |
| `station.json`, `storage_roots.json` | **von Hand** | Repo-Wurzel | nein |

## 8. Frequenzkatalog

`eb200_core/known_freq.py`:

- Verzeichnis `known_freq/`, alle `*.json` alphabetisch, kein Index.
- Blöcke `meta` (`service`, `layer`, `source`), `defaults`, `bands`,
  `entries`.
- Vererbte Felder aus `defaults`: `demod`, `bw_hz`, `squelch_dbuv`,
  `antenna`, `att`, `att_auto`, `afc`, `tags`, `notes`.
- `entries` mit `kind` = `channel` oder `raster`. Raster: `id_prefix`,
  `start_hz`, `stop_hz`, dazu `step_hz` **oder**
  `step_scheme: "airband_8k33"`, plus `label_template` mit `{mhz}`, `{khz}`,
  `{hz}`, `{n}`. Erzeugte Kennung `<id_prefix>-<label>`.
- `enabled: false` erzeugt nichts.
- Anzeigefelder: `site`, `programm`, `bundesland`, `pol`, `erp_w`.
- Prüfregeln: `id` aus `A–Z a–z 0–9 . _ -`, katalogweit eindeutig; `label`
  nicht leer; `freq_hz` ganzzahlig, 10 kHz bis 3 GHz; `demod` aus der
  Siebenerliste; `bw_hz` aus den zwölf Filterstufen; Segmente innerhalb ihres
  Bandes. Fehler sind nie fatal — Eintrag wird übersprungen, Grund gesammelt.
- Kollision einer `id`: alphabetisch frühere Datei gewinnt.
- Cache über Zeitstempel und Größe (`load_catalog_cached`) — Änderungen ohne
  Neustart wirksam.
- `load_station_config()` liest `station.json`, Schlüssel `region_tags`.
  Kanäle mit `land-*`-Tag außerhalb dieser Liste werden ausgeblendet; Kanäle
  ohne `land-*`-Tag bleiben immer sichtbar. Vorlage
  `station.example.json`, Datei selbst nicht versioniert.
- Tag `fnv`: Einzelfrequenzen aus dem Nutzungsplan, erscheinen in der
  Zuweisungsreihe, nicht in der Kanalreihe.
- Ebenen `zuweisung` und `dienst` (Vorgabe `dienst`).

`eb200_core/block_admin.py` (:8010):

- Kachelübersicht, Bearbeitungsblatt mit sechs Reitern (Bearbeiten,
  Datei-Kopf, JSON, Neuer Kanal, Neue Kanalgruppe, Neues Band).
- Neuanlage über den Knopf **+ Neue Katalogdatei** auf der Kachelübersicht
  (Blatt `sheet--neu`); Route `/known/newfile` als Rückfallebene.
- Frequenzeingabe in kHz mit deutscher Zahlenschreibweise, Speicherung in Hz.
- Antennenkennungen: `kw_dipol`, `kw_beam`, `yagi_6m`, `discone`.
- Schreibdisziplin: Probeladen des gesamten Katalogs in einem
  Temp-Verzeichnis; bei neuen Fehlern kein Schreiben. Danach `.bak` der
  Vorversion, dann atomares Ersetzen über eine Zwischendatei. `.bak` hält
  **eine** Generation (Befund A6).

Bestand im Auslieferungszustand: rund 4200 Kanäle, 170 Bänder, 18 Dateien;
Zuweisungsebene und Rundfunklisten sind österreichspezifisch.

## 9. Katalog-Streifen

Gemeinsamer JS-Block, wortgleich in `waterfall.py`, `analyze_sessions.py`,
`sdr_waterfall.py` und `analyze_spectrum.py`; abgeglichen über
`tools/sync_known_block.py`. Endpunkt `/api/known_freq`.

- Drei Reihen: Zuweisungsebene (mit Segment- und `fnv`-Marken), übrige Bänder
  (zwei Spuren bei Überlappung), Kanäle.
- Detailstufe nach Pixeldichte: Balken → Marken → Beschriftungen. Faustwerte:
  Marken ab etwa 4 MHz Fensterbreite, Beschriftungen ab etwa 2 MHz.
- Kanäle gleicher Frequenz werden gebündelt; Marke dicker und
  bernsteinfarben, Beschriftung mit „·" verbunden.
- Tooltip mit Programm, Standort, Bundesland, Polarisation, ERP; Bandzeilen
  darunter, Zuweisung zuerst; Hinweis „+n außerhalb der Region". Klick heftet
  fest, [Esc] löst.
- Schalter in `waterfall.py`: `knownToggle` mit Beschriftung **Katalog**,
  `knownRegionToggle` mit Beschriftung **Regional**. (Frühere Namen
  „Bekannte" und „Region" gelten nicht mehr — siehe `terminologie.md`.)

## 10. D-SCAN-Ansicht (:8000)

- Werkzeugleiste: `panelToggle`, `navToggle`, `jumpLiveBtn` (Zum Anfang),
  `pauseBtn`, `exportJpgBtn` (Als JPG speichern), `clearWfBtn` (Löschen),
  `gridToggle` (Raster), `markToggle` (Klick-Marker), `autoMarkToggle`
  (Auto-Marker) mit `detectCfg`, `detectListBtn` (Markerliste),
  `detectRestartBtn` (Restart), `knownToggle`, `knownRegionToggle`, `helpBtn`.
- Einstellungspanel, vier Blöcke: Scan-Bereich (`dscanStart`, `dscanStop`,
  `applyRangeBtn`); Bandbreite & Speed (`dscanBwSelect`, `speedSelect` mit
  LOW/NORM/HIGH/MTIM, `bwZoomBtn`, `mtimeInput`, `sweepEstimate`);
  Zusatz-Zoom mit SDR (`sdrToggle`, `sdrDeviceSelect`); Aufnahme.
- Farbskala: `scaleMin`, `scaleMax`, `autoScale`; getrennte Zustände für
  Übersicht und Zoom in `scaleState`, gespeichert unter dem
  localStorage-Schlüssel `eb200_wf_scale_v1`.
- Historie als Bildpuffer mit eigenem Scrollbalken; Größe **einmal beim
  Laden** aus der Fenstergröße (`VIEWPORT_ROWS_MIN = 300`), Änderung
  erfordert Neuladen und verwirft die Historie.
- JPG-Export (`exportWaterfallJpg`): Kopfzeile mit Bereich, kompletter
  Bildpuffer, 30-Sekunden-Zeitlinien, Frequenzachse; Qualität 0,92,
  Dateiname `MoniScope_waterfall_<Zeitstempel>.jpg`.
- Statusseite unter `/status`.

## 11. SDR-Wasserfall (:8030)

`sdr_waterfall.py`, Liste `SDR_DEVICES`:

| Schlüssel | Gerät | Bereich | Samplerates |
|---|---|---|---|
| `hackrf` | HackRF One | 1 MHz – 6 GHz | 2/4/8/10/16/20 MS/s |
| `airspy` | Airspy | 24 MHz – 1,8 GHz | 2,5/10 MS/s |

Ansteuerung über das native Vendor-CLI als Subprozess (`hackrf_transfer -r -`
bzw. `airspy_rx`). **Pfade stehen fest im Quelltext** — Befund A3b.
SoapySDR-Python wurde verworfen (Windows-Fehler bei `make()`).

## 12. Jobs und Aufzeichnungen (:8050)

`manage_jobs.py`:

- Seitenaufbau: Laufend, Geplant, Reiter **Aufzeichnungen** und
  **MSCAN-Scan-Definitionen**.
- Spalten (`REC_COLUMNS`): Art mit Herkunft, Kurztext, Start/Dauer, Inhalt,
  Zustand mit Abbruchgrund, Aufnahmen, Umfang, Ablage, Auswertung, Löschen.
- `ART_LABEL`: `dscan`, `ifpan`, `mscan`, `audio`. `HERKUNFT_LABEL`:
  `direkt`, `longrun`.
- Filter-Chips je Art und Herkunft mit Zählern, Suche im Kurztext.
- Kurztext editierbar über `/api/reclabel` (`kind`, `ref`).
- Zustand `verwaist` für Long-Run-Verzeichnisse ohne Job-Eintrag; Ablage
  `nicht erreichbar`, wenn das Manifest außerhalb der Sandbox liegt.
- Löschen gesperrt, solange etwas läuft oder geplant ist.
- Long-Run-Maske (in `waterfall.py`): Typ, Bereich bzw. Mitte/Span/Demod/BW,
  Start als `datetime-local`, Dauer 1/3/6/12/24 h oder Endzeitpunkt,
  Dezimierung (jeder Sweep, 5/15/30/60 s) bei D-SCAN, Rotation in Minuten,
  Audio parallel bei IFPAN, Ablageauswahl mit Budgetanzeige.

## 13. MSCAN

- Definitionsseiten in `:8050`: `/mscan`, `/mscan/job`, `/mscan/pick`.
  Endpunkte `/api/mscan/jobs|job|catalog|runs|create|update|channels|delete|start`.
- Kanalauswahl `/mscan/pick`: Filter nach Datei, Band, Tag und Freitext,
  „nur gewählte", Sammelaktionen „Gefilterte an/aus", Zähler gegen die
  Grenze von 1000.
- Läufe-Tabelle (`RUN_COLUMNS`): Start, Ende, Zustand, Phase, Zyklen,
  Aufnahmen, aktive Kanäle, verwaist (Vorlauf/dazwischen/Nachlauf im
  Tooltip), Ordner.
- Ergebnisansicht `:8060` (`analyze_mscan.py`), Reiter: Aktive Kanäle,
  Stille Kanäle, Aufnahmen, Zeitverlauf, Auswertung. Stille Kanäle lassen
  sich markieren und aus der Definition entfernen.
- Kernlogik in `eb200_core/mscan_view.py`
  (`build_mscan_run_result`, `resolve_run_file`).
- Betriebsart `inventur` (`MSCan:COUNt 1`) verlangt endliche Verweildauer;
  `ueberwachung` läuft bis zur Endzeit.

## 14. Auswerte-Server

- `:8020` `analyze_sessions.py`: eigene Liste plus „Pfad öffnen"; Ansichten
  Liste → Session → Segment; Skalenregler, Frequenzraster, Zeit-Gutter,
  Katalog-Streifen, Notizfeld (max. 4000 Zeichen).
- `:8040` `analyze_spectrum.py`: eigene Liste plus „Pfad öffnen"; Transport
  (Anfang, Frame zurück, Wiedergabe, Frame vor, Ende), Tempo 1 bis 50 Frames
  je Sekunde, Frame-Schieber; Spektrogramm-Gesamtansicht mit magenta
  markierten Lücken, Spektrum des aktuellen Frames mit Peak-Hold,
  Pegelverlauf der gewählten Frequenz.
- Beide: Befund-Navigator mit den Gruppen Dauerträger, bursthafte Kanäle,
  stärkste Ereignisse, Kamm, Periodik — **Klick-Navigation noch nicht
  umgesetzt** (Befund A11).
- `:8070` `analyze_audio.py`: Wiedergabe, Geschwisterdateien, Spektrogramm
  mit Zoom per Ziehen, FFT-Größe 128 bis 2048 (Vorgabe 1024), Marker A und B
  per Strg+Klick mit Differenzanzeige, Klassifikation automatisch und von
  Hand. Klassen: `LEER`, `RAUSCHEN`, `CW`, `DIGITAL`, `SPRACHE`, `UNKLAR`;
  `CLASSIFIER_VERSION = "eb200-audioclass-v4"`.

## 15. Oberfläche

- `eb200_core/ui_theme.py`: `THEME_CSS` (Light als `:root`, Dark als
  `[data-theme="dark"]`), `BASE_CSS`, `THEME_HEAD_JS`, `header_html()`.
  Persistenz über das Cookie `eb200_theme` auf Host-Ebene, damit die Wahl
  portübergreifend gilt. Canvas-Flächen bleiben in beiden Themes dunkel.
- **Bisher nur eingesetzt in `manage_jobs.py` und `analyze_mscan.py`** —
  Befund A8.
- Regel für Bedienglyphen: Inline-SVG mit `fill:currentColor`, nie
  Unicode-Zeichen mit Emoji-Darstellung. **Noch nicht überall umgesetzt** —
  Befund A9.

## 16. IQ-Aufnahme und Signalanalyse

Erhoben 2026-09-02 gegen `waterfall.py`, `eb200_core/audio_wav.py` und
`tools/eye_diagram.py` (Taskserie T-901 bis T-906).

- Audio-Betriebsmodi (`AUDIO_REMOTE_MODES` in `eb200_core/audio_wav.py`):
  `AUDIO_REMOTE_MODE = 6` (16 kHz, 16 Bit, mono) im Normalfall,
  `AUDIO_REMOTE_MODE_IQ = 1` (32 kHz, 16 Bit, 2 Kanäle) bei `DEM IQ`.
  Umschaltung in `_current_audio_mode()`.
- `_configure_ifpan()` begrenzt die ZF-Bandbreite bei `DEM IQ` auf 15 kHz
  (Kommentar: breitere ZF würde bei 32 kHz komplexer Abtastung hineinfalten);
  der Rückleseabgleich zieht die Auswahlliste nach.
- `_start_monitoring()` verwirft den Mithörwunsch bei `DEM IQ`
  (`monitor_wanted = False`, Konsolenzeile) — der Browserpfad ist fest
  einkanalig mit 16 kHz (`X-Audio-Format`).
- `POST /api/job` lehnt `demod == "IQ"` zusammen mit `audio: true` mit
  HTTP 400 ab („IQ-Demodulation mit Job-Audio wird nicht unterstuetzt").
- Sidecar-Feld `is_iq` aus `meta["mode"] == "IQ"` und `channels == 2` —
  UI-Einstellung, kein Geräterückabgleich (Befund A14).
- Verstärkungsregelung (T-905): Panelblock `Verstaerkung` mit `gainMode`
  (`AGC`/`MGC`) und `gainMgc` (−30 bis 110 dBµV, Vorgabe 50), Endpunkt
  `/api/gain`, SCPI `GCON:MGC` dann `GCON:MODE`, `_apply_gain()` bei jedem
  Zoom-Eintritt. `/api/gain` steht in `_LOCKED_GET_PATHS`. Rückspiegelung
  über `GCON:MODE?`/`GCON:MGC?` im Parameter-Polling.

### `tools/eye_diagram.py`

- Reines Offline-Werkzeug, numpy auf Modulebene, SVG ohne externe Verweise.
- `--mode fsk` (Vorgabe) → `<name>.eye.svg`; `--mode psk` → `<name>.psk.svg`
  (`os.path.splitext(wav)[0]` plus Endung). `--json` nur im FSK-Pfad.
- Optionen: `--out`, `--json`, `--baud`, `--burst`, `--max-traces` (400),
  `--decimate` (1), `--min-baud` (20.0), `--suggest`, `--mode`, `--selftest`.
- Rückgabewerte: 0 in Ordnung, 1 Eingabe-/Dateifehler, 2 Auswertung nicht
  möglich (`RuntimeError`).
- `apply_decimation()` fordert `fs_eff >= 4 * min_baud` — daraus die
  Obergrenze von etwa 8 kBd bei 32 kHz ohne Dezimation.
- Sidecar mit `is_iq: false` → Abbruch; fehlende Sidecar → nur Warnung,
  Abtastrate aus dem WAV-Kopf.
- Konstanten mit Messdatum 2026-09-01 (Begründungen im Quelltextkommentar,
  Kurzfassung im Entscheidungslog von PLAN.md): `FLATNESS_THR = 0.94`,
  `BAUD_TOL_ZUFALLSFOLGE = 0.06`, `BAUD_TOL_WECHSELFOLGE = 0.01`,
  `SCHAERFE_FAKTOR = 4.0` (Schwelle `4 * ln(N)`).
- Meldungen: „Symbolrate aus Wechselfolge geschaetzt, Unsicherheit unter
  1 %." / „Symbolrate aus Zufallsfolge geschaetzt, typische Unsicherheit
  ca. 3 %."; Abbrüche „Autokorrelationsflanke zu kurz, Symbolrate nicht
  bestimmbar" und „Symbolrate nicht bestimmbar (Huellkurve zu flach)".
