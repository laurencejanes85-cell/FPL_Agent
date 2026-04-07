"""
FPL Agent - Streamlit Web App
Run locally: streamlit run fpl_app.py
"""

import streamlit as st
import anthropic
import requests
import json

# ── Page config ───────────────────────────────────────────
st.set_page_config(
    page_title="FPL Agent",
    page_icon="⚽",
    layout="centered",
)

# ── Simple access control ─────────────────────────────────
try:
    raw = st.secrets["allowed_emails"]
    ALLOWED_EMAILS = [e.strip().lower() for e in raw.split(",") if e.strip()]
except Exception:
    ALLOWED_EMAILS = []

def check_access():
    if not ALLOWED_EMAILS:
        return True
    email = st.session_state.get("user_email", "").lower().strip()
    return email in ALLOWED_EMAILS

# ── Auth gate ─────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("⚽ FPL Agent")
    st.write("AI-powered Fantasy Premier League advice, live data, squad builder.")
    st.divider()
    st.text_input("Enter your email to access:", key="user_email")
    if st.button("Continue", type="primary"):
        if check_access() or not ALLOWED_EMAILS:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Sorry, that email doesn't have access yet. Purchase access at [your Gumroad link].")
    st.stop()

# ── Load FPL data (cached so it only runs once per session) ─
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

    next_gw_diff, next_gw_fix_str = {}, {}
    for fix in fixtures:
        if fix["event"] != next_gw:
            continue
        h, a = fix["team_h"], fix["team_a"]
        next_gw_diff.setdefault(h, []).append(fix["team_h_difficulty"])
        next_gw_diff.setdefault(a, []).append(fix["team_a_difficulty"])
        next_gw_fix_str.setdefault(h, []).append(f"{teams_by_id.get(a,'?')}(H)")
        next_gw_fix_str.setdefault(a, []).append(f"{teams_by_id.get(h,'?')}(A)")

    return bootstrap, fixtures, teams_by_id, players, next_gw, next_gw_diff, next_gw_fix_str

bootstrap, fixtures, teams_by_id, players, next_gw, next_gw_diff, next_gw_fix_str = load_fpl_data()

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
        mins   = max(p["minutes"], 1)
        gpts   = {"FWD":4,"MID":5,"DEF":6,"GK":6}[p["pos"]]
        attack = (p["xg_per90"]*0.5 + (p["goals"]/mins*90)*0.5)*gpts
        attack+= (p["xa_per90"]*0.5 + (p["assists"]/mins*90)*0.5)*3
        cs_pts = {"GK":4,"DEF":4,"MID":1,"FWD":0}[p["pos"]]
        defence= (p["clean_sheets"]/mins*90)*cs_pts
        bonus  = p["ppg"]*0.3
        form_w = 0.5 if style=="cautious" else 0.3
        t_id   = next((t["id"] for t in bootstrap["teams"] if t["name"]==p["team"]),0)
        diffs  = next_gw_diff.get(t_id, [3])
        fix_d  = sum(diffs)/max(1,len(diffs))
        fix_pow= 0.8 if style=="cautious" else 1.4
        atk_w  = 0.8 if style=="cautious" else 1.5
        fix_mult = ((6-fix_d)/5)**fix_pow
        raw    = attack*atk_w + defence + bonus
        return raw*(1+form_w*(p["form"]/10))*fix_mult

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
    return {"style":style,"formation":best_formation,"gw":next_gw,
            "budget_used":round(sum(p["price"] for p in selected),1),
            "budget_remaining":round(budget-sum(p["price"] for p in selected),1),
            "captain":captain,"vice_captain":vice,"players":players_out}

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
    "filter_players": filter_players, "top_stat_leaders": top_stat_leaders,
    "compare_players": compare_players, "fixture_difficulty": fixture_difficulty,
    "build_squad": build_squad,
}

SYSTEM = f"""You are an expert FPL assistant with live GW{next_gw} data.
Always call tools before answering. Be concise and give clear recommendations."""

# ── UI ────────────────────────────────────────────────────
st.title(f"⚽ FPL Agent — GW{next_gw}")
st.caption(f"Logged in as {st.session_state.get('user_email','')}")

# Suggested prompts
if "messages" not in st.session_state or not st.session_state.messages:
    st.write("**Try asking:**")
    cols = st.columns(2)
    prompts = [
        "Build me a balanced squad for £100m",
        "Who are the best value midfielders under £7m?",
        "Compare Salah and Saka",
        "Which teams have the easiest fixtures next 3 GWs?",
    ]
    for i, prompt in enumerate(prompts):
        if cols[i % 2].button(prompt, use_container_width=True):
            st.session_state.setdefault("messages", [])
            st.session_state.pending_prompt = prompt
            st.rerun()

# Init chat
if "messages" not in st.session_state:
    st.session_state.messages = []
if "history" not in st.session_state:
    st.session_state.history = []

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Handle input
user_input = st.chat_input("Ask anything about FPL...") or st.session_state.pop("pending_prompt", None)

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    st.session_state.history.append({"role": "user", "content": user_input})

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
            history = st.session_state.history

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
            st.markdown(reply)

    st.session_state.messages.append({"role": "assistant", "content": reply})

    # Serialise history for next turn
    safe = []
    for msg in history:
        if isinstance(msg["content"], list):
            safe.append({"role": msg["role"], "content": [
                b.model_dump() if hasattr(b, "model_dump") else b for b in msg["content"]
            ]})
        else:
            safe.append(msg)
    st.session_state.history = safe
