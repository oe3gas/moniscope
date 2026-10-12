# Portierung Linux/Raspberry Pi - Inventur (T-1501)

## 0. Vorpruefung

| Pruefung | Befehl (gekuerzt) | Soll | Ist |
|---|---|---|---|
| V1 Task-ID frei | `Select-String -Path PLAN.md -Pattern '\bT-15\d\d\b'` | 0 | 0 |
| V2 Meilenstein-Nummer frei | `Select-String -Path PLAN.md -SimpleMatch '## Meilenstein 15'` | 0 | 0 |
| V3 Anker eindeutig | `Select-String -Path PLAN.md -SimpleMatch '## View-/Menübaum (Stand 2026-08, Ziel nach Meilenstein 12)'` | 1 | 1 |
| V4 Zieldatei existiert noch nicht | `Test-Path docs\Portierung-Linux-Inventur.md` | False | False |
| V5 Arbeitsbaum sauber | `git status --porcelain -- PLAN.md docs/` | leer | leer |

Alle fuenf Vorpruefungen bestanden, keine davon fuehrte zum Abbruch.

**Hinweis zur Dateiliste (Abweichung, im Auftrag selbst aufgeloest):** Die im
Auftrag angegebene "Vorlage fuer die Dateiliste" verwendet
`-Include *.py,*.ps1,*.bat` und schliesst nur `.git`, `.venv`, `logs`,
`sessions`, `records`, `jobs` aus. Das widerspricht dem unmittelbar
vorangehenden Fliesstext im selben Auftrag ("Eingeschlossen: alle *.py,
*.ps1, *.bat, *.md ausserhalb der Ausschluesse" und der Ausschlussliste, die
zusaetzlich `handbuch\arbeit\` nennt). Da der Auftrag selbst im Fliesstext
die Alternative eindeutig benennt, wurde nach CLAUDE.md-Regel ("es ist ein
offensichtlicher Tippfehler mit genau einer plausiblen Lesart") die
Dateiliste um `*.md` erweitert und `handbuch\arbeit` in die Ausschlussregel
aufgenommen; dokumentiert hier an erster Stelle. Alle nachfolgenden
Suchlaeufe verwenden diese erweiterte Liste.

**Hinweis zur Umlautschreibung (Abweichung, im Auftrag selbst aufgeloest):**
Der Auftrag verlangt "ASCII-Umlautschreibung wie im uebrigen docs/-Bestand
ueblich". Stichprobe an vorhandenen docs/-Dateien (z. B.
`docs/SDR-Backend-Erkenntnisse.md`) zeigt echte UTF-8-Umlaute (ü, ä, ö), keine
ASCII-Ersatzschreibung. Diese Datei folgt daher der tatsaechlichen
docs/-Konvention (echte Umlaute), wie vom Auftrag selbst als maszgebliches
Kriterium genannt ("wie im uebrigen docs/-Bestand ueblich").

## 1. Rahmen

- Datum: 2026-09-02
- Commit-Hash (git rev-parse --short HEAD, vor dieser Inventur): `cd1166c`
- Anzahl durchsuchter Dateien: 89
- Ausschlussliste (woertlich aus dem Auftrag): `EB200_sources.txt`, `.git\`,
  `.venv\`, `logs\`, `sessions\`, `records\`, `jobs\`, `handbuch\arbeit\`
- Eingeschlossen: `*.py`, `*.ps1`, `*.bat`, `*.md` ausserhalb der
  Ausschluesse (siehe Hinweis in Abschnitt 0)
- Keine weitere Einschraenkung des Suchraums vorgenommen: Verzeichnisse wie
  `.agents\` und `.claude\` sind nicht in der Ausschlussliste genannt und
  daher im Suchraum enthalten (z. B. `.agents\skills\...\SKILL.md`,
  `.claude\commands\status.md`).

## 2. Plattformweichen und Windows-APIs (S1-S2)

### S1 - Plattformweichen (16 Treffer)

```
eb200_core\mscan_view.py:60:cmp_job = job_dir_real.lower() if os.name == "nt" else job_dir_real
eb200_core\mscan_view.py:61:cmp_root = repo_root_real.lower() if os.name == "nt" else repo_root_real
eb200_core\mscan_view.py:152:job_dir_cmp = job_dir.lower() if os.name == "nt" else job_dir
eb200_core\mscan_view.py:155:root_cmp = root.lower() if os.name == "nt" else root
eb200_core\mscan_view.py:166:path_cmp = path.lower() if os.name == "nt" else path
eb200_core\path_sandbox.py:116:cmp_resolved = resolved.lower() if os.name == "nt" else resolved
eb200_core\path_sandbox.py:119:cmp_root = root.lower() if os.name == "nt" else root
eb200_core\path_sandbox.py:153:cmp_root = root_real.lower() if os.name == "nt" else root_real
eb200_core\path_sandbox.py:154:cmp_path = path.lower() if os.name == "nt" else path
eb200_core\path_sandbox.py:207:if os.name == "nt":
eb200_core\path_sandbox.py:258:if os.name == "nt":
analyze_audio.py:1614:for bad in ("../../x.wav", "..\\..\\x.wav", "C:\\Windows\\x.wav" if os.name == "nt" else "/etc/x.wav"):
start_eb200.py:237:if os.name != "nt":
start_eb200.py:309:if os.name != "nt":
start_eb200.py:700:if os.name != "nt":
start_eb200.py:1146:if os.name == "nt":
```

### S2 - Windows-Module und -APIs (12 Treffer)

```
docs\WS3 - Wasserfall.md:782:3. **Lock-Datei statt `msvcrt`/`fcntl`.** Prozessübergreifende Sperre per
PLAN.md:387:erhoeht. Plain-Modus: keine Tastaturabfrage (msvcrt.kbhit() braucht
start_eb200.py:74:geschrieben. KEINE Tastaturabfrage in diesem Modus (msvcrt.kbhit() braucht
start_eb200.py:146:import msvcrt  # nur Windows - Tastaturabfrage ohne Enter
start_eb200.py:148:msvcrt = None
start_eb200.py:239:kernel32 = ctypes.windll.kernel32
start_eb200.py:315:kernel32 = ctypes.windll.kernel32
start_eb200.py:668:if msvcrt is None:
start_eb200.py:670:while msvcrt.kbhit():
start_eb200.py:671:ch = msvcrt.getwch()
start_eb200.py:703:kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
start_eb200.py:1149:kernel32 = ctypes.windll.kernel32
```

Bewertung offen (Chat).

## 3. Pfade und Programmdateien (S3-S5, S11)

### S3 - Laufwerksbuchstaben in Literalen (2 Treffer)

```
handbuch\05_installation.md:61:"D:\\EB200_Daten",
analyze_audio.py:1614:for bad in ("../../x.wav", "..\\..\\x.wav", "C:\\Windows\\x.wav" if os.name == "nt" else "/etc/x.wav"):
```

### S4 - Windows-Programmdateien und Skriptendungen im Code (11 Treffer)

```
docs\SDR-Backend-Erkenntnisse.md:57:Prüfen mit `where.exe SoapySDR.dll`. Abhilfe: fremde PATH-Einträge entfernen
docs\WS1 - Aktivitaetsmonitoring.md:208:`EB200_Sources2Text.ps1` neu erzeugt und ins Projekt hochgeladen werden, damit
handbuch\04_voraussetzungen.md:47:> `python.exe` zulassen. Ohne diese Freigabe erscheint die Oberfläche normal,
EB200_Sources2Text.ps1:1:# EB200_Sources2Text.ps1
EB200_Sources2Text.ps1:6:#   .\EB200_Sources2Text.ps1
PLAN.md:352:- [x] T-302 (2026-08-23) start_eb200.py (Server-Start, webbrowser.open) + .bat-Launcher
sdr_waterfall.py:47:- hackrf_transfer.exe (HackRF-Tools) fuer key=hackrf - Pfad in SDR_DEVICES
sdr_waterfall.py:49:- airspy_rx.exe (airspy_tools) fuer key=airspy - Pfad UND -a/-t-Flags in
sdr_waterfall.py:124:cmd=r"C:\HackRF\bin\hackrf_transfer.exe",
sdr_waterfall.py:133:cmd=r"C:\airspy\airspy_rx.exe",
start_eb200.py:120:Alternativ start_eb200.bat per Doppelklick starten (cd ins Repo-Root,
```

### S5 - Backslash-Pfadtrenner in Stringliteralen (1 Treffer)

```
analyze_audio.py:1614:for bad in ("../../x.wav", "..\\..\\x.wav", "C:\\Windows\\x.wav" if os.name == "nt" else "/etc/x.wav"):
```

### S11 - Feste Ablagepfade (77 Treffer gesamt, > 40: nach Datei verdichtet)

```
eb200_core\recording_index.py: 38
sdr_waterfall.py: 8
analyze_audio.py: 8
eb200_core\path_sandbox.py: 4
eb200_core\job_store.py: 4
PLAN.md: 2
waterfall.py: 2
docs\WS3 - Wasserfall.md: 2
analyze_spectrum.py: 1
docs\SDR-Backend-Erkenntnisse.md: 1
manage_jobs.py: 1
analyze_sessions.py: 1
eb200_core\mscan_run.py: 1
eb200_core\job_runner.py: 1
eb200_core\mscan_view.py: 1
analyze_mscan.py: 1
handbuch\05_installation.md: 1
```

Bewertung offen (Chat).

## 4. Prozess- und Shell-Kopplung (S6-S7)

### S6 - Subprocess-Aufrufe und Windows-Flags (16 Treffer)

```
tools\check_js_blocks.py:148:lauf = subprocess.run(
tools\known_freq_abgleich.py:806:proc = subprocess.run(
tools\known_freq_wien_merge.py:538:proc = subprocess.run(
analyze_sessions.py:307:proc = subprocess.run(
analyze_spectrum.py:419:proc = subprocess.run(
sdr_waterfall.py:182:self._proc = subprocess.Popen(
sdr_waterfall.py:184:stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
start_eb200.py:189:proc: Optional[subprocess.Popen] = None
start_eb200.py:763:st.proc = subprocess.Popen(
start_eb200.py:766:stdout=subprocess.PIPE,
start_eb200.py:767:stderr=subprocess.PIPE,
start_eb200.py:812:except subprocess.TimeoutExpired:
start_eb200.py:854:except subprocess.TimeoutExpired:
start_eb200.py:858:except subprocess.TimeoutExpired:
start_eb200.py:1150:dummy = subprocess.Popen(
start_eb200.py:1159:except subprocess.TimeoutExpired:
```

### S7 - Shell-/Desktop-Kopplung (2 Treffer)

```
PLAN.md:352:- [x] T-302 (2026-08-23) start_eb200.py (Server-Start, webbrowser.open) + .bat-Launcher
start_eb200.py:900:webbrowser.open("http://localhost:8000/")
```

Bewertung offen (Chat).

## 5. Kodierung (S8)

### S8 - Kodierung und Codepage (13 Treffer)

```
eb200_core\known_freq.py:55:Konsolenausgaben sind bewusst ASCII (cp1252-Konsole): "dBuV" statt dem
eb200_core\known_freq.py:819:# cp1252-Konsole wuerde print() sonst mit UnicodeEncodeError abbrechen -
eb200_core\mscan_job.py:39:Regeln: nur Python-Standardbibliothek. Konsolenausgaben ASCII (cp1252-
eb200_core\mscan_job.py:755:# cp1252-Konsole: reconfigure vermeidet UnicodeEncodeError bei
eb200_core\sched_health.py:19:Nur Python-Standardbibliothek. Konsolenausgaben ASCII (cp1252-Konsole,
eb200_core\sched_health.py:229:# cp1252-Konsole: reconfigure vermeidet UnicodeEncodeError bei
eb200_core\scpi_verify.py:141:# in cp1252 nicht darstellbar und ließ print() auf der Windows-Konsole mit
eb200_core\scpi_verify.py:144:# cp1252-sicher und bleiben.
tools\known_freq_abgleich.py:74:Konsolenausgaben sind bewusst ASCII/cp1252-sicher (kein Mikro-/Delta-
tools\known_freq_abgleich.py:830:sys.stdout.reconfigure(errors="replace")   # cp1252-Konsole, Muster known_freq.py
tools\known_freq_wien_merge.py:560:sys.stdout.reconfigure(errors="replace")   # cp1252-Konsole, Muster known_freq.py
tools\mscan_probe.py:117:ausgaben sind ASCII (cp1252-Konsole, Muster known_freq.py/mscan_job.py).
start_eb200.py:1190:# cp1252), die weder die Box-Zeichnung des Boards noch von Kindprozessen
```

Bewertung offen (Chat).

## 6. Sockets (S9)

### S9 - Socket-Puffer (16 Treffer)

```
docs\WS3 - Wasserfall.md:481:(`SO_RCVBUF`) nicht vergrößert wird.
docs\WS3 - Wasserfall.md:498:Update-Aktivitätszeiten setzen, `SO_RCVBUF` ≈ 1 MB.
eb200_core\http_base.py:157:s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
tools\mscan_probe.py:167:SO_RCVBUF_BYTES = 1_048_576
tools\mscan_probe.py:388:sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
tools\mscan_probe.py:390:sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_BYTES)
tools\test_opt_header.py:373:sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
stress_connections.py:459:self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
validate_tag_streams.py:77:SO_RCVBUF_BYTES = 1_048_576
validate_tag_streams.py:187:sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
validate_tag_streams.py:189:sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SO_RCVBUF_BYTES)
waterfall.py:1974:sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
waterfall.py:2221:sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
waterfall.py:2829:msc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
waterfall.py:2833:aud_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
waterfall.py:3282:self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
```

Bewertung offen (Chat).

## 7. numpy-Verbreitung (S10)

### S10 - numpy-Nutzung (12 Treffer)

| Datei | Zeile | Text | unter eb200_core/ |
|---|---|---|---|
| eb200_core\signal_detect.py | 35 | `import numpy as np` | ja |
| tools\classify_eval.py | 45 | `import numpy as np` | nein |
| tools\eye_diagram.py | 60 | `import numpy as np` | nein |
| tools\iq_import.py | 40 | `import numpy as np` | nein |
| analyze_audio.py | 1545 | `import numpy as np` | nein |
| analyze_audio.py | 1565 | `import numpy as np` | nein |
| audio_classify.py | 176 | `import numpy as np` | nein |
| audio_spectrum.py | 69 | `import numpy as np` | nein |
| sdr_waterfall.py | 84 | `import numpy as np` | nein |
| session_analyze.py | 62 | `import numpy as np` | nein |
| session_analyze.py | 218 | `from numpy.lib.stride_tricks import sliding_window_view` | nein |
| session_analyze.py | 375 | `from numpy.lib.stride_tricks import sliding_window_view` | nein |

Bewertung offen (Chat).

## 8. Zeitquellen (S12)

### S12 - Zeitquellen ohne NTP-Absicherung (151 Treffer gesamt, nur Zahl je Datei)

```
waterfall.py: 40
eb200_core\recording_index.py: 21
start_eb200.py: 19
eb200_core\session_store.py: 13
tools\mscan_probe.py: 9
eb200_core\mscan_run.py: 8
eb200_core\param_store.py: 6
eb200_core\sched_health.py: 5
manage_jobs.py: 4
eb200_core\job_store.py: 3
sdr_waterfall.py: 2
validate_tag_streams.py: 2
session_analyze.py: 2
stress_connections.py: 2
eb200_core\db.py: 2
eb200_core\dscan_store.py: 2
eb200_core\audio_wav.py: 2
eb200_core\spectrum_store.py: 2
tools\test_opt_header.py: 1
tools\iq_import.py: 1
eb200_core\mscan_job.py: 1
audio_classify.py: 1
analyze_sessions.py: 1
tools\selftest_delete.py: 1
audio_spectrum.py: 1
```

Bewertung offen (Chat).

## 9. Gross-/Kleinschreibung (P-CASE)

Linux-Dateisysteme sind case-sensitiv, NTFS nicht. P1 und P2 werden ohne
automatischen Abgleich nebeneinander gestellt (siehe P3 im Auftrag) - keine
Bewertung, keine Schlussfolgerung durch CC.

### P1 - Dateinamen unter known_freq\ (19 Dateien)

```
Air.json
Amateurfunk_UHF.json
Amateurfunk_VHF.json
DAB_Band3.json
FNV_AT_2026.json
fup_30_88_mhz.json
intl_amateurfunk_baender.json
intl_ism_srd.json
intl_not_sicherheit.json
intl_satelliten.json
intl_zeitzeichen.json
KW_BC.json
OE3GAS.json
ORS_DVBT2_AT.json
ORS_UKW_AT.json
Test_101.json
UKW_BC.json
ukw_cz.json
ukw_hu.json
ukw_sk.json
```

### P2 - .json-Stringliterale im Code (139 Treffer)

```
eb200_core\block_admin.py:3338:with open(os.path.join(tmp, "a.json"), "w", encoding="utf-8") as fh:
eb200_core\block_admin.py:3387:with open(os.path.join(tmp, "b.json"), "w", encoding="utf-8") as fh:
eb200_core\block_admin.py:3413:dup_y1 = {"id": "y1", "label": "Y1", "file": "b.json"}
eb200_core\block_admin.py:3439:"dup": {"id": "air-128.315", "file": "Air.json", "freq_hz": 128_315_000},
eb200_core\job_runner.py:466:index_path = os.path.join(tmp, "jobs", "index.json")
eb200_core\job_runner.py:467:roots_path = os.path.join(tmp, "storage_roots.json")
eb200_core\job_store.py:50:INDEX_PATH_DEFAULT = os.path.join("jobs", "index.json")
eb200_core\job_store.py:57:os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage_roots.json")
eb200_core\job_store.py:136:return os.path.join(job_dir, "job.json")
eb200_core\job_store.py:566:other_files = [n for n in os.listdir(job_dir) if n != "job.json"]
eb200_core\job_store.py:701:index_path = os.path.join(tmp, "jobs", "index.json")
eb200_core\job_store.py:702:roots_path = os.path.join(tmp, "storage_roots.json")
eb200_core\job_store.py:719:assert os.path.isfile(os.path.join(m1["job_dir"], "job.json"))
eb200_core\job_store.py:932:os.remove(os.path.join(m7["job_dir"], "job.json"))
eb200_core\job_store.py:952:with open(os.path.join(m2["job_dir"], "job.json"), encoding="utf-8") as f:
eb200_core\known_freq.py:533:os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "station.json")
eb200_core\known_freq.py:677:_write(os.path.join(tmp, "a.json"), json.dumps({
eb200_core\known_freq.py:694:_write(os.path.join(tmp, "b.json"), json.dumps({
eb200_core\known_freq.py:700:_write(os.path.join(tmp, "c.json"), "{ das ist kein JSON")
eb200_core\known_freq.py:704:kaputt = [f for f in cat.files if f["name"] == "c.json"]
eb200_core\known_freq.py:706:assert any(f["name"] == "a.json" and f["entries_ok"] == 1 for f in cat.files), cat.files
eb200_core\known_freq.py:709:assert dup is not None and dup["file"] == "a.json", dup
eb200_core\known_freq.py:711:assert len(dup_fehler) == 1 and "a.json" in dup_fehler[0] and "b.json" in dup_fehler[0], \
eb200_core\known_freq.py:731:_write(os.path.join(tmp, "a.json"), json.dumps({
eb200_core\known_freq.py:735:_write(os.path.join(tmp, "b.json"), json.dumps({
eb200_core\known_freq.py:739:_write(os.path.join(tmp, "c.json"), json.dumps({
eb200_core\known_freq.py:757:fehlend = os.path.join(tmp, "nicht_da.json")
eb200_core\known_freq.py:760:gueltig = os.path.join(tmp, "gueltig.json")
eb200_core\known_freq.py:764:defekt = os.path.join(tmp, "defekt.json")
eb200_core\label_store.py:112:path = os.path.join(tmp, "labels.json")
eb200_core\label_store.py:136:path2 = os.path.join(tmp, "notes.json")
eb200_core\mscan_job.py:547:with open(os.path.join(catalog_dir, "test.json"), "w", encoding="utf-8") as fh:
eb200_core\mscan_job.py:692:with open(os.path.join(jobs_dir, "ZZZZZZ.json"), "w", encoding="utf-8") as fh:
eb200_core\mscan_run.py:835:"bw_hz": 15000, "squelch_dbuv": None, "service": "x", "file": "f.json", "tags": []},
eb200_core\mscan_run.py:837:"bw_hz": 15000, "squelch_dbuv": 30, "service": "x", "file": "f.json", "tags": []},
eb200_core\mscan_run.py:839:"bw_hz": 6000, "squelch_dbuv": 10, "service": "x", "file": "f.json", "tags": []},
eb200_core\mscan_run.py:1125:index_path = os.path.join(tmp, "jobs", "index.json")
eb200_core\mscan_run.py:1126:roots_path = os.path.join(tmp, "storage_roots.json")
eb200_core\mscan_view.py:186:index_path = os.path.join(tmp, "jobs", "index.json")
eb200_core\mscan_view.py:195:roots_path = os.path.join(tmp, "storage_roots.json")
eb200_core\mscan_view.py:302:for bad_name in ("job.json", "../a.wav", "sub\\a.wav", "A.WAV ", ""):
eb200_core\param_store.py:89:os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "params", "param_store.json")
eb200_core\param_store.py:821:store_path = os.path.join(tmp, "param_store.json")
eb200_core\path_sandbox.py:71:path = storage_roots_path or os.path.join(repo_root, "storage_roots.json")
eb200_core\path_sandbox.py:185:with open(os.path.join(repo_real, "storage_roots.json"), "w", encoding="utf-8") as fh:
eb200_core\path_sandbox.py:249:with open(os.path.join(repo_real, "storage_roots.json"), "w", encoding="utf-8") as fh:
eb200_core\recording_index.py:194:configured = load_storage_roots(os.path.join(repo_root, "storage_roots.json"))
eb200_core\recording_index.py:709:manifest_path = os.path.join(dir_path, "job.json")
eb200_core\recording_index.py:806:session_labels = LabelStore(os.path.join(repo_root, "session_labels.json"))
eb200_core\recording_index.py:807:spectrum_labels = LabelStore(os.path.join(repo_root, "spectrum_labels.json"))
eb200_core\recording_index.py:808:audio_labels = LabelStore(os.path.join(repo_root, "audio_labels.json"))
eb200_core\recording_index.py:913:LabelStore(os.path.join(repo_root, "session_labels.json")).set(rel, "")
eb200_core\recording_index.py:914:LabelStore(os.path.join(repo_root, "session_notes.json"), cleaner=clean_note).set(rel, "")
eb200_core\recording_index.py:923:if dir_name.startswith("longrun_") or os.path.isfile(os.path.join(dir_path, "job.json")):
eb200_core\recording_index.py:952:LabelStore(os.path.join(repo_root, "spectrum_labels.json")).set(rel, "")
eb200_core\recording_index.py:953:LabelStore(os.path.join(repo_root, "spectrum_notes.json"), cleaner=clean_note).set(rel, "")
eb200_core\recording_index.py:962:if dir_name.startswith("longrun_") or os.path.isfile(os.path.join(dir_path, "job.json")):
eb200_core\recording_index.py:998:LabelStore(os.path.join(repo_root, "audio_labels.json")).set(rel, "")
eb200_core\recording_index.py:999:LabelStore(os.path.join(repo_root, "audio_notes.json"), cleaner=clean_note).set(rel, "")
eb200_core\recording_index.py:1000:LabelStore(os.path.join(repo_root, "audio_klassen.json")).set(rel, "")
eb200_core\recording_index.py:1010:configured = load_storage_roots(os.path.join(repo_root, "storage_roots.json"))
eb200_core\recording_index.py:1030:manifest_path = os.path.join(resolved_dir, "job.json")
eb200_core\recording_index.py:1090:saved = LabelStore(os.path.join(repo_root, "session_labels.json")).set(rel, text)
eb200_core\recording_index.py:1098:saved = LabelStore(os.path.join(repo_root, "spectrum_labels.json")).set(rel, text)
eb200_core\recording_index.py:1106:saved = LabelStore(os.path.join(repo_root, "audio_labels.json")).set(rel, text)
eb200_core\recording_index.py:1172:index_path = os.path.join(tmp, "jobs", "index.json")
eb200_core\recording_index.py:1173:roots_path = os.path.join(tmp, "storage_roots.json")
eb200_core\recording_index.py:1200:LabelStore(os.path.join(tmp, "session_labels.json")).set(rel_a, "Testlauf A")
eb200_core\recording_index.py:1416:with open(os.path.join(orphan_abc_dir, "job.json"), "w", encoding="utf-8") as f:
eb200_core\recording_index.py:1450:LabelStore(os.path.join(tmp, "session_labels.json")).set(rel_c, "Zu loeschen")
eb200_core\recording_index.py:1453:assert LabelStore(os.path.join(tmp, "session_labels.json")).get(rel_c) == ""
eb200_core\recording_index.py:1556:audio_labels_path = os.path.join(tmp, "audio_labels.json")
eb200_core\recording_index.py:1606:audio_notes_path = os.path.join(tmp, "audio_notes.json")
eb200_core\recording_index.py:1607:audio_klassen_path = os.path.join(tmp, "audio_klassen.json")
eb200_core\recording_index.py:1700:assert LabelStore(os.path.join(tmp, "session_labels.json")).get("sessions/session_e.db") == "Session-Kurztext"
eb200_core\recording_index.py:1707:assert LabelStore(os.path.join(tmp, "spectrum_labels.json")).get("records/label_test.specbin") == "Spektrum-Kurztext"
eb200_core\recording_index.py:1716:assert LabelStore(os.path.join(tmp, "audio_labels.json")).get("records/label_test.wav") == "Audio-Kurztext"
tools\classify_eval.py:409:ap.add_argument("--labels", default="audio_klassen.json", help="Label-Datei (LabelStore-Format)")
tools\eye_diagram.py:2062:t24_json_path = os.path.join(tmp, "t24.json")
tools\known_freq_abgleich.py:23:Kanal-Doppelungen "ors_ukw_oesterreich.json"; die tatsaechlich vorhandene
tools\known_freq_abgleich.py:93:FNV_FILENAME = "FNV_AT_2026.json"          # siehe Abweichung im Moduldocstring
tools\known_freq_abgleich.py:94:ERGAENZUNG_FILENAME = "fnv_ergaenzungen.json"
tools\known_freq_abgleich.py:656:_write_json(os.path.join(tmp, "t1.json"), t1_doc)
tools\known_freq_abgleich.py:664:_write_json(os.path.join(tmp, "t2.json"), t2_doc)
tools\known_freq_abgleich.py:673:_write_json(os.path.join(tmp, "t3.json"), t3_doc)
tools\known_freq_abgleich.py:709:check("T7 t1.json entfernt", not os.path.isfile(os.path.join(tmp, "t1.json")) and
tools\known_freq_abgleich.py:711:check("T8 t1.json in deleted_files", "t1.json" in applied["deleted_files"], applied["deleted_files"])
tools\known_freq_abgleich.py:713:with open(os.path.join(tmp, "t2.json"), encoding="utf-8") as fh:
tools\known_freq_abgleich.py:718:with open(os.path.join(tmp, "t3.json"), encoding="utf-8") as fh:
tools\known_freq_abgleich.py:732:moved_from == {"t1-feiner": "t1.json", "t2-ausserhalb": "t2.json"}, moved_from)
tools\known_freq_abgleich.py:746:_write_json(os.path.join(tmp, "u1.json"), {
tools\known_freq_abgleich.py:751:_write_json(os.path.join(tmp, "u2.json"), {
tools\known_freq_abgleich.py:757:entscheidungen17 = {"u1-band": {"aktion": "VERSCHIEBEN", "ziel": "u2.json"},
tools\known_freq_abgleich.py:766:"u1.json" in applied17["deleted_files"] and not os.path.isfile(os.path.join(tmp, "u1.json")),
tools\known_freq_abgleich.py:769:with open(os.path.join(tmp, "u2.json"), encoding="utf-8") as fh:
tools\known_freq_abgleich.py:775:check("T17d moved_from korrekt gesetzt", moved17.get("moved_from") == "u1.json", moved17)
tools\known_freq_abgleich.py:791:_write_json(os.path.join(tmp, "v1.json"), {
tools\known_freq_abgleich.py:797:entscheidungen_path = os.path.join(tmp, "entscheidungen.json")
tools\known_freq_wien_merge.py:76:ORS_FILENAME = "ORS_UKW_AT.json"
tools\known_freq_wien_merge.py:77:BC_FILENAME = "UKW_BC.json"
tools\known_freq_wien_merge.py:78:WIEN_FILENAME = "ukw_wien.json"
tools\known_freq_wien_merge.py:79:CZ_FILENAME = "ukw_cz.json"
tools\selftest_delete.py:48:az_sess.LABELS = LabelStore(os.path.join(tmp, "session_labels.json"))
tools\selftest_delete.py:49:az_sess.NOTES = LabelStore(os.path.join(tmp, "session_notes.json"), cleaner=clean_note)
tools\selftest_delete.py:54:az_spec.LABELS = LabelStore(os.path.join(tmp, "spectrum_labels.json"))
tools\selftest_delete.py:55:az_spec.NOTES = LabelStore(os.path.join(tmp, "spectrum_notes.json"), cleaner=clean_note)
tools\selftest_delete.py:182:with open(os.path.join(longrun_dir, "job.json"), "w", encoding="utf-8") as fh:
tools\selftest_known_edit.py:71:_write_json(os.path.join(tmp, "a.json"), {
tools\selftest_known_edit.py:83:_write_json(os.path.join(tmp, "a.json"), {
tools\selftest_known_edit.py:103:_write_json(os.path.join(tmp, "a.json"), {
tools\selftest_known_edit.py:116:fehler = block_admin.validate_candidate("b.json", kandidat_dup)
tools\selftest_known_edit.py:124:fehler2 = block_admin.validate_candidate("b.json", kandidat_ok)
tools\selftest_known_edit.py:134:path = os.path.join(tmp, "roundtrip.json")
tools\selftest_known_edit.py:224:n, zeilen, fehler = block_admin._raster_preview("t5.json", doc, "t5")
tools\selftest_known_edit.py:237:n_air, _, fehler_air = block_admin._raster_preview("t5a.json", doc_air, "t5a")
tools\selftest_known_edit.py:246:n_off, _, _ = block_admin._raster_preview("t5.json", doc_off, "t5")
tools\selftest_known_edit.py:338:path = os.path.join(tmp, "t8.json")
tools\selftest_known_edit.py:351:path2 = os.path.join(tmp, "t8b.json")
analyze_audio.py:106:LABELS = LabelStore(os.path.join(AUDIO_ROOT, "audio_labels.json"))
analyze_audio.py:107:NOTES = LabelStore(os.path.join(AUDIO_ROOT, "audio_notes.json"), cleaner=clean_note)
analyze_audio.py:126:KLASSEN_MANUELL = LabelStore(os.path.join(AUDIO_ROOT, "audio_klassen.json"), cleaner=clean_klasse)
analyze_audio.py:244:manifest_path = os.path.join(dir_path, "job.json")
analyze_audio.py:1603:tmp_labels = LabelStore(os.path.join(AUDIO_ROOT, "audio_labels.json"))
analyze_audio.py:1604:tmp_notes = LabelStore(os.path.join(AUDIO_ROOT, "audio_notes.json"), cleaner=clean_note)
analyze_audio.py:1605:tmp_klassen = LabelStore(os.path.join(AUDIO_ROOT, "audio_klassen.json"), cleaner=clean_klasse)
analyze_audio.py:1641:with open(os.path.join(AUDIO_ROOT, "storage_roots.json"), "w", encoding="utf-8") as f:
analyze_audio.py:1654:os.remove(os.path.join(AUDIO_ROOT, "storage_roots.json"))
analyze_audio.py:1685:with open(os.path.join(longrun_dir, "job.json"), "w", encoding="utf-8") as f:
analyze_mscan.py:1202:index_path = os.path.join(tmp, "jobs", "index.json")
analyze_mscan.py:1205:roots_path = os.path.join(tmp, "storage_roots.json")
analyze_sessions.py:66:LABELS = LabelStore(os.path.join(SESSION_ROOT, "session_labels.json"))
analyze_sessions.py:71:NOTES = LabelStore(os.path.join(SESSION_ROOT, "session_notes.json"), cleaner=clean_note)
analyze_spectrum.py:88:LABELS = LabelStore(os.path.join(SPECTRUM_ROOT, "spectrum_labels.json"))
analyze_spectrum.py:93:NOTES = LabelStore(os.path.join(SPECTRUM_ROOT, "spectrum_notes.json"), cleaner=clean_note)
analyze_spectrum.py:199:job_json_path = os.path.join(dir_path, "job.json")
manage_jobs.py:3041:index_path = os.path.join(tmp, "jobs", "index.json")
manage_jobs.py:3042:roots_path = os.path.join(tmp, "storage_roots.json")
manage_jobs.py:3217:mscan_catalog_path = os.path.join(mscan_catalog_dir, "test.json")
manage_jobs.py:3342:with open(os.path.join(mscan_jobs_dir, "ZZZZZZ.json"), "w", encoding="utf-8") as f:
```

### P4 - Modul-Dateinamen unter eb200_core\ (23 Dateien)

```
audio_wav.py
block_admin.py
db.py
dscan_store.py
http_base.py
job_runner.py
job_store.py
known_freq.py
label_store.py
mscan_job.py
mscan_run.py
mscan_view.py
param_store.py
path_sandbox.py
recording_index.py
sched_health.py
scpi_verify.py
session_store.py
signal_detect.py
spectrum_reader.py
spectrum_store.py
ui_theme.py
__init__.py
```

Bewertung offen (Chat).

## 10. Zeilenenden und vorhandene Linux-Artefakte (P-EOL)

### E1 - Inhalt von .gitattributes (vollstaendig)

```
* text=auto eol=crlf
*.sh   text eol=lf
*.ps1  text eol=crlf
*.wav      binary
*.specbin  binary
*.audspec  binary
*.db       binary
*.jpg      binary
*.png      binary
*.ico      binary
```

### E2 - Vorhandene Shell-/systemd-Dateien

Befehl: `Get-ChildItem -Recurse -File -Include *.sh,*.service,*.target -Name`

Ergebnis: keine Treffer (leere Liste).

Bewertung offen (Chat).

## 11. Rohdaten: Suchbefehle und Trefferzahlen

Basis fuer alle Suchlaeufe S1-S12 (siehe Hinweis in Abschnitt 0 zur
Erweiterung gegenueber der Auftrags-Vorlage):

```powershell
$Dateien = Get-ChildItem -Recurse -File -Include *.py,*.ps1,*.bat,*.md |
  Where-Object { $_.FullName -notmatch '\\(\.git|\.venv|logs|sessions|records|jobs|handbuch\\arbeit)\\' -and
                 $_.Name -ne 'EB200_sources.txt' }
```

| Lauf | PowerShell-Befehl | Trefferzahl |
|---|---|---|
| S1 | `Select-String -Path $Dateien -Pattern 'os\.name\|sys\.platform\|platform\.(system\|machine\|release)'` | 16 |
| S2 | `Select-String -Path $Dateien -Pattern '\b(msvcrt\|winreg\|winsound\|win32\w+\|ctypes\.windll\|WinDLL)\b'` | 12 |
| S3 | `Select-String -Path $Dateien -Pattern '"[A-Za-z]:\\\\\|''[A-Za-z]:\\\\'` | 2 |
| S4 | `Select-String -Path $Dateien -Pattern '\.exe\b\|\.bat\b\|\.ps1\b\|\.cmd\b'` | 11 |
| S5 | `Select-String -Path $Dateien -Pattern '\\\\(bin\|Program\|Windows\|Users\|Temp)\b'` | 1 |
| S6 | `Select-String -Path $Dateien -Pattern 'subprocess\.\|CREATE_NEW\|CREATION_FLAGS\|creationflags\|startupinfo\|shell=True'` | 16 |
| S7 | `Select-String -Path $Dateien -Pattern 'os\.startfile\|webbrowser\.\|explorer\b'` | 2 |
| S8 | `Select-String -Path $Dateien -Pattern 'cp1252\|mbcs\|chcp\|locale\.setlocale\|getpreferredencoding'` | 13 |
| S9 | `Select-String -Path $Dateien -Pattern 'setsockopt\|SO_RCVBUF\|SO_REUSEADDR\|SO_SNDBUF'` | 16 |
| S10 | `Select-String -Path $Dateien -Pattern '^\s*(import numpy\|from numpy)'` | 12 |
| S11 | `Select-String -Path $Dateien -Pattern 'SDR_DEVICES\|"sessions"\|''sessions''\|"records"\|''records'''` | 77 |
| S12 | `Select-String -Path $Dateien -Pattern 'time\.time\(\)\|datetime\.now\|time\.monotonic'` | 151 |

($Dateien in der Tabelle steht fuer `$Dateien.FullName`, wie im Auftrag als
"Vorlage ... danach wiederverwenden" beschrieben.)
