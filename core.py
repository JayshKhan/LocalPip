#!/usr/bin/env python3
"""
LocalPip - Business Logic
Offline Python package downloader core engine.
"""

import sys
import os
import json
import sqlite3
import time
import requests
import logging
import re
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urljoin
from packaging.requirements import Requirement
from packaging.version import parse as parse_version

from PyQt5.QtCore import (
    Qt, pyqtSignal, pyqtSlot, QObject, QRunnable, QThreadPool, QEvent
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# ── Data Classes & Enums ─────────────────────────────────────────────

class DownloadStatus(Enum):
    QUEUED = "Queued"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"
    PAUSED = "Paused"
    CANCELLED = "Cancelled"


@dataclass
class PackageInfo:
    name: str
    version: str
    description: str
    author: str = "N/A"
    license: str = "N/A"
    dependencies: List[str] = field(default_factory=list)
    urls: List[Dict] = field(default_factory=list)


@dataclass
class DownloadItem:
    download_id: str
    package_name: str
    version: str
    filename: str
    url: str
    output_path: str
    python_version: str
    platform: str
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0
    eta: int = 0
    error_message: str = ""
    cancelled: bool = False


@dataclass
class StagedPackage:
    """A package staged for download, with resolved dependency info."""
    package_info: PackageInfo
    is_dependency: bool = False


# ── Custom QEvents ────────────────────────────────────────────────────

class PackageFoundEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.User + 1)
    def __init__(self, package_details):
        super().__init__(self.EVENT_TYPE)
        self.package_details = package_details


class PackageNotFoundEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.User + 3)
    def __init__(self, package_name):
        super().__init__(self.EVENT_TYPE)
        self.package_name = package_name


class PackageStagedEvent(QEvent):
    """Posted when a package is resolved and ready to stage."""
    EVENT_TYPE = QEvent.Type(QEvent.User + 6)
    def __init__(self, package_info: PackageInfo, is_dependency: bool = False):
        super().__init__(self.EVENT_TYPE)
        self.package_info = package_info
        self.is_dependency = is_dependency


class QueueDownloadEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.User + 4)
    def __init__(self, package_info):
        super().__init__(self.EVENT_TYPE)
        self.package_info = package_info


class StatusUpdateEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.User + 5)
    def __init__(self, message):
        super().__init__(self.EVENT_TYPE)
        self.message = message


# ── Worker ────────────────────────────────────────────────────────────

class Worker(QRunnable):
    """Generic worker for running functions in background threads."""
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    @pyqtSlot()
    def run(self):
        self.fn(*self.args, **self.kwargs)


# ── Search Engine ─────────────────────────────────────────────────────

class SearchEngine:
    """Queries PyPI, resolves dependencies, caches to SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.threadpool = QThreadPool()
        self.init_database()

    def init_database(self):
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS packages (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    normalized_name TEXT NOT NULL
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logging.error(f"Database error: {e}")

    def search_packages(self, query: str) -> List[str]:
        if not self.conn:
            return []
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT name FROM packages WHERE name LIKE ? ORDER BY name LIMIT 50",
                (f'%{query}%',)
            )
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logging.error(f"Failed to search packages: {e}")
        return []

    def get_package_details(self, package_name_input: str, pypi_mirror: str) -> Optional[PackageInfo]:
        try:
            req = Requirement(package_name_input)
            package_name = req.name

            url = urljoin(pypi_mirror.replace('/simple/', '/pypi/'), f"{package_name}/json")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if req.specifier:
                releases = data.get('releases', {}).keys()
                matching = list(req.specifier.filter(releases))
                if not matching:
                    logging.warning(f"No versions of {package_name} match {req.specifier}")
                    return None
                matching.sort(key=parse_version)
                target_version = matching[-1]
                if target_version != data.get('info', {}).get('version'):
                    v_url = urljoin(
                        pypi_mirror.replace('/simple/', '/pypi/'),
                        f"{package_name}/{target_version}/json"
                    )
                    data = requests.get(v_url, timeout=10).json()

            info = data.get('info', {})
            version = info.get('version')
            return PackageInfo(
                name=info.get('name'),
                version=version,
                description=info.get('summary'),
                author=info.get('author'),
                license=info.get('license'),
                dependencies=info.get('requires_dist', []) or [],
                urls=data.get('releases', {}).get(version, [])
            )
        except requests.RequestException as e:
            logging.error(f"Failed to get package details for {package_name_input}: {e}")
            return None
        except Exception as e:
            logging.error(f"Error processing {package_name_input}: {e}")
            return None


# ── Download Manager ──────────────────────────────────────────────────

class DownloadManager(QObject):
    """Thread-pooled download engine with progress signals."""
    progress_updated = pyqtSignal(str, dict)

    def __init__(self):
        super().__init__()
        self.downloads: Dict[str, DownloadItem] = {}
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(5)

    def _find_best_url(self, package_info: PackageInfo, python_version: str, platform: str) -> Optional[Dict]:
        wheels = [f for f in package_info.urls if f.get('packagetype') == 'bdist_wheel']
        if not wheels:
            return None

        py_ver_short = python_version.replace('.', '')
        candidates = []

        for wheel in wheels:
            filename = wheel.get('filename', '')

            # Platform filter
            if platform != 'any':
                if platform not in filename and '-any' not in filename:
                    continue

            # Python version filter
            cp_matches = re.findall(r'cp(\d+)', filename)
            if cp_matches:
                is_compatible = any(m == py_ver_short for m in cp_matches)
                if not is_compatible and 'abi3' in filename:
                    is_compatible = True
                if not is_compatible:
                    continue

            # Scoring
            score = 0
            if platform != 'any' and platform in filename:
                score += 100
            if f'cp{py_ver_short}' in filename:
                score += 50
            elif f'py{py_ver_short}' in filename:
                score += 40
            elif 'abi3' in filename:
                score += 30
            elif 'py3' in filename:
                score += 20
            candidates.append((score, wheel))

        if candidates:
            return sorted(candidates, key=lambda x: x[0], reverse=True)[0][1]
        return None

    def add_to_queue(self, package_info: PackageInfo, python_version: str,
                     platform: str, output_dir: str) -> Optional[str]:
        best_file = self._find_best_url(package_info, python_version, platform)
        if not best_file:
            logging.warning(f"No suitable wheel for {package_info.name}=={package_info.version}")
            return None

        download_id = f"{package_info.name}_{package_info.version}_{int(time.time() * 1000)}"
        filename = best_file.get('filename')
        url = best_file.get('url')

        item = DownloadItem(
            download_id=download_id,
            package_name=package_info.name,
            version=package_info.version,
            filename=filename,
            url=url,
            output_path=os.path.join(output_dir, filename),
            python_version=python_version,
            platform=platform
        )
        self.downloads[download_id] = item
        self.start_download(download_id)
        return download_id

    def start_download(self, download_id: str):
        if download_id not in self.downloads:
            return
        item = self.downloads[download_id]
        if os.path.exists(item.output_path):
            logging.info(f"File {item.filename} already exists, skipping.")
            item.status = DownloadStatus.COMPLETED
            item.progress = 100
            item.total_bytes = os.path.getsize(item.output_path)
            item.downloaded_bytes = item.total_bytes
            self.progress_updated.emit(item.download_id, item.__dict__)
            return
        item.status = DownloadStatus.DOWNLOADING
        worker = Worker(self._download_task, item)
        self.threadpool.start(worker)

    def _download_task(self, item: DownloadItem):
        try:
            response = requests.get(item.url, stream=True, timeout=30)
            response.raise_for_status()
            item.total_bytes = int(response.headers.get('content-length', 0))
            start_time = time.time()
            with open(item.output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if item.cancelled:
                        f.close()
                        if os.path.exists(item.output_path):
                            os.remove(item.output_path)
                        item.status = DownloadStatus.CANCELLED
                        self.progress_updated.emit(item.download_id, item.__dict__)
                        return
                    if chunk:
                        f.write(chunk)
                        item.downloaded_bytes += len(chunk)
                        if item.total_bytes > 0:
                            item.progress = (item.downloaded_bytes / item.total_bytes) * 100
                        elapsed = time.time() - start_time
                        if elapsed > 0:
                            item.speed = item.downloaded_bytes / elapsed
                        if item.speed > 0:
                            item.eta = (item.total_bytes - item.downloaded_bytes) / item.speed
                        self.progress_updated.emit(item.download_id, item.__dict__)
            item.status = DownloadStatus.COMPLETED
            item.progress = 100
        except requests.RequestException as e:
            item.status = DownloadStatus.FAILED
            item.error_message = str(e)
        except IOError as e:
            item.status = DownloadStatus.FAILED
            item.error_message = f"File error: {e}"
        self.progress_updated.emit(item.download_id, item.__dict__)

    def cancel_download(self, download_id: str):
        if download_id in self.downloads:
            item = self.downloads[download_id]
            item.cancelled = True
            if item.status == DownloadStatus.QUEUED:
                item.status = DownloadStatus.CANCELLED
                self.progress_updated.emit(download_id, item.__dict__)

    def retry_download(self, download_id: str):
        if download_id in self.downloads:
            item = self.downloads[download_id]
            if item.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                item.status = DownloadStatus.QUEUED
                item.progress = 0
                item.downloaded_bytes = 0
                item.speed = 0
                item.eta = 0
                item.error_message = ""
                item.cancelled = False
                self.start_download(download_id)

    def get_queue(self) -> List[DownloadItem]:
        return list(self.downloads.values())

    def reset(self):
        self.downloads.clear()


# ── Config Manager ────────────────────────────────────────────────────

class ConfigManager:
    """JSON config with dot-notation access."""

    DEFAULT = {
        "network": {
            "pypi_mirror": "https://pypi.org/simple/",
            "timeout": 30,
            "max_concurrent": 5
        },
        "ui": {
            "theme": "Light",
            "window_size": {"width": 1100, "height": 750}
        },
        "download": {
            "default_path": os.path.expanduser("~/Downloads/pip-packages"),
            "include_dependencies": True,
            "python_version": "3.11",
            "platform": "any"
        }
    }

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load()

    def _load(self) -> Dict:
        default = json.loads(json.dumps(self.DEFAULT))
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    loaded = json.load(f)
                for key in default:
                    if key not in loaded:
                        loaded[key] = default[key]
                    elif isinstance(default[key], dict):
                        for sub in default[key]:
                            if sub not in loaded[key]:
                                loaded[key][sub] = default[key][sub]
                return loaded
            except (json.JSONDecodeError, IOError):
                return default
        return default

    def save(self):
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=4)
        except IOError:
            logging.error("Failed to save config.")

    def get(self, key, default=None):
        keys = key.split('.')
        val = self.config
        try:
            for k in keys:
                val = val[k]
            return val
        except (KeyError, TypeError):
            return default

    def set(self, key, value):
        keys = key.split('.')
        d = self.config
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
