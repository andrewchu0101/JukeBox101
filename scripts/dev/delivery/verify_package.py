"""Verify a delivery tar, safely extract it, and recheck every file, manifest and upstream source.

Usage: python3 verify_package.py <delivery.tar.gz> <fresh-extraction-dir>
"""
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

from package_v7 import digest


def main():
    archive, extraction = map(Path, sys.argv[1:])
    extraction.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as tar:
        seen = set()
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or path.parts[0] != "jukebox101-delivery-v7":
                raise ValueError("unsafe archive member")
            if member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError("duplicate or nonregular archive member")
            seen.add(member.name)
        tar.extractall(extraction, filter="data")
    root = extraction / "jukebox101-delivery-v7"
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    checksums = {}
    for line in (root / "SHA256SUMS").read_text().splitlines():
        checksum, relative = line.split("  ", 1)
        checksums[relative] = checksum
        if digest(root / relative) != checksum:
            raise ValueError(f"checksum mismatch: {relative}")
    assert actual == set(checksums) | {"SHA256SUMS"}
    manifest = json.loads((root / "SOURCE-MANIFEST.json").read_text())
    assert actual == {e["path"] for e in manifest["files"]} | {"SHA256SUMS", "SOURCE-MANIFEST.json"}
    for entry in manifest["files"]:
        path = root / entry["path"]
        assert path.stat().st_size == entry["size"] and digest(path) == entry["sha256"]
    assert not any(p.suffix.lower() == ".pdf" for p in root.rglob("*"))
    provenance = json.loads((root / "sources/python/PROVENANCE.json").read_text())
    assert digest(root / "sources/python" / provenance["file"]) == provenance["sha256"]
    subprocess.run([sys.executable, str(root / "sources/debian/verify_debian_sources.py"),
        str(root / "sources/debian"), str(root / "evidence/DEBIAN-SOURCE-SCOPE.json")], check=True)
    subprocess.run([sys.executable, str(Path(__file__).with_name("scan_pack.py")), str(root),
        str(extraction / "package-scan.json"), str(Path(__file__).with_name("classified-pack-baseline.json"))], check=True)
    print(f"VERIFIED: {len(actual)} files; exact manifest and SHA256SUMS sets; no PDF; no blocking/unclassified findings")
    print(root)


if __name__ == "__main__":
    main()
