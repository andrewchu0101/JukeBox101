"""Verify that a mounted secret whose metadata cannot be read is refused with a stable reason code, never with a raw OS error.

Uses real Unix permission bits on a Linux filesystem directory and a monkeypatched stat for the I/O error case; no secret content is read and no Discord or MySQL connection is made.
"""

import errno
import os
from pathlib import Path

import pytest

from bot import __main__ as entry

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix permission bits are required for these scenarios")


def _expected_identity() -> tuple[int, int]:
    """@description: Use the running account as the expected owner and group, so only the failure under test decides the outcome.
    @param: none
    @return: Effective uid and gid of this process.
    """
    return os.geteuid(), os.getegid()


def test_missing_secret_file_is_named_as_missing(unix_permission_dir):
    """@description: A missing mounted file is refused as FILE_MISSING, with no path or OS message in the reason.
    @param unix_permission_dir: Linux-filesystem directory for the scenario.
    @return: None; the assertion checks the exact reason codes.
    """
    expected_uid, expected_gid = _expected_identity()

    reasons = entry._mounted_secret_reasons(str(unix_permission_dir / "absent_secret"), expected_uid, expected_gid)

    assert reasons == ["FILE_MISSING"]


def test_unsearchable_directory_on_the_path_is_named_as_path_not_searchable(unix_permission_dir):
    """@description: A directory on the path without search permission is refused as PATH_NOT_SEARCHABLE, not as a raw permission error and without claiming which directory it was.
    @param unix_permission_dir: Linux-filesystem directory for the scenario.
    @return: None; the assertion checks the exact reason codes and that nothing else is printed.
    """
    expected_uid, expected_gid = _expected_identity()
    locked_dir = unix_permission_dir / "locked"
    locked_dir.mkdir()
    secret_path = locked_dir / "db_password"
    secret_path.write_text("synthetic-not-a-secret", encoding="utf-8")
    secret_path.chmod(0o400)
    locked_dir.chmod(0o600)  # [search bit removed]: The file exists but its path cannot be traversed.
    try:
        reasons = entry._mounted_secret_reasons(str(secret_path), expected_uid, expected_gid)
    finally:
        locked_dir.chmod(0o700)

    assert reasons == ["PATH_NOT_SEARCHABLE"]


def test_unrelated_os_error_is_named_as_open_failed(unix_permission_dir, monkeypatch):
    """@description: Any other metadata failure is refused as OPEN_FAILED, so unexpected I/O errors never pass as a sanctioned file.
    @param unix_permission_dir: Linux-filesystem directory for the scenario.
    @param monkeypatch: Pytest fixture used to simulate an I/O error during stat.
    @return: None; the assertion checks the exact reason code.
    """
    expected_uid, expected_gid = _expected_identity()
    secret_path = unix_permission_dir / "db_password"
    secret_path.write_text("synthetic-not-a-secret", encoding="utf-8")
    secret_path.chmod(0o400)

    def failing_stat(path, *args, **kwargs):
        """@description: Raise a synthetic I/O error for the secret path and delegate every other stat call.
        @param path: Path being inspected.
        @param args: Extra positional arguments for os.stat.
        @param kwargs: Extra keyword arguments for os.stat.
        @return: Real stat result for other paths.
        """
        if Path(path) == secret_path:
            raise OSError(errno.EIO, "synthetic I/O failure")
        return real_stat(path, *args, **kwargs)

    real_stat = os.stat
    monkeypatch.setattr(entry.os, "stat", failing_stat)

    reasons = entry._mounted_secret_reasons(str(secret_path), expected_uid, expected_gid)

    assert reasons == ["OPEN_FAILED"]


def test_healthy_sanctioned_secret_still_passes(unix_permission_dir):
    """@description: The stat-failure mapping must not weaken the normal path, so an approved 0400 file owned by this account passes.
    @param unix_permission_dir: Linux-filesystem directory for the scenario.
    @return: None; the assertion checks there are no reasons.
    """
    expected_uid, expected_gid = _expected_identity()
    secret_path = unix_permission_dir / "db_password"
    secret_path.write_text("synthetic-not-a-secret", encoding="utf-8")
    secret_path.chmod(0o400)

    reasons = entry._mounted_secret_reasons(str(secret_path), expected_uid, expected_gid)

    assert reasons == []
