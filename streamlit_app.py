"""
FPL Agent - Streamlit Web App
Run locally: streamlit run fpl_app.py
"""

import streamlit as st
import anthropic
import requests
import json

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
    .auth-card {
        background: white; border: 1px solid #e5e7eb; border-radius: 16px;
        padding: 2rem; max-width: 420px; margin: 0 auto 2rem;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    .auth-card h3 { margin: 0 0 0.25rem; font-size: 17px; }
    .auth-card p  { margin: 0 0 1.25rem; font-size: 13px; color: #6b7280; }
    .divider { text-align: center; color: #9ca3af; font-size: 12px; margin: 1rem 0; }
    .free-note { text-align: center; font-size: 12px; color: #9ca3af; margin-top: 1rem; }
</style>
""", unsafe_allow_html=True)

# ── Secrets / allowed emails ──────────────────────────────
try:
    raw = st.secrets["allowed_emails"]
    ALLOWED_EMAILS = [e.strip().lower() for e in raw.split(",") if e.strip()]
except Exception:
    ALLOWED_EMAILS = []

# ── Session state defaults ────────────────────────────────
for key, default in {
    "authenticated": False,
    "user_email": "",
    "show_auth": False,
    "auth_mode": "signup",
    "show_how_it_works": False,
    "messages": [],
    "history": [],
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

def gameweek_overview(gameweeks_ahead=5):
    results = []
    for gw in range(next_gw, min(next_gw + gameweeks_ahead, 39)):
        status = gw_status.get(gw, {})
        results.append({
            "gw": gw,
            "dgw_teams": status.get("dgw", []),
            "bgw_teams": status.get("bgw", []),
            "has_dgw": bool(status.get("dgw")),
            "has_bgw": bool(status.get("bgw")),
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
    "gameweek_overview":  gameweek_overview,
    "build_squad":        build_squad,
}

SYSTEM = f"""You are an expert FPL assistant with live GW{next_gw} data.
Always call tools before answering. Be concise and give clear recommendations.

DOUBLE/BLANK GAMEWEEK AWARENESS:
- Use the gameweek_overview tool whenever a user asks about upcoming DGWs, BGWs, or fixture schedules across multiple weeks.
- GW{next_gw} DGW teams: {', '.join(teams_by_id[tid] for tid in dgw_teams) if dgw_teams else 'None'}
- GW{next_gw} BGW teams: {', '.join(teams_by_id[tid] for tid in bgw_teams) if bgw_teams else 'None'}
- DGW players get a 1.8x score boost in squad builds. BGW players are heavily penalised.
- Always flag BGW warnings if any selected XI player has a blank. Always highlight DGW players as targets."""

# ── Helper: run agent ─────────────────────────────────────
def run_agent(history):
    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    for _ in range(8):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
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
    safe = []
    for msg in history:
        if isinstance(msg["content"], list):
            safe.append({"role": msg["role"], "content": [
                b.model_dump() if hasattr(b, "model_dump") else b for b in msg["content"]
            ]})
        else:
            safe.append(msg)
    return reply, safe

# ── Auth modal ────────────────────────────────────────────
def show_auth_modal(mode="signup"):
    st.markdown(f"""
    <div class="auth-card">
        <h3>{"Start your free week" if mode == "signup" else "Welcome back"}</h3>
        <p>{"Get full access for 7 days free, then £4.99/month. Cancel any time." if mode == "signup" else "Sign in to continue your FPL season."}</p>
    </div>
    """, unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        email = st.text_input("Email address", key="auth_email", placeholder="you@email.com")
        if st.button("Continue →", use_container_width=True, type="primary"):
            e = email.strip().lower()
            if not e or "@" not in e:
                st.error("Please enter a valid email address.")
            elif ALLOWED_EMAILS and e not in ALLOWED_EMAILS:
                st.warning("That email doesn't have access yet. [Get access →](https://fplagent.gumroad.com/l/FPL_Agent)")
            else:
                st.session_state.user_email    = e
                st.session_state.authenticated = True
                st.session_state.show_auth     = False
                st.rerun()
        st.markdown('<div class="free-note">By continuing you agree to our terms of service.</div>', unsafe_allow_html=True)
        if mode == "signup":
            st.markdown('<div class="divider">Already have an account?</div>', unsafe_allow_html=True)
            if st.button("Sign in instead", use_container_width=True):
                st.session_state.auth_mode = "signin"
                st.rerun()
        else:
            st.markdown('<div class="divider">Don\'t have an account?</div>', unsafe_allow_html=True)
            if st.button("Start free trial", use_container_width=True):
                st.session_state.auth_mode = "signup"
                st.rerun()

# ── Landing page ──────────────────────────────────────────
if (not st.session_state.authenticated
        and not st.session_state.show_auth
        and not st.session_state.show_how_it_works):
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
        if st.button("Start free trial →", use_container_width=True, type="primary"):
            st.session_state.show_how_it_works = True
            st.rerun()
        st.markdown('<div class="free-note">7 days free · then £4.99/month · cancel any time</div>', unsafe_allow_html=True)
        st.markdown("")
        if st.button("Sign in", use_container_width=True):
            st.session_state.show_auth = True
            st.session_state.auth_mode = "signin"
            st.rerun()
    st.stop()

# ── How it works screen ───────────────────────────────────
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
Recent form is factored in alongside season-long stats. A player on a hot streak gets a boost. The weight of form vs. underlying stats shifts depending on whether you choose cautious, balanced, or aggressive mode.

**4. Fixture difficulty**
Every player's score is multiplied by a fixture difficulty factor based on their upcoming opponent. A £6m defender with a home game against the bottom club scores very differently to the same player facing the title challengers away.

**5. Double and blank gameweek awareness**
Players with a double gameweek automatically receive a 1.8x score multiplier. Players with a blank gameweek are heavily penalised so the engine naturally avoids them.

**6. Points per game**
Season-long consistency matters. A player who reliably scores 5–6 points every week is often more valuable than a boom-or-bust option.

**7. Budget optimisation**
Using linear programming — the same technique used in logistics, finance, and engineering — the engine finds the mathematically optimal 15-player squad within your budget. Not just a good squad. The best possible squad.

---

#### Formation selection
Once the 15-player squad is chosen, the engine tests every legal FPL formation and selects the starting XI that maximises your combined score. Your bench is ordered automatically so your best cover comes on first.

#### Captain and vice-captain
The engine assigns the captaincy to your highest projected scorer — the player with the best combination of form, fixtures, and expected output. No guesswork.

---

> Every recommendation FPL Agent makes is grounded in the same statistical principles used by professional analysts. It won't win you every gameweek — no tool can. But over a full season, making smarter decisions more consistently is how you climb the overall rankings.
    """)
    st.divider()
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        if st.button("Got it — start my free trial →", use_container_width=True, type="primary"):
            st.session_state.show_how_it_works = False
            st.session_state.show_auth = True
            st.session_state.auth_mode = "signup"
            st.rerun()
        if st.button("← Back", use_container_width=True):
            st.session_state.show_how_it_works = False
            st.rerun()
    st.stop()

# ── Auth screen ───────────────────────────────────────────
if st.session_state.show_auth:
    st.markdown("<br>", unsafe_allow_html=True)
    show_auth_modal(st.session_state.auth_mode)
    if st.button("← Back", type="secondary"):
        st.session_state.show_auth = False
        st.rerun()
    st.stop()

# ── Main chat UI ──────────────────────────────────────────
col1, col2 = st.columns([3,1])
with col1:
    st.markdown(f"### ⚽ FPL Agent — GW{next_gw}")
with col2:
    st.markdown(f"<div style='text-align:right;font-size:12px;color:#6b7280;padding-top:1rem'>{st.session_state.user_email}</div>", unsafe_allow_html=True)

st.divider()

if not st.session_state.messages:
    prompts = [
        "Build me a balanced squad for £100m",
        "Best value midfielders under £7m?",
        "Compare Salah and Saka",
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

user_input = st.chat_input("Ask anything about FPL...") or st.session_state.pop("pending_prompt", None)

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state.history.append({"role": "user", "content": user_input})
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            reply, safe_history = run_agent(st.session_state.history)
            st.markdown(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.session_state.history = safe_history
    st.rerun()
