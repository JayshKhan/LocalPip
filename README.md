# LocalPip

Offline-capable Python package downloader. Pulls wheels (and recursive
dependencies) from PyPI for arbitrary `(python_version, platform)` targets,
so you can install them on an air-gapped machine.

Ships with both a **CLI** (stdlib only — single dep is `packaging`) and a
**PyQt5 GUI** (optional install).

## Install

```bash
# CLI only — minimal footprint
pip install localpip

# CLI + GUI
pip install "localpip[gui]"

# From source
git clone https://github.com/JayshKhan/LocalPip.git
cd LocalPip
pip install -e ".[gui]"
```

The CLI works on Python 3.9+. The only runtime dependency is `packaging`;
HTTP uses stdlib `urllib`, no `requests`.

## CLI usage

```bash
# Download a package and its dependencies
localpip download flask --python 3.11 --platform manylinux2014_x86_64 -o ./wheels

# Download from a requirements file
localpip download -r requirements.txt -o ./wheels

# Specify multiple PyPI mirrors (extra index URLs)
localpip download torch \
    --mirror https://download.pytorch.org/whl/cpu/ \
    --mirror https://pypi.org/simple/ \
    -o ./wheels

# Inspect a package without downloading
localpip info numpy --python 3.12 --platform win_amd64

# Resolve and print the dep graph (no downloads)
localpip resolve flask -r dev-requirements.txt

# Skip dependency resolution
localpip download flask --no-deps

# Skip SHA-256 verification (not recommended)
localpip download flask --no-verify

# Launch the GUI
localpip gui
```

The CLI shows a live ANSI progress bar (no `tqdm` dependency) and verifies
SHA-256 digests reported by PyPI.

## Installing the downloaded wheels offline

```bash
pip install package_name --no-index --find-links /path/to/wheels
```

The CLI prints the exact command at the end of every `download` run.

## GUI

A 4-page workflow: **Configure → Search → Downloads → Transfer**, with three
themes (Light, Dark, Nord), drag-and-drop `requirements.txt` import, and
per-package extra index URLs (auto-added for `torch`, `nvidia-*`, `cuda-*`).

## Architecture

```
localpip/
├── core.py          # Engine: HTTP, search, download, resolve, wheels, config
├── cli.py           # argparse CLI + ANSI progress (stdlib only)
├── gui.py           # PyQt5 GUI (optional, lazy-imported)
├── __init__.py      # Public API
└── __main__.py      # `python -m localpip`
```

The core engine has zero PyQt5 / `requests` dependencies, so it can be used
as a library:

```python
from localpip import Engine, ConfigManager, Target

engine = Engine(
    config=ConfigManager("config.json"),
    target=Target(python_version="3.11", platform="any"),
)
resolved = engine.resolve(["flask"], include_deps=True)
results = engine.download([pkg for pkg, _ in resolved], output_dir="./wheels")
```

Wheel selection uses `packaging.tags` (the same logic pip uses), so
`manylinux_2_X`, `musllinux`, `abi3` and free-threaded (`cp3Xt`) wheels
all resolve correctly.

## Robustness features

- **SHA-256 verification** against PyPI's reported digests
- **Atomic downloads** — write to `.part`, fsync, rename
- **Exponential backoff** on 5xx and connection errors
- **Concurrent downloads** with cancellable workers
- **Marker evaluation** for the target environment (drops e.g. `pywin32; sys_platform == 'win32'` on Linux)

## Running the tests

```bash
pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen PYTEST_QT_API=pyqt5 pytest tests/ -v
```

CLI / core tests run without PyQt5; GUI tests skip if it's not installed.

## License

MIT
