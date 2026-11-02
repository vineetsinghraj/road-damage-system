"""
Run with:  python3 -m pytest test_api.py -v
(or, if pytest isn't installed:  python3 test_api.py)

Uses a temp SQLite file so it never touches your real data/app.db.
"""
import os
import io
import tempfile
import unittest

TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["RDM_DB_PATH"] = TMP_DB

from database import init_db
from seed import seed_demo_data
import app as rdm_app


def make_test_image():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color=(100, 100, 100)).save(buf, format="JPEG")
    buf.seek(0)
    return buf


class RoadDamageAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        seed_demo_data()
        rdm_app.app.testing = True
        cls.client = rdm_app.app.test_client()

    def _login(self, email, password):
        r = self.client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(r.status_code, 200)
        return r.get_json()["token"]

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    # --- auth ---------------------------------------------------------
    def test_register_and_login(self):
        r = self.client.post("/api/auth/register", json={
            "name": "Test User", "email": "newuser@test.local", "password": "pw12345"})
        self.assertEqual(r.status_code, 201)
        token = self._login("newuser@test.local", "pw12345")
        self.assertTrue(token)

    def test_login_wrong_password_rejected(self):
        r = self.client.post("/api/auth/login", json={"email": "citizen@demo.local", "password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_protected_route_requires_token(self):
        r = self.client.get("/api/complaints")
        self.assertEqual(r.status_code, 401)

    # --- reports / complaints -----------------------------------------
    def test_report_upload_creates_complaint(self):
        token = self._login("citizen@demo.local", "citizen123")
        img = make_test_image()
        r = self.client.post("/api/reports",
                              data={"image": (img, "test.jpg"), "latitude": "28.61", "longitude": "77.20"},
                              content_type="multipart/form-data", headers=self._auth(token))
        self.assertIn(r.status_code, (200, 201))
        data = r.get_json()
        self.assertIn("detections", data)
        self.assertTrue(data["needs_manual_review"] or data.get("complaint") or data.get("duplicate"))

    def test_report_requires_image(self):
        token = self._login("citizen@demo.local", "citizen123")
        r = self.client.post("/api/reports", data={}, content_type="multipart/form-data", headers=self._auth(token))
        self.assertEqual(r.status_code, 400)

    def test_citizen_cannot_update_status(self):
        token = self._login("citizen@demo.local", "citizen123")
        r = self.client.patch("/api/complaints/1/status", json={"status": "Closed"}, headers=self._auth(token))
        self.assertEqual(r.status_code, 403)

    def test_authority_can_update_status(self):
        token = self._login("authority@demo.local", "authority123")
        complaints = self.client.get("/api/complaints", headers=self._auth(token)).get_json()
        if not complaints:
            self.skipTest("no complaints seeded yet")
        cid = complaints[0]["id"]
        r = self.client.patch(f"/api/complaints/{cid}/status",
                               json={"status": "Under Review", "remarks": "checked"}, headers=self._auth(token))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "Under Review")

    def test_invalid_status_rejected(self):
        token = self._login("authority@demo.local", "authority123")
        r = self.client.patch("/api/complaints/1/status", json={"status": "NotARealStatus"}, headers=self._auth(token))
        self.assertEqual(r.status_code, 400)

    # --- authorities / admin -------------------------------------------
    def test_non_admin_cannot_create_authority(self):
        token = self._login("citizen@demo.local", "citizen123")
        r = self.client.post("/api/authorities", json={"name": "X", "city": "Y"}, headers=self._auth(token))
        self.assertEqual(r.status_code, 403)

    def test_admin_can_create_authority(self):
        token = self._login("admin@demo.local", "admin123")
        r = self.client.post("/api/authorities", json={"name": "Test Authority", "city": "Chennai"},
                              headers=self._auth(token))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["city"], "Chennai")

    # --- map / analytics -------------------------------------------------
    def test_map_endpoint_returns_list(self):
        token = self._login("citizen@demo.local", "citizen123")
        r = self.client.get("/api/map/damages", headers=self._auth(token))
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), list)

    def test_analytics_requires_authority_or_admin(self):
        token = self._login("citizen@demo.local", "citizen123")
        r = self.client.get("/api/analytics", headers=self._auth(token))
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
