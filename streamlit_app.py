"""
Onnyt Pricing & Cost Simulator — v5 (read-only). Built from ONNYT_SIMULATOR_PLAYBOOK.md.
Historical roster reconstructed from HR Masterlist (active) + Attrition (exited): a person is
active in month M if Hire Date <= end(M) AND (still active OR Attrition Effective Date > end(M)).
Cost = Total(USD) [incl seat] + allocated facility. Onnyt excluded (in SGA). Bench = Floaters.
Forward months: latest actual roster + Forecast Headcount Hires/Exits (Group x Level counts) at
per-Level averages. SGA live-spliced (Actual+Forecast), breakdown shown. Horizon Jan-2026..Dec-2027.
"""
import streamlit as st
from pyairtable import Api
from collections import defaultdict
import math, datetime
import pandas as pd

ROSTER_BASE="appdlOkT4HDXUmC5G"; SIM_BASE="appmJlnqk9l48XT5B"
HR_TABLE="HR Masterlist"; ATTR_TABLE="tblYp8YwzoIAYJ4UZ"
ACT_SGA="Actual SGA"; FC_SGA="Forecast SGA"
ACT_HC="tblBIY3inpam32fKk"; FC_HC="tblPlQcsQ26yp4N17"
FX_DEFAULT=59.0; SEAT_FEE=99.0; FAC_BAGUIO=28000.0; FAC_CD_RATE=250.0
NIGHT_DIFF=0.100764; MERIT_2027=0.045; ONBOARD=229.0; RECRUIT=299.0
PASSTHROUGH_ALWAYS={"Yellow Jacket Maintenance","Illium Telecom"}
WW_MARKUP_MCS={"Westward360 - Association","Westward360 - Rental"}

# 24-month horizon Jan 2026 -> Dec 2027
MONTHS=[]; _y,_m=2026,1
for _ in range(24):
    MONTHS.append(f"{_y:04d}-{_m:02d}"); _m+=1
    if _m>12: _m=1; _y+=1
MON_ABBR=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
def col_to_iso(c):
    try:
        a,y=str(c).strip().split(); return f"{int(y):04d}-{MON_ABBR.index(a)+1:02d}"
    except: return None
def iso_to_col(iso):
    y,m=iso.split("-"); return f"{MON_ABBR[int(m)-1]} {y}"
def end_of(iso):
    y,m=map(int,iso.split("-"))
    return datetime.date(y+ (m==12), (m%12)+1, 1) - datetime.timedelta(days=1)
def parse_date(v):
    if not v: return None
    try: return datetime.date.fromisoformat(str(v)[:10])
    except: return None

def safe_num(v):
    """Return a finite float or None. Rejects Airtable specialValue dicts (Infinity/NaN), blanks."""
    if v in (None,""): return None
    if isinstance(v,dict): return None            # {'specialValue':'Infinity'|'NaN'}
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except: return None

def level_of(gt):
    if gt and "-" in str(gt):
        s=str(gt).rsplit("-",1)[-1].strip()
        if s.isdigit(): return int(s)
    return 1
def tier_of(gt):
    if gt and "-" in str(gt): return str(gt).rsplit("-",1)[0].strip()
    return str(gt or "")

def sss_c(g):
    if g<=0: return 0.0
    msc=max(3000,min(35000,round(g/500.0)*500)); return max(265.0,min(3530.0,2030.0+(msc-20000)/500.0*50.0))
def loaded_php(g,hmo):
    if g<=0: return 0.0
    return g+g/12.0+sss_c(g)+min(0.025*g,2500.0)+200.0+(hmo or 0)+NIGHT_DIFF*g

def route(mc):
    m=(mc or "")
    if m in PASSTHROUGH_ALWAYS: return "PASS"
    if m in WW_MARKUP_MCS: return "WWMK"
    ml=m.lower()
    if "onnyt floater" in ml: return "BENCH"
    if ml.strip()=="onnyt": return "EXCLUDE"
    if "after hours" in ml: return "AH"
    if "new client" in ml: return "NC"
    if "westward" in ml: return "WWMK"
    return "OG"

# ------------------------------------------------------------------ loaders
@st.cache_data(ttl=300)
def load_people(_token):
    """Union of active (Masterlist) + exited (Attrition). Each: hire date, exit date (None if active),
    mc, tier level, site, total_usd (incl seat), labour_usd, gross, hmo, bucket, generic."""
    api=Api(_token); people=[]
    diag={"ml_fetched":0,"ml_skip_status":0,"ml_skip_onnyt":0,"ml_skip_col":0,"ml_kept":0,
          "at_fetched":0,"at_skip_onnyt":0,"at_skip_pre2026":0,"at_blank_exit":0,"at_kept":0}
    def gv(f,*names,d=None):
        for n in names:
            if n in f:
                v=f[n]; return v.get("name") if isinstance(v,dict) else v
        return d
    # active from Masterlist
    ml_ids=set()   # every Employee ID seen in Masterlist (active OR resigned)
    for r in api.table(ROSTER_BASE,HR_TABLE).all():
        diag["ml_fetched"]+=1
        f=r["fields"]
        eid=gv(f,"Employee ID")
        if eid is not None: ml_ids.add(eid)
        status=str(gv(f,"Employee Status") or "").strip().lower()
        if status in ("resigned","terminated","n/a - resignation/termination",""): diag["ml_skip_status"]+=1; continue
        mc=gv(f,"Member Company"); bucket=route(mc)
        if bucket=="EXCLUDE": diag["ml_skip_onnyt"]+=1; continue
        gt=gv(f,"Tier Level"); site=str(gv(f,"Office","Site") or "").strip().upper()
        if site=="COL": diag["ml_skip_col"]+=1; continue
        tu=gv(f,"Total (USD)"); lab=gv(f,"Labour Cost $")
        diag["ml_kept"]+=1
        people.append({"eid":eid,"hire":parse_date(gv(f,"Hire Date")),"exit":None,
            "mc":mc,"grptier":gt,"tier":tier_of(gt),"level":level_of(gt),"site":site,
            "generic":gv(f,"Generic Role"),"bucket":bucket,
            "total_usd":safe_num(tu),"labour_usd":safe_num(lab),
            "gross":float(gv(f,"Gross Monthly Salary (Php)",d=0) or 0),"hmo":float(gv(f,"HMO",d=0) or 0),
            "kind":"existing"})
    # exited from Attrition (by field id per playbook)
    F_EID="fldqfJ5fxlKIP774K"; F_HIRE="fldZHoHiyOYN9rI2o"; F_EFF="flduHRHkWrjh0hiBJ"
    F_MC="fldja8VQfZvHLwG9b"; F_TL_OLD="fld99XMjSkSq9Htmp"; F_OFF="fldLvUGMwdCs9YyCM"
    F_TOTUSD_OLD="fldkzHtDN4Q9pwrdT"; F_LAB_OLD="fldWM3gdxvMjeo35s"
    for r in api.table(ROSTER_BASE,ATTR_TABLE).all(use_field_ids=True):
        diag["at_fetched"]+=1
        f=r["fields"]
        def rv(fid):
            v=f.get(fid); return v.get("name") if isinstance(v,dict) else v
        mc=rv(F_MC); bucket=route(mc)
        if bucket=="EXCLUDE": diag["at_skip_onnyt"]+=1; continue
        gt=rv(F_TL_OLD); site=str(rv(F_OFF) or "").strip().upper()
        if site=="COL": continue
        tu=rv(F_TOTUSD_OLD); lab=rv(F_LAB_OLD)
        hire=parse_date(rv(F_HIRE)); exit_=parse_date(rv(F_EFF))
        if exit_ is None: diag["at_blank_exit"]+=1
        # Drop people who exited before the horizon (pre-2026).
        if exit_ is not None and exit_ < datetime.date(2026,1,1): diag["at_skip_pre2026"]+=1; continue
        if hire is None: hire=datetime.date(2025,1,1)   # rare legacy row missing hire date
        diag["at_kept"]+=1
        people.append({"eid":rv(F_EID),"hire":hire,"exit":exit_,
            "mc":mc,"grptier":gt,"tier":tier_of(gt),"level":level_of(gt),"site":site,
            "generic":None,"bucket":bucket,
            "total_usd":safe_num(tu),"labour_usd":safe_num(lab),
            "gross":0.0,"hmo":0.0,"kind":"existing"})
    people.append({"__diag__":diag})
    return people

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
    fc=read(FC_SGA); ac=read(ACT_SGA); merged={m:dict(v) for m,v in fc.items()}; am=set()
    for m,v in ac.items(): merged.setdefault(m,{}).update(v); am.add(m)
    return merged,am

@st.cache_data(ttl=300)
def load_hc_moves(_token):
    """Forecast (and actual) hires/exits counts by Group x Level x Month -> {iso:{(grp,'H',lvl):n,...}}"""
    api=Api(_token)
    def read(tbl):
        out=defaultdict(dict)
        for r in api.table(SIM_BASE,tbl).all():
            f=r["fields"]; grp=f.get("Group"); iso=col_to_iso(f.get("Month"))
            if not iso: continue
            for lvl in (1,2,3,4):
                h=f.get(f"Hires Tier {lvl}",0) or 0; x=f.get(f"Exits Tier {lvl}",0) or 0
                if h: out[iso][(grp,"H",lvl)]=out[iso].get((grp,"H",lvl),0)+h
                if x: out[iso][(grp,"X",lvl)]=out[iso].get((grp,"X",lvl),0)+x
        return out
    return read(FC_HC)

def price_map(pricing):
    pm={}
    for p in pricing:
        try: lvl=int(p["level"])
        except: lvl=1
        k=(p["group"],lvl)
        if k not in pm or p["eff"]>=pm[k]["eff"]: pm[k]=p
    return pm

# ------------------------------------------------------------------ per-Level avg cost (for forecast moves)
def level_avg_cost(people):
    """avg Total(USD) by (bucket-display-group, level) from current active people."""
    acc=defaultdict(list)
    for e in people:
        if e["exit"] is None and e["total_usd"]:
            g=grp_display(e["bucket"]); acc[(g,e["level"])].append(e["total_usd"])
    return {k:(sum(v)/len(v)) for k,v in acc.items()}

def grp_display(bucket):
    return "OG" if bucket=="OG" else "WW" if bucket in("WWMK","PASS") else bucket

# ------------------------------------------------------------------ active roster for a month
def active_people(people, iso):
    eo=end_of(iso); res=[]
    for e in people:
        if e["hire"] and e["hire"]<=eo and (e["exit"] is None or e["exit"]>eo):
            res.append(e)
    return res

def forecast_adds(hc_moves, iso, latest_actual, lvl_avg):
    """Net cumulative forecast moves applied for months after latest_actual, up to iso.
    Returns list of synthetic people (adds) and a set of (grp,level) exit credits."""
    adds=[]; exit_credits=defaultdict(int)
    for m in MONTHS:
        if m<=latest_actual or m>iso: continue
        for (grp,typ,lvl),n in hc_moves.get(m,{}).items():
            gb={"OG":"OG","WW":"WWMK","New Clients":"NC","NC":"NC","After Hours":"AH"}.get(grp,"OG")
            if typ=="H":
                for _ in range(int(n)):
                    adds.append({"eid":f"FC-{m}","hire":end_of(m),"exit":None,"mc":grp,
                        "grptier":f"{grp}-{lvl}","tier":grp,"level":lvl,"site":"BAGUIO","generic":None,
                        "bucket":gb,"total_usd":lvl_avg.get((grp_display(gb),lvl),1000.0),
                        "labour_usd":lvl_avg.get((grp_display(gb),lvl),1000.0)-SEAT_FEE,
                        "gross":0.0,"hmo":0.0,"kind":"forecast","hire_month":m}) 
            else:
                exit_credits[(grp_display(gb),lvl)]+=int(n)
    return adds, exit_credits

# ------------------------------------------------------------------ month engine
def run_month(iso, people, hc_moves, latest_actual, lvl_avg, pm, adjustments, fx, fees,
              sga_override=None, sga_map=None):
    roster=list(active_people(people, iso))
    adds,exit_credits=forecast_adds(hc_moves, iso, latest_actual, lvl_avg)
    roster+=adds
    # apply forecast exit credits: drop N current people per (group,level)
    if exit_credits:
        for (g,lvl),n in exit_credits.items():
            drop=[i for i,e in enumerate(roster) if grp_display(e["bucket"])==g and e["level"]==lvl and e["kind"]=="existing"]
            for i in drop[:n]: roster[i]=None
        roster=[e for e in roster if e is not None]
    # scenario structural levers
    for a in adjustments:
        if not adj_active(a,iso): continue
        if a["lever"]=="Attrition": roster=[e for e in roster if not scope_match(e,a)]
        elif a["lever"]=="Promotion":
            for e in roster:
                if scope_match(e,a): e["level"]=int(a["value"])
        elif a["lever"]=="New Hire":
            try: mc,gt=str(a["scope_value"]).split("|")
            except: continue
            for _ in range(int(a["value"])):
                roster.append({"eid":"NEW","hire":end_of(iso),"exit":None,"mc":mc,"grptier":gt,
                    "tier":tier_of(gt),"level":level_of(gt),"site":"BAGUIO","generic":None,
                    "bucket":route(mc),"total_usd":lvl_avg.get((grp_display(route(mc)),level_of(gt)),1000.0),
                    "labour_usd":None,"gross":0.0,"hmo":0.0,"kind":"forecast","hire_month":iso})
    seat=fees["seat"]; fac_b=fees["fac_baguio"]; fac_r=fees["fac_rate"]
    for a in adjustments:
        if a["lever"]=="FX Override" and adj_active(a,iso): fx=a["value"]
    price_over={}
    for a in adjustments:
        if a["lever"]=="Price Change" and adj_active(a,iso): price_over[str(a["scope_value"])]=a["value"]
    mc_markup={}
    for a in adjustments:
        if a["lever"]=="MC Markup %" and adj_active(a,iso): mc_markup[str(a["scope_value"])]=a["value"]
    cd=sum(1 for e in roster if e.get("site") in ("CEBU","DVO"))
    hc=len(roster); per_head_fac=(fac_b+cd*fac_r)/hc if hc else 0.0
    grp_rev=defaultdict(float); grp_cost=defaultdict(float); bench=0.0; onetime=0.0
    for e in roster:
        lab=labour_of(e,iso,fx,adjustments)
        full=lab+seat+per_head_fac; b=e["bucket"]
        if e.get("hire_month")==iso: onetime+=ONBOARD+RECRUIT
        if b=="BENCH": bench+=full; continue
        g=grp_display(b); grp_cost[g]+=full; lvl=e.get("level") or 1
        if b=="PASS": rev=full
        elif b=="WWMK":
            mk=price_over.get("WW|all"); mult=(1+mk) if mk is not None else (1.09 if iso>="2027-01" else 1.00); rev=full*mult
        elif b in("OG","NC"):
            gname="OG MCs" if b=="OG" else "New Clients"; pinfo=pm.get((gname,lvl))
            if lvl==4 and b=="OG":
                mk=price_over.get("OG|4",pinfo["markup"] if pinfo else 0.15); rev=lab*(1+mk)+seat+per_head_fac
            else:
                key=f"{b}|{lvl}"
                if key in price_over: rev=price_over[key]
                elif pinfo and pinfo["method"]=="flat": rev=pinfo["flat"]
                else: rev={1:1300,2:1600,3:2000}.get(lvl,2000)
        elif b=="AH": rev=price_over.get("AH|all",1600)
        else: rev=0.0
        mc_ov=mc_markup.get(e.get("mc"))          # per-member-company markup what-if overrides the bucket price/markup above
        if mc_ov is not None: rev=full*(1+mc_ov)
        grp_rev[g]+=rev
    if sga_override is not None: total_sga=sga_override+onetime
    else: total_sga=float((sga_map or {}).get(iso,{}).get("Total SGA",0) or 0)+onetime
    gp={g:grp_rev[g]-grp_cost[g] for g in ("OG","WW","NC","AH")}
    net=gp["OG"]+gp["WW"]+gp["NC"]-bench-total_sga
    bill=sum(1 for e in roster if e["bucket"] in ("OG","WWMK","PASS","NC"))
    contrib=(gp["OG"]+gp["WW"]+gp["NC"])/bill if bill else 0
    be=(bench+total_sga)/contrib if contrib>0 else float("inf")
    return {"month":iso,"net":net,"gp":gp,"rev":dict(grp_rev),"cost":dict(grp_cost),"bench":bench,
            "sga":total_sga,"billable":bill,"heads":hc,"contrib":contrib,"breakeven":be}

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
    if t=="Client Group": return grp_display(e.get("bucket"))==sv
    return False
def labour_of(e,iso,fx,adjustments):
    salaried=any(a["lever"] in("Salary %","Salary PHP") and adj_active(a,iso) and scope_match(e,a) for a in adjustments)
    if e.get("total_usd") is not None and not salaried:
        base=e["total_usd"]-SEAT_FEE
    else:
        gross=e["gross"]
        if gross<=0 and e.get("total_usd"): return e["total_usd"]-SEAT_FEE
        if iso>="2027-07": gross*=(1+MERIT_2027)
        mult,addphp=1.0,0.0
        for a in adjustments:
            if not adj_active(a,iso) or not scope_match(e,a): continue
            if a["lever"]=="Salary %": mult+=a["value"]/100.0
            if a["lever"]=="Salary PHP": addphp+=a["value"]
        return loaded_php(gross*mult+addphp,e["hmo"])/fx
    if not salaried and iso>="2027-07": base*=(1+MERIT_2027)
    return base

# ================================================================== UI
st.set_page_config(page_title="Onnyt Pricing Simulator",layout="wide")
token=st.secrets.get("AIRTABLE_TOKEN")
if not token: st.error("No AIRTABLE_TOKEN in Streamlit secrets."); st.stop()
st.title("Onnyt Pricing & Cost Simulator")
st.caption("Read-only. Historical roster reconstructed from Masterlist+Attrition. Full-entitlement cost (conservative vs actual). No writes.")

if st.button("🔄 Refresh data from Airtable"):
    st.cache_data.clear()
    st.rerun()

try:
    people=load_people(token); pricing=load_pricing(token)
    sga_map,actual_months=load_sga(token); hc_moves=load_hc_moves(token)
except Exception as e:
    st.error(f"Load error: {e}"); st.stop()
# pull out the diagnostic sentinel and keep it out of the roster
_diag=None
_clean=[]
for p in people:
    if p.get("__diag__"): _diag=p["__diag__"]
    else: _clean.append(p)
people=_clean
with st.expander("🔎 Data diagnostic (raw load counts from live Airtable)"):
    if _diag:
        d=_diag
        st.write(f"**Masterlist:** fetched **{d['ml_fetched']}** · "
                 f"skipped resigned/blank-status **{d['ml_skip_status']}** · "
                 f"skipped Onnyt **{d['ml_skip_onnyt']}** · skipped COL **{d['ml_skip_col']}** · "
                 f"**kept active {d['ml_kept']}**")
        st.write(f"**Attrition:** fetched **{d['at_fetched']}** · "
                 f"skipped Onnyt **{d['at_skip_onnyt']}** · skipped pre-2026 exit **{d['at_skip_pre2026']}** · "
                 f"blank exit date **{d['at_blank_exit']}** · **kept {d['at_kept']}**")
        st.write(f"**Total people in roster pool:** {d['ml_kept']+d['at_kept']} "
                 f"(Masterlist-active {d['ml_kept']} + Attrition {d['at_kept']})")
        st.caption("Expected from full census: Masterlist fetched ~492, kept ~407; "
                   "Attrition fetched ~191, kept ~small. If these are much higher, the live data has extra/duplicate rows.")
    else:
        st.write("No diagnostic captured.")
pm=price_map(pricing); lvl_avg=level_avg_cost(people)
latest_actual=max(actual_months) if actual_months else "2026-07"
if "adjustments" not in st.session_state: st.session_state.adjustments=[]

with st.sidebar:
    st.header("Global")
    fx=st.number_input("FX (Peso : $1)",value=FX_DEFAULT,step=0.5)
    onshore_adj=st.number_input("Onshore cost +/- ($/mo)",value=0.0,step=1000.0)
    view=st.radio("View",["Internal (cost + margin)","Shareable (no cost/margin)"])
    st.divider(); st.header("Fees")
    seat=st.number_input("Seat fee $/head (in Total USD)",value=SEAT_FEE,step=1.0)
    fac_b=st.number_input("Facility — Baguio $/mo",value=FAC_BAGUIO,step=500.0)
    fac_r=st.number_input("Facility — Cebu/Davao $/head",value=FAC_CD_RATE,step=10.0)
    fees={"seat":seat,"fac_baguio":fac_b,"fac_rate":fac_r}
    st.divider(); st.header("Pricing (editable)")
    og1=st.number_input("OG Level 1 $",value=float(pm.get(("OG MCs",1),{}).get("flat",1300)))
    og2=st.number_input("OG Level 2 $",value=float(pm.get(("OG MCs",2),{}).get("flat",1600)))
    og3=st.number_input("OG Level 3 $",value=float(pm.get(("OG MCs",3),{}).get("flat",2000)))
    og4mk=st.number_input("OG Level 4 markup %",value=15.0,step=1.0)/100
    wwmk=st.number_input("WW markup % (from Jan 2027)",value=9.0,step=1.0)/100
price_adj=[{"lever":"Price Change","scope_type":"","scope_value":k,"value":v,"eff":e,"end":None}
           for k,v,e in [("OG|1",og1,None),("OG|2",og2,None),("OG|3",og3,None),("OG|4",og4mk,None),("WW|all",wwmk,"2027-01")]]

hdr_a,hdr_b=st.columns([5,2])
hdr_a.subheader("Build a what-if")
if hdr_b.button("🧹 Reset ALL what-ifs (price, MC markup, salary, SGA…)"):
    st.session_state.adjustments=[]
    st.session_state.sga_cat_edits={}
    for k in list(st.session_state.keys()):
        if k.startswith("sga_cat_editor_"): del st.session_state[k]
    st.rerun()
c1,c2,c3,c4,c5=st.columns([1.3,1.3,1.3,1,1])
lever=c1.selectbox("Lever",["Price Change","MC Markup %","Salary %","Salary PHP","Promotion","New Hire","Attrition","FX Override"])
def opts(field): return sorted({str(e[field]) for e in people if e.get(field) not in (None,"")})
if lever=="New Hire":
    scope_type="New Hire"; mc_sel=c2.selectbox("MC",opts("mc")); tl_sel=c3.selectbox("Tier-Level",opts("grptier"))
    sv=f"{mc_sel}|{tl_sel}"; val=c4.number_input("How many",value=1,step=1)
elif lever=="MC Markup %":
    scope_type="Member Company"
    mc_sel=c2.selectbox("Member Company",opts("mc"))
    sv=mc_sel; val=c4.number_input("Markup %",value=5.0,step=1.0)/100
    c3.write("")  # spacer — no extra scope needed, MC already picked above
elif lever=="Price Change":
    scope_type=""
    PRICE_LINES={"OG Level 1 ($)":("OG|1","dollar"),"OG Level 2 ($)":("OG|2","dollar"),
                 "OG Level 3 ($)":("OG|3","dollar"),"OG Level 4 markup (%)":("OG|4","pct"),
                 "WW markup (%)":("WW|all","pct"),"AH price ($)":("AH|all","dollar")}
    pl=c2.selectbox("Price line",list(PRICE_LINES.keys()))
    sv,kind=PRICE_LINES[pl]
    if kind=="pct": val=c4.number_input("New value (%)",value=15.0,step=1.0)/100
    else: val=c4.number_input("New value ($)",value=1300.0,step=50.0)
    c3.write("")  # spacer — no extra scope needed for a price line
else:
    scope_type=c2.selectbox("Scope",["All","Generic Role","Tier","Level","Tier-Level","Member Company","Client Group"])
    if scope_type=="Generic Role": sv=c3.selectbox("Which",opts("generic"))
    elif scope_type=="Tier": sv=c3.selectbox("Which",opts("tier"))
    elif scope_type=="Level": sv=c3.selectbox("Which",["1","2","3","4"])
    elif scope_type=="Tier-Level": sv=c3.selectbox("Which",opts("grptier"))
    elif scope_type=="Member Company": sv=c3.selectbox("Which",opts("mc"))
    elif scope_type=="Client Group": sv=c3.selectbox("Which",["OG","WW","NC","AH"])
    else: sv=c3.text_input("Which","")
    val=c4.number_input("Value",value=5.0)
eff=c5.selectbox("From month" if lever!="Price Change" else "Effective from",MONTHS,index=MONTHS.index("2027-03") if "2027-03" in MONTHS else MONTHS.index("2027-07"))
if st.button("➕ Add to scenario"):
    st.session_state.adjustments.append({"lever":lever,"scope_type":scope_type,"scope_value":sv,"value":val,"eff":eff,"end":None})
if st.session_state.adjustments:
    st.write("**Active what-ifs:**")
    for i,a in enumerate(st.session_state.adjustments):
        cc=st.columns([6,1]); cc[0].write(f"• {a['lever']} — {a['scope_type']} {a['scope_value']} = {a['value']} (from {a['eff']})")
        if cc[1].button("remove",key=f"rm{i}"): st.session_state.adjustments.pop(i); st.rerun()
user_adj=st.session_state.adjustments

# editable SGA breakdown (session-only) — pick a month, edit by category instead of one flat total.
# The data has a hierarchy: 5 personnel-detail rows roll up into a "61000 - Personnel Expense"
# subtotal, which plus 5 expense categories rolls up into "Total SGA". Only the detail rows are
# editable — the subtotal and Total SGA are shown as computed, read-only rows so edits can never
# double-count (editing a detail row automatically moves both the subtotal and the grand total).
PERSONNEL_SUBTOTAL_LABEL="61000 - Personnel Expense"
PERSONNEL_DETAIL_PREF=["Onshore Payroll","Onnyt PH Corporate Payroll","JA Relocation Cost (Cebu Condo)",
                       "OG Shared Staff Bonus","Staffing Continency (130K)"]
if "sga_cat_edits" not in st.session_state: st.session_state.sga_cat_edits={}
with st.expander("✏️ Edit SGA for what-ifs (session only — does not save to Airtable)"):
    hdr1,hdr2=st.columns([4,1])
    sga_edit_month=hdr1.selectbox("Month to edit",MONTHS,
        index=MONTHS.index("2026-07") if "2026-07" in MONTHS else 0,key="sga_edit_month_sel")
    if hdr2.button("↺ Reset all SGA to base"):
        st.session_state.sga_cat_edits={}
        for k in list(st.session_state.keys()):
            if k.startswith("sga_cat_editor_"): del st.session_state[k]
        st.rerun()
    base_row=sga_map.get(sga_edit_month,{}) or {}
    # Every real category in the data, excluding the Total row AND the Personnel subtotal row
    # (that subtotal is computed below from its own detail rows, never edited directly).
    all_cats=[k for k in base_row.keys() if k.strip().lower() not in ("total sga",PERSONNEL_SUBTOTAL_LABEL.lower())]
    pers_detail=[c for c in PERSONNEL_DETAIL_PREF if c in all_cats]
    other_cats=sorted([c for c in all_cats if c not in pers_detail])
    cats=pers_detail+other_cats
    prev=st.session_state.sga_cat_edits.get(sga_edit_month,{})
    rows=[{"Category":c,"Base Amount":float(base_row.get(c,0) or 0),
           "Editable Amount":float(prev.get(c, base_row.get(c,0) or 0))} for c in cats]
    cat_df=pd.DataFrame(rows)
    edited_cats=st.data_editor(cat_df,hide_index=True,disabled=["Category","Base Amount"],
        key=f"sga_cat_editor_{sga_edit_month}",use_container_width=True)
    st.session_state.sga_cat_edits[sga_edit_month]={r["Category"]:r["Editable Amount"] for _,r in edited_cats.iterrows()}
    edited_map={r["Category"]:r["Editable Amount"] for _,r in edited_cats.iterrows()}
    pers_subtotal=sum(edited_map.get(c,0.0) for c in pers_detail)
    edited_total=pers_subtotal+sum(edited_map.get(c,0.0) for c in other_cats)
    base_total=float(base_row.get("Total SGA",0) or 0)
    st.markdown(f"**Subtotal — {PERSONNEL_SUBTOTAL_LABEL}: ${pers_subtotal:,.0f}**  _(computed from the personnel rows above, not directly editable)_")
    st.markdown(f"**Total SGA: ${edited_total:,.0f}**  ·  base Total SGA: ${base_total:,.0f}")
    st.caption("Only the month selected above is edited. Every other month keeps its base Total SGA until you visit it here.")
# Build the per-month total the engine uses.
# base_sga_over = always the unedited Airtable Total SGA, for the Baseline line (never moves).
# sga_over      = edited category sum for any month touched in this session, else base — for Scenario only.
base_sga_over={m:float(sga_map.get(m,{}).get("Total SGA",0) or 0) for m in MONTHS}
sga_over={}
for m in MONTHS:
    if m in st.session_state.sga_cat_edits:
        sga_over[m]=sum(st.session_state.sga_cat_edits[m].values())
    else:
        sga_over[m]=base_sga_over[m]

def build(adjs,sga_dict):
    return [run_month(m,people,hc_moves,latest_actual,lvl_avg,pm,adjs,fx,fees,
                      sga_override=sga_dict.get(m,0.0)+onshore_adj,sga_map=sga_map) for m in MONTHS]
baseline=build(price_adj,base_sga_over); scenario=build(price_adj+user_adj,sga_over)

st.divider()
mi=st.select_slider("Month",options=MONTHS,value="2026-07")
idx=MONTHS.index(mi); r=scenario[idx]; b=baseline[idx]
tag="ACTUAL" if mi in actual_months else "FORECAST"
st.caption(f"Showing **{mi}** — SGA basis: **{tag}** · reconstructed heads: **{r['heads']}**")
m1,m2,m3=st.columns(3)
m1.metric("Net profit / (loss)",f"${r['net']:,.0f}",f"{r['net']-b['net']:+,.0f} vs baseline")
if math.isfinite(r["breakeven"]):
    gap=r["breakeven"]-r["billable"]
    m2.metric("Break-even FTEs vs actual",f"{r['breakeven']:.0f} / {r['billable']}",f"{'-' if gap<=0 else '+'}{abs(gap):.0f}",delta_color="inverse")
else: m2.metric("Break-even FTEs","unreachable")
m3.metric("Contribution / FTE",f"${r['contrib']:,.0f}",f"{r['contrib']-b['contrib']:+,.0f}")

st.subheader("24-month net profit")
st.line_chart(pd.DataFrame({"Month":MONTHS,"Baseline":[x["net"] for x in baseline],"Scenario":[x["net"] for x in scenario]}).set_index("Month"),
              color=["#9CA3AF","#7B3FBE"])  # Baseline = neutral gray (continuous), Scenario = Onnyt purple (stands out)

st.subheader("Net profit by month — Baseline vs Scenario")
def year_table(year):
    idxs=[i for i,m in enumerate(MONTHS) if m.startswith(str(year))]
    rows=[]; b_ytd=0.0; s_ytd=0.0
    for i in idxs:
        b_net=baseline[i]["net"]; s_net=scenario[i]["net"]
        b_ytd+=b_net; s_ytd+=s_net
        rows.append({"Month":iso_to_col(MONTHS[i]),"Baseline Net":b_net,"Scenario Net":s_net,
                     "Baseline YTD":b_ytd,"Scenario YTD":s_ytd})
    return pd.DataFrame(rows)
tcol1,tcol2=st.columns(2)
with tcol1:
    st.markdown("**2026**")
    df26=year_table(2026)
    st.dataframe(df26.style.format({"Baseline Net":"${:,.0f}","Scenario Net":"${:,.0f}",
                 "Baseline YTD":"${:,.0f}","Scenario YTD":"${:,.0f}"}),hide_index=True,use_container_width=True)
with tcol2:
    st.markdown("**2027**")
    df27=year_table(2027)
    st.dataframe(df27.style.format({"Baseline Net":"${:,.0f}","Scenario Net":"${:,.0f}",
                 "Baseline YTD":"${:,.0f}","Scenario YTD":"${:,.0f}"}),hide_index=True,use_container_width=True)
st.caption("YTD resets each January. Scenario = Baseline + any active what-ifs (incl. dated price changes) above.")

st.subheader("Headcount — reconstructed / projected by month")
hc_row=pd.DataFrame({"Month":MONTHS,"Billable heads":[x["billable"] for x in scenario],
                     "Total heads (excl Onnyt)":[x["heads"] for x in scenario]}).set_index("Month")
st.bar_chart(hc_row["Billable heads"])

st.subheader("SGA — 24 months")
SGA_ROWS=["Onshore Payroll","Onnyt PH Corporate Payroll","JA Relocation Cost (Cebu Condo)","OG Shared Staff Bonus",
          "Staffing Continency (130K)","61000 - Personnel Expense","62100 - Technology","62200 - Travel & Entertainment",
          "62300 - Office Expense","62600 - Professional Fees","62900 - Other G&A Expenses","Total SGA"]
def sgv(m,lbl):
    row=sga_map.get(m,{})
    for k in row:
        if k.strip()==lbl.strip(): return float(row[k] or 0)
    return 0.0
sga_df=pd.DataFrame({iso_to_col(m)+("*" if m in actual_months else ""):[sgv(m,l) for l in SGA_ROWS] for m in MONTHS},index=SGA_ROWS)
st.dataframe(sga_df.style.format("${:,.0f}"))
st.caption("*= actual (from Actual SGA). Others forecast. Onshore combined (DT/GN never split). Total SGA drives net.")

st.subheader(f"Sensitivity — net at {mi} (WW markup × org salary %)")
ww_axis=[-0.05,0,0.05,0.09,0.14,0.20]; sal_axis=[0,2,4,6,8,10]; grid=[]
for s in sal_axis:
    rr=[]
    for w in ww_axis:
        adj=[a for a in (price_adj+user_adj) if a["scope_value"]!="WW|all"]
        adj+=[{"lever":"Price Change","scope_type":"","scope_value":"WW|all","value":w,"eff":"2027-01","end":None},
              {"lever":"Salary %","scope_type":"All","scope_value":"","value":s,"eff":"2027-07","end":None}]
        rr.append(run_month(mi,people,hc_moves,latest_actual,lvl_avg,pm,adj,fx,fees,sga_override=sga_over.get(mi,0.0)+onshore_adj)["net"])
    grid.append(rr)
gdf=pd.DataFrame(grid,index=[f"Sal +{s}%" for s in sal_axis],columns=[f"WW {int(w*100)}%" for w in ww_axis])
st.dataframe(gdf.style.format("${:,.0f}").background_gradient(cmap="RdYlGn",axis=None))

st.subheader("Where it comes from — "+("group (shareable)" if "Shareable" in view else "internal"))
if "Shareable" in view:
    st.table(pd.DataFrame([{"Group":g,"Revenue":f"${r['rev'].get(g,0):,.0f}"} for g in ("OG","WW","NC","AH")]))
else:
    tdf=pd.DataFrame([{"Group":g,"Revenue":r["rev"].get(g,0),"Cost":r["cost"].get(g,0),"Gross profit":r["gp"].get(g,0)} for g in ("OG","WW","NC","AH")])
    st.dataframe(tdf.style.format({"Revenue":"${:,.0f}","Cost":"${:,.0f}","Gross profit":"${:,.0f}"}),hide_index=True)
    st.caption(f"WW group = Westward markup + passthrough (Yellow Jacket/Illium ≈ $0 GP). Bench: ${r['bench']:,.0f} · Total SGA: ${r['sga']:,.0f} · Billable: {r['billable']}")

with st.expander("🔎 OG cost detail (per-person, as used in the calculation above)"):
    og_roster=[e for e in list(active_people(people, mi)) if e["bucket"]=="OG"]
    cd_dbg=sum(1 for e in list(active_people(people, mi)) if e.get("site") in ("CEBU","DVO"))
    hc_dbg=len(list(active_people(people, mi)))
    fac_dbg=(fees["fac_baguio"]+cd_dbg*fees["fac_rate"])/hc_dbg if hc_dbg else 0.0
    st.write(f"OG heads this month: **{len(og_roster)}** · facility/head used: **${fac_dbg:,.2f}** "
             f"(Baguio ${fees['fac_baguio']:,.0f} + {cd_dbg} Cebu/DVO × ${fees['fac_rate']:,.0f}, over {hc_dbg} total heads)")
    rows=[]
    for e in og_roster:
        tu=e.get("total_usd")
        lab_used = (tu - SEAT_FEE) if (tu is not None) else None
        full = (lab_used if lab_used is not None else 0)+fees["seat"]+fac_dbg
        rows.append({"Employee ID":e.get("eid"),"Level":e.get("level"),"MC":e.get("mc"),
                     "Total(USD) from Airtable":tu,"Full cost used":full})
    ddf=pd.DataFrame(rows)
    if len(ddf):
        st.dataframe(ddf.style.format({"Total(USD) from Airtable":"${:,.0f}","Full cost used":"${:,.0f}"}),hide_index=True)
        st.write(f"**Sum of Total(USD) from Airtable:** ${ddf['Total(USD) from Airtable'].sum():,.0f}")
        st.write(f"**Sum of Full cost used (incl facility):** ${ddf['Full cost used'].sum():,.0f}")
    else:
        st.write("No OG heads found for this month.")

st.divider()
st.caption("History reconstructed: active if Hire Date ≤ month-end AND (active OR Attrition Effective Date > month-end). "
           "Cost=Total(USD)+facility. Onnyt→SGA. Bench=Floaters. Passthrough: Yellow Jacket, Illium. "
           "WW360 ×1.09 from Jan-2027. OG L4=labour×1.15+seat+facility. Full entitlement. Read-only.")
