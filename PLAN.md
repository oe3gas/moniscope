# MoniScope — Entwicklungsplan

## Status-Legende
[ ] offen · [~] in Arbeit · [x] erledigt · [!] blockiert · [-] entfallen

## Meilenstein 1: Known-Frequencies-Streifen auf Analyse-Server & Katalog-Pflege
- [x] T-101 (2026-08-23) KNOWN_STRIPS-Konfigtabelle + Katalog-Streifen in analyze_sessions.py (:8020)
      übernehmen (byte-identischer JS-Block zu waterfall.py, Verifikation per Hash/Diff)
- [x] T-102 (2026-08-23) dito für analyze_spectrum.py (:8040)
- [x] T-103 (2026-08-23) dito für sdr_waterfall.py (:8030)
- [x] T-104 (2026-08-23) Abschlussprüfung: JS-Block in allen vier Servern byte-identisch
      (tools/sync_known_block.py --check)
- [x] T-105 (2026-08-23) Editierfunktion für die Known-Frequencies-JSON-Dateien (create/
      edit/delete für Bänder, Segmente, Kanäle, Raster): Ansiedlung in
      block_admin.py (:8010) als bestehendem JSON-Verwaltungsserver,
      alternativ eigener Server — endgültige Entscheidung im Plan Mode.
      Katalog-Reload der Anzeige-Server mitdenken.
- [x] T-106 (2026-08-23) Raster-Editor (Kanalgruppen) in block_admin.py (:8010):
      Formular mit Rasterart-Wahl (festes Raster/ICAO 8,33), Vorschau ohne
      Schreiben, Listen-Aktionen, Selftest T5. Band-Editor + Datei-Anlage-
      Komfort folgen als Session B.
- [x] T-107 (2026-08-24) Band-Editor in block_admin.py (:8010): bands-Tabelle je
      Sektion (rohe Datei-Sicht, sortierbar), Formular mit dynamischer
      Segmentliste (segments_json), Adressierung ueber Band-id,
      Loader-Regeln gespiegelt (stop streng groesser start, Segmente im
      Band), Selftest T6. Aus Session B noch offen: Datei-Anlage-Komfort.
- [x] T-108 (2026-08-24) Jobs-Uebersicht (:8050): endgueltiges Loeschen
      abgeschlossener Jobs samt Messdaten (JobStore.delete_with_data mit
      Storage-Root- und longrun_-Pfadwaechter, nur Endzustaende, Confirm
      mit Dateizahl/MB); Umfang in eigene numerisch sortierbare Spalten
      Sweeps/Frames/Dateien/MB aufgeteilt; alle Inhaltsspalten sortierbar.
- [x] T-109 (2026-08-24) Datei-Anlage-Komfort in block_admin.py (:8010):
      Datei-Kopf-Formular (meta: service/source/region/maintainer/Notiz,
      defaults: demod/bw/antenna/squelch/tags/notes) fuer Neuanlage und
      Bearbeitung (build_file_head, unbekannte Schluessel bleiben, note/
      notes-Schluessel des Bestands wird beibehalten); Loeschen nur leerer
      Dateien (delete_known_file inkl. .bak/.tmp); Landung auf
      /known?open=<datei>; Selftest T7/T8. Schliesst Session B ab.
- [~] T-110 (implementiert, Browser-Validierung offen) :8010 Katalog-Übersicht /known als
      Fliesen (Name, Dienst, Abdeckung, Bänder·Einträge) mit Bearbeitungsblatt als Overlay
      (Reiter: Bearbeiten | Datei-Kopf | JSON | Neuer Kanal | Neue Kanalgruppe | Neues Band –
      Formular-Reiter verlinken die bestehenden Seiten); Rücksprung aller Speichern/Abbrechen-
      Wege auf /known?open=<Datei> (Blatt öffnet sich wieder). Kein Datenmodell-/API-Umbau.
      Nachtrag 1 (2026-08-28): Katalog auf :8010-Wurzel (Titel „Frequenzkataloge", /known → Redirect),
      Fliesen: Name/Dienst/Pills; Blatt-Reiter nahtlos (Formulare eingebettet, Abbrechen = Reiterwechsel);
      Rücksprünge auf /?open=<Datei>.
      Nachtrag 2 (2026-08-28): „Eintrag bearbeiten" (Kanal/Kanalgruppe/Band) im
      Blatt-Overlay via fetch + embed=1-Fragment; Formularseiten bleiben als Fallback.
- [x] T-111 (2026-08-29) Katalog-Abgleich gegen FNV: Werkzeug (tools/known_freq_abgleich.py,
      --entscheidungen fuer dokumentierte Band-Entscheidungen) + Bericht
      (docs/known_freq_abgleich.md); --apply mit docs/known_freq_abgleich_entscheidungen.json
      ausgefuehrt: 22 Baender geloescht, ham-6m nach intl_amateurfunk_baender.json
      verschoben, vhf_137_180_mhz.json (dadurch leer) entfernt. FNV-Titel um
      Filterreste bereinigt (8 Treffer). Katalog danach: 4191 Kanaele,
      171 Baender, 0 Fehler.
      Nachtrag (2026-08-29, gefunden bei T-113): _self_test_real() in
      eb200_core/known_freq.py pruefte noch die geloeschten Baender bc-fm/
      air-nav/air-com im Bereich 80-150 MHz - Selftest war seit T-111 rot.
      Assertion auf fnv-87500/-108000/-117975 (decken denselben Bereich als
      FNV-Zuweisungsband ab) und ham-2m umgestellt.
- [x] T-112 (2026-08-29) Wiener UKW-Doppelungen: ORS-Eintraege uebernehmen
      Alt-IDs, ukw_wien in UKW_BC eingezogen. Werkzeug tools/known_freq_wien_merge.py
      (Zuordnungstabelle 14 Paare, --apply) laesst jede Alt-id (bc-wien-*, fm-wien-*,
      bc_OE1/bc_FM4/Radio_NOE) in ORS_UKW_AT.json weiterleben (id-Uebernahme durch
      den ORS-Eintrag, legacy_id_from/legacy_label, notes/tags vereinigt) und
      loescht den doppelten Alt-Eintrag; Sonderfall bc_FM4 (Tippfehler 98,9 statt
      98,8 MHz im Alt-Eintrag) einzeln geprueft. Zusaetzlich CZ_RadioZurnal aus
      UKW_BC.json nach ukw_cz.json verschoben (falsche Datei). Die restlichen
      5 Sender aus ukw_wien.json (ohne ORS-Bezug) in UKW_BC.json aufgenommen,
      ukw_wien.json ueber block_admin.delete_known_file entfernt; UKW_BC.json
      meta auf "UKW Wien (nicht-ORS)" umbenannt. Katalog danach unveraendert:
      4177 Kanaele, 171 Baender, 0 Fehler (Umbenennung/Verschiebung aendert die
      Kanalzahl nicht). Repo-weiter Grep bestaetigte reale Scan-Set-Referenzen
      auf mehrere Alt-ids (jobs/mscan/*.json, records/*/job.json) - der Grund
      fuer die Uebernahme durch den ORS-Eintrag statt umgekehrt.
- [x] T-113 (2026-08-30) Streifen in zwei Ebenen (FNV/Realitaet). Browser-validiert;
      Nachtrag: keine Segmenttitel in Reihe 1, Tooltip zweiebenig, Region-Label-
      Ausblendung, station.json am Repo-Root.
      eb200_core/known_freq.py: Datei-Meta "layer" ("zuweisung"/"dienst", Vorgabe "dienst"),
      an jedes Band durchgereicht; FNV_AT_2026.json auf "zuweisung" gesetzt. Streifen-Canvas
      34 -> 46 px, drei Reihen: Reihe 1 (y 0-9) FNV-Zuweisungsebene mit globalAlpha 0.55,
      Bandtitel IMMER (knownFitText kuerzt statt wegzulassen), Segmente als heller Marker an
      der Unterkante statt Titelersatz, FNV-Einzelfrequenzen (Tag "fnv") als eigene Ticks;
      Reihe 2 (y 11-24) alle uebrigen Baender wie bisher, ueberlappende Baender im sichtbaren
      Bereich auf zwei Spuren verteilt (Cluster/greedy, ab dem dritten Ueberlapp teilt sich
      Spur 1); Reihe 3 (ab y 26) Kanaele. Gates gruen (sync_known_block --check, node --check,
      known_freq --selftest, selftest_known_edit.py); Logik (knownFitText, Regionsfilter,
      Cluster/Spuren) per Node-Testharness auf dem extrahierten Block verifiziert.
      Nachtrag (2026-08-30, Browser-Validierung): Segmenttitel in Reihe 1 ueberlagerten sich
      mit dem Bandtitel in den 9 px der Reihe (Befund 200-230 MHz) - Segmentmarker jetzt ohne
      eigenen Titel, dafuer nennt der Tooltip (knownTextAt) ALLE an der Frequenz beteiligten
      Baender (FNV-Baender zuerst, Praefix "FNV: ", mehrere durch " | " getrennt).
- [x] T-114 (2026-08-30) Regionsfilter station.json. Browser-validiert; Nachtrag:
      keine Segmenttitel in Reihe 1, Tooltip zweiebenig, Region-Label-Ausblendung,
      station.json am Repo-Root.
      station.json (Repo-Root, nicht versioniert, Beispiel station.example.json) mit
      region_tags; eb200_core/known_freq.py load_station_config() (fehlende Datei/defektes
      JSON -> Filter aus + Warnung), to_client_json(station) liefert "region": {"tags": [...]}.
      Alle vier /api/known_freq-Routen lesen station.json je Request (wie load_catalog_cached).
      Client: knownVisibleChannels blendet Kanaele mit "land-*"-Tag aus, das nicht in
      region_tags steht; Kanaele ohne "land-*"-Tag (Flugfunk, Amateurfunk, Nachbarlaender,
      OE3GAS) bleiben sichtbar. Nur waterfall.py zeigt den Toolbar-Schalter "Region"
      (versteckt, wenn region_tags leer ist); die anderen Server filtern ohne Schalter.
      Nachtrag (2026-08-30, Browser-Validierung): label.tbtn setzt display:inline-flex, das
      hidden-Attribut wurde von CSS ueberstimmt - Region-Label blieb sichtbar. Ausblendung
      jetzt ueber style.display = 'none'. Ausserdem DEFAULT_STATION_PATH war ein reiner
      Dateiname (haengt vom Arbeitsverzeichnis des Servers ab) statt am Repo-Root verankert
      wie DEFAULT_CATALOG_DIR - umgestellt, station.json wird jetzt zuverlaessig gefunden.
- [x] T-115 (2026-08-30) Streifen-Feinschliff: Reihe 1 mit Marker unter dem Titel,
      Mehrfachbelegungs-Tick, mehrzeiliger Tooltip. Browser-validiert.
      Canvas 46 -> 50 px, Reihe 1 (y 0..12): Balken bleibt 0..9, Marker (Segmente) und
      FNV-Einzelfrequenz-Ticks ruecken auf y 9..12 (Luft statt Ueberlagerung mit dem
      Bandtitel bei y 5); Reihe 2 jetzt y 15..28, Kanalreihe ab y 30 (Konstanten
      KNOWN_FNV_H/KNOWN_BAND_Y/KNOWN_CHAN_Y, drawKnownServiceRow/Kanalreihe pruefen
      ausschliesslich ueber diese Konstanten - keine nackten Zahlen im Positionierungscode
      gefunden). Kanalreihe: Mehrfachbelegung (mehrere sichtbare Sender auf derselben
      Frequenz nach Regionsfilter) als dickerer, bernsteinfarbener Tick (lineWidth 3,
      '#fd8') statt im gebuendelten Label unterzugehen. Neue Funktion knownLinesAt(fHz,
      pxPerHz) liefert die Zeilen des Streifen-Tooltips (je Kanal/Sender bzw. Band eine
      Zeile, Kopfzeile "n Sender auf X.XXXXX MHz" bei Mehrfachbelegung); knownTextAt bleibt
      einzeilig fuer die Wasserfall-Hover-Anzeigen ("label (+n-1)" bei Mehrfachbelegung).
      attachKnownTooltip verbindet die Zeilen ueber String.fromCharCode(10) (kein
      Escape im Quelltext); #tooltip-CSS ergaenzt white-space:pre-line, max-width,
      line-height in allen vier Servern (ausserhalb des Blocks). Gates gruen
      (sync_known_block --check, node --check, ast.parse, known_freq.py 4177/171/0);
      Logik (Layout-Konstanten, knownLinesAt/knownTextAt, Mehrfach-Tick) per
      Node-Testharness auf dem extrahierten Block verifiziert.
- [x] T-116 (2026-08-30, Browser-validiert) Streifen-Tooltip als Raster
      (Programm/Standort/Land/Pol/ERP), im Spektrum, hell, anheftbar (Klick/Esc).
      eb200_core/known_freq.py: _CLIENT_OPTIONAL_KEYS (site/programm/bundesland/
      pol/erp_w) - nur ORS_UKW_AT.json/ORS_DVBT2_AT.json tragen sie, gehen im
      Client-JSON NUR mit, wenn im Kanal vorhanden (kein None-Fuellen, Payload der
      uebrigen ~2700 Kanaele bleibt gleich); _expand_entry reicht sie aus den
      Rohdaten durch. Block: neue Datenfunktion knownInfoAt(fHz, pxPerHz) liefert
      Kanalgruppe (freq_hz, channels, hidden = vom Regionsfilter ausgeblendete
      Kanaele auf derselben Frequenz) oder Baender ({title, seg, fnv}); knownLinesAt/
      knownTextAt darauf umgestellt (Text-Ausgabe unveraendert). Eigener heller
      Tooltip #knownTip (bewusst NICHT das geteilte #tooltip des Wasserfall-Hovers)
      als Raster (Programm/Standort/Land/Pol/ERP-Spalten, Fusszeile demod/bw/service
      nur bei einheitlichen Werten, "+n ausserhalb der Region"), per createElement/
      textContent aufgebaut (kein innerHTML), DOM-Neuaufbau nur bei geaendertem
      Schluessel. Platzierung oberhalb des Streifens im Spektrum (Fallback darunter
      bei wenig Platz), links der Maus folgend. Anheften per Klick auf den Streifen
      (Klasse "pinned", Textmarkierung moeglich), Loesen per Klick ausserhalb oder
      Escape. Gates gruen (sync_known_block --check, node --check, ast.parse,
      Steuerzeichen-Pruefung, known_freq.py 4177/171/0 + --selftest); Node-
      Testharness fuer knownInfoAt (3er-Gruppe, hidden mit/ohne Regionsfilter,
      Bandfall, kein Treffer), Textfunktionen, Rendering/Reuse, Positionierung und
      Anheften/Loesen (Klick aussen, Escape, Klick innen wirkungslos) verifiziert.
- [x] T-117 (2026-08-31, Browser-validiert) Reihe 1 hoeher, Bekannte-Toggle
      klappt Streifen ein.
      Canvas 50 -> 55 px, Reihe 1 (KNOWN_FNV_H 12 -> 18): Balken y 0..11 (Titel
      bei y 6), Marker/Ticks y 11..18 statt bisher y 9..12; Segmentmarker
      gedeckter (rgba(255,255,255,0.35) statt 0.55, damit die FNV-Ticks
      dominieren), FNV-Einzelfrequenz-Ticks in #4ee mit lineWidth 2 statt
      #9be/1px. Reihe 2 (KNOWN_BAND_Y 15 -> 20) und Reihe 3 (KNOWN_CHAN_Y
      30 -> 35) nur verschoben, Hoehen unveraendert. Sechs Streifen-Canvases
      (known-o/known-z/known-f/known-w/known-sp/known-s) auf height="55";
      das siebte height="50"-Vorkommen (list-thumb in analyze_sessions.py,
      unabhaengiges Thumbnail) unangetastet. "Bekannte"-Toggle (waterfall.py)
      klappt den Streifen jetzt per style.display ein/aus, statt ihn nur
      schwarz zu zeichnen; Canvases stehen im normalen Blockfluss (keine
      absolute Positionierung), Handler + Start-Zustand ueber KNOWN_STRIPS.
      Zwei Hoehensummen ausserhalb des Blocks, die die Streifenhoehe fest
      einrechneten (#time-o, .colorbar in waterfall.py, beide buendig mit
      wf-o/wf-z), von 178 auf 183 px nachgezogen; Kommentar-Hoehenangabe
      (36 -> 41 px known-o inkl. Rahmen) mitgezogen. Offener Punkt (nicht
      behoben, da ausserhalb des Prompts): #time-o/.colorbar folgen dem
      Einklappen selbst nicht automatisch (fixes margin-top, kein Bezug auf
      knownEnabled) - bei ausgeschaltetem Katalog verschiebt sich wf-o/wf-z
      nach oben, Zeit-Gutter/Farbskala bleiben stehen. Gates gruen (sync
      --check, node --check auf allen vier Servern, ast.parse, Steuerzeichen-
      Pruefung, known_freq.py 4177/171/0 + --selftest).
      Doku: WS3 14.3 um Nachtrag "Reihe 1 hoeher, Bekannte-Toggle klappt den
      Streifen ein (T-117)" ergaenzt.
- [ ] T-118 (2026-08-31) FNV-Einzelfrequenz-Ticks im Streifen-Tooltip.
      Befund (Bereich 232-238 MHz): die tuerkisen Ticks in Reihe 1 (#4ee,
      y 11..18) liefern beim Hover keine Auskunft. Ursache: Die Ticks werden
      aus knownFnvChannels() gezeichnet (Kanaele mit Tag fnv), der Tooltip
      (knownInfoAt) sucht aber nur ueber knownNearestChannels/
      knownVisibleChannels (Kanaele OHNE Tag fnv) - findet also nie einen
      Treffer und faellt in den Bandfall zurueck, ohne die FNV-Frequenz oder
      ihr Label zu zeigen. Der Ausschluss aus der Kanalreihe (Reihe 3) bleibt
      bewusst bestehen (Dichte-Argument, siehe Kommentar bei
      knownFnvChannels) - geaendert wird nur der Tooltip: knownInfoAt sucht
      im Bandfall zusaetzlich die naechstgelegene FNV-Einzelfrequenz
      innerhalb 5 px (gleiche Toleranz wie knownNearestChannels) und stellt
      sie als erste Bandzeile {title: label, seg: "f MHz", fnv: true} voran -
      im Tooltip als "FNV: <Label> · f MHz", danach wie bisher die
      beruehrten Baender. knownRenderTip/knownLinesAt/knownTextAt rendern
      bands generisch und blieben unveraendert; knownTipKeyOf unterscheidet
      ueber title+seg bereits verschiedene FNV-Frequenzen. Aenderung
      ausschliesslich im KNOWN_STRIP_BLOCK von waterfall.py, Sync in die drei
      anderen Server per tools/sync_known_block.py (sync --check vor und
      nach dem Edit je Exit 0). Gates gruen (ast.parse auf allen vier
      Servern, node --check auf den extrahierten Script-Bloecken,
      Steuerzeichen-Pruefung, known_freq.py 4177/171/0 + --selftest
      unveraendert - Regressionskontrolle, kein Katalogeingriff). Status
      bleibt [ ] bis zur Browser-Validierung durch Gerhard.

- [x] T-119 (2026-08-30, Browser-validiert) :8010 Neuanlage einer
      Katalogdatei als Sheet (Popover) auf der Uebersicht statt eigener Seite.
      Button "+ Neue Katalogdatei" oeffnet sheet--neu (vorhandene Sheet-/
      Backdrop-Mechanik aus KNOWN_SHEET_JS wiederverwendet, inkl. ESC und
      Backdrop-Klick). Formular via _file_head_form_body(mode="new",
      embed=True); Abbrechen = data-close. Route /known/newfile bleibt als
      Fallback; Validierungsfehler beim Speichern rendern weiterhin die
      Standalone-Fehlerseite (gleiches Verhalten wie beim eingebetteten
      Datei-Kopf-Tab, bewusst unveraendert).

- [x] T-120 (2026-08-30, Browser-validiert) Abdeckungs-Index
      (coverage) in known_freq.load_catalog(): Raster tragen ihre Spannweite
      (erster..letzter expandierter Kanal) als einen Bereich bei. Behebt die
      zersplitterte Abdeckungszeile in :8010 fuer Rasterdateien ohne eigene
      Baender (Air.json zeigte ~2280 0-breite Punktbereiche; Baender liegen
      seit T-111 allein im FNV-Katalog). Selftest-Absicherung in
      _self_test_real(). Browser-Validierung: :8010 /known, Kachel und
      Bearbeitungsblatt Air.json - Abdeckung als ein Bereich 118.0000-136.9917.

- [x] T-121 (2026-09-14, Browser-validiert) CSV-Import in den
      Frequenzkatalog (:8010, eb200_core/block_admin.py). Zwei getrennte
      Knoepfe neben "+ Neue Katalogdatei": "CSV-Vorlage" (GET
      /known/csv-template, reiner Download katalog_vorlage.csv mit BOM und
      CRLF; Kopfzeile plus Hinweis-/Beispielzeilen als Kommentar, eine
      unveraendert importierte Vorlage legt daher nichts an) und "CSV-Import"
      (Sheet sheet--csv auf der Uebersicht, Rueckfallseite GET
      /known/csv-import, Schreiben POST /known/csv-import/save).
      CSV-Format: Semikolon (Excel-DE), Spalte typ=channel|band,
      Spaltenreihenfolge frei ueber die Kopfzeile, Frequenzen in ganzen Hz
      (nur Ziffern - "118.5" waere weder als Hz noch als MHz eindeutig),
      leeres Feld = geerbt aus den defaults der Zieldatei, # und Leerzeilen
      uebersprungen. Raster bleiben dem Formular vorbehalten.
      Eine id gilt ab ihrem ERSTEN Auftreten als gesehen, auch wenn diese
      Zeile an einem anderen Fehler scheitert - sonst kaeme die Meldung ueber
      die Wiederholung erst im naechsten Anlauf und nicht zusammen mit den
      uebrigen Fehlern.
      Gebaut wird ueber build_channel_entry/build_band_entry/build_file_head,
      geschrieben ueber validate_candidate + _write_known_file (Temp-Spiegel,
      .bak, EOL-Erhalt, atomar). Der Import ist alles-oder-nichts: eine
      einzige beanstandete Zeile schreibt nichts und meldet ALLE Fehler
      gesammelt - bewusst ohne Vorschauschritt, weil ein <input type=file>
      nach einem Fehlversuch nicht wieder vorbelegt werden kann.
      Ziel ist ausdrueckliche Option: neue Datei (Dateiname + Dienstname +
      defaults demod/bw_hz/squelch) ODER Anhaengen an eine bestehende (Kopf
      bleibt unangetastet); gleiche id ist ohne Haken "Vorhandene ids
      ersetzen" ein Fehler, mit Haken ein Update. Upload ueber
      parse_multipart_form() (stdlib-only, cgi ist seit Python 3.13
      entfernt), Groessenschranke CSV_UPLOAD_MAX_BYTES = 4 MiB vor dem Lesen
      des Rumpfes, Dekodierung utf-8-sig -> cp1252 (beide Excel-Varianten).
      Befund beim Bau: der Rahmen eines multipart-Teilstuecks darf nur
      gezielt abgetragen werden (fuehrendes \r\n einmal, abschliessendes
      \r\n einmal), nie per strip(b"\r\n") - das nimmt jeder normal
      gespeicherten CSV das Zeilenende ihrer letzten Datenzeile, ohne dass
      der Import daran scheitern wuerde. C5 sichert das ab.
      Selbsttests: block_admin.py --selftest um _self_test_csv_import()
      ergaenzt (C1-C6: Vorlage, gueltige CSV, sechs Zeilenfehlerarten,
      Dekodierung, multipart, Formular), tools/selftest_known_edit.py um T9
      (Zerlegung bis geschriebene Datei, Anhaengen, id-Kollision).
      Browser-Validierung 2026-09-14: Vorlagendownload, Import in eine neue
      Datei, Anhaengen und die Fehlerseite mit Zeilenliste gruen.

- [x] T-122 (2026-09-14, Browser-validiert) CSV-Export statt
      CSV-Vorlage (:8010, eb200_core/block_admin.py). Der Knopf "CSV-Vorlage"
      heisst jetzt "CSV-Export"; GET /known/csv-export?file=<name> gibt eine
      BESTEHENDE Katalogdatei als CSV aus (catalog_csv_text), die Route
      /known/csv-template und csv_template_text() sind entfallen. Der Export
      einer leeren Katalogdatei ist zugleich die frühere Vorlage - Kopfzeile
      plus Hinweiszeilen (CSV_HINT_ROWS).
      Trennzeichen bleibt Semikolon wie im Import, damit Export -> Excel ->
      Re-Import ohne Zwischenschritt zusammenpasst.
      Ausgegeben wird, was in der Datei STEHT (leeres Feld = geerbt), nicht
      der aufgeloeste Katalog; Reihenfolge Band-Zeilen, dann Kanal-Zeilen.
      Raster bildet die CSV nicht ab (Erzeugungsvorschrift, keine Zeile;
      expandiert braechte Air.json 2280 Zeilen mit) - sie stehen als
      Kommentarzeile mit ihren Kennwerten in der Datei und werden im Kopf
      gezaehlt. Ein Kommentarblock nennt zusaetzlich Quelldatei, Dienst,
      Datei-defaults und alle Zusatzfelder ohne Spalte.
      Zwei Wege: Knopf in der Leiste oeffnet sheet--export (GET-Formular mit
      Dateiauswahl, ohne JavaScript), dazu ein Link "CSV-Export" im
      Bearbeitungsblatt jeder Datei neben "Datei loeschen".
      Befund und Korrektur (T-122): ein Re-Import mit Haken "Vorhandene ids
      ersetzen" haette Schluessel ohne CSV-Spalte geloescht - site, programm,
      bundesland, pol, erp_w der ORS-Listen und segments eines Bandes. Der
      Import uebernimmt solche Schluessel jetzt aus dem bestehenden Eintrag
      (_csv_merge_bestand, _CSV_CHANNEL_KEYS/_CSV_BAND_KEYS); Schluessel MIT
      Spalte bleiben unangetastet, dort heisst ein leeres Feld weiterhin
      "geerbt" und muss einen alten Wert entfernen duerfen.
      Befund und Korrektur (CSS): der Reiter "Eintrag bearbeiten" war trotz
      hidden-Attribut immer sichtbar und leer - ".tabbar .tab { display:
      inline-block }" ist eine Autorenregel und schlaegt das display:none des
      Browser-Stylesheets. Neue Regel ".tabbar .tab[hidden] { display:none }".
      Der Reiter bleibt im Markup: KNOWN_SHEET_JS laedt die Bearbeitungs-
      formulare fuer Kanal/Raster/Band per fetch genau in seine Pane, ohne ihn
      waere das Bearbeiten aus der Tabelle heraus tot.
      Selbsttest: C1 prueft jetzt den Export einer leeren Datei statt der
      Vorlage, C6 zusaetzlich das Export-Formular, neu C7 (Kommentarkopf,
      Datenzeilen, Rueckweg ueber parse_catalog_csv, _csv_merge_bestand).
      Browser-Validierung 2026-09-14: Export ueber Knopf im Blatt und ueber
      das Sheet mit Dateiauswahl gruen, Zusatzfelder der ORS-Listen
      ueberstehen das Ersetzen, Reiter "Eintrag bearbeiten" erscheint nur
      noch beim Bearbeiten eines Eintrags.

- [x] T-123 (2026-09-14, Browser-validiert) CSV-Feinschliff aus
      der Validierung von T-121/T-122 (:8010, eb200_core/block_admin.py).
      Befund und Korrektur: ein unmittelbar nach dem Export versuchter
      Re-Import scheiterte mit "unbekannte Spalte(n) typ,id,label,..." und
      "Spalte 'typ' fehlt in der Kopfzeile". Ursache war nicht der Export,
      sondern die Tabellenkalkulation - LibreOffice und Excel speichern eine
      geoeffnete CSV je nach Version und Spracheinstellung mit einem anderen
      Trennzeichen zurueck, als sie sie gelesen haben (aus Semikolon wird
      Komma); der Parser las die Kopfzeile dadurch als eine einzige Spalte.
      parse_catalog_csv bestimmt das Trennzeichen jetzt aus der Kopfzeile
      (_csv_delimiter_of: Semikolon, Komma oder Tabulator, gewaehlt wird das
      Zeichen mit den meisten bekannten Spaltennamen, Gleichstand bleibt beim
      Semikolon des Exports; Anfuehrungszeichen werden abgestreift).
      Der Export schreibt keine Kommentarzeilen mehr - nur Kopfzeile und
      Daten, damit die Datei in der Tabellenkalkulation ohne Vorspann
      beginnt. Der Import ueberspringt "#"-Zeilen unveraendert weiter (fremde
      CSV-Dateien duerfen welche mitbringen). catalog_csv_text verliert damit
      den Parameter file_name und die Sammlung von Raster-/Zusatzfeldhinweisen;
      was die CSV nicht abbildet (Kanalgruppen, Sender-Standort/Programm/
      Sendeleistung der ORS-Listen, Segmente eines Bandes), steht jetzt im
      Export-Formular statt in der Datei.
      Bedienung: "(Loeschen nur bei leerer Datei)" entfaellt ersatzlos - der
      Loeschknopf steht nur noch da, wenn die Datei leer ist. Das
      Importformular sperrt die jeweils nicht geltenden Felder
      (CSV_IMPORT_JS, eigene Konstante wegen der JS-Klammern): bei "Neue
      Katalogdatei" die Auswahl der bestehenden Datei, bei "Anhaengen"
      Dateiname, Dienstname und die drei defaults-Felder. Gesperrte Felder
      senden nichts - fuer den Handler unkritisch, er liest je Ziel ohnehin
      nur die zugehoerigen Felder.
      Selbsttest: C1 prueft die Kopfzeile allein (meta/defaults duerfen keine
      Kommentarzeile mehr erzeugen), C7 die Abwesenheit jeder "#"-Zeile, neu
      C8 (Trennzeichenerkennung inkl. gequoteter Kopfzeile, Komma- und
      Tabulator-Import, Rueckweg ueber den eigenen Export).
      Browser-Validierung 2026-09-14: der Weg Export -> LibreOffice ->
      Speichern -> Import laeuft durch, auch wenn Calc dabei auf Komma
      wechselt (genau der Befund, der zu T-123 gefuehrt hat).

- [x] T-124 (2026-09-14, Browser-validiert) CSV-Import/-Export:
      Bedienung und Fehlerbericht (:8010, eb200_core/block_admin.py).
      CSV-Export im Bearbeitungsblatt ist ein Knopf statt eines Links -
      gleiche Bauart wie "Datei loeschen" daneben (Inline-Formular, method=get,
      weil der Export nichts veraendert).
      Das Feld "Neue Datei" ergaenzt ".json", wenn es fehlt
      (_csv_zieldateiname, angewandt beim Einlesen des Formulars, damit nach
      einem Fehlversuch der vollstaendige Name im Feld steht); das
      pattern-Attribut entfaellt, es haette genau die jetzt erlaubte Eingabe
      abgewiesen.
      Fehlerbericht: statt einer Kette roter Einzelmeldungen ein Kasten
      (_csv_bericht_html, CSS .bericht), der mit dem Wichtigsten beginnt -
      "Es wurde nichts geschrieben - die Katalogdatei X wurde NICHT angelegt."
      bzw. "X ist unveraendert." Aus den bisherigen Meldungen ging das nicht
      hervor; bei einem fehlgeschlagenen Import in eine neue Datei war nicht
      zu erkennen, ob die Datei nun halb im Katalog liegt. ID-Konflikte
      werden aus den Loader-/Handler-Meldungen zerlegt (_CSV_KONFLIKT_RES)
      und als Tabelle ID -> "bereits vergeben in" gezeigt, mit der Erklaerung,
      dass IDs im GESAMTEN Katalog eindeutig sein muessen (Scan-Sets sprechen
      Kanaele ueber die ID an) und dem Hinweis auf "Anhaengen" +
      "Vorhandene ids ersetzen". Was auf kein Konfliktmuster passt, bleibt
      unveraendert als Liste stehen - es geht nichts verloren.
      Die drei Fehlertitel benennen jetzt die Pruefstufe ("Die CSV konnte
      nicht gelesen werden", "Der Abgleich mit der Zieldatei/mit dem gesamten
      Katalog hat Folgendes ergeben"); _csv_import_form_body nimmt statt
      error/Zeilenliste das fertige Bericht-HTML entgegen.
      Selbsttest C9 (Endungsergaenzung inkl. Leerraum und bereits gesetzter
      Endung, Bericht mit beiden Konfliktmustern, Restzeilen, Kopfsatz je
      Zielart).
      Browser-Validierung 2026-09-14: Export-Knopf, Import ohne ".json" im
      Namensfeld, Fehlerbericht beim zweiten Import derselben IDs; das
      Katalogverzeichnis bleibt dabei frei von halb angelegten Dateien
      (weder .json noch .tmp/.bak).

- [x] T-125 (implementiert, validiert 2026-09-14) Kosmetik :8010,
      Reiter "Bearbeiten" (eb200_core/block_admin.py, reines CSS im
      style-Block von PAGE_HEAD). Befund aus der Bedienung: die Band- und
      die Kanal-/Rastertabelle laufen mit width:100% ueber die volle
      Blattbreite des Sheets (inset:4% 6%); auf einem breiten Schirm stehen
      die Spalten dadurch weit auseinander und die Zeilen wirken luftig.
      Neuer Regelblock hinter den th.sortable-Regeln: Breitendeckel
      max-width:88em und margin-top:8px, Zellen auf padding:3px 8px /
      line-height:1.25 gezogen. Die Kopfzeile ist jetzt als Block lesbar -
      Hintergrund --accent-soft (in beiden Themes definiert), font-size
      1.1em, font-weight 700, border-bottom 2px in --accent.
      Alle Regeln haengen an table.known-table:not(.marker-table): die
      Dublettentabelle (marker-table) traegt Eingabefelder und behaelt ihre
      60em und die weiten Zellen. Keine Python-Logik, kein HTML, kein JS
      beruehrt; die Sortierlogik (KNOWN_SORT_JS) haengt an der Klasse
      known-table und bleibt unveraendert.
      Browser-validiert 2026-09-14 (hell und dunkel).

- [x] T-126 (2026-09-14, Browser-validiert) Ansichtsauswahl der
      Katalogdateien. In :8010 traegt jede Fliese den Schalter "Ansicht";
      abgewaehlte Dateien verschwinden in D-SCAN und IFPAN (:8000),
      SDR-Scan (:8030), :8020 und :8040 aus dem Katalogstreifen.
      Gespeichert als AUSSCHLUSSliste hidden_files in catalog_view.json
      (Repo-Root, nicht versioniert, Muster station.json) - eine neu
      angelegte oder importierte Katalogdatei ist damit sofort sichtbar.
      Leser known_freq.load_view_config (je Request, kein Cache), Schreiber
      block_admin._write_view_config ueber POST /known/view (JSON statt
      Redirect, sonst waere ein Seiten-Reload je Klick noetig).
      Gefiltert wird ausschliesslich in Catalog.to_client_json - by_id(),
      channels_in_range(), Scan-Set-Upload, MSCAN-Jobs und der Kanalwaehler
      in :8050 sehen weiterhin den ganzen Katalog, genau wie beim
      Regionsfilter aus station.json. Der wortgleiche KNOWN_STRIP_BLOCK
      bleibt unangetastet; in der Bedienleiste von :8000 zeigt
      "Katalog (n/m)" an, dass Dateien abgewaehlt sind.
      Nachzug T-126a: das Hamburger-Zeichen vor "Katalog" ist entfallen -
      mit dem angehaengten Zaehler passte "Katalog (1/23)" sonst nicht mehr
      in eine Zeile der Bedienleiste. Die Glyphe vor "Ansichten" im
      Navigationsknopf bleibt.

- [x] T-127 (implementiert 2026-09-15, Browser-validiert 2026-09-16) Notizen im
      Katalog-Streifen-Tooltip. Catalog.to_client_json liefert notes mit,
      aber NUR bei nichtleerem Text: notes ist ein geerbtes Feld und liegt
      deshalb als None auf jedem Kanal, "if k in ch" wie bei den
      ORS-Zusatzfeldern haette alle 8322 Kanaele mit notes=null beliefert.
      Die Fusszeile von knownRenderTip zeigt die Notiz des naechstgelegenen
      Kanals anstelle des Dienstes, der bei Kanaelen ohne ORS-Zusatzfelder
      schon in Spalte 2 des Rasters steht; bei ORS-Kanaelen (Spalte 2 =
      Standort) bleibt der Dienst stehen und die Notiz folgt dahinter. Ohne
      Notiz ist die Zeile Wort fuer Wort und DOM-gleich wie bisher.
      Umbruch ueber die neue Klasse kt-note (white-space:pre-wrap,
      overflow-wrap:break-word) an der vorhandenen Grenze max-width:560px -
      832 Notizen tragen echte Zeilenumbrueche, die laengste Notiz hat 268
      Zeichen. Geaendert wurde nur waterfall.py, die drei uebrigen Server
      per tools/sync_known_block.py. :8010 ist nicht betroffen, es traegt
      keinen Streifen-Tooltip. Kosten: /api/known_freq waechst von 2119 KiB
      auf 3208 KiB (+51 %), bewusst in Kauf genommen - die Notizen sind
      durch das Erzeugungsschema auf rund 270 Zeichen begrenzt.
      Selbsttest _self_test_notes deckt die vier Faelle keine/eigene/
      geerbte/leere Notiz ab.

- [~] T-128 (implementiert 2026-09-15, Sichtpruefung offen) ID-Praefix-
      Konvention festgehalten. Anlass: ALE_UDXF.json und UDXF_logs.json
      vergaben beide udxf-<kHz>; fuer jede Frequenz in beiden Listen verwarf
      _add_channel den Eintrag der zweitgelesenen Datei und meldete das bei
      jedem gecachten Neueinlesen erneut (rund 300 Zeilen in der Fliese von
      :8010). Auf Dateiseite behoben durch das Praefix udxflog- und das
      Entfernen der 301 Dubletten. Festgehalten wurde die ENGERE Regel:
      nur frequenzabgeleitete ids (Muster <praefix>-<kHz> ohne weiteres
      unterscheidendes Segment) brauchen ein dateieigenes Praefix. Die
      Messung zeigte, dass bc/fm/ham/ors heute von 2 bis 5 Dateien geteilt
      werden und das gefahrlos ist, weil dort Land, Band oder Standort im
      zweiten Segment unterscheidet - eine pauschale Regel "ein Praefix je
      Datei" haette 11 Bestandsdateien zu Unrecht als regelwidrig erklaert.
      Orte: Moduldoku eb200_core/known_freq.py (Design-Entscheidung 1),
      handbuch/06_frequenzkatalog.md Abschnitt 6.4 samt Konverterregel, und
      neu die Uebersicht "python eb200_core/known_freq.py --praefixe"
      (Praefixe je Datei plus die geteilten). Bewusst KEINE vorbeugende
      Warnung in :8010: validate_candidate faengt echte Dubletten auf jedem
      Schreibweg ab, eine Praefixwarnung wuerde bei bc/fm/ham/ors staendig
      zu Unrecht anschlagen.

- [x] T-129 (implementiert 2026-09-15, Browser-validiert 2026-09-16, samt
      den Nachtraegen T-129a und T-129b)
      Katalogstreifen hoeher und lesbarer. Canvas 55 -> 96 px in allen sechs
      Streifen (known-o/known-z in :8000, known-f in :8020, known-w/known-sp
      in :8040, known-s in :8030), Schrift 9 -> 12 px. Reihe 1 (FNV) y 0..26,
      Reihe 2 (Dienst) y 29..59, Reihe 3 (Kanaele) ab y 62; gemeldete
      Belegthoehen 0/26/59/96 statt 0/18/33/55.
      Kern der Aufgabe war nicht die Hoehe allein: die Dienstebene kannte nur
      zwei Spuren zu je 6 px, im 40-m-Bereich liegen aber DREI Dienstbaender
      uebereinander (HF unten 3-10 MHz aus UDXF_logs.json, Amateurfunk 40 m,
      41 m Rundfunk) - das dritte Band uebermalte Spur 1. Jetzt bis zu drei
      Spuren (KNOWN_BAND_LANES), Spurhoehe aus der Zahl der belegten Spuren
      (1 Band 30 px, 2 Spuren je 14 px, 3 Spuren je 9 px) und die Schrift
      folgt der Spurhoehe (knownFontFor: 14/12/9 px) - eine 12-px-Schrift in
      einer 9-px-Spur ragt sonst in die Nachbarspur.
      KNOWN_LABEL_PX 45 -> 60, weil ein typisches Kanallabel bei 12 px rund
      50 px breit ist. #time-o padding-top 183 -> 224 px (Streifen + 41 px);
      der Kommentar dort ist richtiggestellt, seine Summandenrechnung ging
      schon vorher nicht auf. Geaendert wurde nur waterfall.py, die drei
      uebrigen Server per tools/sync_known_block.py; deren Canvas-Hoehen
      liegen ausserhalb des geteilten Blocks und wurden einzeln gesetzt.
      Nachtrag T-129a (implementiert 2026-09-16): die drei Reihen sitzen
      nicht mehr auf festen y-Werten, sondern werden je Darstellung
      gestapelt. Eine Reihe ohne Inhalt kostet keine Hoehe, und ihr Platz
      faellt an die Dienstebene. Anlass: Zuweisungsbaender beginnen laut
      Messung erst bei 30,01 MHz und FNV-Einzelfrequenzen gibt es unter
      30 MHz keine - auf Kurzwelle war Reihe 1 also immer leer und ihre
      26 px blieben schwarz, weil knownStripResized nur unten abschneidet.
      Jetzt hat die Dienstebene dort 59 statt 30 px.
      Nachtrag T-129b (implementiert 2026-09-16): die Spurhoehe der
      Dienstebene gilt einheitlich fuer die ganze Darstellung und richtet
      sich nach dem dichtesten Cluster (knownLaneLayout). Vorher bekam ein
      Band ohne Ueberlappung die volle Hoehe, waehrend Nachbarn sich 14 oder
      9 px teilten - am DVB-T-Balken las sich das als Gewichtung, die es
      nicht gibt. Untergrenze ist KNOWN_LANE_H = 14 px; passen dann nicht
      alle Spuren, teilen sich die ueberzaehligen Baender die letzte Spur
      (oberhalb 30 MHz moeglich, weil dort nur zwei Spuren hineingehen -
      der gemessene Dreifachfall liegt im 40-m-Bereich und damit dort, wo
      T-129a die 59 px bereitstellt).
      Neu KNOWN_STRIP_H, KNOWN_ROW_GAP, KNOWN_LANE_H, KNOWN_CHAN_H;
      KNOWN_BAND_Y und KNOWN_CHAN_Y entfallen. KNOWN_STRIP_H = 96 ist an
      die Canvas-Hoehen gekoppelt und muss mit ihnen zusammen geaendert
      werden. Das Soll-0-Gate auf den entfallenen Namen KNOWN_BAND_Y hat
      dabei den Doku-Kommentar vor drawKnownServiceRow aufgedeckt, der seit
      T-113 das feste Zwei-Spuren-Modell beschrieb; er ist mitgezogen.

- [x] T-131 (implementiert 2026-09-16, Browser-validiert 2026-09-16) Kanalreihe:
      Dichtekriterium auf den Median, Ausduennung, Sammelbalken. Befund aus
      der T-129-Validierung: im IFPAN-Zoom auf 11 MHz erschien kein einziger
      Kanal-Tick, obwohl der Tooltip die Kanaele kannte - das Kriterium
      verwarf die GANZE Gruppe, sobald der kleinste Abstand unter
      KNOWN_TICK_PX lag, und zwei UDXF-Eintraege wenige hundert Hertz
      auseinander genuegen dafuer. Massstab ist jetzt der Median der
      Abstaende. Gemessen bei 160 kHz Span: minPx 2,81 gegen medianPx 22,50.
      Der Median laesst genau die drei Faelle durch, in denen enge Paare ein
      duenn besetztes Bild loeschten (IFPAN 11 MHz, Flugfunk benannt, 2 m
      benannt), und haelt die wirklich dichten Gruppen weiter zurueck
      (Flugfunkraster, alle benannten Kanaele ueber 1-30 MHz).
      Die Ausduennung (Tick auslassen, wenn er naeher als KNOWN_TICK_PX am
      zuletzt gezeichneten laege) greift NUR innerhalb zugelassener Gruppen -
      als Kriterium waere sie falsch, sie erzeugte im Flugfunkraster 95 %
      Zaunbreite, bei 1-30 MHz benannt 73 %, bei UKW 67 %.
      Neu knownDrawDenseBar: eine unterdrueckte Gruppe verschwand bisher
      spurlos, "zu dicht" sah aus wie "keine Kanaele". Jetzt steht ein
      halbtransparenter 3-px-Balken ueber ihrem Frequenzbereich, benannte und
      Rasterkanaele in zwei Lagen uebereinander.
      Dritter Teil: die Beschriftung ist nicht mehr alles-oder-nichts.
      withLabels unterdrueckte sie fuer die ganze Gruppe, sobald der mittlere
      Abstand unter KNOWN_LABEL_PX lag - bei 100 kHz Span im IFPAN blieb
      deshalb jeder Tick namenlos, obwohl mehrere Labels nebeneinander Platz
      gehabt haetten. Jetzt wird jedes Label versucht und die vorhandene
      Kollisionspruefung entscheidet; withLabels waehlt nur noch die
      Tickhoehe.

- [x] T-132 (implementiert 2026-09-16, Browser-validiert 2026-09-16, samt
      den Nachtraegen T-132a bis T-132d) Bandfarbe je Katalogdatei. Bisher
      entstand die Farbe aus einem Hash ueber die
      BAND-ID: die DAB-Bloecke trugen deshalb untereinander verschiedene
      Farben, waehrend das 70-cm-Band zufaellig dieselbe Farbe traf wie
      DVB-T2 - die Farbe suggerierte eine Verwandtschaft, die es nicht gibt.
      Jetzt nennt der Dateikopf unter meta.color einen Namen aus
      CATALOG_COLORS (known_freq.py, elf gedeckte Farben; die ersten sieben
      sind die bisherige Hash-Palette, damit Dateien ohne Farbe aussehen wie
      vorher). Feste Auswahl statt freier Hexwerte, damit kein Tippfehler
      einen unlesbaren Balken erzeugt. Alle Baender der Datei tragen den
      Hexwert im Client-JSON, die Dateiliste den Namen.
      Ohne meta.color hasht die Darstellung ab jetzt den DIENSTNAMEN statt
      der Band-ID - schon das macht die DAB-Bloecke einheitlich und trennt
      70 cm von DVB-T2.
      In :8010 waehlt man die Farbe im Datei-Kopf-Formular; jede Option
      traegt ihre Farbe als Hintergrund. Der Segmentmarker der FNV-Reihe
      wird in der Bandfarbe statt in Weiss gezeichnet.
      Nachtrag T-132a (2026-09-16): Erstbelegung aller 25 Katalogdateien mit
      meta.color nach Inhalt - gelb fuer die FNV-Zuweisungsebene, blau fuer
      UKW-Hoerfunk (ORS, UKW_BC, cz/hu/sk), violett fuer DAB, oliv fuer
      DVB-T2 (bewusst nicht wie DAB, beide liegen in derselben Ansicht
      nebeneinander), ocker fuer KW-Rundfunk, gruen fuer Amateurfunk, petrol
      fuer Flugfunk, rotbraun fuer Utility/DX (ALE_UDXF, KiwiSDR_DX,
      UDXF_logs), rot fuer Not- und Sicherheitsfunk, graublau fuer ISM/SRD,
      Satelliten und Zeitzeichen, grau fuer eigene Eintraege, Marker und
      Test. fup_30_88_mhz.json steht vorlaeufig auf grau - der Inhalt liess
      sich keiner Familie sicher zuordnen, die Farbe ist in :8010 in zwei
      Klicks zu aendern.
      Nachtrag T-132b (2026-09-16): freie Farbwahl. Zwei Befunde der
      Validierung - die Palette aus elf Namen ist zu eng (allein vier
      UKW-Dateien teilen sich ein Blau), und ein zugeklapptes <select> zeigt
      die Farbe seiner Option nicht, im Formular stand also nur das Wort.
      Beides loest <input type="color">: das Feld ist selbst der Farbfleck
      und oeffnet den RGB-Waehler des Systems. Die elf benannten Farben
      stehen als Knopfreihe daneben, damit gleiche Dienstfamilien mit einem
      Klick dieselbe Farbe behalten. meta.color nimmt jetzt einen Namen ODER
      ein eigenes #rrggbb (known_freq.resolve_color) - die 25 Dateien aus
      T-132a bleiben damit unveraendert gueltig. Der Verzicht auf eine Farbe
      braucht ein eigenes Bedienelement, weil ein Farbfeld keinen leeren Wert
      liefern kann: Haken "keine eigene Farbe".
      Preis der freien Wahl: der Bediener kann jetzt Farben treffen, die mit
      der Pegel-Colormap des Wasserfalls konkurrieren. Die feste Palette
      hatte das verhindert.
      Nachtrag T-132c (2026-09-16): Palette auf 39 Farben erweitert (13
      Familien zu je drei Helligkeitsstufen) und die Schriftfarbe der
      Bandtitel folgt der Helligkeit des Balkens (knownTextColor). Beides
      bedingt einander: die Titel standen fest auf '#eee', womit jede helle
      Farbe unbrauchbar war - die halbe neue Palette und jede helle Wahl aus
      dem RGB-Waehler von T-132b. Gerechnet wird mit der WIRKSAMEN Farbe: die
      FNV-Reihe zeichnet mit globalAlpha 0.55 auf Schwarz, dort ist der
      Untergrund dunkler als der eingestellte Wert - sonnengelb bekommt in
      der Dienstebene dunkle Schrift, als FNV-Balken helle. Die Schwelle
      0.179 ist der gerechnete Umschlagpunkt, an dem reines Weiss und reines
      Schwarz denselben Kontrast liefern; die Schrift ist deshalb #fff/#000
      und nicht das sonst uebliche #eee/#111 - sonnengelb und limette fallen
      in der FNV-Reihe genau in die Mitte und blieben mit #eee bei 4,05 bzw.
      4,28. Mit #fff traegt jede der 39 Farben in beiden Ebenen mindestens
      Kontrast 4,7.
      Die Katalogdateien blieben unangetastet: sieben der elf alten Namen
      fuehrt die neue Palette weiter, die vier uebrigen (ocker, rotbraun,
      gelb, graublau) loesen ueber _COLOR_ALIASES auf. "gelb" zeigt dabei auf
      sonnengelb statt auf die naechstverwandte Farbe - FNV_AT_2026.json
      traegt sie, und erst die 55 % Deckkraft machen daraus das satte
      Ockergelb der Zuweisungsebene. Die Rueckfall-Palette in waterfall.py
      (Hash ueber den Dienstnamen) bleibt gedeckt: wer keine Farbe waehlt,
      soll nicht auffallen.
      Nachtrag T-132d (2026-09-16): die Schnellwahl wirkte nur im ersten
      Bearbeitungsblatt. Die Uebersicht in :8010 legt je Katalogdatei ein
      eigenes Blatt ins DOM und blendet es nur ein/aus - id="color" gab es
      also 25-mal, und getElementById traf immer ALE_UDXF.json; das Skript
      lag ebenfalls im Formular und band mit querySelectorAll jedes Mal alle
      Knoepfe der Seite. Jetzt tragen die Felder keine id mehr, sondern
      werden ueber ihren name im umschliessenden .farb-wahl gesucht, und das
      Skript steht als FARB_WAHL_JS einmal je Seite in PAGE_TAIL und
      arbeitet per Delegation - damit wirkt es auch auf der eigenstaendigen
      Seite /known/head, nicht nur im Uebersichtsblatt. Nebengewinn: als
      eigene Konstante ohne f-String wird es von tools/check_js_blocks.py
      geprueft, im Formular blieb es ungeprueft.

- [~] T-133 (implementiert 2026-09-16, Browser-Validierung offen) Der
      Streifen-Tooltip zeigt nur noch die Reihe unter dem Zeiger. Vorher gab
      knownInfoAt den Kanaelen Vorfahrt und kam bei einem Treffer gar nicht
      zu den Baendern; wer einen Bandbalken anfuhr, sah je nach Frequenz
      einen Kanal oder beide Ebenen samt der langen FNV-Titel.
      Jetzt entscheidet die Hoehe des Zeigers: FNV-Reihe, Dienstebene oder
      Kanalreihe. knownInfoAt bekam dafuer einen dritten Parameter; ohne ihn
      gilt das alte Verhalten, das knownLinesAt und knownTextAt brauchen -
      die Hover-Anzeigen der Wasserfaelle haben keine Zeigerhoehe.
      Neu ist die Ablage knownLastRows: drawKnownStrip merkt die je
      Darstellung gerechneten Reihengrenzen, so wie es das mit knownLastRange
      und knownLastHeight schon tut. Seit T-129a sind sie nicht mehr
      konstant. knownReiheBei schlaegt darin nach; die Luecken zwischen den
      Reihen gehen an die obere, damit beim Hinuebergleiten nichts flackert.
      Dieselbe Ablage ist die Grundlage der Detailtafel aus T-130.
      Der Schluessel des Tooltips fuehrt die Reihe mit - sonst bliebe er
      stehen, wenn der Zeiger bei gleicher Frequenz die Reihe wechselt.
      Nachtrag: die gemessenen Kontrastwerte im Kommentar von knownTextColor
      berichtigt (4,74 und 5,01 statt 4,70 und 4,97 - die Canvas-Ueberblendung
      schneidet auf ganze Bytes ab).

- [~] T-134 (2026-09-17, Sichtpruefung offen) Quellenhinweis aus den Notizen
      in den Dateikopf. Befund der Messung: UDXF_logs.json traegt in ALLEN
      2918 Notizen dasselbe Feld "Quelle: UDXF-Logs 2006-2025, kanaele.csv
      (V01-Clustering)" (175 kB), KiwiSDR_DX.json in allen 905 die Zeile
      "Quelle: KiwiSDR Community DX" (25 kB). Beides ist jetzt gestrichen.
      Der UDXF-Hinweis wandert dabei nach meta.source - die Datei hatte
      bisher KEINEN source-Schluessel, ersatzloses Streichen haette also
      Information vernichtet. Bei KiwiSDR steht der Text schon doppelt im
      Kopf (meta.service nennt "KiwiSDR Community DX", meta.source die URL),
      dort ist er ersatzlos entfallen. 73 KiwiSDR-Notizen bestanden nur aus
      dem Hinweis und haben ihren notes-Schluessel verloren statt als leerer
      Text stehenzubleiben.
      Wirkung: /api/known_freq faellt von 3211 auf rund 3014 KiB, die
      laengste Notiz von 268 auf 231 Zeichen. ALE_UDXF.json ist nicht
      betroffen (individuelle Notizen, mit " . " gegliedert, ohne
      Quellenhinweis).
      Regel fuer den Konverter: was in JEDER Notiz einer Datei gleich lautet,
      gehoert in den Dateikopf. Der naechste Lauf der Logauswertung darf den
      Hinweis nicht erneut in jede Notiz schreiben.
      Nicht Teil dieser Aufgabe: das Feld "Logs 2010-2025: 170 (gesamt
      2006-2025: 283, UNID 64)" steht zwar in allen 2918 Notizen und belegt
      145 kB, traegt aber je Kanal andere Zahlen - eine kuerzere Schreibweise
      waere eine Aenderung der Wortwahl, keine Entdopplung.

- [-] T-130 VERWORFEN (implementiert und am 2026-09-16 wieder
      zurueckgenommen, Commit e269194 und dessen revert). Eine ortsfeste
      Detailtafel unter dem Katalogstreifen sollte die langen Notizen aus
      T-127 lesbar machen, die der schwebende Tooltip nicht tragen kann.
      Umgesetzt war sie unter beiden Streifen in :8000, mit fester Hoehe von
      84 px und bleibendem Inhalt. Im Betrieb hat sie die Oberflaeche
      verdorben: eine dauerhaft belegte Flaeche zwischen Streifen und
      Wasserfall kostet Platz, den der Wasserfall braucht, und sie steht
      auch dann da, wenn man sie nicht braucht - also fast immer.
      Wer das Thema erneut angeht: das Problem (Notizen bis 268 Zeichen sind
      im Tooltip nicht lesbar) besteht weiter. Gangbar waere eine Flaeche,
      die NUR auf Anforderung erscheint - etwa ein Panel wie die Markerliste,
      das der Bedienleisten-Knopf oeffnet - oder eine Anzeige ausserhalb des
      Canvas-Stapels, die den Wasserfall nicht nach unten schiebt. Die
      Vorarbeit aus T-133 (knownLastRows, knownReiheBei, reihenbewusstes
      knownInfoAt) bleibt erhalten und traegt jede solche Loesung.
      Gesetzt per Einmalskript. Amateurfunk_UHF.json und KW_BC.json lagen
      teilweise kompakt formatiert vor und wurden dabei auf die kanonische
      Form gebracht (json.dumps mit indent=2), die :8010 ohnehin bei jedem
      Speichern erzeugt - der Diff dieser beiden Dateien ist deshalb gross,
      inhaltlich aber nur der eine neue Schluessel. Belegt durch einen
      Strukturvergleich Vorfassung gegen Neufassung: identisch bis auf
      meta.color.

## Meilenstein 2: Long-Run-Jobsystem — Restarbeiten
- [x] T-201 (2026-08-30, Betriebsvalidierung: externer Root M:/eb200-test,
      Listing/Links/Loeschen/403-Negativtests gruen) Pfad-Sandbox:
      resolve_safe_path in :8020/:8040/:8070 auf Whitelist der konfigurierten
      storage_roots erweitern (Schritt 7 aus WS3 11.7).
      eb200_core/path_sandbox.py (neu, stdlib-only): storage_root_abs(repo_root,
      entry) (Semantik identisch zu recording_index._resolve_storage_root
      geprueft - dessen fehlendes normpath ist wirkungslos, da jeder Aufrufer
      dort das Ergebnis selbst per realpath/abspath/normpath weiterverarbeitet,
      bevor es verglichen wird; Funktion dort durch Import-Alias ersetzt),
      sandbox_roots(repo_root) (Repo-Root Index 0 + storage_roots.json-
      Eintraege, je Request neu gelesen, normcase-Dedup), resolve_within(
      user_path, roots, suffixes=, reject_suffixes=) (Root-Vergleich IMMER vor
      der Endungspruefung - sonst waere aus der Fehlerart ablesbar, ob eine
      Datei mit passender Endung ausserhalb der Sandbox existiert), to_portable
      (path, repo_root) (portables Format: relativ mit "/" im Repo-Root, sonst
      absolut mit "/" - nie "../" ueber das Repo-Root hinaus, ersetzt die drei
      to_relative() sowie recording_index._to_relative, dessen os.path.relpath
      bei einem Root auf anderem Laufwerk unter Windows sogar ValueError
      geworfen haette). Selbsttest T1-T8 gruen.
      analyze_sessions.py/analyze_spectrum.py/analyze_audio.py:
      resolve_safe_path()/to_relative() sind duenne Wrapper um
      resolve_within()/sandbox_roots()/to_portable(); Suffixe :8020 (".db",
      reject "-wal"/"-shm" - alle 7 bestehenden resolve_safe_path-Aufrufer
      geprueft, keiner schickt einen Nicht-.db-Pfad hindurch), :8040
      (".specbin",), :8070 (".wav",). ERR_SANDBOX-Text "ausserhalb der
      erlaubten Wurzel" -> "ausserhalb der erlaubten Ablagen" (mehrere Roots
      jetzt moeglich). analyze_audio.py --selftest um T2b ergaenzt (externer
      storage_root wird akzeptiert, Pfad ausserhalb aller Roots weiterhin
      abgelehnt) - path_sandbox.py deckt die eigentliche Root-/Suffix-/
      Traversal-Logik bereits erschoepfend ab (T1-T8), T2b beweist nur, dass
      der Wrapper sie tatsaechlich mit AUDIO_ROOT aufruft.
      eb200_core/recording_index.py: _to_relative/_resolve_storage_root sind
      seither Import-Aliase auf path_sandbox (Aufrufer je 12x/3x unveraendert).
      Gates gruen (ast.parse auf allen fuenf Dateien, path_sandbox --selftest
      T1-T8, analyze_audio.py --selftest, analyze_mscan.py --selftest,
      manage_jobs.py --selftest, recording_index --selftest T1-T30,
      Import-Check analyze_sessions/analyze_spectrum). KEIN Serverstart.
      Nachtrag (2026-08-30): Listing/Ablage/Loeschen ueber alle Roots (:8050).
      Befund: _spectrum_rows()/_audio_rows() lasen bislang NUR record_dir
      (records/) - eine Aufnahme in einem externen storage_root tauchte in
      der Aufzeichnungsliste ueberhaupt nicht auf; nur _orphan_rows()
      durchsuchte schon alle Roots. "ablage" war ueberall der blosse
      basename() des Roots (bei zwei gleichnamigen externen Roots auf
      verschiedenen Laufwerken nicht mehr unterscheidbar). delete_recording()/
      set_recording_label() liessen ueber _resolve_within() ausserdem NUR
      Pfade innerhalb des Repo-Roots zu - der orphan-Zweig pruefte zwar
      zusaetzlich gegen die konfigurierten Roots, kam dabei aber nie an, weil
      _resolve_within() externe Pfade schon vorher ablehnte.
      Neue Hilfsfunktion _all_storage_roots(repo_root, record_dir) (aus dem
      bisherigen _orphan_rows()-Codeblock gezogen, dort jetzt ein Aufruf)
      liefert die absolute, deduplizierte Root-Liste (konfigurierte Roots,
      dann record_dir); _spectrum_rows()/_audio_rows() iterieren jetzt
      ebenfalls darueber (Signaturen unveraendert, Zeilen aller Roots werden
      zusammengefuehrt). "ablage" ist jetzt ueberall (spectrum/audio/job/
      orphan) einheitlich to_portable(root_abs, repo_root) - "records" fuer
      den Standard-Root, sonst der absolute, unterscheidbare Pfad
      (z. B. "D:/eb200-test"); Direkt-Session bleibt bewusst aussen vor
      (sessions/ ist kein storage_root). _resolve_within() nutzt jetzt
      path_sandbox.resolve_within(rel, sandbox_roots(repo_root)) statt der
      alten Ein-Root-Pruefung (ValueError -> RecordingNotDeletableError);
      Suffixregeln je kind (.db/.specbin/.wav) bleiben unveraendert VOR dem
      Aufruf. manage_jobs.py: Ablage-Spalte zeigt "ablage" nur an (kein
      Vergleich gegen "records" im JS gefunden), Auswertungs-Link reicht
      auswertung.query samt portablem Pfad unveraendert (nur encodeURIComponent)
      als ?path= weiter - keine Aenderung noetig.
      Selbsttest um T31 (externer Root: WAV+.specbin gelistet, ablage/ref
      portabel, Root doppelt in storage_roots.json -> keine doppelten Zeilen)
      und T32 (delete_recording im externen Root erfolgreich, ausserhalb
      aller Roots weiterhin RecordingNotDeletableError) auf T1-T32 erweitert,
      alle gruen. Gates gruen (ast.parse, recording_index --selftest T1-T32,
      analyze_mscan.py --selftest, manage_jobs.py --selftest). KEIN
      Serverstart.
      Fund (2026-08-30, damals nicht Teil dieser Aufgabe, gemeldet statt
      committet): storage_roots.json existiert am Repo-Root (lokale
      Konfiguration mit einem externen Root auf M:), fehlte aber in
      .gitignore - anders als station.json nicht als "nie mitstagen"
      abgesichert. Nachtrag: .gitignore-Zeile ergaenzt (siehe unten).
- [ ] T-202 Hardware-Validierung: drei parallele UDP-Streams gleichzeitig
      (D-SCAN-Sweeps + IFPAN-Bins + Audio, je eigener Port, Lückenzählung je Pfad)
- [ ] T-203 Browser-Mithören (Mithören via /audio/stream): End-to-End-Validierung
      am Gerät (Latenz, Aussetzer, Verhalten bei Seiten-Reload)
      Bugfix (2026-08-23): Zombie-Client-Erkennung in /audio/stream (select+MSG_PEEK im
      Timeout-Zweig); ohne sie blieb das Mithören nach Deaktivierung serverseitig an
      und Re-Aktivierung stapelte Streams. Hardware-/Browser-Validierung offen.
- [ ] T-204 /audio/stream fehlt in den 423-Sperrlisten der Long-Run-/MSCAN-Läufe (Mithören darf einen Lauf
      nicht stören)
- [ ] T-205 register_server ohne Abmelden – Zombie-Einträge im param_store beim Absturz eines Servers
- [x] T-206 (2026-08-30, validiert: ticks/age_s, /api/debug/threads) Scheduler-Diagnostik: Herzschlag mit Phase, Waechter mit
      faulthandler-Dump, /api/longrun/status.scheduler, Warnung in :8050 -
      validiert: Server gestartet, /api/longrun/status zeigt scheduler.ticks
      wachsend, /api/debug/threads liefert eine Datei.
      eb200_core/sched_health.py (neu, stdlib-only): SchedulerHealth (Lock-
      geschuetzt, snapshot() ohne I/O) mit tick_start(phase)/tick_end() (Dauer >
      slow_s=5.0 -> slow_ticks++, Rueckgabe True), error(text), snapshot()
      ({ticks, slow_ticks, phase, age_s, in_tick_s, last_tick_iso, last_error}),
      should_dump() (True genau einmal je Freeze-Episode: age_s > freeze_s=120,
      scharf erst wieder nach Erholung age_s < recover_s=60). dump_threads(path,
      header): Thread-Legende (Name -> ident) VOR faulthandler.dump_traceback
      (das nur idents zeigt), fh.flush() vor dem C-Level-Dump zwingend (der
      schreibt direkt auf den Deskriptor, am Python-Puffer vorbei). Selbsttest
      T1-T7 gruen (now stets explizit uebergeben, kein time.sleep).
      waterfall.py: Controller.sched_health = SchedulerHealth(); Scheduler-Loop
      meldet tick_start("pruefen") am Schleifenkopf, tick_start("stop <job_id>
      <grund>")/"start <type> <job_id>" vor den jeweiligen Aktionen, error(repr)
      im except; vor stop_event.wait snapshot() DANN tick_end() (Reihenfolge
      absichtlich so - in_tick_s waere nach tick_end() schon wieder None, siehe
      Modul-Docstring), bei langsamem Takt eine stderr-Zeile. Neuer Waechter-
      Thread "sched-watchdog" (_sched_watchdog_loop, alle 30 s): should_dump()
      -> logs/freeze_<Zeitstempel>.txt via dump_threads, stderr-Warnung. Hilft
      nicht bei einer faulthandler-Blockade selbst - dafuer zusaetzlich
      faulthandler.enable() in main() (vor Serverstart) und der rein lesende
      Endpunkt GET /api/debug/threads (logs/threads_<Zeitstempel>.txt, NICHT in
      _LOCKED_GET_PATHS) fuer den manuellen Dump. longrun_status() liefert
      zusaetzlich "scheduler": sched_health.snapshot().
      manage_jobs.py (:8050): /api/livestatus-Proxy reicht "scheduler"
      ungefiltert durch (result.update(data), keine Feldliste - geprueft, keine
      Aenderung noetig). renderRunning() haelt schedWarnHtml (Modul-Feld) fest,
      wenn scheduler.age_s > 90; Zeile <div class="hint warn"> (neue CSS-Klasse)
      "Scheduler seit X s ohne Lebenszeichen (Phase: ...)" erscheint sowohl im
      Leerzustand (updateRunningEmptyState liest schedWarnHtml) als auch neben
      einem aktiven Job - unabhaengig von status.active, wie gefordert.
      Gates gruen (ast.parse auf allen drei Dateien, sched_health --selftest
      T1-T7, node --check auf dem JS aller betroffenen Templates,
      manage_jobs.py --selftest, Steuerzeichen-Pruefung). KEIN Start gegen das
      EB200 (Vorgabe).
      Entscheidungslog: Scheduler-Herzschlag statt Timeout-Erhoehung - erst
      messen, wo es haengt. Doku: WS3 11.5 Absatz "Diagnose".

## Meilenstein 3: Windows-Packaging
- [ ] T-301 PowerShell-Build-Skript: Python Embeddable Package + Repo-Inhalt
      in Zielordner packen (keine Codeänderungen nötig)
- [x] T-302 (2026-08-23) start_eb200.py (Server-Start, webbrowser.open) + .bat-Launcher
- [ ] T-303 Autostart-Konzept: waterfall.py als Dauerdienst (Voraussetzung für
      Scheduler-Betriebsregel „geplante Jobs starten nur bei laufendem Server")
- [ ] T-304 Kurzanleitung Installation/Start (docs/, fließt später ins Handbuch)
- [x] T-305 (2026-08-24) start_eb200.py: Prozess-Lebenszeit koppeln per Windows-Job-Objekt
      (KILL_ON_JOB_CLOSE): Starter hängt sich selbst ins Job-Objekt, alle
      Kindprozesse (inkl. hackrf_transfer-Enkel aus sdr_waterfall.py) werden
      vom Kernel beendet, egal wie der Starter endet (Strg+C, Task-Manager,
      Fenster-X, Absturz) — heute räumt nur der Strg+C-Pfad (_shutdown) auf,
      harte Beendigung hinterlässt Waisen auf den Ports 8000–8050.
      ctypes/stdlib-only; --selftest-Erweiterung: Dummy-Kind in ein Test-Job
      hängen, Handle schließen, Kind muss enden; Fallback mit Warnung, falls
      die Job-Zuordnung fehlschlägt (Starter läuft bereits in fremdem Job).
      Voraussetzung für T-303 (Dauerdienst).
- [x] T-306 (2026-08-31, Konsolen-validiert: [r] auf waterfall, [R] alle,
      Log ohne Sweep-Zeilen) start_eb200.py: Neustart [r]/[R] im
      Statusboard, Sweep-Meldung aus dem Log, Filter [1-8] auf acht Server.
      start_eb200.py: neue Funktion _restart(st, ui, log_fh, echo) -
      terminate() (bei Zeitueberschreitung kill()), wartet danach max. 5 s
      in 0,25-s-Schritten auf einen freien Port (_check_port), bricht sonst
      mit einer Meldung ab (Status/PID unveraendert); sonst Zustand
      zuruecksetzen (proc/exit_code/port_ok/_in_traceback/_err_block, log/
      errors bleiben erhalten - Trennzeile "---- Neustart <Zeit> ----") und
      _spawn() erneut. Fuer Status UEBERSPRUNGEN (optionaler Server ohne
      Skript) nur eine Hinweiszeile. Neuer Helfer _log_line() (Muster
      _browser_open_when_ready) traegt Meldungen ausserhalb eines
      Pump-Threads in st.log UND die Logdatei ein. [r] neu startet den ueber
      [1-8] gefilterten Server (ohne Filter Hinweis "Zuerst Server waehlen"),
      [R] alle acht seriell in SERVERS-Reihenfolge (waterfall zuerst, wie
      beim Start); laeuft in einem eigenen Daemon-Thread (_restart()
      blockiert bis zu ~13 s je Server), UIState.restarting sperrt weitere
      [r]/[R] waehrend ein Neustart laeuft (Hinweiszeile "Neustart läuft").
      Filtertasten "123456" -> "12345678" (8 Server). _FOOTER1/_FOOTER2
      (neu, zwei Zeilen - "[1-8] ... [n/p] ..." plus "[r]/[R] ..." passt
      nicht mehr in eine Zeile), Zeilenbudget in _render() (fixed) um 1
      erhoeht. Plain-Modus: keine Tastaturabfrage (msvcrt.kbhit() braucht
      eine TTY) - damit kein Neustart per Taste, nur ueber Beenden+Neustart
      des ganzen Starters. --selftest erweitert: dummy_restart.py (1,5 s
      Laufzeit) fuer Fall 1 (normaler Neustart: neue PID, Status BEENDET,
      Trennzeile, "neu gestartet"-Zeile, error_count 0) und Fall 2 (Listener-
      Socket haelt einen echten Port belegt -> Abbruch nach ~5 s, Status
      unveraendert, Abbruch-Meldung im Log) - Backlog des Test-Listeners auf
      64 gesetzt (mit 1 lief die Warteschlange nach 1-2 _check_port()-Anfragen
      leer und liess den Neustart faelschlich zu frueh durchgehen, Befund
      beim ersten Testlauf).
      waterfall.py: die Konsolenzeile "[controller] Sweep fertig: ..." samt
      der davor nur dafuer berechneten n_points/rate entfernt (Kommentar an
      der Stelle) - dieselben Kennzahlen gehen weiterhin ins DScanUpdate
      fuers Frontend (Aktivitaetszeile), nur keine Zeile mehr je Sweep auf
      der Konsole. Sweep-Watchdog-Meldungen unveraendert. Select-String nach
      "Sweep fertig" ueber *.py/tools/*.py: ausser den jetzt aktualisierten
      Kommentaren in waterfall.py selbst kein Treffer in einem Selftest oder
      Tool.
      eb200_core/job_store.py: STORAGE_ROOTS_PATH jetzt am Repo-Root
      verankert (Muster DEFAULT_CATALOG_DIR in known_freq.py) statt CWD-
      relativ; betrifft nur den Default zweier Aufrufer ohne expliziten Pfad
      (waterfall.py/analyze_spectrum.py RECORD_DIR, waterfall.py
      storage-Aufzaehlung) - unter dem ueblichen Start (CWD = Repo-Root)
      identisches Verhalten, robuster bei abweichendem CWD. Aufrufer mit
      explizitem Pfad (job_store.py selbst, path_sandbox.py,
      recording_index.py) unveraendert.
      Gates gruen (ast.parse start_eb200.py/waterfall.py/job_store.py,
      start_eb200.py --selftest, eb200_core/job_store.py [_self_test laeuft
      unconditional bei __main__], python -c "import waterfall"). KEIN Start
      gegen das EB200.

## Meilenstein 4: Architektur — gemeinsame JS-Render-Schicht
- [ ] T-401 Entscheidung: gemeinsame Render-Schicht (Colormap, Auto-Scale,
      scrollbarer Viewport) für die Analyse-Server — Umfang und Schnittstelle
      festlegen (derzeit bewusst zurückgestellt)
- [ ] T-402 Umsetzung nach Freigabe von T-401
- [ ] T-403 Einheitliche Bestätigungs-/Hinweisdialoge: native confirm()/alert()-
      Browserdialoge suite-weit durch ein eigenes <dialog>-basiertes Modal ersetzen
      (eigener Titel statt Chrome-Kopfzeile „Auf localhost:PORT …", strukturierte
      Detailzeilen statt Fliesstext, Lösch-Button rot, Fokus-Default auf Abbrechen —
      Enter darf nie endgültig löschen, Warnhinweis „kein Papierkorb" abgesetzt).
      Bestandsaufnahme 2026-08-28 (Code-Stellen): confirm(): manage_jobs.py 7×
      (Job beenden/löschen, Aufnahme endgültig löschen, MSCAN-Job löschen, Lauf
      beenden/löschen, stille Kanäle aus MSCAN-Definition entfernen),
      eb200_core/block_admin.py 4× (Band/Kanal/Kanalgruppe/Datei — als
      form-onsubmit servergerendert, eigenes Ersetzungsmuster nötig),
      analyze_sessions.py 1×, analyze_spectrum.py 1×, waterfall.py 1×
      (Sweep-Dauer). alert() überwiegend Fehleranzeigen: waterfall.py 11×,
      block_admin.py 8×, manage_jobs.py 7×. Reihenfolge: zuerst
      ENDGÜLTIG-Löschen-Dialog in :8050 (manage_jobs.py) als Referenzmuster,
      dann übrige Server nachziehen.
      Nachtrag 2026-08-31: erst nach T-1400 (Token-Schicht, Meilenstein 14), damit
      das Modal von Beginn an tokenbasiert entsteht.

## Meilenstein 5: WS1 — Aktivitätsmonitoring / Memory-Verwaltung
- [x] T-501 Format von <mem_paras> (packed struct) aus Annex C verifizieren,
      alternativ RX-Umweg (MEMory:COPY RX,<dest>) bewerten - geklaert 2026-08:
      MEMory:CONTents akzeptiert ASCII-Parameterfeld, packed struct nicht noetig
- [ ] T-502 Mapping App-Kanalliste ↔ EB200-Speicherparameter definieren
      (inkl. Squelch-from-Memory)
- [ ] T-503 Sync-Funktion Kanalliste (Datei) ↔ Gerät: Diff, Upload, Download
- [ ] T-504 Technologie-Entscheidung „große" Projekt-DB (SQLite vs. Ausbau)
      und Verhältnis zur JSON-Memory-Verwaltung dokumentieren
- [~] T-505 (implementiert 2026-08-30, Browser-/Hardware-Validierung offen) Auto-Marker im
      D-SCAN-Wasserfall: serverseitige Signaldetektion je fertigem Sweep
      (eb200_core/signal_detect.py, numpy): Rauschflur = Zeit-Median je Bin über 20 Sweeps
      + Block-Median über die Frequenz (64 Bins, interpoliert); Schwelle = Flur + Δ dB
      (Regler 3–30, Default 15, seit T-505d automatisch aus Rauschstreuung); zusammenhängende Bins → ein Signal (Peak-Bin); jede
      Frequenz wird GENAU EINMAL markiert (Merkliste ±1 Bin, Reset bei Bereichswechsel,
      „Löschen" und Seitenaufbau); Pegelsprung-Schutz (>30 % Bins über Schwelle → Sweep
      übersprungen). Ereignisse im dscan-Frame (detect), Marker gelb via paintClickMarker,
      Toggle „Marker mit Frequenz". Endpunkte /api/detect?on=&delta=, /api/detect/clear.
      Hardware-Validierung 2026-08-30 (Flugfunk 118-135 MHz): einmalige Marker je Kanal
      bestätigt; Δ 8 dB zu niedrig für reale Rauschverteilung, Default anzuheben.
      T-505b (2026-08-30): Ereignisliste als Overlay (Toolbar „Liste"): Zeit/MHz/dBµV,
      neueste oben, „Kopieren" (Clipboard-API, Fallback execCommand für http im LAN),
      „CSV"-Download; Button „Restart" (Merkliste/Zähler/Liste von vorn, Canvas bleibt).
      T-505d (2026-08-30): Auto-Δ: nach Aufwärmphase/Bereichswechsel/Einschalten
      Δ = round(σ·√(2·ln N) + 2), σ = 1,4826·MAD(Sweep − Flur), begrenzt 3–30; Regler
      schaltet auf manuell, Server meldet Δ im dscan-Frame (detect_delta). Default 15
      als Fallback. Toolbar: Knöpfe gleich hoch, Raster…Restart gleich breit, Trennlinie
      vor Katalog, Texte „Klick-Marker"/„Auto-Marker"/„Markerliste" ohne Glyphen,
      Markerliste-Knopf aktiv grün, Zähler „n Marker".
      T-505e (2026-08-30): Mindestdauer: Signal erst nach n aufeinanderfolgenden Sweeps über
      der Schwelle (Streak je Bin, Default 3 ≈ 0,5 s bei 0,18 s/Sweep, Regler 1–10,
      /api/detect?min=); Marker wird in die Onset-Zeile gebrannt (streak−1 Zeilen tiefer),
      Liste zeigt Onset-Zeit; Pegelsprung setzt Streaks zurück. Auto-Marker-Einstellungen
      (Δ/automatisch, Mindestdauer + „≈ s", Marker mit Frequenz, Zähler) als Popover unter
      dem Toolbar-Knopf (▾), Seitenpanel-Block entfernt. Hinweis: ACARS-Bursts (~0,3 s)
      fallen bei n=3 heraus, n=2 behält sie.
      T-505f (2026-08-30): Gerätefüllwerte (≥150 dBµV, z. B. Rohwert 2000 im letzten Bin)
      nehmen nie an der Detektion teil (Befund: Marker „135.0010 / 200.0"). Katalog-Streifen
      der Übersicht wird auf belegte Reihen zugeschnitten (drawKnownStrip meldet 0/18/33/55 px,
      Hook knownStripResized, Wrapper overflow:hidden, Zeit-Gutter rückt nach); Änderung im
      KNOWN_STRIP_BLOCK, per tools/sync_known_block.py auf analyze_sessions/analyze_spectrum/
      sdr_waterfall abgeglichen. Folgearbeit: Katalogabgleich known_freq.
- [x] T-506 (2026-09-01, Commit c29b21c) Markerliste -> Katalog-Import (:8000, waterfall.py): Knopf "Katalog" neben
      Kopieren/CSV im Overlay #detectList. Baut zur Laufzeit ein Formular
      (method=post, action=http://<location.hostname>:8010/known/marker-import/save,
      target=eb200_catalog wie die Navigationsleiste) mit den versteckten Feldern
      data=detectRowsText(DETECT_TAB) und action=preview und sendet es ab - normaler
      Formular-POST statt fetch, damit die Portgrenze ohne CORS auskommt, und POST statt
      ?data= wegen der URL-Laengengrenze. Es wird IMMER die vollstaendige Markerliste
      uebergeben; Auswahl, Frequenzkorrektur und Duplikatentscheidung passieren in der
      Vorschaumaske von T-1015 (keine Checkboxen im Overlay). Knopf deaktiviert, solange
      autoMarkEvents leer ist. Voraussetzung: T-1015.
      Umgesetzt ohne Checkboxen im Overlay (die Auswahl passiert in der Maske auf
      :8010); Kopieren/CSV sind jetzt ebenfalls gesperrt, solange die Liste leer ist.

- [~] T-507 Messwerkzeug tools/afc_probe.py: AFC-Modusmatrix (Phase B), Ablage-
      Sensorfunktion je Demodulationsart (Phase C), Linearitaetstest der Ablage
      innerhalb +-1/2 Bandbreite (Phase D), AFC-Fangbereich (Phase E, --afc-range),
      Wiederherstellung des Vorzustands (Phase F). Stdlib-only, kein Import von
      waterfall.py, Selbsttest S1-S8 ohne Geraet. Status [~] bis Hardwarelauf.
- [~] T-514 CLAUDE.md: Gate-Formulierungsregeln ergaenzt (ast statt
      Substring fuer Verbotsproben, Soll-Werte aus der fertigen Datei,
      Verbot gilt dem Ausdruck nicht der Funktion, Prompts sind
      selbsttragend). Anlass: drei falsch spezifizierte Gates in T-507/
      T-511/T-512.
- [~] T-512 Phase-E-Urteil: ok nur bei Rueckzug in Richtung f0
      (rueckzug_pct <= -50), kein abs(). Ein Weglauf vom Traeger
      (rueckzug_pct > 0) wurde bisher als Erfolg gezaehlt; neues Feld
      "weggelaufen" und Warnung im Bericht. Selbsttest S14/S15.
- [~] T-511 afc_probe-Korrekturen aus dem Hardwarelauf: Moduswechsel in Phase C/D
      per set_demodulation() verifizieren (abgelehnte Umschaltung fuehrte zu
      Messungen unter falschem Modusnamen), konstante Null als Fehlanzeige statt
      "kein Zusammenhang", --bw unabhaengig von Phase B, --range-factors und
      monotones Fangbereichsurteil, Phase F schaltet FREQ:OFFS explizit ab,
      JSON auch im Abbruchpfad. Selbsttest S9-S13.
- [x] T-508 (2026-09) Hardwaremessung AFC-Modusmatrix und Fangbereich (afc_probe Phasen B, E).
      Zu klaeren: ob DEM CW/USB/LSB bei aktiver AFC einen Fehler erzeugt oder die
      AFC stumm abgeschaltet wird, und wie sich der Fangbereich zur eingestellten
      Bandbreite verhaelt. Befund nach docs/EB200_Geraeteprofil.md, neuer Abschnitt
      "AFC und Ablagemessung". Setzt T-507 voraus.
- [x] T-509 (2026-09) Hardwaremessung Ablage-Sensorfunktion (afc_probe Phasen C, D): liefert
      "FREQuency:OFFSet" in CW/USB/LSB verwertbare Werte, und folgt der Messwert der
      Verstimmung? Vorzeichen der Steigung ist Messergebnis, keine Annahme. Befund
      ebenfalls nach docs/EB200_Geraeteprofil.md. Setzt T-507 voraus.
      Befunde in docs/EB200_Geraeteprofil.md, Abschnitt "AFC und
      Ablagemessung" (T-513). Kernbefunde: AFC per SCPI auch in CW/IQ
      annehmbar, wirkt dort aber nicht; Fangbereich +-0,75 x BW,
      100-Hz-Raster, Weglauf-Gefahr am Rand; Ablagemessung nur in
      FM/AM/PULSe, Vorzeichen Signal minus Empfaenger.
- [ ] T-515 Aufnahme-Metadaten bei aktiver AFC: IFPAN-Aufnahmen schreiben
      meta.freq_hz aus params.center_hz (Sollwert beim Start). Laeuft die AFC
      waehrend der Aufnahme, stimmt der Wert im Manifest am Ende nicht mehr.
      Zu entscheiden: Ist-Frequenz aus dem IFPAN-OptionalHeader mitschreiben
      (Soll- UND Ist-Wert) oder Aufnahmen bei aktiver AFC ablehnen.
- [~] T-510 Stufe 1 in der Oberflaeche: AFC-Zustand (SENSe:FREQuency:CW:AFC?) im
      Kopf/Annotationsbanner anzeigen, Ist-Frequenz per Query fuehren statt den
      zuletzt gesetzten Wert anzunehmen, Hinweis beim Umschalten auf USB/LSB bei
      aktiver AFC. Setzt T-508 voraus.
      Umgesetzt (Browser-Validierung offen): Poll um FREQ:AFC? und FREQ?
      erweitert, abgeleitetes Feld afc_wirksam (AFC wird in CW/IQ angenommen,
      regelt dort aber nicht), Anzeige afcHint mit drei Zustaenden, Warnung
      bei USB/LSB-Wahl mit aktiver AFC. freq_hz auf 100 Hz gerundet, damit
      die AFC-Nachfuehrung keinen SSE-Sturm ausloest. Zoom-Kopf zeigt bereits
      den Ist-Wert (center_hz kommt aus dem IFPAN-OptionalHeader).
      Selftest T11-T14.

## Meilenstein 6: Dokumentation / Benutzerhandbuch
EINGEFROREN 2026-09-20 mit dem Stand de-final. Das deutsche Handbuch wird nicht
fertiggestellt; die offenen Teile T-604 (Kapitel 9-11), T-605 (Teil D) und T-606
(Teil E) bleiben als Entwurf stehen und dienen als Quellenmaterial fuer das
englische Handbuch in Meilenstein 19. T-607 (Typst) wandert ebenfalls dorthin.
Begruendung: die Oberflaeche wird englisch (Meilenstein 18), ein deutsches
Handbuch beschriebe dann Bedienelemente, die es nicht mehr gibt.
- [x] T-601 (2026-08-29) Gliederung Teil A-E festgelegt, Zielgruppe alle
      EB200-Besitzer, Markdown unter handbuch/, Releases spaeter per Typst
- [x] T-602 (2026-08-29) Teil A (Kapitel 1-5) im Entwurf
- [x] T-603 (2026-08-29) Teil B (Kapitel 6) im Entwurf
- [~] T-604 Teil C: Kapitel 7 und 8 im Entwurf, 9-11 als Stichworte
- [ ] T-605 Teil D (Kapitel 12)
- [ ] T-606 Teil E (Kapitel 13-18 und Anhaenge)
- [ ] T-607 Typst-Aufbereitung fuer Releases
- [ ] T-608 station.json um eb200_ip/local_ip erweitern (Befund A3a)
- [ ] T-609 Ablage-Asymmetrie sessions/ gegen storage_roots (Befund A2)
- [x] T-610 (2026-09-03) numpy-Abhaengigkeit von :8000 geklaert (Befund A1): Regel neu
      gefasst statt Code umgebaut. numpy ist ab sofort zugelassene Pflichtabhaengigkeit
      der gesamten Suite, auch in eb200_core/; eb200_core/signal_detect.py bleibt
      unveraendert. Weitere Fremdbibliotheken bleiben optional und gekapselt.
      Siehe Entscheidungslog 2026-09-03 und CLAUDE.md.
- [x] T-611 (2026-09-03) requirements.txt im Repo-Root angelegt: numpy als einzige
      Pflichtabhaengigkeit, matplotlib als auskommentierte Kuer-Abhaengigkeit fuer
      session_analyze.py --plots.
- [ ] T-612 Handbuch Kapitel 4: Abhaengigkeitsabsatz korrigieren. Der Entwurf behauptet,
      die Kern-Server kaemen mit der Standardbibliothek aus und numpy sei nur fuer
      :8030/:8070 noetig - das ist falsch. Neuer Text: CPython und numpy sind
      Grundvoraussetzung (Windows: pip install -r requirements.txt; Pi OS:
      sudo apt install python3-numpy), matplotlib bleibt optional.
- [~] T-613 start_eb200.py: fehlendes numpy beim Start abfangen und als Klartext-Hinweis
      mit Installationsbefehl melden, statt einen ImportError-Traceback aus einem
      Kindprozess durchschlagen zu lassen. Umsetzung: _numpy_missing_hint() liefert
      Zeilenliste oder None, Aufruf in main() NACH dem --selftest-Zweig, Exit-Code 2.
      Plattformabhaengiger Installationsbefehl (Windows pip, sonst apt). Zusaetzlich
      Docstring-Absatz zum optionalen SDR-Wasserfall korrigiert (SoapySDR verworfen,
      numpy ist Grundvoraussetzung statt Zusatzabhaengigkeit).
      (implementiert 2026-09-03; Validierung offen: normaler Start mit numpy muss
      unveraendert alle Server hochfahren)
- [x] T-614 (2026-09-03) CLAUDE.md wird versioniert: aus der Liste der nie zu stagenden
      lokalen Konfiguration entfernt und regulaer ins Repo aufgenommen; Regel zur
      Gate-Disziplin ergaenzt. Maschinenlokal bleiben station.json und storage_roots.json.
      Begruendung im Entscheidungslog 2026-09-03.

## Meilenstein 7: Wasserfall-GUI — Bedienkomfort (waterfall.py)
- [~] T-701 (implementiert, Browser-/Hardware-Validierung offen) Skala min/max je Ansicht merken: D-SCAN-Übersicht und IFPAN-Zoom
      erhalten getrennte Farbskalen-Zustände (min/max/Auto); Ansichtswechsel
      stellt den jeweils gemerkten Zustand wieder her; Persistenz über
      Seiten-Reload via localStorage.
- [~] T-702 (implementiert, Browser-/Hardware-Validierung offen) Squelch-Bedienung im IFPAN-Zoom: Ein/Aus + Schwellen-Regler für
      OUTP:SQU:THR (-30..+110 dBµV, Annex C); zusätzlich Toggle „folgt
      Skala min": Squelch-Schwelle zieht mit dem Skala-min-Regler der
      Zoom-Ansicht mit (alles unterhalb Skala min wird stummgeschaltet).
- [~] T-703 (implementiert: SCPI-Polling device_params + SSE-Push, Mod/BW-Sync, Squelch-Sync, Speed-Dropdown — Hardware-Validierung offen)
- [~] T-704 Kosmetik Long-Run-Maske (:8000): fuenfzeilig (Start/Ende/Endzeitpunkt -
      Dezimierung|Framerate-Hinweis + Rotation - Ablage + Budget), Titel groesser,
      Zeilenabstand 12px, Dauer-Presets 1 h und 3 h, Budgetzeile ohne
      Freiraum-Anhang (Info steckt im Ablage-Dropdown; 90-%-Sperre bleibt).
      (implementiert 2026-08-30; Browser-Validierung offen)
- [~] T-705 Long-Run-Maske (:8000) in drei umrandete Bloecke gegliedert
      (Empfaenger (EB200) / Job-Aufnahme / Speicherung, <fieldset>+<legend>);
      Eingabefelder links buendig (.lk-Span mit fester Breite); Audio-Checkbox
      in den Job-Block verschoben (IFPAN-only-Zeile); Framerate-Hinweis steht
      bei IFPAN an der Stelle des Dezimierung-Dropdowns.
      (implementiert 2026-08-30; Browser-Validierung offen)
- [x] T-706 Kosmetik IFPAN-Einstellungspanel (:8000): Blockabstand h3 18px
      (wirkt auch in der Übersicht), Trennlinie vor "Aufnahme", Aufnahme-Block
      dreizeilig (Mithören / Audio+Spektrum / Long Run) mit gleich breiten
      Knöpfen; Hinweiszeile unter Demodulation bei unzulässiger Kombination
      Modulation/ZF-BW (USB/LSB/CW > 9 kHz, IQ > 15 kHz; Annex B "Bandwidth
      too high with SSB/CW") - rein informativ, Readback T-703 korrigiert.
      (implementiert 2026-08-30; Browser-Validierung offen)
- [~] T-707 Kosmetik D-SCAN-Übersicht (:8000): Breitenangabe mit korrekter
      Einheit (formatBreite: ≥1 MHz in MHz, sonst kHz; toLocaleString de-DE
      entfernt - "16.000 kHz" war irreführend), Header-Untertitel
      "Breitband-Scan", Einstellungs-Panel in vier fieldset-Blöcke
      (Scan-Bereich / Bandbreite & Speed / Zusatz-Zoom mit SDR / Aufnahme,
      Optik wie Long-Run-Maske), SDR-Checkbox "Wähle SDR", Toolbar-Toggles
      Raster/Marker/Katalog/Regional gleich hoch/breit (vormals
      Bekannte/Region). (implementiert 2026-08-30; Browser-Validierung offen)
- [x] T-708 Feinabstimmung der IFPAN-Mittenfrequenz per Mausrad/Pfeiltasten
      (Zoom-Ansicht): Rad/Pfeil = 1 Hz, +Shift = 10 Hz, +Strg = 100 Hz,
      Pfeiltaste halten x10/x100 (zeitbasiert); neuer Endpoint /api/tune
      (inline nur FREQ, kein Zoom-Neuaufbau; 423 waehrend Long-Run-Jobs,
      409 waehrend WAV-/Specbin-Aufnahme); Wasserfall-Historie wird
      frame-getrieben frequenzrichtig verschoben (OptionalHeader-Mitten-
      frequenz, Bruchteil-Pixel aufsummiert); centerFreqBig jetzt mit
      6 Nachkommastellen (Hz-genau). (implementiert 2026-08-30;
      Browser-/Hardware-Validierung bestanden 2026-08-30; Pfeiltasten-Teil
      mit T-709 wieder entfernt)
      Gate-Nachtrag: Soll /api/tune = 5 (Kommentarzeilen mitgezaehlt).
- [x] T-709 Feinabstimmung nachgebessert (Hardware-Befunde T-708):
      Pfeiltasten-Steuerung samt Halte-Beschleunigung ersatzlos entfernt;
      neue Rad-Stufe Shift+Strg = 1 kHz; Aufnahme-Sperre jetzt wirksam:
      clientseitige Pruefung (audioRec/spectrumRec.isActive) mit sofortigem
      Hinweis VOR dem optimistischen Anzeige-Update, Zielwert-Rollback
      (tuneReset) bei jeder Nicht-200-Antwort (Phantomwert-Befund), 409-Log
      im Server zur Diagnose. Tooltip aktualisiert.
      (implementiert 2026-08-30; Browser-/Hardware-Validierung bestanden
      2026-08-30: Sperre bei WAV-/Specbin-Aufnahme greift clientseitig,
      1-kHz-Stufe ok, Pfeiltasten folgenlos)
      Gate-Nachtrag: Soll T-709 = 5 (Kommentarzeile in E9 mitgezaehlt).
- [~] T-710 AGC/MGC-Bedienung auch in der D-SCAN-Uebersicht (:8000): neuer
      Panel-Block "Verstaerkung" (dscanGainMode/dscanGainMgc) mit denselben
      Grenzen wie im Zoom (-30..110 dBuV, Annex C GCON:MODE/GCON:MGC). Beide
      Bedienpaare spiegeln sich gegenseitig (paintGainUi), gemeinsamer Guard
      gegen den SSE-Push (syncDeviceParams), Long-Run-Sperre erfasst die
      Verstaerkung mit. Server: _configure_dscan() stellt die Verstaerkung als
      SOLL her (Muster _configure_ifpan). Nachtrag: die zunaechst inline im
      Overview-Loop verarbeitete Umschaltung kam am Geraet nicht an - siehe
      T-711.
      (implementiert 2026-09-03; Browser-/Hardware-Validierung offen)
- [~] T-711 Nachbesserung T-710 (Hardware-Befund: MGC/AGC kam im D-SCAN nicht
      am Geraet an). (a) _configure_dscan() setzt die Verstaerkung jetzt NACH
      "FREQ:MODE DSC" und VOR "INIT:IMM" (Reihenfolge wie _configure_ifpan);
      im laufenden Sweep nahm das Geraet die Umschaltung nicht an. (b) Der
      Overview-Loop verarbeitet "gain" nicht mehr inline, sondern gibt es wie
      range/speed/BW zurueck - ABOR + Neuaufbau mit der neuen Verstaerkung.
      (c) normalize_device_params(): GCON:MODE? antwortet mit FIX|AUTO
      (Handbuch 4.79), FIXed ist das Synonym fuer MGC - die bisherige
      Zuordnung FIXed -> "AGC" meldete ein MGC-Geraet als AGC zurueck und die
      Oberflaeche stellte das Bedienelement bei jedem Poll auf AGC zurueck;
      Selbsttest T9 entsprechend nachgezogen. (d) Ruecklese-Beleg unter
      VERIFY_SCPI: GCON:MODE?/GCON:MGC? nach dem D-SCAN-Aufbau, Meldung in
      Konsole und Browser nur bei Abweichung.
      (implementiert 2026-09-03; Browser-/Hardware-Validierung offen)
- [x] T-712 (2026-09-05) Sweep-Watchdog der D-SCAN-Uebersicht baut wirklich neu auf
      (Hardware-Befund: nach Bedienung am Frontpanel bricht der Scan ab, der
      Watchdog meldete "Starte neu", verwarf aber nur den Puffer - die
      Uebersicht blieb bis zum Serverneustart stehen). Bei 20 s ohne jedes
      D-SCAN-Datagramm verlaesst _run_overview_loop() die Schleife mit
      ("overview", None); ABOR im finally, danach voller _configure_dscan()
      samt UdpPath-Neuanmeldung und INIT:IMM. cmd wird vor dem return gesetzt,
      damit eine laufende Session-Aufzeichnung NICHT beendet wird. Die
      SCPI-Fehler-Queue (drain_errors) wird in Konsole und Browser-Hinweis
      mitprotokolliert; Erholungszaehler _dscan_watchdog_rebuilds wird beim
      ersten vollstaendigen Sweep zurueckgesetzt und der Erfolg einmal
      protokolliert. Der Fall Paketverlust (Pakete > 0) bleibt unveraendert.
      Hardware-validiert 2026-09-05 (S/N 101.459): Abbruch per ABOR aus einer
      zweiten TCP-Sitzung und per ESC am Frontpanel, Erholung ohne
      Serverneustart, laufende Session-Aufzeichnung ueberlebt den Neuaufbau,
      Mehrfachversuche zaehlen hoch und der Zaehler wird beim ersten
      vollstaendigen Sweep zurueckgesetzt, keine Fehlausloesung bei Speed
      MTIME. Schwelle fuer den Null-Paket-Fall danach auf 10 s -> T-719.
      (implementiert 2026-09-03; hardware-validiert 2026-09-05)
- [x] T-719 (2026-09-05) Sweep-Watchdog reagiert auf voellige Stille schon nach 10 s
      (Wunsch OE3GAS nach der Validierung von T-712). Neue Konstante
      SWEEP_SILENCE_S = 10.0 nur fuer den Fall "kein einziges Datagramm im
      laufenden Sweep". SWEEP_WATCHDOG_S bleibt bei 20 s, weil es die
      maximale Sweepdauer ist und den Zweig Paketverlust (Pakete > 0)
      steuert - ein gesunder LOW-Sweep ueber weiten Bereich bei schmaler BW
      dauert laenger als 10 s und wuerde sonst zyklisch verworfen, ohne je
      fertig zu werden. _sweep_watchdog_s() und die MTIM-Kopplung bleiben
      unveraendert.
      Hardware-validiert 2026-09-05 (S/N 101.459): Hinweis erscheint nach
      rund 10 s statt 20 s, Erholung wie bei T-712; Gegenprobe mit Speed LOW,
      schmaler BW und weitem Bereich ohne Fehlausloesung. Docstring von
      _sweep_watchdog_s() um die zweite Frist ergaenzt.
      (implementiert und hardware-validiert 2026-09-05)
- [~] T-1010 Navigationsmenü "Ansichten" in waterfall.py (:8000) mit Direktlinks zu :8010/:8020/:8040/:8050 (Host via location.hostname); Browser-Validierung offen. Erweiterung um :8060 → T-1107. Nachtrag (2026-08-28): Menüliste einspaltig.

- [x] T-713 (2026-09-04) Fenstergroessen-Aenderung (:8000): statt Hinweis mit Knopf laedt die
      Seite jetzt selbsttaetig neu - entprellt, rund 0,8 s nach der letzten
      Aenderung, Schwelle unveraendert 100 px in Breite oder Hoehe. #resizeHint
      bleibt als kurze Rueckmeldung sichtbar, der Knopf reloadBtn entfaellt.
      Die Wasserfall-Historie im Browser geht beim Reload verloren (bewusste
      Entscheidung 2026-09-04).
      (implementiert und browser-validiert 2026-09-04)

- [x] T-714 (2026-09-05) Beschriftung des BW-ZOOM-Knopfes (:8000,
      D-SCAN-Uebersicht) zeigt die Aktion statt des Zustands: bei
      ausgeschaltetem Zoom "BW-ZOOM
      einschalten", bei eingeschaltetem "BW-ZOOM ausschalten". Bisher stand
      dort der Zustand ("BW-ZOOM: aus"), was beim Bedienen genau andersherum
      gelesen wurde. Der Zustand bleibt in der Parameter-Tafel ueber dem
      Wasserfall sichtbar ("BW-Zoom ein"/"BW-Zoom aus") - dort ist er richtig,
      weil die Tafel den aufgezeichneten Sweep beschreibt. Zusaetzlich ein
      title-Tooltip am Knopf. Keine Aenderung an /api/dscan_bwzoom oder am
      Geraetezustand.
      Nachtrag 2026-09-05: handbuch/arbeit/terminologie.md nachgefuehrt
      (Knopfbeschriftung und Schreibweisen-Tabelle, dort jetzt Fliesstext-
      Schreibweise und Knopfbeschriftung getrennt), ebenso Kapitel 8.3 in
      handbuch/08_dscan_wasserfall.md. docs/WS3 - Wasserfall.md nennt nur den
      Funktionsnamen und bleibt unveraendert.
      (implementiert und browser-validiert 2026-09-05)

- [~] T-715 Segment-Textierung in der Session-Auswertung (:8020) und
      Betriebsparameter im Session-Store. Die segment-Tabelle
      (eb200_core/session_store.py) bekommt vier nullable Spalten bw_hz,
      bw_zoom, gain_mode, gain_mgc_dbuv; sie werden vom Controller
      (waterfall.py, Aufruf rec.record()) mitgeschrieben und erzwingen wie
      speed/mtime_s eine Segmentgrenze - ohne das spannte ein Segment ueber
      einen BW- oder Verstaerkungswechsel hinweg. analyze_sessions.py liest
      die Spalten ueber PRAGMA table_info (aeltere Session-DBs haben sie
      nicht) und liefert sie in /api/session und /api/segment mit null aus.
      Segmentkarte jetzt fuenfzeilig: Frequenzbereich mit Spanne (adaptiv
      MHz/kHz), Uhrzeit von/bis mit Dauer (s / m:ss min / h:mm:ss h) ohne
      Datum, "Schrittweite ... Hz (Bandbreite: ... kHz, n Bins)", Betriebs-
      zeile "BW-Zoom: an/aus · AGC: auto" bzw. "MGC: nn dBµV", Sweeps/Rate.
      Die Betriebszeile entfaellt bei Sessions vor T-715. Kopfzeile der
      Segment-Vollansicht einzeilig mit denselben Bausteinen.
      Gate-Nachtrag: Soll 'Schrittweite ' in HTML_PAGE = 3, nicht 2 - die Datei
      fuehrt das Wort bereits im Kommentar zu computeNiceTicks. CC hat den
      falschen Soll-Wert korrekt gemeldet statt ihn auszugleichen.
      (implementiert 2026-09-05; Browser-Validierung offen)

- [x] T-721 (2026-09-06) Mittenfrequenz in der Kopfleiste prominenter (Wunsch
      OE3GAS 2026-09-06). Schriftgroesse 1.25em -> 2.5em, font-weight 700,
      mittige Platzierung ueber ein zweites .spacer-Element links der Anzeige
      (Spacer-Variante statt position:absolute, damit bei schmalem Fenster
      keine Ueberlappung mit Status-Link und Theme-Umschalter entsteht).
      display inline -> inline-block wegen min-width:13ch, flex:0 0 auto gegen
      Stauchung, body.zoom #headerBar mit reduziertem vertikalem Padding.
      Neue Funktion formatCenterMHz() vereinheitlicht die drei bisherigen
      Schreibstellen (startZoom 4 Stellen, applyTuneStep und updateMetaZoom
      je 6 Stellen) auf durchgehend sechs Nachkommastellen mit
      Dreiergruppierung (3.626 075 MHz): erste Gruppe kHz-Anteil, zweite
      Hz-Anteil. Trennzeichen normales Leerzeichen, bei tabular-nums genau
      Ziffernbreite. Der title-Tooltip erklaert die Gruppierung und behaelt
      die bisherige Feinabstimm-Legende. Vorarbeit fuer die Direkteingabe der
      Mittenfrequenz per Tastatur.
      Gate-Nachtrag: Soll 'centerFreqBig' = 10, nicht 9 - der neue
      HTML-Kommentar zum Spacer-Paar nennt den Bezeichner selbst, der
      Soll-Wert im Prompt stammte aus dem Text vor den Edits. CC hat die
      Abweichung korrekt gemeldet statt sie auszugleichen.
      Browser-validiert 2026-09-06 (OE3GAS): Anzeige mittig, gross und fett,
      Gruppierung lesbar, Feinabstimmung laesst die Anzeige ruhig stehen,
      Uebersichtslayout unveraendert, Tooltip vollstaendig.
      (implementiert 2026-09-06, Commit f59772d; browser-validiert 2026-09-06)

- [x] T-722 (2026-09-06) Direkteingabe der IFPAN-Mittenfrequenz per Tastatur
      (Wunsch OE3GAS 2026-09-06, setzt T-721 voraus). Klick auf die grosse
      Anzeige oder Taste f bzw. Enter blendet an ihrer Stelle ein
      Eingabefeld ein (body.freqedit, zweiklassige CSS-Regeln schlagen die
      Sichtbarkeitsregel aus T-721). Enter uebernimmt, Esc und Fokusverlust
      verwerfen. parseFreqInput() nimmt Komma wie Punkt, entfernt
      Leerzeichen (auch die Gruppierungsluecken der Anzeige) und liest die
      Einheit aus der Endung Hz/kHz/MHz/GHz bzw. k/M/G; ohne Endung gilt
      MHz. Statt einer Heuristik zeigt ein Echo unter dem Feld waehrend des
      Tippens den interpretierten Wert und meldet ungueltige Zahlen sowie
      Werte ausserhalb 10 kHz..3 GHz in Warnfarbe. Der uebernommene Wert
      geht innerhalb des halben Spans ueber applyTuneStep (Historie bleibt,
      die frame-getriebene Shift-Logik zieht sie mit), darueber hinaus ueber
      startZoom (Neuaufbau). Gesperrt bei Long-Run-Job und bei laufender
      Audio-/Spektrum-Aufnahme - fuer BEIDE Wege, da ein Frequenzsprung
      mitten in einer Aufnahme die Datei genauso entwertet wie eine
      Verstimmung; erneut geprueft beim Uebernehmen, nicht nur beim Oeffnen.
      Kein Server-Code: /api/tune und /api/zoom bestehen unveraendert.
      Browser-validiert 2026-09-06 (OE3GAS): Oeffnen per Taste f und per
      Klick, Echo und Bereichspruefung, Uebernahme innerhalb des Spans mit
      erhaltener Historie und Bandwechsel mit Neuaufbau, Esc und
      Fokusverlust verwerfen, Sperren bei Aufnahme greifen.
      (implementiert 2026-09-06, Commit 9c3fa87; browser-validiert 2026-09-06)

- [x] T-720 (2026-09-05) Segment-Wasserfall der Session-Auswertung (:8020) auf Fensterbreite
      (Wunsch OE3GAS 2026-09-05). sizeSegWaterfall() setzt jetzt auch die
      Breite: innerWidth - SEG_SIDE_PX (435), mindestens 720, hoechstens 3200,
      Vielfache von 4; fullCanvas, freqAxis und known-f bekommen denselben
      Wert. Muster und Grenzen aus sizeWaterfall() in analyze_spectrum.py
      (T-1409a). Die width="900"-Attribute im HTML bleiben als Anfangswert.
      Der bestehende resize-Handler zieht die Breite ohne Neuladen nach, weil
      er sizeSegWaterfall() ohnehin aufruft. Hoehe, Cache-Logik und der
      geteilte KNOWN-Block unveraendert.
      Browser-validiert 2026-09-05 (OE3GAS): Breite folgt dem resize, Achse und
      Katalog-Streifen bleiben buendig, Klick-Marker aus dem Befund-Navigator
      sitzt bei jeder Fensterbreite auf der erwarteten Frequenz.
      (implementiert 2026-09-05, Commit 7487b16; browser-validiert 2026-09-05)

- [x] T-716 (2026-09-05) Wasserfall-Versatz nach jedem Sweep-Neuaufbau behoben. Ursache
      (Befund OE3GAS 2026-09-05, sichtbar beim Verstellen des MGC-Reglers):
      SweepAccumulator setzt Sweeps rein durch Aneinanderhaengen der Chunks
      zusammen, der D-SCAN-OptionalHeader (Annex F) enthaelt keine Position des
      Chunks innerhalb des Sweeps. Nach ABOR blieben Datagramme des
      abgebrochenen Sweeps im UDP-Empfangspuffer stehen; die neu aufgebaute
      Schleife las eines davon zuerst und erklaerte es zum Sweep-Anfang. Der
      Phasenversatz blieb dauerhaft, weil ueberzaehlige Samples je Sweep
      verworfen werden und sich die Chunk-Grenzen dadurch Sweep fuer Sweep
      wiederholen. Neu: _drain_udp() leert den Puffer zwischen ABOR und
      INIT:IMM (in _configure_dscan, nach DRAIN_SETTLE_S Wartezeit fuer noch
      fliegende Pakete, gedeckelt durch DRAIN_MAX_DATAGRAMS) und protokolliert
      die Zahl der verworfenen Datagramme - diese Zahl ist zugleich der
      Nachweis der Ursache. Gilt fuer alle Neuaufbau-Anlaesse: Verstaerkung,
      Bereich, BW, Speed, BW-ZOOM und Sweep-Watchdog (T-712).
      (implementiert und hardware-validiert 2026-09-05: MGC-Regler mehrfach
      verstellt bei 150-200 MHz / BW 50 kHz / Speed HIGH - der Wasserfall
      bleibt seitlich stehen, die Konsole meldet je Neuaufbau verworfene
      Datagramme. Auch das sporadische Zuruckspringen aus dem Ausgangsbefund
      trat nicht mehr auf.)
      Gate-Nachtrag: Soll DRAIN_SETTLE_S = 3 (Konstantenkommentar nennt den
      Namen mit), im Prompt mit 2 angesetzt.

- [x] T-717 (2026-09-05) Verstaerkung in der Parameter-Tafel ueber dem D-SCAN-Wasserfall:
      neuer Teil 'gain' in composeParameterAnnotation() ("AGC" bzw.
      "MGC nn dBµV"), und sendGain() setzt im Uebersichtsmodus pendingHeader,
      damit die Aenderung wie Bereich/BW/Speed eingebrannt wird. Ohne das war
      ein Pegelsprung im Wasserfall im Nachhinein nicht mehr zuzuordnen. Der
      Parameter von sendGain heisst jetzt modeArg - der bisherige Name mode
      verdeckte die gleichnamige globale Ansichtsvariable ('overview'|'zoom'),
      die neue Abfrage haette sonst den Verstaerkungsmodus geprueft.
      Ruecklesen ueber syncDeviceParams brennt bewusst KEIN Banner (nur echte
      Bedienung laeuft ueber sendGain).
      (implementiert und browser-validiert 2026-09-05: Umschalten AGC/MGC und
      Aendern des MGC-Pegels brennen je eine Parameter-Tafel ein, der
      Verstaerkungsteil erscheint dabei gelb/fett; ein SSE-Ruecklesen allein
      erzeugt keine Tafel.)
      Gate-Nachtrag: Soll "dscanGainMgc').value" = 2, nicht 1 - paintGainUi
      ZUWEIST an dieselbe Eigenschaft und eine Substring-Zaehlung trennt Lesen
      und Zuweisen nicht (CLAUDE.md, "Verbotsproben auf CODE laufen ueber
      ast"). CC hat den falschen Soll-Wert korrekt gemeldet.

- [ ] T-718 Absoluter Anker fuer den Sweep-Zusammenbau untersuchen. T-716
      beseitigt den Versatz beim Neuaufbau, nicht aber den bei Paketverlust
      mitten im Sweep: auch dann fuellt der Puffer erst mit Samples des
      Folgesweeps auf und die Phase rutscht dauerhaft. Messen, ob die
      Chunk-Geometrie einen Anker hergibt (z. B. ein kuerzerer letzter Chunk je
      Sweep): die Folge der n_items je Sweep ueber einige Sweeps protokollieren,
      fuer mehrere Bereichsgroessen, Schrittweiten und Speed-Stufen. Ergibt sich
      ein verlaesslicher Sweep-Endemarker, kann sich SweepAccumulator daran
      dauerhaft resynchronisieren; sonst bleibt es beim Verwerfen und der
      Versatz ist als Restrisiko zu dokumentieren.

- [x] T-723 (2026-09-12, Commit 83a9467, browser-validiert 2026-09-12)
      Dezimierungshinweis der Session-Analyse (:8020) nennt Sweepzahl und
      Faktor. Bisher stand ueber dem Segment-Wasserfall nur "Zeitachse
      dezimiert (zu viele Sweeps fuer volle Aufloesung)" - weder die Zahl der
      aufgezeichneten Sweeps noch die Staerke der Zusammenfassung waren
      ablesbar, obwohl beides bestimmt, wie weit man dem Bild bei kurzen
      Signalen trauen darf. Neu:
      "Zuviele Sweeps zur vollen Anzeige. <n> Sweeps, Dezimierung um Faktor
      <k>". decimate_max_pool() gibt die Gruppengroesse als dritten Wert
      zurueck - sie IST der Dezimierungsfaktor und wird damit an genau einer
      Stelle bestimmt, statt beim Aufrufer aus Zeilenzahlen nachgerechnet zu
      werden (aus n und len(out) laesst sich k nicht zuverlaessig
      zurueckrechnen). /api/segment reicht sie als "decimation" durch, dazu
      "n_rows_raw" - die tatsaechlich geladene Sweepzahl, bewusst NICHT
      segment.row_count, das die laufende Aufzeichnung fortschreibt und bei
      einem abgebrochenen Lauf abweichen kann. "decimated" bleibt
      unveraendert und entscheidet weiterhin, OB der Hinweis erscheint.
      Schwelle unveraendert FULL_ROW_THRESHOLD = 5000, mode=thumb
      (130 Zeilen) unberuehrt.
      Gate-Nachtrag: G6 war als "beide Byte-Zahlen gleichen den Werten der
      Vorpruefung" formuliert und damit bei einem Edit, der Zeilen
      hinzufuegt, unerfuellbar (CRLF 2640 -> 2654). Gemeint war die
      Invariante CRLF == LF == CR. CC hat korrekt gestoppt statt
      auszugleichen; ein Korrekturprompt hat das Gate ersetzt
      (Konsistenzprobe plus git diff --numstat 20/6). Merksatz: ein
      Baseline-Schnappschuss taugt nur als Gate, wenn der Edit die
      Zeilenzahl nicht aendert - sonst gehoert die Invariante ins Gate, nicht
      der Absolutwert.
      Abweichung im Commit 83a9467: die im Prompt vorgegebenen
      Attribution-Zeilen (Co-Authored-By / Claude-Session) fehlen in der
      Commit-Message - eine Systemanweisung der CC-Sitzung untersagte sie;
      sonst wortgleich.

## Meilenstein 8: View-Editing — Notizen, Verlinkung, Löschen
- [x] T-801 (2026-08-23, war bereits implementiert: /api/label + /api/note in :8020/:8040) Freitext/Notizen-Editor für Sessions (:8020) und Spektrum-
      aufnahmen (:8040): Sidecar-basiert, Messdaten (Session-DB, .specbin)
      bleiben unangetastet; atomares Schreiben mit .bak wie beim
      Known-Freq-Editor
- [x] T-802 (2026-08-23) Verlinkung D-SCAN-View → Session-Edit (Einstiegspunkt zu T-801
      aus der Session-Ansicht in :8020)
- [x] T-803 (2026-08-23) Verlinkung IFPAN-View → Spektrum-Edit (Einstiegspunkt zu T-801
      aus der Spektrum-Ansicht in :8040)
- [x] T-804 (2026-08-23) Löschen von Sessions/Spektrumaufnahmen aus den Views:
      Bestätigungsdialog, Sandbox-Pfadprüfung, zugehörige Sidecars
      mitlöschen; Erweiterung der Messdaten-Invariante (Löschen ganzer
      Datensätze erlaubt, Verändern von Inhalten weiterhin verboten)
      vor Umsetzung im Entscheidungslog festgehalten
- [x] T-805 (2026-09-12, Commit 01223c2, Browser-validiert) Session-Liste (:8020),
      Spaltenbild: Kurztext direkt hinter Datei/Spektrogramm statt am
      Zeilenende; neue Spalte "Dauer" hinter Ende (adaptiv s/min/h über das
      vorhandene fmtDauer); Breite in Klammern hinter der Frequenzspanne
      (fmtBreite, adaptiv MHz/kHz); "Segmente / Sweeps" in zwei eigene,
      getrennt sortierbare Spalten aufgeteilt. Reine Anzeige — keine neuen
      Serverfelder, /api/sessions unverändert.
- [x] T-806 (2026-09-12, Commit 01223c2, Browser-validiert) Session-Liste (:8020),
      Thumbnail-Cache: bisher las /api/segment?mode=thumb bei JEDEM
      Seitenaufbau sämtliche Sweeps jeder Session aus der DB — das wächst mit
      der Zahl der Aufzeichnungen. Neu ein regenerierbarer Sidecar
      <name>.thumb.json (Format eb200-sessthumb-v1) neben der DB mit den
      fertigen Thumbnail-Payloads je Segment-ID; gültig nur zu genau einem
      Dateistand (mtime_ns + Größe, db_fingerprint()). Laufende Sessions
      (frische -wal-Datei) werden nicht gecacht. Zusätzlich wird mode=thumb
      auch über die Bin-Achse max-gepoolt (THUMB_TARGET_BINS = 300), weil das
      Canvas nur 160 bzw. 300 px breit ist — n_bins nennt im Thumb-Payload
      deshalb die gelieferten Spalten, n_bins_raw die der Aufzeichnung. Die
      "live"-Heuristik liegt jetzt einmal in session_is_live(). Der Sidecar
      fällt beim Löschen mit (eb200_core/recording_index.py).
- [x] T-807 (2026-09-12, Commit b915e68, Browser-validiert) Session-Liste (:8020),
      Feinschliff nach der Validierung von T-805: die Tabelle füllt nicht mehr
      die volle Fensterbreite (#sessionList width:auto, linksbündig) — auf
      einem breiten Bildschirm riss das die Zeilen auseinander; "Segmente" und
      "Sweeps" rechtsbündig mit tabular-nums (cls 'num', Muster :8040); der
      Kurztext steht in einer textarea von Thumbnail-Höhe (52 px), bricht um
      statt zu scrollen und bleibt inhaltlich einzeilig — Enter speichert
      weiterhin, maxlength bleibt 128 (MAX_LABEL_LEN).

## Meilenstein 9: SDR/IQ-Funktionalität
- [x] T-901 (2026-08-31) IQ-Aufnahme über AUDIO-Kanal (Mode 1 dynamisch,
      Mithören-Sperre, Job-Audio-Ablehnung, Sidecar-Flag, tools/wav2sdr.py).
      Teil B Hardware-Validierung: Träger 5,19 kHz rechts der Mitte,
      wav2sdr --analyze liefert +5189,5 Hz -> Kanal 0 = I, Kanal 1 = Q
      bestätigt; DC-Bin -75,7 dB relativ zum Peak -> keine DC-Kompensation
      nötig. Kreuztest HDSDR entfällt (Vorzeichen eindeutig).

- [x] T-902 (2026-09-01) tools/eye_diagram.py: Augendiagramm und Symbolparameter (Hub,
      Symbolrate, Augenoeffnung, Jitter, Pegelanzahl) aus IQ-Aufnahmen,
      SVG- und JSON-Ausgabe, Selftest T1-T10. Validierung an DWD Pinneberg DDK2
      (4583 kHz, RTTY, Soll 50 Bd / +-225 Hz, Aufnahme 2026-09-01,
      --decimate 16 --min-baud 20): Hub 221,4 Hz (1,6 % Abweichung),
      Symbolrate 48,26 Bd (Zufallsfolgen-Fall).
- [x] T-903 (2026-09-01) eye_diagram.py: Dezimation (--decimate) und erweiterter
      Ratenbereich (--min-baud) fuer Schmalbandsignale, --suggest als
      Parametervorschlag, Selftest T11-T13. Validierung an DWD Pinneberg DDK2
      (4583 kHz, RTTY, Soll 50 Bd / +-225 Hz, Aufnahme 2026-09-01,
      --decimate 16 --min-baud 20): Hub 221,4 Hz (1,6 % Abweichung),
      Symbolrate 48,26 Bd (Zufallsfolgen-Fall).
- [x] T-904 (2026-09-01) eye_diagram.py: PSK-Pfad (--mode psk) mit Traegerversatz und
      Modulationsordnung ueber M-te Potenz, Symbolrate aus der Huellkurve,
      Konstellationsdiagramm, Phasenhistogramm und EVM. Selftest T14-T18.
      Validierung an HFDL Shannon (Traeger 10082,440 kHz = 10081 kHz +
      1440 Hz Subtraeger, Symbolrate fest 1800 Bd; Aufnahme 2026-09-01 auf
      10082 kHz, --mode psk): Traeger 10082,420 kHz (20 Hz bei 10 MHz =
      2 ppm), Symbolrate 1800,09 Bd.
      Zweite Validierung an HFDL Bahrain (Traeger 10031,440 kHz = 10030 kHz +
      1440 Hz; zwei Aufnahmen 2026-09-01 auf 10031 kHz, 15 kHz BB): Traeger
      10031,446 und 10031,425 kHz (6 bzw. 15 Hz Abweichung), Symbolrate
      1799,97 und 1799,99 Bd, Modulationsordnung M=2 (BPSK) in beiden.
- [x] T-905 (2026-09-01) Zoom-Panel: Umschalter AGC/MGC und MGC-Pegelfeld
      (GCON:MODE, GCON:MGC), Endpunkt /api/gain, Rueckspiegelung ueber
      /api/dscan_config. Hardwarevalidierung an HFDL Bahrain 10031 kHz:
      MGC-Aufnahme (Pegel 15 dBuV) zeigt zwei scharf getrennte BPSK-Cluster
      bei konstantem Radius, AGC-Aufnahme radial ausgefranste Wolken mit
      zerklueftetem Phasenhistogramm. Reihenfolge in _apply_gain (erst
      GCON:MGC, dann GCON:MODE MGC) bestaetigt.
- [x] T-906 (2026-09-01) find_histogram_peaks: exakte Bin-Plateaus werden als ein Peak in
      der Laufmitte erkannt (plateau_local_maxima, linear und zyklisch).
      Betrifft FSK- und PSK-Pfad. Selftest T19/T20 gruen.

- [~] T-907 eye_diagram.py PSK-Pfad: Betragsstreuung und Phasenfehler als
      eigene Kennzahlen neben dem EVM. Korrektur der urspruenglichen
      Aufgabenstellung: die dort verlangte Zuordnung zum naechstgelegenen
      Idealpunkt war in compute_evm bereits enthalten, das EVM ist
      rotationsinvariant. Der beobachtete Widerspruch bei HFDL Bahrain
      (MGC 51,17 %, AGC 46,84 % bei optisch besserer MGC-Konstellation)
      erklaert sich anders: EVM gewichtet auf dem Einheitskreis Phasenfehler
      staerker als Betragsfehler, und die MGC-Aufnahme hat konstanten Radius
      bei breiterer Phasenstreuung. Ein Skalar kann beides nicht trennen,
      daher zwei zusaetzliche Kennzahlen. Selftest T21-T23.
      Selftest T21-T23 als Relativvergleich gegen eine mitgemessene Baseline;
      absolute Schwellen sind untauglich, da der Rauschboden bei SNR 20 dB
      selbst mag_cv_pct 14,1-14,7 % und phase_rms_deg 3,09-3,14 Grad erzeugt
      und sich quadratisch mit der gezielten Stoerung addiert (gemessen
      2026-09-02).
- [~] T-908 eye_diagram.py: --json auch im PSK-Modus. Derzeit Hinweis
      "--json wird im PSK-Modus noch nicht unterstuetzt". Wird fuer die
      Ablage der Handbuch-Referenzwerte im Repo gebraucht.
      Selftest T24 (Lesbarkeit, keine numpy-Typen, Rohdatenfelder
      ausgeschlossen).
- [~] T-909 tools/iq_import.py: Konverter fuer Fremd-IQ-Aufnahmen (cs8,
      cs16, cf32, WAV, SigMF) in das MoniScope-IQ-Format. Blockweise
      Verarbeitung mit fortgefuehrter Mischerphase und Filterzustand,
      Frequenzverschiebung (--tune), Tiefpass und Dezimation,
      Amplitudenskalierung mit Clipping-Zaehler, Sidecar mit
      Herkunftsgruppe "source". Oeffnet Signale jenseits der 15-kHz-Grenze
      des EB200-IQ-Wegs (TETRA, breitbandige Verfahren) fuer
      tools/eye_diagram.py. Selftest S1-S6. Validierung an realer
      HackRF/Airspy-Aufnahme offen.

- [ ] T-912 Vermessung des analogen ZF-Ausgangs X5 (IFWB) mit dem HackRF.
      Reine Hardware-/Messaufgabe, keine Codeaenderung. Zu klaeren: (a) die
      tatsaechlich nutzbare flache Bandbreite um 10,7 MHz - Handbuch 2.1 nennt
      +-5 MHz, docs/EB200_Geraeteprofil.md fuehrte bisher +-1 MHz; (b) Pegel am
      X5 und ob ein Daempfungsglied vor den HackRF gehoert; (c) ob AGC den
      Ausgang vorregelt (fuer X6 sagt Annex I genau das) und ob damit fuer
      pegelstabile Aufnahmen MGC noetig ist; (d) Leerlaufspektrum ohne Antenne
      am EB200 als Referenz fuer die Nebenprodukte des HackRF-Mischerpfads
      unterhalb 30 MHz. Randbedingung HackRF: 200 kHz versetzt abstimmen
      (DC-Peak), Abtastrate entsprechend groesser waehlen. Ergebnis als
      docs/X5-IFWB-Vermessung.md plus Nachtrag in docs/EB200_Geraeteprofil.md
      Abschnitt 6; erst danach entscheidet sich die Zoomstufe des
      SDR-Wasserfalls (:8030) zwischen D-SCAN und IFPAN.
- [ ] T-913 Referenzkopplung EB200 X4 -> HackRF CLKIN. Die interne 10-MHz-
      Referenz des EB200 liegt an X4 an, der HackRF hat einen CLKIN-Eingang.
      Bei gemeinsamer Referenz und fester dritter ZF von 10,7 MHz genuegt eine
      einmalige Kalibrierkonstante, damit die Frequenzachse des SDR-Wasserfalls
      dauerhaft zur eingestellten EB200-Frequenz passt. Aufgabe: Konstante
      messen, Ablageort festlegen (Kandidat station.json, maschinenlokal) und
      in sdr_waterfall.py anwenden. Setzt T-912 voraus.
- [x] T-914 (implementiert 2026-09-05, validiert 2026-09-06) audio_spectrum.py: IQ-Pfad.
      stft_db() unterscheidet am dtype zwischen reell (rfft, n_bins = n_fft//2+1) und
      komplex (fft + fftshift, n_bins = n_fft); wav_kind() ersetzt _check_supported()
      und laesst 2 Kanaele / 16 Bit als KIND_IQ zu (Kanal 0 = I, Kanal 1 = Q, T-901).
      Cache-Format steigt auf "eb200-audspec-v2" mit den Feldern kind/f_min_hz/f_max_hz
      (Mittenfrequenzen von Bin 0 und Bin n_bins-1); Bestands-Caches werden dadurch
      automatisch neu gebaut. Selftest T5 auf 3 Kanaele umgestellt (Stereo ist jetzt
      gueltig), neu T8 (IQ-Traeger bei +5 kHz und -3 kHz seitenrichtig, Spiegelprobe
      als Nachweis der I/Q-Vorzeichenkonvention) und T9 (stft_window auf IQ).
      Ohne sichtbare Wirkung im Browser - die Anzeige folgt mit T-915.
- [x] T-915 (implementiert 2026-09-05, validiert 2026-09-06) :8070 zweiseitige
      Frequenzachse und IQ-Spektrogramm. geom/yToFreq/freqToY/drawAxes rechnen mit
      der Spanne fMin..fMax statt mit fMax allein; Band aus dem /api/audspec-Header
      (kind/f_min_hz/f_max_hz, von build_audspec_response durchgereicht), mono
      unveraendert 0..Nyquist. Gedaempfte Nulllinie und Achsenbeschriftung
      "kHz rel." bei zweiseitiger Achse. audioKind() (mono/iq/other) loest
      isUnsupportedAudio() aus T-1211 ab: bei IQ wird das Spektrogramm wieder
      eingeblendet, Wiedergabeleiste und Klassenzeilen bleiben aus (Player: IQ ist
      als Audio sinnlos; Klassifikator: audio_classify bleibt bei mono). Formatzeile
      entsprechend umformuliert. Selftest neu T4-IQ (Header-Felder, n_bins=n_fft,
      Traeger bei +5 kHz oberhalb der Mitte als Vorzeichenprobe) und T-915
      (Textbausteine in HTML_PAGE).
- [x] T-916 (implementiert 2026-09-06, validiert 2026-09-06) Kosmetik :8070:
      Achsenbeschriftungen ("kHz rel." / "kHz", "Zeit [...]") in derselben Farbe wie
      die Tickzahlen. Beide Stellen ueber die Konstante AXIS_TEXT_COLOR statt ueber
      zwei Farbliterale ('#9cf' fuer Ticks, '#888' fuer Einheiten).
- [x] T-917 (implementiert 2026-09-06, validiert 2026-09-06) Empfangsparameter
      und Geraeteprovenienz in den Sidecar. Neues Modul eb200_core/rx_snapshot.py
      (stdlib-only, ohne eigenen SCPI-Zugriff - der Aufrufer reicht seine
      Query-Funktion herein; strikt lesend, kein setzender Befehl). waterfall.py
      liest den Schnappschuss einmalig beim Aufnahmestart; Verstaerkung und Squelch
      kommen aus dem Controller-Zustand statt von der Leitung. audio_wav.py reicht
      die Bloecke "rx" und "device" nur durch. Felder: gain_mode, gain_mgc_dbuv,
      att_state, att_auto, ref_level_dbuv, level_dbuv, afc, squelch_on,
      squelch_thr_dbuv sowie idn/manufacturer/model/serial/firmware/options.
      Nicht ermittelbare Werte sind None, fehlende Bloecke (Bestandsaufnahmen)
      ebenfalls. MSCAN-Aufnahmen bleiben aussen vor (kein SCPI im MscanRecorder).
- [~] T-918 (implementiert 2026-09-06, Browser-Validierung offen) :8070 zeigt die
      Sidecar-Bloecke rx/device als zwei Sammelzeilen "Empfangskette" und "Gerät"
      in der Metadatentabelle. Empfangskette: Verstaerkung (MGC mit Wert bzw. AGC),
      ATT mit Automatikvermerk, Referenzpegel, Pegel beim Start, AFC, Squelch mit
      Schwelle. Geraet: Modell, Seriennummer, Firmware. Drei Faelle unterschieden -
      Block fehlt (Aufnahme vor T-917) -> "n/a (Aufnahme vor T-917)", Einzelwert
      null -> "n/a", Wert vorhanden. Reines Frontend, build_meta() reichte das
      Sidecar bereits durch.
- [ ] T-919 Pegelkalibrierung IQ nach dBµV. Rechnet aus rx-Block und
      Spektrogramm-dB einen absoluten Pegel; erst danach bekommt :8070 den Tooltip
      mit Zeit, Frequenz (relativ und absolut) und Pegel an der Mausposition.
      Zwei Punkte sind VOR der Umsetzung zu entscheiden und zu dokumentieren:
        a) Normierung. Traeger und Rauschflur brauchen unterschiedliche Bezuege
           (Kohaerenzgewinn des Hann-Fensters bzw. aequivalente Rauschbandbreite
           eines Bins). Ohne Festlegung liest derselbe Rauschflur bei n_fft=512
           und 2048 verschiedene Werte. Konvention waehlen und an der Achse
           benennen.
        b) Gueltigkeit. Bei gain_mode="AGC" wandert die Verstaerkung waehrend der
           Aufnahme - ein einzelner Offset ist dann falsch. Solche Aufnahmen
           duerfen keinen absoluten Pegel anzeigen.
      Verifikation gegen die Pegelanzeige des EB200 erforderlich (Hardware).

      Warum die Pegelanzeige zurueckgestellt ist (Entscheidung 2026-09-09):
      Ein Tooltip-Pegel waere heute in einem Teil der Faelle plausibel
      aussehend und falsch - der schlechteste Zustand, schlechter als gar
      keine Zahl, weil man ihr glaubt. Drei Faelle sind zu unterscheiden.
      (a) AGC: NICHT reparierbar. Die Verstaerkung wandert waehrend der
          Aufnahme und zwar signalabhaengig; ein einzelner Offset kann das
          prinzipiell nicht abbilden. Betroffen sind nicht nur absolute
          Werte, sondern auch der Vergleich INNERHALB derselben Aufnahme:
          taucht ein starkes Signal auf, regelt die AGC herunter, und im
          Wasserfall sieht es aus, als sei gleichzeitig der Rauschflur
          gesunken. Diese Scheinbewegung ist dem Bild nicht anzusehen.
      (b) MGC: nicht ungueltig, sondern noch nicht kalibriert. Die
          Verstaerkungskette steht fest und ist seit T-917 im Sidecar
          dokumentiert. Es fehlt allein die Bruecke von den
          PCM-Vollausschlagswerten nach dBuV - dieser Faktor ist nirgends
          dokumentiert und muss gemessen werden.
      (c) Aufnahmen vor T-917: keine Angabe moeglich, "n/a".
      Zeit- und Frequenzachse sind von alldem unberuehrt und schon heute
      exakt. Auch die Farbabstufungen innerhalb EINER MGC-Aufnahme sind
      bereits aussagekraeftig (20 dB ueber Rauschen sind als solche
      erkennbar) - es fehlt nur der Bezugspunkt nach aussen.

      Vorgehensweise zur Kalibrierung (MGC):
      1. Ankerbildung nicht ueber ein einzelnes Bin, sondern ueber die
         GESAMTLEISTUNG des Bandes zum Zeitpunkt des Schnappschusses; diese
         Summe wird dem level_dbuv aus dem rx-Block gleichgesetzt. Damit ist
         keine Annahme ueber den Signalinhalt noetig - es traegt beim
         Einzeltraeger wie beim belegten Kanal.
      2. Linearitaetsnachweis: dieselbe Frequenz bei MGC 20, 30 und 40 dBuV
         aufnehmen. Folgt der gerechnete Pegel der Verstaerkungsaenderung
         linear, traegt der Ansatz; tut er es nicht, ist das nach zehn
         Minuten bekannt statt nach dem halben Ausbau. Diese Messung steht
         VOR jedem Code.
      3. Uebersteuerung bleibt eine Fehlerquelle, die man dem Spektrogramm
         nicht ansieht - Erkennung und Kennzeichnung sind Teil der Aufgabe.

      Konsequenz fuer die Anzeige: Nur Aufnahmen mit gain_mode "MGC" koennen
      je absolute Werte im Spektrogramm tragen. Bei "AGC" bleibt die
      Pegelangabe dauerhaft aus - nicht als Provisorium, sondern als
      Ergebnis. Der Tooltip zeigt in dem Fall Zeit und Frequenz und einen
      Hinweis auf den Grund; die Form des Hinweises ist noch offen.
- [~] T-920 (implementiert 2026-09-09, Browser-Validierung offen) Literale
      \uXXXX-Escapes durch UTF-8-Zeichen ersetzt: analyze_audio.py 6x \u2013 und
      1x \u2026, waterfall.py 1x \u2013 und 1x \u26A0. Ausgabe unveraendert
      (Python loest die Sequenzen beim Parsen ohnehin auf, die Templates sind
      keine Raw-Strings; belegt durch Vergleich aller Stringliterale vor/nach).
      Zweck: die Zeilen taugen wieder als Editieranker - bei T-915 und T-918
      hatten sie je einen Umweg erzwungen, weil das Edit-Werkzeug solche
      Sequenzen beim Schreiben aufloest (CLAUDE.md). Ersetzung auf Byte-Ebene
      per Einmal-Skript, nicht per Edit-Werkzeug. Offen: das Warnzeichen
      U+26A0 in waterfall.py bleibt ein Unicode-Glyph in der Oberflaeche -
      ob es nach der SVG-Regel fuer Bedienglyphen zu ersetzen ist, ist nicht
      Teil dieser Aufgabe.
- [x] T-921 (implementiert 2026-09-14, validiert 2026-09-14) SigMF-Export fuer IQ-Aufnahmen.
      eb200_core/sigmf_export.py erzeugt aus IQ-WAV und Sidecar eine
      SigMF-Metadatei, wahlweise als NCD (nur <name>.sigmf-meta neben der
      WAV, core:dataset + core:header_bytes, kein Byte Nutzdaten kopiert)
      oder spec-konform mit kopierter <name>.sigmf-data. Der Datenversatz
      wird aus dem RIFF gelesen, nicht auf 44 geraten. Alles ohne
      Kern-Entsprechung (Demodulationsart, Bandbreite, AUDIO_REMOTE_MODE,
      rx-Block aus T-917, device-Block) steht unter dem Namensraum
      "moniscope:", angemeldet in core:extensions mit optional=true. Nur
      IQ - Mono-Audio waere als ri16_le darstellbar, aber zweckentfremdet;
      die Pruefung sitzt im Modul, nicht in der Oberflaeche. Die
      Metadatei ist regenerierbar und wird still ueberschrieben (Haltung
      wie .thumb.json). Kommandozeile tools/sigmf_export.py [--data]
      [--out-dir DIR]. Gegenstueck zum vorhandenen SigMF-LESER in
      tools/iq_import.py (read_sigmf_meta). Selbsttest T1-T11.
      Nachtrag T-921a (vor dem Commit behoben): tools/sigmf_export.py fehlte
      der Pfad-Bootstrap 'if __package__ in (None, "")' vor dem
      eb200_core-Import - beim Skriptaufruf steht in sys.path[0] das
      tools-Verzeichnis, nicht das Arbeitsverzeichnis. Muster jetzt wie
      tools/eye_diagram.py.
      Nachtrag T-921c: moniscope:-Felder ohne Wert entfallen, statt als
      null in der Metadatei zu stehen - core:hw wurde schon so behandelt,
      zwei Umgangsweisen mit "kein Wert" in EINER Datei waren nicht zu
      erklaeren. Die Unterscheidung "Feld fehlt" gegen "erhoben, aber
      leer" bleibt dem Sidecar (T-917) vorbehalten. Selbsttest T12.
      Erprobt 2026-09-14 an records/20260901_230708_10031kHz_IQ.wav
      (HFDL Bahrain, 15 kHz BB): Versatz 44 Byte, 6,3 MiB Nutzdaten,
      sample_rate 32000 gegen duration_s 51,545 gegengerechnet - 1 649 440
      komplexe Samples, kein Faktor 2. core:frequency 10031400 (der
      Dateiname rundet auf 10031 kHz). rx und device fehlen, weil die
      Aufnahme aelter ist als T-917. Folge fuer T-919: die Bahrain-
      Aufnahmen vom 2026-09-01 sind fuer eine Pegelkalibrierung dauerhaft
      unbrauchbar, kalibrierbar sind nur Aufnahmen ab 2026-09-06.
      Gegenprobe 2026-09-14 mit sigmf_validate (sigmf-python) an derselben
      Aufnahme: BEIDE Fassungen "Validated all 1 files OK!", Exit 0. Die
      NCD-Fassung wird dabei nicht nur schema-geprueft, sondern als Aufnahme
      geoeffnet - sigmf-python loest core:dataset samt core:header_bytes auf
      und liest die WAV direkt. Damit ist NCD der praxistaugliche Regelfall
      und --data die Weitergabe-Option. Befund: liegt eine .sigmf-data aus
      einem frueheren --data-Lauf neben einer NCD-Metadatei, warnt
      sigmf-python wegen zweier Datenquellen und waehlt core:dataset.
      Behandelt in T-922a.
- [x] T-922 SigMF-Knoepfe in der Aufzeichnungsliste (:8050). Zwei Knoepfe
      in einer neuen Spalte hinter "Loeschen", nur in IQ-Zeilen (isIqRec)
      gefuellt, sonst leere Zelle nach dem Muster von COL_EVAL: "SigMF"
      schreibt die NCD-Metadatei neben die WAV, "SigMF+Daten" zusaetzlich
      die .sigmf-data. Neuer Endpunkt POST /api/sigmfexport in
      manage_jobs.py; Pruefung auf IQ und Schreibort ueber
      eb200_core/path_sandbox.py, das Frontend blendet nur aus. Setzt
      T-921 voraus. Aufgeteilt in T-922a (Serverseite) und T-922b
      (Oberflaeche).
      Erledigt durch T-922a, T-922b und T-922c; validiert 2026-09-15.
- [x] T-922a (implementiert 2026-09-14, validiert 2026-09-15) Serverseite des SigMF-Exports.
      Endpunkt POST /api/sigmfexport in manage_jobs.py, Aufloesung und
      Zulaessigkeit in eb200_core/recording_index.export_recording_sigmf()
      als Pendant zu delete_recording() - gleiche kind/ref-Adressierung,
      gleiche Sandbox, gleiche Fehlerklassen. Laufende Aufnahmen werden
      mit SigmfRunningError -> RecordingLockedError -> HTTP 409
      abgewiesen. Eine liegengebliebene .sigmf-data wird im Ergebnis
      gemeldet, aber nicht geloescht. Zusaetzlich geschlossen: die
      SigMF-Nebendateien wurden beim Loeschen einer Audio-Aufnahme bisher
      nicht mitentfernt. Selbsttests T13/T14 in sigmf_export, Nutzlast-
      Pruefung im Selbsttest von manage_jobs.
      Validiert 2026-09-15 im Browser gemeinsam mit T-922b und T-922c.
- [x] T-922b (implementiert 2026-09-14, validiert 2026-09-15) Oberflaeche des SigMF-Exports in
      :8050. Neue Spalte COL_SIGMF hinter COL_DEL, gefuellt nur in
      IQ-Zeilen (isIqRec), sonst leere Zelle nach dem Muster COL_EVAL.
      Zwei Textknoepfe statt eines mit Auswahl - der Unterschied ist der
      Plattenplatz, und der laesst sich benennen, nicht andeuten.
      Laufende Aufnahmen sind gesperrt (der Server weist sie ohnehin mit
      409 ab). Vor "SigMF+Daten" eine Rueckfrage ab SIGMF_WARN_BYTES
      (500 MiB), gerechnet aus r.bytes - kein zusaetzlicher Weg zum
      Server nur fuer die Groesse. Rueckmeldung als Kurzmeldung mit
      Dateiname und Groesse, plus Hinweis wenn eine fremde .sigmf-data
      danebenliegt (stale_data_name aus T-922a). Spalte in BEIDE
      Spaltensaetze von recColumns() aufgenommen (fuss-Array UND
      ausgeschriebene Liste fuer "Alle") - im Selbsttest abgesichert.
      Nachtrag T-922c (Befund aus der Browser-Erprobung): die Meldung
      nennt jetzt auch den Ablageort (res.dir), im portablen Format von
      path_sandbox.to_portable - innerhalb des Repos relativ, bei einem
      externen storage_root absolut, wie in der Spalte "Ablage".
      Korrigiert die Festlegung aus T-922a, nach aussen nur Dateinamen
      zu geben: bei mehreren Storage-Roots ist "geschrieben" ohne
      "wohin" keine Auskunft.
      Validiert 2026-09-15 im Browser: Knoepfe erscheinen in BEIDEN
      Spaltensaetzen nur in IQ-Zeilen (Art-Chip "Alle" und "IQ"), leere
      Zelle sonst; Meldungen nennen Dateinamen, Groesse und Ablageort;
      der Hinweis auf eine danebenliegende .sigmf-data erscheint beim
      zweiten Lauf und verschwindet nach dem Loeschen der Datei; beide
      Knoepfe sind waehrend einer laufenden Aufnahme gesperrt; kein
      waagerechter Ueberlauf im schmalen Fenster.

## Meilenstein 10: Block-Admin-Überarbeitung → MSCAN-Jobs
- [x] T-1001 (2026-08-25) Drag-Bereichsselektion Wasserfall → Frequenzblock-Verwaltung entfernt
- [x] T-1002 (2026-08-25) eb200_core/mscan_job.py: Job-Definition (6-stellige Zufalls-ID
      ohne 0/O/1/I, created/modified/last_run, Kanal-IDs ≤ 1000 in Auswahlreihenfolge,
      Parameter dwell/hold/squelch_from_memory/min_recording_s=1,0), Datei je Job unter
      jobs/mscan/, atomar mit .bak, Validierung gegen Catalog.by_id(), Snapshot-Auflösung,
      Selftest T1–T14
- [x] T-1003 (2026-08-25) Job-Verwaltung in :8050 (manage_jobs.py): Jobliste sortier- und filterbar
      (ID, Titel, Kanäle, erstellt, geändert, letzter Lauf, Anzahl Läufe), Neu/Bearbeiten
      (Titel, Notiz, Parameter), Kanalwähler nur kind=channel inkl. aufgelöster Raster,
      Filter nach Datei/Band/Tag/Text, Toggle SCAN mit Live-Zähler n/1000, Listenansicht
      der gewählten Kanäle mit Entfernen. Katalog über load_catalog_cached(). Job-Modus
      inventur (ein Zyklus, COUNt 1, Dwell endlich) / ueberwachung (COUNt INF, Laufdauer
      je Lauf).
      Nachtrag (2026-08-28): Detailseite mit Reitern Job/Parameter/Kanäle/Läufe, Speichern-
      Knöpfe oberhalb der Reiter (gelten für den ganzen Job).
      Nachtrag (2026-08-28): Kanalwähler mit Kopf-Checkbox „Alle (sichtbaren)
      markieren" (nutzt Gefilterte-an/aus-Logik, MAX-Kappung bleibt).
- [x] T-1004 (2026-08-26) Speicherplätze programmieren über direktes MEMory:CONTents
      (RX-Umweg nur noch Rückfalloption --via-rx); Hardwaretest 5 Kanäle, MSCAN Start/Stop,
      DWELL/HOLD/Squelch-from-Memory. Löst T-501 für den MSCAN-Fall. Werkzeug
      tools/mscan_probe.py (Phasen A-G, --selftest); Kanalzuordnung über MSCan-Datagramme
      (FREQuency:RX, VOLTage:AC, SQUelch, OPTional) statt SCPI-Polling; Readback über
      MEMory:CONTents?/MEMory:CONTents:MPAR? (Nachtrag 2, 2026-08). Befunde in
      docs/EB200_Geraeteprofil.md, Abschnitt MSCAN.
- [x] T-1005 (2026-08-26) Lauf = job_store-Job vom Typ mscan: params mit mscan_job_id, Kanal-Snapshot
      (resolve_snapshot) und Laufparametern (mode, dwell, hold, squelch_from_memory,
      min_recording_s). Runner im :8000-Prozess (blockiert D-SCAN/IFPAN wie Longruns, Banner
      in beiden Ansichten): MEMory:CLEar -> MEMory:CONTents je Platz (ASCII, ACT=1) -> Readback
      MPAR? -> MSCan:CHANnel MEM999 + COUNt/DWELl/HOLD/CONTrol "STOP:SIGN" + OUTP:SQU:CONT MEM
      -> UDP-Pfade MSC (FREQ/LEVEL/SQU/OPT) und AUD (Modus 6, eigener Port) -> INIT:CONM.
      Segmentierung: Start = MScan-Item (Frequenz -> Kanal-Snapshot), Ende = 28-Byte-Audio-
      Marker, Rueckfall Luecke > 0,5 s; WAV je Segment via audio_wav; Segmente < min_recording_s
      verwerfen. Inventur endet am Zyklusende-Marker, Ueberwachung an end_iso/Stopp.
      Ergebnis-Index im Manifest: results.channels[] -> recordings[]. set_last_run.
      Session A [x] (2026-08-26): eb200_core/mscan_run.py (Befehle, Parser, Segmenter,
      Recorder, Selftest S1-S8). Session B [x] (2026-08-26) Code: Controller-Modus mscan
      (_run_mscan_loop), Scheduler/Stop, Banner, Start-Dialog + Laufliste in :8050,
      job_store.heartbeat(results=). Hardware-Lauf [x] (2026-08-27: Überwachung 5 min 7 Segmente/7 WAVs,
      Stopp-Pfad, Inventur endet am Zyklusende-Marker; Nachtrag 5 löste die Antwortverschiebung).
- [~] T-1006 (implementiert, Browser-Validierung offen) Ergebnisansicht in :8050: Job → Läufe → aktive Kanäle (≥ 1 Aufnahme ≥ 1 s) →
      Aufnahmen mit Player; alle Listen sortierbar, Filter Mindestdauer. Seite /mscan/run?id=<Lauf>:
      Kopfdaten, EIN Audio-Player mit Range-Requests (Spulen), Tabellen Aktive Kanäle/Aufnahmen/
      Stille Kanäle. WAV-Endpunkt /api/mscan/wav mit Whitelist (nur Dateien aus dem Manifest) +
      storage_roots-Sandbox, nie eine auf der Platte vorhandene, aber nicht gelistete Datei.
      Laufliste (HTML_MSCAN_DETAIL): Start verlinkt auf /mscan/run, Ende zeigt das reale Ende
      (ended_iso) statt des geplanten, neue Spalte Ordner, „verwaist“ zeigt audio_orphan_mid mit
      Vorlauf/dazwischen/Nachlauf im Tooltip.
      Nachtrag 1 (2026-08-28): Kopf-Labelbreite, Filtergruppe „Auswahl der Aufnahmen", Reiter Aufnahmen/Stille Kanäle; Reiter-Stile nach COMMON_CSS
      (kontrastreicher Aktivzustand, gilt auch für Startseite T-1101/T-1103).
      → abgelöst durch T-1106 (eigener Auswerte-Server analyze_mscan.py :8060); Run-Detailseite in
      :8050 bleibt bis dahin Übergangslösung.
      Nachtrag 2 (2026-08-28): Ergebnisseite mit Reitern Aktive Kanäle | Stille
      Kanäle | Aufnahmen | Auswertung; Stille Kanäle markier- und aus der Definition entfernbar
      (/api/mscan/channels remove, Lauf-Snapshot bleibt); Auswertung je Kanal (Aufnahmen, Gesamtzeit,
      max/min dBµV, Mindestdauer-Filter wirkt).
- [~] T-1007 Nachklassifikation der WAV-Aufnahmen: klasse {LEER, RAUSCHEN, CW, DIGITAL,
      SPRACHE, UNKLAR} + voice_score 0..1 (Design E1-E10 abgestimmt 2026-08-30: numpy-Modul
      im Repo-Root auf audio_spectrum.stft_db(), Merkmale M1-M6, Kaskade billig-vor-teuer,
      Sidecar <wav>.klasse.json regenerierbar, Auslösung per CLI/:8060, nie im Laufprozess;
      kein MUSIK). Der frühere Wortlaut "stdlib-Heuristik, Filter in T-1006" ist überholt.
      - [x] T-1007a (2026-08-30) audio_classify.py + Selftest an synthetischen Signalen
      - [~] T-1007b (implementiert 2026-08-30, Browser-Validierung offen) :8070: Badge Klasse/
            voice_score/sicherheit, Merkmale aufklappbar, Auto-Klassifikation beim Oeffnen (deterministisch, Version-Bump invalidiert; kein manueller Button) (POST
            /api/classify, synchron), Select Klasse manuell (POST /api/klasse ->
            audio_klassen.json, LabelStore). recording_index: .klasse.json und
            Klassen-Eintrag beim Löschen mit entfernt.
      - [x] T-1007c (2026-08-30, vor T-1007b gezogen - Kalibriermaterial lag vor)
            Klassifikator v3: belegtes Band (M1/M6), Merkmale peak_energy_ratio/keying_rate,
            CW über dominante Linie + Tastrate, DIGITAL über Flachheit ≤ 0,005, zweiter
            schwacher Sprachweg, Fluss aus den Regeln entfernt, fsk_corr (FSK-Paar-
            Antikorrelation), Tastrate aus Pausenmaske; tools/classify_eval.py (Ganzdatei +
            Chunk-Modus 3/5 s, Konfusionsmatrix, Perzentile je Label); Handlabels
            audio_klassen.json (11 IFPAN-Direktaufnahmen: 2 CW, 3 DIGITAL, 3 SPRACHE,
            3 RAUSCHEN; 2 Dateien mit gemischtem Material entfernt). Ergebnis: 3-s-Chunks
            GESAMT 98,6 % (CW/RAUSCHEN/SPRACHE je 100 %, DIGITAL 93,3 %), 5-s-Chunks
            GESAMT 100 %, UNKLAR-Quote 0 % in beiden Fällen.
      - [ ] T-1007d :8060 Reiter Aufnahmen: Spalte Klasse + Score, Filter-Chips,
            Button "Klassifizieren" (Hintergrund-Batch, ein Worker, Fortschritt)
      - [ ] T-1007e Klasse MUSIK evaluieren (derzeit NICHT noetig, nur vorgemerkt).
            Befund 2026-08-31: ein FM-Rundfunkmitschnitt mit klassischer Musik wird als
            SPRACHE/schwach eingestuft (syllable_ratio 0.43, knapp ueber der Schwelle) -
            erwartbar, da Musik durch CW und DIGITAL faellt und bei SPRACHE mittlere
            Silbenwerte erreicht. Eine siebte Klasse braucht eine eigene Stufe VOR
            SPRACHE und damit neue Kalibrierung (Handlabels Musik, classify_eval,
            CLASSIFIER_VERSION-Bump); Risiko: echte Sprache wird mit abgefangen, die
            heutigen 98,6 % (3 s) / 100 % (5 s) duerfen nicht abrutschen. Lohnt erst,
            wenn Rundfunkmitschnitte systematisch ausgewertet werden.
      Kein Aufnahme-Gate: es wird immer aufgenommen, klassifiziert wird nachträglich.
- [~] T-1008 Abbau Frequenzblock-Tabelle (db.py, Block-Formular in block_admin.py),
      Doku-Nachtrag WS1 3a/4, WS3 3a (Rückkanal), Kommentar in start_eb200.py
      UI-Teil erledigt 2026-08-28 (Blockliste/-Formulare, Routen /new /edit /save /delete,
      db-Import in block_admin entfernt); offen: db.py, tools/sync_known_block.py, Doku WS1 3a/4 + WS3 3a,
      Kommentar start_eb200.py.
- [ ] T-1009 Kanalwähler: Hinweis bei Nachbarkanal < 10 kHz bereits im Job (25-kHz- vs
      8,33-kHz-Schreibweise desselben Kanals)
- [ ] T-1011 T-703-Polling (eigene SCPI-Verbindung) während eines MSCAN-Laufs pausieren – prüfen, ob es die
      Antwortverschiebung (Nachtrag 5) begünstigt hat
- [ ] T-1012 Programmier-Tempo MEMory:CONTents (~250 ms/Platz, 1000 Plätze ~4 min) – *OPC?-Takt prüfen
- [x] T-1013 (2026-08-31, Betriebsvalidierung: Fehlerzaehler im Statusboard
      bleibt bei harten Browser-Reloads auf 0) Alle acht Server:
      Browser-Verbindungsabbrüche (WinError 10053/10054, BrokenPipe) in einer
      gemeinsamen ThreadingHTTPServer-Basisklasse (handle_error) still
      verwerfen – sie treten auch beim Lesen der Request-Zeile auf
      (Reserve-Sockets von Chromium), ein try im Handler reicht nicht;
      Fehlerzähler im Statusboard sonst wertlos.
      eb200_core/http_base.py (neu, stdlib-only): QuietThreadingHTTPServer
      (http.server.ThreadingHTTPServer, daemon_threads/allow_reuse_address wie
      bisher) ueberschreibt handle_error() statt try/except in den Handlern -
      ein Abbruch kann schon beim Lesen der Request-Zeile passieren (vor jedem
      Handler-Code), handle_error() ist die einzige Stelle, die alle Faelle
      abdeckt. _is_client_abort() erkennt BrokenPipeError/ConnectionResetError/
      ConnectionAbortedError sowie OSError mit winerror 10053/10054 bzw. errno
      EPIPE/ECONNRESET/ECONNABORTED und zaehlt sie in dropped_connections
      (Lock-geschuetzt, rein informativ); alles andere (Programmfehler) geht
      unveraendert an die Standardbehandlung (Traceback auf stderr) - der
      Fehlerzaehler im Statusboard (start_eb200.py) soll nur echte Fehler
      zaehlen. Selbsttest T1-T3 gruen (T2: harter RST-Abbruch per SO_LINGER(0)
      bleibt still, naechste Anfrage 200; T3: absichtlicher ValueError erzeugt
      weiterhin einen Traceback, Server laeuft weiter).
      Alle acht Server (waterfall.py, analyze_sessions.py, analyze_spectrum.py,
      manage_jobs.py, analyze_mscan.py, analyze_audio.py, sdr_waterfall.py,
      eb200_core/block_admin.py): die wortgleiche lokale
      "class ThreadingHTTPServer(http.server.ThreadingHTTPServer): ..."
      ersetzt durch "from eb200_core.http_base import QuietThreadingHTTPServer
      as ThreadingHTTPServer" an derselben Stelle (httpd = ThreadingHTTPServer(...)
      bleibt unveraendert); block_admin.py (cwd eb200_core/, importiert
      Geschwistermodule flach wie "from param_store import ...") verwendet
      denselben flachen Stil ("from http_base import ..."). "import http.server"
      bleibt in allen acht Dateien noetig (Handler erbt weiterhin von
      http.server.BaseHTTPRequestHandler). Bestehende
      except-(BrokenPipe...)-Bloecke in den Handlern (Senden im Handler,
      bevor es handle_error erreicht) unveraendert gelassen.
      manage_jobs.py: _send_file() entfernt (0 Aufrufer ausser der eigenen
      Definition - historisch fuer die MSCAN-Ergebnisseite, seit T-1106 in
      analyze_mscan.py; toter Code).
      Gates gruen (ast.parse auf allen neun Dateien, http_base --selftest
      T1-T3, Selftests analyze_audio/analyze_mscan/manage_jobs/start_eb200/
      tools/selftest_known_edit.py [block_admin], Import-Check
      analyze_sessions/analyze_spectrum/waterfall/sdr_waterfall [sdr_waterfall
      liess sich in dieser Umgebung ohne SDR-Hardware importieren]). KEIN
      Serverstart gegen das EB200.
- [ ] T-1014 MSCAN-Audioverlust: Segmentgrenzen folgen dem Ton statt den
      Steuerereignissen. Befund 2026-09-08 (Messreihe ueber 19 Laeufe, Belege in
      docs/EB200_Geraeteprofil.md Abschnitt "Audio-Bilanz aus Produktionslaeufen"):
      je nach Lauf 15-91 % des empfangenen Tons landen in keiner behaltenen WAV.
      Zwei Ursachen, beide im Segmenter (eb200_core/mscan_run.py), KEINE
      Geraetestoerung - Squelch/Demodulation/WAV-Format unauffaellig, Datei seit
      T-1006 unveraendert:
      (A) Ton in der Hold-Zeit ohne begleitendes MScan-Item. Ein Item entsteht nur
          bei der Squelch-Oeffnung; oeffnet der Squelch in einer Sprechpause erneut,
          fliesst Ton ohne Item und wird als audio_orphan_mid verworfen. Gemessen
          2,0-2,3 s je gescheiterter Oeffnung bei hold_s 2,0, exakt 0,0 s bei
          hold_s 0.
      (B) Ein fremdes Item oder das Zyklusende schliesst das laufende Segment; beim
          naechsten Rundlauf beginnt eine NEUE Datei. Eine Aussendung zerfaellt in
          Schnipsel von je einem Datagramm (~30 ms), die einzeln an min_recording_s
          scheitern und geloescht werden - im Manifest nur als discarded_short
          sichtbar, NICHT als orphan. Beleg: Median-Abstand aufeinanderfolgender
          Aufnahmen desselben Kanals 0,87 s = eine Zyklusdauer (Lauf
          20260908_200359: 1126 Segmente, 75 Aufnahmen).
      hold_s 0 ist KEIN Workaround: der Verlustanteil sinkt, die empfangene
      Tonmenge aber ebenso (396 s -> 155 s bei sonst gleichem Job und gleicher
      Laufzeit).
      - [x] T-1014a (2026-09-09 hardware-validiert, Commit 15be25b) Segmenter:
            Audio ohne offenes Segment setzt das Segment des ZULETZT gemeldeten
            Kanals fort, solange das Fortsetzungsfenster laeuft
            (RESUME_WINDOW_S = max(2.0, hold_s + 1.0)). Ein Item auf DEMSELBEN
            Kanal innerhalb des Fensters schreibt in dieselbe WAV weiter statt eine
            neue anzulegen. Marker, Zyklusende und fremdes Item schliessen nur noch
            vorlaeufig; endgueltig schliesst die Lueckenregel (gap_s) oder das
            Fensterende. Praezisierung bei der Umsetzung: die Lueckenregel (gap_s
            0,5 s) schliesst NICHT endgueltig - sie ist kuerzer als das
            Fortsetzungsfenster, ein endgueltiger Schluss wuerde die
            Hold-Zeit-Fortsetzung verhindern. Endgueltig schliesst allein das
            Fensterende (bzw. Laufende und Inventur-Zyklusende).
            min_recording_s wirkt erst auf das zusammengesetzte
            Segment. Selbsttests S9-S12: Hold-Fortsetzung, Zyklus-Fortsetzung,
            Fensterende, Kanalwechsel innerhalb des Fensters.
      ERGEBNIS T-1014a (Abnahme 2026-09-09 09:24, Lauf longrun_20260909_092434:
      Job SCJVXF, 61 Kanaele, hold_s 2,0, min_recording_s 1,0, 20 min):
      Tonverlust 46,9 % -> 2,0 %; verwaist 106,3 s -> 0,0 s; in verworfenen
      Kurzsegmenten 29,8 s -> 2,7 s; mittlere Aufnahmedauer 2,54 s -> 3,61 s.
      (Korrektur 2026-09-09: die zuerst notierten 61,1 % beruhten auf einer
      falschen Bilanz - stats.bytes zaehlt ALLES am Empfaenger Eingetroffene,
      auch verwaiste Datagramme, weil MscanJobRecorder.record_audio() den
      Zaehler unbedingt erhoeht. Die verwaisten Sekunden waren dadurch doppelt
      erfasst. Richtige Rechnung ohne jede Annahme ueber die Datagrammdauer:
      Verlust = 1 - behaltene Bytes / stats.bytes.)
      Vergleichslauf longrun_20260908_191653 (gleicher Job, gleiche Parameter,
      vor der Aenderung). Der Restverlust von 2,0 % entspricht dem Niveau der
      stoerungsfreien 6-Kanal-Laeufe vom 27.08. (1,4-4,0 %) und besteht aus
      kurzen Squelch-Zuckern ohne Aussendung - kein Systemfehler.

      WAS VERBESSERT WURDE (Kurzfassung, damit der Befund nicht verlorengeht):
      Vorher galt im Segmenter die Annahme "kein MScan-Item, kein Ton". Sie ist
      falsch. Ein Item entsteht NUR bei der Squelch-Oeffnung, nicht bei jeder
      Tonphase. Daraus zwei Verlustwege, die zusammen bis zu 91 % des
      empfangenen Tons vernichteten:
      (A) Der 28-Byte-Marker folgt ~Hold-Zeit nach dem letzten Nutzdaten-
          Datagramm und trifft damit auch Sprechpausen MITTEN in einer
          Aussendung. Das Segment wurde geschlossen, der weiterfliessende Ton
          hatte kein Segment mehr und wurde als audio_orphan_mid verworfen -
          gemessen exakt hold_s je gescheiterter Oeffnung.
      (B) Zyklusende oder ein Item auf einem anderen Kanal schlossen das
          Segment ebenfalls endgueltig. Beim naechsten Rundlauf (0,7-1,2 s bei
          61 Kanaelen) begann eine NEUE Datei. Eine Aussendung zerfiel in
          Schnipsel von je einem Datagramm, die einzeln an min_recording_s
          scheiterten und geloescht wurden - im Manifest nur als
          discarded_short sichtbar, nicht als orphan, deshalb jahrelang
          unbemerkt.
      Die Loesung dreht das Prinzip um: Segmentgrenzen folgen dem TON, nicht
      den Steuerereignissen. Marker, Zyklusende, fremdes Item und Lueckenregel
      merken ein Segment nur noch VOR - die Datei bleibt offen. Audio ohne
      Item setzt das zuletzt vorgemerkte Segment fort, ein Item auf demselben
      Kanal ebenso; beides schreibt in DIESELBE Datei. Endgueltig schliesst
      allein das Fortsetzungsfenster max(2,0 s, hold_s + 1,0 s), und erst dann
      entscheidet min_recording_s ueber das zusammengesetzte Ganze.
      Nebenwirkung: duration_s ist jetzt die Wanduhr-Spanne ueber alle Teile
      und enthaelt die stillen Luecken. Als Mass fuer die TONMENGE taugt es
      damit nicht mehr - dafuer bytes/32000 verwenden (betrifft jede
      Auswertung, die aus duration_s auf Sekunden Ton schliesst).
      Sackgassen, die nicht noch einmal begangen werden muessen: hold_s 0 als
      Workaround (senkt den Verlustanteil, aber auch die empfangene Tonmenge -
      396 s gegen 155 s bei sonst gleichem Job), min_recording_s senken (die
      gescheiterten Segmente sind LEER, nicht kurz), Verdacht auf Demodulation,
      Squelch-Schwelle, WAV-Format oder Codeaenderung an mscan_run.py (alle
      geprueft und ausgeschlossen).
      Merkposten Bedienung: "Toene statt Sprache" in den Aufnahmen kommt von
      der TONE-Ausgabe am Geraet, nicht von der Software.

      - [x] T-1014b (2026-09-09, Commit 9ff8a77) Sichtbarkeit: Zaehler audio_resumed und
            audio_lost_short_s (Ton in verworfenen Segmenten, Sekunden) in
            current_stats und Manifest; Tonbilanz (empfangen / behalten / verwaist /
            in Kurzsegmenten) in der Laufliste :8050 und im Kopf :8060.
            tools/mscan_audio_bilanz.py aus der Diagnosereihe uebernehmen - Messlatte
            fuer den Vorher-Nachher-Vergleich.
      - [~] T-1014c Fortsetzung nur bei nachweisbarer Kanaltreue. Befund
            2026-09-09 am Abnahmelauf longrun_20260909_092434: 4 von 37
            Aufnahmen zusammengesetzt, davon eine (093035_021, 2,78 s Ton in
            7,42 s Spanne) nach dem ersten Block nur noch abgehacktes Knattern
            - aneinandergereihte Squelch-Einschaltknalle FREMDER Kanaele.
            Die Annahme aus T-1014a ("Ton kann nur vom zuletzt gemeldeten
            Kanal kommen") gilt nur, solange der Empfaenger dort steht. MSC-
            und AUD-Pfad sind getrennte UDP-Stroeme mit eigener Latenz; nach
            Zyklusende trifft der Knall des naechsten Kanals VOR dessen Item
            ein und wird an die alte Datei geklebt. Loesung: vorgemerkte
            Segmente tragen ein Kennzeichen "geparkt", gesetzt beim Vormerken,
            aufgehoben bei Zyklusende und bei einem Item auf einem anderen
            Kanal. Audio ohne Item setzt nur geparkte Segmente fort; die
            item-basierte Fortsetzung bleibt unberuehrt (durch die genannte
            Frequenz beweisbar sicher). Selbsttests S13/S14.
            Folge: der gemessene Verlust steigt wieder etwas, weil nicht mehr
            fortgesetzter Ton als verwaist zaehlt - das ist ehrlicher als eine
            unverstaendliche Datei.
            Abnahme: derselbe Job 20 min; Anteil zusammengesetzter Aufnahmen
            mit grosser Luecke geht gegen 0, Stichprobe hoerbar durchgehend.
      - [~] T-1018 Lueckenschwelle an die Hold-Zeit koppeln. Befund 2026-09-09
            (longrun_20260909_184414): die sieben zusammengesetzten Aufnahmen
            haben Luecken von 0,69 bis 1,94 s - keine unter 0,5 s, keine bei
            2,0 s, also exakt das Fenster zwischen DEFAULT_GAP_S und hold_s.
            Die Lueckenregel in tick() war ein Rueckfall fuer ausbleibende
            28-Byte-Marker, feuerte aber bei jeder Sprechpause, obwohl der
            Empfaenger waehrend der Hold-Zeit auf dem Kanal steht und der
            Squelch nicht abfaellt. Wirksame Schwelle jetzt
            max(gap_s, hold_s + GAP_EXTRA_S); gap_s bleibt Rueckfall-Basis fuer
            hold_s = 0. Folge: Aufnahmen bleiben durchgehend offen statt
            geschlossen und per T-1014a wieder zusammengesetzt zu werden -
            weniger Mechanik bei gleichem Ergebnis. Selbsttest S15.
            Abnahme: derselbe Job 20 min; Anteil zusammengesetzter Aufnahmen
            (audio_test7) sinkt deutlich, Verlust (audio_test6) bleibt im
            einstelligen Bereich.
      - [~] T-1017 Squelch-Einschaltknall verwerfen. Befund 2026-09-09, vier
            Aufnahmen im Spektrogramm vermessen: jede beginnt mit Stille
            (31-128 ms, stark schwankend), dann ein breitbandiger Knall von
            konstant ~65 ms, erst dann Sprache. Der Knall entsteht bei JEDER
            Squelch-Oeffnung, also auch nach einer Sprechpause mitten in der
            Aufnahme; bei Fehloeffnungen besteht die Datei fast nur daraus
            (185353_065: 0,38 s Ton). Zweistufig in on_audio(): stille
            Datagramme verwerfen (Spitzenwert < LEAD_IN_SILENCE_PEAK), dann ab
            dem ersten lauten Datagramm PLOP_SKIP_BYTES byte-genau. Neu
            scharfgeschaltet nach jeder Stille > PLOP_GAP_S - Ausloeser ist die
            Stille, nicht die Fortsetzung, weil Segmente seit T-1018 bei
            Sprechpausen offen bleiben. Schnitt im Segmenter, nicht in der
            WAV-Ausgabe: dort zaehlt seg["bytes"] mit, sonst waere die
            Tonbilanz um den Vorlauf je Aufnahme zu optimistisch.
            Selbsttest S16. Restrisiko: beginnt eine Aussendung ausnahmsweise
            ohne Knall, fehlen die ersten 70 ms - nach den vier Messungen ist
            der Knall immer da, sicher ist das erst nach dem naechsten Lauf.
      Abnahme: derselbe Job SCJVXF (61 Kanaele, hold_s 2,0, min_recording_s 1,0),
      20 min -> Verlust unter 10 % (Ausgangswert 62,9 %, Lauf 20260908_191653).
- [x] T-1015 (2026-09-01, Commit 204d113) Marker-Import in den Frequenzkatalog (:8010,
      block_admin.py): neue Routen GET /known/marker-import (Maske) und
      POST /known/marker-import/save (Schreiben, EIN Formular mit
      name="action" value="preview"/"save" - Muster _raster_form_body/
      _handle_known_raster_save, da nur diese zwei Routen im Prompt genannt
      waren). Eingabe ist die Tabelle der Auto-Markerliste (Zeit/MHz/dBuV,
      Tab- oder Semikolon-getrennt, Kopfzeile optional, Dezimalpunkt oder
      -komma) per Textfeld; die GET-Maske akzeptiert zusaetzlich ?data= zur
      Vorbelegung (Empfangsweg fuer ein verstecktes Feld/Link aus dem
      Wasserfall - T-506 selbst existiert noch nicht in diesem Plan und
      wurde NICHT angelegt, waterfall.py blieb unangetastet).
      Abweichung/Interpretation (Punkt 1, wie von CLAUDE.md gefordert):
      "Vorbelegung Demod/Bandbreite/Squelch aus Catalog.bands_in_range der
      ersten Frequenz" laesst sich nicht direkt aus einem Band lesen -
      bands-Eintraege tragen kein demod/bw_hz/squelch_dbuv (siehe
      known_freq._load_bands, nur id/title/start_hz/stop_hz/segments/file/
      service/layer). Aufgeloest als: das ERSTE ueber bands_in_range
      getroffene Band liefert seine Datei (band['file']); deren defaults
      sind die Vorbelegung. Eindeutig genug fuer eine reine Formular-
      Vorbelegung (jederzeit im Formular aenderbar) - dokumentiert statt
      abgebrochen.
      eb200_core/block_admin.py: parse_marker_table (Parser, s. o.),
      marker_channel_id/-label (MK-<MHz 4 Dez.>/"MK <MHz> MHz" ueber die
      vorhandene _mhz()-Formatierung - wiederholter Import derselben
      Frequenz ergibt dieselbe id, KEIN Raster-Einrasten), _marker_notes
      (Erkennungszeit + Pegel als Herkunftsnachweis), build_marker_channel_
      entry (kind:'channel', tags=['marker'], demod/bw_hz/squelch_dbuv
      bewusst NICHT gesetzt - Kanaele erben die defaults), marker_duplicate_
      of (Catalog.channels_in_range mit Toleranz +-bw_hz/2 gegen ALLE
      Katalogdateien; bw_hz<=0 vor Formularwahl prueft nur exakte Treffer),
      _marker_prefill_defaults (siehe Abweichung oben). Formular: Zieldatei
      bestehend (Dropdown) oder neu (Vorschlag marker_<JJJJ-MM-TT>.json,
      meta.service "Marker" nur bei echter Neuanlage - eine schon
      vorhandene Datei unter dem Vorschlagsnamen wird abgelehnt statt
      stillschweigend ueberschrieben); Vorschautabelle mit editierbarer
      Frequenz je Zeile (freq_i-Textfeld) und Auswahl-Checkbox (sel_i,
      Duplikate zu Beginn abgewaehlt, Haekchen erzwingt den Import
      trotzdem); "Vorschau" parst die Rohtabelle IMMER frisch (Frequenz-/
      Auswahl-Aenderungen gelten daher nur bis zum naechsten Vorschau-Klick,
      im Formular vermerkt), "Importieren" baut die Zeilen aus den
      row_i-Feldern (die editierte Frequenz ist damit massgeblich).
      Wiederholter Import derselben Frequenz in dieselbe Datei aktualisiert
      den bestehenden Eintrag (id-Treffer, _find_channel_entry) statt eine
      zweite gleiche id zu erzeugen. Schreiben ueber validate_candidate +
      das vorhandene _write_known_file (.bak, atomar, Zeilenende der
      Datei), Post/Redirect/Get auf /?open=<Datei>.
      Selbsttest _self_test_marker_import() (--selftest, ohne Browser):
      Parser (Tab+Kopfzeile, Semikolon ohne Kopfzeile, Dezimalkomma, kaputte
      Zeile wird gemeldet und uebersprungen, leere dBuV-Spalte), ID-/Label-
      Bildung, Duplikaterkennung (synthetischer Katalog in einem Temp-
      Verzeichnis: Treffer innerhalb Toleranz, keiner ausserhalb, exakte
      Pruefung bei bw_hz<=0), Eintragsbau (inkl. Fall ohne Pegel). Zusaetzlich
      per HTTP-Smoke-Test (Handler auf Zufallsport gegen ein Temp-
      Katalogverzeichnis, kein Bezug zu known_freq/) verifiziert: GET-Maske,
      Vorschau inkl. Vorbelegung + Duplikathinweis, Import in eine neue
      Datei, wiederholter Import derselben Frequenz aktualisiert statt
      dupliziert.
      Gates gruen (ast.parse, Steuerzeichen-/Byte-Pruefung der neuen dBµV-
      Stellen gegen die bestehende Schreibweise, python -c "import
      block_admin", block_admin.py --selftest, HTTP-Smoke-Test). KEIN
      Serverstart gegen das EB200 (Werkzeug ist reine Katalogpflege ohne
      Geraetebezug). waterfall.py (T-506, Sende-Seite des versteckten
      Feldes) NICHT angefasst - ausserhalb des Prompts, als offene
      Folgeaufgabe zu sehen.
      ACHTUNG: Das oben beschriebene Auswahlverhalten (Duplikate zu Beginn
      abgewaehlt, Haken = Katalogimport) wurde durch T-1016 umgekehrt - der
      Haken bedeutet seither "in den MSCAN-Job uebernehmen".
- [x] T-1016a (2026-09-01, Commit e4a571c) Kanaluebernahme in einen MSCAN-Job
      (manage_jobs.py, :8050): neue Seite HTML_MSCAN_IMPORT mit den Routen
      GET /mscan/import?ids=a,b,c (Deep-Link, auch fuer Tests ohne :8010) und
      POST /mscan/import (Formularfeld ids, Aufruf von :8010). Die Seite loest die
      IDs ueber load_catalog_cached auf, zeigt Label und Frequenz, meldet unbekannte
      IDs in einem eigenen Kasten und bietet: neuer Job (Titel) oder bestehender Job
      aus /api/mscan/jobs. Uebernommen wird ueber die vorhandenen JSON-APIs
      /api/mscan/create und /api/mscan/channels, danach Sprung auf /mscan/job?id=.
      IDs werden serverseitig gegen ^[A-Za-z0-9._-]+$ geprueft und auf MAX_CHANNELS
      gekappt. Neue Handler-Methode _read_form_field (urlencodierter POST-Koerper,
      Muster _read_json_body) - noetig, weil die Seite per Formular-POST ueber die
      Portgrenze aufgerufen wird (kein CORS, POST statt Query wegen der
      URL-Laengengrenze).
      Erkenntnis: Platzhalter INNERHALB eines JS-Blocks brauchen einen gueltigen
      Vorgabewert (const IMPORT_ITEMS = [] /*IMPORT_ITEMS*/;), weil
      tools/check_js_blocks.py die Rohvorlage VOR jeder Ersetzung prueft - anders als
      /*COMMON_JS*/, das alleiniger Inhalt eines eigenen <script>-Elements ist.
      Nebenwirkung: faellt die Ersetzung aus, bleibt eine leere Liste stehen statt
      kaputtem JavaScript.
- [x] T-1016b (2026-09-01, Commit 2f80908) Marker-Import als Kanalauswahl statt
      Katalogpflege (eb200_core/block_admin.py): Der Haken je Zeile bedeutet "in den
      MSCAN-Job uebernehmen". Eine Zeile mit Duplikattreffer liefert die BESTEHENDE
      Kanal-ID und erzeugt KEINEN Katalogeintrag - damit entstehen keine Dubletten;
      nur Neufunde werden in die Zieldatei geschrieben. Sind alle Zeilen Treffer,
      wird gar keine Datei angelegt und Zieldatei/Demod/Squelch bleiben ungenutzt.
      Zweithaken neu_i je Trefferzeile ("trotzdem neu anlegen", Standard aus) fuer
      den Fall, dass der Treffer nur ein FNV-Zuteilungseintrag ohne brauchbare
      Kanalbezeichnung ist. Drei Aktionen: "Vorschau", "In Job uebernehmen" (neu),
      "Nur in Katalog" (Verhalten aus T-1015, value="save" unveraendert - ID-Stabilitaet).
      Uebergabe an :8050 ueber render_marker_handoff_page: verstecktes Formular mit
      ids, MARKER_HANDOFF_JS setzt die action zur Laufzeit aus location.hostname und
      sendet ab; der Knopf bleibt im Markup, damit die Seite ohne aktives JavaScript
      per Klick bedienbar bleibt.
      WICHTIG: Die Bandbreite ist fuer BEIDE Aktionen Pflicht, weil sie die Toleranz
      der Duplikatpruefung (+-bw_hz/2) bestimmt - bei leerem Feld waere die Toleranz 0,
      es wuerde kein Duplikat erkannt und jede Zeile landete als neuer Kanal im
      Katalog. Demod, Squelch und Zieldatei werden dagegen nur geprueft, wenn
      ueberhaupt Neufunde vorliegen.
- [x] T-1016c (2026-09-01, Commit 6c35dcf) Vorschautabelle des Marker-Imports
      kompakter: Spalte "Zeit" entfaellt (das versteckte Feld time_i bleibt - die
      Erkennungszeit geht weiterhin als Herkunftsnachweis in die Notizen des neu
      angelegten Kanals), dBµV rechtsbuendig (Klasse num), Trefferanzeige einzeilig
      in der Form "vorhanden: <id> (<MHz> MHz) [<Datei>]" mit dem Dateinamen in
      Akzentfarbe (Klasse dup-file), colgroup mit festen Spaltenbreiten,
      table.marker-table auf 60em begrenzt.
- [x] T-1019a (implementiert 2026-09-11, validiert 2026-09-11)
      MSCAN-Pegel-Inventur, Kern. eb200_core/mscan_job.py: neue Parameter
      stop_after (cycle|runtime, STOP_AFTER), record_audio und
      level_active_dbuv, dazu PEGEL_INVENTUR_DEFAULTS als Vorschlagswerte
      für den schnellen Rundlauf (Dwell 0,2 s, Hold 0) und Selbsttest T16.
      Der Vergleich dwell_s >= min_recording_s entfällt bei record_audio
      False. eb200_core/mscan_run.py: COUNt 1 nur noch bei inventur +
      stop_after cycle (sonst INF), Auf-/Rückbau ohne Audio-Fernmodus und
      AUD-UdpPath bei record_audio False, MscanSegmenter endet nur bei
      stop_after cycle; neuer Teil E2 mit MscanLevelInventory (zählt
      Squelch-Öffnungen je Kanal oberhalb level_active_dbuv),
      MscanLevelRecorder (gleiche äußere Schnittstelle wie
      MscanJobRecorder, schreibt keine Dateien) und make_recorder() als
      einziger Auswahlpunkt; Selbsttests S17/S18. Ergebnis im Manifest mit
      kind == "level": ALLE Kanäle mit active/hits/cycles_active/per_cycle
      ([Durchgang, Treffer]-Paare, gekappt bei MAX_PER_CYCLE),
      level_max_dbuv, below_threshold — stille Kanäle sind bei einer
      Inventur ebenso Ergebnis wie Treffer. manage_jobs.py:
      handle_mscan_update() mergt params, statt sie vollständig zu
      ersetzen — sonst fällt ein Pegel-Inventur-Job beim bloßen
      Titel-Speichern still auf Tonaufnahme zurück, weil die Detailseite
      die neuen Parameter noch nicht mitschickt.
      Hardwaregrundlage: je Squelch-Öffnung EIN Item mit Frequenz/Pegel,
      ohne Signal keins; Zyklusende-Marker auch bei COUNt INF je Runde
      (docs/EB200_Geraeteprofil.md, Abschnitt MSCAN).
- [x] T-1019b (implementiert 2026-09-11, validiert 2026-09-11 am Geraet)
      waterfall.py: _run_mscan_loop() legt den Recorder ueber
      make_recorder() an (MscanJobRecorder mit Ton, MscanLevelRecorder bei
      record_audio False), oeffnet den AUD-Socket nur mit Tonaufnahme,
      wählt in select() entsprechend nur den MSC-Socket und ruft
      scan_teardown_commands() mit record_audio auf. Die Startmeldung nennt
      Modus/Ende-Bedingung, Ton-Betrieb und Schwelle.
      OFFEN und in T-1019c zu lösen: handle_mscan_start() (manage_jobs.py)
      ignoriert bei mode == "inventur" end_iso und duration_min und setzt
      stattdessen ein Sicherheitsende start + max(10 min, 3 · Kanalzahl ·
      (Dwell + 0,1 s)). Eine Inventur mit stop_after "runtime" endet damit
      unabhängig von der gewünschten Dauer spätestens nach diesem
      Sicherheitsende — bei 300 Kanälen und Dwell 0,2 s also nach 10 min.
- [~] T-1019c manage_jobs.py, zwei Teile - Teil (1) implementiert 2026-09-11,
      Teil (2) implementiert 2026-09-11, Browser-Validierung offen.
      (1) ERLEDIGT: handle_mscan_start() setzt das Sicherheitsende nur noch
      bei stop_after "cycle"; bei "runtime" gelten end_iso/duration_min wie
      bei der Überwachung (beide fehlen → "bis Stopp", end_iso None).
      Selbsttest MS2b belegt die vorgegebene Dauer gegen das frühere
      Sicherheitsende.
      (2) ERLEDIGT: :8050 Parameter-Tab mit Zeile „Ende der Inventur“
      (ein Durchgang / bis Laufende, nur bei Modus Inventur sichtbar),
      Schalter „nur Pegel erheben“ und Feld „Schwellwert aktiv (dBµV)“
      (leer = jede Öffnung zählt); saveJob() schickt stop_after,
      record_audio und level_active_dbuv mit. „Nur Pegel“ schlägt Dwell
      0,2 s vor, wenn der bisherige Wert dafür ungeeignet ist, und sperrt
      die Mindestdauer. Lauf-Formular und Start-Payload hängen jetzt an
      einDurchgangGewaehlt() (Modus inventur UND stop_after cycle) statt
      allein am Modus — das Sicherheitsende-Hinweisfeld erscheint nur noch
      dort, Dauer/Endzeit gelten sonst wie bei der Überwachung.
- [x] T-1019d, zwei Teile - Teil (1) implementiert 2026-09-11, Teil (2)
      implementiert 2026-09-11, Browser-Validierung offen.
      (1) ERLEDIGT: eb200_core/mscan_view.py. build_mscan_run_result()
      trennt bei results.kind == "level" die aktiven Kanäle (nach
      channels, samt hits/cycles_active/per_cycle/level_max_dbuv) von den
      stillen (nach silent_channels, mit below_threshold als gemessener
      Aussage), statt silent_channels wie beim Tonlauf aus den im Ergebnis
      fehlenden Snapshot-IDs zu erschließen. run_params reicht stop_after,
      record_audio und level_active_dbuv durch, der Lauf-Kopf zusätzlich
      kind, items, below_threshold und level_active_dbuv; Selbsttest V5.
      manage_jobs.py: build_mscan_runs() zählt results_channels bei kind
      "level" aus active_channels statt über len(channels), sonst wäre dort
      immer die volle Kanalzahl gemeldet; Selbsttest MS6b.
      (2) ERLEDIGT: :8060 (analyze_mscan.py). isLevelRun() (run.kind ==
      "level") ist die einzige Weiche; computeChannelStats() baut die
      Kennzahlen bei einem Pegel-Lauf aus der Kanalzeile statt aus
      recordings[] — vorher hat `if (!recs.length) return;` dort JEDEN
      Kanal ausgeblendet. Eigene Spaltensätze LEVEL_CHAN_COLUMNS und
      LEVEL_EVAL_COLUMNS (Öffnungen statt „Treffer“ = WAV-Anzahl; „aktiv in
      Durchgängen“ aus cycles_active/run.cycles statt Belegung in Prozent,
      die ohne Sendedauern nicht berechenbar ist; Spalte „unter
      Schwelle“), gewählt über chanColumns()/evalColumns(). Lauf-Kopf:
      Zeile „Erhebung“ (nur Pegel, Schwelle, Ende-Bedingung) statt
      Audiobilanz, Mindestdauer fällt aus der Dwell/Hold-Zeile. Reiter
      „Aufnahmen“/„Zeitverlauf“ und die Leiste „Auswahl der Aufnahmen“
      werden ausgeblendet, Kanalzeilen sind nicht klickbar (der Sprung in
      die Trefferliste ginge ins Leere); Selbsttest T3.
      per_cycle (Treffer je Durchgang) liegt im Manifest bereit, wird aber
      noch nicht dargestellt — siehe T-1019f.
- [~] T-1019f Verlauf der Pegel-Inventur sichtbar machen, drei Stufen -
      Stufe 1 implementiert 2026-09-11, Stufe 2 implementiert 2026-09-11, Stufe 3 offen.
      (1) ERLEDIGT: Datenmodell. MscanLevelInventory führt je Durchgang
      neben der Trefferzahl das Pegelmaximum mit; results() liefert
      per_cycle als [Durchgang, Treffer, max_dbuv]-Tripel statt der
      früheren Paare. Das Maximum bezieht sich nur auf die Treffer dieses
      Durchgangs, Öffnungen unter der Schwelle zählen weiterhin allein in
      below_threshold; Selbsttest S17. WICHTIG: wirkt nur für NEUE Läufe -
      bestehende Manifeste tragen Paare, Konsumenten müssen beide Längen
      vertragen und bei Länge 2 auf die Pegelfarbe verzichten (bewusst
      über die Listenlänge statt über eine Versionsnummer im Ergebnis -
      es geht um genau ein Zusatzfeld).
      (2) ERLEDIGT: Spalte „Verlauf“ in der Kanaltabelle von :8060, hinter
      „aktiv in Durchgängen“. Inline-SVG fester Breite (120 px), ein Balken
      je Durchgang; bei mehr Durchgängen als Balken werden benachbarte
      gruppiert und der Balken zeigt das MAXIMUM seiner Gruppe, nicht die
      Summe — ein einzelner Treffer neben einem dichten Nachbarn soll nicht
      verschwinden. Deckkraft nach Trefferzahl, Untergrenze 0,35. Tooltip
      nennt aktive Durchgänge, Gruppengröße und eine etwaige Kappung.
      SVG statt Canvas, weil die Tabelle bei jeder Sortierung neu gebaut
      wird. In die Tabellenzelle statt groß unter die Tabelle: der
      Vergleich ZWISCHEN Kanälen ist die eigentliche Frage, und den liest
      man untereinander in einer Spalte auf einen Blick. Die Spalte ist
      nicht sortierbar (ihre Ordnung steckt in cycles_active daneben).
      Läufe mit Paaren statt Tripeln (vor T-1019f Stufe 1) zeichnen
      unverändert mit, nur ohne spätere Pegelfarbe. Selbsttest T3
      erweitert; V5-Fixture in eb200_core/mscan_view.py auf Tripel
      umgestellt, damit der Selbsttest die Form übt, die neue Läufe
      liefern.
      (3) OFFEN, dreiteilig (Abnahme Stufe 2 am 300-Kanal-Lauf):
      (3a) ERLEDIGT 2026-09-11: Zeitstempel je Durchgang.
      MscanLevelInventory.on_cycle_end() hängt den Sekunden-Offset seit
      Laufbeginn an self._cycle_ends_s an, VOR dem Hochzählen (sonst zeigte
      der Eintrag auf den nächsten Durchgang); results() gibt die Liste als
      cycle_ends_s aus, mscan_view reicht sie im Lauf-Kopf durch. Index i =
      Ende des Durchgangs i. Gerechnet wird t - t0_mono, dieselbe Basis wie
      _wall_iso(). Ohne das hätte die Matrix nur eine genäherte Achse
      (Laufdauer / Zyklenzahl), und die trügt: ein Durchgang über viele
      belegte Kanäle dauert länger als einer über stille, mit Hold 2 s
      spürbar. Wirkt nur für NEUE Läufe; bei älteren fehlt der Schlüssel
      (None) und das Frontend nähert über die Laufdauer.
      (3b) ERLEDIGT 2026-09-11: renderLevelTimeline() in analyze_mscan.py -
      eigene Zeichenroutine statt einer Verzweigung in renderTimeline(),
      weil vier von sieben Bausteinen dort an eine Wanduhr-Achse aus
      Aufnahmen gebunden sind. Wiederverwendet werden matrixRowY(),
      matrixBarAt() und truncateLabel(); neu sind matrixCycleToX() (x aus
      Sekunden seit Laufbeginn ueber geom.offsets) und levelCycleAxis()
      (cycle_ends_s aus Stufe 3a, sonst gleichmaessig genaehert - die
      Legende benennt, welcher Fall vorliegt). Balkenbreite = Dauer des
      Durchgangs, Achse beschriftet als Uhrzeit. Abweichung vom
      urspruenglichen Plan: die Matrix behaelt das Panel des
      Zeitverlauf-Reiters (Canvas, Tooltip, Hinweisfeld stehen dort
      bereits) statt in das des Auswertungs-Reiters umzuziehen; der
      Zeitverlauf-Reiter wird fuer Pegel-Laeufe wieder eingeblendet,
      Auswertung bleibt aus (T-1019g). LEVEL_EVAL_COLUMNS bleibt damit
      vorerst bestehen, ungenutzt.
      (3c) ERLEDIGT 2026-09-11: Farbe nach Pegelmaximum des Durchgangs
      (levelColor/levelScale), blau nach rot ueber den HSL-Farbton,
      Grenzen aus dem Wertebereich DIESES Laufs - gemessen wurden
      20-47 dBuV, eine feste Skala 0-120 staucht das auf drei
      ununterscheidbare Toene. Die Legende unter dem Bild nennt die
      Grenzwerte und den Zustand der Zeitachse. Laeufe mit Paaren statt
      Tripeln (vor Stufe 1) bleiben einfarbig.
- [~] T-1019g (implementiert 2026-09-11, Browser-Validierung offen) Kosmetik
      der Pegel-Ansicht in :8060, aus der Abnahme von T-1019f Stufe 2:
      Spalte „Demod“ entfällt (ohne Tonaufnahme sagt die Betriebsart
      nichts über das Ergebnis), der Verlaufsstreifen trägt keinen Tooltip
      mehr (die Zahl der aktiven Durchgänge steht eine Spalte weiter
      links), und der Reiter „Auswertung“ wird ausgeblendet — er zeigte
      dieselben Zahlen wie die Kanaltabelle, nur ohne Verlauf. Sein Panel
      bleibt im Markup und nimmt mit T-1019f Stufe 3b den Zeitverlauf auf.
- [~] T-1019h (implementiert 2026-09-12, Browser-Validierung offen) Befunde aus
      der Abnahme von T-1019f Stufe 3b/3c am 300-Kanal-Lauf.
      (1) Nullpunkt der Zeitachse ist jetzt der SCAN-Start, nicht der
      Recorder-Start: das Programmieren der Speicherplätze läuft davor und
      kostet bei 300 Kanälen 75–85 s (210–280 ms je Platz), wodurch der erste
      Durchgang um diese Spanne zu lang erschien. MscanLevelInventory.
      mark_scan_start() wird von _run_mscan_loop() nach INIT:CONM gemeldet;
      beide Recorder haben die Methode (beim Tonlauf ohne Wirkung), damit der
      Loop nicht verzweigen muss. Die Spanne davor steht als programming_s im
      Ergebnis und als Zeile „Programmierung" im Lauf-Kopf von :8060. Wirkt
      nur für NEUE Läufe; ältere tragen programming_s None, die Kopfzeile
      entfällt dort.
      (2) Zeilenreihenfolge der Matrix wählbar (Speicherplatz, Frequenz,
      Öffnungen, aktiv in Durchgängen) über ein Auswahlfeld über dem Bild.
      Bei vielen aktiven Kanälen bringt erst die Sortierung nach Auslastung
      die ergiebigen Kanäle nach oben.
      (3) Player-Leiste bei record_audio False ausgeblendet.
- [ ] T-1019e Anzeigetexte für die Pegel-Inventur: die
      Abschlussmeldung in waterfall.py ("<n> Aufnahmen auf <k> Kanaelen")
      und der JS-Banner in HTML_PAGE ("<n> Zyklen – <n> Aufnahmen auf <k>
      Kanälen") zeigen bei einer Pegel-Inventur dauerhaft 0 Aufnahmen,
      obwohl der Lauf Treffer sammelt - "Aufnahmen" meint dort WAV-Dateien.
      Vorschlag: MscanLevelRecorder.current_stats() trägt kind "level"
      (Server liefert den Code, der Client formuliert - siehe
      Lokalisierungsvorbereitung in CLAUDE.md), Meldung und Banner zeigen
      dann Zyklen und aktive Kanäle statt Aufnahmen. Befund aus der
      T-1019b-Abnahme.
      Zwei weitere Fundstellen in manage_jobs.py (Abnahme T-1019c Teil 2):
      die Text-Zusammenfassung einer Laufzeile in der
      Aufzeichnungen-Übersicht ("<n> Aufnahmen auf ...") und die
      Modus-Spalte der MSCAN-Definitionsliste, die beide nur "Inventur"
      schreiben, ohne Ende-Bedingung und Ton-Betrieb zu unterscheiden.
- [~] T-1020 (2026-09-23) Pflichtfeldpruefung ergaenzt: Demodulation und Bandbreite
      muessen bei Kanal und Raster einen wirksamen Wert haben - ein aus den
      Datei-defaults geerbter zaehlt als erfuellt, nur wenn beide Quellen leer sind,
      ist es ein Fehler. Dateiname bei der Neuanlage einer Katalogdatei: .json wird
      ergaenzt, wenn keine Endung angegeben ist, eine abweichende Endung wird
      abgelehnt. Anlass war die Durchsicht zu BUG-0004; die uebrigen Pflichtfelder
      waren bereits geprueft.
      Nicht abgedeckt: der CSV-Importpfad ruft build_channel_entry ohne defaults auf -
      parse_catalog_csv kennt die Ziel-Datei-defaults nicht. Die Demod/BW-Pruefung
      greift dort daher nicht. Bewusst so belassen, statt eine Parameterkette durch den
      Import zu ziehen; als Luecke vermerkt.
      Im Datei-Kopf war mit "Titel" der Dienstname gemeint - das Formular hat kein
      eigenes Titel-Feld, und die Pflichtpruefung "Dienstname darf nicht leer sein"
      besteht bereits. Keine Aenderung noetig.

## Meilenstein 11: Aufzeichnungen-Übersicht (:8050) — Vereinheitlichung DSCAN/IFPAN/MSCAN
Ziel: :8050 zeigt ALLE Aufzeichnungen (Session, Spektrum, Kanalscan) in einer Liste mit
einheitlichem Zeilenschema, unabhängig von der Herkunft (direkt aus der View oder Long-Run-Job).
Erst „Auswertung“ führt in die art-spezifische Analyse-View (eigener Server je Art).
- [~] T-1101 (implementiert, Browser-Validierung offen) manage_jobs.py: MSCAN-Definitionsliste (bisher /mscan) als Abschnitt
      „MSCAN-Scan-Definitionen“ direkt auf „/“ (einklappbar), /mscan → Redirect auf /#definitionen,
      Link „MSCAN-Jobs“ entfernen; Titel „EB200 – Aufzeichnungen“. Kein neues Datenmodell.
      Nachtrag (2026-08-28): Laufend-Kasten zeigt für MSCAN
      „MB – n Voice-Aufnahme(n)" statt Sweeps/Frames/Datei(en).
- [x] T-1102 (2026-08-27) eb200_core/recording_index.py: Lese-Aggregator über jobs/index.json (Long-Run
      dscan/ifpan/mscan) + sessions/*.db (Direkt-Session) + records/*.specbin+.json (Direkt-
      Spektrum) + records/*.wav+.wav.json (Direkt-Audio, Art AUDIO; Long-Run-WAV-Teile bleiben
      Dateien ihres ifpan/dscan-Jobs, keine eigene Zeile). Einheitliches Zeilenschema: art,
      herkunft (direkt|longrun), label, start, dauer,
      zustand, inhalt (art-spezifischer Einzeiler), dateien, bytes, ablage, auswertung_url,
      loesch_ref. Selftest ohne Hardware (tmp-Verzeichnisse). Nur stdlib.
- [~] T-1103 (implementiert, Browser-Validierung offen) :8050 Tabelle „Aufzeichnungen“ auf recording_index umstellen: Spalten Art-Badge,
      Kurztext, Start/Dauer, Inhalt, Zustand, Umfang (Dateien · MB), Ablage, Aktionen.
      Filter-Chips Art (Alle/DSCAN/IFPAN/MSCAN/AUDIO), Herkunft (Alle/direkt/Long Run), Zustand;
      Suchfeld bleibt. Sortierbar nach Start (Standard), Art, Zustand, MB.
      Aktiv/Geplant als schmale Leiste oben in derselben Zeilenform.
      Nachtrag (2026-08-27): Reiter Aufzeichnungen/Definitionen mit Hash-Routing,
      Zustand-Chips entfernt (Tabelle nur abgeschlossene), Inhalt nach Frequenz sortierbar,
      Zeilenlimit 25, Altbestand ohne segment.speed gültig, abort_reason bei ungültigen
      Direkt-Zeilen.
- [~] T-1104 (implementiert, Browser-Validierung offen) Einheitliches „Löschen inkl. Daten“ über recording_index: Long-Run →
      job_store.delete_data; Direkt-Session → DB + Sidecars + Notizen; Direkt-Spektrum →
      .specbin + .specbin.json + Befund/Notiz; Direkt-Audio → .wav + .wav.json + .wav.audspec +
      Notiz. Laufende Jobs nicht löschbar (nur Stopp).
      Pfad-Sandbox an EINER Stelle (recording_index).
      Zusatz: inkl. verwaiste longrun_*-Verzeichnisse (Zustand verwaist, kind orphan);
      Analyse-Server :8020/:8040 löschen über dieselbe Funktion.
- [~] T-1105 (implementiert, Browser-Validierung offen) DSCAN-Long-Run → :8040/?job= (Maxhold-Flatfile ist spectrum_reader-Format, nicht SQLite; die
      frühere „Geschwister-Logik in :8020" entfällt). :8040 ?job= mit genau einer Teildatei öffnet direkt,
      mehrere Teile → gefilterte Liste. IFPAN → :8040 ?job=/?path=, Direkt-Session → :8020 ?path=,
      MSCAN → :8060 ?run=, AUDIO → :8070 ?path= (T-1204).
- [x] T-1106 analyze_mscan.py (:8060): Auswerte-Server Kanalscan. Kanal × Zeit-Matrix,
      Trefferliste mit Audio-Player (je Segment Link „Spektrum" → :8070/?path=), Inventur:
      Belegungs-Ranking, Überwachung: Zeitverlauf. MSCAN-Segmente erscheinen NUR hier, nie
      als eigene Zeilen in :8050.
      Übernimmt T-1006/T-1007-Inhalte. Eintrag in start_eb200.py.
      (vollstaendig validiert 2026-08-29; Nachtrag: computeRunTimeRange
      nutzt ended_iso vor end_iso [Belegungs-% auf tatsaechliche
      Laufdauer], Zeitverlauf-Achse folgt dem Aufnahme-Zeitraum mit
      Polster)
- [x] T-1107 Navigationsmenü „Ansichten“ in waterfall.py um :8060 und :8070 ergänzen; MSCAN-
      Definitionen-Zeile: „Lauf planen“ direkt aus der Liste.
      (revidiert + abgeschlossen 2026-08-29: Menue ergaenzt nur :8010-:8050;
      :8060/:8070 bewusst ausgenommen, reine Deep-Link-Server -
      Sackgassen-Menuepunkte nach Browser-Validierung wieder entfernt)
- [~] T-1108 Läufe-Tab (/mscan/job): Löschen je Lauf — Endzustände via /api/deletedata
      (inkl. Messdaten/Segment-WAVs), geplant/verpasst via /api/delete; Bestätigungsdialog
      nennt Aufnahmenzahl. Definition und Läufe löschen sich NIE gegenseitig (Snapshot-
      Unabhängigkeit). Browser-Validierung offen.
- [x] T-1109 Laufende Direkt-Aufzeichnungen (Audio/Spektrum/Session) in
      der Laufend-Leiste von :8050 (eigener Container, aus recording_index-
      Zeilen zustand!=done + herkunft=direkt); WavAudioWriter schreibt Sidecar
      ab Aufnahmestart mit running-Flag (Muster SpectrumBinWriter);
      Loesch-Sperre ueber running-Flag mit 60-s-Absturzrest-Escape.
      (implementiert 2026-08-28; validiert 2026-08-28)
- [x] T-1110 UI-Konsistenz :8050: laufende Direktaufnahmen innerhalb der
      Laufend-Box (Leermeldung konsolidiert); Art-Badges auch fuer Jobs in
      Laufend/Geplant; Filter-Chips (Art/Herkunft) im URL-Hash persistent;
      Kurztext-Editieren fuer alle Aufzeichnungsarten ueber zentrales
      set_recording_label (recording_index) + /api/reclabel.
      (implementiert 2026-08-28; Browser-Validierung offen; Nachbesserung
      2026-08-28: Filter zusaetzlich in sessionStorage (Rueckkehr ueber
      :8070-Link), Editier-Waechter pausiert 5-s-Poll; validiert 2026-08-28)
- [x] T-1111 (implementiert 2026-08-31, validiert 2026-08-31) Kosmetik :8050:
      Aufzeichnungstabelle mit eigener Spalte "Aufnahmen" (Dateizahl, sortierbar)
      zwischen Zustand und Umfang; Umfang zeigt nur noch MB. MSCAN-Job-Detail:
      Button "Kanaele hinzufuegen" (vorher "Kanaele waehlen ..."); Parameter-Tab
      ohne SCPI-Befehle in den Labels (Dwell-Zeit, Hold-Zeit, "Squelch aus
      Kanaleinstellung"); Felder mit Trennlinie (#333) und 14 px Abstand.
- [x] T-1112 (implementiert 2026-09-05, validiert 2026-09-05) IQ-Kennzeichnung :8050:
      Zusatz-Badge "IQ" (orange, Tokens --badge-iq-bg/-fg in eb200_core/ui_theme.py)
      neben dem AUDIO-Badge fuer Zeilen mit inhalt.is_iq - in der Spalte Art und in der
      Laufend-Leiste. Eigener Art-Filterchip "IQ" (Filterwert 'iq', persistent in Hash
      und sessionStorage). IQ bleibt bewusst KEINE eigene Art in ART_LABEL bzw.
      recording_index.summarize() - reines Merkmal von AUDIO-Zeilen, Backend unveraendert.
- [x] T-1113 (implementiert 2026-09-09, validiert 2026-09-15) Kopfzeile :8060:
      Ansichtstitel 'MSCAN-Lauf „<Kurztext>“ <Lauf-ID>' statt nur 'MSCAN-Lauf <Lauf-ID>'.
      Kurztext ist run.label aus /api/run (job_store-Label, in :8050 editierbar); fehlt er,
      bleibt es beim bisherigen Titel. Setzung in renderHeader(), document.title zieht mit.
- [x] T-1114 (implementiert 2026-09-14, validiert 2026-09-14) Spaltensatz je Art-Filter
      in der Aufzeichnungstabelle :8050: statt EINER festen Liste baut recColumns() den Satz
      aus dem Art-Chip (filterArt). "Alle" unveraendert; DSCAN zeigt F (min)/F max/BW/Speed/
      Sweeps/Segmente, IFPAN Mittenfrequenz/Span/Demod (Bandbreite als Unterzeile)/Frames,
      MSCAN Modus/Kanaele/aktive K./Aufnahmen (inhalt.recordings)/Zyklen, AUDIO und IQ
      Mittenfrequenz/Rec.BW (Demod als Unterzeile)/Sampling (+Spektrum als Unterzeile).
      In allen vier Art-Ansichten entfallen "Inhalt" und "Aufnahmen" (Dateizahl, T-1111).
      Gemeinsame Spalten als benannte Konstanten COL_*, art-eigene als ART_COLUMNS_*;
      buildRecHead(cols, activeKey) bekommt Satz und Sortierspalte von aussen, sortKey
      ueberlebt einen Filterwechsel und faellt bei fehlender Spalte auf "start" zurueck.
      Backend (eb200_core/recording_index.py) unveraendert - alle Werte aus r.inhalt.
- [x] T-1115 (implementiert 2026-09-14, validiert 2026-09-14) Lesbarkeit MSCAN-Job-Detail
      (:8050 /mscan/job): Reiter "Parameter" entfaellt - seine Felder stehen jetzt im Reiter
      "Job" unter Titel/Notiz/Meta-Zeile; Reiterleiste damit Job | Kanaele | Laeufe.
      Der Job-Reiter ist auf 820 px begrenzt, damit die Hinweistexte umbrechen statt auf
      breiten Bildschirmen einzeilig durchzulaufen - Kanal- und Lauftabelle bleiben unbegrenzt.
      Neue Klasse .row-title (fett, --fg, 1.02em) fuer die Ueberschriften Titel, Notiz, Modus,
      Ende der Inventur, Dwell-Zeit, Hold-Zeit, Schwellwert aktiv, Mindestdauer Aufnahme; die
      inline-Labels der Radios/Checkboxen bleiben unveraendert. In der Kanaltabelle ist die
      Spalte Frequenz (MHz) fett (td.freq-cell, ohne eigene Farbe, damit .missing-row rot
      bleibt); dafuer nimmt buildChannelsHead() mehrere Klassen je Spalte entgegen
      (col.cls.split(' ')). Reine Darstellung - Backend, API und gespeicherte Parameter
      unveraendert.

## Meilenstein 12: Audio-Auswertung (:8070) — Spektrum/Wasserfall für WAV-Aufnahmen
Ziel: Jede WAV-Aufnahme der Suite (Direkt aus IFPAN-Zoom, Long-Run-Teil, MSCAN-Segment) ist
im Browser anhörbar und als Audio-Spektrum (0–8 kHz, 16 kHz/16 Bit/mono) auswertbar. Der
Auswerte-Server hat KEINE eigene Aufnahmeliste — Einstieg nur per Deep-Link aus :8050/:8060.
Reihenfolge: T-1205 → T-1204 → T-1201 → T-1202 → T-1203; T-1206/T-1207 nachrangig.
- [~] T-1201 audio_spectrum.py (Repo-Root, numpy erlaubt): STFT-Modul mit stft(path, n_fft,
      hop) → quantisierte dB-Matrix (uint8, Skala im Kopf); n_fft 512/1024/2048, Hann, 50 %
      Overlap; Übersicht per Max-Hold auf ≤ 8000 Zeilen (Muster .specbin-Dezimierung),
      Drill-down eines Zeitfensters in voller Auflösung; regenerierbarer Cache
      <name>.wav.audspec (Sidecar-Regel wie .befund.json). Selftest ohne Hardware
      (synthetische WAV mit Zweitonsignal, Bin-Lage prüfen). Schnittstelle so schneiden, dass
      die spätere Sprach-Nachklassifikation der MSCAN-Segmente sie als Konsument nutzt.
      (implementiert 2026-08-28; Konsument-Validierung folgt mit T-1202/T-1203)
- [x] T-1202 analyze_audio.py (:8070): Server ohne Liste. Routen: / (?path=), /api/wav
      (Datei mit HTTP-Range/206 für Scrubbing, zugleich Download-Pfad), /api/audspec
      (Übersicht/Drill-down aus T-1201), /api/note (LabelStore) (known_freq eingebettet
      in /api/meta). resolve_safe_path wie :8040, register_server, Eintrag in
      start_eb200.py. Kopf: Sidecar-Metadaten (freq/mode/bw/start), Known-Freq-Label,
      Job-Kontext falls im Job-Verzeichnis, Geschwisterdateien (weitere Teile /
      passendes .specbin) als Links.
      (implementiert 2026-08-28; validiert 2026-08-28: Deep-Link aus :8050,
      Wiedergabe/Scrubbing, /api/audspec-Antwortzeiten an echter Aufnahme)
      Nachtrag 2026-08-29: Geschwisterliste zeigt bei MSCAN-Segmenten einen
      Sammellink zur Trefferliste (:8060) statt aller Segment-WAVs.
- [x] T-1203 Frontend :8070: Wasserfall Zeit × Frequenz mit Spielzeiger, gekoppelt an
      <audio>-Element (Datei-Wiedergabe, nicht Stream — <audio> hier zulässig); Klick ins Bild
      setzt Position; zwei Frequenzmarker mit Hz- und Delta-Anzeige (Shift-/Tonabstand-
      Messung); FFT-Länge umschaltbar; Colormap/Auto-Scale nach T-401-Entscheidung (erster
      Konsument der gemeinsamen Render-Schicht oder bewusst markierte Kopie).
      (implementiert 2026-08-28; validiert 2026-08-28: Zweiton-/RTTY-Aufnahme,
      Marker-Delta, Zoom, Spielzeiger-Kopplung)
- [x] T-1204 Direkt-Audio in :8050: über recording_index (T-1102) als Art AUDIO, Herkunft
      direkt, Zustand beendet; Zeile aus .wav.json (freq/mode/bw/start/dauer/MB); Querverweis
      auf .specbin mit gleichem Namenspräfix (JJJJMMTT_HHMMSS_<freq>kHz_<Mode>); Auswertung →
      :8070/?path=; Löschen nach T-1104. Kein Eintrag im job_store-Index.
      (implementiert 2026-08-28; Browser-Validierung offen: AUDIO-Zeile, Filter-Chip,
      Loeschen, Auswertungs-Link zeigt auf :8070 - Server folgt mit T-1202) (validiert 2026-08-28)
- [x] T-1205 Ablage vereinheitlichen: RECORD_DIR („records“, hart verdrahtet in waterfall.py
      und analyze_spectrum.py) durch ersten Eintrag aus storage_roots.json ersetzen
      (Schreibseite von T-201); Lese-Whitelist aller Viewer = alle storage_roots. Bestehende
      records/-Ablage bleibt gültig, wenn sie als storage_root eingetragen ist.
      (implementiert 2026-08-28; Betriebsvalidierung: naechste echte Aufnahme
      mit und ohne storage_roots.json) (validiert 2026-08-28)
- [ ] T-1206 Zeitachsen-Kopplung Long-Run: :8040 und :8070 akzeptieren ?t= (Sekunden ab
      start_iso) und verlinken wechselseitig auf die Geschwisterdatei desselben Job-
      Verzeichnisses. Nach T-1202.
- [ ] T-1207 Live-Audiospektrum im IFPAN-Zoom (:8000): AnalyserNode im bestehenden Mithören-
      AudioContext, kleine Spektrumleiste im Zoom-Kopf. Nur Browser, kein Serveranteil.
- [x] T-1208 FFT-Erweiterung Audio-Spektrogramm um 128 und 256
      (audio_spectrum FFT_SIZES, Cache-Gueltigkeit erzwingt vollstaendige
      Sektionsmenge = automatische Neuerstellung der Bestands-Caches,
      :8070-Umschalter). (implementiert 2026-08-29; validiert 2026-08-29)
- [x] T-1209 Kosmetik :8070: Toolbar dreizeilig (Zoom-Hinweis + „Zoom Out“ / FFT /
      Marker-Hinweis „CTRL + Mausklick“ mit Marker A, Marker B, Differenz, „Marker löschen“),
      Trennlinien; dB-Min/Max-Anzeige entfernt; Spektrogramm volle Fensterbreite mit
      Resize-Neuzeichnen aus gecachten Daten; Achseneinheiten (y „kHz“, x „Zeit [min:s|s|ms]“
      nach Zoomstufe). (implementiert 2026-08-29; Browser-Validierung offen)
- [x] T-1210 Kosmetik :8040 Aufnahmetabelle: Spaltenfolge Datei, Datum, Beginn, Ende,
      Job, Mittenfrequenz (statt „Mitte“, rechtsbündig, hellgrün), Span, BW, Demod, Bins,
      Frames (rechtsbündig), Kurztext, Aktion. Kopfzellen übernehmen die Spaltenklasse.
      (implementiert 2026-08-30; Browser-Validierung offen)
      Nachtrag 2026-08-30: Spaltenfolge geändert auf Datum, Beginn, Ende, Mittenfrequenz,
      Span, BW, Demod, Bins, Frames, Job, Datei, Kurztext, Aktion; Ungültig-Zeile als
      Vollbreiten-Zelle (Dateiname + Grund). (Browser-Validierung offen)
- [~] T-1211 (implementiert 2026-09-05, Browser-Validierung offen) Kosmetik :8070:
      Bei Aufnahmen ausserhalb 1 Kanal / 16 Bit (in der Praxis IQ) entfallen
      Wiedergabeleiste, Spektrogramm-Block samt Werkzeugleiste sowie die Zeilen
      "Klasse (auto)" und "Klasse (manuell)"; /api/classify wird gar nicht erst
      angestossen. Kriterium identisch zu audio_spectrum._check_supported(),
      damit genau die Elemente verschwinden, die sonst nur "unsupported" melden.
      Nachtrag 2026-09-05: statt der leeren Stelle eine Metadatenzeile "Format"
      mit Kanalzahl/Bittiefe aus dem WAV-Header ("IQ (2 Kanäle): Wiedergabe und
      Audio-Spektrogramm nicht anwendbar"). (Browser-Validierung offen)
- [~] T-1212 (implementiert 2026-09-07, Commit 9c1556f; Praxisvalidierung an der
      langen IQ-Aufnahme offen)
      audio_spectrum.build_audspec(): ein Lesedurchlauf statt einer je
      FFT-Groesse, Max-Hold-Dezimierung im Durchlauf (_MaxHoldRows) und dB-Skala
      aus einem mitlaufenden Histogramm (_hist_accumulate/_robust_scale_hist)
      statt aus der Sammlung aller Rohwerte. Befund 2026-09-07 an
      20260907_094655_10149kHz_IQ.wav (6779,7 s, 32 kHz, IQ): raw_by_fft hielt je
      FFT-Groesse rows_raw * n_bins = 2 * n_frames float32, also fuenfmal 1,74 GB,
      dazu dieselbe Menge noch einmal fuer np.percentile - Spitzenbedarf ueber
      25 GB, die Datei war damit nicht darstellbar. Nach dem Umbau: MAX_ROWS *
      n_bins je Groesse (zusammen gut 100 MB) und ein Fuenftel der Lese-I/O.
      Entscheidung 2026-09-07 (OE3GAS): Cache-Format bleibt "eb200-audspec-v2" -
      die Histogramm-Perzentile weichen um hoechstens eine halbe Klassenbreite
      (0,025 dB) ab, der Unterschied in den uint8-Matrizen liegt bei hoechstens
      1 Zaehler; Bestands-Caches bleiben gueltig, kein v3. build_audspec()
      erhaelt bereits den optionalen progress_cb-Parameter, den T-1213 nutzt.
- [~] T-1213 (implementiert 2026-09-07, Browser-Validierung offen)
      Fortschrittsanzeige :8070 beim Aufbau grosser Spektrogramme.
      Registry in audio_spectrum.py (Modul-Dict + Lock, progress_snapshot()),
      gefuellt ueber progress_cb und installiert von ensure_audspec(); neuer
      Endpunkt GET /api/audspec_status?path= in analyze_audio.py (Phasencodes
      ASCII, Anzeigetext im Frontend); Balken statt "laedt ..." in der
      Werkzeugleiste, Poll alle 500 ms mit 800 ms Anlaufverzoegerung, damit
      Cache-Treffer nicht kurz aufblitzen. Nach T-1212.
- [~] T-1214 (implementiert 2026-09-09, Browser-Validierung offen) Kopfzeile :8070:
      Ansichtstitel '„<Kurztext>“ <Dateiname>' statt nur <Dateiname>. Kurztext ist
      meta.label aus /api/meta (audio_labels.json, in :8050 editierbar); fehlt er, bleibt
      es beim Dateinamen. Setzung in load() nach dem /api/meta-Fetch, document.title
      zieht mit.

## Meilenstein 13: Repo-Hygiene und Entwicklungswerkzeuge
- [x] T-1301 Produktname "EB200 Suite" -> "MoniScope" (Titel, Header, Sammelstarter,
      Docstrings); Geraetename EB200 unveraendert (Commit e770375, 2026-08-30).
- [x] T-1302 (2026-08-30) .gitattributes: eol=crlf explizit, *.sh lf, Binaerregeln. Index war
      unter text=auto bereits LF-normalisiert; 7 Dateien lagen nur im Arbeitsbaum
      mit LF (Befund aus T-1301) -> Refresh aus dem Index.
- [x] T-1303 (2026-08-30) tools/check_js_blocks.py: node --check auf alle <script>-Bloecke in
      Modul-Strings (ast-basiert, keine Kommentar-Fehltreffer), Zeilenabbildung auf
      die .py-Datei, --selftest. Ab jetzt feste Gate-Zeile in allen Prompts mit
      JS-Aenderungen: python tools/check_js_blocks.py <Dateien> -> Exit 0.
- [ ] T-1304 start_eb200.py -> start_moniscope.py umbenennen (git mv), Verweise in
      Doku/Verknuepfungen nachziehen.
- [ ] T-1305 Einheitlicher --selftest-Einstieg in allen acht Servern (Befund aus T-1402):
      manage_jobs.py, analyze_mscan.py, analyze_audio.py haben Funktion und Argument;
      waterfall.py hat _self_test(), aber kein Argument-Dispatch (ueber CLI nicht
      erreichbar); analyze_spectrum.py, analyze_sessions.py, sdr_waterfall.py und
      block_admin.py haben beides nicht - "python <server>.py --selftest" startet dort
      ungewollt den echten Server. Ziel: gleiches Muster ueberall (argparse mit
      --selftest, Rueckgabe 0/1), damit die Gate-Zeile in jedem Prompt nutzbar ist.

- [x] T-1306 (2026-09-20) Rueckfallpunkt "de-final" vor Meilenstein 18: annotierter Tag auf
      dem letzten deutschen Stand, Git-Bundle ausserhalb des Repos, .gitignore ergaenzt.
- [x] T-1307 (2026-09-20) Katalogregel vom 2026-08-30 revidiert: Referenzkataloge werden
      versioniert und ausgeliefert. Offene Luecken des Rueckfallpunkts im Log ergaenzt.
- [x] T-1309 (2026-09-22) Zeilenenden im Arbeitsbaum hergestellt. 57 Dateien trugen LF,
      obwohl .gitattributes eol=crlf vorschreibt. Kein Repository-Defekt: bei text=auto
      speichert Git Textdateien in der Objektdatenbank IMMER LF-normalisiert, und
      eol=crlf wirkt allein beim Auschecken (Smudge), nicht beim Hinzufuegen (Clean).
      Vier Anlaeufe waren noetig: git add --renormalize wirkt in der Clean-Richtung und
      fand nichts; git checkout -- <pfad> ist ein No-Op, weil Git den gefilterten
      Arbeitsbaum-Inhalt gegen den Blob vergleicht, beide LF sind und die Datei damit
      als aktuell gilt - der Smudge-Filter kommt nie zum Zug; git checkout-index -f
      ebenfalls wirkungslos, aus demselben Grund. Wirksam war erst: Datei loeschen,
      dann git checkout -- <datei>, je Datei einzeln. Danach w/lf = 0, git diff --stat
      durchgehend leer. Nebenbefund: unmittelbar nach dem Neuschreiben meldet git
      status kurzzeitig ein racily-clean "M", das git update-index --refresh aufloest -
      ein Stat-Cache-Effekt, kein Inhaltsunterschied.
      Die Annahme aus dem urspruenglichen Anlass (Gate G14 in T-1802b1), ein Commit auf
      eine solche Datei erzeuge einen Diff ueber die ganze Datei, war falsch - T-1802b1
      selbst zeigt fuer job_store.py 22 Einfuegungen und 4 Loeschungen. Die Warnung
      "LF will be replaced by CRLF the next time Git touches it" bezieht sich auf den
      Checkout, nicht auf den Commit.
      Klarstellung dabei: tools/ui_lang_basis.json ist der eingefrorene Ausgangswert
      des Meilensteins (erhoben mit T-1805d) und wird waehrend der Umstellung NICHT
      nachgefuehrt - sonst wandert die Messlatte mit und der Fortschritt ist nicht mehr
      ablesbar. Ein Gate, das Unveraendertheit belegen soll, misst deshalb in der
      Vorpruefung derselben Aufgabe und vergleicht am Ende dagegen, nicht gegen die
      Basisdatei.
      Folgerung fuer CRLF-Gates in Prompts: nicht gegen einen Absolutwert pruefen,
      sondern CRLF- und LF-Zahl vor und nach dem Edit gegeneinander - ein Gate soll
      belegen, dass der Edit die Zeilenenden nicht verbiegt, nicht dass die Datei einer
      Konvention folgt.
- [x] T-1830 (2026-09-26) Arbeitsregeln fuer Cloud-Sitzungen. Das Repository liegt
      seit heute auf github.com/oe3gas/EB200repo (privat) und kann damit in
      Cloud-Sitzungen bearbeitet werden. CLAUDE.md haelt fest, was in einer frisch
      geklonten Linux-VM anders ist: sauberes Arbeitsverzeichnis, CRLF trotz Linux
      (eol=crlf wirkt plattformunabhaengig), numpy aus dem Aufsetz-Skript, keine
      Hardware, Pull Request statt push.
      Anlass: erste Cloud-Sitzung. Diese Task ist zugleich ihr Probelauf - klein
      genug, dass ein Fehlschlag nur zwei Textdateien betrifft.
      Befund bei der Vorbereitung: die Annahme, .gitattributes wuerde unter Linux zu
      LF fuehren, war falsch. "* text=auto eol=crlf" enthaelt beide Angaben; das
      eol=crlf ist plattformunabhaengig. Ein geplantes Umschreiben der CRLF-Gates
      entfaellt damit ersatzlos.
- [x] T-1830a (2026-09-26) Drei Richtigstellungen in CLAUDE.md, alle aus dem
      Probelauf T-1830 belegt.
      (a) Der Pull Request entsteht NICHT von selbst: die Cloud-Sitzung legt den
          Zweig an, der Push braucht eine ausdrueckliche Freigabe, und der Pull
          Request entsteht erst danach auf Anweisung.
      (b) Das Aufsetz-Skript laeuft nicht im Repository-Verzeichnis;
          "pip install -r requirements.txt" scheitert dort, die Sitzung startet gar
          nicht erst. Es installiert jetzt direkt numpy.
      (c) Zeile 14 nannte .gitattributes mit "* text=auto" statt
          "* text=auto eol=crlf". Aus dieser Zeile wurde in T-1830 ein falscher
          Gate-Sollwert abgeleitet - eine Dokumentationszeile, die einen Prompt in
          die Irre fuehrt, gehoert richtiggestellt.
      Regel daraus: eine frisch geschriebene Regel ist eine Behauptung, bis ein
      Durchlauf sie bestaetigt. T-1830 hat zwei ihrer eigenen Saetze widerlegt -
      und war genau deshalb als Probelauf richtig angesetzt.

- [x] T-1831 (2026-09-27) Autonomer Uebersetzungsbetrieb: der Auftraggeber ist nicht
      mehr Bote zwischen Prompt-Autor und ausfuehrender Sitzung.
      Anlass: der Ablauf verlangte je Aufgabe vier Handgriffe von ihm - Sitzung
      starten, "push freigegeben" antworten, lokal zusammenfuehren, den naechsten
      Auftrag hochschieben. Drei davon sind entbehrlich.
      (a) CLAUDE.md, Abschnitt "Cloud-Sitzungen": Push ohne Rueckfrage direkt auf
          main, sobald alle Gates gruen sind. Kein Zweig, kein Pull Request, keine
          Freigabe. Weicht etwas ab, bleibt "abbrechen und berichten" unveraendert
          in Kraft - dann wird nur der Bericht gepusht, auf einen Zweig
          "bericht/T-XXXX", damit keine ungepruefte Aenderung auf main landet.
          Vor jedem Commit "git pull --rebase", weil parallel docs/ von aussen kommt.
      (b) tools/sync_docs.ps1 (geplante Aufgabe, alle fuenf Minuten): bringt neue
          Auftrags- und Berichtsdateien unter docs/ zwischen Rechner und GitHub auf
          denselben Stand. Fasst ausschliesslich docs/ und sich selbst an, niemals
          Code oder Laufzeitdaten. Damit erreichen lokal geschriebene Auftragsdateien
          die Cloud-Sitzung ohne Zutun - und ohne dass ein Zugangstoken die Hand
          wechselt.
      (c) docs/prompts/00-Auftragsfolge.md: Treiberauftrag, der die Liste T-1814i bis
          T-1820 der Reihe nach abarbeitet, je Aufgabe pusht und bei der ersten
          Abweichung anhaelt statt zu ueberspringen. Fehlt eine Auftragsdatei, endet
          die Sitzung mit Angabe der fehlenden Datei; geraten wird nichts.
      Grenze, die bleibt: eine Sitzung muss gestartet werden, und die Abnahme der
      Oberflaeche macht der Browser des Auftraggebers. Ein gruenes Gate sagt, dass
      die Zeichenketten stimmen, nicht dass die Seite gut aussieht - BUG-0005 wurde
      nur so gefunden.
- [x] T-1832 (2026-09-28) BUG-0006: die Lauftabelle der MSCAN-Detailseite (:8050,
      /mscan/job, Reiter Runs) blieb immer leer, auch bei vorhandenen Laeufen - selbst die
      Ersatzzeile "no runs" fehlte.
      Ursache: RUN_COLUMNS ruft in der Spalte abort_reason die Funktion abortText(r) auf,
      die aber nur im Skript von HTML_PAGE definiert war. Die Detailseite ist eine eigene
      Seitenkonstante mit eigenem Skript. Der Browser warf beim Aufbau der ersten Zeile
      ReferenceError: abortText is not defined; renderRuns() hatte tbody.innerHTML = ''
      da schon ausgefuehrt, daher Kopf sichtbar und Koerper leer.
      Eingeschleppt von c412001 (T-1802b1, 2026-09-22), das Funktion und Aufruf in einem
      Zug anlegte - in zwei verschiedene Seitenkonstanten. Nicht von Meilenstein 18
      verursacht; die Uebersetzung hat nur die Spaltenbeschriftung geaendert.
      Behebung: abortText wandert nach eb200_core/ui_theme.py in THEME_HEAD_JS, hinter
      labelOrDash, und entfaellt in manage_jobs.py. Damit hat jede Seite jedes Servers
      sie, und die Doppelung, aus der der Fehler entstand, ist aufgeloest. labelOf und die
      Labelgruppe abort_code liegen dort bereits.
      ui_theme.py --selftest bekommt P4: abortText und abort_code muessen in THEME_HEAD_JS
      stehen.
      Gefunden bei der Browser-Abnahme von manage_jobs.py (docs/abnahme/manage-jobs.md),
      nicht von einem Gate. Zweiter Fall nach BUG-0005, in dem nur das Hinsehen im Browser
      einen Datenfehler aufgedeckt hat.

## Meilenstein 14: Light Theme (umschaltbar) — Farb-Tokens suite-weit
Beschluss 2026-08-31 (siehe Entscheidungslog). Vorgehen: erst die Farbschicht
einziehen (Tokens, Dark = heutige Farben), dann Light darueberlegen; Server
fuer Server, jeder Schritt browser-validiert. Entwuerfe (statische HTML-
Vorschauen, abgenommen 2026-08-31): :8050 Aufzeichnungen, :8060 MSCAN-Lauf,
:8040 Spektrum-Auswertung, :8000 D-SCAN. Canvas-Flaechen bleiben in beiden
Themes dunkel. Bedienglyphen als Inline-SVG (fill:currentColor), nie als
Emoji-faehiges Unicode (U+23E9/U+23EA/U+23F8/U+1F5BC: Windows rendert farbig).
- [x] T-1400 (implementiert 2026-08-31, Commit 00320f0; Browser-validiert 2026-08-31:
      Light/Dark, Umschalter, Cookie, drei Seiten, Speichern-Meldung)
      eb200_core/ui_theme.py (THEME_CSS Light/Dark, BASE_CSS, THEME_HEAD_JS,
      header_html) + Referenzserver :8050 manage_jobs.py vollstaendig auf Tokens
      (COMMON_CSS, drei Seiten-Styles, Inline-Farben, JS-Farbzuweisungen), Kopfleiste
      statt <h1>, Umschalter, Cookie eb200_theme. Gate: kein Hex-Farbwert mehr in
      manage_jobs.py.
- [x] T-1400a (2026-08-31, Commit afe067c, Sichtpruefung ok) Kosmetik-Nachtrag aus der Validierung (nur ui_theme.py): Art-Badges
      kraeftig gefuellt (Light: Vollton + weisse Schrift, Dark: Fuellungen wie vor
      T-1400 + dunkle Schrift); Reiter mit eigenen Token --tab-active-bg/-fg und
      Rahmen border-strong (Dark-Kontrast); Dark --border #333 -> #454545
      (Tabellen-Trennlinien).
- [x] T-1401 (2026-08-31, Commit 7827d58, Browser-validiert) :8060 analyze_mscan.py auf ui_theme; loest die bewusste Kopie
      _COMMON_CSS_COPY (T-1106a) durch Import ab (_COMMON_JS_COPY bleibt bis T-401).
      Lauf-Kopf als zweispaltige Karte (CSS-Grid, Spaltenfluss), Kopfleiste statt
      <h1>, HTML_NO_RUN ebenfalls im Theme. Hex-Farben verbleiben nur im
      Zeichencode der Zeitverlauf-Matrix (Canvas, 7 Zeilen). (implementiert 2026-08-31)
- [x] T-1402 (2026-08-31, Commit c516c0d, Browser-validiert) :8040 analyze_spectrum.py: Chrome auf Tokens (drei Karten: Navigator,
      Spektrogramm, Spektrum+Pegel), Canvas bleibt schwarz, Transport- und Play/Pause-
      Glyphen als Inline-SVG, Inline-/JS-Farben als Klassen .err. Hex verbleibt nur im
      Canvas-Zeichencode und im KNOWN_STRIP_BLOCK (unveraendert, sync-gebunden).
      Das getComputedStyle/'themechange'-Muster fuer Beschriftung ausserhalb der
      Schwarzflaeche wird erst dort festgelegt, wo es erstmals gebraucht wird
      (:8000 Farbskala/Zeit-Gutter, T-1407) - :8040 hat keine solche Flaeche.
      (implementiert 2026-08-31)
- [x] T-1403 (2026-08-31, Commit ceb3c55, Browser-validiert) :8020 analyze_sessions.py (Muster T-1402; KNOWN_STRIP_BLOCK unveraendert):
      Kopfleiste statt <h1>, Segment-Ansicht zweispaltig als Karten, Segment-Kacheln
      als Karten, Inline-/JS-Farben als Klasse .err. Hex verbleibt nur im Canvas-
      Zeichencode und im KNOWN_STRIP_BLOCK. (implementiert 2026-08-31)
- [x] T-1404c (2026-08-31, Commit 0a64b8a) Wiedergabe :8070 aus dem Seitenkopf in eine eigene Karte zwischen
      Metadaten und Wasserfall verschoben (Befund Sichtpruefung: im hellen Design
      oben leicht zu uebersehen; inhaltlich gehoert sie neben das Spektrogramm, dessen
      Zeitmarker ihr folgt). Reine Umplatzierung im Markup, keine Logikaenderung.
- [x] T-1404b (2026-08-31, Commit a5b5915) Marker :8070 (Befund Sichtpruefung T-1404a): Markerlinie 2 px statt 1 px,
      Beschriftung "Marker A"/"Marker B" waagrecht mittig ueber der jeweiligen Linie
      (dunkler Saum fuer Lesbarkeit auf jeder Colormap-Farbe, Fallback unter die Linie
      am oberen Plotrand).
- [x] T-1404a (2026-08-31, Commit 061b7e1) Nachtrag aus der Validierung T-1404: (1) Overlay-Canvas :8070 wieder
      transparent - die canvas-Regel aus BASE_CSS hatte das Spektrogramm verdeckt
      (Warnhinweis dazu in ui_theme.BASE_CSS); (2) .kv nicht als Grid, da eine Zeile
      mehrere Geschwister enthaelt (Abzeichen, Link, Merkmalsblock) und der <pre>
      sonst in der Label-Spalte landet; Merkmalsblock nun ueber die volle Breite.
- [x] T-1404 (2026-08-31, Commits 28999df/061b7e1/a5b5915/0a64b8a mit T-1404a-c, Browser-validiert) :8070 analyze_audio.py: Kopfleiste statt <h1> (Dateiname weiterhin per JS
      in #pageTitle), Metadaten und Wasserfall als Karten, Kennwertzeilen (.kv) als
      Raster mit Trennlinien, HTML_NO_PATH ebenfalls im Theme. Die Klassen-Abzeichen
      (LEER/RAUSCHEN/CW/DIGITAL/SPRACHE/UNKLAR) behalten ihre eigenen Farben in beiden
      Themes - sie kodieren die Klasse, nicht die Aufnahmeart. Spektrogramm-Canvas
      unveraendert. (implementiert 2026-08-31)
- [x] T-1405 (2026-08-31, Commit 4fe81c9, Browser-validiert) :8010 block_admin.py (servergerendertes HTML, eigenes Ersetzungsmuster):
      Kopfleiste EINMAL in PAGE_HEAD (wird an sieben Stellen zu Seiten zusammengesetzt),
      die seitenspezifische <h1> bleibt als Seitentitel darunter; Kacheln, Pillen und
      Bearbeitungsblatt auf Tokens; kein Canvas auf dieser Seite. Kein --selftest
      vorhanden (siehe T-1305). (implementiert 2026-08-31)
- [x] T-1406 (2026-08-31, Commit 98d429a, Browser-validiert) :8030 sdr_waterfall.py: Kopfleiste statt <h1> (Geraetename bleibt als
      Seitentitel, wird vom Script gesetzt), Fehlerkasten und Toast auf Tokens; Canvas-
      Spalte unveraendert schwarz, KNOWN_STRIP_BLOCK unangetastet. Kein --selftest
      vorhanden (siehe T-1305). (implementiert 2026-08-31)
- [x] T-1407 (2026-08-31, Commit 976aab7, Browser-/Hardware-validiert) :8000 waterfall.py, Teil 1 - Hauptseite: CSS-Block auf Tokens, Umschalter
      in die vorhandene Kopfleiste (eigene Leiste mit Mittenfrequenz/Resize-Hinweis/
      REC-Abzeichen, deshalb kein header_html), zwei Inline-Styles und sieben
      JS-Farbzuweisungen als Klassen. Neue Tokens --canvas-overlay-* (in beiden Themes
      dunkel) fuer Flaechen AUF dem Spektrum: Sweep-Kasten, Markerliste, Zoom-Infokasten.
      Canvas-Zeichencode und KNOWN_STRIP_BLOCK unveraendert. (implementiert 2026-08-31)
- [x] T-1407a :8000 Teil 2 - Status-Seite (STATUS_HTML, eigenes zweites Template) auf
      Tokens; Farbskala (drawColorbar) theme-abhaengig: Rahmen/Beschriftung per
      getComputedStyle aus den Tokens statt fest #555/#bbb, Neuzeichnen auf das
      themechange-Ereignis (colorbarLast zuruecksetzen). Einziger Punkt im Meilenstein,
      der Zeichenverhalten aendert - deshalb bewusst getrennt von T-1407.
- [x] T-1407b (2026-08-31, Commit 48d43d5, Sichtpruefung ok) Toolbar-Glyphen :8000 auf Inline-SVG (Pause U+23F8, JPG U+1F5BC, Loeschen
      U+1F5D1, Zum-Anfang U+23EE): Windows rendert sie als farbige Emoji (Regel aus
      T-1402). Nach T-1407a.
- [x] T-1409a (2026-08-31, validiert) :8040 Breitenberechnung des Spektrogramms (sizeWaterfall) an das
      Karten-Layout aus T-1402 angepasst: SIDE_PX 983 -> 1035 (Kartenpolster und
      groesseres Body-Padding). Befund: die rechte Spalte (Spektrum/Pegelverlauf)
      wurde aus dem Fenster geschoben. (implementiert 2026-08-31)
- [x] T-1409 Kosmetik-Nachtrag aus der Gesamtvalidierung (2026-08-31, acht Befunde):
      :8000 Zeit-Gutter-Versatz als Innenabstand (Canvas-Spalte als geschlossenes
      Rechteck, knownStripResized rechnet mit paddingTop); Mithoeren-Knopf dunkleres
      Gruen mit dunkler Schrift; Markerliste mit durchgehender Kopfleiste und Titel
      "Markerliste (n)"; Ansichten-Menue und Auto-Marker-Popover in Panelgrau;
      Dezimierung "jeder Sweep" -> "keine". :8040 Diagrammflaechen durchgehend dunkel
      (Gutter kuerzer als der Stapel). :8010 Kachelrahmen in Knopf-Blau. :8050
      gewaehlte Kanaele kraeftiger hinterlegt (neues Token --success-soft-strong).
      (implementiert 2026-08-31)
- [x] T-1410 (2026-08-31, Commit 94ce5de, Sichtpruefung ok) Kleinigkeiten aus der Sichtpruefung T-1407b: (1) :8000 Aufnahmestatus
      ZUSAETZLICH als Zeile unter dem Session-Knopf (#rec-status); die Pille in der
      Kopfleiste (#rec-badge) bleibt, weil sie auch bei eingeklapptem Panel sichtbar
      ist - updateRecBadge speist beide. (2) :8000 Long-Run-Banner als Karte mit
      rotem Abzeichen "Long Run" links und Stopp-Knopf rechts statt Fliesstext in
      Warnfarbe; Sichtbarkeit ueber die Klasse .on statt style.display; Trenner "·"
      und Aufnahme-Glyph als SVG (U+23FA war das letzte Emoji-Zeichen auf :8000).
      (3) :8050 Kopf-Links sprechend und mit Pfeil: "<- D-SCAN Wasserfall",
      "<- Spektrum Auswertung" (Muster der Status-Seite). (implementiert 2026-08-31)
- [x] T-1411 (2026-08-31, Commit 853c6c3, Hardware-validiert) Fehler aus der Sichtpruefung T-1410: bei aktivem BW-ZOOM zeigten der
      gebrannte Achsstreifen und die Parameter-Tafel im D-SCAN-Wasserfall weiter den
      Bereich der Eingabefelder (88-108 MHz), waehrend Geraet, Achse ueber dem
      Wasserfall und Daten den schmalen Zoom-Bereich hatten (97.287-98.713 MHz).
      Ursache: composeParameterAnnotation las dscanStart/dscanStop, und pendingHeader
      bekam beim Klick segmentStartHz/StopHz - den neuen Bereich rechnet aber das
      Geraet (FREQ:DSC:RES:AUTO), er ist erst mit dem naechsten fertigen Sweep
      bekannt. Behebung: Bereich als Argument von composeParameterAnnotation
      (Vorgabe = laufendes Segment), neues Kennzeichen rangeFromFrame fuer BW-ZOOM
      und BW-Wechsel bei aktivem Zoom, Uebernahme von Achsstreifen und Bereichszeile
      aus frame.start_hz/stop_hz beim Brennen. (implementiert 2026-08-31)
- [x] T-1408 (2026-08-31) Abschluss Meilenstein 14. Bilanz der Farbwerte nach der
      Umstellung (Zeilen mit Hex-Wert, Stand 853c6c3):
        manage_jobs.py (:8050)        0
        block_admin.py (:8010)        0
        analyze_mscan.py (:8060)      7   Zeitverlauf-Matrix (Canvas)
        analyze_audio.py (:8070)     13   6 Klassen-Abzeichen + 7 Canvas/Marker
        sdr_waterfall.py (:8030)     22   14 KNOWN_STRIP_BLOCK + 8 Canvas
        analyze_sessions.py (:8020)  22   14 KNOWN_STRIP_BLOCK + 8 Canvas
        analyze_spectrum.py (:8040)  32   14 KNOWN_STRIP_BLOCK + 18 Canvas
        waterfall.py (:8000)         60   14 KNOWN_STRIP_BLOCK + 46 Canvas,
                                          Overlay-/Kopfleisten- und S-Meter-Farben
      Summe 156 Zeilen, davon 56 im KNOWN_STRIP_BLOCK (sync-gebunden, wortgleich in
      vier Servern) und 100 im Zeichencode bzw. in bewusst festen Farben. Kein CSS
      ausserhalb von ui_theme.py traegt noch einen Farbwert, der dem Theme folgen
      muesste - Ziel erreicht. Pruefbefehl fuer spaeter:
        Get-ChildItem waterfall.py, sdr_waterfall.py, manage_jobs.py, analyze_mscan.py,
          analyze_spectrum.py, analyze_sessions.py, analyze_audio.py,
          eb200_core/block_admin.py |
          ForEach-Object { "$($_.Name): " + (Select-String -Path $_ -CaseSensitive `
            -Pattern '(?<!&)#[0-9a-fA-F]{3,6}\b').Count }
- [-] T-1412 Handbuch-Screenshots in Light neu erstellen (Meilenstein 6). Alle Bilder
      der Kapitel 1-18 stammen aus dem Dark Theme; sie werden mit dem Standard-Theme
      neu aufgenommen. Erst danach die Lokalisierung angehen, damit die Bilder nicht
      zweimal anfallen.
      ENTFAELLT 2026-09-20 (T-1308). Das deutsche Handbuch ist eingefroren, neue
      Screenshots dafuer waeren verlorene Arbeit. Das englische Handbuch in
      Meilenstein 19 entsteht von Beginn an im Light-Theme, das seit Meilenstein 14
      Standard ist.
- [x] T-1413 (2026-09-05, Commit 41b61ba, browser-validiert) Tooltip-Umbruch suite-weit. Neue Regel in BASE_CSS
      (eb200_core/ui_theme.py): "#tooltip, .hover-tip { max-width:92ch;
      white-space:pre-line; overflow-wrap:break-word; line-height:1.35; }".
      Grenze in ch statt px, damit sie in Zeichen gilt (Vorgabe OE3GAS:
      80-100). Die seitenspezifischen Regeln in waterfall.py,
      analyze_sessions.py, analyze_spectrum.py und sdr_waterfall.py geben
      white-space und max-width ab; .hover-tip verliert white-space:nowrap
      (Ursache des Einzeilers in :8020/:8040, wo der Katalogtext mit
      FNV-Zeile an den Pegeltext angehaengt wird). Neuer Host-Helfer
      placeTip() in :8000, :8020 und :8040 klemmt den jetzt mehrzeiligen
      Tooltip an den sichtbaren Bereich. Unveraendert: #timelineTooltip
      (:8060, 260 px) und #knownTip (Rasterlayout im KNOWN_STRIP_BLOCK,
      560 px) - beide brechen bereits unterhalb der Grenze um; native
      title-Attribute rendert der Browser und sind nicht formatierbar.
      Browser-validiert 2026-09-05: der lange FNV-Text (DME /
      Rundfunkhilfsdienste / JTIDS-MIDS) bei 1090 MHz bricht in :8020 und
      :8040 um, die Box springt am rechten Rand nach links; :8000 bleibt
      einzeilig; #knownTip und #timelineTooltip unveraendert; Hell und
      Dunkel geprueft.
      Nebenbefund fuer kuenftige Prompts: "python -m ast <datei>" ist unter
      Windows als Gate unbrauchbar - das Modul dumpt den Baum und scheitert
      an cp1252 (Exit 1 ohne Syntaxfehler). Ersatz:
        python -c "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(), f) for f in sys.argv[1:]]" <dateien>

## Meilenstein 16: Aufbereitungs-Worker — abgeleitete Artefakte im Hintergrund

Abgeleitete Artefakte entstehen heute traege beim ersten Zugriff: .audspec beim
Oeffnen von :8070 (bei der langen IQ-Aufnahme 361 s gemessen), .klasse.json
synchron per POST /api/classify, .befund.json als Subprozess beim Oeffnen von
:8040. Der Worker nimmt diese Arbeit vorweg. Er bereitet AUSSCHLIESSLICH die
vier suite-eigenen Artefakte auf (.audspec, .klasse.json bei mono,
.befund.json bei .specbin, kuenftig .iqbefund.json). Exporte zu fremden
Werkzeugen (cs16/cf32/SigMF/HDSDR aus tools/wav2sdr.py) bleiben AUSDRUECKLICH
manuell und laufen nie automatisch - Entscheidung 2026-09-07 (OE3GAS): sie
verdoppeln den Platzbedarf und sind nur auf ausdruecklichen Abruf sinnvoll.

Trigger ist eine Abgleichschleife, keine Zuspielung aus den Aufnahmeservern:
eine verlorene Nachricht (Worker-Neustart, per tools/iq_import.py eingelegte
Datei, geloeschter Cache) holt der naechste Durchlauf von selbst ein, und der
Aufnahmepfad bleibt unberuehrt.

- [ ] T-1601 Sperr- und Fortschrittsdatei "<wav>.audspec.lock" in
      audio_spectrum.py; loest die prozesslokale Registry aus T-1213 ab.
      O_CREAT|O_EXCL als prozessuebergreifende Sperre, zugleich
      Fortschrittsdatei (Heartbeat je Block). Verwaiste Sperren ueber das
      Alter des Heartbeats, nicht ueber PID-Pruefung (Windows/Linux
      verschieden, Meilenstein 15 steht an). Neu AudioSpectrumBuildingError
      mit .snapshot; in analyze_audio.py 409 mit code=building, die
      bestehende Busy-Lage bekommt code=recording zur Unterscheidung.
      build_audspec() bleibt unangetastet - _BuildLock.heartbeat() passt auf
      die progress_cb-Signatur aus T-1212.
- [~] T-1602 (implementiert 2026-09-07; Messwerte PC 2026-09-07: IQ 216 950 201
      Frames in 145,8 s = 1,49 Mio Frames/s bei 276 MiB Spitze, mono 2 410 116
      Frames in 1,01 s = 2,40 Mio Frames/s bei 31 MiB Spitze, Startaufwand
      0,30 s. Planungswerte liegen rund 20 % darunter. Pi-Werte offen,
      Meilenstein 15.)
      eb200_core/prepare_budget.py: Groessen- und Laufzeitvorhersage je
      Artefakttyp, Platzpruefung je Volume, Aufnahmereserve. Die
      Artefaktgroesse ist nach oben beschraenkt und vorab exakt berechenbar
      (MAX_ROWS je Sektion): IQ 8000 * (128+256+512+1024+2048) = 31,7 MB,
      mono 8000 * (65+129+257+513+1025) = 15,9 MB - unabhaengig von der
      Aufnahmelaenge. Schwellen (Entscheidung 2026-09-07, OE3GAS): Sockel =
      groesser aus 5 GB und 3 % des Volumes, wird nie angetastet; Bedarf =
      vorhergesagte Groesse x 2 (Temp-Datei) plus Zuschlag; Aufnahmereserve =
      was eine laufende oder geplante Aufnahme noch schreibt, bei offenem
      Ende Horizont 2 h zur aktuellen Rate. Laufzeitvorhersage aus dem
      gemessenen Durchsatz (IQ: 216 950 201 Frames in 361,4 s, rund 600 000
      Frames/s; mono noch zu messen) - keine Arbeit anfangen, die vor der
      naechsten geplanten Aufnahme nicht fertig wird.
- [~] T-1603 (implementiert 2026-09-07; Entscheidungen 2026-09-07, OE3GAS:
      Name preparation_worker.py, Port 8090, KEINE eigene Weboberflaeche -
      nur /api/status, angezeigt im Reiter "Aufbereitung" von :8050, das
      serverseitig proxyt wie /api/livestatus den Controller auf :8000.
      Arbeitsvorrat direkt aus sandbox_roots + Sidecar statt ueber
      recording_index.list_recordings(): das oeffnet je Aufruf
      Session-DBs und Job-Manifeste und liefert channels/frames_written
      gar nicht mit. Journal logs/preparation_worker.jsonl, eine JSON-Zeile
      je Zustandsuebergang, Rotation bei 2 MB nach .1, kein logging-Modul.
      Fehlversuche nur im Speicher, hoechstens 3 je Datei. Hoechstens EIN
      Bau gleichzeitig - Speicherspitze knapp 300 MB. Zusaetzlich in
      audio_spectrum.py: audspec_header() aus load_audspec() herausgeloest,
      damit die Minutenpruefung nicht 31 MB Matrizen einliest.)
      Worker-Server: Abgleichschleife ueber
      recording_index.list_recordings(), Warteschlange, Baulauf,
      Budgetpruefung, Pause solange eine Aufnahme laeuft (UDP-Verlust waere
      teurer als eine spaete Auswertung), Zustaende zurueckgestellt (mit
      Grund) gegen fehlgeschlagen, Aufraeumen verwaister .tmp- und
      Sperrdateien beim Start, /api/status. Name und Port offen:
      prepare_worker.py, Port 8080.
- [~] T-1604 (implementiert 2026-09-08) Worker-Nachbesserungen aus dem ersten
      Trockenlauf von T-1603 (c88a35d): (a) Worker.__init__ bekommt
      journal_path - vorher schrieb JEDER --selftest-Lauf erfundene Eintraege
      in das produktive logs/preparation_worker.jsonl, weil _record()/status()/
      run_forever() auf die Modulkonstante zugriffen; (b) Zeitbudget je
      Durchlauf (CYCLE_BUDGET_S = 60 s) statt "ein Bau je Zyklus": der
      Trockenlauf fand 57 offene Auftraege, einer davon kostete 0,14 s - im
      Minutentakt haette der Rueckstand knapp eine Stunde gebraucht. Vor jedem
      Bau wird neu bewertet, die Terminschranke schrumpft mit; (c) WAV-Kopf
      als Ersatzquelle (_meta_from_wav), wenn das Sidecar fehlt oder
      unvollstaendig ist - betraf TETRA_393438630Hz_IQ.wav, ueber
      tools/iq_import.py eingelegt. CODE_NO_SIDECAR heisst jetzt CODE_NO_META
      und greift nur noch, wenn auch der WAV-Kopf nichts hergibt; neues Feld
      meta_source im Journal.
- [~] T-1610 (implementiert 2026-09-09) Platzlage in /api/status und als
      gedrosselte Warnung. Vorbereitung fuer T-1609: der Worker erhob die Lage
      bisher nur nebenbei in check_budget(), also NUR wenn zufaellig ein
      Auftrag anstand - gewarnt haette sich erst, wenn schon zurueckgestellt
      wird. Neu prepare_budget.space_status() (rein rechnend) und
      disk_report() (echtes Volume, unerreichbar -> space_low), Codes
      space_ok/space_tight/space_low mit SPACE_TIGHT_FACTOR = 5.0.
      available_bytes = frei - Sockel - Aufnahmereserve und DARF negativ
      werden, damit ein angeknabberter Sockel sichtbar bleibt. Der Worker
      erhebt die Lage in jedem Zyklus (summary["space"], status()["space"],
      auch ohne gelaufenen Zyklus) und meldet sie ueber _announce_space()
      gedrosselt: Codewechsel sofort, unveraenderte Lage erst nach
      SPACE_WARN_REPEAT_S = 3600 s, space_ok nur beim Wechsel. Auf E:
      (954 GiB, Sockel 28,6 GiB) beginnt "eng" unter 143 GiB verfuegbar.
- [~] T-1609 (implementiert 2026-09-09) Reiter "Aufbereitung" in :8050
      (frueher T-1604). Proxy /api/prepstatus nach dem Muster
      /api/livestatus (cross-origin, kein CORS in der Suite), nicht
      erreichbarer Worker -> reachable:false statt veralteter Anzeige.
      Platzbalken dreigeteilt (belegt / Sockel / verfuegbar), Warnabzeichen
      am Reiterknopf sobald die Lage nicht space_ok ist - dort sieht man sie
      ohne den Reiter zu oeffnen. Aktueller Bau mit Prozentwert aus der
      Sperrdatei (T-1601). Im Verlauf werden running/done zu EINER Zeile je
      Datei zusammengefasst, mit geschaetzter UND tatsaechlicher Dauer
      nebeneinander - HackRF-Dateien brauchen systematisch laenger als
      veranschlagt (5,3 s gegen 6,8 s). Gepollt wird nur, solange der Reiter
      sichtbar ist. Statusanzeige des
      Workers, serverseitig geproxyt wie /api/livestatus den Controller auf
      :8000; Warteschlange, zurueckgestellte Auftraege mit Grund, Fehlerliste,
      Platzwarnung, letzte Journalzeilen. Meldung ueber Aktivitaet dort, wo
      auch laufende und geplante Jobs stehen.
- [~] T-1605 (implementiert 2026-09-08) start_eb200.py: Eintrag in SERVERS
      (preparation_worker, Port 8090, optional=False), Moduldoku um den
      Dienst und den erweiterten Portbereich 8000-8090 ergaenzt. Mehr war
      nicht noetig: Statusanzeige, Port-Check, Neustart per [r] und die
      Lebenszeit-Kopplung ueber das Windows-Job-Objekt arbeiten alle ueber
      die SERVERS-Liste, ohne festverdrahtete Zahlen. Der Port-Check ist ein
      reiner TCP-Verbindungstest und kommt mit einem Dienst ohne HTML
      zurecht; der Browser-Autostart betrifft nur :8000. Kein
      Navigationseintrag - der Worker hat keine Seite (wie :8060/:8070).
- [~] T-1606 (implementiert 2026-09-10) Befund aus der Validierung von
      T-1601: handleAudspecError() wertete den 409-Code NICHT aus und zeigte
      auch bei code=building die Meldung "Aufnahme laeuft noch" - eine
      Aussage ueber eine Aufnahme, die gar nicht laeuft. Seit T-1601 traegt
      409 zwei Lagen (recording / building). Neu: bei building kein Fehler,
      sondern waitForBackgroundBuild() - Balken mit dem Fortschritt aus der
      Sperrdatei, der bestehende Poll gegen /api/audspec_status uebernimmt
      das Warten, danach wird erneut geholt (WF_BUILD_RETRIES = 3 als
      Schranke). renderWfProgress(st, fremd) formuliert denselben
      Fortschritt zweifach, je nachdem ob dieser Betrachter selbst rechnet
      oder nur zusieht. :8070 zeigt "wird im Hintergrund aufbereitet" statt selbst zu
      bauen (wertet code=building und den Fortschritt aus T-1601 aus).
- [~] T-1607 (implementiert 2026-09-10) Artefaktregister ARTIFACTS im Worker:
      je Typ Aufnahmearten, Gueltigkeitspruefung, Planung und Bau; cycle()
      kennt keinen Typ mehr namentlich, ein neuer ist ein Eintrag plus zwei
      Funktionen. Fehlversuchszaehler haengt am PAAR (Pfad, Artefaktname) -
      eine kaputte Klassifikation reisst den Audspec nicht mit. Als zweiter
      Eintrag .klasse.json (NUR mono, audio_classify lehnt IQ ab); lief
      bisher synchron per POST /api/classify beim Oeffnen von :8070.
      Kostenmodell gemessen 2026-09-10 an 600 s mono: 0,07 s gegen 1,69 s
      fuer den Audspec, Faktor 25 - audio_classify tastet Fenster ab statt
      alles zu lesen; Planungswert 20 Mio Frames/s, Sidecar 16 kiB.
      Entscheidung 2026-09-10 (OE3GAS): der Worker arbeitet AUSSCHLIESSLICH
      mit WAV. .befund.json entstuende aus .specbin, wuerde die
      Bestandsaufnahme verdoppeln und bringt nur Sekunden je Session - er
      bleibt der traegen Erzeugung in :8040 ueberlassen.
- [~] T-1608 (implementiert 2026-09-10) tools/eye_diagram.py liest ein
      ZEITFENSTER statt der ganzen Datei: read_wav_iq(path, t0_s, t1_s) ueber
      wave.setpos(), MAX_WINDOW_S = 120 s, analyze_file()/analyze_file_psk()
      reichen durch, CLI --t0/--t1 (nur gemeinsam). Befund 2026-09-10: das
      Werkzeug las immer die ganze Datei nach complex128 - fuer
      20260907_094655_10149kHz_IQ.wav (217 Mio Frames) 868 MB Rohpuffer plus
      3,5 GB float64-Zwischenstufe plus 3,5 GB Ergebnis, Spitze ueber 8 GB.
      Dieselbe Fehlerklasse wie T-1212 im Audspec; sie fiel nie auf, weil das
      Werkzeug fuer kurze IFPAN-Zoom-Mitschnitte gebaut wurde. Zu grosses
      Fenster -> ValueError statt stiller Kuerzung; ueber das Dateiende hinaus
      wird geklemmt. Selbsttest T25 sichert die Kernzusage: Messung im Fenster
      = Messung an einem Zuschnitt.
      Nachtrag T-1608a (2026-09-15): --suggest ignorierte das Zeitfenster
      vollstaendig - main() rief run_suggest(args.wav) ohne t0/t1, und
      run_suggest las read_wav_iq(wav_path) ohne Fenster. Damit las
      ausgerechnet der Pfad, den man bei einer unbekannten langen Aufnahme
      ZUERST benutzt, weiter die ganze Datei (belegt an der 6779-s-Aufnahme
      20260907_094655_10149kHz_IQ.wav). run_suggest nimmt jetzt t0_s/t1_s
      entgegen und reicht sie durch; wie bei analyze_file() sieht der Rest
      der Uebersicht das Fenster als ganze Aufnahme, die Burstnummerierung
      zaehlt also im Ausschnitt. Die uebrigen Pfade (fsk, psk, ofdm,
      --audio) waren bereits konform. Selbsttest T38 laesst run_suggest
      zweimal ueber dieselbe Fixtur laufen (Rauschen, Burst, Rauschen),
      einmal ganz und einmal ueber die ersten 3,5 s: die beiden Ausgaben
      muessen sich unterscheiden, sonst wirkt das Fenster nicht. Bewusst
      NICHT geprueft wird, was die Burstsuche im Fenster findet - ein
      Rauschfenster wirft keinen Fehler, sondern gilt als Dauersignal
      (Befund aus dem ersten Anlauf von T38).
- [~] T-1611a (implementiert 2026-09-10) Serverseite der Signalvermessung in
      analyze_audio.py. tools/eye_diagram.py laeuft als UNTERPROZESS mit
      --json/--out, nicht als Import: tools/ ist ein Verzeichnis
      eigenstaendiger Offline-Werkzeuge, und kein Server importiert bisher
      daraus (Muster analyze_spectrum.ensure_befund fuer session_analyze.py).
      GET /api/measure?path=&t0=&t1=&mode= misst ein Zeitfenster (moeglich
      seit T-1608) und liefert Kennwerte plus Augendiagramm-SVG, OHNE zu
      speichern. POST /api/iqbefund merkt eine Messung - nur auf
      ausdruecklichen Knopfdruck (Entscheidung 2026-09-10, OE3GAS), sonst
      fuellte sich der Sidecar mit Fehlversuchen aus leeren Ausschnitten.
      GET /api/iqbefund liefert die Liste. "Kein auswertbares Signal" kommt
      mit HTTP 200 zurueck, nicht als Fehler - beim Suchen zoomt man staendig
      in Stellen ohne Signal; eye_diagram unterscheidet das ueber den
      Rueckgabecode (2 = nichts Auswertbares, 1 = ging schief). Sidecar ist
      eine LISTE mit Deckel IQBEFUND_MAX = 50, aeltester faellt heraus; das
      SVG wird nicht gespeichert, es ist jederzeit neu rechenbar.
      MEASURE_MAX_TRACES = 120 statt CLI-Vorgabe 400 - gemessen 195 kB statt
      630 kB SVG bei unveraendert aussagekraeftigem Bild.
- [~] T-1617 (implementiert 2026-09-10) Kontrastregler und Paletten im
      Wasserfall von :8070, rein zeichnerisch - dieselben uint8-Bytes, andere
      Zuordnung auf Farben, kein Servereingriff und kein Cache-Neubau.
      Anlass: die dB-Skala des Caches ist GLOBAL ueber die ganze Aufnahme
      gebildet (T-1212, 1-/99,9-Perzentil aller Rohwerte), weshalb ein
      starkes Signal als einfarbiger Balken erscheint - der Wertebereich
      wird voll genutzt, nur nicht dort wo es interessant ist.
      levelToColorRGB hatte lo/hi immer schon als Parameter, belegt wurden
      sie nur nie anders als 0/255. Neu zwei Regler, Knopf "Spreizen"
      (Anschlaege aus dem SICHTBAREN Ausschnitt ueber ein Histogramm, 2 %
      Randbeschnitt gegen Ausreisser), Ruecksetzknopf und drei Paletten
      (spektral wie bisher, grau fuer feine Kontraste, heiss mit gespreiztem
      oberen Bereich). lastSpec merkt den Zeichenstand, damit die Regler neu
      ZEICHNEN statt neu zu holen.
- [~] T-1618 (implementiert 2026-09-10) FFT_SIZES um 32 und 64 ergaenzt -
      audio_spectrum, prepare_budget und analyze_audio. Seit T-1208 verlangt
      load_audspec() genau die aktuelle Sektionsmenge, alle Bestandscaches
      werden dadurch neu gebaut (gewollt, vom Worker in einem Durchlauf
      erledigt). Ob die groben Groessen taugen, haengt an der ABTASTRATE:
      bei 32 kHz ergibt n_fft=32 eine Binbreite von 1 kHz, ueber 15 kHz
      ZF-Bandbreite also fuenfzehn Streifen; bei 200 kHz sind es 6,25 kHz Bin
      bei 160 us Zeilendauer, und ein TETRA-Symbol dauert bei 18 000 Bd rund
      56 us. Entscheidung 2026-09-10 (OE3GAS): die Auswahl wird in :8070
      GEKENNZEICHNET, nicht gesperrt (T-1619) - die Grenze ist weich, und bei
      importierten Dateien ohne bw_hz waere eine Sperre nicht anwendbar.
      Zwei Selbsttests nachgezogen: T1 prueft statt "keine Dezimierung" den
      Zusammenhang rows = ceil(rows_raw/decimation); die Spiegelprobe T8
      laeuft nur noch ueber Groessen mit hoechstens 300 Hz Binbreite, weil
      bei 1 kHz Traeger und Fensterflanke ineinanderlaufen. Cache-Obergrenzen
      IQ 30,3 -> 31,0 MiB, mono 15,2 -> 15,6 MiB.
- [x] T-1627 (implementiert 2026-09-12, validiert 2026-09-12) Zweispalten-Aufbau in :8070 und
      Empfangskette als Zustandsmarken. Ueber dem Spektrogramm stehen zwei
      gleich breite Bloecke: links die Metadatentafel, rechts die Bedienung;
      darunter Wiedergabeleiste und Geschwisterlinks, dann der Wasserfall in
      voller Breite. Unter 1100 px Fensterbreite untereinander. Die
      Werkzeugleiste ist dafuer auf fuenf Zeilen umgruppiert - die alte
      Sammelzeile aus FFT, Kontrast und Knoepfen braucht rund 1350 px und
      brach in der halben Breite dreifach um. Fallstrick des Umbaus: die
      Leiste liegt nicht mehr in #waterfall, also blenden die beiden
      Ausblendzweige in initWaterfall() sie eigens aus (nurMetadaten(), der
      linke Block nimmt dann die volle Breite). Die Empfangskette steht jetzt
      als Zustandsmarken statt einer Sammelzeile: blau = Automatik oder
      gesetzter Bezugswert, gruen = ein, orange = aus, grau = nicht ermittelt.
      Drei inhaltliche Festlegungen: ATT bekommt einen ZUSATZCHIP fuer die
      Automatik, weil att_state und att_auto am Geraet unabhaengig sind
      (Befund 2026-09-06, "aus + Auto" ist der Normalfall); ATT traegt KEINEN
      dB-Wert, weil Annex C nur ON|OFF|AUTO kennt und die Stufe nicht
      abfragbar ist; der AFC-Chip entfaellt bei IQ, weil das Geraet AFC dort
      annimmt, aber nicht regelt.
      Nachtrag T-1627a (vor dem Commit behoben): die Zusicherung aus T-1615
      ("grid-template-columns" nirgends in HTML_PAGE) meinte die
      Vermessungstafel, war aber auf die ganze Seite formuliert - und das
      neue #topGrid ist ein zweites, voellig anderes Gitter. Sie ist jetzt auf
      den Abschnitt der Tafel eingegrenzt (#measureBox bis #fftGroup), nicht
      aufgehoben. Merksatz fuer kuenftige Regressionssperren: den
      Geltungsbereich mitschreiben, sonst sperrt eine Zusicherung ueber Jahre
      hinweg Dinge, die es beim Schreiben noch gar nicht gab.
- [x] T-1626 (implementiert 2026-09-12, validiert 2026-09-12) Median-Pegel der Aufnahme. Die
      Empfangskette in :8070 zeigte bisher nur rx.level_dbuv - einen
      Momentanwert zum Aufnahmestart aus dem Schnappschuss T-917. Er heisst
      jetzt "Pegel (Start)" und bekommt "Pegel (Median)" an die Seite:
      audio_spectrum.level_median_dbfs() liest hoechstens 512 gleichmaessig
      ueber die Datei verteilte Bloecke zu 4096 Frames per setpos() und
      liefert den Median des Blockpegels. Die Laufzeit haengt damit an der
      Stichprobe, nicht an der Dateilaenge. Der Wert ist dBFS und wird es
      bleiben: das Geraet liefert im Audio-/IQ-Stream keinen Pegel mit (kein
      OptionalHeader, Befund 2026-09-06), eine gepruefte PCM-nach-dBµV-Bruecke
      gibt es nicht (T-919) und bei AGC kann es sie nicht geben. Aus dem
      Audspec-Cache waere der Median NICHT zu holen - dessen Dezimierung nimmt
      das Maximum je Gruppe und die Quantisierung klippt am Perzentil. Bei
      laufender Aufnahme wird nicht gerechnet.
      Nachtrag T-1626a (vor dem Commit behoben): der urspruenglich
      vorgesehene Rueckfall "unbrauchbares Format -> None" in
      level_median_dbfs() war toter Code - wav_kind() liefert keinen dritten
      Wert, es wirft AudioSpectrumUnsupportedError. Der Zweig ist entfernt,
      die Ausnahme wird jetzt in build_meta() abgefangen (dort, wo ohnehin
      alles auf None abgebildet wird). Das ist der praktisch relevante Fall:
      eine 3-Kanal-WAV hat einen lesbaren Kopf, kommt also bis zum Median
      durch, und ohne das Abfangen fiele /api/meta fuer sie ganz aus.
- [x] T-1625 (implementiert 2026-09-12, validiert 2026-09-12) Kopfleiste, Art-Abzeichen und
      Beschriftung in :8070. Die Ansicht heisst jetzt "Signalanalyse" (fester
      Ansichtsname in der Kopfleiste, auch in <title> und der
      Programmbeschreibung); der Untertitel traegt den Kurztext der Aufnahme,
      der Dateiname nur noch als Rueckfall - er steht ohnehin in der
      Metadatentafel. Dafuer bekam ui_theme.header_html() den optionalen
      Parameter sub_id: ist er gesetzt, wird das Untertitelfeld AUCH bei
      leerem sub angelegt, damit der Server es per JS nachtragen kann (rein
      additiv, alle bisherigen Aufrufe unveraendert). Die Zeile "Datei" traegt
      neu ein Abzeichen der Aufnahmeart - IQ, AUDIO oder UNBEKANNT, dieselben
      Klassen wie in der Aufzeichnungsliste (:8050). Es wird aus der
      Kanalzahl der Datei abgeleitet, nicht aus sidecar.mode, der bei
      importierten Aufnahmen fehlen kann. "Aufgenommen" heisst "Aufnahme vom".
- [~] T-1624 (implementiert 2026-09-11) Befund aus der Erprobung von T-1623:
      "Anhoeren" erzeugte die WAV und wies sie dem Abspieler zu, aber die
      Wiedergabeleiste ist bei IQ ausgeblendet (T-1211, damit der Browser die
      Rohdatei nicht laedt) - es war nichts zu hoeren, der Knopf schien
      wirkungslos. Der Selbsttest aus T-1623 fand das nicht, weil er nur
      prueft DASS el('player') angesprochen wird; bei einem Element das
      ausgeblendet sein kann reicht das nicht. Neu wird die Leiste in
      doAudio() sichtbar gemacht, und der Selbsttest verlangt das
      ausdruecklich - vor dem Zuweisen der Quelle. Dazu zwei Nachbesserungen:
      (a) kein Zoomzwang mehr fuers Hoeren, solange die Aufnahme unter 120 s
      liegt - dann wird sie ganz abgespielt; die Zoompflicht stammt vom
      Vermessen, wo eye_diagram einen Burst im Fenster sucht. (b) Das
      Kontrollkaestchen "auf Marker abstimmen" zieht aus der FFT-Zeile in die
      Markerzeile um, wo es hingehoert.
- [~] T-1623 (implementiert 2026-09-11) IQ-Ausschnitt anhoeren. iq_to_audio()
      macht, was ein Empfaenger im SSB-Betrieb macht: ins Basisband schieben,
      tiefpassfiltern, Realteil nehmen - nur landet die Signalmitte nicht auf
      0 Hz, sondern auf AUDIO_SIDETONE_HZ = 700. Ein Traeger wird damit zum
      hoerbaren Ton statt zu Gleichspannung. Entscheidung 2026-09-11
      (OE3GAS): CW als EINZIGE Betriebsart - Sprache wird nicht in IQ
      aufgenommen, und der Mithoerton passt zugleich fuer RTTY und PSK31
      (170 Hz Hub liegen dann bei 615 und 785 Hz, die vertrauten Toene).
      Zielrate 8 kHz, Obergrenze 120 s wie beim Zeitfenster. Die WAV entsteht
      FLUECHTIG (Entscheidung 2026-09-11, OE3GAS): Temp-Verzeichnis,
      Antwortkoerper, danach weg - das erspart Ablage, Verfallszeit,
      Aufraeumen im Worker und Platzbedarf in prepare_budget, bei
      Zehntelsekunden Rechenzeit je Ausschnitt. Route GET /api/audio, CLI
      --audio ZIEL.WAV. Knopf "Anhoeren" neben "Signal vermessen", an
      dieselbe Bedingung gebunden (Zoom UND IQ) und mit demselben
      Kontrollkaestchen abgestimmt. Bausteine kamen aus T-1608 (Zeitfenster)
      und T-1620 (Abstimmung, Dezimationsvorschlag).
- [~] T-1621 (implementiert 2026-09-11) Wertetafel der Signalvermessung
      kompakt und mit Erlaeuterungen, rein zeichnerisch. Die Tabelle zog
      ueber die volle Blockbreite, wodurch zwischen Bezeichnung und Wert eine
      Handbreit Leere klaffte - jetzt ein zentrierter Block von 300 bis
      420 px. Je Feld ein zweizeiliger Tooltip (hoechstens 80 Zeichen je
      Zeile), der nicht nur sagt was der Wert ist, sondern ab wann er
      zweifelhaft wird: r2 unter 0,9, Symbole unter etwa 30, Augenoeffnung
      ueber 50 % gut / nahe 0 unbrauchbar, EVM unter 20 % sauber / ueber
      50 % kaum lesbar. Genau die Grenzen, an denen die RTTY-Messung vom
      2026-09-10 scheiterte (drei Symbole, Augenoeffnung 0,0 %, zehn Pegel
      statt zwei). Umsetzung ueber Template-Literale mit ECHTEN Umbruechen
      und String.fromCharCode(10) in mTip() - kein einziger Backslash im
      Block, weil '\n' und '\u000a' in der nicht-rohen Python-Zeichenkette
      beide beim Schreiben aufgeloest werden und die JS-Zeichenkette
      sprengen (dreimal passiert beim Bau).
- [~] T-1622 (implementiert 2026-09-11) Tasten von Fading unterschieden.
      _luecken_sind_leer() entscheidet ueber die MOMENTANFREQUENZ in den
      Luecken zwischen den Bursts: bei einer echten Tastpause steht dort nur
      Rauschen, bei einem gefadeten Dauersignal weiter Modulation. Der
      Pegelvergleich aus dem ersten Entwurf wurde verworfen, weil er sich mit
      dem Stoerabstand beisst (bei 10 dB SNR liegt der Rauschboden eines echt
      getasteten Signals bei 10 % der Burstleistung). Kalibriert 2026-09-11
      ueber zwei Seeds, drei Fadingtiefen (0,5/0,75/0,9) und die
      Stoerabstaende 20/10/6 dB: gefadetes Dauersignal 0,745..0,950, echt
      getastet 0,988..0,996; BURST_GAP_FLATNESS = 0,97 liegt mittig.
      Dokumentierte Grenze: unter etwa 6 dB Stoerabstand traegt die
      Unterscheidung nicht mehr (bei 3 dB erreicht auch das Fading 0,974) -
      dort ist das Signal aber ohnehin zu schwach fuer eine Symbolanalyse.
      Selbsttest T29 faehrt die Matrix mit 32 Pruefungen ab. Anlass war
      20260910_224408_11038kHz_IQ.wav: drei Symbole statt der ganzen
      Aufnahme, und daraus zehn Pegel und Augenoeffnung 0,0 %.
- [~] T-1620 (implementiert 2026-09-10) Abstimmung auf die Marker vor der
      Vermessung. Befund 2026-09-10 an 20260910_224408_11038kHz_IQ.wav
      (RTTY, 32 kHz, Traeger +293 Hz, Hub 189 Hz): der FSK-Pfad fand nichts,
      weil der Tiefpass in apply_decimation() um 0 Hz sitzt. Ohne
      Dezimierung lagen 32 kHz Rauschbandbreite auf 190 Hz Signal (Flatness
      0,982 gegen Schwelle 0,94 - als Rauschen eingestuft), mit --decimate 64
      fiel die Mark-Frequenz aus dem Durchlass. Solange das Signal nicht in
      der Bandmitte liegt, kann man nicht stark genug dezimieren. Neu
      tune_to_baseband() und suggest_decimation() (halbe Bandbreite in
      0,2*fs_eff, 50 % Reserve, Zweierpotenz, nie unter 4 Samples je Symbol;
      200 Hz Bandbreite bei 32 kHz -> N=32), CLI --tune und
      --auto-decimate BW. Die Ableitungsregel steht im WERKZEUG, nicht im
      Server - der importiert eye_diagram bewusst nicht. In :8070 ein
      Kontrollkaestchen "auf Marker abstimmen" (Entscheidung 2026-09-10,
      OE3GAS), nur mit zwei Markern anwaehlbar; deren Mitte ist die
      Abstimmfrequenz, ihr Abstand die Bandbreite. Kein Automatismus - die
      Marker dienen auch dem blossen Messen. Die Ueberschrift der Tafel
      vermerkt die Abstimmung. Selbsttest T27 belegt es: dasselbe
      RTTY-artige Signal bei 6 dB Stoerabstand hat roh Flatness 0,930 und
      abgestimmt 0,579.
- [~] T-1619 (implementiert 2026-09-10) FFT-Auswahl in :8070 gekennzeichnet,
      rein zeichnerisch - bw_hz steht im Sidecar, das ohnehin komplett im
      Frontend liegt (meta.sidecar), kein Servereingriff. Neben der Auswahl
      steht die Aufloesung der GERADE gezeichneten Darstellung: Binbreite aus
      sampleRateHz/n_fft, Zeilendauer aus dt_row_s des Headers - also
      INKLUSIVE Dezimierung, bei der langen IQ-Aufnahme 0,848 s statt der
      theoretischen 32 ms, mit Vermerk "47x verdichtet". Zu grobe Groessen
      (weniger als FFT_MIN_BINS_IM_BAND = 20 Bins ueber der belegten
      Bandbreite) werden GEDAEMPFT, nicht gesperrt (Entscheidung 2026-09-10,
      OE3GAS): die Grenze ist eine Faustregel, und bei einem schmalen Traeger
      mitten im Band kann die grobe FFT mit feiner Zeitaufloesung genau das
      richtige Werkzeug sein. Der Titel begruendet es. Fehlt bw_hz
      (importierte Aufnahmen), entfaellt die Daempfung vollstaendig - eine
      Regel die manchmal greift waere verwirrender als keine.
- [~] T-1616 (implementiert 2026-09-10) Vertikaler Zoom auf das Markerband,
      Falschfarben-Paletten und neu geordnete Werkzeugleiste in :8070 - alles
      rein zeichnerisch. Der Zoom setzt geom.fMin/fMax und den
      Quellausschnitt beim drawImage; Achsenbeschriftung und Markerlinien
      folgen von selbst, weil beide ueber dieselbe Geometrie rechnen. Knopf
      nur mit zwei Markern aktiv (die dienen auch der Differenzmessung) und
      beschriftet sich im Zoomzustand zu "Ganzes Band" um. Marker B jetzt
      weiss und gestrichelt - violett war auf dem dunklen Wasserfall schlecht
      lesbar, gestrichelt bleibt er von A unterscheidbar. Drei
      Falschfarben-Paletten neben den drei Helligkeitsrampen: regenbogen
      (voller Farbtonumlauf), stufen (12 Klassen, harte Kanten wie Isolinien)
      und saegezahn (Rampe laeuft 4x, 16 Pegelstufen durchlaufen die volle
      Skala). PALETTEN tragen dafuer optional wiederholungen und stufen.
      Werkzeugleiste: Zeile 1 Horizontaler Zoom, Zeile 2 Marker mit
      Vertikalem Zoom, Zeile 3 FFT/Kontrast/Vermessen; "Spreizen" heisst
      jetzt "Kontrast spreizen", weil mit zwei Zoomarten mehrdeutig.
      Die Aufloesung wird durch den Zoom NICHT besser (bei n_fft=2048 und
      32 kHz ist ein Bin 15,6 Hz breit) - dieselben Bins auf mehr Bildhoehe.
- [~] T-1615 (implementiert 2026-09-10) Anordnung der Vermessungstafel in
      :8070, reine Darstellung. Je Pfad EIN gerahmter Block, beide
      nebeneinander, darin von oben nach unten Ueberschrift, Grafik, Werte,
      Merken-Knopf. Vorher lagen Wertetabellen und Grafiken in getrennten
      Behaeltern - bei einem Doppeltreffer lief die Zuordnung nur ueber die
      Reihenfolge. Werte als echte Tabelle mit Zebrastreifen statt CSS-Gitter
      (neun Zeilen mit weit auseinanderliegenden Spalten). Beide Grafiken auf
      480 px Hoehe skaliert: Auge 760x480, Konstellation samt Histogramm
      500x660 - ohne Angleichung stehen sie unterschiedlich hoch. Beide
      tragen eine viewBox, daher genuegen height und width:auto. Der Hinweis
      "r2 und EVM sind nicht vergleichbar" steht ueber den Bloecken, wo er
      gelesen wird, bevor man die Zahlen ansieht. flex-wrap laesst die
      Bloecke auf schmalen Fenstern untereinander rutschen.
- [~] T-1614 (implementiert 2026-09-10) Zwei Befunde aus der Erprobung von
      T-1611b. (a) write_svg_psk zeichnete einen <circle> JE SYMBOL ohne
      Grenze - 360 001 Symbole ergaben 27 MB SVG, das in :8070 als ebenso
      viele DOM-Knoten in der Seite gelandet waere; MEASURE_MAX_TRACES deckelt
      nur den FSK-Pfad. Neu MAX_CONST_POINTS = 2000, gleichmaessig ueber die
      Symbolfolge ausgewaehlt (repraesentativ, nicht nur der Burstanfang),
      Bildunterschrift nennt die Auswahl. CLI --max-points, 0 = alle.
      (b) Bei Doppeltreffer zeigte die Tafel stillschweigend FSK, weil
      MEASURE_MODES so sortiert ist. Befund an TETRA: FSK meldete 29 861 Bd
      bei r2=0,98, PSK 18 000 Bd bei M=4 - und 18 000 Bd ist der
      Spezifikationswert fuer pi/4-DQPSK; der FSK-Wert ist ein Artefakt der
      Momentanfrequenz einer QPSK-Aussendung. Eine Vorrangregel nach r2 oder
      EVM haette das Falsche gewaehlt, also gibt es KEINE: beide Bilder stehen
      nebeneinander, jedes mit eigenem Merken-Knopf.
- [~] T-1611b (implementiert 2026-09-10) Oberflaeche der Signalvermessung in
      :8070. EIN Knopf "Signal vermessen" statt einer Modusauswahl: der
      Server rechnet bei mode=auto BEIDE Pfade und meldet, welcher durchkam.
      Grundlage ist ein Scan ueber sieben eigene Aufnahmen (2026-09-10) - in
      sechs Faellen war genau ein Pfad erfolgreich und der andere warf.
      Bewusst KEINE Wahrscheinlichkeit: r2 (FSK-Symbolratenfit) und evm_pct
      (PSK-Konstellation) messen Verschiedenes, eine Prozentzahl daraus waere
      erfunden. Kommen beide durch, werden beide gezeigt mit dem Hinweis,
      dass die Guetemasse nicht vergleichbar sind - entschieden wird am Bild.
      Knopf nur aktiv bei Zoom UND IQ. Der gescheiterte Pfad erscheint mit
      seiner Meldung, das ist selbst eine Auskunft. Bei leerem Ausschnitt ein
      ruhiger Hinweis samt Zusatz, dass mehrtraegrige Verfahren (OFDM) nicht
      erkannt werden - beide HFDL-Aufnahmen scheitern aus genau diesem Grund
      in beiden Pfaden. "Befund merken" ist getrennt vom Messen, mit
      Notizfeld; darunter die Liste der gemerkten Messungen.
- [~] T-1613 (implementiert 2026-09-11) OFDM-Hinweis. Kein Auswertepfad,
      sondern eine Zusatzauskunft - er wird im auto-Lauf NUR angehaengt, wenn
      FSK und PSK beide gescheitert sind. Die Autokorrelation auf das
      zyklische Praefix ist NOTWENDIGE Bedingung: ohne Gipfel kein Hinweis,
      egal was die anderen Merkmale sagen - Rauschen ist im Zeitbereich
      ebenfalls gaussisch, und ein Kamm laesst sich in verrauschte Daten
      immer hineininterpretieren. Der Traegerabstand dient als RUECKFALL,
      nicht als Vorrang: reisst das globale Maximum die Schwelle nicht, wird
      ein vom Kamm benannter Kandidat geprueft. Die umgekehrte Reihenfolge
      war ein frueherer Entwurf und ging schief, sobald der Kamm danebenlag
      (bei 200 Hz Traegerabstand lieferte er 23 Hz: 1 von 6 Treffern statt
      6 von 6). Gemessen 2026-09-11 ueber sechs Seeds je Fall bei 25 dB
      Stoerabstand: 45/100/200/400 Hz je 6/6 mit unter 1 % Fehler der
      Symboldauer, 62,5 Hz mit cp=1/8 nur 1/6 - bei kurzem Schutzintervall
      traegt der Praefix ein Neuntel der Energie, und ein Rest der
      Eigenkorrelation ueberragt den Gipfel. Fuer einen Hinweis hinnehmbar:
      er bleibt aus statt falsch zu sein. Fehlalarme 0/6 auf Rauschen und
      0/6 auf Dauertraeger; ein FSK2-Signal bei 48 kHz loest ihn dagegen aus
      und meldet 0,98 ms statt der Symboldauer von 10 ms - festgehalten im
      Selbsttest T30 statt wegdefiniert, hinnehmbar NUR weil der Hinweis bei
      einem auswertbaren FSK-Signal nie zum Zug kommt. Eigene Tafel in :8070
      mit sechs Feldern, Kopf "Moeglicherweise OFDM" statt "ausgewertet".
      Volle OFDM-Analyse bleibt bei iqscan (Entscheidung 2026-09-10, OE3GAS).
- [ ] T-1612 Frequenzauswahl fuer die Vermessung ueber die Marker A/B in
      :8070 (Verschiebung ins Basisband plus Tiefpass vor der Analyse).
      Zurueckgestellt bis T-1611 in der Praxis zeigt, wie oft mehrere Signale
      gleichzeitig im 32-kHz-Band liegen. eye_diagram bestimmt den Offset
      bisher selbst ueber das GANZE Band.

## Meilenstein 17: Modenkennung in der Signalvermessung (:8070)

Leitsatz fuer den ganzen Meilenstein: **Die Anzeige behauptet nie mehr, als die
Messung hergibt.** "Kennwerte passen zu RTTY Amateur (170 Hz, 45,4 Bd)" statt
"RTTY erkannt". Mehrere Kandidaten nebeneinander sind ein gueltiges Ergebnis,
keiner auch. Kein Kandidat "gewinnt", es gibt keine Rangfolge und keine
Prozentangabe, die eine Wahrscheinlichkeit vortaeuscht.

Nicht-Ziele (gehoeren nach iqscan, nicht hierher): Dekodieren, Entwuerfeln, FEC,
Interleaving, Sync-Wort-Suche, Burst-Verfolgung ueber die Zeit. Also PACTOR,
ARQ-E/ARQ-M, MIL-STD-188-110, HFDL, STANAG, Link-11 sowie DMR/NXDN jenseits des
groben Vierpegel-Hinweises. Die Grenze ist sauber: MoniScope misst und benennt
Kandidaten, iqscan beweist sie durch Dekodieren.

### Stufe 1 -- sichere Kennung aus (Hub, Baud, Pegelzahl)

- [x] T-1701 (2026-09-12) Kennwerte-Erweiterung: Modulationsindex und
      Tonabstaende. Neue reine Hilfsfunktion tone_metrics(peak_freqs_hz,
      dev_hz, baud) in tools/eye_diagram.py liefert mod_index (2*dev_hz/baud,
      None bei baud<=0), tone_spacings_hz (Abstaende benachbarter Pegel-Peaks),
      tone_spacing_mean_hz und tone_spacing_cv (Variationskoeffizient). Reine
      Rechnung, keine Deutung, keine Schwelle, kein Modename im Code -
      Grundlage fuer T-1703. Ausgabe in print_report() und in der --json-
      Datei (build_json_dict()); analyze_audio.run_measure() fuehrt dort
      keine Whitelist, die vier Schluessel landen also unveraendert im
      kennwerte-Objekt von /api/measure (per Test bestaetigt, keine
      Codeaenderung an analyze_audio.py noetig). Selbsttest T31 (sechs
      Faelle a-g, siehe --selftest). Erprobt an
      records/20260912_202830_7646kHz_IQ.wav (--t0 10 --t1 30 --tune -75
      --auto-decimate 750 --mode fsk): mod_index=8.89, ein Tonabstand
      437,8 Hz, Streuung 0,0 % (FSK2, zwei Pegel) - erster realer
      Datenpunkt fuer die Tabelle in T-1702.
- [x] T-1702 (2026-09-12) Kandidatentabelle modes/fsk.json (format
      eb200-modes-fsk-v1, table_version 1), 12 Zeilen (RTTY x4, SITOR-B-
      Familie, POCSAG x3, FLEX x2, DMR, P25 C4FM). Felder je Zeile: id
      (ASCII-Slug, eingefroren), label, optional level_count, shift_hz
      (=2*dev_hz, Gesamthub, NICHT der Tonabstand), shift_tol_hz, baud,
      baud_tol_rel, tone_spacing_hz, spacing_cv_max, keying ("keyed"/
      "continuous", sprachneutral), note. defaults (shift_tol_hz=15 Hz,
      baud_tol_rel=6%, spacing_cv_max=0.05, near_factor=2.0) gelten je Zeile,
      die den Wert nicht selbst setzt - werden aber bewusst NICHT beim Laden
      aufgeloest, das macht erst der Vergleich in T-1703, sonst staenden zwei
      Wahrheiten im Speicher. Reine Daten, keine Vergleichslogik. Lader
      eb200_core/mode_table.py: load_fsk_table()/validate_fsk_table() nach
      dem Muster known_freq.py, wirft nie (fehlende/kaputte/ungueltige Datei
      -> (None, [Meldungen])). Selbsttest deckt reale Tabelle, elf
      Fehlerfaelle als Speicher-Fixtures und kaputte/fehlende Datei ab.
      Gegenprobe von Hand gegen die T-1701-Erprobung (437,8 Hz / 49,25 Bd):
      trifft genau rtty_kommerziell_425_50 (Rand 2,19 Hz / 4,50 Punkte) und
      rtty_kommerziell_450_50 (Rand 2,81 Hz / 4,50 Punkte), rtty_amateur_170
      verfehlt shift_hz bereits um 253 Hz - Ergebnis wie erwartet, keine
      Toleranz angepasst.
- [x] T-1701a (2026-09-12, Nachtrag) freq_bin_hz als Kennwert. Befund aus der
      T-1702-Gegenprobe: die Raender dort (2,19 / 2,81 Hz) waren kleiner als
      die Breite eines Histogramm-Bins (9,95 Hz bei fs_eff=4000) - dev_hz ist
      eine Differenz zweier bin-quantisierter Peaklagen, der
      Quantisierungsfehler betraegt also bis zu einem ganzen Bin. Neuer
      Schluessel in tools/eye_diagram.py: freq_bin_hz = (fs_eff/2)/
      N_ZERO_HIST_BINS, reine Rechnung aus bereits vorhandenen Groessen.
      Ausgabe in print_report() und im JSON-Schreibweg. Selbsttest T32: bei
      fs_eff=4000 ergibt sich 9,9502 Hz (Soll 9,95 auf 0,01 genau), Wert auch
      in der T1-Ende-zu-Ende-Fixtur vorhanden und positiv. Grundlage fuer die
      Toleranzaufweitung in T-1703.
- [x] T-1703 (2026-09-12) eb200_core/mode_match.py: reine Vergleichsfunktion
      match_fsk(kennwerte, tabelle) -> Liste von Befunden. Aufloesung der
      defaults aus T-1702 passiert HIER, je Zeile/Kriterium, nicht beim
      Laden. Auswertbarkeit je Kriterium: fehlt der zugehoerige Kennwert,
      wird die ganze Zeile uebersprungen (kein Fehler, kein Eintrag) - das
      laesst tone_spacing_hz-Zeilen unberuecksichtigt liegen, bis T-1705 den
      Kammdetektor liefert. shift_hz/tone_spacing_hz-Toleranzen werden um
      freq_bin_hz aufgeweitet (T-1701a). level_count und keying sind harte
      Kriterien - stimmen sie nicht, faellt die Zeile ganz heraus, auch fuer
      "near" (ein Zweipegelsignal erscheint nie als "beinahe DMR"). Konfidenz
      match/near/kein Befund wie spezifiziert; near_factor kommt aus den
      defaults der Tabelle. Reihenfolge: erst match, dann near, INNERHALB
      jeder Gruppe alphabetisch nach mode_id - bewusst nicht nach Guete, das
      schmuggelte eine Rangfolge ein, die es laut Leitsatz nicht gibt. Keine
      Felder score/rank/best/probability. Selbsttest M1-M9 ausschliesslich
      mit Kennwerte-dicts von Hand, keine WAV-Datei; M7/M8 belegen am selben
      Fall (rtty_kommerziell_425_50, Hub-Abweichung 20 Hz), dass die
      Bin-Aufweitung eine Zeile von "near" (freq_bin_hz=0) zu "match"
      (freq_bin_hz=9,95) heben kann - Regressionsschutz fuer T-1701a.
      Einbindung in tools/eye_diagram.py (nur FSK-Pfad): laedt die Tabelle
      ueber eb200_core.mode_table.load_fsk_table(), neuer Schluessel
      mode_candidates im Ergebnis-dict und im JSON-Schreibweg; schlaegt das
      Laden fehl, ist mode_candidates leer und die Meldung geht nach stderr,
      die Messung bleibt gueltig. print_report() zeigt "Kennwerte passen
      zu:" mit je einer Zeile pro Treffer (near mit Zusatz "(knapp)"), sonst
      genau "Keine Uebereinstimmung in der Modentabelle." Erprobt an
      records/20260912_202830_7646kHz_IQ.wav: zwei Treffer wie in der
      T-1702-Gegenprobe erwartet (rtty_kommerziell_425_50/_450_50, beide
      match), Raender nach Aufweitung 12,14 Hz bzw. 12,76 Hz (vorher 2,19 /
      2,81 Hz vor T-1701a).
- [~] T-1709 (2026-09-13, vorgezogen vor T-1704) Nachdetektionsfilter fuer
      Taktlage, Augendiagramm, Augenoeffnung und Jitter. Befund aus der
      T-1703-Erprobung: r2=0,999994 (Symbolrate praktisch fehlerfrei), aber
      Augenoeffnung nur 2,3 %, Jitter 28,6 % - ein einzelner ungemittelter
      Wert je Symbolmitte greift bei 81,2 Samples/Symbol vor allem Rauschen
      ab. Neuer Kennwert postdet_window_samples = max(1, round(0.25 *
      T_samples)), gleitende Mittelung (moving_average(), circular=False)
      als f_det. NUR fuer find_clock_phase(), build_eye_traces(),
      values_at_symbol/eye_opening() und zero_crossings()/jitter_percent().
      Burst-Erkennung (detect_bursts, histogram_flatness),
      freq_offset_and_deviation() und estimate_symbol_duration() bleiben
      UNGEFILTERT - ihre Schwellen (FLATNESS_THR, BURST_GAP_FLATNESS,
      BAUD_TOL_ZUFALLSFOLGE/-WECHSELFOLGE) sind an ungefilterten Daten
      gemessen, eine Mittelung wuerde die Autokorrelationsflanke mit einem
      Dreieck falten und T_fit systematisch verschieben. POSTDET_FILTER_REL
      = 0.25 gemessen (nicht geraten) ueber vier Fixturen (T1/T11 aus dem
      Selbsttest, eine Ueberabtastungs-Fixture fs_eff=4000/50 Bd/SNR=10dB,
      eine Rauschfixture) - 0.25 ist der kleinste Wert, der bei allen drei
      Signalfixturen innerhalb weniger Punkte des Bestwerts liegt UND die
      Mindestforderung "vier Samples/Symbol -> keine Filterung" (W=1)
      einhaelt (rel>=0.5 verletzt das bereits). Kein Kennwert mit
      gemessener Schwelle betroffen: contrast_db, flatness, offset_hz,
      dev_hz, peak_freqs_hz, baud, r2 und mode bitgleich mit/ohne Filter
      (--selftest T33). GEGENBEFUND: Jitter sinkt bei der stark
      verrauschten Ueberabtastungs-Fixture NICHT, sondern steigt
      reproduzierbar (unter 1 % ungefiltert auf rund 35 % gefiltert) -
      ebenso an der echten Aufnahme (dort 28,6 % nahezu unveraendert). Die
      Symbolmitte (Augenoeffnung, Kandidatenvergleich in mode_match.py) ist
      davon nicht betroffen; der Kandidatenbefund aus T-1703 blieb bei der
      Erprobung unveraendert (zwei Treffer, beide match). Auf `[~]` belassen
      (Auftrag), bis OE3GAS den Jitter-Gegenbefund gesehen hat.
- [~] T-1710 (2026-09-13) Nulldurchgangsraster: den Jitter deuten statt ihn nur
      zu messen. Befund aus T-1709: der Nachdetektionsfilter wirkt auf den
      Fixturen wie erwartet, an der echten Aufnahme dagegen kaum
      (Augenoeffnung 2,3 % -> 3,1 %, Jitter 28,61 % -> 28,58 %). Geprueft
      wurde die Hypothese: ITA2 mit 1,5 Stoppbits hat eine Rahmenlaenge von
      7,5 Symboldauern, jedes zweite Zeichen beginnt daher um ein halbes Bit
      versetzt gegen ein ganzzahliges T-Raster gemessen.
      Neue reine Funktion crossing_grid(positions, T, phase) -> dict in
      tools/eye_diagram.py: zyklisches Histogramm (N_CROSSING_HIST_BINS=64)
      der Nulldurchgangslagen relativ zum Taktraster, geglaettet mit
      moving_average(circular=True), ausgewertet mit
      find_histogram_peaks(circular=True) (beide seit T-906 vorhanden, nur
      neu verdrahtet). Liefert crossing_peaks, crossing_grid
      ("full"/"half"/"unclear", sprachneutral) und crossing_concentration
      (Anteil der Nulldurchgaenge in Peakbin+Nachbarbin je Peak). Schwellen
      gemessen statt geraten: CROSSING_MIN_COUNT=10 (Falsch-Positiv-Rate bei
      Zufallspositionen faellt von 0,67 % bei n=4 auf 0 % ab n=7, ueber 20000
      Versuche je Stufe), CROSSING_HALF_TOL=2/64 (die 1,5-Stoppbit-Fixtur
      liefert ueber 90 Kombinationen aus SNR 20/10/6 dB x 30 Seeds einen
      zyklischen Peakabstand von exakt 0,5 - zwei Bin-Breiten Marge fuer
      reale Aufnahmen). jitter_half_pct (nur bei crossing_grid=="half") nutzt
      dieselbe Funktion jitter_percent() gegen T/2 statt T; jitter_pct bleibt
      unveraendert (kein Ersetzen, kein Umrechnen).
      Neue Fixtur _make_async_frames(n_frames, data_bits, stop_bits, rng) ->
      Symbolfolge auf Halbbit-Raster (ueber die Aufgabe hinaus fuer die
      spaetere Rahmenerkennung gedacht) plus _make_fsk_signal_from_symbols()
      (wie _make_fsk_signal, aber mit vorgegebener statt zufaelliger Folge).
      Selbsttest T34 (sechs Punkte, T1-T33 bleiben gruen): 1,0/2,0 Stoppbits
      -> "full", 1,5 Stoppbits -> "half" mit jitter_half_pct rund 600x kleiner
      als jitter_pct, Rauschen -> "unclear", Abschottung (dev_hz/offset_hz/
      baud/jitter_pct/eye_opening_pct/mode_candidates unveraendert) bestehen.
      Abweichung von der Aufgabe (mit OE3GAS abgestimmt, siehe Chat): Punkt 4
      (Mehrwegefall, 0,3 T Verzoegerung, 0,6 Amplitude) senkt die Konzentration
      bei einem korrekt implementierten crossing_concentration NICHT
      messbar - gemessen ueber 8 Seeds, Phasen 0-180 Grad und SNR 3-20 dB
      verschiebt ein STATISCHER Zweiwegekanal (Verzoegerung < 1 Symbol) bei
      binaerem FSK nur die Peaklage (jeder Uebergang sieht dieselbe
      Interferenzgeometrie), er verschmiert sie nicht. Test 4a haelt diesen
      Negativbefund fest. Test 4b prueft zusaetzlich einen zeitvarianten Kanal
      (Doppler exp(2j*pi*f_d*t) auf dem verzoegerten Pfad, f_d in
      {0; 0,1; 0,5; 2,0} Hz): auch dort faellt die Konzentration nicht - ebenfalls
      dokumentierter Negativbefund. Damit gilt: crossing_concentration trennt
      "stabiles Raster" von "zeitveraenderlichem Kanal", nicht allgemein
      "diskret" von "verschmiert" wie urspruenglich angenommen (Korrektur zum
      Leitsatz weiter unten im Entscheidungslog). Die Einstufung full/half/
      unclear haengt nur an den Peaks und ist davon unberuehrt.
      Erprobung an records/20260912_202830_7646kHz_IQ.wav (--t0 10 --t1 30
      --tune -75 --auto-decimate 750 --mode fsk): 10 Haeufungen, Konzentration
      0,51 -> crossing_grid=="unclear". Die Hypothese (Halbbit-Raster durch
      1,5 Stoppbits) ist damit WIDERLEGT, nicht bestaetigt - der Bericht nennt
      Peaklagen und Konzentration ohne Deutung, wie vom Leitsatz gefordert.
      Nachtrag (2026-09-13, T-1711): dieser Befund war ein Artefakt der
      Symbolratengenauigkeit, keine echte Widerlegung. Die grobe Schaetzung
      (49,25 statt 80,0 Samples/Symbol, 1,53 % Fehler) drieb ueber die
      Aufnahme um rund 15 volle Symbolperioden - genug, um ein perfektes
      Halbbit-Raster zu Gleichverteilung zu zermahlen. Mit dem Feinabgleich
      aus T-1711 zeigt dieselbe Erprobung 2 Haeufungen bei 0,26/0,76 T,
      Konzentration 1,00 - die Hypothese gilt nach Korrektur der Symbolrate
      als BESTAETIGT. Siehe T-1711.
      Nachtrag (2026-09-13, T-1710a, Befund aus der Bedienvalidierung von
      T-1704): jitter_percent(positions, T) normiert auf die RASTERWEITE -
      der Aufruf mit T/2 fuer jitter_half_pct lieferte damit Prozent einer
      HALBEN Symboldauer, waehrend jitter_pct Prozent einer GANZEN zeigt.
      Neben "Jitter 25,1 %" stand "Jitter (Halbbit-Raster) 2,608" - las sich
      wie ein Verhaeltnis 1:10, war in Wahrheit 1:19. Korrektur: Faktor 0,5
      auf jitter_half_pct, damit beide Werte dieselbe Bezugsgroesse (ganze
      Symboldauer) tragen; 2,608 wird zu 1,304. jitter_pct selbst bleibt
      unveraendert - nur die Bezugsgroesse des Halbbit-Werts wechselt, kein
      Ersetzen, kein Umrechnen an anderer Stelle. print_report() beschriftete
      den Wert bereits mit "% von T" - das war durch den Fehler falsch und
      wird durch die Korrektur richtig, bleibt also unveraendert stehen.
      Auf [~] belassen (Auftrag).
- [x] T-1711 (2026-09-13) Feinabgleich der Symbolrate ueber die
      Nulldurchgangslagen. Befund: der Flankenfit liefert die Rate auf
      BAUD_TOL_ZUFALLSFOLGE genau (1-6 %) - das reicht zur Identifikation,
      nicht zur Ausrichtung. An der echten Aufnahme ergab der Fehler
      (1,53 %) ueber 985 Symbole rund 15 volle Perioden Drift und zermahlte
      das Halbbit-Raster aus T-1710 zu scheinbarer Gleichverteilung (siehe
      Nachtrag oben).
      Neue reine Funktionen in tools/eye_diagram.py: circular_resultant(
      positions, T, harmonic) - zirkulare Resultierende einer Harmonischen,
      und refine_symbol_rate(positions, T_coarse, n_symbols_approx, tol) ->
      dict (T_fine/r2/accepted/reject_reason). Verwendet wird die ZWEITE
      Harmonische: ein Halbbit-Raster (zwei um T/2 versetzte Populationen)
      loescht sich in der ERSTEN Harmonischen zu R1~0 aus (gegenphasig),
      obwohl das Raster perfekt ist - beide Faelle (ganz/halb) liegen in der
      zweiten Harmonischen gleichermassen bei R2~1 (eine Verschiebung um T/2
      aendert das Argument 2*pi*2*x/T um genau 2*pi). Mit h=1 waere der
      Halbbit-Fall dieser Aufgabe unsichtbar gewesen (--selftest T35.2:
      R1=0,024 gegen R2=1,000 bei 1,5 Stoppbits).
      Suchbereich bewusst an die grobe Schaetzung gebunden (+-
      BAUD_TOL_ZUFALLSFOLGE, NICHT beliebig erweiterbar): R2(T) hat
      Nebenmaxima bei T/2, 2T und weiteren Vielfachen der wahren Periode
      (Alias der Harmonischen) - nur eine hinreichend genaue grobe Schaetzung
      haelt die Suche in der Umgebung der richtigen Periode. Schrittweite
      dT/T < 1/(8*n_symbols_approx), anschliessend Parabelverfeinerung durch
      die drei besten Stuetzstellen.
      Kein stiller Ruckfall: der Feinwert wird nur uebernommen, wenn er sich
      belegen laesst (BAUD_FINE_MIN_CROSSINGS, BAUD_FINE_R2_MIN,
      BAUD_FINE_PEAK_MARGIN - alle drei ueber eine Messreihe bestimmt, siehe
      Kommentar an den Konstanten). Eine Ablehnung wird immer ausgewiesen
      (baud_refined=false, baud_fine_reject_reason intern gesetzt,
      print_report() nennt den Grund), nie kommentarlos die grobe Schaetzung
      weiterverwendet, ohne das zu sagen.
      Neue Schluessel: baud_coarse (bleibt immer erhalten), baud_fine_r2
      (None wenn die Suche nicht anlief), baud_refined. baud traegt weiterhin
      den verwendeten Wert (fein nach erfolgreichem Abgleich, sonst grob);
      alles danach (Taktphase, Augendiagramm, Augenoeffnung, Jitter,
      Nulldurchgangsraster) rechnet damit. Bei --baud (baud_override) laeuft
      kein Feinabgleich - die Vorgabe gewinnt unveraendert.
      Selbsttest T35 (sieben Punkte): Genauigkeit (50/100/1200 Bd, je drei
      Seeds) liefert baud nach Feinabgleich auf 0,0001-0,0001 % genau
      (Vorgabe 0,1 %), baud_coarse weiterhin bis 4,30 % daneben (Vorgabe
      <= 6 %); Halbbit-Raster h=1-gegen-h=2 wie oben; Wirkung an einer
      60-s-Fixtur (rund 3000 Symbole) hebt die Augenoeffnung von 0,86 % auf
      97,97 %; Ablehnung bei Rauschen (R2=0,164 < 0,85) und bei zu wenigen
      Nulldurchgaengen (direkt und Ende-zu-Ende, 29 Symbole); Vorgabe
      gewinnt; Abschottung (contrast_db/flatness/offset_hz/dev_hz/
      peak_freqs_hz/mode bitgleich). T1-T34 bleiben gruen.
      Erprobung an records/20260912_202830_7646kHz_IQ.wav (--t0 10 --t1 30
      --tune -75 --auto-decimate 750 --mode fsk): Symbolrate 50,00 Bd
      (grob 49,25 Bd, Abweichung 1,51 %), Feinabgleich R2=1,00 ueber 493
      Nulldurchgaengen, Augenoeffnung 93,8 % (vorher 3,1 %), Nulldurchgaenge
      2 Haeufungen bei 0,26/0,76 T (Konzentration 1,00), Jitter gegen
      Halbbit-Raster 1,1 %, Kandidaten unveraendert (rtty_kommerziell_425_50
      und _450_50, beide match) - reproduziert exakt das Bild der
      --baud-50-Vorgabe aus dem T-1711-Befund, ohne die Vorgabe zu setzen.
- [~] T-1704 (2026-09-13) Anzeige der Kandidaten in :8070. Eigene Tafel unter
      der FSK-Wertetafel (analyze_audio.py, nur dort geaendert), eigene
      Ueberschrift "Kennwerte passen zu"; je Kandidat Anzeigename und
      Kriterienzeilen (Hub/Symbolrate/Tonabstands-Streuung mit Soll und
      Toleranz, level_count/keying wie im CLI-Bericht nicht gezeigt) plus
      die Notiz aus modes/fsk.json. "near"-Kandidaten in eigener Gruppe
      unter "Knapp daneben", nicht als Randvermerk. Reihenfolge bleibt exakt
      die aus mode_candidates (match vor near, je alphabetisch nach
      mode_id) - keine Umsortierung. Leere/fehlende mode_candidates ->
      "Keine Uebereinstimmung in der Modentabelle.", kein Fehler.
      Verlaesslichkeitshinweis unter der Liste nur bei baud_refined===false
      (T-1711: der Feinabgleich lehnt bei kurzen Bursts ab, dort kann der
      grobe Flankenfit bis zu 11 % danebenliegen - mehr als die 6 %
      Baudtoleranz der Modentabelle).
      Wertetafel (M_FELDER_FSK) waechst von 9 auf 12 Felder: Modulationsindex
      neu, crossing_grid als "Rasterlage" (full/half/unclear ->
      ganze/halbe/kein einheitliches Raster, bei half zusaetzlich der
      Hinweis "moeglich z. B. bei 1,5 Stoppbits (ITA2/RTTY)" wortgleich zu
      T-1710), jitter_half_pct als "Jitter (Halbbit-Raster)" (nur bei
      half). jitter_pct-Erlaeuterung ergaenzt um den Hinweis, dass der Wert
      bei halbem Raster die Rasterlage misst, nicht die Stoerung - ohne den
      Satz liest jeder Bediener ~25 % Jitter als stark gestoertes Signal,
      obwohl Jitter (Halbbit-Raster) rund 1 % zeigt. An der Symbolrate-Zeile
      kenntlich, ob feinabgeglichen wurde ("(feinabgeglichen)"/"(grob
      geschaetzt)"). freq_bin_hz/postdet_window_samples/baud_coarse/
      baud_fine_r2/crossing_peaks/crossing_concentration bleiben in JSON
      und Bericht, aber ausserhalb der Tafel.
      Selbsttest: alle neuen Textbausteine als Fragmente, "erkannt" faellt
      im neuen Kandidatenblock (Regressionsschutz Leitsatz), Tafel steht im
      statischen Quelltext nach der Wertetafel, nur im FSK-Zweig, fehlendes
      mode_candidates nimmt denselben Zweig wie eine leere Liste. T-1613
      (OFDM-Feld symbol_s) mitgezogen, da die ms-Umrechnung beim Refactor
      auf mWertZelle() umgezogen ist.
      Browser-Erprobung an records/20260912_202830_7646kHz_IQ.wav (Marker
      284/-186 Hz, "auf Marker abstimmen" angehakt, Zeitfenster 0:10-0:30):
      Symbolrate 50,0 (feinabgeglichen), Rasterlage halbe Symboldauern mit
      Stoppbit-Hinweis, Jitter 25,0 % neben Jitter (Halbbit-Raster) 1,086 %,
      ein match (RTTY kommerziell 450 Hz) und ein near in "Knapp daneben"
      (RTTY kommerziell 425 Hz) - beide Gruppen einmal live gesehen.
      Gegenprobe ohne Abstimmung: FSK scheitert ("nur ein Pegel erkannt,
      kein FSK-Charakter"), keine Kandidatentafel, keine halbe Aussage -
      wie gefordert. Vollstaendiger Wortlaut im Abschlussbericht des Chats.
      Auf [~] belassen (Auftrag) trotz abgeschlossener Browser-Erprobung.
      Nachtrag (Messauftrag M3): Stand der Aufnahme records/
      20260912_202830_7646kHz_IQ.wav nach heutigem Erkenntnisstand -
      GEMESSEN: 2-FSK, 50,00 Bd (feinabgeglichen, R2 bis zu 1,00), Hub nahe
      450 Hz (belastbarster Lauf N=16: 447,8 Hz, siehe M3), Halbbit-
      Rahmenraster (crossing_grid="half", Konzentration 1,00). GEDEUTET,
      NICHT NACHGEWIESEN: die Kennwerte passen zu ITA2 mit 1,5 Stoppbits -
      das ist eine Moeglichkeit, die zur Rahmenlaenge passt, kein Nachweis
      des Zeichensatzes oder Protokolls.
      Nachtrag (2026-09-13, T-1704a, Bedienvalidierung durch OE3GAS):
      Wortlaut der Kandidatentafel geaendert - Ueberschrift "Kennwerte passen
      zu" -> "Bekannte Dienste:", Gruppenueberschrift "Knapp daneben" ->
      "keine Zuordnung moeglich" (in analyze_audio.py an allen vier
      Fundstellen: CSS-Kommentar, JS-Kommentar, JS-Zeichenkette,
      Selbsttest-Fragmentliste). Reine Umtextung, keine Verhaltensaenderung
      - Reihenfolge, Gruppierung und Leitsatz-Wortlaut ("Keine
      Uebereinstimmung in der Modentabelle.") bleiben unberuehrt. Zusaetzlich
      der OFDM-Hinweis "Mehrtraegrige Verfahren" -> "Multi-Traeger-Verfahren"
      (echtes Ae ueber die HTML-Entity &auml; im ausgelieferten Markup, kein
      Umlaut-Zeichen im Python-Quelltext). Auf [~] belassen (Auftrag).
- [~] T-1717 (2026-09-13) Echte Umlaute in allen sichtbaren Texten der
      :8070-Ansicht (analyze_audio.py, nur dort geaendert). Ausgangsbefund
      aus der Bedienvalidierung von T-1704a: drei Schreibweisen fuer Umlaute
      standen nebeneinander in HTML_PAGE - ASCII-Ersatzschreibung (ae/oe/ue/
      ss), HTML-Entities (&auml; usw.) und, seit T-1704a, ein echtes Ae ueber
      &auml; im ausgelieferten Markup. Die Seite deklariert UTF-8 zweifach
      (meta charset + Content-Type-Header), reale Umlaute sind also ueberall
      im HTML sicher; Entities dagegen loesen in textContent- und .title-
      Zuweisungen NICHT auf und erscheinen dem Bediener als rohes "&ouml;"
      statt "oe" mit Punkten. Deshalb auf echte UTF-8-Umlaute umgestellt:
      HTML-Markup, innerHTML-Strings, M_FELDER_*-Label/Tooltips, textContent/
      .title-Zuweisungen, serverseitige Fehlermeldungen. NICHT angefasst:
      Bezeichner, persistierte Enum-Werte (full/half/unclear, getastet/
      dauersignal, match/near, reject_reason, Format-IDs, mode_id), CSS-
      Klassen/Element-IDs, Query-Parameter, Quelltextkommentare (bewusst -
      auch wo sie dadurch vom nun umgetexteten Code abweichen),
      tools/eye_diagram.py und jede andere Datei.
      Bestandsaufnahme: 60 Fundstellen (9 HTML-Markup, 32 M_FELDER-Label/
      Tooltip, 9 innerHTML, 7 textContent/.title, 3 serverseitig) - unter der
      Ruecksprache-Schwelle von 60, daher ohne Rueckfrage bearbeitet.
      Gegenprobe: 79 eingefuegte Umlautzeichen (aus dem Diff gezaehlt) gegen
      79 zusaetzliche 0xC3-Bytes (99 nach der Aenderung, 20 davor) - exakte
      Uebereinstimmung.
      Gates: ast.parse OK, check_js_blocks.py 3 Bloecke/0 Fehler, --selftest
      vollstaendig durchgelaufen, Entity-Abwesenheit
      (&uuml;|&auml;|&ouml;|&Uuml;|&Auml;|&Ouml;|&szlig;) Ist 0, CRLF
      durchgehend erhalten.
      Browser-Erprobung an records/20260912_202830_7646kHz_IQ.wav:
      Navigationslink "zurueck zur Liste" mit echtem ue, "Ausschnitt im
      Wasserfall waehlen" (textContent) mit echtem ae, Wertetafel "Guete r2",
      "Traegerversatz (Hz)", "Augenoeffnung (%)" korrekt, Kandidatentafel
      "Bekannte Dienste:"/"keine Zuordnung moeglich" mit echtem oe. Der
      Leerfall "Keine Uebereinstimmung in der Modentabelle." liess sich mit
      dieser Aufnahme nicht live ausloesen (nur ein Signal im Material,
      dessen Kennwerte zur Modentabelle passen) - statt dessen quelltextlich
      bestaetigt: unveraenderter String ueber denselben innerHTML-Mechanismus
      ausgeliefert wie die live bestaetigten Faelle, von derselben Entity-
      Abwesenheitspruefung erfasst.
- [ ] T-1712 Baudtoleranz der Modentabelle an baud_refined koppeln: bei
      gelungenem Feinabgleich ist die Rate auf 0,0001 % genau, die 6 % aus
      modes/fsk.json sind dann unnoetig grosszuegig. Lohnt erst bei mehr
      eng benachbarten Zeilen (Befund T-1704).
- [ ] T-1713 BAUD_TOL_ZUFALLSFOLGE (0,06) gilt nachweislich nicht fuer
      kurze Bursts - dort bis zu 11 % gemessen (Befund T-1711).
      Nachtrag (Messauftrag M3): M3 hat fuer die Hub-/Tonabstandsmessung
      gezielt geprueft, ob ein systematischer Anteil aus der Flanke des
      Dezimationstiefpasses beitraegt, und das NICHT bestaetigt - zwei
      verwertbare Dezimationsstufen (N=8/16) ergaben exakte Bin-Mitten, die
      Abweichung zwischen ihnen ist vollstaendig durch Quantisierung des
      Histogramms erklaerbar. Der Punkt schrumpft damit auf die
      Subbin-Interpolation aus T-1714.
- [ ] T-1714 Subbin-Interpolation der Histogramm-Peaks in
      find_histogram_peaks() - Parabel durch die drei Bins um das Maximum,
      Muster der Maximumsverfeinerung aus T-1711 (refine_symbol_rate()).
      Ziel: Peaklage auf etwa ein Zehntel Bin-Breite statt einer halben,
      damit Hub und Tonabstand nicht mehr von der gewaehlten Dezimation
      abhaengen (Befund Messauftrag M3: beide bisher gemessenen Hubwerte
      sind exakte, hart gerasterte Bin-Mitten). Anschliessend den
      Zusatzterm der Frequenztoleranz aus T-1701a neu messen - er duerfte
      kleiner werden.
- [ ] T-1718 Der Hinweis „Kein auswertbares Signal in diesem Ausschnitt" in
      :8070 ist praktisch unerreichbar: Er erscheint nur, wenn FSK und PSK
      scheitern, der PSK-Pfad liefert aber auf reinem Rauschen zuverlässig
      einen unplausiblen Treffer (bekannte Schwäche, im Quelltext
      dokumentiert). Befund bei der Validierung von T-1704a: Die Fehlanzeige
      liess sich nicht auslösen. Folge für den Bediener: Über einem leeren
      Band steht ein PSK-Befund statt einer ehrlichen Fehlanzeige. Zu klären
      ist, ob der PSK-Pfad ein Mindestgütekriterium bekommt oder die
      Fehlanzeige anders ausgelöst wird.

### Stufe 2 -- MFSK andeuten

- [ ] T-1705 Kammdetektor im zeitlich gemittelten Spektrum: Tonanzahl,
      Tonabstand, Kammguete. Eigener Weg, unabhaengig vom
      Momentanfrequenz-Histogramm -- dessen min_bin_distance=5 und
      min_rel_height=0.20 sind auf zwei bis vier Pegel ausgelegt und taugen
      fuer M > 4 nicht. Datenquelle ist das ohnehin vorhandene Mittelspektrum.
- [ ] T-1706 MFSK-Kandidaten aus dem Kamm: ALE 2G (8 Toene, 250 Hz, 125 Bd),
      FT8 (8 Toene, 6,25 Hz), WSPR (4 Toene, 1,46 Hz), Piccolo (32 Toene), sonst
      die allgemeine Andeutung "Moeglicherweise MFSK (M Toene, Δf Hz)".

### Stufe 3 -- Zeitstruktur und Vorwissen

- [ ] T-1707 Zykluszeiten aus der Autokorrelation der Huellkurve: 15 s
      (FT8/FT4), 120 s (WSPR), 210 ms (SITOR-B/AMTOR), 392 ms (ALE-Wort),
      0,5 s (WEFAX-Zeilentakt). Ein FFT-Paar ueber bereits gelesene Daten.
- [ ] T-1708 Frequenzvorwissen aus known_freq/FNV als GETRENNT ausgewiesener
      Hinweis (518 kHz Navtex, DSC-Kanaele, ALE-Kanaele). Fliesst nie in die
      Messevidenz ein -- sonst bestaetigt die Tabelle am Ende nur noch sich
      selbst.

### Stufe 4 -- Asynchrone Rahmenerkennung

- [~] T-1715 (2026-09-13) Rahmensuche auf dem Halbbit-Raster: neue Datei
      eb200_core/framing.py (reine stdlib-Funktion detect_async_framing(),
      kein numpy, Muster eb200_core/mode_match.py), aufgerufen aus
      tools/eye_diagram.py (analyze_file, NUR wenn baud_refined oder
      baud_override_used - eine grobe Symbolrate lieferte sonst eine Rahmen-
      "Erkennung" auf einem driftenden Bitraster, siehe T-1710/T-1711).
      Verfahren: fuer jede Kandidatenlaenge P (14/15/16/18/20/22 Halbbit),
      jede Phase und beide Polaritaeten der Anteil der Rahmen mit
      Startbit=0 (beide Halbbit) und Stoppbit=1; die Laenge mit dem
      hoechsten Trefferanteil gewinnt, gemessen gegen die beste ANDERE
      Laenge (margin). Kein stiller Ruckfall: accepted=False mit
      sprachneutralem reject_reason ("zu_wenige_rahmen",
      "score_zu_niedrig", "kein_eindeutiger_kandidat"), wenn n_frames,
      score oder margin die gemessenen Schwellen unterschreiten. Laenge 20
      traegt zwei gleichberechtigte Deutungen (7 Datenbits + Paritaet + 1
      Stoppbit ODER 8 Datenbits + 1 Stoppbit) - an der Bitfolge allein nicht
      unterscheidbar, beide werden genannt (dieselbe Regel wie bei den
      Modenkandidaten aus T-1704).
      Schwellen gemessen, nicht gesetzt (Methode wie BAUD_FINE_R2_MIN):
      Zufalls-Obergrenze bei n_frames=50 (200 Versuche) Score 0,364/
      Margin 0,089; echte, verrauschte Signale (5 % Bitfehler, 30 Seeds)
      bei genau diesem n_frames Score 0,740/Margin 0,272. FRAMING_MIN_
      FRAMES=50 (kleinster gemessener Punkt mit klarer Trennung),
      FRAMING_SCORE_MIN=0,55 und FRAMING_MARGIN_MIN=0,18 (je mittig in der
      Luecke). Bei 10 Rahmen (Regressionsfixtur) greift folgerichtig allein
      die Mindestzahl, nicht Score oder Margin - ein eindeutiges Signal mit
      zu wenig Material bleibt trotzdem abgelehnt.
      eye_diagram.py: neun framing_*-Felder im Ergebnis-dict (JSON und
      print_report), Wortlaut "Kennwerte passen zu" wie bei den
      Modenkandidaten, nie "erkannt". Selbsttest T36 (10 Pruefungen: reine
      Suche ohne Signal, alle drei ITA2-Laengen, ASCII 7N1, Polaritaet,
      Zufall abgelehnt, Bitfehler, zu wenige Rahmen, Ende-zu-Ende inklusive
      Nulldurchgangsraster, Sperre ohne Feinabgleich, Abschottung der
      unbeteiligten Felder) plus eigener Selbsttest in framing.py (9
      Pruefungen, ohne jede Signalverarbeitung). T1-T35 unveraendert gruen.
      Erprobung an records/20260912_202830_7646kHz_IQ.wav (--t0 10 --t1 30
      --tune -85 --decimate 16 --mode fsk, wie in T-1704/T-1704a): Halbbit-
      Raster mit Konzentration 1,00 bestaetigt, Rahmensuche findet 15
      Halbbit (7,5 Bit, "5 Datenbits + 1,5 Stoppbits (ITA2)"), Polaritaet
      inverted, Start/Stopp-Treffer 71,4 % ueber 133 Rahmen, Abstand zur
      naechsten Rahmenlaenge 37,1 Punkte - klar akzeptiert, wie aus den
      bisherigen Messungen erwartet.
- [~] T-1716 (2026-09-13) Alphabet-Indizien und Deutung der Rahmenausfaelle.
      Anlass: die T-1715-Erprobung fand 15 Halbbit (5 Datenbits + 1,5
      Stoppbits), aber nur 71,4 % Start/Stopp-Trefferanteil ueber 133 Rahmen -
      bei 94 % Augenoeffnung und sauberem Tonwechsel (M2: r=-0,862)
      erklaerungsbeduerftig. Neue Funktion character_stats() in
      eb200_core/framing.py (weiterhin reine stdlib, kein numpy): baut aus
      den gueltigen Rahmen (Start/Stopp-Bedingung erfuellt, sonst Muell und
      NICHT gezaehlt) den fuenfstelligen Code je Zeichen und misst dessen
      Haeufigkeit gegen sechs ITA2-Bezugscodes (LTRS/FIGS/Space/CR/LF/NUL),
      dazu Ausfallslaeufe (fail_run_max, fail_share_in_runs) - das
      Unterscheidungsmerkmal zwischen Leerlauf/Rasterdrift (lange
      zusammenhaengende Ausfallslaeufe) und Bitfehlern (kurze, gestreute
      Ausfaelle). ita2_hint (kein Beweis, nie "erkannt" in der Ausgabe) nur
      bei Space/LTRS/FIGS alle drei ueber gemessener Schwelle.
      Schwellen gemessen (Methode wie FRAMING_SCORE_MIN): Zufalls-Obergrenze
      (200 Versuche, n=200 Zeichen) Space/LTRS/FIGS je bis 0,06-0,08; echte,
      numerisch dichte ITA2-Fixture (kommerzielles/Wetter-RTTY-Wortmix, 150
      Seeds, n=300) FIGS (der seltenste, schwellenbestimmende Code) min
      0,057 / p25 0,073 / median 0,083. ITA2_SPACE_MIN=ITA2_SHIFT_MIN=0,07
      (je mittig in der jeweiligen Luecke).
      Selbsttest F10-F15 in framing.py (u.a. F15: eingefuegte 20-Rahmen-
      Dauer-Mark-Strecke gibt fail_run_max=20/fail_share_in_runs=1,000, 5 %
      Bitfehler geben fail_run_max=2/fail_share_in_runs=0,000 - die
      Kernpruefung der Aufgabe, sauber getrennt), T37 in eye_diagram.py
      (Ende-zu-Ende, Abschottung inklusive aller framing_*-Felder). T1-T36
      und F1-F9 unveraendert gruen.
      Erprobung an records/20260912_202830_7646kHz_IQ.wav (dieselben
      Parameter wie T-1715): Zeichenvorrat 95 von 133 Rahmen gueltig, Space
      34,7 %, LTRS 8,4 %, FIGS 6,3 % (knapp unter der Schwelle - ita2_hint
      bleibt False), Rahmenausfaelle 28,6 %, laengster Lauf 11 Rahmen,
      86,8 % der Ausfaelle in Laeufen >= 3. Deutung: der weit ueberwiegende
      Anteil der Ausfaelle in LANGEN Laeufen (86,8 %) spricht klar GEGEN
      streuende Bitfehler und FUER Leerlauf oder Rasterdrift; Space und LTRS
      sind spuerbar erhoeht (beide ueber ihrer Schwelle), FIGS verfehlt sie
      nur knapp (6,3 % gegen 7 %) - die Messung trennt Leerlauf und
      Rasterdrift damit NICHT eindeutig, beide bleiben moegliche Ursachen.
      Damit wird T-1719 (unten) dringlich.
- [~] T-1716a (2026-09-14) Nachtrag zu T-1716: ita2_hint auf tragfaehige
      Kriterien gestellt. Befund aus der eigenen Messreihe von T-1716: FIGS
      zeigte keine Luecke, sondern eine UEBERLAPPUNG (Zufall bis 0,057-0,08
      je nach n_chars, echte Fixture min/p25/median 0,057/0,073/0,083) - eine
      Schwelle "mittig in der Luecke" war dort nicht konstruierbar, weil es
      keine Luecke gab. FIGS und LTRS fallen als Kriterium weg (bleiben als
      Material in code_shares). Neu: ita2_hint prueft n_chars>=ITA2_MIN_CHARS,
      Space>ITA2_SPACE_MIN und den Anteil des HAEUFIGSTEN Codes
      (top_codes[0]["share"])>ITA2_TOP1_MIN.
      Nachgemessen bei n_chars in {50,100,200,300} (500 Versuche Zufall,
      400 Versuche je Textart): erst bei n_chars=300 trennen sich Zufall und
      echter Text (beide Verkehrsarten: 30 % und 65 % Zahlengruppen-Anteil)
      bei BEIDEN Massen gleichzeitig -> ITA2_MIN_CHARS=300. Bei n_chars=300:
      Zufalls-Obergrenze top1 0,077/Space 0,063, Text-Untergrenze (die
      jeweils engere der beiden Verkehrsarten) top1 0,130/Space 0,073 ->
      ITA2_TOP1_MIN=0,104, ITA2_SPACE_MIN=0,068 (je mittig in der Luecke).
      Selbsttest F10-F17 in framing.py (F10-F13 neu/umgebaut: Zufall ueber
      120 Seeds, echter Text gemischt und numerisch dicht, zu wenige
      Zeichen trotz sonst eindeutiger Anteile; F14-F17 = vormals F12-F15,
      unveraendert bis auf Nummerierung). F1-F9 unveraendert gruen.
      Erprobung an records/20260912_202830_7646kHz_IQ.wav (dieselben
      Parameter wie T-1715/T-1716): unveraendert 95 von 133 Rahmen gueltig,
      Space 34,7 %, haeufigster Code (Space) 34,7 % - beide weit ueber den
      neuen Schwellen, aber n_chars=95 liegt unter ITA2_MIN_CHARS=300, die
      Zeile "Haeufigkeiten passen zu ITA2" bleibt deshalb aus. Das ist die
      erwartete Konsequenz der ehrlichen Messung, keine offene Abweichung -
      95 Zeichen sind fuer eine 300-Zeichen-Schwelle schlicht zu wenig; die
      Erprobung zeigt aber weiterhin denselben Zeichenvorrat wie zuvor.
- [ ] T-1719 detect_async_framing setzt Zeichen lueckenlos aneinander.
      Asynchrone Uebertragung erlaubt aber beliebig lange Mark-Pausen
      zwischen Zeichen; nach jeder Pause driftet die Rahmenphase weg und
      alle folgenden Rahmen fallen durch, bis die Phase zufaellig wieder
      passt. Robusterer Weg: nicht ein starres Raster abtasten, sondern
      jeden Mark-zu-Space-Uebergang als Startbit-Kandidaten pruefen und den
      Anteil der davon ausgehenden gueltigen Rahmen zaehlen. Befund aus
      T-1715 (Trefferanteil 71,4 % bei sonst sauberem Signal), Datenlage aus
      T-1716 (86,8 % der Ausfaelle in Laeufen >= 3, Leerlauf/Rasterdrift
      nicht eindeutig getrennt).
- [ ] T-1720 Anzeige der Zeichenvorrat-Werte (chars_*, T-1716) in :8070.
      Liegt bislang nur auf der Kommandozeile und im JSON vor; die
      Modenkandidaten-Tafel (T-1704) zeigt sie noch nicht.

### Reihenfolge

T-1701 bis T-1704 sind eigenstaendig nuetzlich und liefern nach vier Sitzungen
eine vollstaendige Stufe 1. T-1705/T-1706 und T-1707/T-1708 haengen daran, aber
nicht aneinander.

### Entscheidungslog-Eintrag (zu ergaenzen bei T-1703)

Ansiedlung in eb200_core/ statt in tools/eye_diagram.py: iqscan wird dieselbe
Tabelle und dieselbe Vergleichsfunktion brauchen. Tabelle als Daten statt als
Code, damit neue Modes ohne Codeaenderung und ohne neuen Selbsttest dazukommen.

## Meilenstein 15: Portierung Raspberry Pi / Linux
Ziel: MoniScope-Kern (EB200 ueber LAN) laeuft headless auf Raspberry Pi OS
Lite. Entwicklung bleibt auf dem Windows-PC, der Pi zieht ueber ein Bare-Repo.
SDR-Wasserfall (:8030) und die Auswertung langer Laeufe bleiben vorerst am PC.
- [x] T-1501 (2026-09-02) Inventur der Windows-Bindungen, rein lesend.
      Ergebnis: docs/Portierung-Linux-Inventur.md (Suchlaeufe S1-S12, P-CASE,
      P-EOL, je mit woertlichem Befehl und Trefferzahl). Keine Codeaenderung.
      Bewertung der Befunde erfolgt im Chat, daraus entstehen T-1502 ff.
- [ ] T-1503 Zeilenenden suiteweit festlegen (.gitattributes). Bisher gibt es
      keine Regel; Git normalisiert nichts, und Werkzeuge stellen einzelne
      Dateien beim Bearbeiten stillschweigend von CRLF auf LF um. Bei T-914
      und T-917 trat das je einmal auf und musste von Hand zurueckkonvertiert
      werden (aufgefallen nur, weil ein Gate mitzaehlt).
      Zu entscheiden ist NICHT nur "CRLF fuer *.py": auf dem Pi wuerde
      dieselbe Regel Python-Dateien mit CRLF auschecken. Fuer "python datei.py"
      folgenlos, fuer eine ausfuehrbare Datei mit Shebang nicht -
      "#!/usr/bin/env python3\r" ergibt unter Linux "bad interpreter".
      systemd-Units brauchen ohnehin LF.
      Vorschlag zur Pruefung: *.py und *.md eol=crlf, systemd-Units/Shell-
      Skripte eol=lf, Binaerdateien (.wav, .specbin, .audspec) als binary.
      Vorher ist zu klaeren, ob im Repo Python-Dateien mit Shebang liegen,
      die auf dem Pi direkt ausgefuehrt werden sollen.
      Voraussetzung fuer die Entscheidung: Umfang der Pi-Portierung
      (Ersatz- oder Zweitknoten) steht fest.
      Bis dahin gilt: CRLF von Hand wiederherstellen und im Abschlussbericht
      melden - NICHT stillschweigend belassen.

## Meilenstein 18: Englische Oberflaeche (Umstellung, keine Mehrsprachigkeit)
Die WebGUI wird auf Englisch umgestellt. Klarer Schnitt: kein Umschalter, kein Rueckweg,
keine Mehrsprachigkeit. Kommentare, Docstrings, Variablennamen sowie Log- und CLI-Ausgaben
bleiben deutsch. Der Stand davor ist als Tag de-final eingefroren (T-1306).
Sprachvariante: US-Englisch.
Befund vom 2026-09-14 (Quellenstand f8ffaf2): rund 1.350 UI-Strings, davon 1.271 in den
acht Servern und rund 77 in Kernmodulen, die ihre Texte per JSON in die Oberflaeche
schieben. Nach Abzug der Doppelungen (KNOWN-Block, Zwillinge :8020/:8040) bleiben rund
1.100 eigenstaendige Texte. Schwerpunkt ist nicht das HTML, sondern das JavaScript
(606 von 1.271). Verteilung: :8000 311, :8050 231, :8070 198, :8010 161, :8060 124,
:8040 106, :8020 105, :8030 29, ui_theme 6, :8090 keine.
Gemessen mit tools/check_ui_lang.py (T-1805b, Commit siehe tools/ui_lang_basis.json):
die Zahl der BETROFFENEN ZEILEN je Datei ist das belastbare Mass, nicht die Fundzahl -
eine Zeile wird in einem Zug uebersetzt, waehrend ein Satz mehrere Funde erzeugt. Die
oben genannten String-Zahlen stammen aus einer Stichprobenhochrechnung und bleiben als
Groessenordnung stehen.
Reihenfolge ist bindend: Stufe 0 vor Stufe 2. Wer zuerst uebersetzt, fasst dieselben
Stellen ein zweites Mal an, dann in englischem Text ohne Rueckfallebene.

### Stufe 0: sprachneutrales Fundament (auf main, verbessert den Code auch auf Deutsch)
- [x] T-1800 (2026-09-20) Glossar docs/Glossar-EN.md: rund 200 Begriffe DE->EN in
      US-Englisch, verbindlich fuer alle Folgeaufgaben. Enthaelt die Anzeige-Abbildung
      der Datenwerte (laeuft -> Running usw.), die Schreibweisen, die Satzmuster fuer
      Meldungen und elf Vereinheitlichungen, die gewachsene Uneinheitlichkeiten im
      Deutschen beseitigen (Abschnitt 10). Tabellenform, damit T-1805 dagegen prueft.
- [x] T-1801 (2026-09-21, browser-validiert) Anzeige-Abbildung fuer Datenwerte. Neuer JS-Baustein LABELS_JS in
      eb200_core/ui_theme.py: Nachschlagetabellen fuer zustand, art, herkunft, mode,
      phase, klasse, layer, sicherheit plus labelOf(gruppe, code), das unbekannte Werte
      durchreicht. Persistierte Werte bleiben unveraendert (ID-Stabilitaet). Alle
      Stellen, die heute esc(r.zustand) o. ae. anzeigen, gehen ueber labelOf().
      Zusaetzlich: Klassen-<option> in analyze_audio.py auf getrennte value/Anzeige
      umbauen - value und Text sind dort heute derselbe String, eine Uebersetzung
      wuerde den Roundtrip zu /api/klasse zerlegen.
      Geteilt in drei Prompts: T-1801a LABELS_JS in ui_theme.py samt Selbsttest, ohne
      Aufrufer (umgesetzt 2026-09-20); T-1801b Umstellung der Anzeigestellen in
      manage_jobs.py; T-1801c uebrige Server samt der Trennung von value und Anzeigetext
      in renderKlasseManuell (:8070).
      Befund der Stellensuche: nur rund 15 echte Anzeigestellen, dafuer sehr viele
      Vergleichsstellen, die unveraendert bleiben. Drei zusaetzliche Wertgruppen
      gefunden, die roh angezeigt werden (level, bw_class, period_class aus
      session_analyze.py); ins Glossar aufgenommen. verdict ist dagegen ein
      serverseitig zusammengesetzter Satz und gehoert zu T-1802.
      T-1801b umgesetzt 2026-09-21: neun Anzeigestellen in manage_jobs.py auf labelOf
      umgestellt, ART_LABEL und HERKUNFT_LABEL entfallen zugunsten von ART_ORDER.
      Sichtbare Folgen: DSCAN heisst jetzt D-SCAN, "direkt" wird Direct, "Long Run"
      wird Long run, Inventur/Ueberwachung werden Inventory/Surveillance. Die Spalten
      Zustand und Phase sortieren jetzt nach dem angezeigten Text statt nach dem
      unsichtbaren Rohwert. Rohwerte bleiben unveraendert in Vergleichen, Filtern,
      data-value, URL-Hash, sessionStorage und CSS-Klassennamen.
      T-1801c umgesetzt 2026-09-21: acht Anzeigestellen in analyze_audio, analyze_mscan,
      analyze_sessions, analyze_spectrum und waterfall auf labelOf umgestellt, darunter
      die Klassenauswahl in :8070, wo value und Anzeigetext bisher derselbe String waren
      und der Wert ueber /api/klasse in audio_klassen.json zurueckfloss. Zusaetzlich
      gefunden und mitgenommen: ein fest verdrahtetes "laeuft" im Job-Badge von :8040.
      Damit ist die Anzeige-Abbildung vollstaendig; verbleibende deutsche Datenwerte in
      der Oberflaeche gibt es nur noch als verdict (freier Satz aus session_analyze.py,
      siehe T-1802) und als reasons aus audio_classify.py (T-1822).
- [~] T-1802 Codes statt Saetze nachziehen (Regel 3 auf den Altbestand), vier Teile mit
      je eigenem Prompt: (a) sched_health bekommt einen Codevertrag, waterfall.py
      liefert Codes statt Freitext in phase und last_error.text; (b) job_store
      abort_reason und job_runner self.failed auf Code + Parameter; (c) recording_index
      grund-Felder auf Codes; (d) known_freq Catalog.errors auf Code + Parameter.
      Anzeigetext jeweils in der Tabelle aus T-1801.
      T-1802a umgesetzt 2026-09-21: sched_health hat jetzt einen Codevertrag. phase ist
      einer von check/start_dscan/start_ifpan/start_mscan/stop_finished/stop_aborted,
      der Job steht getrennt in phase_arg, last_error traegt code plus detail statt
      eines freien Textes (detail bleibt technische Diagnostik, typisch repr(exc)).
      Die Warnzeile in :8050 formuliert aus der neuen Gruppe sched_phase. Offen bleiben
      T-1802b (job_store.abort_reason, job_runner.failed), T-1802c (recording_index
      grund) und T-1802d (known_freq Catalog.errors).
      T-1802b1 umgesetzt 2026-09-22: Schema abort_code plus abort_detail, additiv neben
      abort_reason. Anzeige ueber den Helfer abortText() mit Rueckfall auf den alten
      deutschen Freitext, damit bestehende Manifeste lesbar bleiben und keine Migration
      noetig ist. Elf Codes in job_store.ABORT_CODES und in der Labelgruppe abort_code.
      Der einzige hier umgestellte Erzeuger ist recover_on_startup (Serverneustart).
      Offen: T-1802b2 (rund zwanzig Erzeuger in waterfall.py), T-1802b3
      (job_runner.failed).
      T-1802b2 umgesetzt 2026-09-22: die neunzehn Erzeuger in waterfall.py schreiben
      Code und Detail. Die lokale Variable abort_reason in _run_mscan_loop ist durch
      abort_code und abort_detail ersetzt, das Tupel in _stop_active_job und in der
      Kommandoschlange zum Tripel geworden. Die Detailtexte sind englisch, weil sie in
      der Oberflaeche erscheinen; woertliche Geraeteantworten und repr(exc) bleiben
      unveraendert. Konsolenausgaben bleiben deutsch und zeigen Code und Detail.
      Offen bleibt T-1802b3 (job_runner.failed) - dessen deutscher Text wandert derzeit
      als Detail in die Anzeige.
      Nebenbefund zur Gate-Formulierung: eine Aufgabe, die nur Backend-Python anfasst,
      bewegt Stufe B von check_ui_lang.py, nicht Stufe A - Stufe A zaehlt ausschliesslich
      Markup und UI-Konstanten. Gates muessen die erwartete Stufe benennen, sonst laufen
      sie aus dem falschen Grund rot.
      T-1802b3 umgesetzt 2026-09-22: job_runner traegt failed_code neben failed. Zwei
      Faelle statt einem: recording_error fuer eine unerwartete Ausnahme,
      geometry_changed fuer den Geometriewechsel waehrend der Aufzeichnung - letzterer
      ist kein Fehler, sondern eine Bedingung, die sauber beendet. self.failed behaelt
      seinen Namen und traegt die englische Einzelheit, damit die Wahrheitspruefung
      "if recorder.failed" weiterhin richtig liest. Damit ist T-1802b abgeschlossen:
      kein Abbruchgrund entsteht mehr als deutscher Satz. Offen bleiben T-1802c
      (recording_index grund) und T-1802d (known_freq Catalog.errors).
      T-1802c1 umgesetzt 2026-09-22: recording_index.py traegt Codes statt Saetze. Neue
      Basisklasse RecordingError mit code und params; RecordingLockedError und
      RecordingNotDeletableError erben davon, letztere weiterhin auch von ValueError,
      damit bestehende except-Zweige greifen. Dreissig raise-Anweisungen umgestellt,
      Codeliste ERR_* auf Modulebene. Die grund-Texte der _invalid_*_row-Bauer und die
      Verwaist-Zeile schreiben jetzt abort_code/abort_detail statt eines deutschen
      Satzes in abort_reason. Zwischenzustand bis T-1802c2: ein abgelehnter
      Loeschversuch zeigt den rohen Code, weil die Handler noch str(exc) durchreichen.
      Drei Codes ergaenzt, die der urspruengliche Entwurf nicht vorsah: ERR_UNEXPECTED_FORMAT
      (Sidecar mit unbekanntem format-Feld in _spectrum_row - eigene Inline-Fehlerstelle,
      kein grund-Text), ERR_SIGMF_EXPORT (Durchreich-Stellen aus sigmf_export.py) und
      ERR_SESSION_DB_UNREADABLE (Session-DB laesst sich nicht oeffnen/lesen - bewusst
      NICHT ERR_SIDECAR_UNREADABLE wiederverwendet, weil eine Session keinen Sidecar hat
      und der Anzeigetext sonst irrefuehrend waere). Vollstaendige, endgueltige Liste aller
      ERR_*-Konstanten (Eingangsgroesse fuer T-1802c2): ERR_OUTSIDE_STORAGE,
      ERR_WRONG_EXTENSION, ERR_LONGRUN_PART, ERR_MAY_BE_RUNNING, ERR_INCOMPLETE,
      ERR_NO_SIDECAR, ERR_SIDECAR_UNREADABLE, ERR_WRONG_ORPHAN_DIR, ERR_NO_STORAGE_ROOT,
      ERR_KNOWN_JOB_DIR, ERR_RECENTLY_WRITTEN, ERR_UNKNOWN_KIND, ERR_SIGMF_IQ_ONLY,
      ERR_NOT_FOUND, ERR_ORPHAN_NO_LABEL, ERR_DB_SCHEMA, ERR_SIGMF_EXPORT,
      ERR_NO_INDEX_ENTRY, ERR_UNEXPECTED_FORMAT, ERR_SESSION_DB_UNREADABLE (20 Codes).
      Der Session-DB-Schema-Fehler (_open_session_db_ro) bleibt bewusst ValueError mit
      deutschem Text - er wird intern gefangen (_session_row_inner) und erreicht die
      Oberflaeche nie als Ausnahme.
      T-1802c2 umgesetzt 2026-09-22: die drei Handler antworten mit error_code und
      error_params statt eines Textes, sofern die Ausnahme einen Code traegt; alle
      uebrigen Ausnahmen liefern weiter error mit str(exc) - der Rueckfall bleibt, bis
      auch mscan_job und job_store umgestellt sind. :8020 und :8040 verpackten die
      Kernausnahmen bisher in eigene Klassen und verloren dabei code und params; ein
      Helfer _rewrap nimmt beide mit. Der Client formuliert ueber errText() aus der
      neuen Labelgruppe err_code (19 Codes). ERR_DB_SCHEMA entfernt - unbenutzt, weil
      der zugehoerige Pfad intern gefangen wird und deutsch bleibt. Damit ist T-1802c
      abgeschlossen.
      Nebenbefund: die beiden fetchJSON()-Varianten in analyze_sessions.py/
      analyze_spectrum.py bauten den Fehlertext mit einer eigenen const-msg-Zeile statt
      der im Prompt zitierten Einzeiler-Form - inhaltlich derselbe Zweck, deshalb ebenso
      auf errText() umgestellt und mitgezaehlt. Frueher Fund: eine erste, zu grobe
      Volltextersetzung von {"error": str(exc)} haette _error_payload() an seiner
      eigenen Rueckfallzeile mit sich selbst ueberschrieben (Endlosrekursion in jedem
      Fehlerpfad aller drei Server) - vor dem Selbsttest bemerkt und korrigiert.
      T-1802d ENTFAELLT (2026-09-22). Vorgesehen war die Umstellung von
      Catalog.errors in eb200_core/known_freq.py. Befund: die Meldungen gehen an vier
      console.warn-Stellen im Client und vier print-Schleifen im Server - beides
      Diagnose, beides bleibt deutsch - und an genau eine sichtbare Stelle,
      validate_candidate() in block_admin.py. Die vergleicht sie als Zeichenketten
      (set(...), startswith(...)), sodass eine Strukturierung dort mitgezogen werden
      muss. Inhalt deshalb nach T-1815 verlagert, wo block_admin ohnehin uebersetzt
      wird; so entsteht kein Zwischenzustand mit rohen Codes im Katalogeditor.
      Damit sind alle Teile von T-1802 erledigt oder verlagert. Offene Abnahmen:
      (a) /api/longrun/status zeigt phase als Code, (b2) ein MSCAN-Abbruch zeigt
      englischen Text, (b3) ein Geometriewechsel zeigt "Geometry changed",
      (c2) ein abgelehnter Loeschversuch zeigt den englischen Satz und eine
      MSCAN-Parameterpruefung weiterhin den deutschen (Rueckfallprobe).
- [x] T-1803 (2026-09-23, browser-validiert) Formatschicht. fmtNum, fmtDate, fmtTime, fmtDur, fmtBytes, cmpText als
      gemeinsamer JS-Baustein in ui_theme.py; alle 32 Locale-Fundstellen darauf
      umstellen, de-AT und de-DE entfallen. Serverseitig _format_khz() in block_admin.py
      und der Zeichentausch der BW-Optionen in waterfall.py. Im selben Task die beiden
      Eingabeparser in block_admin.py (deutsches Komma/Punkt-Format) - sonst nimmt die
      Katalogpflege falsche Frequenzen an. Einzige Stelle im ganzen Meilenstein, an der
      ein Uebersetzungsfehler Daten beschaedigt.
      Geteilt in zwei Prompts. T-1803a umgesetzt 2026-09-22: die kHz-Kette. Anzeige
      (_format_khz in block_admin, _format_khz_label in waterfall, drei
      toLocaleString-Stellen im JS) und Eingabe (drei Kopien von khzDisplayToHz, fuenf
      Python-Parser) zusammen umgestellt - sie bilden einen Vertrag, und ein einseitiger
      Wechsel haette Frequenzen um Faktor 1000 verschoben, ohne Fehlermeldung.
      Verbindlich ab jetzt: Dezimaltrennzeichen ist der Punkt, Komma ist
      Tausendertrennzeichen und wird beim Einlesen entfernt. Eine von Hand im deutschen
      Format eingetippte Zahl wird damit anders gelesen als bisher - das ist die
      notwendige Kehrseite, zwei gleichzeitig gueltige Zahlformate sind nicht
      unterscheidbar. Neuer Rundlauf-Selbsttest F5 prueft Anzeige gegen Eingabe.
      Nebenbefund: parse_marker_table() (Marker-Import) nutzt dieselben zwei Parser und
      hatte einen bestehenden Selbsttest mit deutschem Dezimalkomma als Testdaten
      ("118,5010") - waere unveraendert geblieben, haette T-1803a einen roten Selbsttest
      hinterlassen. Testdaten auf Punkt umgestellt, Docstring berichtigt, zusaetzlicher
      Testfall ("1,234.5" -> 1234,5 MHz) schreibt die Tausendertrenner-Regel auch dort
      fest. T-1803b folgt: Datum, Uhrzeit, Zahlen und Sortierung in allen Dateien.
      T-1803b umgesetzt 2026-09-23: Datum und Uhrzeit ueberall ISO-nah
      (2026-09-07 22:28:30). Zwanzig Stellen mit toLocale* ohne Gebietsschema auf die
      gemeinsamen Helfer isoDate/isoTime/isoHM/isoDateTime in ui_theme.py umgestellt;
      jede liefert bei ungueltigem Datum einen Bindestrich, wie die Aufrufstellen ihn
      schon als Platzhalter verwenden. Ortszeit bleibt Ortszeit - nur die Schreibweise
      wechselt, nicht die Zeitzone. Offen bleibt T-1803c: Zahlformate (de-AT) und
      Textsortierung (localeCompare).
      T-1803c umgesetzt 2026-09-23: Zahlformate und Sortierung. Drei de-AT-Stellen auf
      en-US umgestellt (fmtSek und Verlustangabe in :8060, fmtN in :8050), vier
      Sortierstellen auf den gemeinsamen Helfer cmpText in ui_theme.py - zwei davon
      hatten gar kein Gebietsschema und folgten der Browsersprache, zwei sortierten
      deutsch. Der Schalter numerisch erhaelt die zahlenbewusste Sortierung der
      Katalogkennungen in :8010 (5A vor 10A). Damit ist T-1803 abgeschlossen: die
      Formatschicht kennt kein deutsches Gebietsschema mehr. Einzige verbliebene
      de-DE-Fundstelle ist ein historischer Kommentar in waterfall.py zu T-707.
- [x] T-1804 (2026-09-24, browser-validiert) Verkettungen und Plural aufloesen. Rund 40 Stellen: je Aussage ein
      Formatstring mit Platzhaltern statt Verkettung von Teilstrings, dazu
      plural(n, sing, plur). Bleibt zunaechst deutsch - der Umbau ist strukturell und
      wird getrennt abgenommen, damit die Uebersetzung reine Textarbeit wird.
      Geteilt in zwei Prompts. T-1804a umgesetzt 2026-09-24: Pluralbildung. Neuer Helfer
      pluralWort(n, einzahl, mehrzahl) in ui_theme.py, der nur das WORT liefert - so
      bleibt jede Zahlformatierung unberuehrt und die Aufrufstelle lesbar. Rund zwanzig
      Stellen im JavaScript umgestellt, darunter alle Klammerformen Datei(en),
      Aufnahme(n), Oeffnung(en), Luecke(n), Paket(e), id(s). Der Text bleibt deutsch;
      in Stufe 2 wird nur das Wortpaar getauscht.
      Bewusst ausgenommen: Pluralformen in Konsolenausgaben (bleiben deutsch) und in
      Python-Fehlertexten von block_admin und manage_jobs - diese werden mit ihrer Datei
      in T-1814 und T-1815 behandelt, ein eigener Python-Helfer lohnt fuer vier Stellen
      nicht.
      Befund der Bestandsaufnahme: rund 61 weitere Stellen setzen sichtbare Saetze aus
      Fragmenten zusammen. Die meisten davon bleiben, weil die Wortstellung durch die
      eingesetzten Werte ohnehin festliegt und ein Uebersetzer die Fragmente einzeln
      ersetzen kann. Nur die besonders zerlegten Saetze werden in T-1804b entflochten.
      Fehler, gefunden in T-1811: die Kopfzeile "n Sender auf X MHz" wurde direkt
      in sdr_waterfall.py geaendert, obwohl sie innerhalb von KNOWN_STRIP_BLOCK
      liegt - also in einem Block, der wortgleich in vier Servern steht und nur
      ueber die Referenz waterfall.py geaendert werden darf. Seit b17fb2d liefen
      die vier Kopien auseinander; aufgefallen ist es erst, als T-1811 den
      Abgleich zum ersten Mal wieder aufrief. Der eingefuegte pluralWort-Aufruf
      (mit zweimal 'Sender' als Argument) ist mit der englischen Fassung
      gegenstandslos geworden und in T-1811 ersatzlos entfallen.
      T-1804b ENTFAELLT (2026-09-24). Vorgesehen war, acht besonders zerlegte Saetze zu
      entflechten. Die Durchsicht zeigt, dass sieben davon keine zerlegten Saetze sind,
      sondern wertgetrennte Aufzaehlungen oder Karten aus eigenstaendigen Zeilen - die
      Wortstellung liegt durch die eingesetzten Werte fest, ein Uebersetzer ersetzt
      Fragment fuer Fragment. Damit gilt fuer sie dieselbe Begruendung wie fuer die
      uebrigen rund 61 Verkettungen.
      Zwei Stellen bleiben, beide in waterfall.py: die Sweep-Zeile, die ueber drei
      span-Elemente mit festen em-Breiten verteilt ist, und die Bandbreitenmeldung
      "X kHz mit FM nicht moeglich", bei der die Modusangabe im Englischen ans Satzende
      wandert. Beide sind in T-1812 und T-1813 vermerkt und werden dort mit der Datei
      erledigt - eine Datei, ein Durchgang, eine Abnahme.
      Damit ist Stufe 0 vollstaendig: Glossar (T-1800), Anzeige-Abbildung (T-1801),
      Codes statt Saetze (T-1802), Formatschicht (T-1803), Pluralbildung (T-1804),
      Pruefwerkzeug (T-1805).
- [x] T-1805 (2026-09-22, im Einsatz bewaehrt) Pruefwerkzeug tools/check_ui_lang.py. Extrahiert die HTML/JS-Bloecke aus den
      Python-Quellen (Muster tools/check_js_blocks.py) und meldet deutsche Reste:
      Umlaute/ss, Stoppwortliste, lang="de", de-AT/de-DE, localeCompare(...,'de'), sowie
      Begriffe, die nicht im Glossar stehen. Kommentare, Docstrings und _self_test-
      Bloecke bleiben ausgeklammert. Rueckgabewert 0/1 statt Textausgabe, damit ein
      unbeaufsichtigter Lauf daran abbrechen kann. Vor T-1810 einmal laufen lassen: der
      Ausgangswert ist die Messlatte, Sollwert am Ende ist 0.
      Umgesetzt 2026-09-20: zweistufig - Stufe A (UI-Konstanten und Markup-Strings) ist
      gate-relevant und bestimmt den Rueckgabewert, Stufe B listet die uebrigen Strings
      als Arbeitsvorrat. Ausgangswert in tools/ui_lang_basis.json.
      Nachgebessert 2026-09-20 (T-1805a): die erste Fassung verortete jeden Treffer am
      Anfang der String-Konstanten statt an seiner eigenen Stelle; zusammen mit der
      Entdopplung je Zeile und Art fiel damit eine lange HTML-Konstante auf einen Treffer
      je Art zusammen, und die Zahlen massen die Bauweise der Datei statt des Textes.
      Jetzt Position je Treffer, Ausschnitt um den Treffer, Entdopplung ueber
      (Datei, Zeile, Art, Wort), zusaetzlich Ausgabe der betroffenen Zeilen. Basiswert
      neu erhoben. Regressionstest T16.
      Nachgebessert 2026-09-20 (T-1805b): Kommentare in eingebettetem CSS, JS und HTML
      werden vor der Suche maskiert (zeichenweise durch Leerzeichen, damit die
      Positionsrechnung exakt bleibt). Ohne das haette Stufe A nie 0 werden koennen, weil
      diese Kommentare regelgemaess deutsch bleiben - das Abschlussgate waere unerfuellbar
      gewesen. Ausgabe je Datei um "maskiert: n Zeilen" ergaenzt. Tests T17 bis T22.
      Nachgebessert 2026-09-21 (T-1805c): eine erste, einfachere Erkennung der
      ui-lang-Markierungen kam bereits mit T-1801a (Commit 8a4e786) und wurde hier durch
      eine zeilenbasierte Fassung ersetzt - markierte Ausschlussbereiche
      /* ui-lang: ignore-begin */ ... /* ui-lang: ignore-end */ werden vor der
      Kommentarmaskierung erkannt und ebenso maskiert. Anlass war LABELS_JS aus T-1801a:
      die persistierten Datenwerte stehen dort als JS-Objektschluessel und kollidierten
      mit Glossareintraegen (zuweisung, dienst, empfangskette) und Stoppwoertern
      (ein, aus) - Stufe A fuer ui_theme.py war dadurch von 8 auf 13 gestiegen, jetzt
      wieder 8. Bewusst KEIN Heuristikfilter ueber die Schreibweise: eine Regel wie
      "Bezeichner vor einem Doppelpunkt" haette auch 'Dauer: ' verschluckt, also echten
      UI-Text. Unpaarige oder verschachtelte Markierungen sind ein Fehler mit
      Rueckgabewert 1, und die Zahl der ausgenommenen Zeilen steht je Datei im Bericht,
      damit ein wachsender Ausschluss auffaellt. Tests T23 bis T27.
      Beim Abgleich mit dem T-1805b-Basiswert bewegten sich auch die acht uebrigen
      Dateien; Ursache war nicht die neue Erkennung, sondern dass T-1801a den
      Glossarbegriff "Raster" durch "Raster (Parameter)" ersetzt hatte, wodurch der
      T-1805b-Wert bereits ueberholt war - deshalb warnt glossar_einlesen jetzt bei
      jedem Deutsch-Begriff mit runder Klammer, weil der als Suchwort nicht taugt.
      Nachgebessert 2026-09-21 (T-1805d): die Suchliste wird bereinigt abgeleitet.
      Klammerzusaetze in Spalte 1 werden abgeschnitten (Kanal (Scan-Schritt) -> Kanal),
      weil zehn gaengige Begriffe in der Klammerform im Code nie vorkommen und damit
      unsichtbar waren; aufgefallen, als T-1801a "Raster" in "Raster (Parameter)"
      umbenannte und ueber alle acht Dateien 51 Funde verschwanden. Begriffe, die in
      beiden Sprachen gleich lauten (Filter, Segment, Sweep, Band, Session, Frame, Bin,
      Marker), werden nicht mehr gesucht: sie koennen keinen Uebersetzungsbedarf
      anzeigen, und ein Fund darauf waere nie abarbeitbar - sichtbar geworden am CSS
      filter:brightness in ui_theme.py. Die Klammerwarnung aus T-1805c entfaellt dadurch
      wieder. Neue Meldezeile mit Zahl der Suchbegriffe und der uebersprungenen
      Gleichlaute. Tests T28 bis T31. Damit ist das Pruefwerkzeug fertig; der hier
      erhobene Basiswert ist der Ausgangswert des Meilensteins.
      Abgeschlossen, nachdem das Werkzeug in T-1801 bis T-1804 durchgehend als Gate
      gedient hat. Bekannte Grenze, nicht behoben: LOCALE_RE erkennt nur ausdrueckliche
      Gebietsschemata (de-AT, de-DE, localeCompare mit 'de'), nicht den nackten Aufruf
      toLocaleString() ohne Argument - genau den browserabhaengigen Fall. Nach T-1803b
      und c gibt es keinen solchen Aufruf mehr; der Nutzen einer Erweiterung laege
      allein im Schutz vor Rueckfaellen und wurde zurueckgestellt.

### Stufe 1: gemeinsame Bausteine
- [~] T-1810 ui_theme.py und Seitenkopf: Kopfzeile, Themenschalter, die an
      header_html() uebergebenen Ansichtsnamen in allen acht Servern, und lang="de" ->
      lang="en" an allen acht Fundstellen.
      Umgesetzt 2026-09-24 (Abnahme im Browser steht aus). lang="en" jetzt auf allen
      vierzehn Seitenkonstanten: analyze_audio.py (HTML_PAGE, HTML_NO_PATH),
      analyze_mscan.py (HTML_NO_RUN, HTML_MSCAN_RESULT), analyze_sessions.py
      (HTML_PAGE), analyze_spectrum.py (HTML_PAGE), waterfall.py (STATUS_HTML,
      HTML_PAGE), eb200_core/block_admin.py (PAGE_HEAD), manage_jobs.py (HTML_PAGE,
      HTML_MSCAN_DETAIL, HTML_MSCAN_PICK, HTML_MSCAN_IMPORT), sdr_waterfall.py
      (HTML_PAGE). Acht davon trugen bisher lang="de", sechs gar kein lang-Attribut.
      Themenschalter (Beschriftung, Titel, statische Knoepfe in ui_theme.py und
      waterfall.py) auf Englisch umgestellt, vier neue Glossareintraege dazu
      (Hell/Dunkel, die beiden Umschalter-Titel).
      Bewusst unveraendert: die Zeile add('HTML_LANG = \'<html lang="de">\'') in
      tools/check_ui_lang.py bleibt deutsch, fuer immer - sie ist Eingabedaten des
      Pruefwerkzeugs fuer sich selbst (Selbsttest T5 erwartet dort einen lang-Befund).
      Wuerde sie auf "en" gesetzt, prueft das Werkzeug seine eigene Regel nicht mehr.
      Beobachtung (kein T-1810-Umfang): eb200_core/block_admin.py ist kein
      Paketmodul, sondern ein Skript mit impliziten relativen Importen
      (from param_store import ...) und erwartet laut start_eb200.py eb200_core/ als
      Arbeitsverzeichnis. "import eb200_core.block_admin" vom Repo-Wurzelverzeichnis
      schlaegt deshalb fehl (ModuleNotFoundError: param_store) - vorbestehend, durch
      T-1810 weder verursacht noch behoben. Importpruefungen gegen block_admin.py
      muessen das beruecksichtigen (cwd=eb200_core, kein Package-Import).
- [~] T-1810a Glyphe des Theme-Umschalters sichtbar machen (Abnahme im Browser steht
      aus). Befund aus der Browser-Abnahme (OE3GAS, 2026-09-24): der Umschalter zeigt
      beim ersten Laden das Mondsymbol als Textzeichen (&#9790;) sichtbar an. Sobald
      paintThemeToggle() laeuft und die Beschriftung durch das Inline-SVG ersetzt,
      ist die Glyphe praktisch unsichtbar - dunkel auf dunkelblauer Kopfleiste. Nicht
      durch T-1810 verursacht: die SVG-Glyphen und fill:currentColor stammen aus
      T-1407b, T-1810 hat nur den Text daneben getauscht - der Fehler war vorher da
      und ist erst jetzt aufgefallen.
      Behebung: .glyph faerbt sich ueber fill:currentColor, erbt also die Textfarbe
      des Knopfes; diese Vererbung kam an dieser Stelle nicht wie erwartet an.
      Statt der Ursache nachzugehen, wird die Farbe fuer genau diesen Knopf fest an
      das Kopfleisten-Token gebunden: #themeToggle .glyph { fill:var(--head-fg); },
      spezifischer als .glyph und daher ohne !important wirksam, in beiden Themes
      dieselbe Farbe wie die Beschriftung daneben.
      Richtigstellung (T-1810b): die Farbe war nie das Problem - fill berechnete
      sich auch vorher zu Weiss. Die Mondglyphe schloss keine Flaeche ein. Die
      Regel #themeToggle .glyph bleibt trotzdem bestehen, weil sie die Farbe der
      Glyphe unabhaengig von der Textfarbe des Knopfes macht; sie hat den Fehler
      nur nicht behoben.
      Regel, die stattdessen gilt: eine Glyphe, die nicht erscheint, wird zuerst
      auf ihre FLAECHE geprueft, nicht auf ihre Farbe. Zwei Boegen zwischen
      denselben Punkten mit zu kleinen Radien werden beide auf den gleichen Wert
      hochskaliert und liegen dann aufeinander - fill, Groesse und Sichtbarkeit
      im Browser sehen dabei vollkommen unauffaellig aus.
- [~] T-1810b Mondglyphe zeichnet nichts (Abnahme im Browser steht aus). Befund:
      die Mondglyphe des Theme-Umschalters schloss keine Flaeche ein und zeichnete
      daher nichts. Belegt durch Messung im Browser: das SVG ist im Knopf
      vorhanden, 14x14 px, display:inline-block, fill berechnet zu rgb(255,255,255)
      - und trotzdem war nichts zu sehen.
      Der alte Pfad "M9.2 1.6 A6.4 6.4 0 1 0 8 14.4 a5.2 5.2 0 0 1 1.2 -12.8 z"
      verbindet mit beiden Boegen dieselben zwei Punkte. Deren Abstand ist
      sqrt(1.2^2 + 12.8^2) = 12.856. Beide angegebenen Radien (6.4 und 5.2) sind
      kleiner als die Haelfte davon (6.428) und werden nach SVG-Spezifikation
      proportional vergroessert, bis der Bogen passt - beide landen damit exakt
      bei 6.428 und werden zu Halbkreisen. Der Hinweg traegt sweep=0, der Rueckweg
      sweep=1; da beim Rueckweg die Durchlaufrichtung umgekehrt ist, bezeichnen
      beide Flaggen dieselbe Bogenseite. Die zwei Boegen liegen aufeinander, die
      eingeschlossene Flaeche ist null.
      Herkunft: T-1407b, nicht T-1810 - sichtbar wurde es erst bei der Abnahme von
      T-1810.
      Nicht betroffen: die Sonnenglyphe. Ihr Mittelkreis
      (M8 4.8A3.2 3.2 0 108 11.2 3.2 3.2 0 008 4.8z) hat bei einer Sehne von 6.4
      den Radius 3.2 - das geht genau auf, und beide Boegen tragen sweep=0, liegen
      also auf gegenueberliegenden Seiten. Die Strahlen sind Rechtecke. Sie bleibt
      unveraendert.
      Behebung: neuer Pfad als Kreis minus Kreis ueber fill-rule="evenodd" -
      Mittelpunkt (8, 8) Radius 6.6 (der Mond) minus Mittelpunkt (11, 5.6) Radius
      6.0 (der ausgestanzte Teil). Mittelpunktsabstand 3.84, die Sichel ist an
      ihrer dicksten Stelle 6.6 - (6.0 - 3.84) = 4.44 Einheiten breit und oeffnet
      sich nach rechts oben. Jeder Kreis ist als zwei Halbkreise geschrieben, mit
      Radius exakt der halben Sehne - der Grenzfall, den die Spezifikation genau
      trifft - und beide Boegen tragen dieselbe Sweep-Flagge, liegen also auf
      gegenueberliegenden Seiten.
      Nachgebessert (T-1810c): die erste Fassung war sichtbar und oeffnete sich
      richtig herum, war aber mit 4,4 von 13,2 Einheiten (ein Drittel der Breite)
      zu dick und wirkte wie eine Scheibe mit Kerbe statt wie ein Mond. Ersetzt
      durch zwei gleich grosse Kreise (Radius 6,8) mit Versatz 2,55 statt
      unterschiedlicher Radien - dadurch haengt die Dicke der Sichel unmittelbar
      am Versatz und laesst sich spaeter im selben Verhaeltnis nachjustieren.
- [~] T-1810d Mondsichel als eine Kontur aus zwei Boegen statt zweier Kreise
      (Abnahme im Browser steht aus). Befund: die Fassung aus T-1810b/c zeichnete
      zwei Sicheln - die gewollte unten links und eine gespiegelte oben rechts;
      im Knopf bei 14 px verschmolzen beide zu einem Ring. Bestaetigt durch
      Ansicht am Mockup und durch die Abnahme (OE3GAS).
      Ursache - und sie war von Anfang an ein Denkfehler: fill-rule="evenodd"
      zieht den zweiten Kreis nicht ab, sondern bildet die symmetrische Differenz.
      Gefuellt wird jede Flaeche, die in genau einem der beiden Kreise liegt -
      auch der Teil des abziehenden Kreises, der ausserhalb des aeusseren liegt.
      Das ist die gespiegelte Sichel. fill-rule="nonzero" hilft nicht: bei
      entgegengesetztem Umlaufsinn hat der ueberstehende Bereich die Windungszahl
      -1, und nonzero fuellt alles ungleich null. Eine Flaeche laesst sich ueber
      beide Regeln nur ausstanzen, wenn die zweite Kontur vollstaendig innerhalb
      der ersten liegt - bei einer Sichel ist sie das nie.
      Richtige Bauweise: die Sichel ist EINE geschlossene Kontur aus genau zwei
      Boegen, die sich in den beiden Spitzen treffen - aussen der Bogen des
      grossen Kreises, innen der Bogen des abziehenden. Kein zweiter Vollkreis,
      kein fill-rule. Genau das hatte der urspruengliche Pfad aus T-1407b
      versucht und wegen zu kleiner Radien verfehlt.
      Gewaehlte Form (OE3GAS, 2026-09-24): Umfassung 240 Grad, Breite 2,4 von
      13,6 Einheiten. Aeusserer Kreis Mittelpunkt (8, 8) Radius 6,8; innerer
      Kreis Mittelpunkt (9,219 / 6,781) Radius 6,123; Spitzen (14,568 / 9,761)
      und (6,239 / 1,432).
      Regel (T-1810d): fill-rule evenodd ZIEHT NICHT AB. Bei zwei Konturen
      faerbt es die symmetrische Differenz - jede Flaeche, die in genau einer
      der beiden liegt, also auch der ueberstehende Teil der zweiten. nonzero
      verhaelt sich nicht besser: dort hat der Ueberstand die Windungszahl -1
      und wird ebenfalls gefuellt. Ein Loch laesst sich ueber beide Regeln nur
      stanzen, wenn die zweite Kontur VOLLSTAENDIG in der ersten liegt. Alles
      andere - Sichel, Ausschnitt am Rand - wird als EINE Kontur aus mehreren
      Boegen gezeichnet oder ueber mask/clipPath geloest.
      T-1810a, b und c bleiben stehen wie sie sind - sie sind der Weg, nicht das
      Ergebnis, und ihre Befunde stimmen jeder fuer sich.
- [~] T-1811 KNOWN_STRIP_BLOCK einmal an der Quelle uebersetzen, dann ueber
      tools/sync_known_block.py auf alle Server ausrollen. Gate: Byte-Gleichheit aller
      Kopien.
      Umgesetzt 2026-09-24 (Abnahme im Browser steht aus). Drei deutsche Textstellen
      im Block: ' Sender auf ' (Tooltip-Kopfzeile bei Mehrfachbelegung, je einmal in
      der Text- und der grafischen Fassung) -> ' stations on ', ' ausserhalb der
      Region' (Tooltip-Fusszeile, ausgeblendete Regionsfilter-Treffer) -> ' outside
      the region'. 'FNV: ' (Praefix der Bandzeile) bleibt unuebersetzt: FNV ist die
      Abkuerzung einer oesterreichischen Rechtsvorschrift, kein deutsches Wort im
      Fliesstext - eine Uebersetzung machte die Quelle fuer den englischsprachigen
      Nutzer unauffindbar.
      Der Block benutzt keinen der gemeinsamen Helfer (labelOf, errText, isoDate,
      cmpText, pluralWort) und braucht auch keinen: kein Datum, keine Uhrzeit, keine
      Textsortierung (sortiert wird nach Frequenz), keine Klammerplurale, die
      einzigen Zahlen (MHz mit toFixed(5), ganzzahlige kHz) sind schon
      gebietsschema-frei. "stations" steht ohne Pluralhelfer immer im Plural, weil
      der Zweig nur bei info.channels.length > 1 erreicht wird - der Einzelfall baut
      eine andere Zeile.
      Arbeitsweise: geaendert wurde ausschliesslich die Referenz waterfall.py; die
      drei Kopien (analyze_sessions.py, analyze_spectrum.py, sdr_waterfall.py)
      entstanden durch tools/sync_known_block.py, bestaetigt durch --check (Exit 0).
      Von Hand wurde in den drei Zieldateien nichts geaendert.

### Stufe 2: Uebersetzung je Server (nach absteigendem Risiko)
- [~] T-1812 waterfall.py (:8000) Teil 1: STATUS_HTML und HTML_PAGE, rund 190 Strings.
      Enthaelt auch CSS-erzeugten Text ueber content:.
      Zusaetzlich aus T-1804b uebernommen: die Sweep-Zeile im Kopfbereich ist ueber drei
      span-Elemente mit festen em-Breiten verteilt ("Sweep: N Punkte in M s
      (R Punkte/s)"). Englischer Text passt nicht in dieselben Kaesten - die Zeile wird
      bei der Uebersetzung als EIN Element mit einem Formatstring neu gebaut.
      Markup von waterfall.py traegt 111 sichtbare deutsche Textstellen; T-1812 zerfaellt
      deshalb in weitere Teile nach Bedienbereich - Kopfleiste und Long-Run-Maske,
      Werkzeugleiste und Einstellungs-Panel, Zoom-Ansicht, Skala-Leiste.
      T-1812a umgesetzt 2026-09-25 (Abnahme im Browser steht aus): Status-Seite
      (STATUS_HTML, :8000/status), 17 Textstellen. Entscheidungen: (1) "Speicher" wird
      "Parameter store", nicht "Storage" - der Abschnitt zeigt Schema-Version,
      Schreibzeitpunkt und Pfad der Parameterdatei, nicht die Aufzeichnungs-
      Speicherorte, fuer die das Glossar "storage" fuehrt; zwei verschiedene Dinge
      duerfen nicht dasselbe Wort bekommen, nur weil sie im Deutschen beide "Speicher"
      heissen. (2) "... Fehler" bekommt pluralWort - erste Stelle der Suite, an der der
      Helfer tatsaechlich zwei verschiedene Woerter unterscheidet ("1 error" / "3
      errors"), bisher war er nur Vorbereitung. (3) Unveraendert: <title>EB200
      Status</title>, 'IDN: ', 'OPT: ', ' OK, ', Gruppennamen und Schluesselnamen der
      Parameterabfragen - Geraetedaten, keine Oberflaeche. (4) 'Stand: ' bekommt keinen
      Datumshelfer, der Wert kommt bereits als ISO-Zeichenkette vom Server und wird nur
      an 'T' getrennt (T-1803b-Schreibweise).
      Fund: die vier "Leerzeichen" zwischen Datum und Uhrzeit in der Stand:-Zeile sind
      keine ASCII-Leerzeichen, sondern vier U+00A0 (NO-BREAK SPACE) - unveraendert
      uebernommen. Unsichtbare Zeichen sind damit eine eigene Fehlerklasse, der Suite
      bereits an zwei Stellen bekannt: String.fromCharCode(183) als Trennzeichen im
      Katalogstreifen, bewusst ohne Escape geschrieben, und jetzt die vier U+00A0 hier.
      Eine ALT-Zeichenkette in einem Prompt, die sichtbaren Leerraum enthaelt, ist
      deshalb ein Risiko - sie sieht richtig aus und trifft trotzdem nicht. Regel:
      Ankertext endet vor dem Leerraum, oder der Anker wird auf das Wort beschraenkt,
      das tatsaechlich getauscht wird.
      Nebenwirkung der Glossarzeile "Inventur -> inventory": in manage_jobs.py
      entstehen 6 neue Stufe-A-Befunde. Zwei davon sind echter Oberflaechentext
      (Z. 2009 "Inventur - Belegung erheben...", Z. 2014 "Ende der Inventur")
      und gehoeren nach T-1814. Die anderen vier (Z. 2141, 2142, 2265, 2584)
      sind Vergleiche gegen den PERSISTIERTEN Wert 'inventur' bzw.
      'ueberwachung' - der Suchbegriff trifft dort den Datenwert, nicht den
      Anzeigetext. Diese vier Stellen bleiben UNVERAENDERT; ihre Anzeige laeuft
      ohnehin bereits ueber EB200_LABELS.mode (inventur -> Inventory, T-1801a).
      In T-1814 werden sie entweder in eine ui-lang-ignore-Zone gelegt oder in
      die Stufe-B-Basis uebernommen.
      T-1812b umgesetzt 2026-09-25 (Abnahme im Browser steht aus): Kopfleiste,
      Long-Run-Banner und Long-Run-Maske in HTML_PAGE, 29 Textstellen, davon 25
      mit Aenderung (4 waren bereits englischtauglich: Start (MHz), Stop (MHz),
      Start, Rotation (min)) plus ein Folge-Edit an der CSS-Breite. Entscheidungen:
      (1) "Komma" wird "decimal point" - Dezimaltrenner ist laut T-1803a ueberall
      der Punkt, die deutsche Benennung im Hilfetext war schon vorher irrefuehrend.
      (2) "ZF-BW" wird "IF BW" - Sachbezeichnung (Zwischenfrequenz), keine
      Geraetebezeichnung; MTIME/LOW/NORM/HIGH/DEFAULT/D-SCAN/IFPAN bleiben, weil sie
      so im Geraet und Handbuch stehen. (3) "Speicherung" wird "Storage",
      "Ablage" wird "Storage location" (siehe Fund unten) - dieselbe Abgrenzung wie
      bei "Speicher" in T-1812a: die Legende benennt den Themenbereich, das Feld
      waehlt einen konkreten Speicherort aus storage_roots.json. (4) "Strg" wird
      "Ctrl", passend zu Shift/Enter/Esc. (5) Unveraendert: MoniScope, D-SCAN,
      IFPAN, Long Run, EB200, Speed, Span, Demodulation, Start (MHz), Stop (MHz),
      Rotation (min), WAV, LOW, NORM, HIGH, MTIME, DEFAULT, WS3 11.2, alle
      value-Attribute. (6) Breitenpruefung vorab: die sechs Beschriftungen in
      <span class="lk"> (7,5 em) sind alle kuerzer oder gleich lang wie vorher -
      bis auf einen Fall, siehe Fund.
      Korrektur nach Ruecksprache: zwei der urspruenglich vorgeschlagenen
      UEbersetzungen widersprachen bereits bestehenden, verbindlichen
      Glossar-Regeln und wurden vor dem Commit berichtigt. "Kurztext" (Long-Run-
      Feld lrLabel) ist per Abschnitt 10.6 bereits als "Label" festgelegt
      (Vereinheitlichung mit "Titel"/"Kurztext" ueberall in der Suite) - nicht
      "Short text" wie zunaechst vorgeschlagen. "Ablage" (Feld lrStorage,
      Auswahl aus storage_roots.json) ist per Zeile "Ablage (Storage-Root) |
      storage location" bereits festgelegt - nicht "Location". Beide
      Glossarzeilen aus dem urspruenglichen Vorschlag entfallen ersatzlos,
      die bestehenden Regeln gelten unveraendert.
      Fund: "Storage location" ist laenger als "Dezimierung", dem bisher
      laengsten Text im 7,5-em-Kasten (<span class="lk">). Genau der Fall, vor
      dem T-1804b gewarnt hat - Layout und Wort wurden gegeneinander abgewogen,
      das Layout hat nachgegeben: #longrunPanel .lk auf 9,5 em verbreitert statt
      das Glossar zu beugen. Betroffen sind alle sechs Beschriftungen in diesem
      Kasten (Type, Start (MHz), Center (MHz), Start, Decimation, Storage
      location).
      Fortschrittsmass: von 111 sichtbaren deutschen Textstellen im Markup von
      waterfall.py sind nach T-1812a und T-1812b 46 erledigt (17 Status-Seite,
      29 hier), es bleiben 65. ("46 Textstellen" ist nicht gleich "46 Edits" -
      vier der 29 Textstellen brauchten keine Aenderung, weil sie schon
      englischtauglich waren.)
      Nebenbefunde (kein T-1812b-Umfang, fuer T-1813/T-1815 vermerkt): die neue
      Glossarzeile "Komma (Dezimaltrenner)" trifft in eb200_core/block_admin.py
      Zeile 3216 einen bestehenden Hilfetext ("...von Hand einfuegen
      (Dezimalpunkt oder -komma).") - echter Text fuer T-1815. Die neue
      Glossarzeile "Empfaenger" trifft in waterfall.py Zeile 7116 eine weitere,
      hier nicht bearbeitete Meldung ("Empfaenger gesperrt - Long-Run-Job
      laeuft") - Text fuer T-1813.
      Nachtrag T-1812b1 (Abnahme OE3GAS): zwei Stellen nachgezogen.
      (a) Der Titel der Long-Run-Maske steht als content: in einer CSS-Regel und
          wurde beim Uebersetzen des Markups uebersehen. Regel: CSS ist nicht nur
          Layout - content: erzeugt sichtbaren Text, attr() holt ihn aus einem
          Attribut. Beides gehoert in jede Erfassung und in das Abschluss-Gate
          T-1826.
      (b) Die Werkstromnummer "(WS3 11.2)" stand im Hinweistext der Dezimierung.
          Interne Kennungen sind Quelltextsprache und werden beim Uebersetzen
          entfernt, nicht mituebersetzt. Weitere solche Stellen (T-505, T-505b in
          title-Attributen der Werkzeugleiste) folgen in T-1812c.
      T-1812c umgesetzt 2026-09-25 (Abnahme im Browser steht aus): Werkzeugleiste
      der Uebersicht (<div id="toolbar"> samt #detectCfg) in HTML_PAGE, 20 Edits,
      23 ALT-Zeichenketten (drei der 20 Edits bestehen aus zwei Teilstuecken -
      title und Knopftext). Entscheidungen: (1) deutsche Bindestrich-
      Zusammensetzungen werden im Englischen zwei Woerter (Klick-Marker -> Click
      marker, Auto-Marker -> Auto marker, Markerliste -> Marker list) - eine
      durchgekoppelte Form laese sich wie ein Eigenname. (2) "Zum Anfang" wird
      "Jump to live" - der Knopf (jumpLiveBtn) springt an den Live-Rand, nicht an
      den Anfang der Aufzeichnung; die deutsche Beschriftung war schon vorher
      irrefuehrend. (3) T-505 und T-505b entfallen aus den beiden title-
      Attributen (Beschluss T-1812b1) - die dort gezaehlte Liste interner
      Kennungen in sichtbarem Text ist damit vollstaendig abgearbeitet. (4)
      "0 Marker" wird "0 markers" (Vorbelegung; pluralWort folgt mit der JS-Seite
      in T-1813). (5) Unveraendert: Pause, Restart, Regional, MoniScope, IFPAN,
      SDR, JPG (als Dateiendung/Formatname in "Save as JPEG"), dB, Portnummern,
      known_freq/, station.json. (6) Breitenpruefung vorab ergab keinen
      CSS-Bedarf - alle sechs Beschriftungen in den 8,6-em-Kaesten sind gleich
      lang oder kuerzer als vorher.
      Korrektur nach Ruecksprache: drei Uebersetzungen widersprachen bereits
      verbindlichen Glossarzeilen und wurden vor dem Commit berichtigt.
      "Einstellungen ein-/ausblenden" ist per Altbestand "Show/hide settings"
      (Schraegstrichform, ohne Artikel) - nicht "Show or hide settings" wie
      zunaechst gesetzt; die Regel gilt jetzt fuer ALLE ein-/ausblenden-Stellen
      (auch "Frequenzraster ein-/ausblenden" und "Markerliste ein-/ausblenden"),
      neue Glossarzeile "ein-/ausblenden -> show/hide" haelt das fest. "Als JPG
      speichern" ist per Altbestand "Save as JPEG" - die Dateiendung bleibt .jpg,
      die Beschriftung nennt nur das Format. "Löschen" (clearWfBtn) bleibt
      dagegen bei "Clear": der Knopf setzt nur die Wasserfall-Anzeige zurueck
      (Historie leeren, mit aktuellem Parameter-Header neu beginnen) und
      entfernt keine Daten - "Delete" haette einem Benutzer zu Recht Angst
      gemacht. Neue Glossarzeile "Loeschen (Anzeige zuruecksetzen) -> clear" mit
      der Abgrenzung: Delete nimmt etwas weg, Clear raeumt eine Anzeige auf. Die
      Unterscheidung gilt ab jetzt fuer die ganze Suite.
      Befund: die Zoom-Werkzeugleiste (#toolbarZ) enthaelt Knoepfe, die mit
      denen der Uebersichts-Werkzeugleiste (#toolbar) wortgleich sind - der
      Einstellungsknopf (panelToggle / panelToggleZ) war der erste gefundene
      Fall, dazu identisch panelToggle/pauseBtn <-> pauseBtnZ (zwei Zeilen
      insgesamt, identisch bis auf das "Z" im id-Attribut). Eine ALT-
      Zeichenkette, die nur den Knopftext ohne id enthaelt, trifft dann beide.
      Regel: Anker in waterfall.py schliessen die id mit ein, sonst ist nicht
      entscheidbar, welche der beiden Ansichten gemeint ist. #toolbarZ selbst
      bleibt unveraendert und wird in T-1812e uebersetzt.
      Nebenwirkung zweier neuer Glossarzeilen: "Aufzeichnungen" trifft in
      manage_jobs.py 12 bestehende Fundstellen (Seitentitel, Tab-Id/-Ueberschrift
      von :8050 selbst, mehrere Navigationslinks "Aufzeichnungen / Definitionen")
      - Text fuer T-1814. "Mindestdauer" trifft in manage_jobs.py Zeile 2044
      sowie in analyze_mscan.py an vier Stellen (Z. 241, 455, 753, 1346) ein
      voellig anderes Konzept: die Mindestdauer-Filterung von MSCAN-Kanaelen,
      nicht die Mindestdauer der Auto-Marker-Erkennung - Text fuer T-1814/T-1817.
      "automatisch" trifft in analyze_sessions.py Zeile 680 eine weitere, hier
      nicht bearbeitete Stelle (Skala-Automatik-Knopf) - Text fuer T-1818.
      Fortschrittsmass: nach T-1812a, b, b1 und c sind 67 von 111 sichtbaren
      deutschen Textstellen im Markup von waterfall.py erledigt (17 + 29 + 1 +
      20), es bleiben 44. Die in T-1812b1 gezaehlten internen Kennungen in
      sichtbarem Text (T-505, T-505b, WS3 11.2) sind damit vollstaendig entfernt.
      T-1812d umgesetzt 2026-09-25 (Abnahme im Browser steht aus): Einstellungs-
      Panel der Uebersicht (<div id="sidePanel"> mit seinen fuenf fieldset-
      Bloecken) in HTML_PAGE, 14 Edits (Edit 10 und 11 zu einem Block
      zusammengefasst, siehe Befund unten), 17 Textstellen; Start (MHz) und
      Stop (MHz) blieben unveraendert. Entscheidungen: (1) "Verstaerkung" wird
      "Gain", "Regelung" wird "Control" - Legende benennt die Groesse, das Feld
      darunter ihre Regelungsart; zusammen "Gain / Control: AGC". AGC/MGC
      bleiben Geraetekuerzel. (2) "Handbuch 3.18.3.1" wird "manual 3.18.3.1",
      "Annex C" bleibt (Eigenname des Rohde-&-Schwarz-Befehlsanhangs); der
      SCPI-Befehl [SENSe:]FREQuency:DSCan:SPEed bleibt unangetastet -
      Geraetesprache. (3) "BW-ZOOM" bleibt Funktionsname, nur das Verb wird
      uebersetzt: "BW-ZOOM einschalten" -> "Enable BW-ZOOM"; die JS-seitige
      Ausschalt-Fassung "Disable BW-ZOOM" folgt mit T-1813. (4) "Aufnahme" wird
      "Recording", "Session Aufnahme" wird "Session recording" - konsistent mit
      "Job / recording" aus T-1812b. (5) Breitenpruefung vorab: das 330-px-Panel
      brauchte keine CSS-Aenderung, "Extra zoom with SDR" ist kuerzer als
      "Zusatz-Zoom mit SDR".
      Befund: dritter Fall der Uebersicht/Zoom-Doppelung nach T-1812c
      (Einstellungsknopf) und mit demselben Muster - die Verstaerkungsregelung
      existiert zweimal: dscanGainMode im Uebersichts-Panel (hier bearbeitet),
      gainMode im Zoom-Panel (T-1812e, eigener title "...fuer amplitudentreue
      IQ-Aufnahmen noetig"). Beide tragen das wortgleiche Label "Regelung". Ein
      Anker, der nur das Label enthaelt, traf beide - Edit 10 und 11 wurden
      deshalb zu einem Zweizeilen-Block zusammengefasst, der ueber den
      title-Text eindeutig wird. Regel (gilt jetzt fuer beide Faelle): Anker in
      waterfall.py schliessen die id oder eine unterscheidende Nachbarzeile
      ein, nie die Beschriftung allein.
      Pruefung auf Title Case (Rueckfrage OE3GAS bei der Abnahme von T-1812c):
      alle bisher uebersetzten Beschriftungen in waterfall.py (T-1810, T-1812a
      bis d) durchgesehen - keine verstoesst gegen die Sentence-Case-Regel. Die
      Regel selbst stand bereits vor T-1812d im Glossar (Abschnitt
      "Schreibweisen", mit den Beispielen "Save as JPEG"/"Apply range"/"Center
      frequency"); ein neuer Regelabschnitt war deshalb nicht noetig.
      Fortschrittsmass: nach T-1812a, b, b1, c und d sind 84 von 111 Textstellen
      erledigt (17 + 29 + 1 + 20 + 17), es bleiben 27 - Zoom-Ansicht, ihr Panel,
      die Canvas-Overlays und die Skala-Leiste. "Enable BW-ZOOM" und
      "0 markers" sind statische Vorbelegungen; ihre umschaltenden Gegenstuecke
      ("Disable BW-ZOOM", pluralWort fuer die Markerzahl) folgen mit der
      JS-Seite in T-1813.
      T-1812e umgesetzt 2026-09-25 (Abnahme im Browser steht aus): Zoom-Ansicht,
      Canvas-Overlays und Skala-Leiste in HTML_PAGE, 25 Edits (Edit 19 als
      Zweizeilen-Block, Edit 25 in zwei Teilstellen Skala min/max), 27
      Textstellen. Entscheidungen: (1) "Pegel" wird "Level", "Skala min/max"
      werden "Scale min/max" - Achsenbeschriftungen des S-Meters bzw. der
      Farbskala. (2) "Mithoeren" wird "Monitor" - die drei Aufnahmeknoepfe
      sitzen in Kaesten halber Panelbreite mit text-overflow:ellipsis; "Monitor"
      ist kuerzer als "Mithoeren", "Spectrum" gleich lang wie "Spektrum", kein
      Platzproblem. (3) "Squelch ein" wird "Squelch on" - Squelch bleibt
      Funkfachsprache, unveraendert in beiden Sprachen. (4) Unveraendert: Span,
      Demodulation, Modulation, Squelch, Auto, Pause, Long Run, Audio, Max -,
      MHz, dB&micro;V, AGC, MGC, die SVG-Achsenzahlen, die Platzhalter
      __IFPAN_MODE_OPTIONS_HTML__ und __IFPAN_BW_OPTIONS_HTML__.
      Erwartete Falle bestaetigt: "warte auf erstes Frame ..." kam zweimal vor
      (#meta-o Uebersicht, #meta-z Zoom) - beide Anker ueber die id eindeutig
      gemacht, beide getroffen. "Panel einklappen" kam nach T-1812d nur noch
      einmal vor (Zoom-Gegenstueck panelCloseZ) und wurde hier erledigt.
      Sweep-Zeile (T-1804b-Vermerk) GEPRUEFT UND ENTFALLEN: die Zeile ist auf
      drei span-Elemente mit festen Breiten 10/4,5/8 em verteilt und war seit
      T-1804b als Strukturfall vorgemerkt - englischer Text sollte dort nicht
      hineinpassen und die Zeile als EIN Formatstring neu gebaut werden. Die
      Pruefung widerlegt das: " Punkte in" und " points in" sind beide zehn
      Zeichen lang, " Punkte/s)" und " points/s)" ebenfalls, die Wortstellung
      ist in beiden Sprachen dieselbe. Fragmentweise uebersetzt, die Kaesten
      bleiben unveraendert, der geplante Formatstring-Umbau ENTFAELLT. Von den
      zwei aus T-1804b uebernommenen Strukturstellen ist damit eine erledigt;
      die zweite (Bandbreitenmeldung "X kHz mit FM nicht moeglich") liegt im
      JavaScript und folgt in T-1813.
      Nebenwirkung neuer Glossarzeilen (kein T-1812e-Umfang): "Zurueck zur
      Uebersicht" trifft analyze_sessions.py Zeile 672 (eigener
      Rueck-Navigationsknopf) und eb200_core/block_admin.py Zeile 4188 - Text
      fuer T-1818/T-1815. "Zwischenablage" trifft analyze_mscan.py Zeilen 467
      und 470 (Kopieren-Fehlermeldungen) - Text fuer T-1817.
      "Duplikatpruefung" trifft eb200_core/block_admin.py Zeile 3247 - Text fuer
      T-1815.
      Damit ist T-1812 inhaltlich abgeschlossen: 111 von 111 sichtbaren
      deutschen Textstellen im Markup von waterfall.py erledigt. T-1812 bleibt
      [~], bis die Abnahme im Browser vorliegt. Verbleibende Stufe-A-Befunde in
      waterfall.py laut check_ui_lang.py: 160 - Ausgangszahl fuer T-1813
      (JS-Client und Handler-Meldungen).
      Nachtrag T-1812e1 (Abnahme OE3GAS): die Sweep-Zeile zeigte sichtbare
      Luecken hinter den Labels. Ursache war nicht die Uebersetzung, sondern die
      feste Breite der .fld-Felder, die deutlich groesser war als ihr Inhalt -
      im Deutschen genauso, nur nie bemerkt. Die feste Breite liegt jetzt auf der
      Zahl statt auf dem Feld, rechtsbuendig; Zweck (Labels springen nicht) ist
      erhalten, die Luecke ist weg.
      Regel: eine feste Breite gehoert auf das Element, dessen INHALT sich
      aendert - also auf die Zahl -, nicht auf die Gruppe aus Zahl und Text.
      Sonst bezahlt man die Stabilitaet mit Leerraum, der mit der Laenge des
      Textes waechst.
- [~] T-1813 waterfall.py (:8000) Teil 2: JS-Client und Handler-Meldungen, rund 120
      Strings, davon rund 24 JSON-Fehlerantworten.
      Zusaetzlich aus T-1804b uebernommen: die Bandbreitenmeldung
      "X kHz mit <Modus> nicht moeglich (max. Y kHz)" ist einer der wenigen echten
      Umstellungsfaelle - im Englischen wandert die Modusangabe ans Satzende
      ("X kHz not possible with <mode>"). Beim Uebersetzen als ein Formatstring bauen,
      nicht fragmentweise ersetzen.
      Schnitt in vier Teile nach Thema (160 Stufe-A-Befunde in 107 Zeilen bei
      Uebernahme): T-1813a Long Run (Ablagen, Budget, Job anlegen, Status-Banner,
      ~28 dt. Zeilen), T-1813b D-SCAN (Messzeit, Sweep-Schaetzung, Bereich
      uebernehmen, Parametertafel, JPG-Export, ~20), T-1813c Zoom (Abstimmung,
      Frequenzeingabe, Sperrmeldungen, BW/Modulation-Hinweise, ~21), T-1813d
      Aufnahme/Mithoeren/Marker/SDR/SSE-Verteiler (~21, enthaelt die bei der
      Abnahme von T-1812e aufgefallenen Stellen paintSessionBtn, Mithoer-Knopf,
      detectListTitle).
      T-1813a umgesetzt 2026-09-25 (Abnahme im Browser steht aus): Long Run im
      JavaScript, 20 Edits. Entscheidungen: (1) Sechs Zaehler im Status-Banner
      bekommen pluralWort (Sweeps, Frames, Dateien, Zyklen, Aufnahmen, Kanaele) -
      im Deutschen war Ein-/Mehrzahl formgleich, im Englischen nicht; ohne den
      Helfer stuende dort "1 files". Erste Task, in der der Helfer aus T-1804a
      in nennenswerter Zahl gebraucht wird. (2) Die Phasennamen folgen
      EB200_LABELS.phase (wartet -> Waiting/waiting, ende -> Finishing/
      finishing) im Wortlaut, ohne auf labelOf() umgestellt zu werden - die
      Bannertexte sind Saetze mit Zahlen, keine reinen Beschriftungen; "Rueckbau
      ..." wird "finishing ...". (3) "WS3 10.1" entfaellt aus dem sichtbaren
      Planungswert-Hinweis (Regel aus T-1812b1); die beiden Erwaehnungen in
      JS-Kommentaren bleiben deutsch. (4) "Ablage" bleibt in der Optionsliste
      selbst unuebersetzt (dort stehen Pfade); uebersetzt werden nur die
      Klammerzusaetze "(frei: ... GB)" und "(nicht erreichbar)".
      Messfehler, gefunden in T-1813a: (Select-String ... ).Count zaehlt ZEILEN
      mit Treffer, nicht Treffer. Zeilen mit zwei oder mehr Vorkommen wurden
      dadurch unterzaehlt - die bisherigen pluralWort-Zahlen in den Berichten
      (28 statt 31) waren zu niedrig. Richtig ist
      (Select-String ... -AllMatches).Matches.Count.
      Regel: jede Zaehlung von VORKOMMEN braucht -AllMatches; ohne den Schalter
      zaehlt PowerShell Zeilen. Fuer Gates, die "genau eine Fundstelle" pruefen,
      ist der Unterschied folgenlos - fuer Summen ueber eine ganze Datei nicht.
      Nebenwirkung neuer Glossarzeilen (kein T-1813a-Umfang): "nicht erreichbar"
      trifft manage_jobs.py (Z. 975, 1203, 1884, 1888) und sdr_waterfall.py
      (Z. 630) - Text fuer T-1814/T-1818. "bis Stopp" trifft manage_jobs.py
      (Z. 1037, 2076, 2422) und analyze_mscan.py (Z. 408) - Text fuer
      T-1814/T-1817. "Zyklen" trifft manage_jobs.py (Z. 1121, 1347, 2427),
      analyze_mscan.py (Z. 415) sowie je einen bestehenden pluralWort-Aufruf in
      analyze_sessions.py (Z. 2362) und analyze_spectrum.py (Z. 2149) - Text
      fuer T-1814/T-1817/T-1818. "Startzeit" trifft manage_jobs.py (Z. 2623) -
      Text fuer T-1814.
      T-1813b umgesetzt 2026-09-25: D-SCAN im JavaScript, 17 Edits. Entscheidungen:
      (1) Der Kommentar ueber dem BW-ZOOM-Knopf zitiert die Texte der Parametertafel
      woertlich - beim Uebersetzen der Tafel wird das Zitat mituebersetzt (deutscher
      Kommentar, englisches Zitat). Neue Regel dazu in docs/Glossar-EN.md Abschnitt 11.
      (2) "BW-Zoom" wird einheitlich "BW-ZOOM", wie Knopf und Funktionsbezeichnung.
      (3) "Verweildauer" wird "dwell time" (stehender Fachbegriff, nicht "lingering
      time"). (4) Zwei weitere pluralWort-Aufrufe fuer Kanaele (Sweep-Dauer-Schaetzung,
      Rueckfrage bei langen Sweeps) - sonst "1 channels" bei einem Kanal. (5) BW-ZOOM-
      Knopf jetzt in beiden Zustaenden englisch: "Enable BW-ZOOM" als statische
      Vorbelegung aus T-1812d, "Disable BW-ZOOM"/"Enable BW-ZOOM" als Umschaltung
      hier - macht "Enable BW-ZOOM" im gerenderten String zwei Treffer (Vorbelegung
      plus Ternary-Alternative), nicht einen.
      Gate-Abweichung G5 (Auftraggeber-Prompt): Soll "1 1" fuer
      s.count('Enable BW-ZOOM'), s.count('Disable BW-ZOOM'); Ist "2 1". Ursache:
      die Umschaltzeile enthaelt beide Woerter im selben Ternary, zusaetzlich zur
      Vorbelegung aus T-1812d - macht "Enable BW-ZOOM" zwangslaeufig zwei Treffer.
      Nicht behoben (Gate wird nie durch Anpassen des geschriebenen Textes erfuellt);
      der Soll-Wert war falsch, nicht die Datei.
      T-1813c umgesetzt 2026-09-26: Zoom, Abstimmung und Sperrmeldungen im
      JavaScript, 19 Edits. Entscheidungen: (1) "gesperrt" wird "locked", nicht
      "blocked" - die Sperre ist ein Besitzzustand (ein Long-Run-Job haelt den
      Empfaenger), keine Zurueckweisung; dasselbe Wort steht bereits in
      EB200_LABELS.err_code und in setLongrunLock. (2) "Abgelehnt:" wird
      "Rejected:" - der Gegenbegriff, weil der Server eine Anfrage zurueckweist;
      Sperre und Ablehnung sind zwei verschiedene Dinge mit zwei verschiedenen
      Woertern. (3) "Breite" wird "width", aber NICHT ins Glossar aufgenommen -
      siehe eigener Vermerk unten. (4) Kein pluralWort fuer
      "${f.levels.length} points" - das ist die Bin-Zahl eines IFPAN-Frames
      (im Betrieb 1001), geht nie durch 1; der Helfer kommt nur dorthin, wo ein
      Zaehler tatsaechlich bei eins stehen kann. (5) Unveraendert: "Zoom (IFPAN)",
      AFC, USB, LSB, CW, IQ, MHz, kHz, GHz, Fehlercode "-221 No AFC with SSB",
      ${mode}.
      Die zweite in T-1804b vorgemerkte Strukturstelle ist erledigt, ohne dass
      Struktur angefasst wurde: die Bandbreitenmeldung war bereits eine
      Verkettung aus vier Fragmenten, und die englische Wortstellung entsteht,
      indem zwei davon getauscht werden. Zusammen mit der Sweep-Zeile (T-1812e)
      gilt damit fuer BEIDE vorgemerkten Faelle: die Annahme, englische Saetze
      erzwaengen eine andere Bauweise, hat sich nicht bestaetigt. Die
      Entscheidung aus T-1804, Verkettungen NICHT vorsorglich aufzuloesen, war
      rueckblickend richtig - haette man es getan, waere die Arbeit an 61
      Stellen umsonst gewesen.
      "Breite" wird NICHT ins Glossar aufgenommen, obwohl es uebersetzt wird.
      Grund: der Bezeichner formatBreite bleibt deutsch wie alle Bezeichner, und
      ein Suchbegriff "Breite" meldete ihn dauerhaft als Fundstelle - eine, die
      nie verschwinden darf und T-1826 am Erreichen der Null hindern wuerde.
      Regel: ein Wort, das sowohl in der Oberflaeche als auch in einem
      Bezeichner vorkommt, gehoert nicht in die Suchliste. Die Entscheidung wird
      stattdessen hier festgehalten.
      Gate-Abweichung G3 (Auftraggeber-Prompt): Soll "4 4" fuer
      s.count('Rejected: '), s.count('locked - '); Ist "4 5". Ursache: der Prompt
      zaehlte "vier Sperrmeldungen", listete aber selbst fuenf auf (Empfaenger,
      Feinabstimmung zweimal, Frequenzeingabe zweimal) und bat ausdruecklich um
      Nachpruefung. Nachgezaehlt an den fuenf Fundstellen in waterfall.py (vor dem
      Edit: Zeilen 7122, 7219, 7270, 7313, 7317) - es sind tatsaechlich fuenf.
      Nicht behoben (Gate wird nie durch Anpassen des geschriebenen Textes
      erfuellt); der Soll-Wert war falsch, nicht die Datei.
      T-1813d umgesetzt 2026-09-26: Aufnahme, Mithoeren, Marker, SDR und SSE im
      JavaScript, 18 Edits. Damit ist T-1813 inhaltlich abgeschlossen; offen bleibt
      nur T-1813e (Kommentarzitate). Entscheidungen: (1) "Weiter" wird "Resume" -
      Gegenstueck zu "Pause", nicht "Continue" (das ist die Antwort auf eine
      Rueckfrage, T-1813b). (2) "Mithoeren" wird "Monitor" (Knopf, Handlung),
      "Monitoring" (Tooltip, Zustand) - dieselbe Trennung wie beim BW-ZOOM-Knopf
      aus T-1813b. (3) Drei weitere pluralWort-Aufrufe (Sweeps REC-Pille, Sweeps
      Statuszeile, Marker-Zaehler); "0 markers" bleibt eine feste Zeichenkette, die
      Zahl steht im Quelltext. (4) "Session beendet" wird "Session finished",
      "Aufnahme beendet" wird "Recording finished", beide einheitlich mit
      "Long run finished - analysis:" aus T-1813a. (5) Die Spaltenkoepfe der
      kopierten Markerliste (Zeit/MHz/dBuV) werden mituebersetzt - landen zwar nur
      in Zwischenablage/CSV, nicht im DOM, aber die Overlay-Tabelle traegt seit
      T-1812e bereits "Time"; zwei Schreibweisen fuer dieselbe Spalte waeren ein
      Fehler. (6) Unveraendert: REC, SDR, MHz, dBuV, "Sweep:" in der
      Fortschrittsanzeige, D-SCAN, ${d.label}, alle Pfade.
      Damit sind auch die drei bei der Abnahme von T-1812e gefundenen Stellen
      erledigt (paintSessionBtn/Session-Knopf, Mithoer-Knopf-Beschriftung,
      detectListTitle) - Beleg dafuer, dass ein uebersetztes Markup nichts nuetzt,
      solange das Skript es bei jedem Laden wieder ueberschreibt.
      Vorpruefungs-Abweichung (Auftraggeber-Prompt, Edit 16): ALT/NEU-Block fuer
      die "Aufnahme beendet"-Zeile (#meta-z) war im Prompt mit 6/8 Leerzeichen
      Einrueckung zitiert, die Datei hatte an der einzigen Fundstelle (damals
      Zeile 9153-9154) tatsaechlich 4/6 Leerzeichen - reiner Whitespace-Unterschied,
      Text sonst wortgleich und eindeutig einer einzigen Stelle zuzuordnen. Mit der
      tatsaechlichen Einrueckung der Datei editiert, nicht mit der des Prompts.
      Gate G7 (Stufe-A-Bestand waterfall.py, Uebergabe an T-1826): vorher 70 in 56
      Zeilen, nachher 40 in 39 Zeilen. Verbleibend:
      - Zeile 4780: Label "Modulation" in der Zoom-Parametertafel. Laut Glossar
        Abschnitt 10.4 offene Vereinheitlichung "Demodulation/Modulation" ->
        "demod"; kein T-1813d-Umfang (statisches Markup, keine der 18 Edits).
      - Zeilen 6095 (2x), 6110, 6111, 6162, 6173, 6174, 6176, 6257, 6258, 6292,
        6295, 6296, 6298, 6304, 6305, 6324, 6346, 6371, 6401, 6442, 6453, 6516,
        6517, 6527, 6533, 6536, 6539, 6542, 6543, 6544, 6558, 6710, 6712, 6713,
        6715 (36 Treffer): deutsche Bezeichner/Variablennamen (raster, kanal,
        dienst, zuweisung, verfuegbar, notiz, titel, ...) im geteilten
        Katalog-Streifen-Block (KNOWN_STRIP_BLOCK, Zeilen 5970-6807). Nur ueber
        waterfall.py als Referenz plus tools/sync_known_block.py aenderbar - kein
        T-1813-Umfang, eigene Aufgabe.
      - Zeilen 8728, 8730, 8731 (3 Treffer): Fehlalarm des Stoppwort-Scanners auf
        die Eigenschaft p.dem (Kurzform fuer "Demodulation", EB200-Parametername
        in syncDeviceParams) - kein Anzeigetext, kein Uebersetzungsbedarf.
- [x] T-1813d1 (2026-09-26) Nachtrag zu T-1813d, drei Befunde aus der Abnahme von
      T-1813c/d durch OE3GAS. Keine Uebersetzung. (1) BUG-0005: Klick und Schwenk im
      IFPAN-Zoom umgingen die Abstimmsperre - startZoom() lief ueber
      attachSelection an tuneWheel und freqInputGesperrt vorbei und verstimmte den
      Empfaenger mitten in einer laufenden Audio-/Spektrum-Aufnahme, weil der
      Server eine rein lokale Aufnahme nicht kennt und nicht abweisen kann. Behoben
      durch die gemeinsame Funktion abstimmenGesperrt(was), die jetzt von
      tuneWheel, freqInputGesperrt UND startZoom gefragt wird; der Zoom-Einstieg
      aus der Uebersicht bleibt erlaubt. (2) #freqEcho.bad nutzte --danger (fuer
      helle Flaechen gedacht) in der dunkelblauen, in beiden Themes dunklen
      Kopfleiste - praktisch unlesbar. Neues Token --head-danger in beiden
      :root-Bloecken von eb200_core/ui_theme.py, unmittelbar nach --head-border.
      (3) Der Bandbreitenhinweis bei USB/LSB/CW > 9 kHz verschwand, bevor er lesbar
      war, weil checkModeBwCombo() bei jedem Frame aus syncZoomSettings/
      syncDeviceParams erneut laeuft und das Geraet die Auswahlfelder zurueckzieht.
      Mindeststandzeit bwHintHoldUntil, gleiches Muster wie noticeHoldUntil fuer
      #meta-o.
      Regel (T-1813d1): eine Sperre wird an EINER Stelle entschieden, und jeder
      Pfad, der den gesperrten Zustand veraendern kann, fragt sie. Zwei getrennte
      Pruefungen derselben Bedingung sind immer eine zu wenig, sobald ein dritter
      Pfad dazukommt - hier war es der Klick, der neben Mausrad und Eingabefeld
      stand.
      Regel (T-1813d1, Farben): Elemente in der Kopfleiste brauchen eigene
      Tokens. --danger, --success und Verwandte sind fuer helle Flaechen
      bemessen; die Kopfleiste ist in BEIDEN Themes dunkel. Neu: --head-danger.
      Vorpruefungs-Abweichung (Auftraggeber-Prompt): erwartet war "der Commit aus
      T-1813d" als HEAD; tatsaechlich war HEAD 08e0143 (T-1813c), weil T-1813d
      selbst noch nicht umgesetzt ist (siehe Eintrag oben, weiterhin offen). Alle
      neun ALT-Anker des Prompts wurden dennoch je genau einmal in der HEAD-Fassung
      (T-1813c) gefunden und passten wortgleich - die im Prompt zitierten Stellen
      (Sperrtexte "locked - long-run job running"/"locked - recording running",
      IFPAN_BW_MAX_HZ, die Bandbreitenmeldung) stammen bereits aus T-1813c, nicht
      aus dem noch fehlenden T-1813d. Fortgefahren, da die Vorbedingung sich als
      Bezeichnungsungenauigkeit ("T-1813d" statt "T-1813c") erwies und die
      tatsaechlich benoetigten Ankertexte vollstaendig und eindeutig vorlagen.
      T-1813e umgesetzt 2026-09-26: Kommentare, die Oberflaechentexte zitieren,
      drei Edits, kein Oberflaechentext veraendert. Damit ist T-1813 (und
      waterfall.py als Ganzes) inhaltlich abgeschlossen, offen bleibt nur die
      Browser-Abnahme - T-1813 und T-1812 daher weiterhin auf [~], nicht [x].
      Dreiteilung fuer Anfuehrungszeichen in Kommentaren (neu im Glossar):
      (a) Zitat einer Anzeigezeichenkette - wird mituebersetzt; (b) Hervorhebung
      eines Begriffs in deutscher Prosa - bleibt deutsch; (c) historischer
      Verweis ("vormals ...") - bleibt deutsch, der alte Text WAR deutsch.
      V1 (alle Kommentarzeilen in waterfall.py mit einem Zitat in
      Anfuehrungszeichen, `"..."` mit mindestens 3 Zeichen): 110 Fundstellen.
      Davon 3 in diesem Prompt bearbeitet (Edits 1-3), 2 laut Prompt ausdruecklich
      ausgenommen (Hervorhebung "läuft"-Zustand, identisches Wort "D-SCAN"),
      1 kein echter Kommentar (Regex-Fehltreffer auf eine CSS-Selektorzeile).
      Von den restlichen 104: 3 Zitate sind bereits aktuell und korrekt ("FNV: ",
      "BW-ZOOM on"/"off", "Long Run" - keine Aenderung noetig), 25 Hervorhebungen
      deutscher Prosa (b), 1 weiterer historischer Verweis (c, Bug-Befund
      2026-09-03 "Starte neu"), 65 technische/Protokoll-/Code-Zitate (SCPI-
      Fehlertexte, JSON-/Python-Feldnamen, persistierte Datenwerte wie "laeuft"/
      "beendet"/"overview", CLI-Beispiele) - diese passen in KEINE der drei
      Kategorien, weil sie keinen App-UI-Text zitieren; das Schema braucht dafuer
      ggf. eine vierte Kategorie, Entscheidung beim Auftraggeber.
      Wichtigster Befund: 11 Zitate zeigen auf ueberholten Text (Zeilen 1253, 4897,
      6566, 7748, 7775, 7823, 8002, 8187, 8394, 8580, 10179) - die Frage "sind die
      fuenf bekannten Stellen alles?" ist damit eindeutig NEIN, es sind mehr als
      doppelt so viele. Keine davon wurde geaendert (nicht Auftrag dieses Prompts);
      vollstaendige Liste mit Fundstelle vs. aktuellem Text im Sitzungsbericht.
      Nebenbefund: Zeile 8580 zitiert "automatisch" wie Edit 2, ist aber ein
      zweites, im Prompt nicht genanntes Vorkommen - Ursache des Gate-G3-Fehlers
      (Ist 1, Soll 0). Nicht behoben (kein Auftrag, Gates werden nie durch
      Nachbessern am geschriebenen Text erfuellt).
      Restbestand waterfall.py nach T-1813e (Stufe A unveraendert bei 40 in 39
      Zeilen, siehe T-1813d):
      - 1 echte Fundstelle: Label "Modulation" im Zoom-Panel. Haengt an der
        offenen Vereinheitlichung Demodulation/Modulation im Glossar §10.4 und
        wird entschieden, wenn block_admin.py (T-1815) dieselbe Frage stellt.
      - 36 deutsche BEZEICHNER im geteilten Katalogstreifen (raster, kanal,
        dienst, zuweisung, notiz, titel). Bezeichner bleiben deutsch - diese
        Fundstellen duerfen nie verschwinden. Da der Block in vier Dateien
        liegt, sind es suiteweit 144.
      - 3 Fehlalarme auf die Eigenschaft p.dem (EB200-Parametername).
      T-1826 kann "Stufe A = 0" daher NICHT als Abschlusskriterium verwenden,
      solange das Pruefwerkzeug Bezeichner nicht von Anzeigetext unterscheidet.
      Die Entscheidung darueber faellt, wenn alle Server uebersetzt sind - dann
      ist die Groesse des Problems bekannt.
      T-1813e1 umgesetzt 2026-09-26: Nachtrag zu T-1813e, dreizehn ueberholte
      Kommentarzitate auf die englischen Texte gebracht (elf aus der
      T-1813e-Bestandsaufnahme, zwei zusaetzliche Fortsetzungszeilen
      mehrzeiliger Kommentare, die deren Suchmuster nicht erfasst hatte, sowie
      das zweite, nicht behandelte Vorkommen von "automatisch" - macht
      dreizehn). Kein Oberflaechentext veraendert.
      Vierte Kategorie im Glossar ergaenzt: ZITAT einer TECHNISCHEN Zeichenkette
      (SCPI-Texte, JSON-/Python-Feldnamen, persistierte Datenwerte,
      CLI-Beispiele, Trennzeichen) - bleibt unveraendert, weil das Zitierte nie
      uebersetzt wird. Mit 65 von 110 Fundstellen in waterfall.py die
      haeufigste Art (siehe T-1813e-Bestandsaufnahme).
      Eine Zeile (6566, "n Sender auf X.XXXXX MHz" -> "n stations on
      X.XXXXX MHz") lag im geteilten KNOWN_STRIP_BLOCK. Ueber
      tools/sync_known_block.py auf analyze_sessions.py, analyze_spectrum.py
      und sdr_waterfall.py uebertragen - je eine geaenderte Zeile, keine
      weitere Abweichung entdeckt (der Block war nicht auseinandergelaufen).
      Regel: Kommentaraenderungen im geteilten Block laufen genauso ueber die
      Referenz waterfall.py und den Abgleich wie Aenderungen am Code selbst
      (T-1811 kennt keine Ausnahme fuer Kommentare).
      Die Bestandsaufnahme in T-1813e hat sich gelohnt: statt der fuenf
      vermuteten Stellen gab es dreizehn veraltete Zitate. Fuenf davon haette
      niemand gesucht - sie stehen in Kommentaren zu Funktionen, die diese Task
      gar nicht angefasst hat. Regel: nach dem Uebersetzen einer Datei wird der
      Kommentarbestand EINMAL vollstaendig durchgesehen, nicht stellenweise
      beim Uebersetzen.
      Aufwandsschaetzung fuer die uebrigen Server: waterfall.py hat auf 10.437
      Zeilen 110 Kommentarzitate, davon 13 veraltet - rund ein veraltetes
      Zitat je 800 Zeilen. Ob manage_jobs.py und die Auswerte-Server eine
      eigene Durchsicht-Task brauchen oder es nebenbei geht, entscheidet sich
      an dieser Quote, sobald die jeweilige Datei uebersetzt ist.
- [~] T-1814 manage_jobs.py (:8050), rund 288 Zeilen mit sichtbarem deutschem Text -
      mehr als waterfall.py in Markup und Skript zusammen. Vier HTML-Seiten statt
      einer. Schnitt urspruenglich in neun Teile nach Thema; T-1814d war mit
      rund 45 Stellen zu gross fuer einen Durchgang und wurde bei der Umsetzung
      aufgeteilt - die Art-Spalten, Filter-Chips und die Zeilendarstellung
      wandern nach T-1814e, alles Folgende rueckt einen Buchstaben weiter.
      Jetzt zehn Teile: T-1814a Serverlogik (Fehlercodes, Kopfleistentitel,
      ~22), T-1814b HTML_PAGE Markup/drei Reiter (~18), T-1814c HTML_PAGE
      Skript laufende/geplante Jobs (~20), T-1814d Sammel-Einzeiler/
      gemeinsame Spalten/SigMF (~26), T-1814e Art-Spalten/Filter-Chips/
      Zeilendarstellung (Rest von urspruenglich ~45 minus T-1814d), T-1814f
      HTML_PAGE Skript MSCAN-Liste (~30), T-1814g MSCAN-Detailseite Markup
      (~40), T-1814h MSCAN-Detailseite Skript (~47), T-1814i Kanalwaehler
      (~34), T-1814j Import-Maske (~14).
      T-1814a umgesetzt 2026-09-26: serverseitige Meldungen auf sprachneutrale
      Fehlercodes umgestellt (neue Klasse JobsError(ValueError), acht Codes:
      json_object_expected, kind_and_ref_required, title_required,
      retry_not_missed, mscan_delete_blocked, mscan_no_channels,
      mscan_missing_channels, invalid_request), dazu die vier Kopfleistentitel.
      Die Serverlogik schickt keine Anzeigetexte mehr, sondern Codes. Damit ist
      der in _error_payload als Uebergang markierte Rueckfall auf str(exc) fuer
      manage_jobs.py abgeschlossen; er bleibt nur noch fuer Ausnahmen aus
      fremden Modulen bestehen.
      Zwei deutsche Klammerformen sind dabei ersatzlos entfallen: "Lauf/Laeufe"
      und "Kanal-ID(s)". Sie waren Notloesungen fuer die Ein-/Mehrzahl; im
      Englischen steht die Zahl als Parameter daneben.
      manage_jobs.py --selftest vergleicht an KEINER Stelle einen der acht
      umgestellten Meldungstexte - die einzige str(exc)-Textpruefung im
      Selbsttest (Zeile ~4068, MS4) betrifft eine ANDERE, unveraendert
      durchgereichte Ueberlappungs-ValueError aus JobStore.create_job(), laut
      Docstring bewusst nicht Teil dieser Umstellung. Keine Anpassung noetig.
      Gate-Abweichung G6 (Auftraggeber-Prompt): Soll "False True" fuer
      s.count('Aufzeichnungen') in s, s.count('Recordings') in s; Ist "True
      True". Ursache: header_html() setzt nur den Kopfleisten-Titel - eine von
      mehreren Stellen, die "Aufzeichnungen" zeigen (Seitentitel <title>,
      Reiter-Knopf, <h2>, JS-Leerzustand, drei Nav-Links "Aufzeichnungen /
      Definitionen"). Diese anderen Stellen sind laut eigener Aufteilung dieses
      Prompts T-1814b/c-Umfang, nicht T-1814a. Nicht behoben (kein Auftrag,
      Gates werden nie durch Uebersetzen ueber den Prompt hinaus erfuellt); der
      Soll-Wert nahm faelschlich an, der Kopfleistentitel sei die einzige
      Fundstelle.
      T-1814b umgesetzt 2026-09-26: Markup der Hauptseite (HTML_PAGE, nur
      <body> und Seitentitel), 19 Edits. Entscheidungen: (1) Die
      Reiterkennungen aufzeichnungen/definitionen/aufbereitung bleiben
      deutsch. Sie stehen in data-tab, in den Abschnitts-ids, im URL-Hash und
      im sessionStorage - eine Uebersetzung braeche jedes Lesezeichen und
      jeden wiederhergestellten Reiterzustand. Gleiches gilt fuer die
      data-value der Filter-Chips. Regel: eine Kennung ist keine Anzeige.
      Dass beide hier deutsch waren, war ein Zufall der Entstehung, keine
      Absicht - und genau deshalb die Gefahr. (2) "Laufend" wird "Running",
      "Geplant" wird "Scheduled" - wortgleich mit EB200_LABELS.zustand.
      (3) "Aufbereitung" wird "Preparation", nach dem Dienstnamen
      preparation_worker.py. (4) "Kurztext" wird "label", Glossar 10.6.
      (5) Unveraendert: MoniScope, MSCAN, D-SCAN, ID, &ndash;/&larr;, alle
      id-/class-Attribute.
      manage_jobs.py --selftest vergleicht an keiner Stelle einen der 19
      geaenderten Markup-Texte; die einzige oberflaechlich aehnliche Stelle
      (label="Geplant" in der Testfixture um Zeile 3666) ist ein Freitext-Wert
      eines Test-Jobs, kein Bezug zur <h2>Geplant</h2>-Ueberschrift. Keine
      Anpassung noetig.
      T-1814c umgesetzt 2026-09-26: laufende und geplante Jobs, 17 Edits.
      Entscheidungen: (1) zwei weitere pluralWort-Aufrufe (Frames, Sweeps im
      Umfangstext des laufenden Jobs) - beide gehen im Betrieb durch 1.
      (2) "VERPASST" wird "MISSED", die Versalform von EB200_LABELS.zustand
      verpasst. (3) "Jetzt nachholen" wird "Retry now" - Knopf, Endpunkt
      (/api/retry) und Fehlercode (ERR_RETRY_NOT_MISSED, T-1814a) tragen damit
      dasselbe Wort. (4) "Stopp" war im Deutschen Zustand und Handlung
      zugleich - "laeuft bis Stopp" und der Knopf "Stoppen". Im Englischen
      trennen sich die beiden: "runs until stopped" gegen "Stop". Solche
      Doppelbedeutungen fallen erst beim Uebersetzen auf; im Deutschen liest
      man darueber hinweg. (5) Unveraendert: die Vergleichswerte 'geplant',
      'verpasst', 'mscan', 'ifpan', &ndash;, MB, :8000, alle class-/id-Namen.
      manage_jobs.py --selftest vergleicht an keiner Stelle einen der 17
      geaenderten Texte; keine Anpassung noetig.
      Nebenbefund (kein T-1814c-Umfang): mindestens zwei Kommentare zitieren
      jetzt ueberholten Text - Zeile 7 (Moduldocstring, "Jetzt nachholen") und
      Zeile 969 ("kein laufender Job", jetzt "no job running" im Code direkt
      darunter). Dieselbe Art Fund wie T-1813e/e1 bei waterfall.py - vorgemerkt
      fuer eine spaetere Kommentar-Durchsicht von manage_jobs.py, analog zu
      T-1813e, sobald die Datei uebersetzt ist.
      Ebenfalls ausserhalb des Umfangs: zwei weitere deutsche confirm()-Dialoge
      (Zeile ~1707 MSCAN-Job loeschen, Zeile ~2519 Lauf beenden) - gehoeren zu
      T-1814e/g (MSCAN-Liste/-Detailseite), nicht zu den laufenden/geplanten
      Jobs dieser Task.
      T-1814d umgesetzt 2026-09-26: Sammel-Einzeiler recInhaltText, gemeinsame
      Spaltendefinitionen, Loeschen-Spalte und SigMF-Export, 26 Edits.
      Zuschnitt geaendert: der urspruenglich geplante T-1814d-Umfang (~45
      Stellen) war zu gross fuer einen Durchgang; Art-Spalten, Filter-Chips
      und Zeilendarstellung wandern nach T-1814e, alles Folgende rueckt einen
      Buchstaben weiter - T-1814 hat jetzt zehn Teile statt neun (Tabelle oben
      aktualisiert).
      Entscheidungen: (1) "Art" wird "Kind", nicht "Type" - die Schnittstelle
      benutzt bereits kind (r.loesch.kind, /api/deleterecording,
      unknown_kind); "Type" waere ein zweites Wort fuer dieselbe Sache und
      stuende neben job.type, das etwas anderes meint. (2) "Umfang" wird
      "Size" (Byte-Spalte). (3) "Ablage" wird "Storage location", wortgleich
      mit der Long-Run-Maske aus T-1812b. (4) sechs weitere pluralWort-Zaehler
      (Sweeps, Segmente, Frames, Kanaele, Aufnahmen, Zyklen im
      Sammel-Einzeiler) - der Helfer bekommt den Rohwert aus i.*, nicht die
      formatierte fmtN()-Zeichenkette. (5) "SigMF+Daten" wird "SigMF+data" -
      SigMF bleibt Formatname, nur der deutsche Zusatz wird uebersetzt.
      (6) Unveraendert: SigMF, .sigmf-data, WAV, IQ, MHz, kHz, MB, Speed,
      Demod, Span, die Vergleichswerte 'laeuft'/'verwaist'/'dscan'/'ifpan'/
      'mscan'/'audio', alle key-Werte der Spaltendefinitionen.
      Die key-Werte der Spaltendefinitionen bleiben deutsch. sortKey wird im
      URL-Hash und im sessionStorage gehalten - ein uebersetzter key braeche
      jede gespeicherte Sortierung. Dritter Fall derselben Art in dieser Datei
      nach den Reiterkennungen (T-1814b) und den Zustandsvergleichen (T-1814c).
      Selbsttest-Anpassung (Teil dieser Task, nicht nur Pruefung): die
      T-922b-Assertion in _self_test() prueft das woertliche Quelltextfragment
      "Ablage: ' + res.dir" gegen HTML_PAGE
      (Zeile ~4170). Mit Edit 25 ("Ablage:" -> "Storage location:") aktualisiert
      auf "Storage location: ' + res.dir", sonst waere der Selbsttest zu Recht
      rot gegangen. Einzige Stelle, an der der Selbsttest einen der 26 Texte
      pruefte.
      Gate-Abweichung G3 (Auftraggeber-Prompt): Soll "sechs mehr" (9 -> 15) fuer
      pluralWort(-Treffer; Ist "sieben mehr" (9 -> 16). Ursache: Edit 4 (MSCAN-
      Zeile) verwendet das Wortpaar 'channel'/'channels' ZWEIMAL - fuer
      i.n_channels UND i.active_channels -, macht also zwei pluralWort-Aufrufe
      aus einem der sechs benannten Zaehler ("Kanaele"). Nicht behoben (Gate
      wird nie durch Weglassen einer im Edit-Text stehenden Ersetzung erfuellt);
      der Soll-Wert zaehlte Zaehler-Namen statt tatsaechliche Aufrufe.
      Gate-Abweichung G5 (Auftraggeber-Prompt): Soll "2 0" fuer
      s.count('SigMF+data'), s.count('SigMF+Daten'); Ist "2 1". Ursache: ein
      Kommentar (Zeile ~1249, im JS-Block von HTML_PAGE) zitiert "SigMF+Daten"
      woertlich und ist nicht Teil der 26 Edits. Dieselbe Fund-Art wie
      T-1813e/e1 bei waterfall.py und der Nebenbefund aus T-1814c. Nicht
      behoben (kein Auftrag); vorgemerkt fuer die spaetere Kommentar-Durchsicht
      von manage_jobs.py.
      T-1814e umgesetzt 2026-09-26: Art-Spalten, Filter-Chips und
      Zeilendarstellung - der beim Zuschnitt von T-1814d abgetrennte Rest des
      Aufzeichnungs-Reiters. Vorgabe "24 Edits" im Prompt, tatsaechlich 18
      nummerierte ALT/NEU-Bloecke angegeben - alle 18 vollstaendig umgesetzt,
      die Kopfzahl war ungenau (kein inhaltlicher Widerspruch, jeder Block war
      eindeutig spezifiziert).
      Entscheidungen: (1) Viele Spaltenkoepfe bleiben (F (min)/F max/BW/Speed/
      Sweeps/Span/Demod/Frames/Rec.BW/Sampling sind Fachkuerzel, die englisch
      genauso heissen); uebersetzt werden nur Segmente, Mittenfrequenz, Modus,
      Kanaele, aktive K., Aufnahmen, Zyklen. (2) "aktive K." wird "active ch."
      - Abkuerzung bleibt Abkuerzung, die Spalte ist schmal. (3) "Alle" wird
      "All", der Wert 'alle' bleibt - dritter Fall der Kennung/Anzeige-Trennung
      in dieser Datei. (4) "weitere anzeigen" wird "show ... more" mit
      umgestellter Wortfolge (Zahl vorn -> Verb vorn) - einer der wenigen
      Faelle, in denen die Fragmentreihenfolge tatsaechlich wechselt.
      (5) Unveraendert: alle key-Werte, die Vergleichswerte 'alle'/'iq'/
      'verwaist'/'direkt'/'longrun'/'mscan'/'orphan'/'job', MHz/kHz/IQ.
      Der Aufzeichnungs-Reiter ist damit vollstaendig. Offen bleiben MSCAN-
      Liste und Aufbereitung (T-1814f), die Detailseite (T-1814g/h), der
      Kanalwaehler (T-1814i) und die Import-Maske (T-1814j).
      manage_jobs.py --selftest vergleicht an keiner Stelle einen der 18
      geaenderten Texte; keine Anpassung noetig.
      T-1814f umgesetzt 2026-09-26: MSCAN-Definitionsliste, 13 Edits.
      Endgueltiger Zuschnitt von T-1814, jetzt zwoelf Teile (zwei Anpassungen
      unterwegs: T-1814d war mit ~45 Stellen zu gross fuer einen Durchgang und
      wurde aufgeteilt; die Kommentardurchsicht der ganzen Datei bekam einen
      eigenen Teil T-1814l, statt sie stellenweise beim Uebersetzen mitzuerledigen):
      | Teil | Inhalt | Stand |
      |---|---|---|
      | a | Serverlogik: Fehlercodes, Kopfleistentitel | erledigt |
      | b | Markup der Hauptseite | erledigt |
      | c | laufende und geplante Jobs | erledigt |
      | d | Inhaltstexte, gemeinsame Spalten, SigMF | erledigt |
      | e | Art-Spalten, Filter-Chips, Zeilendarstellung | erledigt |
      | f | MSCAN-Definitionsliste | erledigt |
      | g | Aufbereitung | offen |
      | h | MSCAN-Detailseite, Markup | offen |
      | i | MSCAN-Detailseite, Skript | offen |
      | j | Kanalwaehler | offen |
      | k | Import-Maske | offen |
      | l | Kommentardurchsicht der ganzen Datei | offen |
      Entscheidungen: (1) "Laeufe" wird "Runs", "Letzter Lauf" wird "Last run" -
      ein Lauf ist die Ausfuehrung einer Definition. (2) "Notiz" wird "Note",
      wortgleich mit dem Filterfeld aus T-1814b; Zellklasse notiz und
      Spaltenschluessel notes bleiben unveraendert. (3) "Geaendert" wird
      "Modified", nicht "Changed" - das Feld heisst modified_at, die Anzeige
      folgt dem Datenfeld. (4) Unveraendert: alle key-Werte, ID, MSCAN, die
      Klasse notiz, die Sortierpfeile.
      manage_jobs.py --selftest vergleicht an keiner Stelle einen der 13
      geaenderten Texte; keine Anpassung noetig.
      T-1814g umgesetzt 2026-09-26: Reiter Aufbereitung, 15 Edits (Prompt-Kopf
      nannte 20, tatsaechlich 15 nummerierte ALT/NEU-Bloecke angegeben - alle
      15 vollstaendig umgesetzt, Kopfzahl ungenau, kein inhaltlicher
      Widerspruch). Damit ist die Hauptseite von manage_jobs.py vollstaendig;
      Teil g in der Zuschnitt-Tabelle aus T-1814f ist erledigt.
      Kriterium fuer EB200_LABELS (T-1814g): ein Code gehoert in die gemeinsame
      Abbildung, wenn MEHR ALS EIN Server ihn anzeigt - ihr Zweck ist, dass
      dieselbe Sache ueberall gleich heisst. PREP_CODE_TEXT und PREP_SPACE_TEXT
      kommen aus preparation_worker.py und erscheinen nur auf :8050; sie bleiben
      deshalb lokal. Die gemeinsame Schicht wird in acht Server geladen, und
      sieben davon haetten mit diesen Codes nichts zu tun.
      Entscheidungen: (1) "Sockel" wird "floor" - Anzeige und Datenfeld
      (floor_bytes) tragen dasselbe Wort. (2) "geprueft/offen/aufbereitet/
      fehlgeschlagen" werden "scanned/pending/prepared/failed" - die ersten
      drei folgen den Feldnamen scanned/todo/built; "offen" wird "pending" und
      nicht "open" (das hiesse "geoeffnet"). (3) "Takt" wird "interval", folgt
      dem Feld scan_interval_s. (4) Kleinschreibung in Satzfragmenten bleibt
      (prepared/running/deferred: ... stehen als Zustandsangabe in einer
      Tabellenzelle, keine Ueberschrift). (5) Unveraendert: alle Codeschluessel,
      alle Zustandsvergleiche, Worker, GiB, die Escape-Folgen –/×/⚠,
      alle CSS-Klassen.
      manage_jobs.py --selftest vergleicht an keiner Stelle einen der 15
      geaenderten Texte; keine Anpassung noetig.
      T-1814h umgesetzt 2026-09-26: MSCAN-Detailseite (HTML_MSCAN_DETAIL),
      Kopfbereich, Speichern-Zeile, Reiterleiste und Reiter "Job", 29 Edits
      (30 ALT/NEU-Bloecke, Block 31 absichtlich identisch - Kopfleistentitel
      aus T-1814a, unveraendert). Dritte und letzte Anpassung des Zuschnitts,
      jetzt dreizehn Teile: das Markup der Detailseite traegt allein 42
      Textstellen, ein Durchgang mit 42 Bloecken waere zu gross.
      | Teil | Inhalt | Stand |
      |---|---|---|
      | a-g | Serverlogik, Hauptseite mit allen drei Reitern | erledigt |
      | h | Detailseite: Kopfbereich und Job-Reiter | erledigt |
      | i | Detailseite: Reiter Kanaele und Laeufe | offen |
      | j | Detailseite: Skript | offen |
      | k | Kanalwaehler | offen |
      | l | Import-Maske | offen |
      | m | Kommentardurchsicht der ganzen Datei | offen |
      Entscheidungen: (1) Reiterkennungen pane-job/pane-kanaele/pane-laeufe und
      id="tabKanaele" bleiben deutsch (Skript schaltet darueber um). (2)
      "Inventur" wird "Inventory", "Ueberwachung" wird "Surveillance" -
      wortgleich mit EB200_LABELS.mode. (3) "bis Laufende/Stopp" wird "until
      end of run or stop" ("Laufende" = Ende des Laufs). (4) "Verweildauer"
      wird "dwell time", "Nachlaufzeit" wird "hold time" (Glossar Z. 113/116);
      (4a) "Speicherplatz" wird "memory slot" (Z. 467); (4b) "Mindestdauer
      Aufnahme" wird "Min. recording duration" (setzt Z. 119 fort). (5)
      "Schwellwert" wird "threshold", "Rauschsperre" wird "squelch". (6)
      Unveraendert: MSCAN, COUNt 1, COUNt INF, INF, dB&micro;V, WAV, Dwell,
      Hold, Squelch, Job, &ndash;, &ge;, alle id- und class-Attribute.
      Gate G-c: Sollwert im Auftrag war "1 2", Ist "1 3" - die dritte
      Fundstelle von COUNt INF ist ein JS-Kommentar (T-1019c) im Skriptteil,
      vorher wie nachher vorhanden. Erster Durchlauf am Gate abgebrochen
      (af51d76), Soll "1 3" danach vom Auftraggeber bestaetigt.
      manage_jobs.py --selftest vergleicht an keiner Stelle einen der 29
      geaenderten Texte; keine Anpassung noetig.
      - [x] T-1814i (2026-09-27) Detailseite, Reiter Kanaele und Laeufe: 10 Textstellen im
            Markup von HTML_MSCAN_DETAIL. Nur Beschriftungen, Knopftexte und ein Platzhalter;
            die Tabellenkoepfe und Hinweiszeilen dieser Reiter erzeugt das Skript und gehoeren
            zu T-1814j.
            Entscheidungen: "Uhrzeit" wird "at time", weil "time" neben "Duration (min)"
            mehrdeutig waere; "Kanaele hinzufuegen" wird "Add channels" in Abgrenzung zu den
            Knoepfen IM Kanalwaehler ("Select channels", "Apply channels", Glossar Z. 464);
            "<label>Start</label>" bleibt wortgleich, deshalb zehn statt elf Bloecke.
            Aus dem Glossar uebernommen statt neu erfunden: "until stopped" (Z. 47),
            "Remove selected" (Z. 465), "runs" (Z. 411).
            Abweichend vom Auftrag, auf Entscheidung des Auftraggebers: (1) Glossar-
            Zeilennummern korrigiert - der Auftrag nannte Z. 447/448 (Stand vor T-1814h,
            das elf Zeilen eingefuegt hat); nach den sechs neuen Zeilen dieser Task stehen
            die Eintraege in Z. 464/465. (2) Die Glossarzeilen "Dauer (min)" und "Ende (Zeile
            im Formular)" umformuliert: das Pruefwerkzeug schneidet ab der ersten Klammer ab,
            als Suchbegriff blieben "Dauer" und "Ende", und "dauer" ist auch Bezeichner
            (Konventionen §6) - Stufe A waere insgesamt 1674 -> 1684 gestiegen (erster
            Anlauf, Bericht auf Zweig bericht/T-1814i). Jetzt 1674 -> 1668.
      - [x] T-1814j (2026-09-27) Detailseite, Skriptteil: 34 Bloecke, rund 47 Anzeigestellen -
            Spaltenkoepfe beider Tabellen, Tooltips, Bestaetigungsfragen, Meldungen und die
            zusammengesetzten Hinweiszeilen. Kommentare bleiben deutsch.
            Entscheidungen: "Datei" wird "Catalog file" nach der Glossarzeile "Katalogdatei, Dateiname, Datei" (Vereinheitlichung
            10.5), nicht "File"; "Startzeit fehlt." wird "Start time is missing." nach dem
            Muster "<X> fehlt" -> "<X> is missing"; "aktive Kanaele" wird ausgeschrieben zu "active channels", waehrend
            die Kurzform "aktive K." der Jobliste "active ch." bleibt; die Kleinschreibung der
            abgeleiteten Spalten bleibt erhalten (active channels, orphaned), die der primaeren
            Spalten gross (Start, End, State).
            Zwei Zaehler bekommen pluralWort: die Kanalzahl der Durchgangsschaetzung und die
            Aufnahmezahl der Loeschfrage - beide koennen im Betrieb 1 werden, im Deutschen war
            das folgenlos. Das handgeschriebene Dezimalkomma "0,1 s" wird zum Punkt; in
            Kommentaren bleibt es (0,15 kHz bei fmtKHz).
            "verwaist" erscheint jetzt an zwei Stellen mit zwei Uebersetzungen: die Zaehlspalte
            heisst "orphaned", der Zustandswert bleibt "Orphaned" aus EB200_LABELS. Zwei
            Bedeutungen, keine Inkonsistenz.
      - [x] T-1814k (2026-09-27) Kanalwaehler (HTML_MSCAN_PICK): 24 Bloecke, rund 34
            Anzeigestellen - Kopfzeile, Filterleiste, Spaltenkoepfe, Tooltips, Meldungen und
            die Warnung beim Verlassen mit ungespeicherter Auswahl. Markup und Skript in einem
            Auftrag, weil die Seite klein ist.
            Entscheidungen: die Spalte "Raster" heisst "Channel group", nicht "Grid" - das
            Glossar grenzt das Anzeigegitter (grid) ausdruecklich vom Frequenzraster des
            Katalogs (channel group) ab, und gemeint ist Letzteres; der Zellinhalt bleibt das
            einzelne "R". "Uebernehmen" heisst "Apply channels", nicht blosses "Apply", weil
            die Glossarzeile fuer genau diese Seite den Gegenstand mitnennt. "Zurueck zum Job"
            wird gross geschrieben, wie der bereits uebersetzte Knopf "Back to overview" in
            waterfall.py.
            Zwei Zaehler bekommen pluralWort: die fehlenden Kanaele (das Verb wandert in die
            Pluralform, "channel is" / "channels are") und die sichtbaren Kanaele - ein Filter
            kann genau einen Kanal uebrig lassen, und "1 channels visible" waere der
            sichtbarste Fehler dieser Seite.
      - [x] T-1814n (2026-09-28) Die zwei Filterknoepfe des Kanalwaehlers, Nachtrag zu T-1814k:
            "Gefilterte an" -> "Select filtered", "Gefilterte aus" -> "Deselect filtered".
            Sie rufen selectVisible() und deselectVisible() und markieren Tabellenzeilen - die
            Glossarzeile "Alle/Keine in die Ansicht" (Show all/Show none) betrifft Kurven im
            Wasserfall und gilt hier nicht. "an"/"aus" werden nicht woertlich uebersetzt, weil
            "Filtered on/off" nach einem Schalter fuer den Filter selbst klingt.
            Anlass: T-1814k hatte fuer beide keinen ALT/NEU-Block. Ursache im Auftrag, nicht im
            Lauf: die Bestandsaufnahme suchte Zeichenketten in Anfuehrungszeichen, diese Texte
            stehen roh zwischen Tags. Regel daraus in T-1814n dokumentiert - fuer Markup den
            Text ZWISCHEN den Tags durchsuchen.
      - [x] T-1814l (2026-09-27) Importmaske (HTML_MSCAN_IMPORT): 13 Bloecke, 16 Stellen -
            Dokumenttitel, Kopflink, Ueberschrift, Zielwahl, die zwei Knoepfe
            und drei Meldungen. Kleinste der vier Seiten in manage_jobs.py.
            Entscheidungen: der Dokumenttitel wird wortgleich mit dem Seitenkopf, den
            header_html("Channels into MSCAN job") schon liefert; "Kanaele uebernehmen" wird
            "Apply channels" wie der Knopf im Kanalwaehler, dieselbe Handlung, dasselbe Wort.
            Die Zaehlueberschrift wird umgestellt: "5 Kanaele uebernehmen" -> "Apply 5
            channels", weil Englisch das Verb nach vorn nimmt. "unbekannte id" wird "unknown
            ID" gross - Fliesstext gross, Feldname id im Tabellenkopf klein, der Unterschied
            ist gewollt.
            Die Jobauswahl bekommt pluralWort: ein bestehender Job mit genau einem Kanal ist
            beim Anlegen der Normalfall, "(A7X2QK, 1 channels)" waere sofort sichtbar.
            Der Vorschlagstitel "Marker <Datum>" bleibt unveraendert - gleiche Schreibung in
            beiden Sprachen, und der Wert wird als Jobtitel gespeichert.
            Reihenfolge: dieser Auftrag setzt T-1814k voraus, weil die Kopfzeile
            "Aufzeichnungen / Definitionen" in beiden Seitenkonstanten stand und der Anker
            sonst nicht eindeutig ist.
      - [x] T-1814m (2026-09-28) Abschlussnachweis manage_jobs.py. KEINE Aenderung am Code.
            Urspruenglich als Kommentardurchsicht geplant; die entfaellt, weil Kommentare deutsch
            bleiben und die Zitate uebersetzter Anzeigetexte in T-1814h/j/l mitgezogen wurden -
            die Gegenprobe findet 0 Zitate ohne Entsprechung im Code.
            Jede der restlichen Fundstellen der Stufe A ist einzeln zugeordnet: 22 verschiedene
            Woerter, alle Kennungen oder persistierte Datenwerte (art, alle, inhalt,
            aufzeichnungen, aufbereitung, laeufe, geplant, verpasst, inventur, raster, notiz,
            ablage, umfang, quelle, auswertung, neu, dazu lokale Variablen). Anzeigetext ist
            keiner mehr darunter. manage_jobs.py ist damit vollstaendig uebersetzt: von 386
            Befunden am Anfang von T-1814 auf 114. (Nachgemessen: 386 ist der Stand vor
            T-1814h; vor T-1814a waren es 461 - siehe docs/berichte/T-1814m.md.)
            Neu: docs/abnahme/manage-jobs.md als Pruefliste fuer die Browser-Abnahme der vier
            Seiten - Umbruch und Breite, die Einzahl der Zaehler, die nur im Fehlerfall
            sichtbaren Meldungen, die Bestaetigungsfragen, die Tooltips, und der Nachweis, dass
            Lesezeichen auf die Reiter weiter funktionieren.
            Offen bleibt ausdruecklich die Frage, wie check_ui_lang.py kuenftig Bezeichner von
            Anzeigetext unterscheidet. Sie gehoert zu T-1826 und betrifft auch die rund 144
            Befunde in den vier Kopien des geteilten Katalog-Blocks. Hier nicht entschieden.
- [x] T-1815 (2026-10-02) block_admin.py (:8010), 161 Strings, davon 40 Validierungsmeldungen. Achtung
      auf den Hinweistext, der Button-Beschriftungen woertlich zitiert.
      Uebernimmt zusaetzlich den Inhalt des gestrichenen T-1802d: die rund fuenfzehn
      Meldungen aus Catalog.errors (eb200_core/known_freq.py) werden strukturiert
      (file, code, detail), und validate_candidate() vergleicht sie danach ueber Tupel
      statt ueber Zeichenketten - heute set(baseline.errors) und
      e.startswith(f"{file_name}:"). Beides gehoert in denselben Prompt, weil Erzeuger
      und einzige sichtbare Anzeigestelle sonst auseinanderfallen. Die vier
      console.warn-Stellen und die vier print-Schleifen bleiben deutsch.
      Verschoben nach T-1820 (Entscheidung 2026-10-02): die Strukturierung von
      Catalog.errors gehoert zu known_freq.py und wird dort mit K1 und dem Rest von K3
      in einem Zug erledigt. T-1815 ist mit T-1815j abgeschlossen.
- [x] T-1815a (2026-09-30) block_admin.py, Fehlermeldungen der Bau- und Prueffunktionen:
      35 Bloecke, 35 Meldungen. Sie stehen als deutsche Saetze in raise ValueError(...)
      und erscheinen unveraendert im Browser als <p class="error">.
      Massgeblich ist Abschnitt 8 des Glossars, "Satzmuster fuer Meldungen": er legt die
      FORM fest, nicht nur die Wortwahl - "unknown <X> (allowed: ...)" statt "<X> unknown",
      "invalid <X>" statt "<X> invalid", "segment <n>: ..." klein. Die erste Fassung des
      Auftrags hatte diesen Abschnitt uebersehen; der Lauf vom 2026-09-29 ist in der
      Vorpruefung zu Recht daran abgebrochen (Bericht docs/berichte/T-1815a.md).
      Diese Meldungen beginnen klein, auch wo das Deutsche gross schreibt: englische
      Substantive werden nicht grossgeschrieben, und die Beispiele des Abschnitts sind
      durchweg klein.
      Entscheidung des Auftraggebers (2026-09-28): direkt uebersetzen, nicht auf
      Fehlercodes umstellen - die Code-Regel aus CLAUDE.md gilt fuer NEUE Fehlergruende,
      ein Umbau bestehender Stellen widerspraeche der Regel, bestehende Stellen nicht
      eigenmaechtig umzubauen.
      Feldnamen bleiben stehen: "bw_hz is not a number", nicht "bandwidth is not a
      number" - wer die Meldung liest, soll das Feld in der JSON-Datei wiederfinden.
      "Schrittweite" wird "grid spacing", nicht "step size": das Glossar grenzt das
      Anzeigegitter (grid) vom Frequenzraster des Katalogs (channel group) ab, und
      gemeint ist die Schrittweite eines Rasters.
      Die Loeschsperre bekommt beschriftete Zahlen ("bands: 3, entries: 12") statt
      gezaehlter Substantive. Grund: Python hat keinen pluralWort-Helfer, und woertlich
      uebersetzt staende bei genau einem Band "1 bands". Beschriftete Zahlen sind bei
      jedem Wert richtig; ein Python-Plural-Helfer waere ein Umbau und nicht Teil dieses
      Auftrags.
      _self_test_pflichtfelder zieht mit (Bloecke 34 und 35): die Proben P2 und P8
      pruefen jetzt "demodulation" und "bandwidth", weil die Meldungen nach dem
      Satzmuster klein und englisch beginnen.
      Zeile 595 (Filter auf das deutsche Wort "Raster") bleibt unangetastet - die
      gefilterten Meldungen entstehen in known_freq.py, siehe K1 in T-1815-Zerlegung.md
      und die Folgeaufgabe T-1820a.
- [x] T-1815b (2026-10-01) block_admin.py, Fehlermeldungen der CSV-Schicht:
      20 Bloecke - 14 Meldungsstellen in decode_csv_bytes, _csv_hz,
      _csv_channel_entry, _csv_band_entry, parse_catalog_csv und
      parse_multipart_form, ein Docstring-Zitat in _csv_wert und fuenf Behauptungen
      in _self_test_csv_import (Kopplung K2 aus T-1815-Zerlegung.md).
      Satzmuster nach Glossar Abschnitt 8 wie in T-1815a: klein, ohne Punkt,
      "line <n>: ...", "column <x> must be empty", "unknown typ ... (allowed: ...)".
      Spaltennamen bleiben, auch das deutsche "typ": es ist der Spaltenkopf eines
      Dateiformats (CSV_COLUMNS), in Umlauf befindliche CSV-Dateien tragen ihn.
      "unbekannte Spalte(n)" wird "unknown columns: a, b" - beschriftete Aufzaehlung
      statt "(s)", aus demselben Grund wie die Loeschsperre in T-1815a.
      Begriffe der CSV, verbindlich auch fuer T-1815g: Kopfzeile -> header row,
      Datenzeile -> data row, Kanalzeile/Bandzeile -> channel row/band row,
      Trennzeichen -> separator, Formular -> form, CSV-Vorlage -> CSV template.
      Nur CSV-Vorlage steht im Glossar; die uebrigen nackten Begriffe treffen
      Bezeichner (kopfzeile) und JS-Kommentare anderer Server.
      Die hochgeladene CSV heisst "CSV file", nicht "catalog file" - Abgrenzung zur
      Vereinheitlichung 10.5, mit eigener Glossarzeile.
      Kopplung K3 (neu gefunden, nicht angefasst): _CSV_KONFLIKT_RES zerlegt deutsche
      Meldungen aus known_freq.py ("id '...' schon in ... vergeben") und aus dem
      Handler ("Kanal '...' existiert in ... bereits") in die Konflikttabelle des
      Importberichts. Wer diese Meldungen uebersetzt (T-1815i, T-1820), muss die
      beiden Ausdruecke im selben Zug mitziehen, sonst faellt jede Konfliktzeile still
      in die unformatierte Restliste.
- [x] T-1815c (2026-10-01) block_admin.py, Fehlermeldungen der Markerschicht:
      3 Bloecke, die drei Meldungen von parse_marker_table. Satzmuster wie T-1815a/b:
      "line <n>: ...", klein, ohne Punkt; "ist keine Zahl" -> "is not a number".
      Die Spaltennamen der Meldung sind die der kopierten Markerliste (Time/MHz/dBuV),
      wie der Wasserfall sie seit T-1813d schreibt.
      Kleiner als die Schaetzung der Zerlegung (~20): die uebrigen deutschen Texte der
      Markerschicht gehoeren woandershin - _marker_notes ist ein persistierter Wert
      (T-1821), die Meldungen von _handle_known_marker_import_save stehen im Handler
      (T-1815i), die beiden im Docstring von _marker_rows_from_form zitierten
      Beschriftungen ziehen mit T-1815h (_marker_row_html) mit.
      _self_test_marker_import prueft nur die Anzahl der Meldungen, nicht ihren Text,
      und bleibt unveraendert. Keine neue Glossarzeile.
- [x] T-1815d (2026-10-01) block_admin.py, Listenansicht: PAGE_HEAD, KNOWN_SHEET_JS,
      RASTER_FORM_JS, BAND_FORM_JS, render_known_list_page; 54 Bloecke.
      Erste Teilaufgabe mit HTML und JavaScript - Stufe A sinkt, nicht Stufe B.
      Schreibweise: Beschriftungen Sentence case; Fehlerabsaetze klein ohne Punkt wie die
      Meldungen aus a/b; Saetze an den Anwender (alert, Hinweis) gross mit Punkt.
      Zaehler als beschriftete Zahlen ("Bands: 3", "Entries: 12",
      "Catalog files in view: 3 of 25") - der Ansichtszaehler steht in Python (erstes
      Laden) und in KNOWN_SHEET_JS (nach dem Umschalten) und muss wortgleich sein.
      Loesch-Rueckfragen nach Abschnitt 8 mit genanntem Objekt:
      'Delete band "X" permanently?'.
      Eintragstyp nach Glossar Abschnitt 4: Single channel / Channel group; Spaltenkopf
      "Type". Ansichtsschalter "Show", Sammelknoepfe "Show all" / "Show none".
      Kennungen bleiben: data-pane (bearbeiten, kopf, kanal, kanalgruppe ...), ids,
      CSS-Klassen, JS-Variablen; der Reiter "Edit" hat weiter data-pane="bearbeiten".
      Abdeckung -> Coverage (nicht im Glossar: Variable abdeckung).
      Fuer die folgenden Teilauftraege: T-1815f zitiert die Reiter als "Edit" /
      "New channel" (Hinweis in _file_editor_form_body, Kommentar in
      _file_head_form_body) und schreibt "Create new catalog file"; T-1815g uebersetzt
      den Knopf Schliessen in _csv_export_form_body als "Close" (seit T-1815d ein
      gezaehlter Befund) und "Neue Katalogdatei" als "New catalog file".
      Browser-Abnahme der Uebersicht steht aus (gesammelt in docs/abnahme/block-admin.md
      nach T-1815i).
      Abweichung im Lauf: Die Glossarzeile Schliessen | Close ist NICHT aufgenommen
      (Zaehlung 25 statt 24, siehe docs/berichte/T-1815d.md); der Knopf Close in den
      Bearbeitungsblaettern ist trotzdem uebersetzt.
- [x] T-1815e (2026-10-01) block_admin.py, Formulare fuer Kanal, Kanalgruppe und Band:
      _channel_form_body, _raster_form_body, _band_form_body, die drei
      render_*_form_page, ANTENNA_OPTIONS und die drei inherit-Helfer; 45 Bloecke.
      Kopplung K4 (neu): die inherit-Helfer beschriften die Leer-Option, und
      _file_head_form_body, _csv_import_form_body und _marker_import_form_body ersetzen
      genau diesen Text per .replace(). Helfer und die sieben Suchschluessel sind
      gemeinsam auf "(inherited: -)" umgestellt; die Ersatztexte "(nicht gesetzt)" und
      "(bitte waehlen)" uebersetzen f, g und h.
      Absendeknopf nach Glossar 10.9: {'Create' if is_new else 'Save'}, wie der
      Datei-Kopf-Knopf; value-Attribute (preview/save) unveraendert.
      geerbt -> inherited, Bezeichnung -> Label (10.6), Rasterart -> Grid type,
      Schrittweite -> Grid spacing, KW -> HF; ICAO 8.33 kHz mit Dezimalpunkt.
      Antennenwerte (kw_dipol ...) bleiben, nur die Anzeigenamen sind englisch.
      Mit erledigt, weil wortgleich und nicht enger verankerbar: der Abbrechen-Knopf in
      _file_editor_form_body (f) und in _csv_import_form_body (g, beide Varianten), die
      Beschriftungen Bandwidth/Antenna/Tags/Notes in _file_head_form_body (f). T-1815f
      und T-1815g fassen diese Stellen nicht noch einmal an.
      Fuer T-1815i: die Rastervorschau ("Vorschau: n Kanaele") baut der Handler.
      Browser-Abnahme der drei Formulare steht aus (eingebettet und als eigene Seite,
      Neuanlage und Bearbeitung).
- [x] T-1815f (2026-10-01) block_admin.py, Datei-Kopf und JSON-Editor:
      _file_editor_form_body, render_file_editor_page, _file_head_form_body,
      render_file_head_form_page; 21 Bloecke.
      Absendeknoepfe nach Glossar 10.9: Datei-Kopf {'Create' if is_new else 'Save'},
      JSON-Editor immer Save. Titel "Create new catalog file" (wie der Blattkopf aus
      T-1815d), "Edit file header: <name>", "Edit catalog file: <name>".
      Kopplung K4 abgeschlossen bis auf die Markermaske: die Ersatztexte
      "(nicht gesetzt)" -> "(not set)", fuenfmal, davon zwei in _csv_import_form_body
      (wortgleich, fuer T-1815g erledigt). "(bitte waehlen)" bleibt fuer T-1815h.
      Der Hinweis im JSON-Editor zitiert die Reiter der Uebersicht als "Edit" /
      "New channel".
      Offener Punkt (Entscheidung des Auftraggebers): die Farbknoepfe zeigen als Tooltip
      den deutschen Farbnamen. Er ist Schluessel von CATALOG_COLORS (known_freq.py) und
      darf als meta.color in Katalogdateien stehen - ein Datenwert, nicht uebersetzt.
      Englische Anzeige braeuchte eine Abbildung Wert -> Anzeige.
      Browser-Abnahme steht aus (Reiter "File header" und "JSON", Neuanlage-Blatt).
- [x] T-1815g (2026-10-02) block_admin.py, CSV-Masken: _csv_export_form_body,
      _csv_bericht_html, _csv_import_form_body, render_csv_import_page; 31 Bloecke,
      davon fuenf Selbsttest-Behauptungen in _self_test_csv_import (C6, C9).
      Zitate der Formularbeschriftungen ("Create new catalog file", "Append to existing
      catalog file", "Replace existing ids") ziehen im selben Zug mit.
      Kopplung K3 unberuehrt: _CSV_KONFLIKT_RES zerlegt weiterhin die deutschen
      Konfliktmeldungen aus known_freq.py und dem Handler; uebersetzt sind nur die
      Texte, die _csv_bericht_html selbst schreibt. Wer die Konfliktmeldungen
      uebersetzt (T-1815i, T-1820), zieht die beiden Ausdruecke und die Eingabezeilen
      von C9 mit.
      Glossarzeile Schliessen | Close nachgetragen (in T-1815d zurueckgestellt).
      Browser-Abnahme steht aus (Blaetter CSV export und CSV import, Fehlerbericht).
- [x] T-1815h (2026-10-02) block_admin.py, Marker-Masken: _marker_row_html,
      _marker_import_form_body, render_marker_import_page, render_marker_handoff_page;
      24 Bloecke.
      Kopplung K4 abgeschlossen: der Ersatztext der Markermaske heisst
      "(please select)", wortgleich mit dem CSV-Import.
      Knoepfe: Preview / Add to job / Catalog only / Cancel; value-Attribute unveraendert.
      Inhaltliche Korrektur: der Hinweis zur Markerliste sagte "Dezimalpunkt oder
      -komma"; seit T-1803a ist das Komma Tausendertrennzeichen. Der englische Hinweis
      sagt, was der Parser tut.
      Uebergabeseite mit derselben Ueberschrift wie die Zielseite auf :8050
      ("Channels into MSCAN job").
      Die in T-1815c zurueckgestellten Zitate im Docstring von _marker_rows_from_form
      sind eingeloest.
      Browser-Abnahme steht aus (Marker list aus dem Wasserfall an :8010 schicken,
      Preview, Treffer- und Neufundzeilen, Catalog only, Add to job bis :8050).
- [x] T-1815i (2026-10-02) block_admin.py, Handler: Meldungen und Fehlerseiten nach dem
      Speichern, Loeschen, CSV-Export/-Import und Marker-Import, dazu drei Meldungen der
      Logikschicht, die T-1815a nicht erfasst hatte; 46 Bloecke.
      Kopplung K3, Handler-Haelfte: die Konfliktmeldungen des CSV-Imports
      ("channel 'x' already exists in Z.json") und der zweite Ausdruck von
      _CSV_KONFLIKT_RES sind gemeinsam englisch, mit den Eingabezeilen des Selbsttests C9.
      Der erste Ausdruck ("schon in ... vergeben") gehoert zu einer Meldung aus
      known_freq.py und bleibt mit ihr fuer T-1820.
      Zwischenstand bis T-1820: hinter "validation failed: " stehen weiter die deutschen
      Meldungen aus known_freq.py.
      Konsolenausgaben und argparse-Hilfen in main bleiben deutsch.
- [x] T-1815j (2026-10-02) Abschlussnachweis fuer block_admin.py, KEINE Aenderung am Code.
      Jede verbliebene Fundstelle von check_ui_lang.py einzeln zugeordnet (Bericht
      T-1815j): JavaScript-Variablen (ein, offen, titel, neu/ziel), Kennungen
      (data-pane-Werte, sheet--neu, option value="neu", URL-Pfade /known/raster),
      persistierte Datenwerte (kind "raster", Notiz "Auto-Marker"), Kopplung K1 und der
      Rest von K3 (beide T-1820). Kein Anzeigetext.
      Neu: docs/abnahme/block-admin.md als Pruefliste; Uebersicht und Formulare
      (T-1815d/e) sind seit 2026-10-01 abgenommen, der Rest steht aus.
      CLAUDE.md: Ausnahme fuer Werkzeugfehler bei Gates (Anlass T-1815i, Block 5 - das
      Edit-Tool hatte ein Leerzeichen am Ende des NEU-Texts abgeschnitten).
      Nebenbei: die Zeile "Browser-Abnahme steht aus (Reiter File header ...)" war beim
      Einfuegen von T-1815g bis hierher gerutscht und steht wieder bei T-1815f.
- [x] T-1816 (2026-10-03) analyze_audio.py (:8070), 198 Strings, Schwerpunkt JS-Spaltendefinitionen
      und Tooltips. Enthaelt die 27 Feldbezeichnungen der Modenkennung
      (M_FELDER_FSK/OFDM/PSK) - Wortlaut in Abschnitt 3 des Glossars.
      Zerlegt in sieben Teilauftraege a bis g, Bauplan docs/prompts/T-1816-Zerlegung.md.
      Entscheidungen des Auftraggebers vom 2026-10-03: die Servermeldungen werden direkt
      uebersetzt, nicht auf Codes umgebaut (wie block_admin.py); die Meldungen von
      tools/eye_diagram.py, die bei einer gescheiterten Vermessung in der Messtafel
      erscheinen, bleiben vorerst deutsch - Umstellung auf Code + Parameter ist T-1828.
- [x] T-1816a (2026-10-03) analyze_audio.py, Python-Seite: ERR_*-Meldungen, run_measure,
      parse_range, build_audspec_response, Handler, HTML_NO_PATH und der Ansichtsname
      "Signal analysis"; 18 Bloecke, davon einer im Selbsttest (T-1611a pruefte "Modus").
      Der <title> von HTML_PAGE folgt in T-1816b, document.title im Skript in T-1816f.
      "Spektrogramm" in ERR_BUILDING wird "spectrogram": eine Rechenmeldung, Glossar 10.8.
      Der JSON-Schluessel "notiz" (Sidecar der Messbefunde) bleibt - Datenwert.
- [x] T-1816b (2026-10-03) analyze_audio.py, Markup von HTML_PAGE: <title>, Ruecklink,
      Werkzeugleiste (Zoom, Marker, FFT, Kontrast, Paletten, Play/Measure signal),
      Messkasten; 22 Bloecke, davon fuenf im Selbsttest (T-1616, T-1620, T-1624:
      Bruchstuecke und Positionsproben der Werkzeugleiste).
      Glossar: Kontrast spreizen -> Stretch contrast, Zuruecksetzen -> Reset,
      Vermessen -> Measure, Wiedergabe/Anhoeren -> Play (10.10), Befund -> finding,
      Marker loeschen -> Clear markers (nur Anzeige, ABGRENZUNG zu Delete).
      Paletten: nur die Anzeigenamen (Spectral, Gray, Hot, Rainbow, Steps, Sawtooth);
      die values (spektral, grau, ...) sind Schluessel im Skript und bleiben.
      "kein Spektrogramm verfuegbar" -> "no waterfall available" (Glossar 10.8, Anzeige).
- [x] T-1816c (2026-10-03) analyze_audio.py, Skript rund um das Spektrogramm: Achse,
      Tooltips von Play, Fortschritt und Fehler beim Laden, FFT-Aufloesung, vertikaler
      Zoom, Abstimmung; 29 Bloecke, davon fuenf im Selbsttest (T-1213, T-1606,
      T-1616, T-1619).
      Glossar 10.8 an einer Stelle zweigeteilt: Fortschrittsmeldungen der Berechnung
      sagen "spectrogram" ("spectrogram is being computed"), alles Angezeigte "waterfall"
      ("waterfall could not be loaded", "error loading the waterfall").
      Drei Zeilen mit JS-Escapes (u00d7, u00b7, u2013 - im Quelltext mit doppeltem
      Backslash) sind nur in Ausschnitten vor bzw. hinter dem Escape geaendert; die
      Escapes selbst sind unberuehrt.
- [x] T-1816d (2026-10-03) analyze_audio.py, Feldtafeln der Signalvermessung:
      M_FELDER_FSK (12 Felder), M_FELDER_OFDM (6), M_FELDER_PSK (9) - Beschriftungen
      wortgleich nach Glossar Abschnitt 3 in Sentence case, dazu alle Erlaeuterungen
      (Tooltips). Die Strukturprobe des Selbsttests (Feldzahl, zwei Backticks je Feld,
      Zeilenlaenge) gilt unveraendert; ein Pruefskript gleicht jede Beschriftung gegen
      das Glossar ab.
      In den Erlaeuterungen heisst das Zeitraster "grid" (Glossarzeile "Raster
      (Parameter) | grid"); nur die Feldbeschriftung "Jitter (half-bit raster)" und ihr
      Zitat behalten "raster", weil Abschnitt 3 sie so festlegt.
      Fuer T-1826: "raster" (2 Fundstellen) und "modulation" (1) bleiben als Befunde von
      check_ui_lang.py stehen, obwohl sie Englisch sind - beide Woerter sind zugleich
      deutsche Suchbegriffe des Glossars.
- [x] T-1816e (2026-10-03) analyze_audio.py, Messung im Skript: Wertedarstellung
      (Rasterlage, Symbolrate), Kandidatentafel, Pfadbloecke, Kopf und Hinweise der
      Messtafel, Liste der gemerkten Befunde, Anhoeren/Vermessen/Merken; 39 Bloecke,
      davon zwoelf im Selbsttest (T-1611b, T-1613, T-1614, T-1620, T-1704).
      Kandidatentafel wortgleich nach den Satzmustern aus Glossar Abschnitt 3
      ("Known services:", "no match possible", "Deviation {x} Hz (expected {y} +- {z})").
      Die Negativprobe des Leitsatzes (die Tafel behauptet nie, etwas erkannt zu haben)
      prueft jetzt "detected" statt "erkannt".
      Das replace('FEHLER: ', '') in mPfadHtml bleibt - es gehoert zu den deutschen
      Meldungen von eye_diagram.py (Kopplung K2, T-1828).
- [x] T-1816f (2026-10-03) analyze_audio.py, Metadatenblock: Empfangskette (Chips und
      Tooltips), Geraetezeile, Art-Abzeichen, automatische und manuelle Klasse,
      Metadatentafel, Hinweis ohne Pfad, Dokumenttitel, Geschwisterlinks; 47 Bloecke,
      davon sieben im Selbsttest (T-915, T-1625, T-1626).
      rxEinAus zeigt "ein"/"aus" jetzt ueber labelOf('empfangskette', ...) an
      (Wertgruppe seit T-1801); als Zustandsschluessel der Chips (CSS-Klassen
      .rxChip.ein/.aus) bleiben "ein" und "aus" Kennungen.
      Drei neue Glossarzeilen: Datei (Aufnahmedatei) -> file, Trefferliste -> hit list
      (T-1817 nennt die Seite :8060 ebenso), Eichdaempfer -> attenuator.
      Altbestand nach Lokalisierungsregel 4: Formatzeile und Sammellink der Segmente
      bleiben Verkettungen mit Zahlen, nur uebersetzt.
- [x] T-1816g (2026-10-03) Abschlussnachweis fuer analyze_audio.py, KEINE Aenderung am Code.
      Jede verbliebene Fundstelle von check_ui_lang.py einzeln zugeordnet (Bericht
      T-1816g): JavaScript-Variablen (titel, von/bis, notiz, zeit, spanne, dateiName),
      Zustandsschluessel und CSS-Klassen der Empfangskette (ein/aus), Namen von
      Wertgruppen (empfangskette, art), der JSON-Schluessel notiz, dazu drei englische
      Woerter, die zugleich deutsche Suchbegriffe sind (raster zweimal, modulation
      einmal - vorgemerkt fuer T-1826). Kein Anzeigetext.
      Neu: docs/abnahme/analyze-audio.md als Pruefliste fuer die Browser-Abnahme mit
      einer Audio-WAV und einer IQ-Aufnahme; die Abnahme steht aus.
- [x] T-1817 (2026-10-03) analyze_mscan.py (:8060), 124 Strings, Schwerpunkt Spaltendefinitionen der
      Lauf- und Kanaltabellen.
      Zerlegt in sechs Teilauftraege a bis f, Bauplan docs/prompts/T-1817-Zerlegung.md.
      Entscheidungen des Auftraggebers vom 2026-10-03: die Servermeldungen werden direkt
      uebersetzt (wie block_admin.py und analyze_audio.py); die sieben ValueError-
      Meldungen von eb200_core/mscan_view.py, die nur :8060 anzeigt, uebersetzt T-1817a
      mit. Die KeyError-Meldung "unbekannte job_id" aus job_store.py bleibt bei T-1820.
- [x] T-1817a (2026-10-03) analyze_mscan.py, Python-Seite: parse_range, Handler-
      Meldungen, HTML_NO_RUN, beide <title>, die Ansichtsnamen "MSCAN run" und
      "MSCAN analysis" sowie alle "MSCAN-Lauf" im Skript (Kopfleiste, Dokumenttitel);
      dazu die sieben ValueError-Meldungen von eb200_core/mscan_view.py. 23 Bloecke,
      keiner im Selbsttest. Die Kopfzeile mit Label setzt englische Anfuehrungszeichen.
- [x] T-1817b (2026-10-03) analyze_mscan.py, Markup: Kopflinks, Player, Auswahlzeile,
      Reiter, Steuerung und Kopf der Stille-Tabelle, Sortierauswahl des Zeitverlaufs;
      dazu die wortgleichen Gegenstuecke im Skript ("all channels", "Silent channels (n)")
      und drei Kommentare, die Reiternamen zitieren. 13 Bloecke, keiner im Selbsttest.
      Drei neue Glossarzeilen: Stille Kanaele -> silent channels, Zeitverlauf ->
      timeline, Oeffnung(en) (Squelch) -> opening(s). Die Spalte "Datei" der
      Stille-Tabelle ist die Katalogdatei des Kanals: "Catalog file".
- [x] T-1817c (2026-10-03) analyze_mscan.py, Kopfkarte des Laufs (renderHeader): Titel,
      Modus, Zustand, Zeitzeile, Zyklen, Audiobilanz samt Tooltip, die Zeilen der
      Pegel-Inventur (Survey, Below threshold, Dwell / hold, Programming), Kanaele,
      Ordner mit Kopierknopf, Laufwarnung, Meldungen zur Zwischenablage; 24 Bloecke,
      davon zwei im Selbsttest (T3). Vier neue Glossarzeilen: Erhebung -> survey,
      verworfene Kurzsegmente -> discarded short segments, Fortsetzungen -> resumptions,
      Programmierung -> programming. Altbestand nach Lokalisierungsregel 4: Audiobilanz,
      Kanalzeile und verworfene Oeffnungen bleiben Verkettungen mit Zahlen.
- [x] T-1817d (2026-10-03) analyze_mscan.py, Tabellen: die fuenf Spaltensaetze
      (LEVEL_CHAN_COLUMNS, CHAN_TABLE_COLUMNS, REC_TABLE_COLUMNS, LEVEL_EVAL_COLUMNS,
      EVAL_COLUMNS), Leerzeilen, Zaehler, Spalte zur Signalanalyse, Hinweis unter der
      Kanaltabelle, Stille-Tabelle samt Rueckfrage und Meldungen beim Entfernen, dazu die
      wortgleichen Leerhinweise des Zeitverlaufs; 29 Bloecke, davon einer im
      Selbsttest (T3). Spaltenkopf "Treffer" -> "Hits" ohne Glossarzeile: "treffer" ist in
      fuenf Dateien ein JavaScript-Bezeichner, eine Zeile haette dort Fehlbefunde erzeugt.
      Altbestand nach Lokalisierungsregel 4: Zaehler, Hinweis, Rueckfrage und Meldung
      bleiben Verkettungen mit Zahlen; die Rueckfrage stellt das Verb nach vorn.
- [x] T-1817e (2026-10-03) analyze_mscan.py, Zeitverlauf: Hinweise bei unbestimmtem
      Zeitraum bzw. unbestimmter Zeitachse, Farblegende und Achsenhinweis der
      Pegel-Matrix, Tooltip eines Pegel-Balkens, Meldung beim Klick auf einen
      ausgefilterten Balken; 5 Bloecke, keiner im Selbsttest. Damit enthaelt
      HTML_MSCAN_RESULT keinen Umlaut mehr. Altbestand nach Lokalisierungsregel 4: die
      Farblegende bleibt eine Verkettung mit Zahlen.
- [x] T-1817f (2026-10-03) Abschlussnachweis fuer analyze_mscan.py, KEINE Aenderung am Code.
      Jede verbliebene Fundstelle von check_ui_lang.py einzeln zugeordnet (Bericht
      T-1817f): JavaScript-Variablen (skala, spanne, pegel, breite, schwelle, verlust),
      der Spaltenschluessel spektrum und die CSS-Klasse spektrum-off. Kein Anzeigetext.
      Die ast-Probe findet in analyze_mscan.py nur den Datenwert 'laeuft' im Vergleich
      r.state === 'laeuft', in eb200_core/mscan_view.py nichts.
      Neu: docs/abnahme/analyze-mscan.md als Pruefliste fuer die Browser-Abnahme mit
      einem Tonlauf und einer Pegel-Inventur; die Abnahme steht aus.
- [x] T-1818 (2026-10-05) analyze_sessions.py (:8020) und analyze_spectrum.py (:8040) gemeinsam, 211
      Strings. Die Dateien sind Zwillinge; getrennte Bearbeitung erzeugt Abweichungen im
      wortgleichen Teil.
      Zerlegt in sechs Teilauftraege a bis f, Bauplan docs/prompts/T-1818-Zerlegung.md.
      Jeder Teilauftrag fasst beide Dateien an; wortgleiche Zeilen werden wortgleich
      uebersetzt, die Zwillingsprobe (Zeilen von analyze_spectrum.py, die auch in
      analyze_sessions.py stehen) sichert das ab. Der Katalog-Streifen
      (KNOWN_STRIP_BLOCK) bleibt unberuehrt. Entscheidungen des Auftraggebers vom
      2026-10-04: Servermeldungen direkt uebersetzen (wie T-1815 bis T-1817);
      "Katalog nicht geladen" bleibt hier stehen und geht an T-1820.
- [x] T-1818a (2026-10-04) Python-Seite beider Dateien: ERR_*-Meldungen, die
      reason-Texte der Spektrum-Liste, der Rueckfall von ensure_befund, die Handler-
      Meldung "unknown endpoint", beide <title> und die Ansichtsnamen "Session analysis"
      und "Spectrum analysis"; 15 Bloecke, davon 5 in beiden Dateien wortgleich.
      Die reason-Texte sind Anzeigetext in einem API-Feld (Altbestand nach
      Lokalisierungsregel 2), nur uebersetzt.
- [x] T-1818b (2026-10-04) Markup beider Seiten: Listenansicht (Suche, Pfad oeffnen),
      Ruecksprungknoepfe, Skalenzeile, Geruest des Befund-Navigators (Glossar Abschnitt 7
      woertlich) samt Notizfeld; dazu in analyze_spectrum.py die Ueberschriften der drei
      Darstellungen und die Transportleiste. 34 Bloecke, davon 14 in beiden
      Dateien wortgleich. Die Hinweise "Klick markiert/springt - folgt" sind woertlich
      uebersetzt ("to follow"), obwohl die Klickfunktion inzwischen besteht; das klaert
      die Abnahme.
- [x] T-1818c (2026-10-04) Listenansicht beider Seiten: Spaltenkoepfe, Loeschknopf und
      Rueckfrage, Lade- und Fehlerzeilen, leere Suche, ungueltige Zeilen, Tooltip beim
      Speichern des Kurztexts, Fehler beim Oeffnen eines Pfads; in analyze_spectrum.py
      dazu der Teil-Name im Job-Abzeichen ("part01"). 22 Bloecke, davon 13 in
      beiden Dateien wortgleich. Die Rueckfrage beim Loeschen stellt das Verb nach vorn
      (Glossarzeile "wirklich loeschen?").
- [x] T-1818d (2026-10-04) Detailansichten, dateieigen. analyze_sessions.py: Betriebszeile
      (fmtSegBetrieb), Kopfzeile der Session, Segmentkarten, Fehler im Vorschaubild,
      Kopf und Dezimierungshinweis der Segmentansicht. analyze_spectrum.py: Kopf und
      Hinweise des Players (renderHeader), Anzeige des gewaehlten Bins (updateBinLabel).
      18 Bloecke, keiner wortgleich in beiden Dateien. Altbestand nach
      Lokalisierungsregel 4: Session-Kopf, Dezimierungshinweis und die Player-Hinweise
      bleiben Verkettungen mit Zahlen; "n segments" ohne pluralWort wie bisher.
- [x] T-1818e (2026-10-05) Befund-Navigator und Notiz beider Seiten (loadBefund, loadNote,
      saveNote, fillGroup, fillTable, renderBefund): Platzhalter, Gruppenzaehler,
      Spaltenkoepfe und Zwischenueberschriften woertlich nach Glossar Abschnitt 7, dazu
      die Hinweise am Notizfeld. 19 Bloecke, davon 17 in beiden Dateien wortgleich.
      Ausserhalb des Katalog-Streifens bleiben nur Kennungen (Route /api/befund,
      Wertgruppe art). Das Urteil der Kamm-Erkennung kommt als deutscher Satz aus
      session_analyze.py und steht im Befund-Sidecar - neue Aufgabe T-1829.
- [x] T-1818f (2026-10-05) Abschlussnachweis fuer analyze_sessions.py und
      analyze_spectrum.py, KEINE Aenderung am Code. Jede verbliebene Fundstelle von
      check_ui_lang.py einzeln zugeordnet (Bericht T-1818f): je 36 Bezeichner im geteilten
      Katalog-Streifen (dieselben wie in waterfall.py), die Route /api/befund und die
      Pfad-Endung .befund.json, die Wertgruppe art (Spektrum) und "Katalog nicht geladen"
      (T-1820). Kein Anzeigetext. Neu: docs/abnahme/analyze-sessions.md und
      docs/abnahme/analyze-spectrum.md als Prueflisten fuer die Browser-Abnahme; die
      Abnahme steht aus.
- [x] T-1819 (2026-10-05) sdr_waterfall.py (:8030), 29 Strings. Uebersetzung und
      Abschlussnachweis in einem Auftrag (kleiner Umfang, keine Zerlegung): Ansichtsname
      "SDR waterfall", Meldungen von /api/sdr/start, Ueberschrift, Status- und Hinweiszeilen,
      Fehlertexte im Skript; 15 Bloecke. Der Katalog-Streifen bleibt unberuehrt.
      Verbliebene Fundstellen einzeln zugeordnet (Bericht T-1819): 36 Bezeichner im
      Katalog-Streifen (wie waterfall.py), zwei Fehlertexte des SDR-Workers, die nur ins
      Konsolenlog gehen (bleiben deutsch), und "Katalog nicht geladen" (T-1820).
      Neu: docs/abnahme/sdr-waterfall.md; die Browser-Abnahme steht aus.
- [x] T-1820 (2026-10-05) Restmeldungen der Kernmodule, die nach T-1802 noch deutschen Text in die
      Oberflaeche liefern (recording_index, job_store, mscan_job, audio_classify,
      audio_spectrum), rund 40 Stellen.
      Uebernimmt aus T-1815 (Entscheidung 2026-10-02): die Strukturierung von
      Catalog.errors (vormals T-1802d) - Meldungen als (file, code, detail),
      validate_candidate() vergleicht ueber Tupel statt Zeichenketten. Im selben Zug:
      Kopplung K1 (_raster_preview filtert auf das Wort "Raster", siehe T-1820a) und der
      Rest von K3 (erster Ausdruck von _CSV_KONFLIKT_RES, "schon in ... vergeben", samt
      Eingabezeile im Selbsttest C9 von block_admin.py).
      Uebernimmt aus T-1818 (Entscheidung 2026-10-04): "Katalog nicht geladen" in der
      Antwort von /api/known, wortgleich in analyze_sessions.py, analyze_spectrum.py,
      waterfall.py und sdr_waterfall.py - in allen vier Servern in einem Zug, zusammen
      mit den Meldungen des Katalog-Loaders, die in derselben Liste "errors" stehen.
      Zerlegt in fuenf Teilauftraege a bis e, Bauplan docs/prompts/T-1820-Zerlegung.md.
      Entscheidung des Auftraggebers vom 2026-10-05: die Strukturierung von
      Catalog.errors entfaellt; die Meldungen werden direkt uebersetzt (Glossar Abschnitt
      8), die Textvergleiche in block_admin.py (K1, K3) auf den englischen Wortlaut
      umgestellt. Ein spaeterer Umbau auf Codes waere eine eigene Aufgabe ausserhalb von
      Meilenstein 18.
- [x] T-1820a (2026-10-05) Fehlerliste des Frequenzkatalogs: alle Meldungen von
      eb200_core/known_freq.py (Catalog.errors und die ValueError-Texte der Pruefungen)
      nach den Satzmustern aus Glossar Abschnitt 8; in block_admin.py der Filter von
      _raster_preview (K1, jetzt "channel group") und der erste Ausdruck von
      _CSV_KONFLIKT_RES (K3, jetzt "already used in") samt Selbsttest C9; "catalog not
      loaded" in /api/known von waterfall.py, analyze_sessions.py, analyze_spectrum.py und
      sdr_waterfall.py (K2). 33 Bloecke.
- [x] T-1820b (2026-10-05) Meldungen von eb200_core/mscan_job.py: Pruefung einer
      MSCAN-Definition (validate_params, validate_job), Ladefehler von MscanJobStore,
      ID-Vergabe, KeyError zu unbekannten Jobs, Grenze der Kanalzahl. 28 Bloecke, 29
      Meldungen; die Meldungsprobe (jetzt auch mit return-Listen und Beugungsformen)
      findet danach keine deutsche Meldung mehr. Datenwerte in den Meldungen (inventur,
      ueberwachung, cycle, runtime) bleiben. manage_jobs.py zeigt die Liste unveraendert an,
      kein Code vergleicht den Text.
- [x] T-1820c (2026-10-05) Meldungen von eb200_core/job_store.py: Job-Anlage (Typ,
      Dezimierung, Zeitfenster, Ablage), Zustandswechsel, Loeschen (Zustand,
      Messdatenschutz, Verzeichnis ausserhalb der Ablagen) und die sechs KeyError zu
      unbekannten Jobs; dazu ein Kommentar in waterfall.py, der eine Meldung zitiert.
      11 Bloecke, 15 Meldungen. Zustandsnamen in den Meldungen sind persistierte
      Datenwerte und bleiben. manage_jobs.py, waterfall.py und mscan_view.py (:8060)
      reichen die Texte unveraendert durch; kein Code vergleicht sie.
- [x] T-1820d (2026-10-05) Ausnahmetexte von audio_classify.py (laufende Aufnahme,
      Format), audio_spectrum.py (laufende Aufnahme, Format, Bau laeuft schon,
      Sperrdatei, Zeitfenster) und recording_index.py (Schema einer Session-DB); 9
      Bloecke. analyze_audio.py unterscheidet die Faelle ueber die Ausnahmeklasse bzw.
      code und formuliert die bekannten Faelle selbst; der Text erscheint nur im Rueckfall.
      Die Begruendungen der Klassifikation (reasons) bleiben fuer T-1822.
- [x] T-1820e (2026-10-05) Abschlussnachweis fuer T-1820, KEINE Aenderung am Code. Die
      Meldungsprobe findet in known_freq.py, block_admin.py, mscan_job.py, job_store.py,
      mscan_view.py, audio_classify.py, audio_spectrum.py und recording_index.py keine
      deutsche Meldung mehr; "Katalog nicht geladen" steht in keiner Python-Datei mehr.
      Die Abnahmelisten block-admin.md, analyze-mscan.md, analyze-sessions.md,
      analyze-spectrum.md und sdr-waterfall.md sind nachgezogen, manage-jobs.md hat einen
      Pruefpunkt fuer Servermeldungen. Neu: T-1835 (Servermeldungen von waterfall.py).

### Stufe 3: Nacharbeit und Abnahme
- [x] T-1821 (2026-10-06) Persistierte deutsche Texte bei Neuanlagen: Dateinamensteil _teil -> _part,
      CSV-Kopfzeile Zeit;MHz;dBuV, Auto-Marker-Notiz, Default-service "Marker",
      Kanallabel "unbekannt {mhz} MHz". Altbestand bleibt lesbar: Leseregexe um die
      englische Variante erweitern, nicht ersetzen.
      Dazu aus T-1835: abort_detail bei receiver_busy ("eine Session-Aufzeichnung",
      "eine Spektrum-Aufnahme", "Audio-Mithoeren") und stats.audio_error ("WAV-Start
      fehlgeschlagen", "keine Audio-Datagramme ...") werden ins Manifest geschrieben und
      in :8050 angezeigt (abortText). Neue Laeufe sollen sprachneutrale Werte schreiben.
- [x] T-1821a (2026-10-06) Dateinamensteil der Long-Run-Teildateien _teil -> _part
      (job_runner.py fuer .specbin, waterfall.py fuer .wav). Vorhandene Dateien werden
      nicht umbenannt; jobBadgeHTML in analyze_spectrum.py liest beide Schreibweisen.
      Ein Lauf, der ueber das Update hinweg weiterlaeuft, hat gemischte Namen - die
      Reihenfolge kommt aus dem Manifest, nicht aus dem Namen. Kein anderer Leser
      zerlegt den Namen (repo-weit geprueft). Selbsttest von job_runner.py, Abnahmeliste
      analyze-spectrum.md und handbuch/arbeit/fakten.md nachgezogen.
- [x] T-1821b (2026-10-06) abort_detail bei receiver_busy als Code (session_recording,
      spectrum_recording, monitoring), Anzeige ueber ui_theme.py; technische
      Einzelheiten (Aufnahmefehler in mscan_run.py, stats.audio_error in waterfall.py)
      als englischer Klartext. Alte Manifeste werden unveraendert angezeigt.
      Umgesetzt: waterfall.py schreibt bei receiver_busy session_recording,
      spectrum_recording oder monitoring; ui_theme.py hat die Labelgruppe busy_by
      (Selbsttest L2: 18 Gruppen), abortText formuliert nur bei receiver_busy ueber sie.
      mscan_run.py: acht Aufnahmefehler (cannot create WAV file, unexpected error in
      <methode>), waterfall.py: vier Texte von stats.audio_error. Die Log-Ausgaben
      (print) bleiben deutsch.
- [x] T-1821c (2026-10-06) Notiz importierter Auto-Marker ("Auto marker, detected at ...") und
      Kanallabel ohne Katalogtreffer ("unknown ... MHz"); Abschluss T-1821.
      Umgesetzt: _marker_notes in block_admin.py (zwei Formen, Selbsttest nachgezogen)
      und das Label in mscan_run.py (Ton- und Pegel-Recorder). Bereits importierte
      Kanaele und vorhandene Laeufe behalten ihre deutschen Texte (Benutzer- bzw.
      Laufdaten). Der interne Gruppierungsschluessel "unbekannt:<freq>" in
      MscanSegmenter.results() bleibt: er wird weder gespeichert noch angezeigt.
      Aus der urspruenglichen Liste von T-1821 waren die CSV-Kopfzeile (seit T-1813
      Time;MHz;dBuV) und der Dienstname "Marker" schon englisch.
- [x] T-1822 (2026-10-06) audio_classify reasons uebersetzen (12 Erzeugungsstellen) und
      CLASSIFIER_VERSION auf eb200-audioclass-v4 heben. Ohne Versionswechsel blieben
      alte Sidecars mit deutschem Text gueltig, die Anzeige waere gemischtsprachig.
      Umgesetzt: sieben der zwoelf Erzeugungsstellen enthielten deutschen Text (die
      uebrigen fuenf bestehen nur aus Merkmalen, Schwellennamen und Klassencodes);
      Klassencodes (LEER, RAUSCHEN ...) und Schwellennamen bleiben. CLASSIFIER_VERSION
      eb200-audioclass-v4, Moduldoku und handbuch/arbeit/fakten.md nachgezogen. Folge:
      die Aufbereitung (preparation_worker, ensure_klasse) klassifiziert alle
      Mono-Aufnahmen einmal neu; die Klassen selbst aendern sich nicht.
- [x] T-1823 (2026-10-06) Katalogtexte: modes/fsk.json sowie die intl_*-Kataloge werden englisch.
      Die nationalen Tabellen (FNV_AT_2026, ORS_*, ukw_*, DAB_Band3, Air, UKW_BC,
      fup_30_88_mhz, Amateurfunk_*) bleiben deutsch - sie zitieren nationale
      Zuweisungen. IDs bleiben in jedem Fall unveraendert (sprachneutrale Slugs).
      Dazu die Farbnamen (offen aus T-1815f): die Schluessel von CATALOG_COLORS
      (known_freq.py) erscheinen als Tooltip der Farbknoepfe und koennen als meta.color
      in Katalogdateien stehen. Werte bleiben; zu entscheiden ist eine englische Anzeige.
- [x] T-1823a (2026-10-06) Katalogtexte der fuenf intl_*-Kataloge und von modes/fsk.json:
      Dienstname, Quelle und Notiz der Dateien, Band- und Kanalbezeichnungen, Notizen
      der Kanaele bzw. Moden. IDs, tags, antenna, demod, color, label_template und
      moved_from bleiben; Eigennamen (GPS L1, NOAA-19 APT, AIS 1, POCSAG 512, DMR,
      ISM/SRD, Mil-Guard) bleiben. Dezimalpunkt nach Glossar. Die Task-Nummern in der
      Notiz von SITOR-B entfallen (Glossar Abschnitt 11). table_version von fsk.json
      bleibt 1 (sie gehoert zu den Toleranzwerten, nicht zum Anzeigetext). Die
      Meldungen von eb200_core/mode_table.py bleiben deutsch: sie erreichen die
      Oberflaeche nicht (eye_diagram.py schreibt sie nur als WARNUNG nach stderr, sonst
      CLI). Vorhandene MSCAN-Snapshots und Aufnahmen behalten die deutschen
      Bezeichnungen (gespeicherte Daten). Glossar: fuenf Zeilen in Abschnitt 2.
- [x] T-1823b (2026-10-06) Farbnamen: englische Anzeige der Schluessel von CATALOG_COLORS als
      Tooltip der Farbknoepfe in :8010 (Anzeigetabelle in known_freq.py); die Schluessel
      selbst bleiben. Dazu die Abnahmepunkte fuer T-1823 in den bestehenden Listen.
      Umgesetzt: CATALOG_COLOR_LABELS in known_freq.py (39 Namen, gleiche Schluessel
      und Reihenfolge wie CATALOG_COLORS, vom Selbsttest geprueft); block_admin.py
      zeigt sie als Tooltip der Schnellwahl. Die Meldung zu einer unbekannten Farbe
      nennt weiter die Schluessel - sie sind die zulaessigen Eingaben. Keine
      Glossarzeilen: Farbnamen wie "grau" sind anderswo Datenwerte (Palette in
      analyze_audio.py) und erzeugten Fehlbefunde. Abnahmepunkte in
      docs/abnahme/block-admin.md, analyze-audio.md und manage-jobs.md; die beiden
      Reste zu T-1823 entfallen.
- [x] T-1824 (2026-10-06) API-Schluessel geloeschte_dateien -> deleted_files (job_store.py Rueckgabe,
      Doku, Selbsttest, recording_index.py). Vor der Veroeffentlichung folgenlos,
      danach ein Bruch.
      Umgesetzt: vier Stellen (Rueckgabe und Docstring in job_store.py, Selbsttest,
      Weitergabe in recording_index.delete_recording). Kein Skript und kein Server liest
      den Schluessel (repo-weit geprueft); der Wert ist nicht persistiert. Das gleichnamige
      deleted_files in tools/known_freq_abgleich.py ist ein anderer, schon englischer
      Schluessel und unberuehrt.
- [ ] T-1825 Layoutabnahme: alle acht Oberflaechen in Light und Dark, Canvas-Kollisionen
      bei measureText-Kuerzung, feste Pixelbreiten, nowrap-Spaltenkoepfe. Reine
      Browserarbeit, kein CC-Prompt.
- [x] T-1826 (2026-10-07) Abschlussgate: check_ui_lang.py meldet 0, selftest_param_sync.py gruen,
      check_js_blocks.py gruen, sync_known_block.py byte-gleich, alle _self_test gruen.
- [x] T-1826a (2026-10-07) check_ui_lang.py prueft nur noch Anzeigetext (Entscheidung des
      Auftraggebers 2026-10-07, Weg B): neues Modul tools/ui_lang_sicht.py
      (Sichtbarkeitsmaske fuer HTML, JS und CSS, eigener Selbsttest), Suche in
      datei_analysieren auf dem sichtbaren Text, lang/locale weiter im Code. Glossar:
      ein deutscher Begriff, der als englisches Wort des Glossars vorkommt, wird nicht
      gesucht (Modulation, Raster - "jitter (half-bit raster)" steht so im Glossar).
      Selbsttest T22 und T31 berichtigt (T22 schlug seit T-1812b1 fehl, unbemerkt, weil
      --selftest in keiner Standardliste stand), T32 bis T35 neu. Ein echter Rest
      behoben: Beschriftung "Modulation" -> "Demod" (waterfall.py, Glossar 10.4).
      Stufe A gesamt 368 -> 0, check_ui_lang.py endet mit Exitcode 0. Bekannte Grenze:
      ein deutsches Einzelwort ohne Umlaut in Kleinschreibung ('keine') gilt im Skript
      als Code und wird nicht gemeldet.
- [x] T-1826b (2026-10-07) Abschlussgate ausfuehren und protokollieren (alle Selbsttests).
      Ausgefuehrt: check_ui_lang.py Exitcode 0 (Stufe A 0), dazu 50 weitere Pruefstaende
      und Selbsttests (Liste und Ergebnis je Aufruf in docs/berichte/T-1826b.md), alle
      Exitcode 0. Keine Codeaenderung. Nicht ausgefuehrt: der Selbsttest von
      eb200_core/db.py - er arbeitet auf der echten data/eb200_project.db (Testblock
      anlegen und loeschen), siehe T-1837.
- [x] T-1837 (2026-10-07) Selbsttest von eb200_core/db.py auf eine temporaere Datenbank umstellen.
      _self_test() benutzt DEFAULT_DB_PATH (data/eb200_project.db), legt dort einen
      Testblock an, aendert und loescht ihn; init_db() migriert dabei die echte Datei.
      Ziel: tempfile wie in den uebrigen Selbsttests, danach in die Liste des
      Abschlussgates aufnehmen. Gefunden bei der Vorbereitung von T-1826b.
      Umgesetzt: _self_test() legt seine Datenbank in einem tempfile-Verzeichnis an und
      prueft am Ende, dass Groesse und Aenderungszeit von data/eb200_project.db gleich
      geblieben sind. Moduldoku nachgezogen. 00-Konventionen.md: neuer Grundsatz
      "Selbsttests nie auf echten Daten". python -m eb200_core.db gehoert ab jetzt in
      jedes Abschlussgate (Liste aus T-1826b plus diesem Aufruf).
- [x] T-1838 (2026-10-08) Kleinkorrekturen aus der Browser-Abnahme (T-1825, Durchgang von
      Claude in Chrome ueber :8010 bis :8070, Entscheidungen des Auftraggebers 2026-10-08):
      :8010 Browser-Tab und Ueberschrift "Frequency catalog" (Einzahl, wie die
      Kopfleiste), Spaltenkoepfe des Bearbeitungsblatts ID/Label/Enabled; :8070
      "Back to list (:8050)" und "Tune to markers" gross (Selbsttest-Bruchstueck mit);
      :8020 "n bins" klein (Segmentkarte, Segmentansicht); Befund-Navigator :8020/:8040
      ohne "- to follow" (die Markierung bzw. der Sprung funktioniert); :8050 Journal der
      Aufbereitung "space OK" statt des rohen Codes space_ok; Anzeige der Phase "ende"
      "Complete" statt "Finishing" (ui_theme.py, Glossar). Abnahmelisten nachgezogen.
- [ ] T-1839 Zeitangaben vereinheitlichen (Glossar, Schreibweisen: UTC immer
      ausgewiesen, lokale Zeit nie). Befund der Browser-Abnahme: :8040 Liste "19:54:07
      UTC", Player "Start 2026-09-11T19:54:07+00:00" (rohes ISO) und Frame-Zeile
      "21:54:07" (Ortszeit ohne Zusatz); :8050 und :8070 zeigen Ortszeit ohne
      Kennzeichnung. Erst Bestandsaufnahme aller Zeitanzeigen (isoTime/isoDateTime und
      direkte Formatierungen), dann Entscheidung UTC oder Ortszeit mit Kennzeichnung.
- [ ] T-1840 :8050 zeigt unter "Running" die IFPAN-Aufnahme
      20260914_192931_8039kHz_IQ.specbin als laufend ("File invalid or incomplete"),
      vermutlich ein Sidecar mit running=true nach einem Absturz. Klaeren, ob die
      Anzeige eine verwaiste Aufnahme erkennen und als solche zeigen soll (Muster
      Zustand verwaist der Jobs). Befund der Browser-Abnahme 2026-10-08.
- [x] T-1827 (2026-10-07) CLAUDE.md und Entscheidungslog fortschreiben: die vier Regeln von 2026-08-31
      von "Vorbereitung" auf "umgesetzt", neue Regel "UI-Text ist Englisch, Code,
      Kommentar und Log sind Deutsch".
      Umgesetzt: CLAUDE.md, Abschnitt "Lokalisierung" (vorher
      "Lokalisierungsvorbereitung") mit Grundregel, Regel 3 nach den Entscheidungen
      T-1820a/T-1828 (englischer Satz oder Code, nie Deutsch), Regel 4 mit englischem
      Beispiel, neue Regel 5 (check_ui_lang.py Exitcode 0); Absatz "Exitcode 1 ist
      Normalzustand" unter Lokaler Betrieb ersetzt. 00-Konventionen.md: Verweis,
      Standardliste um die beiden Selbsttests des Pruefwerkzeugs, Gate Sprachbefund.
      Dazu ein letzter Rest: Seitentitel von :8000 "MoniScope Waterfall - Lupe" ->
      "MoniScope - Waterfall" (Glossarzeile Lupe, damit check_ui_lang.py ihn kuenftig
      findet). Neue Abnahmeliste docs/abnahme/waterfall.md; analyze-audio.md von
      erledigten Resten bereinigt. Offen fuer den Meilenstein bleibt T-1825
      (Layoutabnahme im Browser).
- [x] T-1828 (2026-10-06) Meldungen von tools/eye_diagram.py als Code + Parameter. run_measure
      (analyze_audio.py) reicht bei einer gescheiterten Vermessung die letzte stderr-Zeile
      von eye_diagram.py als "meldung" an :8070 durch, mPfadHtml zeigt sie und schneidet
      dabei das Praefix "FEHLER: " ab. Die Saetze entstehen in eye_diagram.py und
      eb200_core/framing.py u. a. und bleiben als CLI-Ausgabe deutsch. Ziel: eye_diagram
      schreibt zusaetzlich einen Code mit Parametern ins JSON, die Formulierung entsteht im
      Skript von :8070 (Regel 3 der Lokalisierungsvorbereitung). Aus T-1816 abgeleitet
      (Entscheidung 2026-10-03).
      Umgesetzt anders als geplant (Entscheidungslog 2026-10-06, T-1828): direkte
      Uebersetzung der 23 Ausnahmetexte von eye_diagram.py (19 verschiedene) statt Code
      und Parameter. Das CLI-Praefix "FEHLER: " bleibt; analyze_audio.py schneidet es
      jetzt serverseitig ab (_stderr_meldung, Vermessung und Audio aus IQ), das
      Abschneiden in mPfadHtml bleibt als Rueckfall. Selbsttest T4 prueft "noise". Die
      uebrigen CLI-Ausgaben von eye_diagram.py bleiben deutsch.
- [x] T-1829 (2026-10-06) Urteil der Kamm-Erkennung als Code. detect_comb() in session_analyze.py
      schreibt "verdict" als deutschen Satz ("kein Kamm", "sehr regelmaessiger Kamm ueber
      den ganzen Span -> ...", "Kamm vorhanden, weniger regelmaessig -> ...") in den
      Befund-Sidecar (*.befund.json); renderBefund in analyze_sessions.py und
      analyze_spectrum.py zeigt ihn in der Gruppe "Comb" des Befund-Navigators. Ziel: ein
      sprachneutraler Code im Sidecar (Lokalisierungsregeln 1 und 2), die Formulierung im
      Skript beider Seiten; vorhandene Sidecars bleiben lesbar (Rueckfall auf den Text)
      oder werden neu erzeugt. Aus T-1818e abgeleitet.
      Umgesetzt: verdict ist ein Code (none, possible_artifact, likely_channels);
      COMB_VERDICT_TEXT in session_analyze.py liefert den deutschen Klartext fuer den
      Textbericht (Konsole), renderBefund beider Seiten formuliert englisch und zeigt
      einen unbekannten Wert unveraendert. ANALYSIS_VERSION und
      EXPECTED_ANALYSIS_VERSION 2 -> 3: vorhandene Befund-Sidecars werden beim
      naechsten Oeffnen neu berechnet (Notizen liegen getrennt und bleiben).
      tools/selftest_delete.py legt Attrappen mit analysis_version 2 an; fuer den
      Loeschtest ist die Zahl ohne Belang und bleibt.
- [x] T-1835 (2026-10-06) Servermeldungen von waterfall.py (Python-Teil). Die Uebersetzung von :8000
      (T-1812/T-1813) hat Markup und Skript erfasst, nicht die Meldungen des Servers:
      mindestens 17 deutsche Fehlertexte in den /api-Antworten, die das Skript anzeigt
      (z. B. "Frequenz ausserhalb 10 kHz..3 GHz", "Feinabstimmung gesperrt - Aufnahme
      laeuft", "IQ-Demodulation mit Job-Audio wird nicht unterstuetzt", "span_hz fehlt
      oder ungueltig"), dazu die Kurzfassung eines scpi_verify-Berichts fuer die
      Browser-Meldung (_notice_from_report). check_ui_lang.py meldet in waterfall.py noch
      rund 96 Befunde der Stufe B. Erst Bestandsaufnahme (was erreicht die Seite, was ist
      Log), dann direkte Uebersetzung nach Glossar Abschnitt 8 wie bei den uebrigen
      Servern. Gefunden im Abschluss von T-1820.
      Umgesetzt in einem Auftrag (docs/prompts/T-1835-Servermeldungen-Wasserfall.md):
      24 Fehlertexte der /api-Antworten, die drei Browser-Meldungen (SSE "notice") samt
      _notice_from_report und der Abweichungstext der Verstaerkungsregelung, dazu zwei
      Meldungen von preparation_worker.py. Bleiben: Logausgaben, Ausnahmen der
      Datagramm-Auswertung (nur Log), die Kennung dem (Demodulation) und die persistierten
      Texte abort_detail (receiver_busy) und stats.audio_error - diese gehoeren zu T-1821.
- [x] T-1836 (2026-10-06) Ausnahmetexte der uebrigen Kernmodule: path_sandbox, sigmf_export, db,
      dscan_store, mscan_run, param_store, spectrum_reader, spectrum_store, audio_wav,
      mode_match (rund 40 Stellen, Meldungsprobe aus T-1820b). Zuerst klaeren, welche die
      Oberflaeche erreichen (str(exc) in einer Server-Antwort, error_params eines
      err_code), dann diese direkt uebersetzen; reine Log- und Selbsttest-Texte bleiben.
      mode_table.py gehoert zu T-1823. Gefunden bei T-1835.
      Umgesetzt: alle 35 Ausnahmetexte der zehn Module direkt uebersetzt (Glossar
      Abschnitt 8), ohne vorherige Einzelpruefung, ob sie die Oberflaeche erreichen - wie
      bei T-1820 ist die Uebersetzung einer Ausnahme auch dann richtig, wenn sie nur im Log
      landet, und keine wird als Text verglichen (repo-weit geprueft). Dazu die Commit-Zeile
      im Bericht T-1835 berichtigt.

- [x] T-1834 (2026-09-29, abgeschlossen 2026-09-30) Rueckkehr zum lokalen Betrieb. Cloud-Sitzungen brachten keine
      Beschleunigung; Arbeit ab jetzt nur mit Claude Code auf dem Rechner des
      Auftraggebers. Regeln in CLAUDE.md und docs/prompts/ werden umgestellt, der
      Abschnitt "Cloud-Sitzungen" bleibt als ausser Betrieb gekennzeichnetes Wissen stehen,
      tools/sync_docs.ps1 entfaellt.
- [x] T-1834a (2026-09-30) Nachtrag zu T-1834: docs/prompts/00-Konventionen.md, Abschnitt 7,
      verlangte bei Abbruch noch, die Berichtsdatei zu committen, und begruendete das mit
      "git fetch". Beides widersprach dem lokalen Betrieb (CLAUDE.md, "Lokaler Betrieb").
      Jetzt: Abbruchbericht bleibt uncommittet, Wiederholungslauf ueberschreibt ihn.
      Gefunden im Abschlussbericht von T-1834.

## Meilenstein 19: Englisches Handbuch
Das deutsche Handbuch ist mit de-final eingefroren (Meilenstein 6). Das englische
entsteht neu, mit den deutschen Entwuerfen als Quellenmaterial, und erst nachdem die
Oberflaeche steht - sonst veralten die Screenshots sofort.
- [ ] T-1900 Gliederung und Uebernahme: welche der deutschen Kapitel 1-18 uebernommen,
      welche neu geschnitten werden.
- [ ] T-1901 Teil A und B auf Englisch.
- [ ] T-1902 Teil C bis E auf Englisch, einschliesslich der in Meilenstein 6 nie
      fertiggestellten Kapitel.
- [ ] T-1903 Screenshots im Light-Theme, einmalig nach Abschluss von Meilenstein 18.
- [ ] T-1904 Typst-Aufbereitung fuer Releases (uebernimmt die Absicht von T-607).

## Meilenstein 20: Windows-Verteilung (Programm- und Datenwurzel, Verpackung)
MoniScope soll als Windows-Programm an andere EB200-Besitzer gehen: portabler Ordner
oder Nuitka-EXE. Fuehrt Meilenstein 3 (Windows-Packaging, T-301 Embeddable Python)
weiter; T-301/T-303/T-304 gehen in T-2005 auf.

### Stufe A: Programm- und Datenwurzel trennen (Voraussetzung fuer jede Verteilform)
Ziel: MoniScope laeuft unabhaengig vom Arbeitsverzeichnis, alles Beschreibbare liegt
unter einer Datenwurzel, die nur per Kommandozeile gesetzt wird (--data-dir, keine
Umgebungsvariable; ohne Argument die Repo-Wurzel wie bisher). Grundlage sind drei
Messlaeufe vom 2026-10-07/08 (EXE-Inventur, Datenwurzel, block_admin), Entscheidungslog
2026-10-08.
- [~] T-2000 (2026-10-08) block_admin.py auf Paketimporte umstellen. Bisher importierte
      eb200_core/block_admin.py param_store, ui_theme, known_freq und http_base ohne
      Paketpraefix; das ging nur, weil beim Direktaufruf das Skriptverzeichnis
      eb200_core/ als sys.path[0] gilt, und start_eb200.py startete den Server als
      einzigen mit cwd=eb200_core/. Jetzt: from eb200_core.xxx import ..., dazu die
      Direktaufruf-Weiche aus path_sandbox.py; start_eb200.py startet block_admin mit
      cwd = Repo-Root, EB200_CORE entfaellt. tools/known_freq_abgleich.py und
      tools/selftest_known_edit.py nehmen das Repo-Root statt eb200_core/ in den
      Suchpfad und importieren als Paket, damit kein Modul doppelt geladen wird.
      Veraltete Kommentare in start_eb200.py und param_store.py nachgezogen. Kein
      Modul im Repo importiert jetzt noch ein eb200_core-Modul ohne Praefix.
      Handvalidierung offen: Suite mit start_eb200.bat starten, :8010 bedienen
      (Katalogdatei oeffnen und speichern, Ansichtsauswahl umschalten).
- [x] T-2001 (2026-10-08) eb200_core/paths.py: PROGRAM_ROOT (mitgelieferte, nur gelesene Daten) und
      DATA_ROOT (alles Beschreibbare). DATA_ROOT wird beim ersten Import aus sys.argv
      festgelegt (--data-dir X oder --data-dir=X) und ist danach Prozesskonstante,
      damit Modulkonstanten, Importkopien und Parameterdefaults ohne Umbau stimmen.
      --data-dir muss auf ein bestehendes Verzeichnis zeigen, sonst Abbruch mit
      Meldung. Eigener Selbsttest, noch ohne Verbraucher.
      Umgesetzt: PROGRAM_ROOT, DATA_ROOT, data_path(), program_path(), resolve_data_root()
      und DataDirError. Erkannt werden --data-dir PFAD und --data-dir=PFAD; doppelte,
      fehlende oder leere Angabe und ein Pfad ohne bestehendes Verzeichnis beenden den
      Prozess beim Import mit "FEHLER: ..." auf stderr und Exitcode 1. Relative Angaben
      gelten gegen das Arbeitsverzeichnis beim Start. Nur Standardbibliothek, keine
      Importe aus eb200_core. Selbsttest T1-T7 (python -m eb200_core.paths --selftest),
      T6 prueft das Importzeit-Verhalten in Unterprozessen.
- [x] T-2002 (2026-10-08) eb200_core-Module auf paths umstellen: known_freq (Kataloge, station.json,
      catalog_view.json), job_store (storage_roots.json, jobs/index.json, Default
      records), mscan_job, param_store, db, path_sandbox, recording_index, mscan_view.
      storage_roots.json nur noch auf einem Weg lesen (heute job_store ueber __file__,
      path_sandbox/recording_index ueber cwd); relative Eintraege gegen DATA_ROOT.
      Sandbox-Wurzel der Auswerte-Server (path_sandbox, heute das Repo-Root) wird
      DATA_ROOT - sicherheitsrelevant, weil sie bestimmt, welche Dateien :8020/:8040/
      :8070 ueber den path-Parameter ausliefern; eigener Selbsttestfall.
      Umgesetzt: known_freq (Kataloge, station.json, catalog_view.json), job_store
      (jobs/index.json, storage_roots.json), mscan_job, param_store und db liegen unter
      DATA_ROOT, mode_table (modes/fsk.json) unter PROGRAM_ROOT. job_store loest
      relative storage_roots-Eintraege ueber den neuen Helfer _root_abs() gegen
      DATA_ROOT auf statt gegen das Arbeitsverzeichnis (create_job, storage_roots,
      delete_with_data). recording_index: Vorgabe repo_root = DATA_ROOT statt "." in
      list_recordings, delete_recording, export_recording_sigmf, set_recording_label;
      mscan_view: DATA_ROOT statt Arbeitsverzeichnis. known_freq, job_store,
      param_store, db und mode_table bekamen die Direktaufruf-Weiche. Ohne --data-dir
      sind alle Pfadwerte gleich wie vorher (Werteprobe). path_sandbox bleibt
      unveraendert: die Sandbox-Wurzel kommt als repo_root vom Aufrufer; die
      Umstellung auf DATA_ROOT samt Selbsttestfall geht in T-2003.
- [~] T-2003 (2026-10-08) Server und Starter: --data-dir in start_eb200.py, absolut an alle neun
      Server weitergereicht (session_analyze.py und eye_diagram.py brauchen es nicht);
      AUDIO_ROOT/SESSION_ROOT/SPECTRUM_ROOT, SESSION_DIR, RECORD_DIR, logs/,
      preparation_worker.json und Journal auf paths; Selbsttests, die Modulkonstanten
      ueberschreiben, nachziehen. Server mit eigenem argparse (block_admin, manage_jobs,
      analyze_mscan, analyze_audio, preparation_worker, start_eb200) muessen --data-dir
      dort ebenfalls annehmen, sonst bricht parse_args() ab. Die Server uebergeben
      DATA_ROOT als repo_root an path_sandbox und recording_index (Sandbox-Wurzel,
      aus T-2002 verschoben), mit eigenem Selbsttestfall: ausserhalb von DATA_ROOT
      und der storage_roots wird nichts ausgeliefert.
      Umgesetzt: start_eb200.py nimmt --data-dir an, reicht DATA_ROOT absolut an jeden
      Server weiter (_spawn), schreibt Log und Datenwurzel nach DATA_ROOT/logs bzw. in
      Konsole und Log. block_admin, manage_jobs, analyze_mscan, analyze_audio und
      preparation_worker kennen --data-dir im argparse. SESSION_ROOT, SPECTRUM_ROOT,
      AUDIO_ROOT und die Worker-Wurzel sind DATA_ROOT (Sandbox-Wurzel); waterfall:
      RECORD_DIR absolut unter DATA_ROOT, Session-DBs unter DATA_ROOT/sessions
      (Deep-Link-Pfad weiter "sessions/<datei>.db" ueber to_portable), Freeze- und
      Thread-Dumps unter DATA_ROOT/logs, /api/storage prueft den absoluten Pfad;
      session_analyze.py laeuft aus PROGRAM_ROOT. preparation_worker: Konfiguration
      und Journal unter DATA_ROOT. param_store legt data/params/ vor der Lock-Datei
      an (Befund: in einer leeren Datenwurzel scheiterte register_server). Ohne
      --data-dir sind alle Pfadwerte gleich wie vorher (Werteprobe). Die
      Sandbox-Wurzel ist durch eine Probe gesichert (Gate, kein eigener Selbsttest:
      path_sandbox ist unveraendert, geprueft wird die Verdrahtung der Server).
      Handvalidierung offen: Suite normal starten und bedienen; einmal mit
      start_eb200.bat --data-dir <leerer Ordner> starten.
- [~] T-2004 (2026-10-08) Abnahme mit fremdem Datenordner (leerer Ordner ausserhalb des Repos, alle
      Server bedienen, danach keine neuen Dateien im Repo), Startoption --data-dir in
      der Doku; drei Dauerregeln in CLAUDE.md: keine cwd-relativen Pfade, Unterprozesse
      nur ueber eine Hilfsfunktion, Produktivcode liest nie den eigenen Quelltext.
      Befund aus T-2003: eine leere Datenwurzel hat kein known_freq/ - die Server
      starten mit leerem Katalog, und :8010 kann ohne das Verzeichnis keine
      Katalogdatei anlegen (_write_known_file legt es nicht an). Zu entscheiden:
      Verzeichnis beim Schreiben anlegen und/oder die versionierten Kataloge beim
      ersten Start als Vorlage in die Datenwurzel kopieren.
      Entschieden 2026-10-08: beides. Umgesetzt: neues Modul eb200_core/data_seed.py
      (seed_known_freq, Selbsttest T1-T7) kopiert beim ersten Start die *.json aus
      PROGRAM_ROOT/known_freq nach DATA_ROOT/known_freq - nur wenn dort noch kein
      known_freq/ existiert und die Wurzeln verschieden sind, ueber ein
      Temp-Verzeichnis mit abschliessendem Umbenennen; Aufruf einmal in
      start_eb200._run() vor dem Serverstart, mit Meldung in Konsole und Log.
      block_admin._write_known_file legt das Katalogverzeichnis bei Bedarf an.
      CLAUDE.md: neuer Abschnitt "Programm- und Datenwurzel" mit sechs Dauerregeln.
      handbuch/arbeit/fakten.md: --data-dir und Protokollpfad. Maschinelle Abnahme:
      Erststart in einen leeren Ordner, sieben Server ohne Geraetezugriff antworten,
      neue Katalogdatei in einer Datenwurzel ohne known_freq/, Repo unveraendert.
      Handvalidierung offen: start_eb200.bat --data-dir <leerer Ordner>, alle
      Ansichten bedienen (Katalog anzeigen und speichern, Aufnahme, Session),
      danach git status ohne neue Dateien.

### Stufe B: Verpackung
- [ ] T-2005 Unterprozesse ueber eine Hilfsfunktion (start_eb200 -> Server,
      analyze_audio -> eye_diagram, analyze_sessions/analyze_spectrum ->
      session_analyze), danach Entscheidung zwischen portablem Ordner mit Embeddable
      Python (T-301) und Nuitka-Build (eine EXE mit Dispatcher, Konsole bleibt).

## Meilenstein 21: D-SCAN-Bedienung (Einstellungen, Raster, Marker, SDR-ZF)
Sammlung OE3GAS 2026-10-09 (elf Punkte; hier die sechs fuer D-SCAN :8000 und den
SDR-Zoom :8030). Grundlage ist der Messlauf Phase 1 vom 2026-10-09 (HEAD 24b5674).
Entscheidungen 2026-10-09 (OE3GAS): das Raster wird Overlay je Wasserfall-Segment und
braucht dafuer eine Segmentliste (T-2010); Marker bleiben eingebrannt, die
Kollisionsaufloesung geschieht beim Einbrennen gegen eine mitwandernde Liste der
Label-Rechtecke; die BW-Zoom-Vorschau rechnet 95 x BW um die Markerfrequenz (Handbuch
3.18.3: Kanalabstand BW/2, 190 Linien) in einer eigenen, im Selbsttest abgesicherten
Funktion, die Info-Box zeigt weiter die Geraetewerte; Ueberschriften der Einstellungen
bekommen ein eigenes Token (Light wie --btn-fg, Dark --accent); Fixed IF setzt ein SDR
voraus, das die ZF (10,7 MHz an X5) empfangen kann - der HackRF haengt an eigener
Antenne und nutzt die Option nicht, sie wird gesperrt, wenn die Untergrenze des
gewaehlten Geraets ueber der ZF liegt; das Spektrum an X5 ist nicht invertiert, die
Option "Invert spectrum" kommt trotzdem. Die uebrigen Punkte der Sammlung (EB200-Adresse,
Toolbar, Dezimalkomma, Katalog :8010, Session-Liste :8020) folgen nach eigenen Messlaeufen.
- [x] T-2006 (2026-10-09) Verweildauer je Kanal bei BW-ZOOM (BUG-0007): updateMtimeInfo()
      teilte die gemessene Sweepdauer durch channelCount() aus den Eingabefeldern - bei
      BW-ZOOM der volle Bereich, gesweept wird aber nur das Zoom-Fenster. Jetzt Nenner
      frame.total desselben Sweeps (lastSweepPoints neben lastSweepElapsedS), vor dem
      ersten Sweep "Dwell time/channel: —" statt einer leeren Zeile. Die MTIM-Schaetzung
      rechnet weiter mit channelCount() (Vorhersage vor dem Sweep). Browser-validiert 2026-10-09 (OE3GAS):
      BW-ZOOM ein, Wert plausibel (Sweepdauer / Punktzahl der Info-Box); zeigt er weiter
      nahe 0, folgt ein Nachtrag mit dem Rohwert elapsed_s.
- [x] T-2007 (2026-10-09) Achsenlabels (BUG-0008): Randlabel und Tick-Label ueberlappten
      ("142.825 MHz144 MHz"), weil die Kollision mit einem festen Randabstand (60 px
      bzw. 56 px) geprueft wurde, ein langes Randlabel aber selbst so breit ist. Jetzt
      tickLabelFrei(ctx, x, label, w, startLabel, stopLabel) in waterfall.py mit
      measureText und 6 px Luft (AXIS_LABEL_GAP_PX), genutzt von paintFreqAxisStrip und
      drawAxis; sdr_waterfall.py drawAxis rechnet gleich (LABEL_GAP_PX). Das Randlabel
      hat Vorrang. Dieselbe Rechnung in analyze_sessions.py drawFreqAxis (vorher 56 px)
      und analyze_spectrum.py drawNiceFreqAxis (vorher 50 px, relativ zum Datenbereich).
      Rechenprobe mit Attrappen-ctx (6 px je Zeichen) belegt: Tick bei x = 70 neben
      "142.825 MHz" faellt weg (frueher gezeichnet), x = 120 bleibt. Browser-validiert
      2026-10-09 (OE3GAS): Bereich 120-179,9 MHz mit BW-ZOOM, Achse im Wasserfall, unter dem Spektrum
      und im SDR-Zoom; Achsen in :8020 und :8040 ohne Ueberlappung.
- [x] T-2008 (2026-10-09) Einstellungstafel zweispaltig: #sidePanel 440 px breit (vorher
      330), Bloecke mit Rasterzeile .cols2 - Scan range (Start/Stop links, "Apply" rechts
      ueber beide Zeilen, vorher "Apply range"), Bandwidth & speed (BW, Speed, darunter
      Dwell time), neuer Block BW-ZOOM (Schalter links, rechts "Zoom window ~ ... MHz"
      aus bwZoomWindow: 95 x BW um die Bereichsmitte, ganzzahlig abgerundet wie der
      Server; folgt Start/Stop beim Tippen und der BW), Gain unveraendert, Extra zoom
      with SDR ohne Glyphe, Recording mit "Session recording" und "Long Run setup"
      nebeneinander (auch longrunBtnZ in der Zoom-Tafel heisst jetzt so). Ueberschriften
      groesser in var(--accent) - das vorhandene Token ist in Light genau die Schrift
      der Outline-Knoepfe (#1a6fb5) und in Dark hellblau (#8cf), ein eigenes Token ist
      damit unnoetig, ui_theme.py bleibt unveraendert. Tooltips fuer Select SDR,
      Session recording, Long Run setup und die Fenster-Vorschau. Fixed IF und das
      ZF-Feld kommen mit T-2009 in die rechte Spalte des SDR-Blocks. Glossar: zoom
      window, Long Run setup. Abnahmeliste docs/abnahme/waterfall.md Abschnitt 2
      nachgezogen. Rechenprobe: bwZoomWindow(120 MHz, 179.9 MHz, 150 kHz) ergibt
      142.825-157.075 MHz. Browser-validiert 2026-10-09 (OE3GAS): Tafel hell und dunkel, Vorschau gegen
      die Info-Box nach "Enable BW-ZOOM" bei zwei BW-Werten, kein Ueberlauf der Tafel.
- [~] T-2008a (2026-10-09) Einstellungstafel als Raster 2 x 3 nach Entwurf OE3GAS: Scan
      range | Bandwidth & speed, BW-ZOOM | Gain, Extra zoom with SDR | Recording; #sidePanel
      620 px breit (vorher 440) mit display:grid, Kopfzeile mit Einklapp-Taste und
      Trennlinie (::before), eigenes #sidePanel.collapsed. In den Bloecken BW/Speed
      untereinander (Labels voll breit), BW-ZOOM mit der Vorschau unter dem Knopf,
      Recording mit beiden Knoepfen untereinander; Ueberschriften 1.05em. Nur CSS, keine
      IDs oder Skripte geaendert. Abnahmeliste Abschnitt 2 nachgezogen. Handvalidierung
      offen: Tafel hell und dunkel, Einklappen und Aufklappen, kein Ueberlauf der Bloecke.
- [~] T-2008b (2026-10-09) Einstellungstafel schmaeler (Befund OE3GAS zu T-2008a, Tafel zu
      ausladend): Zoom-Vorschau bricht vor "(width ...)", Dwell-Zeile vor "(measured from
      sweep)" um - Zeilenmarke per String.fromCharCode(10), Anzeige mit white-space:pre-line;
      #sidePanel 540 px (vorher 620), Auswahlfelder BW/Speed 7.5em (vorher 9.5em).
      Handvalidierung offen: Tafel hell und dunkel, beide Zeilen umgebrochen, kein Ueberlauf.
- [!] T-2009 SDR Fixed IF: Schalter und ZF-Feld (Vorgabe 10,7 MHz), Option Invert
      spectrum, Abstimmung des SDR auf die ZF, Achse/Katalogstreifen/Klickfrequenz im
      SDR-Zoom in HF; gesperrt fuer Geraete, deren Untergrenze ueber der ZF liegt.
      Zurueckgestellt 2026-10-09 (OE3GAS): X5 liefert 10,7 MHz +-5 MHz um die Frequenz,
      auf die der EB200 GERADE steht - im D-SCAN sweept er, an X5 kommt dann nur ein
      verschmiertes Spektrum; der EB200 muss also fest abgestimmt sein (Ablauf offen:
      Ziehen stimmt ab oder nur aus dem IFPAN-Zoom). Kein angeschlossenes SDR empfaengt
      heute 10,7 MHz am X5 (HackRF an eigener Antenne, Airspy ab 24 MHz). Wieder
      aufgreifen, sobald eines am X5 haengt.
- [x] T-2010 (2026-10-09) Segmentliste im Wasserfall: segmente[] mit {oben, unten,
      startHz, stopHz, ticks, grid, offen}, segmente[0] ist das offene, waechst nur
      unten; shiftHistory fuehrt die Grenzen mit und verwirft Segmente unterhalb
      HISTORY_ROWS; der SSE-Handler schliesst das alte Segment VOR dem Verschieben fuer
      den Kopf und legt danach mit segmentNeu das neue an (Kopfzeilen gehoeren zu keinem
      Segment); clearWaterfallHistory beginnt mit einem frischen Segment. currentTicks
      entfaellt (Ticks je Segment). Keine sichtbare Aenderung. Rechenprobe: zwei Sweeps,
      Kopf mit 56 Zeilen, zwei Sweeps ergibt [0,2) neu und [58,60) alt; nach weiteren
      5945 Zeilen faellt das alte Segment heraus.
- [x] T-2011 (2026-10-09) Raster als Overlay je Segment: zeichneRasterOverlay zeichnet in
      renderViewportO und im JPEG-Export die Nice-Ticks jedes Segments mit grid, Linien
      3 px dunkle Kontur unter 1 px Farbe (bei Schwarz helle Kontur), Deckkraft 10-100 %,
      Farben White/Black/Cyan/Magenta, gestrichelt wahlweise; waagrechte Zeitlinien aus
      rasterZeiten (je 10 s eine Marke, wandern mit) im Intervall Auto/10 s/30 s/1 min/
      5 min/Off, Uhrzeit links/mittig/rechts auf dunklem Kasten; Auto waehlt das kleinste
      Intervall mit mindestens 40 Zeilen Abstand. Popover #gridCfg am Grid-Knopf (Muster
      #detectCfg), Einstellungen in localStorage eb200_wf_grid_v1. Grid schaltet nur
      segmente[0].grid, rueckwirkend im ganzen Segment; ein neues Segment uebernimmt den
      Zustand. bakeVerticalGridRow0 und gridEnabled entfallen, nichts mehr wird
      eingebrannt. Die 30-s-Zeitmarken im Gutter bleiben unveraendert. Glossar: grid
      settings, opacity, dashed, time lines, label position. Abnahmeliste Abschnitt 3
      ergaenzt. Browser-validiert 2026-10-09 (OE3GAS): Raster an/aus, Umstellung von Bereich und BW mit
      aktivem Raster, alle Farben auf hellem und dunklem Wasserfall, Zeitlinien und
      Label-Lage, Neuladen behaelt die Einstellungen, JPEG enthaelt das Raster.
- [x] T-2011a (2026-10-09) Rechter Rand des Wasserfalls verdeckt (BUG-0009): Befund aus
      der Handvalidierung von T-2011 - die Uhrzeit der Zeitlinien in Lage "Right" war
      abgeschnitten, ebenso schon vorher die rechten Labels der eingebrannten
      Achsstreifen ("99.425 M", "108 M"). Ursache: .wf-scroll war genau so breit wie
      wf-o, der senkrechte Scrollbalken (overflow-y:auto) erscheint mit der ersten
      Historie INNERHALB und deckt die rechten ~17 px ab. Jetzt overflow-y:scroll,
      Breite in applyCanvasWidth() gemessen (--wf-scrollbar) und auf .wf-scroll sowie
      --content-width aufgeschlagen; Reserve in CANVAS_W von 28 auf 46 px, damit die
      Seite nicht waagrecht ueberlaeuft. Browser-validiert 2026-10-09 (OE3GAS): rechte Uhrzeit und
      rechte Achslabels vollstaendig, Farbskala rechts daneben, kein waagrechter
      Seitenscrollbalken.
- [~] T-2012 (2026-10-09) Marker-Labels ohne Ueberlappung: paintClickMarker sucht fuer den
      Frequenz-Chip eine freie Lage gegen chipRechtecke (alle gebrannten Chips, wandern in
      shiftHistory mit, geleert in clearWaterfallHistory) - rechts, links, dann je zwei
      Stufen darueber/darunter mit Fuehrungslinie (Kontur wie das Kreuz), dann kuerzere
      Beschriftung ("97.9200", ".9200"); ohne Platz zaehlt der naechstgelegene Chip mit
      ("+n"). Kasten deckend schwarz mit hellem Rand (vorher 85 % und 60 %).
      paintAutoMarkers zeichnet je Sweep nach Pegel absteigend, Zaehler und Markerliste
      behalten die Serverreihenfolge. Regler "Label spacing" (0-20 px, Vorgabe 4) im
      Auto-marker-Menue; "Marker with frequency" bleibt der Schalter fuer Labels ein/aus.
      Gilt fuer Klick- und Auto-Marker gleichermassen. Grenze, bewusst: gebrannte Chips
      werden nicht neu angeordnet, ein spaeter staerkerer Marker verdraengt keinen
      frueheren; "+n" klappt keine Liste auf - dafuer gibt es die Markerliste. Glossar:
      label spacing. Rechenprobe: elf Marker an derselben Stelle ergeben zehn Lagen
      (zwei auf Hoehe, acht gestaffelt) und einen mitgezaehlten. Handvalidierung offen:
      UKW-Band mit Auto marker, eng liegende Sender (z. B. 91.5/91.9 MHz, 99.87/99.90 MHz),
      Fuehrungslinien auf hellem Signal sichtbar, Regler wirkt auf neue Chips.

## Meilenstein 22: Suite-Bedienung (Geraeteadresse, Toolbar, Eingaben, Katalog, Sessions)
Die uebrigen Punkte der Sammlung OE3GAS 2026-10-09 (4, 7, 8, 10, 11). Entscheidungen
2026-10-09 (OE3GAS): die EB200-Adresse kommt aus device.json in der Datenwurzel, gilt nach
einem Neustart von :8000 (kein Live-Umschalten des Controllers) und wird auf der
Statusseite (EB200 Status) eingestellt.
- [~] T-2013 (2026-10-09) EB200-Adresse konfigurierbar: bisher fest in waterfall.py
      (EB200_IP = "10.0.0.55", SCPI_PORT = 5555), einziger Server mit Geraeteverbindung;
      LOCAL_IP wurde schon automatisch ermittelt (determine_local_ip). Jetzt
      lade_device_config/pruefe_device_eingabe/speichere_device_config in waterfall.py,
      device.json {eb200_host, scpi_port, local_ip} in der Datenwurzel, Vorgabe wie bisher;
      --eb200-host auf der Kommandozeile hat Vorrang (start_eb200.py reicht es nur an
      waterfall weiter). Statusseite: Block "Connection" mit Adresse, Port, lokaler Adresse,
      "Test connection" (POST /api/device/test: eigene kurze SCPI-Verbindung, *IDN?) und
      "Save" (POST /api/device, schreibt ueber Temp-Datei, Fehler als Code), GET /api/device
      liefert gespeicherte und aktive Werte. Befund A3a (Konfiguration nach station.json)
      damit erledigt - bewusst eigene Datei, station.json lesen die Katalog-Server.
      device.json in die Liste der nie gestagten lokalen Konfiguration (CLAUDE.md),
      Handbuch 5.2 neu, Glossar, Abnahmeliste Abschnitt 7. Selbstprobe der drei Funktionen
      ohne Server. Handvalidierung offen: Statusseite, Test mit richtiger und falscher
      Adresse, Speichern, Neustart, "In use" zeigt device.json.
- [ ] T-2014 Toolbar-Umbruch (Punkt 8; pruefen, ob identisch mit BUG-0002): Button-Texte
      einzeilig, bei Platzmangel nur Icon mit Tooltip, dann zweite Zeile.
- [ ] T-2015 Dezimalkomma (Punkt 10): Komma und Punkt in allen Frequenzfeldern, ungueltige
      Eingabe rot markiert.
- [ ] T-2016 Katalog :8010 (Punkt 7): Tooltips, Arbeitsverzeichnis unter dem Dateinamen,
      "File note"/"Channel note", Hinweistexte in Tooltips.
- [ ] T-2017 Session-Liste :8020 (Punkt 11): Label-Feld so hoch wie Dateiname plus
      Thumbnail, waechst nach unten, kein Resize-Griff; alle Zellen oben buendig.

## Meilenstein 23: Veroeffentlichung (GitHub, Lizenz, Release)

Seit 2026-10-12 liegt MoniScope als oe3gas/moniscope oeffentlich auf GitHub,
GPL-3.0, Release v1.0.0 auf Commit 675756f. Das Veroeffentlichungs-Repo ist
ein Export dieses Repos: EB200repo bleibt die Werkstatt, moniscope die
Auslieferung. Nicht mit ausgeliefert werden docs/prompts/, docs/berichte/,
docs/abnahme/, docs/messungen/, jobs/ und .claude/.

- [x] T-2018 (2026-10-12, Commit 17ea663) SPDX-Lizenzkopf in allen 66 versionierten *.py:
      zwei Zeilen nach der Shebang-Zeile bzw. am Dateianfang, einzeiliger
      SPDX-Hinweis statt vollem GPL-Kopfkommentar. Ohne ihn ist eine
      abgetrennte Einzeldatei lizenzlos.
- [x] T-2019 (2026-10-12, Commits f017800 und 0b6eecc) Exportwerkzeug tools/export_public.py: schreibt den
      Veroeffentlichungsbaum reproduzierbar aus HEAD, statt ihn wie bei v1.0.0
      von Hand zusammenzustellen. Ausgeschlossen sind docs/prompts/,
      docs/berichte/, docs/abnahme/, docs/messungen/, jobs/, .claude/,
      publish/, die alte .gitignore, die beiden known_freq_abgleich-Dateien und
      die vier Streutestskripte (audio_test6/7/8.py, SDR_probe_test.py).
      LICENSE, README.md und .gitignore des oeffentlichen Repos liegen unter
      publish/ und werden darueber gelegt; publish/** steht in .gitattributes
      auf -text, damit die Pruefsummen ueber Klone hinweg halten.
      Neu dabei: device.example.json schliesst die Vorlagenluecke aus T-2013.
      publish/bilder/ wird als Verzeichnis nach bilder/ ueberlagert - dort
      liegen die sieben Bildschirmfotos des README, auf 1800 px gebracht
      (19 MB Originale auf 6,9 MB), weil sie dauerhaft in der Historie
      bleiben. Probelauf 2026-10-12: 147 Dateien, alle sechs Selbsttests
      gegen den exportierten Baum gruen; gegen 0b6eecc bestaetigt (G5 147
      Dateien und sechsmal OK, G6 0, G7 dreimal True und 7).
      Befund aus dem ersten Lauf: der Auftrag stellte das Exportgate VOR den
      Commit, das Werkzeug liest aber aus HEAD - git show HEAD:publish/LICENSE
      endete mit 128 und der Export brach ab. Die Dateien waren richtig, der
      Auftrag falsch; CC hat korrekt gestoppt. Verworfen wurde die naechst-
      liegende Abhilfe, eine Option zum Export aus dem Index: sie naehme dem
      Werkzeug genau die Eigenschaft, aus einem Arbeitsbaum kein Release
      entstehen zu lassen. Die Regel daraus steht in CLAUDE.md.
- [x] T-2020 (2026-10-12) Bildschirmfotos im README des Veroeffentlichungs-
      Repos (dort Commit 80e737b): der D-SCAN-Wasserfall als Aufmacher, dazu
      IFPAN-Zoom, Spektrumauswertung, Signalvermessung, Aufnahmetabelle,
      MSCAN-Scandefinitionen und Sessionliste in einem zugeklappten
      details-Block. Quellen unter publish/bilder/, auf 1800 px Breite
      gebracht (19 MB auf 6,9 MB), weil sie dauerhaft in der Historie bleiben.

## View-/Menübaum (Stand 2026-08, Ziel nach Meilenstein 12)
```
:8000  D-SCAN Live (waterfall.py) — IFPAN-Zoom — Long-Run-Job planen (DSCAN/IFPAN)
  └─ Ansichten ▾ → :8010 / :8050 / :8020 / :8040 / :8060 / :8070
:8050  Aufzeichnungen (manage_jobs.py)
  ├─ Aktiv / Geplant (Live-Proxy :8000)
  ├─ Aufzeichnungen (alle Arten, Filter Art/Herkunft/Zustand) → Auswertung / Löschen
  └─ MSCAN-Scan-Definitionen (Neu, Bearbeiten /mscan/job, Kanalpicker /mscan/pick, Lauf planen)
:8020  Auswertung Session      (analyze_sessions.py)  ← ?path=
:8040  Auswertung Spektrum     (analyze_spectrum.py)  ← ?path= / ?job=
:8060  Auswertung Kanalscan    (analyze_mscan.py, neu) ← ?job=  → Segment-Spektrum :8070
:8070  Auswertung Audio        (analyze_audio.py, neu) ← ?path= (keine eigene Liste)
:8010  Frequenzkatalog (block_admin.py)   :8030  SDR-Wasserfall (sdr_waterfall.py)
```

## Entscheidungslog
- 2026-09-13: Messauftrag M3 (Reproduzierbarkeit des Hubs ueber die
  Dezimationsstufen). Anlass: zwei Erprobungen derselben Aufnahme mit
  unterschiedlicher Markersetzung (T-1710/T-1711 gegen T-1704) lieferten
  unterschiedliche Kandidatenlisten (bw 750 Hz/N=8: 425 match + 450 match;
  bw 470 Hz/N=16: 450 match + 425 knapp daneben), obwohl sich am Signal
  nichts geaendert hatte. Zu klaeren war, ob die 10-Hz-Abweichung im
  Tonabstand aus der Quantisierung des Histogramms stammt oder aus einem
  systematischen Anteil, etwa der Flanke des Dezimationstiefpasses.
  Messreihe an records/20260912_202830_7646kHz_IQ.wav, Fenster 0:10-0:30,
  feste Abstimmung --tune -85, Dezimation N=2/4/8/16:
  N=2 (fs_eff=16000 Hz, Bin 39,8 Hz): Abbruch "nur ein Pegel erkannt, kein
  FSK-Charakter". N=4 (fs_eff=8000 Hz, Bin 19,9 Hz): Abbruch
  "Autokorrelationsflanke zu kurz, Symbolrate nicht bestimmbar". Beide
  brechen mit klarer Meldung ab und liefern keinen falschen Wert - das
  gewuenschte Verhalten und ausdruecklich Teil des Befunds, kein Mangel.
  N=8 (fs_eff=4000 Hz, Bin 9,95 Hz): Hub 218,9 Hz, Tonabstand 437,8 Hz,
  Kontrast 4,20 dB, Flatness 0,6920, Symbolrate grob/fein 49,25/50,00 Bd,
  Feinabgleich R2=1,00, Augenoeffnung 93,8 %, Nulldurchgangsraster halb bei
  0,26/0,76 T (Konzentration 1,00), Jitter Halbbit-Raster 1,1 %, Kandidaten
  425 match + 450 match.
  N=16 (fs_eff=2000 Hz, Bin 4,98 Hz): Hub 223,9 Hz, Tonabstand 447,8 Hz,
  Kontrast 4,36 dB, Flatness 0,7276, Symbolrate grob/fein 49,24/50,00 Bd,
  Feinabgleich R2=0,97, Augenoeffnung 94,2 %, Nulldurchgangsraster halb bei
  0,24/0,74 T (Konzentration 1,00), Jitter Halbbit-Raster 1,1 %, Kandidaten
  450 match + 425 knapp daneben.
  **Auswertung: beide Hubwerte sind exakte Bin-Mitten** (218,90 Hz ist bei
  9,9502 Hz Bin-Breite genau Bin 122, 223,88 Hz ist bei 4,9751 Hz Bin-Breite
  genau Bin 145). find_histogram_peaks() interpoliert nicht unterhalb der
  Bin-Breite, die Peaklage rastet hart auf das Gitter; die Unsicherheit je
  Peak betraegt eine halbe Bin-Breite (N=8: Hub in [213,9; 223,9], N=16: Hub
  in [221,4; 226,4]). Die Intervalle ueberlappen. **Kernsatz: die 10-Hz-
  Abweichung im Tonabstand ist vollstaendig durch Quantisierung erklaert,
  nicht durch die Filterflanke** - aus zwei verwertbaren Punkten ist ein
  systematischer Anteil aus der Dezimationstiefpassflanke weder nachweisbar
  noch zu seiner Erklaerung noetig. Die Aufweitung um einen Bin aus T-1701a
  bleibt gueltig, sie wird durch diesen Befund nicht infrage gestellt.
  Die Folgerung geht in die Gegenrichtung der urspruenglichen Vermutung:
  **nicht die Toleranz muss wachsen, die Messung muss genauer werden**. Eine
  Parabel durch die drei Bins um das Maximum (dieselbe Technik wie die
  Maximumsverfeinerung in refine_symbol_rate(), T-1711) liefert rund ein
  Zehntel Bin-Breite statt einer halben - siehe T-1714. Danach duerfte der
  Zusatzterm der Frequenztoleranz aus T-1701a schrumpfen statt zu wachsen.
  Belastbarerer der beiden verwertbaren Laeufe ist N=16: feinere Aufloesung,
  beide Toene liegen bei +-224 Hz weit innerhalb des Durchlassbereichs von
  +-400 Hz, und 447,8 Hz liegt dicht am Normhub 450 Hz - das ist eine
  Einschaetzung der Messqualitaet, kein zusaetzlicher Beweis fuer einen der
  beiden Kandidaten.
- 2026-09-13: Messauftrag M2 (Tonwechsel oder Koexistenz, tools/tone_probe.py).
  Pearson-Korrelation und Koinzidenzanteil der beiden Pegelzeitreihen an
  records/20260912_202830_7646kHz_IQ.wav (--t0 10 --t1 30 --tune -75
  --auto-decimate 750): r=-0,862, Koinzidenzanteil 0,004. Vergleichsanker
  synthetisches 2-FSK (50 Bd, 437,8 Hz Tonabstand, 10 dB SNR): r=-0,903,
  Koinzidenzanteil 0,022. Die echte Aufnahme liegt nahe am Anker und deutlich
  unter der _fsk_corr-Schwelle (-0,4) - Beleg, dass diese Aufnahme echtes FSK
  ist (Tonwechsel), nicht zwei gleichzeitige Traeger.
- 2026-09-13: Feinabgleich der Symbolrate (T-1711). Ausgewertet wird die
  ZWEITE Harmonische der zirkularen Resultierenden ueber den Nulldurch-
  gangslagen, nicht die erste: ein Halbbit-Raster besteht aus zwei um T/2
  versetzten Populationen, die in der ersten Harmonischen gegenphasig sind
  und sich zu R1~0 ausloeschen, obwohl das Raster perfekt ist. In der
  zweiten Harmonischen aendert eine Verschiebung um T/2 das Argument
  2*pi*2*x/T um genau 2*pi - ganzes und halbes Raster liegen dort
  gleichermassen bei R2~1. Mit h=1 waere der Halbbit-Fall dieser Aufgabe
  unsichtbar geblieben (gemessen --selftest T35.2: R1=0,02 gegen R2=1,00).
  Der Suchbereich bleibt an die grobe Schaetzung gebunden (+-
  BAUD_TOL_ZUFALLSFOLGE) und ist NICHT beliebig erweiterbar: R2(T) hat
  Nebenmaxima bei T/2, 2T und weiteren Vielfachen der wahren Periode (Alias
  der Harmonischen) - ohne eine hinreichend genaue grobe Schaetzung wuerde
  die Suche in eines dieser Nebenmaxima statt in die richtige Periode
  laufen.
  Eine abgelehnte Verfeinerung wird immer ausgewiesen (baud_refined=false,
  eine Begruendungszeile in print_report()) und nie still verschluckt: ein
  Feinabgleich, der bei schwachen Signalen unbemerkt danebengreift, waere
  schlimmer als keiner - er machte die Augenoeffnung zu einer Zufallszahl,
  die nach Praezision aussieht.
  Nachtrag zu T-1710: der dort an der echten Aufnahme gemeldete Befund
  ("unclear", 10 Haeufungen, Widerlegung der Halbbit-Hypothese) war ein
  Artefakt der ungenauen Symbolrate (1,53 % Fehler, rund 15 Perioden Drift
  ueber die Aufnahme), keine echte Widerlegung - siehe Nachtrag an T-1710
  und T-1711.
- 2026-09-13: Nulldurchgangsraster (T-1710). jitter_pct bleibt bewusst
  unveraendert (kein Ersetzen, kein Umrechnen); jitter_half_pct tritt als
  eigenes Feld daneben (nur gesetzt bei crossing_grid=="half"), berechnet mit
  derselben Funktion jitter_percent() gegen T/2 statt T.
  Korrektur zum eigenen Leitsatz der Aufgabe: crossing_concentration trennt,
  wie gemessen (--selftest T34.4a/4b), NICHT allgemein "diskrete Haeufung"
  von "Verschmierung" bei Mehrwegeausbreitung. Ein STATISCHER Zweiwegekanal
  (Verzoegerung < 1 Symboldauer) verschiebt bei binaerem FSK die Nulldurch-
  gangslage nur deterministisch (jeder Uebergang sieht dieselbe Interferenz-
  geometrie) - das gilt auch mit einer zusaetzlichen, zeitvarianten Phase
  (Doppler bis 2 Hz getestet). Die Kennzahl trennt tatsaechlich "stabiles
  Raster" von "zeitveraenderlichem Kanal". An der echten Aufnahme (siehe
  T-1710-Erprobung) zeigt sich das als "unclear" mit niedriger Konzentration
  (0,51) - vertraeglich mit, aber kein Beweis fuer Mehrwegeausbreitung, wie
  vom Leitsatz gefordert ausdruecklich nicht gedeutet.
- 2026-09-13: Nachdetektionsfilter (T-1709). Er wirkt NUR auf Taktlage
  (find_clock_phase), Augendiagramm (build_eye_traces), Augenoeffnung
  (values_at_symbol/eye_opening) und Jitter (zero_crossings/jitter_percent).
  Burst-Erkennung (detect_bursts, histogram_flatness),
  freq_offset_and_deviation() und estimate_symbol_duration() bleiben
  ungefiltert, weil ihre Schwellen (FLATNESS_THR, BURST_GAP_FLATNESS,
  BAUD_TOL_ZUFALLSFOLGE/-WECHSELFOLGE) an ungefilterten Daten gemessen sind
  und eine Mittelung die Autokorrelationsflanke systematisch verschieben
  wuerde. Gegenbefund: der Filter verkleinert den Jitter bei stark
  verrauschten, hoch ueberabgetasteten Signalen NICHT wie erwartet, sondern
  vergroessert ihn reproduzierbar - siehe Kommentar an POSTDET_FILTER_REL in
  tools/eye_diagram.py und PLAN.md T-1709.
- 2026-09-01: eye_diagram.py (T-902 bis T-906) - verworfene Ansaetze, festgehalten
  damit diese Wege nicht erneut beschritten werden:
  (E1) Frequenzoffset per Median der Momentanfrequenz - der Median einer bimodalen
  Verteilung liegt immer auf einem der Pegel, nie dazwischen. Ersetzt durch den
  Mittelpunkt der aeussersten Histogramm-Peaks.
  (E2) Symbolrate per 10. Perzentil der Nulldurchgangsabstaende - systematisch zu
  kurz, greift die untere Verteilungsflanke ab.
  (E3) Symbolrate per erstem Autokorrelationsmaximum nach dem Nulldurchgang -
  existiert bei zufaelligem NRZ nicht (dreieckfoermige AKF), 29 von 30 Testlaeufen
  fanden nichts.
  (E4) Symbolrate per Nulldurchgang der Autokorrelation - im Prinzip richtig, an
  der Nullstelle aber schlecht konditioniert; in 9 von 30 Laeufen Sprung auf einen
  spaeteren Zufallsnulldurchgang, Fehler bis 303 %. Ersetzt durch Geradenanpassung
  an die Flanke 0,25 bis 0,75 mit Extrapolation auf die Nullstelle.
  (E5) Burst-Erkennung per reinem Amplitudenschwellwert - eine relative Schwelle
  skaliert sich selbst mit und trennt stationaeres Rauschen nicht von einem
  Dauersignal. Ersetzt durch ein zweistufiges Verfahren.
  (E6) Peakigkeitsmass max/mean - skaliert invers mit der Stufenzahl, also nicht
  stufenunabhaengig. Ersetzt durch normierte Entropie (Flatness).
  (E7) Feste Schaerfeschwelle im PSK-Pfad - die Kennzahl folgt fuer Rauschen der
  Extremwertstatistik und waechst mit ln(N), ist also von der Aufnahmedauer
  abhaengig. Ersetzt durch 4 * ln(N).
  (E8) Strikte Groesser-Vergleichs-Peaksuche - uebersieht Plateaus aus exakt
  gleichen Nachbarbins. Behoben in T-906.
- 2026-09-01: eye_diagram.py - Schwellwerte, gemessen 2026-09-01 (Begruendung
  jeweils als Kommentar bei der Konstante in tools/eye_diagram.py):
  FLATNESS_THR = 0,94 (FSK2 0,719-0,722, FSK4 0,878-0,880, Rauschen 0,999);
  BAUD_TOL_ZUFALLSFOLGE = 0,06 und BAUD_TOL_WECHSELFOLGE = 0,01 (intrinsische
  Streuung +-2,5 % 1 Sigma, max. 5,53 %, unabhaengig von SNR und Burstlaenge);
  SCHAERFE_FAKTOR = 4,0 mit Schwelle 4 * ln(N) (BPSK 1279,9, QPSK 369,3,
  8-PSK 116,0, Rauschen 3,86-4,21).
- 2026-09-01: Negativbefund eye_diagram.py (Lesebeispiel fuers Handbuch): Aufnahme
  auf 162,475 MHz, fuer POCSAG gehalten, Abbruch mit "Autokorrelationsflanke zu
  kurz". Diagnose: aeusserste Histogramm-Peaks bei +-7,08 kHz gegen eine
  Filterhalbbreite von 7,5 kHz (ZF-Bandbreite 15 kHz), Autokorrelation bei Lag 1
  bereits unter 0,31 - das Signal war breiter als das Filter und keine
  1200-Bd-FSK. Der Abbruch war korrektes Verhalten.
- 2026-08-31 (Abschluss): Meilenstein 14 umgesetzt und validiert - alle acht Server
  auf eb200_core/ui_theme.py. Erfahrungen, die fuer kuenftige UI-Arbeit gelten:
  (1) BASE_CSS faerbt jede <canvas>; GESTAPELTE Canvas brauchen oben
  "background:transparent", sonst verdeckt der Overlay den Plot (Befund T-1404a).
  (2) Flaechen, die AUF einer Canvas liegen (Infokaesten, Listen), nehmen die Tokens
  --canvas-overlay-* und bleiben in beiden Themes dunkel (T-1407).
  (3) Beschriftung NEBEN der schwarzen Flaeche (Farbskala) liest ihre Farbe zur
  Zeichenzeit per getComputedStyle und zeichnet auf das Ereignis "themechange" neu;
  Zwischenspeicher wie colorbarLast muessen dabei zurueckgesetzt werden (T-1407a).
  (4) Layout-Konstanten in JS (SIDE_PX in :8040, SEG_SIDE_PX in :8020) kennen
  die Kartenmasse nicht - nach Layout-Aenderungen nachrechnen (T-1409a/T-720).
  (5) Bedienglyphen als Inline-SVG mit fill:currentColor, nie als emoji-faehiges
  Unicode; das gilt auch fuer Glyphen in JS-Zeichenketten (T-1402/T-1407b/T-1410).
- 2026-08-31: Light Theme (Meilenstein 14). Light ist Standard, Dark bleibt waehlbar
  (kein prefers-color-scheme-Automatismus - Entwickler-Systeme wuerden sonst still
  Dark liefern). Persistenz per Cookie eb200_theme auf dem Host: Cookies ignorieren
  den Port, die Wahl gilt damit ueber alle acht Server; localStorage waere je Port
  getrennt. Kein Server-Zustand, nichts in station.json. Ein Head-Skript setzt
  data-theme vor dem ersten Paint. Farben ausschliesslich als Tokens in
  eb200_core/ui_theme.py (stdlib, reine Strings; kein Byte-Sync noetig, da alle
  Server eb200_core importieren). Canvas-Flaechen bleiben in beiden Themes dunkel.
  Reihenfolge: T-403 (Dialoge) erst nach T-1400, damit das Modal tokenbasiert
  entsteht; Lokalisierung nach Meilenstein 14, damit Handbuch-Screenshots nur
  einmal anfallen.
- 2026-08-30: Katalogdateien (known_freq/*.json): nur FNV.json wird committet
  (alleinige autoritative Quelle fuer Zuweisungsbaender, siehe T-111). Alle
  uebrigen Katalog-JSONs bleiben untracked/lokal und werden von CC nie
  gestaged — analog zu station.json/storage_roots.json. Der Selftest
  _self_test_real() in known_freq.py setzt damit den lokalen Katalogbestand
  voraus und ist auf einem frischen Checkout nicht aussagekraeftig.
  REVIDIERT 2026-09-20 (T-1307) - die Regel gilt nicht mehr und entsprach zuletzt
  auch nicht mehr dem Repo: versioniert sind 18 Referenzkataloge, und die
  FNV-Datei heisst FNV_AT_2026.json. Neue Regel: Referenzkataloge (nationale
  und internationale Band- und Dienstzuweisungen) werden versioniert und mit
  ausgeliefert; persoenliche oder abgeleitete Kataloge bleiben lokal und
  stehen namentlich in .gitignore (siehe T-1306). Kein Sammelmuster ueber
  known_freq/*.json - ein neuer Katalog taucht in git status auf und wird
  bewusst entschieden. Folge fuer Meilenstein 18: die Suite liefert damit
  deutsche Anzeigetexte (label, service, title) in den Katalogen mit aus,
  deren Sprache ist bei der Umstellung auf Englisch gesondert zu entscheiden.
  Der Hinweis zu _self_test_real() gilt eingeschraenkt weiter: ein frischer
  Checkout hat die Referenzkataloge, aber nicht den persoenlichen Bestand.
- 2026-08: „Ein Port, ein Tag" für Aufnahme-Streams — Sequenznummer läuft pro
  UdpPath, Lückenzählung sonst nicht zuverlässig (Hardware-validiert).
- 2026-08: Long-Run = Flatfile serverseitig, Browser nur Konfigurator/Viewer;
  Session-DB (SQLite) bleibt für interaktiven Kurzbetrieb.
- 2026-08: Dauerträger-Klassifikation über excess_db statt Duty (Duty
  selbstkontaminierend bei quasi-kontinuierlichen Trägern).
- 2026-08: eb200_core/ bleibt stdlib-only; numpy-Module leben im Repo-Root.
  REVIDIERT 2026-09-03 - siehe Eintrag "Abhaengigkeitsregel neu gefasst" am Ende des
  Entscheidungslogs. Die Regel war faktisch bereits gebrochen (signal_detect.py).
- 2026-08: SoapySDR-Python verworfen (Device.make() defekt auf Ziel-Windows);
  SDR-Backend via hackrf_transfer (NativeCliSource).
- 2026-08: Gemeinsame JS-Render-Schicht zurückgestellt (T-401), bis
  Duplizierungsaufwand den Umbau rechtfertigt.
- 2026-08: Design-Grundsatz „Auswerte-Server sind rein lesend" aufgegeben —
  :8020/:8040 schreiben bereits Metadaten (Labels, Notizen, Befund-Sidecars).
  Verbleibende Invariante: Messdaten (Session-DBs, .specbin) werden nie
  verändert; jeder Client-Pfad läuft durch die Sandbox.
- 2026-08: Block-Sync per tools/sync_known_block.py (Referenz waterfall.py,
  --check als Gate); manuelles Kopieren des JS-Blocks entfaellt.
- 2026-08: Known-Freq-Editor in block_admin (:8010): Kanaele strukturiert,
  Baender/Segmente/Raster per validiertem JSON-Editor (Loader-Validierung im
  Temp-Spiegel); Anzeige-Server laden den Katalog mtime-basiert neu
  (load_catalog_cached).
- 2026-08: Kanal-Editor: leeres Feld = geerbt (defaults bleiben wirksam),
  gesetztes Feld = expliziter Override; Leeren entfernt den Override.
- 2026-08: Messdaten-Invariante erweitert (T-804): Loeschen GANZER Datensaetze
  (Session-DB inkl. Beidateien, .specbin inkl. Sidecars) aus den Views ist
  erlaubt; das Veraendern von Messdaten-Inhalten bleibt verboten. Schutz:
  laufende Sessions/Aufnahmen und Long-Run-Teildateien werden verweigert (409).
- 2026-08: Geraeteparameter-Sync (T-703) per SCPI-Polling ueber den Snapshot-Weg
  + SSE-Push. Audio-OptionalHeader vom Geraet nicht geliefert (opt_len=0 trotz
  OPT-Flag und Protokollversion 0x26, validiert via tools/test_opt_header.py);
  MOD/BW/Speed/SQL-Schwelle nur per SCPI. DScan-Header zieht bwZoom/mark/
  Zoom-Bereich dagegen live nach.
- 2026-08: Katalog-meta: Datei-Kopf-Formular bedient service/source/region/
  maintainer + Notiz; Bestandsschluessel note vs. notes wird nicht
  normalisiert (Datei behaelt ihren), Neuanlage schreibt note.
- 2026-08: Frequenzblock-Verwaltung (Scanbereiche per Ziehauswahl) wird aufgegeben;
  Scans laufen künftig nur über MSCAN auf Kanälen aus den Known-Frequencies.
- 2026-08: MSCAN-Job = Definition mit stabiler 6-stelliger Zufalls-ID (eigenes Modul);
  jeder Lauf ist ein regulärer job_store-Job vom Typ mscan mit eingefrorenem Kanal-
  Snapshot, damit Ergebnisse trotz Katalogänderungen interpretierbar bleiben.
- 2026-08: MSCAN-Speicherplätze werden direkt per MEMory:CONTents (ASCII-Feld, inkl. ACT)
  programmiert; Readback per MEMory:CONTents? + MPAR?. RX-Umweg nur noch Rückfalloption
  (--via-rx).
- 2026-08: „Nur Sprache" ist Nachklassifikation, kein Aufnahme-Gate. Aufnahmeschwelle 1 s;
  ein Kanal gilt als aktiv, wenn mindestens eine Aufnahme ≥ 1 s vorliegt.
- 2026-08: Gesamte MSCAN-Job-Verwaltung (Definition, Läufe, Ergebnisse) in :8050,
  nicht in :8010.
- 2026-08: MSCAN-Job-Modus inventur/ueberwachung über SENSe:MSCan:COUNt 1/INF. Laufdauer
  ist Lauf-Parameter (start_iso/end_iso, end=None = bis Stopp), nicht Definition.
  Mehrere Zyklen (COUNt n) als spätere Erweiterung offen; Ergebnis müsste dann je Zyklus
  getrennt zählen.
- 2026-08: MSCAN-Kanalzuordnung für Audio-Segmente über den MSCan-Datagrammstrom
  (Frequenz+Pegel je besuchtem Platz), nicht über SCPI-Polling.
- 2026-08: MSCAN-Runner (T-1005): Segmentstart = MScan-Item, Segmentende = 28-Byte-
  Audio-Marker (Rückfall: Lücke > 0,5 s); Start immer über MSCan:CHANnel MEM999 +
  INIT:CONM.
- 2026-08: FNV-Datei ist alleinige Quelle fuer nationale Zuweisungsbaender
  30 MHz-3 GHz; andere Dateien behalten nur Kanaele und Baender mit
  Zusatzstruktur (fnv_ergaenzungen.json).
- 2026-08: „Aufzeichnung“ als gemeinsame Einheit für Session/Spektrum/Kanalscan
  (Meilenstein 11): Übersicht = Lese-Aggregator über bestehende Stores, KEIN Umbau von
  job_store/session_store/spectrum_store. Direkt-Aufnahmen = Aufzeichnung ohne Job-Eintrag
  (Herkunft „direkt“, Zustand „beendet“).
- 2026-08: MSCAN-Definition (mscan_job.py) ist Vorlage, kein Aufzeichnungseintrag; erst jeder
  Lauf (job_store Typ mscan) erscheint in der Übersicht. Definitionsliste lebt direkt auf
  :8050/ (kein /mscan-Zwischenschritt).
- 2026-08: Auswertung je Art als eigener Server (:8020/:8040/:8060); :8050 bleibt Fenster
  und Fernbedienung, nie Auswerter. Portschema :80x0 beibehalten.
- 2026-08: Audio-Auswertung als eigener Server :8070 (analyze_audio.py) OHNE eigene
  Aufnahmeliste; Einstieg nur per Deep-Link aus :8050 (Direkt-Audio, Long-Run-Teile) und
  :8060 (MSCAN-Segmente). Genau ein Ort zum Finden/Beschriften/Löschen bleibt :8050.
- 2026-08: Direkt-Audio (IFPAN-Zoom-Aufnahme) wird NICHT in den job_store überführt —
  Jobs haben Zeitfenster, Kollisionsprüfung und Zustandsmaschine, spontane Aufnahmen nicht.
  Sichtbarkeit in :8050 über recording_index als Art AUDIO / Herkunft direkt.
- 2026-08-29: T-111 umgesetzt und praezisiert: ein Band ausserhalb der FNV bleibt nur,
  wenn es Struktur oder eine Dienst-Sicht traegt, die die FNV nicht hat (eigene Segmente,
  Kanalraster, Bandplan, Satellitensystem), oder ausserhalb 30 MHz-3 GHz liegt; ein Band
  ohne eigene Segmente, das nur einen FNV-Bereich nachzeichnet, wird geloescht. Ziel eines
  VERSCHIEBEN ist bevorzugt eine fachlich passende bestehende Datei (z. B. ham-6m nach
  intl_amateurfunk_baender.json) statt automatisch fnv_ergaenzungen.json - die Sammeldatei
  wird nur angelegt, wenn tatsaechlich ein VERSCHIEBEN dorthin zeigt (siehe
  tools/known_freq_abgleich.py --entscheidungen, docs/known_freq_abgleich_entscheidungen.json).
- 2026-08: MSCAN-Segment-WAVs erscheinen ausschließlich in der MSCAN-Auswertung (:8060),
  nie als Einzelzeilen in :8050 (Mengenproblem bei hunderten Kurzsegmenten).
- 2026-08: Audio-Spektrum wird serverseitig gerechnet (numpy-STFT im Repo-Root, uint8-Matrix,
  Max-Hold-Übersicht ≤ 8000 Zeilen, Cache .wav.audspec) — Browser-decodeAudioData scheidet
  für Stunden-Dateien aus (≈ 230 MB Float32/h im geteilten Chromium-Renderer). Für die
  Wiedergabe von Dateien ist <audio> mit Range-Requests korrekt; das Verbot aus WS2 gilt
  nur für Endlos-Streams.
- 2026-08: Ad-hoc-Ablage RECORD_DIR wird durch den ersten storage_root ersetzt (T-1205), damit
  Schreib- und Lese-Whitelist aller Server aus einer Quelle kommen.
- 2026-08: Löschen aller Aufzeichnungsarten zentral in recording_index.delete_recording (Sandbox,
  Live-Sperre, Long-Run-Schutz an einer Stelle); fehlender Sidecar sperrt nicht mehr dauerhaft,
  sondern nur 60 s nach letztem Schreibzugriff.
- 2026-08: Alle Long-Run-Flatfiles (ifpan und dscan-maxhold) werden in :8040 ausgewertet; :8020
  bleibt der Session-DB-Auswerter für interaktive Aufnahmen.
- 2026-08-27: MSCAN-Hardwareabnahme T-1005: audio_orphan ist zu ~23 Datagrammen Startburst des Audio-Pfads
  (vor dem ersten MScan-Item) – Zähler wird in Vorlauf/dazwischen/Nachlauf geteilt, nur „dazwischen" ist ein
  Warnsignal. Überwachung mit einem einzigen offenen Kanal läuft sichtbar „auf einem Kanal" (Dwell → Rundlauf →
  gleicher Kanal), Zyklenzähler belegt den Rundlauf. Laufliste zeigt reales Ende (ended_iso) statt geplantem.
  WAV-Auslieferung nur über Manifest-Whitelist innerhalb storage_roots.
- 2026-08-28: Dialog-Vereinheitlichung (T-403): natives confirm()/alert() wird durch ein
  <dialog>-Modal ersetzt. Gründe: Chrome-Kopfzeile wirkt wie Fehlermeldung; confirm()
  hat Fokus-Default auf OK (Enter löscht endgültig — inakzeptabel); keine Gestaltung
  möglich. Inline-Bestätigung in der Tabellenzeile wurde verworfen (Reflexklick-Risiko
  bei destruktiven Aktionen). Rohe Pfade im Dialogtext (z. B. sessions/session_*.db)
  werden bei der Umsetzung durch lesbare Aufbereitung ersetzt (Datum/Uhrzeit aus
  Dateinamen, Art der Aufnahme; Pfad nur als Zusatzinfo).
- 2026-08: Revision T-1103 - laufende Direktaufzeichnungen erscheinen
  in der Laufend-Leiste (bisher bewusst unsichtbar); Audio-Sidecar traegt ab
  sofort running-Flag ab Start. Kein Fernstopp aus :8050 (Stoppen bleibt im
  Zoom :8000).
- 2026-08-29: T-112 - Bei Kanal-Doppelung zwischen alter Datei und neuer
  Primaerquelle uebernimmt der neue Eintrag die alte id (legacy_id_from);
  IDs werden nie aufgegeben.
- 2026-08: Ansichten-Menue (:8000) fuehrt nur Server mit eigenstaendigem
  Einstieg; Deep-Link-Server (:8060 MSCAN-Auswertung, :8070 Audio-Auswertung)
  erscheinen nicht im Menue (Revision der urspruenglichen T-1107-Planung).
- 2026-08-29: T-113/T-114 - FNV-Ebene ist Rahmen, Dienst-Ebene ist Realitaet;
  FNV-Einzelfrequenzen gehoeren zur FNV-Ebene; Region ist reine
  Anzeigefilterung ueber land-*-Tags, konfiguriert in station.json.
- 2026-08-30: T-206 - Scheduler-Herzschlag statt Timeout-Erhoehung: erst
  messen, wo es haengt.
- 2026-08-30: T-201 - Eine Sandbox-Implementierung fuer alle Viewer
  (path_sandbox), Whitelist = Repo-Root + storage_roots; Pfadparameter
  relativ oder absolut, nie ../ ueber das Repo hinaus.
- 2026-08-31: T-1013 - Eine HTTP-Serverbasis fuer alle Server (http_base),
  Client-Abbrueche still, Programmfehler sichtbar.
- 2026-08-31: T-901 - IQ ueber AUDIO-Kanal: Kanalzuordnung I/Q gemessen
  bestaetigt, kein DC-Spike - tools/wav2sdr.py bleibt ohne Default-Swap.
- 2026-08: Produktname "EB200 Suite" -> "MoniScope" (T-1301). "EB200" bleibt
  ausschließlich Gerätename; Identifier (eb200_core, start_eb200.py,
  Cache-Formatkennungen eb200-*-v*) und die Status-Seite unverändert
  (Dateikompatibilität, Geräte-Inventur). Umbenennung start_eb200.py -> eigener Task.
- 2026-08: Audio-Nachklassifikation (T-1007): Kaskade über harte Schwellen statt
  Lernverfahren - nachvollziehbar, in einem Dict kalibrierbar, Kalibrierung über
  Handlabels aus :8070 (T-1007b/c). Sidecar je WAV, nie Manifest.
- 2026-08: Klassifikator-Kalibrierung über Direktaufnahmen im Chunk-Modus (3/5 s) statt über
  MSCAN-Läufe - Direktaufnahmen sind gezielt beschaffbar, Chunks bilden die Segmentlänge nach.
  Handlabels in audio_klassen.json (LabelStore-Muster), nie im Sidecar. Fluss entfiel aus den
  Regeln - er misst Bandbreite (AM 6 kHz vs. USB 2,4 kHz), nicht die Klasse.
- 2026-08: :8070 klassifiziert Einzeldateien automatisch beim Oeffnen - Klassifikator
  deterministisch + Aufnahmen unveraenderlich => erneuter Lauf gleicher Version sinnlos;
  Versionswechsel invalidiert Sidecars selbst. :8060-Batch bleibt expliziter Button (E8).
- 2026-08-31: Vorbereitung Lokalisierung (Bewertung, kein Task). UI bleibt deutsch;
  eine englische Version kommt erst zur Veroeffentlichung und dann als EINZIGE
  UI-Sprache (keine Mehrsprachigkeit, kein Umschalter). Damit die Uebersetzung reine
  Textarbeit an Templates bleibt und keine Datenformate beruehrt, gelten ab sofort:
  (1) Neue persistierte Werte (Zustaende, Arten, Herkunft, Modi, Fehler-/Abbruchcodes,
  Schluessel in job.json/index.json/Sidecars/Manifesten) sind ASCII und sprachneutral.
  Bestehende Werte (laeuft, beendet, gestoppt, verwaist, geplant, ...) bleiben
  unveraendert - ID-Stabilitaet geht vor.
  (2) Anzeigetext nie im Datenwert: Server liefern Codes, Zahlen, Zeitstempel und
  strukturierte Felder (Muster: inhalt in recording_index); die Formulierung entsteht
  im Client.
  (3) Neue Fehler- und Abbruchgruende, die in der UI erscheinen, als Code + Parameter,
  nicht als deutscher Satz. Log-Ausgaben und CLI-Werkzeuge bleiben deutsch.
  (4) Saetze mit Zahlen an neuen Stellen als ein Formatstring je Aussage, nicht als
  Verkettung von Teilstrings.
  Aufwandsrahmen fuer die spaetere Umstellung (Chat-Bewertung 2026-08-31): ca. 400-500
  UI-Strings in 7 Servern, ca. 150-250 UI-relevante Servermeldungen, Zustandswerte als
  Anzeige-Abbildung, Format-Wrapper fuer Zahl/Datum/Plural.
- 2026-09-03: Abhaengigkeitsregel neu gefasst (T-610; revidiert die Regel von 2026-08
  "eb200_core/ bleibt stdlib-only"). Anlass: Befund A1 - eb200_core/signal_detect.py
  importiert numpy auf Modulebene, waterfall.py importiert signal_detect (Auto-Marker
  T-505); :8000 startet damit ohnehin nicht ohne numpy. Die Regel schuetzte nichts mehr
  und erzeugte eine falsche Aussage in Handbuch-Kapitel 4. Neue Fassung in drei Stufen:
  (1) numpy ist Pflichtabhaengigkeit der gesamten Suite, auch in eb200_core/. Begruendung:
  keine transitiven Laufzeitabhaengigkeiten, BSD-Lizenz, in jeder Distribution paketiert
  (Windows als Wheel per pip, Pi OS per apt install python3-numpy) - und ohne numpy ist
  reine Python-Rechnung auf dem Pi 4 um Faktor 30-100 langsamer, was relevant wird,
  sobald pro Frame gerechnet und nicht nur durchgereicht wird (Formvergleich, gleitende
  Mediane, Autokorrelation).
  (2) Jede weitere Fremdbibliothek bleibt KUER: Import per try/except gekapselt, Merkmal
  faellt aus, Server startet trotzdem (Muster: matplotlib in session_analyze.py).
  (3) Harte Grenze unveraendert: keine Web-Frameworks, keine ORMs, keine JS-Build-Kette,
  keine Bibliothek mit transitiven Laufzeitabhaengigkeiten im Kernpfad.
  Kein Codeumbau: signal_detect.py bleibt in eb200_core/. Folgeaufgaben T-611
  (requirements.txt, erledigt), T-612 (Handbuch Kapitel 4), T-613 (Klartext-Hinweis bei
  fehlendem numpy).
- 2026-09-03: CLAUDE.md wird versioniert (T-614). Bisher galt sie als maschinenlokale
  Konfiguration wie station.json und wurde nie gestaged. Folgen: sie stand dauerhaft als
  modified im Arbeitsverzeichnis, was die Pre-Flight-Disziplin verwaessert, und ihre
  Regelaenderungen hatten keine Historie. CLAUDE.md ist aber Projektdokumentation und
  gehoert zu PLAN.md - Entscheidungslog-Eintraege verweisen auf Regeln in CLAUDE.md, und
  ohne Versionierung ist ein Commit nur der halbe Nachweis. Sie wird ab sofort wie PLAN.md
  im selben Commit wie die zugehoerige Task gefuehrt. AUSDRUECKLICH NICHT der Grund:
  CC-Sitzungen auf dem Raspberry Pi - die finden nicht statt, Claude Code laeuft nur auf
  dem PC, der Pi ist reines Ausrollziel (Korrektur einer Fehlannahme aus der
  Chat-Diskussion). Maschinenlokal und ungetrackt bleiben station.json und
  storage_roots.json. Ebenfalls in diesem Zug: Gate-Disziplin in CLAUDE.md praezisiert -
  Gates werden nie durch Anpassen des geschriebenen Textes erfuellt (Anlass: Gate-Muster
  in T-610 lag ueber einem Zeilenumbruch, CC hat den Text umgebrochen statt zu stoppen).
- 2026-09-03: X6 (Digital IF Output) geprueft und als Hardwareweg VERWORFEN. Befundlage aus
  dem Handbuch (Kapitel 2.1 Position 30, Annex I): X6 ist ein 25-poliger Stecker mit
  synchron-seriellem 3,3-V-TTL-Ausgang (SFRAME Pin 12, SCLOCK Pin 24, SDATA Pin 25;
  je Frame 16 Bit I und 16 Bit Q im Zweierkomplement, Bitakt fs x 32; hoechste Abtastrate
  256 kHz bei 150 kHz ZF-Bandbreite = 8,192 Mbit/s; Leitungslaenge ohne eigene Treiber
  max. 20 cm; Voraussetzung Demodulationsart IQ). Gegen eine Hardwareerweiterung sprechen
  drei Punkte: (1) der digitale Weg ist mit 150 kHz SCHMALER als der analoge X5-Ausgang,
  kostet also Bandbreite statt sie zu bringen; (2) er verlangt eigene Hardware (FPGA oder
  MCU, USB-Transport, Treiber), waehrend die Pinbelegung nur in der nicht vorliegenden
  Interface-Beschreibung 4052.2000.01 SB steht; (3) sein einziger Vorteil - 16 Bit und
  kalibrierter Pegel gegen 8 Bit unkalibriert beim HackRF - wird nicht gebraucht, weil
  kalibrierte Pegel ohnehin per SCPI vorliegen und die SDR-Zwischenstufe laut WS3
  ausdruecklich die relative, unkalibrierte Ebene ist. Zusaetzlich haengt bei X6 die
  Abtastrate an der ZF-Bandbreite: volle Rate erzwingt 150 kHz am Geraet und damit den
  Verlust der schmalen Selektion fuers Mithoeren; am X5 ist die ZF-Bandbreiteneinstellung
  ohne Einfluss auf das Fenster. Der Weg bleibt X5 + HackRF; die offenen Messungen sind
  T-912 (nutzbare flache Bandbreite, Pegel/Daempfung, AGC/MGC, Leerlaufspektrum) und
  T-913 (Referenzkopplung X4 -> CLKIN, Kalibrierkonstante). Im Handbuch wird X6 als
  begruendete Fussnote gefuehrt, nicht als Bauanleitung.
- 2026-09-05: T-715: Segmentgrenze auch bei Wechsel von BW, BW-Zoom oder
  Verstaerkung — Begruendung wie bei speed/mtime_s (T-6xx); Alt-DBs bleiben
  lesbar, fehlende Spalten werden als null ausgeliefert.
- 2026-09-05: T-716: Der UDP-Empfangspuffer wird ausschliesslich zwischen ABOR und
  INIT:IMM geleert (_configure_dscan). Ein Leeren im laufenden Empfang ist
  verboten und per Gate abgesichert - dort waeren die Datagramme gueltige
  Messdaten. Hintergrund: der Sweep-Zusammenbau hat keinen absoluten Anker,
  siehe T-718.
- 2026-09-08: Gate-Disziplin in CLAUDE.md praezisiert (Anlass T-1014a). Zwei
  Ergaenzungen: (1) der Zaehlbereich einer Vorkommenspruefung ist der gesamte
  Dateitext inklusive Kommentaren und Docstrings - eine im Docstring erklaerte
  Konstante zaehlt mit; (2) Umbenennungen und Entfernungen werden repo-weit
  gesucht, bevor der Prompt entsteht, und per Gate mit Soll 0 auf den alten Namen
  abgesichert. Ausserdem der handschriftlich begonnene Abschnitt Task-ID-Gates
  vervollstaendigt (Satz brach ab).
- 2026-09-08: T-1014 - Segmentgrenzen folgen dem TON, nicht den Steuerereignissen.
  Marker, Zyklusende und fremdes Item beenden ein Segment nur vorlaeufig; ueber ein
  Fortsetzungsfenster (max(2,0 s, hold_s + 1,0 s)) wird derselbe Kanal in dieselbe
  Datei weitergeschrieben. Grund: ein MScan-Item entsteht nur bei der Squelch-
  Oeffnung, nicht bei jeder Tonphase - die bisherige Annahme "kein Item, kein Ton"
  ist durch die Messreihe 2026-09-08 widerlegt. Verworfen wurde der naheliegende
  Workaround hold_s 0: er senkt den Verlustanteil, aber auch die empfangene
  Tonmenge (der Empfaenger verlaesst den Kanal sofort wieder).
- 2026-09-12: T-806, Messung zur "live"-Heuristik (session_is_live). Ein rein
  lesendes open_session_db() laesst eine VORHANDENE -wal-Datei unberuehrt
  (mtime vor und nach dem Oeffnen bitgleich gemessen); fehlt sie dagegen, legt
  SQLite sie beim Oeffnen der WAL-Datenbank neu an - mit aktueller mtime, und
  die Session gilt dann fuer LIVE_WAL_MAX_AGE_S faelschlich als laufend. In
  sessions/ liegt neben jeder der 21 abgeschlossenen Sessions eine -wal-Datei
  (meist 0 Byte), deshalb tritt der Fall im Betrieb nicht auf. Er trifft nur
  eine DB-Kopie ohne ihre Beidateien - und dann hoechstens 10 s lang, danach
  wird der Cache von selbst geschrieben. Konsequenz fuer Selbsttests: eine
  Session-DB wird immer samt -wal kopiert.
- 2026-09-13: T-1717, echte Umlaute statt HTML-Entities in analyze_audio.py.
  Anlass war die Bedienvalidierung von T-1704a: dort stand fuer denselben
  OFDM-Hinweis ein "Ae" ueber die Entity &auml; im ausgelieferten Markup,
  waehrend andere Stellen bereits ASCII-Ersatzschreibung (ae/oe/ue) oder,
  neu erforderlich, echte UTF-8-Umlaute brauchten - drei Konventionen
  nebeneinander in derselben Seite. Entschieden: Entities werden durchgehend
  durch echte Umlautzeichen ersetzt, nicht umgekehrt. Begruendung: (1) die
  Seite deklariert UTF-8 zweifach (meta charset UND Content-Type-Header),
  ein echtes Umlautzeichen ist also in jedem HTML-Kontext dieser Seite
  sicher; (2) Entities loesen nur in echtem HTML-Markup auf - JS-Zuweisungen
  an textContent oder .title (mehrere Stellen in analyze_audio.py) geben die
  Entity-Zeichenkette unveraendert an den Bediener weiter, sichtbar als
  rohes "&ouml;" statt "oe" mit Punkten. Echte Umlaute sind damit die einzige
  Schreibweise, die in ALLEN sichtbaren-Text-Kanaelen der Seite gleich
  funktioniert. Ausdruecklich nicht angefasst: Bezeichner, persistierte
  Enum-Werte, Quelltextkommentare - dort bleibt die bisherige ASCII-
  Transliteration Projektstandard (CLAUDE.md), die Entscheidung gilt nur fuer
  sichtbaren Text in der :8070-Ansicht.
- 2026-09-13: T-1715, asynchrone Rahmenerkennung auf dem Halbbit-Raster.
  Drei Entscheidungen mit Begruendung:
  (1) Beide Halbbits des Startbits werden geprueft (b[phi]==0 UND
  b[phi+1]==0), nicht nur eines. Das halbiert die Zufallstrefferrate der
  Start-Bedingung, ohne echte Rahmen zu gefaehrden - ein echtes Startbit
  besetzt beide Halbbits ohnehin mit 0. Vom Stoppelement genuegt dagegen das
  letzte Halbbit: bei 1,5 und 2 Stoppbits sind die davor liegenden ohnehin
  ebenfalls Mark, dort steckt keine zusaetzliche Information.
  (2) margin misst den Abstand des besten Scores zur besten ANDEREN
  Rahmenlaenge, nicht zur zweitbesten Phase derselben Laenge. Benachbarte
  Phasen derselben Laenge sind keine konkurrierende Erklaerung fuer das
  Signal - sie klaeren nur, WO innerhalb der immer gleichen Periode das
  Startbit liegt, nicht OB die Periode stimmt. Ein margin gegen die
  Nachbarphase waere strukturell klein (die Start/Stopp-Bedingung faellt bei
  benachbarten Phasen graduell ab, nicht abrupt) und haette nichts mit der
  Frage zu tun, die die Rahmensuche eigentlich beantworten soll: welche
  Rahmenlaenge, nicht welche Phase.
  (3) Die Suche laeuft NUR bei belastbarer Symbolrate (baud_refined oder
  baud_override_used). Mit der groben Schaetzung (bis 11 % Fehler bei
  kurzen Bursts, T-1711) wandert das angenommene Bitraster ueber die
  Aufnahme davon, und die Start/Stopp-Bedingung misst dann Zufall statt
  Rahmenstruktur - derselbe Mechanismus, der T-1710 an der echten Aufnahme
  zunaechst faelschlich als "unclear" auswies (siehe Nachtrag an T-1710),
  bevor T-1711 die Ursache (ungenaue Symbolrate) behob.
  Schwellenmessung (FRAMING_MIN_FRAMES/FRAMING_SCORE_MIN/FRAMING_MARGIN_MIN):
  bei n_frames=50 liegt die per Zufall erreichbare Obergrenze (200 Versuche)
  bei Score 0,364/Margin 0,089, waehrend echte, mit 5 % Bitfehlern
  verrauschte Signale (30 Seeds) bei genau diesem n_frames noch Score
  0,740/Margin 0,272 erreichen - klare Trennung bei dem kleinsten
  gemessenen Punkt, die Schwellen liegen je mittig in der Luecke.
- 2026-09-13: T-1716, Zeichenvorrat der gueltigen Rahmen (character_stats()).
  Nur Rahmen, die die Start/Stopp-Bedingung erfuellen, gehen in
  code_shares/top_codes/halfbit_mismatch_share ein; durchgefallene Rahmen
  liefern per Definition Muell (die Bitentscheidung an beliebiger Phase
  eines nicht erkannten Rahmens ist bedeutungslos) und wuerden die
  Haeufigkeiten verfaelschen, gingen sie mit ein - sie zaehlen ausschliesslich
  in fail_share/fail_run_max/fail_share_in_runs. Dieselbe Trennung wie beim
  Score von detect_async_framing selbst: ein durchgefallener Rahmen liefert
  keinen (falschen) Befund, sondern gar keinen.
- 2026-09-14: T-1716a, ita2_hint von FIGS/LTRS auf Space und den haeufigsten
  Code umgestellt. FIGS und LTRS sind als Schwellenkriterium untauglich, weil
  ihre Verteilungen bei Zufall und echtem Text UEBERLAPPEN statt eine Luecke
  zu zeigen (T-1716-Messung: Zufalls-Obergrenze bis 0,08, echte Fixture ab
  0,057) - FIGS tritt nur bei Ziffern auf und ist strukturell zu selten, um
  als Kriterium zu taugen. Der Anteil des haeufigsten Codes ersetzt beide;
  die Entropie der Verteilung wurde erwogen und verworfen, weil sie bei
  kleinem n_chars stark vom Stichprobenumfang abhaengt (bei 95 Zeichen auf
  32 Codes liegt schon Zufall deutlich unter dem theoretischen Hoechstwert),
  waehrend der Anteil des haeufigsten Codes dieselbe Aussage ohne diese
  Abhaengigkeit traegt.
  Allgemeiner Grundsatz fuer kuenftige Schwellen in diesem Projekt: eine
  Schwelle wird nur gesetzt, wenn die Messreihe eine echte Luecke zeigt.
  Zeigt sie stattdessen eine Ueberlappung, gibt es kein Kriterium an dieser
  Stelle - dann ist neu nachzudenken statt eine Schwelle mitten in die
  Ueberlappung zu legen.
- 2026-09-20: Rueckfallpunkt vor Meilenstein 18 (Umstellung der WebGUI auf
  Englisch), umgesetzt als T-1306. Der Stand der deutschen Oberflaeche ist mit
  dem annotierten Tag "de-final" eingefroren und zusaetzlich als Git-Bundle
  ausserhalb des Repos gesichert (E:\EB200\EB200-de-final-2026-09-20.bundle).
  Ein zweites Repository ist nicht noetig: die deutsche Fassung bleibt
  jederzeit auscheckbar, main traegt die Weiterentwicklung.
  Mit aufgenommen wurden der Handbuch-Zwischenstand und audio_test6/7/8.py.
  Abweichung vom urspruenglichen Entwurf: geplant war die Ausnahmeregel
  "known_freq/*.json ausser FNV.json", doch die Datei heisst FNV_AT_2026.json
  und 17 weitere Kataloge sind bereits versioniert. Auf Gerhards Entscheidung
  listet .gitignore stattdessen die einzelnen unversionierten Kataloge
  namentlich (marker_*.json, ALE_UDXF.json, KW_BC.json, KiwiSDR_DX.json,
  UDXF_logs.json), damit ein kuenftiger neuer Katalog weiter in git status
  auftaucht statt automatisch ignoriert zu werden. Zusaetzlich neu in
  .gitignore: AGENTS.md und .agents/, preparation_worker.json sowie
  eval_*.json - alle bleiben als Datei liegen, nur Git sieht sie nicht mehr
  und sind damit NICHT Teil des Rueckfallpunkts.
  Ebenfalls ausserhalb des Rueckfallpunkts, weil unversioniert: docs/messungen/
  (die Messreihen, auf die sich die Schwellenwert-Begruendungen in diesem Log
  stuetzen) und analyse_20260907_RPR/. Ueber beide ist noch nicht entschieden.
  Die Zweige gui-redesign-a und feature/scpi-verify-integration sind als
  archive/<name> getaggt und geloescht: es liegt keine offene Oberflaechen-
  arbeit mehr darauf, und nach der Uebersetzung waeren sie nicht mehr
  mergebar.
  Aufteilung fuer Meilenstein 18: die sprachneutrale Stufe 0 (Anzeige-Abbildung
  fuer Zustandswerte, Codes statt Saetze, Formatschicht, Verkettungen
  aufloesen, Pruefwerkzeug) wird auf main umgesetzt - sie verbessert den Code
  auch auf Deutsch. Erst die reine Textarbeit bekommt einen Zweig "en", und
  zwar erst wenn Stufe 0 abgenommen ist.
- 2026-09-20: Fuenf Entscheidungen zur Umstellung auf Englisch (Meilenstein 18,
  uebernommen mit T-1308).
  (1) Handbuch: das deutsche Handbuch wird mit dem Stand de-final eingefroren und
  nicht fertiggestellt. Ein englisches entsteht neu in Meilenstein 19, mit den
  deutschen Entwuerfen als Quellenmaterial. T-1412 entfaellt dadurch ersatzlos.
  (2) Katalogtexte: modes/fsk.json und die intl_*-Kataloge werden uebersetzt, die
  nationalen Tabellen bleiben deutsch, weil sie nationale Zuweisungen zitieren. IDs
  bleiben unveraendert.
  (3) Klassifikator: die reasons-Strings werden uebersetzt, CLASSIFIER_VERSION steigt
  auf eb200-audioclass-v4. Ohne Versionswechsel blieben alte Sidecars gueltig und die
  Anzeige waere gemischtsprachig.
  (4) API: der deutsche JSON-Schluessel geloeschte_dateien wird zu deleted_files.
  Jetzt folgenlos, nach der Veroeffentlichung ein Bruch.
  (5) Sprachvariante: US-Englisch, weil die Rohde-&-Schwarz-Dokumentation und das
  funktechnische Umfeld ueberwiegend amerikanisch gepraegt sind.
  Aufteilung der Arbeit: Stufe 0 (T-1800 bis T-1805) ist sprachneutral, verbessert den
  Code auch auf Deutsch und laeuft auf main. Erst die reine Textarbeit ab T-1810
  bekommt den Zweig "en". Stufe 0 vor Stufe 2 ist bindend.
- 2026-09-20 (T-1801a): tools/check_ui_lang.py um Ausschlussmarker erweitert -
  ein Block zwischen "/* ui-lang: ignore-begin ... */" und
  "/* ui-lang: ignore-end */" wird komplett ausgeblendet, nicht nur als
  Kommentar. Grund: LABELS_JS bildet EB200_LABELS mit den persistierten
  deutschen Codes als Objektschluessel (zuweisung, dienst, empfangskette, ein,
  aus, ...) - einige davon sind zufaellig auch normale Glossareintraege
  (Dienst, Zuweisung, Empfangskette in Abschnitt 1/2) oder Stoppwoerter (ein,
  aus) und loesten das Gate faelschlich aus, obwohl der Glossarparser die
  "Feld"-Tabelle in Abschnitt 4 bereits korrekt uebersprang. Die Codes selbst
  bleiben unveraendert (ID-Stabilitaet); nur die Pruefung blendet den Block
  aus. Neuer Selbsttest T23 in check_ui_lang.py sichert das Verhalten ab.
- 2026-09-22: Abgrenzung, wo Codes entstehen und wo uebersetzt wird (Anlass T-1802d).
  Kernmodule, die von mehreren Servern konsumiert werden (job_store, job_runner,
  recording_index, sched_health), liefern sprachneutrale Codes plus Parameter; der
  Anzeigetext entsteht im Client. Die Meldungen eines EINZELNEN Servers werden dagegen
  mit diesem Server uebersetzt, nicht vorher auf Codes umgebaut - ein Umbau brauchte
  dort einen Vertrag zwischen Teilen derselben Datei und erzeugte nur Zwischenzustaende.
  Ebenfalls festgehalten: Meldungen, die ausschliesslich in der Browserkonsole oder auf
  der Serverkonsole landen, sind Diagnose und bleiben deutsch - auch wenn sie ueber eine
  API uebertragen werden. Massgeblich ist, ob ein Text im DOM erscheint, nicht ob er den
  Server verlaesst.
- 2026-09-24: Wie weit Saetze vor der Uebersetzung entflochten werden (Anlass T-1804).
  Die Bestandsaufnahme fand 36 Pluralstellen und rund 61 Stellen, an denen ein sichtbarer
  Satz aus Fragmenten zusammengesetzt wird. Umgebaut wurden nur die Pluralstellen.
  Begruendung: Pluralbildung ist ein Grammatikproblem - "Datei(en)" hat im Englischen
  keine Entsprechung, und eine Ternaerkonstruktion wuerde an jeder Stelle einzeln
  nachgebaut. Verkettungen dagegen sind meist wertgetrennte Aufzaehlungen, deren
  Wortstellung durch die eingesetzten Werte ohnehin festliegt; dort ersetzt der
  Uebersetzer Fragment fuer Fragment, und ein Umbau haette nur Arbeit von Stufe 2 nach
  Stufe 0 verschoben und einen Diff ueber 61 Stellen erzeugt.
  Massgeblich ist damit nicht, ob ein Satz aus Fragmenten besteht, sondern ob die
  englische Fassung eine ANDERE Reihenfolge braucht oder die Struktur selbst im Weg
  steht (feste Elementbreiten). Nach diesem Massstab blieben zwei Stellen uebrig, beide
  in waterfall.py, beide in T-1812 und T-1813 vermerkt.
- 2026-09-24 (Anlass T-1810b): wie die flaechenlose Mondglyphe gefunden wurde. Drei
  Anlaeufe ueber Vermutungen (Kaskadenreihenfolge, fill-Vererbung, Deckung durch ein
  anderes Element) fuehrten ins Leere; aufgeloest hat erst eine einzige Messung in der
  Browserkonsole (Groesse, berechnetes fill, tatsaechliche Sichtbarkeit), die den
  Pfad selbst als Ursache freilegte - zwei deckungsgleiche Boegen ohne eingeschlossene
  Flaeche.
  Regel: bei einem Anzeigefehler, der sich nicht aus dem Quelltext erklaert, wird VOR
  dem ersten Behebungsversuch gemessen - und zwar vollstaendig (Groesse, Farbe,
  Sichtbarkeit, Geometrie zusammen), nicht in Runden mit je einer Vermutung dazwischen.
  T-1810a hatte genau das uebersprungen und eine plausible, aber falsche Ursache
  (Farbvererbung) behoben, ohne den Fehler zu beseitigen.
- 2026-09-24 (Anlass T-1810d): wie die richtige Mondform gefunden wurde. Drei Anlaeufe
  im Code (T-1810a, b, c) aenderten je eine Vermutung und liessen sie am Ergebnis
  pruefen - jeder Anlauf klaerte einen eigenen Befund, aber keiner zeigte die Form
  selbst, bevor sie im Knopf landete. Aufgeloest hat erst eine Gegenueberstellung:
  vier Varianten als Mockup nebeneinander, in Originalgroesse im Knopf und
  vergroessert, Auswahl durch OE3GAS.
  Regel: eine Glyphe oder ein Layoutdetail wird VOR dem Einbau als Mockup gezeigt,
  nicht im laufenden Code durchprobiert. Das ist dieselbe Grundregel wie beim
  Anzeigefehler oben (erst vollstaendig pruefen, dann aendern) - hier auf die Auswahl
  einer Form angewendet statt auf die Diagnose eines Fehlers.
- 2026-09-24: Geteilte Bloecke und die Reihenfolge der Pruefungen (Anlass T-1811).
  Der Fehler aus T-1804a konnte entstehen, weil eine Suche ueber alle Server eine
  Fundstelle liefert, ohne zu sagen, dass sie in einem geteilten Block liegt, und
  weil danach kein Gate den Abgleich aufrief. Beides wird geaendert:
  (a) Jeder Prompt, der Serverdateien anfasst, nennt in der Vorpruefung
      ausdruecklich, ob eine Fundstelle zwischen KNOWN_STRIP_BLOCK_BEGIN und
      KNOWN_STRIP_BLOCK_END liegt - die Fundliste allein genuegt nicht.
  (b) tools/sync_known_block.py --check gehoert als Gate in JEDEN Prompt, der
      waterfall.py, analyze_sessions.py, analyze_spectrum.py oder
      sdr_waterfall.py aendert, auch wenn die Aenderung den Block gar nicht
      betrifft. Ein Werkzeug, das nur aufgerufen wird, wenn man den Fehler
      ohnehin vermutet, findet ihn nie.
- 2026-09-29 (T-1834): Rueckkehr zum lokalen Betrieb. Cloud-Sitzungen (T-1830, T-1831)
  brachten keine Beschleunigung; Arbeit ab jetzt nur mit Claude Code auf dem Rechner des
  Auftraggebers. Die Push-Regel aus T-1831 ist ungueltig: Claude Code committet auf main
  und pusht NICHT, der Auftraggeber pusht selbst. Abbruch lokal: Bericht nach
  docs/berichte/<Task-ID>.md, nicht committen, melden. tools/sync_docs.ps1 entfaellt (die
  geplante Aufgabe ist abgeschaltet). Die Befunde aus dem Cloud-Betrieb (numpy-Importkette,
  CRLF unter Linux) bleiben als Wissen in CLAUDE.md stehen.
- 2026-09-26: Repository auf GitHub (privat, oe3gas/EB200repo) und Cloud-Sitzungen.
  Grund war zuerst die Sicherung - das Projekt lag nach 421 Commits ausschliesslich
  auf einer Platte. Die Cloud-Faehigkeit ist die Zugabe. Die Arbeitsteilung bleibt
  unveraendert: die Prompts entstehen ausserhalb, die Ausfuehrung prueft sie gegen
  die Datei und stoppt bei Abweichung. Dass beide Rollen getrennt bleiben, ist der
  Grund, warum Gate-Fehler auffallen; wo die Ausfuehrung laeuft, ist dafuer
  gleichgueltig.
- 2026-10-02 (T-1815i): Die Strukturierung von Catalog.errors (vormals T-1802d, dann
  T-1815 zugeschlagen) wandert nach T-1820. Sie betrifft vor allem known_freq.py; dort
  liegen auch die Uebersetzung der Loader-Meldungen und die beiden Kopplungen, an denen
  block_admin.py deren Wortlaut auswertet (K1, Rest von K3). In einem Zug geloest, muss
  block_admin.py nicht noch einmal angefasst werden. Der Abschlussnachweis fuer
  block_admin.py wird ein eigener Auftrag (T-1815j), wie T-1814m fuer manage_jobs.py.
- 2026-10-02 (T-1815j): Ausnahme fuer Werkzeugfehler in CLAUDE.md. Anlass T-1815i,
  Block 5: das Edit-Tool schnitt das Leerzeichen am Ende eines NEU-Texts ab, im Ausdruck
  stand "already exists in(". Claude Code hat den Text auf den Auftragswortlaut
  berichtigt und die Gates erneut laufen lassen - richtig im Ergebnis, nach der alten
  Regel aber ein Verstoss ("Gates werden nie dadurch erfuellt, dass der geschriebene Text
  angepasst wird"). Die neue Regel trennt beides: Massstab fuer eine Berichtigung ist allein
  der NEU-Text des Auftrags; jede Anpassung in Richtung eines Gates bleibt verboten. Fuer
  die Auftraege gilt umgekehrt: keine ALT- oder NEU-Texte mit Leerzeichen am Rand.
- 2026-10-03 (T-1816a): Fuer analyze_audio.py gilt dieselbe Entscheidung wie fuer
  block_admin.py - Servermeldungen direkt uebersetzen, keine Codes. Die Seite unterscheidet
  Faelle schon ueber code (building, recording, busy, unsupported), nie ueber den Text.
  Die Meldungen von tools/eye_diagram.py, die in der Messtafel erscheinen, bleiben bis
  T-1828 deutsch: sie entstehen in CLI-Werkzeugen, deren Ausgaben nach CLAUDE.md deutsch
  bleiben, und eine Umstellung reicht tief in die Messkette.
- 2026-10-03 (T-1817a): Fuer analyze_mscan.py gilt dieselbe Entscheidung wie fuer
  block_admin.py und analyze_audio.py - Servermeldungen direkt uebersetzen, keine Codes.
  Die sieben ValueError-Meldungen von eb200_core/mscan_view.py wandern dabei mit, obwohl
  sie in einem Kernmodul stehen: mscan_view.py hat nur diesen einen Verbraucher, kein Code
  vergleicht die Texte, und T-1820 fuehrt das Modul nicht. job_store.py dagegen hat viele
  Verbraucher; seine KeyError-Meldung bleibt bei T-1820.
- 2026-10-04 (T-1818a): analyze_sessions.py und analyze_spectrum.py werden gemeinsam
  uebersetzt, jeder Teilauftrag an beiden Dateien. Servermeldungen direkt uebersetzen,
  wie bei den uebrigen Servern. "Katalog nicht geladen" (/api/known) steht wortgleich in
  vier Servern und gehoert zur Fehlerliste des Katalog-Loaders; es wandert deshalb an
  T-1820, statt in zwei von vier Dateien uebersetzt zu werden.
- 2026-10-05 (T-1820a): Catalog.errors wird nicht auf (file, code, detail) umgebaut,
  anders als am 2026-10-02 geplant. Die Meldungen von known_freq.py werden direkt
  uebersetzt, wie die Servermeldungen seit T-1815. Grund: die englische Oberflaeche wird
  die einzige; der Umbau haette known_freq.py, block_admin.py, /api/known in vier Servern
  und viele Selbsttests beruehrt, ohne der Oberflaeche etwas zu bringen. Die beiden
  Textvergleiche in block_admin.py (Raster-Vorschau, CSV-Konflikte) folgen dem
  englischen Wortlaut und bleiben durch die Selbsttests abgesichert.
- 2026-10-06 (T-1828): Die Meldungen von tools/eye_diagram.py werden direkt uebersetzt,
  nicht als Code mit Parametern an :8070 gegeben (anders als am 2026-10-03 geplant).
  Grund wie bei T-1820a: die englische Oberflaeche wird die einzige; Codes haetten eine
  neue Schnittstelle zwischen eye_diagram.py, analyze_audio.py und dem Skript von :8070
  gebraucht, ohne der Oberflaeche etwas zu bringen.
- 2026-10-07 (T-1826a, T-1827): check_ui_lang.py zaehlt nur noch Anzeigetext (Weg B statt
  Ausnahmeliste oder Umbenennung aller deutschen Bezeichner). Die Regeln zur
  Lokalisierung in CLAUDE.md sind ab jetzt Dauerregeln: UI-Text Englisch, Code,
  Kommentar und Log Deutsch; check_ui_lang.py mit Exitcode 0 ist Pflicht-Gate.
- 2026-10-08 (T-2000): Weg zur Windows-Verteilung (Meilenstein 20). Erst Stufe A, die
  Trennung von Programm- und Datenwurzel, dann die Verpackung (portabler Ordner mit
  Embeddable Python oder Nuitka). Die Datenwurzel wird ausschliesslich per
  Kommandozeile gesetzt (--data-dir), keine Umgebungsvariable; ohne Argument gilt die
  Repo-Wurzel, das bisherige Verhalten bleibt. eb200_core/paths.py liest --data-dir
  beim ersten Import selbst aus sys.argv und haelt DATA_ROOT als Prozesskonstante,
  statt den Wert spaet in main() zu binden: Modulkonstanten, Importkopien und viele
  Parameterdefaults schreiben ihn zur Importzeit fest. Module in eb200_core
  importieren einander nur mit Paketpraefix (eb200_core.xxx).
- 2026-10-08 (T-2004): Eine neue, leere Datenwurzel bekommt beim ersten Start die
  mitgelieferten Kataloge (PROGRAM_ROOT/known_freq/*.json) als Vorlage kopiert; ein
  vorhandenes known_freq/ wird nie angefasst, auch kein leeres. Zusaetzlich legt
  :8010 das Verzeichnis beim Schreiben an. Verworfen: zwei Katalogebenen (Programm
  lesend plus Datenwurzel) - groesserer Umbau, und Aenderungen an mitgelieferten
  Katalogen waeren nicht mehr eindeutig einer Datei zuzuordnen.
