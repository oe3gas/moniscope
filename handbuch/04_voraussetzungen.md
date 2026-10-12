<!-- Status: Entwurf | Quellenstand: 2026-08-29 | offen: A1, B2, B4 -->

# 4. Voraussetzungen und Aufbau

## 4.1 Der Empfänger

Zwingend ist die **LAN-Schnittstelle EB200R4**. Die gesamte Suite läuft über
Ethernet — Steuerung und Messdaten. Ohne LAN-Option ist sie nicht benutzbar.

Welche weiteren Optionen Ihr Gerät hat, entscheidet darüber, welche Teile der
Suite arbeiten:

| Option | Ermöglicht | Ohne diese Option |
|---|---|---|
| EB200R4 (LAN) | alles | Suite nicht benutzbar |
| EB200DS (DIGI-Scan) | D-SCAN, Sessions, breite Übersicht | keine Übersichtsdarstellung; IFPAN und M-SCAN bleiben nutzbar |
| EB200SU (IF Panoramic Unit) | IFPAN-Zoom, Spektrum-Aufnahmen | kein Zoom, keine `.specbin`-Aufnahmen; Audio und M-SCAN bleiben nutzbar |

M-SCAN arbeitet mit den Speicherplätzen des Grundgeräts und braucht keine
zusätzliche Option.

Erprobt ist die Suite gegen Firmware V02.74.

<!-- Befund B2: Die Aussagen zur Spalte "Ohne diese Option" sind aus der
     Gerätelogik abgeleitet, nicht am Code oder an Hardware verifiziert.
     Vor der Veroeffentlichung pruefen. -->

## 4.2 Netzwerk

Empfänger und PC gehören ins selbe Subnetz. Die Adresse des Geräts wird fest
vergeben (alternativ per RARP, siehe Anhang K des Originalhandbuchs).

Verwendet werden:

| Richtung | Protokoll | Port | Zweck |
|---|---|---|---|
| PC → Gerät | TCP | 5555 | SCPI-Steuerung |
| Gerät → PC | UDP | 18102 | Anzeige-Datenstrom (Wasserfall, Mithören) |
| Gerät → PC | UDP | 18103 | Spektrum-Aufnahme |
| Gerät → PC | UDP | 18104 | Audio eines laufenden Jobs |
| Gerät → PC | UDP | 18105 | MSCAN-Treffermeldungen |

Dass jeder Datenstrom seinen eigenen Port bekommt, ist Absicht: Nur so bleibt
die Erkennung fehlender Datagramme zuverlässig.

> **Achtung:** Die Windows-Firewall muss eingehende UDP-Verbindungen für
> `python.exe` zulassen. Ohne diese Freigabe erscheint die Oberfläche normal,
> aber der Wasserfall bleibt leer — das Gerät sendet, der PC verwirft. Das
> ist der mit Abstand häufigste Fehler bei der Inbetriebnahme.

Zwei Grenzen des Geräts sind gut zu wissen:

- Es nimmt höchstens **zehn** gleichzeitige TCP-Steuerverbindungen an.
- Es merkt sich höchstens **vier** UDP-Ziele gleichzeitig.

Beides ist im Normalbetrieb reichlich; wer nebenher eigene Werkzeuge laufen
lässt, sollte es im Hinterkopf behalten.

## 4.3 Der PC

- **Betriebssystem:** Entwickelt und erprobt unter Windows. Ein Betrieb unter
  Linux (auch auf einem Raspberry Pi) ist vorgesehen, aber noch nicht
  Bestandteil dieses Handbuchs. Windows-spezifisch sind das Statusboard des
  Starters und dessen Aufräumen beim Beenden.
- **Python:** Version 3.14, erprobt.
- **numpy:** wird gebraucht. Der Live-Wasserfall auf Port 8000 benutzt es für
  die Auto-Marker, der SDR-Wasserfall auf Port 8030 für die Spektralrechnung
  und die Audio-Auswertung auf Port 8070 für Spektrogramm und
  Klassifikation. Ohne numpy startet Port 8000 nicht.
- **Browser:** Ein aktueller Browser. Die Seiten benutzen Server-Sent-Events
  und Canvas-Darstellung, aber keine externen Bibliotheken.
- **Platte:** Aufzeichnungen wachsen schnell. Rechenbeispiele für die
  einzelnen Aufnahmearten stehen in Kapitel 13. Für Langzeitaufnahmen
  empfiehlt sich ein eigenes Laufwerk, eingetragen in `storage_roots.json`.

<!-- Befund A1: numpy ist Pflichtabhaengigkeit, weil waterfall.py ueber
     eb200_core/signal_detect.py hart importiert. Sollte die Entscheidung
     fallen, den Import abzusichern, ist dieser Abschnitt anzupassen. -->

## 4.4 Optionale SDR-Hardware

Der SDR-Wasserfall auf Port 8030 ist eine Zwischenstufe zwischen der breiten
D-SCAN-Übersicht und dem schmalen IFPAN-Zoom: Er zeigt einen lückenlosen
Ausschnitt, in dem sich ein Signal bequem anvisieren lässt, bevor der EB200
darauf gezoomt wird.

Unterstützt sind HackRF One (1 MHz bis 6 GHz) und Airspy (24 MHz bis
1,8 GHz). Angesprochen werden sie über ihr jeweiliges
Hersteller-Kommandozeilenwerkzeug — bei HackRF also `hackrf_transfer`.

> **Achtung:** Die Pfade zu diesen Werkzeugen stehen derzeit als Konstanten
> im Quelltext von `sdr_waterfall.py`. Wer die Werkzeuge anderswo installiert
> hat, muss sie dort eintragen.

Ohne SDR-Hardware fehlt nichts Wesentliches: Der Server meldet dann schlicht,
dass kein Gerät verfügbar ist, oder wird beim Start übersprungen.

## 4.5 Antennen und Umfeld

Die Suite schaltet keine Antennen. Wenn Sie eine Antennenumschaltung oder
-matrix betreiben, bleibt deren Bedienung Handarbeit.

> **Hinweis:** Pegelangaben in den Aufzeichnungen sind nur so viel wert wie
> das Wissen über den Antennenweg. Wer Pegel später vergleichen will, notiert
> Antenne und Dämpfung im Kurztext der Aufnahme — die Suite kann das nicht
> wissen.
