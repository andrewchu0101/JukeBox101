"""Offline re-verification of the Debian corresponding source shipped in sources/debian/.

For every source package in SOURCE-MAP.json: verifies the recorded InRelease with gpgv against the shipped
debian-archive-keyring, checks Sources.xz against the signed SHA-256, finds the exact (package, version) stanza, and
checks every shipped file's size and SHA-256 against it. Also checks that every copyleft binary in the scope report
maps to a shipped source, and that no unlisted files are present.
Usage: python3 verify_debian_sources.py <sources/debian dir> [<DEBIAN-SOURCE-SCOPE.json>]
Exit code: 0 = all verified, 1 = any problem, 2 = usage error.
"""
import hashlib
import json
import lzma
import re
import subprocess
import sys
from pathlib import Path

# Files in sources/debian/ that are part of this verification kit rather than source packages or index evidence.
KIT_FILES = {"SOURCE-MAP.json", "SOURCE-MAP.md", "README.md", "verify_debian_sources.py",
             "keyring/debian-archive-keyring.gpg", "keyring/TRUST-ANCHOR.json"}


def signed_text(index_dir: Path, keyring: Path) -> str:
    """@description: Return only the OpenPGP-verified content of an InRelease file, or fail.
    @param index_dir: Directory holding InRelease and Sources.xz for one suite snapshot.
    @param keyring: Shipped Debian archive keyring.
    @return: Signed InRelease text.
    """
    result = subprocess.run(["gpgv", "--status-fd", "2", "--keyring", str(keyring), "--output", "-",
                             str(index_dir / "InRelease")], capture_output=True)
    if result.returncode != 0 or b"[GNUPG:] VALIDSIG " not in result.stderr:
        raise ValueError(f"InRelease signature not valid: {index_dir.name}")
    return result.stdout.decode("utf-8")


def signed_sources(index_dir: Path, keyring: Path) -> dict:
    """@description: Parse a Sources index only after its SHA-256 matches the signed InRelease.
    @param index_dir: Directory holding InRelease and Sources.xz.
    @param keyring: Shipped Debian archive keyring.
    @return: (package, version) -> Checksums-Sha256 lines.
    """
    release = signed_text(index_dir, keyring).split("SHA256:", 1)[1]
    match = re.search(r"^ ([0-9a-f]{64})\s+(\d+)\s+main/source/Sources\.xz$", release, flags=re.MULTILINE)
    body = (index_dir / "Sources.xz").read_bytes()
    if not match or hashlib.sha256(body).hexdigest() != match.group(1) or len(body) != int(match.group(2)):
        raise ValueError(f"Sources.xz does not match signed InRelease: {index_dir.name}")
    entries = {}
    for stanza in lzma.decompress(body).decode("utf-8", "replace").split("\n\n"):
        package = re.search(r"^Package: (.+)$", stanza, flags=re.MULTILINE)
        version = re.search(r"^Version: (.+)$", stanza, flags=re.MULTILINE)
        checksums = re.search(r"^Checksums-Sha256:\n((?:[ \t].+\n?)+)", stanza, flags=re.MULTILINE)
        if package and version and checksums:
            entries[(package.group(1), version.group(1))] = {
                parts[2]: (int(parts[1]), parts[0]) for parts in (l.split() for l in checksums.group(1).splitlines()) if len(parts) == 3}
    return entries


def main() -> int:
    """@description: Command-line gate proving shipped Debian source is exact, complete and signed-checksum verified.
    @param: none
    @return: Process exit code.
    """
    if len(sys.argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    keyring = root / "keyring" / "debian-archive-keyring.gpg"
    source_map = json.loads((root / "SOURCE-MAP.json").read_text(encoding="utf-8"))
    problems, index_cache, expected_files, verified_files, verified_bytes = [], {}, set(), 0, 0
    for package in source_map["packages"]:
        index_dir = root / "indices" / package["index_evidence"]
        expected_files |= {f"indices/{package['index_evidence']}/InRelease", f"indices/{package['index_evidence']}/Sources.xz",
                           f"indices/{package['index_evidence']}/gpgv-status.txt"}
        try:
            if package["index_evidence"] not in index_cache:
                index_cache[package["index_evidence"]] = signed_sources(index_dir, keyring)
            entries = index_cache[package["index_evidence"]]
        except ValueError as failure:
            problems.append(str(failure))
            continue
        signed = entries.get((package["source"], package["version"]))
        if signed is None:
            problems.append(f"exact version absent from signed index: {package['source']} {package['version']}")
            continue
        if set(signed) != {f["name"] for f in package["files"]}:
            problems.append(f"file set differs from signed index: {package['source']} {package['version']}")
        for item in package["files"]:
            relative = f"{package['source']}_{package['version']}/{item['name']}"
            expected_files.add(relative)
            path = root / relative
            if not path.is_file():
                problems.append(f"missing source file: {relative}")
                continue
            data = path.read_bytes()
            if (len(data), hashlib.sha256(data).hexdigest()) != signed.get(item["name"]):
                problems.append(f"checksum mismatch vs signed index: {relative}")
                continue
            verified_files += 1
            verified_bytes += len(data)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    problems += [f"unexpected file: {p}" for p in sorted(actual - expected_files - KIT_FILES)]
    if len(sys.argv) == 3:
        scope = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        shipped = {(p["source"], p["version"]) for p in source_map["packages"]}
        for binary in (p for p in scope["packages"] if p["copyleft_mentions"]):
            if (binary["source"], binary["source_version"]) not in shipped:
                problems.append(f"copyleft binary without shipped source: {binary['binary']} -> {binary['source']} {binary['source_version']}")
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"source_packages={len(source_map['packages'])} files_verified={verified_files} bytes_verified={verified_bytes} "
          f"signed_indices={len(index_cache)} problems={len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
