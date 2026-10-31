"""Verify the disposable-lab diagnostics keep the lab password out of any failure output.

The module is loaded by path because its file name contains a hyphen; no Docker command is run.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "verify-fresh-postgres.py"


def load_script():
    """@description: Import the verification script as a module without executing its main entry.
    @param: none
    @return: Loaded module.
    """
    spec = importlib.util.spec_from_file_location("verify_fresh_postgres", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failure_output_never_contains_the_lab_password():
    """@description: Captured Docker output is shown only after the lab password is replaced, so a leaked argv or log line cannot reveal it.
    @param: none
    @return: None.
    """
    lab = load_script().DockerLab("17")
    leaked = f"POSTGRES_PASSWORD={lab.password} and again {lab.password}"
    assert lab.password not in lab.redact(leaked)
    assert lab.redact(leaked).count("<redacted>") == 2


def test_failure_output_is_bounded_to_the_tail():
    """@description: Only the last 2000 characters of Docker output are reported, so a noisy failure cannot flood the evidence.
    @param: none
    @return: None.
    """
    lab = load_script().DockerLab("18")
    shown = lab.redact("x" * 5000 + "END")
    assert len(shown) == 2000 and shown.endswith("END")


def test_unknown_postgres_major_is_refused_before_any_docker_call():
    """@description: Only the reviewed PostgreSQL majors can be requested, so an arbitrary image is never started.
    @param: none
    @return: None.
    """
    with pytest.raises(KeyError):
        load_script().DockerLab("16")


def test_the_runtime_check_receives_the_exact_expected_server_build():
    """@description: The in-image runtime check is told which exact build the lab started, so PostgreSQL 17 is verified as strictly as 18.
    @param: none
    @return: None.
    """
    module = load_script()
    assert "JB_EXPECTED_SERVER_VERSION_NUM=170011" in module.DockerLab("17").test_arguments()
    assert "JB_EXPECTED_SERVER_VERSION_NUM=180006" in module.DockerLab("18").test_arguments()


def test_the_runtime_check_defaults_to_the_reviewed_18_build_and_stays_exact():
    """@description: Without a declared build the runtime check still demands PostgreSQL 18.6 exactly, and never accepts a range.
    @param: none
    @return: None.
    """
    source = (SCRIPT.parent / "verify-runtime-postgres.py").read_text(encoding="utf-8")
    assert "JB_EXPECTED_SERVER_VERSION_NUM" in source and "'180006'" in source
    assert "!= EXPECTED_SERVER_VERSION_NUM" in source
