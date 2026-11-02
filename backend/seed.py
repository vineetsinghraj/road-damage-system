"""
Seeds demo accounts + demo authorities so the app can be explored without
manual registration. Clearly a DEMO fixture -- not real government data.
Safe to run multiple times (checks before inserting).
"""
from database import get_conn, db_session
from auth import hash_password

DEMO_USERS = [
    ("Admin User", "admin@demo.local", "admin123", "admin", None),
    ("Delhi PWD Officer", "authority@demo.local", "authority123", "authority", "Delhi"),
    ("Asha Citizen", "citizen@demo.local", "citizen123", "citizen", None),
]

DEMO_AUTHORITIES = [
    ("Delhi Public Works Department", "Delhi", "Municipal Corporation", "pwd.delhi@demo.local"),
    ("Mumbai Municipal Corporation", "Mumbai", "Municipal Corporation", "bmc.mumbai@demo.local"),
    ("Bengaluru BBMP", "Bengaluru", "Municipal Corporation", "bbmp.bengaluru@demo.local"),
    ("Pune Municipal Corporation", "Pune", "Municipal Corporation", "pmc.pune@demo.local"),
]


def seed_demo_data():
    with db_session() as conn:
        existing = conn.execute("SELECT COUNT(*) c FROM authorities").fetchone()["c"]
        if existing == 0:
            for name, city, category, email in DEMO_AUTHORITIES:
                conn.execute(
                    "INSERT INTO authorities (name, city, category, contact_email) VALUES (?,?,?,?)",
                    (name, city, category, email),
                )

        authority_lookup = {
            row["city"]: row["id"]
            for row in conn.execute("SELECT id, city FROM authorities").fetchall()
        }

        for name, email, password, role, city in DEMO_USERS:
            existing_user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if existing_user:
                continue
            authority_id = authority_lookup.get(city) if role == "authority" else None
            conn.execute(
                "INSERT INTO users (name, email, password_hash, role, authority_id) VALUES (?,?,?,?,?)",
                (name, email, hash_password(password), role, authority_id),
            )
        print("[seed] demo accounts ready: admin@demo.local / authority@demo.local / citizen@demo.local (see README for passwords)")
