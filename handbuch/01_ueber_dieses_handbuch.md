<!-- Status: Entwurf | Quellenstand: 2026-08-29 | offen: - -->

# 1. Über dieses Handbuch

## 1.1 Für wen dieses Handbuch geschrieben ist

Für alle, die einen Rohde & Schwarz EB200 besitzen und ihn vom PC aus
betreiben wollen — Funkamateure, Monitoring-Interessierte, Betreiber kleiner
Messplätze. Vorausgesetzt wird, dass Sie mit Ihrem Empfänger vertraut sind
und Begriffe wie Bandbreite, Demodulation, Squelch oder Wasserfall nicht
erklärt bekommen müssen.

Programmierkenntnisse sind nicht nötig. Wer die Suite nur benutzen will,
kommt mit Kapitel 5 und Teil C aus. Wer sie anpassen oder erweitern will,
findet in Teil E die technischen Grundlagen.

## 1.2 Was dieses Handbuch nicht ist

Es ersetzt nicht das Originalhandbuch des EB200. Gerätefunktionen werden nur
so weit beschrieben, wie die Suite sie benutzt. Für SCPI-Befehle,
Datagrammformate und elektrische Daten bleiben die Anhänge A bis K des
Herstellers maßgeblich; Kapitel 15 nennt die Stellen, auf die es ankommt.

Es ist auch keine Einführung in die Funkbeobachtung. Was Sie mit den
aufgezeichneten Daten anfangen, bleibt Ihre Sache.

## 1.3 Konventionen

| Schreibweise | Bedeutung |
|---|---|
| `Schreibmaschine` | Dateinamen, Pfade, Befehle, SCPI-Kommandos |
| **Fett** | Bedienelemente der Oberfläche (Knöpfe, Felder, Reiter) |
| [Taste] | Tastatureingabe |
| `http://localhost:8000/` | Adresse im Browser |

Pfade stehen in Windows-Schreibweise. Unter Linux gelten sinngemäß die
entsprechenden Pfade mit Schrägstrich.

Hinweise erscheinen in dieser Form:

> **Achtung:** Warnt vor Datenverlust oder Fehlbedienung.

> **Hinweis:** Ergänzt um Hintergrund, der nicht zum Bedienen nötig ist.

## 1.4 Aufbau des Handbuchs

- **Teil A — Einstieg** (Kapitel 1 bis 5): was die Suite ist, welches
  Vokabular sie benutzt, wie sie eingerichtet und gestartet wird.
- **Teil B — Frequenzkatalog** (Kapitel 6): der Katalog bekannter
  Frequenzen, auf den alle Ansichten zurückgreifen.
- **Teil C — Monitoring** (Kapitel 7 bis 11): die Betriebsarten, ihr
  Zusammenspiel und die Bedienung der einzelnen Ansichten.
- **Teil D — Arbeitsabläufe** (Kapitel 12): durchgehende Rezepte, die
  mehrere Ansichten verbinden.
- **Teil E — Referenz** (Kapitel 13 bis 18 und Anhänge): Dateiformate,
  Schnittstellen, Gerätewissen, Fehlersuche.

Wer die Suite zum ersten Mal startet, liest Kapitel 3 und 5. Wer sie schon
betreibt und ein bestimmtes Detail sucht, beginnt beim Glossar (Kapitel 18)
oder bei der Portübersicht (Anhang B).

## 1.5 Versionsstand

Beschrieben ist der Stand der Suite von August 2026. Erprobt wurde sie gegen
einen EB200 mit Firmware V02.74 unter Windows mit Python 3.14. Abweichende
Firmware- oder Betriebssystemstände sind nicht ausgeschlossen, aber
ungetestet.
