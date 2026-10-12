<!-- Status: Entwurf | Quellenstand: 2026-09-02 | offen: A1, A14 -->

# 11a. Signalanalyse von IQ-Aufnahmen

Die Auswertungen auf :8020, :8040 und :8070 zeigen, **dass** und **wann**
etwas gesendet wurde. Dieses Kapitel geht eine Ebene tiefer: Aus einer
IQ-Aufnahme lassen sich Symbolrate, Hub, Modulationsordnung und
Trägerversatz messen — also die Kennzahlen, mit denen man ein unbekanntes
digitales Signal einordnet.

Dafür liegt unter `tools/` das Werkzeug `eye_diagram.py`. Es ist ein reines
Offline-Werkzeug: keine Verbindung zum Gerät, kein Server, keine Oberfläche.
Es liest eine Datei und schreibt eine Grafik.

Wie eine brauchbare IQ-Aufnahme entsteht, steht in Abschnitt 9.11.

## 11a.1 Was das Werkzeug leistet

Zwei Auswertepfade, gewählt über `--mode`:

| | `fsk` (Vorgabe) | `psk` |
|---|---|---|
| Grundlage | Momentanfrequenz | Phase und Amplitude |
| Liefert | Frequenzoffset, Hub, Symbolrate, Augenöffnung, Jitter, Pegelanzahl | Trägerversatz, Modulationsordnung, Symbolrate, EVM |
| Grafik | Augendiagramm | Konstellation und Phasenhistogramm |
| Ausgabedatei | `<name>.eye.svg` | `<name>.psk.svg` |
| Geeignet für | FSK, RTTY, POCSAG, alles Frequenzumgetastete | PSK, HFDL, alles Phasenumgetastete |

Die Pegelanzahl im FSK-Pfad ist zugleich die Antwort auf die Frage, ob eine
zwei- oder vierstufige Umtastung vorliegt: zwei Histogrammspitzen bedeuten
FSK2, vier bedeuten FSK4.

Die Grafik ist reines SVG ohne externe Verweise — sie lässt sich in jedem
Browser öffnen und unverändert in einen Bericht übernehmen.

## 11a.2 Aufruf

```
python tools/eye_diagram.py <aufnahme.wav> [Optionen]
```

| Option | Bedeutung |
|---|---|
| `--mode fsk\|psk` | Auswertepfad, Vorgabe `fsk` |
| `--decimate N` | Dezimation um den ganzzahligen Faktor N vor der Auswertung, Vorgabe 1 |
| `--min-baud` | angenommene untere Baudratengrenze, Vorgabe 20 |
| `--baud` | Symbolrate vorgeben statt schätzen |
| `--burst N` | den N-ten gefundenen Burst auswerten (ab 0), sonst den längsten |
| `--suggest` | nur Abtastrate, Burstübersicht, grobes Frequenzhistogramm und einen Vorschlag für `--decimate` ausgeben, ohne auszuwerten |
| `--out` | Zielpfad der Grafik |
| `--json` | Kennzahlen zusätzlich als JSON schreiben (nur im FSK-Pfad) |
| `--max-traces` | Zahl der Spuren im Augendiagramm, Vorgabe 400 |
| `--selftest` | Selbsttest ohne Aufnahme und ohne Gerät |

Der Rückgabewert unterscheidet zwei Fehlerarten: **1** steht für ein Problem
mit der Eingabe — Datei nicht gefunden, kein lesbares WAV, unsinniger
Parameter. **2** steht für eine Auswertung, die nicht möglich war, obwohl die
Datei in Ordnung ist; das ist der Fall aus Abschnitt 11a.6 und kein Fehler des
Werkzeugs.

Das Werkzeug liest die Begleitdatei der Aufnahme mit. Steht dort
`is_iq: false`, bricht es ab. Fehlt die Begleitdatei ganz, gibt es nur eine
Warnung aus und nimmt die Abtastrate aus dem WAV-Kopf.

> **Hinweis:** `eye_diagram.py` benötigt numpy. Das ist keine zusätzliche
> Voraussetzung — numpy ist ohnehin Pflicht (Kapitel 4).

## 11a.3 Grenzen, die aus dem Übertragungsweg kommen

Drei Grenzen sind fest und lassen sich durch keine Option umgehen.

**Bandbreite.** Der IQ-Strom läuft über den Audiokanal des Geräts mit 32 kHz
komplexer Abtastung. Abtastrate und ZF-Bandbreite sind dabei starr
gekoppelt — 32 kHz entsprechen 15 kHz ZF-Bandbreite. Breitere Signale sind
über LAN nicht übertragbar. Was hineinpasst und was nicht:

| Betriebsart | Bandbreite | passt |
|---|---|---|
| RTTY, POCSAG | bis 9,5 kHz | ja |
| DMR | 12,5 kHz | ja |
| TETRA | 25 kHz | **nein** |

**Symbolrate nach oben.** Das Werkzeug verlangt mindestens vier Abtastwerte
je Symbol. Bei 32 kHz sind damit etwa 8 kBd die Obergrenze.

**Symbolrate nach unten.** Hier hilft `--decimate`. Ein RTTY-Signal mit 50 Bd
liefert bei 32 kHz 640 Abtastwerte je Symbol — deutlich mehr als nötig, und
die beiden Frequenzlagen liegen im Histogramm so eng beieinander, dass sie
nicht mehr sauber getrennt werden. Mit `--decimate 16` bleiben eine effektive
Abtastrate von 2 kHz, 40 Abtastwerte je Symbol und eine Histogrammauflösung
von 5 Hz. `--suggest` rechnet einen Vorschlag für den Faktor aus, wendet ihn
aber nie von selbst an — die Entscheidung bleibt bei Ihnen.

> **Achtung:** Im PSK-Pfad **nicht** dezimieren. Die Symbolrate wird dort aus
> einer Spektrallinie in der Hüllkurve gewonnen, die von den Signalflanken
> erzeugt wird; der Anti-Aliasing-Tiefpass der Dezimation entfernt genau
> diese Flanken. An der HFDL-Referenzaufnahme brach das Werkzeug mit
> `--decimate 2` mit der Meldung „Huellkurve zu flach" ab, während es ohne
> Dezimation 1800,09 Bd lieferte. Der Abbruch war richtig — aber die Ursache
> lag in der Option, nicht im Signal.

## 11a.4 Wie genau ist das?

Der wichtigste Abschnitt dieses Kapitels, weil die Antwort davon abhängt,
**was** gesendet wurde.

**Symbolrate bei Zufallsfolgen** — also bei Nutzdaten — streut um etwa
±2,5 % (eine Standardabweichung), gemessen wurden bis zu 5,53 % Abweichung.
Diese Streuung ist keine Schwäche der Messung, die man mit besseren
Aufnahmen beheben könnte: Sie bleibt bei einem Rauschabstand von 200 dB
bestehen und wird auch bei Bursts von 3200 statt 200 Symbolen nicht kleiner.
Die Ursache liegt im Signal selbst — die ausgewertete Autokorrelationsflanke
hängt von der Lauflängenstatistik der konkret gesendeten Bitfolge ab.

**Die Symbolrate im Nutzdatenfall auf ein Prozent genau zu lesen, ist also
unzulässig.** Das Werkzeug sagt das selbst und gibt die Zeile „Symbolrate aus
Zufallsfolge geschaetzt, typische Unsicherheit ca. 3 %" aus.

**Symbolrate bei Wechselfolgen** — Präambeln, RY-Schleifen, Testsendungen —
ist dagegen genauer als ein Prozent; gemessen wurden 0,0065 %. Das Werkzeug
erkennt diesen Fall selbsttätig und meldet ihn.

> **Hinweis:** Wenn Sie eine genaue Symbolrate brauchen, werten Sie gezielt
> einen Burst mit Präambel oder Testschleife aus (`--burst`) statt des
> längsten Nutzdatenabschnitts. Das ist der Unterschied zwischen drei Prozent
> und drei Promille.

Die übrigen Kennzahlen sind unkritischer. An den Referenzaufnahmen lag der
Hub im FSK-Pfad 1,6 % neben dem Sollwert, die Trägerfrequenz im PSK-Pfad
20 Hz neben dem Sollwert bei 10 MHz — also 2 ppm.

## 11a.5 Referenzmessungen

Zwei Aufnahmen an bekannten Sendern, an denen sich das Werkzeug nachvollziehen
lässt. Beide vom 1. September 2026.

| | DWD Pinneberg DDK2 | HFDL Shannon |
|---|---|---|
| Frequenz | 4583 kHz | 10082 kHz (Träger 10082,440 kHz) |
| Betriebsart | RTTY, FSK2 | 2/4/8-PSK |
| Soll | 50 Bd, ±225 Hz Hub | 1800 Bd |
| Aufruf | `--decimate 16 --min-baud 20` | `--mode psk` |
| Gemessen | Hub 221,4 Hz, 48,26 Bd | Träger 10082,420 kHz, 1800,09 Bd |
| Abweichung | Hub 1,6 %, Symbolrate im Zufallsfolgen-Fall | 20 Hz (2 ppm), Symbolrate 0,005 % |

Die 48,26 Bd gegen 50 Bd sind kein Messfehler, sondern genau die in
Abschnitt 11a.4 beschriebene Streuung: Ausgewertet wurde der Textteil einer
Wettermeldung, also eine Zufallsfolge.

Der HFDL-Träger liegt nicht auf der genannten Kanalfrequenz. Die Bodenstation
sendet einen Unterträger 1440 Hz über der nominellen Frequenz von 10081 kHz;
die 10082,440 kHz sind der Sollwert, gegen den zu messen ist.

## 11a.6 Lesebeispiel: wenn die Aufnahme nicht passt

Eine Aufnahme auf 162,475 MHz, für POCSAG gehalten, brach mit
„Autokorrelationsflanke zu kurz, Symbolrate nicht bestimmbar" ab. Der Abbruch
war korrektes Verhalten, und die Ausgabe sagt auch, warum:

- Die **äußersten Histogrammspitzen** lagen bei ±7,08 kHz, bei einer
  Filterhalbbreite von 7,5 kHz (ZF-Bandbreite 15 kHz). Die Spitzen liegen
  also praktisch auf dem Rand des Filters — ein sicheres Zeichen dafür, dass
  das Signal breiter ist als das Fenster und beschnitten wurde. Bei einem
  Signal, das ins Filter passt, liegen sie deutlich innerhalb.
- Die **Autokorrelation** lag schon bei Verzögerung 1 unter 0,31. Bei einem
  getasteten Signal mit endlicher Symboldauer fällt sie langsam ab — es gibt
  eine Flanke, an die sich eine Gerade legen lässt. Fällt sie sofort, ist die
  Momentanfrequenz von Symbol zu Symbol unkorreliert, und es liegt keine
  Umtastung dieser Rate vor.

Beides zusammen heißt: Die Aufnahme war für die gestellte Frage ungeeignet.
Es war keine 1200-Bd-FSK, und mit 15 kHz Bandbreite wäre sie ohnehin nicht
vollständig zu erfassen gewesen. Die richtige Reaktion ist nicht, an den
Optionen zu drehen, sondern die Aufnahme zu verwerfen — oder, wenn das Signal
schmal genug ist, mit passender Bandbreite und `MGC` neu aufzunehmen.
