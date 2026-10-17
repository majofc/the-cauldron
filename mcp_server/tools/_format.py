"""Compact shapes for tool answers — formatting only, no rules.

Totals keep "unknown is never 0": ``values`` holds what is known, and a
nutrient listed in ``incomplete`` had items with no data, so its value is a
lower bound ("at least").
"""

from django.utils import timezone

KEY = ("kcal", "protein_g", "carbs_g", "fat_g")


def num(value, digits=1):
    return None if value is None else round(float(value), digits)


def totals(rows):
    """``totals_payload`` rows → ``{"values": {...}, "incomplete": [...]}``."""
    return {
        "values": {r["key"]: num(r["value"]) for r in rows if r["value"] or r["missing"]},
        "incomplete": [r["key"] for r in rows if r["missing"]],
    }


def known(nutrients, missing=None):
    return {
        "nutrients": {k: num(v) for k, v in (nutrients or {}).items() if v is not None},
        "incomplete": sorted(k for k, n in (missing or {}).items() if n),
    }


def goal(row):
    out = {
        "nutrient": row["nutrient"],
        "kind": row["kind"],
        "min": row["min_value"],
        "max": row["max_value"],
    }
    if "status" in row:
        out.update(value=num(row["value"]), lower_bound=bool(row["missing"]), status=row["status"])
    if row.get("valid_to") is not None or "status" not in row:
        out.update(valid_from=row["valid_from"], valid_to=row["valid_to"])
    return out


def entry(row):
    return {
        "id": row["uuid"],
        "date": row["local_date"],
        "time": row["local_time"],
        "label": row["label"],
        "source": row["source"],
        "quantity": row["quantity"],
        "unit": row["unit"],
        "grams": num(row["grams"]),
        **known(row["nutrients"], row["missing"]),
    }


def planned(row):
    out = {
        "id": row["uuid"],
        "date": row["date"],
        "slot": row["slot"],
        "spell_id": row["spell"],
        "title": row["spell_title"],
        "quantity": row["quantity"],
        "unit": row["unit"],
        "eaten": row["eaten"],
    }
    if "nutrients" in row:
        out["available"] = row["available"]
        if row["nutrients"] is not None:
            out.update(grams=row["grams"], **known(row["nutrients"], row["missing"]))
    return out


def local_now(tz):
    return timezone.localtime(timezone.now(), tz).strftime("%Y-%m-%dT%H:%M")
