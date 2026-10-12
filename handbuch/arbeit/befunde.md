# MoniScope — Befunde aus der Handbucharbeit

Stand: 2026-08-29. Erhoben beim Entwurf der Handbuchkapitel 1–18 gegen
`EB200_sources.txt` (Stand der Session).

Dieses Dokument ist **kein CC-Prompt**. Es sammelt, was beim Schreiben des
Handbuchs an Unklarheiten, Widersprüchen und Verbesserungsmöglichkeiten
aufgefallen ist. Es ist als Arbeitsgrundlage für eine getrennte
Umsetzungssitzung gedacht. Jeder Punkt nennt Befund, Fundstelle und
Handlungsvorschlag; die Entscheidung selbst ist offen.

Reihenfolge: erst Punkte, die den Code betreffen, dann offene Fragen, dann
reiner Handbuchbedarf. Innerhalb der Gruppen grob nach Gewicht.

---

## A. Code-Befunde

### A1 — numpy ist Pflichtabhängigkeit des Hauptservers (Regelverstoß)

**Befund.** `waterfall.py` importiert `eb200_core.signal_detect`
(Auto-Marker, T-505). `eb200_core/signal_detect.py` importiert `numpy` auf
Modulebene, ohne `try`/`except`. Damit gilt:

- Ohne numpy startet `:8000` überhaupt nicht — kein Betrieb ohne das Paket.
- Die Projektregel „`eb200_core/` nur Standardbibliothek, numpy nur in
  Modulen im Repo-Root" ist verletzt.

Betroffen sind außerdem `sdr_waterfall.py` (:8030), `audio_spectrum.py` und
`audio_classify.py` (über :8070) sowie `session_analyze.py`,
`tools/classify_eval.py` und `tools/eye_diagram.py` (T-902 bis T-906) — dort
ist numpy aber als Root- beziehungsweise Werkzeugmodul regelkonform. Der
Modulkopf von `eye_diagram.py` nennt numpy ausdrücklich als
Pflichtabhängigkeit des Projekts; das stützt die dritte Variante unten.
Ergänzt 2026-09-02.

**Auswirkung aufs Handbuch.** Mein Entwurf zu Kapitel 4 behauptet, die
Kern-Server kämen mit der Standardbibliothek aus und numpy sei nur für
:8030 und :8070 nötig. Das ist falsch und muss so oder so korrigiert werden.

**Vorschlag.** Entweder `signal_detect.py` nach Repo-Root verschieben (dann
ist die Regel gewahrt, numpy bleibt aber Pflicht für :8000), oder den Import
absichern und den Auto-Marker ohne numpy abschalten (dann bleibt :8000
stdlib-only lauffähig), oder die Regel bewusst ändern und numpy als
Grundvoraussetzung dokumentieren. Die dritte Variante ist die ehrlichste,
wenn der Auto-Marker als Kernfunktion gilt.

### A2 — Ablage-Asymmetrie bei Direktaufnahmen

**Befund.** In `waterfall.py`:

- `SESSION_DIR = "sessions"` — relativ zum Arbeitsverzeichnis, also im
  Repo-Root.
- `RECORD_DIR = load_storage_roots()[0]` — konfigurierbares Ablageziel.

Direkt aufgenommene Sessions landen damit **immer** im Repo, während
Direkt-Audio und Direkt-Spektren dem konfigurierten Ziel folgen.

**Auswirkung.** Wer `storage_roots.json` auf ein großes Laufwerk legt, um
Platz zu sparen, erwischt die Sessions nicht — und die sind bei
Langzeitaufnahmen die größten Dateien. Die Pfad-Sandbox deckt beide Wurzeln
bereits ab, das Problem liegt allein auf der Schreibseite.

**Vorschlag.** `SESSION_DIR` ebenfalls an `storage_roots` binden (Migration
bestehender `sessions/` bedenken, Auswertungslisten in :8020 prüfen) — oder
die Asymmetrie bewusst behalten und im Handbuch als Warnhinweis führen. Eine
Entscheidung ist nötig, bevor Kapitel 5 und 13 geschrieben werden.

### A3 — Konfiguration steckt im Quelltext

Zwei Stellen derselben Art. Für ein Handbuch, das fremde EB200-Besitzer
lesen, ist „bearbeiten Sie eine Zeile im Quelltext" keine tragfähige
Anleitung.

**A3a — Empfänger-Adresse.** `waterfall.py`:

    EB200_IP = "10.0.0.55"
    SCPI_PORT = 5555
    LOCAL_IP = None

`station.json` existiert bereits (Regionsfilter, `load_station_config()` in
`eb200_core/known_freq.py`, nicht versioniert, Vorlage
`station.example.json`) und wäre der natürliche Ort. Vorschlag: die beiden
Schlüssel `eb200_ip` und `local_ip` dort mitlesen, Vorgabe = die bisherigen
Konstanten, fehlende Datei bleibt zulässig. Werkzeuge mit eigenem `--host`
bleiben unverändert.

**A3b — SDR-Werkzeugpfade.** `sdr_waterfall.py`, `SDR_DEVICES`:

    cmd=r"C:\HackRF\bin\hackrf_transfer.exe"
    cmd=r"C:\airspy\airspy_rx.exe"

Dazu der Quelltextkommentar, Pfad und `-a`/`-t`-Flags seien anzupassen.
Gleiche Klasse von Problem; gehört in dieselbe Konfigurationsdatei oder in
eine eigene `sdr.json`.

### A4 — Kurztexte und Notizen liegen zentral statt als Sidecar

**Befund.** Die Suite hält sonst konsequent Sidecars neben der Messdatei
(`.specbin.json`, `.wav.json`, `.befund.json`, `.klasse.json`,
`.wav.audspec`). Kurztexte und Notizen sind die Ausnahme: sie liegen als
`session_labels.json`, `session_notes.json`, `spectrum_labels.json`,
`spectrum_notes.json`, `audio_labels.json`, `audio_notes.json` und
`audio_klassen.json` in der Repo-Wurzel, zugeordnet über den **relativen
Pfad** der Aufnahme.

**Auswirkung.** Wird eine Aufnahme verschoben oder umbenannt, verliert sie
still ihren Kurztext, ihre Notiz und ihr Handlabel. Das ist besonders
unangenehm, weil das Verschieben auf ein anderes Laufwerk ausdrücklich
vorgesehen ist (siehe A2).

**Vorschlag.** Entweder als bewusste Entscheidung dokumentieren (dann ins
Handbuch, Kapitel 13, mit Warnhinweis „Aufnahmen nicht außerhalb der Suite
verschieben"), oder mittelfristig auf Sidecars umstellen. Zwischenlösung:
beim Löschen/Verschieben über die Oberfläche die Zuordnung mitziehen.

### A5 — MSCAN scheitert an einem offenen Mithören-Fenster

**Befund.** `_run_mscan_loop()` Phase a prüft `session_active`,
`self.spectrum_recording` und `self.monitoring`. Trifft eines zu, startet der
Lauf gar nicht, sondern geht sofort in `ST_ABGEBROCHEN` mit Begründung.
`self.monitoring` hängt an der Anwesenheit von `/audio/stream`-Clients.

**Auswirkung.** Wer abends den IFPAN-Zoom mit Mithören offen lässt und den
Browser nur zuklappt, riskiert, dass der für 22 Uhr geplante MSCAN-Lauf
sofort abbricht. Die Begründung steht im Job — sie liest um 22 Uhr niemand.

**Vorschlag.** Mindestens eine Warnung bei der Job-Anlage („zur geplanten
Startzeit darf kein Mithören/keine Direktaufnahme laufen"). Besser: kurz vor
Laufbeginn das Mithören serverseitig beenden, statt den Lauf abzubrechen —
Mithören ist flüchtig, ein geplanter Lauf nicht.

### A6 — `.bak` hält nur eine Generation

**Befund.** `_write_known_file()` in `eb200_core/block_admin.py` schreibt vor
jedem Speichern eine `.bak`-Kopie der Vorversion. Zweimaliges Speichern
derselben Katalogdatei überschreibt die Sicherung.

**Vorschlag.** In der Oberfläche einen Hinweis anzeigen, oder mehrere
Generationen halten. Fürs Handbuch ist der Hinweis ohnehin nötig.

### A7 — `alert()` in `waterfall.py`

**Befund.** Der Fehlerfall beim Start der Session-Aufzeichnung meldet sich
über `alert()`. Fällt unter die bereits geplante Umstellung auf native
`<dialog>`-Elemente, betrifft also nicht nur `manage_jobs.py`.

### A8 — Theme-Umstellung erst teilweise ausgerollt

**Befund.** `eb200_core/ui_theme.py` (Light als Standard, Dark wählbar,
Cookie `eb200_theme`) wird bisher nur von `manage_jobs.py` (:8050) und
`analyze_mscan.py` (:8060) genutzt. `waterfall.py`, `block_admin.py`,
`analyze_sessions.py`, `analyze_spectrum.py` und `analyze_audio.py` tragen
weiterhin ihr eigenes dunkles CSS.

**Auswirkung aufs Handbuch.** Screenshots aus verschiedenen Servern sähen
derzeit unterschiedlich aus. Solange die Umstellung läuft, sollten
Bildschirmfotos zurückgestellt werden — sonst müssen sie zweimal gemacht
werden.

### A9 — Bedienglyphen als Unicode statt Inline-SVG

**Befund.** Entgegen der festgelegten Regel stehen in mehreren Servern noch
Emoji-fähige Unicode-Zeichen in Bedienelementen, unter anderem:

- `waterfall.py`: `&#9208;` (Pause), `&#9198;` (Zum Anfang), `&#128444;`
  (Als JPG speichern), `&#128465;` (Löschen), `&#128264;` (Mithören),
  `&#9210;` (Aufnahmeknöpfe), `&#9654;` (Weiter)
- `analyze_spectrum.py`: die fünf Transportknöpfe `&#9198; &#9194; &#9654;
  &#9193; &#9197;`

Unter Windows rendern die als farbige Emoji.

**Vorschlag.** Bestandsaufnahme aller betroffenen Stellen und Umstellung auf
`.glyph`-Inline-SVG, bevor Screenshots fürs Handbuch entstehen.

### A10 — „Spektrum"-Link in :8060 bei externem Ablageziel ausgegraut

**Befund.** `eb200_core/mscan_view.build_mscan_run_result()` erzeugt den Link
je Segment auf :8070 nur, wenn `job_dir` real unterhalb des Repo-Roots liegt;
sonst ist der Link ausgegraut. Im Quelltext als offener Punkt markiert.

**Auswirkung.** Wer die Ablage auf ein externes Laufwerk legt — also genau
das, was `storage_roots.json` ermöglichen soll —, verliert in der
MSCAN-Trefferliste den Weg zur Audio-Auswertung. Passt zu A2: die
Sandbox-Whitelist kennt externe Wurzeln, diese Prüfung nicht.

### A11 — Befund-Navigator ist Gerüst ohne Funktion

**Befund.** In `analyze_sessions.py` und `analyze_spectrum.py` trägt der
Befund-Navigator die Überschrift „(Auto-Analyse; Klick markiert – folgt)"
bzw. „(Klick springt – folgt)". Die Gruppen (Dauerträger, bursthafte Kanäle,
stärkste Ereignisse, Kamm, Periodik) sind angelegt, die Klick-Navigation ist
nicht umgesetzt.

**Auswirkung aufs Handbuch.** Kapitel 11.1 und 11.2 dürfen die Funktion nicht
als vorhanden beschreiben. Entweder vor dem Release nachziehen oder die
Platzhaltertexte entfernen, damit das Handbuch nichts beschreiben muss, was
nur halb da ist.

### A12 — Zoom-Neuaufbau beendet eine laufende Audio-Aufnahme stillschweigend

**Befund.** `_run_zoom_loop()` in `waterfall.py` verarbeitet nur wenige
Kommandos inline (`record_*`, `spectrum_*`, `monitor_*`, `squelch`, `tune`);
`span`, `mode`, `bw` und ein neuer `zoom` verlassen die Schleife über
`return cmd`. Der `finally`-Pfad ruft dabei `_stop_spectrum_recording()`,
`_stop_monitoring()` und `_stop_recording()` — die laufende WAV-Datei wird
also geschlossen, bevor der Zoom mit den neuen Parametern wieder aufgebaut
wird. Das Mithören wird beim Wiedereintritt über `_apply_monitor_wish()`
wiederhergestellt, die Audio-Aufnahme nicht.

Im Browser sperrt `setGeometryLock()` Span, Modulation, Bandbreite,
**← Zurück zur Übersicht** sowie Klick und Ziehen nur während einer
**Spektrum**-Aufnahme (`spectrumRec`); die Audio-Aufnahme (`audioRec`) ruft
`setGeometryLock` nicht. Für die WAV-Aufnahme gibt es außerdem keine
Statusrückmeldung an den Client — der Knopf zeigt nach dem serverseitigen
Stopp weiterhin „aktiv" samt weiterlaufender Zeitanzeige.

**Auswirkung.** Wer während einer Audio-Aufnahme den Span ändert oder in den
Wasserfall klickt, hat eine abgeschnittene WAV-Datei und einen Knopf, der das
Gegenteil behauptet. Nur ein zweiter Klick auf den Knopf bringt die Anzeige
wieder in Deckung — eine zweite Aufnahme startet dabei nicht.

**Vorschlag.** Entweder `setGeometryLock` auch aus `audioRec` heraus rufen
(dann sind die geometrieverändernden Elemente während jeder Aufnahme
gesperrt, analog zur Spektrum-Aufnahme), oder den Aufnahmezustand als
Statusfeld an den Client melden und den Knopf beim serverseitigen Stopp
zurücksetzen. Die erste Variante ist die einfachere und passt zum bereits
etablierten Muster.

Bis zur Entscheidung steht in Kapitel 9.9 ein Warnhinweis.

### A13 — `PULSE` im Katalog, aber nicht in der IFPAN-Auswahl

**Befund.** `DEMOD_TOKENS` in `eb200_core/known_freq.py` erlaubt sieben
Werte (`AM`, `FM`, `USB`, `LSB`, `CW`, `IQ`, `PULSE`), `IFPAN_MODE_OPTIONS`
in `waterfall.py` führt nur sechs — ohne `PULSE`. Die Auswahlliste im
IFPAN-Zoom kann diesen Wert also weder anzeigen noch setzen; `/api/mode`
weist ihn mit HTTP 400 ab, und `syncZoomSettings()` ignoriert unbekannte
Werte ausdrücklich, lässt die Liste also auf dem alten Eintrag stehen.

**Auswirkung.** Ein Katalogkanal mit `demod: PULSE` ist zulässig, aber im
Zoom nicht nachvollziehbar; steht das Gerät auf `PULSE`, zeigt die
Auswahlliste einen falschen Wert an, ohne dass etwas darauf hinweist. Auch
`arbeit/fakten.md`, Abschnitt 5, nannte bisher sieben Demodulationsarten für
den IFPAN — korrigiert am 2026-09-01.

**Vorschlag.** Entweder `PULSE` in `IFPAN_MODE_OPTIONS` aufnehmen (dann ist
die Liste deckungsgleich mit dem Katalog), oder `PULSE` aus `DEMOD_TOKENS`
streichen, falls die Betriebsart nicht unterstützt werden soll. Ein dritter
Weg wäre eine Prüfregel im Katalog, die `PULSE` als „nicht im Zoom
darstellbar" markiert — der Aufwand lohnt kaum.

### A14 — `is_iq` in der Sidecar wird nicht vom Gerät zurückgelesen

**Befund.** `WavAudioWriter._write_sidecar()` in `eb200_core/audio_wav.py`
setzt `"is_iq": (str(self.meta.get("mode", "")).upper() == "IQ" and
self.channels == 2)`. Das `mode`-Feld stammt aus `_start_recording()` in
`waterfall.py` und damit aus `self.ifpan_mode`, also aus der Auswahl in der
Oberfläche — nicht aus einem `DEM?`-Rückabgleich am Gerät.

Der Rückleseabgleich existiert an anderer Stelle sehr wohl: `_configure_ifpan`
liest `DEM?`/`BAND?` zurück und zieht den Controller-Zustand nach. Zwischen
dieser Rücklesung und dem Start einer Aufnahme kann sich der Gerätezustand
aber ändern (Frontpanel), und der Sidecar-Wert wird dabei nicht geprüft.

**Auswirkung.** `tools/eye_diagram.py` bricht ab, wenn die Sidecar
`is_iq: false` meldet, verlässt sich im umgekehrten Fall aber darauf. Eine
Aufnahme, die fälschlich `is_iq: true` trägt, läuft in die Auswertung und
liefert Unsinn statt einer klaren Fehlermeldung. Zweikanaligkeit und 32 kHz
sind die einzigen verbleibenden Anhaltspunkte.

**Vorschlag.** Beim Start der Aufnahme `DEM?` abfragen und das Ergebnis in die
Sidecar schreiben, oder — billiger — `is_iq` zusätzlich an
`audio_remote_mode == 1` knüpfen, denn der Modus wird aus derselben Quelle
gesetzt und wäre bei einem Auseinanderlaufen wenigstens konsistent geprüft.

Bis zur Entscheidung steht in Kapitel 9.11 ein Warnhinweis.

---

## B. Offene Fragen (vor dem Schreiben zu klären)

### B1 — Was passiert mit einer laufenden Session beim Zoom-Wechsel?

Ich finde keine Stelle, die eine laufende Session-Aufzeichnung beim Wechsel
in den IFPAN-Zoom beendet. Der `SessionStore` bleibt offen, es kommen nur
keine D-SCAN-Sweeps mehr — die Datei bekommt also ein zeitliches Loch von der
Länge des Zoom-Aufenthalts.

Zu klären: Ist das gewollt? Weist die Oberfläche darauf hin? Wie stellt :8020
die Lücke dar (eigenes Segment, sichtbarer Sprung in der Zeitachse)? Ohne
Antwort kann Kapitel 8.6 die Session-Aufnahme nicht ehrlich beschreiben.

### B2 — Verhalten ohne Geräteoptionen

Mein Entwurf zu Kapitel 4 behauptet: ohne EB200DS bleiben IFPAN und M-SCAN
nutzbar, ohne EB200SU bleiben D-SCAN, Audio und M-SCAN nutzbar. Das ist aus
der Gerätelogik abgeleitet, **nicht** am Code oder an Hardware verifiziert.
Insbesondere unklar: Wie verhält sich `:8000` beim Start, wenn die
D-SCAN-Konfiguration vom Gerät abgelehnt wird? Bricht der Server ab oder
bleibt die Übersicht leer?

Da die Zielgruppe „alle EB200-Besitzer" ist und Geräte ohne diese Optionen
verbreitet sind, ist das keine Randfrage.

### B3 — Verhalten ohne numpy

Siehe A1. Wenn numpy Pflicht bleibt, muss Kapitel 4 es als Voraussetzung
nennen und Kapitel 16 den Startfehler beschreiben (Server erscheint im
Statusboard als BEENDET, Traceback in Zone 3).

### B4 — Betrieb ohne TTY / auf einem Raspberry Pi

Statusboard, Tastatursteuerung und die Lebenszeit-Kopplung über das
Windows-Job-Objekt sind Windows-spezifisch. Für einen Pi-Betrieb fehlt eine
belastbare Aussage: Läuft `start_eb200.py` dort im Plain-Modus sauber durch?
Was ersetzt die Lebenszeit-Kopplung? Solange das offen ist, bleibt Kapitel 4
bei „Windows erprobt, Linux vorgesehen".

---

## C. Korrekturen an bereits entworfenen Kapiteln

Diese Punkte betreffen Text, den ich schon geschrieben habe.

### C1 — Falsche Namen der Toolbar-Schalter (Kapitel 6)

Im Entwurf zu Kapitel 6 heißen die Schalter in :8000 **Bekannte** und
**Region**. Seit T-707 heißen sie **Katalog** und **Regional**. Betrifft die
Abschnitte 6.5 (Regionsfilter) und 6.7 (Katalog-Streifen). Ursache: Ich hatte
die Bezeichnungen aus `WS3_-_Wasserfall.md` übernommen statt aus dem
aktuellen Markup — die Entwurfsdokumente sind an dieser Stelle veraltet.

### C2 — Falsche numpy-Aussage (Kapitel 4)

Siehe A1. Der Satz „Die Kern-Server kommen mit der Standardbibliothek aus"
ist zu streichen oder einzuschränken.

### C3 — „Neue Datei" in :8010 (Kapitel 6)

Geprüft und geklärt: Die Neuanlage ist ein eigenes Blatt **auf der
Kachelübersicht** (Knopf „+ Neue Katalogdatei", Blatt `sheet--neu`), nicht
ein Reiter im Bearbeitungsblatt einer bestehenden Datei. Die Route
`/known/newfile` besteht als Rückfallebene. Kapitel 6.9 ist entsprechend zu
formulieren.

### C4 — Rückwege aus den Deep-Link-Servern (Kapitel 7/11)

Geprüft und geklärt: :8060 hat drei Rücklinks (Aufzeichnungen/Definitionen,
die zugehörige Definition, Long-Run-Jobs), :8070 einen („zurück zur Liste
(:8050)"). Der Browser-Zurück-Knopf ist also nicht der einzige Weg. Meine
Formulierung im Navigationsdiagramm („nur per Link erreichbar") bleibt
richtig, sollte aber im Text um die Rückwege ergänzt werden.

---

## D. Reiner Handbuchbedarf (kein Codefehler)

### D1 — Screenshots fehlen durchgehend

Vorgesehene Platzhalter bisher: Statusboard (Kapitel 5), Katalog-Streifen und
Bearbeitungsblatt (Kapitel 6). Weitere folgen in Teil C. **Wegen A8 und A9
sollten Screenshots erst nach der Theme- und Glyphen-Umstellung entstehen.**

### D2 — Katalog ist Österreich-zentriert

Der mitgelieferte Bestand (rund 4200 Kanäle, 170 Bänder, 18 Dateien) enthält
den nationalen Frequenznutzungsplan Österreichs sowie österreichische
Rundfunk-Senderlisten. Für die Zielgruppe muss klar dokumentiert sein, welche
Dateien austauschbar sind und wie eine eigene nationale Zuweisungsdatei
aufgebaut wird. Ist im Entwurf zu Kapitel 6.11 bereits so angelegt.

### D3 — Zwei Dinge heißen „Marker"

Klick-Marker (Handmarken) und Auto-Marker (Detektionsereignisse mit eigener
Liste, CSV-Export, Δ-Schwelle, Mindestdauer) tragen denselben Wortstamm. Im
Handbuch werden sie sauber getrennt. Zu überlegen wäre, ob die Oberfläche
deutlicher benennen sollte — etwa „Detektion" statt „Auto-Marker".

### D4 — Gerätewissen, das ins Handbuch gehört

Sammelpunkte für Kapitel 15, damit sich niemand über Nebenwirkungen wundert:

- **Squelch ist am Gerät global**, nicht auf den IFPAN-Zoom beschränkt.
- **`MEAS:TIME` ist global.** Die Suite setzt ihn bei jedem Speed außer
  `MTIM` aktiv auf `DEFAULT` zurück, damit er nicht in andere Messfunktionen
  hineinwirkt.
- **Demodulation wird beim Aufbau des D-SCAN bedingungslos auf FM gesetzt.**
  Eine am Frontpanel gewählte SSB-Betriebsart überlebt die Rückkehr in die
  Übersicht nicht. Technisch begründet (SSB verträgt keine Bandbreiten über
  9 kHz), für den Bediener aber überraschend.
- **Gerätegrenzen:** höchstens zehn gleichzeitige TCP-Steuerverbindungen,
  höchstens vier UDP-Ziele.
- **UDP-Pfade überleben einen Geräteneustart nicht** und können nach einem
  Absturz der Suite stehen bleiben; die MSCAN-Vorbereitung räumt sie deshalb
  vorab ab, ein Fehler dabei ist der Normalfall und kein Startfehler.

### D5 — Fenstergröße verwirft die Historie

Breite und Höhe der Wasserfall-Canvas werden einmal beim Laden aus der
Fenstergröße bestimmt. Wer das Fenster wesentlich ändert, bekommt einen
Hinweis mit Knopf „Neu laden" — und verliert dabei die Historie, weil sie
rein im Browser liegt. Gehört als Hinweis in Kapitel 8.5.

### D6 — Pause hält nur die Anzeige an

Eine laufende Session- oder Direktaufnahme schreibt während „Pause" weiter.
Muss deutlich im Handbuch stehen, sonst hält jemand Pause für einen
Aufnahmestopp. Kapitel 8.4.

### D7 — JPG-Export ist ein Foto, kein Datenexport

`exportWaterfallJpg()` gibt genau den Pixelpuffer aus, mit eingebrannter
Kopfzeile, Zeitmarken und Frequenzachse. Wer Zahlen braucht, nimmt die
Markerliste als CSV oder eine Session-Aufnahme. Kapitel 8.4.

### D8 — Inventur braucht endliche Verweildauer

Bei Job-Modus „Inventur" (`MSCan:COUNt 1`) **muss** `dwell_s` endlich sein,
sonst hält ein Dauerträger den Squelch offen und der Durchlauf bleibt auf
diesem Kanal stehen. `INVENTUR_DEFAULTS` schlägt 3,0 s vor, die Serverseite
erzwingt nur die Regel „endlich und > 0". Gehört als Warnhinweis in
Kapitel 10.

### D9 — `station.json` ist nicht versioniert

Vorlage ist `station.example.json`. Erklärungsbedürftig, sobald die Datei
laut A3a auch die Empfänger-Adresse trägt: Ein Betreiber, der das Repo
aktualisiert, darf seine Konfiguration nicht verlieren.

---

## E. Was bereits vorliegt

- Kapitelstruktur Teil A–E festgelegt, Zielgruppe „alle EB200-Besitzer",
  Format Markdown unter `docs/handbuch/` (ein Kapitel je Datei), Releases
  später per Typst.
- Teil A (Kapitel 1–5) und Teil B (Kapitel 6) im Entwurf fertig, vorbehaltlich
  der Korrekturen C1–C3.
- Zwei Grafiken entworfen: Navigationskarte der Ansichten
  (`07_ansichten.svg`) und Ablagestruktur (`13_ablage.svg`), beide als
  eigenständige SVG mit festen Farbwerten.
- Tabelle „welche Dateiart erzeugt welcher Prozess" für Kapitel 13 erstellt
  (20 Zeilen, mit Spalte „nachbaubar" = gefahrlos löschbar).
- Inhalte für Kapitel 7–11 erhoben, noch nicht ausformuliert.
