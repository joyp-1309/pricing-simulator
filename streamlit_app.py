"""
Onnyt Pricing & Cost Simulator — v4 (read-only).
Cost basis = Airtable 'Total (USD)' per head (already includes $99 seat) + allocated facility.
Full-entitlement cost (planning ceiling) — will run conservative vs actuals by design.
Revenue routing (three buckets):
  - Passthrough always (x1.00): Yellow Jacket Maintenance, Illium Telecom
  - Westward markup: Westward360 - Association / - Rental  (x1.00 thru Dec-2026, x1.09 full-COS from Jan-2027)
  - Fixed OG tiers ($1300/1600/2000); OG L4 = Labour x1.15 + seat + facility
SGA live from Airtable (Actual + Forecast), Total SGA drives net, breakdown displayed.
Onnyt-tagged staff excluded from roster. Bench = Floaters, live. Salary what-if recomputes labour only.
Deploy: replace streamlit_app.py, commit, refresh. Token in Secrets. No writes.
"""
import streamlit as st
from pyairtable import Api
from collections import defaultdict
import math
import pandas as pd

ROSTER_BASE="appdlOkT4HDXUmC5G"; SIM_BASE="appmJlnqk9l48XT5B"; HR_TABLE="HR Masterlist"
FX_DEFAULT=59.0; SEAT_FEE=99.0; FAC_BAGUIO=28000.0; FAC_CD_RATE=250.0
NIGHT_DIFF=0.100764; MERIT_2027=0.045; ONBOARD_ONETIME=229.0; RECRUIT_ONETIME=299.0

PASSTHROUGH_ALWAYS={"Yellow Jacket Maintenance","Illium Telecom"}
WW_MARKUP_MCS={"Westward360 - Association","Westward360 - Rental"}

MONTHS,_y,_m=[],2026,7
for _ in range(18):
    MONTHS.append(f"{_y:04d}-{_m:02d}"); _m+=1
    if _m>12: _m=1; _y+=1
MON_ABBR=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
def col_to_iso(c):
    try:
        a,y=c.strip().split(); return f"{int(y):04d}-{MON_ABBR.index(a)+1:02d}"
    except: return None

MID={"Accounting-1":759.32,"Accounting-2":1138.98,"ACM-1":735.59,"ACM-2":1162.71,"AP/AR-1":664.41,
     "APM-1":735.59,"APM-2":996.61,"CSR-1":717.80,"CSR-2":1014.41,"Compliance-1":593.22,
     "Executive Assistant -1":771.19,"Executive Assistant -2":1127.12,"HR-1":711.86,"HR-2":1275.42,
     "Illium-1":711.86,"Invest Admin-1":711.86,"Sales Admin-1":711.86,"Sales Admin-2":1903.05,
     "SLM-1":711.86,"Transitions-1":711.86,"YJM-1":735.59,"YJM-2":1032.20}
MID_BY_LEVEL={1:720.0,2:1120.0,3:1400.0,4:2000.0}
def level_of(gt):
    if gt and "-" in str(gt):
        s=str(gt).rsplit("-",1)[-1].strip()
        if s.isdigit(): return int(s)
    return 1
def tier_of(gt):
    if gt and "-" in str(gt): return str(gt).rsplit("-",1)[0].strip()
    return str(gt or "")
def mid_for(gt): return MID.get(gt,MID_BY_LEVEL.get(level_of(gt),900.0))

def sss_contribution(g):
    if g<=0: return 0.0
    msc=max(3000,min(35000,round(g/500.0)*500)); return max(265.0,min(3530.0,2030.0+(msc-20000)/500.0*50.0))
def loaded_php(g,hmo):
    if g<=0: return 0.0
    return g+g/12.0+sss_contribution(g)+min(0.025*g,2500.0)+200.0+(hmo or 0)+NIGHT_DIFF*g

def route(mc):
    """Return revenue bucket for an MC."""
    m=(mc or "")
    if m in PASSTHROUGH_ALWAYS: return "PASS"
    if m in WW_MARKUP_MCS: return "WWMK"
    ml=m.lower()
    if "onnyt floater" in ml: return "BENCH"
    if ml.strip()=="onnyt": return "EXCLUDE"
    if "after hours" in ml: return "AH"
    if "new client" in ml: return "NC"
    if "westward" in ml: return "WWMK"     # any other westward -> markup bucket
    return "OG"

@st.cache_data(ttl=300)
def load_roster(_token):
    tbl=Api(_token).table(ROSTER_BASE,HR_TABLE)
    def g(f,*names,d=None):
        for n in names:
            if n in f:
                v=f[n]; return v.get("name") if isinstance(v,dict) else v
        return d
    out=[]
    for r in tbl.all():
        f=r["fields"]
        status=str(g(f,"Employee Status","Status") or "").strip().lower()
        if status in ("resigned","terminated","n/a - resignation/termination",""): continue
        site=str(g(f,"Site","Office") or "").strip().upper()
        if site=="COL": continue
        mc=g(f,"Member Company","MC"); bucket=route(mc)
        if bucket=="EXCLUDE": continue
        gt=g(f,"Tier Level","Grp-Tier")
        total_usd=g(f,"Total (USD)","Total USD",d=None)
        labour=g(f,"Labour Cost $","Labour Cost",d=None)
        out.append({"eid":g(f,"Employee ID"),"mc":mc,"site":site,"grptier":gt,
            "tier":tier_of(gt),"level":level_of(gt),"generic":g(f,"Generic Role"),
            "gross":float(g(f,"Gross Monthly Salary (Php)","Gross Monthly Salary (PHP)",d=0) or 0),
            "hmo":float(g(f,"HMO",d=0) or 0),
            "total_usd":float(total_usd) if total_usd not in (None,"") else None,
            "labour_usd":float(labour) if labour not in (None,"") else None,
            "bucket":bucket,"kind":"existing"})
    return out

@st.cache_data(ttl=300)
def load_pricing(_token):
    out=[]
    for r in Api(_token).table(SIM_BASE,"Sim: Tier Price").all():
        f=r["fields"]
        def gg(n,d=None):
            v=f.get(n); return v.get("name") if isinstance(v,dict) else (v if v is not None else d)
        out.append({"group":gg("Client Group"),"level":gg("Tier"),"method":(gg("Pricing Method") or "").strip(),
            "flat":float(gg("Flat Price USD",0) or 0),"markup":float(gg("Markup %",0) or 0),
            "eff":str(gg("Effective Month","") or "")[:7]})
    return out

@st.cache_data(ttl=300)
def load_forecast_hc(_token):
    hires,exits=[],[]
    for r in Api(_token).table(SIM_BASE,"Sim: Forecast Headcount").all():
        f=r["fields"]
        def gg(n):
            v=f.get(n); return v.get("name") if isinstance(v,dict) else v
        gt=gg("Grp-Tier") or gg("Tier Level"); mc=gg("MC")
        rec={"mc":mc,"month":str(gg("Month") or "")[:7],"grptier":gt,"bucket":route(mc),
             "tier":tier_of(gt),"level":level_of(gt),"generic":None,
             "mid_usd":mid_for(gt),"kind":"forecast","site":"BAGUIO","gross":0,"hmo":0,
             "total_usd":None,"labour_usd":None}
        (hires if (gg("Event Type") or "").strip().lower()=="hire" else exits).append(rec)
    return hires,exits

@st.cache_data(ttl=300)
def load_sga(_token):
    api=Api(_token)
    def read(t):
        d={}
        for r in api.table(SIM_BASE,t).all():
            f=r["fields"]; lbl=str(f.get("Financial Row","")).strip()
            for col,val in f.items():
                iso=col_to_iso(col)
                if iso: d.setdefault(iso,{})[lbl]=val
        return d
    fc=read("Forecast SGA"); ac=read("Actual SGA")
    merged={m:dict(v) for m,v in fc.items()}; actual_months=set()
    for m,v in ac.items():
        merged.setdefault(m,{}).update(v); actual_months.add(m)
    return merged,actual_months

def price_map(pricing):
    pm={}
    for p in pricing:
        try: lvl=int(p["level"])
        except: lvl=1
        key=(p["group"],lvl)
        if key not in pm or p["eff"]>=pm[key]["eff"]: pm[key]=p
    return pm

def adj_active(a,m):
    if a.get("eff") and m<a["eff"]: return False
    if a.get("end") and m>a["end"]: return False
    return True
def scope_match(e,a):
    t,sv=a["scope_type"],str(a.get("scope_value") or "")
    if t=="All": return True
    if t=="Generic Role": return e.get("generic")==sv
    if t=="Tier": return e.get("tier")==sv
    if t=="Level": return str(e.get("level"))==sv
    if t=="Tier-Level": return e.get("grptier")==sv
    if t=="Member Company": return e.get("mc")==sv
    if t=="Client Group": return e.get("bucket")==sv
    return False

def active_roster(base,hires,exits,month,adjustments):
    roster=[dict(e) for e in base]
    for h in hires:
        if h["month"] and h["month"]<=month: roster.append(dict(h))
    for x in exits:
        if x["month"] and x["month"]<=month:
            for i,e in enumerate(roster):
                if e.get("bucket")==x["bucket"] and e.get("grptier")==x["grptier"]:
                    roster.pop(i); break
    for a in adjustments:
        if not adj_active(a,month): continue
        if a["lever"]=="Attrition":
            roster=[e for e in roster if not scope_match(e,a)]
        elif a["lever"]=="Promotion":
            for e in roster:
                if scope_match(e,a): e["level"]=int(a["value"])
        elif a["lever"]=="New Hire":
            try: mc,gt=str(a["scope_value"]).split("|")
            except: continue
            for _ in range(int(a["value"])):
                roster.append({"eid":f"NEW-{month}","mc":mc,"site":"BAGUIO","grptier":gt,
                    "tier":tier_of(gt),"level":level_of(gt),"generic":None,"gross":0,"hmo":0,
                    "total_usd":None,"labour_usd":None,"bucket":route(mc),"kind":"forecast",
                    "mid_usd":mid_for(gt),"hire_month":a["eff"]})
    return roster

def labour_cost(e,month,fx,adjustments):
    """Loaded labour cost EXCLUDING seat/facility. Uses Airtable Total(USD)-seat at baseline;
    recomputes from gross when a salary lever hits this person, or for forecast hires."""
    salaried=any(a["lever"] in ("Salary %","Salary PHP") and adj_active(a,month) and scope_match(e,a)
                 for a in adjustments)
    if e["kind"]=="forecast":
        base=e["mid_usd"]
    elif e.get("total_usd") is not None and not salaried:
        base=e["total_usd"]-SEAT_FEE     # Total(USD) includes seat; strip it, add back uniformly later
    else:
        gross=e["gross"]
        if month>="2027-07": gross*=(1+MERIT_2027)
        mult,addphp=1.0,0.0
        for a in adjustments:
            if not adj_active(a,month) or not scope_match(e,a): continue
            if a["lever"]=="Salary %": mult+=a["value"]/100.0
            if a["lever"]=="Salary PHP": addphp+=a["value"]
        base=loaded_php(gross*mult+addphp,e["hmo"])/fx
    # apply merit for Airtable-baseline path too (2027-07)
    if e["kind"]!="forecast" and e.get("total_usd") is not None and not salaried and month>="2027-07":
        base*=(1+MERIT_2027)
    return base

def run_month(month,base,hires,exits,pm,adjustments,fx,fees,sga_map,onshore_adj):
    roster=active_roster(base,hires,exits,month,adjustments)
    seat=fees.get("seat",SEAT_FEE); fac_b=fees.get("fac_baguio",FAC_BAGUIO); fac_r=fees.get("fac_rate",FAC_CD_RATE)
    for a in adjustments:
        if a["lever"]=="FX Override" and adj_active(a,month): fx=a["value"]
    price_over={}
    for a in adjustments:
        if a["lever"]=="Price Change" and adj_active(a,month): price_over[str(a["scope_value"])]=a["value"]
    cd=sum(1 for e in roster if e.get("site") in ("CEBU","DVO"))
    hc=len(roster); per_head_fac=(fac_b+cd*fac_r)/hc if hc else 0.0
    grp_rev=defaultdict(float); grp_cost=defaultdict(float); bench=0.0; onetime=0.0
    for e in roster:
        lab=labour_cost(e,month,fx,adjustments)
        full=lab+seat+per_head_fac        # full COS incl seat + facility
        b=e["bucket"]
        if e.get("hire_month")==month: onetime+=ONBOARD_ONETIME+RECRUIT_ONETIME
        if b=="BENCH": bench+=full; continue
        grp=("OG" if b=="OG" else "WW" if b in("WWMK","PASS") else b)   # display grouping
        grp_cost[grp]+=full; lvl=e.get("level") or 1
        if b=="PASS":
            rev=full                       # passthrough always: price = full COS
        elif b=="WWMK":
            mk=price_over.get("WW|all")
            mult=(1+mk) if mk is not None else (1.09 if month>="2027-01" else 1.00)
            rev=full*mult
        elif b in("OG","NC"):
            gname="OG MCs" if b=="OG" else "New Clients"
            pinfo=pm.get((gname,lvl))
            if lvl==4 and b=="OG":
                mk=price_over.get("OG|4",pinfo["markup"] if pinfo else 0.15)
                rev=lab*(1+mk)+seat+per_head_fac
            else:
                key=f"{b}|{lvl}"
                if key in price_over: rev=price_over[key]
                elif pinfo and pinfo["method"]=="flat": rev=pinfo["flat"]
                else: rev={1:1300,2:1600,3:2000}.get(lvl,2000)
        elif b=="AH": rev=price_over.get("AH|all",1600)
        else: rev=0.0
        grp_rev[grp]+=rev
    total_sga=float(sga_map.get(month,{}).get("Total SGA",0) or 0)+onshore_adj+onetime
    gp={g:grp_rev[g]-grp_cost[g] for g in ("OG","WW","NC","AH")}
    net=gp["OG"]+gp["WW"]+gp["NC"]-bench-total_sga
    bill=sum(1 for e in roster if e["bucket"] in ("OG","WWMK","PASS","NC"))
    contrib=(gp["OG"]+gp["WW"]+gp["NC"])/bill if bill else 0
    be=(bench+total_sga)/contrib if contrib>0 else float("inf")
    return {"month":month,"net":net,"gp":gp,"rev":dict(grp_rev),"cost":dict(grp_cost),
            "bench":bench,"sga":total_sga,"billable":bill,"contrib":contrib,"breakeven":be}

def project(base,hires,exits,pm,adjustments,fx,fees,sga_map,onshore_adj):
    return [run_month(m,base,hires,exits,pm,adjustments,fx,fees,sga_map,onshore_adj) for m in MONTHS]

# ================================================================== UI
st.set_page_config(page_title="Onnyt Pricing Simulator",layout="wide")
token=st.secrets.get("AIRTABLE_TOKEN")
if not token: st.error("No AIRTABLE_TOKEN in Streamlit secrets."); st.stop()
st.title("Onnyt Pricing & Cost Simulator")
st.caption("Read-only. Full-entitlement cost from Airtable (Total USD + facility). Runs conservative vs actuals by design. No writes.")

try:
    base=load_roster(token); pricing=load_pricing(token)
    hires,exits=load_forecast_hc(token); sga_map,actual_months=load_sga(token)
except Exception as e:
    st.error(f"Could not load data: {e}"); st.stop()
pm=price_map(pricing)
if "adjustments" not in st.session_state: st.session_state.adjustments=[]

with st.sidebar:
    st.header("Global")
    fx=st.number_input("FX (Peso : $1)",value=FX_DEFAULT,step=0.5)
    onshore_adj=st.number_input("Onshore cost +/- ($/mo)",value=0.0,step=1000.0)
    view=st.radio("View",["Internal (cost + margin)","Shareable (no cost/margin)"])
    st.divider(); st.header("Fees")
    seat=st.number_input("Seat fee $/head (in Total USD)",value=SEAT_FEE,step=1.0)
    fac_b=st.number_input("Facility — Baguio block $/mo",value=FAC_BAGUIO,step=500.0)
    fac_r=st.number_input("Facility — Cebu/Davao $/head",value=FAC_CD_RATE,step=10.0)
    fees={"seat":seat,"fac_baguio":fac_b,"fac_rate":fac_r}
    st.divider(); st.header("Pricing (editable)")
    og1=st.number_input("OG Level 1 $",value=float(pm.get(("OG MCs",1),{}).get("flat",1300)))
    og2=st.number_input("OG Level 2 $",value=float(pm.get(("OG MCs",2),{}).get("flat",1600)))
    og3=st.number_input("OG Level 3 $",value=float(pm.get(("OG MCs",3),{}).get("flat",2000)))
    og4mk=st.number_input("OG Level 4 markup %",value=15.0,step=1.0)/100
    wwmk=st.number_input("WW markup % (from Jan 2027)",value=9.0,step=1.0)/100

price_adj=[
    {"lever":"Price Change","scope_type":"","scope_value":"OG|1","value":og1,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"OG|2","value":og2,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"OG|3","value":og3,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"OG|4","value":og4mk,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"WW|all","value":wwmk,"eff":"2027-01","end":None},
]

st.subheader("Build a what-if")
c1,c2,c3,c4,c5=st.columns([1.3,1.3,1.3,1,1])
lever=c1.selectbox("Lever",["Salary %","Salary PHP","Promotion","New Hire","Attrition","FX Override"])
def opts(field): return sorted({str(e[field]) for e in base if e.get(field) not in (None,"")})
if lever=="New Hire":
    scope_type="New Hire"
    mc_sel=c2.selectbox("Member Company",opts("mc")); tl_sel=c3.selectbox("Tier-Level",opts("grptier"))
    sv=f"{mc_sel}|{tl_sel}"; val=c4.number_input("How many",value=1,step=1)
else:
    scope_type=c2.selectbox("Scope",["All","Generic Role","Tier","Level","Tier-Level","Member Company","Client Group"])
    if scope_type=="Generic Role": sv=c3.selectbox("Which",opts("generic"))
    elif scope_type=="Tier": sv=c3.selectbox("Which",opts("tier"))
    elif scope_type=="Level": sv=c3.selectbox("Which",["1","2","3","4"])
    elif scope_type=="Tier-Level": sv=c3.selectbox("Which",opts("grptier"))
    elif scope_type=="Member Company": sv=c3.selectbox("Which",opts("mc"))
    elif scope_type=="Client Group": sv=c3.selectbox("Which",["OG","WWMK","PASS","AH"])
    else: sv=c3.text_input("Which","")
    val=c4.number_input("Value",value=5.0)
eff=c5.selectbox("From month",MONTHS,index=MONTHS.index("2027-07"))
if st.button("➕ Add to scenario"):
    st.session_state.adjustments.append({"lever":lever,"scope_type":scope_type,"scope_value":sv,"value":val,"eff":eff,"end":None})
if st.session_state.adjustments:
    st.write("**Active what-ifs:**")
    for i,a in enumerate(st.session_state.adjustments):
        cc=st.columns([6,1]); cc[0].write(f"• {a['lever']} — {a['scope_type']} {a['scope_value']} = {a['value']} (from {a['eff']})")
        if cc[1].button("remove",key=f"rm{i}"): st.session_state.adjustments.pop(i); st.rerun()

user_adj=st.session_state.adjustments
baseline=project(base,hires,exits,pm,price_adj,fx,fees,sga_map,onshore_adj)
scenario=project(base,hires,exits,pm,price_adj+user_adj,fx,fees,sga_map,onshore_adj)

st.divider()
mi=st.select_slider("Month",options=MONTHS,value="2027-07")
idx=MONTHS.index(mi); r=scenario[idx]; b=baseline[idx]
tag="ACTUAL" if mi in actual_months else "FORECAST"
st.caption(f"Showing **{mi}** — SGA basis: **{tag}**")
m1,m2,m3=st.columns(3)
m1.metric("Net profit / (loss)",f"${r['net']:,.0f}",f"{r['net']-b['net']:+,.0f} vs baseline")
if math.isfinite(r["breakeven"]):
    gap=r["breakeven"]-r["billable"]
    m2.metric("Break-even FTEs vs actual",f"{r['breakeven']:.0f} / {r['billable']}",f"{'-' if gap<=0 else '+'}{abs(gap):.0f} FTEs",delta_color="inverse")
else: m2.metric("Break-even FTEs","unreachable")
m3.metric("Contribution / FTE",f"${r['contrib']:,.0f}",f"{r['contrib']-b['contrib']:+,.0f}")

st.subheader("24-month net profit")
st.line_chart(pd.DataFrame({"Month":MONTHS,"Baseline":[x["net"] for x in baseline],"Scenario":[x["net"] for x in scenario]}).set_index("Month"))

# ---- full 24-month SGA table ----
st.subheader("SGA — 24 months (Actual + Forecast)")
SGA_ROWS=["Onshore Payroll","Onnyt PH Corporate Payroll","JA Relocation Cost (Cebu Condo)",
          "OG Shared Staff Bonus","Staffing Continency (130K)","61000 - Personnel Expense",
          "62100 - Technology","62200 - Travel & Entertainment","62300 - Office Expense",
          "62600 - Professional Fees","62900 - Other G&A Expenses","Total SGA"]
def sga_val(m,lbl):
    row=sga_map.get(m,{})
    for k in row:
        if k.strip()==lbl.strip(): return float(row[k] or 0)
    return 0.0
sga_df=pd.DataFrame({m:[sga_val(m,lbl) for lbl in SGA_ROWS] for m in MONTHS},index=SGA_ROWS)
sga_df.columns=[f"{m}{'*' if m in actual_months else ''}" for m in MONTHS]
st.dataframe(sga_df.style.format("${:,.0f}"))
st.caption("*= actual month (from Actual SGA). Others forecast. Onshore shown combined; 'Total SGA' drives the net.")

# ---- sensitivity ----
st.subheader(f"Sensitivity — net at {mi} (WW markup × org salary %)")
ww_axis=[-0.05,0,0.05,0.09,0.14,0.20]; sal_axis=[0,2,4,6,8,10]; grid=[]
for s in sal_axis:
    rowv=[]
    for w in ww_axis:
        adj=[a for a in (price_adj+user_adj) if a["scope_value"]!="WW|all"]
        adj+=[{"lever":"Price Change","scope_type":"","scope_value":"WW|all","value":w,"eff":"2027-01","end":None},
              {"lever":"Salary %","scope_type":"All","scope_value":"","value":s,"eff":"2027-07","end":None}]
        rowv.append(run_month(mi,base,hires,exits,pm,adj,fx,fees,sga_map,onshore_adj)["net"])
    grid.append(rowv)
gdf=pd.DataFrame(grid,index=[f"Sal +{s}%" for s in sal_axis],columns=[f"WW {int(w*100)}%" for w in ww_axis])
st.dataframe(gdf.style.format("${:,.0f}").background_gradient(cmap="RdYlGn",axis=None))

# ---- group table ----
st.subheader("Where it comes from — "+("group (shareable)" if "Shareable" in view else "internal"))
if "Shareable" in view:
    st.table(pd.DataFrame([{"Group":g,"Revenue":f"${r['rev'].get(g,0):,.0f}"} for g in ("OG","WW","NC","AH")]))
else:
    tdf=pd.DataFrame([{"Group":g,"Revenue":r["rev"].get(g,0),"Cost":r["cost"].get(g,0),"Gross profit":r["gp"].get(g,0)} for g in ("OG","WW","NC","AH")])
    st.dataframe(tdf.style.format({"Revenue":"${:,.0f}","Cost":"${:,.0f}","Gross profit":"${:,.0f}"}),hide_index=True)
    st.caption(f"WW group = Westward markup + passthrough (Yellow Jacket, Illium show $0 GP). "
               f"Bench: ${r['bench']:,.0f} · Total SGA: ${r['sga']:,.0f} · Billable heads: {r['billable']}")

st.divider()
st.caption("Cost = Airtable Total(USD) incl seat + allocated facility ($28k Baguio + $250×Cebu/Davao). "
           "Passthrough always: Yellow Jacket, Illium. WW markup: Westward360 Assoc/Rental (×1.09 from Jan-2027). "
           "OG fixed tiers; L4 = labour×1.15 + seat + facility. Full entitlement — conservative vs actual. Read-only.")
