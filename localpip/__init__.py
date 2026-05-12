"""LocalPip — offline-capable Python package downloader.

The public API is the `core` module; see `core.Engine` for the high-level
entry point that wraps an HTTP client, resolver and downloader.

CLI: `localpip` (always available)
GUI: `localpip gui`  (requires `pip install localpip[gui]`)
"""

from localpip.core import (
    ConfigManager,
    Downloader,
    DownloadResult,
    Engine,
    HTTPClient,
    HTTPError,
    JsonCache,
    LockEntry,
    LockFile,
    PackageInfo,
    Resolver,
    Target,
    WheelNotFoundError,
    default_config_path,
    explain_no_match,
    pick_sdist,
    select_distribution,
    select_wheel,
)
from localpip.pack import (
    PackError,
    PackResult,
    UnpackResult,
    VerifyResult,
    pack_environment,
    read_manifest,
    repair_environment,
    unpack_archive,
    verify_archive,
)

__version__ = "0.3.0"

__all__ = [
    "ConfigManager",
    "DownloadResult",
    "Downloader",
    "Engine",
    "HTTPClient",
    "HTTPError",
    "JsonCache",
    "LockEntry",
    "LockFile",
    "PackageInfo",
    "PackError",
    "PackResult",
    "Resolver",
    "Target",
    "UnpackResult",
    "VerifyResult",
    "WheelNotFoundError",
    "default_config_path",
    "explain_no_match",
    "pack_environment",
    "pick_sdist",
    "read_manifest",
    "repair_environment",
    "select_distribution",
    "select_wheel",
    "unpack_archive",
    "verify_archive",
    "__version__",
]
