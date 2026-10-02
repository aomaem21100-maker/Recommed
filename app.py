import os
import re
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st
from neo4j import GraphDatabase

st.set_page_config(page_title="Book Recommendation", page_icon="📚", layout="wide")


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
@st.cache_data(ttl=86400, show_spinner=False)
def wiki_image(name):
    title = name.replace(" ", "_")
    try:
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
            headers={"User-Agent": "BookRecommenderProject/1.0"},
            timeout=6,
        )
        if r.ok:
            data = r.json()
            return (data.get("thumbnail") or {}).get("source")
    except requests.RequestException:
        pass
    return None


def book_image(name, url=None):
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
    return f"https://placehold.co/600x400/1a1a1a/3b82f6?text={str(name).replace(' ', '+')}"


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
    hops = int(hops)
    return f"""
MATCH (me:Person {{person_id: $pid}})-[:FRIEND_OF*1..{hops}]-(f:Person)-[:READ]->(book:Book)
WHERE f <> me AND NOT (me)-[:READ]->(book)
RETURN book.book_id AS book_id,
       book.title AS book,
       book.image AS image,
       count(DISTINCT f) AS score,
       collect(DISTINCT f.name) AS read_by
ORDER BY score DESC, book
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
    reads = run(
        """
        MATCH (p:Person)-[:READ]->(b:Book)
        WHERE p.person_id IN $ids
        RETURN p.person_id AS pid, p.name AS person, b.title AS book
        """, ids=ids)
    return friends, reads


def build_dot(me_name, friends, reads, my_pid, recommended):
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
    for r in reads:
        book = q(r["book"])
        mine = r["pid"] == my_pid
        hit = r["book"] in recommended and not mine
        fill = "#FFD966" if hit else ("#B7E1CD" if mine else "#F3F3F3")
        lines.append(f'"book:{book}" [label="{book}", shape=box, style="rounded,filled", fillcolor="{fill}"];')
        lines.append(f'"{q(r["person"])}" -- "book:{book}" [label="READ", fontsize=9, style=dashed];')
    lines.append("}")
    return "\n".join(lines)


def write(query, **params):
    get_driver().execute_query(query, parameters_=params, database_=DATABASE)
    st.cache_data.clear()


@st.cache_data(ttl=300)
def load_books():
    return run("MATCH (b:Book) RETURN b.book_id AS id, b.title AS name ORDER BY id")


@st.cache_data(ttl=300)
def popular_books():
    return pd.DataFrame(run(
        """
        MATCH (b:Book)
        OPTIONAL MATCH (p:Person)-[:READ]->(b)
        RETURN b.title AS book, count(p) AS readers
        ORDER BY readers DESC, book
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
def read_relationships():
    return run(
        """
        MATCH (p:Person)-[:READ]->(b:Book)
        RETURN p.person_id AS pid, p.name AS person, b.book_id AS bid, b.title AS book
        ORDER BY pid, bid
        """)


def next_id(prefix, rows):
    nums = [int(r["id"][1:]) for r in rows if r["id"][1:].isdigit()]
    return f"{prefix}{(max(nums) if nums else 0) + 1:03d}"


SAMPLE_PEOPLE = ["Ing", "Somying", "Natee", "Plana", "Wichai", "On", "Beam", "Non", "Games", "Palm"]
SAMPLE_BOOKS = ["Clean Code", "The Pragmatic Programmer", "Design Patterns", "Atomic Habits", "Sapiens",
                "Deep Work", "Refactoring", "Zero to One", "Thinking, Fast and Slow", "Dune"]
SAMPLE_FRIENDS = [(1, 2), (1, 3), (1, 4), (2, 5), (2, 6), (3, 7),
                  (3, 8), (4, 9), (5, 10), (6, 7), (8, 9), (9, 10)]


def seed_sample_data():
    people = [{"person_id": f"P{i+1:03d}", "name": n} for i, n in enumerate(SAMPLE_PEOPLE)]
    books = [{"book_id": f"B{i+1:03d}", "title": n} for i, n in enumerate(SAMPLE_BOOKS)]
    reads = [{"p": f"P{i:03d}", "b": f"B{i:03d}"} for i in range(1, 11)]
    fr = [{"a": f"P{a:03d}", "b": f"P{b:03d}"} for a, b in SAMPLE_FRIENDS]
    write("CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.person_id IS UNIQUE")
    write("CREATE CONSTRAINT book_id_unique IF NOT EXISTS FOR (b:Book) REQUIRE b.book_id IS UNIQUE")
    write("UNWIND $r AS row MERGE (p:Person {person_id: row.person_id}) SET p.name = row.name", r=people)
    write("UNWIND $r AS row MERGE (b:Book {book_id: row.book_id}) SET b.title = row.title", r=books)
    write("""UNWIND $r AS row MATCH (p:Person {person_id: row.p}) MATCH (b:Book {book_id: row.b})
             MERGE (p)-[:READ]->(b)""", r=reads)
    write("""UNWIND $r AS row MATCH (a:Person {person_id: row.a}) MATCH (b:Person {person_id: row.b})
             WHERE NOT (a)-[:FRIEND_OF]-(b) MERGE (a)-[:FRIEND_OF]->(b)""", r=fr)


# ---------- UI ----------
st.title("📚 Book Recommendation System")
st.caption("Graph Database · Neo4j Aura · Cypher — แนะนำหนังสือจากเครือข่ายเพื่อน")

try:
    people = load_people()
    stats = load_stats()
except Exception as e:
    st.error("เชื่อมต่อ Neo4j ไม่สำเร็จ ตรวจสอบ .streamlit/secrets.toml")
    st.exception(e)
    st.stop()

if not people:
    st.warning("ยังไม่มีข้อมูล Person ในฐานข้อมูล")
    if st.button("โหลดข้อมูลตัวอย่าง (10 คน / 10 เล่ม)"):
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
    limit = st.slider("จำนวนหนังสือที่แนะนำ", 1, 10, 5)
    st.divider()
    c1, c2 = st.columns(2)
    c1.metric("Nodes", stats["nodes"])
    c2.metric("Relationships", stats["rels"])

st.subheader(f"ผลการแนะนำสำหรับ {names[pid]}")

tab_rec, tab_graph, tab_stats, tab_manage, tab_cypher = st.tabs(
    ["📚 หนังสือที่แนะนำ", "🌐 กราฟเครือข่าย", "📊 สถิติ", "⚙️ จัดการข้อมูล", "💻 Cypher"]
)

df = recommend(pid, hops, limit)

with tab_rec:
    if df.empty:
        st.info("ไม่พบหนังสือที่แนะนำ — เพื่อนอาจยังไม่ได้อ่าน หรือผู้ใช้อ่านหนังสือนั้นแล้ว ลองเพิ่มระยะเครือข่าย")
    else:
        for start in range(0, len(df), 3):
            cols = st.columns(3)
            for col, (_, row) in zip(cols, df.iloc[start:start + 3].iterrows()):
                with col:
                    st.image(book_image(row["book"], row.get("image")), use_container_width=True)
                    st.markdown(f"**{row['book']}**")
                    st.caption(f"คะแนน {row['score']} · เพื่อนที่อ่าน: {', '.join(row['read_by'])}")

        with st.expander("ดูตาราง / กราฟคะแนน"):
            show = (
                df.drop(columns=["image"], errors="ignore")
                .assign(read_by=df["read_by"].apply(", ".join))
                .rename(columns={"book_id": "รหัส", "book": "หนังสือ", "score": "คะแนน", "read_by": "เพื่อนที่อ่าน"})
            )
            left, right = st.columns([3, 2])
            left.dataframe(show, hide_index=True, use_container_width=True)
            right.bar_chart(df.set_index("book")["score"])

with tab_graph:
    friends, reads = network(pid, hops)
    rec_names = set(df["book"]) if not df.empty else set()
    st.graphviz_chart(build_dot(names[pid], friends, reads, pid, rec_names), use_container_width=True)
    st.caption("ฟ้า = ผู้ใช้ · เหลือง = หนังสือที่แนะนำ · เขียว = หนังสือที่ผู้ใช้อ่านแล้ว")

with tab_stats:
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**หนังสือยอดนิยม (จำนวนผู้อ่าน)**")
        pb = popular_books()
        if not pb.empty:
            st.bar_chart(pb.set_index("book")["readers"])
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
    books = load_books()
    book_names = {b["id"]: b["name"] for b in books}

    with st.expander("โหลดข้อมูลตัวอย่าง (10 คน / 10 เล่ม / 22 relationships)"):
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
        st.markdown("**เพิ่ม Book**")
        with st.form("add_book", clear_on_submit=True):
            bname = st.text_input("ชื่อหนังสือ")
            bimg = st.text_input("ลิงก์รูป (ไม่บังคับ)")
            if st.form_submit_button("เพิ่ม") and bname.strip():
                write("MERGE (b:Book {book_id: $id}) SET b.title = $t, b.image = $img",
                      id=next_id("B", books), t=bname.strip(), img=bimg.strip() or None)
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
        st.markdown("**เพิ่มการอ่านหนังสือ (READ)**")
        if books:
            with st.form("add_read"):
                op = st.selectbox("ผู้อ่าน", list(names), format_func=lambda i: names[i], key="op")
                ob = st.selectbox("หนังสือ", list(book_names), format_func=lambda i: book_names[i], key="ob")
                if st.form_submit_button("เพิ่มการอ่าน"):
                    write("""MATCH (p:Person {person_id: $p}), (b:Book {book_id: $b})
                             MERGE (p)-[:READ]->(b)""", p=op, b=ob)
                    st.rerun()
        else:
            st.info("ยังไม่มีหนังสือในระบบ")

    with st.expander("ลบความสัมพันธ์"):
        pairs = friend_pairs()
        if pairs:
            pick = st.selectbox("เลือกมิตรภาพที่จะลบ", range(len(pairs)),
                                format_func=lambda i: f"{pairs[i]['an']} — {pairs[i]['bn']}")
            if st.button("ลบ FRIEND_OF"):
                write("MATCH (:Person {person_id: $a})-[r:FRIEND_OF]->(:Person {person_id: $b}) DELETE r",
                      a=pairs[pick]["a"], b=pairs[pick]["b"])
                st.rerun()
        reads_rel = read_relationships()
        if reads_rel:
            pick2 = st.selectbox("เลือกรายการการอ่านที่จะลบ", range(len(reads_rel)),
                                 format_func=lambda i: f"{reads_rel[i]['person']} — {reads_rel[i]['book']}")
            if st.button("ลบ READ"):
                write("MATCH (:Person {person_id: $p})-[r:READ]->(:Book {book_id: $b}) DELETE r",
                      p=reads_rel[pick2]["pid"], b=reads_rel[pick2]["bid"])
                st.rerun()


with tab_cypher:
    st.code(recommend_query(hops), language="cypher")
    st.caption(f"พารามิเตอร์: pid = '{pid}', limit = {limit}")
