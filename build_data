"""
Build normalized monthly municipal data for Kommunal tillväxtanalys.

SCB source: PxWebApi v2.
The output is deliberately shaped to match index.html:
  periods, communes, variables, values, sources.

Core monthly municipal tables:
  TAB1625  population 2000-2024
  TAB6473  population 2025-
  TAB6260  labour market status, preliminary
  TAB4718  ongoing employments by region
  TAB4723  ongoing employments in business sector by region/industry
"""

from __future__ import annotations
import json, re, time, unicodedata
from pathlib import Path
from urllib.parse import quote
import requests

API = "https://statistikdatabasen.scb.se/api/v2"
OUT = Path("data.json")
S = requests.Session()
S.headers.update({"User-Agent": "kommunal-tillvaxtanalys/2.0"})

TABLES = [
    ("TAB1625", "Befolkningsstatistik efter region och kön. Månad 2000M01–2024M12", "population"),
    ("TAB6473", "Befolkningsstatistik efter region och kön. Månad 2025M01–senaste", "population"),
    ("TAB6260", "Arbetsmarknadsstatus efter region, kön, ålder och födelseregion. Preliminär statistik. Månad", "labour"),
    ("TAB4718", "Antal pågående anställningar efter kön, region och sektor. Månad", "employment"),
    ("TAB4723", "Antal pågående anställningar i näringslivet efter region och näringsgren. Månad", "industry"),
]

def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.strip().lower())

def get(url, **params):
    r = S.get(url, params=params, timeout=90)
    r.raise_for_status()
    return r

def post(url, payload):
    r = S.post(url, params={"lang": "sv", "outputFormat": "json-stat2"},
               json=payload, timeout=180)
    r.raise_for_status()
    return r.json()

def metadata(tid):
    return get(f"{API}/tables/{quote(tid, safe='')}/metadata", lang="sv").json()

def dims(meta):
    return meta.get("dimension") or {}

def codes_labels(d):
    cat = (d.get("category") or {})
    idx = cat.get("index") or {}
    labels = cat.get("label") or {}
    if isinstance(idx, list):
        codes = idx
    else:
        codes = [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]
    return [(str(c), str(labels.get(c, c))) for c in codes]

def role_first(meta, key):
    r = meta.get("role") or {}
    x = r.get(key)
    if isinstance(x, list):
        return x[0] if x else None
    return x

def find_dim(meta, words, preferred_codes=()):
    for code, d in dims(meta).items():
        n = norm(d.get("label", code))
        if code in preferred_codes or any(w in n for w in words):
            return code
    return None

def get_region_dim(meta):
    r = role_first(meta, "geo")
    return r if r in dims(meta) else find_dim(meta, ["region", "kommun"], ("Region", "region"))

def get_time_dim(meta):
    t = role_first(meta, "time")
    return t if t in dims(meta) else find_dim(meta, ["manad", "month", "tid"], ("Tid", "tid", "månad"))

def get_metric_dim(meta):
    r = role_first(meta, "metric")
    return r if r in dims(meta) else find_dim(
        meta, ["tabellinnehall", "contents", "forandringar", "forandring", "nyckeltal", "matt"],
        ("ContentsCode", "Forandringar", "tabellinnehåll")
    )

def choose_municipalities(meta, region_dim):
    out = []
    for c, label in codes_labels(dims(meta)[region_dim]):
        m = re.match(r"^\s*(\d{4})\b", label)
        if m:
            out.append((c, label))
        elif re.fullmatch(r"\d{4}", c):
            out.append((c, label))
    return out

def dim_eliminated(d):
    return bool((d.get("extension") or {}).get("elimination", False))

def choose_dimension_values(code, d):
    pairs = codes_labels(d)
    if not pairs:
        return []

    # Prefer an explicit total for dimensions such as sex, age, sector and birth region.
    preferred = ["totalt", "samtliga", "alla", "total", "bada kon", "bada"]
    for c, label in pairs:
        n = norm(label)
        if any(p in n for p in preferred):
            return [c]

    # For industry dimensions, retaining all categories is useful and still fits
    # the six-month batching used below.
    ncode = norm(code)
    nlabel = norm(d.get("label", code))
    if any(x in (ncode + " " + nlabel) for x in ["naring", "sni", "industry"]):
        return [c for c, _ in pairs]

    # If there is no obvious total, use the first category rather than silently
    # multiplying the result by every possible breakdown.
    return [pairs[0][0]]

def choose_metric_pairs(meta, metric_dim, kind):
    pairs = codes_labels(dims(meta)[metric_dim])
    if kind == "population":
        wanted_words = [
            "folkmangd", "folk okning", "folkning", "fodd", "doda",
            "fodelseoverskott", "inrikes inflytt", "inrikes utflytt",
            "invandr", "utvand", "flyttningsoverskott", "justeringspost"
        ]
    elif kind == "labour":
        wanted_words = [
            "sysselsatta", "arbetslosa", "arbetskraft", "arbetsloshet",
            "arbetskraftsdeltagande", "sysselsattningsgrad", "personer ej"
        ]
    else:
        wanted_words = ["antal pagaende anstallningar", "arlig forandring"]

    selected = []
    for c, label in pairs:
        n = norm(label)
        if any(w in n for w in wanted_words):
            selected.append((c, label))
    return selected or pairs[:1]

def safe_id(kind, label):
    n = norm(label)
    if kind == "population":
        mapping = [
            ("folkmangd", "population"),
            ("folk okning", "population_growth"),
            ("fodelseoverskott", "birth_surplus"),
            ("fodd", "births"),
            ("doda", "deaths"),
            ("samtliga inrikes inflytt", "domestic_in_migration"),
            ("inrikes inflytt", "domestic_in_migration"),
            ("samtliga inrikes utflytt", "domestic_out_migration"),
            ("inrikes utflytt", "domestic_out_migration"),
            ("invandringsoverskott", "net_immigration"),
            ("invandring", "immigration"),
            ("utvandring", "emigration"),
            ("flyttningsoverskott", "net_migration"),
            ("justeringspost", "population_adjustment"),
        ]
    elif kind == "labour":
        mapping = [
            ("antal sysselsatta", "employed"),
            ("sysselsattningsgrad", "employment_rate"),
            ("antal arbetslosa", "unemployed"),
            ("arbetsloshet", "unemployment_rate"),
            ("antal sysselsatta och arbetslosa", "labour_force"),
            ("arbetskraftsdeltagande", "participation_rate"),
            ("antal personer ej i arbetskraften", "outside_labour_force"),
            ("antal totalt", "population_labour"),
        ]
    else:
        mapping = [
            ("antal pagaende anstallningar", "jobs"),
            ("arlig forandring", "jobs_yoy_pct"),
        ]
    for needle, out in mapping:
        if needle in n:
            return out
    return re.sub(r"[^a-z0-9]+", "_", n).strip("_")[:60]

def jsonstat_rows(obj):
    ids = obj.get("id") or []
    sizes = obj.get("size") or []
    dimensions = obj.get("dimension") or {}
    vals = obj.get("value") or []
    cats = {}
    for d in ids:
        cat = (dimensions.get(d) or {}).get("category") or {}
        idx = cat.get("index") or {}
        labels = cat.get("label") or {}
        ordered = idx if isinstance(idx, list) else [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]
        cats[d] = [(str(c), str(labels.get(c, c))) for c in ordered]

    rows = []
    if not ids or not sizes:
        return rows
    for flat, value in enumerate(vals):
        if value is None:
            continue
        rem = flat
        coord = [0] * len(ids)
        for i in range(len(ids) - 1, -1, -1):
            coord[i] = rem % sizes[i]
            rem //= sizes[i]
        row = {"value": value}
        for i, d in enumerate(ids):
            code, label = cats[d][coord[i]]
            row[d] = code
            row[d + "_text"] = label
        rows.append(row)
    return rows

def query_table(tid, meta, kind):
    rd, td, md = get_region_dim(meta), get_time_dim(meta), get_metric_dim(meta)
    if not rd or not td or not md:
        raise RuntimeError(f"Could not identify region/time/metric: {rd}, {td}, {md}")

    regions = choose_municipalities(meta, rd)
    times = codes_labels(dims(meta)[td])
    metrics = choose_metric_pairs(meta, md, kind)
    if not regions or not times or not metrics:
        raise RuntimeError("Missing municipalities, periods or metrics")

    rows = []
    for start in range(0, len(times), 6):
        tchunk = [c for c, _ in times[start:start+6]]
        selection = [
            {"variableCode": rd, "valueCodes": [c for c, _ in regions]},
            {"variableCode": td, "valueCodes": tchunk},
            {"variableCode": md, "valueCodes": [c for c, _ in metrics]},
        ]

        for code, d in dims(meta).items():
            if code in {rd, td, md} or dim_eliminated(d):
                continue
            vals = choose_dimension_values(code, d)
            if vals:
                selection.append({"variableCode": code, "valueCodes": vals})

        obj = post(f"{API}/tables/{quote(tid, safe='')}/data", {"selection": selection})
        rows.extend(jsonstat_rows(obj))
        print(f"  {tid}: months {start+1}-{min(start+6,len(times))}/{len(times)}; rows={len(rows)}")
        time.sleep(0.25)

    return rows, rd, td, md, regions, metrics

def main():
    values, variables = {}, {}
    commune_map, all_periods = {}, set()
    sources, loaded = [], []

    for tid, fallback_title, kind in TABLES:
        try:
            print(f"\nMETADATA {tid}")
            meta = metadata(tid)
            title = meta.get("label") or meta.get("title") or fallback_title
            rows, rd, td, md, regions, metrics = query_table(tid, meta, kind)
            print(f"  fetched {len(rows)} rows")

            sources.append({
                "name": f"SCB {title} ({tid})",
                "url": f"https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/",
                "note": "Hämtad via PxWebApi v2."
            })

            for c, label in regions:
                m = re.match(r"^\s*(\d{4})\b", label)
                if m:
                    code = m.group(1)
                    commune_map[code] = re.sub(r"^\s*\d{4}\s*:?\s*", "", label).strip() or label

            for mc, mlabel in metrics:
                var_id = safe_id(kind, mlabel)
                bucket = values.setdefault(var_id, {})
                for row in rows:
                    if row.get(md) != mc:
                        continue
                    txt = str(row.get(rd + "_text", row.get(rd, "")))
                    m = re.match(r"^\s*(\d{4})\b", txt)
                    if not m:
                        continue
                    code, t = m.group(1), str(row.get(td))
                    if re.fullmatch(r"\d{4}M\d{2}", t):
                        all_periods.add(t)
                        bucket.setdefault(code, {})[t] = row.get("value")

                if bucket:
                    ps = [t for cm in bucket.values() for t in cm]
                    variables[var_id] = {
                        "id": var_id,
                        "name": mlabel,
                        "group": {
                            "population": "Befolkning",
                            "labour": "Arbetsmarknad",
                            "employment": "Jobb och anställningar",
                            "industry": "Jobb efter näringsgren",
                        }[kind],
                        "tags": [tid, kind],
                        "from": min(ps),
                        "to": max(ps),
                    }
            loaded.append(tid)
        except Exception as e:
            print(f"ERROR {tid}: {e}")

    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "SCB Statistikdatabasen, PxWebApi v2",
        "communes": [{"code": c, "name": commune_map[c]} for c in sorted(commune_map)],
        "periods": sorted(all_periods),
        "variables": sorted(variables.values(), key=lambda x: (x["group"], x["name"])),
        "values": values,
        "sources": sources,
        "tables_loaded": loaded,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\nWrote {OUT}: municipalities={len(result['communes'])}, months={len(result['periods'])}, variables={len(result['variables'])}")
    print("Tables loaded:", loaded)

if __name__ == "__main__":
    main()
