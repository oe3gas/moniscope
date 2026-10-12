"""
eb200_core/signal_detect.py - Signaldetektion auf fertigen D-SCAN-Sweeps (T-505).

Zweck: "detect"-Schritt der Monitoring-Kaskade (scan -> detect -> ...). Liefert je
fertigem Sweep die Liste NEU erkannter Signale; der Wasserfall (:8000) brennt sie
als Auto-Marker ein. Nur stdlib + numpy (eb200_core-Regel), keine Geraetezugriffe.

Verfahren (Entscheidungen 2026-08-30, OE3GAS):
  1. Rauschflur-Schaetzung in zwei Stufen:
     a) Zeit-Median je Bin ueber die letzten `history` Sweeps (Ringpuffer),
     b) darauf Frequenz-Median in Bloecken zu `block_bins` Bins mit linearer
        Interpolation zwischen den Blockmitten. Ergebnis: ein Flur je Bin, der
        einem schraegen Rauschflur folgt, schmale Traeger aber NICHT aufnimmt -
        dadurch werden auch Dauertraeger genau einmal erkannt.
  2. Schwelle = Flur + delta_db. Zusammenhaengende Bins ueber der Schwelle bilden
     ein Signal; seine Frequenz ist das Peak-Bin des Laufs.
  3. Ein Signal wird GENAU EINMAL gemeldet: Merkliste "schon markiert" je Bin,
     Toleranz +-tol_bins. Reset der Merkliste bei Bereichs-/Schrittwechsel, per
     clear_marked() (Wasserfall "Loeschen" / Seitenaufbau).
  4. Schutz gegen Pegelspruenge (Attenuator, Antennenwechsel): liegen mehr als
     max_active_frac der Bins ueber der Schwelle, wird der Sweep uebersprungen
     (kein Marker-Regen), der Ringpuffer laeuft trotzdem weiter.
  5. Aufwaermphase: erst ab `warmup` gepufferten Sweeps werden Ereignisse geliefert.

Threading: process() laeuft im Controller-Thread, configure()/clear_marked()/status()
aus HTTP-Threads - alle Zustandszugriffe unter einem Lock.
"""
from __future__ import annotations

import sys
import threading
from collections import deque
from typing import Optional

import numpy as np

# T-505f: Pegel ab dieser Grenze sind keine Messwerte, sondern Geraete-Marker/
# Fuellwerte (beobachtet: Rohwert 2000 = 200.0 dBuV im letzten Bin des D-SCAN-
# Bereichs; gleiches Muster wie der MSCAN-Zyklusende-Marker, siehe
# eb200_core/mscan_run.py MARKER_RAW_LEVEL_MIN). Solche Bins nehmen nie an
# der Detektion teil - weder als Signal noch als Schwellenkandidat.
LEVEL_VALID_MAX_DBUV = 150.0


class SignalDetector:
    def __init__(self, delta_db: float = 8.0, history: int = 20, warmup: int = 3,
                 tol_bins: int = 1, block_bins: int = 64,
                 max_active_frac: float = 0.3, enabled: bool = True,
                 auto_delta: bool = True, min_sweeps: int = 3) -> None:
        self._lock = threading.Lock()
        self.delta_db = float(delta_db)
        self.history = max(1, int(history))
        self.warmup = max(1, int(warmup))
        self.tol_bins = max(0, int(tol_bins))
        self.block_bins = max(1, int(block_bins))
        self.max_active_frac = float(max_active_frac)
        self.enabled = bool(enabled)
        self._params: Optional[tuple[int, int, int, int]] = None  # (start, stop, step, total)
        self._ring: deque = deque(maxlen=self.history)
        self._marked: Optional[np.ndarray] = None   # bool je Bin: schon markiert
        self._events: list[dict] = []               # alle Ereignisse seit letztem Reset/Clear
        self.sweeps_seen = 0
        self.skipped_jumps = 0
        # T-505d: automatische Delta-Bestimmung aus den ersten `warmup` Sweeps
        # (siehe suggest_delta). _delta_pending erzwingt eine Neuberechnung beim
        # naechsten Sweep (Einschalten zur Laufzeit ueber configure()).
        self.auto_delta = bool(auto_delta)
        self._delta_pending = False
        # T-505e: Mindestdauer - ein Signal wird erst gemeldet, wenn es in
        # min_sweeps AUFEINANDERFOLGENDEN Sweeps ueber der Schwelle liegt (1 = sofort).
        # _streak zaehlt je Bin die fortlaufenden Sweeps ueber der Schwelle,
        # _t0 merkt je Bin den Zeitpunkt des Einsetzens (Streak 0 -> 1).
        self.min_sweeps = max(1, int(min_sweeps))
        self._streak: Optional[np.ndarray] = None
        self._t0: Optional[np.ndarray] = None

    # -- Verwaltung (HTTP-Threads) --------------------------------------------

    def configure(self, *, enabled: Optional[bool] = None,
                  delta_db: Optional[float] = None,
                  tol_bins: Optional[int] = None,
                  auto_delta: Optional[bool] = None,
                  min_sweeps: Optional[int] = None) -> None:
        with self._lock:
            if enabled is not None:
                self.enabled = bool(enabled)
            if delta_db is not None:
                self.delta_db = float(delta_db)
            if tol_bins is not None:
                self.tol_bins = max(0, int(tol_bins))
            if auto_delta is not None:
                was_on = self.auto_delta
                self.auto_delta = bool(auto_delta)
                if self.auto_delta and not was_on:
                    self._delta_pending = True
            if min_sweeps is not None:
                self.min_sweeps = max(1, int(min_sweeps))

    def clear_marked(self) -> None:
        """Merkliste leeren - jedes Signal darf danach erneut (einmal) gemeldet werden."""
        with self._lock:
            if self._marked is not None:
                self._marked[:] = False
            self._events = []

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "delta_db": self.delta_db,
                "tol_bins": self.tol_bins,
                "marked": int(self._marked.sum()) if self._marked is not None else 0,
                "sweeps_seen": self.sweeps_seen,
                "skipped_jumps": self.skipped_jumps,
                "auto_delta": self.auto_delta,
                "min_sweeps": self.min_sweeps,
            }

    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    # -- Kern ----------------------------------------------------------------

    def _reset_locked(self, params: tuple[int, int, int, int]) -> None:
        self._params = params
        self._ring = deque(maxlen=self.history)
        self._marked = np.zeros(params[3], dtype=bool)
        self._streak = np.zeros(params[3], dtype=int)
        self._t0 = np.zeros(params[3], dtype=float)
        self._events = []
        self.sweeps_seen = 0

    @staticmethod
    def estimate_floor(tmed: np.ndarray, block_bins: int) -> np.ndarray:
        """Stufe 1b: Block-Median ueber die Frequenz + lineare Interpolation."""
        total = int(tmed.shape[0])
        nblocks = max(1, total // block_bins)
        centers: list[float] = []
        meds: list[float] = []
        pos = 0
        for block in np.array_split(tmed, nblocks):
            centers.append(pos + (len(block) - 1) / 2.0)
            meds.append(float(np.median(block)))
            pos += len(block)
        if nblocks == 1:
            return np.full(total, meds[0])
        return np.interp(np.arange(total), centers, meds)

    @staticmethod
    def suggest_delta(sweeps: np.ndarray, floor: np.ndarray) -> float:
        """T-505d: Delta aus der Rauschstreuung der gepufferten Sweeps.
        Residuum r = Sweep - Flur; robuste Streuung sigma = 1.4826 * MAD(r) (Median-
        basiert, daher unempfindlich gegen Signale/Traeger im Puffer). Die Schwelle
        muss das MAXIMUM des Rauschens ueber alle N Bins eines Sweeps ueberragen; fuer
        Gauss-Rauschen liegt das bei etwa sigma*sqrt(2 ln N). Plus 2 dB Reserve, auf
        ganze dB gerundet, begrenzt auf 3..30 (Reglerbereich im Frontend)."""
        r = sweeps - floor
        mad = float(np.median(np.abs(r - np.median(r))))
        sigma = 1.4826 * mad
        n_bins = int(floor.shape[0])
        z = float(np.sqrt(2.0 * np.log(max(2, n_bins))))
        return float(min(30.0, max(3.0, round(z * sigma + 2.0))))

    def process(self, levels, start_hz: int, stop_hz: int, step_hz: int, t: float) -> list[dict]:
        """Einen FERTIGEN Sweep einspeisen; liefert die Liste NEU erkannter Signale.
        Ereignis: {"bin", "f_hz", "level", "floor", "t"}."""
        arr = np.asarray(levels, dtype=float)
        total = int(arr.shape[0])
        if total == 0:
            return []
        params = (int(start_hz), int(stop_hz), int(step_hz), total)
        with self._lock:
            if params != self._params:
                self._reset_locked(params)
            self._ring.append(arr)
            self.sweeps_seen += 1
            if len(self._ring) < self.warmup or not self.enabled:
                return []

            tmed = np.median(np.stack(list(self._ring)), axis=0)
            floor = self.estimate_floor(tmed, self.block_bins)
            # T-505d: einmal am Ende der Aufwaermphase (Start/Bereichswechsel) bzw.
            # nach Einschalten zur Laufzeit; danach bleibt das Delta stehen.
            if self.auto_delta and (len(self._ring) == self.warmup or self._delta_pending):
                self.delta_db = self.suggest_delta(np.stack(list(self._ring)), floor)
                self._delta_pending = False
            above = (arr >= (floor + self.delta_db)) & (arr < LEVEL_VALID_MAX_DBUV)   # T-505f
            if float(above.mean()) > self.max_active_frac:
                self.skipped_jumps += 1
                self._streak[:] = 0   # T-505e: Dauerzaehlung nach Pegelsprung neu beginnen
                return []

            # T-505e: fortlaufende Sweeps ueber der Schwelle je Bin; Einsetz-Zeit merken.
            self._streak = np.where(above, self._streak + 1, 0)
            self._t0 = np.where(self._streak == 1, float(t), self._t0)
            idx = np.flatnonzero(above)
            if idx.size == 0:
                return []
            # Laeufe zusammenhaengender Bins: Bruchstellen dort, wo idx springt.
            breaks = np.flatnonzero(np.diff(idx) > 1)
            starts = np.concatenate(([idx[0]], idx[breaks + 1]))
            ends = np.concatenate((idx[breaks], [idx[-1]]))

            out: list[dict] = []
            tol = self.tol_bins
            for a, b in zip(starts, ends):
                a = int(a)
                b = int(b)
                peak = a + int(np.argmax(arr[a:b + 1]))
                lo = max(0, peak - tol)
                hi = min(total, peak + tol + 1)
                if self._marked[lo:hi].any():
                    continue
                # T-505e: hoechster Streak im Lauf (+-tol, damit ein leicht wanderndes
                # Peak-Bin die Zaehlung nicht unterbricht) muss min_sweeps erreichen.
                slo = max(0, a - tol)
                shi = min(total, b + tol + 1)
                s_idx = slo + int(np.argmax(self._streak[slo:shi]))
                streak = int(self._streak[s_idx])
                if streak < self.min_sweeps:
                    continue
                self._marked[peak] = True
                ev = {
                    "bin": peak,
                    "f_hz": params[0] + peak * params[2],
                    "level": round(float(arr[peak]), 1),
                    "floor": round(float(floor[peak]), 1),
                    "t": float(self._t0[s_idx]),   # T-505e: Zeit des Einsetzens (Onset)
                    "t_confirm": float(t),          # Zeit des bestaetigenden Sweeps
                    "streak": streak,               # Sweeps seit Onset inkl. diesem
                }
                self._events.append(ev)
                out.append(ev)
            return out


# ---------------------------------------------------------------------------
# Selbsttest (ohne Geraet): python eb200_core/signal_detect.py --selftest
# ---------------------------------------------------------------------------

def _selftest() -> None:
    rng = np.random.RandomState(1)
    total = 400
    start_hz, step_hz = 118_000_000, 25_000
    base = 5.0 + np.linspace(0.0, 4.0, total)   # schraeger Rauschflur 5..9 dBuV

    def sweep(extra=None, offset=0.0):
        s = base + rng.normal(0.0, 1.5, total) + offset
        if extra:
            for b, db in extra.items():
                s[b] += db
        return s.tolist()

    det = SignalDetector(delta_db=8.0, warmup=3, tol_bins=1, auto_delta=False, min_sweeps=1)
    t = 0.0

    def feed(extra=None, offset=0.0, start=start_hz):
        nonlocal t
        t += 1.0
        stop = start + (total - 1) * step_hz
        return det.process(sweep(extra, offset), start, stop, step_hz, t)

    def bins(ev):
        return [e["bin"] for e in ev]

    carrier = {50: 25.0}
    # Sweeps 1-2: Aufwaermphase -> keine Ereignisse
    assert feed(carrier) == []
    assert feed(carrier) == []
    # Sweep 3: Dauertraeger Bin 50 wird genau einmal erkannt
    ev = feed(carrier)
    assert bins(ev) == [50], ev
    assert ev[0]["f_hz"] == start_hz + 50 * step_hz
    assert ev[0]["level"] >= ev[0]["floor"] + 8.0, ev
    print("OK  Dauertraeger einmalig erkannt (Bin 50)")
    # Sweeps 4-6: Burst Bin 200 -> nur beim Einsetzen ein Ereignis
    assert bins(feed({**carrier, 200: 20.0})) == [200]
    assert feed({**carrier, 200: 20.0}) == []
    assert feed({**carrier, 200: 20.0}) == []
    # Sweeps 7-9: Pause
    for _ in range(3):
        assert feed(carrier) == []
    # Sweep 10: derselbe Burst erneut -> KEIN neuer Marker (einmal je Frequenz)
    assert feed({**carrier, 200: 20.0}) == []
    # Sweep 11: Burst 1 Bin daneben -> innerhalb Toleranz, kein Marker
    assert feed({**carrier, 201: 20.0}) == []
    # Sweep 12: Burst 3 Bins daneben -> neuer Marker
    assert bins(feed({**carrier, 203: 20.0})) == [203]
    print("OK  Burst: Einsetzen, Wiederholung, Toleranz +-1 Bin")
    # Sweep 13: Pegelsprung (alle Bins +25 dB) -> uebersprungen
    assert feed(carrier, offset=25.0) == []
    assert det.status()["skipped_jumps"] == 1
    # Sweep 14: wieder normal -> keine Fehlerkennung
    assert feed(carrier) == []
    assert det.status()["marked"] == 3
    print("OK  Pegelsprung uebersprungen, kein Marker-Regen")
    # Deaktiviert: Burst Bin 300 wird nicht markiert, nach Aktivieren schon
    det.configure(enabled=False)
    assert feed({**carrier, 300: 20.0}) == []
    det.configure(enabled=True)
    assert bins(feed({**carrier, 300: 20.0})) == [300]
    # clear_marked: Dauertraeger sofort wieder (einmal) markiert
    det.clear_marked()
    assert det.status()["marked"] == 0
    assert bins(feed(carrier)) == [50]
    print("OK  enabled-Schalter und clear_marked")
    # Bereichswechsel -> Reset + Aufwaermphase, dann Dauertraeger erneut
    new_start = 130_000_000
    assert feed(carrier, start=new_start) == []
    assert feed(carrier, start=new_start) == []
    ev = feed(carrier, start=new_start)
    assert bins(ev) == [50] and ev[0]["f_hz"] == new_start + 50 * step_hz, ev
    # Delta 30 dB: 20-dB-Burst bleibt unter der Schwelle
    det.configure(delta_db=30.0)
    assert feed({**carrier, 100: 20.0}, start=new_start) == []
    print("OK  Bereichswechsel-Reset und Delta-Aenderung")
    # T-505d: automatische Delta-Bestimmung. sigma 1.5 -> z(400)=3.46 -> ~7 dB,
    # sigma 3.5 -> ~14 dB; Traeger im Puffer darf die Schaetzung nicht verzerren.
    stop_hz = start_hz + (total - 1) * step_hz
    for sigma, lo_hi in ((1.5, (5.0, 10.0)), (3.5, (12.0, 18.0))):
        det2 = SignalDetector(delta_db=8.0, warmup=3)
        for _ in range(3):
            s = base + rng.normal(0.0, sigma, total) + np.where(np.arange(total) == 50, 25.0, 0.0)
            det2.process(s.tolist(), start_hz, stop_hz, step_hz, 0.0)
        d = det2.status()["delta_db"]
        assert lo_hi[0] <= d <= lo_hi[1], (sigma, d)
        assert d == round(d), d
    # manuell gesetztes Delta bleibt bei auto_delta=False stehen
    det2.configure(auto_delta=False, delta_db=12.0)
    det2.process(sweep(carrier), start_hz, stop_hz, step_hz, 0.0)
    assert det2.status()["delta_db"] == 12.0
    # Einschalten zur Laufzeit -> Neuberechnung beim naechsten Sweep
    det2.configure(auto_delta=True)
    det2.process(sweep(carrier), start_hz, stop_hz, step_hz, 0.0)
    assert det2.status()["auto_delta"] is True
    assert 5.0 <= det2.status()["delta_db"] <= 18.0, det2.status()
    print("OK  Auto-Delta aus Rauschstreuung (MAD), manuell/auto-Umschaltung")
    # T-505e: Mindestdauer 3 Sweeps. Streak zaehlt erst nach der Aufwaermphase.
    det3 = SignalDetector(delta_db=8.0, warmup=3, tol_bins=1, auto_delta=False, min_sweeps=3)
    t3 = 100.0

    def feed3(extra=None):
        nonlocal t3
        t3 += 1.0
        return det3.process(sweep(extra), start_hz, stop_hz, step_hz, t3)

    assert feed3(carrier) == []            # Sweep 1 (Aufwaermphase)
    assert feed3(carrier) == []            # Sweep 2 (Aufwaermphase)
    assert feed3(carrier) == []            # Sweep 3: Streak 1
    assert feed3(carrier) == []            # Sweep 4: Streak 2
    ev = feed3(carrier)                    # Sweep 5: Streak 3 -> Dauertraeger gemeldet
    assert bins(ev) == [50] and ev[0]["streak"] == 3, ev
    assert ev[0]["t"] == t3 - 2.0 and ev[0]["t_confirm"] == t3, ev   # Onset = Sweep 3
    # Einzel-Impuls (1 Sweep) und 2-Sweep-Impuls werden NICHT gemeldet
    assert feed3({**carrier, 300: 25.0}) == []
    assert feed3(carrier) == []
    assert feed3({**carrier, 310: 25.0}) == []
    assert feed3({**carrier, 310: 25.0}) == []
    assert feed3(carrier) == []
    # 3-Sweep-Signal wird beim dritten Sweep gemeldet, Onset = erster Sweep
    assert feed3({**carrier, 320: 25.0}) == []
    assert feed3({**carrier, 320: 25.0}) == []
    ev = feed3({**carrier, 320: 25.0})
    assert bins(ev) == [320] and ev[0]["t"] == t3 - 2.0, ev
    assert det3.status()["marked"] == 2
    # Mindestdauer 1 zur Laufzeit -> sofortige Meldung
    det3.configure(min_sweeps=1)
    assert bins(feed3({**carrier, 330: 25.0})) == [330]
    print("OK  Mindestdauer (Streak), Onset-Zeit, Impulsunterdrueckung")
    # T-505f: Geraete-Fuellwert (hier +300 dB im letzten Bin, jeden Sweep) darf nie
    # markiert werden; ein echtes Signal daneben bleibt detektierbar.
    det4 = SignalDetector(delta_db=8.0, warmup=3, tol_bins=1, auto_delta=False, min_sweeps=1)
    t4 = 200.0
    for i in range(5):
        t4 += 1.0
        extra = {total - 1: 300.0}
        if i == 4:
            extra[220] = 25.0
        ev = det4.process(sweep(extra), start_hz, stop_hz, step_hz, t4)
        if i < 4:
            assert ev == [], ev
        else:
            assert bins(ev) == [220], ev
    assert all(e["bin"] != total - 1 for e in det4.events())
    print("OK  Ungueltige Pegel (>= 150 dBuV) werden ignoriert")
    print("\nSelbsttest SignalDetector erfolgreich abgeschlossen.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("Verwendung: python eb200_core/signal_detect.py --selftest")
