#!/usr/bin/env python3
"""Read-only production gates shared by deploy automation and CI tests."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"(?i)(change[_-]?me|replace[_-]?me|placeholder|example|password|secret_here)")
SECRET_SPECS = {
    "postgres_password": 32,
    "admin_bootstrap_password": 12,
    "session_secret": 32,
}


class GateError(RuntimeError):
    pass


class ExternalBlock(GateError):
    pass


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ExternalBlock(f"environment file not found: {path}")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise GateError(f"invalid environment line {number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check_environment(env: dict[str, str]) -> str:
    required = ("DOMAIN", "CORS_ALLOWED_ORIGINS", "FACE_MODEL_NAME", "RELEASE_VERSION")
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise GateError("missing required environment values: " + ", ".join(missing))
    if env.get("ENVIRONMENT") != "production" or env.get("DEBUG", "").lower() != "false":
        raise GateError("ENVIRONMENT=production and DEBUG=false are required")
    if env.get("AUTO_MIGRATE", "").lower() != "false":
        raise GateError("AUTO_MIGRATE=false is required")
    forbidden = [name for name in ("POSTGRES_PASSWORD", "ADMIN_PASSWORD", "SESSION_SECRET", "DATABASE_URL") if env.get(name)]
    if forbidden:
        raise GateError("critical secrets must not be stored in .env.prod: " + ", ".join(forbidden))
    version = env["RELEASE_VERSION"]
    if version.lower() == "latest" or PLACEHOLDER.search(version) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", version):
        raise GateError("RELEASE_VERSION must be an immutable, safe tag or commit (not latest)")
    return f"release={version} domain={env['DOMAIN']}"


def check_secrets(_env: dict[str, str]) -> str:
    directory = ROOT / "deploy" / "secrets"
    for name, minimum in SECRET_SPECS.items():
        path = directory / name
        if not path.is_file():
            raise ExternalBlock(f"secret file not found: {path}")
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise GateError(f"secret file is not readable: {path}: {exc}") from exc
        if not value:
            raise GateError(f"secret file is empty: {path}")
        if len(value) < minimum:
            raise GateError(f"secret {name} must contain at least {minimum} characters")
        if PLACEHOLDER.search(value):
            raise GateError(f"secret {name} contains a placeholder or weak default")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise GateError(f"secret {name} permissions are {mode:04o}; require 0600 (no group/world access)")
    return "all required secret files are non-empty and restricted"


def run_openssl(openssl: str, args: list[str], input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        [openssl, *args], input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=ROOT, timeout=20,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GateError("TLS validation failed: " + (message[-1] if message else "openssl error"))
    return result.stdout


def check_tls(env: dict[str, str]) -> str:
    cert = ROOT / "deploy" / "certs" / "fullchain.pem"
    key = ROOT / "deploy" / "certs" / "privkey.pem"
    for path in (cert, key):
        if not path.is_file():
            raise ExternalBlock(f"TLS file not found: {path}")
        try:
            if not path.read_bytes():
                raise GateError(f"TLS file is empty: {path}")
        except OSError as exc:
            raise GateError(f"TLS file is not readable: {path}: {exc}") from exc
    openssl = shutil.which("openssl")
    if not openssl:
        raise ExternalBlock("openssl CLI is required for production TLS validation")
    run_openssl(openssl, ["x509", "-in", str(cert), "-noout"])
    run_openssl(openssl, ["pkey", "-in", str(key), "-noout"])
    cert_public = run_openssl(openssl, ["x509", "-in", str(cert), "-pubkey", "-noout"])
    key_public = run_openssl(openssl, ["pkey", "-in", str(key), "-pubout"])
    if hashlib.sha256(cert_public).digest() != hashlib.sha256(key_public).digest():
        raise GateError("TLS private key does not match certificate")
    run_openssl(openssl, ["x509", "-in", str(cert), "-checkend", "0", "-noout"])
    hostname = env.get("DOMAIN", "").strip()
    if hostname:
        run_openssl(openssl, ["x509", "-in", str(cert), "-checkhost", hostname, "-noout"])
    return f"certificate parses, is current, key matches, SAN covers {hostname}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_models(env: dict[str, str]) -> str:
    specs = (
        ("anti_spoof.onnx", "ANTI_SPOOFING_MODEL_SHA256"),
        ("fsvfm_vit_s_oci2m_run0.pth.tar", "FSVFM_CHECKPOINT_SHA256"),
    )
    for filename, variable in specs:
        path = ROOT / "deploy" / "models" / filename
        expected = env.get(variable, "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise GateError(f"{variable} must be a 64-character SHA-256")
        if not path.is_file():
            raise ExternalBlock(f"model artifact not found: {path}")
        if sha256_file(path) != expected:
            raise GateError(f"model digest mismatch: {filename}")
    if env.get("FSVFM_SHADOW_MODE", "").lower() != "true":
        raise GateError("PHASE 2C requires FSVFM_SHADOW_MODE=true")
    return "model artifacts match pinned SHA-256 values; FS-VFM is shadow-only"


def check_docker(_env: dict[str, str]) -> str:
    docker = shutil.which("docker")
    if not docker:
        raise ExternalBlock("docker CLI is not installed")
    result = subprocess.run([docker, "compose", "version"], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ExternalBlock("Docker Compose plugin is unavailable")
    daemon = subprocess.run(
        [docker, "info", "--format", "{{.ServerVersion}}"],
        capture_output=True, text=True, timeout=20,
    )
    if daemon.returncode:
        raise ExternalBlock("Docker engine is unavailable")
    return f"{result.stdout.strip().splitlines()[0]}; engine={daemon.stdout.strip()}"


CHECKS = {
    "environment": check_environment,
    "secrets": check_secrets,
    "tls": check_tls,
    "models": check_models,
    "docker": check_docker,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=str(ROOT / ".env.prod"))
    parser.add_argument("--check", choices=CHECKS, action="append")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    selected = args.check or list(CHECKS)
    try:
        env = read_env(Path(args.env_file))
    except (GateError, OSError) as exc:
        status = "BLOCKED_EXTERNAL" if isinstance(exc, ExternalBlock) and args.dry_run else "FAIL"
        print(f"{status} environment: {exc}")
        return 2 if status == "BLOCKED_EXTERNAL" else 1
    exit_code = 0
    for name in selected:
        try:
            detail = CHECKS[name](env)
            print(f"PASS {name}: {detail}")
        except ExternalBlock as exc:
            status = "BLOCKED_EXTERNAL" if args.dry_run else "FAIL"
            print(f"{status} {name}: {exc}")
            exit_code = max(exit_code, 2 if args.dry_run else 1)
            if not args.dry_run:
                break
        except (GateError, OSError, subprocess.SubprocessError) as exc:
            print(f"FAIL {name}: {exc}")
            return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
