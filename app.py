#!/usr/bin/env python3
"""
LocalPip - UI & Entry Point
Beautiful multi-theme PyQt5 interface for offline Python package downloading.
"""

import sys
import os
import time
from typing import Dict, List

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QProgressBar,
    QFrame, QScrollArea, QStackedWidget, QFileDialog, QMessageBox,
    QSizePolicy, QLayout, QGraphicsDropShadowEffect, QStatusBar
)
from PyQt5.QtCore import (
    Qt, QSize, QRect, QPoint, pyqtSignal, QEvent, QMimeData
)
from PyQt5.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QFontMetrics, QClipboard,
    QDragEnterEvent, QDropEvent
)
from packaging.requirements import Requirement

from pip import (
    PackageInfo, DownloadItem, DownloadStatus, StagedPackage,
    PackageFoundEvent, PackageNotFoundEvent, PackageStagedEvent,
    QueueDownloadEvent, StatusUpdateEvent,
    Worker, SearchEngine, DownloadManager, ConfigManager
)


# ── Platform Font ─────────────────────────────────────────────────────

if sys.platform == "darwin":
    FONT_FAMILY = "SF Pro Display"
    FONT_FALLBACK = "Helvetica Neue"
elif sys.platform == "win32":
    FONT_FAMILY = "Segoe UI"
    FONT_FALLBACK = "Arial"
else:
    FONT_FAMILY = "Sans Serif"
    FONT_FALLBACK = "sans-serif"


# ── Helpers ───────────────────────────────────────────────────────────

def format_bytes(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024 ** 3:.2f} GB"


# ── Theme Definitions ─────────────────────────────────────────────────

THEMES = {
    "Light": {
        "bg_primary": "#FFFFFF",
        "bg_secondary": "#F5F5F7",
        "bg_tertiary": "#E8E8ED",
        "bg_input": "#FFFFFF",
        "text_primary": "#1D1D1F",
        "text_secondary": "#6E6E73",
        "text_tertiary": "#86868B",
        "accent": "#007AFF",
        "accent_hover": "#0056B3",
        "accent_pressed": "#004494",
        "accent_text": "#FFFFFF",
        "accent_subtle": "#E8F0FE",
        "success": "#34C759",
        "warning": "#FF9500",
        "error": "#FF3B30",
        "border": "#E5E5EA",
        "border_focus": "#007AFF",
        "card_bg": "#FFFFFF",
        "sidebar_bg": "#F5F5F7",
        "sidebar_active": "#E8E8ED",
        "sidebar_hover": "#EDEDF0",
        "scrollbar_handle": "#C7C7CC",
        "drop_zone_bg": "#F9F9FB",
        "drop_zone_border": "#C7C7CC",
        "code_bg": "#F5F5F7",
        "code_text": "#1D1D1F",
        "badge_bg": "#E8E8ED",
        "badge_text": "#6E6E73",
        "progress_bg": "#E5E5EA",
    },
    "Dark": {
        "bg_primary": "#1C1C1E",
        "bg_secondary": "#2C2C2E",
        "bg_tertiary": "#3A3A3C",
        "bg_input": "#2C2C2E",
        "text_primary": "#F5F5F7",
        "text_secondary": "#A1A1A6",
        "text_tertiary": "#636366",
        "accent": "#0A84FF",
        "accent_hover": "#409CFF",
        "accent_pressed": "#0071E3",
        "accent_text": "#FFFFFF",
        "accent_subtle": "#1A3A5C",
        "success": "#30D158",
        "warning": "#FF9F0A",
        "error": "#FF453A",
        "border": "#38383A",
        "border_focus": "#0A84FF",
        "card_bg": "#2C2C2E",
        "sidebar_bg": "#2C2C2E",
        "sidebar_active": "#3A3A3C",
        "sidebar_hover": "#333335",
        "scrollbar_handle": "#48484A",
        "drop_zone_bg": "#2C2C2E",
        "drop_zone_border": "#48484A",
        "code_bg": "#2C2C2E",
        "code_text": "#F5F5F7",
        "badge_bg": "#3A3A3C",
        "badge_text": "#A1A1A6",
        "progress_bg": "#38383A",
    },
    "Nord": {
        "bg_primary": "#2E3440",
        "bg_secondary": "#3B4252",
        "bg_tertiary": "#434C5E",
        "bg_input": "#3B4252",
        "text_primary": "#ECEFF4",
        "text_secondary": "#D8DEE9",
        "text_tertiary": "#7B88A1",
        "accent": "#88C0D0",
        "accent_hover": "#8FBCBB",
        "accent_pressed": "#5E81AC",
        "accent_text": "#2E3440",
        "accent_subtle": "#384D5E",
        "success": "#A3BE8C",
        "warning": "#EBCB8B",
        "error": "#BF616A",
        "border": "#4C566A",
        "border_focus": "#88C0D0",
        "card_bg": "#3B4252",
        "sidebar_bg": "#3B4252",
        "sidebar_active": "#434C5E",
        "sidebar_hover": "#3E4455",
        "scrollbar_handle": "#4C566A",
        "drop_zone_bg": "#3B4252",
        "drop_zone_border": "#4C566A",
        "code_bg": "#3B4252",
        "code_text": "#ECEFF4",
        "badge_bg": "#434C5E",
        "badge_text": "#D8DEE9",
        "progress_bg": "#4C566A",
    },
}

_current_theme = THEMES["Light"]


def get_theme():
    return _current_theme


def set_theme(name):
    global _current_theme
    _current_theme = THEMES.get(name, THEMES["Light"])


# ── Stylesheet Generator ─────────────────────────────────────────────

def generate_stylesheet(t: dict) -> str:
    return f"""
    /* ── Global ── */
    QMainWindow, QWidget#centralWidget {{
        background-color: {t['bg_primary']};
    }}
    QLabel {{
        color: {t['text_primary']};
        background: transparent;
    }}
    QLabel[class="page-title"] {{
        font-size: 22px;
        font-weight: 700;
        color: {t['text_primary']};
    }}
    QLabel[class="page-desc"] {{
        font-size: 13px;
        color: {t['text_secondary']};
        margin-bottom: 4px;
    }}
    QLabel[class="section-title"] {{
        font-size: 14px;
        font-weight: 600;
        color: {t['text_primary']};
        margin-top: 8px;
    }}
    QLabel[class="section-label"] {{
        font-size: 12px;
        font-weight: 600;
        color: {t['text_secondary']};
        margin-top: 4px;
    }}
    QLabel[class="card-title"] {{
        font-size: 17px;
        font-weight: 600;
        color: {t['text_primary']};
    }}
    QLabel[class="hint"] {{
        font-size: 12px;
        color: {t['text_tertiary']};
    }}
    QLabel[class="meta"] {{
        font-size: 12px;
        color: {t['text_secondary']};
    }}
    QLabel[class="desc"] {{
        font-size: 13px;
        color: {t['text_secondary']};
        padding: 2px 0;
    }}
    QLabel[class="logo"] {{
        font-size: 18px;
        font-weight: 700;
        color: {t['accent']};
    }}
    QLabel[class="subtitle"] {{
        font-size: 11px;
        color: {t['text_tertiary']};
        margin-bottom: 8px;
    }}
    QLabel[class="stats"] {{
        font-size: 11px;
        color: {t['text_tertiary']};
        padding: 8px 0;
    }}
    QLabel[class="version-badge"] {{
        font-size: 12px;
        color: {t['accent']};
        background-color: {t['accent_subtle']};
        padding: 2px 8px;
        border-radius: 8px;
    }}
    QLabel[class="pill"] {{
        font-size: 11px;
        color: {t['badge_text']};
        background-color: {t['badge_bg']};
        padding: 3px 10px;
        border-radius: 10px;
    }}
    QLabel[class="staged-name"] {{
        font-size: 13px;
        font-weight: 500;
        color: {t['text_primary']};
    }}

    /* ── Inputs ── */
    QLineEdit {{
        background-color: {t['bg_input']};
        border: 1px solid {t['border']};
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 13px;
        color: {t['text_primary']};
        selection-background-color: {t['accent_subtle']};
    }}
    QLineEdit:focus {{
        border-color: {t['border_focus']};
    }}
    QLineEdit[class="search"] {{
        border-radius: 18px;
        padding: 10px 18px;
        font-size: 14px;
    }}
    QComboBox {{
        background-color: {t['bg_input']};
        border: 1px solid {t['border']};
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 13px;
        color: {t['text_primary']};
        min-width: 100px;
    }}
    QComboBox:focus {{
        border-color: {t['border_focus']};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {t['text_tertiary']};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {t['card_bg']};
        border: 1px solid {t['border']};
        border-radius: 6px;
        color: {t['text_primary']};
        selection-background-color: {t['accent_subtle']};
        selection-color: {t['text_primary']};
        padding: 4px;
    }}
    QCheckBox {{
        color: {t['text_primary']};
        font-size: 13px;
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 2px solid {t['border']};
        border-radius: 4px;
        background-color: {t['bg_input']};
    }}
    QCheckBox::indicator:checked {{
        background-color: {t['accent']};
        border-color: {t['accent']};
    }}

    /* ── Buttons ── */
    QPushButton {{
        font-size: 13px;
        font-weight: 500;
        border: 1px solid {t['border']};
        border-radius: 8px;
        padding: 8px 18px;
        color: {t['text_primary']};
        background-color: {t['bg_secondary']};
    }}
    QPushButton:hover {{
        background-color: {t['bg_tertiary']};
    }}
    QPushButton:pressed {{
        background-color: {t['border']};
    }}
    QPushButton:disabled {{
        color: {t['text_tertiary']};
        background-color: {t['bg_secondary']};
        border-color: {t['bg_tertiary']};
    }}
    QPushButton[class="accent"] {{
        background-color: {t['accent']};
        color: {t['accent_text']};
        border: none;
        border-radius: 8px;
        padding: 10px 24px;
        font-weight: 600;
    }}
    QPushButton[class="accent"]:hover {{
        background-color: {t['accent_hover']};
    }}
    QPushButton[class="accent"]:pressed {{
        background-color: {t['accent_pressed']};
    }}
    QPushButton[class="accent"]:disabled {{
        opacity: 0.5;
        background-color: {t['bg_tertiary']};
        color: {t['text_tertiary']};
    }}
    QPushButton[class="secondary"] {{
        background-color: transparent;
        border: 1px solid {t['accent']};
        color: {t['accent']};
        border-radius: 8px;
        padding: 8px 18px;
    }}
    QPushButton[class="secondary"]:hover {{
        background-color: {t['accent_subtle']};
    }}
    QPushButton[class="icon-btn"] {{
        background: transparent;
        border: none;
        color: {t['text_tertiary']};
        font-size: 14px;
        padding: 4px;
        border-radius: 4px;
    }}
    QPushButton[class="icon-btn"]:hover {{
        background-color: {t['bg_tertiary']};
        color: {t['text_primary']};
    }}

    /* ── Progress Bar ── */
    QProgressBar {{
        background-color: {t['progress_bg']};
        border: none;
        border-radius: 3px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {t['accent']};
        border-radius: 3px;
    }}

    /* ── Cards ── */
    QFrame[class="card"] {{
        background-color: {t['card_bg']};
        border: 1px solid {t['border']};
        border-radius: 12px;
        padding: 16px;
    }}
    QFrame[class="download-card"] {{
        background-color: {t['card_bg']};
        border: 1px solid {t['border']};
        border-radius: 8px;
    }}
    QFrame[class="staged-row"] {{
        background-color: {t['bg_secondary']};
        border: 1px solid {t['border']};
        border-radius: 6px;
    }}
    QFrame[class="staged-row"]:hover {{
        background-color: {t['sidebar_hover']};
    }}

    /* ── Drop Zone ── */
    QFrame[class="drop-zone"] {{
        background-color: {t['drop_zone_bg']};
        border: 2px dashed {t['drop_zone_border']};
        border-radius: 12px;
    }}
    QFrame[class="drop-zone"][dragHover="true"] {{
        border-color: {t['accent']};
        background-color: {t['accent_subtle']};
    }}

    /* ── Code Block ── */
    QFrame[class="code-block"] {{
        background-color: {t['code_bg']};
        border: 1px solid {t['border']};
        border-radius: 8px;
        padding: 12px;
    }}
    QFrame[class="code-block"] QLabel {{
        font-family: "Menlo", "Consolas", monospace;
        font-size: 12px;
        color: {t['code_text']};
    }}

    /* ── Sidebar ── */
    QFrame[class="sidebar"] {{
        background-color: {t['sidebar_bg']};
        border-right: 1px solid {t['border']};
    }}

    /* ── Scroll Area ── */
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {t['scrollbar_handle']};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t['scrollbar_handle']};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}

    /* ── Status Bar ── */
    QStatusBar {{
        background-color: {t['bg_secondary']};
        color: {t['text_secondary']};
        font-size: 12px;
        border-top: 1px solid {t['border']};
    }}

    /* ── Tooltip ── */
    QToolTip {{
        background-color: {t['card_bg']};
        color: {t['text_primary']};
        border: 1px solid {t['border']};
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
    }}
    """


# ── FlowLayout ───────────────────────────────────────────────────────

class FlowLayout(QLayout):
    """Layout that wraps widgets to the next line when horizontal space runs out."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        row_height = 0

        for item in self._items:
            next_x = x + item.sizeHint().width() + self._spacing
            if next_x - self._spacing > effective.right() and row_height > 0:
                x = effective.x()
                y += row_height + self._spacing
                next_x = x + item.sizeHint().width() + self._spacing
                row_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            row_height = max(row_height, item.sizeHint().height())

        return y + row_height - rect.y() + m.bottom()


# ── Base Components ───────────────────────────────────────────────────

class DropZone(QFrame):
    """Drag-and-drop area for requirements.txt files."""
    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "drop-zone")
        self.setAcceptDrops(True)
        self.setFixedHeight(80)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self.label = QLabel("Drop a requirements.txt file here")
        self.label.setProperty("class", "hint")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().endswith('.txt'):
                    event.acceptProposedAction()
                    self.setProperty("dragHover", "true")
                    self.style().unpolish(self)
                    self.style().polish(self)
                    return

    def dragLeaveEvent(self, event):
        self.setProperty("dragHover", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("dragHover", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.endswith('.txt'):
                self.file_dropped.emit(path)
                return


class CodeBlock(QFrame):
    """Styled read-only code display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "code-block")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.code_label = QLabel()
        self.code_label.setWordWrap(True)
        self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.code_label)
        self._text = ""

    def set_code(self, text):
        self._text = text
        self.code_label.setText(text)

    def get_code(self):
        return self._text


class PackageCard(QFrame):
    """Rich package info display with dependency pills."""
    add_to_queue = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")
        self.package_info = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        self.name_label = QLabel()
        self.name_label.setProperty("class", "card-title")
        header.addWidget(self.name_label)
        self.version_label = QLabel()
        self.version_label.setProperty("class", "version-badge")
        header.addWidget(self.version_label)
        header.addStretch()
        self.add_btn = QPushButton("Add to Queue")
        self.add_btn.setProperty("class", "accent")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(lambda: self.add_to_queue.emit(self.package_info))
        header.addWidget(self.add_btn)
        layout.addLayout(header)

        # Description
        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setProperty("class", "desc")
        layout.addWidget(self.desc_label)

        # Meta row
        self.meta_label = QLabel()
        self.meta_label.setProperty("class", "meta")
        layout.addWidget(self.meta_label)

        # Dependencies flow
        self.deps_label = QLabel("Dependencies:")
        self.deps_label.setProperty("class", "section-label")
        layout.addWidget(self.deps_label)

        self.deps_container = QWidget()
        self.deps_flow = FlowLayout(self.deps_container, spacing=6)
        layout.addWidget(self.deps_container)

    def set_package(self, pkg: PackageInfo):
        self.package_info = pkg
        self.name_label.setText(pkg.name)
        self.version_label.setText(pkg.version)
        self.desc_label.setText(pkg.description or "No description available")
        author = pkg.author or "N/A"
        lic = pkg.license or "N/A"
        self.meta_label.setText(f"Author: {author}  |  License: {lic}")

        # Clear old pills
        while self.deps_flow.count():
            item = self.deps_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if pkg.dependencies:
            self.deps_label.show()
            self.deps_container.show()
            shown = pkg.dependencies[:20]
            for dep_str in shown:
                try:
                    req = Requirement(dep_str)
                    pill = QLabel(req.name)
                    pill.setProperty("class", "pill")
                    self.deps_flow.addWidget(pill)
                except Exception:
                    pass
            if len(pkg.dependencies) > 20:
                more = QLabel(f"+{len(pkg.dependencies) - 20} more")
                more.setProperty("class", "pill")
                self.deps_flow.addWidget(more)
        else:
            self.deps_label.hide()
            self.deps_container.hide()


class StagedPackageRow(QFrame):
    """Compact row for a staged package."""
    remove_clicked = pyqtSignal(str)

    def __init__(self, name, version, is_dep=False, parent=None):
        super().__init__(parent)
        self.pkg_name = name
        self.setProperty("class", "staged-row")
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)

        name_label = QLabel(name)
        name_label.setProperty("class", "staged-name")
        layout.addWidget(name_label)

        ver_label = QLabel(version)
        ver_label.setProperty("class", "hint")
        layout.addWidget(ver_label)

        if is_dep:
            dep_badge = QLabel("dependency")
            dep_badge.setProperty("class", "pill")
            layout.addWidget(dep_badge)

        layout.addStretch()

        remove_btn = QPushButton("\u00d7")
        remove_btn.setProperty("class", "icon-btn")
        remove_btn.setFixedSize(24, 24)
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(name))
        layout.addWidget(remove_btn)


class DownloadItemCard(QFrame):
    """Single download row with progress bar and status."""
    cancel_clicked = pyqtSignal()
    retry_clicked = pyqtSignal()

    def __init__(self, download_id, parent=None):
        super().__init__(parent)
        self.download_id = download_id
        self.setProperty("class", "download-card")
        self.setFixedHeight(56)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Top row
        top = QHBoxLayout()
        top.setSpacing(8)
        self.status_dot = QLabel("\u25cf")
        self.status_dot.setFixedWidth(16)
        top.addWidget(self.status_dot)

        self.filename_label = QLabel()
        self.filename_label.setProperty("class", "staged-name")
        top.addWidget(self.filename_label, 1)

        self.size_label = QLabel()
        self.size_label.setProperty("class", "hint")
        top.addWidget(self.size_label)

        self.speed_label = QLabel()
        self.speed_label.setProperty("class", "hint")
        self.speed_label.setFixedWidth(80)
        top.addWidget(self.speed_label)

        self.action_btn = QPushButton("Cancel")
        self.action_btn.setProperty("class", "icon-btn")
        self.action_btn.setFixedWidth(60)
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.clicked.connect(self._on_action)
        top.addWidget(self.action_btn)

        layout.addLayout(top)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

    def update_progress(self, d):
        self.filename_label.setText(d.get('filename', ''))
        self.progress_bar.setValue(int(d.get('progress', 0)))

        total = d.get('total_bytes', 0)
        dl = d.get('downloaded_bytes', 0)
        self.size_label.setText(f"{format_bytes(dl)} / {format_bytes(total)}" if total else "")

        speed = d.get('speed', 0)
        self.speed_label.setText(f"{format_bytes(speed)}/s" if speed > 0 else "")

        status = d.get('status', DownloadStatus.QUEUED)
        t = get_theme()
        if status == DownloadStatus.DOWNLOADING:
            self.status_dot.setStyleSheet(f"color: {t['warning']}; background: transparent;")
            self.action_btn.setText("Cancel")
            self.action_btn.setEnabled(True)
        elif status == DownloadStatus.COMPLETED:
            self.status_dot.setStyleSheet(f"color: {t['success']}; background: transparent;")
            self.action_btn.setText("Done")
            self.action_btn.setEnabled(False)
        elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            self.status_dot.setStyleSheet(f"color: {t['error']}; background: transparent;")
            self.action_btn.setText("Retry")
            self.action_btn.setEnabled(True)
        else:
            self.status_dot.setStyleSheet(f"color: {t['text_tertiary']}; background: transparent;")
            self.action_btn.setText("Cancel")

    def _on_action(self):
        if self.action_btn.text() == "Retry":
            self.retry_clicked.emit()
        else:
            self.cancel_clicked.emit()


# ── Sidebar ───────────────────────────────────────────────────────────

class StepIndicator(QWidget):
    """Custom painted numbered step in sidebar."""
    clicked = pyqtSignal()

    def __init__(self, number, title, subtitle="", parent=None):
        super().__init__(parent)
        self.number = number
        self.title = title
        self.subtitle = subtitle
        self.active = False
        self.completed = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(52)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        t = get_theme()

        cx, cy, r = 22, self.height() // 2, 12

        # Background highlight for active
        if self.active:
            painter.setBrush(QColor(t['sidebar_active']))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(0, 2, self.width(), self.height() - 4, 8, 8)

        # Circle
        if self.active:
            painter.setBrush(QColor(t['accent']))
            painter.setPen(Qt.NoPen)
        elif self.completed:
            painter.setBrush(QColor(t['success']))
            painter.setPen(Qt.NoPen)
        else:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(t['border']), 1.5))
        painter.drawEllipse(QPoint(cx, cy), r, r)

        # Number or checkmark
        if self.completed:
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.drawLine(cx - 4, cy, cx - 1, cy + 3)
            painter.drawLine(cx - 1, cy + 3, cx + 5, cy - 3)
        else:
            tc = QColor(t['accent_text']) if self.active else QColor(t['text_secondary'])
            painter.setPen(tc)
            f = QFont(FONT_FAMILY, 10, QFont.Bold)
            painter.setFont(f)
            painter.drawText(QRect(cx - r, cy - r, r * 2, r * 2), Qt.AlignCenter, str(self.number))

        # Title
        text_x = cx + r + 12
        tc = QColor(t['text_primary']) if self.active else QColor(t['text_secondary'])
        painter.setPen(tc)
        weight = QFont.DemiBold if self.active else QFont.Normal
        painter.setFont(QFont(FONT_FAMILY, 12, weight))
        painter.drawText(text_x, cy + (4 if not self.subtitle else -1), self.title)

        # Subtitle
        if self.subtitle:
            painter.setPen(QColor(t['text_tertiary']))
            painter.setFont(QFont(FONT_FAMILY, 10))
            painter.drawText(text_x, cy + 13, self.subtitle)

        painter.end()

    def mousePressEvent(self, event):
        self.clicked.emit()


class SidebarWidget(QFrame):
    """Left navigation sidebar with step indicators and stats."""
    page_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "sidebar")
        self.setFixedWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(2)

        # Logo
        logo = QLabel("LocalPip")
        logo.setProperty("class", "logo")
        layout.addWidget(logo)

        subtitle = QLabel("Offline Package Manager")
        subtitle.setProperty("class", "subtitle")
        layout.addWidget(subtitle)

        layout.addSpacing(20)

        # Steps
        self.steps = []
        step_data = [
            (1, "Configure", "Environment setup"),
            (2, "Search", "Find & stage packages"),
            (3, "Downloads", "Track progress"),
            (4, "Transfer", "Install offline"),
        ]
        for num, title, sub in step_data:
            step = StepIndicator(num, title, sub)
            idx = num - 1
            step.clicked.connect(lambda n=idx: self._on_step(n))
            self.steps.append(step)
            layout.addWidget(step)

        layout.addStretch()

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {get_theme()['border']};")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Stats
        self.stats_label = QLabel("No packages staged")
        self.stats_label.setProperty("class", "stats")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

    def _on_step(self, index):
        self.set_active(index)
        self.page_changed.emit(index)

    def set_active(self, index):
        for i, step in enumerate(self.steps):
            step.active = (i == index)
            step.update()

    def set_completed(self, index):
        self.steps[index].completed = True
        self.steps[index].update()

    def update_stats(self, text):
        self.stats_label.setText(text)


# ── Page 1: Configure ────────────────────────────────────────────────

class ConfigurePage(QScrollArea):
    """Target environment, output directory, network, and theme settings."""
    continue_clicked = pyqtSignal()
    theme_changed = pyqtSignal(str)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config = config_manager
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(20)

        title = QLabel("Configure")
        title.setProperty("class", "page-title")
        layout.addWidget(title)
        desc = QLabel("Set up your target environment and download preferences.")
        desc.setProperty("class", "page-desc")
        layout.addWidget(desc)

        # ── Card 1: Target Environment ──
        env_card = QFrame()
        env_card.setProperty("class", "card")
        ec = QVBoxLayout(env_card)
        ec.setSpacing(12)
        env_title = QLabel("Target Environment")
        env_title.setProperty("class", "section-title")
        ec.addWidget(env_title)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Python Version"))
        self.python_combo = QComboBox()
        self.python_combo.addItems(["3.13", "3.12", "3.11", "3.10", "3.9", "3.8"])
        row1.addWidget(self.python_combo)
        row1.addSpacing(24)
        row1.addWidget(QLabel("Platform"))
        self.platform_combo = QComboBox()
        self.platform_combo.addItems(["any", "win_amd64", "manylinux2014_x86_64"])
        row1.addWidget(self.platform_combo)
        row1.addStretch()
        ec.addLayout(row1)

        self.include_deps = QCheckBox("Include dependencies")
        self.include_deps.setChecked(True)
        ec.addWidget(self.include_deps)
        layout.addWidget(env_card)

        # ── Card 2: Output Directory ──
        out_card = QFrame()
        out_card.setProperty("class", "card")
        oc = QVBoxLayout(out_card)
        oc.setSpacing(12)
        out_title = QLabel("Output Directory")
        out_title.setProperty("class", "section-title")
        oc.addWidget(out_title)

        path_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Choose a directory for downloaded wheels")
        path_row.addWidget(self.output_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.setProperty("class", "secondary")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        oc.addLayout(path_row)

        self.whl_count = QLabel("")
        self.whl_count.setProperty("class", "hint")
        oc.addWidget(self.whl_count)
        layout.addWidget(out_card)

        # ── Card 3: Network & Appearance ──
        net_card = QFrame()
        net_card.setProperty("class", "card")
        nc = QVBoxLayout(net_card)
        nc.setSpacing(12)
        net_title = QLabel("Network & Appearance")
        net_title.setProperty("class", "section-title")
        nc.addWidget(net_title)

        mirror_row = QHBoxLayout()
        mirror_row.addWidget(QLabel("PyPI Mirror"))
        self.mirror_edit = QLineEdit()
        self.mirror_edit.setPlaceholderText("https://pypi.org/simple/")
        mirror_row.addWidget(self.mirror_edit)
        nc.addLayout(mirror_row)

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        self.theme_combo.currentTextChanged.connect(self.theme_changed.emit)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch()
        nc.addLayout(theme_row)
        layout.addWidget(net_card)

        layout.addStretch()

        # Continue button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        continue_btn = QPushButton("Continue to Search \u2192")
        continue_btn.setProperty("class", "accent")
        continue_btn.setCursor(Qt.PointingHandCursor)
        continue_btn.clicked.connect(self.continue_clicked.emit)
        btn_row.addWidget(continue_btn)
        layout.addLayout(btn_row)

        self.setWidget(container)
        self._load_settings()

    def _load_settings(self):
        self.output_edit.setText(self.config.get("download.default_path", ""))
        self.mirror_edit.setText(self.config.get("network.pypi_mirror", "https://pypi.org/simple/"))

        py_ver = self.config.get("download.python_version", "3.11")
        idx = self.python_combo.findText(py_ver)
        if idx >= 0:
            self.python_combo.setCurrentIndex(idx)

        plat = self.config.get("download.platform", "any")
        idx = self.platform_combo.findText(plat)
        if idx >= 0:
            self.platform_combo.setCurrentIndex(idx)

        inc = self.config.get("download.include_dependencies", True)
        self.include_deps.setChecked(inc)

        theme = self.config.get("ui.theme", "Light")
        idx = self.theme_combo.findText(theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)

        self._update_whl_count()

    def save_settings(self):
        self.config.set("download.default_path", self.output_edit.text())
        self.config.set("network.pypi_mirror", self.mirror_edit.text())
        self.config.set("download.python_version", self.python_combo.currentText())
        self.config.set("download.platform", self.platform_combo.currentText())
        self.config.set("download.include_dependencies", self.include_deps.isChecked())
        self.config.set("ui.theme", self.theme_combo.currentText())

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.output_edit.text())
        if path:
            self.output_edit.setText(path)
            self._update_whl_count()

    def _update_whl_count(self):
        path = self.output_edit.text()
        if os.path.isdir(path):
            count = len([f for f in os.listdir(path) if f.endswith('.whl')])
            self.whl_count.setText(f"{count} .whl file{'s' if count != 1 else ''} in directory" if count else "Directory is empty")
        else:
            self.whl_count.setText("")


# ── Page 2: Search & Stage ───────────────────────────────────────────

class SearchPage(QWidget):
    """Search for packages, view details, stage for download."""
    download_all_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(14)

        title = QLabel("Search & Stage")
        title.setProperty("class", "page-title")
        layout.addWidget(title)
        desc = QLabel("Find packages on PyPI and stage them for download.")
        desc.setProperty("class", "page-desc")
        layout.addWidget(desc)

        # Search row
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_bar = QLineEdit()
        self.search_bar.setProperty("class", "search")
        self.search_bar.setPlaceholderText("Enter package name (e.g., requests, flask==2.0)")
        search_row.addWidget(self.search_bar)

        self.search_btn = QPushButton("Search")
        self.search_btn.setProperty("class", "accent")
        self.search_btn.setCursor(Qt.PointingHandCursor)
        search_row.addWidget(self.search_btn)

        self.import_btn = QPushButton("Import")
        self.import_btn.setProperty("class", "secondary")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        search_row.addWidget(self.import_btn)
        layout.addLayout(search_row)

        # Drop zone
        self.drop_zone = DropZone()
        layout.addWidget(self.drop_zone)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_widget = QWidget()
        self.content_layout = QVBoxLayout(scroll_widget)
        self.content_layout.setAlignment(Qt.AlignTop)
        self.content_layout.setSpacing(10)

        # Package card (hidden until search)
        self.package_card = PackageCard()
        self.package_card.hide()
        self.content_layout.addWidget(self.package_card)

        # Resolution status
        self.resolution_label = QLabel("")
        self.resolution_label.setProperty("class", "hint")
        self.content_layout.addWidget(self.resolution_label)

        # Staged packages header
        self.staged_header = QLabel("Staged Packages")
        self.staged_header.setProperty("class", "section-title")
        self.staged_header.hide()
        self.content_layout.addWidget(self.staged_header)

        # Staged packages list
        self.staged_list_widget = QWidget()
        self.staged_list_layout = QVBoxLayout(self.staged_list_widget)
        self.staged_list_layout.setContentsMargins(0, 0, 0, 0)
        self.staged_list_layout.setSpacing(4)
        self.content_layout.addWidget(self.staged_list_widget)

        self.content_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, 1)

        # Download button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.download_btn = QPushButton("Review & Download \u2192")
        self.download_btn.setProperty("class", "accent")
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self.download_all_clicked.emit)
        btn_row.addWidget(self.download_btn)
        layout.addLayout(btn_row)

    def show_package(self, package_info):
        self.package_card.set_package(package_info)
        self.package_card.show()

    def add_staged_row(self, name, version, is_dep):
        row = StagedPackageRow(name, version, is_dep)
        row.remove_clicked.connect(self._on_remove)
        self.staged_list_layout.addWidget(row)
        self.staged_header.show()
        self.download_btn.setEnabled(True)

    def _on_remove(self, name):
        # Signal to MainWindow to remove from staged dict
        # We find and remove the widget here; MainWindow handles the data
        for i in range(self.staged_list_layout.count()):
            item = self.staged_list_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if isinstance(w, StagedPackageRow) and w.pkg_name == name:
                    w.deleteLater()
                    break
        # Check if any staged remain
        remaining = 0
        for i in range(self.staged_list_layout.count()):
            item = self.staged_list_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), StagedPackageRow):
                remaining += 1
        if remaining <= 1:  # The one being deleted is still counted briefly
            self.staged_header.hide()
            self.download_btn.setEnabled(False)

    def set_resolution_status(self, text):
        self.resolution_label.setText(text)

    def clear_staged(self):
        while self.staged_list_layout.count():
            item = self.staged_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.staged_header.hide()
        self.download_btn.setEnabled(False)
        self.package_card.hide()
        self.resolution_label.setText("")

    def update_download_btn_state(self, count):
        self.download_btn.setEnabled(count > 0)
        if count > 0:
            self.staged_header.show()


# ── Page 3: Downloads ─────────────────────────────────────────────────

class DownloadsPage(QWidget):
    """Real-time download progress with individual cards."""
    transfer_clicked = pyqtSignal()

    def __init__(self, download_manager, parent=None):
        super().__init__(parent)
        self.dm = download_manager
        self.cards: Dict[str, DownloadItemCard] = {}
        self.last_update: Dict[str, float] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(16)

        title = QLabel("Downloads")
        title.setProperty("class", "page-title")
        layout.addWidget(title)

        # Overall progress card
        progress_card = QFrame()
        progress_card.setProperty("class", "card")
        pc = QVBoxLayout(progress_card)
        pc.setSpacing(8)

        self.overall_bar = QProgressBar()
        self.overall_bar.setFixedHeight(8)
        self.overall_bar.setTextVisible(False)
        pc.addWidget(self.overall_bar)

        self.stats_label = QLabel("Waiting for downloads...")
        self.stats_label.setProperty("class", "hint")
        pc.addWidget(self.stats_label)
        layout.addWidget(progress_card)

        # Scroll area for download cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.cards_widget = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_widget)
        self.cards_layout.setAlignment(Qt.AlignTop)
        self.cards_layout.setSpacing(4)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards_widget)
        layout.addWidget(scroll, 1)

        # Transfer button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.transfer_btn = QPushButton("Continue to Transfer \u2192")
        self.transfer_btn.setProperty("class", "accent")
        self.transfer_btn.setCursor(Qt.PointingHandCursor)
        self.transfer_btn.setEnabled(False)
        self.transfer_btn.clicked.connect(self.transfer_clicked.emit)
        btn_row.addWidget(self.transfer_btn)
        layout.addLayout(btn_row)

        # Connect
        self.dm.progress_updated.connect(self._on_progress)

    def _on_progress(self, download_id, progress_dict):
        now = time.time()
        status = progress_dict.get('status', DownloadStatus.QUEUED)
        is_terminal = status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)

        if not is_terminal:
            if now - self.last_update.get(download_id, 0) < 0.1:
                return
            self.last_update[download_id] = now

        if download_id not in self.cards:
            card = DownloadItemCard(download_id)
            card.cancel_clicked.connect(lambda did=download_id: self.dm.cancel_download(did))
            card.retry_clicked.connect(lambda did=download_id: self.dm.retry_download(did))
            self.cards[download_id] = card
            # Insert before the stretch
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        self.cards[download_id].update_progress(progress_dict)
        self._update_overall()

    def _update_overall(self):
        queue = self.dm.get_queue()
        if not queue:
            return
        total = len(queue)
        completed = sum(1 for item in queue if item.status == DownloadStatus.COMPLETED)
        failed = sum(1 for item in queue if item.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED))
        total_bytes = sum(item.total_bytes for item in queue)
        dl_bytes = sum(item.downloaded_bytes for item in queue)
        speed = sum(item.speed for item in queue if item.status == DownloadStatus.DOWNLOADING)

        pct = int(dl_bytes / total_bytes * 100) if total_bytes > 0 else 0
        self.overall_bar.setValue(pct)

        parts = [f"{completed} of {total} complete"]
        if failed:
            parts.append(f"{failed} failed")
        parts.append(f"{format_bytes(dl_bytes)} / {format_bytes(total_bytes)}")
        if speed > 0:
            parts.append(f"{format_bytes(speed)}/s")
        self.stats_label.setText("  \u2022  ".join(parts))

        all_done = all(
            item.status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)
            for item in queue
        )
        if all_done and total > 0:
            self.transfer_btn.setEnabled(True)

    def reset(self):
        for card in self.cards.values():
            card.deleteLater()
        self.cards.clear()
        self.last_update.clear()
        self.overall_bar.setValue(0)
        self.stats_label.setText("Waiting for downloads...")
        self.transfer_btn.setEnabled(False)


# ── Page 4: Transfer ──────────────────────────────────────────────────

class TransferPage(QScrollArea):
    """Summary, pip install command, and file listing."""
    new_download_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._output_path = ""

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(16)

        title = QLabel("Transfer")
        title.setProperty("class", "page-title")
        layout.addWidget(title)
        desc = QLabel("Your packages are ready. Copy the folder to the offline machine and run the install command.")
        desc.setWordWrap(True)
        desc.setProperty("class", "page-desc")
        layout.addWidget(desc)

        # Summary card
        summary_card = QFrame()
        summary_card.setProperty("class", "card")
        sc = QVBoxLayout(summary_card)
        sc.setSpacing(10)
        sc_title = QLabel("Summary")
        sc_title.setProperty("class", "section-title")
        sc.addWidget(sc_title)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        sc.addWidget(self.summary_label)

        open_btn = QPushButton("Open Folder")
        open_btn.setProperty("class", "secondary")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(self._open_folder)
        sc.addWidget(open_btn, alignment=Qt.AlignLeft)
        layout.addWidget(summary_card)

        # Command card
        cmd_card = QFrame()
        cmd_card.setProperty("class", "card")
        cc = QVBoxLayout(cmd_card)
        cc.setSpacing(10)
        cc_title = QLabel("Install Command")
        cc_title.setProperty("class", "section-title")
        cc.addWidget(cc_title)

        self.code_block = CodeBlock()
        cc.addWidget(self.code_block)

        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.setProperty("class", "accent")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_command)
        cc.addWidget(copy_btn, alignment=Qt.AlignLeft)
        layout.addWidget(cmd_card)

        # Files card
        files_card = QFrame()
        files_card.setProperty("class", "card")
        fc = QVBoxLayout(files_card)
        fc.setSpacing(8)
        fc_title = QLabel("Downloaded Files")
        fc_title.setProperty("class", "section-title")
        fc.addWidget(fc_title)
        self.files_label = QLabel("")
        self.files_label.setWordWrap(True)
        self.files_label.setProperty("class", "hint")
        fc.addWidget(self.files_label)
        layout.addWidget(files_card)

        layout.addStretch()

        # New download button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        new_btn = QPushButton("Start New Download")
        new_btn.setProperty("class", "secondary")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self.new_download_clicked.emit)
        btn_row.addWidget(new_btn)
        layout.addLayout(btn_row)

        self.setWidget(container)

    def populate(self, output_path, staged_names):
        self._output_path = output_path
        whl_files = []
        total_size = 0
        if os.path.isdir(output_path):
            for f in sorted(os.listdir(output_path)):
                if f.endswith('.whl'):
                    fpath = os.path.join(output_path, f)
                    size = os.path.getsize(fpath)
                    whl_files.append((f, size))
                    total_size += size

        self.summary_label.setText(
            f"{len(whl_files)} package{'s' if len(whl_files) != 1 else ''}  \u2022  "
            f"{format_bytes(total_size)}  \u2022  {output_path}"
        )

        # Generate pip install command
        cmd = f"pip install --no-index --find-links \"{output_path}\""
        if staged_names:
            cmd += " " + " ".join(sorted(staged_names))
        else:
            # Fallback: list wheel filenames
            names = set()
            for f, _ in whl_files:
                parts = f.split('-')
                if parts:
                    names.add(parts[0].replace('_', '-'))
            cmd += " " + " ".join(sorted(names))
        self.code_block.set_code(cmd)

        # File listing
        lines = []
        for f, size in whl_files:
            lines.append(f"{f}  ({format_bytes(size)})")
        self.files_label.setText("\n".join(lines) if lines else "No .whl files found")

    def _open_folder(self):
        if self._output_path and os.path.isdir(self._output_path):
            if sys.platform == "darwin":
                os.system(f'open "{self._output_path}"')
            elif sys.platform == "win32":
                os.startfile(self._output_path)
            else:
                os.system(f'xdg-open "{self._output_path}"')

    def _copy_command(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.code_block.get_code())


# ── MainWindow ────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Primary application window with sidebar navigation and 4 pages."""

    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager("config.json")
        self.search_engine = SearchEngine("packages.db")
        self.download_manager = DownloadManager()

        self.staged_packages: Dict[str, StagedPackage] = {}
        self.processed_packages: set = set()
        self._resolving = False

        self._init_ui()
        self._connect_signals()
        self._apply_theme(self.config_manager.get("ui.theme", "Light"))

    def _init_ui(self):
        self.setWindowTitle("LocalPip")
        w = self.config_manager.get("ui.window_size.width", 1100)
        h = self.config_manager.get("ui.window_size.height", 750)
        self.resize(w, h)
        self.setMinimumSize(900, 600)

        # Central widget
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self.sidebar = SidebarWidget()
        main_layout.addWidget(self.sidebar)

        # Stacked pages
        self.stack = QStackedWidget()
        self.configure_page = ConfigurePage(self.config_manager)
        self.search_page = SearchPage()
        self.downloads_page = DownloadsPage(self.download_manager)
        self.transfer_page = TransferPage()

        self.stack.addWidget(self.configure_page)
        self.stack.addWidget(self.search_page)
        self.stack.addWidget(self.downloads_page)
        self.stack.addWidget(self.transfer_page)
        main_layout.addWidget(self.stack, 1)

        # Status bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

        # Start on page 0
        self.sidebar.set_active(0)

    def _connect_signals(self):
        # Sidebar navigation
        self.sidebar.page_changed.connect(self._go_to_page)

        # Configure page
        self.configure_page.continue_clicked.connect(lambda: self._go_to_page(1))
        self.configure_page.theme_changed.connect(self._apply_theme)

        # Search page
        self.search_page.search_btn.clicked.connect(self._on_search)
        self.search_page.search_bar.returnPressed.connect(self._on_search)
        self.search_page.import_btn.clicked.connect(self._on_import)
        self.search_page.drop_zone.file_dropped.connect(self._on_file_dropped)
        self.search_page.package_card.add_to_queue.connect(self._on_add_to_queue)
        self.search_page.download_all_clicked.connect(self._on_download_all)

        # Downloads page
        self.downloads_page.transfer_clicked.connect(self._on_transfer)

        # Transfer page
        self.transfer_page.new_download_clicked.connect(self._on_new_download)

    def _go_to_page(self, index):
        # Save config when leaving configure
        if self.stack.currentIndex() == 0:
            self.configure_page.save_settings()
            self.config_manager.save()

        self.stack.setCurrentIndex(index)
        self.sidebar.set_active(index)

        # Mark completed steps
        if index > 0:
            self.sidebar.set_completed(0)
        if index > 1:
            self.sidebar.set_completed(1)
        if index > 2:
            self.sidebar.set_completed(2)

    def _apply_theme(self, theme_name):
        if theme_name not in THEMES:
            theme_name = "Light"
        set_theme(theme_name)
        QApplication.instance().setStyleSheet(generate_stylesheet(get_theme()))
        self.config_manager.set("ui.theme", theme_name)
        # Repaint custom-painted widgets
        for step in self.sidebar.steps:
            step.update()

    # ── Search & Staging ──

    def _on_search(self):
        query = self.search_page.search_bar.text().strip()
        if not query:
            return
        self.search_page.search_btn.setEnabled(False)
        self.search_page.set_resolution_status(f"Searching for {query}...")
        self.status_bar.showMessage(f"Searching for '{query}'...")

        worker = Worker(self._search_work, query)
        self.search_engine.threadpool.start(worker)

    def _search_work(self, query):
        """Worker thread: fetch package details for display."""
        pypi_mirror = self.config_manager.get("network.pypi_mirror", "https://pypi.org/simple/")
        pkg = self.search_engine.get_package_details(query, pypi_mirror)
        if pkg:
            QApplication.instance().postEvent(self, PackageFoundEvent(pkg))
        else:
            QApplication.instance().postEvent(self, PackageNotFoundEvent(query))

    def _on_add_to_queue(self, package_info):
        """User clicked 'Add to Queue' on the package card."""
        if package_info is None:
            return
        self._resolving = True
        self.search_page.set_resolution_status("Resolving dependencies...")
        self.search_page.search_btn.setEnabled(False)

        # Start resolution in background
        worker = Worker(self._resolve_work, [package_info.name])
        self.search_engine.threadpool.start(worker)

    def _on_import(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Requirements", "", "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            self._import_file(file_path)

    def _on_file_dropped(self, file_path):
        self._import_file(file_path)

    def _import_file(self, file_path):
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            packages = []
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('-'):
                    continue
                packages.append(line)
            if not packages:
                QMessageBox.warning(self, "Import Error", "No valid packages found in file.")
                return

            self._resolving = True
            self.search_page.set_resolution_status(f"Resolving {len(packages)} packages...")
            self.search_page.search_btn.setEnabled(False)
            self.status_bar.showMessage(f"Resolving {len(packages)} packages from requirements...")

            worker = Worker(self._resolve_work, packages)
            self.search_engine.threadpool.start(worker)
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to read file: {e}")

    def _get_evaluation_environment(self) -> Dict:
        py_ver = self.configure_page.python_combo.currentText()
        platform = self.configure_page.platform_combo.currentText()
        env = {
            'python_version': '.'.join(py_ver.split('.')[:2]),
            'python_full_version': py_ver,
        }
        if 'win' in platform:
            env['sys_platform'] = 'win32'
            env['os_name'] = 'nt'
        else:
            env['sys_platform'] = 'linux'
            env['os_name'] = 'posix'
        return env

    def _resolve_work(self, initial_packages: List[str]):
        """Worker thread: recursively resolve packages and post staging events."""
        packages_to_process = list(initial_packages)
        pypi_mirror = self.config_manager.get("network.pypi_mirror", "https://pypi.org/simple/")
        environment = self._get_evaluation_environment()
        include_deps = self.configure_page.include_deps.isChecked()
        is_first = True

        while packages_to_process:
            package_name = packages_to_process.pop(0)
            normalized = Requirement(package_name).name.lower()

            if normalized in self.processed_packages:
                continue

            QApplication.instance().postEvent(
                self, StatusUpdateEvent(f"Resolving {package_name}...")
            )

            pkg = self.search_engine.get_package_details(package_name, pypi_mirror)
            if pkg:
                self.processed_packages.add(normalized)
                is_dep = not is_first and normalized not in {
                    Requirement(p).name.lower() for p in initial_packages
                }
                is_first = False
                QApplication.instance().postEvent(
                    self, PackageStagedEvent(pkg, is_dep)
                )

                if include_deps and pkg.dependencies:
                    for dep_string in pkg.dependencies:
                        try:
                            req = Requirement(dep_string)
                            if req.marker and not req.marker.evaluate(environment=environment):
                                continue
                            if req.name.lower() not in self.processed_packages:
                                packages_to_process.append(req.name)
                        except Exception:
                            pass
            else:
                QApplication.instance().postEvent(
                    self, PackageNotFoundEvent(package_name)
                )

        QApplication.instance().postEvent(
            self, StatusUpdateEvent("Resolution complete.")
        )

    def customEvent(self, event):
        if event.type() == PackageFoundEvent.EVENT_TYPE:
            self.search_page.show_package(event.package_details)
            self.search_page.search_btn.setEnabled(True)
            self.search_page.set_resolution_status("")
            self.status_bar.showMessage("Package found.", 3000)

        elif event.type() == PackageStagedEvent.EVENT_TYPE:
            pkg = event.package_info
            normalized = pkg.name.lower()
            if normalized not in self.staged_packages:
                self.staged_packages[normalized] = StagedPackage(
                    package_info=pkg, is_dependency=event.is_dependency
                )
                self.search_page.add_staged_row(pkg.name, pkg.version, event.is_dependency)
                self._update_sidebar_stats()

        elif event.type() == PackageNotFoundEvent.EVENT_TYPE:
            self.status_bar.showMessage(f"Could not find: {event.package_name}", 5000)
            self.search_page.search_btn.setEnabled(True)

        elif event.type() == StatusUpdateEvent.EVENT_TYPE:
            self.status_bar.showMessage(event.message)
            self.search_page.set_resolution_status(event.message)
            if "complete" in event.message.lower():
                self._resolving = False
                self.search_page.search_btn.setEnabled(True)
                self.search_page.update_download_btn_state(len(self.staged_packages))

    def _update_sidebar_stats(self):
        n = len(self.staged_packages)
        deps = sum(1 for s in self.staged_packages.values() if s.is_dependency)
        root = n - deps
        parts = []
        if root:
            parts.append(f"{root} package{'s' if root != 1 else ''}")
        if deps:
            parts.append(f"{deps} dep{'s' if deps != 1 else ''}")
        self.sidebar.update_stats(" + ".join(parts) if parts else "No packages staged")

    # ── Download All ──

    def _on_download_all(self):
        if not self.staged_packages:
            return

        # Save settings and ensure output dir exists
        self.configure_page.save_settings()
        self.config_manager.save()
        output_dir = self.configure_page.output_edit.text()
        if not output_dir:
            QMessageBox.warning(self, "Error", "Please set an output directory on the Configure page.")
            return
        os.makedirs(output_dir, exist_ok=True)

        py_ver = self.configure_page.python_combo.currentText()
        platform = self.configure_page.platform_combo.currentText()

        # Reset downloads page
        self.downloads_page.reset()
        self.download_manager.reset()

        # Queue all staged packages
        for staged in self.staged_packages.values():
            self.download_manager.add_to_queue(
                staged.package_info, py_ver, platform, output_dir
            )

        self._go_to_page(2)
        self.status_bar.showMessage("Downloading...")

    # ── Transfer ──

    def _on_transfer(self):
        output_dir = self.configure_page.output_edit.text()
        root_names = {
            s.package_info.name for s in self.staged_packages.values()
            if not s.is_dependency
        }
        self.transfer_page.populate(output_dir, root_names)
        self._go_to_page(3)

    # ── New Download ──

    def _on_new_download(self):
        self.staged_packages.clear()
        self.processed_packages.clear()
        self.search_page.clear_staged()
        self.downloads_page.reset()
        self.download_manager.reset()
        self._update_sidebar_stats()
        self._go_to_page(1)

    # ── Window lifecycle ──

    def closeEvent(self, event):
        self.configure_page.save_settings()
        self.config_manager.set("ui.window_size.width", self.width())
        self.config_manager.set("ui.window_size.height", self.height())
        self.config_manager.save()
        event.accept()


# ── Entry Point ───────────────────────────────────────────────────────

def main():
    try:
        from packaging.requirements import Requirement
    except ImportError:
        print("ERROR: 'packaging' library not found. Install it: pip install packaging")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("LocalPip")
    app.setStyle('Fusion')

    # Set default font
    font = QFont(FONT_FAMILY, 13)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
