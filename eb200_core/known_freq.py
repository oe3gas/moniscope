#!/usr/bin/env python3
"""
eb200_core/known_freq.py

Laedt den Katalog bekannter Frequenzen aus known_freq/*.json und liefert eine
flache, sortierte Kanal- und Bandliste. Nur Python-Standardbibliothek, rein
lesend - dieses Modul schreibt NIE in die Katalogdateien.

Design-Entscheidungen
---------------------
1. Katalog != Speicherabbild. Der Katalog enthaelt beliebig viele Kanaele
   (allein das Airband hat 2280), die 1000 Speicherplaetze MEM0-999 werden
   spaeter aus einem *Scan-Set* belegt. Scan-Sets verweisen per sprechender
   `id` auf Katalogeintraege - deshalb ist `id` global eindeutig und stabil.
   Vergaberegel fuer FREQUENZABGELEITETE ids (Muster `<praefix>-<kHz>`, ohne
   weiteres unterscheidendes Segment): ihr Praefix gehoert genau einer
   Katalogdatei. Zwei Listen ueber denselben Frequenzbereich erzeugen sonst
   zwangslaeufig dieselben ids, und die zweitgelesene Datei verliert ihre
   Eintraege - stumm fuer den Benutzer, laut in Catalog.errors (Befund
   09/2026: ALE_UDXF.json und UDXF_logs.json vergaben beide `udxf-<kHz>`,
   rund 300 verworfene Kanaele bei jedem Laden; behoben durch das eigene
   Praefix `udxflog-`). Ids, die neben dem Praefix noch Land, Band oder
   Standort fuehren (`fm-cz-impuls-kojal`, `ham-2m-ft8`,
   `ors-ukw-87600-abtenau-buchberg`), duerfen sich ein Praefix dagegen
   dateiuebergreifend teilen - dort unterscheidet das zweite Segment. Welche
   Praefixe im Bestand vergeben sind, zeigt `--praefixe` (siehe CLI).
2. Mehrere Dateien, kein gepflegter Index. Eine JSON-Datei je Dienst/Band.
   Welche Datei welche Frequenzen abdeckt (`coverage`), wird beim Laden aus
   den Eintraegen ABGELEITET und nie von Hand gepflegt.
3. Band != Summe der Kanaele. Jede Datei hat einen `bands`-Block
   (Bereichsbalken mit Titel, optional Segmente) unabhaengig von den
   Einzelkanaelen. Die Darstellung waehlt je nach Pixeldichte Balken,
   Segmente, Ticks oder beschriftete Kanaele.
4. Laden ist nie fatal. Defekte Datei, doppelte ID, ungueltige Bandbreite ->
   Eintrag bzw. Datei wird uebersprungen und als Fehlertext in
   `Catalog.errors` gesammelt. Ein Server muss auch mit leerem oder kaputtem
   Katalog starten.
5. Antennen symbolisch (`kw_dipol`, `kw_beam`, `yagi_6m`, `discone` - wie
   ANTENNA_OPTIONS in eb200_core/block_admin.py), nicht als EB200-ANT-Nummer.
   Die Zuordnung zu einem Geraeteeingang faellt erst beim Upload.

Eintragsarten in `entries`
--------------------------
`kind: "channel"` - ein einzelner Kanal (id, label, freq_hz + optionale
Parameter, fehlende kommen aus `defaults` der Datei).

`kind: "raster"` - erzeugt viele Kanaele: entweder `step_scheme:
"airband_8k33"` oder ein festes `step_hz` mit `label_template`. Die uebrigen
Felder werden an jeden erzeugten Kanal vererbt. Erzeugte IDs lauten
`<id_prefix>-<label>`.

Ein Eintrag mit `enabled: false` erzeugt gar keine Kanaele (bei Rastern der
uebliche Fall: ein vollstaendiges Raster liegt zwar in der Datei, ist aber
normalerweise abgeschaltet). Der Schalter ist damit reine Katalogpflege und
taucht in der Ausgabe nicht mehr auf.

Datei-Meta (`meta`)
--------------------
`service` - Anzeigename des Dienstes (Freitext).
`layer` - "zuweisung" (Regelwerksicht wie die FNV: was DARF hier senden) oder
"dienst" (tatsaechlich beobachtete/bekannte Nutzung, Standard-Ebene). Fehlt
der Schluessel, gilt "dienst". Ein ungueltiger Wert wird verworfen (Eintrag
in `Catalog.errors`), die Datei laedt dann mit "dienst". Jedes Band der Datei
traegt `layer` im Client-JSON; die Darstellung zeichnet Zuweisungs- und
Dienstbaender in getrennten Reihen (siehe waterfall.py, KNOWN_STRIP_BLOCK).

Ansichtsauswahl (T-126)
-----------------------
`catalog_view.json` im Repo-Root (nicht versioniert, siehe
catalog_view.example.json und .gitignore) nennt unter `hidden_files` die
Katalogdateien, die in den Anzeige-Servern NICHT gezeigt werden sollen.
Gepflegt wird die Datei ausschliesslich in eb200_core/block_admin.py (:8010);
hier liegt nur der Leser load_view_config(). Bewusst eine AUSSCHLUSSliste: eine
neu angelegte oder importierte Katalogdatei ist damit sofort sichtbar, und eine
fehlende Datei bedeutet "alles sichtbar" - also genau das Verhalten vor T-126.

Der Ausschluss wirkt NUR in to_client_json(), also auf die Darstellung.
by_id(), channels_in_range() und bands_in_range() sehen weiterhin den ganzen
Katalog - Scan-Set-Upload, MSCAN-Jobs und der Editor bleiben unberuehrt, genau
wie beim Regionsfilter aus station.json.

Konsolenausgaben sind bewusst ASCII (cp1252-Konsole): "dBuV" statt dem
Mikro-Zeichen, kein Delta-Symbol.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import sys

# Erlaubt auch den Direktaufruf "python eb200_core/known_freq.py": dort fehlt
# sonst das Repo-Root im Suchpfad fuer den paths-Import (T-2002, Weiche wie
# in eb200_core/path_sandbox.py).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eb200_core import paths

# Filterstufen des EB200 - identisch zu BW_STAGES_HZ in eb200_core/scpi_verify.py.
# Eine Bandbreite ausserhalb dieser Liste kann das Geraet nicht einstellen, der
# Eintrag waere also unbrauchbar und wird verworfen.
BW_STAGES_HZ = [150, 300, 600, 1500, 2400, 6000, 9000, 15000, 30000, 50000, 120000, 150000]

# Demodulationsarten, die der EB200 kennt (SENSe:DEModulation). Exakt diese
# Tokens - "NFM" o. ae. sind KEINE gueltigen Werte und werden verworfen.
DEMOD_TOKENS = ("AM", "FM", "USB", "LSB", "CW", "IQ", "PULSE")

# Empfangsbereich des EB200 (Annex C): 10 kHz .. 3 GHz.
FREQ_MIN_HZ = 10_000
FREQ_MAX_HZ = 3_000_000_000

# Standard-Katalogverzeichnis: <DATA_ROOT>/known_freq (T-2002; DATA_ROOT aus
# eb200_core/paths.py, ohne --data-dir das Repo-Root wie bisher).
DEFAULT_CATALOG_DIR = paths.data_path("known_freq")

# Erlaubte Zeichen in einer Katalog-ID. Bewusst eng: die ID wandert spaeter in
# Scan-Sets, URLs und Dateinamen.
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Felder, die ein Kanal von den Datei-Defaults bzw. vom Raster-Eintrag erbt.
_INHERITED_FIELDS = ("demod", "bw_hz", "squelch_dbuv", "antenna", "att",
                     "att_auto", "afc", "tags", "notes")

# Kompakte Schluessel fuer to_client_json() - alles andere bleibt serverseitig.
_CLIENT_CHANNEL_KEYS = ("id", "label", "freq_hz", "demod", "bw_hz",
                        "service", "file", "tags", "raster")

# Optionale Anzeige-Zusatzfelder (Sender-Standort/Programm/Bundesland/
# Polarisation/Sendeleistung) - nur ORS_UKW_AT.json und ORS_DVBT2_AT.json
# tragen sie. Gehen im Client-JSON NUR mit, wenn der Kanal sie hat (kein
# None-Fuellen) - der Payload der uebrigen ~2700 Kanaele bleibt unveraendert.
_CLIENT_OPTIONAL_KEYS = ("site", "programm", "bundesland", "pol", "erp_w")

# notes gehoert bewusst NICHT in dieses Tupel (T-127). Das Feld ist ein
# geerbtes Feld und wird in _expand_entry fuer JEDEN Kanal angelegt - ohne
# Datei-Default mit dem Wert None. Die Pruefung "if k in ch" wuerde deshalb
# saemtliche Kanaele mit notes=null beliefern statt nur die mit Notiz.
# to_client_json nimmt notes ueber einen eigenen Zweig mit, der auf
# nichtleeren Text prueft (Messung 2026-09-15: 8203 von 8322 Kanaelen tragen
# eine Notiz, 119 nicht).

# Farbpalette der Katalogdateien (T-132, erweitert in T-132c). Der Dateikopf
# nennt unter meta.color einen dieser Namen oder einen eigenen Hexwert; die
# Darstellung faerbt damit alle Baender der Datei. 13 Familien zu je drei
# Helligkeitsstufen - dunkel, mittel, hell -, die Reihenfolge ist zugleich die
# der Schnellwahl in :8010. Die hellen Stufen sind erst seit T-132c brauchbar:
# vorher stand die Schriftfarbe der Bandtitel fest auf Weiss, was auf
# sonnengelb oder mint unlesbar gewesen waere (siehe knownTextColor in
# waterfall.py). Jede Farbe traegt mit der automatisch gewaehlten Schriftfarbe
# in BEIDEN Ebenen mindestens Kontrast 4,7 - nachgerechnet 2026-09-16, der
# knappste Fall ist sonnengelb in der FNV-Reihe.
CATALOG_COLORS = {
    "dunkelblau":   "#123a8c",
    "blau":         "#1f5fd0",
    "himmelblau":   "#5aa6f0",
    "indigo":       "#3b2a9c",
    "violett":      "#6a1fb0",
    "lavendel":     "#b0a8e8",
    "magenta":      "#a3128a",
    "pink":         "#e03fa8",
    "flieder":      "#d8a6e0",
    "bordeaux":     "#6b0d2a",
    "himbeere":     "#9c1247",
    "altrosa":      "#e08a9a",
    "dunkelrot":    "#8c0f0f",
    "rot":          "#c01818",
    "hellrot":      "#f0604a",
    "dunkelorange": "#8f3f00",
    "orange":       "#d4700f",
    "hellorange":   "#f4a24a",
    "kupfer":       "#7d4a10",
    "bernstein":    "#c9960a",
    "sand":         "#d9b98a",
    "braun":        "#6b4423",
    "gold":         "#7a5c05",
    "sonnengelb":   "#f2d13c",
    "moos":         "#3d5c14",
    "oliv":         "#5f6a12",
    "limette":      "#c3d63a",
    "dunkelgruen":  "#15612f",
    "gruen":        "#1f7a1f",
    "hellgruen":    "#7ac943",
    "smaragd":      "#0d7a5a",
    "petrol":       "#0e6b6b",
    "mint":         "#6fd6b0",
    "tuerkis":      "#0b6a80",
    "azur":         "#0a6fa8",
    "eisblau":      "#79cbe8",
    "schiefer":     "#2f4a6b",
    "anthrazit":    "#3a3f45",
    "grau":         "#5a5a5a",
}

# Anzeigenamen der Farben (T-1823b). Die Schluessel oben sind Datenwerte - sie
# stehen als meta.color in den Katalogdateien und bleiben deutsch. Die
# Schnellwahl in :8010 zeigt im Tooltip diesen Namen; fehlt ein Eintrag, faellt
# sie auf den Schluessel zurueck. Gleiche Schluessel wie CATALOG_COLORS
# (Selbsttest _self_test_color).
CATALOG_COLOR_LABELS = {
    "dunkelblau":   "dark blue",
    "blau":         "blue",
    "himmelblau":   "sky blue",
    "indigo":       "indigo",
    "violett":      "violet",
    "lavendel":     "lavender",
    "magenta":      "magenta",
    "pink":         "pink",
    "flieder":      "lilac",
    "bordeaux":     "burgundy",
    "himbeere":     "raspberry",
    "altrosa":      "old rose",
    "dunkelrot":    "dark red",
    "rot":          "red",
    "hellrot":      "light red",
    "dunkelorange": "dark orange",
    "orange":       "orange",
    "hellorange":   "light orange",
    "kupfer":       "copper",
    "bernstein":    "amber",
    "sand":         "sand",
    "braun":        "brown",
    "gold":         "gold",
    "sonnengelb":   "sun yellow",
    "moos":         "moss",
    "oliv":         "olive",
    "limette":      "lime",
    "dunkelgruen":  "dark green",
    "gruen":        "green",
    "hellgruen":    "light green",
    "smaragd":      "emerald",
    "petrol":       "teal",
    "mint":         "mint",
    "tuerkis":      "turquoise",
    "azur":         "azure",
    "eisblau":      "ice blue",
    "schiefer":     "slate",
    "anthrazit":    "charcoal",
    "grau":         "gray",
}

# Die elf Namen der ersten Fassung bleiben gueltig - sie stehen seit T-132a in
# den Katalogdateien. Sieben davon fuehrt die neue Palette weiter (blau,
# violett, gruen, petrol, oliv, rot, grau, jeweils kraeftiger), die uebrigen
# vier zeigen hierher. "gelb" ist dabei bewusst nicht die naechstverwandte,
# sondern die fuer ihren Zweck richtige Farbe: sie steht in FNV_AT_2026.json,
# und die FNV-Reihe zeichnet mit 55 % Deckkraft - aus sonnengelb wird dort
# genau das satte Ockergelb, das die Zuweisungsebene braucht.
_COLOR_ALIASES = {
    "ocker":    "gold",
    "rotbraun": "braun",
    "gelb":     "sonnengelb",
    "graublau": "schiefer",
}

# Ein eigener Farbwert im Dateikopf (T-132b): #rrggbb. Die Namen aus
# CATALOG_COLORS bleiben gueltig - sie sind die lesbarere Schreibweise und
# stehen seit T-132a in allen 25 Dateien; das Formular in :8010 schreibt
# dagegen Hexwerte, weil dort frei gewaehlt wird.
_COLOR_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def resolve_color(value) -> str | None:
    """Farbangabe aus meta.color zu einem Hexwert aufloesen - Name aus
    CATALOG_COLORS oder eigenes #rrggbb. Unbekannt oder leer -> None (die
    Darstellung faellt dann auf den Hash ueber den Dienstnamen zurueck)."""
    if not isinstance(value, str) or not value:
        return None
    if value in CATALOG_COLORS:
        return CATALOG_COLORS[value]
    if value in _COLOR_ALIASES:
        return CATALOG_COLORS[_COLOR_ALIASES[value]]
    if _COLOR_HEX_RE.match(value):
        return value.lower()
    return None


# ---------------------------------------------------------------------------
# Raster-Schemata
# ---------------------------------------------------------------------------

def _airband_8k33(start_hz: int, stop_hz: int):
    """
    ICAO-8,33-kHz-Raster (Annex 10 Vol. V). Je 25-kHz-Basis entstehen drei
    Kanaele mit den Frequenzabstaenden 0 / 8333 / 16667 Hz.

    WICHTIG: Kanalname != Frequenz. Der ICAO-Bezeichner zaehlt in 5-kHz-
    Schritten ab der Basis+5 kHz, also 118.005 / 118.010 / 118.015 fuer die
    Basis 118.000 - danach springt er auf 118.030. Die Endziffern
    .020 / .045 / .070 / .095 kommen im 8,33-Raster deshalb NIE vor; sie
    waeren ein sicheres Zeichen fuer eine falsche Umrechnung.
    """
    out = []
    n = 0
    while True:
        f25 = start_hz + n * 25_000
        if f25 > stop_hz:
            break
        f25_khz = f25 // 1000
        for k in (0, 1, 2):
            freq_hz = f25 + round(k * 25_000 / 3)          # 0, 8333, 16667
            label = "%.3f" % ((f25_khz + 5 * (k + 1)) / 1000.0)
            out.append((freq_hz, label, n * 3 + k))
        n += 1
    return out


def _fixed_step(start_hz: int, stop_hz: int, step_hz: int, template: str):
    """Gleichmaessiges Raster mit fester Schrittweite. Im label_template stehen
    {mhz}, {khz}, {hz} und {n} (laufende Nummer ab 0) zur Verfuegung."""
    out = []
    n = 0
    while True:
        freq_hz = start_hz + n * step_hz
        if freq_hz > stop_hz:
            break
        label = template.format(mhz=freq_hz / 1e6, khz=freq_hz / 1e3,
                                hz=freq_hz, n=n)
        out.append((freq_hz, label, n))
        n += 1
    return out


# ---------------------------------------------------------------------------
# Hilfen: Bereiche zusammenfassen, Werte pruefen
# ---------------------------------------------------------------------------

def _merge_ranges(ranges):
    """Sortiert Bereiche und legt ueberlappende/beruehrende zusammen.
    Ergebnis ist der abgeleitete 'Index' einer Datei (coverage)."""
    if not ranges:
        return []
    ranges = sorted(ranges)
    out = [list(ranges[0])]
    for lo, hi in ranges[1:]:
        if lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def _as_int(value):
    """int aus JSON-Wert - akzeptiert 1.0, lehnt 1.5 und "1" ab."""
    if isinstance(value, bool):
        raise ValueError("bool is not a number")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError(f"integer expected, found {value!r}")


def _check_channel(ch: dict) -> None:
    """Prueft einen fertig zusammengesetzten Kanal. Wirft ValueError mit einem
    Text, der ohne weiteren Kontext verstaendlich ist."""
    cid = ch.get("id", "")
    if not isinstance(cid, str) or not cid or not _ID_RE.match(cid):
        raise ValueError(f"invalid id {cid!r} (allowed: A-Z a-z 0-9 . _ -)")
    if not isinstance(ch.get("label"), str) or not ch["label"]:
        raise ValueError(f"{cid}: label is missing or empty")

    freq = _as_int(ch.get("freq_hz"))
    if not (FREQ_MIN_HZ <= freq <= FREQ_MAX_HZ):
        raise ValueError(f"{cid}: freq_hz {freq} must be between "
                         f"{FREQ_MIN_HZ} and {FREQ_MAX_HZ} Hz")
    ch["freq_hz"] = freq

    demod = ch.get("demod")
    if demod not in DEMOD_TOKENS:
        raise ValueError(f"{cid}: unknown demod {demod!r} "
                         f"(allowed: {', '.join(DEMOD_TOKENS)})")

    bw = _as_int(ch.get("bw_hz"))
    if bw not in BW_STAGES_HZ:
        raise ValueError(f"{cid}: bw_hz {bw} is not an EB200 filter step "
                         f"({', '.join(str(b) for b in BW_STAGES_HZ)})")
    ch["bw_hz"] = bw


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------

class Catalog:
    """Ergebnis von load_catalog(). Reine Datenhaltung - keine Datei-, Netz-
    oder Geraetezugriffe nach dem Laden."""

    def __init__(self) -> None:
        self.files: list[dict] = []
        self.bands: list[dict] = []
        self.channels: list[dict] = []
        self.errors: list[str] = []
        self._freqs: list[int] = []      # Suchschluessel parallel zu channels
        self._by_id: dict[str, dict] = {}

    # -- Abfragen ----------------------------------------------------------
    def channels_in_range(self, start_hz: int, stop_hz: int) -> list[dict]:
        """Alle Kanaele mit start_hz <= freq_hz <= stop_hz. bisect auf der
        nach freq_hz sortierten Liste - bei 2280 Airband-Kanaelen lohnt das
        gegenueber einem Filter ueber die Gesamtliste."""
        lo = bisect.bisect_left(self._freqs, start_hz)
        hi = bisect.bisect_right(self._freqs, stop_hz)
        return self.channels[lo:hi]

    def bands_in_range(self, start_hz: int, stop_hz: int) -> list[dict]:
        """Alle Baender, die den Bereich beruehren - Ueberlappung genuegt,
        das Band muss nicht vollstaendig enthalten sein."""
        return [b for b in self.bands
                if b["start_hz"] <= stop_hz and b["stop_hz"] >= start_hz]

    def by_id(self, cid: str) -> dict | None:
        return self._by_id.get(cid)

    def to_client_json(self, station: dict | None = None,
                       view: dict | None = None) -> dict:
        """Kompakte Fassung fuer /api/known_freq. Kanaele tragen nur die
        Felder, die eine Darstellung braucht - Squelch, Antenne, ATT usw.
        bleiben serverseitig, sie werden erst beim Upload gebraucht.
        `station` ist das Ergebnis von load_station_config(): der Client
        filtert damit rein optisch nach 'land-*'-Tags (Region ist reine
        Anzeigefilterung, siehe waterfall.py knownVisibleChannels) - Katalog,
        Scan-Set-Upload und Editor bleiben davon unberuehrt.
        `view` ist das Ergebnis von load_view_config() (T-126): die dort
        genannten Dateien fallen aus bands und channels heraus. Auch das ist
        reine Anzeigefilterung. `files` bleibt VOLLSTAENDIG und traegt je
        Datei den Schluessel in_view - :8010 braucht die ganze Liste; `view`
        nennt die Zaehlstaende fuer einen Hinweis in der Bedienleiste.
        view=None (Vorgabe) filtert nichts: der Kanalwaehler in
        manage_jobs.py bleibt so unveraendert vollstaendig.
        `notes` geht seit T-127 mit, aber NUR mit nichtleerem Text (Anzeige in
        der Fusszeile des Streifen-Tooltips, siehe waterfall.py
        knownRenderTip); ohne Notiz fehlt der Schluessel ganz, genau wie bei
        den ORS-Zusatzfeldern."""
        region_tags = list((station or {}).get("region_tags") or [])
        hidden = set((view or {}).get("hidden_files") or [])
        sichtbare = [ch for ch in self.channels if ch["file"] not in hidden]
        return {
            "version": 1,
            "bands": [b for b in self.bands if b["file"] not in hidden],
            "channels": [{**{k: ch[k] for k in _CLIENT_CHANNEL_KEYS},
                          **{k: ch[k] for k in _CLIENT_OPTIONAL_KEYS if k in ch},
                          **({"notes": ch["notes"]}
                             if isinstance(ch.get("notes"), str) and ch["notes"].strip()
                             else {})}
                         for ch in sichtbare],
            "files": [{**f, "in_view": f["name"] not in hidden}
                      for f in self.files],
            "errors": self.errors,
            "region": {"tags": region_tags},
            "view": {"total": len(self.files),
                     "hidden": sum(1 for f in self.files
                                   if f["name"] in hidden)},
        }

    # -- Aufbau (nur von load_catalog benutzt) -----------------------------
    def _add_channel(self, ch: dict, fname: str) -> bool:
        vorher = self._by_id.get(ch["id"])
        if vorher is not None:
            self.errors.append(
                f"{fname}: id '{ch['id']}' already used in {vorher['file']} "
                f"- entry from {fname} discarded")
            return False
        self._by_id[ch["id"]] = ch
        self.channels.append(ch)
        return True

    def _finalize(self) -> None:
        self.channels.sort(key=lambda c: (c["freq_hz"], c["id"]))
        self._freqs = [c["freq_hz"] for c in self.channels]
        self.bands.sort(key=lambda b: (b["start_hz"], b["id"]))


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------

def _load_bands(raw_bands, fname: str, service: str, layer: str, cat: Catalog,
                color: str | None = None) -> list[dict]:
    """Baender einer Datei pruefen und uebernehmen. Ein ungueltiges Segment
    verwirft nur das Segment, ein ungueltiges Band nur das Band."""
    out = []
    for raw in raw_bands if isinstance(raw_bands, list) else []:
        if not isinstance(raw, dict):
            cat.errors.append(f"{fname}: band is not an object - discarded")
            continue
        bid = raw.get("id")
        try:
            if not isinstance(bid, str) or not bid or not _ID_RE.match(bid):
                raise ValueError(f"invalid band id {bid!r}")
            start = _as_int(raw.get("start_hz"))
            stop = _as_int(raw.get("stop_hz"))
            if stop <= start:
                raise ValueError(f"{bid}: stop_hz {stop} <= start_hz {start}")
        except ValueError as exc:
            cat.errors.append(f"{fname}: band discarded - {exc}")
            continue

        segmente = []
        for seg in raw.get("segments") or []:
            try:
                if not isinstance(seg, dict):
                    raise ValueError("segment is not an object")
                s_start = _as_int(seg.get("start_hz"))
                s_stop = _as_int(seg.get("stop_hz"))
                if s_stop <= s_start:
                    raise ValueError(f"stop_hz {s_stop} <= start_hz {s_start}")
                if s_start < start or s_stop > stop:
                    raise ValueError(f"{s_start}..{s_stop} is not inside the band "
                                     f"{start}..{stop}")
            except ValueError as exc:
                cat.errors.append(f"{fname}: segment in band '{bid}' discarded - {exc}")
                continue
            segmente.append({"title": str(seg.get("title", "")),
                             "start_hz": s_start, "stop_hz": s_stop})

        out.append({"id": bid, "title": str(raw.get("title", bid)),
                    "start_hz": start, "stop_hz": stop,
                    "segments": segmente, "file": fname, "service": service,
                    "layer": layer, "color": color})
    return out


def _expand_entry(raw: dict, defaults: dict, fname: str, service: str,
                  cat: Catalog) -> list[dict]:
    """Einen entries-Eintrag zu einer Liste fertiger Kanal-Dicts machen.
    Fehler landen in cat.errors, der Eintrag wird dann uebersprungen."""
    kind = raw.get("kind")
    if raw.get("enabled", True) is False:
        return []      # abgeschaltet: erzeugt bewusst gar nichts

    # Datei-Defaults, dann die Felder des Eintrags selbst.
    basis = {f: defaults.get(f) for f in _INHERITED_FIELDS}
    for f in _INHERITED_FIELDS:
        if f in raw:
            basis[f] = raw[f]
    basis["tags"] = list(basis.get("tags") or [])

    if kind == "channel":
        ch = dict(basis)
        ch.update({"id": raw.get("id"), "label": raw.get("label"),
                   "freq_hz": raw.get("freq_hz"),
                   "file": fname, "service": service, "raster": False})
        for f in _CLIENT_OPTIONAL_KEYS:
            if f in raw:
                ch[f] = raw[f]
        try:
            _check_channel(ch)
        except ValueError as exc:
            cat.errors.append(f"{fname}: channel discarded - {exc}")
            return []
        return [ch]

    if kind == "raster":
        prefix = raw.get("id_prefix")
        try:
            if not isinstance(prefix, str) or not prefix or not _ID_RE.match(prefix):
                raise ValueError(f"invalid id_prefix {prefix!r}")
            start = _as_int(raw.get("start_hz"))
            stop = _as_int(raw.get("stop_hz"))
            if stop < start:
                raise ValueError(f"{prefix}: stop_hz {stop} < start_hz {start}")
            schema = raw.get("step_scheme")
            if schema == "airband_8k33":
                punkte = _airband_8k33(start, stop)
            elif schema is None:
                step = _as_int(raw.get("step_hz"))
                if step <= 0:
                    raise ValueError(f"{prefix}: step_hz {step} must be greater than 0")
                punkte = _fixed_step(start, stop, step,
                                     str(raw.get("label_template", "{mhz:.4f}")))
            else:
                raise ValueError(f"{prefix}: unknown step_scheme {schema!r}")
        except (ValueError, KeyError, IndexError) as exc:
            cat.errors.append(f"{fname}: channel group discarded - {exc}")
            return []

        out = []
        for freq_hz, label, _n in punkte:
            ch = dict(basis)
            ch.update({"id": f"{prefix}-{label}", "label": label,
                       "freq_hz": freq_hz, "file": fname, "service": service,
                       "raster": True})
            try:
                _check_channel(ch)
            except ValueError as exc:
                cat.errors.append(f"{fname}: channel from channel group discarded - {exc}")
                continue
            out.append(ch)
        return out

    cat.errors.append(f"{fname}: unknown kind {kind!r} - entry discarded")
    return []


def load_catalog(catalog_dir: str | None = None) -> Catalog:
    """
    Laedt alle *.json aus dem Katalogverzeichnis (alphabetisch, damit die
    Reihenfolge und damit auch die Doppel-ID-Meldungen reproduzierbar sind).

    Wirft nie: ein fehlendes Verzeichnis ergibt einen leeren Katalog, eine
    kaputte Datei einen Eintrag in files[] mit 'error' plus einen Text in
    errors[].
    """
    cat = Catalog()
    verzeichnis = catalog_dir or DEFAULT_CATALOG_DIR
    try:
        namen = sorted(n for n in os.listdir(verzeichnis) if n.endswith(".json"))
    except OSError:
        return cat        # kein Verzeichnis = leerer Katalog, kein Fehler

    for name in namen:
        pfad = os.path.join(verzeichnis, name)
        try:
            with open(pfad, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict):
                raise ValueError("top level is not an object")
        except (OSError, ValueError) as exc:
            cat.errors.append(f"{name}: unreadable - {exc}")
            cat.files.append({"name": name, "service": "", "entries_ok": 0,
                              "entries_skipped": 0, "coverage": [],
                              "color": None, "error": str(exc)})
            continue

        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        service = str(meta.get("service", ""))
        layer = meta.get("layer", "dienst")
        if layer not in ("zuweisung", "dienst"):
            cat.errors.append(f"{name}: unknown layer {layer!r} "
                              f"(allowed: zuweisung, dienst) - dienst used")
            layer = "dienst"
        # T-132: Farbname aus dem Dateikopf. Unbekannter Name ist kein fataler
        # Fehler - die Datei laedt, die Darstellung faellt auf den Hash zurueck.
        farbname = meta.get("color")
        farbwert = resolve_color(farbname)
        if farbname is not None and farbwert is None:
            cat.errors.append(f"{name}: unknown color {farbname!r} (allowed: "
                              f"{', '.join(CATALOG_COLORS)} or #rrggbb) - ignored")
            farbname = None
        defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}

        baender = _load_bands(doc.get("bands"), name, service, layer, cat, farbwert)
        cat.bands.extend(baender)

        ok = 0
        verworfen = 0
        bereiche = [(b["start_hz"], b["stop_hz"]) for b in baender]
        for raw in doc.get("entries") or []:
            if not isinstance(raw, dict):
                cat.errors.append(f"{name}: entries item is not an object - discarded")
                verworfen += 1
                continue
            vorher_fehler = len(cat.errors)
            kanaele = _expand_entry(raw, defaults, name, service, cat)
            for ch in kanaele:
                if cat._add_channel(ch, name):
                    ok += 1
                    bereiche.append((ch["freq_hz"], ch["freq_hz"]))
                else:
                    verworfen += 1
            verworfen += len(cat.errors) - vorher_fehler
            # (T-120) Ein Raster traegt zusaetzlich seine Spannweite als
            # EINEN Bereich zur Abdeckung bei. Zuweisungsbaender liegen seit
            # T-111 allein im FNV-Katalog - eine Rasterdatei ohne eigene
            # bands hinterliesse sonst nur 0-breite Punktbereiche, die
            # _merge_ranges() nie zusammenlegen kann (Air.json: ~2280
            # Einzelbereiche in der Abdeckungszeile von :8010). Die
            # Expansion liefert streng steigende Frequenzen (siehe
            # _airband_8k33/_fixed_step) - erster/letzter Kanal = Spannweite.
            if kanaele and kanaele[0]["raster"]:
                bereiche.append((kanaele[0]["freq_hz"], kanaele[-1]["freq_hz"]))

        cat.files.append({"name": name, "service": service, "entries_ok": ok,
                          "entries_skipped": verworfen, "color": farbname,
                          "coverage": _merge_ranges(bereiche)})

    cat._finalize()
    return cat


# ---------------------------------------------------------------------------
# Gecachter Loader (fuer Anzeige-Server: Katalog-Edits ohne Neustart aktuell)
# ---------------------------------------------------------------------------

# Modul-globaler Cache: Verzeichnis -> (Signatur, Catalog).
_catalog_cache: dict[str, tuple] = {}


def _catalog_dir_signature(catalog_dir: str) -> tuple:
    """Sortiertes Tupel aus (Dateiname, st_mtime_ns, st_size) aller *.json im
    Verzeichnis - Cache-Schluessel fuer load_catalog_cached(). Fehlt das
    Verzeichnis, ist die Signatur leer (wie ein leeres Verzeichnis)."""
    try:
        namen = sorted(n for n in os.listdir(catalog_dir) if n.endswith(".json"))
    except OSError:
        return ()
    sig = []
    for name in namen:
        try:
            st = os.stat(os.path.join(catalog_dir, name))
        except OSError:
            continue
        sig.append((name, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def load_catalog_cached(catalog_dir: str | None = None) -> Catalog:
    """
    Wie load_catalog(), aber mit einem Modul-globalen Cache je Verzeichnis:
    das Ergebnis wird wiederverwendet, solange sich die Signatur (Dateiname,
    mtime_ns, Groesse aller *.json) nicht geaendert hat, sonst wird neu
    geladen und der Cache ersetzt. Zweck: die Anzeige-Server liefern
    /api/known_freq nach Katalog-Edits (block_admin.py) ohne Neustart
    aktuell aus, ohne bei jedem Request die Dateien neu von Platte zu lesen.
    Fehlt das Verzeichnis: Verhalten identisch zu load_catalog (nie fatal).
    """
    verzeichnis = catalog_dir or DEFAULT_CATALOG_DIR
    sig = _catalog_dir_signature(verzeichnis)
    cached = _catalog_cache.get(verzeichnis)
    if cached is not None and cached[0] == sig:
        return cached[1]
    cat = load_catalog(verzeichnis)
    _catalog_cache[verzeichnis] = (sig, cat)
    return cat


# ---------------------------------------------------------------------------
# Standortkonfiguration (Regionsfilter)
# ---------------------------------------------------------------------------

# Unter DATA_ROOT wie DEFAULT_CATALOG_DIR (T-2002) - ein rein relativer Pfad
# wuerde vom Arbeitsverzeichnis des jeweiligen Servers abhaengen (Befund:
# station.json wurde je nach Startverzeichnis nicht gefunden).
DEFAULT_STATION_PATH = paths.data_path("station.json")


def load_station_config(path: str = DEFAULT_STATION_PATH) -> dict:
    """
    Lokale Standortkonfiguration (station.json, Repo-Root, NICHT versioniert -
    siehe station.example.json und .gitignore). Aktuell nur `region_tags`:
    welche 'land-*'-Tags des Katalogs als "eigene Region" gelten. Reine
    Anzeigefilterung im Client (siehe waterfall.py knownVisibleChannels) -
    dieses Modul selbst wertet die Datei nicht aus.

    Fehlende Datei -> {"region_tags": []} (Filter aus, wie bisher ohne
    station.json). Defektes JSON bzw. falsche Form -> dieselbe Vorgabe plus
    Warnung auf stderr. Wirft nie - wie load_catalog(). Die Datei ist winzig,
    Aufrufer lesen sie deshalb JE REQUEST neu (kein Cache noetig, Aenderungen
    wirken ohne Neustart, wie Katalog-Edits ueber load_catalog_cached()).
    """
    vorgabe = {"region_tags": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return vorgabe
    except (OSError, ValueError) as exc:
        print(f"[known_freq] {path} nicht lesbar ({exc}) - Regionsfilter aus",
              file=sys.stderr)
        return vorgabe

    tags = doc.get("region_tags") if isinstance(doc, dict) else None
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        print(f"[known_freq] {path}: region_tags fehlt oder ungueltig - "
              f"Regionsfilter aus", file=sys.stderr)
        return vorgabe
    return {"region_tags": tags}


# ---------------------------------------------------------------------------
# Ansichtsauswahl (T-126)
# ---------------------------------------------------------------------------

# Unter DATA_ROOT wie DEFAULT_CATALOG_DIR und DEFAULT_STATION_PATH (T-2002).
DEFAULT_VIEW_PATH = paths.data_path("catalog_view.json")


def load_view_config(path: str = DEFAULT_VIEW_PATH) -> dict:
    """
    Ansichtsauswahl (catalog_view.json, Repo-Root, NICHT versioniert - siehe
    catalog_view.example.json und .gitignore). `hidden_files` nennt die
    Katalogdateien, die to_client_json() aus bands/channels entfernt.
    Geschrieben wird die Datei nur von eb200_core/block_admin.py (:8010).

    Fehlende Datei -> {"hidden_files": []} (nichts ausgeblendet, Verhalten wie
    vor T-126). Defektes JSON bzw. falsche Form -> dieselbe Vorgabe plus
    Warnung auf stderr. Wirft nie - wie load_station_config(). Die Datei ist
    winzig, Aufrufer lesen sie deshalb JE REQUEST neu (kein Cache noetig,
    Aenderungen wirken ohne Neustart, wie Katalog-Edits ueber
    load_catalog_cached()).

    Namen, zu denen es keine Datei (mehr) gibt, bleiben unbeanstandet stehen -
    sie kosten nichts und ueberleben ein Umbenennen hin und zurueck.
    """
    vorgabe = {"hidden_files": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return vorgabe
    except (OSError, ValueError) as exc:
        print(f"[known_freq] {path} nicht lesbar ({exc}) - Ansichtsfilter aus",
              file=sys.stderr)
        return vorgabe

    namen = doc.get("hidden_files") if isinstance(doc, dict) else None
    if not isinstance(namen, list) or not all(isinstance(n, str) for n in namen):
        print(f"[known_freq] {path}: hidden_files fehlt oder ungueltig - "
              f"Ansichtsfilter aus", file=sys.stderr)
        return vorgabe
    return {"hidden_files": namen}


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def _self_test_real() -> None:
    """Teil A: der echte Katalog in known_freq/."""
    cat = load_catalog()
    assert cat.errors == [], "Katalogfehler:\n  " + "\n  ".join(cat.errors)

    air = [c for c in cat.channels if c["id"].startswith("air-")
           and c["id"] != "air-guard"]
    assert len(air) == 2280, f"Airband: {len(air)} Kanaele statt 2280"
    assert air[0]["label"] == "118.005" and air[0]["freq_hz"] == 118_000_000, air[0]
    assert air[-1]["label"] == "136.990" and air[-1]["freq_hz"] == 136_991_667, air[-1]
    for c in air:
        assert not c["label"].endswith((".020", ".045", ".070", ".095")), \
            f"unmoegliches 8,33-Label {c['label']}"
        assert c["bw_hz"] == 6000, c
    freqs = [c["freq_hz"] for c in air]
    assert all(b > a for a, b in zip(freqs, freqs[1:])), "Airband nicht streng steigend"

    # (T-120) Abdeckungs-Index: das Airband-Raster traegt seine Spannweite
    # als einen Bereich bei - die Datei-Abdeckung darf nicht aus tausenden
    # 0-breiten Punktbereichen bestehen.
    air_datei = cat.by_id("air-118.005")["file"]
    air_cov = next(f for f in cat.files if f["name"] == air_datei)["coverage"]
    assert any(lo <= 118_000_000 and hi >= 136_991_667 for lo, hi in air_cov), air_cov
    assert len(air_cov) <= 10, f"Abdeckung zersplittert: {len(air_cov)} Bereiche"

    # enabled:false-Raster duerfen nichts erzeugen
    assert not [c for c in cat.channels if c["id"].startswith("air25-")]
    assert not [c for c in cat.channels if c["id"].startswith("bc-raster-")]

    ids = [c["id"] for c in cat.channels]
    assert len(ids) == len(set(ids)), "doppelte IDs im Katalog"
    for c in cat.channels:
        assert c["demod"] in DEMOD_TOKENS and c["bw_hz"] in BW_STAGES_HZ, c

    treffer = cat.channels_in_range(118_000_000, 118_025_000)
    assert len(treffer) == 4, [c["label"] for c in treffer]
    assert [c["label"] for c in treffer] == ["118.005", "118.010", "118.015", "118.030"]

    # (Katalogerweiterung 08/2026) Der Bereich 80-150 MHz enthaelt jetzt
    # zusaetzliche Baender (Satelliten-Downlinks, FUP, VHF-Zuweisungen) - die
    # feste Liste waere Wartungsballast. Geprueft wird stattdessen, dass die
    # vier urspruenglichen Baender weiterhin da sind und dass _finalize()
    # nach start_hz sortiert hat.
    # (Nachtrag T-111) bc-fm/air-nav/air-com wurden als reine FNV-Duplikate
    # gelöscht (FNV ist seither die alleinige Quelle fuer Zuweisungsbaender,
    # siehe docs/known_freq_abgleich.md) - fnv-87500/-108000/-117975 decken
    # denselben Bereich jetzt als Zuweisungsband ab.
    baender = [b["id"] for b in cat.bands_in_range(80_000_000, 150_000_000)]
    assert {"fnv-87500", "fnv-108000", "fnv-117975", "ham-2m"} <= set(baender), baender
    starts = [b["start_hz"] for b in cat.bands_in_range(80_000_000, 150_000_000)]
    assert starts == sorted(starts), "Baender nicht nach start_hz sortiert"

    fm = cat.by_id("ham-2m-fm-anruf")
    assert fm is not None and fm["freq_hz"] == 145_500_000, fm
    assert fm["bw_hz"] == 15000, fm      # aus defaults geerbt

    client = cat.to_client_json()
    json.dumps(client)                   # muss serialisierbar sein
    # T-127: notes darf zusaetzlich dabeisein - welcher Kanal channels[0] ist,
    # haengt am Katalogbestand (derzeit ein KiwiSDR-Eintrag auf 11,9 kHz mit
    # Notiz). Leere Notizen darf es im Client-JSON nirgends geben.
    assert set(client["channels"][0]) - {"notes"} == set(_CLIENT_CHANNEL_KEYS), client["channels"][0]
    assert any("notes" in c for c in client["channels"]), "kein Kanal mit Notiz"
    leere = [c["id"] for c in client["channels"]
             if "notes" in c and not c["notes"].strip()]
    assert not leere, leere[:5]

    # Optionale ORS-Anzeigefelder (T-116): nur mit, wenn im Kanal vorhanden -
    # kein None-Fuellen der uebrigen ~2700 Kanaele ohne Standortdaten.
    client_by_id = {c["id"]: c for c in client["channels"]}
    ors = client_by_id["ors-ukw-87600-abtenau-buchberg"]
    assert ors["site"] == "ABTENAU-BUCHBERG" and ors["erp_w"] == 100, ors
    guard = client_by_id["air-guard"]
    assert "site" not in guard and "erp_w" not in guard, guard

    assert cat.by_id("air-guard")["raster"] is False
    assert cat.by_id("air-118.005")["raster"] is True
    # Benannter Kanal und Rasterkanal auf derselben Frequenz koexistieren -
    # die Darstellung bevorzugt den benannten (siehe waterfall.py, drawKnownStrip).
    # Stichproben aus der Katalogerweiterung 08/2026 - je eine pro Bauart:
    # benannter Kanal aus einer Senderliste, geerbte Bandbreite, abweichende
    # Demodulation, erzeugtes Raster, Obergrenze des Empfangsbereichs.
    # Oe1 (92,0 MHz) liegt als bc-wien-oe1 in UKW_BC.json - in ukw_wien.json
    # stehen nur die Sender, die dort noch NICHT vertreten waren.
    assert cat.by_id("bc-wien-oe1")["freq_hz"] == 92_000_000
    assert cat.by_id("sos-marine-16")["bw_hz"] == 15000
    assert cat.by_id("dab-5a")["demod"] == "IQ"
    pmr = [c for c in cat.channels if c["id"].startswith("pmr-")]
    assert len(pmr) == 16 and pmr[0]["freq_hz"] == 446_006_250, pmr[:1]
    assert all(c["freq_hz"] <= FREQ_MAX_HZ for c in cat.channels)

    gleiche = [c for c in cat.channels_in_range(121_500_000, 121_500_000)]
    assert {c["id"] for c in gleiche} == {"air-guard", "air-121.505"}, gleiche


def _write(pfad: str, text: str) -> None:
    with open(pfad, "w", encoding="utf-8") as fh:
        fh.write(text)


def _self_test_synthetic() -> None:
    """Teil B: kuenstliche Katalogverzeichnisse im Temp-Ordner."""
    import tempfile

    # leeres Verzeichnis -> leerer Katalog, keine Fehler
    with tempfile.TemporaryDirectory() as tmp:
        leer = load_catalog(tmp)
        assert leer.channels == [] and leer.bands == [] and leer.errors == []

    with tempfile.TemporaryDirectory() as tmp:
        # a.json: gueltig, plus ungueltige Bandbreite und ungueltige Demodulation
        _write(os.path.join(tmp, "a.json"), json.dumps({
            "version": 1, "meta": {"service": "A"},
            "defaults": {"demod": "FM", "bw_hz": 15000},
            "bands": [{"id": "b1", "title": "B1", "start_hz": 100_000_000,
                       "stop_hz": 101_000_000,
                       "segments": [
                           {"title": "drin", "start_hz": 100_100_000, "stop_hz": 100_200_000},
                           {"title": "draussen", "start_hz": 99_000_000, "stop_hz": 100_500_000},
                       ]}],
            "entries": [
                {"kind": "channel", "id": "dup", "label": "Doppelt", "freq_hz": 100_000_000},
                {"kind": "channel", "id": "bad-bw", "label": "BW", "freq_hz": 100_100_000,
                 "bw_hz": 7000},
                {"kind": "channel", "id": "bad-demod", "label": "Demod", "freq_hz": 100_200_000,
                 "demod": "NFM"},
            ]}, ensure_ascii=False))
        # b.json: dieselbe id wie in a.json -> zweite muss fallen
        _write(os.path.join(tmp, "b.json"), json.dumps({
            "version": 1, "meta": {"service": "B"},
            "defaults": {"demod": "AM", "bw_hz": 6000},
            "entries": [{"kind": "channel", "id": "dup", "label": "Zweitbelegung",
                         "freq_hz": 200_000_000}]}, ensure_ascii=False))
        # c.json: kaputtes JSON -> Datei uebersprungen, Rest laedt weiter
        _write(os.path.join(tmp, "c.json"), "{ das ist kein JSON")

        cat = load_catalog(tmp)

        kaputt = [f for f in cat.files if f["name"] == "c.json"]
        assert kaputt and "error" in kaputt[0], cat.files
        assert any(f["name"] == "a.json" and f["entries_ok"] == 1 for f in cat.files), cat.files

        dup = cat.by_id("dup")
        assert dup is not None and dup["file"] == "a.json", dup
        dup_fehler = [e for e in cat.errors if "'dup'" in e]
        assert len(dup_fehler) == 1 and "a.json" in dup_fehler[0] and "b.json" in dup_fehler[0], \
            dup_fehler

        assert cat.by_id("bad-bw") is None
        assert any("bw_hz 7000" in e for e in cat.errors), cat.errors
        assert cat.by_id("bad-demod") is None
        assert any("'NFM'" in e for e in cat.errors), cat.errors

        band = [b for b in cat.bands if b["id"] == "b1"]
        assert len(band) == 1, cat.bands                       # Band bleibt
        assert [s["title"] for s in band[0]["segments"]] == ["drin"], band[0]
        assert any("segment in band 'b1'" in e for e in cat.errors), cat.errors


def _self_test_layer() -> None:
    """Datei-Meta 'layer': fehlt -> 'dienst', 'zuweisung' -> 'zuweisung',
    ungueltiger Wert -> Fehler + 'dienst'."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "a.json"), json.dumps({
            "version": 1, "meta": {"service": "A"},
            "bands": [{"id": "layer-a", "title": "A", "start_hz": 100_000_000,
                       "stop_hz": 101_000_000}]}, ensure_ascii=False))
        _write(os.path.join(tmp, "b.json"), json.dumps({
            "version": 1, "meta": {"service": "B", "layer": "zuweisung"},
            "bands": [{"id": "layer-b", "title": "B", "start_hz": 102_000_000,
                       "stop_hz": 103_000_000}]}, ensure_ascii=False))
        _write(os.path.join(tmp, "c.json"), json.dumps({
            "version": 1, "meta": {"service": "C", "layer": "unsinn"},
            "bands": [{"id": "layer-c", "title": "C", "start_hz": 104_000_000,
                       "stop_hz": 105_000_000}]}, ensure_ascii=False))

        cat = load_catalog(tmp)
        by_id = {b["id"]: b for b in cat.bands}
        assert by_id["layer-a"]["layer"] == "dienst", by_id["layer-a"]
        assert by_id["layer-b"]["layer"] == "zuweisung", by_id["layer-b"]
        assert by_id["layer-c"]["layer"] == "dienst", by_id["layer-c"]
        assert any("layer 'unsinn'" in e for e in cat.errors), cat.errors


def _self_test_station() -> None:
    """load_station_config: fehlende, gueltige, defekte Datei."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        fehlend = os.path.join(tmp, "nicht_da.json")
        assert load_station_config(fehlend) == {"region_tags": []}

        gueltig = os.path.join(tmp, "gueltig.json")
        _write(gueltig, json.dumps({"region_tags": ["land-w", "land-n"]}))
        assert load_station_config(gueltig) == {"region_tags": ["land-w", "land-n"]}

        defekt = os.path.join(tmp, "defekt.json")
        _write(defekt, "{ das ist kein JSON")
        assert load_station_config(defekt) == {"region_tags": []}


def _self_test_view() -> None:
    """T-126: load_view_config (fehlend/gueltig/defekt) und die Filterwirkung
    von to_client_json. Geprueft wird ausdruecklich auch, dass by_id() vom
    Filter UNBERUEHRT bleibt - das ist die Zusage 'reine Anzeigefilterung'."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        fehlend = os.path.join(tmp, "nicht_da.json")
        assert load_view_config(fehlend) == {"hidden_files": []}

        gueltig = os.path.join(tmp, "gueltig.json")
        _write(gueltig, json.dumps({"version": 1, "hidden_files": ["b.json"]}))
        assert load_view_config(gueltig) == {"hidden_files": ["b.json"]}

        defekt = os.path.join(tmp, "defekt.json")
        _write(defekt, "{ das ist kein JSON")
        assert load_view_config(defekt) == {"hidden_files": []}

        # Zwei Katalogdateien, je ein Band und ein Kanal.
        kat = os.path.join(tmp, "kat")
        os.mkdir(kat)
        for name, kid, hz in (("a.json", "view-a", 100_000_000),
                              ("b.json", "view-b", 200_000_000)):
            _write(os.path.join(kat, name), json.dumps({
                "version": 1, "meta": {"service": name},
                "defaults": {"demod": "FM", "bw_hz": 15000},
                "bands": [{"id": kid + "-band", "title": kid,
                           "start_hz": hz, "stop_hz": hz + 1_000_000}],
                "entries": [{"kind": "channel", "id": kid, "label": kid,
                             "freq_hz": hz}]}, ensure_ascii=False))

        cat = load_catalog(kat)
        assert cat.errors == [], cat.errors

        voll = cat.to_client_json()
        assert len(voll["channels"]) == 2 and len(voll["bands"]) == 2, voll
        assert all(f["in_view"] for f in voll["files"]), voll["files"]
        assert voll["view"] == {"total": 2, "hidden": 0}, voll["view"]

        gefiltert = cat.to_client_json(view={"hidden_files": ["b.json"]})
        assert [c["id"] for c in gefiltert["channels"]] == ["view-a"], gefiltert["channels"]
        assert [b["file"] for b in gefiltert["bands"]] == ["a.json"], gefiltert["bands"]
        assert len(gefiltert["files"]) == 2, gefiltert["files"]
        assert {f["name"]: f["in_view"] for f in gefiltert["files"]} == \
            {"a.json": True, "b.json": False}, gefiltert["files"]
        assert gefiltert["view"] == {"total": 2, "hidden": 1}, gefiltert["view"]
        json.dumps(gefiltert)        # muss serialisierbar bleiben

        # Reine Anzeigefilterung: die Katalogsicht selbst bleibt vollstaendig.
        assert cat.by_id("view-b") is not None
        assert len(cat.channels_in_range(FREQ_MIN_HZ, FREQ_MAX_HZ)) == 2

        # Unbekannter Name schadet nicht, leere Liste filtert nichts.
        assert len(cat.to_client_json(view={"hidden_files": ["gibtsnicht.json"]})["channels"]) == 2
        assert len(cat.to_client_json(view={"hidden_files": []})["channels"]) == 2


def _self_test_notes() -> None:
    """T-127: notes geht im Client-JSON NUR mit, wenn der Kanal nichtleeren
    Text traegt. Der Fallstrick ist, dass notes ein geerbtes Feld ist und
    damit auf JEDEM Kanal-Dict liegt - ohne Datei-Default mit dem Wert None.
    Geprueft werden alle vier Faelle: keine Notiz, eigene Notiz, geerbte
    Notiz aus den defaults, Notiz nur aus Leerzeichen."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        kat = os.path.join(tmp, "kat")
        os.mkdir(kat)
        _write(os.path.join(kat, "n.json"), json.dumps({
            "version": 1, "meta": {"service": "Test"},
            "defaults": {"demod": "USB", "bw_hz": 2400},
            "entries": [
                {"kind": "channel", "id": "n-ohne", "label": "ohne",
                 "freq_hz": 5_000_000},
                {"kind": "channel", "id": "n-eigen", "label": "eigen",
                 "freq_hz": 6_000_000, "notes": "eigene Notiz"},
                {"kind": "channel", "id": "n-leer", "label": "leer",
                 "freq_hz": 7_000_000, "notes": "   "},
            ]}, ensure_ascii=False))
        _write(os.path.join(kat, "g.json"), json.dumps({
            "version": 1, "meta": {"service": "Test geerbt"},
            "defaults": {"demod": "USB", "bw_hz": 2400,
                         "notes": "aus den defaults"},
            "entries": [{"kind": "channel", "id": "g-erbt", "label": "erbt",
                         "freq_hz": 8_000_000}]}, ensure_ascii=False))

        cat = load_catalog(kat)
        assert cat.errors == [], cat.errors

        # Serverseitig traegt jeder Kanal den Schluessel - ohne Notiz mit None.
        assert cat.by_id("n-ohne")["notes"] is None, cat.by_id("n-ohne")
        assert cat.by_id("g-erbt")["notes"] == "aus den defaults", cat.by_id("g-erbt")

        klient = {c["id"]: c for c in cat.to_client_json()["channels"]}
        assert "notes" not in klient["n-ohne"], klient["n-ohne"]
        assert "notes" not in klient["n-leer"], klient["n-leer"]
        assert klient["n-eigen"]["notes"] == "eigene Notiz", klient["n-eigen"]
        assert klient["g-erbt"]["notes"] == "aus den defaults", klient["g-erbt"]


def _self_test_color() -> None:
    """T-132: meta.color faerbt alle Baender der Datei. Geprueft werden alle
    drei Faelle - gesetzte Farbe, fehlende Farbe, unbekannter Name (laedt
    weiter, meldet und faellt auf keine Farbe zurueck)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        kat = os.path.join(tmp, "kat")
        os.mkdir(kat)

        def schreibe(name, meta_extra, bid, hz):
            meta = {"service": name}
            meta.update(meta_extra)
            _write(os.path.join(kat, name), json.dumps({
                "version": 1, "meta": meta,
                "defaults": {"demod": "FM", "bw_hz": 15000},
                "bands": [{"id": bid, "title": bid,
                           "start_hz": hz, "stop_hz": hz + 1_000_000}],
                "entries": []}, ensure_ascii=False))

        schreibe("a.json", {"color": "gelb"}, "farb-a", 100_000_000)
        schreibe("b.json", {}, "farb-b", 200_000_000)
        schreibe("c.json", {"color": "knallpink"}, "farb-c", 300_000_000)
        # T-132b: eigener Hexwert, Grossschreibung, und ein Wert, der wie Hex
        # aussieht, aber keiner ist.
        schreibe("d.json", {"color": "#1A2B3C"}, "farb-d", 400_000_000)
        schreibe("e.json", {"color": "#12345"}, "farb-e", 500_000_000)

        cat = load_catalog(kat)
        nach_id = {b["id"]: b for b in cat.bands}
        assert nach_id["farb-a"]["color"] == resolve_color("gelb"), nach_id["farb-a"]
        assert nach_id["farb-b"]["color"] is None, nach_id["farb-b"]
        assert nach_id["farb-c"]["color"] is None, nach_id["farb-c"]
        assert nach_id["farb-d"]["color"] == "#1a2b3c", nach_id["farb-d"]
        assert nach_id["farb-e"]["color"] is None, nach_id["farb-e"]
        assert any("knallpink" in e for e in cat.errors), cat.errors
        assert any("12345" in e for e in cat.errors), cat.errors
        assert len(cat.errors) == 2, cat.errors
        assert resolve_color("#AABBCC") == "#aabbcc"
        assert list(CATALOG_COLOR_LABELS) == list(CATALOG_COLORS), "Farb-Anzeigenamen unvollstaendig"
        assert resolve_color("") is None and resolve_color(None) is None
        assert resolve_color("#gggggg") is None
        # T-132c: die alten Namen loesen ueber _COLOR_ALIASES weiter auf, damit
        # die Katalogdateien aus T-132a unveraendert gueltig bleiben.
        for alt_name, neu_name in _COLOR_ALIASES.items():
            assert resolve_color(alt_name) == CATALOG_COLORS[neu_name], alt_name
        for name in ("blau", "violett", "gruen", "petrol", "oliv", "rot", "grau"):
            assert resolve_color(name) == CATALOG_COLORS[name], name
        assert len(CATALOG_COLORS) == 39, len(CATALOG_COLORS)
        assert not (set(_COLOR_ALIASES) & set(CATALOG_COLORS)), "Alias verdeckt eine Farbe"
        assert all(v in CATALOG_COLORS for v in _COLOR_ALIASES.values()), _COLOR_ALIASES

        nach_datei = {f["name"]: f for f in cat.files}
        assert nach_datei["a.json"]["color"] == "gelb", nach_datei["a.json"]
        assert nach_datei["b.json"]["color"] is None, nach_datei["b.json"]
        assert nach_datei["c.json"]["color"] is None, nach_datei["c.json"]
        assert nach_datei["d.json"]["color"] == "#1A2B3C", nach_datei["d.json"]

        client = cat.to_client_json()
        json.dumps(client)
        farben = {b["id"]: b["color"] for b in client["bands"]}
        assert farben["farb-a"] == resolve_color("gelb"), farben


def _self_test() -> None:
    _self_test_real()
    _self_test_synthetic()
    _self_test_layer()
    _self_test_station()
    _self_test_view()
    _self_test_notes()
    _self_test_color()
    print("[known_freq] Selbsttest OK")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _mhz(hz: int) -> str:
    return f"{hz / 1e6:.4f}"


def _dump(cat: Catalog) -> None:
    for f in cat.files:
        kanaele = sum(1 for c in cat.channels if c["file"] == f["name"])
        baender = sum(1 for b in cat.bands if b["file"] == f["name"])
        abdeckung = ", ".join(f"{_mhz(lo)}-{_mhz(hi)}" for lo, hi in f["coverage"]) or "-"
        print(f"{f['name']:<26} {f['service']:<20} "
              f"Kanaele={kanaele:<6} Baender={baender:<3} "
              f"uebersprungen={f['entries_skipped']:<4} MHz: {abdeckung}")
    print(f"\nSumme: {len(cat.channels)} Kanaele, {len(cat.bands)} Baender, "
          f"{len(cat.errors)} Fehler")
    for e in cat.errors:
        print(f"  FEHLER {e}")


def _range(cat: Catalog, start_mhz: float, stop_mhz: float) -> None:
    start_hz, stop_hz = int(round(start_mhz * 1e6)), int(round(stop_mhz * 1e6))
    baender = cat.bands_in_range(start_hz, stop_hz)
    kanaele = cat.channels_in_range(start_hz, stop_hz)
    print(f"Bereich {_mhz(start_hz)}-{_mhz(stop_hz)} MHz: "
          f"{len(baender)} Baender, {len(kanaele)} Kanaele\n")
    for b in baender:
        print(f"  BAND  {b['id']:<12} {b['title']:<26} "
              f"{_mhz(b['start_hz'])}-{_mhz(b['stop_hz'])} MHz")
    if baender and kanaele:
        print()
    for c in kanaele:
        print(f"  KANAL {c['label']:<14} {_mhz(c['freq_hz']):>12} MHz  "
              f"{c['demod']:<5} BW {c['bw_hz']:>6} Hz  {c['id']}")


def _praefixe(cat: Catalog) -> None:
    """Vergebene ID-Praefixe je Katalogdatei, dazu die dateiuebergreifend
    geteilten. Gedacht fuer den Moment VOR dem Anlegen oder Einspielen einer
    neuen Liste - siehe Vergaberegel in der Moduldoku. Als Praefix gilt der
    Anfang der id bis zum ersten Trennzeichen ('-' oder '.')."""
    def kopf(cid: str) -> str:
        treffer = re.match(r"^[A-Za-z0-9_]+", cid)
        return treffer.group(0) if treffer else cid

    je_datei: dict = {}
    for eintrag in list(cat.channels) + list(cat.bands):
        pf = je_datei.setdefault(eintrag["file"], {})
        p = kopf(eintrag["id"])
        pf[p] = pf.get(p, 0) + 1

    for name in sorted(je_datei):
        liste = sorted(je_datei[name].items(), key=lambda kv: (-kv[1], kv[0]))
        print(f"{name:<30} " + ", ".join(f"{p}({n})" for p, n in liste))

    geteilt: dict = {}
    for name, pf in je_datei.items():
        for p in pf:
            geteilt.setdefault(p, set()).add(name)
    mehrfach = {p: sorted(d) for p, d in geteilt.items() if len(d) > 1}

    print("\nPraefixe in mehreren Dateien:")
    if not mehrfach:
        print("  keine")
    for p in sorted(mehrfach):
        print(f"  {p:<14} {', '.join(mehrfach[p])}")
    print("\nEin geteiltes Praefix ist nur dann ein Fehler, wenn die id allein "
          "aus der\nFrequenz gebildet wird - dann kollidiert jede zweite Liste "
          "ueber denselben\nBereich. Traegt die id noch Land, Band oder "
          "Standort, ist das Teilen in\nOrdnung.")


def main() -> int:
    import argparse

    # Die Senderlisten enthalten Diakritika (CRo, Gyoer, Maria). Auf einer
    # cp1252-Konsole wuerde print() sonst mit UnicodeEncodeError abbrechen -
    # errors="replace" ersetzt nur das einzelne Zeichen. Ab Python 3.7.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(description="Katalog bekannter Frequenzen")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    ap.add_argument("--dump", action="store_true", help="Dateien und Abdeckung listen")
    ap.add_argument("--range", nargs=2, type=float, metavar=("START_MHZ", "STOP_MHZ"),
                    help="Baender und Kanaele im Bereich listen")
    ap.add_argument("--praefixe", action="store_true",
                    help="vergebene ID-Praefixe je Datei und geteilte Praefixe listen")
    ap.add_argument("--dir", default=None, help="abweichendes Katalogverzeichnis")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    cat = load_catalog(args.dir)
    if args.praefixe:
        _praefixe(cat)
    elif args.range:
        _range(cat, args.range[0], args.range[1])
    else:
        _dump(cat)          # auch der Default ohne Argumente
    return 0                # Fehler im Katalog sind Daten, kein Abbruch


if __name__ == "__main__":
    raise SystemExit(main())
