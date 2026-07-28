"""Send to NiceLabel, JSON edition — bulk HTTP POST.

Everything here runs against a REAL ``http.server`` bound to 127.0.0.1, not a
mocked ``urlopen``: the point of the module is how it behaves against an actual
socket (status codes, bodies, timeouts, connection failures), and a mock would
happily agree with whatever the code does.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from okgen import nicelabel_post as nlp
from okgen.api import service
from okgen.config import Config


# --------------------------------------------------------------------------- #
# A scriptable endpoint
# --------------------------------------------------------------------------- #

class _Endpoint:
    """A local HTTP server whose reply to each POST comes from ``plan``.

    ``plan`` is a list of (status, body) consumed one per request; once it runs
    out the last entry repeats. ``delay`` sleeps before replying (for timeouts).
    """

    def __init__(self, plan=None, delay=0.0):
        self.plan = list(plan or [(200, '{"status":"OK"}')])
        self.delay = delay
        self.requests = []          # (headers, body) per received POST
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = self.rfile.read(length)
                outer.requests.append((dict(self.headers), payload))
                if outer.delay:
                    time.sleep(outer.delay)
                i = min(len(outer.requests) - 1, len(outer.plan) - 1)
                status, body = outer.plan[i]
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):      # keep pytest output clean
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/labels"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _cfg(endpoint, folder, **over):
    raw = {
        "endpoint_url": endpoint,
        "json_folder": str(folder),
        "username": "labeluser",
        "password": "s3cret",
        "retries": 0,
        "timeout_seconds": 5,
        "retry_backoff_seconds": 0,
        "write_log": False,
    }
    raw.update(over)
    return raw


def _files(tmp_path, n=1, prefix="Style"):
    """n real .json files in a 'working' folder, plus a separate staging folder."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    stage = tmp_path / "stage"
    stage.mkdir(exist_ok=True)
    paths = []
    for i in range(n):
        p = work / f"{prefix}{i}.json"
        p.write_text(json.dumps({"data": {"type": "styleHeaders", "n": i}}),
                     encoding="utf-8")
        paths.append(str(p))
    return paths, stage


def _run(paths, raw):
    return nlp.run(paths, raw, sleep=lambda s: None)


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #

def test_posts_each_file_and_files_it_under_processed(tmp_path):
    paths, stage = _files(tmp_path, 3)
    with _Endpoint() as ep:
        res = _run(paths, _cfg(ep.url, stage))

    assert res["summary"]["posted"] == 3
    assert res["summary"]["failed"] == 0
    assert len(ep.requests) == 3                      # one request per file
    assert sorted(p.name for p in (stage / "Processed").iterdir()) == [
        "Style0.json", "Style1.json", "Style2.json"]
    assert list((stage / "failed").iterdir()) == []


def test_the_original_file_is_never_moved_or_modified(tmp_path):
    paths, stage = _files(tmp_path, 2)
    before = {p: open(p, "rb").read() for p in paths}
    with _Endpoint() as ep:
        _run(paths, _cfg(ep.url, stage))
    for p in paths:
        assert open(p, "rb").read() == before[p], "the user's own file was touched"


def test_the_body_is_the_files_exact_bytes(tmp_path):
    paths, stage = _files(tmp_path, 1)
    raw_bytes = open(paths[0], "rb").read()
    with _Endpoint() as ep:
        _run(paths, _cfg(ep.url, stage))
    headers, body = ep.requests[0]
    assert body == raw_bytes                          # byte-exact over the wire
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Filename"] == "Style0.json"


def test_basic_auth_header_carries_the_credentials(tmp_path):
    import base64
    paths, stage = _files(tmp_path, 1)
    with _Endpoint() as ep:
        _run(paths, _cfg(ep.url, stage))
    auth = ep.requests[0][0]["Authorization"]
    assert auth.startswith("Basic ")
    assert base64.b64decode(auth.split()[1]).decode() == "labeluser:s3cret"


def test_no_authorization_header_when_no_username(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint() as ep:
        _run(paths, _cfg(ep.url, stage, username="", password=""))
    assert "Authorization" not in ep.requests[0][0]


def test_extra_headers_are_sent(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint() as ep:
        _run(paths, _cfg(ep.url, stage, headers={"X-Source": "OkGen"}))
    assert ep.requests[0][0]["X-Source"] == "OkGen"


def test_multipart_mode_wraps_the_file(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint() as ep:
        res = _run(paths, _cfg(ep.url, stage, body_mode="multipart",
                               multipart_field="payload"))
    headers, body = ep.requests[0]
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="payload"; filename="Style0.json"' in body
    assert b'"styleHeaders"' in body                  # the file itself is in there
    assert res["summary"]["posted"] == 1


# --------------------------------------------------------------------------- #
# Failures
# --------------------------------------------------------------------------- #

def test_server_error_lands_in_failed_with_the_response_body(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(plan=[(500, "loader offline")]) as ep:
        res = _run(paths, _cfg(ep.url, stage))

    r = res["results"][0]
    assert r["outcome"] == "failed"
    assert r["status"] == 500
    assert r["error_class"] == nlp.ERR_SERVER
    assert "loader offline" in r["body"]
    assert [p.name for p in (stage / "failed").iterdir()] == ["Style0.json"]
    assert list((stage / "Processed").iterdir()) == []


def test_a_5xx_is_retried_and_a_4xx_is_not(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(plan=[(500, "boom")]) as ep:
        res = _run(paths, _cfg(ep.url, stage, retries=2))
    assert res["results"][0]["attempts"] == 3         # first try + 2 retries

    paths, stage = _files(tmp_path, 1, prefix="Bad")
    with _Endpoint(plan=[(400, "malformed")]) as ep:
        res = _run(paths, _cfg(ep.url, stage, retries=2))
    assert res["results"][0]["attempts"] == 1, "a 400 must not be retried"
    assert res["results"][0]["error_class"] == nlp.ERR_CLIENT


def test_a_transient_failure_that_recovers_is_a_success(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(plan=[(503, "starting"), (200, "ok")]) as ep:
        res = _run(paths, _cfg(ep.url, stage, retries=2))
    assert res["summary"]["posted"] == 1
    assert res["results"][0]["attempts"] == 2
    assert [p.name for p in (stage / "Processed").iterdir()] == ["Style0.json"]


def test_auth_failure_stops_the_run_and_leaves_the_rest_untouched(tmp_path):
    paths, stage = _files(tmp_path, 5)
    with _Endpoint(plan=[(401, "bad credentials")]) as ep:
        res = _run(paths, _cfg(ep.url, stage))

    assert len(ep.requests) == 1, "kept hammering the endpoint with bad credentials"
    outcomes = [r["outcome"] for r in res["results"]]
    assert outcomes == ["failed"] + ["not_attempted"] * 4
    assert res["summary"]["not_attempted"] == 4
    assert "authentication" in res["summary"]["aborted"].lower()
    # The four untried files were never even staged.
    assert [p.name for p in (stage / "failed").iterdir()] == ["Style0.json"]
    assert list((stage / "Processed").iterdir()) == []


def test_stop_on_auth_failure_can_be_turned_off(tmp_path):
    paths, stage = _files(tmp_path, 3)
    with _Endpoint(plan=[(403, "nope")]) as ep:
        res = _run(paths, _cfg(ep.url, stage, stop_on_auth_failure=False))
    assert len(ep.requests) == 3
    assert res["summary"]["failed"] == 3


def test_a_2xx_whose_body_says_error_counts_as_failed(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(plan=[(200, '{"status":"ERROR","msg":"bad chain"}')]) as ep:
        res = _run(paths, _cfg(ep.url, stage, failure_body_contains="\"ERROR\""))
    r = res["results"][0]
    assert r["outcome"] == "failed"
    assert r["status"] == 200
    assert r["error_class"] == nlp.ERR_BODY
    assert [p.name for p in (stage / "failed").iterdir()] == ["Style0.json"]


def test_a_2xx_missing_the_required_marker_counts_as_failed(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(plan=[(200, '{"result":"queued"}')]) as ep:
        res = _run(paths, _cfg(ep.url, stage, success_body_contains="ACCEPTED"))
    assert res["results"][0]["error_class"] == nlp.ERR_BODY

    paths, stage = _files(tmp_path, 1, prefix="Good")
    with _Endpoint(plan=[(200, '{"result":"ACCEPTED"}')]) as ep:
        res = _run(paths, _cfg(ep.url, stage, success_body_contains="ACCEPTED"))
    assert res["summary"]["posted"] == 1


def test_a_status_outside_success_statuses_fails(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(plan=[(202, "queued")]) as ep:
        res = _run(paths, _cfg(ep.url, stage, success_statuses=[200]))
    assert res["results"][0]["outcome"] == "failed"


def test_timeout_is_reported_as_a_timeout(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(delay=1.5) as ep:
        res = _run(paths, _cfg(ep.url, stage, timeout_seconds=0.3, retries=0))
    r = res["results"][0]
    assert r["error_class"] == nlp.ERR_TIMEOUT
    assert r["outcome"] == "failed"


def test_an_unreachable_endpoint_is_a_network_failure(tmp_path):
    paths, stage = _files(tmp_path, 2)
    # Port 9 (discard) on localhost: nothing is listening.
    res = _run(paths, _cfg("http://127.0.0.1:9/labels", stage, retries=0))
    assert all(r["error_class"] == nlp.ERR_NETWORK for r in res["results"])
    assert res["summary"]["failed"] == 2
    assert res["summary"]["failures_by_cause"] == {nlp.ERR_NETWORK: 2}


def test_non_json_files_are_skipped_not_posted(tmp_path):
    paths, stage = _files(tmp_path, 1)
    stray = tmp_path / "work" / "Notes.txt"
    stray.write_text("hello", encoding="utf-8")
    with _Endpoint() as ep:
        res = _run(paths + [str(stray)], _cfg(ep.url, stage))
    assert len(ep.requests) == 1
    assert res["summary"]["skipped"] == 1
    assert [r["outcome"] for r in res["results"]] == ["posted", "skipped"]


def test_two_selected_files_with_the_same_name_do_not_clobber_each_other(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    stage = tmp_path / "stage"
    for d in (a, b, stage):
        d.mkdir()
    for d in (a, b):
        (d / "Same.json").write_text(json.dumps({"from": d.name}), encoding="utf-8")
    with _Endpoint() as ep:
        res = _run([str(a / "Same.json"), str(b / "Same.json")],
                   _cfg(ep.url, stage))
    assert res["summary"]["posted"] == 2
    assert sorted(p.name for p in (stage / "Processed").iterdir()) == [
        "Same (1).json", "Same.json"]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def test_summary_counts_and_failure_breakdown(tmp_path):
    paths, stage = _files(tmp_path, 3)
    with _Endpoint(plan=[(200, "ok"), (500, "boom"), (400, "bad")]) as ep:
        res = _run(paths, _cfg(ep.url, stage))
    s = res["summary"]
    assert (s["total"], s["posted"], s["failed"], s["skipped"]) == (3, 1, 2, 0)
    assert s["failures_by_cause"] == {nlp.ERR_SERVER: 1, nlp.ERR_CLIENT: 1}
    assert s["elapsed_seconds"] >= 0
    assert s["processed_dir"].endswith("Processed")


def test_progress_is_reported_as_it_goes(tmp_path):
    paths, stage = _files(tmp_path, 3)
    seen = []
    with _Endpoint() as ep:
        nlp.run(paths, _cfg(ep.url, stage), progress=seen.append,
                sleep=lambda s: None)
    assert seen[0]["done"] == 0 and seen[0]["total"] == 3
    assert [p["done"] for p in seen] == [0, 1, 2, 3]
    assert seen[-1]["posted"] == 3


def test_run_log_records_every_file(tmp_path):
    paths, stage = _files(tmp_path, 2)
    with _Endpoint(plan=[(200, "ok"), (500, "boom")]) as ep:
        res = _run(paths, _cfg(ep.url, stage, write_log=True))
    log = res["summary"]["log"]
    assert log
    text = open(log, encoding="utf-8").read()
    assert "Style0.json" in text and "Style1.json" in text
    assert "1 posted, 1 failed" in text
    assert "s3cret" not in text, "the password leaked into the run log"


def test_the_password_never_appears_in_the_result(tmp_path):
    paths, stage = _files(tmp_path, 1)
    with _Endpoint(plan=[(401, "denied")]) as ep:
        res = _run(paths, _cfg(ep.url, stage))
    assert "s3cret" not in json.dumps(res)


def test_credentials_in_the_url_are_redacted(tmp_path):
    assert nlp.redact_url("https://u:p@host/x") == "https://host/x"
    assert nlp.redact_url("https://host/x") == "https://host/x"


def test_sent_and_errors_mirror_the_ok_hand_off_shape(tmp_path):
    # The client reports both hand-offs the same way, so 'post' must return the
    # same sent/errors keys the hot-folder copy does.
    paths, stage = _files(tmp_path, 2)
    with _Endpoint(plan=[(200, "ok"), (500, "boom")]) as ep:
        res = _run(paths, _cfg(ep.url, stage))
    assert res["sent"] == ["Style0.json"]
    assert len(res["errors"]) == 1 and "path" in res["errors"][0]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def test_unconfigured_endpoint_is_a_clear_error(tmp_path):
    with pytest.raises(nlp.PostError, match="not configured"):
        nlp.run([], {})
    with pytest.raises(nlp.PostError, match="endpoint_url"):
        nlp.run([], {"endpoint_url": "CHANGE_ME", "json_folder": str(tmp_path)})
    with pytest.raises(nlp.PostError, match="json_folder"):
        nlp.run([], {"endpoint_url": "http://x/y", "json_folder": "CHANGE_ME"})


def test_a_missing_staging_folder_fails_before_anything_is_sent(tmp_path):
    paths, _ = _files(tmp_path, 1)
    with pytest.raises(nlp.PostError, match="not found or unreachable"):
        _run(paths, _cfg("http://127.0.0.1:9/x", tmp_path / "nope"))


def test_endpoint_must_be_http(tmp_path):
    with pytest.raises(nlp.PostError, match="http"):
        nlp.run([], {"endpoint_url": "ftp://host/x", "json_folder": str(tmp_path)})


def test_password_reads_an_environment_variable(tmp_path, monkeypatch):
    import base64
    monkeypatch.setenv("OKGEN_TEST_PW", "from-env")
    paths, stage = _files(tmp_path, 1)
    with _Endpoint() as ep:
        _run(paths, _cfg(ep.url, stage, password="${OKGEN_TEST_PW}"))
    auth = ep.requests[0][0]["Authorization"]
    assert base64.b64decode(auth.split()[1]).decode() == "labeluser:from-env"


def test_an_unset_password_variable_says_which_one(tmp_path, monkeypatch):
    monkeypatch.delenv("OKGEN_MISSING_PW", raising=False)
    with pytest.raises(nlp.PostError, match="OKGEN_MISSING_PW"):
        nlp.settings_from(_cfg("http://x/y", tmp_path, password="${OKGEN_MISSING_PW}"))


def test_bad_body_mode_is_rejected(tmp_path):
    with pytest.raises(nlp.PostError, match="body_mode"):
        nlp.settings_from(_cfg("http://x/y", tmp_path, body_mode="xml"))


def test_describe_never_leaks_the_password(tmp_path):
    info = nlp.describe(_cfg("https://user:pw@host/api", tmp_path))
    assert info["configured"] is True
    assert info["endpoint"] == "https://host/api"
    assert "s3cret" not in json.dumps(info)
    assert "password" not in info


def test_describe_reports_why_it_is_unconfigured(tmp_path):
    info = nlp.describe({"warning": "careful"})
    assert info["configured"] is False
    assert "nicelabel_post.yaml" in info["error"]
    assert info["warning"] == "careful"


def test_config_loads_the_yaml_block(tmp_path):
    (tmp_path / "nicelabel_post.yaml").write_text(
        "endpoint_url: http://x/y\njson_folder: /tmp/j\n", encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.nicelabel_post()["endpoint_url"] == "http://x/y"


def test_a_malformed_yaml_disables_the_send_without_killing_startup(tmp_path, capsys):
    (tmp_path / "nicelabel_post.yaml").write_text("endpoint_url: [unclosed\n",
                                                  encoding="utf-8")
    cfg = Config.load(tmp_path)          # must not raise
    assert cfg.nicelabel_post() == {}
    assert "JSON send is disabled" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Service wiring
# --------------------------------------------------------------------------- #

def _service_cfg(endpoint, folder, **over):
    return Config({}, [], nicelabel_post=_cfg(endpoint, folder, **over),
                  nicelabel_path=str(folder))


def test_the_ok_hand_off_is_untouched_by_the_json_one(tmp_path):
    """The .OK copy must behave EXACTLY as it did before the POST work.

    Including the mixed case: a selection holding an .OK file copies the .OK
    files and reports anything else as a per-file error — it does not refuse
    the batch, and the result dict grows no new keys.
    """
    hot = tmp_path / "hot"
    hot.mkdir()
    ok = tmp_path / "Style.OK"
    ok.write_bytes(b"HEADER\\\r\n")
    js = tmp_path / "Calgary.json"
    js.write_text("{}", encoding="utf-8")
    cfg = Config({}, [], nicelabel_path=str(hot), nicelabel_post={})

    res = service.send_to_nicelabel([str(ok), str(js)], cfg)
    assert res["sent"] == ["Style.OK"]
    assert res["errors"] == [{"path": str(js), "error": "not an .OK file"}]
    assert set(res) == {"sent", "errors", "dest"}, "the .OK result shape changed"
    assert (hot / "Style.OK").read_bytes() == ok.read_bytes()


def test_only_an_all_json_selection_takes_the_post_route(tmp_path):
    cfg = _service_cfg("http://x/y", tmp_path)
    mixed = [str(tmp_path / "a.OK"), str(tmp_path / "b.json")]
    assert service.send_scope(mixed, cfg)["mode"] == "copy"
    assert service.send_scope([str(tmp_path / "a.OK")], cfg)["mode"] == "copy"
    assert service.send_scope([str(tmp_path / "b.json")], cfg)["mode"] == "post"
    with pytest.raises(service.EditError, match="only"):
        service.start_send_job(mixed, cfg)


def test_send_scope_describes_each_hand_off(tmp_path):
    paths, stage = _files(tmp_path, 1)
    cfg = _service_cfg("https://labels.example/api", stage)

    post = service.send_scope(paths, cfg)
    assert post["mode"] == "post"
    assert post["destination"] == "https://labels.example/api"
    assert post["configured"] is True

    copy = service.send_scope([str(tmp_path / "x.OK")], cfg)
    assert copy["mode"] == "copy"
    assert copy["destination"] == str(stage)


def test_send_scope_reports_an_unconfigured_endpoint(tmp_path):
    paths, _ = _files(tmp_path, 1)
    cfg = Config({}, [], nicelabel_post={})
    scope = service.send_scope(paths, cfg)
    assert scope["configured"] is False
    assert "nicelabel_post.yaml" in scope["error"]


def test_the_ok_route_still_reports_json_as_not_an_ok_file(tmp_path):
    # Unchanged from before the POST work: this route knows only .OK files.
    paths, stage = _files(tmp_path, 1)
    cfg = _service_cfg("http://x/y", stage)
    res = service.send_to_nicelabel(paths, cfg)
    assert res["sent"] == []
    assert res["errors"][0]["error"] == "not an .OK file"


def test_start_send_job_fails_fast_on_bad_config(tmp_path):
    paths, _ = _files(tmp_path, 1)
    cfg = Config({}, [], nicelabel_post={})
    with pytest.raises(service.EditError, match="not configured"):
        service.start_send_job(paths, cfg)

    cfg = _service_cfg("http://127.0.0.1:9/x", tmp_path / "missing")
    with pytest.raises(service.EditError, match="not found or unreachable"):
        service.start_send_job(paths, cfg)


def test_send_job_runs_in_the_background_and_reports_its_result(tmp_path):
    paths, stage = _files(tmp_path, 3)
    with _Endpoint(plan=[(200, "ok"), (200, "ok"), (500, "boom")]) as ep:
        cfg = _service_cfg(ep.url, stage)
        handle = service.start_send_job(paths, cfg)
        assert handle["total"] == 3 and handle["mode"] == "post"

        deadline = time.time() + 20
        while time.time() < deadline:
            status = service.send_job_status(handle["job"])
            if status["state"] != "running":
                break
            time.sleep(0.02)

    assert status["state"] == "done"
    assert status["done"] == 3
    assert status["posted"] == 2 and status["failed"] == 1
    assert status["result"]["summary"]["posted"] == 2
    assert [p.name for p in (stage / "failed").iterdir()] == ["Style2.json"]


def test_an_unknown_job_id_is_an_error():
    with pytest.raises(service.EditError, match="unknown"):
        service.send_job_status("nope")
