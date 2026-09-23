import csv
import io
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import yaml
from fastapi.testclient import TestClient

import main
from core.config import Config
from core.job_manager import JobManager


class WorkspaceChecker:
    def __init__(self):
        self.runs = 0
        self.options = []

    async def run_check(
        self,
        path,
        progress_cb=None,
        options=None,
        stop_event=None,
        target_nodes=None,
    ):
        self.runs += 1
        self.options.append(options)
        with open(path, "r", encoding="utf-8") as source:
            proxies = yaml.safe_load(source)["proxies"]
        candidates = [
            (target_nodes[proxy["name"]] if target_nodes else position, proxy)
            for position, proxy in enumerate(proxies)
            if not target_nodes or proxy["name"] in target_nodes
        ]
        for current, (node_id, proxy) in enumerate(candidates, 1):
            if progress_cb:
                await progress_cb(
                    current,
                    len(candidates),
                    "checked",
                    {
                        "id": node_id,
                        "original_name": proxy["name"],
                        "name": f"{proxy['name']} 【⚪ 住宅|原生】",
                        "ip": f"203.0.113.{node_id + 1}",
                        "risk": "10%",
                        "bot": "N/A",
                        "shared": "2",
                        "type": "住宅",
                        "native": "原生",
                        "source": "ping0",
                        "error": "",
                        "degraded": False,
                        "status": "checked",
                    },
                )


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old_data_dir = main.DATA_DIR
        self.old_manager = main.job_manager
        main.DATA_DIR = self.directory.name
        main.job_manager = JobManager(
            WorkspaceChecker(),
            max_queue_size=3,
            history_path=os.path.join(self.directory.name, "history.json"),
        )
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        main.DATA_DIR = self.old_data_dir
        main.job_manager = self.old_manager
        self.directory.cleanup()

    def wait_for_terminal(self, job_id):
        for _ in range(100):
            snapshot = self.client.get(f"/api/jobs/{job_id}").json()
            if snapshot["status"] in {"completed", "cancelled", "error"}:
                return snapshot
            time.sleep(0.01)
        self.fail("job did not finish")

    def wait_for_history(self, job_id):
        for _ in range(100):
            records = self.client.get("/api/history").json()["records"]
            if any(record["job_id"] == job_id for record in records):
                return records
            time.sleep(0.01)
        self.fail("history record was not persisted")

    def test_workspace_flow_and_local_assets(self):
        source = {
            "proxies": [
                {"name": "node-a", "type": "ss"},
                {"name": "node-b", "type": "ss"},
            ],
            "proxy-groups": [
                {
                    "name": "select",
                    "type": "select",
                    "proxies": ["node-a", "node-b", "DIRECT"],
                }
            ],
        }
        payload = {
            "yaml": yaml.safe_dump(source),
            "options": {
                "mode": "fast",
                "source": "ping0",
                "fallback": True,
                "request_timeout": 5,
                "max_age": 360,
                "headless": True,
            },
        }
        response = self.client.post("/api/jobs", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()["job_id"]
        snapshot = self.wait_for_terminal(job_id)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(len(snapshot["results"]), 2)
        self.assertIn("剩余", main.job_manager.checker.options[0]["skip_keywords"])

        cached = self.client.post("/api/jobs", json=payload)
        self.assertEqual(cached.json()["job_id"], job_id)
        self.assertEqual(main.job_manager.checker.runs, 1)

        changed = {
            **payload,
            "options": {**payload["options"], "source": "ippure"},
        }
        changed_job_id = self.client.post("/api/jobs", json=changed).json()["job_id"]
        self.wait_for_terminal(changed_job_id)
        self.assertNotEqual(changed_job_id, job_id)
        self.assertEqual(main.job_manager.checker.runs, 2)

        renamed = 'renamed, "quoted"'
        response = self.client.put(
            f"/api/jobs/{job_id}/nodes/0",
            json={"name": renamed},
        )
        self.assertEqual(response.status_code, 200, response.text)

        exported = self.client.post(
            f"/api/jobs/{job_id}/export",
            json={"node_ids": [0]},
        ).json()
        csv_rows = list(csv.DictReader(io.StringIO(exported["csv"].lstrip("\ufeff"))))
        self.assertEqual(csv_rows[0]["name"], renamed)
        selected_yaml = yaml.safe_load(exported["yaml"])
        self.assertEqual([proxy["name"] for proxy in selected_yaml["proxies"]], [renamed])
        self.assertEqual(
            selected_yaml["proxy-groups"][0]["proxies"],
            [renamed, "DIRECT"],
        )

        response = self.client.delete(f"/api/jobs/{job_id}/nodes/1")
        self.assertEqual(response.status_code, 200, response.text)
        raw = yaml.safe_load(self.client.get(f"/api/jobs/{job_id}/raw").text)
        self.assertEqual([proxy["name"] for proxy in raw["proxies"]], [renamed])

        response = self.client.post(
            f"/api/jobs/{job_id}/nodes/0/recheck",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], 0)
        self.assertEqual(
            self.client.get(f"/api/jobs/{job_id}").json()["results"][0]["status"],
            "checked",
        )

        self.assertEqual(self.client.get("/").status_code, 200)
        page = self.client.get("/ipcheck")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="history-list"', page.text)
        self.assertIn('id="history-readonly"', page.text)
        self.assertIn('id="return-current"', page.text)
        self.assertIn('id="filter-name"', page.text)
        self.assertIn('id="filter-status"', page.text)
        self.assertIn('id="mobile-results"', page.text)
        self.assertIn('id="mobile-filter-name"', page.text)
        self.assertIn('id="mobile-filter-status"', page.text)
        self.assertIn('id="mobile-sort"', page.text)
        self.assertIn('class="global-bar"', page.text)
        self.assertIn('class="command-deck"', page.text)
        self.assertIn('class="data-plane"', page.text)
        self.assertIn('id="sidebar"', page.text)
        self.assertIn('id="history-count-badge"', page.text)
        self.assertIn(
            'id="tab-table" class="view-tab active" type="button" '
            'role="tab" aria-selected="true" aria-controls="table-view"',
            page.text,
        )
        self.assertIn(
            'id="table-view" class="result-view" role="tabpanel" '
            'aria-labelledby="tab-table"',
            page.text,
        )
        self.assertIn(
            'id="table-wrap" class="table-wrap" role="region" '
            'aria-label="节点检查结果表格，可横向滚动" tabindex="0"',
            page.text,
        )
        self.assertIn(
            '<meta name="color-scheme" content="light">',
            page.text,
        )
        for key in (
            "name",
            "ip",
            "risk",
            "shared",
            "type",
            "native",
            "source",
            "status",
        ):
            self.assertIn(f'data-sort="{key}"', page.text)
            self.assertIn(
                f'data-sort-header="{key}" aria-sort="none"',
                page.text,
            )
        app_script = self.client.get("/static/js/app.js").text
        self.assertNotIn('$("#fallback").disabled = browserMode', app_script)
        self.assertIn('api("/api/history")', app_script)
        self.assertIn("state.historyMode", app_script)
        self.assertIn("setSidebar(false)", app_script)
        self.assertIn("historyCountBadge.textContent", app_script)
        self.assertNotIn("—", page.text + app_script)
        self.assertNotIn("–", page.text + app_script)
        stylesheet = self.client.get("/static/css/style.css")
        self.assertIn(
            "text/css",
            stylesheet.headers["content-type"],
        )
        self.assertIn("color-scheme: light", stylesheet.text)
        self.assertNotIn("background-image: linear-gradient", stylesheet.text)
        self.assertIn("transform: scaleX(var(--progress))", stylesheet.text)
        self.assertNotIn("overflow-x: clip", stylesheet.text)
        self.assertEqual(
            main.mask_subscription_label(
                "https://secret.example/private/token?auth=hidden"
            ),
            "secret.example / …",
        )

    def test_history_list_get_delete_flow(self):
        payload = {
            "yaml": yaml.safe_dump(
                {"proxies": [{"name": "history-node", "type": "ss"}]}
            ),
            "options": {"mode": "fast"},
        }
        response = self.client.post("/api/jobs", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()["job_id"]
        self.wait_for_terminal(job_id)

        records = self.wait_for_history(job_id)
        summary = next(record for record in records if record["job_id"] == job_id)
        self.assertEqual(summary["checked"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertNotIn("results", summary)

        snapshot = self.client.get(f"/api/history/{job_id}")
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        self.assertEqual(snapshot.json()["results"][0]["name"].split()[0], "history-node")

        deleted = self.client.delete(f"/api/history/{job_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get(f"/api/history/{job_id}").status_code, 404)
        self.assertEqual(self.client.get("/api/history").json()["records"], [])

    def test_history_delete_write_failure_is_not_reported_as_missing(self):
        payload = {
            "yaml": yaml.safe_dump(
                {"proxies": [{"name": "history-node", "type": "ss"}]}
            ),
            "options": {"mode": "fast"},
        }
        job_id = self.client.post("/api/jobs", json=payload).json()["job_id"]
        self.wait_for_terminal(job_id)
        self.wait_for_history(job_id)

        with patch(
            "core.job_manager.save_file_atomic",
            side_effect=OSError("read-only"),
        ):
            response = self.client.delete(f"/api/history/{job_id}")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            self.client.get(f"/api/history/{job_id}").status_code,
            200,
        )
        self.assertEqual(
            self.client.delete("/api/history/" + ("f" * 32)).status_code,
            404,
        )

    def test_workspace_rejects_ambiguous_or_invalid_options(self):
        response = self.client.post(
            "/api/jobs",
            json={"url": "https://example.com", "yaml": "proxies: []"},
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/jobs",
            json={
                "yaml": "proxies: []",
                "options": {"mode": "browser", "headless": False},
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_health_token_auth_and_environment_overrides(self):
        with patch.object(
            main.checker_service.clash,
            "version",
            new=AsyncMock(return_value=True),
        ):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mihomo"], "ready")
        with patch.object(
            main.checker_service.clash,
            "version",
            new=AsyncMock(return_value=False),
        ):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)

        with patch.dict(os.environ, {"API_TOKEN": "test-secret"}):
            self.assertEqual(
                self.client.get("/api/jobs/missing").status_code,
                401,
            )
            self.assertEqual(self.client.get("/api/history").status_code, 401)
            self.assertEqual(
                self.client.get("/api/history/" + ("0" * 32)).status_code,
                401,
            )
            self.assertEqual(
                self.client.delete("/api/history/" + ("0" * 32)).status_code,
                401,
            )
            self.assertEqual(self.client.get("/api/config").status_code, 200)
            self.assertEqual(
                self.client.get(
                    "/api/jobs/missing",
                    headers={"Authorization": "Bearer test-secret"},
                ).status_code,
                404,
            )
            self.assertEqual(
                self.client.get(
                    "/api/history",
                    headers={"Authorization": "Bearer test-secret"},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    "/api/history/" + ("0" * 32),
                    headers={"Authorization": "Bearer test-secret"},
                ).status_code,
                404,
            )

        config_path = os.path.join(self.directory.name, "config.yaml")
        with open(config_path, "w", encoding="utf-8") as output:
            output.write("max_queue_size: 2\nfallback: true\n")
        with patch.dict(
            os.environ,
            {
                "CONFIG_PATH": config_path,
                "MAX_QUEUE_SIZE": "7",
                "FALLBACK": "false",
            },
        ):
            loaded = Config()
        self.assertEqual(loaded.max_queue_size, 7)
        self.assertFalse(loaded.fallback)

        with open(config_path, "w", encoding="utf-8") as output:
            output.write(
                'fallback: "false"\n'
                'allow_private_subscriptions: "false"\n'
                'show_advanced_settings: "false"\n'
            )
        with patch.dict(os.environ, {"CONFIG_PATH": config_path}, clear=True):
            loaded = Config()
        self.assertFalse(loaded.fallback)
        self.assertFalse(loaded.allow_private_subscriptions)
        self.assertFalse(loaded.show_advanced_settings)

        response = self.client.post(
            "/api/jobs",
            json={
                "yaml": "proxies:\n- name: node\n",
                "options": {"mode": "magic"},
            },
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
