# EB200 – Testpunkte (DIAGnostic-Subsystem)

Referenz für die Selbstdiagnose-Funktion (siehe WS1, Phase D: Selbstdiagnose des
Empfangspfads). Enthält die Modulzuordnung, die einzelnen Testpunkte je Baugruppe
mit ihrer Bedeutung, sowie eine verifizierte Baseline-Messung des konkreten Geräts
(OE3GAS, EB200 S/N 101.459) als Referenz für spätere Abweichungserkennung.

---

## 1. SCPI-Zugriff

```
DIAGnostic:MONitor? <module>          ' alle Testpunkte eines Moduls (oder ALL)
DIAGnostic[:MEASure]:TPOint<n>? <module>   ' einzelner Testpunkt (n = 1..8)
```

Ausgabeformat (ASCII): `<Modul> <Testpunktname> <Wert in mV> ( <Min> / <Max> )`
Zustandssymbole: „ “ = OK, „^“ = über oberer Grenze, „v“ = unter unterer Grenze.
Fehlen Min/Max, ist der Testpunkt im aktuellen Betriebsmodus nicht relevant.

## 2. Module

| Kürzel | Baugruppe |
|---|---|
| **P1** | Prozessor |
| **V1** | Preselektor (Vorselektion) |
| **S1** | Standard-Frontend 1 |
| **S2** | Standard-Frontend 2 |
| **Z1** | ZF-Sektion (IF Section) |
| **DC** | DC/DC-Wandler |
| **SU** | IF-Panorama-Einheit (Option EB200SU) |

## 3. Testpunkte je Modul (Bezeichner verifiziert am realen Gerät)

### Z1 – ZF-Sektion

| Testpunkt | Bedeutung |
|---|---|
| `TEXREF` | Regelspannung externe Referenz |
| `TLEVREF` | Pegel interne Referenz |
| `TUREF` | Betriebsspannung Referenzquarz |
| `LDET` | PLL für Taktgenerierung |
| `T16.384MHZ` | Pegel AD-Wandler-Takt |
| `TLOG` | ZF-Pegel für Regelung |
| `TINAMP` | Arbeitspunkt Eingangsverstärker |
| `W_DOG` | Watchdog DSP |

### SU – IF-Panorama-Einheit

| Testpunkt | Bedeutung |
|---|---|
| `SUIAMP` | Betriebsspannung AD-Wandler |
| `SUVAD` | AD-Wandler-Spannung |
| `SUPLL` | PLL für Taktgenerierung |
| `SUCLOCK` | Pegel AD-Wandler-Takt |

### S1 – Frontend 1

| Testpunkt | Bedeutung |
|---|---|
| `ST_384` | Pegeldetektor 384 MHz (Basiswert) |
| `TEST_SS` | Pegeldetektor 384 MHz (Testsignal) |
| `SWP_VCO_ST` | Abstimmspannung Sweep-VCO |
| `1ST_LO_ST` | Abstimmspannung VCO 1–3 |
| `ST_LO` | Mischerpegel Sweep-Synthesizer |
| `LO_LEVEL` | Pegel 1. Lokaloszillator |
| `TESTIF1` | Arbeitspunkt 1. ZF-Verstärker |
| `TEMP` | Temperatur Frontend 1 |

### S2 – Frontend 2

| Testpunkt | Bedeutung |
|---|---|
| `TP_2ND_IF` | Arbeitspunkt 2. ZF-Verstärker |
| `TP_3RD_IF` | Arbeitspunkt 3. ZF-Verstärker |
| `TP_SAM_LEV` | Pegel am Mischer (Sampling) |
| `TP_2NDLOLEV` | Pegel 2. Lokaloszillator |
| `TP_3RDLOLEV` | Pegel 3. Lokaloszillator |
| `TP_2NDLOPLL` | 2. PLL eingerastet |
| `TP_VXO_PLL` | Regelspannung VXO-Referenz |
| `TP_EXT_REF` | Pegel externe Referenz |

### P1 – Prozessor

| Testpunkt | Bedeutung |
|---|---|
| `LI-BATTERY` | Spannung der Lithium-Backup-Batterie (CMOS-RAM-Parametererhalt) |

### DC – DC/DC-Wandler

| Testpunkt | Bedeutung |
|---|---|
| `T-5V` | -5-V-Schiene |
| `T+3.3V` | +3,3-V-Schiene |
| `T+5VD` | +5 V digital |
| `T+5VSTDFE` | +5 V Standard-Frontend |
| `T+5VA` | +5 V analog |
| `T+25V` | +25-V-Schiene |

### V1 – Preselektor

| Testpunkt | Bedeutung |
|---|---|
| `TGAIN` | Strom im Verstärkungsbereich |
| `TTUNE` | Abstimmspannung Filter |
| `TREF128` | Pegel REF128 |
| `TCOMB64` | Pegel für SRD-Steuerung |
| `TCOMB8` | Mittelwert 8-MHz-Puls |
| `T5VINT` | interne 5-V-Versorgung |

---

## 4. Verifizierte Baseline-Messung

**Gerät:** R&S EB200, S/N 101.459, Betreiber OE3GAS
**Firmware:** MAIN CPU V02.74
**Status:** alle Testpunkte innerhalb Toleranz (kein `v`/`^`-Symbol im Live-Output)
**Quelle:** `DIAGnostic:MONitor? ALL` (SCPI, live abgefragt)

```
Z1      TEXREF            82
Z1      TLEVREF         1247   (    900 /   1700 )
Z1      TUREF           2199   (   2100 /   2350 )
Z1      LDET            2165   (   1600 /   2500 )
Z1      T16.384MHZ      1352   (   1000 /   1600 )
Z1      TLOG             141   (      0 /   2000 )
Z1      TINAMP          2109   (   1700 /   2300 )
Z1      W_DOG           2312   (   2000 /   2500 )
SU      SUIAMP          1960   (   1500 /   2300 )
SU      SUVAD           2121   (   1900 /   2300 )
SU      SUPLL           2221   (   1700 /   2500 )
SU      SUCLOCK         1351   (    900 /   1800 )
S1      ST_384           193   (     50 /   4096 )
S1      TEST_SS         3544   (   1900 /   4096 )
S1      SWP_VCO_ST       729   (    400 /   1800 )
S1      1ST_LO_ST        493   (    280 /    680 )
S1      ST_LO            461   (    100 /   4096 )
S1      LO_LEVEL          85   (     23 /   4096 )
S1      TESTIF1         3248   (   2678 /   3623 )
S1      TEMP            3160   (   2530 /   3530 )
S2      TP_2ND_IF       2603   (   2300 /   2800 )
S2      TP_3RD_IF       2399   (   2120 /   2600 )
S2      TP_SAM_LEV      2851   (    200 /   4080 )
S2      TP_2NDLOLEV     1810   (    700 /   3200 )
S2      TP_3RDLOLEV     1030   (    300 /   1400 )
S2      TP_2NDLOPLL     2034   (   1000 /   3000 )
S2      TP_VXO_PLL      2387   (    200 /   3400 )
S2      TP_EXT_REF      3164   (   2000 /   4000 )
P1      LI-BATTERY      3000   (   2250 /   3000 )
DC      T-5V           -5280   (  -5459 /  -5141 )
DC      T+3.3V          3280   (   3201 /   3399 )
DC      T+5VD           5097   (   4998 /   5202 )
DC      T+5VSTDFE       5285   (   5200 /   5500 )
DC      T+5VA           5309   (   5141 /   5459 )
DC      T+25V          25146   (  24000 /  26000 )
V1      TGAIN            502   (    380 /    650 )
V1      TTUNE            134   (      0 /   2500 )
V1      TREF128            3
V1      TCOMB64            0
V1      TCOMB8          2260
V1      T5VINT          1992   (   1950 /   2050 )
```

**Anmerkungen zur Baseline:**
- `P1 LI-BATTERY` liegt exakt auf der oberen Grenze (3000 von 2250–3000) – das ist
  bei einer frischen/vollen Lithium-Backup-Batterie (CR2477) normal und **kein**
  Warnsignal (die Fehlermeldung „Backup LI-Battery low" bezieht sich auf zu
  *niedrige* Werte).
- `V1 TREF128`, `V1 TCOMB64`, `V1 TCOMB8` sowie `Z1 TEXREF` werden ohne Min/Max
  ausgegeben – laut Handbuch normal, wenn die Grenzwerte im aktuellen
  Betriebsmodus nicht relevant sind (z. B. interne statt externe Referenz aktiv).

## 5. Verwendung für den Gesundheits-Snapshot (WS1)

Vorschlag für die Nutzung im Skript:
- Baseline-Werte (Abschnitt 4) als Referenz-Datensatz einlesen.
- Bei jedem Snapshot `DIAGnostic:MONitor? ALL` abfragen und parsen.
- Je Testpunkt: Zustandssymbol auswerten (OK/zu hoch/zu niedrig) und zusätzlich
  optional die Abweichung vom Baseline-Wert berechnen (Trendbeobachtung, z. B.
  Alterung von Bauteilen, Temperaturdrift), auch wenn der Wert noch innerhalb der
  Toleranz liegt.
- Ergebnis in der WS1-Diagnose-Historie ablegen (siehe WS1 Phase D, Selbstdiagnose)
  – ergänzend zum einfachen Kurztest (`TEST? SHORt`), der nur ein Gesamtergebnis
  liefert, aber keine Einzeltestpunkte.