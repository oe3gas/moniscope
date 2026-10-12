# WS1 – Aktivitätsmonitoring (Scan → Tabelle)

**Kurzbeschreibung:** Automatisiertes Erfassen von Funkaktivität über definierte
Frequenzbereiche bzw. Kanallisten zu definierten Zeiten. Ergebnis ist eine
tabellarische Aktivitätshistorie („Wer/was war wann auf welcher Frequenz QRV").

---

## 1. Ziel & Nutzen

- Unbeaufsichtigte Beobachtung ausgewählter Bänder/Kanäle über längere Zeiträume.
- Aktivitätsstatistik statt Rohaufnahme: schlanke Tabelle, gut auswertbar.
- Grundlage für Ausbreitungs-, Belegungs- und Anomalie-Betrachtungen.
- Bewusst **ohne** Audio/IQ – nur Steuerkanal (SCPI) und Pegel/Squelch. Minimaler
  Datenfluss, läuft auch auf einem Raspberry Pi dauerhaft.

## 2. Umfang

**In Scope**
- Definition von Scan-Aufgaben (Bereich oder Kanalliste, Mode, Bandbreite, Schwelle).
- Zeitsteuerung (Zeitfenster, Intervalle, Tag/Nacht-Profile).
- Aktivitätserkennung über Squelch-Status und/oder Pegelschwelle.
- Ausgabe als Tabelle (CSV, optional SQLite als Backend).
- Frequenzabhängige Antennenumschaltung (Übergabe an externe Relaisbox).

**Out of Scope** (→ WS2/WS3)
- Aufnahme des Audiosignals.
- Spektrum-/Wasserfalldarstellung.
- Rohdaten-Sweeps: die auf D-SCAN-Pegelsweeps **begrenzte** Aufzeichnung je
  Session (siehe Abschnitt 3c) ist davon ausgenommen und nun bewusst
  zugelassen – Abgrenzung bleibt bestehen: weiterhin **keine** Audio-/IQ-
  Aufnahme, die bleibt bei WS2.

## 3. Scan-Strategie: Zusammenspiel D-SCAN → F-SCAN → M-SCAN

Alle drei geräteseitigen Scan-Arten werden im Skript genutzt, mit klar getrennten
Rollen:

| Scan-Art | Rolle | Ergebnis-Ziel |
|---|---|---|
| **D-SCAN** | Grobtriage: schneller Überblick, ob in einem Frequenzblock überhaupt Aktivität herrscht | liefert Kandidaten-Subbereiche für F-SCAN |
| **F-SCAN** | Verfeinerung: präzise Vermessung definierter Frequenzblöcke mit fester Schrittweite (z. B. Airband 8,33 kHz) | aktualisiert Messergebnisse in der „großen" DB |
| **M-SCAN** | Autarker Gerätebetrieb: Kanalliste aus lokal gepflegten Speicherplätzen (MEM0–999) auf dem Gerät selbst abfahren, unabhängig vom PC/LAN | eigene, kleine lokale DB (siehe Abschnitt 3b) |

### 3.1 D-SCAN als Vorstufe zu F-SCAN

D-SCAN liefert **kein präzises Aktivitätslog**, sondern einen groben Pegel-Array
über einen gesamten Sweep-Bereich in einem UDP-Datagramm (`DScanTrace`, Tag 301):

```
OptionalHeader {
    startFrequency, stopFrequency, stepFrequency   ' Sweep-Parameter
    markFrequency, bwZoom, referenceLevel
    newStepScheme                                   ' altes/neues Channel-Spacing (Annex J)
}
PeriodicTraceData: Pegelwert je Frequenzschritt über den gesamten Sweep
```

SelectorFlags (steuerbar über `TRACe:UDP:FLAG`): `LEVEL`, `FSTRENGTH` (nur mit
Option EB200FS), `SIGNAL_GREATER_SQUELCH` (liefert nur Werte oberhalb der
Squelch-Schwelle statt des kompletten Arrays – reduziert Datenmenge erheblich),
`OPTIONAL_HEADER`.

**Wichtige Einschränkung (Annex J):** Die Frequenzzuordnung der D-SCAN-Werte ist
prinzipbedingt ungenau – bestenfalls **±¼ der eingestellten IF-Bandbreite**.
D-SCAN sagt also nur „hier im Bereich X–Y ist Energie", nicht auf welcher exakten
Frequenz. Genau das prädestiniert D-SCAN zur Grobtriage vor dem präziseren F-SCAN.

**Ablauf (Phase A, siehe Abschnitt 5):**
1. D-SCAN über den Zielbereich mit `SIGNAL_GREATER_SQUELCH`-Flag fahren.
2. Aus den zurückgelieferten Punkten Kandidaten-Subbereiche clustern (Punkte
   oberhalb der Schwelle, ggf. mit ±¼BW-Toleranz erweitert).
3. Kandidaten-Subbereiche als F-SCAN-Aufträge mit exakter Schrittweite (z. B.
   8,33 kHz Airband) übergeben → präzise Frequenz-/Pegelzuordnung, Ergebnis
   aktualisiert die „große" DB (siehe 3b).

Relevante SCPI-Befehle:
```
[SENSe:]FREQuency:DSCan:STARt / :STOP / :SPAN
[SENSe:]FREQuency:DSCan:SPEed LOW|NORMal|HIGH|MTIMe
[SENSe:]FREQuency:MODE DSCan
TRACe:UDP:TAG:ON <ip>,<port>,DSCan
TRACe:UDP:FLAG:ON <ip>,<port>,"VOLTage:AC","SQUelch","OPTional"
```

### 3.2 F-SCAN: definierte Frequenzblöcke mit fester Schrittweite

F-SCAN wird für bereits bekannte/definierte Frequenzblöcke verwendet (z. B.
Airband 118–137 MHz mit 8,33 kHz Kanalraster), entweder direkt konfiguriert oder
aus der D-SCAN-Triage (3.1) abgeleitet. Jeder Frequenzblock benötigt einen
eigenen Datensatz in der „großen" DB (siehe Datenmodell, Abschnitt 4) mit
Bereich, Schrittweite, Antenne und Scan-Rhythmus – die Ergebnisse **aktualisieren**
bestehende Einträge bei jedem F-SCAN-Lauf, statt nur anzuhängen (siehe 3b).

### 3.3 M-SCAN: siehe Abschnitt 3a/3b (Geräte-Speicherplätze)

### 3.4 Aktivitätserkennung (F-SCAN/Kanalliste, unverändert)

- **Aktivitätskriterium:** Squelch offen ODER gemessener Pegel > Schwellwert für
  mindestens *t* ms (Entprellung gegen kurze Störer).
- **Ereignismodell:** ein Datensatz pro erkanntem Aktivitätsblock mit Start-/Endzeit,
  nicht ein Datensatz pro Sample.
- **Pegeldetektor (SCPI, geklärt):** `level_dbuv` ist als Peak-während-des-Events
  definiert (siehe Datenmodell) → Detektor muss explizit auf **PEAK** gesetzt werden,
  da der EB200-Default `PAVerage` (AVG) ist:
  ```
  [SENSe:]DETector[:FUNCtion] POSitive   ' PEAK-Detektor aktivieren (RST-Default: PAVerage)
  [SENSe:]DETector[:FUNCtion]?           ' Rückgabe: POS, PAV oder FAST
  ```
  Hintergrund (Annex E): PEAK hat dieselbe (schnelle) Anstiegszeit wie FAST, aber
  eine deutlich längere Abfallzeitkonstante – hält kurzzeitige Spitzen fest. AVG
  mittelt symmetrisch und würde Spitzenpegel unterschätzen.
  Zu beachten: Im CONTINUOUS-Modus pollt das EB200 den Detektorwert intern nur alle
  200 ms (`SENSe:DATA?` liefert also einen bis zu 200 ms alten Wert). Detektoren
  werden bei jeder Parameteränderung (z. B. Frequenzwechsel im Scan, Bandbreiten-/
  Demodulations-/Detektortyp-/Attenuator-Wechsel) automatisch zurückgesetzt und
  müssen sich neu einschwingen.

- **Messzeit (`MEASure:TIME`) – Einschwingdauer nach Detektor-Reset:** `DEFAULT`
  ist keine feste Zahl, sondern eine von Bandbreite und Detektortyp abhängige
  Tabelle (Annex E), die genau diese Einschwingzeit nach einem Reset bestimmt:

  | Bandbreite [kHz] | PEAK [ms] | AVG [ms] | FAST [ms] |
  |---|---|---|---|
  | 150 | 2 | 3 | 0,3 |
  | 120 | 2 | 3 | 0,3 |
  | 50 | 4 | 3 | 0,3 |
  | 30 | 5 | 3 | 0,3 |
  | 15 | 10 | 3 | 0,3 |
  | 9 | 20 | 6 | 0,6 |
  | 6 | 30 | 12 | 0,6 |
  | 2,4 | 60 | 25 | 2,2 |
  | 1,5 | 100 | 50 | 2,2 |
  | 0,6 | 200 | 100 | 9 |
  | 0,3 | 400 | 100 | 18 |
  | 0,15 | 500 | 200 | 18 |

  → **Für F-SCAN-Verweildauer je Kanal (Dwell Time) relevant:** Die Verweildauer
  pro Kanal muss mindestens die DEFAULT-Messzeit des gewählten Detektors bei der
  gewählten Bandbreite abdecken, sonst liefert der PEAK-Wert kein vollständig
  eingeschwungenes Ergebnis. Beispiel Airband (8,33 kHz Kanalraster, meist 9 kHz
  Bandbreite, Detektor PEAK): **mindestens 20 ms Verweildauer pro Kanal**
  einplanen (siehe DB-Zeile 9 kHz), zzgl. Synthesizer-Einschwingzeit (Annex G:
  ca. 3 ms je Sprung) und CPU-Verarbeitungszeit.
  Alternativ kann die Messzeit **explizit gesetzt** werden
  (`MEASure:TIME <value>`, 0,5 ms…900 s) – dann hängt sie nicht mehr von
  Bandbreite/Demodulation ab (Trise PEAK=0, Tfall PEAK=gesetzte Zeit), erlaubt
  aber auch **zu kurze, fehlerhafte Messungen** — laut Handbuch liegt die
  Verantwortung für eine sinnvolle Wahl beim Anwender. Voraussetzung für eigene
  Messzeiten: ZF-Sektion mit **DDC2** (ab IF-DSP V03.00) – bei diesem Gerät
  (IF DSP V03.23) vorhanden.

### 3.5 SCPI-Verifikation

Parameteränderungen per SCPI (D-SCAN-Bereich, Bandbreite, Demodulation, Detektor
…) werden vom Gerät nicht immer 1:1 übernommen – es kann rastern, auf eine
Bereichsgrenze begrenzen oder das Kommando ganz ablehnen, ohne dass das am
blinden Senden (`send()`) erkennbar wäre. `eb200_core/scpi_verify.py` prüft das
per Rücklesen, stdlib-only, unabhängig vom jeweiligen Skript einsetzbar
(Wasserfall, WS1-Scanner):

```
Fehler-Queue leeren (SYST:ERR? bis "0,No error")
  -> Set-Kommando(s) senden
  -> *OPC? abfragen (auf Ausführung warten, statt blind zu schlafen)
  -> Fehler-Queue erneut einsammeln (Batch-Ebene)
  -> Parameter zurücklesen (Query)
  -> mit dem gesendeten Wert vergleichen (toleranzbasiert bei Frequenzen,
     nächste Stufe bei Bandbreite, Kurzform-tolerant bei Enums)
```

Daraus ergibt sich je geprüftem Parameter eines von vier Urteilen:

| Urteil | Bedeutung |
|---|---|
| `OK` | Rückgelesener Wert liegt innerhalb der Toleranz – normale Rasterung. |
| `CLAMPED` | Gerät hat einen anderen Wert übernommen, aber **keinen** Fehler gemeldet (z. B. auf eine Bereichsgrenze begrenzt). |
| `REJECTED` | Set wurde abgelehnt (Eintrag in der Fehler-Queue), der Altwert bleibt bestehen. |
| `QUERY_ERROR` | Rücklesen/Parsen ist fehlgeschlagen (Query-Kurzform vom Gerät nicht unterstützt, Timeout, unlesbare Antwort) – nützlich, um offene Annahmen sichtbar zu machen. |

**Wichtig zur Fehler-Queue:** `SYST:ERR?` ist FIFO und lässt sich nicht
eindeutig einem einzelnen Set-Kommando in einem Batch zuordnen – liegen nach
einem Batch Fehler vor **und** ein Check schlägt fehl, wird er als `REJECTED`
gewertet, sonst als `CLAMPED`. Für eine eindeutige Zuordnung müssen
fehleranfällige Sets **einzeln** verifiziert werden (eigener `verify()`-Aufruf
je Kommando statt eines gemeinsamen Batches).

Eingebunden ist die Verifikation bislang im D-SCAN-Konfigpfad des Wasserfalls
(`waterfall.py`, `_configure_dscan()`, Flag `VERIFY_SCPI`) – nicht-fatal, eine
Abweichung wird nur geloggt, der Sweep startet trotzdem.

Damit lassen sich auch die beiden bisher offenen Annahmen aus diesem Dokument
und aus `waterfall.py` jetzt **empirisch** statt nur vermutet klären, ganz ohne
etwas am Gerät zu verstellen (`do_set=False` bei `freq_check()`/`enum_check()`
prüft nur den Startwert):
- Kurzform-Rückgabe von `DEM`/`BAND` (Enum-Kurzform, z. B. `AM` vs. `AMDS`) und
  ob `AM`/9 kHz tatsächlich der geräteseitige Default ist (siehe
  `IFPAN_MODE_DEFAULT`/`IFPAN_BW_HZ_DEFAULT` in `waterfall.py`) –
  `enum_check("DEM-Default", "DEM", "AM", do_set=False)` bzw.
  `bw_check("BAND-Default", "BAND", 9000, do_set=False)`.
- Die 0,1-dBµV-Rohwert-Skalierung der LEVEL-Daten (Kopfkommentar in
  `waterfall.py`) – dafür eignet sich `scpi_verify` nicht direkt (das ist ein
  UDP-Datenformat, kein SCPI-Parameter), lässt sich aber indirekt absichern,
  indem ein bekannter Referenzpegel per SCPI gesetzt/gelesen und mit dem
  parallel empfangenen D-SCAN/IFPAN-Rohwert verglichen wird.

*Hinweis:* Nach dem Einbau von `scpi_verify` sollte `EB200_sources.txt` per
`EB200_Sources2Text.ps1` neu erzeugt und ins Projekt hochgeladen werden, damit
der dort gepflegte Quellcode-Stand wieder vollständig ist.

## 3a. Geräte-Speicherplätze (MEM0–MEM999) – Save/Load

Zusätzlich zur laufenden SCPI-Steuerung soll die App die **1000 geräteinternen
Speicherplätze** des EB200 (MEM0…MEM999) verwalten können. Hintergrund: Damit lässt
sich eine Kanalliste dauerhaft im Gerät ablegen, sodass ein **M-SCAN auch
eigenständig am Gerät** (z. B. mobiler/autarker Einsatz ohne Rechner/LAN-Anbindung)
durchgeführt werden kann – die App liefert dafür die Pflege-Oberfläche, das Gerät
den autarken Scan-Betrieb.

**Funktionsumfang:**
- **Save (App → EB200):** Speicherplätze aus einer in der App gepflegten Kanalliste
  (Frequenz, Mode, Bandbreite, Label) auf das Gerät schreiben.
- **Load (EB200 → App):** Vorhandene Speicherplatzinhalte vom Gerät auslesen, in der
  App anzeigen/bearbeiten und als Kanalliste weiterverwenden (z. B. auch als
  Grundlage für den SCPI-gesteuerten Scan in WS1 selbst).
- **Editierbare Ablage in der App**, unabhängig vom Gerät (lokale Datei/DB), damit
  Kanallisten offline gepflegt und versioniert werden können, bevor sie aufs Gerät
  übertragen werden.

**SCPI-Befehle:**
```
MEMory:COPY RX,<dest_name>          ' RX-Settings → Speicherplatz (SAVE-Äquivalent)
MEMory:COPY <src_name>,RX           ' Speicherplatz → RX-Settings (RCL-Äquivalent)
MEMory:CONTents <name>,<mem_paras>  ' direkter Schreibzugriff (packed struct)
MEMory:CONTents? <name>             ' Speicherinhalt/Status auslesen (ACT-Flag)
MEMory:CLEar <name>|MAXimum         ' einzelnen oder alle Speicherplätze löschen
```
`<name>`/`<dest_name>`/`<src_name>`: `MEM0` … `MEM999`.

**Offene Punkte:**
- Genaues Format von `<mem_paras>` (packed struct) aus Annex C/Programmierbeispielen
  verifizieren – wird für Bulk-Schreiben/Lesen aller 1000 Plätze benötigt.
  Alternative: pro Platz einzeln über `RX`-Umweg (RX-Settings setzen, dann
  `MEMory:COPY RX,<dest>`) – langsamer, aber ohne Kenntnis des packed-struct-Formats
  nutzbar.
- Mapping der App-internen Kanalliste (Frequenz/Mode/BW/Label) auf die vom EB200
  unterstützten Speicherparameter (inkl. eventueller Squelch-/Pegel-Voreinstellung
  je Speicherplatz, siehe „Squelch from memory" `OUTPut:SQUelch:CONTrol MEMory`).
- UI/CLI-Funktion: Kanalliste (Datei) ↔ Gerät synchronisieren (Diff, Upload, Download).

## 3b. Datenhaltung: „kleine" DB (Memory-Plätze) vs. „große" DB (Projekt-DB)

Es zeichnen sich zwei unterschiedliche Datenhaltungsebenen ab, die noch in der
Technologiewahl zu klären sind:

**„Kleine" DB – lokale Memory-Platz-Verwaltung (siehe 3a):**
- Zweck: 1:1-Abbild der max. 1000 Geräte-Speicherplätze (MEM0–999) für M-SCAN,
  rein lokal am PC gepflegt, dann per SCPI aufs Gerät übertragen.
- Umfang klein und fix begrenzt (≤1000 Datensätze, wenige Felder je Platz) →
  eine **einfache indizierte Datei (z. B. JSON, ggf. JSON Lines)** dürfte hier
  völlig ausreichen; kein Bedarf an Concurrent-Access, Transaktionen oder
  komplexen Abfragen.

**„Große" DB – Projekt-Datenbank (Aktivitätslog, F-SCAN-Frequenzblöcke, WAV-
Referenzen aus WS2):**
- Zweck: fortlaufend aktualisierte Messergebnisse aus F-SCAN-Läufen (siehe 3.2),
  Aktivitäts-Ereignisse (Abschnitt 4), Verknüpfung mit WS2-Audioaufnahmen
  (Dateireferenzen/Metadaten der WAV-Archive).
- Anforderungen deutlich höher: wiederholte **Updates** bestehender Datensätze
  (nicht nur Anhängen), Verknüpfung/Joins zwischen Frequenzblöcken, Ereignissen
  und Aufnahmen, potenziell gleichzeitiger Zugriff durch mehrere Workstreams
  (WS1 schreibt Ereignisse, WS2 schreibt WAV-Metadaten, gemeinsame Auswertung
  liest beides) → spricht für **SQLite** (Datei-basiert, aber SQL-fähig,
  transaktionssicher, aus WS1 bereits als Option genannt) als Minimalvariante,
  ggf. später PostgreSQL falls Mehrbenutzerzugriff übers Netz nötig wird.

**Offene Entscheidung:** Technologie für die „große" DB (SQLite vs. leistungsfähigere
Lösung) und ob die „kleine" JSON-basierte Memory-Verwaltung komplett getrennt bleibt
oder als weitere Tabelle in dieselbe SQLite-Datei integriert wird (einfachere
Handhabung, ein gemeinsames Backup/Dateiformat) – siehe Abschnitt 8.

## 3c. Rohdaten-Session-Store & Offline-Auswertung

Ergänzend zur schlanken Live-Aktivitätstabelle (Abschnitt 4) soll während einer
Monitor-Session der vollständige D-SCAN-Pegelverlauf verlustfrei mitgeschrieben
werden, um ihn nachträglich auf Muster – insbesondere regelmäßig wiederkehrende
Signale – zu untersuchen. Das ist eine bewusste, **begrenzte** Erweiterung des
ursprünglichen „ohne Rohaufnahme"-Scopes aus Abschnitt 1: keine
Daueraufzeichnung, sondern ein pro Session abgeschlossener Rohbestand als
Substrat für die in Abschnitt 1 genannten Belegungs-/Anomalie-Betrachtungen.

### 3c.1 Erfasste Einheit

Erfasste Einheit ist ein fertiger D-SCAN-Sweep (`complete=True` aus dem
`SweepAccumulator`, siehe Abschnitt 3.1) – nicht der einzelne UDP-Chunk. Jeder
gespeicherte Sweep ist selbstbeschreibend (`start_hz`/`stop_hz`/`step_hz` plus
Zeitstempel), sodass sich die Frequenz jedes einzelnen Bins daraus ableiten
lässt: `n_bins = round((stop_hz - start_hz) / step_hz) + 1`, Frequenz von Bin
`i` = `start_hz + i * step_hz`. Teil-Chunks (laufende, noch unvollständige
Sweeps) werden nicht gespeichert.

### 3c.2 Datenmodell: Session → Segment → Sweep

Für den Rohbestand wird eine **eigene SQLite-Datei je Session** angelegt,
getrennt vom `frequency_blocks`-Konfigbestand der „großen" DB (Abschnitt 3b/4).
Ein Segment ist ein zusammenhängender Lauf mit konstantem Raster (Start/Stop/
Schrittweite unverändert); die Segmentgrenze fällt mit `params_changed` im
`SweepAccumulator` zusammen – demselben Ereignis, das im Wasserfall die
Parameter-Tafel („Banner") auslöst.

```sql
CREATE TABLE session (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    started_at REAL NOT NULL,
    source     TEXT,
    mode       TEXT NOT NULL DEFAULT 'DSCAN',
    note       TEXT
);
CREATE TABLE segment (
    id        INTEGER PRIMARY KEY,
    start_hz  INTEGER NOT NULL,
    stop_hz   INTEGER NOT NULL,
    step_hz   INTEGER NOT NULL,
    n_bins    INTEGER NOT NULL,
    t_first   REAL NOT NULL,
    t_last    REAL NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    CHECK (start_hz < stop_hz), CHECK (step_hz > 0)
);
CREATE TABLE sweep (
    id         INTEGER PRIMARY KEY,
    segment_id INTEGER NOT NULL REFERENCES segment(id),
    seq        INTEGER NOT NULL,
    t_unix     REAL NOT NULL,
    levels     BLOB NOT NULL
);
CREATE INDEX idx_sweep_segment ON sweep(segment_id, seq);
```

### 3c.3 Pegel-Kodierung

Der `levels`-BLOB je Sweep-Zeile ist `n_bins` × `int16` in 0,1-dBµV-Schritten
(Deci-dBµV) – roh, wie auf der Leitung empfangen. Beim Speichern wird **nicht**
durch 10 geteilt; geschrieben wird mit `array('h', ...).tobytes()`, gelesen mit
`array('h')` + `frombytes()`, die Division durch 10.0 erfolgt erst bei
Analyse/Anzeige.

Zwei Caveats:
- **Endianness:** `tobytes()`/`frombytes()` nutzen die native Byte-Reihenfolge
  – für eine Ein-Stations-DB unkritisch, für Portabilität zwischen Rechnern mit
  unterschiedlicher Endianness wäre eine feste Byte-Reihenfolge plus
  `byteswap()` nötig.
- **0,1-dBµV-Annahme:** Diese Skalierung ist laut Abschnitt 3.5 bislang nicht
  bestätigt – genau deshalb wird roh gespeichert (unveränderte LEVEL-Rohwerte,
  keine vorzeitige Interpretation), damit eine spätere Korrektur der Skalierung
  nicht die gesamte Rohdatenbasis entwertet.

### 3c.4 Schreibpfad

Der Schreibpfad ist ein passiver Abgriff am bestehenden D-SCAN-Strom – ein Hook
auf `DScanUpdate`/`complete=True` (siehe Abschnitt 3.1), kein eigener
UDP-Empfang. Bei erkanntem `params_changed` wird ein neues Segment angelegt,
sonst wird der Sweep an das laufende Segment angehängt.

Latenz-Schutz gegen den laufenden D-SCAN-Empfang: gebündelte Commits statt
eines Commits je Sweep, oder ein eigener Writer-Thread mit vorgeschalteter
Queue (Muster analog `CLIENT_QUEUE_SIZE` im Wasserfall) – kein `fsync` pro
Sweep. Beim Teardown (Session-Ende, Programmende) muss die DB sauber
geschlossen werden.

### 3c.5 Auswertung (stdlib-only)

Für die Offline-Auswertung wird ein Segment als Matrix (`rows × n_bins`) plus
Zeitvektor `t[]` geladen. Ein gemeinsamer Charakterisierungs-Kern dient sowohl
der manuellen Markierung als auch der Auto-Detektion: Peak, Rauschflur (Median
oder niedriges Perzentil), Mittenfrequenz, Bandbreite bei −6 dB, Belegungsgrad
und Zeitmuster je Kandidat.

Periodizität wird über ein Histogramm der Zwischenankunftszeiten der
Schwellüberschreitungen je Bin bestimmt – **bewusst kein blindes FFT**, da die
Sweep-Intervalle ungleichmäßig sind (variable Sweep-Dauer je nach
Bereichsgröße/Speed, siehe Abschnitt 3.1/3.4).

### 3c.6 Visualisierung & Abgrenzung

Die Darstellung wird bei jeder Auswertung frisch aus den Rohdaten erzeugt
(ehrliche Segment-Achse statt einer nachträglich verzerrten Zeitachse),
periodische Bins werden als Overlay markiert. Das im Wasserfall gespeicherte
JPG (`waterfall.py`) bleibt davon unberührt – es ist und bleibt ein reiner
Notizzettel, keine Datenquelle für diese Auswertung.

Wie im übrigen Projekt gilt: **nur Python-Standardbibliothek** (`sqlite3`,
`array`, `statistics`, `math`) – keine zusätzliche Abhängigkeit.

## 4. Datenmodell (Vorschlag)

**Aktivitäts-Ereignisse (F-SCAN/Kanalliste-Ergebnisse, „große" DB):**

| Spalte        | Bedeutung                                  |
|---------------|--------------------------------------------|
| `ts_start`    | Beginn der erkannten Aktivität (ISO 8601)  |
| `ts_end`      | Ende der Aktivität                          |
| `freq_hz`     | Frequenz                                    |
| `mode`        | Demodulationsart (AM/FM/USB/…)             |
| `bw_hz`       | eingestellte Bandbreite                     |
| `level_dbuv`  | max. Pegel während der Aktivität            |
| `antenna`     | genutzte Antenne                            |
| `label`       | optionaler Klartext (z. B. „NDB OED")       |

**Frequenzblock-Definitionen (F-SCAN-Aufträge, „große" DB):**

| Spalte          | Bedeutung                                              |
|-----------------|---------------------------------------------------------|
| `block_id`      | eindeutige ID des Frequenzblocks                        |
| `freq_start_hz` | Startfrequenz des Blocks                                 |
| `freq_stop_hz`  | Stoppfrequenz des Blocks                                 |
| `step_hz`       | Schrittweite (z. B. 8330 Hz für Airband)                 |
| `mode`          | Demodulationsart für den Scan                            |
| `bw_hz`         | Bandbreite für den Scan                                  |
| `antenna`       | zuständige Antenne                                        |
| `origin`        | Herkunft: `manual` (fest definiert) oder `dscan` (aus D-SCAN-Triage abgeleitet) |
| `schedule`      | Scan-Rhythmus (z. B. alle 15 min, stündlich, Tag/Nacht)  |
| `last_scan_ts`  | Zeitpunkt des letzten F-SCAN-Laufs über diesen Block      |

Jeder F-SCAN-Lauf über einen Block **aktualisiert** `last_scan_ts` und erzeugt/
aktualisiert zugehörige Aktivitäts-Ereignisse – kein reines Anhängen, sondern ein
laufend gepflegter Belegungszustand je Block.

**D-SCAN-Triagepunkte (optional, Zwischenergebnis vor F-SCAN-Auftragserstellung):**

| Spalte          | Bedeutung                                              |
|-----------------|---------------------------------------------------------|
| `ts`            | Zeitpunkt der D-SCAN-Messung                             |
| `freq_hz`       | ungefähre Frequenz (±¼ IF-Bandbreite Unsicherheit)       |
| `level_dbuv`    | gemessener Pegel                                          |
| `promoted_to`   | Verweis auf `block_id`, falls daraus ein F-SCAN-Auftrag abgeleitet wurde |

Muss nicht dauerhaft aufbewahrt werden (reine Zwischenstufe) – Aufbewahrung ist
eine Umsetzungsdetail-Frage (z. B. nur die letzten N Läufe, oder gar nicht
persistieren und nur zur Laufzeit verarbeiten).

## 5. Geplante Aktivitäten

**Phase A – Grundgerüst**
- SCPI-Verbindungsklasse (Connect, Set, Query, sauberes Timeout/Reconnect).
- Einzelmessung: Frequenz setzen → Pegel/Squelch lesen → zurückgeben.
- D-SCAN-Datagramm-Parser (`DScanTrace`, Tag 301) als Grundlage für die Grobtriage
  (siehe 3.1) – UDP-Empfang, Header/Pegel-Array parsen, Kandidaten-Clustering.

**Phase B – Scanner**
- Kanalliste (M-SCAN-artig, Basis: 3a/3b) und Bereichsscan (F-SCAN) implementieren.
- D-SCAN-Triage → F-SCAN-Auftragserzeugung (Kandidaten-Subbereiche aus Phase A
  in Frequenzblock-Einträge überführen, siehe Datenmodell Abschnitt 4).
- Aktivitätslogik inkl. Entprellung und Ereignisbildung.

**Phase C – Zeit- & Konfigsteuerung**
- Konfigdatei (Frequenzpläne, Schwellwerte, Zeitfenster) – z. B. YAML.
- Scheduler (Zeitfenster/Intervalle, Tag-/Nachtprofile).
- Antennenumschaltung je Frequenzbereich anbinden.

**Phase D – Ausgabe & Betrieb**
- CSV-/SQLite-Writer, Rotation/Archiv.
- Einfache Auswertung (z. B. Aktivität je Kanal/Stunde).
- Dauerbetrieb (systemd-Service / Autostart).
- Periodische Selbstdiagnose des Empfangspfads via SCPI-Kurztest, z. B. beim Start
  des Scan-Dienstes und danach in regelmäßigen Abständen (z. B. täglich):
  ```
  TEST? SHORt,REPort      ' Kurztest (Signalpfad Antenne → NF), entspricht TEST-Taste
  *TST?                    ' äquivalenter IEEE-488.2-Standardbefehl
  ```
  Ergebnis auswerten (`TEST OK SIGNAL PATH` vs. `SENSITIVITY OUT OF RANGE`) und bei
  Fehlschlag als Ereignis/Alarm loggen bzw. Betreiber benachrichtigen – verhindert,
  dass ein defekter Empfangspfad fälschlich als „keine Aktivität" über Stunden/Tage
  interpretiert wird. Bei Bedarf zusätzlich Modulstatus/Testpunkte prüfen
  (`DIAGnostic:MONitor?`, `DIAGnostic[:MEASure]:TPOint?`).

## 6. Deliverables

- `scanner.py` (Steuerung + Scan-Logik).
- Konfigdatei mit Frequenzplänen und Schwellwerten.
- Aktivitätstabelle (CSV/SQLite) + kleines Auswertungsskript.
- `memsync.py` (o. ä.): Save/Load der geräteinternen Speicherplätze MEM0–MEM999
  (App ↔ EB200), inkl. lokaler editierbarer Kanallisten-Ablage.
- Kurzdoku zum Betrieb.

## 7. Abhängigkeiten / Voraussetzungen

- Netzwerkzugriff auf den EB200 (feste IP empfohlen).
- Genaue SCPI-Befehle für Pegelabfrage und Squelch-Status aus dem EB200-Handbuch
  (bzw. EB500/PR100) verifizieren.
- Externe Antennenumschaltung (Hardware) für den Mehrantennenbetrieb.

## 8. Offene Entscheidungen (für unser nächstes Gespräch)

- Welche Bereiche/Kanäle konkret? (KW-Baken, Utility, V/UHF …)
- Zeitraster: fixe Intervalle vs. Tag/Nacht-Profile.
- **DB-Technologie „große" DB:** SQLite als Minimalvariante ausreichend, oder von
  Anfang an leistungsfähigere Lösung (z. B. PostgreSQL), insbesondere im Hinblick
  auf gemeinsamen Zugriff mit WS2 (WAV-Metadaten) und spätere Mehrbenutzer-/
  Netzzugriffsanforderungen?
- **DB-Technologie „kleine" DB (Memory-Plätze):** eigene JSON-Datei oder als
  zusätzliche Tabelle in dieselbe SQLite-Datei wie die „große" DB integrieren?
- D-SCAN-Clustering-Logik: welche Kriterien/Toleranzen für die Zusammenfassung von
  D-SCAN-Treffern zu F-SCAN-Kandidaten-Subbereichen (siehe 3.1)?