"""Download the exact Debian source packages for the image's copyleft binaries with a signed SHA-256 chain.

Chain: InRelease (OpenPGP, verified by gpgv against the image's debian-archive-keyring) -> SHA-256 of
main/source/Sources.xz -> Checksums-Sha256 and sizes of every file of the exact source version -> downloaded bytes.
Only the gpgv-emitted signed text is parsed. Resumable; stops before downloading when the signed total size
exceeds the inventoried estimate by more than the allowed margin.
Exit code: 0 = every source version fetched and verified, 1 = any failure, 2 = usage error.
"""
import hashlib
import json
import lzma
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SNAPSHOT = "https://snapshot.debian.org"
# [primary index time]: just after the base image was created, when every installed version was published.
PRIMARY_TIMESTAMP = "20261028T020358Z"
SUITES = [("debian", "bookworm"), ("debian", "bookworm-updates"), ("debian", "bookworm-proposed-updates"),
          ("debian-security", "bookworm-security")]
# [size margin]: stop and report if the signed total exceeds the inventoried estimate by more than this ratio.
ALLOWED_SIZE_OVERRUN = 0.15


def fetch(url: str, attempts: int = 4) -> tuple[bytes, str]:
    """@description: Download one public URL with bounded retries so transient snapshot errors do not abort the run.
    @param url: Absolute URL.
    @param attempts: Maximum tries.
    @return: (body bytes, final URL after redirects).
    """
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                return response.read(), response.geturl()
        except Exception as error:  # boundary: retried, then surfaced to the caller
            if attempt == attempts:
                raise RuntimeError(f"download failed after {attempts} attempts: {url}: {error}") from error
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def verified_release(archive: str, timestamp: str, suite: str, keyring: Path, index_dir: Path) -> tuple[str, str] | None:
    """@description: Obtain the signed text of one suite's InRelease, refusing anything gpgv does not accept.
    @param archive: snapshot archive name (debian or debian-security).
    @param timestamp: snapshot timestamp requested.
    @param suite: Debian suite name.
    @param keyring: Trusted Debian archive keyring.
    @param index_dir: Directory where evidence files are stored.
    @return: (signed text, resolved base URL) or None when the suite does not exist at that time.
    """
    url = f"{SNAPSHOT}/archive/{archive}/{timestamp}/dists/{suite}/InRelease"
    try:
        body, final_url = fetch(url)
    except RuntimeError:
        return None
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "InRelease").write_bytes(body)
    result = subprocess.run(["gpgv", "--status-fd", "2", "--keyring", str(keyring), "--output", "-",
                             str(index_dir / "InRelease")], capture_output=True)
    status = result.stderr.decode("utf-8", "replace")
    (index_dir / "gpgv-status.txt").write_text(status, encoding="utf-8")
    valid_signers = re.findall(r"^\[GNUPG:\] VALIDSIG ([0-9A-F]{40})", status, flags=re.MULTILINE)
    if result.returncode != 0 or not valid_signers:
        raise RuntimeError(f"gpgv rejected {url}")
    signed_text = result.stdout.decode("utf-8")
    (index_dir / "InRelease.signed-content.txt").write_text(signed_text, encoding="utf-8")
    # snapshot redirects file bodies to /file/<hash>; paths are built from the requested archive/timestamp, which
    # snapshot resolves to the same snapshot for every request.
    (index_dir / "RESOLVED-URL.txt").write_text(final_url + "\n", encoding="utf-8")
    return signed_text, f"{SNAPSHOT}/archive/{archive}/{timestamp}"


def verified_sources(signed_release: str, base_url: str, suite: str, index_dir: Path) -> dict:
    """@description: Load main/source/Sources.xz only if its SHA-256 matches the signed InRelease.
    @param signed_release: gpgv-verified InRelease text.
    @param base_url: Resolved snapshot archive base URL.
    @param suite: Debian suite name.
    @param index_dir: Directory where evidence files are stored.
    @return: (package, version) -> stanza fields.
    """
    sha_block = signed_release.split("SHA256:", 1)[1]
    match = re.search(r"^ ([0-9a-f]{64})\s+(\d+)\s+main/source/Sources\.xz$", sha_block, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"no signed Sources.xz entry for {suite}")
    body, _ = fetch(f"{base_url}/dists/{suite}/main/source/Sources.xz")
    if hashlib.sha256(body).hexdigest() != match.group(1) or len(body) != int(match.group(2)):
        raise RuntimeError(f"Sources.xz digest mismatch for {suite}")
    (index_dir / "Sources.xz").write_bytes(body)
    entries = {}
    for stanza in lzma.decompress(body).decode("utf-8", "replace").split("\n\n"):
        fields = parse_stanza(stanza)
        if "Package" in fields:
            entries[(fields["Package"], fields["Version"])] = fields
    return entries


def parse_stanza(stanza: str) -> dict:
    """@description: Read a deb822 stanza, keeping multi-line field bodies intact.
    @param stanza: One paragraph of a Sources index.
    @return: Field name to value (continuation lines joined by newlines).
    """
    fields, current = {}, None
    for line in stanza.splitlines():
        if line.startswith((" ", "\t")) and current:
            fields[current] += "\n" + line.strip()
        elif ":" in line:
            current, value = line.split(":", 1)
            fields[current] = value.strip()
    return fields


def load_index(archive: str, timestamp: str, suite: str, keyring: Path, evidence_root: Path, cache: dict) -> dict | None:
    """@description: Return the verified Sources index for one suite and time, downloading it at most once.
    @param archive: snapshot archive name.
    @param timestamp: snapshot timestamp requested.
    @param suite: Debian suite name.
    @param keyring: Trusted keyring.
    @param evidence_root: Root directory for index evidence.
    @param cache: In-memory cache keyed by (archive, timestamp, suite).
    @return: {"entries", "base_url", "evidence_dir"} or None if the suite is absent.
    """
    key = (archive, timestamp, suite)
    if key not in cache:
        index_dir = evidence_root / f"{archive}_{suite}_{timestamp}"
        release = verified_release(archive, timestamp, suite, keyring, index_dir)
        cache[key] = None if release is None else {
            "entries": verified_sources(release[0], release[1], suite, index_dir),
            "base_url": release[1], "evidence_dir": index_dir.name}
    return cache[key]


def first_seen_timestamps(source: str, version: str) -> list[tuple[str, str]]:
    """@description: Find when snapshot first saw a source version, as a fallback index time.
    @param source: Source package name.
    @param version: Exact source version.
    @return: Distinct (archive, timestamp) pairs from the snapshot file info.
    """
    url = f"{SNAPSHOT}/mr/package/{urllib.parse.quote(source)}/{urllib.parse.quote(version)}/srcfiles?fileinfo=1"
    body = json.loads(fetch(url)[0])
    seen = {(info["archive_name"], info["first_seen"]) for infos in body["fileinfo"].values() for info in infos}
    return sorted(seen)


def locate(source: str, version: str, keyring: Path, evidence_root: Path, cache: dict) -> tuple[dict, dict, str]:
    """@description: Find the signed index stanza for one exact source version (primary time first, then first-seen).
    @param source: Source package name.
    @param version: Exact source version.
    @param keyring: Trusted keyring.
    @param evidence_root: Root directory for index evidence.
    @param cache: Index cache.
    @return: (stanza fields, index record, suite).
    """
    candidates = [(archive, PRIMARY_TIMESTAMP, suite) for archive, suite in SUITES]
    for archive, timestamp in first_seen_timestamps(source, version):
        candidates += [(archive, timestamp, suite) for a, suite in SUITES if a == archive]
    for archive, timestamp, suite in candidates:
        index = load_index(archive, timestamp, suite, keyring, evidence_root, cache)
        if index and (source, version) in index["entries"]:
            return index["entries"][(source, version)], index, suite
    raise RuntimeError(f"exact version not found in any signed index: {source} {version}")


def file_list(stanza: dict) -> list[tuple[str, int, str]]:
    """@description: Read the signed (name, size, SHA-256) list of every file in a source package.
    @param stanza: Sources index stanza.
    @return: List of (file name, size, sha256).
    """
    files = []
    for line in stanza["Checksums-Sha256"].splitlines():
        parts = line.split()
        if len(parts) == 3:
            files.append((parts[2], int(parts[1]), parts[0]))
    return files


def download_verified(url: str, destination: Path, size: int, sha256: str) -> None:
    """@description: Fetch one source file unless an identical verified copy already exists.
    @param url: Pool URL at the snapshot of the signed index.
    @param destination: Local path.
    @param size: Signed size.
    @param sha256: Signed SHA-256.
    @return: None
    """
    if destination.exists() and destination.stat().st_size == size and hashlib.sha256(destination.read_bytes()).hexdigest() == sha256:
        return
    body, _ = fetch(url)
    if len(body) != size or hashlib.sha256(body).hexdigest() != sha256:
        raise RuntimeError(f"downloaded file does not match signed checksum: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)


def main() -> int:
    """@description: Resolve, size-check, download and verify every copyleft source version, writing a mapping report.
    @param: none
    @return: Process exit code.
    """
    if len(sys.argv) != 4:
        print("usage: debian_source_fetch.py <debian-source-scope.json> <keyring.gpg> <out-dir>", file=sys.stderr)
        return 2
    scope = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    keyring, out_dir = Path(sys.argv[2]), Path(sys.argv[3])
    evidence_root, cache, plan = out_dir / "indices", {}, []
    for entry in scope["sources"]:
        stanza, index, suite = locate(entry["source"], entry["version"], keyring, evidence_root, cache)
        snapshot_names = {f["name"]: f["sha1"] for f in entry["files"]}
        files = file_list(stanza)
        if {name for name, _, _ in files} != set(snapshot_names):
            raise RuntimeError(f"file set differs between signed index and snapshot for {entry['source']}")
        plan.append({"source": entry["source"], "version": entry["version"], "binaries": entry["binaries"],
                     "format": stanza.get("Format", "?"), "suite": suite, "index_evidence": index["evidence_dir"],
                     "files": [{"name": n, "size": s, "sha256": h, "snapshot_sha1": snapshot_names[n],
                                "url": f"{index['base_url']}/{stanza['Directory']}/{n}"} for n, s, h in files]})
        print(f"located {entry['source']} {entry['version']} in {suite} ({index['evidence_dir']})", flush=True)
    signed_total = sum(f["size"] for p in plan for f in p["files"])
    print(f"signed_total_bytes={signed_total} inventoried={scope['total_source_bytes']}", flush=True)
    if signed_total > scope["total_source_bytes"] * (1 + ALLOWED_SIZE_OVERRUN):
        print("size exceeds the approved estimate; stopping before download", file=sys.stderr)
        return 1
    for package in plan:
        for item in package["files"]:
            destination = out_dir / "pool" / f"{package['source']}_{package['version']}" / item["name"]
            download_verified(item["url"], destination, item["size"], item["sha256"])
            if hashlib.sha1(destination.read_bytes()).hexdigest() != item["snapshot_sha1"]:
                raise RuntimeError(f"snapshot SHA-1 cross-check failed: {item['name']}")
        print(f"verified {package['source']} {package['version']} ({len(package['files'])} files)", flush=True)
    (out_dir / "SOURCE-MAP.json").write_text(json.dumps({"primary_timestamp": PRIMARY_TIMESTAMP, "signed_total_bytes": signed_total,
                                                         "packages": plan}, indent=2) + "\n", encoding="utf-8")
    print(f"done packages={len(plan)} files={sum(len(p['files']) for p in plan)} bytes={signed_total}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as failure:
        print(f"FAIL: {failure}", file=sys.stderr)
        sys.exit(1)
