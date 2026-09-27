import json, requests, re
from pathlib import Path

API="https://api.scb.se/OV0104/v1/doris/sv/ssd"
OUT=Path("data.json")

# Monthly municipal indicators chosen for relevance to population growth,
# employment/job growth and attractiveness. The script resolves dimension codes
# from SCB metadata rather than hard-coding most codes.
TABLES = [
  {"id":"population_old","path":"/BE/BE0101/BE0101G/ManadBefStatRegion","metric":"folkmängd"},
  {"id":"population","path":"/BE/BE0101/BE0101G/MBefStatRegionCKM","metric":"folkmängd"},
  {"id":"birth_surplus","path":"/BE/BE0101/BE0101G/MBefStatRegionCKM","metric":"födelseöverskott"},
  {"id":"domestic_migration","path":"/BE/BE0101/BE0101G/MBefStatRegionCKM","metric":"samtliga inrikes inflyttningar"},
  {"id":"immigration","path":"/BE/BE0101/BE0101G/MBefStatRegionCKM","metric":"invandringar"},
  {"id":"emigration","path":"/BE/BE0101/BE0101G/MBefStatRegionCKM","metric":"utvandringar"},
  {"id":"net_migration","path":"/BE/BE0101/BE0101G/MBefStatRegionCKM","metric":"totalt flyttningsöverskott"},
  {"id":"employment","path":"/AM/AM0210/AM0210A/ArbStatusM","metric":"antal sysselsatta"},
  {"id":"unemployment","path":"/AM/AM0210/AM0210A/ArbStatusM","metric":"antal arbetslösa"},
  {"id":"labor_force","path":"/AM/AM0210/AM0210A/ArbStatusM","metric":"antal sysselsatta och arbetslösa (arbetskraften)"},
  {"id":"unemployment_rate","path":"/AM/AM0210/AM0210A/ArbStatusM","metric":"arbetslöshet"},
  {"id":"employment_rate","path":"/AM/AM0210/AM0210A/ArbStatusM","metric":"sysselsättningsgrad"},
  {"id":"participation_rate","path":"/AM/AM0210/AM0210A/ArbStatusM","metric":"arbetskraftsdeltagande"},
  {"id":"disposable_income_median","path":"/HE/HE0110/HE0110M/MistTab1","metric":"Medianvärde, tkr"},
  {"id":"jobs_total","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"A-U+US Total"},
  {"id":"jobs_manufacturing","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"B+C tillverkningsindustri; gruvor och mineralutvinningsindustri"},
  {"id":"jobs_construction","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"F byggverksamhet"},
  {"id":"jobs_trade","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"G handel"},
  {"id":"jobs_transport","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"H transport och magasinering"},
  {"id":"jobs_ict","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"J informations- och kommunikationsverksamhet"},
  {"id":"jobs_business","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"M+N företagstjänster"},
  {"id":"jobs_public","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"O offentlig förvaltning och försvar"},
  {"id":"jobs_education","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"P utbildning"},
  {"id":"jobs_health","path":"/AM/AM0210/AM0210B/ArbStDoNMNN","metric":"sysselsatta efter arbetsställets belägenhet","industry":"Q vård och omsorg; sociala tjänster"},
]

NAMES={
"population":("Befolkning","Befolkning","befolkning"),
"birth_surplus":("Befolkning","Födelseöverskott","demografi"),
"domestic_migration":("Befolkning","Inrikes inflyttningar","attraktivitet"),
"immigration":("Befolkning","Invandringar","attraktivitet"),
"emigration":("Befolkning","Utvandringar","attraktivitet"),
"net_migration":("Befolkning","Totalt flyttningsöverskott","attraktivitet"),
"employment":("Arbetsmarknad","Sysselsatta","jobb"),
"unemployment":("Arbetsmarknad","Arbetslösa","jobb"),
"labor_force":("Arbetsmarknad","Arbetskraft","jobb"),
"unemployment_rate":("Arbetsmarknad","Arbetslöshet","jobb"),
"employment_rate":("Arbetsmarknad","Sysselsättningsgrad","jobb"),
"participation_rate":("Arbetsmarknad","Arbetskraftsdeltagande","jobb"),
"disposable_income_median":("Attraktivitet","Median disponibel inkomst","attraktivitet"),
"jobs_total":("Jobb efter bransch","Sysselsatta – arbetsställets belägenhet, totalt","jobb"),
"jobs_manufacturing":("Jobb efter bransch","Tillverkningsindustri + gruvor","jobb"),
"jobs_construction":("Jobb efter bransch","Byggverksamhet","jobb"),
"jobs_trade":("Jobb efter bransch","Handel","jobb"),
"jobs_transport":("Jobb efter bransch","Transport och magasinering","jobb"),
"jobs_ict":("Jobb efter bransch","Information och kommunikation","jobb"),
"jobs_business":("Jobb efter bransch","Företagstjänster","jobb"),
"jobs_public":("Jobb efter bransch","Offentlig förvaltning och försvar","jobb"),
"jobs_education":("Jobb efter bransch","Utbildning","jobb"),
"jobs_health":("Jobb efter bransch","Vård och omsorg","jobb"),
}

def meta(path):
    r=requests.get(API+path,timeout=60); r.raise_for_status(); return r.json()

def pick(v, wanted=None):
    vals=v["values"]; texts=v.get("valueTexts",[])
    if wanted:
        for i,t in enumerate(texts):
            if t.lower()==wanted.lower(): return vals[i]
        for i,t in enumerate(texts):
            if wanted.lower() in t.lower(): return vals[i]
    for i,t in enumerate(texts):
        if re.search(r"\btotal\b|totalt",t,re.I): return vals[i]
    return vals[0]

def find_var(m, patterns):
    for v in m["variables"]:
        t=v["text"].lower()
        if any(p.lower() in t for p in patterns): return v
    raise ValueError(f"Dimension not found: {patterns}")

def request_metric(t, m, regions, months):
    rv=find_var(m,["region","kommun"])
    tv=find_var(m,["månad"])
    metric=find_var(m,["förändringar","tabellinnehåll"])
    metric_code=pick(metric,t["metric"])
    q=[
      {"code":rv["code"],"selection":{"filter":"item","values":regions}},
      {"code":metric["code"],"selection":{"filter":"item","values":[metric_code]}}
    ]
    for v in m["variables"]:
        if v["code"] in (rv["code"],tv["code"],metric["code"]): continue
        wanted=t.get("industry") if "näringsgren" in v["text"].lower() else None
        q.append({"code":v["code"],"selection":{"filter":"item","values":[pick(v,wanted)]}})
    q.append({"code":tv["code"],"selection":{"filter":"item","values":months}})
    r=requests.post(API+t["path"],json={"query":q,"response":{"format":"json-stat2"}},timeout=180)
    r.raise_for_status();return r.json(),rv["code"],tv["code"]

def unpack(js):
    dims=js["id"];sizes=js["size"];vals=js["value"];codes={}
    for d in dims:
        idx=js["dimension"][d]["category"]["index"]
        codes[d]=idx if isinstance(idx,list) else [k for k,_ in sorted(idx.items(),key=lambda kv:kv[1])]
    rows=[]
    def rec(pos,coords):
        if pos==len(dims):
            flat=0;mul=1
            for i in range(len(dims)-1,-1,-1): flat+=coords[i]*mul;mul*=sizes[i]
            rows.append((coords[:],vals[flat]));return
        for i in range(sizes[pos]):rec(pos+1,coords+[i])
    rec(0,[])
    return dims,codes,rows

def main():
    metas={}; regions=None; months=None
    for t in TABLES:
        if t["path"] not in metas: metas[t["path"]]=meta(t["path"])
    pm=metas[TABLES[0]["path"]]
    rv=find_var(pm,["region","kommun"]); tv=find_var(pm,["månad"])
    regions=[c for c in rv["values"] if re.fullmatch(r"\d{4}",str(c))]; region_names=dict(zip(rv["values"],rv["valueTexts"]))
    months=tv["values"]
    # Limit to months available in the longest current municipal monthly series.
    values={}
    for t in TABLES:
        m=metas[t["path"]]
        try:
            js,rc,tc=request_metric(t,m,regions,months)
        except Exception as e:
            print("SKIP",t["id"],e);continue
        dims,codes,rows=unpack(js); ri=dims.index(rc);ti=dims.index(tc)
        d={}
        for coords,v in rows:
            if v is None:continue
            reg=codes[rc][coords[ri]];mo=codes[tc][coords[ti]]
            d.setdefault(reg,{})[mo]=v
        values[t["id"]]=d
    variables=[]
    for t in TABLES:
        if t["id"] in values:
            g,n,tag=NAMES[t["id"]]
            variables.append({"id":t["id"],"name":n,"group":g,"tags":[tag],"from":min(values[t["id"]][r] for r in values[t["id"]] for _ in [0] if values[t["id"]][r]),"to":max(values[t["id"]][r] for r in values[t["id"]] for _ in [0] if values[t["id"]][r])})
    # Compute common period availability per variable.
    for v in variables:
        ts=[m for r in values[v["id"]].values() for m in r]
        v["from"]=min(ts);v["to"]=max(ts)
    out={"periods":months,"communes":[{"code":c,"name":region_names[c]} for c in regions if c!="00"],
         "values":values,"variables":variables,
         "sources":[
           {"name":"SCB – Befolkningsstatistik månadsvis","url":"https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__BE__BE0101__BE0101G/MBefStatRegionCKM/","note":"Befolkning och folkökning, månad."},
           {"name":"SCB – BAS månadsvis","url":"https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__AM__AM0210__AM0210A/ArbStatusM/","note":"Sysselsättning, arbetslöshet och arbetskraft, månad."},
           {"name":"SCB – sysselsatta efter bransch","url":"https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__AM__AM0210__AM0210B/ArbStDoNMNN/","note":"Sysselsatta efter arbetsställets belägenhet och SNI, månad."}
         ]}
    OUT.write_text(json.dumps(out,ensure_ascii=False),encoding="utf-8")
if __name__=="__main__":main()
