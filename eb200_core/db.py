#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Gerhard Amon (OE3GAS)
"""
eb200_core/db.py

SQLite-Anbindung für die "große" Projekt-Datenbank (siehe WS1_-_Aktivitaetsmonitoring.md,
Abschnitt 3b/4). Enthält vorerst die Tabelle `frequency_blocks` (Scanbereiche, egal ob
manuell über die Verwaltungsmaske oder aus dem Wasserfall per Bereichsselektion angelegt).

Design-Entscheidungen (bewusst einfach gehalten für den Einstieg ohne SQLite-Erfahrung):
  - Eine einzige .db-Datei (Standardpfad: <DATA_ROOT>/data/eb200_project.db).
  - Kein ORM, reines sqlite3 aus der Python-Standardbibliothek - keine zusätzliche
    Abhängigkeit nötig.
  - Zeilen werden als sqlite3.Row zurückgegeben und in normale dict-Objekte umgewandelt,
    damit der Rest des Codes (z. B. FastAPI/Flask-Endpunkte) nicht mit SQLite-internen
    Typen hantieren muss.
  - init_db() ist idempotent (CREATE TABLE IF NOT EXISTS) - beliebig oft aufrufbar,
    z. B. bei jedem App-Start. Zusätzlich läuft eine leichte Schema-Migration
    (_ensure_columns), die fehlende Spalten bei bereits existierenden Datenbanken
    per ALTER TABLE nachrüstet, ohne bestehende Daten zu verlieren.

Aufruf als eigenständiges Skript (python db.py) führt einen Selbsttest aus: legt in
einer temporären Datenbank (T-1837; die echte Datei bleibt unberührt) einen
Testblock an, listet ihn, aktualisiert ihn, löscht ihn wieder - ohne dass man selbst
SQL schreiben oder ein DB-Tool bedienen muss.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# Erlaubt auch den Direktaufruf "python eb200_core/db.py": dort fehlt sonst das
# Repo-Root im Suchpfad fuer den paths-Import (T-2002, Weiche wie in
# eb200_core/path_sandbox.py).
if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eb200_core import paths

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# Standard-Pfad: <DATA_ROOT>/data/eb200_project.db (T-2002, eb200_core/paths.py).
DEFAULT_DB_PATH = Path(paths.data_path("data", "eb200_project.db"))

# Erlaubte Werte für das origin-Feld (Herkunft des Frequenzblocks)
VALID_ORIGINS = ("manual", "waterfall")

# Spalten, die per Migration nachgerüstet werden, falls eine ältere
# Datenbankdatei ohne sie existiert. Reihenfolge = ALTER-TABLE-Reihenfolge.
_MIGRATABLE_COLUMNS = [
    ("label", "TEXT NOT NULL DEFAULT ''"),
    ("comment", "TEXT"),
]


# ---------------------------------------------------------------------------
# Datenmodell (leichte dataclass statt eigener ORM-Klasse)
# ---------------------------------------------------------------------------

@dataclass
class FrequencyBlock:
    block_id: int
    label: str
    freq_start_hz: int
    freq_stop_hz: int
    step_hz: int
    mode: str
    bw_hz: int
    threshold_dbuv: float
    antenna: str
    origin: str
    schedule: Optional[str]
    comment: Optional[str]
    last_scan_ts: Optional[str]
    created_ts: str


# ---------------------------------------------------------------------------
# Verbindung & Schema
# ---------------------------------------------------------------------------

def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Öffnet (und legt bei Bedarf an) die SQLite-Datenbankdatei.
    Verzeichnis wird automatisch erstellt, falls es noch nicht existiert.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row      # Zeilen wie dicts ansprechbar (row["freq_hz"])
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """
    Kontextmanager für einen kompletten Verbindungs-Lebenszyklus:
    öffnet, committet bei Erfolg, rollt bei Exception zurück, schließt immer.

    Verwendung:
        with db_session() as conn:
            create_block(conn, ...)
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    """
    Legt die Tabelle frequency_blocks an, falls sie noch nicht existiert, und
    rüstet bei bereits existierenden (älteren) Datenbanken fehlende Spalten
    per Migration nach. Beliebig oft aufrufbar (idempotent) - z. B. bei jedem
    App-Start aufrufen.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS frequency_blocks (
            block_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            label           TEXT    NOT NULL DEFAULT '',
            freq_start_hz   INTEGER NOT NULL,
            freq_stop_hz    INTEGER NOT NULL,
            step_hz         INTEGER NOT NULL,
            mode            TEXT    NOT NULL,
            bw_hz           INTEGER NOT NULL,
            threshold_dbuv  REAL    NOT NULL,
            antenna         TEXT    NOT NULL,
            origin          TEXT    NOT NULL DEFAULT 'manual',
            schedule        TEXT,
            comment         TEXT,
            last_scan_ts    TEXT,
            created_ts      TEXT    NOT NULL,
            CHECK (freq_start_hz < freq_stop_hz),
            CHECK (step_hz > 0),
            CHECK (origin IN ('manual', 'waterfall'))
        )
        """
    )
    _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """
    Migration für bereits existierende Datenbanken, die vor Einführung von
    label/comment angelegt wurden: fehlende Spalten per ALTER TABLE
    nachrüsten, bestehende Zeilen bleiben erhalten (label wird '', comment
    wird NULL für alte Zeilen).
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(frequency_blocks)")}
    for column_name, column_def in _MIGRATABLE_COLUMNS:
        if column_name not in existing:
            conn.execute(f"ALTER TABLE frequency_blocks ADD COLUMN {column_name} {column_def}")


# ---------------------------------------------------------------------------
# CRUD-Funktionen
# ---------------------------------------------------------------------------

def create_block(
    conn: sqlite3.Connection,
    freq_start_hz: int,
    freq_stop_hz: int,
    step_hz: int,
    mode: str,
    bw_hz: int,
    threshold_dbuv: float,
    antenna: str,
    origin: str = "manual",
    label: str = "",
    schedule: Optional[str] = None,
    comment: Optional[str] = None,
) -> int:
    """
    Legt einen neuen Frequenzblock an und gibt die neu vergebene block_id zurück.
    Wirft ValueError bei offensichtlich ungültigen Werten (Start >= Stop, unbekannter
    origin) - vor dem eigentlichen INSERT, damit die Fehlermeldung verständlich bleibt
    statt einer rohen SQLite-Fehlermeldung.
    """
    if freq_start_hz >= freq_stop_hz:
        raise ValueError(
            f"freq_start_hz ({freq_start_hz}) must be less than freq_stop_hz "
            f"({freq_stop_hz})"
        )
    if step_hz <= 0:
        raise ValueError(f"step_hz must be positive, was {step_hz}")
    if origin not in VALID_ORIGINS:
        raise ValueError(f"origin must be one of: {VALID_ORIGINS}, was '{origin}'")

    if not label.strip():
        # Fallback, falls das Formular (z. B. per API) ohne Bezeichnung
        # aufgerufen wird - der eigentliche Default kommt normalerweise
        # bereits aus dem Browser-Formular ("Scan " + Startfrequenz).
        label = f"Scan {freq_start_hz / 1000:.3f} kHz"

    created_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    cursor = conn.execute(
        """
        INSERT INTO frequency_blocks (
            label, freq_start_hz, freq_stop_hz, step_hz, mode, bw_hz,
            threshold_dbuv, antenna, origin, schedule, comment, last_scan_ts, created_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            label, freq_start_hz, freq_stop_hz, step_hz, mode, bw_hz,
            threshold_dbuv, antenna, origin, schedule, comment, created_ts,
        ),
    )
    return cursor.lastrowid


def list_blocks(conn: sqlite3.Connection) -> list[FrequencyBlock]:
    """Gibt alle Frequenzblöcke zurück, sortiert nach Startfrequenz."""
    rows = conn.execute(
        "SELECT * FROM frequency_blocks ORDER BY freq_start_hz"
    ).fetchall()
    return [FrequencyBlock(**dict(row)) for row in rows]


def get_block(conn: sqlite3.Connection, block_id: int) -> Optional[FrequencyBlock]:
    """Gibt einen einzelnen Frequenzblock zurück, oder None falls nicht vorhanden."""
    row = conn.execute(
        "SELECT * FROM frequency_blocks WHERE block_id = ?", (block_id,)
    ).fetchone()
    return FrequencyBlock(**dict(row)) if row else None


def update_block(conn: sqlite3.Connection, block_id: int, **fields) -> None:
    """
    Aktualisiert einzelne Felder eines bestehenden Frequenzblocks.

    Verwendung:
        update_block(conn, 3, threshold_dbuv=12.5, step_hz=5000)

    Nur die übergebenen Felder werden geändert, alle anderen bleiben unverändert.
    Wirft ValueError, falls block_id nicht existiert oder ein unbekanntes Feld
    übergeben wird.
    """
    if not fields:
        return   # nichts zu tun

    allowed = {
        "label", "freq_start_hz", "freq_stop_hz", "step_hz", "mode", "bw_hz",
        "threshold_dbuv", "antenna", "origin", "schedule", "comment", "last_scan_ts",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown fields for update_block: {unknown}")

    set_clause = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [block_id]

    cursor = conn.execute(
        f"UPDATE frequency_blocks SET {set_clause} WHERE block_id = ?", values
    )
    if cursor.rowcount == 0:
        raise ValueError(f"no frequency block with block_id={block_id} found")


def delete_block(conn: sqlite3.Connection, block_id: int) -> None:
    """Löscht einen Frequenzblock. Kein Fehler, falls die ID nicht existiert."""
    conn.execute("DELETE FROM frequency_blocks WHERE block_id = ?", (block_id,))


def touch_last_scan(conn: sqlite3.Connection, block_id: int) -> None:
    """
    Setzt last_scan_ts auf 'jetzt' - vom F-SCAN-Ablauf nach jedem abgeschlossenen
    Lauf über diesen Block aufzurufen (siehe WS1, Abschnitt 4).
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    update_block(conn, block_id, last_scan_ts=ts)


# ---------------------------------------------------------------------------
# Selbsttest - "python db.py" direkt ausführen, ohne DB-Tool oder eigenes SQL
# ---------------------------------------------------------------------------

def _self_test() -> None:
    # T-1837: eigene temporaere Datenbank. Bis dahin lief der Selbsttest auf der
    # echten data/eb200_project.db (Testblock anlegen/loeschen, init_db migriert).
    import tempfile
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "selbsttest.db"

    def stand(pfad: Path):
        return (pfad.stat().st_size, pfad.stat().st_mtime_ns) if pfad.exists() else None

    echt_vorher = stand(DEFAULT_DB_PATH)
    print(f"Verwende Datenbankdatei: {db_path}")

    with db_session(db_path) as conn:
        init_db(conn)
        print("OK  Tabelle frequency_blocks vorhanden (angelegt/migriert oder bereits aktuell)")

        block_id = create_block(
            conn,
            freq_start_hz=118_000_000,
            freq_stop_hz=137_000_000,
            step_hz=8_330,
            mode="AM",
            bw_hz=9_000,
            threshold_dbuv=10.0,
            antenna="discone",
            origin="manual",
            label="Testblock Airband",
            schedule="stündlich",
            comment="nur für den Selbsttest angelegt",
        )
        print(f"OK  Testblock angelegt, block_id={block_id}")

        blocks = list_blocks(conn)
        print(f"OK  {len(blocks)} Block(s) in der Tabelle:")
        for b in blocks:
            print(f"    {b}")

        update_block(conn, block_id, threshold_dbuv=12.5, comment="aktualisiert im Selbsttest")
        updated = get_block(conn, block_id)
        assert updated is not None and updated.threshold_dbuv == 12.5
        print(f"OK  Update erfolgreich, neuer threshold_dbuv={updated.threshold_dbuv}")

        delete_block(conn, block_id)
        assert get_block(conn, block_id) is None
        print("OK  Testblock wieder gelöscht")

    tmp.cleanup()
    assert stand(DEFAULT_DB_PATH) == echt_vorher, "echte Datenbankdatei veraendert"
    print("OK  echte Datenbankdatei unberuehrt")

    print("\nSelbsttest erfolgreich abgeschlossen.")


if __name__ == "__main__":
    _self_test()