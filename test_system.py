"""
Automated Smoke Test Verification for Multi-Profile Web Automation System.
Verifies Database operations, Proxy parsing, Playwright context initialization,
and FastAPI endpoint responses.
"""

import os
import sys
import asyncio
import unittest
from fastapi.testclient import TestClient

# Ensure current directory is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
import browser_engine as engine
from main import app


class TestDatabaseLayer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()

    async def test_profile_and_queue_lifecycle(self):
        # Create test profile
        test_id = "test_profile_smoke"
        # Cleanup if exists
        await db.delete_profile(test_id)

        profile = await db.create_profile(
            profile_id=test_id,
            label="Smoke Test Profile",
            proxy_url="http://user:pass@127.0.0.1:8080",
            daily_limit=20
        )
        self.assertIsNotNone(profile)
        self.assertEqual(profile["id"], test_id)
        self.assertEqual(profile["daily_limit"], 20)

        # Update status
        await db.update_profile_status(test_id, "running")
        updated = await db.get_profile(test_id)
        self.assertEqual(updated["status"], "running")

        # Add queue items
        item_id1 = await db.add_queue_item(test_id, "https://example.com/profile/1", "Hello there!")
        self.assertGreater(item_id1, 0)

        bulk_items = [
            (test_id, "https://example.com/profile/2", "Note 2"),
            (test_id, "https://example.com/profile/3", None)
        ]
        inserted = await db.add_queue_items_bulk(bulk_items)
        self.assertEqual(inserted, 2)

        # Retrieve pending items
        pending = await db.get_pending_queue_items(test_id, limit=10)
        self.assertEqual(len(pending), 3)

        # Update item status
        await db.update_queue_item_status(item_id1, "completed", error_message="Dispatched")
        items = await db.get_queue_items(profile_id=test_id)
        completed_items = [i for i in items if i["status"] == "completed"]
        self.assertEqual(len(completed_items), 1)

        # Get dashboard stats
        stats = await db.get_dashboard_stats()
        self.assertGreaterEqual(stats["total_profiles"], 1)

        # Test update_profile
        await db.update_profile(test_id, label="Updated Label", proxy_url=None, daily_limit=25)
        prof_updated = await db.get_profile(test_id)
        self.assertEqual(prof_updated["label"], "Updated Label")
        self.assertIsNone(prof_updated["proxy_url"])
        self.assertEqual(prof_updated["daily_limit"], 25)

        # Test add_discovered_target (>100 mutual connections & deduplication)
        disc_url = "https://www.linkedin.com/in/mutual-leader"
        added1 = await db.add_discovered_target(test_id, disc_url, mutual_count=145, name="Mutual Leader")
        self.assertTrue(added1)

        # Duplicate should be ignored
        added2 = await db.add_discovered_target(test_id, disc_url, mutual_count=145, name="Mutual Leader")
        self.assertFalse(added2)

        # Test Auto-Pilot fields and queries
        ap_test_id = "test_ap_profile"
        await db.delete_profile(ap_test_id)
        ap_prof = await db.create_profile(
            profile_id=ap_test_id,
            label="AutoPilot Profile",
            daily_limit=20,
            auto_pilot=1,
            schedule_hour=8
        )
        self.assertEqual(ap_prof["auto_pilot"], 1)
        self.assertEqual(ap_prof["schedule_hour"], 8)
        self.assertIsNone(ap_prof["last_auto_run_date"])

        # Check today sent count initially 0
        today_sent = await db.get_today_sent_count(ap_test_id)
        self.assertEqual(today_sent, 0)

        # Check due profiles
        due = await db.get_auto_pilot_due_profiles(current_hour=10, today_str="2026-10-02")
        self.assertTrue(any(p["id"] == ap_test_id for p in due))

        # Record run completed
        await db.record_daily_auto_run_completed(ap_test_id, "2026-10-02")
        due_after = await db.get_auto_pilot_due_profiles(current_hour=10, today_str="2026-10-02")
        self.assertFalse(any(p["id"] == ap_test_id for p in due_after))

        # Test reset_stale_profile_statuses
        await db.update_profile_status(ap_test_id, "running")
        recovered = await db.reset_stale_profile_statuses()
        self.assertGreaterEqual(recovered, 1)
        prof_st = await db.get_profile(ap_test_id)
        self.assertEqual(prof_st["status"], "idle")

        # Toggle auto-pilot off
        await db.update_profile_auto_pilot(ap_test_id, auto_pilot=0)
        due_off = await db.get_auto_pilot_due_profiles(current_hour=10, today_str="2026-10-03")
        self.assertFalse(any(p["id"] == ap_test_id for p in due_off))

        # Test 50/50 Dual Strategy method tracking
        dual_test_id = "test_dual_profile"
        await db.delete_profile(dual_test_id)
        await db.create_profile(dual_test_id, label="Dual Test Profile", daily_limit=30)

        # Record 2 via Search
        await db.record_sent_connection(
            dual_test_id, 
            "https://www.linkedin.com/in/vp-eng-stripe", 
            mutual_count=0, 
            name="VP Eng Stripe", 
            role_label="High-Profile Tech Leader", 
            method="search"
        )
        await db.record_sent_connection(
            dual_test_id, 
            "https://www.linkedin.com/in/tech-recruiter-meta", 
            mutual_count=0, 
            name="Recruiter Meta", 
            role_label="Tech Company HR", 
            method="search"
        )

        # Record 2 via Network
        await db.record_sent_connection(
            dual_test_id, 
            "https://www.linkedin.com/in/founder-ai", 
            mutual_count=130, 
            name="Founder AI", 
            role_label="Tech Founder", 
            method="network"
        )
        await db.record_sent_connection(
            dual_test_id, 
            "https://www.linkedin.com/in/staff-swe-google", 
            mutual_count=115, 
            name="Staff SWE Google", 
            role_label="Software Engineer", 
            method="network"
        )

        counts = await db.get_today_method_counts(dual_test_id)
        self.assertEqual(counts["total"], 4)
        self.assertEqual(counts["search"], 2)
        self.assertEqual(counts["network"], 2)

        # Check get_all_profiles has method breakdown fields
        all_profs = await db.get_all_profiles()
        dual_prof = next((p for p in all_profs if p["id"] == dual_test_id), None)
        self.assertIsNotNone(dual_prof)
        self.assertEqual(dual_prof["today_sent_count"], 4)
        self.assertEqual(dual_prof["today_search_count"], 2)
        self.assertEqual(dual_prof["today_network_count"], 2)

        await db.delete_profile(dual_test_id)

        # Cleanup
        deleted = await db.delete_profile(test_id)
        self.assertTrue(deleted)



class TestBrowserEngine(unittest.TestCase):
    def test_proxy_parser(self):
        # Test standard http
        p1 = engine.parse_proxy_settings("http://127.0.0.1:8080")
        self.assertEqual(p1["server"], "http://127.0.0.1:8080")
        self.assertNotIn("username", p1)

        # Test auth proxy
        p2 = engine.parse_proxy_settings("http://myuser:mypass@proxy.corp.net:3128")
        self.assertEqual(p2["server"], "http://proxy.corp.net:3128")
        self.assertEqual(p2["username"], "myuser")
        self.assertEqual(p2["password"], "mypass")

        # Test socks5
        p3 = engine.parse_proxy_settings("socks5://10.0.0.1:1080")
        self.assertEqual(p3["server"], "socks5://10.0.0.1:1080")

        # Test None / empty / example placeholder
        self.assertIsNone(engine.parse_proxy_settings(None))
        self.assertIsNone(engine.parse_proxy_settings("   "))
        self.assertIsNone(engine.parse_proxy_settings("http://user:pass@proxy.example.com:8080"))

    def test_search_query_pools(self):
        # Verify search query pools cover leaders, insiders, and HRs
        self.assertGreaterEqual(len(engine.SEARCH_QUERY_POOLS), 4)
        all_queries = " ".join(engine.SEARCH_QUERY_POOLS).lower()
        self.assertIn("vp of engineering", all_queries)
        self.assertIn("recruiter", all_queries)
        self.assertIn("principal", all_queries)
        self.assertIn("cto", all_queries)

    def test_playwright_stealth_and_search_script(self):
        async def run_test():
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                context = await engine.launch_profile_context(
                    pw,
                    profile_id="smoke_pw_test",
                    headless=True
                )
                page = await context.new_page()
                await page.goto("about:blank")
                # Verify stealth script injected
                webdriver_val = await page.evaluate("() => navigator.webdriver")
                languages_val = await page.evaluate("() => navigator.languages")
                has_chrome = await page.evaluate("() => typeof window.chrome === 'object'")

                self.assertIsNone(webdriver_val)
                self.assertIn("en-US", languages_val)
                self.assertTrue(has_chrome)

                # Set up HTML mock of LinkedIn search results
                mock_html = """
                <html><body>
                    <ul class="search-results-container">
                        <li class="reusable-search__result-container">
                            <a class="app-aware-link" href="https://www.linkedin.com/in/john-techleader">John Leader</a>
                            <div class="entity-result__primary-subtitle">VP of Engineering at Google</div>
                            <button aria-label="Invite John to connect">Connect</button>
                        </li>
                        <li class="reusable-search__result-container">
                            <a class="app-aware-link" href="https://www.linkedin.com/in/sarah-recruiter">Sarah HR</a>
                            <div class="entity-result__primary-subtitle">Technical Recruiter at Stripe</div>
                            <button aria-label="Invite Sarah to connect">Connect</button>
                        </li>
                    </ul>
                </body></html>
                """
                await page.set_content(mock_html)

                # Evaluate SCRIPT_SCAN_SEARCH_CANDIDATES
                cands = await page.evaluate(engine.SCRIPT_SCAN_SEARCH_CANDIDATES)
                self.assertEqual(len(cands), 2)
                self.assertTrue(any(c["role_category"] == "TECH_LEADER" for c in cands))
                self.assertTrue(any(c["role_category"] == "TECH_HR" for c in cands))
                self.assertIsNotNone(cands[0]["btnTag"])

                await context.close()

                # Verify profile directory created
                test_dir = os.path.join(engine.PROFILES_DIR, "smoke_pw_test")
                self.assertTrue(os.path.exists(test_dir))

        asyncio.run(run_test())


class TestFastAPIRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_dashboard_endpoint(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Profile Orchestrator", response.text)
        self.assertIn("Automation Queue", response.text)

    def test_api_stats_endpoint(self):
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total_profiles", data)
        self.assertIn("pending_items", data)

    def test_api_profiles_endpoint(self):
        response = self.client.get("/api/profiles")
        self.assertEqual(response.status_code, 200)
        self.assertIn("profiles", response.json())

    def test_api_queue_endpoint(self):
        response = self.client.get("/api/queue")
        self.assertEqual(response.status_code, 200)
        self.assertIn("items", response.json())

    def test_download_sample_csv(self):
        response = self.client.get("/download-sample-csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("target_url,custom_note", response.text)

    def test_scheduler_status_endpoint(self):
        response = self.client.get("/api/scheduler/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("today_date", data)
        self.assertIn("profiles", data)


if __name__ == "__main__":
    unittest.main()

