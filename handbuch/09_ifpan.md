<!-- Status: Entwurf | Quellenstand: 2026-09-02 | offen: A5, A9, A12, A13, A14, B1 -->

# 9. IFPAN — Zoom auf eine Frequenz

Der D-SCAN zeigt, **wo** etwas ist. Der IFPAN zeigt, **was** es ist: ein
lückenloses Panorama um eine einzelne Frequenz, mit Ton, S-Meter,
Feinabstimmung und der Möglichkeit, Audio oder Spektrum-Bins auf die Platte
zu schreiben. Es ist die einzige Ansicht der Suite, in der man hört, was man
sieht.

Die Ansicht setzt die Geräteoption **EB200SU** voraus (siehe Kapitel 4).

## 9.1 Hinein und wieder heraus

Es gibt zwei Wege in den Zoom:

- **Klick in die D-SCAN-Übersicht** — in den Spektrum-Streifen oder in den
  Wasserfall. Der häufigste Weg.
- **Klick im SDR-Wasserfall** auf :8030. Der SDR schickt die angeklickte
  Frequenz zurück und holt das Hauptfenster nach vorn (Abschnitt 8.8).

Beim Eintritt wechselt die Kopfzeile auf **Zoom (IFPAN)** und zeigt die
Mittenfrequenz groß an, Spektrum und Wasserfall werden geleert — die
Frequenzachse ist eine andere als in der Übersicht, alte Zeilen würden nicht
mehr dazu passen. Bis das erste Frame eintrifft, steht in der Infozeile
`Zoome auf … MHz …`.

Zurück geht es mit **← Zurück zur Übersicht**. Der Server baut den D-SCAN
wieder auf und beendet dabei alles, was zum Zoom gehört: Mithören, eine
laufende Audio-Aufnahme, eine laufende Spektrum-Aufnahme.

> **Achtung:** Der Sweep der Übersicht steht, solange Sie im Zoom sind. Ein
> Empfänger, ein Modus — das ist die Regel aus Abschnitt 7.1.

<!-- Befund B1: Was mit einer laufenden Session-Aufzeichnung beim Wechsel in
     den Zoom geschieht, ist ungeklaert (Recorder bleibt offen, es kommen
     keine Sweeps mehr). Vor der Veroeffentlichung klaeren und hier
     ergaenzen. -->

## 9.2 Aufbau der Ansicht

Von oben nach unten:

- **Kopfzeile** mit der Mittenfrequenz, auf sechs Nachkommastellen genau,
  damit Hz-Schritte der Feinabstimmung sichtbar sind.
- **Werkzeugleiste** mit **Einstellungen**, **← Zurück zur Übersicht**,
  **Pause**, dem S-Meter und dem **?**-Knopf.
- **Einstellungspanel** links, einklappbar, mit fünf Blöcken: **Span**,
  **Demodulation**, **Squelch**, **Verstaerkung**, **Aufnahme**.
- **Mitte:** Infozeile als Einblendung, Einzelspektrum, Frequenzachse,
  Katalog-Streifen und darunter der rollende Wasserfall mit dem hellen
  Bandbreitenstreifen.
- **Rechts** die Farbskala in dBµV, **unten** die Skalenleiste.

Die Infozeile oben rechts im Spektrum nennt Mittenfrequenz, Anfang und Ende
des Fensters, dessen Breite, die Zahl der Punkte und die Uhrzeit des zuletzt
eingetroffenen Frames. Sie ist zugleich die Stelle, an der Ablehnungen und
Sperrmeldungen erscheinen; solche Meldungen bleiben ein paar Sekunden
stehen, bevor die laufenden Frames die Zeile wieder übernehmen.

Der Katalog-Streifen verhält sich wie in der Übersicht (Kapitel 6). Im Zoom
ist er besonders nützlich: Bei 25 kHz Span steht die Beschriftung eines
Kanals unmittelbar über dem Träger, den man gerade hört.

## 9.3 Span

Der Schieberegler im Block **Span** kennt sechs feste Stufen: 25, 50, 100,
200 und 500 kHz sowie 1 MHz. Andere Werte gibt es nicht — die Stufen stammen
aus dem Gerät, nicht aus der Suite. Die Vorgabe ist 200 kHz.

Die Beschriftung über dem Regler folgt der Bewegung sofort, angewendet wird
der neue Wert erst beim Loslassen. Der Zoom wird dabei mit derselben
Mittenfrequenz und der neuen Breite neu aufgebaut; Spektrum und Wasserfall
beginnen von vorn.

## 9.4 Modulation und ZF-Bandbreite

Der Block **Demodulation** hat zwei Auswahllisten:

| Feld | Werte |
|---|---|
| **Modulation** | AM, FM, USB, LSB, CW, IQ |
| **Bandbreite** | 0,15 · 0,3 · 0,6 · 1,5 · 2,4 · 6 · 9 · 15 · 30 · 50 · 120 · 150 kHz |

Es sind dieselben zwölf Filterstufen wie im D-SCAN, hier aber in ihrer
eigentlichen Rolle: als Empfangsbandbreite, nicht als Kanalraster.

> **Hinweis:** Der Frequenzkatalog lässt im Feld `demod` zusätzlich `PULSE`
> zu (Abschnitt 6.4). Die Auswahlliste im Zoom kennt diesen Wert nicht.

<!-- Befund A13: DEMOD_TOKENS in eb200_core/known_freq.py fuehrt sieben
     Werte, IFPAN_MODE_OPTIONS in waterfall.py nur sechs (ohne PULSE).
     Verhalten eines Katalogkanals mit demod: PULSE ist ungeklaert. -->

Nicht jede Kombination ist am Gerät zulässig. Passen Modulation und
Bandbreite nicht zusammen, erscheint unter den Listen eine Hinweiszeile der
Form „30 kHz mit USB nicht möglich (max. 9 kHz)". Betroffen sind **USB**,
**LSB** und **CW** oberhalb von 9 kHz sowie **IQ** oberhalb von 15 kHz. Der
Hinweis ist rein informativ — die Suite schickt die Wahl trotzdem ans Gerät,
das Gerät lehnt sie ab, und die Auswahllisten ziehen kurz darauf wieder auf
den tatsächlichen Gerätestand nach. Der Hinweis verschwindet dann von selbst.

> **Hinweis:** Beide Listen zeigen immer, was am Gerät wirklich eingestellt
> ist. Wer am Frontpanel etwas ändert oder die Seite neu lädt, findet hier
> keine veralteten Werte, sondern den Ist-Zustand — die Suite liest ihn
> laufend zurück.

Ein Wechsel von Modulation oder Bandbreite baut den Zoom neu auf, genau wie
ein Wechsel des Spans.

## 9.5 Squelch

Drei Bedienelemente:

| Element | Wirkung |
|---|---|
| **Squelch ein** | schaltet die Rauschsperre am Gerät ein und aus |
| Schwellenregler | −30 bis 110 dBµV, Vorgabe 10 dBµV |
| **folgt Skala min** | koppelt die Schwelle an den Regler **Skala min** der Zoom-Ansicht |

Auch hier gilt: Die Beschriftung folgt dem Ziehen, gesendet wird erst beim
Loslassen. Anders als Span, Modulation und Bandbreite wirkt der Squelch
sofort, ohne den Zoom neu aufzubauen.

**folgt Skala min** ist für die Arbeit am Wasserfall gedacht: Was farblich
unter der Skalenuntergrenze verschwindet, soll auch nicht zu hören sein. Ist
die Option aktiv, wird der Schwellenregler gesperrt und folgt dem
Skala-min-Wert; beim Ausschalten bleibt der zuletzt eingestellte Wert stehen.

> **Achtung:** Der Squelch ist am EB200 ein globaler Parameter. Er gilt nach
> dem Verlassen des Zooms weiter — unter anderem für einen MSCAN-Lauf, der
> seine Schwelle aus dem Speicherplatz übernehmen soll (Abschnitt 7.5 und
> Kapitel 15).

## 9.6 Verstärkung: AGC oder MGC

Der Block **Verstaerkung** hat zwei Bedienelemente:

| Element | Wirkung |
|---|---|
| **Regelung** | `AGC` (das Gerät regelt selbst) oder `MGC` (feste Verstärkung) |
| Pegelregler | −30 bis 110 dBµV, Vorgabe 50 dBµV; nur bei `MGC` bedienbar |

Bei `AGC` ist der Regler gesperrt. Der Wert wird wie beim Squelch erst beim
Loslassen gesendet und wirkt sofort; bei jedem Zoom-Eintritt stellt die Suite
den eingestellten Zustand am Gerät wieder her. Was am Frontpanel verstellt
wird, erscheint hier innerhalb weniger Sekunden.

Für das Hören und für Pegelablesungen ist `AGC` die richtige Wahl. `MGC`
braucht man, wenn die **Amplitude** der Aufnahme etwas bedeuten soll — vor
allem bei IQ-Aufnahmen, die später als Konstellation ausgewertet werden: Eine
nachregelnde AGC verschmiert die Konstellation radial, weil sie die
Amplitudeninformation während der Aufnahme verändert. Siehe Kapitel 11a.

> **Achtung:** `MGC` friert die Verstärkung auf dem eingestellten Pegel ein.
> Ein zu niedrig gewählter Pegel übersteuert bei starken Signalen, ein zu
> hoher lässt schwache im Rauschen verschwinden. Nach der Aufnahme gehört die
> Regelung zurück auf `AGC` — sie ist, wie der Squelch, am Gerät global.

## 9.7 S-Meter

Rechts in der Werkzeugleiste steht das S-Meter, beschriftet mit **Pegel**.
Die Skala reicht fest von −40 bis 80 dBµV.

Der Balken zeigt den **stärksten Pegel innerhalb der eingestellten
ZF-Bandbreite** um die Mittenfrequenz, nicht den stärksten des ganzen
Fensters. Ein Träger am Rand des Spans schlägt also nicht aus. Die Nadel ist
bewusst träge, damit sie ablesbar bleibt.

Daneben steht die Höchstwertmarke **Max** mit dem größten seit dem Beginn
gemessenen Wert. Sie steigt nur und fällt nie; ein Klick auf die Anzeige
setzt sie zurück. Von selbst zurückgesetzt wird sie beim Eintritt in den
Zoom, bei jedem Wechsel von Span oder Bandbreite und bei der Rückkehr in die
Übersicht — in all diesen Fällen wäre der alte Höchstwert nicht mehr
vergleichbar.

Der helle Streifen über dem Wasserfall zeigt dieselbe Bandbreite grafisch:
Er ist so breit, wie die eingestellte ZF-Bandbreite im Verhältnis zum Span
ist. Bei 150 kHz Bandbreite und 200 kHz Span füllt er fast das ganze Bild,
bei 2,4 kHz und 1 MHz Span ist er ein Strich. Das ist die schnellste Art zu
sehen, ob man das Signal überhaupt vollständig im Filter hat.

## 9.8 Abstimmen: Klick, Ziehen, Mausrad

Drei Wege, und sie unterscheiden sich in dem, was sie kosten:

**Klick** in Spektrum oder Wasserfall zentriert den Zoom auf die angeklickte
Stelle. Der Zoom wird dabei neu aufgebaut.

**Ziehen** schwenkt die Mittenfrequenz. Während der Bewegung verschiebt sich
nur das Bild, das Gerät wird nicht angefasst; erst beim Loslassen wird
einmal neu abgestimmt. Die Richtung folgt der Karten-Logik: Zieht man nach
rechts, wandert der Inhalt mit dem Zeiger nach rechts, die Mittenfrequenz
sinkt also.

**Mausrad** über Spektrum oder Wasserfall stimmt fein ab, ohne den Zoom neu
aufzubauen — Bild und Ton laufen weiter:

| Rad | Schrittweite |
|---|---|
| ohne Zusatztaste | 1 Hz |
| mit Umschalt | 10 Hz |
| mit Strg | 100 Hz |
| mit Umschalt und Strg | 1 kHz |

Rad nach oben erhöht die Frequenz. Die Kopfzeile zeigt den neuen Wert
sofort, auch wenn das Gerät ihn erst Sekundenbruchteile später bestätigt;
schnelles Radeln wird gebündelt, damit sich am Gerät keine Kommandos stauen.
Die Grenzen sind die des Empfängers, 10 kHz bis 3 GHz.

> **Hinweis:** Die Feinabstimmung ist gesperrt, solange eine Audio- oder
> Spektrum-Aufnahme läuft — die Mittenfrequenz steht in der Begleitdatei
> beziehungsweise in der Geometrie der Aufnahme und darf sich nicht ändern.
> In der Infozeile erscheint dann `Feinabstimmung gesperrt - Aufnahme
> laeuft`. Während eines Long-Run-Jobs ist sie ebenfalls gesperrt.

## 9.9 Mithören

Der Knopf **Mithören** im Block **Aufnahme** schaltet den demodulierten Ton
in den Browser. Er merkt sich seine Stellung: Ist Mithören eingeschaltet,
startet der Ton beim nächsten Zoom von selbst. Ab Werk ist es eingeschaltet.

Mithören und Audio-Aufnahme sind zwei verschiedene Dinge. Man kann hören,
ohne aufzunehmen, und aufnehmen, ohne zu hören.

Browser lassen Ton erst zu, nachdem der Benutzer etwas angeklickt hat. Beim
Zoom per Klick ist das erfüllt. Kommt der Zoom von außen — etwa aus dem
SDR-Wasserfall —, kann der Browser blockieren; der Knopf trägt dann den
Zusatz `(Klick zum Start)`, und ein Klick darauf gibt den Ton frei.

> **Achtung:** Ein offenes Mithören zählt serverseitig als laufender
> Empfangsbetrieb. Ein geplanter MSCAN-Lauf, der in diesem Moment starten
> soll, bricht deshalb sofort ab (Abschnitt 7.1 und Kapitel 10). Wer abends
> Läufe geplant hat, schließt den Zoom, statt nur den Browser zuzuklappen.

## 9.10 Audio und Spektrum aufzeichnen

Zwei Knöpfe, zwei Dateiarten, ein gemeinsames Bedienmuster: Der erste Klick
startet, der Knopf zeigt danach die verstrichene Zeit als MM:SS, der zweite
Klick beendet die Aufnahme.

| Knopf | Ergebnis | Ablage |
|---|---|---|
| **Audio** | WAV mit Begleitdatei, Name aus Zeit, Frequenz und Modulation | Ablage-Wurzel |
| **Spektrum** | `.specbin` mit Begleitdatei, die rohen IFPAN-Bins | Ablage-Wurzel |

Beides läuft auf getrennten Datenpfaden zum Gerät und stört sich nicht;
Audio und Spektrum können gleichzeitig laufen.

Steht die Modulation auf `IQ`, schreibt der Knopf **Audio** statt einer
Tonspur die komplexen Basisbandwerte — siehe Abschnitt 9.11.

Während einer **Spektrum-Aufnahme** sperrt die Oberfläche alles, was die
Geometrie des Fensters verändern würde: Span, Modulation, Bandbreite, den
Knopf **← Zurück zur Übersicht** sowie Klick und Ziehen im Bild. Das ist
Absicht — eine Aufnahme, deren Fenster sich mittendrin verschiebt, wäre
nicht auswertbar. Nach dem Stoppen erscheint in der Infozeile ein Link auf
die Spektrum-Auswertung auf :8040 mit der soeben geschlossenen Datei.

Die **Audio-Aufnahme** bleibt dabei bedienbar.

> **Achtung:** Umgekehrt gilt diese Sperre nicht. Läuft nur eine
> Audio-Aufnahme, sind Span, Modulation, Bandbreite, Klick und Ziehen
> weiterhin frei — und jede dieser Bedienungen baut den Zoom neu auf und
> schließt dabei die laufende WAV-Datei. Der Knopf zeigt die Aufnahme
> weiterhin als laufend an. Wer Audio aufzeichnet, lässt die Abstimmung in
> Ruhe oder beendet die Aufnahme vorher.

<!-- Befund A12: Zoom-Neuaufbau (Klick, Ziehen, Span, Modulation, ZF-BW)
     schliesst eine laufende WAV-Aufnahme ueber den finally-Pfad von
     _run_zoom_loop, ohne den Aufnahmeknopf im Browser zurueckzusetzen.
     Sobald das behoben ist, den Warnhinweis hier anpassen. -->

> **Hinweis:** Direkte Audio- und Spektrum-Aufnahmen folgen dem
> konfigurierten Ablageziel, Session-Aufnahmen aus der Übersicht dagegen
> nicht (Abschnitt 5.3 und Kapitel 13).

## 9.11 IQ aufzeichnen

Steht die Modulation auf `IQ`, schreibt der Knopf **Audio** keine Tonspur,
sondern die komplexen Basisbandwerte des Empfängers: eine zweikanalige
WAV-Datei mit 32 kHz Abtastrate, Kanal 0 ist I, Kanal 1 ist Q. Das ist die
Eingangsdatei für die Signalanalyse in Kapitel 11a.

Drei Dinge verhalten sich dabei anders als sonst:

- **Die Bandbreite ist auf 15 kHz begrenzt.** Der IQ-Strom läuft über denselben
  Audiokanal wie der Ton, dort im Geräte-Betriebsmodus mit 32 kHz komplexer
  Abtastung; eine breitere ZF würde Anteile hineinfalten. Wählen Sie mehr,
  setzt der Server beim Aufbau des Zooms auf 15 kHz zurück, und die
  Auswahlliste zieht nach. Die nutzbare Signalbandbreite liegt damit bei etwa
  15 kHz.
- **Mithören entfällt.** IQ-Rohwerte sind keine Niederfrequenz; der
  Mithörpfad im Browser ist fest einkanalig. Die Suite überspringt den Wunsch,
  statt Rauschen auszugeben.
- **Long-Run-Jobs mit IQ nehmen kein Audio auf.** Die Kombination aus
  IQ-Demodulation und **Audio parallel aufnehmen (WAV)** wird bei der
  Job-Anlage mit einer Begründung abgelehnt.

Vor einer IQ-Aufnahme, die als Konstellation ausgewertet werden soll, gehört
die Regelung auf `MGC` (Abschnitt 9.6).

Neben der WAV-Datei entsteht wie immer eine Begleitdatei. Sie trägt das Feld
`is_iq`, an dem das Auswertewerkzeug die Datei erkennt.

> **Achtung:** `is_iq` wird aus der Einstellung der Oberfläche abgeleitet und
> nicht vom Gerät zurückgelesen. Stand die Betriebsart beim Aufnehmen nicht
> wirklich auf IQ, behauptet die Begleitdatei es trotzdem. Wer sich nicht
> sicher ist, prüft die Aufnahme vor der Auswertung — eine echte IQ-Datei ist
> zweikanalig mit 32 kHz.

<!-- Befund A14: is_iq in der Sidecar stammt aus meta["mode"] der
     UI-Einstellung, nicht aus einem DEM?-Rueckabgleich. Sobald das
     rueckgelesen wird, den Warnhinweis hier streichen. -->

## 9.12 Long Run aus dem Zoom

Der Knopf **Long Run** öffnet dieselbe Job-Maske wie in der Übersicht, aber
mit dem Typ **IFPAN** und mit den Feldern **Mitte (MHz)**, **Span**,
**Demodulation** und **ZF-BW**. Vorbelegt werden sie aus dem laufenden Zoom:
die Mitte aus dem zuletzt empfangenen Frame, der Rest aus den Reglern des
Einstellungspanels.

Zwei Unterschiede zum D-SCAN-Job sind sichtbar:

- Es gibt **keine Dezimierung**. Ein IFPAN-Job schreibt jedes Frame; die
  Maske weist darauf hin.
- Die Zeile **Audio parallel aufnehmen (WAV)** kommt hinzu. Damit entstehen
  neben den Spektrum-Teildateien auch WAV-Dateien desselben Laufs.

Alles Weitere zu Jobs — Startzeit, Dauer, Rotation, Ablageauswahl mit
Budgetanzeige und die Übersicht der laufenden und geplanten Jobs — steht in
Kapitel 11.

## 9.13 Farbskala und Pause

**Skala min**, **Skala max** und **Auto** in der Leiste am unteren Rand
wirken hier genauso wie in der Übersicht, führen aber einen **eigenen
Zustand**: Wer den Zoom kontrastreicher einstellt, verstellt damit nicht die
Übersicht. Beide Zustände werden im Browser gespeichert und nach einem
Neuladen wiederhergestellt.

**Pause** dagegen ist ein gemeinsamer Zustand für beide Ansichten — wer im
Zoom pausiert, findet auch die Übersicht angehalten vor. Wie dort friert
Pause nur die Anzeige ein: Eine laufende Aufnahme schreibt weiter.
