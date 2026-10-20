# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['updater.py'],
    pathex=['.'],
    binaries=[],
    datas=[('app_icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'numpy', 'cv2', 'PIL', 'mss', 'keyboard', 'pytesseract',
        'psutil', 'onnxruntime', 'rapidocr_onnxruntime', 'torch',
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Updater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
