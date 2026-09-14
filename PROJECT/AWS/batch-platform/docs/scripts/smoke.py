#!/usr/bin/env python3
"""End-to-end: real frontend authentication/CSRF, two apps, SH+API and request-key deduplication."""
import base64
from datetime import date
import getpass
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
username = os.environ.get("UI_USER", "operator")
password = os.environ.get("UI_PASSWORD") or getpass.getpass("UI password: ")
auth = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(method, path, form=None, csrf=None):
    headers = {"Authorization": auth}
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if csrf:
        headers[csrf["headerName"]] = csrf["token"]
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        response = client.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            payload = {"error": raw[:200].decode(errors="replace")}
        return response.code, payload


def expect(condition, message):
    if not condition:
        raise SystemExit("FAIL: " + message)


status, csrf = call("GET", "/api/csrf")
expect(status == 200, "GET /api/csrf: " + str(status))
expect(call("POST", "/api/jobs", {})[0] == 403, "POST without CSRF must be rejected")
jobs = []
for app in ["app1", "app2"]:
    for job_type in ["SH", "API"]:
        form = {"app": app, "type": job_type, "name": "reconcile",
                "businessDate": date.today().isoformat(), "requestKey": str(uuid.uuid4())}
        code, result = call("POST", "/api/jobs", form, csrf)
        expect(code == 202, "submit: " + str(result))
        job_id = result["id"]
        code, repeated = call("POST", "/api/jobs", form, csrf)
        expect(code == 202 and repeated.get("id") == job_id, "same requestKey must return the same id")
        changed = dict(form, type="API" if job_type == "SH" else "SH")
        expect(call("POST", "/api/jobs", changed, csrf)[0] == 409, "changed payload must return 409")
        jobs.append((app, job_type, job_id))
        print("ACCEPTED", app, job_type, job_id, flush=True)

invalid = dict(form, businessDate="2026-02-30", requestKey=str(uuid.uuid4()))
expect(call("POST", "/api/jobs", invalid, csrf)[0] == 400, "invalid date must return 400")
remaining = {job_id for _, _, job_id in jobs}
deadline = time.monotonic() + 180
while remaining and time.monotonic() < deadline:
    for job_id in list(remaining):
        code, result = call("GET", "/api/jobs/" + job_id)
        expect(code == 200, "query job: " + str(result))
        state = result["status"]
        if state in ["SUCCEEDED", "FAILED", "UNKNOWN"]:
            print("RESULT", job_id, state, result.get("result"), flush=True)
            expect(state == "SUCCEEDED", "sample job did not succeed; inspect logs before creating a new requestKey")
            remaining.remove(job_id)
    if remaining:
        time.sleep(2)
expect(not remaining, "jobs did not finish within 180 seconds")
print("PASS: both apps completed SH and API; deduplication, conflict, CSRF and validation checked.")
