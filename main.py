import asyncio
import base64
import copy
import csv
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import logging
import os
import socket
import ssl
import sys
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from core.checker_service import CheckerService
from core.config import config
from core.job_manager import JobManager, TERMINAL_STATUSES, save_file_atomic


logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("Main")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_PATH = os.path.join(BASE_DIR, "templates", "index.html")
os.makedirs(DATA_DIR, exist_ok=True)

checker_service = CheckerService(
    api_url=os.getenv("CLASH_API_URL", "http://127.0.0.1:9090")
)
job_manager = JobManager(
    checker_service,
    max_queue_size=config.max_queue_size,
    ttl=config.job_ttl,
    max_jobs=config.max_jobs,
    history_path=os.path.join(DATA_DIR, "history.json"),
)


@asynccontextmanager
async def lifespan(app):
    await job_manager.start_worker()
    try:
        yield
    finally:
        await job_manager.stop_worker()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

PROTECTED_ROUTES = {
    "/api/status",
    "/cancel",
    "/check",
    "/download",
    "/status/stream",
}


@app.middleware("http")
async def require_api_token(request, call_next):
    expected = config.api_token
    path = request.url.path
    protected = (
        path.startswith("/api/jobs")
        or path.startswith("/api/history")
        or path in PROTECTED_ROUTES
    )
    if expected and protected:
        authorization = request.headers.get("Authorization", "")
        supplied = (
            authorization[7:]
            if authorization.lower().startswith("bearer ")
            else ""
        )
        if not supplied or not hmac.compare_digest(supplied, expected):
            return JSONResponse(
                {"detail": "valid Bearer token required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


def hash_value(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def normalize_options(
    skip_keywords=None,
    request_timeout=None,
    source=None,
    fallback=None,
    mode="fast",
    headless=True,
):
    if isinstance(skip_keywords, str):
        skip_keywords = [
            keyword.strip()
            for keyword in skip_keywords.split(",")
            if keyword.strip()
        ]
    elif skip_keywords is None:
        skip_keywords = list(config.skip_keywords)
    else:
        skip_keywords = [str(keyword).strip() for keyword in skip_keywords]
        skip_keywords = [keyword for keyword in skip_keywords if keyword]

    if len(skip_keywords) > 100 or any(len(keyword) > 100 for keyword in skip_keywords):
        raise ValueError("Too many or overly long skip keywords")

    timeout = config.request_timeout if request_timeout is None else int(request_timeout)
    if not 1 <= timeout <= 120:
        raise ValueError("request_timeout must be between 1 and 120 seconds")

    source = source or config.source
    if source not in {"ping0", "ippure"}:
        raise ValueError("source must be ping0 or ippure")

    if fallback is None:
        fallback = config.fallback
    elif not isinstance(fallback, bool):
        raise ValueError("fallback must be true or false")

    if mode not in {"fast", "browser"}:
        raise ValueError("mode must be fast or browser")
    if not isinstance(headless, bool):
        raise ValueError("headless must be true or false")
    if mode == "browser" and headless is not True:
        raise ValueError("browser mode only supports headless=true")

    return {
        "fallback": fallback,
        "request_timeout": timeout,
        "skip_keywords": skip_keywords,
        "source": source,
        "mode": mode,
        "headless": headless,
    }


def canonical_options(options):
    return json.dumps(
        options,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_output_key(content, options):
    return hash_value(content + b"\0" + canonical_options(options))


def source_key(url):
    return hash_value(url)


def map_key(url, options):
    return hash_value(url.encode("utf-8") + b"\0" + canonical_options(options))


def map_path(url, options):
    return os.path.join(DATA_DIR, f"{map_key(url, options)}.map")


def is_in_time_cache(file_path, max_age_seconds=None):
    max_age_seconds = config.max_age if max_age_seconds is None else max_age_seconds
    return os.path.exists(file_path) and (
        time.time() - os.path.getmtime(file_path)
    ) < max_age_seconds


def resolve_subscription_target(
    target_url,
    allow_private=None,
    resolver=socket.getaddrinfo,
):
    parsed = urlsplit(target_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Subscription URL must be a credential-free HTTP(S) URL")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise ValueError("Subscription URL has an invalid port") from error

    if allow_private is None:
        allow_private = config.allow_private_subscriptions

    try:
        resolved = resolver(
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise ValueError("Subscription hostname could not be resolved") from error

    if not resolved:
        raise ValueError("Subscription hostname could not be resolved")
    addresses = [address[4][0].split("%", 1)[0] for address in resolved]
    if not allow_private:
        for address in addresses:
            if not ipaddress.ip_address(address).is_global:
                raise ValueError("Private subscription targets are not allowed")
    return parsed, addresses[0], port


def validate_subscription_url(
    target_url,
    allow_private=None,
    resolver=socket.getaddrinfo,
):
    resolve_subscription_target(target_url, allow_private, resolver)
    return target_url


def open_pinned_response(parsed, address, port, timeout, headers):
    raw_socket = socket.create_connection((address, port), timeout=timeout)
    connection = http.client.HTTPConnection(
        parsed.hostname,
        port,
        timeout=timeout,
    )
    try:
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            raw_socket = context.wrap_socket(
                raw_socket,
                server_hostname=parsed.hostname,
            )
        connection.sock = raw_socket
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request(
            "GET",
            path,
            headers={
                **headers,
                "Accept-Encoding": "identity",
                "Host": parsed.netloc,
            },
        )
        return connection, connection.getresponse()
    except Exception:
        connection.close()
        raw_socket.close()
        raise


def fetch_subscription(
    target_url,
    timeout=None,
    max_bytes=None,
    max_redirects=None,
    allow_private=None,
    resolver=socket.getaddrinfo,
    opener=open_pinned_response,
    clock=time.monotonic,
):
    timeout = config.request_timeout if timeout is None else timeout
    max_bytes = config.max_subscription_bytes if max_bytes is None else max_bytes
    max_redirects = config.max_redirects if max_redirects is None else max_redirects
    headers = {"User-Agent": config.user_agent}
    current_url = target_url
    deadline = clock() + timeout

    for redirect_count in range(max_redirects + 1):
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("Subscription download timed out")
        parsed, address, port = resolve_subscription_target(
            current_url,
            allow_private,
            resolver,
        )
        connection, response = opener(
            parsed,
            address,
            port,
            remaining,
            headers,
        )
        try:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect_count >= max_redirects:
                    raise ValueError("Subscription redirect limit exceeded")
                current_url = urljoin(current_url, location)
                continue
            if response.status >= 400:
                raise ValueError(
                    f"Subscription returned HTTP {response.status}"
                )

            length = response.getheader("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("Subscription exceeds size limit")

            content = bytearray()
            while True:
                if clock() >= deadline:
                    raise ValueError("Subscription download timed out")
                chunk = response.read(64 * 1024)
                if not chunk:
                    return bytes(content)
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ValueError("Subscription exceeds size limit")
        finally:
            response.close()
            if connection:
                connection.close()

    raise ValueError("Subscription redirect limit exceeded")


async def fetch_url_with_retry(target_url, timeout=None):
    return await asyncio.to_thread(
        fetch_subscription,
        target_url,
        timeout,
    )


def load_clash_yaml(data_bytes):
    try:
        data = yaml.safe_load(data_bytes)
    except yaml.YAMLError as error:
        raise ValueError("Invalid YAML") from error
    proxies = data.get("proxies") if isinstance(data, dict) else None
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("Clash YAML must contain a non-empty proxies list")
    if not all(
        isinstance(proxy, dict)
        and isinstance(proxy.get("name"), str)
        and proxy["name"].strip()
        for proxy in proxies
    ):
        raise ValueError("Every proxy must have a non-empty name")
    return data


def is_valid_clash(data_bytes):
    try:
        load_clash_yaml(data_bytes)
        return True
    except ValueError:
        return False


def write_map_atomic(file_path, output_key):
    save_file_atomic(file_path, output_key.encode("ascii"))


def unwrap_subscription_url(url):
    for _ in range(3):
        parsed = urlsplit(url)
        nested = parse_qs(parsed.query).get("url")
        if "/check" not in parsed.path or not nested or not nested[0]:
            break
        url = nested[0]
    return url


def conversion_url(url):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    query.update({"target": ["clash"], "ver": ["meta"], "flag": ["clash"]})
    return urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))


async def fetch_valid_subscription(url, timeout):
    content = await fetch_url_with_retry(url, timeout)
    if is_valid_clash(content):
        return content

    converted = await fetch_url_with_retry(conversion_url(url), timeout)
    if is_valid_clash(converted):
        return converted

    try:
        decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
    except Exception:
        decoded = ""
    if "vmess://" in decoded or "vless://" in decoded:
        raise ValueError(
            "Received a raw node list; use a Clash-target subscription URL"
        )
    raise ValueError("Subscription is not a valid Clash YAML configuration")


@app.get("/", include_in_schema=False)
@app.get("/ipcheck")
async def root():
    return FileResponse(INDEX_PATH)


@app.get("/health")
async def health():
    mihomo_ready = await checker_service.clash.version(timeout=1)
    if not mihomo_ready:
        return JSONResponse(
            {"status": "unhealthy", "mihomo": "unreachable"},
            status_code=503,
        )
    return {"status": "healthy", "mihomo": "ready"}


def _job_or_404(job_id):
    job = job_manager.get_job(job_id=job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return job


def mask_subscription_label(url):
    parsed = urlsplit(url)
    return f"{parsed.hostname} / …"


def seed_results(proxies):
    return [
        {
            "id": node_id,
            "original_name": proxy["name"],
            "name": proxy["name"],
            "ip": "—",
            "risk": "—",
            "bot": "N/A",
            "shared": "N/A",
            "type": "—",
            "native": "—",
            "source": "",
            "error": "",
            "degraded": False,
            "status": "pending",
        }
        for node_id, proxy in enumerate(proxies)
    ]


def build_job_document(job, selected_ids=None):
    source = copy.deepcopy(job.source_document)
    if not isinstance(source, dict):
        with open(job.file_path, "r", encoding="utf-8") as input_file:
            source = yaml.safe_load(input_file)

    rows = {row["id"]: row for row in job.results}
    if selected_ids is None:
        selected_ids = set(rows)
    selected_ids -= job.deleted_ids

    proxies = []
    renamed = {}
    original_proxies = source.get("proxies", [])
    for node_id, proxy in enumerate(original_proxies):
        row = rows.get(node_id)
        if node_id not in selected_ids or not row:
            continue
        proxy = copy.deepcopy(proxy)
        original_name = proxy["name"]
        proxy["name"] = row["name"]
        renamed[original_name] = row["name"]
        proxies.append(proxy)
    source["proxies"] = proxies

    groups = source.get("proxy-groups", [])
    group_names = {
        group.get("name")
        for group in groups
        if isinstance(group, dict) and group.get("name")
    }
    builtins = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("proxies"), list):
            continue
        group["proxies"] = [
            renamed[name] if name in renamed else name
            for name in group["proxies"]
            if name in renamed or name in group_names or name in builtins
        ]
    return source


async def persist_job_document(job):
    document = build_job_document(job)
    content = yaml.safe_dump(
        document,
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")
    await asyncio.to_thread(save_file_atomic, job.file_path, content)


@app.post("/api/jobs")
async def create_job(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError as error:
        raise HTTPException(400, "request body must be JSON") from error
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be an object")

    has_url = isinstance(body.get("url"), str) and bool(body["url"].strip())
    has_yaml = isinstance(body.get("yaml"), str) and bool(body["yaml"].strip())
    if has_url == has_yaml:
        raise HTTPException(400, "provide exactly one non-empty url or yaml")

    opts = body.get("options") or {}
    if not isinstance(opts, dict):
        raise HTTPException(400, "options must be an object")
    try:
        options = normalize_options(
            skip_keywords=opts.get("skip_keywords"),
            request_timeout=opts.get("request_timeout"),
            source=opts.get("source"),
            fallback=opts.get("fallback"),
            mode=opts.get("mode", "fast"),
            headless=opts.get("headless", True),
        )
        max_age = int(opts.get("max_age", config.max_age))
        if not 0 <= max_age <= 604800:
            raise ValueError("max_age must be between 0 and 604800 seconds")
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error

    if has_url:
        url = unwrap_subscription_url(body["url"].strip())
        try:
            content = await fetch_valid_subscription(
                url,
                options["request_timeout"],
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError) as error:
            raise HTTPException(502, "unable to download subscription") from error
        skey = source_key(url)
        label = mask_subscription_label(url)
    else:
        content = body["yaml"].encode("utf-8")
        if len(content) > config.max_subscription_bytes:
            raise HTTPException(413, "YAML exceeds size limit")
        skey = hash_value(content)
        label = "粘贴的 YAML"

    try:
        data = load_clash_yaml(content)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    output = build_output_key(content, options)
    path = os.path.join(DATA_DIR, f"{output}.yaml")
    try:
        job = await job_manager.submit_job(
            skey,
            output,
            path,
            options=options,
            max_age=max_age,
            file_content=content,
        )
    except ValueError as error:
        raise HTTPException(503, str(error)) from error

    job.label = label
    if not job.source_document:
        job.source_document = copy.deepcopy(data)
    if not job.results:
        job.total = len(data["proxies"])
        job.results = seed_results(data["proxies"])

    return {
        "job_id": job.job_id,
        "status": job.status,
        "total": job.total,
        "label": job.label,
    }


@app.get("/api/jobs/{job_id}")
async def job_snapshot(job_id: str):
    return _job_or_404(job_id).snapshot()


def history_summary(record):
    statuses = [
        row.get("status")
        for row in record.get("results", [])
        if isinstance(row, dict)
    ]
    return {
        "job_id": record["job_id"],
        "label": record["label"],
        "status": record["status"],
        "finish_time": record["finish_time"],
        "total": record["total"],
        "checked": statuses.count("checked"),
        "failed": statuses.count("failed"),
        "skipped": statuses.count("skipped"),
    }


@app.get("/api/history")
async def history_list():
    records = await job_manager.list_history()
    return {"records": [history_summary(record) for record in records]}


@app.get("/api/history/{job_id}")
async def history_snapshot(job_id: str):
    record = await job_manager.get_history(job_id)
    if not record:
        raise HTTPException(404, "unknown history record")
    return record


@app.delete("/api/history/{job_id}")
async def history_delete(job_id: str):
    deleted = await job_manager.delete_history(job_id)
    if deleted is None:
        raise HTTPException(404, "unknown history record")
    if not deleted:
        raise HTTPException(500, "failed to persist history deletion")
    return {"status": "deleted", "job_id": job_id}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    async def event_generator():
        while True:
            job = _job_or_404(job_id)
            yield (
                "data: "
                + json.dumps(job.snapshot(), ensure_ascii=False)
                + "\n\n"
            )
            if job.status in TERMINAL_STATUSES:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    cancelled = await job_manager.cancel_job(job_id=job_id)
    return {"status": "cancelled" if cancelled else "not_found_or_ignored"}


def terminal_job(job_id):
    job = _job_or_404(job_id)
    if job.status not in TERMINAL_STATUSES:
        raise HTTPException(409, "job must finish before results can be changed")
    return job


@app.put("/api/jobs/{job_id}/nodes/{node_id}")
async def node_edit(job_id: str, node_id: int, request: Request):
    job = terminal_job(job_id)
    body = await request.json()
    name = str(body.get("name", "")).strip() if isinstance(body, dict) else ""
    if not name:
        raise HTTPException(400, "name is required")
    if len(name) > 200:
        raise HTTPException(400, "name is too long")

    async with job.lock:
        row = next((item for item in job.results if item["id"] == node_id), None)
        if not row:
            raise HTTPException(404, "unknown node")
        if any(
            item["id"] != node_id and item["name"] == name
            for item in job.results
        ):
            raise HTTPException(409, "node name must be unique")
        row["name"] = name
        await persist_job_document(job)
        await job_manager.record_history(job)
        return dict(row)


@app.delete("/api/jobs/{job_id}/nodes/{node_id}")
async def node_delete(job_id: str, node_id: int):
    job = terminal_job(job_id)
    async with job.lock:
        row = next((item for item in job.results if item["id"] == node_id), None)
        if not row:
            raise HTTPException(404, "unknown node")
        job.deleted_ids.add(node_id)
        job.results.remove(row)
        await persist_job_document(job)
        await job_manager.record_history(job)
    return {"status": "deleted", "id": node_id}


@app.post("/api/jobs/{job_id}/nodes/{node_id}/recheck")
async def node_recheck(job_id: str, node_id: int):
    job = terminal_job(job_id)
    async with job.lock:
        row = next((item for item in job.results if item["id"] == node_id), None)
        if not row:
            raise HTTPException(404, "unknown node")
        await persist_job_document(job)
        checked_result = None

        async def collect_result(current, total, message, result=None):
            nonlocal checked_result
            if result is not None:
                checked_result = dict(result)

        async with job_manager.execution_lock:
            await job_manager.checker.run_check(
                job.file_path,
                progress_cb=collect_result,
                options=job.options,
                stop_event=asyncio.Event(),
                target_nodes={row["name"]: node_id},
            )
        if not checked_result:
            raise HTTPException(502, "node check did not return a result")
        checked_result["original_name"] = row["original_name"]
        job.results[job.results.index(row)] = checked_result
        await persist_job_document(job)
        await job_manager.record_history(job)
        return dict(checked_result)


@app.get("/api/jobs/{job_id}/raw")
async def job_raw(job_id: str):
    job = _job_or_404(job_id)
    async with job.lock:
        document = build_job_document(job)
        yaml_text = yaml.safe_dump(
            document,
            allow_unicode=True,
            sort_keys=False,
        )
    return PlainTextResponse(
        yaml_text,
        media_type="application/yaml",
    )


def requested_node_ids(body, job):
    values = body.get("node_ids") if isinstance(body, dict) else None
    if not values:
        return {row["id"] for row in job.results}
    try:
        node_ids = {int(value) for value in values}
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "node_ids must contain integers") from error
    known_ids = {row["id"] for row in job.results}
    if not node_ids <= known_ids:
        raise HTTPException(400, "node_ids contains an unknown node")
    return node_ids


@app.post("/api/jobs/{job_id}/export")
async def job_export(job_id: str, request: Request):
    job = _job_or_404(job_id)
    body = await request.json()
    async with job.lock:
        node_ids = requested_node_ids(body, job)
        document = build_job_document(job, node_ids)
        yaml_text = yaml.safe_dump(
            document,
            allow_unicode=True,
            sort_keys=False,
        )

        fields = [
            "id",
            "name",
            "ip",
            "risk",
            "bot",
            "shared",
            "type",
            "native",
            "source",
            "degraded",
            "status",
        ]
        csv_output = io.StringIO()
        writer = csv.DictWriter(
            csv_output,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in job.results:
            if row["id"] in node_ids:
                writer.writerow(row)
        csv_text = "\ufeff" + csv_output.getvalue()
        suffix = "selected" if len(node_ids) < len(job.results) else "all"
    return {
        "yaml": yaml_text,
        "csv": csv_text,
        "yaml_filename": f"clash-{suffix}.yaml",
        "csv_filename": f"purity-{suffix}.csv",
    }


@app.get("/api/config")
async def get_ui_config():
    return {
        "show_advanced_settings": config.show_advanced_settings,
        "token_required": bool(config.api_token),
    }


@app.get("/api/status")
async def get_status_json(url: str = Query(..., description="Subscription URL")):
    status = job_manager.get_status(source_key=source_key(unwrap_subscription_url(url)))
    queue_info = job_manager.get_queue_info()
    response = {
        "job_status": status,
        "global_queue_size": queue_info["queue_size"],
        "running_job": queue_info["running_job"],
    }
    if status["status"] == "queued":
        response["message"] = (
            f"In queue. Total waiting: {queue_info['queue_size']}"
        )
    return response


@app.get("/check")
async def ip_check(
    request: Request,
    url: str = Query(..., description="Subscription URL"),
    max_queue_size: int = Query(None, description="Max Queue Size"),
    max_age: int = Query(None, description="Max Cache Age"),
    skip_keywords: str = Query(None, description="Skip keywords"),
    request_timeout: int = Query(None, description="Request timeout"),
    source: str = Query(None, description="Primary source"),
    fallback: bool = Query(None, description="Fallback enabled"),
    request_id: str = Query(None, description="Request ID"),
):
    created_file = False
    file_path = None
    try:
        url = unwrap_subscription_url(url)
        options = normalize_options(
            skip_keywords,
            request_timeout,
            source,
            fallback,
        )
        current_max_age = config.max_age if max_age is None else int(max_age)
        if current_max_age < 0:
            raise ValueError("max_age must be zero or greater")
        admission_limit = (
            config.max_queue_size
            if max_queue_size is None
            else max(1, min(int(max_queue_size), config.max_queue_size))
        )

        content = await fetch_valid_subscription(
            url,
            options["request_timeout"],
        )
        output_key = build_output_key(content, options)
        file_path = os.path.join(DATA_DIR, f"{output_key}.yaml")
        existed = os.path.exists(file_path)
        active_job = job_manager.get_active_by_output(output_key)
        new_request = (
            request_id
            and active_job
            and active_job.request_id != request_id
        )

        if existed and (
            is_in_time_cache(file_path, current_max_age) or active_job
        ) and not new_request:
            if not active_job:
                active_job = await job_manager.register_completed(
                    source_key(url),
                    output_key,
                    file_path,
                    options,
                )
            write_map_atomic(map_path(url, options), output_key)
            return FileResponse(
                file_path,
                media_type="application/x-yaml",
                filename="checked.yaml",
                headers={"X-Job-ID": active_job.job_id},
            )

        if not existed:
            await asyncio.to_thread(save_file_atomic, file_path, content)
            created_file = True

        try:
            job = await job_manager.submit_job(
                source_key(url),
                output_key,
                file_path,
                user_ip=request.client.host if request.client else None,
                options=options,
                request_id=request_id,
                admission_limit=admission_limit,
            )
        except ValueError:
            if created_file and os.path.exists(file_path):
                os.remove(file_path)
            if existed:
                return FileResponse(
                    file_path,
                    media_type="application/x-yaml",
                    filename="clash.yaml",
                    headers={"X-QC-Queue-Full": "1"},
                )
            return PlainTextResponse(
                "服务器繁忙，请稍后重试。当前检测队列已满。",
                status_code=503,
                headers={"X-QC-Queue-Full": "1"},
            )

        write_map_atomic(map_path(url, options), output_key)
        return FileResponse(
            file_path,
            media_type="application/x-yaml",
            filename="clash.yaml",
            headers={"X-Job-ID": job.job_id},
        )
    except ValueError as error:
        if created_file and file_path and os.path.exists(file_path):
            os.remove(file_path)
        return PlainTextResponse(str(error), status_code=400)
    except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError):
        return PlainTextResponse(
            "Unable to download the subscription",
            status_code=502,
        )
    except Exception as error:
        logger.error("Subscription processing failed: %s", type(error).__name__)
        return PlainTextResponse("Internal server error", status_code=500)


@app.post("/cancel")
async def cancel_check(
    url: str = Query(..., description="Subscription URL"),
    request_id: str = Query(None),
):
    success = await job_manager.cancel_job(
        source_key=source_key(unwrap_subscription_url(url)),
        request_id=request_id,
    )
    return {"status": "cancelled" if success else "not_found_or_ignored"}


@app.get("/download")
async def download_config(
    url: str,
    skip_keywords: str = None,
    request_timeout: int = None,
    source: str = None,
    fallback: bool = None,
):
    try:
        url = unwrap_subscription_url(url)
        options = normalize_options(
            skip_keywords,
            request_timeout,
            source,
            fallback,
        )
        target_map = map_path(url, options)
        with open(target_map, "r", encoding="ascii") as mapping:
            output_key = mapping.read().strip()
        file_path = os.path.join(DATA_DIR, f"{output_key}.yaml")
        if os.path.exists(file_path):
            return FileResponse(
                file_path,
                media_type="application/x-yaml",
                filename="clash_checked.yaml",
            )
    except (OSError, ValueError):
        pass
    return PlainTextResponse(
        "File not found or expired. Please check again.",
        status_code=404,
    )


@app.get("/status/stream")
async def stream_status(url: str = Query(..., description="Job URL")):
    key = source_key(unwrap_subscription_url(url))

    async def event_generator():
        while True:
            job = job_manager.get_job(source_key=key)
            status = job.snapshot() if job else {"status": "unknown"}
            messages = job.consume_logs() if job else []
            payloads = messages or [status.get("message")]
            for message in payloads:
                snapshot = dict(status)
                if message:
                    snapshot["message"] = message
                yield "data: " + json.dumps(
                    {
                        "job_status": snapshot,
                        "global_queue_size": job_manager.queue.qsize(),
                    },
                    ensure_ascii=False,
                ) + "\n\n"

            if status["status"] in {"completed", "cancelled", "error", "unknown"}:
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
