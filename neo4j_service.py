from __future__ import annotations

from typing import Any

import streamlit as st
from neo4j import GraphDatabase, RoutingControl


def _config() -> tuple[str, str, str, str]:
    cfg = st.secrets["neo4j"]
    return (
        cfg["uri"],
        cfg["username"],
        cfg["password"],
        cfg.get("database", "neo4j"),
    )


@st.cache_resource(show_spinner=False)
def get_driver():
    """Create one thread-safe Neo4j Driver for the Streamlit process."""
    uri, username, password, _ = _config()
    driver = GraphDatabase.driver(uri, auth=(username, password))
    driver.verify_connectivity()
    return driver


def query(cypher: str, parameters: dict[str, Any] | None = None, *, write: bool = False) -> list[dict[str, Any]]:
    """Execute parameterized Cypher and return rows as dictionaries."""
    _, _, _, database = _config()
    records, _, _ = get_driver().execute_query(
        cypher,
        parameters_=parameters or {},
        database_=database,
        routing_=RoutingControl.WRITE if write else RoutingControl.READ,
    )
    return [record.data() for record in records]


def ping() -> bool:
    rows = query("RETURN 1 AS ok")
    return bool(rows and rows[0]["ok"] == 1)


def create_schema() -> None:
    statements = [
        "CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.person_id IS UNIQUE",
        "CREATE CONSTRAINT car_id_unique IF NOT EXISTS FOR (c:Car) REQUIRE c.car_id IS UNIQUE",
    ]
    for stmt in statements:
        query(stmt, write=True)


def seed_demo_data() -> None:
    """Idempotent sample dataset for Car Recommendation System: safe to run more than once."""
    create_schema()

    persons = [
        {"person_id": "P001", "name": "Ing"},
        {"person_id": "P002", "name": "Somying"},
        {"person_id": "P003", "name": "Natee"},
        {"person_id": "P004", "name": "Wichai"},
        {"person_id": "P005", "name": "On"},
        {"person_id": "P006", "name": "Beam"},
        {"person_id": "P007", "name": "Non"},
        {"person_id": "P008", "name": "Games"},
        {"person_id": "P009", "name": "Plana"},
        {"person_id": "P010", "name": "Palm"},
    ]

    cars = [
        {"car_id": "C001", "name": "BYD Atto 3", "image": "https://example.com/byd.jpg"},
        {"car_id": "C002", "name": "Ford Ranger", "image": "https://example.com/ford.jpg"},
        {"car_id": "C003", "name": "Honda Civic", "image": ""},
        {"car_id": "C004", "name": "Tesla Model 3", "image": ""},
        {"car_id": "C005", "name": "Toyota Corolla", "image": ""},
        {"car_id": "C006", "name": "Honda HR-V", "image": ""},
        {"car_id": "C007", "name": "Mazda 3", "image": ""},
        {"car_id": "C008", "name": "Nissan Almera", "image": ""},
        {"car_id": "C009", "name": "Isuzu D-Max", "image": ""},
        {"car_id": "C010", "name": "Toyota Yaris", "image": ""},
    ]

    query(
        """
        UNWIND $rows AS row
        MERGE (p:Person {person_id: row.person_id})
        SET p.name = row.name
        """,
        {"rows": persons},
        write=True,
    )

    query(
        """
        UNWIND $rows AS row
        MERGE (c:Car {car_id: row.car_id})
        SET c.name = row.name, c.image = row.image
        """,
        {"rows": cars},
        write=True,
    )

    # สร้างความสัมพันธ์เพื่อน
    friendships = [
        ["P001", "P002"], ["P001", "P003"], ["P001", "P009"],
        ["P002", "P005"], ["P003", "P006"], ["P003", "P007"],
        ["P007", "P008"], ["P009", "P010"]
    ]
    query(
        """
        UNWIND $rows AS row
        MATCH (a:Person {person_id: row[0]}), (b:Person {person_id: row[1]})
        MERGE (a)-[:FRIEND_OF]->(b)
        """,
        {"rows": friendships},
        write=True,
    )

    # สร้างความสัมพันธ์การเป็นเจ้าของรถ
    ownerships = [
        {"pid": "P004", "cid": "C001"},
        {"pid": "P005", "cid": "C001"},
        {"pid": "P007", "cid": "C002"},
        {"pid": "P008", "cid": "C002"},
    ]
    query(
        """
        UNWIND $rows AS row
        MATCH (p:Person {person_id: row.pid}), (c:Car {car_id: row.cid})
        MERGE (p)-[:OWNS]->(c)
        """,
        {"rows": ownerships},
        write=True,
    )


def get_persons() -> list[dict[str, Any]]:
    return query("MATCH (p:Person) RETURN p.person_id AS person_id, p.name AS name ORDER BY p.person_id")


def get_cars() -> list[dict[str, Any]]:
    return query("MATCH (c:Car) RETURN c.car_id AS car_id, c.name AS name ORDER BY c.name")


def get_dashboard_metrics() -> dict[str, int]:
    rows = query(
        """
        MATCH (p:Person) WITH count(p) AS persons
        MATCH (c:Car) WITH persons, count(c) AS cars
        MATCH ()-[o:OWNS]->() WITH persons, cars, count(o) AS ownerships
        MATCH ()-[f:FRIEND_OF]->()
        RETURN (persons + cars) AS nodes, (ownerships + count(f)) AS relationships
        """
    )
    return rows[0] if rows else {"nodes": 0, "relationships": 0}


def recommend_cars(pid: str, min_hops: int = 1, max_hops: int = 2) -> list[dict[str, Any]]:
    """คำนวณการแนะนำรถยนต์จากเครือข่ายเพื่อน (เอา LIMIT ออกแล้ว)"""
    cypher = f"""
    MATCH (me:Person {{person_id: $pid}})-[:FRIEND_OF*{min_hops}..{max_hops}]-(f:Person)-[:OWNS]->(car:Car)
    WHERE f <> me AND NOT (me)-[:OWNS]->(car)
    RETURN car.car_id AS car_id,
           car.name AS car,
           car.image AS image,
           count(DISTINCT f) AS score,
           collect(DISTINCT f.name) AS owned_by
    ORDER BY score DESC, car
    """
    return query(cypher, {"pid": pid})


def get_popular_cars() -> list[dict[str, Any]]:
    return query(
        """
        MATCH (c:Car)
        OPTIONAL MATCH (p:Person)-[:OWNS]->(c)
        RETURN c.name AS car_name, count(p) AS owner_count
        ORDER BY owner_count DESC, c.name
        """
    )


def get_most_connected_users() -> list[dict[str, Any]]:
    return query(
        """
        MATCH (p:Person)
        OPTIONAL MATCH (p)-[:FRIEND_OF]-(f:Person)
        RETURN p.name AS user_name, count(DISTINCT f) AS friend_count
        ORDER BY friend_count DESC, p.name
        """
    )


def get_mutual_friends(pid1: str, pid2: str) -> list[str]:
    rows = query(
        """
        MATCH (p1:Person {person_id: $pid1})-[:FRIEND_OF]-(f:Person)-[:FRIEND_OF]-(p2:Person {person_id: $pid2})
        RETURN DISTINCT f.name AS mutual_friend
        """,
        {"pid1": pid1, "pid2": pid2},
    )
    return [r["mutual_friend"] for r in rows]


def add_person(name: str) -> None:
    query("CREATE (p:Person {person_id: randomUUID(), name: $name})", {"name": name}, write=True)


def add_car(name: str, image: str = "") -> None:
    query("CREATE (c:Car {car_id: randomUUID(), name: $name, image: $image})", {"name": name, "image": image}, write=True)


def add_friendship(person_id1: str, person_id2: str) -> None:
    query(
        """
        MATCH (a:Person {person_id: $p1}), (b:Person {person_id: $p2})
        MERGE (a)-[:FRIEND_OF]->(b)
        """,
        {"p1": person_id1, "p2": person_id2},
        write=True,
    )


def add_car_ownership(person_id: str, car_id: str) -> None:
    query(
        """
        MATCH (p:Person {person_id: $pid}), (c:Car {car_id: $cid})
        MERGE (p)-[:OWNS]->(c)
        """,
        {"pid": person_id, "cid": car_id},
        write=True,
    )


def delete_relationship(person_id1: str, person_id2_or_car_id: str, rel_type: str = "FRIEND_OF") -> None:
    if rel_type == "FRIEND_OF":
        query(
            """
            MATCH (a:Person {person_id: $p1})-[r:FRIEND_OF]-(b:Person {person_id: $p2})
            DELETE r
            """,
            {"p1": person_id1, "p2": person_id2_or_car_id},
            write=True,
        )
    elif rel_type == "OWNS":
        query(
            """
            MATCH (p:Person {person_id: $pid})-[r:OWNS]->(c:Car {car_id: $cid})
            DELETE r
            """,
            {"pid": person_id1, "cid": person_id2_or_car_id},
            write=True,
        )


def graph_neighborhood(pid: str, limit: int = 40) -> list[dict[str, Any]]:
    return query(
        """
        MATCH (u:Person {person_id: $pid})
        OPTIONAL MATCH p=(u)-[:FRIEND_OF|OWNS*1..2]-(x)
        WITH u, collect(p)[0..$limit] AS paths
        UNWIND paths AS p
        UNWIND relationships(p) AS r
        WITH DISTINCT startNode(r) AS s, r, endNode(r) AS t
        RETURN elementId(s) AS source_id, labels(s)[0] AS source_label,
               coalesce(s.name, s.person_id) AS source_name,
               type(r) AS relationship,
               elementId(t) AS target_id, labels(t)[0] AS target_label,
               coalesce(t.name, t.car_id) AS target_name
        LIMIT $limit
        """,
        {"pid": pid, "limit": int(limit)},
    )
