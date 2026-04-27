"""LocalPip — offline-capable Python package downloader.

The public API is the `core` module; see `core.Engine` for the high-level
entry point that wraps an HTTP client, resolver and downloader.

CLI: `localpip` (always available)
GUI: `localpip gui`  (requires `pip install localpip[gui]`)
"""

from localpip.core import (
    ConfigManager,
    DownloadResult,
    Downloader,
    Engine,
    HTTPClient,
    HTTPError,
    PackageInfo,
    Resolver,
    Target,
    WheelNotFoundError,
    select_wheel,
)

__version__ = "0.2.0"

__all__ = [
    "ConfigManager",
    "DownloadResult",
    "Downloader",
    "Engine",
    "HTTPClient",
    "HTTPError",
    "PackageInfo",
    "Resolver",
    "Target",
    "WheelNotFoundError",
    "select_wheel",
    "__version__",
]
