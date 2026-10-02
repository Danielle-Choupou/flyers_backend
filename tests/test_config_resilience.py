import unittest
from unittest.mock import patch

import server
from fastapi import HTTPException
from fastapi.testclient import TestClient


class ConfigResilienceTests(unittest.TestCase):
    def setUp(self):
        server._apply_config_snapshot({}, {}, "unavailable")
        server.LAST_CONFIG_ERROR = None
        server._last_config_attempt = 0.0

    def test_supabase_success_refreshes_runtime_and_redis_snapshot(self):
        profiles = {"ACME": {"nom_officiel": "Acme"}}
        templates = {"ACME": {"Flyer": {"objectif_publication": "Annonce"}}}
        with (
            patch.object(server, "load_configs", return_value=(profiles, templates)),
            patch.object(server.cache, "set_json", return_value=True) as set_json,
        ):
            source = server.refresh_config(force=True)

        self.assertEqual(source, "supabase")
        self.assertEqual(server.COMPANY_PROFILES, profiles)
        self.assertEqual(server.TEMPLATES_CONFIG, templates)
        set_json.assert_called_once_with(
            server.CONFIG_CACHE_KEY,
            {"profiles": profiles, "templates": templates},
            ttl=server.CONFIG_CACHE_TTL,
        )

    def test_supabase_failure_uses_redis_snapshot_read_only(self):
        snapshot = {
            "profiles": {"ACME": {"nom_officiel": "Acme"}},
            "templates": {"ACME": {"Flyer": {}}},
        }
        with (
            patch.object(server, "load_configs", side_effect=ConnectionError("offline")) as load,
            patch.object(server.cache, "get_json", return_value=snapshot),
            patch.object(server.time, "sleep"),
        ):
            source = server.refresh_config(force=True)

        self.assertEqual(source, "redis_stale")
        self.assertEqual(server.COMPANY_PROFILES, snapshot["profiles"])
        self.assertEqual(server.TEMPLATES_CONFIG, snapshot["templates"])
        self.assertEqual(load.call_count, 3)
        self.assertTrue(server._route_writes_supabase("PUT", "/modeles/ACME/Flyer"))
        self.assertFalse(server._route_writes_supabase("GET", "/modeles/ACME/Flyer"))

    def test_supabase_failure_without_snapshot_marks_backend_unavailable(self):
        with (
            patch.object(server, "load_configs", side_effect=ConnectionError("offline")),
            patch.object(server.cache, "get_json", return_value=None),
            patch.object(server.time, "sleep"),
        ):
            source = server.refresh_config(force=True)

        self.assertEqual(source, "unavailable")
        self.assertEqual(server.COMPANY_PROFILES, {})
        self.assertEqual(server.TEMPLATES_CONFIG, {})

    def test_reload_requires_live_supabase(self):
        with patch.object(server, "refresh_config", return_value="redis_stale"):
            with self.assertRaises(HTTPException) as raised:
                server.reload()

        self.assertEqual(raised.exception.status_code, 503)

    def test_stale_snapshot_serves_reads_and_blocks_writes(self):
        snapshot = {
            "profiles": {"ACME": {"nom_officiel": "Acme"}},
            "templates": {"ACME": {"Flyer": {"objectif_publication": "Annonce"}}},
        }
        with (
            patch.object(server, "load_configs", side_effect=ConnectionError("offline")),
            patch.object(server.cache, "get_json", return_value=snapshot),
            patch.object(server.cache, "set_json", return_value=True),
            patch.object(server.time, "sleep"),
        ):
            with TestClient(server.app) as client:
                health = client.get("/health")
                ready = client.get("/ready")
                companies = client.get("/entreprises")
                create = client.post("/entreprises", json={"nom": "NEW"})

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["config_source"], "redis_stale")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(companies.status_code, 200)
        self.assertEqual(companies.json(), {"ACME": ["Flyer"]})
        self.assertEqual(create.status_code, 503)
        self.assertTrue(create.headers.get("X-Request-ID"))
        self.assertTrue(create.headers.get("X-Process-Time-Ms"))

    def test_backend_stays_alive_without_any_config_snapshot(self):
        with (
            patch.object(server, "load_configs", side_effect=ConnectionError("offline")),
            patch.object(server.cache, "get_json", return_value=None),
            patch.object(server.time, "sleep"),
        ):
            with TestClient(server.app) as client:
                health = client.get("/health")
                ready = client.get("/ready")
                companies = client.get("/entreprises")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["config_source"], "unavailable")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(companies.status_code, 503)

    def test_degraded_backend_recovers_when_supabase_returns(self):
        snapshot = {
            "profiles": {"ACME": {"nom_officiel": "Ancien"}},
            "templates": {"ACME": {"Ancien": {}}},
        }
        fresh_profiles = {"BETA": {"nom_officiel": "Beta"}}
        fresh_templates = {"BETA": {"Nouveau": {}}}
        live = False

        def load_when_available():
            if not live:
                raise ConnectionError("offline")
            return fresh_profiles, fresh_templates

        with (
            patch.object(server, "load_configs", side_effect=load_when_available),
            patch.object(server.cache, "get_json", return_value=snapshot),
            patch.object(server.cache, "set_json", return_value=True),
            patch.object(server.time, "sleep"),
        ):
            with TestClient(server.app) as client:
                stale = client.get("/entreprises")
                live = True
                server._last_config_attempt = 0.0
                recovered = client.get("/entreprises")
                ready = client.get("/ready")

        self.assertEqual(stale.json(), {"ACME": ["Ancien"]})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json(), {"BETA": ["Nouveau"]})
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["config_source"], "supabase")


if __name__ == "__main__":
    unittest.main()
