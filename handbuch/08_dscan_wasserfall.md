<!-- Status: Entwurf | Quellenstand: 2026-08-29 | offen: B1, A3b, D5, D6, D7 -->

# 8. Live-Wasserfall mit D-SCAN

## 8.1 Aufbau der Ansicht

Von oben nach unten:

- **Kopfzeile** mit der Mittenfrequenz, dem Link zur Statusseite und einer
  Anzeige, wenn eine Aufnahme läuft.
- **Werkzeugleiste** — Aktionen auf die Darstellung, dazu das
  Ansichten-Menü.
- **Einstellungspanel** links, einklappbar, mit vier Blöcken: Scan-Bereich,
  Bandbreite und Speed, Zusatz-Zoom mit SDR, Aufnahme.
- **Mitte:** Sweep-Info, Einzelspektrum, Frequenzachse, Katalog-Streifen und
  darunter der rollende Wasserfall.
- **Links davon** das Zeit-Gutter mit 30-Sekunden-Marken, **rechts** die
  Farbskala mit Beschriftung in dBµV.
- **Unten** die einklappbare Skalenleiste.

## 8.2 Den Scan-Bereich einstellen

Start und Stop in MHz eintragen, dann **Bereich übernehmen**. Das Panel
klappt danach von selbst zu, damit der Wasserfall die volle Breite bekommt.

Die kleinste zulässige Spanne beträgt 20 kHz. Darunter lehnt der Server ab,
bevor er das Gerät überhaupt fragt, und weist auf den IFPAN-Zoom hin. Der
Sweep läuft bei einer Ablehnung unverändert weiter — es ändert sich nur der
Hinweistext.

## 8.3 Bandbreite und Speed

Die **Bandbreite** ist eine Auswahl der zwölf Filterstufen des Geräts und
wirkt hier als Kanalabstand: größer bedeutet gröber und schneller.

**Speed** kennt LOW, NORMAL, HIGH und MTIME. Nur bei MTIME erscheint das Feld
für die Messzeit je Kanal. Die Schätzung darunter rechnet Kanalzahl mal
Messzeit plus Abstimmzeit und sagt Ihnen vorab, wie lange ein Sweep dauern
wird.

> **Hinweis:** Die Messzeit ist am Gerät ein globaler Parameter. Die Suite
> setzt sie bei jedem anderen Speed aktiv auf den Vorgabewert zurück, damit
> sie nicht in andere Messfunktionen hineinwirkt.

**BW-ZOOM** ist ein eigener Knopf neben der Speed-Auswahl, kein Eintrag in
einer Liste. Er koppelt den dargestellten Span an die Bandbreite und legt ihn
um die Mitte des eingestellten Bereichs. Beim Ausschalten kehrt das Gerät zum
vollen Bereich zurück. Die Beschriftung nennt die nächste Aktion, nicht den
Zustand: **BW-ZOOM einschalten** heißt also, dass der Zoom derzeit aus ist.
Den aktuellen Zustand nennt die Parameter-Tafel über dem Wasserfall
(`BW-Zoom ein` bzw. `BW-Zoom aus`).

## 8.4 Die Wasserfall-Optionen

| Schalter | Wirkung |
|---|---|
| **Zum Anfang** | Ansicht wieder an den Live-Rand heften |
| **Pause / Weiter** | friert die Anzeige ein |
| **Als JPG speichern** | brennt Kopfzeile, Historie, Zeitmarken und Achse in ein Bild |
| **Löschen** | leert die Historie und beginnt mit aktuellem Parameter-Kopf neu |
| **Raster** | Frequenzraster ein und aus |
| **Klick-Marker** | von Hand gesetzte Marken |
| **Auto-Marker** | Detektion durch den Server, siehe unten |
| **Markerliste** | Ereignisliste der Auto-Marker ein- und ausblenden |
| **Katalog** | Katalog-Streifen ein- und ausklappen |
| **Regional** | nur Sender der eigenen Region zeigen |

> **Achtung:** **Pause** hält nur die Anzeige an. Eine laufende Session- oder
> Direktaufnahme schreibt unverändert weiter. Wer eine Aufnahme beenden will,
> benutzt den Aufnahmeknopf im Einstellungspanel.

> **Hinweis:** **Als JPG speichern** ist ein Fotoapparat, kein Datenexport.
> Ausgegeben wird genau das, was gerade im Bildpuffer steht, mit
> eingebrannter Kopfzeile, Zeitmarken und Frequenzachse. Wer Zahlen braucht,
> nimmt die Markerliste als CSV oder gleich eine Session-Aufnahme.

### Klick-Marker und Auto-Marker

Zwei verschiedene Dinge mit ähnlichem Namen.

**Klick-Marker** setzen Sie selbst mit einem Klick in den Wasserfall. Sie
dienen dazu, sich eine Stelle zu merken, während man arbeitet.

**Auto-Marker** setzt der Server: Er schätzt fortlaufend den Rauschflur und
markiert eine Frequenz, sobald der Pegel diesen Flur um einen Betrag Δ
übersteigt. Je Frequenz geschieht das einmal, nicht bei jedem Sweep erneut.
Über das kleine Dreieck neben dem Schalter erreichen Sie die Einstellungen:

- **Δ-Schwelle**, 3 bis 30 dB. Mit der Option **automatisch** bestimmt der
  Server sie selbst aus der Rauschstreuung der ersten Sweeps; sobald Sie den
  Regler bewegen, gilt Ihr Wert.
- **Mindestdauer** in Sweeps. Ein Signal muss so viele Sweeps hintereinander
  über der Schwelle liegen, bevor es als Marke zählt. Das unterdrückt
  einzelne Störspitzen. Daneben steht, wie viele Sekunden das beim aktuellen
  Sweep-Tempo sind.
- **Marker mit Frequenz** blendet die Frequenz direkt an der Marke ein.

Die **Markerliste** zeigt die Ereignisse mit Zeit, Frequenz in MHz und Pegel
in dBµV, das Neueste oben. Von dort lässt sich die Tabelle in die
Zwischenablage kopieren oder als CSV mit Semikolon herunterladen.
**Restart** verwirft die bisherigen Marken und beginnt die Schätzung neu —
sinnvoll nach jedem Bereichswechsel.

### Farbskala

Die Leiste unten hat **Skala min**, **Skala max** und **Auto**. Übersicht und
Zoom haben getrennte Zustände; beide werden im Browser gespeichert und nach
einem Neuladen wiederhergestellt.

## 8.5 Historie und Fenstergröße

Der Wasserfall ist ein Bildpuffer mit eigenem Scrollbalken: Sie können weit
zurückblättern, während oben weitergeschrieben wird. **Zum Anfang** bringt
Sie wieder an den Live-Rand.

Breite und Höhe dieses Puffers werden **einmal beim Laden** der Seite aus der
Fenstergröße bestimmt.

> **Achtung:** Wer das Browserfenster wesentlich verändert, bekommt einen
> Hinweis mit dem Knopf **Neu laden**. Das Neuladen passt die Darstellung an
> — verwirft aber die Historie, weil sie ausschließlich im Browser liegt. Wer
> die Historie behalten will, speichert sie vorher als JPG oder zeichnet
> gleich eine Session auf.

## 8.6 Session-Aufnahme

Im Block **Aufnahme** des Einstellungspanels startet und stoppt ein Knopf die
Session-Aufzeichnung. Sie läuft serverseitig, überlebt also das Schließen des
Browsers; die Kopfzeile zeigt währenddessen eine Aufnahmeanzeige.

Segmentgrenzen entstehen automatisch, wenn sich Bereich, Schrittweite, Speed
oder Messzeit ändern. Das ist wichtig für die spätere Auswertung: Innerhalb
eines Segments sind alle Sweeps vergleichbar, über Segmentgrenzen hinweg
nicht.

Nach dem Stoppen blendet die Seite einen Link auf die fertige Session in der
Session-Auswertung ein.

Session-Aufnahmen landen immer im Verzeichnis `sessions/` des Repos, auch
wenn `storage_roots.json` auf ein anderes Laufwerk zeigt — siehe
Abschnitt 5.3.

<!-- Befund B1: Ungeklaert ist, was mit einer laufenden Session-Aufnahme
     beim Wechsel in den IFPAN-Zoom passiert. Der Recorder bleibt offen, es
     kommen nur keine Sweeps mehr. Vor der Veroeffentlichung klaeren und
     hier beschreiben. -->

## 8.7 Long Run aus der Übersicht

Der Knopf **Long Run** öffnet die Maske für einen geplanten Job mit dem
gerade eingestellten Bereich. Einzelheiten dazu in Kapitel 11.

## 8.8 Der SDR-Wasserfall als Zwischenstufe

Im Block **Zusatz-Zoom mit SDR** lädt der Schalter **Wähle SDR** die Liste
der erkannten Geräte. Ist ein Gerät gewählt, markiert Ziehen in der Übersicht
einen Frequenzbereich und öffnet den SDR-Wasserfall in einem eigenen Fenster.
Ein Klick dort schickt die Frequenz an den IFPAN-Zoom zurück und holt das
Hauptfenster nach vorn.

Ohne aktiven SDR-Schalter hat Ziehen keine Wirkung; ein einfacher Klick zoomt
wie gewohnt.

Der SDR-Wasserfall zeigt seinen Ausschnitt lückenlos — anders als der D-SCAN,
der abtastet. Er sagt aber nichts über den EB200 aus: Es sind zwei getrennte
Empfänger an möglicherweise verschiedenen Antennen. Für die Beurteilung von
Pegeln ist der EB200 maßgeblich.

## 8.9 Die Statusseite

Der Link **EB200 Status** in der Kopfzeile öffnet das Parameter-Memory: die
Geräte-Inventur, die beim Start aufgenommen wurde (also der Zustand **vor**
dem ersten eigenen Kommando der Suite), und daneben die laufenden Parameter.

Die Seite ist reine Anzeige. Sie ist nützlich, um zu sehen, was die Suite am
Gerät verändert hat — und um nach einem Frontpanel-Eingriff zu prüfen, was
gerade wirklich eingestellt ist.
