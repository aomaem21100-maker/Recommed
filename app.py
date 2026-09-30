import pandas as pd
import streamlit as st
from neo4j import GraphDatabase

st.set_page_config(page_title="Car Recommendation", page_icon="🚗", layout="wide")


# ---------- Neo4j connection ----------
@st.cache_resource
def get_driver():
    cfg = st.secrets["neo4j"]
    driver = GraphDatabase.driver(cfg["uri"], auth=(cfg["username"], cfg["password"]))
    driver.verify_connectivity()
    return driver


DATABASE = st.secrets["neo4j"].get("database", "neo4j")


def run(query, **params):
    records, _, _ = get_driver().execute_query(
        query, parameters_=params, database_=DATABASE
    )
    return [r.data() for r in records]


# ---------- Data functions ----------
@st.cache_data(ttl=300)
def load_people():
    return run("MATCH (p:Person) RETURN p.person_id AS id, p.name AS name ORDER BY id")


@st.cache_data(ttl=300)
def load_stats():
    rows = run(
        """
        MATCH (n)
        OPTIONAL MATCH (n)-[r]->()
        RETURN count(DISTINCT n) AS nodes, count(r) AS rels
        """
    )
    return rows[0]


def recommend_query(hops: int) -> str:
    hops = int(hops)  # controlled value (1 or 2), safe to format
    return f"""
MATCH (me:Person {{person_id: $pid}})-[:FRIEND_OF*1..{hops}]-(f:Person)-[:OWNS]->(car:Car)
WHERE f <> me AND NOT (me)-[:OWNS]->(car)
RETURN car.car_id AS car_id,
       car.name AS car,
       count(DISTINCT f) AS score,
       collect(DISTINCT f.name) AS owned_by
ORDER BY score DESC, car
LIMIT $limit
"""


@st.cache_data(ttl=300)
def recommend(pid: str, hops: int, limit: int) -> pd.DataFrame:
    return pd.DataFrame(run(recommend_query(hops), pid=pid, limit=limit))


@st.cache_data(ttl=300)
def network(pid: str, hops: int):
    hops = int(hops)
    ids = [r["id"] for r in run(
        f"""
        MATCH (me:Person {{person_id: $pid}})-[:FRIEND_OF*1..{hops}]-(x:Person)
        RETURN DISTINCT x.person_id AS id
        """, pid=pid)]
    ids = list(set(ids + [pid]))
    friends = run(
        """
        MATCH (a:Person)-[:FRIEND_OF]-(b:Person)
        WHERE a.person_id IN $ids AND b.person_id IN $ids AND a.person_id < b.person_id
        RETURN a.name AS a, b.name AS b
        """, ids=ids)
    owns = run(
        """
        MATCH (p:Person)-[:OWNS]->(c:Car)
        WHERE p.person_id IN $ids
        RETURN p.person_id AS pid, p.name AS person, c.name AS car
        """, ids=ids)
    return friends, owns


def build_dot(me_name, friends, owns, my_pid, recommended):
    q = lambda s: str(s).replace('"', "'")
    lines = ["graph G {", "rankdir=LR;", 'node [fontname="Helvetica"];']
    people = {me_name}
    for e in friends:
        people.update([e["a"], e["b"]])
    for p in people:
        color = "#4F8BF9" if p == me_name else "#DDE6F5"
        font = "white" if p == me_name else "black"
        lines.append(f'"{q(p)}" [shape=ellipse, style=filled, fillcolor="{color}", fontcolor="{font}"];')
    for e in friends:
        lines.append(f'"{q(e["a"])}" -- "{q(e["b"])}" [label="FRIEND_OF", fontsize=9];')
    for o in owns:
        car = q(o["car"])
        mine = o["pid"] == my_pid
        hit = o["car"] in recommended and not mine
        fill = "#FFD966" if hit else ("#B7E1CD" if mine else "#F3F3F3")
        lines.append(f'"car:{car}" [label="{car}", shape=box, style="rounded,filled", fillcolor="{fill}"];')
        lines.append(f'"{q(o["person"])}" -- "car:{car}" [label="OWNS", fontsize=9, style=dashed];')
    lines.append("}")
    return "\n".join(lines)


# ---------- UI ----------
st.title("🚗 Car Recommendation System")
st.caption("Graph Database · Neo4j Aura · Cypher — แนะนำรถจากเครือข่ายเพื่อน")

try:
    people = load_people()
    stats = load_stats()
except Exception as e:
    st.error("เชื่อมต่อ Neo4j ไม่สำเร็จ ตรวจสอบ .streamlit/secrets.toml")
    st.exception(e)
    st.stop()

if not people:
    st.warning("ยังไม่มีข้อมูล Person ในฐานข้อมูล")
    st.stop()

with st.sidebar:
    st.header("ตั้งค่า")
    names = {p["id"]: p["name"] for p in people}
    pid = st.selectbox("เลือกผู้ใช้", list(names), format_func=lambda i: f"{i} — {names[i]}")
    hops = st.radio(
        "ระยะเครือข่าย", [1, 2],
        format_func=lambda h: "เพื่อนโดยตรง" if h == 1 else "เพื่อน + เพื่อนของเพื่อน",
    )
    limit = st.slider("จำนวนรถที่แนะนำ", 1, 10, 5)
    st.divider()
    c1, c2 = st.columns(2)
    c1.metric("Nodes", stats["nodes"])
    c2.metric("Relationships", stats["rels"])

st.subheader(f"ผลการแนะนำสำหรับ {names[pid]}")
tab_rec, tab_graph, tab_cypher = st.tabs(["🚘 รถที่แนะนำ", "🕸️ กราฟเครือข่าย", "🧾 Cypher"])

df = recommend(pid, hops, limit)

with tab_rec:
    if df.empty:
        st.info("ไม่พบรถที่แนะนำ — เพื่อนอาจไม่มีรถ หรือผู้ใช้มีรถเหล่านั้นแล้ว ลองเพิ่มระยะเครือข่าย")
    else:
        show = df.assign(owned_by=df["owned_by"].apply(", ".join)).rename(
            columns={"car_id": "รหัส", "car": "รถ", "score": "คะแนน", "owned_by": "เพื่อนที่ใช้"}
        )
        left, right = st.columns([3, 2])
        left.dataframe(show, hide_index=True, use_container_width=True)
        right.bar_chart(df.set_index("car")["score"])

with tab_graph:
    friends, owns = network(pid, hops)
    rec_names = set(df["car"]) if not df.empty else set()
    st.graphviz_chart(build_dot(names[pid], friends, owns, pid, rec_names), use_container_width=True)
    st.caption("ฟ้า = ผู้ใช้ · เหลือง = รถที่แนะนำ · เขียว = รถของผู้ใช้เอง")

with tab_cypher:
    st.code(recommend_query(hops), language="cypher")
    st.caption(f"พารามิเตอร์: pid = '{pid}', limit = {limit}")
