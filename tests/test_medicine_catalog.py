"""Tests for the local, display-only medicine autocomplete catalog."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ecg_cvd.clinical.app import create_app
from ecg_cvd.clinical.medicine_catalog import MedicineCatalog, MedicineCatalogError
from ecg_cvd.clinical.seed import seed_development_data


PASSWORD = "AMedicineCatalogTestPassword!"
CSV = """Medicine Name,Composition,Type of Medicine
Paracetamol 500mg Tablet,Paracetamol (500mg),strip of 10 tablets
Paracetamol 650mg Tablet,Paracetamol (650mg),strip of 10 tablets
Aspirin 300mg Tablet,Aspirin (300mg),strip of 10 tablets
"""


class MedicineCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ecg-medicine-catalog-")
        self.root = Path(self.temp.name)
        self.source = self.root / "medicine-catalog.csv"
        self.source.write_text(CSV, encoding="utf-8")
        self.index = self.root / "var" / "catalog.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_prefix_lookup_preserves_source_text_without_inventing_manufacturer(self):
        catalog = MedicineCatalog(self.source, self.index)
        items = catalog.search("para", 8)
        self.assertEqual([item.name for item in items], ["Paracetamol 500mg Tablet", "Paracetamol 650mg Tablet"])
        self.assertEqual(items[0].composition, "Paracetamol (500mg)")
        self.assertEqual(items[0].category, "Tablet")
        self.assertIsNone(items[0].manufacturer)
        self.assertEqual(items[0].api_dict(), {
            "id": "medicine-1", "name": "Paracetamol 500mg Tablet",
            "composition": "Paracetamol (500mg)", "category": "Tablet",
        })
        self.assertTrue(self.index.is_file())

    def test_blank_search_is_empty_and_overlong_search_is_rejected(self):
        catalog = MedicineCatalog(self.source, self.index)
        self.assertEqual(catalog.search("", 8), [])
        with self.assertRaises(MedicineCatalogError):
            catalog.search("x" * 101, 8)

    def test_authorized_doctor_can_use_endpoint_and_technician_cannot(self):
        app = create_app({
            "PROJECT_ROOT": str(self.root), "ENVIRONMENT": "test",
            "DATABASE_URL": f"sqlite+pysqlite:///{self.root / 'clinical.db'}",
            "JWT_SECRET": "x" * 48, "SECRET_KEY": "y" * 48,
            "LOCAL_OBJECT_STORAGE_PATH": str(self.root / "private_objects"),
            "MEDICINE_CATALOG_PATH": str(self.source),
            "MEDICINE_CATALOG_INDEX_PATH": str(self.index),
            "AUTO_CREATE_SCHEMA": True,
        })
        seed_development_data(app, PASSWORD)
        doctor = app.test_client()
        logged_in = doctor.post("/api/v1/auth/login", json={
            "email": "doctor@example.test", "password": PASSWORD,
        })
        self.assertEqual(logged_in.status_code, 200, logged_in.get_json())
        headers = {"Authorization": f"Bearer {logged_in.get_json()['access_token']}"}
        response = doctor.get("/api/v1/medicines?query=para&limit=8", headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json(), {
            "items": [
                {"id": "medicine-1", "name": "Paracetamol 500mg Tablet",
                 "composition": "Paracetamol (500mg)", "category": "Tablet"},
                {"id": "medicine-2", "name": "Paracetamol 650mg Tablet",
                 "composition": "Paracetamol (650mg)", "category": "Tablet"},
            ],
            "query": "para", "catalog_available": True,
        })
        technician = app.test_client()
        technician_login = technician.post("/api/v1/auth/login", json={
            "email": "technician@example.test", "password": PASSWORD,
        })
        technician_headers = {"Authorization": f"Bearer {technician_login.get_json()['access_token']}"}
        denied = technician.get("/api/v1/medicines?query=para", headers=technician_headers)
        self.assertEqual(denied.status_code, 403, denied.get_json())
