"""Verify the host-side pre-deployment check for secret source files.

Runs scripts/deploy/check-host-secrets.sh against real temporary files whose metadata is set by the test.
Secret-looking content is fake and never expected to appear in output.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = PROJECT_ROOT / "scripts" / "deploy" / "check-host-secrets.sh"
# [fake secret content]: Synthetic value with characters that would be dangerous to echo; it must never reach output.
FAKE_SECRET_CONTENT = "FAKE-secret-%s#假值\"'\\"
CURRENT_UID = os.getuid()
CURRENT_GID = os.getgid()


def make_secret_file(directory: Path, mode: int) -> Path:
    """@description: Create a temporary secret file owned by the test user with the requested permission bits.
    @param directory: Temporary directory provided by pytest.
    @param mode: Permission bits to apply, such as 0o440.
    @return: Path to the created file.
    """
    secret_path = directory / "db_password"
    secret_path.write_text(FAKE_SECRET_CONTENT, encoding="utf-8")
    os.chmod(secret_path, mode)
    return secret_path


def run_check(secret_path: Path, expected_owner_uid: str | None = str(CURRENT_UID), expected_group_gid: str | None = str(CURRENT_GID), extra_env: dict | None = None):
    """@description: Execute the deployment check the way the deployer will, passing expectations only through the environment.
    @param secret_path: Host source file to inspect.
    @param expected_owner_uid: Value for SECRET_EXPECTED_UID; None leaves it unset.
    @param expected_group_gid: Value for SECRET_EXPECTED_GID; None leaves it unset.
    @param extra_env: Additional environment entries, such as a PATH override for stubs.
    @return: Completed process with captured stdout and stderr.
    """
    environment = {"PATH": os.environ["PATH"], "PYTHON": sys.executable}
    if expected_owner_uid is not None:
        environment["SECRET_EXPECTED_UID"] = expected_owner_uid
    if expected_group_gid is not None:
        environment["SECRET_EXPECTED_GID"] = expected_group_gid
    environment.update(extra_env or {})
    return subprocess.run(
        ["bash", str(CHECK_SCRIPT), str(secret_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_expected_owner_fails_with_configuration_message(tmp_path):
    """@description: Refuse to pass when the deployer has not stated which owner account is expected.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check exit status and the explanation.
    """
    secret_path = make_secret_file(tmp_path, 0o440)

    result = run_check(secret_path, expected_owner_uid=None)

    assert result.returncode == 1
    assert "EXPECTED_OWNER_UNCONFIGURED" in result.stdout
    assert "未設定" in result.stdout


def test_missing_expected_group_fails_with_configuration_message(tmp_path):
    """@description: Refuse to pass when the deployer has not stated which secret group is expected.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check exit status and the explanation.
    """
    secret_path = make_secret_file(tmp_path, 0o600)

    result = run_check(secret_path, expected_group_gid=None)

    assert result.returncode == 1
    assert "EXPECTED_GROUP_UNCONFIGURED" in result.stdout


def test_unset_expectations_produce_an_executable_placeholder_template(tmp_path):
    """@description: Confirm the refusal output for unset expectations never prints the status word inside a command.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check the chown template uses placeholders.
    """
    secret_path = make_secret_file(tmp_path, 0o640)

    result = run_check(secret_path, expected_owner_uid=None, expected_group_gid=None)
    chown_lines = [line for line in result.stdout.splitlines() if "chown" in line]

    assert result.returncode == 1
    assert chown_lines == ["  chown <預期擁有者 uid>:<預期群組 gid> <秘密檔案路徑>"]


def test_other_readable_file_is_refused_with_current_and_expected_values(tmp_path):
    """@description: Refuse a host secret that other accounts can read, and show the current and expected identity.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check the reason and the repair guidance values.
    """
    secret_path = make_secret_file(tmp_path, 0o604)

    result = run_check(secret_path)

    assert result.returncode == 1
    assert "OTHER_ACCESS" in result.stdout
    assert "0604" in result.stdout
    assert f"uid={CURRENT_UID}" in result.stdout
    assert f"gid={CURRENT_GID}" in result.stdout


def test_group_read_for_undedicated_group_is_refused_and_names_the_group_mismatch(tmp_path):
    """@description: Refuse group-readable secrets whose group is not the designated one, and say so in the guidance.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check the reason and that the expected group is shown.
    """
    secret_path = make_secret_file(tmp_path, 0o640)
    other_group_gid = CURRENT_GID + 1

    result = run_check(secret_path, expected_group_gid=str(other_group_gid))

    assert result.returncode == 1
    assert "GROUP_READ_UNDEDICATED" in result.stdout
    assert f"gid={other_group_gid}" in result.stdout


def test_owner_mismatch_is_refused(tmp_path):
    """@description: Refuse a host secret that the expected deployment account does not own.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check the reason.
    """
    secret_path = make_secret_file(tmp_path, 0o600)

    result = run_check(secret_path, expected_owner_uid=str(CURRENT_UID + 1))

    assert result.returncode == 1
    assert "UNEXPECTED_OWNER" in result.stdout


@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_owner_only_file_passes(tmp_path, mode):
    """@description: Accept owner-only host secrets that the expected account owns.
    @param tmp_path: Temporary directory for the secret file.
    @param mode: Owner-only permission bits.
    @return: None; the assertions check exit status 0.
    """
    secret_path = make_secret_file(tmp_path, mode)

    result = run_check(secret_path)

    assert result.returncode == 0, result.stdout


def test_0440_with_designated_group_passes(tmp_path):
    """@description: Accept the 0440 layout where only the designated secret group can read the file.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check exit status 0.
    """
    secret_path = make_secret_file(tmp_path, 0o440)

    result = run_check(secret_path)

    assert result.returncode == 0, result.stdout


def test_output_never_contains_the_secret_content(tmp_path):
    """@description: Prove that neither a pass nor a refusal prints anything from the secret file.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check both outputs.
    """
    refused_path = make_secret_file(tmp_path, 0o644)
    passed_path = tmp_path / "passing_secret"
    passed_path.write_text(FAKE_SECRET_CONTENT, encoding="utf-8")
    os.chmod(passed_path, 0o400)

    refused = run_check(refused_path)
    passed = run_check(passed_path)

    assert FAKE_SECRET_CONTENT not in refused.stdout + refused.stderr
    assert FAKE_SECRET_CONTENT not in passed.stdout + passed.stderr


def test_check_does_not_need_read_permission_on_the_file(tmp_path):
    """@description: Confirm the check inspects metadata only, so a file without read bits is still judged on ownership and mode.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertion checks the file is judged without being opened.
    """
    secret_path = make_secret_file(tmp_path, 0o000)

    result = run_check(secret_path)

    assert result.returncode == 0, result.stdout


def test_check_never_runs_chmod_chown_or_privilege_escalation(tmp_path):
    """@description: Use stub commands on PATH to prove a failing check does not repair, change owner or escalate.
    @param tmp_path: Temporary directory for the secret file and stub commands.
    @return: None; the assertions check that no stub was invoked and that the file mode is unchanged.
    """
    stub_directory = tmp_path / "stubs"
    stub_directory.mkdir()
    invocation_log = tmp_path / "invocations.log"
    for command_name in ("chmod", "chown", "sudo", "su"):
        stub_path = stub_directory / command_name
        stub_path.write_text(f"#!/bin/sh\necho {command_name} >> '{invocation_log}'\n", encoding="utf-8")
        os.chmod(stub_path, 0o755)
    secret_path = make_secret_file(tmp_path, 0o644)

    result = run_check(secret_path, extra_env={"PATH": f"{stub_directory}{os.pathsep}{os.environ['PATH']}"})

    assert result.returncode == 1
    assert not invocation_log.exists()
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o644


def test_script_text_contains_no_mutating_or_escalating_commands():
    """@description: Catch accidental repair or privilege commands in the script itself, not just in its output.
    @param: none
    @return: None; the assertion checks the script source is free of these command names.
    """
    script_text = CHECK_SCRIPT.read_text(encoding="utf-8")

    for forbidden_command in ("chmod", "chown", "sudo", "su "):
        assert forbidden_command not in script_text, forbidden_command


def test_missing_secret_file_reports_cannot_inspect_without_contents(tmp_path):
    """@description: Stop with a distinct exit status when the file cannot be inspected, without printing secret text.
    @param tmp_path: Temporary directory that does not contain the referenced file.
    @return: None; the assertions check exit status 2 and the absence of secret text.
    """
    result = run_check(tmp_path / "does-not-exist")

    assert result.returncode == 2
    assert FAKE_SECRET_CONTENT not in result.stdout + result.stderr


def test_retired_expected_names_are_not_read(tmp_path):
    """@description: Confirm only the approved SECRET_EXPECTED_UID and SECRET_EXPECTED_GID names configure the check.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check that the old names leave the expectations unset.
    """
    secret_path = make_secret_file(tmp_path, 0o440)
    environment = {
        "PATH": os.environ["PATH"],
        "PYTHON": sys.executable,
        "SECRET_EXPECTED_OWNER_UID": str(CURRENT_UID),
        "SECRET_EXPECTED_GROUP_GID": str(CURRENT_GID),
    }

    result = subprocess.run(
        ["bash", str(CHECK_SCRIPT), str(secret_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "EXPECTED_OWNER_UNCONFIGURED" in result.stdout
    assert "EXPECTED_GROUP_UNCONFIGURED" in result.stdout


def test_missing_path_argument_is_a_usage_error():
    """@description: Refuse a run without a file argument so the check never silently inspects nothing.
    @param: none
    @return: None; the assertions check the usage exit status.
    """
    result = subprocess.run(
        ["bash", str(CHECK_SCRIPT)],
        cwd=PROJECT_ROOT,
        env={"PATH": os.environ["PATH"], "PYTHON": sys.executable},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "用法" in result.stderr
