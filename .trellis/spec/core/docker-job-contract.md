# Docker Subscription and Job Contract

## 1. Scope / Trigger

Read this contract when changing Docker subscription fetching, cache identity,
job admission/cancellation, Checker progress, status/SSE, or checked-file
retention, source fallback order, source-result validation, or persistent
history. One Mihomo instance is mutable global state, so one serialized worker
per container is required.

## 2. Signatures

- `normalize_options(...) -> dict`: returns effective `fallback`,
  `request_timeout`, `skip_keywords`, `source`, `mode`, and `headless`.
- `build_output_key(content: bytes, options: dict) -> str`: SHA-256 of source
  bytes plus canonical sorted options.
- `JobManager.submit_job(source_key, output_key, file_path, ..., options,
  request_id, admission_limit, max_age, file_content) -> JobStatus`.
- Checker progress callback:
  `await callback(current, total, message, node_result=None)`.
- `CheckerService._is_complete_result(result: dict) -> bool`: accepts only a
  global IP, finite `0..100` risk, and source-appropriate property fields.
- `IPQuerySource.check(proxy_url, timeout=None) -> dict`: performs one anonymous
  `GET https://api.ipquery.io/?format=json` through the selected node.
- `JobManager(..., max_jobs, history_path)`: loads at most `max_jobs` sanitized
  terminal records from `history_path`.
- `record_history(job)`: atomically upserts one terminal public snapshot.
- `list_history()`, `get_history(job_id)`, and `delete_history(job_id)`:
  return deep copies; deletion returns `None` for unknown, `False` for a failed
  disk write, and `True` after a persisted deletion.
- Compatibility routes remain `/check`, `/api/status`, `/status/stream`,
  `/cancel`, and `/download`.

## 3. Contracts

- Job state stores only hashed `source_key`; never the subscription URL.
- Queue entries contain the immutable `JobStatus` object. Workers never resolve
  a mutable URL/source mapping to find the job they are executing.
- Terminal states are `completed`, `cancelled`, and `error`; late progress is
  ignored.
- Result progress may use the original numeric ID as a list position only after
  checking bounds and the stored row's ID. Sparse or reordered rows must fall
  back to identity lookup; never assume delete/recheck results stay contiguous.
- `/api/jobs/{id}/events` validates the job before streaming headers, sends a
  full initial snapshot, then sends only changed snapshots at the existing
  0.5-second sampling interval. An unchanged stream gets a comment heartbeat
  after 15 seconds. Compare copied snapshots, not references to live results,
  so in-place row changes remain observable. Close only after the emitted
  snapshot is terminal; live job state may change while a yield is suspended.
  An established stream retains the initially validated JobStatus through that
  terminal delivery even if retention cleanup removes it from the registry;
  new requests for an evicted job still return 404.
- Public node results contain `id`, `original_name`, `name`, `ip`, `risk`,
  `bot`, `shared`, `type`, `native`, `source`, `error`, `degraded`, and
  `status`. `error` is whitespace-normalized, limited to 240 characters, and
  redacts URLs plus Authorization/API-key/token/secret/password values.
- Fast and browser checks share the same worker and global Mihomo execution
  lock. Browser mode owns one lazy Playwright browser per check run and closes
  it in `finally`.
- With fallback enabled, fast mode tries the selected Ping0/IPPure source, the
  other built-in source, then IPQuery. IPQuery is never a selectable primary
  and is never requested when fallback is disabled.
- A source result is successful only when it has a global IP, numeric
  percentage risk, and a non-unknown property. Ping0/IPPure/browser also require
  a non-unknown native/broadcast field; IPQuery may return `native=未知` because
  that provider does not establish native/broadcast status.
- Browser parse errors and browser exceptions enter the same fast fallback
  chain only when fallback is enabled. Otherwise the row remains failed.
- `ALLOW_PRIVATE_SUBSCRIPTIONS=false` rejects non-global resolved targets.
  Production fetches connect to the already-validated address while preserving
  the original HTTPS SNI and Host header.
- Output files are atomic and expire with their last retained job.
- Docker history is stored at `/data/jobs/history.json`, newest first, and
  capped by the existing `MAX_JOBS` setting. It uses the same atomic file
  replacement helper as generated YAML; a corrupt or untrusted file is ignored
  rather than trusted.
- Only terminal snapshots are persisted. The allowlist is `job_id`, `status`,
  `current`, `total`, `message`, `label`, `submit_time`, `finish_time`, and
  public result rows. Each row may contain only `id`, `original_name`, `name`,
  `ip`, `risk`, `bot`, `shared`, `type`, `native`, `source`, `error`,
  `degraded`, and `status`.
- History must never contain subscription URLs, API tokens, raw YAML/source
  documents, proxy credentials/configuration, `source_key`, `output_key`,
  `file_path`, options, request IDs, worker logs, or deleted-ID state. History
  labels remain privacy-safe; URL submissions use only `hostname / …`.
- History write failures are logged and must not change a completed,
  cancelled, or failed job back into a non-terminal/error state. A failed
  deletion leaves both memory and disk unchanged.
- Workspace submission performs active reuse, completed-cache lookup, admission,
  input reset, and enqueue under one submission lock. A completed workspace job
  is reusable only when its content/options `output_key` matches and
  `finish_time` is within `max_age`; `max_age=0` disables completed reuse but
  still deduplicates active work.
- When cache is expired, `file_content` atomically restores the original YAML
  before the new worker runs. Queue-full requests must fail before replacing
  any existing output.
- The Web client omits `skip_keywords` when the advanced field is blank so the
  server default still skips subscription metadata nodes such as remaining
  traffic, reset, expiry, announcements, and official-site links.

## 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| URL is not HTTP(S), embeds credentials, or cannot resolve | HTTP 400 |
| Any resolved/redirect target is non-global without explicit opt-in | HTTP 400 |
| Redirect count, response bytes, or total deadline exceeded | HTTP 400 |
| Remote connection/protocol failure | HTTP 502 |
| YAML is not a mapping with a non-empty named `proxies` list | HTTP 400 |
| Atomic admission limit reached | HTTP 503 with `X-QC-Queue-Full: 1` |
| Completed cache expired or `max_age=0` | Restore original input and enqueue a new job |
| Mihomo cannot select a proxy | Row is `failed`, source is `mihomo`, and a sanitized error is returned |
| Source returns a private/invalid IP, non-numeric risk, missing flags, or unknown required property | Try the next enabled source; fail the row if none is complete |
| Browser navigation/parse raises or returns incomplete data | Enter fast fallback when enabled; otherwise fail |
| IPQuery returns non-200, invalid JSON, incomplete boolean flags, or risk outside `0..100` | Treat it as a source error, never a checked row |
| History file is missing, corrupt, has an invalid UUID/status/type, or contains an invalid row | Ignore the invalid file/record/row and continue serving |
| A history write fails after a job terminates | Keep the terminal job usable; log only the exception type |
| A history deletion write fails | Return failure and retain the in-memory record |
| Unknown task/download | `unknown` status / HTTP 404 |

## 5. Good / Base / Bad Cases

- Good: identical source bytes and effective options reuse one active/cache
  output.
- Base: same URL with different source/fallback/timeout/skip options gets a
  distinct output key and map.
- Good: Ping0 is blocked by Cloudflare, IPPure fails, and a complete IPQuery
  response produces a degraded checked row with `native=未知`.
- Base: browser parsing fails with fallback disabled, so the row remains failed
  with a sanitized reason.
- Good: a terminal job survives a container recreation through the named data
  volume while its live `/api/jobs/{id}` state correctly does not.
- Base: an empty history file produces an empty history list.
- Bad: serialize `job.snapshot()` directly and leak `output_key`, raw errors,
  or future internal fields into persistent storage.
- Bad: an HTTP 200 body containing `ip=?` and `risk=?` is counted as checked.
- Bad: a replacement request overwrites a URL dictionary entry and an old
  queued worker later updates the replacement job.
- Bad: validate DNS once, then let another client resolve the hostname again
  before connecting.

## 6. Tests Required

- Duplicate request identity reuses the same active job.
- Replacement cancellation never lets the old worker mutate the new job.
- Atomic queue admission rejects excess work.
- Cancelled jobs ignore late progress and snapshots do not expose mutable
  result dictionaries.
- Seeded result updates scale linearly across a job; sparse and reordered IDs
  retain stable identity and copied-result isolation.
- SSE suppresses duplicate data frames, emits heartbeats and in-place result
  changes, and delivers completed/cancelled/error snapshots even if the job
  terminates during a previous yield. Reconnect gets a full snapshot, and an
  unknown job returns 404 before response headers.
- Output keys vary with effective transformation options.
- Identical completed workspace requests within `max_age` return the same job;
  changing a transformation option returns a different job.
- Credentials, private targets, unsafe redirects, size limits, and total
  deadlines fail without network access.
- Checker emits the public result schema and keeps proxy-group names synced.
- Fast fallback order is selected source, other built-in source, then IPQuery;
  disabling fallback calls only the selected built-in source.
- Partial source responses and private IPs never become checked rows.
- Browser exceptions and parse failures respect the fallback option.
- IPQuery tests mock the session and assert global IP, complete boolean flags,
  numeric `0..100` risk, honest `native=未知`, and invalid-schema rejection.
- Blank Web skip input uses server defaults; explicit non-empty keywords
  override them.
- Error sanitization removes URL, Bearer/Basic Authorization, and token-like
  secrets before the result reaches the browser.
- Terminal completion, cancellation/replacement, and worker error paths each
  persist at most one sanitized record.
- Restart loading rejects corrupt JSON, invalid UUIDs, wrong scalar types,
  invalid result statuses, and secret/internal fields.
- History cap, newest-first order, atomic write failure, restart restoration,
  and delete success/failure preserve the in-memory/disk contract.

## 7. Wrong vs Correct

### Wrong

```python
self.jobs[url] = new_job
job = self.jobs[url]  # an old queue item can now execute the new job
```

### Correct

```python
job = await self.queue.get()  # the queue owns the exact immutable job object
await self.checker.run_check(..., stop_event=job.stop_event)
```

### Wrong

```python
if not result.get("error"):
    return result  # "?" IP/risk is silently reported as checked
```

### Correct

```python
if not result.get("error") and self._is_complete_result(result):
    return result
```

### Wrong

```python
history.append(job.snapshot())  # persists internal keys and raw errors
```

### Correct

```python
record = self._history_record(job)  # strict terminal/public allowlist
await asyncio.to_thread(save_file_atomic, self.history_path, encoded_records)
```
