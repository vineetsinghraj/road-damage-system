import os
import json
import uuid
import datetime

from flask import Flask, request, jsonify, g, send_from_directory
from werkzeug.utils import secure_filename

from database import init_db, get_conn, db_session
from auth import hash_password, verify_password, issue_token, login_required, current_user_row
from ai_detector import (
    detect_road_damage, estimate_severity, annotate_image,
    reverse_geocode, CONFIDENCE_THRESHOLD,
)
from utils import haversine_meters, generate_complaint_ref, notify_user

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
ALLOWED_EXT = {"png", "jpg", "jpeg"}
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8 MB
DUPLICATE_RADIUS_M = 150
DUPLICATE_WINDOW_DAYS = 30

STATUS_FLOW = [
    "Submitted", "Under Review", "Assigned", "In Progress",
    "Repair Completed", "Verified", "Closed", "Rejected", "Duplicate",
]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
os.makedirs(UPLOAD_DIR, exist_ok=True)


def row_to_dict(row):
    return dict(row) if row else None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


# ---------------------------------------------------------------- AUTH -----
@app.post("/api/auth/register")
def register():
    data = request.get_json(force=True) or {}
    name, email, password = data.get("name"), data.get("email"), data.get("password")
    role = data.get("role", "citizen")
    if not all([name, email, password]):
        return jsonify({"error": "name, email and password are required"}), 400
    if role not in ("citizen", "authority"):
        role = "citizen"  # admins are seeded, not self-registered

    with db_session() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return jsonify({"error": "Email already registered"}), 409
        cur = conn.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (name, email, hash_password(password), role),
        )
        user = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    token = issue_token(user)
    return jsonify({"token": token, "user": {"id": user["id"], "name": user["name"], "role": user["role"]}}), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json(force=True) or {}
    email, password = data.get("email"), data.get("password")
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not user or not verify_password(password, user["password_hash"]):
        return jsonify({"error": "Invalid email or password"}), 401
    token = issue_token(user)
    return jsonify({"token": token, "user": {"id": user["id"], "name": user["name"], "role": user["role"]}})


@app.get("/api/auth/me")
@login_required()
def me():
    user = current_user_row()
    return jsonify({"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]})


# -------------------------------------------------------------- REPORTS ----
@app.post("/api/reports")
@login_required(roles=["citizen"])
def create_report():
    if "image" not in request.files:
        return jsonify({"error": "image file is required (field name: image)"}), 400
    file = request.files["image"]
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "invalid or missing image file (png/jpg only)"}), 400

    lat = request.form.get("latitude", type=float)
    lng = request.form.get("longitude", type=float)

    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    path = os.path.join(UPLOAD_DIR, filename)
    file.save(path)

    raw_detections = detect_road_damage(path)
    for d in raw_detections:
        d["severity"] = estimate_severity(d)

    # Pick the highest-confidence detection to drive the report/complaint;
    # all detections are kept in `all_detections` for the record.
    best = max(raw_detections, key=lambda d: d["confidence"])
    needs_review = best["confidence"] < CONFIDENCE_THRESHOLD

    annotated_name = f"annotated_{filename}"
    annotate_image(path, raw_detections, os.path.join(UPLOAD_DIR, annotated_name))

    geo = reverse_geocode(lat, lng)

    with db_session() as conn:
        cur = conn.execute(
            """INSERT INTO road_damage_reports
               (user_id, image_path, annotated_image_path, damage_type, confidence,
                severity, bbox, needs_manual_review, latitude, longitude,
                address, city, state, country)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (g.user_id, filename, annotated_name, best["damage_type"], best["confidence"],
             best["severity"], json.dumps([d["bbox"] for d in raw_detections]), int(needs_review),
             lat, lng, geo["address"], geo["city"], geo["state"], geo["country"]),
        )
        report_id = cur.lastrowid

        result = {"report_id": report_id, "detections": raw_detections, "needs_manual_review": needs_review}

        if needs_review:
            result["complaint"] = None
            result["message"] = "Low-confidence detection flagged for manual verification; no complaint auto-created."
            return jsonify(result), 201

        # --- duplicate check -------------------------------------------
        window_start = (datetime.datetime.utcnow() - datetime.timedelta(days=DUPLICATE_WINDOW_DAYS)).isoformat()
        candidates = conn.execute(
            """SELECT c.id as complaint_id, c.complaint_ref, r.latitude, r.longitude, r.damage_type
               FROM complaints c JOIN road_damage_reports r ON c.report_id = r.id
               WHERE r.damage_type = ? AND c.created_at >= ? AND c.status != 'Duplicate'""",
            (best["damage_type"], window_start),
        ).fetchall()
        duplicate = None
        for cand in candidates:
            if haversine_meters(lat, lng, cand["latitude"], cand["longitude"]) <= DUPLICATE_RADIUS_M:
                duplicate = cand
                break

        if duplicate:
            result["duplicate"] = {
                "existing_complaint_id": duplicate["complaint_id"],
                "complaint_ref": duplicate["complaint_ref"],
                "message": "A similar road-damage complaint already exists nearby. "
                           "Upvote it instead of filing a new one.",
            }
            result["complaint"] = None
            return jsonify(result), 200

        # --- authority mapping (by city) --------------------------------
        authority = conn.execute(
            "SELECT * FROM authorities WHERE city = ? LIMIT 1", (geo["city"],)
        ).fetchone()
        authority_id = authority["id"] if authority else None

        ref = generate_complaint_ref(conn)
        cur = conn.execute(
            """INSERT INTO complaints (complaint_ref, report_id, user_id, authority_id, status)
               VALUES (?,?,?,?,?)""",
            (ref, report_id, g.user_id, authority_id, "Submitted"),
        )
        complaint_id = cur.lastrowid
        conn.execute(
            "INSERT INTO complaint_status_history (complaint_id, status, remarks, changed_by) VALUES (?,?,?,?)",
            (complaint_id, "Submitted", "Auto-generated from AI detection", g.user_id),
        )
        notify_user(conn, g.user_id, f"Complaint {ref} created for {best['damage_type']} ({best['severity']}).")

        result["complaint"] = {
            "id": complaint_id, "complaint_ref": ref, "status": "Submitted",
            "authority_id": authority_id, "authority_name": authority["name"] if authority else None,
        }
        return jsonify(result), 201


@app.get("/api/reports")
@login_required()
def list_reports():
    conn = get_conn()
    if g.role == "citizen":
        rows = conn.execute(
            "SELECT * FROM road_damage_reports WHERE user_id = ? ORDER BY created_at DESC", (g.user_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM road_damage_reports ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.get("/api/reports/<int:report_id>")
@login_required()
def get_report(report_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM road_damage_reports WHERE id = ?", (report_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row_to_dict(row))


# ------------------------------------------------------------ COMPLAINTS ---
@app.get("/api/complaints")
@login_required()
def list_complaints():
    status = request.args.get("status")
    severity = request.args.get("severity")
    authority_id = request.args.get("authority_id")

    query = """
        SELECT c.*, r.damage_type, r.severity, r.latitude, r.longitude, r.address,
               r.city, r.image_path, r.annotated_image_path, u.name as citizen_name,
               a.name as authority_name,
               (SELECT COUNT(*) FROM upvotes up WHERE up.complaint_id = c.id) as upvote_count
        FROM complaints c
        JOIN road_damage_reports r ON c.report_id = r.id
        JOIN users u ON c.user_id = u.id
        LEFT JOIN authorities a ON c.authority_id = a.id
        WHERE 1=1
    """
    params = []
    if g.role == "citizen":
        query += " AND c.user_id = ?"
        params.append(g.user_id)
    if status:
        query += " AND c.status = ?"
        params.append(status)
    if severity:
        query += " AND r.severity = ?"
        params.append(severity)
    if authority_id:
        query += " AND c.authority_id = ?"
        params.append(authority_id)
    query += " ORDER BY c.created_at DESC"

    conn = get_conn()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.get("/api/complaints/<int:complaint_id>")
@login_required()
def get_complaint(complaint_id):
    conn = get_conn()
    c = conn.execute(
        """SELECT c.*, r.damage_type, r.severity, r.latitude, r.longitude, r.address,
                  r.image_path, r.annotated_image_path
           FROM complaints c JOIN road_damage_reports r ON c.report_id = r.id
           WHERE c.id = ?""", (complaint_id,)).fetchone()
    if not c:
        conn.close()
        return jsonify({"error": "not found"}), 404
    history = conn.execute(
        "SELECT * FROM complaint_status_history WHERE complaint_id = ? ORDER BY changed_at",
        (complaint_id,)).fetchall()
    conn.close()
    result = row_to_dict(c)
    result["history"] = [row_to_dict(h) for h in history]
    return jsonify(result)


@app.patch("/api/complaints/<int:complaint_id>/status")
@login_required(roles=["authority", "admin"])
def update_status(complaint_id):
    data = request.get_json(force=True) or {}
    new_status = data.get("status")
    remarks = data.get("remarks", "")
    assigned_to = data.get("assigned_to")

    if new_status not in STATUS_FLOW:
        return jsonify({"error": f"status must be one of {STATUS_FLOW}"}), 400

    with db_session() as conn:
        c = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if not c:
            return jsonify({"error": "not found"}), 404

        fields, params = ["status = ?", "updated_at = datetime('now')"], [new_status]
        if assigned_to is not None:
            fields.append("assigned_to = ?")
            params.append(assigned_to)
        params.append(complaint_id)
        conn.execute(f"UPDATE complaints SET {', '.join(fields)} WHERE id = ?", params)

        conn.execute(
            "INSERT INTO complaint_status_history (complaint_id, status, remarks, changed_by) VALUES (?,?,?,?)",
            (complaint_id, new_status, remarks, g.user_id),
        )
        notify_user(conn, c["user_id"], f"Complaint {c['complaint_ref']} status changed to '{new_status}'.")
        updated = conn.execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        return jsonify(row_to_dict(updated))


@app.post("/api/complaints/<int:complaint_id>/upvote")
@login_required(roles=["citizen"])
def upvote(complaint_id):
    with db_session() as conn:
        try:
            conn.execute(
                "INSERT INTO upvotes (complaint_id, user_id) VALUES (?, ?)",
                (complaint_id, g.user_id),
            )
        except Exception:
            return jsonify({"error": "already upvoted"}), 409
        count = conn.execute(
            "SELECT COUNT(*) c FROM upvotes WHERE complaint_id = ?", (complaint_id,)
        ).fetchone()["c"]
    return jsonify({"complaint_id": complaint_id, "upvote_count": count})


# ------------------------------------------------------------- MAP/STATS ---
@app.get("/api/map/damages")
@login_required()
def map_damages():
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.id as complaint_id, c.complaint_ref, c.status, r.damage_type, r.severity,
                  r.latitude, r.longitude, r.created_at
           FROM complaints c JOIN road_damage_reports r ON c.report_id = r.id
           WHERE r.latitude IS NOT NULL AND r.longitude IS NOT NULL"""
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.get("/api/analytics")
@login_required(roles=["authority", "admin"])
def analytics():
    conn = get_conn()
    by_status = conn.execute(
        "SELECT status, COUNT(*) as count FROM complaints GROUP BY status").fetchall()
    by_severity = conn.execute(
        """SELECT r.severity, COUNT(*) as count FROM complaints c
           JOIN road_damage_reports r ON c.report_id = r.id GROUP BY r.severity"""
    ).fetchall()
    by_type = conn.execute(
        """SELECT r.damage_type, COUNT(*) as count FROM complaints c
           JOIN road_damage_reports r ON c.report_id = r.id GROUP BY r.damage_type"""
    ).fetchall()
    by_month = conn.execute(
        """SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as count
           FROM complaints GROUP BY month ORDER BY month"""
    ).fetchall()
    totals = conn.execute(
        """SELECT
             COUNT(*) as total,
             SUM(CASE WHEN status NOT IN ('Closed','Rejected','Duplicate') THEN 1 ELSE 0 END) as active,
             SUM(CASE WHEN status = 'Closed' THEN 1 ELSE 0 END) as resolved,
             SUM(CASE WHEN r.severity = 'high' OR r.severity = 'critical' THEN 1 ELSE 0 END) as high_priority
           FROM complaints c JOIN road_damage_reports r ON c.report_id = r.id"""
    ).fetchone()
    avg_res = conn.execute(
        """SELECT AVG(julianday(updated_at) - julianday(created_at)) as avg_days
           FROM complaints WHERE status = 'Closed'"""
    ).fetchone()
    conn.close()
    return jsonify({
        "totals": row_to_dict(totals),
        "avg_resolution_days": round(avg_res["avg_days"], 1) if avg_res["avg_days"] else None,
        "by_status": [row_to_dict(r) for r in by_status],
        "by_severity": [row_to_dict(r) for r in by_severity],
        "by_type": [row_to_dict(r) for r in by_type],
        "by_month": [row_to_dict(r) for r in by_month],
    })


# ------------------------------------------------------------ AUTHORITIES --
@app.get("/api/authorities")
@login_required()
def list_authorities():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM authorities ORDER BY city").fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/authorities")
@login_required(roles=["admin"])
def create_authority():
    data = request.get_json(force=True) or {}
    required = ["name", "city"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": f"required fields: {required}"}), 400
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO authorities (name, city, category, contact_email) VALUES (?,?,?,?)",
            (data["name"], data["city"], data.get("category", "Municipal Corporation"), data.get("contact_email")),
        )
        row = conn.execute("SELECT * FROM authorities WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.patch("/api/authorities/<int:authority_id>")
@login_required(roles=["admin"])
def update_authority(authority_id):
    data = request.get_json(force=True) or {}
    fields, params = [], []
    for k in ("name", "city", "category", "contact_email"):
        if k in data:
            fields.append(f"{k} = ?")
            params.append(data[k])
    if not fields:
        return jsonify({"error": "no fields to update"}), 400
    params.append(authority_id)
    with db_session() as conn:
        conn.execute(f"UPDATE authorities SET {', '.join(fields)} WHERE id = ?", params)
        row = conn.execute("SELECT * FROM authorities WHERE id = ?", (authority_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row_to_dict(row))


# ------------------------------------------------------------------ USERS --
@app.get("/api/users")
@login_required(roles=["admin"])
def list_users():
    conn = get_conn()
    rows = conn.execute("SELECT id, name, email, role, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


# ------------------------------------------------------------ NOTIFICATIONS-
@app.get("/api/notifications")
@login_required()
def list_notifications():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50", (g.user_id,)
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


# --------------------------------------------------------- STATIC/UPLOADS --
@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/")
@app.get("/<path:path>")
def frontend(path="index.html"):
    if os.path.exists(os.path.join(FRONTEND_DIR, path)):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    init_db()
    from seed import seed_demo_data
    seed_demo_data()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=True)
