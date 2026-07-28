"""Send to NiceLabel, JSON edition — bulk HTTP POST of Calgary ``.json`` files.

The ``.OK`` layouts hand off to NiceLabel by COPYING into a watched hot folder
(``service.send_to_nicelabel``). The Calgary JSON layouts hand off over HTTP
instead: each selected file is POSTed on its own request, so every file gets its
own verdict and can be filed accordingly.

Per file:  copy into ``json_folder``  ->  POST  ->  move that COPY into
``Processed/`` or ``failed/``. The user's ORIGINAL file is never moved or
touched — this folder is a staging area, not their working folder.

Deliberately stdlib-only (``urllib.request``): OkGen ships offline to locked-down
DC boxes from ``vendor/wheels`` (D4), and pulling in ``requests`` would mean
vendoring it plus urllib3/certifi/idna/charset-normalizer across five CPython
versions for one feature. Basic auth, timeouts, TLS and custom headers are all
available without it.

Config: ``config/nicelabel_post.yaml`` (see that file for the schema).
"""

from __future__ import annotations

import base64
import datetime
import http.client
import os
import re
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


class PostError(Exception):
    """A configuration/environment failure that stops the whole run.

    Distinct from a per-file failure, which is reported in the results and
    filed under ``failed/`` rather than raised.
    """


# How much of a response body to keep for the report. Enough to read a real
# error message, small enough that 500 failures don't blow up the payload.
_BODY_SNIPPET = 500

# Error classes, in the vocabulary the UI groups failures by.
ERR_AUTH = "auth"           # 401/403 — credentials rejected
ERR_CLIENT = "client"       # other 4xx — the request/payload is wrong
ERR_SERVER = "server"       # 5xx — the far end broke
ERR_TIMEOUT = "timeout"     # no response in timeout_seconds
ERR_NETWORK = "network"     # DNS/connection refused/reset
ERR_TLS = "tls"             # certificate/handshake
ERR_BODY = "body"           # 2xx, but the body says it failed
ERR_LOCAL = "local"         # staging/move failed on our side

_RETRYABLE = {ERR_SERVER, ERR_TIMEOUT, ERR_NETWORK}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

@dataclass
class Settings:
    endpoint_url: str
    json_folder: str
    username: str = ""
    password: str = ""
    body_mode: str = "raw_json"
    multipart_field: str = "file"
    headers: Dict[str, str] = field(default_factory=dict)
    success_statuses: List[int] = field(default_factory=lambda: [200, 201, 202, 204])
    success_body_contains: str = ""
    failure_body_contains: str = ""
    timeout_seconds: float = 30.0
    retries: int = 2
    retry_backoff_seconds: float = 1.0
    stop_on_auth_failure: bool = True
    verify_tls: bool = True
    ca_bundle: str = ""
    processed_dir: str = "Processed"
    failed_dir: str = "failed"
    write_log: bool = True
    warning: str = ""


_ENV_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _expand(value: str) -> str:
    """Resolve a whole-value ``${VAR}`` against the environment.

    Only the whole-value form is expanded, so a password that legitimately
    contains ``${`` in the middle is left alone.
    """
    raw = str(value or "").strip()
    m = _ENV_RE.match(raw)
    return os.environ.get(m.group(1), "") if m else raw


def _is_placeholder(value: str) -> bool:
    return not value or value.strip().upper().startswith("CHANGE_ME")


def _num(raw, default, cast):
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def settings_from(raw: Optional[dict]) -> Settings:
    """Build validated ``Settings`` from the ``nicelabel_post.yaml`` block.

    Raises ``PostError`` with an actionable message for anything the run cannot
    proceed without — the endpoint, the staging folder, a password when a
    username is set.
    """
    raw = raw or {}
    if not raw:
        raise PostError(
            "The JSON send is not configured — fill in config/nicelabel_post.yaml "
            "(endpoint_url, json_folder, credentials).")

    url = _expand(raw.get("endpoint_url", ""))
    if _is_placeholder(url):
        raise PostError("endpoint_url is not set in config/nicelabel_post.yaml")
    if not url.lower().startswith(("http://", "https://")):
        raise PostError(f"endpoint_url must start with http:// or https:// (got {url!r})")

    folder = _expand(raw.get("json_folder", ""))
    if _is_placeholder(folder):
        raise PostError("json_folder is not set in config/nicelabel_post.yaml")

    username = _expand(raw.get("username", ""))
    password = _expand(raw.get("password", ""))
    if username and not password:
        pw_raw = str(raw.get("password", "") or "").strip()
        m = _ENV_RE.match(pw_raw)
        if m:
            raise PostError(
                f"password reads environment variable {m.group(1)}, which is not "
                f"set — set it before sending (or put the password in "
                f"config/nicelabel_post.yaml).")
        raise PostError("username is set but password is empty in config/nicelabel_post.yaml")

    mode = str(raw.get("body_mode", "raw_json") or "raw_json").strip().lower()
    if mode not in ("raw_json", "multipart"):
        raise PostError(f"body_mode must be 'raw_json' or 'multipart' (got {mode!r})")

    statuses = raw.get("success_statuses")
    if isinstance(statuses, list) and statuses:
        ok_statuses = [int(s) for s in statuses]
    else:
        ok_statuses = [200, 201, 202, 204]

    headers = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()}

    return Settings(
        endpoint_url=url,
        json_folder=folder,
        username=username,
        password=password,
        body_mode=mode,
        multipart_field=str(raw.get("multipart_field", "file") or "file"),
        headers=headers,
        success_statuses=ok_statuses,
        success_body_contains=str(raw.get("success_body_contains", "") or ""),
        failure_body_contains=str(raw.get("failure_body_contains", "") or ""),
        timeout_seconds=_num(raw.get("timeout_seconds", 30), 30.0, float),
        retries=max(0, _num(raw.get("retries", 2), 2, int)),
        retry_backoff_seconds=_num(raw.get("retry_backoff_seconds", 1.0), 1.0, float),
        stop_on_auth_failure=bool(raw.get("stop_on_auth_failure", True)),
        verify_tls=bool(raw.get("verify_tls", True)),
        ca_bundle=_expand(raw.get("ca_bundle", "")),
        processed_dir=str(raw.get("processed_dir", "Processed") or "Processed"),
        failed_dir=str(raw.get("failed_dir", "failed") or "failed"),
        write_log=bool(raw.get("write_log", True)),
        warning=str(raw.get("warning", "") or ""),
    )


def redact_url(url: str) -> str:
    """Strip any ``user:pass@`` userinfo before a URL is shown or logged."""
    return re.sub(r"://[^/@]*@", "://", str(url or ""))


def describe(raw: Optional[dict]) -> dict:
    """Non-secret summary of the JSON send target, for the confirm dialog.

    Never includes the password. Returns ``configured: False`` plus the reason
    when the config is incomplete, so the UI can say what to fix instead of
    failing at post time.
    """
    try:
        s = settings_from(raw)
    except PostError as exc:
        return {"configured": False, "error": str(exc),
                "warning": str((raw or {}).get("warning", "") or "")}
    return {
        "configured": True,
        "endpoint": redact_url(s.endpoint_url),
        "folder": s.json_folder,
        "username": s.username,
        "body_mode": s.body_mode,
        "warning": s.warning,
    }


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _ssl_context(s: Settings) -> Optional[ssl.SSLContext]:
    if not s.endpoint_url.lower().startswith("https://"):
        return None
    if not s.verify_tls:
        # Explicit opt-out for an internal self-signed endpoint (config only).
        return ssl._create_unverified_context()
    if s.ca_bundle:
        bundle = Path(s.ca_bundle)
        if not bundle.is_file():
            raise PostError(f"ca_bundle not found: {s.ca_bundle}")
        return ssl.create_default_context(cafile=str(bundle))
    return ssl.create_default_context()


def _multipart(field_name: str, filename: str, payload: bytes):
    """Encode one file as multipart/form-data. Returns (body, content_type)."""
    boundary = "----OkGen" + uuid.uuid4().hex
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: application/json\r\n\r\n"
    ).encode("utf-8")
    post = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return pre + payload + post, f"multipart/form-data; boundary={boundary}"


def _classify_status(status: int) -> str:
    if status in (401, 403):
        return ERR_AUTH
    if status >= 500:
        return ERR_SERVER
    return ERR_CLIENT


def _body_verdict(s: Settings, body: str):
    """Check a 2xx response body against the configured success/failure markers.

    Returns ``None`` when the body is acceptable, else the failure message.
    """
    low = body.lower()
    if s.failure_body_contains and s.failure_body_contains.lower() in low:
        return f"response contains {s.failure_body_contains!r}"
    if s.success_body_contains and s.success_body_contains.lower() not in low:
        return f"response does not contain {s.success_body_contains!r}"
    return None


def _snippet(body: str) -> str:
    body = " ".join(body.split())
    return body[:_BODY_SNIPPET] + ("…" if len(body) > _BODY_SNIPPET else "")


def post_once(s: Settings, payload: bytes, filename: str, context) -> dict:
    """One POST attempt. Returns {ok, status, error_class, message, body}.

    Never raises for an HTTP-level failure — only a genuine programming error
    would escape. Transport problems come back as an error class so the caller
    can decide whether they are worth retrying.
    """
    if s.body_mode == "multipart":
        body, content_type = _multipart(s.multipart_field, filename, payload)
    else:
        body, content_type = payload, "application/json"

    req = urllib.request.Request(s.endpoint_url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("Content-Length", str(len(body)))
    req.add_header("X-Filename", filename)
    for k, v in s.headers.items():
        req.add_header(k, v)
    if s.username:
        token = base64.b64encode(
            f"{s.username}:{s.password}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", "Basic " + token)

    try:
        with urllib.request.urlopen(req, timeout=s.timeout_seconds,
                                    context=context) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # 4xx/5xx arrive here, body and all.
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:                       # pragma: no cover - defensive
            text = ""
        cls = _classify_status(exc.code)
        return {"ok": False, "status": exc.code, "error_class": cls,
                "message": f"HTTP {exc.code} {exc.reason}", "body": _snippet(text)}
    except socket.timeout:
        return {"ok": False, "status": None, "error_class": ERR_TIMEOUT,
                "message": f"no response within {s.timeout_seconds:g}s", "body": ""}
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return {"ok": False, "status": None, "error_class": ERR_TLS,
                    "message": f"TLS failure: {reason}", "body": ""}
        if isinstance(reason, socket.timeout):
            return {"ok": False, "status": None, "error_class": ERR_TIMEOUT,
                    "message": f"no response within {s.timeout_seconds:g}s", "body": ""}
        return {"ok": False, "status": None, "error_class": ERR_NETWORK,
                "message": f"could not reach the endpoint: {reason}", "body": ""}
    except (http.client.HTTPException, OSError) as exc:
        return {"ok": False, "status": None, "error_class": ERR_NETWORK,
                "message": f"connection failed: {exc}", "body": ""}

    if status not in s.success_statuses:
        return {"ok": False, "status": status, "error_class": _classify_status(status),
                "message": f"HTTP {status}", "body": _snippet(text)}

    rejected = _body_verdict(s, text)
    if rejected:
        return {"ok": False, "status": status, "error_class": ERR_BODY,
                "message": f"HTTP {status} but {rejected}", "body": _snippet(text)}
    return {"ok": True, "status": status, "error_class": "",
            "message": f"HTTP {status}", "body": _snippet(text)}


def _post_with_retries(s: Settings, payload: bytes, filename: str, context,
                       sleep=time.sleep) -> dict:
    """POST, retrying only failures that could plausibly succeed next time."""
    delay = s.retry_backoff_seconds
    attempt = 0
    while True:
        attempt += 1
        res = post_once(s, payload, filename, context)
        res["attempts"] = attempt
        if res["ok"] or attempt > s.retries:
            return res
        retryable = res["error_class"] in _RETRYABLE or res.get("status") == 429
        if not retryable:
            return res
        if delay > 0:
            sleep(delay)
        delay *= 2


# --------------------------------------------------------------------------- #
# Filing
# --------------------------------------------------------------------------- #

def _unique_in(folder: Path, name: str) -> Path:
    """A free path in ``folder`` for ``name``, de-duplicating like a browser's
    Downloads folder ('X.json' -> 'X (1).json')."""
    p = Path(name)
    candidate = folder / name
    i = 1
    while candidate.exists():
        candidate = folder / f"{p.stem} ({i}){p.suffix}"
        i += 1
    return candidate


def _run_log(folder: Path, summary: dict, results: List[dict], stamp: str) -> Optional[str]:
    lines = [
        f"OkGen — Send to NiceLabel (JSON POST)   {summary['started']}",
        f"Endpoint : {summary['endpoint']}",
        f"Folder   : {summary['folder']}",
        f"Result   : {summary['posted']} posted, {summary['failed']} failed, "
        f"{summary['skipped']} skipped of {summary['total']} selected "
        f"in {summary['elapsed_seconds']:.1f}s",
        "",
    ]
    for r in results:
        lines.append(
            f"[{r['outcome'].upper():<7}] {r['name']}  "
            f"status={r.get('status') or '-'}  attempts={r.get('attempts', 0)}  "
            f"{r.get('duration_ms', 0)}ms  {r.get('message', '')}"
            + (f"  | {r['body']}" if r.get("body") else "")
        )
    try:
        path = folder / f"okgen_send_{stamp}.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)
    except OSError:
        return None       # a log we cannot write must not fail the run


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def run(paths, raw_config: Optional[dict],
        progress: Optional[Callable[[dict], None]] = None,
        sleep=time.sleep) -> dict:
    """POST every selected ``.json`` file, then file the staged copies.

    ``progress`` is called after each file with the live counters, so a
    long-running background job can report '124 of 500' while it works.

    Raises ``PostError`` only for whole-run problems (unconfigured endpoint,
    missing staging folder). A per-file failure is reported, never raised.
    """
    s = settings_from(raw_config)

    folder = Path(s.json_folder)
    if not folder.is_dir():
        raise PostError(f"json_folder not found or unreachable: {s.json_folder}")
    processed = folder / s.processed_dir
    failed = folder / s.failed_dir
    try:
        processed.mkdir(parents=True, exist_ok=True)
        failed.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PostError(f"could not create Processed/failed under {folder}: {exc}")

    context = _ssl_context(s)

    started = datetime.datetime.now()
    stamp = started.strftime("%Y%m%d_%H%M%S")
    selected = [Path(p) for p in (paths or [])]
    total = len(selected)
    results: List[dict] = []
    posted = failed_n = skipped = 0
    aborted = ""
    t0 = time.time()

    def emit():
        if progress:
            progress({"done": len(results), "total": total, "posted": posted,
                      "failed": failed_n, "skipped": skipped})

    emit()
    for src in selected:
        if aborted:
            # Left untouched on purpose — not staged, not posted, not moved.
            results.append({"name": src.name, "path": str(src), "outcome": "not_attempted",
                            "status": None, "attempts": 0, "duration_ms": 0,
                            "error_class": ERR_AUTH,
                            "message": "skipped — run stopped after an authentication failure",
                            "body": ""})
            continue

        base = {"name": src.name, "path": str(src), "status": None,
                "attempts": 0, "duration_ms": 0, "error_class": "", "body": ""}

        if src.suffix.lower() != ".json" or not src.is_file():
            skipped += 1
            results.append({**base, "outcome": "skipped", "error_class": ERR_LOCAL,
                            "message": "not a .json file"})
            emit()
            continue

        # Stage it, then post it. Staging one file at a time means an aborted
        # run leaves the remaining originals completely alone.
        try:
            payload = src.read_bytes()
            staged = _unique_in(folder, src.name)
            shutil.copy2(src, staged)
        except OSError as exc:
            failed_n += 1
            results.append({**base, "outcome": "failed", "error_class": ERR_LOCAL,
                            "message": f"could not stage the file: {exc}"})
            emit()
            continue

        t = time.time()
        res = _post_with_retries(s, payload, src.name, context, sleep=sleep)
        duration = int((time.time() - t) * 1000)

        dest_dir = processed if res["ok"] else failed
        filed, file_err = None, ""
        try:
            filed = str(shutil.move(str(staged), str(_unique_in(dest_dir, staged.name))))
        except OSError as exc:
            file_err = f" (could not move the staged copy: {exc})"

        if res["ok"]:
            posted += 1
            outcome = "posted"
        else:
            failed_n += 1
            outcome = "failed"
            if s.stop_on_auth_failure and res["error_class"] == ERR_AUTH:
                aborted = ("Stopped after an authentication failure — check the "
                           "username/password in config/nicelabel_post.yaml. "
                           "Remaining files were not sent.")

        results.append({**base, "outcome": outcome, "status": res.get("status"),
                        "attempts": res.get("attempts", 1), "duration_ms": duration,
                        "error_class": res.get("error_class", ""),
                        "message": res.get("message", "") + file_err,
                        "body": res.get("body", ""), "filed_to": filed})
        emit()

    elapsed = time.time() - t0
    by_cause: Dict[str, int] = {}
    for r in results:
        if r["outcome"] in ("failed", "not_attempted") and r.get("error_class"):
            by_cause[r["error_class"]] = by_cause.get(r["error_class"], 0) + 1

    summary = {
        "total": total, "posted": posted, "failed": failed_n, "skipped": skipped,
        "not_attempted": sum(1 for r in results if r["outcome"] == "not_attempted"),
        "elapsed_seconds": round(elapsed, 2),
        "endpoint": redact_url(s.endpoint_url),
        "folder": str(folder),
        "processed_dir": str(processed),
        "failed_dir": str(failed),
        "started": started.strftime("%Y-%m-%d %H:%M:%S"),
        "failures_by_cause": by_cause,
        "aborted": aborted,
    }
    summary["log"] = _run_log(folder, summary, results, stamp) if s.write_log else None
    return {"mode": "post", "summary": summary, "results": results,
            # Names only, so the existing send animation can report like the
            # .OK hot-folder path does.
            "sent": [r["name"] for r in results if r["outcome"] == "posted"],
            "errors": [{"path": r["path"], "error": r["message"]}
                       for r in results if r["outcome"] in ("failed", "not_attempted")]}
