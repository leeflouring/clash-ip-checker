import asyncio
import copy
import json
import logging
import math
import os
import time
import uuid
from collections import deque

from core.checker_service import CheckerService


ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "cancelled", "error"}
PUBLIC_RESULT_FIELDS = {
    "id",
    "original_name",
    "name",
    "ip",
    "risk",
    "bot",
    "shared",
    "type",
    "native",
    "source",
    "error",
    "degraded",
    "status",
}
RESULT_STATUSES = {"pending", "checked", "failed", "skipped"}
logger = logging.getLogger(__name__)


def save_file_atomic(file_path, content):
    temporary_path = f"{file_path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, file_path)
    except Exception:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise


class JobStatus:
    def __init__(
        self,
        source_key,
        output_key,
        file_path,
        options=None,
        request_id=None,
    ):
        self.job_id = uuid.uuid4().hex
        self.source_key = source_key
        self.output_key = output_key
        self.file_path = file_path
        self.options = dict(options or {})
        self.request_id = request_id
        self.status = "queued"
        self.total = 0
        self.current = 0
        self.message = "Waiting..."
        self.label = "任务"
        self.error = None
        self.submit_time = time.time()
        self.finish_time = None
        self.stop_event = asyncio.Event()
        self.logs = deque(maxlen=200)
        self.results = []
        self.lock = asyncio.Lock()
        self.source_document = None
        self.deleted_ids = set()

    async def update_progress(self, current, total, message, result=None):
        if self.status in TERMINAL_STATUSES:
            return
        self.status = "running"
        self.current = current
        self.total = total
        self.message = str(message)[:500]
        self.logs.append(self.message)
        if result is not None:
            result = dict(result)
            node_id = result.get("id")
            # Workspace rows are seeded in original proxy order. Check identity
            # before using that position so sparse/reordered rows still work.
            if (
                isinstance(node_id, int)
                and 0 <= node_id < len(self.results)
                and self.results[node_id].get("id") == node_id
            ):
                self.results[node_id] = result
                return
            for index, previous in enumerate(self.results):
                if previous.get("id") == node_id:
                    self.results[index] = result
                    break
            else:
                self.results.append(result)

    async def complete(self, cached=False):
        if self.status == "cancelled":
            return
        self.status = "completed"
        self.finish_time = time.time()
        self.message = "Result loaded from cache" if cached else "Done"
        self.logs.append(self.message)

    async def cancel(self):
        if self.status in TERMINAL_STATUSES:
            return
        self.stop_event.set()
        self.status = "cancelled"
        self.finish_time = time.time()
        self.message = "Cancelled by user"
        self.logs.append(self.message)

    def fail(self, error):
        if self.status == "cancelled":
            return
        self.status = "error"
        self.error = str(error)[:500]
        self.finish_time = time.time()
        self.message = f"Error: {self.error}"
        self.logs.append(self.message)

    def consume_logs(self):
        messages = list(self.logs)
        self.logs.clear()
        return messages

    def snapshot(self):
        return {
            "job_id": self.job_id,
            "status": self.status,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "label": self.label,
            "error": self.error,
            "submit_time": self.submit_time,
            "finish_time": self.finish_time,
            "results": [dict(result) for result in self.results],
            "output_key": self.output_key,
        }


class JobManager:
    def __init__(
        self,
        checker_service,
        max_queue_size=10,
        ttl=3600,
        max_jobs=100,
        history_path=None,
    ):
        self.checker = checker_service
        self.max_queue_size = max(1, int(max_queue_size))
        self.queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.jobs_by_id = {}
        self.latest_by_source = {}
        self.active_by_output = {}
        self.user_active_tasks = {}
        self.submit_lock = asyncio.Lock()
        self.worker_task = None
        self.running_job_id = None
        self.execution_lock = asyncio.Lock()
        self.ttl = max(0, int(ttl))
        self.max_jobs = max(1, int(max_jobs))
        self.history_path = history_path
        self.history_lock = asyncio.Lock()
        self.history = self._load_history()

    @staticmethod
    def _sanitize_history_result(row):
        if not isinstance(row, dict):
            return None
        row_id = row.get("id")
        status = row.get("status")
        if (
            not isinstance(row_id, int)
            or isinstance(row_id, bool)
            or row_id < 0
            or status not in RESULT_STATUSES
        ):
            return None
        sanitized = {"id": row_id, "status": status}
        for key, value in row.items():
            if key not in PUBLIC_RESULT_FIELDS or key in sanitized:
                continue
            if key == "degraded":
                if isinstance(value, bool):
                    sanitized[key] = value
            elif isinstance(value, str):
                sanitized[key] = (
                    CheckerService._public_error(value)
                    if key == "error"
                    else value[:500]
                )
        return sanitized

    @staticmethod
    def _sanitize_history_record(record):
        if not isinstance(record, dict):
            return None
        job_id = record.get("job_id")
        status = record.get("status")
        try:
            parsed_job_id = uuid.UUID(job_id)
            valid_job_id = (
                isinstance(job_id, str)
                and len(job_id) == 32
                and parsed_job_id.hex == job_id
                and parsed_job_id.version == 4
            )
        except (ValueError, AttributeError, TypeError):
            valid_job_id = False
        current = record.get("current")
        total = record.get("total")
        submit_time = record.get("submit_time")
        finish_time = record.get("finish_time")
        message = record.get("message")
        label = record.get("label")
        if (
            not valid_job_id
            or status not in TERMINAL_STATUSES
            or not isinstance(current, int)
            or isinstance(current, bool)
            or current < 0
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or not isinstance(submit_time, (int, float))
            or isinstance(submit_time, bool)
            or not math.isfinite(submit_time)
            or not isinstance(finish_time, (int, float))
            or isinstance(finish_time, bool)
            or not math.isfinite(finish_time)
            or not isinstance(message, str)
            or not isinstance(label, str)
        ):
            return None
        results = record.get("results")
        if not isinstance(results, list):
            return None
        return {
            "job_id": job_id,
            "status": status,
            "current": current,
            "total": total,
            "message": message[:500],
            "label": label[:500],
            "submit_time": submit_time,
            "finish_time": finish_time,
            "results": [
                result
                for row in results
                if (result := JobManager._sanitize_history_result(row)) is not None
            ],
        }

    def _load_history(self):
        if not self.history_path:
            return []
        try:
            with open(self.history_path, "r", encoding="utf-8") as source:
                payload = json.load(source)
            records = payload.get("records") if isinstance(payload, dict) else payload
            if not isinstance(records, list):
                return []
            sanitized = [
                record
                for item in records
                if (record := self._sanitize_history_record(item)) is not None
            ]
            return sorted(
                sanitized,
                key=lambda item: item.get("finish_time") or 0,
                reverse=True,
            )[: self.max_jobs]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Unable to load job history")
            return []

    @staticmethod
    def _history_record(job):
        message = "Detection failed" if job.status == "error" else job.message
        return JobManager._sanitize_history_record(
            {
                "job_id": job.job_id,
                "status": job.status,
                "current": job.current,
                "total": job.total,
                "message": message,
                "label": job.label,
                "submit_time": job.submit_time,
                "finish_time": job.finish_time,
                "results": job.results,
            }
        )

    async def record_history(self, job):
        if job.status not in TERMINAL_STATUSES:
            return
        try:
            record = self._history_record(job)
            if record is None:
                return
            async with self.history_lock:
                history = [
                    item for item in self.history if item["job_id"] != job.job_id
                ]
                history.append(record)
                history.sort(
                    key=lambda item: item.get("finish_time") or 0,
                    reverse=True,
                )
                history = history[: self.max_jobs]
                if self.history_path:
                    content = json.dumps(
                        {"records": history},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    await asyncio.to_thread(
                        save_file_atomic,
                        self.history_path,
                        content,
                    )
                self.history = history
        except Exception as error:
            logger.warning(
                "Unable to persist job history: %s",
                type(error).__name__,
            )

    async def list_history(self):
        async with self.history_lock:
            return copy.deepcopy(self.history)

    async def get_history(self, job_id):
        async with self.history_lock:
            record = next(
                (item for item in self.history if item["job_id"] == job_id),
                None,
            )
            return copy.deepcopy(record)

    async def delete_history(self, job_id):
        async with self.history_lock:
            history = [
                item for item in self.history if item["job_id"] != job_id
            ]
            if len(history) == len(self.history):
                return None
            try:
                if self.history_path:
                    content = json.dumps(
                        {"records": history},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    await asyncio.to_thread(
                        save_file_atomic,
                        self.history_path,
                        content,
                    )
                self.history = history
                return True
            except Exception as error:
                logger.warning(
                    "Unable to persist job history deletion: %s",
                    type(error).__name__,
                )
                return False

    async def start_worker(self):
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker_loop())

    async def stop_worker(self):
        if not self.worker_task:
            return
        self.worker_task.cancel()
        try:
            await self.worker_task
        except asyncio.CancelledError:
            pass
        self.worker_task = None

    def _get_job(self, source_key=None, job_id=None):
        if job_id:
            return self.jobs_by_id.get(job_id)
        if source_key:
            return self.jobs_by_id.get(self.latest_by_source.get(source_key))
        return None

    def get_job(self, source_key=None, job_id=None):
        self._cleanup()
        return self._get_job(source_key, job_id)

    def get_active_by_output(self, output_key):
        job = self.jobs_by_id.get(self.active_by_output.get(output_key))
        return job if job and job.status in ACTIVE_STATUSES else None

    async def register_completed(self, source_key, output_key, file_path, options=None):
        job = JobStatus(source_key, output_key, file_path, options)
        await job.complete(cached=True)
        self.jobs_by_id[job.job_id] = job
        self.latest_by_source[source_key] = job.job_id
        self._cleanup()
        await self.record_history(job)
        return job

    async def submit_job(
        self,
        source_key,
        output_key,
        file_path,
        user_ip=None,
        options=None,
        request_id=None,
        admission_limit=None,
        max_age=0,
        file_content=None,
    ):
        options = dict(options or {})
        async with self.submit_lock:
            self._cleanup()

            existing = self.get_active_by_output(output_key)
            if existing and (not request_id or existing.request_id == request_id):
                self.latest_by_source[source_key] = existing.job_id
                if user_ip:
                    self.user_active_tasks[user_ip] = existing.job_id
                return existing

            if max_age > 0:
                now = time.time()
                cached = max(
                    (
                        job
                        for job in self.jobs_by_id.values()
                        if job.output_key == output_key
                        and job.status == "completed"
                        and job.finish_time is not None
                        and job.source_document is not None
                        and now - job.finish_time <= max_age
                    ),
                    key=lambda job: job.finish_time,
                    default=None,
                )
                if cached:
                    self.latest_by_source[source_key] = cached.job_id
                    return cached

            limit = self.max_queue_size
            if admission_limit is not None:
                limit = min(limit, max(1, int(admission_limit)))
            active_count = self.queue.qsize() + (1 if self.running_job_id else 0)
            if self.queue.full() or active_count >= limit:
                raise ValueError("服务器繁忙，请稍后重试。当前检测队列已满。")

            if existing:
                await existing.cancel()
                await self.record_history(existing)

            if user_ip:
                previous = self.jobs_by_id.get(self.user_active_tasks.get(user_ip))
                if previous and previous.status in ACTIVE_STATUSES:
                    await previous.cancel()
                    await self.record_history(previous)

            if file_content is not None:
                await asyncio.to_thread(
                    save_file_atomic,
                    file_path,
                    file_content,
                )

            job = JobStatus(
                source_key,
                output_key,
                file_path,
                options,
                request_id,
            )
            self.jobs_by_id[job.job_id] = job
            self.latest_by_source[source_key] = job.job_id
            self.active_by_output[output_key] = job.job_id
            if user_ip:
                self.user_active_tasks[user_ip] = job.job_id
            self.queue.put_nowait(job)
            return job

    async def cancel_job(self, source_key=None, request_id=None, job_id=None):
        job = self._get_job(source_key, job_id)
        if not job or (request_id and job.request_id != request_id):
            return False
        await job.cancel()
        await self.record_history(job)
        return True

    def get_status(self, source_key=None, job_id=None):
        job = self.get_job(source_key, job_id)
        return job.snapshot() if job else {"status": "unknown"}

    def get_queue_info(self):
        return {
            "queue_size": self.queue.qsize(),
            "running_job": self.running_job_id,
        }

    def _remove_job(self, job):
        self.jobs_by_id.pop(job.job_id, None)
        if self.latest_by_source.get(job.source_key) == job.job_id:
            self.latest_by_source.pop(job.source_key, None)
        if self.active_by_output.get(job.output_key) == job.job_id:
            self.active_by_output.pop(job.output_key, None)
        for user_ip, job_id in list(self.user_active_tasks.items()):
            if job_id == job.job_id:
                self.user_active_tasks.pop(user_ip, None)
        if not any(
            other.output_key == job.output_key
            for other in self.jobs_by_id.values()
        ):
            try:
                os.remove(job.file_path)
            except FileNotFoundError:
                pass

    def _cleanup(self):
        now = time.time()
        expired = [
            job
            for job in self.jobs_by_id.values()
            if job.finish_time is not None
            and self.ttl >= 0
            and now - job.finish_time > self.ttl
        ]
        for job in expired:
            self._remove_job(job)

        excess = len(self.jobs_by_id) - self.max_jobs
        if excess <= 0:
            return
        terminal = sorted(
            (
                job
                for job in self.jobs_by_id.values()
                if job.status in TERMINAL_STATUSES
            ),
            key=lambda job: job.finish_time or job.submit_time,
        )
        for job in terminal[:excess]:
            self._remove_job(job)

    async def _worker_loop(self):
        while True:
            job = await self.queue.get()
            self.running_job_id = job.job_id
            try:
                if job.status == "cancelled":
                    continue

                async def progress_callback(current, total, message, result=None):
                    await job.update_progress(current, total, message, result)

                async with self.execution_lock:
                    await self.checker.run_check(job.file_path, progress_cb=progress_callback, options=job.options, stop_event=job.stop_event)
                if not job.stop_event.is_set():
                    await job.complete()
            except Exception as error:
                job.fail(error)
            finally:
                await self.record_history(job)
                if self.active_by_output.get(job.output_key) == job.job_id:
                    self.active_by_output.pop(job.output_key, None)
                for user_ip, job_id in list(self.user_active_tasks.items()):
                    if job_id == job.job_id:
                        self.user_active_tasks.pop(user_ip, None)
                self.running_job_id = None
                self.queue.task_done()
