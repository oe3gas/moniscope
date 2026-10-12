# Rohde & Schwarz EB200 – Geräteprofil

*Pflege im Repo unter docs/EB200_Geraeteprofil.md; nach Änderungen ins
Claude-Projekt hochladen (wie WS1–WS3).*

Projektwissen zum angeschafften Miniport-Empfänger R&S EB200.
Stand der Konfiguration: laut Selbsttest- und Menü-Screenshots des konkreten Geräts.

---

## 1. Konkretes Exemplar – Konfiguration

| Baugruppe | Version | Datum |
|---|---|---|
| MAIN CPU | V02.74 | 2005-01-26 |
| IF DSP | V03.23 | 2001-01-16 |
| IF PAN DSP | V01.03 | 2000-08-10 |
| FP CTRL CPU | V01.02 | 1998-12-09 |
| DC-CONVERTER CPU | V01.08 | – |

- **Seriennummer Hauptgerät:** EB200 101.459 (Sachnr. 4052.2000.03)
- **Seriennummer LAN-Interface:** 101762 (Sachnr. 4052.9156.02)
- **Firmware-Stand:** V02.74 (2005) – späteste Hauptversion
- **Selbsttest:** alle Baugruppen OK (Preselection, Frontend, IF Section 2,
  Proc/ERAM, Front Panel, Remote LAN, IF Panorama, DC Converter)

---

## 2. Installierte Optionen (dieses Gerät)

| Option | Sachnr. | Funktion | Status |
|---|---|---|---|
| EB200SU – IF Panoramic Unit | 4052.3206.02 | Internes ZF-Panorama-Sichtgerät | ✅ vorhanden |
| EB200DS – RF Spectrum DIGI-Scan | 4052.9604.02 | Schneller RF-Spektrum-Suchlauf | ✅ INSTALLED |
| EB200R4 – LAN Interface | 4052.9156.02 | Ethernet 10Base-T Fernsteuerung | ✅ vorhanden |
| IFWB-Ausgang (X5) | – | Analoger Breitband-ZF-Ausgang | ✅ physisch vorhanden |

Damit sind die drei begehrten Hardware-Optionen (Panorama, DIGI-Scan, LAN) an Bord –
das macht das Gerät zum voll ausgestatteten Exemplar statt Grundmodell.

## 3. NICHT installierte Software-Optionen

| SW-Option | Funktion | Status | Relevanz Feststation |
|---|---|---|---|
| COVERAGE MEASUREMENT | Versorgungs-/Abdeckungsmessung (Drive-Test) | ❌ NOT INSTALLED | ohne Nutzen (Behördenfunktion) |
| FIELD STRENGTH | Kalibrierte Feldstärke in dBµV/m (mit Antennenfaktor) | ❌ NOT INSTALLED | verzichtbar; Grund-Pegelmessung dBµV bleibt |

> Hinweis: Freischaltung nur kostenpflichtig über R&S-Optionscode,
> für Privatkunden praktisch nicht mehr zu bekommen.
> Die reine Pegelmessung in dBµV am Eingang funktioniert als Grundfunktion.

---

## 4. Allgemeine Gerätedaten

- **Typ:** Miniport-Empfänger für Radiomonitoring / Störungssuche (Radiolocation)
- **Frequenzbereich:** 10 kHz – 3 GHz (durchgehend)
- **Dynamikbereich:** ca. 110 dB
- **Frontend:** mitlaufender Preselektor, hohe Großsignal-/Intermodulationsfestigkeit
- **Demodulationsarten:** AM, FM, PULSE, CW, LSB, USB, IQ
- **ZF-Konzept:** Mehrfachüberlagerung; 3. ZF bei 10,7 MHz
- **Pegelmessung:** kalibriert in dBµV am Eingang
- **Aufbau:** tragbar, akkufähig, feldtauglich (Körpertragebetrieb möglich)

### Ursprünglicher Anwendungszweck
Professionelles Spektrum-Monitoring nach ITU, Störungssuche und Peilung (mit
Handpeilantenne HE200), behördliche/militärische Funkaufklärung, Aufspüren
unautorisierter Sender. **Monitoring-Empfänger, kein Kommunikationsempfänger.**

---

## 5. Rückseitige Anschlüsse

| Bez. | Anschluss | Funktion |
|---|---|---|
| X91 | RJ45 | Ethernet 10Base-T (LAN-Interface EB200R4) |
| X5 | BNC | IFWB – Breitband-ZF-Ausgang um 10,7 MHz (nutzbare Breite offen, siehe Abschnitt 6) |
| X4 | BNC | Referenz EXT/INT (10-MHz-Referenz aus- oder eingeben) |
| X13 | SMA | Antenne (u. a. für Rack-Einbau) |
| X6 | 25-pol | OPTION – digitaler ZF-Ausgang (IQ, seriell), externes Panorama-Zusatzgerät, Software-Update |
| X8 | 37-pol | AUX |
| X7 | 15-pol | AUDIO |
| X1 / X2 / X12 | – | DC-Versorgung 10–30 V / LINE |
| X10 | – | Battery Pack |

---

## 6. LAN-Schnittstelle (EB200R4) – Fähigkeiten

- **SCPI-Fernsteuerung über TCP/IP:** vollständige Steuerung aller Frontpanel-Funktionen
  (Frequenz, Modulation, Bandbreite, Squelch, Attenuator, Scan, Speicher).
  Gut per Python (socket / pyvisa) skriptbar → Automatisierung, Logging, Belegungsstatistik.
- **Webinterface:** Display im Browser unter `http://<IP>:81`, Aktualisierung alle 2 s
  (langsam, nur für gelegentliche Fernsicht).
- **Messwert-Auslese:** Pegel, Scan-Ergebnisse, Statusregister über SCPI abholbar.

### Verifizierte Eigenschaften (eigenes Gerät, Firmware V02.74)

Die Punkte oben stammen aus dem Handbuch. Hier steht, was am eigenen Gerät
tatsächlich gemessen wurde – mit Datum, Methode und ausdrücklich dem, was
**nicht** geprüft ist.

**Mehrere SCPI-Clients gleichzeitig** *(2026-08)*
Eine zweite TCP-Verbindung auf Port 5555 wird parallel zu einer bestehenden
Controller-Verbindung mit laufendem D-SCAN angenommen (Handbuch 4.x: der
„list socket" legt je Host einen eigenen Remote-Client an). Messung: 71
Queries mit 50 ms Abstand über die Zweitverbindung, währenddessen D-SCAN
über die Erstverbindung aktiv – Sweep-Dauer unverändert (Δ ≤ 0,02 s),
keine UDP-Lücken, alle Queries beantwortet. Genutzt von der
Hintergrund-Inventur des Parameter-Memory (WS3, Abschnitt 12).
*Nicht geprüft:* Verhalten vieler Clients unter aktiver Last (die reine
Client-Zahl ist inzwischen gemessen, siehe „Verbindungsgrenzen (Stresstest)"
unten); **Set-Kommandos** über die
Zweitverbindung (nur Queries getestet – bei konkurrierenden Sets wäre
offen, wer gewinnt und wie sich das STATus:EXTension-Change-Bit-Register
verhält); Verhalten während IFPAN-Zoom statt D-SCAN. Bis zur Prüfung gilt:
Zweitverbindungen nur lesend.

**Verbindungsgrenzen (Stresstest)** *(2026-08)*
Gemessen mit `stress_connections.py` (schrittweiser Aufbau paralleler
Verbindungen, jede mit `*IDN?` verifiziert, danach Liveness-Recheck der
gehaltenen Verbindungen).
- **TCP-Remote-Clients (Port 5555):** 10 gleichzeitige Verbindungen von
  einem Host werden angenommen und beantworten `*IDN?`; alle 10 bleiben
  während der Haltephase aktiv – der älteste Client wird nicht verdrängt.
  Die 11. Verbindung weist das Gerät per TCP-Reset ab. Harte Obergrenze
  also **10 Remote-Clients pro Host** (bisher waren nur 2 bestätigt).
- **UDP-Paths (UdpPath-Tabelle, Annex F):** `TRAC:UDP? MAX` liefert Index 3,
  d. h. die Tabelle fasst **4 Einträge** (Index 0..3). Index 0 ist der im
  CMOS gespeicherte Default-Eintrag. Für die drei parallelen Projekt-Streams
  (IFPAN-Display, Audio, Spektrum-Bins) reicht das exakt; nennenswerter
  Spielraum für weitere UDP-Ziele besteht nicht.
*Methodik/Grenze:* alle TCP-Testverbindungen kamen von derselben Quell-IP
(verschiedene Quell-Ports) → Aussage gilt „Clients pro Host"; ein
Mehr-Host-Szenario ist damit nicht abgedeckt. Rein lesend getestet
(`*IDN?`); das Verhalten der 10 Clients unter gleichzeitiger D-SCAN/IFPAN-Last
und bei konkurrierenden Set-Kommandos ist nicht geprüft.

**SCPI-Parameterinventur** *(2026-08)*
71 Queries aus Annex C / Handbuch Tabelle 4-11 werden beantwortet (Liste:
`eb200_core/param_store.py`, `EB200_QUERIES`). Abweichung vom Handbuch:
Tabelle 4-11 nennt `CALC:IFPAN:TYPE?`, das Gerät kennt nur
`CALCulate:IFPan:AVERage:TYPE?` (Kapitel 4.5). Bei Widersprüchen gilt die
Befehlsbeschreibung in 4.5, nicht die Tabelle.

**Pegelabfrage braucht das `SENSe:`-Präfix** *(2026-09-06)*
Annex C schreibt `[SENSe:]DATA?` mit optionalem Präfix. Am Gerät ist es
**nicht** optional: `DATA?` allein liefert eine Leerzeile und legt
`-109,"Missing parameter;DATA?"` in die Fehler-Queue; `SENSe:DATA?` liefert
den Pegel. Messung im IFPAN-Zoom, Demodulation IQ, BW 15 kHz, MGC 21 dBµV:
`SENSe:DATA?` → `23.5` (dBµV), unmittelbar danach `SYSTem:ERRor?` →
`0,"No error"`. Voraussetzungen im selben Zustand belegt:
`SENSe:FUNCtion?` → `"VOLT:AC"` und `FORMat:DATA?` → `ASC` (beides
Werkslage, Parameter-Schnappschuss desselben Tages).
Genutzt von `eb200_core/rx_snapshot.py` (T-917); der Fehlerfall war dort bis
zum Nachtrag aktiv und hat bei jeder Aufnahme einen `-109`-Eintrag
hinterlassen, ohne dass es auffiel – die Abfrage schluckt Fehler und schreibt
`None`.
*Kurzformen sonst unauffällig:* Paarvergleich Lang-/Kurzform am Gerät ergab
für `INPut:ATTenuation:AUTO?`/`INP:ATT:AUTO?` und
`INPut:ATTenuation:STATe?`/`INP:ATT:STAT?` jeweils identische Antworten.
Nur die Pegelabfrage verhält sich anders.
*Nicht verwechseln:* Der Abschnitt „Ablage-Sensorfunktion
(FREQuency:OFFSet)" weiter unten stellt fest, dass `SENSe:DATA?` in CW, USB,
LSB und IQ konstant 0,0 liefert. Das gilt für die Sensorfunktion
`FREQuency:OFFSet`, **nicht** für die hier beschriebene Pegelmessung mit
`FUNCtion "VOLTage:AC"` – derselbe Befehl, zwei verschiedene Messgrößen.

### D-SCAN: Mindestspanne (empirisch)

Das Gerät lehnt D-SCAN-Bereiche unter etwa **20 kHz** mit
`-221,"Settings conflict;Span too low"` ab (Annex B: Warnung). Bisektion aus
der Ausgangslage 32–48 MHz, BW 30 kHz: 100/50/30/20 kHz OK, 10/5/2 kHz
abgelehnt. Richtungs-unabhängig (auf- wie abwärts) und **unabhängig von der
IF-Bandbreite** (2 kHz auch bei BW 600 Hz und 9 kHz abgelehnt). Das
abgelehnte Kommando behält den Altwert – ohne Vorprüfung liefe der Sweep
still mit falschem Bereich (beobachtet: Soll 48.830–48.832 MHz, Ist
32.000–48.832 MHz). Umgesetzt als `DSCAN_MIN_SPAN_HZ` in `waterfall.py`;
Verifikationsbefunde erscheinen seither auch in der Browser-Meta-Zeile.
Nicht im Handbuch dokumentiert; Stand Firmware V02.74.

### D-SCAN: Gerät sendet nicht („0 Pakete"-Verklemmung)

Beobachtet am 2026-08-22 (Firmware V02.74): Nach dem Start stand das Gerät auf
**130–240 MHz, Speed HIGH, BW 50 kHz** und lieferte **kein einziges
D-SCAN-Datagramm**. Der Sweep-Watchdog in `waterfall.py`
(`SWEEP_WATCHDOG_S = 20 s`) schlug siebenmal in Folge an, jedes Mal mit der
Variante „seit 20 s KEIN D-SCAN-Datagramm … Gerät sendet nicht"; der zugehörige
⚠-Hinweis erschien über den SSE-Typ `notice` in der Browser-Meta-Zeile.
Belegte Dauer: **mindestens zwei Minuten** (sieben Watchdog-Schläge à 20 s).
Der Zustand bestand bereits vor der ersten Messung – der Beginn ist unbekannt,
die tatsächliche Dauer also nach oben offen.

**Auflösung:** ein Bereichswechsel, also ein erneutes `_configure_dscan` mit
anderen Grenzen. Danach lief der Sweep sofort wieder.

Einordnung:
- Der Watchdog unterscheidet bewusst zwei Fälle. „0 Pakete" heißt: das Gerät
  sendet überhaupt nicht (Konfiguration abgelehnt oder Verklemmung).
  „Unvollständig" heißt: Pakete kommen an, der Sweep wird aber nicht fertig
  (Paketverlust). Nur die erste Variante trat hier auf.
- Der Zustand ließ sich nicht gezielt herbeiführen – er trat spontan nach dem
  Einschalten auf. Ob eine bestimmte Parameterkombination oder ein Zustand aus
  einer vorangegangenen Sitzung ursächlich ist, ist offen.
- Die SCPI-Verbindung selbst war nicht auffällig; verworfen wurde nur der
  UDP-Datenstrom. Eine Rückmeldung des Geräts, die auf den Zustand hingewiesen
  hätte, gab es nicht.

**Konsequenz für die Bedienung:** Bleibt der Wasserfall nach dem Start leer und
meldet die Meta-Zeile „Gerät sendet nicht", genügt ein Bereichswechsel
(„Bereich übernehmen" mit anderen Grenzen). Ein Neustart des Servers oder des
Geräts ist nicht nötig.

**Offen:** Ob ein automatischer Rekonfigurationsversuch nach dem zweiten
Watchdog-Schlag sinnvoll ist. Dagegen spricht, dass ein selbsttätiges Umsetzen
von Geräteparametern im laufenden Monitoring unerwünscht sein kann; dafür, dass
der Zustand sonst unbemerkt eine ganze Nacht kosten kann. Vor einer Umsetzung
sollte der Fall mindestens ein zweites Mal beobachtet werden.

### Annex-F-Befunde (validiert 2026-08, tools/test_opt_header.py)

Zwei Hardware-Läufe (2026-08-23) gegen das eigene Gerät (S/N 101.459,
V02.74, Protokollversion `minor_version_number = 0x26`), je eigener
Test-UdpPath (18110/18111/18112), produktive Pfade 18102/18103 unberührt.

- **IQ-Ausgabe über den AUDIO-Kanal (Remote-Mode 1, 32 kHz/16 Bit/2 Kanäle)
  ist DC-frei und richtig gepolt:** gemessen 2026-08-31 mit tools/wav2sdr.py
  --analyze an einer IFPAN-Zoom-Aufnahme (Mitte 124,997 MHz, Träger
  +5,19 kHz): dominante Frequenz +5189,5 Hz (positiv = oberhalb der Mitte,
  also Kanal 0 = I, Kanal 1 = Q wie angenommen), DC-Bin -75,7 dB unter dem
  Peak. Für SDR-Exporte (HDSDR/SigMF) ist weder --swap-iq noch eine
  DC-Kompensation nötig; ein Träger rechts der Mitte erscheint auch im
  SDR-Programm rechts.
- **Audio-OptionalHeader wird NICHT geliefert:** `opt_len = 0`, obwohl der
  Audio-UdpPath dediziert mit dem Flag `"OPT"` registriert wurde und die
  Protokollversion (0x26) laut Annex F ein `sDemodulation`-Feld erlauben
  würde. Konsequenz: MOD/BW sind über den Audio-Stream nicht ermittelbar.
- **IFPan-OptionalHeader (16 Byte)** enthält nur `frequency`,
  `spanFrequency`, `averageType`, `measureTime` – kein MOD/BW/Speed.
- **DScan-OptionalHeader (22 Byte):** `start-`/`stop-`/`stepFrequency`,
  `markFrequency`, `bwZoom`, `referenceLevel`, `newStepScheme`. `bwZoom`,
  `mark_hz` und der vom Gerät gekoppelte Zoom-Bereich (`start`/`stop`)
  werden **live nachgezogen** – sichtbar im nächsten Datagramm nach ca.
  0,5 s Einschwingzeit, ohne erneutes `INIT:IMM` (bestätigt im
  Wiederholungslauf mit Socket-Drain und Einschwingpause, siehe unten).
- **Merksatz Testmethodik:** nach SCPI-Kommandos, die den Stream-Inhalt
  ändern, zuerst den OS-Empfangspuffer leeren (bereits wartende Alt-Pakete
  verwerfen), dann erst messen – bei ~180 Datagrammen/s liegen sonst noch
  Pakete von VOR der Umschaltung im Puffer und die Änderung erscheint
  fälschlich als „nicht sichtbar" (so im ersten Lauf geschehen; im zweiten
  Lauf mit `drain_socket()` + 0,5 s Pause bestätigt).
- **MOD, BW, Speed und Squelch-Schwelle sind ausschließlich per SCPI
  abrufbar** (`DEM?`, `BAND?`, `FREQ:DSC:SPE?`,
  `OUTP:SQU:STAT?`/`OUTP:SQU:THR?`) – kein UDP-OptionalHeader trägt sie.

### Wichtige Grenze
- **Kein breitbandiges IQ-Streaming über LAN.** Das 10Base-T-Interface ist für
  Steuerung und schmalbandige Daten gedacht. Für SDR-Kopplung führt der Weg über
  den **analogen IFWB-Ausgang X5**, nicht über Ethernet.
- Echtes netzwerkfähiges Wideband-IQ kann erst der Nachfolger EB500.

### X6 (Digital IF Output) – Handbuchbefund und Bewertung 2026-09-03

**Handbuchlage.** Kapitel 2.1 (Position 30) beschreibt X6 „OPTION“ als 25-poligen
Stecker mit drei Funktionen: Anschluss eines externen digitalen
Panorama-Anzeigegeräts (Option), digitaler ZF-Ausgang und Anschluss für das
Software-Update. Die Steckerbelegung selbst steht nur in der separaten
Interface-Beschreibung 4052.2000.01 SB, die nicht vorliegt.

**Digitaler ZF-Ausgang nach Annex I:**
- Voraussetzung ist Demodulationsart IQ (`[SENSe:]DEModulation IQ`, alias `A0`).
  Im AGC-Betrieb ist der Ausgang laut Handbuch vorgeregelt – für pegelrichtige
  Aufzeichnung also MGC.
- Synchron-serielle TTL-Schnittstelle: SFRAME (Pin 12), SCLOCK (Pin 24),
  SDATA (Pin 25), getrieben von einem 74LVC245 an 3,3 V mit je 100 Ω in Reihe.
  Handbuchgrenze für die Leitungslänge: 20 cm ohne eigene Treiber.
- Format: je Frame Realteil, dann Imaginärteil, je 16 Bit im Zweierkomplement;
  Bitakt = fs × 32.
- Die Abtastrate hängt an der eingestellten ZF-Bandbreite: 256 kHz bei
  150/120 kHz (8,192 Mbit/s), 128 kHz bei 50 kHz, 64 kHz bei 30 kHz, 32 kHz bei
  15 kHz, 16 kHz bei 9/6 kHz, herunter bis 0,25 kHz bei 150 Hz (8 kbit/s).
- Als Gegenstelle nennt Annex I einen DSP der Motorola-563xx-Familie (ESSI0)
  oder ein FPGA, das den seriellen Strom parallel weiterreicht.

**Bewertung: keine Hardwareerweiterung für X6.** Der digitale Weg ist mit
maximal 150 kHz ZF-Bandbreite **schmaler** als der analoge X5-Weg und verlangt
zugleich eigene Hardware (FPGA oder MCU, 8,2 Mbit/s, 20 cm Leitungslänge,
fehlende Pinbelegung). Sein einziger Vorteil sind 16 Bit Auflösung und
kalibrierter Pegel gegenüber 8 Bit unkalibriert beim HackRF – beides braucht
MoniScope nicht: kalibrierte Pegel liefert der EB200 per SCPI, und die
SDR-Zwischenstufe ist in WS3 ausdrücklich als relative, unkalibrierte Ebene
definiert. Hinter Preselektor, Frontend und ZF-Filter ist der Dynamikbedarf im
Fenster um 10,7 MHz zudem deutlich kleiner als an der Antenne. Zweiter
Nachteil: bei X6 hängt die Abtastrate an der ZF-Bandbreite – die volle Rate
erzwingt 150 kHz am Gerät und damit den Verlust der schmalen Selektion fürs
Mithören; am X5 ist die ZF-Bandbreiteneinstellung ohne Einfluss auf das
Fenster.

**Offene Messungen am X5-Weg** (Aufgaben T-912 und T-913):
- Tatsächlich nutzbare flache Bandbreite. Handbuch 2.1 nennt 10,7 MHz ± 5 MHz,
  dieses Profil führte bisher ±1 MHz. Die Differenz entscheidet über die
  Zoomstufe des SDR-Wasserfalls und ist zu messen.
- Pegel am X5 und ob ein Dämpfungsglied vor den HackRF gehört.
- Verhalten bei AGC (Vorregelung analog zur Annex-I-Aussage erwartet, aber
  nicht bestätigt) – für pegelstabile Aufzeichnungen voraussichtlich MGC.
- Referenzkopplung: X4 gibt die interne 10-MHz-Referenz aus, der HackRF hat
  einen CLKIN-Eingang. Bei gemeinsamer Referenz und fester ZF von 10,7 MHz
  genügt eine einmalige Kalibrierkonstante, damit die Frequenzachse des
  SDR-Wasserfalls dauerhaft zur EB200-Frequenz passt.
- HackRF-Randbedingungen: 200-kHz-Regel gegen den DC-Peak (versetzt abstimmen,
  Abtastrate entsprechend größer wählen) sowie der Mischerpfad unterhalb
  30 MHz mit seinen Nebenprodukten – Leerlaufspektrum ohne Antenne am EB200
  als Referenz aufnehmen.

---

## 7. Einsatzideen im eigenen Setup

- **SDR-Kopplung:** IFWB-Ausgang (X5) als hochwertiges Frontend vor ein SDR schalten;
  Preselektor/Großsignalfestigkeit des EB200 nutzen, Breitbandverarbeitung im PC.
  Momentanbandbreite noch nicht gemessen (Handbuch 2.1: ±5 MHz, bisherige
  Profilannahme ±1 MHz) – offen als T-912. Der digitale Weg über X6 wurde
  geprüft und verworfen, siehe Abschnitt 6.
- **Automatisiertes Monitoring:** per SCPI/LAN Frequenzlisten absuchen, Pegel
  protokollieren, Belegungsstatistiken über Nacht erstellen.
- **Störungssuche am Standort:** kalibrierte Pegelanzeige zum Aufspüren lokaler
  QRM-Quellen (z. B. Schaltnetzteile, Inverter).
- **Reiner Empfang/Charakterisierung:** Detektion, Modulationsart, Bandbreiten.

---

## 8. Status geklärter Punkte

- **Stromversorgung:** im Shack über 13,8 V gewährleistet → kein R&S-Netzgerät nötig.
- **Akku EB200BP:** nicht vorhanden – irrelevant, da kein Portabelbetrieb geplant.
- **HF-Performance:** OK und getestet.

---

## 9. Weitere mögliche Optionen (nicht an diesem Gerät)

- **EB200BP** (4052.4102.02) – Battery Pack
- **EB200SC** (4052.9304.02) – Carrying Case (Teleskopantenne, Headset, Gürtel)
- **EB200ZZ** (4052.8250.02) – Rack Adapter (19")
- **Antennen:** HE200 / HE200HF (Handpeil), Loop-Antennen (0,01–20 / 20–200 / 200–500 MHz),
  Log-Periodic 500 MHz – 3 GHz

---

*Rufzeichen Betreiber: OE3GAS*

---

## MSCAN (Speicherkanal-Scan) – Hardwarebefunde 2026-08 (tools/mscan_probe.py, FW V02.74)

Speicherplaetze
- Programmieren per ASCII: MEMory:CONTents MEMn,<F Hz>,<THR dBuV>,<DEM>,<BW Hz>,(@<ant>),<ATT>,<ATTA>,<SQUC>,<AFC>,<ACT>
  (ACT=1 aktiviert den Platz fuer den Scan). Kein packed struct noetig (T-501 erledigt).
- Readback: FORMat:MEMory ASCii; MEMory:CONTents? MEMn -> F,THR,DEM,BW,#14(@n),ATT,ATTA,SQUC,AFC,ACT
  (reine Zahlen); MEMory:CONTents:MPAR? MEMn -> 1|0. Antennenfeld der Anlage: (@0).
- MEMory:CLEar MEM0,MAXimum loescht alle 1000 Plaetze.
- Zeitbedarf: ~210–280 ms je Platz inkl. *OPC? (1000 Plaetze ~3,5–4,5 min); MPAR?-Abfrage 4 ms.

Scan-Steuerung
- SENSe:MSCan:COUNt 1 (ein Zyklus) | INF (9.9E37 als Antwort); DWELl 0..10 s;
  HOLD:TIME 0..10 s; CONTrol "STOP:SIGN" (CONTinue ist falsch, -113);
  OUTPut:SQUelch:CONTrol MEM (Squelch je Platz); FREQ:MODE MSC.
- Deterministischer Start bei MEM0: MSCan:CHANnel MEM999 setzen, dann INIT:CONM.
  INIT:CONM allein setzt ab dem aktuellen Kanal fort (zustandsabhaengig, MEM0 wird
  uebersprungen, wenn CHANnel=0). INIT:IMM begann im Test ebenfalls bei MEM0, aber nur
  bei CHANnel=4 geprueft.
- Erster Platz eines Zyklus erhaelt nur ~1,5 s Dwell statt des eingestellten Werts
  (Uhr laeuft vermutlich schon waehrend der Betriebsart-Umschaltung).
- Ohne Signal ~25 ms je Kanalbesuch (5 Kanaele: 8–10 Zyklen/s); 1000 Plaetze ~25 s/Zyklus.

Datagramme (UDP-Tag MSC, Annex F Tag 201)
- Flags "VOLTage:AC","FREQuency:RX","SQUelch","OPTional": je Squelch-Oeffnung EIN Item
  mit Frequenz (Hz) und Pegel (dBuV*10); ohne Signal keine Items. OptionalHeader 10 Byte:
  cycleCount, holdTime, dwellTime, directionUp, stopSignal.
- Zyklusende-Marker: Item mit Frequenz 0 und Rohpegel 2000 (200,0 dBuV) am Ende jedes
  Zyklus, auch bei COUNt INF. Empirisch, nicht im Handbuch dokumentiert.
- Mehr-Item-Datagramme kommen vor (mehrere Oeffnungen kurz hintereinander).

Audio waehrend MSCAN (Tag AUD, SYST:AUD:REM:MOD 6 = 16 kHz/16 Bit/mono)
- Audio kommt ausschliesslich bei offenem Squelch (Segmente 32–35 kB/s, im Test 0
  Datagramme ausserhalb). Der Zusatz "Waehrend der Hold-Zeit kein Audio" galt fuer
  den 5-Kanal-Probelauf 2026-08 und ist WIDERLEGT - siehe Abschnitt
  "Audio-Bilanz aus Produktionslaeufen" weiter unten.
- Segmentende-Signal: ~Hold-Zeit nach dem letzten Nutzdaten-Datagramm folgt ein
  28-Byte-Datagramm (nur Header). Segmentdauer = reine Sendedauer.
- Beim Einschalten des Pfads Startburst (bis 7400 B je Datagramm, kurzzeitig ~67 kB/s):
  gepufferte Samples, kein Formatwechsel.
- Audio-Datagramme tragen wie bekannt keinen OptionalHeader; Kanalzuordnung ueber das
  zeitlich vorangehende MScan-Item.
- Lauf-Befunde 2026-08-27 (T-1005 Abnahme): Startburst des Audio-Pfads = 22-23 Datagramme
  (~1 s) VOR dem ersten MScan-Item, bei jedem Lauf gleich - im Recorder als audio_orphan_pre
  gezaehlt, kein Datenverlust. Ueberwachung (COUNt INF) mit Dwell 3/Hold 0 und nur einem
  Kanal ueber Squelch: 3 s auf dem Kanal, ~100 ms Rundlauf ueber die stillen Plaetze, wieder
  derselbe Kanal - wirkt wie "haengt", Zyklusende-Marker kommt je Runde. Inventur (COUNt 1)
  endet regulaer am Marker (6 Kanaele, Dwell 3: ~20 s). Stopp schliesst das offene Segment
  sauber mit Dauer bis zum Stoppzeitpunkt.

Audio-Bilanz aus Produktionslaeufen (Messreihe 2026-09-08, 19 Laeufe, T-1014)
- Quelle: Auswertung der job.json-Manifeste. Empfangener Ton = stats.bytes / 32000 B/s
  (Modus 6) plus audio_orphan_mid * 30 ms; der 30-ms-Datagrammtakt stammt aus Annex F
  und wurde ueber bytes/duration der behaltenen Aufnahmen gegengeprueft (33,0-33,9 kB/s,
  Abweichung = Zaehlung der Intervalle statt der Datagramme).
- Waehrend der Hold-Zeit KOMMT Audio, und zwar OHNE begleitendes MScan-Item. Ein Item
  entsteht nur bei der Squelch-Oeffnung; oeffnet der Squelch in einer Sprechpause
  erneut, fliesst Ton ohne Item. Gemessen: 2,0-2,3 s je gescheiterter Oeffnung bei
  hold_s 2,0, exakt 0,0 s bei hold_s 0 (Laeufe 20260908_191653 und 20260908_200359 -
  gleicher Job, gleicher Abend, je 20 min).
- Der 28-Byte-Marker folgt ~Hold-Zeit nach dem letzten Nutzdaten-Datagramm. Eine
  Sprechpause laenger als die Hold-Zeit beendet das Segment damit MITTEN in der
  Aussendung.
- Die Hold-Zeit erhoeht die empfangene Tonmenge erheblich: derselbe Job, je 20 min,
  hold_s 2,0 -> 396 s empfangen; hold_s 0 -> 155 s. Ohne Hold verlaesst der Empfaenger
  den Kanal nach jedem Datagramm; das Fehlende ist dann nicht verworfen, sondern nie
  empfangen. hold_s 0 taugt deshalb NICHT als Gegenmittel gegen den Verlust.
- Zyklusdauer bei 61 Kanaelen: 0,69-1,2 s (1738 Zyklen in 20 min). Aufeinanderfolgende
  Aufnahmen desselben Kanals liegen im Median 0,87 s auseinander, also genau einen
  Rundlauf - eine Aussendung wird ueber mehrere Zyklen in Stuecke zerlegt, die einzeln
  an min_recording_s scheitern.
- Squelch-Schwelle (durchgehend 20 dBuV), Demodulation und WAV-Format waren in allen
  Laeufen unauffaellig; eb200_core/mscan_run.py ist seit T-1006 unveraendert. Die
  Verluste sind KEIN Geraeteeffekt (siehe T-1014).
- Bedienhinweis: bei eingeschalteter TONE-Ausgabe am Geraet liefert der AUD-Pfad statt
  Sprache einen pegelabhaengigen Ton (bei AM klingt das melodisch). Die Aufnahme ist
  dann technisch fehlerfrei und trotzdem unbrauchbar - bei "Toene statt Sprache" zuerst
  die Geraeteeinstellung pruefen.

---

## AFC und Ablagemessung

Quelle: Messreihe 2026-09 mit `tools/afc_probe.py` (T-507/T-511/T-512),
Dauertraeger 125,0014 MHz, Pegel rund 19 dBuV, Rohdaten unter
`docs/messungen/afc_*.json`. Geraet S/N 101.459, Firmware V02.74.

### Zulaessige Demodulationsarten

Das Handbuch (3.4.5) nennt AM, FM und PULSE. Die Firmware ist grosszuegiger
als die Doku: `SENSe:FREQuency:CW:AFC ON` wird zusaetzlich in CW und IQ
ohne Fehler angenommen. Abgelehnt wird nur SSB:

    -221,"Settings conflict;No AFC with SSB"

Der Konflikt wirkt in BEIDE Richtungen: Bei aktiver AFC wird auch der
Wechsel auf USB/LSB mit derselben Meldung abgelehnt; der Empfaenger bleibt
im vorherigen Modus und die AFC bleibt eingeschaltet. Das erklaert, warum
in der IFPAN-Ansicht bei aktiver AFC keine Seitenbandwahl moeglich ist.

WICHTIG: In CW und IQ wird die AFC zwar angenommen, sie WIRKT dort aber
nicht (Phase E: null Rueckzug bei allen getesteten Verstimmungen, in jeder
Bandbreite). `FREQ:AFC?` liefert dort also 1, ohne dass geregelt wird -
eine Zustandsanzeige, die nicht der Wirkung entspricht. Wirksam ist die
AFC ausschliesslich in FM, AM und PULSe.

### Fangbereich

Gemessen als Ruecklauf nach sprunghafter Verstimmung, 4 s Einschwingzeit,
Kriterium: mindestens 50 Prozent des Sprungs in Richtung Traeger
zurueckgeregelt, und zwar durchgehend fuer alle kleineren Betraege in
beiden Richtungen (monotone Grenze, siehe T-512).

    Bandbreite 6 kHz    FM/AM/PULSe   +-5250 Hz    (0,875 x BW)
    Bandbreite 15 kHz   FM/AM         +-11250 Hz   (0,75 x BW)
    Bandbreite 15 kHz   PULSe         +-15000 Hz   (1,0 x BW)
    Bandbreite 50 kHz   nicht monoton eingegrenzt

Der Fangbereich skaliert also mit der ZF-Bandbreite; die Handbuchangabe
"entspricht etwa der ZF-Bandbreite" trifft zu, wenn man sie als
Gesamtfenster liest. Als belastbare Regel fuer die Anwendung gilt
+-0,75 x Bandbreite.

Jenseits des monotonen Bereichs ist das Verhalten ausgefranst: Einzelne
groessere Verstimmungen rasten noch ein, benachbarte kleinere nicht.
Am Rand kann die AFC auch VOM Traeger WEGLAUFEN - bei 6 kHz und -6000 Hz
landete das Geraet in allen drei Modi reproduzierbar rund 3200 Hz neben
dem Sollwert (bei 124,9922 MHz statt 124,9955 MHz), also weiter entfernt
als vor der Regelung. Fuer die Anwendung ist das der unguenstigste Fall:
die Frequenz aendert sich, ohne dass der Traeger gefunden wurde.

Die Nachfuehrung arbeitet in 100-Hz-Schritten - alle Einrastfrequenzen
enden auf volle Hundert. Unterhalb von 100 Hz Restablage regelt die AFC
nicht aus.

### Ablage-Sensorfunktion (FREQuency:OFFSet)

Aktivierung:

    SENSe:FUNCtion:CONCurrent ON
    SENSe:FUNCtion "VOLTage:AC","FREQuency:OFFSet"
    SENSe:DATA?   ->   23.4, -2500      (Pegel dBuV, Ablage Hz)

Ohne `CONCurrent ON` erzeugt die Zweierliste `-108,"Parameter not allowed"`.

Vorzeichenkonvention (gemessen, im Handbuch nicht angegeben): Die Ablage
ist SIGNAL MINUS EMPFAENGER. Hoeher abstimmen verringert den Wert. Die
Regression ueber neun Verstimmungsschritte liefert Steigungen von -0,95
bis -0,99 bei r besser als -0,998.

Verfuegbar ist die Messung nur in FM, AM und PULSe - also in genau der
Modusmenge, in der auch die AFC wirkt. In CW, USB, LSB und IQ liefert
`SENSe:DATA?` konstant 0,0. Das ist syntaktisch ein gueltiger Wert, aber
KEINE Messung; eine konstante Null ueber alle Verstimmungen ist als
Fehlanzeige zu werten (siehe `ist_fehlanzeige()` in tools/afc_probe.py).

Der Anzeigebereich betraegt laut Annex C +-1/2 Bandbreite; ausserhalb
saettigt der Wert. Messungen muessen innerhalb dieses Fensters bleiben.

### Folgerungen fuer MoniScope

- Ein am Frontpanel aktiviertes AFC verstellt die Empfangsfrequenz
  waehrend des Betriebs. Die zuletzt GESETZTE Frequenz ist dann nicht die
  tatsaechliche; `SENSe:FREQuency:CW?` muss als Ist-Wert gefuehrt werden.
- `FREQ:AFC? = 1` bedeutet nicht, dass geregelt wird (CW/IQ). Fuer eine
  Anzeige "AFC aktiv" muss zusaetzlich die Demodulationsart geprueft
  werden.
- Eine Ablageanzeige ist in FM/AM/PULSe moeglich und dort auch ohne AFC
  nutzbar (passive Messung, veraendert die Frequenz nicht).
- Speicherplaetze fuer MSCAN werden bereits mit AFC=0 programmiert
  (`memory_write_command()`); der MSCAN-Pfad ist damit nicht betroffen.