<!-- Status: geplant | Quellenstand: 2026-09-02 | offen: - -->

# Anhang C — Kommandozeilenoptionen

*Geplanter Inhalt: Optionen des Starters und der einzelnen Server, dazu die
Werkzeuge unter `tools/`.*

*Erhoben ist bereits:*

- `start_eb200.py`: `--plain`, `--no-browser`, `--selftest`
- `tools/eye_diagram.py` (ausführlich in Kapitel 11a): `--mode`,
  `--decimate`, `--min-baud`, `--baud`, `--burst`, `--suggest`, `--out`,
  `--json`, `--max-traces`, `--selftest`; Rückgabewert 1 bei Eingabefehlern,
  2 bei nicht möglicher Auswertung
- `tools/wav2sdr.py`: Umsetzung einer IQ-Aufnahme für fremde SDR-Software —
  `--hdsdr`, `--cs16`, `--cf32`, `--sigmf`, dazu `--swap-iq`, `--analyze`,
  `--out`, `--selftest`
