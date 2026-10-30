"""Load non-secret host policy from administrator-owned paths without following links.

The SSH caller cannot select these paths or override the disabled acceptance gates.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

POLICY_PATH = Path('/etc/jukebox101/cd-policy.json')
MAX_POLICY_BYTES = 65536


def require_owned_path(path: Path, *, boundary: Path = Path('/'), owner: int = 0) -> None:
    """@description: Reject writable or symlinked policy, executable and parent paths.
    @param path: Absolute existing host path selected by the administrator.
    @param boundary: Trusted root boundary; tests may supply their private fixture root.
    @param owner: Required owner UID; production is root.
    @return: None; unsafe metadata raises PermissionError.
    """
    if not path.is_absolute() or boundary not in (path, *path.parents):
        raise PermissionError('UNTRUSTED_PATH')
    for component in (path, *path.parents):
        metadata = component.lstat()
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != owner or metadata.st_mode & 0o022:
            raise PermissionError('UNTRUSTED_PATH_METADATA')
        if component == boundary:
            return
    raise PermissionError('TRUST_BOUNDARY_NOT_FOUND')


def load_policy(path: Path = POLICY_PATH) -> dict:
    """@description: Read bounded non-secret configuration, denying absent or incomplete authority.
    @param path: Fixed production policy path, injectable only by direct tests.
    @return: Validated host policy; no account credentials are read here.
    """
    require_owned_path(path)
    if path.stat().st_size > MAX_POLICY_BYTES:
        raise ValueError('POLICY_TOO_LARGE')
    policy = json.loads(path.read_text())
    if policy.get('version') != 1:
        raise ValueError('POLICY_VERSION')
    for key in ('compose_sha256', 'env_sha256'):
        if not re.fullmatch(r'[a-f0-9]{64}', policy.get(key, '')):
            raise ValueError('POLICY_CONFIG_HASH_MISSING')
    return policy


def require_file_hash(path: Path, expected: str) -> None:
    """@description: Prevent unnoticed configuration drift before a service stop or rollback.
    @param path: Root-owned non-secret Compose or env configuration.
    @param expected: Administrator-approved SHA-256, never caller input.
    @return: None; mismatches refuse deployment.
    """
    require_owned_path(path)
    if path.stat().st_size > MAX_POLICY_BYTES:
        raise ValueError('CONFIG_TOO_LARGE')
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('CONFIG_DRIFT')


def clean_docker_environment(image: str) -> dict:
    """@description: Remove caller-controlled Docker/Compose/Python endpoint selectors.
    @param image: Validated immutable image ref selected by trusted code.
    @return: Minimal local-daemon environment; no secret values are loaded.
    """
    return {'PATH': '/usr/bin:/bin', 'HOME': '/var/lib/jukebox101-cd',
            'DOCKER_CONFIG': '/etc/jukebox101/docker', 'JB_IMAGE': image,
            'COMPOSE_DISABLE_ENV_FILE': '1', 'COMPOSE_INTERACTIVE_NO_CLI': '1'}


def require_secret_metadata(paths: dict) -> None:
    """@description: Inspect only file ownership/type/mode before any old Gateway is stopped.
    @param paths: Root-approved secret mount paths; values are never read or logged.
    @return: None; unsafe files or parent directories refuse deployment.
    """
    for name in ('discord_bot_token', 'db_password'):
        path = Path(paths[name])
        require_owned_path(path.parent)
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PermissionError('SECRET_TYPE_OR_LINK')
        if (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)) != (10001, 10001, 0o400):
            raise PermissionError('SECRET_METADATA')
        if metadata.st_size < 1:
            raise PermissionError('SECRET_EMPTY')
    ca = Path(paths['db_ca.crt'])
    require_owned_path(ca)
    if not stat.S_ISREG(ca.lstat().st_mode) or not ca.stat().st_size:
        raise PermissionError('CA_METADATA')
