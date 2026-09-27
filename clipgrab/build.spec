# -*- mode: python ; coding: utf-8 -*-
"""
YouCut — PyInstaller build spec.

Produces a one-folder distribution (--onedir) with ffmpeg.exe and yt-dlp.exe bundled.
Uses onedir mode for fast startup (no temp extraction on each launch).

Output:
    dist/YouCut/YouCut.exe (and bundled dependencies)
"""

import os
import sys

block_cipher = None

# Resolve absolute paths
SPEC_DIR = os.path.abspath(os.path.dirname(__file__)) if '__file__' in globals() else os.path.abspath(os.path.dirname(SPEC))
ASSETS_DIR = os.path.join(SPEC_DIR, 'assets')
PROJECT_ROOT = os.path.dirname(SPEC_DIR)

a = Analysis(
    [os.path.join(SPEC_DIR, 'main.py')],
    pathex=[SPEC_DIR, PROJECT_ROOT],
    binaries=[
        (os.path.join(ASSETS_DIR, 'ffmpeg.exe'), '.'),
        (os.path.join(ASSETS_DIR, 'yt-dlp.exe'), '.'),
    ],
    datas=[
        (os.path.join(ASSETS_DIR, 'icons'), os.path.join('assets', 'icons')),
        (os.path.join(ASSETS_DIR, 'icons'), 'icons'),
    ],
    hiddenimports=[
        'clipgrab',
        'clipgrab.core',
        'clipgrab.core.logger',
        'clipgrab.core.validator',
        'clipgrab.core.formats',
        'clipgrab.core.ffmpeg_utils',
        'clipgrab.core.downloader',
        'clipgrab.core.queue_manager',
        'clipgrab.core.updater',
        'clipgrab.config',
        'clipgrab.config.settings',
        'clipgrab.data',
        'clipgrab.data.history_db',
        'clipgrab.gui',
        'clipgrab.gui.theme',
        'clipgrab.gui.main_window',
        'clipgrab.gui.widgets',
        'clipgrab.gui.widgets.url_input',
        'clipgrab.gui.widgets.format_selector',
        'clipgrab.gui.widgets.time_range_input',
        'clipgrab.gui.widgets.download_controls',
        'clipgrab.gui.widgets.queue_panel',
        'clipgrab.gui.widgets.queue_item_widget',
        'clipgrab.gui.widgets.history_panel',
        'clipgrab.gui.widgets.history_item_widget',
        'clipgrab.gui.widgets.settings_panel',
        'clipgrab.gui.widgets.toast_notification',
        'clipgrab.version',
        'requests',
        'sqlite3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas', 'unittest', 'test'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='YouCut',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ASSETS_DIR, 'icons', 'youcut.ico')
    if os.path.exists(os.path.join(ASSETS_DIR, 'icons', 'youcut.ico'))
    else (os.path.join(ASSETS_DIR, 'icons', 'youclip.ico') if os.path.exists(os.path.join(ASSETS_DIR, 'icons', 'youclip.ico')) else None),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='YouCut',
)
