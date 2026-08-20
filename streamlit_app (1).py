"""
Onnyt Pricing & Cost Simulator — full app v2 (read-only).
Roster/pricing/forecast: live from Airtable. SGA: embedded 24-month series read from the
P&L (actuals) + Budget Corp Payroll/SGA (forecast), spliced at the latest actual month.
Confidential DT/GN payroll is never touched — only the combined Onshore figure, inside SGA.

Deploy: replace streamlit_app.py in the repo, commit, refresh. Token stays in Secrets.
To refresh SGA when the sheets change: update SGA_TOTAL below (or ask to wire it live).
"""
import streamlit as st
from pyairtable import Api
from collections import defaultdict
import math
import pandas as pd

ROSTER_BASE = "appdlOkT4HDXUmC5G"
SIM_BASE    = "appmJlnqk9l48XT5B"
HR_TABLE    = "HR Masterlist"
FX_DEFAULT  = 59.0
SEAT_FEE    = 99.0
FAC_BAGUIO  = 28000.0
FAC_CD_RATE = 250.0
NIGHT_DIFF  = 0.100764
MERIT_2027  = 0.045
ONBOARD_ONETIME = 229.0
RECRUIT_ONETIME = 299.0

MONTHS = []
_y, _m = 2026, 7
for _ in range(18):
    MONTHS.append(f"{_y:04d}-{_m:02d}"); _m += 1
    if _m > 12: _m = 1; _y += 1

# --- SGA: TOTAL SGA per month = full Personnel (Onshore + Onnyt PH Corporate + extras)
#     + all 62000 lines. Actuals for closed months (from P&L), forecast beyond (from Budget).
#     Onnyt-tagged staff are EXCLUDED from the roster, so their cost lives here, not there.
#     Update these when the sheets change (or ask to wire live via a service account).
SGA_TOTAL = {
    "2026-07":51766, "2026-08":52004, "2026-09":76882, "2026-10":69960, "2026-11":53038, "2026-12":59616,
    "2027-01":67816, "2027-02":66716, "2027-03":72591, "2027-04":70636, "2027-05":70636, "2027-06":72511,
    "2027-07":71602, "2027-08":71602, "2027-09":92477, "2027-10":75852, "2027-11":71852, "2027-12":77727,
}
LATEST_ACTUAL = "2026-07"   # months <= this read as actual; beyond = forecast (auto-detect when live)

# tier-mid loaded-USD cost for projected/new hires
MID = {"Accounting-1":759.32,"Accounting-2":1138.98,"ACM-1":735.59,"ACM-2":1162.71,
       "AP/AR-1":664.41,"APM-1":735.59,"APM-2":996.61,"CSR-1":717.80,"CSR-2":1014.41,
       "Compliance-1":593.22,"Executive Assistant -1":771.19,"Executive Assistant -2":1127.12,
       "HR-1":711.86,"HR-2":1275.42,"Illium-1":711.86,"Invest Admin-1":711.86,
       "Sales Admin-1":711.86,"Sales Admin-2":1903.05,"SLM-1":711.86,"Transitions-1":711.86,
       "YJM-1":735.59,"YJM-2":1032.20}
MID_BY_LEVEL = {1:720.0, 2:1120.0, 3:1400.0, 4:2000.0}

def level_of(gt):
    if gt and "-" in str(gt):
        s = str(gt).rsplit("-",1)[-1].strip()
        if s.isdigit(): return int(s)
    return 1
def tier_of(gt):
    if gt and "-" in str(gt): return str(gt).rsplit("-",1)[0].strip()
    return str(gt or "")
def mid_for(gt):
    return MID.get(gt, MID_BY_LEVEL.get(level_of(gt), 900.0))

def sss_contribution(gross):
    if gross <= 0: return 0.0
    msc = max(3000, min(35000, round(gross/500.0)*500))
    return max(265.0, min(3530.0, 2030.0 + (msc-20000)/500.0*50.0))
def loaded_php(gross, hmo):
    if gross <= 0: return 0.0
    return (gross + gross/12.0 + sss_contribution(gross) + min(0.025*gross,2500.0)
            + 200.0 + (hmo or 0) + NIGHT_DIFF*gross)

def client_group(mc):
    m = (mc or "").lower()
    if "onnyt floater" in m: return "BENCH"
    if m.strip() == "onnyt": return "EXCLUDE"      # corporate admin -> lives in SGA, not roster
    if "westward" in m:      return "WW"
    if "after hours" in m:   return "AH"
    if "new client" in m:    return "NC"
    return "OG"

@st.cache_data(ttl=300)
def load_roster(_token):
    tbl = Api(_token).table(ROSTER_BASE, HR_TABLE)
    def g(f, *names, d=None):
        for n in names:
            if n in f:
                v = f[n]; return v.get("name") if isinstance(v, dict) else v
        return d
    out = []
    for r in tbl.all():
        f = r["fields"]
        status = str(g(f,"Employee Status","Status") or "").strip().lower()
        if status in ("resigned","terminated","n/a - resignation/termination",""): continue
        site = str(g(f,"Site","Office") or "").strip().upper()
        if site == "COL": continue
        mc = g(f,"Member Company","MC")
        grp = client_group(mc)
        if grp == "EXCLUDE": continue                 # Onnyt corporate admin excluded
        gt = g(f,"Tier Level","Grp-Tier")
        out.append({"eid":g(f,"Employee ID"),"mc":mc,"site":site,"grptier":gt,
                    "tier":tier_of(gt),"level":level_of(gt),"generic":g(f,"Generic Role"),
                    "gross":float(g(f,"Gross Monthly Salary (Php)","Gross Monthly Salary (PHP)",d=0) or 0),
                    "hmo":float(g(f,"HMO",d=0) or 0),"group":grp,"kind":"existing"})
    return out

@st.cache_data(ttl=300)
def load_pricing(_token):
    out = []
    for r in Api(_token).table(SIM_BASE,"Sim: Tier Price").all():
        f = r["fields"]
        def gg(n,d=None):
            v=f.get(n); return v.get("name") if isinstance(v,dict) else (v if v is not None else d)
        out.append({"group":gg("Client Group"),"level":gg("Tier"),"method":(gg("Pricing Method") or "").strip(),
                    "flat":float(gg("Flat Price USD",0) or 0),"markup":float(gg("Markup %",0) or 0),
                    "eff":str(gg("Effective Month","") or "")[:7]})
    return out

@st.cache_data(ttl=300)
def load_forecast(_token):
    hires, exits = [], []
    for r in Api(_token).table(SIM_BASE,"Sim: Forecast Headcount").all():
        f = r["fields"]
        def gg(n):
            v=f.get(n); return v.get("name") if isinstance(v,dict) else v
        gt = gg("Grp-Tier") or gg("Tier Level")
        grp = gg("Client Group") or client_group(gg("MC"))
        rec = {"mc":gg("MC"),"month":str(gg("Month") or "")[:7],"grptier":gt,"group":grp,
               "tier":tier_of(gt),"level":level_of(gt),"generic":None,
               "mid_usd":mid_for(gt),"kind":"forecast","site":"BAGUIO","gross":0,"hmo":0}
        (hires if (gg("Event Type") or "").strip().lower()=="hire" else exits).append(rec)
    return hires, exits

def price_map(pricing):
    pm = {}
    for p in pricing:
        try: lvl = int(p["level"])
        except: lvl = 1
        key = (p["group"], lvl)
        if key not in pm or p["eff"] >= pm[key]["eff"]: pm[key] = p
    return pm

def adj_active(a, month):
    if a.get("eff") and month < a["eff"]: return False
    if a.get("end") and month > a["end"]: return False
    return True
def scope_match(e, a):
    t, sv = a["scope_type"], str(a.get("scope_value") or "")
    if t == "All": return True
    if t == "Generic Role": return e.get("generic") == sv
    if t == "Tier": return e.get("tier") == sv
    if t == "Level": return str(e.get("level")) == sv
    if t == "Tier-Level": return e.get("grptier") == sv
    if t == "Member Company": return e.get("mc") == sv
    if t == "Client Group": return e.get("group") == sv
    return False

def active_roster(base, hires, exits, month, adjustments):
    roster = [dict(e) for e in base]
    for h in hires:
        if h["month"] and h["month"] <= month: roster.append(dict(h))
    for x in exits:
        if x["month"] and x["month"] <= month:
            for i, e in enumerate(roster):
                if e.get("group")==x["group"] and e.get("grptier")==x["grptier"]:
                    roster.pop(i); break
    for a in adjustments:
        if not adj_active(a, month): continue
        if a["lever"] == "Attrition":
            roster = [e for e in roster if not scope_match(e, a)]
        elif a["lever"] == "Promotion":
            for e in roster:
                if scope_match(e, a): e["level"] = int(a["value"])
        elif a["lever"] == "New Hire":
            try: mc, gt = str(a["scope_value"]).split("|")
            except: continue
            for _ in range(int(a["value"])):
                roster.append({"eid":f"NEW-{month}","mc":mc,"site":"BAGUIO","grptier":gt,
                    "tier":tier_of(gt),"level":level_of(gt),"generic":None,"gross":0,"hmo":0,
                    "group":client_group(mc),"kind":"forecast","mid_usd":mid_for(gt),
                    "hire_month":a["eff"]})
    return roster

def emp_lc(e, month, fx, adjustments):
    if e["kind"] == "forecast": return e["mid_usd"]
    gross = e["gross"]
    if month >= "2027-07": gross *= (1 + MERIT_2027)
    mult, addphp = 1.0, 0.0
    for a in adjustments:
        if not adj_active(a, month) or not scope_match(e, a): continue
        if a["lever"] == "Salary %": mult += a["value"]/100.0
        if a["lever"] == "Salary PHP": addphp += a["value"]
    return loaded_php(gross*mult+addphp, e["hmo"]) / fx

def run_month(month, base, hires, exits, pm, adjustments, fx, fees, onshore_adj):
    roster = active_roster(base, hires, exits, month, adjustments)
    seat = fees.get("seat",SEAT_FEE); fac_b = fees.get("fac_baguio",FAC_BAGUIO); fac_r = fees.get("fac_rate",FAC_CD_RATE)
    for a in adjustments:
        if a["lever"] == "FX Override" and adj_active(a, month): fx = a["value"]
    price_over = {}
    for a in adjustments:
        if a["lever"] == "Price Change" and adj_active(a, month):
            price_over[str(a["scope_value"])] = a["value"]
    cd = sum(1 for e in roster if e.get("site") in ("CEBU","DVO"))
    hc = len(roster)
    per_head_fac = (fac_b + cd*fac_r)/hc if hc else 0.0
    grp_rev = defaultdict(float); grp_cost = defaultdict(float); bench = 0.0; onetime = 0.0
    for e in roster:
        lc = emp_lc(e, month, fx, adjustments)
        g = e["group"]; full = lc + seat + per_head_fac
        # one-time onboarding + recruitment in the hire month for New-Hire-lever adds
        if e.get("hire_month") == month:
            onetime += ONBOARD_ONETIME + RECRUIT_ONETIME
        if g == "BENCH": bench += full; continue
        grp_cost[g] += full; lvl = e.get("level") or 1
        if g == "WW":
            mk = price_over.get("WW|all")
            mult = (1+mk) if mk is not None else (1.09 if month >= "2027-01" else 1.00)
            rev = full*mult
        elif g in ("OG","NC"):
            pinfo = pm.get((("OG MCs" if g=="OG" else "New Clients"), lvl))
            if lvl == 4 and g == "OG":
                mk = price_over.get("OG|4", pinfo["markup"] if pinfo else 0.15)
                rev = lc*(1+mk) + seat + per_head_fac
            else:
                key = f"{g}|{lvl}"
                if key in price_over: rev = price_over[key]
                elif pinfo and pinfo["method"]=="flat": rev = pinfo["flat"]
                else: rev = {1:1300,2:1600,3:2000}.get(lvl,2000)
        elif g == "AH": rev = price_over.get("AH|all",1600)
        else: rev = 0.0
        grp_rev[g] += rev
    sga = SGA_TOTAL.get(month, 0) + onshore_adj + onetime
    gp = {g: grp_rev[g]-grp_cost[g] for g in ("OG","WW","NC","AH")}
    net = gp["OG"]+gp["WW"]+gp["NC"] - bench - sga
    bill = sum(1 for e in roster if e["group"] in ("OG","WW","NC"))
    contrib = (gp["OG"]+gp["WW"]+gp["NC"])/bill if bill else 0
    be = (bench+sga)/contrib if contrib>0 else float("inf")
    return {"month":month,"net":net,"gp":gp,"rev":dict(grp_rev),"cost":dict(grp_cost),
            "bench":bench,"sga":sga,"billable":bill,"contrib":contrib,"breakeven":be}

def project(base, hires, exits, pm, adjustments, fx, fees, onshore_adj):
    return [run_month(m, base, hires, exits, pm, adjustments, fx, fees, onshore_adj) for m in MONTHS]

# ================================================================== UI
st.set_page_config(page_title="Onnyt Pricing Simulator", layout="wide")
token = st.secrets.get("AIRTABLE_TOKEN")
if not token:
    st.error("No AIRTABLE_TOKEN in Streamlit secrets."); st.stop()

st.title("Onnyt Pricing & Cost Simulator")
st.caption("Read-only. Levers move a 24-month projection; break-even is a reference. "
           "SGA (incl. onshore + Onnyt corporate) comes from the P&L/Budget. Nothing writes to Airtable.")

try:
    base = load_roster(token); pricing = load_pricing(token); hires, exits = load_forecast(token)
except Exception as e:
    st.error(f"Could not load data: {e}"); st.stop()
pm = price_map(pricing)
if "adjustments" not in st.session_state: st.session_state.adjustments = []

with st.sidebar:
    st.header("Global")
    fx = st.number_input("FX (Peso : $1)", value=FX_DEFAULT, step=0.5)
    onshore_adj = st.number_input("Onshore cost +/- ($/mo)", value=0.0, step=1000.0,
        help="Adjust total onshore staff cost up or down. Baseline onshore is already in SGA.")
    view = st.radio("View", ["Internal (cost + margin)","Shareable (no cost/margin)"])
    st.divider(); st.header("Fees")
    seat = st.number_input("Seat fee $/head", value=SEAT_FEE, step=1.0)
    fac_b = st.number_input("Facility — Baguio block $/mo", value=FAC_BAGUIO, step=500.0)
    fac_r = st.number_input("Facility — Cebu/Davao $/head", value=FAC_CD_RATE, step=10.0)
    fees = {"seat":seat,"fac_baguio":fac_b,"fac_rate":fac_r}
    st.divider(); st.header("Pricing (editable)")
    og1 = st.number_input("OG Level 1 $", value=float(pm.get(("OG MCs",1),{}).get("flat",1300)))
    og2 = st.number_input("OG Level 2 $", value=float(pm.get(("OG MCs",2),{}).get("flat",1600)))
    og3 = st.number_input("OG Level 3 $", value=float(pm.get(("OG MCs",3),{}).get("flat",2000)))
    og4mk = st.number_input("OG Level 4 markup %", value=15.0, step=1.0)/100
    wwmk = st.number_input("WW markup % (from Jan 2027)", value=9.0, step=1.0)/100

price_adj = [
    {"lever":"Price Change","scope_type":"","scope_value":"OG|1","value":og1,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"OG|2","value":og2,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"OG|3","value":og3,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"OG|4","value":og4mk,"eff":None,"end":None},
    {"lever":"Price Change","scope_type":"","scope_value":"WW|all","value":wwmk,"eff":"2027-01","end":None},
]

st.subheader("Build a what-if")
c1,c2,c3,c4,c5 = st.columns([1.3,1.3,1.3,1,1])
lever = c1.selectbox("Lever", ["Salary %","Salary PHP","Promotion","New Hire","Attrition","FX Override"])
def opts(field): return sorted({str(e[field]) for e in base if e.get(field) not in (None,"")})
if lever == "New Hire":
    scope_type = "New Hire MC|Tier-Level"
    mc_sel = c2.selectbox("Member Company", opts("mc"))
    tl_sel = c3.selectbox("Tier-Level", opts("grptier"))
    sv = f"{mc_sel}|{tl_sel}"
    val = c4.number_input("How many", value=1, step=1)
else:
    scope_type = c2.selectbox("Scope", ["All","Generic Role","Tier","Level","Tier-Level","Member Company","Client Group"])
    if scope_type == "Generic Role": sv = c3.selectbox("Which", opts("generic"))
    elif scope_type == "Tier": sv = c3.selectbox("Which", opts("tier"))
    elif scope_type == "Level": sv = c3.selectbox("Which", ["1","2","3","4"])
    elif scope_type == "Tier-Level": sv = c3.selectbox("Which", opts("grptier"))
    elif scope_type == "Member Company": sv = c3.selectbox("Which", opts("mc"))
    elif scope_type == "Client Group": sv = c3.selectbox("Which", ["OG","WW","NC","AH"])
    else: sv = c3.text_input("Which", "")
    val = c4.number_input("Value", value=5.0)
eff = c5.selectbox("From month", MONTHS, index=MONTHS.index("2027-07"))
if st.button("➕ Add to scenario"):
    st.session_state.adjustments.append(
        {"lever":lever,"scope_type":scope_type,"scope_value":sv,"value":val,"eff":eff,"end":None})

if st.session_state.adjustments:
    st.write("**Active what-ifs:**")
    for i, a in enumerate(st.session_state.adjustments):
        cc = st.columns([6,1])
        cc[0].write(f"• {a['lever']} — {a['scope_type']} {a['scope_value']} = {a['value']} (from {a['eff']})")
        if cc[1].button("remove", key=f"rm{i}"):
            st.session_state.adjustments.pop(i); st.rerun()

user_adj = st.session_state.adjustments
baseline = project(base, hires, exits, pm, price_adj, fx, fees, onshore_adj)
scenario = project(base, hires, exits, pm, price_adj+user_adj, fx, fees, onshore_adj)

st.divider()
mi = st.select_slider("Month", options=MONTHS, value="2027-07")
idx = MONTHS.index(mi); r = scenario[idx]; b = baseline[idx]
m1,m2,m3 = st.columns(3)
m1.metric("Net profit / (loss)", f"${r['net']:,.0f}", f"{r['net']-b['net']:+,.0f} vs baseline")
if math.isfinite(r["breakeven"]):
    gap = r["breakeven"] - r["billable"]
    m2.metric("Break-even FTEs vs actual", f"{r['breakeven']:.0f} / {r['billable']}",
              f"{'-' if gap<=0 else '+'}{abs(gap):.0f} FTEs", delta_color="inverse")
else: m2.metric("Break-even FTEs", "unreachable")
m3.metric("Contribution / FTE", f"${r['contrib']:,.0f}", f"{r['contrib']-b['contrib']:+,.0f}")

st.subheader("24-month net profit")
df = pd.DataFrame({"Month":MONTHS,"Baseline":[x["net"] for x in baseline],
                   "Scenario":[x["net"] for x in scenario]}).set_index("Month")
st.line_chart(df)

st.subheader(f"Sensitivity — net at {mi} (WW markup × org salary %)")
ww_axis = [-0.05,0,0.05,0.09,0.14,0.20]; sal_axis = [0,2,4,6,8,10]
grid = []
for s in sal_axis:
    row = []
    for w in ww_axis:
        adj = [a for a in (price_adj+user_adj) if a["scope_value"] != "WW|all"]
        adj += [{"lever":"Price Change","scope_type":"","scope_value":"WW|all","value":w,"eff":"2027-01","end":None},
                {"lever":"Salary %","scope_type":"All","scope_value":"","value":s,"eff":"2027-07","end":None}]
        row.append(run_month(mi, base, hires, exits, pm, adj, fx, fees, onshore_adj)["net"])
    grid.append(row)
gdf = pd.DataFrame(grid, index=[f"Sal +{s}%" for s in sal_axis],
                   columns=[f"WW {int(w*100)}%" for w in ww_axis])
st.dataframe(gdf.style.format("${:,.0f}").background_gradient(cmap="RdYlGn", axis=None))

st.subheader("Where it comes from — " + ("group level (shareable)" if "Shareable" in view else "MC level (internal)"))
if "Shareable" in view:
    st.table(pd.DataFrame([{"Group":g,"Revenue":f"${r['rev'].get(g,0):,.0f}"} for g in ("OG","WW","NC","AH")]))
else:
    tdf = pd.DataFrame([{"Group":g,"Revenue":r["rev"].get(g,0),"Cost":r["cost"].get(g,0),
                         "Gross profit":r["gp"].get(g,0)} for g in ("OG","WW","NC","AH")])
    st.dataframe(tdf.style.format({"Revenue":"${:,.0f}","Cost":"${:,.0f}","Gross profit":"${:,.0f}"}))
    st.caption(f"Bench (own COS line): ${r['bench']:,.0f}   ·   SGA (incl. onshore + Onnyt corp): ${r['sga']:,.0f}"
               f"   ·   Active billable heads: {r['billable']}")

st.divider()
st.caption("SGA from P&L (actuals) + Budget (forecast), incl. onshore & Onnyt corporate; DT/GN never shown. "
           "Onnyt-tagged staff excluded from roster. Bench computed live from Floaters. "
           "WW = passthrough thru Dec-2026 then full-COS×1.09. OG L4 = labour×1.15 + seat + facility. Read-only.")
