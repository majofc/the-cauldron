"""Published age/sex strength norms → percentile → decile → "flames" (1-10).

This module is PURE (no Django/ORM): it takes plain values and returns plain
results, so it is unit-testable in isolation.

HONESTY POLICY — the peer score must be fair, so every number here is sourced and
its confidence is labelled. We never invent deciles:

- ``pushup``  GOOD   — real age×sex percentiles, CSEP-PATH (Payne et al., 2000).
                       NB: female norms use the MODIFIED (knee) push-up position.
- ``plank``   THIN   — one peer-reviewed study, collegiate athletes 18-25 only
                       (Chase/Brigham, IJES). Only applied for ages ≤ 29.
- ``pullup``  THIN   — single 18-35 bracket, category bands only; men only
                       (women's data is a floor effect → excluded).

Movements with NO published norms (vertical push, hinge/unilateral) are simply
absent here; the scorer returns ``has_data=False`` for them rather than guessing.
Category-band sources are mapped to deciles by midpoint and clearly flagged
``approximate``. See the research report in the PR description for full sourcing.
"""

from dataclasses import dataclass
from typing import Optional

# Which exercise (by exact catalog name) maps to which norm table. Only the
# movements that genuinely match a normed test are scored; everything else
# returns "no peer data".
EXERCISE_NORMS = {
    "Push-up": "pushup",
    "Plank": "plank",
    "Pull-up": "pullup",
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
                  "collegiate athletes 18-25; quartiles only.",
        "url": "https://digitalcommons.wku.edu/ijesab/vol8/iss2/14/",
        "type": "percentiles",
        "max_age": 29,  # study population is young; do not extrapolate older
        "tables": {
            "male": {(18, 29): {25: 84, 50: 110, 75: 135}},
            "female": {(18, 29): {25: 73, 50: 95, 75: 122}},
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
}


@dataclass
class PeerScore:
    has_data: bool
    flames: Optional[int] = None       # 1-10
    decile: Optional[int] = None       # 1-10
    percentile: Optional[float] = None  # 0-100
    label: str = ""
    confidence: str = ""               # good | thin | none
    approximate: bool = False
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

    decile, flames = _flames_from_percentile(pct)
    note = norm.get("female_note", "") if sex == "female" else ""
    return PeerScore(
        has_data=True,
        flames=flames,
        decile=decile,
        percentile=round(pct, 1),
        label=norm["label"],
        confidence=norm["confidence"],
        approximate=approximate,
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
