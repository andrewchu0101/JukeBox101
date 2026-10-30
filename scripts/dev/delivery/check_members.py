"""Gate a delivery tarball before extraction.

Inspects every member through the tarfile index (safe for any file name) and fails on absolute paths,
path traversal, entries outside the package root, links or special files.
Exit code: 0 = all members acceptable, 1 = rejected member found, 2 = usage error.
"""
import sys
import tarfile
from pathlib import PurePosixPath


def member_violations(member: tarfile.TarInfo, package_root: str) -> list[str]:
    """@description: Decide whether one archive entry could escape or smuggle non-regular content on extraction.
    @param member: Archive entry from the tarball index.
    @param package_root: The single top-level directory every entry must live under.
    @return: Violation names; empty when the entry is acceptable.
    """
    entry_path = PurePosixPath(member.name)
    violations = []
    if entry_path.is_absolute():
        violations.append("absolute_path")
    if ".." in entry_path.parts:
        violations.append("path_traversal")
    if not entry_path.parts or entry_path.parts[0] != package_root:
        violations.append("outside_package_root")
    if member.issym() or member.islnk():
        violations.append("link")
    if not (member.isfile() or member.isdir()):
        violations.append("special_or_non_regular")
    return violations


def main() -> int:
    """@description: Command-line boundary that stops the pipeline when any archive entry is unsafe.
    @param: none
    @return: Process exit code.
    """
    if len(sys.argv) != 3:
        print("usage: check_members.py <archive.tar.gz> <package-root>", file=sys.stderr)
        return 2
    archive_path, package_root = sys.argv[1], sys.argv[2]
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
    rejected = [(member.name, member_violations(member, package_root)) for member in members]
    rejected = [(name, violations) for name, violations in rejected if violations]
    for name, violations in rejected:
        print(f"REJECT {name!r}: {','.join(violations)}", file=sys.stderr)
    print(f"members={len(members)} rejected={len(rejected)}")
    return 1 if rejected or not members else 0


if __name__ == "__main__":
    sys.exit(main())
