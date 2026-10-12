#!/usr/bin/env python3
"""
eb200_core/sched_health.py

Scheduler-Diagnostik (PLAN.md T-206): misst per Herzschlag, ob der
Long-Run-Scheduler-Thread (waterfall.py Controller._job_scheduler_loop) noch
lebt, statt bei einem Haenger nur nach 30 s Stille zu raten. Motivation
(Entscheidungslog): erst messen, wo es haengt, statt den Scheduler-Timeout
zu erhoehen.

SchedulerHealth ist ein reiner Zaehler/Zeitstempel-Halter (kein I/O, keine
Netzwerk-/Geraeteabhaengigkeit) - der Scheduler-Thread meldet sich vor/nach
jeder Phase (tick_start/tick_end), ein separater Waechter-Thread liest nur
snapshot()/should_dump() und schreibt bei Verdacht auf einen Freeze einen
Thread-Dump (dump_threads, faulthandler.dump_traceback) auf die Platte -
der Dump selbst ist NICHT threadsicher-blockierend fuer den Scheduler, da er
in einem eigenen Thread laeuft.

Nur Python-Standardbibliothek. Konsolenausgaben ASCII (cp1252-Konsole,
siehe eb200_core/known_freq.py).

Sprachneutraler Codevertrag (T-1802a, Entscheidungslog 2026-08-31 Regel 2/3):
`phase` (aus tick_start()) und `last_error["code"]` (aus error()) sind
sprachneutrale Codes, keine Anzeigetexte - der Anzeigetext entsteht im
Client (labelOf('sched_phase', ...) in eb200_core/ui_theme.py). `phase_arg`
traegt den beteiligten Job, sofern einer beteiligt ist, getrennt vom Code.
`last_error["detail"]` bleibt technische Diagnostik (typischerweise
repr(exc)) und wird bewusst NICHT uebersetzt.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
from datetime import datetime


def _now_iso() -> str:
    """Lokale Zeit mit Offset, Sekundenaufloesung (Muster eb200_core/job_store.py._now_iso)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


PHASE_CODES = ("check", "start_dscan", "start_ifpan", "start_mscan",
               "stop_finished", "stop_aborted")


class SchedulerHealth:
    """Herzschlag-Zustand eines Scheduler-Threads.

    Alle oeffentlichen Methoden sind ueber EIN Lock threadsicher; snapshot()
    fuehrt kein I/O aus und haelt das Lock nur fuer die reine Feld-Kopie.
    """

    def __init__(self, slow_s: float = 5.0, freeze_s: float = 120.0, recover_s: float = 60.0) -> None:
        self._lock = threading.Lock()
        self._slow_s = slow_s
        self._freeze_s = freeze_s
        self._recover_s = recover_s

        self._phase: str | None = None
        self._phase_arg: str | None = None
        self._t0: float | None = None          # monotonic Start der laufenden Phase
        self._in_tick = False                  # zwischen tick_start und tick_end

        self._ticks = 0
        self._slow_ticks = 0
        self._last_tick: float | None = None   # monotonic Zeitpunkt des letzten tick_end
        self._last_tick_iso: str | None = None
        self._last_error: dict | None = None

        # should_dump() feuert genau einmal je Freeze-Episode: _dump_armed
        # wird bei Erholung (age_s < recover_s) wieder scharf gemacht. Vor
        # dem allerersten Freeze ist sie scharf, ein Reset ist nicht noetig.
        self._dump_armed = True

    def tick_start(self, code: str, arg: str | None = None, now: float | None = None) -> None:
        """Scheduler betritt eine Phase. code ist einer der sechs Werte aus
        PHASE_CODES (check, start_dscan, start_ifpan, start_mscan,
        stop_finished, stop_aborted); arg traegt den beteiligten Job, sofern
        einer beteiligt ist. Der Code wird NICHT zur Laufzeit geprueft - ein
        unbekannter Code darf den Scheduler nicht stoeren, er faellt in der
        Oberflaeche roh durch wie jeder unbekannte Wert."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._phase = code
            self._phase_arg = arg
            self._t0 = now
            self._in_tick = True

    def tick_end(self, now: float | None = None) -> bool:
        """Scheduler hat die laufende Phase beendet - Herzschlag.

        Gibt True zurueck, wenn die Phase laenger als slow_s gedauert hat
        (der Aufrufer loggt das dann); zaehlt in dem Fall slow_ticks hoch.
        """
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._ticks += 1
            self._in_tick = False
            self._last_tick = now
            self._last_tick_iso = _now_iso()
            dauer = (now - self._t0) if self._t0 is not None else None
            langsam = dauer is not None and dauer > self._slow_s
            if langsam:
                self._slow_ticks += 1
            return langsam

    def error(self, code: str, detail: str | None = None) -> None:
        """Merkt den letzten Scheduler-Fehler (Code + Zeitpunkt) fuer snapshot().
        detail ist technische Diagnostik (typischerweise repr(exc)) und wird
        bewusst NICHT uebersetzt."""
        with self._lock:
            self._last_error = {"code": code, "detail": detail, "at": _now_iso()}

    def snapshot(self, now: float | None = None) -> dict:
        """Momentaufnahme fuer /api/longrun/status - kein I/O, nur Feld-Kopie."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            age_s = (now - self._last_tick) if self._last_tick is not None else None
            in_tick_s = (now - self._t0) if (self._in_tick and self._t0 is not None) else None
            return {
                "ticks": self._ticks,
                "slow_ticks": self._slow_ticks,
                "phase": self._phase,
                "phase_arg": self._phase_arg,
                "age_s": age_s,
                "in_tick_s": in_tick_s,
                "last_tick_iso": self._last_tick_iso,
                "last_error": self._last_error,
            }

    def should_dump(self, now: float | None = None) -> bool:
        """True genau einmal je Freeze-Episode (siehe Modul-Docstring).

        Vor dem ersten tick_end (last_tick None) immer False. Eine Episode
        gilt als beendet, sobald age_s zwischenzeitlich unter recover_s
        gefallen ist - erst danach kann die naechste Episode wieder feuern.
        """
        now = now if now is not None else time.monotonic()
        with self._lock:
            if self._last_tick is None:
                return False
            age_s = now - self._last_tick
            if age_s < self._recover_s:
                self._dump_armed = True
            if age_s > self._freeze_s and self._dump_armed:
                self._dump_armed = False
                return True
            return False


def dump_threads(path: str, header: str) -> None:
    """Schreibt header, eine Thread-Legende (Name -> ident) und den
    faulthandler-Traceback aller Threads nach path (utf-8, Verzeichnis wird
    angelegt). faulthandler zeigt je Thread nur den ident, keinen Namen -
    die Legende davor macht den Dump lesbar.

    faulthandler.dump_traceback schreibt direkt auf den Datei-Deskriptor
    (C-Level, signalsicher) und umgeht damit den Python-Schreibpuffer -
    ohne den expliziten flush() vor dem Aufruf koennte der C-Teil VOR
    Header/Legende im File landen.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        if not header.endswith("\n"):
            fh.write("\n")
        fh.write("\nThread-Legende (Name -> ident):\n")
        for t in threading.enumerate():
            fh.write(f"  {t.name} -> {t.ident}\n")
        fh.write("\n")
        fh.flush()
        faulthandler.dump_traceback(fh, all_threads=True)


# ---------------------------------------------------------------------------
# Selbsttest - "python eb200_core/sched_health.py --selftest" ohne Hardware,
# ohne Zeit zu schlafen (now wird bei jedem Aufruf explizit uebergeben).
# ---------------------------------------------------------------------------

def _self_test() -> None:
    import tempfile

    # -- T1: Zustand vor dem ersten Tick --------------------------------
    sh = SchedulerHealth(slow_s=5.0, freeze_s=120.0, recover_s=60.0)
    snap = sh.snapshot(now=0.0)
    assert snap["ticks"] == 0, snap
    assert snap["age_s"] is None, snap
    assert sh.should_dump(now=0.0) is False
    print("T1 ok")

    # -- T2: normaler Tick (1 s) -----------------------------------------
    sh.tick_start("phase-a", now=10.0)
    langsam = sh.tick_end(now=11.0)
    assert langsam is False, langsam
    snap = sh.snapshot(now=11.0)
    assert snap["ticks"] == 1, snap
    assert snap["slow_ticks"] == 0, snap
    print("T2 ok")

    # -- T3: langsamer Tick (7 s) ------------------------------------------
    sh.tick_start("phase-b", now=20.0)
    langsam = sh.tick_end(now=27.0)
    assert langsam is True, langsam
    snap = sh.snapshot(now=27.0)
    assert snap["ticks"] == 2, snap
    assert snap["slow_ticks"] == 1, snap
    print("T3 ok")

    # -- T4: should_dump-Episoden (last_tick liegt bei now=27.0) ------------
    assert sh.should_dump(now=27.0 + 130.0) is True     # age 130 -> Freeze erkannt
    assert sh.should_dump(now=27.0 + 130.0) is False    # dieselbe Episode
    assert sh.should_dump(now=27.0 + 30.0) is False     # age 30 -> Erholung, kein Freeze
    assert sh.should_dump(now=27.0 + 130.0) is True     # neue Episode
    print("T4 ok")

    # -- T5: in_tick_s waechst mit now, solange kein tick_end kam -----------
    sh2 = SchedulerHealth()
    sh2.tick_start("phase-c", now=100.0)
    assert sh2.snapshot(now=105.0)["in_tick_s"] == 5.0, sh2.snapshot(now=105.0)
    assert sh2.snapshot(now=140.0)["in_tick_s"] == 40.0, sh2.snapshot(now=140.0)
    print("T5 ok")

    # -- T6: dump_threads schreibt Header, Legende und faulthandler-Teil ----
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sub", "dump.txt")
        dump_threads(path, "Test-Header (Selbsttest)")
        text = open(path, encoding="utf-8").read()
        assert "Test-Header (Selbsttest)" in text, text
        assert "Thread-Legende" in text, text
        assert str(threading.get_ident()) in text, text
    print("T6 ok")

    # -- T7: error() landet im snapshot --------------------------------
    sh3 = SchedulerHealth()
    sh3.error("scheduler_exception", "ValueError('x')")
    snap = sh3.snapshot(now=0.0)
    assert snap["last_error"] is not None, snap
    assert snap["last_error"]["code"] == "scheduler_exception", snap
    assert snap["last_error"]["detail"] == "ValueError('x')", snap
    print("T7 ok")

    # -- S1: tick_start(code, arg) setzt phase und phase_arg ----------------
    sh4 = SchedulerHealth()
    sh4.tick_start("start_dscan", "20260907_2228", now=0.0)
    snap = sh4.snapshot(now=0.0)
    assert snap["phase"] == "start_dscan", snap
    assert snap["phase_arg"] == "20260907_2228", snap
    print("S1 ok")

    # -- S2: tick_start ohne arg setzt phase_arg auf None --------------------
    sh4.tick_start("check", now=1.0)
    snap = sh4.snapshot(now=1.0)
    assert snap["phase"] == "check", snap
    assert snap["phase_arg"] is None, snap
    print("S2 ok")

    # -- S3: error(code, detail) legt code/detail ab, text kommt nicht mehr vor -
    sh5 = SchedulerHealth()
    sh5.error("scheduler_exception", "ValueError('x')")
    snap = sh5.snapshot(now=0.0)
    assert snap["last_error"]["code"] == "scheduler_exception", snap
    assert snap["last_error"]["detail"] == "ValueError('x')", snap
    assert "text" not in snap["last_error"], snap
    print("S3 ok")

    # -- S4: PHASE_CODES ist ASCII und ohne Leerzeichen ----------------------
    assert len(PHASE_CODES) == 6, PHASE_CODES
    for code in PHASE_CODES:
        assert code.isascii(), code
        assert " " not in code, code
    print("S4 ok")

    print("Selbsttest OK")


def main() -> int:
    import argparse

    # cp1252-Konsole: reconfigure vermeidet UnicodeEncodeError bei
    # Diakritika in Fehlertexten (Muster known_freq.py).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(description="Scheduler-Diagnostik (Herzschlag/Thread-Dump)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
