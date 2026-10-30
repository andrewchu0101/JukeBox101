"""Heuristic sensitive-data scan for a JukeBox101 delivery directory.

Reports only rule name, relative path, line number and match length; never prints matched content.
Exit code: 0 = no blocking finding, 1 = blocking finding, 2 = usage error.
"""
import json
import re
import sys
from pathlib import Path

# Content rules whose hits block the delivery outright.
BLOCKING_CONTENT_RULES = {
    "discord_token_shape": re.compile(r"[MNO][A-Za-z\d_-]{23,28}\.[A-Za-z\d_-]{6,7}\.[A-Za-z\d_-]{27,}"),
    "private_key_header": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "discord_webhook_url": re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "credential_in_uri": re.compile(r"[a-z][a-z0-9+.-]*://[^:\s/@]+:[^@\s/]{3,}@"),
}
# Content rules that need human classification (reported, not auto-blocking).
REVIEW_CONTENT_RULES = {
    "secret_like_assignment": re.compile(
        r"(?i)\b(password|passwd|pwd|token|secret|api_key)\b\s*[=:]\s*[\"']?([^\s\"'<>${}()\[\],]{8,})"
    ),
    "discord_snowflake_like": re.compile(r"(?<![0-9A-Za-z.])\d{17,20}(?![0-9A-Za-z.])"),
    "email_address": re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"),
    "local_user_path": re.compile(r"(?i)(C:\\Users\\|/home/[a-z_][\w-]*/|/mnt/[a-z]/Users/)"),
}
# File names that must never appear in a delivery.
FORBIDDEN_NAMES = {".env", "discord_bot_token.txt", "db_password.txt", ".git", ".local", "__pycache__", ".venv"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".log", ".sqlite", ".db", ".bak", ".dump", ".journal", ".key", ".pem"}
# Image archives hold compressed layers that text rules cannot see; image_audit.py scans them layer by layer instead.
CONTENT_DELEGATED_PREFIX = "image/"
# Debian source archives and indices are compressed upstream data; verify_debian_sources.py checks them against
# the OpenPGP-signed SHA-256 chain instead of text rules.
SOURCE_ARCHIVE_PREFIX = "sources/debian/"
SOURCE_ARCHIVE_SUFFIXES = {".xz", ".gz", ".bz2", ".zst", ".lzma", ".tar", ".asc", ".sig"}


def classify_path(relative_path: Path) -> list[str]:
    """@description: Flag delivery paths whose name alone shows they are secrets, caches or private artifacts.
    @param relative_path: Path of one delivered file relative to the delivery root.
    @return: Rule names violated by the path; empty when the path is acceptable.
    """
    violations = []
    for part in relative_path.parts:
        if part in FORBIDDEN_NAMES or (part.startswith(".env.") and part != ".env.example"):
            violations.append(f"forbidden_name:{part}")
    if relative_path.suffix.lower() in FORBIDDEN_SUFFIXES:
        violations.append(f"forbidden_suffix:{relative_path.suffix.lower()}")
    if relative_path.suffix.lower() == ".pdf" and "deidentified" not in relative_path.name:
        violations.append("non_deidentified_pdf")
    return violations


def scan_text(relative_path: Path, text: str) -> list[dict]:
    """@description: Locate secret-shaped content without echoing it so the report itself stays safe to share.
    @param relative_path: Path of the scanned file relative to the delivery root.
    @param text: Decoded file content.
    @return: Findings with rule, path, line, match length and severity.
    """
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for severity, rules in (("blocking", BLOCKING_CONTENT_RULES), ("review", REVIEW_CONTENT_RULES)):
            for rule_name, pattern in rules.items():
                for match in pattern.finditer(line):
                    findings.append({
                        "rule": rule_name, "severity": severity, "path": str(relative_path),
                        "line": line_number, "match_length": len(match.group(0)),
                        "key": match.group(1).lower() if rule_name == "secret_like_assignment" else None,
                    })
    return findings


def scan_directory(delivery_root: Path) -> dict:
    """@description: Apply path and content rules to every regular file the partner would receive.
    @param delivery_root: Extracted or staged delivery directory.
    @return: Report with file count, path violations, findings and non-regular entries.
    """
    report = {"root_name": delivery_root.name, "files_scanned": 0, "path_violations": [],
              "findings": [], "non_regular_entries": [], "content_delegated": []}
    for entry in sorted(delivery_root.rglob("*")):
        relative_path = entry.relative_to(delivery_root)
        report["path_violations"] += [{"path": str(relative_path), "rule": rule} for rule in classify_path(relative_path)]
        if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
            report["non_regular_entries"].append(str(relative_path))
            continue
        if entry.is_dir():
            continue
        report["files_scanned"] += 1
        if relative_path.as_posix().startswith(CONTENT_DELEGATED_PREFIX) and relative_path.suffix == ".tar":
            report["content_delegated"].append(str(relative_path))
            continue
        if relative_path.as_posix().startswith(SOURCE_ARCHIVE_PREFIX) and relative_path.suffix in SOURCE_ARCHIVE_SUFFIXES:
            report["content_delegated"].append(str(relative_path))
            continue
        report["findings"] += scan_text(relative_path, entry.read_bytes().decode("utf-8", errors="replace"))
    return report


def main() -> int:
    """@description: Command-line boundary that turns the scan report into a gate exit code.
    @param: none
    @return: Process exit code (0 pass, 1 blocking finding, 2 usage error).
    """
    if len(sys.argv) != 4:
        print("usage: scan_pack.py <delivery-root> <report.json> <classified-baseline.json>", file=sys.stderr)
        return 2
    report = scan_directory(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    # [classified review findings]: (rule, path, line) triples a human already classified as non-secret.
    classified = {tuple(item) for item in baseline if item[2] != "*"}
    # [classified review prefixes]: (rule, path prefix) pairs classified as a class, e.g. author e-mails in license texts.
    classified_prefixes = [(item[0], item[1]) for item in baseline if item[2] == "*"]
    blocking = [f for f in report["findings"] if f["severity"] == "blocking"]
    review = [f for f in report["findings"] if f["severity"] == "review"]
    unclassified = [f for f in review if (f["rule"], f["path"], f["line"]) not in classified
                    and not any(f["rule"] == rule and f["path"].startswith(prefix) for rule, prefix in classified_prefixes)]
    for finding in unclassified:
        print(f"UNCLASSIFIED {finding['rule']} {finding['path']}:{finding['line']} len={finding['match_length']}", file=sys.stderr)
    print(f"files={report['files_scanned']} path_violations={len(report['path_violations'])} "
          f"blocking={len(blocking)} review={len(review)} unclassified={len(unclassified)} "
          f"non_regular={len(report['non_regular_entries'])} content_delegated={len(report['content_delegated'])}")
    return 1 if blocking or unclassified or report["path_violations"] or report["non_regular_entries"] else 0


if __name__ == "__main__":
    sys.exit(main())
