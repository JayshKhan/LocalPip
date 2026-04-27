"""LocalPip CLI — argparse + ANSI progress, stdlib only.

Usage:
    localpip download <pkg> [<pkg> ...]   download package(s) + deps
    localpip download -r FILE             download from requirements.txt
    localpip info <pkg>                   show package info
    localpip resolve <pkg>                resolve and print the dep graph
    localpip gui                          launch the GUI (needs PyQt5)

Common flags: --python, --platform, --output, --no-deps, --no-verify,
              --mirror URL (repeatable), --config PATH, --jobs N, -v
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from typing import Dict, List, Optional, Sequence

from localpip import __version__
from localpip.core import (
    ConfigManager,
    DownloadResult,
    Engine,
    PackageInfo,
    Target,
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
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KiB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MiB"
    return f"{n / 1024 ** 3:.2f} GiB"


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
        self._active: Dict[str, dict] = {}
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
                lines.append(
                    f"  {short:38s} [{_bar(frac)}] {fmt_bytes(done)}/{fmt_bytes(total)}"
                )
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
    return Engine(config=config, target=target)


def _read_requirements_file(path: str) -> List[str]:
    out: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            out.append(line)
    return out


def _gather_requirements(args: argparse.Namespace) -> List[str]:
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
    engine = _build_engine(args)
    reqs = _gather_requirements(args)
    reporter = CliReporter(verbose=args.verbose, no_color=args.no_color)

    print(
        f"localpip — target py{engine.target.python_xy}/{engine.target.platform}"
    )
    print(f"  mirrors: {', '.join(engine.resolver.mirrors)}")
    print(f"  output:  {args.output or engine.config.get('download.default_path')}")
    print()
    print("Resolving:")
    resolved = engine.resolve(
        reqs,
        include_deps=not args.no_deps,
        on_event=reporter.on_resolve,
    )
    if not resolved:
        print(reporter.red("nothing to download"), file=sys.stderr)
        return 1

    # Pre-flight: warn about missing wheels before starting downloads
    missing = []
    for pkg, _is_dep in resolved:
        if select_wheel(pkg.files, engine.target) is None:
            missing.append(pkg)
    if missing:
        for pkg in missing:
            print(
                reporter.yellow(
                    f"  ! no compatible wheel for {pkg.name}=={pkg.version}"
                )
            )

    print()
    print("Downloading:")
    output = args.output or engine.config.get("download.default_path")
    if args.no_verify:
        engine.config.set("download.verify_checksums", False)
    results = engine.download(
        [pkg for pkg, _ in resolved],
        output_dir=output,
        on_event=reporter.on_download,
    )
    reporter.finish()

    return _summarize(results, output, reporter)


def _summarize(
    results: Sequence[DownloadResult], output_dir: str, reporter: CliReporter
) -> int:
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
        cmd = (
            f'pip install --no-index --find-links "{output_dir}" '
            + " ".join(names)
        )
        print(reporter.dim("Install offline with:"))
        print(f"  {cmd}")
    return 0 if not failed else 1


def cmd_info(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    pkg = engine.resolver.get_package_info(args.package)
    if pkg is None:
        print(f"package not found: {args.package}", file=sys.stderr)
        return 1
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
    wheel = select_wheel(pkg.files, engine.target)
    if wheel:
        print(f"  best wheel for py{engine.target.python_xy}/{engine.target.platform}:")
        print(f"    {wheel['filename']}")
    else:
        print(
            f"  no compatible wheel for py{engine.target.python_xy}/{engine.target.platform}"
        )
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    reqs = _gather_requirements(args)
    reporter = CliReporter(verbose=args.verbose, no_color=args.no_color)
    resolved = engine.resolve(
        reqs,
        include_deps=not args.no_deps,
        on_event=reporter.on_resolve,
    )
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
        default=os.environ.get("LOCALPIP_CONFIG", "config.json"),
        help="path to config.json (default: ./config.json or $LOCALPIP_CONFIG)",
    )
    p.add_argument(
        "--jobs", type=int, help="max concurrent downloads (default from config)"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--no-color", action="store_true", help="disable ANSI colors / progress bars"
    )


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
    dl.add_argument("-o", "--output", help="output directory for wheels")
    dl.add_argument("--no-deps", action="store_true", help="do not resolve dependencies")
    dl.add_argument(
        "--no-verify",
        action="store_true",
        help="skip SHA-256 verification against PyPI digests",
    )
    _add_engine_args(dl)
    dl.set_defaults(func=cmd_download)

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

    # gui
    gui = sub.add_parser("gui", help="launch the GUI (requires PyQt5)")
    gui.add_argument("argv", nargs=argparse.REMAINDER)
    gui.set_defaults(func=cmd_gui)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
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
