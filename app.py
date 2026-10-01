import os
import re
from urllib.parse import quote
 
import pandas as pd
import requests
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
 
 
# ---------- Image helper ----------
# ชื่อรถ -> ชื่อบทความ Wikipedia (ถ้าไม่ระบุ จะใช้ชื่อรถตรง ๆ)
WIKI_TITLES = {
    "Mazda 3": "Mazda3",
    "Ford Ranger": "Ford Ranger (T6)",
    "Nissan Almera": "Nissan Almera",
}
 
 
@st.cache_data(ttl=86400, show_spinner=False)
def wiki_image(name):
    """ดึงรูปหลักของบทความ Wikipedia (ไม่ต้องใช้ API key) คืน None ถ้าไม่เจอ"""
    title = WIKI_TITLES.get(name, name).replace(" ", "_")
    try:
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
            headers={"User-Agent": "CarRecommenderStudentProject/1.0"},
            timeout=6,
        )
        if r.ok:
            data = r.json()
            return (data.get("thumbnail") or {}).get("source")
    except requests.RequestException:
        pass
    return None
 
 
def car_image(name, url=None):
    """ลำดับ: images/<ชื่อรถ>.jpg -> car.image ใน Neo4j -> Wikipedia -> placeholder"""
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    for ext in ("jpg", "jpeg", "png", "webp"):
        path = os.path.join("images", f"{slug}.{ext}")
        if os.path.exists(path):
            return path
    if isinstance(url, str) and url.strip():
        return url.strip()
    wiki = wiki_image(str(name))
    if wiki:
        return wiki
    return f"https://placehold.co/600x400/1a1a1a/ef4444?text={str(name).replace(' ', '+')}"
 
 
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
       car.image AS image,
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
 
 
def write(query, **params):
    get_driver().execute_query(query, parameters_=params, database_=DATABASE)
    st.cache_data.clear()
 
 
@st.cache_data(ttl=300)
def load_cars():
    return run("MATCH (c:Car) RETURN c.car_id AS id, c.name AS name ORDER BY id")
 
 
@st.cache_data(ttl=300)
def popular_cars():
    return pd.DataFrame(run(
        """
        MATCH (c:Car)
        OPTIONAL MATCH (p:Person)-[:OWNS]->(c)
        RETURN c.name AS car, count(p) AS owners
        ORDER BY owners DESC, car
        """))
 
 
@st.cache_data(ttl=300)
def connected_people():
    return pd.DataFrame(run(
        """
        MATCH (p:Person)
        OPTIONAL MATCH (p)-[:FRIEND_OF]-(f:Person)
        RETURN p.name AS person, count(DISTINCT f) AS friends
        ORDER BY friends DESC, person
        """))
 
 
@st.cache_data(ttl=300)
def mutual_friends(a: str, b: str):
    return [r["name"] for r in run(
        """
        MATCH (a:Person {person_id: $a})-[:FRIEND_OF]-(m:Person)-[:FRIEND_OF]-(b:Person {person_id: $b})
        RETURN DISTINCT m.name AS name ORDER BY name
        """, a=a, b=b)]
 
 
@st.cache_data(ttl=300)
def friend_pairs():
    return run(
        """
        MATCH (a:Person)-[:FRIEND_OF]->(b:Person)
        RETURN a.person_id AS a, a.name AS an, b.person_id AS b, b.name AS bn
        ORDER BY a, b
        """)
 
 
@st.cache_data(ttl=300)
def ownerships():
    return run(
        """
        MATCH (p:Person)-[:OWNS]->(c:Car)
        RETURN p.person_id AS pid, p.name AS person, c.car_id AS cid, c.name AS car
        ORDER BY pid, cid
        """)
 
 
def next_id(prefix, rows):
    nums = [int(r["id"][1:]) for r in rows if r["id"][1:].isdigit()]
    return f"{prefix}{(max(nums) if nums else 0) + 1:03d}"
 
 
SAMPLE_PEOPLE = ["Ing", "Somying", "Natee", "Plana", "Wichai", "On", "Beam", "Non", "Games", "Palm"]
SAMPLE_CARS = ["Toyota Yaris", "Honda Civic", "Mazda 3", "Toyota Corolla", "Honda HR-V",
               "BYD Atto 3", "Tesla Model 3", "Nissan Almera", "Ford Ranger", "Isuzu D-Max"]
SAMPLE_FRIENDS = [(1, 2), (1, 3), (1, 4), (2, 5), (2, 6), (3, 7),
                  (3, 8), (4, 9), (5, 10), (6, 7), (8, 9), (9, 10)]
 
 
def seed_sample_data():
    people = [{"person_id": f"P{i+1:03d}", "name": n} for i, n in enumerate(SAMPLE_PEOPLE)]
    cars = [{"car_id": f"C{i+1:03d}", "name": n, "model": n} for i, n in enumerate(SAMPLE_CARS)]
    owns = [{"p": f"P{i:03d}", "c": f"C{i:03d}"} for i in range(1, 11)]
    fr = [{"a": f"P{a:03d}", "b": f"P{b:03d}"} for a, b in SAMPLE_FRIENDS]
    write("CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.person_id IS UNIQUE")
    write("CREATE CONSTRAINT car_id_unique IF NOT EXISTS FOR (c:Car) REQUIRE c.car_id IS UNIQUE")
    write("UNWIND $r AS row MERGE (p:Person {person_id: row.person_id}) SET p.name = row.name", r=people)
    write("UNWIND $r AS row MERGE (c:Car {car_id: row.car_id}) SET c.name = row.name, c.model = row.model", r=cars)
    write("""UNWIND $r AS row MATCH (p:Person {person_id: row.p}) MATCH (c:Car {car_id: row.c})
             MERGE (p)-[:OWNS]->(c)""", r=owns)
    write("""UNWIND $r AS row MATCH (a:Person {person_id: row.a}) MATCH (b:Person {person_id: row.b})
             WHERE NOT (a)-[:FRIEND_OF]-(b) MERGE (a)-[:FRIEND_OF]->(b)""", r=fr)
 
 
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
    if st.button("โหลดข้อมูลตัวอย่าง (10 คน / 10 รถ)"):
        seed_sample_data()
        st.rerun()
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
tab_rec, tab_graph, tab_stats, tab_manage, tab_cypher = st.tabs(
    ["🚘 รถที่แนะนำ", "🕸️ กราฟเครือข่าย", "📊 สถิติ", "🛠️ จัดการข้อมูล", "🧾 Cypher"]
)
 
df = recommend(pid, hops, limit)
 
with tab_rec:
    if df.empty:
        st.info("ไม่พบรถที่แนะนำ — เพื่อนอาจไม่มีรถ หรือผู้ใช้มีรถเหล่านั้นแล้ว ลองเพิ่มระยะเครือข่าย")
    else:
        for start in range(0, len(df), 3):
            cols = st.columns(3)
            for col, (_, row) in zip(cols, df.iloc[start:start + 3].iterrows()):
                with col:
                    st.image(car_image(row["car"], row.get("image")), use_container_width=True)
                    st.markdown(f"**{row['car']}**")
                    st.caption(f"คะแนน {row['score']} · เพื่อนที่ใช้: {', '.join(row['owned_by'])}")
 
        with st.expander("ดูตาราง / กราฟคะแนน"):
            show = (
                df.drop(columns=["image"], errors="ignore")
                .assign(owned_by=df["owned_by"].apply(", ".join))
                .rename(columns={"car_id": "รหัส", "car": "รถ", "score": "คะแนน", "owned_by": "เพื่อนที่ใช้"})
            )
            left, right = st.columns([3, 2])
            left.dataframe(show, hide_index=True, use_container_width=True)
            right.bar_chart(df.set_index("car")["score"])
 
with tab_graph:
    friends, owns = network(pid, hops)
    rec_names = set(df["car"]) if not df.empty else set()
    st.graphviz_chart(build_dot(names[pid], friends, owns, pid, rec_names), use_container_width=True)
    st.caption("ฟ้า = ผู้ใช้ · เหลือง = รถที่แนะนำ · เขียว = รถของผู้ใช้เอง")
 
with tab_stats:
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**รถยอดนิยม (จำนวนเจ้าของ)**")
        pc = popular_cars()
        if not pc.empty:
            st.bar_chart(pc.set_index("car")["owners"])
    with s2:
        st.markdown("**คนที่มีเพื่อนมากที่สุด**")
        cp = connected_people()
        if not cp.empty:
            st.bar_chart(cp.set_index("person")["friends"])
    st.divider()
    st.markdown("**เพื่อนร่วมกันของ 2 คน**")
    m1, m2 = st.columns(2)
    a = m1.selectbox("คนที่ 1", list(names), format_func=lambda i: names[i], key="mf_a")
    b = m2.selectbox("คนที่ 2", list(names), index=min(1, len(names) - 1),
                     format_func=lambda i: names[i], key="mf_b")
    if a == b:
        st.info("เลือกคนละคนกัน")
    else:
        mf = mutual_friends(a, b)
        st.write(f"เพื่อนร่วม {len(mf)} คน: " + (", ".join(mf) if mf else "ไม่มี"))
 
with tab_manage:
    cars = load_cars()
    car_names = {c["id"]: c["name"] for c in cars}
 
    with st.expander("โหลดข้อมูลตัวอย่าง (10 คน / 10 รถ / 22 relationships)"):
        st.caption("ใช้ MERGE จึงรันซ้ำได้โดยไม่เกิดข้อมูลซ้ำ")
        if st.button("โหลดข้อมูลตัวอย่าง"):
            seed_sample_data()
            st.success("โหลดข้อมูลตัวอย่างแล้ว")
            st.rerun()
 
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**เพิ่ม Person**")
        with st.form("add_person", clear_on_submit=True):
            pname = st.text_input("ชื่อ")
            if st.form_submit_button("เพิ่ม") and pname.strip():
                write("MERGE (p:Person {person_id: $id}) SET p.name = $name",
                      id=next_id("P", people), name=pname.strip())
                st.rerun()
    with g2:
        st.markdown("**เพิ่ม Car**")
        with st.form("add_car", clear_on_submit=True):
            cname = st.text_input("รุ่นรถ")
            cimg = st.text_input("ลิงก์รูป (ไม่บังคับ)")
            if st.form_submit_button("เพิ่ม") and cname.strip():
                write("MERGE (c:Car {car_id: $id}) SET c.name = $n, c.model = $n, c.image = $img",
                      id=next_id("C", cars), n=cname.strip(), img=cimg.strip() or None)
                st.rerun()
 
    h1, h2 = st.columns(2)
    with h1:
        st.markdown("**เพิ่มเพื่อน (FRIEND_OF)**")
        with st.form("add_friend"):
            fa = st.selectbox("คนที่ 1", list(names), format_func=lambda i: names[i], key="fa")
            fb = st.selectbox("คนที่ 2", list(names), format_func=lambda i: names[i], key="fb")
            if st.form_submit_button("เพิ่มความเป็นเพื่อน"):
                if fa == fb:
                    st.warning("เลือกคนละคนกัน")
                else:
                    write("""MATCH (a:Person {person_id: $a}), (b:Person {person_id: $b})
                             WHERE NOT (a)-[:FRIEND_OF]-(b) CREATE (a)-[:FRIEND_OF]->(b)""", a=fa, b=fb)
                    st.rerun()
    with h2:
        st.markdown("**เพิ่มเจ้าของรถ (OWNS)**")
        if cars:
            with st.form("add_owns"):
                op = st.selectbox("เจ้าของ", list(names), format_func=lambda i: names[i], key="op")
                oc = st.selectbox("รถ", list(car_names), format_func=lambda i: car_names[i], key="oc")
                if st.form_submit_button("เพิ่มความเป็นเจ้าของ"):
                    write("""MATCH (p:Person {person_id: $p}), (c:Car {car_id: $c})
                             MERGE (p)-[:OWNS]->(c)""", p=op, c=oc)
                    st.rerun()
        else:
            st.info("ยังไม่มีรถในระบบ")
 
    with st.expander("ลบความสัมพันธ์"):
        pairs = friend_pairs()
        if pairs:
            pick = st.selectbox("เลือกมิตรภาพที่จะลบ", range(len(pairs)),
                                format_func=lambda i: f"{pairs[i]['an']} — {pairs[i]['bn']}")
            if st.button("ลบ FRIEND_OF"):
                write("MATCH (:Person {person_id: $a})-[r:FRIEND_OF]->(:Person {person_id: $b}) DELETE r",
                      a=pairs[pick]["a"], b=pairs[pick]["b"])
                st.rerun()
        own = ownerships()
        if own:
            pick2 = st.selectbox("เลือกความเป็นเจ้าของที่จะลบ", range(len(own)),
                                 format_func=lambda i: f"{own[i]['person']} — {own[i]['car']}")
            if st.button("ลบ OWNS"):
                write("MATCH (:Person {person_id: $p})-[r:OWNS]->(:Car {car_id: $c}) DELETE r",
                      p=own[pick2]["pid"], c=own[pick2]["cid"])
                st.rerun()
 
 
with tab_cypher:
    st.code(recommend_query(hops), language="cypher")
    st.caption(f"พารามิเตอร์: pid = '{pid}', limit = {limit}")
 
