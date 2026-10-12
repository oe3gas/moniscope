# Glossar DE -> EN (MoniScope, Meilenstein 18)

Verbindlich fuer alle Aufgaben T-1801 bis T-1827. Wer hier einen Begriff findet, verwendet
genau diesen und keinen anderen. Fehlt ein Begriff, wird er hier ergaenzt, bevor er in den
Code geht.

Stand: 2026-09-20 (T-1800). Quellenstand der Erhebung: 85d13b0.
Sprachvariante: **US-Englisch** (catalog, center, analyze, meter, color).

## Aufbau und maschinelle Pruefung

Jeder Abschnitt besteht aus Tabellen der Form `| Deutsch | English | Anmerkung |`.
`tools/check_ui_lang.py` (T-1805) liest ausschliesslich diese Tabellen: die erste Spalte
liefert die Liste der deutschen Begriffe, deren Auftreten in HTML/JS-Bloecken einen Treffer
erzeugt; die zweite Spalte liefert den erlaubten englischen Wortschatz. Zeilen ausserhalb
von Tabellen sind Kommentar und werden ignoriert. Eine Zelle darf mehrere durch Komma
getrennte Varianten enthalten.

Seit T-1826a sucht das Werkzeug nur in Anzeigetext (HTML-Textknoten, title,
placeholder, aria-label, alt, label, String-Literale im Skript); Bezeichner und
CSS-Klassen zaehlen nicht. Ein deutscher Begriff, der als Wort in der
Englisch-Spalte irgendeiner Zeile steht, wird nicht gesucht.

## Schreibweisen

| Regel | Festlegung |
|---|---|
| Beschriftungen, Buttons, Spaltenkoepfe | Sentence case: nur das erste Wort und Eigennamen gross ("Save as JPEG", "Apply range", "Center frequency") |
| Ueberschriften | ebenso Sentence case |
| Einheiten | mit schmalem Abstand zur Zahl, Einheitenzeichen unveraendert: Hz, kHz, MHz, dBuV, dBFS, Bd, s, ms, %, GiB |
| dBuV | im Anzeigetext als dB&micro;V (echtes Mikrozeichen), in Fehlertexten ebenso - die bisherige Mischung aus dBµV und dBuV entfaellt |
| Dezimaltrennzeichen | Punkt. Kein Tausendertrennzeichen in Eingabefeldern; in reinen Anzeigezahlen ueber 9999 ein Komma. Formatierung ueber `toLocaleString('en-US', ...)`; Textsortierung laeuft ueber den gemeinsamen Helfer `cmpText` aus `eb200_core/ui_theme.py` (T-1803c) |
| Datum und Uhrzeit | ISO-nah: `2026-09-20 14:07:33`. UTC wird immer explizit angehaengt, lokale Zeit nie. Formatierung laeuft ueber die gemeinsamen Helfer `isoDate`/`isoTime`/`isoHM`/`isoDateTime` aus `eb200_core/ui_theme.py`; `toLocale*` kommt in der Oberflaeche nicht mehr vor (T-1803b) |
| Dauer | `5 s`, `2:05 min`, `1:02:05 h` (Format bleibt, nur die Einheitenkuerzel sind ohnehin sprachneutral) |
| Abkuerzungen | erst ausschreiben, dann Kurzform in Klammern; danach Kurzform allein |

---

## 1 Datenobjekte

| Deutsch | English | Anmerkung |
|---|---|---|
| Aufzeichnung | recording | Oberbegriff jeder aufgezeichneten Messung |
| Aufzeichnungen | recordings | Menuepunkt (:8050), T-1812c |
| Aufnahme | recording | **Vereinheitlichung**, siehe Abschnitt 10.1 |
| Lauf | run | |
| Job, Long-Run-Job | job, long-run job | **Vereinheitlichung** mit "Lauf", siehe 10.2 |
| Startzeit | start time | T-1813a |
| Job angelegt | job created | T-1813a |
| geplanter Start | scheduled start | T-1813a |
| laeuft seit | running since | T-1813a |
| bis Stopp | until stopped | T-1813a |
| Long-Run beendet | long run finished | T-1813a |
| wartet auf Controller | waiting for controller | Phase wartet, vgl. EB200_LABELS.phase, T-1813a |
| Rueckbau | finishing | Phase ende, vgl. EB200_LABELS.phase, T-1813a |
| Scan-Definition, MSCAN-Job (Definition) | scan definition | **Vereinheitlichung**, siehe 10.3 |
| Session | session | bleibt |
| Segment | segment | |
| Sweep | sweep | bleibt |
| Frame | frame | bleibt |
| Framerate | frame rate | T-1812b |
| Kanal (Scan-Schritt) | channel | |
| Kanal (Katalogeintrag) | channel entry | nur wo die Unterscheidung noetig ist |
| Sender (Katalogtreffer) | station | "n Sender auf X MHz" -> "n stations on X MHz" |
| ausserhalb der Region | outside the region | Katalogstreifen, Regionsfilter, T-1811 |
| FNV | FNV | Oesterreichische Vorschrift - bleibt unuebersetzt, T-1811 |
| Kanalgruppe | channel group | Erzeugungsvorschrift fuer viele Kanaele; der Datenwert heisst raster |
| Raster (Parameter) | grid | Rasterart -> grid type, Schrittweite -> grid spacing |
| Raster (Anzeigegitter) | grid | ABGRENZUNG: das Frequenzraster des Katalogs heisst channel group, siehe Abschnitt 10, T-1812c |
| Band | band | |
| Eintrag | entry | |
| Katalog, Frequenzkatalog | catalog, frequency catalog | US-Schreibweise, nicht catalogue |
| Katalogdatei, Dateiname, Datei | catalog file | **Vereinheitlichung**, siehe 10.5 |
| Datei-Kopf | file header | |
| Voreinstellungen | defaults | |
| Marker, Auto-Marker | marker, auto marker | bleibt |
| Klick-Marker | click marker | deutsche Bindestrichfuegung wird im Englischen zweiwortig, T-1812c |
| Markerliste | marker list | |
| Befund | finding | Plural findings |
| Kurztext, Titel, Label (Freitextfeld) | label | **Vereinheitlichung**, siehe 10.6 |
| Notiz | note | vom Label getrennt, bleibt getrennt |
| Sidecar | sidecar | bleibt |
| Streifen (Katalogstreifen) | strip | known strip |
| Manifest | manifest | |
| Historie, Historienpuffer | history, history buffer | |
| Zwischenablage | clipboard | T-1812e |
| Duplikatpruefung | duplicate check | T-1812e |
| Aufbereitung | preparation | Reiter auf :8050, Worker auf :8090 |
| Emitter | emitter | |
| Kandidat (Modenkennung) | candidate | |
| Geschwisterdateien | sibling files | |

## 2 Messgroessen und HF-Begriffe

| Deutsch | English | Anmerkung |
|---|---|---|
| Bandbreite, BW, ZF-BW, IF-Bandbreite | bandwidth, BW | **Vereinheitlichung**, siehe 10.4. "IF bandwidth" (kurz "IF BW") nur wo wirklich die ZF gemeint ist - Beispiel: Long-Run-Panel IFPAN-Bandbreitenfeld, T-1812b |
| Bereich (Start/Stopp-Paar) | range | |
| Kanalabstand | channel spacing | T-1812d |
| Spanne, Breite, Span, Frequenzspanne | span | **Vereinheitlichung**, siehe 10.4 |
| Mittenfrequenz | center frequency | US-Schreibweise |
| Komma (Dezimaltrenner) | decimal point | Festlegung aus T-1803a: Punkt trennt Dezimalstellen, T-1812b |
| Frequenz, Startfrequenz, Stoppfrequenz | frequency, start frequency, stop frequency | |
| Breitband-Scan | wideband scan | Untertitel der D-SCAN-Ansicht, T-1812b |
| Schrittweite | step size | im Katalograster: grid spacing |
| Verstaerkung | gain | |
| Regelung | control | Betriebsart der Verstaerkungsregelung, AGC/MGC, T-1812d |
| Verstaerkungsregelung | gain control | AGC/MGC bleiben |
| Pegel | level | doppeldeutig dBuV/dBFS, siehe 10.7 |
| Rauschflur, Rauschsockel | noise floor | **Vereinheitlichung** |
| Schwelle, Schwellwert | threshold | |
| Squelch | squelch | bleibt, "Rauschsperre" entfaellt |
| Squelch ein | squelch on | Squelch bleibt unuebersetzt, T-1812e |
| stummgeschaltet | muted | T-1812e |
| Demodulation, Modulation | demodulation, demod | **Vereinheitlichung**, siehe 10.4 |
| Speed, Geschwindigkeit | speed | **Vereinheitlichung** |
| Messzeit, MTIME | measurement time, MTIME | |
| Verweildauer, Dwell-Zeit | dwell time | |
| Sweep-Dauer | sweep duration | T-1813b |
| aus Sweep gemessen | measured from sweep | T-1813b |
| Nachlaufzeit, Hold-Zeit | hold time | |
| Abstimmzeit | tuning time | T-1812d |
| Handbuch | manual | Verweis auf das Geraetehandbuch; Annex C bleibt unuebersetzt, T-1812d |
| Mindestdauer | min. duration | T-1812c |
| Detektor | detector | PEAK/AVG/FAST bleiben |
| Dezimierung | decimation | |
| Rotation | rotation | |
| Schaetzung | estimate | T-1813a |
| Planungswert | planning value | T-1813a |
| schaetzungsweise | approximately | T-1813b |
| Skala | scale | |
| Skala min / Skala max | scale min / scale max | Farbskala des Wasserfalls, T-1812e |
| Frequenzraster | frequency grid | Gitterlinien im Wasserfall |
| Raster-Einstellungen | grid settings | Popover am Grid-Knopf, T-2011 |
| Deckkraft | opacity | T-2011 |
| gestrichelt | dashed | T-2011 |
| Zeitlinien | time lines | waagrechte Rasterlinien mit Uhrzeit, T-2011 |
| Beschriftungslage | label position | Werte left, center, right; T-2011 |
| Label-Abstand | label spacing | Mindestabstand der Marker-Chips, T-2012 |
| Zeitmarke | time marker | |
| Endzeitpunkt | end time | T-1812b |
| Peak-Hold | peak hold | bleibt |
| Bin | bin | bleibt |
| Perzentil | percentile | |
| Luecke | gap | |
| Paket | packet | |
| Belegung | occupancy | |
| Verlust | loss | |
| Zyklen | cycles | MSCAN-Durchlaeufe, T-1813a |
| Audio-Fehler | audio error | T-1813a |
| Duty | duty | bleibt |
| Dauertraeger | continuous carrier | |
| Bursthafte Kanaele | bursty channels | |
| Staerkste Ereignisse | strongest events | |
| Ereignis | event | |
| Kamm | comb | |
| Periodik, Periode | periodicity, period | |
| Regelmaessigkeit | regularity | |
| Mess-Artefakt | measurement artifact | T-1829 |
| kanalisierte Aktivitaet | channelized activity | T-1829 |
| Amateurfunk | amateur radio | Katalogtext, T-1823a |
| Zeitzeichen | time signals | Katalogtext, T-1823a |
| Normalfrequenz | standard frequency | Katalogtext, T-1823a |
| Seenot | maritime distress | Katalogtext, T-1823a |
| Funkruf | paging | Katalogtext, T-1823a |
| Empfangskette | receive chain | |
| Empfangsparameter | reception parameters | |
| Empfaenger | receiver | T-1812b |
| Antenne | antenna | |
| Dienst, Dienstname | service, service name | |
| Zuweisung | allocation | |
| Dienstebene, Zuweisungsebene | service layer, allocation layer | Datenwerte dienst/zuweisung, siehe 4 |
| Feinabstimmung | fine tuning | |
| S-Meter | S meter | bleibt |
| Hoechstwert | peak value | T-1812e |
| Spektrum | spectrum | |
| Lupe | waterfall | Arbeitsname der Seite :8000 aus WS3, T-1827 |
| Spektrogramm, Wasserfall | waterfall | **Vereinheitlichung**, siehe 10.8 |
| Audiobilanz | audio balance | |

## 3 Signalvermessung und Modenkennung (Feldtabellen :8070)

Diese 27 Bezeichnungen stehen in `M_FELDER_FSK`, `M_FELDER_OFDM` und `M_FELDER_PSK` in
analyze_audio.py und sind woertlich festgelegt.

| Deutsch | English | Anmerkung |
|---|---|---|
| Pegel | level | |
| Symbolrate (Bd) | symbol rate (Bd) | |
| Modulationsindex | modulation index | |
| Guete r&sup2; | fit quality r&sup2; | |
| Standardrate | standard rate | |
| Hub (Hz) | deviation (Hz) | FSK-Frequenzhub |
| Traegerversatz (Hz) | carrier offset (Hz) | |
| Traegerabstand (Hz) | carrier spacing (Hz) | |
| Augenoeffnung (%) | eye opening (%) | |
| Jitter (%) | jitter (%) | |
| Jitter (Halbbit-Raster) | jitter (half-bit raster) | |
| Rasterlage | timing phase | |
| Symbole | symbols | |
| Symboldauer (ms) | symbol duration (ms) | |
| Kamm im Spektrum (Hz) | spectral comb (Hz) | |
| Kurtosis | kurtosis | |
| Korrelationsgipfel | correlation peak | |
| Ueberhoehung | excess | |
| Ordnung (M) | order (M) | |
| Traeger (kHz) | carrier (kHz) | |
| EVM (%) | EVM (%) | bleibt |
| Phasenrauschen (&deg;) | phase noise (&deg;) | |
| Amplitudenstreuung (%) | amplitude spread (%) | |
| Burstdauer (s) | burst duration (s) | |
| Augendiagramm | eye diagram | |
| Konstellation | constellation | |
| Tonabstand | tone spacing | |

Satzmuster der Kandidatenanzeige (Leitsatz Meilenstein 17: die Anzeige behauptet nie mehr,
als die Messung hergibt):

| Deutsch | English |
|---|---|
| Bekannte Dienste: | Known services: |
| keine Zuordnung moeglich | no match possible |
| Keine Uebereinstimmung in der Modentabelle. | No match in the mode table. |
| Hub {x} Hz (Soll {y} &plusmn; {z}) | Deviation {x} Hz (expected {y} &plusmn; {z}) |
| Symbolrate {x} Bd (Soll {y} &plusmn; {z} %) | Symbol rate {x} Bd (expected {y} &plusmn; {z} %) |
| Tonabstands-Streuung {x} % (Grenze {y} %) | Tone spacing spread {x} % (limit {y} %) |
| Symbolrate nur grob bestimmt ... entsprechend unsicher | Symbol rate is only coarsely determined; candidates are correspondingly uncertain |

## 4 Datenwerte und ihre Anzeige (Grundlage fuer T-1801)

Die persistierten Werte bleiben unveraendert (ID-Stabilitaet). Uebersetzt wird nur die
Anzeige ueber `labelOf()`.

| Feld | Wert (persistiert) | Anzeige |
|---|---|---|
| zustand | geplant | Scheduled |
| zustand | laeuft | Running |
| zustand | beendet | Finished |
| zustand | gestoppt | Stopped |
| zustand | abgebrochen | Aborted |
| zustand | verpasst | Missed |
| zustand | verwaist | Orphaned |
| zustand | unbekannt | Unknown |
| art | dscan, ifpan, mscan, audio | D-SCAN, IFPAN, MSCAN, Audio |
| herkunft | direkt | Direct |
| herkunft | longrun | Long run |
| mode (MSCAN) | inventur | Inventory |
| mode (MSCAN) | ueberwachung | Surveillance |
| phase | wartet | Waiting |
| phase | programmieren | Programming |
| phase | scan | Scanning |
| phase | ende | Complete |
| klasse | LEER | Empty |
| klasse | RAUSCHEN | Noise |
| klasse | CW | CW |
| klasse | DIGITAL | Digital |
| klasse | SPRACHE | Voice |
| klasse | UNKLAR | Unclear |
| sicherheit | normal | Normal |
| sicherheit | schwach | Weak |
| layer | zuweisung | Allocation |
| layer | dienst | Service |
| confidence | match, near | Match, Near |
| Empfangskette-Chip | auto, ein, aus, n/a | Auto, On, Off, n/a |
| level (Befund) | sicher | Certain |
| level (Befund) | wahrscheinlich | Probable |
| level (Befund) | moeglich | Possible |
| bw_class | schmal | Narrow |
| bw_class | kanal | Channel |
| bw_class | breit | Wide |
| period_class | schnell | Fast |
| period_class | sekunden | Seconds |
| period_class | minuten | Minutes |
| kind (Katalog) | channel | Single channel |
| kind (Katalog) | raster | Channel group |
| sched_phase | check | Checking |
| sched_phase | start_dscan | Starting D-SCAN |
| sched_phase | start_ifpan | Starting IFPAN |
| sched_phase | start_mscan | Starting MSCAN |
| sched_phase | stop_finished | Stopping (finished) |
| sched_phase | stop_aborted | Stopping (aborted) |
| abort_code | server_restart | Server restart |
| abort_code | server_stop | Server stop |
| abort_code | receiver_busy | Receiver busy |
| abort_code | recording_error | Recording error |
| abort_code | antenna_unknown | Antenna not detectable |
| abort_code | program_failed | Programming failed |
| abort_code | udp_bind_failed | UDP bind failed |
| abort_code | scpi_error | SCPI error |
| abort_code | bin_record_failed | Bin recording could not be started |
| abort_code | stop_while_programming | Stopped while programming |
| abort_code | unexpected_exit | Unexpected exit |
| abort_code | geometry_changed | Geometry changed |
| err_code | outside_storage | Path outside the allowed storage locations |
| err_code | wrong_extension | Wrong file type |
| err_code | longrun_part | Part of a long-run job, handle it via job management |
| err_code | may_be_running | May still be running |
| err_code | incomplete | Still running or incomplete |
| err_code | no_sidecar | No sidecar file |
| err_code | sidecar_unreadable | Sidecar unreadable |
| err_code | wrong_orphan_dir | Not a longrun_* directory |
| err_code | no_storage_root | Directory is not under any storage root |
| err_code | known_job_dir | Directory belongs to a known job, delete it as a job |
| err_code | recently_written | Directory was written to recently |
| err_code | unknown_kind | Unknown kind |
| err_code | sigmf_iq_only | SigMF export is available for IQ recordings only |
| err_code | not_found | Recording not found |
| err_code | orphan_no_label | Orphaned directories have no label |
| err_code | sigmf_export_failed | SigMF export failed |
| err_code | no_index_entry | No entry in jobs/index.json |
| err_code | unexpected_format | Unexpected format |
| err_code | session_db_unreadable | Session database unreadable |

`verdict` aus session_analyze.py ist **kein** Aufzaehlungswert, sondern ein serverseitig
zusammengesetzter deutscher Satz, der in :8020 und :8040 roh angezeigt wird. Er laesst
sich nicht ueber labelOf() abbilden und wird in T-1802 auf Code plus Parameter
umgestellt. `regularity` ist eine Zahl und bleibt unveraendert.

## 5 Bedienelemente und Aktionen

| Deutsch | English | Anmerkung |
|---|---|---|
| Anlegen, Neu | Create | **Vereinheitlichung**, siehe 10.9 |
| Job anlegen | create job | T-1812b |
| Long-Run-Job anlegen | create long-run job | Titel der Maske, steht im CSS als content:, T-1812b1 |
| Speichern | Save | nur fuer das Sichern vorhandener Objekte |
| Loeschen | Delete | |
| Loeschen (Anzeige zuruecksetzen) | clear | ABGRENZUNG: Delete entfernt Daten, Clear setzt nur eine Anzeige zurueck - Wasserfall-Historie, Markerliste, Eingabefelder. T-1812c |
| Endgueltig loeschen | Delete permanently | wo Messdaten mit verschwinden |
| Abbrechen | Cancel | |
| Fortfahren? | Continue? | Rueckfrage, T-1813b |
| Verwerfen | Discard | |
| Stoppen | Stop | |
| Uebernehmen (Bereich, Werte) | Apply | |
| In Job uebernehmen | Add to job | |
| Bearbeiten | Edit | |
| Vorschau | Preview | |
| Importieren, Exportieren | Import, Export | |
| Herunterladen | Download | |
| Wiedergabe, Anhoeren | Play | **Vereinheitlichung**, siehe 10.10 |
| Pause, Weiter | Pause, Resume | |
| Mithoeren | Monitor | Live-Audio waehrend des Empfangs |
| Zum Anfang | To start | |
| Ein Frame zurueck, vor | Previous frame, Next frame | |
| Tempo | Speed | Wiedergabegeschwindigkeit |
| Zoomen, Zoom zurueck | Zoom, Zoom out | |
| Ganzes Band | Full band | |
| Schwenken | Pan | |
| Markieren | Mark | |
| Anheften | Pin | |
| Kontrast spreizen | Stretch contrast | |
| Zuruecksetzen | Reset | |
| Panel einklappen | collapse panel | T-1812d |
| Scan-Bereich | scan range | T-1812d |
| Bereich uebernehmen | apply range | T-1812d |
| Bereich unveraendert | range unchanged | T-1813b |
| nichts zu tun | nothing to do | T-1813b |
| Zusatz-Zoom | extra zoom | T-1812d |
| Zoom-Fenster (BW-ZOOM) | zoom window | Vorschau in den D-SCAN-Einstellungen, T-2008 |
| Long-Run-Einrichtung | Long Run setup | Knopf, oeffnet die Long-Run-Maske, T-2008 |
| ausschalten | disable | Gegenstueck zu einschalten -> enable, T-1813b |
| gesperrt | locked | Besitzzustand: ein Job haelt den Empfaenger, T-1813c |
| Abgelehnt | rejected | Server weist eine Anfrage zurueck, T-1813c |
| ungueltige Eingabe | invalid input | T-1813c |
| Feinabstimmung | fine tuning | T-1813c |
| Frequenzeingabe | frequency input | T-1813c |
| keine gueltige Zahl | not a valid number | T-1813c |
| ausserhalb | outside | T-1813c |
| eingeschaltet | on | Zustand eines Schalters, nicht "switched on", T-1813c |
| am Frontpanel | at the front panel | T-1813c |
| letztes Frame | last frame | T-1813c |
| Weiter (nach Pause) | resume | Gegenstueck zu Pause, nicht "continue", T-1813d |
| nicht verfuegbar | not available | T-1813d |
| nicht erreichbar (Dienst) | not reachable | einheitlich mit der Ablagenliste, T-1813d |
| Aufnahme laeuft | recording | Statuszeile, T-1813d |
| Session beendet | session finished | T-1813d |
| Aufnahme beendet | recording finished | T-1813d |
| Mithoeren (Knopf) | monitor | Handlung, T-1813d |
| Mithoeren (Zustand) | monitoring | "Monitoring is on/off", T-1813d |
| Klick zum Start | click to start | T-1813d |
| Verbindung unterbrochen | connection lost | T-1813d |
| Geraeteverbindung | connection | Block auf der Statusseite, T-2013 |
| Verbindung testen | test connection | T-2013 |
| Lokale Adresse | local address | Adresse dieses PCs fuer die UDP-Daten, T-2013 |
| wirkt nach Neustart | takes effect after restarting | T-2013 |
| Waehle ... | select ... | T-1812d |
| Laufend | running | Abschnitt der Jobliste, wortgleich mit EB200_LABELS.zustand, T-1814b |
| Geplant | scheduled | dito, T-1814b |
| lade ... | loading ... | Ladehinweis, T-1814b |
| Scan-Definition | scan definition | MSCAN-Vorlage, aus der Laeufe entstehen, T-1814b |
| Neue Definition | new definition | T-1814b |
| Aufbereitung | preparation | Dienst preparation_worker, T-1814b |
| Letzter Durchlauf | last cycle | T-1814b |
| Verlauf | history | T-1814b |
| Ergebnis | result | T-1814b |
| Quelle | source | T-1814b |
| geschaetzt | estimated | T-1814b |
| Notiz | note | T-1814b |
| Voice-Aufnahme | voice recording | MSCAN behaelt nur Aufnahmen mit Sprache, T-1814c |
| Klicken zum Bearbeiten | click to edit | T-1814c |
| ohne Kurztext | no label | leeres Freitextfeld, T-1814c |
| kein laufender Job | no job running | T-1814c |
| keine geplanten Jobs | no scheduled jobs | T-1814c |
| ohne Lebenszeichen | no sign of life | Scheduler-Warnung, T-1814c |
| VERPASST | MISSED | Abzeichen, Versalform von EB200_LABELS.zustand verpasst, T-1814c |
| Jetzt nachholen | retry now | wortgleich mit /api/retry und ERR_RETRY_NOT_MISSED, T-1814c |
| Stopp (Endzeitangabe) | stopped | ABGRENZUNG: der Knopf "Stoppen" heisst Stop, T-1814c |
| offen (bis Stopp) | open (until stopped) | Job ohne festes Ende, T-1814c |
| Art (Aufzeichnungsart) | kind | einheitlich mit r.loesch.kind und /api/deleterecording, T-1814d |
| Umfang | size | Spalte mit der Byte-Groesse, T-1814d |
| Inhalt | content | Sammelspalte der Aufzeichnungstabelle, T-1814d |
| ungueltig oder unvollstaendig | invalid or incomplete | T-1814d |
| Segmente | segments | T-1814d |
| endgueltig loeschen | delete permanently | T-1814d |
| Nicht loeschbar | cannot be deleted | T-1814d |
| Laufende Aufnahme | recording in progress | T-1814d |
| Metadatei | metadata file | T-1814d |
| Datenkopie | data copy | T-1814d |
| Achtung | warning | Einleitung eines Warnhinweises, T-1814d |
| SigMF+Daten | SigMF+data | Knopfbeschriftung, Formatname bleibt, T-1814d |
| aktive K. | active ch. | Spaltenkopf, Abkuerzung bleibt Abkuerzung, T-1814e |
| Alle (Filter) | All | ABGRENZUNG: der Wert 'alle' bleibt deutsch, T-1814e |
| von (Trefferzaehler) | of | "12 of 40", T-1814e |
| keine Aufzeichnungen | no recordings | T-1814e |
| kein Kurztext | no label | T-1814e |
| geloescht | deleted | T-1814e |
| ENDGUELTIG | PERMANENTLY | Versalschreibung bleibt - sie ist die Warnung, T-1814e |
| Papierkorb | recycle bin | T-1814e |
| weitere anzeigen | show ... more | Wortfolge wechselt: Zahl vorn wird Verb vorn, T-1814e |
| Messdaten | measurement data | T-1814e |
| Titel | title | T-1814f |
| Notiz | note | einheitlich mit dem Filterfeld aus T-1814b, T-1814f |
| Erstellt | created | T-1814f |
| Geaendert | modified | folgt dem Datenfeld modified_at, nicht "changed", T-1814f |
| Letzter Lauf | last run | T-1814f |
| Laeufe | runs | Ausfuehrungen einer MSCAN-Definition, T-1814f |
| Bearbeiten | edit | T-1814f |
| Sockel (Plattenplatz) | floor | unantastbarer Rest, Feld floor_bytes, T-1814g |
| Reserve | reserve | T-1814g |
| verfuegbar | available | T-1814g |
| Platz wird knapp | space is getting tight | T-1814g |
| Platzlage | space status | T-1814g |
| aufbereitet | prepared | Zustand einer Datei nach dem Bau, T-1814g |
| zurueckgestellt | deferred | T-1814g |
| fehlgeschlagen | failed | T-1814g |
| geprueft | scanned | folgt dem Feld scanned, T-1814g |
| offen (noch zu tun) | pending | NICHT "open" - das hiesse geoeffnet, T-1814g |
| Takt | interval | folgt dem Feld scan_interval_s, T-1814g |
| baut gerade | building now | T-1814g |
| kein Bau im Gange | nothing being built | T-1814g |
| Zeitbudget ausgeschoepft | time budget exhausted | T-1814g |
| noch nichts geschehen | nothing has happened yet | T-1814g |
| Format nicht unterstuetzt | format not supported | T-1814g |
| wird anderswo gebaut | being built elsewhere | zweiter Worker haelt die Sperre, T-1814g |
| Belegung erheben | survey occupancy | MSCAN-Inventur, T-1814h |
| Hoerprobe | listening sample | T-1814h |
| Durchgang | cycle | ein Durchlauf aller Kanaele, T-1814h |
| Zyklusende-Marker | end-of-cycle marker | T-1814h |
| bis Laufende/Stopp | until end of run or stop | "Laufende" = Ende des Laufs, T-1814h |
| Rauschsperre | squelch | T-1814h |
| Schwellwert | threshold | T-1814h |
| Mindestdauer Aufnahme | min. recording duration | Zusammensetzung aus Z. 119, T-1814h |
| unbegrenzt | unlimited | T-1814h |
| Sprechpausen | pauses in speech | T-1814h |
| Speichern und zur Liste | save and back to list | T-1814h |
| Kanaele hinzufuegen | Add channels | oeffnet den Kanalwaehler, ABGRENZUNG zu Z. 464, T-1814i |
| Uhrzeit (Auswahlknopf) | at time | Zeitpunkt statt Dauer, Start und Ende eines Laufs, T-1814i |
| Dauerangabe in Minuten (Auswahlknopf "Dauer (min)") | Duration (min) | Endezeile eines Laufs; nicht als "Dauer" gefuehrt, weil dauer auch Bezeichner ist (00-Konventionen.md §6), T-1814i |
| Endezeile im Laufformular (Beschriftung "Ende") | End | Gegenpart zu Start; nicht als "Ende" gefuehrt, sonst neue Funde ausserhalb des Auftrags, T-1814i |
| Lauf starten | Start run | T-1814i |
| Filter Label / Datei / Tags | Filter label / file / tags | Platzhalter der Kanalsuche, T-1814i |
| Keine Job-ID in der URL | No job ID in the URL | Ladefehler der Detailseite, T-1814j |
| Ein Durchgang: ca. | One cycle: approx. | Schaetzzeile, T-1814j |
| je Umschaltung | per channel change | T-1814j |
| bei Signal | when a signal is present | T-1814j |
| Gespeichert | Saved | mit Zeitstempel, T-1814j |
| fehlt im Katalog | missing from the catalog | Kanal ohne Katalogeintrag, T-1814j |
| aktive Kanaele | active channels | ABGRENZUNG: die Kurzform "aktive K." bleibt "active ch." (Glossarzeile `aktive K. | active ch.`), T-1814j |
| Abbruchgrund | Abort reason | T-1814j |
| Vorlauf/dazwischen/Nachlauf/gesamt | before/between/after/total | Tooltip der Spalte "verwaist"; Schraegstriche, damit daraus EIN Suchbegriff wird und nicht vier, T-1814j |
| reales Ende | actual end | Tooltip der Spalte "Ende", T-1814j |
| Lauf wirklich beenden? | Really stop this run? | T-1814j |
| Endet nach einem Zyklus | Ends after one cycle | T-1814j |
| keine Kanaele, keine Laeufe | no channels, no runs | Leerzeilen der Tabellen, T-1814j |
| Entfernen (Kanalzeile) | Remove | einzelne Zeile; "Markierte entfernen" bleibt "Remove selected", T-1814j |
| Gewaehlt (Zaehler) | Selected | Kopfzeile des Kanalwaehlers, T-1814k |
| nur gewaehlte | only selected | Filterkasten, T-1814k |
| noch nicht uebernommen | not applied yet | Hinweis auf ungespeicherte Auswahl, T-1814k |
| Alle sichtbaren markieren | Select all visible | Kopf-Ankreuzfeld der Tabelle, T-1814k |
| Kanaele sichtbar | channels visible | Trefferzeile des Filters, T-1814k |
| Maximum erreicht | Maximum reached | T-1814k |
| Kanalauswahl | channel selection | T-1814k |
| aus einem Raster erzeugter Kanal | channel generated from a channel group | Tooltip der Rasterspalte, T-1814k |
| Datei/Service | Catalog file/service | Dateifilter des Kanalwaehlers, T-1814k |
| Kanaele in MSCAN-Job | Channels into MSCAN job | Dokumenttitel der Importmaske, wortgleich mit header_html, T-1814l |
| neuer Job, bestehender Job | new job, existing job | Zielwahl der Importmaske; die Datenwerte neu/bestehend bleiben deutsch, T-1814l |
| unbekannte id | unknown ID | gross, anders als der Feldname id im Tabellenkopf, T-1814l |
| werden uebersprungen | skipped | T-1814l |
| Keine gueltigen Kanaele | No valid channels | T-1814l |
| Gefilterte an, Gefilterte aus | Select filtered, Deselect filtered | Knoepfe der Filterleiste im Kanalwaehler; ABGRENZUNG: "Alle/Keine in die Ansicht" (Show all/Show none) betrifft Kurven im Wasserfall, nicht Tabellenzeilen, T-1814n |
| Palette waehlen | Select palette | |
| Neu einlesen, Param-Datei neu lesen | Reload | **Vereinheitlichung** |
| Neu starten, Restart | Restart | **Vereinheitlichung** |
| Jetzt nachholen | Run now | |
| Beschriften | Label | Verb |
| Vermessen | Measure | |
| Befund merken | Save finding | |
| Auswertung | Analysis | Verweis auf den Auswerteserver |
| Auswertung (zusammengesetzt) | analysis | "Session Auswertung" -> "Session analysis", T-1812c |
| Filter, Suche, Sortieren | Filter, Search, Sort | |
| Einstellungen | settings | T-1812c |
| Ansichten | views | Menue weiterer MoniScope-Seiten, T-1812c |
| automatisch | automatic | T-1812c |
| Einstellungen ein-/ausblenden | Show/hide settings | |
| ein-/ausblenden | show/hide | Schraegstrichform, ohne Artikel: "Show/hide marker list", T-1812c |
| Als JPG speichern | Save as JPEG | |
| Alle in die Ansicht, Keine in die Ansicht | Show all, Show none | |
| Kanaele waehlen, Kanaele uebernehmen | Select channels, Apply channels | |
| Markierte entfernen | Remove selected | |
| Pfad oeffnen | Open path | |
| Zurueck zur Uebersicht | back to overview | T-1812e |
| Hell | Light | Beschriftung des Theme-Umschalters, T-1810 |
| Dunkel | Dark | Beschriftung des Theme-Umschalters, T-1810 |
| Auf helles Design umschalten | Switch to light theme | Titel des Umschalters, T-1810 |
| Auf dunkles Design umschalten | Switch to dark theme | Titel des Umschalters, T-1810 |
| Strg | Ctrl | Tastenname, T-1812b |
| Fenstergroesse | window size | T-1812b |
| Dateiname (der Name selbst) | catalog file name | ABGRENZUNG zur Vereinheitlichung "Katalogdatei, Dateiname, Datei -> catalog file": dort ist die DATEI gemeint, hier die Zeichenkette ihres Namens. "catalog file must not be empty" hiesse etwas anderes - naemlich dass die Datei keinen Inhalt hat. T-1815a |
| ist keine Zahl | is not a number | ABGRENZUNG zum Muster "<X> ist keine ganze Zahl -> <X> is not an integer": hier wird auf Gleitkomma geprueft, nicht auf Ganzzahl. T-1815a |
| oberste Ebene | top level | JSON-Struktur einer Katalogdatei, T-1815a |
| EB200-Filterstufe | EB200 filter step | T-1815a |
| Segmentliste | segment list | T-1815a |
| liegt nicht vollstaendig im Band | not completely inside the band | Segmentpruefung, T-1815a |
| erst leeren, dann loeschen | empty it first, then delete | Loeschsperre fuer nicht leere Katalogdateien, T-1815a |
| CSV-Vorlage | CSV template | Importvorlage: der CSV-Export einer leeren Katalogdatei (Kopfzeile allein), T-1815b |
| Datei (hochgeladene CSV) | CSV file | ABGRENZUNG zur Vereinheitlichung "Katalogdatei, Dateiname, Datei -> catalog file": 10.5 meint die Katalogdatei; eine hochgeladene CSV ist keine. T-1815b |
| Aktionen | Actions | Spaltenkopf, T-1815d |
| Ansichtsauswahl | view selection | welche Katalogdateien die Anzeige-Server zeigen, T-1815d |
| Katalogverzeichnis | catalog directory | T-1815d |
| Anzeige-Server | display servers | :8000/:8020/:8030/:8040, T-1815d |
| Fliese | tile | Kachel einer Katalogdatei in der Uebersicht, T-1815d |
| Ladefehler | load error | T-1815d |
| geerbt | inherited | Leer-Option und Platzhalter vererbbarer Felder ("(inherited: AM)"), T-1815e |
| Label-Vorlage | label template | Feld label_template einer Kanalgruppe, T-1815e |
| Betreuer | maintainer | Feld meta.maintainer einer Katalogdatei, T-1815f |
| Schliessen | Close | Knopf der Bearbeitungsblaetter und des Exportformulars; in T-1815d wegen einer Fehlzaehlung zurueckgestellt, T-1815g |
| Nur in Katalog | Catalog only | Knopf der Marker-Importmaske, Gegenstueck zu "Add to job", T-1815h |
| MSCAN-Jobverwaltung | MSCAN job management | der MSCAN-Teil von :8050, T-1815h |
| Datei (Aufnahmedatei) | file | ABGRENZUNG zur Vereinheitlichung "Katalogdatei, Dateiname, Datei -> catalog file": 10.5 meint die Katalogdatei; eine Aufnahme ist keine. Dateizeile der Metadatentafel in :8070, wie der Spaltenkopf in :8050, T-1816f |
| Trefferliste | hit list | Ergebnisseite eines MSCAN-Laufs (:8060), Verweis aus :8070, T-1816f |
| Eichdaempfer | attenuator | Tooltip des ATT-Chips der Empfangskette, T-1816f |
| Stille Kanaele | silent channels | Reiter der MSCAN-Ergebnisansicht (:8060): Kanaele des Snapshots ohne Aufnahme, T-1817b |
| Zeitverlauf | timeline | Reiter der MSCAN-Ergebnisansicht (:8060), Kanal-mal-Zeit-Matrix, T-1817b |
| Oeffnung, Oeffnungen (Squelch) | opening, openings | Zaehlgroesse der Pegel-Inventur: wie oft die Rauschsperre eines Kanals aufging, T-1817b |
| Erhebung (Pegel-Inventur) | survey | Kopfzeile eines Inventur-Laufs in :8060 ("level only, no audio recording"), T-1817c |
| verworfene Kurzsegmente | discarded short segments | Tooltip der Audiobilanz: Segmente unter der Mindestdauer, T-1817c |
| Fortsetzungen | resumptions | Tooltip der Audiobilanz: fortgesetzte Segmente, T-1817c |
| Programmierung (Speicherplaetze) | programming | Dauer der Speicherplatz-Programmierung vor dem Scan, T-1817c |

## 6 Ablage und Speicher

Drei Begriffe, die im Deutschen verwechselbar sind und im Englischen getrennt bleiben:

| Deutsch | English | Anmerkung |
|---|---|---|
| Ablage (Storage-Root) | storage location | Wurzelverzeichnis der Messdatenablage |
| Speicherung | storage | Themenbereich (Legende), Long-Run-Maske, T-1812b |
| Ordner (job_dir) | folder | konkretes Verzeichnis eines Laufs |
| Speicherplatz (EB200-Memory) | memory slot | **Geraetespeicher, nicht Plattenplatz** |
| Plattenplatz, freier Platz | disk space | |
| frei (Speicherplatz) | free | Ablagenliste, T-1813a |
| nicht erreichbar | not reachable | T-1813a |
| Sockel | floor | reservierter Mindestplatz |
| Reserve | reserve | |
| belegt, frei, verfuegbar | used, free, available | |
| Umfang | size | Datenmenge einer Aufzeichnung |
| Speicher (Parameterdatei) | parameter store | ABGRENZUNG: Speicherorte fuer Aufzeichnungen heissen weiterhin storage, T-1812a |
| Inventur | inventory | Geraeteabfrage der Parameterwerte, Status-Seite, T-1812a |
| Parameterwerte | parameter values | Status-Seite, T-1812a |
| Zuletzt geschrieben | last written | Status-Seite, T-1812a |
| Stand: | As of: | Zeitstempel einer Erhebung, T-1812a |

## 7 Befund-Navigator (:8020 und :8040, wortgleich)

| Deutsch | English |
|---|---|
| Befund-Navigator | Findings navigator |
| Dauertraeger | Continuous carriers |
| Bursthafte Kanaele | Bursty channels |
| Staerkste Ereignisse | Strongest events |
| Kamm | Comb |
| Periodik | Periodicity |
| Notiz | Note |
| Emitter | Emitter |
| Band (Rest) | Band (remainder) |
| Periode [s] | Period [s] |
| Zeit | Time |
| &ndash; noch nicht geladen &ndash; | &ndash; not loaded yet &ndash; |
| &ndash; wird analysiert &ndash; | &ndash; analyzing &ndash; |
| &ndash; keine &ndash; | &ndash; none &ndash; |
| keine feste Periode | no fixed period |
| wird analysiert | analyzing |
| Analyse fehlgeschlagen | analysis failed |

## 8 Satzmuster fuer Meldungen

Ab T-1804 gilt: je Aussage ein Formatstring. Diese Muster sind die erlaubten Formen.

| Deutsch (bisherige Formen) | English (verbindlich) |
|---|---|
| `<X> fehlt` | `<X> is missing` |
| `<X> fehlt oder ist ungueltig` | `<X> is missing or invalid` |
| `<X> darf nicht leer sein` | `<X> must not be empty` |
| `<X> muss zwischen A und B liegen` / `<X> ausserhalb A..B` | `<X> must be between A and B` |
| `<X> muss groesser als <Y> sein` | `<X> must be greater than <Y>` |
| `<X> muss kleiner als <Y> sein` | `<X> must be less than <Y>` |
| `<X> muss einer von (...) sein` | `<X> must be one of: ...` |
| `<X> unbekannt (erlaubt: ...)` | `unknown <X> (allowed: ...)` |
| `ungueltige(s) <X>` | `invalid <X>` |
| `<X> ist keine ganze Zahl` | `<X> is not an integer` |
| `genau eines von X/Y angeben` | `specify exactly one of X or Y` |
| `id '<x>' schon in <Datei> vergeben` | `id '<x>' already used in <file>` |
| `<X> existiert bereits` | `<X> already exists` |
| `<X> kann beim Bearbeiten nicht geaendert werden` | `<X> cannot be changed when editing` |
| `Zeile <n>: ...` / `Segment <n>: ...` | `line <n>: ...` / `segment <n>: ...` |
| `Spalte <x> muss leer sein` | `column <x> must be empty` |
| `Empfaenger gesperrt: Long-Run-Job laeuft` | `Receiver locked: long-run job running` |
| `Abgelehnt: <Grund>` | `Rejected: <reason>` |
| `<X> nicht erreichbar` | `<X> not reachable` |
| `Aufnahme laeuft moeglicherweise noch` | `recording may still be in progress` |
| `Sidecar fehlt` / `Sidecar unlesbar` | `sidecar missing` / `sidecar unreadable` |
| `wirklich loeschen?` | `Delete this permanently?` |

Plural: laeuft ueber `pluralWort(n, einzahl, mehrzahl)` aus `eb200_core/ui_theme.py`
(T-1804a); der Helfer liefert nur das Wort, bei der Uebersetzung wird lediglich das
Wortpaar getauscht, z. B. `pluralWort(n, "run", "runs")`. Konstruktionen wie
"Datei(en)" sind nicht mehr zulaessig.

## 9 Begriffe, die englisch bleiben

Squelch, sweep, span, marker, auto marker, session, segment, frame, bin, peak hold,
long run, job, label (intern), sidecar, duty, burst, comb, emitter, jitter, kurtosis, EVM,
zoom, pin, badge, chip, panel, toolbar, tab.

Geraete- und Verfahrensbezeichnungen: D-SCAN, IFPAN, MSCAN, BW-ZOOM, MTIME, AGC, MGC,
AFC, PEAK, AVG, FAST, LOW, NORM, HIGH, AM, FM, USB, LSB, CW, IQ, SDR, SigMF, FNV,
ICAO, WAV, JPEG, CSV, JSON, SCPI.

Einheiten: Hz, kHz, MHz, dB&micro;V, dBFS, Bd, GiB, MB, s, ms, &micro;s, %.

## 10 Vereinheitlichungen - hier aendert sich auch die Sache, nicht nur die Sprache

Diese elf Punkte beseitigen Uneinheitlichkeiten, die im Deutschen gewachsen sind. Jeder
Punkt ist eine Entscheidung, keine blosse Uebersetzung.

**10.1 Aufnahme und Aufzeichnung** werden beide `recording`. Die Spalte "Aufnahmen"
hatte zwei Bedeutungen: Dateianzahl in der allgemeinen Ansicht, behaltene Sprachaufnahmen
im MSCAN-Spaltensatz. Getrennt in `Files` und `Voice recordings`.

**10.2 Lauf und Job.** MSCAN sagt "Lauf", DSCAN/IFPAN sagt "Job" - technisch ist beides
ein Eintrag in job_store. Englisch: `run` fuer die Ausfuehrung, `job` fuer den geplanten
Auftrag. "Laeufe" (Reiter) wird `Runs`.

**10.3 Scan-Definition.** Die MSCAN-Definition heisst mal "MSCAN-Job", mal
"Scan-Definition". Englisch durchgaengig `scan definition`; der Reiter heisst
`Scan definitions`.

**10.4 Bandbreite, Span, Demodulation.** Vier Bezeichnungen fuer die Bandbreite
(Bandbreite, BW, ZF-BW, IF-Bandbreite) werden `bandwidth` / `BW`. Vier fuer die
Frequenzweite (Spanne, Breite, Span, Frequenzspanne) werden `span`; `range` bleibt dem
Start/Stopp-Paar vorbehalten. "Demodulation" und "Modulation" bezeichnen dasselbe Feld und
werden `demodulation`, kurz `demod`.

**10.5 Datei.** "Dateiname", "Katalogdatei" und "Datei" meinen dieselbe Sache:
`catalog file`.

**10.6 Kurztext und Titel.** Long-Run-Aufzeichnungen haben einen "Kurztext",
MSCAN-Definitionen einen "Titel" - beides ein Freitext-Namensfeld. Englisch beides `label`.
Die "Notiz" bleibt davon getrennt (`note`).

**10.7 Pegel.** Bleibt `level`, aber die Einheit steht immer dabei, weil dBFS (Datei) und
dB&micro;V (Empfaenger) im Deutschen beide "Pegel" heissen.

**10.8 Spektrogramm und Wasserfall.** Zwei Woerter fuer dieselbe Darstellung. Englisch
durchgaengig `waterfall`; `spectrogram` nur dort, wo das Rechenergebnis und nicht die
Anzeige gemeint ist (Cacheformat, Fortschrittsmeldung).

**10.9 Anlegen und Speichern.** Neuanlage heisst bei der Katalogdatei "Anlegen", bei
Kanal, Raster und Band "Speichern". Englisch: `Create` fuer neue Objekte, `Save` fuer
bestehende.

**10.10 Wiedergabe und Anhoeren.** Ein Konzept, zwei Verben. Englisch `Play`. "Mithoeren"
bleibt getrennt (`Monitor`), weil es Live-Audio meint, nicht Wiedergabe.

**10.11 Uebersicht und D-SCAN.** Dieselbe Ansicht heisst intern "Uebersicht", im
sichtbaren Text "D-SCAN". Englisch `D-SCAN` im sichtbaren Text, `overview` bleibt
Codebezeichner.

## 11 Was nicht uebersetzt wird

Kommentare, Docstrings, Variablen- und Funktionsnamen, Log- und Konsolenausgaben,
argparse-Hilfetexte, Selbsttests, PLAN.md, CLAUDE.md, bugs/. Die persistierten Datenwerte
aus Abschnitt 4 bleiben ebenfalls deutsch - uebersetzt wird nur ihre Anzeige.

Kommentare, die Oberflaechentexte zitieren. Kommentare bleiben deutsch, aber
ein woertliches Zitat einer Anzeigezeichenkette nennt die Zeichenkette, die
TATSAECHLICH erscheint - nach der Uebersetzung also die englische. Deutscher
Kommentar, englisches Zitat. Sonst zeigt die Dokumentation auf einen Text, den
es nicht mehr gibt. (T-1813b)

Anfuehrungszeichen in Kommentaren bedeuten viererlei, und nur eines davon wird
mituebersetzt:
  (a) ZITAT einer Anzeigezeichenkette - wird auf den englischen Text gebracht,
      damit der Kommentar auf etwas zeigt, das es gibt.
  (b) HERVORHEBUNG eines Begriffs in deutscher Prosa ("läuft"-Zustand) - bleibt
      deutsch. Kennzeichen: der Begriff steht in einem deutschen Satz und laesst
      sich nicht als Zeichenkette im Code wiederfinden.
  (c) HISTORISCHER VERWEIS auf einen frueheren Text ("vormals ...") - bleibt
      deutsch. Der alte Text WAR deutsch; eine Uebersetzung wuerde die Geschichte
      verfaelschen.
  (d) ZITAT einer technischen Zeichenkette - bleibt unveraendert. SCPI-Texte
      ("Span too low"), Feldnamen aus JSON oder Python ("phase", "path"),
      persistierte Datenwerte ("laeuft", "beendet"), CLI-Beispiele,
      Trennzeichen. Grund: das Zitierte wird NIE uebersetzt, weil es
      Geraetesprache, Protokoll oder gespeicherte Daten ist. Ein "laeuft" im
      Kommentar zeigt auf den Datenwert, nicht auf die Anzeige "Running".
      Mit 65 von 110 Fundstellen in waterfall.py ist das die haeufigste Art.
Im Zweifel entscheidet die Probe: laesst sich das Zitat als Zeichenkette im Code
finden, ist es (a) oder (d) - Anzeigetext gegenueber Geraete-/Protokoll-/
Datenwert entscheidet zwischen beiden. Laesst es sich NICHT finden, ist es (b)
oder (c) - eine mit "vormals" eingeleitete fruehere Fassung entscheidet fuer
(c). (T-1813e, T-1813e1)

Nationale Katalogtabellen (FNV_AT_2026, ORS_*, ukw_*, DAB_Band3, Air, UKW_BC,
fup_30_88_mhz, Amateurfunk_*) behalten ihre deutschen Dienstbezeichnungen; uebersetzt
werden `modes/fsk.json` und die `intl_*`-Kataloge (T-1823).

Interne Kennungen in der Oberflaeche. Task-, Werkstrom- und Fehlernummern
(T-..., WS..., Schritt ..., BUG-...) sind Quelltextsprache. Sie gehoeren in
Kommentare und in PLAN.md, nicht in Beschriftungen, Hinweistexte oder
title-Attribute. Beim Uebersetzen einer Stelle wird ein solches Kuerzel
ersatzlos entfernt, nicht mituebersetzt. (T-1812b1)