# handbuch/ — Arbeitsdokument

Diese Datei richtet sich an die, die am Handbuch arbeiten. Für Leser des
Handbuchs ist `handbuch.md` der Einstieg.

## Zielgruppe und Format

Zielgruppe sind **alle Besitzer eines EB200**, nicht die Betreiber dieser
einen Station. Geräteoptionen werden deshalb als Voraussetzung behandelt und
nicht als gegeben; länderspezifische Katalogdateien werden als austauschbar
gekennzeichnet.

Quellformat ist Markdown, ein Kapitel je Datei. Veröffentlichte Fassungen
entstehen später über Typst. Deshalb:

- **Genau eine H1 je Datei**, sie ist der Kapiteltitel.
- Gliederung darunter mit H2 und H3, nicht tiefer.
- Reihenfolge ergibt sich aus dem Dateinamen. Beim Typst-Lauf werden die
  Dateien in alphabetischer Reihenfolge zusammengesetzt; `arbeit/` bleibt
  außen vor.
- Nummern werden **nicht** neu vergeben. Kommt ein Kapitel dazu, wird
  angehängt oder ein Buchstabe benutzt (`09a_…`), damit Querverweise halten.
- Bilder liegen in `bilder/`, benannt nach dem Kapitel, in dem sie zuerst
  vorkommen.

## Statuszeile

Jede Kapiteldatei beginnt mit einem Kommentar:

```
<!-- Status: Entwurf | Quellenstand: 2026-08-29 | offen: B1, C2 -->
```

- **Status:** `geplant` (nur Überschrift), `Stichworte` (Fakten erhoben, nicht
  ausformuliert), `Entwurf` (ausformuliert, ungeprüft), `geprüft` (gegen
  aktuelle Quellen und am Gerät nachvollzogen).
- **Quellenstand:** gegen welchen Stand der Quellen geschrieben wurde.
- **offen:** Kennungen aus `arbeit/befunde.md`, die dieses Kapitel betreffen.

## Stand der Kapitel

| Datei | Kapitel | Status | offen |
|---|---|---|---|
| `01_ueber_dieses_handbuch.md` | Über dieses Handbuch | Entwurf | — |
| `02_was_ist_die_suite.md` | Was die Suite ist | Entwurf | — |
| `03_begriffe.md` | Begriffe | Entwurf | — |
| `04_voraussetzungen.md` | Voraussetzungen | Entwurf | A1, B2, B4 |
| `05_installation.md` | Installation | Entwurf | A2, A3a, D9 |
| `06_frequenzkatalog.md` | Frequenzkatalog | Entwurf | A6 |
| `07_monitoring_modi.md` | Monitoring-Modi | Entwurf | A5, D4, D8 |
| `08_dscan_wasserfall.md` | D-SCAN-Wasserfall | Entwurf | B1, A3b, D5–D7 |
| `09_ifpan.md` | IFPAN | Entwurf | A5, A9, A12, A13, A14, B1 |
| `10_mscan.md` | MSCAN | Stichworte | D8 |
| `11_aufzeichnungen.md` | Aufzeichnungen | Stichworte | A11 |
| `11a_signalanalyse_iq.md` | Signalanalyse von IQ-Aufnahmen | Entwurf | A1, A14 |
| `12_arbeitsablaeufe.md` | Arbeitsabläufe | geplant | — |
| `13_daten_und_dateien.md` | Daten und Dateien | geplant | A2, A4, A14 |
| `14_http_schnittstellen.md` | HTTP-Schnittstellen | geplant | — |
| `15_geraetewissen.md` | Gerätewissen | geplant | — |
| `16_fehlersuche.md` | Fehlersuche | geplant | B2, B3, B4 |
| `17_sicherheit.md` | Sicherheit | geplant | — |
| `18_glossar.md` | Glossar | geplant | — |
| `A1_tastenkuerzel.md` bis `A4_…` | Anhänge | geplant | — |

## Bilder

| Datei | Zustand |
|---|---|
| `bilder/07_ansichten.svg` | fertig |
| `bilder/13_ablage.svg` | fertig |
| `bilder/05_statusboard.png` | fehlt |
| `bilder/06_katalogstreifen.png` | fehlt |
| `bilder/06_block_admin.png` | fehlt |

> **Screenshots zurückstellen.** Solange die Umstellung auf das gemeinsame
> Theme (`eb200_core/ui_theme.py`) und auf Inline-SVG-Bedienglyphen nicht
> abgeschlossen ist, wären Bildschirmfotos Wegwerfarbeit. Siehe Befunde A8
> und A9.

## Arbeitsdateien

`arbeit/` gehört ins Repo, aber nicht ins ausgelieferte Handbuch:

- `befunde.md` — Unklarheiten, Widersprüche und Änderungsbedarf, die beim
  Schreiben aufgefallen sind. Erledigte Punkte werden mit Datum als erledigt
  markiert, nicht gelöscht.
- `fakten.md` — was aus den Quellen erhoben wurde, jeweils mit Fundstelle
  (Datei plus Funktions- oder Konstantenname, keine Zeilennummern). Grundlage
  für die Prüfung gegen neue Quellenstände.
- `terminologie.md` — verbindliche Schreibweisen und die Bedienelemente
  wortgetreu so, wie sie in der Oberfläche stehen. Zugleich Grundlage für
  eine spätere englische Fassung.

## Arbeitsweise

Die Kapitel entstehen in einer Planungssitzung (Claude.ai) gegen einen
exportierten Quellenstand; Änderungen am Repo laufen über eine getrennte
Umsetzungssitzung. Nach jeder Codeänderung wird der Quellenexport neu erzeugt
und gegen `arbeit/fakten.md` geprüft.

Faustregel: Lieber ein Kapitel als „Stichworte" stehen lassen, als etwas zu
beschreiben, das nur halb vorhanden ist. Der Befund-Navigator in :8020 und
:8040 ist das Beispiel, an dem das aufgefallen ist (Befund A11).
