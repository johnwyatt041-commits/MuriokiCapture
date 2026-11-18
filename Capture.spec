# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os

block_cipher = None

datas = [
    ('app_icon.ico', '.'),
    ('app_icon.png', '.'),
    ('Updater.exe', '.'),
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
    'numpy',
    'keyboard',
    'pytesseract',
    'pin_drawer',
    'frosted_ui',
]

# RapidOCR 仅收集必要的推理包与模型配置，避免 collect_all('onnxruntime'/'cv2') 带入训练/量化/Haar 级联等冗余组件
datas += collect_data_files('rapidocr_onnxruntime')
hiddenimports += collect_submodules('rapidocr_onnxruntime')

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
        # 低风险精简：移除未使用的传递依赖与开发/测试模块
        'psutil',
        'requests',
        'urllib3',
        'OpenSSL',
        'cryptography',
        'bcrypt',
        'zstandard',
        'h2',
        'hpack',
        'certifi',
        'charset_normalizer',
        'idna',
        'onnxruntime.transformers',
        'onnxruntime.quantization',
        'onnxruntime.tools',
        'onnxruntime.datasets',
        'onnxruntime.training',
        'PIL.AvifImagePlugin',
        'PIL._avif',
        'setuptools',
        'pkg_resources',
        'distutils',
        'tqdm',
        'unittest',
        'doctest',
        'pdb',
        'pydoc',
        'pydoc_data',
        '_pyrepl',
        'PyQt5.QtNetwork',
        'PyQt5.QtQml',
        'PyQt5.QtQuick',
        'PyQt5.QtSvg',
        'PyQt5.QtDBus',
        'PyQt5.QtWebSockets',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=1,
)

# 过滤未使用的 Qt 插件/DLL、OpenCV Haar 级联数据、onnxruntime 非推理脚本与 PIL AVIF 插件
_UNUSED_QT_BINS = {
    'qt5quick.dll', 'qt5qml.dll', 'qt5qmlmodels.dll',
    'qt5network.dll', 'qt5websockets.dll', 'qt5dbus.dll', 'qt5svg.dll',
}
_UNUSED_QT_PLUGINS = {
    'pyqt5/qt5/plugins/platforms/qwebgl.dll',
    'pyqt5/qt5/plugins/platforms/qminimal.dll',
    'pyqt5/qt5/plugins/platforms/qoffscreen.dll',
    'pyqt5/qt5/plugins/platformthemes/qxdgdesktopportal.dll',
    'pyqt5/qt5/plugins/generic/qtuiotouchplugin.dll',
    'pyqt5/qt5/plugins/iconengines/qsvgicon.dll',
    'pyqt5/qt5/plugins/imageformats/qsvg.dll',
    'pyqt5/qt5/plugins/imageformats/qicns.dll',
    'pyqt5/qt5/plugins/imageformats/qtga.dll',
    'pyqt5/qt5/plugins/imageformats/qwbmp.dll',
}
_UNUSED_PREFIXES = (
    'onnxruntime/transformers/',
    'onnxruntime/quantization/',
    'onnxruntime/tools/',
    'onnxruntime/datasets/',
    'cv2/data/',
)

# 确保打包使用系统最新的 MSVC 运行时库，替换 PyQt5 自带旧版 DLL，并剔除冗余二进制
new_binaries = []
sys_dir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
for dest, src, type_ in a.binaries:
    norm = dest.replace('\\', '/').lower()
    base = os.path.basename(norm)
    if (
        norm.startswith('pil/_avif')
        or norm.startswith(_UNUSED_PREFIXES)
        or norm in _UNUSED_QT_PLUGINS
        or (norm.startswith('pyqt5/qt5/bin/') and base in _UNUSED_QT_BINS)
    ):
        continue
    if base in ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140_1.dll', 'msvcp140_2.dll']:
        sys_target = os.path.join(sys_dir, base)
        if os.path.exists(sys_target):
            new_binaries.append((dest, sys_target, type_))
            continue
    new_binaries.append((dest, src, type_))
a.binaries = new_binaries

a.datas = [
    item for item in a.datas
    if not (
        item[0].replace('\\', '/').lower().startswith('pyqt5/qt5/translations/')
        or item[0].replace('\\', '/').lower().startswith(_UNUSED_PREFIXES)
        or item[0].replace('\\', '/').lower() in {
            'cv2/__init__.pyi',
            'cv2/license-3rd-party.txt',
            'onnxruntime/thirdpartynotices.txt',
        }
    )
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Capture',
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
