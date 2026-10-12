# MoniScope

Browser-based monitoring suite for the **Rohde & Schwarz EB200** receiver.

The EB200 is an excellent receiver with a tiny display and no memory worth
mentioning. Everything it measures exists at the moment of measurement and is
gone afterwards — unless a computer is listening. MoniScope is that computer:
nine small HTTP servers that give the device a waterfall display, recording,
scheduled jobs, signal analysis and a frequency catalogue, all operated from a
web browser.

Written for **every EB200 owner**, not just one station. No web framework, no
build chain, no JavaScript dependencies — CPython, numpy and the standard
library.

> **Status:** first public release. Developed and tested on Windows against
> firmware V02.74. Linux operation (including Raspberry Pi) works in practice
> but is not yet documented.

---

## Requirements

### Receiver

The **EB200R4 LAN option is mandatory** — the entire suite runs over Ethernet,
both control and measurement data. Which further options your device has
decides which parts work:

| Option | Enables | Without it |
|---|---|---|
| **EB200R4** (LAN) | everything | suite unusable |
| **EB200DS** (DIGI-Scan) | D-SCAN, sessions, wide overview | no overview display; IFPAN and M-SCAN still work |
| **EB200SU** (IF Panoramic Unit) | IFPAN zoom, spectrum recording | no zoom, no `.specbin` recordings; audio and M-SCAN still work |

M-SCAN uses the base unit's memory locations and needs no extra option.

### Network

Receiver and PC belong in the same subnet. The device address is set in
`device.json` (see Configuration) or with `--eb200-host`.

| Direction | Protocol | Port | Purpose |
|---|---|---|---|
| PC → device | TCP | 5555 | SCPI control |
| device → PC | UDP | 18102 | display stream (waterfall, listening) |
| device → PC | UDP | 18103 | spectrum recording |
| device → PC | UDP | 18104 | audio of a running job |
| device → PC | UDP | 18105 | M-SCAN hit reports |

Each stream gets its own port on purpose: only then does missing-datagram
detection stay reliable.

> ⚠️ **The most common setup failure:** the Windows firewall must allow
> **incoming UDP** for `python.exe`. Without it the interface looks normal but
> the waterfall stays empty — the device sends, the PC discards.

Two device limits worth knowing: at most **10** simultaneous TCP control
connections, and at most **4** UDP destinations.

### PC

- **Python 3.14** (tested)
- **numpy** — mandatory. Port 8000 will not start without it.
- A current browser. The pages use Server-Sent Events and Canvas, nothing else.
- Disk space. Recordings grow fast; put long runs on their own drive via
  `storage_roots.json`.

---

## Installation

```bash
git clone https://github.com/oe3gas/moniscope.git
cd moniscope
pip install -r requirements.txt
```

On Raspberry Pi OS prefer the distribution package:
`sudo apt install python3-numpy` (then create the venv with
`--system-site-packages`).

## Running

```bash
python start_eb200.py
```

or `start_eb200.bat` on Windows. One command starts all servers as independent
child processes and opens http://localhost:8000/ in your browser. On a Windows
terminal the starter draws a status board showing each server's state, port
check, PID and the last error block.

Useful flags:

| Flag | Effect |
|---|---|
| `--data-dir PATH` | writable data root (recordings, sessions, configuration) |
| `--eb200-host ADDRESS` | device address for this run, overrides `device.json` |
| `--no-browser` | do not open the browser |
| `--plain` | plain log output instead of the status board |
| `--selftest` | run the self-test and exit |

### The servers

| Port | Program | Purpose |
|---|---|---|
| 8000 | `waterfall.py` | live D-SCAN waterfall, listening, recording |
| 8010 | `eb200_core/block_admin.py` | frequency blocks and catalogue administration |
| 8020 | `analyze_sessions.py` | session analysis |
| 8030 | `sdr_waterfall.py` | SDR waterfall (optional, needs HackRF/Airspy) |
| 8040 | `analyze_spectrum.py` | spectrum analysis |
| 8050 | `manage_jobs.py` | job and recording management |
| 8060 | `analyze_mscan.py` | M-SCAN results |
| 8070 | `analyze_audio.py` | audio analysis and classification |
| 8090 | `preparation_worker.py` | background preparation worker (no UI; status shown in :8050) |

Ports 8060 and 8070 are reached by deep link from :8050, not from a menu.

---

## Configuration

Program and data are separate. Everything writable lives under the data root
(`--data-dir`, default: the program directory). All configuration files are
optional — the suite starts without them.

| File | Purpose | Template |
|---|---|---|
| `device.json` | receiver address, SCPI port, local IP | `device.example.json` |
| `station.json` | region filter for the frequency catalogue | `station.example.json` |
| `catalog_view.json` | which catalogue files are displayed | `catalog_view.example.json` |
| `storage_roots.json` | additional storage locations for recordings | — |

Copy a template, drop the `.example`, edit. The device address can also be set
in the browser on the :8000 status page; it takes effect after a restart.

---

## Frequency catalogues

`known_freq/` ships reference catalogues as JSON: international amateur bands,
ISM/SRD, satellites, time signals, emergency and safety frequencies, aviation,
DAB Band III — plus **Austria-specific** files (`FNV_AT_2026.json`, the ORS
FM and DVB-T2 transmitter lists, neighbouring-country FM lists for CZ/HU/SK).

The Austrian files are an example of what a national catalogue looks like, not
a requirement. Replace them with your own country's data; the format is
documented in `eb200_core/known_freq.py`, and `python eb200_core/known_freq.py
--praefixe` lists the ID prefix convention. :8010 imports CSV and lets you hide
individual catalogue files from the display.

---

## Documentation

- `docs/EB200_Geraeteprofil.md` — hardware findings: SCPI quirks of firmware
  V02.74, AFC behaviour, M-SCAN datagram structure, device limits. Useful even
  if you never run this software.
- `docs/WS1`–`WS3` — the design specifications of the three workstreams.
- `docs/Glossar-EN.md` — binding UI terminology.
- `docs/Portierung-Linux-Inventur.md` — what is Windows-specific.
- `PLAN.md` — the full task and decision log. Every architectural decision in
  this project, with its reasoning and the measurements behind it.
- `bugs/` — known defects.

Source comments, docstrings and log output are **German**; the user interface
is **English**. A German user manual exists and is planned for a later
release.

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

MoniScope is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE.

Not affiliated with or endorsed by Rohde & Schwarz. "EB200" is used only to
identify the hardware this software talks to.

## Author

Gerhard, **OE3GAS** — Hollabrunn, Austria.

Bug reports and findings from other EB200 stations are very welcome: the suite
has so far been tested against exactly one receiver (S/N 101459, V02.74).
