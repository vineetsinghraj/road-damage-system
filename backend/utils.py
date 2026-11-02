import math
import datetime


def haversine_meters(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2):
        return float("inf")
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def generate_complaint_ref(conn):
    year = datetime.datetime.utcnow().year
    row = conn.execute(
        "SELECT COUNT(*) as c FROM complaints WHERE complaint_ref LIKE ?",
        (f"RDM-{year}-%",),
    ).fetchone()
    seq = row["c"] + 1
    return f"RDM-{year}-{seq:06d}"


# --- Notification provider interface ---------------------------------------
# DEMO MODE: notifications are written to the `notifications` table and
# printed to the console. To support real email/push, implement additional
# providers with the same signature and call them alongside notify_user().
#
#   def send_email_notification(user_row, message): ...
#   def send_push_notification(user_row, message): ...
#
# then have notify_user() dispatch to whichever providers are configured
# via environment variables (e.g. RDM_NOTIFY_PROVIDERS=email,push).
def notify_user(conn, user_id, message):
    conn.execute(
        "INSERT INTO notifications (user_id, message) VALUES (?, ?)",
        (user_id, message),
    )
    print(f"[notify] user={user_id}: {message}")
