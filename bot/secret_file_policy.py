"""Decide whether secret files are safe to use, separately for host source files and container mounts.

Receives simulated or real uid, gid and permission bits only; never reads file content, calls Docker or prints secret values.
"""

import argparse
import os
import stat
import sys
from dataclasses import dataclass
from enum import StrEnum

OTHER_PERMISSION_BITS = 0o007  # [other access bits]: Read, write and execute granted to users outside the owner and group.
GROUP_WRITE_BIT = 0o020  # [group write bit]: Lets the group alter the credential.
GROUP_READ_BIT = 0o040  # [group read bit]: Lets the group read the credential.
OWNER_READ_BIT = 0o400  # [owner read bit]: Lets the file owner read the credential.
OTHER_READ_BIT = 0o004  # [other read bit]: Lets unrelated users read the credential.
SANCTIONED_SECRET_MODE = 0o400  # [sanctioned mode]: Owner-only read for the expected uid; group-readable 0440 is never the default.
OWNER_EXECUTE_BIT = 0o100  # [owner search bit]: Lets the directory owner traverse a parent directory.
GROUP_EXECUTE_BIT = 0o010  # [group search bit]: Lets the directory group traverse a parent directory.
OTHER_EXECUTE_BIT = 0o001  # [other search bit]: Lets unrelated users traverse a parent directory.


class SecretFileReason(StrEnum):
    """Stable reason codes that explain a refusal without naming paths or secret values."""

    OTHER_ACCESS = "OTHER_ACCESS"
    GROUP_WRITABLE = "GROUP_WRITABLE"
    UNEXPECTED_OWNER = "UNEXPECTED_OWNER"
    GROUP_READ_UNDEDICATED = "GROUP_READ_UNDEDICATED"
    EXPECTED_OWNER_UNCONFIGURED = "EXPECTED_OWNER_UNCONFIGURED"
    EXPECTED_GROUP_UNCONFIGURED = "EXPECTED_GROUP_UNCONFIGURED"
    RUNS_AS_ROOT = "RUNS_AS_ROOT"
    NOT_READABLE = "NOT_READABLE"
    FILE_MISSING = "FILE_MISSING"
    PATH_NOT_SEARCHABLE = "PATH_NOT_SEARCHABLE"
    EMPTY = "EMPTY"
    NOT_SANCTIONED_MODE = "NOT_SANCTIONED_MODE"
    GROUP_UNEXPECTED = "GROUP_UNEXPECTED"
    PARENT_NOT_SEARCHABLE = "PARENT_NOT_SEARCHABLE"
    OPEN_FAILED = "OPEN_FAILED"


@dataclass(frozen=True)
class HostSourceFile:
    """Attributes of a deployer's source secret file on the host, as reported by the file system."""

    owner_uid: int  # [host owner uid]: Account that owns the source file on the deployment host.
    group_gid: int  # [host group gid]: Group that owns the source file on the deployment host.
    mode: int  # [host permission bits]: Permission bits such as 0o600 or 0o440.


@dataclass(frozen=True)
class ContainerMountedFile:
    """Attributes of a secret file as seen from inside the container after mounting."""

    owner_uid: int  # [mounted owner uid]: Uid that owns the mounted file inside the container.
    group_gid: int  # [mounted group gid]: Gid that owns the mounted file inside the container.
    mode: int  # [mounted permission bits]: Permission bits the container process observes.
    is_blank: bool  # [blank content flag]: True when the caller found the file empty or whitespace-only.


@dataclass(frozen=True)
class ContainerProcess:
    """Identity of the process that will read the mounted secret inside the container."""

    effective_uid: int  # [process uid]: Effective uid; must not be 0 for the bot.
    group_ids: frozenset[int]  # [process groups]: Primary and supplementary group ids of the process.


@dataclass(frozen=True)
class MountedDirectory:
    """Attributes of the directory that holds a mounted secret, checked for traversal by the container process."""

    owner_uid: int  # [directory owner uid]: Uid that owns the parent directory inside the container.
    group_gid: int  # [directory group gid]: Gid that owns the parent directory inside the container.
    mode: int  # [directory permission bits]: Bits that decide whether the process may search the directory.


@dataclass(frozen=True)
class SecretFileDecision:
    """Outcome of a secret-file check with stable reasons for every refusal."""

    passed: bool  # [decision outcome]: True only when no refusal reason applies.
    reasons: tuple[SecretFileReason, ...]  # [refusal reasons]: Empty when the file passes.


def evaluate_host_source_file(
    host_file: HostSourceFile,
    expected_owner_uid: int | None,
    expected_group_gid: int | None,
) -> SecretFileDecision:
    """@description: Refuse deployment source secrets that other accounts could read or that are not owned by the deployer.
    @param host_file: Host-side attributes of the source secret file.
    @param expected_owner_uid: Deployer account allowed to own the file; None means not configured and refuses.
    @param expected_group_gid: Dedicated secret group allowed to read the file; None means not configured and refuses.
    @return: Decision containing every refusal reason that applies.
    """
    reasons = []
    if host_file.mode & OTHER_PERMISSION_BITS:
        reasons.append(SecretFileReason.OTHER_ACCESS)
    if host_file.mode & GROUP_WRITE_BIT:
        reasons.append(SecretFileReason.GROUP_WRITABLE)
    if host_file.mode & 0o777 != SANCTIONED_SECRET_MODE:
        reasons.append(SecretFileReason.NOT_SANCTIONED_MODE)
    reasons.extend(_host_identity_reasons(host_file, expected_owner_uid, expected_group_gid))
    return SecretFileDecision(passed=not reasons, reasons=tuple(reasons))


def _host_identity_reasons(
    host_file: HostSourceFile,
    expected_owner_uid: int | None,
    expected_group_gid: int | None,
) -> list[SecretFileReason]:
    """@description: Compare the file's owner and group with configured expectations, refusing when either is unset.
    @param host_file: Host-side attributes of the source secret file.
    @param expected_owner_uid: Deployer account allowed to own the file; None means not configured.
    @param expected_group_gid: Dedicated secret group allowed to read the file; None means not configured.
    @return: Refusal reasons for unset expectations, a mismatched owner, or group read for another group.
    """
    reasons = []
    if expected_owner_uid is None:
        reasons.append(SecretFileReason.EXPECTED_OWNER_UNCONFIGURED)
    elif host_file.owner_uid != expected_owner_uid:
        reasons.append(SecretFileReason.UNEXPECTED_OWNER)
    if expected_group_gid is None:
        reasons.append(SecretFileReason.EXPECTED_GROUP_UNCONFIGURED)
    elif host_file.mode & GROUP_READ_BIT and host_file.group_gid != expected_group_gid:
        reasons.append(SecretFileReason.GROUP_READ_UNDEDICATED)
    return reasons


def build_host_repair_guidance(
    host_file: HostSourceFile,
    expected_owner_uid: int | None,
    expected_group_gid: int | None,
) -> str:
    """@description: Tell the deployer how to fix ownership and mode without printing anything from the secret file.
    @param host_file: Host-side attributes that failed the check.
    @param expected_owner_uid: Configured deployer owner, or None when not configured.
    @param expected_group_gid: Configured secret group, or None when not configured.
    @return: Multi-line Chinese guidance listing identity, permission bits and command templates.
    """
    expected_owner_text = _describe_expected_identity(expected_owner_uid)
    expected_group_text = _describe_expected_identity(expected_group_gid)
    return "\n".join([
        f"目前：擁有者 uid={host_file.owner_uid}、群組 gid={host_file.group_gid}、權限 {host_file.mode:04o}",
        f"預期：擁有者 uid={expected_owner_text}、秘密群組 gid={expected_group_text}",
        "修復範本（以實際路徑取代 <秘密檔案路徑>）：",
        f"  chown {_describe_command_identity(expected_owner_uid, '<預期擁有者 uid>')}:"
        f"{_describe_command_identity(expected_group_gid, '<預期群組 gid>')} <秘密檔案路徑>",
        "  chmod 0400 <秘密檔案路徑>   # 唯一核准的權限；不得改為 0440、0600 或其他值",
    ])


def _describe_command_identity(identity: int | None, placeholder: str) -> str:
    """@description: Keep the copy-paste command executable when an identity is unset by showing a placeholder.
    @param identity: Configured uid or gid, or None.
    @param placeholder: Angle-bracket placeholder the deployer replaces with the real number.
    @return: The configured number, or the placeholder when the identity is not configured.
    """
    return placeholder if identity is None else str(identity)


def _describe_expected_identity(identity: int | None) -> str:
    """@description: Show an unset expected identity as an explicit placeholder instead of a guessed default.
    @param identity: Configured uid or gid, or None.
    @return: The number as text, or the placeholder 未設定 when not configured.
    """
    return "未設定" if identity is None else str(identity)


def evaluate_container_mounted_file(mounted_file: ContainerMountedFile, process: ContainerProcess) -> SecretFileDecision:
    """@description: Confirm the bot process can read its mounted secret as a non-root user without exposing it to others.
    @param mounted_file: Attributes of the secret file as seen inside the container.
    @param process: Identity of the bot process that will read the file.
    @return: Decision containing every refusal reason that applies.
    """
    reasons = []
    if process.effective_uid == 0:
        reasons.append(SecretFileReason.RUNS_AS_ROOT)
    if mounted_file.mode & OTHER_PERMISSION_BITS:
        reasons.append(SecretFileReason.OTHER_ACCESS)
    if not _process_can_read(mounted_file, process):
        reasons.append(SecretFileReason.NOT_READABLE)
    if mounted_file.is_blank:
        reasons.append(SecretFileReason.EMPTY)
    return SecretFileDecision(passed=not reasons, reasons=tuple(reasons))


def _process_can_read(mounted_file: ContainerMountedFile, process: ContainerProcess) -> bool:
    """@description: Apply the owner, group, then other permission class that POSIX uses for this process.
    @param mounted_file: Attributes of the mounted secret file.
    @param process: Identity of the bot process.
    @return: True when the process falls into a permission class that grants read access.
    """
    if process.effective_uid == mounted_file.owner_uid:
        return bool(mounted_file.mode & OWNER_READ_BIT)
    if mounted_file.group_gid in process.group_ids:
        return bool(mounted_file.mode & GROUP_READ_BIT)
    return bool(mounted_file.mode & OTHER_READ_BIT)


EXIT_PASSED = 0  # [check passed]: The host source file meets every scenario A rule.
EXIT_REFUSED = 1  # [check refused]: At least one rule failed; guidance explains the repair without secret values.
EXIT_CANNOT_INSPECT = 2  # [check could not run]: Metadata was unreadable or the command was used incorrectly.


def read_host_file_attributes(secret_file_path: str) -> HostSourceFile:
    """@description: Read owner and permission bits from file metadata so the check never opens the secret.
    @param secret_file_path: Host path of the secret source file.
    @return: Host file attributes taken from os.stat, without reading file content.
    """
    file_status = os.stat(secret_file_path)
    return HostSourceFile(
        owner_uid=file_status.st_uid,
        group_gid=file_status.st_gid,
        mode=stat.S_IMODE(file_status.st_mode),
    )


def check_host_secret_file(
    secret_file_path: str,
    expected_owner_uid: int | None,
    expected_group_gid: int | None,
) -> tuple[int, str]:
    """@description: Run the deployment pre-check and produce a report that never contains file content.
    @param secret_file_path: Host path of the secret source file.
    @param expected_owner_uid: Deployer uid supplied by the operator; None means not configured.
    @param expected_group_gid: Secret group gid supplied by the operator; None means not configured.
    @return: Exit status and a report listing reasons and, when refused, the repair guidance.
    """
    host_file = read_host_file_attributes(secret_file_path)
    decision = evaluate_host_source_file(host_file, expected_owner_uid, expected_group_gid)
    if decision.passed:
        return EXIT_PASSED, f"通過：擁有者 uid={host_file.owner_uid}、群組 gid={host_file.group_gid}、權限 {host_file.mode:04o}"
    reason_text = ", ".join(decision.reasons)
    guidance = build_host_repair_guidance(host_file, expected_owner_uid, expected_group_gid)
    return EXIT_REFUSED, f"拒絕原因：{reason_text}\n{guidance}"


def _optional_uid_or_gid(raw_text: str) -> int | None:
    """@description: Treat an empty command-line value as not configured so no default identity is ever assumed.
    @param raw_text: Text passed for an expected uid or gid; empty means the operator did not set it.
    @return: Parsed integer identity, or None when the value is empty.
    """
    return int(raw_text) if raw_text.strip() else None


def main(argv: list[str] | None = None) -> int:
    """@description: Serve the deployment check command so the shell wrapper stays a thin launcher.
    @param argv: Command-line arguments; None reads sys.argv.
    @return: Process exit status for the deployer's terminal.
    """
    parser = argparse.ArgumentParser(description="檢查主機來源秘密檔案的擁有者與權限（不讀取內容）。")
    parser.add_argument("secret_file_path")
    parser.add_argument("--expected-owner-uid", default="")
    parser.add_argument("--expected-group-gid", default="")
    arguments = parser.parse_args(argv)
    try:
        exit_status, report = check_host_secret_file(
            arguments.secret_file_path,
            _optional_uid_or_gid(arguments.expected_owner_uid),
            _optional_uid_or_gid(arguments.expected_group_gid),
        )
    except OSError as inspection_error:
        print(f"無法檢查秘密來源檔的中繼資料（{type(inspection_error).__name__}）；請確認路徑與存取權限，不會自動提權。", file=sys.stderr)
        return EXIT_CANNOT_INSPECT
    print(report)
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())


def evaluate_sanctioned_mount(
    mounted_file: ContainerMountedFile,
    parent_directory: MountedDirectory,
    process: ContainerProcess,
    *,
    opens_read_only: bool,
    expected_owner_uid: int,
    expected_group_gid: int,
) -> SecretFileDecision:
    """@description: Apply the approved 0400 scheme with the expected numeric owner and group, a searchable parent, and a real non-root read probe.
    @param mounted_file: Attributes of the mounted secret as seen inside the container.
    @param parent_directory: Attributes of the directory that holds the secret.
    @param process: Identity of the bot process that will read the secret.
    @param opens_read_only: True when a read-only open by the process succeeded; the content is never read here.
    @param expected_owner_uid: Numeric uid the mounted secret must carry.
    @param expected_group_gid: Numeric gid the mounted secret must carry.
    @return: Decision containing every refusal reason that applies.
    """
    reasons = list(evaluate_container_mounted_file(mounted_file, process).reasons)
    if mounted_file.mode & 0o777 != SANCTIONED_SECRET_MODE:
        reasons.append(SecretFileReason.NOT_SANCTIONED_MODE)
    if mounted_file.owner_uid != expected_owner_uid:
        reasons.append(SecretFileReason.UNEXPECTED_OWNER)
    if mounted_file.group_gid != expected_group_gid:
        reasons.append(SecretFileReason.GROUP_UNEXPECTED)
    if not _process_can_search(parent_directory, process):
        reasons.append(SecretFileReason.PARENT_NOT_SEARCHABLE)
    if not opens_read_only:
        reasons.append(SecretFileReason.OPEN_FAILED)
    unique_reasons = tuple(dict.fromkeys(reasons))
    return SecretFileDecision(passed=not unique_reasons, reasons=unique_reasons)


def _process_can_search(parent_directory: MountedDirectory, process: ContainerProcess) -> bool:
    """@description: Apply the owner, group, then other permission class to decide whether the process may traverse the parent directory.
    @param parent_directory: Attributes of the directory that holds the secret.
    @param process: Identity of the bot process.
    @return: True when the process falls into a class that grants search access.
    """
    if process.effective_uid == parent_directory.owner_uid:
        return bool(parent_directory.mode & OWNER_EXECUTE_BIT)
    if parent_directory.group_gid in process.group_ids:
        return bool(parent_directory.mode & GROUP_EXECUTE_BIT)
    return bool(parent_directory.mode & OTHER_EXECUTE_BIT)

