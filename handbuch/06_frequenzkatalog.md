<!-- Status: Entwurf | Quellenstand: 2026-08-29 | offen: A6 -->

# 6. Der Frequenzkatalog

## 6.1 Wozu der Katalog da ist

Der Katalog beantwortet beim Blick auf den Wasserfall die Frage: *Was liegt
hier eigentlich?* Bänder, Segmente und einzelne Kanäle werden als
beschrifteter Streifen an der Frequenzachse eingeblendet — ohne dass Sie
nebenher in Listen blättern.

Er hat eine zweite Aufgabe: Er ist der Vorrat, aus dem MSCAN-Kanallisten
zusammengestellt werden. Was Sie dort auswählen, wandert später in die
Speicherplätze des Geräts.

> **Der Katalog ist kein Abbild des Gerätespeichers.** Er darf beliebig groß
> sein — allein das Flugfunkband bringt 2280 Kanäle mit. Das Gerät dagegen
> hat 1000 Speicherplätze (MEM0 bis MEM999), die aus einer Auswahl belegt
> werden. Der Katalog ist Ihr Wissensbestand, der Gerätespeicher eine daraus
> abgeleitete Arbeitsliste.

## 6.2 Wo er liegt und wie er geladen wird

Der Katalog besteht aus JSON-Dateien im Verzeichnis `known_freq/` — eine
Datei je Dienst oder Bandgruppe. Einen gepflegten Index gibt es bewusst
nicht: Die Suite liest beim Laden schlicht alle `*.json` des Verzeichnisses
in alphabetischer Reihenfolge und leitet daraus ab, welche Datei welchen
Frequenzbereich abdeckt.

**Laden ist nie fatal.** Eine defekte Datei, eine doppelt vergebene Kennung
oder eine unmögliche Bandbreite führen nicht zum Absturz: Der betroffene
Eintrag wird übersprungen, der Grund als Klartext gesammelt und in der
Pflegemaske angezeigt. Die Suite startet auch mit leerem oder beschädigtem
Katalog.

Änderungen wirken ohne Neustart. Die Anzeige-Server merken sich den geladenen
Katalog, prüfen aber bei jeder Anfrage Zeitstempel und Größe der Dateien und
laden bei Abweichung neu. Nach dem Speichern in der Pflegemaske genügt ein
Neuladen der Wasserfallseite.

## 6.3 Was in einer Katalogdatei steht

Jede Datei hat bis zu vier Blöcke:

```json
{
  "meta":     { "service": "Amateurfunk 2 m", "layer": "dienst" },
  "defaults": { "demod": "FM", "bw_hz": 15000, "tags": ["ham"] },
  "bands":    [ ],
  "entries":  [ ]
}
```

### meta — der Dateikopf

| Feld | Bedeutung |
|---|---|
| `service` | Anzeigename des Dienstes, freier Text |
| `layer` | `"zuweisung"` oder `"dienst"` — siehe Abschnitt 6.6. Fehlt der Schlüssel, gilt `"dienst"` |
| `source` | Herkunftsangabe, freier Text; wird nicht ausgewertet, ist aber für die Nachvollziehbarkeit dringend zu empfehlen |

### defaults — was sich Einträge sparen können

Alles, was in `defaults` steht, erben die Einträge der Datei, sofern sie es
nicht selbst setzen. Vererbt werden: `demod`, `bw_hz`, `squelch_dbuv`,
`antenna`, `att`, `att_auto`, `afc`, `tags`, `notes`.

Bei einer Datei mit lauter FM-Kanälen gleicher Bandbreite schrumpft ein
Eintrag damit auf Kennung, Name und Frequenz.

### bands — Bereichsbalken

Ein Band ist ein Bereich mit Titel, unabhängig von den Einzelkanälen:

```json
{
  "id": "ham-2m",
  "title": "2-m-Amateurfunk",
  "start_hz": 144000000,
  "stop_hz": 146000000,
  "segments": [
    { "title": "CW/SSB", "start_hz": 144000000, "stop_hz": 144400000 },
    { "title": "FM-Relais", "start_hz": 145600000, "stop_hz": 145800000 }
  ]
}
```

Segmente sind optional und müssen innerhalb ihres Bandes liegen.

### entries — Kanäle

Ein einzelner Kanal (`kind: "channel"`):

```json
{
  "kind": "channel",
  "id": "ham-2m-fm-anruf",
  "label": "FM-Anruf",
  "freq_hz": 145500000
}
```

Eine Kanalgruppe (`kind: "raster"`) erzeugt viele Kanäle auf einmal:

```json
{
  "kind": "raster",
  "id_prefix": "pmr446",
  "start_hz": 446006250,
  "stop_hz": 446193750,
  "step_hz": 12500,
  "label_template": "PMR {n}",
  "demod": "FM",
  "bw_hz": 9000
}
```

Im `label_template` stehen `{mhz}`, `{khz}`, `{hz}` und `{n}` (laufende
Nummer ab 0) zur Verfügung. Die erzeugten Kennungen lauten
`<id_prefix>-<label>`. Statt `step_hz` kann `"step_scheme": "airband_8k33"`
stehen — das erzeugt das 8,33-kHz-Kanalraster des Flugfunks nach ICAO-Regeln,
bei dem Kanalname und Frequenz bewusst auseinanderfallen.

Ein Eintrag mit `"enabled": false` erzeugt gar nichts. Das ist der übliche
Zustand vollständiger Raster: Sie liegen fertig in der Datei, bleiben aber
abgeschaltet, bis man sie braucht.

Zusätzlich kennt die Anzeige die optionalen Felder `site`, `programm`,
`bundesland`, `pol` und `erp_w` (Standort, Programm, Bundesland,
Polarisation, Strahlungsleistung). Sie sind für Rundfunksenderlisten gedacht
und erscheinen nur im Tooltip.

## 6.4 Was beim Laden geprüft wird

| Feld | Regel |
|---|---|
| `id` | nur `A–Z a–z 0–9 . _ -`, nicht leer, **im gesamten Katalog eindeutig** |
| `label` | nicht leer |
| `freq_hz` | ganze Zahl, 10 kHz bis 3 GHz (Empfangsbereich des EB200) |
| `demod` | `AM`, `FM`, `USB`, `LSB`, `CW`, `IQ` oder `PULSE` |
| `bw_hz` | eine der zwölf ZF-Filterstufen: 0,15 · 0,3 · 0,6 · 1,5 · 2,4 · 6 · 9 · 15 · 30 · 50 · 120 · 150 kHz |
| Segmente | müssen innerhalb ihres Bandes liegen |

Wird eine Kennung zweimal vergeben, gewinnt die alphabetisch frühere Datei;
der spätere Eintrag wird verworfen und gemeldet.

**Präfixe: eine Regel, die nur einen Fall betrifft.** Wird eine Kennung
allein aus der Frequenz gebildet — Muster `<präfix>-<kHz>`, ohne weiteres
unterscheidendes Segment —, dann gehört ihr Präfix genau einer Katalogdatei.
Zwei Listen über denselben Frequenzbereich erzeugen sonst zwangsläufig
dieselben Kennungen: die zweitgelesene Datei verliert jeden Eintrag auf einer
Frequenz, die schon in der ersten steht. Im September 2026 ist genau das
passiert — zwei Utility-Listen vergaben beide `udxf-<kHz>`, rund 300 Kanäle
wurden bei jedem Laden verworfen und gemeldet. Das eigene Präfix `udxflog-`
für die zweite Liste hat es behoben.

Kennungen, die neben dem Präfix noch Land, Band oder Standort führen, dürfen
sich ein Präfix dagegen über Dateigrenzen hinweg teilen: `fm-cz-impuls-kojal`
und `fm-wien-superfly` stehen sich nicht im Weg, ebenso wenig `ham-2m-ft8`
und `ham-70cm-fm-anruf`. Das zweite Segment unterscheidet sie.

**Wenn Sie eine fremde Liste umwandeln**, gilt die Regel für den Konverter:
ein eigenes Präfix je erzeugter Datei, sobald die Kennung aus der Frequenz
entsteht. Welche Präfixe bereits vergeben sind, zeigt

    python eb200_core/known_freq.py --praefixe

— die Ausgabe nennt sie je Datei und listet darunter die, die sich mehrere
Dateien teilen.

> **Hinweis:** Die Strenge hat einen Grund. Ein Katalogeintrag soll ohne
> Umrechnung in einen Speicherplatz des Geräts übertragbar sein. Eine
> Bandbreite, die das Gerät gar nicht einstellen kann, wäre dort unbrauchbar
> — deshalb wird sie schon im Katalog abgelehnt.

## 6.5 Tags und der Regionsfilter

Kanäle und Kanalgruppen können beliebige Schlagworte (`tags`) tragen, auch
geerbt aus `defaults`. Zwei Sorten wertet die Darstellung besonders aus:

- **`fnv`** kennzeichnet Einzelfrequenzen, die aus einem Frequenznutzungsplan
  stammen. Sie erscheinen als eigene Marken in der Zuweisungsreihe und nicht
  in der Kanalreihe — sonst stünden Verwaltungsangaben gleichwertig neben
  echten, beobachteten Sendern.
- **`land-*`** (etwa `land-w`, `land-n`) kennzeichnet die geografische
  Zuständigkeit eines Eintrags.

Welche `land-*`-Tags als eigene Region gelten, steht in `station.json` im
Repo-Verzeichnis:

```json
{ "region_tags": ["land-w", "land-n"] }
```

Ein Kanal mit einem `land-*`-Tag, das dort nicht steht, wird ausgeblendet.
Kanäle ganz ohne `land-*`-Tag — Flugfunk, Amateurfunk, Nachbarländer, eigene
Einträge — bleiben immer sichtbar. Fehlt die Datei, ist der Filter einfach
aus.

Der Live-Wasserfall hat dafür den Schalter **Regional** in der
Werkzeugleiste; er erscheint nur, wenn `region_tags` gefüllt ist. Die übrigen
Server filtern still nach derselben Datei.

> **Der Filter wirkt nur auf die Anzeige.** Katalog, Pflegemaske und
> MSCAN-Kanalauswahl sehen weiterhin den vollen Bestand. Ein ausgeblendeter
> Sender ist nicht gelöscht, nur nicht gezeichnet.

`station.json` ist eine lokale Datei und wird nicht mitversioniert; als
Vorlage dient `station.example.json`.

## 6.6 Die zwei Ebenen: Zuweisung und Dienst

Über demselben Frequenzbereich gibt es zwei berechtigte Sichten:

- **Zuweisung** (`"layer": "zuweisung"`) — was das Regelwerk erlaubt. Quelle
  ist typischerweise der nationale Frequenznutzungsplan. Diese Sicht ist
  vollständig und lückenlos, sagt aber nichts darüber aus, ob dort
  tatsächlich jemand sendet.
- **Dienst** (`"layer": "dienst"`, die Vorgabe) — was bekanntermaßen dort
  läuft: Senderlisten, Bandpläne, eigene Beobachtungen.

Beide werden in getrennten Reihen gezeichnet, und Doppelungen sind erwünscht,
wo die zweite Sicht etwas hinzufügt. Das 70-cm-Band etwa steht einmal als
Zuweisungsband und einmal als Amateurfunk-Bandplan mit Segmenten für
Relaisablage und Bakenbereich — die Zuweisung sagt, wer darf, der Bandplan
sagt, wo was zu erwarten ist.

Eine reine Wiederholung ohne Mehrwert gehört dagegen nicht in den Katalog:
Ein Band ohne eigene Segmente, das nur einen Zuweisungsbereich nachzeichnet,
verdoppelt bloß den Strich.

## 6.7 Der Katalog-Streifen in der Darstellung

Der Streifen liegt an der Frequenzachse und hat drei Reihen:

1. **Zuweisungsebene** — Bänder dieser Ebene, bewusst zurückgenommen
   gezeichnet, mit stets sichtbarem Titel (zu lange Titel werden gekürzt,
   nicht weggelassen). Darunter schmale Marken für Segmente und für
   Einzelfrequenzen mit dem Tag `fnv`.
2. **Bänder** — alle übrigen Bänder. Überlappende Bänder verteilen sich auf
   zwei Spuren, damit sie sich nicht gegenseitig übermalen.
3. **Kanäle** — die Einzelkanäle.

![Abb. 6.1: Katalog-Streifen mit den drei Reihen](bilder/06_katalogstreifen.png)

Wie viel gezeichnet wird, hängt davon ab, wie viel Platz ein Kanal im Bild
bekommt: Bei sehr breiten Bereichen bleibt es bei Balken, dann kommen Marken
hinzu, schließlich Beschriftungen. Als Faustwert erscheinen Marken ab etwa
4 MHz Fensterbreite, Beschriftungen ab etwa 2 MHz.

Liegen mehrere Kanäle auf derselben Frequenz — im UKW-Band kommt das
regelmäßig vor —, werden sie zu einem Punkt gebündelt; die Marke ist dann
dicker und bernsteinfarben, die Beschriftung führt beide Namen mit „·".

**Der Tooltip.** Wer mit der Maus über den Streifen fährt, bekommt ein helles
Feld mit einer Zeile je Kanal: Programm, Standort, Bundesland, Polarisation
und Strahlungsleistung, soweit im Katalog vorhanden, sonst Name und Dienst.
Darunter stehen die berührten Bänder, die Zuweisungsebene zuerst. Blendet der
Regionsfilter weitere Kanäle derselben Frequenz aus, erscheint ein Hinweis
„+n außerhalb der Region".

Ein **Klick** heftet den Tooltip fest — dann lassen sich die Werte markieren
und kopieren. Gelöst wird er mit [Esc] oder einem Klick daneben.

Den Streifen gibt es im Live-Wasserfall (Port 8000, dort mit dem Schalter
**Katalog** ein- und ausklappbar), im SDR-Wasserfall (8030), in der
Session-Auswertung (8020) und in der Spektrum-Auswertung (8040).

## 6.8 Den Katalog pflegen

Die Pflegemaske läuft auf `http://localhost:8010/`. Sie braucht kein Gerät —
nur Zugriff auf das Katalogverzeichnis.

Die Startseite zeigt jede Katalogdatei als Kachel mit Dienstnamen und
Kennzahlen; fehlerhafte Dateien sind rot markiert. Ein Klick öffnet das
Bearbeitungsblatt mit sechs Reitern:

| Reiter | Zweck |
|---|---|
| **Bearbeiten** | Liste der Bänder und Einträge der Datei, je mit Bearbeiten und Löschen |
| **Datei-Kopf** | `meta` und `defaults` der Datei |
| **JSON** | die Datei als Text — für größere Umbauten |
| **Neuer Kanal** | Formular für einen Einzelkanal |
| **Neue Kanalgruppe** | Formular für ein Raster |
| **Neues Band** | Formular für ein Band samt Segmenten |

Eine neue Katalogdatei entsteht nicht hier, sondern auf der Kachelübersicht
über den Knopf **+ Neue Katalogdatei**.

![Abb. 6.2: Bearbeitungsblatt einer Katalogdatei](bilder/06_block_admin.png)

Zwei Eigenheiten der Formulare:

- **Frequenzen werden in kHz eingegeben**, mit deutscher Zahlenschreibweise
  (Punkt als Tausender-, Komma als Dezimaltrennzeichen, bis auf 1 Hz genau).
  Gespeichert wird intern in Hz; die Umrechnung passiert beim Verlassen des
  Feldes.
- **Bandbreite und Antenne sind Auswahllisten**, keine Textfelder. Die
  Bandbreite bietet genau die zwölf Filterstufen des Geräts. Die Antenne wird
  symbolisch benannt (`kw_dipol`, `kw_beam`, `yagi_6m`, `discone`) und erst
  beim Übertragen in den Gerätespeicher einem Eingang zugeordnet.

**Wie gespeichert wird.** Vor jedem Schreiben lädt die Maske den gesamten
Katalog in einer temporären Kopie noch einmal — mit der geänderten Datei.
Entstehen dabei neue Fehler, wird nicht gespeichert, sondern der Grund
angezeigt. Erst danach wird geschrieben: eine Sicherung `.bak` der
Vorversion, dann die neue Fassung über eine Zwischendatei, die in einem Zug
an ihren Platz rückt. Ein Abbruch mitten im Schreiben kann so keine halbe
Datei hinterlassen.

> **Achtung:** Die Sicherung `.bak` hält genau eine Generation. Wer dieselbe
> Datei zweimal hintereinander speichert, überschreibt sie. Für größere
> Umbauten empfiehlt sich eine eigene Kopie oder eine Versionsverwaltung.

## 6.9 Eigene Einträge

Legen Sie Ihre eigenen Beobachtungen in einer **eigenen Datei** ab, nicht in
einer importierten Dienstliste. Sonst geht Ihre Arbeit verloren, sobald Sie
die Liste durch eine neuere Fassung ersetzen.

**Kennungen sind dauerhaft.** MSCAN-Definitionen verweisen über die `id` auf
Katalogeinträge. Eine einmal vergebene Kennung wird deshalb nicht umbenannt —
auch dann nicht, wenn ein Eintrag in eine andere Datei wandert oder sein Name
sich ändert. Wandert ein Eintrag um, dokumentiert das Feld `legacy_id_from`
die Herkunft. Wer eine Kennung ändert, findet die betroffenen Kanäle in
seinen MSCAN-Jobs als „fehlend" wieder.

## 6.10 Werkzeuge

Zwei Kommandozeilenwerkzeuge liegen bei. Für den normalen Betrieb braucht man
sie nicht.

- `tools/known_freq_abgleich.py` gleicht die Bänder aller Dateien gegen eine
  Zuweisungs-Referenzdatei ab und schlägt vor, was gelöscht, verschoben oder
  geprüft werden sollte. Ohne Argument läuft es trocken und schreibt nur
  einen Bericht; erst `--apply` ändert etwas. Kanaleinträge fasst es nie an.
- `tools/sync_known_block.py` hält den Anzeigecode des Streifens in den vier
  beteiligten Servern wortgleich. Das ist nur relevant, wenn Sie am Quelltext
  arbeiten.

## 6.11 Der mitgelieferte Bestand

Im Auslieferungszustand enthält `known_freq/` rund 4200 Kanäle und 170 Bänder
in achtzehn Dateien:

| Gruppe | Inhalt |
|---|---|
| Zuweisung | nationaler Frequenznutzungsplan Österreich, 30 MHz bis 3 GHz |
| Rundfunk | UKW und DVB-T2 Österreich, UKW Wien, Nachbarländer CZ/SK/HU, DAB+ Band III |
| Flugfunk | VHF-Sprechfunk im 8,33-kHz-Raster, Navigationsband als Bereich |
| Amateurfunk | IARU-Region-1-Bandpläne, 2 m und 70 cm im Detail |
| Weitere | ISM/SRD, Satelliten, Zeitzeichen, Not- und Sicherheitsfunk, 30 bis 88 MHz |

> **Für Betreiber außerhalb Österreichs:** Die Zuweisungs- und
> Rundfunkdateien sind länderspezifisch. Sie können sie gefahrlos löschen —
> der Katalog lädt danach einfach mit weniger Einträgen. Die international
> gültigen Dateien (Flugfunk, Amateurfunk, ISM, Satelliten, Zeitzeichen, Not-
> und Sicherheitsfunk) bleiben brauchbar. Für eine eigene nationale
> Zuweisungsdatei ist der Aufbau aus Abschnitt 6.3 die Vorlage;
> `"layer": "zuweisung"` sorgt dafür, dass sie in der richtigen Reihe landet.
