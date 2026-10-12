#!/usr/bin/env python3
"""
eb200_core/framing.py

Asynchrone Rahmenerkennung auf dem Halbbit-Raster (Meilenstein 17, T-1715).
Reine Vergleichsfunktion wie eb200_core/mode_match.py: kein numpy, kein Datei-,
Netz- oder Zeitzugriff, kein Zustand - deshalb ohne Signalverarbeitung, allein
mit Bitlisten von Hand pruefbar (siehe --selftest).

Die eine Invariante asynchroner Uebertragung ist der Massstab: Startbit ist
immer 0 (Space), Stoppbit immer 1 (Mark). Bei falscher Rahmenlaenge oder
falscher Phase trifft das nur zufaellig zu. detect_async_framing() sucht ueber
Kandidatenlaengen, Phasen und Polaritaet nach der Rahmenlaenge, bei der diese
Bedingung am haeufigsten zutrifft, und lehnt ab statt zu raten, wenn kein
Kandidat deutlich heraussticht.

Kein stiller Ruckfall auf den besten Kandidaten: eine behauptete
Rahmenstruktur, die nicht traegt, ist schlimmer als keine - sie stuende in der
Anzeige neben den Modenkandidaten und beschaedigte deren Verlaesslichkeit mit.
"""
from __future__ import annotations

# Kandidatenlaengen in Halbbit und ihre Deutung (PLAN.md T-1715). Laenge 20
# traegt zwei Deutungen - die Messung kann sie nicht trennen (dieselbe Regel
# wie bei den Modenkandidaten, eb200_core/mode_match.py).
CANDIDATE_LENGTHS_HALFBITS = (14, 15, 16, 18, 20, 22)

_INTERPRETATIONEN = {
    14: ["5 Datenbits + 1 Stoppbit (ITA2)"],
    15: ["5 Datenbits + 1,5 Stoppbits (ITA2)"],
    16: ["5 Datenbits + 2 Stoppbits (ITA2)"],
    18: ["7 Datenbits + 1 Stoppbit (ASCII ohne Paritaet)"],
    20: ["7 Datenbits + Paritaet + 1 Stoppbit (ASCII)",
         "8 Datenbits + 1 Stoppbit (8N1)"],
    22: ["8 Datenbits + Paritaet + 1 Stoppbit"],
}

# Schwellen gemessen (nicht gesetzt), Methode wie bei BAUD_FINE_R2_MIN in
# tools/eye_diagram.py. Score = Anteil der Rahmen mit b[phi]==0, b[phi+1]==0
# und b[phi+P-1]==1; Margin = Abstand des besten Scores zur besten ANDEREN
# Rahmenlaenge.
#
# Obergrenze fuer Zufall (200 Versuche je Punkt, gleichverteilte Zufallsbits,
# Laenge = n_frames * 15 Halbbit als Skalierungsbasis):
#   n_frames=20:  Score bis 0,583   Margin bis 0,226
#   n_frames=50:  Score bis 0,364   Margin bis 0,089
#   n_frames=100: Score bis 0,324   Margin bis 0,100
#   n_frames=200: Score bis 0,267   Margin bis 0,061
# Beide Kennzahlen fallen mit wachsendem n_frames monoton - der schaerfste
# Fall ist also der kleinste noch zugelassene n_frames-Wert.
#
# Untergrenze fuer echte Signale (_make_async_frames-Fixtur, 200 Rahmen,
# 20 Seeds je Fall), sauber: alle vier Kandidatenlaengen (ITA2 1,0/1,5/2,0
# Stoppbits, ASCII 7N1) erreichen Score 1,000, Margin mindestens 0,470.
# Mit Bitfehlern (1%/2%/5% zufaellig gekippt, 1,5-Stoppbit-Fall):
#   1%: Score min 0,940  Margin min 0,472
#   2%: Score min 0,910  Margin min 0,438
#   5%: Score min 0,810  Margin min 0,383
# Direkt am geplanten Mindestwert n_frames=50 (5% Bitfehler, 30 Seeds):
#   Score min 0,740  Margin min 0,272 - deutlich ueber der Zufalls-Obergrenze
#   bei n_frames=50 (Score 0,364 / Margin 0,089).
#
# FRAMING_MIN_FRAMES=50: kleinster in der Zufallsmessreihe erfasster Punkt,
# bei dem echte (verrauschte) Signale die Zufalls-Obergrenze bereits klar
# ueberragen. Bei 10 Rahmen (Regressionsfixtur, siehe --selftest) greift
# folgerichtig allein die Mindestzahl, nicht Score oder Margin.
# FRAMING_SCORE_MIN=0,55: mittig zwischen der Zufalls-Obergrenze (0,364) und
# der verrauschten Untergrenze (0,740) bei n_frames=50.
# FRAMING_MARGIN_MIN=0,18: mittig zwischen 0,089 und 0,272, denselben Punkt.
FRAMING_MIN_FRAMES = 50
FRAMING_SCORE_MIN = 0.55
FRAMING_MARGIN_MIN = 0.18


def _score_for_phase(bits: list[int], P: int, phi: int) -> tuple[float, int]:
    """Anteil der vollstaendigen P-Fenster ab Phase phi, die die Start/Stopp-
    Bedingung erfuellen, plus die Zahl der geprueften Fenster (= Rahmen)."""
    n = len(bits)
    n_frames = 0
    hits = 0
    idx = phi
    while idx + P - 1 < n:
        if bits[idx] == 0 and bits[idx + 1] == 0 and bits[idx + P - 1] == 1:
            hits += 1
        n_frames += 1
        idx += P
    if n_frames == 0:
        return 0.0, 0
    return hits / n_frames, n_frames


def _best_for_length(bits_normal: list[int], bits_inverted: list[int], P: int) -> dict:
    best = None
    for polarity, bits in (("normal", bits_normal), ("inverted", bits_inverted)):
        for phi in range(P):
            score, n_frames = _score_for_phase(bits, P, phi)
            if best is None or score > best["score"]:
                best = {"frame_halfbits": P, "polarity": polarity,
                        "phase_halfbits": phi, "score": score, "n_frames": n_frames}
    return best


def detect_async_framing(bits_half: list[int], min_frames: int = FRAMING_MIN_FRAMES) -> dict:
    """bits_half sind harte Bitentscheidungen auf dem HALBBIT-Raster (zwei
    Eintraege je Bit, Werte 0 oder 1). Sucht ueber CANDIDATE_LENGTHS_HALFBITS,
    alle Phasen und beide Polaritaeten nach der Rahmenlaenge mit dem hoechsten
    Start/Stopp-Trefferanteil.

    accepted=True nur, wenn n_frames>=min_frames, score>=FRAMING_SCORE_MIN und
    margin (Abstand zur besten ANDEREN Rahmenlaenge) >=FRAMING_MARGIN_MIN.
    Sonst accepted=False, alle Befundfelder None, reject_reason einer von
    "zu_wenige_rahmen", "score_zu_niedrig", "kein_eindeutiger_kandidat".

    scores traegt IMMER den besten Treffer je Kandidatenlaenge, auch bei
    Ablehnung - Material fuer die Fehlersuche, keine Anzeige."""
    bits_normal = [int(b) for b in bits_half]
    bits_inverted = [1 - b for b in bits_normal]

    scores = [_best_for_length(bits_normal, bits_inverted, P) for P in CANDIDATE_LENGTHS_HALFBITS]

    ranked = sorted(scores, key=lambda s: s["score"], reverse=True)
    best = ranked[0]
    runner_up = next(s for s in ranked[1:] if s["frame_halfbits"] != best["frame_halfbits"])
    margin = best["score"] - runner_up["score"]

    if best["n_frames"] < min_frames:
        reject_reason = "zu_wenige_rahmen"
    elif best["score"] < FRAMING_SCORE_MIN:
        reject_reason = "score_zu_niedrig"
    elif margin < FRAMING_MARGIN_MIN:
        reject_reason = "kein_eindeutiger_kandidat"
    else:
        reject_reason = None

    if reject_reason is None:
        return {
            "frame_halfbits": best["frame_halfbits"],
            "frame_bits": best["frame_halfbits"] / 2.0,
            "interpretations": list(_INTERPRETATIONEN[best["frame_halfbits"]]),
            "polarity": best["polarity"],
            "phase_halfbits": best["phase_halfbits"],
            "score": best["score"],
            "margin": margin,
            "n_frames": best["n_frames"],
            "accepted": True,
            "reject_reason": None,
            "scores": scores,
        }
    return {
        "frame_halfbits": None,
        "frame_bits": None,
        "interpretations": [],
        "polarity": None,
        "phase_halfbits": None,
        "score": None,
        "margin": None,
        "n_frames": best["n_frames"],
        "accepted": False,
        "reject_reason": reject_reason,
        "scores": scores,
    }


# ITA2-Bezugscodes in Sendereihenfolge (T-1716). LTRS ist zugleich das
# Leerlaufzeichen (Dauer-LTRS/Diddle in Sendepausen). Alle sechs Anteile
# werden berichtet; als KRITERIUM fuer ita2_hint dienen seit T-1716a nur
# Space und der Anteil des haeufigsten Codes - LTRS und FIGS sind dafuer zu
# selten (Begruendung an den Schwellen unten).
_ITA2_REFERENZCODES = ("11111", "11011", "00100", "00010", "01000", "00000")

# Schwellen gemessen (nicht gesetzt), Methode wie FRAMING_SCORE_MIN oben.
# T-1716a-Nachmessung: FIGS/LTRS fielen als Kriterium weg (Befund unten),
# ita2_hint stuetzt sich seither auf Space-Anteil und den Anteil des
# HAEUFIGSTEN Codes (top_codes[0]["share"]). Beide Anteile bleiben in
# code_shares/top_codes erhalten und werden weiter berichtet.
#
# Ursprung des Nachtrags: die T-1716-Messreihe wies fuer FIGS eine
# UEBERLAPPUNG statt einer Luecke aus (Zufall bis 0,067..0,08 je nach n,
# echte Fixture min/p25/median 0,057/0,073/0,083) - eine Schwelle "mittig
# in der Luecke" war dort nicht konstruierbar. Ursache: FIGS tritt nur bei
# Ziffern auf und ist strukturell zu selten. Die Erprobung an einer echten
# Aufnahme (20260912_202830_7646kHz) bestaetigte es: 6,3 % FIGS-Anteil
# verfehlte die alte Schwelle 0,07, obwohl Space (34,7 %) und CR:LF (4:2)
# eindeutig fuer ITA2 sprachen.
#
# Entropie der Verteilung wurde erwogen und verworfen: sie waere das
# elegantere Mass, haengt bei kleinem n_chars aber stark vom
# Stichprobenumfang ab (bei 95 Zeichen auf 32 Codes liegt schon Zufall
# deutlich unter dem theoretischen Hoechstwert). Der Anteil des
# haeufigsten Codes traegt dieselbe Aussage ohne diese Abhaengigkeit.
#
# Messmethode wie zuvor: Score = Anteil von Zeichen-Code X (bzw. des
# haeufigsten Codes) unter allen gueltigen Zeichen, bei n_chars in
# {50, 100, 200, 300}.
#
# Zufalls-Obergrenze (500 Versuche je Punkt, gleichverteilte 5-Bit-Codes,
# echte Rahmen aus _make_frame_bits):
#   n_chars= 50: top1 bis 0,180  Space bis 0,120
#   n_chars=100: top1 bis 0,110  Space bis 0,100
#   n_chars=200: top1 bis 0,085  Space bis 0,070
#   n_chars=300: top1 bis 0,077  Space bis 0,063
# Top1 und Space fallen beide monoton mit wachsendem n_chars.
#
# Untergrenze echter Text, ZWEI Verkehrsarten (_make_ita2_text_bits, je
# 400 Seeds), Zahlengruppen-Anteil ist jetzt Parameter (T-1716a):
#   gemischt (30 % Zahlengruppen, realistischer Mischverkehr):
#     n_chars= 50: top1 min 0,100  Space min 0,020
#     n_chars=100: top1 min 0,120  Space min 0,030
#     n_chars=200: top1 min 0,120  Space min 0,060
#     n_chars=300: top1 min 0,130  Space min 0,077
#   numerisch dicht (65 % Zahlengruppen, Bestandsfixtur aus T-1716):
#     n_chars= 50: top1 min 0,140  Space min 0,000
#     n_chars=100: top1 min 0,150  Space min 0,020
#     n_chars=200: top1 min 0,145  Space min 0,070
#     n_chars=300: top1 min 0,150  Space min 0,073
#
# Trennung je n_chars (Zufalls-Obergrenze gegen die SCHLECHTERE der beiden
# Verkehrsarten, da die Schwelle fuer beide gelten muss):
#   n_chars= 50: top1 UEBERLAPPT (Zufall 0,180 > Text 0,100); Space UEBERLAPPT (0,120 > 0,020)
#   n_chars=100: top1 getrennt (0,110 < 0,120);               Space UEBERLAPPT (0,100 > 0,020)
#   n_chars=200: top1 getrennt (0,085 < 0,120);                Space UEBERLAPPT (0,070 > 0,060)
#   n_chars=300: top1 getrennt (0,077 < 0,130);                Space getrennt (0,063 < 0,073)
# Erst bei n_chars=300 trennen sich BEIDE Masse gleichzeitig - das ist der
# kleinste der vier gemessenen Punkte mit einer echten Luecke bei beiden
# Kriterien, also ITA2_MIN_CHARS=300.
#
# ITA2_SPACE_MIN=0,068 mittig zwischen Zufalls-Obergrenze (0,063 bei n=300)
# und Text-Untergrenze (0,073 bei n=300, numerisch dicht - der engere der
# beiden Werte). ITA2_TOP1_MIN=0,104 mittig zwischen Zufalls-Obergrenze
# (0,077 bei n=300) und Text-Untergrenze (0,130 bei n=300, gemischt - der
# engere der beiden Werte).
#
# Folge: bei n_chars<300 bleibt ita2_hint IMMER False, auch bei sonst
# eindeutigem Text (siehe Erprobung an der 95-Zeichen-Aufnahme oben) - ein
# zu haeufiges Anschlagen auf Zufall waere der schlechtere Fehler (siehe
# Leitsatz des Meilensteins). Das ist derselbe Kompromiss wie zuvor, nur
# jetzt anhand tragfaehiger statt ueberlappender Kriterien.
ITA2_MIN_CHARS = 300
ITA2_SPACE_MIN = 0.068
ITA2_TOP1_MIN = 0.104


def character_stats(bits_half: list[int], framing: dict) -> dict:
    """Zeichenvorrat der gueltigen Rahmen (T-1716) - nur bei 5 Datenbits
    (frame_halfbits 14/15/16) zustaendig, siehe Moduldoku. Durchgefallene
    Rahmen (Start/Stopp-Bedingung nicht erfuellt) liefern Muell und gehen
    nicht in code_shares/top_codes/halfbit_mismatch_share ein - nur in
    fail_share/fail_run_max/fail_share_in_runs."""
    frame_halfbits = framing.get("frame_halfbits")
    leer = {
        "n_frames_total": 0,
        "n_chars": 0,
        "fail_share": None,
        "fail_run_max": 0,
        "fail_share_in_runs": None,
        "code_shares": {code: None for code in _ITA2_REFERENZCODES},
        "top_codes": [],
        "halfbit_mismatch_share": None,
        "ita2_hint": False,
    }
    if not framing.get("accepted") or frame_halfbits not in (14, 15, 16):
        return leer

    bits = [int(b) for b in bits_half]
    if framing["polarity"] == "inverted":
        bits = [1 - b for b in bits]

    phase = framing["phase_halfbits"]
    n = len(bits)
    fail_flags: list[bool] = []
    code_counts: dict[str, int] = {}
    mismatches = 0
    idx = phase
    while idx + frame_halfbits - 1 < n:
        window = bits[idx: idx + frame_halfbits]
        ok = window[0] == 0 and window[1] == 0 and window[-1] == 1
        fail_flags.append(not ok)
        if ok:
            code_bits = []
            for i in range(5):
                off = 2 + 2 * i
                b0, b1 = window[off], window[off + 1]
                if b0 != b1:
                    mismatches += 1
                code_bits.append(str(b0))
            code = "".join(code_bits)
            code_counts[code] = code_counts.get(code, 0) + 1
        idx += frame_halfbits

    n_frames_total = len(fail_flags)
    if n_frames_total == 0:
        return leer
    n_fail = sum(fail_flags)
    n_chars = n_frames_total - n_fail
    fail_share = n_fail / n_frames_total

    max_run = 0
    run = 0
    runs = []
    for failed in fail_flags:
        if failed:
            run += 1
            max_run = max(max_run, run)
        else:
            if run:
                runs.append(run)
            run = 0
    if run:
        runs.append(run)
    fail_in_runs = sum(r for r in runs if r >= 3)
    fail_share_in_runs = (fail_in_runs / n_fail) if n_fail else 0.0

    code_shares = {code: (code_counts.get(code, 0) / n_chars if n_chars else 0.0)
                   for code in _ITA2_REFERENZCODES}
    halfbit_mismatch_share = (mismatches / (5 * n_chars)) if n_chars else 0.0

    ranked = sorted(code_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top_codes = [{"code": code, "share": count / n_chars} for code, count in ranked[:5]]

    ita2_hint = (n_chars >= ITA2_MIN_CHARS
                 and code_shares["00100"] > ITA2_SPACE_MIN
                 and bool(top_codes) and top_codes[0]["share"] > ITA2_TOP1_MIN)

    return {
        "n_frames_total": n_frames_total,
        "n_chars": n_chars,
        "fail_share": fail_share,
        "fail_run_max": max_run,
        "fail_share_in_runs": fail_share_in_runs,
        "code_shares": code_shares,
        "top_codes": top_codes,
        "halfbit_mismatch_share": halfbit_mismatch_share,
        "ita2_hint": ita2_hint,
    }


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------

def _make_frame_bits(n_frames: int, data_bits: int, stop_bits: float, rng) -> list[int]:
    """Reine Python/stdlib-Fixtur (kein numpy - siehe Moduldoku), Muster wie
    tools/eye_diagram.py._make_async_frames: je Rahmen ein Startbit (0, 0),
    data_bits Zufallsbits (je zwei gleiche Halbbit-Eintraege), dann
    stop_bits Stoppbits (1)."""
    stop_halfbits = int(round(2.0 * stop_bits))
    halfbits: list[int] = []
    for _ in range(n_frames):
        halfbits.extend([0, 0])
        for _ in range(data_bits):
            bit = rng.randint(0, 1)
            halfbits.extend([bit, bit])
        halfbits.extend([1] * stop_halfbits)
    return halfbits


def _flip_bits(bits: list[int], p: float, rng) -> list[int]:
    return [1 - b if rng.random() < p else b for b in bits]


def _make_ita2_text_bits(n_chars_target: int, rng, stop_halfbits: int = 3,
                          zahlen_anteil: float = 0.65) -> list[int]:
    """Halbbit-Bitfolge, die grob wie kommerzieller/Wetter-RTTY-Verkehr
    aussieht (T-1716, Parameter siehe Kommentar an ITA2_SPACE_MIN oben):
    Woerter aus Buchstaben-Platzhaltern (3-8 Zeichen) mit Space dazwischen,
    CR+LF alle 60-72 Zeichen, Leerlaufstrecken aus Dauer-LTRS, und
    Zahlengruppen (2-3 Ziffern-Platzhalter, je eine FIGS- und eine
    LTRS-Umschaltung). Kein echtes ITA2-Alphabet - reine Haeufigkeitsfixtur
    (kein Dekodieren, das gehoert nach iqscan, siehe PLAN.md T-1716).
    `zahlen_anteil` (T-1716a) ist die Wahrscheinlichkeit je Textabschnitt,
    dass eine Zahlengruppe statt eines Wortes folgt - 0,65 (Vorgabe) bildet
    numerisch dichten Verkehr nach, 0,30 einen gemischten."""
    reserved = set(_ITA2_REFERENZCODES)
    letters = [format(i, "05b") for i in range(32) if format(i, "05b") not in reserved]
    codes: list[str] = []
    chars_since_break = rng.randint(0, 60)
    next_break = rng.randint(60, 72)
    while len(codes) < n_chars_target:
        if rng.random() < 0.08:
            for _ in range(rng.randint(10, 30)):
                codes.append("11111")
            chars_since_break = 0
            next_break = rng.randint(60, 72)
            continue
        if chars_since_break >= next_break:
            codes.append("00010")
            codes.append("01000")
            chars_since_break = 0
            next_break = rng.randint(60, 72)
            continue
        if rng.random() < zahlen_anteil:
            codes.append("11011")
            for _ in range(rng.randint(2, 3)):
                codes.append(rng.choice(letters))
                chars_since_break += 1
            codes.append("11111")
            codes.append("00100")
            chars_since_break += 3
            continue
        wortlaenge = rng.randint(3, 8)
        for _ in range(wortlaenge):
            codes.append(rng.choice(letters))
            chars_since_break += 1
        codes.append("00100")
        chars_since_break += 1
    codes = codes[:n_chars_target]

    halfbits: list[int] = []
    for code in codes:
        halfbits.extend([0, 0])
        for bit in code:
            b = int(bit)
            halfbits.extend([b, b])
        halfbits.extend([1] * stop_halfbits)
    return halfbits


def _self_test() -> None:
    import random

    # F1/F2: saubere Fixturen, alle vier Kandidatenlaengen aus der ITA2/ASCII-
    # Tabelle, je 200 Rahmen - eindeutig und mit hohem Abstand erkannt.
    for data_bits, stop_bits, erwartet_P in ((5, 1.0, 14), (5, 1.5, 15), (5, 2.0, 16), (7, 1.0, 18)):
        rng = random.Random(100 + erwartet_P)
        bits = _make_frame_bits(200, data_bits, stop_bits, rng)
        befund = detect_async_framing(bits)
        assert befund["accepted"], (erwartet_P, befund)
        assert befund["frame_halfbits"] == erwartet_P, (erwartet_P, befund["frame_halfbits"])
        assert befund["frame_bits"] == erwartet_P / 2.0
        assert befund["polarity"] == "normal", befund["polarity"]
        assert befund["score"] > 0.99, befund["score"]
        assert befund["margin"] > 0.4, befund["margin"]
        assert befund["reject_reason"] is None
    print("[framing] F1/F2 (saubere Fixturen, alle Kandidatenlaengen) OK")

    # F3: Polaritaet. Dieselbe 1,5-Stoppbit-Folge invertiert -> gleiche
    # Rahmenlaenge, polarity=="inverted" statt "normal".
    rng = random.Random(115)
    bits = _make_frame_bits(200, 5, 1.5, rng)
    bits_inv = [1 - b for b in bits]
    befund = detect_async_framing(bits_inv)
    assert befund["accepted"], befund
    assert befund["frame_halfbits"] == 15, befund
    assert befund["polarity"] == "inverted", befund["polarity"]
    print("[framing] F3 (Polaritaet erkannt) OK")

    # F4: Laenge 20 traegt zwei Deutungen (7+Paritaet+1 ODER 8+1, ununterscheid-
    # bar an der Bitfolge allein) - beide muessen genannt werden.
    rng = random.Random(120)
    bits = _make_frame_bits(200, 8, 1.0, rng)
    befund = detect_async_framing(bits)
    assert befund["accepted"], befund
    assert befund["frame_halfbits"] == 20, befund
    assert len(befund["interpretations"]) == 2, befund["interpretations"]
    assert "7 Datenbits + Paritaet + 1 Stoppbit (ASCII)" in befund["interpretations"]
    assert "8 Datenbits + 1 Stoppbit (8N1)" in befund["interpretations"]
    print("[framing] F4 (Laenge 20, zwei Deutungen) OK")

    # F5: Bitfehler (5%) - Rahmenlaenge bleibt richtig, Score messbar kleiner
    # als im sauberen Fall (F1: >0,99).
    rng = random.Random(125)
    bits = _make_frame_bits(200, 5, 1.5, rng)
    bits_err = _flip_bits(bits, 0.05, rng)
    befund = detect_async_framing(bits_err)
    assert befund["accepted"], befund
    assert befund["frame_halfbits"] == 15, befund
    assert befund["score"] < 0.99, befund["score"]
    assert befund["score"] >= FRAMING_SCORE_MIN, befund["score"]
    print(f"[framing] F5 (5% Bitfehler) score={befund['score']:.3f} OK")

    # F6: Zufallsbits - abgelehnt. Die wichtigste Pruefung der Aufgabe: das
    # Verfahren behauptet auf reinem Rauschen keine Rahmenstruktur.
    rng = random.Random(130)
    bits = [rng.randint(0, 1) for _ in range(200 * 15)]
    befund = detect_async_framing(bits)
    assert befund["accepted"] is False, befund
    assert befund["reject_reason"] in ("score_zu_niedrig", "kein_eindeutiger_kandidat"), befund["reject_reason"]
    assert befund["frame_halfbits"] is None
    assert befund["frame_bits"] is None
    assert befund["interpretations"] == []
    assert befund["polarity"] is None
    assert befund["phase_halfbits"] is None
    assert befund["score"] is None
    assert befund["margin"] is None
    print(f"[framing] F6 (Zufall abgelehnt, {befund['reject_reason']}) OK")

    # F7: zu wenige Rahmen - 10 Rahmen liegen unter FRAMING_MIN_FRAMES, obwohl
    # das Signal selbst eindeutig ist (score waere 1,000). Die Ablehnung greift
    # allein ueber die Rahmenzahl, nicht ueber Score oder Margin.
    rng = random.Random(135)
    bits = _make_frame_bits(10, 5, 1.5, rng)
    befund = detect_async_framing(bits)
    assert befund["accepted"] is False, befund
    assert befund["reject_reason"] == "zu_wenige_rahmen", befund["reject_reason"]
    assert befund["n_frames"] == 10, befund["n_frames"]
    print("[framing] F7 (zu wenige Rahmen) OK")

    # F8: scores wird immer gefuellt, auch bei Ablehnung - eine Zeile je
    # Kandidatenlaenge, unabhaengig vom Ausgang.
    assert len(befund["scores"]) == len(CANDIDATE_LENGTHS_HALFBITS)
    gefundene_laengen = sorted(s["frame_halfbits"] for s in befund["scores"])
    assert gefundene_laengen == sorted(CANDIDATE_LENGTHS_HALFBITS)
    print("[framing] F8 (scores immer gefuellt) OK")

    # F9: Margin misst gegen die beste ANDERE Rahmenlaenge - Regressionsschutz:
    # aus den scores der akzeptierten 1,5-Stoppbit-Fixtur (F1) muss margin
    # exakt best_score minus dem hoechsten Score einer ANDEREN Laenge sein.
    rng = random.Random(101 + 15)
    bits = _make_frame_bits(200, 5, 1.5, rng)
    befund = detect_async_framing(bits)
    andere = [s["score"] for s in befund["scores"] if s["frame_halfbits"] != befund["frame_halfbits"]]
    assert abs(befund["margin"] - (befund["score"] - max(andere))) < 1e-12, befund
    print("[framing] F9 (Margin gegen beste andere Rahmenlaenge) OK")

    # F10 (T-1716a): gleichverteilte 5-Bit-Codes (echte Rahmen, aber
    # Zufallsinhalt) -> ita2_hint IMMER falsch, ueber mindestens 100 Seeds.
    # Die wichtigste Pruefung des Nachtrags: das neue Kriterium (Space +
    # haeufigster Code) darf auf reinem Zufall nicht anschlagen.
    n_seeds = 120
    for seed in range(500, 500 + n_seeds):
        rng = random.Random(seed)
        bits = _make_frame_bits(300, 5, 1.5, rng)
        befund = detect_async_framing(bits)
        assert befund["accepted"], (seed, befund)
        chars = character_stats(bits, befund)
        assert chars["ita2_hint"] is False, (seed, chars)
    print(f"[framing] F10 (Zufallsinhalt, {n_seeds} Seeds, ita2_hint immer falsch) OK")

    # F11 (T-1716a): echter Text, GEMISCHTER Verkehr (~30% Zahlengruppen) ->
    # ita2_hint wahr. FIGS/LTRS werden nicht mehr geprueft (Material, kein
    # Kriterium), Space und der haeufigste Code muessen ueber den Schwellen
    # liegen, n_chars muss ITA2_MIN_CHARS erreichen.
    rng = random.Random(201)
    bits = _make_ita2_text_bits(300, rng, zahlen_anteil=0.30)
    befund = detect_async_framing(bits)
    assert befund["accepted"], befund
    assert befund["frame_halfbits"] == 15, befund["frame_halfbits"]
    chars = character_stats(bits, befund)
    print(f"[framing] F11: n_chars={chars['n_chars']} "
          f"Space={chars['code_shares']['00100']:.3f} "
          f"top1={chars['top_codes'][0]['share']:.3f} ita2_hint={chars['ita2_hint']}")
    assert chars["ita2_hint"], chars
    assert chars["n_chars"] >= ITA2_MIN_CHARS, chars
    assert chars["code_shares"]["00100"] > ITA2_SPACE_MIN, chars
    assert chars["top_codes"][0]["share"] > ITA2_TOP1_MIN, chars
    print("[framing] F11 (echter Text, gemischt, ita2_hint wahr) OK")

    # F12 (T-1716a): echter Text, NUMERISCH DICHTER Verkehr (65%
    # Zahlengruppen, Bestandsfixtur aus T-1716) -> ita2_hint ebenfalls wahr.
    # Beide Verkehrsarten muessen durchkommen - genau das konnte die alte,
    # FIGS-gestuetzte Bedingung nicht (FIGS ist bei dieser Verkehrsart zwar
    # haeufiger, aber die Ueberlappung mit Zufall macht sie trotzdem
    # untauglich, siehe Kommentar an ITA2_SPACE_MIN oben).
    rng = random.Random(201)
    bits = _make_ita2_text_bits(300, rng, zahlen_anteil=0.65)
    befund = detect_async_framing(bits)
    assert befund["accepted"], befund
    chars = character_stats(bits, befund)
    print(f"[framing] F12: n_chars={chars['n_chars']} "
          f"Space={chars['code_shares']['00100']:.3f} "
          f"top1={chars['top_codes'][0]['share']:.3f} ita2_hint={chars['ita2_hint']}")
    assert chars["ita2_hint"], chars
    print("[framing] F12 (echter Text, numerisch dicht, ita2_hint wahr) OK")

    # F13 (T-1716a): zu wenige Zeichen - n_chars knapp unter ITA2_MIN_CHARS
    # bei sonst eindeutigem Text (Space und haeufigster Code weit ueber den
    # Schwellen) -> ita2_hint dennoch falsch. Die Mindestzahl greift also
    # unabhaengig von den beiden Anteilskriterien.
    rng = random.Random(400)
    bits = _make_ita2_text_bits(ITA2_MIN_CHARS - 10, rng, zahlen_anteil=0.65)
    befund = detect_async_framing(bits)
    assert befund["accepted"], befund
    chars = character_stats(bits, befund)
    assert chars["n_chars"] < ITA2_MIN_CHARS, chars
    assert chars["code_shares"]["00100"] > ITA2_SPACE_MIN, chars
    assert chars["top_codes"][0]["share"] > ITA2_TOP1_MIN, chars
    assert chars["ita2_hint"] is False, chars
    print(f"[framing] F13 (n_chars={chars['n_chars']} < ITA2_MIN_CHARS, "
          f"ita2_hint falsch trotz eindeutiger Anteile) OK")

    # Keine Pruefung mit den Erprobungswerten der Aufnahme 20260912_202830_
    # 7646kHz (Space 0,347, haeufigster Code 0,347 ueber 95 Zeichen, siehe
    # PLAN.md T-1716a): ITA2_MIN_CHARS=300 liegt ueber den 95 Zeichen dieser
    # Erprobung, die Bedingung n_chars>=ITA2_MIN_CHARS liesse ita2_hint dort
    # unabhaengig von den Anteilen falsch ausfallen. Die 95-Zeichen-Erprobung
    # ist damit fuer diese Konstante schlicht zu kurz - kein Fall fuer den
    # Selbsttest, siehe Abschlussbericht.

    # F14: framing nicht akzeptiert -> leeres Ergebnis, keine Ausnahme.
    rng = random.Random(202)
    bits = [rng.randint(0, 1) for _ in range(300 * 15)]
    befund = detect_async_framing(bits)
    assert befund["accepted"] is False, befund
    chars = character_stats(bits, befund)
    assert chars["n_frames_total"] == 0
    assert chars["n_chars"] == 0
    assert chars["fail_share"] is None
    assert chars["fail_run_max"] == 0
    assert chars["fail_share_in_runs"] is None
    assert all(v is None for v in chars["code_shares"].values())
    assert chars["top_codes"] == []
    assert chars["halfbit_mismatch_share"] is None
    assert chars["ita2_hint"] is False
    print("[framing] F14 (framing nicht akzeptiert, leeres Ergebnis) OK")

    # F15: Rahmenlaenge 18 (ASCII, 7 Datenbits) -> leeres Ergebnis - diese
    # Funktion ist nur fuer 14/15/16 Halbbit (5 Datenbits) zustaendig.
    rng = random.Random(203)
    bits = _make_frame_bits(300, 7, 1.0, rng)
    befund = detect_async_framing(bits)
    assert befund["accepted"], befund
    assert befund["frame_halfbits"] == 18, befund
    chars = character_stats(bits, befund)
    assert chars["n_chars"] == 0
    assert chars["ita2_hint"] is False
    print("[framing] F15 (Rahmenlaenge 18, leeres Ergebnis) OK")

    # F16: Polaritaet "inverted" - dieselbe Fixture invertiert liefert
    # IDENTISCHE code_shares wie die nicht invertierte (character_stats
    # kippt die Bits selbst zurueck).
    rng = random.Random(204)
    bits = _make_ita2_text_bits(300, rng)
    bits_inv = [1 - b for b in bits]
    befund = detect_async_framing(bits)
    befund_inv = detect_async_framing(bits_inv)
    assert befund["polarity"] == "normal", befund
    assert befund_inv["polarity"] == "inverted", befund_inv
    chars = character_stats(bits, befund)
    chars_inv = character_stats(bits_inv, befund_inv)
    assert chars["code_shares"] == chars_inv["code_shares"], (chars["code_shares"], chars_inv["code_shares"])
    print("[framing] F16 (Polaritaet, identische code_shares) OK")

    # F17: Ausfallslaeufe unterscheiden Leerlauf/Rasterdrift von Bitfehlern -
    # die Kernpruefung der Aufgabe.
    rng = random.Random(205)
    bits_clean = _make_frame_bits(300, 5, 1.5, rng)
    befund_clean = detect_async_framing(bits_clean)
    assert befund_clean["accepted"], befund_clean
    phase = befund_clean["phase_halfbits"]
    P = befund_clean["frame_halfbits"]

    insert_at = phase + 100 * P
    bits_idle = bits_clean[:insert_at] + [1] * (20 * P) + bits_clean[insert_at:]
    befund_idle = detect_async_framing(bits_idle)
    assert befund_idle["accepted"], befund_idle
    chars_idle = character_stats(bits_idle, befund_idle)

    rng2 = random.Random(206)
    bits_err = _flip_bits(bits_clean, 0.05, rng2)
    befund_err = detect_async_framing(bits_err)
    assert befund_err["accepted"], befund_err
    chars_err = character_stats(bits_err, befund_err)

    print(f"[framing] F17: Leerlauf fail_run_max={chars_idle['fail_run_max']} "
          f"fail_share_in_runs={chars_idle['fail_share_in_runs']:.3f}  "
          f"Bitfehler fail_run_max={chars_err['fail_run_max']} "
          f"fail_share_in_runs={chars_err['fail_share_in_runs']:.3f}")
    assert chars_idle["fail_run_max"] >= 20, chars_idle
    assert chars_idle["fail_share_in_runs"] > 0.8, chars_idle
    assert chars_err["fail_run_max"] < 10, chars_err
    assert chars_err["fail_share_in_runs"] < 0.5, chars_err
    print("[framing] F17 (Ausfallslaeufe: Leerlauf gegen Bitfehler unterschieden) OK")

    print("[framing] Selbsttest erfolgreich abgeschlossen.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Asynchrone Rahmenerkennung auf dem Halbbit-Raster (T-1715)")
    ap.add_argument("--selftest", action="store_true", help="Selbsttest ausfuehren")
    args = ap.parse_args()

    if args.selftest:
        _self_test()
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
