"""Pack and relocate pip/venv environments.

This module is intentionally stdlib-only. The MVP target is same-platform
relocation of an existing virtual environment, not cross-platform conversion.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import stat
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from typing import Any

PACK_MANIFEST = "localpip-pack-manifest.json"
PACK_ENV_PREFIX = "environment"
PACK_FORMAT_VERSION = "1"
DEFAULT_EXCLUDES = (
    "__pycache__",
    "__pycache__/*",
    "*.pyc",
    "*.pyo",
    ".git",
    ".git/*",
)


class PackError(Exception):
    """Raised when an environment cannot be packed, unpacked, or verified."""


@dataclass
class PackResult:
    archive_path: str
    file_count: int
    size: int
    manifest: dict[str, Any]


@dataclass
class UnpackResult:
    destination: str
    file_count: int
    repaired: list[str]
    manifest: dict[str, Any]


@dataclass
class VerifyResult:
    ok: bool
    file_count: int = 0
    errors: list[str] | None = None
    warnings: list[str] | None = None
    manifest: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []


def pack_environment(
    source_dir: str,
    archive_path: str,
    *,
    archive_format: str | None = None,
    exclude: list[str] | None = None,
) -> PackResult:
    """Create a LocalPip pack archive from an existing virtual environment."""
    source = os.path.abspath(source_dir)
    _validate_venv(source)
    archive_format = _archive_format(archive_path, archive_format)
    excludes = list(DEFAULT_EXCLUDES) + list(exclude or [])

    entries = _collect_entries(source, excludes)
    if not entries:
        raise PackError(f"no files found to pack in {source}")

    manifest = _build_manifest(source, entries)
    os.makedirs(os.path.dirname(os.path.abspath(archive_path)) or ".", exist_ok=True)

    if archive_format == "tar.gz":
        _write_tar(source, archive_path, entries, manifest)
    elif archive_format == "zip":
        _write_zip(source, archive_path, entries, manifest)
    else:
        raise PackError(f"unsupported archive format: {archive_format}")

    return PackResult(
        archive_path=archive_path,
        file_count=len(entries),
        size=os.path.getsize(archive_path),
        manifest=manifest,
    )


def unpack_archive(archive_path: str, destination: str, *, force: bool = False) -> UnpackResult:
    """Extract a LocalPip pack archive and repair relocatable scripts."""
    dest = os.path.abspath(destination)
    if os.path.exists(dest) and os.listdir(dest) and not force:
        raise PackError(f"destination is not empty: {dest}")
    os.makedirs(dest, exist_ok=True)

    manifest = read_manifest(archive_path)
    _extract_archive(archive_path, dest)
    repaired = repair_environment(dest, manifest)
    return UnpackResult(
        destination=dest,
        file_count=len(manifest.get("files", [])),
        repaired=repaired,
        manifest=manifest,
    )


def verify_archive(archive_path: str, *, destination: str | None = None) -> VerifyResult:
    """Verify an archive manifest and, optionally, an unpacked destination."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = read_manifest(archive_path)
    except PackError as e:
        return VerifyResult(ok=False, errors=[str(e)])

    names = set(_archive_names(archive_path))
    for entry in manifest.get("files", []):
        rel = entry.get("path")
        if not rel:
            errors.append("manifest contains a file entry without a path")
            continue
        archive_name = _archive_env_name(rel)
        if archive_name not in names:
            errors.append(f"archive missing {rel}")

    if destination is not None:
        dest = os.path.abspath(destination)
        relocatable = set(manifest.get("relocatable_scripts", []))
        source_prefix = manifest.get("source_prefix") or ""
        for entry in manifest.get("files", []):
            rel = entry.get("path")
            if not rel:
                continue
            path = os.path.join(dest, rel)
            if not os.path.exists(path):
                errors.append(f"destination missing {rel}")
                continue
            if entry.get("type") == "file":
                if rel in relocatable:
                    actual = _hash_relocated_file(path, source_prefix, dest)
                else:
                    actual = _hash_file(path)
                if actual != entry.get("sha256"):
                    errors.append(f"destination hash mismatch for {rel}")

    return VerifyResult(
        ok=not errors,
        file_count=len(manifest.get("files", [])),
        errors=errors,
        warnings=warnings,
        manifest=manifest,
    )


def read_manifest(archive_path: str) -> dict[str, Any]:
    """Read the pack manifest from an archive."""
    try:
        if tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, "r:*") as tf:
                member = tf.getmember(PACK_MANIFEST)
                fp = tf.extractfile(member)
                if fp is None:
                    raise PackError(f"manifest is not readable: {PACK_MANIFEST}")
                return json.loads(fp.read().decode("utf-8"))
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as zf:
                return json.loads(zf.read(PACK_MANIFEST).decode("utf-8"))
    except (KeyError, OSError, tarfile.TarError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        raise PackError(f"failed to read pack manifest: {e}") from e
    raise PackError(f"unsupported archive file: {archive_path}")


def repair_environment(destination: str, manifest: dict[str, Any]) -> list[str]:
    """Repair known relocatable files after unpacking."""
    dest = os.path.abspath(destination)
    source_prefix = manifest.get("source_prefix") or ""
    if not source_prefix:
        return []

    repaired: list[str] = []
    for rel in manifest.get("relocatable_scripts", []):
        path = os.path.join(dest, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
            first, sep, rest = data.partition(b"\n")
            if not sep or not first.startswith(b"#!"):
                continue
            if source_prefix.encode("utf-8") not in first:
                continue
            new_first = _replacement_shebang(first, source_prefix, dest)
            if new_first == first:
                continue
            with open(path, "wb") as f:
                f.write(new_first + sep + rest)
            repaired.append(rel)
        except OSError:
            continue
    return repaired


def _validate_venv(source: str) -> None:
    if not os.path.isdir(source):
        raise PackError(f"environment directory not found: {source}")
    if not os.path.exists(os.path.join(source, "pyvenv.cfg")):
        raise PackError(f"not a virtual environment: {source} (missing pyvenv.cfg)")


def _archive_format(path: str, requested: str | None) -> str:
    if requested:
        fmt = requested.lower()
        if fmt in {"tgz", "tar"}:
            return "tar.gz"
        return fmt
    lower = path.lower()
    if lower.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if lower.endswith(".zip"):
        return "zip"
    return "tar.gz"


def _collect_entries(source: str, excludes: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for root, dirs, files in os.walk(source):
        rel_root = os.path.relpath(root, source)
        if rel_root == ".":
            rel_root = ""
        dirs[:] = [
            d
            for d in sorted(dirs)
            if not _excluded(_join_rel(rel_root, d), excludes)
        ]
        for filename in sorted(files):
            rel = _join_rel(rel_root, filename)
            if _excluded(rel, excludes):
                continue
            path = os.path.join(source, rel)
            st = os.lstat(path)
            mode = stat.S_IMODE(st.st_mode)
            if os.path.islink(path):
                entries.append(
                    {
                        "path": rel,
                        "type": "symlink",
                        "target": os.readlink(path),
                        "mode": mode,
                        "size": 0,
                    }
                )
            elif os.path.isfile(path):
                entries.append(
                    {
                        "path": rel,
                        "type": "file",
                        "sha256": _hash_file(path),
                        "mode": mode,
                        "size": os.path.getsize(path),
                    }
                )
    return entries


def _build_manifest(source: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pack_format_version": PACK_FORMAT_VERSION,
        "created_by": "localpip",
        "source_prefix": source,
        "python": {
            "version": _read_pyvenv_version(source) or platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "sys_platform": sys.platform,
            "machine": platform.machine(),
            "system": platform.system(),
        },
        "packages": _installed_packages(source),
        "relocatable_scripts": _relocatable_scripts(source, entries),
        "files": entries,
    }


def _write_tar(
    source: str,
    archive_path: str,
    entries: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    manifest_bytes = _manifest_bytes(manifest)
    with tarfile.open(archive_path, "w:gz") as tf:
        info = tarfile.TarInfo(PACK_MANIFEST)
        info.size = len(manifest_bytes)
        info.mode = 0o644
        tf.addfile(info, _BytesReader(manifest_bytes))
        for entry in entries:
            src = os.path.join(source, entry["path"])
            tf.add(src, arcname=_archive_env_name(entry["path"]), recursive=False)


def _write_zip(
    source: str,
    archive_path: str,
    entries: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(PACK_MANIFEST, _manifest_bytes(manifest))
        for entry in entries:
            if entry["type"] != "file":
                continue
            zf.write(os.path.join(source, entry["path"]), _archive_env_name(entry["path"]))


def _extract_archive(archive_path: str, dest: str) -> None:
    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path, "r:*") as tf:
            members = [m for m in tf.getmembers() if m.name.startswith(f"{PACK_ENV_PREFIX}/")]
            for member in members:
                target = _safe_join(dest, member.name[len(PACK_ENV_PREFIX) + 1 :])
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                elif member.issym():
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if os.path.lexists(target):
                        os.unlink(target)
                    os.symlink(member.linkname, target)
                elif member.isfile():
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    fp = tf.extractfile(member)
                    if fp is None:
                        continue
                    with open(target, "wb") as out:
                        out.write(fp.read())
                    os.chmod(target, member.mode)
            return
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                if not name.startswith(f"{PACK_ENV_PREFIX}/") or name.endswith("/"):
                    continue
                rel = name[len(PACK_ENV_PREFIX) + 1 :]
                target = _safe_join(dest, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as out:
                    out.write(src.read())
            return
    raise PackError(f"unsupported archive file: {archive_path}")


def _archive_names(archive_path: str) -> list[str]:
    if tarfile.is_tarfile(archive_path):
        with tarfile.open(archive_path, "r:*") as tf:
            return tf.getnames()
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            return zf.namelist()
    raise PackError(f"unsupported archive file: {archive_path}")


def _safe_join(root: str, rel: str) -> str:
    target = os.path.abspath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise PackError(f"unsafe archive path: {rel}")
    return target


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_relocated_file(path: str, source_prefix: str, dest: str) -> str:
    with open(path, "rb") as f:
        data = f.read()
    first, sep, rest = data.partition(b"\n")
    if sep and first.startswith(b"#!"):
        first = first.replace(dest.encode("utf-8"), source_prefix.encode("utf-8"), 1)
        data = first + sep + rest
    return hashlib.sha256(data).hexdigest()


def _installed_packages(source: str) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for root, dirs, _files in os.walk(source):
        for dirname in sorted(dirs):
            if not dirname.endswith(".dist-info"):
                continue
            metadata = os.path.join(root, dirname, "METADATA")
            if not os.path.exists(metadata):
                continue
            try:
                with open(metadata, encoding="utf-8", errors="replace") as f:
                    msg = Parser().parsestr(f.read())
            except OSError:
                continue
            packages.append(
                {
                    "name": msg.get("Name", dirname[: -len(".dist-info")]),
                    "version": msg.get("Version", ""),
                }
            )
    return packages


def _relocatable_scripts(source: str, entries: list[dict[str, Any]]) -> list[str]:
    scripts: list[str] = []
    prefix = source.encode("utf-8")
    for entry in entries:
        if entry.get("type") != "file":
            continue
        rel = entry["path"]
        parts = rel.split(os.sep)
        if not parts or parts[0] not in {"bin", "Scripts"}:
            continue
        try:
            with open(os.path.join(source, rel), "rb") as f:
                first = f.readline(4096)
        except OSError:
            continue
        if first.startswith(b"#!") and prefix in first:
            scripts.append(rel)
    return scripts


def _replacement_shebang(first_line: bytes, source_prefix: str, dest: str) -> bytes:
    text = first_line.decode("utf-8", errors="replace")
    if text.startswith("#!"):
        return text.replace(source_prefix, dest, 1).encode("utf-8")
    return first_line


def _read_pyvenv_version(source: str) -> str | None:
    try:
        with open(os.path.join(source, "pyvenv.cfg"), encoding="utf-8", errors="replace") as f:
            for line in f:
                key, sep, value = line.partition("=")
                if sep and key.strip().lower() == "version":
                    return value.strip()
    except OSError:
        return None
    return None


def _excluded(rel: str, patterns: list[str]) -> bool:
    rel = rel.replace(os.sep, "/")
    name = os.path.basename(rel)
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in patterns)


def _join_rel(root: str, name: str) -> str:
    return name if not root else os.path.join(root, name)


def _archive_env_name(rel: str) -> str:
    return f"{PACK_ENV_PREFIX}/{rel.replace(os.sep, '/')}"


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


class _BytesReader:
    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk
