"""
FPL Agent - Streamlit Web App
Run locally: streamlit run fpl_app.py
"""

import streamlit as st
import anthropic
import requests
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

st.set_page_config(page_title="FPL Agent", page_icon="⚽", layout="centered")

st.markdown("""
<style>
    .block-container { max-width: 720px; padding-top: 2rem; }
    .hero { text-align: center; padding: 2.5rem 1rem 1.5rem; }
    .hero h1 { font-size: 2rem; font-weight: 700; margin: 0.5rem 0 0.25rem; }
    .hero p  { font-size: 1rem; color: #6b7280; margin: 0 0 1.75rem; }
    .feature-row { display: flex; justify-content: center; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }
    .feature-pill { background: #f3f4f6; border-radius: 999px; padding: 6px 14px; font-size: 13px; color: #374151; }
    .nudge { background: #f0fdf4; border: 1px solid #86efac; border-radius: 10px; padding: 0.75rem 1rem; margin: 1rem 0; text-align: center; font-size: 14px; color: #166534; }
    .free-note { text-align: center; font-size: 12px; color: #9ca3af; margin-top: 1rem; }
</style>
""", unsafe_allow_html=True)

BMC_URL  = "https://buymeacoffee.com/fplagent"
BMC_LINK = f'<div class="free-note"><a href="{BMC_URL}" target="_blank"><strong>☕ Buy me a coffee if you find it useful</strong></a></div>'

# ── Google Sheets logging ─────────────────────────────────
@st.cache_resource
def get_sheet():
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return gspread.authorize(creds).open_by_key(st.secrets["SHEET_ID"]).sheet1
    except Exception:
        return None

def log_question(q):
    try:
        s = get_sheet()
        if s: s.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), q])
    except Exception:
        pass

# ── Session state ─────────────────────────────────────────
for k, v in {"messages":[],"history":[],"question_count":0,
             "show_landing":True,"show_how_it_works":False,"pending_prompt":None}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Load FPL data (cached 1hr) ────────────────────────────
@st.cache_data(ttl=3600)
def load_fpl_data():
    bootstrap = requests.get("https://fantasy.premierleague.com/api/bootstrap-static/").json()
    fixtures  = requests.get("https://fantasy.premierleague.com/api/fixtures/").json()
    teams_by_id = {t["id"]: t["name"] for t in bootstrap["teams"]}
    pos_map     = {1:"GK", 2:"DEF", 3:"MID", 4:"FWD"}
    next_gw     = next((e["id"] for e in bootstrap["events"] if not e.get("finished", True)), 38)

    # Count fixtures per team per GW
    team_gw_count = {}
    for fix in fixtures:
        gw = fix["event"]
        if not gw: continue
        for tid in [fix["team_h"], fix["team_a"]]:
            team_gw_count.setdefault(tid, {})
            team_gw_count[tid][gw] = team_gw_count[tid].get(gw, 0) + 1

    all_ids = {t["id"] for t in bootstrap["teams"]}
    dgw_teams = {tid for tid in all_ids if team_gw_count.get(tid,{}).get(next_gw,0) >= 2}
    bgw_teams = {tid for tid in all_ids if team_gw_count.get(tid,{}).get(next_gw,0) == 0}

    # Build fixture strings and difficulty for next GW
    next_gw_diff    = {}
    next_gw_fix_str = {}
    for fix in fixtures:
        if fix["event"] != next_gw: continue
        h, a = fix["team_h"], fix["team_a"]
        next_gw_diff.setdefault(h,[]).append(fix["team_h_difficulty"])
        next_gw_diff.setdefault(a,[]).append(fix["team_a_difficulty"])
        next_gw_fix_str.setdefault(h,[]).append(f"{teams_by_id.get(a,'?')}(H)")
        next_gw_fix_str.setdefault(a,[]).append(f"{teams_by_id.get(h,'?')}(A)")

    # Build gw_status for overview
    remaining_gws = [e["id"] for e in bootstrap["events"] if not e.get("finished", True)]
    gw_status = {}
    for gw in remaining_gws:
        dgw = {tid for tid in all_ids if team_gw_count.get(tid,{}).get(gw,0) >= 2}
        bgw = {tid for tid in all_ids if team_gw_count.get(tid,{}).get(gw,0) == 0}
        if dgw or bgw:
            gw_status[gw] = {
                "dgw": [teams_by_id[tid] for tid in dgw],
                "bgw": [teams_by_id[tid] for tid in bgw],
            }

    # Build players
    players = []
    for p in bootstrap["elements"]:
        if p["status"] != "a" or p["minutes"] < 90: continue
        tid = p["team"]
        players.append({
            "id": p["id"], "name": f"{p['first_name']} {p['second_name']}",
            "web_name": p["web_name"], "team": teams_by_id.get(tid,"?"), "team_id": tid,
            "pos": pos_map.get(p["element_type"],"?"), "price": p["now_cost"]/10,
            "total_points": p["total_points"], "ppg": float(p["points_per_game"] or 0),
            "form": float(p["form"] or 0), "minutes": p["minutes"],
            "goals": p["goals_scored"], "assists": p["assists"],
            "clean_sheets": p["clean_sheets"],
            "xg_per90":  float(p.get("expected_goals_per_90") or 0),
            "xa_per90":  float(p.get("expected_assists_per_90") or 0),
            "xgi_per90": float(p.get("expected_goal_involvements_per_90") or 0),
            "ict": float(p.get("ict_index") or 0),
            "selected_pct": float(p.get("selected_by_percent") or 0),
            "dgw": tid in dgw_teams, "bgw": tid in bgw_teams,
            "fixture": ", ".join(next_gw_fix_str.get(tid, ["No fixture — BGW"])),
            "fix_diff": sum(next_gw_diff.get(tid,[3]))/max(1,len(next_gw_diff.get(tid,[3]))),
        })

    return (bootstrap, fixtures, teams_by_id, players, next_gw,
            next_gw_diff, next_gw_fix_str, dgw_teams, bgw_teams, gw_status)

(bootstrap, fixtures, teams_by_id, players, next_gw,
 next_gw_diff, next_gw_fix_str, dgw_teams, bgw_teams, gw_status) = load_fpl_data()

# ── Scoring function ──────────────────────────────────────
def score_player(p, style="balanced"):
    mins    = max(p["minutes"], 1)
    gpts    = {"FWD":4,"MID":5,"DEF":6,"GK":6}[p["pos"]]
    attack  = (p["xg_per90"]*0.5 + (p["goals"]/mins*90)*0.5)*gpts
    attack += (p["xa_per90"]*0.5 + (p["assists"]/mins*90)*0.5)*3
    cs_pts  = {"GK":4,"DEF":4,"MID":1,"FWD":0}[p["pos"]]
    defence = (p["clean_sheets"]/mins*90)*cs_pts
    bonus   = p["ppg"]*0.3
    form_w  = 0.5 if style=="cautious" else 0.3
    fix_pow = 0.8 if style=="cautious" else 1.4
    atk_w   = 0.8 if style=="cautious" else 1.5
    fix_mult = ((6 - p["fix_diff"]) / 5) ** fix_pow
    raw     = attack*atk_w + defence + bonus
    base    = raw*(1 + form_w*(p["form"]/10))*fix_mult
    if p["dgw"]: base *= 1.8
    elif p["bgw"]: base *= 0.1
    return base

# ── Tool implementations ──────────────────────────────────
def get_fixtures(gameweek=None):
    gw  = gameweek or next_gw
    gws = [f for f in fixtures if f["event"] == gw]
    if not gws:
        return {"error": f"No fixtures for GW{gw}"}
    all_ids     = {t["id"] for t in bootstrap["teams"]}
    playing_ids = {f["team_h"] for f in gws} | {f["team_a"] for f in gws}
    dgw_count   = {}
    for f in gws:
        dgw_count[f["team_h"]] = dgw_count.get(f["team_h"],0)+1
        dgw_count[f["team_a"]] = dgw_count.get(f["team_a"],0)+1
    return {
        "gameweek": gw,
        "fixtures": [{"home": teams_by_id.get(f["team_h"],"?"),
                      "away": teams_by_id.get(f["team_a"],"?"),
                      "home_diff": f["team_h_difficulty"],
                      "away_diff": f["team_a_difficulty"]} for f in gws],
        "dgw_teams": [teams_by_id[tid] for tid,c in dgw_count.items() if c>=2],
        "bgw_teams": [teams_by_id[tid] for tid in all_ids - playing_ids],
    }

def get_top_players(position="ALL", limit=20):
    pool = [dict(p, score=round(score_player(p),3)) for p in players]
    if position != "ALL":
        pool = [p for p in pool if p["pos"] == position]
    pool.sort(key=lambda p: p["score"], reverse=True)
    return {"gameweek": next_gw, "players": [
        {"name":p["web_name"],"team":p["team"],"pos":p["pos"],"price":p["price"],
         "score":p["score"],"ppg":p["ppg"],"form":p["form"],
         "xgi_per90":p["xgi_per90"],"fixture":p["fixture"],
         "dgw":p["dgw"],"bgw":p["bgw"]}
        for p in pool[:limit]
    ]}

def filter_players(position="ALL", max_price=None, min_price=None,
                   sort_by="ppg", min_minutes=90, team=None, limit=10):
    pool = list(players)
    if position != "ALL": pool = [p for p in pool if p["pos"]==position]
    if max_price: pool = [p for p in pool if p["price"]<=max_price]
    if min_price: pool = [p for p in pool if p["price"]>=min_price]
    if min_minutes: pool = [p for p in pool if p["minutes"]>=min_minutes]
    if team: pool = [p for p in pool if team.lower() in p["team"].lower()]
    sk = sort_by if sort_by in ("ppg","form","xg_per90","xa_per90","xgi_per90","ict","total_points","price") else "ppg"
    pool.sort(key=lambda p: p.get(sk,0), reverse=True)
    return {"results":[{"name":p["web_name"],"team":p["team"],"pos":p["pos"],
                        "price":p["price"],"fixture":p["fixture"],sk:round(p.get(sk,0),2)}
                       for p in pool[:limit]]}

def compare_players(player_a, player_b):
    def find(n):
        n = n.lower()
        for p in players:
            if n in p["name"].lower() or n in p["web_name"].lower(): return p
    pa, pb = find(player_a), find(player_b)
    if not pa: return {"error": f"Not found: {player_a}"}
    if not pb: return {"error": f"Not found: {player_b}"}
    def fmt(p):
        return {"name":p["web_name"],"team":p["team"],"pos":p["pos"],"price":p["price"],
                "ppg":p["ppg"],"form":p["form"],"xgi_per90":round(p["xgi_per90"],3),
                "ict":p["ict"],"total_points":p["total_points"],"fixture":p["fixture"],
                "dgw":p["dgw"],"bgw":p["bgw"]}
    return {"player_a":fmt(pa),"player_b":fmt(pb)}

def fixture_difficulty(team=None, gameweeks=3):
    gw_range = range(next_gw, min(next_gw+gameweeks, 39))
    tf = {t["name"]:[] for t in bootstrap["teams"]}
    for fix in fixtures:
        if fix["event"] not in gw_range: continue
        h = teams_by_id.get(fix["team_h"],"?")
        a = teams_by_id.get(fix["team_a"],"?")
        tf[h].append({"opp":a,"diff":fix["team_h_difficulty"],"venue":"H"})
        tf[a].append({"opp":h,"diff":fix["team_a_difficulty"],"venue":"A"})
    results = []
    for t_name, fixes in tf.items():
        if not fixes: continue
        if team and team.lower() not in t_name.lower(): continue
        avg = round(sum(f["diff"] for f in fixes)/len(fixes),1)
        results.append({"team":t_name,"avg_difficulty":avg,
                        "fixtures":", ".join(f"{f['opp']}({f['venue']}) diff:{f['diff']}" for f in fixes)})
    results.sort(key=lambda x: x["avg_difficulty"])
    return {"gameweeks":gameweeks,"from_gw":next_gw,"teams":results[:20] if not team else results}

def gameweek_overview(gameweeks_ahead=5):
    results = []
    for gw in range(next_gw, min(next_gw+gameweeks_ahead, 39)):
        s = gw_status.get(gw,{})
        results.append({"gw":gw,"dgw_teams":s.get("dgw",[]),"bgw_teams":s.get("bgw",[]),
                        "has_dgw":bool(s.get("dgw")),"has_bgw":bool(s.get("bgw"))})
    return {"from_gw":next_gw,"gameweeks":results}

def get_team(team_id):
    try:
        entry = requests.get(f"https://fantasy.premierleague.com/api/entry/{team_id}/").json()
        if "detail" in entry:
            return {"error": f"Team ID {team_id} not found."}
        picks_url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{next_gw}/picks/"
        picks_res = requests.get(picks_url).json()
        if "detail" in picks_res:
            picks_url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{next_gw-1}/picks/"
            picks_res = requests.get(picks_url).json()
            if "detail" in picks_res:
                return {"error": "Could not retrieve picks."}
        pid_map = {p["id"]: p for p in players}
        squad, captain, vc = [], None, None
        for pick in picks_res.get("picks",[]):
            p = pid_map.get(pick["element"])
            if not p: continue
            role = "XI" if pick["position"]<=11 else "Bench"
            if pick["is_captain"]: role="Captain"; captain=p["web_name"]
            if pick["is_vice_captain"]: role="Vice-Captain"; vc=p["web_name"]
            squad.append({"name":p["web_name"],"team":p["team"],"pos":p["pos"],
                          "price":p["price"],"ppg":p["ppg"],"form":p["form"],
                          "xgi_per90":round(p["xgi_per90"],3),"fixture":p["fixture"],
                          "dgw":p["dgw"],"bgw":p["bgw"],"role":role})
        xi = [p for p in squad if p["role"] in ("XI","Captain","Vice-Captain")]
        return {
            "manager": f"{entry.get('player_first_name','')} {entry.get('player_last_name','')}".strip(),
            "team_name": entry.get("name","Unknown"),
            "overall_rank": entry.get("summary_overall_rank"),
            "total_points": entry.get("summary_overall_points"),
            "captain": captain, "vice_captain": vc, "squad": squad,
            "bgw_warnings": [p["name"] for p in xi if p["bgw"]],
            "dgw_players":  [p["name"] for p in xi if p["dgw"]],
            "transfer_candidates": [p["name"] for p in sorted(
                [p for p in xi if not p["dgw"]], key=lambda p: p["xgi_per90"])[:3]],
        }
    except Exception as e:
        return {"error": str(e)}

def build_squad(style="balanced", excluded_teams=None, excluded_players=None,
                forced_players=None, budget=100.0, max_per_team=3):
    try: import pulp; use_milp=True
    except ImportError: use_milp=False
    excl_t = [t.lower() for t in (excluded_teams or [])]
    excl_p = [p.lower() for p in (excluded_players or [])]
    forced = [p.lower() for p in (forced_players or [])]
    pool   = [p for p in players
              if p["team"].lower() not in excl_t
              and not any(e in p["name"].lower() or e in p["web_name"].lower() for e in excl_p)]
    for p in pool: p["_score"] = score_player(p, style)
    pos_limits = {"GK":2,"DEF":5,"MID":5,"FWD":3}
    if use_milp:
        import pulp
        m  = pulp.LpProblem("FPL", pulp.LpMaximize)
        vs = {p["id"]: pulp.LpVariable(f"p_{p['id']}", cat="Binary") for p in pool}
        m += pulp.lpSum(p["_score"]*vs[p["id"]] for p in pool)
        m += pulp.lpSum(p["price"]*vs[p["id"]] for p in pool) <= budget
        m += pulp.lpSum(vs[p["id"]] for p in pool) == 15
        for pos, lim in pos_limits.items():
            m += pulp.lpSum(vs[p["id"]] for p in pool if p["pos"]==pos) == lim
        for team in set(p["team"] for p in pool):
            m += pulp.lpSum(vs[p["id"]] for p in pool if p["team"]==team) <= max_per_team
        for fp in forced:
            matches = [p for p in pool if fp in p["name"].lower() or fp in p["web_name"].lower()]
            if matches: m += vs[matches[0]["id"]] == 1
        m.solve(pulp.PULP_CBC_CMD(msg=0))
        selected = [p for p in pool if pulp.value(vs[p["id"]])==1]
    else:
        selected, counts = [], {}
        for pos, lim in pos_limits.items():
            for p in sorted([p for p in pool if p["pos"]==pos], key=lambda p: p["_score"], reverse=True):
                if len([s for s in selected if s["pos"]==pos]) >= lim: break
                if counts.get(p["team"],0) >= max_per_team: continue
                if sum(s["price"] for s in selected)+p["price"] > budget: continue
                selected.append(p); counts[p["team"]] = counts.get(p["team"],0)+1
    if len(selected) != 15:
        return {"error": f"Could not build valid squad (got {len(selected)})."}
    by_pos = {pos: sorted([p for p in selected if p["pos"]==pos], key=lambda p: p["_score"], reverse=True)
              for pos in pos_limits}
    best_xi, best_s, best_f = None, -1, "4-4-2"
    for nd in range(3,6):
        for nm in range(2,6):
            nf = 10-nd-nm
            if nf<1 or nf>4: continue
            if nd>len(by_pos["DEF"]) or nm>len(by_pos["MID"]) or nf>len(by_pos["FWD"]): continue
            xi = by_pos["GK"][:1]+by_pos["DEF"][:nd]+by_pos["MID"][:nm]+by_pos["FWD"][:nf]
            s  = sum(p["_score"] for p in xi)
            if s > best_s: best_s, best_xi, best_f = s, xi, f"{nd}-{nm}-{nf}"
    bench     = [p for p in selected if p not in best_xi]
    xi_sorted = sorted(best_xi, key=lambda p: p["_score"], reverse=True)
    captain, vice = xi_sorted[0]["web_name"], xi_sorted[1]["web_name"]
    def fmt(p, role):
        return {"name":p["web_name"],"team":p["team"],"pos":p["pos"],"price":p["price"],
                "score":round(p["_score"],2),"fixture":p["fixture"],"role":role,
                "dgw":p["dgw"],"bgw":p["bgw"]}
    players_out = (
        [fmt(p,"Captain" if p["web_name"]==captain else "Vice-Captain" if p["web_name"]==vice else "XI") for p in best_xi]+
        [fmt(p,"Bench") for p in sorted(bench, key=lambda p: p["_score"], reverse=True)]
    )
    return {"style":style,"formation":best_f,"gw":next_gw,
            "budget_used":round(sum(p["price"] for p in selected),1),
            "budget_remaining":round(budget-sum(p["price"] for p in selected),1),
            "captain":captain,"vice_captain":vice,"players":players_out,
            "dgw_players":[p["name"] for p in players_out if p["dgw"] and p["role"]!="Bench"],
            "bgw_warnings":[p["name"] for p in players_out if p["bgw"] and p["role"]!="Bench"]}

# ── Tools ─────────────────────────────────────────────────
TOOLS = [
    {"name":"get_fixtures","description":"Get verified fixtures for a gameweek. ALWAYS call this before mentioning any fixture or opponent.",
     "input_schema":{"type":"object","properties":{"gameweek":{"type":"integer"}}}},
    {"name":"get_top_players","description":"Get top scored players with verified fixture strings. Call before any transfer or captaincy discussion.",
     "input_schema":{"type":"object","properties":{
         "position":{"type":"string","enum":["GK","DEF","MID","FWD","ALL"]},
         "limit":{"type":"integer"}}}},
    {"name":"filter_players","description":"Filter players by position, price, or stat.",
     "input_schema":{"type":"object","properties":{
         "position":{"type":"string","enum":["GK","DEF","MID","FWD","ALL"]},
         "max_price":{"type":"number"},"min_price":{"type":"number"},
         "sort_by":{"type":"string","enum":["ppg","form","xg_per90","xa_per90","xgi_per90","ict","total_points","price"]},
         "min_minutes":{"type":"integer"},"team":{"type":"string"},"limit":{"type":"integer"}},
         "required":["position","sort_by"]}},
    {"name":"compare_players","description":"Compare two players head to head.",
     "input_schema":{"type":"object","properties":{
         "player_a":{"type":"string"},"player_b":{"type":"string"}},
         "required":["player_a","player_b"]}},
    {"name":"fixture_difficulty","description":"Fixture difficulty ratings for teams over multiple gameweeks.",
     "input_schema":{"type":"object","properties":{
         "team":{"type":"string"},"gameweeks":{"type":"integer"}}}},
    {"name":"gameweek_overview","description":"Show upcoming DGW and BGW gameweeks.",
     "input_schema":{"type":"object","properties":{
         "gameweeks_ahead":{"type":"integer"}}}},
    {"name":"get_team","description":"Fetch and analyse a user's FPL team by their team ID.",
     "input_schema":{"type":"object","properties":{
         "team_id":{"type":"integer"}},
         "required":["team_id"]}},
    {"name":"build_squad","description":"Build an optimised 15-player FPL squad.",
     "input_schema":{"type":"object","properties":{
         "style":{"type":"string","enum":["cautious","balanced","aggressive"]},
         "excluded_teams":{"type":"array","items":{"type":"string"}},
         "excluded_players":{"type":"array","items":{"type":"string"}},
         "forced_players":{"type":"array","items":{"type":"string"}},
         "budget":{"type":"number"},"max_per_team":{"type":"integer"}},
         "required":["style"]}},
]

TOOL_FNS = {
    "get_fixtures":       get_fixtures,
    "get_top_players":    get_top_players,
    "filter_players":     filter_players,
    "compare_players":    compare_players,
    "fixture_difficulty": fixture_difficulty,
    "gameweek_overview":  gameweek_overview,
    "get_team":           get_team,
    "build_squad":        build_squad,
}

# ── System prompt ─────────────────────────────────────────
# Inject verified fixture list so Claude has ground truth
_fix_lines = []
for fix in fixtures:
    if fix["event"] != next_gw: continue
    h = teams_by_id.get(fix["team_h"],"?")
    a = teams_by_id.get(fix["team_a"],"?")
    _fix_lines.append(f"  {h} vs {a}")

SYSTEM = f"""You are an FPL assistant for GW{next_gw}. You have tools that return live data from the FPL API.

VERIFIED GW{next_gw} FIXTURES (these are the only valid fixtures this week):
{chr(10).join(_fix_lines) if _fix_lines else "  No fixtures found"}

CRITICAL RULES:
1. ALWAYS call get_fixtures or get_top_players before mentioning any fixture or player recommendation
2. Only reference fixtures from the verified list above or from tool results — never from your own knowledge
3. Never mention Leeds, Championship clubs, or teams not in the verified fixture list
4. After build_squad or get_team: give brief insights only — the app displays the squad table automatically
5. Every fixture you mention must match a result from get_fixtures exactly

FPL RULES:
- DGW players are priority targets (1.8x scoring boost)
- BGW players should be avoided or benched
- Focus on xGI/90, form, and fixture difficulty when recommending players
- PPG above 6 = excellent, below 4 = concern
- Always give a reason grounded in the tool data"""

# ── Render helpers ────────────────────────────────────────
def render_squad(data):
    if "error" in data: st.error(data["error"]); return
    st.markdown(f"**{data['formation']}** | GW{data['gw']} | £{data['budget_used']}m used | £{data['budget_remaining']}m left")
    st.markdown(f"**Captain:** {data['captain']} | **Vice:** {data['vice_captain']}")
    if data.get("dgw_players"): st.success(f"⚡ DGW: {', '.join(data['dgw_players'])}")
    if data.get("bgw_warnings"): st.warning(f"⚠️ BGW: {', '.join(data['bgw_warnings'])}")
    rows = [{"":("🔲" if p["role"]=="Bench" else "✅"),
             "Player":p["name"]+(" 🟡" if p["role"]=="Captain" else " 🔵" if p["role"]=="Vice-Captain" else ""),
             "Pos":p["pos"],"Team":p["team"],"£":f"£{p['price']}m",
             "Fixture":p["fixture"],"Role":p["role"]} for p in data["players"]]
    st.dataframe(rows, use_container_width=True, hide_index=True)

def render_team(data):
    if "error" in data: st.error(data["error"]); return
    rank = data.get("overall_rank")
    rank_str = f"{rank:,}" if isinstance(rank, int) else str(rank) if rank else "?"
    st.markdown(f"**{data['team_name']}** ({data['manager']}) | Rank: {rank_str} | Pts: {data.get('total_points','?')}")
    st.markdown(f"**Captain:** {data['captain']} | **Vice:** {data['vice_captain']}")
    if data.get("dgw_players"): st.success(f"⚡ DGW: {', '.join(data['dgw_players'])}")
    if data.get("bgw_warnings"): st.warning(f"⚠️ BGW: {', '.join(data['bgw_warnings'])}")
    rows = [{"":("🔲" if p["role"]=="Bench" else "✅"),
             "Player":p["name"]+(" 🟡" if p["role"]=="Captain" else " 🔵" if p["role"]=="Vice-Captain" else ""),
             "Pos":p["pos"],"Team":p["team"],"£":f"£{p['price']}m",
             "PPG":p["ppg"],"Form":p["form"],"xGI/90":p["xgi_per90"],
             "Fixture":p["fixture"],"Role":p["role"]} for p in data["squad"]]
    st.dataframe(rows, use_container_width=True, hide_index=True)

# ── Agent ─────────────────────────────────────────────────
def run_agent(history):
    client        = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    squad_renders = []
    for _ in range(8):
        resp = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=1500,
            system=SYSTEM, tools=TOOLS, messages=history,
        )
        history.append({"role":"assistant","content":resp.content})
        if resp.stop_reason != "tool_use": break
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                fn     = TOOL_FNS.get(block.name)
                result = fn(**block.input) if fn else {"error":"unknown tool"}
                if block.name in ("build_squad","get_team"):
                    squad_renders.append((block.name, result))
                tool_results.append({"type":"tool_result","tool_use_id":block.id,"content":json.dumps(result)})
        history.append({"role":"user","content":tool_results})
    reply = "".join(b.text for b in resp.content if hasattr(b,"text"))
    safe  = []
    for msg in history:
        if isinstance(msg["content"], list):
            safe.append({"role":msg["role"],"content":[
                b.model_dump() if hasattr(b,"model_dump") else b for b in msg["content"]]})
        else:
            safe.append(msg)
    return reply, safe, squad_renders

# ── Landing page ──────────────────────────────────────────
if st.session_state.show_landing and not st.session_state.show_how_it_works:
    st.markdown("""
    <div class="hero">
        <div style="font-size:2.5rem">⚽</div>
        <h1>FPL Agent</h1>
        <p>AI-powered Fantasy Premier League advice, squad builder, and fixture analysis.<br>Live data updated every gameweek.</p>
        <div class="feature-row">
            <span class="feature-pill">Squad builder</span>
            <span class="feature-pill">Player comparisons</span>
            <span class="feature-pill">Fixture analysis</span>
            <span class="feature-pill">xG &amp; xA stats</span>
            <span class="feature-pill">DGW &amp; BGW alerts</span>
        </div>
    </div>""", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        if st.button("Get started →", use_container_width=True, type="primary"):
            st.session_state.show_how_it_works = True
            st.rerun()
        st.markdown(BMC_LINK, unsafe_allow_html=True)
    st.stop()

# ── How it works ──────────────────────────────────────────
if st.session_state.show_how_it_works:
    st.markdown("### ⚽ How FPL Agent works")
    st.divider()
    st.markdown("""
Most FPL tools show you a table of stats and leave you to figure it out. FPL Agent does the thinking for you.

**The squad builder** runs a full mathematical optimisation across every available player, balancing:
- xG and xA per 90 minutes (not raw goals/assists)
- Position-adjusted scoring (clean sheets worth different amounts by position)
- Form weighting alongside season-long stats
- Fixture difficulty multipliers
- Double gameweek boosts (1.8x) and blank gameweek penalties
- Budget optimisation using linear programming

The engine tests every legal formation, picks the best XI, orders the bench, and assigns captaincy automatically.

> Grounded in the same statistical principles used by professional analysts. Smarter decisions over a full season is how you climb the overall rankings.
    """)
    st.divider()
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        if st.button("Let's go →", use_container_width=True, type="primary"):
            st.session_state.show_how_it_works = False
            st.session_state.show_landing      = False
            st.rerun()
        if st.button("← Back", use_container_width=True):
            st.session_state.show_how_it_works = False
            st.rerun()
        st.markdown(BMC_LINK, unsafe_allow_html=True)
    st.stop()

# ── Main chat ─────────────────────────────────────────────
st.markdown(f"### ⚽ FPL Agent — GW{next_gw}")
st.markdown(f'<div style="text-align:right;font-size:12px"><a href="{BMC_URL}" target="_blank"><strong>☕ Buy me a coffee if you find it useful</strong></a></div>', unsafe_allow_html=True)
st.divider()

with st.expander("💡 Get personalised advice — enter your FPL Team ID"):
    st.markdown("""
**How to find your FPL Team ID:**
1. Go to [fantasy.premierleague.com](https://fantasy.premierleague.com) → Points or Pick Team
2. Your ID is the number in the URL: `fantasy.premierleague.com/entry/`**`1234567`**`/event/36`

Then ask: *"My FPL ID is 1234567, analyse my team"*
    """)

if not st.session_state.messages:
    cols = st.columns(2)
    for i, p in enumerate([
        "Build me a balanced squad for £100m",
        "Best value midfielders under £7m?",
        "Compare Salah and Saka",
        "Any double gameweeks coming up?",
    ]):
        if cols[i%2].button(p, use_container_width=True):
            st.session_state.pending_prompt = p
            st.rerun()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if st.session_state.question_count > 0 and st.session_state.question_count % 3 == 0:
    st.markdown(f'<div class="nudge">☕ Finding FPL Agent useful? <a href="{BMC_URL}" target="_blank">Buy me a coffee</a> — completely optional, always appreciated.</div>', unsafe_allow_html=True)

user_input = st.chat_input("Ask anything about FPL...") or st.session_state.pop("pending_prompt", None)

if user_input:
    st.session_state.messages.append({"role":"user","content":user_input})
    with st.chat_message("user"): st.markdown(user_input)
    st.session_state.history.append({"role":"user","content":user_input})
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            reply, safe_history, squad_renders = run_agent(st.session_state.history)
        for tool_name, data in squad_renders:
            if tool_name == "build_squad": render_squad(data)
            elif tool_name == "get_team":  render_team(data)
        if reply: st.markdown(reply)
    st.session_state.messages.append({"role":"assistant","content":reply})
    st.session_state.history       = safe_history
    st.session_state.question_count += 1
    log_question(user_input)
    st.rerun()
