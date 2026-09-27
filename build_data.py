"""
Build data.json for Kommunal tillväxtanalys.

SCB PxWebApi v2:
  https://statistikdatabasen.scb.se/api/v2

Important:
PxWebApi v2 metadata returns table variables, while JSON-stat data returns
dimensions. This script deliberately handles both structures.

The web app expects:
  communes, periods, variables, values, sources

No SCB v1 endpoint is used.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import requests

API = "https://statistikdatabasen.scb.se/api/v2"
OUT = Path("data.json")

# These are the municipal monthly tables we actually want to use.
TABLES = [
    ("TAB1625", "population_old"),
    ("TAB6473", "population_new"),
    ("TAB6260", "labour"),
]

S = requests.Session()
S.headers.update({"User-Agent": "kommunal-tillvaxtanalys/3.0"})


def norm(s: object) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.strip().lower())


def get(url: str, **params):
    r = S.get(url, params=params, timeout=90)
    r.raise_for_status()
    return r


def post_data(tid: str, payload: dict):
    r = S.post(
        f"{API}/tables/{quote(tid, safe='')}/data",
        params={"lang": "sv", "outputFormat": "json-stat2"},
        json=payload,
        timeout=180,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:1000]}")
    return r.json()


def metadata(tid: str) -> dict:
    return get(
        f"{API}/tables/{quote(tid, safe='')}/metadata",
        lang="sv",
    ).json()


def variables_from_metadata(meta: dict) -> list[dict]:
    """
    PxWebApi v2 metadata normally exposes variables as:
      {"variables":[{"code":...,"text":...,"values":[...],"valueTexts":[...]}]}
    Older/alternate responses can expose dimensions instead.
    """
    vars_ = meta.get("variables")
    if isinstance(vars_, list):
        return vars_

    dims = meta.get("dimension")
    if isinstance(dims, dict):
        out = []
        for code, d in dims.items():
            cat = d.get("category") or {}
            idx = cat.get("index") or {}
            labels = cat.get("label") or {}
            if isinstance(idx, dict):
                ordered = [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]
            else:
                ordered = list(idx)
            out.append({
                "code": code,
                "text": d.get("label", code),
                "values": ordered,
                "valueTexts": [labels.get(k, k) for k in ordered] if isinstance(labels, dict) else ordered,
                "elimination": (d.get("extension") or {}).get("elimination", False),
            })
        return out

    return []


def var_code(v: dict) -> str:
    return str(v.get("code") or v.get("variableCode") or v.get("id") or "")


def var_text(v: dict) -> str:
    return str(v.get("text") or v.get("label") or v.get("name") or var_code(v))


def var_pairs(v: dict) -> list[tuple[str, str]]:
    vals = v.get("values") or v.get("valueCodes") or []
    texts = v.get("valueTexts") or v.get("valueLabels") or []

    if isinstance(vals, dict):
        vals = list(vals.keys())
    vals = [str(x) for x in vals]

    if isinstance(texts, dict):
        texts = [texts.get(x, x) for x in vals]
    texts = [str(x) for x in texts]

    if len(texts) != len(vals):
        texts = vals[:]

    return list(zip(vals, texts))


def is_eliminable(v: dict) -> bool:
    return bool(v.get("elimination") is True)


def find_variable(vars_: list[dict], *needles: str) -> dict | None:
    ns = [norm(x) for x in needles]
    for v in vars_:
        t = norm(var_text(v))
        c = norm(var_code(v))
        if any(n in t or n == c for n in ns):
            return v
    return None


def identify_dims(vars_: list[dict]):
    region = find_variable(vars_, "region", "kommun")
    time = find_variable(vars_, "tid", "månad", "month")

    # Prefer exact SCB codes where present.
    for v in vars_:
        c = var_code(v)
        if c.lower() == "region":
            region = v
        if c.lower() in {"tid", "månad", "month"}:
            time = v

    return region, time


def municipal_regions(region_var: dict):
    out = []
    for code, label in var_pairs(region_var):
        m = re.match(r"^\s*(\d{4})\b", label)
        if m:
            out.append((code, m.group(1), label))
            continue
        if re.fullmatch(r"\d{4}", code):
            out.append((code, code, label))
    return out


def total_value(v: dict) -> str | None:
    pairs = var_pairs(v)
    if not pairs:
        return None

    preferred = [
        "totalt", "samtliga", "alla", "total",
        "bada kon", "bada kön", "båda",
        "totsa", "totsa", "tot",
    ]

    for code, label in pairs:
        n = norm(label)
        nc = norm(code)
        if any(p in n or p == nc for p in preferred):
            return code

    # Common SCB total codes.
    for code, _ in pairs:
        if norm(code) in {"totsa", "tot", "total"}:
            return code

    return pairs[0][0]


def choose_non_region_values(v: dict, kind: str) -> list[str]:
    """
    Choose sensible values for mandatory dimensions.

    - sex/age/birth-region/sector: total
    - labour-market status: all categories
    - population change: useful population measures
    - contents code: normally the first/total measure
    """
    code = norm(var_code(v))
    label = norm(var_text(v))
    pairs = var_pairs(v)

    if not pairs:
        return []

    if any(x in (code + " " + label)
           for x in ["arbetsmarknadsstatus", "status"]):
        return [c for c, _ in pairs]

    if "forandring" in code or "förändring" in label:
        # For the two monthly population tables the relevant SCB codes are
        # stable: 100 = folkmängd and 110 = folkökning.  Use the codes first
        # and text matching only as a fallback, because JSON-stat labels can
        # differ between API responses.
        codes = {c for c, _ in pairs}
        if {"100", "110"}.issubset(codes):
            return ["100", "110"]

        wanted = [
            "folkmangd", "folk okning",
            "fodelse", "dod", "flytt", "invandr", "utvand",
        ]
        chosen = [(c, t) for c, t in pairs
                  if any(w in norm(t) for w in wanted)]
        if chosen:
            return [c for c, _ in chosen]
        return [pairs[0][0]]

    # ContentsCode is usually a unit/measure dimension. Select all if small,
    # otherwise the first value. This keeps the cell count manageable.
    if "contents" in code or "innehall" in label or "matt" in label:
        return [c for c, _ in pairs[:3]]

    # For common breakdown dimensions, use the total.
    if any(x in (code + " " + label) for x in [
        "kon", "sex", "alder", "ålder", "fodelseregion",
        "fodelseregion", "sektor", "sector"
    ]):
        return [total_value(v)]

    # If the table has an unknown mandatory dimension, use a total when one
    # exists; otherwise the first value. This is safer than multiplying
    # every dimension silently.
    return [total_value(v)]


def choose_selections(tid: str, vars_: list[dict], regions, times, kind):
    region_var, time_var = identify_dims(vars_)
    if not region_var or not time_var:
        raise RuntimeError("Could not identify Region and Tid variables")

    selection = [
        {
            "variableCode": var_code(region_var),
            "valueCodes": [x[0] for x in regions],
        },
        {
            "variableCode": var_code(time_var),
            "valueCodes": times,
        },
    ]

    for v in vars_:
        c = var_code(v)
        if c in {var_code(region_var), var_code(time_var)}:
            continue
        if is_eliminable(v):
            continue

        vals = choose_non_region_values(v, kind)
        if not vals:
            raise RuntimeError(
                f"No values selected for mandatory variable {c} ({var_text(v)})"
            )
        selection.append({"variableCode": c, "valueCodes": vals})

    return selection


def jsonstat_rows(obj: dict) -> list[dict]:
    """
    Decode JSON-stat2 into one tidy row per cell.
    """
    ids = obj.get("id") or []
    sizes = obj.get("size") or []
    dims = obj.get("dimension") or {}
    values = obj.get("value") or []

    cats = {}
    for d in ids:
        cat = (dims.get(d) or {}).get("category") or {}
        idx = cat.get("index") or {}
        labels = cat.get("label") or {}

        if isinstance(idx, list):
            codes = idx
        else:
            codes = [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]

        cats[d] = [
            (str(c), str(labels.get(c, c)) if isinstance(labels, dict) else str(c))
            for c in codes
        ]

    rows = []
    if not ids or not sizes:
        return rows

    for flat, value in enumerate(values):
        if value is None:
            continue

        rem = flat
        coords = [0] * len(ids)

        for i in range(len(ids) - 1, -1, -1):
            coords[i] = rem % sizes[i]
            rem //= sizes[i]

        row = {"value": value}

        for i, d in enumerate(ids):
            code, label = cats[d][coords[i]]
            row[d] = code
            row[d + "_text"] = label

        rows.append(row)

    return rows


def detect_region_time(rows: list[dict], vars_: list[dict]):
    region, time = identify_dims(vars_)
    return var_code(region), var_code(time)


def classify_population_row(row: dict, vars_: list[dict]) -> str | None:
    """Map SCB monthly population rows to stable application IDs.

    The current monthly tables use the Forandringar dimension where:
      100 = folkmängd
      110 = folkökning
    The code-based mapping is authoritative for these two measures; text is
    retained as a fallback for the other population-change categories.
    """
    # Code-based mapping first.  This avoids depending on translated labels.
    for key in ("Forandringar", "Förändringar", "forandringar"):
        if key in row:
            code = str(row[key])
            if code == "100":
                return "population"
            if code == "110":
                return "population_growth"

    text = " ".join(
        str(v) for k, v in row.items()
        if k.endswith("_text")
    )
    n = norm(text)

    rules = [
        ("fodelseoverskott", "birth_surplus"),
        ("folk okning", "population_growth"),
        ("folkmangd", "population"),
        ("fodd", "births"),
        ("doda", "deaths"),
        ("inrikes inflytt", "domestic_in_migration"),
        ("inrikes utflytt", "domestic_out_migration"),
        ("invandring", "immigration"),
        ("utvandring", "emigration"),
        ("flyttningsoverskott", "net_migration"),
    ]

    for needle, out in rules:
        if needle in n:
            return out

    return None


def classify_labour_row(row: dict) -> str | None:
    text = " ".join(
        str(v) for k, v in row.items()
        if k.endswith("_text")
    )
    n = norm(text)

    # More specific phrases first.
    rules = [
        ("sysselsattningsgrad", "employment_rate"),
        ("arbetsloshet", "unemployment_rate"),
        ("arbetskraftsdeltagande", "participation_rate"),
        ("sysselsatta", "employed"),
        ("arbetslosa", "unemployed"),
        ("arbetskraft", "labour_force"),
    ]

    for needle, out in rules:
        if needle in n:
            return out

    return None


def add_rows_to_values(rows, vars_, kind, values, communes, periods):
    rd, td = detect_region_time(rows, vars_)

    for row in rows:
        region_text = str(row.get(rd + "_text", row.get(rd, "")))
        m = re.match(r"^\s*(\d{4})\b", region_text)
        if not m:
            # Sometimes JSON-stat gives the municipality code directly.
            raw = str(row.get(rd, ""))
            if re.fullmatch(r"\d{4}", raw):
                code = raw
            else:
                continue
        else:
            code = m.group(1)

        time_code = str(row.get(td, ""))
        if not re.fullmatch(r"\d{4}M\d{2}", time_code):
            continue

        if kind == "population":
            vid = classify_population_row(row, vars_)
        else:
            vid = classify_labour_row(row)

        if not vid:
            continue

        value = row.get("value")
        if isinstance(value, bool):
            continue

        try:
            value = float(value)
            if value.is_integer():
                value = int(value)
        except (TypeError, ValueError):
            continue

        values.setdefault(vid, {}).setdefault(code, {})[time_code] = value
        periods.add(time_code)


def variable_metadata(values, source_ranges):
    names = {
        "population": "Folkmängd",
        "population_growth": "Folkökning",
        "birth_surplus": "Födelseöverskott",
        "births": "Födda",
        "deaths": "Döda",
        "domestic_in_migration": "Inrikes inflyttning",
        "domestic_out_migration": "Inrikes utflyttning",
        "immigration": "Invandring",
        "emigration": "Utvandring",
        "net_migration": "Flyttningsöverskott",
        "employed": "Sysselsatta",
        "unemployed": "Arbetslösa",
        "labour_force": "Arbetskraft",
        "employment_rate": "Sysselsättningsgrad",
        "unemployment_rate": "Arbetslöshet",
        "participation_rate": "Arbetskraftsdeltagande",
    }

    groups = {
        "population": "Befolkning",
        "population_growth": "Befolkning",
        "birth_surplus": "Befolkning",
        "births": "Befolkning",
        "deaths": "Befolkning",
        "domestic_in_migration": "Migration",
        "domestic_out_migration": "Migration",
        "immigration": "Migration",
        "emigration": "Migration",
        "net_migration": "Migration",
        "employed": "Arbetsmarknad",
        "unemployed": "Arbetsmarknad",
        "labour_force": "Arbetsmarknad",
        "employment_rate": "Arbetsmarknad",
        "unemployment_rate": "Arbetsmarknad",
        "participation_rate": "Arbetsmarknad",
    }

    out = []

    for vid, by_commune in values.items():
        periods = sorted(
            t for cm in by_commune.values()
            for t in cm
        )
        if not periods:
            continue

        out.append({
            "id": vid,
            "name": names.get(vid, vid),
            "group": groups.get(vid, "Övrigt"),
            "tags": source_ranges.get(vid, []),
            "from": min(periods),
            "to": max(periods),
        })

    return out


def process_table(tid, kind, values, communes, periods, source_ranges):
    print(f"\n=== {tid} / {kind} ===")

    meta = metadata(tid)
    title = (
        meta.get("title")
        or meta.get("text")
        or meta.get("label")
        or tid
    )

    vars_ = variables_from_metadata(meta)
    if not vars_:
        raise RuntimeError("No variables found in metadata")

    print("Variables:")
    for v in vars_:
        print(
            f"  {var_code(v)} | {var_text(v)} | "
            f"n={len(var_pairs(v))} | elimination={is_eliminable(v)}"
        )

    region_var, time_var = identify_dims(vars_)
    if not region_var or not time_var:
        raise RuntimeError("Region/Tid not found")

    regions = municipal_regions(region_var)
    times = [c for c, _ in var_pairs(time_var)]

    print(f"Municipal region candidates: {len(regions)}")
    print(f"Periods in table: {len(times)}")

    if not regions:
        raise RuntimeError("No four-digit municipal region codes found")

    # Use six months per call. 290 municipalities x 6 months is small enough
    # for the 150,000-cell limit for the selected dimensions.
    for start in range(0, len(times), 6):
        tchunk = times[start:start + 6]

        selection = choose_selections(
            tid, vars_, regions, tchunk, kind
        )
        payload = {"selection": selection}

        print(
            f"Query {start + 1}-{min(start + 6, len(times))}/"
            f"{len(times)}; selections="
            f"{[(x['variableCode'], len(x['valueCodes'])) for x in selection]}"
        )

        obj = post_data(tid, payload)
        rows = jsonstat_rows(obj)

        print(f"Returned cells: {len(rows)}")

        before = sum(len(cm) for cm in values.get("population", {}).values())
        add_rows_to_values(
            rows, vars_, kind, values, communes, periods
        )
        after = sum(len(cm) for cm in values.get("population", {}).values())

        if rows:
            # Show diagnostics for the first returned cell and the population
            # measure codes when this is a population table.
            sample = rows[0]
            print("Sample:", sample)
            if kind == "population":
                fd = next((k for k in ("Forandringar", "Förändringar", "forandringar") if k in sample), None)
                if fd:
                    seen = sorted({(str(r.get(fd)), str(r.get(fd + "_text", ""))) for r in rows})
                    print("Population measure codes returned:", seen)

        time.sleep(0.25)

    # Register municipal names from metadata.
    for _, code, label in regions:
        name = re.sub(r"^\s*\d{4}\s*:?\s*", "", label).strip()
        communes[code] = name

    # Record source tag for each stable variable.
    for vid in values:
        source_ranges.setdefault(vid, [])
        if tid not in source_ranges[vid]:
            source_ranges[vid].append(tid)

    print(f"Finished {tid}")


def main():
    values = {}
    communes = {}
    periods = set()
    source_ranges = {}
    sources = []
    loaded = []

    for tid, kind in TABLES:
        try:
            process_table(
                tid, kind, values, communes, periods, source_ranges
            )
            loaded.append(tid)

            sources.append({
                "name": f"SCB tabell {tid}",
                "url": (
                    "https://statistikdatabasen.scb.se/"
                    f"api/v2/tables/{tid}/metadata?lang=sv"
                ),
                "note": "Hämtad via PxWebApi v2."
            })

        except Exception as e:
            print(f"ERROR {tid}: {type(e).__name__}: {e}")

    variables = variable_metadata(values, source_ranges)

    # Population is the target variable in the HTML app. If it is missing,
    # fail loudly rather than producing another misleading green/empty build.
    if not values.get("population"):
        raise RuntimeError(
            "No population observations were extracted. "
            "The Action must fail instead of publishing empty data.json."
        )

    result = {
        "generated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        ),
        "source": "SCB Statistikdatabasen, PxWebApi v2",
        "communes": [
            {"code": c, "name": communes[c]}
            for c in sorted(communes)
        ],
        "periods": sorted(periods),
        "variables": variables,
        "values": values,
        "sources": sources,
        "tables_loaded": loaded,
    }

    OUT.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    print("\n=== RESULT ===")
    print("Municipalities:", len(result["communes"]))
    print("Periods:", len(result["periods"]))
    print("Variables:", len(result["variables"]))
    print("Observations:")
    for k, by_commune in values.items():
        n = sum(len(x) for x in by_commune.values())
        print(f"  {k}: {n}")
    print("Tables loaded:", loaded)


if __name__ == "__main__":
    main()
