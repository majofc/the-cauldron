"""Published age/sex strength norms → percentile → decile → "flames" (1-10).

This module is PURE (no Django/ORM): it takes plain values and returns plain
results, so it is unit-testable in isolation.

HONESTY POLICY — the peer score must be fair, so every number here is sourced and
its confidence is labelled. We never silently invent deciles; estimates are
flagged so they can be swapped for real data later (see the_cauldron/README.md).

Each norm carries a ``confidence``:

- ``good``       real age×sex percentiles from a published reference.
- ``thin``       real but narrow (one study, few brackets, or one sex).
- ``estimated``  PLACEHOLDER — no published age×sex percentile table was found
                 for this movement, so the table is a physiologically-plausible
                 estimate. Flagged ``estimated`` end-to-end (score → API → UI)
                 and listed in the README for replacement with real data.

Per movement:

- ``pushup``         GOOD      — CSEP-PATH (Payne et al., 2000). Female norms use
                                 the MODIFIED (knee) push-up position.
- ``plank``          THIN/EST  — ages ≤29 real (Chase/Brigham, IJES, collegiate
                                 18-25); ages 30+ ESTIMATED (~12%/decade decay,
                                 consistent with reported ~10-15%/decade), flagged
                                 via ``estimated_brackets``.
- ``pullup``         THIN      — single 18-35 bracket, category bands, men only
                                 (women's data is a floor effect → excluded).
- ``australian_row`` THIN/EST  — Strength Level community standards (inverted row,
                                 101k sets); 20-39 as published, 40+ estimated.
- ``pike_pushup``    THIN/EST  — Strength Level community standards (79k sets);
                                 20-39 as published, 40+ estimated.
- ``glute_bridge``   THIN/EST  — Strength Level community standards (163k sets);
                                 20-39 as published, 40+ estimated.
- ``split_squat``    EST       — DERIVED from Strength Level bodyweight-squat
                                 standards at ~0.5× per leg; whole table estimated.

Strength Level publishes population rep standards with documented level cutoffs
(Beginner/Novice/Intermediate/Advanced/Elite = P5/P20/P50/P80/P95). The logging
population skews trained and young-adult, hence confidence "thin" and the 40+
estimated extensions.

Peer scoring is per the CALIBRATED TEST movement of each pattern (the Trial
anchor). Other rungs on a ladder (e.g. Wall Push-up vs Push-up) are intentionally
NOT scored: a rep count is only comparable to a norm at the difficulty the norm
was measured at. Such movements return ``has_data=False``.
"""

from dataclasses import dataclass
from typing import Optional

# Which exercise (by exact catalog name) maps to which norm table. Only the
# movements that genuinely match a normed test are scored; everything else
# returns "no peer data".
EXERCISE_NORMS = {
    "Push-up": "pushup",          # horizontal push  (real)
    "Plank": "plank",             # core             (real ≤29, estimated 30+)
    "Pull-up": "pullup",          # vertical pull    (real, men only)
    "Chin-up": "pullup",          # underhand grip of the pull-up rung — same norm
    "Australian Row": "australian_row",  # vertical pull anchor (estimated)
    "Pike Push-up": "pike_pushup",       # vertical push        (estimated)
    "Split Squat": "split_squat",        # lower unilateral     (estimated)
    "Glute Bridge": "glute_bridge",      # hinge                (estimated)
}

AGE_BRACKETS = [(20, 29), (30, 39), (40, 49), (50, 59), (60, 69)]

# ── Norm tables ──────────────────────────────────────────────────────────────
# "percentiles": {pct: value} anchor points; we interpolate intermediate deciles.
# "bands": ordered [(min_value, percentile_center)]; coarse, flagged approximate.

NORMS = {
    "pushup": {
        "label": "Push-ups",
        "metric": "reps",
        "confidence": "good",
        "source": "CSEP-PATH percentiles (Payne et al., 2000)",
        "url": "https://fitnessnorms.com/strength/push-ups/",
        "type": "percentiles",
        "female_note": "Female norms use the modified (knee) push-up position.",
        "tables": {
            "male": {
                (20, 29): {5: 8, 25: 22, 50: 25, 75: 29, 95: 40},
                (30, 39): {5: 5, 25: 17, 50: 19, 75: 22, 95: 35},
                (40, 49): {5: 4, 25: 13, 50: 15, 75: 17, 95: 30},
                (50, 59): {5: 3, 25: 10, 50: 11, 75: 13, 95: 26},
                (60, 69): {5: 2, 25: 8, 50: 9, 75: 11, 95: 22},
            },
            "female": {
                (20, 29): {5: 4, 25: 15, 50: 18, 75: 21, 95: 35},
                (30, 39): {5: 3, 25: 13, 50: 16, 75: 20, 95: 31},
                (40, 49): {5: 2, 25: 11, 50: 13, 75: 15, 95: 29},
                (50, 59): {5: 0, 25: 7, 50: 9, 75: 11, 95: 27},
                (60, 69): {5: 0, 25: 5, 50: 8, 75: 12, 95: 20},
            },
        },
    },
    "plank": {
        "label": "Plank hold",
        "metric": "seconds",
        "confidence": "thin",
        "source": "Fitness norms for the plank (Chase/Brigham et al., IJES) — "
                  "collegiate athletes 18-25; quartiles only. Brackets 30+ are "
                  "ESTIMATED (≈12%/decade decay, consistent with the reported "
                  "~10-15%/decade decline after 35), not directly measured.",
        "url": "https://digitalcommons.wku.edu/ijesab/vol8/iss2/14/",
        "type": "percentiles",
        # Ages 30+ are estimated extensions of the 18-29 study data, not measured.
        "estimated_brackets": {(30, 39), (40, 49), (50, 59), (60, 69)},
        "tables": {
            "male": {
                (18, 29): {25: 84, 50: 110, 75: 135},   # real
                (30, 39): {25: 74, 50: 97, 75: 119},    # estimated
                (40, 49): {25: 65, 50: 85, 75: 105},    # estimated
                (50, 59): {25: 57, 50: 75, 75: 92},     # estimated
                (60, 69): {25: 50, 50: 66, 75: 81},     # estimated
            },
            "female": {
                (18, 29): {25: 73, 50: 95, 75: 122},    # real
                (30, 39): {25: 64, 50: 84, 75: 107},    # estimated
                (40, 49): {25: 56, 50: 74, 75: 94},     # estimated
                (50, 59): {25: 49, 50: 65, 75: 83},     # estimated
                (60, 69): {25: 43, 50: 57, 75: 73},     # estimated
            },
        },
    },
    "pullup": {
        "label": "Pull-ups",
        "metric": "reps",
        "confidence": "thin",
        "source": "Topend Sports adult categories, 18-35 (category bands → "
                  "deciles by midpoint; approximate).",
        "url": "https://www.topendsports.com/testing/tests/pullup.htm",
        "type": "bands",
        "max_age": 35,
        "tables": {
            # (min reps for band, percentile center of band)
            "male": {(18, 35): [(0, 8), (3, 28), (7, 55), (10, 78), (16, 95)]},
            # Women's pull-up data is a floor effect (0-2 across the board) — not
            # meaningful as deciles, so it is intentionally omitted.
        },
    },
    # ── Crowd-sourced norms (Strength Level) ─────────────────────────────────
    # No peer-reviewed age×sex AMRAP percentile study was found for these moves,
    # but Strength Level publishes population rep standards from hundreds of
    # thousands of logged sets, with documented level→percentile cutoffs:
    #   Beginner=P5, Novice=P20, Intermediate=P50, Advanced=P80, Elite=P95.
    # That population skews trained and young-adult, so we treat the published
    # figures as the 20-39 brackets (confidence "thin") and model ages 40+ as an
    # estimated decline (~15%/decade; flagged via estimated_brackets). "<1" rep
    # at P5 is recorded as 1. See the_cauldron/README.md for replacement notes.
    "australian_row": {
        "label": "Australian row",
        "metric": "reps",
        "confidence": "thin",
        "source": "Strength Level community rep standards for the inverted row "
                  "(101,242 logged sets; level→percentile B/N/I/A/E = 5/20/50/"
                  "80/95). Trained-skewed; ages 40+ are an estimated decline.",
        "url": "https://strengthlevel.com/strength-standards/inverted-row",
        "type": "percentiles",
        "estimated_brackets": {(40, 49), (50, 59), (60, 69)},
        "tables": {
            "male": {
                (20, 29): {5: 1, 20: 6, 50: 19, 80: 35, 95: 54},   # Strength Level
                (30, 39): {5: 1, 20: 6, 50: 19, 80: 35, 95: 54},   # Strength Level
                (40, 49): {5: 1, 20: 5, 50: 16, 80: 30, 95: 46},   # estimated
                (50, 59): {5: 1, 20: 4, 50: 13, 80: 25, 95: 38},   # estimated
                (60, 69): {5: 1, 20: 3, 50: 11, 80: 20, 95: 31},   # estimated
            },
            "female": {
                (20, 29): {5: 1, 20: 2, 50: 13, 80: 27, 95: 43},   # Strength Level
                (30, 39): {5: 1, 20: 2, 50: 13, 80: 27, 95: 43},   # Strength Level
                (40, 49): {5: 1, 20: 2, 50: 11, 80: 23, 95: 37},   # estimated
                (50, 59): {5: 1, 20: 1, 50: 9, 80: 19, 95: 30},    # estimated
                (60, 69): {5: 1, 20: 1, 50: 8, 80: 16, 95: 25},    # estimated
            },
        },
    },
    "pike_pushup": {
        "label": "Pike push-up",
        "metric": "reps",
        "confidence": "thin",
        "source": "Strength Level community rep standards for the pike push-up "
                  "(79,652 logged sets; level→percentile B/N/I/A/E = 5/20/50/80/"
                  "95). Trained-skewed; ages 40+ are an estimated decline.",
        "url": "https://strengthlevel.com/strength-standards/pike-push-up",
        "type": "percentiles",
        "estimated_brackets": {(40, 49), (50, 59), (60, 69)},
        "tables": {
            "male": {
                (20, 29): {5: 1, 20: 6, 50: 19, 80: 36, 95: 54},   # Strength Level
                (30, 39): {5: 1, 20: 6, 50: 19, 80: 36, 95: 54},   # Strength Level
                (40, 49): {5: 1, 20: 5, 50: 16, 80: 31, 95: 46},   # estimated
                (50, 59): {5: 1, 20: 4, 50: 13, 80: 25, 95: 38},   # estimated
                (60, 69): {5: 1, 20: 3, 50: 11, 80: 21, 95: 31},   # estimated
            },
            "female": {
                (20, 29): {5: 1, 20: 2, 50: 13, 80: 28, 95: 44},   # Strength Level
                (30, 39): {5: 1, 20: 2, 50: 13, 80: 28, 95: 44},   # Strength Level
                (40, 49): {5: 1, 20: 2, 50: 11, 80: 24, 95: 37},   # estimated
                (50, 59): {5: 1, 20: 1, 50: 9, 80: 20, 95: 31},    # estimated
                (60, 69): {5: 1, 20: 1, 50: 8, 80: 16, 95: 26},    # estimated
            },
        },
    },
    "glute_bridge": {
        "label": "Glute bridge",
        "metric": "reps",
        "confidence": "thin",
        "source": "Strength Level community rep standards for the bodyweight "
                  "glute bridge (163,095 logged sets; level→percentile B/N/I/A/E "
                  "= 5/20/50/80/95). Trained-skewed; ages 40+ are an estimated "
                  "decline.",
        "url": "https://strengthlevel.com/strength-standards/glute-bridge",
        "type": "percentiles",
        "estimated_brackets": {(40, 49), (50, 59), (60, 69)},
        "tables": {
            "male": {
                (20, 29): {5: 1, 20: 7, 50: 37, 80: 78, 95: 125},  # Strength Level
                (30, 39): {5: 1, 20: 7, 50: 37, 80: 78, 95: 125},  # Strength Level
                (40, 49): {5: 1, 20: 6, 50: 31, 80: 66, 95: 106},  # estimated
                (50, 59): {5: 1, 20: 5, 50: 26, 80: 55, 95: 88},   # estimated
                (60, 69): {5: 1, 20: 4, 50: 21, 80: 45, 95: 73},   # estimated
            },
            "female": {
                (20, 29): {5: 1, 20: 8, 50: 31, 80: 62, 95: 97},   # Strength Level
                (30, 39): {5: 1, 20: 8, 50: 31, 80: 62, 95: 97},   # Strength Level
                (40, 49): {5: 1, 20: 7, 50: 26, 80: 53, 95: 82},   # estimated
                (50, 59): {5: 1, 20: 6, 50: 22, 80: 43, 95: 68},   # estimated
                (60, 69): {5: 1, 20: 5, 50: 18, 80: 36, 95: 56},   # estimated
            },
        },
    },
    # ── Derived (estimated) norm ─────────────────────────────────────────────
    # No per-leg split-squat rep norm exists. We derive it from Strength Level's
    # bodyweight (bilateral) squat standards (546,341 logged sets) at ~0.5× —
    # the rough per-leg-to-bilateral rep ratio. The 0.5 ratio is our assumption,
    # so the WHOLE table is flagged ``estimated`` (not just ages 40+).
    "split_squat": {
        "label": "Split squat (per leg)",
        "metric": "reps",
        "confidence": "estimated",
        "estimated": True,
        "source": "DERIVED — Strength Level bodyweight-squat rep standards "
                  "(546,341 sets) scaled ~0.5× for per-leg work. Ratio is an "
                  "assumption; replace with a real per-leg split-squat norm.",
        "url": "https://strengthlevel.com/strength-standards/bodyweight-squat",
        "type": "percentiles",
        "tables": {
            "male": {
                (20, 29): {5: 1, 20: 8, 50: 29, 80: 57, 95: 89},
                (30, 39): {5: 1, 20: 8, 50: 29, 80: 57, 95: 89},
                (40, 49): {5: 1, 20: 7, 50: 25, 80: 48, 95: 76},
                (50, 59): {5: 1, 20: 6, 50: 20, 80: 40, 95: 62},
                (60, 69): {5: 1, 20: 5, 50: 17, 80: 33, 95: 52},
            },
            "female": {
                (20, 29): {5: 1, 20: 4, 50: 20, 80: 41, 95: 66},
                (30, 39): {5: 1, 20: 4, 50: 20, 80: 41, 95: 66},
                (40, 49): {5: 1, 20: 3, 50: 17, 80: 35, 95: 56},
                (50, 59): {5: 1, 20: 3, 50: 14, 80: 29, 95: 46},
                (60, 69): {5: 1, 20: 2, 50: 12, 80: 24, 95: 38},
            },
        },
    },
}


@dataclass
class PeerScore:
    has_data: bool
    flames: Optional[int] = None       # 1-10
    decile: Optional[int] = None       # 1-10
    percentile: Optional[float] = None  # 0-100
    label: str = ""
    confidence: str = ""               # good | thin | estimated | none
    approximate: bool = False
    estimated: bool = False            # table is a placeholder, not published data
    source: str = ""
    url: str = ""
    note: str = ""
    reason: str = ""                   # why has_data is False


def _bracket(age: int, brackets) -> Optional[tuple]:
    for lo, hi in brackets:
        if lo <= age <= hi:
            return (lo, hi)
    # Clamp into the nearest end bracket (flagged via note by caller).
    if age < brackets[0][0]:
        return brackets[0]
    if age > brackets[-1][1]:
        return brackets[-1]
    return None


def _pct_from_percentiles(anchors: dict, value: float) -> float:
    """Piecewise-linear percentile for a value given {pct: value} anchors."""
    pts = sorted(anchors.items())  # [(pct, val), ...] ascending by pct → val
    pcts = [p for p, _ in pts]
    vals = [v for _, v in pts]
    lo_pct, lo_val = pcts[0], vals[0]
    hi_pct, hi_val = pcts[-1], vals[-1]
    if value <= lo_val:
        if lo_val <= 0:
            return float(lo_pct)
        return max(1.0, lo_pct * (value / lo_val))
    if value >= hi_val:
        # Approach 100 as the value exceeds the top anchor.
        extra = min(1.0, (value - hi_val) / hi_val) if hi_val else 1.0
        return hi_pct + (100 - hi_pct) * extra
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        if v0 <= value <= v1:
            span = (v1 - v0) or 1
            return p0 + (p1 - p0) * (value - v0) / span
    return float(hi_pct)


def _pct_from_bands(bands: list, value: float) -> float:
    """Percentile for a value from ordered [(min_value, percentile_center)]."""
    pct = bands[0][1]
    for min_v, center in bands:
        if value >= min_v:
            pct = center
        else:
            break
    return float(pct)


def _flames_from_percentile(pct: float) -> tuple:
    """(decile 1-10, flames 1-10) from a percentile."""
    decile = min(10, max(1, int(pct // 10) + 1))
    return decile, decile


def score(exercise_name: str, value, sex: str, age: Optional[int]) -> PeerScore:
    """Peer score for an AMRAP ``value`` on ``exercise_name`` given sex+age.

    Returns ``has_data=False`` (with a reason) when we have no fair basis to
    score — missing demographics, an un-normed movement, or out-of-range age.
    """
    norm_key = EXERCISE_NORMS.get(exercise_name)
    if norm_key is None:
        return PeerScore(False, confidence="none",
                         reason=f"No published peer benchmark for {exercise_name} yet.")
    if sex not in ("male", "female"):
        return PeerScore(False, reason="Add your sex in Equipment to unlock peer scoring.")
    if age is None:
        return PeerScore(False, reason="Add your birth year in Equipment to unlock peer scoring.")

    norm = NORMS[norm_key]
    if "max_age" in norm and age > norm["max_age"]:
        return PeerScore(False, confidence=norm["confidence"],
                         reason=f"{norm['label']} norms only cover ages up to {norm['max_age']}.")

    table = norm["tables"].get(sex)
    if not table:
        return PeerScore(False, confidence=norm["confidence"],
                         reason=f"No {norm['label']} peer data for this group yet.")

    brackets = list(table.keys())
    bracket = _bracket(age, sorted(brackets))
    anchors = table.get(bracket)
    if anchors is None:
        return PeerScore(False, confidence=norm["confidence"],
                         reason=f"No {norm['label']} peer data for your age group yet.")

    if norm["type"] == "percentiles":
        pct = _pct_from_percentiles(anchors, float(value))
        approximate = True  # only P25/50/75(/95) are direct; deciles interpolated
    else:
        pct = _pct_from_bands(anchors, float(value))
        approximate = True

    # Whole-table placeholder, or a per-bracket estimated extension of real data.
    estimated = norm.get("estimated", False) or (
        bracket in norm.get("estimated_brackets", set())
    )

    decile, flames = _flames_from_percentile(pct)
    note = norm.get("female_note", "") if sex == "female" else ""
    if estimated:
        est_note = "Estimated benchmark — not yet from published data."
        note = f"{note} {est_note}".strip() if note else est_note
    return PeerScore(
        has_data=True,
        flames=flames,
        decile=decile,
        percentile=round(pct, 1),
        label=norm["label"],
        confidence=norm["confidence"],
        approximate=approximate,
        estimated=estimated,
        source=norm["source"],
        url=norm["url"],
        note=note,
    )


def decile_cutoffs(exercise_name: str, sex: str, age: Optional[int]) -> Optional[dict]:
    """Estimated value at each decile boundary (P10..P90) for charting reference
    lines. Returns ``{10: v, 20: v, ...}`` or ``None`` when not scoreable."""
    probe = score(exercise_name, 0, sex, age)
    if not probe.has_data:
        return None
    norm = NORMS[EXERCISE_NORMS[exercise_name]]
    table = norm["tables"][sex]
    bracket = _bracket(age, sorted(table.keys()))
    anchors = table[bracket]

    cutoffs = {}
    for d in range(10, 100, 10):
        # Invert: find the value whose percentile ≈ d.
        cutoffs[d] = _value_at_percentile(norm["type"], anchors, d)
    return cutoffs


def _value_at_percentile(kind: str, anchors, target_pct: float) -> float:
    if kind == "percentiles":
        pts = sorted(anchors.items())
        pcts = [p for p, _ in pts]
        vals = [v for _, v in pts]
        if target_pct <= pcts[0]:
            return round(vals[0] * (target_pct / pcts[0]), 1) if pcts[0] else float(vals[0])
        if target_pct >= pcts[-1]:
            return float(vals[-1])
        for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
            if p0 <= target_pct <= p1:
                span = (p1 - p0) or 1
                return round(v0 + (v1 - v0) * (target_pct - p0) / span, 1)
        return float(vals[-1])
    # bands: nearest band center
    best = anchors[0][0]
    for min_v, center in anchors:
        if center <= target_pct:
            best = min_v
    return float(best)
