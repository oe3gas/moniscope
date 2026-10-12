# Terminologie

Stand: 2026-09-02.

Zweck: einheitliche Benennung im ganzen Handbuch, und eine Stelle, an der die
Bedienelemente **wortgetreu so stehen, wie sie in der Oberfläche
erscheinen**. Anlass war ein Fehler im ersten Entwurf: Zwei Schalter wurden
mit ihren alten Namen aus den Entwurfsdokumenten beschrieben statt mit den
aktuellen aus dem Quelltext.

**Regel:** Bedienelemente werden aus dem Markup abgeschrieben, nicht aus
`WS*.md` oder `PLAN.md`. Die Entwurfsdokumente hinken der Oberfläche
hinterher.

---

## 1. Begriffe der Suite

| Begriff | Bedeutung | Nicht verwenden |
|---|---|---|
| **Session** | fortlaufend mitgeschriebene D-SCAN-Sweeps in einer Datenbank | „Mitschnitt", „Scan" |
| **Spektrum-Aufnahme** | IFPAN-Bins als `.specbin` | „Spektrogramm" (das ist die Darstellung) |
| **Audio-Aufnahme** | WAV aus IFPAN oder MSCAN | „Mitschnitt" |
| **Aufnahme** | der Vorgang des Aufzeichnens | — |
| **Aufzeichnung** | die entstandene Datei, der Listeneintrag | — |
| **Job** | die Definition (Kanäle, Parameter, Zeitplan) | „Auftrag" |
| **Lauf** | eine Ausführung eines Jobs, eingefroren | „Durchlauf", „Session" |
| **Direkte Aufnahme** | per Knopf gestartet, sofort | „manuelle Aufnahme" |
| **Long-Run-Job** | geplant, serverseitig, Parameter eingefroren | „Langzeitjob", „Dauerlauf" |
| **Sidecar** | Begleitdatei neben der Messdatei | „Metadatei" |
| **Befund** | Ergebnis einer Auswertung, neu berechenbar | „Analyse", „Report" |
| **Kurztext** | die eine Zeile in der Aufzeichnungsliste | „Label", „Titel" |
| **Notiz** | der längere Freitext in der Auswertung | „Kommentar" |
| **Katalog-Streifen** | die beschriftete Leiste an der Frequenzachse | „Bandleiste" |
| **IQ-Aufnahme** | zweikanalige WAV mit den Basisbandwerten, 32 kHz | „IQ-Datei", „Rohaufnahme" |
| **Augendiagramm** | übereinandergelegte Symbolspuren im FSK-Pfad | „Eye Pattern" |
| **Konstellation** | Punktwolke aus Phase und Amplitude im PSK-Pfad | „Konstellationsdiagramm" (nur als Bildunterschrift) |
| **Ablage-Wurzel** | erster Eintrag aus `storage_roots.json` | „Speicherort" |

## 2. Schreibweisen

| Richtig | Falsch |
|---|---|
| D-SCAN | DSCAN, dscan, D-Scan |
| M-SCAN (Betriebsart) / MSCAN (Job, Lauf, Ansicht) | Mscan, M-Scan durchgehend |
| BW-Zoom (Fließtext) / BW-ZOOM (Knopfbeschriftung, wörtlich zitiert) | BWZOOM, BW Zoom |
| IFPAN | IF-PAN, IfPan |
| EB200 | EB-200, eb200 |
| ZF-Bandbreite | IF-Bandbreite (nur im Zitat aus dem Originalhandbuch) |
| dBµV | dBuV (nur im Quelltext zulässig) |
| Squelch | Rauschsperre (uneinheitlich mit der Oberfläche) |
| Sweep | Durchlauf, Abtastung |
| Speicherplatz (MEM0 bis MEM999) | Speicher, Kanalspeicher |

Die Suite heißt im Handbuch durchgehend **EB200 Suite**; der Produktname
**MoniScope** erscheint in der Oberfläche und wird dort zitiert.

## 3. Bedienelemente, wortgetreu

### :8000 — Live-Wasserfall, Werkzeugleiste Übersicht

`⚙ Einstellungen`, `☰ Ansichten`, `Zum Anfang`, `Pause` / `Weiter`,
`Als JPG speichern`, `Löschen`, `Raster`, `Klick-Marker`, `Auto-Marker`,
`Markerliste`, `Restart`, `Katalog`, `Regional`, `?`

> **Nicht** „Bekannte" und **nicht** „Region" — diese Namen galten vor T-707.

### :8000 — Einstellungspanel Übersicht

Blöcke: `Scan-Bereich`, `Bandbreite & Speed`, `Zusatz-Zoom mit SDR`,
`Aufnahme`.
Felder: `Start (MHz)`, `Stop (MHz)`, `Bereich übernehmen`, `BW`, `Speed`,
`BW-ZOOM einschalten` / `BW-ZOOM ausschalten`, `MTIME (ms)`, `Wähle SDR`.
Der BW-ZOOM-Knopf ist mit der Aktion beschriftet, nicht mit dem Zustand.
Speed-Werte in der Auswahl: `LOW`, `NORMAL`, `HIGH`, `MTIME`.

### :8000 — Auto-Marker-Einstellungen

`Δ Schwelle`, `automatisch`, `Mindestdauer`, `Marker mit Frequenz`.
Markerliste: Spalten `Zeit`, `MHz`, `dBµV`; Knöpfe `Kopieren`, `CSV`.

### :8000 — Zoom-Ansicht

`← Zurück zur Übersicht`, `Pause`, `Pegel` (S-Meter), `Max`, `?`
Panel: `Span`, `Demodulation` mit `Modulation` und `Bandbreite`, `Squelch`
mit `Squelch ein` und `folgt Skala min`, `Verstaerkung` mit `Regelung`
(`AGC` / `MGC`) und Pegelregler, `Aufnahme` mit `Mithören`, `Audio`,
`Spektrum`, `Long Run`.

### :8000 — Skalenleiste

`Skala min:`, `Skala max:`, `Auto`.

### :8000 — Kopfzeile

`EB200 Status`, `Neu laden`.

### :8010 — Frequenzkatalog

Überschrift `Frequenzkataloge`, Knopf `+ Neue Katalogdatei`.
Reiter im Bearbeitungsblatt: `Bearbeiten`, `Datei-Kopf`, `JSON`,
`Neuer Kanal`, `Neue Kanalgruppe`, `Neues Band`.

### :8050 — Aufzeichnungen und Jobs

Abschnitte `Laufend`, `Geplant`.
Reiter: `Aufzeichnungen`, `MSCAN-Scan-Definitionen`.
Spalten: `Art`, `Kurztext`, `Start / Dauer`, `Inhalt`, `Zustand`,
`Aufnahmen`, `Umfang`, `Ablage`, dann `Auswertung` und `Löschen`.
Kopfzeile: `D-SCAN (:8000)`, `Auswertung (:8040)`.
Kanalauswahl: `Übernehmen`, `Verwerfen`, `Abbrechen`, `nur gewählte`,
`Gefilterte an`, `Gefilterte aus`.

### :8060 — MSCAN-Lauf

Reiter: `Aktive Kanäle`, `Stille Kanäle`, `Aufnahmen`, `Zeitverlauf`,
`Auswertung`.
Knöpfe: `Alle markieren`, `Markierte aus der Definition entfernen`.
Kopfzeile: `Aufzeichnungen / Definitionen`, `Definition`,
`Long-Run-Jobs (:8050)`.

### :8070 — Audio-Auswertung

`Zoom Out`, `FFT:` mit 128/256/512/1024/2048, `Marker A`, `Marker B`,
`Differenz`, `Marker löschen`.
Klassen: `LEER`, `RAUSCHEN`, `CW`, `DIGITAL`, `SPRACHE`, `UNKLAR`.
Kopfzeile: `« zurück zur Liste (:8050)`.

### :8020 und :8040 — Session- und Spektrum-Auswertung

`Suche in Datei / Kurztext ...`, `Pfad oeffnen`, `Oeffnen`,
`« Zurueck zur Liste`, `Skala min:`, `Skala max:`, `Auto`,
`Frequenzraster`, `Befund-Navigator`.
Navigator-Gruppen: `Dauertraeger`, `Bursthafte Kanaele`,
`Staerkste Ereignisse`, `Kamm`, `Periodik`, `Notiz`.
Transport in :8040: Anfang, ein Frame zurück, Wiedergabe/Pause, ein Frame
vor, Ende; dazu `Tempo:`.

> **Hinweis:** In :8020 und :8040 stehen einige Beschriftungen noch ohne
> Umlaute („Zurueck", „oeffnen", „Dauertraeger"), seit T-905 auch die
> Blocküberschrift `Verstaerkung` in :8000. Im Handbuch werden sie so
> zitiert, wie sie in der Oberfläche stehen; im Fließtext steht dagegen
> „Verstärkung". Sollte das vereinheitlicht werden, ist diese Datei
> nachzuziehen.

### Starter — Statusboard

Zustände: `STARTET`, `LÄUFT`, `BEENDET`, `ÜBERSPRUNGEN`.

## 4. Für die spätere englische Fassung

Die Tabellen in Abschnitt 1 und 3 sind die Übersetzungsgrundlage. Zwei
Entscheidungen sind vorab zu treffen:

- Bleiben Gerätebegriffe (D-SCAN, IFPAN, M-SCAN, Squelch) unverändert? Sie
  stammen aus dem englischen Originalhandbuch und sollten es.
- Die Unterscheidung **Aufnahme** (Vorgang) gegen **Aufzeichnung** (Datei)
  lässt sich im Englischen nicht mit einem Wortpaar abbilden. Vorschlag:
  „recording" für die Datei, „to record / recording session" für den Vorgang,
  und die Unterscheidung dort auflösen, wo sie im Deutschen tragend ist.
