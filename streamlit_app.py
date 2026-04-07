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

# ── Page config ───────────────────────────────────────────
st.set_page_config(page_title="FPL Agent", page_icon="⚽", layout="centered")

# ── Custom CSS ────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { max-width: 720px; padding-top: 2rem; }
    .stChatMessage { border-radius: 12px; }
    div[data-testid="stChatInput"] { border-radius: 12px; }
    .hero { text-align: center; padding: 2.5rem 1rem 1.5rem; }
    .hero h1 { font-size: 2rem; font-weight: 700; margin: 0.5rem 0 0.25rem; }
    .hero p  { font-size: 1rem; color: #6b7280; margin: 0 0 1.75rem; }
    .feature-row { display: flex; justify-content: center; gap: 1.5rem; margin-bottom: 2rem; flex-wrap: wrap; }
    .feature-pill { background: #f3f4f6; border-radius: 999px; padding: 6px 16px; font-size: 13px; color: #374151; }
    .nudge-banner {
        background: #f0fdf4; border: 1px solid #86efac; border-radius: 12px;
        padding: 1rem 1.25rem; margin: 1rem 0; text-align: center;
    }
    .nudge-banner p { margin: 0; font-size: 14px; color: #166534; }
    .divider { text-align: center; color: #9ca3af; font-size: 12px; margin: 1rem 0; }
    .free-note { text-align: center; font-size: 12px; color: #9ca3af; margin-top: 1rem; }
</style>
""", unsafe_allow_html=True)

BMC_URL = "https://buymeacoffee.com/fplagent"
BMC_LINK = f'<div class="free-note"><a href="{BMC_URL}" target="_blank"><strong>☕ Buy me a coffee if you find it useful</strong></a></div>'

# ── Google Sheets logging ─────────────────────────────────
@st.cache_resource
def get_sheet():
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        gc = gspread.authorize(creds)
        return gc.open_by_key(st.secrets["SHEET_ID"]).sheet1
    except Exception:
        return None

def log_question(question):
    try:
        sheet = get_sheet()
        if sheet:
            sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), question])
    except Exception:
        pass

# ── Session state defaults ────────────────────────────────
for key, default in {
    "messages": [],
    "history": [],
    "question_count": 0,
    "show_how_it_works": False,
    "show_landing": True,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ── Load FPL data ─────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_fpl_data():
    bootstrap = requests.get("https://fantasy.premierleague.com/api/bootstrap-static/").json()
    fixtures  = requests.get("https://fantasy.premierleague.com/api/fixtures/").json()
    teams_by_id = {t["id"]: t["name"] for t in bootstrap["teams"]}
    pos_map     = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    next_gw     = next((e["id"] for e in bootstrap["events"] if not e.get("finished", True)), 38)

    players = []
    for p in bootstrap["elements"]:
        if p["status"] != "a" or p["minutes"] < 90:
            continue
        players.append({
            "id": p["id"], "name": f"{p['first_name']} {p['second_name']}",
            "web_name": p["web_name"], "team": teams_by_id.get(p["team"], "?"),
            "pos": pos_map.get(p["element_type"], "?"), "price": p["now_cost"] / 10,
            "total_points": p["total_points"], "ppg": float(p["points_per_game"] or 0),
            "form": float(p["form"] or 0), "minutes": p["minutes"],
            "goals": p["goals_scored"], "assists": p["assists"],
            "clean_sheets": p["clean_sheets"],
            "xg": float(p.get("expected_goals") or 0),
            "xa": float(p.get("expected_assists") or 0),
            "xgi": float(p.get("expected_goal_involvements") or 0),
            "xg_per90": float(p.get("expected_goals_per_90") or 0),
            "xa_per90": float(p.get("expected_assists_per_90") or 0),
            "xgi_per90": float(p.get("expected_goal_involvements_per_90") or 0),
            "ict": float(p.get("ict_index") or 0),
            "selected_pct": float(p.get("selected_by_percent") or 0),
            "pen_order": p.get("penalties_order"), "status": p["status"],
        })

    team_gw_fixture_count = {}
    for fix in fixtures:
        gw = fix["event"]
        if gw is None:
            continue
        for tid in [fix["team_h"], fix["team_a"]]:
            team_gw_fixture_count.setdefault(tid, {})
            team_gw_fixture_count[tid][gw] = team_gw_fixture_count[tid].get(gw, 0) + 1

    all_team_ids  = {t["id"] for t in bootstrap["teams"]}
    remaining_gws = [e["id"] for e in bootstrap["events"] if not e.get("finished", True)]

    gw_status = {}
    for gw in remaining_gws:
        dgw = {tid for tid in all_team_ids if team_gw_fixture_count.get(tid, {}).get(gw, 0) >= 2}
        bgw = {tid for tid in all_team_ids if team_gw_fixture_count.get(tid, {}).get(gw, 0) == 0}
        if dgw or bgw:
            gw_status[gw] = {
                "dgw": [teams_by_id[tid] for tid in dgw],
                "bgw": [teams_by_id[tid] for tid in bgw],
            }

    dgw_teams = {tid for tid in all_team_ids if team_gw_fixture_count.get(tid, {}).get(next_gw, 0) >= 2}
    bgw_teams = {tid for tid in all_team_ids if team_gw_fixture_count.get(tid, {}).get(next_gw, 0) == 0}

    next_gw_diff, next_gw_fix_str = {}, {}
    for fix in fixtures:
        if fix["event"] != next_gw:
            continue
        h, a = fix["team_h"], fix["team_a"]
        next_gw_diff.setdefault(h, []).append(fix["team_h_difficulty"])
        next_gw_diff.setdefault(a, []).append(fix["team_a_difficulty"])
        next_gw_fix_str.setdefault(h, []).append(f"{teams_by_id.get(a,'?')}(H)")
        next_gw_fix_str.setdefault(a, []).append(f"{teams_by_id.get(h,'?')}(A)")

    for p in players:
        t_id = next((t["id"] for t in bootstrap["teams"] if t["name"] == p["team"]), 0)
        p["dgw"] = t_id in dgw_teams
        p["bgw"] = t_id in bgw_teams
        p["fixture_count"] = team_gw_fixture_count.get(t_id, {}).get(next_gw, 1)

    return (bootstrap, fixtures, teams_by_id, players, next_gw,
            next_gw_diff, next_gw_fix_str, dgw_teams, bgw_teams,
            team_gw_fixture_count, gw_status)

(bootstrap, fixtures, teams_by_id, players, next_gw,
 next_gw_diff, next_gw_fix_str, dgw_teams, bgw_teams,
 team_gw_fixture_count, gw_status) = load_fpl_data()

# ── Tool implementations ──────────────────────────────────
def filter_players(position="ALL", max_price=None, min_price=None,
                   sort_by="ppg", min_minutes=90, team=None, limit=10):
    pool = list(players)
    if position != "ALL":
        pool = [p for p in pool if p["pos"] == position]
    if max_price:
        pool = [p for p in pool if p["price"] <= max_price]
    if min_price:
        pool = [p for p in pool if p["price"] >= min_price]
    if min_minutes:
        pool = [p for p in pool if p["minutes"] >= min_minutes]
    if team:
        pool = [p for p in pool if team.lower() in p["team"].lower()]
    sort_key = sort_by if sort_by in ("ppg","form","xg_per90","xa_per90","xgi_per90","ict","total_points","price") else "ppg"
    pool.sort(key=lambda p: p.get(sort_key, 0), reverse=True)
    return {"sort_by": sort_by, "position": position, "results": [
        {"name": p["web_name"], "team": p["team"], "pos": p["pos"],
         "price": p["price"], sort_key: round(p.get(sort_key, 0), 2)}
        for p in pool[:limit]
    ]}

def top_stat_leaders(stat="xg", position="ALL", limit=10):
    stat_map = {"xg":"xg","xa":"xa","xgi":"xgi","goals":"goals","assists":"assists",
                "clean_sheets":"clean_sheets","ict":"ict","form":"form","ppg":"ppg","total_points":"total_points"}
    key  = stat_map.get(stat, "xg")
    pool = [p for p in players if (position == "ALL" or p["pos"] == position)]
    pool.sort(key=lambda p: p.get(key, 0), reverse=True)
    return {"stat": stat, "position": position, "leaders": [
        {"name": p["web_name"], "team": p["team"], "pos": p["pos"],
         "price": p["price"], "value": round(p.get(key, 0), 2)}
        for p in pool[:limit]
    ]}

def compare_players(player_a, player_b):
    def find(name):
        name = name.lower()
        for p in players:
            if name in p["name"].lower() or name in p["web_name"].lower():
                return p
    pa, pb = find(player_a), find(player_b)
    if not pa: return {"error": f"Not found: {player_a}"}
    if not pb: return {"error": f"Not found: {player_b}"}
    def fmt(p):
        return {"name": p["web_name"], "team": p["team"], "pos": p["pos"],
                "price": p["price"], "ppg": p["ppg"], "form": p["form"],
                "xg_per90": round(p["xg_per90"],3), "xa_per90": round(p["xa_per90"],3),
                "xgi_per90": round(p["xgi_per90"],3), "ict": p["ict"],
                "total_points": p["total_points"], "selected_pct": p["selected_pct"]}
    return {"player_a": fmt(pa), "player_b": fmt(pb)}

def fixture_difficulty(team=None, gameweeks=3):
    gw_range      = range(next_gw, min(next_gw + gameweeks, 39))
    team_fixtures = {t["name"]: [] for t in bootstrap["teams"]}
    for fix in fixtures:
        if fix["event"] not in gw_range: continue
        h_name = teams_by_id.get(fix["team_h"], "?")
        a_name = teams_by_id.get(fix["team_a"], "?")
        team_fixtures[h_name].append({"opp": a_name, "diff": fix["team_h_difficulty"], "venue": "H"})
        team_fixtures[a_name].append({"opp": h_name, "diff": fix["team_a_difficulty"], "venue": "A"})
    results = []
    for t_name, fixes in team_fixtures.items():
        if not fixes: continue
        if team and team.lower() not in t_name.lower(): continue
        avg = round(sum(f["diff"] for f in fixes) / len(fixes), 1)
        results.append({"team": t_name, "avg_difficulty": avg,
                        "fixtures": ", ".join(f"{f['opp']}({f['venue']}) diff:{f['diff']}" for f in fixes)})
    results.sort(key=lambda x: x["avg_difficulty"])
    return {"gameweeks": gameweeks, "from_gw": next_gw, "teams": results[:20] if not team else results}

def get_team(team_id):
    try:
        entry = requests.get(f"https://fantasy.premierleague.com/api/entry/{team_id}/").json()
        if "detail" in entry:
            return {"error": f"Team ID {team_id} not found. Please check your FPL ID."}
        picks_url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{next_gw}/picks/"
        picks_res = requests.get(picks_url).json()
        if "detail" in picks_res:
            picks_url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{next_gw - 1}/picks/"
            picks_res = requests.get(picks_url).json()
            if "detail" in picks_res:
                return {"error": "Could not retrieve team picks. Try again later."}
        player_by_id = {p["id"]: p for p in players}
        squad, captain, vice_captain = [], None, None
        for pick in picks_res.get("picks", []):
            pid    = pick["element"]
            player = player_by_id.get(pid)
            if not player:
                continue
            t_id = next((t["id"] for t in bootstrap["teams"] if t["name"] == player["team"]), 0)
            fix  = ", ".join(next_gw_fix_str.get(t_id, ["?"]))
            role = "XI" if pick["position"] <= 11 else "Bench"
            if pick["is_captain"]:
                role = "Captain"
                captain = player["web_name"]
            if pick["is_vice_captain"]:
                role = "Vice-Captain"
                vice_captain = player["web_name"]
            squad.append({
                "name":      player["web_name"],
                "team":      player["team"],
                "pos":       player["pos"],
                "price":     player["price"],
                "ppg":       player["ppg"],
                "form":      player["form"],
                "xgi_per90": round(player["xgi_per90"], 3),
                "fixture":   fix,
                "dgw":       t_id in dgw_teams,
                "bgw":       t_id in bgw_teams,
                "role":      role,
            })
        bgw_players        = [p["name"] for p in squad if p["bgw"] and p["role"] in ("XI","Captain","Vice-Captain")]
        dgw_players        = [p["name"] for p in squad if p["dgw"] and p["role"] in ("XI","Captain","Vice-Captain")]
        xi                 = [p for p in squad if p["role"] in ("XI","Captain","Vice-Captain")]
        transfer_candidates = sorted([p for p in xi if not p["dgw"]], key=lambda p: p["xgi_per90"])[:3]
        return {
            "manager":             f"{entry.get('player_first_name','')} {entry.get('player_last_name','')}".strip(),
            "team_name":           entry.get("name", "Unknown"),
            "overall_rank":        entry.get("summary_overall_rank"),
            "total_points":        entry.get("summary_overall_points"),
            "gw":                  next_gw,
            "captain":             captain,
            "vice_captain":        vice_captain,
            "squad":               squad,
            "bgw_warnings":        bgw_players,
            "dgw_players":         dgw_players,
            "transfer_candidates": [p["name"] for p in transfer_candidates],
        }
    except Exception as e:
        return {"error": f"Something went wrong: {str(e)}"}

def gameweek_overview(gameweeks_ahead=5):
    results = []
    for gw in range(next_gw, min(next_gw + gameweeks_ahead, 39)):
        status = gw_status.get(gw, {})
        results.append({
            "gw":        gw,
            "dgw_teams": status.get("dgw", []),
            "bgw_teams": status.get("bgw", []),
            "has_dgw":   bool(status.get("dgw")),
            "has_bgw":   bool(status.get("bgw")),
        })
    return {"from_gw": next_gw, "gameweeks": results}

def build_squad(style="balanced", excluded_teams=None, excluded_players=None,
                forced_players=None, budget=100.0, max_per_team=3):
    try:
        import pulp
        use_milp = True
    except ImportError:
        use_milp = False
    excl_teams   = [t.lower() for t in (excluded_teams or [])]
    excl_players = [p.lower() for p in (excluded_players or [])]
    forced       = [p.lower() for p in (forced_players or [])]

    def score(p):
        mins     = max(p["minutes"], 1)
        gpts     = {"FWD":4,"MID":5,"DEF":6,"GK":6}[p["pos"]]
        attack   = (p["xg_per90"]*0.5 + (p["goals"]/mins*90)*0.5)*gpts
        attack  += (p["xa_per90"]*0.5 + (p["assists"]/mins*90)*0.5)*3
        cs_pts   = {"GK":4,"DEF":4,"MID":1,"FWD":0}[p["pos"]]
        defence  = (p["clean_sheets"]/mins*90)*cs_pts
        bonus    = p["ppg"]*0.3
        form_w   = 0.5 if style=="cautious" else 0.3
        t_id     = next((t["id"] for t in bootstrap["teams"] if t["name"]==p["team"]),0)
        diffs    = next_gw_diff.get(t_id, [3])
        fix_d    = sum(diffs)/max(1,len(diffs))
        fix_pow  = 0.8 if style=="cautious" else 1.4
        atk_w    = 0.8 if style=="cautious" else 1.5
        fix_mult = ((6-fix_d)/5)**fix_pow
        raw      = attack*atk_w + defence + bonus
        base     = raw*(1+form_w*(p["form"]/10))*fix_mult
        if t_id in dgw_teams:
            base *= 1.8
        elif t_id in bgw_teams:
            base *= 0.1
        return base

    pool = [p for p in players
            if p["team"].lower() not in excl_teams
            and not any(e in p["name"].lower() or e in p["web_name"].lower() for e in excl_players)]
    for p in pool:
        p["_score"] = score(p)

    pos_limits = {"GK":2,"DEF":5,"MID":5,"FWD":3}

    if use_milp:
        import pulp
        model = pulp.LpProblem("FPL", pulp.LpMaximize)
        vs    = {p["id"]: pulp.LpVariable(f"p_{p['id']}", cat="Binary") for p in pool}
        model += pulp.lpSum(p["_score"]*vs[p["id"]] for p in pool)
        model += pulp.lpSum(p["price"]*vs[p["id"]] for p in pool) <= budget
        model += pulp.lpSum(vs[p["id"]] for p in pool) == 15
        for pos, lim in pos_limits.items():
            model += pulp.lpSum(vs[p["id"]] for p in pool if p["pos"]==pos) == lim
        for team in set(p["team"] for p in pool):
            model += pulp.lpSum(vs[p["id"]] for p in pool if p["team"]==team) <= max_per_team
        for fp in forced:
            matches = [p for p in pool if fp in p["name"].lower() or fp in p["web_name"].lower()]
            if matches: model += vs[matches[0]["id"]] == 1
        model.solve(pulp.PULP_CBC_CMD(msg=0))
        selected = [p for p in pool if pulp.value(vs[p["id"]])==1]
    else:
        selected, counts = [], {}
        for pos, lim in pos_limits.items():
            candidates = sorted([p for p in pool if p["pos"]==pos], key=lambda p: p["_score"], reverse=True)
            for p in candidates:
                if len([s for s in selected if s["pos"]==pos]) >= lim: break
                if counts.get(p["team"],0) >= max_per_team: continue
                if sum(s["price"] for s in selected)+p["price"] > budget: continue
                selected.append(p)
                counts[p["team"]] = counts.get(p["team"],0)+1

    if len(selected) != 15:
        return {"error": f"Could not build valid squad (got {len(selected)}). Try relaxing constraints."}

    by_pos = {pos: sorted([p for p in selected if p["pos"]==pos], key=lambda p: p["_score"], reverse=True)
              for pos in pos_limits}
    best_xi, best_score, best_formation = None, -1, "4-4-2"
    for nd in range(3,6):
        for nm in range(2,6):
            nf = 10-nd-nm
            if nf<1 or nf>4: continue
            if nd>len(by_pos["DEF"]) or nm>len(by_pos["MID"]) or nf>len(by_pos["FWD"]): continue
            xi = by_pos["GK"][:1]+by_pos["DEF"][:nd]+by_pos["MID"][:nm]+by_pos["FWD"][:nf]
            s  = sum(p["_score"] for p in xi)
            if s > best_score: best_score, best_xi, best_formation = s, xi, f"{nd}-{nm}-{nf}"

    bench     = [p for p in selected if p not in best_xi]
    xi_sorted = sorted(best_xi, key=lambda p: p["_score"], reverse=True)
    captain, vice = xi_sorted[0]["web_name"], xi_sorted[1]["web_name"]

    def fmt(p, role):
        t_id = next((t["id"] for t in bootstrap["teams"] if t["name"]==p["team"]),0)
        fix  = ", ".join(next_gw_fix_str.get(t_id, ["?"]))
        return {"name":p["web_name"],"team":p["team"],"pos":p["pos"],
                "price":p["price"],"score":round(p["_score"],2),"fixture":fix,"role":role}

    players_out = (
        [fmt(p, "Captain" if p["web_name"]==captain else "Vice-Captain" if p["web_name"]==vice else "XI") for p in best_xi] +
        [fmt(p, "Bench") for p in sorted(bench, key=lambda p: p["_score"], reverse=True)]
    )

    bgw_warnings = [p["web_name"] for p in best_xi
                    if next((t["id"] for t in bootstrap["teams"] if t["name"]==p["team"]),0) in bgw_teams]
    dgw_players  = [p["web_name"] for p in best_xi
                    if next((t["id"] for t in bootstrap["teams"] if t["name"]==p["team"]),0) in dgw_teams]

    return {"style":style,"formation":best_formation,"gw":next_gw,
            "budget_used":round(sum(p["price"] for p in selected),1),
            "budget_remaining":round(budget-sum(p["price"] for p in selected),1),
            "captain":captain,"vice_captain":vice,"players":players_out,
            "dgw_players":dgw_players,"bgw_warnings":bgw_warnings,
            "dgw_teams":[teams_by_id[tid] for tid in dgw_teams],
            "bgw_teams":[teams_by_id[tid] for tid in bgw_teams]}

# ── Tools definition ──────────────────────────────────────
TOOLS = [
    {"name":"filter_players","description":"Filter and rank players by stats.",
     "input_schema":{"type":"object","properties":{
         "position":{"type":"string","enum":["GK","DEF","MID","FWD","ALL"]},
         "max_price":{"type":"number"},"min_price":{"type":"number"},
         "sort_by":{"type":"string","enum":["ppg","form","xg_per90","xa_per90","xgi_per90","ict","total_points","price"]},
         "min_minutes":{"type":"integer"},"team":{"type":"string"},"limit":{"type":"integer"}},
         "required":["position","sort_by"]}},
    {"name":"top_stat_leaders","description":"Top players for a given stat.",
     "input_schema":{"type":"object","properties":{
         "stat":{"type":"string","enum":["xg","xa","xgi","goals","assists","clean_sheets","ict","form","ppg","total_points"]},
         "position":{"type":"string","enum":["GK","DEF","MID","FWD","ALL"]},"limit":{"type":"integer"}},
         "required":["stat"]}},
    {"name":"compare_players","description":"Head-to-head comparison of two players.",
     "input_schema":{"type":"object","properties":{
         "player_a":{"type":"string"},"player_b":{"type":"string"}},
         "required":["player_a","player_b"]}},
    {"name":"fixture_difficulty","description":"Upcoming fixture difficulty for teams.",
     "input_schema":{"type":"object","properties":{
         "team":{"type":"string"},"gameweeks":{"type":"integer"}}}},
    {"name":"get_team","description":"Fetch and analyse a user's actual FPL team by their team ID.",
     "input_schema":{"type":"object","properties":{
         "team_id":{"type":"integer","description":"The user's FPL team ID"}},
         "required":["team_id"]}},
    {"name":"gameweek_overview","description":"Show which upcoming gameweeks have double or blank gameweeks and which teams are affected.",
     "input_schema":{"type":"object","properties":{
         "gameweeks_ahead":{"type":"integer","description":"How many GWs ahead to look, default 5"}}}},
    {"name":"build_squad","description":"Build an optimised 15-player FPL squad.",
     "input_schema":{"type":"object","properties":{
         "style":{"type":"string","enum":["cautious","aggressive","balanced"]},
         "excluded_teams":{"type":"array","items":{"type":"string"}},
         "excluded_players":{"type":"array","items":{"type":"string"}},
         "forced_players":{"type":"array","items":{"type":"string"}},
         "budget":{"type":"number"},"max_per_team":{"type":"integer"}},
         "required":["style"]}},
]

TOOL_FNS = {
    "filter_players":     filter_players,
    "top_stat_leaders":   top_stat_leaders,
    "compare_players":    compare_players,
    "fixture_difficulty": fixture_difficulty,
    "get_team":           get_team,
    "gameweek_overview":  gameweek_overview,
    "build_squad":        build_squad,
}

# ── Build live context for system prompt ──────────────────
def build_live_context():
    team_lines = "\n".join(f"  {t['id']}: {t['name']}" for t in bootstrap["teams"])
    fix_lines  = []
    for fix in fixtures:
        if fix["event"] != next_gw:
            continue
        h = teams_by_id.get(fix["team_h"], "?")
        a = teams_by_id.get(fix["team_a"], "?")
        fix_lines.append(f"  {h}(H) vs {a}(A) — home diff:{fix['team_h_difficulty']} away diff:{fix['team_a_difficulty']}")
    gw_lines = []
    for gw in range(next_gw, min(next_gw + 5, 39)):
        status = gw_status.get(gw, {})
        dgw    = ", ".join(status.get("dgw", [])) or "None"
        bgw    = ", ".join(status.get("bgw", [])) or "None"
        gw_lines.append(f"  GW{gw}: DGW={dgw} | BGW={bgw}")
    return f"""
LIVE FPL DATA (source: official FPL API, treat as ground truth):

Premier League teams this season:
{team_lines}

GW{next_gw} fixtures (these are the ONLY valid fixtures — never use any other):
{chr(10).join(fix_lines)}

Upcoming DGW/BGW schedule:
{chr(10).join(gw_lines)}
"""

LIVE_CONTEXT = build_live_context()

SYSTEM = f"""You are an expert Fantasy Premier League (FPL) assistant with access to live GW{next_gw} data via tools.

{LIVE_CONTEXT}

CORE RULES:
- The live FPL data above is ground truth — never contradict it
- Only reference teams that appear in the team list above
- Only reference fixtures that appear in the GW{next_gw} fixture list above
- ALWAYS call the relevant tool before answering — never use general football knowledge
- Never write out player names, teams, fixtures, or prices yourself — these come from tool results only
- Do not format or list squad players yourself — the app displays squad data automatically from tool results
- Your job after a build_squad or get_team call is to provide INSIGHTS and REASONING only — not to repeat the squad list
- Every stat you quote MUST come from a tool call in this conversation
- Never invent team names, fixtures, opponents, prices, or scores under any circumstances

FPL KNOWLEDGE & DECISION FRAMEWORKS:

Captain picks:
- Weight form (last 3 GWs), fixture difficulty, and xG per 90 equally
- DGW players should almost always be captained unless fixture difficulty is 5
- Premiums are safer captain picks; differentials carry more risk

Transfers:
- Never take a points hit unless the player has a BGW, injury concern, or blanked 3+ GWs in a row
- Always consider the next 3 GWs of fixtures, not just the next one
- Chasing last week's scores is the most common FPL mistake — focus on underlying xG/xA
- A player with high xG but low goals is likely to score soon — this is value
- A player with low xG but high goals is likely to regress — this is risk

Value & budget:
- Value = total points divided by price. Anything above 6pts per million is good value
- Budget players (under £5.5m) should be reliable starters with clean sheet potential

Fixtures:
- Difficulty 1-2 = easy, 3 = neutral, 4-5 = tough
- Home fixtures are generally easier than away for defenders and goalkeepers

Stats interpretation:
- xG is more predictive than actual goals over a full season
- ICT index above 40 indicates heavy attacking involvement
- PPG above 6 is excellent, above 5 is good, below 4 is a concern
- Form is the last 5 GW average
- Selected by % above 30% = template, below 5% = differential

DOUBLE/BLANK GAMEWEEK AWARENESS:
- Use the gameweek_overview tool whenever a user asks about upcoming DGWs, BGWs, or fixture schedules
- GW{next_gw} DGW teams: {', '.join(teams_by_id[tid] for tid in dgw_teams) if dgw_teams else 'None'}
- GW{next_gw} BGW teams: {', '.join(teams_by_id[tid] for tid in bgw_teams) if bgw_teams else 'None'}
- DGW players get a 1.8x score boost in squad builds. BGW players are heavily penalised
- Always flag BGW warnings and highlight DGW players as priority targets

PERSONALISED TEAM ANALYSIS:
- Use the get_team tool whenever a user shares their FPL ID
- After fetching: confirm team name and manager, flag BGW players in XI, highlight DGW players owned, identify 2-3 weakest players by xgi_per90 as transfer candidates, give captain recommendation with reasoning

OUTPUT STYLE:
- The app automatically renders squad and team data as a formatted table — you do NOT need to list players
- Focus purely on insights, reasoning, and recommendations after a build_squad or get_team call
- Lead with the most important insight
- Always give a reason for every recommendation
- End with a one-line summary of the key action to take
- Use bullet points for clarity, keep it concise"""

def render_squad(data):
    """Render a build_squad result as a clean formatted table."""
    if "error" in data:
        return
    st.markdown(f"**Formation:** {data['formation']} | **GW{data['gw']}** | £{data['budget_used']}m used | £{data['budget_remaining']}m left")
    st.markdown(f"**Captain:** {data['captain']} | **Vice:** {data['vice_captain']}")
    if data.get("dgw_players"):
        st.success(f"⚡ DGW players in XI: {', '.join(data['dgw_players'])}")
    if data.get("bgw_warnings"):
        st.warning(f"⚠️ BGW warnings: {', '.join(data['bgw_warnings'])}")
    rows = []
    for p in data["players"]:
        badge = " 🟡" if p["role"] == "Captain" else " 🔵" if p["role"] == "Vice-Captain" else ""
        bench = p["role"] == "Bench"
        rows.append({
            "": "🔲" if bench else "✅",
            "Player":   p["name"] + badge,
            "Pos":      p["pos"],
            "Team":     p["team"],
            "£":        f"£{p['price']}m",
            "Fixture":  p["fixture"],
            "Role":     p["role"],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

def render_team(data):
    """Render a get_team result as a clean formatted table."""
    if "error" in data:
        return
    st.markdown(f"**{data['team_name']}** ({data['manager']}) | Rank: {data.get('overall_rank','?'):,} | Points: {data.get('total_points','?')}")
    st.markdown(f"**Captain:** {data['captain']} | **Vice:** {data['vice_captain']}")
    if data.get("dgw_players"):
        st.success(f"⚡ DGW players in XI: {', '.join(data['dgw_players'])}")
    if data.get("bgw_warnings"):
        st.warning(f"⚠️ BGW warnings in XI: {', '.join(data['bgw_warnings'])}")
    rows = []
    for p in data["squad"]:
        badge = " 🟡" if p["role"] == "Captain" else " 🔵" if p["role"] == "Vice-Captain" else ""
        bench = p["role"] == "Bench"
        rows.append({
            "": "🔲" if bench else "✅",
            "Player":    p["name"] + badge,
            "Pos":       p["pos"],
            "Team":      p["team"],
            "£":         f"£{p['price']}m",
            "PPG":       p["ppg"],
            "Form":      p["form"],
            "xGI/90":   p["xgi_per90"],
            "Fixture":   p["fixture"],
            "Role":      p["role"],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
def run_agent(history):
    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    for _ in range(8):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=SYSTEM,
            tools=TOOLS,
            messages=history,
        )
        history.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                fn     = TOOL_FNS.get(block.name)
                result = json.dumps(fn(**block.input)) if fn else json.dumps({"error": "unknown tool"})
                tool_results.append({"type":"tool_result","tool_use_id":block.id,"content":result})
        history.append({"role": "user", "content": tool_results})
    reply = "".join(b.text for b in response.content if hasattr(b, "text"))
    safe  = []
    for msg in history:
        if isinstance(msg["content"], list):
            safe.append({"role": msg["role"], "content": [
                b.model_dump() if hasattr(b, "model_dump") else b for b in msg["content"]
            ]})
        else:
            safe.append(msg)
    return reply, safe

# ── Landing page ──────────────────────────────────────────
if st.session_state.show_landing and not st.session_state.show_how_it_works:
    st.markdown("""
    <div class="hero">
        <div style="font-size: 2.5rem;">⚽</div>
        <h1>FPL Agent</h1>
        <p>AI-powered Fantasy Premier League advice, squad builder, and fixture analysis.<br>Live data updated every gameweek.</p>
        <div class="feature-row">
            <span class="feature-pill">Squad builder</span>
            <span class="feature-pill">Player comparisons</span>
            <span class="feature-pill">Fixture difficulty</span>
            <span class="feature-pill">xG &amp; xA stats</span>
            <span class="feature-pill">DGW &amp; BGW alerts</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
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
Most FPL tools show you a table of stats and leave you to figure it out. FPL Agent does the thinking for you — and here's exactly how.

---

#### The squad builder

When you ask FPL Agent to build you a squad, it doesn't just sort players by points and pick the top 15. It runs a full mathematical optimisation across every available player simultaneously, balancing seven different factors at once:

**1. Expected goals and assists (xG / xA)**
Rather than relying on goals and assists alone — which are subject to luck — the engine weights expected goals and expected assists per 90 minutes. A striker who hits the post three times is more valuable than his blank scoresheet suggests.

**2. Position-adjusted scoring**
A clean sheet is worth 4 points to a goalkeeper or defender, 1 point to a midfielder, and nothing to a forward. The engine knows this and scores each player relative to what they can actually earn.

**3. Form weighting**
Recent form is factored in alongside season-long stats. A player on a hot streak gets a boost. The weight shifts depending on whether you choose cautious, balanced, or aggressive mode.

**4. Fixture difficulty**
Every player's score is multiplied by a fixture difficulty factor based on their upcoming opponent.

**5. Double and blank gameweek awareness**
Players with a double gameweek automatically receive a 1.8x score multiplier. Players with a blank gameweek are heavily penalised so the engine naturally avoids them.

**6. Points per game**
Season-long consistency matters. A player who reliably scores 5–6 points every week is often more valuable than a boom-or-bust option.

**7. Budget optimisation**
Using linear programming — the same technique used in logistics, finance, and engineering — the engine finds the mathematically optimal 15-player squad within your budget.

---

#### Formation, captain & bench
The engine tests every legal FPL formation, picks the best starting XI, orders the bench automatically, and assigns the captaincy to your highest projected scorer.

---

> Every recommendation is grounded in the same statistical principles used by professional analysts. It won't win you every gameweek — no tool can. But over a full season, making smarter decisions more consistently is how you climb the overall rankings.
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

# ── Main chat UI ──────────────────────────────────────────
st.markdown(f"### ⚽ FPL Agent — GW{next_gw}")
st.markdown(f'<div style="text-align:right;font-size:12px;"><a href="{BMC_URL}" target="_blank"><strong>☕ Buy me a coffee if you find it useful</strong></a></div>', unsafe_allow_html=True)
st.divider()

with st.expander("💡 Did you know? Get personalised advice using your FPL Team ID"):
    st.markdown("""
    FPL Agent can analyse **your actual squad** and give personalised transfer advice, captain picks, and BGW/DGW warnings.

    **How to find your FPL Team ID:**
    1. Go to [fantasy.premierleague.com](https://fantasy.premierleague.com)
    2. Click **Points** or **Pick Team**
    3. Look at the URL — your ID is the number in the address bar:
    `fantasy.premierleague.com/entry/`**`1234567`**`/event/36`

    Then just tell FPL Agent: *"My FPL ID is 1234567, analyse my team"*
    """)

if not st.session_state.messages:
    prompts = [
        "Build me a balanced squad for £100m",
        "Best value midfielders under £7m?",
        "Any double gameweeks coming up?",
    ]
    cols = st.columns(2)
    for i, p in enumerate(prompts):
        if cols[i % 2].button(p, use_container_width=True):
            st.session_state.pending_prompt = p
            st.rerun()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if st.session_state.question_count > 0 and st.session_state.question_count % 3 == 0:
    st.markdown(f"""
    <div class="nudge-banner">
        <p>☕ Finding FPL Agent useful? <a href="{BMC_URL}" target="_blank">Buy me a coffee</a> to help keep it running — completely optional, always appreciated.</p>
    </div>
    """, unsafe_allow_html=True)

user_input = st.chat_input("Ask anything about FPL...") or st.session_state.pop("pending_prompt", None)

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state.history.append({"role": "user", "content": user_input})
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            reply, safe_history, squad_renders = run_agent(st.session_state.history)
        for tool_name, data in squad_renders:
            if tool_name == "build_squad":
                render_squad(data)
            elif tool_name == "get_team":
                render_team(data)
        if reply:
            st.markdown(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.history      = safe_history
    st.session_state.question_count += 1
    log_question(user_input)
    st.rerun()
