<!-- Status: Entwurf | Quellenstand: 2026-09-02 | offen: A5, D4, D8 -->

# 7. Die Monitoring-Modi im Überblick

## 7.1 Ein Empfänger, ein Modus

Das ist die Regel, aus der sich alles andere ergibt. Am Gerät steht immer
genau ein Frequenzmodus: `DSC` für den Suchlauf, `MSC` für den Speicherscan,
`CW` als Grundzustand, auf dem der IFPAN-Zoom aufsetzt. Ein Wechsel baut den
vorherigen Modus vollständig ab.

Die Suite setzt das auf vier Ebenen durch, und die sind unterschiedlich
streng:

**Beim Bedienen** genügt ein Klick — der Zoom hält den Sweep an,
**Zurück zur Übersicht** baut ihn wieder auf. Kein Konflikt, weil nur ein
Mensch am Browser sitzt.

**Während eines Long-Run-Jobs** sperrt der Server alle verändernden
Endpunkte: Zoom, Übersicht, Bereich, Speed, Messzeit, Span, Demodulation,
Bandbreite, BW-Zoom, Squelch, Verstärkungsregelung, Feinabstimmung und beide
Direktaufnahmen.
Lesende Zugriffe bleiben offen — man kann dem Job also beim Arbeiten zusehen,
nur nicht dazwischenfunken. Die Oberfläche graut die betroffenen
Bedienelemente aus.

**Beim Start eines MSCAN-Laufs** prüft der Server, ob eine Session-Aufnahme,
eine Spektrum-Aufnahme oder das Mithören läuft. Wenn ja, startet der Lauf gar
nicht erst, sondern endet sofort im Zustand „abgebrochen" mit Begründung.

> **Achtung:** Das gilt auch für einen geplanten Lauf. Wer abends den
> IFPAN-Zoom mit Mithören offen lässt und den Browser nur zuklappt, riskiert,
> dass der für 22 Uhr geplante MSCAN-Lauf sofort abbricht. Vor einem
> geplanten Lauf gehört der Zoom geschlossen.

**Bei der Job-Anlage** lehnt die Suite überlappende Zeitfenster ab: ein
Empfänger, ein Job.

## 7.2 Die vier Modi im Vergleich

| | D-SCAN | BW-Zoom | IFPAN | M-SCAN |
|---|---|---|---|---|
| Was das Gerät tut | Sweep über einen Bereich | Sweep über einen an die Bandbreite gekoppelten Ausschnitt | Panorama um eine Frequenz | Liste von Speicherplätzen abarbeiten |
| Auflösung | etwa halbe ZF-Bandbreite je Punkt | dieselbe, auf schmalem Span | fein, Bin-Auflösung | keine, nur Kanal offen oder zu |
| Audio | nein | nein | **ja** | **ja**, je Squelch-Öffnung |
| Erzeugt | Session | Session | Spektrum, Audio | Audio-Segmente |
| Geräteoption | EB200DS | EB200DS | EB200SU | keine |
| Kapitel | 8 | 8 | 9 | 10 |

Der wichtigste Satz für den Einstieg: **Die Bandbreite ist im D-SCAN der
Tempo- und Auflösungsregler**, nicht ein Filter für die Empfangsqualität.
Größere Bandbreite heißt größere Schritte, weniger Punkte, schnellerer Sweep
— und schmale Träger, die durchs Raster fallen.

## 7.3 Wer an wen übergibt

Fünf Übergaben, die man kennen muss:

- **D-SCAN → IFPAN.** Klick in die Übersicht. Der häufigste Weg.
- **D-SCAN → SDR-Wasserfall → IFPAN.** Ziehen markiert einen Bereich, der SDR
  zeigt ihn lückenlos, ein Klick dort schickt die Frequenz zurück an den
  IFPAN. Die Zwischenstufe existiert, weil zwischen einer 30 MHz breiten
  Übersicht und einem 200 kHz breiten Panorama sonst ein Sprung klafft.
- **Katalog → MSCAN.** Kanäle werden aus dem Katalog in eine Job-Definition
  gewählt und beim Laufstart in die Speicherplätze programmiert.
- **MSCAN-Lauf → Trefferliste → Audio.** Ein Segment führt von :8060 nach
  :8070 und wieder zurück.
- **Aufnahme → Auswertung.** Session nach :8020, Spektrum nach :8040, Audio
  nach :8070 — in der Regel über die Aufzeichnungsliste auf :8050.

Welche Ansicht von welcher aus erreichbar ist, zeigt die Karte in
Abschnitt 2.3.

## 7.4 Kanäle verwalten: vom Katalog in den Gerätespeicher

Vier Stufen, und die Unterscheidung zwischen Stufe 2 und 3 ist die, die
erfahrungsgemäß verwirrt:

1. **Katalog** — beliebig viele Kanäle, dauerhaft, mit stabilen Kennungen.
2. **Job-Definition** — eine Auswahl daraus, höchstens 1000 (so viele
   Speicherplätze hat das Gerät), in der Reihenfolge der Aufnahme, ohne
   Doppelte. Jederzeit änderbar.
3. **Lauf** — friert die Definition zum Startzeitpunkt ein. Kanäle, die
   inzwischen aus dem Katalog verschwunden sind, werden als „fehlend"
   markiert statt still zu verschwinden.
4. **Speicherplätze MEM0 bis MEM999** — werden beim Laufstart programmiert
   und danach wieder freigegeben.

Dazu die Scan-Parameter, die in der Definition stehen: Betriebsart
**Inventur** (ein Durchlauf, endet von selbst) oder **Überwachung** (läuft
bis zur Endzeit), Verweildauer, Haltezeit nach dem Signalende, Squelch aus
dem Speicherplatz oder nicht, und eine Mindestdauer, unter der Aufnahmen
verworfen werden.

> **Achtung:** Bei der Inventur muss die Verweildauer endlich sein. Sonst
> hält ein Dauerträger den Squelch offen, und der Durchlauf bleibt für immer
> auf diesem Kanal stehen.

## 7.5 Nebenwirkungen, die vom Gerät kommen

Drei Dinge wirken über die aktuelle Ansicht hinaus, weil sie am Gerät global
sind:

- **Der Squelch** gilt für das ganze Gerät, nicht nur für den IFPAN-Zoom.
- **Die Verstärkungsregelung** ebenso. Wer für eine IQ-Aufnahme auf `MGC`
  umschaltet, lässt das Gerät auch danach mit fester Verstärkung laufen
  (Abschnitt 9.6).
- **Die Messzeit** bei Speed MTIME ebenso. Die Suite setzt sie deshalb bei
  jedem anderen Speed aktiv auf den Vorgabewert zurück.
- **Die Demodulation** wird beim Aufbau des D-SCAN bedingungslos auf FM
  gesetzt. Eine am Frontpanel gewählte Betriebsart überlebt die Rückkehr in
  die Übersicht also nicht. Der Grund: SSB verträgt keine Bandbreiten über
  9 kHz, und der Sweep würde stehenbleiben.

Ausführlich behandelt Kapitel 15 diese Zusammenhänge.

## 7.6 Exemplarische Abläufe

Vier typische Wege durch die Suite, jeweils ausführlich in Kapitel 12:

**Unbekanntes Signal einordnen.** D-SCAN über das fragliche Band, Katalog-
Streifen einblenden, verdächtige Stelle anklicken, im IFPAN zuhören und bei
Bedarf ein paar Minuten Audio mitschneiden.

**Kanalgruppe über Nacht überwachen.** Kanäle im Katalog anlegen, MSCAN-
Definition daraus zusammenstellen, Lauf für die Nacht planen, am Morgen die
Trefferliste durchgehen und stille Kanäle aus der Definition entfernen.

**Ein Band über zwölf Stunden aufzeichnen.** Bereich im D-SCAN einstellen,
Long-Run-Job anlegen, Suite laufen lassen, danach die Session in :8020
segmentweise sichten.

**Den Katalog verbessern.** Aus einer Auswertung heraus die tatsächlich
beobachteten Frequenzen notieren und in :8010 in die eigene Katalogdatei
eintragen.
