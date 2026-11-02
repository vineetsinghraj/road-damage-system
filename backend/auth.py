import os
import datetime
from functools import wraps

import jwt
from flask import request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

from database import get_conn

JWT_SECRET = os.environ.get("RDM_JWT_SECRET", "dev-secret-change-me")
JWT_ALGO = "HS256"
JWT_EXPIRY_HOURS = 12


def hash_password(pw):
    return generate_password_hash(pw)


def verify_password(pw, pw_hash):
    return check_password_hash(pw_hash, pw)


def issue_token(user):
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "email": user["email"],
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def _decode(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        return None


def login_required(roles=None):
    """
    Decorator. If `roles` is given (e.g. ["authority", "admin"]), also
    enforces role-based authorization. Sets g.user_id / g.role / g.email.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "Missing or invalid Authorization header"}), 401
            token = auth_header.split(" ", 1)[1]
            payload = _decode(token)
            if not payload:
                return jsonify({"error": "Invalid or expired token"}), 401
            if roles and payload["role"] not in roles:
                return jsonify({"error": "Forbidden: insufficient role"}), 403
            g.user_id = payload["sub"]
            g.role = payload["role"]
            g.email = payload["email"]
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def current_user_row():
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (g.user_id,)).fetchone()
    conn.close()
    return row
