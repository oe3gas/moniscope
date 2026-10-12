# EB200 Suite – Arbeitsregeln für Claude Code

- `EB200_sources.txt` ist ein generierter Export für Claude.ai. NIE lesen, NIE editieren.
  Immer die Zieldateien direkt öffnen (`waterfall.py`, `eb200_core/*.py` usw.).
- Abhängigkeiten (Entscheidungslog 2026-09-03, T-610): numpy ist zugelassene
  Pflichtabhängigkeit der gesamten Suite – auch in `eb200_core/`. Jede weitere
  Fremdbibliothek (scipy, matplotlib, pandas, …) ist OPTIONAL und wird per
  `try`/`except ImportError` gekapselt: das betroffene Merkmal fällt aus, der Server
  startet trotzdem (Muster: matplotlib in `session_analyze.py`).
  Verboten bleiben Web-Frameworks, ORMs, JS-Build-Ketten und jede Bibliothek mit
  transitiven Laufzeitabhängigkeiten. Neue Pflichtabhängigkeiten nur nach ausdrücklicher
  Entscheidung, dann mit Eintrag in `requirements.txt` und im Entscheidungslog von PLAN.md.
- Inline-Kommentare auf Deutsch, ASCII-Umlaute sind in bestehenden Dateien üblich (ae/oe/ue) – Stil der jeweiligen Datei beibehalten.
- Zeilenenden sind CRLF, auf jeder Plattform (`.gitattributes`: `* text=auto eol=crlf`).
  Das eol=crlf wirkt auch beim Auschecken unter Linux; die Blobs im Repository tragen
  wie immer LF. Nie konvertieren.
- Edits nur per Edit-Tool mit eindeutigem Anker, nie nach Zeilennummern.
- Nach Python-Edits: `python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" <datei>`.
- Geteilter JS-Block „Katalog-Streifen“: steht in `waterfall.py`, `analyze_sessions.py`,
  `analyze_spectrum.py` und `sdr_waterfall.py` wortgleich zwischen `// >>> KNOWN_STRIP_BLOCK_BEGIN`
  und `// <<< KNOWN_STRIP_BLOCK_END`. Referenz ist `waterfall.py`. Änderungen nur dort, dann
  `python tools/sync_known_block.py <ziele>` und anschließend `--check` (Exit 0). Nie per Editor kopieren.
- Keine Tests gegen das EB200 (10.0.0.55) oder SDR-Hardware starten, außer der Prompt verlangt es ausdrücklich.
- Keine Edits über den Prompt hinaus. Abweichungen vom Prompt nicht stillschweigend lösen, sondern melden.
- Weicht eine Vorprüfung (Anker-Zählung, Soll-Wert, Dateiname, Vorbedingung) vom Prompt ab:
  abbrechen und berichten – AUSSER der Prompt-Text selbst löst die Abweichung eindeutig auf
  (er nennt die Alternative, oder es ist ein offensichtlicher Tippfehler mit genau einer plausiblen
  Lesart). Dann dokumentiert fortfahren; Abweichung und Auflösung stehen im Abschlussbericht an
  erster Stelle. Stillschweigende Korrekturen bleiben verboten.
- Die Ausnahmeklausel gilt NUR für Vorprüfungen. Gates nach einem Edit werden nie dadurch
  erfüllt, dass der geschriebene Text angepasst wird.
  Weicht ein Gate ab: abbrechen und berichten. Ein Gate, der so lange verschoben wird, bis
  er passt, misst nichts mehr; ein falscher Soll-Wert ist ein Prompt-Fehler und gehört
  gemeldet, nicht ausgeglichen.
- Ausnahme Werkzeugfehler (T-1815j, Anlass T-1815i): Weicht der geschriebene Text
  nachweislich vom NEU-Text des Auftrags ab, weil das Edit-Tool ihn verändert hat
  (abgeschnittenes Leerzeichen, aufgelöste Escape-Sequenz), darf Claude Code ihn auf den
  wörtlichen NEU-Text berichtigen. Danach laufen ALLE Gates erneut, und der Bericht nennt
  die Abweichung an erster Stelle. Maßstab ist allein der NEU-Text des Auftrags - jede
  Anpassung in Richtung eines Gates bleibt verboten.
- Nicht rückfragen bei eindeutigen Ankern; nur wenn ein Anker nicht oder mehrfach gefunden wird.
- Das Edit-Tool loest Escape-Sequenzen wie `\u00b7` beim Schreiben zum Glyphen auf. Solche Sequenzen
  NIE per Edit-Tool eintragen: Byte-Vergleich nach dem Edit, im Zweifel per Python-Skript auf Byte-Ebene.

## Planungs-Workflow
- PLAN.md ist die verbindliche Aufgabenliste dieses Projekts.
- Vor Arbeitsbeginn: PLAN.md lesen, betroffene Task auf [~] setzen.
- Nach Abschluss einer Task: Status auf [x] setzen, Datum ergänzen,
  PLAN.md im selben Commit wie den Code committen.
- Neue Erkenntnisse/Folgeaufgaben: als neue Tasks mit ID anlegen,
  nicht stillschweigend erledigen.
- Architekturentscheidungen kurz im Entscheidungslog dokumentieren.
- Commits enthalten nur Dateien der laufenden Task. Laufzeitdaten (`jobs/`, `records/`,
  `audio_labels.json`, `audio_notes.json`) und lokale Konfiguration (`storage_roots.json`,
  `station.json`, `device.json`) werden nie mitgestaged; fehlen sie in `.gitignore`, im
  Abschlussbericht melden statt committen.
- CLAUDE.md ist versionierte Projektdokumentation (Entscheidungslog 2026-09-03, T-614),
  kein Maschinenzustand: Regeländerungen werden wie PLAN.md-Änderungen im selben Commit
  wie die zugehörige Task geführt.
- Jeder Abschlussbericht nennt: Abweichungen (zuerst), Gate-Ausgaben mit Ist-Zahlen,
  `git status --porcelain` nach dem Commit, Commit-Hash(es).
- Auf die Frage "Status" oder "/status": kompakte Zusammenfassung liefern
  (erledigt / in Arbeit / blockiert / nächste Schritte), keine Codeänderungen.

## Eingebettete HTML/CSS/JS in Python-Strings (HTML_PAGE, COMMON_CSS, COMMON_JS, …)

Die Seiten-Templates sind normale """-Strings, KEINE Raw-Strings und KEINE f-Strings. Python
interpretiert darin jeden Backslash: `\n`, `\t`, `\u2026`, und – leicht übersehen – `\25BE`
als OKTAL-Escape (chr(21) + "BE"). Das erzeugt stille Fehler (Steuerzeichen im CSS, kaputte
Regex in JS), die kein ast.parse und kein node --check findet.

Regeln:
1. Kein Backslash in CSS-Inhalten: statt `content:'\25BE '` das Zeichen direkt schreiben
   (`content:'▾ '`), die Dateien sind UTF-8.
2. Kein Backslash in JS-Strings/Regex innerhalb eines nicht-rohen Python-Strings. Wo ein
   Backslash unvermeidbar ist (Regex wie `/\d+/`): den Backslash VERDOPPELN (`/\\d+/`), oder
   den JS-Block als eigene r"""…"""-Konstante anlegen (Muster waterfall.py: JS-Konstanten
   sind separate Modulvariablen).
3. Nach jedem Edit an einem Template-String, der einen Backslash enthält, prüfen:
   `python -c "import <modul> as m; s=m.HTML_PAGE; assert not any(ord(c)<32 and c not in '\n\t' for c in s)"`
   (kein Steuerzeichen außer Zeilenumbruch/Tab im gerenderten String).
4. Ein Anker für str_replace darf niemals ein Steuerzeichen enthalten – solche Zeilen per
   Select-String über einen benachbarten Klartext-Teil finden und die ganze Zeile ersetzen.
5. Bei Unsicherheit, ob ein Backslash nötig ist: Alternative ohne Backslash wählen
   (Unicode-Zeichen direkt, String.fromCharCode, CSS-Klassen statt content-Escapes).
   
## Lokalisierung (Entscheidungslog 2026-08-31, umgesetzt mit Meilenstein 18)

Die Oberfläche ist seit Meilenstein 18 (T-1800 bis T-1828) englisch; Englisch ist die
einzige UI-Sprache. Grundregel: **UI-Text ist Englisch. Code, Bezeichner, Kommentare,
Docstrings, Log- und CLI-Ausgaben sind Deutsch.** Verbindlich für jeden Begriff ist
`docs/Glossar-EN.md`; fehlt ein Begriff, kommt er im selben Auftrag als Glossarzeile hinzu.
Die Regeln von 2026-08-31 waren Vorbereitung und gelten jetzt für jede Code-Änderung:

1. Neue persistierte Werte (Zustände, Arten, Herkunft, Modi, Fehler-/Abbruchcodes,
   Schlüssel in job.json / index.json / Sidecars / Manifesten) sind ASCII und
   sprachneutral. Bestehende Werte (`laeuft`, `beendet`, `gestoppt`, `verwaist`,
   `geplant`, ...) niemals umbenennen – ID-Stabilität geht vor.
2. Anzeigetext gehört nie in einen Datenwert. Server liefern Codes, Zahlen, Zeitstempel
   und strukturierte Felder (Muster: `inhalt` in `recording_index`); die Formulierung
   entsteht im Client (JS).
3. Neue Fehler- und Abbruchgründe, die in der UI erscheinen: als englischer Satz nach
   Glossar Abschnitt 8 oder als Code + Parameter
   (`{"code": "job.wrong_state", "params": {...}}`), nie als deutscher Satz. Wer liest,
   entscheidet: Code, wenn ein Skript den Fall unterscheiden muss, sonst der Satz
   (Entscheidungslog T-1820a, T-1828). Log-Ausgaben und CLI-Werkzeuge bleiben deutsch.
4. Sätze mit Zahlen an neuen Stellen als ein Formatstring je Aussage
   (`'{n} recordings on {k} channels'`), nicht als Verkettung von Teilstrings.
5. `python tools/check_ui_lang.py` endet mit Exitcode 0 (Stufe A 0, seit T-1826a). Ein
   neuer Befund der Stufe A ist ein Gate-Fehler. Gezählt wird nur Anzeigetext
   (`tools/ui_lang_sicht.py`); deutsche Bezeichner im Skript sind kein Befund.

Bei Konflikt mit einer bestehenden Stelle: bestehende Stelle unverändert lassen und im
Bericht als Altbestand nennen, nicht eigenmächtig umbauen.

## Gates richtig formulieren (T-514)

Ein fehlgeschlagenes Gate bedeutet nicht automatisch, dass die Datei falsch
ist – es kann auch das Gate falsch sein. CC korrigiert in diesem Fall NIE
die Datei, sondern meldet die Abweichung mit Begründung; die Entscheidung
trifft der Auftraggeber. Das gilt zusätzlich zu, nicht anstelle von der
Regel weiter oben, dass Gates nie durch Anpassen des geschriebenen Textes
erfüllt werden.

Regeln für die Formulierung von Gates:

- Verbotsproben auf CODE laufen über `ast`, nicht über `Select-String`.
  Eine Substring-Suche trifft auch Kommentare und Docstrings – und gute
  Module nennen genau die Dinge beim Namen, die sie bewusst NICHT tun.
  `Select-String` bleibt für Vorkommensprüfungen mit Soll >= 1.
- Ein Soll-Zählwert muss aus der FERTIGEN Datei ableitbar sein. Zählt ein
  Gate Treffer im gesamten File, müssen alle Fundstellen bekannt sein –
  auch die in Funktionen, die der Auftrag gar nicht anfasst. Im Zweifel den
  Geltungsbereich benennen ("0 Treffer in run_phase_c und run_phase_d")
  statt eine Gesamtzahl zu raten.
- Zählbereich einer Vorkommensprüfung ist der GESAMTE Dateitext: Code,
  Kommentare und Docstrings gleichermaßen. Eine Konstante, die in einem
  Docstring erklärt wird, zählt als Treffer wie ihre Definition. Beim
  Ableiten des Soll-Werts ist der NEU-Block vollständig durchzugehen, nicht
  nur sein ausführbarer Teil.
- Ein Verbot gilt für den benannten AUSDRUCK, nicht für die ganze
  Funktion. "Kein abs() im ok-Kriterium" verbietet nicht jedes abs() im
  Rumpf.
- Umbenennungen und Entfernungen werden repo-weit gesucht, BEVOR der Prompt
  entsteht. Verweise auf den alten Namen in Kommentaren und Docstrings gehören
  zur Umbenennung und nicht in den Rest: sie zeigen nach dem Edit auf etwas,
  das es nicht mehr gibt. Ein Gate mit Soll 0 auf den alten Namen sichert das
  ab und deckt zugleich auf, wenn der Auftrag Fundstellen übersehen hat.
- Ein ALT-Block ist ein wörtliches Zitat, kein Kurzzitat. Wird nur die
  erste Zeile eines mehrzeiligen Blocks zitiert, muss der Auftrag das
  ausdrücklich sagen und den Verbleib des Rests regeln.
- Jeder Prompt ist für sich vollständig. Verweise auf Skripte, Blöcke
  oder Abschnitte aus früheren Nachrichten sind für CC nicht auflösbar;
  der Text muss im Prompt stehen. Ebenso wenig darf der Prompt die Struktur
  einer Zieldatei behaupten – er lässt sie in der Vorprüfung ermitteln.

Anlass: In der Aufgabenserie T-507/T-511/T-512/T-514 waren vier Gate- bzw.
Ortsangaben falsch spezifiziert (Substring-Verbotsprobe auf Docstring-Prosa,
zu niedrig berechneter Soll-Zählwert, Verbot auf Funktion statt Ausdruck,
angenommener ##-Abschnitt ohne Entsprechung). In allen vier Fällen war die
Datei richtig und der Prompt falsch; CC hat jeweils korrekt gestoppt.

Nachtrag 2026-09-08 (T-1014a): erneut drei falsche Soll-Werte. Zweimal war die
Ursache, dass beim Ableiten nur der Code gezählt und die Erwähnung derselben
Konstante im Docstring übersehen wurde; einmal hatte der Auftrag vier Verweise
auf eine umbenannte Methode nicht erfasst, sodass ein Gate mit Soll 0 zu Recht
anschlug. Wieder war in allen Fällen die Datei richtig und der Prompt falsch.
Daraus die beiden Regeln zu Zählbereich und Umbenennungen.

## Task-ID-Gates

`\bT-NNNN\b` nur verwenden, wenn die Task keine Unter-IDs hat. Bei Familien mit
Buchstabensuffix (T-NNNNa, T-NNNNb) stattdessen `T-NNNN(?!\d)` verwenden: die
Wortgrenze `\b` greift zwischen Ziffer und Buchstabe nicht, `\bT-1014\b` findet
T-1014a und T-1014b also NICHT. Der Negative-Lookahead trifft die Basis-ID und
alle Buchstabenvarianten, schließt längere Ziffernfolgen (T-10140) aber weiterhin
aus. Anlass: T-1014, Gate-Soll 4 gegen Ist 2.

## Programm- und Datenwurzel (Meilenstein 20, T-2000 bis T-2004)

MoniScope trennt Programm und Daten (Entscheidungslog 2026-10-08). Dauerregeln für jede
Code-Änderung:

1. Kein Pfad hängt vom Arbeitsverzeichnis ab: kein `os.path.abspath(".")`, kein
   `os.getcwd()`, kein relativer Datei- oder Verzeichnisname als Lese- oder Schreibziel.
2. Beschreibbare Daten und Betreiberkonfiguration liegen unter der Datenwurzel:
   `eb200_core.paths.DATA_ROOT` bzw. `paths.data_path(...)`. Mitgelieferte, nur gelesene
   Programmdaten (z. B. `modes/fsk.json`, `session_analyze.py`) über `paths.PROGRAM_ROOT`
   bzw. `paths.program_path(...)`. `__file__` dient für Pfade nur noch der
   Direktaufruf-Weiche (`if __package__ in (None, ""):`) und `eb200_core/paths.py` selbst.
3. Die Datenwurzel kommt nur aus `--data-dir`, nie aus einer Umgebungsvariable. Jeder
   Server mit eigenem `argparse` nimmt `--data-dir` an; `start_eb200.py` reicht es absolut
   an alle Server weiter.
4. Module in `eb200_core` importieren einander nur mit Paketpräfix (`eb200_core.xxx`).
5. Keine neuen Unterprozesse nach dem Muster `[sys.executable, "<datei>.py", ...]` ohne
   ausdrücklichen Auftrag; die bestehenden stellt T-2005 auf eine Hilfsfunktion um.
6. Produktivcode liest nie den eigenen Quelltext (`__file__` öffnen, `inspect.getsource`);
   Selbsttests dürfen das.

Prüfung bei Bedarf: mit `--data-dir` auf einen leeren Ordner starten; danach darf im Repo
keine neue Datei liegen.

## Lokaler Betrieb (seit 2026-09-29, T-1834)

Arbeit ab jetzt nur mit Claude Code auf dem Rechner des Auftraggebers (Entscheidungslog
2026-09-29, T-1834). Der Betrieb in der Linux-VM brachte keine Beschleunigung; sein
Abschnitt weiter unten ist ausser Betrieb und nur noch Historie.

- COMMIT JA, PUSH NEIN. Sind alle Gates gruen, committet Claude Code auf main und pusht
  NICHT. Der Auftraggeber pusht selbst. Kein Zweig, kein Pull Request.
- ABBRUCH. Weicht eine Vorpruefung oder ein Gate ab, schreibt Claude Code den Bericht
  nach docs/berichte/<Task-ID>.md, committet ihn NICHT und meldet die Abweichung.
- BEKANNTE AUSNAHMEN in "git status --porcelain" (Laufzeitzustand dieses Rechners):
  jobs/mscan/*.json (geaendert), analyse_20260907_RPR/, docs/messungen/,
  tools/deploy_from_repo.sh (unversioniert). Sonst nichts Fremdes.
- AUFTRAGSDATEIEN. Neue oder geaenderte Dateien unter docs/prompts/ entstehen im Chat
  und liegen uncommittet im Arbeitsverzeichnis. In der Vorpruefung sind sie zulaessig
  und kein Abbruchgrund. Die Auftragsdatei der laufenden Task gehoert zur Task und wird
  mit ihr committet; andere Auftragsdateien bleiben liegen.
- WIEDERHOLUNGSLAUF. Ein uncommitteter Abbruchbericht docs/berichte/<Task-ID>.md aus
  einem frueheren Lauf ist zulaessig. Er wird ueberschrieben bzw. fortgeschrieben und
  mit dem erfolgreichen Lauf committet.
- check_ui_lang.py endet seit T-1826a mit Exitcode 0. Exitcode 1 ist kein
  Normalzustand mehr, sondern ein Gate-Fehler: abbrechen und berichten.

## Cloud-Sitzungen (Claude Code in der Cloud) - AUSSER BETRIEB seit 2026-09-29 (T-1834)

Dieser Abschnitt ist ausser Betrieb. Die Push-Regel aus T-1831 ist UNGUELTIG; es gilt
"Lokaler Betrieb" oben. Die Befunde (numpy-Importkette, CRLF unter Linux) bleiben als
Wissen stehen. Urspruenglicher Text, wortgleich:

Dieses Repository wird sowohl auf dem Windows-Rechner des Auftraggebers als auch in
Cloud-Sitzungen bearbeitet. Eine Cloud-Sitzung klont das Repository in eine frische
Linux-VM. Was dort anders ist:

- ARBEITSVERZEICHNIS SAUBER. Die bekannten Ausnahmen des lokalen Rechners
  (jobs/mscan/*.json als geaendert, dazu analyse_20260907_RPR/, docs/messungen/,
  tools/deploy_from_repo.sh als unversioniert) sind Laufzeitzustand EINES Rechners
  und existieren in der VM nicht. Ein Gate "git status --porcelain zeigt nur die
  bekannten Ausnahmen" bedeutet dort: die Ausgabe ist LEER, bis auf die Dateien der
  laufenden Task. Das ist die schaerfere Fassung derselben Pruefung - zwischen den
  Ausnahmen kann sich lokal eine fremde Aenderung verstecken, in der VM nicht.

- ZEILENENDEN BLEIBEN CRLF. .gitattributes setzt "* text=auto eol=crlf". Das
  eol=crlf gilt plattformunabhaengig, auch beim Auschecken unter Linux; die Blobs
  im Repository tragen wie immer LF. Die CRLF-Gates gelten unveraendert, und die
  Regel "nie konvertieren" gilt in der VM genauso.

- NUMPY KOMMT AUS DEM AUFSETZ-SKRIPT. Das Skript laeuft NICHT im
  Repository-Verzeichnis - "pip install -r requirements.txt" scheitert dort mit
  "No such file or directory: 'requirements.txt'", und die Sitzung startet dann gar
  nicht erst. Es installiert deshalb direkt: "python3 -m pip install numpy". Das
  Repository selbst liegt in der Sitzung unter /home/user/EB200repo. Ohne numpy
  laufen check_js_blocks.py, selftest_param_sync.py, ui_theme.py --selftest und
  check_ui_lang.py zwar, aber jeder Import von sdr_waterfall, audio_classify,
  eb200_core/signal_detect oder eb200_core/mscan_run scheitert.
  ACHTUNG, Befund aus dem Lauf vom 2026-09-27: auch "import manage_jobs" scheitert.
  Die Kette ist manage_jobs.py Zeile 63 -> eb200_core/mscan_run.py Zeile 106 ->
  "import numpy as np". Damit ist OHNE numpy kein einziges Gate der Aufgabenfamilie
  T-1814 ausfuehrbar, denn alle lauten "import manage_jobs as m; ... m.HTML_MSCAN_*".
  Die urspruengliche Fassung dieses Absatzes nannte manage_jobs bei den Werkzeugen,
  die ohne numpy laufen - das gilt nur fuer "manage_jobs.py --selftest" als
  Unterprozess, nicht fuer den Import des Moduls.
  Fehlt numpy: abbrechen und melden, nicht selbst nachinstallieren. Die Ursache ist
  immer das Aufsetz-Skript der Cloud-Umgebung, und dort gehoert sie behoben - eine
  Nachinstallation in der laufenden Sitzung verdeckt sie fuer alle folgenden.

- KEINE HARDWARE. Der EB200 (10.0.0.55) und angeschlossene SDR sind aus der Cloud
  nicht erreichbar. Das aendert nichts, denn Tests gegen die Hardware sind ohnehin
  untersagt. Abnahmen im Browser macht der Auftraggeber auf seinem Rechner, nachdem
  er den Pull Request zusammengefuehrt hat.

- PUSH OHNE RUECKFRAGE, DIREKT AUF main - UNGUELTIG seit 2026-09-29 (T-1834), ersetzt
  durch "Lokaler Betrieb". Nachfolgender Text nur noch Historie:
  (Entscheidungslog 2026-09-27, T-1831). Der
  Commit bleibt sonst in der VM und geht mit ihr verloren. Deshalb gilt fuer
  Meilenstein 18:
  * Alle Gates gruen: committen, dann "git pull --rebase" und "git push" auf main.
    Kein Zweig, kein Pull Request, keine Freigabe. Der Abschlussbericht nennt den
    Hash und dass der Push erfolgt ist.
  * Ein Gate oder eine Vorpruefung weicht ab: die Regel "abbrechen und berichten"
    bleibt unveraendert in Kraft. Dann wird NUR die Berichtsdatei committet und
    gepusht - auf einen eigenen Zweig "bericht/T-XXXX", nicht auf main. So steht der
    Befund binnen Minuten im Repository, ohne dass eine ungepruefte Aenderung auf
    main landet.
  * Vor jedem Commit "git pull --rebase": der Rechner des Auftraggebers pusht
    parallel Auftrags- und Berichtsdateien unter docs/ (tools/sync_docs.ps1).
    Konflikte sind dort nicht zu erwarten, weil beide Seiten verschiedene Dateien
    anfassen; tritt einer doch auf, ist er ein Abbruchgrund wie jeder andere.
  Befund aus T-1830: die urspruengliche Fassung dieses Absatzes behauptete, der Pull
  Request entstehe von selbst. Er entsteht nicht - und seit T-1831 wird auch keiner
  mehr gebraucht.
