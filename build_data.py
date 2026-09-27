"""
Build SCB monthly municipal data for the population-growth analysis.

The script intentionally discovers table metadata before querying data.
This avoids hard-coding variable codes that can change between SCB table
versions. It uses PxWebApi v2, which SCB launched in October 2025.

Output:
    data.json
"""

from __future__ import annotations
import json, re, time
from pathlib import Path
from urllib.parse import quote
import requests

API = "https://statistikdatabasen.scb.se/api/v2"
OUT = Path("data.json")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "kommunal-tillvaxtanalys/1.0"})

# Search terms used to discover the small set of monthly municipal tables.
# We then inspect metadata and keep tables with Region + monthly time.
SEARCHES = [
    "befolkningsstatistik månad",
    "arbetsmarknadsstatus månad",
    "pågående anställningar månad",
    "lönesumma månad",
]

# Words used to identify useful tables/contents.
KEEP_TABLE_WORDS = [
    "befolkning", "arbetsmarknadsstatus", "anställningar", "lönesumma",
    "arbetsställen", "sysselsatta"
]

DROP_TABLE_WORDS = [
    "län", "riket", "kvartal", "år ", "års", "veck", "dag"
]

def get(url, **params):
    r = SESSION.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r

def post(url, payload, **params):
    r = SESSION.post(url, params=params, json=payload, timeout=120)
    r.raise_for_status()
    return r

def norm(s):
    return re.sub(r"\s+", " ", str(s).strip().lower())

def table_id(t):
    return t.get("id") or t.get("tableId") or t.get("table_id") or t.get("matrix") or t.get("matrixId")

def table_title(t):
    return t.get("title") or t.get("text") or t.get("label") or t.get("name") or ""

def flatten_tables(obj):
    """Accept several PxWeb v2 list response shapes."""
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if not isinstance(obj, dict):
        return []
    for key in ("tables", "items", "results", "data"):
        if isinstance(obj.get(key), list):
            return [x for x in obj[key] if isinstance(x, dict)]
    return []

def discover_tables():
    found = {}
    for q in SEARCHES:
        try:
            r = get(f"{API}/tables", lang="sv", query=q)
            obj = r.json()
            for t in flatten_tables(obj):
                tid = table_id(t)
                title = table_title(t)
                if tid and title:
                    found[str(tid)] = {"id": str(tid), "title": title}
        except Exception as e:
            print(f"SEARCH WARNING: {q}: {e}")
    return list(found.values())

def metadata(tid):
    r = get(f"{API}/tables/{quote(tid, safe='')}", lang="sv")
    obj = r.json()
    # Some installations put variables under dimensions/variables.
    vars_ = obj.get("variables") or obj.get("dimensions") or []
    if not vars_ and isinstance(obj.get("table"), dict):
        vars_ = obj["table"].get("variables") or obj["table"].get("dimensions") or []
    return obj, vars_

def vcode(v):
    return v.get("code") or v.get("variableCode") or v.get("id") or v.get("key")

def vtext(v):
    return v.get("text") or v.get("label") or v.get("name") or vcode(v) or ""

def vvalues(v):
    vals = v.get("values") or v.get("valueCodes") or []
    texts = v.get("valueTexts") or v.get("valueLabels") or []
    if isinstance(vals, dict):
        vals = list(vals.keys())
    return list(vals), list(texts)

def classify_variables(vars_):
    region = timevar = content = None
    for v in vars_:
        txt = norm(vtext(v))
        code = norm(vcode(v) or "")
        vals, texts = vvalues(v)
        sample = " ".join(map(norm, texts[:20]))
        if region is None and ("region" in txt or "kommun" in txt or code in {"region", "kommunkod"}):
            region = v
        if timevar is None and ("månad" in txt or "month" in txt or code in {"tid", "månad", "month"}):
            timevar = v
        if content is None and (
            "förändring" in txt or "förändringar" in txt or
            "tabellinnehåll" in txt or "contents" in txt or "mått" in txt
        ):
            content = v
    return region, timevar, content

def choose_region_codes(v):
    codes, texts = vvalues(v)
    out=[]
    for c,t in zip(codes,texts or codes):
        s=norm(t)
        # Swedish municipality codes are normally four digits.
        if re.fullmatch(r"\d{4}.*", str(t).strip()) or re.fullmatch(r"\d{4}", str(c).strip()):
            out.append(c)
    return out

def choose_content_codes(v, title):
    codes, texts = vvalues(v)
    # Prefer total/count/overall contents. Keep several useful contents,
    # but never explode the cell limit.
    selected=[]
    for c,t in zip(codes,texts or codes):
        s=norm(t)
        if any(k in s for k in [
            "folkmängd", "folkökning", "födda", "döda",
            "flytt", "sysselsatta", "arbetslösa", "arbetskraft",
            "anställningar", "lönesumma"
        ]):
            selected.append(c)
    if selected:
        return selected[:40]
    # Fall back to the first value (some tables have a single contents code).
    return codes[:1]

def choose_other_dimension(v):
    codes, texts = vvalues(v)
    if not codes:
        return []
    for c,t in zip(codes,texts or codes):
        if norm(t) in {"totalt", "samtliga", "total", "alla"}:
            return [c]
    return codes[:1]

def jsonstat_rows(obj):
    ids=obj.get("id", [])
    sizes=obj.get("size", [])
    dims=obj.get("dimension", {})
    values=obj.get("value", [])
    categories={}
    for d in ids:
        cat=(dims.get(d) or {}).get("category") or {}
        idx=cat.get("index", {})
        if isinstance(idx, list):
            codes=idx
        else:
            codes=[k for k,_ in sorted(idx.items(), key=lambda kv: kv[1])]
        labels=cat.get("label", {})
        categories[d]=[(c, labels.get(c,c) if isinstance(labels,dict) else c) for c in codes]

    rows=[]
    if not ids or not sizes:
        return rows
    for flat,val in enumerate(values):
        rem=flat
        coords=[0]*len(ids)
        for i in range(len(ids)-1,-1,-1):
            coords[i]=rem % sizes[i]
            rem//=sizes[i]
        row={}
        for i,d in enumerate(ids):
            code,label=categories[d][coords[i]]
            row[d]=code
            row[d+"_text"]=label
        row["value"]=val
        rows.append(row)
    return rows

def query_data(tid, vars_, region_codes):
    """
    Fetch in small time chunks. SCB limits a response to 150,000 cells,
    so 6 months is deliberately conservative for municipal monthly tables.
    """
    rv,tv,cv=classify_variables(vars_)
    if not rv or not tv:
        raise RuntimeError("Could not identify Region and month variables")

    rc=vcode(rv); tc=vcode(tv)
    tvals,_=vvalues(tv)
    content_codes=choose_content_codes(cv, "") if cv else []

    all_rows=[]
    chunk_size=6

    for start in range(0,len(tvals),chunk_size):
        tchunk=tvals[start:start+chunk_size]
        selection=[
            {"variableCode":rc, "valueCodes":region_codes},
            {"variableCode":tc, "valueCodes":tchunk},
        ]

        if cv:
            selection.append({
                "variableCode":vcode(cv),
                "valueCodes":content_codes
            })

        for v in vars_:
            c=vcode(v)
            if c in {rc,tc} or (cv and c==vcode(cv)):
                continue
            vals=choose_other_dimension(v)
            if vals:
                selection.append({"variableCode":c,"valueCodes":vals})

        payload={"selection":selection}
        r=post(
            f"{API}/tables/{quote(tid,safe='')}/data",
            payload,
            lang="sv",
            outputFormat="json-stat2"
        )
        obj=r.json()
        all_rows.extend(jsonstat_rows(obj))
        time.sleep(0.2)

    return {"rows": all_rows}


def main():
    tables=discover_tables()
    print(f"Discovered {len(tables)} candidate tables")

    # Fetch metadata and keep monthly tables with a municipal region dimension.
    candidates=[]
    for t in tables:
        try:
            obj,vars_=metadata(t["id"])
            rv,tv,cv=classify_variables(vars_)
            title=table_title(obj) or t["title"]
            title_n=norm(title)
            if not rv or not tv:
                continue
            if any(w in title_n for w in DROP_TABLE_WORDS):
                continue
            if not any(w in title_n for w in KEEP_TABLE_WORDS):
                continue
            regions=choose_region_codes(rv)
            if len(regions) < 200:
                continue
            candidates.append((t["id"],title,vars_,regions))
            print("KEEP:",t["id"],title)
        except Exception as e:
            print("METADATA WARNING:",t["id"],e)

    # Deduplicate and cap to avoid duplicate versions of the same subject.
    seen=set()
    datasets=[]
    for tid,title,vars_,regions in candidates:
        key=norm(title)
        if key in seen:
            continue
        seen.add(key)
        try:
            print("FETCH:",tid,title)
            js=query_data(tid,vars_,regions)
            rows=js.get('rows', [])
            if rows:
                datasets.append({
                    "table_id":tid,
                    "title":title,
                    "rows":rows,
                    "dimensions":[
                        {"code":vcode(v),"text":vtext(v),"values":vvalues(v)[0]}
                        for v in vars_
                    ],
                })
            time.sleep(0.5)
        except Exception as e:
            print("DATA WARNING:",tid,e)

    result={
        "generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "source":"SCB Statistikdatabasen, PxWebApi v2",
        "datasets":datasets,
    }
    OUT.write_text(json.dumps(result,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    print(f"Wrote {OUT} with {len(datasets)} datasets")

if __name__=="__main__":
    main()
