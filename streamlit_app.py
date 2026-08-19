"""
Onnyt Pricing Simulator — CONNECTION TEST (deploy this first)
Proves the full chain works: GitHub -> Streamlit Cloud -> Airtable -> your browser.
It reads (never writes) and confirms the exact field names the real engine will use.

Deploy steps:
  1. Put this file, requirements.txt in a GitHub repo.
  2. In Streamlit Cloud: New app -> pick the repo -> main file = streamlit_app.py.
  3. In the app's Settings -> Secrets, paste:
         AIRTABLE_TOKEN = "pat_your_readonly_token"
  4. Deploy. If the numbers below appear, the whole pipeline works.
"""
import streamlit as st
from pyairtable import Api

ROSTER_BASE = "appdlOkT4HDXUmC5G"     # Onnyt Roster
SIM_BASE    = "appmJlnqk9l48XT5B"     # Onnyt Pricing Simulator
HR_TABLE    = "HR Masterlist"

st.set_page_config(page_title="Onnyt Simulator — Connection Test", layout="wide")
st.title("Onnyt Pricing Simulator — connection test")
st.caption("Read-only. Confirms the pipeline works and shows the exact field names.")

# --- token from Streamlit secrets (never in the code) ---
token = st.secrets.get("AIRTABLE_TOKEN")
if not token:
    st.error("No AIRTABLE_TOKEN found in Streamlit secrets. Add it under Settings → Secrets.")
    st.stop()

api = Api(token)

# --- 1) Roster base: read HR Masterlist ---
st.header("1 · Roster base — HR Masterlist")
try:
    tbl = api.table(ROSTER_BASE, HR_TABLE)
    rows = tbl.all()                       # reads all records (read-only)
    st.success(f"Connected. Total records in HR Masterlist: **{len(rows)}**")

    # exact field names (display names) — this is what the real loader will target
    if rows:
        field_names = sorted(rows[0]["fields"].keys())
        with st.expander("Exact field names in HR Masterlist (confirm these)"):
            st.write(field_names)

    # try to identify the columns we mapped; adjust names here if they differ
    def col(row, *candidates, default=None):
        for c in candidates:
            if c in row["fields"]:
                v = row["fields"][c]
                return v.get("name") if isinstance(v, dict) else v
        return default

    STATUS   = ("Status", "Employee Status")
    ROLE     = ("Generic Role",)
    TIERLVL  = ("Grp-Tier", "Tier-Level", "Tier Level")
    MC       = ("MC", "Member Company")

    active = [r for r in rows if str(col(r, *STATUS) or "").strip().lower()
              not in ("resigned", "terminated", "n/a - resignation/termination", "")]
    st.metric("Active staff (excl. resigned/terminated)", len(active))

    # distinct Generic Roles / Tiers / Levels
    roles, tiers, levels = set(), set(), set()
    for r in active:
        gr = col(r, *ROLE)
        if gr: roles.add(gr)
        tl = col(r, *TIERLVL)
        if tl and "-" in str(tl):
            pre, suf = str(tl).rsplit("-", 1)
            tiers.add(pre.strip()); levels.add(suf.strip())
    c1, c2, c3 = st.columns(3)
    c1.write("**Generic Roles**"); c1.write(sorted(roles) or "— (check field name)")
    c2.write("**Tiers** (Tier-Level prefix)"); c2.write(sorted(tiers) or "— (check field name)")
    c3.write("**Levels** (suffix)"); c3.write(sorted(levels) or "— (check field name)")

    with st.expander("Sample of 5 active rows"):
        for r in active[:5]:
            st.write({k: (v.get("name") if isinstance(v, dict) else v)
                      for k, v in r["fields"].items()
                      if k in ("Employee ID", *ROLE, *TIERLVL, *MC, "Site",
                               "Gross Monthly Salary (PHP)", "HMO")})
except Exception as e:
    st.error(f"Roster read failed: {e}")

# --- 2) Sim base: confirm access to pricing + forecast ---
st.header("2 · Sim base — pricing & forecast")
for tname in ("Sim: Tier Price", "Sim: Forecast Headcount"):
    try:
        n = len(api.table(SIM_BASE, tname).all())
        st.success(f"{tname}: {n} records")
    except Exception as e:
        st.error(f"{tname} read failed: {e}")

st.divider()
st.caption("If both sections show data, the pipeline is proven and the field names above "
           "are what the full simulator will read. Nothing here writes to Airtable.")
