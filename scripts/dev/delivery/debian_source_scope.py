"""Determine which Debian packages in the delivered image carry GPL-family obligations and what their sources weigh.

Reads the final dpkg status from the image archive, maps binary packages to exact source package versions, classifies
copyleft licenses from the shipped copyright texts, and queries snapshot.debian.org for each source version's files.
Writes a JSON report; downloads no source archives.
"""
import io
import json
import re
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

# Copyleft families whose binary redistribution requires offering the corresponding source.
COPYLEFT_PATTERN = re.compile(r"\b(A?L?GPL|GNU (Lesser |Library |Affero )?General Public License)", re.IGNORECASE)
SNAPSHOT_API = "https://snapshot.debian.org/mr/package/{source}/{version}/srcfiles?fileinfo=1"


def final_dpkg_status(image_path: Path) -> str:
    """@description: Read the dpkg database as the container sees it (topmost layer that contains it).
    @param image_path: docker save archive.
    @return: Text of var/lib/dpkg/status.
    """
    status_text = ""
    with tarfile.open(image_path) as image_tar:
        layers = json.loads(image_tar.extractfile("manifest.json").read())[0]["Layers"]
        for layer_path in layers:
            with tarfile.open(fileobj=io.BytesIO(image_tar.extractfile(layer_path).read()), mode="r:*") as layer:
                for member in layer.getmembers():
                    if member.isfile() and member.name.removeprefix("./") == "var/lib/dpkg/status":
                        status_text = layer.extractfile(member).read().decode("utf-8", "replace")
    return status_text


def installed_packages(status_text: str) -> list[dict]:
    """@description: Map each installed binary package to the exact source package version it was built from.
    @param status_text: dpkg status database text.
    @return: Records with binary name/version and source name/version.
    """
    records = []
    for stanza in status_text.split("\n\n"):
        fields = dict(re.findall(r"^([A-Za-z-]+): (.*)$", stanza, flags=re.MULTILINE))
        if "Package" not in fields or "install ok installed" not in fields.get("Status", ""):
            continue
        source_field = fields.get("Source", fields["Package"])
        source_match = re.match(r"^(\S+)(?: \((\S+)\))?$", source_field)
        records.append({"binary": fields["Package"], "binary_version": fields["Version"],
                        "source": source_match.group(1), "source_version": source_match.group(2) or fields["Version"]})
    return records


def source_files(source: str, version: str) -> dict:
    """@description: Ask the official Debian snapshot archive which files make up one exact source version.
    @param source: Debian source package name.
    @param version: Exact source version.
    @return: {"files": [{name, size, hash}], "error": str|None}.
    """
    url = SNAPSHOT_API.format(source=urllib.parse.quote(source), version=urllib.parse.quote(version))
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            body = json.load(response)
    except Exception as error:  # boundary: record the failure per package, never drop it silently
        return {"files": [], "error": f"{type(error).__name__}: {error}", "url": url}
    files = []
    for item in body["result"]:
        info = body["fileinfo"][item["hash"]][0]
        files.append({"name": info["name"], "size": info["size"], "sha1": item["hash"], "archive": info["archive_name"]})
    return {"files": files, "error": None, "url": url}


def main() -> int:
    """@description: Produce the copyleft source-obligation scope report for the delivered image.
    @param: none
    @return: Process exit code; 1 when any copyleft source version could not be resolved.
    """
    image_path, licenses_dir, report_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    packages = installed_packages(final_dpkg_status(image_path))
    sources = {}
    for package in packages:
        copyright_text = (licenses_dir / "debian" / package["binary"] / "copyright").read_text(encoding="utf-8", errors="replace")
        families = sorted({m.group(1).upper() for m in COPYLEFT_PATTERN.finditer(copyright_text)})
        package["copyleft_mentions"] = families
        if families:
            key = (package["source"], package["source_version"])
            sources.setdefault(key, {"source": key[0], "version": key[1], "binaries": []})["binaries"].append(package["binary"])
    for entry in sources.values():
        entry.update(source_files(entry["source"], entry["version"]))
        entry["total_bytes"] = sum(f["size"] for f in entry["files"])
    unresolved = [f"{e['source']} {e['version']}" for e in sources.values() if e["error"] or not e["files"]]
    report = {"installed_binary_packages": len(packages),
              "copyleft_binary_packages": sum(1 for p in packages if p["copyleft_mentions"]),
              "copyleft_source_packages": len(sources),
              "total_source_bytes": sum(e["total_bytes"] for e in sources.values()),
              "unresolved": unresolved, "sources": sorted(sources.values(), key=lambda e: -e["total_bytes"]),
              "packages": packages}
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("installed_binary_packages", "copyleft_binary_packages",
                                             "copyleft_source_packages", "total_source_bytes", "unresolved")}))
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
