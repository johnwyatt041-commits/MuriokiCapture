#! python3.12
import sys
import os
import time
import math
import ctypes
from ctypes import wintypes

# Windows 原生高分屏 PerMonitorV2 级感知注入（必须在任何 GUI 库加载前注入，彻底杜绝 DWM 位图拉伸模糊）
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        pass

os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=freetype")

# 优先加载 Windows 系统核心最新的 MSVC 运行时库，避免 PyQt5 自带旧版 MSVCP140.dll 产生符号与内存冲突
for _dll_name in ['vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll']:
    _sys_path = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', _dll_name)
    if os.path.exists(_sys_path):
        try:
            ctypes.CDLL(_sys_path)
        except OSError:
            pass
import re
import json
import hashlib
import threading
import urllib.request
import urllib.parse
import urllib.error
import base64
import io
import keyboard
import mss
import mss.tools
import cv2
import numpy as np
from PIL import Image
from datetime import datetime
import pytesseract
import shutil
import subprocess
import webbrowser
import tempfile
import logging # 引入日志模块
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor, as_completed

# RapidOCR 离线引擎懒加载单例（高精度中文/英文识别，首次 OCR 时再按需加载，彻底杜绝冷启动卡顿）
_RAPID_OCR_ENGINE = None
_RAPID_OCR_TRIED = False
_RAPID_OCR_LOCK = threading.Lock()
_RAPID_OCR_INFER_LOCK = threading.Lock()

def _find_custom_ocr_models():
    """检查程序目录或打包资源中的 models/ 是否放有升级版 ONNX 模型（如 PP-OCRv4/v5）。"""
    model_dirs = []
    for cand in (os.path.join(BASE_DIR, "models"), get_resource_path("models")):
        if cand and os.path.isdir(cand) and cand not in model_dirs:
            model_dirs.append(cand)
    det_names = ("ch_PP-OCRv5_det_infer.onnx", "ch_PP-OCRv4_det_infer.onnx", "det.onnx")
    rec_names = ("ch_PP-OCRv5_rec_infer.onnx", "ch_PP-OCRv4_rec_infer.onnx", "rec.onnx")
    cls_names = ("ch_ppocr_mobile_v2.0_cls_infer.onnx", "cls.onnx")

    def _pick(names):
        for d in model_dirs:
            for name in names:
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    return p
        return None

    return _pick(det_names), _pick(rec_names), _pick(cls_names)


def get_rapid_ocr_engine():
    global _RAPID_OCR_ENGINE, _RAPID_OCR_TRIED
    if not _RAPID_OCR_TRIED:
        with _RAPID_OCR_LOCK:
            if not _RAPID_OCR_TRIED:
                _RAPID_OCR_TRIED = True
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    det_path, rec_path, cls_path = _find_custom_ocr_models()
                    init_kwargs = {
                        "use_angle_cls": False,
                        "text_score": 0.32,
                        "min_height": 12,
                        "width_height_ratio": -1,
                        "det_model_path": det_path,
                        "det_limit_type": "max",
                        "det_limit_side_len": 1920,
                        "det_box_thresh": 0.35,
                        "det_unclip_ratio": 1.85,
                    }
                    if rec_path:
                        init_kwargs["rec_model_path"] = rec_path
                    if cls_path:
                        init_kwargs["cls_model_path"] = cls_path
                    try:
                        _RAPID_OCR_ENGINE = RapidOCR(**init_kwargs)
                    except Exception:
                        _RAPID_OCR_ENGINE = RapidOCR()
                    logging.info("RapidOCR 引擎按需懒加载成功 (det=%s, rec=%s)", det_path or "builtin", rec_path or "builtin")
                except Exception as _ocr_pre_err:
                    logging.warning("RapidOCR 引擎加载失败: %s", _ocr_pre_err)
    return _RAPID_OCR_ENGINE

from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QHBoxLayout, QVBoxLayout,
                             QFileDialog, QLabel, QMessageBox, QDialog, QScrollArea,
                             QColorDialog, QFontDialog, QTextEdit, QMenu, QComboBox, QSpinBox,
                             QSystemTrayIcon, QAction, QFrame, QSlider, QProgressBar, QToolTip,
                             QGraphicsDropShadowEffect, QGroupBox, QGridLayout, QLineEdit, QCheckBox,
                             QStackedWidget, QSizePolicy, QAbstractButton, QButtonGroup, QShortcut,
                             QAbstractSpinBox)
from PyQt5.QtCore import (Qt, QRect, QPoint, QSize, QThread, pyqtSignal, QSharedMemory, QTimer,
                          QAbstractNativeEventFilter, QObject, QUrl, QMimeData, QPointF, QRectF,
                          QPropertyAnimation, QEasingCurve, pyqtProperty, QEvent, QEventLoop)
from PyQt5.QtGui import (QPainter, QColor, QPen, QImage, QPixmap, QFont, QFontDatabase, QBrush, QWheelEvent,
                         QIcon, QCursor, QPainterPath, QPainterPathStroker, QFontMetrics, QFontMetricsF, QRegion,
                         QKeySequence, QLinearGradient, QTextDocument, QTextOption, QAbstractTextDocumentLayout,
                         QPalette, QTextCursor, QTransform)

# ==========================================
# ENVIRONMENT & OCR SETUP
# ==========================================
class DummyConsole:
    def write(self, *args, **kwargs): pass
    def flush(self, *args, **kwargs): pass

if sys.stdout is None: sys.stdout = DummyConsole()
if sys.stderr is None: sys.stderr = DummyConsole()

# 打包成 exe 后 __file__ 指向临时解压目录，需用 sys.executable 定位真实路径
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _is_dir_writable(path):
    """检测目录是否具备真实写权限（兼容 Windows Program Files ACL / UAC 虚拟化）。"""
    if not path or not os.path.isdir(path):
        return False
    try:
        with tempfile.TemporaryFile(dir=path):
            pass
        return True
    except OSError:
        return False

def _resolve_data_dir(base_dir):
    """优先使用便携目录（若可写），只读安装目录自动回落至 %LOCALAPPDATA%\\MuriokiCapture。"""
    env_dir = os.environ.get("MURIOKI_DATA_DIR", "").strip()
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir
    if _is_dir_writable(base_dir):
        data_dir = base_dir
    else:
        local_root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(local_root, "MuriokiCapture")
        os.makedirs(data_dir, exist_ok=True)
    os.environ["MURIOKI_DATA_DIR"] = data_dir
    return data_dir

DATA_DIR = _resolve_data_dir(BASE_DIR)

def get_resource_path(relative_path):
    """获取资源文件路径（兼容 PyInstaller 打包临时目录与源码运行目录）"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        bundle_path = os.path.join(sys._MEIPASS, relative_path)
        if os.path.exists(bundle_path):
            return bundle_path
    return os.path.join(BASE_DIR, relative_path)

TEMP_FOLDER = os.path.join(DATA_DIR, "temp_scroll_frames")
REC_TEMP_FOLDER = os.path.join(DATA_DIR, "temp_screen_recording")

# ==========================================
# AI 翻译与多模态识别配置 (智谱 GLM-OCR & 火山方舟豆包翻译)
# ==========================================
SETTINGS_FILE = os.path.join(DATA_DIR, "capture_settings.json")
from pin_drawer import (
    PinDrawerWindow,
    DrawerHoverPreview,
    start_pixmap_file_drag,
    run_toggleable_drag,
    copy_pixmap_as_clipboard_file,
    _extract_pixmaps_from_mimedata,
)
from frosted_ui import FrostedBarWidget, FrostedTrayMenu, create_frosted_pixmap

# ==========================================
# 应用版本与在线更新
# ==========================================
APP_VERSION = "1.5.6"
GITHUB_REPO = "johnwyatt041-commits/MuriokiCapture"
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_LATEST_JSON = f"https://github.com/{GITHUB_REPO}/releases/latest/download/latest.json"
UPDATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or BASE_DIR, "MuriokiCapture", "updates")

# 智谱 AI 专业文档/文字识别 (GLM-OCR) 配置
DEFAULT_ZHIPU_OCR_MODEL = "glm-ocr"
ZHIPU_OCR_API_URL = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"

# 火山引擎方舟 (Doubao 翻译) 配置
DEFAULT_VOLC_TRANS_MODEL = "ep-20260629102203-cfllm"
VOLC_ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

# 截图底部工具栏工具快捷键配置表：(设置键, 工具/动作ID, 类型['tool'|'action'], 名称, 说明)
TOOLBAR_TOOL_HOTKEY_SPECS = [
    ("hotkey_tool_rect_ellipse", "RECT_ELLIPSE", "tool", "矩形 / 椭圆", "截图编辑时切换矩形/椭圆标注工具"),
    ("hotkey_tool_arrow", "ARROW", "tool", "箭头", "截图编辑时切换箭头标注工具"),
    ("hotkey_tool_pen", "PEN", "tool", "画笔", "截图编辑时切换画笔/折线标注工具"),
    ("hotkey_tool_marker", "MARKER", "tool", "记号笔 / 荧光笔", "截图编辑时切换记号笔/荧光笔工具"),
    ("hotkey_tool_mosaic_blur", "MOSAIC_BLUR", "tool", "马赛克 / 高斯模糊", "截图编辑时切换马赛克/高斯模糊工具"),
    ("hotkey_tool_text", "TEXT", "tool", "文本", "截图编辑时切换文字标注工具"),
    ("hotkey_tool_eraser", "ERASER", "tool", "橡皮擦", "截图编辑时切换橡皮擦工具"),
    ("hotkey_tool_crop", "CROP", "action", "裁剪选区", "截图编辑时进入或退出裁剪模式"),
    ("hotkey_tool_rotate", "ROTATE", "tool", "旋转 / 翻转", "截图编辑时打开或关闭旋转与翻转选项"),
    ("hotkey_tool_translate", "TRANSLATE", "action", "一键翻译", "截图编辑时一键识别并翻译当前选区"),
]

AUTOSTART_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_APPROVED_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
AUTOSTART_VALUE_NAME = "MuriokiCapture"

_HOTKEY_KEY_NAME_MAP = {
    Qt.Key_Space: "Space",
    Qt.Key_Tab: "Tab",
    Qt.Key_Backtab: "Tab",
    Qt.Key_Return: "Enter",
    Qt.Key_Enter: "Enter",
    Qt.Key_Escape: "Esc",
    Qt.Key_Backspace: "Backspace",
    Qt.Key_Delete: "Delete",
    Qt.Key_Print: "PrintScreen",
    Qt.Key_Insert: "Insert",
    Qt.Key_Home: "Home",
    Qt.Key_End: "End",
    Qt.Key_PageUp: "PageUp",
    Qt.Key_PageDown: "PageDown",
    Qt.Key_Left: "Left",
    Qt.Key_Right: "Right",
    Qt.Key_Up: "Up",
    Qt.Key_Down: "Down",
    Qt.Key_Plus: "Plus",
    Qt.Key_Minus: "-",
    Qt.Key_Equal: "=",
    Qt.Key_BracketLeft: "[",
    Qt.Key_BracketRight: "]",
    Qt.Key_Backslash: "\\",
    Qt.Key_Semicolon: ";",
    Qt.Key_Apostrophe: "'",
    Qt.Key_Comma: ",",
    Qt.Key_Period: ".",
    Qt.Key_Slash: "/",
    Qt.Key_QuoteLeft: "`",
}

_HOTKEY_TOKEN_ALIASES = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
    "windows": "Win",
    "meta": "Win",
    "space": "Space",
    "tab": "Tab",
    "enter": "Enter",
    "return": "Enter",
    "esc": "Esc",
    "escape": "Esc",
    "backspace": "Backspace",
    "del": "Delete",
    "delete": "Delete",
    "printscreen": "PrintScreen",
    "prtscr": "PrintScreen",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "plus": "Plus",
}


def normalize_hotkey_str(hotkey_str):
    """将快捷键字符串规范化为统一格式（如 Ctrl+Shift+R），空串原样返回。"""
    s = str(hotkey_str or "").strip()
    if not s:
        return ""
    if s == "+":
        return "Plus"
    raw_parts = [p.strip() for p in s.split("+")]
    if s.endswith("++"):
        raw_parts = [p.strip() for p in s[:-1].split("+")]
        raw_parts[-1] = "Plus"
    mods = []
    key_token = ""
    for p in raw_parts:
        if not p:
            continue
        low = p.lower()
        canonical_mod = _HOTKEY_TOKEN_ALIASES.get(low)
        if canonical_mod in ("Ctrl", "Alt", "Shift", "Win"):
            if canonical_mod not in mods:
                mods.append(canonical_mod)
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            key_token = f"F{int(low[1:])}"
        elif canonical_mod is not None:
            key_token = canonical_mod
        elif len(p) == 1:
            key_token = p.upper()
        else:
            key_token = p
    if not key_token:
        return ""
    mod_order = [m for m in ("Ctrl", "Alt", "Shift", "Win") if m in mods]
    return "+".join(mod_order + [key_token])


def key_event_to_hotkey_str(event):
    """将 Qt QKeyEvent 转换为与 HotkeyRecorderWidget 一致的规范化快捷键字符串。"""
    key = event.key()
    modifiers = event.modifiers()
    if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, getattr(Qt, "Key_AltGr", -1)):
        return ""

    mod_names = []
    if modifiers & Qt.ControlModifier:
        mod_names.append("Ctrl")
    if modifiers & Qt.AltModifier:
        mod_names.append("Alt")
    if modifiers & Qt.ShiftModifier:
        mod_names.append("Shift")
    if modifiers & Qt.MetaModifier:
        mod_names.append("Win")

    key_name = ""
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        key_name = f"F{key - Qt.Key_F1 + 1}"
    elif Qt.Key_A <= key <= Qt.Key_Z:
        key_name = chr(key).upper()
    elif Qt.Key_0 <= key <= Qt.Key_9:
        key_name = chr(key)
    elif key in _HOTKEY_KEY_NAME_MAP:
        key_name = _HOTKEY_KEY_NAME_MAP[key]
    else:
        txt = (event.text() or "").strip()
        if len(txt) == 1 and txt.isprintable():
            key_name = "Plus" if txt == "+" else txt.upper()
        elif 0x21 <= key <= 0x7E:
            seq = QKeySequence(key).toString().strip()
            if len(seq) == 1 and seq.isprintable():
                key_name = "Plus" if seq == "+" else seq.upper()

    if not key_name:
        return ""
    parts = mod_names[:]
    if key_name not in parts:
        parts.append(key_name)
    return "+".join(parts)


def get_autostart_command():
    """生成写入 Windows 启动项的命令行（兼容打包 exe 与源码运行）"""
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        return f'"{exe_path}"'
    py_exe = os.path.abspath(sys.executable)
    pyw_exe = os.path.join(os.path.dirname(py_exe), "pythonw.exe")
    if os.path.isfile(pyw_exe) and os.path.basename(py_exe).lower() == "python.exe":
        py_exe = pyw_exe
    script_path = os.path.abspath(__file__)
    return f'"{py_exe}" "{script_path}"'


def is_autostart_enabled():
    """检查当前 Windows 用户登录自启动是否处于开启状态"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            if not str(val or "").strip():
                return False
    except FileNotFoundError:
        return False
    except Exception as e:
        logging.warning("读取开机自启动注册表失败: %s", e)
        return False

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_APPROVED_PATH, 0, winreg.KEY_READ) as key:
            data, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            if isinstance(data, (bytes, bytearray)) and len(data) >= 1 and (data[0] % 2 == 1):
                return False
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return True


def set_autostart_enabled(enabled):
    """开启或关闭当前 Windows 用户的开机自启动"""
    if sys.platform != "win32":
        return False, "当前系统不支持 Windows 注册表自启动"
    try:
        import winreg
        if enabled:
            cmd = get_autostart_command()
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, cmd)
            # 若此前在任务管理器被禁用，同步清除禁用态以保证开关立即生效
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_APPROVED_PATH, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                    data, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
                    if isinstance(data, (bytes, bytearray)) and len(data) >= 1 and (data[0] % 2 == 1):
                        winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_BINARY, b"\x02" + b"\x00" * 11)
            except FileNotFoundError:
                pass
            except Exception:
                pass
        else:
            for reg_path in (AUTOSTART_REG_PATH, AUTOSTART_APPROVED_PATH):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:
        logging.warning("设置开机自启动失败: %s", e)
        return False, str(e)


def sync_autostart_path_if_enabled():
    """若已开启自启动且原注册路径已失效（如移动了程序目录），自动刷新为当前路径"""
    if sys.platform != "win32" or not is_autostart_enabled():
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
        val_str = str(val or "").strip()
        target_cmd = get_autostart_command()
        if val_str == target_cmd:
            return
        # 解析首个引号或空格路径，仅在旧目标不存在时自动修复
        first_token = val_str.split('"')[1] if val_str.startswith('"') and '"' in val_str[1:] else val_str.split()[0]
        if first_token and not os.path.exists(first_token):
            set_autostart_enabled(True)
    except Exception:
        pass


_SECRET_SETTINGS_KEYS = ("zhipu_api_key", "volc_api_key")
_DPAPI_PREFIX = "dpapi:"


class _DPAPIDataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_transform(raw_bytes, protect=True):
    """使用 Windows DPAPI 对字节串做当前用户级加解密。"""
    if sys.platform != "win32" or not raw_bytes:
        return None
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if protect:
        fn.argtypes = [
            ctypes.POINTER(_DPAPIDataBlob), wintypes.LPCWSTR,
            ctypes.POINTER(_DPAPIDataBlob), ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DPAPIDataBlob),
        ]
    else:
        fn.argtypes = [
            ctypes.POINTER(_DPAPIDataBlob), ctypes.c_void_p,
            ctypes.POINTER(_DPAPIDataBlob), ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DPAPIDataBlob),
        ]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buf = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
    in_blob = _DPAPIDataBlob(len(raw_bytes), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DPAPIDataBlob()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    args = (ctypes.byref(in_blob), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not fn(*args):
        return None
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _protect_secret(value):
    s = str(value or "").strip()
    if not s or s.startswith(_DPAPI_PREFIX):
        return s
    try:
        encrypted = _dpapi_transform(s.encode("utf-8"), protect=True)
        if encrypted:
            return _DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception as e:
        logging.warning("DPAPI 加密配置密钥失败，回退明文保存: %s", e)
    return s


def _unprotect_secret(value):
    s = str(value or "").strip()
    if not s:
        return ""
    if not s.startswith(_DPAPI_PREFIX):
        return s
    try:
        raw = base64.b64decode(s[len(_DPAPI_PREFIX):].encode("ascii"))
        decrypted = _dpapi_transform(raw, protect=False)
        if decrypted is not None:
            return decrypted.decode("utf-8").strip()
    except Exception as e:
        logging.warning("DPAPI 解密配置密钥失败（可能更换了系统用户或机器）: %s", e)
    return ""


def load_settings():
    """从本地 JSON 文件加载用户设置（自动兼容迁移历史配置文件与加密密钥）"""
    defaults = {
        "use_ai_translation": False,
        "use_ai_ocr": False,
        "ocr_enable_sea_langs": False,
        "ocr_target_lang": "",
        "translation_display_mode": "dialog",
        "zhipu_api_key": "",
        "glm_ocr_model": DEFAULT_ZHIPU_OCR_MODEL,
        "volc_api_key": "",
        "volc_model": DEFAULT_VOLC_TRANS_MODEL,
        "hotkey_snip": "F1",
        "hotkey_ocr": "F2",
        "hotkey_pin": "F3",
        "hotkey_record": "F4",
        "hotkey_window_pin": "",
        "hotkey_scroll": "",
        "hotkey_history": "",
        "hotkey_settings": "",
        "hotkey_drawer": "Ctrl+1",
        "hotkey_toggle_pins": "Ctrl+Alt+H",
        "hotkey_paste_pin": "Ctrl+Alt+V",
        "hotkey_restore_click_through": "Ctrl+Alt+T",
        "hotkey_pin_border": "",
        "pin_border_enabled": False,
        "drawer_hover_enabled": True,
        "auto_start": False,
        "default_save_dir": os.path.join(os.path.expanduser("~"), "Pictures"),
        "auto_save_enabled": False,
        "save_format": "png",
        "save_quality": 95,
        "show_mode_hints": True,
        "auto_check_update": True,
        "last_update_check": 0,
        "skipped_version": "",
    }
    for _spec in TOOLBAR_TOOL_HOTKEY_SPECS:
        defaults[_spec[0]] = ""
    try:
        cfg_file = SETTINGS_FILE
        if not os.path.exists(cfg_file):
            # 只读安装目录下若预置了 capture_settings.json，则作为初始配置读取
            install_cfg = os.path.join(BASE_DIR, "capture_settings.json")
            if os.path.exists(install_cfg):
                cfg_file = install_cfg
            elif os.path.exists(DATA_DIR):
                legacy_cands = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.endswith("settings.json") and f != "capture_settings.json"]
                if legacy_cands and os.path.exists(legacy_cands[0]):
                    cfg_file = legacy_cands[0]
        if cfg_file and os.path.exists(cfg_file):
            with open(cfg_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            defaults.update(data)
    except Exception as e:
        logging.warning("加载设置文件失败: %s", e)
    for _sk in _SECRET_SETTINGS_KEYS:
        defaults[_sk] = _unprotect_secret(defaults.get(_sk, ""))
    return defaults

def save_settings(settings_dict):
    """将用户设置持久化到本地 JSON 文件（API Key 自动使用 Windows DPAPI 保护）"""
    try:
        existing = load_settings()
        existing.update(settings_dict)
        for _sk in _SECRET_SETTINGS_KEYS:
            existing[_sk] = _protect_secret(existing.get(_sk, ""))
        os.makedirs(os.path.dirname(os.path.abspath(SETTINGS_FILE)), exist_ok=True)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("保存设置文件失败: %s", e)

def get_zhipu_api_key():
    """获取智谱 AI (GLM-OCR) API 密钥"""
    key = load_settings().get("zhipu_api_key", "").strip()
    if key:
        return key
    return os.environ.get("ZHIPU_API_KEY", "").strip()

def get_volc_api_key():
    """获取火山方舟 (Ark) API 密钥"""
    key = load_settings().get("volc_api_key", "").strip()
    if key:
        return key
    return os.environ.get("ARK_API_KEY", os.environ.get("VOLC_API_KEY", "")).strip()
RECORD_QUALITY_PRESETS = {
    'p60': {'scale': 1.0, 'fps': 60, 'name': '原画 60FPS (100%)'},
    'p30': {'scale': 1.0, 'fps': 30, 'name': '原画 30FPS (100%)'},
    'high60': {'scale': 0.75, 'fps': 60, 'name': '高清 60FPS (75%)'},
    'high30': {'scale': 0.75, 'fps': 30, 'name': '高清 30FPS (75%)'},
    'standard': {'scale': 0.50, 'fps': 30, 'name': '标清 30FPS (50%)'},
    'original': {'scale': 1.0, 'fps': 60, 'name': '原画 60FPS (100%)'},
    'high': {'scale': 0.75, 'fps': 60, 'name': '高清 60FPS (75%)'}
}
SCROLL_CAPTURE_INTERVAL = 0.08
SCROLL_MIN_CAPTURE_SIZE = 12
SCROLL_DUPLICATE_DIFF_THRESHOLD = 1.4
SCROLL_MIN_UNIQUE_PIXELS = 3
SCROLL_WHEEL_DELTA = -120
SCROLL_SETTLE_DELAY = 0.22
CROP_HANDLE_SIZE = 12
CROP_MIN_SIZE = 20
CROP_RATIO_PRESETS = [("Free", None), ("1:1", (1, 1)), ("4:3", (4, 3)), ("3:2", (3, 2)), ("16:9", (16, 9)), ("9:16", (9, 16))]
SCROLL_MAX_FRAMES = 500
SCROLL_FIXED_STEP_TOLERANCE = 0.28
COLOR_MAGNIFIER_SIZE = 180
COLOR_MAGNIFIER_SOURCE = 21
COLOR_PICKER_WIDTH = 180
COLOR_PICKER_INFO_HEIGHT = 86
MOUSEEVENTF_WHEEL = 0x0800

TESSERACT_CMD = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
if not os.path.exists(TESSERACT_CMD):
    _alt_cmd = r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'
    if os.path.exists(_alt_cmd):
        TESSERACT_CMD = _alt_cmd
    else:
        _which = shutil.which('tesseract')
        if _which:
            TESSERACT_CMD = _which

if os.path.exists(TESSERACT_CMD):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    _tessdata_dir = os.path.join(os.path.dirname(TESSERACT_CMD), 'tessdata')
    if os.path.exists(_tessdata_dir) and 'TESSDATA_PREFIX' not in os.environ:
        os.environ['TESSDATA_PREFIX'] = _tessdata_dir

# Windows 下子进程彻底静默化补丁，杜绝外部进程调用时弹出任何 cmd/conhost 控制台黑框
if sys.platform == 'win32':
    _orig_subprocess_args = pytesseract.pytesseract.subprocess_args
    def _silent_subprocess_args(*args, **kwargs):
        ret = _orig_subprocess_args(*args, **kwargs)
        # CREATE_NO_WINDOW (0x08000000) 确保绝对不为子进程创建任何控制台窗口
        ret['creationflags'] = 0x08000000
        si = ret.get('startupinfo')
        if si is None:
            si = subprocess.STARTUPINFO()
            ret['startupinfo'] = si
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return ret
    pytesseract.pytesseract.subprocess_args = _silent_subprocess_args

    # 直接通过扫描本地 tessdata 目录获取已安装语言包，零耗时且完全不触发外部进程调用与黑框闪烁
    def _silent_get_languages(config=''):
        tessdata = os.environ.get('TESSDATA_PREFIX')
        if not tessdata and os.path.exists(TESSERACT_CMD):
            tessdata = os.path.join(os.path.dirname(TESSERACT_CMD), 'tessdata')
        if tessdata and os.path.exists(tessdata):
            try:
                return [f[:-12] for f in os.listdir(tessdata) if f.endswith('.traineddata')]
            except Exception:
                pass
        return ['chi_sim', 'eng', 'tha', 'fil']

    pytesseract.pytesseract.get_languages = _silent_get_languages
    pytesseract.get_languages = _silent_get_languages

def get_tesseract_languages():
    """动态获取可用的 Tesseract 语言包，优先支持 chi_sim, eng, tha, fil"""
    try:
        langs = pytesseract.get_languages()
        preferred = ['chi_sim', 'eng', 'tha', 'fil']
        supported = [l for l in preferred if l in langs]
        if supported:
            return "+".join(supported)
    except Exception:
        pass
    return "chi_sim+eng+tha+fil"

TESSERACT_LANGS = get_tesseract_languages()


def _pick_auto_ocr_langs():
    """取字/截图翻译默认只用中英，避免泰语/菲律宾语包把中文误识别成泰文。"""
    available = set(TESSERACT_LANGS.split('+'))
    preferred = [l for l in ('chi_sim', 'eng') if l in available]
    return '+'.join(preferred) if preferred else TESSERACT_LANGS


AUTO_OCR_LANGS = _pick_auto_ocr_langs()

# 初始化日志配置（采用 RotatingFileHandler 限制最大 1MB，日常仅记录 INFO/ERROR，彻底避免无限膨胀）
LOG_FILE = os.path.join(DATA_DIR, "capture_app.log")
_log_handler = RotatingFileHandler(LOG_FILE, maxBytes=1*1024*1024, backupCount=1, encoding='utf-8')
_log_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.addHandler(_log_handler)
logging.info("=== 截图工具启动 ===")

def global_exception_handler(exctype, value, tb):
    logging.critical("全局未捕获异常: ", exc_info=(exctype, value, tb))
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_handler
if not os.path.exists(TESSERACT_CMD):
    logging.warning("默认 Tesseract 路径不存在，将尝试使用系统 PATH 中的 tesseract。")
else:
    logging.info(f"Tesseract OCR 引擎就绪: {TESSERACT_CMD}, 语言包: {get_tesseract_languages()}")

# ==========================================
# WINDOW & CONTROL DETECTOR (Windows 窗口/控件智能吸附)
# ==========================================
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_dwmapi = ctypes.windll.dwmapi

class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
_user32.EnumChildWindows.restype = wintypes.BOOL

_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL

_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = wintypes.LONG
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL

HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
WS_EX_TOPMOST = 0x00000008
GW_HWNDPREV = 3

_kernel32 = ctypes.windll.kernel32
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
_user32.AllowSetForegroundWindow.restype = wintypes.BOOL

class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]

_user32.MonitorFromRect.argtypes = [ctypes.POINTER(_RECT), wintypes.DWORD]
_user32.MonitorFromRect.restype = wintypes.HANDLE
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFOEXW)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL

_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.SetEvent.argtypes = [wintypes.HANDLE]
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

SINGLE_INSTANCE_SNIP_EVENT = "Local\\MuriokiCapture_TriggerSnip_Event"


def _phys_rect_to_logical_qrect(phys_left, phys_top, phys_w, phys_h):
    """将 Windows 物理像素矩形转换为 Qt 逻辑像素 QRect（兼容 100%~200% 多屏 DPI 缩放）。"""
    try:
        rc = _RECT(int(phys_left), int(phys_top), int(phys_left + phys_w), int(phys_top + phys_h))
        hmon = _user32.MonitorFromRect(ctypes.byref(rc), 2)  # MONITOR_DEFAULTTONEAREST
        if hmon:
            mi = _MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                target_screen = None
                for s in QApplication.screens():
                    if s.name() == mi.szDevice:
                        target_screen = s
                        break
                if target_screen is None:
                    target_screen = QApplication.primaryScreen()
                if target_screen is not None:
                    dpr = float(target_screen.devicePixelRatio() or 1.0)
                    sg = target_screen.geometry()
                    if dpr == 1.0 and sg.x() == mi.rcMonitor.left and sg.y() == mi.rcMonitor.top:
                        return QRect(int(phys_left), int(phys_top), int(phys_w), int(phys_h))
                    lx = sg.x() + int(round((phys_left - mi.rcMonitor.left) / dpr))
                    ly = sg.y() + int(round((phys_top - mi.rcMonitor.top) / dpr))
                    lw = max(1, int(round(phys_w / dpr)))
                    lh = max(1, int(round(phys_h / dpr)))
                    return QRect(lx, ly, lw, lh)
    except Exception:
        pass
    return QRect(int(phys_left), int(phys_top), int(phys_w), int(phys_h))


def _logical_point_to_phys(lx, ly):
    """将 Qt 逻辑全局坐标转换为 Windows 物理坐标。"""
    try:
        pt = QPoint(int(lx), int(ly))
        screen = QApplication.screenAt(pt) or QApplication.primaryScreen()
        if screen is not None:
            dpr = float(screen.devicePixelRatio() or 1.0)
            if dpr == 1.0:
                return int(lx), int(ly)
            sg = screen.geometry()
            # 枚举当前屏幕对应的物理监视器范围
            _MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(_RECT), wintypes.LPARAM)
            matched_rc = []

            def _mon_cb(hmon, _hdc, _lprect, _lp):
                mi = _MONITORINFOEXW()
                mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
                if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)) and mi.szDevice == screen.name():
                    matched_rc.append(mi.rcMonitor)
                    return False
                return True

            _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_mon_cb), 0)
            if matched_rc:
                mr = matched_rc[0]
                return (
                    mr.left + int(round((lx - sg.x()) * dpr)),
                    mr.top + int(round((ly - sg.y()) * dpr)),
                )
            return int(round(lx * dpr)), int(round(ly * dpr))
    except Exception:
        pass
    return int(lx), int(ly)


_NONMODAL_DIALOGS = []


def _register_nonmodal_dialog(dlg):
    if dlg is not None and dlg not in _NONMODAL_DIALOGS:
        _NONMODAL_DIALOGS.append(dlg)


def _unregister_nonmodal_dialog(dlg):
    if dlg in _NONMODAL_DIALOGS:
        _NONMODAL_DIALOGS.remove(dlg)


def _get_visible_nonmodal_dialogs():
    alive = []
    still_valid = []
    for dlg in list(_NONMODAL_DIALOGS):
        try:
            _ = dlg.isVisible()
            still_valid.append(dlg)
            if dlg.isVisible():
                alive.append(dlg)
        except RuntimeError:
            pass
    _NONMODAL_DIALOGS[:] = still_valid
    return alive


def exec_dialog_nonmodal(dlg):
    """以非模态局部事件循环运行 QDialog，避免 Qt activeModalWidget 阻塞全局热键唤起的截图选区。"""
    if dlg is None:
        return QDialog.Rejected
    dlg.setModal(False)
    dlg.setWindowModality(Qt.NonModal)
    _register_nonmodal_dialog(dlg)
    loop = QEventLoop(dlg)
    res_box = [QDialog.Rejected]

    def _on_finished(r=QDialog.Rejected):
        res_box[0] = r
        if loop.isRunning():
            loop.quit()

    def _quit_loop(*_args):
        if loop.isRunning():
            loop.quit()

    class _CloseWatcher(QObject):
        def eventFilter(self, obj, ev):
            if obj is dlg and ev.type() == QEvent.Close and ev.isAccepted():
                QTimer.singleShot(0, _quit_loop)
            return False

    watcher = _CloseWatcher(dlg)
    dlg.installEventFilter(watcher)
    dlg.finished.connect(_on_finished)
    dlg.destroyed.connect(_quit_loop)
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(_quit_loop)

    dlg.show()
    _force_activate_window(dlg)
    if dlg.isVisible():
        loop.exec_()
    try:
        dlg.removeEventFilter(watcher)
    except RuntimeError:
        pass
    _unregister_nonmodal_dialog(dlg)
    return res_box[0]


def _force_activate_window(widget):
    """在外部拖拽或子窗口关闭后可靠恢复窗口前台焦点，避免热键/Esc 失效。"""
    if widget is None:
        return
    try:
        hwnd = int(widget.winId())
        if sys.platform == "win32" and hwnd:
            fg = _user32.GetForegroundWindow()
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = False
            if fg_tid and fg_tid != cur_tid:
                attached = bool(_user32.AttachThreadInput(fg_tid, cur_tid, True))
            try:
                _user32.BringWindowToTop(hwnd)
                _user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    _user32.AttachThreadInput(fg_tid, cur_tid, False)
    except Exception:
        pass
    try:
        widget.raise_()
        widget.activateWindow()
        widget.setFocus(Qt.ActiveWindowFocusReason)
    except Exception:
        pass

try:
    _user32.OpenWindowStationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
    _user32.OpenWindowStationW.restype = wintypes.HANDLE
    _user32.SetProcessWindowStation.argtypes = [wintypes.HANDLE]
    _user32.SetProcessWindowStation.restype = wintypes.BOOL
    _user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _user32.OpenDesktopW.restype = wintypes.HANDLE
    _user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    _user32.SetThreadDesktop.restype = wintypes.BOOL
except Exception:
    pass

def ensure_interactive_desktop():
    try:
        hw = _user32.OpenWindowStationW("WinSta0", False, 0x037F)
        if hw:
            _user32.SetProcessWindowStation(hw)
            hd = _user32.OpenDesktopW("default", 0, False, 0x01FF)
            if hd:
                _user32.SetThreadDesktop(hd)
    except Exception:
        pass

ensure_interactive_desktop()


class WindowSnapper:
    """
    Windows 窗口与 UI 控件智能吸附检测引擎：
    基于 Windows DWM 扩展框架边界与顶级窗口 Z 轴层级，毫秒级预缓存桌面所有可见窗口与子控件，
    在鼠标悬停时提供微秒级（<0.01ms）无锁几何拾取与磁性高亮吸附。
    """
    def __init__(self, my_pid=None, exclude_hwnds=None):
        self.my_pid = my_pid or os.getpid()
        self.exclude_hwnds = set(exclude_hwnds or [])
        self.cached_windows = []
        try:
            self.refresh_window_tree()
        except Exception as e:
            logging.warning("WindowSnapper 初始化窗口树失败: %s", e)

    def refresh_window_tree(self):
        ensure_interactive_desktop()
        windows = []
        my_pid = self.my_pid
        desktop_hwnd = _user32.GetDesktopWindow()

        def enum_win_cb(hwnd, lparam):
            if hwnd == desktop_hwnd or hwnd in self.exclude_hwnds:
                return True
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True

            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == my_pid:
                return True

            r = _RECT()
            hr = _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
            if hr != 0:
                _user32.GetWindowRect(hwnd, ctypes.byref(r))

            w = r.right - r.left
            h = r.bottom - r.top
            if w <= 20 or h <= 20:
                return True

            length = _user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.strip()

            win_rect = QRect(r.left, r.top, w, h)

            children = []
            child_count = 0

            def child_cb(ch, lp):
                nonlocal child_count
                if child_count >= 80:
                    return False
                if _user32.IsWindowVisible(ch):
                    cr = _RECT()
                    _user32.GetWindowRect(ch, ctypes.byref(cr))
                    cw = cr.right - cr.left
                    ch_h = cr.bottom - cr.top
                    if 20 <= cw <= w and 16 <= ch_h <= h and (cw < w or ch_h < h):
                        ch_rect = QRect(cr.left, cr.top, cw, ch_h)
                        if win_rect.intersects(ch_rect):
                            ch_title = ""
                            ch_len = _user32.GetWindowTextLengthW(ch)
                            if ch_len > 0:
                                b = ctypes.create_unicode_buffer(ch_len + 1)
                                _user32.GetWindowTextW(ch, b, ch_len + 1)
                                ch_title = b.value.strip()
                            children.append({'hwnd': ch, 'rect': ch_rect, 'title': ch_title})
                            child_count += 1
                return True

            try:
                _user32.EnumChildWindows(hwnd, _WNDENUMPROC(child_cb), 0)
            except Exception:
                pass

            windows.append({
                'hwnd': hwnd,
                'rect': win_rect,
                'title': title,
                'children': children
            })
            return True

        try:
            _user32.EnumWindows(_WNDENUMPROC(enum_win_cb), 0)
            self.cached_windows = windows
        except Exception as e:
            logging.warning("EnumWindows 刷新失败: %s", e)

    def detect_at(self, screen_x, screen_y):
        try:
            for w in self.cached_windows:
                if w['rect'].contains(screen_x, screen_y):
                    best_child = None
                    best_area = w['rect'].width() * w['rect'].height()
                    for c in w['children']:
                        if c['rect'].contains(screen_x, screen_y):
                            area = c['rect'].width() * c['rect'].height()
                            if area < best_area:
                                best_area = area
                                best_child = c
                    if best_child:
                        child_title = best_child['title'] or w['title']
                        return best_child['rect'], child_title
                    return w['rect'], w['title']
        except Exception as e:
            logging.debug("WindowSnapper.detect_at 异常: %s", e)
        return None, ""

    def detect_top_level_at(self, screen_x, screen_y):
        ensure_interactive_desktop()
        found = None
        my_pid = self.my_pid

        def enum_cb(h, _):
            nonlocal found
            if h in self.exclude_hwnds:
                return True
            if not _user32.IsWindowVisible(h) or _user32.IsIconic(h):
                return True
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == my_pid:
                return True

            buf = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(h, buf, 256)
            cls = buf.value
            if cls in ['Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd']:
                return True

            r = _RECT()
            hr = _dwmapi.DwmGetWindowAttribute(h, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
            if hr != 0:
                _user32.GetWindowRect(h, ctypes.byref(r))

            w = r.right - r.left
            height = r.bottom - r.top
            if w >= 40 and height >= 40 and r.left <= screen_x < r.right and r.top <= screen_y < r.bottom:
                _user32.GetWindowTextW(h, buf, 256)
                title = buf.value.strip()
                ex = _user32.GetWindowLongW(h, -20)
                if not title and (ex & 0x00000080):  # WS_EX_TOOLWINDOW
                    return True
                found = (h, QRect(r.left, r.top, w, height), title)
                return False
            return True

        try:
            _user32.EnumWindows(_WNDENUMPROC(enum_cb), 0)
        except Exception as e:
            logging.debug("WindowSnapper.detect_top_level_at EnumWindows 异常: %s", e)

        if found:
            return found
        return None, None, ""


_global_window_snapper = None

def get_window_snapper():
    global _global_window_snapper
    if _global_window_snapper is None:
        _global_window_snapper = WindowSnapper()
    return _global_window_snapper

def detect_window_or_control_rect(screen_x, screen_y, ignored_hwnd=None):
    try:
        snapper = get_window_snapper()
        if ignored_hwnd:
            snapper.exclude_hwnds.add(ignored_hwnd)
        return snapper.detect_at(int(screen_x), int(screen_y))
    except Exception:
        return None, ""

# ==========================================
# CUSTOM CURSORS
# ==========================================
def create_eraser_cursor():
    pix = QPixmap(24, 24)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(40, 44, 52), 2))
    p.setBrush(QBrush(QColor(255, 255, 255, 230)))
    p.drawEllipse(3, 3, 18, 18)
    p.setPen(QPen(QColor(239, 68, 68), 2))
    p.drawEllipse(6, 6, 12, 12)
    p.drawLine(12, 2, 12, 22)
    p.drawLine(2, 12, 22, 12)
    p.end()
    return QCursor(pix, 12, 12)

def create_brush_cursor(radius=8):
    size = max(18, int(radius * 2 + 4))
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0, 180), 2))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.setPen(QPen(QColor(255, 255, 255, 240), 1))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QCursor(pix, size // 2, size // 2)

def create_deskpin_cursor():
    """绘制高精 DeskPin 大头针指针 (红白醒目配色的图钉，针尖定位于 (3, 3))"""
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)

    # 针尖钢针：自 (3, 3) 延伸至 (12, 12)
    pen_needle_shadow = QPen(QColor(40, 40, 40, 200), 2.8, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen_needle_shadow)
    p.drawLine(QPointF(3.0, 3.0), QPointF(12.0, 12.0))
    pen_needle = QPen(QColor(220, 220, 225), 1.6, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen_needle)
    p.drawLine(QPointF(3.5, 3.5), QPointF(11.5, 11.5))

    # 旋转 45 度绘制大头针塑料主体
    p.save()
    p.translate(13.0, 13.0)
    p.rotate(45)

    # 针座基环 (圆环底盘)
    p.setPen(QPen(QColor(153, 27, 27), 1.2))
    p.setBrush(QBrush(QColor(220, 38, 38)))
    p.drawRoundedRect(QRectF(-6.0, -1.0, 12.0, 3.5), 1.5, 1.5)

    # 针柱主体
    p.setBrush(QBrush(QColor(239, 68, 68)))
    p.drawRoundedRect(QRectF(-3.5, 2.5, 7.0, 8.0), 1.5, 1.5)

    # 针顶圆顶大帽
    p.setBrush(QBrush(QColor(248, 113, 113)))
    p.drawEllipse(QRectF(-5.5, 9.5, 11.0, 6.0))

    p.restore()
    p.end()
    return QCursor(pix, 3, 3)


CODEX_FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif'
CODEX_MONO_FAMILY = '"Cascadia Mono", "Cascadia Code", "Consolas", "Microsoft YaHei UI", monospace'

_UI_PRIMARY_FAMILY = "Segoe UI Variable Text"
_UI_MONO_FAMILY = "Cascadia Mono"
_UI_CJK_FAMILY = "Microsoft YaHei UI"
_FONTS_CONFIGURED = False


def _configure_ui_font_system():
    """探测系统可用现代字体族并注册一次全局回退链，确保 FreeType 下西文、中文与 Emoji 无缝混排。"""
    global _UI_PRIMARY_FAMILY, _UI_MONO_FAMILY, _UI_CJK_FAMILY, _FONTS_CONFIGURED
    if _FONTS_CONFIGURED or not QApplication.instance():
        return
    families = set(QFontDatabase().families())
    _UI_PRIMARY_FAMILY = (
        "Segoe UI Variable Text" if "Segoe UI Variable Text" in families
        else ("Segoe UI" if "Segoe UI" in families else "Microsoft YaHei UI")
    )
    if "Cascadia Mono" in families:
        _UI_MONO_FAMILY = "Cascadia Mono"
    elif "Cascadia Code" in families:
        _UI_MONO_FAMILY = "Cascadia Code"
    else:
        _UI_MONO_FAMILY = "Consolas"
    _UI_CJK_FAMILY = "Microsoft YaHei UI" if "Microsoft YaHei UI" in families else "Microsoft YaHei"

    def _uniq(seq, exclude=None):
        seen = {exclude} if exclude else set()
        out = []
        for item in seq:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    cjk_chain = [_UI_CJK_FAMILY, "Microsoft YaHei", "Segoe UI Emoji"]
    QFont.insertSubstitutions(
        "Segoe UI Variable Text",
        _uniq(["Segoe UI"] + cjk_chain, exclude="Segoe UI Variable Text"),
    )
    QFont.insertSubstitutions("Segoe UI", _uniq(cjk_chain, exclude="Segoe UI"))
    QFont.insertSubstitutions("Microsoft YaHei UI", _uniq(["Segoe UI Emoji"], exclude="Microsoft YaHei UI"))
    QFont.insertSubstitutions("Microsoft YaHei", _uniq(["Segoe UI Emoji"], exclude="Microsoft YaHei"))
    QFont.insertSubstitutions(
        _UI_MONO_FAMILY,
        _uniq(["Cascadia Mono", "Cascadia Code", "Consolas"] + cjk_chain, exclude=_UI_MONO_FAMILY),
    )
    QFont.insertSubstitutions("Consolas", _uniq([_UI_MONO_FAMILY] + cjk_chain, exclude="Consolas"))
    _FONTS_CONFIGURED = True


def make_ui_font(pt_size=9, weight=QFont.Normal, mono=False, pixel_size=None):
    """构造统一的现代无衬线/等宽 QFont，关闭 FreeType 强制网格对齐并启用高质量灰度抗锯齿。"""
    _configure_ui_font_system()
    family = _UI_MONO_FAMILY if mono else _UI_PRIMARY_FAMILY
    font = QFont(family, -1 if pixel_size is not None else pt_size, weight)
    if pixel_size is not None:
        font.setPixelSize(max(1, int(pixel_size)))
    font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
    font.setHintingPreference(QFont.PreferNoHinting)
    return font


CODEX_DIALOG_BASE_QSS = f"""
    QDialog {{
        background-color: #FCFCFD;
        color: #0D0D0D;
        font-family: {CODEX_FONT_FAMILY};
    }}
    QWidget#codexTopBar {{
        background-color: #F7F7F8;
        border-bottom: 1px solid #ECECEF;
    }}
    QWidget#codexBottomBar {{
        background-color: #F7F7F8;
        border-top: 1px solid #ECECEF;
    }}
    QFrame#codexCard {{
        background-color: #FFFFFF;
        border: 1px solid #E4E4E7;
        border-radius: 10px;
    }}
    QLabel#codexTitle {{
        font-size: 14.5px;
        font-weight: 600;
        color: #0D0D0D;
        background: transparent;
    }}
    QLabel#codexSubtitle {{
        font-size: 12px;
        color: #71717A;
        background: transparent;
    }}
    QLabel#codexSectionLabel {{
        font-size: 11.5px;
        font-weight: 600;
        color: #71717A;
        background: transparent;
    }}
    QLabel#codexPill {{
        background-color: #FFFFFF;
        color: #52525B;
        border: 1px solid #E4E4E7;
        border-radius: 6px;
        padding: 3px 9px;
        font-size: 11.5px;
        font-weight: 400;
    }}
    QComboBox {{
        background-color: #FFFFFF;
        color: #18181B;
        border: 1px solid #E4E4E7;
        border-radius: 7px;
        padding: 5px 26px 5px 10px;
        font-size: 12.5px;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border-color: #D4D4D8;
        background-color: #FAFAFA;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border: none;
        background: transparent;
    }}
    QComboBox::down-arrow {{
        width: 0px;
        height: 0px;
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background-color: #FFFFFF;
        color: #0D0D0D;
        border: 1px solid #E4E4E7;
        border-radius: 8px;
        padding: 4px;
        selection-background-color: #F4F4F5;
        selection-color: #0D0D0D;
        outline: none;
    }}
    QLineEdit {{
        background-color: #FFFFFF;
        color: #0D0D0D;
        border: 1px solid #E4E4E7;
        border-radius: 7px;
        padding: 6px 10px;
        font-size: 12.5px;
        selection-background-color: #0D0D0D;
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus {{
        border-color: #0D0D0D;
    }}
    QPushButton#codexPrimaryBtn {{
        background-color: #0D0D0D;
        color: #FFFFFF;
        border: 1px solid #0D0D0D;
        border-radius: 7px;
        padding: 6px 14px;
        font-size: 12.5px;
        font-weight: 600;
    }}
    QPushButton#codexPrimaryBtn:hover {{
        background-color: #27272A;
        border-color: #27272A;
    }}
    QPushButton#codexPrimaryBtn:pressed {{
        background-color: #3F3F46;
    }}
    QPushButton#codexPrimaryBtn:disabled {{
        background-color: #E4E4E7;
        color: #A1A1AA;
        border-color: #E4E4E7;
    }}
    QPushButton#codexSecondaryBtn {{
        background-color: #FFFFFF;
        color: #18181B;
        border: 1px solid #E4E4E7;
        border-radius: 7px;
        padding: 6px 12px;
        font-size: 12.5px;
        font-weight: 400;
    }}
    QPushButton#codexSecondaryBtn:hover {{
        background-color: #F4F4F5;
        border-color: #D4D4D8;
        color: #0D0D0D;
    }}
    QPushButton#codexSecondaryBtn:disabled {{
        background-color: #FAFAFA;
        color: #A1A1AA;
        border-color: #ECECEF;
    }}
    QPushButton#codexGhostBtn {{
        background-color: transparent;
        color: #52525B;
        border: 1px solid transparent;
        border-radius: 7px;
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 400;
    }}
    QPushButton#codexGhostBtn:hover {{
        background-color: #F4F4F5;
        color: #0D0D0D;
    }}
    QPushButton#codexDangerBtn {{
        background-color: #FFFFFF;
        color: #DC2626;
        border: 1px solid #FECACA;
        border-radius: 7px;
        padding: 6px 12px;
        font-size: 12.5px;
        font-weight: 400;
    }}
    QPushButton#codexDangerBtn:hover {{
        background-color: #FEF2F2;
        border-color: #FCA5A5;
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 2px 1px;
    }}
    QScrollBar::handle:vertical {{
        background: #D4D4D8;
        border-radius: 3px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #A1A1AA;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 6px;
        margin: 1px 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: #D4D4D8;
        border-radius: 3px;
        min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: #A1A1AA;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
        width: 0px;
    }}
"""


def _create_codex_icon(name, color="#52525B", size=16):
    """绘制 Codex 风格高分屏极简 1.5px 线性矢量图标"""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(float(dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    if size != 16:
        p.scale(size / 16.0, size / 16.0)
    pen = QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    if name == "brand":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0D0D0D"))
        p.drawRoundedRect(QRectF(0.5, 0.5, 15.0, 15.0), 4.0, 4.0)
        p.setPen(QPen(QColor("#FFFFFF"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        # 极简取景框四角
        p.drawPolyline(QPointF(3.8, 6.2), QPointF(3.8, 3.8), QPointF(6.2, 3.8))
        p.drawPolyline(QPointF(9.8, 3.8), QPointF(12.2, 3.8), QPointF(12.2, 6.2))
        p.drawPolyline(QPointF(12.2, 9.8), QPointF(12.2, 12.2), QPointF(9.8, 12.2))
        p.drawPolyline(QPointF(6.2, 12.2), QPointF(3.8, 12.2), QPointF(3.8, 9.8))
        p.drawEllipse(QPointF(8.0, 8.0), 1.7, 1.7)
    elif name == "shortcuts":
        # Command ⌘ 极简对称符号
        p.drawRect(QRectF(5.8, 5.8, 4.4, 4.4))
        p.drawRoundedRect(QRectF(2.2, 2.2, 3.6, 3.6), 1.8, 1.8)
        p.drawRoundedRect(QRectF(10.2, 2.2, 3.6, 3.6), 1.8, 1.8)
        p.drawRoundedRect(QRectF(2.2, 10.2, 3.6, 3.6), 1.8, 1.8)
        p.drawRoundedRect(QRectF(10.2, 10.2, 3.6, 3.6), 1.8, 1.8)
    elif name == "general":
        # 极简调节滑块三横线
        p.drawLine(QPointF(2.5, 4.0), QPointF(13.5, 4.0))
        p.drawLine(QPointF(2.5, 8.0), QPointF(13.5, 8.0))
        p.drawLine(QPointF(2.5, 12.0), QPointF(13.5, 12.0))
        p.setBrush(QColor("#F7F7F8"))
        p.drawEllipse(QPointF(5.5, 4.0), 1.6, 1.6)
        p.drawEllipse(QPointF(10.5, 8.0), 1.6, 1.6)
        p.drawEllipse(QPointF(6.5, 12.0), 1.6, 1.6)
    elif name == "ai":
        # OpenAI / AI 星芒轮廓
        star = QPainterPath()
        star.moveTo(7.5, 1.8)
        star.quadTo(8.0, 7.2, 13.4, 7.7)
        star.quadTo(8.0, 8.2, 7.5, 13.6)
        star.quadTo(7.0, 8.2, 1.6, 7.7)
        star.quadTo(7.0, 7.2, 7.5, 1.8)
        p.drawPath(star)
        p.drawLine(QPointF(12.8, 2.2), QPointF(12.8, 4.6))
        p.drawLine(QPointF(11.6, 3.4), QPointF(14.0, 3.4))
    elif name == "update":
        # 循环刷新箭头
        arc_path = QPainterPath()
        arc_path.arcMoveTo(QRectF(2.5, 2.5, 11.0, 11.0), 35)
        arc_path.arcTo(QRectF(2.5, 2.5, 11.0, 11.0), 35, 285)
        p.drawPath(arc_path)
        p.drawPolyline(QPointF(10.5, 2.6), QPointF(12.8, 4.3), QPointF(10.4, 5.8))
    elif name == "eye":
        eye = QPainterPath()
        eye.moveTo(1.8, 8.0)
        eye.quadTo(8.0, 2.5, 14.2, 8.0)
        eye.quadTo(8.0, 13.5, 1.8, 8.0)
        p.drawPath(eye)
        p.drawEllipse(QPointF(8.0, 8.0), 2.1, 2.1)
    elif name == "eye_off":
        eye = QPainterPath()
        eye.moveTo(1.8, 8.0)
        eye.quadTo(8.0, 2.8, 14.2, 8.0)
        eye.quadTo(8.0, 13.2, 1.8, 8.0)
        p.drawPath(eye)
        p.drawLine(QPointF(2.6, 13.0), QPointF(13.4, 3.0))

    p.end()
    return QIcon(pm)


class CodexSwitch(QAbstractButton):
    """Codex 极简黑白胶囊开关控件，无缝兼容 QCheckBox 的 isChecked/setChecked 接口"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self._knob_pos = 0.0
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._on_toggled)

    @pyqtProperty(float)
    def knobPos(self):
        return self._knob_pos

    @knobPos.setter
    def knobPos(self, val):
        self._knob_pos = float(val)
        self.update()

    def _on_toggled(self, checked):
        target = 1.0 if checked else 0.0
        if self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._knob_pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._anim.stop()
            self._knob_pos = target
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        checked = self.isChecked()
        hovered = self.underMouse()

        if checked:
            track_color = QColor("#27272A") if hovered else QColor("#0D0D0D")
        else:
            track_color = QColor("#A1A1AA") if hovered else QColor("#D4D4D8")

        p.setPen(Qt.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(QRectF(0.0, 1.0, 38.0, 20.0), 10.0, 10.0)

        # 白色滑块圆点
        knob_d = 16.0
        knob_x = 2.0 + self._knob_pos * 18.0
        knob_y = 3.0
        p.setBrush(QColor(0, 0, 0, 24))
        p.drawEllipse(QRectF(knob_x, knob_y + 0.6, knob_d, knob_d))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(knob_x, knob_y, knob_d, knob_d))


class CodexComboBox(QComboBox):
    """Codex 风格下拉选择框，自带高清细线条下箭头"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor("#71717A"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx = self.width() - 14.0
        cy = self.height() / 2.0
        p.drawPolyline(QPointF(cx - 3.6, cy - 1.6), QPointF(cx, cy + 2.2), QPointF(cx + 3.6, cy - 1.6))


class CodexSlider(QSlider):
    """Codex 风格纯矢量滑动条，彻底消除原生 Windows 样式的方形锯齿滑块"""
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setFixedHeight(22)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pad_x = 8.0
        track_w = max(10.0, float(self.width()) - 2.0 * pad_x)
        cy = self.height() / 2.0

        # 底层细轨道
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E4E4E7"))
        p.drawRoundedRect(QRectF(pad_x, cy - 2.0, track_w, 4.0), 2.0, 2.0)

        span = max(1, self.maximum() - self.minimum())
        ratio = max(0.0, min(1.0, float(self.value() - self.minimum()) / float(span)))
        fill_w = ratio * track_w

        # 已激活黑色轨道
        if fill_w > 0.5:
            p.setBrush(QColor("#0D0D0D"))
            p.drawRoundedRect(QRectF(pad_x, cy - 2.0, fill_w, 4.0), 2.0, 2.0)

        # 圆形极简滑块
        cx = pad_x + fill_w
        r = 7.0 if not self.underMouse() else 7.6
        p.setBrush(QColor(0, 0, 0, 28))
        p.drawEllipse(QPointF(cx, cy + 0.8), r, r)
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(QPen(QColor("#0D0D0D"), 2.0))
        p.drawEllipse(QPointF(cx, cy), r - 1.0, r - 1.0)


class CodexCard(QFrame):
    """Codex 风格分组列表容器：纯白底 + 1px 细边框 + 内部分割线"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("codexCard")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._row_count = 0

    def add_row(self, title, description="", control=None):
        if self._row_count > 0:
            div_wrap = QWidget()
            div_wrap.setObjectName("codexDivWrap")
            div_wrap.setStyleSheet("QWidget#codexDivWrap { background: transparent; }")
            div_lay = QHBoxLayout(div_wrap)
            div_lay.setContentsMargins(16, 0, 16, 0)
            div_lay.setSpacing(0)
            div = QFrame()
            div.setObjectName("codexDivider")
            div.setFixedHeight(1)
            div.setStyleSheet("QFrame#codexDivider { background: #F1F1F5; border: none; }")
            div_lay.addWidget(div)
            self._layout.addWidget(div_wrap)

        row = QWidget()
        row.setObjectName("codexRow")
        row.setStyleSheet("QWidget#codexRow { background: transparent; }")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(16, 12, 16, 12)
        row_lay.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("QLabel { font-size: 13px; font-weight: 400; color: #0D0D0D; background: transparent; }")
        text_col.addWidget(lbl_title)

        if description:
            lbl_desc = QLabel(description)
            lbl_desc.setWordWrap(True)
            lbl_desc.setStyleSheet("QLabel { font-size: 11.5px; color: #71717A; background: transparent; }")
            text_col.addWidget(lbl_desc)

        row_lay.addLayout(text_col, 1)

        if control is not None:
            row_lay.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)

        self._layout.addWidget(row)
        self._row_count += 1
        return row


class PinToast(QWidget):
    """置顶/取消置顶浮动状态轻量级提示框"""
    def __init__(self, text, pinned=True, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)

        lbl = QLabel(text)
        bg = "#0D0D0D" if pinned else "#3F3F46"
        border = "rgba(255, 255, 255, 0.16)"
        lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: #FAFAFA;
                border: 1px solid {border};
                font-family: {CODEX_FONT_FAMILY};
                font-size: 12.5px;
                font-weight: 600;
                padding: 8px 16px;
                border-radius: 9px;
            }}
        """)
        layout.addWidget(lbl)
        self.adjustSize()

        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geo = screen.geometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + 60
        self.move(x, y)

        QTimer.singleShot(2200, self.close)


_active_pin_toast = None

def show_pin_toast(text, pinned=True):
    global _active_pin_toast
    try:
        if _active_pin_toast:
            _active_pin_toast.close()
    except Exception:
        pass
    _active_pin_toast = PinToast(text, pinned)
    _active_pin_toast.show()


class PinnedWindowBadge(QWidget):
    """被置顶窗口顶部的常驻大头针标志，直观指示置顶状态并支持直接点击取消置顶"""
    unpin_requested = pyqtSignal(int)

    def __init__(self, target_hwnd, title=""):
        super().__init__(None)
        self.target_hwnd = int(target_hwnd)
        self.target_title = title or ""
        self._hovered = False
        self._pressed = False
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(84, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self._build_tooltip())

    def _build_tooltip(self):
        name = f"「{self.target_title}」" if self.target_title else "当前窗口"
        return f"📌 {name} 已置顶（点击取消置顶）"

    def set_title(self, title):
        if title and title != self.target_title:
            self.target_title = title
            self.setToolTip(self._build_tooltip())

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        elif event.button() == Qt.RightButton:
            menu = QMenu(self)
            act_unpin = menu.addAction("📌 取消窗口置顶")
            chosen = menu.exec_(event.globalPos())
            if chosen == act_unpin:
                self.unpin_requested.emit(self.target_hwnd)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.pos()):
                self.unpin_requested.emit(self.target_hwnd)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)

        if self._pressed:
            bg = QColor(185, 28, 28, 248)
            border = QColor(254, 202, 202, 230)
        elif self._hovered:
            bg = QColor(220, 38, 38, 245)
            border = QColor(255, 255, 255, 220)
        else:
            bg = QColor(24, 24, 27, 234)
            border = QColor(239, 68, 68, 225)

        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.fillPath(path, QBrush(bg))
        painter.setPen(QPen(border, 1.25))
        painter.drawPath(path)

        # 绘制左侧大头针矢量图标（针尖朝下指向被置顶窗口）
        painter.save()
        pin_cx = rect.left() + 13.0
        pin_cy = rect.center().y()
        painter.translate(pin_cx, pin_cy)
        painter.rotate(32)
        painter.setPen(QPen(QColor(228, 228, 231), 1.4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(0.0, 2.2), QPointF(0.0, 6.2))
        painter.setPen(Qt.NoPen)
        head_color = QColor(255, 255, 255) if self._hovered else QColor(239, 68, 68)
        cap_color = QColor(254, 226, 226) if self._hovered else QColor(248, 113, 113)
        painter.setBrush(QBrush(head_color))
        painter.drawRoundedRect(QRectF(-4.0, 0.2, 8.0, 2.2), 1.0, 1.0)
        painter.drawRoundedRect(QRectF(-2.2, -3.4, 4.4, 3.8), 1.0, 1.0)
        painter.setBrush(QBrush(cap_color))
        painter.drawEllipse(QRectF(-3.6, -5.8, 7.2, 3.0))
        painter.restore()

        label_text = "取消置顶" if self._hovered else "已置顶"
        font = make_ui_font(8, QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        text_rect = QRectF(rect.left() + 21.0, rect.top(), rect.width() - 25.0, rect.height())
        painter.drawText(text_rect, Qt.AlignCenter, label_text)


class WindowPinManager(QObject):
    """管理所有通过 DeskPin 置顶的外部窗口及其顶部常驻置顶标志。"""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = WindowPinManager()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._tracked = {}  # hwnd -> {'badge': PinnedWindowBadge, 'title': str}
        self._overlay_suppressed = False
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(60)
        self._sync_timer.timeout.connect(self._sync_all)

    def track(self, hwnd, title=""):
        hwnd = int(hwnd)
        if not hwnd or not _user32.IsWindow(hwnd):
            return
        entry = self._tracked.get(hwnd)
        if entry is None:
            badge = PinnedWindowBadge(hwnd, title)
            badge.unpin_requested.connect(self.unpin)
            self._tracked[hwnd] = {'badge': badge, 'title': title or ""}
        else:
            if title:
                entry['title'] = title
                entry['badge'].set_title(title)
        if not self._sync_timer.isActive():
            self._sync_timer.start()
        self._sync_all()

    def untrack(self, hwnd):
        hwnd = int(hwnd)
        entry = self._tracked.pop(hwnd, None)
        if entry is not None:
            badge = entry.get('badge')
            if badge is not None:
                try:
                    badge.close()
                    badge.deleteLater()
                except RuntimeError as e:
                    logging.debug("关闭置顶角标时窗口已释放: %s", e)
        if not self._tracked and self._sync_timer.isActive():
            self._sync_timer.stop()

    def unpin(self, hwnd):
        hwnd = int(hwnd)
        entry = self._tracked.get(hwnd)
        win_name = (entry.get('title') if entry else "") or f"窗口 (HWND:{hwnd})"
        try:
            if _user32.IsWindow(hwnd):
                SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
                _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        except Exception as e:
            logging.error("通过置顶标志取消窗口置顶失败: %s", e)
        self.untrack(hwnd)
        show_pin_toast(f"📌 已取消窗口置顶: {win_name}", pinned=False)

    def set_overlay_suppressed(self, suppressed: bool):
        self._overlay_suppressed = bool(suppressed)
        if self._overlay_suppressed:
            for entry in list(self._tracked.values()):
                badge = entry.get('badge')
                if badge is not None:
                    try:
                        badge.hide()
                    except Exception:
                        pass
        else:
            self._sync_all()

    def clear_all(self):
        if self._sync_timer.isActive():
            self._sync_timer.stop()
        for hwnd in list(self._tracked.keys()):
            self.untrack(hwnd)

    @staticmethod
    def _is_hwnd_above(target_hwnd, badge_hwnd):
        try:
            cur = _user32.GetWindow(badge_hwnd, GW_HWNDPREV)
            steps = 0
            while cur and steps < 64:
                if int(cur) == int(target_hwnd):
                    return True
                cur = _user32.GetWindow(cur, GW_HWNDPREV)
                steps += 1
        except Exception as e:
            logging.debug("检查窗口 Z 序异常: %s", e)
        return False

    @staticmethod
    def _get_target_logical_rect(hwnd):
        r = _RECT()
        hr = _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
        if hr != 0:
            if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None
        w = r.right - r.left
        h = r.bottom - r.top
        if w <= 0 or h <= 0:
            return None
        return _phys_rect_to_logical_qrect(r.left, r.top, w, h)

    def _sync_all(self):
        if not self._tracked:
            if self._sync_timer.isActive():
                self._sync_timer.stop()
            return
        for hwnd in list(self._tracked.keys()):
            entry = self._tracked.get(hwnd)
            if entry is None:
                continue
            badge = entry['badge']
            try:
                if not _user32.IsWindow(hwnd):
                    self.untrack(hwnd)
                    continue
                ex_style = _user32.GetWindowLongW(hwnd, -20)
                if not (ex_style & WS_EX_TOPMOST):
                    self.untrack(hwnd)
                    continue
                if self._overlay_suppressed or not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                    if badge.isVisible():
                        badge.hide()
                    continue
                win_rect = self._get_target_logical_rect(hwnd)
                if win_rect is None or win_rect.width() < 40 or win_rect.height() < 30:
                    if badge.isVisible():
                        badge.hide()
                    continue

                screen = QApplication.screenAt(win_rect.center()) or QApplication.primaryScreen()
                s_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
                bw, bh = badge.width(), badge.height()
                bx = win_rect.left() + (win_rect.width() - bw) // 2
                bx = max(s_geo.left() + 4, min(bx, s_geo.right() - bw - 4))
                if win_rect.top() - bh + 5 >= s_geo.top():
                    by = win_rect.top() - bh + 5
                else:
                    by = max(s_geo.top() + 3, win_rect.top() + 3)

                if badge.x() != bx or badge.y() != by:
                    badge.move(bx, by)
                if not badge.isVisible():
                    badge.show()
                badge_hwnd = int(badge.winId())
                if badge_hwnd and self._is_hwnd_above(hwnd, badge_hwnd):
                    _user32.SetWindowPos(
                        badge_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
                    )
            except Exception as e:
                logging.debug("同步置顶标志异常 (HWND=%s): %s", hwnd, e)


class WindowPinPicker(QWidget):
    """DeskPin 窗口置顶拾取器：全屏覆盖并吸附顶层窗口，左键切换置顶，右键/ESC 退出"""
    _instance = None

    @classmethod
    def start_picking(cls):
        if cls._instance is not None:
            try:
                cls._instance.close()
            except Exception:
                pass
        WindowPinManager.get_instance().set_overlay_suppressed(True)
        cls._instance = WindowPinPicker()
        cls._instance.show()
        _force_activate_window(cls._instance)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(create_deskpin_cursor())

        desktop = QApplication.desktop()
        geom = desktop.geometry() if desktop else QApplication.primaryScreen().geometry()
        self.setGeometry(geom)

        self.snapper = WindowSnapper(my_pid=os.getpid(), exclude_hwnds={int(self.winId())})

        self.hovered_hwnd = None
        self.hovered_rect = None
        self.hovered_title = ""
        self.hovered_is_topmost = False

    def mouseMoveEvent(self, event):
        pos = event.globalPos()
        px, py = _logical_point_to_phys(pos.x(), pos.y())
        hwnd, rect, title = self.snapper.detect_top_level_at(px, py)
        if hwnd and hwnd != int(self.winId()):
            self.hovered_hwnd = hwnd
            self.hovered_rect = _phys_rect_to_logical_qrect(rect.x(), rect.y(), rect.width(), rect.height()) if rect else None
            self.hovered_title = title
            try:
                ex_style = _user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
                self.hovered_is_topmost = bool(ex_style & WS_EX_TOPMOST)
            except Exception:
                self.hovered_is_topmost = False
        else:
            self.hovered_hwnd = None
            self.hovered_rect = None
            self.hovered_title = ""
            self.hovered_is_topmost = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.globalPos()
            px, py = _logical_point_to_phys(pos.x(), pos.y())
            hwnd, rect, title = self.snapper.detect_top_level_at(px, py)
            if hwnd and hwnd != int(self.winId()):
                self.toggle_topmost(hwnd, title)
            self.close()
        elif event.button() == Qt.RightButton:
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def toggle_topmost(self, hwnd, title):
        try:
            ex_style = _user32.GetWindowLongW(hwnd, -20)
            is_top = bool(ex_style & WS_EX_TOPMOST)
            SWP_FLAGS = 0x0001 | 0x0002  # SWP_NOSIZE | SWP_NOMOVE
            win_name = title if title else f"窗口 (HWND:{hwnd})"
            if is_top:
                res = _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_FLAGS)
                ex_after = _user32.GetWindowLongW(hwnd, -20)
                still_top = bool(ex_after & WS_EX_TOPMOST)
                if not still_top or res:
                    WindowPinManager.get_instance().untrack(hwnd)
                    show_pin_toast(f"📌 已取消窗口置顶: {win_name}", pinned=False)
                    logging.info("已成功取消置顶窗口 %s (HWND %d, res=%s)", win_name, hwnd, res)
                else:
                    logging.warning("取消置顶窗口失败 %s (HWND %d, res=%s)", win_name, hwnd, res)
            else:
                res = _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
                _user32.BringWindowToTop(hwnd)
                _user32.SetForegroundWindow(hwnd)
                ex_after = _user32.GetWindowLongW(hwnd, -20)
                now_top = bool(ex_after & WS_EX_TOPMOST)
                if now_top or res:
                    WindowPinManager.get_instance().track(hwnd, win_name)
                    show_pin_toast(f"📌 已将窗口固定置顶: {win_name}", pinned=True)
                    logging.info("已成功将窗口固定置顶 %s (HWND %d, res=%s)", win_name, hwnd, res)
                else:
                    logging.warning("置顶窗口失败 %s (HWND %d, res=%s)", win_name, hwnd, res)
        except Exception as e:
            logging.error("切换窗口置顶失败: %s", e)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 细微暗化屏幕背景，增强焦点吸附感
        painter.fillRect(self.rect(), QColor(0, 0, 0, 30))

        if self.hovered_rect and not self.hovered_rect.isNull():
            local_tl = self.mapFromGlobal(self.hovered_rect.topLeft())
            draw_rect = QRect(local_tl.x(), local_tl.y(), self.hovered_rect.width(), self.hovered_rect.height())

            border_color = QColor("#ef4444") if self.hovered_is_topmost else QColor("#C8963E")
            bg_fill = QColor(239, 68, 68, 30) if self.hovered_is_topmost else QColor(200, 150, 62, 32)

            painter.setBrush(QBrush(bg_fill))
            painter.setPen(QPen(border_color, 3))
            painter.drawRoundedRect(draw_rect, 4, 4)

            status_text = "📌 窗口当前已置顶 (点击取消置顶)" if self.hovered_is_topmost else "📌 点击将此窗口置顶"
            disp_title = self.hovered_title[:45] + ("..." if len(self.hovered_title) > 45 else "")
            badge_text = f"{status_text}  |  {disp_title}" if disp_title else status_text

            font = make_ui_font(9, QFont.DemiBold)
            painter.setFont(font)
            fm = QFontMetrics(font)
            bw = fm.horizontalAdvance(badge_text) + 24
            bh = 30
            bx = max(10, min(draw_rect.left(), self.width() - bw - 10))
            by = draw_rect.top() - bh - 8
            if by < 10:
                by = draw_rect.top() + 10

            badge_rect = QRect(bx, by, bw, bh)
            painter.setPen(Qt.NoPen)
            badge_bg = QColor(220, 38, 38, 235) if self.hovered_is_topmost else QColor(15, 23, 42, 235)
            painter.setBrush(QBrush(badge_bg))
            painter.drawRoundedRect(badge_rect, 6, 6)

            painter.setPen(QPen(Qt.white))
            painter.drawText(badge_rect, Qt.AlignCenter, badge_text)

        # 屏幕顶部操作指引卡片
        banner_font = make_ui_font(10, QFont.DemiBold)
        painter.setFont(banner_font)
        bfm = QFontMetrics(banner_font)
        inst_text = "📌 窗口置顶模式 (DeskPin)：点击任意窗口切换置顶状态  |  右键或 ESC 退出"
        iw = bfm.horizontalAdvance(inst_text) + 36
        ih = 36
        ix = (self.width() - iw) // 2
        iy = 20
        inst_rect = QRect(ix, iy, iw, ih)

        painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
        painter.setBrush(QBrush(QColor(24, 24, 27, 235)))
        painter.drawRoundedRect(inst_rect, 8, 8)

        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(inst_rect, Qt.AlignCenter, inst_text)

    def closeEvent(self, event):
        WindowPinPicker._instance = None
        WindowPinManager.get_instance().set_overlay_suppressed(False)
        super().closeEvent(event)


def draw_viewfinder_corners(painter, rect, color, length=14, width=3):
    """在选区四角绘制高精相机取景框角标"""
    pen = QPen(color, width)
    pen.setCapStyle(Qt.SquareCap)
    painter.setPen(pen)
    l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
    # 左上
    painter.drawLine(l, t, l + length, t)
    painter.drawLine(l, t, l, t + length)
    # 右上
    painter.drawLine(r, t, r - length, t)
    painter.drawLine(r, t, r, t + length)
    # 左下
    painter.drawLine(l, b, l + length, b)
    painter.drawLine(l, b, l, b - length)
    # 右下
    painter.drawLine(r, b, r - length, b)
    painter.drawLine(r, b, r, b - length)

def draw_selection_badge(painter, rect, text, border_color=QColor(168, 118, 40), bg_color=QColor(255, 255, 255, 240), text_color=QColor(29, 29, 31)):
    """在选区边缘绘制清晰的浅色信息胶囊气泡"""
    font = make_ui_font(9, QFont.DemiBold)
    painter.setFont(font)
    fm = QFontMetrics(font)
    txt_w = fm.horizontalAdvance(text) + 16
    txt_h = 24
    bx = rect.left()
    by = rect.top() - txt_h - 6
    if by < 10:
        by = rect.bottom() + 6
    if painter.device():
        dev_w = painter.device().width()
        if bx + txt_w > dev_w - 10:
            bx = dev_w - txt_w - 10
    bx = max(10, bx)
    badge_rect = QRect(bx, by, txt_w, txt_h)

    painter.setPen(QPen(border_color, 1))
    painter.setBrush(QBrush(bg_color))
    painter.drawRoundedRect(badge_rect, 5, 5)
    painter.setPen(QPen(text_color))
    painter.drawText(badge_rect, Qt.AlignCenter, text)
    painter.setBrush(Qt.NoBrush)

# ==========================================
# DEEP COPY HELPER FOR UNDO/REDO
# ==========================================
def clone_edits(edits):
    cloned = []
    for e in edits:
        new_e = {}
        for k, v in e.items():
            if isinstance(v, QRect): new_e[k] = QRect(v)
            elif isinstance(v, QPoint): new_e[k] = QPoint(v)
            elif isinstance(v, QColor): new_e[k] = QColor(v)
            elif isinstance(v, QFont): new_e[k] = QFont(v)
            elif isinstance(v, list):
                new_e[k] = [QPoint(p) if isinstance(p, QPoint) else p for p in v]
            else: new_e[k] = v
        cloned.append(new_e)
    return cloned


def transform_pixmap_op(pixmap, op):
    """对 QPixmap 执行无损正交旋转或镜像翻转。"""
    if pixmap is None or pixmap.isNull():
        return pixmap
    if op == 'cw90':
        return pixmap.transformed(QTransform().rotate(90), Qt.FastTransformation)
    if op == 'ccw90':
        return pixmap.transformed(QTransform().rotate(-90), Qt.FastTransformation)
    if op == 'rot180':
        return pixmap.transformed(QTransform().rotate(180), Qt.FastTransformation)
    if op == 'flip_h':
        return pixmap.transformed(QTransform().scale(-1, 1), Qt.FastTransformation)
    if op == 'flip_v':
        return pixmap.transformed(QTransform().scale(1, -1), Qt.FastTransformation)
    return QPixmap(pixmap)


def rotate_pixmap_angle(pixmap, angle_deg):
    """按任意角度（顺时针为正）旋转 QPixmap；90° 倍数自动走无损快路径。"""
    if pixmap is None or pixmap.isNull():
        return pixmap
    norm = float(angle_deg) % 360.0
    if abs(norm) < 1e-6 or abs(norm - 360.0) < 1e-6:
        return QPixmap(pixmap)
    if abs(norm - 90.0) < 1e-6:
        return transform_pixmap_op(pixmap, 'cw90')
    if abs(norm - 180.0) < 1e-6:
        return transform_pixmap_op(pixmap, 'rot180')
    if abs(norm - 270.0) < 1e-6:
        return transform_pixmap_op(pixmap, 'ccw90')
    return pixmap.transformed(QTransform().rotate(float(angle_deg)), Qt.SmoothTransformation)


def _transform_point_op_f(x, y, src_w, src_h, op):
    # 标注坐标为连续几何坐标（像素边界），因此使用 W - x 而不是像素索引的 W - 1 - x
    sw = max(1.0, float(src_w))
    sh = max(1.0, float(src_h))
    if op == 'cw90':
        return (sh - y, x)
    if op == 'ccw90':
        return (y, sw - x)
    if op == 'rot180':
        return (sw - x, sh - y)
    if op == 'flip_h':
        return (sw - x, y)
    if op == 'flip_v':
        return (x, sh - y)
    return (x, y)


def _map_pointf_between(x, y, old_origin, old_disp_size, old_true_size,
                        new_origin, new_disp_size, new_true_size, op=None):
    lx = float(x) - float(old_origin.x())
    ly = float(y) - float(old_origin.y())
    if op is not None:
        if old_disp_size != old_true_size and old_disp_size.width() > 0 and old_disp_size.height() > 0:
            lx *= float(old_true_size.width()) / float(old_disp_size.width())
            ly *= float(old_true_size.height()) / float(old_disp_size.height())
        rx, ry = _transform_point_op_f(lx, ly, old_true_size.width(), old_true_size.height(), op)
        if new_disp_size != new_true_size and new_true_size.width() > 0 and new_true_size.height() > 0:
            rx *= float(new_disp_size.width()) / float(new_true_size.width())
            ry *= float(new_disp_size.height()) / float(new_true_size.height())
    else:
        rx = lx * (float(new_disp_size.width()) / max(1.0, float(old_disp_size.width())))
        ry = ly * (float(new_disp_size.height()) / max(1.0, float(old_disp_size.height())))
    return (float(new_origin.x()) + rx, float(new_origin.y()) + ry)


def _map_point_between(pt, old_origin, old_disp_size, old_true_size,
                       new_origin, new_disp_size, new_true_size, op=None):
    nx, ny = _map_pointf_between(
        pt.x(), pt.y(),
        old_origin, old_disp_size, old_true_size,
        new_origin, new_disp_size, new_true_size,
        op=op
    )
    return QPoint(int(round(nx)), int(round(ny)))


def translate_edits(edits, delta):
    """原地安全平移所有标注图元坐标，避免共享 QPoint 引用重复偏移。"""
    if not edits or delta.isNull():
        return
    dx, dy = delta.x(), delta.y()
    d = QPoint(dx, dy)
    for edit in edits:
        if 'rect' in edit and isinstance(edit['rect'], QRect):
            edit['rect'] = edit['rect'].translated(d)
        for key in ('pos', 'center', 'start', 'end', 'p1', 'p2'):
            if key in edit and isinstance(edit[key], QPoint):
                edit[key] = QPoint(edit[key].x() + dx, edit[key].y() + dy)
        if 'points' in edit and isinstance(edit['points'], list):
            edit['points'] = [QPoint(p.x() + dx, p.y() + dy) for p in edit['points']]


def scale_edits(edits, old_origin, old_size, new_origin, new_size):
    """将标注图元按比例从旧矩形映射到新矩形（用于超大图展示与导出分辨率映射）。"""
    cloned = clone_edits(edits)
    if not cloned:
        return cloned
    sx = float(new_size.width()) / max(1.0, float(old_size.width()))
    sy = float(new_size.height()) / max(1.0, float(old_size.height()))
    s_avg = (sx + sy) * 0.5

    def _mp(pt):
        return _map_point_between(pt, old_origin, old_size, old_size, new_origin, new_size, new_size, op=None)

    for edit in cloned:
        for key in ('pos', 'center', 'start', 'end', 'p1', 'p2'):
            if key in edit and isinstance(edit[key], QPoint):
                edit[key] = _mp(edit[key])
        if 'points' in edit and isinstance(edit['points'], list):
            edit['points'] = [_mp(p) for p in edit['points']]
        if 'rect' in edit and isinstance(edit['rect'], QRect):
            r = edit['rect']
            p1 = _mp(r.topLeft())
            p2 = _mp(r.bottomRight())
            edit['rect'] = QRect(p1, p2).normalized()
        for wk in ('thickness', 'strokeWidth', 'width', 'brushWidth', 'blockSize', 'cornerRadius', 'radius'):
            if wk in edit and isinstance(edit[wk], (int, float)) and edit[wk] > 0:
                edit[wk] = max(1, int(round(edit[wk] * s_avg)))
        if edit.get('type') == 'TEXT':
            fs = edit.get('fontSize')
            if isinstance(fs, (int, float)) and fs > 0:
                new_fs = max(6, int(round(fs * s_avg)))
                edit['fontSize'] = new_fs
                if isinstance(edit.get('font'), QFont):
                    f = QFont(edit['font'])
                    f.setPointSize(new_fs)
                    edit['font'] = f
            if edit.get('user_width') is not None:
                edit['user_width'] = max(16, int(round(edit['user_width'] * sx)))
            if edit.get('user_height') is not None:
                edit['user_height'] = max(12, int(round(edit['user_height'] * sy)))
    return cloned


def transform_edits_op(edits, old_origin, old_disp_size, old_true_size,
                       new_origin, new_disp_size, new_true_size, op):
    """对全部矢量/位图标注执行正交旋转或翻转坐标映射，保持可编辑与可擦除。"""
    cloned = clone_edits(edits)
    if not cloned:
        return cloned

    def _mp(pt):
        return _map_point_between(
            pt, old_origin, old_disp_size, old_true_size,
            new_origin, new_disp_size, new_true_size, op=op
        )

    old_scale_x = float(old_disp_size.width()) / max(1.0, float(old_true_size.width()))
    new_scale_x = float(new_disp_size.width()) / max(1.0, float(new_true_size.width()))
    disp_ratio = new_scale_x / max(1e-6, old_scale_x)

    for edit in cloned:
        t = edit.get('type')
        if t == 'TEXT':
            box_rect = get_text_annotation_rect(edit, QPoint(0, 0))
            cx, cy = box_rect.center().x(), box_rect.center().y()
            ncx, ncy = _map_pointf_between(
                cx, cy, old_origin, old_disp_size, old_true_size,
                new_origin, new_disp_size, new_true_size, op=op
            )
            bw = box_rect.width() * disp_ratio
            bh = box_rect.height() * disp_ratio
            rot = int(edit.get('rotation', 0)) % 360
            if op == 'cw90':
                new_rot = (rot + 90) % 360
            elif op == 'ccw90':
                new_rot = (rot - 90) % 360
            elif op == 'rot180':
                new_rot = (rot + 180) % 360
            elif op in ('flip_h', 'flip_v'):
                new_rot = (360 - rot) % 360 if rot in (90, 270) else rot
            else:
                new_rot = rot
            new_rect = QRect(
                int(round(ncx - bw * 0.5)),
                int(round(ncy - bh * 0.5)),
                max(8, int(round(bw))),
                max(8, int(round(bh)))
            )
            edit['rect'] = new_rect
            edit['pos'] = new_rect.topLeft()
            edit['rotation'] = new_rot
            if abs(disp_ratio - 1.0) > 1e-3:
                fs = edit.get('fontSize')
                if isinstance(fs, (int, float)) and fs > 0:
                    new_fs = max(6, int(round(fs * disp_ratio)))
                    edit['fontSize'] = new_fs
                    if isinstance(edit.get('font'), QFont):
                        f = QFont(edit['font'])
                        f.setPointSize(new_fs)
                        edit['font'] = f
                if edit.get('user_width') is not None:
                    edit['user_width'] = max(16, int(round(edit['user_width'] * disp_ratio)))
                if edit.get('user_height') is not None:
                    edit['user_height'] = max(12, int(round(edit['user_height'] * disp_ratio)))
            continue

        if 'rect' in edit and isinstance(edit['rect'], QRect):
            r = edit['rect']
            p1 = _mp(r.topLeft())
            p2 = _mp(r.bottomRight())
            edit['rect'] = QRect(p1, p2).normalized()
        for key in ('pos', 'center', 'start', 'end', 'p1', 'p2'):
            if key in edit and isinstance(edit[key], QPoint):
                edit[key] = _mp(edit[key])
        if 'points' in edit and isinstance(edit['points'], list):
            edit['points'] = [_mp(p) for p in edit['points']]
        if abs(disp_ratio - 1.0) > 1e-3:
            for wk in ('thickness', 'strokeWidth', 'width', 'brushWidth', 'blockSize', 'cornerRadius', 'radius'):
                if wk in edit and isinstance(edit[wk], (int, float)) and edit[wk] > 0:
                    edit[wk] = max(1, int(round(edit[wk] * disp_ratio)))
    return cloned


def composite_pixmap_with_edits(base_pixmap, edits, origin=QPoint(0, 0), disp_size=None,
                                base_source=None, base_origin=QPoint(0, 0)):
    """将底图与当前标注合成为独立 QPixmap。"""
    if base_pixmap is None or base_pixmap.isNull():
        return QPixmap()
    if not edits:
        return QPixmap(base_pixmap)
    final_img = QPixmap(base_pixmap)
    src = base_source if (base_source is not None and not base_source.isNull()) else base_pixmap
    painter = QPainter(final_img)
    if disp_size is not None and disp_size != base_pixmap.size() and disp_size.width() > 0 and disp_size.height() > 0:
        scaled = scale_edits(edits, origin, disp_size, QPoint(0, 0), base_pixmap.size())
        draw_all_edits(painter, scaled, base_pixmap, QPoint(0, 0), base_origin=QPoint(0, 0))
    else:
        draw_all_edits(painter, edits, src, origin, base_origin=base_origin)
    painter.end()
    return final_img

# ==========================================
# 在线多语言翻译服务与工作线程 (支持中文、英文、菲律宾语、泰语)
# ==========================================
SUPPORTED_LANGUAGES = [
    ("zh-CN", "🇨🇳 中文 (Chinese)"),
    ("en", "🇬🇧 英语 (English)"),
    ("tl", "🇵🇭 菲律宾语 (Filipino)"),
    ("th", "🇹🇭 泰语 (Thai)"),
]

LANG_NAME_MAP = {
    "zh": "中文",
    "zh-CN": "中文",
    "zh-TW": "繁体中文",
    "en": "英语",
    "tl": "菲律宾语",
    "fil": "菲律宾语",
    "th": "泰语",
}

def resolve_ocr_target_lang(text=""):
    """智能判断默认目标语言：中英混排或有明显外文正文时优先译中文，避免误把英文对话反向译英。"""
    t = text or ""
    cjk_chars = len(re.findall(r'[\u4e00-\u9fff]', t))
    non_cjk = len(re.findall(r'[A-Za-z\u00C0-\u024F\u0E00-\u0E7F\u3040-\u30ff\uac00-\ud7af]', t))
    natural = "en" if (cjk_chars > 0 and cjk_chars * 3.0 >= non_cjk * 1.4 and non_cjk < 30) else "zh-CN"

    saved = str(load_settings().get("ocr_target_lang", "") or "").strip()
    valid_codes = {code for code, _label in SUPPORTED_LANGUAGES}
    if saved not in valid_codes:
        return natural
    # 东南亚小语种（菲/泰）显式偏好保留；中/英切换若与当前截图语种冲突则自动自适应翻转
    if saved in ("tl", "th"):
        return saved
    if saved == "en" and non_cjk >= 18 and cjk_chars * 3.0 < non_cjk:
        return "zh-CN"
    if saved == "zh-CN" and natural == "en" and cjk_chars >= 6 and non_cjk < 12:
        return "en"
    return saved

def translate_single_chunk(text, target_lang, source_lang='auto', timeout=6):
    """单段落文本在线翻译：主通道 Chrome 接口，备用通道 MyMemory"""
    if not text or not text.strip():
        return "", source_lang
    text = text.strip()

    # 1. 优先使用 Google Translate dict-chrome-ex 接口
    try:
        url = (
            f"https://translate.googleapis.com/translate_a/single?client=dict-chrome-ex"
            f"&sl={source_lang}&tl={target_lang}&dt=t&q={urllib.parse.quote(text)}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            result = "".join([part[0] for part in data[0] if part[0]])
            detected_src = data[2] if len(data) > 2 else source_lang
            if result:
                low = result.strip().lower()
                # 源语言与目标语言相同时 Google 会返回这句提示，直接保留原文
                if "please select two distinct languages" in low:
                    return text, detected_src
                src_base = str(detected_src).split('-')[0].lower()
                tgt_base = str(target_lang).split('-')[0].lower()
                if src_base == tgt_base:
                    return text, detected_src
                return result, detected_src
    except Exception as e:
        logging.warning("Google 翻译通道请求失败: %s，尝试备用通道", e)

    # 2. 备用通道：MyMemory API
    try:
        src_pair = "autodetect" if source_lang == 'auto' else source_lang
        tgt_pair = "zh-CN" if target_lang == 'zh-CN' else target_lang
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair={src_pair}|{tgt_pair}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            trans_text = data.get('responseData', {}).get('translatedText')
            if trans_text:
                low = str(trans_text).strip().lower()
                if "please select two distinct languages" in low or "invalid language pair" in low:
                    return text, source_lang
                return trans_text, source_lang
    except Exception as e2:
        logging.error("备用翻译通道请求失败: %s", e2)

    raise RuntimeError("翻译请求超时或网络异常，请检查网络设置。")

def _looks_like_target(text, target_lang):
    """谨慎判断文本是否已纯为目标语言或纯标识符/数字，避免误跳日文汉字、法/西/菲等拉丁语或中英混排句。"""
    t = (text or "").strip()
    if not t:
        return True
    # 纯数字、时间戳、百分比、货币或纯符号无需翻译
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return True
    # 纯 URL / 路径 / 版本号 / 点号及双下划线隐藏目录(.git, __pycache__) / 带单位数字(15 KB) / 用户名句柄 直接保留
    if ' ' not in t and re.fullmatch(
        r'(?:https?://\S+|[/\\][\w./\\-]+|v?\d+(?:\.\d+)+[a-zA-Z0-9_-]*'
        r'|\d+(?:\.\d+)?[smhdw%]?'
        r'|\.[A-Za-z0-9_.-]+|__[A-Za-z0-9_]+__'
        r'|@[A-Za-z0-9_.-]+|[A-Za-z]+_?[A-Za-z0-9_]*\d+[A-Za-z0-9_]*|[a-z0-9]{2,}_+)',
        t, re.IGNORECASE
    ):
        return True
    if re.fullmatch(r'\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB|PB|px|pt|dp|sp|ms|Hz|fps|%)', t, re.IGNORECASE):
        return True

    has_kana = any(('\u3040' <= c <= '\u30ff') for c in t)
    has_hangul = any(('\uac00' <= c <= '\ud7af') for c in t)
    cjk_count = sum(1 for c in letters if '\u4e00' <= c <= '\u9fff')
    ascii_count = sum(1 for c in letters if ord(c) < 128)
    total = len(letters)
    cjk_ratio = cjk_count / total
    ascii_ratio = ascii_count / total

    base = str(target_lang or "").split('-')[0].lower()
    if base == 'zh':
        if has_kana or has_hangul:
            return False
        # 仅当几乎全为汉字（>=85%）且不含明显英文单词时视为已是中文
        has_eng_word = bool(re.search(r'[A-Za-z]{2,}', t))
        return cjk_ratio >= 0.85 and not has_eng_word
    if base == 'en':
        # 拉丁语系（法/西/德/菲等）也全部是 ASCII/拉丁字母，不可按 ascii_ratio>=0.9 跳过！
        # 仅跳过无空格且含下划线/驼峰/斜杠的纯代码标识符
        if cjk_count == 0 and ' ' not in t and re.fullmatch(r'[A-Za-z0-9_./:-]+', t):
            if '_' in t or '/' in t or re.search(r'[a-z][A-Z]', t) or len(t) <= 1:
                return True
        return False
    return False


def translate_text(text, target_lang, source_lang='auto'):
    """多段落/超长文本翻译处理"""
    if not text or not text.strip():
        return "", source_lang
    text = text.strip()
    if len(text) <= 1500:
        return translate_single_chunk(text, target_lang, source_lang)

    paragraphs = text.split('\n')
    results = []
    detected_final = source_lang
    for p in paragraphs:
        if not p.strip():
            results.append("")
            continue
        res, det = translate_single_chunk(p, target_lang, source_lang)
        results.append(res)
        if detected_final == 'auto' and det != 'auto':
            detected_final = det
    return "\n".join(results), detected_final

# ==========================================
# AI 翻译 (火山引擎方舟豆包大模型 API)
# ==========================================
_AI_LANG_NAMES = {
    "zh-CN": "简体中文 (Simplified Chinese)",
    "en": "English",
    "tl": "Filipino (Tagalog)",
    "th": "Thai (ภาษาไทย)",
}

def _clean_ai_output(choice_dict):
    """
    清洗大模型输出，严格剥离思考链（<think>...</think> 或 reasoning 泄露），仅提取最终正文。
    """
    content = choice_dict.get('content')
    if content:
        # 去除 <think>...</think> 标签及其内容
        cleaned = re.sub(r'<think>[\s\S]*?</think>', '', str(content), flags=re.DOTALL).strip()
        if cleaned:
            return cleaned
    
    # 若 content 为空且模型将输出写在了 reasoning 字段
    reasoning = choice_dict.get('reasoning')
    if reasoning:
        cleaned = re.sub(r'<think>[\s\S]*?</think>', '', str(reasoning), flags=re.DOTALL).strip()
        # 若以常见思考词开头，尝试截取最后段落
        lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        if lines:
            return lines[-1]
    return ""

def _call_volc_ark_chat(messages, timeout=25, max_tokens=2048, is_cancelled_fn=None):
    """共享火山引擎方舟豆包大模型调用（支持自动关闭思考与不支持该参数时的降级重试）。"""
    if is_cancelled_fn and is_cancelled_fn():
        return ""
    api_key = get_volc_api_key()
    if not api_key:
        raise RuntimeError("未检测到火山方舟 API 密钥，请在系统设置中配置 翻译 Token (ARK_API_KEY)。")

    model_ep = load_settings().get("volc_model", "").strip() or DEFAULT_VOLC_TRANS_MODEL
    base_payload = {
        "model": model_ep,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    auth_header = api_key if api_key.startswith("Bearer ") else f"Bearer {api_key}"

    for try_mode in ("no_thinking", "fallback_standard"):
        if is_cancelled_fn and is_cancelled_fn():
            return ""
        payload_dict = dict(base_payload)
        if try_mode == "fallback_standard":
            payload_dict.pop("thinking", None)

        req = urllib.request.Request(
            VOLC_ARK_API_URL,
            data=json.dumps(payload_dict).encode('utf-8'),
            headers={"Content-Type": "application/json", "Authorization": auth_header},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                choice = data['choices'][0]['message']
                return _clean_ai_output(choice)
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace') if e.fp else ''
            logging.warning("火山方舟翻译请求失败 (HTTP %s): %s", e.code, body[:180])
            if e.code == 401:
                raise RuntimeError("火山方舟 API 密钥无效或未授权 (HTTP 401)，请在系统设置中检查您的翻译 Token。")
            if e.code == 400 and try_mode == "no_thinking" and "thinking" in body.lower():
                continue
            api_msg = ""
            try:
                err_json = json.loads(body)
                api_msg = err_json.get("error", {}).get("message") or err_json.get("message") or ""
            except Exception:
                api_msg = ""
            raise RuntimeError(f"火山方舟翻译失败: {api_msg}" if api_msg else f"火山方舟翻译失败 (HTTP {e.code})。")
        except Exception as e:
            logging.warning("火山方舟翻译调用异常: %s", e)
            raise RuntimeError(f"火山方舟翻译请求失败: {e}")

    return ""


def ai_translate_single_chunk(text, target_lang, source_lang='auto', timeout=25, is_cancelled_fn=None):
    """使用火山引擎方舟豆包大模型进行极速高质量文本翻译（关闭思考以实现极低延迟与高吞吐）"""
    if not text or not text.strip():
        return "", source_lang
    text = text.strip()

    target_name = _AI_LANG_NAMES.get(target_lang, target_lang)
    if source_lang and source_lang != 'auto':
        source_name = _AI_LANG_NAMES.get(source_lang, source_lang)
        src_hint = f" The source text is in {source_name}."
    else:
        src_hint = " Auto-detect the source language."

    system_prompt = (
        f"You are a professional, accurate translator.{src_hint} "
        f"Translate the following text into {target_name}. "
        f"Output ONLY the translated text without explanations, greetings, or notes."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]
    result = _call_volc_ark_chat(messages, timeout=timeout, max_tokens=2048, is_cancelled_fn=is_cancelled_fn)
    detected_src = source_lang if source_lang != 'auto' else 'auto'
    return result, detected_src


def ai_translate_batch(texts, target_lang, timeout=30, is_cancelled_fn=None):
    """带上下文连贯性的批量 AI 翻译：一次传入同屏多个文字块，返回等长译文列表。"""
    if not texts:
        return []
    target_name = _AI_LANG_NAMES.get(target_lang, target_lang)
    system_prompt = (
        f"You are a professional UI and document translator. "
        f"You will receive a JSON array of {len(texts)} text snippets extracted in reading order from the same screenshot. "
        f"Translate every snippet naturally and coherently into {target_name}, using surrounding snippets as context so terminology and phrasing match. "
        f"If a snippet is already in {target_name} or is a pure identifier/number, keep it as-is. "
        f"Return ONLY a valid JSON array of exact length {len(texts)} (no markdown code fence, no extra text)."
    )
    user_content = json.dumps(list(texts), ensure_ascii=False)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    raw = _call_volc_ark_chat(messages, timeout=timeout, max_tokens=4096, is_cancelled_fn=is_cancelled_fn)
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list) or len(parsed) != len(texts):
        raise ValueError(f"AI 批量返回条数不匹配: {len(parsed) if isinstance(parsed, list) else type(parsed).__name__} != {len(texts)}")
    return [str(item or "").strip() for item in parsed]

def ai_translate_text(text, target_lang, source_lang='auto', is_cancelled_fn=None):
    """AI 翻译：多段落/超长文本处理"""
    if not text or not text.strip():
        return "", source_lang
    text = text.strip()
    if len(text) <= 3000:
        return ai_translate_single_chunk(text, target_lang, source_lang, is_cancelled_fn=is_cancelled_fn)

    # 超长文本分段翻译
    paragraphs = text.split('\n')
    results = []
    detected_final = source_lang
    for p in paragraphs:
        if is_cancelled_fn and is_cancelled_fn():
            return "", source_lang
        if not p.strip():
            results.append("")
            continue
        res, det = ai_translate_single_chunk(p, target_lang, source_lang, is_cancelled_fn=is_cancelled_fn)
        results.append(res)
        if detected_final == 'auto' and det != 'auto':
            detected_final = det
    return "\n".join(results), detected_final

class TranslationWorker(QThread):
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, text, target_lang, source_lang='auto', use_ai=False):
        super().__init__()
        self.text = text
        self.target_lang = target_lang
        self.source_lang = source_lang
        self.use_ai = use_ai
        self._is_cancelled = False

    def cancel(self):
        """协作式安全取消，取代有死锁崩溃风险的 QThread.terminate()"""
        self._is_cancelled = True

    def run(self):
        try:
            if self._is_cancelled:
                return

            if self.use_ai:
                res, det = ai_translate_text(self.text, self.target_lang, self.source_lang, is_cancelled_fn=lambda: self._is_cancelled)
            else:
                res, det = translate_text(self.text, self.target_lang, self.source_lang)

            if not self._is_cancelled:
                self.finished.emit(res, det)
        except Exception as e:
            if not self._is_cancelled:
                self.error.emit(str(e))

# ==========================================
# OCR 结果展示与多语言智能翻译弹窗 (现代浅色双栏对照风格)
# ==========================================
_DETACHED_OCR_WORKERS = set()


class OcrResultDialog(QDialog):
    def __init__(self, text, parent=None, prefill_translation=None, target_lang=None, prefill_error="",
                 detected_codes=None, ocr_doc=None, source_image=None, ocr_warning=""):
        super().__init__(parent)
        self.setWindowTitle("文字识别与翻译 (OCR & Translation)")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.resize(980, 620)
        self.setMinimumSize(800, 500)

        self.current_worker = None
        self._reocr_worker = None
        self._dialog_closed = False
        self.detected_lang = "auto"
        self.prefill_translation = prefill_translation
        self.prefill_error = prefill_error
        self.detected_codes = list(detected_codes or [])
        self.ocr_doc = ocr_doc
        self.source_image = QImage(source_image) if (source_image is not None and not source_image.isNull()) else None
        self.ocr_warning = str(ocr_warning or "").strip()
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self.trigger_translation)

        # 初始目标语言：外部指定 > 上次手动选择 > 内容智能判断（中文译英，其余译中）
        saved_target = str(load_settings().get("ocr_target_lang", "") or "").strip()
        valid_codes = {code for code, _label in SUPPORTED_LANGUAGES}
        if target_lang in valid_codes:
            self.initial_target = target_lang
        elif saved_target in valid_codes:
            self.initial_target = saved_target
        else:
            has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in (text or ""))
            self.initial_target = "en" if has_chinese else "zh-CN"

        self._setup_ui(text or "")
        self._apply_style()

        if prefill_translation is not None:
            # 一键翻译入口已经在后台完成翻译，这里仅负责展示，避免重复请求
            self.target_edit.setPlainText(prefill_translation)
            if prefill_error:
                self._set_status("翻译失败", "err")
                self.target_edit.setPlaceholderText(f"翻译失败: {prefill_error}")
            else:
                use_ai = self.combo_mode.currentData() == "ai"
                self._set_status("AI 翻译完成" if use_ai else "翻译完成", "ok")
        elif (text or "").strip():
            if self.ocr_warning:
                self._set_status(self.ocr_warning, "warn")
            # 普通识别入口：弹窗打开后立即发起初次自动翻译
            QTimer.singleShot(80, self.trigger_translation)
        else:
            self._set_status(self.ocr_warning or "已解码二维码/条码", "warn" if self.ocr_warning else "ok")

    def _set_status(self, text, tone="muted"):
        if not text:
            self.status_lbl.setVisible(False)
            self.status_lbl.setText("")
            return
        palettes = {
            "info":  ("#FBF7EE", "#7A5110", "#E5D2AE"),
            "ok":    ("#F0FDF4", "#15803D", "#BBF7D0"),
            "warn":  ("#FFFBEB", "#B45309", "#FDE68A"),
            "err":   ("#FEF2F2", "#DC2626", "#FECACA"),
            "muted": ("#F4F4F5", "#52525B", "#E4E4E7"),
        }
        bg, fg, bd = palettes.get(tone, palettes["muted"])
        self.status_lbl.setVisible(True)
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(
            f"QLabel#statusLabel {{ background-color: {bg}; color: {fg}; border: 1px solid {bd}; "
            f"border-radius: 6px; padding: 3px 10px; font-size: 11.5px; font-weight: 600; }}"
        )

    def _setup_ui(self, initial_text):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._build_top_bar(initial_text))
        main_layout.addWidget(self._build_editor_body(initial_text), 1)
        main_layout.addWidget(self._build_bottom_bar())

    def _build_top_bar(self, initial_text):
        top_bar = QWidget()
        top_bar.setObjectName("codexTopBar")
        top_lay = QVBoxLayout(top_bar)
        top_lay.setContentsMargins(20, 14, 20, 12)
        top_lay.setSpacing(10)

        row1 = QHBoxLayout()
        row1.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_create_codex_icon("ai", "#0D0D0D", 16).pixmap(16, 16))
        icon_lbl.setFixedSize(18, 18)

        title_lbl = QLabel("文字识别与翻译")
        title_lbl.setObjectName("codexTitle")

        self.lbl_src_detected = QLabel("源语言: 自动检测")
        self.lbl_src_detected.setObjectName("codexPill")

        self.lbl_char_count = QLabel(f"{len(initial_text.strip())} 字符")
        self.lbl_char_count.setObjectName("codexPill")

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("statusLabel")
        self.status_lbl.setVisible(False)

        row1.addWidget(icon_lbl)
        row1.addWidget(title_lbl)
        row1.addSpacing(4)
        row1.addWidget(self.lbl_src_detected)
        row1.addWidget(self.lbl_char_count)
        row1.addStretch()
        row1.addWidget(self.status_lbl)
        top_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        _saved = load_settings()

        lbl_ocr_mode = QLabel("识别引擎")
        lbl_ocr_mode.setObjectName("ctrlLabel")
        self.combo_ocr_mode = CodexComboBox()
        self.combo_ocr_mode.addItem("OCR 引擎", "ocr")
        self.combo_ocr_mode.addItem("AI 识别", "ai_ocr")
        if _saved.get("use_ai_ocr", False):
            self.combo_ocr_mode.setCurrentIndex(1)
        self.combo_ocr_mode.setMinimumWidth(116)
        self.combo_ocr_mode.currentIndexChanged.connect(self._on_ocr_mode_changed)

        self.btn_reocr = QPushButton("重新识别")
        self.btn_reocr.setObjectName("codexSecondaryBtn")
        self.btn_reocr.setCursor(Qt.PointingHandCursor)
        self.btn_reocr.setToolTip("使用当前所选识别引擎对原截图重新执行 OCR")
        self.btn_reocr.setVisible(self.source_image is not None)
        self.btn_reocr.clicked.connect(self.trigger_reocr)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setObjectName("sepLine")
        sep1.setFixedHeight(16)

        lbl_mode = QLabel("翻译方式")
        lbl_mode.setObjectName("ctrlLabel")
        self.combo_mode = CodexComboBox()
        self.combo_mode.addItem("普通翻译", "normal")
        self.combo_mode.addItem("AI 翻译", "ai")
        if _saved.get("use_ai_translation", False):
            self.combo_mode.setCurrentIndex(1)
        self.combo_mode.setMinimumWidth(116)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setObjectName("sepLine")
        sep2.setFixedHeight(16)

        lbl_target = QLabel("目标语言")
        lbl_target.setObjectName("ctrlLabel")
        self.combo_target = CodexComboBox()
        for code, label in SUPPORTED_LANGUAGES:
            self.combo_target.addItem(label, code)
        idx = self.combo_target.findData(self.initial_target)
        if idx >= 0:
            self.combo_target.setCurrentIndex(idx)
        self.combo_target.setMinimumWidth(134)
        self.combo_target.currentIndexChanged.connect(self._on_target_changed)

        self.btn_translate = QPushButton("立即翻译")
        self.btn_translate.setObjectName("codexPrimaryBtn")
        self.btn_translate.setIcon(_create_codex_icon("update", "#FFFFFF", 13))
        self.btn_translate.setIconSize(QSize(13, 13))
        self.btn_translate.setCursor(Qt.PointingHandCursor)
        self.btn_translate.clicked.connect(self.trigger_translation)

        row2.addWidget(lbl_ocr_mode)
        row2.addWidget(self.combo_ocr_mode)
        row2.addWidget(self.btn_reocr)
        row2.addWidget(sep1)
        row2.addWidget(lbl_mode)
        row2.addWidget(self.combo_mode)
        row2.addWidget(sep2)
        row2.addWidget(lbl_target)
        row2.addWidget(self.combo_target)
        row2.addStretch()
        row2.addWidget(self.btn_translate)
        top_lay.addLayout(row2)
        return top_bar

    def _build_editor_body(self, initial_text):
        body_wrap = QWidget()
        body_wrap.setObjectName("codexBody")
        body_lay = QVBoxLayout(body_wrap)
        body_lay.setContentsMargins(20, 16, 20, 16)
        body_lay.setSpacing(12)

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(14)

        left_card = QFrame()
        left_card.setObjectName("codexCard")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        left_header_w = QWidget()
        left_header_w.setObjectName("cardHeader")
        left_header = QHBoxLayout(left_header_w)
        left_header.setContentsMargins(14, 9, 10, 9)
        left_header.setSpacing(8)
        left_title = QLabel("原文")
        left_title.setObjectName("cardTitle")
        left_hint = QLabel("支持直接编辑")
        left_hint.setObjectName("cardHint")
        self.btn_copy_source = QPushButton("复制原文")
        self.btn_copy_source.setObjectName("codexGhostBtn")
        self.btn_copy_source.setCursor(Qt.PointingHandCursor)
        self.btn_copy_source.clicked.connect(self.copy_source)
        self.btn_copy_table = QPushButton("复制为表格 (Excel)")
        self.btn_copy_table.setObjectName("codexGhostBtn")
        self.btn_copy_table.setCursor(Qt.PointingHandCursor)
        self.btn_copy_table.setToolTip("按识别坐标或分隔符重建行列（制表符 TSV），可直接粘贴进 Excel / WPS 分列")
        self.btn_copy_table.clicked.connect(self.copy_as_table)
        left_header.addWidget(left_title)
        left_header.addWidget(left_hint)
        left_header.addStretch()
        left_header.addWidget(self.btn_copy_table)
        left_header.addWidget(self.btn_copy_source)
        left_layout.addWidget(left_header_w)

        self.source_edit = QTextEdit()
        self.source_edit.setObjectName("editorText")
        self.source_edit.setPlainText(initial_text)
        if not initial_text.strip() and self.detected_codes:
            self.source_edit.setPlaceholderText("（画面未包含正文文本，上方已提取出二维码 / 条形码结果）")
        self.source_edit.textChanged.connect(self._on_source_text_changed)
        left_layout.addWidget(self.source_edit, 1)

        right_card = QFrame()
        right_card.setObjectName("codexCard")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        right_header_w = QWidget()
        right_header_w.setObjectName("cardHeader")
        right_header = QHBoxLayout(right_header_w)
        right_header.setContentsMargins(14, 9, 10, 9)
        right_header.setSpacing(8)
        right_title = QLabel("译文")
        right_title.setObjectName("cardTitle")
        self.btn_copy_target = QPushButton("复制译文")
        self.btn_copy_target.setObjectName("codexGhostBtn")
        self.btn_copy_target.setCursor(Qt.PointingHandCursor)
        self.btn_copy_target.clicked.connect(self.copy_target)
        right_header.addWidget(right_title)
        right_header.addStretch()
        right_header.addWidget(self.btn_copy_target)
        right_layout.addWidget(right_header_w)

        self.target_edit = QTextEdit()
        self.target_edit.setObjectName("editorText")
        self.target_edit.setPlaceholderText("等待翻译...")
        right_layout.addWidget(self.target_edit, 1)

        if self.detected_codes:
            body_lay.addWidget(self._build_codes_card())

        cards_layout.addWidget(left_card, 1)
        cards_layout.addWidget(right_card, 1)
        body_lay.addLayout(cards_layout, 1)
        return body_wrap

    def _build_codes_card(self):
        codes_card = QFrame()
        codes_card.setObjectName("codesCard")
        codes_card.setStyleSheet(
            "QFrame#codesCard { background-color: #FFFFFF; border: 1px solid #E5D2AE; border-radius: 10px; }"
        )
        codes_lay = QVBoxLayout(codes_card)
        codes_lay.setContentsMargins(14, 10, 14, 10)
        codes_lay.setSpacing(6)
        for code_info in self.detected_codes[:5]:
            c_row = QHBoxLayout()
            c_row.setSpacing(8)
            badge = QLabel(code_info.get("type", "CODE"))
            badge.setStyleSheet(
                "background-color: #FBF7EE; color: #7A5110; border: 1px solid #E5D2AE; "
                "border-radius: 5px; padding: 2px 7px; font-size: 11px; font-weight: 600;"
            )
            val_txt = code_info.get("text", "")
            val_edit = QLineEdit(val_txt)
            val_edit.setReadOnly(True)
            val_edit.setStyleSheet(
                f"QLineEdit {{ font-family: {CODEX_MONO_FAMILY}; font-size: 12px; color: #0F172A; "
                "background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 3px 8px; }}"
            )
            btn_copy_code = QPushButton("复制")
            btn_copy_code.setObjectName("codexGhostBtn")
            btn_copy_code.setCursor(Qt.PointingHandCursor)

            def _copy_this(_=False, t=val_txt, b=btn_copy_code):
                QApplication.clipboard().setText(t)
                b.setText("已复制")
                QTimer.singleShot(1400, lambda: b.setText("复制"))

            btn_copy_code.clicked.connect(_copy_this)
            c_row.addWidget(badge)
            c_row.addWidget(val_edit, 1)
            c_row.addWidget(btn_copy_code)
            if code_info.get("is_url"):
                btn_open = QPushButton("打开链接")
                btn_open.setObjectName("codexGhostBtn")
                btn_open.setCursor(Qt.PointingHandCursor)
                btn_open.clicked.connect(lambda _=False, u=val_txt: webbrowser.open(u))
                c_row.addWidget(btn_open)
            codes_lay.addLayout(c_row)
        return codes_card

    def _build_bottom_bar(self):
        bottom_bar_w = QWidget()
        bottom_bar_w.setObjectName("codexBottomBar")
        bottom_bar = QHBoxLayout(bottom_bar_w)
        bottom_bar.setContentsMargins(20, 11, 20, 11)
        bottom_bar.setSpacing(10)

        self.btn_copy_both = QPushButton("复制双语对照")
        self.btn_copy_both.setObjectName("codexSecondaryBtn")
        self.btn_copy_both.setCursor(Qt.PointingHandCursor)
        self.btn_copy_both.clicked.connect(self.copy_bilingual)

        self.btn_primary_copy = QPushButton("复制译文")
        self.btn_primary_copy.setObjectName("codexPrimaryBtn")
        self.btn_primary_copy.setCursor(Qt.PointingHandCursor)
        self.btn_primary_copy.clicked.connect(self.copy_target)

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("codexSecondaryBtn")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.accept)

        bottom_bar.addWidget(self.btn_copy_both)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.btn_primary_copy)
        bottom_bar.addWidget(self.btn_close)
        return bottom_bar_w

    def _apply_style(self):
        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + """
            QWidget#codexBody {
                background-color: #FCFCFD;
            }
            QLabel#ctrlLabel {
                font-size: 12px;
                font-weight: 400;
                color: #71717A;
                background: transparent;
            }
            QFrame#sepLine {
                background-color: #E4E4E7;
                max-width: 1px;
                min-width: 1px;
                border: none;
                margin: 0 4px;
            }
            QWidget#cardHeader {
                background-color: #FAFAFA;
                border-bottom: 1px solid #F1F1F5;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QLabel#cardTitle {
                font-size: 12.5px;
                font-weight: 600;
                color: #0D0D0D;
                background: transparent;
            }
            QLabel#cardHint {
                font-size: 11px;
                color: #A1A1AA;
                background: transparent;
            }
            QTextEdit#editorText {
                background-color: #FFFFFF;
                color: #18181B;
                border: none;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                padding: 12px 14px;
                font-family: """ + CODEX_FONT_FAMILY + """;
                font-size: 13.5px;
                selection-background-color: #0D0D0D;
                selection-color: #FFFFFF;
            }
        """)

    def _on_source_text_changed(self):
        text = self.source_edit.toPlainText().strip()
        self.lbl_char_count.setText(f"{len(text)} 字符")
        self.debounce_timer.start(800)

    def _on_mode_changed(self):
        """翻译模式切换时保存设置并自动重新翻译"""
        use_ai = self.combo_mode.currentData() == "ai"
        save_settings({"use_ai_translation": use_ai})
        self.trigger_translation()

    def _on_target_changed(self):
        """目标语言修改后持久化记忆，后续所有 OCR 入口默认沿用"""
        code = self.combo_target.currentData()
        if code:
            save_settings({"ocr_target_lang": code})
        self.trigger_translation()

    def _on_ocr_mode_changed(self):
        """识别模式切换时保存设置，若持有原图则立即用新引擎重新识别"""
        use_ai_ocr = self.combo_ocr_mode.currentData() == "ai_ocr"
        save_settings({"use_ai_ocr": use_ai_ocr})
        if self.source_image is not None:
            self.trigger_reocr()

    def trigger_reocr(self):
        """使用当前选择的识别引擎对原截图重新识别并刷新结果"""
        if self.source_image is None or self.source_image.isNull() or self._dialog_closed:
            return
        use_ai_ocr = self.combo_ocr_mode.currentData() == "ai_ocr"
        self.debounce_timer.stop()

        self.btn_reocr.setEnabled(False)
        self.combo_ocr_mode.setEnabled(False)
        self._set_status("AI 正在重新识别..." if use_ai_ocr else "正在重新识别...", "info")

        worker = OcrWorker(self.source_image, use_ai=use_ai_ocr)
        _DETACHED_OCR_WORKERS.add(worker)
        self._reocr_worker = worker

        def _cleanup_worker(*_args, _w=worker):
            _DETACHED_OCR_WORKERS.discard(_w)
            try:
                _w.deleteLater()
            except RuntimeError:
                pass

        def _on_done(new_text, _w=worker):
            if self._dialog_closed or self._reocr_worker is not _w:
                return
            self._reocr_worker = None
            self.btn_reocr.setEnabled(True)
            self.combo_ocr_mode.setEnabled(True)
            self.ocr_doc = getattr(_w, "ocr_doc", None)
            warn = str(getattr(_w, "ocr_warning", "") or "").strip()
            self.ocr_warning = warn
            clean = (new_text or "").strip()
            if clean.startswith("识别发生错误:"):
                self._set_status("重新识别失败", "err")
                QMessageBox.warning(self, "重新识别失败", clean)
                return
            self.source_edit.blockSignals(True)
            self.source_edit.setPlainText(clean)
            self.source_edit.blockSignals(False)
            self.lbl_char_count.setText(f"{len(clean)} 字符")
            if clean:
                self.trigger_translation()
                if warn:
                    self._set_status(warn, "warn")
            else:
                self.target_edit.clear()
                self._set_status(warn or "未识别到文字", "warn")

        worker.finished.connect(_on_done)
        worker.finished.connect(_cleanup_worker)
        worker.start()

    def _cleanup_on_close(self):
        self._dialog_closed = True
        self.debounce_timer.stop()
        if self.current_worker and self.current_worker.isRunning():
            try:
                self.current_worker.cancel()
                self.current_worker.finished.disconnect()
                self.current_worker.error.disconnect()
            except Exception:
                pass
        self._reocr_worker = None

    def done(self, r):
        self._cleanup_on_close()
        super().done(r)

    def closeEvent(self, event):
        self._cleanup_on_close()
        super().closeEvent(event)

    def trigger_translation(self):
        text = self.source_edit.toPlainText().strip()
        if not text:
            self.target_edit.clear()
            self._set_status("")
            return

        target_lang = self.combo_target.currentData()
        use_ai = self.combo_mode.currentData() == "ai"

        self._set_status("AI 正在翻译..." if use_ai else "正在翻译...", "info")
        self.btn_translate.setEnabled(False)

        if self.current_worker and self.current_worker.isRunning():
            try:
                self.current_worker.cancel()
                self.current_worker.finished.disconnect()
                self.current_worker.error.disconnect()
            except Exception:
                pass

        self.current_worker = TranslationWorker(text, target_lang, use_ai=use_ai)
        self.current_worker.finished.connect(self._on_translation_finished)
        self.current_worker.error.connect(self._on_translation_error)
        self.current_worker.start()

    def _on_translation_finished(self, result, detected_src):
        self.btn_translate.setEnabled(True)
        self.target_edit.setPlainText(result)
        use_ai = self.combo_mode.currentData() == "ai"
        if self.ocr_warning:
            self._set_status(self.ocr_warning, "warn")
        else:
            self._set_status("AI 翻译完成" if use_ai else "翻译完成", "ok")

        # 更新识别到的源语言标签
        lang_name = LANG_NAME_MAP.get(detected_src, detected_src)
        self.lbl_src_detected.setText(f"源语言: {lang_name}")

    def _on_translation_error(self, err):
        self.btn_translate.setEnabled(True)
        use_ai = self.combo_mode.currentData() == "ai"
        self._set_status("AI 翻译失败" if use_ai else "翻译失败", "err")
        self.target_edit.setPlaceholderText(f"翻译失败: {err}")

    def copy_source(self):
        src = self.source_edit.toPlainText().strip()
        if src:
            QApplication.clipboard().setText(src)
            self.btn_copy_source.setText("已复制")
            QTimer.singleShot(1500, lambda: self.btn_copy_source.setText("复制原文"))

    def copy_as_table(self):
        tsv = extract_table_tsv(self.ocr_doc, self.source_edit.toPlainText())
        if tsv.strip():
            QApplication.clipboard().setText(tsv)
            self.btn_copy_table.setText("已复制表格")
            QTimer.singleShot(1500, lambda: self.btn_copy_table.setText("复制为表格 (Excel)"))

    def copy_target(self):
        tgt = self.target_edit.toPlainText().strip()
        if tgt:
            QApplication.clipboard().setText(tgt)
            self.btn_copy_target.setText("已复制")
            self.btn_primary_copy.setText("已复制译文")
            QTimer.singleShot(1500, lambda: (
                self.btn_copy_target.setText("复制译文"),
                self.btn_primary_copy.setText("复制译文")
            ))

    def copy_bilingual(self):
        src = self.source_edit.toPlainText().strip()
        tgt = self.target_edit.toPlainText().strip()
        if not src and not tgt:
            return
        bilingual = f"{src}\n\n【译文】\n{tgt}"
        QApplication.clipboard().setText(bilingual)
        self.btn_copy_both.setText("双语已复制")
        QTimer.singleShot(1500, lambda: self.btn_copy_both.setText("复制双语对照"))

# ==========================================
# AI 文字识别 (智谱 AI GLM-OCR 专业多模态模型)
# ==========================================
def _image_to_base64(image: QImage):
    """将 QImage 转换为 base64 编码的 PNG 字符串（安全跨线程纯数据操作）"""
    img = image.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(img.byteCount())
    pil_img = Image.frombytes("RGB", (w, h), bytes(ptr), "raw", "RGB", img.bytesPerLine())
    ba = io.BytesIO()
    pil_img.save(ba, format="PNG", optimize=True)
    return base64.b64encode(ba.getvalue()).decode('utf-8')


def _image_to_data_uri(image: QImage, max_side=2400, png_byte_limit=1_600_000):
    """为多模态 AI OCR 准备最优图像 Data URI：极小图放大并留白，超大高分图适度压缩以提速防超时。"""
    img = image.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(img.byteCount())
    pil_img = Image.frombytes("RGB", (w, h), bytes(ptr), "raw", "RGB", img.bytesPerLine())
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    if min(w, h) < 40 and max(w, h) * 2 <= max_side:
        pil_img = pil_img.resize((max(1, w * 2), max(1, h * 2)), resample)
        arr_rgb = np.asarray(pil_img)
        padded = cv2.copyMakeBorder(arr_rgb, 12, 12, 12, 12, cv2.BORDER_REPLICATE)
        pil_img = Image.fromarray(padded, mode="RGB")
    elif max(w, h) > max_side:
        pil_img.thumbnail((max_side, max_side), resample)

    ba = io.BytesIO()
    pil_img.save(ba, format="PNG", optimize=True)
    raw = ba.getvalue()
    if len(raw) > png_byte_limit:
        jb = io.BytesIO()
        pil_img.save(jb, format="JPEG", quality=92, optimize=True)
        jraw = jb.getvalue()
        if len(jraw) < len(raw):
            return f"data:image/jpeg;base64,{base64.b64encode(jraw).decode('utf-8')}"
    return f"data:image/png;base64,{base64.b64encode(raw).decode('utf-8')}"


def ai_ocr_recognize(image: QImage, timeout=30):
    """使用智谱 AI GLM-OCR 专业多模态/文档识别模型解析文字"""
    api_key = get_zhipu_api_key()
    if not api_key:
        raise RuntimeError("未检测到智谱 API 密钥，请在系统设置中配置 识图 Token (bigmodel.cn)。")

    data_uri = _image_to_data_uri(image)
    model_name = load_settings().get("glm_ocr_model", "").strip() or DEFAULT_ZHIPU_OCR_MODEL

    payload = json.dumps({
        "model": model_name,
        "file": data_uri
    }).encode('utf-8')

    auth_header = api_key if api_key.startswith("Bearer ") else f"Bearer {api_key}"
    req = urllib.request.Request(
        ZHIPU_OCR_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": auth_header,
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            
            # 1. 优先读取 GLM-OCR 返回的标准 Markdown 格式解析结果
            if "md_results" in data and data["md_results"]:
                return str(data["md_results"]).strip()
            
            # 2. 兼容 chat 结构返回
            if "choices" in data and data["choices"]:
                choice = data["choices"][0].get("message", {})
                result = _clean_ai_output(choice)
                if result:
                    return result

            # 3. 兼容 layout_details 元素列表拼接
            if "layout_details" in data and isinstance(data["layout_details"], list):
                parts = []
                for item in data["layout_details"]:
                    if isinstance(item, list):
                        for sub in item:
                            c = sub.get("content", "")
                            if c: parts.append(str(c))
                    elif isinstance(item, dict):
                        c = item.get("content", "")
                        if c: parts.append(str(c))
                if parts:
                    return "\n".join(parts).strip()

            return ""
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace') if e.fp else ''
        logging.warning("智谱 GLM-OCR 请求失败 (HTTP %s): %s", e.code, body[:180])
        if e.code == 401:
            raise RuntimeError("智谱 API 密钥无效或未授权 (HTTP 401)，请在系统设置中检查您的识图 Token。")
        api_msg = ""
        try:
            err_json = json.loads(body)
            api_msg = err_json.get("error", {}).get("message") or err_json.get("msg") or ""
        except Exception:
            api_msg = ""
        raise RuntimeError(f"智谱 GLM-OCR 识别失败: {api_msg}" if api_msg else f"智谱 GLM-OCR 识别失败 (HTTP {e.code})。")
    except Exception as e:
        logging.warning("智谱 GLM-OCR 调用异常: %s", e)
        raise RuntimeError(f"智谱 GLM-OCR 请求失败: {e}")

# ==========================================
# OCR 版面数据 (保留文字块的几何坐标，供图上取字 / 译文覆盖使用)
# ==========================================
# ==========================================
# 二维码 / 条形码自动检测与表格 TSV 结构重建
# ==========================================
def _qimage_to_rgb_array(image: QImage):
    """将 QImage 安全转换为独立拥有的连续 RGB ndarray (H, W, 3)，绝不悬空引用临时 QImage 内存。"""
    if image is None or image.isNull():
        return None
    img_rgb = image if image.format() == QImage.Format_RGB888 else image.convertToFormat(QImage.Format_RGB888)
    width, height = img_rgb.width(), img_rgb.height()
    if width <= 0 or height <= 0:
        return None
    bpl = img_rgb.bytesPerLine()
    ptr = img_rgb.constBits()
    ptr.setsize(img_rgb.byteCount())
    raw = np.frombuffer(ptr, np.uint8).reshape((height, bpl))
    return np.array(raw[:, :width * 3].reshape((height, width, 3)), dtype=np.uint8, copy=True)


def detect_codes_in_image(img_source):
    """识别图像中的二维码与一维条形码（基于 OpenCV QRCodeDetector / BarcodeDetector）。"""
    arr_rgb = None
    if isinstance(img_source, QPixmap):
        if img_source.isNull():
            return []
        arr_rgb = _qimage_to_rgb_array(img_source.toImage())
    elif isinstance(img_source, QImage):
        arr_rgb = _qimage_to_rgb_array(img_source)
    elif isinstance(img_source, np.ndarray):
        if img_source.ndim == 3 and img_source.shape[2] >= 3:
            arr_rgb = np.ascontiguousarray(img_source[:, :, :3])
    if arr_rgb is None or arr_rgb.size == 0:
        return []

    bgr = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2BGR)
    results = []
    seen = set()

    def _add(code_type, text):
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        key = (code_type, cleaned)
        if key in seen:
            return
        seen.add(key)
        low = cleaned.lower()
        results.append({
            "type": code_type,
            "text": cleaned,
            "is_url": low.startswith(("http://", "https://")),
        })

    def _scan_bgr(mat):
        # 1. QR 码检测 (标准 + Aruco 补充低质量小码)
        try:
            qd = cv2.QRCodeDetector()
            ok, infos, _pts, _ = qd.detectAndDecodeMulti(mat)
            if ok and infos:
                for txt in infos:
                    if txt:
                        _add("QR 码", txt)
            else:
                txt, _p, _ = qd.detectAndDecode(mat)
                if txt:
                    _add("QR 码", txt)
        except Exception as e:
            logging.debug("QRCodeDetector 扫码异常: %s", e)

        if not results and hasattr(cv2, "QRCodeDetectorAruco"):
            try:
                qa = cv2.QRCodeDetectorAruco()
                ok, infos, _pts, _ = qa.detectAndDecodeMulti(mat)
                if ok and infos:
                    for txt in infos:
                        if txt:
                            _add("QR 码", txt)
            except Exception:
                pass

        # 2. 一维条形码检测 (EAN-13, EAN-8, UPC-A, Code-128 等)
        if hasattr(cv2, "barcode") and hasattr(cv2.barcode, "BarcodeDetector"):
            try:
                bd = cv2.barcode.BarcodeDetector()
                ok, infos, types, _pts = bd.detectAndDecodeWithType(mat)
                if ok and infos:
                    for idx_b, txt in enumerate(infos):
                        if txt:
                            tname = str(types[idx_b]).strip() if types and idx_b < len(types) else ""
                            _add(tname or "条形码", txt)
            except Exception:
                pass

    # 为紧贴边缘裁剪的二维码/条码增加安静区白边，大幅提高识别率
    padded = cv2.copyMakeBorder(bgr, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    _scan_bgr(padded)

    # 小尺寸条码放大重试一次
    if not results and min(bgr.shape[0], bgr.shape[1]) < 280:
        up = cv2.resize(padded, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _scan_bgr(up)

    return results


def extract_table_tsv(doc=None, fallback_text=""):
    """根据 OCR 版面坐标（或分隔符回退）将识别结果重建为制表符分隔 (TSV) 文本，可直接粘贴进 Excel / WPS。"""
    if doc is not None and getattr(doc, "rows", None):
        valid_rows = [r for r in doc.rows if r.text.strip() and r.rect.isValid()]
        if valid_rows:
            # 将垂直坐标在同一行高度范围内的单元格聚合为视觉表格行
            sorted_cells = sorted(valid_rows, key=lambda r: (r.rect.center().y(), r.rect.left()))
            visual_rows = []
            for cell in sorted_cells:
                cy = cell.rect.center().y()
                ch = max(1, cell.rect.height())
                placed = False
                for vr in visual_rows:
                    anchor_y = vr["cy"]
                    anchor_h = vr["h"]
                    if abs(cy - anchor_y) <= max(8.0, max(ch, anchor_h) * 0.65):
                        vr["cells"].append(cell)
                        vr["cy"] = sum(c.rect.center().y() for c in vr["cells"]) / len(vr["cells"])
                        vr["h"] = max(anchor_h, ch)
                        placed = True
                        break
                if not placed:
                    visual_rows.append({"cy": float(cy), "h": float(ch), "cells": [cell]})

            for vr in visual_rows:
                vr["cells"].sort(key=lambda c: c.rect.left())

            has_multi_col = any(len(vr["cells"]) >= 2 for vr in visual_rows)
            if has_multi_col:
                # 统计中位行高，用于聚齐每一列左边界 X 坐标（保留空白列对齐）
                heights = sorted(max(8, c.rect.height()) for c in valid_rows)
                med_h = heights[len(heights) // 2]
                col_tol = max(16.0, med_h * 1.1)
                multi_cells = [c for vr in visual_rows if len(vr["cells"]) >= 2 for c in vr["cells"]]
                x_buckets = []
                for left_x in sorted(c.rect.left() for c in multi_cells):
                    if not x_buckets or abs(left_x - x_buckets[-1]) > col_tol:
                        x_buckets.append(float(left_x))
                    else:
                        x_buckets[-1] = (x_buckets[-1] + left_x) / 2.0

                if len(x_buckets) >= 2:
                    tsv_lines = []
                    for vr in visual_rows:
                        row_vals = [""] * len(x_buckets)
                        for c in vr["cells"]:
                            # 归入最近列桶；若差距过大仍放到最接近列中
                            best_col = min(range(len(x_buckets)), key=lambda idx_b: abs(c.rect.left() - x_buckets[idx_b]))
                            row_vals[best_col] = (
                                f"{row_vals[best_col]} {c.text.strip()}".strip()
                                if row_vals[best_col] else c.text.strip()
                            )
                        while len(row_vals) > 1 and row_vals[-1] == "":
                            row_vals.pop()
                        tsv_lines.append("\t".join(row_vals))
                    return "\n".join(tsv_lines)

    # 回退方案：按多空格、制表符或竖线分割普通文本
    lines = []
    for raw_line in (fallback_text or "").splitlines():
        s = raw_line.strip()
        if not s:
            continue
        cols = [c.strip() for c in re.split(r'\t+|\s{2,}|\s*\|\s*', s) if c.strip()]
        lines.append("\t".join(cols) if cols else s)
    return "\n".join(lines)


class OcrRow:
    """一行文字：包含整行文本、包围盒，以及逐字符的近似矩形。"""

    __slots__ = ("text", "rect", "char_rects")

    def __init__(self, text, rect, char_rects):
        self.text = text
        self.rect = rect
        self.char_rects = char_rects


class OcrDoc:
    """一次 OCR 的完整版面结果，坐标以识别图像的左上角为原点。"""

    __slots__ = ("rows", "image_size", "text")

    def __init__(self, rows, image_size):
        self.rows = rows
        self.image_size = QSize(image_size)
        self.text = "\n".join(r.text for r in rows if r.text)

    def is_empty(self):
        return not self.rows or not self.text.strip()


def _ocr_item_bbox(points):
    """把 RapidOCR 的四点/多边形坐标转换为整数包围盒"""
    try:
        xs = [float(pt[0]) for pt in points]
        ys = [float(pt[1]) for pt in points]
        if not xs or not ys:
            return None
        left, top = int(math.floor(min(xs))), int(math.floor(min(ys)))
        right, bottom = int(math.ceil(max(xs))), int(math.ceil(max(ys)))
        if right - left < 2 or bottom - top < 2:
            return None
        return QRect(left, top, right - left, bottom - top)
    except Exception:
        return None


def _char_visual_weight(ch):
    """按字符视觉宽度估算权重，避免中英混排时等分字符框发生横向漂移。"""
    if not ch:
        return 1.0
    o = ord(ch)
    if (0x4E00 <= o <= 0x9FFF) or (0x3000 <= o <= 0x30FF) or (0xFF00 <= o <= 0xFFEF) or (0xAC00 <= o <= 0xD7AF):
        return 2.0
    if ch in "ilI1|.,:;'!` ":
        return 0.58
    if ch in "mwMW@#%&":
        return 1.25
    return 1.0


def _estimate_char_rects(rect, text):
    """按字符视觉权重为文本块估算逐字符包围盒（保证 len == len(text)）。"""
    if not text:
        return []
    weights = [_char_visual_weight(ch) for ch in text]
    total_w = sum(weights) or float(len(text))
    rects = []
    cur_x = float(rect.left())
    span_w = float(max(1, rect.width()))
    for idx, w in enumerate(weights):
        cw = span_w * (w / total_w)
        x0 = int(round(cur_x))
        x1 = rect.right() + 1 if idx == len(weights) - 1 else int(round(cur_x + cw))
        rects.append(QRect(x0, rect.top(), max(1, x1 - x0), rect.height()))
        cur_x += cw
    return rects


def _should_insert_space_between(prev_text, next_text, gap, min_h):
    """判断同行相邻两个识别词块之间是否应补充空格。"""
    if not prev_text or not next_text:
        return False
    prev_ch = prev_text[-1]
    next_ch = next_text[0]
    if prev_ch.isspace() or next_ch.isspace():
        return False
    if prev_ch.isascii() and prev_ch.isalnum() and next_ch.isascii() and next_ch.isalnum():
        return True
    if prev_ch in ",:;.)]}%?!" and next_ch.isascii() and (next_ch.isalnum() or next_ch in "([{\"'"):
        return True
    is_cjk_pair = ('\u4e00' <= prev_ch <= '\u9fff') and ('\u4e00' <= next_ch <= '\u9fff')
    if is_cjk_pair:
        return gap >= max(8.0, min_h * 0.85)
    return gap >= max(3.0, min_h * 0.30)


def build_ocr_doc(rapid_lines, image_size):
    """
    将 RapidOCR 的逐块结果整理为按阅读顺序排列的行：
    1. 先按纵向中心聚类成视觉行；
    2. 行内按横坐标排序，结合标点与间隙智能补空格；
    3. 按字符视觉权重估算矩形，用于鼠标框选与高亮。
    """
    if not rapid_lines:
        return None

    segments = []
    for item in rapid_lines:
        try:
            if not item or len(item) < 2:
                continue
            text = str(item[1]).strip()
            if not text:
                continue
            rect = _ocr_item_bbox(item[0])
            if rect is None:
                continue
            score = float(item[2]) if len(item) > 2 else 1.0
            word_spans = item[3] if len(item) > 3 and item[3] else None
            segments.append((rect, text, score, word_spans))
        except Exception:
            continue

    if not segments:
        return None

    segments.sort(key=lambda s: (s[0].center().y(), s[0].left()))

    # 纵向聚类成行：跟踪行内平均 Y 中心与平均高度，避免首个词块偏大导致整行漂移
    grouped = []
    for seg in segments:
        rect = seg[0]
        placed = False
        for row in grouped:
            row_rect = row["rect"]
            anchor_y = row["cy"]
            anchor_h = row["h"]
            h_min = max(1, min(row_rect.height(), rect.height()))
            overlap = min(row_rect.bottom(), rect.bottom()) - max(row_rect.top(), rect.top())
            center_delta = abs(rect.center().y() - anchor_y)
            same_row = (center_delta <= max(6.0, max(anchor_h, rect.height()) * 0.65)
                        or (overlap >= h_min * 0.55 and center_delta <= max(anchor_h, rect.height()) * 1.15))
            if same_row:
                row["items"].append(seg)
                row["rect"] = row_rect.united(rect)
                n = len(row["items"])
                row["cy"] = (anchor_y * (n - 1) + rect.center().y()) / n
                row["h"] = (anchor_h * (n - 1) + rect.height()) / n
                placed = True
                break
        if not placed:
            grouped.append({
                "rect": QRect(rect),
                "items": [seg],
                "cy": float(rect.center().y()),
                "h": float(rect.height()),
            })

    grouped.sort(key=lambda r: (r["rect"].top(), r["rect"].left()))

    rows = []
    for group in grouped:
        items = sorted(group["items"], key=lambda s: s[0].left())

        # 同一视觉行内按行高自适应横向间隙分块：小字表格/多栏不会被硬编码 56px 强行拼成一条
        clusters = []
        for item in items:
            rect = item[0]
            if clusters:
                prev_rect = clusters[-1][-1][0]
                gap = rect.left() - prev_rect.right()
                min_h = max(4.0, float(min(rect.height(), prev_rect.height())))
                gap_limit = max(14.0, min_h * 2.2)
                if gap <= gap_limit:
                    clusters[-1].append(item)
                    continue
            clusters.append([item])

        for cluster in clusters:
            parts = []
            char_rects = []
            prev_rect = None
            prev_text = ""
            for rect, text, _score, word_spans in cluster:
                if prev_rect is not None and prev_text and text:
                    gap = rect.left() - prev_rect.right()
                    min_h = max(4.0, float(min(rect.height(), prev_rect.height())))
                    if _should_insert_space_between(prev_text, text, gap, min_h):
                        gap_left = prev_rect.right() + 1
                        gap_right = rect.left() - 1
                        if gap_right - gap_left >= 2:
                            space_rect = QRect(gap_left, min(prev_rect.top(), rect.top()),
                                               gap_right - gap_left, max(prev_rect.height(), rect.height()))
                        else:
                            space_rect = QRect(rect.left() - 2, rect.top(), 2, rect.height())
                        parts.append(" ")
                        char_rects.append(space_rect)

                used_spans = False
                if word_spans:
                    span_parts = []
                    span_rects = []
                    prev_w_rect = None
                    for w_rect, w_txt in word_spans:
                        wt = str(w_txt or "").strip()
                        if not wt or w_rect is None:
                            continue
                        if span_parts and (span_parts[-1].isascii() or wt[0].isascii()):
                            sp_left = (prev_w_rect.right() + 1) if prev_w_rect is not None else w_rect.left() - 2
                            sp_w = max(2, w_rect.left() - sp_left)
                            span_parts.append(" ")
                            span_rects.append(QRect(sp_left, w_rect.top(), sp_w, w_rect.height()))
                        span_parts.extend(wt)
                        span_rects.extend(_estimate_char_rects(w_rect, wt))
                        prev_w_rect = w_rect
                    if "".join(span_parts).strip() == text and len(span_rects) == len(text):
                        parts.extend(text)
                        char_rects.extend(span_rects)
                        used_spans = True

                if not used_spans:
                    parts.extend(text)
                    char_rects.extend(_estimate_char_rects(rect, text))
                prev_rect = rect
                prev_text = text

            row_text = "".join(parts).strip()
            if not row_text:
                continue
            cluster_rect = QRect(cluster[0][0])
            for item in cluster[1:]:
                cluster_rect = cluster_rect.united(item[0])
            rows.append(OcrRow(row_text, cluster_rect, char_rects))

    if not rows:
        return None
    return OcrDoc(rows, image_size)


def ocr_doc_char_at(doc, lx, ly):
    """在图像坐标系中定位光标所在字符索引 (row_idx, char_idx)。"""
    if doc is None or doc.is_empty():
        return None
    pt = QPoint(int(lx), int(ly))
    for ri, row in enumerate(doc.rows):
        if ly < row.rect.top() - 3 or ly > row.rect.bottom() + 3:
            continue
        for ci, cr in enumerate(row.char_rects):
            if cr.adjusted(-2, -2, 2, 2).contains(pt):
                return (ri, ci)
    return None


def ocr_doc_column_rows(doc, col_rect, target_lx=None, target_ly=None):
    """按列聚类范围锁定当前栏，兼顾多栏与窄标题接宽段落；拖过栏隙到相邻列文字时自动放开跨栏选取。"""
    if doc is None or not doc.rows:
        return []
    rows = doc.rows
    if col_rect is None or col_rect.isNull():
        return list(range(len(rows)))

    img_w = doc.image_size.width() or col_rect.right()
    col_h = max(8, col_rect.height())
    gutter = max(20, int(col_h * 1.4))

    hard_left = 0
    hard_right = img_w - 1
    for r in rows:
        if r.rect == col_rect:
            continue
        v_ov = min(r.rect.bottom(), col_rect.bottom()) - max(r.rect.top(), col_rect.top())
        if v_ov >= min(r.rect.height(), col_h) * 0.35:
            if r.rect.right() < col_rect.left() - gutter:
                hard_left = max(hard_left, r.rect.right() + 2)
            elif r.rect.left() > col_rect.right() + gutter:
                hard_right = min(hard_right, r.rect.left() - 2)

    col_left = max(hard_left, col_rect.left())
    col_right = min(hard_right, col_rect.right())
    changed = True
    while changed:
        changed = False
        for r in rows:
            if r.rect.left() < hard_left - gutter * 0.5 or r.rect.right() > hard_right + gutter * 0.5:
                continue
            ov = min(col_right, r.rect.right()) - max(col_left, r.rect.left())
            min_w = max(1, min(col_right - col_left + 1, r.rect.width()))
            if ov >= min_w * 0.45:
                nl = max(hard_left, min(col_left, r.rect.left()))
                nr = min(hard_right, max(col_right, r.rect.right()))
                if nl < col_left or nr > col_right:
                    col_left, col_right = nl, nr
                    changed = True

    if target_lx is not None and (target_lx < col_left - gutter or target_lx > col_right + gutter):
        if target_ly is not None:
            for r in rows:
                if r.rect.top() - col_h <= target_ly <= r.rect.bottom() + col_h:
                    if r.rect.left() - 8 <= target_lx <= r.rect.right() + 8:
                        return list(range(len(rows)))

    result = []
    for ri, row in enumerate(rows):
        left = max(col_left, row.rect.left())
        right = min(col_right, row.rect.right())
        overlap = max(0, right - left + 1)
        min_w = min(max(1, col_right - col_left + 1), row.rect.width())
        if min_w > 0 and overlap / min_w >= 0.35:
            result.append(ri)
    return result or list(range(len(rows)))


def ocr_doc_flat(doc, row_indices=None):
    if doc is None:
        return []
    rows = doc.rows
    if row_indices is None:
        row_indices = range(len(rows))
    return [(ri, ci) for ri in row_indices for ci in range(len(rows[ri].text))]


def ocr_doc_index_at(doc, lx, ly, row_indices=None):
    """将图像坐标映射为阅读顺序中的字符索引。"""
    if doc is None or doc.is_empty():
        return None
    rows = doc.rows
    candidates = list(row_indices) if row_indices is not None else list(range(len(rows)))
    if not candidates:
        candidates = list(range(len(rows)))

    def vdist(row):
        if ly < row.rect.top():
            return row.rect.top() - ly
        if ly > row.rect.bottom():
            return ly - row.rect.bottom()
        return 0

    def hdist(row):
        if lx < row.rect.left():
            return row.rect.left() - lx
        if lx > row.rect.right():
            return lx - row.rect.right()
        return 0

    ri = min(candidates, key=lambda i: (vdist(rows[i]), hdist(rows[i])))
    row = rows[ri]
    for ci, cr in enumerate(row.char_rects):
        if cr.center().x() >= lx:
            return (ri, ci)
    return (ri, max(0, len(row.char_rects) - 1))


def ocr_doc_selected_text(doc, selected_set):
    """按视觉行聚合选中文本：同行多列词块用制表符/空格连接，跨视觉行用换行连接。"""
    if not selected_set or doc is None:
        return ""
    picked = []
    for ri, row in enumerate(doc.rows):
        idxs = sorted(ci for (r, ci) in selected_set if r == ri and ci < len(row.text))
        if not idxs:
            continue
        txt = "".join(row.text[ci] for ci in idxs).rstrip()
        if not txt:
            continue
        if idxs[0] < len(row.char_rects) and idxs[-1] < len(row.char_rects):
            seg_rect = row.char_rects[idxs[0]].united(row.char_rects[idxs[-1]])
        else:
            seg_rect = row.rect
        picked.append((seg_rect, txt))
    if not picked:
        return ""

    visual_lines = []
    for rect, txt in picked:
        cy = rect.center().y()
        ch = max(1, rect.height())
        if visual_lines:
            last_line = visual_lines[-1]
            anchor_y = last_line["cy"]
            anchor_h = last_line["h"]
            if abs(cy - anchor_y) <= max(6.0, max(ch, anchor_h) * 0.55):
                last_line["items"].append((rect, txt))
                n = len(last_line["items"])
                last_line["cy"] = (anchor_y * (n - 1) + cy) / n
                last_line["h"] = max(anchor_h, ch)
                continue
        visual_lines.append({"cy": float(cy), "h": float(ch), "items": [(rect, txt)]})

    out_lines = []
    for vline in visual_lines:
        items = sorted(vline["items"], key=lambda it: it[0].left())
        parts = [items[0][1]]
        prev_r = items[0][0]
        for r, t in items[1:]:
            gap = r.left() - prev_r.right()
            sep = "\t" if gap >= max(14.0, min(prev_r.height(), r.height()) * 1.4) else " "
            parts.append(sep + t)
            prev_r = r
        out_lines.append("".join(parts).rstrip())
    return "\n".join(out_lines).strip()


def ocr_doc_selected_subdoc(doc, selected_set):
    """构建仅包含选中文字的子 OcrDoc，供表格 TSV 提取或局部翻译使用。"""
    if not selected_set or doc is None:
        return None
    sub_rows = []
    for ri, row in enumerate(doc.rows):
        idxs = sorted(ci for (r, ci) in selected_set
                      if r == ri and ci < len(row.text) and ci < len(row.char_rects))
        if not idxs:
            continue
        txt = "".join(row.text[ci] for ci in idxs).strip()
        if not txt:
            continue
        c_rects = [QRect(row.char_rects[ci]) for ci in idxs]
        rect = QRect(c_rects[0])
        for cr in c_rects[1:]:
            rect = rect.united(cr)
        sub_rows.append(OcrRow(txt, rect, c_rects))
    if not sub_rows:
        return None
    return OcrDoc(sub_rows, doc.image_size)


def ocr_doc_draw_selection(painter, doc, selected_set, hover_idx, map_rect_fn):
    """在画布/贴图上绘制连续字符选区高亮与悬停下划线。"""
    if doc is None or doc.is_empty():
        return
    if selected_set:
        fill = QColor(196, 146, 62, 78)
        border = QColor(148, 102, 32, 95)
        painter.setPen(QPen(border, 1))
        for ri, row in enumerate(doc.rows):
            idxs = sorted(ci for (r, ci) in selected_set
                          if r == ri and ci < len(row.char_rects))
            if not idxs:
                continue
            runs = []
            start = prev = idxs[0]
            for ci in idxs[1:]:
                if ci == prev + 1:
                    prev = ci
                else:
                    runs.append((start, prev))
                    start = prev = ci
            runs.append((start, prev))
            for a, b in runs:
                ra = map_rect_fn(row.char_rects[a])
                rb = map_rect_fn(row.char_rects[b])
                rect = QRect(ra.left(), min(ra.top(), rb.top()),
                             max(1, rb.right() - ra.left() + 1), max(ra.height(), rb.height()))
                painter.fillRect(rect, fill)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
    elif hover_idx is not None:
        try:
            ri, ci = hover_idx
            rect = map_rect_fn(doc.rows[ri].char_rects[ci])
            painter.fillRect(QRect(rect.left(), rect.bottom() - 2, rect.width(), 2),
                             QColor(182, 134, 58, 200))
        except (IndexError, KeyError):
            pass


def _border_ring_median(img_arr):
    """采样图像最外圈 2px 边界的中位颜色/亮度，用于稳定判断深浅背景与边缘留白填充。"""
    if img_arr is None or img_arr.size == 0:
        return 255 if (img_arr is not None and img_arr.ndim == 2) else (255, 255, 255)
    h, w = img_arr.shape[:2]
    b = min(2, h, w)
    strips = [img_arr[:b, :], img_arr[h - b:, :], img_arr[:, :b], img_arr[:, w - b:]]
    if img_arr.ndim == 2:
        vals = np.concatenate([s.reshape(-1) for s in strips])
        return int(np.median(vals))
    vals = np.concatenate([s.reshape(-1, img_arr.shape[2]) for s in strips], axis=0)
    med = np.median(vals, axis=0)
    return tuple(int(v) for v in med[:3])


def _prepare_gray_for_tesseract(image_rgb):
    """共享 Tesseract 灰度预处理：深底彩字用最大色差映射提纯对比度、小中字放大与留白。"""
    bg_rgb = _border_ring_median(image_rgb)
    bg_luma = 0.299 * bg_rgb[0] + 0.587 * bg_rgb[1] + 0.114 * bg_rgb[2]
    if bg_luma < 115:
        # 深底彩色终端字在灰度转换下容易对比度骤降，按与深色底的最大通道色差提取黑字白底
        bg_vec = np.array(bg_rgb, dtype=np.int16).reshape(1, 1, 3)
        diff = np.max(np.abs(image_rgb.astype(np.int16) - bg_vec), axis=2)
        gray = np.clip(255 - diff, 0, 255).astype(np.uint8)
    else:
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)

    h_img, w_img = gray.shape
    scale = 2.5 if h_img < 160 else (2.0 if max(h_img, w_img) < 1400 else 1.0)
    if scale > 1.0 and max(h_img, w_img) * scale > 4200:
        scale = max(1.0, 4200.0 / max(1, h_img, w_img))
    if scale > 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # 仅在前景文字相对背景确实偏淡时启用 CLAHE，避免在干净深色终端高对比字上制造振铃噪点
    fg_contrast = float(np.percentile(np.abs(gray.astype(np.int16) - float(np.median(gray))), 98))
    if fg_contrast < 65.0:
        clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
        processed = clahe.apply(gray)
    else:
        processed = gray
    processed = cv2.normalize(processed, None, 0, 255, cv2.NORM_MINMAX)

    pad = 16
    edge_val = _border_ring_median(processed)
    canvas = cv2.copyMakeBorder(processed, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=edge_val)
    return canvas, scale, pad


def tesseract_ocr_lines(image_rgb, lang_str=None, min_conf=5, allow_fallback_langs=False):
    """
    使用 Tesseract image_to_data 获取词级 bbox 与置信度。
    默认仅用中英 (AUTO_OCR_LANGS)，避免泰语/菲律宾语包误认污染正常中文结果。
    """
    requested_lang = lang_str or AUTO_OCR_LANGS
    try:
        canvas, scale, pad = _prepare_gray_for_tesseract(image_rgb)
        pil_canvas = Image.fromarray(canvas, mode='L')
        data = pytesseract.image_to_data(
            pil_canvas, lang=requested_lang, config='--oem 1 --psm 3',
            output_type=pytesseract.Output.DICT
        )
        has_words = any(str(t).strip() for t in (data.get('text') or []))
        if not has_words:
            # 稀疏单块/单行截图在 psm 3 下可能为空，回退 psm 6 再扫一次
            data = pytesseract.image_to_data(
                pil_canvas, lang=requested_lang, config='--oem 1 --psm 6',
                output_type=pytesseract.Output.DICT
            )
    except Exception as e:
        logging.warning("Tesseract 词级版面识别失败: %s", e)
        return []

    lines = []
    count = len(data.get('text', []) or [])
    for i in range(count):
        text = str(data['text'][i]).strip()
        if not text:
            continue
        try:
            conf = float(data['conf'][i])
        except Exception:
            conf = 0.0
        if conf < min_conf:
            continue
        try:
            x = int((data['left'][i] - pad) / scale)
            y = int((data['top'][i] - pad) / scale)
            w = int(math.ceil(data['width'][i] / scale))
            h = int(math.ceil(data['height'][i] / scale))
        except Exception:
            continue
        if w < 2 or h < 2:
            continue
        box = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        lines.append((box, text, max(0.0, min(1.0, conf / 100.0))))

    if not lines and allow_fallback_langs and lang_str is None and requested_lang != TESSERACT_LANGS:
        return tesseract_ocr_lines(image_rgb, TESSERACT_LANGS, min_conf, allow_fallback_langs=False)
    return lines


def _tile_starts(total_len, tile_len, overlap):
    """生成带重叠的滑窗切片起点列表，保证末端完整覆盖。"""
    if total_len <= tile_len:
        return [0]
    step = max(1, tile_len - overlap)
    starts = list(range(0, total_len - tile_len + 1, step))
    last = total_len - tile_len
    if not starts or starts[-1] != last:
        starts.append(last)
    return starts


def _rapid_ocr_single_tile(rapid_engine, bgr):
    """对单块 BGR 图像做小字放大、边缘留白后推理，并精确反算回原图坐标。"""
    h, w = bgr.shape[:2]
    if h <= 0 or w <= 0:
        return []

    if h < 48 or w < 120:
        scale = min(2.5, 1600.0 / max(1, w, h))
    elif max(h, w) < 900 and min(h, w) < 450:
        scale = 2.0
    else:
        scale = 1.0
    scale = max(1.0, scale)

    if scale > 1.05:
        work = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    else:
        scale = 1.0
        work = bgr

    pad = 24
    bg_bgr = _border_ring_median(work)
    padded = cv2.copyMakeBorder(work, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=bg_bgr)

    with _RAPID_OCR_INFER_LOCK:
        res, _ = rapid_engine(padded)
    if not res:
        return []

    mapped = []
    for item in res:
        try:
            pts, text, score = item[0], str(item[1]).strip(), float(item[2])
            if not text:
                continue
            new_pts = []
            for pt in pts:
                ox = max(0.0, min(float(w), (float(pt[0]) - pad) / scale))
                oy = max(0.0, min(float(h), (float(pt[1]) - pad) / scale))
                new_pts.append([ox, oy])
            xs = [p[0] for p in new_pts]
            ys = [p[1] for p in new_pts]
            if max(xs) - min(xs) < 2 or max(ys) - min(ys) < 2:
                continue
            mapped.append([new_pts, text, score])
        except Exception:
            continue
    return mapped


def _dedup_tiled_boxes(candidates):
    """对切片识别结果按非切边优先 + 置信度与字数加权去重。"""
    if len(candidates) <= 1:
        return [c[0] for c in candidates]

    ordered = sorted(
        candidates,
        key=lambda c: (not c[2], c[1] * max(1, len(c[0][1].strip()))),
        reverse=True
    )
    kept = []
    for item, _score, _clipped, rect in ordered:
        x0, y0, x1, y1 = rect
        rw = max(1.0, x1 - x0)
        rh = max(1.0, y1 - y0)
        duplicate = False
        for _k_item, k_rect in kept:
            kx0, ky0, kx1, ky1 = k_rect
            ix0, iy0 = max(x0, kx0), max(y0, ky0)
            ix1, iy1 = min(x1, kx1), min(y1, ky1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            iw, ih = ix1 - ix0, iy1 - iy0
            min_w = min(rw, max(1.0, kx1 - kx0))
            min_h = min(rh, max(1.0, ky1 - ky0))
            inter_area = iw * ih
            min_area = min(rw * rh, max(1.0, (kx1 - kx0) * (ky1 - ky0)))
            if (ih >= 0.55 * min_h and iw >= 0.50 * min_w) or (inter_area >= 0.50 * min_area):
                duplicate = True
                break
        if not duplicate:
            kept.append((item, rect))
    return [k[0] for k in kept]


def _rapid_ocr_tiled(rapid_engine, bgr):
    """超大/长截图走带重叠滑窗分块识别，避免长边压缩导致小字糊掉。"""
    h, w = bgr.shape[:2]
    tile_w, tile_h, overlap = 1600, 1400, 180
    if max(h, w) <= 1920:
        return _rapid_ocr_single_tile(rapid_engine, bgr)

    xs = _tile_starts(w, tile_w, overlap)
    ys = _tile_starts(h, tile_h, overlap)
    candidates = []
    for y0 in ys:
        y1 = min(h, y0 + tile_h)
        for x0 in xs:
            x1 = min(w, x0 + tile_w)
            sub_res = _rapid_ocr_single_tile(rapid_engine, bgr[y0:y1, x0:x1])
            for pts, text, score in sub_res:
                shifted = [[pt[0] + x0, pt[1] + y0] for pt in pts]
                pxs = [p[0] for p in shifted]
                pys = [p[1] for p in shifted]
                bx0, bx1 = min(pxs), max(pxs)
                by0, by1 = min(pys), max(pys)
                clipped = (
                    (x0 > 0 and bx0 <= x0 + 4)
                    or (x1 < w and bx1 >= x1 - 4)
                    or (y0 > 0 and by0 <= y0 + 4)
                    or (y1 < h and by1 >= y1 - 4)
                )
                candidates.append(([shifted, text, score], score, clipped, (bx0, by0, bx1, by1)))
    return _dedup_tiled_boxes(candidates)


def _enhance_bgr_for_retry(bgr):
    """低置信度或空结果时的对比度增强 + 锐化预处理（深色底自动反色）。"""
    work = bgr
    bg = _border_ring_median(work)
    luma = 0.114 * bg[0] + 0.587 * bg[1] + 0.299 * bg[2]
    if luma < 110:
        work = cv2.bitwise_not(work)
    lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    l_ch = clahe.apply(l_ch)
    merged = cv2.cvtColor(cv2.merge((l_ch, a_ch, b_ch)), cv2.COLOR_LAB2BGR)
    blur = cv2.GaussianBlur(merged, (0, 0), 1.0)
    return cv2.addWeighted(merged, 1.35, blur, -0.35, 0)


def _ocr_quality_stats(lines):
    if not lines:
        return 0.0, 0.0, 0, 1.0
    total_weighted = 0.0
    conf_sum = 0.0
    total_chars = 0
    low_cnt = 0
    for item in lines:
        text = str(item[1]).strip()
        score = float(item[2]) if len(item) > 2 else 1.0
        n = max(1, len(text))
        total_weighted += score * n
        conf_sum += score
        total_chars += len(text)
        if score < 0.72:
            low_cnt += 1
    mean_conf = conf_sum / len(lines)
    low_ratio = low_cnt / len(lines)
    return total_weighted, mean_conf, total_chars, low_ratio


def _merge_non_overlapping_ocr(primary_lines, extra_lines, min_score=0.72):
    """把补充识别中未与主结果重叠的高置信度文本框并入，挽回局部漏检。"""
    if not extra_lines:
        return primary_lines
    merged = list(primary_lines)
    primary_rects = [r for r in (_ocr_item_bbox(it[0]) for it in primary_lines) if r is not None]
    for item in extra_lines:
        score = float(item[2]) if len(item) > 2 else 0.0
        if score < min_score:
            continue
        rect = _ocr_item_bbox(item[0])
        if rect is None:
            continue
        overlapped = False
        for pr in primary_rects:
            inter = pr.intersected(rect)
            if not inter.isValid():
                continue
            min_h = max(1, min(pr.height(), rect.height()))
            min_w = max(1, min(pr.width(), rect.width()))
            if inter.height() >= min_h * 0.4 and inter.width() >= min_w * 0.3:
                overlapped = True
                break
        if not overlapped:
            merged.append(item)
            primary_rects.append(rect)
    return merged


def _rapid_ocr_with_retry(rapid_engine, rgb_arr):
    """执行 RapidOCR：RGB→BGR 转换、切片/小字放大留白，并在为空或低置信度时自动对比度增强重识。"""
    bgr = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)
    primary = _rapid_ocr_tiled(rapid_engine, bgr)
    p_total, p_mean, p_chars, p_low = _ocr_quality_stats(primary)
    bg = _border_ring_median(bgr)
    is_dark_bg = (0.114 * bg[0] + 0.587 * bg[1] + 0.299 * bg[2]) < 110

    need_retry = (
        (not primary)
        or (p_mean < 0.78)
        or (len(primary) >= 2 and p_low >= 0.30)
        or (is_dark_bg and p_mean < 0.86 and p_low >= 0.12)
    )
    if not need_retry:
        return primary

    enhanced = _enhance_bgr_for_retry(bgr)
    retry = _rapid_ocr_tiled(rapid_engine, enhanced)
    if not retry:
        return primary

    r_total, r_mean, r_chars, _ = _ocr_quality_stats(retry)
    if not primary:
        return retry
    if r_total > p_total * 1.06 or (r_chars >= p_chars * 0.9 and r_mean > p_mean + 0.04):
        return _merge_non_overlapping_ocr(retry, primary, min_score=0.45)
    return _merge_non_overlapping_ocr(primary, retry, min_score=0.45)


def _group_tess_words_into_lines(tess_words, image_size):
    """将 Tesseract 词级结果通过 build_ocr_doc 聚合成视觉行并附带行平均置信度。"""
    if not tess_words:
        return []
    doc = build_ocr_doc(tess_words, image_size)
    if doc is None or doc.is_empty():
        return []

    word_items = []
    for item in tess_words:
        rect = _ocr_item_bbox(item[0])
        if rect is not None:
            word_items.append((rect, float(item[2]) if len(item) > 2 else 0.5))

    grouped = []
    for row in doc.rows:
        txt = re.sub(r'(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])', '', row.text).strip()
        if not txt:
            continue
        confs = [c for wr, c in word_items if row.rect.intersects(wr)]
        avg_conf = sum(confs) / len(confs) if confs else 0.5
        r = row.rect
        box = [[r.left(), r.top()], [r.right(), r.top()], [r.right(), r.bottom()], [r.left(), r.bottom()]]
        grouped.append([box, txt, avg_conf])
    return grouped


def _win_ocr_clusters_for_tile(bgr, langs=("zh-Hans-CN", "en-US")):
    """调用 Windows 内置 Windows.Media.Ocr 对单块图像识别并按横向间隙拆分为独立 UI 词簇。"""
    try:
        import asyncio
        import winsdk.windows.media.ocr as wocr
        import winsdk.windows.graphics.imaging as wimg
        import winsdk.windows.globalization as wglob
        import winsdk.windows.storage.streams as wstreams
    except Exception:
        return []

    h, w = bgr.shape[:2]
    if h <= 0 or w <= 0:
        return []
    scale = 2.5 if h < 70 else (2.0 if max(h, w) <= 1350 else 1.0)
    if max(h, w) * scale > 3200:
        scale = max(1.0, 3200.0 / max(1, h, w))
    work = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1.05 else bgr
    bg_bgr = _border_ring_median(work)
    if (0.114 * bg_bgr[0] + 0.587 * bg_bgr[1] + 0.299 * bg_bgr[2]) < 115:
        work = cv2.bitwise_not(work)
        bg_bgr = _border_ring_median(work)
    pad = 16
    padded = cv2.copyMakeBorder(work, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=bg_bgr)
    ok_enc, enc_buf = cv2.imencode(".png", padded, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok_enc:
        return []
    png_bytes = enc_buf.tobytes()

    async def _run_async():
        stream = wstreams.InMemoryRandomAccessStream()
        writer = wstreams.DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(png_bytes)
        await writer.store_async()
        await writer.flush_async()
        dec = await wimg.BitmapDecoder.create_async(stream)
        bmp = await dec.get_software_bitmap_async()

        avail = {l.language_tag.lower(): l for l in wocr.OcrEngine.available_recognizer_languages}
        per_lang = {}
        for tag in langs:
            lang_obj = avail.get(tag.lower())
            if lang_obj is None:
                continue
            eng = wocr.OcrEngine.try_create_from_language(lang_obj)
            if eng is None:
                continue
            res = await eng.recognize_async(bmp)
            clusters = []
            for line in res.lines:
                words = list(line.words)
                if not words:
                    continue
                groups = [[words[0]]]
                for wd in words[1:]:
                    prev_r = groups[-1][-1].bounding_rect
                    curr_r = wd.bounding_rect
                    gap = curr_r.x - (prev_r.x + prev_r.width)
                    ref_h = max(6.0, min(float(prev_r.height), float(curr_r.height)))
                    if gap > max(12.0 * scale, ref_h * 1.8):
                        groups.append([wd])
                    else:
                        groups[-1].append(wd)
                for grp in groups:
                    # 剥离 WinOCR 在文件列表/菜单左侧把图标识别出的前导孤立词盒（如 Ü / 同 / 国 / 口 / a(紧接文件名)）
                    while len(grp) >= 2:
                        w0 = str(grp[0].text or "").strip()
                        w1 = str(grp[1].text or "").strip()
                        is_sym_icon = (
                            re.fullmatch(r'[\u00C0-\u024F©®™•·◦▪▸►□■▫○●◌◎◇◆◈|]|[口囗凵曰白国同回图目日四困团圆因网]|C\)', w0)
                            and re.match(r'^[A-Za-z0-9._\-\u4e00-\u9fff]', w1)
                        )
                        is_file_icon_char = (
                            len(w0) == 1 and w0.isascii() and w0.isalnum()
                            and ('.' in w1 or '_' in w1 or '-' in w1)
                            and re.match(r'^[A-Za-z0-9._\-]+\.[A-Za-z0-9]{1,6}$', w1)
                        )
                        if is_sym_icon or is_file_icon_char:
                            grp = grp[1:]
                        else:
                            break
                    parts = []
                    word_spans = []
                    x0 = min(wd.bounding_rect.x for wd in grp)
                    y0 = min(wd.bounding_rect.y for wd in grp)
                    x1 = max(wd.bounding_rect.x + wd.bounding_rect.width for wd in grp)
                    y1 = max(wd.bounding_rect.y + wd.bounding_rect.height for wd in grp)
                    for wi, wd in enumerate(grp):
                        wt = str(wd.text or "").strip()
                        if not wt:
                            continue
                        if parts and (parts[-1][-1].isascii() or wt[0].isascii()):
                            parts.append(" " + wt)
                        else:
                            parts.append(wt)
                        wx0 = max(0.0, min(float(w), (wd.bounding_rect.x - pad) / scale))
                        wy0 = max(0.0, min(float(h), (wd.bounding_rect.y - pad) / scale))
                        wx1 = max(wx0 + 1.0, min(float(w), (wd.bounding_rect.x + wd.bounding_rect.width - pad) / scale))
                        wy1 = max(wy0 + 1.0, min(float(h), (wd.bounding_rect.y + wd.bounding_rect.height - pad) / scale))
                        w_rect = QRect(int(math.floor(wx0)), int(math.floor(wy0)),
                                       max(1, int(math.ceil(wx1 - wx0))), max(1, int(math.ceil(wy1 - wy0))))
                        word_spans.append((w_rect, wt))
                    txt = "".join(parts).strip()
                    if not txt:
                        continue
                    ox0 = max(0.0, min(float(w), (x0 - pad) / scale))
                    oy0 = max(0.0, min(float(h), (y0 - pad) / scale))
                    ox1 = max(ox0 + 2.0, min(float(w), (x1 - pad) / scale))
                    oy1 = max(oy0 + 2.0, min(float(h), (y1 - pad) / scale))
                    box = [[ox0, oy0], [ox1, oy0], [ox1, oy1], [ox0, oy1]]
                    has_spurious_cjk = (
                        bool(re.search(r'[A-Za-z]+\s*[\u3001-\u303f\u2103\xb0]+\s*[A-Za-z]+', txt))
                        or bool(re.search(r'[A-Za-z]+\s*[\uff1a\uff08\uff09]+\s*[A-Za-z0-9]', txt))
                        or (len(re.findall(r'[A-Za-z]', txt)) >= 4 and len(re.findall(r'[\u4e00-\u9fff]', txt)) == 1)
                    )
                    base_conf = 0.80 if tag == "en-US" else 0.74
                    conf = 0.55 if (tag == "zh-Hans-CN" and has_spurious_cjk) else base_conf
                    clusters.append([box, txt, conf, word_spans])
            per_lang[tag] = clusters
        return per_lang

    try:
        per_lang = asyncio.run(_run_async())
    except Exception as e:
        logging.warning("Windows OCR 执行异常: %s", e)
        return []

    zh_lines = per_lang.get("zh-Hans-CN", [])
    en_lines = per_lang.get("en-US", [])
    if not zh_lines:
        return en_lines
    if not en_lines:
        return zh_lines

    merged = list(zh_lines)
    merged_rects = [_ocr_item_bbox(it[0]) for it in merged]
    for e_item in en_lines:
        e_rect = _ocr_item_bbox(e_item[0])
        if e_rect is None:
            continue
        e_text = str(e_item[1]).strip()
        overlaps = []
        for idx, z_rect in enumerate(merged_rects):
            if z_rect is None:
                continue
            inter = z_rect.intersected(e_rect)
            if not inter.isValid():
                continue
            min_h = max(1, min(z_rect.height(), e_rect.height()))
            min_w = max(1, min(z_rect.width(), e_rect.width()))
            if inter.height() >= min_h * 0.45 and inter.width() >= min_w * 0.30:
                overlaps.append(idx)
        if not overlaps:
            merged.append(e_item)
            merged_rects.append(e_rect)
        elif len(overlaps) == 1:
            idx = overlaps[0]
            z_text = str(merged[idx][1]).strip()
            z_conf = float(merged[idx][2])
            pure_latin_row = not re.search(r'[\u4e00-\u9fff]', z_text)
            if (z_conf < 0.70 or pure_latin_row) and re.fullmatch(r'[A-Za-z0-9\s\-_./:=,;!?()|]+', e_text):
                merged[idx] = e_item
                merged_rects[idx] = e_rect
        elif all(not re.search(r'[\u4e00-\u9fff]', str(merged[k][1])) for k in overlaps) and re.fullmatch(r'[A-Za-z0-9\s\-_./:=,;!?()|]+', e_text):
            # 同一纯英文行被 zh-Hans-CN 拆断时，用完整的 en-US 整行整体替换
            for k in sorted(overlaps, reverse=True):
                merged.pop(k)
                merged_rects.pop(k)
            merged.append(e_item)
            merged_rects.append(e_rect)
    return merged


def win_ocr_lines(image_rgb):
    """调用 Windows.Media.Ocr（支持大图滑窗切片与中英互补）获取高精 UI 行框。"""
    if image_rgb is None or image_rgb.size == 0:
        return []
    bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    if max(h, w) <= 1350:
        return _win_ocr_clusters_for_tile(bgr)

    tile_w, tile_h, overlap = 1300, 1080, 160
    xs = _tile_starts(w, tile_w, overlap)
    ys = _tile_starts(h, tile_h, overlap)
    candidates = []
    for y0 in ys:
        y1 = min(h, y0 + tile_h)
        for x0 in xs:
            x1 = min(w, x0 + tile_w)
            sub_res = _win_ocr_clusters_for_tile(bgr[y0:y1, x0:x1])
            for item in sub_res:
                pts, text, score = item[0], item[1], item[2]
                shifted = [[pt[0] + x0, pt[1] + y0] for pt in pts]
                shifted_spans = (
                    [(r.translated(x0, y0), wt) for r, wt in item[3]]
                    if len(item) > 3 and item[3] else None
                )
                pxs = [p[0] for p in shifted]
                pys = [p[1] for p in shifted]
                bx0, bx1 = min(pxs), max(pxs)
                by0, by1 = min(pys), max(pys)
                clipped = (
                    (x0 > 0 and bx0 <= x0 + 4)
                    or (x1 < w and bx1 >= x1 - 4)
                    or (y0 > 0 and by0 <= y0 + 4)
                    or (y1 < h and by1 >= y1 - 4)
                )
                entry = [shifted, text, score, shifted_spans] if shifted_spans else [shifted, text, score]
                candidates.append((entry, score, clipped, (bx0, by0, bx1, by1)))
    return _dedup_tiled_boxes(candidates)


def _fuse_rapid_and_tesseract(rapid_lines, tess_words, image_size):
    """
    空间框级融合 RapidOCR 与 Tesseract：
    - RapidOCR 空白/漏检区域：直接补入 Tesseract 行；
    - RapidOCR 极低置信度框 (<0.62) 且 Tesseract 高置信度 (>=0.72)：局部替换；
    - 避免把孤立误识别噪声替代掉整段正常结果。
    """
    tess_lines = _group_tess_words_into_lines(tess_words, image_size)
    if not rapid_lines:
        return tess_lines
    if not tess_lines:
        return rapid_lines

    fused = [list(it) for it in rapid_lines]
    fused_rects = [_ocr_item_bbox(it[0]) for it in fused]

    for t_box, t_text, t_conf in tess_lines:
        t_rect = _ocr_item_bbox(t_box)
        if t_rect is None:
            continue
        if not any(ch.isalnum() or ('\u4e00' <= ch <= '\u9fff') for ch in t_text):
            continue

        overlaps = []
        for idx, r_rect in enumerate(fused_rects):
            if r_rect is None:
                continue
            inter = r_rect.intersected(t_rect)
            if not inter.isValid():
                continue
            min_h = max(1, min(r_rect.height(), t_rect.height()))
            min_w = max(1, min(r_rect.width(), t_rect.width()))
            if inter.height() >= min_h * 0.45 and inter.width() >= min_w * 0.30:
                overlaps.append(idx)

        if not overlaps:
            if t_conf >= 0.42:
                fused.append([t_box, t_text, t_conf])
                fused_rects.append(t_rect)
        elif len(overlaps) == 1:
            idx = overlaps[0]
            r_score = float(fused[idx][2]) if len(fused[idx]) > 2 else 1.0
            r_text = str(fused[idx][1]).strip()
            can_replace = (r_score < 0.62 and t_conf >= 0.72) or (r_score <= 0.75 and t_conf >= 0.85)
            if can_replace and len(t_text.replace(" ", "")) >= max(2, int(len(r_text.replace(" ", "")) * 0.82)):
                fused[idx] = [t_box, t_text, t_conf]
                fused_rects[idx] = t_rect
        else:
            # 多个低分碎片跨同一整行时，用高置信度 Tesseract 行整体替换
            if t_conf >= 0.85 and all((float(fused[k][2]) if len(fused[k]) > 2 else 1.0) <= 0.76 for k in overlaps):
                merged_r_len = sum(len(str(fused[k][1]).replace(" ", "")) for k in overlaps)
                if len(t_text.replace(" ", "")) >= max(2, int(merged_r_len * 0.85)):
                    for k in sorted(overlaps, reverse=True):
                        fused.pop(k)
                        fused_rects.pop(k)
                    fused.append([t_box, t_text, t_conf])
                    fused_rects.append(t_rect)

    return fused



# ==========================================
# OCR 后台工作线程 (智能双引擎融合: 懒加载 RapidOCR + 空间对齐 Tesseract / AI 视觉识别)
# ==========================================
class OcrWorker(QThread):
    finished = pyqtSignal(str)
    doc_ready = pyqtSignal(object)
    codes_ready = pyqtSignal(list)

    def __init__(self, image: QImage, use_ai=False, fast=False):
        super().__init__()
        # 严格使用主线程传入的 QImage 纯数据对象，彻底杜绝 QPixmap 跨线程调用崩溃
        self.image = image
        self.use_ai = use_ai
        # fast=True 时仅跑本地 RapidOCR 并直接返回版面文本，跳过耗时的 Tesseract 融合
        self.fast = fast
        self.detected_codes = []
        self.ocr_doc = None
        self.ocr_warning = ""

    def run(self):
        try:
            arr = _qimage_to_rgb_array(self.image)

            # 同步扫描二维码与一维条码，纯码截图也能直接解码
            if not self.fast:
                try:
                    self.detected_codes = detect_codes_in_image(arr if arr is not None else self.image)
                    if self.detected_codes:
                        self.codes_ready.emit(self.detected_codes)
                except Exception as ex_c:
                    logging.warning("QR/Barcode 识别异常: %s", ex_c)

            # AI 识别模式（失败时自动回退到本地离线 OCR，并附带警告提示）
            if self.use_ai:
                try:
                    result = ai_ocr_recognize(self.image)
                    self.finished.emit(result if result else "")
                    return
                except Exception as ex_ai:
                    logging.warning("AI OCR 失败，将尝试回退本地 OCR: %s", ex_ai)
                    self.ocr_warning = f"AI 识别失败，已回退本地 OCR：{ex_ai}"

            if arr is None:
                if self.ocr_warning:
                    self.finished.emit(f"识别发生错误:\n{self.ocr_warning}")
                else:
                    self.finished.emit("")
                return

            # 1. 运行 RapidOCR（含 RGB→BGR、小字放大留白、大图滑窗切片与低置信度对比增强重识）
            rapid_lines = []
            rapid_engine = get_rapid_ocr_engine()
            if rapid_engine is not None:
                try:
                    rapid_lines = _rapid_ocr_with_retry(rapid_engine, arr)
                except Exception as ex_r:
                    logging.warning(f"RapidOCR 执行异常: {ex_r}")

            # 2. 若 RapidOCR 未安装或置信度偏低，启用 Windows 内置 OCR（即使快扫模式低置信度时也轻量互补）
            _tot, mean_conf, _chars, low_ratio = _ocr_quality_stats(rapid_lines)
            need_win_ocr = (
                (not rapid_lines)
                or (not self.fast and (mean_conf < 0.78 or low_ratio > 0.25))
                or (self.fast and (mean_conf < 0.74 or low_ratio > 0.30))
            )
            if need_win_ocr:
                try:
                    win_lines = win_ocr_lines(arr)
                    if win_lines:
                        if not rapid_lines:
                            rapid_lines = win_lines
                        else:
                            rapid_lines = _merge_non_overlapping_ocr(rapid_lines, win_lines, min_score=0.72)
                except Exception as ex_win:
                    logging.warning(f"Windows OCR 融合异常: {ex_win}")

            # 2.5 快扫模式下未检出文字时补扫二维码/条形码，避免纯码截图毫无反应
            if self.fast and not rapid_lines and not self.detected_codes:
                try:
                    self.detected_codes = detect_codes_in_image(arr)
                    if self.detected_codes:
                        self.codes_ready.emit(self.detected_codes)
                except Exception as ex_fc:
                    logging.warning("快扫 QR/Barcode 兜底识别异常: %s", ex_fc)

            # 3. 按需运行 Tesseract（无 RapidOCR 时的非快扫高精度融合、小语种兜底或其余引擎空白/低置信度补全）
            settings = load_settings()
            use_sea_langs = bool(settings.get("ocr_enable_sea_langs", False))
            _tot, mean_conf, _chars, low_ratio = _ocr_quality_stats(rapid_lines)
            need_tess = (not rapid_lines) or use_sea_langs or (rapid_engine is None and not self.fast) or (
                not self.fast and (mean_conf < 0.76 or low_ratio > 0.25)
            )
            if need_tess:
                try:
                    req_lang = TESSERACT_LANGS if use_sea_langs else AUTO_OCR_LANGS
                    tess_min_conf = 25 if rapid_lines else 18
                    tess_words = tesseract_ocr_lines(
                        arr, lang_str=req_lang, min_conf=tess_min_conf, allow_fallback_langs=(not rapid_lines)
                    )
                    if tess_words:
                        rapid_lines = _fuse_rapid_and_tesseract(rapid_lines, tess_words, self.image.size())
                        logging.info("Tesseract 空间融合完成: 当前共 %d 个文本块", len(rapid_lines))
                except Exception as ex_tb:
                    logging.warning(f"Tesseract 空间融合异常: {ex_tb}")

            # 3. 统一通过 build_ocr_doc 构建版面坐标与整洁的行序/空格文本
            if rapid_lines:
                try:
                    doc = build_ocr_doc(rapid_lines, self.image.size())
                    if doc is not None and not doc.is_empty():
                        self.ocr_doc = doc
                        self.doc_ready.emit(doc)
                        self.finished.emit(doc.text)
                        return
                except Exception as ex_doc:
                    logging.warning(f"OCR 版面构建异常: {ex_doc}")

            if self.ocr_warning and not rapid_lines:
                self.finished.emit(f"识别发生错误:\n{self.ocr_warning}")
                return
            text_out = "\n".join(str(item[1]).strip() for item in rapid_lines if item and str(item[1]).strip())
            self.finished.emit(text_out)

        except Exception as e:
            logging.error("OCR Worker Error: %s", e, exc_info=True)
            self.finished.emit(f"识别发生错误:\n{e}")

# ==========================================
# 共用划词右键菜单辅助
# ==========================================
CODEX_MENU_QSS = """
    QMenu {
        background-color: #FFFFFF;
        border: 1px solid #E4E4E7;
        border-radius: 10px;
        padding: 5px;
        font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif;
        font-size: 12.5px;
        color: #18181B;
    }
    QMenu::item {
        padding: 6px 14px;
        border-radius: 6px;
    }
    QMenu::item:selected {
        background-color: #F4F4F5;
        color: #09090B;
    }
    QMenu::separator {
        height: 1px;
        background: #ECECEF;
        margin: 4px 8px;
    }
"""


def exec_text_selection_menu(parent, global_pos, text, on_copy, on_copy_table, on_translate,
                             on_select_all, on_clear, on_copy_exit=None, extra_exit_action=None):
    """在截图选区与贴图窗口间共用的划词快捷右键菜单。"""
    if not text:
        return None
    menu = QMenu(parent)
    menu.setStyleSheet(CODEX_MENU_QSS)
    act_copy = menu.addAction("📋 复制选中文字 (Ctrl+C)")
    act_copy_exit = menu.addAction("✅ 复制并退出") if on_copy_exit else None
    act_table = menu.addAction("📊 复制为表格 (Excel)")
    act_translate = menu.addAction("🌐 翻译选中文字")
    url_match = re.search(r'https?://[^\s<>"\')\]}]+', text)
    act_open_url = menu.addAction("🔗 打开链接") if url_match else None
    menu.addSeparator()
    act_all = menu.addAction("🔘 全选文字 (Ctrl+A)")
    act_clear = menu.addAction("✖ 取消选中 (Esc)")
    act_exit = menu.addAction(extra_exit_action[0]) if extra_exit_action else None

    chosen = menu.exec_(global_pos)
    if chosen == act_copy:
        on_copy()
    elif act_copy_exit is not None and chosen == act_copy_exit:
        on_copy_exit()
    elif chosen == act_table:
        on_copy_table()
    elif chosen == act_translate:
        on_translate()
    elif act_open_url is not None and chosen == act_open_url and url_match:
        webbrowser.open(url_match.group(0))
    elif chosen == act_all:
        on_select_all()
    elif chosen == act_clear:
        on_clear()
    elif act_exit is not None and chosen == act_exit:
        extra_exit_action[1]()
    return chosen


# ==========================================
# 图上译文覆盖层 (逐行并发翻译，原地替换显示)
# ==========================================
def _prep_text_for_translate(text, use_ai=False):
    """在线翻译遇到小写下划线连接词/文件名（如 pin_drawer.py / app_icon.ico）时展开为空格，避免引擎整词拒翻。"""
    t = (text or "").strip()
    if use_ai or not t or ' ' in t or '_' not in t:
        return t
    m = re.fullmatch(r'([A-Za-z]{2,}(?:_[A-Za-z]{2,})+)(\.[A-Za-z0-9]{1,6})?', t)
    if not m:
        return t
    stem, ext = m.group(1), m.group(2) or ""
    if stem.isupper():
        return t
    return stem.replace('_', ' ') + ext


class RowTranslationWorker(QThread):
    """按行并发翻译，逐行回传，避免整段翻译无法定位到原文位置。"""

    row_ready = pyqtSignal(int, str)
    progress = pyqtSignal(int, int)
    all_done = pyqtSignal(bool)

    def __init__(self, texts, target_lang, use_ai=False):
        super().__init__()
        self.texts = list(texts)
        self.target_lang = target_lang
        self.use_ai = use_ai
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _translate_one(self, text):
        if self._cancelled or not text.strip():
            return text
        if _looks_like_target(text, self.target_lang):
            return text
        send_text = _prep_text_for_translate(text, self.use_ai)
        last_err = None
        for attempt in range(2):
            if self._cancelled:
                return text
            try:
                if self.use_ai:
                    res, _ = ai_translate_single_chunk(
                        send_text, self.target_lang, 'auto', timeout=25,
                        is_cancelled_fn=lambda: self._cancelled
                    )
                else:
                    res, _ = translate_single_chunk(send_text, self.target_lang, 'auto', timeout=8)
                return res if res else text
            except Exception as ex:
                last_err = ex
                if attempt == 0 and not self._cancelled:
                    time.sleep(0.2)
        if last_err is not None:
            raise last_err
        return text

    def _build_batches(self):
        batches = []
        current = []
        size = 0
        max_items = 8 if self.use_ai else 10
        max_chars = 1500 if self.use_ai else 1700
        for i, text in enumerate(self.texts):
            add = len(text) + 4
            if current and (size + add > max_chars or len(current) >= max_items):
                batches.append(current)
                current = []
                size = 0
            current.append(i)
            size += add
        if current:
            batches.append(current)
        return batches

    def _translate_batch(self, indices):
        results = [None] * len(indices)
        pending = []
        for pos, i in enumerate(indices):
            if _looks_like_target(self.texts[i], self.target_lang):
                results[pos] = self.texts[i]
            else:
                pending.append((pos, i))
        if not pending:
            return results, True

        # AI 翻译优先走带上下文的 JSON 批量接口，保证同屏术语与句子前后连贯
        if self.use_ai and len(pending) > 1:
            plain = [self.texts[i].strip() for _, i in pending]
            try:
                translated_list = ai_translate_batch(
                    plain, self.target_lang, timeout=30,
                    is_cancelled_fn=lambda: self._cancelled
                )
                for (pos, _i), trans in zip(pending, translated_list):
                    results[pos] = trans if trans else self.texts[_i]
                return results, True
            except Exception as e:
                logging.warning("AI 批量上下文翻译失败，回退逐块翻译: %s", e)

        if self.use_ai or len(pending) == 1:
            ok = True
            for pos, i in pending:
                try:
                    results[pos] = self._translate_one(self.texts[i])
                except Exception:
                    results[pos] = self.texts[i]
                    ok = False
            return results, ok

        # 普通在线翻译把多个段落拼成一次请求；用 [[n]] 编号并容忍空格/全角括号变形
        plain = [_prep_text_for_translate(self.texts[i].replace("\n\n", "\n"), use_ai=False) for _, i in pending]
        try:
            marked = "\n\n".join(f"[[{k + 1}]] {p}" for k, p in enumerate(plain))
            res, _ = translate_single_chunk(marked, self.target_lang, 'auto', timeout=12)
            matches = list(re.finditer(r'\[\s*\[\s*(\d+)\s*\]\s*\]|【\s*【\s*(\d+)\s*】\s*】', res or ""))
            if len(matches) == len(pending):
                ok = True
                for k, match in enumerate(matches):
                    num = int(match.group(1) or match.group(2)) - 1
                    start = match.end()
                    end = matches[k + 1].start() if k + 1 < len(matches) else len(res)
                    part = res[start:end].strip()
                    if 0 <= num < len(pending) and part:
                        results[pending[num][0]] = part
                    else:
                        ok = False
                if ok and all(r is not None for r in results):
                    return results, True
            logging.warning("批量翻译编号回收失败 (%d 个标记/%d 段)，回退逐块翻译", len(matches), len(pending))
        except Exception as e:
            logging.warning("批量翻译失败，回退逐块翻译: %s", e)

        ok = True
        for pos, i in pending:
            try:
                results[pos] = self._translate_one(self.texts[i])
            except Exception:
                results[pos] = self.texts[i]
                ok = False
        return results, ok

    def run(self):
        total = len(self.texts)
        if total == 0:
            self.all_done.emit(True)
            return
        ok_all = True
        done = 0
        batches = self._build_batches()
        try:
            with ThreadPoolExecutor(max_workers=min(5 if not self.use_ai else 4, max(1, len(batches)))) as pool:
                future_map = {pool.submit(self._translate_batch, b): b for b in batches}
                for fut in as_completed(future_map):
                    if self._cancelled:
                        break
                    batch = future_map[fut]
                    try:
                        results, ok = fut.result()
                        ok_all = ok_all and ok
                    except Exception:
                        results = [self.texts[i] for i in batch]
                        ok_all = False
                    for i, result in zip(batch, results):
                        done += 1
                        if not self._cancelled:
                            self.row_ready.emit(i, result)
                            self.progress.emit(done, total)
        except Exception as e:
            logging.warning("逐行翻译异常: %s", e)
            ok_all = False
        self.all_done.emit(ok_all and not self._cancelled)


class TranslationBlock:
    """把同一段落的连续文字行合并为语义块，并保留真实视觉字高与行数供精确排版。"""

    __slots__ = ("text", "rect", "row_indices", "line_height", "ink_height", "line_count")

    def __init__(self, row_indices, rect, line_height=None, line_count=1, ink_height=None):
        self.row_indices = list(row_indices)
        self.rect = QRect(rect)
        self.text = ""
        lh = float(line_height if line_height is not None else max(1, rect.height()))
        self.line_height = lh
        self.ink_height = float(ink_height if ink_height is not None else lh)
        self.line_count = int(line_count)


_LEADING_ICON_PREFIX_RE = re.compile(
    r'^(?:[©®™•·◦▪▸►□■▫○●◌◎◇◆◈]+(?:\s+|$)'
    r'|[\u00C0-\u024F]\s+(?=[A-Za-z0-9._\-\u4e00-\u9fff])'
    r'|[A-Za-z0-9]\s+(?=[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,6}\b)'
    r'|[口囗凵曰白国同回图目日四困团圆因网]+\s*(?=[A-Za-z_][A-Za-z0-9_.\-]{1,})'
    r'|[口囗凵曰白]+\s*(?=[A-Za-z0-9])'
    r'|[OQ]\s+(?=[A-Za-z\u4e00-\u9fff])(?!型|币|&)'
    r'|C\)\s+(?=[A-Za-z0-9]))'
)


def _clean_ocr_text(text):
    """清掉 OCR 边角竖线与常见图标误识前缀，绝不误删 Open/OK/Query 等英文首字母或正常汉字。"""
    t = (text or "").strip()
    t = re.sub(r'^[\s\|`~^\\]+', '', t)
    t = re.sub(r'[\s\|`~^\\]+$', '', t)
    t = _LEADING_ICON_PREFIX_RE.sub('', t)
    return t.strip()


def _is_ocr_noise_token(t):
    """过滤头像/图标/圆角徽章被误识出的符号乱码（如 (@ERE), OBE), +1)）。"""
    if not t:
        return True
    if len(t) <= 9 and not re.search(r'[\u4e00-\u9fff]', t):
        if (')' in t and '(' not in t and not re.match(r'^\d{1,2}\)', t)) or (']' in t and '[' not in t):
            return True
        if re.fullmatch(r'[\(\[\{@#*+]+[^a-z\u4e00-\u9fff]{1,6}[\)\]\}]*', t):
            return True
    return False


def _is_meaningful_block(text, rect):
    t = (text or "").strip()
    if _is_ocr_noise_token(t):
        return False
    clean = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+', '', t, flags=re.UNICODE)
    if not clean:
        return False
    if rect.width() < 8 or rect.height() < 7:
        return False
    if len(clean) == 1 and clean.isascii() and not clean.isdigit() and rect.width() < 20 and rect.height() < 20:
        return False
    return True


_LIST_PREFIX_RE = re.compile(
    r'^(?:[•·◦▪▸►]|\*\s+|-\s+(?!\d)|(?:\d{1,2}|[a-zA-Z])[.)、]\s+|[\u2460-\u2473]\s*)'
)


def _is_list_or_menu_boundary(prev_text, curr_text, prev_rect, curr_rect, curr_raw=""):
    """判断两行之间是否属于列表项/独立短菜单项/标题切正文边界，避免错误合并破坏排版。"""
    if not prev_text or not curr_text:
        return True
    if _LIST_PREFIX_RE.match(curr_text) or (curr_raw and _LIST_PREFIX_RE.match(curr_raw.strip())):
        return True
    if len(prev_text) < 18 or prev_rect.width() < curr_rect.height() * 6.5:
        return True
    if prev_rect.width() < curr_rect.width() * 0.55 and len(prev_text) <= 24:
        return True
    if (prev_rect.width() < 150 and curr_rect.width() < 150
            and len(prev_text) <= 18 and len(curr_text) <= 18
            and not prev_text.endswith((",", "，", "、", "-"))
            and (prev_text[0].isupper() and curr_text[0].isupper())):
        return True
    if prev_text[-1] in "。！？.!?：:…" or prev_text.endswith(".."):
        return True
    return False


def _join_block_lines(line_pairs, block_width):
    """将同段落内自动折行合并成连贯语义句送翻，避免按硬换行逐行碎裂翻译。"""
    if not line_pairs:
        return ""
    if len(line_pairs) == 1:
        return line_pairs[0][0]

    parts = [line_pairs[0][0]]
    for idx in range(1, len(line_pairs)):
        prev_txt, prev_rect = line_pairs[idx - 1]
        curr_txt, _curr_rect = line_pairs[idx]
        if not prev_txt:
            parts.append(curr_txt)
            continue
        is_wrapped = (
            prev_rect.width() >= block_width * 0.58
            and prev_txt[-1] not in "。！？.!?:：;；…"
            and not _LIST_PREFIX_RE.match(curr_txt)
        )
        if not is_wrapped:
            parts.append("\n" + curr_txt)
            continue
        if len(prev_txt) >= 2 and prev_txt[-1] == '-' and prev_txt[-2].isascii() and curr_txt[0].isascii():
            parts[-1] = parts[-1][:-1] + curr_txt
        elif ('\u4e00' <= prev_txt[-1] <= '\u9fff' or '\u3040' <= prev_txt[-1] <= '\u30ff') and (
            '\u4e00' <= curr_txt[0] <= '\u9fff' or '\u3040' <= curr_txt[0] <= '\u30ff'
        ):
            parts.append(curr_txt)
        else:
            parts.append(" " + curr_txt)
    return "".join(parts)


def _split_row_by_gaps(row):
    """若同一 OcrRow 内存在宽空白分隔（例如 用户名   徽章   时间戳），按 char_rects 拆分为独立片段。"""
    txt = row.text or ""
    crs = row.char_rects or []
    if not txt or len(crs) != len(txt):
        return [(txt, QRect(row.rect), crs)]

    gap_thresh = max(14, int(row.rect.height() * 1.45))
    split_indices = [
        ci for ci, ch in enumerate(txt)
        if ch == " " and crs[ci].width() >= gap_thresh
    ]
    if not split_indices:
        return [(txt, QRect(row.rect), crs)]

    spans = []
    start = 0
    for si in split_indices:
        if si > start:
            spans.append((start, si))
        start = si + 1
    if start < len(txt):
        spans.append((start, len(txt)))

    out = []
    for a, b in spans:
        sub_txt = txt[a:b].strip()
        if not sub_txt:
            continue
        lead_ws = len(txt[a:b]) - len(txt[a:b].lstrip())
        trail_ws = len(txt[a:b]) - len(txt[a:b].rstrip())
        ca, cb = a + lead_ws, b - trail_ws
        sub_crs = crs[ca:cb]
        if not sub_crs:
            continue
        u = QRect(sub_crs[0])
        for cr in sub_crs[1:]:
            u = u.united(cr)
        out.append((sub_txt, u, sub_crs))
    return out or [(txt, QRect(row.rect), crs)]


_GUTTER_ICON_TOKEN_RE = re.compile(
    r'^((?:[0OBDQa-z0-9]|LB|C\)|[A-Z]\)|[\u00C0-\u024F]|[口囗凵曰白国同回图目日四困团圆因网])\s+)(?=[A-Za-z0-9_.\-\u4e00-\u9fff]{2,})'
)


def _detect_gutter_icon_indices(segments):
    """识别竖向列表左侧反复出现的 UI 图标误识前缀，并返回需剥离行集合及对齐文字起笔 X 坐标。"""
    candidates = []
    for idx, (raw_txt, rect, crs) in enumerate(segments):
        m = _GUTTER_ICON_TOKEN_RE.match(raw_txt.strip())
        if not m:
            continue
        tok_len = len(m.group(1))
        if crs and len(crs) >= tok_len:
            prefix_w = crs[tok_len - 1].right() - rect.left() + 1
            text_left = crs[tok_len].left() if tok_len < len(crs) else rect.left() + prefix_w
        else:
            prefix_w = max(8, int(rect.width() * (tok_len / max(1, len(raw_txt)))))
            text_left = rect.left() + prefix_w
        if prefix_w <= max(20, rect.height() * 1.85):
            candidates.append((idx, rect.left(), text_left))

    flagged = {}
    for i, left_i, tleft_i in candidates:
        peers = [(j, tleft_j) for j, left_j, tleft_j in candidates if abs(left_j - left_i) <= 10 and abs(tleft_j - tleft_i) <= 14]
        if len(peers) >= 2:
            max_tl = max(p[1] for p in peers)
            for j, _tl in peers:
                flagged[j] = max_tl
    return flagged


def _strip_icon_prefix_segment(raw_txt, rect, char_rects, strip_gutter=False, min_text_left=None, image=None, bg_color=None):
    """清理前缀图标噪声并将 rect.left()/top()/bottom() 精确收敛到真实文字区，绝不盖住左侧图标。"""
    t = raw_txt or ""
    leading_match = re.match(r'^[\s\|`~^\\]+', t)
    lead_cut = len(leading_match.group(0)) if leading_match else 0
    core = t[lead_cut:]
    core = re.sub(r'[\s\|`~^\\]+$', '', core)

    stripped_icon = False
    icon_m = _LEADING_ICON_PREFIX_RE.match(core)
    if icon_m:
        lead_cut += len(icon_m.group(0))
        core = core[len(icon_m.group(0)):]
        stripped_icon = True
    elif strip_gutter:
        gm = _GUTTER_ICON_TOKEN_RE.match(core)
        if gm:
            lead_cut += len(gm.group(1))
            core = core[len(gm.group(1)):]
            stripped_icon = True

    clean_txt = core.strip()
    if not clean_txt:
        return "", QRect(rect)

    new_rect = QRect(rect)
    if lead_cut > 0:
        cand_left = new_rect.left()
        if char_rects and len(char_rects) == len(t) and lead_cut < len(char_rects):
            cand_left = max(cand_left, char_rects[lead_cut].left())
        if stripped_icon:
            # 加权字符框容易低估左侧 16~18px 方形图标宽，保证至少越过图标安全区
            est_icon_end = new_rect.left() + max(10, min(int(new_rect.height() * 1.15), int(new_rect.width() * 0.42)))
            cand_left = max(cand_left, est_icon_end)
        if min_text_left is not None:
            cand_left = max(cand_left, int(min_text_left))
        if cand_left > new_rect.left() and (new_rect.right() - cand_left + 1) >= 6:
            new_rect.setLeft(cand_left)
    return clean_txt, new_rect


def _measure_ink_line_height(image, rect, bg_color):
    """测量文字块内实际字符笔画纵向跨度，并返回精确字高（消除图标与高亮边框虚高）。"""
    fallback = float(max(1, rect.height()))
    if image is None or image.isNull():
        return fallback
    r = rect.intersected(image.rect())
    if r.width() < 4 or r.height() < 5:
        return fallback

    br, bg_g, bb = bg_color.red(), bg_color.green(), bg_color.blue()
    x_step = max(1, r.width() // 28)
    xs = list(range(r.left() + 1, r.right(), x_step))
    if not xs:
        return fallback

    min_active = max(2 if len(xs) >= 8 else 1, int(len(xs) * 0.08))
    active_ys = []
    for y in range(r.top(), r.bottom() + 1):
        hits = 0
        for x in xs:
            c = image.pixelColor(x, y)
            if max(abs(c.red() - br), abs(c.green() - bg_g), abs(c.blue() - bb)) >= 24:
                hits += 1
        if hits >= min_active:
            active_ys.append(y)

    if len(active_ys) < 4:
        return fallback
    span = float(active_ys[-1] - active_ys[0] + 1)
    return max(min(fallback, max(6.0, fallback * 0.42)), min(fallback, span))


def _tighten_rect_vertical(image, rect, bg_color):
    """若 OCR 框包含上下空白或高亮边线，将单行文字框垂直紧缩到真实字迹核心。"""
    if image is None or image.isNull() or rect.height() < 12:
        return QRect(rect)
    r = rect.intersected(image.rect())
    if r.width() < 4 or r.height() < 8:
        return QRect(rect)
    br, bg_g, bb = bg_color.red(), bg_color.green(), bg_color.blue()
    x_step = max(1, r.width() // 24)
    xs = list(range(r.left() + 1, r.right(), x_step))
    if not xs:
        return QRect(rect)
    min_active = max(2 if len(xs) >= 8 else 1, int(len(xs) * 0.08))
    active_ys = []
    for y in range(r.top(), r.bottom() + 1):
        hits = 0
        for x in xs:
            c = image.pixelColor(x, y)
            if max(abs(c.red() - br), abs(c.green() - bg_g), abs(c.blue() - bb)) >= 24:
                hits += 1
        if hits >= min_active:
            active_ys.append(y)
    if len(active_ys) >= 5:
        nt = max(r.top(), active_ys[0] - 1)
        nb = min(r.bottom(), active_ys[-1] + 1)
        if nb - nt + 1 >= 6 and (r.height() - (nb - nt + 1)) >= 3:
            out = QRect(r)
            out.setTop(nt)
            out.setBottom(nb)
            return out
    return QRect(rect)


def _bg_color_diff(c1, c2):
    return max(abs(c1.red() - c2.red()), abs(c1.green() - c2.green()), abs(c1.blue() - c2.blue()))


def _has_visual_divider_or_bg_jump(image, prev_rect, curr_rect, bg_prev, bg_curr):
    """检查两行之间是否存在背景色跳变（如选中行与未选中行）或 UI 分隔线。"""
    if image is None or image.isNull():
        return False
    if bg_prev is not None and bg_curr is not None and _bg_color_diff(bg_prev, bg_curr) > 28:
        return True

    y0 = prev_rect.bottom() + 1
    y1 = curr_rect.top() - 1
    if y1 < y0 or (y1 - y0) > 24:
        return False
    x0 = max(0, max(prev_rect.left(), curr_rect.left()))
    x1 = min(image.width() - 1, min(prev_rect.right(), curr_rect.right()))
    if x1 - x0 < 16:
        return False
    step = max(2, (x1 - x0) // 16)
    xs = list(range(x0, x1 + 1, step))
    ref = bg_curr or bg_prev or QColor(255, 255, 255)
    rr, rg, rb = ref.red(), ref.green(), ref.blue()
    for y in range(y0, min(image.height(), y1 + 1)):
        diff_cnt = 0
        for x in xs:
            c = image.pixelColor(x, y)
            if max(abs(c.red() - rr), abs(c.green() - rg), abs(c.blue() - rb)) >= 18:
                diff_cnt += 1
        if diff_cnt >= max(4, int(len(xs) * 0.60)):
            return True
    return False


def build_translation_blocks(rows, image=None):
    """按语义与版面结构切分待翻译块：剥离图标噪声、保留独立菜单/列表项、仅合并真正的连续折行正文。"""
    has_img = (image is not None and not image.isNull())
    raw_segments = []
    for row_idx, row in enumerate(rows):
        for seg_txt, seg_rect, seg_crs in _split_row_by_gaps(row):
            raw_segments.append((seg_txt, seg_rect, seg_crs, row_idx))

    gutter_map = _detect_gutter_icon_indices([(s[0], s[1], s[2]) for s in raw_segments])
    clean_items = []
    for seg_i, (seg_txt, seg_rect, seg_crs, row_idx) in enumerate(raw_segments):
        cleaned, clean_rect = _strip_icon_prefix_segment(
            seg_txt, seg_rect, seg_crs,
            strip_gutter=(seg_i in gutter_map),
            min_text_left=gutter_map.get(seg_i)
        )
        if not cleaned or not _is_meaningful_block(cleaned, clean_rect):
            continue
        bg = _sample_block_bg(image, clean_rect) if has_img else None
        if has_img:
            clean_rect = _tighten_rect_vertical(image, clean_rect, bg)
            bg = _sample_block_bg(image, clean_rect)
        ink_h = _measure_ink_line_height(image, clean_rect, bg) if has_img else float(max(1, clean_rect.height()))
        is_date_or_num = bool(re.match(r'^(?:\d{1,4}[/-]\d{1,2}|\d{1,2}:\d{2}\b)', cleaned))
        clean_items.append({
            "raw": seg_txt,
            "clean": cleaned,
            "rect": clean_rect,
            "row_idx": row_idx,
            "bg": bg,
            "ink_h": ink_h,
            "is_tabular": is_date_or_num,
            "has_row_peer": False,
        })

    # 识别横向存在其它同排独立列的表格单元（短单元或连续两行均多列并排时不跨行合并；左侧栏对右侧长段落可合并）
    for idx_a, it_a in enumerate(clean_items):
        ra = it_a["rect"]
        cy_a = ra.center().y()
        tol_y = max(6.0, ra.height() * 0.65)
        for idx_b, it_b in enumerate(clean_items):
            if idx_a == idx_b:
                continue
            rb = it_b["rect"]
            if abs(rb.center().y() - cy_a) <= tol_y:
                horiz_gap = max(rb.left() - ra.right(), ra.left() - rb.right())
                if horiz_gap >= max(10, int(ra.height() * 0.8)):
                    it_a["has_row_peer"] = True
                    it_b["has_row_peer"] = True
                    if len(it_a["clean"]) <= 28:
                        it_a["is_tabular"] = True
                    if len(it_b["clean"]) <= 28:
                        it_b["is_tabular"] = True
                    break

    grouped = []
    for item in clean_items:
        curr_rect = item["rect"]
        curr_clean = item["clean"]
        target = None
        if not item["is_tabular"]:
            for grp in reversed(grouped):
                prev_item = grp["items"][-1]
                if prev_item["is_tabular"] or (prev_item["has_row_peer"] and item["has_row_peer"]):
                    continue
                prev_rect = prev_item["rect"]
                h_ratio = curr_rect.height() / max(1.0, grp["line_height"])
                ink_ratio = item["ink_h"] / max(1.0, grp["ink_height"])
                if not (0.72 <= h_ratio <= 1.38 and 0.72 <= ink_ratio <= 1.38):
                    continue

                inter_x = max(0, min(grp["rect"].right(), curr_rect.right()) - max(grp["rect"].left(), curr_rect.left()) + 1)
                min_w = max(1, min(grp["rect"].width(), curr_rect.width()))
                overlap_ratio = inter_x / min_w
                left_aligned = abs(curr_rect.left() - grp["rect"].left()) <= max(10.0, grp["line_height"] * 0.85)
                center_aligned = abs(curr_rect.center().x() - grp["rect"].center().x()) <= max(10.0, grp["line_height"] * 0.85)
                if overlap_ratio < 0.45 or not (left_aligned or center_aligned):
                    continue

                min_h = max(4.0, min(float(prev_rect.height()), float(curr_rect.height())))
                gap = curr_rect.top() - prev_rect.bottom()
                if not (-min_h * 0.35 <= gap <= min_h * 0.95):
                    continue

                if _is_list_or_menu_boundary(prev_item["clean"], curr_clean, prev_rect, curr_rect, item["raw"]):
                    continue
                if has_img and _has_visual_divider_or_bg_jump(image, prev_rect, curr_rect, prev_item["bg"], item["bg"]):
                    continue

                target = grp
                break

        if target is not None:
            target["items"].append(item)
            target["rect"] = target["rect"].united(curr_rect)
            hs = sorted(max(1, it["rect"].height()) for it in target["items"])
            ihs = sorted(max(1.0, it["ink_h"]) for it in target["items"])
            target["line_height"] = float(hs[len(hs) // 2])
            target["ink_height"] = float(ihs[len(ihs) // 2])
        else:
            grouped.append({
                "items": [item],
                "rect": QRect(curr_rect),
                "line_height": float(max(1, curr_rect.height())),
                "ink_height": float(max(1.0, item["ink_h"])),
            })

    result = []
    for grp in grouped:
        line_pairs = [(it["clean"], it["rect"]) for it in grp["items"]]
        row_indices = []
        for it in grp["items"]:
            if it["row_idx"] not in row_indices:
                row_indices.append(it["row_idx"])
        blk = TranslationBlock(
            row_indices, grp["rect"],
            line_height=grp["line_height"],
            line_count=len(line_pairs),
            ink_height=grp["ink_height"]
        )
        blk.text = _join_block_lines(line_pairs, grp["rect"].width())
        if _is_meaningful_block(blk.text, blk.rect):
            result.append(blk)
    return result


def _sample_block_bg(image, rect):
    """严格在文字块内部四周边缘与角落采样主背景色，避免采到选中高亮框外部的白底形成“白条”。"""
    iw, ih = image.width(), image.height()
    if iw <= 0 or ih <= 0:
        return QColor(255, 255, 255, 255)

    r = rect.intersected(QRect(0, 0, iw, ih))
    if not r.isValid() or r.width() <= 0 or r.height() <= 0:
        return QColor(255, 255, 255, 255)

    xs = sorted({
        r.left(), min(r.right(), r.left() + 1),
        r.left() + r.width() // 4, r.center().x(), r.right() - r.width() // 4,
        max(r.left(), r.right() - 1), r.right(),
    })
    ys = sorted({
        r.top(), min(r.bottom(), r.top() + 1),
        r.top() + r.height() // 4, r.center().y(), r.bottom() - r.height() // 4,
        max(r.top(), r.bottom() - 1), r.bottom(),
    })
    pts = []
    for x in xs:
        pts.append((x, r.top()))
        pts.append((x, min(r.bottom(), r.top() + 1)))
        pts.append((x, r.bottom()))
        pts.append((x, max(r.top(), r.bottom() - 1)))
    for y in ys:
        pts.append((r.left(), y))
        pts.append((min(r.right(), r.left() + 1), y))
        pts.append((r.right(), y))
        pts.append((max(r.left(), r.right() - 1), y))

    samples = []
    for x, y in pts:
        if 0 <= x < iw and 0 <= y < ih:
            c = image.pixelColor(x, y)
            if c.alpha() > 0:
                samples.append((c.red(), c.green(), c.blue()))
    if not samples:
        return QColor(255, 255, 255, 255)

    buckets = {}
    for r_v, g_v, b_v in samples:
        key = (r_v >> 3, g_v >> 3, b_v >> 3)
        buckets.setdefault(key, []).append((r_v, g_v, b_v))
    dominant = max(buckets.values(), key=len)
    rs = sorted(p[0] for p in dominant)
    gs = sorted(p[1] for p in dominant)
    bs = sorted(p[2] for p in dominant)
    mid = len(dominant) // 2
    return QColor(rs[mid], gs[mid], bs[mid], 255)


def _contrast_ink_for_bg(bg: QColor):
    """按相对亮度计算高对比度前景色（深底白字 / 浅底深字）。"""
    lum = 0.2126 * bg.red() + 0.7152 * bg.green() + 0.0722 * bg.blue()
    return QColor(15, 23, 42) if lum >= 142 else QColor(248, 250, 252)


def _block_visual_height(block):
    ih = float(getattr(block, "ink_height", 0.0) or 0.0)
    lh = float(getattr(block, "line_height", block.rect.height()) or block.rect.height())
    return max(6.0, ih if ih > 0 else lh)


def _snap_peer_line_heights(blocks):
    """将全屏正文字级与紧凑列表列项统一到一致排版层级，杜绝单条因残留框边/高亮突兀放大。"""
    if not blocks:
        return {}
    heights = [_block_visual_height(b) for b in blocks]
    sorted_hs = sorted(heights)

    clusters = []
    for h in sorted_hs:
        if clusters and h <= clusters[-1][0] * 1.32:
            clusters[-1].append(h)
        else:
            clusters.append([h])

    body_cluster = max(clusters, key=len)
    body_med = body_cluster[len(body_cluster) // 2]

    tier_medians = []
    merged_body = []
    for c in clusters:
        c_med = c[len(c) // 2]
        if body_med * 0.72 <= c_med <= body_med * 1.48:
            merged_body.extend(c)
        else:
            tier_medians.append(c_med)
    if merged_body:
        merged_body.sort()
        body_med = merged_body[len(merged_body) // 2]
        tier_medians.append(body_med)
    tier_medians.sort()

    mapping = {}
    for idx, bh in enumerate(heights):
        mapping[idx] = min(tier_medians, key=lambda m: abs(m - bh))

    # 同列紧凑堆叠的列表/文件/表格项：即使单行被图标残边拉高至 2.0x 以内，仍强制归齐该列主体字号
    n = len(blocks)
    for i in range(n):
        col_group = [j for j in range(n) if abs(blocks[j].rect.left() - blocks[i].rect.left()) <= 28]
        if len(col_group) >= 3:
            sub = sorted(col_group, key=lambda k: heights[k])
            col_med = heights[sub[len(sub) // 2]]
            peers = [k for k in col_group if col_med * 0.65 <= heights[k] <= col_med * 1.95]
            if len(peers) >= 2:
                snap_val = min(tier_medians, key=lambda m: abs(m - col_med))
                for k in peers:
                    # 只有本身紧邻同列普通行（纵向间隙 <= max(26, 2.8 * col_med)）才归齐；孤立大标题保留
                    gap_lim = max(26.0, col_med * 2.8)
                    has_close_normal_peer = any(
                        p != k and heights[p] <= col_med * 1.35 and (
                            -4 <= blocks[p].rect.top() - blocks[k].rect.bottom() <= gap_lim
                            or -4 <= blocks[k].rect.top() - blocks[p].rect.bottom() <= gap_lim
                        )
                        for p in peers
                    )
                    if has_close_normal_peer or heights[k] <= col_med * 1.48:
                        mapping[k] = snap_val
    return mapping


def _free_right_extent(image, rect, bg_color, hard_right_limit):
    """逐列精准扫描右侧全高像素带，碰到细图钉(📌)/1px分割线/边框立即止步。"""
    if image is None or image.isNull() or hard_right_limit <= rect.right():
        return min(hard_right_limit, rect.right())
    y0 = max(0, rect.top())
    y1 = min(image.height() - 1, rect.bottom())
    if y1 < y0:
        return rect.right()
    step_y = 1 if (y1 - y0 + 1) <= 40 else max(1, (y1 - y0 + 1) // 32)
    ys = list(range(y0, y1 + 1, step_y))
    br, bg_g, bb = bg_color.red(), bg_color.green(), bg_color.blue()

    last_safe = rect.right()
    start_x = min(image.width() - 1, rect.right() + 1)
    end_x = min(image.width() - 1, hard_right_limit)
    for x in range(start_x, end_x + 1):
        bad_cnt = 0
        for y in ys:
            c = image.pixelColor(x, y)
            if max(abs(c.red() - br), abs(c.green() - bg_g), abs(c.blue() - bb)) >= 18:
                bad_cnt += 1
                if bad_cnt > 1:
                    break
        if bad_cnt <= 1:
            last_safe = x
        else:
            break
    if last_safe > rect.right() + 4:
        return last_safe - 4
    return rect.right()


def _pixel_size_for_visual_height(target_visual_h, min_px=10, max_px=96):
    """通过真实无下行部字形 tightBoundingRect('国A') 标定 pixelSize，使中文译文字高与原图自然齐平。"""
    target = max(8.0, float(target_visual_h))
    lo = max(min_px, int(round(target * 0.90)))
    hi = min(max_px, max(lo + 6, int(round(target * 1.85))))
    best_px = lo
    best_diff = 1e9
    for px in range(lo, hi + 1):
        fm = QFontMetricsF(make_ui_font(pixel_size=px))
        gh = fm.tightBoundingRect("国A").height()
        diff = abs(gh - target)
        if diff < best_diff:
            best_diff = diff
            best_px = px
    return best_px


def _fit_translation_box(rect, obstacles, canvas_rect, trans, line_h, line_count, scale,
                         source_image=None, bg_color=None):
    """
    计算原位覆盖译文盒与精确字号：
    1. 用视觉字高标定字号，与同级文字保持统一可读高度；
    2. 水平扩展同时受邻居文本块及原图像素障碍物（头像/图标/边框）双约束；
    3. 单行标签垂直方向保持紧致贴合行高，绝不膨胀遮盖上下相邻行。
    """
    is_single_line = (line_count <= 1 and "\n" not in trans)
    pad_x = max(2, int(round(3 * scale)))
    pad_y = 0 if is_single_line else max(1, int(round(2 * scale)))
    gap = max(2, int(round(3 * scale)))

    target_visual_h = max(9.5, float(line_h) * 1.12) * scale
    ideal_px = _pixel_size_for_visual_height(
        target_visual_h,
        min_px=max(10, int(round(10 * scale))),
        max_px=max(24, int(round(72 * scale))),
    )
    min_px = max(8, int(round(8 * scale)))

    max_right = canvas_rect.right() - 2
    max_bottom = canvas_rect.bottom() - 2
    for ob in obstacles:
        if ob.bottom() >= rect.top() + 2 and ob.top() <= rect.bottom() - 2:
            if ob.left() >= rect.right() - 2:
                max_right = min(max_right, ob.left() - gap)
        if ob.right() >= rect.left() + 2 and ob.left() <= rect.right() - 2:
            if ob.top() >= rect.bottom() - 2:
                max_bottom = min(max_bottom, ob.top() - gap)

    if source_image is not None and bg_color is not None:
        max_right = min(max_right, _free_right_extent(source_image, rect, bg_color, max_right))

    max_right = max(rect.right(), min(max_right, rect.left() + max(int(rect.width() * 2.4), rect.width() + int(180 * scale))))
    if is_single_line:
        single_v_allowance = rect.height() + max(2, int(round(3 * scale)))
        max_bottom = max(rect.bottom(), min(max_bottom, rect.top() + single_v_allowance - 1))
    else:
        max_bottom = max(rect.bottom(), min(max_bottom, rect.top() + max(int(rect.height() * 1.75), rect.height() + int(36 * scale))))

    avail_max_w = max(rect.width(), max_right - rect.left() + 1)
    avail_max_h = max(rect.height(), max_bottom - rect.top() + 1)

    def _measure(size_px, box_w, flags):
        f = make_ui_font(pixel_size=size_px)
        inner_w = max(8, box_w - pad_x * 2)
        br = QFontMetrics(f).boundingRect(QRect(0, 0, inner_w, 10000), flags, trans)
        return f, br

    wrap_flags = Qt.TextWordWrap
    chosen_font = None
    chosen_w = rect.width()
    chosen_h = rect.height()
    used_flags = wrap_flags

    if is_single_line:
        f_ideal, br_ideal = _measure(ideal_px, avail_max_w, Qt.TextSingleLine)
        need_w = br_ideal.width() + pad_x * 2 + 2
        need_glyph_h = int(round(ideal_px * 1.15))
        if need_w <= avail_max_w and need_glyph_h <= avail_max_h:
            chosen_font = f_ideal
            chosen_w = max(rect.width(), need_w)
            chosen_h = max(rect.height(), min(avail_max_h, max(rect.height(), need_glyph_h + 2)))
            used_flags = Qt.TextSingleLine
    else:
        f_ideal, br_ideal = _measure(ideal_px, rect.width(), wrap_flags)
        if br_ideal.height() + pad_y * 2 <= avail_max_h and br_ideal.width() + pad_x * 2 <= rect.width():
            chosen_font = f_ideal
            chosen_w = rect.width()
            chosen_h = max(rect.height(), br_ideal.height() + pad_y * 2)
        elif avail_max_w > rect.width():
            f_exp, br_exp = _measure(ideal_px, avail_max_w, wrap_flags)
            if br_exp.height() + pad_y * 2 <= avail_max_h and br_exp.width() + pad_x * 2 <= avail_max_w:
                chosen_font = f_exp
                chosen_w = max(rect.width(), min(avail_max_w, br_exp.width() + pad_x * 2 + 2))
                chosen_h = max(rect.height(), br_exp.height() + pad_y * 2)

    if chosen_font is None:
        for size in range(ideal_px, min_px - 1, -1):
            if is_single_line:
                f_try, br_try = _measure(size, avail_max_w, Qt.TextSingleLine)
                need_w = br_try.width() + pad_x * 2 + 2
                need_gh = int(round(size * 1.15))
                if need_w <= avail_max_w and need_gh <= avail_max_h:
                    chosen_font = f_try
                    chosen_w = max(rect.width(), need_w)
                    chosen_h = max(rect.height(), min(avail_max_h, need_gh + 2))
                    used_flags = Qt.TextSingleLine
                    break
            f_try, br_try = _measure(size, avail_max_w, wrap_flags)
            if br_try.height() + pad_y * 2 <= avail_max_h and br_try.width() + pad_x * 2 <= avail_max_w:
                chosen_font = f_try
                chosen_w = max(rect.width(), min(avail_max_w, br_try.width() + pad_x * 2 + 2))
                chosen_h = max(rect.height(), br_try.height() + pad_y * 2)
                used_flags = wrap_flags
                break

    if chosen_font is None:
        used_flags = Qt.TextWordWrap | Qt.TextWrapAnywhere
        chosen_font, chosen_br = _measure(min_px, avail_max_w, used_flags)
        chosen_w = avail_max_w
        chosen_h = max(rect.height(), min(avail_max_h, chosen_br.height() + pad_y * 2))

    cover_left = max(0, int(round(0.5 * scale))) if is_single_line else max(1, int(round(scale)))
    cover_right = max(1, int(round(scale)))
    cover_y = 0 if is_single_line else max(1, int(round(1 * scale)))
    bx0 = max(0, rect.left() - cover_left)
    by0 = max(0, rect.top() - cover_y)
    bx1 = min(canvas_rect.right(), rect.left() + chosen_w - 1 + cover_right)
    by1 = min(canvas_rect.bottom(), rect.top() + chosen_h - 1 + cover_y)
    box = QRect(bx0, by0, max(1, bx1 - bx0 + 1), max(1, by1 - by0 + 1)).intersected(canvas_rect)
    align_v = Qt.AlignVCenter if is_single_line else Qt.AlignTop
    return box, chosen_font, used_flags | Qt.AlignLeft | align_v, pad_x, pad_y


def _sample_band_bg(image, rect, y_rows):
    """采样指定水平行带内的主背景簇，用于识别顶部/底部细微渐变（如选中蓝条）。"""
    iw, ih = image.width(), image.height()
    r = rect.intersected(QRect(0, 0, iw, ih))
    xs = sorted({
        r.left(), min(r.right(), r.left() + 1),
        r.left() + r.width() // 4, r.center().x(), r.right() - r.width() // 4,
        max(r.left(), r.right() - 1), r.right(),
    })
    samples = []
    for y in y_rows:
        if 0 <= y < ih:
            for x in xs:
                if 0 <= x < iw:
                    c = image.pixelColor(x, y)
                    if c.alpha() > 0:
                        samples.append((c.red(), c.green(), c.blue()))
    if not samples:
        return None
    buckets = {}
    for rv, gv, bv in samples:
        buckets.setdefault((rv >> 3, gv >> 3, bv >> 3), []).append((rv, gv, bv))
    dom = max(buckets.values(), key=len)
    mid = len(dom) // 2
    return QColor(
        sorted(p[0] for p in dom)[mid],
        sorted(p[1] for p in dom)[mid],
        sorted(p[2] for p in dom)[mid],
        255,
    )


def _draw_translated_item(painter, item, box_f, erase_f, radius, font, pad_x, pad_y, hovered=False):
    """共用的译文块绘制器：仅擦除原外文像素足迹（支持渐变融入），避免长白条盖住周围底色。"""
    painter.setPen(Qt.NoPen)
    bg_top = item.get("bg_top")
    bg_bot = item.get("bg_bot")
    if bg_top is not None and bg_bot is not None and _bg_color_diff(bg_top, bg_bot) >= 3:
        grad = QLinearGradient(erase_f.topLeft(), erase_f.bottomLeft())
        grad.setColorAt(0.0, bg_top)
        grad.setColorAt(1.0, bg_bot)
        painter.setBrush(QBrush(grad))
    else:
        painter.setBrush(item["bg"])
    painter.drawRoundedRect(erase_f, radius, radius)

    painter.setFont(font)
    painter.setPen(item["fg"])
    text_rect = box_f.adjusted(pad_x, pad_y, -pad_x, -pad_y)
    if text_rect.width() >= 2.0 and text_rect.height() >= 2.0:
        painter.save()
        painter.setClipRect(box_f.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.drawText(text_rect, item["draw_flags"], item["trans"])
        painter.restore()

    if hovered:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#2563EB"), 1.5))
        painter.drawRoundedRect(box_f.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def _compute_block_layouts(source_image, blocks, translations, scale=1.0):
    """统一求解全屏所有译文块的位置、尺寸、字体与配色，供实时矢量画布与导出 PNG 复用。"""
    if source_image is None or source_image.isNull() or not blocks:
        return []
    canvas_rect = QRect(0, 0, source_image.width(), source_image.height())
    scaled_rects = [
        QRect(int(round(b.rect.left() * scale)), int(round(b.rect.top() * scale)),
              max(1, int(round(b.rect.width() * scale))), max(1, int(round(b.rect.height() * scale))))
        for b in blocks
    ]
    snapped_heights = _snap_peer_line_heights(blocks)
    render_order = sorted(range(len(blocks)), key=lambda i: (blocks[i].rect.top(), blocks[i].rect.left()))

    placed_boxes = []
    layouts = []
    for idx in render_order:
        if idx >= len(translations):
            continue
        block = blocks[idx]
        trans = (translations[idx] or "").strip()
        if not trans or trans == block.text.strip():
            continue
        if block.rect.width() < 8 or block.rect.height() < 7:
            continue
        rect = scaled_rects[idx].intersected(canvas_rect)
        if rect.width() < 6 or rect.height() < 5:
            continue

        bg = _sample_block_bg(source_image, rect)
        bg.setAlpha(255)
        bg_top = _sample_band_bg(source_image, rect, [rect.top(), min(rect.bottom(), rect.top() + 1)]) or bg
        bg_bot = _sample_band_bg(source_image, rect, [max(rect.top(), rect.bottom() - 1), rect.bottom()]) or bg
        if _bg_color_diff(bg_top, bg) > 36:
            bg_top = bg
        if _bg_color_diff(bg_bot, bg) > 36:
            bg_bot = bg
        fg = _contrast_ink_for_bg(bg)

        obstacles = [scaled_rects[j] for j in range(len(scaled_rects)) if j != idx and scaled_rects[j].width() > 2]
        obstacles.extend(placed_boxes)

        target_h = snapped_heights.get(idx, _block_visual_height(block))
        line_count = int(getattr(block, "line_count", 1))
        box, font, draw_flags, pad_x, pad_y = _fit_translation_box(
            rect, obstacles, canvas_rect, trans, target_h, line_count, scale,
            source_image=source_image, bg_color=bg
        )
        placed_boxes.append(box)

        # 仅在原本外文字符占据的范围（左右微留抗锯齿缓冲）涂背景色，向右扩出的干净空白保持透明不拉白条
        pad_r = max(2, int(round(3 * scale)))
        is_single = (line_count <= 1 and "\n" not in trans)
        erase_y = 0 if is_single else max(1, int(round(scale)))
        erase_box = QRect(
            max(box.left(), rect.left() - max(0, int(round(0.5 * scale)))),
            max(box.top(), rect.top() - erase_y),
            max(1, min(box.right(), rect.right() + pad_r) - max(box.left(), rect.left() - max(0, int(round(0.5 * scale)))) + 1),
            max(1, min(box.bottom(), rect.bottom() + erase_y) - max(box.top(), rect.top() - erase_y) + 1),
        ).intersected(box)

        layouts.append({
            "index": idx,
            "orig": block.text,
            "trans": trans,
            "orig_rect": rect,
            "box": box,
            "erase_box": erase_box,
            "font": font,
            "font_px": max(8, font.pixelSize()),
            "draw_flags": draw_flags,
            "pad_x": pad_x,
            "pad_y": pad_y,
            "bg": bg,
            "bg_top": bg_top,
            "bg_bot": bg_bot,
            "fg": fg,
        })
    return layouts


def _render_translation_sheet(base_pixmap, blocks, translations, scale=1.0):
    """备用两栏译文卡片布局（保留给显式调用，不再覆盖常规原位排版）。"""
    width = base_pixmap.width()
    sw = max(1, int(width * scale))
    base_h = max(1, int(base_pixmap.height() * scale))
    margin = max(8, int(12 * scale))
    gap = max(8, int(12 * scale))
    col_w = max(int(120 * scale), (sw - margin * 2 - gap) // 2)
    cursors = {0: margin, 1: margin}
    layouts = []

    order = sorted(range(len(blocks)), key=lambda i: (blocks[i].rect.top(), blocks[i].rect.left()))
    for idx in order:
        if idx >= len(translations):
            continue
        block = blocks[idx]
        trans = (translations[idx] or "").strip()
        if not trans or trans == block.text.strip():
            continue
        col = 0 if block.rect.center().x() < width // 2 else 1
        size = int((12 if getattr(block, "line_height", block.rect.height()) >= 28 else 11) * scale)
        font = make_ui_font(pixel_size=max(7, size))
        inner_w = max(40, col_w - int(12 * scale))
        bound = QFontMetrics(font).boundingRect(QRect(0, 0, inner_w, 10000), Qt.TextWordWrap, trans)
        h = max(int(28 * scale), bound.height() + int(12 * scale))
        y = max(int(block.rect.top() * scale), cursors[col])
        x = margin + col * (col_w + gap)
        layouts.append((QRect(x, y, col_w, h), font, trans))
        cursors[col] = y + h + int(8 * scale)

    final_h = max(base_h, max(cursors.values()) + margin)
    canvas = QPixmap(sw, final_h)
    canvas.fill(QColor(250, 251, 252))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setOpacity(0.14)
    painter.drawPixmap(QRect(0, 0, sw, base_h), base_pixmap,
                       QRect(0, 0, base_pixmap.width(), base_pixmap.height()))
    painter.setOpacity(1.0)

    for rect, font, text in layouts:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 246))
        painter.drawRoundedRect(rect, max(3, int(5 * scale)), max(3, int(5 * scale)))
        painter.setPen(QColor(15, 23, 42))
        painter.setFont(font)
        pad_x = max(4, int(7 * scale))
        pad_y = max(3, int(5 * scale))
        painter.drawText(rect.adjusted(pad_x, pad_y, -pad_x, -pad_y),
                         Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, text)
    painter.end()
    return canvas


def render_translated_pixmap(base_pixmap, blocks, translations):
    """按原生清晰分辨率原位覆盖译文：小截图整数 2x 放大，正常/大图保持 1:1 零插值模糊并矢量绘字。"""
    if base_pixmap is None or base_pixmap.isNull():
        return QPixmap()
    width = base_pixmap.width()
    height = base_pixmap.height()

    scale = 2 if (width < 480 or height < 340) else 1
    if scale > 1 and max(width, height) * scale > 3200:
        scale = 1

    if scale > 1:
        result = base_pixmap.scaled(width * scale, height * scale, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    else:
        result = QPixmap(base_pixmap)
    source_image = result.toImage()
    layouts = _compute_block_layouts(source_image, blocks, translations, scale=float(scale))

    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    radius = max(1.5, float(scale))
    for item in layouts:
        box_f = QRectF(item["box"])
        erase_f = QRectF(item.get("erase_box", item["box"]))
        _draw_translated_item(
            painter, item, box_f, erase_f, radius,
            item["font"], float(item["pad_x"]), float(item["pad_y"]), hovered=False
        )

    painter.end()
    return result

def _rescale_ocr_rows(rows, src_size, dst_size):
    """若 OCR 尺寸与显示底图尺寸存在缩放差异，先统一行框及逐字符坐标，便于精确像素采样。"""
    if (not src_size.isValid() or src_size.width() <= 0 or src_size.height() <= 0
            or not dst_size.isValid() or dst_size.width() <= 0 or dst_size.height() <= 0):
        return list(rows)
    if src_size.width() == dst_size.width() and src_size.height() == dst_size.height():
        return list(rows)
    sx = dst_size.width() / float(src_size.width())
    sy = dst_size.height() / float(src_size.height())

    def _map_r(r):
        return QRect(
            int(round(r.left() * sx)), int(round(r.top() * sy)),
            max(1, int(round(r.width() * sx))), max(1, int(round(r.height() * sy)))
        )

    out = []
    for row in rows:
        out.append(OcrRow(row.text, _map_r(row.rect), [_map_r(cr) for cr in (row.char_rects or [])]))
    return out


class _TranslationCanvasSurface(QWidget):
    """高分屏原生实时矢量译文画布：底图保持零模糊像素级渲染，译文直接在屏幕 DPI 上矢量绘制。"""

    block_copied = pyqtSignal(str)
    zoom_requested = pyqtSignal(float, QPoint)

    def __init__(self, pixmap, interactive=False, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap(pixmap) if pixmap is not None else QPixmap()
        self._interactive = interactive
        self._layouts = []
        self._zoom = 1.0
        self._show_overlay = True
        self._hover_idx = -1
        self._press_pos = None
        self._dragged = False
        self.setMouseTracking(interactive)
        self._sync_size()

    def set_pixmap(self, pixmap):
        self._pixmap = QPixmap(pixmap) if pixmap is not None else QPixmap()
        self._sync_size()
        self.update()

    def set_layouts(self, layouts):
        self._layouts = list(layouts or [])
        self._hover_idx = -1
        self.update()

    def set_show_overlay(self, show):
        self._show_overlay = bool(show)
        self.update()

    def set_zoom(self, zoom):
        z = max(0.15, min(4.0, float(zoom)))
        if abs(z - self._zoom) < 1e-4:
            return
        self._zoom = z
        self._sync_size()
        self.update()

    def _sync_size(self):
        if self._pixmap is None or self._pixmap.isNull():
            self.resize(100, 100)
            return
        w = max(1, int(round(self._pixmap.width() * self._zoom)))
        h = max(1, int(round(self._pixmap.height() * self._zoom)))
        self.setFixedSize(w, h)

    def _layout_at_pos(self, pos):
        if not self._interactive or not self._show_overlay or not self._layouts or self._zoom <= 0:
            return -1
        ix = pos.x() / self._zoom
        iy = pos.y() / self._zoom
        for i in range(len(self._layouts) - 1, -1, -1):
            box = self._layouts[i]["box"]
            if box.left() <= ix <= box.right() + 1 and box.top() <= iy <= box.bottom() + 1:
                return i
        return -1

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        new_z = max(0.15, min(4.0, self._zoom * factor))
        self.zoom_requested.emit(new_z, event.pos())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._interactive:
            idx = self._layout_at_pos(event.pos())
            if idx != self._hover_idx:
                self._hover_idx = idx
                if idx >= 0:
                    item = self._layouts[idx]
                    QToolTip.showText(
                        event.globalPos(),
                        f"原文：{item['orig']}\n译文：{item['trans']}\n（点击可复制此段译文）",
                        self,
                    )
                else:
                    QToolTip.hideText()
                self.update()
        event.ignore()

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._interactive and event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
            self._dragged = False
        event.ignore()

    def mouseReleaseEvent(self, event):
        if (self._interactive and event.button() == Qt.LeftButton and self._press_pos is not None
                and (event.pos() - self._press_pos).manhattanLength() <= 5):
            idx = self._layout_at_pos(event.pos())
            if idx >= 0:
                trans = self._layouts[idx]["trans"]
                QApplication.clipboard().setText(trans)
                self.block_copied.emit(trans)
                event.accept()
                return
        event.ignore()

    def paintEvent(self, event):
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter = QPainter(self)
        z = self._zoom
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        # 大于等于 100% 时禁用平滑插值，保证截图像素锐利无雾感；小于 100% 缩小时开平滑防锯齿
        painter.setRenderHint(QPainter.SmoothPixmapTransform, z < 0.995)

        target_rect = QRectF(0.0, 0.0, self._pixmap.width() * z, self._pixmap.height() * z)
        painter.drawPixmap(target_rect, self._pixmap, QRectF(0.0, 0.0, self._pixmap.width(), self._pixmap.height()))

        if self._show_overlay and self._layouts:
            radius = max(1.0, 1.5 * z)
            for idx, item in enumerate(self._layouts):
                box = item["box"]
                ebox = item.get("erase_box", box)
                zbox = QRectF(box.left() * z, box.top() * z, box.width() * z, box.height() * z)
                zerase = QRectF(ebox.left() * z, ebox.top() * z, ebox.width() * z, ebox.height() * z)

                # 直接按目标逻辑字号使用矢量 pt 绘制，在任何缩放倍率与 HiDPI 屏幕下都清晰锐利
                f = make_ui_font()
                f.setPointSizeF(max(5.5, float(item["font_px"]) * z * 0.75))
                _draw_translated_item(
                    painter, item, zbox, zerase, radius,
                    f, item["pad_x"] * z, item["pad_y"] * z, hovered=(idx == self._hover_idx)
                )

        painter.end()


class _TranslationCanvasView(QScrollArea):
    """支持同步缩放与左键拖拽平移的滚动画布容器。"""

    zoom_changed = pyqtSignal(float)

    def __init__(self, pixmap, interactive=False, parent=None):
        super().__init__(parent)
        self.setObjectName("transCanvasScroll")
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self.setFrameShape(QFrame.NoFrame)
        self.surface = _TranslationCanvasSurface(pixmap, interactive=interactive)
        self.setWidget(self.surface)
        self._panning = False
        self._pan_start = QPoint()
        self._pan_scroll_x = 0
        self._pan_scroll_y = 0
        self.viewport().setCursor(Qt.OpenHandCursor)
        self.surface.zoom_requested.connect(self._on_surface_zoom_request)

    def calc_fit_zoom(self):
        pm = self.surface._pixmap
        if pm is None or pm.isNull():
            return 1.0
        vw = max(120, self.viewport().width() - 12)
        vh = max(100, self.viewport().height() - 12)
        z = min(vw / max(1, pm.width()), vh / max(1, pm.height()))
        return max(0.15, min(2.0, z))

    def set_zoom(self, zoom, anchor_surface_pos=None):
        old_z = self.surface._zoom
        new_z = max(0.15, min(4.0, float(zoom)))
        if abs(new_z - old_z) < 1e-4:
            return
        if anchor_surface_pos is not None and old_z > 0:
            vp_pos = self.surface.mapTo(self.viewport(), anchor_surface_pos)
            img_x = anchor_surface_pos.x() / old_z
            img_y = anchor_surface_pos.y() / old_z
            self.surface.set_zoom(new_z)
            self.horizontalScrollBar().setValue(int(round(img_x * new_z - vp_pos.x())))
            self.verticalScrollBar().setValue(int(round(img_y * new_z - vp_pos.y())))
        else:
            self.surface.set_zoom(new_z)
        self.zoom_changed.emit(new_z)

    def _on_surface_zoom_request(self, new_z, surface_pos):
        self.set_zoom(new_z, anchor_surface_pos=surface_pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._panning = True
            self._pan_start = event.pos()
            self._pan_scroll_x = self.horizontalScrollBar().value()
            self._pan_scroll_y = self.verticalScrollBar().value()
            self.viewport().setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._pan_start
            self.horizontalScrollBar().setValue(self._pan_scroll_x - delta.x())
            self.verticalScrollBar().setValue(self._pan_scroll_y - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() == Qt.LeftButton:
            self._panning = False
            self.viewport().setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)


class TranslationResultWindow(QDialog):
    """
    一键翻译结果窗口：
    - 原生高分屏矢量画布（支持滚轮缩放 / 1:1 / 适应窗口 / 拖拽平移 / 双栏同步联动 / 点击复制单段译文）；
    - 支持「对比原图」透视与导出原生无损分辨率译文图。
    """

    def __init__(self, original_pixmap, doc, target_lang, use_ai=False, mode="compare", parent=None):
        super().__init__(parent)
        self.setWindowTitle("翻译结果")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setObjectName("transResultDialog")

        self._original_pm = QPixmap(original_pixmap)
        self._translated_pm = QPixmap(original_pixmap)
        self._original_img = self._original_pm.toImage() if not self._original_pm.isNull() else QImage()
        self.doc = doc
        scaled_rows = _rescale_ocr_rows(doc.rows, doc.image_size, self._original_pm.size())
        self.rows = scaled_rows
        self.blocks = build_translation_blocks(scaled_rows, image=self._original_img)
        self._layouts = []

        self.target_lang = target_lang
        self.use_ai = use_ai
        self.mode = "overlay" if mode == "overlay" else "compare"
        self.translations = ["" for _ in self.blocks]
        self._workers = []
        self._worker = None
        self._closed = False
        self._syncing_view = False
        self._initial_zoom_applied = False
        self.content = None
        self.view_original = None
        self.view_translated = None

        screen = QApplication.primaryScreen().availableGeometry()
        pw, ph = self._original_pm.width(), self._original_pm.height()
        if self.mode == "compare":
            content_w = max(920, pw * 2.0 + 120)
            content_h = max(560, ph + 148)
        else:
            content_w = max(680, pw + 96)
            content_h = max(480, ph + 132)
        self.resize(min(int(screen.width() * 0.94), int(content_w)),
                    min(int(screen.height() * 0.90), int(content_h)))
        self.setMinimumSize(640, 420)

        self._setup_ui()
        self._apply_style()
        QTimer.singleShot(40, self._start_translation)

    def _set_status(self, text, tone="info"):
        palettes = {
            "info": ("#FBF7EE", "#7A5110", "#E5D2AE"),
            "ok":   ("#F0FDF4", "#15803D", "#BBF7D0"),
            "warn": ("#FFFBEB", "#B45309", "#FDE68A"),
            "err":  ("#FEF2F2", "#DC2626", "#FECACA"),
        }
        bg, fg, bd = palettes.get(tone, palettes["info"])
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(
            f"QLabel#transStatus {{ background-color: {bg}; color: {fg}; border: 1px solid {bd}; "
            f"border-radius: 6px; padding: 3px 10px; font-size: 11.5px; font-weight: 600; }}"
        )

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        top_bar = QWidget()
        top_bar.setObjectName("codexTopBar")
        bar = QHBoxLayout(top_bar)
        bar.setContentsMargins(16, 10, 16, 10)
        bar.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_create_codex_icon("ai", "#0D0D0D", 16).pixmap(16, 16))
        icon_lbl.setFixedSize(18, 18)
        bar.addWidget(icon_lbl)

        title = QLabel("双栏对照" if self.mode == "compare" else "覆盖翻译")
        title.setObjectName("codexTitle")
        self.lbl_status = QLabel("正在翻译…")
        self.lbl_status.setObjectName("transStatus")
        self._set_status("正在翻译…", "info")

        bar.addWidget(title)
        bar.addWidget(self.lbl_status)
        bar.addStretch()

        # 缩放与对比工具组
        self.btn_fit = QPushButton("适应")
        self.btn_fit.setObjectName("codexZoomBtn")
        self.btn_fit.setCursor(Qt.PointingHandCursor)
        self.btn_fit.setToolTip("缩放图片以完整适配当前窗口")
        self.btn_fit.clicked.connect(self._fit_zoom)
        bar.addWidget(self.btn_fit)

        self.btn_1to1 = QPushButton("1:1")
        self.btn_1to1.setObjectName("codexZoomBtn")
        self.btn_1to1.setCursor(Qt.PointingHandCursor)
        self.btn_1to1.setToolTip("按 100% 原始像素尺寸显示（最清晰）")
        self.btn_1to1.clicked.connect(lambda: self._apply_zoom_all(1.0))
        bar.addWidget(self.btn_1to1)

        btn_zoom_out = QPushButton("-")
        btn_zoom_out.setObjectName("codexZoomBtn")
        btn_zoom_out.setFixedWidth(26)
        btn_zoom_out.setCursor(Qt.PointingHandCursor)
        btn_zoom_out.clicked.connect(lambda: self._step_zoom(1.0 / 1.18))
        bar.addWidget(btn_zoom_out)

        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("codexSubtitle")
        self.lbl_zoom.setMinimumWidth(40)
        self.lbl_zoom.setAlignment(Qt.AlignCenter)
        bar.addWidget(self.lbl_zoom)

        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setObjectName("codexZoomBtn")
        btn_zoom_in.setFixedWidth(26)
        btn_zoom_in.setCursor(Qt.PointingHandCursor)
        btn_zoom_in.clicked.connect(lambda: self._step_zoom(1.18))
        bar.addWidget(btn_zoom_in)

        self.btn_peek = QPushButton("对比原图")
        self.btn_peek.setObjectName("codexZoomBtn")
        self.btn_peek.setCheckable(True)
        self.btn_peek.setCursor(Qt.PointingHandCursor)
        self.btn_peek.setToolTip("临时隐藏译文覆盖层以核对原图")
        self.btn_peek.toggled.connect(self._on_peek_toggled)
        bar.addWidget(self.btn_peek)

        lbl_lang = QLabel("目标")
        lbl_lang.setObjectName("codexSubtitle")
        bar.addWidget(lbl_lang)

        self.combo_lang = CodexComboBox()
        for code, label in SUPPORTED_LANGUAGES:
            self.combo_lang.addItem(label, code)
        idx = self.combo_lang.findData(self.target_lang)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)
        self.combo_lang.currentIndexChanged.connect(self._on_lang_changed)
        self.combo_lang.setMinimumWidth(148)
        bar.addWidget(self.combo_lang)

        self.btn_copy_text = QPushButton("复制文本")
        self.btn_copy_text.setObjectName("codexSecondaryBtn")
        self.btn_copy_text.setCursor(Qt.PointingHandCursor)
        self.btn_copy_text.clicked.connect(self._copy_all_text)
        bar.addWidget(self.btn_copy_text)

        self.btn_copy = QPushButton("复制图片")
        self.btn_copy.setObjectName("codexSecondaryBtn")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_image)
        bar.addWidget(self.btn_copy)

        self.btn_save = QPushButton("保存图片")
        self.btn_save.setObjectName("codexSecondaryBtn")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_image)
        bar.addWidget(self.btn_save)

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("codexPrimaryBtn")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.close)
        bar.addWidget(btn_close)
        main.addWidget(top_bar)

        self.content = QWidget()
        self.content.setObjectName("transBody")
        content_layout = QHBoxLayout(self.content)
        content_layout.setContentsMargins(16, 14, 16, 14)
        content_layout.setSpacing(12)

        if self.mode == "compare":
            left_card, self.view_original = self._make_card("原图（支持滚轮缩放 / 拖拽联动）", interactive=False)
            right_card, self.view_translated = self._make_card("译文图（矢量清晰 · 悬停看原文 / 点击复制）", interactive=True)
            content_layout.addWidget(left_card, 1)
            content_layout.addWidget(right_card, 1)
            self._wire_view_sync(self.view_original, self.view_translated)
        else:
            card, self.view_translated = self._make_card("译文图（滚轮缩放 / 悬停看原文 / 点击复制）", interactive=True)
            content_layout.addWidget(card, 1)
            self.view_translated.zoom_changed.connect(self._update_zoom_label)

        self.view_translated.surface.block_copied.connect(
            lambda txt: self._set_status(f"已复制：{txt[:18]}{'…' if len(txt) > 18 else ''}", "ok")
        )
        main.addWidget(self.content, 1)

    def _make_card(self, title_text, interactive=False):
        card = QFrame()
        card.setObjectName("codexCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        head.setObjectName("transCardHead")
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(14, 8, 14, 8)
        title = QLabel(title_text)
        title.setObjectName("transCardTitle")
        head_lay.addWidget(title)
        head_lay.addStretch()
        lay.addWidget(head)

        stage = QWidget()
        stage.setObjectName("transStageWrap")
        stage_lay = QVBoxLayout(stage)
        stage_lay.setContentsMargins(8, 8, 8, 8)
        view = _TranslationCanvasView(self._original_pm, interactive=interactive)
        stage_lay.addWidget(view, 1)
        lay.addWidget(stage, 1)
        return card, view

    def _wire_view_sync(self, v1, v2):
        def _sync_zoom(src, dst, z):
            self._update_zoom_label(z)
            if self._syncing_view:
                return
            self._syncing_view = True
            try:
                dst.surface.set_zoom(z)
            finally:
                self._syncing_view = False

        def _sync_sb(src_bar, dst_bar, val):
            if self._syncing_view:
                return
            self._syncing_view = True
            try:
                dst_bar.setValue(val)
            finally:
                self._syncing_view = False

        v1.zoom_changed.connect(lambda z: _sync_zoom(v1, v2, z))
        v2.zoom_changed.connect(lambda z: _sync_zoom(v2, v1, z))
        v1.horizontalScrollBar().valueChanged.connect(lambda val: _sync_sb(v1.horizontalScrollBar(), v2.horizontalScrollBar(), val))
        v2.horizontalScrollBar().valueChanged.connect(lambda val: _sync_sb(v2.horizontalScrollBar(), v1.horizontalScrollBar(), val))
        v1.verticalScrollBar().valueChanged.connect(lambda val: _sync_sb(v1.verticalScrollBar(), v2.verticalScrollBar(), val))
        v2.verticalScrollBar().valueChanged.connect(lambda val: _sync_sb(v2.verticalScrollBar(), v1.verticalScrollBar(), val))

    def _apply_style(self):
        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + """
            QWidget#transBody {
                background-color: #FCFCFD;
            }
            QWidget#transCardHead {
                background-color: #FAFAFA;
                border-bottom: 1px solid #F1F1F5;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QLabel#transCardTitle {
                font-size: 12px;
                font-weight: 600;
                color: #0D0D0D;
                background: transparent;
            }
            QWidget#transStageWrap {
                background-color: #FFFFFF;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
            }
            QScrollArea#transCanvasScroll {
                background-color: #F4F4F5;
                border: 1px solid #ECECEF;
                border-radius: 7px;
            }
            QPushButton#codexZoomBtn {
                background-color: #FFFFFF;
                color: #27272A;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11.5px;
                font-weight: 500;
            }
            QPushButton#codexZoomBtn:hover {
                background-color: #F4F4F5;
            }
            QPushButton#codexZoomBtn:checked {
                background-color: #EEF2FF;
                color: #2563EB;
                border-color: #93C5FD;
            }
        """)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_zoom_applied:
            self._initial_zoom_applied = True
            QTimer.singleShot(0, self._init_smart_zoom)

    def _init_smart_zoom(self):
        if self._closed or self.view_translated is None:
            return
        fit_z = self.view_translated.calc_fit_zoom()
        # 能完整展示时默认 1:1 锐利显示，超宽/超高大图默认适应窗口（译文依然矢量高清）
        init_z = 1.0 if fit_z >= 0.92 else fit_z
        self._apply_zoom_all(init_z)

    def _update_zoom_label(self, z):
        if hasattr(self, "lbl_zoom") and self.lbl_zoom is not None:
            self.lbl_zoom.setText(f"{int(round(z * 100))}%")

    def _apply_zoom_all(self, z):
        z = max(0.15, min(4.0, float(z)))
        if self.view_translated is not None:
            self.view_translated.set_zoom(z)
        if self.view_original is not None:
            self.view_original.set_zoom(z)
        self._update_zoom_label(z)

    def _fit_zoom(self):
        if self.view_translated is not None:
            self._apply_zoom_all(self.view_translated.calc_fit_zoom())

    def _step_zoom(self, factor):
        if self.view_translated is not None:
            self._apply_zoom_all(self.view_translated.surface._zoom * factor)

    def _on_peek_toggled(self, checked):
        if self.view_translated is not None:
            self.view_translated.surface.set_show_overlay(not checked)

    def _update_display(self):
        if self.view_translated is not None:
            self.view_translated.surface.set_layouts(self._layouts)

    def _start_translation(self):
        if self._closed:
            return
        self.translations = ["" for _ in self.blocks]
        self._layouts = []
        self._translated_pm = QPixmap(self._original_pm)
        self._set_status("正在翻译…", "info")
        self.btn_copy_text.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.btn_save.setEnabled(False)
        self._update_display()

        worker = RowTranslationWorker([b.text for b in self.blocks], self.target_lang, self.use_ai)
        worker.row_ready.connect(self._on_row_ready)
        worker.progress.connect(self._on_progress)
        worker.all_done.connect(self._on_all_done)
        worker.finished.connect(self._on_worker_finished)
        self._workers.append(worker)
        self._worker = worker
        worker.start()

    def _stop_worker(self):
        old = self._worker
        self._worker = None
        if old is None:
            return
        old.cancel()
        try:
            old.row_ready.disconnect(self._on_row_ready)
            old.progress.disconnect(self._on_progress)
            old.all_done.disconnect(self._on_all_done)
        except (TypeError, RuntimeError):
            pass

    def _on_worker_finished(self):
        worker = self.sender()
        if worker in self._workers:
            self._workers.remove(worker)
        if worker is not None:
            worker.deleteLater()
        if self._closed and not self._workers:
            self.deleteLater()

    def _on_row_ready(self, index, text):
        if self._closed or self.sender() is not self._worker:
            return
        if 0 <= index < len(self.translations):
            self.translations[index] = text

    def _on_progress(self, done, total):
        if self._closed or self.sender() is not self._worker:
            return
        self.lbl_status.setText(f"正在翻译 {done}/{total}…")

    def _on_all_done(self, ok):
        if self._closed or self.sender() is not self._worker:
            return
        self._layouts = _compute_block_layouts(self._original_img, self.blocks, self.translations, scale=1.0)
        self._translated_pm = render_translated_pixmap(self._original_pm, self.blocks, self.translations)
        self._update_display()
        changed = sum(
            1 for b, t in zip(self.blocks, self.translations)
            if (t or "").strip() and (t or "").strip() != b.text.strip()
        )
        if not self.translations:
            self._set_status("未识别到可翻译文字", "err")
        elif not ok:
            self._set_status("部分内容翻译失败，已保留原文", "warn")
        elif changed == 0:
            self._set_status("内容似与目标语言一致，可在右上角切换目标语言", "warn")
        else:
            self._set_status(f"翻译完成 · {changed} 处", "ok")
        self.btn_copy_text.setEnabled(True)
        self.btn_copy.setEnabled(True)
        self.btn_save.setEnabled(True)

    def _on_lang_changed(self):
        code = self.combo_lang.currentData()
        if not code or code == self.target_lang:
            return
        self.target_lang = code
        save_settings({"ocr_target_lang": code})
        self._stop_worker()
        self._start_translation()

    def _copy_all_text(self):
        lines = []
        for b, t in zip(self.blocks, self.translations):
            txt = (t or b.text or "").strip()
            if txt:
                lines.append(txt)
        if lines:
            QApplication.clipboard().setText("\n".join(lines))
            self._set_status("全部译文已复制", "ok")

    def _copy_image(self):
        if self._translated_pm is None or self._translated_pm.isNull():
            return
        QApplication.clipboard().setPixmap(self._translated_pm)
        self._set_status("译文图已复制", "ok")

    def _save_image(self):
        if self._translated_pm is None or self._translated_pm.isNull():
            return
        try:
            save_image_dialog(self, self._translated_pm, default_prefix="translated")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            self.reject()
            return
        self._closed = True
        self._stop_worker()
        for worker in list(self._workers):
            worker.cancel()
        self.hide()
        event.accept()
        self.reject()
        if not self._workers:
            self.deleteLater()


class TranslationOverlay(QWidget):
    """把译文按原文字块位置覆盖在截图上，支持语言切换与复制。"""

    closed = pyqtSignal()

    def __init__(self, doc: OcrDoc, target_lang, use_ai=False, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.rows = doc.rows
        self.target_lang = target_lang
        self.use_ai = use_ai
        self.translations = ["" for _ in self.rows]
        self.show_translation = True
        self._worker = None
        self._workers = []
        self._closed = False
        self._sx = 1.0
        self._sy = 1.0

        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._restore_status)

        self._build_bar()
        # 延迟到控件显示后再发请求，避免尺寸/布局尚未就绪
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._start_translation)
        self._start_timer.start(30)

    def _build_bar(self):
        self.bar = QWidget(self)
        self.bar.setObjectName("transOverlayBar")
        self.bar.setStyleSheet(f"""
            QWidget#transOverlayBar {{
                background: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 8px;
                font-family: {CODEX_FONT_FAMILY};
            }}
            QLabel#transStatus {{
                color: #7A5110;
                font-size: 12px;
                font-weight: 600;
                padding: 0 6px;
            }}
            QComboBox {{
                background: #FFFFFF;
                color: #18181B;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 3px 22px 3px 8px;
                font-size: 12px;
                min-width: 88px;
            }}
            QComboBox:hover {{
                border-color: #D4D4D8;
                background: #FAFAFA;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                width: 0px;
                height: 0px;
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background: #FFFFFF;
                color: #0D0D0D;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
                selection-background-color: #F4F4F5;
                selection-color: #0D0D0D;
            }}
            QPushButton {{
                background: transparent;
                color: #3F3F46;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 4px 9px;
                font-size: 12px;
                font-weight: 400;
            }}
            QPushButton:hover {{
                background: #F4F4F5;
                color: #0D0D0D;
            }}
            QPushButton#transClose {{
                color: #71717A;
                font-weight: 600;
            }}
        """)
        lay = QHBoxLayout(self.bar)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(6)

        self.lbl_status = QLabel("正在翻译…")
        self.lbl_status.setObjectName("transStatus")
        lay.addWidget(self.lbl_status)

        self.combo_lang = CodexComboBox()
        for code, label in SUPPORTED_LANGUAGES:
            self.combo_lang.addItem(label, code)
        idx = self.combo_lang.findData(self.target_lang)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)
        self.combo_lang.currentIndexChanged.connect(self._on_lang_changed)
        lay.addWidget(self.combo_lang)

        self.btn_toggle = QPushButton("显示原文")
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._toggle_view)
        lay.addWidget(self.btn_toggle)

        self.btn_copy = QPushButton("复制译文")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_all)
        lay.addWidget(self.btn_copy)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("transClose")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setFixedWidth(26)
        btn_close.clicked.connect(self.close_overlay)
        lay.addWidget(btn_close)

        self.bar.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self._recalc_scale()
        self.setFocus()
        self._place_bar()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recalc_scale()
        self._place_bar()

    def _place_bar(self):
        # 选区很窄时逐级隐藏次要控件，保证“复制/关闭”始终可点
        self.lbl_status.setVisible(self.width() >= 520)
        self.btn_toggle.setVisible(self.width() >= 430)
        self.combo_lang.setVisible(self.width() >= 300)
        self.bar.adjustSize()
        x = max(6, self.width() - self.bar.width() - 8)
        self.bar.move(x, 8)
        self.bar.raise_()

    def _recalc_scale(self):
        iw = max(1, self.doc.image_size.width())
        ih = max(1, self.doc.image_size.height())
        self._sx = self.width() / iw if iw else 1.0
        self._sy = self.height() / ih if ih else 1.0

    def _scale_rect(self, rect):
        return QRect(int(rect.left() * self._sx), int(rect.top() * self._sy),
                     max(1, int(rect.width() * self._sx)), max(1, int(rect.height() * self._sy)))

    def _start_translation(self):
        if self._closed:
            return
        if self._start_timer.isActive():
            self._start_timer.stop()
        texts = [r.text for r in self.rows]
        self.translations = ["" for _ in self.rows]
        self.lbl_status.setText("正在翻译…")
        self.btn_copy.setEnabled(False)
        worker = RowTranslationWorker(texts, self.target_lang, self.use_ai)
        worker.row_ready.connect(self._on_row_ready)
        worker.progress.connect(self._on_progress)
        worker.all_done.connect(self._on_all_done)
        worker.finished.connect(self._on_worker_finished)
        self._workers.append(worker)
        self._worker = worker
        worker.start()
        self.update()

    def _stop_worker(self):
        """语言切换时取消旧任务；旧线程保留引用，结束后自动释放，避免销毁运行中的 QThread。"""
        old = self._worker
        self._worker = None
        if old is None:
            return
        old.cancel()
        try:
            old.row_ready.disconnect(self._on_row_ready)
            old.progress.disconnect(self._on_progress)
            old.all_done.disconnect(self._on_all_done)
        except (TypeError, RuntimeError):
            pass

    def _on_worker_finished(self):
        worker = self.sender()
        if worker in self._workers:
            self._workers.remove(worker)
        if worker is not None:
            worker.deleteLater()
        if self._closed and not self._workers:
            self.deleteLater()

    def _on_lang_changed(self):
        code = self.combo_lang.currentData()
        if not code or code == self.target_lang:
            return
        self.target_lang = code
        save_settings({"ocr_target_lang": code})
        self._stop_worker()
        self._start_translation()

    def _on_row_ready(self, index, text):
        if self._closed or self.sender() is not self._worker:
            return
        if index < 0 or index >= len(self.translations):
            return
        self.translations[index] = text
        self.update()

    def _on_progress(self, done, total):
        if self._closed or self.sender() is not self._worker:
            return
        self.lbl_status.setText(f"正在翻译 {done}/{total}…")

    def _on_all_done(self, ok):
        if self.sender() is not self._worker:
            return
        if self._closed:
            return
        self.btn_copy.setEnabled(any(t.strip() for t in self.translations))
        if ok:
            self._done_text = "✓ 翻译完成"
        else:
            self._done_text = "⚠ 部分行翻译失败"
        self.lbl_status.setText(self._done_text)
        self.update()

    def _toggle_view(self):
        self.show_translation = not self.show_translation
        self.btn_toggle.setText("显示译文" if not self.show_translation else "显示原文")
        self.update()

    def _copy_all(self):
        text = "\n".join(t for t in self.translations if t.strip())
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.lbl_status.setText("✓ 译文已复制")
        self._status_timer.start(1600)

    def _restore_status(self):
        if self._closed:
            return
        self.lbl_status.setText(getattr(self, "_done_text", "✓ 翻译完成"))

    def close_overlay(self):
        if self._closed:
            return
        self._closed = True
        self._start_timer.stop()
        self._status_timer.stop()
        self._stop_worker()
        for worker in list(self._workers):
            worker.cancel()
        self.hide()
        self.closed.emit()
        if not self._workers:
            self.deleteLater()

    def paintEvent(self, event):
        if not self.show_translation:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        painter.fillRect(self.rect(), QColor(255, 255, 255, 26))

        for idx, row in enumerate(self.rows):
            text = self.translations[idx] if idx < len(self.translations) else ""
            if not text.strip():
                continue
            rect = self._scale_rect(row.rect)
            if not rect.intersects(self.rect()):
                continue
            self._draw_translated_block(painter, rect, text)

        painter.end()

    def _draw_translated_block(self, painter, rect, text):
        max_w = max(30, min(self.width() - rect.left() - 8, max(rect.width(), int(rect.width() * 2.2))))
        base_size = max(9, min(16, int(rect.height() * 0.62)))
        chosen_font = None
        chosen_rect = None
        for size in range(base_size, 7, -1):
            font = make_ui_font(pixel_size=size)
            fm = QFontMetrics(font)
            bound = fm.boundingRect(QRect(0, 0, max_w, 10000), Qt.TextWordWrap, text)
            if bound.height() <= max(rect.height(), int(rect.height() * 2.2)) and bound.width() <= max_w + 2:
                chosen_font = font
                chosen_rect = bound
                break
        if chosen_font is None:
            chosen_font = make_ui_font(pixel_size=8)
            fm = QFontMetrics(chosen_font)
            chosen_rect = fm.boundingRect(QRect(0, 0, max_w, 10000), Qt.TextWordWrap, text)

        box_w = max(rect.width(), chosen_rect.width() + 10)
        box_h = max(rect.height(), chosen_rect.height() + 8)
        box = QRect(rect.left() - 3, rect.top() - 3, box_w + 6, box_h + 6)
        if box.right() > self.width() - 4:
            box.setRight(self.width() - 4)
        if box.bottom() > self.height() - 4:
            box.setBottom(self.height() - 4)

        painter.setPen(QPen(QColor(203, 213, 225), 1))
        painter.setBrush(QColor(255, 255, 255, 242))
        painter.drawRoundedRect(box, 3, 3)

        painter.setFont(chosen_font)
        painter.setPen(QColor(15, 23, 42))
        text_rect = box.adjusted(5, 4, -5, -4)
        painter.drawText(text_rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.setBrush(Qt.NoBrush)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_overlay()
        elif event.key() == Qt.Key_C and event.modifiers() & Qt.ControlModifier:
            self._copy_all()
        else:
            super().keyPressEvent(event)

# ==========================================
# UTILITIES & DRAWING ENGINE
# ==========================================
def point_to_segment_dist(p, p1, p2):
    l2 = (p1.x() - p2.x())**2 + (p1.y() - p2.y())**2
    if l2 == 0: return math.hypot(p.x() - p1.x(), p.y() - p1.y())
    t = max(0, min(1, ((p.x() - p1.x()) * (p2.x() - p1.x()) + (p.y() - p1.y()) * (p2.y() - p1.y())) / l2))
    proj = QPoint(int(p1.x() + t * (p2.x() - p1.x())), int(p1.y() + t * (p2.y() - p1.y())))
    return math.hypot(p.x() - proj.x(), p.y() - proj.y())

TEXT_BOX_BORDER_INSET = 5
TEXT_BOX_CONTENT_PAD = 4
TEXT_BOX_DOC_MARGIN = 2


def _extract_edit_font(edit):
    font = edit.get('font')
    if isinstance(font, QFont):
        f = QFont(font)
    else:
        f = QFont(edit.get('fontFamily', 'Microsoft YaHei'), edit.get('fontSize', 14))
        f.setBold(edit.get('bold', False))
        f.setItalic(edit.get('italic', False))
    f.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
    return f


def _build_text_document(text, font, content_width=None):
    """构建与 QuickTextEdit 一致的 QTextDocument，保证编辑期与提交后的折行、行距完全一致。"""
    doc = QTextDocument()
    doc.setDocumentMargin(TEXT_BOX_DOC_MARGIN)
    doc.setDefaultFont(font)
    opt = QTextOption(Qt.AlignLeft | Qt.AlignTop)
    if content_width is not None and content_width > 0:
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(opt)
        doc.setPlainText(text)
        doc.setTextWidth(float(content_width))
    else:
        opt.setWrapMode(QTextOption.NoWrap)
        doc.setDefaultTextOption(opt)
        doc.setPlainText(text)
    return doc


def compute_text_bounds(text, font, pos=QPointF(0, 0), content_width=None):
    doc = _build_text_document(text, font, content_width=content_width)
    sz = doc.size()
    w = max(8.0, float(content_width) if (content_width is not None and content_width > 0) else math.ceil(sz.width()))
    h = max(8.0, math.ceil(sz.height()))
    return QRectF(pos.x(), pos.y(), w + 2 * TEXT_BOX_CONTENT_PAD, h + 2 * TEXT_BOX_CONTENT_PAD)


def get_text_annotation_rect(edit, offset=QPoint(0, 0)):
    """统一计算 TEXT 图元的精确外框矩形（用于绘制背景、命中检测及重新激活编辑框）。"""
    text = edit.get('text', '')
    font = _extract_edit_font(edit)
    raw_rect = edit.get('rect')
    if isinstance(raw_rect, QRect) and raw_rect.isValid() and raw_rect.width() > 4:
        r = QRectF(raw_rect.translated(-offset))
        cw = max(16.0, r.width() - 2 * TEXT_BOX_CONTENT_PAD)
        doc = _build_text_document(text, font, content_width=cw)
        doc_h = math.ceil(doc.size().height())
        if edit.get('user_width') is not None or edit.get('fixed_width'):
            w = max(36.0, r.width())
        else:
            w = r.width()
        h = max(r.height(), float(doc_h + 2 * TEXT_BOX_CONTENT_PAD))
        return QRectF(r.left(), r.top(), w, h)

    pos_pt = QPointF(edit.get('pos', QPoint(0, 0))) - QPointF(offset)
    uw = edit.get('user_width')
    uh = edit.get('user_height')
    cw = max(16.0, float(uw) - 2 * TEXT_BOX_CONTENT_PAD) if uw is not None else None
    bounds = compute_text_bounds(text, font, pos_pt, content_width=cw)
    w = max(36.0, float(uw)) if uw is not None else bounds.width()
    h = max(bounds.height(), float(uh)) if uh is not None else bounds.height()
    return QRectF(bounds.left(), bounds.top(), w, h)

def _draw_edit_rect_ellipse(painter, edit, offset):
    p1 = edit['p1'] - offset
    p2 = edit['p2'] - offset
    r = QRectF(p1, p2).normalized()
    kind = edit.get('kind', 'rect')
    fill_mode = edit.get('fillMode', 'none')
    stroke_color = edit.get('strokeColor', QColor(255, 0, 0))
    fill_color = edit.get('fillColor', stroke_color)
    stroke_w = edit.get('strokeWidth', 3)
    corner_radius = edit.get('cornerRadius', 0)

    painter.save()
    pen = QPen(stroke_color, stroke_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    if fill_mode == 'fill':
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(fill_color))
    elif fill_mode == 'stroke_fill':
        painter.setPen(pen)
        painter.setBrush(QBrush(fill_color))
    else:
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

    if kind == 'ellipse':
        painter.drawEllipse(r)
    elif corner_radius > 0:
        painter.drawRoundedRect(r, corner_radius, corner_radius)
    else:
        painter.drawRect(r)
    painter.restore()


def _draw_edit_arrow(painter, edit, offset):
    p1 = edit['start'] - offset
    p2 = edit['end'] - offset
    color = edit.get('color', QColor(255, 0, 0))
    width = edit.get('width', 3)
    mode = edit.get('arrowMode', 'end')

    painter.save()
    painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(p1, p2)

    def _draw_arrowhead(p_from, p_to):
        dx = p_to.x() - p_from.x()
        dy = p_to.y() - p_from.y()
        dist = math.hypot(dx, dy)
        if dist < 2:
            return
        angle = math.atan2(p_from.y() - p_to.y(), p_from.x() - p_to.x())
        arrow_len = min(dist * 0.45, 12.0 + width * 1.8)
        arrow_angle = math.pi / 6.5
        pa = QPointF(p_to.x() + arrow_len * math.cos(angle + arrow_angle),
                     p_to.y() + arrow_len * math.sin(angle + arrow_angle))
        pb = QPointF(p_to.x() + arrow_len * math.cos(angle - arrow_angle),
                     p_to.y() + arrow_len * math.sin(angle - arrow_angle))
        head_path = QPainterPath()
        head_path.moveTo(QPointF(p_to))
        head_path.lineTo(pa)
        head_path.lineTo(pb)
        head_path.closeSubpath()
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(color, 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(head_path)

    if mode in ['end', 'both']:
        _draw_arrowhead(p1, p2)
    if mode in ['start', 'both']:
        _draw_arrowhead(p2, p1)
    painter.restore()


def _draw_edit_polyline(painter, points, offset, color, width, cap=Qt.RoundCap):
    if len(points) < 2:
        return
    painter.save()
    painter.setPen(QPen(color, width, Qt.SolidLine, cap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(points[0] - offset)
    for pt in points[1:]:
        path.lineTo(pt - offset)
    painter.drawPath(path)
    painter.restore()


def _make_mosaic_tile_pixmap(sub_img, width, height, tile_size, smooth=False):
    gw = max(1, width // max(2, tile_size))
    gh = max(1, height // max(2, tile_size))
    small = sub_img.scaled(gw, gh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    transform = Qt.SmoothTransformation if smooth else Qt.FastTransformation
    return QPixmap.fromImage(small.scaled(width, height, Qt.IgnoreAspectRatio, transform))


def _draw_clipped_mosaic_stroke(painter, points, radius, stroke_w, proc_pix, bbox, offset, base_origin):
    painter.save()
    stroker = QPainterPathStroker()
    stroker.setWidth(stroke_w)
    stroker.setCapStyle(Qt.RoundCap)
    stroker.setJoinStyle(Qt.RoundJoin)

    line_path = QPainterPath()
    line_path.moveTo(points[0] - offset)
    if len(points) == 1:
        line_path.lineTo(points[0] - offset + QPoint(1, 1))
    else:
        for pt in points[1:]:
            line_path.lineTo(pt - offset)
    painter.setClipPath(stroker.createStroke(line_path))
    painter.drawPixmap(bbox.translated(base_origin - offset).topLeft(), proc_pix)
    painter.restore()


def _draw_edit_mosaic_blur(painter, edit, base_pixmap, offset, base_origin):
    points = edit.get('points', [])
    if base_pixmap is None or not points:
        return
    mode = edit.get('mode', 'mosaic')
    block_size = edit.get('blockSize', 10)
    smooth = edit.get('smooth', False)
    brush_w = edit.get('brushWidth', 22)

    radius = brush_w // 2 + 2
    world_bbox = QRect(
        QPoint(min(p.x() for p in points) - radius, min(p.y() for p in points) - radius),
        QPoint(max(p.x() for p in points) + radius, max(p.y() for p in points) + radius),
    )
    bbox = world_bbox.translated(-base_origin).intersected(base_pixmap.rect())
    if bbox.isEmpty() or bbox.width() <= 0 or bbox.height() <= 0:
        return

    sub_img = base_pixmap.copy(bbox).toImage()
    if mode == 'blur':
        try:
            ptr = sub_img.bits()
            ptr.setsize(sub_img.byteCount())
            arr = np.frombuffer(ptr, np.uint8).reshape((sub_img.height(), sub_img.width(), 4))
            ksize = max(3, int(block_size * 2) | 1)
            blurred_rgb = cv2.GaussianBlur(arr[:, :, :3], (ksize, ksize), 0)
            out_arr = arr.copy()
            out_arr[:, :, :3] = blurred_rgb
            qimg_blur = QImage(out_arr.data, bbox.width(), bbox.height(), sub_img.bytesPerLine(), QImage.Format_ARGB32_Premultiplied)
            proc_pix = QPixmap.fromImage(qimg_blur)
        except Exception as e:
            logging.debug("高斯模糊回退缩放模糊: %s", e)
            tile_size = max(2, block_size)
            small = sub_img.scaled(max(1, bbox.width() // tile_size), max(1, bbox.height() // tile_size), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            proc_pix = QPixmap.fromImage(small.scaled(bbox.width(), bbox.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    else:
        proc_pix = _make_mosaic_tile_pixmap(sub_img, bbox.width(), bbox.height(), block_size, smooth=smooth)

    _draw_clipped_mosaic_stroke(painter, points, radius, brush_w, proc_pix, bbox, offset, base_origin)


def _draw_edit_text(painter, edit, offset):
    text = edit.get('text', '')
    if not text:
        return
    painter.save()
    font = _extract_edit_font(edit)
    box_rect = get_text_annotation_rect(edit, offset)
    rot = int(edit.get('rotation', 0)) % 360
    if rot:
        center = box_rect.center()
        painter.translate(center)
        painter.rotate(rot)
        painter.translate(-center)

    if edit.get('bgEnabled', False) and edit.get('bgColor'):
        painter.setBrush(QBrush(edit['bgColor']))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(box_rect, 4.0, 4.0)

    inner_rect = box_rect.adjusted(
        TEXT_BOX_CONTENT_PAD, TEXT_BOX_CONTENT_PAD,
        -TEXT_BOX_CONTENT_PAD, -TEXT_BOX_CONTENT_PAD
    )
    doc = _build_text_document(text, font, content_width=max(12.0, inner_rect.width()))
    ctx = QAbstractTextDocumentLayout.PaintContext()
    ctx.palette.setColor(QPalette.Text, edit.get('color', QColor(255, 0, 0)))
    painter.setClipRect(box_rect)
    painter.translate(inner_rect.topLeft())
    doc.documentLayout().draw(painter, ctx)
    painter.restore()


def _draw_edit_legacy(painter, edit, t, thickness, base_pixmap, offset, base_origin):
    if t == 'RECTANGLE':
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(edit['color'], thickness))
        painter.drawRect(edit['rect'].translated(-offset))
    elif t == 'LINE':
        painter.save()
        painter.setBrush(Qt.NoBrush)
        line_style = Qt.DashLine if edit.get('lineStyle') == 'dashed' else Qt.SolidLine
        painter.setPen(QPen(edit['color'], thickness, line_style, Qt.RoundCap, Qt.RoundJoin))
        p1 = edit['start'] - offset
        p2 = edit['end'] - offset
        painter.drawLine(p1, p2)
        if edit.get('arrow'):
            angle = math.atan2(p1.y() - p2.y(), p1.x() - p2.x())
            arrow_size = 10 + thickness * 1.5
            p3 = QPoint(int(p2.x() + arrow_size * math.cos(angle + math.pi / 6)),
                        int(p2.y() + arrow_size * math.sin(angle + math.pi / 6)))
            p4 = QPoint(int(p2.x() + arrow_size * math.cos(angle - math.pi / 6)),
                        int(p2.y() + arrow_size * math.sin(angle - math.pi / 6)))
            painter.setBrush(QBrush(edit['color']))
            painter.setPen(QPen(edit['color'], 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolygon(p2, p3, p4)
        painter.restore()
    elif t == 'BLUR':
        source_rect = edit['rect'].translated(-base_origin)
        if base_pixmap is not None:
            source_rect = source_rect.intersected(base_pixmap.rect())
        if base_pixmap is not None and not source_rect.isEmpty():
            draw_rect = source_rect.translated(base_origin - offset)
            mosaic = _make_mosaic_tile_pixmap(
                base_pixmap.copy(source_rect).toImage(),
                source_rect.width(), source_rect.height(),
                max(6, thickness * 3), smooth=False
            )
            painter.drawPixmap(draw_rect.topLeft(), mosaic)
    elif t == 'BLUR_STROKE':
        points = edit.get('points', [])
        if base_pixmap is not None and points:
            radius = max(8, thickness * 4)
            world_bbox = QRect(
                QPoint(min(p.x() for p in points) - radius, min(p.y() for p in points) - radius),
                QPoint(max(p.x() for p in points) + radius, max(p.y() for p in points) + radius),
            )
            bbox = world_bbox.translated(-base_origin).intersected(base_pixmap.rect())
            if not bbox.isEmpty():
                mosaic = _make_mosaic_tile_pixmap(
                    base_pixmap.copy(bbox).toImage(),
                    bbox.width(), bbox.height(),
                    max(6, thickness * 3), smooth=False
                )
                _draw_clipped_mosaic_stroke(painter, points, radius, radius * 2, mosaic, bbox, offset, base_origin)
    elif t == 'ELLIPSE':
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(edit['color'], thickness))
        painter.drawEllipse(edit['rect'].translated(-offset))
    elif t == 'HIGHLIGHTER':
        hl_color = QColor(edit['color'])
        hl_color.setAlpha(115)
        _draw_edit_polyline(painter, edit.get('points', []), offset, hl_color, max(12, thickness * 4))
    elif t == 'STEP':
        step_pos = edit.get('pos') or edit.get('center')
        if step_pos is not None:
            pos = step_pos - offset
            radius = edit.get('radius') or max(11, 8 + thickness * 2)
            num_val = edit.get('num') if edit.get('num') is not None else edit.get('number', 1)
            painter.save()
            painter.setBrush(QBrush(edit['color']))
            painter.setPen(QPen(Qt.white, 1.5))
            painter.drawEllipse(pos, radius, radius)
            text_color = Qt.white if edit['color'].lightness() < 165 else Qt.black
            painter.setPen(QPen(text_color))
            painter.setFont(make_ui_font(max(8, int(radius * 0.95)), QFont.DemiBold))
            painter.drawText(QRect(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2), Qt.AlignCenter, str(num_val))
            painter.restore()


def draw_all_edits(painter, edits, base_pixmap, offset=QPoint(0, 0), base_origin=QPoint(0, 0)):
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.setBrush(Qt.NoBrush)

    for edit in edits:
        t = edit.get('type')
        thickness = edit.get('thickness', edit.get('strokeWidth', edit.get('width', 3)))
        if t == 'RECT_ELLIPSE':
            _draw_edit_rect_ellipse(painter, edit, offset)
        elif t == 'ARROW':
            _draw_edit_arrow(painter, edit, offset)
        elif t == 'MARKER':
            color = QColor(edit.get('color', QColor(255, 230, 0)))
            color.setAlpha(int(255 * (edit.get('opacity', 45) / 100.0)))
            cap = Qt.RoundCap if edit.get('tipStyle', 'round') == 'round' else Qt.SquareCap
            _draw_edit_polyline(painter, edit.get('points', []), offset, color, edit.get('width', 16), cap=cap)
        elif t == 'MOSAIC_BLUR':
            _draw_edit_mosaic_blur(painter, edit, base_pixmap, offset, base_origin)
        elif t == 'PEN':
            points = edit.get('points', [])
            if len(points) >= 2:
                color = edit.get('color', QColor(255, 0, 0))
                width = edit.get('width', edit.get('thickness', 3))
                if edit.get('penStyle', 'polyline') == 'line':
                    _draw_edit_polyline(painter, [points[0], points[-1]], offset, color, width)
                else:
                    _draw_edit_polyline(painter, points, offset, color, width)
        elif t == 'TEXT':
            _draw_edit_text(painter, edit, offset)
        else:
            _draw_edit_legacy(painter, edit, t, thickness, base_pixmap, offset, base_origin)

    painter.setBrush(Qt.NoBrush)


def is_annotation_hit(edit, pos):
    """精确检测鼠标点击位置是否命中某一标注元素，支持所有矢量与位图标注类型"""
    try:
        t = edit.get('type')
        if t == 'RECT_ELLIPSE':
            r = QRectF(edit['p1'], edit['p2']).normalized()
            kind = edit.get('kind', 'rect')
            fill_mode = edit.get('fillMode', 'none')
            stroke_w = edit.get('strokeWidth', 3)
            tol = stroke_w / 2.0 + 5.0

            if fill_mode in ['fill', 'stroke_fill']:
                if kind == 'ellipse':
                    cx, cy = r.center().x(), r.center().y()
                    rx, ry = max(1.0, r.width() / 2.0), max(1.0, r.height() / 2.0)
                    return ((pos.x() - cx) / rx) ** 2 + ((pos.y() - cy) / ry) ** 2 <= 1.0
                else:
                    return r.contains(QPointF(pos))
            else:
                if kind == 'ellipse':
                    cx, cy = r.center().x(), r.center().y()
                    rx, ry = max(1.0, r.width() / 2.0), max(1.0, r.height() / 2.0)
                    dist = math.sqrt(((pos.x() - cx) / rx) ** 2 + ((pos.y() - cy) / ry) ** 2)
                    avg_r = (rx + ry) / 2.0
                    return abs(dist - 1.0) * avg_r <= tol
                else:
                    outer = r.adjusted(-tol, -tol, tol, tol)
                    inner = r.adjusted(tol, tol, -tol, -tol)
                    if inner.width() <= 0 or inner.height() <= 0:
                        return outer.contains(QPointF(pos))
                    return outer.contains(QPointF(pos)) and not inner.contains(QPointF(pos))

        elif t == 'ARROW':
            w = edit.get('width', 3)
            return point_to_segment_dist(pos, edit['start'], edit['end']) < max(8, w / 2 + 6)

        elif t == 'PEN':
            w = edit.get('width', 3)
            style = edit.get('penStyle', 'polyline')
            pts = edit.get('points', [])
            if style == 'line' and len(pts) >= 2:
                return point_to_segment_dist(pos, pts[0], pts[-1]) < max(8, w / 2 + 6)
            for i in range(len(pts) - 1):
                if point_to_segment_dist(pos, pts[i], pts[i+1]) < max(8, w / 2 + 6):
                    return True
            for pt in pts:
                if math.hypot(pos.x() - pt.x(), pos.y() - pt.y()) <= max(8, w / 2 + 6):
                    return True
            return False

        elif t == 'MARKER':
            w = edit.get('width', 16)
            radius = max(8, w / 2 + 4)
            pts = edit.get('points', [])
            for i in range(len(pts) - 1):
                if point_to_segment_dist(pos, pts[i], pts[i+1]) < radius:
                    return True
            for pt in pts:
                if math.hypot(pos.x() - pt.x(), pos.y() - pt.y()) <= radius:
                    return True
            return False

        elif t == 'MOSAIC_BLUR':
            brush_w = edit.get('brushWidth', 22)
            radius = max(8, brush_w / 2 + 4)
            pts = edit.get('points', [])
            for i in range(len(pts) - 1):
                if point_to_segment_dist(pos, pts[i], pts[i+1]) < radius:
                    return True
            for pt in pts:
                if math.hypot(pos.x() - pt.x(), pos.y() - pt.y()) <= radius:
                    return True
            return False

        elif t == 'TEXT':
            text = edit.get('text', '')
            if not text: return False
            bounds = get_text_annotation_rect(edit)
            rot = int(edit.get('rotation', 0)) % 360
            test_pt = QPointF(pos)
            if rot:
                c = bounds.center()
                rad = math.radians(-rot)
                dx, dy = test_pt.x() - c.x(), test_pt.y() - c.y()
                test_pt = QPointF(
                    c.x() + dx * math.cos(rad) - dy * math.sin(rad),
                    c.y() + dx * math.sin(rad) + dy * math.cos(rad)
                )
            tol = 6.0
            return bounds.adjusted(-tol, -tol, tol, tol).contains(test_pt)

        elif t in ['RECTANGLE', 'BLUR']:
            return 'rect' in edit and edit['rect'].contains(pos)
        elif t == 'ELLIPSE':
            if 'rect' in edit:
                r = edit['rect']
                if r.contains(pos):
                    cx = r.center().x()
                    cy = r.center().y()
                    rx = max(1.0, r.width() / 2.0)
                    ry = max(1.0, r.height() / 2.0)
                    val = ((pos.x() - cx) / rx) ** 2 + ((pos.y() - cy) / ry) ** 2
                    return val <= 1.35
            return False
        elif t == 'LINE':
            return point_to_segment_dist(pos, edit['start'], edit['end']) < 15
        elif t in ['BLUR_STROKE', 'HIGHLIGHTER']:
            thick = edit.get('thickness', 3)
            radius = max(8, thick * 4) + 6
            for pt in edit.get('points', []):
                if math.hypot(pos.x() - pt.x(), pos.y() - pt.y()) <= radius:
                    return True
            return False
        elif t == 'STEP':
            step_pos = edit.get('pos') or edit.get('center')
            if step_pos is not None:
                thick = edit.get('thickness', 3)
                radius = edit.get('radius') or (max(11, 8 + thick * 2) + 6)
                return math.hypot(pos.x() - step_pos.x(), pos.y() - step_pos.y()) <= radius
            return False
    except Exception as e:
        logging.debug("is_annotation_hit 异常: %s", e)
    return False


def clear_scroll_temp_folder(folder_path):
    os.makedirs(folder_path, exist_ok=True)
    for name in os.listdir(folder_path):
        if name.startswith("frame_") and name.lower().endswith(".png"):
            try:
                os.remove(os.path.join(folder_path, name))
            except OSError:
                logging.warning("无法删除旧滚动截图缓存: %s", name, exc_info=True)


def get_scroll_frame_files(folder_path):
    if not os.path.exists(folder_path):
        return []

    def frame_number(name):
        try:
            return int(name.split('_')[1].split('.')[0])
        except (IndexError, ValueError):
            return -1

    return sorted(
        [f for f in os.listdir(folder_path) if f.startswith("frame_") and f.lower().endswith(".png")],
        key=frame_number
    )


def normalize_scroll_frames(frames):
    frames = [frame for frame in frames if frame is not None and frame.size > 0]
    if not frames:
        return []
    min_width = min(frame.shape[1] for frame in frames)
    return [frame[:, :min_width].copy() if frame.shape[1] != min_width else frame.copy() for frame in frames]


def central_crop_bounds(width):
    if width < 80:
        return 0, width
    margin = int(width * 0.10)
    if width - (margin * 2) < 40:
        return 0, width
    return margin, width - margin


def gray_crop(image, x1, x2):
    cropped = image[:, x1:x2]
    return cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)


def mean_abs_difference(a, b):
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    if h <= 0 or w <= 0:
        return float("inf")
    return float(np.mean(cv2.absdiff(a[:h, :w], b[:h, :w])))


def is_duplicate_scroll_frame(prev_img, next_img):
    x1, x2 = central_crop_bounds(min(prev_img.shape[1], next_img.shape[1]))
    prev_gray = gray_crop(prev_img, x1, x2)
    next_gray = gray_crop(next_img, x1, x2)
    diff = mean_abs_difference(prev_gray, next_gray)
    return diff < SCROLL_DUPLICATE_DIFF_THRESHOLD


def scan_scroll_overlap(prev_img, next_img):
    h_prev, h_next = prev_img.shape[:2]
    max_overlap = min(h_prev, h_next) - 1
    if max_overlap < 6:
        return None

    x1, x2 = central_crop_bounds(prev_img.shape[1])
    prev_gray = gray_crop(prev_img, x1, x2)
    next_gray = gray_crop(next_img, x1, x2)
    min_overlap = max(6, min(h_prev, h_next) // 12)
    best_overlap = None
    best_diff = float("inf")

    for overlap in range(max_overlap, min_overlap - 1, -1):
        prev_tail = prev_gray[h_prev - overlap:h_prev, :]
        next_head = next_gray[:overlap, :]
        diff = mean_abs_difference(prev_tail, next_head)
        if diff < best_diff:
            best_diff = diff
            best_overlap = overlap
        if diff <= 5.0:
            return overlap, 1.0 - min(diff / 255.0, 1.0), "scan"

    if best_overlap is not None and best_diff <= 9.0:
        return best_overlap, 1.0 - min(best_diff / 255.0, 1.0), "scan-loose"
    return None


def template_scroll_overlap(prev_img, next_img):
    h_prev, h_next = prev_img.shape[:2]
    min_h = min(h_prev, h_next)
    if min_h < 12:
        return None

    x1, x2 = central_crop_bounds(prev_img.shape[1])
    prev_gray = gray_crop(prev_img, x1, x2)
    next_gray = gray_crop(next_img, x1, x2)
    template_h = min(max(8, int(min_h * 0.28)), min_h - 1, 180)
    if template_h < 6 or template_h >= h_next:
        return None

    margins = [0, int(h_prev * 0.06), int(h_prev * 0.14), int(h_prev * 0.24)]
    best = None

    for margin in margins:
        start_y = h_prev - margin - template_h
        if start_y < 0:
            continue
        template = prev_gray[start_y:start_y + template_h, :]
        if template.shape[0] < 6 or template.shape[1] < 6 or float(np.std(template)) < 2.0:
            continue

        result = cv2.matchTemplate(next_gray, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        y = loc[1]
        scroll_delta = start_y - y
        if scroll_delta <= 0:
            continue
        unique_start = y + template_h
        if unique_start >= h_next:
            continue
        candidate = (unique_start, float(score), "template")
        if best is None or candidate[1] > best[1]:
            best = candidate

    if best and best[1] >= 0.42:
        return best
    return None


def find_scroll_append_start(prev_img, next_img):
    if is_duplicate_scroll_frame(prev_img, next_img):
        return None, 0.0, "duplicate"

    overlap = scan_scroll_overlap(prev_img, next_img)
    if overlap:
        append_start, score, method = overlap
        if next_img.shape[0] - append_start >= SCROLL_MIN_UNIQUE_PIXELS:
            return append_start, score, method
        return None, score, "too-small-shift"

    overlap = template_scroll_overlap(prev_img, next_img)
    if overlap:
        append_start, score, method = overlap
        if next_img.shape[0] - append_start >= SCROLL_MIN_UNIQUE_PIXELS:
            return append_start, score, method
        return None, score, "too-small-shift"

    return 0, 0.0, "fallback"


def stitch_scroll_frame_images(frames):
    frames = normalize_scroll_frames(frames)
    if not frames:
        return None, {"frames": 0, "appended": 0, "skipped": 0}
    if len(frames) == 1:
        return frames[0], {"frames": 1, "appended": 0, "skipped": 0}

    base_img = frames[0].copy()
    last_frame = frames[0]
    stats = {"frames": len(frames), "appended": 0, "skipped": 0, "fallbacks": 0}

    for next_img in frames[1:]:
        append_start, score, method = find_scroll_append_start(last_frame, next_img)
        if append_start is None:
            stats["skipped"] += 1
            logging.debug("跳过重复/过小滚动帧: method=%s score=%.3f", method, score)
            continue

        append_start = max(0, min(int(append_start), next_img.shape[0]))
        unique_pixels = next_img[append_start:, :]
        if unique_pixels.shape[0] < SCROLL_MIN_UNIQUE_PIXELS:
            stats["skipped"] += 1
            continue

        base_img = np.vstack((base_img, unique_pixels))
        last_frame = next_img
        stats["appended"] += 1
        if method == "fallback":
            stats["fallbacks"] += 1
        logging.debug(
            "拼接滚动帧: method=%s score=%.3f append_start=%s unique_height=%s",
            method, score, append_start, unique_pixels.shape[0]
        )

    return base_img, stats


def estimate_scroll_delta(prev_img, next_img):
    if is_duplicate_scroll_frame(prev_img, next_img):
        return None, 0.0

    prev_img, next_img = normalize_scroll_frames([prev_img, next_img])
    h = min(prev_img.shape[0], next_img.shape[0])
    if h < 20:
        return None, 0.0

    x1, x2 = central_crop_bounds(prev_img.shape[1])
    prev_gray = gray_crop(prev_img[:h, :], x1, x2)
    next_gray = gray_crop(next_img[:h, :], x1, x2)

    min_delta = max(3, h // 80)
    max_delta = max(min_delta, min(int(h * 0.70), h - 6))
    best_delta = None
    best_diff = float("inf")

    for delta in range(min_delta, max_delta + 1):
        overlap_h = h - delta
        if overlap_h < max(12, int(h * 0.25)):
            continue
        diff = mean_abs_difference(prev_gray[delta:h, :], next_gray[:overlap_h, :])
        if diff < best_diff:
            best_diff = diff
            best_delta = delta

    if best_delta is None:
        return None, 0.0
    return best_delta, best_diff


def estimate_fixed_scroll_step(frames):
    deltas = []
    for i in range(1, len(frames)):
        delta, diff = estimate_scroll_delta(frames[i - 1], frames[i])
        if delta is not None and diff <= 18.0:
            deltas.append(delta)

    if not deltas:
        return None

    median = int(round(float(np.median(deltas))))
    if median <= 0:
        return None
    return median


def stitch_fixed_step_scroll_images(frames):
    frames = normalize_scroll_frames(frames)
    if not frames:
        return None, {"frames": 0, "appended": 0, "skipped": 0}
    if len(frames) == 1:
        return frames[0], {"frames": 1, "appended": 0, "skipped": 0, "fixed_step": 0}

    fixed_step = estimate_fixed_scroll_step(frames)
    if fixed_step is None:
        return stitch_scroll_frame_images(frames)

    base_img = frames[0].copy()
    stats = {"frames": len(frames), "appended": 0, "skipped": 0, "fixed_step": fixed_step, "fallbacks": 0}
    tolerance = max(6, int(fixed_step * SCROLL_FIXED_STEP_TOLERANCE))

    for i in range(1, len(frames)):
        prev_img, next_img = frames[i - 1], frames[i]
        if is_duplicate_scroll_frame(prev_img, next_img):
            stats["skipped"] += 1
            continue

        measured_step, diff = estimate_scroll_delta(prev_img, next_img)
        append_step = fixed_step
        if measured_step is not None:
            if abs(measured_step - fixed_step) <= tolerance:
                append_step = measured_step
            elif measured_step < fixed_step and diff <= 18.0:
                append_step = measured_step
            else:
                stats["fallbacks"] += 1
        else:
            stats["fallbacks"] += 1

        append_step = max(SCROLL_MIN_UNIQUE_PIXELS, min(int(append_step), next_img.shape[0]))
        unique_pixels = next_img[-append_step:, :]
        if unique_pixels.shape[0] < SCROLL_MIN_UNIQUE_PIXELS:
            stats["skipped"] += 1
            continue
        base_img = np.vstack((base_img, unique_pixels))
        stats["appended"] += 1

    return base_img, stats


def send_mouse_wheel(delta):
    try:
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(delta), 0)
    except Exception:
        logging.warning("自动滚动事件发送失败", exc_info=True)


TOOLBAR_STYLE = f"""
QWidget#mainToolbar, QWidget#editorToolbar, QWidget#cropToolbar {{
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 10px;
    font-family: {CODEX_FONT_FAMILY};
}}
"""

BUTTON_STYLE = """
QPushButton {
    background: transparent;
    color: #24292f;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 9px;
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
    font-size: 12px;
    font-weight: 400;
}
QPushButton:hover {
    background: #f5f3ef;
    border: 1px solid #d6d0c4;
    color: #7A5110;
}
QPushButton:pressed {
    background: #eae6de;
    border: 1px solid #b8b0a0;
}
QPushButton:disabled {
    color: #8c959f;
    background: transparent;
    border: 1px solid transparent;
}
"""

ACTIVE_BUTTON_STYLE = """
QPushButton {
    background-color: #1D1D1F;
    color: #FFFFFF;
    border: 1px solid #1D1D1F;
    border-radius: 6px;
    padding: 5px 9px;
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #2C2C2E;
}
QPushButton:pressed {
    background-color: #000000;
}
"""

DANGER_BUTTON_STYLE = """
QPushButton {
    background: transparent;
    color: #cf222e;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 9px;
    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
    font-size: 12px;
    font-weight: 400;
}
QPushButton:hover {
    background: #ffebe9;
    border: 1px solid #ffcecb;
    color: #a40e26;
}
QPushButton:pressed {
    background: #ffd8d5;
}
"""

def add_drop_shadow(widget, blur=18, y_offset=4, alpha=40):
    """为悬浮组件挂载现代柔和多层立体阴影"""
    try:
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, y_offset)
        shadow.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(shadow)
    except Exception:
        pass

def ensure_high_dpi_and_fonts():
    """探测系统可用现代字体并注册全局回退链，开启 FreeType 无边缘彩边平滑渲染"""
    app = QApplication.instance()
    if app:
        _configure_ui_font_system()
        app.setFont(make_ui_font(9, QFont.Normal))

def apply_button_style(button, active=False, danger=False):
    if danger:
        button.setStyleSheet(DANGER_BUTTON_STYLE)
    elif active:
        button.setStyleSheet(ACTIVE_BUTTON_STYLE)
    else:
        button.setStyleSheet(BUTTON_STYLE)

# ==========================================
# CUSTOM DIALOGS & WIDGETS
# ==========================================
class VectorIconFactory:
    """纯矢量 QPainter 绘制 Apple SF Symbols 质感 27x27 线框图标，保证任意高分屏 DPI 下极致细腻锐利"""

    @staticmethod
    def _draw_shape_and_draw_icon(p, name, color):
        if name == 'rect_ellipse':
            p.drawRoundedRect(QRectF(3.5, 4.0, 12.2, 12.2), 2.8, 2.8)
            p.drawEllipse(QRectF(11.2, 11.2, 12.2, 12.2))
        elif name == 'rect':
            p.drawRoundedRect(QRectF(4.2, 5.5, 18.6, 16.0), 3.0, 3.0)
        elif name == 'ellipse':
            p.drawEllipse(QRectF(4.2, 5.5, 18.6, 16.0))
        elif name == 'line':
            p.drawLine(QPointF(5.5, 21.5), QPointF(21.5, 5.5))
        elif name == 'arrow':
            path = QPainterPath()
            path.moveTo(5.0, 21.2)
            path.quadTo(11.8, 17.8, 21.0, 6.2)
            p.drawPath(path)
            head = QPainterPath()
            head.moveTo(13.2, 6.0)
            head.lineTo(21.2, 6.0)
            head.lineTo(21.2, 14.0)
            p.drawPath(head)
        elif name == 'pen':
            p.save()
            p.translate(13.5, 13.5)
            p.rotate(-45)
            p.translate(-13.5, -13.5)
            p.drawRoundedRect(QRectF(10.8, 5.2, 5.4, 11.6), 1.4, 1.4)
            nib = QPainterPath()
            nib.moveTo(10.8, 16.8)
            nib.lineTo(13.5, 22.4)
            nib.lineTo(16.2, 16.8)
            p.drawPath(nib)
            p.restore()
        elif name == 'marker':
            p.save()
            p.translate(13.5, 13.5)
            p.rotate(-45)
            p.translate(-13.5, -13.5)
            p.drawRoundedRect(QRectF(9.6, 4.8, 7.8, 10.4), 1.8, 1.8)
            chisel = QPainterPath()
            chisel.moveTo(10.6, 15.2)
            chisel.lineTo(11.2, 21.2)
            chisel.lineTo(15.8, 18.6)
            chisel.lineTo(16.4, 15.2)
            p.drawPath(chisel)
            p.restore()
        elif name == 'mosaic_blur':
            outer = QRectF(4.2, 4.2, 18.6, 18.6)
            p.save()
            clip_p = QPainterPath()
            clip_p.addRoundedRect(outer, 3.0, 3.0)
            p.setClipPath(clip_p)
            p.fillRect(QRectF(4.2, 4.2, 9.3, 9.3), color)
            p.fillRect(QRectF(13.5, 13.5, 9.3, 9.3), color)
            p.restore()
            p.drawRoundedRect(outer, 3.0, 3.0)
        elif name == 'text':
            p.drawLine(QPointF(5.0, 6.0), QPointF(22.0, 6.0))
            p.drawLine(QPointF(5.0, 6.0), QPointF(5.0, 8.8))
            p.drawLine(QPointF(22.0, 6.0), QPointF(22.0, 8.8))
            p.drawLine(QPointF(13.5, 6.0), QPointF(13.5, 21.0))
            p.drawLine(QPointF(9.8, 21.0), QPointF(17.2, 21.0))
        elif name == 'eraser':
            p.save()
            p.translate(13.5, 13.5)
            p.rotate(-40)
            p.translate(-13.5, -13.5)
            p.drawRoundedRect(QRectF(8.2, 5.0, 10.6, 16.8), 2.8, 2.8)
            p.drawLine(QPointF(8.2, 13.8), QPointF(18.8, 13.8))
            p.restore()
        else:
            return False
        return True

    @staticmethod
    def _draw_action_icon(p, name, color):
        if name == 'undo':
            path = QPainterPath()
            path.arcMoveTo(QRectF(6.2, 6.2, 14.8, 14.8), 215)
            path.arcTo(QRectF(6.2, 6.2, 14.8, 14.8), 215, -210)
            p.drawPath(path)
            head = QPainterPath()
            head.moveTo(7.2, 10.2)
            head.lineTo(6.2, 16.5)
            head.lineTo(12.5, 17.0)
            p.drawPath(head)
        elif name == 'redo':
            path = QPainterPath()
            path.arcMoveTo(QRectF(6.0, 6.2, 14.8, 14.8), -35)
            path.arcTo(QRectF(6.0, 6.2, 14.8, 14.8), -35, 210)
            p.drawPath(path)
            head = QPainterPath()
            head.moveTo(19.8, 10.2)
            head.lineTo(20.8, 16.5)
            head.lineTo(14.5, 17.0)
            p.drawPath(head)
        elif name == 'close':
            p.drawLine(QPointF(7.5, 7.5), QPointF(19.5, 19.5))
            p.drawLine(QPointF(19.5, 7.5), QPointF(7.5, 19.5))
        elif name == 'pin':
            p.save()
            p.translate(13.5, 13.5)
            p.rotate(-45)
            p.translate(-13.5, -13.5)
            p.drawRoundedRect(QRectF(9.4, 4.4, 8.2, 3.6), 1.4, 1.4)
            p.drawRoundedRect(QRectF(11.2, 8.0, 4.6, 7.6), 1.0, 1.0)
            p.drawLine(QPointF(7.5, 15.8), QPointF(19.5, 15.8))
            p.drawLine(QPointF(13.5, 15.8), QPointF(13.5, 22.8))
            p.restore()
        elif name == 'save':
            body = QPainterPath()
            body.moveTo(6.0, 4.5)
            body.lineTo(18.5, 4.5)
            body.lineTo(22.5, 8.5)
            body.lineTo(22.5, 20.5)
            body.quadTo(22.5, 22.5, 20.5, 22.5)
            body.lineTo(6.5, 22.5)
            body.quadTo(4.5, 22.5, 4.5, 20.5)
            body.lineTo(4.5, 6.5)
            body.quadTo(4.5, 4.5, 6.5, 4.5)
            p.drawPath(body)
            p.drawRoundedRect(QRectF(8.2, 4.5, 7.6, 5.8), 1.0, 1.0)
            p.drawRoundedRect(QRectF(7.5, 14.5, 12.0, 8.0), 1.6, 1.6)
        elif name == 'copy':
            back = QPainterPath()
            back.moveTo(9.0, 9.0)
            back.lineTo(9.0, 6.8)
            back.quadTo(9.0, 4.5, 11.3, 4.5)
            back.lineTo(20.2, 4.5)
            back.quadTo(22.5, 4.5, 22.5, 6.8)
            back.lineTo(22.5, 15.7)
            back.quadTo(22.5, 18.0, 20.2, 18.0)
            back.lineTo(18.0, 18.0)
            p.drawPath(back)
            p.drawRoundedRect(QRectF(4.5, 9.0, 13.5, 13.5), 2.8, 2.8)
        elif name == 'crop':
            p.drawLine(QPointF(5.0, 8.8), QPointF(18.2, 8.8))
            p.drawLine(QPointF(18.2, 8.8), QPointF(18.2, 22.0))
            p.drawLine(QPointF(8.8, 5.0), QPointF(8.8, 18.2))
            p.drawLine(QPointF(8.8, 18.2), QPointF(22.0, 18.2))
        elif name == 'ocr':
            tl = QPainterPath()
            tl.moveTo(4.5, 9.2); tl.lineTo(4.5, 6.8); tl.quadTo(4.5, 4.5, 6.8, 4.5); tl.lineTo(9.2, 4.5)
            p.drawPath(tl)
            tr = QPainterPath()
            tr.moveTo(17.8, 4.5); tr.lineTo(20.2, 4.5); tr.quadTo(22.5, 4.5, 22.5, 6.8); tr.lineTo(22.5, 9.2)
            p.drawPath(tr)
            bl = QPainterPath()
            bl.moveTo(4.5, 17.8); bl.lineTo(4.5, 20.2); bl.quadTo(4.5, 22.5, 6.8, 22.5); bl.lineTo(9.2, 22.5)
            p.drawPath(bl)
            br = QPainterPath()
            br.moveTo(17.8, 22.5); br.lineTo(20.2, 22.5); br.quadTo(22.5, 22.5, 22.5, 20.2); br.lineTo(22.5, 17.8)
            p.drawPath(br)
            a_path = QPainterPath()
            a_path.moveTo(8.8, 18.6)
            a_path.lineTo(13.5, 8.4)
            a_path.lineTo(18.2, 18.6)
            p.drawPath(a_path)
            p.drawLine(QPointF(10.4, 15.2), QPointF(16.6, 15.2))
        elif name == 'text_pick':
            p.drawLine(QPointF(6.5, 5.2), QPointF(10.5, 5.2))
            p.drawLine(QPointF(8.5, 5.2), QPointF(8.5, 21.8))
            p.drawLine(QPointF(6.5, 21.8), QPointF(10.5, 21.8))
            p.drawLine(QPointF(14.0, 8.2), QPointF(21.8, 8.2))
            p.drawLine(QPointF(14.0, 13.5), QPointF(21.8, 13.5))
            p.drawLine(QPointF(14.0, 18.8), QPointF(19.2, 18.8))
        elif name == 'translate':
            p.drawRoundedRect(QRectF(3.6, 6.0, 9.0, 10.8), 2.0, 2.0)
            p.drawRoundedRect(QRectF(14.4, 10.2, 9.0, 10.8), 2.0, 2.0)
            p.drawLine(QPointF(6.0, 9.5), QPointF(10.2, 9.5))
            p.drawLine(QPointF(6.0, 12.5), QPointF(10.2, 12.5))
            p.drawLine(QPointF(16.8, 13.8), QPointF(21.0, 13.8))
            p.drawLine(QPointF(16.8, 16.8), QPointF(21.0, 16.8))
            arrow = QPainterPath()
            arrow.moveTo(12.2, 7.2)
            arrow.lineTo(15.0, 10.0)
            arrow.lineTo(12.2, 12.8)
            p.drawPath(arrow)
        elif name == 'scroll':
            p.drawRoundedRect(QRectF(5.0, 4.2, 17.0, 18.6), 3.0, 3.0)
            p.drawLine(QPointF(13.5, 8.2), QPointF(13.5, 18.0))
            arrow = QPainterPath()
            arrow.moveTo(9.8, 14.5)
            arrow.lineTo(13.5, 18.4)
            arrow.lineTo(17.2, 14.5)
            p.drawPath(arrow)
        elif name == 'record':
            p.drawRoundedRect(QRectF(4.0, 6.5, 12.0, 14.0), 2.6, 2.6)
            lens = QPainterPath()
            lens.moveTo(16.0, 10.6)
            lens.lineTo(22.4, 7.2)
            lens.lineTo(22.4, 19.8)
            lens.lineTo(16.0, 16.4)
            lens.closeSubpath()
            p.drawPath(lens)
            p.setBrush(QBrush(color))
            p.drawEllipse(QRectF(8.6, 12.1, 2.8, 2.8))
            p.setBrush(Qt.NoBrush)
        elif name == 'history':
            p.drawEllipse(QRectF(4.5, 4.5, 18.0, 18.0))
            p.drawLine(QPointF(13.5, 13.5), QPointF(13.5, 8.2))
            p.drawLine(QPointF(13.5, 13.5), QPointF(17.4, 15.2))
        elif name == 'window_pin':
            p.drawRoundedRect(QRectF(4.0, 5.0, 19.0, 17.0), 2.6, 2.6)
            p.drawLine(QPointF(4.0, 9.6), QPointF(23.0, 9.6))
            p.setBrush(QBrush(color))
            p.drawEllipse(QRectF(6.5, 6.8, 1.4, 1.4))
            p.drawEllipse(QRectF(9.5, 6.8, 1.4, 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(13.5, 19.5), QPointF(13.5, 12.2))
            arr = QPainterPath()
            arr.moveTo(10.2, 15.2)
            arr.lineTo(13.5, 11.8)
            arr.lineTo(16.8, 15.2)
            p.drawPath(arr)
        else:
            return False
        return True

    @staticmethod
    def _draw_transform_icon(p, name, color, stroke):
        if name == 'rotate':
            p.drawRoundedRect(QRectF(4.4, 11.2, 11.2, 11.2), 2.2, 2.2)
            arc = QPainterPath()
            arc.arcMoveTo(QRectF(7.6, 4.4, 14.0, 14.0), 112)
            arc.arcTo(QRectF(7.6, 4.4, 14.0, 14.0), 112, -122)
            p.drawPath(arc)
            head = QPainterPath()
            head.moveTo(18.2, 10.6)
            head.lineTo(21.4, 13.0)
            head.lineTo(23.6, 9.6)
            p.drawPath(head)
        elif name == 'rotate_ccw':
            arc = QPainterPath()
            arc.arcMoveTo(QRectF(5.5, 5.5, 16.0, 16.0), -45)
            arc.arcTo(QRectF(5.5, 5.5, 16.0, 16.0), -45, 225)
            p.drawPath(arc)
            head = QPainterPath()
            head.moveTo(3.4, 10.6)
            head.lineTo(5.5, 14.2)
            head.lineTo(8.6, 11.4)
            p.drawPath(head)
        elif name == 'rotate_cw':
            arc = QPainterPath()
            arc.arcMoveTo(QRectF(5.5, 5.5, 16.0, 16.0), 225)
            arc.arcTo(QRectF(5.5, 5.5, 16.0, 16.0), 225, -225)
            p.drawPath(arc)
            head = QPainterPath()
            head.moveTo(18.4, 11.4)
            head.lineTo(21.5, 14.2)
            head.lineTo(23.6, 10.6)
            p.drawPath(head)
        elif name == 'rotate_180':
            top_arc = QPainterPath()
            top_arc.arcMoveTo(QRectF(5.2, 5.2, 16.6, 16.6), 155)
            top_arc.arcTo(QRectF(5.2, 5.2, 16.6, 16.6), 155, -130)
            p.drawPath(top_arc)
            h1 = QPainterPath()
            h1.moveTo(18.6, 8.2)
            h1.lineTo(21.4, 10.4)
            h1.lineTo(22.6, 6.8)
            p.drawPath(h1)
            bot_arc = QPainterPath()
            bot_arc.arcMoveTo(QRectF(5.2, 5.2, 16.6, 16.6), -25)
            bot_arc.arcTo(QRectF(5.2, 5.2, 16.6, 16.6), -25, -130)
            p.drawPath(bot_arc)
            h2 = QPainterPath()
            h2.moveTo(4.4, 20.2)
            h2.lineTo(5.6, 16.6)
            h2.lineTo(8.4, 18.8)
            p.drawPath(h2)
        elif name in ('flip_h', 'flip_v'):
            dash_pen = QPen(color, max(1.6, stroke * 0.85), Qt.DashLine, Qt.RoundCap)
            p.save()
            p.setPen(dash_pen)
            if name == 'flip_h':
                p.drawLine(QPointF(13.5, 4.5), QPointF(13.5, 22.5))
            else:
                p.drawLine(QPointF(4.5, 13.5), QPointF(22.5, 13.5))
            p.restore()
            if name == 'flip_h':
                t1_pts = [(10.5, 7.2), (4.6, 13.5), (10.5, 19.8)]
                t2_pts = [(16.5, 7.2), (22.4, 13.5), (16.5, 19.8)]
            else:
                t1_pts = [(7.2, 10.5), (13.5, 4.6), (19.8, 10.5)]
                t2_pts = [(7.2, 16.5), (13.5, 22.4), (19.8, 16.5)]
            for pts, filled in ((t1_pts, False), (t2_pts, True)):
                tri = QPainterPath()
                tri.moveTo(*pts[0])
                tri.lineTo(*pts[1])
                tri.lineTo(*pts[2])
                tri.closeSubpath()
                if filled:
                    p.save()
                    p.setBrush(QBrush(color))
                    p.drawPath(tri)
                    p.restore()
                else:
                    p.drawPath(tri)

    @staticmethod
    def create_pixmap(name, color=QColor("#1D1D1F"), size=44, stroke=2.15):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        if size != 27:
            p.scale(size / 27.0, size / 27.0)
        p.setPen(QPen(color, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)

        if not VectorIconFactory._draw_shape_and_draw_icon(p, name, color):
            if not VectorIconFactory._draw_action_icon(p, name, color):
                VectorIconFactory._draw_transform_icon(p, name, color, stroke)

        p.end()
        return pm

    @staticmethod
    def get_icon(name):
        icon = QIcon()
        icon.addPixmap(VectorIconFactory.create_pixmap(name, QColor("#1D1D1F")), QIcon.Normal, QIcon.Off)
        icon.addPixmap(VectorIconFactory.create_pixmap(name, QColor("#78787D")), QIcon.Disabled, QIcon.Off)
        icon.addPixmap(VectorIconFactory.create_pixmap(name, QColor("#9E6B1E")), QIcon.Normal, QIcon.On)
        return icon


class SegmentedButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(22)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.78), stop:1 rgba(248, 245, 239, 0.62));
                color: #1D1D1F;
                border: 1px solid rgba(142, 142, 147, 0.45);
                border-radius: 5px;
                padding: 1px 6px;
                font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
                font-size: 11px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.96), stop:1 rgba(250, 244, 232, 0.88));
                border: 1.2px solid rgba(182, 134, 58, 0.82);
                color: #7A5110;
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(245, 233, 206, 0.95), stop:1 rgba(235, 217, 178, 0.92));
                border: 1.5px solid #B6863A;
                color: #6B450C;
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(250, 241, 222, 0.95), stop:1 rgba(240, 224, 190, 0.88));
                color: #7A5110;
                border: 1.5px solid #B6863A;
                font-weight: 600;
            }
            QPushButton:disabled {
                background: rgba(244, 244, 245, 0.55);
                color: #9CA3AF;
                border: 1px solid rgba(203, 213, 225, 0.55);
            }
        """)


class RotateControlsWidget(QWidget):
    """共享的旋转与镜像翻转选项面板（支持截图磨砂条与独立图片编辑窗两种风格）。"""
    op_triggered = pyqtSignal(str)       # 'ccw90', 'cw90', 'rot180', 'flip_h', 'flip_v'
    angle_changed = pyqtSignal(int)      # -180..180
    angle_drag_finished = pyqtSignal()
    apply_clicked = pyqtSignal()
    reset_clicked = pyqtSignal()

    def __init__(self, theme="frosted", parent=None):
        super().__init__(parent)
        self._theme = theme
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        def _make_btn(icon_name, label, tip, accent=False):
            if theme == "frosted":
                btn = SegmentedButton(label, self)
                btn.setCheckable(False)
            else:
                btn = QPushButton(label, self)
                btn.setObjectName("codexPrimaryBtn" if accent else "editorToolBtn")
                btn.setFocusPolicy(Qt.NoFocus)
                btn.setAutoDefault(False)
                btn.setDefault(False)
                btn.setFixedHeight(26)
                btn.setCursor(Qt.PointingHandCursor)
            if icon_name:
                fg = "#FFFFFF" if (theme != "frosted" and accent) else "#1D1D1F"
                ico = QIcon()
                ico.addPixmap(VectorIconFactory.create_pixmap(icon_name, QColor(fg)), QIcon.Normal, QIcon.Off)
                ico.addPixmap(VectorIconFactory.create_pixmap(icon_name, QColor("#9CA3AF")), QIcon.Disabled, QIcon.Off)
                btn.setIcon(ico)
                btn.setIconSize(QSize(13, 13))
            if tip:
                btn.setToolTip(tip)
            return btn

        def _add_sep():
            sep = QFrame(self)
            sep.setFixedSize(1, 16)
            sep.setStyleSheet(
                "background: rgba(148, 163, 184, 0.35); border: none; margin: 0 2px;"
                if theme == "frosted"
                else "background: #E4E4E7; border: none; margin: 0 2px;"
            )
            l.addWidget(sep)

        self.btn_ccw90 = _make_btn("rotate_ccw", "左转90°", "逆时针旋转 90°")
        self.btn_cw90 = _make_btn("rotate_cw", "右转90°", "顺时针旋转 90°")
        self.btn_180 = _make_btn("rotate_180", "180°", "旋转 180°")
        self.btn_ccw90.clicked.connect(lambda: self.op_triggered.emit("ccw90"))
        self.btn_cw90.clicked.connect(lambda: self.op_triggered.emit("cw90"))
        self.btn_180.clicked.connect(lambda: self.op_triggered.emit("rot180"))
        l.addWidget(self.btn_ccw90)
        l.addWidget(self.btn_cw90)
        l.addWidget(self.btn_180)

        _add_sep()

        self.btn_flip_h = _make_btn("flip_h", "水平翻转", "左右镜像翻转")
        self.btn_flip_v = _make_btn("flip_v", "垂直翻转", "上下镜像翻转")
        self.btn_flip_h.clicked.connect(lambda: self.op_triggered.emit("flip_h"))
        self.btn_flip_v.clicked.connect(lambda: self.op_triggered.emit("flip_v"))
        l.addWidget(self.btn_flip_h)
        l.addWidget(self.btn_flip_v)

        _add_sep()

        lbl_ang = QLabel("角度:", self)
        if theme != "frosted":
            lbl_ang.setStyleSheet("color: #3F3F46; font-size: 12px; background: transparent;")
        l.addWidget(lbl_ang)

        self.slider_angle = QSlider(Qt.Horizontal, self)
        self.slider_angle.setFocusPolicy(Qt.NoFocus)
        self.slider_angle.setRange(-180, 180)
        self.slider_angle.setSingleStep(1)
        self.slider_angle.setPageStep(15)
        self.slider_angle.setValue(0)
        self.slider_angle.setFixedWidth(98)
        self.slider_angle.setCursor(Qt.PointingHandCursor)
        self.slider_angle.setToolTip("拖动自由旋转任意角度 (-180° ~ +180°)")
        if theme == "frosted":
            self.slider_angle.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 4px;
                    background: rgba(142, 142, 147, 0.35);
                    border-radius: 2px;
                }
                QSlider::handle:horizontal {
                    background: #FFFFFF;
                    border: 1.5px solid #B6863A;
                    width: 12px;
                    height: 12px;
                    margin: -4px 0;
                    border-radius: 7px;
                }
                QSlider::handle:horizontal:hover {
                    background: #FAF1DE;
                }
            """)
        l.addWidget(self.slider_angle)

        self.spin_angle = QSpinBox(self)
        self.spin_angle.setRange(-180, 180)
        self.spin_angle.setValue(0)
        self.spin_angle.setSuffix("°")
        self.spin_angle.setFixedWidth(54)
        self.spin_angle.setToolTip("精确输入旋转角度（正数顺时针，负数逆时针）")
        if theme != "frosted":
            self.spin_angle.setFixedHeight(26)
            self.spin_angle.setStyleSheet(
                "QSpinBox { background: #FFFFFF; color: #18181B; border: 1px solid #E4E4E7; "
                "border-radius: 6px; padding: 1px 4px; font-size: 12px; }"
                "QSpinBox:focus { border: 1.5px solid #0D0D0D; }"
            )
        l.addWidget(self.spin_angle)

        self.btn_apply = _make_btn(None, "应用", "确认当前角度旋转", accent=True)
        self.btn_reset = _make_btn(None, "重置", "取消未应用的自由角度预览")
        if theme == "frosted":
            self.btn_apply.setStyleSheet("""
                QPushButton {
                    background: #1f883d;
                    color: #FFFFFF;
                    border: none;
                    border-radius: 5px;
                    padding: 1px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background: #1a7f37;
                }
                QPushButton:disabled {
                    background: rgba(203, 213, 225, 0.55);
                    color: #9CA3AF;
                }
            """)
        self.btn_apply.setEnabled(False)
        self.btn_reset.setEnabled(False)
        self.btn_apply.clicked.connect(self.apply_clicked.emit)
        self.btn_reset.clicked.connect(self.reset_clicked.emit)
        l.addWidget(self.btn_apply)
        l.addWidget(self.btn_reset)

        self.slider_angle.valueChanged.connect(self._on_slider_changed)
        self.slider_angle.sliderReleased.connect(self.angle_drag_finished.emit)
        self.spin_angle.valueChanged.connect(self._on_spin_changed)

    def _sync_action_buttons(self, angle):
        has_angle = int(angle) != 0
        self.btn_apply.setEnabled(has_angle)
        self.btn_reset.setEnabled(has_angle)

    def _on_slider_changed(self, val):
        prev = self.spin_angle.blockSignals(True)
        try:
            self.spin_angle.setValue(val)
        finally:
            self.spin_angle.blockSignals(prev)
        self._sync_action_buttons(val)
        self.angle_changed.emit(int(val))

    def _on_spin_changed(self, val):
        prev = self.slider_angle.blockSignals(True)
        try:
            self.slider_angle.setValue(val)
        finally:
            self.slider_angle.blockSignals(prev)
        self._sync_action_buttons(val)
        self.angle_changed.emit(int(val))

    def current_angle(self):
        return int(self.spin_angle.value())

    def set_angle(self, angle, emit=False):
        val = max(-180, min(180, int(angle)))
        p1 = self.slider_angle.blockSignals(True)
        p2 = self.spin_angle.blockSignals(True)
        try:
            self.slider_angle.setValue(val)
            self.spin_angle.setValue(val)
        finally:
            self.slider_angle.blockSignals(p1)
            self.spin_angle.blockSignals(p2)
        self._sync_action_buttons(val)
        if emit:
            self.angle_changed.emit(val)

    def is_dragging_angle(self):
        return bool(self.slider_angle.isSliderDown())


class ColorPaletteWidget(QWidget):
    color_changed = pyqtSignal(QColor)

    PRESETS = [
        "#FF3B30", # 经典红
        "#FF9500", # 橙色
        "#FFCC00", # 黄色
        "#34C759", # 绿色
        "#00C7BE", # 青绿
        "#007AFF", # 蓝色
        "#AF52DE", # 紫色
        "#000000", # 黑色
        "#FFFFFF", # 白色
    ]

    def __init__(self, parent=None, default_color="#FF3B30", presets=None, swatch_size=18, spacing=4, theme="frosted"):
        super().__init__(parent)
        self.presets = presets or self.PRESETS
        self._swatch_size = int(swatch_size)
        self._theme = theme
        self.current_color = QColor(default_color)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 0, 1, 0)
        layout.setSpacing(int(spacing))

        self.buttons = []
        for hex_col in self.presets:
            btn = QPushButton()
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setFixedSize(self._swatch_size, self._swatch_size)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, c=hex_col: self.set_color(QColor(c), emit=True))
            layout.addWidget(btn)
            self.buttons.append((btn, hex_col))

        self.btn_custom = QPushButton("🎨")
        self.btn_custom.setFocusPolicy(Qt.NoFocus)
        self.btn_custom.setFixedSize(22, max(20, self._swatch_size + 2))
        self.btn_custom.setToolTip("选择自定义颜色...")
        self.btn_custom.setCursor(Qt.PointingHandCursor)
        if self._theme == "codex":
            self.btn_custom.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF;
                    border: 1px solid #E4E4E7;
                    border-radius: 5px;
                    font-size: 10px;
                    padding: 0;
                }
                QPushButton:hover {
                    background: #F4F4F5;
                    border-color: #0D0D0D;
                }
            """)
        else:
            self.btn_custom.setStyleSheet("""
                QPushButton {
                    background: #F6F8FA;
                    border: 1px solid #D0D7DE;
                    border-radius: 4px;
                    font-size: 10px;
                    padding: 0;
                }
                QPushButton:hover {
                    background: #ECECEC;
                    border-color: #B6863A;
                }
            """)
        self.btn_custom.clicked.connect(self._pick_custom)
        layout.addWidget(self.btn_custom)

        self._refresh()

    def set_color(self, color, emit=False):
        self.current_color = QColor(color)
        self._refresh()
        if emit:
            self.color_changed.emit(self.current_color)

    def _pick_custom(self):
        col = QColorDialog.getColor(self.current_color, self, "选择自定义颜色")
        if col.isValid():
            self.set_color(col, emit=True)

    def _refresh(self):
        cur_hex = self.current_color.name().upper()
        rad = max(4, self._swatch_size // 2)
        hover_col = "#0D0D0D" if self._theme == "codex" else "#B6863A"
        for btn, hex_col in self.buttons:
            is_active = (cur_hex == hex_col.upper())
            border = "border: 2px solid #1D1D1F;" if is_active else ("border: 1px solid #DCDCDC;" if hex_col.upper() == "#FFFFFF" else "border: 1px solid transparent;")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {hex_col};
                    border-radius: {rad}px;
                    {border}
                }}
                QPushButton:hover {{
                    border: 2px solid {hover_col};
                }}
            """)


class SnipasteToolbar(FrostedBarWidget):
    tool_triggered = pyqtSignal(str)   # 'RECT_ELLIPSE', 'ARROW', 'PEN', 'MARKER', 'MOSAIC_BLUR', 'TEXT', 'ERASER'
    action_triggered = pyqtSignal(str) # 'UNDO', 'REDO', 'CROP', 'OCR', 'TRANSLATE', 'SCROLL', 'RECORD', 'HISTORY', 'CLOSE', 'PIN', 'SAVE', 'COPY'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("snipasteToolbar")
        self.setStyleSheet("""
            QWidget#snipasteToolbar {
                background: transparent;
            }
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                padding: 0px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.78);
                border: 1px solid rgba(182, 134, 58, 0.55);
            }
            QPushButton:pressed {
                background: rgba(248, 238, 214, 0.90);
                border: 1px solid rgba(182, 134, 58, 0.85);
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(250, 241, 222, 0.95), stop:1 rgba(240, 224, 190, 0.90));
                border: 1.5px solid #B6863A;
            }
            QPushButton:disabled {
                background: transparent;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.SHADOW_PAD + 6, self.SHADOW_PAD + 3, self.SHADOW_PAD + 6, self.SHADOW_PAD + 3)
        layout.setSpacing(2)

        self.tool_buttons = {}

        # --- 组 1: 绘图标注 ---
        # 1. 矩形 / 椭圆 (合并图元)
        self.btn_rect_ellipse = self._create_btn("rect_ellipse", "矩形 / 椭圆", checkable=True)
        self.btn_rect_ellipse.clicked.connect(lambda: self._on_tool_btn_clicked('RECT_ELLIPSE'))
        layout.addWidget(self.btn_rect_ellipse)
        self.tool_buttons['RECT_ELLIPSE'] = self.btn_rect_ellipse

        # 2. 箭头
        self.btn_arrow = self._create_btn("arrow", "箭头", checkable=True)
        self.btn_arrow.clicked.connect(lambda: self._on_tool_btn_clicked('ARROW'))
        layout.addWidget(self.btn_arrow)
        self.tool_buttons['ARROW'] = self.btn_arrow

        # 3. 画笔
        self.btn_pen = self._create_btn("pen", "画笔 (拖拽画单条直线，点按画折线)", checkable=True)
        self.btn_pen.clicked.connect(lambda: self._on_tool_btn_clicked('PEN'))
        layout.addWidget(self.btn_pen)
        self.tool_buttons['PEN'] = self.btn_pen

        # 4. 记号笔
        self.btn_marker = self._create_btn("marker", "记号笔 / 荧光笔", checkable=True)
        self.btn_marker.clicked.connect(lambda: self._on_tool_btn_clicked('MARKER'))
        layout.addWidget(self.btn_marker)
        self.tool_buttons['MARKER'] = self.btn_marker

        # 5. 马赛克 / 模糊
        self.btn_mosaic_blur = self._create_btn("mosaic_blur", "马赛克 / 高斯模糊", checkable=True)
        self.btn_mosaic_blur.clicked.connect(lambda: self._on_tool_btn_clicked('MOSAIC_BLUR'))
        layout.addWidget(self.btn_mosaic_blur)
        self.tool_buttons['MOSAIC_BLUR'] = self.btn_mosaic_blur

        # 6. 文本
        self.btn_text = self._create_btn("text", "文本", checkable=True)
        self.btn_text.clicked.connect(lambda: self._on_tool_btn_clicked('TEXT'))
        layout.addWidget(self.btn_text)
        self.tool_buttons['TEXT'] = self.btn_text

        # 7. 橡皮擦
        self.btn_eraser = self._create_btn("eraser", "橡皮擦 (拖拽整项擦除)", checkable=True)
        self.btn_eraser.clicked.connect(lambda: self._on_tool_btn_clicked('ERASER'))
        layout.addWidget(self.btn_eraser)
        self.tool_buttons['ERASER'] = self.btn_eraser

        # 分隔线 1: 1 × 24 px, #D4D4D4
        layout.addWidget(self._create_divider())

        # --- 组 2: 撤销 / 重做 ---
        # 8. 撤销
        self.btn_undo = self._create_btn("undo", "撤销 (Ctrl+Z)", checkable=False)
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(lambda: self.action_triggered.emit('UNDO'))
        layout.addWidget(self.btn_undo)

        # 9. 重做
        self.btn_redo = self._create_btn("redo", "重做 (Ctrl+Y)", checkable=False)
        self.btn_redo.setEnabled(False)
        self.btn_redo.clicked.connect(lambda: self.action_triggered.emit('REDO'))
        layout.addWidget(self.btn_redo)

        # 分隔线 2: 1 × 24 px, #D4D4D4
        layout.addWidget(self._create_divider())

        # --- 组 3: 扩展功能 ---
        # 10. 裁剪
        self.btn_crop = self._create_btn("crop", "裁剪选区", checkable=False)
        self.btn_crop.clicked.connect(lambda: self.action_triggered.emit('CROP'))
        layout.addWidget(self.btn_crop)

        # 11. 旋转 / 翻转
        self.btn_rotate = self._create_btn("rotate", "旋转 / 翻转", checkable=True)
        self.btn_rotate.clicked.connect(lambda: self._on_tool_btn_clicked('ROTATE'))
        layout.addWidget(self.btn_rotate)
        self.tool_buttons['ROTATE'] = self.btn_rotate

        # 12. 识图翻译
        self.btn_ocr = self._create_btn("ocr", "文字识别与多语言翻译 (F2)", checkable=False)
        self.btn_ocr.clicked.connect(lambda: self.action_triggered.emit('OCR'))
        layout.addWidget(self.btn_ocr)

        # 12. 一键翻译 (识别 + 翻译，直接出结果)
        self.btn_ocr_translate = self._create_btn("translate", "一键翻译当前截图 (结果显示方式可在设置中切换)", checkable=False)
        self.btn_ocr_translate.clicked.connect(lambda: self.action_triggered.emit('TRANSLATE'))
        layout.addWidget(self.btn_ocr_translate)

        # 13. 滚动截图
        self.btn_scroll = self._create_btn("scroll", "滚动长截图 (Space)", checkable=False)
        self.btn_scroll.clicked.connect(lambda: self.action_triggered.emit('SCROLL'))
        layout.addWidget(self.btn_scroll)

        # 14. 屏幕录制
        self.btn_record = self._create_btn("record", "屏幕录制 (F4)", checkable=False)
        self.btn_record.clicked.connect(lambda: self.action_triggered.emit('RECORD'))
        layout.addWidget(self.btn_record)

        # 15. 截图历史
        self.btn_history = self._create_btn("history", "截图历史", checkable=False)
        self.btn_history.clicked.connect(lambda: self.action_triggered.emit('HISTORY'))
        layout.addWidget(self.btn_history)

        # 16. 窗口置顶 (DeskPin)
        self.btn_win_pin = self._create_btn("window_pin", "窗口置顶 (DeskPin: 点击任意窗口将其置顶)", checkable=False)
        self.btn_win_pin.clicked.connect(lambda: self.action_triggered.emit('WINDOW_PIN'))
        layout.addWidget(self.btn_win_pin)

        # 分隔线 3: 1 × 24 px, #D4D4D4
        layout.addWidget(self._create_divider())

        # --- 组 4: 结果操作 ---
        # 15. 关闭 / 取消
        self.btn_close = self._create_btn("close", "退出截图 (Esc)", checkable=False)
        self.btn_close.clicked.connect(lambda: self.action_triggered.emit('CLOSE'))
        layout.addWidget(self.btn_close)

        # 16. 贴图
        self.btn_pin = self._create_btn("pin", "贴图到桌面 (F3)", checkable=False)
        self.btn_pin.clicked.connect(lambda: self.action_triggered.emit('PIN'))
        layout.addWidget(self.btn_pin)

        # 17. 保存
        self.btn_save = self._create_btn("save", "保存到文件 (Ctrl+S)", checkable=False)
        self.btn_save.clicked.connect(lambda: self.action_triggered.emit('SAVE'))
        layout.addWidget(self.btn_save)

        # 18. 复制完成
        self.btn_copy = self._create_btn("copy", "复制到剪贴板并退出 (Ctrl+C / 回车)", checkable=False)
        self.btn_copy.clicked.connect(lambda: self.action_triggered.emit('COPY'))
        layout.addWidget(self.btn_copy)

        self.refresh_tooltips()

    def refresh_tooltips(self, settings=None):
        """根据最新快捷键配置动态刷新底部工具栏按钮悬浮提示"""
        s = settings if isinstance(settings, dict) else load_settings()

        def _fmt(base_tip, key_name, default_val=""):
            hk = normalize_hotkey_str(s.get(key_name, default_val))
            return f"{base_tip} ({hk})" if hk else base_tip

        self.btn_rect_ellipse.setToolTip(_fmt("矩形 / 椭圆", "hotkey_tool_rect_ellipse"))
        self.btn_arrow.setToolTip(_fmt("箭头", "hotkey_tool_arrow"))
        self.btn_pen.setToolTip(_fmt("画笔 (拖拽画单条直线，点按画折线)", "hotkey_tool_pen"))
        self.btn_marker.setToolTip(_fmt("记号笔 / 荧光笔", "hotkey_tool_marker"))
        self.btn_mosaic_blur.setToolTip(_fmt("马赛克 / 高斯模糊", "hotkey_tool_mosaic_blur"))
        self.btn_text.setToolTip(_fmt("文本", "hotkey_tool_text"))
        self.btn_eraser.setToolTip(_fmt("橡皮擦 (拖拽整项擦除)", "hotkey_tool_eraser"))
        self.btn_crop.setToolTip(_fmt("裁剪选区", "hotkey_tool_crop"))
        self.btn_rotate.setToolTip(_fmt("旋转 / 翻转", "hotkey_tool_rotate"))
        self.btn_ocr.setToolTip(_fmt("文字识别与多语言翻译", "hotkey_ocr", "F2"))
        self.btn_ocr_translate.setToolTip(_fmt("一键翻译当前截图 (结果显示方式可在设置中切换)", "hotkey_tool_translate"))
        hk_scroll = normalize_hotkey_str(s.get("hotkey_scroll", ""))
        self.btn_scroll.setToolTip(
            f"滚动长截图 ({hk_scroll} · 开始后按 Space 暂停/完成)" if hk_scroll else "滚动长截图 (开始后按 Space 暂停/完成)"
        )
        self.btn_record.setToolTip(_fmt("屏幕录制", "hotkey_record", "F4"))
        self.btn_history.setToolTip(_fmt("截图历史", "hotkey_history"))
        self.btn_win_pin.setToolTip(_fmt("窗口置顶 (DeskPin: 点击任意窗口将其置顶)", "hotkey_window_pin"))
        self.btn_pin.setToolTip(_fmt("贴图到桌面", "hotkey_pin", "F3"))

    def _on_tool_btn_clicked(self, tool_name):
        # 严格互斥：点击一个工具时，立即将其它工具全部反选，杜绝多工具叠加
        for name, btn in self.tool_buttons.items():
            if name != tool_name:
                btn.setChecked(False)
        self.tool_triggered.emit(tool_name)

    def _create_btn(self, icon_name, tooltip, checkable=False):
        btn = QPushButton()
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setFixedSize(32, 32)
        btn.setIcon(VectorIconFactory.get_icon(icon_name))
        btn.setIconSize(QSize(22, 22))
        btn.setCheckable(checkable)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setText = lambda txt: btn.setToolTip(txt) if txt else None
        return btn

    def _create_divider(self):
        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("background: rgba(148, 163, 184, 0.35); border: none; margin: 0 1px;")
        return sep

    def set_active_tool(self, tool_name):
        for name, btn in self.tool_buttons.items():
            btn.setChecked(name == tool_name)

    def update_undo_redo(self, can_undo, can_redo):
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)


class TextSelectionActionBar(FrostedBarWidget):
    """划选文字后贴近选区悬浮的快捷操作条：复制、复制并退出、表格、翻译、打开链接。"""
    action_triggered = pyqtSignal(str)  # 'COPY', 'COPY_EXIT', 'TABLE', 'TRANSLATE', 'OPEN_URL'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("textSelectionActionBar")
        self.setStyleSheet("""
            QWidget#textSelectionActionBar {
                background: transparent;
            }
            QLabel#selCountBadge {
                color: #7A5110;
                background: rgba(251, 247, 238, 0.92);
                border: 1px solid rgba(229, 210, 174, 0.95);
                border-radius: 6px;
                padding: 2px 7px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 4px 9px;
                color: #18181B;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(182, 134, 58, 0.55);
            }
            QPushButton:pressed {
                background: rgba(248, 238, 214, 0.95);
                border: 1px solid rgba(182, 134, 58, 0.85);
            }
            QPushButton#primaryCopyBtn {
                background: rgba(182, 134, 58, 0.14);
                border: 1px solid rgba(182, 134, 58, 0.55);
                color: #7A5110;
                font-weight: 600;
            }
            QPushButton#primaryCopyBtn:hover {
                background: rgba(182, 134, 58, 0.24);
                border: 1px solid rgba(182, 134, 58, 0.85);
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.SHADOW_PAD + 6, self.SHADOW_PAD + 3, self.SHADOW_PAD + 6, self.SHADOW_PAD + 3)
        layout.setSpacing(4)

        self.lbl_count = QLabel("0 字")
        self.lbl_count.setObjectName("selCountBadge")
        layout.addWidget(self.lbl_count)

        def _make_btn(text, tip, action, obj_name=None):
            btn = QPushButton(text)
            if obj_name:
                btn.setObjectName(obj_name)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setFont(make_ui_font(pixel_size=12))
            btn.clicked.connect(lambda _=False, a=action: self.action_triggered.emit(a))
            layout.addWidget(btn)
            return btn

        self.btn_copy = _make_btn("复制", "复制选中文字 (Ctrl+C)", "COPY", "primaryCopyBtn")
        self.btn_copy_exit = _make_btn("复制并退出", "复制选中文字并退出截图", "COPY_EXIT")
        self.btn_table = _make_btn("表格", "按行列结构复制为 Excel 表格 (TSV)", "TABLE")
        self.btn_translate = _make_btn("翻译", "翻译当前选中文字", "TRANSLATE")
        self.btn_open_url = _make_btn("打开链接", "在浏览器中打开选中链接", "OPEN_URL")

        self.btn_table.hide()
        self.btn_open_url.hide()

    def configure(self, char_count, has_table=False, has_url=False):
        self.lbl_count.setText(f"{char_count} 字")
        self.btn_table.setVisible(bool(has_table))
        self.btn_open_url.setVisible(bool(has_url))
        self.adjustSize()


class _ToolOptionsPanelsMixin:
    """共享的二级工具选项栏面板构建与状态读取混入（供悬浮毛玻璃栏与独立编辑器栏复用）。"""
    _opt_theme = "frosted"
    _opt_panel_stretch = False
    _opt_divider_qss = "background: rgba(148, 163, 184, 0.35); border: none; margin: 0 2px;"
    _opt_hint_qss = "color: #6E7781; font-size: 10px; margin-left: 4px;"
    _opt_rotate_theme = "frosted"
    _opt_text_bold_w = 26

    def _add_divider(self, layout):
        sep = QFrame(self)
        sep.setFixedSize(1, 16)
        sep.setStyleSheet(self._opt_divider_qss)
        layout.addWidget(sep)

    def _make_opt_btn(self, text, checked=False, checkable=True, tip="", fixed_w=None):
        if self._opt_theme == "codex":
            btn = QPushButton(text, self)
            btn.setObjectName("subSegBtn")
            btn.setCheckable(checkable)
            btn.setChecked(checked if checkable else False)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedHeight(24)
            btn.setCursor(Qt.PointingHandCursor)
        else:
            btn = SegmentedButton(text, self)
            if not checkable:
                btn.setCheckable(False)
            elif checked:
                btn.setChecked(True)
        if fixed_w is not None:
            btn.setFixedWidth(fixed_w)
        if tip:
            btn.setToolTip(tip)
        return btn

    def _make_opt_spin(self, min_v, max_v, val, width=44, suffix=""):
        sb = QSpinBox(self)
        sb.setRange(min_v, max_v)
        sb.setValue(val)
        sb.setFixedWidth(width)
        if suffix:
            sb.setSuffix(suffix)
        sb.valueChanged.connect(self.options_changed.emit)
        return sb

    def _make_opt_palette(self, default_color="#FF3B30", presets=None):
        if self._opt_theme == "codex":
            pal = ColorPaletteWidget(
                self, default_color=default_color, presets=presets,
                swatch_size=15, spacing=3, theme="codex"
            )
        else:
            pal = ColorPaletteWidget(default_color=default_color, presets=presets)
        pal.color_changed.connect(lambda _: self.options_changed.emit())
        return pal

    def _make_opt_combo(self):
        combo = CodexComboBox(self) if self._opt_theme == "codex" else QComboBox()
        combo.setFocusPolicy(Qt.NoFocus)
        return combo

    def _make_opt_hint(self, text):
        lbl = QLabel(text, self)
        if self._opt_theme == "codex":
            lbl.setObjectName("subHintLabel")
        else:
            lbl.setStyleSheet(self._opt_hint_qss)
        return lbl

    def _register_panel(self, tool_name, panel, layout=None):
        if layout is not None and self._opt_panel_stretch:
            layout.addStretch(1)
        idx = self.stack.addWidget(panel)
        self.tool_indices[tool_name] = idx

    def _build_shape_panel(self, tool_name, with_kind_toggle=False, with_radius=True, hint_text=""):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        btn_rect = btn_ellipse = None
        if with_kind_toggle:
            btn_rect = self._make_opt_btn("矩形 ▢", checked=True)
            btn_ellipse = self._make_opt_btn("椭圆 ◯")
            l.addWidget(btn_rect)
            l.addWidget(btn_ellipse)
            self._add_divider(l)

        btn_none = self._make_opt_btn("无填充", checked=True)
        btn_stroke = self._make_opt_btn("带框填充" if self._opt_theme == "codex" else "带边框填充")
        btn_solid = self._make_opt_btn("实心填充")
        l.addWidget(btn_none)
        l.addWidget(btn_stroke)
        l.addWidget(btn_solid)

        self._add_divider(l)

        lbl_w = QLabel("宽度:", panel)
        l.addWidget(lbl_w)
        spin_w = self._make_opt_spin(1, 24, 3, width=44 if self._opt_theme == "codex" else 42)
        l.addWidget(spin_w)

        lbl_r = spin_r = None
        if with_radius:
            lbl_r = QLabel("圆角:", panel)
            l.addWidget(lbl_r)
            spin_r = self._make_opt_spin(0, 30, 0, width=44 if self._opt_theme == "codex" else 42)
            l.addWidget(spin_r)

        self._add_divider(l)

        lbl_stroke = QLabel("描边:", panel)
        stroke_pal = self._make_opt_palette(default_color="#FF3B30")
        l.addWidget(lbl_stroke)
        l.addWidget(stroke_pal)

        lbl_fill = QLabel("填充:", panel)
        fill_pal = self._make_opt_palette(default_color="#FF3B30")
        l.addWidget(lbl_fill)
        l.addWidget(fill_pal)
        lbl_fill.hide()
        fill_pal.hide()

        if hint_text:
            l.addWidget(self._make_opt_hint(hint_text))

        def _set_kind(kind):
            if btn_rect is not None and btn_ellipse is not None:
                btn_rect.setChecked(kind == 'rect')
                btn_ellipse.setChecked(kind == 'ellipse')
            if lbl_r is not None and spin_r is not None:
                lbl_r.setEnabled(kind == 'rect')
                spin_r.setEnabled(kind == 'rect')
            self.options_changed.emit()

        def _set_fill(mode):
            btn_none.setChecked(mode == 'none')
            btn_stroke.setChecked(mode == 'stroke_fill')
            btn_solid.setChecked(mode == 'fill')
            has_fill = (mode != 'none')
            if self._opt_theme == "codex":
                has_stroke = (mode != 'fill')
                lbl_stroke.setVisible(has_stroke)
                stroke_pal.setVisible(has_stroke)
                lbl_w.setEnabled(has_stroke)
                spin_w.setEnabled(has_stroke)
            lbl_fill.setVisible(has_fill)
            fill_pal.setVisible(has_fill)
            self.options_changed.emit()

        if btn_rect is not None and btn_ellipse is not None:
            btn_rect.clicked.connect(lambda: _set_kind('rect'))
            btn_ellipse.clicked.connect(lambda: _set_kind('ellipse'))
        btn_none.clicked.connect(lambda: _set_fill('none'))
        btn_stroke.clicked.connect(lambda: _set_fill('stroke_fill'))
        btn_solid.clicked.connect(lambda: _set_fill('fill'))

        self._register_panel(tool_name, panel, l)
        return {
            'btn_rect': btn_rect, 'btn_ellipse': btn_ellipse,
            'btn_none': btn_none, 'btn_stroke': btn_stroke, 'btn_solid': btn_solid,
            'lbl_r': lbl_r, 'spin_w': spin_w, 'spin_r': spin_r,
            'stroke_pal': stroke_pal, 'fill_pal': fill_pal,
            'set_kind': _set_kind, 'set_fill': _set_fill,
        }

    @staticmethod
    def _read_shape_options(refs, default_kind='rect'):
        kind = default_kind
        if refs.get('btn_ellipse') is not None:
            kind = 'ellipse' if refs['btn_ellipse'].isChecked() else 'rect'
        if refs['btn_solid'].isChecked():
            fill_mode = 'fill'
        elif refs['btn_stroke'].isChecked():
            fill_mode = 'stroke_fill'
        else:
            fill_mode = 'none'
        spin_r = refs.get('spin_r')
        return {
            'kind': kind,
            'fillMode': fill_mode,
            'strokeWidth': refs['spin_w'].value(),
            'cornerRadius': spin_r.value() if (spin_r is not None and kind == 'rect') else 0,
            'strokeColor': refs['stroke_pal'].current_color,
            'fillColor': refs['fill_pal'].current_color,
        }

    def _init_arrow_panel(self, tool_name='ARROW', none_label="关 ━", show_hint=False):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        self.btn_arr_end = self._make_opt_btn("结尾 ➔", checked=True)
        self.btn_arr_start = self._make_opt_btn("开始 ⬅")
        self.btn_arr_both = self._make_opt_btn("双向 ⬌")
        self.btn_arr_none = self._make_opt_btn(none_label)
        self.btn_arr_end.clicked.connect(lambda: self._set_arr_mode('end'))
        self.btn_arr_start.clicked.connect(lambda: self._set_arr_mode('start'))
        self.btn_arr_both.clicked.connect(lambda: self._set_arr_mode('both'))
        self.btn_arr_none.clicked.connect(lambda: self._set_arr_mode('none'))
        l.addWidget(self.btn_arr_end)
        l.addWidget(self.btn_arr_start)
        l.addWidget(self.btn_arr_both)
        l.addWidget(self.btn_arr_none)

        self._add_divider(l)

        l.addWidget(QLabel("宽度:", panel))
        self.spin_arr_width = self._make_opt_spin(1, 24, 3, width=44 if self._opt_theme == "codex" else 42)
        self.spin_arrow_width = self.spin_arr_width
        l.addWidget(self.spin_arr_width)

        self._add_divider(l)

        l.addWidget(QLabel("颜色:", panel))
        self.arr_palette = self._make_opt_palette(default_color="#FF3B30")
        self.arrow_palette = self.arr_palette
        l.addWidget(self.arr_palette)

        if show_hint:
            l.addWidget(self._make_opt_hint("💡 Shift 对齐 45°"))

        self._register_panel(tool_name, panel, l)

    def _set_arr_mode(self, mode):
        self.btn_arr_end.setChecked(mode == 'end')
        self.btn_arr_start.setChecked(mode == 'start')
        self.btn_arr_both.setChecked(mode == 'both')
        self.btn_arr_none.setChecked(mode == 'none')
        self.options_changed.emit()

    def get_arrow_options(self):
        mode = 'end'
        if self.btn_arr_start.isChecked():
            mode = 'start'
        elif self.btn_arr_both.isChecked():
            mode = 'both'
        elif self.btn_arr_none.isChecked():
            mode = 'none'
        return {
            'arrowMode': mode,
            'width': self.spin_arr_width.value(),
            'color': self.arr_palette.current_color,
        }

    def _init_pen_panel(self, tool_name='PEN', poly_label="折线 〰", hint_text="💡 拖拽画单条直线，点按画折线"):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        self.btn_pen_poly = self._make_opt_btn(poly_label, checked=True)
        self.btn_pen_line = self._make_opt_btn("直线 ╱")
        self.btn_pen_poly.clicked.connect(lambda: self._set_pen_style('polyline'))
        self.btn_pen_line.clicked.connect(lambda: self._set_pen_style('line'))
        l.addWidget(self.btn_pen_poly)
        l.addWidget(self.btn_pen_line)

        self._add_divider(l)

        l.addWidget(QLabel("宽度:", panel))
        self.spin_pen_width = self._make_opt_spin(1, 24, 3, width=44 if self._opt_theme == "codex" else 42)
        l.addWidget(self.spin_pen_width)

        self._add_divider(l)

        l.addWidget(QLabel("颜色:", panel))
        self.pen_palette = self._make_opt_palette(default_color="#FF3B30")
        l.addWidget(self.pen_palette)

        if hint_text:
            l.addWidget(self._make_opt_hint(hint_text))

        self._register_panel(tool_name, panel, l)

    def _set_pen_style(self, style):
        self.btn_pen_poly.setChecked(style == 'polyline')
        self.btn_pen_line.setChecked(style == 'line')
        self.options_changed.emit()

    def get_pen_options(self):
        return {
            'penStyle': 'line' if self.btn_pen_line.isChecked() else 'polyline',
            'width': self.spin_pen_width.value(),
            'color': self.pen_palette.current_color,
        }

    def _init_marker_panel(self, tool_name='MARKER'):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        l.addWidget(QLabel("颜色:", panel))
        marker_presets = ["#FFE600", "#39FF14", "#FF1493", "#00E5FF", "#FF8C00", "#A855F7", "#FF3B30", "#000000"]
        self.marker_palette = self._make_opt_palette(default_color="#FFE600", presets=marker_presets)
        l.addWidget(self.marker_palette)

        self._add_divider(l)

        l.addWidget(QLabel("宽度:", panel))
        self.spin_marker_width = self._make_opt_spin(6, 50, 16, width=46 if self._opt_theme == "codex" else 44)
        l.addWidget(self.spin_marker_width)

        l.addWidget(QLabel("不透明度:", panel))
        self.spin_marker_opacity = self._make_opt_spin(15, 90, 45, width=54 if self._opt_theme == "codex" else 52, suffix="%")
        l.addWidget(self.spin_marker_opacity)

        self._add_divider(l)

        self.btn_tip_round = self._make_opt_btn("圆头 ◐", checked=True)
        self.btn_tip_flat = self._make_opt_btn("平头 ◼")
        self.btn_tip_round.clicked.connect(lambda: self._set_tip_style('round'))
        self.btn_tip_flat.clicked.connect(lambda: self._set_tip_style('flat'))
        l.addWidget(self.btn_tip_round)
        l.addWidget(self.btn_tip_flat)

        self._register_panel(tool_name, panel, l)

    def _set_tip_style(self, style):
        self.btn_tip_round.setChecked(style == 'round')
        self.btn_tip_flat.setChecked(style == 'flat')
        self.options_changed.emit()

    def get_marker_options(self):
        return {
            'color': self.marker_palette.current_color,
            'width': self.spin_marker_width.value(),
            'opacity': self.spin_marker_opacity.value(),
            'tipStyle': 'flat' if self.btn_tip_flat.isChecked() else 'round',
        }

    def _init_mosaic_blur_panel(self, tool_name='MOSAIC_BLUR'):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        self.btn_mb_mosaic = self._make_opt_btn("马赛克 ▨", checked=True, tip="切换马赛克 / 高斯模糊")
        self.btn_mb_blur = self._make_opt_btn("高斯模糊 💧", tip="切换马赛克 / 高斯模糊")
        self.btn_mb_mosaic.clicked.connect(lambda: self._set_mb_mode('mosaic'))
        self.btn_mb_blur.clicked.connect(lambda: self._set_mb_mode('blur'))
        l.addWidget(self.btn_mb_mosaic)
        l.addWidget(self.btn_mb_blur)

        self._add_divider(l)

        self.btn_mb_smooth = self._make_opt_btn("像素平滑 ≋", tip="开启柔化像素边缘，关闭为硬边方块")
        self.btn_mb_smooth.clicked.connect(self.options_changed.emit)
        l.addWidget(self.btn_mb_smooth)

        self._add_divider(l)

        spin_w = 46 if self._opt_theme == "codex" else 44
        l.addWidget(QLabel("粒度:", panel))
        self.spin_mb_block = self._make_opt_spin(4, 32, 10, width=spin_w)
        l.addWidget(self.spin_mb_block)

        l.addWidget(QLabel("笔尖:", panel))
        self.spin_mb_brush = self._make_opt_spin(8, 60, 22, width=spin_w)
        l.addWidget(self.spin_mb_brush)

        self._register_panel(tool_name, panel, l)

    def _set_mb_mode(self, mode):
        self.btn_mb_mosaic.setChecked(mode == 'mosaic')
        self.btn_mb_blur.setChecked(mode == 'blur')
        self.btn_mb_smooth.setEnabled(mode == 'mosaic')
        self.options_changed.emit()

    def get_mosaic_options(self):
        return {
            'mode': 'blur' if self.btn_mb_blur.isChecked() else 'mosaic',
            'smooth': self.btn_mb_smooth.isChecked(),
            'blockSize': self.spin_mb_block.value(),
            'brushWidth': self.spin_mb_brush.value(),
        }

    def _init_text_panel(self, tool_name='TEXT'):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        self.combo_text_font = self._make_opt_combo()
        self.combo_text_font.addItems(["Microsoft YaHei", "Segoe UI", "Arial", "SimHei", "Consolas"])
        self.combo_text_font.currentTextChanged.connect(self.options_changed.emit)
        l.addWidget(self.combo_text_font)

        l.addWidget(QLabel("字号:", panel))
        self.spin_text_size = self._make_opt_spin(8, 72, 14, width=46 if self._opt_theme == "codex" else 44)
        l.addWidget(self.spin_text_size)

        self._add_divider(l)

        self.btn_text_bold = self._make_opt_btn("B", fixed_w=self._opt_text_bold_w, tip="粗体")
        self.btn_text_bold.setStyleSheet(self.btn_text_bold.styleSheet() + "font-weight: bold;")
        self.btn_text_bold.clicked.connect(self.options_changed.emit)
        l.addWidget(self.btn_text_bold)

        self.btn_text_italic = self._make_opt_btn("I", fixed_w=self._opt_text_bold_w, tip="斜体")
        self.btn_text_italic.setStyleSheet(self.btn_text_italic.styleSheet() + "font-style: italic;")
        self.btn_text_italic.clicked.connect(self.options_changed.emit)
        l.addWidget(self.btn_text_italic)

        self._add_divider(l)

        l.addWidget(QLabel("颜色:", panel))
        self.text_palette = self._make_opt_palette(default_color="#FF3B30")
        l.addWidget(self.text_palette)

        self._add_divider(l)

        self.btn_text_bg = self._make_opt_btn("背景框", tip="开启/关闭文字圆角背景框")
        self.btn_text_bg.clicked.connect(self.options_changed.emit)
        l.addWidget(self.btn_text_bg)

        self._register_panel(tool_name, panel, l)

    def get_text_options(self):
        return {
            'fontFamily': self.combo_text_font.currentText(),
            'fontSize': self.spin_text_size.value(),
            'bold': self.btn_text_bold.isChecked(),
            'italic': self.btn_text_italic.isChecked(),
            'color': self.text_palette.current_color,
            'bgEnabled': self.btn_text_bg.isChecked(),
            'bgColor': QColor(255, 255, 255, 230),
        }

    def sync_text_options_from_edit(self, edit):
        """选中已有文本图元时，将其字体/字号/样式/颜色/背景框状态反向同步到工具选项栏。"""
        if not edit:
            return
        font = _extract_edit_font(edit)
        family = edit.get('fontFamily') or font.family()
        size = int(edit.get('fontSize') or (font.pointSize() if font.pointSize() > 0 else 14))
        bold = bool(edit.get('bold', font.bold()))
        italic = bool(edit.get('italic', font.italic()))
        color = edit.get('color', self.text_palette.current_color)
        bg_enabled = bool(edit.get('bgEnabled', False))

        widgets = [
            self.combo_text_font, self.spin_text_size,
            self.btn_text_bold, self.btn_text_italic, self.btn_text_bg,
        ]
        prev_states = [w.blockSignals(True) for w in widgets]
        try:
            idx = self.combo_text_font.findText(family, Qt.MatchFixedString)
            if idx >= 0:
                self.combo_text_font.setCurrentIndex(idx)
            self.spin_text_size.setValue(max(self.spin_text_size.minimum(), min(self.spin_text_size.maximum(), size)))
            self.btn_text_bold.setChecked(bold)
            self.btn_text_italic.setChecked(italic)
            self.btn_text_bg.setChecked(bg_enabled)
            if isinstance(color, QColor) and color.isValid():
                self.text_palette.set_color(color, emit=False)
        finally:
            for w, prev in zip(widgets, prev_states):
                w.blockSignals(prev)

    def _init_rotate_panel(self):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        self.rotate_controls = RotateControlsWidget(theme=self._opt_rotate_theme, parent=panel)
        l.addWidget(self.rotate_controls)
        self._register_panel('ROTATE', panel, l)

    def set_tool(self, tool_name):
        self.current_tool_name = tool_name
        if tool_name in self.tool_indices:
            self.stack.setCurrentIndex(self.tool_indices[tool_name])
            self.show()
        else:
            self.hide()

    def has_active_tool(self):
        return self.current_tool_name in self.tool_indices


class ToolOptionsBar(_ToolOptionsPanelsMixin, FrostedBarWidget):
    options_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toolOptionsBar")
        self.setStyleSheet("""
            QWidget#toolOptionsBar {
                background: transparent;
            }
            QLabel {
                font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
                font-size: 11px;
                font-weight: 400;
                color: #1E293B;
                background: transparent;
            }
            QSpinBox {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.90), stop:1 rgba(248, 245, 239, 0.75));
                color: #1D1D1F;
                border: 1px solid rgba(142, 142, 147, 0.45);
                border-radius: 5px;
                padding: 1px 3px;
                font-size: 11px;
                height: 20px;
            }
            QSpinBox:focus {
                border: 1.5px solid #B6863A;
                background: #FFFFFF;
            }
            QComboBox {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.90), stop:1 rgba(248, 245, 239, 0.75));
                color: #1D1D1F;
                border: 1px solid rgba(142, 142, 147, 0.45);
                border-radius: 5px;
                padding: 1px 5px;
                font-size: 11px;
                height: 20px;
            }
            QComboBox:focus {
                border: 1.5px solid #B6863A;
                background: #FFFFFF;
            }
        """)

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(self.SHADOW_PAD + 6, self.SHADOW_PAD + 3, self.SHADOW_PAD + 6, self.SHADOW_PAD + 3)
        self.main_layout.setSpacing(5)

        self.stack = QStackedWidget(self)
        self.main_layout.addWidget(self.stack)

        self.tool_indices = {}
        self._init_rect_ellipse_panel()
        self._init_arrow_panel(tool_name='ARROW', none_label="关 ━", show_hint=False)
        self._init_pen_panel(tool_name='PEN', poly_label="折线 〰", hint_text="💡 拖拽画单条直线，点按画折线")
        self._init_marker_panel(tool_name='MARKER')
        self._init_mosaic_blur_panel(tool_name='MOSAIC_BLUR')
        self._init_text_panel(tool_name='TEXT')
        self._init_eraser_panel()
        self._init_rotate_panel()

        self.current_tool_name = None

    def _init_rect_ellipse_panel(self):
        refs = self._build_shape_panel('RECT_ELLIPSE', with_kind_toggle=True, with_radius=True)
        self._re_refs = refs
        self.btn_re_rect = refs['btn_rect']
        self.btn_re_ellipse = refs['btn_ellipse']
        self.btn_re_fill_none = refs['btn_none']
        self.btn_re_fill_stroke = refs['btn_stroke']
        self.btn_re_fill_solid = refs['btn_solid']
        self.spin_re_width = refs['spin_w']
        self.lbl_re_radius = refs['lbl_r']
        self.spin_re_radius = refs['spin_r']
        self.re_stroke_palette = refs['stroke_pal']
        self.re_fill_palette = refs['fill_pal']
        self._set_re_kind = refs['set_kind']
        self._set_re_fill = refs['set_fill']

    def get_rect_ellipse_options(self):
        return self._read_shape_options(self._re_refs, default_kind='rect')

    def _init_eraser_panel(self):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)
        lbl = QLabel("🧹 拖动鼠标经过已有标注即可整项擦除 (一次拖动记录为单步撤销)", panel)
        lbl.setStyleSheet("color: #57606A; font-size: 11px;")
        l.addWidget(lbl)
        self._register_panel('ERASER', panel, l)


class QuickTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor_parent = parent
        self.setFrameShape(QFrame.NoFrame)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self.document().setDocumentMargin(TEXT_BOX_DOC_MARGIN)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Escape:
            if self.editor_parent is not None:
                if not self.toPlainText().strip() and self.editor_parent.active_edit_ref is None:
                    self.editor_parent.cancel_edit()
                    event.accept()
                    return
                if self.editor_parent.editing_text:
                    self.editor_parent.set_editing_text(False)
                    event.accept()
                    return
                self.editor_parent.commit_edit()
                event.accept()
                return
        if (mods & Qt.ControlModifier) and key in (Qt.Key_Return, Qt.Key_Enter):
            if self.editor_parent is not None:
                self.editor_parent.commit_edit()
                event.accept()
                return
        if (mods & Qt.ControlModifier) and key == Qt.Key_Y:
            self.redo()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if self.editor_parent is not None:
            pv = getattr(self.editor_parent, 'parent_view', None)
            if pv is not None:
                pv.wheelEvent(event)
                return
        super().wheelEvent(event)


class InlineTextEditor(QWidget):
    """PowerPoint 风格内联文本框：支持边框拖动、八向控制柄自由缩放、固定宽度自动换行及双模态编辑。"""
    BORDER_INSET = TEXT_BOX_BORDER_INSET
    CONTENT_PAD = TEXT_BOX_CONTENT_PAD
    TEXT_INSET = TEXT_BOX_BORDER_INSET + TEXT_BOX_CONTENT_PAD
    DOC_MARGIN = TEXT_BOX_DOC_MARGIN
    HANDLE_SIZE = 7
    HANDLE_HIT_RADIUS = 7
    MIN_FRAME_W = 56
    MIN_FRAME_H = 28

    _HANDLE_CURSORS = {
        'tl': Qt.SizeFDiagCursor,
        'br': Qt.SizeFDiagCursor,
        'tr': Qt.SizeBDiagCursor,
        'bl': Qt.SizeBDiagCursor,
        't': Qt.SizeVerCursor,
        'b': Qt.SizeVerCursor,
        'l': Qt.SizeHorCursor,
        'r': Qt.SizeHorCursor,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_view = parent
        self.setWindowFlags(Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.current_color = QColor(255, 0, 0)
        self.bg_enabled = False
        self.bg_color = QColor(255, 255, 255, 230)
        self.active_edit_ref = None
        self.base_pos = QPoint(0, 0)
        self.user_width = None
        self.user_height = None
        self.editing_text = True

        self._drag_mode = None
        self._drag_start_global = QPoint()
        self._drag_start_frame = QRect()
        self._drag_start_font_size = 14
        self._drag_moved = False
        self._hover_handle = None

        self.text_edit = QuickTextEdit(self)
        self.text_edit.setPlaceholderText("输入文字...")
        self.text_edit.textChanged.connect(self.adjust_size_to_content)
        self.text_edit.viewport().installEventFilter(self)
        self.hide()

    def frame_rect(self):
        """返回控件局部坐标系下可见边框矩形。"""
        return QRect(
            self.BORDER_INSET,
            self.BORDER_INSET,
            max(1, self.width() - 2 * self.BORDER_INSET),
            max(1, self.height() - 2 * self.BORDER_INSET),
        )

    def _parent_bounds(self):
        pv = self.parent_view
        if pv is not None:
            sel = getattr(pv, 'selection_rect', None)
            if isinstance(sel, QRect) and not sel.isNull() and sel.width() >= self.MIN_FRAME_W and sel.height() >= self.MIN_FRAME_H:
                return QRect(sel)
            r = pv.rect()
            if r.isValid() and r.width() > 0 and r.height() > 0:
                return r
        return QRect(0, 0, 1920, 1080)

    def _handle_points(self):
        fr = self.frame_rect()
        cx = fr.x() + fr.width() // 2
        cy = fr.y() + fr.height() // 2
        rx = fr.x() + fr.width() - 1
        by = fr.y() + fr.height() - 1
        return {
            'tl': QPoint(fr.x(), fr.y()),
            'tr': QPoint(rx, fr.y()),
            'bl': QPoint(fr.x(), by),
            'br': QPoint(rx, by),
            't': QPoint(cx, fr.y()),
            'b': QPoint(cx, by),
            'l': QPoint(fr.x(), cy),
            'r': QPoint(rx, cy),
        }

    def _hit_test(self, local_pos):
        pts = self._handle_points()
        r_hit = self.HANDLE_HIT_RADIUS
        for key in ('tl', 'tr', 'bl', 'br', 't', 'b', 'l', 'r'):
            pt = pts[key]
            if abs(local_pos.x() - pt.x()) <= r_hit and abs(local_pos.y() - pt.y()) <= r_hit:
                return key

        fr = self.frame_rect()
        outer = fr.adjusted(-5, -5, 5, 5)
        inner = self.text_edit.geometry()
        if outer.contains(local_pos) and not inner.contains(local_pos):
            return 'move'
        if not self.editing_text and fr.contains(local_pos):
            return 'move'
        return None

    def _apply_hover_cursor(self, hit):
        if hit in self._HANDLE_CURSORS:
            cur = self._HANDLE_CURSORS[hit]
        elif hit == 'move':
            cur = Qt.SizeAllCursor
        elif self.editing_text:
            cur = Qt.IBeamCursor
        else:
            cur = Qt.SizeAllCursor
        self.setCursor(cur)
        self.text_edit.viewport().setCursor(cur if hit in self._HANDLE_CURSORS else Qt.IBeamCursor)

    def _begin_drag(self, mode, global_pos):
        self._drag_mode = mode
        self._drag_start_global = QPoint(global_pos)
        self._drag_start_frame = QRect(self.get_text_rect())
        ps = self.text_edit.font().pointSize()
        self._drag_start_font_size = ps if ps > 0 else 14
        self._drag_moved = False
        if mode == 'move' and self.editing_text:
            self.set_editing_text(False)

    def _sync_font_size_to_options(self, point_size):
        f = self.text_edit.font()
        f.setPointSize(point_size)
        self.text_edit.setFont(f)
        self.text_edit.document().setDefaultFont(f)
        pv = self.parent_view
        if pv is not None and hasattr(pv, 'options_bar') and hasattr(pv.options_bar, 'spin_text_size'):
            sb = pv.options_bar.spin_text_size
            prev = sb.blockSignals(True)
            try:
                sb.setValue(max(sb.minimum(), min(sb.maximum(), int(point_size))))
            finally:
                sb.blockSignals(prev)

    def _handle_drag_move(self, global_pos, modifiers=Qt.NoModifier):
        delta = global_pos - self._drag_start_global
        if delta.manhattanLength() >= 3:
            self._drag_moved = True

        mode = self._drag_mode
        if mode == 'move':
            self.move_frame_to(self._drag_start_frame.topLeft() + delta)
            return

        bounds = self._parent_bounds()
        b_left, b_top = bounds.x(), bounds.y()
        b_right = bounds.x() + bounds.width()
        b_bottom = bounds.y() + bounds.height()

        start = self._drag_start_frame
        sx, sy, sw, sh = start.x(), start.y(), start.width(), start.height()
        sr, sb = sx + sw, sy + sh

        if 'l' in mode:
            nx = max(b_left, min(sr - self.MIN_FRAME_W, sx + delta.x()))
            nw = sr - nx
        elif 'r' in mode:
            nx = sx
            nw = max(self.MIN_FRAME_W, min(b_right - sx, sw + delta.x()))
        else:
            nx, nw = sx, sw

        if (modifiers & Qt.ControlModifier) and mode in ('tl', 'tr', 'bl', 'br') and sh > 0:
            raw_nh = (sb - (sy + delta.y())) if 't' in mode else (sh + delta.y())
            scaled_pt = max(8, min(72, int(round(self._drag_start_font_size * max(self.MIN_FRAME_H, raw_nh) / float(sh)))))
            if scaled_pt != self.text_edit.font().pointSize():
                self._sync_font_size_to_options(scaled_pt)

        cw = max(16, nw - 2 * self.CONTENT_PAD)
        doc = _build_text_document(self.text_edit.toPlainText(), self.text_edit.font(), content_width=cw)
        min_h = max(self.MIN_FRAME_H, int(math.ceil(doc.size().height()) + 2 * self.CONTENT_PAD))

        if 't' in mode:
            ny = max(b_top, min(sb - min_h, sy + delta.y()))
            nh = sb - ny
        elif 'b' in mode:
            ny = sy
            nh = max(min_h, min(b_bottom - sy, sh + delta.y()))
        else:
            ny = sy
            nh = max(min_h, sh if self.user_height is not None else min_h)

        if 'l' in mode or 'r' in mode:
            self.user_width = nw
        if 't' in mode or 'b' in mode:
            self.user_height = nh

        self.base_pos = QPoint(nx, ny)
        self.adjust_size_to_content()

    def _finish_drag(self, global_pos):
        mode = self._drag_mode
        moved = self._drag_moved
        self._drag_mode = None
        self._drag_moved = False

        if mode == 'move':
            if moved:
                self.set_editing_text(False)
            else:
                local_pos = self.mapFromGlobal(global_pos)
                if self.text_edit.geometry().contains(local_pos):
                    self.set_editing_text(True, click_global_pos=global_pos)
                else:
                    self.set_editing_text(False)
        elif mode in self._HANDLE_CURSORS:
            if self.editing_text:
                self.text_edit.setFocus()
            else:
                self.setFocus()

    def move_frame_to(self, top_left):
        bounds = self._parent_bounds()
        fr = self.frame_rect()
        max_x = bounds.x() + max(0, bounds.width() - fr.width())
        max_y = bounds.y() + max(0, bounds.height() - fr.height())
        nx = max(bounds.x(), min(top_left.x(), max_x))
        ny = max(bounds.y(), min(top_left.y(), max_y))
        self.base_pos = QPoint(nx, ny)
        self.move(nx - self.BORDER_INSET, ny - self.BORDER_INSET)

    def set_editing_text(self, editing, click_global_pos=None):
        self.editing_text = bool(editing)
        if self.editing_text:
            self.text_edit.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.text_edit.setFocus()
            if click_global_pos is not None:
                vp_pos = self.text_edit.viewport().mapFromGlobal(click_global_pos)
                self.text_edit.setTextCursor(self.text_edit.cursorForPosition(vp_pos))
        else:
            cursor = self.text_edit.textCursor()
            cursor.clearSelection()
            self.text_edit.setTextCursor(cursor)
            self.text_edit.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setFocus()
        self._apply_hover_cursor(self._hover_handle)
        self.update()

    def update_font_from_options(self, options=None):
        if options is None and hasattr(self.parent_view, 'options_bar'):
            options = self.parent_view.options_bar.get_text_options()
        if options:
            self.current_color = QColor(options.get('color', QColor(255, 0, 0)))
            self.bg_enabled = bool(options.get('bgEnabled', False))
            self.bg_color = QColor(options.get('bgColor', QColor(255, 255, 255, 230)))
            font_size = int(options.get('fontSize', 14) or 14)
            f = QFont(options.get('fontFamily', 'Microsoft YaHei'), font_size)
            f.setBold(bool(options.get('bold', False)))
            f.setItalic(bool(options.get('italic', False)))
            f.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
            self.text_edit.setFont(f)
            self.text_edit.document().setDefaultFont(f)
            opt = QTextOption(Qt.AlignLeft | Qt.AlignTop)
            opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            self.text_edit.document().setDefaultTextOption(opt)
            self.text_edit.setStyleSheet(
                f"QTextEdit {{ background: transparent; border: none; padding: 0px; margin: 0px; color: {self.current_color.name()}; }}"
            )
            pal = self.text_edit.palette()
            pal.setColor(QPalette.Text, self.current_color)
            if hasattr(QPalette, 'PlaceholderText'):
                pal.setColor(QPalette.PlaceholderText, QColor(120, 128, 138, 175))
            self.text_edit.setPalette(pal)
        self.adjust_size_to_content()

    def adjust_size_to_content(self):
        text = self.text_edit.toPlainText()
        font = self.text_edit.font()
        bounds = self._parent_bounds()
        b_w = max(self.MIN_FRAME_W, bounds.width())
        b_h = max(self.MIN_FRAME_H, bounds.height())

        if self.user_width is not None:
            frame_w = min(b_w, max(self.MIN_FRAME_W, int(self.user_width)))
        else:
            doc_nowrap = _build_text_document(text, font, content_width=None)
            fm = QFontMetricsF(font)
            placeholder = self.text_edit.placeholderText() or "输入文字..."
            placeholder_w = fm.horizontalAdvance(placeholder) + 2 * self.DOC_MARGIN + 6
            ideal_cw = max(placeholder_w if not text else 20.0, math.ceil(doc_nowrap.idealWidth()) + 6)
            anchor_x = max(bounds.x(), min(self.base_pos.x(), bounds.x() + b_w - self.MIN_FRAME_W))
            avail_right_w = max(self.MIN_FRAME_W, (bounds.x() + b_w) - anchor_x)
            frame_w = min(avail_right_w, max(self.MIN_FRAME_W, int(math.ceil(ideal_cw + 2 * self.CONTENT_PAD))))

        cw = max(16, frame_w - 2 * self.CONTENT_PAD)
        doc_wrapped = _build_text_document(text, font, content_width=cw)
        doc_h = math.ceil(doc_wrapped.size().height())
        min_frame_h = max(self.MIN_FRAME_H, int(doc_h + 2 * self.CONTENT_PAD))

        if self.user_height is not None:
            frame_h = max(min_frame_h, int(self.user_height))
        else:
            frame_h = min_frame_h
        if b_h >= min_frame_h:
            frame_h = min(b_h, frame_h)

        max_x = bounds.x() + max(0, bounds.width() - frame_w)
        max_y = bounds.y() + max(0, bounds.height() - frame_h)
        frame_x = max(bounds.x(), min(self.base_pos.x(), max_x))
        frame_y = max(bounds.y(), min(self.base_pos.y(), max_y))

        widget_w = frame_w + 2 * self.BORDER_INSET
        widget_h = frame_h + 2 * self.BORDER_INSET
        self.setGeometry(frame_x - self.BORDER_INSET, frame_y - self.BORDER_INSET, widget_w, widget_h)
        self.text_edit.setGeometry(
            self.TEXT_INSET,
            self.TEXT_INSET,
            cw,
            max(12, frame_h - 2 * self.CONTENT_PAD),
        )
        self.text_edit.verticalScrollBar().setValue(0)
        self.text_edit.horizontalScrollBar().setValue(0)
        self.update()

    def start_editing(self, pos_or_rect, existing_edit=None, start_in_text_mode=True):
        self._drag_mode = None
        self._hover_handle = None
        self._drag_moved = False

        if isinstance(pos_or_rect, QRect):
            self.base_pos = pos_or_rect.topLeft()
        elif isinstance(pos_or_rect, QPoint):
            self.base_pos = QPoint(pos_or_rect)
        else:
            self.base_pos = QPoint(50, 50)

        self.active_edit_ref = existing_edit
        prev_block = self.text_edit.blockSignals(True)
        try:
            if existing_edit:
                self.user_width = existing_edit.get('user_width')
                self.user_height = existing_edit.get('user_height')
                self.text_edit.setPlainText(existing_edit.get('text', ''))
                font_obj = _extract_edit_font(existing_edit)
                if 'color' in existing_edit:
                    self.current_color = QColor(existing_edit['color'])
                opts = {
                    'fontFamily': existing_edit.get('fontFamily', font_obj.family()),
                    'fontSize': existing_edit.get('fontSize', font_obj.pointSize() if font_obj.pointSize() > 0 else 14),
                    'bold': existing_edit.get('bold', font_obj.bold()),
                    'italic': existing_edit.get('italic', font_obj.italic()),
                    'color': self.current_color,
                    'bgEnabled': existing_edit.get('bgEnabled', False),
                    'bgColor': existing_edit.get('bgColor', QColor(255, 255, 255, 230)),
                }
            else:
                if isinstance(pos_or_rect, QRect):
                    self.user_width = max(self.MIN_FRAME_W, pos_or_rect.width())
                    self.user_height = max(self.MIN_FRAME_H, pos_or_rect.height())
                else:
                    self.user_width = None
                    self.user_height = None
                self.text_edit.clear()
                if hasattr(self.parent_view, 'options_bar'):
                    opts = self.parent_view.options_bar.get_text_options()
                elif hasattr(self.parent_view, 'toolbar_ref'):
                    opts = {
                        'color': getattr(self.parent_view.toolbar_ref, 'current_color', QColor(255, 0, 0)),
                        'fontFamily': 'Microsoft YaHei',
                        'fontSize': 14,
                        'bold': False,
                        'italic': False,
                        'bgEnabled': False,
                        'bgColor': QColor(255, 255, 255, 230),
                    }
                else:
                    opts = {
                        'color': self.current_color,
                        'fontFamily': 'Microsoft YaHei',
                        'fontSize': 14,
                        'bold': False,
                        'italic': False,
                        'bgEnabled': False,
                        'bgColor': QColor(255, 255, 255, 230),
                    }
        finally:
            self.text_edit.blockSignals(prev_block)

        self.update_font_from_options(opts)
        self.show()
        self.raise_()
        if hasattr(self.parent_view, '_raise_floating_bars'):
            self.parent_view._raise_floating_bars()

        self.set_editing_text(start_in_text_mode)
        if start_in_text_mode and existing_edit:
            self.text_edit.moveCursor(QTextCursor.End)

    def get_text_rect(self):
        """返回父视图坐标系下的可见文本框矩形（不含外围控制柄留白）。"""
        return QRect(
            self.x() + self.BORDER_INSET,
            self.y() + self.BORDER_INSET,
            max(1, self.width() - 2 * self.BORDER_INSET),
            max(1, self.height() - 2 * self.BORDER_INSET),
        )

    def commit_edit(self):
        if hasattr(self.parent_view, 'commit_text_edit'):
            self.parent_view.commit_text_edit()

    def cancel_edit(self):
        self._drag_mode = None
        self._hover_handle = None
        self.text_edit.clear()
        self.active_edit_ref = None
        self.user_width = None
        self.user_height = None
        self.hide()
        if self.parent_view is not None and self.parent_view.isVisible():
            self.parent_view.setFocus()
            self.parent_view.update()

    def eventFilter(self, obj, event):
        if obj is self.text_edit.viewport():
            et = event.type()
            if et == QEvent.MouseMove:
                if self._drag_mode is not None:
                    self._handle_drag_move(event.globalPos(), event.modifiers())
                    return True
                if self.editing_text:
                    local_pos = self.mapFromGlobal(event.globalPos())
                    hit = self._hit_test(local_pos)
                    if hit in self._HANDLE_CURSORS:
                        if self._hover_handle != hit:
                            self._hover_handle = hit
                            self.update()
                        self._apply_hover_cursor(hit)
                        return True
                    if self._hover_handle is not None:
                        self._hover_handle = None
                        self._apply_hover_cursor(None)
                        self.update()
            elif et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and self.editing_text:
                local_pos = self.mapFromGlobal(event.globalPos())
                hit = self._hit_test(local_pos)
                if hit in self._HANDLE_CURSORS:
                    self._begin_drag(hit, event.globalPos())
                    return True
            elif et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._drag_mode is not None:
                self._finish_drag(event.globalPos())
                return True
            elif et == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton and self.editing_text:
                local_pos = self.mapFromGlobal(event.globalPos())
                hit = self._hit_test(local_pos)
                if hit in ('l', 'r'):
                    self.user_width = None
                    self.adjust_size_to_content()
                    return True
                if hit in ('t', 'b'):
                    self.user_height = None
                    self.adjust_size_to_content()
                    return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            if event.button() == Qt.RightButton:
                self.commit_edit()
                event.accept()
                return
            super().mousePressEvent(event)
            return
        hit = self._hit_test(event.pos())
        if hit is not None:
            self._begin_drag(hit, event.globalPos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_mode is not None:
            self._handle_drag_move(event.globalPos(), event.modifiers())
            event.accept()
            return
        hit = self._hit_test(event.pos())
        if self._hover_handle != hit:
            self._hover_handle = hit
            self.update()
        self._apply_hover_cursor(hit)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_mode is not None:
            self._finish_drag(event.globalPos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            hit = self._hit_test(event.pos())
            if hit in ('l', 'r'):
                self.user_width = None
                self.adjust_size_to_content()
                event.accept()
                return
            if hit in ('t', 'b'):
                self.user_height = None
                self.adjust_size_to_content()
                event.accept()
                return
            if not self.editing_text and self.frame_rect().contains(event.pos()):
                self.set_editing_text(True, click_global_pos=event.globalPos())
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        if self._hover_handle is not None:
            self._hover_handle = None
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if not self.editing_text:
            key = event.key()
            mods = event.modifiers()
            if key == Qt.Key_Escape:
                self.commit_edit()
                event.accept()
                return
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.set_editing_text(True)
                event.accept()
                return
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                self.cancel_edit()
                event.accept()
                return
            if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and not (mods & (Qt.ControlModifier | Qt.AltModifier)):
                step = 10 if (mods & Qt.ShiftModifier) else 1
                dx = -step if key == Qt.Key_Left else (step if key == Qt.Key_Right else 0)
                dy = -step if key == Qt.Key_Up else (step if key == Qt.Key_Down else 0)
                cur = self.get_text_rect().topLeft()
                self.move_frame_to(QPoint(cur.x() + dx, cur.y() + dy))
                event.accept()
                return
            txt = event.text()
            if txt and not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) and txt.isprintable():
                self.set_editing_text(True)
                self.text_edit.moveCursor(QTextCursor.End)
                self.text_edit.insertPlainText(txt)
                event.accept()
                return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        fr = QRectF(self.frame_rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        if self.bg_enabled and self.bg_color.isValid():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(self.bg_color))
            painter.drawRoundedRect(fr, 4.0, 4.0)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 55))
            painter.drawRoundedRect(fr, 4.0, 4.0)

        # 双层对比边框：外层浅色衬线 + 内层 PPT 虚线(输入态)/实线(选中态)
        line_style = Qt.DashLine if self.editing_text else Qt.SolidLine
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 215), 2.2, line_style))
        painter.drawRect(fr)
        painter.setPen(QPen(QColor(200, 144, 48), 1.4, line_style))
        painter.drawRect(fr)

        # 八向 PPT 缩放控制柄
        hs = self.HANDLE_SIZE
        half = hs / 2.0
        pts = self._handle_points()
        for key, pt in pts.items():
            is_active = (key == self._drag_mode or key == self._hover_handle)
            hr = QRectF(pt.x() - half, pt.y() - half, hs, hs)
            painter.setBrush(QBrush(QColor(200, 144, 48) if is_active else QColor(255, 255, 255)))
            painter.setPen(QPen(QColor(255, 255, 255) if is_active else QColor(200, 144, 48), 1.3))
            painter.drawRect(hr)



class _ClickThroughUnlockHandle(QWidget):
    """鼠标穿透模式下悬浮在贴图左上角的小型可点击解锁胶囊（仅该胶囊拦截鼠标，图片其余区域 100% 穿透）"""
    def __init__(self, pin_win):
        super().__init__(None)
        self._pin_win = pin_win
        self._hovered = False
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip("点击退出鼠标穿透模式（或按全局快捷键 Ctrl+Alt+T）")
        self.resize(96, 24)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.RightButton):
            event.accept()
            if self._pin_win is not None:
                self._pin_win.set_click_through(False)
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(r, 7, 7)
        bg = QColor(138, 94, 28, 242) if self._hovered else QColor(168, 118, 40, 232)
        painter.fillPath(path, QBrush(bg))
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
        painter.drawPath(path)
        painter.setFont(make_ui_font(8, QFont.Bold))
        painter.setPen(QColor(255, 255, 255))
        label = "🔓 取消穿透" if self._hovered else "👻 穿透中 ×"
        painter.drawText(r, Qt.AlignCenter, label)


class PinnedWindow(QWidget):
    def __init__(self, pixmap, main_app_ref=None, initial_pos=None, zoom_factor=1.0, ocr_doc=None):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.pixmap = pixmap
        self.main_app_ref = main_app_ref
        self._ocr_doc = ocr_doc
        self._ocr_loading = False
        self._pin_ocr_worker = None
        self._text_select_mode = False
        self._text_dragging = False
        self._text_sel_start = QPoint()
        self._text_sel_start_index = None
        self._text_sel_column_rect = QRect()
        self._text_selected = set()
        self._text_hover = None

        # 屏幕可用区域
        screen = None
        if initial_pos is not None:
            screen = QApplication.screenAt(initial_pos)
        if not screen:
            screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        # 智能自适应大图/超长滚动截图：
        # 如果是初次贴图（默认 zoom_factor==1.0），且图片高度或宽度超过屏幕可用区域的 85%，
        # 自动计算缩放比例，使其优雅适配屏幕，避免超大窗口撑爆 DWM 或飞出屏幕外
        max_init_w = avail.width() * 0.85
        max_init_h = avail.height() * 0.85
        if zoom_factor == 1.0 and (self.pixmap.width() > max_init_w or self.pixmap.height() > max_init_h):
            auto_zoom = min(max_init_w / max(1, self.pixmap.width()), max_init_h / max(1, self.pixmap.height()))
            self.zoom_factor = max(0.05, round(auto_zoom, 3))
        else:
            self.zoom_factor = float(zoom_factor) if zoom_factor > 0 else 1.0

        # 限制单边最大物理像素，防止超过系统 DWM / GPU 纹理限制（8192px）导致崩溃
        MAX_PIN_DIM = 8192
        target_w = min(MAX_PIN_DIM, max(20, int(self.pixmap.width() * self.zoom_factor)))
        target_h = min(MAX_PIN_DIM, max(20, int(self.pixmap.height() * self.zoom_factor)))
        self.resize(target_w, target_h)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.drag_position = QPoint()
        self._press_global_pos = QPoint()
        self._left_dragging = False
        self._file_drag_armed = False
        self._suppress_next_context_menu = False
        self._expand_btn_pressed = False
        self._hover_expand_btn = False
        self._opacity = 1.0
        self._click_through = False
        self._ct_handle = None
        self._is_collapsed = False
        self._expanded_size = None
        self._expanded_zoom = self.zoom_factor
        self._hud_text = ""
        self._hud_timer = QTimer(self)
        self._hud_timer.setSingleShot(True)
        self._hud_timer.timeout.connect(self._clear_hud)

        if initial_pos is not None:
            x = initial_pos.x()
            y = initial_pos.y()
        else:
            cur = QCursor.pos()
            x = cur.x()
            y = cur.y()

        if x + target_w > avail.right():
            x = max(avail.left(), avail.right() - target_w)
        if y + target_h > avail.bottom():
            y = max(avail.top(), avail.bottom() - target_h)
        if x < avail.left():
            x = avail.left()
        if y < avail.top():
            y = avail.top()

        self.move(x, y)
        self.show()

    def _clear_hud(self):
        self._hud_text = ""
        self.update()

    def _show_hud(self, text, ms=900):
        self._hud_text = str(text or "")
        if self._hud_text:
            self._hud_timer.start(ms)
        else:
            self._hud_timer.stop()
        self.update()

    # ---------- 贴图内直接划词取字 ----------
    def _text_doc_ready(self):
        return (self._ocr_doc is not None and not self._ocr_doc.is_empty()
                and not getattr(self, '_is_collapsed', False)
                and not getattr(self, '_is_mini_for_drawer', False))

    def _text_geometry(self):
        doc = self._ocr_doc
        iw = max(1, doc.image_size.width()) if doc else max(1, self.pixmap.width())
        ih = max(1, doc.image_size.height()) if doc else max(1, self.pixmap.height())
        return (self.width() / iw, self.height() / ih)

    def _text_widget_rect(self, char_rect):
        sx, sy = self._text_geometry()
        return QRect(int(char_rect.left() * sx), int(char_rect.top() * sy),
                     max(1, int(char_rect.width() * sx)), max(1, int(char_rect.height() * sy)))

    def _text_char_at(self, pos):
        if not self._text_doc_ready():
            return None
        sx, sy = self._text_geometry()
        if sx <= 0 or sy <= 0:
            return None
        return ocr_doc_char_at(self._ocr_doc, pos.x() / sx, pos.y() / sy)

    def _text_index_at(self, pos, row_indices=None):
        if not self._text_doc_ready():
            return None
        sx, sy = self._text_geometry()
        if sx <= 0 or sy <= 0:
            return None
        return ocr_doc_index_at(self._ocr_doc, pos.x() / sx, pos.y() / sy, row_indices)

    def _selected_text(self):
        return ocr_doc_selected_text(self._ocr_doc, self._text_selected)

    def _clear_text_selection(self):
        self._text_dragging = False
        self._text_sel_start_index = None
        self._text_sel_column_rect = QRect()
        self._text_selected = set()
        self._text_hover = None
        self.update()

    def _copy_selected_text(self):
        text = self._selected_text()
        if not text:
            return False
        QApplication.clipboard().setText(text)
        self._clear_text_selection()
        self._show_hud(f"✓ 已复制 {len(text)} 字", ms=1600)
        return True

    def _copy_selected_as_table(self):
        text = self._selected_text()
        if not text:
            return False
        sub_doc = ocr_doc_selected_subdoc(self._ocr_doc, self._text_selected)
        tsv = extract_table_tsv(sub_doc, text)
        if not tsv.strip():
            return False
        QApplication.clipboard().setText(tsv)
        self._clear_text_selection()
        row_cnt = len([ln for ln in tsv.splitlines() if ln.strip()])
        self._show_hud(f"✓ 已复制表格 ({row_cnt} 行)", ms=1600)
        return True

    def _translate_selected_text(self):
        text = self._selected_text()
        if not text:
            return
        sub_doc = ocr_doc_selected_subdoc(self._ocr_doc, self._text_selected)
        dlg = OcrResultDialog(text, None, ocr_doc=sub_doc)
        dlg.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self._temp_ocr_dialog = dlg
        exec_dialog_nonmodal(dlg)

    def _select_all_text(self):
        if not self._text_doc_ready():
            return
        self._text_selected = set(ocr_doc_flat(self._ocr_doc))
        self.update()

    def _ensure_ocr_doc(self, notify=False):
        if self._ocr_doc is not None or self._ocr_loading:
            return
        if not self.pixmap or self.pixmap.isNull():
            return
        self._ocr_loading = True
        if notify:
            self._show_hud("正在识别文字…", ms=4000)
        worker = OcrWorker(self.pixmap.toImage(), use_ai=False, fast=True)
        self._pin_ocr_worker = worker

        def _on_doc(doc):
            self._ocr_loading = False
            self._ocr_doc = doc
            if self._pin_ocr_worker is worker:
                self._pin_ocr_worker = None
            if self._text_doc_ready():
                pos = self.mapFromGlobal(QCursor.pos())
                if self.rect().contains(pos) and (self._text_select_mode or (QApplication.keyboardModifiers() & Qt.ShiftModifier)):
                    hover = self._text_char_at(pos)
                    if hover is not None:
                        self._text_hover = hover
                        self.setCursor(Qt.IBeamCursor)
            if self._text_select_mode:
                self._show_hud("划词取字已开启 · 拖选复制", ms=1500)
            elif notify:
                self._clear_hud()
            self.update()

        def _on_done(_txt):
            if self._ocr_loading:
                self._ocr_loading = False
                if self._pin_ocr_worker is worker:
                    self._pin_ocr_worker = None
                if self._ocr_doc is None and notify:
                    self._show_hud("未识别到文字", ms=1400)

        worker.doc_ready.connect(_on_doc)
        worker.finished.connect(_on_done)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def toggle_text_select_mode(self, enable=None):
        target = (not self._text_select_mode) if enable is None else bool(enable)
        self._text_select_mode = target
        if target:
            if getattr(self, '_is_collapsed', False):
                self.toggle_collapse()
            if self._ocr_doc is not None and not self._ocr_doc.is_empty():
                self._show_hud("划词取字已开启 · 拖选复制 (Esc退出)", ms=1800)
                pos = self.mapFromGlobal(QCursor.pos())
                if self.rect().contains(pos):
                    self._text_hover = self._text_char_at(pos)
                    self.setCursor(Qt.IBeamCursor if self._text_hover is not None else Qt.ArrowCursor)
            else:
                self._ensure_ocr_doc(notify=True)
        else:
            self._clear_text_selection()
            self.setCursor(Qt.ArrowCursor)
            self._show_hud("已退出划词取字", ms=1100)
        self.update()

    def _sync_click_through_handle(self):
        handle = getattr(self, "_ct_handle", None)
        should_show = (
            bool(getattr(self, "_click_through", False))
            and self.isVisible()
            and not getattr(self, "_is_mini_for_drawer", False)
        )
        if not should_show:
            if handle is not None:
                try:
                    handle.hide()
                except RuntimeError:
                    pass
            return
        if handle is None:
            handle = _ClickThroughUnlockHandle(self)
            self._ct_handle = handle
        if self.height() >= 36 and self.width() >= 106:
            hx, hy = self.x() + 6, self.y() + 6
        else:
            hx, hy = self.x(), max(0, self.y() - handle.height() - 4)
        handle.move(hx, hy)
        if not handle.isVisible():
            handle.show()
        handle.raise_()

    def _collapsed_expand_rect(self):
        return QRect(max(36, self.width() - 50), 6, 42, max(16, self.height() - 12))

    def set_click_through(self, enabled: bool):
        """开启/关闭鼠标穿透模式（点击穿过贴图直达底层窗口），可点击左上角解锁胶囊、按 Ctrl+Alt+T 或托盘菜单随时恢复"""
        self._click_through = bool(enabled)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._click_through)
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if self._click_through:
                ex |= WS_EX_TRANSPARENT
            else:
                ex &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
            )
        except Exception as e:
            logging.warning("切换鼠标穿透状态失败: %s", e)
        self.show()
        self.update()
        self._sync_click_through_handle()
        if self._click_through:
            try:
                self._ct_toast = PinToast("👻 已开启鼠标穿透 · 点击左上角「穿透中 ×」或按 Ctrl+Alt+T 解除", pinned=True)
                self._ct_toast.show()
            except Exception:
                pass
        else:
            if getattr(self, "_ct_handle", None) is not None:
                try:
                    self._ct_handle.close()
                except RuntimeError:
                    pass
                self._ct_handle = None
            self._show_hud("已恢复鼠标交互")

    def toggle_collapse(self):
        """将桌面贴图折叠为紧凑迷你标签条或还原展开"""
        if not self._is_collapsed:
            self._expanded_size = self.size()
            self._expanded_zoom = getattr(self, "zoom_factor", 1.0)
            self._is_collapsed = True
            self._hover_expand_btn = False
            self.resize(176, 36)
        else:
            self._is_collapsed = False
            self._hover_expand_btn = False
            self.setCursor(Qt.ArrowCursor)
            self.zoom_factor = self._expanded_zoom or getattr(self, "zoom_factor", 1.0)
            if self._expanded_size is not None and self._expanded_size.isValid():
                self.resize(self._expanded_size)
            else:
                self.resize(max(20, int(self.pixmap.width() * self.zoom_factor)),
                            max(20, int(self.pixmap.height() * self.zoom_factor)))
        self.update()
        self._sync_click_through_handle()

    def reset_transform(self):
        """恢复贴图 1:1 原始大小与 100% 不透明度"""
        self._is_collapsed = False
        self._expanded_size = None
        self.zoom_factor = 1.0
        self._opacity = 1.0
        self.setWindowOpacity(1.0)
        if self.pixmap and not self.pixmap.isNull():
            self.resize(max(20, self.pixmap.width()), max(20, self.pixmap.height()))
        self._show_hud("1:1 原始大小")

    def copy_as_file(self):
        if copy_pixmap_as_clipboard_file(self.pixmap):
            self._show_hud("📄 已复制为 PNG 文件")

    def save_as(self):
        if not self.pixmap or self.pixmap.isNull():
            return
        fn, _ = QFileDialog.getSaveFileName(
            self, "另存贴图", f"pin_{time.strftime('%Y%m%d_%H%M%S')}.png",
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;BMP (*.bmp)"
        )
        if fn:
            self.pixmap.save(fn)
            self._show_hud("已保存文件")

    def silent_ocr_copy(self):
        """静默识别贴图文字（若无文字则尝试识别二维码/条码）并直接复制进剪贴板，不打断操作"""
        try:
            QToolTip.showText(QCursor.pos(), "⚡ 正在静默识别文字...", None, QRect(), 2500)
            ocr_img = self.pixmap.toImage()
            worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))

            def _on_done(text):
                QToolTip.hideText()
                codes = getattr(worker, "detected_codes", [])
                clean = (text or "").strip()
                if clean:
                    QApplication.clipboard().setText(clean)
                    self._show_hud(f"⚡ 已复制 {len(clean)} 字")
                elif codes:
                    first_code = codes[0]["text"]
                    QApplication.clipboard().setText(first_code)
                    self._show_hud(f"🔲 已复制 {codes[0]['type']}")
                else:
                    self._show_hud("未识别到文字或条码")

            worker.finished.connect(_on_done)
            worker.start()
            self._silent_ocr_worker = worker
        except Exception as e:
            logging.warning("静默识别异常: %s", e)

    def scan_codes(self):
        """专门扫描贴图中的二维码与一维条码"""
        codes = detect_codes_in_image(self.pixmap)
        if not codes:
            QMessageBox.information(None, "二维码 / 条码识别", "未在当前贴图中检测到可解码的二维码或条形码。")
            return
        QApplication.clipboard().setText(codes[0]["text"])
        dlg = OcrResultDialog("", None, detected_codes=codes)
        dlg.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self._temp_ocr_dialog = dlg
        exec_dialog_nonmodal(dlg)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            if getattr(self, '_is_collapsed', False):
                self.toggle_collapse()
                event.accept()
                return
            if (self._text_select_mode or (event.modifiers() & Qt.ShiftModifier)) and self._text_doc_ready():
                hit = self._text_char_at(event.pos())
                if hit is not None:
                    ri = hit[0]
                    self._text_selected = {(ri, ci) for ci in range(len(self._ocr_doc.rows[ri].text))}
                    self._copy_selected_text()
                    event.accept()
                    return
            if event.modifiers() & Qt.AltModifier:
                self.toggle_collapse()
                event.accept()
                return
            self.close()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def closeEvent(self, event):
        if getattr(self, "_ct_handle", None) is not None:
            try:
                self._ct_handle.close()
            except Exception:
                pass
            self._ct_handle = None
        if getattr(self, '_dropped_to_drawer', False):
            if self.main_app_ref is not None and self in self.main_app_ref.pinned_windows:
                self.main_app_ref.pinned_windows.remove(self)
            super().closeEvent(event)
            return
        if self.main_app_ref is not None:
            try:
                self.main_app_ref.on_pin_closed(self)
            except Exception as e:
                logging.error("关闭贴图并同步历史失败: %s", e, exc_info=True)
        super().closeEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_click_through_handle()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_click_through_handle()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_click_through_handle()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_click_through_handle()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 拖向储藏柜时自动呈现 macOS 冰晶毛玻璃圆角迷你卡片（四周 100% 透明无直角边，绝无任何溢出）
        if getattr(self, '_is_mini_for_drawer', False):
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(self.rect(), Qt.transparent)
            painter.restore()

            for i, alpha in enumerate([6, 12, 20]):
                sr = QRectF(self.rect()).adjusted(2 + i, 2 + i, -2 - i, -2 - i)
                sp = QPainterPath()
                sp.addRoundedRect(sr, 14 - i, 14 - i)
                painter.fillPath(sp, QBrush(QColor(15, 23, 42, alpha)))

            r = QRectF(self.rect()).adjusted(5, 5, -5, -5)
            path = QPainterPath()
            path.addRoundedRect(r, 12, 12)
            painter.fillPath(path, QBrush(QColor(250, 248, 243, 242)))
            painter.setPen(QPen(QColor(182, 134, 58, 230), 2.0))
            painter.drawPath(path)

            card_inner = r.adjusted(6, 6, -6, -6)
            scaled = self.pixmap.scaled(int(card_inner.width()), int(card_inner.height()), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            tw, th = scaled.width(), scaled.height()
            tx = card_inner.x() + (card_inner.width() - tw) / 2.0
            ty = card_inner.y() + (card_inner.height() - th) / 2.0
            img_rect = QRectF(tx, ty, tw, th)

            painter.save()
            clip_p = QPainterPath()
            clip_p.addRoundedRect(img_rect, 6, 6)
            painter.setClipPath(clip_p)
            painter.drawPixmap(int(tx), int(ty), scaled)
            painter.restore()

            painter.setPen(QPen(QColor(15, 23, 42, 35), 1.0))
            painter.drawRoundedRect(img_rect, 6, 6)
        elif getattr(self, '_is_collapsed', False):
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(self.rect(), Qt.transparent)
            painter.restore()

            pill = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            p_path = QPainterPath()
            p_path.addRoundedRect(pill, 10, 10)
            painter.fillPath(p_path, QBrush(QColor(255, 255, 255, 242)))
            painter.setPen(QPen(QColor(24, 24, 27, 45), 1.2))
            painter.drawPath(p_path)

            thumb = self.pixmap.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            tx = pill.left() + 7 + (22 - thumb.width()) / 2.0
            ty = pill.top() + (pill.height() - thumb.height()) / 2.0
            painter.drawPixmap(int(tx), int(ty), thumb)

            painter.setFont(make_ui_font(9, QFont.DemiBold))
            painter.setPen(QColor(24, 24, 27))
            res_txt = f"{self.pixmap.width()}×{self.pixmap.height()}"
            painter.drawText(QRectF(pill.left() + 34, pill.top(), pill.width() - 86, pill.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, res_txt)

            exp_rect = QRectF(self._collapsed_expand_rect())
            is_hover = bool(getattr(self, "_hover_expand_btn", False))
            is_pressed = bool(getattr(self, "_expand_btn_pressed", False))
            exp_path = QPainterPath()
            exp_path.addRoundedRect(exp_rect, 6, 6)
            if is_pressed:
                exp_fill = QColor(182, 134, 58, 62)
                exp_border = QColor(182, 134, 58, 165)
                exp_fg = QColor(122, 81, 16)
            elif is_hover:
                exp_fill = QColor(182, 134, 58, 36)
                exp_border = QColor(182, 134, 58, 125)
                exp_fg = QColor(122, 81, 16)
            else:
                exp_fill = QColor(244, 244, 245, 230)
                exp_border = QColor(212, 212, 216, 200)
                exp_fg = QColor(63, 63, 70)
            painter.fillPath(exp_path, QBrush(exp_fill))
            painter.setPen(QPen(exp_border, 1.0))
            painter.drawPath(exp_path)
            painter.setFont(make_ui_font(8, QFont.DemiBold))
            painter.setPen(exp_fg)
            painter.drawText(exp_rect, Qt.AlignCenter, "展开")
        else:
            painter.drawPixmap(self.rect(), self.pixmap)
            if self._text_doc_ready():
                ocr_doc_draw_selection(painter, self._ocr_doc, self._text_selected, self._text_hover, self._text_widget_rect)
            border_enabled = (
                bool(getattr(self.main_app_ref, "pin_border_enabled", False))
                if self.main_app_ref is not None
                else bool(load_settings().get("pin_border_enabled", False))
            )
            if border_enabled and not getattr(self, '_click_through', False):
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(24, 24, 27, 175), 1.0))
                painter.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
                if self.width() >= 8 and self.height() >= 8:
                    painter.setPen(QPen(QColor(182, 134, 58, 225), 1.2))
                    painter.drawRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))
            if getattr(self, '_click_through', False):
                painter.setPen(QPen(QColor(182, 134, 58, 215), 1.6, Qt.DashLine))
                painter.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
                handle = getattr(self, '_ct_handle', None)
                if not (handle is not None and handle.isVisible()):
                    badge_r = QRectF(6, 6, 46, 20)
                    bp = QPainterPath()
                    bp.addRoundedRect(badge_r, 5, 5)
                    painter.fillPath(bp, QBrush(QColor(168, 118, 40, 224)))
                    painter.setFont(make_ui_font(8, QFont.Bold))
                    painter.setPen(QColor(255, 255, 255))
                    painter.drawText(badge_r, Qt.AlignCenter, "穿透中")

            hud = getattr(self, '_hud_text', '')
            if hud and self.width() >= 90 and self.height() >= 34:
                painter.setFont(make_ui_font(9, QFont.DemiBold))
                fm = painter.fontMetrics()
                bw = min(self.width() - 16, fm.horizontalAdvance(hud) + 18)
                bh = 24
                bx = (self.width() - bw) / 2.0
                hud_r = QRectF(bx, 8, bw, bh)
                hp = QPainterPath()
                hp.addRoundedRect(hud_r, 7, 7)
                painter.fillPath(hp, QBrush(QColor(15, 23, 42, 208)))
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(hud_r, Qt.AlignCenter, hud)

    def wheelEvent(self, event: QWheelEvent):
        if getattr(self, '_is_mini_for_drawer', False):
            event.accept()
            return
        if event.modifiers() & Qt.ControlModifier:
            delta_y = event.angleDelta().y()
            cur_op = getattr(self, '_opacity', 1.0)
            step = 0.05 if cur_op < 0.45 else 0.10
            if delta_y > 0:
                new_op = min(1.0, cur_op + step)
            else:
                new_op = max(0.15, cur_op - step)
            self._opacity = round(new_op, 2)
            self.setWindowOpacity(self._opacity)
            self._show_hud(f"透明度 {int(round(self._opacity * 100))}%")
            event.accept()
            return
        if getattr(self, '_is_collapsed', False):
            event.accept()
            return
        step = 0.05 if self.zoom_factor < 0.5 else 0.1
        if event.angleDelta().y() > 0:
            new_zoom = min(10.0, self.zoom_factor + step)
        else:
            new_zoom = max(0.05, self.zoom_factor - step)

        MAX_PIN_DIM = 8192
        target_w = max(20, int(self.pixmap.width() * new_zoom))
        target_h = max(20, int(self.pixmap.height() * new_zoom))
        if target_w > MAX_PIN_DIM or target_h > MAX_PIN_DIM:
            event.accept()
            return

        self.zoom_factor = round(new_zoom, 3)
        if self.width() != target_w or self.height() != target_h:
            self.resize(target_w, target_h)
        self._show_hud(f"{int(round(self.zoom_factor * 100))}%")
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setFocus()
            self._press_global_pos = event.globalPos()
            if getattr(self, '_is_collapsed', False) and self._collapsed_expand_rect().contains(event.pos()):
                self._expand_btn_pressed = True
                self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
                self._normal_size = self.size()
                self._normal_zoom = getattr(self, 'zoom_factor', 1.0)
                self._is_mini_for_drawer = False
                self._left_dragging = False
                self._file_drag_armed = False
                self._suppress_next_context_menu = False
                self.update()
                event.accept()
                return
            if (not getattr(self, '_is_collapsed', False)
                    and not (event.modifiers() & Qt.AltModifier)
                    and (self._text_select_mode or (event.modifiers() & Qt.ShiftModifier))):
                if not self._text_doc_ready():
                    self._ensure_ocr_doc(notify=True)
                    event.accept()
                    return
                sx, sy = self._text_geometry()
                self._left_dragging = False
                self._text_dragging = True
                self._text_sel_start = event.pos()
                self._text_sel_start_index = self._text_index_at(event.pos())
                self._text_sel_column_rect = (
                    QRect(self._ocr_doc.rows[self._text_sel_start_index[0]].rect)
                    if self._text_sel_start_index is not None else QRect()
                )
                self._text_selected = set()
                self._text_hover = None
                self.setCursor(Qt.IBeamCursor)
                self.update()
                event.accept()
                return
            self._expand_btn_pressed = False
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            self._normal_size = self.size()
            self._normal_zoom = getattr(self, 'zoom_factor', 1.0)
            self._is_mini_for_drawer = False
            self._left_dragging = True
            self._file_drag_armed = False
            self._suppress_next_context_menu = False
        elif event.button() == Qt.RightButton:
            if self._left_dragging and (event.buttons() & Qt.LeftButton):
                self._file_drag_armed = True
                self._suppress_next_context_menu = True
                self.update()
                event.accept()
                return
            self._suppress_next_context_menu = False

    def leaveEvent(self, event):
        if getattr(self, '_text_hover', None) is not None:
            self._text_hover = None
            self.update()
        if getattr(self, '_hover_expand_btn', False):
            self._hover_expand_btn = False
            self.setCursor(Qt.ArrowCursor)
            self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.NoButton:
            if getattr(self, '_is_collapsed', False):
                hovered = self._collapsed_expand_rect().contains(event.pos())
                if hovered != getattr(self, '_hover_expand_btn', False):
                    self._hover_expand_btn = hovered
                    self.setCursor(Qt.PointingHandCursor if hovered else Qt.ArrowCursor)
                    self.update()
                return
            elif getattr(self, '_hover_expand_btn', False):
                self._hover_expand_btn = False
                self.setCursor(Qt.ArrowCursor)
                self.update()
            if self._text_select_mode or (event.modifiers() & Qt.ShiftModifier):
                if self._ocr_doc is None and not self._ocr_loading:
                    self._ensure_ocr_doc(notify=False)
                if self._text_doc_ready():
                    hover = self._text_char_at(event.pos())
                    if hover != self._text_hover:
                        self._text_hover = hover
                        self.update()
                    self.setCursor(Qt.IBeamCursor if (hover is not None or self._text_select_mode) else Qt.ArrowCursor)
                    return
            elif self._text_hover is not None:
                self._text_hover = None
                self.setCursor(Qt.ArrowCursor)
                self.update()
            return

        if event.buttons() == Qt.LeftButton and self._text_dragging:
            if self._text_doc_ready() and self._text_sel_start_index is not None:
                sx, sy = self._text_geometry()
                tlx = event.pos().x() / sx if sx > 0 else None
                tly = event.pos().y() / sy if sy > 0 else None
                row_indices = ocr_doc_column_rows(self._ocr_doc, self._text_sel_column_rect, tlx, tly)
                end_idx = self._text_index_at(event.pos(), row_indices)
                if end_idx is not None:
                    flat = ocr_doc_flat(self._ocr_doc, row_indices)
                    try:
                        a = flat.index(self._text_sel_start_index)
                        b = flat.index(end_idx)
                        lo, hi = (a, b) if a <= b else (b, a)
                        self._text_selected = set(flat[lo:hi + 1])
                        self.update()
                    except ValueError:
                        pass
            event.accept()
            return

        if event.buttons() == Qt.LeftButton:
            gpos = event.globalPos()
            if getattr(self, '_expand_btn_pressed', False):
                if (gpos - getattr(self, '_press_global_pos', gpos)).manhattanLength() < QApplication.startDragDistance():
                    return
                self._expand_btn_pressed = False
                self._left_dragging = True
                self.update()

            is_near_drawer = False
            drawer = None
            if self.main_app_ref and hasattr(self.main_app_ref, "pin_drawer") and self.main_app_ref.pin_drawer:
                drawer = self.main_app_ref.pin_drawer
                is_near_drawer = drawer.check_proximity(gpos)

            # 靠近或移入抽屉区域时：瞬间智能缩小为 100x100 迷你卡片，清晰看到插入位
            if is_near_drawer:
                if not getattr(self, '_is_mini_for_drawer', False):
                    self._is_mini_for_drawer = True
                    self._normal_size = self.size()
                    self._normal_zoom = getattr(self, 'zoom_factor', 1.0)
                    self.resize(100, 100)
                    self.drag_position = QPoint(50, 50)
                    self.update()
            else:
                # 拖离抽屉重返桌面时：即刻恢复原图原始尺寸
                if getattr(self, '_is_mini_for_drawer', False):
                    self._is_mini_for_drawer = False
                    self.resize(self._normal_size)
                    self.zoom_factor = self._normal_zoom
                    self.drag_position = QPoint(self._normal_size.width() // 2, self._normal_size.height() // 2)
                    self.update()

            self.move(gpos - self.drag_position)
            if drawer is not None:
                drawer.on_external_pin_drag(self, gpos)
                if is_near_drawer:
                    drawer._keep_dragged_windows_on_top(extra_win=self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            if getattr(self, '_file_drag_armed', False):
                self._file_drag_armed = False
                self._suppress_next_context_menu = True
                self.update()
                if getattr(self, '_left_dragging', False) and (event.buttons() & Qt.LeftButton):
                    QTimer.singleShot(0, self._trigger_os_file_drag)
                event.accept()
                return

        if event.button() == Qt.LeftButton and getattr(self, '_text_dragging', False):
            self._text_dragging = False
            moved = (event.pos() - self._text_sel_start).manhattanLength()
            if moved < 3:
                self._text_selected = set()
                self._text_hover = self._text_char_at(event.pos())
                self.update()
            else:
                txt = self._selected_text()
                if txt:
                    self._show_hud(f"已选 {len(txt)} 字 · Ctrl+C 复制", ms=1800)
                self.update()
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            if getattr(self, '_expand_btn_pressed', False):
                self._expand_btn_pressed = False
                self._left_dragging = False
                self._file_drag_armed = False
                gpos = event.globalPos()
                dist = (gpos - getattr(self, '_press_global_pos', gpos)).manhattanLength()
                if getattr(self, '_is_collapsed', False) and (
                    self._collapsed_expand_rect().contains(event.pos())
                    or dist < QApplication.startDragDistance()
                ):
                    self.toggle_collapse()
                else:
                    self.update()
                event.accept()
                return

            self._left_dragging = False
            self._file_drag_armed = False
            gpos = event.globalPos()
            handled = False
            if self.main_app_ref and hasattr(self.main_app_ref, "pin_drawer") and self.main_app_ref.pin_drawer:
                handled = self.main_app_ref.pin_drawer.on_external_pin_released(self, gpos)
            if handled:
                event.accept()
                return

            # 未存入抽屉且松手在桌面：恢复完整大小并居中落位
            if getattr(self, '_is_mini_for_drawer', False):
                self._is_mini_for_drawer = False
                self.resize(self._normal_size)
                self.zoom_factor = self._normal_zoom
                self.move(max(0, gpos.x() - self._normal_size.width() // 2),
                          max(0, gpos.y() - self._normal_size.height() // 2))
                self.update()
        super().mouseReleaseEvent(event)

    def _trigger_os_file_drag(self):
        """在左键拖拽过程中点击右键后，进入可右键往复切换的 PNG 文件 / 贴图拖拽"""
        export_pix = getattr(self, 'original_pixmap', None)
        if export_pix is None or (hasattr(export_pix, 'isNull') and export_pix.isNull()):
            export_pix = getattr(self, 'pixmap', None)
        if export_pix is None or (hasattr(export_pix, 'isNull') and export_pix.isNull()):
            self._left_dragging = False
            self._file_drag_armed = False
            self._suppress_next_context_menu = False
            return

        had_mini = getattr(self, '_is_mini_for_drawer', False)
        self._is_mini_for_drawer = False
        self._left_dragging = False
        self._file_drag_armed = False

        if had_mini and hasattr(self, '_normal_size') and self._normal_size is not None:
            self.zoom_factor = getattr(self, '_normal_zoom', getattr(self, 'zoom_factor', 1.0))
            self.resize(self._normal_size)

        saved_pos = self.pos()
        if self.main_app_ref and hasattr(self.main_app_ref, "pin_drawer") and self.main_app_ref.pin_drawer:
            drawer = self.main_app_ref.pin_drawer
            if getattr(drawer, '_is_expanded', False):
                drawer.retract()

        was_visible = self.isVisible()
        if was_visible:
            self.hide()
            if getattr(self, '_ct_handle', None) is not None:
                try:
                    self._ct_handle.hide()
                except RuntimeError:
                    pass
            QApplication.processEvents()

        outcome, drop_pos = "file", None
        try:
            outcome, _drag_res, drop_pos = run_toggleable_drag(
                self,
                export_pix,
                start_mode="file",
                copy_to_clipboard=True,
                file_drag_fn=lambda w, p: start_pixmap_file_drag(w, p),
            )
        finally:
            self._left_dragging = False
            self._file_drag_armed = False
            self._suppress_next_context_menu = False
            if was_visible:
                if outcome == "drop_pin" and drop_pos is not None:
                    saved_pos = QPoint(
                        max(0, drop_pos.x() - self.width() // 2),
                        max(0, drop_pos.y() - self.height() // 2),
                    )
                self.move(saved_pos)
                self.show()
                self._sync_click_through_handle()
                self.update()

    def contextMenuEvent(self, event):
        if (
            getattr(self, '_suppress_next_context_menu', False)
            or getattr(self, '_file_drag_armed', False)
            or getattr(self, '_left_dragging', False)
        ):
            self._suppress_next_context_menu = False
            event.accept()
            return
        if self._text_selected:
            exec_text_selection_menu(
                self, self.mapToGlobal(event.pos()), self._selected_text(),
                on_copy=self._copy_selected_text,
                on_copy_table=self._copy_selected_as_table,
                on_translate=self._translate_selected_text,
                on_select_all=self._select_all_text,
                on_clear=self._clear_text_selection,
                extra_exit_action=("🔓 退出划词取字 (T)", lambda: self.toggle_text_select_mode(False))
                if self._text_select_mode else None,
            )
            return
        menu = QMenu(self)
        menu.setStyleSheet(CODEX_MENU_QSS)
        drawer_action = menu.addAction("🗄️ 存入抽屉 (Drawer)")
        text_mode_action = menu.addAction("✅ 退出划词取字 (T)" if self._text_select_mode else "✏️ 贴图内划词取字 (T · Shift+拖选)")
        silent_ocr_action = menu.addAction("⚡ 静默识别文字并复制")
        ocr_action = menu.addAction("🔤 OCR 识别与翻译 (F2)")
        qr_action = menu.addAction("🔲 识别二维码 / 条码")
        edit_action = menu.addAction("🛠 编辑标注")
        menu.addSeparator()
        copy_img_action = menu.addAction("📋 复制图片 (Ctrl+C)")
        copy_file_action = menu.addAction("📄 复制为 PNG 文件 (Ctrl+Shift+C)")
        save_action = menu.addAction("💾 另存为... (Ctrl+S)")
        reset_action = menu.addAction("⚖️ 1:1 原始大小与重置透明度 (1)")
        collapse_label = "📌 展开贴图 (M)" if getattr(self, '_is_collapsed', False) else "📌 折叠为紧凑标签 (M)"
        collapse_action = menu.addAction(collapse_label)
        border_action = None
        if self.main_app_ref and hasattr(self.main_app_ref, "toggle_pin_border"):
            border_on = bool(getattr(self.main_app_ref, "pin_border_enabled", False))
            hk_border = load_settings().get("hotkey_pin_border", "")
            hk_hint = f" ({hk_border})" if hk_border else ""
            border_action = menu.addAction(
                f"✅ 关闭贴图边框{hk_hint}" if border_on else f"🖼️ 开启贴图边框{hk_hint}"
            )
        click_through_action = menu.addAction("👻 鼠标穿透 (Ctrl+Shift+T · Ctrl+Alt+T 恢复)")
        menu.addSeparator()
        drawer_all_action = close_all_action = None
        if self.main_app_ref and hasattr(self.main_app_ref, "pin_all_to_drawer"):
            drawer_all_action = menu.addAction("🗂️ 全部贴图存入抽屉")
        if self.main_app_ref and hasattr(self.main_app_ref, "close_all_pins"):
            close_all_action = menu.addAction("🧹 关闭全部贴图")
        close_action = menu.addAction("❌ 关闭当前贴图 (Esc)")

        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action is None:
            return
        if action == drawer_action:
            if self.main_app_ref and hasattr(self.main_app_ref, "pin_drawer") and self.main_app_ref.pin_drawer:
                self.main_app_ref.pin_drawer.add_pin_directly(self)
        elif action == text_mode_action:
            self.toggle_text_select_mode()
        elif action == silent_ocr_action:
            self.silent_ocr_copy()
        elif action == ocr_action:
            self.trigger_ocr()
        elif action == qr_action:
            self.scan_codes()
        elif action == edit_action and self.main_app_ref:
            active_ed = getattr(self, "_active_editor", None)
            if active_ed is not None and active_ed.isVisible():
                _force_activate_window(active_ed)
                return
            pinned_before = set(self.main_app_ref.pinned_windows)
            editor = ImageEditorDialog(self.pixmap, self.main_app_ref)
            self._active_editor = editor
            try:
                editor.exec_nonmodal()
            finally:
                self._active_editor = None
            # 仅当用户在编辑器中执行了 Pin（创建了新 PinnedWindow）时才关闭原贴图
            # 如果用户只是按 ESC 或 Close 关闭编辑器，原贴图保留不动
            if any(p not in pinned_before for p in self.main_app_ref.pinned_windows):
                self.close()
        elif action == copy_img_action:
            QApplication.clipboard().setPixmap(self.pixmap)
            self._show_hud("已复制图片")
        elif action == copy_file_action:
            self.copy_as_file()
        elif action == save_action:
            self.save_as()
        elif action == reset_action:
            self.reset_transform()
        elif action == collapse_action:
            self.toggle_collapse()
        elif border_action is not None and action == border_action:
            self.main_app_ref.toggle_pin_border()
        elif action == click_through_action:
            self.set_click_through(True)
        elif drawer_all_action is not None and action == drawer_all_action:
            self.main_app_ref.pin_all_to_drawer()
        elif close_all_action is not None and action == close_all_action:
            self.main_app_ref.close_all_pins()
        elif action == close_action:
            self.close()

    def trigger_ocr(self):
        try:
            QToolTip.showText(QCursor.pos(), "🔤 正在识别贴图文字与条码...", None, QRect(), 2500)
            ocr_img = self.pixmap.toImage()
            worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))
            def _on_done(text):
                QToolTip.hideText()
                codes = getattr(worker, "detected_codes", [])
                doc = getattr(worker, "ocr_doc", None)
                warn = getattr(worker, "ocr_warning", "")
                if (not text or not text.strip()) and not codes:
                    QMessageBox.information(None, "文字识别结果", "未能从贴图中识别到有效文字或二维码。")
                    return
                dlg = OcrResultDialog(
                    text, None, detected_codes=codes, ocr_doc=doc,
                    source_image=getattr(worker, "image", None), ocr_warning=warn
                )
                dlg.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
                self._temp_ocr_dialog = dlg
                exec_dialog_nonmodal(dlg)
            worker.finished.connect(_on_done)
            worker.start()
            self._temp_ocr_worker = worker
        except Exception as e:
            logging.error("贴图 OCR 触发异常: %s", e, exc_info=True)

    def keyPressEvent(self, event):
        mods = event.modifiers()
        key = event.key()
        if key == Qt.Key_Escape:
            if self._text_selected:
                self._clear_text_selection()
            elif self._text_select_mode:
                self.toggle_text_select_mode(False)
            else:
                self.close()
        elif key == Qt.Key_T and not mods:
            self.toggle_text_select_mode()
        elif key in (Qt.Key_Return, Qt.Key_Enter) and not mods and self._text_selected:
            self._copy_selected_text()
        elif (mods & Qt.ControlModifier) and key == Qt.Key_A and (self._text_select_mode or self._text_selected) and self._text_doc_ready():
            self._select_all_text()
        elif key == Qt.Key_F2:
            self.trigger_ocr()
        elif key == Qt.Key_1 and not mods:
            self.reset_transform()
        elif key == Qt.Key_M and not mods:
            self.toggle_collapse()
        elif (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier) and key == Qt.Key_C:
            self.copy_as_file()
        elif (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier) and key == Qt.Key_T:
            self.set_click_through(True)
        elif (mods & Qt.ControlModifier) and key == Qt.Key_C:
            if self._text_selected:
                self._copy_selected_text()
                return
            QApplication.clipboard().setPixmap(self.pixmap)
            self._show_hud("已复制图片")
            print("Pinned image copied!")
        elif (mods & Qt.ControlModifier) and key == Qt.Key_S:
            self.save_as()

class _CanvasEditingMixin:
    """截图画布与独立图片编辑器共享的矢量标注、行内文本、橡皮擦与裁剪交互混入。"""

    @staticmethod
    def _constrain_square_point(p1, p2):
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        side = max(abs(dx), abs(dy))
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        return QPoint(p1.x() + sx * side, p1.y() + sy * side)

    @staticmethod
    def _snap_angle_45(p1, p2):
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        angle = math.degrees(math.atan2(dy, dx))
        snapped = round(angle / 45.0) * 45.0
        rad = math.radians(snapped)
        dist = math.hypot(dx, dy)
        return QPoint(int(round(p1.x() + dist * math.cos(rad))), int(round(p1.y() + dist * math.sin(rad))))

    @staticmethod
    def _draw_text_draw_preview(painter, text_rect):
        if text_rect is None or text_rect.width() <= 3 or text_rect.height() <= 3:
            return
        painter.save()
        painter.fillRect(text_rect, QColor(255, 255, 255, 35))
        painter.setPen(QPen(QColor(255, 255, 255, 210), 2, Qt.DashLine))
        painter.drawRect(text_rect)
        painter.setPen(QPen(QColor(200, 144, 48), 1.4, Qt.DashLine))
        painter.drawRect(text_rect)
        painter.restore()

    @staticmethod
    def _draw_crop_chrome(painter, crop_rect, grid_alpha=80):
        painter.setPen(QPen(QColor(255, 255, 255, grid_alpha), 1))
        for i in range(1, 3):
            x = crop_rect.left() + crop_rect.width() * i // 3
            painter.drawLine(x, crop_rect.top(), x, crop_rect.bottom())
            y = crop_rect.top() + crop_rect.height() * i // 3
            painter.drawLine(crop_rect.left(), y, crop_rect.right(), y)

        painter.setPen(QPen(QColor(204, 153, 68), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(crop_rect)

        handle = CROP_HANDLE_SIZE
        painter.setBrush(QBrush(QColor(204, 153, 68)))
        painter.setPen(QPen(Qt.white, 1))
        corners = [crop_rect.topLeft(), crop_rect.topRight(),
                   crop_rect.bottomLeft(), crop_rect.bottomRight()]
        edges = [QPoint(crop_rect.center().x(), crop_rect.top()),
                 QPoint(crop_rect.center().x(), crop_rect.bottom()),
                 QPoint(crop_rect.left(), crop_rect.center().y()),
                 QPoint(crop_rect.right(), crop_rect.center().y())]
        for pt in corners + edges:
            painter.drawRect(pt.x() - handle // 2, pt.y() - handle // 2, handle, handle)
        painter.setBrush(Qt.NoBrush)

    def _get_crop_handle_at(self, pos):
        """返回 pos 处的裁剪手柄名称"""
        if not self.crop_rect:
            return None
        r = self.crop_rect
        hs = CROP_HANDLE_SIZE
        corners = {
            'tl': r.topLeft(), 'tr': r.topRight(),
            'bl': r.bottomLeft(), 'br': r.bottomRight()
        }
        for name, pt in corners.items():
            if abs(pos.x() - pt.x()) <= hs and abs(pos.y() - pt.y()) <= hs:
                return name
        edges = {
            't': QPoint(r.center().x(), r.top()),
            'b': QPoint(r.center().x(), r.bottom()),
            'l': QPoint(r.left(), r.center().y()),
            'r': QPoint(r.right(), r.center().y()),
        }
        for name, pt in edges.items():
            if abs(pos.x() - pt.x()) <= hs and abs(pos.y() - pt.y()) <= hs:
                return name
        if r.contains(pos):
            return 'move'
        return None

    def _compute_updated_crop_rect(self, pos, bounds):
        """根据拖动手柄与比例约束计算新裁剪框（约束在 bounds 内）。"""
        start = self.crop_drag_start_rect
        h = self.crop_drag_handle
        if start is None or not h:
            return None
        new_rect = QRect(start)

        if h == 'move':
            new_rect = start.translated(pos - self.crop_drag_start_pos)
        else:
            if 'l' in h:
                new_rect.setLeft(max(bounds.left(), min(start.right() - CROP_MIN_SIZE, pos.x())))
            if 'r' in h:
                new_rect.setRight(min(bounds.right(), max(start.left() + CROP_MIN_SIZE, pos.x())))
            if 't' in h:
                new_rect.setTop(max(bounds.top(), min(start.bottom() - CROP_MIN_SIZE, pos.y())))
            if 'b' in h:
                new_rect.setBottom(min(bounds.bottom(), max(start.top() + CROP_MIN_SIZE, pos.y())))

            if self.crop_ratio:
                rw, rh = self.crop_ratio
                target_ratio = rw / rh
                if h in ('tl', 'tr', 'bl', 'br'):
                    if h == 'br':
                        ax, ay = start.left(), start.top()
                        new_w = max(CROP_MIN_SIZE, pos.x() - ax)
                        new_h = int(new_w / target_ratio)
                        new_rect = QRect(ax, ay, new_w, new_h)
                    elif h == 'tl':
                        ax, ay = start.right(), start.bottom()
                        new_w = max(CROP_MIN_SIZE, ax - pos.x())
                        new_h = int(new_w / target_ratio)
                        new_rect = QRect(ax - new_w, ay - new_h, new_w, new_h)
                    elif h == 'tr':
                        ax, ay = start.left(), start.bottom()
                        new_w = max(CROP_MIN_SIZE, pos.x() - ax)
                        new_h = int(new_w / target_ratio)
                        new_rect = QRect(ax, ay - new_h, new_w, new_h)
                    elif h == 'bl':
                        ax, ay = start.right(), start.top()
                        new_w = max(CROP_MIN_SIZE, ax - pos.x())
                        new_h = int(new_w / target_ratio)
                        new_rect = QRect(ax - new_w, ay, new_w, new_h)
                elif h in ('t', 'b'):
                    cx = start.center().x()
                    new_h = max(CROP_MIN_SIZE, pos.y() - start.top()) if h == 'b' else max(CROP_MIN_SIZE, start.bottom() - pos.y())
                    new_w = int(new_h * target_ratio)
                    new_x = cx - new_w // 2
                    new_y = start.bottom() - new_h if h == 't' else start.top()
                    new_rect = QRect(new_x, new_y, new_w, new_h)
                elif h in ('l', 'r'):
                    cy = start.center().y()
                    new_w = max(CROP_MIN_SIZE, pos.x() - start.left()) if h == 'r' else max(CROP_MIN_SIZE, start.right() - pos.x())
                    new_h = int(new_w / target_ratio)
                    new_x = start.right() - new_w if h == 'l' else start.left()
                    new_y = cy - new_h // 2
                    new_rect = QRect(new_x, new_y, new_w, new_h)

        new_rect = new_rect.intersected(bounds)
        if new_rect.width() >= CROP_MIN_SIZE and new_rect.height() >= CROP_MIN_SIZE:
            return new_rect
        return None

    def _find_editable_text_edit_index(self, pos):
        for i in range(len(self.edits) - 1, -1, -1):
            if (self.edits[i].get('type') == 'TEXT'
                    and int(self.edits[i].get('rotation', 0) or 0) % 360 == 0
                    and is_annotation_hit(self.edits[i], pos)):
                return i
        return None

    def _open_existing_text_edit(self, idx, start_in_text_mode=True, click_global_pos=None):
        edit = self.edits.pop(idx)
        self.save_state()
        ob = getattr(self, 'options_bar', None)
        if ob is not None:
            ob.sync_text_options_from_edit(edit)
        tr = getattr(self, 'toolbar_ref', None)
        if tr is not None and hasattr(tr, '_sync_color_button_from_active_tool'):
            tr._sync_color_button_from_active_tool()
        edit_rect = get_text_annotation_rect(edit).toAlignedRect()
        self.inline_editor.start_editing(edit_rect, existing_edit=edit, start_in_text_mode=start_in_text_mode)
        if start_in_text_mode:
            self.inline_editor.set_editing_text(True, click_global_pos=click_global_pos)
            self.drag_mode = None
        else:
            self.drag_mode = 'text_move'
            self._text_move_start_global = click_global_pos
            self._text_move_start_frame = QRect(self.inline_editor.get_text_rect())
            self._text_move_dragged = False
        self.update()

    def _commit_inline_text_edit(self):
        if not getattr(self, 'inline_editor', None) or not self.inline_editor.isVisible():
            return False
        text = self.inline_editor.text_edit.toPlainText()
        had_existing = self.inline_editor.active_edit_ref is not None
        committed = bool(text.strip())
        if committed:
            if not had_existing:
                self.save_state()
            rect = self.inline_editor.get_text_rect()
            ob = getattr(self, 'options_bar', None)
            opts = ob.get_text_options() if ob is not None else {}
            font = self.inline_editor.text_edit.font()
            self.edits.append({
                'type': 'TEXT',
                'pos': rect.topLeft(),
                'rect': rect,
                'text': text,
                'color': QColor(opts.get('color', self.inline_editor.current_color)),
                'font': QFont(font),
                'fontFamily': opts.get('fontFamily', font.family()),
                'fontSize': opts.get('fontSize', font.pointSize() if font.pointSize() > 0 else 14),
                'bold': opts.get('bold', font.bold()),
                'italic': opts.get('italic', font.italic()),
                'bgEnabled': opts.get('bgEnabled', self.inline_editor.bg_enabled),
                'bgColor': QColor(opts.get('bgColor', self.inline_editor.bg_color)),
                'user_width': self.inline_editor.user_width,
                'user_height': self.inline_editor.user_height,
            })
        self.inline_editor.active_edit_ref = None
        self.inline_editor.hide()
        if self.isVisible():
            self.setFocus()
        return committed

    def _handle_text_tool_press(self, pos, global_pos, was_editing, had_text):
        clicked_idx = self._find_editable_text_edit_index(pos)
        if clicked_idx is not None:
            self._open_existing_text_edit(clicked_idx, start_in_text_mode=False, click_global_pos=global_pos)
            return 'text_move'
        self.drag_mode = 'text_draw'
        self.text_start = pos
        self.text_rect = QRect(pos, pos)
        self._text_just_committed = bool(was_editing and had_text)
        return 'text_draw'

    def _handle_text_tool_release(self, global_pos):
        if self.drag_mode == 'text_move':
            self.drag_mode = None
            if getattr(self, '_text_move_dragged', False):
                self.inline_editor.set_editing_text(False)
            else:
                self.inline_editor.set_editing_text(True, click_global_pos=global_pos)
            return 'text_move'
        if self.drag_mode == 'text_draw':
            rect = self.text_rect
            self.text_rect = None
            if rect is not None and rect.width() >= 18 and rect.height() >= 14:
                self.inline_editor.start_editing(rect)
            elif not getattr(self, '_text_just_committed', False):
                self.inline_editor.start_editing(self.text_start)
            self._text_just_committed = False
            self.update()
            return 'text_draw'
        return None

    def _erase_annotation_at(self, pos, saved_attr='eraser_state_saved'):
        for i in range(len(self.edits) - 1, -1, -1):
            if is_annotation_hit(self.edits[i], pos):
                if not getattr(self, saved_attr, False):
                    self.save_state()
                    setattr(self, saved_attr, True)
                return self.edits.pop(i)
        return None

    def _begin_annotation_edit(self, tool, pos, default_color, default_thickness=3, options_bar=None, step_num=1):
        ob = options_bar
        if tool == 'STEP':
            self.save_state()
            opts = ob.get_step_options() if (ob is not None and hasattr(ob, 'get_step_options')) else {}
            used_num = opts.get('num', step_num)
            radius = opts.get('radius', max(11, 8 + default_thickness * 2))
            color = QColor(opts.get('color', default_color))
            self.edits.append({
                'type': 'STEP',
                'pos': pos,
                'center': pos,
                'num': used_num,
                'number': used_num,
                'color': color,
                'thickness': max(1, (radius - 8) // 2),
                'radius': radius,
                'temp': False,
            })
            return ('step', used_num + 1)

        if tool in ('RECT_ELLIPSE', 'RECTANGLE', 'ELLIPSE'):
            self.save_state()
            self.edit_start = pos
            if tool == 'RECT_ELLIPSE' and ob is not None and hasattr(ob, 'get_rect_ellipse_options'):
                opts = ob.get_rect_ellipse_options()
            elif tool in ('RECTANGLE', 'ELLIPSE') and ob is not None and hasattr(ob, 'get_rect_options'):
                opts = ob.get_rect_options() if tool == 'RECTANGLE' else ob.get_ellipse_options()
            else:
                opts = None
            if opts is not None:
                self.edits.append({
                    'type': 'RECT_ELLIPSE',
                    'kind': opts.get('kind', 'ellipse' if tool == 'ELLIPSE' else 'rect'),
                    'p1': pos,
                    'p2': pos,
                    'strokeColor': QColor(opts.get('strokeColor', default_color)),
                    'strokeWidth': opts.get('strokeWidth', default_thickness),
                    'fillMode': opts.get('fillMode', 'none'),
                    'fillColor': QColor(opts.get('fillColor', default_color)),
                    'cornerRadius': opts.get('cornerRadius', 0),
                    'temp': True,
                })
            else:
                self.edits.append({
                    'type': tool,
                    'rect': QRect(pos, pos),
                    'color': QColor(default_color),
                    'thickness': default_thickness,
                    'temp': True,
                })
            return 'drawing'

        if tool == 'ARROW':
            self.save_state()
            self.edit_start = pos
            opts = ob.get_arrow_options() if (ob is not None and hasattr(ob, 'get_arrow_options')) else {}
            self.edits.append({
                'type': 'ARROW',
                'start': pos,
                'end': pos,
                'arrowMode': opts.get('arrowMode', 'end'),
                'width': opts.get('width', default_thickness),
                'color': QColor(opts.get('color', default_color)),
                'temp': True,
            })
            return 'drawing'

        if tool == 'LINE':
            self.save_state()
            self.edit_start = pos
            opts = ob.get_line_options() if (ob is not None and hasattr(ob, 'get_line_options')) else {}
            self.edits.append({
                'type': 'LINE',
                'rect': QRect(pos, pos),
                'start': pos,
                'end': pos,
                'color': QColor(opts.get('color', default_color)),
                'thickness': opts.get('thickness', default_thickness),
                'lineStyle': opts.get('lineStyle', 'solid'),
                'arrow': opts.get('arrow', getattr(self, 'arrow_enabled', False)),
                'temp': True,
            })
            return 'drawing'

        if tool == 'PEN':
            self.save_state()
            self.edit_start = pos
            opts = ob.get_pen_options() if (ob is not None and hasattr(ob, 'get_pen_options')) else {}
            self.edits.append({
                'type': 'PEN',
                'penStyle': opts.get('penStyle', 'polyline'),
                'start': pos,
                'end': pos,
                'points': [pos],
                'width': opts.get('width', default_thickness),
                'color': QColor(opts.get('color', default_color)),
                'temp': True,
            })
            return 'drawing'

        if tool in ('MARKER', 'HIGHLIGHTER'):
            self.save_state()
            if ob is not None and hasattr(ob, 'get_marker_options'):
                opts = ob.get_marker_options()
                self.edits.append({
                    'type': 'MARKER',
                    'points': [pos],
                    'color': QColor(opts.get('color', QColor(255, 230, 0))),
                    'width': opts.get('width', 16),
                    'opacity': opts.get('opacity', 45),
                    'tipStyle': opts.get('tipStyle', 'round'),
                    'temp': True,
                })
            else:
                self.edits.append({
                    'type': 'HIGHLIGHTER',
                    'points': [pos],
                    'color': QColor(default_color),
                    'thickness': max(12, default_thickness * 3),
                    'temp': True,
                })
            return 'drawing'

        if tool in ('MOSAIC_BLUR', 'BLUR'):
            self.save_state()
            if ob is not None and hasattr(ob, 'get_mosaic_options'):
                opts = ob.get_mosaic_options()
                self.edits.append({
                    'type': 'MOSAIC_BLUR',
                    'points': [pos],
                    'mode': opts.get('mode', 'mosaic'),
                    'smooth': opts.get('smooth', False),
                    'blockSize': opts.get('blockSize', 10),
                    'brushWidth': opts.get('brushWidth', 22),
                    'temp': True,
                })
            else:
                self.edits.append({
                    'type': 'BLUR_STROKE',
                    'points': [pos],
                    'thickness': default_thickness,
                    'temp': True,
                })
            return 'drawing'

        return None

    def _update_active_temp_edit(self, pos, shift_held):
        if not self.edits or not self.edits[-1].get('temp'):
            return False
        item = self.edits[-1]
        t = item.get('type')
        if t == 'RECT_ELLIPSE':
            item['p2'] = self._constrain_square_point(self.edit_start, pos) if shift_held else pos
        elif t in ('ARROW', 'LINE'):
            p2 = self._snap_angle_45(self.edit_start, pos) if shift_held else pos
            item['end'] = p2
            if t == 'LINE':
                item['rect'] = QRect(self.edit_start, p2).normalized()
        elif t == 'PEN':
            if item.get('penStyle') == 'line':
                p2 = self._snap_angle_45(self.edit_start, pos) if shift_held else pos
                item['end'] = p2
                item['points'] = [self.edit_start, p2]
            else:
                item['points'].append(pos)
        elif t in ('MOSAIC_BLUR', 'MARKER', 'BLUR_STROKE', 'HIGHLIGHTER'):
            item['points'].append(pos)
        elif t == 'ELLIPSE':
            p2 = self._constrain_square_point(self.edit_start, pos) if shift_held else pos
            item['rect'] = QRect(self.edit_start, p2).normalized()
        else:
            item['rect'] = QRect(self.edit_start, pos).normalized()
            item['end'] = pos
        return True

    def _finish_active_temp_edit(self):
        if not self.edits or not self.edits[-1].get('temp'):
            return None, False
        item = self.edits[-1]
        t = item.get('type')
        discard = False
        if t == 'RECT_ELLIPSE':
            discard = (item.get('p2', QPoint()) - item.get('p1', QPoint())).manhattanLength() < 2
        elif t in ('ARROW', 'LINE'):
            discard = (item.get('end', QPoint()) - item.get('start', QPoint())).manhattanLength() < 2
        elif t == 'PEN':
            discard = len(item.get('points', [])) <= 1
        elif t in ('MARKER', 'MOSAIC_BLUR', 'BLUR_STROKE', 'HIGHLIGHTER'):
            discard = len(item.get('points', [])) < 1

        if discard:
            self.edits.pop()
            if self.undo_stack:
                self.undo_stack.pop()
            return None, True
        item['temp'] = False
        return item, False

    def _apply_editing_tool_cursor(self, tool, pos=None, options_bar=None, default_thickness=3, allow_text_hit=True):
        if self.crop_mode:
            if pos is not None and not getattr(self, 'crop_drag_handle', None):
                handle = self._get_crop_handle_at(pos)
                cursors = {
                    'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
                    'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
                    't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
                    'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
                    'move': Qt.SizeAllCursor,
                }
                self.setCursor(cursors.get(handle, Qt.ArrowCursor))
            elif pos is None:
                self.setCursor(Qt.SizeAllCursor)
            return True

        ob = options_bar
        if tool in ('ERASER', 'ERASE'):
            self.setCursor(create_eraser_cursor())
            return True
        if tool == 'ROTATE':
            self.setCursor(Qt.ArrowCursor)
            return True
        if tool == 'TEXT':
            hit_text = bool(allow_text_hit and pos is not None and self._find_editable_text_edit_index(pos) is not None)
            self.setCursor(Qt.SizeAllCursor if hit_text else Qt.IBeamCursor)
            return True
        if tool in ('MOSAIC_BLUR', 'BLUR'):
            bw = ob.get_mosaic_options().get('brushWidth', 22) if ob else default_thickness * 8
            self.setCursor(create_brush_cursor(max(4, bw // 2)))
            return True
        if tool in ('MARKER', 'HIGHLIGHTER'):
            mw = ob.get_marker_options().get('width', 16) if ob else 16
            self.setCursor(create_brush_cursor(max(4, mw // 2)))
            return True
        if tool == 'STEP':
            self.setCursor(Qt.PointingHandCursor)
            return True
        if tool in ('RECT_ELLIPSE', 'RECTANGLE', 'ELLIPSE', 'LINE', 'ARROW', 'PEN', 'OCR'):
            self.setCursor(Qt.CrossCursor)
            return True
        return False


class EditableImageLabel(_CanvasEditingMixin, QLabel):
    def __init__(self, pixmap, toolbar_ref):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)
        self.base_pixmap = pixmap
        self.setPixmap(pixmap)
        self.toolbar_ref = toolbar_ref
        self.edits = []
        
        self.undo_stack = []
        self.redo_stack = []
        
        self.inline_editor = InlineTextEditor(self)
        self.inline_editor.hide()

        self.is_drawing = False
        self.drag_mode = None
        self.text_rect = None
        self.text_start = QPoint()
        self._text_just_committed = False
        self._text_move_start_global = QPoint()
        self._text_move_start_frame = QRect()
        self._text_move_dragged = False
        self._eraser_state_saved = False
        self.crop_mode = False
        self.setMouseTracking(True)
        self.crop_rect = None
        self.crop_ratio = None
        self.crop_drag_handle = None
        self.crop_drag_start_pos = None
        self.crop_drag_start_rect = None
        self._rotation_preview_pixmap = None
        self._rotation_preview_snapshot = None

    @property
    def options_bar(self):
        return getattr(self.toolbar_ref, 'sub_bar', None)

    def _notify_editor_state_changed(self, sync_step=False):
        tr = self.toolbar_ref
        if tr is not None:
            if hasattr(tr, '_sync_undo_redo_buttons'):
                tr._sync_undo_redo_buttons()
            if sync_step and hasattr(tr, 'sync_step_num_from_edits'):
                tr.sync_step_num_from_edits()

    def has_pending_rotation_preview(self):
        return self._rotation_preview_snapshot is not None

    def apply_orthogonal_transform(self, op):
        """执行无损正交旋转或镜像翻转，保持矢量标注同步变换并写入撤销栈。"""
        if getattr(self, 'inline_editor', None) and self.inline_editor.isVisible():
            self.commit_text_edit()
        if self.has_pending_rotation_preview():
            self.commit_rotation_preview()
        if self.base_pixmap is None or self.base_pixmap.isNull():
            return
        self.save_state()
        old_size = self.base_pixmap.size()
        new_pm = transform_pixmap_op(self.base_pixmap, op)
        new_size = new_pm.size()
        self.edits = transform_edits_op(
            self.edits, QPoint(0, 0), old_size, old_size,
            QPoint(0, 0), new_size, new_size, op
        )
        self.base_pixmap = new_pm
        self.setPixmap(self.base_pixmap)
        self.resize(self.base_pixmap.size())
        if hasattr(self.toolbar_ref, '_sync_dim_badge'):
            self.toolbar_ref._sync_dim_badge()
        self._notify_editor_state_changed()
        self.update()

    def preview_rotation_angle(self, angle_deg):
        """实时预览任意角度旋转（无损从起点快照重算，不叠加插值模糊）。"""
        angle = int(angle_deg)
        if angle == 0:
            self.cancel_rotation_preview()
            return
        if self._rotation_preview_snapshot is None:
            if getattr(self, 'inline_editor', None) and self.inline_editor.isVisible():
                self.commit_text_edit()
            src_comp = composite_pixmap_with_edits(self.base_pixmap, self.edits, QPoint(0, 0))
            self._rotation_preview_snapshot = {
                'base_pixmap': QPixmap(self.base_pixmap),
                'edits': clone_edits(self.edits),
                'composite': src_comp,
                'step_num': getattr(self.toolbar_ref, 'current_step_num', 1),
            }
        src_comp = self._rotation_preview_snapshot['composite']
        prev_pm = rotate_pixmap_angle(src_comp, angle)
        self._rotation_preview_pixmap = prev_pm
        self.setPixmap(prev_pm)
        self.resize(prev_pm.size())
        if hasattr(self.toolbar_ref, '_sync_dim_badge'):
            self.toolbar_ref._sync_dim_badge(prev_pm)
        self.update()

    def commit_rotation_preview(self):
        """确认当前自由角度旋转预览并写入撤销栈。"""
        if self._rotation_preview_snapshot is None:
            return False
        snap = self._rotation_preview_snapshot
        prev_pm = self._rotation_preview_pixmap
        self._rotation_preview_snapshot = None
        self._rotation_preview_pixmap = None
        if prev_pm is not None and not prev_pm.isNull():
            self.undo_stack.append({
                'edits': clone_edits(snap['edits']),
                'base_pixmap': QPixmap(snap['base_pixmap']),
                'step_num': snap.get('step_num', 1),
            })
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self.base_pixmap = QPixmap(prev_pm)
            self.edits = []
            self.setPixmap(self.base_pixmap)
            self.resize(self.base_pixmap.size())
            if hasattr(self.toolbar_ref, '_sync_dim_badge'):
                self.toolbar_ref._sync_dim_badge()
        if hasattr(self.toolbar_ref, 'rotate_controls') and self.toolbar_ref.rotate_controls is not None:
            self.toolbar_ref.rotate_controls.set_angle(0, emit=False)
        self._notify_editor_state_changed(sync_step=True)
        self.update()
        return True

    def cancel_rotation_preview(self):
        """取消未提交的自由角度旋转预览，恢复原图与标注。"""
        if self._rotation_preview_snapshot is None:
            return False
        snap = self._rotation_preview_snapshot
        self._rotation_preview_snapshot = None
        self._rotation_preview_pixmap = None
        self.base_pixmap = QPixmap(snap['base_pixmap'])
        self.edits = clone_edits(snap['edits'])
        self.setPixmap(self.base_pixmap)
        self.resize(self.base_pixmap.size())
        if hasattr(self.toolbar_ref, '_sync_dim_badge'):
            self.toolbar_ref._sync_dim_badge()
        if hasattr(self.toolbar_ref, 'rotate_controls') and self.toolbar_ref.rotate_controls is not None:
            self.toolbar_ref.rotate_controls.set_angle(0, emit=False)
        self.update()
        return True

    def save_state(self):
        try:
            state = {
                'edits': clone_edits(self.edits),
                'base_pixmap': QPixmap(self.base_pixmap),
                'step_num': getattr(self.toolbar_ref, 'current_step_num', 1),
            }
            self.undo_stack.append(state)
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self._notify_editor_state_changed()
        except Exception as e:
            logging.error("保存编辑状态失败: %s", e, exc_info=True)

    def _restore_step_num_from_state(self, state):
        if 'step_num' in state and self.toolbar_ref is not None:
            val = max(1, int(state['step_num']))
            self.toolbar_ref.current_step_num = val
            if self.options_bar is not None:
                self.options_bar.set_step_num(val)

    def undo(self):
        try:
            if getattr(self, 'inline_editor', None) and self.inline_editor.isVisible():
                self.commit_text_edit()
            if self.has_pending_rotation_preview():
                return self.cancel_rotation_preview()
            if self.undo_stack:
                if self.crop_mode and hasattr(self.toolbar_ref, 'cancel_crop'):
                    self.toolbar_ref.cancel_crop()
                self.redo_stack.append({
                    'edits': clone_edits(self.edits),
                    'base_pixmap': QPixmap(self.base_pixmap),
                    'step_num': getattr(self.toolbar_ref, 'current_step_num', 1),
                })
                state = self.undo_stack.pop()
                self.edits = state['edits']
                self.base_pixmap = state['base_pixmap']
                self.setPixmap(self.base_pixmap)
                self.resize(self.base_pixmap.size())
                if hasattr(self.toolbar_ref, '_sync_dim_badge'):
                    self.toolbar_ref._sync_dim_badge()
                self._restore_step_num_from_state(state)
                self._notify_editor_state_changed()
                self.update()
                return True
        except Exception as e:
            logging.error("撤回操作失败: %s", e, exc_info=True)
        return False

    def redo(self):
        try:
            if getattr(self, 'inline_editor', None) and self.inline_editor.isVisible():
                self.commit_text_edit()
            if self.has_pending_rotation_preview():
                self.cancel_rotation_preview()
            if self.redo_stack:
                if self.crop_mode and hasattr(self.toolbar_ref, 'cancel_crop'):
                    self.toolbar_ref.cancel_crop()
                self.undo_stack.append({
                    'edits': clone_edits(self.edits),
                    'base_pixmap': QPixmap(self.base_pixmap),
                    'step_num': getattr(self.toolbar_ref, 'current_step_num', 1),
                })
                state = self.redo_stack.pop()
                self.edits = state['edits']
                self.base_pixmap = state['base_pixmap']
                self.setPixmap(self.base_pixmap)
                self.resize(self.base_pixmap.size())
                if hasattr(self.toolbar_ref, '_sync_dim_badge'):
                    self.toolbar_ref._sync_dim_badge()
                self._restore_step_num_from_state(state)
                self._notify_editor_state_changed()
                self.update()
                return True
        except Exception as e:
            logging.error("重做操作失败: %s", e, exc_info=True)
        return False

    def clear_all_edits(self):
        """清空当前全部矢量/马赛克标注（可撤销恢复）。"""
        if getattr(self, 'inline_editor', None) and self.inline_editor.isVisible():
            self.inline_editor.cancel_edit()
        if not self.edits:
            return False
        self.save_state()
        self.edits.clear()
        if self.toolbar_ref is not None:
            self.toolbar_ref.current_step_num = 1
        if self.options_bar is not None:
            self.options_bar.set_step_num(1)
        self._notify_editor_state_changed()
        self.update()
        return True

    def erase_annotation(self, pos):
        if self._erase_annotation_at(pos, saved_attr='_eraser_state_saved') is not None:
            self._notify_editor_state_changed()
            return True
        return False

    def update_cursor_for_tool(self, pos=None):
        tool = getattr(self.toolbar_ref, 'current_tool', None)
        if not self._apply_editing_tool_cursor(
            tool, pos=pos, options_bar=self.options_bar,
            default_thickness=getattr(self.toolbar_ref, 'current_thickness', 3),
            allow_text_hit=True,
        ):
            self.setCursor(Qt.ArrowCursor)

    def paintEvent(self, event):
        try:
            super().paintEvent(event)
            painter = QPainter(self)
            if self._rotation_preview_pixmap is None:
                draw_all_edits(painter, self.edits, self.base_pixmap, QPoint(0, 0))
            
            if self.toolbar_ref.current_tool == 'TEXT' and getattr(self, 'drag_mode', None) == 'text_draw':
                self._draw_text_draw_preview(painter, getattr(self, 'text_rect', None))

            if self.crop_mode and self.crop_rect:
                # 暗化裁剪框外区域
                painter.fillRect(self.rect(), QColor(0, 0, 0, 150))
                # 裁剪框内显示原图
                valid_crop = self.crop_rect.intersected(self.base_pixmap.rect())
                if not valid_crop.isEmpty():
                    painter.drawPixmap(valid_crop, self.base_pixmap.copy(valid_crop))
                # 裁剪框内显示编辑标注（设置 clip）
                painter.setClipRect(self.crop_rect)
                draw_all_edits(painter, self.edits, self.base_pixmap, QPoint(0, 0))
                painter.setClipping(False)
                self._draw_crop_chrome(painter, self.crop_rect, grid_alpha=80)

            if self.toolbar_ref.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                painter.setPen(QPen(Qt.green, 2, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self.ocr_rect)
        except Exception as e:
            logging.error("EditableImageLabel paintEvent 出错: %s", e, exc_info=True)

    def commit_text_edit(self):
        if not getattr(self, 'inline_editor', None) or not self.inline_editor.isVisible():
            return
        self._commit_inline_text_edit()
        self._notify_editor_state_changed()
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        try:
            tool = getattr(self.toolbar_ref, 'current_tool', None)
            if tool in ['RECTANGLE', 'ELLIPSE', 'LINE', 'ARROW', 'PEN', 'HIGHLIGHTER', 'STEP', 'BLUR'] and not self.crop_mode:
                delta = event.angleDelta().y()
                step = 1 if delta > 0 else -1
                ob = self.options_bar
                if ob is not None:
                    spin_map = {
                        'RECTANGLE': (ob.spin_rect_width, step),
                        'ELLIPSE': (ob.spin_ellipse_width, step),
                        'LINE': (ob.spin_line_width, step),
                        'ARROW': (ob.spin_arrow_width, step),
                        'PEN': (ob.spin_pen_width, step),
                        'HIGHLIGHTER': (ob.spin_marker_width, step * 2),
                        'STEP': (ob.spin_step_radius, step),
                        'BLUR': (ob.spin_mb_brush, step * 2),
                    }
                    sb, diff = spin_map[tool]
                    sb.setValue(sb.value() + diff)
                    if self.edits and self.edits[-1].get('temp'):
                        cur = self.edits[-1]
                        if cur.get('type') == 'RECT_ELLIPSE':
                            cur['strokeWidth'] = sb.value()
                        elif cur.get('type') == 'LINE':
                            cur['thickness'] = sb.value()
                        elif cur.get('type') in ('ARROW', 'PEN', 'MARKER'):
                            cur['width'] = sb.value()
                        elif cur.get('type') == 'MOSAIC_BLUR':
                            cur['brushWidth'] = sb.value()
                else:
                    if delta > 0:
                        self.toolbar_ref.current_thickness = min(20, getattr(self.toolbar_ref, 'current_thickness', 3) + 1)
                    else:
                        self.toolbar_ref.current_thickness = max(1, getattr(self.toolbar_ref, 'current_thickness', 3) - 1)
                    if self.edits and self.edits[-1].get('temp'):
                        self.edits[-1]['thickness'] = self.toolbar_ref.current_thickness
                self.update_cursor_for_tool(event.pos())
                self.update()
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            logging.error("EditableImageLabel wheelEvent 出错: %s", e, exc_info=True)

    def mouseDoubleClickEvent(self, event):
        try:
            pos = event.pos()
            # 裁剪模式下双击裁剪框内 = 应用裁剪
            if self.crop_mode and self.crop_rect and self.crop_rect.contains(pos):
                self.toolbar_ref.apply_crop()
                return
            idx = self._find_editable_text_edit_index(pos)
            if idx is not None:
                self.commit_text_edit()
                if self.toolbar_ref.current_tool != 'TEXT':
                    self.toolbar_ref.set_tool('TEXT', getattr(self.toolbar_ref, 'btn_text', None))
                self._open_existing_text_edit(idx, start_in_text_mode=True, click_global_pos=event.globalPos())
        except Exception as e:
            logging.error("EditableImageLabel mouseDoubleClickEvent 出错: %s", e, exc_info=True)

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.RightButton:
                if self.crop_mode:
                    self.toolbar_ref.cancel_crop()
                    return
                if self.toolbar_ref and self.toolbar_ref.current_tool:
                    self.toolbar_ref.set_tool(None)
                    return
            if event.button() != Qt.LeftButton:
                return
            pos = event.pos()

            # 裁剪模式优先处理
            if self.crop_mode:
                handle = self._get_crop_handle_at(pos)
                if handle:
                    self.crop_drag_handle = handle
                    self.crop_drag_start_pos = pos
                    self.crop_drag_start_rect = QRect(self.crop_rect)
                return

            tool = self.toolbar_ref.current_tool
            if not tool:
                return

            was_editing = bool(getattr(self, 'inline_editor', None) and self.inline_editor.isVisible())
            had_text = bool(
                was_editing and (
                    self.inline_editor.text_edit.toPlainText().strip()
                    or self.inline_editor.active_edit_ref is not None
                )
            )
            self.commit_text_edit()

            ob = self.options_bar
            if tool == 'ERASE':
                self._eraser_state_saved = False
                if self.erase_annotation(pos):
                    self.update()
            elif tool == 'CROP':
                pass
            elif tool == 'OCR':
                self.is_drawing = True
                self.ocr_start = pos
                self.ocr_rect = QRect(pos, pos)
            elif tool == 'TEXT':
                mode = self._handle_text_tool_press(pos, event.globalPos(), was_editing, had_text)
                self.is_drawing = (mode == 'text_draw')
            else:
                res = self._begin_annotation_edit(
                    tool, pos,
                    default_color=self.toolbar_ref.current_color,
                    default_thickness=getattr(self.toolbar_ref, 'current_thickness', 3),
                    options_bar=ob,
                    step_num=getattr(self.toolbar_ref, 'current_step_num', 1),
                )
                if isinstance(res, tuple) and res[0] == 'step':
                    next_num = res[1]
                    self.toolbar_ref.current_step_num = next_num
                    if ob is not None:
                        ob.set_step_num(next_num)
                    self._notify_editor_state_changed()
                    self.update()
                elif res == 'drawing':
                    self.is_drawing = True
                    self.drag_mode = None
        except Exception as e:
            logging.error("EditableImageLabel mousePressEvent 出错: %s", e, exc_info=True)

    def mouseMoveEvent(self, event):
        try:
            pos = event.pos()

            # 裁剪模式优先处理
            if self.crop_mode:
                if self.crop_drag_handle:
                    self._update_crop_rect(pos)
                    self.update()
                else:
                    self.update_cursor_for_tool(pos)
                return

            self.update_cursor_for_tool(pos)

            tool = getattr(self.toolbar_ref, 'current_tool', None)
            if tool == 'ERASE' and (event.buttons() & Qt.LeftButton):
                if self.erase_annotation(pos):
                    self.update()
                return

            if tool == 'TEXT' and self.drag_mode == 'text_move':
                delta = event.globalPos() - self._text_move_start_global
                if delta.manhattanLength() >= 3:
                    self._text_move_dragged = True
                self.inline_editor.move_frame_to(self._text_move_start_frame.topLeft() + delta)

            elif self.is_drawing:
                if tool == 'OCR':
                    rect = QRect(self.ocr_start, pos).normalized()
                    self.ocr_rect = rect.intersected(self.base_pixmap.rect())
                    self.update()
                elif tool == 'TEXT' and self.drag_mode == 'text_draw':
                    self.text_rect = QRect(self.text_start, pos).normalized().intersected(self.rect())
                    self.update()
                elif self._update_active_temp_edit(pos, bool(event.modifiers() & Qt.ShiftModifier)):
                    self.update()
        except Exception as e:
            logging.error("EditableImageLabel mouseMoveEvent 出错: %s", e, exc_info=True)

    def mouseReleaseEvent(self, event):
        try:
            # 裁剪模式：释放手柄
            if self.crop_mode:
                self.crop_drag_handle = None
                return

            if self.toolbar_ref.current_tool == 'ERASE':
                self._eraser_state_saved = False
                return

            if self.toolbar_ref.current_tool == 'TEXT' and self.drag_mode == 'text_move':
                self._handle_text_tool_release(event.globalPos())
                return

            if self.is_drawing:
                if self.toolbar_ref.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                    if self.ocr_rect.width() > 10 and self.ocr_rect.height() > 10:
                        src_pm = self.toolbar_ref.get_final_static_image() if hasattr(self.toolbar_ref, 'get_final_static_image') else self.base_pixmap
                        sub_pixmap = src_pm.copy(self.ocr_rect)
                        self.toolbar_ref.process_ocr(sub_pixmap)
                    self.ocr_rect = None
                    self.update()

                elif self.toolbar_ref.current_tool == 'TEXT' and self.drag_mode == 'text_draw':
                    self._handle_text_tool_release(event.globalPos())

                if self.edits and self.edits[-1].get('temp'):
                    self._finish_active_temp_edit()
                    self._notify_editor_state_changed()
                    self.update()
                self.is_drawing = False
                self.drag_mode = None
        except Exception as e:
            logging.error("EditableImageLabel mouseReleaseEvent 出错: %s", e, exc_info=True)

    def enter_crop_mode(self):
        """进入裁剪模式，显示默认裁剪框"""
        self.crop_mode = True
        margin_x = self.base_pixmap.width() // 10
        margin_y = self.base_pixmap.height() // 10
        self.crop_rect = QRect(margin_x, margin_y,
                               self.base_pixmap.width() - 2 * margin_x,
                               self.base_pixmap.height() - 2 * margin_y)
        self.crop_ratio = None
        self.crop_drag_handle = None
        self.setCursor(Qt.SizeAllCursor)
        if hasattr(self.toolbar_ref, 'crop_toolbar') and self.toolbar_ref.crop_toolbar:
            self.toolbar_ref.crop_toolbar.set_ratio(None)
            self.toolbar_ref.crop_toolbar.update_size(self.crop_rect.width(), self.crop_rect.height())
        self.update()

    def exit_crop_mode(self):
        """退出裁剪模式"""
        self.crop_mode = False
        self.crop_rect = None
        self.crop_drag_handle = None
        self.update_cursor_for_tool()
        self.update()

    def apply_crop(self):
        """应用裁剪"""
        if not self.crop_rect or self.crop_rect.width() < CROP_MIN_SIZE or self.crop_rect.height() < CROP_MIN_SIZE:
            return
        valid_rect = self.crop_rect.intersected(self.base_pixmap.rect())
        if valid_rect.isEmpty():
            return
        self.save_state()
        self.base_pixmap = self.base_pixmap.copy(valid_rect)
        offset = valid_rect.topLeft()
        translate_edits(self.edits, -offset)
        self.setPixmap(self.base_pixmap)
        self.resize(self.base_pixmap.size())
        if hasattr(self.toolbar_ref, '_sync_dim_badge'):
            self.toolbar_ref._sync_dim_badge()
        self._notify_editor_state_changed()
        self.exit_crop_mode()

    def set_crop_ratio(self, ratio):
        """设置裁剪比例约束，ratio=None 自由，ratio=(w,h) 固定比例"""
        self.crop_ratio = ratio
        if ratio and self.crop_rect:
            rw, rh = ratio
            target_ratio = rw / rh
            cx = self.crop_rect.center().x()
            cy = self.crop_rect.center().y()
            new_w = self.crop_rect.width()
            new_h = int(new_w / target_ratio)
            pm = self.base_pixmap
            if cy - new_h // 2 < 0 or cy + new_h // 2 > pm.height():
                new_h = self.crop_rect.height()
                new_w = int(new_h * target_ratio)
            new_x = cx - new_w // 2
            new_y = cy - new_h // 2
            new_rect = QRect(new_x, new_y, new_w, new_h).intersected(pm.rect())
            if new_rect.width() >= CROP_MIN_SIZE and new_rect.height() >= CROP_MIN_SIZE:
                self.crop_rect = new_rect
                if hasattr(self.toolbar_ref, 'crop_toolbar') and self.toolbar_ref.crop_toolbar:
                    self.toolbar_ref.crop_toolbar.update_size(self.crop_rect.width(), self.crop_rect.height())
                self.update()

    def _update_crop_rect(self, pos):
        """根据拖动手柄更新裁剪框"""
        new_rect = self._compute_updated_crop_rect(pos, self.base_pixmap.rect())
        if new_rect is not None:
            self.crop_rect = new_rect
            if hasattr(self.toolbar_ref, 'crop_toolbar') and self.toolbar_ref.crop_toolbar:
                self.toolbar_ref.crop_toolbar.update_size(self.crop_rect.width(), self.crop_rect.height())

class CropToolbar(FrostedBarWidget):
    apply_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    ratio_changed = pyqtSignal(object)  # None or (w, h)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setStyleSheet("""
            QWidget#cropToolbar {
                background: transparent;
            }
        """)
        self.setObjectName("cropToolbar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.SHADOW_PAD + 8, self.SHADOW_PAD + 4, self.SHADOW_PAD + 8, self.SHADOW_PAD + 4)
        layout.setSpacing(4)

        self.size_label = QLabel("0 × 0", self)
        self.size_label.setStyleSheet(
            f"color: #7A5110; font-family: {CODEX_MONO_FAMILY}; font-size: 11px; "
            f"font-weight: 600; padding: 0 4px; border: none; background: transparent;"
        )
        layout.addWidget(self.size_label)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.VLine)
        sep0.setStyleSheet("color: rgba(142, 142, 147, 0.38);")
        layout.addWidget(sep0)

        self.ratio_buttons = {}
        for name, ratio in CROP_RATIO_PRESETS:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.78), stop:1 rgba(248, 245, 239, 0.62)); "
                "color: #1D1D1F; border: 1px solid rgba(142, 142, 147, 0.45); border-radius: 5px; padding: 2px 7px; font-size: 11px; }"
                "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.96), stop:1 rgba(250, 244, 232, 0.88)); "
                "color: #7A5110; border: 1.2px solid rgba(182, 134, 58, 0.82); }"
                "QPushButton:checked { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(250, 241, 222, 0.95), stop:1 rgba(240, 224, 190, 0.88)); "
                "color: #7A5110; font-weight: 600; border: 1.5px solid #B6863A; }"
            )
            btn.clicked.connect(lambda checked, r=ratio: self._on_ratio_btn(r))
            layout.addWidget(btn)
            self.ratio_buttons[name] = (btn, ratio)

        # 默认选中 Free
        self.ratio_buttons["Free"][0].setChecked(True)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: rgba(148, 163, 184, 0.4);")
        layout.addWidget(sep)

        apply_btn = QPushButton("✓ 确定", self)
        apply_btn.setFocusPolicy(Qt.NoFocus)
        apply_btn.setFixedHeight(28)
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(
            "QPushButton { background: #1f883d; color: white; border: none; "
            "border-radius: 5px; font-weight: 600; font-size: 12px; padding: 2px 10px; }"
            "QPushButton:hover { background: #1a7f37; }"
        )
        apply_btn.clicked.connect(self.apply_clicked.emit)
        layout.addWidget(apply_btn)

        cancel_btn = QPushButton("✕ 取消", self)
        cancel_btn.setFocusPolicy(Qt.NoFocus)
        cancel_btn.setFixedHeight(28)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            "QPushButton { background: rgba(255, 240, 238, 0.85); color: #cf222e; border: 1px solid rgba(255, 190, 190, 0.85); "
            "border-radius: 5px; font-weight: 400; font-size: 12px; padding: 2px 8px; }"
            "QPushButton:hover { background: rgba(255, 235, 233, 0.95); color: #a40e26; }"
        )
        cancel_btn.clicked.connect(self.cancel_clicked.emit)
        layout.addWidget(cancel_btn)

    def update_size(self, w, h):
        self.size_label.setText(f"{int(w)} × {int(h)}")

    def _on_ratio_btn(self, ratio):
        # 取消其他按钮的选中状态
        for name, (btn, r) in self.ratio_buttons.items():
            btn.setChecked(r == ratio)
        self.ratio_changed.emit(ratio)

    def set_ratio(self, ratio):
        for name, (btn, r) in self.ratio_buttons.items():
            btn.setChecked(r == ratio)

    def keyPressEvent(self, event):
        p = self.parent()
        if p is not None and hasattr(p, 'keyPressEvent'):
            p.keyPressEvent(event)
            return
        super().keyPressEvent(event)

class EditorCropControlsWidget(QWidget):
    """嵌入图片编辑与预览二级工具栏的裁剪控制面板（无独立悬浮窗遮挡画布）。"""
    apply_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    ratio_changed = pyqtSignal(object)  # None or (w, h)

    def __init__(self, parent=None):
        super().__init__(parent)
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        lbl = QLabel("裁剪尺寸:", self)
        lbl.setStyleSheet("color: #3F3F46; font-size: 11.5px; background: transparent;")
        l.addWidget(lbl)

        self.size_label = QLabel("0 × 0 px", self)
        self.size_label.setStyleSheet(
            f"background-color: #FFFFFF; color: #52525B; border: 1px solid #E4E4E7; "
            f"border-radius: 5px; padding: 2px 7px; font-family: {CODEX_MONO_FAMILY}; "
            f"font-size: 11px; font-weight: 600;"
        )
        l.addWidget(self.size_label)

        sep0 = QFrame(self)
        sep0.setFixedSize(1, 16)
        sep0.setStyleSheet("background: #E4E4E7; border: none; margin: 0 2px;")
        l.addWidget(sep0)

        lbl_ratio = QLabel("比例:", self)
        lbl_ratio.setStyleSheet("color: #3F3F46; font-size: 11.5px; background: transparent;")
        l.addWidget(lbl_ratio)

        self.ratio_buttons = {}
        for name, ratio in CROP_RATIO_PRESETS:
            btn = QPushButton(name, self)
            btn.setObjectName("subSegBtn")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedHeight(24)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, r=ratio: self._on_ratio_btn(r))
            l.addWidget(btn)
            self.ratio_buttons[name] = (btn, ratio)

        self.ratio_buttons["Free"][0].setChecked(True)

        l.addStretch(1)

        self.btn_apply = QPushButton("✓ 应用裁剪", self)
        self.btn_apply.setFocusPolicy(Qt.NoFocus)
        self.btn_apply.setAutoDefault(False)
        self.btn_apply.setDefault(False)
        self.btn_apply.setFixedHeight(24)
        self.btn_apply.setCursor(Qt.PointingHandCursor)
        self.btn_apply.setStyleSheet(
            "QPushButton { background: #1f883d; color: #FFFFFF; border: none; "
            "border-radius: 6px; font-weight: 600; font-size: 11.5px; padding: 2px 10px; }"
            "QPushButton:hover { background: #1a7f37; }"
        )
        self.btn_apply.clicked.connect(self.apply_clicked.emit)
        l.addWidget(self.btn_apply)

        self.btn_cancel = QPushButton("✕ 取消", self)
        self.btn_cancel.setObjectName("codexSecondaryBtn")
        self.btn_cancel.setFocusPolicy(Qt.NoFocus)
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.setDefault(False)
        self.btn_cancel.setFixedHeight(24)
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setStyleSheet(
            "QPushButton { background: #FFFFFF; color: #DC2626; border: 1px solid #FECACA; "
            "border-radius: 6px; font-size: 11.5px; padding: 2px 9px; }"
            "QPushButton:hover { background: #FEF2F2; border-color: #FCA5A5; }"
        )
        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)
        l.addWidget(self.btn_cancel)

    def update_size(self, w, h):
        self.size_label.setText(f"{int(w)} × {int(h)} px")

    def _on_ratio_btn(self, ratio):
        self.set_ratio(ratio)
        self.ratio_changed.emit(ratio)

    def set_ratio(self, ratio):
        for _name, (btn, r) in self.ratio_buttons.items():
            btn.setChecked(r == ratio)


class EditorToolOptionsBar(_ToolOptionsPanelsMixin, QWidget):
    """图片编辑与预览窗口的统一二级菜单栏（Codex 风格，覆盖所有编辑与预览工具）。"""
    _opt_theme = "codex"
    _opt_panel_stretch = True
    _opt_divider_qss = "background: #E4E4E7; border: none; margin: 0 2px;"
    _opt_rotate_theme = "codex"
    _opt_text_bold_w = 28

    options_changed = pyqtSignal()
    eraser_undo_clicked = pyqtSignal()
    eraser_clear_clicked = pyqtSignal()
    ocr_full_clicked = pyqtSignal()
    ocr_translate_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editorSubBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#editorSubBar {
                background-color: #FAFAFB;
                border-bottom: 1px solid #E4E4E7;
            }
            QWidget#editorSubBar QLabel {
                color: #3F3F46;
                font-size: 11.5px;
                background: transparent;
            }
            QWidget#editorSubBar QLabel#subHintLabel {
                color: #71717A;
                font-size: 11px;
                background: transparent;
            }
            QWidget#editorSubBar QPushButton#subSegBtn {
                background-color: #FFFFFF;
                color: #27272A;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11.5px;
                font-weight: 400;
                min-height: 20px;
            }
            QWidget#editorSubBar QPushButton#subSegBtn:hover {
                background-color: #F4F4F5;
                border-color: #D4D4D8;
                color: #0D0D0D;
            }
            QWidget#editorSubBar QPushButton#subSegBtn:checked {
                background-color: #0D0D0D;
                color: #FFFFFF;
                border-color: #0D0D0D;
                font-weight: 600;
            }
            QWidget#editorSubBar QPushButton#subSegBtn:disabled {
                background-color: #FAFAFA;
                color: #A1A1AA;
                border-color: #ECECEF;
            }
            QWidget#editorSubBar QSpinBox {
                background-color: #FFFFFF;
                color: #18181B;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 1px 4px;
                font-size: 11.5px;
                height: 22px;
            }
            QWidget#editorSubBar QSpinBox:focus {
                border: 1.5px solid #0D0D0D;
            }
            QWidget#editorSubBar QComboBox {
                background-color: #FFFFFF;
                color: #18181B;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 1px 22px 1px 8px;
                font-size: 11.5px;
                min-height: 22px;
                height: 24px;
            }
            QWidget#editorSubBar QPushButton#editorToolBtn {
                padding: 3px 8px;
                font-size: 11.5px;
                min-height: 18px;
            }
            QWidget#editorSubBar QPushButton#codexPrimaryBtn,
            QWidget#editorSubBar QPushButton#codexSecondaryBtn,
            QWidget#editorSubBar QPushButton#codexDangerBtn {
                padding: 2px 10px;
                font-size: 11.5px;
                min-height: 20px;
            }
        """)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 6)
        root.setSpacing(0)

        self.stack = QStackedWidget(self)
        root.addWidget(self.stack)

        self.tool_indices = {}
        self.current_tool_name = None

        self._init_rect_panel()
        self._init_ellipse_panel()
        self._init_line_panel()
        self._init_arrow_panel(tool_name='ARROW', none_label="无箭头 ━", show_hint=True)
        self._init_pen_panel(tool_name='PEN', poly_label="自由曲线 〰", hint_text="💡 自由涂鸦或切换“直线”绘制单条直线")
        self._init_marker_panel(tool_name='HIGHLIGHTER')
        self._init_step_panel()
        self._init_text_panel(tool_name='TEXT')
        self._init_mosaic_blur_panel(tool_name='BLUR')
        self._init_eraser_panel()
        self._init_crop_panel()
        self._init_rotate_panel()
        self._init_ocr_panel()

    def _init_rect_panel(self):
        refs = self._build_shape_panel('RECTANGLE', with_kind_toggle=False, with_radius=True)
        self.spin_rect_width = refs['spin_w']
        self.spin_rect_radius = refs['spin_r']
        self.rect_stroke_palette = refs['stroke_pal']
        self.rect_fill_palette = refs['fill_pal']
        self._rect_refs = refs

    def get_rect_options(self):
        return self._read_shape_options(self._rect_refs, default_kind='rect')

    def _init_ellipse_panel(self):
        refs = self._build_shape_panel('ELLIPSE', with_kind_toggle=False, with_radius=False, hint_text="💡 Shift 画正圆")
        self.spin_ellipse_width = refs['spin_w']
        self.ellipse_stroke_palette = refs['stroke_pal']
        self.ellipse_fill_palette = refs['fill_pal']
        self._ellipse_refs = refs

    def get_ellipse_options(self):
        return self._read_shape_options(self._ellipse_refs, default_kind='ellipse')

    def _init_line_panel(self):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        self.btn_line_solid = self._make_opt_btn("实线 ━", checked=True)
        self.btn_line_dashed = self._make_opt_btn("虚线 ┄")
        self.btn_line_solid.clicked.connect(lambda: self._set_line_style('solid'))
        self.btn_line_dashed.clicked.connect(lambda: self._set_line_style('dashed'))
        l.addWidget(self.btn_line_solid)
        l.addWidget(self.btn_line_dashed)

        self._add_divider(l)

        l.addWidget(QLabel("宽度:", panel))
        self.spin_line_width = self._make_opt_spin(1, 24, 3, width=44)
        l.addWidget(self.spin_line_width)

        self._add_divider(l)

        l.addWidget(QLabel("颜色:", panel))
        self.line_palette = self._make_opt_palette(default_color="#FF3B30")
        l.addWidget(self.line_palette)

        l.addWidget(self._make_opt_hint("💡 按住 Shift 对齐 45°"))
        self._register_panel('LINE', panel, l)

    def _set_line_style(self, style):
        self.btn_line_solid.setChecked(style == 'solid')
        self.btn_line_dashed.setChecked(style == 'dashed')
        self.options_changed.emit()

    def get_line_options(self):
        return {
            'lineStyle': 'dashed' if self.btn_line_dashed.isChecked() else 'solid',
            'thickness': self.spin_line_width.value(),
            'color': self.line_palette.current_color,
        }

    def _init_step_panel(self):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(5)

        l.addWidget(QLabel("下一序号:", panel))
        self.spin_step_num = self._make_opt_spin(1, 999, 1, width=50)
        l.addWidget(self.spin_step_num)

        self.btn_step_reset = self._make_opt_btn("重置为 1", checkable=False, tip="将下一个序号重置为 1")
        self.btn_step_reset.clicked.connect(self._reset_step_num)
        l.addWidget(self.btn_step_reset)

        self._add_divider(l)

        l.addWidget(QLabel("尺寸:", panel))
        self.spin_step_radius = self._make_opt_spin(10, 36, 14, width=46)
        l.addWidget(self.spin_step_radius)

        self._add_divider(l)

        l.addWidget(QLabel("颜色:", panel))
        self.step_palette = self._make_opt_palette(default_color="#FF3B30")
        l.addWidget(self.step_palette)

        l.addWidget(self._make_opt_hint("💡 点击画布放置递增序号"))
        self._register_panel('STEP', panel, l)

    def _reset_step_num(self):
        self.set_step_num(1)
        self.options_changed.emit()

    def set_step_num(self, num):
        val = max(1, min(999, int(num)))
        prev = self.spin_step_num.blockSignals(True)
        try:
            self.spin_step_num.setValue(val)
        finally:
            self.spin_step_num.blockSignals(prev)

    def get_step_options(self):
        return {
            'num': self.spin_step_num.value(),
            'radius': self.spin_step_radius.value(),
            'color': self.step_palette.current_color,
        }

    def _init_eraser_panel(self):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        l.addWidget(self._make_opt_hint("🧹 点击或按住拖动经过已有标注即可整项擦除（单次拖动记为一步撤销）"))
        l.addStretch(1)

        self.btn_eraser_undo = QPushButton("撤销上一步", panel)
        self.btn_eraser_undo.setObjectName("codexSecondaryBtn")
        self.btn_eraser_undo.setFocusPolicy(Qt.NoFocus)
        self.btn_eraser_undo.setAutoDefault(False)
        self.btn_eraser_undo.setDefault(False)
        self.btn_eraser_undo.setFixedHeight(24)
        self.btn_eraser_undo.setCursor(Qt.PointingHandCursor)
        self.btn_eraser_undo.clicked.connect(self.eraser_undo_clicked.emit)
        l.addWidget(self.btn_eraser_undo)

        self.btn_eraser_clear = QPushButton("清空全部标注", panel)
        self.btn_eraser_clear.setObjectName("codexDangerBtn")
        self.btn_eraser_clear.setFocusPolicy(Qt.NoFocus)
        self.btn_eraser_clear.setAutoDefault(False)
        self.btn_eraser_clear.setDefault(False)
        self.btn_eraser_clear.setFixedHeight(24)
        self.btn_eraser_clear.setCursor(Qt.PointingHandCursor)
        self.btn_eraser_clear.clicked.connect(self.eraser_clear_clicked.emit)
        l.addWidget(self.btn_eraser_clear)

        self.tool_indices['ERASE'] = self.stack.addWidget(panel)

    def _init_crop_panel(self):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        self.crop_bar = EditorCropControlsWidget(panel)
        l.addWidget(self.crop_bar)
        self.tool_indices['CROP'] = self.stack.addWidget(panel)

    def _init_ocr_panel(self):
        panel = QWidget(self)
        l = QHBoxLayout(panel)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        l.addWidget(self._make_opt_hint("🔤 在画布上拖选区域进行局部识别，或直接处理整图："))
        l.addStretch(1)

        self.btn_ocr_full = QPushButton("识别当前整图", panel)
        self.btn_ocr_full.setObjectName("codexSecondaryBtn")
        self.btn_ocr_full.setFocusPolicy(Qt.NoFocus)
        self.btn_ocr_full.setAutoDefault(False)
        self.btn_ocr_full.setDefault(False)
        self.btn_ocr_full.setFixedHeight(24)
        self.btn_ocr_full.setCursor(Qt.PointingHandCursor)
        self.btn_ocr_full.clicked.connect(self.ocr_full_clicked.emit)
        l.addWidget(self.btn_ocr_full)

        self.btn_ocr_translate = QPushButton("一键翻译整图", panel)
        self.btn_ocr_translate.setObjectName("codexPrimaryBtn")
        self.btn_ocr_translate.setFocusPolicy(Qt.NoFocus)
        self.btn_ocr_translate.setAutoDefault(False)
        self.btn_ocr_translate.setDefault(False)
        self.btn_ocr_translate.setFixedHeight(24)
        self.btn_ocr_translate.setCursor(Qt.PointingHandCursor)
        self.btn_ocr_translate.clicked.connect(self.ocr_translate_clicked.emit)
        l.addWidget(self.btn_ocr_translate)

        self.tool_indices['OCR'] = self.stack.addWidget(panel)

    def _palette_for_tool(self, tool_name):
        if tool_name == 'RECTANGLE':
            return self.rect_fill_palette if self._rect_refs['btn_solid'].isChecked() else self.rect_stroke_palette
        if tool_name == 'ELLIPSE':
            return self.ellipse_fill_palette if self._ellipse_refs['btn_solid'].isChecked() else self.ellipse_stroke_palette
        mapping = {
            'LINE': self.line_palette,
            'ARROW': self.arrow_palette,
            'PEN': self.pen_palette,
            'HIGHLIGHTER': self.marker_palette,
            'STEP': self.step_palette,
            'TEXT': self.text_palette,
        }
        return mapping.get(tool_name)

    def get_active_color(self, tool_name=None):
        pal = self._palette_for_tool(tool_name or self.current_tool_name)
        return QColor(pal.current_color) if pal is not None else None

    def set_active_color(self, color, tool_name=None, emit=False):
        target = tool_name or self.current_tool_name
        if target is None:
            for pal in (
                self.rect_stroke_palette, self.ellipse_stroke_palette,
                self.line_palette, self.arrow_palette, self.pen_palette,
                self.step_palette, self.text_palette,
            ):
                pal.set_color(color, emit=False)
            if emit:
                self.options_changed.emit()
            return True
        pal = self._palette_for_tool(target)
        if pal is not None:
            pal.set_color(color, emit=emit)
            return True
        return False

    def sync_action_states(self, can_undo, has_edits, ocr_busy=False):
        self.btn_eraser_undo.setEnabled(bool(can_undo))
        self.btn_eraser_clear.setEnabled(bool(has_edits))
        self.btn_ocr_full.setEnabled(not ocr_busy)
        self.btn_ocr_translate.setEnabled(not ocr_busy)
        self.btn_ocr_full.setText("识别中…" if ocr_busy else "识别当前整图")


class ImageEditorDialog(QDialog):
    _active_editors = []

    @classmethod
    def get_open_editors(cls):
        alive = []
        cleaned = []
        for ed in list(cls._active_editors):
            try:
                _ = ed.isVisible()
                cleaned.append(ed)
                if ed.isVisible() or getattr(ed, "_temp_hidden_for_capture", False):
                    alive.append(ed)
            except RuntimeError:
                pass
        cls._active_editors[:] = cleaned
        return alive

    def __init__(self, image_data, main_app_ref=None):
        super().__init__()
        self.setWindowTitle("图片编辑与预览")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.StrongFocus)
        self.main_app_ref = main_app_ref
        self._temp_hidden_for_capture = False
        self._editor_closed = False
        if self not in ImageEditorDialog._active_editors:
            ImageEditorDialog._active_editors.append(self)
        
        if isinstance(image_data, QPixmap):
            self.base_pixmap = image_data
        else:
            rgb_image = cv2.cvtColor(image_data, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            self.base_pixmap = QPixmap.fromImage(QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888))

        self.resize(
            max(1000, min(1280, self.base_pixmap.width() + 72)),
            max(580, min(880, self.base_pixmap.height() + 160)),
        )
        self.setMinimumSize(960, 480)
        self.current_tool = None
        self.current_step_num = 1
        self.current_thickness = 3
        self.current_color = QColor("#FF3B30")
        self._ocr_busy = False
        self._ocr_workers = []

        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + """
            QDialog {
                background-color: #F4F4F5;
            }
            QWidget#editorToolbar {
                background-color: #F7F7F8;
                border-bottom: 1px solid #E4E4E7;
            }
            QWidget#editorStatusBar {
                background-color: #F7F7F8;
                border-top: 1px solid #E4E4E7;
            }
            QLabel#editorStatusHint {
                color: #71717A;
                font-size: 11.5px;
                background: transparent;
            }
            QFrame#editorSep {
                background-color: #E4E4E7;
                max-width: 1px;
                min-width: 1px;
                border: none;
                margin: 2px 3px;
            }
            QLabel#editorDimBadge {
                background-color: #FFFFFF;
                color: #52525B;
                border: 1px solid #E4E4E7;
                border-radius: 5px;
                padding: 2px 8px;
                font-family: """ + CODEX_MONO_FAMILY + """;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#editorToolBtn {
                background-color: #FFFFFF;
                color: #27272A;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 400;
                min-height: 20px;
            }
            QPushButton#editorToolBtn:hover {
                background-color: #F4F4F5;
                border-color: #D4D4D8;
                color: #0D0D0D;
            }
            QPushButton#editorToolBtn:checked {
                background-color: #0D0D0D;
                color: #FFFFFF;
                border-color: #0D0D0D;
            }
            QPushButton#editorToolBtn:checked:hover {
                background-color: #27272A;
                border-color: #27272A;
            }
            QPushButton#editorToolBtn:disabled {
                background-color: #FAFAFA;
                color: #A1A1AA;
                border-color: #ECECEF;
            }
            QWidget#editorToolbar QPushButton#codexPrimaryBtn,
            QWidget#editorToolbar QPushButton#codexSecondaryBtn,
            QWidget#editorToolbar QPushButton#codexGhostBtn {
                padding: 4px 9px;
                font-size: 12px;
                min-height: 20px;
            }
            QPushButton#editorColorBtn {
                background-color: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
                padding: 0px;
            }
            QPushButton#editorColorBtn:hover {
                background-color: #F4F4F5;
                border-color: #D4D4D8;
            }
            QScrollArea#editorCanvasArea {
                background-color: #F4F4F5;
                border: none;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.setup_toolbar(layout)

        self.sub_bar = EditorToolOptionsBar(self)
        self.sub_bar.hide()
        layout.addWidget(self.sub_bar)
        self.rotate_controls = self.sub_bar.rotate_controls
        self.crop_toolbar = self.sub_bar.crop_bar

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("editorCanvasArea")
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.img_label = EditableImageLabel(self.base_pixmap, self)
        self.scroll_area.setWidget(self.img_label)
        layout.addWidget(self.scroll_area, 1)

        self.sub_bar.options_changed.connect(self.on_tool_options_changed)
        self.sub_bar.eraser_undo_clicked.connect(self.undo_edit)
        self.sub_bar.eraser_clear_clicked.connect(self.clear_all_edits)
        self.sub_bar.ocr_full_clicked.connect(self.ocr_full_image)
        self.sub_bar.ocr_translate_clicked.connect(self.translate_full_image)

        self.rotate_controls.op_triggered.connect(self.img_label.apply_orthogonal_transform)
        self.rotate_controls.angle_changed.connect(self.img_label.preview_rotation_angle)
        self.rotate_controls.apply_clicked.connect(self.img_label.commit_rotation_preview)
        self.rotate_controls.reset_clicked.connect(self.img_label.cancel_rotation_preview)

        self.crop_toolbar.apply_clicked.connect(self.apply_crop)
        self.crop_toolbar.cancel_clicked.connect(self.cancel_crop)
        self.crop_toolbar.ratio_changed.connect(self.img_label.set_crop_ratio)

        # 底部状态栏（快捷提示 + 实时分辨率）
        status_bar = QWidget()
        status_bar.setObjectName("editorStatusBar")
        sb_lay = QHBoxLayout(status_bar)
        sb_lay.setContentsMargins(14, 6, 14, 6)
        sb_lay.setSpacing(10)
        hint_lbl = QLabel("Ctrl+C 复制并关闭   ·   Ctrl+Z / Ctrl+Y 撤销重做   ·   F2 文字识别   ·   Esc 退出")
        hint_lbl.setObjectName("editorStatusHint")
        sb_lay.addWidget(hint_lbl)
        sb_lay.addStretch()
        self.dim_badge = QLabel(f"{self.base_pixmap.width()} × {self.base_pixmap.height()} px")
        self.dim_badge.setObjectName("editorDimBadge")
        sb_lay.addWidget(self.dim_badge)
        layout.addWidget(status_bar)

        self._update_color_button()
        self._sync_undo_redo_buttons()
        self._setup_shortcuts()
        self.setMinimumSize(max(960, self.toolbar.sizeHint().width() + 4), 480)
        if self.width() < self.minimumWidth():
            self.resize(self.minimumWidth(), self.height())

    def _setup_shortcuts(self):
        sc_undo = QShortcut(QKeySequence("Ctrl+Z"), self)
        sc_undo.setContext(Qt.WidgetWithChildrenShortcut)
        sc_undo.activated.connect(self._on_shortcut_undo)

        sc_redo = QShortcut(QKeySequence("Ctrl+Y"), self)
        sc_redo.setContext(Qt.WidgetWithChildrenShortcut)
        sc_redo.activated.connect(self._on_shortcut_redo)

        sc_redo_alt = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        sc_redo_alt.setContext(Qt.WidgetWithChildrenShortcut)
        sc_redo_alt.activated.connect(self._on_shortcut_redo)

        sc_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        sc_copy.setContext(Qt.WidgetWithChildrenShortcut)
        sc_copy.activated.connect(self._on_shortcut_copy)

        sc_save = QShortcut(QKeySequence("Ctrl+S"), self)
        sc_save.setContext(Qt.WidgetWithChildrenShortcut)
        sc_save.activated.connect(self.save_image)

        self._shortcuts = [sc_undo, sc_redo, sc_redo_alt, sc_copy, sc_save]

    def _is_inline_text_editing(self):
        inline = getattr(self.img_label, 'inline_editor', None)
        if inline is None or not inline.isVisible():
            return False
        fw = QApplication.focusWidget()
        return bool(
            inline.text_edit.hasFocus()
            or fw is inline.text_edit
            or (fw is not None and fw.parent() is inline.text_edit)
        )

    def _on_shortcut_undo(self):
        if self._is_inline_text_editing():
            self.img_label.inline_editor.text_edit.undo()
            return
        self.undo_edit()

    def _on_shortcut_redo(self):
        if self._is_inline_text_editing():
            self.img_label.inline_editor.text_edit.redo()
            return
        self.redo_edit()

    def _on_shortcut_copy(self):
        if self._is_inline_text_editing():
            te = self.img_label.inline_editor.text_edit
            if te.textCursor().hasSelection():
                te.copy()
                return
        self.copy_to_clipboard()

    def _sync_dim_badge(self, pm=None):
        if pm is None:
            pm = getattr(self.img_label, "base_pixmap", None)
        if hasattr(self, "dim_badge") and pm is not None and not pm.isNull():
            self.dim_badge.setText(f"{pm.width()} × {pm.height()} px")

    def _sync_undo_redo_buttons(self):
        can_undo = bool(getattr(self.img_label, 'undo_stack', None))
        can_redo = bool(getattr(self.img_label, 'redo_stack', None))
        has_edits = bool(getattr(self.img_label, 'edits', None))
        if hasattr(self, 'btn_undo'):
            self.btn_undo.setEnabled(can_undo)
        if hasattr(self, 'btn_redo'):
            self.btn_redo.setEnabled(can_redo)
        if hasattr(self, 'sub_bar'):
            self.sub_bar.sync_action_states(can_undo, has_edits, ocr_busy=self._ocr_busy)

    def sync_step_num_from_edits(self):
        edits = getattr(self.img_label, 'edits', []) or []
        max_step = max((int(e.get('num', 0) or 0) for e in edits if e.get('type') == 'STEP'), default=0)
        next_num = max(1, max_step + 1)
        self.current_step_num = next_num
        if hasattr(self, 'sub_bar'):
            self.sub_bar.set_step_num(next_num)

    def undo_edit(self):
        self.img_label.undo()
        if hasattr(self, 'rotate_controls'):
            self.rotate_controls.set_angle(0)
        self._sync_dim_badge()
        self._sync_undo_redo_buttons()

    def redo_edit(self):
        self.img_label.redo()
        if hasattr(self, 'rotate_controls'):
            self.rotate_controls.set_angle(0)
        self._sync_dim_badge()
        self._sync_undo_redo_buttons()

    def clear_all_edits(self):
        self.img_label.clear_all_edits()
        self._sync_undo_redo_buttons()

    def exec_nonmodal(self):
        return exec_dialog_nonmodal(self)

    def _unregister_editor(self):
        if self in ImageEditorDialog._active_editors:
            ImageEditorDialog._active_editors.remove(self)

    def done(self, result):
        self._editor_closed = True
        self._temp_hidden_for_capture = False
        self._unregister_editor()
        super().done(result)

    def closeEvent(self, event):
        self._editor_closed = True
        self._temp_hidden_for_capture = False
        self._unregister_editor()
        super().closeEvent(event)

    @staticmethod
    def _make_editor_icon(name):
        icon = QIcon()
        icon.addPixmap(VectorIconFactory.create_pixmap(name, QColor("#27272A")), QIcon.Normal, QIcon.Off)
        icon.addPixmap(VectorIconFactory.create_pixmap(name, QColor("#FFFFFF")), QIcon.Normal, QIcon.On)
        icon.addPixmap(VectorIconFactory.create_pixmap(name, QColor("#A1A1AA")), QIcon.Disabled, QIcon.Off)
        return icon

    def _add_toolbar_sep(self, layout):
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setObjectName("editorSep")
        sep.setFixedHeight(18)
        layout.addWidget(sep)

    def setup_toolbar(self, parent_layout):
        self.toolbar = QWidget()
        self.toolbar.setObjectName("editorToolbar")
        layout = QHBoxLayout(self.toolbar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)

        self.btn_rect = QPushButton()
        self.btn_ellipse = QPushButton()
        self.btn_line = QPushButton()
        self.btn_arrow = QPushButton()
        self.btn_pen = QPushButton()
        self.btn_highlighter = QPushButton()
        self.btn_step = QPushButton("1")
        self.btn_text = QPushButton()
        self.btn_blur = QPushButton()
        self.btn_erase = QPushButton()
        self.btn_color = QPushButton()
        self.btn_undo = QPushButton()
        self.btn_redo = QPushButton()
        self.btn_crop = QPushButton("裁剪")
        self.btn_rotate = QPushButton("旋转")
        self.btn_ocr = QPushButton("识别")
        self.btn_history = QPushButton("历史")
        self.btn_settings = QPushButton("设置")
        self.btn_pin = QPushButton("贴图")
        self.btn_copy = QPushButton("复制")
        self.btn_save = QPushButton("保存")
        self.btn_close = QPushButton("关闭")

        draw_Specs = [
            (self.btn_rect, "rect", "矩形标注", lambda: self.set_tool('RECTANGLE', self.btn_rect)),
            (self.btn_ellipse, "ellipse", "圆形/椭圆标注", lambda: self.set_tool('ELLIPSE', self.btn_ellipse)),
            (self.btn_line, "line", "直线标注", lambda: self.set_tool('LINE', self.btn_line)),
            (self.btn_arrow, "arrow", "箭头标注", lambda: self.set_tool('ARROW', self.btn_arrow)),
            (self.btn_pen, "pen", "画笔工具", lambda: self.set_tool('PEN', self.btn_pen)),
            (self.btn_highlighter, "marker", "荧光笔", lambda: self.set_tool('HIGHLIGHTER', self.btn_highlighter)),
            (self.btn_step, None, "序号标注", lambda: self.set_tool('STEP', self.btn_step)),
            (self.btn_text, "text", "文字输入", lambda: self.set_tool('TEXT', self.btn_text)),
            (self.btn_blur, "mosaic_blur", "马赛克/模糊", lambda: self.set_tool('BLUR', self.btn_blur)),
            (self.btn_erase, "eraser", "橡皮擦", lambda: self.set_tool('ERASE', self.btn_erase)),
        ]

        for btn, icon_name, tip, slot in draw_Specs:
            btn.setObjectName("editorToolBtn")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedSize(27, 27)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            if icon_name:
                btn.setIcon(self._make_editor_icon(icon_name))
                btn.setIconSize(QSize(15, 15))
            else:
                btn.setStyleSheet(
                    f"QPushButton#editorToolBtn {{ font-family: {CODEX_MONO_FAMILY}; font-weight: 600; font-size: 13px; padding: 0; }}"
                )
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        self.btn_color.setObjectName("editorColorBtn")
        self.btn_color.setFocusPolicy(Qt.NoFocus)
        self.btn_color.setAutoDefault(False)
        self.btn_color.setDefault(False)
        self.btn_color.setFixedSize(27, 27)
        self.btn_color.setToolTip("选择画笔颜色")
        self.btn_color.setCursor(Qt.PointingHandCursor)
        self.btn_color.clicked.connect(self.pick_drawing_color)
        layout.addWidget(self.btn_color)

        self._add_toolbar_sep(layout)

        for btn, icon_name, tip, slot in [
            (self.btn_undo, "undo", "撤销 (Ctrl+Z)", self.undo_edit),
            (self.btn_redo, "redo", "重做 (Ctrl+Y)", self.redo_edit),
        ]:
            btn.setObjectName("editorToolBtn")
            btn.setCheckable(False)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedSize(27, 27)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(self._make_editor_icon(icon_name))
            btn.setIconSize(QSize(14, 14))
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        self._add_toolbar_sep(layout)

        util_Specs = [
            (self.btn_crop, "crop", True, "裁剪区域", lambda: self.set_tool('CROP', self.btn_crop)),
            (self.btn_rotate, "rotate", True, "旋转 / 翻转图像", lambda: self.set_tool('ROTATE', self.btn_rotate)),
            (self.btn_ocr, "ocr", True, "文字识别与翻译 (F2)", lambda: self.set_tool('OCR', self.btn_ocr)),
            (self.btn_history, "history", False, "截图历史记录", self.open_history),
            (self.btn_settings, None, False, "打开系统设置", self.open_settings),
        ]
        for btn, icon_name, checkable, tip, slot in util_Specs:
            btn.setObjectName("editorToolBtn")
            btn.setCheckable(checkable)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedHeight(27)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            if icon_name:
                btn.setIcon(self._make_editor_icon(icon_name))
                btn.setIconSize(QSize(13, 13))
            else:
                btn.setIcon(_create_codex_icon("general", "#27272A", 13))
                btn.setIconSize(QSize(13, 13))
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        layout.addStretch()

        self._add_toolbar_sep(layout)

        for btn, icon_name, obj_name, tip, slot in [
            (self.btn_pin, "pin", "codexSecondaryBtn", "固定到桌面贴图", self.pin_to_screen),
            (self.btn_save, "save", "codexSecondaryBtn", "保存图片到本地", self.save_image),
            (self.btn_copy, "copy", "codexPrimaryBtn", "复制到剪贴板 (Ctrl+C)", self.copy_to_clipboard),
            (self.btn_close, None, "codexGhostBtn", "关闭窗口 (Esc)", self.close),
        ]:
            btn.setObjectName(obj_name)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedHeight(27)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            if icon_name:
                c = "#FFFFFF" if obj_name == "codexPrimaryBtn" else "#27272A"
                ico = QIcon()
                ico.addPixmap(VectorIconFactory.create_pixmap(icon_name, QColor(c)), QIcon.Normal, QIcon.Off)
                btn.setIcon(ico)
                btn.setIconSize(QSize(13, 13))
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        parent_layout.addWidget(self.toolbar)

    def open_history(self):
        dlg = HistoryDialog(self, self.main_app_ref)
        dlg.exec_nonmodal()

    def open_settings(self):
        hotkey_mgr = getattr(self.main_app_ref, 'hotkey_listener', None)
        dlg = SettingsDialog(self, hotkey_mgr)
        if exec_dialog_nonmodal(dlg) == QDialog.Accepted and hotkey_mgr:
            hotkey_mgr.reload_hotkeys()

    def _sync_color_button_from_active_tool(self):
        if hasattr(self, 'sub_bar'):
            col = self.sub_bar.get_active_color(self.current_tool)
            if col is not None and col.isValid():
                self.current_color = col
                self._update_color_button()

    def on_tool_options_changed(self):
        self._sync_color_button_from_active_tool()
        if self.current_tool == 'STEP' and hasattr(self, 'sub_bar'):
            self.current_step_num = self.sub_bar.spin_step_num.value()
        if (
            self.current_tool == 'TEXT'
            and getattr(self.img_label, 'inline_editor', None)
            and self.img_label.inline_editor.isVisible()
        ):
            self.img_label.inline_editor.update_font_from_options(self.sub_bar.get_text_options())
        if hasattr(self.img_label, 'update_cursor_for_tool'):
            self.img_label.update_cursor_for_tool()

    def pick_drawing_color(self):
        color = QColorDialog.getColor(self.current_color, self, "选择画笔颜色")
        if color.isValid():
            self.current_color = color
            self._update_color_button()
            if hasattr(self, 'sub_bar'):
                self.sub_bar.set_active_color(color, self.current_tool, emit=True)

    def _update_color_button(self):
        dpr = 2
        pm = QPixmap(18 * dpr, 18 * dpr)
        pm.setDevicePixelRatio(float(dpr))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor("#D4D4D8"), 1.0))
        p.setBrush(self.current_color)
        p.drawRoundedRect(QRectF(1.5, 1.5, 15.0, 15.0), 4.0, 4.0)
        p.end()
        self.btn_color.setIcon(QIcon(pm))
        self.btn_color.setIconSize(QSize(18, 18))
        self.btn_color.setText("")

    def _set_ocr_busy(self, busy):
        self._ocr_busy = bool(busy)
        self.btn_ocr.setText("识别中…" if busy else "识别")
        self._sync_undo_redo_buttons()

    def _prepare_final_snapshot(self):
        if getattr(self.img_label, 'inline_editor', None) and self.img_label.inline_editor.isVisible():
            self.img_label.commit_text_edit()
        if getattr(self.img_label, 'has_pending_rotation_preview', None) and self.img_label.has_pending_rotation_preview():
            self.img_label.commit_rotation_preview()
            if hasattr(self, 'rotate_controls'):
                self.rotate_controls.set_angle(0)
            self._sync_dim_badge()

    def ocr_full_image(self):
        if self._ocr_busy:
            return
        self._prepare_final_snapshot()
        final_img = self.get_final_static_image()
        if final_img is not None and not final_img.isNull():
            self.process_ocr(final_img)

    def translate_full_image(self):
        if self._ocr_busy:
            return
        self._prepare_final_snapshot()
        final_img = self.get_final_static_image()
        if final_img is None or final_img.isNull():
            return
        self._set_ocr_busy(True)
        worker = OcrWorker(final_img.toImage(), use_ai=False, fast=True)
        self._ocr_workers.append(worker)
        state = {"doc": None}

        def _on_doc(doc):
            state["doc"] = doc

        def _on_finished(text):
            try:
                if worker in self._ocr_workers:
                    self._ocr_workers.remove(worker)
            except Exception:
                pass
            if getattr(self, "_editor_closed", False):
                return
            self._set_ocr_busy(False)
            doc = state["doc"] or getattr(worker, "ocr_doc", None)
            if doc is not None and not doc.is_empty():
                txt = doc.text.strip()
                if not txt:
                    QMessageBox.information(self, "一键翻译", "未识别到可翻译的文字。")
                    return
                target = resolve_ocr_target_lang(txt)
                use_ai = bool(load_settings().get("use_ai_translation", False))
                mode = str(load_settings().get("translation_display_mode", "dialog") or "dialog")
                win = TranslationResultWindow(
                    final_img, doc, target, use_ai,
                    mode="overlay" if mode == "overlay" else "compare",
                    parent=self,
                )
                exec_dialog_nonmodal(win)
                return
            if text and text.strip():
                codes = detect_codes_in_image(final_img)
                dlg = OcrResultDialog(
                    text, self, detected_codes=codes,
                    source_image=final_img.toImage(),
                )
                exec_dialog_nonmodal(dlg)
            else:
                QMessageBox.information(self, "一键翻译", "未能识别到文字，请重试。")

        worker.doc_ready.connect(_on_doc)
        worker.finished.connect(_on_finished)
        worker.start()

    def process_ocr(self, pixmap):
        if self._ocr_busy:
            return
        self._set_ocr_busy(True)
        ocr_img = pixmap.toImage()
        self.ocr_worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))
        self.ocr_worker.finished.connect(self.show_ocr_result)
        self.ocr_worker.start()

    def show_ocr_result(self, text):
        if getattr(self, "_editor_closed", False):
            return
        self._set_ocr_busy(False)
        self.btn_ocr.setChecked(self.current_tool == 'OCR')
        codes = getattr(self.ocr_worker, "detected_codes", []) if hasattr(self, "ocr_worker") and self.ocr_worker else []
        doc = getattr(self.ocr_worker, "ocr_doc", None) if hasattr(self, "ocr_worker") and self.ocr_worker else None
        src_img = getattr(self.ocr_worker, "image", None) if hasattr(self, "ocr_worker") and self.ocr_worker else None
        warn = getattr(self.ocr_worker, "ocr_warning", "") if hasattr(self, "ocr_worker") and self.ocr_worker else ""
        if not text and not codes:
            QMessageBox.information(self, "OCR 结果", "未能识别到文字或二维码，请重试。")
        else:
            dlg = OcrResultDialog(
                text, self, detected_codes=codes, ocr_doc=doc,
                source_image=src_img, ocr_warning=warn
            )
            exec_dialog_nonmodal(dlg)

    def set_tool(self, tool_name, active_btn=None):
        if getattr(self.img_label, 'inline_editor', None) and self.img_label.inline_editor.isVisible():
            self.img_label.commit_text_edit()

        if getattr(self.img_label, 'has_pending_rotation_preview', None) and self.img_label.has_pending_rotation_preview() and (tool_name != 'ROTATE' or self.current_tool == 'ROTATE'):
            self.img_label.commit_rotation_preview()
            if hasattr(self, 'rotate_controls'):
                self.rotate_controls.set_angle(0)
            self._sync_dim_badge()

        # 退出裁剪模式（切换至其他工具，或再次点击裁剪按钮关闭时）
        if self.img_label.crop_mode and (tool_name != 'CROP' or self.current_tool == 'CROP'):
            self.img_label.exit_crop_mode()

        tool_btns = [
            self.btn_rect, self.btn_ellipse, self.btn_line, self.btn_arrow, self.btn_pen,
            self.btn_highlighter, self.btn_step, self.btn_rotate, self.btn_crop, self.btn_text,
            self.btn_blur, self.btn_erase, self.btn_ocr
        ]

        if tool_name is None or self.current_tool == tool_name:
            self.current_tool = None
            for btn in tool_btns:
                btn.setChecked(False)
            if hasattr(self, 'sub_bar'):
                self.sub_bar.set_tool(None)
            self.img_label.update_cursor_for_tool()
            return

        self.current_tool = tool_name
        for btn in tool_btns:
            btn.setChecked(btn is active_btn)

        if hasattr(self, 'sub_bar'):
            self.sub_bar.set_tool(tool_name)
        self._sync_color_button_from_active_tool()

        if tool_name == 'CROP':
            self.img_label.enter_crop_mode()
        self.img_label.update_cursor_for_tool()

    def keyPressEvent(self, event):
        mods = event.modifiers()
        key = event.key()
        fw = QApplication.focusWidget()
        in_spinbox = isinstance(fw, QAbstractSpinBox) or (fw is not None and isinstance(fw.parent(), QAbstractSpinBox))
        if in_spinbox and key in (Qt.Key_Return, Qt.Key_Enter):
            fw.interpretText()
            fw.clearFocus()
            if self.current_tool == 'ROTATE' or self.img_label.has_pending_rotation_preview():
                if self.img_label.has_pending_rotation_preview():
                    self.img_label.commit_rotation_preview()
                if hasattr(self, 'rotate_controls'):
                    self.rotate_controls.set_angle(0)
                self._sync_dim_badge()
            elif getattr(self.img_label, 'inline_editor', None) and self.img_label.inline_editor.isVisible():
                self.img_label.inline_editor.text_edit.setFocus()
            else:
                self.img_label.setFocus()
            return

        if key == Qt.Key_Escape:
            if getattr(self.img_label, 'inline_editor', None) and self.img_label.inline_editor.isVisible():
                self.img_label.commit_text_edit()
                return
            if self.img_label.has_pending_rotation_preview():
                self.img_label.cancel_rotation_preview()
                if hasattr(self, 'rotate_controls'):
                    self.rotate_controls.set_angle(0)
                self._sync_dim_badge()
                return
            if self.img_label.crop_mode:
                self.cancel_crop()
                return
            if self.current_tool:
                self.set_tool(None)
                return
        elif self.img_label.crop_mode and key in (Qt.Key_Return, Qt.Key_Enter):
            self.apply_crop()
            return
        elif (self.current_tool == 'ROTATE' or self.img_label.has_pending_rotation_preview()) and key in (Qt.Key_Return, Qt.Key_Enter):
            if self.img_label.has_pending_rotation_preview():
                self.img_label.commit_rotation_preview()
            if hasattr(self, 'rotate_controls'):
                self.rotate_controls.set_angle(0)
            self._sync_dim_badge()
            return
        elif key == Qt.Key_F2 and not (mods & (Qt.ControlModifier | Qt.AltModifier)):
            self.set_tool('OCR', self.btn_ocr)
            return
        elif mods & Qt.ControlModifier:
            if key == Qt.Key_Z:
                if mods & Qt.ShiftModifier:
                    self._on_shortcut_redo()
                else:
                    self._on_shortcut_undo()
                return
            elif key == Qt.Key_Y:
                self._on_shortcut_redo()
                return
            elif key == Qt.Key_C:
                self._on_shortcut_copy()
                return
            elif key == Qt.Key_S:
                self.save_image()
                return
        super().keyPressEvent(event)

    def apply_crop(self):
        """应用裁剪"""
        self.img_label.apply_crop()
        self.set_tool(None)
        self._sync_dim_badge()

    def cancel_crop(self):
        """取消裁剪"""
        self.img_label.exit_crop_mode()
        self.set_tool(None)

    def get_final_static_image(self):
        if getattr(self.img_label, '_rotation_preview_pixmap', None) is not None and not self.img_label._rotation_preview_pixmap.isNull():
            return QPixmap(self.img_label._rotation_preview_pixmap)
        if not self.img_label.edits:
            return self.img_label.base_pixmap
        return composite_pixmap_with_edits(self.img_label.base_pixmap, self.img_label.edits, QPoint(0, 0))

    def copy_to_clipboard(self):
        try:
            self._prepare_final_snapshot()
            final_img = self.get_final_static_image()
            if final_img and not final_img.isNull():
                QApplication.clipboard().setPixmap(final_img)
                CaptureHistoryManager.get_instance().add_capture(final_img, title="编辑后复制")
                check_and_auto_save_capture(final_img, prefix="edited")
                print("Copied to clipboard!")
            self.accept()
        except Exception as e:
            logging.error("复制到剪贴板失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "复制失败", f"复制到剪贴板时出错: {e}")

    def pin_to_screen(self):
        try:
            self._prepare_final_snapshot()
            final_img = self.get_final_static_image()
            if self.main_app_ref and final_img and not final_img.isNull():
                self.main_app_ref.create_pinned_window(final_img)
                CaptureHistoryManager.get_instance().add_capture(final_img, title="编辑后贴图")
                check_and_auto_save_capture(final_img, prefix="edited")
            self.accept()
        except Exception as e:
            logging.error("贴图失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "贴图失败", f"贴图时出错: {e}")

    def save_image(self):
        try:
            self._prepare_final_snapshot()
            final_img = self.get_final_static_image()
            if final_img and not final_img.isNull():
                ok = save_image_dialog(self, final_img, default_prefix="edited")
                if ok:
                    check_and_auto_save_capture(final_img, prefix="edited")
                    self.accept()
        except Exception as e:
            logging.error("保存图片失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "保存失败", f"保存图片时出错: {e}")


# ==========================================
# MULTI-FORMAT SAVE & AUTO-SAVE HELPERS
# ==========================================
def save_image_dialog(parent_widget, pixmap, default_prefix="screenshot"):
    """
    通用多格式保存对话框：支持 PNG/JPG/WebP，依据扩展名与画质参数正确编码保存
    """
    if pixmap is None or pixmap.isNull():
        return False
    try:
        settings = load_settings()
        save_dir = settings.get("default_save_dir", "")
        if not save_dir or not os.path.exists(save_dir):
            save_dir = os.path.join(os.path.expanduser("~"), "Pictures")
            if not os.path.exists(save_dir):
                save_dir = "."

        fmt_pref = settings.get("save_format", "png").lower()
        ext = ".jpg" if "jp" in fmt_pref else (".webp" if "webp" in fmt_pref else ".png")
        default_filename = os.path.join(save_dir, f"{default_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}")

        filter_str = "PNG 图像 (*.png);;JPEG 图像 (*.jpg *.jpeg);;WebP 图像 (*.webp);;所有文件 (*.*)"
        selected_filter = "PNG 图像 (*.png)"
        if "jp" in fmt_pref: selected_filter = "JPEG 图像 (*.jpg *.jpeg)"
        elif "webp" in fmt_pref: selected_filter = "WebP 图像 (*.webp)"

        fileName, chosen_filter = QFileDialog.getSaveFileName(parent_widget, "保存图像", default_filename, filter_str, selected_filter)
        if not fileName:
            return False

        lower_name = fileName.lower()
        quality = int(settings.get("save_quality", 95))
        fmt = "PNG"
        if lower_name.endswith(".jpg") or lower_name.endswith(".jpeg") or "JPEG" in chosen_filter:
            fmt = "JPG"
            if not (lower_name.endswith(".jpg") or lower_name.endswith(".jpeg")):
                fileName += ".jpg"
        elif lower_name.endswith(".webp") or "WebP" in chosen_filter:
            fmt = "WEBP"
            if not lower_name.endswith(".webp"):
                fileName += ".webp"
        else:
            fmt = "PNG"
            if not lower_name.endswith(".png"):
                fileName += ".png"

        ok = pixmap.save(fileName, fmt, quality)
        if ok:
            logging.info("图像保存成功: %s (%s, 质量=%d)", fileName, fmt, quality)
            CaptureHistoryManager.get_instance().add_capture(pixmap, title="另存截图")
            return True
        else:
            QMessageBox.warning(parent_widget, "保存失败", "保存图片时发生未知错误，未能写入文件。")
            return False
    except Exception as e:
        logging.error("保存图像出错: %s", e, exc_info=True)
        QMessageBox.warning(parent_widget, "保存失败", f"保存图片时出错: {e}")
        return False

def check_and_auto_save_capture(pixmap, prefix="capture"):
    """若在设置中启用了自动保存，静默在后台存储一份到默认目录"""
    if pixmap is None or pixmap.isNull():
        return
    try:
        settings = load_settings()
        if not settings.get("auto_save_enabled", False):
            return
        save_dir = settings.get("default_save_dir", "")
        if not save_dir or not os.path.exists(save_dir):
            save_dir = os.path.join(os.path.expanduser("~"), "Pictures")
            os.makedirs(save_dir, exist_ok=True)

        fmt_pref = settings.get("save_format", "png").lower()
        ext = ".jpg" if "jp" in fmt_pref else (".webp" if "webp" in fmt_pref else ".png")
        fmt = "JPG" if "jp" in fmt_pref else ("WEBP" if "webp" in fmt_pref else "PNG")
        quality = int(settings.get("save_quality", 95))

        fname = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:19]}{ext}"
        fpath = os.path.join(save_dir, fname)
        pixmap.save(fpath, fmt, quality)
        logging.info("自动静默保存截图完成: %s", fpath)
    except Exception as e:
        logging.warning("自动保存失败: %s", e)

def clean_orphan_temp_files():
    """清理超过 24 小时的孤儿录屏和临时文件"""
    try:
        now = time.time()
        for folder in [REC_TEMP_FOLDER, TEMP_FOLDER]:
            if os.path.exists(folder):
                for fname in os.listdir(folder):
                    fpath = os.path.join(folder, fname)
                    if os.path.isfile(fpath):
                        if now - os.path.getmtime(fpath) > 86400:
                            try:
                                os.remove(fpath)
                            except Exception:
                                pass
    except Exception as e:
        logging.warning("清理临时文件失败: %s", e)

# ==========================================
# CAPTURE HISTORY PERSISTENCE & UI
# ==========================================
HISTORY_DIR = os.path.join(DATA_DIR, "capture_history")
HISTORY_INDEX_FILE = os.path.join(HISTORY_DIR, "history_index.json")

class CaptureHistoryManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = CaptureHistoryManager()
        return cls._instance

    def __init__(self):
        os.makedirs(HISTORY_DIR, exist_ok=True)
        self.max_items = 100
        self.items = []
        self._load_index()

    def _load_index(self):
        try:
            if os.path.exists(HISTORY_INDEX_FILE):
                with open(HISTORY_INDEX_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.items = [it for it in data if os.path.exists(it.get('filepath', ''))]
        except Exception as e:
            logging.warning("读取截图历史索引失败: %s", e)
            self.items = []

    def _save_index(self):
        try:
            with open(HISTORY_INDEX_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning("保存截图历史索引失败: %s", e)

    def add_capture(self, pixmap, title="截图"):
        if pixmap is None or pixmap.isNull():
            return None
        try:
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
            filename = f"cap_{ts_str}.png"
            filepath = os.path.join(HISTORY_DIR, filename)
            pixmap.save(filepath, "PNG")

            thumb_filename = f"thumb_{ts_str}.png"
            thumb_path = os.path.join(HISTORY_DIR, thumb_filename)
            thumb = pixmap.scaled(220, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            thumb.save(thumb_path, "PNG")

            item = {
                'id': ts_str,
                'title': title,
                'filepath': filepath,
                'thumb_path': thumb_path,
                'width': pixmap.width(),
                'height': pixmap.height(),
                'time_str': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'timestamp': time.time()
            }
            self.items.insert(0, item)

            while len(self.items) > self.max_items:
                old = self.items.pop()
                try:
                    if os.path.exists(old.get('filepath', '')):
                        os.remove(old['filepath'])
                    if os.path.exists(old.get('thumb_path', '')):
                        os.remove(old['thumb_path'])
                except OSError as e:
                    logging.debug("清理旧历史截图文件失败: %s", e)

            self._save_index()
            return item
        except Exception as e:
            logging.error("保存截图历史失败: %s", e, exc_info=True)
            return None

    def delete_item(self, item_id):
        item = next((it for it in self.items if it.get('id') == item_id), None)
        if item:
            self.items.remove(item)
            try:
                if os.path.exists(item.get('filepath', '')):
                    os.remove(item['filepath'])
                if os.path.exists(item.get('thumb_path', '')):
                    os.remove(item['thumb_path'])
            except Exception:
                pass
            self._save_index()

    def clear_all(self):
        for item in self.items:
            try:
                if os.path.exists(item.get('filepath', '')):
                    os.remove(item['filepath'])
                if os.path.exists(item.get('thumb_path', '')):
                    os.remove(item['thumb_path'])
            except Exception:
                pass
        self.items.clear()
        self._save_index()


class _HistoryImagePreviewDialog(QDialog):
    """历史截图大图灯箱预览窗：支持滚轮缩放、1:1 / 适应窗口切换、拖拽平移与快捷操作"""
    def __init__(self, item, parent_dialog=None):
        super().__init__(parent_dialog)
        self._item = item or {}
        self._parent_dialog = parent_dialog
        self._filepath = self._item.get("filepath", "")
        self._pixmap = QPixmap(self._filepath) if self._filepath and os.path.exists(self._filepath) else QPixmap()
        self._fit_mode = True
        self._scale = 1.0
        self._panning = False
        self._pan_start = QPoint()
        self._h_start = 0
        self._v_start = 0

        self.setWindowTitle("历史截图大图预览")
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        self.resize(min(1120, int(avail.width() * 0.84)), min(780, int(avail.height() * 0.84)))
        self.setMinimumSize(640, 440)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + """
            QScrollArea#previewViewport {
                background-color: #EFEFF2;
                border: none;
            }
            QWidget#previewCanvasWrap {
                background-color: #EFEFF2;
            }
            QPushButton#pvBtn {
                background-color: #FFFFFF;
                color: #18181B;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                min-height: 22px;
            }
            QPushButton#pvBtn:hover {
                background-color: #F4F4F5;
                border-color: #D4D4D8;
                color: #0D0D0D;
            }
            QPushButton#pvPrimaryBtn {
                background-color: #0D0D0D;
                color: #FFFFFF;
                border: 1px solid #0D0D0D;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 600;
                min-height: 22px;
            }
            QPushButton#pvPrimaryBtn:hover {
                background-color: #27272A;
            }
            QLabel#pvFooter {
                background-color: #FFFFFF;
                border-top: 1px solid #ECECEF;
                color: #71717A;
                font-size: 11.5px;
                padding: 6px 16px;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_w = QWidget()
        top_w.setObjectName("codexTopBar")
        top = QHBoxLayout(top_w)
        top.setContentsMargins(18, 12, 18, 12)
        top.setSpacing(8)

        title_txt = self._item.get("time_str") or os.path.basename(self._filepath) or "截图预览"
        w_px = self._pixmap.width() if not self._pixmap.isNull() else int(self._item.get("width", 0) or 0)
        h_px = self._pixmap.height() if not self._pixmap.isNull() else int(self._item.get("height", 0) or 0)
        lbl_title = QLabel(title_txt)
        lbl_title.setObjectName("codexTitle")
        top.addWidget(lbl_title)

        lbl_dim = QLabel(f"{w_px} × {h_px} px")
        lbl_dim.setObjectName("codexPill")
        top.addWidget(lbl_dim)

        self.lbl_zoom = QLabel("适应")
        self.lbl_zoom.setObjectName("codexPill")
        top.addWidget(self.lbl_zoom)

        top.addStretch()

        btn_fit = QPushButton("适应窗口")
        btn_fit.setObjectName("pvBtn")
        btn_fit.setCursor(Qt.PointingHandCursor)
        btn_fit.clicked.connect(self.fit_to_window)
        top.addWidget(btn_fit)

        btn_actual = QPushButton("1:1 原图")
        btn_actual.setObjectName("pvBtn")
        btn_actual.setCursor(Qt.PointingHandCursor)
        btn_actual.clicked.connect(lambda: self.set_scale(1.0))
        top.addWidget(btn_actual)

        btn_copy = QPushButton("复制图片")
        btn_copy.setObjectName("pvPrimaryBtn")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(self._on_copy)
        top.addWidget(btn_copy)

        btn_copy_file = QPushButton("复制文件")
        btn_copy_file.setObjectName("pvBtn")
        btn_copy_file.setCursor(Qt.PointingHandCursor)
        btn_copy_file.clicked.connect(self._on_copy_file)
        top.addWidget(btn_copy_file)

        main_ref = parent_dialog._resolve_main_app_ref() if parent_dialog and hasattr(parent_dialog, "_resolve_main_app_ref") else None
        if main_ref is not None:
            btn_pin = QPushButton("贴图")
            btn_pin.setObjectName("pvBtn")
            btn_pin.setCursor(Qt.PointingHandCursor)
            btn_pin.clicked.connect(self._on_pin)
            top.addWidget(btn_pin)

            if getattr(main_ref, "pin_drawer", None) is not None:
                btn_drawer = QPushButton("存抽屉")
                btn_drawer.setObjectName("pvBtn")
                btn_drawer.setCursor(Qt.PointingHandCursor)
                btn_drawer.clicked.connect(self._on_drawer)
                top.addWidget(btn_drawer)

        btn_save = QPushButton("另存为...")
        btn_save.setObjectName("pvBtn")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._on_save)
        top.addWidget(btn_save)

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("codexSecondaryBtn")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.close)
        top.addWidget(btn_close)

        root.addWidget(top_w)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("previewViewport")
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)

        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setStyleSheet("background: #FFFFFF; border: 1px solid #D4D4D8;")
        self.scroll.setWidget(self.img_label)
        self.scroll.viewport().installEventFilter(self)
        self.img_label.installEventFilter(self)
        root.addWidget(self.scroll, 1)

        footer = QLabel("滚轮缩放 · 双击画面切换 适应 / 1:1 · 按住左键拖拽平移 · Ctrl+C 复制 · Esc 关闭")
        footer.setObjectName("pvFooter")
        root.addWidget(footer)

        self._sc_copy = QShortcut(QKeySequence("Ctrl+C"), self)
        self._sc_copy.setContext(Qt.WidgetWithChildrenShortcut)
        self._sc_copy.activated.connect(self._on_copy)

        if self._pixmap.isNull():
            self.img_label.setText("图片文件不存在或无法读取")
            self.img_label.resize(360, 180)
        else:
            QTimer.singleShot(0, self.fit_to_window)

    def fit_to_window(self):
        self._fit_mode = True
        self._render_pixmap()

    def set_scale(self, scale):
        self._fit_mode = False
        self._scale = max(0.15, min(4.0, float(scale)))
        self._render_pixmap()

    def _render_pixmap(self):
        if self._pixmap.isNull():
            return
        vp = self.scroll.viewport().size()
        avail_w = max(120, vp.width() - 32)
        avail_h = max(120, vp.height() - 32)
        if self._fit_mode:
            fit_scale = min(1.0, avail_w / max(1, self._pixmap.width()), avail_h / max(1, self._pixmap.height()))
            self._scale = max(0.1, fit_scale)
            prefix = "适应 · "
        else:
            prefix = ""
        tw = max(1, int(round(self._pixmap.width() * self._scale)))
        th = max(1, int(round(self._pixmap.height() * self._scale)))
        scaled = self._pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img_label.setPixmap(scaled)
        self.img_label.resize(scaled.size())
        self.lbl_zoom.setText(f"{prefix}{int(round(self._scale * 100))}%")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_fit_mode", False):
            self._render_pixmap()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_C:
            self._on_copy()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj in (self.scroll.viewport(), self.img_label):
            if event.type() == QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta != 0 and not self._pixmap.isNull():
                    factor = 1.15 if delta > 0 else (1.0 / 1.15)
                    self.set_scale(self._scale * factor)
                    return True
            elif event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                if self._fit_mode:
                    self.set_scale(1.0)
                else:
                    self.fit_to_window()
                return True
            elif event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._panning = True
                self._pan_start = event.globalPos()
                self._h_start = self.scroll.horizontalScrollBar().value()
                self._v_start = self.scroll.verticalScrollBar().value()
                self.scroll.viewport().setCursor(Qt.ClosedHandCursor)
                return True
            elif event.type() == QEvent.MouseMove and self._panning:
                delta = event.globalPos() - self._pan_start
                self.scroll.horizontalScrollBar().setValue(self._h_start - delta.x())
                self.scroll.verticalScrollBar().setValue(self._v_start - delta.y())
                return True
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self._panning:
                    self._panning = False
                    self.scroll.viewport().setCursor(Qt.ArrowCursor)
                    return True
        return super().eventFilter(obj, event)

    def _on_copy(self):
        if self._parent_dialog:
            self._parent_dialog._copy_item(self._item)
        elif not self._pixmap.isNull():
            QApplication.clipboard().setPixmap(self._pixmap)

    def _on_copy_file(self):
        if self._parent_dialog:
            self._parent_dialog._copy_item_as_file(self._item)

    def _on_pin(self):
        self.close()
        if self._parent_dialog:
            self._parent_dialog._pin_item(self._item)

    def _on_drawer(self):
        if self._parent_dialog:
            self._parent_dialog._send_item_to_drawer(self._item)

    def _on_save(self):
        if not self._pixmap.isNull():
            save_image_dialog(self, self._pixmap, default_prefix="history")


class _DraggableHistoryThumb(QLabel):
    """支持悬停预览、点击查看大图，以及左键拖出（途中右键切换 PNG 文件 / 贴图）的历史缩略图控件"""
    def __init__(self, filepath, parent_dialog=None, item_data=None):
        super().__init__()
        self._filepath = filepath
        self._parent_dialog = parent_dialog
        self._item_data = item_data or {"filepath": filepath}
        self._drag_start = None
        self._click_candidate = False
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击查看大图 · 悬停快速预览 · 按住拖出（途中点击右键可在 PNG 文件 / 贴图间切换）")

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(180)
        self._hover_timer.timeout.connect(self._on_hover_timeout)

    def _on_hover_timeout(self):
        if not self.underMouse():
            return
        if self._parent_dialog and self._parent_dialog.isVisible() and hasattr(self._parent_dialog, "_show_hover_preview"):
            self._parent_dialog._show_hover_preview(self, self._filepath)

    def enterEvent(self, event):
        self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_timer.stop()
        if self._parent_dialog and hasattr(self._parent_dialog, "_hide_hover_preview"):
            self._parent_dialog._hide_hover_preview()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._hover_timer.stop()
        if self._parent_dialog and hasattr(self._parent_dialog, "_hide_hover_preview"):
            self._parent_dialog._hide_hover_preview()
        if event.button() == Qt.LeftButton:
            self._drag_start = event.pos()
            self._click_candidate = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start is not None
            and (event.buttons() & Qt.LeftButton)
            and (event.pos() - self._drag_start).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._drag_start = None
            self._click_candidate = False
            self._start_toggleable_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and getattr(self, "_click_candidate", False):
            self._click_candidate = False
            self._drag_start = None
            if self._parent_dialog and hasattr(self._parent_dialog, "_preview_item"):
                self._parent_dialog._preview_item(self._item_data)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _start_toggleable_drag(self):
        if not self._filepath or not os.path.exists(self._filepath):
            return
        pix = QPixmap(self._filepath)
        if pix.isNull():
            return

        dlg = self._parent_dialog
        was_visible = bool(dlg is not None and dlg.isVisible())
        was_modal = bool(dlg is not None and dlg.isModal())
        orig_geo = dlg.geometry() if was_visible else None
        orig_opacity = dlg.windowOpacity() if was_visible else 1.0
        if dlg is not None and hasattr(dlg, "_hide_hover_preview"):
            dlg._hide_hover_preview()
        if was_visible:
            dlg._temp_hidden_for_drag = True
            if was_modal:
                # 若对话框处于模态 exec_()，绝不能调用 hide()（否则会提前触发 QDialog::done(0) 并破坏 activeModalWidget）
                dlg.setWindowOpacity(0.0)
                dlg.move(-24000, -24000)
            else:
                dlg.hide()
            QApplication.processEvents()

        outcome, drop_pos = "file", None
        try:
            outcome, _drag_res, drop_pos = run_toggleable_drag(
                self,
                pix,
                start_mode="file",
                copy_to_clipboard=True,
                file_drag_fn=lambda w, p: start_pixmap_file_drag(w, p),
            )
        finally:
            if dlg is not None:
                dlg._temp_hidden_for_drag = False
                if was_modal and was_visible:
                    dlg.setWindowOpacity(orig_opacity)
                    if orig_geo is not None:
                        dlg.setGeometry(orig_geo)
            if outcome == "drop_pin" and dlg is not None and hasattr(dlg, "_finish_drag_to_pin"):
                dlg._finish_drag_to_pin(pix, drop_pos)
            elif was_visible and dlg is not None and not getattr(dlg, "_did_commit_action", False):
                if not was_modal:
                    dlg.show()
                dlg.raise_()
                dlg.activateWindow()


class HistoryDialog(QDialog):
    """Codex 风格卡片流式截图历史管理窗口，支持持久化、大图预览、悬停预览、拖拽切换贴图/文件、重新编辑、贴图、复制与清理"""
    really_closed = pyqtSignal()

    def __init__(self, parent=None, main_app_ref=None):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.main_app_ref = main_app_ref
        self.main_app_ref = self._resolve_main_app_ref()
        self.mgr = CaptureHistoryManager.get_instance()
        self._did_commit_action = False
        self._really_closed_emitted = False
        self._temp_hidden_for_drag = False
        self._hover_preview = None
        self._hover_preview_path = ""
        self._hover_pixmap_cache = {}

        self.setWindowTitle("截图历史记录 (History)")
        self.resize(900, 630)
        self.setMinimumSize(660, 460)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + """
            QWidget#historyBody {
                background-color: #FCFCFD;
            }
            QFrame#historyCard {
                background-color: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 10px;
            }
            QFrame#historyCard:hover {
                border-color: #D4D4D8;
                background-color: #FAFAFA;
            }
            QLabel#historyThumb {
                background-color: #F4F4F5;
                border: 1px solid #ECECEF;
                border-radius: 7px;
            }
            QLabel#historyMetaTitle {
                font-size: 13.5px;
                font-weight: 600;
                color: #0D0D0D;
                background: transparent;
            }
            QLabel#historyMetaSub {
                font-size: 12px;
                color: #52525B;
                font-family: """ + CODEX_MONO_FAMILY + """;
                background: transparent;
            }
            QLabel#historyMetaFile {
                font-size: 11.5px;
                color: #A1A1AA;
                font-family: """ + CODEX_MONO_FAMILY + """;
                background: transparent;
            }
            QPushButton#histBtn {
                background-color: #FFFFFF;
                color: #18181B;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 400;
                min-height: 20px;
            }
            QPushButton#histBtn:hover {
                background-color: #F4F4F5;
                border-color: #D4D4D8;
                color: #0D0D0D;
            }
            QPushButton#histPrimaryBtn {
                background-color: #0D0D0D;
                color: #FFFFFF;
                border: 1px solid #0D0D0D;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 600;
                min-height: 20px;
            }
            QPushButton#histPrimaryBtn:hover {
                background-color: #27272A;
            }
            QPushButton#histDangerBtn {
                background-color: #FFFFFF;
                color: #DC2626;
                border: 1px solid #FECACA;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 400;
                min-height: 20px;
            }
            QPushButton#histDangerBtn:hover {
                background-color: #FEF2F2;
                border-color: #FCA5A5;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部 Codex 工具栏
        top_bar_w = QWidget()
        top_bar_w.setObjectName("codexTopBar")
        top_bar = QHBoxLayout(top_bar_w)
        top_bar.setContentsMargins(20, 14, 20, 14)
        top_bar.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(VectorIconFactory.create_pixmap("history", QColor("#0D0D0D")).scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon_lbl.setFixedSize(18, 18)
        top_bar.addWidget(icon_lbl)

        self.title_lbl = QLabel("截图历史记录")
        self.title_lbl.setObjectName("codexTitle")
        top_bar.addWidget(self.title_lbl)

        self.lbl_count = QLabel(f"共 {len(self.mgr.items)} 条")
        self.lbl_count.setObjectName("codexPill")
        top_bar.addWidget(self.lbl_count)

        top_bar.addStretch()

        self._time_filter = "all"
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for key, label in (("all", "全部"), ("today", "今天"), ("week", "近 7 天")):
            fbtn = QPushButton(label)
            fbtn.setCheckable(True)
            fbtn.setChecked(key == "all")
            fbtn.setCursor(Qt.PointingHandCursor)
            fbtn.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF; color: #52525B; border: 1px solid #E4E4E7;
                    border-radius: 6px; padding: 4px 10px; font-size: 12px;
                }
                QPushButton:hover { background: #F4F4F5; color: #0D0D0D; }
                QPushButton:checked { background: #0D0D0D; color: #FFFFFF; border-color: #0D0D0D; font-weight: 600; }
            """)
            fbtn.clicked.connect(lambda _=False, k=key: self._set_time_filter(k))
            self._filter_group.addButton(fbtn)
            top_bar.addWidget(fbtn)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索时间、尺寸或备注...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedWidth(220)
        self.search_input.textChanged.connect(self.populate_cards)
        top_bar.addWidget(self.search_input)

        self.btn_clear = QPushButton("清空全部")
        self.btn_clear.setObjectName("codexDangerBtn")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.on_clear_all)
        top_bar.addWidget(self.btn_clear)

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("codexSecondaryBtn")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        top_bar.addWidget(self.btn_close)

        layout.addWidget(top_bar_w)

        # 中部滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.verticalScrollBar().valueChanged.connect(lambda _v: self._hide_hover_preview())

        self.cards_container = QWidget()
        self.cards_container.setObjectName("historyBody")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(20, 16, 20, 16)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()
        self.scroll_area.setWidget(self.cards_container)

        layout.addWidget(self.scroll_area, 1)

        self.populate_cards()

    def _resolve_main_app_ref(self):
        if self.main_app_ref is not None and hasattr(self.main_app_ref, "create_pinned_window"):
            return self.main_app_ref
        p = self.parent()
        if p is not None and hasattr(p, "create_pinned_window"):
            self.main_app_ref = p
            return self.main_app_ref
        for w in QApplication.topLevelWidgets():
            if hasattr(w, "create_pinned_window") and hasattr(w, "pin_drawer"):
                self.main_app_ref = w
                return self.main_app_ref
        return self.main_app_ref

    def _show_hover_preview(self, thumb_widget, filepath):
        if DrawerHoverPreview is None or not filepath or not os.path.exists(filepath):
            return
        if self._hover_preview is None:
            self._hover_preview = DrawerHoverPreview()
        pix = self._hover_pixmap_cache.get(filepath)
        if pix is None or pix.isNull():
            pix = QPixmap(filepath)
            if not pix.isNull():
                if len(self._hover_pixmap_cache) >= 24:
                    self._hover_pixmap_cache.clear()
                self._hover_pixmap_cache[filepath] = pix
        if pix.isNull():
            return
        top_left = thumb_widget.mapToGlobal(QPoint(0, 0))
        anchor_rect = QRect(top_left, thumb_widget.size())
        screen = QApplication.screenAt(anchor_rect.center()) or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        hint = "点击查看大图 · 右键切 PNG/贴图"
        self._hover_preview_path = filepath
        self._hover_preview.show_for_pixmap_beside_rect(pix, anchor_rect, avail, hint_text=hint)

    def _hide_hover_preview(self):
        if self._hover_preview is not None:
            try:
                self._hover_preview.hide()
            except Exception:
                pass
        self._hover_preview_path = ""

    def hideEvent(self, event):
        self._hide_hover_preview()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._hide_hover_preview()
        super().closeEvent(event)

    def _set_time_filter(self, key):
        self._time_filter = key
        self.populate_cards()

    def populate_cards(self):
        while self.cards_layout.count() > 1:
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        filter_txt = self.search_input.text().strip().lower()
        now_dt = datetime.now()
        today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        week_start = time.time() - 7 * 86400

        matched_items = []
        for item in self.mgr.items:
            ts = float(item.get('timestamp', 0) or 0)
            if self._time_filter == "today" and ts < today_start:
                continue
            elif self._time_filter == "week" and ts < week_start:
                continue
            if not filter_txt:
                matched_items.append(item)
            else:
                desc = f"{item.get('time_str', '')} {item.get('width', 0)}x{item.get('height', 0)} {item.get('title', '')}".lower()
                if filter_txt in desc:
                    matched_items.append(item)

        self.lbl_count.setText(f"共 {len(matched_items)} 条")

        if not matched_items:
            empty_card = QFrame()
            empty_card.setObjectName("codexCard")
            empty_lay = QVBoxLayout(empty_card)
            empty_lay.setContentsMargins(24, 56, 24, 56)
            empty_lay.setSpacing(6)
            empty_title = QLabel("暂无匹配的截图记录" if filter_txt else "暂无截图历史记录")
            empty_title.setAlignment(Qt.AlignCenter)
            empty_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #18181B; background: transparent;")
            empty_sub = QLabel("尝试更换搜索关键词" if filter_txt else "使用截图快捷键捕获屏幕后，记录将自动保存并展示在这里。")
            empty_sub.setAlignment(Qt.AlignCenter)
            empty_sub.setStyleSheet("font-size: 12px; color: #71717A; background: transparent;")
            empty_lay.addWidget(empty_title)
            empty_lay.addWidget(empty_sub)
            self.cards_layout.insertWidget(0, empty_card)
            return

        for item in matched_items:
            card = self._create_item_card(item)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    def _create_item_card(self, item):
        card = QFrame()
        card.setObjectName("historyCard")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(16)

        thumb_lbl = _DraggableHistoryThumb(item.get('filepath', ''), parent_dialog=self, item_data=item)
        thumb_lbl.setObjectName("historyThumb")
        thumb_lbl.setFixedSize(148, 96)
        thumb_lbl.setAlignment(Qt.AlignCenter)
        tpath = item.get('thumb_path', '')
        if os.path.exists(tpath):
            pix = QPixmap(tpath)
            if not pix.isNull():
                scaled = pix.scaled(140, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                thumb_lbl.setPixmap(scaled)
        card_layout.addWidget(thumb_lbl)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        lbl_time = QLabel(item.get('time_str', ''))
        lbl_time.setObjectName("historyMetaTitle")
        lbl_res = QLabel(f"{item.get('width', 0)} × {item.get('height', 0)} px")
        lbl_res.setObjectName("historyMetaSub")
        lbl_path = QLabel(os.path.basename(item.get('filepath', '')))
        lbl_path.setObjectName("historyMetaFile")

        info_col.addWidget(lbl_time)
        info_col.addWidget(lbl_res)
        info_col.addWidget(lbl_path)
        info_col.addStretch()
        card_layout.addLayout(info_col, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)

        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(6)

        btn_copy = QPushButton("复制")
        btn_copy.setObjectName("histPrimaryBtn")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(lambda _, it=item: self._copy_item(it))
        btn_row1.addWidget(btn_copy)

        btn_preview = QPushButton("大图")
        btn_preview.setObjectName("histBtn")
        btn_preview.setCursor(Qt.PointingHandCursor)
        btn_preview.setToolTip("打开高清大图预览窗口（支持滚轮缩放与 1:1 切换）")
        btn_preview.clicked.connect(lambda _, it=item: self._preview_item(it))
        btn_row1.addWidget(btn_preview)

        btn_edit = QPushButton("编辑")
        btn_edit.setObjectName("histBtn")
        btn_edit.setCursor(Qt.PointingHandCursor)
        btn_edit.clicked.connect(lambda _, it=item: self._edit_item(it))
        btn_row1.addWidget(btn_edit)

        btn_pin = QPushButton("贴图")
        btn_pin.setObjectName("histBtn")
        btn_pin.setCursor(Qt.PointingHandCursor)
        btn_pin.clicked.connect(lambda _, it=item: self._pin_item(it))
        btn_row1.addWidget(btn_pin)

        btn_col.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(6)
        btn_row2.addStretch()

        btn_copy_file = QPushButton("复制文件")
        btn_copy_file.setObjectName("histBtn")
        btn_copy_file.setCursor(Qt.PointingHandCursor)
        btn_copy_file.setToolTip("导出为 PNG 文件写入剪贴板，可直接粘贴发送到微信 / 文件夹")
        btn_copy_file.clicked.connect(lambda _, it=item: self._copy_item_as_file(it))
        btn_row2.addWidget(btn_copy_file)

        main_ref = self._resolve_main_app_ref()
        if main_ref and hasattr(main_ref, "pin_drawer") and main_ref.pin_drawer:
            btn_drawer = QPushButton("存抽屉")
            btn_drawer.setObjectName("histBtn")
            btn_drawer.setCursor(Qt.PointingHandCursor)
            btn_drawer.clicked.connect(lambda _, it=item: self._send_item_to_drawer(it))
            btn_row2.addWidget(btn_drawer)

        btn_save = QPushButton("另存为...")
        btn_save.setObjectName("histBtn")
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(lambda _, it=item: self._save_item(it))
        btn_row2.addWidget(btn_save)

        btn_del = QPushButton("删除")
        btn_del.setObjectName("histDangerBtn")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(lambda _, it=item: self._delete_item(it))
        btn_row2.addWidget(btn_del)

        btn_col.addLayout(btn_row2)
        card_layout.addLayout(btn_col)

        return card

    def _preview_item(self, item):
        self._hide_hover_preview()
        fpath = (item or {}).get('filepath', '')
        if not fpath or not os.path.exists(fpath):
            QToolTip.showText(QCursor.pos(), "图片文件不存在或已被移除", None, QRect(), 2000)
            return
        dlg = _HistoryImagePreviewDialog(item, parent_dialog=self)
        exec_dialog_nonmodal(dlg)

    def _finish_drag_to_pin(self, pix, drop_pos):
        main_ref = self._resolve_main_app_ref()
        if main_ref is not None and pix is not None and not pix.isNull():
            self._did_commit_action = True
            self._hide_hover_preview()
            self.close()
            pin = main_ref.create_pinned_window(pix)
            if pin is not None and drop_pos is not None:
                pin.move(max(0, drop_pos.x() - pin.width() // 2), max(0, drop_pos.y() - pin.height() // 2))
            return pin
        if self.isHidden():
            self.show()
            self.raise_()
        return None

    def _copy_item_as_file(self, item):
        fpath = item.get('filepath', '')
        if os.path.exists(fpath):
            pix = QPixmap(fpath)
            if not pix.isNull() and copy_pixmap_as_clipboard_file(pix):
                QToolTip.showText(QCursor.pos(), "📄 已复制为 PNG 文件（可直接 Ctrl+V 发送）", None, QRect(), 2200)

    def _send_item_to_drawer(self, item):
        fpath = item.get('filepath', '')
        main_ref = self._resolve_main_app_ref()
        if os.path.exists(fpath) and main_ref and getattr(main_ref, 'pin_drawer', None):
            pix = QPixmap(fpath)
            if not pix.isNull():
                drawer = main_ref.pin_drawer
                if not drawer.drawer_enabled:
                    drawer.set_feature_enabled(True)
                    save_settings({"drawer_hover_enabled": True})
                drawer._insert_pixmaps([pix], keep_open_ms=900)
                QToolTip.showText(QCursor.pos(), "🗄️ 已存入右侧贴图抽屉！", None, QRect(), 1800)

    def _copy_item(self, item):
        fpath = item.get('filepath', '')
        if os.path.exists(fpath):
            pix = QPixmap(fpath)
            if not pix.isNull():
                QApplication.clipboard().setPixmap(pix)
                QToolTip.showText(QCursor.pos(), "✅ 已复制到剪贴板！", None, QRect(), 2000)

    def _edit_item(self, item):
        fpath = item.get('filepath', '')
        if os.path.exists(fpath):
            pix = QPixmap(fpath)
            if not pix.isNull():
                self._did_commit_action = True
                self._hide_hover_preview()
                self.close()
                editor = ImageEditorDialog(pix, self._resolve_main_app_ref())
                editor.exec_nonmodal()

    def _pin_item(self, item):
        fpath = item.get('filepath', '')
        if os.path.exists(fpath):
            pix = QPixmap(fpath)
            main_ref = self._resolve_main_app_ref()
            if not pix.isNull() and main_ref:
                self._did_commit_action = True
                self._hide_hover_preview()
                self.close()
                main_ref.create_pinned_window(pix)
            elif not main_ref:
                QToolTip.showText(QCursor.pos(), "未检测到活跃的贴图管理器", None, QRect(), 2000)

    def _save_item(self, item):
        fpath = item.get('filepath', '')
        if os.path.exists(fpath):
            pix = QPixmap(fpath)
            if not pix.isNull():
                save_image_dialog(self, pix, default_prefix="history")

    def _delete_item(self, item):
        self.mgr.delete_item(item.get('id'))
        self.populate_cards()

    def on_clear_all(self):
        ret = QMessageBox.question(self, "确认清空", "确定要清空所有截图历史记录和本地文件吗？", QMessageBox.Yes | QMessageBox.No)
        if ret == QMessageBox.Yes:
            self.mgr.clear_all()
            self.populate_cards()

    def _emit_really_closed(self):
        self._hide_hover_preview()
        if not getattr(self, "_really_closed_emitted", False):
            self._really_closed_emitted = True
            self.really_closed.emit()

    def exec_nonmodal(self):
        """以非模态事件循环等待窗口关闭，彻底避免拖拽时临时 hide() 破坏 activeModalWidget。"""
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._really_closed_emitted = False
        _register_nonmodal_dialog(self)
        loop = QEventLoop(self)
        self.really_closed.connect(loop.quit)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(loop.quit)
        self.show()
        _force_activate_window(self)
        if not self._really_closed_emitted:
            loop.exec_()
        _unregister_nonmodal_dialog(self)
        return 0

    def done(self, r):
        super().done(r)
        if not getattr(self, "_temp_hidden_for_drag", False):
            self._emit_really_closed()

    def closeEvent(self, event):
        super().closeEvent(event)
        self._emit_really_closed()


class _HotkeyInnerEdit(QLineEdit):
    keyRecorded = pyqtSignal(str)
    recordingCancelled = pyqtSignal()
    recordingStateChanged = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self._recording = False
        self._original_val = ""
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self.start_recording()

    def start_recording(self):
        if self._recording:
            return
        self._recording = True
        self._original_val = self.text()
        self.setText("按下快捷键...")
        self.setStyleSheet("border: 1.5px solid #0D0D0D; background: #F4F4F5; color: #0D0D0D; font-weight: 600; border-radius: 7px;")
        self.recordingStateChanged.emit(True)

    def stop_recording(self, restore=True):
        if self._recording:
            self._recording = False
            self.setStyleSheet("")
            if restore:
                self.setText(self._original_val)
                self.recordingCancelled.emit()
            self.recordingStateChanged.emit(False)

    def focusOutEvent(self, event):
        if self._recording:
            self.stop_recording(restore=True)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if not self._recording:
            super().keyPressEvent(event)
            return

        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key_Escape:
            self.stop_recording(restore=True)
            return

        # Backspace or Delete without modifiers -> clear hotkey
        if key in (Qt.Key_Backspace, Qt.Key_Delete) and not (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)):
            self._recording = False
            self.setStyleSheet("")
            self.setText("")
            self.keyRecorded.emit("")
            self.recordingStateChanged.emit(False)
            return

        # Modifier key only -> show feedback
        mod_names = []
        if (modifiers & Qt.ControlModifier) or key == Qt.Key_Control:
            mod_names.append("Ctrl")
        if (modifiers & Qt.AltModifier) or key == Qt.Key_Alt:
            mod_names.append("Alt")
        if (modifiers & Qt.ShiftModifier) or key == Qt.Key_Shift:
            mod_names.append("Shift")
        if (modifiers & Qt.MetaModifier) or key == Qt.Key_Meta:
            mod_names.append("Win")

        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            if mod_names:
                self.setText(" + ".join(mod_names) + " + ...")
            return

        final_hotkey = key_event_to_hotkey_str(event)
        if final_hotkey:
            self._recording = False
            self.setStyleSheet("")
            self.setText(final_hotkey)
            self.keyRecorded.emit(final_hotkey)
            self.recordingStateChanged.emit(False)


class HotkeyRecorderWidget(QWidget):
    hotkeyChanged = pyqtSignal(str)
    recordingStateChanged = pyqtSignal(bool)

    def __init__(self, placeholder="点击录制", parent=None):
        super().__init__(parent)
        self.setFixedWidth(182)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.edit = _HotkeyInnerEdit(self)
        self.edit.setObjectName("codexHotkeyEdit")
        self.edit.setFixedHeight(30)
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.setPlaceholderText(placeholder)
        self.edit.keyRecorded.connect(self._on_recorded)
        self.edit.recordingCancelled.connect(self._update_clear_btn)
        self.edit.recordingStateChanged.connect(self.recordingStateChanged)
        layout.addWidget(self.edit, 1)

        self.btn_clear = QPushButton("×")
        self.btn_clear.setFixedSize(26, 26)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setToolTip("清除快捷键")
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background: #F4F4F5;
                color: #71717A;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                padding-bottom: 1px;
            }
            QPushButton:hover {
                background: #FEE2E2;
                color: #DC2626;
                border-color: #FCA5A5;
            }
        """)
        self.btn_clear.clicked.connect(self.clear)
        layout.addWidget(self.btn_clear)

        self._update_clear_btn()

    def _on_recorded(self, val):
        self._update_clear_btn()
        self.hotkeyChanged.emit(val)

    def _update_clear_btn(self):
        val = self.edit.text().strip()
        self.btn_clear.setVisible(bool(val and val != "按下快捷键..."))

    def text(self):
        t = self.edit.text().strip()
        if t == "按下快捷键...":
            return ""
        return t

    def setText(self, val):
        self.edit.setText(val or "")
        self._update_clear_btn()

    def clear(self):
        if self.edit._recording:
            self.edit.stop_recording(restore=False)
        self.edit.setText("")
        self._update_clear_btn()
        self.hotkeyChanged.emit("")


def parse_version_tuple(text):
    s = str(text or "").strip().lower()
    if s.startswith("v"):
        s = s[1:]
    m = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?", s)
    if not m:
        return (0,)
    return tuple(int(part) if part else 0 for part in m.groups())


def _http_get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Capture/{APP_VERSION}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_github_asset_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in {
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
            "github-releases.githubusercontent.com",
        }
    except Exception:
        return False


class UpdateInfo:
    def __init__(self, version, tag, notes, asset_url, asset_name, size, sha256, page_url, published_at=""):
        self.version = str(version or "").lstrip("vV")
        self.tag = tag or f"v{self.version}"
        self.notes = notes or ""
        self.asset_url = asset_url or ""
        self.asset_name = asset_name or "Capture.exe"
        self.size = int(size or 0)
        self.sha256 = (sha256 or "").lower().replace("sha256:", "").strip()
        self.page_url = page_url or f"{GITHUB_RELEASES_PAGE}/tag/{self.tag}"
        self.published_at = published_at or ""
        self.version_tuple = parse_version_tuple(self.version)


def _parse_latest_json(data):
    version = data.get("version") or data.get("tag") or data.get("tag_name") or ""
    version = str(version).lstrip("vV")
    if not version:
        return None
    asset = data.get("asset") or {}
    asset_url = data.get("asset_url") or asset.get("url") or data.get("url") or ""
    if not asset_url:
        return None
    return UpdateInfo(
        version,
        data.get("tag") or f"v{version}",
        data.get("notes") or data.get("body") or "",
        asset_url,
        asset.get("name") or data.get("asset_name") or "Capture.exe",
        data.get("size") or asset.get("size") or 0,
        data.get("sha256") or asset.get("sha256") or "",
        data.get("page_url") or data.get("html_url") or "",
        data.get("published_at") or "",
    )


def _parse_release_json(data):
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name") or ""
    version = str(tag).lstrip("vV")
    if not version:
        return None
    assets = data.get("assets") or []
    asset = None
    for item in assets:
        if str(item.get("name", "")).lower() == "capture.exe":
            asset = item
            break
    if asset is None:
        return None
    return UpdateInfo(
        version,
        tag,
        data.get("body") or "",
        asset.get("browser_download_url") or "",
        asset.get("name") or "Capture.exe",
        asset.get("size") or 0,
        asset.get("digest") or "",
        data.get("html_url") or "",
        data.get("published_at") or "",
    )


def fetch_latest_update():
    # GitHub API 通常 1 秒内返回；先走 API，失败/限流再回退 latest.json 资产
    api_error = None
    try:
        data = _http_get_json(GITHUB_API_LATEST, timeout=6)
        info = _parse_release_json(data)
        if info is None:
            return None
        return info if info.version_tuple > parse_version_tuple(APP_VERSION) else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        api_error = f"HTTP {e.code}"
    except Exception as e:
        api_error = str(e)

    logging.warning("GitHub API 检查失败，尝试 latest.json: %s", api_error)
    try:
        data = _http_get_json(GITHUB_LATEST_JSON, timeout=4)
        info = _parse_latest_json(data)
        if info is not None:
            return info if info.version_tuple > parse_version_tuple(APP_VERSION) else None
    except Exception as e:
        logging.warning("读取 latest.json 失败: %s", e)

    if api_error:
        raise RuntimeError(f"无法检查更新：{api_error}")
    return None

class UpdateChecker(QThread):
    checked = pyqtSignal(object, str)

    def __init__(self, manual=False, parent=None):
        super().__init__(parent)
        self.manual = manual

    def run(self):
        try:
            self.checked.emit(fetch_latest_update(), "")
        except Exception as e:
            self.checked.emit(None, str(e))


class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        part_path = ""
        try:
            os.makedirs(UPDATE_DIR, exist_ok=True)
            filename = f"Capture-{self.info.version}.exe"
            final_path = os.path.join(UPDATE_DIR, filename)
            part_path = final_path + ".part"
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            if not _is_github_asset_url(self.info.asset_url):
                raise RuntimeError("下载地址不是 GitHub 官方地址，已拒绝")

            req = urllib.request.Request(self.info.asset_url, headers={
                "User-Agent": f"Capture/{APP_VERSION}",
                "Accept": "application/octet-stream",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                final_url = resp.geturl()
                if not _is_github_asset_url(final_url):
                    raise RuntimeError("下载跳转到了非 GitHub 地址，已拒绝")
                total = int(resp.headers.get("Content-Length") or self.info.size or 0)
                digest = hashlib.sha256()
                downloaded = 0
                start = time.time()
                last_emit = 0.0
                with open(part_path, "wb") as f:
                    while True:
                        if self._cancelled:
                            raise RuntimeError("已取消下载")
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_emit >= 0.15:
                            last_emit = now
                            pct = int(downloaded * 100 / total) if total > 0 else -1
                            speed = downloaded / max(0.2, now - start) / 1024 / 1024
                            if total > 0:
                                text = f"{downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB  ·  {speed:.1f} MB/s"
                            else:
                                text = f"{downloaded / 1024 / 1024:.1f} MB  ·  {speed:.1f} MB/s"
                            self.progress.emit(pct, text)
            if self._cancelled:
                raise RuntimeError("已取消下载")
            actual_sha = digest.hexdigest().lower()
            if self.info.sha256 and actual_sha != self.info.sha256:
                raise RuntimeError("安装包 SHA256 校验失败，已删除下载文件")
            if self.info.size and os.path.getsize(part_path) != self.info.size:
                raise RuntimeError("安装包大小校验失败，已删除下载文件")
            os.replace(part_path, final_path)
            part_path = ""
            self.done.emit(final_path)
        except Exception as e:
            if part_path and os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            self.failed.emit(str(e))


def _plain_release_notes(text):
    lines = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "• ", line)
        lines.append(line)
    return "\n".join(lines).strip()


class UpdateAvailableDialog(QDialog):
    install_requested = pyqtSignal()
    skip_requested = pyqtSignal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.setWindowTitle("发现新版本")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(580)
        self.resize(640, 540)
        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + """
            QWidget#updBody {
                background-color: #FCFCFD;
            }
            QTextEdit#updNotes {
                background-color: #FFFFFF;
                color: #18181B;
                border: none;
                border-radius: 9px;
                padding: 12px 14px;
                font-size: 12.5px;
                selection-background-color: #0D0D0D;
                selection-color: #FFFFFF;
            }
        """)
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        top_w = QWidget()
        top_w.setObjectName("codexTopBar")
        top_lay = QVBoxLayout(top_w)
        top_lay.setContentsMargins(20, 16, 20, 14)
        top_lay.setSpacing(6)

        row_head = QHBoxLayout()
        row_head.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(_create_codex_icon("update", "#0D0D0D", 16).pixmap(16, 16))
        ico.setFixedSize(18, 18)
        row_head.addWidget(ico)

        title = QLabel(f"发现新版本 v{info.version}")
        title.setObjectName("codexTitle")
        row_head.addWidget(title)

        ver_pill = QLabel(f"v{APP_VERSION} → v{info.version}")
        ver_pill.setObjectName("codexPill")
        row_head.addWidget(ver_pill)
        row_head.addStretch()
        top_lay.addLayout(row_head)

        meta_parts = [f"安装包大小：{info.size / 1024 / 1024:.1f} MB" if info.size else "安装包大小：未知"]
        if info.published_at:
            meta_parts.append(f"发布日期：{info.published_at[:10]}")
        sub = QLabel("   ·   ".join(meta_parts))
        sub.setObjectName("codexSubtitle")
        top_lay.addWidget(sub)
        main.addWidget(top_w)

        body_w = QWidget()
        body_w.setObjectName("updBody")
        body_lay = QVBoxLayout(body_w)
        body_lay.setContentsMargins(20, 16, 20, 16)
        body_lay.setSpacing(8)

        lbl_notes = QLabel("更新说明")
        lbl_notes.setObjectName("codexSectionLabel")
        body_lay.addWidget(lbl_notes)

        notes_card = QFrame()
        notes_card.setObjectName("codexCard")
        notes_lay = QVBoxLayout(notes_card)
        notes_lay.setContentsMargins(2, 2, 2, 2)
        self.notes = QTextEdit()
        self.notes.setObjectName("updNotes")
        self.notes.setReadOnly(True)
        self.notes.setPlainText((_plain_release_notes(info.notes) or "本次更新暂无详细说明。")[:12000])
        notes_lay.addWidget(self.notes)
        body_lay.addWidget(notes_card, 1)
        main.addWidget(body_w, 1)

        bottom_w = QWidget()
        bottom_w.setObjectName("codexBottomBar")
        buttons = QHBoxLayout(bottom_w)
        buttons.setContentsMargins(20, 12, 20, 12)
        buttons.setSpacing(8)

        btn_page = QPushButton("打开发布页")
        btn_page.setObjectName("codexGhostBtn")
        btn_page.setCursor(Qt.PointingHandCursor)
        btn_page.clicked.connect(lambda: webbrowser.open(info.page_url))
        buttons.addWidget(btn_page)
        buttons.addStretch()

        btn_skip = QPushButton("跳过此版本")
        btn_skip.setObjectName("codexSecondaryBtn")
        btn_skip.setCursor(Qt.PointingHandCursor)
        btn_skip.clicked.connect(lambda: (self.skip_requested.emit(info.version), self.accept()))
        buttons.addWidget(btn_skip)

        btn_later = QPushButton("稍后")
        btn_later.setObjectName("codexSecondaryBtn")
        btn_later.setCursor(Qt.PointingHandCursor)
        btn_later.clicked.connect(self.reject)
        buttons.addWidget(btn_later)

        btn_install = QPushButton("下载并安装")
        btn_install.setObjectName("codexPrimaryBtn")
        btn_install.setCursor(Qt.PointingHandCursor)
        btn_install.clicked.connect(lambda: (self.install_requested.emit(), self.accept()))
        buttons.addWidget(btn_install)
        main.addWidget(bottom_w)


class UpdateProgressDialog(QDialog):
    ready = pyqtSignal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.worker = None
        self._closed = False
        self.setWindowTitle("正在下载更新")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setFixedWidth(520)
        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + """
            QProgressBar#codexProgress {
                border: none;
                border-radius: 4px;
                background-color: #ECECEF;
                height: 8px;
            }
            QProgressBar#codexProgress::chunk {
                background-color: #0D0D0D;
                border-radius: 4px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(_create_codex_icon("update", "#0D0D0D", 16).pixmap(16, 16))
        ico.setFixedSize(18, 18)
        head_row.addWidget(ico)

        self.lbl_title = QLabel(f"正在下载 Capture v{info.version}…")
        self.lbl_title.setObjectName("codexTitle")
        head_row.addWidget(self.lbl_title)
        head_row.addStretch()
        layout.addLayout(head_row)

        self.bar = QProgressBar()
        self.bar.setObjectName("codexProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.lbl_status = QLabel("正在连接 GitHub…")
        self.lbl_status.setObjectName("codexSubtitle")
        row.addWidget(self.lbl_status, 1)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("codexSecondaryBtn")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._cancel)
        row.addWidget(self.btn_cancel)
        layout.addLayout(row)

    def start(self):
        self.worker = UpdateDownloadWorker(self.info, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.lbl_status.setText("正在取消…")

    def _on_progress(self, pct, text):
        if pct < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
        self.lbl_status.setText(text)

    def _on_done(self, path):
        self._closed = True
        self.ready.emit(path)
        self.accept()

    def _on_failed(self, msg):
        self._closed = True
        QMessageBox.warning(self, "更新下载失败", msg)
        self.reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning() and not self._closed:
            self._cancel()
            event.ignore()
            return
        event.accept()


def find_updater_executable():
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), "Updater.exe"))
    candidates.append(os.path.join(BASE_DIR, "Updater.exe"))
    for path in candidates:
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def launch_updater_process(new_exe, info):
    if not getattr(sys, "frozen", False):
        raise RuntimeError("自动更新只支持打包后的 exe")
    updater = find_updater_executable()
    if not updater:
        raise RuntimeError("未找到内置更新器 Updater.exe，请到发布页手动下载")
    os.makedirs(UPDATE_DIR, exist_ok=True)
    temp_updater = os.path.join(tempfile.gettempdir(), f"MuriokiCaptureUpdater-{int(time.time())}.exe")
    shutil.copy2(updater, temp_updater)
    cmd = [
        temp_updater,
        "--pid", str(os.getpid()),
        "--source", new_exe,
        "--target", sys.executable,
        "--sha256", info.sha256 or "",
        "--log", os.path.join(UPDATE_DIR, "updater.log"),
        "--restart",
    ]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, cwd=BASE_DIR, close_fds=True, creationflags=flags)
    return temp_updater


# (Codex shared icons & widgets are defined earlier above PinToast)


class SettingsDialog(QDialog):
    """系统偏好设置面板（OpenAI Codex 风格双栏极简设置中心）"""
    def __init__(self, parent=None, hotkey_mgr=None):
        super().__init__(parent)
        self.hotkey_mgr = hotkey_mgr
        self.setWindowTitle("系统设置 - Capture")
        self.resize(790, 560)
        self.setMinimumSize(680, 440)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._apply_codex_qss()

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        main_pane = QWidget()
        main_pane.setObjectName("codexMainPane")
        pane_lay = QVBoxLayout(main_pane)
        pane_lay.setContentsMargins(0, 0, 0, 0)
        pane_lay.setSpacing(0)
        pane_lay.addWidget(self._build_header())

        self.pages = QStackedWidget()
        self.pages.setObjectName("codexPages")
        self.pages.setStyleSheet("QStackedWidget#codexPages { background: #FFFFFF; border: none; }")
        self.pages.addWidget(self._build_hotkeys_page())
        self.pages.addWidget(self._build_general_page())
        self.pages.addWidget(self._build_ai_page())
        self.pages.addWidget(self._build_update_page())
        pane_lay.addWidget(self.pages, 1)

        pane_lay.addWidget(self._build_footer())
        root_layout.addWidget(main_pane, 1)
        self._load_current_values()

    def _apply_codex_qss(self):
        self.setStyleSheet("""
            QDialog {
                background: #FFFFFF;
                color: #0D0D0D;
                font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
            }
            QDialog QLabel, QDialog QPushButton, QDialog QLineEdit, QDialog QComboBox {
                font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
            }
            QWidget#codexSidebar {
                background: #F7F7F8;
                border-right: 1px solid #ECECEF;
            }
            QWidget#codexMainPane {
                background: #FFFFFF;
            }
            QWidget#codexHeader {
                background: #FFFFFF;
                border-bottom: 1px solid #F1F1F5;
            }
            QWidget#codexFooter {
                background: #FCFCFD;
                border-top: 1px solid #ECECEF;
            }
            QFrame#codexCard {
                background: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 10px;
            }
            QLabel#codexSectionLabel {
                font-size: 11.5px;
                font-weight: 600;
                color: #71717A;
                padding: 2px 2px 4px 2px;
                background: transparent;
            }
            QPushButton#codexNavBtn {
                background: transparent;
                color: #52525B;
                border: none;
                border-radius: 8px;
                padding: 0 10px;
                height: 34px;
                text-align: left;
                font-size: 13px;
                font-weight: 400;
            }
            QPushButton#codexNavBtn:hover {
                background: #EFEFF3;
                color: #18181B;
            }
            QPushButton#codexNavBtn:checked {
                background: #E8E8ED;
                color: #0D0D0D;
                font-weight: 600;
            }
            QLineEdit {
                background: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
                padding: 0 10px;
                font-size: 12.5px;
                color: #0D0D0D;
                min-height: 30px;
                max-height: 30px;
            }
            QLineEdit:hover {
                border-color: #D4D4D8;
            }
            QLineEdit:focus {
                border: 1.5px solid #0D0D0D;
                background: #FFFFFF;
            }
            QLineEdit#codexHotkeyEdit {
                background: #FAFAFA;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
                padding: 0 8px;
                font-family: "Cascadia Mono", "Cascadia Code", "Consolas", "Microsoft YaHei UI", monospace;
                font-size: 12px;
                font-weight: 600;
                color: #18181B;
            }
            QLineEdit#codexHotkeyEdit:hover {
                background: #F4F4F5;
                border-color: #D4D4D8;
            }
            QComboBox {
                background: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
                padding: 0 26px 0 10px;
                font-size: 12.5px;
                color: #0D0D0D;
                min-height: 30px;
                max-height: 30px;
            }
            QComboBox:hover {
                border-color: #D4D4D8;
            }
            QComboBox:focus {
                border: 1.5px solid #0D0D0D;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
                background: transparent;
            }
            QComboBox QAbstractItemView {
                background: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 8px;
                padding: 4px;
                color: #0D0D0D;
                selection-background-color: #F4F4F5;
                selection-color: #0D0D0D;
                outline: none;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #E4E4E7;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #0D0D0D;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #0D0D0D;
                width: 12px;
                height: 12px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover {
                background: #F4F4F5;
            }
            QPushButton#codexPrimaryBtn {
                background: #0D0D0D;
                color: #FFFFFF;
                border: none;
                border-radius: 7px;
                padding: 0 16px;
                font-size: 12.5px;
                font-weight: 600;
            }
            QPushButton#codexPrimaryBtn:hover {
                background: #27272A;
            }
            QPushButton#codexPrimaryBtn:pressed {
                background: #18181B;
            }
            QPushButton#codexSecondaryBtn {
                background: #FFFFFF;
                color: #18181B;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
                padding: 0 12px;
                font-size: 12.5px;
                font-weight: 400;
            }
            QPushButton#codexSecondaryBtn:hover {
                background: #F4F4F5;
                border-color: #D4D4D8;
            }
            QPushButton#codexIconBtn {
                background: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 7px;
            }
            QPushButton#codexIconBtn:hover {
                background: #F4F4F5;
                border-color: #D4D4D8;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 4px 2px;
            }
            QScrollBar::handle:vertical {
                background: #D4D4D8;
                min-height: 28px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #A1A1AA;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("codexSidebar")
        sidebar.setFixedWidth(202)
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(12, 16, 12, 14)
        side_lay.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 0, 6, 10)
        brand_row.setSpacing(9)
        brand_icon = QLabel()
        brand_icon.setPixmap(_create_codex_icon("brand", size=24).pixmap(24, 24))
        brand_icon.setFixedSize(24, 24)
        brand_row.addWidget(brand_icon)

        brand_title = QLabel("Capture")
        brand_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #0D0D0D; letter-spacing: -0.2px;")
        brand_row.addWidget(brand_title)

        badge_ver = QLabel(f"v{APP_VERSION}")
        badge_ver.setStyleSheet(f"""
            background: #EAEAEF;
            color: #52525B;
            border-radius: 5px;
            padding: 1px 6px;
            font-family: {CODEX_MONO_FAMILY};
            font-size: 10.5px;
            font-weight: 600;
        """)
        brand_row.addWidget(badge_ver)
        brand_row.addStretch()
        side_lay.addLayout(brand_row)

        self._nav_specs = [
            ("shortcuts", "快捷键", "快捷键", "点击右侧按键槽后直接按下按键或组合键完成录制，按 Delete 或 × 清除", True),
            ("general", "通用与保存", "通用与保存", "管理开机启动、默认存储路径、导出格式与工作区交互偏好", False),
            ("ai", "AI 与翻译", "AI 与翻译", "配置智谱 GLM-OCR 多模态识别与火山方舟豆包大模型翻译", False),
            ("update", "软件更新", "软件更新", "查看版本信息、自动检测策略与 GitHub 官方发行页", False),
        ]
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons = []

        for idx, (icon_key, nav_label, _, _, _) in enumerate(self._nav_specs):
            btn = QPushButton(f"  {nav_label}")
            btn.setObjectName("codexNavBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIconSize(QSize(15, 15))
            btn.setIcon(_create_codex_icon(icon_key, "#0D0D0D" if idx == 0 else "#52525B", 16))
            if idx == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, i=idx: self._switch_page(i))
            self._nav_group.addButton(btn, idx)
            self._nav_buttons.append((btn, icon_key))
            side_lay.addWidget(btn)

        side_lay.addStretch(1)

        side_foot = QLabel("配置实时写入本地\ncapture_settings.json")
        side_foot.setToolTip(SETTINGS_FILE)
        side_foot.setStyleSheet("""
            color: #8E8EA0;
            font-size: 11px;
            padding: 8px 8px 2px 8px;
            line-height: 1.35;
            background: transparent;
        """)
        side_lay.addWidget(side_foot)
        return sidebar

    def _build_header(self):
        header = QWidget()
        header.setObjectName("codexHeader")
        hdr_lay = QHBoxLayout(header)
        hdr_lay.setContentsMargins(26, 16, 26, 14)
        hdr_lay.setSpacing(12)

        hdr_text_col = QVBoxLayout()
        hdr_text_col.setContentsMargins(0, 0, 0, 0)
        hdr_text_col.setSpacing(3)
        self.lbl_page_title = QLabel(self._nav_specs[0][2])
        self.lbl_page_title.setStyleSheet("font-size: 16.5px; font-weight: 600; color: #0D0D0D;")
        hdr_text_col.addWidget(self.lbl_page_title)

        self.lbl_page_desc = QLabel(self._nav_specs[0][3])
        self.lbl_page_desc.setStyleSheet("font-size: 12px; color: #71717A;")
        hdr_text_col.addWidget(self.lbl_page_desc)
        hdr_lay.addLayout(hdr_text_col, 1)

        self.btn_reset_hk = QPushButton("恢复默认快捷键")
        self.btn_reset_hk.setObjectName("codexSecondaryBtn")
        self.btn_reset_hk.setFixedHeight(30)
        self.btn_reset_hk.setCursor(Qt.PointingHandCursor)
        self.btn_reset_hk.clicked.connect(self._reset_default_hotkeys)
        hdr_lay.addWidget(self.btn_reset_hk, 0, Qt.AlignVCenter)
        return header

    def _add_section_label(self, parent_layout, title):
        lbl = QLabel(title)
        lbl.setObjectName("codexSectionLabel")
        parent_layout.addWidget(lbl)

    def _build_hotkeys_page(self):
        p0_scroll, p0_lay = self._make_scroll_page()

        self._add_section_label(p0_lay, "核心截图与录制")
        card_hk_core = CodexCard()
        self.input_hk_snip = HotkeyRecorderWidget("默认 F1")
        card_hk_core.add_row("屏幕截图", "框选屏幕区域进行标注、复制、保存或贴图", self.input_hk_snip)
        self.input_hk_ocr = HotkeyRecorderWidget("默认 F2")
        card_hk_core.add_row("识图与翻译", "选取屏幕文字区域进行 OCR 提取与智能翻译", self.input_hk_ocr)
        self.input_hk_pin = HotkeyRecorderWidget("默认 F3")
        card_hk_core.add_row("贴图置顶", "将剪贴板内容或所选区域作为悬浮贴图置顶", self.input_hk_pin)
        self.input_hk_rec = HotkeyRecorderWidget("默认 F4")
        card_hk_core.add_row("屏幕录制", "录制选定区域为 MP4 高清视频或 GIF 动图", self.input_hk_rec)
        p0_lay.addWidget(card_hk_core)

        self._add_section_label(p0_lay, "效率工具与窗口")
        card_hk_tools = CodexCard()
        self.input_hk_drawer = HotkeyRecorderWidget("默认 Ctrl+1")
        card_hk_tools.add_row("贴图储藏柜总开关", "彻底开启或禁用右侧磁吸贴图收纳抽屉", self.input_hk_drawer)
        self.input_hk_toggle_pins = HotkeyRecorderWidget("默认 Ctrl+Alt+H")
        card_hk_tools.add_row("隐藏/恢复全部贴图", "临时一键隐藏桌面贴图与抽屉，再次按下全部恢复原位", self.input_hk_toggle_pins)
        self.input_hk_paste_pin = HotkeyRecorderWidget("默认 Ctrl+Alt+V")
        card_hk_tools.add_row("剪贴板图片直接贴图", "直接把剪贴板最新复制的图片或图片文件置顶贴图", self.input_hk_paste_pin)
        self.input_hk_restore_click_through = HotkeyRecorderWidget("默认 Ctrl+Alt+T")
        card_hk_tools.add_row("解除贴图鼠标穿透", "一键恢复所有开启鼠标穿透的桌面贴图，防止无法点击关闭", self.input_hk_restore_click_through)
        self.input_hk_pin_border = HotkeyRecorderWidget("点击录制")
        card_hk_tools.add_row("切换贴图边框", "一键开启或关闭全部桌面贴图的轮廓边框", self.input_hk_pin_border)
        self.input_hk_win_pin = HotkeyRecorderWidget("点击录制")
        card_hk_tools.add_row("窗口置顶 (DeskPin)", "将当前鼠标所在应用窗口固定在最前端", self.input_hk_win_pin)
        self.input_hk_scroll = HotkeyRecorderWidget("点击录制")
        card_hk_tools.add_row("滚动长截图", "自动向下滚动拼接超长网页或文档内容", self.input_hk_scroll)
        self.input_hk_history = HotkeyRecorderWidget("点击录制")
        card_hk_tools.add_row("截图历史记录", "快速浏览、重新编辑、复制或另存历史截图", self.input_hk_history)
        self.input_hk_settings = HotkeyRecorderWidget("点击录制")
        card_hk_tools.add_row("打开系统设置", "从后台随时快速唤出设置面板", self.input_hk_settings)
        p0_lay.addWidget(card_hk_tools)

        self._add_section_label(p0_lay, "截图底部工具栏工具（仅截图编辑时生效，默认不绑定）")
        card_hk_toolbar = CodexCard()
        self.toolbar_hk_inputs = {}
        for cfg_key, _action_id, _kind, label, desc in TOOLBAR_TOOL_HOTKEY_SPECS:
            recorder = HotkeyRecorderWidget("点击录制")
            card_hk_toolbar.add_row(label, desc, recorder)
            self.toolbar_hk_inputs[cfg_key] = recorder
        p0_lay.addWidget(card_hk_toolbar)

        for _hk_widget in self._all_hotkey_widgets():
            _hk_widget.recordingStateChanged.connect(self._on_hotkey_recording_state_changed)

        p0_lay.addStretch(1)
        return p0_scroll

    def _build_general_page(self):
        p1_scroll, p1_lay = self._make_scroll_page()

        self._add_section_label(p1_lay, "保存与导出")
        card_save = CodexCard()
        dir_wrap = QWidget()
        dir_wrap.setObjectName("codexDirWrap")
        dir_wrap.setStyleSheet("QWidget#codexDirWrap { background: transparent; }")
        dir_wrap.setFixedWidth(295)
        dir_box = QHBoxLayout(dir_wrap)
        dir_box.setContentsMargins(0, 0, 0, 0)
        dir_box.setSpacing(6)
        self.input_save_dir = QLineEdit()
        dir_box.addWidget(self.input_save_dir, 1)
        btn_browse_dir = QPushButton("浏览...")
        btn_browse_dir.setObjectName("codexSecondaryBtn")
        btn_browse_dir.setFixedHeight(30)
        btn_browse_dir.setCursor(Qt.PointingHandCursor)
        btn_browse_dir.clicked.connect(self._browse_save_dir)
        dir_box.addWidget(btn_browse_dir)
        card_save.add_row("默认保存目录", "另存与自动备份副本的本地存放文件夹", dir_wrap)

        self.cb_auto_save = CodexSwitch()
        card_save.add_row("静默自动保存", "截图或复制完成后，自动在后台保存一份副本到默认目录", self.cb_auto_save)

        self.combo_format = CodexComboBox()
        self.combo_format.setFixedWidth(185)
        self.combo_format.addItems(["PNG (*.png)", "JPEG (*.jpg)", "WebP (*.webp)"])
        card_save.add_row("默认输出格式", "自动保存与快捷导出使用的图像编码格式", self.combo_format)

        qual_wrap = QWidget()
        qual_wrap.setObjectName("codexQualWrap")
        qual_wrap.setStyleSheet("QWidget#codexQualWrap { background: transparent; }")
        qual_wrap.setFixedWidth(215)
        qual_box = QHBoxLayout(qual_wrap)
        qual_box.setContentsMargins(0, 0, 0, 0)
        qual_box.setSpacing(10)
        self.slider_quality = CodexSlider(Qt.Horizontal)
        self.slider_quality.setRange(70, 100)
        self.slider_quality.setValue(95)
        self.lbl_qual_val = QLabel("95%")
        self.lbl_qual_val.setFixedWidth(40)
        self.lbl_qual_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_qual_val.setStyleSheet(
            f"QLabel {{ font-family: {CODEX_MONO_FAMILY}; font-size: 12px; font-weight: 600; color: #0D0D0D; }}"
        )
        self.slider_quality.valueChanged.connect(lambda v: self.lbl_qual_val.setText(f"{v}%"))
        qual_box.addWidget(self.slider_quality, 1)
        qual_box.addWidget(self.lbl_qual_val)
        card_save.add_row("有损压缩画质", "应用于 JPEG / WebP 格式输出质量（70% - 100%）", qual_wrap)
        p1_lay.addWidget(card_save)

        self._add_section_label(p1_lay, "启动与工作区交互")
        card_ux = CodexCard()
        self.cb_auto_start = CodexSwitch()
        card_ux.add_row("开机自启动", "随 Windows 系统登录自动在后台静默启动（仅写入当前用户启动项）", self.cb_auto_start)
        self.cb_show_mode_hints = CodexSwitch()
        card_ux.add_row("快捷操作指引条", "识图或录屏模式显示顶部提示条（支持智能避让与拖拽隐藏）", self.cb_show_mode_hints)
        self.cb_drawer_hover = CodexSwitch()
        card_ux.add_row("开启贴图储藏柜", "在屏幕边缘启用磁吸贴图抽屉，可随时按快捷键彻底开关", self.cb_drawer_hover)
        self.cb_pin_border = CodexSwitch()
        card_ux.add_row("显示贴图边框", "为所有桌面贴图绘制清晰轮廓边框，便于与背景窗口区分", self.cb_pin_border)
        p1_lay.addWidget(card_ux)
        p1_lay.addStretch(1)
        return p1_scroll

    @staticmethod
    def _make_secret_key_input(placeholder):
        wrap = QWidget()
        wrap.setFixedWidth(275)
        box = QHBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setPlaceholderText(placeholder)
        box.addWidget(edit, 1)
        btn = QPushButton()
        btn.setObjectName("codexIconBtn")
        btn.setFixedSize(30, 30)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(_create_codex_icon("eye", "#52525B", 16))

        def _toggle():
            is_pwd = edit.echoMode() == QLineEdit.Password
            edit.setEchoMode(QLineEdit.Normal if is_pwd else QLineEdit.Password)
            btn.setIcon(_create_codex_icon("eye_off" if is_pwd else "eye", "#0D0D0D" if is_pwd else "#52525B", 16))

        btn.clicked.connect(_toggle)
        box.addWidget(btn)
        return wrap, edit

    def _build_ai_page(self):
        p2_scroll, p2_lay = self._make_scroll_page()

        self._add_section_label(p2_lay, "智谱 GLM-OCR 文字识别")
        card_ocr = CodexCard()
        self.cb_ai_ocr = CodexSwitch()
        card_ocr.add_row("启用 AI 多模态识图", "开启后优先使用智谱大模型识别复杂排版、公式与代码片段", self.cb_ai_ocr)
        self.cb_ocr_sea_langs = CodexSwitch()
        card_ocr.add_row("启用东南亚语言扩展 (泰语/菲律宾语)", "默认仅使用中英引擎防误识；需离线识别泰文或菲律宾文时再开启", self.cb_ocr_sea_langs)
        key_ocr_wrap, self.input_zhipu_key = self._make_secret_key_input("输入智谱 API Key (bigmodel.cn)")
        card_ocr.add_row("智谱 API Key", "用于鉴权访问 GLM-OCR 接口（本地 DPAPI 加密保存）", key_ocr_wrap)
        self.input_ocr_model = QLineEdit()
        self.input_ocr_model.setFixedWidth(240)
        self.input_ocr_model.setPlaceholderText("默认 glm-ocr")
        card_ocr.add_row("识图模型名称", "填写模型标识，留空则默认使用 glm-ocr", self.input_ocr_model)
        p2_lay.addWidget(card_ocr)

        self._add_section_label(p2_lay, "火山方舟豆包大模型翻译")
        card_trans = CodexCard()
        self.cb_ai_trans = CodexSwitch()
        card_trans.add_row("启用 AI 智能翻译", "利用豆包大模型结合语境精准翻译截屏文字与图片文本", self.cb_ai_trans)
        key_trans_wrap, self.input_volc_key = self._make_secret_key_input("输入火山方舟 ARK_API_KEY")
        card_trans.add_row("火山方舟 API Key", "ARK_API_KEY 密钥凭证（本地 DPAPI 加密保存）", key_trans_wrap)
        self.input_volc_model = QLineEdit()
        self.input_volc_model.setFixedWidth(240)
        self.input_volc_model.setPlaceholderText("例如 ep-20260629102203-cfllm")
        card_trans.add_row("推理接入点 ID", "火山引擎方舟控制台创建的应用 Endpoint ID", self.input_volc_model)
        self.combo_trans_display = CodexComboBox()
        self.combo_trans_display.setFixedWidth(220)
        self.combo_trans_display.addItem("双栏对照（原图 / 译文）", "dialog")
        self.combo_trans_display.addItem("单图覆盖（直接看译文）", "overlay")
        card_trans.add_row("翻译结果视图", "一键识图翻译完成后的默认结果展示模式", self.combo_trans_display)
        p2_lay.addWidget(card_trans)
        p2_lay.addStretch(1)
        return p2_scroll

    def _build_update_page(self):
        p3_scroll, p3_lay = self._make_scroll_page()

        self._add_section_label(p3_lay, "版本与更新通道")
        card_up = CodexCard()
        ver_pill = QLabel(f"v{APP_VERSION}")
        ver_pill.setObjectName("codexVerPill")
        ver_pill.setStyleSheet(f"""
            QLabel#codexVerPill {{
                background: #F4F4F5;
                border: 1px solid #E4E4E7;
                border-radius: 6px;
                padding: 3px 10px;
                font-family: {CODEX_MONO_FAMILY};
                font-size: 12px;
                font-weight: 600;
                color: #0D0D0D;
            }}
        """)
        card_up.add_row("当前运行版本", "Murioki Capture 桌面版核心程序", ver_pill)

        self.cb_auto_update = CodexSwitch()
        card_up.add_row("启动时自动检查更新", "后台静默检索 GitHub Releases（每 24 小时最多检查一次）", self.cb_auto_update)

        up_btns_wrap = QWidget()
        up_btns_wrap.setObjectName("codexUpBtns")
        up_btns_wrap.setStyleSheet("QWidget#codexUpBtns { background: transparent; }")
        up_btn_row = QHBoxLayout(up_btns_wrap)
        up_btn_row.setContentsMargins(0, 0, 0, 0)
        up_btn_row.setSpacing(8)
        btn_check_update = QPushButton("检查更新")
        btn_check_update.setObjectName("codexPrimaryBtn")
        btn_check_update.setFixedHeight(30)
        btn_check_update.setCursor(Qt.PointingHandCursor)
        btn_check_update.clicked.connect(self._check_updates_from_settings)
        up_btn_row.addWidget(btn_check_update)

        btn_release_page = QPushButton("GitHub 发布页")
        btn_release_page.setObjectName("codexSecondaryBtn")
        btn_release_page.setFixedHeight(30)
        btn_release_page.setCursor(Qt.PointingHandCursor)
        btn_release_page.clicked.connect(lambda: webbrowser.open(GITHUB_RELEASES_PAGE))
        up_btn_row.addWidget(btn_release_page)
        card_up.add_row("手动检查更新", "发现新版后支持直接查看发行说明并一键下载自动替换重启", up_btns_wrap)
        p3_lay.addWidget(card_up)
        p3_lay.addStretch(1)
        return p3_scroll

    def _build_footer(self):
        footer = QWidget()
        footer.setObjectName("codexFooter")
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(26, 12, 26, 12)
        foot_lay.setSpacing(10)

        foot_hint = QLabel("点击「保存并应用」后，热键与面板配置将立即生效")
        foot_hint.setStyleSheet("font-size: 11.5px; color: #8E8EA0; background: transparent;")
        foot_lay.addWidget(foot_hint)
        foot_lay.addStretch(1)

        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("codexSecondaryBtn")
        btn_cancel.setFixedHeight(34)
        btn_cancel.setMinimumWidth(78)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self.close)
        foot_lay.addWidget(btn_cancel)

        btn_save = QPushButton("保存并应用")
        btn_save.setObjectName("codexPrimaryBtn")
        btn_save.setFixedHeight(34)
        btn_save.setMinimumWidth(108)
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self.save_and_apply)
        foot_lay.addWidget(btn_save)
        return footer

    def _make_scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setObjectName("codexViewport")
        scroll.viewport().setStyleSheet("QWidget#codexViewport { background: transparent; }")
        inner = QWidget()
        inner.setObjectName("codexPageInner")
        inner.setStyleSheet("QWidget#codexPageInner { background: transparent; }")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(26, 18, 26, 20)
        lay.setSpacing(12)
        scroll.setWidget(inner)
        return scroll, lay

    def _switch_page(self, index):
        if 0 <= index < len(self._nav_specs):
            self.pages.setCurrentIndex(index)
            _, _, title, desc, show_reset = self._nav_specs[index]
            self.lbl_page_title.setText(title)
            self.lbl_page_desc.setText(desc)
            self.btn_reset_hk.setVisible(show_reset)
            for idx, (btn, icon_key) in enumerate(self._nav_buttons):
                btn.setIcon(_create_codex_icon(icon_key, "#0D0D0D" if idx == index else "#52525B", 16))

    def _all_hotkey_widgets(self):
        widgets = [
            self.input_hk_snip,
            self.input_hk_ocr,
            self.input_hk_pin,
            self.input_hk_rec,
            self.input_hk_drawer,
            self.input_hk_toggle_pins,
            self.input_hk_paste_pin,
            self.input_hk_restore_click_through,
            self.input_hk_pin_border,
            self.input_hk_win_pin,
            self.input_hk_scroll,
            self.input_hk_history,
            self.input_hk_settings,
        ]
        widgets.extend(self.toolbar_hk_inputs.values())
        return widgets

    def _on_hotkey_recording_state_changed(self, _recording):
        if not self.hotkey_mgr:
            return
        any_recording = any(w.edit._recording for w in self._all_hotkey_widgets())
        if any_recording:
            self.hotkey_mgr.suspend_hotkeys()
        else:
            self.hotkey_mgr.resume_hotkeys()

    def done(self, result):
        for w in self._all_hotkey_widgets():
            if w.edit._recording:
                w.edit.stop_recording(restore=True)
        if self.hotkey_mgr:
            self.hotkey_mgr.resume_hotkeys()
        super().done(result)

    def _load_current_values(self):
        s = load_settings()
        self.input_hk_snip.setText(s.get("hotkey_snip", "F1"))
        self.input_hk_ocr.setText(s.get("hotkey_ocr", "F2"))
        self.input_hk_pin.setText(s.get("hotkey_pin", "F3"))
        self.input_hk_rec.setText(s.get("hotkey_record", "F4"))
        self.input_hk_win_pin.setText(s.get("hotkey_window_pin", ""))
        self.input_hk_scroll.setText(s.get("hotkey_scroll", ""))
        self.input_hk_history.setText(s.get("hotkey_history", ""))
        self.input_hk_settings.setText(s.get("hotkey_settings", ""))
        self.input_hk_drawer.setText(s.get("hotkey_drawer", "Ctrl+1"))
        self.input_hk_toggle_pins.setText(s.get("hotkey_toggle_pins", "Ctrl+Alt+H"))
        self.input_hk_paste_pin.setText(s.get("hotkey_paste_pin", "Ctrl+Alt+V"))
        self.input_hk_restore_click_through.setText(s.get("hotkey_restore_click_through", "Ctrl+Alt+T"))
        self.input_hk_pin_border.setText(s.get("hotkey_pin_border", ""))
        for cfg_key, recorder in self.toolbar_hk_inputs.items():
            recorder.setText(s.get(cfg_key, ""))

        self.input_save_dir.setText(s.get("default_save_dir", os.path.join(os.path.expanduser("~"), "Pictures")))
        self.input_save_dir.setCursorPosition(0)
        self.cb_auto_save.setChecked(bool(s.get("auto_save_enabled", False)))
        self.cb_auto_start.setChecked(is_autostart_enabled() if sys.platform == "win32" else bool(s.get("auto_start", False)))
        self.cb_show_mode_hints.setChecked(bool(s.get("show_mode_hints", True)))
        self.cb_drawer_hover.setChecked(bool(s.get("drawer_hover_enabled", True)))
        self.cb_pin_border.setChecked(bool(s.get("pin_border_enabled", False)))
        self.cb_auto_update.setChecked(bool(s.get("auto_check_update", True)))

        fmt = s.get("save_format", "png").lower()
        if "jp" in fmt: self.combo_format.setCurrentIndex(1)
        elif "webp" in fmt: self.combo_format.setCurrentIndex(2)
        else: self.combo_format.setCurrentIndex(0)

        qual = int(s.get("save_quality", 95))
        self.slider_quality.setValue(qual)
        self.lbl_qual_val.setText(f"{qual}%")

        self.input_zhipu_key.setText(s.get("zhipu_api_key", ""))
        self.input_ocr_model.setText(s.get("glm_ocr_model", DEFAULT_ZHIPU_OCR_MODEL))
        self.cb_ai_ocr.setChecked(bool(s.get("use_ai_ocr", False)))
        self.cb_ocr_sea_langs.setChecked(bool(s.get("ocr_enable_sea_langs", False)))

        self.input_volc_key.setText(s.get("volc_api_key", ""))
        self.input_volc_model.setText(s.get("volc_model", DEFAULT_VOLC_TRANS_MODEL))
        self.cb_ai_trans.setChecked(bool(s.get("use_ai_translation", False)))

        display_mode = str(s.get("translation_display_mode", "dialog") or "dialog")
        idx_display = self.combo_trans_display.findData(display_mode)
        if idx_display >= 0:
            self.combo_trans_display.setCurrentIndex(idx_display)

    def _reset_default_hotkeys(self):
        self.input_hk_snip.setText("F1")
        self.input_hk_ocr.setText("F2")
        self.input_hk_pin.setText("F3")
        self.input_hk_rec.setText("F4")
        self.input_hk_win_pin.setText("")
        self.input_hk_scroll.setText("")
        self.input_hk_history.setText("")
        self.input_hk_settings.setText("")
        self.input_hk_drawer.setText("Ctrl+1")
        self.input_hk_toggle_pins.setText("Ctrl+Alt+H")
        self.input_hk_paste_pin.setText("Ctrl+Alt+V")
        self.input_hk_restore_click_through.setText("Ctrl+Alt+T")
        self.input_hk_pin_border.setText("")
        for recorder in self.toolbar_hk_inputs.values():
            recorder.setText("")

    def _browse_save_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "选择默认保存目录", self.input_save_dir.text())
        if chosen:
            self.input_save_dir.setText(chosen)
            self.input_save_dir.setCursorPosition(0)

    def _check_updates_from_settings(self):
        save_settings({"auto_check_update": self.cb_auto_update.isChecked()})
        main_app = self.parent()
        if main_app is not None and not hasattr(main_app, "check_for_updates") and hasattr(main_app, "main_app_ref"):
            main_app = main_app.main_app_ref
        self.accept()
        if main_app is not None and hasattr(main_app, "check_for_updates"):
            QTimer.singleShot(150, lambda: main_app.check_for_updates(manual=True))

    def save_and_apply(self):
        fmt_idx = self.combo_format.currentIndex()
        fmt_str = "png" if fmt_idx == 0 else ("jpg" if fmt_idx == 1 else "webp")

        global_entries = [
            ("hotkey_snip", "屏幕截图", normalize_hotkey_str(self.input_hk_snip.text()) or "F1"),
            ("hotkey_ocr", "识图与翻译", normalize_hotkey_str(self.input_hk_ocr.text()) or "F2"),
            ("hotkey_pin", "贴图置顶", normalize_hotkey_str(self.input_hk_pin.text()) or "F3"),
            ("hotkey_record", "屏幕录制", normalize_hotkey_str(self.input_hk_rec.text()) or "F4"),
            ("hotkey_window_pin", "窗口置顶 (DeskPin)", normalize_hotkey_str(self.input_hk_win_pin.text())),
            ("hotkey_scroll", "滚动长截图", normalize_hotkey_str(self.input_hk_scroll.text())),
            ("hotkey_history", "截图历史记录", normalize_hotkey_str(self.input_hk_history.text())),
            ("hotkey_settings", "打开系统设置", normalize_hotkey_str(self.input_hk_settings.text())),
            ("hotkey_drawer", "贴图储藏柜总开关", normalize_hotkey_str(self.input_hk_drawer.text()) or "Ctrl+1"),
            ("hotkey_toggle_pins", "隐藏/恢复全部贴图", normalize_hotkey_str(self.input_hk_toggle_pins.text())),
            ("hotkey_paste_pin", "剪贴板图片直接贴图", normalize_hotkey_str(self.input_hk_paste_pin.text())),
            ("hotkey_restore_click_through", "解除贴图鼠标穿透", normalize_hotkey_str(self.input_hk_restore_click_through.text()) or "Ctrl+Alt+T"),
            ("hotkey_pin_border", "切换贴图边框", normalize_hotkey_str(self.input_hk_pin_border.text())),
        ]

        seen_global = {}
        for _k, g_label, hk in global_entries:
            if not hk:
                continue
            if hk in seen_global:
                QMessageBox.warning(
                    self, "快捷键冲突",
                    f"全局快捷键「{hk}」被同时分配给了「{seen_global[hk]}」和「{g_label}」，请修改其中一个。"
                )
                return
            seen_global[hk] = g_label

        reserved_screenshot_keys = {"Esc", "Ctrl+C", "Ctrl+Z", "Ctrl+Y", "Ctrl+S", "Ctrl+A", "Enter"}
        toolbar_entries = []
        seen_toolbar = {}
        for cfg_key, _action_id, _kind, t_label, _desc in TOOLBAR_TOOL_HOTKEY_SPECS:
            hk = normalize_hotkey_str(self.toolbar_hk_inputs[cfg_key].text())
            if hk:
                if hk in seen_toolbar:
                    QMessageBox.warning(
                        self, "快捷键冲突",
                        f"截图工具栏快捷键「{hk}」被同时分配给了「{seen_toolbar[hk]}」和「{t_label}」，请修改其中一个。"
                    )
                    return
                if hk in seen_global:
                    QMessageBox.warning(
                        self, "快捷键冲突",
                        f"截图工具「{t_label}」的快捷键「{hk}」与全局快捷键「{seen_global[hk]}」冲突（会被全局热键优先拦截），请更换其它快捷键。"
                    )
                    return
                if hk in reserved_screenshot_keys:
                    QMessageBox.warning(
                        self, "快捷键冲突",
                        f"截图工具「{t_label}」的快捷键「{hk}」与系统内置截图快捷键冲突，请更换其它快捷键。"
                    )
                    return
                seen_toolbar[hk] = t_label
            toolbar_entries.append((cfg_key, hk))

        want_autostart = self.cb_auto_start.isChecked()
        actual_autostart = want_autostart
        if sys.platform == "win32":
            ok_auto, err_auto = set_autostart_enabled(want_autostart)
            if not ok_auto:
                QMessageBox.warning(self, "开机自启动设置失败", f"无法修改系统开机自启动设置：\n{err_auto}")
                actual_autostart = is_autostart_enabled()
                self.cb_auto_start.setChecked(actual_autostart)

        new_settings = {
            "default_save_dir": self.input_save_dir.text().strip(),
            "auto_save_enabled": self.cb_auto_save.isChecked(),
            "auto_start": actual_autostart,
            "show_mode_hints": self.cb_show_mode_hints.isChecked(),
            "drawer_hover_enabled": self.cb_drawer_hover.isChecked(),
            "pin_border_enabled": self.cb_pin_border.isChecked(),
            "save_format": fmt_str,
            "save_quality": self.slider_quality.value(),
            "zhipu_api_key": self.input_zhipu_key.text().strip(),
            "glm_ocr_model": self.input_ocr_model.text().strip() or DEFAULT_ZHIPU_OCR_MODEL,
            "volc_api_key": self.input_volc_key.text().strip(),
            "volc_model": self.input_volc_model.text().strip() or DEFAULT_VOLC_TRANS_MODEL,
            "use_ai_translation": self.cb_ai_trans.isChecked(),
            "use_ai_ocr": self.cb_ai_ocr.isChecked(),
            "ocr_enable_sea_langs": self.cb_ocr_sea_langs.isChecked(),
            "translation_display_mode": self.combo_trans_display.currentData(),
            "auto_check_update": self.cb_auto_update.isChecked(),
        }
        for k, _label, val in global_entries:
            new_settings[k] = val
        for k, val in toolbar_entries:
            new_settings[k] = val

        save_settings(new_settings)
        main_app = self.parent()
        if main_app is not None and not hasattr(main_app, "pin_drawer") and hasattr(main_app, "main_app_ref"):
            main_app = main_app.main_app_ref
        if main_app is not None and hasattr(main_app, "pin_drawer") and main_app.pin_drawer:
            main_app.pin_drawer.set_feature_enabled(self.cb_drawer_hover.isChecked())
        if main_app is not None and hasattr(main_app, "set_pin_border_enabled"):
            main_app.set_pin_border_enabled(self.cb_pin_border.isChecked(), show_toast=False)
        if main_app is not None and hasattr(main_app, "toolbar") and isinstance(main_app.toolbar, SnipasteToolbar):
            main_app.toolbar.refresh_tooltips(new_settings)
        if main_app is not None and hasattr(main_app, "_reload_toolbar_shortcuts"):
            main_app._reload_toolbar_shortcuts(new_settings)
        if self.hotkey_mgr:
            self.hotkey_mgr.reload_hotkeys()
        if main_app is not None and getattr(main_app, "_tray_icon", None) is not None:
            main_app._tray_icon._refresh_menu_labels()
        QMessageBox.information(self, "设置已保存", "偏好设置已成功保存并立即生效！")
        self.accept()

# ==========================================
# SCROLL CAPTURE SYSTEM
# ==========================================
class CaptureFrame(QWidget):
    def __init__(self, rect):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(rect)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor(204, 153, 68), 3))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)


class ScrollCaptureThread(QThread):
    # 发射在内存中捕获的所有 BGR 图像帧列表（彻底杜绝磁盘 I/O 磨损与卡顿）
    capture_finished = pyqtSignal(list)

    def __init__(self, rect):
        super().__init__()
        self.rect = rect
        self.is_recording = True
        self.frame_count = 0
        self.captured_frames = []

    def grab_frame_bgr(self, sct, monitor):
        img = sct.grab(monitor)
        raw = np.array(img)
        # mss 返回的是 BGRA 格式，转换为 BGR 供 cv2 拼接
        return cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)

    def run(self):
        try:
            with mss.mss() as sct:
                border = 3
                monitor = {
                    "top": self.rect.y() + border,
                    "left": self.rect.x() + border,
                    "width": max(1, self.rect.width() - (border * 2)),
                    "height": max(1, self.rect.height() - (border * 2))
                }
                first_frame = self.grab_frame_bgr(sct, monitor)
                self.captured_frames.append(first_frame)
                self.frame_count += 1

                center = self.rect.center()
                identical_count = 0

                while self.is_recording and self.frame_count < SCROLL_MAX_FRAMES:
                    QCursor.setPos(QPoint(center.x(), center.y()))
                    send_mouse_wheel(SCROLL_WHEEL_DELTA)
                    time.sleep(SCROLL_SETTLE_DELAY)
                    if not self.is_recording:
                        break

                    new_frame = self.grab_frame_bgr(sct, monitor)
                    
                    # 智能到底检测：比对相邻两帧画面差异
                    if len(self.captured_frames) > 0:
                        prev_frame = self.captured_frames[-1]
                        if prev_frame.shape == new_frame.shape:
                            # 提取底部 35% 区域对比差异
                            h = prev_frame.shape[0]
                            sample_h = max(10, int(h * 0.35))
                            diff = np.mean(np.abs(new_frame[-sample_h:, :].astype(np.float32) - prev_frame[-sample_h:, :].astype(np.float32)))
                            if diff < 1.2:
                                identical_count += 1
                                if identical_count >= 3:
                                    logging.info("滚动截图检测到连续 3 次滚动无画面位移，判定到达页面底部，自动停止")
                                    break
                            else:
                                identical_count = 0

                    self.captured_frames.append(new_frame)
                    self.frame_count += 1
                    time.sleep(SCROLL_CAPTURE_INTERVAL)
        except Exception as e:
            logging.error("滚动截图线程发生错误: %s", e, exc_info=True)
        finally:
            self.is_recording = False
            self.capture_finished.emit(self.captured_frames)

    def stop(self):
        self.is_recording = False

# ==========================================
# SCREEN RECORDING SYSTEM (F4)
# ==========================================
def convert_video_to_gif(video_path, gif_path, max_fps=15, scale=1.0):
    """
    使用 OpenCV 解码帧并结合 Pillow 将视频高效转换为高质量自适应调色板 GIF。
    无需安装 ffmpeg，内存占用低，耗时极短。支持自定义帧率与缩放尺寸。
    """
    if not os.path.exists(video_path):
        return False
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return False

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(video_fps / max(1, max_fps))))
    target_fps = video_fps / step
    duration_ms = int(1000.0 / target_fps)

    frames = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % step == 0:
            if scale < 0.99:
                nw = max(16, int(frame.shape[1] * scale))
                nh = max(16, int(frame.shape[0] * scale))
                if nw % 2 != 0: nw -= 1
                if nh % 2 != 0: nh -= 1
                frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            p_img = pil_img.convert('P', palette=Image.ADAPTIVE, colors=128)
            frames.append(p_img)
        idx += 1
    cap.release()

    if not frames:
        return False

    os.makedirs(os.path.dirname(os.path.abspath(gif_path)), exist_ok=True)
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True
    )
    return True


class ScreenRecorderThread(QThread):
    time_tick = pyqtSignal(int)
    recording_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, rect, quality_key='original'):
        super().__init__()
        self.rect = rect
        self.quality_key = quality_key
        self.is_running = False
        self.is_paused = False
        self.temp_video_path = None
        self.frame_count = 0
        self.fps = 30
        self.duration = 0.0

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        return self.is_paused

    def stop(self):
        self.is_running = False

    def _compute_physical_capture_rect(self):
        """将 Qt 逻辑选区换算为 Win32 物理像素坐标，确保高分屏/多屏录屏不偏位也不越界补黑。"""
        lx, ly = self.rect.x(), self.rect.y()
        lw, lh = self.rect.width(), self.rect.height()
        px1, py1 = _logical_point_to_phys(lx, ly)
        dpr = 1.0
        try:
            screen = QApplication.screenAt(QPoint(lx + lw // 2, ly + lh // 2)) or QApplication.primaryScreen()
            if screen is not None:
                dpr = float(screen.devicePixelRatio() or 1.0)
        except Exception as e:
            logging.debug("获取录屏屏幕 DPR 失败，使用默认 1.0: %s", e)
        pw = max(16, int(round(lw * dpr)))
        ph = max(16, int(round(lh * dpr)))
        return px1, py1, pw, ph

    def run(self):
        try:
            os.makedirs(REC_TEMP_FOLDER, exist_ok=True)
            preset = RECORD_QUALITY_PRESETS.get(self.quality_key, RECORD_QUALITY_PRESETS['high'])
            scale = preset['scale']
            self.fps = preset['fps']
            frame_interval = 1.0 / self.fps

            with mss.mss() as sct:
                phys_x, phys_y, phys_w, phys_h = self._compute_physical_capture_rect()
                # 将物理抓屏框限制在虚拟桌面范围内，避免贴边或越界导致 mss 填充黑边
                vmon = sct.monitors[0]
                vx0, vy0 = int(vmon["left"]), int(vmon["top"])
                vx1, vy1 = vx0 + int(vmon["width"]), vy0 + int(vmon["height"])
                cx0 = max(vx0, min(phys_x, vx1 - 16))
                cy0 = max(vy0, min(phys_y, vy1 - 16))
                cx1 = min(vx1, max(cx0 + 16, phys_x + phys_w))
                cy1 = min(vy1, max(cy0 + 16, phys_y + phys_h))
                cap_w = max(16, cx1 - cx0)
                cap_h = max(16, cy1 - cy0)

                # OpenCV 要求写入尺寸必须为偶数
                out_w = int(round(cap_w * scale))
                out_h = int(round(cap_h * scale))
                if out_w % 2 != 0: out_w -= 1
                if out_h % 2 != 0: out_h -= 1
                out_w = max(16, out_w)
                out_h = max(16, out_h)

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self.temp_video_path = os.path.join(REC_TEMP_FOLDER, f"rec_{ts}.mp4")

                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                writer = cv2.VideoWriter(self.temp_video_path, fourcc, self.fps, (out_w, out_h))
                if not writer.isOpened():
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    self.temp_video_path = self.temp_video_path.replace('.mp4', '.avi')
                    writer = cv2.VideoWriter(self.temp_video_path, fourcc, self.fps, (out_w, out_h))

                if not writer.isOpened():
                    self.error_occurred.emit("无法初始化 OpenCV VideoWriter 编码器")
                    return

                self.is_running = True
                self.is_paused = False
                self.frame_count = 0
                last_emitted_sec = -1
                next_frame_time = time.time()
                record_start_time = time.time()
                total_paused_time = 0.0
                pause_start_time = 0.0

                monitor = {
                    "top": cy0,
                    "left": cx0,
                    "width": cap_w,
                    "height": cap_h,
                }

                while self.is_running:
                    now = time.time()
                    if self.is_paused:
                        if pause_start_time == 0.0:
                            pause_start_time = now
                        time.sleep(0.04)
                        continue
                    elif pause_start_time > 0.0:
                        total_paused_time += (now - pause_start_time)
                        pause_start_time = 0.0

                    if now < next_frame_time:
                        sleep_dur = next_frame_time - now
                        if sleep_dur > 0.002:
                            time.sleep(sleep_dur - 0.001)
                        continue

                    sct_img = sct.grab(monitor)
                    frame_raw = np.array(sct_img)
                    frame_bgr = cv2.cvtColor(frame_raw, cv2.COLOR_BGRA2BGR)

                    if scale != 1.0 or frame_bgr.shape[1] != out_w or frame_bgr.shape[0] != out_h:
                        frame_bgr = cv2.resize(frame_bgr, (out_w, out_h), interpolation=cv2.INTER_AREA)

                    # 墙钟时间校准：若机器负载高导致轻微掉帧，自动平滑补偿帧，杜绝回放快进失真
                    effective_elapsed = (time.time() - record_start_time) - total_paused_time
                    expected_frames = max(1, int(effective_elapsed * self.fps))
                    frames_behind = expected_frames - self.frame_count
                    write_times = max(1, min(frames_behind, 4))
                    for _ in range(write_times):
                        writer.write(frame_bgr)
                        self.frame_count += 1

                    next_frame_time += frame_interval
                    if time.time() - next_frame_time > frame_interval * 2:
                        next_frame_time = time.time()

                    sec = int(self.frame_count / self.fps)
                    if sec != last_emitted_sec:
                        last_emitted_sec = sec
                        self.time_tick.emit(sec)

                try:
                    writer.release()
                except Exception as _w_err:
                    logging.warning("释放 VideoWriter 异常: %s", _w_err)
                del writer
            self.duration = self.frame_count / self.fps if self.fps > 0 else 0

            info = {
                'video_path': self.temp_video_path,
                'duration': self.duration,
                'width': out_w,
                'height': out_h,
                'frame_count': self.frame_count,
                'fps': self.fps,
                'quality_key': self.quality_key,
                'quality_name': preset['name']
            }
            self.recording_finished.emit(info)
        except Exception as e:
            logging.error("录屏线程发生异常: %s", e, exc_info=True)
            self.error_occurred.emit(str(e))


class RecordBorderFrame(QWidget):
    def __init__(self, rect):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # 将取景框放置在录制区域外侧 (外扩 2 像素)，双重保险彻底杜绝录入视频
        self.border_width = 2
        outer_rect = rect.adjusted(-self.border_width, -self.border_width, self.border_width, self.border_width)
        self.setGeometry(outer_rect)

        self.is_recording = False
        self.blink_state = True
        self.timer = QTimer(self)
        self.timer.setInterval(600)
        self.timer.timeout.connect(self._toggle_blink)
        self.show()

        # Windows 原生底层防录屏穿透 (WDA_EXCLUDEFROMCAPTURE = 0x11)
        # 该 API 指示系统 DWM 在任何屏幕截取/录制时将本窗口完全透明化剔除，屏幕可见但绝不录入视频
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x11)
        except Exception as e:
            logging.debug("设置录屏边框 DisplayAffinity 失败: %s", e)

    def set_recording(self, recording):
        self.is_recording = recording
        if recording:
            self.timer.start()
        else:
            self.timer.stop()
            self.blink_state = True
        self.update()

    def _toggle_blink(self):
        self.blink_state = not self.blink_state
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()

        # 优雅现代的珊瑚红，录制中呼吸，未录制时虚线
        color = QColor(244, 63, 94) if self.blink_state else QColor(244, 63, 94, 90)
        pen = QPen(color, self.border_width, Qt.SolidLine if self.is_recording else Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(0, 0, w - 1, h - 1)

        # 4 个取景角标 (在最外缘)
        cpen = QPen(QColor(244, 63, 94), self.border_width + 1)
        painter.setPen(cpen)
        corner_len = min(16, max(8, w // 8), max(8, h // 8))
        # Top-Left
        painter.drawLine(0, 0, corner_len, 0)
        painter.drawLine(0, 0, 0, corner_len)
        # Top-Right
        painter.drawLine(w - 1, 0, w - 1 - corner_len, 0)
        painter.drawLine(w - 1, 0, w - 1, corner_len)
        # Bottom-Left
        painter.drawLine(0, h - 1, corner_len, h - 1)
        painter.drawLine(0, h - 1, 0, h - 1 - corner_len)
        # Bottom-Right
        painter.drawLine(w - 1, h - 1, w - 1 - corner_len, h - 1)
        painter.drawLine(w - 1, h - 1, w - 1, h - 1 - corner_len)


class RecordingToolbar(FrostedBarWidget):
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    pause_toggled = pyqtSignal(bool)
    cancel_requested = pyqtSignal()
    quality_changed = pyqtSignal(str)

    def __init__(self, rect, default_quality='original', parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.target_rect = rect
        self.is_recording = False
        self.is_paused = False
        self.drag_position = None

        self.setStyleSheet(f"""
            QWidget#recToolbar {{
                background: transparent;
                font-family: {CODEX_FONT_FAMILY};
            }}
            QLabel {{
                font-size: 12px;
                color: #0F172A;
                background: transparent;
            }}
            QComboBox {{
                background: rgba(255, 255, 255, 0.72);
                color: #0F172A;
                border: 1px solid rgba(15, 23, 42, 0.14);
                border-radius: 7px;
                padding: 4px 8px 4px 10px;
                font-size: 11.5px;
                min-height: 18px;
            }}
            QComboBox:hover {{
                border-color: rgba(182, 134, 58, 0.55);
                background: rgba(255, 255, 255, 0.90);
            }}
            QComboBox::drop-down {{
                border: none;
                width: 22px;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                width: 0px;
                height: 0px;
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background: rgba(255, 255, 255, 0.96);
                color: #1D1D1F;
                border: 1px solid rgba(28, 28, 30, 0.14);
                border-radius: 7px;
                selection-background-color: #FAF1DE;
                selection-color: #1D1D1F;
            }}
            QPushButton#recSecondary {{
                background: rgba(255, 255, 255, 0.72);
                color: #0F172A;
                border: 1px solid rgba(15, 23, 42, 0.14);
                border-radius: 7px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#recSecondary:hover {{
                background: rgba(255, 255, 255, 0.92);
                border-color: rgba(15, 23, 42, 0.24);
            }}
            QPushButton#recSecondary:disabled {{
                color: rgba(100, 116, 139, 0.65);
                border-color: rgba(15, 23, 42, 0.08);
                background: rgba(255, 255, 255, 0.42);
            }}
            QPushButton#recDanger {{
                background: rgba(255, 255, 255, 0.72);
                color: #DC2626;
                border: 1px solid rgba(239, 68, 68, 0.30);
                border-radius: 7px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#recDanger:hover {{
                background: rgba(254, 242, 242, 0.92);
                border-color: rgba(239, 68, 68, 0.55);
            }}
        """)
        self.setObjectName("recToolbar")

        pad = self.SHADOW_PAD
        layout = QHBoxLayout(self)
        layout.setContentsMargins(pad + 12, pad + 7, pad + 12, pad + 7)
        layout.setSpacing(8)

        # 录制状态指示红点
        self.dot_label = QLabel("●")
        self.dot_label.setStyleSheet("color: #EF4444; font-size: 13px; font-weight: bold;")
        layout.addWidget(self.dot_label)

        # 录制时间显示
        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet(f"font-family: {CODEX_MONO_FAMILY}; font-size: 13px; font-weight: 700; color: #0F172A; min-width: 44px;")
        layout.addWidget(self.time_label)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setFixedHeight(16)
        sep1.setStyleSheet("background-color: rgba(15, 23, 42, 0.14); max-width: 1px; border: none;")
        layout.addWidget(sep1)

        # 尺寸显示
        self.size_label = QLabel(f"{rect.width()} × {rect.height()}")
        self.size_label.setStyleSheet(f"font-family: {CODEX_MONO_FAMILY}; font-size: 11.5px; color: #475569;")
        layout.addWidget(self.size_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFixedHeight(16)
        sep2.setStyleSheet("background-color: rgba(15, 23, 42, 0.14); max-width: 1px; border: none;")
        layout.addWidget(sep2)

        # 画质预设下拉框（支持 60FPS 极速高刷与 30FPS 均衡预设）
        self.combo_quality = CodexComboBox()
        self.combo_quality.addItem("原画 60FPS (100%)", "p60")
        self.combo_quality.addItem("原画 30FPS (100%)", "p30")
        self.combo_quality.addItem("高清 60FPS (75%)", "high60")
        self.combo_quality.addItem("高清 30FPS (75%)", "high30")
        self.combo_quality.addItem("标清 30FPS (50%)", "standard")
        found_idx = 0
        for i in range(self.combo_quality.count()):
            if self.combo_quality.itemData(i) == default_quality:
                found_idx = i
                break
        self.combo_quality.setCurrentIndex(found_idx)
        self.combo_quality.ensurePolished()
        self.combo_quality.setMinimumWidth(max(158, self.combo_quality.sizeHint().width() + 22))
        self.combo_quality.currentIndexChanged.connect(self._on_quality_index_changed)
        layout.addWidget(self.combo_quality)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.VLine)
        sep3.setFixedHeight(16)
        sep3.setStyleSheet("background-color: rgba(15, 23, 42, 0.14); max-width: 1px; border: none;")
        layout.addWidget(sep3)

        # 开始/完成录制按钮
        self.btn_rec = QPushButton("● 开始录制")
        self.btn_rec.setCursor(Qt.PointingHandCursor)
        self.btn_rec.setStyleSheet("""
            QPushButton {
                background: #0F172A;
                color: #FFFFFF;
                border: none;
                border-radius: 7px;
                padding: 5px 12px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #1E293B; }
        """)
        self.btn_rec.clicked.connect(self._toggle_recording)
        layout.addWidget(self.btn_rec)

        # 暂停/继续按钮
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setObjectName("recSecondary")
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        layout.addWidget(self.btn_pause)

        # 取消按钮
        self.btn_cancel = QPushButton("取消 (Esc)")
        self.btn_cancel.setObjectName("recDanger")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.setToolTip("退出/取消录制 (快捷键 Esc)")
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(self.btn_cancel)

        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self.cancel_requested.emit)

        self.adjustSize()
        self._position_toolbar()

        # 先设置 WDA_EXCLUDEFROMCAPTURE，使毛玻璃采样与视频录制均不拍入控制栏本身
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x11)
        except Exception as e:
            logging.debug("设置录屏工具条 DisplayAffinity 失败: %s", e)

        self.show()
        self.update_frosted_bg()

    def _position_toolbar(self):
        screen_geo = QApplication.primaryScreen().geometry()
        w = self.width()
        h = self.height()
        pad = self.SHADOW_PAD
        x = self.target_rect.center().x() - w // 2
        y = self.target_rect.bottom() + 10 - pad
        if y + h > screen_geo.bottom() - 10 + pad:
            y = self.target_rect.top() - h - 10 + pad
        x = max(10 - pad, min(x, screen_geo.right() - w - 10 + pad))
        y = max(10 - pad, min(y, screen_geo.bottom() - h - 10 + pad))
        self.move(x, y)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.drag_position:
            self.move(event.globalPos() - self.drag_position)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_quality_index_changed(self):
        key = self.combo_quality.currentData()
        self.quality_changed.emit(key)

    def _toggle_recording(self):
        if not self.is_recording:
            self.start_requested.emit()
        else:
            self.btn_rec.setEnabled(False)
            self.stop_requested.emit()

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self.btn_pause.setText("继续" if self.is_paused else "暂停")
        self.pause_toggled.emit(self.is_paused)

    def set_recording_state(self, recording):
        self.is_recording = recording
        self.combo_quality.setEnabled(not recording)
        self.btn_pause.setEnabled(recording)
        if recording:
            self.btn_rec.setText("■ 停止录制")
            self.btn_rec.setStyleSheet("""
                QPushButton {
                    background: #DC2626;
                    color: #FFFFFF;
                    border: none;
                    border-radius: 7px;
                    padding: 5px 12px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover { background: #B91C1C; }
            """)
        else:
            self.btn_rec.setText("● 开始录制")
            self.btn_rec.setStyleSheet("""
                QPushButton {
                    background: #0F172A;
                    color: #FFFFFF;
                    border: none;
                    border-radius: 7px;
                    padding: 5px 12px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover { background: #1E293B; }
            """)

    def update_timer(self, elapsed_seconds):
        m = elapsed_seconds // 60
        s = elapsed_seconds % 60
        self.time_label.setText(f"{m:02d}:{s:02d}")


class RecordingExportDialog(QDialog):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.video_path = info.get('video_path', '')
        self.temp_gif_path = None
        self.cap = None
        self.total_frames = 0
        self.current_frame = 0
        self._current_raw_pixmap = None
        self.fps = info.get('fps', 30.0) or 30.0
        self.is_playing = False

        self.setWindowTitle("屏幕录制 - 导出预览")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(620, 440)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.setStyleSheet(CODEX_DIALOG_BASE_QSS + f"""
            QWidget#recBody {{
                background-color: #FCFCFD;
            }}
            QLabel#recPreviewStage {{
                background-color: #F4F4F5;
                color: #71717A;
                border: 1px solid #E4E4E7;
                border-radius: 10px;
                font-size: 13px;
            }}
            QLabel#recTimeLabel {{
                font-family: {CODEX_MONO_FAMILY};
                font-size: 11.5px;
                color: #71717A;
            }}
            QLabel#recOptLabel {{
                font-size: 12px;
                color: #52525B;
                font-weight: 400;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sec = int(info.get('duration', 0))
        dur_str = f"{sec // 60:02d}:{sec % 60:02d}"
        file_size_bytes = os.path.getsize(self.video_path) if os.path.exists(self.video_path) else 0
        if file_size_bytes >= 1024 * 1024:
            size_str = f"{file_size_bytes / (1024 * 1024):.1f} MB"
        else:
            size_str = f"{file_size_bytes / 1024:.1f} KB"

        # 1. 顶部 Codex 摘要栏
        top_w = QWidget()
        top_w.setObjectName("codexTopBar")
        top_lay = QVBoxLayout(top_w)
        top_lay.setContentsMargins(20, 14, 20, 12)
        top_lay.setSpacing(8)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(VectorIconFactory.create_pixmap("record", QColor("#0D0D0D")).scaled(16, 16, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        ico.setFixedSize(18, 18)
        head_row.addWidget(ico)

        title = QLabel("屏幕录制完成")
        title.setObjectName("codexTitle")
        head_row.addWidget(title)
        head_row.addStretch()

        pills_row = QHBoxLayout()
        pills_row.setSpacing(8)
        for text in [
            f"时长 {dur_str}",
            f"{info.get('width', 0)} × {info.get('height', 0)}",
            f"{info.get('frame_count', 0)} 帧 · {int(self.fps)} fps",
            f"画质：{info.get('quality_name', '原画')}",
            size_str,
        ]:
            pill = QLabel(text)
            pill.setObjectName("codexPill")
            pills_row.addWidget(pill)
        pills_row.addStretch()

        top_lay.addLayout(head_row)
        top_lay.addLayout(pills_row)
        main_layout.addWidget(top_w)

        # 2. 主预览与播放控制区
        body_w = QWidget()
        body_w.setObjectName("recBody")
        body_lay = QVBoxLayout(body_w)
        body_lay.setContentsMargins(20, 16, 20, 16)
        body_lay.setSpacing(12)

        self.preview_label = QLabel()
        self.preview_label.setObjectName("recPreviewStage")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(180)
        body_lay.addWidget(self.preview_label, 1)

        # 播放控制条
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(10)

        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setObjectName("codexSecondaryBtn")
        self.btn_play.setFixedWidth(78)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl_layout.addWidget(self.btn_play)

        self.slider = CodexSlider(Qt.Horizontal)
        self.slider.sliderMoved.connect(self.on_slider_moved)
        ctrl_layout.addWidget(self.slider, 1)

        self.time_lbl = QLabel(f"00:00 / {dur_str}")
        self.time_lbl.setObjectName("recTimeLabel")
        ctrl_layout.addWidget(self.time_lbl)

        body_lay.addLayout(ctrl_layout)

        # GIF 导出参数卡片
        gif_card = QFrame()
        gif_card.setObjectName("codexCard")
        gif_opt_layout = QHBoxLayout(gif_card)
        gif_opt_layout.setContentsMargins(14, 10, 14, 10)
        gif_opt_layout.setSpacing(10)

        lbl_gif_title = QLabel("GIF 导出参数")
        lbl_gif_title.setStyleSheet("font-size: 12.5px; font-weight: 600; color: #0D0D0D; background: transparent;")
        gif_opt_layout.addWidget(lbl_gif_title)
        gif_opt_layout.addSpacing(8)

        lbl_fps = QLabel("帧率")
        lbl_fps.setObjectName("recOptLabel")
        gif_opt_layout.addWidget(lbl_fps)
        self.combo_gif_fps = CodexComboBox()
        self.combo_gif_fps.addItems(["5 FPS", "10 FPS", "15 FPS", "20 FPS"])
        self.combo_gif_fps.setCurrentIndex(1)
        self.combo_gif_fps.setMinimumWidth(96)
        gif_opt_layout.addWidget(self.combo_gif_fps)

        gif_opt_layout.addSpacing(6)
        lbl_scale = QLabel("尺寸")
        lbl_scale.setObjectName("recOptLabel")
        gif_opt_layout.addWidget(lbl_scale)
        self.combo_gif_scale = CodexComboBox()
        self.combo_gif_scale.addItems(["原尺寸 (100%)", "高清 (75%)", "适中 (50%)", "紧凑 (33%)"])
        self.combo_gif_scale.setCurrentIndex(1)
        self.combo_gif_scale.setMinimumWidth(130)
        gif_opt_layout.addWidget(self.combo_gif_scale)
        gif_opt_layout.addStretch()
        body_lay.addWidget(gif_card)

        main_layout.addWidget(body_w, 1)

        # 3. 底部操作栏
        bottom_w = QWidget()
        bottom_w.setObjectName("codexBottomBar")
        btn_bar = QHBoxLayout(bottom_w)
        btn_bar.setContentsMargins(20, 12, 20, 12)
        btn_bar.setSpacing(10)

        self.btn_save_mp4 = QPushButton("保存为 MP4")
        self.btn_save_mp4.setObjectName("codexPrimaryBtn")
        self.btn_save_mp4.setFixedHeight(34)
        self.btn_save_mp4.setCursor(Qt.PointingHandCursor)
        self.btn_save_mp4.clicked.connect(self.save_as_mp4)
        btn_bar.addWidget(self.btn_save_mp4)

        self.btn_save_gif = QPushButton("导出 GIF 动图")
        self.btn_save_gif.setObjectName("codexSecondaryBtn")
        self.btn_save_gif.setFixedHeight(34)
        self.btn_save_gif.setCursor(Qt.PointingHandCursor)
        self.btn_save_gif.clicked.connect(self.save_as_gif)
        btn_bar.addWidget(self.btn_save_gif)

        self.btn_copy_gif = QPushButton("复制 GIF")
        self.btn_copy_gif.setObjectName("codexSecondaryBtn")
        self.btn_copy_gif.setFixedHeight(34)
        self.btn_copy_gif.setCursor(Qt.PointingHandCursor)
        self.btn_copy_gif.clicked.connect(self.copy_gif_to_clipboard)
        btn_bar.addWidget(self.btn_copy_gif)

        btn_bar.addStretch()

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("codexSecondaryBtn")
        self.btn_close.setFixedHeight(34)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        btn_bar.addWidget(self.btn_close)

        main_layout.addWidget(bottom_w)

        # 初始化视频解码器与播放定时器
        self.play_timer = QTimer(self)
        interval = max(10, int(1000.0 / self.fps))
        self.play_timer.setInterval(interval)
        self.play_timer.timeout.connect(self._on_play_step)

        self._apply_initial_dialog_size()
        self._load_video()

    def _apply_initial_dialog_size(self):
        """根据视频真实宽高比设置初始弹窗尺寸，让预览区恰好贴合画面而不留黑边。"""
        vw = int(self.info.get('width', 0) or 0)
        vh = int(self.info.get('height', 0) or 0)
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        chrome_w = 40
        chrome_h = 276
        max_stage_w = max(580, min(960, int(avail.width() * 0.80) - chrome_w))
        max_stage_h = max(220, min(560, int(avail.height() * 0.82) - chrome_h))

        if vw > 0 and vh > 0:
            scale = min(max_stage_w / vw, max_stage_h / vh, 1.0)
            stage_w = int(round(vw * scale))
            stage_h = int(round(vh * scale))
            if stage_w < 580 and stage_h < max_stage_h:
                up = min(580 / vw, max_stage_h / vh)
                stage_w = int(round(vw * up))
                stage_h = int(round(vh * up))
            dlg_w = max(620, stage_w + chrome_w)
            dlg_h = max(440, stage_h + chrome_h)
        else:
            dlg_w, dlg_h = 720, 580

        self.resize(min(dlg_w, int(avail.width() * 0.90)), min(dlg_h, int(avail.height() * 0.90)))

    def _refresh_preview_pixmap(self):
        if self._current_raw_pixmap is None or self._current_raw_pixmap.isNull():
            return
        crect = self.preview_label.contentsRect()
        lbl_w = max(100, crect.width() - 2)
        lbl_h = max(100, crect.height() - 2)
        dpr = float(self.preview_label.devicePixelRatioF() or 1.0)
        scaled = self._current_raw_pixmap.scaled(
            max(1, int(round(lbl_w * dpr))),
            max(1, int(round(lbl_h * dpr))),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if dpr != 1.0:
            scaled.setDevicePixelRatio(dpr)
        self.preview_label.setPixmap(scaled)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_preview_pixmap()
        QTimer.singleShot(0, self._refresh_preview_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_preview_pixmap()

    def _load_video(self):
        if not self.video_path or not os.path.exists(self.video_path):
            self.preview_label.setText("未找到录屏视频文件")
            return
        try:
            self.cap = cv2.VideoCapture(self.video_path)
            if not self.cap.isOpened():
                self.preview_label.setText("无法打开录屏视频文件")
                return
            self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
            if self.total_frames > 0:
                self.slider.setRange(0, max(0, self.total_frames - 1))
                self.show_frame(0)
            else:
                self.slider.setRange(0, 0)
                self.preview_label.setText("视频未包含有效帧")
        except Exception as e:
            logging.error("_load_video 异常: %s", e, exc_info=True)
            self.preview_label.setText(f"视频加载出错: {e}")

    def show_frame(self, frame_idx):
        if not self.cap or not self.cap.isOpened() or self.total_frames <= 0:
            return
        try:
            frame_idx = max(0, min(frame_idx, self.total_frames - 1))
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = self.cap.read()
            if not ret or frame is None:
                return
            self.current_frame = frame_idx
            self.slider.blockSignals(True)
            self.slider.setValue(frame_idx)
            self.slider.blockSignals(False)

            rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            h, w, ch = rgb.shape
            bytes_per_line = rgb.strides[0]
            qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
            self._current_raw_pixmap = QPixmap.fromImage(qimg)
            self._refresh_preview_pixmap()

            cur_sec = int(frame_idx / self.fps)
            total_sec = int(self.info.get('duration', 0))
            self.time_lbl.setText(f"{cur_sec // 60:02d}:{cur_sec % 60:02d} / {total_sec // 60:02d}:{total_sec % 60:02d}")
        except Exception as e:
            logging.error("show_frame 异常: %s", e, exc_info=True)

    def toggle_play(self):
        if self.is_playing:
            self.is_playing = False
            self.play_timer.stop()
            self.btn_play.setText("▶ 播放")
        else:
            if self.current_frame >= self.total_frames - 1:
                self.current_frame = 0
            self.is_playing = True
            self.play_timer.start()
            self.btn_play.setText("⏸ 暂停")

    def _on_play_step(self):
        if self.current_frame >= self.total_frames - 1:
            self.toggle_play()
            return
        self.show_frame(self.current_frame + 1)

    def on_slider_moved(self, value):
        if self.is_playing:
            self.toggle_play()
        self.show_frame(value)

    def save_as_mp4(self):
        default_name = f"ScreenRecording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        out_path, _ = QFileDialog.getSaveFileName(self, "保存 MP4 录屏视频", default_name, "MP4 视频 (*.mp4);;所有文件 (*.*)")
        if out_path:
            try:
                shutil.copyfile(self.video_path, out_path)
                QMessageBox.information(self, "导出成功", f"MP4 视频已成功保存至:\n{out_path}")
            except Exception as e:
                QMessageBox.critical(self, "导出失败", f"保存 MP4 视频失败: {e}")

    def _ensure_gif_generated(self):
        fps_map = {"5 FPS": 5, "10 FPS": 10, "15 FPS": 15, "20 FPS": 20}
        scale_map = {"原尺寸 (100%)": 1.0, "高清 (75%)": 0.75, "适中 (50%)": 0.5, "紧凑 (33%)": 0.33}
        chosen_fps = fps_map.get(self.combo_gif_fps.currentText(), 10)
        chosen_scale = scale_map.get(self.combo_gif_scale.currentText(), 0.75)

        gif_name = f"{os.path.splitext(os.path.basename(self.video_path))[0]}_{chosen_fps}fps_{int(chosen_scale*100)}.gif"
        target_path = os.path.join(REC_TEMP_FOLDER, gif_name)
        if os.path.exists(target_path):
            self.temp_gif_path = target_path
            return self.temp_gif_path

        self.temp_gif_path = target_path
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = convert_video_to_gif(self.video_path, self.temp_gif_path, max_fps=chosen_fps, scale=chosen_scale)
            if not ok:
                return None
            return self.temp_gif_path
        finally:
            QApplication.restoreOverrideCursor()

    def save_as_gif(self):
        gif_file = self._ensure_gif_generated()
        if not gif_file or not os.path.exists(gif_file):
            QMessageBox.critical(self, "生成失败", "GIF 动图生成失败，请检查视频文件。")
            return
        default_name = f"ScreenRecording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gif"
        out_path, _ = QFileDialog.getSaveFileName(self, "保存 GIF 动图", default_name, "GIF 动图 (*.gif);;所有文件 (*.*)")
        if out_path:
            try:
                shutil.copyfile(gif_file, out_path)
                QMessageBox.information(self, "导出成功", f"GIF 动图已成功保存至:\n{out_path}")
            except Exception as e:
                QMessageBox.critical(self, "导出失败", f"保存 GIF 动图失败: {e}")

    def copy_gif_to_clipboard(self):
        gif_file = self._ensure_gif_generated()
        if not gif_file or not os.path.exists(gif_file):
            QMessageBox.critical(self, "生成失败", "GIF 动图生成失败，请检查视频文件。")
            return
        try:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(os.path.abspath(gif_file))])
            img = QImage(gif_file)
            if not img.isNull():
                mime.setImageData(img)
            QApplication.clipboard().setMimeData(mime)
            QMessageBox.information(self, "复制成功", "GIF 动图已复制到剪贴板！\n可以直接粘贴到聊天软件、文档或文件夹中。")
        except Exception as e:
            QMessageBox.critical(self, "复制失败", f"复制到剪贴板失败: {e}")

    def closeEvent(self, event):
        try:
            if hasattr(self, 'play_timer') and self.play_timer and self.play_timer.isActive():
                self.play_timer.stop()
            if hasattr(self, 'cap') and self.cap and self.cap.isOpened():
                self.cap.release()
                self.cap = None
            if hasattr(self, 'temp_gif_path') and self.temp_gif_path and os.path.exists(self.temp_gif_path):
                try:
                    os.remove(self.temp_gif_path)
                except OSError as e:
                    logging.debug("清理临时 GIF 文件失败: %s", e)
        except Exception as e:
            logging.error("RecordingExportDialog closeEvent 异常: %s", e)
        super().closeEvent(event)

def parse_hotkey_to_native(hotkey_str):
    """解析如 'F1', 'Ctrl+Alt+A', 'Shift+F2', 'Win+Shift+S' 等快捷键字符串为 Windows 原生 MOD 掩码与 VK 键码"""
    if not hotkey_str or not hotkey_str.strip():
        return 0, 0
    parts = [p.strip().lower() for p in hotkey_str.split('+')]
    mod = 0x4000  # MOD_NOREPEAT
    vk = 0
    VK_MAP = {
        'backspace': 0x08, 'tab': 0x09, 'clear': 0x0C, 'return': 0x0D, 'enter': 0x0D,
        'pause': 0x13, 'capslock': 0x14, 'caps_lock': 0x14, 'escape': 0x1B, 'esc': 0x1B,
        'space': 0x20, 'pageup': 0x21, 'pagedown': 0x22, 'end': 0x23, 'home': 0x24,
        'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'select': 0x29,
        'print': 0x2A, 'execute': 0x2B, 'printscreen': 0x2C, 'prtscr': 0x2C,
        'insert': 0x2D, 'delete': 0x2E, 'del': 0x2E, 'help': 0x2F,
        'multiply': 0x6A, 'add': 0x6B, 'separator': 0x6C, 'subtract': 0x6D,
        'decimal': 0x6E, 'divide': 0x6F,
        ';': 0xBA, '=': 0xBB, 'plus': 0xBB, ',': 0xBC, '-': 0xBD, '.': 0xBE, '/': 0xBF, '`': 0xC0,
        '[': 0xDB, '\\': 0xDC, ']': 0xDD, "'": 0xDE,
    }
    for p in parts:
        if p in ['ctrl', 'control']:
            mod |= 0x0002
        elif p == 'alt':
            mod |= 0x0001
        elif p == 'shift':
            mod |= 0x0004
        elif p in ['win', 'windows', 'meta']:
            mod |= 0x0008
        elif p.startswith('f') and p[1:].isdigit():
            f_num = int(p[1:])
            if 1 <= f_num <= 24:
                vk = 0x70 + (f_num - 1)
        elif p in VK_MAP:
            vk = VK_MAP[p]
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
    return mod, vk


class GlobalHotkeyManager(QObject):
    """
    基于 Windows 原生 RegisterHotKey 的全局快捷键管理器。

    优势：
    1. 不依赖易被系统静默卸载的低级键盘钩子 (WH_KEYBOARD_LL)。
    2. 无需周期性 unhook_all 刷新，彻底解决快捷键失效、打字卡顿与丢键问题。
    3. 支持全部 8 项基础与扩展功能绑定，默认 F1~F4 注册生效。
    4. 若原生 API 注册冲突，自动降级为 keyboard 库作为兜底。
    """
    on_hotkey_pressed = pyqtSignal()   # F1: 截图
    on_ocr_pressed = pyqtSignal()      # F2: 文字识别与翻译
    on_record_pressed = pyqtSignal()   # F4: 录屏
    on_stop_scroll = pyqtSignal()      # Esc: 取消滚动截图
    on_pin_pressed = pyqtSignal()      # F3: 贴图
    on_space_pressed = pyqtSignal()    # Space: 滚动截图开始/停止
    on_win_pin_pressed = pyqtSignal()  # 窗口置顶 (DeskPin)
    on_scroll_pressed = pyqtSignal()   # 滚动长截图
    on_history_pressed = pyqtSignal()  # 截图历史
    on_settings_pressed = pyqtSignal() # 打开系统设置
    on_drawer_pressed = pyqtSignal()   # 贴图储藏柜 (默认 Ctrl+1)
    on_toggle_pins_pressed = pyqtSignal()  # 隐藏/恢复全部贴图与抽屉 (默认 Ctrl+Alt+H)
    on_paste_pin_pressed = pyqtSignal()    # 剪贴板图片直接贴图 (默认 Ctrl+Alt+V)
    on_restore_click_through_pressed = pyqtSignal()  # 解除所有贴图鼠标穿透 (默认 Ctrl+Alt+T)
    on_pin_border_pressed = pyqtSignal()   # 切换贴图边框开关

    HOTKEY_F1_ID = 101
    HOTKEY_F3_ID = 102
    HOTKEY_SPACE_ID = 103
    HOTKEY_ESC_ID = 104
    HOTKEY_F4_ID = 105
    HOTKEY_F2_ID = 106
    HOTKEY_WIN_PIN_ID = 107
    HOTKEY_SCROLL_ID = 108
    HOTKEY_HISTORY_ID = 109
    HOTKEY_SETTINGS_ID = 110
    HOTKEY_DRAWER_ID = 111
    HOTKEY_TOGGLE_PINS_ID = 112
    HOTKEY_PASTE_PIN_ID = 113
    HOTKEY_RESTORE_CT_ID = 114
    HOTKEY_PIN_BORDER_ID = 115

    def __init__(self, parent=None):
        super().__init__(parent)
        self._user32 = ctypes.windll.user32
        self._filter = None
        self._registered_native_ids = set()
        self._space_registered = False
        self._esc_registered = False
        self._scroll_esc_active = False
        self._recording_esc_active = False
        self._suspended = False

    def suspend_hotkeys(self):
        """在设置面板录制快捷键期间暂时挂起全局热键，避免按键被拦截"""
        if self._suspended:
            return
        self._suspended = True
        self._unregister_all_active()

    def resume_hotkeys(self):
        """恢复全局热键监听"""
        if not self._suspended:
            return
        self._suspended = False
        self.reload_hotkeys()

    def _unregister_all_active(self):
        for hid in list(self._registered_native_ids):
            try:
                self._user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self._registered_native_ids.clear()
        self._space_registered = False
        self._esc_registered = False
        try:
            keyboard.unhook_all()
        except Exception as e:
            logging.debug("卸载全局键盘钩子失败: %s", e)

    def install(self, app):
        """安装 Qt 全局原生事件过滤器并注册基础热键"""
        class _NativeFilter(QAbstractNativeEventFilter):
            def __init__(self, mgr):
                super().__init__()
                self.mgr = mgr

            def nativeEventFilter(self, eventType, message):
                if eventType == "windows_generic_MSG":
                    msg = wintypes.MSG.from_address(int(message))
                    if msg.message == 0x0312:  # WM_HOTKEY
                        self.mgr._on_wm_hotkey(msg.wParam)
                        return True, 0
                return False, 0

        self._filter = _NativeFilter(self)
        app.installNativeEventFilter(self._filter)
        self._register_static_hotkeys()

    def _register_one_hotkey(self, hotkey_id, hotkey_str, trigger_callback):
        if not hotkey_str:
            return
        mod, vk = parse_hotkey_to_native(hotkey_str)
        if vk != 0 and self._user32.RegisterHotKey(None, hotkey_id, mod, vk):
            self._registered_native_ids.add(hotkey_id)
            logging.info("Windows 原生热键 %s 注册成功 (ID=%d)", hotkey_str, hotkey_id)
        else:
            logging.info("Windows 原生热键 %s 未生效，采用 keyboard 库监听...", hotkey_str)
            try:
                keyboard.add_hotkey(hotkey_str.lower(), trigger_callback, suppress=True)
            except Exception as e:
                logging.warning("keyboard 热键 %s 注册失败: %s", hotkey_str, e)

    def _register_static_hotkeys(self):
        settings = load_settings()
        hk_snip = settings.get("hotkey_snip", "F1")
        hk_ocr = settings.get("hotkey_ocr", "F2")
        hk_pin = settings.get("hotkey_pin", "F3")
        hk_rec = settings.get("hotkey_record", "F4")
        hk_win_pin = settings.get("hotkey_window_pin", "")
        hk_scroll = settings.get("hotkey_scroll", "")
        hk_hist = settings.get("hotkey_history", "")
        hk_sett = settings.get("hotkey_settings", "")
        hk_drawer = settings.get("hotkey_drawer", "Ctrl+1")
        hk_toggle_pins = settings.get("hotkey_toggle_pins", "Ctrl+Alt+H")
        hk_paste_pin = settings.get("hotkey_paste_pin", "Ctrl+Alt+V")
        hk_restore_ct = settings.get("hotkey_restore_click_through", "Ctrl+Alt+T")
        hk_pin_border = settings.get("hotkey_pin_border", "")

        self._register_one_hotkey(self.HOTKEY_F1_ID, hk_snip, self.trigger_snip)
        self._register_one_hotkey(self.HOTKEY_F2_ID, hk_ocr, self.trigger_ocr)
        self._register_one_hotkey(self.HOTKEY_F3_ID, hk_pin, self.trigger_pin)
        self._register_one_hotkey(self.HOTKEY_F4_ID, hk_rec, self.trigger_record)
        self._register_one_hotkey(self.HOTKEY_WIN_PIN_ID, hk_win_pin, self.trigger_win_pin)
        self._register_one_hotkey(self.HOTKEY_SCROLL_ID, hk_scroll, self.trigger_scroll)
        self._register_one_hotkey(self.HOTKEY_HISTORY_ID, hk_hist, self.trigger_history)
        self._register_one_hotkey(self.HOTKEY_SETTINGS_ID, hk_sett, self.trigger_settings)
        self._register_one_hotkey(self.HOTKEY_DRAWER_ID, hk_drawer, self.trigger_drawer)
        self._register_one_hotkey(self.HOTKEY_TOGGLE_PINS_ID, hk_toggle_pins, self.trigger_toggle_pins)
        self._register_one_hotkey(self.HOTKEY_PASTE_PIN_ID, hk_paste_pin, self.trigger_paste_pin)
        self._register_one_hotkey(self.HOTKEY_RESTORE_CT_ID, hk_restore_ct, self.trigger_restore_click_through)
        self._register_one_hotkey(self.HOTKEY_PIN_BORDER_ID, hk_pin_border, self.trigger_pin_border)

    def reload_hotkeys(self):
        self._suspended = False
        self._unregister_all_active()
        self._register_static_hotkeys()
        if self._space_registered or self._scroll_esc_active:
            self.register_space_hotkey()
        if self._recording_esc_active:
            self._ensure_esc_hotkey_registered()

    def _ensure_esc_hotkey_registered(self):
        if self._esc_registered:
            return
        MOD_NOREPEAT = 0x4000
        VK_ESCAPE = 0x1B
        if self._user32.RegisterHotKey(None, self.HOTKEY_ESC_ID, MOD_NOREPEAT, VK_ESCAPE):
            self._registered_native_ids.add(self.HOTKEY_ESC_ID)
        else:
            try:
                keyboard.add_hotkey('esc', self.trigger_stop, suppress=True)
            except Exception:
                pass
        self._esc_registered = True

    def _release_esc_hotkey_if_unused(self):
        if self._scroll_esc_active or self._recording_esc_active:
            return
        if self.HOTKEY_ESC_ID in self._registered_native_ids:
            self._user32.UnregisterHotKey(None, self.HOTKEY_ESC_ID)
            self._registered_native_ids.remove(self.HOTKEY_ESC_ID)
        else:
            try:
                keyboard.remove_hotkey('esc')
            except Exception:
                pass
        self._esc_registered = False

    def register_recording_esc_hotkey(self):
        """录屏准备/录制期间全局启用 Esc 退出热键"""
        self._recording_esc_active = True
        self._ensure_esc_hotkey_registered()
        logging.info("录屏专用 Esc 退出热键已激活。")

    def unregister_recording_esc_hotkey(self):
        """退出录屏状态后注销 Esc 热键，归还按键控制权"""
        self._recording_esc_active = False
        self._release_esc_hotkey_if_unused()

    def register_space_hotkey(self):
        """滚动截图期间动态启用 Space 与 Esc 热键"""
        self._scroll_esc_active = True
        if self._space_registered:
            self._ensure_esc_hotkey_registered()
            return
        MOD_NOREPEAT = 0x4000
        VK_SPACE = 0x20

        if self._user32.RegisterHotKey(None, self.HOTKEY_SPACE_ID, MOD_NOREPEAT, VK_SPACE):
            self._registered_native_ids.add(self.HOTKEY_SPACE_ID)
        else:
            try:
                keyboard.add_hotkey('space', self.trigger_space, suppress=True)
            except Exception:
                pass
        self._space_registered = True
        self._ensure_esc_hotkey_registered()
        logging.info("滚动截图专用热键 (Space/Esc) 已激活。")

    def unregister_space_hotkey(self):
        """退出滚动截图时注销 Space 与 Esc 热键，归还按键控制权"""
        self._scroll_esc_active = False
        if self.HOTKEY_SPACE_ID in self._registered_native_ids:
            self._user32.UnregisterHotKey(None, self.HOTKEY_SPACE_ID)
            self._registered_native_ids.remove(self.HOTKEY_SPACE_ID)
        else:
            try:
                keyboard.remove_hotkey('space')
            except Exception:
                pass
        self._space_registered = False
        self._release_esc_hotkey_if_unused()
        logging.info("滚动截图专用热键 (Space/Esc) 已注销。")

    def _on_wm_hotkey(self, hotkey_id):
        if hotkey_id == self.HOTKEY_F1_ID:
            self.trigger_snip()
        elif hotkey_id == self.HOTKEY_F2_ID:
            self.trigger_ocr()
        elif hotkey_id == self.HOTKEY_F3_ID:
            self.trigger_pin()
        elif hotkey_id == self.HOTKEY_F4_ID:
            self.trigger_record()
        elif hotkey_id == self.HOTKEY_WIN_PIN_ID:
            self.trigger_win_pin()
        elif hotkey_id == self.HOTKEY_SCROLL_ID:
            self.trigger_scroll()
        elif hotkey_id == self.HOTKEY_HISTORY_ID:
            self.trigger_history()
        elif hotkey_id == self.HOTKEY_SETTINGS_ID:
            self.trigger_settings()
        elif hotkey_id == self.HOTKEY_DRAWER_ID:
            self.trigger_drawer()
        elif hotkey_id == self.HOTKEY_TOGGLE_PINS_ID:
            self.trigger_toggle_pins()
        elif hotkey_id == self.HOTKEY_PASTE_PIN_ID:
            self.trigger_paste_pin()
        elif hotkey_id == self.HOTKEY_RESTORE_CT_ID:
            self.trigger_restore_click_through()
        elif hotkey_id == self.HOTKEY_PIN_BORDER_ID:
            self.trigger_pin_border()
        elif hotkey_id == self.HOTKEY_SPACE_ID:
            self.trigger_space()
        elif hotkey_id == self.HOTKEY_ESC_ID:
            self.trigger_stop()

    def trigger_snip(self):
        try:
            logging.info("触发截图热键 F1 at %s", time.strftime('%H:%M:%S'))
            self.on_hotkey_pressed.emit()
        except Exception as e:
            logging.error("F1 触发异常: %s", e)

    def trigger_ocr(self):
        try:
            logging.info("触发识图翻译热键 F2 at %s", time.strftime('%H:%M:%S'))
            self.on_ocr_pressed.emit()
        except Exception as e:
            logging.error("F2 触发异常: %s", e)

    def trigger_record(self):
        try:
            logging.info("触发录屏热键 F4 at %s", time.strftime('%H:%M:%S'))
            self.on_record_pressed.emit()
        except Exception as e:
            logging.error("F4 触发异常: %s", e)

    def trigger_pin(self):
        try:
            logging.info("触发贴图热键 F3 at %s", time.strftime('%H:%M:%S'))
            self.on_pin_pressed.emit()
        except Exception as e:
            logging.error("F3 触发异常: %s", e)

    def trigger_win_pin(self):
        try:
            logging.info("触发窗口置顶热键 at %s", time.strftime('%H:%M:%S'))
            self.on_win_pin_pressed.emit()
        except Exception as e:
            logging.error("窗口置顶热键触发异常: %s", e)

    def trigger_scroll(self):
        try:
            logging.info("触发滚动截图热键 at %s", time.strftime('%H:%M:%S'))
            self.on_scroll_pressed.emit()
        except Exception as e:
            logging.error("滚动截图热键触发异常: %s", e)

    def trigger_history(self):
        try:
            logging.info("触发截图历史热键 at %s", time.strftime('%H:%M:%S'))
            self.on_history_pressed.emit()
        except Exception as e:
            logging.error("截图历史热键触发异常: %s", e)

    def trigger_settings(self):
        try:
            logging.info("触发系统设置热键 at %s", time.strftime('%H:%M:%S'))
            self.on_settings_pressed.emit()
        except Exception as e:
            logging.error("系统设置热键触发异常: %s", e)

    def trigger_drawer(self):
        try:
            logging.info("触发贴图抽屉热键 at %s", time.strftime('%H:%M:%S'))
            self.on_drawer_pressed.emit()
        except Exception as e:
            logging.error("贴图抽屉热键触发异常: %s", e)

    def trigger_toggle_pins(self):
        try:
            logging.info("触发隐藏/恢复贴图热键 at %s", time.strftime('%H:%M:%S'))
            self.on_toggle_pins_pressed.emit()
        except Exception as e:
            logging.error("隐藏/恢复贴图热键触发异常: %s", e)

    def trigger_paste_pin(self):
        try:
            logging.info("触发剪贴板直接贴图热键 at %s", time.strftime('%H:%M:%S'))
            self.on_paste_pin_pressed.emit()
        except Exception as e:
            logging.error("剪贴板贴图热键触发异常: %s", e)

    def trigger_restore_click_through(self):
        try:
            logging.info("触发解除贴图鼠标穿透热键 at %s", time.strftime('%H:%M:%S'))
            self.on_restore_click_through_pressed.emit()
        except Exception as e:
            logging.error("解除鼠标穿透热键触发异常: %s", e)

    def trigger_pin_border(self):
        try:
            logging.info("触发贴图边框开关热键 at %s", time.strftime('%H:%M:%S'))
            self.on_pin_border_pressed.emit()
        except Exception as e:
            logging.error("贴图边框开关热键触发异常: %s", e)

    def trigger_space(self):
        try:
            logging.debug("触发滚动热键 Space")
            self.on_space_pressed.emit()
        except Exception as e:
            logging.error("Space 触发异常: %s", e)

    def trigger_stop(self):
        try:
            logging.debug("触发滚动停止热键 Esc")
            self.on_stop_scroll.emit()
        except Exception as e:
            logging.error("Esc 触发异常: %s", e)

    def start(self):
        # 兼容旧接口（旧 HotkeyListener 为 QThread.start()）
        pass

    def stop(self):
        for hid in list(self._registered_native_ids):
            self._user32.UnregisterHotKey(None, hid)
        self._registered_native_ids.clear()
        try:
            keyboard.unhook_all()
        except Exception as e:
            logging.debug("卸载全局键盘钩子失败: %s", e)

    def wait(self, msecs=2000):
        # 兼容 QThread.wait 接口
        pass

# 保持类名别名以确保外部或历史引用无缝兼容
HotkeyListener = GlobalHotkeyManager


class ScreenshotTool(_CanvasEditingMixin, QWidget):
    def __init__(self, hotkey_listener=None, parent_tool=None):
        super().__init__()
        ensure_high_dpi_and_fonts()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.parent_tool = parent_tool
        self.root_tool = parent_tool.root_tool if parent_tool is not None else self
        self._capture_active = False
        self._secondary_capture = None
        self._temp_hidden_dialogs = []
        self._temp_hidden_parent_info = None
        self._suppressed_pin_overlay = False
        self._active_settings_dialog = None
        self._history_dialog_open = False
        self._active_history_dialog = None

        self.hotkey_listener = hotkey_listener
        self._toolbar_shortcut_map = {}
        self._reload_toolbar_shortcuts()
        
        self.scroll_thread = None
        self.border_frame = None
        self.active_scroll_rect = None
        self.scroll_waiting = False
        self._scroll_cancelled = False
        self.drag_mode = None
        self.drag_offset = QPoint()

        self.recording_thread = None
        self.recording_toolbar = None
        self.recording_border = None
        self.recording_intent = False
        self.ocr_intent = False
        self._quick_ocr_worker = None
        self._active_ocr_dialog = None
        self.recording_target_rect = None
        self.recording_quality_key = 'p60'

        self.state = "IDLE"
        self.selection_rect = QRect()
        self.hovered_window_rect = None
        self.hovered_window_title = ""
        self.window_snapper = WindowSnapper(my_pid=os.getpid())
        self.press_pos = QPoint()
        self.current_step_num = 1
        self.current_tool = None
        self._cached_selection_pixmap = None
        self._cached_selection_key = None
        self._rotated_base_pixmap = None
        self._rotated_display_cache = None
        self._rotation_preview_pixmap = None
        self._rotation_preview_snapshot = None
        self._rotation_preview_display_cache = None
        self.crop_rect = None
        self.crop_mode = False
        self.crop_ratio = None
        self.crop_drag_handle = None
        self.crop_drag_start_pos = None
        self.crop_drag_start_rect = None

        self.edits = []
        self.undo_stack = []
        self.redo_stack = []

        self.arrow_enabled = False
        self.current_thickness = 3
        self.current_color = QColor(255, 0, 0) 
        
        self.ocr_rect = None 
        self.ocr_worker = None
        self._ocr_doc = None
        self._ocr_doc_key = None
        self._ocr_doc_image = None
        self._ocr_doc_worker = None
        self._ocr_busy = False
        self._text_select_layer = None
        self._translation_overlay = None
        self._retired_overlays = []
        self._translate_worker = None
        self._active_translation_window = None
        self._update_thread = None
        self._manual_check_pending = False
        self._update_watchdog = QTimer(self)
        self._update_watchdog.setSingleShot(True)
        self._update_watchdog.timeout.connect(self._on_update_timeout)
        self._retired_update_threads = []
        self._update_dialog = None
        self._update_progress = None
        self._ocr_running = False
        self._ocr_generation = 0
        self._ocr_waiters = []
        self._text_dragging = False
        self._text_sel_start = QPoint()
        self._text_sel_start_index = None
        self._text_sel_column_rect = QRect()
        self._text_sel_rect = QRect()
        self._text_selected = set()
        self._text_hover = None
        self._text_flash = ""
        self._text_ocr_hint = ""
        self._auto_ocr_pending = False
        self._text_flash_timer = QTimer(self)
        self._text_flash_timer.setSingleShot(True)
        self._text_flash_timer.timeout.connect(self._clear_text_flash)
        self._auto_ocr_timer = QTimer(self)
        self._auto_ocr_timer.setSingleShot(True)
        self._auto_ocr_timer.timeout.connect(self._run_auto_text_ocr)
        self.color_pick_pos = None
        self.color_picker_visible = False
        self.color_output_hex = False
        self.color_output_hex = False
        self.eraser_state_saved = False

        if self.parent_tool is None:
            self.pin_border_enabled = bool(load_settings().get("pin_border_enabled", False))
            self.capture_history = []
            self.pinned_windows = []
            self._pins_boss_hidden = False
            self._boss_hidden_pins = []
            self._boss_hidden_drawer = False
            self.pin_drawer = PinDrawerWindow(main_app=self)
            self.pin_drawer.set_feature_enabled(load_settings().get("drawer_hover_enabled", True))
        else:
            self.pin_border_enabled = self.root_tool.pin_border_enabled
            self.capture_history = self.root_tool.capture_history
            self.pinned_windows = self.root_tool.pinned_windows
            self._pins_boss_hidden = False
            self._boss_hidden_pins = []
            self._boss_hidden_drawer = False
            self.pin_drawer = None

        self.inline_editor = InlineTextEditor(self)
        self.crop_toolbar = CropToolbar(self)
        self.crop_toolbar.apply_clicked.connect(self.apply_crop)
        self.crop_toolbar.cancel_clicked.connect(self.cancel_crop)
        self.crop_toolbar.ratio_changed.connect(self.set_crop_ratio)
        self.crop_toolbar.hide()

        self.color_timer = QTimer(self)
        self.color_timer.setInterval(30)
        self.color_timer.timeout.connect(self.update_color_picker_from_cursor)

        self.setup_toolbar()
        if self.parent_tool is None:
            QTimer.singleShot(3000, lambda: self.check_for_updates(manual=False))

    def get_active_capture_layer_depth(self):
        """统计当前同时叠加的截图编辑层数（活动 ScreenshotTool 层 + 打开的图片编辑与预览窗口）。"""
        root = getattr(self, "root_tool", self)
        depth = 1 if getattr(root, "_capture_active", False) else 0
        sec = getattr(root, "_secondary_capture", None)
        if sec is not None and getattr(sec, "_capture_active", False):
            depth += 1
        try:
            depth += len(ImageEditorDialog.get_open_editors())
        except Exception as e:
            logging.debug("统计打开编辑器层数异常: %s", e)
        return depth

    def _ensure_secondary_capture(self):
        root = getattr(self, "root_tool", self)
        if root._secondary_capture is None:
            root._secondary_capture = ScreenshotTool(
                hotkey_listener=root.hotkey_listener,
                parent_tool=root,
            )
        return root._secondary_capture

    def set_pin_border_enabled(self, enabled, show_toast=True):
        """设置并持久化桌面贴图边框开关，同时刷新全部已有贴图窗口。"""
        if self.parent_tool is not None:
            return self.root_tool.set_pin_border_enabled(enabled, show_toast=show_toast)
        enabled = bool(enabled)
        self.pin_border_enabled = enabled
        if self._secondary_capture is not None:
            self._secondary_capture.pin_border_enabled = enabled
        save_settings({"pin_border_enabled": enabled})
        for pw in list(self.pinned_windows):
            try:
                if pw is not None:
                    pw.update()
            except RuntimeError:
                pass
        if show_toast:
            try:
                msg = "🖼️ 已开启桌面贴图边框" if enabled else "🖼️ 已关闭桌面贴图边框"
                self._border_toast = PinToast(msg, pinned=enabled)
                self._border_toast.show()
            except Exception:
                pass
        return enabled

    def toggle_pin_border(self):
        """热键 / 托盘 / 右键菜单切换桌面贴图边框。"""
        if self.parent_tool is not None:
            return self.root_tool.toggle_pin_border()
        return self.set_pin_border_enabled(not bool(getattr(self, "pin_border_enabled", False)), show_toast=True)

    def toggle_drawer(self):
        """快捷键：彻底开启或关闭贴图储藏柜功能"""
        if self.parent_tool is not None:
            return self.root_tool.toggle_drawer()
        if hasattr(self, 'pin_drawer') and self.pin_drawer:
            enabled = self.pin_drawer.toggle()
            save_settings({"drawer_hover_enabled": enabled})
            try:
                hk_drawer = (load_settings().get("hotkey_drawer", "Ctrl+1") or "").strip()
                hint = f" (按 {hk_drawer} 开启)" if hk_drawer else ""
                msg = "🗄️ 贴图抽屉功能已开启" if enabled else f"🚫 贴图抽屉功能已关闭{hint}"
                self._drawer_toast = PinToast(msg, pinned=enabled)
                self._drawer_toast.show()
            except Exception:
                pass

    def toggle_all_pins_visibility(self):
        """老板键（Ctrl+Alt+H / 托盘）：一键临时隐藏全部桌面贴图与抽屉，再次触发全部恢复。"""
        if self.parent_tool is not None:
            return self.root_tool.toggle_all_pins_visibility()
        drawer = getattr(self, "pin_drawer", None)
        if not self._pins_boss_hidden:
            hidden_list = []
            for pw in list(self.pinned_windows):
                try:
                    if pw is not None and pw.isVisible():
                        pw.hide()
                        hidden_list.append(pw)
                except RuntimeError:
                    pass
            self._boss_hidden_pins = hidden_list
            self._boss_hidden_drawer = bool(drawer and drawer.drawer_enabled)
            if self._boss_hidden_drawer:
                if hasattr(drawer, "set_boss_hidden"):
                    drawer.set_boss_hidden(True)
                else:
                    drawer.hide()
            self._pins_boss_hidden = True
            self._boss_toast = PinToast("👁️ 已隐藏所有贴图与抽屉（再次按键或托盘恢复）", pinned=False)
            self._boss_toast.show()
        else:
            for pw in list(self._boss_hidden_pins):
                try:
                    if pw is not None:
                        pw.show()
                        pw.raise_()
                except RuntimeError:
                    pass
            self._boss_hidden_pins = []
            if self._boss_hidden_drawer and drawer and drawer.drawer_enabled:
                if hasattr(drawer, "set_boss_hidden"):
                    drawer.set_boss_hidden(False)
                else:
                    drawer.show()
                    drawer.ensure_on_top(force=True)
            self._boss_hidden_drawer = False
            self._pins_boss_hidden = False
            self._boss_toast = PinToast("👁️ 已恢复所有贴图与抽屉", pinned=True)
            self._boss_toast.show()

    def paste_clipboard_as_pin(self):
        """将剪贴板最新的图片（位图或复制的图片文件）直接贴到鼠标所在屏幕位置。"""
        if self.parent_tool is not None:
            return self.root_tool.paste_clipboard_as_pin()
        cb = QApplication.clipboard()
        if cb is None:
            return None
        mime = cb.mimeData()
        pixmaps = _extract_pixmaps_from_mimedata(mime) if mime is not None else []
        if not pixmaps:
            cb_pix = cb.pixmap()
            if cb_pix is not None and not cb_pix.isNull() and cb_pix.width() > 2 and cb_pix.height() > 2:
                pixmaps = [cb_pix]
        if not pixmaps:
            QToolTip.showText(QCursor.pos(), "📋 剪贴板中暂无可贴图的图片", None, QRect(), 2000)
            return None

        cursor_pos = QCursor.pos()
        last_pin = None
        for idx, pix in enumerate(pixmaps):
            pos = QPoint(cursor_pos.x() + idx * 24 - 20, cursor_pos.y() + idx * 24 - 20)
            last_pin = self.create_pinned_window(pix, initial_pos=pos, zoom_factor=1.0)
        return last_pin

    def restore_all_click_through(self):
        """解除所有置顶贴图的鼠标穿透锁定。"""
        if self.parent_tool is not None:
            return self.root_tool.restore_all_click_through()
        restored = 0
        for pw in list(self.pinned_windows):
            try:
                if pw is not None and getattr(pw, "_click_through", False):
                    pw.set_click_through(False)
                    restored += 1
            except RuntimeError:
                pass
        msg = f"🖱️ 已解除 {restored} 张贴图的鼠标穿透" if restored > 0 else "🖱️ 当前没有开启鼠标穿透的贴图"
        self._ct_restore_toast = PinToast(msg, pinned=(restored > 0))
        self._ct_restore_toast.show()

    def pin_all_to_drawer(self):
        """一键将当前桌面上的所有贴图批量收入右侧抽屉。"""
        if self.parent_tool is not None:
            return self.root_tool.pin_all_to_drawer()
        drawer = getattr(self, "pin_drawer", None)
        if not drawer:
            return
        if not drawer.drawer_enabled:
            drawer.set_feature_enabled(True)
            save_settings({"drawer_hover_enabled": True})
        pixmaps = []
        for pw in list(self.pinned_windows):
            try:
                if pw is not None and pw.pixmap and not pw.pixmap.isNull():
                    pixmaps.append(pw.pixmap)
                    pw._dropped_to_drawer = True
                    pw.close()
            except RuntimeError:
                pass
        if pixmaps:
            drawer._insert_pixmaps(pixmaps, keep_open_ms=900)
            self._batch_drawer_toast = PinToast(f"🗄️ 已将 {len(pixmaps)} 张贴图全部收入抽屉", pinned=True)
            self._batch_drawer_toast.show()

    def close_all_pins(self):
        """关闭全部当前显示的桌面贴图（记录仍可按 F3 找回）。"""
        if self.parent_tool is not None:
            return self.root_tool.close_all_pins()
        for pw in list(self.pinned_windows):
            try:
                if pw is not None:
                    pw.close()
            except RuntimeError:
                pass

    def _snapshot_state(self):
        return {
            'edits': clone_edits(self.edits),
            'selection_rect': QRect(self.selection_rect),
            'rotated_base_pixmap': getattr(self, '_rotated_base_pixmap', None)
        }

    def save_state(self):
        self.undo_stack.append(self._snapshot_state())
        self.redo_stack.clear()
        self.update_undo_redo_states()

    def _restore_state(self, state):
        self.edits = state['edits']
        new_rect = state['selection_rect']
        new_rot = state.get('rotated_base_pixmap')
        old_rot = getattr(self, '_rotated_base_pixmap', None)
        content_changed = (new_rect != self.selection_rect) or (new_rot is not old_rot)
        self.selection_rect = new_rect
        self._rotated_base_pixmap = new_rot
        self._rotated_display_cache = None
        self.update_undo_redo_states()
        self.update()
        if content_changed:
            self._invalidate_text_doc()
            self.update_toolbar_positions()
            if self.state == "EDITING":
                self._schedule_auto_text_ocr()

    def undo(self):
        if self.has_pending_rotation_preview():
            self.cancel_rotation_preview()
            return
        if self.undo_stack:
            self.commit_text_edit()
            self.redo_stack.append(self._snapshot_state())
            self._restore_state(self.undo_stack.pop())

    def redo(self):
        if self.has_pending_rotation_preview():
            self.cancel_rotation_preview()
        if self.redo_stack:
            self.commit_text_edit()
            self.undo_stack.append(self._snapshot_state())
            self._restore_state(self.redo_stack.pop())

    # ---------- 旋转 / 翻转 ----------
    def _reset_rotation_state(self):
        self._rotated_base_pixmap = None
        self._rotated_display_cache = None
        self._rotation_preview_pixmap = None
        self._rotation_preview_snapshot = None
        self._rotation_preview_display_cache = None
        rc = getattr(getattr(self, 'options_bar', None), 'rotate_controls', None)
        if rc is not None:
            rc.set_angle(0, emit=False)

    def has_pending_rotation_preview(self):
        return getattr(self, '_rotation_preview_pixmap', None) is not None

    def _selection_rect_to_base(self, rect):
        """把屏幕坐标中的矩形（位于选区内）换算为旋转底图的像素矩形"""
        base = getattr(self, '_rotated_base_pixmap', None)
        sel = self.selection_rect
        rel = QRect(rect).intersected(sel).translated(-sel.topLeft())
        if base is None or base.isNull() or rel.isEmpty():
            return rel
        sx = base.width() / float(max(1, sel.width()))
        sy = base.height() / float(max(1, sel.height()))
        x1 = int(round(rel.left() * sx))
        y1 = int(round(rel.top() * sy))
        x2 = int(round((rel.left() + rel.width()) * sx))
        y2 = int(round((rel.top() + rel.height()) * sy))
        return QRect(x1, y1, max(1, x2 - x1), max(1, y2 - y1)).intersected(base.rect())

    def _crop_selection_content(self, rect):
        """截取选区内某个屏幕矩形对应的底图内容（兼容旋转后的选区）"""
        base = getattr(self, '_rotated_base_pixmap', None)
        if base is None or base.isNull():
            return self.original_screen.copy(rect)
        return base.copy(self._selection_rect_to_base(rect))

    def _selection_base_pixmap(self):
        """当前选区的底图（不含标注，真实分辨率）；旋转后返回旋转后的底图"""
        rot = getattr(self, '_rotated_base_pixmap', None)
        if rot is not None and not rot.isNull():
            return QPixmap(rot)
        if getattr(self, 'original_screen', None) is None or self.selection_rect.isNull():
            return QPixmap()
        return self.original_screen.copy(self.selection_rect)

    def _scaled_for_selection(self, pm, cache_attr):
        if pm is None or pm.isNull():
            return None
        size = self.selection_rect.size()
        if pm.size() == size:
            return pm
        key = (pm.cacheKey(), size.width(), size.height())
        cache = getattr(self, cache_attr, None)
        if cache is not None and cache[0] == key:
            return cache[1]
        scaled = pm.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        setattr(self, cache_attr, (key, scaled))
        return scaled

    def _get_rotated_display_pixmap(self):
        """旋转后底图按选区显示尺寸缩放后的像素图（未旋转时返回 None）"""
        return self._scaled_for_selection(getattr(self, '_rotated_base_pixmap', None), '_rotated_display_cache')

    def _get_rotation_preview_display_pixmap(self):
        return self._scaled_for_selection(getattr(self, '_rotation_preview_pixmap', None), '_rotation_preview_display_cache')

    def _draw_edits_on_canvas(self, painter):
        """在截图画布上绘制标注；任意角度预览时标注已合入预览图，不再重复绘制"""
        if self.has_pending_rotation_preview():
            return
        rot_disp = self._get_rotated_display_pixmap()
        if rot_disp is not None:
            draw_all_edits(painter, self.edits, rot_disp, QPoint(0, 0), base_origin=self.selection_rect.topLeft())
        else:
            draw_all_edits(painter, self.edits, self.original_screen, QPoint(0, 0))

    def _compute_rotated_selection_rect(self, true_size, center_pt):
        """按真实尺寸计算新选区：超出屏幕时等比缩小显示，并保持中心、限制在屏幕内"""
        sw, sh = max(1, self.width()), max(1, self.height())
        tw, th = max(1, true_size.width()), max(1, true_size.height())
        scale = min(1.0, sw / tw, sh / th)
        dw, dh = max(1, int(round(tw * scale))), max(1, int(round(th * scale)))
        x = int(round(center_pt.x() - dw / 2.0))
        y = int(round(center_pt.y() - dh / 2.0))
        x = max(0, min(x, sw - dw))
        y = max(0, min(y, sh - dh))
        return QRect(x, y, dw, dh)

    def _after_rotation_change(self):
        self._rotated_display_cache = None
        self._rotation_preview_display_cache = None
        self._cached_selection_pixmap = None
        self._cached_selection_key = None
        self._reset_text_selection_state()
        self._invalidate_text_doc()
        self.update_toolbar_positions()
        self.update()
        if self.state == "EDITING":
            self._schedule_auto_text_ocr()

    def apply_orthogonal_transform(self, op):
        if self.state != "EDITING" or self.selection_rect.isNull():
            return
        self.commit_text_edit()
        if self.has_pending_rotation_preview():
            self.commit_rotation_preview()
        if self.crop_mode:
            self.exit_crop_mode()
        cur = self._selection_base_pixmap()
        if cur.isNull():
            return
        new_pm = transform_pixmap_op(cur, op)
        if new_pm is None or new_pm.isNull():
            return
        old_rect = QRect(self.selection_rect)
        new_rect = self._compute_rotated_selection_rect(new_pm.size(), QRectF(old_rect).center())
        self.save_state()
        self.edits = transform_edits_op(
            self.edits, old_rect.topLeft(), old_rect.size(), cur.size(),
            new_rect.topLeft(), new_rect.size(), new_pm.size(), op
        )
        self._rotated_base_pixmap = new_pm
        self.selection_rect = new_rect
        self._after_rotation_change()

    def preview_rotation_angle(self, angle_deg):
        if self.state != "EDITING" or self.selection_rect.isNull():
            return
        angle = int(angle_deg) % 360
        if angle > 180:
            angle -= 360
        if angle == 0:
            if self.has_pending_rotation_preview():
                self.cancel_rotation_preview(reset_controls=False)
            return
        self.commit_text_edit()
        if self.crop_mode:
            self.exit_crop_mode()
        snap = getattr(self, '_rotation_preview_snapshot', None)
        if snap is None:
            source = self.get_final_static_image()
            if source is None or source.isNull():
                return
            snap = {
                'source': source,
                'selection_rect': QRect(self.selection_rect),
            }
            self._rotation_preview_snapshot = snap
        rotated = rotate_pixmap_angle(snap['source'], angle)
        if rotated is None or rotated.isNull():
            return
        self._rotation_preview_pixmap = rotated
        self._rotation_preview_display_cache = None
        self.selection_rect = self._compute_rotated_selection_rect(rotated.size(), QRectF(snap['selection_rect']).center())
        dragging = False
        rc = getattr(getattr(self, 'options_bar', None), 'rotate_controls', None)
        if rc is not None:
            dragging = rc.is_dragging_angle()
        self._reset_text_selection_state()
        if not dragging:
            self.update_toolbar_positions()
        self.update()

    def _on_rotate_drag_finished(self):
        self.update_toolbar_positions()

    def commit_rotation_preview(self):
        if not self.has_pending_rotation_preview():
            return
        snap = self._rotation_preview_snapshot or {}
        rotated = self._rotation_preview_pixmap
        new_rect = QRect(self.selection_rect)
        # 以预览前的状态入撤销栈
        self.selection_rect = QRect(snap.get('selection_rect', new_rect))
        self.save_state()
        self.selection_rect = new_rect
        self.edits = []
        self._rotated_base_pixmap = rotated
        self._rotation_preview_pixmap = None
        self._rotation_preview_snapshot = None
        rc = getattr(getattr(self, 'options_bar', None), 'rotate_controls', None)
        if rc is not None:
            rc.set_angle(0, emit=False)
        self._after_rotation_change()

    def cancel_rotation_preview(self, reset_controls=True):
        if not self.has_pending_rotation_preview():
            if reset_controls:
                rc = getattr(getattr(self, 'options_bar', None), 'rotate_controls', None)
                if rc is not None:
                    rc.set_angle(0, emit=False)
            return
        snap = self._rotation_preview_snapshot or {}
        if 'selection_rect' in snap:
            self.selection_rect = QRect(snap['selection_rect'])
        self._rotation_preview_pixmap = None
        self._rotation_preview_snapshot = None
        self._rotation_preview_display_cache = None
        if reset_controls:
            rc = getattr(getattr(self, 'options_bar', None), 'rotate_controls', None)
            if rc is not None:
                rc.set_angle(0, emit=False)
        self.update_toolbar_positions()
        self.update()

    def _apply_rotate_controls_angle(self):
        """“应用”按钮：提交当前角度预览"""
        self.commit_rotation_preview()

    def erase_annotation_under_mouse(self, pos):
        removed = self._erase_annotation_at(pos, saved_attr='eraser_state_saved')
        if removed is not None:
            if removed.get('type') in ('TEXT', 'MOSAIC_BLUR', 'BLUR_STROKE'):
                self._erased_content_edit = True
            return True
        return False

    def erase_annotation(self, pos):
        return self.erase_annotation_under_mouse(pos)

    def setup_toolbar(self):
        self.toolbar = SnipasteToolbar(self)
        self.toolbar.setObjectName("mainToolbar")
        self.toolbar.tool_triggered.connect(self.on_tool_triggered)
        self.toolbar.action_triggered.connect(self.on_action_triggered)

        self.options_bar = ToolOptionsBar(self)
        self.options_bar.options_changed.connect(self.on_tool_options_changed)
        self.text_action_bar = TextSelectionActionBar(self)
        self.text_action_bar.action_triggered.connect(self._on_text_bar_action)
        self.text_action_bar.hide()
        rc = getattr(self.options_bar, 'rotate_controls', None)
        if rc is not None:
            rc.op_triggered.connect(self.apply_orthogonal_transform)
            rc.angle_changed.connect(self.preview_rotation_angle)
            rc.angle_drag_finished.connect(self._on_rotate_drag_finished)
            rc.apply_clicked.connect(self._apply_rotate_controls_angle)
            rc.reset_clicked.connect(self.cancel_rotation_preview)

        # 映射工具栏按钮，使内部方法与异步逻辑完全打通
        self.btn_crop = self.toolbar.btn_crop
        self.btn_ocr = self.toolbar.btn_ocr
        self.btn_scroll = self.toolbar.btn_scroll
        self.btn_record = self.toolbar.btn_record
        self.btn_history = self.toolbar.btn_history
        self.btn_win_pin = getattr(self.toolbar, 'btn_win_pin', None)
        self.btn_pin = self.toolbar.btn_pin
        self.btn_save = self.toolbar.btn_save
        self.btn_copy = self.toolbar.btn_copy
        self.btn_close = self.toolbar.btn_close

        self.update_undo_redo_states()
        self.toolbar.hide()
        self.options_bar.hide()

    def on_tool_triggered(self, tool_name):
        if self.current_tool == tool_name:
            self.set_tool(None)
        else:
            self.set_tool(tool_name)

    def on_action_triggered(self, action_name):
        if action_name != 'TRANSLATE':
            self._close_text_select_layer()
            self._close_translation_overlay()
        if action_name == 'UNDO':
            self.undo()
        elif action_name == 'REDO':
            self.redo()
        elif action_name == 'CROP':
            self.enter_crop_mode()
        elif action_name == 'OCR':
            self.activate_ocr()
        elif action_name == 'TRANSLATE':
            self.translate_current_capture()
        elif action_name == 'SCROLL':
            self.start_scroll()
        elif action_name == 'RECORD':
            self.activate_recording()
        elif action_name == 'HISTORY':
            self.show_history_dialog()
        elif action_name == 'WINDOW_PIN':
            self.activate_window_pin()
        elif action_name == 'CLOSE':
            self.hide_app(keep_history=False)
        elif action_name == 'PIN':
            self.pin_image()
        elif action_name == 'SAVE':
            self.save_image()
        elif action_name == 'COPY':
            self.copy_image()

    def on_tool_options_changed(self):
        if self.current_tool == 'TEXT' and hasattr(self, 'inline_editor') and self.inline_editor.isVisible():
            self.inline_editor.update_font_from_options(self.options_bar.get_text_options())
        self.update_cursor_for_tool()
        self.update()

    def update_undo_redo_states(self):
        if hasattr(self, 'toolbar') and isinstance(self.toolbar, SnipasteToolbar):
            self.toolbar.update_undo_redo(len(self.undo_stack) > 0, len(self.redo_stack) > 0)

    def update_toolbar_positions(self):
        if not hasattr(self, 'toolbar') or not self.toolbar:
            return
        if self.crop_mode or self.state != "EDITING" or self.selection_rect.isNull():
            self.toolbar.hide()
            if hasattr(self, 'options_bar') and self.options_bar:
                self.options_bar.hide()
            if hasattr(self, 'text_action_bar') and self.text_action_bar:
                self.text_action_bar.hide()
            return

        # 边界严格使用 ScreenshotTool 自身坐标系，杜绝多显示器全局坐标偏移
        bounds = self.rect()
        margin = 6
        min_x = bounds.left() + margin
        max_x = bounds.right() - margin
        min_y = bounds.top() + margin
        max_y = bounds.bottom() - margin

        tb_hint = self.toolbar.sizeHint()
        tb_w = tb_hint.width()
        tb_h = tb_hint.height()
        self.toolbar.resize(tb_w, tb_h)

        show_options = hasattr(self, 'options_bar') and self.options_bar and self.options_bar.has_active_tool()
        if show_options:
            opt_hint = self.options_bar.sizeHint()
            opt_w = opt_hint.width()
            opt_h = opt_hint.height()
            self.options_bar.resize(opt_w, opt_h)
        else:
            opt_w = 0
            opt_h = 0
            if hasattr(self, 'options_bar') and self.options_bar:
                self.options_bar.hide()

        total_h = tb_h + (opt_h + 4 if show_options else 0)

        # 垂直方位决策：绝不让 options_bar 与 toolbar 发生 Y 轴重叠
        space_below = max_y - self.selection_rect.bottom()
        space_above = self.selection_rect.top() - min_y

        if space_below >= total_h + 6:
            # 方案 1: 放置在选区下方 (主条在上，选项条在下)
            tb_y = self.selection_rect.bottom() + 6
            opt_y = tb_y + tb_h + 4
        elif space_above >= total_h + 6:
            # 方案 2: 放置在选区上方 (选项条在最上，主条紧邻选区)
            tb_y = self.selection_rect.top() - 6 - tb_h
            opt_y = tb_y - opt_h - 4
        else:
            # 方案 3: 选区贴顶且贴底 (如全屏选区或超高窗口)，放置在选区内部底端
            tb_y = self.selection_rect.bottom() - 10 - total_h
            opt_y = tb_y + tb_h + 4
            # 整体防溢出校准（保持间距，绝不重叠）
            if tb_y < min_y:
                tb_y = min_y
                opt_y = tb_y + tb_h + 4
            if (opt_y + opt_h if show_options else tb_y + tb_h) > max_y:
                if show_options:
                    opt_y = max_y - opt_h
                    tb_y = opt_y - tb_h - 4
                else:
                    tb_y = max_y - tb_h

        # 水平对齐：右对齐选区，并限制在屏幕内
        tb_x = self.selection_rect.right() - tb_w
        tb_x = max(min_x, min(tb_x, max_x - tb_w))

        if show_options:
            opt_x = tb_x + tb_w - opt_w
            opt_x = max(min_x, min(opt_x, max_x - opt_w))
        else:
            opt_x = tb_x

        self.toolbar.move(tb_x, tb_y)
        self.toolbar.show()
        self.toolbar.raise_()
        self.toolbar.update_frosted_bg()

        if show_options:
            self.options_bar.move(opt_x, opt_y)
            self.options_bar.show()
            self.options_bar.raise_()
            self.options_bar.update_frosted_bg()

        self._update_text_action_bar()

    def _raise_floating_bars(self):
        if hasattr(self, 'toolbar') and self.toolbar and self.toolbar.isVisible():
            self.toolbar.raise_()
        if hasattr(self, 'options_bar') and self.options_bar and self.options_bar.isVisible():
            self.options_bar.raise_()
        if hasattr(self, 'text_action_bar') and self.text_action_bar and self.text_action_bar.isVisible():
            self.text_action_bar.raise_()


    def _show_layer_limit_toast(self):
        try:
            self._layer_limit_toast = PinToast(
                "⚠️ 截图功能最多支持同时叠加两层，请先完成或关闭当前窗口",
                pinned=False,
            )
            self._layer_limit_toast.show()
            self._layer_limit_toast.raise_()
        except Exception as e:
            logging.debug("显示层数上限提示失败: %s", e)

    def activate_capture(self, ocr_intent=False, recording_intent=False):
        import time as _time
        t0 = _time.time()

        active_modal = QApplication.activeModalWidget()
        if active_modal is not None and active_modal is not self:
            try:
                active_modal.raise_()
                active_modal.activateWindow()
            except Exception:
                pass
            return

        root = getattr(self, "root_tool", self)
        if self.parent_tool is None and self._capture_active:
            if root.get_active_capture_layer_depth() >= 2:
                root._show_layer_limit_toast()
                return
            sec = root._ensure_secondary_capture()
            return sec.activate_capture(ocr_intent=ocr_intent, recording_intent=recording_intent)

        if root.get_active_capture_layer_depth() >= 2:
            root._show_layer_limit_toast()
            return

        self._capture_active = True

        if self.scroll_thread is not None:
            if self.scroll_thread.is_recording:
                self.scroll_thread.stop()
            try:
                self.scroll_thread.capture_finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.scroll_thread = None
        if self.scroll_waiting:
            self.scroll_waiting = False
        if self.border_frame:
            self.border_frame.close()
            self.border_frame = None
        if self.hotkey_listener is not None:
            self.hotkey_listener.unregister_space_hotkey()
        self.active_scroll_rect = None
        self._scroll_cancelled = False

        t1 = _time.time()
        logging.info("activate_capture() 被调用，开始进入截图模式 (layer=%d, 清理耗时 %.0fms)" % (
            root.get_active_capture_layer_depth(), (t1 - t0) * 1000))
        self.state = "SELECTING"
        self.current_tool = None
        self.current_step_num = 1
        self.drag_mode = None
        self.edits = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._reset_rotation_state()
        self.ocr_intent = ocr_intent
        self.recording_intent = recording_intent
        self.mode_banner_dismissed = False
        self.mode_banner_show_time = _time.time()
        self.current_mouse_pos = self.mapFromGlobal(QCursor.pos())

        self.ocr_rect = None
        self.color_pick_pos = self.mapFromGlobal(QCursor.pos())
        self.color_picker_visible = not (ocr_intent or recording_intent)
        self.color_output_hex = False
        self.selection_rect = QRect()
        self.hovered_window_rect = None
        self.hovered_window_title = ""
        if hasattr(self, 'window_snapper') and self.window_snapper:
            try:
                excl = {int(self.winId())}
                if self.parent_tool is not None:
                    try:
                        excl.add(int(self.parent_tool.winId()))
                    except Exception:
                        pass
                self.window_snapper.exclude_hwnds = excl
                self.window_snapper.refresh_window_tree()
            except Exception as e:
                logging.warning("刷新吸附窗口树异常: %s", e)
        self.crop_rect = None
        self.crop_mode = False
        self.crop_ratio = None
        self.crop_drag_handle = None
        self._cached_selection_pixmap = None
        self._close_text_select_layer()
        self._close_translation_overlay()
        self._close_translation_result_window()
        self._auto_ocr_timer.stop()
        self._text_flash_timer.stop()
        self._auto_ocr_pending = False
        self._text_flash = ""
        self._invalidate_text_doc()
        self._reload_toolbar_shortcuts()
        if hasattr(self, 'toolbar') and isinstance(self.toolbar, SnipasteToolbar):
            self.toolbar.refresh_tooltips()
            self.toolbar.set_active_tool(None)
        self.toolbar.hide()
        if hasattr(self, 'options_bar'):
            self.options_bar.hide()
            self.options_bar.set_tool(None)
        if hasattr(self, 'text_action_bar') and self.text_action_bar:
            self.text_action_bar.hide()
        self.update_undo_redo_states()
        self.inline_editor.hide()
        if hasattr(self, 'crop_toolbar'):
            self.crop_toolbar.hide()
        self.reset_button_styles()

        drawer = getattr(root, "pin_drawer", None)
        drawer_temporarily_hidden = False
        if drawer is not None:
            if hasattr(drawer, "_hide_hover_preview"):
                drawer._hide_hover_preview()
            if getattr(drawer, "_is_expanded", False):
                drawer.retract()
            if drawer.isVisible() and getattr(drawer, "_drawer_width", 0) > getattr(drawer, "HANDLE_WIDTH", 16) + 4:
                drawer.hide()
                drawer_temporarily_hidden = True
                QApplication.processEvents()

        t2 = _time.time()
        screen = QApplication.primaryScreen()
        self.original_screen = screen.grabWindow(0)

        # 截屏完成之后再临时隐藏活动中的父截图层、非模态弹窗及窗口置顶徽章，避免遮挡/抢占焦点
        self._temp_hidden_parent_info = None
        if self.parent_tool is not None and self.parent_tool.isVisible():
            pt = self.parent_tool
            tb_vis = bool(getattr(pt, "toolbar", None) and pt.toolbar.isVisible())
            opt_vis = bool(getattr(pt, "options_bar", None) and pt.options_bar.isVisible())
            crop_vis = bool(getattr(pt, "crop_toolbar", None) and pt.crop_toolbar.isVisible())
            if tb_vis:
                pt.toolbar.hide()
            if opt_vis:
                pt.options_bar.hide()
            if crop_vis:
                pt.crop_toolbar.hide()
            pt.hide()
            self._temp_hidden_parent_info = (pt, tb_vis, opt_vis, crop_vis)

        hidden_dlgs = []
        for dlg in _get_visible_nonmodal_dialogs():
            if dlg is self:
                continue
            try:
                if dlg.isVisible():
                    if isinstance(dlg, ImageEditorDialog):
                        dlg._temp_hidden_for_capture = True
                    dlg.hide()
                    hidden_dlgs.append(dlg)
            except RuntimeError:
                pass
        self._temp_hidden_dialogs = hidden_dlgs

        WindowPinManager.get_instance().set_overlay_suppressed(True)
        self._suppressed_pin_overlay = True

        if drawer_temporarily_hidden and drawer is not None and not getattr(drawer, "_boss_hidden", False):
            drawer.ensure_on_top()
        t3 = _time.time()
        self.original_screen_image = self.original_screen.toImage()
        self.blurred_screen = create_frosted_pixmap(self.original_screen, self.original_screen.width(), self.original_screen.height())
        t4 = _time.time()
        self.setGeometry(0, 0, self.original_screen.width(), self.original_screen.height())
        self.setMouseTracking(True)
        self.showFullScreen()
        _force_activate_window(self)
        self.setFocus()
        self.setCursor(Qt.CrossCursor)
        if not (ocr_intent or recording_intent):
            self.color_timer.start()
        self.update()
        t5 = _time.time()
        logging.info("截图模式已启动 (截图 %.0fms, toImage %.0fms, UI %.0fms, 总计 %.0fms)" % (
            (t3 - t2) * 1000, (t4 - t3) * 1000, (t5 - t4) * 1000, (t5 - t0) * 1000))

    def hide_app(self, keep_history=False):
        try:
            self.color_timer.stop()
            self.color_picker_visible = False
            self.crop_mode = False
            self.crop_rect = None
            self.crop_drag_handle = None
            self.ocr_intent = False
            self.recording_intent = False
            if hasattr(self, 'crop_toolbar'):
                self.crop_toolbar.hide()
            self._close_text_select_layer()
            self._close_translation_overlay()
            self._close_translation_result_window()
            self._auto_ocr_timer.stop()
            self._text_flash_timer.stop()
            self._auto_ocr_pending = False
            self._invalidate_text_doc()
            self.commit_text_edit()
            has_selection = hasattr(self, 'selection_rect') and not self.selection_rect.isNull() and not self.selection_rect.isEmpty()
            if keep_history and has_selection:
                try:
                    pixmap = self.get_final_static_image()
                    pos = self.selection_rect.topLeft()
                    self.add_to_capture_history(pixmap, pos=pos, is_pinned=False)
                except Exception as e:
                    logging.error("保存截图历史失败: %s", e, exc_info=True)
            if has_selection:
                self.selection_rect = QRect()
                self._cached_selection_pixmap = None
                self._cached_selection_key = None
            self._reset_rotation_state()
            self.original_screen = None
            self.original_screen_image = None
            self.blurred_screen = None
        finally:
            self._capture_active = False
            if hasattr(self, 'toolbar') and self.toolbar:
                self.toolbar.hide()
            if hasattr(self, 'options_bar') and self.options_bar:
                self.options_bar.hide()
            if hasattr(self, 'text_action_bar') and self.text_action_bar:
                self.text_action_bar.hide()
            self.hide()

            root = getattr(self, "root_tool", self)
            parent_info = getattr(self, "_temp_hidden_parent_info", None)
            self._temp_hidden_parent_info = None
            if parent_info is not None:
                pt, tb_vis, opt_vis, crop_vis = parent_info
                try:
                    if pt is not None and getattr(pt, "_capture_active", False):
                        pt.showFullScreen()
                        _force_activate_window(pt)
                        if tb_vis and getattr(pt, "toolbar", None):
                            pt.toolbar.show()
                            pt.toolbar.raise_()
                        if opt_vis and getattr(pt, "options_bar", None):
                            pt.options_bar.show()
                            pt.options_bar.raise_()
                        if crop_vis and getattr(pt, "crop_toolbar", None):
                            pt.crop_toolbar.show()
                            pt.crop_toolbar.raise_()
                        QTimer.singleShot(30, lambda w=pt: _force_activate_window(w) if w.isVisible() else None)
                except RuntimeError:
                    pass

            hidden_dlgs = list(getattr(self, "_temp_hidden_dialogs", []) or [])
            self._temp_hidden_dialogs = []
            top_restored = None
            for dlg in hidden_dlgs:
                try:
                    if isinstance(dlg, ImageEditorDialog):
                        dlg._temp_hidden_for_capture = False
                    if not getattr(dlg, "_really_closed_emitted", False):
                        dlg.show()
                        dlg.raise_()
                        top_restored = dlg
                except RuntimeError:
                    pass
            if top_restored is not None:
                try:
                    _force_activate_window(top_restored)
                except RuntimeError:
                    pass

            sec = getattr(root, "_secondary_capture", None)
            still_capturing = bool(getattr(root, "_capture_active", False) or (sec is not None and getattr(sec, "_capture_active", False)))
            if getattr(self, "_suppressed_pin_overlay", False):
                self._suppressed_pin_overlay = False
                if not still_capturing:
                    WindowPinManager.get_instance().set_overlay_suppressed(False)

            drawer = getattr(root, "pin_drawer", None)
            if drawer is not None and not still_capturing:
                drawer.ensure_on_top(force=True)

    def activate_recording(self):
        """激活屏幕录制模式 (F4)"""
        if self.parent_tool is None:
            sec = getattr(self, "_secondary_capture", None)
            if sec is not None and getattr(sec, "_capture_active", False):
                return sec.activate_recording()

        # 1. 若正在录屏中，再次按下快捷键直接完成录制
        root = getattr(self, "root_tool", self)
        if root.recording_thread and root.recording_thread.isRunning():
            root._on_stop_record()
            return

        # 2. 若已有有效选区正在编辑，直接以该选区开启录制
        if self.state == "EDITING" and hasattr(self, 'selection_rect') and not self.selection_rect.isNull() and self.selection_rect.width() > 10:
            rect = QRect(self.mapToGlobal(self.selection_rect.topLeft()), self.selection_rect.size())
            self.hide_app(keep_history=False)
            root.start_recording_session(rect)
            return

        # 若当前已在选区中，就地切换为录屏意图，避免多叠一层截图
        if self.isVisible() and self.state == "SELECTING":
            self.recording_intent = True
            self.ocr_intent = False
            self.color_picker_visible = False
            self.mode_banner_dismissed = False
            self.update()
            return

        # 3. 否则进入录屏选区模式 (完全免除深色遮罩)
        self.activate_capture(recording_intent=True)

    def activate_ocr(self):
        """激活快速文字识别与翻译模式 (F2)"""
        if self.parent_tool is None:
            sec = getattr(self, "_secondary_capture", None)
            if sec is not None and getattr(sec, "_capture_active", False):
                return sec.activate_ocr()

        # 1. 若已有有效选区正在编辑，直接以该选区开启 OCR
        if self.state == "EDITING" and hasattr(self, 'selection_rect') and not self.selection_rect.isNull() and self.selection_rect.width() > 10:
            rect = QRect(self.selection_rect)
            if self.has_pending_rotation_preview():
                self.commit_rotation_preview()
                rect = QRect(self.selection_rect)
            if getattr(self, '_rotated_base_pixmap', None) is not None:
                sub_pixmap = self._selection_base_pixmap()
            else:
                sub_pixmap = self.original_screen.copy(rect) if (hasattr(self, 'original_screen') and self.original_screen) else None
            self.hide_app(keep_history=False)
            self.start_quick_ocr_session(rect, sub_pixmap)
            return

        # 若当前已在选区中，就地切换为识图意图，避免多叠一层截图
        if self.isVisible() and self.state == "SELECTING":
            self.ocr_intent = True
            self.recording_intent = False
            self.color_picker_visible = False
            self.mode_banner_dismissed = False
            self.update()
            return

        # 2. 否则进入快速文字识别选区模式 (完全免除深色遮罩)
        self.activate_capture(ocr_intent=True)

    def activate_window_pin(self):
        """激活窗口置顶拾取模式 (DeskPin)"""
        if self.parent_tool is None:
            sec = getattr(self, "_secondary_capture", None)
            if sec is not None and getattr(sec, "_capture_active", False) and sec.isVisible():
                sec.hide_app(keep_history=False)
        if self.isVisible():
            self.hide_app(keep_history=False)
        WindowPinPicker.start_picking()

    def activate_scroll_capture(self):
        """激活滚动长截图模式"""
        if self.parent_tool is None:
            sec = getattr(self, "_secondary_capture", None)
            if sec is not None and getattr(sec, "_capture_active", False):
                return sec.activate_scroll_capture()
        if self.state == "EDITING" and hasattr(self, 'selection_rect') and not self.selection_rect.isNull() and self.selection_rect.width() >= SCROLL_MIN_CAPTURE_SIZE and self.selection_rect.height() >= SCROLL_MIN_CAPTURE_SIZE:
            self.start_scroll()
            return
        self.activate_capture()

    def start_quick_ocr_session(self, target_rect, pre_pixmap=None):
        """对目标矩形区域执行高精 OCR 识别并弹出多语言翻译窗口"""
        try:
            if pre_pixmap is not None and not pre_pixmap.isNull():
                sub_pixmap = pre_pixmap
            else:
                if hasattr(self, 'original_screen') and self.original_screen and not self.original_screen.isNull():
                    rect = target_rect.normalized().intersected(self.original_screen.rect())
                    if rect.width() < 5 or rect.height() < 5:
                        return
                    sub_pixmap = self.original_screen.copy(rect)
                else:
                    screen = QApplication.primaryScreen()
                    full = screen.grabWindow(0)
                    rect = target_rect.normalized().intersected(full.rect())
                    if rect.width() < 5 or rect.height() < 5:
                        return
                    sub_pixmap = full.copy(rect)

            QToolTip.showText(QCursor.pos(), "🔤 正在识别并翻译文字...", None, QRect(), 3000)
            ocr_img = sub_pixmap.toImage() if sub_pixmap else QImage()
            self._quick_ocr_worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))
            self._quick_ocr_worker.finished.connect(self._on_quick_ocr_finished)
            self._quick_ocr_worker.start()
        except Exception as e:
            logging.error("start_quick_ocr_session 异常: %s", e, exc_info=True)
            QToolTip.hideText()

    def _on_quick_ocr_finished(self, text):
        try:
            QToolTip.hideText()
            worker = getattr(self, "_quick_ocr_worker", None)
            codes = getattr(worker, "detected_codes", []) if worker is not None else []
            doc = getattr(worker, "ocr_doc", None) if worker is not None else None
            src_img = getattr(worker, "image", None) if worker is not None else None
            warn = getattr(worker, "ocr_warning", "") if worker is not None else ""
            if (not text or not text.strip()) and not codes:
                QMessageBox.information(None, "文字识别结果", "未能识别到有效文字或二维码，请重新框选。")
                return
            dlg = OcrResultDialog(
                text, None, detected_codes=codes, ocr_doc=doc,
                source_image=src_img, ocr_warning=warn
            )
            dlg.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
            self._active_ocr_dialog = dlg
            dlg.activateWindow()
            dlg.raise_()
            exec_dialog_nonmodal(dlg)
            if self._active_ocr_dialog is dlg:
                self._active_ocr_dialog = None
        except Exception as e:
            logging.error("_on_quick_ocr_finished 异常: %s", e, exc_info=True)

    # ---------- 取字 / 一键翻译 ----------
    def _valid_capture_selection(self):
        return (self.isVisible() and self.state == "EDITING" and not self.selection_rect.isNull()
                and self.selection_rect.width() > 10 and self.selection_rect.height() > 10)

    def _enter_modal_layer(self):
        """进入取字/译文层时隐藏截图工具栏，退出后恢复"""
        if hasattr(self, 'toolbar'):
            self.toolbar.hide()
        if hasattr(self, 'options_bar'):
            self.options_bar.hide()
        if hasattr(self, 'text_action_bar') and self.text_action_bar:
            self.text_action_bar.hide()

    def _leave_modal_layer(self):
        self.setCursor(Qt.CrossCursor)
        self.update_toolbar_positions()
        self.setFocus()

    def _on_text_layer_closed(self):
        layer = self._text_select_layer
        self._text_select_layer = None
        if layer is not None:
            try:
                layer.hide()
                layer.deleteLater()
            except RuntimeError:
                pass
        self._leave_modal_layer()

    def _on_translation_overlay_closed(self):
        overlay = self._translation_overlay
        self._translation_overlay = None
        if overlay is not None:
            self._retired_overlays.append(overlay)
            try:
                overlay.destroyed.connect(lambda _obj=None, ov=overlay: self._forget_overlay(ov))
            except RuntimeError:
                pass
        self._leave_modal_layer()

    def _forget_overlay(self, overlay):
        if overlay in self._retired_overlays:
            self._retired_overlays.remove(overlay)

    def _on_translation_window_destroyed(self, *_args):
        self._active_translation_window = None

    def _close_text_select_layer(self):
        if self._text_select_layer is not None:
            self._on_text_layer_closed()

    def _close_translation_overlay(self):
        overlay = self._translation_overlay
        if overlay is not None:
            try:
                overlay.close_overlay()
            except RuntimeError:
                self._on_translation_overlay_closed()

    def _close_translation_result_window(self):
        window = self._active_translation_window
        if window is not None:
            try:
                window.close()
            except RuntimeError:
                pass
            self._active_translation_window = None

    def _set_ocr_busy(self, busy):
        self._ocr_busy = busy
        btn = getattr(self.toolbar, 'btn_ocr_translate', None)
        if btn is not None:
            btn.setEnabled(not busy)

    def _current_capture_image(self, include_annotations=True):
        self.commit_text_edit()
        if not include_annotations and hasattr(self, 'original_screen') and self.original_screen and not self.selection_rect.isNull() and not self.has_pending_rotation_preview():
            has_content_edit = any(e.get('type') in ('TEXT', 'MOSAIC_BLUR', 'BLUR_STROKE') for e in (self.edits or []))
            if not has_content_edit:
                pixmap = self._selection_base_pixmap()
                return pixmap.toImage() if pixmap is not None and not pixmap.isNull() else QImage()
        pixmap = self.get_final_static_image()
        return pixmap.toImage() if pixmap is not None and not pixmap.isNull() else QImage()

    @staticmethod
    def _image_digest(image):
        """生成稳定的图像指纹，用于 OCR 结果缓存（QImage.cacheKey 每次都会变，不能用）。"""
        img = image.convertToFormat(QImage.Format_RGB888)
        ptr = img.bits()
        ptr.setsize(img.byteCount())
        digest = hashlib.md5(ptr.asstring(img.byteCount())).hexdigest()
        return (img.width(), img.height(), digest)

    def _request_ocr_doc(self, on_ready, on_no_coords=None, on_error=None, silent=False):
        """整图 OCR（强制本地引擎以保留文字坐标，长图自动滑窗切片不压缩），同一张图优先复用缓存。"""
        if self._ocr_running:
            self._ocr_waiters.append({"ready": on_ready, "no_coords": on_no_coords, "error": on_error})
            if not silent:
                QToolTip.showText(QCursor.pos(), "正在识别文字，请稍候…", None, QRect(), 1500)
            return

        image = self._current_capture_image(include_annotations=False)
        if image.isNull():
            if on_error:
                on_error("截图区域无效")
            return

        key = self._image_digest(image)
        if self._ocr_doc is not None and self._ocr_doc_key == key and not self._ocr_doc.is_empty():
            cached_doc = self._ocr_doc
            def _cached_ready():
                if self.isVisible() and self.state == "EDITING":
                    on_ready(cached_doc)
                    self._drain_ocr_waiters(doc=cached_doc)
            QTimer.singleShot(0, _cached_ready)
            return

        self._ocr_running = True
        generation = self._ocr_generation
        if not silent:
            self._set_ocr_busy(True)
            QToolTip.showText(QCursor.pos(), "🔤 正在识别文字…", None, QRect(), 4000)
        worker = OcrWorker(image, use_ai=False, fast=True)
        self._ocr_doc_worker = worker
        state = {"doc": None}

        def _on_doc(doc):
            if generation != self._ocr_generation:
                # 旧截图的识别结果已过期（选区被移动/裁剪），丢弃并重新识别当前选区
                self._ocr_running = False
                if not silent:
                    self._set_ocr_busy(False)
                    QToolTip.hideText()
                self._schedule_auto_text_ocr()
                return
            state["doc"] = doc
            self._ocr_doc = doc
            self._ocr_doc_key = key
            self._ocr_doc_image = image
            self._ocr_running = False
            if not silent:
                self._set_ocr_busy(False)
                QToolTip.hideText()
            if self.isVisible() and self.state == "EDITING":
                on_ready(doc)
            self._drain_ocr_waiters(doc=doc)

        def _on_finished(text):
            if generation != self._ocr_generation:
                self._ocr_running = False
                if not silent:
                    self._set_ocr_busy(False)
                    QToolTip.hideText()
                self._schedule_auto_text_ocr()
                return
            self._ocr_running = False
            if not silent:
                self._set_ocr_busy(False)
                QToolTip.hideText()
            if self._ocr_doc_worker is worker:
                self._ocr_doc_worker = None
            if state["doc"] is not None:
                return
            clean = (text or "").strip()
            if not self.isVisible() or self.state != "EDITING":
                self._drain_ocr_waiters(error="截图已关闭")
                return
            if silent and not clean and getattr(worker, "detected_codes", None):
                self._last_auto_codes = list(worker.detected_codes)
            if clean and not clean.startswith("识别发生错误"):
                if on_no_coords:
                    on_no_coords(clean)
                self._drain_ocr_waiters(text=clean)
            else:
                msg = clean or "未识别到文字"
                if on_error:
                    on_error(msg)
                self._drain_ocr_waiters(error=msg)

        worker.doc_ready.connect(_on_doc)
        worker.finished.connect(_on_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _drain_ocr_waiters(self, doc=None, text=None, error=None):
        """后台自动识别期间用户点了一键翻译时，等识别完成后再执行，避免点击丢失。"""
        waiters = self._ocr_waiters
        self._ocr_waiters = []
        for waiter in waiters:
            try:
                if not self.isVisible() or self.state != "EDITING":
                    continue
                if doc is not None:
                    waiter["ready"](doc)
                elif text:
                    if waiter.get("no_coords"):
                        waiter["no_coords"](text)
                elif waiter.get("error"):
                    waiter["error"](error or "未识别到文字")
            except Exception as e:
                logging.warning("OCR 等待回调异常: %s", e)

    # ---------- 截图后自动取字 ----------
    def _text_doc_ready(self):
        return (self._ocr_doc is not None and not self._ocr_doc.is_empty()
                and self.state == "EDITING" and not self.crop_mode
                and self.current_tool is None)

    def _text_geometry(self):
        doc = self._ocr_doc
        sel = self.selection_rect
        iw = max(1, doc.image_size.width()) if doc else 1
        ih = max(1, doc.image_size.height()) if doc else 1
        return (sel.width() / iw, sel.height() / ih, sel.left(), sel.top())

    def _text_widget_rect(self, char_rect):
        sx, sy, ox, oy = self._text_geometry()
        return QRect(ox + int(char_rect.left() * sx), oy + int(char_rect.top() * sy),
                     max(1, int(char_rect.width() * sx)), max(1, int(char_rect.height() * sy)))

    def _text_char_at(self, pos):
        if not self._text_doc_ready():
            return None
        sx, sy, ox, oy = self._text_geometry()
        if sx <= 0 or sy <= 0:
            return None
        return ocr_doc_char_at(self._ocr_doc, (pos.x() - ox) / sx, (pos.y() - oy) / sy)

    def _schedule_auto_text_ocr(self):
        if self._valid_capture_selection():
            self._auto_ocr_timer.start(120)

    def _run_auto_text_ocr(self):
        if not self._valid_capture_selection():
            return
        if self._text_select_layer is not None or self._translation_overlay is not None:
            return
        if getattr(self, '_auto_ocr_pending', False):
            return
        self._auto_ocr_pending = True
        self._text_ocr_hint = "正在识别文字…"
        self.update()

        def _ok(doc):
            self._auto_ocr_pending = False
            self._text_ocr_hint = ""
            if (self._text_doc_ready() and not self._text_dragging
                    and not getattr(self, 'drag_mode', None)
                    and not (QApplication.keyboardModifiers() & Qt.AltModifier)):
                pos = self.mapFromGlobal(QCursor.pos())
                if (self.selection_rect.contains(pos)
                        and self.get_resize_edge(pos, allow_move=False) is None):
                    hover = self._text_char_at(pos)
                    if hover is not None:
                        self._text_hover = hover
                        self.color_picker_visible = False
                        self.setCursor(Qt.IBeamCursor)
            self.update()

        def _fail(_msg=""):
            self._auto_ocr_pending = False
            self._text_ocr_hint = ""
            codes = getattr(self, "_last_auto_codes", None)
            if codes:
                self._last_auto_codes = None
                self._flash_text_status(f"🔲 检测到 {codes[0].get('type', '二维码')} · 按 F2 查看/复制", ms=3200)
                return
            self.update()

        self._last_auto_codes = None
        self._request_ocr_doc(_ok, on_no_coords=_fail, on_error=_fail, silent=True)

    def _reset_text_selection_state(self):
        self._text_dragging = False
        self._text_sel_start = QPoint()
        self._text_sel_start_index = None
        self._text_sel_column_rect = QRect()
        self._text_sel_rect = QRect()
        self._text_selected = set()
        self._text_hover = None
        bar = getattr(self, 'text_action_bar', None)
        if bar is not None:
            bar.hide()

    def _invalidate_text_doc(self):
        self._ocr_generation += 1
        self._ocr_waiters = []
        self._auto_ocr_pending = False
        self._ocr_doc = None
        self._ocr_doc_key = None
        self._ocr_doc_image = None
        self._text_ocr_hint = ""
        self._reset_text_selection_state()
        self.update()

    def _flash_text_status(self, text, ms=2600):
        self._text_flash = text
        self._text_flash_timer.start(ms)
        self.update()

    def _clear_text_flash(self):
        self._text_flash = ""
        self.update()

    def _text_column_rows(self, pos=None):
        """锁定起始文字所在栏；若拖拽明显跨过栏隙到达相邻列，则放开跨栏选取。"""
        if self._ocr_doc is None:
            return []
        target_lx = target_ly = None
        if pos is not None:
            sx, sy, ox, oy = self._text_geometry()
            if sx > 0 and sy > 0:
                target_lx = (pos.x() - ox) / sx
                target_ly = (pos.y() - oy) / sy
        return ocr_doc_column_rows(self._ocr_doc, self._text_sel_column_rect, target_lx, target_ly)

    def _text_flat(self, row_indices=None):
        return ocr_doc_flat(self._ocr_doc, row_indices)

    def _text_index_at(self, pos, row_indices=None):
        """把鼠标位置映射成阅读顺序中的字符索引，用于像文本编辑器一样线性选取。"""
        if not self._text_doc_ready():
            return None
        sx, sy, ox, oy = self._text_geometry()
        if sx <= 0 or sy <= 0:
            return None
        return ocr_doc_index_at(self._ocr_doc, (pos.x() - ox) / sx, (pos.y() - oy) / sy, row_indices)

    def _start_text_selection(self, pos):
        self.drag_mode = None
        self._text_dragging = True
        self._text_flash = ""
        self._text_flash_timer.stop()
        self._text_sel_start = pos
        self._text_sel_column_rect = QRect()
        self._text_sel_start_index = self._text_index_at(pos)
        if self._text_sel_start_index is not None and self._ocr_doc is not None:
            ri = self._text_sel_start_index[0]
            self._text_sel_column_rect = QRect(self._ocr_doc.rows[ri].rect)
        self._text_sel_rect = QRect(pos, pos)
        self._text_selected = set()
        self._text_hover = None
        self.color_picker_visible = False
        self.setCursor(Qt.IBeamCursor)
        self.update()

    def _update_text_selection(self, pos):
        self._text_sel_rect = QRect(self._text_sel_start, pos).normalized()
        if self._ocr_doc is None or self._text_sel_start_index is None:
            return
        row_indices = self._text_column_rows(pos)
        end_index = self._text_index_at(pos, row_indices)
        if end_index is None:
            return
        flat = self._text_flat(row_indices)
        try:
            a = flat.index(self._text_sel_start_index)
            b = flat.index(end_index)
        except ValueError:
            return
        lo, hi = (a, b) if a <= b else (b, a)
        self._text_selected = set(flat[lo:hi + 1])
        self.update()

    def _finish_text_selection(self, pos):
        self._text_dragging = False
        moved = (pos - self._text_sel_start).manhattanLength()
        if moved < 3:
            # 单击不复制，只清掉之前的选区；整行复制改为双击
            self._text_selected.clear()
            self._text_sel_rect = QRect()
            self._text_hover = self._text_char_at(pos)
            self._update_text_action_bar()
            self.update()
            return
        self._update_text_selection(pos)
        self._update_text_action_bar()
        self.update()

    def _selected_text(self):
        return ocr_doc_selected_text(self._ocr_doc, self._text_selected)

    def _copy_selected_text(self):
        text = self._selected_text()
        if not text:
            return False
        QApplication.clipboard().setText(text)
        # 复制后立即清掉文字高亮（不遮挡原文），并在选区外侧显示确认胶囊
        self._reset_text_selection_state()
        self._flash_text_status(f"✓ 已复制 {len(text)} 字 · Esc 退出", ms=2200)
        self.repaint()
        return True

    def _select_all_text(self):
        if not self._text_doc_ready():
            return
        self._text_selected = {(ri, ci) for ri, row in enumerate(self._ocr_doc.rows)
                               for ci in range(len(row.text))}
        self._text_sel_rect = QRect(self.selection_rect)
        self._text_flash = ""
        self._text_flash_timer.stop()
        self._update_text_action_bar()
        self.update()

    def _clear_text_selection(self):
        self._reset_text_selection_state()
        self._text_flash = ""
        self._text_flash_timer.stop()
        self.update()

    def _place_text_status_pill(self, width, height):
        """选择不与工具栏/选项栏及选区文字重叠的胶囊位置。"""
        bounds = self.rect().adjusted(6, 6, -6, -6)
        sel = self.selection_rect
        x_left = max(bounds.left(), min(sel.left(), bounds.right() - width + 1))

        blocked = []
        for bar in (getattr(self, 'toolbar', None), getattr(self, 'options_bar', None), getattr(self, 'text_action_bar', None)):
            try:
                if bar is not None and bar.isVisible():
                    blocked.append(bar.geometry().adjusted(-6, -4, 6, 4))
            except RuntimeError:
                pass

        candidates = [
            QRect(x_left, sel.bottom() + 8, width, height),
            QRect(x_left, sel.top() - height - 8, width, height),
        ]
        tb = getattr(self, 'toolbar', None)
        if tb is not None and tb.isVisible():
            tb_g = tb.geometry()
            candidates.append(QRect(tb_g.left() - width - 8, tb_g.top() + (tb_g.height() - height) // 2, width, height))
            candidates.append(QRect(tb_g.right() + 9, tb_g.top() + (tb_g.height() - height) // 2, width, height))
        candidates.extend([
            QRect(sel.left() + 10, sel.top() + 10, width, height),
            QRect(sel.left() + 10, max(bounds.top(), sel.bottom() - height - 10), width, height),
        ])

        for cand in candidates:
            if bounds.contains(cand) and not any(cand.intersects(b) for b in blocked):
                return cand
        for cand in candidates:
            if bounds.contains(cand):
                return cand
        cx = max(bounds.left(), min(x_left, max(bounds.left(), bounds.right() - width + 1)))
        cy = max(bounds.top(), min(sel.top() + 10, max(bounds.top(), bounds.bottom() - height + 1)))
        return QRect(cx, cy, width, height)

    def _active_text_status_pill(self):
        """返回当前应当显示的取字状态文案与色调: (text, tone)。"""
        if self._text_flash:
            return self._text_flash, "ok"
        if self.current_tool is None and not self.crop_mode and not getattr(self, 'drag_mode', None):
            if self._text_selected:
                bar = getattr(self, 'text_action_bar', None)
                if bar is not None and bar.isVisible():
                    return "", "select"
                sel_len = len(self._selected_text())
                if sel_len > 0:
                    hint = f"已选 {sel_len} 字" if self._text_dragging else f"已选 {sel_len} 字 · Ctrl+C / Enter 复制"
                    return hint, "select"
            if self._text_ocr_hint:
                return self._text_ocr_hint, "info"
        return "", "info"

    def _draw_text_status_pill(self, painter, text, tone="info"):
        """状态提示优先停靠在选区外侧并自动避让工具栏。"""
        if not text:
            return
        font = make_ui_font(pixel_size=12)
        painter.setFont(font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 22
        h = 26
        rect = self._place_text_status_pill(w, h)
        palettes = {
            "ok": (QColor(240, 253, 244, 246), QColor(134, 239, 172), QColor(21, 128, 61)),
            "select": (QColor(251, 247, 238, 244), QColor(229, 210, 174), QColor(122, 81, 16)),
            "info": (QColor(255, 255, 255, 242), QColor(203, 213, 225), QColor(51, 65, 85)),
        }
        bg, border, fg = palettes.get(tone, palettes["info"])
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.setBrush(Qt.NoBrush)

    def _selected_ocr_doc(self):
        """基于当前选中的文字构建子版面对象，供表格 TSV 还原或局部翻译使用。"""
        return ocr_doc_selected_subdoc(self._ocr_doc, self._text_selected)

    def _copy_selected_as_table(self):
        text = self._selected_text()
        if not text:
            return False
        tsv = extract_table_tsv(self._selected_ocr_doc(), text)
        if not tsv.strip():
            return False
        QApplication.clipboard().setText(tsv)
        self._reset_text_selection_state()
        row_cnt = len([ln for ln in tsv.splitlines() if ln.strip()])
        self._flash_text_status(f"✓ 已复制表格 ({row_cnt} 行) · Esc 退出", ms=2200)
        self.repaint()
        return True

    def _translate_selected_text(self):
        text = self._selected_text()
        if not text:
            return
        sub_doc = self._selected_ocr_doc()
        self._enter_modal_layer()
        dlg = None
        try:
            dlg = OcrResultDialog(text, self, ocr_doc=sub_doc)
            self._active_ocr_dialog = dlg
            exec_dialog_nonmodal(dlg)
        finally:
            if dlg is not None and getattr(self, "_active_ocr_dialog", None) is dlg:
                self._active_ocr_dialog = None
            if self.isVisible():
                self._leave_modal_layer()

    def _show_text_context_menu(self, global_pos):
        """选中文字或右键点击文字时弹出快捷菜单，避免误退出截图。"""
        text = self._selected_text()
        if not text:
            return
        chosen = exec_text_selection_menu(
            self, global_pos, text,
            on_copy=self._copy_selected_text,
            on_copy_table=self._copy_selected_as_table,
            on_translate=self._translate_selected_text,
            on_select_all=self._select_all_text,
            on_clear=self._clear_text_selection,
            on_copy_exit=lambda: self.hide_app(keep_history=False) if self._copy_selected_text() else None,
            extra_exit_action=("🚪 退出截图", lambda: self.hide_app(keep_history=False)),
        )
        if chosen is None and self.isVisible():
            self._update_text_action_bar()
            self.setFocus()

    def _text_selection_bounds(self):
        """返回当前选中文字在窗口坐标系中的外接矩形。"""
        doc = self._ocr_doc
        if doc is None or not self._text_selected:
            return QRect()
        bounds = QRect()
        for ri, row in enumerate(doc.rows):
            idxs = [ci for (r, ci) in self._text_selected if r == ri and ci < len(row.char_rects)]
            if not idxs:
                continue
            ra = self._text_widget_rect(row.char_rects[min(idxs)])
            rb = self._text_widget_rect(row.char_rects[max(idxs)])
            row_r = QRect(
                ra.left(), min(ra.top(), rb.top()),
                max(1, rb.right() - ra.left() + 1), max(ra.height(), rb.height())
            )
            bounds = row_r if bounds.isNull() else bounds.united(row_r)
        return bounds

    def _update_text_action_bar(self):
        bar = getattr(self, 'text_action_bar', None)
        if bar is None:
            return
        should_show = (
            self.isVisible()
            and self.state == "EDITING"
            and not self.crop_mode
            and self.current_tool is None
            and not self._text_dragging
            and not getattr(self, 'drag_mode', None)
            and self._text_select_layer is None
            and self._translation_overlay is None
            and bool(self._text_selected)
        )
        if not should_show:
            bar.hide()
            return
        text = self._selected_text()
        if not text:
            bar.hide()
            return

        sub_doc = self._selected_ocr_doc()
        has_table = bool(sub_doc is not None and len(sub_doc.rows) >= 2)
        has_url = bool(re.search(r'https?://[^\s<>"\')\]}]+', text))
        bar.configure(len(text), has_table=has_table, has_url=has_url)
        hint = bar.sizeHint()
        bw, bh = hint.width(), hint.height()
        bar.resize(bw, bh)

        sel_bounds = self._text_selection_bounds()
        if sel_bounds.isNull():
            sel_bounds = QRect(self.selection_rect)
        screen_bounds = self.rect().adjusted(4, 4, -4, -4)
        pad = getattr(bar, 'SHADOW_PAD', 8)

        blocked = []
        for other in (getattr(self, 'toolbar', None), getattr(self, 'options_bar', None)):
            try:
                if other is not None and other.isVisible():
                    blocked.append(other.geometry().adjusted(-4, -4, 4, 4))
            except RuntimeError:
                pass

        def _clamp_x(x_val):
            return max(screen_bounds.left(), min(x_val, max(screen_bounds.left(), screen_bounds.right() - bw + 1)))

        def _clamp_y(y_val):
            return max(screen_bounds.top(), min(y_val, max(screen_bounds.top(), screen_bounds.bottom() - bh + 1)))

        x_pref = _clamp_x(sel_bounds.left() - pad)
        sel = self.selection_rect
        candidates = [
            QRect(x_pref, sel_bounds.bottom() + 4 - pad, bw, bh),
            QRect(x_pref, sel_bounds.top() - bh - 4 + pad, bw, bh),
            QRect(_clamp_x(sel.left() - pad), sel.bottom() + 6 - pad, bw, bh),
            QRect(_clamp_x(sel.left() - pad), sel.top() - bh - 6 + pad, bw, bh),
            QRect(x_pref, _clamp_y(sel_bounds.top() + 8), bw, bh),
        ]

        chosen = None
        for cand in candidates:
            if screen_bounds.contains(cand) and not any(cand.intersects(b) for b in blocked):
                chosen = cand
                break
        if chosen is None:
            for cand in candidates:
                if screen_bounds.contains(cand):
                    chosen = cand
                    break
        if chosen is None:
            chosen = QRect(x_pref, _clamp_y(sel_bounds.bottom() + 4 - pad), bw, bh)

        bar.move(chosen.topLeft())
        bar.show()
        bar.raise_()
        bar.update_frosted_bg()

    def _on_text_bar_action(self, action):
        if action == 'COPY':
            self._copy_selected_text()
        elif action == 'COPY_EXIT':
            if self._copy_selected_text():
                self.hide_app(keep_history=False)
        elif action == 'TABLE':
            self._copy_selected_as_table()
        elif action == 'TRANSLATE':
            self._translate_selected_text()
        elif action == 'OPEN_URL':
            m = re.search(r'https?://[^\s<>"\')\]}]+', self._selected_text())
            if m:
                webbrowser.open(m.group(0))

    def _draw_text_layer(self, painter):
        ocr_doc_draw_selection(painter, self._ocr_doc, self._text_selected, self._text_hover, self._text_widget_rect)

        pill_text, pill_tone = self._active_text_status_pill()
        if pill_text:
            self._draw_text_status_pill(painter, pill_text, tone=pill_tone)

    def translate_current_capture(self):
        """一键翻译：OCR 后生成覆盖译文的图片，按设置显示单图或左右对照。"""
        if self.has_pending_rotation_preview():
            self.commit_rotation_preview()
        if not self._valid_capture_selection():
            QMessageBox.information(None, "一键翻译", "请先框选需要翻译的截图区域。")
            return

        self._close_text_select_layer()
        self._close_translation_overlay()
        self._enter_modal_layer()

        def _ready(doc):
            text = doc.text.strip() if doc is not None else ""
            if not text:
                self._leave_modal_layer()
                QMessageBox.information(None, "一键翻译", "未识别到文字，请重新框选清晰区域。")
                return
            target = resolve_ocr_target_lang(text)
            use_ai = bool(load_settings().get("use_ai_translation", False))
            mode = str(load_settings().get("translation_display_mode", "dialog") or "dialog")

            if self._ocr_doc_image is not None and not self._ocr_doc_image.isNull():
                base_pixmap = QPixmap.fromImage(self._ocr_doc_image)
            else:
                base_pixmap = self.get_final_static_image()
            if base_pixmap is None or base_pixmap.isNull():
                self._leave_modal_layer()
                QMessageBox.warning(None, "一键翻译失败", "无法获取当前截图。")
                return

            window = TranslationResultWindow(
                base_pixmap, doc, target, use_ai,
                mode="overlay" if mode == "overlay" else "compare",
                parent=self
            )
            self._active_translation_window = window
            try:
                window.destroyed.connect(self._on_translation_window_destroyed)
            except Exception:
                pass
            try:
                exec_dialog_nonmodal(window)
            finally:
                if self._active_translation_window is window:
                    self._active_translation_window = None
                if self.isVisible():
                    self._leave_modal_layer()

        def _no_coords(text):
            # 极少数本地引擎完全没有坐标时，退化为文字对照（仍携带可能检出的条码）
            if self.isVisible():
                self._leave_modal_layer()
            img_for_code = self.get_final_static_image()
            codes = detect_codes_in_image(img_for_code)
            dlg = OcrResultDialog(
                text, self, detected_codes=codes,
                source_image=img_for_code.toImage() if img_for_code and not img_for_code.isNull() else None
            )
            self._active_ocr_dialog = dlg
            exec_dialog_nonmodal(dlg)
            if self._active_ocr_dialog is dlg:
                self._active_ocr_dialog = None

        def _error(err):
            self._leave_modal_layer()
            QMessageBox.warning(None, "一键翻译失败", err or "未识别到文字")

        self._request_ocr_doc(_ready, on_no_coords=_no_coords, on_error=_error)

    def start_recording_session(self, target_rect):
        """开启录屏准备会话（显示红框与悬浮控制条）"""
        if self.parent_tool is not None:
            return self.root_tool.start_recording_session(target_rect)
        self._cleanup_recording_ui()

        target_rect = target_rect.normalized()
        w = target_rect.width()
        h = target_rect.height()
        if w % 2 != 0: w -= 1
        if h % 2 != 0: h -= 1
        w = max(64, w)
        h = max(64, h)
        self.recording_target_rect = QRect(target_rect.x(), target_rect.y(), w, h)

        # 全屏录制时完全不显示全屏大红框，保持屏幕纯净无干扰；仅在局部选区录制时显示外扩取景框
        screen_geom = QApplication.primaryScreen().geometry()
        is_fullscreen = (w >= screen_geom.width() - 8 and h >= screen_geom.height() - 8)
        if not is_fullscreen:
            self.recording_border = RecordBorderFrame(self.recording_target_rect)
        else:
            self.recording_border = None
        self.recording_toolbar = RecordingToolbar(self.recording_target_rect, default_quality=self.recording_quality_key)
        self.recording_toolbar.start_requested.connect(self._on_start_record)
        self.recording_toolbar.stop_requested.connect(self._on_stop_record)
        self.recording_toolbar.pause_toggled.connect(self._on_pause_record)
        self.recording_toolbar.cancel_requested.connect(self._on_cancel_record)
        self.recording_toolbar.quality_changed.connect(self._on_recording_quality_changed)
        if self.hotkey_listener is not None and hasattr(self.hotkey_listener, "register_recording_esc_hotkey"):
            self.hotkey_listener.register_recording_esc_hotkey()

    def _on_recording_quality_changed(self, quality_key):
        self.recording_quality_key = quality_key

    def _on_start_record(self):
        if not self.recording_target_rect:
            return
        if self.recording_thread and self.recording_thread.isRunning():
            return

        self.recording_thread = ScreenRecorderThread(self.recording_target_rect, self.recording_quality_key)
        self.recording_thread.time_tick.connect(self.recording_toolbar.update_timer)
        self.recording_thread.recording_finished.connect(self._on_recording_finished)
        self.recording_thread.error_occurred.connect(self._on_recording_error)
        self.recording_thread.start()

        if self.recording_border:
            self.recording_border.set_recording(True)
        if self.recording_toolbar:
            self.recording_toolbar.set_recording_state(True)

    def _on_pause_record(self, is_paused):
        if self.recording_thread:
            if is_paused:
                self.recording_thread.pause()
            else:
                self.recording_thread.resume()

    def _on_stop_record(self):
        try:
            if self.recording_thread and self.recording_thread.isRunning():
                self.recording_thread.stop()
                if self.recording_border:
                    self.recording_border.set_recording(False)
                if self.recording_toolbar:
                    self.recording_toolbar.set_recording_state(False)
        except Exception as e:
            logging.error("_on_stop_record 异常: %s", e, exc_info=True)

    def _on_cancel_record(self):
        try:
            if self.recording_thread and self.recording_thread.isRunning():
                try:
                    self.recording_thread.recording_finished.disconnect()
                except (TypeError, RuntimeError):
                    pass
                try:
                    self.recording_thread.error_occurred.disconnect()
                except (TypeError, RuntimeError):
                    pass
                self.recording_thread.stop()
                self.recording_thread.wait(2000)
                if self.recording_thread.temp_video_path and os.path.exists(self.recording_thread.temp_video_path):
                    try:
                        os.remove(self.recording_thread.temp_video_path)
                    except Exception:
                        pass
        except Exception as e:
            logging.error("_on_cancel_record 异常: %s", e, exc_info=True)
        finally:
            self.recording_thread = None
            self._cleanup_recording_ui()

    def _on_recording_finished(self, info):
        logging.info("录屏完成信号到达，正在清理录制UI并安全同步线程生命周期...")
        try:
            if self.recording_thread is not None:
                self.recording_thread.wait(3000)
                self.recording_thread = None
        except Exception as e:
            logging.error("等待录屏线程结束失败: %s", e, exc_info=True)
            self.recording_thread = None

        self._cleanup_recording_ui()

        video_path = info.get('video_path', '')
        if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            logging.warning("录屏文件不存在或为空: %s", video_path)
            QMessageBox.warning(None, "录屏提示", "录屏时长过短或未采集到有效视频帧。")
            return

        try:
            logging.info("正在打开录制导出预览窗口: %s", video_path)
            dialog = RecordingExportDialog(info, parent=None)
            exec_dialog_nonmodal(dialog)
        except Exception as e:
            logging.error("打开 RecordingExportDialog 异常: %s", e, exc_info=True)
            QMessageBox.critical(None, "导出错误", f"无法打开录屏导出窗口:\n{e}")

    def _on_recording_error(self, err_msg):
        logging.error("录屏错误信号到达: %s", err_msg)
        try:
            if self.recording_thread is not None:
                self.recording_thread.wait(2000)
                self.recording_thread = None
        except Exception:
            self.recording_thread = None
        self._cleanup_recording_ui()
        QMessageBox.critical(None, "录屏错误", f"屏幕录制出现错误:\n{err_msg}")

    def _cleanup_recording_ui(self):
        if self.recording_border:
            try:
                self.recording_border.hide()
                self.recording_border.close()
                self.recording_border.deleteLater()
            except Exception:
                pass
            self.recording_border = None
        if self.recording_toolbar:
            try:
                self.recording_toolbar.hide()
                self.recording_toolbar.close()
                self.recording_toolbar.deleteLater()
            except Exception:
                pass
            self.recording_toolbar = None
        if self.hotkey_listener is not None and hasattr(self.hotkey_listener, "unregister_recording_esc_hotkey"):
            self.hotkey_listener.unregister_recording_esc_hotkey()

    def reset_button_styles(self):
        pass

    def update_cursor_for_tool(self, pos=None):
        """统一管理工具光标，杜绝光标状态卡在边缘拉伸等异常状态"""
        if self._apply_editing_tool_cursor(
            self.current_tool,
            pos=pos,
            options_bar=getattr(self, 'options_bar', None),
            default_thickness=getattr(self, 'current_thickness', 3),
            allow_text_hit=(self.state == "EDITING"),
        ):
            return
        if self.current_tool is None:
            if pos is not None and self.state == "EDITING":
                edge = self.get_resize_edge(pos, allow_move=True)
                if edge in ['topleft', 'bottomright']:
                    self.setCursor(Qt.SizeFDiagCursor)
                elif edge in ['topright', 'bottomleft']:
                    self.setCursor(Qt.SizeBDiagCursor)
                elif edge in ['left', 'right']:
                    self.setCursor(Qt.SizeHorCursor)
                elif edge in ['top', 'bottom']:
                    self.setCursor(Qt.SizeVerCursor)
                elif edge == 'move':
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
            elif self.state == "EDITING":
                self.setCursor(Qt.ArrowCursor)
            else:
                self.setCursor(Qt.CrossCursor)

    def set_tool(self, tool_name, active_btn=None):
        self.commit_text_edit()
        if self.has_pending_rotation_preview() and (tool_name != 'ROTATE' or self.current_tool == 'ROTATE'):
            self.commit_rotation_preview()
        if self.crop_mode and tool_name != 'CROP':
            self.cancel_crop()

        legacy_map = {
            'RECTANGLE': 'RECT_ELLIPSE',
            'ELLIPSE': 'RECT_ELLIPSE',
            'LINE': 'PEN',
            'HIGHLIGHTER': 'MARKER',
            'BLUR': 'MOSAIC_BLUR',
            'ERASE': 'ERASER'
        }
        if tool_name in legacy_map:
            tool_name = legacy_map[tool_name]

        if tool_name == self.current_tool or tool_name is None:
            self.current_tool = None
            if hasattr(self, 'toolbar') and isinstance(self.toolbar, SnipasteToolbar):
                self.toolbar.set_active_tool(None)
            if hasattr(self, 'options_bar') and isinstance(self.options_bar, ToolOptionsBar):
                self.options_bar.set_tool(None)
            self.update_cursor_for_tool()
            self.update_toolbar_positions()
            return

        self.current_tool = tool_name
        self._reset_text_selection_state()
        self._text_flash = ""
        if hasattr(self, 'toolbar') and isinstance(self.toolbar, SnipasteToolbar):
            self.toolbar.set_active_tool(tool_name)
        if hasattr(self, 'options_bar') and isinstance(self.options_bar, ToolOptionsBar):
            self.options_bar.set_tool(tool_name)
        self.update_cursor_for_tool()
        self.update_toolbar_positions()
        if tool_name == 'CROP':
            self.enter_crop_mode()
        elif tool_name == 'SCROLL':
            self.start_scroll()
        elif self.crop_mode:
            self.exit_crop_mode()
        if self.isVisible() and not (hasattr(self, 'inline_editor') and self.inline_editor.isVisible()):
            self.setFocus()

    def _reload_toolbar_shortcuts(self, settings=None):
        """从配置加载截图底部工具栏工具的快捷键映射表"""
        s = settings if isinstance(settings, dict) else load_settings()
        mapping = {}
        for cfg_key, action_id, kind, _label, _desc in TOOLBAR_TOOL_HOTKEY_SPECS:
            hk = normalize_hotkey_str(s.get(cfg_key, ""))
            if hk:
                mapping[hk] = (action_id, kind)
        self._toolbar_shortcut_map = mapping

    def _trigger_toolbar_hotkey_action(self, action_id, kind):
        """执行截图底部工具栏工具的绑定快捷键动作"""
        if kind == "tool":
            self.on_tool_triggered(action_id)
        elif action_id == "CROP":
            if self.crop_mode:
                self.cancel_crop()
            else:
                self.on_action_triggered("CROP")
        elif action_id == "TRANSLATE":
            if self.crop_mode:
                self.cancel_crop()
            self.on_action_triggered("TRANSLATE")

    def get_resize_edge(self, pos, allow_move=True):
        r = self.selection_rect
        if not r.isValid() or r.isNull():
            return None
        if self._rotation_locked_edges():
            return 'move' if (allow_move and r.contains(pos)) else None
        margin = min(10, max(3, min(r.width(), r.height()) // 3))
        x, y = pos.x(), pos.y()
        in_x = (r.left() - margin <= x <= r.right() + margin)
        in_y = (r.top() - margin <= y <= r.bottom() + margin)
        left = abs(x - r.left()) < margin and in_y
        right = abs(x - r.right()) < margin and in_y
        top = abs(y - r.top()) < margin and in_x
        bottom = abs(y - r.bottom()) < margin and in_x

        if left and top: return 'topleft'
        if right and top: return 'topright'
        if left and bottom: return 'bottomleft'
        if right and bottom: return 'bottomright'
        if left: return 'left'
        if right: return 'right'
        if top: return 'top'
        if bottom: return 'bottom'
        if r.contains(pos): return 'move' if allow_move else None
        return None

    def _rotation_locked_edges(self):
        """旋转后的选区内容已脱离屏幕原图，只允许整体移动，不允许拉伸边缘"""
        return getattr(self, '_rotated_base_pixmap', None) is not None or self.has_pending_rotation_preview()

    def commit_text_edit(self):
        if not hasattr(self, 'inline_editor') or not self.inline_editor.isVisible():
            return
        if self._commit_inline_text_edit():
            if self.isVisible() and self.state == "EDITING":
                self._invalidate_text_doc()
                self._schedule_auto_text_ocr()
        self.update()

    def sample_screen_color(self, pos):
        if not hasattr(self, 'original_screen_image'):
            return QColor(0, 0, 0)
        x = max(0, min(pos.x(), self.original_screen_image.width() - 1))
        y = max(0, min(pos.y(), self.original_screen_image.height() - 1))
        return self.original_screen_image.pixelColor(x, y)

    def format_color_value(self, color):
        if self.color_output_hex:
            return color.name().upper()
        return f"{color.red()}, {color.green()}, {color.blue()}"

    def copy_color_value(self, pos=None):
        if pos is not None:
            self.color_pick_pos = pos
        if self.color_pick_pos is None:
            return
        color = self.sample_screen_color(self.color_pick_pos)
        QApplication.clipboard().setText(self.format_color_value(color))
        self.update()

    def toggle_color_format(self):
        self.color_output_hex = not self.color_output_hex
        self.update()

    def update_color_picker_from_cursor(self):
        if self.state != "SELECTING" or not self.color_picker_visible:
            if self.color_timer.isActive():
                self.color_timer.stop()
            return
        self.color_pick_pos = self.mapFromGlobal(QCursor.pos())
        self.update()

    def draw_color_picker(self, painter):
        if not self.color_picker_visible or self.color_pick_pos is None:
            return
        if getattr(self, 'ocr_intent', False) or getattr(self, 'recording_intent', False):
            return

        pos = self.color_pick_pos
        color = self.sample_screen_color(pos)
        zoom_size = COLOR_MAGNIFIER_SIZE
        info_h = COLOR_PICKER_INFO_HEIGHT
        margin = 16
        box_w = COLOR_PICKER_WIDTH
        box_h = zoom_size + info_h
        screen_rect = self.rect()

        x = pos.x() + 24
        y = pos.y() + 24
        if x + box_w > screen_rect.right():
            x = pos.x() - box_w - 24
        if y + box_h > screen_rect.bottom():
            y = pos.y() - box_h - 24
        x = max(margin, min(x, screen_rect.width() - box_w - margin))
        y = max(margin, min(y, screen_rect.height() - box_h - margin))

        half = COLOR_MAGNIFIER_SOURCE // 2
        src = QRect(pos.x() - half, pos.y() - half, COLOR_MAGNIFIER_SOURCE, COLOR_MAGNIFIER_SOURCE).intersected(self.original_screen.rect())
        if src.isEmpty():
            return

        zoom_rect = QRect(x, y, zoom_size, zoom_size)
        info_rect = QRect(x, y + zoom_size, box_w, info_h)
        painter.setPen(QPen(QColor(60, 64, 67), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawRect(zoom_rect)
        painter.drawPixmap(zoom_rect, self.original_screen.copy(src))

        painter.setPen(QPen(QColor(212, 162, 72), 2))
        cx, cy = zoom_rect.center().x(), zoom_rect.center().y()
        painter.drawLine(cx, zoom_rect.top(), cx, zoom_rect.bottom())
        painter.drawLine(zoom_rect.left(), cy, zoom_rect.right(), cy)

        painter.fillRect(info_rect, QColor(35, 38, 43, 235))
        painter.setPen(QPen(Qt.white))
        painter.setFont(make_ui_font(9))
        value_label = "HEX" if self.color_output_hex else "RGB"
        painter.drawText(info_rect.adjusted(8, 6, -6, -6), Qt.AlignLeft | Qt.AlignTop,
                         f"坐标: {pos.x()}, {pos.y()}\n{value_label}: {self.format_color_value(color)}\nC 复制颜色值\nShift 切换 RGB/HEX")

    def wheelEvent(self, event):
        if self.state == "EDITING" and not self.crop_mode:
            delta = event.angleDelta().y()
            step = 1 if delta > 0 else -1
            if self.current_tool == 'RECT_ELLIPSE':
                if hasattr(self, 'options_bar') and hasattr(self.options_bar, 'spin_re_width'):
                    self.options_bar.spin_re_width.setValue(self.options_bar.spin_re_width.value() + step)
            elif self.current_tool == 'ARROW':
                if hasattr(self, 'options_bar') and hasattr(self.options_bar, 'spin_arrow_width'):
                    self.options_bar.spin_arrow_width.setValue(self.options_bar.spin_arrow_width.value() + step)
            elif self.current_tool == 'PEN':
                if hasattr(self, 'options_bar') and hasattr(self.options_bar, 'spin_pen_width'):
                    self.options_bar.spin_pen_width.setValue(self.options_bar.spin_pen_width.value() + step)
            elif self.current_tool == 'MARKER':
                if hasattr(self, 'options_bar') and hasattr(self.options_bar, 'spin_marker_width'):
                    self.options_bar.spin_marker_width.setValue(self.options_bar.spin_marker_width.value() + step * 2)
            elif self.current_tool == 'MOSAIC_BLUR':
                if hasattr(self, 'options_bar') and hasattr(self.options_bar, 'spin_mb_brush'):
                    self.options_bar.spin_mb_brush.setValue(self.options_bar.spin_mb_brush.value() + step * 2)
            elif self.current_tool == 'TEXT':
                if hasattr(self, 'options_bar') and hasattr(self.options_bar, 'spin_text_size'):
                    self.options_bar.spin_text_size.setValue(self.options_bar.spin_text_size.value() + step)
            elif self.current_tool in ['LINE', 'RECTANGLE', 'BLUR', 'ELLIPSE', 'HIGHLIGHTER', 'STEP']:
                if delta > 0:
                    self.current_thickness = min(20, self.current_thickness + 1)
                else:
                    self.current_thickness = max(1, self.current_thickness - 1)
                if self.edits and self.edits[-1].get('temp'):
                    self.edits[-1]['thickness'] = self.current_thickness
            self.update_cursor_for_tool(event.pos())
            self.update()

    def _handle_editing_keypress(self, event, fw, is_inline_editing, in_spinbox):
        if not self.crop_mode:
            if event.key() == Qt.Key_C and event.modifiers() & Qt.ControlModifier and self._text_selected:
                if self._copy_selected_text():
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._text_selected:
                if self._copy_selected_text():
                    return True
            if event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier and self._text_doc_ready():
                self._select_all_text()
                return True
            if event.key() == Qt.Key_Escape and self._text_selected:
                self._clear_text_selection()
                return True

        allow_toolbar_hk = not (is_inline_editing and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)))
        if allow_toolbar_hk and self._toolbar_shortcut_map:
            hk_pressed = key_event_to_hotkey_str(event)
            if hk_pressed and hk_pressed in self._toolbar_shortcut_map:
                if not event.isAutoRepeat():
                    action_id, kind = self._toolbar_shortcut_map[hk_pressed]
                    self._trigger_toolbar_hotkey_action(action_id, kind)
                return True

        if self.crop_mode:
            return False

        if in_spinbox and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            fw.clearFocus()
            if hasattr(self, 'inline_editor') and self.inline_editor.isVisible():
                if self.inline_editor.editing_text:
                    self.inline_editor.text_edit.setFocus()
                else:
                    self.inline_editor.setFocus()
            else:
                self.setFocus()
            return True

        if (not self.selection_rect.isNull()
                and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier))
                and not (hasattr(self, 'inline_editor') and self.inline_editor.isVisible())):
            step = 10 if (event.modifiers() & Qt.ShiftModifier) else 1
            dx, dy = {
                Qt.Key_Left: (-step, 0),
                Qt.Key_Right: (step, 0),
                Qt.Key_Up: (0, -step),
                Qt.Key_Down: (0, step),
            }.get(event.key(), (0, 0))
            if dx or dy:
                r = QRect(self.selection_rect)
                nx = max(0, min(max(0, self.width() - r.width()), r.x() + dx))
                ny = max(0, min(max(0, self.height() - r.height()), r.y() + dy))
                if nx != r.x() or ny != r.y():
                    moved_by = QPoint(nx - r.x(), ny - r.y())
                    r.moveTo(nx, ny)
                    self.selection_rect = r
                    self._reset_text_selection_state()
                    if self._rotation_locked_edges():
                        translate_edits(self.edits, moved_by)
                    else:
                        self._invalidate_text_doc()
                        self._schedule_auto_text_ocr()
                    self.update_toolbar_positions()
                    self.update()
                return True
        return False

    def _handle_intent_keypress(self, event):
        for flag_attr, runner in (
            ('ocr_intent', lambda rect: self.start_quick_ocr_session(
                rect, self.original_screen.copy(rect) if (hasattr(self, 'original_screen') and self.original_screen) else None
            )),
            ('recording_intent', lambda rect: getattr(self, "root_tool", self).start_recording_session(rect)),
        ):
            if getattr(self, flag_attr, False):
                if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                    setattr(self, flag_attr, False)
                    rect = self.selection_rect if (not self.selection_rect.isNull() and self.selection_rect.width() > 10) else QApplication.primaryScreen().geometry()
                    self.hide_app(keep_history=False)
                    runner(rect)
                    return True
                if event.key() == Qt.Key_Escape:
                    setattr(self, flag_attr, False)
                    self.hide_app(keep_history=False)
                    return True
        return False

    def keyPressEvent(self, event):
        # 取字 / 译文层打开时，键盘事件统一交给该层处理（Esc 只退出该层，不关闭整个截图）
        active_layer = self._text_select_layer or self._translation_overlay
        if active_layer is not None:
            active_layer.keyPressEvent(event)
            return

        fw = self.focusWidget() or QApplication.focusWidget()
        is_inline_editing = (
            hasattr(self, 'inline_editor')
            and self.inline_editor.isVisible()
            and (
                fw is self.inline_editor
                or (fw is not None and self.inline_editor.isAncestorOf(fw))
                or self.inline_editor.text_edit.hasFocus()
                or fw is self.inline_editor.text_edit
                or (fw is not None and fw.parent() is self.inline_editor.text_edit)
            )
        )
        in_spinbox = isinstance(fw, QAbstractSpinBox) or (fw is not None and isinstance(fw.parent(), QAbstractSpinBox))

        if self.state == "EDITING" and self._handle_editing_keypress(event, fw, is_inline_editing, in_spinbox):
            return
        if self._handle_intent_keypress(event):
            return

        if event.key() == Qt.Key_F2 and normalize_hotkey_str(load_settings().get("hotkey_ocr", "F2")) == "F2":
            self.activate_ocr()
            return
        if event.key() == Qt.Key_Space and (self.scroll_thread or getattr(self, 'scroll_waiting', False)):
            self.toggle_auto_scroll_capture()
            return
        if self.crop_mode:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.apply_crop()
                return
            if event.key() == Qt.Key_Escape:
                self.cancel_crop()
                return
        if self.state == "SELECTING":
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if getattr(self, 'hovered_window_rect', None) and not self.hovered_window_rect.isNull():
                    self.selection_rect = QRect(self.hovered_window_rect)
                    self.hovered_window_rect = None
                    self.state = "EDITING"
                    self.color_picker_visible = False
                    self.drag_mode = None
                    self.setFocus()
                    self.show_toolbar()
                    self.update()
                    self._schedule_auto_text_ocr()
                    return
            if self.color_picker_visible:
                if event.key() == Qt.Key_C:
                    self.copy_color_value()
                    return
                if event.key() == Qt.Key_Shift:
                    self.toggle_color_format()
                    return
        if event.modifiers() & Qt.ControlModifier:
            if event.key() == Qt.Key_C and self.state == "EDITING":
                self.copy_image()
            elif event.key() == Qt.Key_S and self.state == "EDITING" and not self.crop_mode:
                self.save_image()
            elif event.key() == Qt.Key_Z and self.state == "EDITING":
                self.undo()
            elif event.key() == Qt.Key_Y and self.state == "EDITING":
                self.redo()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & (Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)):
            if self.state == "EDITING" and not self.crop_mode and not is_inline_editing and not self._text_selected and not in_spinbox:
                self.copy_image()
                return
        elif event.key() == Qt.Key_Escape:
            if self.has_pending_rotation_preview():
                self.cancel_rotation_preview()
                return
            if self.current_tool is not None:
                self.set_tool(None)
                self.show_toolbar()
                return
            if self.crop_mode:
                self.cancel_crop()
                self.show_toolbar()
                return
            self.hide_app(keep_history=False)

    def _get_mode_banner_geometry(self, banner_w=480, banner_h=32):
        screen_w = self.width()
        screen_h = self.height()
        bx = (screen_w - banner_w) // 2

        m_pos = getattr(self, 'current_mouse_pos', None)
        if m_pos is None:
            m_pos = self.mapFromGlobal(QCursor.pos())

        top_y = 16
        bottom_y = screen_h - banner_h - 18

        # 智能上下避让：如果鼠标位于屏幕上半部分 (y < 240) 或悬停吸附的窗口在屏幕顶部，立刻避让到底部！
        dodge_to_bottom = False
        if m_pos.y() < 240:
            dodge_to_bottom = True
        elif hasattr(self, 'hovered_window_rect') and self.hovered_window_rect:
            if self.hovered_window_rect.top() < 120 and self.hovered_window_rect.intersects(QRect(bx, top_y, banner_w, banner_h + 30)):
                dodge_to_bottom = True

        # 如果鼠标直接接近顶部 banner 50px 范围内，强制避让到底部
        if not dodge_to_bottom and (abs(m_pos.x() - screen_w // 2) < banner_w // 2 + 40) and m_pos.y() < top_y + banner_h + 40:
            dodge_to_bottom = True
        # 如果避让到底部后鼠标又移动到底部 banner 50px 范围内，则翻转回顶部
        elif dodge_to_bottom and (abs(m_pos.x() - screen_w // 2) < banner_w // 2 + 40) and m_pos.y() > bottom_y - 40:
            dodge_to_bottom = False

        by = bottom_y if dodge_to_bottom else top_y
        return QRect(bx, by, banner_w, banner_h)

    def _draw_mode_banner(self, painter, is_ocr, is_rec):
        if not load_settings().get("show_mode_hints", True):
            self._mode_banner_rect = None
            self._mode_banner_close_rect = None
            return

        if getattr(self, 'mode_banner_dismissed', False):
            self._mode_banner_rect = None
            self._mode_banner_close_rect = None
            return

        # 只要用户开始拖选或已存在选区，彻底隐藏指引条，100% 杜绝遮挡任何屏幕画面
        if not self.selection_rect.isNull() or getattr(self, 'drag_mode', None):
            self._mode_banner_rect = None
            self._mode_banner_close_rect = None
            return

        banner_w = 480
        banner_h = 32
        banner_rect = self._get_mode_banner_geometry(banner_w, banner_h)
        self._mode_banner_rect = banner_rect
        close_btn_rect = QRect(banner_rect.right() - 25, banner_rect.top() + (banner_h - 16) // 2, 16, 16)
        self._mode_banner_close_rect = close_btn_rect

        border_color = QColor(168, 118, 40) if is_ocr else QColor(239, 68, 68)

        # 柔和轻量阴影
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 25))
        painter.drawRoundedRect(banner_rect.adjusted(0, 2, 0, 2), 8, 8)

        # 胶囊卡片主体（纯白背景、微圆角）
        painter.setPen(QPen(border_color, 1.2))
        painter.setBrush(QBrush(QColor(255, 255, 255, 248)))
        painter.drawRoundedRect(banner_rect, 7, 7)

        # 提示文字
        painter.setPen(QPen(QColor(36, 41, 47)))
        painter.setFont(make_ui_font(9, QFont.DemiBold))
        text = "🔤 识图翻译: 拖选识别区域 | 双击/回车全屏识别 | ESC 取消" if is_ocr else "🎥 屏幕录制: 拖选录制区域 | 双击/回车全屏录制 | ESC 取消"
        text_rect = QRect(banner_rect.left() + 10, banner_rect.top(), banner_rect.width() - 36, banner_rect.height())
        painter.drawText(text_rect, Qt.AlignCenter, text)

        # 右侧关闭小按钮 ×
        m_pos = getattr(self, 'current_mouse_pos', None)
        is_close_hover = (m_pos is not None and close_btn_rect.contains(m_pos))
        if is_close_hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(240, 243, 246))
            painter.drawRoundedRect(close_btn_rect, 3, 3)

        painter.setPen(QPen(QColor(220, 38, 38) if is_close_hover else QColor(140, 149, 159), 1.5))
        cx = close_btn_rect.center().x()
        cy = close_btn_rect.center().y()
        d = 4
        painter.drawLine(cx - d, cy - d, cx + d, cy + d)
        painter.drawLine(cx + d, cy - d, cx - d, cy + d)

        painter.setBrush(Qt.NoBrush)

    def paintEvent(self, event):
        if not hasattr(self, 'original_screen') or self.original_screen is None: return 
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            # 1. 绘制底层屏幕原始画面
            painter.drawPixmap(0, 0, self.original_screen)

            is_ocr = getattr(self, 'ocr_intent', False)
            is_rec = getattr(self, 'recording_intent', False)

            # 2. 经典深色遮罩系统（未选区域覆盖深色遮罩，选区内部 100% 原始通透清晰）
            mask_color = QColor(0, 0, 0, 120)
            if self.selection_rect.isNull():
                # 未开始拖选时，检查是否有窗口/控件吸附高亮
                if getattr(self, 'hovered_window_rect', None) and not self.hovered_window_rect.isNull():
                    outer_region = QRegion(self.rect()).subtracted(QRegion(self.hovered_window_rect))
                    for r in outer_region.rects():
                        painter.fillRect(r, mask_color)

                    border_color = QColor(204, 153, 68)
                    painter.setPen(QPen(border_color, 2))
                    painter.setBrush(Qt.NoBrush)
                    draw_r = self.hovered_window_rect
                    if draw_r.width() >= self.width() - 2 and draw_r.height() >= self.height() - 2:
                        draw_r = draw_r.adjusted(1, 1, -2, -2)
                    painter.drawRect(draw_r)
                    draw_viewfinder_corners(painter, draw_r, border_color, length=14, width=3)
                    badge_text = f"{self.hovered_window_rect.width()} × {self.hovered_window_rect.height()}"
                    if getattr(self, 'hovered_window_title', None):
                        title_preview = self.hovered_window_title[:24] + "..." if len(self.hovered_window_title) > 24 else self.hovered_window_title
                        badge_text = f"{title_preview} | {badge_text}"
                    draw_selection_badge(painter, draw_r, badge_text, border_color=border_color)
                else:
                    painter.fillRect(self.rect(), mask_color)
            else:
                # 拖选后，仅在选区外部绘制深色遮罩；选区内部完全通透，原屏文字和图像一览无余
                outer_region = QRegion(self.rect()).subtracted(QRegion(self.selection_rect))
                for r in outer_region.rects():
                    painter.fillRect(r, mask_color)

                # 旋转/翻转后的选区内容：以旋转后的底图覆盖原屏画面（透明角落以遮罩色填充）
                if self.state == "EDITING":
                    rot_disp = self._get_rotation_preview_display_pixmap()
                    if rot_disp is None:
                        rot_disp = self._get_rotated_display_pixmap()
                    if rot_disp is not None:
                        painter.fillRect(self.selection_rect, mask_color)
                        painter.drawPixmap(self.selection_rect.topLeft(), rot_disp)

            # 3. 顶部/底部智能避让指引胶囊条（拖选时彻底隐藏，靠近时自动上下对调，点击可关闭）
            if (is_ocr or is_rec) and self.state == "SELECTING":
                self._draw_mode_banner(painter, is_ocr, is_rec)

            # 4. 选区渲染：选区内部 100% 原屏通透（零遮罩、零底色），配高精边框与取景器角标
            if not self.selection_rect.isNull():
                if is_ocr:
                    border_color = QColor(168, 118, 40)
                    badge_text = f"🔤 识别区域: {self.selection_rect.width()} × {self.selection_rect.height()}"
                elif is_rec:
                    border_color = QColor(239, 68, 68)
                    fps_label = "60FPS" if "60" in getattr(self, 'recording_quality_key', '') else "30FPS"
                    badge_text = f"🎥 录屏选区: {self.selection_rect.width()} × {self.selection_rect.height()} ({fps_label})"
                else:
                    border_color = QColor(204, 153, 68)
                    badge_text = f"{self.selection_rect.width()} × {self.selection_rect.height()}"

                # 边框线（选区内部绝对不加遮罩，完全透明高亮）
                painter.setPen(QPen(border_color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self.selection_rect)

                # 四角相机取景器角标
                draw_viewfinder_corners(painter, self.selection_rect, border_color, length=14, width=3)

                # 悬浮尺寸徽标（框选期或编辑期拖拽移动/拉伸选区时实时显示）
                is_adjusting_sel = (
                    self.state == "EDITING"
                    and getattr(self, 'drag_mode', None) in (
                        'move', 'left', 'right', 'top', 'bottom',
                        'topleft', 'topright', 'bottomleft', 'bottomright'
                    )
                )
                if self.state != "EDITING" or is_adjusting_sel:
                    draw_selection_badge(painter, self.selection_rect, badge_text, border_color=border_color)

                # 编辑状态下的标注图元绘制
                if self.state == "EDITING":
                    painter.setBrush(Qt.NoBrush)
                    painter.setClipRect(self.selection_rect)
                    self._draw_edits_on_canvas(painter)

                    if self.current_tool == 'TEXT' and getattr(self, 'drag_mode', None) == 'text_draw':
                        self._draw_text_draw_preview(painter, getattr(self, 'text_rect', None))

                    painter.setClipping(False)
                    if not self.crop_mode:
                        self._draw_text_layer(painter)

                    if self.crop_mode and getattr(self, 'crop_rect', None):
                        crop_outer = QRegion(self.selection_rect).subtracted(QRegion(self.crop_rect))
                        for r in crop_outer.rects():
                            painter.fillRect(r, QColor(0, 0, 0, 150))

                        painter.setClipRect(self.crop_rect)
                        rot_disp = self._get_rotated_display_pixmap() if not self.has_pending_rotation_preview() else None
                        if rot_disp is not None:
                            painter.drawPixmap(self.selection_rect.topLeft(), rot_disp)
                        self._draw_edits_on_canvas(painter)
                        painter.setClipping(False)
                        self._draw_crop_chrome(painter, self.crop_rect, grid_alpha=120)

                    if self.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                        painter.setPen(QPen(Qt.green, 2, Qt.DashLine))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(self.ocr_rect)

            # 颜色拾取放大镜只在框选阶段显示；截图完成后不再弹出，避免遮挡文字
            if self.state == "SELECTING":
                self.draw_color_picker(painter)
        except Exception as e:
            logging.error("ScreenshotTool paintEvent 绘制异常: %s", e, exc_info=True)
        finally:
            if painter.isActive():
                painter.end()

    def mouseDoubleClickEvent(self, event):
        if self._text_select_layer is not None or self._translation_overlay is not None:
            return
        if event.button() != Qt.LeftButton: return
        pos = event.pos()

        # 双击已添加的 TEXT 标注图元：自动切入文字工具并就地编辑
        if self.state == "EDITING" and not self.crop_mode:
            idx = self._find_editable_text_edit_index(pos)
            if idx is not None:
                self.commit_text_edit()
                if self.current_tool != 'TEXT':
                    self.set_tool('TEXT')
                self._open_existing_text_edit(idx, start_in_text_mode=True, click_global_pos=event.globalPos())
                return

        # 双击文字：复制整行（单击只清选区，不复制）
        if (self.state == "EDITING" and not self.crop_mode
                and self.current_tool is None and self._text_doc_ready()):
            hit = self._text_char_at(pos)
            if hit is not None:
                ri = hit[0]
                self._text_selected = {(ri, ci) for ci in range(len(self._ocr_doc.rows[ri].text))}
                self._text_sel_rect = self._text_widget_rect(self._ocr_doc.rows[ri].rect)
                self._copy_selected_text()
                return

        if getattr(self, 'ocr_intent', False):
            self.ocr_intent = False
            screen_geo = QApplication.primaryScreen().geometry()
            sub_pixmap = self.original_screen.copy(screen_geo) if (hasattr(self, 'original_screen') and self.original_screen) else None
            self.hide_app(keep_history=False)
            self.start_quick_ocr_session(screen_geo, sub_pixmap)
            return

        if getattr(self, 'recording_intent', False):
            self.recording_intent = False
            screen_geo = QApplication.primaryScreen().geometry()
            self.hide_app(keep_history=False)
            self.start_recording_session(screen_geo)
            return

        if self.state == "SELECTING":
            if getattr(self, 'hovered_window_rect', None) and not self.hovered_window_rect.isNull():
                self.selection_rect = QRect(self.hovered_window_rect)
                self.hovered_window_rect = None
                self.state = "EDITING"
                self.color_picker_visible = False
                self.drag_mode = None
                self.setFocus()
                self.show_toolbar()
                self.update()
                self._schedule_auto_text_ocr()
                return

        # 裁剪模式下双击裁剪框内应用裁剪
        if self.crop_mode and self.crop_rect and self.crop_rect.contains(pos):
            self.apply_crop()
            return

    def mousePressEvent(self, event):
        # 取字 / 译文层打开时，点击选区外先退出该层，避免误触发新的框选
        if self._text_select_layer is not None:
            self._on_text_layer_closed()
            return
        if self._translation_overlay is not None:
            self._close_translation_overlay()
            return
        if event.button() == Qt.RightButton:
            if self.current_tool is not None:
                self.set_tool(None)
                self.show_toolbar()
                return
            elif self.crop_mode:
                self.cancel_crop()
                self.show_toolbar()
                return
            elif getattr(self, 'ocr_intent', False) or getattr(self, 'recording_intent', False):
                self.hide_app(keep_history=False)
                return
            elif self.state == "EDITING" and not self.crop_mode:
                pos = event.pos()
                if self._text_selected:
                    self._show_text_context_menu(event.globalPos())
                    return
                if (self._text_doc_ready() and self.selection_rect.contains(pos)):
                    hit = self._text_char_at(pos)
                    if hit is not None:
                        ri = hit[0]
                        self._text_selected = {(ri, ci) for ci in range(len(self._ocr_doc.rows[ri].text))}
                        self._text_sel_rect = self._text_widget_rect(self._ocr_doc.rows[ri].rect)
                        self._text_flash = ""
                        self._text_flash_timer.stop()
                        self.update()
                        self._show_text_context_menu(event.globalPos())
                        return
                self.hide_app(keep_history=False)
                return
            elif self.state in ["EDITING", "SELECTING"]:
                self.hide_app(keep_history=False)
                return
        if event.button() != Qt.LeftButton: return
        pos = event.pos()

        # 识图/录屏模式下，点击指引条自身或右侧关闭按钮时即刻关闭该提示，不影响后续选区
        if (getattr(self, 'ocr_intent', False) or getattr(self, 'recording_intent', False)) and self.state == "SELECTING":
            if hasattr(self, '_mode_banner_rect') and self._mode_banner_rect and self._mode_banner_rect.contains(pos):
                self.mode_banner_dismissed = True
                self.update()
                return

        if self.state == "SELECTING":
            self.color_picker_visible = False
            self.color_timer.stop()
            self.begin_point = pos
            self.press_pos = pos
            self.selection_rect = QRect()
            self.toolbar.hide()
            
        elif self.state == "EDITING":
            self.color_picker_visible = False
            was_editing = bool(hasattr(self, 'inline_editor') and self.inline_editor.isVisible())
            had_text = bool(
                was_editing and (
                    self.inline_editor.text_edit.toPlainText().strip()
                    or self.inline_editor.active_edit_ref is not None
                )
            )
            self.commit_text_edit()

            if self.crop_mode:
                handle = self._get_crop_handle_at(pos)
                if handle:
                    self.crop_drag_handle = handle
                    self.crop_drag_start_pos = pos
                    self.crop_drag_start_rect = QRect(self.crop_rect)
                return

            if self.current_tool:
                if not self.selection_rect.contains(pos):
                    return

            if self.current_tool in ['ERASER', 'ERASE']:
                self.eraser_state_saved = False
                if self.erase_annotation_under_mouse(pos):
                    self.update()
            elif self.current_tool == 'CROP':
                handle = self._get_crop_handle_at(pos)
                if handle:
                    self.crop_drag_handle = handle
                    self.crop_drag_start_pos = pos
                    self.crop_drag_start_rect = QRect(self.crop_rect)
            elif self.current_tool == 'OCR':
                self.drag_mode = 'ocr'; self.ocr_start = pos; self.ocr_rect = QRect(pos, pos)
            elif self.current_tool == 'TEXT':
                self._handle_text_tool_press(pos, event.globalPos(), was_editing, had_text)
                return
            elif self.current_tool in (
                'RECT_ELLIPSE', 'ARROW', 'PEN', 'MARKER', 'MOSAIC_BLUR',
                'STEP', 'ELLIPSE', 'HIGHLIGHTER', 'BLUR', 'RECTANGLE', 'LINE'
            ):
                res = self._begin_annotation_edit(
                    self.current_tool, pos,
                    default_color=self.current_color,
                    default_thickness=getattr(self, 'current_thickness', 3),
                    options_bar=getattr(self, 'options_bar', None),
                    step_num=getattr(self, 'current_step_num', 1),
                )
                if isinstance(res, tuple) and res[0] == 'step':
                    self.current_step_num = res[1]
                    self.update()
                    self.show_toolbar()
                    return
            
            elif self.current_tool is None:
                edge = self.get_resize_edge(pos, allow_move=False)
                # 按住 Alt 时始终优先移动选区；否则当鼠标落在识别文字上时进入文字拖选
                if (edge is None
                        and not (event.modifiers() & Qt.AltModifier)
                        and self._text_doc_ready()
                        and self.selection_rect.contains(pos)
                        and self._text_char_at(pos) is not None):
                    self._start_text_selection(pos)
                    return

                # 边框/角点支持拉伸；选区内部（非文字区域或按住 Alt）支持整体拖动调整位置
                move_edge = self.get_resize_edge(pos, allow_move=True)
                self.drag_mode = move_edge
                if move_edge is not None:
                    self._selection_before_drag = QRect(self.selection_rect)
                    if move_edge == 'move':
                        self.drag_offset = pos - self.selection_rect.topLeft()
                    self._reset_text_selection_state()
                    self.update_cursor_for_tool(pos)
                    return
                elif self.geometry().width() == QApplication.primaryScreen().geometry().width():
                    self.state = "SELECTING"
                    self.begin_point = pos
                    self.selection_rect = QRect()
                    self.current_step_num = 1
                    self.edits.clear()
                    self.undo_stack.clear()
                    self.redo_stack.clear()
                    self._reset_rotation_state()
                    self._invalidate_text_doc()
                    self.update_undo_redo_states()
                    self.toolbar.hide()
                    if hasattr(self, 'options_bar') and self.options_bar:
                        self.options_bar.hide()
                    self.update()

    def leaveEvent(self, event):
        if self._text_hover is not None:
            self._text_hover = None
            self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        self.current_mouse_pos = pos
        if self.state == "SELECTING":
            if event.buttons() == Qt.LeftButton:
                self.selection_rect = QRect(self.begin_point, pos).normalized()
                self.hovered_window_rect = None
                self.update() 
            else:
                self.color_pick_pos = pos
                self.color_picker_visible = True
                try:
                    global_pos = event.globalPos()
                    win_rect, win_title = self.window_snapper.detect_at(global_pos.x(), global_pos.y())
                    if win_rect and win_rect.isValid() and win_rect.width() > 10 and win_rect.height() > 10:
                        offset = self.mapToGlobal(QPoint(0, 0))
                        local_rect = win_rect.translated(-offset.x(), -offset.y())
                        bounded = local_rect.intersected(self.rect())
                        if bounded.isValid() and bounded.width() > 10 and bounded.height() > 10:
                            self.hovered_window_rect = bounded
                            self.hovered_window_title = win_title
                        else:
                            self.hovered_window_rect = None
                            self.hovered_window_title = ""
                    else:
                        self.hovered_window_rect = None
                        self.hovered_window_title = ""
                except Exception:
                    self.hovered_window_rect = None
                    self.hovered_window_title = ""
                self.update()
        elif self.state == "EDITING":
            # 裁剪模式优先处理
            if self.crop_mode:
                if self.crop_drag_handle:
                    self._update_crop_rect(pos)
                    self._update_crop_toolbar_pos()
                    self.update()
                else:
                    self.update_cursor_for_tool(pos)
                return

            if event.buttons() == Qt.NoButton:
                hover = None
                if (self.current_tool is None and not self.crop_mode
                        and not (event.modifiers() & Qt.AltModifier)):
                    if (self.selection_rect.contains(pos)
                            and self.get_resize_edge(pos, allow_move=False) is None):
                        hover = self._text_char_at(pos)
                if hover is not None:
                    self._text_hover = hover
                    self.color_picker_visible = False
                    self.setCursor(Qt.IBeamCursor)
                    self.update()
                    return
                self._text_hover = None
                self.color_picker_visible = False
                self.update_cursor_for_tool(pos)
                return

            if not event.buttons() & Qt.LeftButton: return

            if self._text_dragging:
                self._update_text_selection(pos)
                return

            if self.current_tool in ['ERASER', 'ERASE']:
                if self.erase_annotation_under_mouse(pos):
                    self.update()
                return

            if self.current_tool == 'TEXT' and self.drag_mode == 'text_move':
                delta = event.globalPos() - self._text_move_start_global
                if delta.manhattanLength() >= 3:
                    self._text_move_dragged = True
                self.inline_editor.move_frame_to(self._text_move_start_frame.topLeft() + delta)
                
            elif self.current_tool == 'OCR' and self.drag_mode == 'ocr':
                rect = QRect(self.ocr_start, pos).normalized()
                self.ocr_rect = rect.intersected(self.selection_rect)
                self.update()

            elif self.current_tool == 'TEXT' and self.drag_mode == 'text_draw':
                self.text_rect = QRect(self.text_start, pos).normalized().intersected(self.selection_rect)
                self.update()

            elif (self.current_tool in (
                    'RECT_ELLIPSE', 'ARROW', 'PEN', 'MARKER', 'MOSAIC_BLUR',
                    'BLUR', 'HIGHLIGHTER', 'ELLIPSE', 'RECTANGLE', 'LINE'
                ) and self._update_active_temp_edit(pos, bool((event.modifiers() | QApplication.keyboardModifiers()) & Qt.ShiftModifier))):
                self.update()

            elif self.drag_mode and self.drag_mode not in ['ocr', 'text_draw', 'text_move']:
                r = QRect(self.selection_rect)
                if self.drag_mode == 'move':
                    new_tl = pos - self.drag_offset
                    max_x = max(0, self.width() - r.width())
                    max_y = max(0, self.height() - r.height())
                    r.moveTo(max(0, min(max_x, new_tl.x())), max(0, min(max_y, new_tl.y())))
                    if self._rotation_locked_edges() and r.topLeft() != self.selection_rect.topLeft():
                        translate_edits(self.edits, r.topLeft() - self.selection_rect.topLeft())
                    self.selection_rect = r
                else:
                    px = max(0, min(self.width() - 1, pos.x()))
                    py = max(0, min(self.height() - 1, pos.y()))
                    if 'left' in self.drag_mode: r.setLeft(px)
                    if 'right' in self.drag_mode: r.setRight(px)
                    if 'top' in self.drag_mode: r.setTop(py)
                    if 'bottom' in self.drag_mode: r.setBottom(py)
                    r = r.normalized()
                    if r.width() < 4:
                        if 'left' in self.drag_mode and 'right' not in self.drag_mode:
                            r.setLeft(max(0, r.right() - 3))
                        else:
                            r.setRight(min(self.width() - 1, r.left() + 3))
                    if r.height() < 4:
                        if 'top' in self.drag_mode and 'bottom' not in self.drag_mode:
                            r.setTop(max(0, r.bottom() - 3))
                        else:
                            r.setBottom(min(self.height() - 1, r.top() + 3))
                    self.selection_rect = r
                self.toolbar.hide()
                if hasattr(self, 'options_bar') and self.options_bar:
                    self.options_bar.hide()
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._text_dragging:
            self._finish_text_selection(event.pos())
            return
        if event.button() == Qt.LeftButton:
            if self.state == "SELECTING":
                click_dist = (event.pos() - getattr(self, 'press_pos', event.pos())).manhattanLength()
                if (self.selection_rect.isNull() or self.selection_rect.width() < 6 or self.selection_rect.height() < 6) and click_dist < 6:
                    if getattr(self, 'hovered_window_rect', None) and not self.hovered_window_rect.isNull():
                        self.selection_rect = QRect(self.hovered_window_rect)
                        self.hovered_window_rect = None
                    else:
                        self.selection_rect = QRect()
                        self.drag_mode = None
                        self.update()
                        return
                elif self.selection_rect.width() < 3 or self.selection_rect.height() < 3:
                    self.selection_rect = QRect()
                    self.drag_mode = None
                    self.update()
                    return

                if getattr(self, 'ocr_intent', False):
                    self.ocr_intent = False
                    rect = QRect(self.selection_rect)
                    sub_pixmap = self.original_screen.copy(rect) if (hasattr(self, 'original_screen') and self.original_screen) else None
                    self.hide_app(keep_history=False)
                    self.start_quick_ocr_session(rect, sub_pixmap)
                    return
                if getattr(self, 'recording_intent', False):
                    self.recording_intent = False
                    rect = QRect(self.mapToGlobal(self.selection_rect.topLeft()), self.selection_rect.size())
                    self.hide_app(keep_history=False)
                    self.start_recording_session(rect)
                    return
                self.state = "EDITING"
                self.color_picker_visible = False
                self.drag_mode = None
                self.setFocus()
                self.show_toolbar()
                self.color_timer.start()
                self._schedule_auto_text_ocr()

            elif self.state == "EDITING":
                if self.crop_mode:
                    self.crop_drag_handle = None
                    self._update_crop_toolbar_pos()
                    return

                if self.current_tool in ['ERASER', 'ERASE']:
                    self.eraser_state_saved = False
                    if getattr(self, '_erased_content_edit', False):
                        self._erased_content_edit = False
                        self._invalidate_text_doc()
                        self._schedule_auto_text_ocr()
                    self.show_toolbar()
                    return

                if self.current_tool in ['RECT_ELLIPSE', 'ARROW', 'PEN', 'MARKER', 'MOSAIC_BLUR', 'BLUR', 'HIGHLIGHTER']:
                    item, discarded = self._finish_active_temp_edit()
                    if discarded:
                        self.update_undo_redo_states()
                    elif item is not None and item.get('type') in ('MOSAIC_BLUR', 'BLUR_STROKE'):
                        self._invalidate_text_doc()
                        self._schedule_auto_text_ocr()
                    self.show_toolbar()
                    self.update()
                    return

                elif self.drag_mode:
                    if self.current_tool == 'TEXT' and self.drag_mode == 'text_move':
                        self._handle_text_tool_release(event.globalPos())
                        self.show_toolbar()
                        return

                    if self.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                        if self.ocr_rect.width() > 10 and self.ocr_rect.height() > 10:
                            sub_pixmap = self._crop_selection_content(self.ocr_rect)
                            self.btn_ocr.setText("🔤 识别中...")
                            ocr_img = sub_pixmap.toImage()
                            self.ocr_worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))
                            self.ocr_worker.finished.connect(self.show_ocr_result)
                            self.ocr_worker.start()

                        self.ocr_rect = None
                        self.update()

                    elif self.current_tool == 'TEXT' and self.drag_mode == 'text_draw':
                        self._handle_text_tool_release(event.globalPos())

                    if self.drag_mode in ('move', 'left', 'right', 'top', 'bottom',
                                          'topleft', 'topright', 'bottomleft', 'bottomright'):
                        before = getattr(self, '_selection_before_drag', None)
                        if before is not None and self.selection_rect != before:
                            # 只有选区位置或尺寸真的变了才重新识别，避免点击触发无意义 OCR
                            if self._rotation_locked_edges():
                                # 旋转后的内容随选区整体平移，识别结果按选区坐标自动换算，无需重新识别
                                self._reset_text_selection_state()
                            else:
                                self._invalidate_text_doc()
                                self._schedule_auto_text_ocr()
                    self._selection_before_drag = None

                    self.drag_mode = None
                    if not (hasattr(self, 'inline_editor') and self.inline_editor.isVisible()):
                        self.setFocus()
                    self.show_toolbar()
                    self.update_cursor_for_tool(event.pos())
                    self.update()

                elif self.current_tool in ['RECTANGLE', 'LINE', 'ELLIPSE', 'STEP']:
                    if len(self.edits) > 0 and self.edits[-1].get('temp'):
                        self.edits[-1]['temp'] = False
                    self.show_toolbar()
                else:
                    self.show_toolbar()

    def show_ocr_result(self, text):
        self.btn_ocr.setText("🔤 识别")
        worker = getattr(self, "ocr_worker", None)
        codes = getattr(worker, "detected_codes", []) if worker is not None else []
        doc = getattr(worker, "ocr_doc", None) if worker is not None else None
        src_img = getattr(worker, "image", None) if worker is not None else None
        warn = getattr(worker, "ocr_warning", "") if worker is not None else ""
        if not text and not codes:
            QMessageBox.information(self, "OCR 结果", "未能识别到文字或二维码，请重试。")
        else:
            dlg = OcrResultDialog(
                text, self, detected_codes=codes, ocr_doc=doc,
                source_image=src_img, ocr_warning=warn
            )
            exec_dialog_nonmodal(dlg)
            
    def show_toolbar(self):
        self.update_toolbar_positions()

    def enter_crop_mode(self):
        """进入裁剪模式，在选区内显示默认裁剪框与控制栏"""
        if self.has_pending_rotation_preview():
            self.commit_rotation_preview()
        self.crop_mode = True
        margin_x = max(10, self.selection_rect.width() // 10)
        margin_y = max(10, self.selection_rect.height() // 10)
        self.crop_rect = QRect(
            self.selection_rect.left() + margin_x,
            self.selection_rect.top() + margin_y,
            max(CROP_MIN_SIZE, self.selection_rect.width() - 2 * margin_x),
            max(CROP_MIN_SIZE, self.selection_rect.height() - 2 * margin_y)
        )
        self.crop_ratio = None
        self.crop_drag_handle = None
        self.toolbar.hide()
        self._update_crop_toolbar_pos()
        self.crop_toolbar.show()
        self.update_cursor_for_tool()
        self.update()

    def exit_crop_mode(self):
        """退出裁剪模式"""
        self.crop_mode = False
        self.crop_rect = None
        self.crop_drag_handle = None
        if hasattr(self, 'crop_toolbar'):
            self.crop_toolbar.hide()
        self.update_cursor_for_tool()
        self.update()

    def apply_crop(self):
        """应用裁剪：将选区缩小到裁剪框"""
        if not self.crop_rect or self.crop_rect.width() < CROP_MIN_SIZE:
            return
        self.save_state()
        if getattr(self, '_rotated_base_pixmap', None) is not None:
            # 旋转后的选区：同步裁剪旋转底图（保持真实分辨率），标注坐标仍为屏幕坐标无需变换
            new_base = self._rotated_base_pixmap.copy(self._selection_rect_to_base(self.crop_rect))
            if new_base is not None and not new_base.isNull():
                self._rotated_base_pixmap = new_base
                self._rotated_display_cache = None
        self.selection_rect = QRect(self.crop_rect)
        self.crop_mode = False
        self.crop_rect = None
        self.crop_drag_handle = None
        if hasattr(self, 'crop_toolbar'):
            self.crop_toolbar.hide()
        self.set_tool(None, self.btn_crop)
        self._invalidate_text_doc()
        self.show_toolbar()
        self.update()
        self._schedule_auto_text_ocr()

    def cancel_crop(self):
        """取消裁剪"""
        self.exit_crop_mode()
        self.set_tool(None, self.btn_crop)
        self.show_toolbar()

    def set_crop_ratio(self, ratio):
        self.crop_ratio = ratio
        if self.crop_rect and ratio:
            rw, rh = ratio
            target = rw / rh
            w = self.crop_rect.width()
            h = int(w / target)
            bounds = self.selection_rect
            if bounds.contains(QRect(self.crop_rect.left(), self.crop_rect.top(), w, h)):
                self.crop_rect.setHeight(h)
            else:
                h = self.crop_rect.height()
                w = int(h * target)
                if bounds.contains(QRect(self.crop_rect.left(), self.crop_rect.top(), w, h)):
                    self.crop_rect.setWidth(w)
            self._update_crop_toolbar_pos()
            self.update()

    def _update_crop_toolbar_pos(self):
        if not self.crop_mode or not self.crop_rect or not hasattr(self, 'crop_toolbar'):
            return
        self.crop_toolbar.update_size(self.crop_rect.width(), self.crop_rect.height())
        self.crop_toolbar.adjustSize()
        tb_w = self.crop_toolbar.width()
        tb_h = self.crop_toolbar.height()
        screen_rect = QApplication.primaryScreen().geometry()

        x = self.crop_rect.center().x() - tb_w // 2
        y = self.crop_rect.bottom() + 10
        if y + tb_h > screen_rect.bottom() - 10:
            y = self.crop_rect.top() - tb_h - 10
        x = max(10, min(x, screen_rect.width() - tb_w - 10))
        y = max(10, min(y, screen_rect.height() - tb_h - 10))
        self.crop_toolbar.move(x, y)

    def _update_crop_rect(self, pos):
        """根据拖动手柄更新裁剪框（约束在 selection_rect 内）"""
        new_rect = self._compute_updated_crop_rect(pos, self.selection_rect)
        if new_rect is not None:
            self.crop_rect = new_rect

    def get_final_static_image(self):
        preview = getattr(self, '_rotation_preview_pixmap', None)
        if preview is not None and not preview.isNull():
            return QPixmap(preview)
        rot = getattr(self, '_rotated_base_pixmap', None)
        if rot is not None and not rot.isNull():
            base_image = QPixmap(rot)
            if not self.edits:
                return base_image
            sel = self.selection_rect
            if rot.size() != sel.size():
                # 显示时被等比缩小：标注换算回真实分辨率后再合成
                edits = scale_edits(self.edits, sel.topLeft(), sel.size(), QPoint(0, 0), rot.size())
                offset = QPoint(0, 0)
                base_origin = QPoint(0, 0)
            else:
                edits = self.edits
                offset = sel.topLeft()
                base_origin = sel.topLeft()
            painter = QPainter(base_image)
            painter.setRenderHint(QPainter.Antialiasing)
            draw_all_edits(painter, edits, rot, offset, base_origin=base_origin)
            painter.end()
            return base_image
        base_image = self.original_screen.copy(self.selection_rect)
        painter = QPainter(base_image)
        draw_all_edits(painter, self.edits, self.original_screen, self.selection_rect.topLeft())
        painter.end()
        return base_image

    def add_to_capture_history(self, pixmap, pos=None, is_pinned=False, pin_window=None, zoom_factor=1.0):
        """统一管理截图与贴图历史，支持去重和顺序维护 (LIFO 栈顶在末尾)"""
        if self.parent_tool is not None:
            return self.root_tool.add_to_capture_history(
                pixmap, pos=pos, is_pinned=is_pinned, pin_window=pin_window, zoom_factor=zoom_factor
            )
        if pixmap is None or pixmap.isNull():
            return None
        if pos is None:
            pos = QCursor.pos()

        key = pixmap.cacheKey()
        for item in self.capture_history:
            if item['pixmap'].cacheKey() == key:
                item['pos'] = pos
                item['zoom_factor'] = zoom_factor
                item['is_pinned'] = is_pinned
                item['pin_window'] = pin_window
                item['timestamp'] = time.time()
                self.capture_history.remove(item)
                self.capture_history.append(item)
                return item

        item = {
            'pixmap': pixmap,
            'pos': pos,
            'zoom_factor': zoom_factor,
            'is_pinned': is_pinned,
            'pin_window': pin_window,
            'timestamp': time.time()
        }
        self.capture_history.append(item)
        if len(self.capture_history) > 30:
            self.capture_history.pop(0)
        return item

    def create_pinned_window(self, pixmap, initial_pos=None, zoom_factor=1.0, ocr_doc=None):
        """创建贴图窗口并注册到历史与活跃贴图列表"""
        if self.parent_tool is not None:
            return self.root_tool.create_pinned_window(pixmap, initial_pos=initial_pos, zoom_factor=zoom_factor, ocr_doc=ocr_doc)
        if pixmap is None or pixmap.isNull():
            return None
        try:
            new_pin = PinnedWindow(pixmap, main_app_ref=self, initial_pos=initial_pos, zoom_factor=zoom_factor, ocr_doc=ocr_doc)
            self.pinned_windows.append(new_pin)
            self.add_to_capture_history(pixmap, pos=new_pin.pos(), is_pinned=True, pin_window=new_pin, zoom_factor=new_pin.zoom_factor)
            return new_pin
        except Exception as e:
            logging.error("创建贴图窗口失败: %s", e, exc_info=True)
            return None

    def on_pin_closed(self, pin_window):
        """当贴图被关闭时触发：将贴图标记为未激活，并将其更新至历史栈顶 (LIFO)，以便下次按 F3 优先重新贴出"""
        if self.parent_tool is not None:
            return self.root_tool.on_pin_closed(pin_window)
        try:
            if pin_window in self.pinned_windows:
                self.pinned_windows.remove(pin_window)
        except ValueError:
            pass

        target_item = None
        for item in self.capture_history:
            if item.get('pin_window') is pin_window:
                target_item = item
                break
        if not target_item:
            for item in self.capture_history:
                if item['pixmap'].cacheKey() == pin_window.pixmap.cacheKey():
                    target_item = item
                    break

        current_pos = pin_window.pos()
        current_zoom = getattr(pin_window, 'zoom_factor', 1.0)

        if target_item:
            target_item['is_pinned'] = False
            target_item['pin_window'] = None
            target_item['pos'] = current_pos
            target_item['zoom_factor'] = current_zoom
            target_item['timestamp'] = time.time()
            self.capture_history.remove(target_item)
            self.capture_history.append(target_item)
        else:
            self.capture_history.append({
                'pixmap': pin_window.pixmap,
                'pos': current_pos,
                'zoom_factor': current_zoom,
                'pin_window': None,
                'is_pinned': False,
                'timestamp': time.time()
            })
            if len(self.capture_history) > 30:
                self.capture_history.pop(0)

    def pin_image(self):
        self.commit_text_edit()
        if getattr(self, '_rotation_preview_pixmap', None) is not None:
            self.commit_rotation_preview()
        cached_doc = None
        if (not self.edits and getattr(self, '_ocr_doc', None) is not None
                and not self._ocr_doc.is_empty() and not getattr(self, '_rotated_base_pixmap', None)):
            cached_doc = self._ocr_doc
        pixmap = self.get_final_static_image()
        pos = self.selection_rect.topLeft()
        self.create_pinned_window(pixmap, initial_pos=pos, zoom_factor=1.0, ocr_doc=cached_doc)
        try:
            CaptureHistoryManager.get_instance().add_capture(pixmap, "pin")
            check_and_auto_save_capture(pixmap, prefix="pinned")
        except Exception as e:
            logging.error("保存历史或自动存盘失败: %s", e)
        self.hide_app(keep_history=False)

    def hotkey_pin(self):
        """F3 快捷键处理逻辑：
        1. 若正在截图中：直接把当前选区固定贴到屏幕上；
        2. 若在桌面状态：按 LIFO 逆序恢复最近关闭或尚未贴出的历史贴图；
        3. 若历史贴图全已在屏幕上或历史为空：尝试从剪贴板贴图；
        4. 若均无可用图片：弹出友好浮动提示。
        """
        if self.parent_tool is None:
            sec = getattr(self, "_secondary_capture", None)
            if sec is not None and getattr(sec, "_capture_active", False) and sec.isVisible() and sec.state == "EDITING":
                sec.hotkey_pin()
                return
        if self.isVisible() and self.state == "EDITING":
            self.pin_image()
            return

        # 1. 清理已销毁或已隐藏的贴图引用
        alive_pinned = []
        for pw in self.pinned_windows:
            try:
                if pw.isVisible():
                    alive_pinned.append(pw)
            except RuntimeError as e:
                logging.debug("检查贴图窗口可见性时对象已释放: %s", e)
        self.pinned_windows = alive_pinned

        # 2. 从栈顶（队尾）逆序扫描，寻找最近关闭的贴图或尚未贴出的历史项
        target_item = None
        for i in range(len(self.capture_history) - 1, -1, -1):
            item = self.capture_history[i]
            pw = item.get('pin_window')
            is_still_alive = False
            if pw is not None:
                try:
                    is_still_alive = pw.isVisible()
                except RuntimeError:
                    is_still_alive = False

            if not item.get('is_pinned', False) or not is_still_alive:
                target_item = item
                break

        if target_item is not None:
            pixmap = target_item['pixmap']
            pos = target_item.get('pos')
            zoom = target_item.get('zoom_factor', 1.0)

            new_pin = PinnedWindow(pixmap, main_app_ref=self, initial_pos=pos, zoom_factor=zoom)
            self.pinned_windows.append(new_pin)

            target_item['is_pinned'] = True
            target_item['pin_window'] = new_pin
            target_item['timestamp'] = time.time()

            # 将其更新到队尾，后续连续按 F3 时自动继续向前遍历更早的历史贴图
            self.capture_history.remove(target_item)
            self.capture_history.append(target_item)
            return

        # 3. 若历史中所有图片均已在屏幕上，尝试从剪贴板贴图
        clipboard = QApplication.clipboard()
        cb_pixmap = clipboard.pixmap()
        if not cb_pixmap.isNull() and cb_pixmap.width() > 10 and cb_pixmap.height() > 10:
            cursor_pos = QCursor.pos()
            pos = QPoint(cursor_pos.x() - 10, cursor_pos.y() - 10)
            self.create_pinned_window(cb_pixmap, initial_pos=pos, zoom_factor=1.0)
            return

        # 4. 既无历史也无剪贴板图片，浮动提示
        QToolTip.showText(QCursor.pos(), "📌 暂无可恢复的历史贴图", None, QRect(), 2000)

    def copy_image(self):
        self.commit_text_edit()
        if getattr(self, '_rotation_preview_pixmap', None) is not None:
            self.commit_rotation_preview()
        pixmap = self.get_final_static_image()
        pos = self.selection_rect.topLeft()
        QApplication.clipboard().setPixmap(pixmap)
        self.add_to_capture_history(pixmap, pos=pos, is_pinned=False)
        try:
            CaptureHistoryManager.get_instance().add_capture(pixmap, "copy")
            check_and_auto_save_capture(pixmap, prefix="capture")
        except Exception as e:
            logging.error("保存历史或自动存盘失败: %s", e)
        print("Copied to clipboard!")
        self.hide_app(keep_history=False)

    def save_image(self):
        self.commit_text_edit()
        if getattr(self, '_rotation_preview_pixmap', None) is not None:
            self.commit_rotation_preview()
        pixmap = self.get_final_static_image()
        pos = self.selection_rect.topLeft()
        ok = save_image_dialog(self, pixmap, default_prefix="screenshot")
        if not ok:
            return
        self.add_to_capture_history(pixmap, pos=pos, is_pinned=False)
        self.hide_app(keep_history=False)

    def show_history_dialog(self):
        root = getattr(self, "root_tool", self)
        if getattr(root, "_history_dialog_open", False):
            active_dlg = getattr(root, "_active_history_dialog", None)
            if active_dlg is not None and not getattr(active_dlg, "_really_closed_emitted", True):
                active_dlg.show()
                _force_activate_window(active_dlg)
            return
        root._history_dialog_open = True
        was_capturing = self.isVisible()
        tb_was_visible = bool(getattr(self, "toolbar", None) and self.toolbar.isVisible())
        opt_was_visible = bool(getattr(self, "options_bar", None) and self.options_bar.isVisible())
        crop_was_visible = bool(getattr(self, "crop_toolbar", None) and self.crop_toolbar.isVisible())
        dlg = None
        try:
            if was_capturing:
                self.commit_text_edit()
                if tb_was_visible:
                    self.toolbar.hide()
                if opt_was_visible:
                    self.options_bar.hide()
                if crop_was_visible:
                    self.crop_toolbar.hide()
                self.hide()
                QApplication.processEvents()
            dlg = HistoryDialog(parent=None, main_app_ref=root)
            root._active_history_dialog = dlg
            dlg.exec_nonmodal()
        except Exception as e:
            logging.error("打开历史窗口失败: %s", e, exc_info=True)
            QMessageBox.warning(None, "截图历史", f"无法打开截图历史:\n{e}")
        finally:
            root._active_history_dialog = None
            root._history_dialog_open = False
            if was_capturing:
                if dlg is not None and getattr(dlg, "_did_commit_action", False):
                    self.hide_app(keep_history=False)
                else:
                    self.showFullScreen()
                    _force_activate_window(self)
                    if tb_was_visible and getattr(self, "toolbar", None):
                        self.toolbar.show()
                        self.toolbar.raise_()
                    if opt_was_visible and getattr(self, "options_bar", None):
                        self.options_bar.show()
                        self.options_bar.raise_()
                    if crop_was_visible and getattr(self, "crop_toolbar", None):
                        self.crop_toolbar.show()
                        self.crop_toolbar.raise_()
                    QTimer.singleShot(30, lambda: _force_activate_window(self) if self.isVisible() else None)

    def _shutdown_update_workers(self):
        thread = self._update_thread
        if thread is not None and thread.isRunning():
            try:
                thread.wait(1500)
            except Exception:
                pass
        for retired in list(getattr(self, "_retired_update_threads", [])):
            if retired.isRunning():
                try:
                    retired.wait(1000)
                except RuntimeError as e:
                    logging.debug("等待已淘汰更新线程退出异常: %s", e)
        progress = self._update_progress
        worker = getattr(progress, "worker", None)
        if worker is not None and worker.isRunning():
            try:
                worker.cancel()
                worker.wait(1500)
            except Exception:
                pass

    def check_for_updates(self, manual=False):
        if self._update_thread is not None and self._update_thread.isRunning():
            if manual:
                self._manual_check_pending = True
                QToolTip.showText(QCursor.pos(), "正在检查更新，请稍候…", None, QRect(), 1500)
            return

        settings = load_settings()
        if not manual:
            if not settings.get("auto_check_update", True):
                return
            try:
                last = float(settings.get("last_update_check", 0) or 0)
            except (TypeError, ValueError):
                last = 0
            if time.time() - last < 24 * 3600:
                return

        # 记录检查时间，网络失败也不会每次启动都重试
        save_settings({"last_update_check": time.time()})
        logging.info("检查更新开始 (manual=%s)", manual)
        self._update_thread = UpdateChecker(manual=manual)
        self._update_thread.checked.connect(
            lambda info, err, m=manual: self._on_update_checked(info, err, m)
        )
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.start()
        self._update_watchdog.start(15000)
        if manual:
            QToolTip.showText(QCursor.pos(), "正在检查更新…", None, QRect(), 3000)

    def _on_update_timeout(self):
        thread = self._update_thread
        if thread is None:
            return
        logging.warning("检查更新超时（15 秒）")
        try:
            thread.checked.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.finished.disconnect(self._on_update_thread_finished)
        except (TypeError, RuntimeError):
            pass
        self._update_thread = None
        self._retired_update_threads.append(thread)
        thread.finished.connect(lambda t=thread: self._retire_update_thread(t))
        manual = self._manual_check_pending
        self._manual_check_pending = False
        if manual:
            QMessageBox.warning(None, "检查更新超时", "连接 GitHub 超时，请检查网络后重试。")

    def _retire_update_thread(self, thread):
        if thread in self._retired_update_threads:
            self._retired_update_threads.remove(thread)
        thread.deleteLater()

    def _on_update_thread_finished(self):
        self._update_watchdog.stop()
        thread = self._update_thread
        self._update_thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_update_checked(self, info, error, manual):
        self._update_watchdog.stop()
        manual = manual or self._manual_check_pending
        self._manual_check_pending = False
        logging.info("检查更新完成: info=%s error=%s", info.version if info else None, error or "")
        if error:
            logging.warning("检查更新失败: %s", error)
            if manual:
                QMessageBox.warning(None, "检查更新失败", f"无法检查更新：\n{error}")
            return
        if info is None:
            if manual:
                QMessageBox.information(None, "检查更新", f"当前已是最新版本 v{APP_VERSION}。")
            return
        if not manual and load_settings().get("skipped_version", "") == info.version:
            return
        self._show_update_dialog(info)

    def _show_update_dialog(self, info):
        if self._update_dialog is not None:
            try:
                self._update_dialog.close()
            except RuntimeError:
                pass
        dlg = UpdateAvailableDialog(info, None)
        dlg.install_requested.connect(lambda: self._start_update_download(info))
        dlg.skip_requested.connect(lambda version: save_settings({"skipped_version": version}))
        dlg.finished.connect(lambda _result: setattr(self, "_update_dialog", None))
        self._update_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _start_update_download(self, info):
        dlg = UpdateProgressDialog(info, None)
        self._update_progress = dlg
        dlg.ready.connect(lambda path: QTimer.singleShot(120, lambda: self._install_update(path, info)))
        dlg.finished.connect(lambda _result: setattr(self, "_update_progress", None))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        dlg.start()

    def _install_update(self, path, info):
        answer = QMessageBox.question(
            None, "更新已下载",
            f"Capture v{info.version} 已下载完成。\n\n"
            "是否立即关闭应用并自动安装？\n安装完成后会自动重新启动。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if answer != QMessageBox.Yes:
            QMessageBox.information(
                None, "更新已下载",
                f"安装包已保存到：\n{path}\n\n下次可以重新检查更新。"
            )
            return
        try:
            launch_updater_process(path, info)
        except Exception as e:
            logging.error("启动更新器失败: %s", e)
            QMessageBox.critical(
                None, "无法自动更新",
                f"{e}\n\n请到发布页手动下载：\n{info.page_url}"
            )
            webbrowser.open(info.page_url)
            return
        QTimer.singleShot(250, QApplication.quit)

    def show_settings_dialog(self):
        if self.parent_tool is not None:
            return self.root_tool.show_settings_dialog()
        active_dlg = getattr(self, "_active_settings_dialog", None)
        if active_dlg is not None and not getattr(active_dlg, "_really_closed_emitted", True):
            active_dlg.show()
            _force_activate_window(active_dlg)
            return
        dlg = None
        try:
            dlg = SettingsDialog(self, hotkey_mgr=getattr(self, 'hotkey_listener', None))
            self._active_settings_dialog = dlg
            if exec_dialog_nonmodal(dlg) == QDialog.Accepted:
                if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
                    self.hotkey_listener.reload_hotkeys()
        except Exception as e:
            logging.error("打开设置窗口失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "系统设置", f"无法打开设置窗口:\n{e}")
        finally:
            if dlg is not None and getattr(self, "_active_settings_dialog", None) is dlg:
                self._active_settings_dialog = None

    def start_scroll(self):
        scroll_rect = QRect(self.selection_rect)
        if scroll_rect.width() < SCROLL_MIN_CAPTURE_SIZE or scroll_rect.height() < SCROLL_MIN_CAPTURE_SIZE:
            QMessageBox.information(self, "Scroll Capture", "Please select a larger area before starting scroll capture.")
            self.set_tool(None, self.btn_scroll)
            return

        self.hide_app(keep_history=False)
        getattr(self, "root_tool", self)._begin_scroll_session(scroll_rect)

    def _begin_scroll_session(self, scroll_rect):
        clear_scroll_temp_folder(TEMP_FOLDER)
        self.active_scroll_rect = QRect(scroll_rect)
        self.scroll_waiting = True
        self._scroll_cancelled = False
        QApplication.processEvents()
        time.sleep(0.12)

        self.border_frame = CaptureFrame(self.active_scroll_rect)
        if self.hotkey_listener is not None:
            self.hotkey_listener.register_space_hotkey()

        # 提示条设为无父级的全局 ToolTip 置顶窗口，彻底解决因父窗口 hide_app 导致跟随隐藏的严重 Bug
        if hasattr(self, '_scroll_hint') and self._scroll_hint:
            try:
                self._scroll_hint.close()
            except RuntimeError as e:
                logging.debug("关闭旧滚动提示条失败: %s", e)

        hint = QLabel("Space 开始/停止滚动   ·   ESC 取消")
        hint.setWindowFlags(Qt.ToolTip | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        hint.setAttribute(Qt.WA_ShowWithoutActivating)
        hint.setStyleSheet(
            f"background: rgba(13, 13, 13, 236); color: #FAFAFA; padding: 7px 16px; "
            f"border-radius: 8px; font-family: {CODEX_FONT_FAMILY}; font-size: 12.5px; "
            f"font-weight: 400; border: 1px solid rgba(255, 255, 255, 0.14);"
        )
        hint.adjustSize()
        hint_x = self.active_scroll_rect.left() + max(0, (self.active_scroll_rect.width() - hint.width()) // 2)
        hint_y = max(0, self.active_scroll_rect.top() - hint.height() - 8)
        hint.move(hint_x, hint_y)
        hint.show()
        self._scroll_hint = hint
        QTimer.singleShot(4000, lambda: hint.close() if hint else None)

    def start_auto_scroll_capture(self):
        if self.scroll_thread or self.active_scroll_rect is None or not self.scroll_waiting:
            return
        self.scroll_waiting = False
        self._scroll_cancelled = False
        center = self.active_scroll_rect.center()
        QCursor.setPos(center)
        QApplication.processEvents()
        time.sleep(0.12)

        self.scroll_thread = ScrollCaptureThread(self.active_scroll_rect)
        self.scroll_thread.capture_finished.connect(self.on_scroll_finished)
        self.scroll_thread.start()

    def toggle_auto_scroll_capture(self):
        if self.scroll_thread and self.scroll_thread.is_recording:
            self.scroll_thread.stop()
            # Space 停止：用户主动停止查看结果，_scroll_cancelled 保持 False，会拼接
        elif self.scroll_waiting:
            self.start_auto_scroll_capture()

    def cancel_scroll_capture(self):
        if self.hotkey_listener is not None:
            self.hotkey_listener.unregister_space_hotkey()
        if self.border_frame:
            self.border_frame.close()
            self.border_frame = None
        if hasattr(self, '_scroll_hint') and self._scroll_hint:
            self._scroll_hint.close()
            self._scroll_hint = None
        self.active_scroll_rect = None
        self.scroll_thread = None
        self.scroll_waiting = False

    def stop_trigger(self):
        if self.parent_tool is None:
            sec = getattr(self, "_secondary_capture", None)
            if sec is not None and getattr(sec, "_capture_active", False) and sec.isVisible():
                sec.hide_app(keep_history=False)
                return
        if (
            (self.recording_thread and self.recording_thread.isRunning())
            or self.recording_toolbar is not None
            or self.recording_border is not None
        ):
            self._on_cancel_record()
        elif self.scroll_thread and self.scroll_thread.is_recording:
            self.scroll_thread.stop()
            # ESC 取消：不拼接
            self._scroll_cancelled = True
        elif self.scroll_waiting:
            self.cancel_scroll_capture()
        elif self.isVisible():
            self.hide_app(keep_history=False)
        else:
            # 兜底：清理可能残留的滚动截图 UI 状态
            if self.border_frame:
                self.border_frame.close()
                self.border_frame = None
            if hasattr(self, '_scroll_hint') and self._scroll_hint:
                self._scroll_hint.close()
                self._scroll_hint = None
            if self.hotkey_listener is not None:
                self.hotkey_listener.unregister_space_hotkey()

    def on_scroll_finished(self, frames):
        if self.hotkey_listener is not None:
            self.hotkey_listener.unregister_space_hotkey()
        if self.border_frame: self.border_frame.close(); self.border_frame = None
        if hasattr(self, '_scroll_hint') and self._scroll_hint:
            self._scroll_hint.close()
            self._scroll_hint = None
        if self.scroll_thread:
            self.scroll_thread.deleteLater()
            self.scroll_thread = None
        self.active_scroll_rect = None
        self.scroll_waiting = False

        # ESC 取消时不拼接
        if getattr(self, '_scroll_cancelled', False):
            self._scroll_cancelled = False
            return

        self.stitch_frames(frames)

    def stitch_frames(self, frames):
        try:
            if not frames:
                QMessageBox.information(self, "Scroll Capture", "No scroll frames were captured.")
                return

            base_img, stats = stitch_fixed_step_scroll_images(frames)
            if base_img is None:
                QMessageBox.information(self, "Scroll Capture", "The captured frames could not be stitched.")
                return

            logging.info("滚动截图全内存拼接完成: %s", stats)
            preview = ImageEditorDialog(base_img, getattr(self, "root_tool", self))
            preview.exec_nonmodal()
        except Exception as e:
            logging.error("滚动截图拼接失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "Scroll Capture Error", f"Could not stitch the scroll capture:\n{e}")

def check_for_snipaste():
    # 彻底移除同步全进程阻塞扫描，消除冷启动卡顿
    return False

# ==========================================
# SYSTEM TRAY
# ==========================================
class AppTrayIcon(QSystemTrayIcon):
    def __init__(self, main_app, hotkey_listener=None, parent=None):
        super().__init__(parent)
        self.main_app = main_app
        self.hotkey_listener = hotkey_listener
        
        icon_path = get_resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            self.setIcon(QIcon(icon_path))
        else:
            pixmap = QPixmap(32, 32)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(28, 28, 30))
            painter.setPen(QPen(QColor(180, 174, 165, 220), 1.5))
            painter.drawRoundedRect(2, 2, 28, 28, 7, 7)
            painter.setBrush(QColor(219, 166, 74))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(12, 12, 8, 8)
            painter.end()
            self.setIcon(QIcon(pixmap))
        
        self.setToolTip(f"Capture v{APP_VERSION} (F1 截图 | F2 识图翻译 | F4 录屏 | F3 贴图)")
        
        menu = FrostedTrayMenu()
        self._tray_menu = menu
        snip_action = QAction("✂ 开始截图", menu)
        snip_action.triggered.connect(self.main_app.activate_capture)
        menu.addAction(snip_action)
        self._snip_action = snip_action
        ocr_action = QAction("🔤 识图翻译", menu)
        ocr_action.triggered.connect(self.main_app.activate_ocr)
        menu.addAction(ocr_action)
        self._ocr_action = ocr_action
        rec_action = QAction("🎥 屏幕录制", menu)
        rec_action.triggered.connect(self.main_app.activate_recording)
        menu.addAction(rec_action)
        self._rec_action = rec_action
        pin_action = QAction("📌 贴图置顶", menu)
        pin_action.triggered.connect(self.main_app.hotkey_pin)
        menu.addAction(pin_action)
        self._pin_action = pin_action
        paste_pin_action = QAction("📋 剪贴板图片贴图", menu)
        paste_pin_action.triggered.connect(self.main_app.paste_clipboard_as_pin)
        menu.addAction(paste_pin_action)
        self._paste_pin_action = paste_pin_action
        toggle_pins_action = QAction("👁️ 隐藏/恢复贴图与抽屉", menu)
        toggle_pins_action.triggered.connect(self.main_app.toggle_all_pins_visibility)
        menu.addAction(toggle_pins_action)
        self._toggle_pins_action = toggle_pins_action
        restore_ct_action = QAction("🖱️ 恢复贴图点击", menu)
        restore_ct_action.triggered.connect(self.main_app.restore_all_click_through)
        menu.addAction(restore_ct_action)
        self._restore_ct_action = restore_ct_action
        pin_border_action = QAction("🖼️ 贴图边框", menu)
        pin_border_action.triggered.connect(self.main_app.toggle_pin_border)
        menu.addAction(pin_border_action)
        self._pin_border_action = pin_border_action
        menu.aboutToShow.connect(self._refresh_menu_labels)
        drawer_action = QAction("🗄️ 贴图抽屉", menu)
        drawer_action.triggered.connect(self.main_app.toggle_drawer)
        menu.addAction(drawer_action)
        self._drawer_action = drawer_action
        menu.addSeparator()
        win_pin_action = QAction("📌 窗口置顶 (DeskPin)", menu)
        win_pin_action.triggered.connect(self.main_app.activate_window_pin)
        menu.addAction(win_pin_action)
        self._win_pin_action = win_pin_action
        scroll_action = QAction("📜 滚动截图 (Scroll)", menu)
        scroll_action.triggered.connect(self.main_app.activate_scroll_capture)
        menu.addAction(scroll_action)
        self._scroll_action = scroll_action
        menu.addSeparator()
        history_action = QAction("📜 截图历史 (History)", menu)
        history_action.triggered.connect(self.main_app.show_history_dialog)
        menu.addAction(history_action)
        self._history_action = history_action
        update_action = QAction("🔄 检查更新", menu)
        update_action.triggered.connect(lambda: self.main_app.check_for_updates(manual=True))
        menu.addAction(update_action)
        autostart_action = QAction("🚀 开机自启动", menu)
        autostart_action.triggered.connect(self.toggle_autostart)
        menu.addAction(autostart_action)
        self._autostart_action = autostart_action
        settings_action = QAction("⚙ 系统设置 (Settings)", menu)
        settings_action.triggered.connect(self.main_app.show_settings_dialog)
        menu.addAction(settings_action)
        self._settings_action = settings_action
        menu.addSeparator() 
        quit_action = QAction("❌ 退出程序", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        
        self._refresh_menu_labels()
        self.setContextMenu(menu)
        self.activated.connect(self.on_tray_activated)

    def _refresh_menu_labels(self):
        try:
            s = load_settings()
            def _hk(key, default=""):
                return normalize_hotkey_str(s.get(key, default))
            def _with_hk(base, hk):
                return f"{base} ({hk})" if hk else base

            hk_snip = _hk("hotkey_snip", "F1") or "F1"
            hk_ocr = _hk("hotkey_ocr", "F2") or "F2"
            hk_pin = _hk("hotkey_pin", "F3") or "F3"
            hk_rec = _hk("hotkey_record", "F4") or "F4"
            hk_drawer = _hk("hotkey_drawer", "Ctrl+1")
            hk_boss = _hk("hotkey_toggle_pins", "")
            hk_paste_pin = _hk("hotkey_paste_pin", "")
            hk_ct = _hk("hotkey_restore_click_through", "")
            hk_border = _hk("hotkey_pin_border", "")
            hk_win_pin = _hk("hotkey_window_pin", "")
            hk_scroll = _hk("hotkey_scroll", "")
            hk_history = _hk("hotkey_history", "")
            hk_settings = _hk("hotkey_settings", "")

            self.setToolTip(f"Capture v{APP_VERSION} ({hk_snip} 截图 | {hk_ocr} 识图翻译 | {hk_rec} 录屏 | {hk_pin} 贴图)")

            for act, base, hk in (
                (getattr(self, "_snip_action", None), "✂ 开始截图", hk_snip),
                (getattr(self, "_ocr_action", None), "🔤 识图翻译", hk_ocr),
                (getattr(self, "_rec_action", None), "🎥 屏幕录制", hk_rec),
                (getattr(self, "_pin_action", None), "📌 贴图置顶", hk_pin),
                (getattr(self, "_paste_pin_action", None), "📋 剪贴板图片贴图", hk_paste_pin),
                (getattr(self, "_drawer_action", None), "🗄️ 贴图抽屉", hk_drawer),
                (getattr(self, "_win_pin_action", None), "📌 窗口置顶 (DeskPin)", hk_win_pin),
                (getattr(self, "_scroll_action", None), "📜 滚动截图 (Scroll)", hk_scroll),
                (getattr(self, "_history_action", None), "📜 截图历史 (History)", hk_history),
                (getattr(self, "_settings_action", None), "⚙ 系统设置 (Settings)", hk_settings),
            ):
                if act is not None:
                    act.setText(_with_hk(base, hk))

            if getattr(self, "_toggle_pins_action", None) is not None:
                hidden = bool(getattr(self.main_app, "_pins_boss_hidden", False))
                base_boss = "👁️ 恢复全部贴图与抽屉" if hidden else "👁️ 隐藏全部贴图与抽屉"
                self._toggle_pins_action.setText(_with_hk(base_boss, hk_boss))
            if getattr(self, "_restore_ct_action", None) is not None:
                ct_count = sum(
                    1 for pw in getattr(self.main_app, "pinned_windows", [])
                    if pw is not None and getattr(pw, "_click_through", False)
                )
                suffix = f" · {hk_ct}" if hk_ct else ""
                if ct_count > 0:
                    self._restore_ct_action.setText(f"🖱️ 解除鼠标穿透 ({ct_count} 张穿透中{suffix})")
                else:
                    self._restore_ct_action.setText(f"🖱️ 恢复贴图点击 (无穿透{suffix})")
            if getattr(self, "_pin_border_action", None) is not None:
                border_on = bool(getattr(self.main_app, "pin_border_enabled", False))
                state_txt = "已开启" if border_on else "未开启"
                hk_suffix = f" · {hk_border}" if hk_border else ""
                self._pin_border_action.setText(f"🖼️ 贴图边框 ({state_txt}{hk_suffix})")
            if getattr(self, "_autostart_action", None) is not None:
                enabled = is_autostart_enabled()
                self._autostart_action.setText("✅ 开机自启动 (已开启)" if enabled else "🚀 开机自启动 (未开启)")
        except Exception as e:
            logging.debug("刷新托盘菜单文案失败: %s", e)

    def toggle_autostart(self):
        new_state = not is_autostart_enabled()
        ok, err = set_autostart_enabled(new_state)
        if ok:
            save_settings({"auto_start": new_state})
            try:
                msg = "🚀 已开启开机自启动" if new_state else "🛑 已关闭开机自启动"
                self._autostart_toast = PinToast(msg, pinned=new_state)
                self._autostart_toast.show()
            except Exception:
                pass
        else:
            QMessageBox.warning(None, "开机自启动设置失败", f"无法修改系统开机自启动设置：\n{err}")

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick: self.main_app.activate_capture()

    def quit_app(self):
        try:
            clean_orphan_temp_files()
        except Exception as e:
            logging.debug("清理孤儿临时文件失败: %s", e)
        if hasattr(self.main_app, 'pin_drawer') and self.main_app.pin_drawer:
            try:
                self.main_app.pin_drawer.close()
            except Exception:
                pass
        if hasattr(self.main_app, '_cleanup_recording_ui'):
            self.main_app._cleanup_recording_ui()
        if hasattr(self.main_app, 'recording_thread') and self.main_app.recording_thread:
            try:
                self.main_app.recording_thread.stop()
                self.main_app.recording_thread.wait(500)
            except Exception:
                pass
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        try:
            keyboard.unhook_all()
        except Exception as e:
            logging.debug("卸载全局键盘钩子失败: %s", e)
        QApplication.quit()


class SingleInstanceTriggerListener(QThread):
    """监听单实例唤醒命名事件，收到再次启动信号时通知主线程立即进入截图。"""
    capture_requested = pyqtSignal()

    def __init__(self, event_handle=None, parent=None):
        super().__init__(parent)
        self._event_handle = event_handle
        self._stop = False

    def stop(self):
        self._stop = True
        if self._event_handle:
            try:
                _kernel32.SetEvent(self._event_handle)
            except Exception:
                pass

    def close_handle(self):
        if self._event_handle:
            try:
                _kernel32.CloseHandle(self._event_handle)
            except Exception:
                pass
            self._event_handle = None

    def run(self):
        INFINITE = 0xFFFFFFFF
        WAIT_OBJECT_0 = 0
        while not self._stop and self._event_handle:
            res = _kernel32.WaitForSingleObject(self._event_handle, INFINITE)
            if self._stop:
                break
            if res == WAIT_OBJECT_0:
                self.capture_requested.emit()


def _signal_running_instance_to_capture():
    """向已在运行的实例发送唤醒事件以立即触发截图，无需弹出重复运行提示框。"""
    try:
        _user32.AllowSetForegroundWindow(0xFFFFFFFF)
    except Exception:
        pass
    EVENT_MODIFY_STATE = 0x0002
    for attempt in range(4):
        h_event = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SINGLE_INSTANCE_SNIP_EVENT)
        if h_event:
            try:
                ok = bool(_kernel32.SetEvent(h_event))
                logging.info("检测到已有实例在运行，已发送触发截图事件 (ok=%s)", ok)
                return ok
            finally:
                try:
                    _kernel32.CloseHandle(h_event)
                except OSError as e:
                    logging.debug("关闭单实例唤醒事件句柄失败: %s", e)
        if attempt < 3:
            time.sleep(0.03)
    logging.warning("检测到已有实例在运行，但未能打开唤醒事件 %s", SINGLE_INSTANCE_SNIP_EVENT)
    return False


if __name__ == '__main__':
    app = None
    single_instance_listener = None
    try:
        if hasattr(Qt, 'AA_EnableHighDpiScaling'): QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        if hasattr(Qt, 'AA_UseHighDpiPixmaps'): QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
            QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

        # 设置 Windows 进程 AppUserModelID，确保任务栏独立显示专属图标
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("screencapture.app.1.0")
        except Exception as e:
            logging.debug("设置 AppUserModelID 失败: %s", e)

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        # 全局超清现代系统字体注入（Microsoft YaHei UI / Segoe UI，开启抗锯齿与亚像素 ClearType 微调）
        ensure_high_dpi_and_fonts()

        icon_path = get_resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))

        shared_memory = QSharedMemory("ScreenCaptureTool_SingleInstance")
        if shared_memory.attach():
            _signal_running_instance_to_capture()
            shared_memory.detach()
            sys.exit(0)
        shared_memory.create(1)

        single_instance_event = _kernel32.CreateEventW(None, False, False, SINGLE_INSTANCE_SNIP_EVENT)

        if check_for_snipaste():
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Conflict Detected. Please close Snipaste.")
            msg.exec_()

        try:
            clean_orphan_temp_files()
        except Exception as e:
            logging.debug("清理孤儿临时文件失败: %s", e)

        sync_autostart_path_if_enabled()

        hotkey_mgr = GlobalHotkeyManager()
        hotkey_mgr.install(app)

        ex = ScreenshotTool(hotkey_listener=hotkey_mgr)
        app.aboutToQuit.connect(ex._shutdown_update_workers)
        app.aboutToQuit.connect(WindowPinManager.get_instance().clear_all)
        if single_instance_event:
            single_instance_listener = SingleInstanceTriggerListener(single_instance_event)
            single_instance_listener.capture_requested.connect(ex.activate_capture, Qt.QueuedConnection)
            app.aboutToQuit.connect(single_instance_listener.stop)
            single_instance_listener.start()

        tray = AppTrayIcon(ex, hotkey_listener=hotkey_mgr)
        ex._tray_icon = tray
        tray.show()

        hotkey_mgr.on_hotkey_pressed.connect(ex.activate_capture)
        hotkey_mgr.on_ocr_pressed.connect(ex.activate_ocr)
        hotkey_mgr.on_record_pressed.connect(ex.activate_recording)
        hotkey_mgr.on_stop_scroll.connect(ex.stop_trigger)
        hotkey_mgr.on_pin_pressed.connect(ex.hotkey_pin)
        hotkey_mgr.on_space_pressed.connect(ex.toggle_auto_scroll_capture)
        hotkey_mgr.on_win_pin_pressed.connect(ex.activate_window_pin)
        hotkey_mgr.on_scroll_pressed.connect(ex.activate_scroll_capture)
        hotkey_mgr.on_history_pressed.connect(ex.show_history_dialog)
        hotkey_mgr.on_settings_pressed.connect(ex.show_settings_dialog)
        hotkey_mgr.on_drawer_pressed.connect(ex.toggle_drawer)
        hotkey_mgr.on_toggle_pins_pressed.connect(ex.toggle_all_pins_visibility)
        hotkey_mgr.on_paste_pin_pressed.connect(ex.paste_clipboard_as_pin)
        hotkey_mgr.on_restore_click_through_pressed.connect(ex.restore_all_click_through)
        hotkey_mgr.on_pin_border_pressed.connect(ex.toggle_pin_border)

        sys.exit(app.exec_())
    except Exception as e:
        logging.exception("程序启动失败")
        if app is not None:
            QMessageBox.critical(None, "Startup Error", f"The screenshot tool failed to start:\n{e}")
        raise
    finally:
        if single_instance_listener is not None:
            single_instance_listener.stop()
            single_instance_listener.wait(1000)
            single_instance_listener.close_handle()
