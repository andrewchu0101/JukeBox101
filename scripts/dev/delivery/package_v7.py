"""Assemble a fresh v7 delivery from audited inputs without changing Git metadata.

Usage: python3 scripts/dev/delivery/package_v7.py <work-dir> <fresh-output-dir>
Docker build/test/audit evidence must already exist in work-dir/evidence.
"""
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assemble(work, output):
    repo = Path.cwd()
    audit = json.loads((work / "evidence/image-audit.json").read_text())
    assert not audit["blob_digest_problems"] and not audit["name_violations"]
    assert all(not audit["app_compare"][key] for key in ("missing_in_image", "extra_in_image", "content_mismatch"))
    assert "1747 passed" in (work / "evidence/offline.log").read_text()
    pg = (work / "evidence/postgres.log").read_text()
    assert "35 passed" in pg and "skipped" not in pg
    assert "BACKUP/RESTORE VERIFIED" in (work / "evidence/backup-restore.log").read_text()
    assert "RUNTIME CLI VERIFIED" in (work / "evidence/runtime-cli.log").read_text()
    assert "problems=0" in (work / "evidence/debian-verification.txt").read_text()
    assert "Loaded image: jukebox101-bot:v7" in (work / "evidence/docker-load.txt").read_text()
    dryrun = (work / "evidence/compose-dry-run.txt").read_text()
    assert "jb-v7-codex-dryrun-bot-1" in dryrun
    assert "Pulling" not in dryrun and "Building" not in dryrun
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "jukebox101-delivery-v7"
    stage.mkdir()
    origins = {}

    def copy(source, relative, origin):
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o755 if destination.suffix == ".sh" else 0o644)
        origins[relative] = origin

    for path in sorted((repo / "bot").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
            copy(path, path.relative_to(repo).as_posix(), "worktree")
    files = ["README.md", "CODING_STANDARD.md", "Dockerfile", ".dockerignore", ".env.example",
             "compose.yaml", "requirements-docker.txt", "dependency-versions.txt", "pyproject.toml",
             "docs/deploy/v7-postgresql-deployment.md", "docs/deploy/postgres-setup.sql",
             "docs/deploy/v7-delivery-status.md",
             "docs/deploy/mysql-to-postgresql-migration-plan.md", "scripts/deploy/check-host-secrets.sh"]
    for relative in files:
        copy(repo / relative, relative, "worktree")
    for directory in ("licenses", "sources"):
        for path in sorted((work / directory).rglob("*")):
            if path.is_file():
                copy(path, path.relative_to(work).as_posix(), "third-party")
    copy(work / "jukebox101-bot-v7.tar", "image/jukebox101-bot-v7.tar", "docker-save")
    for name in ("offline.log", "postgres.log", "backup-restore.log", "runtime-cli.log", "build-runtime.log"):
        copy(work / "evidence" / name, "evidence/" + name.replace(".log", ".txt"), "verification")
    for name in ("image-audit.json", "image-identity.txt", "command-preview.json", "debian-verification.txt",
                 "DEBIAN-SOURCE-SCOPE.json", "compose-dry-run.txt", "docker-load.txt", "package-scan-summary.txt",
                 "fresh-tls-setup.txt"):
        copy(work / "evidence" / name, "evidence/" + name, "verification")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    baseline = {}
    for line in subprocess.check_output(["git", "ls-tree", "-r", "HEAD"], text=True).splitlines():
        metadata, name = line.split("\t", 1)
        baseline[name] = metadata.split()[2]
    names = set(subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0"))
    names.update(subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], text=True).split("\0"))
    changes = []
    for name in sorted((names | baseline.keys()) - {""}):
        if name.startswith(("artifacts/", ".pytest", ".local/")):
            continue
        path = repo / name
        if not path.is_file():
            if name in baseline:
                changes.append({"path": name, "status": "deleted", "base_blob": baseline[name]})
            continue
        data = path.read_bytes()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        if blob != baseline.get(name):
            changes.append({"path": name, "status": "modified" if name in baseline else "added",
                            "base_blob": baseline.get(name), "sha256": hashlib.sha256(data).hexdigest(),
                            "included": name in origins})
    (stage / "SOURCE-CHANGES.json").write_text(json.dumps({"base_commit": base, "uncommitted": True,
        "scope": "Current worktree inventory; included marks files shipped in this delivery. No Git metadata included.",
        "changes": changes}, ensure_ascii=False, indent=2) + "\n")
    (stage / "PACK-README.md").write_text("""# JukeBox101 v7 Docker 交付包

部署入口：`docs/deploy/v7-postgresql-deployment.md`。先驗 `SHA256SUMS`，再 `docker load -i image/jukebox101-bot-v7.tar`。
映像：`jukebox101-bot:v7`，linux/amd64，10001:10001。正式環境一律 `--no-build`。

本包已完成隔離測試與封裝驗證；這不代表正式部署或 Discord 人工驗收完成。
1747 項離線與 35 項 PostgreSQL 測試通過，包含多 Guild 入口、全域帳號、TLS、migration、權限、並行與失聯復原。
備份還原摘要一致且可繼續投稿。交付映像的 migration/readiness 已在拋棄式庫透過 verify-full 實測。
證據在 `evidence/`。僅執行離線 command preview；未啟動正式 Bot、未同步 Discord、未操作正式資料庫。

/claim、正式 /exchange、補償保持關閉；只能單一 Gateway 實例。真實 Discord、新 Guild 安裝與正式外部 PG 仍待人工驗收。
開發測試留在原工作樹（`compose.test.yaml`），本包為部署包；證據已隨包。重新測試須使用完整工作樹。
MySQL 舊資料搬遷方案未執行。歷史文件與 legacy/mysql 未納入部署包；原工作樹保留完整歷史成果。

`SOURCE-MANIFEST.json` 記錄本包逐檔 SHA-256；`SOURCE-CHANGES.json` 記錄基準 commit 與未提交修改清單及 SHA-256。
第三方授權全文見 `licenses/THIRD-PARTY-NOTICES.md`；Debian 確切原始碼與簽章鏈、psycopg sdist 均隨包提供。
Debian 離線重驗：`python3 sources/debian/verify_debian_sources.py sources/debian evidence/DEBIAN-SOURCE-SCOPE.json`。
本專案自身授權仍由 Owner 決定。包內無秘密、Git metadata 或 PDF。
""")
    manifest = {"delivery": "v7", "delivery_status": "COMPLETE", "base_commit": base, "uncommitted": True,
        "image": (work / "evidence/image-identity.txt").read_text().strip(),
        "formal_deployment_accepted": False,
        "files": [{"path": p.relative_to(stage).as_posix(), "size": p.stat().st_size, "sha256": digest(p),
                   "origin": origins.get(p.relative_to(stage).as_posix(), "generated")}
                  for p in sorted(stage.rglob("*")) if p.is_file()]}
    (stage / "SOURCE-MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (stage / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.relative_to(stage).as_posix()}\n"
        for p in sorted(stage.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"))
    print(stage)
    return stage


def archive(stage, destination):
    with destination.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w|", format=tarfile.GNU_FORMAT) as tar:
            for path in [stage, *sorted(stage.rglob("*"))]:
                info = tar.gettarinfo(str(path), arcname=path.relative_to(stage.parent).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                if path.is_file():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)


if __name__ == "__main__":
    work, output = map(Path, sys.argv[1:])
    stage = assemble(work, output)
    target = output / "jukebox101-delivery-v7.tar.gz"
    archive(stage, target)
    (output / (target.name + ".sha256")).write_text(f"{digest(target)}  {target.name}\n")
    print(f"{target}: {digest(target)} ({target.stat().st_size} bytes)")
