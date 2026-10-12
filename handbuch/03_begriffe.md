<!-- Status: Entwurf | Quellenstand: 2026-08-29 | offen: - -->

# 3. Begriffe der Suite

Dieses Kapitel erklärt das Vokabular, das im ganzen Handbuch benutzt wird. Es
lohnt sich, es einmal zu lesen — die Begriffe **Session**, **Spektrum**,
**Audio**, **Job** und **Lauf** bedeuten in der Suite etwas sehr Bestimmtes.

## 3.1 Die Betriebsarten des Empfängers

### D-SCAN

Ein schneller Suchlauf über einen breiten Frequenzbereich. Das Gerät tastet
den Bereich ab und liefert für jeden Durchlauf einen kompletten Satz
Pegelwerte — einen **Sweep**. Im Wasserfall ist ein Sweep eine Bildzeile.

Die Auflösung hängt an der ZF-Bandbreite: Etwa alle halbe Bandbreite entsteht
ein Messpunkt. Damit ist die Bandbreite zugleich der Tempo- und
Auflösungsregler — größere Bandbreite bedeutet größere Schritte, weniger
Punkte je Sweep und damit einen schnelleren Durchlauf.

D-SCAN setzt die Geräteoption **EB200DS** (DIGI-Scan) voraus.

### BW-Zoom

Eine Betriebsvariante des D-SCAN. Ist BW-Zoom eingeschaltet, koppelt das
Gerät den dargestellten Bereich an die eingestellte ZF-Bandbreite und legt
ihn um eine Markerfrequenz — statt eines breiten Bereichs sehen Sie einen
schmalen Ausschnitt in feiner Auflösung. Die Suite setzt den Marker auf die
Mitte des eingestellten Bereichs.

BW-Zoom ist gedacht, um im laufenden D-SCAN kurz genauer hinzusehen, ohne die
Betriebsart zu wechseln. Beim Ausschalten stellt das Gerät den vorherigen
breiten Bereich wieder her.

### IFPAN

Das ZF-Panorama: eine feine Spektraldarstellung rund um **eine**
Empfangsfrequenz, zusammen mit Demodulation und Audio. Hier wird zugehört,
hier werden Signale genau vermessen, hier entstehen Audio- und
Spektrum-Aufnahmen.

IFPAN setzt die Geräteoption **EB200SU** (IF Panoramic Unit) voraus.

### M-SCAN

Der Speicherplatz-Suchlauf: Das Gerät arbeitet eine Liste gespeicherter
Kanäle ab und meldet sich nur dann, wenn auf einem Kanal der Squelch öffnet —
also tatsächlich etwas zu hören ist. Das ist die Grundlage für
unbeaufsichtigte Kanalüberwachung über Stunden.

> **Hinweis:** Es gibt einen Empfänger und damit zu jedem Zeitpunkt genau
> eine Betriebsart. Ein Wechsel in den IFPAN-Zoom hält den D-SCAN an; ein
> startender MSCAN-Lauf beansprucht das Gerät allein.

## 3.2 Die drei Aufnahmearten

Was aufgezeichnet wird, hängt an der Betriebsart. Daraus ergeben sich drei
Arten von Aufnahmen, die im ganzen Handbuch so heißen:

### Session

Eine **Session** entsteht aus dem D-SCAN. Aufeinanderfolgende Sweeps werden
fortlaufend in eine Datenbank geschrieben, gegliedert in Session → Segment →
Sweep. Ein Segment ist dabei ein Abschnitt mit unveränderten Einstellungen;
ändert sich Bereich, Schrittweite, Speed oder Messzeit, beginnt ein neues.

Eine Session beantwortet die Frage: *Was war heute Nacht in diesem Band los?*
Ausgewertet wird sie auf Port 8020.

### Spektrum-Aufnahme

Eine **Spektrum-Aufnahme** entsteht aus dem IFPAN. Die Bin-Werte des
Panoramas werden als Datei `<name>.specbin` mitgeschrieben, begleitet von
einer Beschreibungsdatei `<name>.specbin.json`.

Sie beantwortet die Frage: *Wie hat sich dieses eine Signal im Detail
verhalten?* Ausgewertet wird sie auf Port 8040 — dort lässt sie sich vor- und
zurückspulen wie ein Videoband.

### Audio-Aufnahme

Eine **Audio-Aufnahme** ist eine gewöhnliche WAV-Datei mit 16 kHz, 16 Bit,
mono, dazu eine Beschreibungsdatei `<name>.wav.json`. Sie entsteht auf zwei
Wegen: von Hand aus dem IFPAN-Zoom heraus, oder automatisch während eines
MSCAN-Laufs — dort für jede Squelch-Öffnung ein eigener Abschnitt.

Ausgewertet wird sie auf Port 8070: anhören, als Spektrogramm ansehen,
Tonabstände messen.

| Art | Betriebsart | Dateien | Auswertung |
|---|---|---|---|
| Session | D-SCAN | Session-Datenbank | Port 8020 |
| Spektrum | IFPAN | `.specbin` + `.specbin.json` | Port 8040 |
| Audio | IFPAN oder M-SCAN | `.wav` + `.wav.json` | Port 8070 |

## 3.3 Direkte Aufnahme und Long-Run-Job

Aufgezeichnet wird auf zwei grundsätzlich verschiedene Weisen.

**Die direkte Aufnahme** starten Sie mit einem Knopf in der laufenden
Ansicht. Sie beginnt sofort, verwendet die Einstellungen, die gerade aktiv
sind, und läuft, bis Sie sie beenden. Gedacht ist sie für Minuten bis wenige
Stunden. Die Aufnahme läuft serverseitig weiter, auch wenn der Browser
geschlossen wird — gestoppt wird sie aber dort, wo sie gestartet wurde.

**Der Long-Run-Job** wird vorher definiert: Bereich, Parameter,
Startzeitpunkt, Dauer. Die Parameter werden bei der Anlage **eingefroren** —
spätere Änderungen an den Live-Einstellungen wirken sich auf einen schon
angelegten Job nicht mehr aus. Zur eingetragenen Zeit startet der Job von
selbst und schreibt in ein eigenes Job-Verzeichnis: eine Beschreibungsdatei
`job.json`, die Messdateien und einen regelmäßigen Lebenszeichen-Eintrag.
Gedacht ist er für 12 bis 24 Stunden.

> **Betriebsregel:** Ein geplanter Job startet nur, wenn der Server auf Port
> 8000 zur Startzeit läuft. Wer Jobs plant, lässt die Suite laufen. Ein Job,
> dessen Startzeit beim Serverstart schon vorbei ist, wird nicht
> stillschweigend nachgeholt, sondern als **verpasst** gekennzeichnet und zum
> Nachholen angeboten.

| | Direkte Aufnahme | Long-Run-Job |
|---|---|---|
| Start | sofort, per Knopf | geplant, zur festgelegten Zeit |
| Parameter | die gerade eingestellten | bei der Anlage eingefroren |
| Typische Dauer | Minuten bis Stunden | 12 bis 24 Stunden |
| Ablage | Aufnahmeverzeichnis | eigenes Job-Verzeichnis |
| Browser | zum Starten und Stoppen nötig | nur zum Anlegen und Nachsehen |

## 3.4 Job, Lauf und Aufzeichnung

Diese drei Ebenen werden gern verwechselt, besonders bei M-SCAN:

- Ein **Job** ist eine Definition: welche Kanäle, welche Parameter, welcher
  Zeitplan. Ein Job ist änderbar und löschbar.
- Ein **Lauf** ist eine Ausführung dieses Jobs. Er ist eine Momentaufnahme:
  Was zur Startzeit in der Definition stand, gilt für diesen Lauf — auch wenn
  die Definition später geändert wird. Wird ein Job gelöscht, bleiben seine
  Läufe und deren Aufnahmen erhalten.
- Eine **Aufzeichnung** ist eine einzelne entstandene Datei: ein
  WAV-Abschnitt, eine Spektrum-Aufnahme, eine Session-Datenbank.

Ein Job kann also viele Läufe haben, ein Lauf viele Aufzeichnungen.

## 3.5 Sidecars, Befunde und Kurztexte

Grundregel der Suite: **eine Messdatei wird nach dem Schreiben nie wieder
verändert.** Alles, was später dazukommt, landet in eigenen Dateien daneben —
sogenannten Sidecars:

- **Beschreibungs-Sidecar** (`<datei>.json`): Frequenz, Betriebsart,
  Bandbreite, Startzeitpunkt, bei laufender Aufnahme ein Merkmal „läuft
  noch". Entsteht zusammen mit der Aufnahme.
- **Befund** (`<name>.befund.json`): das Ergebnis einer Auswertung. Ein
  Befund ist jederzeit neu berechenbar und darf gefahrlos gelöscht werden.
- **Kurztext und Notiz**: Ihre eigenen Anmerkungen zu einer Aufnahme oder
  einem Job. Der Kurztext ist die eine Zeile, die in der Aufzeichnungsliste
  erscheint; die Notiz ist der längere Text in der Auswertung.

> **Achtung:** Kurztexte, Notizen und Handlabels sind die Ausnahme von der
> Sidecar-Regel. Sie liegen zentral im Repo-Verzeichnis und sind der Aufnahme
> über ihren relativen Pfad zugeordnet. Wird eine Aufnahme außerhalb der
> Suite verschoben oder umbenannt, verliert sie ihre Anmerkungen
> stillschweigend.

Auch die Spektrogramme der Audio-Auswertung werden zwischengespeichert
(`<name>.wav.audspec`). Diese Zwischenspeicher sind reine Beschleunigung —
löschen Sie sie, werden sie beim nächsten Öffnen neu berechnet.

## 3.6 Frequenzkatalog und Katalog-Streifen

Der **Frequenzkatalog** ist die Sammlung dessen, was Sie über die Bänder
wissen: Bandgrenzen, benannte Kanäle, Dienste, Regionen. Er liegt als Satz
JSON-Dateien im Verzeichnis `known_freq/` und wird auf Port 8010 gepflegt.

Sichtbar wird er als **Katalog-Streifen**: farbige Balken mit Beschriftung an
der Frequenzachse der Wasserfälle und Auswertungen. Ein unbekannter Träger im
Wasserfall bekommt so sofort einen Kontext. Derselbe Katalog ist außerdem die
Quelle, aus der MSCAN-Kanallisten zusammengestellt werden. Einzelheiten in
Kapitel 6.

## 3.7 Ablage und `storage_roots`

Wohin geschrieben wird, steht in der Datei `storage_roots.json` im
Repo-Verzeichnis: eine Liste von Verzeichnissen. Der erste Eintrag ist das
Standardziel für Aufnahmen; fehlt die Datei, wird das Unterverzeichnis
`records` im Repo benutzt.

Diese Liste hat eine zweite Aufgabe: Sie ist zugleich die Erlaubnisliste der
Auswerte-Server. Ein Pfad, der nicht unterhalb des Repos oder eines dieser
Verzeichnisse liegt, wird gar nicht erst geöffnet — siehe Kapitel 17.
