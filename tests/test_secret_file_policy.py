"""Verify the pure permission rules for host-side and container-mounted secret files.

Uses simulated uid, gid and mode values only; no real file, Docker or Compose command is involved.
"""

import pytest

from bot.secret_file_policy import (
    ContainerMountedFile,
    ContainerProcess,
    HostSourceFile,
    SecretFileReason,
    build_host_repair_guidance,
    evaluate_container_mounted_file,
    evaluate_host_source_file,
)

EXPECTED_OWNER_UID = 1001  # [expected deployer owner]: Synthetic deployment account used by the host-side check.
EXPECTED_SECRET_GROUP_GID = 2001  # [expected secret group]: Synthetic group dedicated to reading the secret.
CONTAINER_PROCESS_UID = 10001  # [container process uid]: Synthetic non-root uid the bot runs as inside the container.
CONTAINER_PROCESS_GID = 10001  # [container process gid]: Synthetic group the container process belongs to.
OTHER_HOST_GID = 3001  # [unrelated group]: Synthetic group that is neither the secret group nor the container group.


def host_file(mode: int, owner_uid: int = EXPECTED_OWNER_UID, group_gid: int = EXPECTED_SECRET_GROUP_GID) -> HostSourceFile:
    """@description: Build a simulated host source file so the host rules can be checked without touching disk.
    @param mode: Permission bits, such as 0o440 or 0o600.
    @param owner_uid: Simulated owner uid of the host file.
    @param group_gid: Simulated group gid of the host file.
    @return: Host file attributes for the evaluator.
    """
    return HostSourceFile(owner_uid=owner_uid, group_gid=group_gid, mode=mode)


def host_decision(mode: int, owner_uid: int = EXPECTED_OWNER_UID, group_gid: int = EXPECTED_SECRET_GROUP_GID):
    """@description: Evaluate a simulated host file against the configured deployer owner and secret group.
    @param mode: Permission bits under test.
    @param owner_uid: Simulated owner uid of the host file.
    @param group_gid: Simulated group gid of the host file.
    @return: Decision from the host-side evaluator.
    """
    return evaluate_host_source_file(
        host_file(mode, owner_uid, group_gid),
        expected_owner_uid=EXPECTED_OWNER_UID,
        expected_group_gid=EXPECTED_SECRET_GROUP_GID,
    )


def container_process(uid: int = CONTAINER_PROCESS_UID, group_ids: frozenset[int] = frozenset({CONTAINER_PROCESS_GID})) -> ContainerProcess:
    """@description: Describe the simulated process identity inside the container.
    @param uid: Effective uid of the process.
    @param group_ids: Group ids the process belongs to, including its primary group.
    @return: Process identity for the container evaluator.
    """
    return ContainerProcess(effective_uid=uid, group_ids=group_ids)


def mounted_file(mode: int, owner_uid: int = 0, group_gid: int = CONTAINER_PROCESS_GID, is_blank: bool = False) -> ContainerMountedFile:
    """@description: Build a simulated mounted secret file so the container rules can be checked without a mount.
    @param mode: Permission bits of the mounted file.
    @param owner_uid: Uid that owns the mounted file as the container sees it.
    @param group_gid: Gid that owns the mounted file as the container sees it.
    @param is_blank: Whether the caller found the file empty or whitespace-only after reading it.
    @return: Mounted file attributes for the evaluator.
    """
    return ContainerMountedFile(owner_uid=owner_uid, group_gid=group_gid, mode=mode, is_blank=is_blank)


# Scenario A: host-side source file


@pytest.mark.parametrize("mode", [0o604, 0o601, 0o607])
def test_host_file_with_any_other_access_is_refused(mode):
    """@description: Refuse a host secret that any unrelated account can read, write or execute.
    @param mode: Permission bits that grant access to other users.
    @return: None; the assertion checks the refusal reason.
    """
    decision = host_decision(mode)

    assert not decision.passed
    assert SecretFileReason.OTHER_ACCESS in decision.reasons


@pytest.mark.parametrize("mode", [0o620, 0o660])
def test_host_file_with_group_write_is_refused(mode):
    """@description: Refuse a host secret whose group could alter the credential.
    @param mode: Permission bits with group write enabled.
    @return: None; the assertion checks the refusal reason.
    """
    decision = host_decision(mode)

    assert not decision.passed
    assert SecretFileReason.GROUP_WRITABLE in decision.reasons


def test_host_file_with_unexpected_owner_is_refused():
    """@description: Refuse a host secret that the deployment account does not own.
    @param: none
    @return: None; the assertion checks the refusal reason.
    """
    decision = host_decision(0o600, owner_uid=9999)

    assert not decision.passed
    assert SecretFileReason.UNEXPECTED_OWNER in decision.reasons


def test_group_read_for_undedicated_group_is_refused_not_warned():
    """@description: Refuse group-readable host secrets whose group is not the designated secret group.
    @param: none
    @return: None; the assertion checks the refusal reason and that the decision is not a pass.
    """
    decision = host_decision(0o440, group_gid=OTHER_HOST_GID)

    assert not decision.passed
    assert SecretFileReason.GROUP_READ_UNDEDICATED in decision.reasons


@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_owner_only_host_file_passes(mode):
    """@description: Accept the owner-only permissions that the deployment checklist recommends.
    @param mode: Owner-only permission bits.
    @return: None; the assertion checks the decision passes without reasons.
    """
    decision = host_decision(mode, group_gid=OTHER_HOST_GID)

    assert decision.passed
    assert decision.reasons == ()


def test_host_file_0440_with_designated_group_passes():
    """@description: Accept the 0440 layout where only the designated secret group can read the file.
    @param: none
    @return: None; the assertion checks the decision passes.
    """
    decision = host_decision(0o440)

    assert decision.passed


def test_host_check_refuses_when_expected_owner_is_not_configured():
    """@description: Never let an unset expected owner pass by accident, even for an otherwise safe 0600 file.
    @param: none
    @return: None; the assertion checks the refusal reason.
    """
    decision = evaluate_host_source_file(
        host_file(0o600),
        expected_owner_uid=None,
        expected_group_gid=EXPECTED_SECRET_GROUP_GID,
    )

    assert not decision.passed
    assert SecretFileReason.EXPECTED_OWNER_UNCONFIGURED in decision.reasons


def test_host_check_refuses_when_expected_group_is_not_configured():
    """@description: Never let an unset expected secret group pass by accident.
    @param: none
    @return: None; the assertion checks the refusal reason.
    """
    decision = evaluate_host_source_file(
        host_file(0o600),
        expected_owner_uid=EXPECTED_OWNER_UID,
        expected_group_gid=None,
    )

    assert not decision.passed
    assert SecretFileReason.EXPECTED_GROUP_UNCONFIGURED in decision.reasons


def test_repair_guidance_lists_identity_and_commands_without_secret_material():
    """@description: Give the deployer a fix path that shows identity and permission bits but never file content.
    @param: none
    @return: None; the assertions check the guidance content.
    """
    guidance = build_host_repair_guidance(
        host_file(0o644, owner_uid=9999, group_gid=OTHER_HOST_GID),
        expected_owner_uid=EXPECTED_OWNER_UID,
        expected_group_gid=EXPECTED_SECRET_GROUP_GID,
    )

    assert "9999" in guidance
    assert "0644" in guidance
    assert f"{EXPECTED_OWNER_UID}:{EXPECTED_SECRET_GROUP_GID}" in guidance
    assert "chmod 0440" in guidance


def test_repair_guidance_marks_unset_expectations_instead_of_guessing():
    """@description: Show that an unset expected identity is reported as unset rather than filled with a default.
    @param: none
    @return: None; the assertion checks the placeholder text.
    """
    guidance = build_host_repair_guidance(
        host_file(0o600),
        expected_owner_uid=None,
        expected_group_gid=None,
    )

    assert "未設定" in guidance


def test_repair_command_template_uses_placeholders_when_expectations_are_unset():
    """@description: Keep the chown template executable by using placeholders instead of the status word for unset identities.
    @param: none
    @return: None; the assertions check the command line uses placeholders and never the status text.
    """
    guidance = build_host_repair_guidance(
        host_file(0o600),
        expected_owner_uid=None,
        expected_group_gid=None,
    )
    chown_line = next(line for line in guidance.splitlines() if "chown" in line)

    assert chown_line.strip().startswith("chown <預期擁有者 uid>:<預期群組 gid> ")
    assert "未設定" not in chown_line


# Scenario B: container-mounted secret file


def test_container_root_process_is_refused_even_with_owner_only_mode():
    """@description: Require a non-root container process, so root is refused even for a 0600 mount.
    @param: none
    @return: None; the assertion checks the refusal reason.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o600, owner_uid=0),
        container_process(uid=0, group_ids=frozenset({0})),
    )

    assert not decision.passed
    assert SecretFileReason.RUNS_AS_ROOT in decision.reasons


def test_container_other_access_is_refused():
    """@description: Refuse a mounted secret readable by unrelated accounts.
    @param: none
    @return: None; the assertion checks the refusal reason.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o604, owner_uid=CONTAINER_PROCESS_UID),
        container_process(),
    )

    assert not decision.passed
    assert SecretFileReason.OTHER_ACCESS in decision.reasons


def test_container_0440_with_container_group_passes():
    """@description: Accept the intended 0440 mount that lets a non-root container group read the secret.
    @param: none
    @return: None; the assertion checks the decision passes.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o440, owner_uid=0, group_gid=CONTAINER_PROCESS_GID),
        container_process(),
    )

    assert decision.passed
    assert decision.reasons == ()


def test_container_owner_only_file_owned_by_container_user_passes():
    """@description: Accept a 0600 mount when the container process is its owner.
    @param: none
    @return: None; the assertion checks the decision passes.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o600, owner_uid=CONTAINER_PROCESS_UID, group_gid=OTHER_HOST_GID),
        container_process(),
    )

    assert decision.passed


def test_container_0600_owned_by_other_uid_is_not_readable_not_leaked():
    """@description: Report a readability problem, not a leak, when the mount is owner-only for another uid.
    @param: none
    @return: None; the assertion checks the reason is NOT_READABLE and not OTHER_ACCESS.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o600, owner_uid=0, group_gid=OTHER_HOST_GID),
        container_process(),
    )

    assert not decision.passed
    assert SecretFileReason.NOT_READABLE in decision.reasons
    assert SecretFileReason.OTHER_ACCESS not in decision.reasons


def test_container_group_bit_without_membership_is_not_readable():
    """@description: Refuse a group-only 0440 mount when the process does not belong to that group.
    @param: none
    @return: None; the assertion checks the refusal reason.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o440, owner_uid=0, group_gid=OTHER_HOST_GID),
        container_process(),
    )

    assert not decision.passed
    assert SecretFileReason.NOT_READABLE in decision.reasons


def test_container_supplementary_group_membership_grants_group_read():
    """@description: Accept group read when the mounted file's gid is one of the process's supplementary groups.
    @param: none
    @return: None; the assertion checks the decision passes.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o440, owner_uid=0, group_gid=OTHER_HOST_GID),
        container_process(group_ids=frozenset({CONTAINER_PROCESS_GID, OTHER_HOST_GID})),
    )

    assert decision.passed


def test_container_empty_file_is_refused():
    """@description: Refuse an empty or whitespace-only mounted secret before the service tries to use it.
    @param: none
    @return: None; the assertion checks the refusal reason.
    """
    decision = evaluate_container_mounted_file(
        mounted_file(0o440, owner_uid=0, is_blank=True),
        container_process(),
    )

    assert not decision.passed
    assert SecretFileReason.EMPTY in decision.reasons


def test_reason_codes_are_stable_identifiers_without_paths_or_values():
    """@description: Keep reason codes as fixed identifiers so logs and messages cannot carry file paths or secret text.
    @param: none
    @return: None; the assertion checks each code is an upper-case identifier.
    """
    for reason in SecretFileReason:
        assert reason.value == reason.value.upper()
        assert "/" not in reason.value and " " not in reason.value


# --- sanctioned mount: 0400, UID 10001, GID check, searchable parent, real read probe -----------------------------------------
# The sanctioned scheme is owner-only 0400 owned by the expected numeric uid. 0440 stays a lower-level case, never the default.

from bot import secret_file_policy as policy  # noqa: E402  (new names are referenced inside tests so each red fails on its own reason)


def _parent(mode: int = 0o755, owner_uid: int = 0, group_gid: int = 0) -> object:
    """@description: Build a simulated parent directory of the mounted secret.
    @param mode: Permission bits of the directory.
    @param owner_uid: Simulated owner uid of the directory.
    @param group_gid: Simulated group gid of the directory.
    @return: Directory attributes for the sanctioned evaluator.
    """
    return policy.MountedDirectory(owner_uid=owner_uid, group_gid=group_gid, mode=mode)


def _sanctioned(mode: int = 0o400, *, owner_uid: int = CONTAINER_PROCESS_UID, group_gid: int = CONTAINER_PROCESS_GID,
                parent: object | None = None, opens: bool = True) -> object:
    """@description: Evaluate one mounted secret under the sanctioned scheme with the synthetic container process.
    @param mode: Permission bits of the mounted file.
    @param owner_uid: Numeric owner of the mounted file.
    @param group_gid: Numeric group of the mounted file.
    @param parent: Parent directory attributes; defaults to a world-searchable root-owned directory.
    @param opens: Whether the non-root read-only open probe succeeded.
    @return: Sanctioned-mount decision.
    """
    return policy.evaluate_sanctioned_mount(
        mounted_file(mode, owner_uid=owner_uid, group_gid=group_gid),
        parent if parent is not None else _parent(),
        container_process(),
        opens_read_only=opens,
        expected_owner_uid=CONTAINER_PROCESS_UID,
        expected_group_gid=CONTAINER_PROCESS_GID,
    )


def test_sanctioned_owner_only_mount_with_searchable_parent_and_readable_passes():
    """@description: The approved 0400 mount owned by the expected uid, under a searchable parent and readable by the process, passes.
    @return: None.
    """
    decision = _sanctioned()
    assert decision.passed and decision.reasons == ()


def test_sanctioned_scheme_refuses_0440_even_when_the_group_matches():
    """@description: 0440 is not the sanctioned default, so a group-readable mount is refused by the sanctioned evaluator.
    @return: None.
    """
    decision = _sanctioned(0o440)
    assert not decision.passed
    assert policy.SecretFileReason.NOT_SANCTIONED_MODE in decision.reasons


def test_sanctioned_scheme_refuses_a_file_owned_by_a_different_uid():
    """@description: The numeric owner must be the expected uid, so a file owned by another account is refused.
    @return: None.
    """
    decision = _sanctioned(owner_uid=0)
    assert not decision.passed
    assert policy.SecretFileReason.UNEXPECTED_OWNER in decision.reasons


def test_sanctioned_scheme_refuses_a_file_whose_numeric_group_differs():
    """@description: The numeric group must be the expected gid, checked separately from the uid.
    @return: None.
    """
    decision = _sanctioned(group_gid=CONTAINER_PROCESS_GID + 1)
    assert not decision.passed
    assert policy.SecretFileReason.GROUP_UNEXPECTED in decision.reasons


def test_sanctioned_scheme_refuses_an_unsearchable_parent_directory():
    """@description: A parent directory the process cannot search blocks the read even when the file itself is readable, so it is refused.
    @return: None.
    """
    decision = _sanctioned(parent=_parent(mode=0o700, owner_uid=0, group_gid=0))
    assert not decision.passed
    assert policy.SecretFileReason.PARENT_NOT_SEARCHABLE in decision.reasons


def test_sanctioned_scheme_refuses_when_the_non_root_read_probe_fails():
    """@description: A read-only open that fails for the non-root process is refused, so a permission the mode bits claim but the kernel denies is caught.
    @return: None.
    """
    decision = _sanctioned(opens=False)
    assert not decision.passed
    assert policy.SecretFileReason.OPEN_FAILED in decision.reasons


def test_sanctioned_scheme_reports_the_container_rules_it_inherits():
    """@description: Root-run processes stay refused through the inherited container rules, not only through the new checks.
    @return: None.
    """
    decision = policy.evaluate_sanctioned_mount(
        mounted_file(0o400, owner_uid=CONTAINER_PROCESS_UID, group_gid=CONTAINER_PROCESS_GID),
        _parent(),
        ContainerProcess(effective_uid=0, group_ids=frozenset({0})),
        opens_read_only=True,
        expected_owner_uid=CONTAINER_PROCESS_UID,
        expected_group_gid=CONTAINER_PROCESS_GID,
    )
    assert policy.SecretFileReason.RUNS_AS_ROOT in decision.reasons
