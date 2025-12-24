#!/usr/bin/env python3
"""
Pip Wheel Downloader - GUI Application
A beautiful PyQt5 application for downloading Python wheel files offline.
Now with smart, conditional dependency downloading.

Requirements:
- PyQt5
- requests
- sqlite3
- json
- threading
- packaging
"""

import sys
import os
import json
import sqlite3
import threading
import time
import requests
import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urljoin
from packaging.requirements import Requirement
from packaging.markers import Marker
from packaging.version import parse as parse_version

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QTextEdit, QComboBox, QCheckBox, QProgressBar, QSplitter, QTabWidget,
    QGroupBox, QSpinBox, QFileDialog, QMessageBox, QDialog, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QScrollArea,
    QSlider, QRadioButton, QButtonGroup, QTreeWidget, QTreeWidgetItem,
    QStatusBar, QMenuBar, QMenu, QAction, QToolBar, QSizePolicy
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize, QRect, pyqtSlot,
    QObject, QRunnable, QThreadPool, QEvent
)
from PyQt5.QtGui import (
    QFont, QIcon, QPalette, QColor, QPixmap, QPainter, QBrush,
    QLinearGradient, QFontMetrics, QPen
)

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Data Classes and Enums ---
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


# --- Custom Events for thread-safe GUI updates ---
class PackageFoundEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.User + 1)
    def __init__(self, package_details):
        super().__init__(self.EVENT_TYPE)
        self.package_details = package_details

class PackageAmbiguousEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.User + 2)
    def __init__(self, possible_packages):
        super().__init__(self.EVENT_TYPE)
        self.possible_packages = possible_packages

class PackageNotFoundEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.User + 3)
    def __init__(self, package_name):
        super().__init__(self.EVENT_TYPE)
        self.package_name = package_name

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

# --- Core Business Logic Classes ---

# Worker for background tasks
class Worker(QRunnable):
    """Generic worker thread for running functions in the background."""
    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    @pyqtSlot()
    def run(self):
        self.fn(*self.args, **self.kwargs)


class SearchEngine:
    """Handles package search and indexing"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.threadpool = QThreadPool()
        self.init_database()

    def init_database(self):
        """Initialize SQLite database and create tables if they don't exist."""
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
            logging.info("Database initialized successfully.")
        except sqlite3.Error as e:
            logging.error(f"Database error: {e}")

    def search_packages(self, query: str) -> List[str]:
        """Search for packages in the local database."""
        if not self.conn: return []
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM packages WHERE name LIKE ? ORDER BY name LIMIT 50", (f'%{query}%',))
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logging.error(f"Failed to search packages: {e}")
        return []

    def get_package_details(self, package_name_input: str, pypi_mirror: str) -> Optional[PackageInfo]:
        """Get detailed information about a package from PyPI, handling version specifiers."""
        try:
            # Parse input to handle version specifiers (e.g., "requests==2.25.1")
            req = Requirement(package_name_input)
            package_name = req.name
            
            # 1. Fetch main package data (info represents latest version usually)
            url = urljoin(pypi_mirror.replace('/simple/', '/pypi/'), f"{package_name}/json")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            target_version = None
            
            # 2. If version specifiers exist, find the best matching version
            if req.specifier:
                releases = data.get('releases', {}).keys()
                # Filter versions that match the specifier
                matching_versions = list(req.specifier.filter(releases))
                
                if not matching_versions:
                    logging.warning(f"No versions of {package_name} match specifier {req.specifier}")
                    return None
                
                # Sort to find the highest matching version
                matching_versions.sort(key=parse_version)
                target_version = matching_versions[-1]
                
                # Check if we need to fetch specific version metadata
                current_info_version = data.get('info', {}).get('version')
                if target_version != current_info_version:
                    logging.info(f"Fetching specific version details for {package_name} {target_version}")
                    version_url = urljoin(pypi_mirror.replace('/simple/', '/pypi/'), f"{package_name}/{target_version}/json")
                    v_response = requests.get(version_url, timeout=10)
                    v_response.raise_for_status()
                    data = v_response.json()
            
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
             logging.error(f"Error processing package details for {package_name_input}: {e}")
             return None


class DownloadManager(QObject):
    """Handles download operations and queue management"""
    progress_updated = pyqtSignal(str, dict)

    def __init__(self):
        super().__init__()
        self.downloads: Dict[str, DownloadItem] = {}
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(5)

    def _find_best_url(self, package_info: PackageInfo, python_version: str, platform: str) -> Optional[Dict]:
        """Finds the best matching wheel file URL from the list of available files."""
        wheels = [f for f in package_info.urls if f.get('packagetype') == 'bdist_wheel']
        if not wheels:
            return None # No wheels available

        py_ver_short = python_version.replace('.', '')
        candidates = []
        for wheel in wheels:
            filename = wheel.get('filename', '')
            score = 0
            # Higher score for more specific matches
            if platform != 'any' and platform in filename:
                score += 10
            if f'cp{py_ver_short}' in filename or f'py{py_ver_short}' in filename:
                score += 5
            if 'any' in filename: # 'any' is a fallback
                score += 1
            
            if score > 0:
                candidates.append((score, wheel))
        
        if candidates:
            # Return the wheel with the highest score
            return sorted(candidates, key=lambda x: x[0], reverse=True)[0][1]
        
        # If no candidates scored, fall back to the first available wheel
        return wheels[0]

    def add_to_queue(self, package_info: PackageInfo, python_version: str, platform: str, output_dir: str):
        """Add package to download queue"""
        best_file = self._find_best_url(package_info, python_version, platform)
        if not best_file:
            logging.warning(f"No suitable wheel found for {package_info.name}=={package_info.version}")
            return

        download_id = f"{package_info.name}_{package_info.version}_{int(time.time())}"
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

    def start_download(self, download_id: str):
        """Start downloading a package in a worker thread"""
        if download_id in self.downloads:
            item = self.downloads[download_id]
            if os.path.exists(item.output_path):
                logging.info(f"File {item.filename} already exists. Skipping download.")
                item.status = DownloadStatus.COMPLETED
                item.progress = 100
                self.progress_updated.emit(item.download_id, item.__dict__)
                return
            
            item.status = DownloadStatus.DOWNLOADING
            worker = Worker(self.download_task, item)
            self.threadpool.start(worker)

    def download_task(self, item: DownloadItem):
        """The actual download logic that runs in a thread."""
        try:
            response = requests.get(item.url, stream=True, timeout=30)
            response.raise_for_status()
            item.total_bytes = int(response.headers.get('content-length', 0))
            start_time = time.time()
            with open(item.output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        item.downloaded_bytes += len(chunk)
                        if item.total_bytes > 0: item.progress = (item.downloaded_bytes / item.total_bytes) * 100
                        elapsed_time = time.time() - start_time
                        if elapsed_time > 0: item.speed = item.downloaded_bytes / elapsed_time
                        if item.speed > 0: item.eta = (item.total_bytes - item.downloaded_bytes) / item.speed
                        self.progress_updated.emit(item.download_id, item.__dict__)
            item.status = DownloadStatus.COMPLETED
            item.progress = 100
        except requests.RequestException as e:
            item.status = DownloadStatus.FAILED
            item.error_message = str(e)
            logging.error(f"Download failed for {item.filename}: {e}")
        except IOError as e:
            item.status = DownloadStatus.FAILED
            item.error_message = f"File error: {e}"
            logging.error(f"File error for {item.filename}: {e}")
        self.progress_updated.emit(item.download_id, item.__dict__)

    def get_queue(self) -> List[DownloadItem]:
        return list(self.downloads.values())

class ConfigManager:
    """Handles application configuration"""
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> Dict:
        default_config = {
            "network": {"pypi_mirror": "https://pypi.org/simple/", "timeout": 30, "max_concurrent": 5},
            "ui": {"window_size": {"width": 1024, "height": 768}},
            "download": {"default_path": os.path.expanduser("~/Downloads/pip-packages")}
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f: return json.load(f)
            except json.JSONDecodeError: return default_config
        return default_config

    def save_config(self):
        try:
            with open(self.config_path, 'w') as f: json.dump(self.config, f, indent=4)
        except IOError: logging.error("Failed to save configuration file.")

    def get(self, key, default=None):
        keys = key.split('.'); val = self.config
        try:
            for k in keys: val = val[k]
            return val
        except (KeyError, TypeError): return default

    def set(self, key, value):
        keys = key.split('.'); d = self.config
        for k in keys[:-1]: d = d.setdefault(k, {})
        d[keys[-1]] = value

# --- Custom UI Components ---
class ModernButton(QPushButton):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumHeight(40); self.setFont(QFont("Arial", 10)); self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton { background-color: #0078d4; border: none; color: white; padding: 8px 16px; border-radius: 20px; font-weight: bold; }
            QPushButton:hover { background-color: #106ebe; } QPushButton:pressed { background-color: #005a9e; }
            QPushButton:disabled { background-color: #cccccc; color: #666666; }""")

class SearchBar(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("🔍 Enter exact package name (e.g., requests)"); self.setMinimumHeight(40); self.setFont(QFont("Arial", 11))
        self.setStyleSheet("""
            QLineEdit { border: 2px solid #e1e1e1; border-radius: 20px; padding: 10px 20px; background-color: white; font-size: 11px; }
            QLineEdit:focus { border-color: #0078d4; outline: none; }""")

# --- Main Application Window ---
class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager("config.json")
        self.search_engine = SearchEngine("packages.db")
        self.download_manager = DownloadManager()
        self.processed_packages = set()

        self.init_ui()
        self.setup_connections()
        self.load_settings()

    def init_ui(self):
        self.setWindowTitle("Pip Wheel Downloader v1.4")
        self.resize(self.config_manager.get("ui.window_size.width", 1024), self.config_manager.get("ui.window_size.height", 768))
        central_widget = QWidget(); self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget); main_layout.setContentsMargins(20, 10, 20, 10)
        self.create_toolbar()
        main_layout.addLayout(self.create_search_section())
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken); main_layout.addWidget(line)
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken); main_layout.addWidget(line)
        
        # Splitter for Details and Queue
        splitter = QSplitter(Qt.Vertical)
        self.details_panel = self.create_package_details_panel()
        splitter.addWidget(self.details_panel)
        
        self.queue_widget = DownloadQueueWidget(self.download_manager)
        splitter.addWidget(self.queue_widget)
        
        main_layout.addWidget(splitter, 1)
        main_layout.addWidget(self.create_config_section())
        self.status_bar = self.statusBar(); self.status_bar.showMessage("Ready")

    def create_toolbar(self):
        toolbar = self.addToolBar("Main"); toolbar.setMovable(False)
        toolbar = self.addToolBar("Main"); toolbar.setMovable(False)

    def create_search_section(self) -> QHBoxLayout:
        search_layout = QHBoxLayout(); search_layout.setContentsMargins(0, 10, 0, 10)
        self.search_bar = SearchBar(); self.download_button = ModernButton("📥 Download");
        search_layout.addWidget(self.search_bar, 5); search_layout.addWidget(self.download_button, 1)
        return search_layout

    def create_package_details_panel(self) -> QWidget:
        panel = QGroupBox("Package Information"); panel.setFont(QFont("Arial", 12, QFont.Bold))
        layout = QVBoxLayout(panel)
        scroll_area = QScrollArea(); scroll_area.setWidgetResizable(True); scroll_area.setStyleSheet("QScrollArea { border: none; }")
        self.details_widget = QWidget(); self.details_layout = QVBoxLayout(self.details_widget); self.details_layout.setAlignment(Qt.AlignTop)
        self.clear_package_details()
        scroll_area.setWidget(self.details_widget); layout.addWidget(scroll_area)
        return panel

    def create_config_section(self) -> QGroupBox:
        config_group = QGroupBox("Download Configuration"); config_layout = QHBoxLayout(config_group)
        config_layout.addWidget(QLabel("🔧 Python:")); self.python_combo = QComboBox(); self.python_combo.addItems(["3.11", "3.10", "3.9", "3.8"]); config_layout.addWidget(self.python_combo)
        config_layout.addWidget(QLabel("📱 Platform:")); self.platform_combo = QComboBox(); self.platform_combo.addItems(["any", "win_amd64", "manylinux2014_x86_64"]); config_layout.addWidget(self.platform_combo)
        self.include_deps_checkbox = QCheckBox("Include Dependencies"); self.include_deps_checkbox.setChecked(True); config_layout.addWidget(self.include_deps_checkbox)
        config_layout.addStretch()
        config_layout.addWidget(QLabel("📁 Output:")); self.output_edit = QLineEdit(); config_layout.addWidget(self.output_edit, 1)
        browse_btn = QPushButton("Browse..."); browse_btn.clicked.connect(self.browse_output_dir); config_layout.addWidget(browse_btn)
        return config_group

    def setup_connections(self):
        self.download_button.clicked.connect(self.on_download_clicked)
        self.search_bar.returnPressed.connect(self.on_download_clicked)

    def load_settings(self):
        default_path = self.config_manager.get("download.default_path")
        self.output_edit.setText(default_path)
        if not os.path.exists(default_path): os.makedirs(default_path)

    def on_download_clicked(self):
        query = self.search_bar.text().strip()
        if not query:
            QMessageBox.warning(self, "Input Error", "Please enter a package name."); return
        self.status_bar.showMessage(f"Resolving dependencies for '{query}'...")
        self.download_button.setEnabled(False); self.search_bar.setEnabled(False)
        self.processed_packages.clear()
        worker = Worker(self.resolve_and_queue_dependencies_work, query)
        self.search_engine.threadpool.start(worker)

    def get_evaluation_environment(self) -> Dict:
        """Creates an environment dictionary for evaluating dependency markers."""
        py_ver = self.python_combo.currentText()
        platform = self.platform_combo.currentText()
        
        env = {
            'python_version': '.'.join(py_ver.split('.')[:2]),
            'python_full_version': py_ver,
        }
        if 'win' in platform:
            env['sys_platform'] = 'win32'
            env['os_name'] = 'nt'
        else: # manylinux
            env['sys_platform'] = 'linux'
            env['os_name'] = 'posix'
        
        # NOTE: This does not handle 'extra' markers, which is intentional
        # to avoid pulling in optional dependencies like 'all' or 'test'.
        return env

    def resolve_and_queue_dependencies_work(self, initial_package_name: str):
        """Worker thread function to recursively find and queue all required dependencies."""
        packages_to_process = [initial_package_name]
        pypi_mirror = self.config_manager.get("network.pypi_mirror")
        environment = self.get_evaluation_environment()
        
        while packages_to_process:
            package_name = packages_to_process.pop(0)
            
            # Normalize name for tracking to avoid duplicates like 'Pillow' and 'pillow'
            normalized_name = Requirement(package_name).name.lower()
            if normalized_name in self.processed_packages:
                continue
            
            QApplication.instance().postEvent(self, StatusUpdateEvent(f"Fetching details for {package_name}..."))
            package_info = self.search_engine.get_package_details(package_name, pypi_mirror)
            
            if package_info:
                self.processed_packages.add(normalized_name)
                QApplication.instance().postEvent(self, QueueDownloadEvent(package_info))
                
                if self.include_deps_checkbox.isChecked() and package_info.dependencies:
                    for dep_string in package_info.dependencies:
                        try:
                            req = Requirement(dep_string)
                            
                            # *** NEW: Evaluate environment markers ***
                            if req.marker and not req.marker.evaluate(environment=environment):
                                continue # Skip if markers do not match
                                
                            if req.name.lower() not in self.processed_packages:
                                packages_to_process.append(req.name)
                        except Exception as e:
                            logging.warning(f"Could not parse or evaluate dependency '{dep_string}': {e}")
            else:
                QApplication.instance().postEvent(self, PackageNotFoundEvent(package_name))
        
        QApplication.instance().postEvent(self, StatusUpdateEvent("Dependency resolution complete."))

    def customEvent(self, event):
        if event.type() == QueueDownloadEvent.EVENT_TYPE:
            # Display details for the first package found
            if len(self.processed_packages) == 1:
                self.display_package_details(event.package_info)

            self.download_manager.add_to_queue(
                event.package_info,
                self.python_combo.currentText(),
                self.platform_combo.currentText(),
                self.output_edit.text()
            )
        elif event.type() == PackageNotFoundEvent.EVENT_TYPE:
            self.status_bar.showMessage(f"Could not find dependency: {event.package_name}", 5000)
        elif event.type() == StatusUpdateEvent.EVENT_TYPE:
            self.status_bar.showMessage(event.message)
            if "complete" in event.message:
                self.download_button.setEnabled(True); self.search_bar.setEnabled(True)

    def display_package_details(self, package: PackageInfo):
        self.clear_package_details(clear_placeholder=True)
        if not package: self.clear_package_details(); return
        self.details_layout.addWidget(QLabel(f"<h2>{package.name} <font color='gray' size='4'>{package.version}</font></h2>"))
        self.details_layout.addWidget(QLabel(f"<i>{package.description}</i>"))
        self.details_layout.addWidget(self.create_separator())
        self.details_layout.addWidget(QLabel(f"<b>Author:</b> {package.author}"))
        self.details_layout.addWidget(QLabel(f"<b>License:</b> {package.license}"))
        if package.dependencies:
            self.details_layout.addWidget(self.create_separator())
            deps_label = QLabel("<b>Dependencies:</b>"); self.details_layout.addWidget(deps_label)
            deps_text = QTextEdit(); deps_text.setReadOnly(True); deps_text.setText("\n".join(package.dependencies)); deps_text.setMaximumHeight(150); self.details_layout.addWidget(deps_text)
        self.details_layout.addStretch()

    def create_separator(self) -> QFrame:
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken); return line

    def clear_package_details(self, clear_placeholder=False):
        while self.details_layout.count():
            child = self.details_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        if not clear_placeholder:
            placeholder = QLabel("<i>Search for a package to see its details here.</i>"); placeholder.setAlignment(Qt.AlignCenter); placeholder.setStyleSheet("color: gray;"); self.details_layout.addWidget(placeholder)



    def browse_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)
            self.config_manager.set("download.default_path", path); self.config_manager.save_config()

    def closeEvent(self, event):
        self.config_manager.set("ui.window_size.width", self.width()); self.config_manager.set("ui.window_size.height", self.height()); self.config_manager.save_config()
        event.accept()

# --- Download Queue Window ---
class DownloadQueueWidget(QGroupBox):
    def __init__(self, download_manager: DownloadManager, parent=None):
        super().__init__("Download Queue", parent)
        self.setFont(QFont("Arial", 12, QFont.Bold))
        self.download_manager = download_manager
        self.init_ui()
        self.download_manager.progress_updated.connect(self.update_download_item)

    def init_ui(self):
        layout = QVBoxLayout(self)
        self.downloads_table = QTableWidget(); self.downloads_table.setColumnCount(5)
        self.downloads_table.setHorizontalHeaderLabels(["Filename", "Size", "Status", "Progress", "Speed"])
        self.downloads_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.downloads_table.setEditTriggers(QTableWidget.NoEditTriggers); self.downloads_table.setSelectionBehavior(QTableWidget.SelectRows); self.downloads_table.setSortingEnabled(True)
        layout.addWidget(self.downloads_table)
        self.populate_downloads()

    def populate_downloads(self):
        queue = self.download_manager.get_queue()
        self.downloads_table.setRowCount(len(queue))
        for row, item in enumerate(queue): self.add_or_update_row(row, item)

    def add_or_update_row(self, row, item):
        self.downloads_table.setItem(row, 0, QTableWidgetItem(item.filename))
        size_str = f"{item.downloaded_bytes/1024**2:.2f} / {item.total_bytes/1024**2:.2f} MB" if item.total_bytes else "0 / 0 MB"
        self.downloads_table.setItem(row, 1, QTableWidgetItem(size_str))
        self.downloads_table.setItem(row, 2, QTableWidgetItem(item.status.value))
        progress_bar = QProgressBar(); progress_bar.setValue(int(item.progress)); progress_bar.setTextVisible(True); self.downloads_table.setCellWidget(row, 3, progress_bar)
        speed_str = f"{item.speed/1024**2:.2f} MB/s" if item.speed > 0 else "N/A"
        self.downloads_table.setItem(row, 4, QTableWidgetItem(speed_str))

    @pyqtSlot(str, dict)
    def update_download_item(self, download_id, progress_dict):
        queue = self.download_manager.get_queue()
        try:
            # A more robust way to find the row
            row = next(i for i, item in enumerate(queue) if item.download_id == download_id)
            item = DownloadItem(**progress_dict)
            self.add_or_update_row(row, item)
        except StopIteration: # Item not in table yet
            self.populate_downloads()

# --- Application Entry Point ---
class PipDownloaderApp(QApplication):
    def __init__(self, sys_argv):
        super().__init__(sys_argv)
        self.setApplicationName("Pip Wheel Downloader"); self.setStyle('Fusion')
        self.main_window = MainWindow(); self.main_window.show()

def main():
    try:
        from packaging.requirements import Requirement
    except ImportError:
        print("ERROR: 'packaging' library not found. Please install it using: pip install packaging")
        sys.exit(1)
        
    app = PipDownloaderApp(sys.argv)
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
