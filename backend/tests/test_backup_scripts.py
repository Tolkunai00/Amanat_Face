import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "scripts" / "postgres_backup.sh"
RESTORE = ROOT / "scripts" / "postgres_restore.sh"


@lru_cache
def shell_path():
    found = shutil.which("sh")
    candidate = found or str(Path("C:/Program Files/Git/bin/sh.exe"))
    if not Path(candidate).exists():
        return None
    try:
        probe = subprocess.run(
            [candidate, "-c", "exit 0"], capture_output=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return candidate if probe.returncode == 0 else None


def executable(path, body):
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def environment(fake_bin, **values):
    result = dict(os.environ)
    result.update({
        "PATH": str(fake_bin) + os.pathsep + result.get("PATH", ""),
        "POSTGRES_PASSWORD": "test-only-password",
        "POSTGRES_DB": "amanat",
    })
    result.update(values)
    return result


@pytest.mark.skipif(shell_path() is None, reason="POSIX shell is unavailable")
def test_backup_command_failure_returns_nonzero(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable(fake_bin / "pg_dump", "exit 23\n")
    result = subprocess.run(
        [shell_path(), str(BACKUP)],
        env=environment(fake_bin, BACKUP_DIR=str(tmp_path / "backups"), BACKUP_RUN_ONCE="1"),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "pg_dump failed" in result.stderr


@pytest.mark.skipif(shell_path() is None, reason="POSIX shell is unavailable")
def test_corrupt_backup_is_rejected(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable(fake_bin / "pg_restore", "exit 1\n")
    backup = tmp_path / "corrupt.dump"
    backup.write_bytes(b"not-a-pg-dump")
    result = subprocess.run(
        [shell_path(), str(RESTORE), str(backup), "restore_test"],
        env=environment(fake_bin), capture_output=True, text=True,
    )
    assert result.returncode == 6
    assert "catalog is corrupt" in result.stderr


@pytest.mark.skipif(shell_path() is None, reason="POSIX shell is unavailable")
def test_restore_rejects_wrong_checksum(tmp_path):
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"dump")
    backup.with_suffix(".dump.json").write_text(
        json.dumps({"sha256": "0" * 64}, separators=(",", ":")), encoding="utf-8"
    )
    result = subprocess.run(
        [shell_path(), str(RESTORE), str(backup), "restore_test"],
        env=environment(tmp_path), capture_output=True, text=True,
    )
    assert result.returncode == 5
    assert "SHA256 mismatch" in result.stderr


@pytest.mark.skipif(shell_path() is None, reason="POSIX shell is unavailable")
def test_temporary_restore_database_is_cleaned_up(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.log"
    executable(fake_bin / "pg_restore", "exit 0\n")
    executable(fake_bin / "createdb", f'echo "createdb $*" >> "{calls.as_posix()}"\n')
    executable(fake_bin / "dropdb", f'echo "dropdb $*" >> "{calls.as_posix()}"\n')
    executable(
        fake_bin / "psql",
        "case \"$*\" in\n"
        "  *pg_database*) exit 0 ;;\n"
        "  *version_num*) echo test_revision ;;\n"
        "  *teacher_accounts*) echo 'relation teacher_accounts does not exist' >&2; exit 1 ;;\n"
        "  *COUNT*) echo 0 ;;\n"
        "esac\n",
    )
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"valid-enough-for-fake-pg-restore")
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    backup.with_suffix(".dump.json").write_text(
        json.dumps({
            "sha256": digest,
            "counts": {key: 0 for key in (
                "students", "attendance", "face_embeddings", "teachers", "parents"
            )},
        }, separators=(",", ":")),
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell_path(), str(RESTORE), str(backup), "restore_unique_123", "--temporary"],
        env=environment(fake_bin), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "createdb restore_unique_123" in calls.read_text(encoding="utf-8")
    assert "dropdb --if-exists --force restore_unique_123" in calls.read_text(encoding="utf-8")
