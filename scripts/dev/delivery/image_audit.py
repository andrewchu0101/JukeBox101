"""Audit a `docker save` OCI archive of the JukeBox101 image without extracting layers to disk.

Verifies blob digests, scans every layer member (including files later removed by whiteouts), rebuilds the merged
filesystem view in memory, compares /app with the source commit, and extracts third-party license texts.
Exit code: 0 = audit passed, 1 = blocking problem, 2 = usage error.
"""
import hashlib
import io
import json
import re
import sys
import tarfile
from email.parser import HeaderParser
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_pack import BLOCKING_CONTENT_RULES, REVIEW_CONTENT_RULES  # noqa: E402

# Largest member read for content scanning; larger files are recorded as skipped, never silently ignored.
MAX_SCAN_BYTES = 20 * 1024 * 1024
SECRET_FILE_NAMES = {".env", "discord_bot_token.txt", "db_password.txt"}
PRIVATE_DIR_NAMES = {".git", ".local", ".venv", "compensation-evidence"}
LICENSE_NAME_PATTERN = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS|COPYRIGHT)", re.IGNORECASE)
SITE_PACKAGES = "usr/local/lib/python3.12/site-packages/"
PYTHON_RUNTIME_LICENSE = "usr/local/lib/python3.12/LICENSE.txt"


def normalized(member_name: str) -> str:
    """@description: Give layer paths one spelling so whiteouts and lookups match across layers.
    @param member_name: Raw tar member name from a layer.
    @return: Path without leading './' or '/' and without trailing '/'.
    """
    path = member_name
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/").rstrip("/")
    return "" if path == "." else path


def load_layout(image_tar: tarfile.TarFile) -> tuple[dict, list[str], list[str]]:
    """@description: Read the docker-save manifest so layers are audited in their real stacking order.
    @param image_tar: Opened image archive.
    @return: (image config JSON, ordered layer blob paths, blob digest problems).
    """
    manifest = json.loads(image_tar.extractfile("manifest.json").read())[0]
    digest_problems = []
    for member in image_tar.getmembers():
        if member.isfile() and member.name.startswith("blobs/sha256/"):
            actual = hashlib.sha256(image_tar.extractfile(member).read()).hexdigest()
            if actual != member.name.rsplit("/", 1)[1]:
                digest_problems.append(member.name)
    config = json.loads(image_tar.extractfile(manifest["Config"]).read())
    return config, manifest["Layers"], digest_problems


def scan_member_content(layer_index: int, path: str, data: bytes, report: dict) -> None:
    """@description: Apply secret-shape rules to one layer file, including files a later layer deletes.
    @param layer_index: 1-based layer number.
    @param path: Normalized path inside the layer.
    @param data: Member content.
    @param report: Audit report updated in place.
    @return: None
    """
    text = data.decode("utf-8", errors="replace")
    rule_sets = [("blocking", BLOCKING_CONTENT_RULES)] + ([("review", REVIEW_CONTENT_RULES)] if path.startswith("app/") else [])
    for severity, rules in rule_sets:
        for rule_name, pattern in rules.items():
            hits = len(pattern.findall(text))
            if hits:
                report["content_findings"].append({"layer": layer_index, "path": path, "rule": rule_name,
                                                   "severity": severity, "count": hits})


def check_member_name(layer_index: int, path: str, report: dict) -> None:
    """@description: Flag secret, private-data or PDF names anywhere in any layer, whatever the final state.
    @param layer_index: 1-based layer number.
    @param path: Normalized path inside the layer.
    @param report: Audit report updated in place.
    @return: None
    """
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ""
    if name in SECRET_FILE_NAMES or (name.startswith(".env.") and name != ".env.example"):
        report["name_violations"].append({"layer": layer_index, "path": path, "rule": "secret_file_name"})
    if PRIVATE_DIR_NAMES.intersection(parts):
        report["name_violations"].append({"layer": layer_index, "path": path, "rule": "private_directory"})
    if name.lower().endswith(".pdf"):
        report["name_violations"].append({"layer": layer_index, "path": path, "rule": "pdf_in_image"})
    if name.endswith(".log"):
        report["log_files"].append({"layer": layer_index, "path": path})


def apply_whiteout(merged: dict, path: str) -> bool:
    """@description: Mirror overlay deletion semantics so the merged view equals what a container sees.
    @param merged: Path to (layer index, member) view built so far.
    @param path: Normalized member path that may be a whiteout marker.
    @return: True when the member was a whiteout marker and has been applied.
    """
    parent, _, name = path.rpartition("/")
    if not name.startswith(".wh."):
        return False
    target = parent if name == ".wh..wh..opq" else f"{parent}/{name[4:]}".lstrip("/")
    for existing in [p for p in merged if p.startswith(target + "/") or (p == target and name != ".wh..wh..opq")]:
        del merged[existing]
    return True


def audit_layers(image_tar: tarfile.TarFile, layer_paths: list[str], report: dict) -> dict:
    """@description: Walk every layer once, scanning raw members and building the merged filesystem view.
    @param image_tar: Opened image archive.
    @param layer_paths: Layer blob paths in stacking order.
    @param report: Audit report updated in place.
    @return: Merged view mapping path to (layer index, member, content bytes or None).
    """
    merged = {}
    for layer_index, layer_path in enumerate(layer_paths, start=1):
        with tarfile.open(fileobj=io.BytesIO(image_tar.extractfile(layer_path).read()), mode="r:*") as layer:
            layer_stats = {"layer": layer_index, "blob": layer_path, "members": 0, "files": 0, "whiteouts": 0}
            for member in layer.getmembers():
                path = normalized(member.name)
                layer_stats["members"] += 1
                if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
                    report["name_violations"].append({"layer": layer_index, "path": path, "rule": "unsafe_path"})
                check_member_name(layer_index, path, report)
                if apply_whiteout(merged, path):
                    layer_stats["whiteouts"] += 1
                    continue
                data = layer.extractfile(member).read() if member.isfile() else None
                if member.isfile():
                    layer_stats["files"] += 1
                    if member.size <= MAX_SCAN_BYTES:
                        scan_member_content(layer_index, path, data, report)
                    else:
                        report["skipped_large_files"].append({"layer": layer_index, "path": path, "size": member.size})
                merged[path] = (layer_index, member, data)
            report["layers"].append(layer_stats)
    return merged


def git_blob_id(data: bytes) -> str:
    """@description: Compute the Git blob id so image files can be matched to the exact commit content.
    @param data: File content.
    @return: Hex SHA-1 Git blob id.
    """
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def compare_app_tree(merged: dict, ls_tree_dump: Path, report: dict) -> None:
    """@description: Prove the application code in the image is byte-identical to the source commit's bot/ tree.
    @param merged: Merged filesystem view.
    @param ls_tree_dump: `git ls-tree -r <commit>` output.
    @param report: Audit report updated in place.
    @return: None
    """
    expected = {}
    for line in ls_tree_dump.read_text(encoding="utf-8").splitlines():
        meta, path = line.split("\t", 1)
        if path.startswith("bot/"):
            expected["app/" + path] = meta.split()[2]
    actual = {path: git_blob_id(entry[2]) for path, entry in merged.items()
              if path.startswith("app/") and entry[1].isfile()}
    report["app_compare"] = {
        "expected_files": len(expected), "image_files": len(actual),
        "missing_in_image": sorted(set(expected) - set(actual)),
        "extra_in_image": sorted(set(actual) - set(expected)),
        "content_mismatch": sorted(p for p in set(expected) & set(actual) if expected[p] != actual[p]),
    }


def write_file(output_root: Path, relative: str, data: bytes) -> None:
    """@description: Store one extracted license text under the license output directory only.
    @param output_root: Destination licenses directory.
    @param relative: Destination path relative to output_root.
    @param data: File content.
    @return: None
    """
    destination = (output_root / relative).resolve()
    if not str(destination).startswith(str(output_root.resolve()) + "/"):
        raise ValueError(f"refusing to write outside license directory: {relative}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def resolve_regular(merged: dict, path: str, depth: int = 0) -> bytes | None:
    """@description: Follow in-image symlinks (as the container would) to reach a license file's content.
    @param merged: Merged filesystem view.
    @param path: Normalized path to resolve.
    @param depth: Current symlink depth guard.
    @return: File content, or None when it does not resolve to a regular file.
    """
    if depth > 8:
        return None
    entry = merged.get(path)
    if entry is None:
        return resolve_through_parent_link(merged, path, depth)
    member = entry[1]
    if member.isfile():
        return entry[2]
    if member.issym():
        target = member.linkname
        base = "" if target.startswith("/") else str(PurePosixPath(path).parent)
        joined = PurePosixPath(base, target.lstrip("/")).as_posix()
        return resolve_regular(merged, normalized(str(PurePosixPath(*_collapse(joined)))), depth + 1)
    return None


def resolve_through_parent_link(merged: dict, path: str, depth: int) -> bytes | None:
    """@description: Reach files whose parent directory is an in-image symlink (e.g. Debian shared doc dirs).
    @param merged: Merged filesystem view.
    @param path: Normalized path that has no direct entry.
    @param depth: Current symlink depth guard.
    @return: File content, or None when no parent symlink leads to a regular file.
    """
    components = path.split("/")
    for split_at in range(len(components) - 1, 0, -1):
        prefix = "/".join(components[:split_at])
        entry = merged.get(prefix)
        if entry is None or not entry[1].issym():
            continue
        target = entry[1].linkname
        base = "" if target.startswith("/") else str(PurePosixPath(prefix).parent)
        rewritten = "/".join(_collapse(PurePosixPath(base, target.lstrip("/"), *components[split_at:]).as_posix()))
        return resolve_regular(merged, rewritten, depth + 1)
    return None


def _collapse(path: str) -> list[str]:
    """@description: Resolve '.' and '..' inside an in-image path without touching the host filesystem.
    @param path: Slash-separated path.
    @return: Remaining path components.
    """
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            parts = parts[:-1]
        else:
            parts.append(part)
    return parts or ["."]


def extract_python_licenses(merged: dict, output_root: Path) -> list[dict]:
    """@description: Collect license texts and metadata for every Python distribution installed in the image.
    @param merged: Merged filesystem view.
    @param output_root: Destination licenses directory.
    @return: One record per distribution with license fields and copied files.
    """
    records = []
    dist_dirs = sorted({p[len(SITE_PACKAGES):].split("/")[0] for p in merged
                        if p.startswith(SITE_PACKAGES) and ".dist-info/" in p})
    for dist_dir in dist_dirs:
        prefix = f"{SITE_PACKAGES}{dist_dir}/"
        metadata = HeaderParser().parsestr((resolve_regular(merged, prefix + "METADATA") or b"").decode("utf-8", "replace"))
        name, version = metadata.get("Name", dist_dir), metadata.get("Version", "?")
        layer_index = min((merged[p][0] for p in merged if p.startswith(prefix)), default=0)
        copied = []
        for path in sorted(p for p in merged if p.startswith(prefix)):
            inner = path[len(prefix):]
            if inner.startswith("licenses/") or LICENSE_NAME_PATTERN.match(PurePosixPath(inner).name):
                data = resolve_regular(merged, path)
                if data is not None:
                    write_file(output_root, f"python-packages/{name}-{version}/{inner}", data)
                    copied.append(inner)
        # [declared license]: SPDX expression when present, else the first line of the legacy License field.
        declared_license = metadata.get("License-Expression") or metadata.get("License") or ""
        records.append({"component": name, "version": version, "kind": "python-package",
                        "license": declared_license.splitlines()[0][:80] if declared_license.strip() else "",
                        "classifiers": [c for c in metadata.get_all("Classifier", []) if c.startswith("License")],
                        "files": copied, "image_path": prefix, "layer": layer_index})
    return records


def extract_debian_copyrights(merged: dict, output_root: Path) -> list[dict]:
    """@description: Collect Debian copyright files for every installed OS package of the base image.
    @param merged: Merged filesystem view.
    @param output_root: Destination licenses directory.
    @return: One record per dpkg package with version and copied copyright file.
    """
    status = (resolve_regular(merged, "var/lib/dpkg/status") or b"").decode("utf-8", "replace")
    records = []
    for stanza in status.split("\n\n"):
        fields = dict(re.findall(r"^([A-Za-z-]+): (.*)$", stanza, flags=re.MULTILINE))
        if "Package" not in fields or "installed" not in fields.get("Status", ""):
            continue
        package = fields["Package"]
        copyright_data = resolve_regular(merged, f"usr/share/doc/{package}/copyright")
        if copyright_data is not None:
            write_file(output_root, f"debian/{package}/copyright", copyright_data)
        records.append({"component": package, "version": fields.get("Version", "?"), "kind": "debian-package",
                        "files": ["copyright"] if copyright_data is not None else [],
                        "image_path": f"usr/share/doc/{package}/copyright"})
    return records


def main() -> int:
    """@description: Command-line boundary that turns the image audit into a pass/fail gate and a JSON report.
    @param: none
    @return: Process exit code.
    """
    if len(sys.argv) != 6:
        print("usage: image_audit.py <image.tar> <git-ls-tree.txt> <licenses-out-dir> <report.json> <classified-image-baseline.json>",
              file=sys.stderr)
        return 2
    image_path, ls_tree_dump, licenses_out, report_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    # [classified upstream findings]: (layer, path, rule) blocking hits a human verified as upstream, non-project content.
    classified = {(item["layer"], item["path"], item["rule"]) for item in json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))}
    if licenses_out.exists():
        print(f"refusing to reuse existing license dir: {licenses_out}", file=sys.stderr)
        return 1
    report = {"layers": [], "name_violations": [], "content_findings": [], "skipped_large_files": [], "log_files": []}
    with tarfile.open(image_path) as image_tar:
        config, layer_paths, report["blob_digest_problems"] = load_layout(image_tar)
        merged = audit_layers(image_tar, layer_paths, report)
    report["config_env_names"] = [e.split("=", 1)[0] for e in config["config"].get("Env", [])]
    report["config_user"] = config["config"].get("User")
    report["config_architecture"] = f"{config.get('os')}/{config.get('architecture')}"
    compare_app_tree(merged, ls_tree_dump, report)
    licenses_out.mkdir(parents=True)
    python_runtime = resolve_regular(merged, PYTHON_RUNTIME_LICENSE)
    if python_runtime is not None:
        write_file(licenses_out, "python-runtime/LICENSE.txt", python_runtime)
    report["python_runtime_license"] = python_runtime is not None
    report["python_packages"] = extract_python_licenses(merged, licenses_out)
    report["debian_packages"] = extract_debian_copyrights(merged, licenses_out)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    blocking = [f for f in report["content_findings"] if f["severity"] == "blocking"]
    unclassified_blocking = [f for f in blocking if (f["layer"], f["path"], f["rule"]) not in classified]
    for finding in unclassified_blocking:
        print(f"UNCLASSIFIED BLOCKING layer={finding['layer']} {finding['path']} {finding['rule']}", file=sys.stderr)
    app = report["app_compare"]
    problems = {
        "blob_digest_problems": len(report["blob_digest_problems"]), "name_violations": len(report["name_violations"]),
        "unclassified_blocking_content": len(unclassified_blocking), "app_missing": len(app["missing_in_image"]),
        "app_extra": len(app["extra_in_image"]), "app_mismatch": len(app["content_mismatch"]),
        "python_runtime_license_missing": int(not report["python_runtime_license"]),
        "architecture_not_amd64": int(report["config_architecture"] != "linux/amd64"),
    }
    print(json.dumps({"layers": len(report["layers"]), **problems, "classified_blocking": len(blocking) - len(unclassified_blocking),
                      "review_findings_in_app": len(report["content_findings"]) - len(blocking),
                      "skipped_large_files": len(report["skipped_large_files"]), "log_files": len(report["log_files"]),
                      "python_packages": len(report["python_packages"]), "debian_packages": len(report["debian_packages"])}))
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
