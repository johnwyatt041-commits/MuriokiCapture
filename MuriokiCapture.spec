# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
import os

block_cipher = None

datas = [
    ('app_icon.ico', '.'),
    ('app_icon.png', '.'),
]
binaries = []
hiddenimports = [
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'mss',
    'mss.tools',
    'cv2',
    'PIL',
    'PIL.Image',
    'psutil',
    'numpy',
    'keyboard',
]

tmp_ret_rapid = collect_all('rapidocr_onnxruntime')
datas += tmp_ret_rapid[0]
binaries += tmp_ret_rapid[1]
hiddenimports += tmp_ret_rapid[2]

tmp_ret_onnx = collect_all('onnxruntime')
datas += tmp_ret_onnx[0]
binaries += tmp_ret_onnx[1]
hiddenimports += tmp_ret_onnx[2]

tmp_ret_cv2 = collect_all('cv2')
datas += tmp_ret_cv2[0]
binaries += tmp_ret_cv2[1]
hiddenimports += tmp_ret_cv2[2]

tmp_ret_tess = collect_all('pytesseract')
datas += tmp_ret_tess[0]
binaries += tmp_ret_tess[1]
hiddenimports += tmp_ret_tess[2]

a = Analysis(
    ['capture.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'pandas',
        'openpyxl',
        'xlsxwriter',
        'tkinter',
        'customtkinter',
        'torch',
        'tensorflow',
        'scipy',
        'reportlab',
        'lark_oapi',
        'docx',
        'winrt',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=1,
)

# 确保打包使用系统最新的 MSVC 运行时库，替换 PyQt5 自带旧版 DLL
new_binaries = []
sys_dir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
for dest, src, type_ in a.binaries:
    base = os.path.basename(dest).lower()
    if base in ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140_1.dll', 'msvcp140_2.dll']:
        sys_target = os.path.join(sys_dir, base)
        if os.path.exists(sys_target):
            new_binaries.append((dest, sys_target, type_))
            continue
    new_binaries.append((dest, src, type_))
a.binaries = new_binaries

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MuriokiCapture',
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
    manifest='app.manifest',
)
