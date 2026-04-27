"""LocalPip core engine — pure stdlib + `packaging`.

This module has no dependency on PyQt5 or `requests`; it is safe to import
in headless / CLI / library contexts. The GUI lives in `localpip.gui` and
wraps these objects.

Public surface:
    Engine       — high-level facade (config + http + resolver + downloader)
    Target       — describes the install environment to target
    Resolver     — fetches PackageInfo and resolves dependency graphs
    Downloader   — concurrent wheel downloader with sha256 + atomic writes
    HTTPClient   — small urllib wrapper with retries + JSON + streaming
    ConfigManager — JSON config with dot-notation get/set
    PackageInfo / DownloadResult — data classes
    select_wheel — pure function: pick the best wheel file for a target
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from packaging.requirements import Requirement
from packaging.tags import Tag, parse_tag
from packaging.version import parse as parse_version

logger = logging.getLogger("localpip")

USER_AGENT = "localpip/0.2 (+https://github.com/JayshKhan/LocalPip)"


# ── Errors ────────────────────────────────────────────────────────────


class HTTPError(Exception):
    """Raised by HTTPClient on non-recoverable HTTP/network failures."""


class WheelNotFoundError(Exception):
    """Raised when no wheel matches the target environment."""


# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class Target:
    """Target install environment for wheel selection and marker evaluation."""

    python_version: str
    platform: str = "any"

    @property
    def python_short(self) -> str:
        major, minor = self.python_version.split(".")[:2]
        return f"{major}{minor}"

    @property
    def python_xy(self) -> str:
        major, minor = self.python_version.split(".")[:2]
        return f"{major}.{minor}"


@dataclass
class PackageInfo:
    name: str
    version: str
    summary: str = ""
    author: str = ""
    license: str = ""
    requires_dist: List[str] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DownloadResult:
    package: str
    version: str
    filename: str
    path: str
    size: int = 0
    sha256: Optional[str] = None
    skipped: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ── Wheel selection (proper PEP 425 / packaging.tags based) ──────────


def _platform_tags(platform: str) -> List[str]:
    """Compatible platform tags for `platform`, most specific first, ending with 'any'."""
    if platform == "any" or not platform:
        return ["any"]

    p = platform.lower()
    if "win" in p:
        return [platform, "any"]

    if "macosx" in p or "darwin" in p:
        return [platform, "any"]

    if "manylinux" in p or "linux" in p:
        if "_" in platform:
            arch = platform.split("_")[-1]
            if "x86_64" in platform:
                arch = "x86_64"
            elif "aarch64" in platform:
                arch = "aarch64"
            elif "i686" in platform:
                arch = "i686"
        else:
            arch = "x86_64"
        out = [
            f"manylinux2014_{arch}",
            f"manylinux_2_17_{arch}",
            f"manylinux2010_{arch}",
            f"manylinux_2_12_{arch}",
            f"manylinux1_{arch}",
            f"manylinux_2_5_{arch}",
            f"musllinux_1_2_{arch}",
            f"musllinux_1_1_{arch}",
            f"linux_{arch}",
            "any",
        ]
        if platform not in out:
            out.insert(0, platform)
        return out

    return [platform, "any"]


def compatible_tags(target: Target) -> List[Tag]:
    """Compatible PEP 425 tags for `target`, ranked most-specific first.

    Includes:
      * cp{XY} with cp{XY}, abi3 (for any cp{<=XY}), and none ABIs
      * py{XY}, py{X} fallbacks for pure-Python wheels
    """
    major_s, minor_s = target.python_version.split(".")[:2]
    major, minor = int(major_s), int(minor_s)
    py = f"{major_s}{minor_s}"
    interp_cp = f"cp{py}"
    interp_py_xy = f"py{py}"
    interp_py_x = f"py{major_s}"

    plats = _platform_tags(target.platform)

    tags: List[Tag] = []
    # 1. cp{XY} with exact cp{XY} ABI — the perfect match
    for plat in plats:
        tags.append(Tag(interp_cp, interp_cp, plat))
    # 2. abi3 wheels are forward-compatible: any cp{<=XY} interpreter+abi3 works
    #    Order from newest (XY) down to py3.2 (where abi3 was introduced)
    for older_minor in range(minor, 1, -1):
        older_interp = f"cp{major_s}{older_minor}"
        for plat in plats:
            tags.append(Tag(older_interp, "abi3", plat))
    # 3. cp{XY} with no ABI (pure-Python sdist-built wheels)
    for plat in plats:
        tags.append(Tag(interp_cp, "none", plat))
    # 4. py{XY} and py{X} fallbacks (pure-Python wheels)
    for interp in (interp_py_xy, interp_py_x):
        for plat in plats:
            tags.append(Tag(interp, "none", plat))
    return tags


def _wheel_tag_string(filename: str) -> str:
    """Extract `{python}-{abi}-{platform}` from a wheel filename."""
    base = filename
    if base.endswith(".whl"):
        base = base[:-4]
    parts = base.rsplit("-", 3)
    if len(parts) < 3:
        raise ValueError(f"Not a wheel filename: {filename}")
    return "-".join(parts[-3:])


def select_wheel(
    files: Sequence[Dict[str, Any]], target: Target
) -> Optional[Dict[str, Any]]:
    """Return the best wheel from `files` for `target`, or None.

    Uses packaging.tags so manylinux/musllinux/abi3/free-threaded are handled
    the same way pip would handle them.
    """
    wheels = [f for f in files if f.get("packagetype") == "bdist_wheel"]
    if not wheels:
        return None

    compat = compatible_tags(target)
    rank = {tag: i for i, tag in enumerate(compat)}

    best: Optional[Dict[str, Any]] = None
    best_rank = len(compat)  # sentinel: unmatched

    for f in wheels:
        try:
            wheel_tags = parse_tag(_wheel_tag_string(f["filename"]))
        except (ValueError, KeyError):
            continue
        for t in wheel_tags:
            r = rank.get(t)
            if r is not None and r < best_rank:
                best_rank = r
                best = f
                break  # next wheel
    return best


# ── HTTP client (stdlib urllib + retries + sha256 streaming) ─────────


class HTTPClient:
    """Tiny urllib wrapper with timeout, exponential-backoff retries, JSON,
    and atomic streaming downloads with SHA-256 hashing."""

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        user_agent: str = USER_AGENT,
    ):
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self.user_agent = user_agent

    def _build_request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(url, headers={"User-Agent": self.user_agent})

    def _retryable(self, attempt: int, err: Exception) -> bool:
        if attempt >= self.max_retries - 1:
            return False
        if isinstance(err, urllib.error.HTTPError):
            return err.code >= 500
        return isinstance(err, (urllib.error.URLError, socket.timeout, TimeoutError))

    def _sleep(self, attempt: int) -> None:
        time.sleep(self.backoff_base * (2 ** attempt))

    def get_json(self, url: str) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(
                    self._build_request(url), timeout=self.timeout
                ) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise HTTPError(f"404 Not Found: {url}") from e
                last_err = e
                if not self._retryable(attempt, e):
                    raise HTTPError(f"HTTP {e.code} from {url}") from e
                self._sleep(attempt)
            except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                last_err = e
                if not self._retryable(attempt, e):
                    raise HTTPError(f"Network error fetching {url}: {e}") from e
                self._sleep(attempt)
        raise HTTPError(f"Network error fetching {url}: {last_err}")

    def stream_to_file(
        self,
        url: str,
        dest_path: str,
        chunk_size: int = 16384,
        on_chunk: Optional[Callable[[int, int], bool]] = None,
    ) -> Tuple[str, int]:
        """Download `url` to `dest_path` atomically. Returns (sha256_hex, size).

        `on_chunk(downloaded, total)` is called per chunk; return False to abort.
        Aborts and partial files are cleaned up. Retries on transient errors.
        """
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            tmp_path = dest_path + ".part"
            h = hashlib.sha256()
            downloaded = 0
            try:
                with urllib.request.urlopen(
                    self._build_request(url), timeout=self.timeout
                ) as resp:
                    total = int(resp.headers.get("content-length") or 0)
                    with open(tmp_path, "wb") as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            h.update(chunk)
                            downloaded += len(chunk)
                            if on_chunk is not None and not on_chunk(
                                downloaded, total
                            ):
                                self._cleanup(tmp_path)
                                raise HTTPError("cancelled")
                os.replace(tmp_path, dest_path)
                return h.hexdigest(), downloaded
            except urllib.error.HTTPError as e:
                self._cleanup(tmp_path)
                last_err = e
                if not self._retryable(attempt, e):
                    raise HTTPError(f"HTTP {e.code} from {url}") from e
                self._sleep(attempt)
            except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                self._cleanup(tmp_path)
                last_err = e
                if not self._retryable(attempt, e):
                    raise HTTPError(f"Network error: {e}") from e
                self._sleep(attempt)
            except HTTPError:
                raise
            except OSError as e:
                self._cleanup(tmp_path)
                raise HTTPError(f"File error: {e}") from e
        raise HTTPError(f"Download failed after {self.max_retries} attempts: {last_err}")

    @staticmethod
    def _cleanup(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── Resolver (PyPI JSON API + dependency graph) ─────────────────────


def build_environment(target: Target) -> Dict[str, str]:
    """PEP 508 marker-evaluation environment for `target`."""
    env: Dict[str, str] = {
        "python_version": target.python_xy,
        "python_full_version": target.python_version,
        "implementation_name": "cpython",
        "implementation_version": target.python_version,
    }
    p = target.platform.lower()
    if "win" in p:
        env.update(
            sys_platform="win32",
            os_name="nt",
            platform_system="Windows",
            platform_machine="AMD64",
            platform_release="",
        )
    elif "macos" in p or "darwin" in p:
        env.update(
            sys_platform="darwin",
            os_name="posix",
            platform_system="Darwin",
            platform_machine="x86_64",
            platform_release="",
        )
    else:
        machine = "x86_64"
        if "aarch64" in p or "arm64" in p:
            machine = "aarch64"
        env.update(
            sys_platform="linux",
            os_name="posix",
            platform_system="Linux",
            platform_machine=machine,
            platform_release="",
        )
    return env


class Resolver:
    """Fetches PackageInfo from PyPI mirrors, resolves dependency graphs."""

    def __init__(
        self,
        http: HTTPClient,
        mirrors: Sequence[str],
        target: Target,
    ):
        if not mirrors:
            raise ValueError("at least one mirror is required")
        self.http = http
        self.mirrors = list(mirrors)
        self.target = target

    @staticmethod
    def _json_url(mirror: str, name: str, version: Optional[str] = None) -> str:
        base = mirror.rstrip("/")
        if base.endswith("/simple"):
            base = base[: -len("/simple")] + "/pypi"
        elif base.endswith("/simple/"):
            base = base[: -len("/simple/")] + "/pypi"
        elif "/pypi" not in base:
            base = base + "/pypi"
        if version:
            return f"{base}/{name}/{version}/json"
        return f"{base}/{name}/json"

    def get_package_info(self, requirement: str) -> Optional[PackageInfo]:
        """Resolve a requirement string against the configured mirrors."""
        try:
            req = Requirement(requirement)
        except Exception as e:
            logger.warning("invalid requirement %r: %s", requirement, e)
            return None

        for mirror in self.mirrors:
            try:
                data = self.http.get_json(self._json_url(mirror, req.name))
            except HTTPError as e:
                logger.debug("mirror %s failed for %s: %s", mirror, req.name, e)
                continue

            try:
                if req.specifier:
                    matching = sorted(
                        req.specifier.filter(data.get("releases", {}).keys()),
                        key=parse_version,
                    )
                    if not matching:
                        logger.warning(
                            "no versions of %s match %s", req.name, req.specifier
                        )
                        continue
                    target_version = str(matching[-1])
                    if target_version != data.get("info", {}).get("version"):
                        try:
                            data = self.http.get_json(
                                self._json_url(mirror, req.name, target_version)
                            )
                        except HTTPError:
                            continue

                info = data.get("info", {}) or {}
                version = info.get("version") or ""
                files = data.get("releases", {}).get(version, []) or data.get(
                    "urls", []
                )
                return PackageInfo(
                    name=info.get("name") or req.name,
                    version=version,
                    summary=info.get("summary") or "",
                    author=info.get("author") or "",
                    license=info.get("license") or "",
                    requires_dist=info.get("requires_dist") or [],
                    files=files or [],
                )
            except (KeyError, TypeError) as e:
                logger.warning("malformed PyPI response for %s: %s", req.name, e)
                continue
        return None

    def resolve(
        self,
        requirements: Iterable[str],
        *,
        include_deps: bool = True,
        on_event: Optional[Callable[..., None]] = None,
    ) -> List[Tuple[PackageInfo, bool]]:
        """Resolve `requirements` (and optionally their deps).

        Returns a list of (package_info, is_dependency) tuples, in resolution
        order. `on_event` receives keyword events: 'resolving', 'resolved',
        'not_found', 'done'.
        """
        env = build_environment(self.target)
        roots = set()
        queue: List[str] = []
        for r in requirements:
            try:
                roots.add(Requirement(r).name.lower())
            except Exception:
                pass
            queue.append(r)

        seen: Set[str] = set()
        out: List[Tuple[PackageInfo, bool]] = []

        while queue:
            req_str = queue.pop(0)
            try:
                req = Requirement(req_str)
            except Exception:
                if on_event:
                    on_event("not_found", requirement=req_str, reason="invalid")
                continue

            normalized = req.name.lower()
            if normalized in seen:
                continue
            seen.add(normalized)

            if on_event:
                on_event("resolving", requirement=req_str)

            pkg = self.get_package_info(req_str)
            if pkg is None:
                if on_event:
                    on_event("not_found", requirement=req_str)
                continue

            is_dep = normalized not in roots
            out.append((pkg, is_dep))
            if on_event:
                on_event("resolved", package=pkg, is_dependency=is_dep)

            if include_deps:
                for dep_str in pkg.requires_dist:
                    try:
                        dep_req = Requirement(dep_str)
                    except Exception:
                        continue
                    if dep_req.marker and not dep_req.marker.evaluate(environment=env):
                        continue
                    if dep_req.name.lower() in seen:
                        continue
                    spec = str(dep_req.specifier) if dep_req.specifier else ""
                    queue.append(f"{dep_req.name}{spec}")

        if on_event:
            on_event("done", count=len(out))
        return out


# ── Downloader (concurrent, sha256-verified, atomic) ────────────────


class Downloader:
    """Concurrent wheel downloader. Uses HTTPClient.stream_to_file for atomic IO."""

    def __init__(self, http: HTTPClient, max_workers: int = 5):
        self.http = http
        self.max_workers = max(1, max_workers)
        self._cancelled: Set[str] = set()

    def cancel(self, filename: str) -> None:
        self._cancelled.add(filename)

    def reset_cancellations(self) -> None:
        self._cancelled.clear()

    def download(
        self,
        packages: Sequence[PackageInfo],
        target: Target,
        output_dir: str,
        *,
        on_event: Optional[Callable[..., None]] = None,
        verify_sha256: bool = True,
    ) -> List[DownloadResult]:
        os.makedirs(output_dir, exist_ok=True)
        jobs: List[Tuple[PackageInfo, Dict[str, Any]]] = []
        results: List[DownloadResult] = []

        for pkg in packages:
            wheel = select_wheel(pkg.files, target)
            if wheel is None:
                msg = f"no compatible wheel for {pkg.name}=={pkg.version} on {target.platform}/py{target.python_xy}"
                logger.warning(msg)
                results.append(
                    DownloadResult(
                        package=pkg.name,
                        version=pkg.version,
                        filename="",
                        path="",
                        error=msg,
                    )
                )
                if on_event:
                    on_event("skip", package=pkg, reason="no compatible wheel")
                continue
            jobs.append((pkg, wheel))

        if not jobs:
            return results

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as ex:
            futures = {
                ex.submit(
                    self._download_one,
                    pkg,
                    wheel,
                    output_dir,
                    on_event,
                    verify_sha256,
                ): pkg
                for pkg, wheel in jobs
            }
            for fut in concurrent.futures.as_completed(futures):
                pkg = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    logger.exception("download failed for %s", pkg.name)
                    results.append(
                        DownloadResult(
                            package=pkg.name,
                            version=pkg.version,
                            filename="",
                            path="",
                            error=str(e),
                        )
                    )
        return results

    def _download_one(
        self,
        pkg: PackageInfo,
        wheel: Dict[str, Any],
        output_dir: str,
        on_event: Optional[Callable[..., None]],
        verify_sha256: bool,
    ) -> DownloadResult:
        filename = wheel["filename"]
        url = wheel["url"]
        dest = os.path.join(output_dir, filename)
        expected = (wheel.get("digests") or {}).get("sha256") or wheel.get(
            "sha256_digest"
        )

        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            if on_event:
                on_event("skip", package=pkg, filename=filename, reason="exists")
            return DownloadResult(
                package=pkg.name,
                version=pkg.version,
                filename=filename,
                path=dest,
                size=os.path.getsize(dest),
                skipped=True,
            )

        if on_event:
            on_event("start", package=pkg, filename=filename, url=url)

        def chunk_cb(downloaded: int, total: int) -> bool:
            if filename in self._cancelled:
                return False
            if on_event:
                on_event(
                    "progress",
                    package=pkg,
                    filename=filename,
                    downloaded=downloaded,
                    total=total,
                )
            return True

        try:
            actual_sha, size = self.http.stream_to_file(
                url, dest, on_chunk=chunk_cb
            )
        except HTTPError as e:
            return DownloadResult(
                package=pkg.name,
                version=pkg.version,
                filename=filename,
                path=dest,
                error=str(e),
            )

        if verify_sha256 and expected and actual_sha != expected:
            try:
                os.unlink(dest)
            except OSError:
                pass
            err = f"SHA-256 mismatch (expected {expected}, got {actual_sha})"
            if on_event:
                on_event("error", package=pkg, filename=filename, message=err)
            return DownloadResult(
                package=pkg.name,
                version=pkg.version,
                filename=filename,
                path=dest,
                error=err,
            )

        if on_event:
            on_event(
                "complete",
                package=pkg,
                filename=filename,
                size=size,
                sha256=actual_sha,
            )
        return DownloadResult(
            package=pkg.name,
            version=pkg.version,
            filename=filename,
            path=dest,
            size=size,
            sha256=actual_sha,
        )


# ── Config (JSON, dot-notation) ─────────────────────────────────────


class ConfigManager:
    """JSON config with dot-notation get/set and merge-with-defaults loading."""

    DEFAULT: Dict[str, Any] = {
        "network": {
            "pypi_mirrors": ["https://pypi.org/simple/"],
            "timeout": 30,
            "max_concurrent": 5,
            "max_retries": 3,
        },
        "ui": {
            "theme": "Light",
            "window_size": {"width": 1100, "height": 750},
        },
        "download": {
            "default_path": os.path.expanduser("~/Downloads/pip-packages"),
            "include_dependencies": True,
            "verify_checksums": True,
            "python_version": "3.11",
            "platform": "any",
        },
    }

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load()

    def _load(self) -> Dict[str, Any]:
        default = json.loads(json.dumps(self.DEFAULT))
        if not os.path.exists(self.config_path):
            return default
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            return default

        # Migrate: legacy single mirror string → list
        net = loaded.get("network", {}) if isinstance(loaded, dict) else {}
        if "pypi_mirror" in net and "pypi_mirrors" not in net:
            net["pypi_mirrors"] = [net.pop("pypi_mirror")]
        elif "pypi_mirror" in net:
            del net["pypi_mirror"]

        # Recursive merge with defaults so missing keys are populated
        return self._deep_merge(default, loaded)

    @staticmethod
    def _deep_merge(default: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(default)
        for k, v in override.items():
            if (
                k in result
                and isinstance(result[k], dict)
                and isinstance(v, dict)
            ):
                result[k] = ConfigManager._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    def save(self) -> None:
        try:
            os.makedirs(
                os.path.dirname(os.path.abspath(self.config_path)) or ".",
                exist_ok=True,
            )
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except OSError as e:
            logger.error("failed to save config: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        cur: Any = self.config
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        cur = self.config
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value


# ── Engine (high-level facade) ──────────────────────────────────────


class Engine:
    """High-level facade composing config, http, resolver, downloader."""

    def __init__(self, config: ConfigManager, target: Optional[Target] = None):
        self.config = config
        self.target = target or Target(
            python_version=config.get("download.python_version", "3.11"),
            platform=config.get("download.platform", "any"),
        )
        self.http = HTTPClient(
            timeout=config.get("network.timeout", 30),
            max_retries=config.get("network.max_retries", 3),
        )
        self.resolver = Resolver(
            http=self.http,
            mirrors=config.get(
                "network.pypi_mirrors", ["https://pypi.org/simple/"]
            ),
            target=self.target,
        )
        self.downloader = Downloader(
            http=self.http,
            max_workers=config.get("network.max_concurrent", 5),
        )

    def resolve(
        self,
        requirements: Iterable[str],
        *,
        include_deps: Optional[bool] = None,
        on_event: Optional[Callable[..., None]] = None,
    ) -> List[Tuple[PackageInfo, bool]]:
        if include_deps is None:
            include_deps = self.config.get("download.include_dependencies", True)
        return self.resolver.resolve(
            requirements, include_deps=include_deps, on_event=on_event
        )

    def download(
        self,
        packages: Sequence[PackageInfo],
        output_dir: Optional[str] = None,
        *,
        on_event: Optional[Callable[..., None]] = None,
    ) -> List[DownloadResult]:
        out = output_dir or self.config.get("download.default_path")
        if not out:
            raise ValueError("no output_dir provided and download.default_path not set")
        verify = self.config.get("download.verify_checksums", True)
        return self.downloader.download(
            packages, self.target, out, on_event=on_event, verify_sha256=verify
        )
