import asyncio
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

import yaml
from fastapi import HTTPException

from core.checker_service import CheckerService
from core.job_manager import JobManager, JobStatus
from core.sources.ipquery import IPQuerySource
from main import (
    build_output_key,
    fetch_subscription,
    is_valid_clash,
    job_events,
    normalize_options,
    seed_results,
    validate_subscription_url,
)


def public_resolver(host, port, **kwargs):
    address = "127.0.0.1" if host == "private.test" else "93.184.216.34"
    return [(2, 1, 6, "", (address, port))]


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = headers or {}
        self.chunks = list(chunks or [])
        self.closed = False

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, size):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


class FakeChecker:
    async def run_check(self, path, progress_cb=None, options=None, stop_event=None):
        if progress_cb:
            await progress_cb(
                1,
                1,
                "ok",
                {
                    "id": 0,
                    "name": "node",
                    "original_name": "node",
                    "status": "checked",
                },
            )


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_seeded_progress_lookup_scales_linearly(self):
        class CountedRows(list):
            visits = 0

            def __iter__(self):
                for row in super().__iter__():
                    self.visits += 1
                    yield row

            def __getitem__(self, index):
                self.visits += 1
                return super().__getitem__(index)

        count = 1000
        job = JobStatus("source", "output", "config.yaml")
        job.results = CountedRows(
            seed_results([{"name": f"node-{index}"} for index in range(count)])
        )
        for node_id in range(count):
            await job.update_progress(
                node_id + 1,
                count,
                "checked",
                {"id": node_id, "name": f"checked-{node_id}", "status": "checked"},
            )

        # A linear scan visits 500,500 rows for this same workload.
        self.assertLessEqual(job.results.visits, count * 2)
        self.assertEqual(len(job.results), count)
        self.assertEqual(job.results[-1]["name"], "checked-999")

    async def test_progress_preserves_sparse_ids_and_snapshot_isolation(self):
        job = JobStatus("source", "output", "config.yaml")
        job.results = [
            {"id": 2, "name": "two"},
            {"id": 0, "name": "zero"},
            {"id": 9, "name": "nine"},
        ]
        before = job.snapshot()
        result = {"id": 0, "name": "updated"}
        await job.update_progress(1, 3, "updated", result)
        result["name"] = "caller mutation"
        await job.update_progress(2, 3, "updated", {"id": 9, "name": "updated nine"})
        await job.update_progress(3, 3, "new", {"id": 12, "name": "twelve"})

        self.assertEqual([row["id"] for row in job.results], [2, 0, 9, 12])
        self.assertEqual(job.results[1]["name"], "updated")
        self.assertEqual(before["results"][1]["name"], "zero")
        snapshot = job.snapshot()
        snapshot["results"][0]["name"] = "snapshot mutation"
        self.assertEqual(job.results[0]["name"], "two")

    async def test_duplicate_reuse_and_replacement_are_isolated(self):
        manager = JobManager(FakeChecker(), max_queue_size=3)
        first = await manager.submit_job(
            "source",
            "output",
            "config.yaml",
            request_id="first",
        )
        duplicate = await manager.submit_job(
            "source",
            "output",
            "config.yaml",
            request_id="first",
        )
        replacement = await manager.submit_job(
            "source",
            "output",
            "config.yaml",
            request_id="second",
        )

        self.assertIs(first, duplicate)
        self.assertEqual(first.status, "cancelled")
        self.assertNotEqual(first.job_id, replacement.job_id)
        self.assertEqual(
            manager.get_status(source_key="source")["job_id"],
            replacement.job_id,
        )

    async def test_queue_admission_is_bounded(self):
        manager = JobManager(FakeChecker(), max_queue_size=1)
        await manager.submit_job("source-a", "output-a", "a.yaml")
        with self.assertRaises(ValueError):
            await manager.submit_job("source-b", "output-b", "b.yaml")

    async def test_worker_completes_with_structured_result(self):
        with tempfile.TemporaryDirectory() as directory:
            history_path = os.path.join(directory, "history.json")
            manager = JobManager(FakeChecker(), history_path=history_path)
            await manager.start_worker()
            self.addAsyncCleanup(manager.stop_worker)
            job = await manager.submit_job("source", "output", "config.yaml")

            await asyncio.wait_for(manager.queue.join(), timeout=1)

            self.assertEqual(job.status, "completed")
            self.assertEqual(job.results[0]["id"], 0)
            self.assertEqual(manager.running_job_id, None)
            restored = JobManager(FakeChecker(), history_path=history_path)
            self.assertEqual(
                (await restored.get_history(job.job_id))["status"],
                "completed",
            )

    async def test_cancelled_job_ignores_late_progress(self):
        manager = JobManager(FakeChecker())
        job = await manager.submit_job("source", "output", "config.yaml")
        await job.cancel()
        snapshot = job.snapshot()

        await job.update_progress(
            1,
            1,
            "late",
            {"id": 0, "status": "checked"},
        )

        self.assertEqual(job.snapshot(), snapshot)

    async def test_history_round_trip_privacy_and_delete(self):
        secrets = {
            "url": "https://unique-source.example/private/subscription",
            "token": "unique-api-token",
            "raw": "raw-yaml-unique",
            "password": "unique-proxy-password",
        }
        with tempfile.TemporaryDirectory() as directory:
            history_path = os.path.join(directory, "history.json")
            manager = JobManager(FakeChecker(), history_path=history_path)
            job = await manager.submit_job(
                secrets["url"],
                "output",
                os.path.join(directory, "config.yaml"),
                options={"api_token": secrets["token"]},
                request_id=secrets["raw"],
            )
            job.label = "unique-source.example / … / subscription"
            job.source_document = {
                "raw": secrets["raw"],
                "proxies": [{"password": secrets["password"]}],
            }
            job.current = 1
            job.total = 1
            job.results = [
                {
                    "id": 0,
                    "original_name": "node",
                    "name": "node",
                    "status": "checked",
                    "ip": "203.0.113.1",
                    "error": (
                        f"{secrets['url']} token={secrets['token']} "
                        f"password={secrets['password']}"
                    ),
                    "ignored_secret": secrets["password"],
                }
            ]
            await manager.record_history(job)
            self.assertFalse(os.path.exists(history_path))
            job.fail(
                f"{secrets['url']} token={secrets['token']}"
            )
            await manager.record_history(job)

            with open(history_path, "r", encoding="utf-8") as source:
                serialized = source.read()
            for secret in secrets.values():
                self.assertNotIn(secret, serialized)

            restored = JobManager(FakeChecker(), history_path=history_path)
            record = await restored.get_history(job.job_id)
            self.assertEqual(record["results"][0]["status"], "checked")
            self.assertNotIn("ignored_secret", record["results"][0])
            self.assertTrue(await restored.delete_history(job.job_id))
            self.assertIsNone(
                await JobManager(
                    FakeChecker(),
                    history_path=history_path,
                ).get_history(job.job_id)
            )

    async def test_history_is_capped_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            history_path = os.path.join(directory, "history.json")
            manager = JobManager(
                FakeChecker(),
                max_jobs=2,
                history_path=history_path,
            )
            jobs = []
            for index in range(3):
                job = await manager.submit_job(
                    f"source-{index}",
                    f"output-{index}",
                    f"{index}.yaml",
                )
                await job.complete()
                job.finish_time = float(index + 1)
                await manager.record_history(job)
                jobs.append(job)

            records = await manager.list_history()
            self.assertEqual(
                [record["job_id"] for record in records],
                [jobs[2].job_id, jobs[1].job_id],
            )

    async def test_corrupt_history_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            history_path = os.path.join(directory, "history.json")
            with open(history_path, "w", encoding="utf-8") as output:
                output.write("{not json")
            manager = JobManager(FakeChecker(), history_path=history_path)
            self.assertEqual(await manager.list_history(), [])

    async def test_history_rejects_untrusted_ids_and_types(self):
        valid_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as directory:
            history_path = os.path.join(directory, "history.json")
            with open(history_path, "w", encoding="utf-8") as output:
                json.dump(
                    {
                        "records": [
                            {
                                "job_id": "../../unsafe",
                                "status": "completed",
                                "current": 1,
                                "total": 1,
                                "message": "Done",
                                "label": "bad id",
                                "submit_time": 1,
                                "finish_time": 3,
                                "results": [],
                            },
                            {
                                "job_id": valid_id,
                                "status": "completed",
                                "current": 1,
                                "total": 1,
                                "message": "Done",
                                "label": "safe",
                                "submit_time": 1,
                                "finish_time": 2,
                                "results": [
                                    {
                                        "id": {"unsafe": True},
                                        "status": "checked",
                                        "name": "<img src=x onerror=alert(1)>",
                                    },
                                    {
                                        "id": 0,
                                        "status": "checked",
                                        "name": "node",
                                        "error": {"unsafe": True},
                                    },
                                ],
                            },
                        ]
                    },
                    output,
                )

            records = await JobManager(
                FakeChecker(),
                history_path=history_path,
            ).list_history()

        self.assertEqual([record["job_id"] for record in records], [valid_id])
        self.assertEqual(records[0]["results"], [{"id": 0, "status": "checked", "name": "node"}])

    async def test_history_write_failure_does_not_change_worker_status(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(
                FakeChecker(),
                history_path=os.path.join(directory, "history.json"),
            )
            await manager.start_worker()
            self.addAsyncCleanup(manager.stop_worker)
            with patch(
                "core.job_manager.save_file_atomic",
                side_effect=OSError("read-only"),
            ):
                job = await manager.submit_job(
                    "source",
                    "output",
                    "config.yaml",
                )
                await asyncio.wait_for(manager.queue.join(), timeout=1)

            self.assertEqual(job.status, "completed")
            self.assertEqual(await manager.list_history(), [])


class JobEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_snapshots_only_send_heartbeat_and_changes(self):
        job = JobStatus("source", "output", "config.yaml")
        job.results = [{"id": 0, "status": "pending"}]
        polls = 0

        async def advance(interval):
            nonlocal polls
            self.assertEqual(interval, 0.5)
            polls += 1
            if polls == 1:
                job.status = "running"
            elif polls == 2:
                # Rows can change in place without a progress/status change.
                job.results[0]["risk"] = "12%"
            elif polls == 40:
                job.results[0]["status"] = "checked"
                await job.complete()

        with (
            patch("main._job_or_404", return_value=job),
            patch("main.time", Mock(monotonic=lambda: polls * 0.5)),
            patch("main.asyncio.sleep", new=advance),
            patch("main.json.dumps", wraps=json.dumps) as serialize,
        ):
            response = await job_events(job.job_id)
            events = [event async for event in response.body_iterator]

        data = [json.loads(event[6:]) for event in events if event.startswith("data: ")]
        self.assertEqual(
            [event["status"] for event in data],
            ["queued", "running", "running", "completed"],
        )
        self.assertNotIn("risk", data[1]["results"][0])
        self.assertEqual(data[2]["results"][0]["risk"], "12%")
        self.assertEqual(data[-1]["results"][0]["status"], "checked")
        self.assertEqual(data[0]["results"][0]["status"], "pending")
        self.assertEqual(serialize.call_count, 4)
        self.assertEqual(events.count(": keep-alive\n\n"), 1)
        self.assertEqual(response.headers["x-accel-buffering"], "no")

    async def test_terminal_transition_while_sending_is_not_lost(self):
        for terminal in ("completed", "cancelled", "error"):
            with self.subTest(terminal=terminal):
                job = JobStatus("source", "output", "config.yaml")
                with (
                    patch("main._job_or_404", return_value=job),
                    patch("main.asyncio.sleep", new=AsyncMock()),
                ):
                    response = await job_events(job.job_id)
                    stream = response.body_iterator
                    initial = json.loads((await anext(stream))[6:])
                    self.assertEqual(initial["status"], "queued")
                    # The server can finish a job while a slow client receives
                    # the previous frame. Its terminal frame must precede EOF.
                    job.status = terminal
                    final = json.loads((await anext(stream))[6:])
                    self.assertEqual(final["status"], terminal)
                    with self.assertRaises(StopAsyncIteration):
                        await anext(stream)

                    # Every reconnect starts with a full current snapshot.
                    reconnect = await job_events(job.job_id)
                    frames = [event async for event in reconnect.body_iterator]
                    self.assertEqual(len(frames), 1)
                    self.assertEqual(json.loads(frames[0][6:]), job.snapshot())

    async def test_unknown_job_fails_before_stream_headers(self):
        with patch("main.job_manager.get_job", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await job_events("missing")
        self.assertEqual(raised.exception.status_code, 404)

    async def test_retention_cleanup_does_not_drop_connected_terminal_frame(self):
        manager = JobManager(FakeChecker(), ttl=0)
        job = JobStatus("source", "output", "missing-event-test.yaml")
        manager.jobs_by_id[job.job_id] = job
        with (
            patch("main.job_manager", manager),
            patch("main.asyncio.sleep", new=AsyncMock()),
        ):
            response = await job_events(job.job_id)
            stream = response.body_iterator
            self.assertEqual(json.loads((await anext(stream))[6:])["status"], "queued")
            await job.complete()
            with (
                patch("core.job_manager.time.time", return_value=job.finish_time + 1),
                patch("core.job_manager.os.remove"),
            ):
                self.assertIsNone(manager.get_job(job_id=job.job_id))

            final = json.loads((await anext(stream))[6:])
            self.assertEqual(final, job.snapshot())
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)


class FetchAndValidationTests(unittest.TestCase):
    def test_output_key_includes_normalized_options(self):
        content = b"proxies:\n- name: node\n"
        ping0 = normalize_options(source="ping0")
        ippure = normalize_options(source="ippure")
        self.assertNotEqual(
            build_output_key(content, ping0),
            build_output_key(content, ippure),
        )
        self.assertEqual(
            build_output_key(content, ping0),
            build_output_key(content, normalize_options(source="ping0")),
        )

    def test_url_validation_rejects_credentials_and_private_addresses(self):
        with self.assertRaises(ValueError):
            validate_subscription_url(
                "https://user:secret@example.test/sub",
                resolver=public_resolver,
            )
        with self.assertRaises(ValueError):
            validate_subscription_url(
                "http://private.test/sub",
                resolver=public_resolver,
            )

    def test_fetch_validates_redirects_and_caps_size(self):
        responses = iter(
            [
                FakeResponse(302, {"Location": "https://next.test/sub"}),
                FakeResponse(200, chunks=[b"abc"]),
            ]
        )
        content = fetch_subscription(
            "https://start.test/sub",
            opener=lambda *args, **kwargs: (None, next(responses)),
            resolver=public_resolver,
        )
        self.assertEqual(content, b"abc")

        unsafe_redirect = iter(
            [FakeResponse(302, {"Location": "http://private.test/sub"})]
        )
        with self.assertRaises(ValueError):
            fetch_subscription(
                "https://start.test/sub",
                opener=lambda *args, **kwargs: (None, next(unsafe_redirect)),
                resolver=public_resolver,
            )

        with self.assertRaises(ValueError):
            fetch_subscription(
                "https://start.test/sub",
                max_bytes=3,
                opener=lambda *args, **kwargs: (
                    None,
                    FakeResponse(200, chunks=[b"abcd"]),
                ),
                resolver=public_resolver,
            )

    def test_fetch_pins_validated_address_and_enforces_deadline(self):
        seen = []

        def opener(parsed, address, port, timeout, headers):
            seen.append(address)
            return None, FakeResponse(200, chunks=[b"ok"])

        self.assertEqual(
            fetch_subscription(
                "https://start.test/sub",
                opener=opener,
                resolver=public_resolver,
            ),
            b"ok",
        )
        self.assertEqual(seen, ["93.184.216.34"])

        ticks = iter([0, 0, 2])
        with self.assertRaises(ValueError):
            fetch_subscription(
                "https://start.test/sub",
                timeout=1,
                opener=lambda *args, **kwargs: (
                    None,
                    FakeResponse(200, chunks=[b"late"]),
                ),
                resolver=public_resolver,
                clock=lambda: next(ticks),
            )

    def test_clash_yaml_requires_named_proxies(self):
        self.assertTrue(is_valid_clash(b"proxies:\n- name: node\n"))
        self.assertFalse(is_valid_clash(b"proxies: []\n"))
        self.assertFalse(is_valid_clash(b"proxies:\n- type: ss\n"))


class FakeClash:
    async def version(self):
        return True

    async def load_config(self, path):
        return True

    async def update_ports(self, port):
        return True

    async def set_mode_global(self):
        return True

    async def get_mixed_port(self):
        return 7890

    async def get_proxies(self):
        return {"GLOBAL": {}}

    async def switch_proxy(self, name):
        return True


class CheckerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ipquery_accepts_only_complete_public_risk_data(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "ip": "8.8.8.8",
            "risk": {
                "risk_score": 12,
                "is_mobile": False,
                "is_vpn": True,
                "is_tor": False,
                "is_proxy": True,
                "is_datacenter": True,
            },
        }
        session = AsyncMock()
        session.__aenter__.return_value.get.return_value = response

        with patch(
            "core.sources.ipquery.AsyncSession",
            return_value=session,
        ):
            result = await IPQuerySource().check(
                "http://127.0.0.1:7890",
                timeout=5,
            )

        self.assertEqual(result["ip"], "8.8.8.8")
        self.assertEqual(result["pure_score"], "12%")
        self.assertEqual(result["ip_attr"], "机房")
        self.assertEqual(result["ip_src"], "未知")
        self.assertEqual(result["source"], "ipquery")
        self.assertIsNone(result["error"])

    async def test_ipquery_rejects_invalid_schema(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "ip": "192.168.1.1",
            "risk": {"risk_score": "12"},
        }
        session = AsyncMock()
        session.__aenter__.return_value.get.return_value = response

        with patch(
            "core.sources.ipquery.AsyncSession",
            return_value=session,
        ):
            result = await IPQuerySource().check(
                "http://127.0.0.1:7890",
                timeout=5,
            )

        self.assertEqual(result["source"], "ipquery")
        self.assertEqual(result["error"], "Invalid IPQuery response")

    async def test_ipquery_is_last_and_requires_fallback(self):
        calls = []

        def source(name, result):
            mock = Mock()

            async def check(*args, **kwargs):
                calls.append(name)
                return result

            mock.check = AsyncMock(side_effect=check)
            return mock

        ping0 = source("ping0", None)
        ippure = source("ippure", {"error": "failed"})
        ipquery = source(
            "ipquery",
            {
                "ip": "8.8.8.8",
                "pure_score": "12%",
                "ip_attr": "机房",
                "ip_src": "未知",
                "source": "ipquery",
                "error": None,
            },
        )
        with (
            patch("core.sources.ping0.Ping0Source", return_value=ping0),
            patch("core.sources.ippure.IPPureSource", return_value=ippure),
            patch("core.sources.ipquery.IPQuerySource", return_value=ipquery),
        ):
            service = CheckerService()
            result = await service._check_ip_fast(
                "http://127.0.0.1:7890",
                {"source": "ping0", "fallback": True},
            )
            self.assertEqual(calls, ["ping0", "ippure", "ipquery"])
            self.assertEqual(result["source"], "ipquery")

            calls.clear()
            result = await service._check_ip_fast(
                "http://127.0.0.1:7890",
                {"source": "ping0", "fallback": False},
            )
            self.assertEqual(calls, ["ping0"])
            self.assertEqual(result["source"], "unknown")

            calls.clear()
            result = await service._check_ip_fast(
                "http://127.0.0.1:7890",
                {"source": "ipquery", "fallback": False},
            )
            self.assertEqual(calls, ["ping0"])
            self.assertEqual(result["source"], "unknown")

    async def test_fast_check_rejects_partial_success(self):
        partial = Mock()
        partial.check = AsyncMock(
            return_value={
                "ip": "8.8.8.8",
                "pure_score": "?",
                "ip_attr": "未知",
                "ip_src": "未知",
                "error": None,
            }
        )
        with patch(
            "core.sources.ping0.Ping0Source",
            return_value=partial,
        ):
            result = await CheckerService()._check_ip_fast(
                "http://127.0.0.1:7890",
                {"source": "ping0", "fallback": False},
            )

        self.assertEqual(result["source"], "unknown")
        self.assertIn("Incomplete response", result["error"])

    def test_public_result_sanitizes_and_limits_error(self):
        result = CheckerService._public_result(
            0,
            "node",
            "node",
            "failed",
            {
                "source": "ping0",
                "error": (
                    "TLS failed at https://user:password@example.test/private "
                    "token=top-secret "
                    "Authorization: Bearer bearer-secret "
                    + ("detail " * 80)
                ),
            },
        )

        self.assertLessEqual(len(result["error"]), 240)
        self.assertNotIn("example.test", result["error"])
        self.assertNotIn("top-secret", result["error"])
        self.assertNotIn("bearer-secret", result["error"])
        self.assertTrue(result["error"].endswith("…"))

    async def test_checker_emits_public_result_and_updates_groups(self):
        service = CheckerService()
        service.clash = FakeClash()
        service._check_ip_fast = AsyncMock(
            return_value={
                "ip": "8.8.8.8",
                "pure_score": "10%",
                "pure_emoji": "⚪",
                "ip_attr": "住宅",
                "ip_src": "原生",
                "shared_users": "2",
                "source": "ping0",
                "error": None,
            }
        )
        events = []

        async def progress(current, total, message, result=None):
            if result:
                events.append(result)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.yaml")
            with open(path, "w", encoding="utf-8") as output:
                yaml.safe_dump(
                    {
                        "proxies": [{"name": "node-a", "type": "ss"}],
                        "proxy-groups": [
                            {"name": "group", "proxies": ["node-a", "DIRECT"]}
                        ],
                    },
                    output,
                    allow_unicode=True,
                )

            with patch(
                "core.checker_service.asyncio.sleep",
                new=AsyncMock(),
            ):
                await service.run_check(
                    path,
                    progress_cb=progress,
                    options=normalize_options(source="ping0"),
                )

            with open(path, "r", encoding="utf-8") as source:
                saved = yaml.safe_load(source)

        self.assertEqual(events[0]["status"], "checked")
        self.assertEqual(events[0]["error"], "")
        self.assertEqual(events[0]["risk"], "10%")
        self.assertIn("【", saved["proxies"][0]["name"])
        self.assertEqual(
            saved["proxy-groups"][0]["proxies"][0],
            saved["proxies"][0]["name"],
        )

    async def test_switch_failure_emits_mihomo_error(self):
        service = CheckerService()
        service.clash = FakeClash()
        service.clash.switch_proxy = AsyncMock(return_value=False)
        events = []

        async def progress(current, total, message, result=None):
            if result:
                events.append(result)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.yaml")
            with open(path, "w", encoding="utf-8") as output:
                yaml.safe_dump(
                    {"proxies": [{"name": "node-a", "type": "ss"}]},
                    output,
                )
            with patch(
                "core.checker_service.asyncio.sleep",
                new=AsyncMock(),
            ):
                await service.run_check(
                    path,
                    progress_cb=progress,
                    options=normalize_options(source="ping0"),
                )

        self.assertEqual(events[0]["status"], "failed")
        self.assertEqual(events[0]["source"], "mihomo")
        self.assertEqual(events[0]["error"], "Mihomo 无法切换到该节点")

    async def test_browser_error_falls_back_to_fast_source(self):
        service = CheckerService()
        service.clash = FakeClash()
        service._check_ip_fast = AsyncMock(
            return_value={
                "ip": "8.8.8.8",
                "pure_score": "10%",
                "pure_emoji": "⚪",
                "ip_attr": "住宅",
                "ip_src": "原生",
                "source": "ping0",
                "error": None,
            }
        )
        browser = Mock()
        browser.check = AsyncMock(
            return_value={"source": "browser", "error": "parse failed"}
        )
        browser.stop = AsyncMock()
        events = []

        async def progress(current, total, message, result=None):
            if result:
                events.append(result)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.yaml")
            with open(path, "w", encoding="utf-8") as output:
                yaml.safe_dump(
                    {"proxies": [{"name": "node-a", "type": "ss"}]},
                    output,
                )
            with (
                patch(
                    "core.checker_service.asyncio.sleep",
                    new=AsyncMock(),
                ),
                patch(
                    "core.sources.browser.BrowserSource",
                    return_value=browser,
                ),
            ):
                await service.run_check(
                    path,
                    progress_cb=progress,
                    options={
                        "mode": "browser",
                        "source": "ping0",
                        "fallback": True,
                        "request_timeout": 5,
                    },
                )

                service._check_ip_fast.assert_awaited_once()
                self.assertEqual(events[0]["source"], "ping0")
                self.assertTrue(events[0]["degraded"])
                self.assertEqual(events[0]["status"], "checked")

                service._check_ip_fast.reset_mock()
                events.clear()
                await service.run_check(
                    path,
                    progress_cb=progress,
                    options={
                        "mode": "browser",
                        "source": "ping0",
                        "fallback": False,
                        "request_timeout": 5,
                    },
                )

                service._check_ip_fast.assert_not_awaited()
                self.assertEqual(events[0]["source"], "browser")
                self.assertEqual(events[0]["status"], "failed")

        self.assertEqual(browser.stop.await_count, 2)

    async def test_browser_exception_respects_fallback_setting(self):
        service = CheckerService()
        service.clash = FakeClash()
        service._check_ip_fast = AsyncMock(
            return_value={
                "ip": "8.8.8.8",
                "pure_score": "10%",
                "pure_emoji": "⚪",
                "ip_attr": "住宅",
                "ip_src": "原生",
                "source": "ping0",
                "error": None,
            }
        )
        browser = Mock()
        browser.check = AsyncMock(side_effect=RuntimeError("browser failed"))
        browser.stop = AsyncMock()
        events = []

        async def progress(current, total, message, result=None):
            if result:
                events.append(result)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.yaml")
            with open(path, "w", encoding="utf-8") as output:
                yaml.safe_dump(
                    {"proxies": [{"name": "node-a", "type": "ss"}]},
                    output,
                )
            with (
                patch(
                    "core.checker_service.asyncio.sleep",
                    new=AsyncMock(),
                ),
                patch(
                    "core.sources.browser.BrowserSource",
                    return_value=browser,
                ),
            ):
                await service.run_check(
                    path,
                    progress_cb=progress,
                    options={
                        "mode": "browser",
                        "fallback": True,
                        "request_timeout": 5,
                    },
                )

        service._check_ip_fast.assert_awaited_once()
        self.assertEqual(events[0]["status"], "checked")


if __name__ == "__main__":
    unittest.main()
