# WS3 – Funktionale Erweiterung (Spektrum / Wasserfall am PC)

**Kurzbeschreibung:** Das Empfangssignal des EB200 wird am PC visualisiert –
Spektrum und Wasserfall in (nahezu) Echtzeit. Der EB200 wird damit zu einem
komfortablen Beobachtungs-Frontend erweitert.

---

## 1. Ziel & Nutzen

- Sichtbarmachen von Bandbelegung, Signalformen und kurzzeitigen Ereignissen.
- Schnelles visuelles Auffinden interessanter Signale (Ergänzung zu WS1/WS2).
- Einstieg in eigene DSP-/GUI-Programmierung mit unmittelbar sichtbarem Ergebnis.

## 2. Umfang

**In Scope**
- Empfang eines geeigneten UDP-Streams vom EB200.
- Aufbereitung zu Spektrum + rollierender Wasserfalldarstellung.
- Einfache Bedienung (Frequenz/Bandbreite setzen, Farbskala, Anhalten).

**Out of Scope**
- Vollständiger SDR-Empfänger / Demodulation (kann später via GNU Radio folgen).
- Aufnahme (→ WS2).

## 3. Drei technische Wege (korrigierte Grundlage)

Anders als ursprünglich angenommen handelt es sich um **drei grundverschiedene
Methoden** mit unterschiedlicher Zoomstufe – kein Entweder-Oder, sondern ein
komplementäres Set, gestaffelt von breit/grob bis schmal/roh:

| Weg | Bereich | Auflösung/Genauigkeit | Wer berechnet? | Charakter |
|-----|---------|------------------------|-----------------|-----------|
| **A: D-SCAN** | sehr breit, frei wählbar 10 kHz–3 GHz (Option `EB200DS`) | grob – bestenfalls ±¼ der eingestellten IF-Bandbreite (Annex J) | Gerät (Hardware-Sweep) | weiträumige Übersicht, Triage |
| **B: IFPAN** | mittel, ±12,5…±500 kHz um aktuelle Empfangsfrequenz (Option `EB200SU`) | fein, 770–1230 Pixel, hardware-FFT | Gerät (IF-Panorama-DSP) | „Lupe" um aktuellen Kanal, schneller Echtzeit-Wasserfall |
| **C: IQ (AUDIO-Kanal, Demod=IQ)** | sehr schmal, ≤ 32 kHz komplex (2-Kanal-PCM-Modus, `SYSTem:AUDio:REMote:MODe`) | roh, eigene FFT/Verarbeitung nötig | PC | Rohdaten-Erfassung eines einzelnen schmalen Signals |

**Wichtige Korrektur gegenüber der ursprünglichen Annahme:** Es gibt beim EB200
**keinen eigenen UDP-Trace-Typ für breitbandige IQ-Daten** – die vollständige
Tag-Liste in Annex F ist `FSCAN, MSCAN, DSCAN, AUDIO, IFPAN, FASTLEVCW, LIST, CW`.
Rohe IQ-Samples lassen sich nur über den **AUDIO-Kanal** gewinnen, indem die
Demodulationsart auf `IQ` gesetzt wird (`[SENSe:]DEModulation IQ`) – der ohnehin
für WS2 genutzte AUDIO-Datenstrom liefert dann I/Q statt NF. Die maximale Rate
ist der 2-Kanal-PCM-Modus mit 32 kHz/16 Bit (`SYSTem:AUDio:REMote:MODe` Mode 1)
→ **komplexe Bandbreite von grob 32 kHz**, nicht die ursprünglich vermuteten
500 kHz. Echtes breitbandiges IQ gibt es beim EB200 nur über die serielle
Schnittstelle X6 (Annex I, bis 256 kHz Scanrate, aber Hardware-seriell, nicht
LAN) oder analog über den IFWB-Ausgang X5 (±1 MHz) – nicht über UDP/LAN. Das
deckt sich mit der bereits im Geräteprofil festgehaltenen Einschränkung „Kein
breitbandiges IQ-Streaming über LAN".

**Konsequenz für die Priorisierung:** IFPAN deckt tatsächlich einen breiteren
Bereich ab als der UDP-„IQ"-Weg (bis 1 MHz Gesamtspan vs. ~32 kHz) – IQ ist der
schmalste der drei Wege, dafür aber der einzige mit Zugriff auf Rohdaten statt
fertigem Pegel/Spektrum. IQ ist damit eher eine **Ausbaustufe für
Signalanalyse/Rohdaten-Erfassung einzelner Signale** (thematisch näher an WS2 als
an WS3) als ein Ersatz für einen breiten Wasserfall.

**Empfehlung:**
1. **D-SCAN** zuerst – weiträumiger Übersichts-Wasserfall, und Wiederverwendung
   desselben Datagramm-Parsers, der bereits für die WS1-Triage gebaut wird
   (`DScanTrace`, Tag 301) – ein Parser, zwei Nutzer.
2. **IFPAN** als Kernstück des eigentlichen Echtzeit-Wasserfalls (schnell,
   hochauflösend, geringer Implementierungsaufwand, Option bereits vorhanden).
3. **IQ** als spätere Ausbaustufe, eher für WS2-nahe Anwendungsfälle
   (Rohdaten-Aufzeichnung statt demoduliertem Audio) als für die
   Wasserfalldarstellung selbst.

## 3a. GUI-Konzept „Lupe": D-SCAN → IFPAN → Präzisierung

Zentrale Bedienidee, die die drei Scan-Ebenen (siehe WS1, Abschnitt 3) und die
zwei Spektrum-Quellen (D-SCAN, IFPAN) in der GUI zusammenführt:

**Ablauf:**
1. **D-SCAN-Übersicht** wird angezeigt (breiter Bereich, grobe Auflösung, siehe
   Weg A oben).
2. Der Benutzer **markiert im D-SCAN-Wasserfall/Spektrum interaktiv einen
   Ausschnitt** (Klick-und-Ziehen oder Klick auf einen auffälligen Peak) – dieser
   Ausschnitt wird als Mittenfrequenz + Span für einen IFPAN-Aufruf interpretiert
   (Span-Wahl aus den verfügbaren IFPAN-Stufen: ±12,5…±500 kHz, siehe Weg B).
3. Die App **konfiguriert und startet automatisch einen IFPAN** auf diesem
   Ausschnitt (`FREQuency:SPAN`, `DISPlay:MENU IFPAN`, `TRACe:FEED:CONTrol IFPAN,
   ALWays`) – die „Lupe" zoomt vom groben D-SCAN-Überblick in die feine,
   hochauflösende IFPAN-Ansicht.
4. Aus dem IFPAN-Ergebnis (feine Pegel-/Frequenzauflösung) lassen sich präzise
   Frequenzen extrahieren, die zwei Zwecke erfüllen:
   - **Sofortige Feinbeobachtung** (Live-Wasserfall des Ausschnitts).
   - **Übergabe an WS1**: Präzisierung/Erzeugung von F-SCAN-Frequenzblöcken oder
     einzelnen Kanal-Einträgen (M-SCAN-Speicherplätze, siehe WS1 Abschnitt 3a) auf
     Basis der im IFPAN gefundenen aktiven Frequenzen – analog zur bereits in
     WS1 3.1 beschriebenen D-SCAN→F-SCAN-Triage, hier jedoch interaktiv vom
     Benutzer angestoßen statt automatisiert.

**Nutzen:** Der Benutzer arbeitet sich visuell vom groben Überblick zur exakten
Frequenz vor – „erst den Wald sehen (D-SCAN), dann den Baum (IFPAN)" – und kann
das Ergebnis direkt in die automatisierte Überwachung (WS1) übernehmen, statt
Frequenzen manuell abzutippen.

**IFPAN-CONFIG-Parameter (SCPI, für den Zoom-Aufruf relevant):**

| Parameter | Bedeutung | Bereich |
|---|---|---|
| `REF` | Referenzpegel – oberes Ende der Y-Achse | -20…+120 dBµV, 10-dB-Schritte |
| `RNG` | Anzeigebereich der Y-Achse (dB unterhalb REF) | 10…160 dBµV, 10-dB-Schritte |
| `FREQuency:SPAN` | dargestellte Frequenzbreite (Zoom-Faktor) | 150 Hz…1000 kHz, 17 Stufen; im COUPLED-Modus = Breite des aktiven ZF-Filters |
| `MEASure:TIME` (bzw. `CALCulate:IFPan:AVERage:TIME`) | Mittelungszeit der IFPAN-Daten | DEFAULT / 0,5 ms…900 s (DDC2 vorausgesetzt) |

Kein eigener SCPI-Befehl für REF/RNG dokumentiert (reine Anzeigeskalierung am
Frontpanel, `no command` in Annex C) – für die GUI heißt das: REF/RNG werden
**lokal in der eigenen Darstellung** berechnet/gesetzt, nicht am Gerät.

**Anzeigemodus (Mittelungsart) für den IFPAN-Datenstrom:**
```
CALCulate:IFPan:AVERage:TYPE MINimum|MAXimum|SCALar   ' MIN-Hold / MAX-Hold / AVG
CALCulate:IFPan:AVERage:TYPE OFF                       ' CLRWRITE (aktueller Wert, keine Haltefunktion)
CALCulate:IFPan:CLEar                                  ' MIN-/MAX-Hold neu starten
```
Für die Lupe sinnvoller Default: `MAXimum` (MAX-Hold) direkt nach dem Zoom, damit
kurze Signalspitzen im Beobachtungszeitraum nicht verpasst werden, bevor der
Nutzer manuell CLRWRITE für die Echtzeitansicht wählt.

**Marker-Zentrierung – automatische Präzisierung nach dem Zoom:**
```
CALCulate:IFPan:MARKer:MAXimum[:PEAK]   ' zentriert auf absolutes Pegelmaximum im Ausschnitt
CALCulate:IFPan:MARKer:MAXimum:LEFT     ' nächstes Maximum links vom Marker
CALCulate:IFPan:MARKer:MAXimum:RIGHt    ' nächstes Maximum rechts vom Marker
```
Vorschlag: Nach jedem Zoom-Schritt automatisch `MARKer:MAXimum:PEAK` aufrufen –
das Gerät zentriert den Marker selbständig auf den stärksten Punkt im
Ausschnitt und liefert damit direkt die präzisierte Frequenz, ohne dass der
Nutzer manuell im Spektrum suchen muss. Diese Marker-Frequenz ist der
naheliegende Kandidat für die Übergabe an WS1 (F-SCAN-Block bzw.
M-SCAN-Speicherplatz, siehe Rückkanal unten).

**Technischer Rahmen (grob, Detaillierung später):**
- GUI-seitig: D-SCAN-Ansicht mit Marker-/Auswahlwerkzeug (Rechteck-Selektion oder
  Klick-auf-Peak mit automatischer Span-Vorschlagslogik).
- Backend: Funktion `zoom_to_ifpan(center_freq, span)`, die die nötigen SCPI-Befehle
  ausführt, auf den IFPAN-Datenstrom umschaltet, MAX-Hold aktiviert und
  anschließend automatisch `MARKer:MAXimum:PEAK` aufruft.
- Rückkanal: Aktions-Button/Menüpunkt „Als F-SCAN-Block übernehmen" bzw. „Auf
  M-SCAN-Speicherplatz sichern", der die per Marker gefundene Frequenz (bzw.
  weitere im IFPAN sichtbare Peaks) in die WS1-Datenhaltung (Frequenzblock- bzw.
  Memory-Tabelle, siehe WS1 3a/3b/4) schreibt.
- Einordnung: gehört inhaltlich zu **Phase D (Komfort & Ausbau)**, da sie auf
  den Grundfunktionen von D-SCAN- und IFPAN-Darstellung (Phase A–C) aufbaut.

## 3b. Idee: Optionale virtuelle Frontplatte

Ergänzend zur Wasserfall-/Spektrumdarstellung soll die Browser-GUI optional eine
**virtuelle Nachbildung der EB200-Frontplatte** anbieten – als zuschaltbare
Ansicht neben/unter dem Wasserfall.

**Nutzen:**
- Vertraute, geräte-nahe Bedienmetapher für den manuellen Betrieb per Fernsteuerung
  (Frequenz, MOD, BW, MGC/SQU/TONE, ATT, AFC, LEV als virtuelle Tasten/Anzeigen),
  ohne SCPI-Befehle im Kopf haben zu müssen.
- Anzeige der aktuellen Gerätezustände (Pegelbalken, Squelch-/Tone-/MGC-Werte,
  Frequenz, Speicherplatznummer) in vertrauter Anordnung – 1:1-Analogie zum
  Originalgerät statt abstrahierter Web-Formulare.
- Guter Einstiegspunkt für Gelegenheitsnutzung/Remote-Zugriff (z. B. vom Sofa oder
  unterwegs übers LAN), wenn das physische Gerät im Shack nicht sichtbar ist.
- Didaktischer Nebeneffekt: erleichtert anderen (oder einem selbst nach längerer
  Pause) den Einstieg, da die Bedienlogik der Papier-/PDF-Anleitung entspricht.

**Technischer Ansatz (grober Rahmen, Detaillierung später):**
- Reine **Anzeige- und Steuerschicht** auf Basis der ohnehin vorhandenen SCPI-
  Verbindung (kein neues Protokoll) – jede virtuelle Taste/jeder Regler mapped auf
  den passenden SCPI-Befehl aus Annex C (siehe bisherige Tastenrecherche: MOD, BW,
  MGC/SQU/TONE, ATT, AFC, LEV, FRQ, MEM, SAVE/RCL).
  - Ereignisse ohne SCPI-Äquivalent (SEL, ESC, LOCK, Spinwheel-Navigation) sind rein
    lokale UI-Zustände der virtuellen Frontplatte, ohne Geräteentsprechung.
- Statusanzeige (Pegel, aktuelle Werte) per Polling oder – falls das EB200 sowas
  unterstützt – Status-Reporting (`STATus:...`-Subsystem, siehe Annex C) statt
  reinem Polling.
- Darstellung als eigenständige Ansicht/Tab in der Web-GUI, unabhängig vom
  Wasserfall aktivierbar/deaktivierbar (nicht zwingend gemeinsam sichtbar).
- Grafisch: SVG- oder HTML/CSS-Nachbildung der Frontplatte (Layout aus Handbuch-
  Abbildung Fig. 3-1 als Vorlage), keine Fotorealismus-Anforderung.

**Einordnung:** Eher ein Komfort-/Ausbauziel für WS3 (Phase D) bzw. für die
gemeinsame Web-GUI insgesamt, nicht zeitkritisch für die Kernfunktion. Wird hier
nur als Idee festgehalten, Detailplanung folgt später.

## 3c. SDR-Wasserfall als Zwischenstufe (implementiert)

Der EB200 ist ein „wobbelnder Superhet": ein sequenzieller Scanner. Der D-SCAN
ist zwar weit (bis in den GHz-Bereich), aber zeitlich lückenhaft — während der
Empfänger auf Frequenz A steht, ist er für B blind — und liefert kein echtes
Momentanspektrum jenseits des IFPAN-Deckels (max. 1 MHz Span). Zwischen grobem
D-SCAN und schmalem IFPAN klafft damit eine Lücke.

Ein SDR aus dem Bestand füllt genau diese Lücke: Es liefert einen **lückenlosen
FFT-Wasserfall** über seine Momentanbandbreite (je nach Gerät ~2–20 MHz) als
mittlere Zoomstufe. Dreistufige Kaskade:

    D-SCAN (weit, kalibriert, lückenhaft)
        → SDR-Wasserfall (einige MHz, lückenlos, relativ/unkalibriert)
            → IFPAN (≤ 1 MHz, kalibriert)

Zusätzlicher Nutzen: Der EB200 ist *ein* Empfänger und kann immer nur *einen*
Modus (D-SCAN ODER IFPAN). Der SDR-Wasserfall läuft davon unabhängig weiter und
bleibt als **persistente Kontextebene** sichtbar, auch während der EB200 zoomt.

**Grenze:** „Lückenlos" gilt nur *innerhalb* der Momentanbandbreite des SDR;
eine breitere Bereichsauswahl wird darauf geklemmt.

### Dienst und Bedienung

- Eigenständiger Dienst `sdr_waterfall.py` auf **Port 8030**, getrennt von den
  Kernservern; separat oder optional über `start_eb200.py` gestartet.
- In der D-SCAN-Ansicht schaltet der Toggle **„⊕ Ergänze mit SDR-Wasserfall"**
  ein Geräte-Dropdown frei (Verfügbarkeitsanzeige, geladen von :8030).
- **D-SCAN → SDR:** Bei aktivem SDR-Modus öffnet ein Ziehen in der D-SCAN-
  Übersicht den SDR-Wasserfall (neues Fenster :8030, per `?device&center&span`);
  Shift+Ziehen bleibt der Blockverwaltung vorbehalten.
- **SDR → IFPAN:** Ein Klick im SDR-Wasserfall ruft den bestehenden Endpoint
  `/api/zoom?freq=` auf :8000 → der EB200 geht in IFPAN. Die :8000-Seite schaltet
  bei eintreffenden IFPAN-Frames **selbst** in die Zoom-Ansicht (auch bei
  externem Auslöser), und der SDR-Wasserfall holt das :8000-Fenster nach vorn.

## 3d. Datenformat-Architektur: Session-DB (D-SCAN) vs. Specbin (IFPAN)

Im Projekt existieren **zwei getrennte Persistenzformate** für aufgezeichnete
Pegeldaten — und zwar bewusst, nicht historisch gewachsen. Der Unterschied
folgt direkt aus der Form der Daten:

- **D-SCAN-Sessions** → SQLite je Session (`sessions/*.db`, Schema
  `session/segment/sweep`; Writer `eb200_core/session_store.py`, siehe WS1 3c;
  Lesen/Auswerten `analyze_sessions.py` :8020 und `session_analyze.py`).
- **IFPAN-Spektrumaufnahmen** → selbsttragendes Flatfile (`records/*.specbin`
  plus Sidecar `*.specbin.json`; Writer `eb200_core/spectrum_store.py`, Reader
  `eb200_core/spectrum_reader.py`; Wiedergabe `analyze_spectrum.py` :8040).

| Kriterium | Session-DB (D-SCAN) | Specbin (IFPAN) |
|---|---|---|
| Geometrie über die Aufnahme | **variabel** — Bereich/Schrittweite/Bin-Zahl können sich mitten in der Session ändern (`params_changed` → neues Segment mit ggf. anderer `n_bins`) | **konstant** — ein festes Fenster (Mitte/Span/BW), gleiche `n_window` für jeden Frame |
| Zeilen-/Framegröße | variabel je Segment | **fix** → Frame *k* liegt bei `k · framesize` |
| Wahlfreier Zugriff | per SQL (ganzes Segment am Stück laden) | **O(1)-Byte-Seek** je Frame — genau das, was das Scrubbing („Videorekorder") braucht |
| Struktur | relational (session→segment→sweep), 1..n Segmente | flache Zeitreihe, kein Kopf in der Binärdatei |
| Live-Anhängen + gleichzeitiges Lesen | SQLite-WAL, „LIVE"-Erkennung über die WAL-Beidatei | Flatfile-Anhang; Reader behandelt einen abgeschnittenen Tail nur defensiv (Absturzschutz), kein echtes Nebenläufig-Lesen im Betrieb |
| Metadaten | in den Tabellen | im Sidecar-JSON (Muster wie WAV in WS2) |

**Faustregel:** SQLite, wenn die Geometrie *variabel* ist und ganze Segmente am
Stück gelesen werden; Flatfile mit *fester* Framegröße, wenn die Geometrie
konstant ist und *wahlfreier Frame-Zugriff* (Springen, Frame-für-Frame vor/
zurück) gebraucht wird.

### Warum eine Vereinheitlichung des Dateiformats NICHT ratsam ist

Beide Richtungen verlieren etwas Wesentliches:

- **IFPAN in SQLite zwingen** → der O(1)-Byte-Seek entfällt; das Scrubbing über
  zehntausende Frames wird umständlicher und langsamer, ohne Gegenwert.
- **D-SCAN ins Flatfile zwingen** → scheitert an der variablen Geometrie: feste
  Framegröße ist dann unmöglich; man müsste pro Zeile einen Längen-Header
  voranstellen oder pro Segment eine eigene Datei anlegen — also im Grunde eine
  Mini-Datenbank von Hand nachbauen. Der Mehrsegment-Fall ist gerade *der*
  Grund für SQLite. (Auch wenn die meisten realen Sessions nur ein Segment
  haben, muss das Schema den Mehrsegment-Fall tragen.)

### Was bereits vereinheitlicht ist (und zählt)

Die *Semantik* ist längst einheitlich, nur der *Container* unterscheidet sich:

- **Rohe int16-Deci-dBµV** in beiden Formaten; die `/10`-Umrechnung passiert
  ausschließlich im Anzeige-/Analysepfad, nie auf den gespeicherten Rohwerten.
- **Trennung Nutzdaten ↔ Metadaten** (SQLite-Tabellen bzw. Sidecar-JSON).
- **Nur Python-Standardbibliothek** in beiden Schreib-/Lesepfaden
  (`eb200_core/`-Konvention).
- **Rein lesender Zugriff über gesandboxte Pfadauflösung** in beiden
  LAN-sichtbaren Viewern (`resolve_safe_path`, read-only).

Das ist gesunde Architektur (das richtige Werkzeug je Datenform), kein Wildwuchs.

### Empfehlung: Interface & Render-Schicht vereinheitlichen, nicht das Format

Die eigentliche Redundanz sitzt eine Ebene höher — in der **Darstellungs-/
Auswerteschicht**, nicht im Speicherformat. Colormap (`levelToColor`),
Perzentil-Auto-Skalierung und das scrollbare Wasserfall-Viewport liegen heute
dupliziert in `analyze_sessions.py` (:8020) und `analyze_spectrum.py` (:8040).
Eine Format-Vereinheitlichung würde diese Doppelung *nicht* beseitigen.

Zwei sinnvolle Konsolidierungen (für später vorgemerkt):

1. **Gemeinsames Lese-Interface.** `spectrum_reader.SpectrumRecording` bietet
   bereits `freq_axis()`, `frame(k)`, `bin_series(j)`, `times()`, `gaps()`. Ein
   Segment-Reader für die Session-DBs könnte dieselbe Signatur anbieten — beide
   Quellen sähen von außen aus wie „eine Zeit×Bin-Matrix mit Frequenzachse und
   echten Zeitstempeln". Ein Frontend könnte dann beide konsumieren
   („Interface vereinheitlichen, nicht das Format").
2. **Gemeinsame Render-Schicht.** Die geteilte Wasserfall-Zeichenlogik
   (Colormap, Cache-Aufbau via `putImageData`, Auto-Skala, scrollbarer Viewport)
   in ein einziges, gemeinsam ausgeliefertes JS-Modul ziehen. Das wäre die
   bewusste Ausnahme vom Muster „Inline-JS als Python-String, kein
   Static-File-Serving" — hier rechnet sie sich.

Querverweis: die D-SCAN-Seite dieses Formats ist in **WS1 3c** (Rohdaten-
Session-Store) im Detail beschrieben.

> **Usermanual-Hinweis:** Dieser Abschnitt ist wesentlicher Inhalt fürs spätere
> Benutzerhandbuch — dort erklärt er, *warum* Aufnahmen in zwei verschiedenen
> Datei­formen vorliegen (`.db` vs. `.specbin`) und welches Werkzeug (:8020 bzw.
> :8040) welche Aufnahme öffnet.

## 4. Technischer Ansatz

- **Steuerkanal (SCPI):** je nach Weg unterschiedlich:
  - D-SCAN: `[SENSe:]FREQuency:DSCan:STARt/STOP/SPAN/SPEed`, `[SENSe:]FREQuency:MODE DSCan`.
  - IFPAN: `FREQuency:SPAN`, `DISPlay:MENU IFPAN`, `TRACe:FEED:CONTrol IFPAN,ALWays`.
  - IQ: `[SENSe:]DEModulation IQ`, `SYSTem:AUDio:REMote:MODe` (2-Kanal-PCM-Modus).
- **Datenkanal (EB200-Protokoll, UDP):** Datagramme parsen – bei D-SCAN und IFPAN
  sind es fertige Pegel-/Spektrallinien (Tag/Format je Stream, siehe Abschnitt 3),
  bei IQ die Rohsamples (über den AUDIO-Tag) für eine eigene FFT.
- **Darstellung:** Spektrumzeile + Wasserfall (2D-Bild, das nach unten rollt), je
  nach Zoomstufe (D-SCAN breit/grob, IFPAN eng/fein). Rendering-Optionen:
  `pyqtgraph` (performant, für Echtzeit gut geeignet), alternativ `matplotlib`
  (einfacher, aber langsamer).
- **Alternativpfad:** vorhandener GNU-Radio-Block (`gr-eb200`) für den IQ-Weg,
  falls man ohnehin in Richtung GNU Radio gehen will (spätere Ausbaustufe).

## 5. Geplante Aktivitäten

**Phase A – Datenpfad**
- D-SCAN-Datagramm-Parser (`DScanTrace`) – ggf. Wiederverwendung des WS1-Parsers.
- IFPAN-Datagramm-Parser (`IFPan`-Tag) aufbauen.
- Rohdaten validieren (Header, Sequenz, Werte plausibel?).

**Phase B – Verarbeitung**
- D-SCAN: Pegel-Array übernehmen/skalieren (grobe Übersicht).
- IFPAN: Spektrallinien direkt übernehmen/skalieren (feine Ansicht).

**Phase C – Visualisierung**
- Live-Spektrum + rollierender Wasserfall für D-SCAN (Übersicht) und IFPAN
  (Zoom), Farbskala, dB-Bereich.
- Bedienelemente: Frequenz/Bandbreite/Span setzen, Pause, Autoskalierung.

**Phase D – Komfort & Ausbau**
- GUI-Konzept „Lupe": D-SCAN → interaktive Auswahl → automatischer IFPAN-Zoom →
  Übergabe an WS1 (siehe Abschnitt 3a).
- Marker/Cursor mit Frequenzanzeige, ggf. Peak-Hold.
- Optional Verknüpfung mit WS2 (Klick auf Signal → Aufnahme starten). **Umgesetzt:**
  WAV-Aufnahme („⏺ Audio“) und Live-Mithören („🔊 Mithören“, Web Audio API,
  Auto-Start beim Zoom) im IFPAN-Zoom – Technik und Grenzen in WS2 Abschnitt 11.
- Optionale IQ-Ausbaustufe (Rohdaten-Erfassung, eher WS2-nah) bzw.
  GNU-Radio-Variante.
- Optionale virtuelle Frontplatte als zuschaltbare Bedien-/Anzeigeansicht
  (siehe Abschnitt 3b).

## 6. Deliverables

- `waterfall.py` (UDP-Empfang D-SCAN + IFPAN + FFT/Aufbereitung + Live-GUI).
- Kurzdoku inkl. Voraussetzungen (Geräteoptionen `EB200DS`/`EB200SU`,
  Bandbreitengrenzen der drei Wege).

## 7. Abhängigkeiten / Voraussetzungen

- D-SCAN-Option (`EB200DS`) und IF-Panorama-Option (`EB200SU`) am Gerät
  vorhanden (beide bei diesem Gerät installiert, siehe docs/EB200_Geraeteprofil.md).
- Für IQ-Ausbaustufe: keine zusätzliche Option nötig (läuft über AUDIO-Kanal),
  aber auf die Bandbreitengrenze von ~32 kHz komplex begrenzt.
- Python mit `numpy`, GUI-Bibliothek (`pyqtgraph`/`PyQt` oder `matplotlib`).
- Genaue Datagramm-Formate (`DScanTrace`, `IFPan`-Trace, `AUDio`-OptionalHeader)
  aus Annex F bereits verifiziert (siehe Abschnitt 3).

## 8. Offene Entscheidungen (für unser nächstes Gespräch)

- GUI-Framework: `pyqtgraph` (Echtzeit) vs. `matplotlib` (einfach)?
- Eigenständiges Tool – oder integriert mit WS1/WS2 zu einer kleinen Monitoring-Oberfläche?
- „Lupe"-Interaktion: automatischer Span-Vorschlag beim D-SCAN→IFPAN-Zoom –
  welche Heuristik (z. B. fixe Stufe vs. an Peak-Breite angepasst)?
- Clustering-/Peak-Erkennungslogik im IFPAN-Ergebnis für die Übergabe an WS1
  (F-SCAN-Block bzw. M-SCAN-Speicherplatz) – analog zur D-SCAN-Triage in WS1 3.1.
- Perspektive GNU Radio (IQ-Ausbaustufe): gewünscht oder bewusst außen vor?

## 9. Umsetzungsstand & Betrieb (SDR-Wasserfall)

*Stand: 2026-08.*

### Geräteanbindung: native Vendor-CLIs statt SoapySDR-Python

Die Geräte werden **nicht** über die SoapySDR-Python-Bindings angesprochen,
sondern über ihr **natives Kommandozeilen-Tool als Subprozess** (`NativeCliSource`
in `sdr_waterfall.py`). Grund: Auf der Windows-Zielmaschine öffnen die
SoapySDR-Python-Bindings (radioconda) kein USB-SDR — `make()` scheitert mit
„no match" —, während die C++-Ebene und die nativen Tools sauber funktionieren.
Vollständige Begründung: `docs\SDR-Backend-Erkenntnisse.md`.

Ablauf: Der Dienst startet z. B. `hackrf_transfer -r - -f <Hz> -s <Hz>`, liest
das rohe IQ aus dessen stdout und rechnet die FFT mit numpy. Gerätekonfig in
`SDR_DEVICES` (`cmd`, `args`-Vorlage mit `{center}`/`{rate}`, `iq_dtype`).

**Folge — eine Umgebung:** Der SDR-Dienst braucht nur Python-Stdlib + numpy und
läuft damit in derselben `.venv` wie der EB200-Kern. radioconda/SoapySDR sind
für den Betrieb nicht mehr nötig.

### Betrieb (Kurzanleitung)

1. Im `.venv` einmalig: `pip install numpy`.
2. Natives Vendor-CLI vorhanden und Pfad in `SDR_DEVICES` korrekt (HackRF:
   `hackrf_transfer` aus `C:\HackRF\bin`).
3. Start: `python start_eb200.py` (Wasserfall :8000, Blockverwaltung :8010 und —
   optional — SDR-Wasserfall :8030, alle mit demselben Python), oder der
   SDR-Dienst separat mit `python sdr_waterfall.py`.
4. Ablauf: Toggle setzen → Gerät wählen → Bereich in der D-SCAN-Übersicht ziehen
   → SDR-Wasserfall (:8030) → hineinklicken → IFPAN auf :8000.

### Deliverables (SDR-Wasserfall)

- `sdr_waterfall.py` — SDR-Wasserfall-Dienst (Port 8030), natives CLI-Backend
  (`NativeCliSource`), FFT/Wasserfall, SSE, Klick-Handoff an IFPAN.
- Erweiterungen in `waterfall.py` — Toggle + Geräte-Dropdown, D-SCAN→SDR-
  Übergabe in `attachSelection`, automatische IFPAN-Ansichtsumschaltung bei
  externem Zoom-Auslöser, CORS-Header für die :8030-Abfragen.
- `sdr_probe_test.py` — Diagnose-Hilfsskript (Geräteerkennung/Öffnen).

### Gerätestatus

| Gerät | Anbindung | Stand |
|-------|-----------|-------|
| HackRF One | `hackrf_transfer` (nativ) | funktionsfähig |
| Airspy (R2, nicht HF) | `airspy_rx` (nativ) | vorgesehen — Pfad und `-a`/`-t`-Flags noch eintragen |
| SDRplay RSPdx | — | vorerst außen vor (API-/dienstbasiert, kein einfaches stdout-IQ-CLI) |

Damit sind die zugehörigen Punkte aus Abschnitt 8 für den SDR-Wasserfall im Kern
beantwortet.

## 10. Langzeit-Recording (12–24 h): Grenzen & Kalkulation

*Stand: 2026-08. Betrifft die IFPAN-Bin-Aufnahme (`.specbin`) und die
D-SCAN-Session-Aufzeichnung; WAV-spezifische Grenzen der Audio-Aufnahme:
siehe WS2, Abschnitt 10. Wesentlicher Inhalt für das geplante Usermanual.*

### 10.1 Datenmengen

Grundformeln:
- WAV Audio (fixer Modus 6: 16 kHz / 16 Bit / mono): 32 kB/s.
- `.specbin`: Framegröße = 12 B Präfix + 2 B × n_window; Datenrate =
  Framegröße × Framerate. Die tatsächliche IFPAN-Framerate ist aus jeder
  bestehenden Aufnahme ablesbar: `frames_written / (stop_iso − start_iso)`
  aus der Sidecar-JSON.
- Session-DB: n_bins × 2 B je Sweep (BLOB) + SQLite-Overhead.

| Aufnahme | Rate | 1 h | 12 h | 24 h |
|---|---|---|---|---|
| WAV Audio (Modus 6) | 32 kB/s | 115 MB | 1,38 GB | 2,77 GB |
| `.specbin`, BW 9 kHz in 25-kHz-Span (n_window ≈ 361, 734 B/Frame), 15 fps¹ | 11 kB/s | 40 MB | 0,48 GB | 0,95 GB |
| `.specbin`, voller Frame (1001 Bins, 2014 B/Frame), 20 fps¹ | 40 kB/s | 145 MB | 1,74 GB | 3,48 GB |
| D-SCAN-Session-DB (Bsp.: 4000 Bins/Sweep, 1 Sweep/2 s) | ~4 kB/s | 15 MB | 0,17 GB | 0,35 GB |

¹ Framerate am Gerät verifiziert (2026-08, `validate_tag_streams.py`):
≈ 20 IFPAN-Datagramme/s bei den Testeinstellungen – die 20-fps-Zeile ist
damit der realistische Planungswert; die Rate bleibt aber von der
Mittelungszeit abhängig und ist je Aufnahme aus der Sidecar ablesbar
(Formel oben).

**Plattenplatz ist keine Grenze** – Audio + Bins parallel über 24 h bleiben
unter 7 GB. Die echten Grenzen liegen woanders:

### 10.2 Die echten Grenzen

1. **Sidecar erst bei `close()`** (`.specbin` wie WAV): Ein Absturz bei
   Stunde 23 hinterlässt eine Binärdatei ohne Sidecar → die eigene
   Tooling-Kette markiert sie als unvollständig. `spectrum_reader`
   verkraftet abgeschnittene Dateien (`truncated`), aber nur wenn die
   Sidecar existiert.
2. **WAV-4-GB-Grenze und `wave`-Header** – Details in WS2, Abschnitt 10.
3. **Kopplung an den Zoom-Loop**: `_stop_spectrum_recording` hängt im
   `finally` von `_run_zoom_loop` – jedes Ereignis, das den Zoom beendet
   (Zurück-Klick, Frequenzwechsel, Verbindungsproblem), beendet die
   Aufnahme mit.
4. **Auswerte-Kette als Flaschenhals**: `MAX_FRAMES = 8000` in
   `analyze_spectrum.py` entspricht bei 15 fps nur ~9 Minuten – von einer
   24-h-Aufnahme zeigt der Player nur den Anfang.
   `session_analyze.load_recording_matrix` lädt die komplette Matrix in
   numpy (24 h × 15 fps × 601 Bins × 2 B ≈ 1,5 GB int16, nach
   float-Konvertierung ein Mehrfaches).
5. **Windows-Betriebsumgebung**: Standby/Ruhezustand, NIC-Energiesparen,
   Windows-Update-Neustarts. Dazu UDP-Verluste bei kurzen
   Empfangspausen des Python-Prozesses, wenn der Socket-Puffer
   (`SO_RCVBUF`) nicht vergrößert wird.

### 10.3 Maßnahmen (fließen in das Long-Run-Konzept, Abschnitt 11, ein)

- **Stündliche Dateirotation**: Writer nach 60 min sauber schließen (inkl.
  Sidecar), nächste Datei öffnen – zwischen zwei Frames, ohne Datenverlust.
  Absturz kostet max. 1 h; jede Einzeldatei bleibt auswertbar.
- **Periodischer Flush + laufende Sidecar**: alle 5 min `f.flush()` und
  Sidecar-Zwischenstand schreiben (`"running": true`) → auch nach
  Stromausfall bleibt jede Datei interpretierbar.
- **Serverseitige Übersichts-Dezimierung** in `analyze_spectrum.py`: statt
  hartem Abschneiden bei `MAX_FRAMES` eine auf ≤ 8000 Zeilen verdichtete
  Max-Hold-Übersicht (blockweises Maximum, dank O(1)-Seek billig);
  Drill-down auf einen Zeitausschnitt lädt in voller Auflösung nach.
  Reine ANZEIGE-Verdichtung – gespeicherte Daten bleiben vollständig.
- **Betriebsfestigkeit**: Standby deaktivieren (powercfg bzw.
  `SetThreadExecutionState`), NIC-Energiesparen aus,
  Update-Aktivitätszeiten setzen, `SO_RCVBUF` ≈ 1 MB.
- **Plattenplatz-Wächter**: freien Platz vor Start gegen die Kalkulation
  prüfen und periodisch überwachen; bei Unterschreiten sauber stoppen.

## 11. Long-Run-Jobs (Batch-Konzept)

*Stand: 2026-08, Konzept abgestimmt, Umsetzung ausstehend.*

### 11.1 Grundprinzip

Ein Dauer-Recording ist ein **Batch-Job**: im D-SCAN- bzw. IFPAN-View
konfiguriert, dann vollständig **serverseitig** im Controller ausgeführt.
Der Browser ist nur Konfigurator und Zuschauer – Schließen des Browsers
beeinflusst den Job nicht. Während ein Job läuft, ist der Empfänger
**gesperrt**: jeder mutierende API-Endpoint (`/api/range`, `/api/speed`,
`/api/zoom`, `/api/dscan_bw*` …) lehnt serverseitig ab (keine reine
UI-Sperre – ein zweites Browserfenster darf den Lauf nicht zerstören).
Konsequenz: während eines D-SCAN-Long-Runs kein IFPAN-Zoom (ein Empfänger,
ein Modus); Klick in den Wasserfall setzt höchstens einen Marker. Die
Session-Aufzeichnung bleibt passiver Abgriff – der Live-Wasserfall läuft
während des Jobs normal weiter.

### 11.2 D-SCAN- vs. IFPAN-Job (Grundsatzentscheidung)

| | D-SCAN-Long-Run | IFPAN-Long-Run |
|---|---|---|
| Zweck | Überblick, Triage („wo war was") | detaillierte Analyse („wie sah es genau aus") |
| Speicherung | **Max-Hold-Dezimierung**, User wählt Zeitraster | **volle Framerate, KEINE Dezimierung** |
| Format | neu: `eb200-dscan-maxhold-v1` (Max + Trefferzähler) | bestehendes `eb200-ifpan-bins-v1`, unverändert |
| Segmente | keine (Parameter eingefroren) | keine |
| Gemeinsam | Job-Manifest, Zeitplan, stündliche Rotation, laufende Sidecar, Empfänger-Sperre, konfigurierbare Ablagen | |

Begründung (gehört so ins Usermanual): Im D-SCAN ist der Überblick wichtig
– Dezimierung macht einen 24-h-Lauf kompakt und auswertbar. Im IFPAN ist
die detaillierte Analyse der Zweck – eine Zusammenfassung würde genau das
wegwerfen, wofür der Lauf gestartet wird. Anzeige-Dezimierung beim
Abspielen (10.3) ist davon unabhängig und kein Widerspruch.

### 11.3 Max-Hold-Dezimierung mit Trefferzähler (nur D-SCAN)

Der Dezimierer sammelt k Sub-Frames (k aus gewähltem Zeitraster ×
gemessener Sweep-Rate) und schreibt pro Bin ZWEI Werte:
- **Max-Pegel** (int16, Deci-dBµV): höchster Wert des Intervalls – kurze
  Bursts bleiben sichtbar (Prinzip wie `CALC:IFP:AVER:TYPE MAX` am Gerät,
  softwareseitig). Mittelwert wäre falsch: ein Burst in 1 von 15
  Sub-Frames würde um ~12 dB heruntergerechnet.
- **Trefferzähler** (uint8): in wie vielen Sub-Frames lag der Bin über
  (Intervall-Max − 6 dB). Zähler × Sub-Frame-Intervall ≈ Aktivitätsdauer;
  Zähler/k = Duty pro Intervall und Bin. Beispiel: 15 Sub-Frames/Intervall,
  100-ms-Burst → Zähler 1–2 → dokumentiert als „≈ 67–133 ms";
  Dauerträger → Zähler 15.
- Frameformat damit: `seq + t + n × int16 (Max) + n × uint8 (Zähler)`;
  Sidecar dokumentiert `decimation_k`, gemessene Sub-Frame-Rate und die
  Schwellenregel (−6 dB relativ zum Intervall-Max).
- Prinzipbedingte Grenzen (Usermanual): Lage des Bursts im Intervall
  unbekannt; mehrere kurze Bursts im selben Intervall nicht von einem
  längeren unterscheidbar; Dauerauflösung = Sub-Frame-Intervall. Feinere
  Zeitstruktur → gezieltes undezimiertes Kurz-Recording bzw. Audio.
- `seq` ist beim D-SCAN-Flatfile ein monotoner SWEEP-Zähler (ein Sweep =
  mehrere UDP-Pakete), nicht die Datagramm-Sequenznummer; Lücken =
  Zähler-Sprung plus t-Delta.
- Auswertung: Hover/Klick zeigt Pegel + Aktivitätsdauer; optionale
  Zweitansicht färbt nach Aktivitätsdauer statt Pegel (Burst vs.
  Dauerträger auf einen Blick).

### 11.4 Konfigurationsmaske (im D-SCAN- bzw. IFPAN-View)

Vorbelegung aus den aktuellen Live-Einstellungen der jeweiligen Ansicht;
Job-Typ ergibt sich aus dem Kontext, ist sichtbar und umschaltbar.

| Parameter | D-SCAN-Job | IFPAN-Job |
|---|---|---|
| Frequenz | Start/Stop (vorbelegt: aktueller Bereich) | Mitte + Span (vorbelegt: aktueller Zoom) |
| Empfänger-Setup | Speed, ggf. BW-ZOOM/BW | Demodulation, ZF-Bandbreite |
| Kurztext | frei (Manifest + Joblisten) | dito |
| Start | vorbelegt: JETZT (= Sofortstart per OK), editierbar auf Zukunft | dito |
| Ende | Dauer (Presets 6/12/24 h, frei) oder Endzeitpunkt | dito |
| Dezimierung | 1 Frame je Sweep / 5 s / 15 s / 30 s / 60 s (Max-Hold) | ENTFÄLLT – zeigt informativ „≈ n Frames/s, volle Auflösung" |
| Rotation | Default 60 min | dito |
| Speicherort | Auswahl konfigurierter Ablagen | dito |
| Zusatz-Audio | – | optional WAV parallel ☐ (Hardware-Validierung nötig, 11.7) |
| Platzbudget | berechnet (gemessene Rate × Dauer ÷ Dezimierung) gegen freien Platz der GEWÄHLTEN Ablage; zu wenig Platz → Start verweigert | dito |

Kein separater „Sofort starten"-Knopf – spontanes Mitschneiden bleibt
Sache der bestehenden ⏺-Buttons (Live-Recording).

### 11.5 Jobmanagement

- **Ablage**: ein Verzeichnis je Job (`<Ablage>\longrun_<jobid>\`, jobid =
  Zeitstempel) mit `job.json` (Manifest: Typ, eingefrorene Parameter,
  Zustand, Zeitplan, Dateiliste, Statistik, Heartbeat) + Stundendateien.
  Manifest wird alle 5 min mitaktualisiert (= laufende Sidecar auf
  Job-Ebene).
- **Job-Index** `jobs/index.json` im Repo: jobid → Ablagepfad + Zustand.
  Übersicht funktioniert auch, wenn eine Ablage-Platte gerade fehlt
  (Detailansicht meldet „Ablage nicht erreichbar").
- **Zustände**: geplant → wartet auf Start → läuft → beendet | gestoppt
  (User) | abgebrochen (Grund im Manifest: Platte voll, Empfänger weg …).
  Zusätzlich „verpasst": Startzeit lag beim Serverstart in der
  Vergangenheit – kein stilles Nachholen, sondern Markierung + Angebot
  „jetzt nachholen". Verwaistes „läuft" beim Serverstart (Absturz) →
  „abgebrochen" mit letztem Heartbeat als Ende. KEIN Auto-Resume in v1.
- **Scheduler**: Thread im Controller von `waterfall.py`, prüft minütlich.
  Betriebsregel (Usermanual): geplante Jobs starten nur, wenn
  `waterfall.py` zur Startzeit läuft → Server wird Dauerdienst
  (Autostart, siehe Packaging-Thema).
- **Port-Regel (aus der Hardware-Validierung, 11.7)**: Jeder
  Aufnahme-Stream erhält einen eigenen, exklusiven UDP-Port mit genau
  EINEM Tag – nur so bleibt die Lückenerkennung über die
  Datagramm-Sequenznummer (die pro UdpPath läuft) zuverlässig. Der
  Bin-Recorder (18103, nur IFP) folgt der Regel bereits; Long-Run-Writer
  (D-SCAN-Sweeps, IFPAN-Bins, Audio) bekommen je eigene Ports. Der
  Display-Pfad (18102, IFP+AUD) ist davon unberührt – dort wird
  angezeigt, nicht archiviert.
- **Konflikte**: ein Empfänger, ein Job – Zeitfenster-Überlappung wird bei
  der Anlage mit Klartext verweigert. Keine Warteschlange, keine
  Prioritäten (bewusst simpel).
- **Jobs-Seite**: `manage_jobs.py` auf **Port 8050** (Ein-Datei-Muster),
  Gruppen Laufend (Live-Statistik, Stopp mit Bestätigung) / Geplant
  (editier-/löschbar bis Startzeit) / Abgeschlossen (sortierbar,
  Kurztext-Suche, Direktlink „→ Auswertung" auf :8040). Job-ANLAGE bleibt
  in den Views (Vorbelegung aus Live-Einstellungen). D-SCAN-View zeigt
  bei aktivem Job ein Status-Banner (Bereich, Laufzeit, Ende, Sweeps,
  Datenmenge, Lücken, Stopp) statt der gesperrten Bedienelemente; nach
  regulärem Ende einmalig „beendet → zur Auswertung".

**Diagnose (T-206).** Ein haengender Scheduler-Thread liess sich bisher nur
an Symptomen erkennen (geplante Jobs starten nicht, Long-Run-Job endet nie) -
`eb200_core/sched_health.py` (`SchedulerHealth`, stdlib-only) gibt ihm einen
Herzschlag: `_job_scheduler_loop` meldet vor jeder Aktion `tick_start(phase)`
(`"pruefen"`, `"start <typ> <job_id>"`, `"stop <job_id> <grund>"`) und nach
jedem Durchlauf `tick_end()` (Dauer > **5 s** → „langsamer Takt" auf stderr).
Ein eigener Wächter-Thread (`_sched_watchdog_loop`, ebenfalls 30-s-Takt)
prüft `should_dump()`: **120 s** ohne Herzschlag → EINMAL je Freeze-Episode
ein Thread-Dump (`faulthandler.dump_traceback` + Thread-Legende Name→ident,
siehe `dump_threads()`) nach `logs/freeze_<Zeitstempel>.txt`; „einmal je
Episode" heißt, dass `should_dump()` erst nach einer Erholung (`age_s`
zwischenzeitlich unter **60 s**) für die nächste Episode wieder scharf ist.
Ein GIL-Deadlock, der auch `faulthandler` selbst blockiert, entzieht sich dem
Wächter – dagegen steht zusätzlich `faulthandler.enable()` (fatale
Prozessfehler → stderr) und der manuelle, rein lesende Endpunkt
`GET /api/debug/threads` (schreibt `logs/threads_<Zeitstempel>.txt` und
liefert dessen Inhalt zurück, bewusst NICHT in `_LOCKED_GET_PATHS`).
`/api/longrun/status` liefert den Zustand zusätzlich als `scheduler`-Feld
(`ticks`, `slow_ticks`, `phase`, `age_s`, `in_tick_s`, `last_tick_iso`,
`last_error`); `manage_jobs.py` zeigt bei `age_s` über **90 s** in der
Laufend-Box eine Warnzeile („Scheduler seit X s ohne Lebenszeichen") –
unabhängig davon, ob gerade ein Job aktiv ist, denn ein hängender Scheduler
verhindert ja gerade das Starten des nächsten geplanten Jobs.
Entscheidung: Herzschlag statt Timeout-Erhöhung – erst messen, wo es hängt,
statt Symptome mit einem größeren Timeout zu überdecken.

### 11.6 Speicherorte & Pfad-Sandbox (sicherheitsrelevant)

Konfigurierbare Ablagen (z. B. `E:\EB200\records`, `F:\Langzeit`) in
`storage_roots.json`. Die Auswerte-Server (:8020/:8040) erzwingen heute
EINE Sandbox-Wurzel – mit externen Ablagen wird `resolve_safe_path` auf
eine **Whitelist genau dieser konfigurierten Wurzeln** erweitert (nicht
„ganze Laufwerke"). Die Konfigdatei definiert damit, was ein LAN-Client
lesen darf → nur lokal bzw. bewusst änderbar. UNC-/Netzlaufwerke in v1
bewusst ausgeschlossen (Latenz/Abrisse eigenes Kapitel).

### 11.7 Offene Punkte & Umsetzungsreihenfolge

- **Hardware-Validierung: ERLEDIGT** (2026-08, `validate_tag_streams.py`,
  zwei unabhängige Läufe, identisches Ergebnis):
  - IFPAN-Doppelverteilung an zwei UdpPaths: **JA** – beide Ports je
    20,20 Datagr./s, 0,0 % Abweichung.
  - `TAG:ON AUD` additiv zu aktivem IFP auf demselben Pfad: **JA** –
    AUD 33,4 Datagr./s neben IFP 20,20 Datagr./s, IFPAN-Rateneinbruch
    durch Audio: 0,0 %.
  - Nebenbefund mit Design-Konsequenz: Die Datagramm-Sequenznummer läuft
    PRO UdpPath, nicht pro Tag – auf einem Pfad mit zwei aktiven Tags ist
    Lückenzählung je Tag nicht zuverlässig interpretierbar. Regel daraus:
    siehe 11.5 („ein Port, ein Tag" für Aufnahme-Streams).
  Der IFPAN-Long-Run mit optionalem Parallel-Audio ist damit
  hardware-seitig freigegeben.
- Zerlegung: (1) diese Doku; (2) Hardware-Validierung; (3)
  D-SCAN-Flatfile-Writer (`eb200-dscan-maxhold-v1`) +
  `spectrum_reader`-Verallgemeinerung (Formatkennung/Frequenzachse
  parametrisieren) inkl. Selbsttests; (4) Job-Store, Manifest, Scheduler,
  Empfänger-Sperre im Controller; (5) Maske + Status-Banner; (6)
  `manage_jobs.py` (:8050); (7) **Sandbox-Whitelist in den
  Auswerte-Servern: ERLEDIGT (T-201)**. Jeder Schritt ein eigener CC-Prompt
  mit frischem `EB200_sources.txt` dazwischen.
- **Sandbox-Whitelist aller storage_roots (T-201).** `:8020`/`:8040`/`:8070`
  hatten bis dahin je eine eigene, wortgleiche `resolve_safe_path()`/
  `to_relative()`-Kopie, geprüft nur gegen die eigene Wurzel
  (`SESSION_ROOT`/`SPECTRUM_ROOT`/`AUDIO_ROOT`) - eine Aufnahme in einem
  externen `storage_roots`-Eintrag (T-1205) war fürs Auswerten unerreichbar.
  `eb200_core/path_sandbox.py` (stdlib-only) verallgemeinert die Prüfung auf
  eine Root-Liste (`sandbox_roots()`: Repo-Root immer an Index 0, danach
  jeder `storage_roots.json`-Eintrag, je Request neu gelesen - Änderungen an
  der Datei wirken ohne Server-Neustart) und liefert `resolve_within()`
  (Root-Vergleich IMMER vor der Endungsprüfung, sonst wäre aus der Fehlerart
  ablesbar, ob eine Datei mit passender Endung außerhalb der Sandbox
  existiert) sowie `to_portable()` für das **portable Pfadformat**: relativ
  mit `/` innerhalb des Repo-Roots, sonst absolut mit `/` - ein `../`-Pfad
  über das Repo-Root hinaus kommt darin nie vor (anders als der bisherige
  `os.path.relpath`, der bei einem Root auf einem anderen Laufwerk unter
  Windows sogar mit `ValueError` abgebrochen wäre). Die drei Auswerte-Server
  und `eb200_core/recording_index.py` (`_to_relative`/`_resolve_storage_root`
  sind seither Import-Aliase auf `path_sandbox`) nutzen dieselbe Prüfung.
  Nachtrag: `:8050` (Aufnahmeliste) durchsuchte anfangs trotzdem nur
  `record_dir`, nicht alle Roots (`_all_storage_roots()` behebt das für
  `_spectrum_rows()`/`_audio_rows()`, analog zum vorher schon
  root-übergreifenden `_orphan_rows()`), und `delete_recording()`/
  `set_recording_label()` ließen über `_resolve_within()` nur Pfade innerhalb
  des Repo-Roots zu - Löschen in einem externen Root war praktisch
  unmöglich; beides jetzt auf `path_sandbox`/`_all_storage_roots` umgestellt,
  `"ablage"` einheitlich der portable Pfad statt nur `basename()`.
- Parameter-Memory und Status-Seite (Inventur, Lebenszeichen aller
  Server, `/status` auf :8000) sind umgesetzt – siehe Abschnitt 12.
- Die Session-DB (SQLite) bleibt für den interaktiven Kurzbetrieb mit
  Parameterwechseln/Segmenten bestehen; Long-Run = Flatfile. Ablöse der
  DB allenfalls später, wenn interaktive Sessions entfallen.

## 12. Parameter-Memory & Status-Seite

*Stand: 2026-08, umgesetzt (Schritte 1, 1a, 1b, 2, 3).*

### 12.1 Zweck

Ein gemeinsamer Parameterspeicher für das ganze System: eine Inventur
der EB200-Einstellungen per SCPI-Query („wie haben wir das Gerät
vorgefunden"), dazu die Laufzeitparameter und Lebenszeichen aller
Server. Anzeige auf einer Status-Seite, **nur lesend** – Schreiben ins
Gerät (Restore) ist bewusst nicht Teil von v1 (bräuchte Whitelist,
Set-Reihenfolge, Verifikation je Kommando via `scpi_verify`).

### 12.2 Bausteine

- `eb200_core/param_store.py` (stdlib-only): `ParamStore` (dotted keys,
  Namensräume `meta` / `eb200` / `app`), `snapshot_eb200()` (Query-
  Tabelle `EB200_QUERIES` nach Annex C / Handbuch Tab. 4-11, ~70
  Queries), `register_server()` (Lebenszeichen). CLI:
  `python -m eb200_core.param_store snapshot|show|set|--selftest`.
- Speicherort: `data/params/param_store.json` (repo-verankert über
  `__file__`, damit auch `block_admin.py` mit `cwd=eb200_core/` dieselbe
  Datei trifft); jeder Snapshot zusätzlich als Archiv unter
  `data/params/eb200_snapshots/eb200_<Zeitstempel>.json` (Basis für
  spätere Diffs). `data/params/` ist Laufzeitzustand, im `.gitignore`.
- `waterfall.py`: Inventur im **Hintergrund** – eigener Thread, eigene
  SCPI-Verbindung (`request_snapshot()` / `_run_snapshot_background()`),
  Start `SNAPSHOT_START_DELAY_S` = 3 s nach dem Hochfahren, zwischen den
  Queries `SNAPSHOT_QUERY_PAUSE_S` = 50 ms Pause; Flag
  `SNAPSHOT_ON_START`, nicht-fatal, nie zwei gleichzeitig. Manuell
  auslösbar über `POST /api/params/snapshot` (Button „Inventur jetzt"
  auf der Status-Seite). Spiegelung von `app.waterfall.*` (Bereich,
  Speed, BW …) aus dem Controller-Thread bei Range-/Speed-Wechsel.
  Routen `/status` (Seite) und `/api/params` (Store als JSON, bei jedem
  Aufruf frisch von der Platte, plus `_snapshot_running`). Beide
  **nicht** in `_LOCKED_GET_PATHS` – auch während eines Long-Run-Jobs
  erreichbar.
- Alle sechs Server rufen in `main()` `register_server(name, port, extra)`
  auf → `app.<name>.{started_at, http_port, pid}` (+ z. B. `root`,
  `db_path`). Jeder View trägt einen „Status"-Link auf `:8000/status`.

Schlüsselschema:

```
meta.{schema, updated_at}
eb200.{meta{taken_at, idn, opt, n_ok, n_failed, duration_s, resyncs}, groups, params, errors}
app.<server>.{started_at, http_port, pid, ...}
```

### 12.3 Entscheidungen (und warum)

1. **Rohantworten statt Interpretation.** `eb200.params` hält die
   SCPI-Antwort als String, unverändert (`"98500000"`, nicht
   `frequency_hz: 98500000`). Gleiche Philosophie wie die int16-Deci-dBµV-
   BLOBs der Session-DB: der Speicher ist die Wahrheit vom Gerät,
   Umrechnung passiert nur im Anzeigepfad. Jede Query läuft einzeln mit
   anschließendem `SYST:ERR?`, damit Fehler eindeutig zuordenbar sind
   (FIFO-Problem der Fehler-Queue, siehe WS1 3.5); nicht beantwortete
   Queries stehen mit `null` + Fehlertext im Store – die Inventur ist
   zugleich ein empirischer Test, was Firmware V02.74 kann.
2. **Datei statt RAM.** Kein Cache im `ParamStore`: jeder `get()` liest
   die Datei, jeder `set()` ist lesen-ändern-schreiben. Grund: sechs
   Prozesse schreiben in dieselbe Datei; ein RAM-Stand pro Prozess würde
   beim Zurückschreiben die Einträge der anderen überbügeln. Kosten:
   ein Dateizugriff pro `get()` – bei wenigen KB JSON und GUI-Ereignis-
   raten belanglos. Regel für Aufrufer: in Schleifen einmal
   `get_namespace()` holen, nicht wiederholt `get()`.
3. **Lock-Datei statt `msvcrt`/`fcntl`.** Prozessübergreifende Sperre per
   `param_store.json.lock`, exklusiv angelegt mit `os.O_CREAT|os.O_EXCL`
   (auf Windows und POSIX atomar, stdlib). Timeout 5 s, verwaiste Locks
   (> 30 s) werden entfernt. `threading.Lock` zusätzlich für Threads im
   Prozess. Schreiben selbst bleibt atomar (`.tmp` + `os.replace`), daher
   brauchen Lesepfade keine Sperre. Bewährt: alle sechs Server schrieben
   beim Sammelstart innerhalb derselben Sekunde ohne Verlust.
4. **Resync nach Query-Timeout.** Antwortet das Gerät nicht innerhalb des
   Timeouts, kann die Antwort verspätet im Socket liegen und würde von
   der nächsten Query als deren Antwort gelesen – alle Folgewerte wären
   stillschweigend um eins verschoben. Daher nach jedem Timeout `*OPC?`
   bis zur Antwort `1` lesen (Zähler `meta.resyncs`). Der einzige bisher
   beobachtete Timeout (siehe 12.4) verschwand mit der Query-Korrektur;
   `resyncs` steht seither in allen Echtläufen auf `0` (71/71 OK) – der
   Mechanismus ist als Sicherheitsnetz per Selbsttest (simulierter
   Timeout + verspätete Altantwort) verifiziert, auf echter Hardware
   aber noch nicht ausgelöst worden.
5. **Status-Seite im Wasserfall statt eigener Server/Port.** `/status`
   und `/api/params` liegen auf `:8000`, wo auch der Snapshot entsteht;
   die anderen Views verlinken dorthin (`onclick` setzt den Hostnamen
   erst beim Klick, damit localhost wie LAN-IP funktionieren). Kein
   weiterer Prozess, kein weiterer Port. Einschränkung: ohne laufenden
   Wasserfall keine Status-Seite – als Kern des Systems vertretbar.
6. **Der Store ist Anzeige-Quelle, nicht Konfigurationsquelle.**
   `app.waterfall.dscan_*` spiegelt, was der Wasserfall gerade fährt; die
   Startwerte kommen weiterhin aus dem Gerät (`_read_dscan_config_from_
   device`, CMOS-persistent). Damit ist der frühere Backlog-Punkt
   „D-SCAN-Bereich über Neustart merken" erledigt – der Merker ist das
   Gerät, der Store zeigt es nur zusätzlich.
7. **Inventur im Hintergrund, nicht vor dem Sweep.** Die erste Fassung
   lief synchron im Controller-Thread *vor* `_configure_dscan()`, um den
   Gerätezustand „wie vorgefunden" festzuhalten – Folge: der Browser
   zeigte 5–7 s lang einen stehenden Wasserfall. Jetzt läuft die
   Inventur 3 s nach dem Start in einem eigenen Thread über eine
   **zweite SCPI-Verbindung**; der erste Sweep beginnt innerhalb von
   < 0,3 s, die Inventur dauert ~6 s und ist am Sweep nicht messbar
   (siehe docs/EB200_Geraeteprofil.md, „Mehrere SCPI-Clients"). Aufgegeben wurde damit
   nur die Reihenfolge „vor unserem Eingriff": die D-SCAN-Werte im
   Snapshot sind die von uns gesetzten – die aber per
   `_read_dscan_config_from_device()` ohnehin vom Gerät übernommen
   wurden, also identisch mit den vorgefundenen. Alle übrigen Parameter
   (Audio, Squelch, Antenne, Display …) fasst der Wasserfall nicht an.

### 12.4 Korrekturen an der Query-Tabelle

- `CALCulate:IFPan:TYPE?` (so in Handbuch Tab. 4-11) existiert nicht
  (`-113 Undefined header`); korrekt ist `CALCulate:IFPan:AVERage:TYPE?`
  (Handbuch 4.5, liefert MIN/MAX/SCAL/OFF). Tab. 4-11 ist an dieser
  Stelle ungenau – bei weiteren Abweichungen gilt die Befehlsbeschreibung
  in Kapitel 4.5, nicht die Tabelle.

### 12.5 Offen / Ausbau

- Status-Seite: Offen-Zustand der Gruppen bleibt über den 30-s-Refresh
  erhalten (umgesetzt); ein In-place-Update statt Neuaufbau wäre die
  nächste Stufe, falls Flackern stört.
- Diff zweier Snapshots (Archivdateien liegen dafür bereits vor).
- Restore aus einem Snapshot (Whitelist setzbarer Parameter, Reihenfolge
  DEM vor BAND wegen `-221 Settings conflict`, je Set einzeln per
  `scpi_verify`) – eigener Schritt, bewusst nicht in v1.
- Weitere `app.<server>.*`-Werte nur, wenn sie auf der Status-Seite
  tatsächlich gebraucht werden – kein Spiegeln auf Vorrat.
- **Befunde sichtbar machen (umgesetzt):** Rücklese-Abweichungen der
  SCPI-Verifikation und der Sweep-Watchdog melden jetzt per SSE-Typ `notice`
  in `#meta-o` (15 s gehalten), nicht mehr nur in der Konsole. Auslöser war
  die unbemerkte „Span too low“-Ablehnung (siehe Geräteprofil, D-SCAN
  Mindestspanne). Watchdog unterscheidet „0 Pakete“ (Gerät sendet nicht)
  von „unvollständig“ (Paketverlust).

## 13. GUI-Struktur (Redesign 2026-08)

Die Bedienoberfläche von `waterfall.py` wurde von gestapelten Bedienzeilen auf
ein festes Raster umgestellt: Kopfzeile, Aktionsleiste, seitliches
Einstellungs-Panel, Canvas-Bereich mit Overlays, einklappbare Skala-Leiste.
Umgesetzt in drei Schritten (a) Grundgerüst, (b) Overlays/Farbskala,
(c) Zoom-Ansicht – Commits 25e5c49, 3d0b592, 08c7260.

### 13.1 Aufteilung

| Bereich | Inhalt |
|---|---|
| Kopfzeile (`#headerBar`) | Marke, Ansichtsname (`#viewName`), Untertitel bzw. Mittenfrequenz (`#centerFreqBig`), REC-Pille, Status-Link, Panel-Umschalter |
| Aktionsleiste (`#toolbar` / `#toolbarZ`) | wirkt auf die **Darstellung**: Zum Anfang, Pause, JPG, Löschen, Raster, Marker, `[?]` |
| Seiten-Panel (`#sidePanel` / `#sidePanelZ`, Klasse `.side-panel`) | wirkt auf **Gerät/Aufnahme**: Scan-Bereich, BW/Speed, Darstellungsoptionen, S-Meter, Aufnahmeknöpfe |
| Canvas-Bereich | Zeit-Gutter, Spektrum/Achse/Katalog/Wasserfall, Farbskala; darüber die Overlays |
| Skala-Leiste (`#scaleBar`) | Farbskalen-Regler, einklappbar |

Leitlinie der Aufteilung: **Was die Anzeige verändert, gehört in die
Aktionsleiste; was das Gerät oder eine Aufnahme verändert, ins Panel.** Ein
Bedienelement hat genau eine Heimat – Doppelungen (Pause/Raster an zwei Stellen)
wurden bewusst aufgelöst.

Ansichtswechsel: `document.body.classList` trägt `zoom`; CSS blendet danach
Untertitel bzw. Mittenfrequenz um. Beide Panels teilen **einen** Offen/Zu-Zustand
(`setPanelOpen`), der Ansichtswechsel lässt ihn unangetastet. Das Panel der
Übersicht klappt nach „Bereich übernehmen" automatisch zu, damit es Klick und
Ziehen auf dem darunterliegenden Canvas nicht dauerhaft verdeckt.

### 13.2 Vorgehensweise: DOM-erhaltend mit ID-Inventar als Prüfgatter

Der Umbau verschob bestehende Elemente in neue Container und gestaltete sie per
CSS neu – **ohne** eine einzige Element-ID zu ändern. Das Script blieb dadurch
bis auf gezielte Ergänzungen unberührt.

Als Gatter diente ein Vergleich des ID-Inventars vor und nach jedem Schritt
(`Compare-Object` über die aus `HTML_PAGE` extrahierten `id="…"`), ergänzt um
zwei Prüfungen: keine `el('…')`-Referenz ohne Ziel, keine doppelte ID. Diese drei
Prüfungen fangen die typische Fehlerklasse eines DOM-Umbaus ohne laufende
Hardware ab und sollten bei künftigen Layout-Eingriffen wiederverwendet werden.

### 13.3 Technische Festlegungen und Fallstricke

- **Icons** sind Unicode-Glyphen, keine Icon-Fonts – konsistent zur Regel
  „keine externen Dateien".
- **Toggle-Knöpfe** (Raster, Marker) bleiben `<input type=checkbox>`; die
  Knopf-Optik samt Aktivzustand kommt aus `label.tbtn:has(input:checked)`. So
  bleiben `.checked` und `onchange` im Script unverändert gültig.
- **Overlays auf dem Canvas** (`#sweepBox`, `#sweepBoxZ`, `.sel-overlay`,
  `#bw-band-z`) tragen `pointer-events:none`, damit Klick, Ziehen und Tooltip
  unverändert auf den Canvas durchgehen.
  **Fallstrick:** `pointer-events:none` vererbt sich auf alle Kinder. Die
  Long-Run-Maske wird von `openLongrunPanel()` per `insertBefore` vor `#meta-o`
  bzw. `#meta-z` gehängt – beide liegen seit dem Redesign **im** Sweep-Kasten.
  Ohne `pointer-events:auto` auf `#longrunPanel` ist der Dialog mit der Maus
  nicht bedienbar (Klicks gehen hindurch auf den Wasserfall); der Tastaturfokus
  funktioniert dabei weiterhin, weshalb der Fehler in einer reinen
  Positionsmessung unsichtbar bleibt. Bei jedem weiteren Element, das dynamisch
  in einen Overlay-Container gehängt wird, ist dasselbe zu beachten.
  Nachtrag 2026-08: Das `insertBefore` in den Sweep-Kasten hatte einen zweiten
  Fallstrick — der Stacking-Context des Kastens (z-index:10) deckelte das
  z-index:50 des Dialogs, das Seiten-Panel (z-index:20) verdeckte ihn.
  `openLongrunPanel()` hängt den Dialog deshalb jetzt per `appendChild` direkt
  unter `<body>`; bei `position:fixed`-Elementen bestimmt die DOM-Position nur
  den Stacking-Context, nie die Platzierung.
- **Farbskala** (`canvas.colorbar`, je eine in Übersicht und Zoom): gezeichnet
  von `drawColorbar(lo, hi)`, aufgerufen aus `currentScaleBounds()` – dem
  einzigen Ort, an dem die je Frame **wirksamen** Grenzen entstehen (Auto oder
  Regler). Ein Cache auf die gerundeten Grenzen verhindert Flackern im
  Auto-Modus. Bei Pause zieht zusätzlich der `oninput` der Regler nach.
  `levelToColor()` liefert einen **CSS-Farbstring**, kein `[r,g,b]`-Array – der
  Rückgabewert geht direkt an `ctx.fillStyle`.
- **Temporal Dead Zone:** Eine Erstzeichnung darf nicht vor den `let`-Bindungen
  stehen, die sie liest. Funktionsdeklarationen werden gehoisted, `let`/`const`
  nicht – ein Aufruf oberhalb der Deklaration bricht mit
  `Cannot access 'x' before initialization` ab und **beendet den gesamten
  Script-Lauf**. Die Seite wirkt dann teilweise funktionsfähig, weil alles
  oberhalb der Fehlerstelle bereits ausgeführt wurde. Erstaufrufe deshalb direkt
  hinter die zugehörige Funktionsdefinition setzen.
- **Zwei Einstiegspunkte in die Zoom-Ansicht:** `el('zoomView').style.display =
  'block'` kommt in `startZoom()` **und** im SSE-Zweig für den von außen
  ausgelösten Zoom vor (z. B. Klick im SDR-Wasserfall auf Port 8030). Änderungen
  am Ansichtswechsel müssen beide Stellen erfassen.
- **Inhaltsbreite** `--content-width` auf 1040 px erhöht: Zeit-Gutter 64 +
  Canvas-Spalte 902 + Farbskala ~70.
- **S-Meter** ließ sich ohne Script-Eingriff von 430 auf 300 px verschmälern,
  weil Maske und Peak-Marker in Prozent rechnen und die Achse ein SVG mit
  `viewBox` ist.
- **Server-Hinweise** in `#meta-o` (Sperrmeldungen, „Setze neuen Bereich …")
  brauchen `noticeHoldUntil`, sonst überschreibt das nächste Sweep-Frame sie
  nach wenigen Millisekunden.

### 13.4 Nicht übernommen

- Pfeiltasten am Auswahlrahmen zum schrittweisen Verschieben des Zoom-Bereichs:
  wäre ein neues Bedienkonzept (Schwenken aus der Übersicht), nicht Redesign.
  Der Rahmen zeigt jetzt nur die markierte Start-/Stoppfrequenz.
- Übertragung des Rasters auf `analyze_sessions.py`, `analyze_spectrum.py`,
  `sdr_waterfall.py` – offen, wäre ein eigener, auf CSS und Kopfzeile
  begrenzter Schritt.

## 14. Katalog bekannter Frequenzen (`known_freq/`)

Der Katalog beantwortet beim Blick auf den Wasserfall die Frage „was liegt hier
eigentlich?", ohne dass man nebenher in Listen blättert: Bänder, Segmente und
einzelne Kanäle werden als Streifen unter der Frequenzachse eingeblendet.
Geladen wird er von `eb200_core/known_freq.py` (alle `known_freq/*.json`, ein
Verzeichnis-Scan ohne gepflegten Index), ausgeliefert über `/api/known_freq` und
gezeichnet von `drawKnownStrip` in `waterfall.py`; der Toolbar-Toggle
„Bekannte" blendet den Streifen ein und aus.

### 14.1 Bestand (Stand 08/2026, nach T-111 Katalog-Abgleich gegen die FNV und T-112 Wiener UKW-Doppelungen)

| Datei | Dienst | Quelle | Kanäle | Bänder |
|---|---|---|---|---|
| `Air.json` | Flugfunk VHF | ICAO Annex 10 Vol. V (8,33-kHz-Kanalraster); NAV-Band nur als Bereich | 2291 | 0 |
| `Amateurfunk_UHF.json` | Amateurfunk 70 cm | IARU-R1-Bandplan 430-440 MHz (vereinfacht); OE-Relais: Ablage -7,6 MHz | 66 | 1 |
| `Amateurfunk_VHF.json` | Amateurfunk 2 m | IARU-R1-Bandplan 144-146 MHz (vereinfacht) | 32 | 1 |
| `DAB_Band3.json` | DAB+ Band III | ETSI EN 300 401 / DAB+ Kanalraster Band III (Blöcke 5A-12D) | 32 | 1 |
| `FNV_AT_2026.json` | Frequenznutzung Österreich (FNV Anlage 2) | Frequenznutzungsverordnung 2023, BGBl. II Nr. 61/2023, Anlage 2 (Frequenznutzungsplan), 30 MHz - 3 GHz | 129 | 140 |
| `OE3GAS.json` | Gerhard's Einträge | (persönlich, unverifiziert) | 2 | 0 |
| `ORS_DVBT2_AT.json` | DVB-T2 Österreich (ORS) | ORS / Big Blue Marble, Fernsehsender nach Frequenzen, Stand 02.12.2025 | 502 | 1 |
| `ORS_UKW_AT.json` | UKW-Hörfunk Österreich (ORS) | ORS / Big Blue Marble, UKW-Hörfunksender nach Frequenzen, Stand 03.04.2026 | 976 | 0 |
| `UKW_BC.json` | UKW Wien (nicht-ORS) | Wiener Privatsender ohne ORS-Abstrahlung; ORS-Standorte siehe ORS_UKW_AT.json | 12 | 0 |
| `fup_30_88_mhz.json` | FUP 30-88 MHz | Frequenznutzungsplan AT (Auszug), IARU-R1 für 6 m | 42 | 0 |
| `intl_amateurfunk_baender.json` | Amateurfunk | IARU-Region-1-Bandplan | 0 | 15 |
| `intl_ism_srd.json` | ISM/SRD | ITU-ISM-Zuweisungen, ERC/REC 70-03 | 16 | 4 |
| `intl_not_sicherheit.json` | Not/Sicherheit | ITU-Zuweisungen, GMDSS | 6 | 0 |
| `intl_satelliten.json` | Satelliten | ITU-Zuweisungen, Betreiberangaben | 4 | 8 |
| `intl_zeitzeichen.json` | Zeitzeichen | ITU-Zuweisungen (Normalfrequenz- und Zeitzeichenfunk) | 5 | 0 |
| `ukw_cz.json` | UKW CZ | UKW-Senderliste Tschechien (Südmähren), Stand 08/2026 | 22 | 0 |
| `ukw_hu.json` | UKW HU | UKW-Senderliste Ungarn (Győr/Sopron/Kab-hegy), Stand 08/2026 | 23 | 0 |
| `ukw_sk.json` | UKW SK | UKW-Senderliste Slowakei (Bratislava/Kamzík), Stand 08/2026 | 16 | 0 |

Summe: **4177 Kanäle, 171 Bänder** aus 18 Dateien, 0 Ladefehler.

Quellen insgesamt: Frequenznutzungsverordnung AT (FNV Anlage 2, jetzt alleinige
Quelle für nationale Zuweisungsbänder 30 MHz-3 GHz) / ICAO / IARU-Bandpläne /
ETSI-DAB+-Kanalraster / ORS-Senderlisten (UKW, DVB-T2) / ITU-Zuweisungen /
UKW-Senderlisten Wien (ORS-Standorte plus Privatsender ohne ORS-Abstrahlung),
CZ, SK, HU – Stand 08/2026.

### 14.2 Festlegungen

**Oberhalb 3 GHz bewusst nichts.** Der Empfangsbereich des EB200 endet dort;
ISM 5,8/24/61/122/245 GHz, WLAN 5/6 GHz, 57–71 GHz und die Amateurbänder 6 cm
und 3 cm sind deshalb nicht aufgenommen. 13 cm ist auf 2320–2450 MHz gekürzt.

**FNV als Zuweisungsschicht.** Seit T-111 (`tools/known_freq_abgleich.py`,
Entscheidungsdatei `docs/known_freq_abgleich_entscheidungen.json`) ist
`FNV_AT_2026.json` die alleinige Quelle für nationale Zuweisungsbänder
30 MHz-3 GHz. Ein Band ausserhalb der FNV bleibt bestehen, wenn es Struktur
oder eine Dienst-Sicht trägt, die die FNV nicht hat (eigene Segmente,
Kanalraster, Bandplan, Satellitensystem), oder wenn es ausserhalb
30 MHz-3 GHz liegt. Ein Band ohne eigene Segmente, das nur einen
FNV-Bereich unverändert nachzeichnet, wird gelöscht - die zugrundeliegende
Zuweisung steht dann bereits als FNV-Band da. 22 Bänder wurden auf diesem
Weg entfernt, ein Band (`ham-6m`, 6-m-Amateurfunk) in die fachlich passende
Datei `intl_amateurfunk_baender.json` verschoben (die FNV kennt Amateurfunk
nur als eine von mehreren Nutzungen desselben Bereichs, nicht als eigenen
Bandplan-Eintrag).

**Doppelungen sind erlaubt und teilweise gewollt.** Ein Band kann weiterhin
mehrfach im Katalog erscheinen, wenn die zusätzliche Sicht etwas zeigt, das
die FNV nicht abbildet: 430-440 MHz steht sowohl als Amateurfunk-Bandplan
(`Amateurfunk_UHF.json`, mit Segmenten für Relais-Ablage und Bakenband) als
auch als zugrundeliegendes FNV-Zuweisungsband (`FNV_AT_2026.json`) im
Katalog - die Darstellung zeigt beide Balken übereinander. Ebenso dürfen
mehrere Kanäle auf **derselben** Frequenz liegen – im UKW-Band betrifft das
fünf Frequenzen, auf denen ein inländischer und ein ausländischer Sender
zusammenfallen (92,6 · 93,1 · 93,6 · 99,5 · 102,0 MHz). Die IDs bleiben durch
Präfix und Standort eindeutig.

**Eine Frequenz nur einmal je Sender.** Für ORS-Standorte ist `ORS_UKW_AT.json`
maßgeblich - dort steht jeder ORS-Sender genau einmal. Seit T-112 gilt das auch
für Wien: 14 Sender, die vor T-112 doppelt geführt wurden (einmal unter einer
neuen `ors-ukw-*`-id in `ORS_UKW_AT.json`, einmal unter ihrer alten id in
`UKW_BC.json`/`ukw_wien.json`), tragen ihren ORS-Eintrag jetzt unter der alten
id (Felder `legacy_id_from`/`legacy_label` verweisen auf die Herkunft) - der
doppelte Alt-Eintrag wurde gelöscht, die id selbst nie (siehe `IDs sind
stabil` unten, `tools/known_freq_wien_merge.py`). `UKW_BC.json` enthält
dadurch nur noch Wiener Privatsender ohne ORS-Abstrahlung; `ukw_wien.json`
ist entfallen (seine restlichen fünf Sender ohne ORS-Bezug wurden nach
`UKW_BC.json` übernommen).

**IDs sind stabil.** Scan-Sets werden später per `id` auf Katalogeinträge
verweisen. Eine einmal vergebene ID wird deshalb nicht umbenannt, auch nicht,
wenn ein Eintrag in eine andere Datei wandert oder das Label sich ändert.

**Ebene (`layer`) seit T-113.** Jede Datei kann `meta.layer` auf „zuweisung"
(Regelwerk, z. B. `FNV_AT_2026.json`) oder „dienst" (beobachtete/bekannte
Nutzung, Vorgabe ohne den Schlüssel) setzen; die Darstellung zeichnet beide
Ebenen in getrennten Streifen-Reihen (siehe 14.3).

### 14.3 Grenze der Darstellung

`drawKnownStrip` beurteilt die Zeichendichte über den **kleinsten Abstand**
zweier benachbarter Kanäle im Bild. Zwei Kanäle auf exakt derselben Frequenz
(im UKW-Band fünfmal: in- und ausländischer Sender) ergäben den Abstand 0 und
drückten damit die gesamte Gruppe unter `KNOWN_TICK_PX`. Deshalb werden
gleichfrequente Kanäle **vor** der Dichteprüfung zu einem Punkt gebündelt; das
Label führt beide Namen mit „·". Der Streifen-Tooltip zeigt dagegen nur den in
der Katalogreihenfolge ersten Treffer – wer beide sehen will, zoomt so weit,
dass Labels erscheinen. Gemessen: ab etwa 4 MHz Fensterbreite Ticks, ab etwa
2 MHz Labels (103–105 MHz → 45 px Abstand, genau an `KNOWN_LABEL_PX`).

**Zwei Ebenen statt einer Reihe (T-113).** Befund an echten Bildern: Baender
verschiedener Dateien uebermalten sich in Katalogreihenfolge (`fnv-470000`
loeschte `dvbt2-uhf` samt K-Segmenten vollstaendig), `useSegs` war Entweder-
oder (der Bandtitel verschwand, sobald alle Segmente breit genug waren, auch
wenn kein Segment im Bild lag), lange Titel wurden weggelassen statt gekuerzt,
und FNV-Einzelfrequenzen standen gleichwertig neben echten Sendern in der
Kanalreihe. Der Streifen (Canvas-Hoehe jetzt 50 statt 34 px, siehe T-115 unten)
trennt seither drei Reihen: Reihe 1 (y 0–12, `KNOWN_FNV_H`) zeichnet die
FNV-Zuweisungsebene (`layer: "zuweisung"`) mit `globalAlpha 0.55` (der Rahmen
tritt bewusst zurueck) und IMMER einem Titel bei y 5 (`knownFitText` kuerzt
statt wegzulassen); Segmente ersetzen den Titel dort nicht, sondern erscheinen
als schmaler Marker bei y 9–12 ohne eigenen Titel (Nachtrag nach Browser-
Validierung: Band- und Segmenttitel überlagerten sich anfangs in den 9 px der
Reihe, Befund 200–230 MHz) – der Segmenttitel steht seither nur im Tooltip
(`knownLinesAt`/`knownTextAt`, siehe unten). FNV-Einzelfrequenzen (Kanäle mit
Tag `fnv`) sind eigene Ticks bei y 9–12 in Reihe 1 und aus der Kanalreihe
ausgeschlossen (`knownVisibleChannels`). Reihe 2 (y 15–28,
`KNOWN_BAND_Y`/`KNOWN_BAND_H`) zeichnet alle übrigen Bänder wie bisher,
verteilt aber überlappende Bänder im sichtbaren Bereich auf zwei Spuren
(Cluster über die Pixel-Boxen, greedy Spur 0/Spur 1; ab dem dritten
überlappenden Band im selben Cluster teilen sich weitere Bänder Spur 1,
Übermalen dort akzeptiert). Reihe 3 (ab y 30, `KNOWN_CHAN_Y`) bleibt die
Kanalreihe; `drawKnownServiceRow` und die Kanalreihe positionieren
ausschließlich über diese Konstanten, keine Pixel-Literale im Zeichencode.

**Regionsfilter (T-114).** `station.json` im Repo-Root (nicht versioniert,
Beispiel `station.example.json`) trägt `region_tags` (z. B.
`["land-w", "land-n"]`); `eb200_core/known_freq.py`
(`load_station_config`) liest sie serverseitig je Request und liefert sie im
`/api/known_freq`-Client-JSON unter `region.tags` mit. Der Client
(`knownVisibleChannels`) blendet Kanäle mit mindestens einem `land-*`-Tag
aus, das nicht in `region_tags` steht; Kanäle ganz ohne `land-*`-Tag
(Flugfunk, Amateurfunk, Nachbarländer, `OE3GAS.json`) bleiben immer sichtbar.
Nur `waterfall.py` zeigt dafür einen Toolbar-Schalter „Region" (versteckt,
wenn `region_tags` leer ist); die anderen Server filtern ohne eigenen
Schalter nach `station.json`. Der Filter ist reine Anzeigefilterung – Katalog,
Scan-Set-Upload und Editor bleiben davon unberührt.

**Reihe 1 höher, Bekannte-Toggle klappt den Streifen ein (T-117).** Die
Layoutzahlen aus T-113/T-115 oben (Canvas-Höhe 50 px, Reihe 1 y 0–12) sind
überholt: Canvas jetzt 55 px, Reihe 1 (`KNOWN_FNV_H`) y 0–18 – Balken y 0–11
(Titel bei y 6), Marker/Ticks y 11–18 statt bisher y 9–12, damit Marker und
FNV-Einzelfrequenz-Ticks bei kleiner Schrift lesbar bleiben. Segmentmarker
dazu gedeckter (`rgba(255,255,255,0.35)` statt `0.55`, damit die kräftigeren
FNV-Ticks dominieren); FNV-Einzelfrequenz-Ticks in `#4ee` mit `lineWidth 2`
statt `#9be`/1 px. Reihe 2 (`KNOWN_BAND_Y`/`KNOWN_BAND_H`) jetzt y 20–33,
Reihe 3 (`KNOWN_CHAN_Y`) ab y 35 – Höhen/Abstände unverändert, nur
verschoben. Die sechs Streifen-Canvases (`known-o`/`known-z`/`known-f`/
`known-w`/`known-sp`/`known-s`) tragen `height="55"`; zwei Höhensummen
außerhalb des Blocks, die die Streifenhöhe fest einrechnen (`#time-o` und
`.colorbar` in `waterfall.py`, beide bündig mit `wf-o`/`wf-z`), sind auf
183 px (statt 178 px) nachgezogen.
Der Toolbar-Schalter „Bekannte" (`knownToggle`, nur `waterfall.py`) klappt
den Streifen jetzt komplett ein statt ihn nur schwarz zu zeichnen:
`el(id).style.display = knownEnabled ? '' : 'none'` für alle `KNOWN_STRIPS`.
Die Canvases stehen im normalen Blockfluss von `canvasWrapO`/`canvasWrapZ`
(keine absolute Positionierung), collapsen also Platz; `time-o`/`colorbar`
sitzen daneben im selben Flex-Container mit fixem `margin-top` und folgen
dem Einklappen NICHT automatisch – bei ausgeschaltetem Katalog verschiebt
sich `wf-o`/`wf-z` nach oben, während Zeit-Gutter und Farbskala auf ihrer
Höhe bleiben (Versatz zur Browser-Validierung offen, siehe PLAN.md T-117).
Nachtrag: FNV-Einzelfrequenzen erscheinen im Streifen-Tooltip als erste
Zeile „FNV: <Label> · f MHz" (T-118).

**Streifen-Feinschliff (T-115).** Browser-Validierung von T-113/T-114 zeigte
drei weitere Punkte. Erstens zu wenig Luft zwischen Reihe 1 und Reihe 2 – der
Marker/Tick-Bereich der FNV-Ebene (y 9–12) rückte um 3 px nach unten, Reihe 2
und die Kanalreihe folgten (siehe Layout oben). Zweitens verschwanden mehrere
sichtbare Sender auf derselben Frequenz (z. B. in- und ausländischer
UKW-Sender nach Regionsfilter) optisch im gebündelten Label der Kanalreihe –
ein solcher Tick ist jetzt dicker und bernsteinfarben (`lineWidth 3`, `#fd8`)
statt in der Standardfarbe. Drittens zeigte der Streifen-Tooltip bei
Mehrfachbelegung oder bei sich überlappenden Bändern (Reihe 1 + Reihe 2 an
derselben Frequenz) nur einen zusammengequetschten Einzeiler. Die neue
Funktion `knownLinesAt(fHz, pxPerHz)` liefert stattdessen ein Array von
Zeilen – je Kanal/Sender bzw. Band eine eigene Zeile, bei Mehrfachbelegung
mit Kopfzeile „n Sender auf X.XXXXX MHz"; `attachKnownTooltip` verbindet sie
über `String.fromCharCode(10)` (kein Escape im Quelltext), `#tooltip` trägt
dafür `white-space:pre-line`. Für die einzeiligen Hover-Anzeigen der
Wasserfälle (kein Zeilenumbruch möglich) bleibt `knownTextAt` bestehen und
hängt bei Mehrfachbelegung nur `(+n-1)` an das Label des nächstgelegenen
Kanals an.

**Tooltip als eigenes Raster (T-116).** Der in T-115 eingeführte mehrzeilige
Text im geteilten `#tooltip` (dunkel, `white-space:pre-line`) wich einem
eigenen, HELLEN Element `#knownTip` – das geteilte `#tooltip` bleibt seitdem
ausschließlich dem Wasserfall-Hover (`attachTooltip`, außerhalb des Blocks)
vorbehalten. Datengrundlage ist die neue Funktion `knownInfoAt(fHz, pxPerHz)`:
liefert bei einem Kanaltreffer `{freq_hz, channels, hidden, bands:[]}` (`hidden`
= wie viele weitere Kanäle auf derselben Frequenz NUR der Regionsfilter
ausblendet), bei einem reinen Bandtreffer `{freq_hz:null, channels:[],
hidden:0, bands:[{title, seg, fnv}]}`, sonst `null`; `knownLinesAt`/
`knownTextAt` bauen unverändert auf ihr auf. `knownRenderTip(info)` baut
`#knownTip` per `createElement`/`textContent` neu auf (kein `innerHTML`) –
Kopfzeile, dann bei Kanaltreffern ein `kt-grid`-Raster mit fünf Spalten
(Programm, Standort, Land, Pol, ERP – aus den neuen optionalen
Katalogfeldern `site`/`programm`/`bundesland`/`pol`/`erp_w`, die nur
`ORS_UKW_AT.json` und `ORS_DVBT2_AT.json` tragen; Kanäle ohne sie zeigen
Label/Dienst statt leerer Zellen), eine Fußzeile `demod bw kHz · service`
nur bei über die Gruppe einheitlichen Werten, ein Hinweis „+n außerhalb der
Region" bei `hidden > 0`, und je berührtes Band eine `kt-band`-Zeile
(FNV zuerst, Klasse `kt-fnv`, Präfix „FNV: "). Neu aufgebaut wird nur bei
geändertem Schlüssel (Frequenz bzw. Band-Titel/Segment-Liste) – bei
gleichbleibendem Treffer zieht `attachKnownTooltip` nur die Position nach.
Platzierung: oberhalb des Streifen-Canvas im Spektrum (`rect.top - Höhe - 8`),
bei zu wenig Platz darüber als Fallback unterhalb; `left` folgt der
Mausposition, auf den sichtbaren Bereich geklemmt. Anheftbar: ein Klick auf
den Streifen bei sichtbarem Tooltip setzt die Klasse `pinned` (Rahmen/Schatten
hervorgehoben, `user-select:text` – Werte lassen sich markieren/kopieren);
gelöst wird über einen Klick außerhalb von `#knownTip` (Capture-Phase) oder
Escape, ein Klick INNERHALB des angehefteten Tooltips bleibt wirkungslos.

## 15. Katalog-Streifen in allen Servern: geteilter JS-Block

### 15.1 Entscheidung

Der Streifen (Bänder/Segmente oben, Kanäle unten, Tooltip) wird in
`waterfall.py` (8000), `sdr_waterfall.py` (8030), `analyze_sessions.py` (8020)
und `analyze_spectrum.py` (8040) **wortgleich inline** geführt – kein
gemeinsames statisches JS, gleiche Architekturentscheidung wie bei den
Nice-Ticks. Der Block steht in jeder Datei zwischen den Markern

    // >>> KNOWN_STRIP_BLOCK_BEGIN
    // <<< KNOWN_STRIP_BLOCK_END

**Referenz ist `waterfall.py`.** Änderungen am Block werden nur dort gemacht
und anschließend verteilt:

    python tools/sync_known_block.py sdr_waterfall.py analyze_sessions.py analyze_spectrum.py
    python tools/sync_known_block.py --check sdr_waterfall.py analyze_sessions.py analyze_spectrum.py

Das Werkzeug ersetzt den Block byteweise (Zeilenenden an die Zieldatei
angepasst) und vergleicht mit `--check` LF-normalisiert. Es existiert, weil das
Kopieren per Editor einmal die JS-Escape-Sequenz `\u00b7` zum echten Glyphen
„aufgelöst" hat. Seitdem enthält der Block keine Escape-Sequenzen mehr
(Trennzeichen `KNOWN_SEP = ' ' + String.fromCharCode(183) + ' '`).

### 15.2 Schnittstelle zwischen Host-Seite und Block

Der Block kennt **keine Canvas-IDs**. Jede Seite definiert vor dem Block

    const KNOWN_STRIPS = ['known-o', 'known-z'];   // waterfall.py
    const KNOWN_STRIPS = ['known-s'];              // sdr_waterfall.py
    const KNOWN_STRIPS = ['known-f'];              // analyze_sessions.py
    const KNOWN_STRIPS = ['known-w', 'known-sp'];  // analyze_spectrum.py

und ruft bei jedem Bereichswechsel `updateKnownStrip(id, loHz, hiHz)`. Der
Block merkt sich je ID den letzten Bereich (Toggle-Redraw, Tooltip) und hängt
den Tooltip an alle Streifen in `KNOWN_STRIPS`. Jede Seite braucht außerdem
`el()`, ein `<div id="tooltip">` mit dem CSS aus `waterfall.py` und den
Endpunkt `/api/known_freq` (Handler-Attribut `known_catalog`, in `main()` per
`load_catalog()` befüllt – wirft nie, Fehler landen in `.errors`).

`knownTextAt(fHz, pxPerHz)` liefert den Katalogtext zu einer Frequenz (Kanal
innerhalb von 5 px, sonst Band/Segment, sonst `''`). Die Hover-Anzeigen der
Wasserfälle in 8020 und 8040 hängen ihn an ihren Pegeltext an; die
Pixeldichte der jeweiligen Anzeige bestimmt die Toleranz.

### 15.3 Layout der Streifen

- 8000: zwischen Achse und Wasserfall (Übersicht und Zoom); das Zeit-Gutter
  hat einen oberen Rand in Höhe des gesamten Stapels darüber.
- 8030: unter der Achse (die dort unter dem Wasserfall liegt), damit das
  Zeit-Gutter unverändert bleibt.
- 8020: eigene `seg-axis-row` unter der Frequenzachse, gleicher 74-px-Spacer.
- 8040: unter dem Spektrogramm mit 70-px-Spacer (Gutter 66 + 4 wie der
  Datenteil der Achse) und Breite `dataW − 4`; zweiter Streifen unter dem
  Einzelspektrum (560 px).

### 15.4 Fenstergröße: Breite und Höhe der Wasserfälle

**8000 (D-SCAN/Zoom):** Breite (`CANVAS_W`) und Höhe (`VIEWPORT_ROWS`) werden
**einmal beim Laden** aus der Fenstergröße bestimmt und auf alle Canvas, Gutter
und Farbskalen übertragen. Bewusst nicht dynamisch: der Wasserfall ist ein
gebrannter Pixelpuffer, eine Änderung im Betrieb müsste die Historie skalieren
oder verwerfen. Bei Änderung um mehr als 100 px (Breite oder Höhe) erscheint
der Hinweis „Neu laden". Stellschraube unten: `BOTTOM_RESERVE_PX` (110).

**8040 (Spektrum-Auswertung):** Breite und Höhe des Spektrogramms folgen dem
Fenster **dynamisch**, weil der `resize`-Handler ohnehin Cache und Anzeige neu
baut. Breite: `innerWidth − SIDE_PX` (Befund-Navigator 260 + rechte Spalte
630 + Ränder), mindestens 720, Vielfache von 4. Höhe: bis `BELOW_PX` (150)
über dem Fensterboden; die rechte Spalte behält ihre Höhe.

**8020 (Session-Analyse):** Höhe aus der Fensterhöhe, Reserve `BELOW_PX` (112)
für Frequenzachse und Streifen darunter. Breite seit T-720 ebenfalls dynamisch
(`sizeSegWaterfall`): `innerWidth − SEG_SIDE_PX` (435 = Body-Padding 40 +
Navigator 260 + Spaltenlücke 16 + Kartenpolster 26 + Zeit-Gutter 74 +
Canvas-Rahmen 2 + Scrollbalken 17), mindestens 720, höchstens 3200, Vielfache
von 4. `fullCanvas`, `freqAxis` und `known-f` erhalten denselben Wert, weil
Achse und Katalogstreifen hinter demselben 74-px-Spacer sitzen.

### 15.5 Windows-Lehren aus diesem Abschnitt

- **SSE-Abbruch:** Schließt der Browser eine `/events`-Verbindung, meldet
  Windows `ConnectionAbortedError` (WinError 10053), nicht `BrokenPipeError`.
  Alle SSE-Handler fangen jetzt alle drei Ausnahmen; vorher füllten Tracebacks
  das Log bei jedem Reload.
- **Zeilenenden:** Der Arbeitsbaum enthält Dateien mit CRLF und mit LF
  (`sdr_waterfall.py` war LF, `waterfall.py` CRLF). Git normalisiert per
  `.gitattributes`; Werkzeuge, die Dateien vergleichen, müssen das ebenfalls tun.
- **Escape-Sequenzen in eingebettetem JS** sind beim Kopieren zwischen Dateien
  gefährdet; im geteilten Block werden sie vermieden (siehe 15.1).
