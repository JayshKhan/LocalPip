"""LocalPip CLI — argparse + ANSI progress, stdlib only.

Usage:
    localpip download <pkg> [<pkg> ...]    download package(s) + deps
    localpip download -r FILE              download from requirements.txt
    localpip download --lock LOCK          deterministic install from lockfile
    localpip lock <pkg> [-o lock.json]     resolve and write a pinned lockfile
    localpip info <pkg>                    show package info
    localpip resolve <pkg>                 resolve and print the dep graph
    localpip list [DIR]                    list wheels already in DIR
    localpip clean [DIR]                   remove .part files / corrupt wheels
    localpip gui                           launch the GUI (needs PyQt5)

Most commands accept --json for machine-readable output.

Common flags: --python, --platform, --output, --no-deps, --no-verify,
              --no-cache, --mirror URL (repeatable), --config PATH,
              --jobs N, -v, --json, --no-color
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Sequence
from typing import Any

from localpip import __version__
from localpip.core import (
    ConfigManager,
    DownloadResult,
    Engine,
    LockFile,
    PackageInfo,
    Target,
    default_config_path,
    explain_no_match,
    pick_sdist,
    select_distribution,
    select_wheel,
)

# ── Progress / formatting ─────────────────────────────────────────────


def _isatty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KiB"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} MiB"
    return f"{n / 1024**3:.2f} GiB"


def _bar(fraction: float, width: int = 24) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(fraction * width)
    return "#" * filled + "-" * (width - filled)


class CliReporter:
    """Renders resolve and download events to stdout. Thread-safe."""

    def __init__(self, verbose: bool = False, no_color: bool = False):
        self.verbose = verbose
        self.no_color = no_color or not _isatty()
        self.lock = threading.Lock()
        self._active: dict[str, dict] = {}
        self._last_lines = 0

    # ── ANSI helpers ──
    def _c(self, text: str, code: str) -> str:
        if self.no_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def green(self, t: str) -> str:
        return self._c(t, "32")

    def yellow(self, t: str) -> str:
        return self._c(t, "33")

    def red(self, t: str) -> str:
        return self._c(t, "31")

    def dim(self, t: str) -> str:
        return self._c(t, "2")

    def bold(self, t: str) -> str:
        return self._c(t, "1")

    # ── Resolution events ──
    def on_resolve(self, event: str, **kw) -> None:
        with self.lock:
            if event == "resolving":
                if self.verbose:
                    print(self.dim(f"  resolving {kw['requirement']}…"))
            elif event == "resolved":
                pkg: PackageInfo = kw["package"]
                tag = self.dim("[dep]") if kw.get("is_dependency") else self.bold("[pkg]")
                print(f"  {tag} {pkg.name}=={pkg.version}")
            elif event == "not_found":
                print(self.red(f"  ! could not resolve {kw['requirement']}"))
            elif event == "done":
                print(self.dim(f"  resolved {kw['count']} package(s)"))

    # ── Download events (in-place ANSI bar per file) ──
    def on_download(self, event: str, **kw) -> None:
        with self.lock:
            if event == "start":
                self._active[kw["filename"]] = {"downloaded": 0, "total": 0}
                if not self.no_color:
                    self._render()
                else:
                    print(f"  ↓ {kw['filename']}")
            elif event == "progress":
                if kw["filename"] in self._active:
                    self._active[kw["filename"]] = {
                        "downloaded": kw["downloaded"],
                        "total": kw["total"],
                    }
                    if not self.no_color:
                        self._render()
            elif event == "complete":
                self._active.pop(kw["filename"], None)
                if not self.no_color:
                    self._clear()
                size = fmt_bytes(kw.get("size", 0))
                print(self.green(f"  ✓ {kw['filename']}  ({size})"))
                if not self.no_color:
                    self._render()
            elif event == "skip":
                reason = kw.get("reason", "")
                fn = kw.get("filename") or kw["package"].name
                if not self.no_color:
                    self._clear()
                print(self.yellow(f"  ↷ {fn}  [{reason}]"))
                if not self.no_color:
                    self._render()
            elif event == "error":
                self._active.pop(kw.get("filename", ""), None)
                if not self.no_color:
                    self._clear()
                print(self.red(f"  ✗ {kw.get('filename', '?')}: {kw['message']}"))
                if not self.no_color:
                    self._render()

    def _clear(self) -> None:
        for _ in range(self._last_lines):
            sys.stdout.write("\033[F\033[K")
        self._last_lines = 0
        sys.stdout.flush()

    def _render(self) -> None:
        self._clear()
        lines = []
        for fn, st in list(self._active.items())[-5:]:
            total = st["total"]
            done = st["downloaded"]
            frac = done / total if total else 0.0
            short = fn if len(fn) <= 38 else "…" + fn[-37:]
            if total:
                lines.append(f"  {short:38s} [{_bar(frac)}] {fmt_bytes(done)}/{fmt_bytes(total)}")
            else:
                lines.append(f"  {short:38s} {fmt_bytes(done)}")
        for line in lines:
            print(line)
        self._last_lines = len(lines)
        sys.stdout.flush()

    def finish(self) -> None:
        with self.lock:
            if not self.no_color:
                self._clear()


# ── Command implementations ──────────────────────────────────────────


def _build_engine(args: argparse.Namespace) -> Engine:
    config = ConfigManager(args.config)
    if args.mirror:
        # Override mirrors with --mirror flags (extras prepended preserved)
        existing = config.get("network.pypi_mirrors", [])
        merged = list(args.mirror)
        for m in existing:
            if m not in merged:
                merged.append(m)
        config.set("network.pypi_mirrors", merged)
    if args.jobs:
        config.set("network.max_concurrent", args.jobs)
    target = Target(
        python_version=args.python or config.get("download.python_version", "3.11"),
        platform=args.platform or config.get("download.platform", "any"),
    )
    use_cache = not getattr(args, "no_cache", False)
    return Engine(config=config, target=target, use_cache=use_cache)


def _read_requirements_file(path: str) -> list[str]:
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            out.append(line)
    return out


def _gather_requirements(args: argparse.Namespace) -> list[str]:
    reqs = list(args.packages or [])
    for path in args.requirement or []:
        if not os.path.exists(path):
            print(f"error: requirements file not found: {path}", file=sys.stderr)
            sys.exit(2)
        reqs.extend(_read_requirements_file(path))
    if not reqs:
        print(
            "error: no packages specified (give names or use -r FILE)",
            file=sys.stderr,
        )
        sys.exit(2)
    return reqs


def cmd_download(args: argparse.Namespace) -> int:
    # Two modes: --lock (deterministic) or normal resolve+download
    if getattr(args, "lock", None):
        return _cmd_download_locked(args)

    engine = _build_engine(args)
    reqs = _gather_requirements(args)
    json_mode = getattr(args, "json_output", False)
    reporter = CliReporter(verbose=args.verbose, no_color=args.no_color or json_mode)

    if not json_mode:
        print(f"localpip — target py{engine.target.python_xy}/{engine.target.platform}")
        print(f"  mirrors: {', '.join(engine.resolver.mirrors)}")
        print(f"  output:  {args.output or engine.config.get('download.default_path')}")
        print()
        print("Resolving:")
    resolved = engine.resolve(
        reqs,
        include_deps=not args.no_deps,
        on_event=None if json_mode else reporter.on_resolve,
    )
    if not resolved:
        if json_mode:
            json.dump({"ok": False, "error": "nothing to download"}, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(reporter.red("nothing to download"), file=sys.stderr)
        return 1

    # Pre-flight diagnostic for missing wheels (when sdist won't help either)
    if not json_mode:
        for pkg, _is_dep in resolved:
            dist, kind = select_distribution(
                pkg.files, engine.target, allow_sdist=not args.no_sdist
            )
            if dist is None:
                print(
                    reporter.yellow(
                        f"  ! {pkg.name}=={pkg.version}: "
                        f"{explain_no_match(pkg.files, engine.target)}"
                    )
                )
            elif kind == "sdist":
                print(
                    reporter.yellow(
                        f"  ⚠ {pkg.name}=={pkg.version}: only sdist available "
                        f"({dist['filename']}) — must be built on the target machine"
                    )
                )

    if not json_mode:
        print()
        print("Downloading:")
    output = args.output or engine.config.get("download.default_path")
    if args.no_verify:
        engine.config.set("download.verify_checksums", False)
    results = engine.download(
        [pkg for pkg, _ in resolved],
        output_dir=output,
        on_event=None if json_mode else reporter.on_download,
        allow_sdist=not args.no_sdist,
    )
    reporter.finish()

    if json_mode:
        return _render_download_json(results, output)
    return _summarize(results, output, reporter)


def _cmd_download_locked(args: argparse.Namespace) -> int:
    json_mode = getattr(args, "json_output", False)
    reporter = CliReporter(verbose=args.verbose, no_color=args.no_color or json_mode)
    try:
        lock = LockFile.read(args.lock)
    except (OSError, ValueError, KeyError) as e:
        msg = f"failed to read lockfile {args.lock}: {e}"
        if json_mode:
            json.dump({"ok": False, "error": msg}, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(reporter.red(f"error: {msg}"), file=sys.stderr)
        return 2

    engine = _build_engine(args)
    output = args.output or engine.config.get("download.default_path")
    if not json_mode:
        print(
            f"localpip — installing from lockfile {args.lock}\n"
            f"  target: py{lock.target.python_version}/{lock.target.platform}\n"
            f"  output: {output}\n"
            f"  packages: {len(lock.packages)}"
        )
        if (
            lock.target.python_version != engine.target.python_version
            or lock.target.platform != engine.target.platform
        ):
            print(
                reporter.yellow(
                    "  ! lockfile target differs from current --python/--platform; using lockfile target"
                )
            )
        print()
        print("Downloading (sha256-pinned):")
    results = engine.download_locked(
        lock,
        output_dir=output,
        on_event=None if json_mode else reporter.on_download,
    )
    reporter.finish()
    if json_mode:
        return _render_download_json(results, output)
    return _summarize(results, output, reporter)


def cmd_lock(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    reqs = _gather_requirements(args)
    json_mode = getattr(args, "json_output", False)
    reporter = CliReporter(verbose=args.verbose, no_color=args.no_color or json_mode)
    if not json_mode:
        print(f"Resolving for lockfile (py{engine.target.python_xy}/{engine.target.platform})…")
    resolved = engine.resolve(
        reqs,
        include_deps=not args.no_deps,
        on_event=None if json_mode else reporter.on_resolve,
    )
    if not resolved:
        msg = "nothing resolved; lockfile not written"
        if json_mode:
            json.dump({"ok": False, "error": msg}, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(reporter.red(msg), file=sys.stderr)
        return 1

    lock = LockFile.from_resolution(resolved, engine.target, allow_sdist=not args.no_sdist)
    out_path = args.output or "localpip.lock.json"
    lock.write(out_path)

    if json_mode:
        json.dump(
            {
                "ok": True,
                "lockfile": out_path,
                "package_count": len(lock.packages),
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 0
    print(
        f"\nWrote {len(lock.packages)} pinned package(s) to {out_path}\n"
        f"Install offline with:\n"
        f"  localpip download --lock {out_path} -o ./wheels"
    )
    return 0


# ── Local-directory commands (list / clean) ──────────────────────────


_WHEEL_NAME_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.\-]+?)-(?P<version>\d[A-Za-z0-9._\-+!]*?)"
    r"(-(?P<build>\d[^-]*?))?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$"
)


def _scan_directory(path: str) -> list[dict[str, Any]]:
    if not os.path.isdir(path):
        return []
    entries: list[dict[str, Any]] = []
    for fn in sorted(os.listdir(path)):
        full = os.path.join(path, fn)
        if not os.path.isfile(full):
            continue
        info: dict[str, Any] = {
            "filename": fn,
            "size": os.path.getsize(full),
            "kind": "other",
        }
        if fn.endswith(".whl"):
            m = _WHEEL_NAME_RE.match(fn)
            if m:
                info.update(
                    name=m.group("name").replace("_", "-"),
                    version=m.group("version"),
                    tag=f"{m.group('py')}-{m.group('abi')}-{m.group('plat')}",
                    kind="wheel",
                )
            else:
                info["kind"] = "wheel"
        elif fn.endswith((".tar.gz", ".zip")):
            info["kind"] = "sdist"
        elif fn.endswith(".part"):
            info["kind"] = "partial"
        entries.append(info)
    return entries


def cmd_list(args: argparse.Namespace) -> int:
    target_dir = args.directory or args.output or os.getcwd()
    entries = _scan_directory(target_dir)
    json_mode = getattr(args, "json_output", False)
    if json_mode:
        json.dump({"directory": target_dir, "entries": entries}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    wheels = [e for e in entries if e["kind"] == "wheel"]
    sdists = [e for e in entries if e["kind"] == "sdist"]
    partials = [e for e in entries if e["kind"] == "partial"]
    total = sum(e["size"] for e in entries)

    print(f"{target_dir}")
    print(
        f"  {len(wheels)} wheel(s), {len(sdists)} sdist(s), {len(partials)} partial(s)  "
        f"({fmt_bytes(total)})"
    )
    print()
    for e in wheels:
        if "name" in e:
            print(
                f"  {e['name']:<28s} {e['version']:<14s} {fmt_bytes(e['size']):>10s}  {e.get('tag', '')}"
            )
        else:
            print(f"  {e['filename']:<60s} {fmt_bytes(e['size']):>10s}")
    for e in sdists:
        print(f"  [sdist] {e['filename']:<54s} {fmt_bytes(e['size']):>10s}")
    if partials:
        print()
        for e in partials:
            print(f"  [partial] {e['filename']:<52s} {fmt_bytes(e['size']):>10s}")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    target_dir = args.directory or args.output or os.getcwd()
    json_mode = getattr(args, "json_output", False)
    removed: list[dict[str, Any]] = []
    if not os.path.isdir(target_dir):
        msg = f"not a directory: {target_dir}"
        if json_mode:
            json.dump({"ok": False, "error": msg}, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 2

    for fn in os.listdir(target_dir):
        full = os.path.join(target_dir, fn)
        if not os.path.isfile(full):
            continue
        # Always remove .part / .tmp leftovers
        if fn.endswith(".part") or fn.endswith(".tmp"):
            removed.append({"filename": fn, "reason": "partial download"})
            if not args.dry_run:
                try:
                    os.unlink(full)
                except OSError as e:
                    logging.warning("could not remove %s: %s", full, e)
            continue
        # Optional sha256 validation across .whl files
        if args.validate and fn.endswith(".whl"):
            try:
                h = hashlib.sha256()
                with open(full, "rb") as fp:
                    for chunk in iter(lambda: fp.read(65536), b""):
                        h.update(chunk)
                # No way to know expected sha256 without PyPI roundtrip — only
                # remove if the file is truncated (size 0).
                if os.path.getsize(full) == 0:
                    removed.append({"filename": fn, "reason": "empty file"})
                    if not args.dry_run:
                        os.unlink(full)
            except OSError as e:
                logging.warning("could not read %s: %s", full, e)

    if json_mode:
        json.dump(
            {"ok": True, "directory": target_dir, "removed": removed, "dry_run": args.dry_run},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    label = "Would remove" if args.dry_run else "Removed"
    if not removed:
        print(f"{target_dir}: nothing to clean")
    else:
        print(f"{target_dir}: {label} {len(removed)} file(s)")
        for r in removed:
            print(f"  - {r['filename']}  ({r['reason']})")
    return 0


def _render_download_json(results: Sequence[DownloadResult], output_dir: str) -> int:
    payload = {
        "ok": all(r.ok for r in results),
        "output_dir": output_dir,
        "results": [
            {
                "package": r.package,
                "version": r.version,
                "filename": r.filename,
                "path": r.path,
                "size": r.size,
                "sha256": r.sha256,
                "skipped": r.skipped,
                "error": r.error,
            }
            for r in results
        ],
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if payload["ok"] else 1


def _summarize(results: Sequence[DownloadResult], output_dir: str, reporter: CliReporter) -> int:
    ok = [r for r in results if r.ok and not r.skipped]
    skipped = [r for r in results if r.skipped]
    failed = [r for r in results if not r.ok]
    total_bytes = sum(r.size for r in results if r.ok)

    print()
    print(
        reporter.bold("Summary:"),
        reporter.green(f"{len(ok)} downloaded"),
        reporter.yellow(f"{len(skipped)} skipped"),
        reporter.red(f"{len(failed)} failed") if failed else f"{len(failed)} failed",
        f"  ({fmt_bytes(total_bytes)})",
    )
    if failed:
        print(reporter.red("Failures:"))
        for r in failed:
            print(f"  - {r.package}=={r.version}: {r.error}")

    if ok or skipped:
        print()
        names = sorted({r.package for r in results if r.ok})
        cmd = f'pip install --no-index --find-links "{output_dir}" ' + " ".join(names)
        print(reporter.dim("Install offline with:"))
        print(f"  {cmd}")
    return 0 if not failed else 1


def cmd_info(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    pkg = engine.resolver.get_package_info(args.package)
    json_mode = getattr(args, "json_output", False)
    if pkg is None:
        if json_mode:
            json.dump(
                {"ok": False, "error": f"package not found: {args.package}"},
                sys.stdout,
            )
            sys.stdout.write("\n")
        else:
            print(f"package not found: {args.package}", file=sys.stderr)
        return 1

    wheel = select_wheel(pkg.files, engine.target)
    sdist = pick_sdist(pkg.files)
    if json_mode:
        payload = {
            "ok": True,
            "name": pkg.name,
            "version": pkg.version,
            "summary": pkg.summary,
            "author": pkg.author,
            "license": pkg.license,
            "requires_dist": pkg.requires_dist,
            "files_count": len(pkg.files),
            "best_wheel": wheel["filename"] if wheel else None,
            "best_wheel_url": wheel["url"] if wheel else None,
            "sdist_filename": sdist["filename"] if sdist else None,
            "target": {
                "python_version": engine.target.python_version,
                "platform": engine.target.platform,
            },
        }
        if not wheel:
            payload["no_wheel_reason"] = explain_no_match(pkg.files, engine.target)
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"{pkg.name} {pkg.version}")
    if pkg.summary:
        print(f"  {pkg.summary}")
    if pkg.author:
        print(f"  author:  {pkg.author}")
    if pkg.license:
        print(f"  license: {pkg.license}")
    print(f"  files:   {len(pkg.files)}")
    print(f"  deps:    {len(pkg.requires_dist)}")
    if pkg.requires_dist:
        for d in pkg.requires_dist:
            print(f"    - {d}")
    if wheel:
        print(f"  best wheel for py{engine.target.python_xy}/{engine.target.platform}:")
        print(f"    {wheel['filename']}")
    else:
        print(f"  {explain_no_match(pkg.files, engine.target)}")
        if sdist:
            print(f"  fallback sdist available: {sdist['filename']}")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    reqs = _gather_requirements(args)
    json_mode = getattr(args, "json_output", False)
    reporter = CliReporter(verbose=args.verbose, no_color=args.no_color or json_mode)
    resolved = engine.resolve(
        reqs,
        include_deps=not args.no_deps,
        on_event=None if json_mode else reporter.on_resolve,
    )
    if json_mode:
        json.dump(
            {
                "ok": bool(resolved),
                "count": len(resolved),
                "packages": [
                    {
                        "name": p.name,
                        "version": p.version,
                        "is_dependency": is_dep,
                        "requires_dist": p.requires_dist,
                    }
                    for p, is_dep in resolved
                ],
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0 if resolved else 1
    print()
    print(f"{len(resolved)} package(s) resolved.")
    return 0 if resolved else 1


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from localpip.gui import main as gui_main
    except ImportError as e:
        print(
            "error: GUI support requires PyQt5. Install with:\n"
            "    pip install localpip[gui]\n"
            f"  (import error: {e})",
            file=sys.stderr,
        )
        return 1
    return gui_main(args.argv or [])


# ── Argument parser ──────────────────────────────────────────────────


def _add_engine_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--python",
        help="target Python version (e.g. 3.11). Defaults to config.",
    )
    p.add_argument(
        "--platform",
        help="target platform: any | win_amd64 | manylinux2014_x86_64 | macosx_11_0_arm64 …",
    )
    p.add_argument(
        "--mirror",
        action="append",
        metavar="URL",
        help="extra PyPI mirror URL (repeatable, takes precedence over config)",
    )
    p.add_argument(
        "--config",
        default=default_config_path(),
        help="path to config.json (default: $LOCALPIP_CONFIG, then $XDG_CONFIG_HOME/localpip/config.json, then ./config.json)",
    )
    p.add_argument("--jobs", type=int, help="max concurrent downloads (default from config)")
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="disable on-disk PyPI JSON cache (force network revalidation)",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit machine-readable JSON instead of human output",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors / progress bars")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="localpip",
        description="Offline-capable Python package downloader",
    )
    p.add_argument("--version", action="version", version=f"localpip {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # download
    dl = sub.add_parser(
        "download",
        help="download wheels for one or more packages (with deps)",
    )
    dl.add_argument("packages", nargs="*", help="package names or specs (e.g. flask==3.0)")
    dl.add_argument(
        "-r",
        "--requirement",
        action="append",
        metavar="FILE",
        help="install from a requirements file (repeatable)",
    )
    dl.add_argument(
        "--lock",
        metavar="LOCKFILE",
        help="install exactly the packages pinned in LOCKFILE (skip resolution)",
    )
    dl.add_argument("-o", "--output", help="output directory for wheels")
    dl.add_argument("--no-deps", action="store_true", help="do not resolve dependencies")
    dl.add_argument(
        "--no-verify",
        action="store_true",
        help="skip SHA-256 verification against PyPI digests",
    )
    dl.add_argument(
        "--no-sdist",
        action="store_true",
        help="error out instead of falling back to sdist when no wheel matches",
    )
    _add_engine_args(dl)
    dl.set_defaults(func=cmd_download)

    # lock
    lk = sub.add_parser(
        "lock",
        help="resolve packages and write a pinned lockfile (versions + sha256s)",
    )
    lk.add_argument("packages", nargs="*", help="package names or specs")
    lk.add_argument("-r", "--requirement", action="append", metavar="FILE")
    lk.add_argument("-o", "--output", help="lockfile path (default: ./localpip.lock.json)")
    lk.add_argument("--no-deps", action="store_true")
    lk.add_argument(
        "--no-sdist", action="store_true", help="exclude sdist-only packages from the lockfile"
    )
    _add_engine_args(lk)
    lk.set_defaults(func=cmd_lock)

    # info
    info = sub.add_parser("info", help="show package info and best wheel for target")
    info.add_argument("package", help="package name (with optional spec)")
    _add_engine_args(info)
    info.set_defaults(func=cmd_info)

    # resolve
    res = sub.add_parser(
        "resolve", help="resolve packages and print the dependency tree without downloading"
    )
    res.add_argument("packages", nargs="*")
    res.add_argument("-r", "--requirement", action="append", metavar="FILE")
    res.add_argument("--no-deps", action="store_true")
    _add_engine_args(res)
    res.set_defaults(func=cmd_resolve)

    # list
    ls = sub.add_parser("list", help="list wheels in a directory")
    ls.add_argument("directory", nargs="?", help="directory to scan (default: cwd)")
    ls.add_argument("-o", "--output", dest="output", help=argparse.SUPPRESS)
    ls.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit machine-readable JSON",
    )
    ls.add_argument("--no-color", action="store_true")
    ls.add_argument("-v", "--verbose", action="store_true")
    ls.set_defaults(func=cmd_list)

    # clean
    cln = sub.add_parser("clean", help="remove .part files and broken wheels")
    cln.add_argument("directory", nargs="?", help="directory to clean (default: cwd)")
    cln.add_argument("-o", "--output", dest="output", help=argparse.SUPPRESS)
    cln.add_argument(
        "--dry-run", action="store_true", help="show what would be removed without deleting"
    )
    cln.add_argument(
        "--validate",
        action="store_true",
        help="also validate wheel readability (slower)",
    )
    cln.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit machine-readable JSON",
    )
    cln.add_argument("--no-color", action="store_true")
    cln.add_argument("-v", "--verbose", action="store_true")
    cln.set_defaults(func=cmd_clean)

    # gui
    gui = sub.add_parser("gui", help="launch the GUI (requires PyQt5)")
    gui.add_argument("argv", nargs=argparse.REMAINDER)
    gui.set_defaults(func=cmd_gui)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
