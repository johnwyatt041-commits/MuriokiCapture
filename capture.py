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

# 优先加载 Windows 系统核心最新的 MSVC 运行时库，避免 PyQt5 自带旧版 MSVCP140.dll 产生符号与内存冲突
for _dll_name in ['vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll']:
    _sys_path = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', _dll_name)
    if os.path.exists(_sys_path):
        try:
            ctypes.CDLL(_sys_path)
        except Exception:
            pass
import re
import json
import urllib.request
import urllib.parse
import urllib.error
import base64
import io
import keyboard
import mss
import mss.tools
import cv2
import psutil
import numpy as np
from PIL import Image
from datetime import datetime
import pytesseract
import shutil
import subprocess
import logging # 引入日志模块
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor

# RapidOCR 离线引擎懒加载单例（高精度中文/英文识别，首次 OCR 时再按需加载，彻底杜绝冷启动卡顿）
_RAPID_OCR_ENGINE = None
_RAPID_OCR_TRIED = False

def get_rapid_ocr_engine():
    global _RAPID_OCR_ENGINE, _RAPID_OCR_TRIED
    if not _RAPID_OCR_TRIED:
        _RAPID_OCR_TRIED = True
        try:
            from rapidocr_onnxruntime import RapidOCR
            _RAPID_OCR_ENGINE = RapidOCR()
            logging.info("RapidOCR 引擎按需懒加载成功")
        except Exception as _ocr_pre_err:
            logging.warning("RapidOCR 引擎加载失败: %s", _ocr_pre_err)
    return _RAPID_OCR_ENGINE

from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QHBoxLayout, QVBoxLayout,
                             QFileDialog, QLabel, QMessageBox, QDialog, QScrollArea,
                             QColorDialog, QFontDialog, QTextEdit, QMenu, QComboBox, QSpinBox,
                             QSystemTrayIcon, QAction, QFrame, QSlider, QProgressBar, QToolTip,
                             QGraphicsDropShadowEffect, QGroupBox, QGridLayout, QLineEdit, QCheckBox)
from PyQt5.QtCore import Qt, QRect, QPoint, QSize, QThread, pyqtSignal, QSharedMemory, QTimer, QAbstractNativeEventFilter, QObject, QUrl, QMimeData
from PyQt5.QtGui import (QPainter, QColor, QPen, QImage, QPixmap, QFont, QBrush, QWheelEvent,
                         QIcon, QCursor, QPainterPath, QPainterPathStroker, QFontMetrics, QRegion)

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

def get_resource_path(relative_path):
    """获取资源文件路径（兼容 PyInstaller 打包临时目录与源码运行目录）"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        bundle_path = os.path.join(sys._MEIPASS, relative_path)
        if os.path.exists(bundle_path):
            return bundle_path
    return os.path.join(BASE_DIR, relative_path)

TEMP_FOLDER = os.path.join(BASE_DIR, "temp_scroll_frames")
REC_TEMP_FOLDER = os.path.join(BASE_DIR, "temp_screen_recording")

# ==========================================
# AI 翻译与多模态识别配置 (OpenRouter)
# ==========================================
SETTINGS_FILE = os.path.join(BASE_DIR, "murioki_settings.json")
# 默认首选模型与稳定长期支持的备用免费多模态视觉模型（按优先级自动降级重试）
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3.8-27b:free"
FALLBACK_OPENROUTER_MODELS = [
    "inclusionai/ling-3.0-flash-vl:free",
    "nex-agi/nex-n2.5-mini:free",
    "nex-agi/nex-n2.5-pro:free",
]
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

def load_settings():
    """从本地 JSON 文件加载用户设置"""
    defaults = {
        "use_ai_translation": False,
        "use_ai_ocr": False,
        "openrouter_api_key": "",
        "openrouter_model": DEFAULT_OPENROUTER_MODEL,
        "hotkey_snip": "F1",
        "hotkey_ocr": "F2",
        "hotkey_pin": "F3",
        "hotkey_record": "F4",
        "default_save_dir": os.path.join(os.path.expanduser("~"), "Pictures"),
        "auto_save_enabled": False,
        "save_format": "png",
        "save_quality": 95,
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            defaults.update(data)
    except Exception as e:
        logging.warning("加载设置文件失败: %s", e)
    return defaults

def save_settings(settings_dict):
    """将用户设置持久化到本地 JSON 文件"""
    try:
        existing = load_settings()
        existing.update(settings_dict)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("保存设置文件失败: %s", e)

def get_openrouter_api_key():
    """获取 OpenRouter API 密钥（优先从设置文件，其次环境变量）"""
    key = load_settings().get("openrouter_api_key", "")
    if key:
        return key
    return os.environ.get("OPENROUTER_API_KEY", "")

def get_candidate_models():
    """获取待尝试的模型列表（用户配置首选 + 自动备用降级池）"""
    user_model = load_settings().get("openrouter_model", "").strip() or DEFAULT_OPENROUTER_MODEL
    models = [user_model]
    for fb in FALLBACK_OPENROUTER_MODELS:
        if fb not in models:
            models.append(fb)
    return models
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

# 初始化日志配置（采用 RotatingFileHandler 限制最大 1MB，日常仅记录 INFO/ERROR，彻底避免无限膨胀）
LOG_FILE = os.path.join(BASE_DIR, "murioki_app.log")
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
        except Exception:
            pass
        return None, ""


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

def draw_selection_badge(painter, rect, text, border_color=QColor(9, 105, 218), bg_color=QColor(255, 255, 255, 240), text_color=QColor(36, 41, 47)):
    """在选区边缘绘制清晰的浅色信息胶囊气泡"""
    font = QFont("Microsoft YaHei", 9, QFont.Bold)
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
                return trans_text, source_lang
    except Exception as e2:
        logging.error("备用翻译通道请求失败: %s", e2)

    raise RuntimeError("翻译请求超时或网络异常，请检查网络设置。")

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
# AI 翻译 (OpenRouter API)
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

def ai_translate_single_chunk(text, target_lang, source_lang='auto', timeout=25, is_cancelled_fn=None):
    """使用 OpenRouter AI 模型翻译单段文本（带多模型自动回退容错与思考链清洗）"""
    if not text or not text.strip():
        return "", source_lang
    text = text.strip()

    if is_cancelled_fn and is_cancelled_fn():
        return "", source_lang

    api_key = get_openrouter_api_key()
    if not api_key:
        raise RuntimeError("未检测到 API 密钥，请在设置或 murioki_settings.json 中配置 openrouter_api_key。")

    target_name = _AI_LANG_NAMES.get(target_lang, target_lang)
    if source_lang and source_lang != 'auto':
        source_name = _AI_LANG_NAMES.get(source_lang, source_lang)
        src_hint = f" The source text is in {source_name}."
    else:
        src_hint = " Auto-detect the source language."

    system_prompt = (
        f"You are a professional translator.{src_hint} "
        f"Translate the following text into {target_name}. "
        f"Output ONLY the translated text, nothing else. Do not output any thought process or notes."
    )

    models_to_try = get_candidate_models()
    last_err = None

    for idx, model_name in enumerate(models_to_try):
        if is_cancelled_fn and is_cancelled_fn():
            return "", source_lang

        payload = json.dumps({
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.3,
        }).encode('utf-8')

        req = urllib.request.Request(
            OPENROUTER_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://murioki-capture.local",
                "X-Title": "Murioki Capture OCR Translator",
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                choice = data['choices'][0]['message']
                result = _clean_ai_output(choice)
                detected_src = source_lang if source_lang != 'auto' else 'auto'
                if idx > 0:
                    logging.info("首选模型受限，已自动切换备用模型 %s 翻译成功", model_name)
                return result, detected_src
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace') if e.fp else ''
            logging.warning("模型 %s 请求失败 (HTTP %s): %s", model_name, e.code, body[:120])
            last_err = f"HTTP {e.code}"
            if e.code in [429, 500, 502, 503, 504] and idx < len(models_to_try) - 1:
                continue
            if idx == len(models_to_try) - 1:
                raise RuntimeError(f"AI 翻译失败 (所有模型均受限，最后报错: {last_err})。")
        except Exception as e:
            logging.warning("模型 %s 调用异常: %s", model_name, e)
            last_err = str(e)
            if idx < len(models_to_try) - 1:
                continue

    raise RuntimeError(f"AI 翻译请求失败: {last_err}")

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
class OcrResultDialog(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔤 文字识别与翻译 (OCR & Translation)")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.resize(960, 580)
        self.setMinimumSize(780, 480)

        self.current_worker = None
        self.detected_lang = "auto"
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self.trigger_translation)

        # 智能判定初始目标语言：包含中文则目标为英文，其余语言目标为中文
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in text)
        self.initial_target = "en" if has_chinese else "zh-CN"

        self._setup_ui(text)
        self._apply_style()

        # 弹窗打开后立即发起初次自动翻译
        QTimer.singleShot(80, self.trigger_translation)

    def _setup_ui(self, initial_text):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 18, 20, 18)
        main_layout.setSpacing(10)

        # ── 第 1 行：标题 + 状态徽章 ──
        row1 = QHBoxLayout()
        row1.setSpacing(12)

        title_lbl = QLabel("🔤 文字识别与翻译")
        title_lbl.setObjectName("dialogTitle")

        self.lbl_src_detected = QLabel("🌐 源语言: 自动检测")
        self.lbl_src_detected.setObjectName("badgeLabel")

        self.lbl_char_count = QLabel(f"📝 {len(initial_text.strip())} 字符")
        self.lbl_char_count.setObjectName("badgeLabel")

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("statusLabel")

        row1.addWidget(title_lbl)
        row1.addWidget(self.lbl_src_detected)
        row1.addWidget(self.lbl_char_count)
        row1.addStretch()
        row1.addWidget(self.status_lbl)
        main_layout.addLayout(row1)

        # ── 第 2 行：设置栏（识别模式 | 翻译模式 | 目标语言 | 翻译按钮）──
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        # 从设置文件恢复
        _saved = load_settings()

        # 识别模式
        lbl_ocr_mode = QLabel("识别:")
        lbl_ocr_mode.setObjectName("ctrlLabel")
        self.combo_ocr_mode = QComboBox()
        self.combo_ocr_mode.addItem("📷 OCR 引擎", "ocr")
        self.combo_ocr_mode.addItem("🤖 AI 识别", "ai_ocr")
        if _saved.get("use_ai_ocr", False):
            self.combo_ocr_mode.setCurrentIndex(1)
        self.combo_ocr_mode.currentIndexChanged.connect(self._on_ocr_mode_changed)

        # 分隔线
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setObjectName("sepLine")

        # 翻译模式
        lbl_mode = QLabel("翻译:")
        lbl_mode.setObjectName("ctrlLabel")
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("🌐 普通翻译", "normal")
        self.combo_mode.addItem("🤖 AI 翻译", "ai")
        if _saved.get("use_ai_translation", False):
            self.combo_mode.setCurrentIndex(1)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)

        # 分隔线
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setObjectName("sepLine")

        # 目标语言
        lbl_target = QLabel("目标:")
        lbl_target.setObjectName("ctrlLabel")
        self.combo_target = QComboBox()
        for code, label in SUPPORTED_LANGUAGES:
            self.combo_target.addItem(label, code)
        idx = self.combo_target.findData(self.initial_target)
        if idx >= 0:
            self.combo_target.setCurrentIndex(idx)
        self.combo_target.currentIndexChanged.connect(lambda: self.trigger_translation())

        # 翻译按钮
        self.btn_translate = QPushButton("🔄 翻译")
        self.btn_translate.setObjectName("toolBtn")
        self.btn_translate.setCursor(Qt.PointingHandCursor)
        self.btn_translate.clicked.connect(self.trigger_translation)

        row2.addWidget(lbl_ocr_mode)
        row2.addWidget(self.combo_ocr_mode)
        row2.addWidget(sep1)
        row2.addWidget(lbl_mode)
        row2.addWidget(self.combo_mode)
        row2.addWidget(sep2)
        row2.addWidget(lbl_target)
        row2.addWidget(self.combo_target)
        row2.addStretch()
        row2.addWidget(self.btn_translate)
        main_layout.addLayout(row2)

        # ── 第 3 行：双栏对照卡片区 ──
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(14)

        # 左栏：原文卡片
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 12, 14, 12)
        left_layout.setSpacing(8)

        left_header = QHBoxLayout()
        left_title = QLabel("📄 原文  (可直接编辑)")
        left_title.setObjectName("cardTitle")
        self.btn_copy_source = QPushButton("📋 复制原文")
        self.btn_copy_source.setObjectName("cardActionBtn")
        self.btn_copy_source.setCursor(Qt.PointingHandCursor)
        self.btn_copy_source.clicked.connect(self.copy_source)
        left_header.addWidget(left_title)
        left_header.addStretch()
        left_header.addWidget(self.btn_copy_source)
        left_layout.addLayout(left_header)

        self.source_edit = QTextEdit()
        self.source_edit.setObjectName("editorText")
        self.source_edit.setPlainText(initial_text)
        self.source_edit.textChanged.connect(self._on_source_text_changed)
        left_layout.addWidget(self.source_edit)

        # 右栏：译文卡片
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(8)

        right_header = QHBoxLayout()
        right_title = QLabel("🌐 译文")
        right_title.setObjectName("cardTitle")
        self.btn_copy_target = QPushButton("📋 复制译文")
        self.btn_copy_target.setObjectName("cardActionBtn")
        self.btn_copy_target.setCursor(Qt.PointingHandCursor)
        self.btn_copy_target.clicked.connect(self.copy_target)
        right_header.addWidget(right_title)
        right_header.addStretch()
        right_header.addWidget(self.btn_copy_target)
        right_layout.addLayout(right_header)

        self.target_edit = QTextEdit()
        self.target_edit.setObjectName("editorText")
        self.target_edit.setPlaceholderText("等待翻译...")
        right_layout.addWidget(self.target_edit)

        cards_layout.addWidget(left_card, 1)
        cards_layout.addWidget(right_card, 1)
        main_layout.addLayout(cards_layout, 1)

        # ── 第 4 行：底部快捷操作栏 ──
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.btn_copy_both = QPushButton("📑 复制双语对照")
        self.btn_copy_both.setObjectName("secondaryBtn")
        self.btn_copy_both.setCursor(Qt.PointingHandCursor)
        self.btn_copy_both.clicked.connect(self.copy_bilingual)

        self.btn_primary_copy = QPushButton("📋 复制译文")
        self.btn_primary_copy.setObjectName("primaryBtn")
        self.btn_primary_copy.setCursor(Qt.PointingHandCursor)
        self.btn_primary_copy.clicked.connect(self.copy_target)

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("secondaryBtn")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self.accept)

        bottom_bar.addWidget(self.btn_copy_both)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.btn_primary_copy)
        bottom_bar.addWidget(self.btn_close)
        main_layout.addLayout(bottom_bar)

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #f6f8fa;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QLabel#dialogTitle {
                font-size: 16px;
                font-weight: 700;
                color: #0f172a;
                padding: 2px 0;
            }
            QLabel#ctrlLabel {
                font-size: 13px;
                font-weight: 600;
                color: #334155;
                padding: 0 2px;
            }
            QLabel#cardTitle {
                font-size: 13px;
                font-weight: 600;
                color: #1e293b;
            }
            QLabel#badgeLabel {
                background-color: #e2e8f0;
                color: #475569;
                font-size: 12px;
                font-weight: 500;
                border-radius: 8px;
                padding: 4px 10px;
            }
            QLabel#statusLabel {
                font-size: 13px;
                font-weight: 600;
                color: #0969da;
                padding: 0 6px;
            }
            QFrame#sepLine {
                color: #cbd5e1;
                max-width: 1px;
                margin: 2px 4px;
            }
            QFrame#cardFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
            }
            QTextEdit#editorText {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 10px;
                font-family: "Cascadia Code", "Consolas", "Microsoft YaHei UI", monospace;
                font-size: 13px;
                line-height: 1.6;
            }
            QTextEdit#editorText:focus {
                border: 1.5px solid #0969da;
            }
            QComboBox {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 13px;
                font-weight: 500;
                min-width: 120px;
            }
            QComboBox:hover {
                border-color: #0969da;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QComboBox QAbstractItemView {
                font-size: 13px;
                padding: 4px;
            }
            QPushButton#toolBtn {
                background-color: #0969da;
                color: #ffffff;
                border: 1px solid #085cc0;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#toolBtn:hover {
                background-color: #085cc0;
            }
            QPushButton#toolBtn:disabled {
                background-color: #94a3b8;
                border-color: #94a3b8;
            }
            QPushButton#cardActionBtn {
                background: transparent;
                color: #0969da;
                border: 1px solid transparent;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 500;
                padding: 3px 8px;
            }
            QPushButton#cardActionBtn:hover {
                background-color: #f1f5f9;
                border-color: #cbd5e1;
            }
            QPushButton#primaryBtn {
                background-color: #0969da;
                color: #ffffff;
                border: 1px solid #085cc0;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#primaryBtn:hover {
                background-color: #085cc0;
            }
            QPushButton#primaryBtn:pressed {
                background-color: #074da3;
            }
            QPushButton#secondaryBtn {
                background-color: #ffffff;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#secondaryBtn:hover {
                background-color: #f1f5f9;
                color: #0f172a;
                border-color: #94a3b8;
            }
        """)

    def _on_source_text_changed(self):
        text = self.source_edit.toPlainText().strip()
        self.lbl_char_count.setText(f"📝 {len(text)} 字符")
        self.debounce_timer.start(800)

    def _on_mode_changed(self):
        """翻译模式切换时保存设置并自动重新翻译"""
        use_ai = self.combo_mode.currentData() == "ai"
        save_settings({"use_ai_translation": use_ai})
        self.trigger_translation()

    def _on_ocr_mode_changed(self):
        """识别模式切换时保存设置"""
        use_ai_ocr = self.combo_ocr_mode.currentData() == "ai_ocr"
        save_settings({"use_ai_ocr": use_ai_ocr})

    def trigger_translation(self):
        text = self.source_edit.toPlainText().strip()
        if not text:
            self.target_edit.clear()
            self.status_lbl.setText("")
            return

        target_lang = self.combo_target.currentData()
        use_ai = self.combo_mode.currentData() == "ai"

        if use_ai:
            self.status_lbl.setText("⏳ AI 正在翻译...")
        else:
            self.status_lbl.setText("⏳ 正在翻译...")
        self.status_lbl.setStyleSheet("color: #0969da; font-weight: 500;")
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
        if use_ai:
            self.status_lbl.setText("✓ AI 翻译完成")
        else:
            self.status_lbl.setText("✓ 翻译完成")
        self.status_lbl.setStyleSheet("color: #1a7f37; font-weight: 500;")
        
        # 更新识别到的源语言标签
        lang_name = LANG_NAME_MAP.get(detected_src, detected_src)
        self.lbl_src_detected.setText(f"🌐 源语言: {lang_name}")

    def _on_translation_error(self, err):
        self.btn_translate.setEnabled(True)
        use_ai = self.combo_mode.currentData() == "ai"
        if use_ai:
            self.status_lbl.setText("⚠ AI 翻译失败")
        else:
            self.status_lbl.setText("⚠ 翻译失败")
        self.status_lbl.setStyleSheet("color: #cf222e; font-weight: 500;")
        self.target_edit.setPlaceholderText(f"翻译失败: {err}")

    def copy_source(self):
        src = self.source_edit.toPlainText().strip()
        if src:
            QApplication.clipboard().setText(src)
            self.btn_copy_source.setText("✓ 已复制！")
            QTimer.singleShot(1500, lambda: self.btn_copy_source.setText("📋 复制原文"))

    def copy_target(self):
        tgt = self.target_edit.toPlainText().strip()
        if tgt:
            QApplication.clipboard().setText(tgt)
            self.btn_copy_target.setText("✓ 已复制！")
            self.btn_primary_copy.setText("✓ 译文已复制！")
            QTimer.singleShot(1500, lambda: (
                self.btn_copy_target.setText("📋 复制译文"),
                self.btn_primary_copy.setText("📋 复制译文")
            ))

    def copy_bilingual(self):
        src = self.source_edit.toPlainText().strip()
        tgt = self.target_edit.toPlainText().strip()
        if not src and not tgt:
            return
        bilingual = f"{src}\n\n【译文】\n{tgt}"
        QApplication.clipboard().setText(bilingual)
        self.btn_copy_both.setText("✓ 双语已复制！")
        QTimer.singleShot(1500, lambda: self.btn_copy_both.setText("📑 复制双语对照"))

# ==========================================
# AI 文字识别 (OpenRouter VL 视觉模型)
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

def ai_ocr_recognize(image: QImage, timeout=30):
    """使用 AI 视觉模型识别图片中的文字（带多模型自动回退容错与思考链剥离）"""
    api_key = get_openrouter_api_key()
    if not api_key:
        raise RuntimeError("未检测到 API 密钥，请在设置或 murioki_settings.json 中配置 openrouter_api_key。")

    img_base64 = _image_to_base64(image)

    models_to_try = get_candidate_models()
    last_err = None

    for idx, model_name in enumerate(models_to_try):
        payload = json.dumps({
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an advanced OCR system. Extract ALL text from the image exactly as it appears. "
                        "Preserve the original line breaks and formatting. "
                        "Output ONLY the extracted text, nothing else. Do not output any thought process, reasoning or descriptions."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this image."
                        }
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }).encode('utf-8')

        req = urllib.request.Request(
            OPENROUTER_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://murioki-capture.local",
                "X-Title": "Murioki Capture AI OCR",
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                choice = data['choices'][0]['message']
                result = _clean_ai_output(choice)
                if idx > 0:
                    logging.info("首选模型受限，已自动切换备用模型 %s 识别成功", model_name)
                return result
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace') if e.fp else ''
            logging.warning("AI OCR 模型 %s 请求失败 (HTTP %s): %s", model_name, e.code, body[:120])
            last_err = f"HTTP {e.code}"
            # 若是 429 限流或 5xx 错误，且还有备用模型，则继续尝试下一个备选模型
            if e.code in [429, 500, 502, 503, 504] and idx < len(models_to_try) - 1:
                continue
            if idx == len(models_to_try) - 1:
                raise RuntimeError(f"AI 识别失败 (所有模型均受限，最后报错: {last_err})。")
        except Exception as e:
            logging.warning("AI OCR 模型 %s 调用异常: %s", model_name, e)
            last_err = str(e)
            if idx < len(models_to_try) - 1:
                continue

    raise RuntimeError(f"AI 识别请求失败: {last_err}")

# ==========================================
# OCR 后台工作线程 (智能双引擎融合: 懒加载 RapidOCR + 深度预处理 Tesseract / AI 视觉识别)
# ==========================================
class OcrWorker(QThread):
    finished = pyqtSignal(str)

    def __init__(self, image: QImage, use_ai=False):
        super().__init__()
        # 严格使用主线程传入的 QImage 纯数据对象，彻底杜绝 QPixmap 跨线程调用崩溃
        self.image = image
        self.use_ai = use_ai

    def run(self):
        try:
            # AI 识别模式
            if self.use_ai:
                result = ai_ocr_recognize(self.image)
                self.finished.emit(result if result else "")
                return

            # 传统 OCR 模式
            img_rgb = self.image.convertToFormat(QImage.Format_RGB888)
            width, height = img_rgb.width(), img_rgb.height()
            bpl = img_rgb.bytesPerLine()
            ptr = img_rgb.bits()
            ptr.setsize(img_rgb.byteCount())
            raw = np.frombuffer(ptr, np.uint8).reshape((height, bpl))
            if bpl > width * 3:
                arr = np.ascontiguousarray(raw[:, :width * 3].reshape((height, width, 3)))
            else:
                arr = raw.reshape((height, width, 3))

            def _preprocess_for_tess(img_arr):
                gray = cv2.cvtColor(img_arr, cv2.COLOR_RGB2GRAY)
                if np.mean(gray) < 127:
                    gray = cv2.bitwise_not(gray)

                h_img, w_img = gray.shape
                scale = 2.5 if h_img < 50 else (1.5 if h_img < 100 else 1.0)
                if scale > 1.0:
                    processed = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                else:
                    processed = gray

                processed = cv2.normalize(processed, None, 0, 255, cv2.NORM_MINMAX)
                edge_val = int(np.median(processed[:2, :]))
                padding = 16
                return cv2.copyMakeBorder(processed, padding, padding, padding, padding, cv2.BORDER_CONSTANT, value=edge_val)

            # 1. 运行懒加载 RapidOCR 提取按行分块的识别结果与几何包围盒
            rapid_lines = []
            rapid_engine = get_rapid_ocr_engine()
            if rapid_engine is not None:
                try:
                    res, _ = rapid_engine(arr)
                    if res:
                        rapid_lines = res
                except Exception as ex_r:
                    logging.warning(f"RapidOCR 执行异常: {ex_r}")

            # 2. 运行 Tesseract 多语言识别
            tess_text = ""
            try:
                lang_str = TESSERACT_LANGS
                padded = _preprocess_for_tess(arr)
                # psm 3 (全自动页面分割) 比 psm 6 在多语言稀疏混排下容错率更高
                custom_config = r'--oem 1 --psm 3'
                tess_text = pytesseract.image_to_string(padded, lang=lang_str, config=custom_config).strip()
                if tess_text:
                    tess_text = re.sub(r'(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])', '', tess_text)
            except Exception as ex_t:
                logging.warning(f"Tesseract 执行异常: {ex_t}")

            # 3. 智能多语言融合判决：
            # 判断画面是否出现东南亚/特殊语种（如泰文 U+0E00 ~ U+0E7F）
            has_special_lang = any('\u0e00' <= ch <= '\u0e7f' for ch in tess_text)

            if not rapid_lines and not tess_text:
                self.finished.emit("")
                return

            if not has_special_lang and rapid_lines:
                # 纯中文/英文环境：RapidOCR 准确率极高且无字间虚假空格，优先整合成文
                text_out = "\n".join([item[1] for item in rapid_lines if item and item[1].strip()])
                self.finished.emit(text_out)
                return

            if has_special_lang and not rapid_lines:
                # 仅 Tesseract 检出内容
                self.finished.emit(tess_text)
                return

            # 当同时存在中英文与泰语等小语种混排时：
            # 进行行级置信度修补，若某行在 RapidOCR 中置信度偏低（< 0.65）或为生僻乱码，
            # 说明命中 RapidOCR 字典盲区，此时保留 Tesseract 的完整段落或做联合输出
            rapid_full = "\n".join([item[1] for item in rapid_lines if item and item[1].strip()])
            
            # 若 Tesseract 字符数明显更多（涵盖了 Rapid 漏掉的小语种行）
            if len(tess_text) > len(rapid_full) * 1.2:
                self.finished.emit(tess_text)
            else:
                self.finished.emit(rapid_full if rapid_full else tess_text)

        except Exception as e:
            logging.error("OCR Worker Error: %s", e, exc_info=True)
            self.finished.emit(f"识别发生错误:\n{e}")
# ==========================================
# UTILITIES & DRAWING ENGINE
# ==========================================
def point_to_segment_dist(p, p1, p2):
    l2 = (p1.x() - p2.x())**2 + (p1.y() - p2.y())**2
    if l2 == 0: return math.hypot(p.x() - p1.x(), p.y() - p1.y())
    t = max(0, min(1, ((p.x() - p1.x()) * (p2.x() - p1.x()) + (p.y() - p1.y()) * (p2.y() - p1.y())) / l2))
    proj = QPoint(int(p1.x() + t * (p2.x() - p1.x())), int(p1.y() + t * (p2.y() - p1.y())))
    return math.hypot(p.x() - proj.x(), p.y() - proj.y())

def draw_all_edits(painter, edits, base_pixmap, offset=QPoint(0, 0)):
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.setBrush(Qt.NoBrush)

    for edit in edits:
        thickness = edit.get('thickness', 3)
        if edit['type'] == 'RECTANGLE':
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(edit['color'], thickness))
            painter.drawRect(edit['rect'].translated(-offset))
        elif edit['type'] == 'LINE':
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(edit['color'], thickness))
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
                painter.drawPolygon(p2, p3, p4)
                painter.setBrush(Qt.NoBrush)
        elif edit['type'] == 'TEXT':
            painter.setBrush(Qt.NoBrush)
            painter.setFont(edit['font'])
            painter.setPen(QPen(edit['color']))
            if 'rect' in edit:
                rect = edit['rect'].translated(-offset)
                # 预留 2px 内衬与编辑器完美对应，彻底消除位置漂移
                painter.drawText(rect.adjusted(2, 2, -2, -2), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, edit['text'])
            else:
                rect = QRect(edit['pos'].x() - offset.x(), edit['pos'].y() - offset.y(), 800, 800)
                painter.drawText(rect, Qt.AlignLeft | Qt.AlignTop, edit['text'])
        elif edit['type'] == 'BLUR':
            painter.setBrush(Qt.NoBrush)
            # 矩形固定正方形网格马赛克（彻底消除拉伸条纹变形）
            source_rect = edit['rect']
            if base_pixmap is not None:
                source_rect = source_rect.intersected(base_pixmap.rect())
            if base_pixmap is not None and not source_rect.isEmpty():
                draw_rect = source_rect.translated(-offset)
                tile_size = max(6, thickness * 3)
                sw, sh = source_rect.width(), source_rect.height()
                gw = max(1, sw // tile_size)
                gh = max(1, sh // tile_size)
                sub_img = base_pixmap.copy(source_rect).toImage()
                small = sub_img.scaled(gw, gh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
                mosaic = QPixmap.fromImage(small.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.FastTransformation))
                painter.drawPixmap(draw_rect.topLeft(), mosaic)
        elif edit['type'] == 'BLUR_STROKE':
            # 自由画笔涂抹式打码（像记号笔一样涂抹）
            points = edit.get('points', [])
            if base_pixmap is not None and points:
                radius = max(8, thickness * 4)
                min_x = min(p.x() for p in points) - radius
                max_x = max(p.x() for p in points) + radius
                min_y = min(p.y() for p in points) - radius
                max_y = max(p.y() for p in points) + radius
                bbox = QRect(QPoint(min_x, min_y), QPoint(max_x, max_y)).intersected(base_pixmap.rect())
                if not bbox.isEmpty():
                    tile_size = max(6, thickness * 3)
                    bw, bh = bbox.width(), bbox.height()
                    gw = max(1, bw // tile_size)
                    gh = max(1, bh // tile_size)
                    sub_img = base_pixmap.copy(bbox).toImage()
                    small = sub_img.scaled(gw, gh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
                    mosaic = QPixmap.fromImage(small.scaled(bw, bh, Qt.IgnoreAspectRatio, Qt.FastTransformation))

                    painter.save()
                    stroker = QPainterPathStroker()
                    stroker.setWidth(radius * 2)
                    stroker.setCapStyle(Qt.RoundCap)
                    stroker.setJoinStyle(Qt.RoundJoin)

                    line_path = QPainterPath()
                    line_path.moveTo(points[0] - offset)
                    for pt in points[1:]:
                        line_path.lineTo(pt - offset)
                    clip_path = stroker.createStroke(line_path)

                    painter.setClipPath(clip_path)
                    painter.drawPixmap(bbox.translated(-offset).topLeft(), mosaic)
                    painter.restore()
        elif edit['type'] == 'ELLIPSE':
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(edit['color'], thickness))
            painter.drawEllipse(edit['rect'].translated(-offset))
        elif edit['type'] == 'PEN':
            points = edit.get('points', [])
            if len(points) >= 2:
                pen_brush = QPen(edit['color'], thickness, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(pen_brush)
                path = QPainterPath()
                path.moveTo(points[0] - offset)
                for pt in points[1:]:
                    path.lineTo(pt - offset)
                painter.drawPath(path)
        elif edit['type'] == 'HIGHLIGHTER':
            points = edit.get('points', [])
            if len(points) >= 2:
                painter.save()
                hl_color = QColor(edit['color'])
                hl_color.setAlpha(115)  # 半透明荧光笔效果，透出底文
                hl_pen = QPen(hl_color, max(12, thickness * 4), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(hl_pen)
                path = QPainterPath()
                path.moveTo(points[0] - offset)
                for pt in points[1:]:
                    path.lineTo(pt - offset)
                painter.drawPath(path)
                painter.restore()
        elif edit['type'] == 'STEP':
            step_pos = edit.get('pos') or edit.get('center')
            if step_pos is None:
                continue
            pos = step_pos - offset
            radius = edit.get('radius') or max(11, 8 + thickness * 2)
            num_val = edit.get('num') if edit.get('num') is not None else edit.get('number', 1)
            num_str = str(num_val)
            painter.save()
            painter.setBrush(QBrush(edit['color']))
            painter.setPen(QPen(Qt.white, 1.5))
            painter.drawEllipse(pos, radius, radius)
            text_color = Qt.white if edit['color'].lightness() < 165 else Qt.black
            painter.setPen(QPen(text_color))
            f = QFont("Microsoft YaHei UI", max(8, int(radius * 0.95)), QFont.Bold)
            painter.setFont(f)
            text_rect = QRect(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2)
            painter.drawText(text_rect, Qt.AlignCenter, num_str)
            painter.restore()

    painter.setBrush(Qt.NoBrush)


def is_annotation_hit(edit, pos):
    """精确检测鼠标点击位置是否命中某一标注元素，支持所有矢量与位图标注类型"""
    try:
        t = edit.get('type')
        if t in ['RECTANGLE', 'BLUR', 'TEXT']:
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
        elif t in ['BLUR_STROKE', 'PEN', 'HIGHLIGHTER']:
            thick = edit.get('thickness', 3)
            radius = max(8, thick * (4 if t in ['BLUR_STROKE', 'HIGHLIGHTER'] else 2)) + 6
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
    except Exception:
        pass
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


TOOLBAR_STYLE = """
QWidget#mainToolbar, QWidget#editorToolbar, QWidget#cropToolbar {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 10px;
}
"""

BUTTON_STYLE = """
QPushButton {
    background: transparent;
    color: #24292f;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 9px;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 12px;
    font-weight: 500;
}
QPushButton:hover {
    background: #f3f4f6;
    border: 1px solid #d0d7de;
    color: #0969da;
}
QPushButton:pressed {
    background: #e5e7eb;
    border: 1px solid #afb8c1;
}
QPushButton:disabled {
    color: #8c959f;
    background: transparent;
    border: 1px solid transparent;
}
"""

ACTIVE_BUTTON_STYLE = """
QPushButton {
    background-color: #0969da;
    color: #ffffff;
    border: 1px solid #085cc0;
    border-radius: 6px;
    padding: 5px 9px;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #085cc0;
}
QPushButton:pressed {
    background-color: #074da3;
}
"""

DANGER_BUTTON_STYLE = """
QPushButton {
    background: transparent;
    color: #cf222e;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 9px;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 12px;
    font-weight: 500;
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
    """注入全局现代系统字体族与 ClearType 亚像素抗锯齿微调"""
    app = QApplication.instance()
    if app:
        font = QFont("Microsoft YaHei UI", 9)
        font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
        font.setHintingPreference(QFont.PreferFullHinting)
        app.setFont(font)

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
class QuickTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor_parent = parent
        self.document().setDocumentMargin(2)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, event):
        if (event.modifiers() & Qt.ControlModifier) and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if hasattr(self.editor_parent, 'parent_view') and hasattr(self.editor_parent.parent_view, 'commit_text_edit'):
                self.editor_parent.parent_view.commit_text_edit()
                return
        elif event.key() == Qt.Key_Escape:
            if hasattr(self.editor_parent, 'cancel_edit'):
                self.editor_parent.cancel_edit()
                return
        super().keyPressEvent(event)

class InlineTextEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_view = parent
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.SubWindow)

        self.current_color = QColor(255, 0, 0)
        self.active_edit_ref = None
        self.base_pos = QPoint(0, 0)

        # 浮动工具栏（浅色圆角药丸条）
        self.toolbar = QWidget(self)
        self.toolbar.setStyleSheet("""
            QWidget {
                background: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 6px;
            }
            QSpinBox {
                background: #ffffff;
                color: #24292f;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                padding: 1px 4px;
                font-size: 11px;
            }
            QComboBox {
                background: #ffffff;
                color: #24292f;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                padding: 1px 4px;
                font-size: 11px;
            }
        """)

        tb_layout = QHBoxLayout(self.toolbar)
        tb_layout.setContentsMargins(4, 3, 4, 3)
        tb_layout.setSpacing(4)

        self.btn_color = QPushButton("🎨")
        self.btn_color.setFixedSize(26, 22)
        self.btn_color.setToolTip("选择文字颜色")
        self.btn_color.setCursor(Qt.PointingHandCursor)

        # 支持 6 ~ 72pt 字号，彻底解决无法输入小字问题
        self.spin_size = QSpinBox()
        self.spin_size.setRange(6, 72)
        self.spin_size.setValue(14)
        self.spin_size.setToolTip("文字大小 (6~72pt)")
        self.spin_size.setFixedWidth(46)

        self.combo_font = QComboBox()
        self.combo_font.addItems(["Microsoft YaHei", "Arial", "SimHei", "Segoe UI", "Consolas"])
        self.combo_font.setToolTip("字体")

        self.btn_bold = QPushButton("B")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setFixedSize(22, 22)
        self.btn_bold.setStyleSheet("font-weight: bold;")
        self.btn_bold.setToolTip("加粗")

        self.btn_italic = QPushButton("I")
        self.btn_italic.setCheckable(True)
        self.btn_italic.setFixedSize(22, 22)
        self.btn_italic.setStyleSheet("font-style: italic;")
        self.btn_italic.setToolTip("斜体")

        self.btn_confirm = QPushButton("✓")
        self.btn_confirm.setFixedSize(22, 22)
        self.btn_confirm.setStyleSheet("color: #4ade80; font-weight: bold;")
        self.btn_confirm.setToolTip("完成输入 (Ctrl+Enter)")
        self.btn_confirm.setCursor(Qt.PointingHandCursor)

        self.btn_cancel = QPushButton("✕")
        self.btn_cancel.setFixedSize(22, 22)
        self.btn_cancel.setStyleSheet("color: #f87171; font-weight: bold;")
        self.btn_cancel.setToolTip("取消输入 (Esc)")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)

        tb_layout.addWidget(self.btn_color)
        tb_layout.addWidget(self.spin_size)
        tb_layout.addWidget(self.combo_font)
        tb_layout.addWidget(self.btn_bold)
        tb_layout.addWidget(self.btn_italic)
        tb_layout.addWidget(self.btn_confirm)
        tb_layout.addWidget(self.btn_cancel)

        for btn in [self.btn_color, self.btn_bold, self.btn_italic, self.btn_confirm, self.btn_cancel]:
            apply_button_style(btn)

        # 核心文字输入框（自动伸缩尺寸，透明磨砂底，支持极小字）
        self.text_edit = QuickTextEdit(self)
        self.text_edit.setPlaceholderText("输入文字...")
        self.text_edit.textChanged.connect(self.adjust_size_to_content)

        self.btn_color.clicked.connect(self.choose_color)
        self.spin_size.valueChanged.connect(self.update_font)
        self.combo_font.currentTextChanged.connect(self.update_font)
        self.btn_bold.clicked.connect(self.update_font)
        self.btn_italic.clicked.connect(self.update_font)
        self.btn_confirm.clicked.connect(self.commit_edit)
        self.btn_cancel.clicked.connect(self.cancel_edit)

        self.hide()

    def choose_color(self):
        color = QColorDialog.getColor(self.current_color, self, "选择文字颜色")
        if color.isValid():
            self.current_color = color
            self.update_font()

    def update_font(self):
        font = QFont(self.combo_font.currentText(), self.spin_size.value())
        font.setBold(self.btn_bold.isChecked())
        font.setItalic(self.btn_italic.isChecked())
        self.text_edit.setFont(font)
        self.btn_color.setStyleSheet(f"background-color: {self.current_color.name()}; border: 1px solid #ffffff; border-radius: 4px;")
        self.text_edit.setStyleSheet(
            f"background: rgba(255, 255, 255, 0.92); "
            f"color: {self.current_color.name()}; "
            f"border: 1.5px dashed #0078d4; "
            f"border-radius: 4px; "
            f"padding: 2px;"
        )
        self.adjust_size_to_content()

    def adjust_size_to_content(self):
        text = self.text_edit.toPlainText()
        fm = QFontMetrics(self.text_edit.font())
        lines = text.split('\n') if text else ['']
        max_w = max([fm.width(line) for line in lines] or [30])
        line_h = max(14, fm.lineSpacing())
        total_h = line_h * len(lines)

        # 随文本动态弹性伸展，不再有 150x80 的强制死板大框
        box_w = max(60, max_w + 16)
        box_h = max(22, total_h + 8)

        self.toolbar.adjustSize()
        tb_w = max(self.toolbar.sizeHint().width(), 220)
        tb_h = self.toolbar.sizeHint().height()

        widget_w = max(box_w, tb_w)
        widget_h = box_h + tb_h + 4

        parent_w = self.parent_view.width() if self.parent_view else 1920
        parent_h = self.parent_view.height() if self.parent_view else 1080

        # 判断工具栏是在上方还是下方（防止超出上边界）
        pos_x = max(0, min(self.base_pos.x(), parent_w - widget_w - 4))
        if self.base_pos.y() - tb_h - 6 >= 0:
            pos_y = self.base_pos.y() - tb_h - 4
            self.toolbar.move(0, 0)
            self.text_edit.move(0, tb_h + 2)
        else:
            pos_y = self.base_pos.y()
            self.text_edit.move(0, 0)
            self.toolbar.move(0, box_h + 2)

        self.toolbar.resize(tb_w, tb_h)
        self.text_edit.resize(box_w, box_h)
        self.setGeometry(pos_x, pos_y, widget_w, widget_h)

    def start_editing(self, pos_or_rect, existing_edit=None):
        if isinstance(pos_or_rect, QRect):
            self.base_pos = pos_or_rect.topLeft()
        elif isinstance(pos_or_rect, QPoint):
            self.base_pos = pos_or_rect
        else:
            self.base_pos = QPoint(50, 50)

        self.active_edit_ref = existing_edit
        if existing_edit:
            self.text_edit.setPlainText(existing_edit['text'])
            self.current_color = existing_edit['color']
            self.spin_size.setValue(existing_edit['font'].pointSize())
            self.combo_font.setCurrentText(existing_edit['font'].family())
            self.btn_bold.setChecked(existing_edit['font'].bold())
            self.btn_italic.setChecked(existing_edit['font'].italic())
        else:
            self.text_edit.clear()
            self.btn_bold.setChecked(False)
            self.btn_italic.setChecked(False)

        self.update_font()
        self.show()
        self.raise_()
        self.text_edit.setFocus()

    def get_text_rect(self):
        """返回文本在父控件中的真实绝对几何矩形"""
        text_geo = self.text_edit.geometry()
        return QRect(self.x() + text_geo.x(), self.y() + text_geo.y(), text_geo.width(), text_geo.height())

    def commit_edit(self):
        if hasattr(self.parent_view, 'commit_text_edit'):
            self.parent_view.commit_text_edit()

    def cancel_edit(self):
        self.text_edit.clear()
        self.active_edit_ref = None
        self.hide()



class PinnedWindow(QWidget):
    def __init__(self, pixmap, main_app_ref=None, initial_pos=None, zoom_factor=1.0):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.pixmap = pixmap
        self.main_app_ref = main_app_ref

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
        self.drag_position = QPoint()

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

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.close()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def closeEvent(self, event):
        if self.main_app_ref is not None:
            try:
                self.main_app_ref.on_pin_closed(self)
            except Exception as e:
                logging.error("关闭贴图并同步历史失败: %s", e, exc_info=True)
        super().closeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 直接利用 QPainter 变换绘制原图至当前控件矩形，零中间大图内存拷贝，避免频繁重绘导致内存暴涨闪退
        painter.drawPixmap(self.rect(), self.pixmap)

    def wheelEvent(self, event: QWheelEvent):
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
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton: 
            self.move(event.globalPos() - self.drag_position)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setFocus()
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        ocr_action = menu.addAction("🔤 OCR & Translate (F2)")
        edit_action = menu.addAction("🛠 Edit") 
        close_action = menu.addAction("❌ Close")
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == ocr_action:
            self.trigger_ocr()
        elif action == edit_action and self.main_app_ref:
            pinned_before = set(self.main_app_ref.pinned_windows)
            editor = ImageEditorDialog(self.pixmap, self.main_app_ref)
            editor.exec_()
            # 仅当用户在编辑器中执行了 Pin（创建了新 PinnedWindow）时才关闭原贴图
            # 如果用户只是按 ESC 或 Close 关闭编辑器，原贴图保留不动
            if any(p not in pinned_before for p in self.main_app_ref.pinned_windows):
                self.close()
        elif action == close_action: self.close()

    def trigger_ocr(self):
        try:
            QToolTip.showText(QCursor.pos(), "🔤 正在识别贴图文字...", None, QRect(), 2500)
            ocr_img = self.pixmap.toImage()
            worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))
            def _on_done(text):
                QToolTip.hideText()
                if not text or not text.strip():
                    QMessageBox.information(None, "文字识别结果", "未能从贴图中识别到有效文字。")
                    return
                dlg = OcrResultDialog(text, None)
                dlg.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
                self._temp_ocr_dialog = dlg
                dlg.exec_()
            worker.finished.connect(_on_done)
            worker.start()
            self._temp_ocr_worker = worker
        except Exception as e:
            logging.error("贴图 OCR 触发异常: %s", e, exc_info=True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape: self.close()
        elif event.key() == Qt.Key_F2:
            self.trigger_ocr()
        elif event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_C:
            QApplication.clipboard().setPixmap(self.pixmap)
            print("Pinned image copied!")

class EditableImageLabel(QLabel):
    def __init__(self, pixmap, toolbar_ref):
        super().__init__()
        self.base_pixmap = pixmap
        self.setPixmap(pixmap)
        self.toolbar_ref = toolbar_ref
        self.edits = []
        
        self.undo_stack = []
        self.redo_stack = []
        
        self.inline_editor = InlineTextEditor(self)
        self.inline_editor.hide()

        self.is_drawing = False
        self.crop_mode = False
        self.crop_rect = None
        self.crop_ratio = None
        self.crop_drag_handle = None
        self.crop_drag_start_pos = None
        self.crop_drag_start_rect = None

    def save_state(self):
        try:
            state = {
                'edits': clone_edits(self.edits),
                'base_pixmap': QPixmap(self.base_pixmap)
            }
            self.undo_stack.append(state)
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
        except Exception as e:
            logging.error("保存编辑状态失败: %s", e, exc_info=True)

    def undo(self):
        try:
            if self.undo_stack:
                self.commit_text_edit()
                self.redo_stack.append({
                    'edits': clone_edits(self.edits),
                    'base_pixmap': QPixmap(self.base_pixmap)
                })
                state = self.undo_stack.pop()
                self.edits = state['edits']
                self.base_pixmap = state['base_pixmap']
                self.setPixmap(self.base_pixmap)
                self.resize(self.base_pixmap.size())
                self.update()
        except Exception as e:
            logging.error("撤回操作失败: %s", e, exc_info=True)

    def redo(self):
        try:
            if self.redo_stack:
                self.commit_text_edit()
                self.undo_stack.append({
                    'edits': clone_edits(self.edits),
                    'base_pixmap': QPixmap(self.base_pixmap)
                })
                state = self.redo_stack.pop()
                self.edits = state['edits']
                self.base_pixmap = state['base_pixmap']
                self.setPixmap(self.base_pixmap)
                self.resize(self.base_pixmap.size())
                self.update()
        except Exception as e:
            logging.error("重做操作失败: %s", e, exc_info=True)

    def erase_annotation(self, pos):
        for i in range(len(self.edits)-1, -1, -1):
            if is_annotation_hit(self.edits[i], pos):
                self.save_state()
                self.edits.pop(i)
                return True
        return False

    def paintEvent(self, event):
        try:
            super().paintEvent(event)
            painter = QPainter(self)
            draw_all_edits(painter, self.edits, self.base_pixmap, QPoint(0, 0))
            
            if self.toolbar_ref.current_tool == 'TEXT' and getattr(self, 'drag_mode', None) == 'text_draw' and getattr(self, 'text_rect', None):
                painter.setPen(QPen(Qt.black, 1, Qt.DashLine))
                painter.drawRect(self.text_rect)
                painter.setPen(QPen(Qt.white, 1, Qt.DashLine))
                painter.drawRect(self.text_rect.adjusted(1, 1, -1, -1))

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

                # 三分线
                painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
                for i in range(1, 3):
                    x = self.crop_rect.left() + self.crop_rect.width() * i // 3
                    painter.drawLine(x, self.crop_rect.top(), x, self.crop_rect.bottom())
                    y = self.crop_rect.top() + self.crop_rect.height() * i // 3
                    painter.drawLine(self.crop_rect.left(), y, self.crop_rect.right(), y)

                # 裁剪框边线
                painter.setPen(QPen(QColor(0, 174, 255), 2))
                painter.drawRect(self.crop_rect)

                # 8 个手柄
                handle = CROP_HANDLE_SIZE
                painter.setBrush(QBrush(QColor(0, 174, 255)))
                painter.setPen(QPen(Qt.white, 1))
                corners = [self.crop_rect.topLeft(), self.crop_rect.topRight(),
                           self.crop_rect.bottomLeft(), self.crop_rect.bottomRight()]
                edges = [QPoint(self.crop_rect.center().x(), self.crop_rect.top()),
                         QPoint(self.crop_rect.center().x(), self.crop_rect.bottom()),
                         QPoint(self.crop_rect.left(), self.crop_rect.center().y()),
                         QPoint(self.crop_rect.right(), self.crop_rect.center().y())]
                for pt in corners + edges:
                    painter.drawRect(pt.x() - handle // 2, pt.y() - handle // 2, handle, handle)
                painter.setBrush(Qt.NoBrush)

            if self.toolbar_ref.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                painter.setPen(QPen(Qt.green, 2, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self.ocr_rect)
        except Exception as e:
            logging.error("EditableImageLabel paintEvent 出错: %s", e, exc_info=True)

    def commit_text_edit(self):
        if not getattr(self, 'inline_editor', None) or not self.inline_editor.isVisible():
            return
        text = self.inline_editor.text_edit.toPlainText()
        if text.strip() or self.inline_editor.active_edit_ref:
            self.save_state() 
            rect = self.inline_editor.get_text_rect()
            if text.strip():
                self.edits.append({
                    'type': 'TEXT',
                    'rect': rect,
                    'text': text,
                    'color': self.inline_editor.current_color,
                    'font': self.inline_editor.text_edit.font()
                })
        self.inline_editor.hide()
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        try:
            if self.toolbar_ref.current_tool in ['LINE', 'RECTANGLE', 'BLUR', 'ELLIPSE', 'PEN', 'HIGHLIGHTER', 'STEP'] and not self.crop_mode:
                delta = event.angleDelta().y()
                if delta > 0: self.toolbar_ref.current_thickness = min(20, getattr(self.toolbar_ref, 'current_thickness', 3) + 1)
                else: self.toolbar_ref.current_thickness = max(1, getattr(self.toolbar_ref, 'current_thickness', 3) - 1)
                if self.edits and self.edits[-1].get('temp'): self.edits[-1]['thickness'] = self.toolbar_ref.current_thickness
                self.update(); event.accept() 
            else: event.ignore()
        except Exception as e:
            logging.error("EditableImageLabel wheelEvent 出错: %s", e, exc_info=True)

    def mouseDoubleClickEvent(self, event):
        try:
            pos = event.pos()
            # 裁剪模式下双击裁剪框内 = 应用裁剪
            if self.crop_mode and self.crop_rect and self.crop_rect.contains(pos):
                self.toolbar_ref.apply_crop()
                return
            if self.toolbar_ref.current_tool == 'TEXT':
                for edit in reversed(self.edits):
                    if edit['type'] == 'TEXT' and 'rect' in edit and edit['rect'].contains(pos):
                        self.save_state()
                        self.edits.remove(edit)
                        self.inline_editor.start_editing(edit['rect'], edit)
                        self.drag_mode = None
                        self.update()
                        break
        except Exception as e:
            logging.error("EditableImageLabel mouseDoubleClickEvent 出错: %s", e, exc_info=True)

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.RightButton:
                if self.toolbar_ref and self.toolbar_ref.current_tool:
                    self.toolbar_ref.set_tool(None)
                    return
                elif self.crop_mode:
                    self.exit_crop_mode()
                    return
            if event.button() != Qt.LeftButton: return
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
            if not tool: return

            self.commit_text_edit()

            if tool == 'ERASE':
                if self.erase_annotation(pos): self.update()
            elif tool == 'CROP':
                pass
            elif tool == 'OCR':
                self.is_drawing = True; self.ocr_start = pos; self.ocr_rect = QRect(pos, pos)
            elif tool == 'TEXT':
                clicked_edit = None
                for edit in reversed(self.edits):
                    if edit['type'] == 'TEXT' and 'rect' in edit and edit['rect'].contains(pos):
                        clicked_edit = edit; break
                
                if clicked_edit:
                    self.save_state()
                    self.drag_mode = 'text_move'
                    self.active_text_edit = clicked_edit
                    self.drag_offset = pos - clicked_edit['rect'].topLeft()
                else:
                    self.is_drawing = True
                    self.drag_mode = 'text_draw'
                    self.text_start = pos
                    self.text_rect = QRect(pos, pos)
                    # 单击即可直接在点击位置激活输入框
                    self.inline_editor.start_editing(pos)
                    
            elif tool == 'BLUR':
                self.save_state()
                self.is_drawing = True
                self.drag_mode = None
                self.edits.append({
                    'type': 'BLUR_STROKE',
                    'points': [pos],
                    'thickness': getattr(self.toolbar_ref, 'current_thickness', 3),
                    'temp': True
                })
            elif tool == 'STEP':
                self.save_state()
                step_num = getattr(self.toolbar_ref, 'current_step_num', 1)
                thick = getattr(self.toolbar_ref, 'current_thickness', 3)
                self.edits.append({
                    'type': 'STEP',
                    'pos': pos,
                    'center': pos,
                    'num': step_num,
                    'number': step_num,
                    'color': self.toolbar_ref.current_color,
                    'thickness': thick,
                    'radius': max(11, 8 + thick * 2),
                    'temp': False
                })
                self.toolbar_ref.current_step_num = step_num + 1
                self.update()
            elif tool == 'ELLIPSE':
                self.save_state()
                self.is_drawing = True
                self.drag_mode = None
                self.edit_start = pos
                self.edits.append({
                    'type': 'ELLIPSE',
                    'rect': QRect(pos, pos),
                    'color': self.toolbar_ref.current_color,
                    'thickness': getattr(self.toolbar_ref, 'current_thickness', 3),
                    'temp': True
                })
            elif tool in ['PEN', 'HIGHLIGHTER']:
                self.save_state()
                self.is_drawing = True
                self.drag_mode = None
                self.edits.append({
                    'type': tool,
                    'points': [pos],
                    'color': self.toolbar_ref.current_color,
                    'thickness': getattr(self.toolbar_ref, 'current_thickness', 3),
                    'temp': True
                })
            elif tool in ['RECTANGLE', 'LINE']:
                self.save_state()
                self.is_drawing = True; self.drag_mode = None; self.edit_start = pos
                self.edits.append({'type': tool, 'rect': QRect(pos, pos), 'start': pos, 'end': pos, 'color': self.toolbar_ref.current_color, 'arrow': self.toolbar_ref.arrow_enabled, 'temp': True, 'thickness': getattr(self.toolbar_ref, 'current_thickness', 3)})
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
                    handle = self._get_crop_handle_at(pos)
                    cursors = {
                        'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
                        'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
                        't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
                        'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
                        'move': Qt.SizeAllCursor,
                    }
                    self.setCursor(cursors.get(handle, Qt.ArrowCursor))
                return

            # 更新工具对应的光标（防止卡在上下拉伸图标）
            tool = getattr(self.toolbar_ref, 'current_tool', None)
            if tool == 'ERASE':
                self.setCursor(create_eraser_cursor())
            elif tool == 'TEXT':
                self.setCursor(Qt.IBeamCursor)
            elif tool == 'BLUR':
                r = getattr(self.toolbar_ref, 'current_thickness', 3) * 4
                self.setCursor(create_brush_cursor(r))
            elif tool == 'STEP':
                self.setCursor(Qt.PointingHandCursor)
            elif tool in ['RECTANGLE', 'LINE', 'OCR', 'ELLIPSE', 'PEN', 'HIGHLIGHTER']:
                self.setCursor(Qt.CrossCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

            if self.toolbar_ref.current_tool == 'TEXT' and self.drag_mode == 'text_move':
                new_top_left = pos - self.drag_offset
                self.active_text_edit['rect'].moveTo(new_top_left)
                self.update()

            elif self.is_drawing:
                if self.toolbar_ref.current_tool == 'OCR':
                    rect = QRect(self.ocr_start, pos).normalized()
                    self.ocr_rect = rect.intersected(self.base_pixmap.rect()); self.update()
                elif self.toolbar_ref.current_tool == 'TEXT' and self.drag_mode == 'text_draw':
                    self.text_rect = QRect(self.text_start, pos).normalized(); self.update()
                elif self.edits and self.edits[-1].get('temp'):
                    if self.edits[-1]['type'] in ['BLUR_STROKE', 'PEN', 'HIGHLIGHTER']:
                        self.edits[-1]['points'].append(pos)
                        self.update()
                    elif self.edits[-1]['type'] == 'ELLIPSE':
                        rect = QRect(self.edit_start, pos).normalized()
                        if event.modifiers() & Qt.ShiftModifier:
                            side = max(rect.width(), rect.height())
                            rect.setSize(QSize(side, side))
                        self.edits[-1]['rect'] = rect
                        self.update()
                    else:
                        self.edits[-1]['rect'] = QRect(self.edit_start, pos).normalized(); self.edits[-1]['end'] = pos; self.update()
        except Exception as e:
            logging.error("EditableImageLabel mouseMoveEvent 出错: %s", e, exc_info=True)

    def mouseReleaseEvent(self, event):
        try:
            # 裁剪模式：释放手柄
            if self.crop_mode:
                self.crop_drag_handle = None
                return

            if self.toolbar_ref.current_tool == 'TEXT' and self.drag_mode == 'text_move':
                self.drag_mode = None
                return

            if self.is_drawing:
                if self.toolbar_ref.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                    if self.ocr_rect.width() > 10 and self.ocr_rect.height() > 10:
                        sub_pixmap = self.base_pixmap.copy(self.ocr_rect)
                        self.toolbar_ref.process_ocr(sub_pixmap)
                    self.ocr_rect = None; self.update()
                    
                elif self.toolbar_ref.current_tool == 'TEXT' and self.drag_mode == 'text_draw':
                    if hasattr(self, 'text_rect') and self.text_rect.width() > 20 and self.text_rect.height() > 20:
                        self.inline_editor.start_editing(self.text_rect)
                    
                if self.edits and self.edits[-1].get('temp'): self.edits[-1]['temp'] = False
                self.is_drawing = False; self.drag_mode = None
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
            self.toolbar_ref.crop_toolbar.update_size(self.crop_rect.width(), self.crop_rect.height())
        self.update()

    def exit_crop_mode(self):
        """退出裁剪模式"""
        self.crop_mode = False
        self.crop_rect = None
        self.crop_drag_handle = None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def apply_crop(self):
        """应用裁剪"""
        if not self.crop_rect or self.crop_rect.width() < CROP_MIN_SIZE or self.crop_rect.height() < CROP_MIN_SIZE:
            return
        self.save_state()
        valid_rect = self.crop_rect.intersected(self.base_pixmap.rect())
        if valid_rect.isEmpty():
            return
        self.base_pixmap = self.base_pixmap.copy(valid_rect)
        offset = valid_rect.topLeft()
        for edit in self.edits:
            if 'rect' in edit and isinstance(edit['rect'], QRect):
                edit['rect'].translate(-offset)
            if 'start' in edit and isinstance(edit['start'], QPoint):
                edit['start'] -= offset
            if 'end' in edit and isinstance(edit['end'], QPoint):
                edit['end'] -= offset
            if 'points' in edit and isinstance(edit['points'], list):
                edit['points'] = [p - offset for p in edit['points']]
        self.setPixmap(self.base_pixmap)
        self.resize(self.base_pixmap.size())
        try:
            if self.parent() and hasattr(self.parent(), 'parent') and self.parent().parent():
                self.parent().parent().adjustSize()
        except Exception:
            pass
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

    def _update_crop_rect(self, pos):
        """根据拖动手柄更新裁剪框"""
        start = self.crop_drag_start_rect
        h = self.crop_drag_handle
        pm = self.base_pixmap
        new_rect = QRect(start)

        if h == 'move':
            delta = pos - self.crop_drag_start_pos
            new_rect = start.translated(delta)
        else:
            if 'l' in h: new_rect.setLeft(min(start.right() - CROP_MIN_SIZE, pos.x()))
            if 'r' in h: new_rect.setRight(max(start.left() + CROP_MIN_SIZE, pos.x()))
            if 't' in h: new_rect.setTop(min(start.bottom() - CROP_MIN_SIZE, pos.y()))
            if 'b' in h: new_rect.setBottom(max(start.top() + CROP_MIN_SIZE, pos.y()))

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
                    if h == 'b': new_h = max(CROP_MIN_SIZE, pos.y() - start.top())
                    else: new_h = max(CROP_MIN_SIZE, start.bottom() - pos.y())
                    new_w = int(new_h * target_ratio)
                    new_x = cx - new_w // 2
                    new_y = start.bottom() - new_h if h == 't' else start.top()
                    new_rect = QRect(new_x, new_y, new_w, new_h)
                elif h in ('l', 'r'):
                    cy = start.center().y()
                    if h == 'r': new_w = max(CROP_MIN_SIZE, pos.x() - start.left())
                    else: new_w = max(CROP_MIN_SIZE, start.right() - pos.x())
                    new_h = int(new_w / target_ratio)
                    new_x = start.right() - new_w if h == 'l' else start.left()
                    new_y = cy - new_h // 2
                    new_rect = QRect(new_x, new_y, new_w, new_h)

        new_rect = new_rect.intersected(pm.rect())
        if new_rect.width() >= CROP_MIN_SIZE and new_rect.height() >= CROP_MIN_SIZE:
            self.crop_rect = new_rect
            if hasattr(self.toolbar_ref, 'crop_toolbar') and self.toolbar_ref.crop_toolbar:
                self.toolbar_ref.crop_toolbar.update_size(self.crop_rect.width(), self.crop_rect.height())

class CropToolbar(QWidget):
    apply_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    ratio_changed = pyqtSignal(object)  # None or (w, h)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setStyleSheet("""
            QWidget#cropToolbar {
                background: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 10px;
            }
        """)
        self.setObjectName("cropToolbar")
        add_drop_shadow(self, blur=18, y_offset=4, alpha=40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(4)

        self.size_label = QLabel("0 × 0", self)
        self.size_label.setStyleSheet("color: #0969da; font-family: 'Consolas', 'Segoe UI'; font-size: 11px; font-weight: bold; padding: 0 4px; border: none; background: transparent;")
        layout.addWidget(self.size_label)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.VLine)
        sep0.setStyleSheet("color: #d0d7de;")
        layout.addWidget(sep0)

        self.ratio_buttons = {}
        for name, ratio in CROP_RATIO_PRESETS:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: #f6f8fa; color: #24292f; border: 1px solid #d0d7de; "
                "border-radius: 4px; padding: 2px 7px; font-size: 11px; }"
                "QPushButton:hover { background: #ebf5ff; color: #0969da; border-color: #0969da; }"
                "QPushButton:checked { background: #0969da; color: white; font-weight: 600; border-color: #0969da; }"
            )
            btn.clicked.connect(lambda checked, r=ratio: self._on_ratio_btn(r))
            layout.addWidget(btn)
            self.ratio_buttons[name] = (btn, ratio)

        # 默认选中 Free
        self.ratio_buttons["Free"][0].setChecked(True)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #d0d7de;")
        layout.addWidget(sep)

        apply_btn = QPushButton("✓ 确定", self)
        apply_btn.setFixedHeight(28)
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(
            "QPushButton { background: #1f883d; color: white; border: none; "
            "border-radius: 4px; font-weight: bold; font-size: 12px; padding: 2px 10px; }"
            "QPushButton:hover { background: #1a7f37; }"
        )
        apply_btn.clicked.connect(self.apply_clicked.emit)
        layout.addWidget(apply_btn)

        cancel_btn = QPushButton("✕ 取消", self)
        cancel_btn.setFixedHeight(28)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            "QPushButton { background: #fff0ee; color: #cf222e; border: 1px solid #ffbebe; "
            "border-radius: 4px; font-weight: 500; font-size: 12px; padding: 2px 8px; }"
            "QPushButton:hover { background: #ffebe9; color: #a40e26; }"
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

class ImageEditorDialog(QDialog):
    def __init__(self, image_data, main_app_ref=None):
        super().__init__()
        self.setWindowTitle("Image Editor")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.main_app_ref = main_app_ref
        
        if isinstance(image_data, QPixmap): self.base_pixmap = image_data
        else: 
            rgb_image = cv2.cvtColor(image_data, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            self.base_pixmap = QPixmap.fromImage(QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888))

        self.resize(min(1200, self.base_pixmap.width() + 40), min(800, self.base_pixmap.height() + 100))
        self.current_tool = None
        self.current_step_num = 1
        self.current_thickness = 3 
        self.arrow_enabled = False
        self.current_color = QColor(255, 0, 0)
        
        layout = QVBoxLayout(self)
        self.setup_toolbar(layout)
        self.scroll_area = QScrollArea()
        self.img_label = EditableImageLabel(self.base_pixmap, self)
        self.scroll_area.setWidget(self.img_label)
        layout.addWidget(self.scroll_area)
        # 初始化 Color 按钮显示当前颜色
        self._update_color_button()

        # 裁剪工具栏
        self.crop_toolbar = CropToolbar(self)
        self.crop_toolbar.apply_clicked.connect(self.apply_crop)
        self.crop_toolbar.cancel_clicked.connect(self.cancel_crop)
        self.crop_toolbar.ratio_changed.connect(self.img_label.set_crop_ratio)
        self.crop_toolbar.hide()

    def keyPressEvent(self, event):
        # 裁剪模式优先处理
        if self.img_label.crop_mode:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.apply_crop()
                return
            elif event.key() == Qt.Key_Escape:
                self.cancel_crop()
                return
        if event.modifiers() & Qt.ControlModifier:
            if event.key() == Qt.Key_C:
                self.copy_to_clipboard()
            elif event.key() == Qt.Key_Z:
                self.img_label.undo()
            elif event.key() == Qt.Key_Y:
                self.img_label.redo()
        elif event.key() == Qt.Key_Escape:
            # 关闭前先提交正在编辑的文本，避免内容丢失
            self.img_label.commit_text_edit()
            self.close()
        elif event.key() == Qt.Key_F2:
            self.set_tool('OCR', self.btn_ocr)
            return

    def setup_toolbar(self, parent_layout):
        self.toolbar = QWidget()
        self.toolbar.setObjectName("editorToolbar")
        self.toolbar.setStyleSheet(TOOLBAR_STYLE)
        layout = QHBoxLayout(self.toolbar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        self.btn_rect = QPushButton("Rect")
        self.btn_ellipse = QPushButton("Ellipse")
        self.btn_line = QPushButton("Line")
        self.btn_arrow = QPushButton("Arrow Off")
        self.btn_pen = QPushButton("Pen")
        self.btn_highlighter = QPushButton("Highlighter")
        self.btn_step = QPushButton("Step")
        self.btn_text = QPushButton("Text")
        self.btn_blur = QPushButton("Blur")
        self.btn_erase = QPushButton("Erase")
        self.btn_color = QPushButton("Color")
        self.btn_crop = QPushButton("Crop")
        self.btn_ocr = QPushButton("OCR")
        self.btn_history = QPushButton("History")
        self.btn_settings = QPushButton("Settings")
        self.btn_pin = QPushButton("Pin")
        self.btn_copy = QPushButton("Copy")
        self.btn_save = QPushButton("Save")
        self.btn_close = QPushButton("Close")
        
        self.btn_rect.clicked.connect(lambda: self.set_tool('RECTANGLE', self.btn_rect))
        self.btn_ellipse.clicked.connect(lambda: self.set_tool('ELLIPSE', self.btn_ellipse))
        self.btn_line.clicked.connect(lambda: self.set_tool('LINE', self.btn_line))
        self.btn_arrow.clicked.connect(self.toggle_arrow)
        self.btn_pen.clicked.connect(lambda: self.set_tool('PEN', self.btn_pen))
        self.btn_highlighter.clicked.connect(lambda: self.set_tool('HIGHLIGHTER', self.btn_highlighter))
        self.btn_step.clicked.connect(lambda: self.set_tool('STEP', self.btn_step))
        self.btn_text.clicked.connect(lambda: self.set_tool('TEXT', self.btn_text))
        self.btn_blur.clicked.connect(lambda: self.set_tool('BLUR', self.btn_blur))
        self.btn_erase.clicked.connect(lambda: self.set_tool('ERASE', self.btn_erase))
        self.btn_color.clicked.connect(self.pick_drawing_color)
        self.btn_crop.clicked.connect(lambda: self.set_tool('CROP', self.btn_crop))
        self.btn_ocr.clicked.connect(lambda: self.set_tool('OCR', self.btn_ocr))
        self.btn_history.clicked.connect(self.open_history)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_pin.clicked.connect(self.pin_to_screen)
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        self.btn_save.clicked.connect(self.save_image)
        self.btn_close.clicked.connect(self.close)

        all_btns = [
            self.btn_rect, self.btn_ellipse, self.btn_line, self.btn_arrow, self.btn_pen,
            self.btn_highlighter, self.btn_step, self.btn_text, self.btn_blur, self.btn_erase,
            self.btn_color, self.btn_crop, self.btn_ocr, self.btn_history, self.btn_settings,
            self.btn_pin, self.btn_copy, self.btn_save, self.btn_close
        ]
        for btn in all_btns:
            btn.setCursor(Qt.PointingHandCursor)
            apply_button_style(btn, danger=(btn is self.btn_close))
            layout.addWidget(btn)
        parent_layout.addWidget(self.toolbar)

    def open_history(self):
        dlg = HistoryDialog(self, self.main_app_ref)
        dlg.exec_()

    def open_settings(self):
        hotkey_mgr = getattr(self.main_app_ref, 'hotkey_listener', None)
        dlg = SettingsDialog(self, hotkey_mgr)
        dlg.exec_()

    def toggle_arrow(self):
        self.arrow_enabled = not self.arrow_enabled
        self.btn_arrow.setText("Arrow On" if self.arrow_enabled else "Arrow Off")

    def pick_drawing_color(self):
        color = QColorDialog.getColor(self.current_color, self, "Pick Drawing Color")
        if color.isValid():
            self.current_color = color
            self._update_color_button()

    def _update_color_button(self):
        self.btn_color.setStyleSheet(
            f"background-color: {self.current_color.name()}; color: {'white' if self.current_color.lightness() < 128 else 'black'}; border: 2px solid #888; border-radius: 4px; padding: 4px 12px;"
        )
        self.btn_color.setText("Color")

    def process_ocr(self, pixmap):
        self.btn_ocr.setText("OCR...")
        ocr_img = pixmap.toImage()
        self.ocr_worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))
        self.ocr_worker.finished.connect(self.show_ocr_result)
        self.ocr_worker.start()

    def show_ocr_result(self, text):
        self.btn_ocr.setText("OCR")
        if not text:
            QMessageBox.information(self, "OCR 结果", "未能识别到文字，请重试。")
        else:
            dlg = OcrResultDialog(text, self)
            dlg.exec_()

    def set_tool(self, tool_name, active_btn=None):
        if getattr(self.img_label, 'inline_editor', None) and self.img_label.inline_editor.isVisible():
            self.img_label.commit_text_edit()

        # 退出裁剪模式（如果正在裁剪且不是切换到 CROP）
        if self.img_label.crop_mode and tool_name != 'CROP':
            self.cancel_crop()

        # 切换工具时重置 OCR 按钮文字
        if self.btn_ocr.text() != "OCR":
            self.btn_ocr.setText("OCR")

        tool_btns = [
            self.btn_rect, self.btn_ellipse, self.btn_line, self.btn_pen,
            self.btn_highlighter, self.btn_step, self.btn_crop, self.btn_text,
            self.btn_blur, self.btn_erase, self.btn_ocr
        ]

        if tool_name is None or self.current_tool == tool_name:
            self.current_tool = None
            for btn in tool_btns:
                apply_button_style(btn)
            return

        self.current_tool = tool_name
        for btn in tool_btns:
            apply_button_style(btn)
        if active_btn:
            apply_button_style(active_btn, active=True)

        # 进入裁剪模式
        if tool_name == 'CROP':
            self.img_label.enter_crop_mode()
            self._show_crop_toolbar()
        else:
            if self.img_label.crop_mode:
                self.img_label.exit_crop_mode()
            self.crop_toolbar.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.current_tool:
                self.set_tool(None)
                return
            elif self.img_label.crop_mode:
                self.cancel_crop()
                return
        super().keyPressEvent(event)

    def _show_crop_toolbar(self):
        """显示裁剪工具栏在窗口顶部居中"""
        self.crop_toolbar.adjustSize()
        x = (self.width() - self.crop_toolbar.width()) // 2
        y = 8
        self.crop_toolbar.move(max(0, x), y)
        self.crop_toolbar.show()
        self.crop_toolbar.raise_()

    def apply_crop(self):
        """应用裁剪"""
        self.img_label.apply_crop()
        self.crop_toolbar.hide()
        self.set_tool(None, self.btn_crop)

    def cancel_crop(self):
        """取消裁剪"""
        self.img_label.exit_crop_mode()
        self.crop_toolbar.hide()
        self.set_tool(None, self.btn_crop)

    def get_final_static_image(self):
        if not self.img_label.edits:
            return self.img_label.base_pixmap
        final_img = QPixmap(self.img_label.base_pixmap.size())
        final_img.fill(Qt.transparent)
        painter = QPainter(final_img)
        painter.drawPixmap(0, 0, self.img_label.base_pixmap)
        draw_all_edits(painter, self.img_label.edits, self.img_label.base_pixmap, QPoint(0,0))
        painter.end()
        return final_img

    def copy_to_clipboard(self):
        try:
            self.img_label.commit_text_edit()
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
            self.img_label.commit_text_edit()
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
            self.img_label.commit_text_edit()
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
HISTORY_DIR = os.path.join(BASE_DIR, "capture_history")
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
                except Exception:
                    pass

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


class HistoryDialog(QDialog):
    """现代卡片流式截图历史管理窗口，支持持久化、缩略图预览、重新编辑、贴图、复制、另存与清理"""
    def __init__(self, parent=None, main_app_ref=None):
        super().__init__(parent)
        self.main_app_ref = main_app_ref
        self.mgr = CaptureHistoryManager.get_instance()

        self.setWindowTitle("📜 截图历史记录 (History)")
        self.resize(880, 620)
        self.setMinimumSize(600, 450)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QDialog { background: #ffffff; color: #24292f; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
            QLabel { color: #24292f; }
            QFrame#card {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QFrame#card:hover {
                border: 1px solid #0969da;
                background: #f0f7ff;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #0969da;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # 顶部工具栏
        top_bar = QHBoxLayout()
        self.title_lbl = QLabel(f"<b>截图历史记录</b> <span style='color:#656d76;'>（共 {len(self.mgr.items)} 条）</span>")
        self.title_lbl.setStyleSheet("font-size: 16px;")
        top_bar.addWidget(self.title_lbl)

        top_bar.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索时间或尺寸...")
        self.search_input.setFixedWidth(200)
        self.search_input.textChanged.connect(self.populate_cards)
        top_bar.addWidget(self.search_input)

        self.btn_clear = QPushButton("🧹 清空全部")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        apply_button_style(self.btn_clear, danger=True)
        self.btn_clear.clicked.connect(self.on_clear_all)
        top_bar.addWidget(self.btn_clear)

        self.btn_close = QPushButton("✕ 关闭")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        apply_button_style(self.btn_close)
        self.btn_close.clicked.connect(self.close)
        top_bar.addWidget(self.btn_close)

        layout.addLayout(top_bar)

        # 中部滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: 1px solid #e2e8f0; border-radius: 8px; background: #ffffff; }")

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(12, 12, 12, 12)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()
        self.scroll_area.setWidget(self.cards_container)

        layout.addWidget(self.scroll_area, 1)

        self.populate_cards()

    def populate_cards(self):
        while self.cards_layout.count() > 1:
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        filter_txt = self.search_input.text().strip().lower()
        matched_items = []
        for item in self.mgr.items:
            if not filter_txt:
                matched_items.append(item)
            else:
                desc = f"{item.get('time_str', '')} {item.get('width', 0)}x{item.get('height', 0)} {item.get('title', '')}".lower()
                if filter_txt in desc:
                    matched_items.append(item)

        self.title_lbl.setText(f"<b>截图历史记录</b> <span style='color:#656d76;'>（共 {len(matched_items)} 条）</span>")

        if not matched_items:
            empty_lbl = QLabel("暂无截图历史记录\n使用 F1 截图后，将自动在这里保存与展示历史。")
            empty_lbl.setAlignment(Qt.AlignCenter)
            empty_lbl.setStyleSheet("color: #8c959f; font-size: 14px; padding: 60px;")
            self.cards_layout.insertWidget(0, empty_lbl)
            return

        for item in matched_items:
            card = self._create_item_card(item)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    def _create_item_card(self, item):
        card = QFrame()
        card.setObjectName("card")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(16)

        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(140, 95)
        thumb_lbl.setAlignment(Qt.AlignCenter)
        thumb_lbl.setStyleSheet("background: #0d1117; border-radius: 6px; border: 1px solid #d0d7de;")
        tpath = item.get('thumb_path', '')
        if os.path.exists(tpath):
            pix = QPixmap(tpath)
            if not pix.isNull():
                scaled = pix.scaled(136, 91, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                thumb_lbl.setPixmap(scaled)
        card_layout.addWidget(thumb_lbl)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        lbl_time = QLabel(f"<b>🕒 时间:</b> {item.get('time_str', '')}")
        lbl_time.setStyleSheet("font-size: 13px; font-weight: 600; color: #1f2328;")
        lbl_res = QLabel(f"<b>📐 尺寸:</b> {item.get('width', 0)} × {item.get('height', 0)} 像素")
        lbl_res.setStyleSheet("font-size: 12px; color: #57606a;")
        lbl_path = QLabel(f"<b>📁 存储:</b> {os.path.basename(item.get('filepath', ''))}")
        lbl_path.setStyleSheet("font-size: 11px; color: #8c959f;")

        info_col.addWidget(lbl_time)
        info_col.addWidget(lbl_res)
        info_col.addWidget(lbl_path)
        info_col.addStretch()
        card_layout.addLayout(info_col, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)

        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(6)

        btn_copy = QPushButton("📋 复制")
        btn_copy.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_copy)
        btn_copy.clicked.connect(lambda _, it=item: self._copy_item(it))
        btn_row1.addWidget(btn_copy)

        btn_edit = QPushButton("✏ 编辑")
        btn_edit.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_edit)
        btn_edit.clicked.connect(lambda _, it=item: self._edit_item(it))
        btn_row1.addWidget(btn_edit)

        btn_pin = QPushButton("📌 贴图")
        btn_pin.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_pin)
        btn_pin.clicked.connect(lambda _, it=item: self._pin_item(it))
        btn_row1.addWidget(btn_pin)

        btn_col.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(6)

        btn_save = QPushButton("💾 另存...")
        btn_save.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_save)
        btn_save.clicked.connect(lambda _, it=item: self._save_item(it))
        btn_row2.addWidget(btn_save)

        btn_del = QPushButton("🗑 删除")
        btn_del.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_del, danger=True)
        btn_del.clicked.connect(lambda _, it=item: self._delete_item(it))
        btn_row2.addWidget(btn_del)

        btn_col.addLayout(btn_row2)
        card_layout.addLayout(btn_col)

        return card

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
                self.close()
                editor = ImageEditorDialog(pix, self.main_app_ref)
                editor.exec_()

    def _pin_item(self, item):
        fpath = item.get('filepath', '')
        if os.path.exists(fpath):
            pix = QPixmap(fpath)
            if not pix.isNull() and self.main_app_ref:
                self.close()
                self.main_app_ref.create_pinned_window(pix)

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


class SettingsDialog(QDialog):
    """系统偏好设置面板：快捷键自定义、默认保存目录与画质、OpenRouter AI 模型配置"""
    def __init__(self, parent=None, hotkey_mgr=None):
        super().__init__(parent)
        self.hotkey_mgr = hotkey_mgr
        self.setWindowTitle("⚙ 系统设置 - Murioki Capture")
        self.resize(580, 560)
        self.setMinimumSize(520, 500)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QDialog { background: #ffffff; color: #24292f; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
            QLabel { color: #24292f; font-size: 13px; }
            QGroupBox {
                font-size: 13px;
                font-weight: 600;
                color: #0969da;
                border: 1px solid #d0d7de;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }
            QLineEdit, QComboBox {
                background: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
                color: #24292f;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #0969da; }
            QCheckBox { font-size: 13px; color: #24292f; spacing: 8px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 1. 全局快捷键分组
        hk_group = QGroupBox("⌨ 全局快捷键自定义")
        hk_layout = QGridLayout(hk_group)
        hk_layout.setContentsMargins(14, 14, 14, 14)
        hk_layout.setSpacing(10)

        hk_layout.addWidget(QLabel("截屏快捷键:"), 0, 0)
        self.input_hk_snip = QLineEdit()
        self.input_hk_snip.setPlaceholderText("例如: F1 或 Ctrl+Alt+A")
        hk_layout.addWidget(self.input_hk_snip, 0, 1)

        hk_layout.addWidget(QLabel("识图/翻译:"), 1, 0)
        self.input_hk_ocr = QLineEdit()
        self.input_hk_ocr.setPlaceholderText("例如: F2")
        hk_layout.addWidget(self.input_hk_ocr, 1, 1)

        hk_layout.addWidget(QLabel("贴图置顶:"), 2, 0)
        self.input_hk_pin = QLineEdit()
        self.input_hk_pin.setPlaceholderText("例如: F3")
        hk_layout.addWidget(self.input_hk_pin, 2, 1)

        hk_layout.addWidget(QLabel("屏幕录制:"), 3, 0)
        self.input_hk_rec = QLineEdit()
        self.input_hk_rec.setPlaceholderText("例如: F4")
        hk_layout.addWidget(self.input_hk_rec, 3, 1)

        btn_reset_hk = QPushButton("恢复默认快捷键 (F1~F4)")
        btn_reset_hk.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_reset_hk)
        btn_reset_hk.clicked.connect(self._reset_default_hotkeys)
        hk_layout.addWidget(btn_reset_hk, 4, 1, Qt.AlignRight)

        layout.addWidget(hk_group)

        # 2. 保存与文件输出分组
        save_group = QGroupBox("💾 保存与输出偏好")
        save_layout = QGridLayout(save_group)
        save_layout.setContentsMargins(14, 14, 14, 14)
        save_layout.setSpacing(10)

        save_layout.addWidget(QLabel("默认保存目录:"), 0, 0)
        dir_box = QHBoxLayout()
        self.input_save_dir = QLineEdit()
        dir_box.addWidget(self.input_save_dir, 1)
        btn_browse_dir = QPushButton("浏览...")
        btn_browse_dir.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_browse_dir)
        btn_browse_dir.clicked.connect(self._browse_save_dir)
        dir_box.addWidget(btn_browse_dir)
        save_layout.addLayout(dir_box, 0, 1)

        self.cb_auto_save = QCheckBox("截图/复制时自动在后台静默保存一份副本到该目录")
        save_layout.addWidget(self.cb_auto_save, 1, 0, 1, 2)

        save_layout.addWidget(QLabel("默认存储格式:"), 2, 0)
        self.combo_format = QComboBox()
        self.combo_format.addItems(["PNG (*.png)", "JPEG (*.jpg)", "WebP (*.webp)"])
        save_layout.addWidget(self.combo_format, 2, 1)

        save_layout.addWidget(QLabel("压缩画质 (JPEG/WebP):"), 3, 0)
        qual_box = QHBoxLayout()
        self.slider_quality = QSlider(Qt.Horizontal)
        self.slider_quality.setRange(70, 100)
        self.slider_quality.setValue(95)
        self.lbl_qual_val = QLabel("95%")
        self.lbl_qual_val.setFixedWidth(40)
        self.slider_quality.valueChanged.connect(lambda v: self.lbl_qual_val.setText(f"{v}%"))
        qual_box.addWidget(self.slider_quality, 1)
        qual_box.addWidget(self.lbl_qual_val)
        save_layout.addLayout(qual_box, 3, 1)

        layout.addWidget(save_group)

        # 3. AI 识别与翻译分组
        ai_group = QGroupBox("🤖 AI 大模型服务 (OpenRouter)")
        ai_layout = QGridLayout(ai_group)
        ai_layout.setContentsMargins(14, 14, 14, 14)
        ai_layout.setSpacing(10)

        ai_layout.addWidget(QLabel("API Key:"), 0, 0)
        key_box = QHBoxLayout()
        self.input_api_key = QLineEdit()
        self.input_api_key.setEchoMode(QLineEdit.Password)
        self.input_api_key.setPlaceholderText("sk-or-v1-...")
        key_box.addWidget(self.input_api_key, 1)
        btn_toggle_key = QPushButton("👁")
        btn_toggle_key.setFixedWidth(30)
        btn_toggle_key.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_toggle_key)
        def _toggle_key():
            if self.input_api_key.echoMode() == QLineEdit.Password:
                self.input_api_key.setEchoMode(QLineEdit.Normal)
            else:
                self.input_api_key.setEchoMode(QLineEdit.Password)
        btn_toggle_key.clicked.connect(_toggle_key)
        key_box.addWidget(btn_toggle_key)
        ai_layout.addLayout(key_box, 0, 1)

        ai_layout.addWidget(QLabel("首选模型:"), 1, 0)
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        model_options = [
            "qwen/qwen-2.5-vl-72b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "google/gemini-2.0-flash-thinking-exp:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "openrouter/auto"
        ]
        self.combo_model.addItems(model_options)
        ai_layout.addWidget(self.combo_model, 1, 1)

        chk_box = QHBoxLayout()
        self.cb_ai_trans = QCheckBox("启用 AI 智能翻译")
        self.cb_ai_ocr = QCheckBox("启用 AI 多模态文字识别")
        chk_box.addWidget(self.cb_ai_trans)
        chk_box.addWidget(self.cb_ai_ocr)
        ai_layout.addLayout(chk_box, 2, 0, 1, 2)

        layout.addWidget(ai_group)

        # 底部按钮栏
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(10)
        btn_bar.addStretch()

        btn_save = QPushButton("💾 保存并应用")
        btn_save.setFixedHeight(34)
        btn_save.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_save, active=True)
        btn_save.clicked.connect(self.save_and_apply)
        btn_bar.addWidget(btn_save)

        btn_cancel = QPushButton("✕ 取消")
        btn_cancel.setFixedHeight(34)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        apply_button_style(btn_cancel)
        btn_cancel.clicked.connect(self.close)
        btn_bar.addWidget(btn_cancel)

        layout.addLayout(btn_bar)

        self._load_current_values()

    def _load_current_values(self):
        s = load_settings()
        self.input_hk_snip.setText(s.get("hotkey_snip", "F1"))
        self.input_hk_ocr.setText(s.get("hotkey_ocr", "F2"))
        self.input_hk_pin.setText(s.get("hotkey_pin", "F3"))
        self.input_hk_rec.setText(s.get("hotkey_record", "F4"))

        self.input_save_dir.setText(s.get("default_save_dir", os.path.join(os.path.expanduser("~"), "Pictures")))
        self.cb_auto_save.setChecked(s.get("auto_save_enabled", False))

        fmt = s.get("save_format", "png").lower()
        if "jp" in fmt: self.combo_format.setCurrentIndex(1)
        elif "webp" in fmt: self.combo_format.setCurrentIndex(2)
        else: self.combo_format.setCurrentIndex(0)

        qual = int(s.get("save_quality", 95))
        self.slider_quality.setValue(qual)
        self.lbl_qual_val.setText(f"{qual}%")

        self.input_api_key.setText(s.get("openrouter_api_key", ""))
        self.combo_model.setCurrentText(s.get("openrouter_model", DEFAULT_OPENROUTER_MODEL))
        self.cb_ai_trans.setChecked(s.get("use_ai_translation", False))
        self.cb_ai_ocr.setChecked(s.get("use_ai_ocr", False))

    def _reset_default_hotkeys(self):
        self.input_hk_snip.setText("F1")
        self.input_hk_ocr.setText("F2")
        self.input_hk_pin.setText("F3")
        self.input_hk_rec.setText("F4")

    def _browse_save_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "选择默认保存目录", self.input_save_dir.text())
        if chosen:
            self.input_save_dir.setText(chosen)

    def save_and_apply(self):
        fmt_idx = self.combo_format.currentIndex()
        fmt_str = "png" if fmt_idx == 0 else ("jpg" if fmt_idx == 1 else "webp")

        new_settings = {
            "hotkey_snip": self.input_hk_snip.text().strip() or "F1",
            "hotkey_ocr": self.input_hk_ocr.text().strip() or "F2",
            "hotkey_pin": self.input_hk_pin.text().strip() or "F3",
            "hotkey_record": self.input_hk_rec.text().strip() or "F4",
            "default_save_dir": self.input_save_dir.text().strip(),
            "auto_save_enabled": self.cb_auto_save.isChecked(),
            "save_format": fmt_str,
            "save_quality": self.slider_quality.value(),
            "openrouter_api_key": self.input_api_key.text().strip(),
            "openrouter_model": self.combo_model.currentText().strip() or DEFAULT_OPENROUTER_MODEL,
            "use_ai_translation": self.cb_ai_trans.isChecked(),
            "use_ai_ocr": self.cb_ai_ocr.isChecked(),
        }
        save_settings(new_settings)
        if self.hotkey_mgr:
            self.hotkey_mgr.reload_hotkeys()
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
        painter.setPen(QPen(QColor(0, 174, 255), 3))
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

    def run(self):
        try:
            os.makedirs(REC_TEMP_FOLDER, exist_ok=True)
            preset = RECORD_QUALITY_PRESETS.get(self.quality_key, RECORD_QUALITY_PRESETS['high'])
            scale = preset['scale']
            self.fps = preset['fps']
            frame_interval = 1.0 / self.fps

            # OpenCV 要求写入尺寸必须为偶数
            out_w = int(self.rect.width() * scale)
            out_h = int(self.rect.height() * scale)
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
                "top": self.rect.y(),
                "left": self.rect.x(),
                "width": self.rect.width(),
                "height": self.rect.height()
            }

            with mss.mss() as sct:
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
        except Exception:
            pass

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


class RecordingToolbar(QWidget):
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    pause_toggled = pyqtSignal(bool)
    cancel_requested = pyqtSignal()
    quality_changed = pyqtSignal(str)

    def __init__(self, rect, default_quality='original', parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.target_rect = rect
        self.is_recording = False
        self.is_paused = False
        self.drag_position = None

        self.setStyleSheet("""
            QWidget#recToolbar {
                background-color: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 10px;
            }
            QLabel {
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 12px;
                color: #24292f;
            }
            QComboBox {
                background: #f6f8fa;
                color: #24292f;
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #24292f;
                selection-background-color: #0969da;
                selection-color: #ffffff;
                border: 1px solid #d0d7de;
            }
        """)
        self.setObjectName("recToolbar")
        add_drop_shadow(self, blur=18, y_offset=4, alpha=40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        # 录制状态指示红点
        self.dot_label = QLabel("●")
        self.dot_label.setStyleSheet("color: #ef4444; font-size: 16px; font-weight: bold;")
        layout.addWidget(self.dot_label)

        # 录制时间显示
        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet("font-family: 'Consolas', 'Segoe UI'; font-size: 13px; font-weight: bold; color: #cf222e; min-width: 44px;")
        layout.addWidget(self.time_label)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setStyleSheet("color: #d0d7de;")
        layout.addWidget(sep1)

        # 尺寸显示
        self.size_label = QLabel(f"{rect.width()} × {rect.height()}")
        self.size_label.setStyleSheet("font-family: 'Consolas', 'Segoe UI'; font-size: 11px; color: #57606a;")
        layout.addWidget(self.size_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet("color: #d0d7de;")
        layout.addWidget(sep2)

        # 画质预设下拉框（支持 60FPS 极速高刷与 30FPS 均衡预设）
        self.combo_quality = QComboBox()
        self.combo_quality.addItem("⚡ 原画 60FPS (100%)", "p60")
        self.combo_quality.addItem("🎬 原画 30FPS (100%)", "p30")
        self.combo_quality.addItem("📺 高清 60FPS (75%)", "high60")
        self.combo_quality.addItem("📺 高清 30FPS (75%)", "high30")
        self.combo_quality.addItem("📦 标清 30FPS (50%)", "standard")
        found_idx = 0
        for i in range(self.combo_quality.count()):
            if self.combo_quality.itemData(i) == default_quality:
                found_idx = i
                break
        self.combo_quality.setCurrentIndex(found_idx)
        self.combo_quality.currentIndexChanged.connect(self._on_quality_index_changed)
        layout.addWidget(self.combo_quality)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.VLine)
        sep3.setStyleSheet("color: #d0d7de;")
        layout.addWidget(sep3)

        # 开始/完成录制按钮
        self.btn_rec = QPushButton("● 开始录制")
        self.btn_rec.setCursor(Qt.PointingHandCursor)
        self.btn_rec.setStyleSheet("""
            QPushButton {
                background: #1f883d;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #1a7f37; }
        """)
        self.btn_rec.clicked.connect(self._toggle_recording)
        layout.addWidget(self.btn_rec)

        # 暂停/继续按钮
        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        apply_button_style(self.btn_pause)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        layout.addWidget(self.btn_pause)

        # 取消按钮
        self.btn_cancel = QPushButton("✕ 取消")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        apply_button_style(self.btn_cancel, danger=True)
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(self.btn_cancel)

        self.adjustSize()
        self._position_toolbar()
        self.show()

        # 对控制栏同样设置 WDA_EXCLUDEFROMCAPTURE，确保控制条即使在录制区域内也绝不录进视频
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x11)
        except Exception:
            pass

    def _position_toolbar(self):
        screen_geo = QApplication.primaryScreen().geometry()
        w = self.width()
        h = self.height()
        x = self.target_rect.center().x() - w // 2
        y = self.target_rect.bottom() + 10
        if y + h > screen_geo.bottom() - 10:
            y = self.target_rect.top() - h - 10
        x = max(10, min(x, screen_geo.right() - w - 10))
        y = max(10, min(y, screen_geo.bottom() - h - 10))
        self.move(x, y)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.drag_position:
            self.move(event.globalPos() - self.drag_position)

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
        self.btn_pause.setText("▶ 继续" if self.is_paused else "⏸ 暂停")
        self.pause_toggled.emit(self.is_paused)

    def set_recording_state(self, recording):
        self.is_recording = recording
        self.combo_quality.setEnabled(not recording)
        self.btn_pause.setEnabled(recording)
        if recording:
            self.btn_rec.setText("■ 完成录制")
            self.btn_rec.setStyleSheet("""
                QPushButton {
                    background: #cf222e;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover { background: #a40e26; }
            """)
        else:
            self.btn_rec.setText("● 开始录制")
            self.btn_rec.setStyleSheet("""
                QPushButton {
                    background: #1f883d;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover { background: #1a7f37; }
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
        self.fps = info.get('fps', 30.0) or 30.0
        self.is_playing = False

        self.setWindowTitle("🎥 屏幕录制完成 - 导出预览")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.resize(680, 560)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                color: #24292f;
                font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            }
            QLabel {
                color: #24292f;
                font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
                font-size: 12px;
            }
            QFrame#cardFrame {
                background-color: #f6f8fa;
                border: 1px solid #d0d7de;
                border-radius: 8px;
            }
            QSlider::groove:horizontal {
                border: 1px solid #d0d7de;
                height: 6px;
                background: #eaeef2;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #0969da;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 2px solid #0969da;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. 顶部概要信息卡片
        info_card = QFrame()
        info_card.setObjectName("cardFrame")
        card_layout = QHBoxLayout(info_card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(16)

        sec = int(info.get('duration', 0))
        dur_str = f"{sec // 60:02d}:{sec % 60:02d}"
        file_size_bytes = os.path.getsize(self.video_path) if os.path.exists(self.video_path) else 0
        if file_size_bytes >= 1024 * 1024:
            size_str = f"{file_size_bytes / (1024 * 1024):.1f} MB"
        else:
            size_str = f"{file_size_bytes / 1024:.1f} KB"

        lbl_dur = QLabel(f"⏱ <b>时长:</b> {dur_str}")
        lbl_res = QLabel(f"📐 <b>分辨率:</b> {info.get('width', 0)} × {info.get('height', 0)}")
        lbl_frames = QLabel(f"🎞 <b>总帧数:</b> {info.get('frame_count', 0)} 帧 ({int(self.fps)}fps)")
        lbl_quality = QLabel(f"💎 <b>画质:</b> {info.get('quality_name', '原画')}")
        lbl_size = QLabel(f"📦 <b>大小:</b> {size_str}")

        for lbl in [lbl_dur, lbl_res, lbl_frames, lbl_quality, lbl_size]:
            card_layout.addWidget(lbl)

        main_layout.addWidget(info_card)

        # 2. 视频预览显示区
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #0d1117; border-radius: 8px; border: 1px solid #d0d7de;")
        self.preview_label.setMinimumHeight(300)
        main_layout.addWidget(self.preview_label, 1)

        # 3. 播放控制条 (播放/暂停、进度滑块、当前时间)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(8)

        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setFixedWidth(70)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        apply_button_style(self.btn_play)
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl_layout.addWidget(self.btn_play)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.sliderMoved.connect(self.on_slider_moved)
        ctrl_layout.addWidget(self.slider)

        self.time_lbl = QLabel(f"00:00 / {dur_str}")
        self.time_lbl.setStyleSheet("font-family: 'Consolas', 'Segoe UI'; font-size: 11px; color: #57606a;")
        ctrl_layout.addWidget(self.time_lbl)

        main_layout.addLayout(ctrl_layout)

        # 4. GIF 导出参数面板
        gif_opt_layout = QHBoxLayout()
        gif_opt_layout.setSpacing(10)
        gif_opt_layout.addWidget(QLabel("<b>GIF 导出选项:</b>"))
        gif_opt_layout.addWidget(QLabel("帧率:"))
        self.combo_gif_fps = QComboBox()
        self.combo_gif_fps.addItems(["5 FPS", "10 FPS", "15 FPS", "20 FPS"])
        self.combo_gif_fps.setCurrentIndex(1)
        gif_opt_layout.addWidget(self.combo_gif_fps)

        gif_opt_layout.addWidget(QLabel("尺寸:"))
        self.combo_gif_scale = QComboBox()
        self.combo_gif_scale.addItems(["原尺寸 (100%)", "高清 (75%)", "适中 (50%)", "紧凑 (33%)"])
        self.combo_gif_scale.setCurrentIndex(1)
        gif_opt_layout.addWidget(self.combo_gif_scale)
        gif_opt_layout.addStretch()
        main_layout.addLayout(gif_opt_layout)

        # 5. 底部操作按钮栏 (保存MP4、保存GIF、复制GIF、关闭)
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(10)

        self.btn_save_mp4 = QPushButton("💾 保存为 MP4 视频")
        self.btn_save_mp4.setFixedHeight(34)
        self.btn_save_mp4.setCursor(Qt.PointingHandCursor)
        self.btn_save_mp4.setStyleSheet("""
            QPushButton {
                background: #0969da;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #085cc0; }
        """)
        self.btn_save_mp4.clicked.connect(self.save_as_mp4)
        btn_bar.addWidget(self.btn_save_mp4)

        self.btn_save_gif = QPushButton("🎞 保存为 GIF 动图")
        self.btn_save_gif.setFixedHeight(34)
        self.btn_save_gif.setCursor(Qt.PointingHandCursor)
        self.btn_save_gif.setStyleSheet("""
            QPushButton {
                background: #1f883d;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #1a7f37; }
        """)
        self.btn_save_gif.clicked.connect(self.save_as_gif)
        btn_bar.addWidget(self.btn_save_gif)

        self.btn_copy_gif = QPushButton("📋 复制 GIF 到剪贴板")
        self.btn_copy_gif.setFixedHeight(34)
        self.btn_copy_gif.setCursor(Qt.PointingHandCursor)
        self.btn_copy_gif.setStyleSheet("""
            QPushButton {
                background: #f6f8fa;
                color: #24292f;
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background: #f3f4f6; color: #0969da; border-color: #0969da; }
        """)
        self.btn_copy_gif.clicked.connect(self.copy_gif_to_clipboard)
        btn_bar.addWidget(self.btn_copy_gif)

        btn_bar.addStretch()

        self.btn_close = QPushButton("✕ 关闭")
        self.btn_close.setFixedHeight(34)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        apply_button_style(self.btn_close, danger=True)
        self.btn_close.clicked.connect(self.close)
        btn_bar.addWidget(self.btn_close)

        main_layout.addLayout(btn_bar)

        # 初始化视频解码器与播放定时器
        self.play_timer = QTimer(self)
        interval = max(10, int(1000.0 / self.fps))
        self.play_timer.setInterval(interval)
        self.play_timer.timeout.connect(self._on_play_step)

        self._load_video()

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
            pix = QPixmap.fromImage(qimg)

            lbl_w = max(100, self.preview_label.width() - 8)
            lbl_h = max(100, self.preview_label.height() - 8)
            scaled = pix.scaled(lbl_w, lbl_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview_label.setPixmap(scaled)

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
                except Exception:
                    pass
        except Exception as e:
            logging.error("RecordingExportDialog closeEvent 异常: %s", e)
        super().closeEvent(event)

def parse_hotkey_to_native(hotkey_str):
    """解析如 'F1', 'Ctrl+Alt+A', 'Shift+F2' 等快捷键字符串为 Windows 原生 MOD 掩码与 VK 键码"""
    if not hotkey_str:
        return 0, 0
    parts = [p.strip().lower() for p in hotkey_str.split('+')]
    mod = 0x4000  # MOD_NOREPEAT
    vk = 0
    for p in parts:
        if p in ['ctrl', 'control']:
            mod |= 0x0002
        elif p == 'alt':
            mod |= 0x0001
        elif p == 'shift':
            mod |= 0x0004
        elif p in ['win', 'windows']:
            mod |= 0x0008
        elif p.startswith('f') and p[1:].isdigit():
            f_num = int(p[1:])
            if 1 <= f_num <= 24:
                vk = 0x70 + (f_num - 1)
        elif len(p) == 1 and p.isalnum():
            vk = ord(p.upper())
        elif p == 'space':
            vk = 0x20
        elif p == 'esc':
            vk = 0x1B
        elif p in ['printscreen', 'prtscr']:
            vk = 0x2C
    return mod, vk


class GlobalHotkeyManager(QObject):
    """
    基于 Windows 原生 RegisterHotKey 的全局快捷键管理器。

    优势：
    1. 不依赖易被系统静默卸载的低级键盘钩子 (WH_KEYBOARD_LL)。
    2. 无需周期性 unhook_all 刷新，彻底解决快捷键失效、打字卡顿与丢键问题。
    3. F1 (截图)、F3 (贴图) 原生常驻；Space 和 Esc 仅在滚动截图期间动态启用，
       绝不影响用户在日常使用、游戏、IDE 中的正常按键（特别是 Esc 键）。
    4. 若原生 API 注册冲突，自动降级为 keyboard 库作为兜底。
    """
    on_hotkey_pressed = pyqtSignal()  # F1: 截图
    on_ocr_pressed = pyqtSignal()     # F2: 文字识别与翻译
    on_record_pressed = pyqtSignal()  # F4: 录屏
    on_stop_scroll = pyqtSignal()     # Esc: 取消滚动截图
    on_pin_pressed = pyqtSignal()     # F3: 贴图
    on_space_pressed = pyqtSignal()   # Space: 滚动截图开始/停止

    HOTKEY_F1_ID = 101
    HOTKEY_F3_ID = 102
    HOTKEY_SPACE_ID = 103
    HOTKEY_ESC_ID = 104
    HOTKEY_F4_ID = 105
    HOTKEY_F2_ID = 106

    def __init__(self, parent=None):
        super().__init__(parent)
        self._user32 = ctypes.windll.user32
        self._filter = None
        self._registered_native_ids = set()
        self._space_registered = False
        self._esc_registered = False

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

        self._register_one_hotkey(self.HOTKEY_F1_ID, hk_snip, self.trigger_snip)
        self._register_one_hotkey(self.HOTKEY_F2_ID, hk_ocr, self.trigger_ocr)
        self._register_one_hotkey(self.HOTKEY_F3_ID, hk_pin, self.trigger_pin)
        self._register_one_hotkey(self.HOTKEY_F4_ID, hk_rec, self.trigger_record)

    def reload_hotkeys(self):
        for hid in list(self._registered_native_ids):
            try:
                self._user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self._registered_native_ids.clear()
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self._register_static_hotkeys()

    def register_space_hotkey(self):
        """滚动截图期间动态启用 Space 与 Esc 热键"""
        if self._space_registered:
            return
        MOD_NOREPEAT = 0x4000
        VK_SPACE = 0x20
        VK_ESCAPE = 0x1B

        if self._user32.RegisterHotKey(None, self.HOTKEY_SPACE_ID, MOD_NOREPEAT, VK_SPACE):
            self._registered_native_ids.add(self.HOTKEY_SPACE_ID)
        else:
            try:
                keyboard.add_hotkey('space', self.trigger_space, suppress=True)
            except Exception:
                pass
        self._space_registered = True

        if not self._esc_registered:
            if self._user32.RegisterHotKey(None, self.HOTKEY_ESC_ID, MOD_NOREPEAT, VK_ESCAPE):
                self._registered_native_ids.add(self.HOTKEY_ESC_ID)
            else:
                try:
                    keyboard.add_hotkey('esc', self.trigger_stop, suppress=True)
                except Exception:
                    pass
            self._esc_registered = True
        logging.info("滚动截图专用热键 (Space/Esc) 已激活。")

    def unregister_space_hotkey(self):
        """退出滚动截图时注销 Space 与 Esc 热键，归还按键控制权"""
        if self.HOTKEY_SPACE_ID in self._registered_native_ids:
            self._user32.UnregisterHotKey(None, self.HOTKEY_SPACE_ID)
            self._registered_native_ids.remove(self.HOTKEY_SPACE_ID)
        else:
            try:
                keyboard.remove_hotkey('space')
            except Exception:
                pass
        self._space_registered = False

        if self.HOTKEY_ESC_ID in self._registered_native_ids:
            self._user32.UnregisterHotKey(None, self.HOTKEY_ESC_ID)
            self._registered_native_ids.remove(self.HOTKEY_ESC_ID)
        else:
            try:
                keyboard.remove_hotkey('esc')
            except Exception:
                pass
        self._esc_registered = False
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
        except Exception:
            pass

    def wait(self, msecs=2000):
        # 兼容 QThread.wait 接口
        pass

# 保持类名别名以确保外部或历史引用无缝兼容
HotkeyListener = GlobalHotkeyManager


class ScreenshotTool(QWidget):
    def __init__(self, hotkey_listener=None):
        super().__init__()
        ensure_high_dpi_and_fonts()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        
        self.hotkey_listener = hotkey_listener
        
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
        self.color_pick_pos = None
        self.color_picker_visible = False
        self.color_output_hex = False
        
        self.capture_history = [] 
        self.pinned_windows = [] 
        
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

    def save_state(self):
        state = {
            'edits': clone_edits(self.edits),
            'selection_rect': QRect(self.selection_rect)
        }
        self.undo_stack.append(state)
        self.redo_stack.clear()

    def undo(self):
        if self.undo_stack:
            self.commit_text_edit()
            self.redo_stack.append({
                'edits': clone_edits(self.edits),
                'selection_rect': QRect(self.selection_rect)
            })
            state = self.undo_stack.pop()
            self.edits = state['edits']
            self.selection_rect = state['selection_rect']
            self.update()

    def redo(self):
        if self.redo_stack:
            self.commit_text_edit()
            self.undo_stack.append({
                'edits': clone_edits(self.edits),
                'selection_rect': QRect(self.selection_rect)
            })
            state = self.redo_stack.pop()
            self.edits = state['edits']
            self.selection_rect = state['selection_rect']
            self.update()

    def erase_annotation(self, pos):
        for i in range(len(self.edits)-1, -1, -1):
            if is_annotation_hit(self.edits[i], pos):
                self.save_state()
                self.edits.pop(i)
                return True
        return False

    def setup_toolbar(self):
        self.toolbar = QWidget(self)
        self.toolbar.setObjectName("mainToolbar")
        self.toolbar.setStyleSheet(TOOLBAR_STYLE)
        add_drop_shadow(self.toolbar)
        layout = QHBoxLayout(self.toolbar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 标注组
        self.btn_rect = QPushButton("⬜ 矩形")
        self.btn_ellipse = QPushButton("⭕ 椭圆")
        self.btn_line = QPushButton("📏 直线")
        self.btn_arrow = QPushButton("↗ 箭头: 关")
        self.btn_pen = QPushButton("✏ 涂鸦")
        self.btn_highlighter = QPushButton("🖍 荧光")
        self.btn_step = QPushButton("① 步骤")
        self.btn_text = QPushButton("T 文字")
        self.btn_blur = QPushButton("💧 模糊")
        self.btn_erase = QPushButton("🧹 橡皮")
        self.btn_color = QPushButton("🎨 颜色")

        # 功能组
        self.btn_crop = QPushButton("✂ 裁剪")
        self.btn_ocr = QPushButton("🔤 识别")
        self.btn_scroll = QPushButton("📜 滚动")
        self.btn_record = QPushButton("🎥 录屏")
        self.btn_history = QPushButton("📜 历史")
        self.btn_settings = QPushButton("⚙ 设置")

        # 操作组
        self.btn_pin = QPushButton("📌 贴图")
        self.btn_copy = QPushButton("📋 复制")
        self.btn_save = QPushButton("💾 保存")
        self.btn_cancel = QPushButton("✕ 关闭")

        self.btn_rect.setToolTip("绘制矩形框 (滚轮调整粗细)")
        self.btn_ellipse.setToolTip("绘制椭圆/圆形 (按住 Shift 绘制正圆，滚轮调整粗细)")
        self.btn_line.setToolTip("绘制直线 (滚轮调整粗细)")
        self.btn_arrow.setToolTip("切换末端箭头")
        self.btn_pen.setToolTip("自由画笔涂鸦 (滚轮调整粗细)")
        self.btn_highlighter.setToolTip("荧光笔半透明高亮 (滚轮调整粗细)")
        self.btn_step.setToolTip("序号步骤标记 ①②③ (点击依次递增)")
        self.btn_text.setToolTip("点击添加文字 (支持极小字/任意字号)")
        self.btn_blur.setToolTip("涂抹马赛克模糊 (滚轮调整画笔粗细)")
        self.btn_erase.setToolTip("点击标注元素擦除")
        self.btn_color.setToolTip("选择绘图颜色")
        self.btn_crop.setToolTip("裁剪选区 (确定/取消/比例预设)")
        self.btn_ocr.setToolTip("文字识别与多语言翻译 (快捷键 F2)")
        self.btn_scroll.setToolTip("滚动长截图 (Space 启停)")
        self.btn_record.setToolTip("屏幕录制 (F4) - 录制选区或全屏，导出MP4/GIF")
        self.btn_history.setToolTip("查看与管理历史截图")
        self.btn_settings.setToolTip("全局设置与自定义快捷键")
        self.btn_pin.setToolTip("贴图到桌面置顶 (F3)")
        self.btn_copy.setToolTip("复制截图到剪贴板 (Ctrl+C)")
        self.btn_save.setToolTip("保存图片到文件")
        self.btn_cancel.setToolTip("退出截图 (ESC)")

        self.btn_rect.clicked.connect(lambda: self.set_tool('RECTANGLE', self.btn_rect))
        self.btn_ellipse.clicked.connect(lambda: self.set_tool('ELLIPSE', self.btn_ellipse))
        self.btn_line.clicked.connect(lambda: self.set_tool('LINE', self.btn_line))
        self.btn_arrow.clicked.connect(self.toggle_arrow)
        self.btn_pen.clicked.connect(lambda: self.set_tool('PEN', self.btn_pen))
        self.btn_highlighter.clicked.connect(lambda: self.set_tool('HIGHLIGHTER', self.btn_highlighter))
        self.btn_step.clicked.connect(lambda: self.set_tool('STEP', self.btn_step))
        self.btn_text.clicked.connect(lambda: self.set_tool('TEXT', self.btn_text))
        self.btn_blur.clicked.connect(lambda: self.set_tool('BLUR', self.btn_blur))
        self.btn_erase.clicked.connect(lambda: self.set_tool('ERASE', self.btn_erase))
        self.btn_color.clicked.connect(self.pick_drawing_color)

        self.btn_crop.clicked.connect(lambda: self.set_tool('CROP', self.btn_crop)) 
        self.btn_ocr.clicked.connect(lambda: self.set_tool('OCR', self.btn_ocr))
        self.btn_scroll.clicked.connect(lambda: self.set_tool('SCROLL', self.btn_scroll))
        self.btn_record.clicked.connect(self.activate_recording)
        self.btn_history.clicked.connect(self.show_history_dialog)
        self.btn_settings.clicked.connect(self.show_settings_dialog)
        
        self.btn_pin.clicked.connect(self.pin_image)
        self.btn_copy.clicked.connect(self.copy_image)
        self.btn_save.clicked.connect(self.save_image)
        self.btn_cancel.clicked.connect(lambda: self.hide_app(keep_history=False))

        def add_vsep():
            sep = QFrame()
            sep.setFrameShape(QFrame.VLine)
            sep.setFrameShadow(QFrame.Plain)
            sep.setStyleSheet("color: #d0d7de; margin: 4px 2px;")
            layout.addWidget(sep)

        for btn in [self.btn_rect, self.btn_ellipse, self.btn_line, self.btn_arrow, self.btn_pen, self.btn_highlighter, self.btn_step, self.btn_text, self.btn_blur, self.btn_erase, self.btn_color]:
            btn.setCursor(Qt.PointingHandCursor)
            apply_button_style(btn)
            layout.addWidget(btn)

        add_vsep()

        for btn in [self.btn_crop, self.btn_ocr, self.btn_scroll, self.btn_record, self.btn_history, self.btn_settings]:
            btn.setCursor(Qt.PointingHandCursor)
            apply_button_style(btn)
            layout.addWidget(btn)

        add_vsep()

        for btn in [self.btn_pin, self.btn_copy, self.btn_save, self.btn_cancel]:
            btn.setCursor(Qt.PointingHandCursor)
            apply_button_style(btn, danger=(btn is self.btn_cancel))
            layout.addWidget(btn)

        self._update_color_button()
        self.toolbar.hide()

    def toggle_arrow(self):
        self.arrow_enabled = not self.arrow_enabled
        self.btn_arrow.setText("↗ 箭头: 开" if self.arrow_enabled else "↗ 箭头: 关")

    def pick_drawing_color(self):
        color = QColorDialog.getColor(self.current_color, self, "Pick Drawing Color")
        if color.isValid():
            self.current_color = color
            self._update_color_button()

    def _update_color_button(self):
        self.btn_color.setStyleSheet(
            "background-color: %s; color: %s; border: 2px solid #5C5F66; border-radius: 4px; font-weight: bold; padding: 4px 10px;" % (
                self.current_color.name(), 'white' if self.current_color.lightness() < 128 else 'black'
            )
        )
        self.btn_color.setText("🎨 颜色")

    def activate_capture(self, ocr_intent=False, recording_intent=False):
        import time as _time
        t0 = _time.time()

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
        logging.info("activate_capture() 被调用，开始进入截图模式 (清理耗时 %.0fms)" % ((t1 - t0) * 1000))
        self.state = "SELECTING"
        self.current_tool = None
        self.current_step_num = 1
        self.drag_mode = None
        self.edits = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.ocr_intent = ocr_intent
        self.recording_intent = recording_intent

        self.ocr_rect = None
        self.color_pick_pos = self.mapFromGlobal(QCursor.pos())
        self.color_picker_visible = not (ocr_intent or recording_intent)
        self.color_output_hex = False
        self.selection_rect = QRect()
        self.hovered_window_rect = None
        self.hovered_window_title = ""
        if hasattr(self, 'window_snapper') and self.window_snapper:
            try:
                self.window_snapper.exclude_hwnds = {int(self.winId())}
                self.window_snapper.refresh_window_tree()
            except Exception as e:
                logging.warning("刷新吸附窗口树异常: %s", e)
        self.crop_rect = None
        self.crop_mode = False
        self.crop_ratio = None
        self.crop_drag_handle = None
        self._cached_selection_pixmap = None
        self.toolbar.hide()
        self.inline_editor.hide()
        if hasattr(self, 'crop_toolbar'):
            self.crop_toolbar.hide()
        self.reset_button_styles()

        t2 = _time.time()
        screen = QApplication.primaryScreen()
        self.original_screen = screen.grabWindow(0)
        t3 = _time.time()
        self.original_screen_image = self.original_screen.toImage()
        t4 = _time.time()
        self.setGeometry(0, 0, self.original_screen.width(), self.original_screen.height())
        self.setMouseTracking(True)
        self.showFullScreen()
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
            self.original_screen = None
            self.original_screen_image = None
        finally:
            self.hide()

    def activate_recording(self):
        """激活屏幕录制模式 (F4)"""
        # 1. 若正在录屏中，再次按下快捷键直接完成录制
        if self.recording_thread and self.recording_thread.isRunning():
            self._on_stop_record()
            return

        # 2. 若已有有效选区正在编辑，直接以该选区开启录制
        if self.state == "EDITING" and hasattr(self, 'selection_rect') and not self.selection_rect.isNull() and self.selection_rect.width() > 10:
            rect = QRect(self.selection_rect)
            self.hide_app(keep_history=False)
            self.start_recording_session(rect)
            return

        # 3. 否则进入录屏选区模式 (完全免除深色遮罩)
        self.activate_capture(recording_intent=True)

    def activate_ocr(self):
        """激活快速文字识别与翻译模式 (F2)"""
        # 1. 若已有有效选区正在编辑，直接以该选区开启 OCR
        if self.state == "EDITING" and hasattr(self, 'selection_rect') and not self.selection_rect.isNull() and self.selection_rect.width() > 10:
            rect = QRect(self.selection_rect)
            sub_pixmap = self.original_screen.copy(rect) if (hasattr(self, 'original_screen') and self.original_screen) else None
            self.hide_app(keep_history=False)
            self.start_quick_ocr_session(rect, sub_pixmap)
            return

        # 2. 否则进入快速文字识别选区模式 (完全免除深色遮罩)
        self.activate_capture(ocr_intent=True)

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
            if not text or not text.strip():
                QMessageBox.information(None, "文字识别结果", "未能识别到有效文字，请重新框选。")
                return
            dlg = OcrResultDialog(text, None)
            dlg.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
            self._active_ocr_dialog = dlg
            dlg.activateWindow()
            dlg.raise_()
            dlg.exec_()
        except Exception as e:
            logging.error("_on_quick_ocr_finished 异常: %s", e, exc_info=True)

    def start_recording_session(self, target_rect):
        """开启录屏准备会话（显示红框与悬浮控制条）"""
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
                except Exception:
                    pass
                try:
                    self.recording_thread.error_occurred.disconnect()
                except Exception:
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
            dialog.exec_()
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

    def reset_button_styles(self):
        buttons = [
            self.btn_rect, self.btn_ellipse, self.btn_line, self.btn_pen,
            self.btn_highlighter, self.btn_step, self.btn_text, self.btn_blur,
            self.btn_erase, self.btn_crop, self.btn_scroll, self.btn_ocr
        ]
        if hasattr(self, 'btn_record'):
            buttons.append(self.btn_record)
        for btn in buttons:
            apply_button_style(btn)

    def update_cursor_for_tool(self, pos=None):
        """统一管理工具光标，杜绝光标状态卡在边缘拉伸等异常状态"""
        if self.crop_mode:
            if pos is not None and not self.crop_drag_handle:
                handle = self._get_crop_handle_at(pos)
                cursors = {
                    'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor,
                    'tr': Qt.SizeBDiagCursor, 'bl': Qt.SizeBDiagCursor,
                    't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor,
                    'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
                    'move': Qt.SizeAllCursor,
                }
                self.setCursor(cursors.get(handle, Qt.ArrowCursor))
            return

        if self.current_tool == 'ERASE':
            self.setCursor(create_eraser_cursor())
        elif self.current_tool == 'TEXT':
            self.setCursor(Qt.IBeamCursor)
        elif self.current_tool == 'BLUR':
            r = self.current_thickness * 4
            self.setCursor(create_brush_cursor(r))
        elif self.current_tool == 'STEP':
            self.setCursor(Qt.PointingHandCursor)
        elif self.current_tool in ['RECTANGLE', 'ELLIPSE', 'LINE', 'PEN', 'HIGHLIGHTER', 'OCR']:
            self.setCursor(Qt.CrossCursor)
        elif self.current_tool is None:
            if pos is not None and self.state == "EDITING":
                edge = self.get_resize_edge(pos)
                if edge == 'move':
                    self.setCursor(Qt.SizeAllCursor)
                elif edge in ['topleft', 'bottomright']:
                    self.setCursor(Qt.SizeFDiagCursor)
                elif edge in ['topright', 'bottomleft']:
                    self.setCursor(Qt.SizeBDiagCursor)
                elif edge in ['left', 'right']:
                    self.setCursor(Qt.SizeHorCursor)
                elif edge in ['top', 'bottom']:
                    self.setCursor(Qt.SizeVerCursor)
                else:
                    self.setCursor(Qt.CrossCursor)
            else:
                self.setCursor(Qt.CrossCursor)

    def set_tool(self, tool_name, active_btn=None):
        self.commit_text_edit()
        if self.crop_mode and tool_name != 'CROP':
            self.cancel_crop()
        if self.btn_ocr.text() != "🔤 识别":
            self.btn_ocr.setText("🔤 识别")
        if tool_name is None:
            self.current_tool = None
            self.reset_button_styles()
            self.update_cursor_for_tool()
            return
        if self.current_tool == tool_name:
            self.current_tool = None
            self.reset_button_styles()
            self.update_cursor_for_tool()
            return

        self.current_tool = tool_name
        self.reset_button_styles()
        if active_btn:
            apply_button_style(active_btn, active=True)
        self.update_cursor_for_tool()
        if tool_name == 'CROP':
            self.enter_crop_mode()
        elif tool_name == 'SCROLL':
            self.start_scroll()
        elif self.crop_mode:
            self.exit_crop_mode()

    def get_resize_edge(self, pos):
        r = self.selection_rect
        margin = 10; x, y = pos.x(), pos.y()
        left, right = abs(x - r.left()) < margin, abs(x - r.right()) < margin
        top, bottom = abs(y - r.top()) < margin, abs(y - r.bottom()) < margin

        if left and top: return 'topleft'
        if right and top: return 'topright'
        if left and bottom: return 'bottomleft'
        if right and bottom: return 'bottomright'
        if left: return 'left'
        if right: return 'right'
        if top: return 'top'
        if bottom: return 'bottom'
        if r.contains(pos): return 'move'
        return None

    def commit_text_edit(self):
        if not self.inline_editor.isVisible(): return
        text = self.inline_editor.text_edit.toPlainText()
        if text.strip() or self.inline_editor.active_edit_ref:
            self.save_state() 
            rect = self.inline_editor.get_text_rect()
            if text.strip():
                self.edits.append({
                    'type': 'TEXT',
                    'rect': rect,
                    'text': text,
                    'color': self.inline_editor.current_color,
                    'font': self.inline_editor.text_edit.font()
                })
        self.inline_editor.hide()
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

        painter.setPen(QPen(QColor(0, 120, 255), 2))
        cx, cy = zoom_rect.center().x(), zoom_rect.center().y()
        painter.drawLine(cx, zoom_rect.top(), cx, zoom_rect.bottom())
        painter.drawLine(zoom_rect.left(), cy, zoom_rect.right(), cy)

        painter.fillRect(info_rect, QColor(35, 38, 43, 235))
        painter.setPen(QPen(Qt.white))
        painter.setFont(QFont("Microsoft YaHei", 9))
        value_label = "HEX" if self.color_output_hex else "RGB"
        painter.drawText(info_rect.adjusted(8, 6, -6, -6), Qt.AlignLeft | Qt.AlignTop,
                         f"坐标: {pos.x()}, {pos.y()}\n{value_label}: {self.format_color_value(color)}\nC 复制颜色值\nShift 切换 RGB/HEX")

    def wheelEvent(self, event):
        if self.state == "EDITING" and self.current_tool in ['LINE', 'RECTANGLE', 'BLUR', 'ELLIPSE', 'PEN', 'HIGHLIGHTER', 'STEP'] and not self.crop_mode:
            delta = event.angleDelta().y()
            if delta > 0:
                self.current_thickness = min(20, self.current_thickness + 1)
            else:
                self.current_thickness = max(1, self.current_thickness - 1)
            if self.edits and self.edits[-1].get('temp'):
                self.edits[-1]['thickness'] = self.current_thickness
            self.update_cursor_for_tool(event.pos())
            self.update()

    def keyPressEvent(self, event):
        if getattr(self, 'ocr_intent', False):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self.ocr_intent = False
                rect = self.selection_rect if (not self.selection_rect.isNull() and self.selection_rect.width() > 10) else QApplication.primaryScreen().geometry()
                sub_pixmap = self.original_screen.copy(rect) if (hasattr(self, 'original_screen') and self.original_screen) else None
                self.hide_app(keep_history=False)
                self.start_quick_ocr_session(rect, sub_pixmap)
                return
            elif event.key() == Qt.Key_Escape:
                self.ocr_intent = False
                self.hide_app(keep_history=False)
                return

        if getattr(self, 'recording_intent', False):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self.recording_intent = False
                rect = self.selection_rect if (not self.selection_rect.isNull() and self.selection_rect.width() > 10) else QApplication.primaryScreen().geometry()
                self.hide_app(keep_history=False)
                self.start_recording_session(rect)
                return
            elif event.key() == Qt.Key_Escape:
                self.recording_intent = False
                self.hide_app(keep_history=False)
                return

        if event.key() == Qt.Key_F2:
            self.activate_ocr()
            return

        if event.key() == Qt.Key_Space and (self.scroll_thread or getattr(self, 'scroll_waiting', False)):
            self.toggle_auto_scroll_capture()
            return
        # 裁剪模式：Enter 应用，ESC 取消
        if self.crop_mode:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.apply_crop()
                return
            elif event.key() == Qt.Key_Escape:
                self.cancel_crop()
                return
        if self.state == "SELECTING":
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if getattr(self, 'hovered_window_rect', None) and not self.hovered_window_rect.isNull():
                    self.selection_rect = QRect(self.hovered_window_rect)
                    self.hovered_window_rect = None
                    self.state = "EDITING"
                    self.drag_mode = None
                    self.setFocus()
                    self.show_toolbar()
                    self.update()
                    return
            if self.color_picker_visible:
                if event.key() == Qt.Key_C:
                    self.copy_color_value()
                    return
                if event.key() == Qt.Key_Shift:
                    self.toggle_color_format()
                    return
        if event.modifiers() & Qt.ControlModifier:
            if event.key() == Qt.Key_C:
                if self.state == "EDITING":
                    self.copy_image()
            elif event.key() == Qt.Key_1:
                if self.state == "EDITING":
                    self.set_tool('RECTANGLE', self.btn_rect)
            elif event.key() == Qt.Key_Z:
                if self.state == "EDITING":
                    self.undo()
            elif event.key() == Qt.Key_Y:
                if self.state == "EDITING":
                    self.redo()
        elif event.key() == Qt.Key_Escape: 
            if self.current_tool is not None:
                self.set_tool(None)
                self.show_toolbar()
                return
            elif self.crop_mode:
                self.cancel_crop()
                self.show_toolbar()
                return
            self.hide_app(keep_history=False)

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

                    border_color = QColor(0, 174, 255)
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

            # 3. 顶部指引胶囊条
            if is_ocr and self.state == "SELECTING":
                banner_rect = QRect(self.width() // 2 - 270, 20, 540, 38)
                painter.setPen(QPen(QColor(9, 105, 218), 1.5))
                painter.setBrush(QBrush(QColor(255, 255, 255, 245)))
                painter.drawRoundedRect(banner_rect, 9, 9)
                painter.setPen(QPen(QColor(36, 41, 47)))
                painter.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
                painter.drawText(banner_rect, Qt.AlignCenter, "🔤 识图翻译：拖选识别区域 | 双击或回车全屏识别 | ESC 取消")
                painter.setBrush(Qt.NoBrush)

            elif is_rec and self.state == "SELECTING":
                banner_rect = QRect(self.width() // 2 - 270, 20, 540, 38)
                painter.setPen(QPen(QColor(239, 68, 68), 1.5))
                painter.setBrush(QBrush(QColor(255, 255, 255, 245)))
                painter.drawRoundedRect(banner_rect, 9, 9)
                painter.setPen(QPen(QColor(36, 41, 47)))
                painter.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
                painter.drawText(banner_rect, Qt.AlignCenter, "🎥 屏幕录制：拖选录制区域 | 双击或回车全屏录制 | ESC 取消")
                painter.setBrush(Qt.NoBrush)

            # 4. 选区渲染：选区内部 100% 原屏通透（零遮罩、零底色），配高精边框与取景器角标
            if not self.selection_rect.isNull():
                if is_ocr:
                    border_color = QColor(9, 105, 218)
                    badge_text = f"🔤 识别区域: {self.selection_rect.width()} × {self.selection_rect.height()}"
                elif is_rec:
                    border_color = QColor(239, 68, 68)
                    fps_label = "60FPS" if "60" in getattr(self, 'recording_quality_key', '') else "30FPS"
                    badge_text = f"🎥 录屏选区: {self.selection_rect.width()} × {self.selection_rect.height()} ({fps_label})"
                else:
                    border_color = QColor(0, 174, 255)
                    badge_text = f"{self.selection_rect.width()} × {self.selection_rect.height()}"

                # 边框线（选区内部绝对不加遮罩，完全透明高亮）
                painter.setPen(QPen(border_color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self.selection_rect)

                # 四角相机取景器角标
                draw_viewfinder_corners(painter, self.selection_rect, border_color, length=14, width=3)

                # 悬浮尺寸徽标
                draw_selection_badge(painter, self.selection_rect, badge_text, border_color=border_color)

                # 编辑状态下的标注图元绘制
                if self.state == "EDITING":
                    painter.setBrush(Qt.NoBrush)
                    painter.setClipRect(self.selection_rect)
                    draw_all_edits(painter, self.edits, self.original_screen, QPoint(0, 0))

                    if self.current_tool == 'TEXT' and getattr(self, 'drag_mode', None) == 'text_draw':
                        painter.setPen(QPen(Qt.black, 1, Qt.DashLine))
                        painter.drawRect(self.text_rect)
                        painter.setPen(QPen(Qt.white, 1, Qt.DashLine))
                        painter.drawRect(self.text_rect.adjusted(1, 1, -1, -1))

                    painter.setClipping(False)

                    if self.crop_mode and getattr(self, 'crop_rect', None):
                        crop_outer = QRegion(self.selection_rect).subtracted(QRegion(self.crop_rect))
                        for r in crop_outer.rects():
                            painter.fillRect(r, QColor(0, 0, 0, 150))

                        painter.setClipRect(self.crop_rect)
                        draw_all_edits(painter, self.edits, self.original_screen, QPoint(0, 0))
                        painter.setClipping(False)

                        # 三分线
                        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
                        for i in range(1, 3):
                            x = self.crop_rect.left() + self.crop_rect.width() * i // 3
                            painter.drawLine(x, self.crop_rect.top(), x, self.crop_rect.bottom())
                            y = self.crop_rect.top() + self.crop_rect.height() * i // 3
                            painter.drawLine(self.crop_rect.left(), y, self.crop_rect.right(), y)

                        # 裁剪框边线与 8 个手柄
                        painter.setPen(QPen(QColor(0, 174, 255), 2))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(self.crop_rect)

                        handle = CROP_HANDLE_SIZE
                        painter.setBrush(QBrush(QColor(0, 174, 255)))
                        painter.setPen(QPen(Qt.white, 1))
                        corners = [self.crop_rect.topLeft(), self.crop_rect.topRight(),
                                   self.crop_rect.bottomLeft(), self.crop_rect.bottomRight()]
                        edges = [QPoint(self.crop_rect.center().x(), self.crop_rect.top()),
                                 QPoint(self.crop_rect.center().x(), self.crop_rect.bottom()),
                                 QPoint(self.crop_rect.left(), self.crop_rect.center().y()),
                                 QPoint(self.crop_rect.right(), self.crop_rect.center().y())]
                        for pt in corners + edges:
                            painter.drawRect(pt.x() - handle // 2, pt.y() - handle // 2, handle, handle)
                        painter.setBrush(Qt.NoBrush)

                    if self.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                        painter.setPen(QPen(Qt.green, 2, Qt.DashLine))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(self.ocr_rect)

            self.draw_color_picker(painter)
        except Exception as e:
            logging.error("ScreenshotTool paintEvent 绘制异常: %s", e, exc_info=True)
        finally:
            if painter.isActive():
                painter.end()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton: return
        pos = event.pos()
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
                self.drag_mode = None
                self.setFocus()
                self.show_toolbar()
                self.update()
                return

        # 裁剪模式下双击裁剪框内应用裁剪
        if self.crop_mode and self.crop_rect and self.crop_rect.contains(pos):
            self.apply_crop()
            return

        if self.state == "EDITING" and self.current_tool == 'TEXT':
            for edit in reversed(self.edits):
                if edit['type'] == 'TEXT' and 'rect' in edit and edit['rect'].contains(pos):
                    self.save_state()
                    self.edits.remove(edit)
                    self.inline_editor.start_editing(edit['rect'], edit)
                    self.drag_mode = None
                    self.update()
                    break

    def mousePressEvent(self, event):
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
            elif self.state in ["EDITING", "SELECTING"]:
                self.hide_app(keep_history=False)
                return
        if event.button() != Qt.LeftButton: return
        pos = event.pos()

        if self.state == "SELECTING":
            self.color_picker_visible = False
            self.color_timer.stop()
            self.begin_point = pos
            self.press_pos = pos
            self.selection_rect = QRect()
            self.toolbar.hide()
            
        elif self.state == "EDITING":
            self.color_picker_visible = False
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

            if self.current_tool == 'ERASE':
                if self.erase_annotation(pos): self.update()
            elif self.current_tool == 'CROP':
                handle = self._get_crop_handle_at(pos)
                if handle:
                    self.crop_drag_handle = handle
                    self.crop_drag_start_pos = pos
                    self.crop_drag_start_rect = QRect(self.crop_rect)
            elif self.current_tool == 'OCR':
                self.drag_mode = 'ocr'; self.ocr_start = pos; self.ocr_rect = QRect(pos, pos)
            elif self.current_tool == 'TEXT':
                clicked_edit = None
                for edit in reversed(self.edits):
                    if edit['type'] == 'TEXT' and 'rect' in edit and edit['rect'].contains(pos):
                        clicked_edit = edit; break
                
                if clicked_edit:
                    self.save_state()
                    self.drag_mode = 'text_move'
                    self.active_text_edit = clicked_edit
                    self.drag_offset = pos - clicked_edit['rect'].topLeft()
                else:
                    self.drag_mode = 'text_draw'
                    self.text_start = pos
                    self.text_rect = QRect(pos, pos)
                    # 单击即可直接在点击位置激活输入框，无需强制拖拽大框
                    self.inline_editor.start_editing(pos)
            elif self.current_tool == 'STEP':
                self.save_state()
                step_val = getattr(self, 'current_step_num', 1)
                thick = getattr(self, 'current_thickness', 3)
                self.edits.append({
                    'type': 'STEP',
                    'pos': pos,
                    'center': pos,
                    'num': step_val,
                    'number': step_val,
                    'color': self.current_color,
                    'thickness': thick,
                    'radius': max(11, 8 + thick * 2),
                    'temp': False
                })
                self.current_step_num = step_val + 1
                self.update()
                self.show_toolbar()
                return
            elif self.current_tool == 'ELLIPSE':
                self.save_state()
                self.edit_start = pos
                self.edits.append({
                    'type': 'ELLIPSE',
                    'rect': QRect(pos, pos),
                    'color': self.current_color,
                    'thickness': self.current_thickness,
                    'temp': True
                })
            elif self.current_tool == 'PEN':
                self.save_state()
                self.edits.append({
                    'type': 'PEN',
                    'points': [pos],
                    'color': self.current_color,
                    'thickness': self.current_thickness,
                    'temp': True
                })
            elif self.current_tool == 'HIGHLIGHTER':
                self.save_state()
                self.edits.append({
                    'type': 'HIGHLIGHTER',
                    'points': [pos],
                    'color': self.current_color,
                    'thickness': max(12, self.current_thickness * 3),
                    'temp': True
                })
            elif self.current_tool == 'BLUR':
                self.save_state()
                self.edits.append({
                    'type': 'BLUR_STROKE',
                    'points': [pos],
                    'thickness': self.current_thickness,
                    'temp': True
                })
            elif self.current_tool in ['RECTANGLE', 'LINE']:
                self.save_state()
                self.edit_start = pos
                self.edits.append({'type': self.current_tool, 'rect': QRect(pos, pos), 'start': pos, 'end': pos, 'color': self.current_color, 'arrow': self.arrow_enabled, 'temp': True, 'thickness': self.current_thickness})
            
            elif self.current_tool is None:
                edge = self.get_resize_edge(pos)
                self.drag_mode = edge
                if self.drag_mode == 'move': self.drag_offset = pos - self.selection_rect.topLeft()
                elif self.drag_mode is None:
                    if self.geometry().width() == QApplication.primaryScreen().geometry().width():
                        self.state = "SELECTING"; self.begin_point = pos; self.selection_rect = QRect(); self.current_step_num = 1; self.edits.clear(); self.undo_stack.clear(); self.redo_stack.clear(); self.toolbar.hide(); self.update()

    def mouseMoveEvent(self, event):
        pos = event.pos()
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
                if self.selection_rect.contains(pos) and self.current_tool is None:
                    self.color_pick_pos = pos
                    self.color_picker_visible = True
                else:
                    self.color_picker_visible = False
                
                self.update_cursor_for_tool(pos)
                return

            if not event.buttons() & Qt.LeftButton: return

            if self.current_tool == 'TEXT' and self.drag_mode == 'text_move':
                new_top_left = pos - self.drag_offset
                self.active_text_edit['rect'].moveTo(new_top_left)
                self.update()
                
            elif self.current_tool == 'OCR' and self.drag_mode == 'ocr':
                rect = QRect(self.ocr_start, pos).normalized()
                self.ocr_rect = rect.intersected(self.selection_rect)
                self.update()
            elif self.current_tool == 'TEXT' and self.drag_mode == 'text_draw':
                self.text_rect = QRect(self.text_start, pos).normalized()
                self.update()
            elif self.current_tool in ['BLUR', 'PEN', 'HIGHLIGHTER'] and len(self.edits) > 0 and self.edits[-1].get('temp'):
                self.edits[-1]['points'].append(pos)
                self.update()
            elif self.current_tool == 'ELLIPSE' and len(self.edits) > 0 and self.edits[-1].get('temp'):
                r = QRect(self.edit_start, pos).normalized()
                if QApplication.keyboardModifiers() & Qt.ShiftModifier:
                    side = min(r.width(), r.height())
                    r = QRect(r.topLeft(), QSize(side, side))
                self.edits[-1]['rect'] = r
                self.update()
            elif self.current_tool in ['RECTANGLE', 'LINE'] and len(self.edits) > 0 and self.edits[-1].get('temp'):
                self.edits[-1]['rect'] = QRect(self.edit_start, pos).normalized()
                self.edits[-1]['end'] = pos
                self.update()
            elif self.drag_mode and self.drag_mode not in ['ocr', 'text_draw', 'text_move']:
                r = self.selection_rect
                if self.drag_mode == 'move': r.moveTo(pos - self.drag_offset)
                else:
                    if 'left' in self.drag_mode: r.setLeft(pos.x())
                    if 'right' in self.drag_mode: r.setRight(pos.x())
                    if 'top' in self.drag_mode: r.setTop(pos.y())
                    if 'bottom' in self.drag_mode: r.setBottom(pos.y())
                self.selection_rect = r.normalized()
                self.toolbar.hide()
                self.update()

    def mouseReleaseEvent(self, event):
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
                    rect = QRect(self.selection_rect)
                    self.hide_app(keep_history=False)
                    self.start_recording_session(rect)
                    return
                self.state = "EDITING"
                self.drag_mode = None
                self.setFocus()
                self.show_toolbar()
                self.color_timer.start()

            elif self.state == "EDITING":
                if self.crop_mode:
                    self.crop_drag_handle = None
                    self._update_crop_toolbar_pos()
                    return

                if self.drag_mode:
                    if self.current_tool == 'TEXT' and self.drag_mode == 'text_move':
                        self.drag_mode = None
                        return

                    if self.current_tool == 'OCR' and getattr(self, 'ocr_rect', None):
                        if self.ocr_rect.width() > 10 and self.ocr_rect.height() > 10:
                            sub_pixmap = self.original_screen.copy(self.ocr_rect)
                            self.btn_ocr.setText("🔤 识别中...")
                            ocr_img = sub_pixmap.toImage()
                            self.ocr_worker = OcrWorker(ocr_img, use_ai=load_settings().get("use_ai_ocr", False))
                            self.ocr_worker.finished.connect(self.show_ocr_result)
                            self.ocr_worker.start()

                        self.ocr_rect = None
                        self.update()

                    elif self.current_tool == 'TEXT' and self.drag_mode == 'text_draw':
                        if self.text_rect.width() > 10 and self.text_rect.height() > 10:
                            self.inline_editor.start_editing(self.text_rect)

                    self.drag_mode = None
                    self.setFocus()
                    if self.current_tool not in ['RECTANGLE', 'LINE', 'BLUR', 'ELLIPSE', 'PEN', 'HIGHLIGHTER', 'STEP']:
                        self.show_toolbar()

                elif self.current_tool in ['RECTANGLE', 'LINE', 'BLUR', 'ELLIPSE', 'PEN', 'HIGHLIGHTER', 'STEP']:
                    if len(self.edits) > 0 and self.edits[-1].get('temp'):
                        self.edits[-1]['temp'] = False
                    self.show_toolbar()
                else:
                    self.show_toolbar()

    def show_ocr_result(self, text):
        self.btn_ocr.setText("🔤 识别")
        if not text:
            QMessageBox.information(self, "OCR 结果", "未能识别到文字，请重试。")
        else:
            dlg = OcrResultDialog(text, self)
            dlg.exec_() 
            
    def show_toolbar(self):
        if self.crop_mode:
            return
        screen_rect = QApplication.primaryScreen().geometry()
        self.toolbar.adjustSize()
        toolbar_y = self.selection_rect.bottom() + 10
        if toolbar_y + self.toolbar.height() > screen_rect.height():
            toolbar_y = self.selection_rect.top() - self.toolbar.height() - 10
        toolbar_y = max(0, min(toolbar_y, screen_rect.height() - self.toolbar.height()))
        toolbar_x = max(0, min(self.selection_rect.left(), screen_rect.width() - self.toolbar.width()))
        self.toolbar.move(toolbar_x, toolbar_y)
        self.toolbar.show()
        self.toolbar.raise_()

    def enter_crop_mode(self):
        """进入裁剪模式，在选区内显示默认裁剪框与控制栏"""
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
        self.selection_rect = QRect(self.crop_rect)
        self.crop_mode = False
        self.crop_rect = None
        self.crop_drag_handle = None
        if hasattr(self, 'crop_toolbar'):
            self.crop_toolbar.hide()
        self.set_tool(None, self.btn_crop)
        self.show_toolbar()
        self.update()

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

    def _update_crop_rect(self, pos):
        """根据拖动手柄更新裁剪框（约束在 selection_rect 内）"""
        start = self.crop_drag_start_rect
        h = self.crop_drag_handle
        bounds = self.selection_rect
        new_rect = QRect(start)

        if h == 'move':
            delta = pos - self.crop_drag_start_pos
            new_rect = start.translated(delta)
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
                    if h == 'b':
                        new_h = max(CROP_MIN_SIZE, pos.y() - start.top())
                    else:
                        new_h = max(CROP_MIN_SIZE, start.bottom() - pos.y())
                    new_w = int(new_h * target_ratio)
                    new_x = cx - new_w // 2
                    new_y = start.bottom() - new_h if h == 't' else start.top()
                    new_rect = QRect(new_x, new_y, new_w, new_h)
                elif h in ('l', 'r'):
                    cy = start.center().y()
                    if h == 'r':
                        new_w = max(CROP_MIN_SIZE, pos.x() - start.left())
                    else:
                        new_w = max(CROP_MIN_SIZE, start.right() - pos.x())
                    new_h = int(new_w / target_ratio)
                    new_x = start.right() - new_w if h == 'l' else start.left()
                    new_y = cy - new_h // 2
                    new_rect = QRect(new_x, new_y, new_w, new_h)

        # 约束在 selection_rect 内
        new_rect = new_rect.intersected(bounds)
        if new_rect.width() >= CROP_MIN_SIZE and new_rect.height() >= CROP_MIN_SIZE:
            self.crop_rect = new_rect

    def get_final_static_image(self):
        base_image = self.original_screen.copy(self.selection_rect)
        painter = QPainter(base_image)
        draw_all_edits(painter, self.edits, self.original_screen, self.selection_rect.topLeft())
        painter.end()
        return base_image

    def add_to_capture_history(self, pixmap, pos=None, is_pinned=False, pin_window=None, zoom_factor=1.0):
        """统一管理截图与贴图历史，支持去重和顺序维护 (LIFO 栈顶在末尾)"""
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

    def create_pinned_window(self, pixmap, initial_pos=None, zoom_factor=1.0):
        """创建贴图窗口并注册到历史与活跃贴图列表"""
        if pixmap is None or pixmap.isNull():
            return None
        try:
            new_pin = PinnedWindow(pixmap, main_app_ref=self, initial_pos=initial_pos, zoom_factor=zoom_factor)
            self.pinned_windows.append(new_pin)
            self.add_to_capture_history(pixmap, pos=new_pin.pos(), is_pinned=True, pin_window=new_pin, zoom_factor=new_pin.zoom_factor)
            return new_pin
        except Exception as e:
            logging.error("创建贴图窗口失败: %s", e, exc_info=True)
            return None

    def on_pin_closed(self, pin_window):
        """当贴图被关闭时触发：将贴图标记为未激活，并将其更新至历史栈顶 (LIFO)，以便下次按 F3 优先重新贴出"""
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
        pixmap = self.get_final_static_image()
        pos = self.selection_rect.topLeft()
        self.create_pinned_window(pixmap, initial_pos=pos, zoom_factor=1.0)
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
        if self.isVisible() and self.state == "EDITING":
            self.pin_image()
            return

        # 1. 清理已销毁或已隐藏的贴图引用
        alive_pinned = []
        for pw in self.pinned_windows:
            try:
                if pw.isVisible():
                    alive_pinned.append(pw)
            except (RuntimeError, Exception):
                pass
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
                except (RuntimeError, Exception):
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
        pixmap = self.get_final_static_image()
        pos = self.selection_rect.topLeft()
        ok = save_image_dialog(self, pixmap, default_prefix="screenshot")
        if not ok:
            return
        self.add_to_capture_history(pixmap, pos=pos, is_pinned=False)
        self.hide_app(keep_history=False)

    def show_history_dialog(self):
        try:
            dlg = HistoryDialog(self)
            dlg.exec_()
        except Exception as e:
            logging.error("打开历史窗口失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "截图历史", f"无法打开截图历史:\n{e}")

    def show_settings_dialog(self):
        try:
            dlg = SettingsDialog(self, hotkey_mgr=getattr(self, 'hotkey_listener', None))
            if dlg.exec_() == QDialog.Accepted:
                if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
                    self.hotkey_listener.reload_hotkeys()
        except Exception as e:
            logging.error("打开设置窗口失败: %s", e, exc_info=True)
            QMessageBox.warning(self, "系统设置", f"无法打开设置窗口:\n{e}")

    def start_scroll(self):
        scroll_rect = QRect(self.selection_rect)
        if scroll_rect.width() < SCROLL_MIN_CAPTURE_SIZE or scroll_rect.height() < SCROLL_MIN_CAPTURE_SIZE:
            QMessageBox.information(self, "Scroll Capture", "Please select a larger area before starting scroll capture.")
            self.set_tool(None, self.btn_scroll)
            return

        clear_scroll_temp_folder(TEMP_FOLDER)
        self.active_scroll_rect = scroll_rect
        self.scroll_waiting = True
        self._scroll_cancelled = False
        self.hide_app(keep_history=False)
        QApplication.processEvents()
        time.sleep(0.12)

        self.border_frame = CaptureFrame(scroll_rect)
        if self.hotkey_listener is not None:
            self.hotkey_listener.register_space_hotkey()

        # 提示条设为无父级的全局 ToolTip 置顶窗口，彻底解决因父窗口 hide_app 导致跟随隐藏的严重 Bug
        if hasattr(self, '_scroll_hint') and self._scroll_hint:
            try:
                self._scroll_hint.close()
            except: pass

        hint = QLabel("Space: 开始/停止滚动  |  ESC: 取消")
        hint.setWindowFlags(Qt.ToolTip | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        hint.setAttribute(Qt.WA_ShowWithoutActivating)
        hint.setStyleSheet("background: rgba(20,20,20,225); color: #00AEFF; padding: 7px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; border: 1px solid rgba(0,174,255,100);")
        hint.adjustSize()
        hint_x = scroll_rect.left() + max(0, (scroll_rect.width() - hint.width()) // 2)
        hint_y = max(0, scroll_rect.top() - hint.height() - 8)
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
        if self.scroll_thread and self.scroll_thread.is_recording:
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
            preview = ImageEditorDialog(base_img, self)
            preview.exec_()
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
            painter.setBrush(QColor(0, 174, 255))
            painter.setPen(QPen(Qt.white, 2))
            painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
            painter.end()
            self.setIcon(QIcon(pixmap))
        
        self.setToolTip("Murioki Capture (F1 截图 | F2 识图翻译 | F4 录屏 | F3 贴图)")
        
        menu = QMenu()
        snip_action = QAction("✂ 开始截图 (F1)", menu)
        snip_action.triggered.connect(self.main_app.activate_capture)
        menu.addAction(snip_action)
        ocr_action = QAction("🔤 识图翻译 (F2)", menu)
        ocr_action.triggered.connect(self.main_app.activate_ocr)
        menu.addAction(ocr_action)
        rec_action = QAction("🎥 屏幕录制 (F4)", menu)
        rec_action.triggered.connect(self.main_app.activate_recording)
        menu.addAction(rec_action)
        pin_action = QAction("📌 贴图置顶 (F3)", menu)
        pin_action.triggered.connect(self.main_app.hotkey_pin)
        menu.addAction(pin_action)
        menu.addSeparator()
        history_action = QAction("📜 截图历史 (History)", menu)
        history_action.triggered.connect(self.main_app.show_history_dialog)
        menu.addAction(history_action)
        settings_action = QAction("⚙ 系统设置 (Settings)", menu)
        settings_action.triggered.connect(self.main_app.show_settings_dialog)
        menu.addAction(settings_action)
        menu.addSeparator() 
        quit_action = QAction("❌ 退出程序", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        
        self.setContextMenu(menu)
        self.activated.connect(self.on_tray_activated)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick: self.main_app.activate_capture()

    def quit_app(self):
        try:
            clean_orphan_temp_files()
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
        except Exception:
            pass
        QApplication.quit()


if __name__ == '__main__':
    app = None
    try:
        if hasattr(Qt, 'AA_EnableHighDpiScaling'): QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        if hasattr(Qt, 'AA_UseHighDpiPixmaps'): QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
            QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

        # 设置 Windows 进程 AppUserModelID，确保任务栏独立显示专属图标
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("murioki.capture.tool.1.0")
        except Exception:
            pass

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        # 全局超清现代系统字体注入（Microsoft YaHei UI / Segoe UI，开启抗锯齿与亚像素 ClearType 微调）
        global_font = QFont("Microsoft YaHei UI", 9)
        global_font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
        global_font.setHintingPreference(QFont.PreferFullHinting)
        app.setFont(global_font)

        icon_path = get_resource_path("app_icon.ico")
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))

        shared_memory = QSharedMemory("MuriokiScreenshotTool_SingleInstance")
        if shared_memory.attach():
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Information)
            msg.setWindowTitle("Already Running")
            msg.setText("The screenshot tool is already running in the background.\n\nPress F1 to capture, F2 for OCR/Translate, F4 to record, F3 to pin!")
            msg.exec_()
            sys.exit(0)
        shared_memory.create(1)

        if check_for_snipaste():
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Conflict Detected. Please close Snipaste.")
            msg.exec_()

        try:
            clean_orphan_temp_files()
        except Exception:
            pass

        hotkey_mgr = GlobalHotkeyManager()
        hotkey_mgr.install(app)

        ex = ScreenshotTool(hotkey_listener=hotkey_mgr)
        tray = AppTrayIcon(ex, hotkey_listener=hotkey_mgr)
        tray.show()

        hotkey_mgr.on_hotkey_pressed.connect(ex.activate_capture)
        hotkey_mgr.on_ocr_pressed.connect(ex.activate_ocr)
        hotkey_mgr.on_record_pressed.connect(ex.activate_recording)
        hotkey_mgr.on_stop_scroll.connect(ex.stop_trigger)
        hotkey_mgr.on_pin_pressed.connect(ex.hotkey_pin)
        hotkey_mgr.on_space_pressed.connect(ex.toggle_auto_scroll_capture)

        sys.exit(app.exec_())
    except Exception as e:
        logging.exception("程序启动失败")
        if app is not None:
            QMessageBox.critical(None, "Startup Error", f"The screenshot tool failed to start:\n{e}")
        raise
