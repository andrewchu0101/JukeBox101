"""Extend verified v6-r4 corresponding sources for the v7 image; never modify the input pack.

Usage: python3 prepare_sources.py <v6-r4 delivery directory> <v7 work directory>
The v7 work directory must contain evidence/DEBIAN-SOURCE-SCOPE.json and licenses/.
"""
import hashlib
import json
import lzma
import shutil
import sys
import urllib.request
from pathlib import Path

import debian_source_fetch as fetch
from verify_debian_sources import signed_sources


def main():
    previous, work = map(Path, sys.argv[1:])
    root = work / "sources/debian"
    if not root.exists():
        shutil.copytree(previous / "sources/debian", root)
    shutil.copyfile(Path(__file__).with_name("verify_debian_sources.py"), root / "verify_debian_sources.py")
    scope = json.loads((work / "evidence/DEBIAN-SOURCE-SCOPE.json").read_text())
    mapping = json.loads((root / "SOURCE-MAP.json").read_text())
    known = {(p["source"], p["version"]) for p in mapping["packages"]}
    keyring = root / "keyring/debian-archive-keyring.gpg"
    for entry in scope["sources"]:
        key = entry["source"], entry["version"]
        if key in known:
            continue
        found = None
        for index_dir in sorted((root / "indices").iterdir()):
            if key not in signed_sources(index_dir, keyring):
                continue
            for stanza in lzma.decompress((index_dir / "Sources.xz").read_bytes()).decode().split("\n\n"):
                fields = fetch.parse_stanza(stanza)
                if (fields.get("Package"), fields.get("Version")) == key:
                    archive, suite, timestamp = index_dir.name.split("_", 2)
                    found = fields, {"base_url": f"{fetch.SNAPSHOT}/archive/{archive}/{timestamp}",
                                     "evidence_dir": index_dir.name}, suite
                    break
            if found:
                break
        if found is None:
            found = fetch.locate(*key, keyring, root / "indices", {})
        stanza, index, suite = found
        package = {"source": key[0], "version": key[1], "binaries": entry["binaries"],
                   "format": stanza.get("Format", "?"), "suite": suite,
                   "index_evidence": index["evidence_dir"], "files": []}
        for name, size, digest in fetch.file_list(stanza):
            url = f"{index['base_url']}/{stanza['Directory']}/{name}"
            fetch.download_verified(url, root / f"{key[0]}_{key[1]}" / name, size, digest)
            package["files"].append({"name": name, "size": size, "sha256": digest, "url": url})
        mapping["packages"].append(package)
        (root / "SOURCE-MAP.json").write_text(json.dumps(mapping, indent=2) + "\n")
        print(f"verified signed source: {key[0]} {key[1]}", flush=True)
    used = {p["index_evidence"] for p in mapping["packages"]}
    for directory in (root / "indices").iterdir():
        if directory.name in used:
            for path in directory.iterdir():
                if path.name not in {"InRelease", "Sources.xz", "gpgv-status.txt"}:
                    path.unlink()
    mapping["signed_total_bytes"] = sum(f["size"] for p in mapping["packages"] for f in p["files"])
    (root / "SOURCE-MAP.json").write_text(json.dumps(mapping, indent=2) + "\n")
    (root / "SOURCE-MAP.md").write_text("# v7 corresponding Debian sources\n\nSee SOURCE-MAP.json for exact versions, signed indices and SHA-256.\n")
    scope["unresolved"] = []
    for entry in scope["sources"]:
        package = next(p for p in mapping["packages"] if (p["source"], p["version"]) == (entry["source"], entry["version"]))
        entry["files"] = package["files"]
        entry["total_bytes"] = sum(f["size"] for f in entry["files"])
        entry["error"] = None
    scope["total_source_bytes"] = sum(e["total_bytes"] for e in scope["sources"])
    (work / "evidence/DEBIAN-SOURCE-SCOPE.json").write_text(json.dumps(scope, indent=2) + "\n")
    target = work / "sources/python"
    target.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("https://pypi.org/pypi/psycopg/3.3.6/json", timeout=60) as response:
        release = json.load(response)
    sdist = next(f for f in release["urls"] if f["packagetype"] == "sdist")
    with urllib.request.urlopen(sdist["url"], timeout=120) as response:
        data = response.read()
    assert hashlib.sha256(data).hexdigest() == sdist["digests"]["sha256"]
    (target / sdist["filename"]).write_bytes(data)
    (target / "PROVENANCE.json").write_text(json.dumps({"package": "psycopg", "version": "3.3.6",
        "url": sdist["url"], "file": sdist["filename"], "sha256": sdist["digests"]["sha256"]}, indent=2) + "\n")
    print("psycopg sdist verified against PyPI SHA-256")


if __name__ == "__main__":
    main()
