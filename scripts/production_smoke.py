"""HTTPS production smoke test using only the Python standard library."""

import argparse
import getpass
import http.cookiejar
import json
import ssl
import sys
from datetime import date
from pathlib import Path
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener
from urllib.error import HTTPError


def request(opener, base_url, path, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    response = opener.open(Request(
        base_url + path, data=body, method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    ), timeout=20)
    content = response.read()
    content_type = response.headers.get("Content-Type", "")
    return response.status, content_type, content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="For example https://school.example.com")
    parser.add_argument("--admin-password-file")
    parser.add_argument("--release-version", help="Expected immutable release provenance")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    if not base_url.startswith("https://"):
        raise SystemExit("Production smoke tests require an https:// URL")
    password = (
        Path(args.admin_password_file).read_text(encoding="utf-8").strip()
        if args.admin_password_file else getpass.getpass("Admin password: ")
    )
    opener = build_opener(
        HTTPCookieProcessor(http.cookiejar.CookieJar()), HTTPSHandler(context=ssl.create_default_context())
    )

    status, _, body = request(opener, base_url, "/api/v1/health/live")
    live = json.loads(body)
    assert status == 200 and live["status"] == "live"

    status, _, body = request(opener, base_url, "/api/v1/health/ready")
    ready = json.loads(body)
    assert status == 200 and ready["status"] in {"ready", "degraded"}
    for component in ("config", "database", "migrations", "identity_model", "anti_spoof"):
        assert ready["components"][component]["status"] == "ready"

    status, _, body = request(opener, base_url, "/api/v1/health")
    health = json.loads(body)
    assert status == 200 and health["status"] == "ok"
    assert health["recognition"]["commercial_license_declared"] is True
    assert health["recognition"]["anti_spoofing_loaded"] is True
    if args.release_version:
        assert health["release_version"] == args.release_version

    # Anonymous role-access check uses an isolated cookie jar.
    anonymous = build_opener(HTTPSHandler(context=ssl.create_default_context()))
    try:
        request(anonymous, base_url, "/api/v1/students")
        raise AssertionError("student list was accessible without authentication")
    except HTTPError as exc:
        assert exc.code == 401

    status, _, body = request(
        opener, base_url, "/api/v1/auth/login", "POST",
        {"login": "admin", "password": password},
    )
    session = json.loads(body)
    assert status == 200 and session["role"] == "admin"

    checks = {
        "cameras": "/api/v1/camera/status",
        "classes": "/api/v1/classes",
        "students": "/api/v1/students",
        "audit": "/api/v1/audit?limit=1",
        "attendance": "/api/v1/attendance/recent?limit=1",
    }
    results = {}
    for name, path in checks.items():
        status, content_type, body = request(opener, base_url, path)
        assert status == 200
        results[name] = len(json.loads(body))

    # Prometheus scrapes the backend privately. Public gateway access must fail.
    try:
        request(opener, base_url, "/api/v1/metrics")
        raise AssertionError("metrics endpoint is publicly accessible")
    except HTTPError as exc:
        assert exc.code in {403, 404}
        results["metrics_external"] = "blocked"

    today = date.today().isoformat()
    status, content_type, body = request(
        opener, base_url,
        f"/api/v1/school-attendance/reports.xlsx?date_from={today}&date_to={today}",
    )
    assert status == 200 and "spreadsheetml" in content_type and body.startswith(b"PK")
    results["xlsx_bytes"] = len(body)
    print(json.dumps({"status": "ok", "base_url": base_url, "checks": results}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"production smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
