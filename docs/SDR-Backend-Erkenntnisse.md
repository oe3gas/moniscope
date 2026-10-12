# SDR-Backend — Entscheidung und Windows-Erkenntnisse

*Stand: 2026-08. Kontext: Anbindung von SDRs an den SDR-Wasserfall
(WS3, `sdr_waterfall.py`, Port 8030).*

## Entscheidung

Die SDRs werden über ihre **nativen Vendor-CLIs als Subprozess** angebunden
(`NativeCliSource`), **nicht** über die SoapySDR-Python-Bindings. Der Dienst
liest rohes IQ aus stdout des CLI und rechnet die FFT mit numpy.

**Vorteile:** eine Umgebung (`.venv` + numpy, kein radioconda zur Laufzeit),
keine DLL-Konflikte, und es nutzt den auf der Maschine nachweislich
funktionierenden Pfad.

## Warum nicht SoapySDR-Python?

Ursprünglich war die SoapySDR-Klasse (einheitliche Schicht für RSPdx, Airspy,
HackRF) geplant. Auf der Windows-Zielmaschine ließ sich aber **kein** USB-SDR
über die Python-Bindings öffnen. Diagnosekette:

- `SoapySDR.Device.enumerate()` **findet** die Geräte inkl. Seriennummer.
- `SoapySDR.Device(dict(driver=...))` (make) scheitert mit
  `RuntimeError: SoapySDR::Device::make() no match` — für Airspy und HackRF
  identisch.
- C++ `SoapySDRUtil --make/--find/--probe` öffnet dieselben Geräte **sauber**.

Ausgeschlossen wurde nacheinander: gerätespezifisch (beide betroffen); make-Args
(auch volle `enumerate`-Args scheitern); Belegung/Zombie-Prozess (auch frisch);
DLL-Verwechslung (auch nach Vorladen der radioconda-DLLs per hartem Pfad und
nach sauberem, nur-radioconda-PATH); Treiberbindung (die C++-Ebene öffnet ja mit
genau dem aktuellen Treiber — Zadig damit ebenfalls entkräftet).

**Fazit:** Die Python-SoapySDR-Bindings dieser radioconda-Installation sind auf
dieser Maschine im make-Pfad defekt. Der Umbau auf native CLIs umgeht das
vollständig.

## IQ-Formate der nativen Tools

- **hackrf_transfer** — `-r -` schreibt nach stdout; Format: **interleaved
  signed 8-bit** (I und Q je 1 Byte), `complex = (I/127) + j·(Q/127)`. Frequenz
  `-f` in **Hz**, Rate `-s` in **Hz** (2–20 MHz). DC-Spike in der Mitte (wird im
  Wasserfall durch den Nachbar-Bin ersetzt).
- **airspy_rx** — `-r -` nach stdout; `-t 0` = **FLOAT32-IQ** (~[-1,1]). Achtung:
  Frequenz `-f` in **MHz** (nicht Hz!), Rate über `-a`.

Die Zuordnung Byteformat → complex steuert das Feld `iq_dtype` je Gerät in
`SDR_DEVICES`.

## Troubleshooting-Landkarte: SoapySDR unter Windows

Falls SoapySDR-Python doch noch einmal versucht wird — die gesammelten
Windows-Fallstricke:

- **Mehrere `SoapySDR.dll`/`libusb-1.0.dll` auf dem PATH** (PothosSDR,
  radioconda, `C:\HackRF`, Hamlib) → „two SoapySDR"-Konflikt, `make() no match`.
  Prüfen mit `where.exe SoapySDR.dll`. Abhilfe: fremde PATH-Einträge entfernen
  oder die korrekten DLLs im Prozess per `os.add_dll_directory` +
  `ctypes.CDLL(<voller Pfad>)` vorladen (löste hier das grundlegende
  make-Problem allerdings nicht).
- **conda-Python durchsucht `%PATH%` nicht** für abhängige DLLs (Python 3.8+):
  `os.add_dll_directory(...)` bzw. `set CONDA_DLL_SEARCH_MODIFICATION_ENABLE=1`.
- **SDRplay RSPdx** braucht den proprietären **SDRplay-API-Dienst v3.15+**; das
  in PothosSDR gebündelte sdrplay-Modul (v2, 0.3.0) unterstützt den RSPdx nicht.
  Ein passendes **SoapySDRPlay3 (v3)** gibt es für Windows nicht als fertiges
  conda-Paket (Quell-Build nötig).
- **Airspy/HackRF** benötigen für SoapySDR die **WinUSB/libusb-Bindung (Zadig)**;
  der native Airspy-Treiber (z. B. für SDR#) schließt das gegenseitig aus.

## Weitere Erkenntnisse aus der Integration

- **enumerate → make-Race (Airspy):** Ein `enumerate()` unmittelbar vor `make()`
  im selben Prozess ließ das Öffnen unter Windows/libusb mit „no match"
  scheitern. Die redundante Verfügbarkeits-Vorprüfung in `/api/sdr/start` wurde
  entfernt (das Dropdown prüft die Verfügbarkeit ohnehin).
- **CORS:** Die :8000-Seite ruft `/api/sdr/*` auf dem :8030-Dienst per `fetch`
  auf (Cross-Origin). Der SDR-Dienst muss `Access-Control-Allow-Origin` senden,
  sonst verwirft der Browser die Antwort.
- **IFPAN-Ansicht bei externem Auslöser:** Wird der Zoom aus dem SDR-Wasserfall
  ausgelöst, muss die :8000-Seite bei eintreffenden IFPAN-Frames selbst in die
  Zoom-Ansicht umschalten — sonst bleibt sie im (stehenden) D-SCAN.
