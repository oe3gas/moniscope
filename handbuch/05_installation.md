<!-- Status: Entwurf | Quellenstand: 2026-08-29 | offen: A2, A3a, D9 -->

# 5. Installation und erster Start

## 5.1 Dateien ablegen

Die Suite wird nicht installiert, sondern ausgepackt. Legen Sie das
Verzeichnis dorthin, wo es bleiben soll, zum Beispiel nach
`E:\EB200\EB200repo`.

Was Sie dort vorfinden:

| Eintrag | Inhalt |
|---|---|
| `start_eb200.py` | Sammelstarter für alle Server |
| `waterfall.py` | Live-Wasserfall, einziger Kontakt zum Gerät |
| `analyze_*.py` | die Auswerte-Server |
| `manage_jobs.py` | Jobs, Läufe, Aufzeichnungen |
| `eb200_core/` | gemeinsame Bausteine, Katalogverwaltung |
| `known_freq/` | der Frequenzkatalog |
| `tools/` | Hilfsprogramme für Diagnose und Katalogpflege |
| `handbuch/` | dieses Handbuch |
| `sessions/` | Session-Aufnahmen |
| `records/` | Standardablage für Audio und Spektren |
| `jobs/` | Job-Verwaltung |
| `logs/` | Protokolle des Starters |

## 5.2 Den Empfänger eintragen

Die Adresse des Geräts tragen Sie auf der Statusseite ein: In der Kopfleiste
des Wasserfalls auf `EB200 Status` klicken, oben im Block `Connection`
`EB200 address` und `SCPI port` ausfüllen, mit `Test connection` prüfen und
mit `Save` speichern. Der Test baut eine eigene, kurze Verbindung auf und
fragt die Gerätekennung ab; der laufende Betrieb bleibt davon unberührt.

Gespeichert wird in `device.json` in der Datenwurzel:

```json
{
  "eb200_host": "10.0.0.55",
  "scpi_port": 5555,
  "local_ip": null
}
```

Die neue Adresse gilt nach einem Neustart des Wasserfalls (:8000). Fehlt die
Datei, nimmt MoniScope `10.0.0.55` und Port `5555`. Für einen einzelnen Lauf
lässt sich die Adresse auch beim Start vorgeben:
`start_eb200.bat --eb200-host 192.168.1.20` — das hat Vorrang vor der Datei.

`Local address` leer (`"local_ip": null`) bedeutet: Der Server ermittelt
selbst, welche eigene Adresse er dem Gerät als Ziel für die Messdaten nennt.
Hat Ihr PC mehrere Netzwerkschnittstellen — WLAN und Kabel, oder eine
virtuelle Adapterkarte —, kann diese Automatik danebengreifen. Dann tragen Sie
die richtige Adresse fest ein, etwa `10.0.0.10`.

> **Achtung:** Ist hier die falsche Adresse eingetragen, schickt das Gerät
> seine Daten ins Leere. Die Oberfläche sieht dann völlig normal aus, nur der
> Wasserfall bleibt leer.

<!-- Befund A3a erledigt mit T-2013: eigene Datei device.json statt
     station.json - station.json traegt die Region und wird von den
     Katalog-Servern gelesen, die Geraeteadresse braucht nur der Wasserfall. -->

## 5.3 Die Ablage festlegen

Wo Audio- und Spektrum-Aufnahmen landen, steht in `storage_roots.json` im
Repo-Verzeichnis — einer JSON-Liste von Verzeichnissen:

```json
[
  "D:\\EB200_Daten",
  "records"
]
```

Der erste Eintrag ist das Standardziel. Pfade dürfen absolut oder relativ zum
Repo angegeben werden; in JSON wird der Backslash verdoppelt. Fehlt die Datei
oder ist sie fehlerhaft, benutzt die Suite `records` im Repo.

Die Datei wird von Hand gepflegt — kein Programm der Suite schreibt sie. Sie
wird bei jedem Zugriff frisch gelesen, eine Änderung wirkt also ohne
Neustart.

> **Achtung:** Session-Aufnahmen folgen dieser Einstellung **nicht**. Sie
> landen immer im Unterverzeichnis `sessions/` des Repos. Wer die Ablage
> wegen Platzmangels auf ein anderes Laufwerk legt, muss das bedenken — bei
> Langzeitaufnahmen sind Sessions die größten Dateien.

> **Hinweis:** Diese Liste ist zugleich die Erlaubnisliste für den
> Dateizugriff der Auswerte-Server. Verschieben Sie Aufnahmen auf ein
> Laufwerk, das hier nicht steht, sind sie in der Auswertung nicht mehr
> erreichbar.

## 5.4 Starten

Im Repo-Verzeichnis:

```
python start_eb200.py
```

Der Starter bringt alle Server als eigenständige Prozesse hoch, prüft der
Reihe nach, ob deren Ports antworten, und öffnet den Standardbrowser auf
`http://localhost:8000/`, sobald der Wasserfall-Server bereit ist.

| Option | Wirkung |
|---|---|
| `--no-browser` | kein automatisches Browserfenster |
| `--plain` | einfacher Zeilenstrom statt Statusboard, für Protokollierung in eine Datei |
| `--selftest` | prüft den Starter selbst mit zwei Dummy-Prozessen, ohne EB200 |

Der Selbsttest ist ein guter erster Schritt: Er zeigt, ob Python und die
Prozesssteuerung funktionieren, ganz ohne Gerät.

## 5.5 Das Statusboard

Im Konsolenfenster zeichnet der Starter ein Board mit drei Zonen:

1. **Server:** je eine Zeile mit Name, Port, Status (STARTET / LÄUFT /
   BEENDET / ÜBERSPRUNGEN), Ergebnis des Port-Checks, Prozessnummer und
   Fehlerzähler.
2. **Letzte Meldungen:** die jüngsten Konsolenzeilen aller Server,
   chronologisch gemischt.
3. **Fehlerblock:** der zuletzt aufgetretene Fehler im Klartext, damit er
   nicht zwischen Statusmeldungen untergeht.

![Abb. 5.1: Statusboard des Starters mit den drei Zonen](bilder/05_statusboard.png)

Tastatur:

| Taste | Wirkung |
|---|---|
| [1] bis [8] | Anzeige auf einen Server filtern |
| [a] | Filter aufheben |
| [e] | nur Fehler- und stderr-Zeilen zeigen |
| [n] / [p] | im Fehlerblock vor- und zurückblättern |
| [r] | den gefilterten Server neu starten |
| [R] | alle Server neu starten |
| [Strg+C] | alles beenden |

Ein Neustart mit [r] setzt voraus, dass vorher mit [1] bis [8] ein Server
ausgewählt wurde. Der Starter beendet ihn, wartet, bis dessen Port wieder
frei ist, und startet ihn neu; die bisherigen Meldungen bleiben stehen, eine
Trennzeile markiert den Schnitt.

Weil das Board immer nur den sichtbaren Bildschirm neu zeichnet, schreibt der
Starter zusätzlich **jede** Zeile ungefiltert in eine Protokolldatei unter
`logs\start_eb200_<Zeitstempel>.log`. Bei der Fehlersuche ist diese Datei die
verlässlichere Quelle.

> **Hinweis:** Tastatur und Statusboard gibt es nur im echten
> Konsolenbetrieb. Wird die Ausgabe umgeleitet oder `--plain` benutzt, läuft
> die Suite als schlichter Zeilenstrom — dann auch ohne [r] und [R].

## 5.6 Beenden

[Strg+C] im Konsolenfenster. Windows liefert das Signal an alle Prozesse der
Konsole; jeder Server fährt selbst geordnet herunter und meldet sich beim
Gerät ab.

Darüber hinaus koppelt der Starter die Lebensdauer aller Kindprozesse an sich
selbst. Wird er hart beendet — Task-Manager, Schließen des Fensters, Absturz
—, beendet das Betriebssystem die Server mit. Verwaiste Prozesse, die einen
Port belegen, kann es dadurch nicht geben.

## 5.7 Die erste Prüfung

Eine kurze Runde, die zeigt, ob alles zusammenspielt:

1. Im Statusboard stehen alle Server auf LÄUFT (der SDR-Wasserfall darf
   ÜBERSPRUNGEN sein).
2. Auf `http://localhost:8000/` rollt der Wasserfall — es kommen also Daten
   vom Gerät.
3. Über der Frequenzachse erscheinen die Katalog-Streifen mit
   Beschriftungen.
4. Ein Klick in die Übersicht führt in den IFPAN-Zoom, dort ist Audio
   hörbar.
5. Nehmen Sie im Zoom zehn Sekunden Audio auf und beenden Sie die Aufnahme.
6. Auf `http://localhost:8050/` erscheint die Aufnahme in der Liste
   **Aufzeichnungen**.
7. Der Link zur Auswertung öffnet sie auf Port 8070, sie lässt sich abspielen
   und als Spektrogramm ansehen.

Läuft das durch, ist die Suite betriebsbereit.

## 5.8 Wenn etwas nicht startet

| Beobachtung | Wahrscheinliche Ursache |
|---|---|
| Ein Server bleibt auf BEENDET, Meldung „Port belegt" | Reste eines früheren Starts oder ein fremdes Programm auf dem Port |
| Wasserfall bleibt leer, sonst alles normal | Firewall blockiert UDP, oder `Local address` (Statusseite, Block `Connection`) zeigt auf die falsche Schnittstelle |
| Keine Verbindung zum Gerät | `EB200 address` falsch (Statusseite, Block `Connection`, `Test connection`), Gerät aus, oder die zehn Steuerverbindungen des Geräts sind belegt |
| Port 8000 startet gar nicht | numpy fehlt |
| SDR-Wasserfall ÜBERSPRUNGEN | kein SDR angeschlossen oder Hersteller-Werkzeug nicht im Suchpfad |

Ausführlich behandelt Kapitel 16 die Fehlersuche.
