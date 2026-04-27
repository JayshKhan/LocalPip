# PyInstaller spec — bundles the GUI + CLI as a single binary.
# Build:  pyinstaller LocalPip.spec
# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['localpip/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules('localpip') + [
        'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        'packaging.tags', 'packaging.markers', 'packaging.requirements',
        'packaging.specifiers', 'packaging.version',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt6', 'PySide2', 'PySide6', 'matplotlib', 'numpy'],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LocalPip',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # GUI mode by default; `LocalPip --help` still works on Linux/macOS
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='LocalPip.app',
        icon=None,
        bundle_identifier='com.jayshkhan.localpip',
        info_plist={
            'CFBundleShortVersionString': '0.2.0',
            'NSHighResolutionCapable': 'True',
            'LSApplicationCategoryType': 'public.app-category.developer-tools',
        },
    )
