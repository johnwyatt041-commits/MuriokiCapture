# -*- coding: utf-8 -*-
"""
Pin Drawer (极简 macOS 风格毛玻璃截图储藏柜)
- 极致纯净极简设计：无顶栏、无任何文字、无冗余按钮，只有一个纯粹的毛玻璃截图卡槽
- 真实高斯模糊毛玻璃 (Real Screen Gaussian Blur + macOS Ice-Glass Tint)：
  不依赖有兼容缺陷的旧版 DWM 黑框 API，采用屏幕背景实时采样 + OpenCV 高斯磨砂 + 冰晶亮色半透明层，
  彻底杜绝黑漆漆底色与四周直角黑框，100% 原生圆角抗锯齿透明！
- Ctrl+1 全局快捷键：彻底开启/关闭抽屉功能（关闭后完全静默，绝不误唤醒）
- 超灵敏边缘感应唤醒：开启状态下光标移入右上角边缘立即滑出，零延迟无需费力对准
- 闪电回缩：离开后 180ms 快速优雅回缩
- 贴图拖入时智能缩小为 100x100 圆角毛玻璃迷你卡片（无任何直角边）：绝不遮挡抽屉，清晰透视插入槽位
- 类似手机桌面图标的互相挤压与随意排序 (Spring Physics 弹簧手感)
- 完整的本地持久化记忆
"""

import os
import sys
import math
import json
import time
import uuid
import ctypes
from ctypes import wintypes
import logging
import tempfile
import cv2
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QApplication, QMenu, QAction, QFileDialog, QMessageBox, QToolTip
)
from PyQt5.QtCore import (
    Qt, QRect, QRectF, QPoint, QPointF, QSize, QEvent, QEventLoop, pyqtProperty,
    QPropertyAnimation, QEasingCurve, QTimer, QMimeData, QUrl
)
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QImage, QImageReader, QFont, QFontDatabase,
    QPainterPath, QCursor, QLinearGradient, QDrag
)

SUPPORTED_DROP_IMAGE_EXTS = {
    '.png', '.jpg', '.jpeg', '.bmp', '.webp', '.gif', '.tif', '.tiff', '.ico', '.jfif'
}

_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_GWL_EXSTYLE = -20
_WS_EX_TOPMOST = 0x00000008
_GW_HWNDPREV = 3
_DWMWA_CLOAKED = 14
_SW_SHOWNOACTIVATE = 4


def _mime_has_candidate_images(mime_data):
    """快速判断拖拽 MIME 数据中是否包含本地图片文件或内存图片（避免拖动中阻塞读盘）。"""
    if mime_data is None:
        return False
    if mime_data.hasUrls():
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            local_path = url.toLocalFile()
            if local_path and os.path.splitext(local_path)[1].lower() in SUPPORTED_DROP_IMAGE_EXTS:
                if os.path.isfile(local_path):
                    return True
    if mime_data.hasImage():
        return True
    return False


def _load_pixmap_from_path(path):
    """安全加载本地图片并自动应用 EXIF 方向校正，返回有效 QPixmap 或 None。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        image = reader.read()
        if not image.isNull():
            pix = QPixmap.fromImage(image)
            if not pix.isNull():
                return pix
    except Exception as e:
        logging.debug("QImageReader 读取图片失败 (%s): %s", path, e)
    try:
        pix = QPixmap(path)
        if not pix.isNull():
            return pix
    except Exception as e:
        logging.debug("QPixmap 直接加载图片失败 (%s): %s", path, e)
    return None


def _extract_pixmaps_from_mimedata(mime_data):
    """从拖拽释放的 MIME 数据中解码所有有效图片为 QPixmap 列表。"""
    pixmaps = []
    if mime_data is None:
        return pixmaps
    if mime_data.hasUrls():
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            local_path = url.toLocalFile()
            if not local_path or not os.path.isfile(local_path):
                continue
            if os.path.splitext(local_path)[1].lower() not in SUPPORTED_DROP_IMAGE_EXTS:
                continue
            pix = _load_pixmap_from_path(local_path)
            if pix is not None and not pix.isNull():
                pixmaps.append(pix)
    if not pixmaps and mime_data.hasImage():
        img_data = mime_data.imageData()
        pix = None
        if isinstance(img_data, QPixmap):
            pix = img_data
        elif isinstance(img_data, QImage):
            pix = QPixmap.fromImage(img_data)
        if pix is not None and not pix.isNull():
            pixmaps.append(pix)
    return pixmaps

_DRAWER_FONT_FAMILY = None


def _drawer_ui_font(pt_size, weight=QFont.Normal):
    """返回与主程序一致的现代无衬线 UI 字体（FreeType 无强制网格对齐平滑抗锯齿）。"""
    global _DRAWER_FONT_FAMILY
    if _DRAWER_FONT_FAMILY is None and QApplication.instance():
        families = set(QFontDatabase().families())
        if "Segoe UI Variable Text" in families:
            _DRAWER_FONT_FAMILY = "Segoe UI Variable Text"
            if not QFont.substitutes("Segoe UI Variable Text"):
                cjk = "Microsoft YaHei UI" if "Microsoft YaHei UI" in families else "Microsoft YaHei"
                QFont.insertSubstitutions("Segoe UI Variable Text", ["Segoe UI", cjk, "Segoe UI Emoji"])
        elif "Segoe UI" in families:
            _DRAWER_FONT_FAMILY = "Segoe UI"
        else:
            _DRAWER_FONT_FAMILY = "Microsoft YaHei UI"
    font = QFont(_DRAWER_FONT_FAMILY or "Microsoft YaHei UI", pt_size, weight)
    font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
    font.setHintingPreference(QFont.PreferNoHinting)
    return font


DRAG_FILE_TEMP_DIR = os.path.join(tempfile.gettempdir(), "MuriokiCapture_DragFiles")


def export_pixmap_as_drag_file(pixmap, max_age_sec=21600):
    """将贴图导出为临时 PNG 文件路径，专用于单次系统级文件拖拽，不触碰抽屉持久文件"""
    if pixmap is None or pixmap.isNull():
        return None
    try:
        os.makedirs(DRAG_FILE_TEMP_DIR, exist_ok=True)
    except Exception as e:
        logging.error(f"Failed to create drag temp dir: {e}")
        return None

    # 清理超过 6 小时的陈旧临时文件，避免误删仍在异步上传中的本次/近期拖拽文件
    now = time.time()
    try:
        for name in os.listdir(DRAG_FILE_TEMP_DIR):
            fpath = os.path.join(DRAG_FILE_TEMP_DIR, name)
            try:
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > max_age_sec:
                    os.remove(fpath)
            except Exception:
                pass
    except Exception:
        pass

    filename = f"Murioki_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
    filepath = os.path.join(DRAG_FILE_TEMP_DIR, filename)
    try:
        if pixmap.save(filepath, "PNG") and os.path.isfile(filepath):
            return filepath
    except Exception as e:
        logging.error(f"Failed to export drag PNG file: {e}")
    return None


def _build_file_drag_pixmap(pixmap, card_size=108):
    """生成带 PNG 文件角标的圆角毛玻璃风格拖拽缩略图，提示已切换为文件拖拽"""
    margin = 6
    total = card_size + margin * 2
    canvas = QPixmap(total, total)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        card_rect = QRectF(margin, margin, card_size, card_size)

        # 柔和阴影与白色卡片底座
        shadow_path = QPainterPath()
        shadow_path.addRoundedRect(card_rect.adjusted(-1.5, -1.0, 1.5, 2.5), 14, 14)
        painter.fillPath(shadow_path, QBrush(QColor(15, 23, 42, 46)))

        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 12, 12)
        painter.fillPath(card_path, QBrush(QColor(255, 255, 255, 242)))
        painter.setPen(QPen(QColor(44, 44, 46, 210), 1.6))
        painter.drawPath(card_path)

        # 居中缩放图像
        pad = 6
        inner_rect = card_rect.adjusted(pad, pad, -pad, -pad)
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                int(inner_rect.width()), int(inner_rect.height()),
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            clip_path = QPainterPath()
            clip_path.addRoundedRect(inner_rect, 8, 8)
            painter.save()
            painter.setClipPath(clip_path)
            ix = inner_rect.x() + (inner_rect.width() - scaled.width()) / 2.0
            iy = inner_rect.y() + (inner_rect.height() - scaled.height()) / 2.0
            painter.drawPixmap(int(round(ix)), int(round(iy)), scaled)
            painter.restore()

        # 右下角 PNG 文件角标
        badge_w, badge_h = 34, 18
        badge_rect = QRectF(
            card_rect.right() - badge_w - 5,
            card_rect.bottom() - badge_h - 5,
            badge_w, badge_h
        )
        badge_path = QPainterPath()
        badge_path.addRoundedRect(badge_rect, 6, 6)
        painter.fillPath(badge_path, QBrush(QColor(29, 29, 31, 240)))
        painter.setPen(QPen(QColor(224, 185, 112, 220), 1.0))
        painter.drawPath(badge_path)

        badge_font = _drawer_ui_font(9, QFont.Bold)
        painter.setFont(badge_font)
        painter.setPen(QColor(252, 232, 186))
        painter.drawText(badge_rect, Qt.AlignCenter, "PNG")
    finally:
        painter.end()
    return canvas


def _set_clipboard_file_url(filepath):
    """将本地文件路径以 file:// URL (CF_HDROP) 写入剪贴板，便于直接 Ctrl+V 作为文件粘贴。"""
    if not filepath or not os.path.isfile(filepath):
        return False
    try:
        clip_mime = QMimeData()
        clip_mime.setUrls([QUrl.fromLocalFile(filepath)])
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setMimeData(clip_mime)
            return True
    except Exception as e:
        logging.warning(f"Failed to set clipboard file url: {e}")
    return False


def copy_pixmap_as_clipboard_file(pixmap):
    """把图片导出为临时 PNG 文件并作为文件对象放入剪贴板，返回导出路径或 None。"""
    filepath = export_pixmap_as_drag_file(pixmap)
    if not filepath:
        return None
    _set_clipboard_file_url(filepath)
    return filepath


_LAST_FILE_DRAG_STATE = {"toggled_to_pin": False}


def _install_file_drag_right_click_hook(state):
    """在系统级 QDrag.exec_() 阻塞期间监听当前线程鼠标右键点击，实时取消文件拖拽以便无缝切回贴图模式。"""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        WH_MOUSE = 7
        HC_ACTION = 0
        WM_RBUTTONDOWN = 0x0204
        VK_LBUTTON = 0x01

        HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        try:
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = wintypes.LPARAM
        except Exception:
            pass

        hook_holder = [None]

        def _mouse_proc(nCode, wParam, lParam):
            if nCode == HC_ACTION and wParam == WM_RBUTTONDOWN and not state.get("toggled_to_pin", False):
                if user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000:
                    state["toggled_to_pin"] = True
                    try:
                        QDrag.cancel()
                    except Exception:
                        pass
            return user32.CallNextHookEx(hook_holder[0], nCode, wParam, lParam)

        cb = HOOKPROC(_mouse_proc)
        tid = kernel32.GetCurrentThreadId()
        hhook = user32.SetWindowsHookExW(WH_MOUSE, cb, 0, tid)
        if not hhook:
            return None
        hook_holder[0] = hhook
        return (hhook, cb, user32)
    except Exception as e:
        logging.debug("安装拖拽右键切换钩子失败: %s", e)
        return None


def start_pixmap_file_drag(source_widget, pixmap, copy_to_clipboard=True):
    """将图片转换为原生 OS 本地文件拖拽 (仅携带 file:// URL / CF_HDROP)，并可选同步至剪贴板"""
    _LAST_FILE_DRAG_STATE["toggled_to_pin"] = False
    filepath = export_pixmap_as_drag_file(pixmap)
    if not filepath:
        return None

    if copy_to_clipboard:
        _set_clipboard_file_url(filepath)

    grabber = QWidget.mouseGrabber()
    if grabber is not None:
        try:
            grabber.releaseMouse()
        except Exception:
            pass

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(filepath)])

    drag = QDrag(source_widget)
    drag.setMimeData(mime)
    preview = _build_file_drag_pixmap(pixmap)
    drag.setPixmap(preview)
    drag.setHotSpot(QPoint(preview.width() // 2, preview.height() // 2))

    hook_state = {"toggled_to_pin": False}
    hook_info = _install_file_drag_right_click_hook(hook_state)
    try:
        res = drag.exec_(Qt.CopyAction, Qt.CopyAction)
    finally:
        if hook_info is not None:
            hhook, _cb, u32 = hook_info
            try:
                u32.UnhookWindowsHookEx(hhook)
            except Exception:
                pass
        _LAST_FILE_DRAG_STATE["toggled_to_pin"] = bool(hook_state.get("toggled_to_pin", False))
    return res


def _track_pin_ghost_drag_until_drop_or_toggle(pixmap, badge_text="贴图"):
    """以悬浮毛玻璃贴图预览跟踪鼠标，左键松开落位贴图，右键点击切回 PNG 文件拖拽。"""
    if sys.platform != "win32":
        return ("drop_pin", QCursor.pos())

    user32 = ctypes.windll.user32
    VK_LBUTTON = 0x01
    VK_RBUTTON = 0x02
    VK_ESCAPE = 0x1B

    if not (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
        return ("drop_pin", QCursor.pos())

    ghost = PinDragGhost(pixmap, badge_text=badge_text, capture_input=True)
    result = {"outcome": "drop_pin", "pos": QCursor.pos()}
    loop = QEventLoop()
    r_initially_down = bool(user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
    state = {
        "r_arm_switch": False,
        "prev_r": r_initially_down,
        "r_seen_up": not r_initially_down,
        "armed_after": time.monotonic() + 0.08,
    }

    def _finish(outcome, pos=None):
        if not loop.isRunning():
            return
        result["outcome"] = outcome
        result["pos"] = pos if pos is not None else QCursor.pos()
        loop.quit()

    ghost._can_arm_right_toggle = lambda: bool(state["r_seen_up"] and time.monotonic() >= state["armed_after"])
    ghost._on_drag_right_click = lambda: _finish("toggle_file")
    ghost._on_drag_left_release = lambda gp: _finish("drop_pin", gp)
    ghost._on_drag_escape = lambda: _finish("cancel")

    gpos = QCursor.pos()
    ghost.move(gpos.x() - ghost.width() // 2, gpos.y() - ghost.height() // 2)
    ghost.show()
    ghost.raise_()
    try:
        ghost.grabMouse()
    except Exception:
        pass

    timer = QTimer()
    timer.setInterval(15)

    def _poll():
        cur_pos = QCursor.pos()
        ghost.move(cur_pos.x() - ghost.width() // 2, cur_pos.y() - ghost.height() // 2)
        ghost.raise_()

        if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            _finish("cancel", cur_pos)
            return

        l_down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        if not l_down:
            _finish("cancel" if state["r_arm_switch"] else "drop_pin", cur_pos)
            return

        r_down = bool(user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
        if not r_down:
            state["r_seen_up"] = True
        if state["r_arm_switch"]:
            # 等待右键抬起后再进入 QDrag.exec_()，确保 OLE 只检测到左键处于按下状态
            if not r_down:
                _finish("toggle_file", cur_pos)
                return
        elif state["r_seen_up"] and r_down and not state["prev_r"] and time.monotonic() >= state["armed_after"]:
            state["r_arm_switch"] = True
        state["prev_r"] = r_down

    timer.timeout.connect(_poll)
    timer.start()
    try:
        loop.exec_()
    finally:
        timer.stop()
        try:
            if QWidget.mouseGrabber() is ghost:
                ghost.releaseMouse()
        except Exception:
            pass
        try:
            ghost.hide()
            ghost.close()
            ghost.deleteLater()
        except Exception:
            pass
    return result["outcome"], result["pos"]


def run_toggleable_drag(source_widget, pixmap, start_mode="file", copy_to_clipboard=True, file_drag_fn=None):
    """支持在左键拖拽途中通过鼠标右键在 'file'(PNG 文件) 与 'pin'(贴图) 之间实时切换。"""
    drag_fn = file_drag_fn or start_pixmap_file_drag
    mode = "file" if start_mode == "file" else "pin"
    user32 = ctypes.windll.user32 if sys.platform == "win32" else None
    VK_LBUTTON = 0x01

    while True:
        if mode == "file":
            _LAST_FILE_DRAG_STATE["toggled_to_pin"] = False
            if file_drag_fn is None:
                res = drag_fn(source_widget, pixmap, copy_to_clipboard=copy_to_clipboard)
            else:
                res = drag_fn(source_widget, pixmap)

            toggled = bool(_LAST_FILE_DRAG_STATE.get("toggled_to_pin", False))
            _LAST_FILE_DRAG_STATE["toggled_to_pin"] = False
            l_still_down = bool(user32 and (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000))
            if toggled and l_still_down:
                mode = "pin"
                continue
            return ("file", res, QCursor.pos())

        outcome, pos = _track_pin_ghost_drag_until_drop_or_toggle(pixmap, badge_text="贴图")
        if outcome == "toggle_file":
            mode = "file"
            continue
        return (outcome, None, pos)


# 基础目录与持久化存储路径
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _resolve_drawer_data_dir(base_dir):
    env_dir = os.environ.get("MURIOKI_DATA_DIR", "").strip()
    if env_dir:
        return env_dir
    try:
        with tempfile.TemporaryFile(dir=base_dir):
            return base_dir
    except OSError:
        local_root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(local_root, "MuriokiCapture")

DATA_DIR = _resolve_drawer_data_dir(BASE_DIR)
DRAWER_DIR = os.path.join(DATA_DIR, "capture_drawer")
INDEX_FILE = os.path.join(DRAWER_DIR, "drawer_index.json")


class DrawerItem:
    """抽屉内单张截图数据模型"""
    def __init__(self, item_id, pixmap, zoom_factor=1.0, timestamp=None, filepath=None, width=None, height=None):
        self.id = item_id or f"pin_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        self.pixmap = pixmap
        self.zoom_factor = float(zoom_factor) if zoom_factor else 1.0
        self.timestamp = timestamp or time.time()
        self.filepath = filepath
        self.width = width or (pixmap.width() if pixmap else 100)
        self.height = height or (pixmap.height() if pixmap else 100)

        # 预先生成高清抗锯齿卡片缩略图 (最大 80x80)
        if self.pixmap and not self.pixmap.isNull():
            self.thumbnail = self.pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            self.thumbnail = QPixmap()

        # 手机桌面图标挤压平滑物理坐标 (浮点数用于插值运动)
        self.current_x = 0.0
        self.target_x = 0.0
        self.current_y = 0.0
        self.target_y = 0.0


class DrawerStorage:
    """抽屉持久化存储管理器"""
    @staticmethod
    def ensure_dir():
        os.makedirs(DRAWER_DIR, exist_ok=True)

    @staticmethod
    def load_items():
        DrawerStorage.ensure_dir()
        items = []
        if not os.path.exists(INDEX_FILE):
            return items

        try:
            with open(INDEX_FILE, 'r', encoding='utf-8') as f:
                records = json.load(f)
            if not isinstance(records, list):
                return items

            for rec in records:
                fp = rec.get('filepath', '')
                if not os.path.isabs(fp):
                    fp = os.path.join(DRAWER_DIR, fp)
                if os.path.exists(fp):
                    pix = QPixmap(fp)
                    if not pix.isNull():
                        item = DrawerItem(
                            item_id=rec.get('id'),
                            pixmap=pix,
                            zoom_factor=rec.get('zoom_factor', 1.0),
                            timestamp=rec.get('timestamp', time.time()),
                            filepath=fp,
                            width=rec.get('width', pix.width()),
                            height=rec.get('height', pix.height())
                        )
                        items.append(item)
        except Exception as e:
            logging.error("加载抽屉持久化数据失败: %s", e, exc_info=True)
        return items

    @staticmethod
    def save_items(items):
        DrawerStorage.ensure_dir()
        try:
            records = []
            for item in items:
                if not item.filepath or not os.path.exists(item.filepath):
                    filename = f"{item.id}.png"
                    item.filepath = os.path.join(DRAWER_DIR, filename)
                    if item.pixmap and not item.pixmap.isNull():
                        item.pixmap.save(item.filepath, "PNG")

                records.append({
                    'id': item.id,
                    'filepath': os.path.basename(item.filepath),
                    'width': item.width,
                    'height': item.height,
                    'zoom_factor': item.zoom_factor,
                    'timestamp': item.timestamp
                })

            temp_index = INDEX_FILE + ".tmp"
            with open(temp_index, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            if os.path.exists(INDEX_FILE):
                os.remove(INDEX_FILE)
            os.rename(temp_index, INDEX_FILE)
        except Exception as e:
            logging.error("保存抽屉持久化数据失败: %s", e, exc_info=True)

    @staticmethod
    def remove_item_file(item):
        try:
            if item.filepath and os.path.exists(item.filepath):
                os.remove(item.filepath)
        except Exception as e:
            logging.warning("删除抽屉本地缓存图片失败: %s", e)


class PinDragGhost(QWidget):
    """拖出抽屉放置到桌面时的 macOS 毛玻璃预览浮层（纯圆角无直角边）"""
    def __init__(self, pixmap, parent=None, badge_text="贴图", capture_input=False):
        super().__init__(parent)
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if not capture_input:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        if not capture_input:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.setFocusPolicy(Qt.NoFocus)
        else:
            self.setCursor(Qt.ClosedHandCursor)
            self.setMouseTracking(True)

        self.pixmap = pixmap
        self.badge_text = badge_text
        self._capture_input = bool(capture_input)
        self._right_pressed_inside = False
        self._can_arm_right_toggle = None
        self._on_drag_right_click = None
        self._on_drag_left_release = None
        self._on_drag_escape = None
        self.resize(108, 108)
        if pixmap and not pixmap.isNull():
            self.ghost_pixmap = pixmap.scaled(82, 82, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            self.ghost_pixmap = QPixmap()

    def mouseMoveEvent(self, event):
        if self._capture_input:
            gpos = event.globalPos()
            self.move(gpos.x() - self.width() // 2, gpos.y() - self.height() // 2)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._capture_input and event.button() == Qt.RightButton:
            can_arm = self._can_arm_right_toggle() if callable(self._can_arm_right_toggle) else True
            self._right_pressed_inside = bool(can_arm and (event.buttons() & Qt.LeftButton))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._capture_input:
            if event.button() == Qt.RightButton:
                armed = self._right_pressed_inside
                self._right_pressed_inside = False
                if armed and (event.buttons() & Qt.LeftButton) and callable(self._on_drag_right_click):
                    self._on_drag_right_click()
                event.accept()
                return
            if event.button() == Qt.LeftButton and callable(self._on_drag_left_release):
                self._right_pressed_inside = False
                self._on_drag_left_release(event.globalPos())
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        event.accept()

    def keyPressEvent(self, event):
        if self._capture_input and event.key() == Qt.Key_Escape and callable(self._on_drag_escape):
            self._on_drag_escape()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        if self.ghost_pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 柔和外阴影
        for i, alpha in enumerate([6, 12, 20]):
            sr = QRectF(self.rect()).adjusted(2 + i, 2 + i, -2 - i, -2 - i)
            sp = QPainterPath()
            sp.addRoundedRect(sr, 14 - i, 14 - i)
            painter.fillPath(sp, QBrush(QColor(15, 23, 42, alpha)))

        r = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)

        # macOS Sequoia 暖白磨砂卡片底色
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, QColor(255, 255, 255, 238))
        grad.setColorAt(1.0, QColor(248, 245, 239, 222))
        painter.fillPath(path, QBrush(grad))

        painter.setPen(QPen(QColor(182, 134, 58, 230), 2.0))
        painter.drawPath(path)

        # 居中圆角绘制缩略图
        tw = self.ghost_pixmap.width()
        th = self.ghost_pixmap.height()
        tx = r.x() + (r.width() - tw) / 2.0
        ty = r.y() + (r.height() - th) / 2.0
        img_rect = QRectF(tx, ty, tw, th)

        painter.save()
        clip_p = QPainterPath()
        clip_p.addRoundedRect(img_rect, 6, 6)
        painter.setClipPath(clip_p)
        painter.drawPixmap(int(tx), int(ty), self.ghost_pixmap)
        painter.restore()

        painter.setPen(QPen(QColor(28, 28, 30, 30), 1.0))
        painter.drawRoundedRect(img_rect, 6, 6)

        if self.badge_text:
            badge_font = _drawer_ui_font(8, QFont.Bold)
            painter.setFont(badge_font)
            fm = painter.fontMetrics()
            bw = max(32, fm.horizontalAdvance(self.badge_text) + 12)
            bh = 17
            br = QRectF(r.right() - bw - 5, r.bottom() - bh - 5, bw, bh)
            bp = QPainterPath()
            bp.addRoundedRect(br, 5, 5)
            painter.fillPath(bp, QBrush(QColor(168, 118, 40, 238)))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(br, Qt.AlignCenter, self.badge_text)


class DrawerHoverPreview(QWidget):
    """抽屉卡片悬停大图快速预览窗（无焦点无鼠标拦截，自动贴边避让抽屉本身）。"""

    MAX_IMG_W = 320
    MAX_IMG_H = 240
    PAD_SHADOW = 10
    PAD_INNER = 10
    FOOTER_H = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._scaled_pixmap = QPixmap()
        self._res_label = ""
        self._hint_label = "拖出贴图 · 拖时右键切 PNG/贴图 · 右键更多"

    def _prepare_preview_metrics(self, pixmap, orig_w=None, orig_h=None, max_img_w=None, max_img_h=None):
        mw = max_img_w or self.MAX_IMG_W
        mh = max_img_h or self.MAX_IMG_H
        self._scaled_pixmap = pixmap.scaled(mw, mh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img_w = max(140, self._scaled_pixmap.width())
        img_h = max(84, self._scaled_pixmap.height())
        total_w = img_w + self.PAD_INNER * 2 + self.PAD_SHADOW * 2
        total_h = img_h + self.PAD_INNER * 2 + self.FOOTER_H + 4 + self.PAD_SHADOW * 2
        w_val = orig_w or pixmap.width()
        h_val = orig_h or pixmap.height()
        self._res_label = f"{w_val} × {h_val}"
        return total_w, total_h

    def show_for_item(self, item, card_global_rect, drawer_global_rect, avail_rect):
        if item is None or item.pixmap is None or item.pixmap.isNull():
            self.hide()
            return
        total_w, total_h = self._prepare_preview_metrics(
            item.pixmap,
            orig_w=getattr(item, "width", None),
            orig_h=getattr(item, "height", None),
        )

        # 优先放在抽屉正下方并横向对齐悬停卡片；底部空间不足则放到抽屉左侧
        if avail_rect.bottom() - drawer_global_rect.bottom() >= total_h - 4:
            x = card_global_rect.center().x() - total_w // 2
            y = drawer_global_rect.bottom() + 2
        else:
            x = drawer_global_rect.left() - total_w - 4
            y = card_global_rect.center().y() - total_h // 2

        x = max(avail_rect.left(), min(avail_rect.right() - total_w + 1, x))
        y = max(avail_rect.top(), min(avail_rect.bottom() - total_h + 1, y))

        self.resize(total_w, total_h)
        self.move(x, y)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def show_for_pixmap_beside_rect(self, pixmap, anchor_global_rect, avail_rect, hint_text=None, max_img_w=420, max_img_h=300):
        """在锚点矩形（如历史缩略图）旁弹出高清大图悬停预览，不遮挡源缩略图且自动避让屏幕边界。"""
        if pixmap is None or pixmap.isNull():
            self.hide()
            return
        if hint_text is not None:
            self._hint_label = hint_text
        total_w, total_h = self._prepare_preview_metrics(
            pixmap, max_img_w=max_img_w, max_img_h=max_img_h
        )
        if avail_rect.right() - anchor_global_rect.right() >= total_w - 4:
            x = anchor_global_rect.right() + 6
        else:
            x = anchor_global_rect.left() - total_w - 6
        y = anchor_global_rect.center().y() - total_h // 2
        x = max(avail_rect.left(), min(avail_rect.right() - total_w + 1, x))
        y = max(avail_rect.top(), min(avail_rect.bottom() - total_h + 1, y))
        self.resize(total_w, total_h)
        self.move(x, y)
        if not self.isVisible():
            self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        if self._scaled_pixmap.isNull():
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)

            card_r = QRectF(
                self.PAD_SHADOW,
                self.PAD_SHADOW,
                self.width() - self.PAD_SHADOW * 2,
                self.height() - self.PAD_SHADOW * 2,
            )

            for i, alpha in enumerate((8, 16, 26)):
                sr = card_r.adjusted(-3 + i, -2 + i, 3 - i, 4 - i)
                sp = QPainterPath()
                sp.addRoundedRect(sr, 14 - i, 14 - i)
                painter.fillPath(sp, QBrush(QColor(15, 23, 42, alpha)))

            card_p = QPainterPath()
            card_p.addRoundedRect(card_r, 12, 12)
            grad = QLinearGradient(0, card_r.top(), 0, card_r.bottom())
            grad.setColorAt(0.0, QColor(255, 255, 255, 248))
            grad.setColorAt(1.0, QColor(246, 249, 255, 244))
            painter.fillPath(card_p, QBrush(grad))
            painter.setPen(QPen(QColor(148, 163, 184, 150), 1.1))
            painter.drawPath(card_p)

            img_area = QRectF(
                card_r.left() + self.PAD_INNER,
                card_r.top() + self.PAD_INNER,
                card_r.width() - self.PAD_INNER * 2,
                card_r.height() - self.PAD_INNER * 2 - self.FOOTER_H - 4,
            )
            sw = self._scaled_pixmap.width()
            sh = self._scaled_pixmap.height()
            ix = img_area.x() + (img_area.width() - sw) / 2.0
            iy = img_area.y() + (img_area.height() - sh) / 2.0
            img_r = QRectF(ix, iy, sw, sh)

            painter.save()
            clip_p = QPainterPath()
            clip_p.addRoundedRect(img_r, 7, 7)
            painter.setClipPath(clip_p)
            painter.drawPixmap(int(round(ix)), int(round(iy)), self._scaled_pixmap)
            painter.restore()
            painter.setPen(QPen(QColor(15, 23, 42, 32), 1.0))
            painter.drawRoundedRect(img_r, 7, 7)

            # 底部信息条：分辨率胶囊 + 极简提示
            foot_y = card_r.bottom() - self.PAD_INNER - self.FOOTER_H + 3
            res_font = _drawer_ui_font(9, QFont.DemiBold)
            painter.setFont(res_font)
            fm = painter.fontMetrics()
            res_w = fm.horizontalAdvance(self._res_label) + 12
            res_rect = QRectF(card_r.left() + self.PAD_INNER, foot_y, res_w, 19)
            res_path = QPainterPath()
            res_path.addRoundedRect(res_rect, 5, 5)
            painter.fillPath(res_path, QBrush(QColor(250, 245, 232, 242)))
            painter.setPen(QPen(QColor(218, 192, 145), 1.0))
            painter.drawPath(res_path)
            painter.setPen(QColor(122, 81, 16))
            painter.drawText(res_rect, Qt.AlignCenter, self._res_label)

            hint_rect = QRectF(
                res_rect.right() + 8, foot_y,
                max(10.0, card_r.right() - self.PAD_INNER - res_rect.right() - 8), 19
            )
            painter.setFont(_drawer_ui_font(9, QFont.Normal))
            painter.setPen(QColor(109, 109, 114))
            elided = painter.fontMetrics().elidedText(
                self._hint_label, Qt.ElideRight, int(hint_rect.width())
            )
            painter.drawText(hint_rect, Qt.AlignRight | Qt.AlignVCenter, elided)
        finally:
            painter.end()


class PinDrawerWindow(QWidget):
    """极简 macOS 风格毛玻璃截图储藏柜窗口 (无顶栏、真实背景高斯磨砂、纯圆角无黑框)"""

    # 布局与外观常量：紧凑纯粹的卡槽设计 + 外圈柔和阴影留白
    SHADOW_PAD = 8          # 左、上、下预留 8px 用于绘制柔和圆角阴影，彻底消除直角截断
    HANDLE_WIDTH = 16       # 收起时屏幕右边缘外露的感应条宽度
    DRAWER_HEIGHT = 132     # 总高度 = 上阴影(8) + 上内边距(10) + 卡片(96) + 下内边距(10) + 下阴影(8)
    CARD_WIDTH = 96         # 单个截图卡片宽度
    CARD_HEIGHT = 96        # 单个截图卡片高度
    CARD_Y = 18             # 卡片 Y 坐标 (8px 阴影 + 10px 内边距)
    CARD_SPACING = 10       # 卡片间距
    PADDING_LEFT = 20       # 抽屉左内边距 (含左阴影 8px + 内部留白 12px)
    PADDING_RIGHT = 14      # 抽屉右内边距
    TOP_OFFSET = 72         # 距离屏幕右上角的 Y 轴下移距离

    def __init__(self, main_app=None):
        super().__init__()
        self.main_app = main_app
        # 仅使用 Qt 原生透明背景，四周无任何直角黑边与系统多余边框
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

        # 抽屉宽度与多行展开尺寸属性 (用于 QPropertyAnimation 平滑动画)
        self._drawer_width = self.HANDLE_WIDTH
        self._is_expanded = False
        self._target_expanded_width = 260
        self._target_expanded_height = self.DRAWER_HEIGHT

        # 抽屉功能总开关（通过 Ctrl+1 彻底开启或关闭整个抽屉功能）
        self.drawer_enabled = True
        self.hover_wake_enabled = True

        # 锚定屏幕与置顶自愈状态（避免多屏鼠标移动或 Win+D / 焦点切换导致右上角感应条消失）
        self._anchor_screen = QApplication.primaryScreen()
        self._is_file_dragging_over = False
        self._last_fg_hwnd = 0
        self._zorder_tick = 0

        # 实时采样模糊后的桌面背景缓存
        self._frosted_bg_pixmap = None
        self._frosted_bg_screen_x = 0
        self._frosted_bg_screen_y = 0

        # 抽屉数据列表
        self.items = DrawerStorage.load_items()

        # 内部拖拽排序状态
        self.dragging_internal_index = None
        self.internal_drag_start_pos = None
        self.is_internal_dragging = False
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.drag_ghost_window = None
        self._file_drag_armed = False
        self._suppress_next_context_menu = False

        # 外部 PinnedWindow 拖入挤压预览状态
        self.external_drag_pin = None
        self.external_insert_slot = None

        # 鼠标悬停卡片与删除按钮索引
        self.hovered_card_index = None
        self.hovered_delete_btn_index = None
        self._context_menu_open = False
        self._paste_hotkey_registered = False
        self._paste_hotkey_id = 9412
        self._boss_hidden = False

        # 悬停大图预览浮窗（停留约 320ms 自动浮现，切卡即刻刷新，离开或拖拽立即隐藏）
        self.hover_preview = DrawerHoverPreview()
        self.hover_preview_index = None
        self.hover_preview_timer = QTimer(self)
        self.hover_preview_timer.setSingleShot(True)
        self.hover_preview_timer.setInterval(320)
        self.hover_preview_timer.timeout.connect(self._on_hover_preview_timeout)

        # 滚动偏移量 (保留 scroll_x=0 兼容字段；多行超出可视高度时使用 scroll_y)
        self.scroll_x = 0.0
        self.target_scroll_x = 0.0
        self.scroll_y = 0.0
        self.target_scroll_y = 0.0

        # 定时器体系
        # 1. 弹簧物理模拟与挤压运动定时器 (60fps)
        self.physics_timer = QTimer(self)
        self.physics_timer.setInterval(16)
        self.physics_timer.timeout.connect(self._update_spring_physics)

        # 2. 毫秒级边缘光标雷达监听器 (30ms 轮询)
        self.edge_radar_timer = QTimer(self)
        self.edge_radar_timer.setInterval(30)
        self.edge_radar_timer.timeout.connect(self._check_edge_radar)
        self.edge_radar_timer.start()

        # 3. 闪电回缩定时器 (160ms)
        self.auto_retract_timer = QTimer(self)
        self.auto_retract_timer.setSingleShot(True)
        self.auto_retract_timer.setInterval(160)
        self.auto_retract_timer.timeout.connect(self._on_auto_retract_timeout)

        # 4. 抽屉滑入滑出动画 (150ms 极速顺滑响应)
        self.slide_anim = QPropertyAnimation(self, b"drawerWidth")
        self.slide_anim.setDuration(150)
        self.slide_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 初始化卡片初始位置
        self._recalc_expanded_width()
        self._recalc_slot_targets()
        for it in self.items:
            it.current_x = it.target_x
            it.current_y = it.target_y

        self._update_geometry()
        app = QApplication.instance()
        if app:
            for sig_name in ("screenAdded", "screenRemoved", "primaryScreenChanged"):
                sig = getattr(app, sig_name, None)
                if sig is not None:
                    sig.connect(self._on_screens_changed)
        self.show()
        self.ensure_on_top(force=True)

    # ==========================================
    # 实时桌面背景采样 + 高斯磨砂模糊 (真正的 macOS 毛玻璃)
    # ==========================================
    def _capture_and_blur_background(self, exclude_win=None):
        """截取抽屉即将展开区域的屏幕画面，并进行高斯模糊与 macOS 冰晶亮色混色"""
        try:
            avail = self._get_active_screen_rect()
            screen = self._resolve_anchor_screen()
            if not screen:
                return

            # 一次性采样足够容纳多列/多行展开区域的桌面背景，避免扩展宽度或换行时背景缺失
            full_w = min(avail.width(), max(self._target_expanded_width + self.CARD_WIDTH + self.CARD_SPACING, 520))
            full_h = min(avail.height(), max(self._target_expanded_height + self.CARD_HEIGHT + self.CARD_SPACING, self.DRAWER_HEIGHT))
            full_x = avail.right() + 1 - full_w
            full_y = max(avail.top(), min(avail.bottom() + 1 - full_h, avail.top() + self.TOP_OFFSET))

            # 临时将自身与正在拖拽的贴图窗口从屏幕截图中剔除，避免自身像素混入毛玻璃基底
            excluded_hwnds = []
            for win in (self if self.isVisible() else None, exclude_win):
                if win is not None:
                    try:
                        hwnd = int(win.winId())
                        if hwnd and hwnd not in excluded_hwnds:
                            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
                            excluded_hwnds.append(hwnd)
                    except Exception as e:
                        logging.debug("设置抽屉模糊截图剔除标志失败: %s", e)

            try:
                grabbed = screen.grabWindow(0, full_x, full_y, full_w, full_h)
            finally:
                for hwnd in excluded_hwnds:
                    try:
                        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00)
                    except Exception as e:
                        logging.debug("恢复窗口 DisplayAffinity 失败: %s", e)

            if grabbed.isNull():
                return

            qimg = grabbed.toImage().convertToFormat(QImage.Format_RGB888)
            w, h = qimg.width(), qimg.height()
            if w <= 20 or h <= 20:
                return

            ptr = qimg.bits()
            ptr.setsize(qimg.byteCount())
            arr = np.frombuffer(ptr, np.uint8).reshape((h, qimg.bytesPerLine()))[:, :w * 3].reshape((h, w, 3)).copy()

            # 若在特殊受限会话下截屏为纯黑，则自动使用雅致冰晶渐变基底代替，绝不产生黑框
            if arr.mean() < 3.0:
                arr[:, :, 0] = 228
                arr[:, :, 1] = 235
                arr[:, :, 2] = 246
            elif not self._is_expanded:
                # 收起态初次采样时抹除右边缘感应条像素，用左侧相邻背景自然延展填充
                handle_px = min(w - 2, self.HANDLE_WIDTH + 6)
                strip_h = min(h, self.DRAWER_HEIGHT)
                if handle_px > 0 and w > handle_px + 2 and strip_h > 0:
                    arr[:strip_h, w - handle_px:] = arr[:strip_h, w - handle_px - 2:w - handle_px - 1]

            # 4 倍降采样 + 大核双道高斯模糊 + 双三次插值升采样，获得苹果级丝滑磨砂背景
            small_w = max(8, w // 4)
            small_h = max(8, h // 4)
            small = cv2.resize(arr, (small_w, small_h), interpolation=cv2.INTER_AREA)
            blurred = cv2.GaussianBlur(small, (25, 25), 0)
            blurred = cv2.GaussianBlur(blurred, (15, 15), 0)
            up = cv2.resize(blurred, (w, h), interpolation=cv2.INTER_CUBIC)

            # 与 macOS 亮色冰晶毛玻璃基底 (RGB: 236, 241, 249) 进行光学混色
            # 保留 62% 背景真实色彩透射 + 38% 冰晶磨砂柔光，彻底告别死黑，无论在白底还是暗底上都晶莹通透
            frost_tint = np.empty_like(up, dtype=np.uint8)
            frost_tint[:, :, 0] = 236
            frost_tint[:, :, 1] = 241
            frost_tint[:, :, 2] = 249
            blended = cv2.addWeighted(up, 0.62, frost_tint, 0.38, 0)

            out_qimg = QImage(blended.data, w, h, w * 3, QImage.Format_RGB888).copy()
            self._frosted_bg_pixmap = QPixmap.fromImage(out_qimg)
            self._frosted_bg_screen_x = full_x
            self._frosted_bg_screen_y = full_y
        except Exception as e:
            logging.debug("背景毛玻璃采样异常: %s", e)

    # ==========================================
    # 属性与几何尺寸管理
    # ==========================================
    def get_drawer_width(self):
        return self._drawer_width

    def set_drawer_width(self, w):
        self._drawer_width = int(w)
        self._update_geometry()
        self.update()

    drawerWidth = pyqtProperty(int, get_drawer_width, set_drawer_width)

    def _resolve_anchor_screen(self):
        """返回当前锚定的有效屏幕对象，屏幕热插拔后自动回退为主屏幕。"""
        screens = QApplication.screens()
        try:
            if self._anchor_screen is not None and self._anchor_screen in screens:
                return self._anchor_screen
        except RuntimeError:
            pass
        self._anchor_screen = QApplication.primaryScreen() or (screens[0] if screens else None)
        return self._anchor_screen

    def _on_screens_changed(self, *args):
        """显示器拓扑或主屏变化时刷新锚定位置，防止感应条跑出屏幕或消失。"""
        self._anchor_screen = self._resolve_anchor_screen()
        if self.drawer_enabled:
            self._recalc_expanded_width()
            self._recalc_slot_targets()
            self._update_geometry()
            self.ensure_on_top(force=True)
            self.update()

    def _get_active_screen_rect(self):
        """获取当前锚定屏幕的可用区域 (避开任务栏，不在缩回动画中途因光标跨屏跳走)"""
        screen = self._resolve_anchor_screen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    def _get_grid_metrics(self, count=None):
        """计算整卡对齐的网格列数、总行数、可视行数与抽屉展开尺寸（绝不半张裁切卡片）。"""
        if count is None:
            count = len(self.items)
            if self.external_insert_slot is not None:
                count += 1

        avail = self._get_active_screen_rect()
        pitch_x = self.CARD_WIDTH + self.CARD_SPACING
        pitch_y = self.CARD_HEIGHT + self.CARD_SPACING
        chrome_v = self.CARD_Y + self.SHADOW_PAD + 10

        max_drawer_w = max(
            self.PADDING_LEFT + self.CARD_WIDTH + self.PADDING_RIGHT,
            int(avail.width() * 0.90),
        )
        cols_cap = max(
            1,
            (max_drawer_w - self.PADDING_LEFT - self.PADDING_RIGHT + self.CARD_SPACING) // pitch_x,
        )

        if count <= 0:
            return (1, 1, 1, 230, self.DRAWER_HEIGHT)

        cols = max(1, min(count, cols_cap))
        total_rows = max(1, math.ceil(count / cols))

        max_drawer_h = max(
            self.DRAWER_HEIGHT,
            min(max(self.DRAWER_HEIGHT, avail.height() - self.TOP_OFFSET - 24), int(avail.height() * 0.72)),
        )
        max_visible_rows = max(1, (max_drawer_h - chrome_v + self.CARD_SPACING) // pitch_y)
        visible_rows = min(total_rows, max_visible_rows)

        target_w = max(
            140,
            self.PADDING_LEFT + cols * self.CARD_WIDTH + max(0, cols - 1) * self.CARD_SPACING + self.PADDING_RIGHT,
        )
        target_h = max(
            self.DRAWER_HEIGHT,
            chrome_v + visible_rows * self.CARD_HEIGHT + max(0, visible_rows - 1) * self.CARD_SPACING,
        )
        return (int(cols), int(total_rows), int(visible_rows), int(target_w), int(target_h))

    def _get_grid_cols(self):
        return self._get_grid_metrics()[0]

    def _update_geometry(self):
        avail = self._get_active_screen_rect()
        target_right = avail.right() + 1
        w = max(self.HANDLE_WIDTH, int(self._drawer_width))
        target_h = getattr(self, '_target_expanded_height', self.DRAWER_HEIGHT)
        if w <= self.HANDLE_WIDTH + 4 and not self._is_expanded:
            h = self.DRAWER_HEIGHT
        else:
            span_w = max(1, self._target_expanded_width - self.HANDLE_WIDTH)
            t = max(0.0, min(1.0, (w - self.HANDLE_WIDTH) / float(span_w)))
            h = int(round(self.DRAWER_HEIGHT + (target_h - self.DRAWER_HEIGHT) * t))
        x = target_right - w
        y = max(avail.top(), min(avail.bottom() + 1 - h, avail.top() + self.TOP_OFFSET))
        target_rect = QRect(x, y, w, h)
        if self.geometry() != target_rect:
            self.setGeometry(target_rect)

    def _recalc_expanded_width(self):
        """根据当前截图张数计算整卡对齐的展开宽高并约束滚动范围。"""
        cols, total_rows, visible_rows, target_w, target_h = self._get_grid_metrics()
        self._target_expanded_width = target_w
        self._target_expanded_height = target_h
        self.scroll_x = 0.0
        self.target_scroll_x = 0.0
        chrome_v = self.CARD_Y + self.SHADOW_PAD + 10
        content_h = chrome_v + total_rows * self.CARD_HEIGHT + max(0, total_rows - 1) * self.CARD_SPACING
        min_scroll_y = min(0.0, float(target_h) - float(content_h))
        self.target_scroll_y = max(min_scroll_y, min(0.0, self.target_scroll_y))
        self.scroll_y = max(min_scroll_y, min(0.0, self.scroll_y))

    def _scroll_slot_into_view(self, slot_idx, immediate=False):
        """确保新增或高亮卡片所在行完整位于可视区域内。"""
        cols, total_rows, visible_rows, _tw, target_h = self._get_grid_metrics()
        if total_rows <= visible_rows:
            self.target_scroll_y = 0.0
            if immediate:
                self.scroll_y = 0.0
            return
        slot_idx = max(0, slot_idx)
        row = slot_idx // max(1, cols)
        pitch_y = self.CARD_HEIGHT + self.CARD_SPACING
        row_top = float(self.CARD_Y + row * pitch_y)
        row_bottom = row_top + float(self.CARD_HEIGHT)
        view_top = float(self.CARD_Y)
        view_bottom = float(target_h - (self.SHADOW_PAD + 10))
        if row_top + self.target_scroll_y < view_top:
            self.target_scroll_y = view_top - row_top
        elif row_bottom + self.target_scroll_y > view_bottom:
            self.target_scroll_y = view_bottom - row_bottom
        chrome_v = self.CARD_Y + self.SHADOW_PAD + 10
        content_h = chrome_v + total_rows * self.CARD_HEIGHT + max(0, total_rows - 1) * self.CARD_SPACING
        min_scroll_y = min(0.0, float(target_h) - float(content_h))
        self.target_scroll_y = max(min_scroll_y, min(0.0, self.target_scroll_y))
        if immediate:
            self.scroll_y = self.target_scroll_y

    def _sync_expanded_layout(self, animate_width=True, focus_slot=None):
        """数量变化或换行时同步更新已展开抽屉的宽高、毛玻璃贴图与卡片目标位置。"""
        self._recalc_expanded_width()
        if focus_slot is not None:
            self._scroll_slot_into_view(focus_slot, immediate=True)
        self._recalc_slot_targets()
        if not self._is_expanded:
            return
        avail = self._get_active_screen_rect()
        target_x = avail.right() + 1 - self._target_expanded_width
        target_y = max(
            avail.top(),
            min(avail.bottom() + 1 - self._target_expanded_height, avail.top() + self.TOP_OFFSET),
        )
        need_bg = (
            self._frosted_bg_pixmap is None
            or self._frosted_bg_pixmap.isNull()
            or self._frosted_bg_screen_x > target_x
            or self._frosted_bg_screen_x + self._frosted_bg_pixmap.width() < avail.right() + 1
            or self._frosted_bg_screen_y > target_y
            or self._frosted_bg_screen_y + self._frosted_bg_pixmap.height() < target_y + self._target_expanded_height
        )
        if need_bg:
            self._capture_and_blur_background(exclude_win=self.external_drag_pin)
        if self._drawer_width != self._target_expanded_width:
            if animate_width:
                self.slide_anim.stop()
                self.slide_anim.setStartValue(self._drawer_width)
                self.slide_anim.setEndValue(self._target_expanded_width)
                self.slide_anim.start()
            else:
                self.set_drawer_width(self._target_expanded_width)
        self._update_geometry()
        self._keep_dragged_windows_on_top()

    def _slot_pos(self, slot_idx):
        """计算第 slot_idx 个卡片在网格中的基准坐标 (x, y)"""
        cols = self._get_grid_cols()
        row = slot_idx // cols
        col = slot_idx % cols
        x = self.PADDING_LEFT + col * (self.CARD_WIDTH + self.CARD_SPACING) + self.scroll_x
        y = self.CARD_Y + row * (self.CARD_HEIGHT + self.CARD_SPACING) + self.scroll_y
        return (float(x), float(y))

    def _slot_x(self, slot_idx):
        """计算第 slot_idx 个卡片的标准基准 X 坐标"""
        return self._slot_pos(slot_idx)[0]

    def _recalc_slot_targets(self):
        """根据挤压逻辑计算每个卡片的目标 (X, Y) 坐标"""
        if self.is_internal_dragging and self.dragging_internal_index is not None:
            drag_idx = self.dragging_internal_index
            drag_it = self.items[drag_idx]
            target_slot = self._calc_slot_from_pos(
                drag_it.current_x + self.CARD_WIDTH / 2.0,
                drag_it.current_y + self.CARD_HEIGHT / 2.0,
            )
            target_slot = max(0, min(len(self.items) - 1, target_slot))

            for i, it in enumerate(self.items):
                if i == drag_idx:
                    continue
                eff_slot = i
                if drag_idx < target_slot:
                    if drag_idx < i <= target_slot:
                        eff_slot = i - 1
                elif drag_idx > target_slot:
                    if target_slot <= i < drag_idx:
                        eff_slot = i + 1
                it.target_x, it.target_y = self._slot_pos(eff_slot)

        elif self.external_insert_slot is not None:
            ins = self.external_insert_slot
            for i, it in enumerate(self.items):
                eff_slot = i + 1 if i >= ins else i
                it.target_x, it.target_y = self._slot_pos(eff_slot)

        else:
            for i, it in enumerate(self.items):
                it.target_x, it.target_y = self._slot_pos(i)

        if not self.physics_timer.isActive():
            self.physics_timer.start()

    def _calc_slot_from_pos(self, x, y=None, for_insert=False):
        """根据 (X, Y) 坐标计算对应的二维槽位索引。
        for_insert=True 时以外部指针插入边界计算；False 时以拖动卡片中心跨越邻卡间隙中点计算。
        """
        cols = self._get_grid_cols()
        pitch_x = self.CARD_WIDTH + self.CARD_SPACING
        rel_x = x - self.PADDING_LEFT - self.scroll_x
        if for_insert:
            col = int(math.floor((rel_x + pitch_x / 2.0) / pitch_x))
            col = max(0, min(cols, col))
            slot_count = len(self.items) + 1
        else:
            col = int(math.floor((rel_x + self.CARD_SPACING / 2.0) / pitch_x))
            col = max(0, min(cols - 1, col))
            slot_count = max(1, len(self.items))

        max_row = max(0, math.ceil(slot_count / max(1, cols)) - 1)
        if y is None or max_row == 0:
            row = 0
        else:
            pitch_y = self.CARD_HEIGHT + self.CARD_SPACING
            rel_y = y - self.CARD_Y - self.scroll_y
            row = int(math.floor((rel_y + self.CARD_SPACING / 2.0) / pitch_y))
            row = max(0, min(max_row, row))

        if for_insert and col >= cols:
            slot = (row + 1) * cols
        else:
            slot = row * cols + col
        max_slot = len(self.items) if for_insert else max(0, len(self.items) - 1)
        return max(0, min(max_slot, slot))

    def _calc_slot_from_x(self, x, y=None, for_insert=False):
        """兼容接口：根据 X/Y 坐标计算对应的槽位索引 (0 ~ N)"""
        return self._calc_slot_from_pos(x, y, for_insert=for_insert)

    def _keep_dragged_windows_on_top(self, extra_win=None):
        """确保正在拖入或拖出抽屉的贴图窗口与拖拽浮层始终悬浮在抽屉之上，绝不被抽屉遮挡。"""
        seen = set()
        for win in (extra_win, self.external_drag_pin, self.drag_ghost_window):
            if win is None or id(win) in seen:
                continue
            seen.add(id(win))
            try:
                if not win.isVisible():
                    continue
                if sys.platform == "win32":
                    hwnd = int(win.winId())
                    if hwnd:
                        ctypes.windll.user32.SetWindowPos(
                            hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
                        )
                win.raise_()
            except Exception:
                pass

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange and self.drawer_enabled:
            if self.isMinimized():
                QTimer.singleShot(15, lambda: self.ensure_on_top(force=True))
        super().changeEvent(event)

    def ensure_on_top(self, force=False):
        """守卫右上角抽屉条的可见性与 Windows TOPMOST Z 序，防止被全屏/Win+D/新顶置窗遮盖。"""
        if not self.drawer_enabled or getattr(self, "_boss_hidden", False):
            return
        if self.main_app is not None and self.main_app.isVisible():
            return
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return

        self._update_geometry()

        need_show = (not self.isVisible()) or self.isMinimized()
        if self.isMinimized():
            self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowNoState)
        if not self.isVisible():
            self.show()

        if sys.platform != "win32":
            if force or need_show:
                self.raise_()
            return

        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
                need_show = True

            fg_hwnd = int(user32.GetForegroundWindow() or 0)
            fg_changed = (fg_hwnd != self._last_fg_hwnd)
            if fg_changed:
                self._last_fg_hwnd = fg_hwnd

            ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            lost_topmost = ((ex_style & _WS_EX_TOPMOST) == 0)

            covered_by_other = False
            if not (force or need_show or fg_changed or lost_topmost):
                class _RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                self_rc = _RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(self_rc)):
                    own_pid = os.getpid()
                    dwmapi = getattr(ctypes.windll, "dwmapi", None)
                    prev_hwnd = user32.GetWindow(hwnd, _GW_HWNDPREV)
                    checked = 0
                    while prev_hwnd and checked < 16:
                        checked += 1
                        if user32.IsWindowVisible(prev_hwnd) and not user32.IsIconic(prev_hwnd):
                            pid = ctypes.c_ulong(0)
                            user32.GetWindowThreadProcessId(prev_hwnd, ctypes.byref(pid))
                            if pid.value != own_pid:
                                cloaked = ctypes.c_int(0)
                                is_cloaked = False
                                if dwmapi is not None:
                                    try:
                                        if dwmapi.DwmGetWindowAttribute(
                                            prev_hwnd, _DWMWA_CLOAKED,
                                            ctypes.byref(cloaked), ctypes.sizeof(cloaked)
                                        ) == 0 and cloaked.value != 0:
                                            is_cloaked = True
                                    except Exception:
                                        pass
                                if not is_cloaked:
                                    other_rc = _RECT()
                                    if user32.GetWindowRect(prev_hwnd, ctypes.byref(other_rc)):
                                        if (other_rc.left < self_rc.right and other_rc.right > self_rc.left
                                                and other_rc.top < self_rc.bottom and other_rc.bottom > self_rc.top):
                                            covered_by_other = True
                                            break
                        prev_hwnd = user32.GetWindow(prev_hwnd, _GW_HWNDPREV)

            if force or need_show or fg_changed or lost_topmost or covered_by_other:
                flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
                if need_show:
                    flags |= _SWP_SHOWWINDOW
                user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags)
            self._keep_dragged_windows_on_top()
        except Exception as e:
            logging.debug("抽屉置顶自愈检查异常: %s", e)

    def _clear_external_insert_preview(self):
        """清除外部拖入占位预览状态并恢复卡片排列。"""
        if self.external_insert_slot is not None or self.external_drag_pin is not None:
            self.external_drag_pin = None
            self.external_insert_slot = None
            if self._is_expanded:
                self._sync_expanded_layout(animate_width=True)
            else:
                self._recalc_expanded_width()
                self._recalc_slot_targets()
            self.update()

    def _update_external_insert_slot(self, local_x, local_y=None):
        """根据拖入时的局部 (X, Y) 坐标同步更新挤压预览槽位、抽屉宽度与行数。"""
        self.auto_retract_timer.stop()
        new_slot = self._calc_slot_from_pos(local_x, local_y, for_insert=True)
        if new_slot != self.external_insert_slot:
            self.external_insert_slot = new_slot
            self._sync_expanded_layout(animate_width=True, focus_slot=new_slot)
            self.update()
        else:
            self._keep_dragged_windows_on_top()

    # ==========================================
    # 展开、回缩与 Ctrl+1 功能总开关控制
    # ==========================================
    def expand(self, exclude_win=None):
        """向左优雅展开极简储藏柜"""
        if not self.drawer_enabled or getattr(self, "_boss_hidden", False):
            return
        if not self.isVisible():
            self.show()
        self.ensure_on_top(force=True)
        if self._is_expanded:
            self._sync_expanded_layout(animate_width=True)
            self._keep_dragged_windows_on_top(extra_win=exclude_win)
            self.auto_retract_timer.stop()
            self.update()
            return
        self._recalc_expanded_width()
        self._recalc_slot_targets()
        if self._drawer_width <= self.HANDLE_WIDTH + 6:
            for it in self.items:
                it.current_x = it.target_x
                it.current_y = it.target_y
            self._capture_and_blur_background(exclude_win=exclude_win)

        self._is_expanded = True
        self.auto_retract_timer.stop()

        self.slide_anim.stop()
        self.slide_anim.setStartValue(self._drawer_width)
        self.slide_anim.setEndValue(self._target_expanded_width)
        self.slide_anim.start()
        self._keep_dragged_windows_on_top(extra_win=exclude_win)
        self._sync_clipboard_paste_hotkey()

    def retract(self):
        """向右迅速回缩至屏幕右边缘"""
        self._hide_hover_preview()
        self._sync_clipboard_paste_hotkey(force_off=True)
        if not self._is_expanded and self._drawer_width == self.HANDLE_WIDTH:
            return
        self._is_expanded = False
        self.auto_retract_timer.stop()

        self._is_file_dragging_over = False
        self.external_drag_pin = None
        self.external_insert_slot = None
        self._recalc_slot_targets()

        self.slide_anim.stop()
        self.slide_anim.setStartValue(self._drawer_width)
        self.slide_anim.setEndValue(self.HANDLE_WIDTH)
        self.slide_anim.start()

    def toggle(self):
        """Ctrl+1 快捷键：彻底开启或关闭抽屉功能（防止不需要时误唤醒）"""
        self.set_feature_enabled(not self.drawer_enabled)
        return self.drawer_enabled

    def set_feature_enabled(self, enabled: bool):
        """设置抽屉功能总开关状态"""
        self.drawer_enabled = bool(enabled)
        if not self.drawer_enabled:
            self._hide_hover_preview()
            self._sync_clipboard_paste_hotkey(force_off=True)
            self.edge_radar_timer.stop()
            self.auto_retract_timer.stop()
            self.slide_anim.stop()
            self._is_expanded = False
            self._is_file_dragging_over = False
            self.external_drag_pin = None
            self.external_insert_slot = None
            self._drawer_width = self.HANDLE_WIDTH
            self._update_geometry()
            self.hide()
        else:
            self._is_expanded = False
            self._is_file_dragging_over = False
            self._drawer_width = self.HANDLE_WIDTH
            self._anchor_screen = QApplication.primaryScreen()
            self._update_geometry()
            self.show()
            self.ensure_on_top(force=True)
            self.edge_radar_timer.start()
        self.update()

    # ==========================================
    # 毫秒级边缘雷达光标检测 + Z序守卫
    # ==========================================
    def _check_edge_radar(self):
        if not self.drawer_enabled or getattr(self, "_boss_hidden", False):
            return

        self._zorder_tick = (self._zorder_tick + 1) % 10
        if self._zorder_tick == 0:
            self.ensure_on_top(force=False)

        self._sync_clipboard_paste_hotkey()

        if not self.hover_wake_enabled:
            return

        gpos = QCursor.pos()

        if not self._is_expanded:
            # 支持多屏：光标靠近当前所在屏幕右上边缘即锚定到该屏并展开；否则稳固停留在原锚定屏
            cursor_screen = QApplication.screenAt(gpos) or self._resolve_anchor_screen()
            avail = cursor_screen.availableGeometry() if cursor_screen else self._get_active_screen_rect()
            target_right = avail.right() + 1
            top_y = avail.top() + self.TOP_OFFSET
            dist_to_right = target_right - gpos.x()
            in_hotspot_y = (top_y - 15 <= gpos.y() <= top_y + self.DRAWER_HEIGHT + 20)
            if 0 <= dist_to_right <= 28 and in_hotspot_y:
                if cursor_screen is not None and cursor_screen is not self._anchor_screen:
                    self._anchor_screen = cursor_screen
                    self._update_geometry()
                self.expand()
        else:
            has_popup = (
                getattr(self, "_context_menu_open", False)
                or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None
            )
            if has_popup:
                self.auto_retract_timer.stop()
            elif not self.is_internal_dragging and not self.external_drag_pin and not self._is_file_dragging_over:
                drawer_rect = self.geometry()
                if not drawer_rect.adjusted(-12, -12, 12, 12).contains(gpos):
                    self._hide_hover_preview()
                    if not self.auto_retract_timer.isActive():
                        self.auto_retract_timer.start(160)
                else:
                    self.auto_retract_timer.stop()

    def _on_auto_retract_timeout(self):
        if (
            getattr(self, "_context_menu_open", False)
            or QApplication.activePopupWidget() is not None
            or QApplication.activeModalWidget() is not None
        ):
            return
        if self._is_expanded and not self.is_internal_dragging and not self.external_drag_pin and not self._is_file_dragging_over:
            gpos = QCursor.pos()
            if not self.geometry().adjusted(-8, -8, 8, 8).contains(gpos):
                self.retract()

    # ==========================================
    # 外部 PinnedWindow / 文件夹图片接近判定与拖入联动
    # ==========================================
    def check_proximity(self, global_pos):
        """判断光标是否靠近抽屉 (用于外部贴图自动缩小为 100x100 迷你卡片)"""
        if not self.drawer_enabled:
            return False
        screen = QApplication.screenAt(global_pos) or self._resolve_anchor_screen()
        avail = screen.availableGeometry() if screen else self._get_active_screen_rect()
        target_right = avail.right() + 1
        top_y = avail.top() + self.TOP_OFFSET
        dist_to_right = target_right - global_pos.x()
        dist_to_y = abs(global_pos.y() - (top_y + self.DRAWER_HEIGHT / 2))

        effective_w = max(280, (self._target_expanded_width + 50) if self._is_expanded else 280)
        effective_h = getattr(self, '_target_expanded_height', self.DRAWER_HEIGHT) if self._is_expanded else self.DRAWER_HEIGHT
        center_y = top_y + effective_h / 2.0
        dist_to_y = abs(global_pos.y() - center_y)
        return (0 <= dist_to_right <= effective_w and dist_to_y <= max(125.0, effective_h / 2.0 + 60.0))

    def on_external_pin_drag(self, pin_win, global_pos):
        """当外部 PinnedWindow 拖动至抽屉附近时联动"""
        if not self.drawer_enabled:
            return

        if not self._is_expanded:
            if self.check_proximity(global_pos):
                screen = QApplication.screenAt(global_pos)
                if screen is not None:
                    self._anchor_screen = screen
                    self._update_geometry()
                self.external_drag_pin = pin_win
                self.expand(exclude_win=pin_win)
                self._keep_dragged_windows_on_top(extra_win=pin_win)
            return

        drawer_rect = self.geometry()
        if drawer_rect.contains(global_pos):
            self.external_drag_pin = pin_win
            self._update_external_insert_slot(
                global_pos.x() - drawer_rect.x(),
                global_pos.y() - drawer_rect.y(),
            )
            self._keep_dragged_windows_on_top(extra_win=pin_win)
        else:
            if not self.check_proximity(global_pos):
                self.external_drag_pin = None
            else:
                self.external_drag_pin = pin_win
                self._keep_dragged_windows_on_top(extra_win=pin_win)
            if self.external_insert_slot is not None:
                self.external_insert_slot = None
                self._sync_expanded_layout(animate_width=True)

    def on_external_pin_released(self, pin_win, global_pos):
        """当外部 PinnedWindow 释放鼠标时调用"""
        if not self.drawer_enabled:
            return False

        drawer_rect = self.geometry()
        if self._is_expanded and drawer_rect.contains(global_pos) and pin_win.pixmap:
            ins_slot = self.external_insert_slot if self.external_insert_slot is not None else len(self.items)
            ins_slot = max(0, min(len(self.items), ins_slot))

            new_item = DrawerItem(
                item_id=None,
                pixmap=pin_win.pixmap,
                zoom_factor=getattr(pin_win, '_normal_zoom', getattr(pin_win, 'zoom_factor', 1.0))
            )
            nx, ny = self._slot_pos(ins_slot)
            new_item.current_x = nx
            new_item.target_x = nx
            new_item.current_y = ny
            new_item.target_y = ny
            self.items.insert(ins_slot, new_item)

            DrawerStorage.save_items(self.items)

            self.external_drag_pin = None
            self.external_insert_slot = None
            self._sync_expanded_layout(animate_width=False, focus_slot=ins_slot)

            pin_win._dropped_to_drawer = True
            pin_win.close()

            self.auto_retract_timer.start(300)
            self.update()
            return True

        self.external_drag_pin = None
        self.external_insert_slot = None
        if self._is_expanded:
            self._sync_expanded_layout(animate_width=False)
        else:
            self._recalc_expanded_width()
            self._recalc_slot_targets()
        if self._is_expanded and not drawer_rect.contains(QCursor.pos()):
            self.retract()
        return False

    def add_pin_directly(self, pin_win):
        """右键菜单一键直接存入抽屉"""
        if not pin_win or not pin_win.pixmap:
            return
        new_item = DrawerItem(
            item_id=None,
            pixmap=pin_win.pixmap,
            zoom_factor=getattr(pin_win, 'zoom_factor', 1.0)
        )
        self.items.append(new_item)
        DrawerStorage.save_items(self.items)
        new_slot = len(self.items) - 1
        self._recalc_expanded_width()
        self._scroll_slot_into_view(new_slot, immediate=True)
        nx, ny = self._slot_pos(new_slot)
        new_item.current_x = nx
        new_item.current_y = ny
        self._recalc_slot_targets()
        pin_win._dropped_to_drawer = True
        pin_win.close()
        if self.drawer_enabled:
            self.expand()
            self.auto_retract_timer.start(800)
        self.update()

    # ==========================================
    # 弹簧物理插值动画 (60fps)
    # ==========================================
    def _update_spring_physics(self):
        moving = False
        if abs(self.target_scroll_y - self.scroll_y) > 0.5:
            prev_scroll_y = self.scroll_y
            self.scroll_y += (self.target_scroll_y - self.scroll_y) * 0.3
            if abs(self.scroll_y - prev_scroll_y) > 0.01:
                self._recalc_slot_targets()
            moving = True
        else:
            if abs(self.scroll_y - self.target_scroll_y) > 0.01:
                self.scroll_y = self.target_scroll_y
                self._recalc_slot_targets()
            else:
                self.scroll_y = self.target_scroll_y

        for i, it in enumerate(self.items):
            if self.is_internal_dragging and i == self.dragging_internal_index:
                continue
            diff_x = it.target_x - it.current_x
            diff_y = it.target_y - it.current_y
            if abs(diff_x) > 0.5:
                it.current_x += diff_x * 0.38
                moving = True
            else:
                it.current_x = it.target_x

            if abs(diff_y) > 0.5:
                it.current_y += diff_y * 0.38
                moving = True
            else:
                it.current_y = it.target_y

        self.update()
        if not moving and not self.is_internal_dragging:
            self.physics_timer.stop()

    # ==========================================
    # 操作系统文件夹 / 外部图片拖入事件 (Drag & Drop)
    # ==========================================
    def dragEnterEvent(self, event):
        if not self.drawer_enabled or not _mime_has_candidate_images(event.mimeData()):
            event.ignore()
            return
        self._hide_hover_preview()
        self._is_file_dragging_over = True
        self.auto_retract_timer.stop()
        screen = QApplication.screenAt(QCursor.pos())
        if not self._is_expanded and screen is not None and screen is not self._anchor_screen:
            self._anchor_screen = screen
            self._update_geometry()
        if not self._is_expanded:
            self.expand()
        self._update_external_insert_slot(event.pos().x(), event.pos().y())
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def dragMoveEvent(self, event):
        if not self.drawer_enabled or not (self._is_file_dragging_over or _mime_has_candidate_images(event.mimeData())):
            event.ignore()
            return
        self._is_file_dragging_over = True
        self._update_external_insert_slot(event.pos().x(), event.pos().y())
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._is_file_dragging_over = False
        self._clear_external_insert_preview()
        if self._is_expanded:
            self.auto_retract_timer.start(220)
        event.accept()

    def _insert_pixmaps(self, pixmaps, ins_slot=None, keep_open_ms=900):
        """将一组 QPixmap 插入抽屉并持久化、同步展开与滚动定位（供拖入与 Ctrl+V 粘贴复用）。"""
        valid = [p for p in (pixmaps or []) if p is not None and not p.isNull()]
        if not valid:
            return 0
        if ins_slot is None:
            ins_slot = len(self.items)
        ins_slot = max(0, min(len(self.items), int(ins_slot)))

        for offset, pix in enumerate(valid):
            new_item = DrawerItem(item_id=None, pixmap=pix, zoom_factor=1.0)
            nx, ny = self._slot_pos(ins_slot + offset)
            new_item.current_x = nx
            new_item.target_x = nx
            new_item.current_y = ny
            new_item.target_y = ny
            self.items.insert(ins_slot + offset, new_item)

        DrawerStorage.save_items(self.items)
        self.external_drag_pin = None
        self.external_insert_slot = None
        last_inserted_slot = min(max(0, len(self.items) - 1), ins_slot + len(valid) - 1)
        self._sync_expanded_layout(animate_width=False, focus_slot=last_inserted_slot)
        for it in self.items:
            it.current_x = it.target_x
            it.current_y = it.target_y

        if self._frosted_bg_pixmap is None or self._frosted_bg_pixmap.width() < self._target_expanded_width:
            self._capture_and_blur_background()

        if self._is_expanded:
            self.slide_anim.stop()
            self.slide_anim.setStartValue(self._drawer_width)
            self.slide_anim.setEndValue(self._target_expanded_width)
            self.slide_anim.start()
        else:
            self.expand()

        if keep_open_ms > 0:
            self.auto_retract_timer.start(keep_open_ms)
        self.update()
        return len(valid)

    def dropEvent(self, event):
        self._is_file_dragging_over = False
        if not self.drawer_enabled:
            self._clear_external_insert_preview()
            event.ignore()
            return

        pixmaps = _extract_pixmaps_from_mimedata(event.mimeData())
        if not pixmaps:
            self._clear_external_insert_preview()
            if self._is_expanded:
                self.auto_retract_timer.start(220)
            event.ignore()
            return

        ins_slot = (
            self.external_insert_slot
            if self.external_insert_slot is not None
            else self._calc_slot_from_pos(event.pos().x(), event.pos().y(), for_insert=True)
        )
        self._insert_pixmaps(pixmaps, ins_slot=ins_slot, keep_open_ms=900)
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def _extract_item_to_pin(self, index, gpos):
        """从抽屉取出卡片，并在全局光标位置自动生成可拖动/缩放的桌面置顶贴图。"""
        if not (0 <= index < len(self.items)):
            return
        removed_item = self.items.pop(index)
        DrawerStorage.remove_item_file(removed_item)
        DrawerStorage.save_items(self.items)

        if self.main_app and hasattr(self.main_app, "create_pinned_window"):
            screen = QApplication.screenAt(gpos) or self._resolve_anchor_screen()
            avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
            zoom = float(removed_item.zoom_factor) if removed_item.zoom_factor else 1.0
            max_w = avail.width() * 0.85
            max_h = avail.height() * 0.85
            # 与 PinnedWindow 首屏自适应逻辑一致，确保文件夹高清大图也能精准居中于落点而非偏向左上角
            if zoom == 1.0 and (removed_item.width > max_w or removed_item.height > max_h):
                zoom = max(0.05, round(min(
                    max_w / max(1, removed_item.width),
                    max_h / max(1, removed_item.height),
                ), 3))
            pw = min(8192, max(20, int(removed_item.width * zoom)))
            ph = min(8192, max(20, int(removed_item.height * zoom)))
            px = max(avail.left(), min(avail.right() + 1 - pw, gpos.x() - pw // 2))
            py = max(avail.top(), min(avail.bottom() + 1 - ph, gpos.y() - ph // 2))
            new_pin = self.main_app.create_pinned_window(
                removed_item.pixmap,
                initial_pos=QPoint(px, py),
                zoom_factor=zoom
            )
            if new_pin:
                new_pin.raise_()

        self.hovered_card_index = None
        self.hovered_delete_btn_index = None
        self.dragging_internal_index = None
        self.is_internal_dragging = False
        self._recalc_expanded_width()
        self._recalc_slot_targets()
        self.retract()
        self.update()

    # ==========================================
    # 鼠标交互与事件处理
    # ==========================================
    def wheelEvent(self, event):
        if not self._is_expanded:
            return
        self._hide_hover_preview()
        cols, total_rows, visible_rows, _tw, target_h = self._get_grid_metrics()
        if total_rows <= visible_rows:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        self.target_scroll_y += delta * 0.8

        chrome_v = self.CARD_Y + self.SHADOW_PAD + 10
        content_h = chrome_v + total_rows * self.CARD_HEIGHT + max(0, total_rows - 1) * self.CARD_SPACING
        min_scroll_y = min(0.0, float(target_h) - float(content_h))
        self.target_scroll_y = max(min_scroll_y, min(0.0, self.target_scroll_y))
        if not self.physics_timer.isActive():
            self.physics_timer.start()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            if (event.buttons() & Qt.LeftButton) and (
                self.dragging_internal_index is not None or self.is_internal_dragging
            ):
                self._hide_hover_preview()
                self._file_drag_armed = True
                self._suppress_next_context_menu = True
                self.update()
                event.accept()
                return
            self._suppress_next_context_menu = False
            return

        if event.button() != Qt.LeftButton:
            return

        # 每次新的左键拖拽都强制回到默认模式（单次右键生效，绝不持久化）
        self._file_drag_armed = False
        self._suppress_next_context_menu = False
        self._hide_hover_preview()

        pos = event.pos()

        # 1. 点击卡片右上角的删除小圆钮 (✕)
        if self.hovered_delete_btn_index is not None and self.hovered_delete_btn_index < len(self.items):
            del_item = self.items.pop(self.hovered_delete_btn_index)
            DrawerStorage.remove_item_file(del_item)
            DrawerStorage.save_items(self.items)
            self.hovered_delete_btn_index = None
            if self._is_expanded:
                self._sync_expanded_layout(animate_width=False)
            else:
                self._recalc_expanded_width()
                self._recalc_slot_targets()
            self.update()
            return

        # 2. 点击卡片准备拖动排序或拖出桌面
        if self.hovered_card_index is not None and self.hovered_card_index < len(self.items):
            self.dragging_internal_index = self.hovered_card_index
            self.internal_drag_start_pos = event.globalPos()
            self.is_internal_dragging = False
            self.drag_offset_x = pos.x() - self.items[self.dragging_internal_index].current_x
            self.drag_offset_y = pos.y() - self.items[self.dragging_internal_index].current_y
            return

        if not self._is_expanded and self.drawer_enabled:
            self.expand()

    def mouseMoveEvent(self, event):
        pos = event.pos()
        gpos = event.globalPos()

        if self.dragging_internal_index is not None and self.dragging_internal_index < len(self.items):
            self._hide_hover_preview()
            if not self.is_internal_dragging:
                if (gpos - self.internal_drag_start_pos).manhattanLength() > 5:
                    self.is_internal_dragging = True
                    drag_item = self.items[self.dragging_internal_index]
                    self.drag_ghost_window = PinDragGhost(drag_item.pixmap)
                    if not self.physics_timer.isActive():
                        self.physics_timer.start()

            if self.is_internal_dragging:
                drag_item = self.items[self.dragging_internal_index]
                if not self.rect().contains(pos):
                    if self.drag_ghost_window:
                        gw = self.drag_ghost_window.width()
                        gh = self.drag_ghost_window.height()
                        self.drag_ghost_window.move(gpos.x() - gw // 2, gpos.y() - gh // 2)
                        if not self.drag_ghost_window.isVisible():
                            self.drag_ghost_window.show()
                        self._keep_dragged_windows_on_top(extra_win=self.drag_ghost_window)

                    drag_item.current_x = -9999.0
                    drag_item.current_y = -9999.0
                    for i, it in enumerate(self.items):
                        if i == self.dragging_internal_index:
                            continue
                        eff = i - 1 if i > self.dragging_internal_index else i
                        it.target_x, it.target_y = self._slot_pos(eff)
                else:
                    if self.drag_ghost_window and self.drag_ghost_window.isVisible():
                        self.drag_ghost_window.hide()

                    min_x = float(self.SHADOW_PAD) + 4.0
                    max_x = max(min_x, float(self.width()) - float(self.CARD_WIDTH) - 4.0)
                    min_y = float(self.SHADOW_PAD) + 4.0
                    max_y = max(min_y, float(self.height()) - float(self.SHADOW_PAD) - 4.0 - float(self.CARD_HEIGHT))
                    drag_item.current_x = max(min_x, min(max_x, float(pos.x() - self.drag_offset_x)))
                    drag_item.current_y = max(min_y, min(max_y, float(pos.y() - self.drag_offset_y)))
                    self._recalc_slot_targets()

                self.update()
                return

        prev_card = self.hovered_card_index
        prev_del = self.hovered_delete_btn_index
        self.hovered_card_index = None
        self.hovered_delete_btn_index = None

        if self._is_expanded:
            for i, it in enumerate(self.items):
                card_r = QRectF(it.current_x, it.current_y, self.CARD_WIDTH, self.CARD_HEIGHT)
                if card_r.contains(pos):
                    self.hovered_card_index = i
                    del_btn_r = QRectF(it.current_x + self.CARD_WIDTH - 20, it.current_y + 4, 16, 16)
                    if del_btn_r.contains(pos):
                        self.hovered_delete_btn_index = i
                    break

        if prev_card != self.hovered_card_index or prev_del != self.hovered_delete_btn_index:
            self.update()

        if (
            self._is_expanded
            and self.hovered_card_index is not None
            and self.hovered_delete_btn_index is None
            and not getattr(self, "_context_menu_open", False)
            and QApplication.activePopupWidget() is None
        ):
            self._schedule_hover_preview(self.hovered_card_index)
        else:
            self._hide_hover_preview()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            if self._file_drag_armed:
                self._file_drag_armed = False
                self._suppress_next_context_menu = True
                drag_idx = self.dragging_internal_index
                if drag_idx is not None and (event.buttons() & Qt.LeftButton):
                    QTimer.singleShot(0, lambda i=drag_idx: self._trigger_internal_card_file_drag(i))
                event.accept()
                return
            return

        if event.button() != Qt.LeftButton:
            return

        self._file_drag_armed = False

        if self.is_internal_dragging and self.dragging_internal_index is not None:
            gpos = event.globalPos()
            drag_item = self.items[self.dragging_internal_index]

            if self.drag_ghost_window:
                self.drag_ghost_window.close()
                self.drag_ghost_window = None

            if not self.rect().contains(event.pos()):
                self._extract_item_to_pin(self.dragging_internal_index, gpos)
                return

            target_slot = self._calc_slot_from_pos(
                drag_item.current_x + self.CARD_WIDTH / 2.0,
                drag_item.current_y + self.CARD_HEIGHT / 2.0,
            )
            target_slot = max(0, min(len(self.items) - 1, target_slot))

            item = self.items.pop(self.dragging_internal_index)
            self.items.insert(target_slot, item)
            DrawerStorage.save_items(self.items)

            self.dragging_internal_index = None
            self.is_internal_dragging = False
            self._recalc_slot_targets()
            self.update()
            return

        self.dragging_internal_index = None
        self.is_internal_dragging = False
        self._file_drag_armed = False

    # ==========================================
    # 悬停大图预览 + 抽屉内 Ctrl+V 粘贴 + 右键管理菜单
    # ==========================================
    def _hide_hover_preview(self):
        if hasattr(self, "hover_preview_timer") and self.hover_preview_timer.isActive():
            self.hover_preview_timer.stop()
        self.hover_preview_index = None
        if hasattr(self, "hover_preview") and self.hover_preview and self.hover_preview.isVisible():
            self.hover_preview.hide()

    def _schedule_hover_preview(self, index):
        if index is None or not (0 <= index < len(self.items)):
            self._hide_hover_preview()
            return
        if self.hover_preview.isVisible():
            if self.hover_preview_index != index:
                self._show_hover_preview_for(index)
            return
        if self.hover_preview_index != index or not self.hover_preview_timer.isActive():
            self.hover_preview_index = index
            self.hover_preview_timer.start()

    def _on_hover_preview_timeout(self):
        if (
            not self._is_expanded
            or self.is_internal_dragging
            or self.dragging_internal_index is not None
            or self.hovered_delete_btn_index is not None
            or getattr(self, "_context_menu_open", False)
            or QApplication.activePopupWidget() is not None
        ):
            return
        idx = self.hovered_card_index
        if idx is None or not (0 <= idx < len(self.items)):
            return
        self._show_hover_preview_for(idx)

    def _show_hover_preview_for(self, index):
        if not self._is_expanded or not (0 <= index < len(self.items)):
            self._hide_hover_preview()
            return
        it = self.items[index]
        self.hover_preview_index = index
        card_local = QRect(int(it.current_x), int(it.current_y), int(self.CARD_WIDTH), int(self.CARD_HEIGHT))
        card_global = QRect(self.mapToGlobal(card_local.topLeft()), card_local.size())
        screen = QApplication.screenAt(card_global.center()) or self._resolve_anchor_screen()
        avail = screen.availableGeometry() if screen else self._get_active_screen_rect()
        self.hover_preview.show_for_item(it, card_global, self.geometry(), avail)

    def _sync_clipboard_paste_hotkey(self, force_off=False):
        """仅在鼠标悬停于已展开抽屉内部且无弹窗时临时接管 Ctrl+V，离开即刻归还给系统。"""
        want_active = False
        if (
            not force_off
            and self.drawer_enabled
            and self.isVisible()
            and self._is_expanded
            and not getattr(self, "_context_menu_open", False)
            and QApplication.activeModalWidget() is None
            and QApplication.activePopupWidget() is None
        ):
            want_active = self.geometry().adjusted(-2, -2, 2, 2).contains(QCursor.pos())

        if want_active == self._paste_hotkey_registered:
            return

        user32 = ctypes.windll.user32
        try:
            hwnd = int(self.winId())
        except Exception:
            hwnd = 0

        if want_active and hwnd:
            MOD_CONTROL = 0x0002
            MOD_NOREPEAT = 0x4000
            VK_V = 0x56
            if user32.RegisterHotKey(hwnd, self._paste_hotkey_id, MOD_CONTROL | MOD_NOREPEAT, VK_V):
                self._paste_hotkey_registered = True
        else:
            if hwnd:
                try:
                    user32.UnregisterHotKey(hwnd, self._paste_hotkey_id)
                except Exception as e:
                    logging.debug("注销抽屉粘贴热键失败: %s", e)
            self._paste_hotkey_registered = False

    def set_boss_hidden(self, hidden: bool):
        """老板键临时隐藏/恢复抽屉，不改变用户持久化的启用配置。"""
        self._boss_hidden = bool(hidden)
        if self._boss_hidden:
            self._hide_hover_preview()
            self._sync_clipboard_paste_hotkey(force_off=True)
            self.auto_retract_timer.stop()
            self._is_expanded = False
            self._drawer_width = self.HANDLE_WIDTH
            self._update_geometry()
            self.hide()
        else:
            if self.drawer_enabled:
                self._drawer_width = self.HANDLE_WIDTH
                self._update_geometry()
                self.show()
                self.ensure_on_top(force=True)

    def nativeEvent(self, eventType, message):
        if self._paste_hotkey_registered and eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312 and int(msg.wParam) == self._paste_hotkey_id:
                    QTimer.singleShot(0, self.paste_from_clipboard)
                    return True, 0
            except Exception as e:
                logging.debug("PinDrawer nativeEvent 解析失败: %s", e)
        return super().nativeEvent(eventType, message)

    def keyPressEvent(self, event):
        if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_V:
            self.paste_from_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        self._sync_clipboard_paste_hotkey()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hide_hover_preview()
        self._sync_clipboard_paste_hotkey(force_off=True)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._hide_hover_preview()
        self._sync_clipboard_paste_hotkey(force_off=True)
        super().hideEvent(event)

    def closeEvent(self, event):
        self._hide_hover_preview()
        self._sync_clipboard_paste_hotkey(force_off=True)
        if hasattr(self, "hover_preview") and self.hover_preview:
            try:
                self.hover_preview.close()
            except Exception:
                pass
        super().closeEvent(event)

    def paste_from_clipboard(self):
        """将剪贴板中的最新图片（截图位图或复制的图片文件）直接粘贴存入抽屉。"""
        if not self.drawer_enabled:
            return 0
        cb = QApplication.clipboard()
        if cb is None:
            return 0
        mime = cb.mimeData()
        pixmaps = _extract_pixmaps_from_mimedata(mime) if mime is not None else []
        if not pixmaps:
            cb_pix = cb.pixmap()
            if cb_pix is not None and not cb_pix.isNull():
                pixmaps = [cb_pix]

        if not pixmaps:
            tip_pos = self.mapToGlobal(QPoint(max(24, self.width() // 2), min(self.height() - 12, self.CARD_Y + 24)))
            QToolTip.showText(tip_pos, "📋 剪贴板暂无可粘贴的图片", None, QRect(), 1800)
            return 0

        self._hide_hover_preview()
        count = self._insert_pixmaps(pixmaps, ins_slot=len(self.items), keep_open_ms=0)
        tip_pos = self.mapToGlobal(QPoint(max(24, self.width() // 2), min(self.height() - 12, self.CARD_Y + 24)))
        QToolTip.showText(tip_pos, f"📥 已将 {count} 张图片粘贴进抽屉", None, QRect(), 2000)
        return count

    def stitch_all_items(self, direction="vertical"):
        """将抽屉内所有图片按纵向（或横向）拼接成一张长图并生成桌面贴图 + 写入剪贴板。"""
        valid = [it.pixmap for it in self.items if it.pixmap and not it.pixmap.isNull()]
        if len(valid) < 2:
            return None

        gap = 8
        margin = 8
        max_side = 2400

        if direction == "vertical":
            target_w = min(max_side, max(p.width() for p in valid))
            scaled = []
            for p in valid:
                if p.width() != target_w and p.width() > 0:
                    scaled.append(p.scaledToWidth(target_w, Qt.SmoothTransformation))
                else:
                    scaled.append(p)
            total_w = target_w + margin * 2
            total_h = sum(p.height() for p in scaled) + gap * (len(scaled) - 1) + margin * 2
        else:
            target_h = min(max_side, max(p.height() for p in valid))
            scaled = []
            for p in valid:
                if p.height() != target_h and p.height() > 0:
                    scaled.append(p.scaledToHeight(target_h, Qt.SmoothTransformation))
                else:
                    scaled.append(p)
            total_h = target_h + margin * 2
            total_w = sum(p.width() for p in scaled) + gap * (len(scaled) - 1) + margin * 2

        canvas = QPixmap(max(1, total_w), max(1, total_h))
        canvas.fill(QColor(255, 255, 255))
        painter = QPainter(canvas)
        try:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            cursor_x = margin
            cursor_y = margin
            for p in scaled:
                painter.drawPixmap(cursor_x, cursor_y, p)
                if direction == "vertical":
                    cursor_y += p.height() + gap
                else:
                    cursor_x += p.width() + gap
        finally:
            painter.end()

        cb = QApplication.clipboard()
        if cb is not None:
            cb.setPixmap(canvas)
        QToolTip.showText(QCursor.pos(), f"🧩 已拼接 {len(valid)} 张图片并复制到剪贴板", None, QRect(), 2200)

        if self.main_app and hasattr(self.main_app, "create_pinned_window"):
            screen = QApplication.screenAt(QCursor.pos()) or self._resolve_anchor_screen()
            avail = screen.availableGeometry() if screen else self._get_active_screen_rect()
            center_pos = QPoint(
                avail.left() + max(40, (avail.width() - min(600, canvas.width())) // 2),
                avail.top() + max(40, (avail.height() - min(500, canvas.height())) // 2),
            )
            self.main_app.create_pinned_window(canvas, initial_pos=center_pos, zoom_factor=1.0)
        return canvas

    def contextMenuEvent(self, event):
        if (
            self._suppress_next_context_menu
            or self._file_drag_armed
            or self.is_internal_dragging
            or self.dragging_internal_index is not None
        ):
            self._suppress_next_context_menu = False
            event.accept()
            return

        if not self._is_expanded:
            event.ignore()
            return

        self._hide_hover_preview()
        pos = event.pos()
        gpos = event.globalPos()

        target_idx = None
        for i, it in enumerate(self.items):
            if QRectF(it.current_x, it.current_y, self.CARD_WIDTH, self.CARD_HEIGHT).contains(pos):
                target_idx = i
                break

        menu = QMenu(self)
        menu.setStyleSheet("""
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
        """)

        act_extract = act_copy_img = act_copy_file = act_save = act_ocr = act_del = None
        if target_idx is not None and 0 <= target_idx < len(self.items):
            item = self.items[target_idx]
            act_extract = menu.addAction("📌 放到桌面贴图")
            act_copy_img = menu.addAction("📋 复制原图")
            act_copy_file = menu.addAction("📄 复制为 PNG 文件")
            act_save = menu.addAction("💾 另存为...")
            if self.main_app and hasattr(self.main_app, "start_quick_ocr_session"):
                act_ocr = menu.addAction("🔤 OCR / 二维码识别")
            act_del = menu.addAction("🗑️ 从抽屉移除")
            menu.addSeparator()

        act_paste = menu.addAction("📥 粘贴剪贴板图片 (Ctrl+V)")
        act_stitch_v = act_stitch_h = act_clear_all = None
        if len(self.items) >= 2:
            act_stitch_v = menu.addAction("🧩 全部纵向拼成长图")
            act_stitch_h = menu.addAction("🧩 全部横向拼成组图")
        if len(self.items) >= 1:
            act_clear_all = menu.addAction("🧹 清空抽屉...")

        self._context_menu_open = True
        self.auto_retract_timer.stop()
        self._sync_clipboard_paste_hotkey(force_off=True)
        try:
            chosen = menu.exec_(gpos)
        finally:
            self._context_menu_open = False

        if chosen is None:
            return
        if target_idx is not None and 0 <= target_idx < len(self.items):
            pix = self.items[target_idx].pixmap
            if chosen == act_extract:
                self._extract_item_to_pin(target_idx, gpos)
                return
            elif chosen == act_copy_img and pix and not pix.isNull():
                QApplication.clipboard().setPixmap(pix)
                QToolTip.showText(gpos, "📋 原图已复制到剪贴板", None, QRect(), 1800)
                return
            elif chosen == act_copy_file and pix and not pix.isNull():
                if copy_pixmap_as_clipboard_file(pix):
                    QToolTip.showText(gpos, "📄 PNG 文件已复制到剪贴板（可直接 Ctrl+V 发送/上传）", None, QRect(), 2200)
                return
            elif chosen == act_save and pix and not pix.isNull():
                fn, _ = QFileDialog.getSaveFileName(
                    self, "保存抽屉图片", f"drawer_{time.strftime('%Y%m%d_%H%M%S')}.png",
                    "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)"
                )
                if fn:
                    pix.save(fn)
                return
            elif chosen == act_ocr and pix and not pix.isNull():
                self.retract()
                QTimer.singleShot(40, lambda p=pix: self.main_app.start_quick_ocr_session(QRect(), pre_pixmap=p))
                return
            elif chosen == act_del:
                removed = self.items.pop(target_idx)
                DrawerStorage.remove_item_file(removed)
                DrawerStorage.save_items(self.items)
                self._sync_expanded_layout(animate_width=False)
                self.update()
                return

        if chosen == act_paste:
            self.paste_from_clipboard()
        elif chosen == act_stitch_v:
            self.retract()
            self.stitch_all_items("vertical")
        elif chosen == act_stitch_h:
            self.retract()
            self.stitch_all_items("horizontal")
        elif chosen == act_clear_all:
            reply = QMessageBox.question(
                self, "清空抽屉", f"确定清空抽屉中的全部 {len(self.items)} 张图片吗？此操作不可撤销。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                for it in self.items:
                    DrawerStorage.remove_item_file(it)
                self.items.clear()
                DrawerStorage.save_items(self.items)
                self._sync_expanded_layout(animate_width=False)
                self.update()

    def _trigger_internal_card_file_drag(self, card_index):
        """将当前拖拽中的抽屉卡片切换为本地 PNG 文件拖拽，并支持拖拽途中使用右键在 PNG 与贴图间自由切换"""
        if card_index is None or card_index < 0 or card_index >= len(self.items):
            return
        pix = self.items[card_index].pixmap
        if pix is None or pix.isNull():
            return

        self.is_internal_dragging = False
        self.dragging_internal_index = None
        self.internal_drag_start_pos = None
        self.drag_target_slot = None
        self._file_drag_armed = False

        if self.drag_ghost_window:
            try:
                self.drag_ghost_window.close()
            except Exception:
                pass
            self.drag_ghost_window = None

        self._recalc_slot_targets()
        self.update()

        if self._is_expanded:
            self.retract()

        outcome, _drag_res, drop_pos = "file", None, QCursor.pos()
        try:
            outcome, _drag_res, drop_pos = run_toggleable_drag(
                self, pix, start_mode="file", copy_to_clipboard=True, file_drag_fn=start_pixmap_file_drag
            )
        finally:
            self._file_drag_armed = False
            self._suppress_next_context_menu = False

        if outcome == "drop_pin" and 0 <= card_index < len(self.items):
            screen = QApplication.screenAt(drop_pos) or self._resolve_anchor_screen()
            avail = screen.availableGeometry() if screen else self._get_active_screen_rect()
            if drop_pos.x() < avail.right() - self.HANDLE_WIDTH:
                self._extract_item_to_pin(card_index, drop_pos)

    def mouseDoubleClickEvent(self, event):
        """双击卡片直接取出放回桌面，抽屉立即回缩"""
        if event.button() == Qt.LeftButton and self.hovered_card_index is not None:
            idx = self.hovered_card_index
            if idx < len(self.items):
                self._extract_item_to_pin(idx, QCursor.pos())
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # ==========================================
    # 辅助：构建左圆角右贴边的抽屉路径
    # ==========================================
    @staticmethod
    def _build_left_rounded_path(left, top, right, bottom, radius):
        path = QPainterPath()
        r = min(radius, (bottom - top) / 2.0, (right - left) / 2.0)
        if r < 1.0:
            path.addRect(QRectF(left, top, right - left, bottom - top))
            return path
        path.moveTo(right, top)
        path.lineTo(left + r, top)
        path.quadTo(left, top, left, top + r)
        path.lineTo(left, bottom - r)
        path.quadTo(left, bottom, left + r, bottom)
        path.lineTo(right, bottom)
        path.closeSubpath()
        return path

    # ==========================================
    # 极简 macOS 冰晶毛玻璃渲染 (paintEvent)
    # ==========================================
    def _paint_collapsed_handle(self, painter, w, h):
        pill_rect = QRectF(4, 24, w - 6, h - 48)
        pill_path = QPainterPath()
        pill_path.addRoundedRect(pill_rect, 5, 5)

        shadow_p = QPainterPath()
        shadow_p.addRoundedRect(pill_rect.adjusted(-1.5, -1.5, 1.5, 1.5), 6, 6)
        painter.fillPath(shadow_p, QBrush(QColor(15, 23, 42, 40)))

        grad = QLinearGradient(0, pill_rect.top(), 0, pill_rect.bottom())
        grad.setColorAt(0.0, QColor(246, 249, 255, 230))
        grad.setColorAt(1.0, QColor(225, 232, 246, 228))
        painter.fillPath(pill_path, QBrush(grad))
        painter.setPen(QPen(QColor(100, 116, 139, 175), 1.0))
        painter.drawPath(pill_path)

        cx = pill_rect.center().x()
        painter.setPen(QPen(QColor(182, 134, 58, 242), 2.4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(cx, pill_rect.top() + 18), QPointF(cx, pill_rect.bottom() - 18))

    def _paint_drawer_glass(self, painter, left, top, right, bottom, radius):
        for i, alpha in enumerate([4, 7, 11, 16, 22]):
            offset = float(5 - i) * 1.3
            sp = self._build_left_rounded_path(left - offset, top - offset + 1.0, right, bottom + offset + 1.0, radius + offset)
            painter.fillPath(sp, QBrush(QColor(15, 23, 42, alpha)))

        drawer_path = self._build_left_rounded_path(left, top, right, bottom, radius)
        painter.save()
        painter.setClipPath(drawer_path)
        painter.fillPath(drawer_path, QBrush(QColor(235, 240, 248, 225)))

        if self._frosted_bg_pixmap and not self._frosted_bg_pixmap.isNull():
            bg_offset_x = self._frosted_bg_screen_x - self.x()
            bg_offset_y = getattr(self, '_frosted_bg_screen_y', self.y()) - self.y()
            painter.drawPixmap(int(bg_offset_x), int(bg_offset_y), self._frosted_bg_pixmap)

        glass_sheen = QLinearGradient(0, top, 0, bottom)
        glass_sheen.setColorAt(0.0, QColor(255, 255, 255, 112))
        glass_sheen.setColorAt(0.5, QColor(250, 249, 247, 52))
        glass_sheen.setColorAt(1.0, QColor(238, 236, 232, 82))
        painter.fillRect(QRectF(left, top, right - left, bottom - top), QBrush(glass_sheen))
        painter.restore()

        inner_path = self._build_left_rounded_path(left + 1.0, top + 1.0, right, bottom - 1.0, radius - 1.0)
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.4))
        painter.drawPath(inner_path)

        painter.setPen(QPen(QColor(100, 116, 139, 85), 1.0))
        painter.drawPath(drawer_path)

    def _paint_cards(self, painter, left, top, right, bottom, radius):
        vp_path = QPainterPath()
        vp_rect = QRectF(left + 2.0, top + 2.0, max(1.0, right - left - 4.0), max(1.0, bottom - top - 4.0))
        vp_path.addRoundedRect(vp_rect, max(4.0, radius - 2.0), max(4.0, radius - 2.0))
        painter.save()
        painter.setClipPath(vp_path)

        if self.external_insert_slot is not None:
            slot_x, slot_y = self._slot_pos(self.external_insert_slot)
            slot_rect = QRectF(slot_x, slot_y, self.CARD_WIDTH, self.CARD_HEIGHT)
            slot_path = QPainterPath()
            slot_path.addRoundedRect(slot_rect, 12, 12)
            painter.fillPath(slot_path, QBrush(QColor(250, 242, 224, 175)))
            painter.setPen(QPen(QColor(182, 134, 58, 220), 1.8, Qt.DashLine))
            painter.drawPath(slot_path)

        draw_indices = [
            i for i in range(len(self.items))
            if not (self.is_internal_dragging and self.dragging_internal_index == i)
        ]
        if self.is_internal_dragging and self.dragging_internal_index is not None and 0 <= self.dragging_internal_index < len(self.items):
            draw_indices.append(self.dragging_internal_index)

        for i in draw_indices:
            self._paint_single_card(painter, i, self.items[i])

        painter.restore()

    def _paint_single_card(self, painter, idx, it):
        card_x = it.current_x
        card_y = it.current_y
        is_hover = (self.hovered_card_index == idx)
        is_drag = (self.is_internal_dragging and self.dragging_internal_index == idx)
        card_rect = QRectF(card_x, card_y, self.CARD_WIDTH, self.CARD_HEIGHT)

        card_shadow = QPainterPath()
        card_shadow.addRoundedRect(card_rect.adjusted(0, 1.5, 0, 2.0), 12, 12)
        painter.fillPath(card_shadow, QBrush(QColor(15, 23, 42, 22)))

        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 12, 12)
        card_grad = QLinearGradient(0, card_rect.top(), 0, card_rect.bottom())
        if is_drag:
            card_grad.setColorAt(0.0, QColor(252, 247, 236, 236))
            card_grad.setColorAt(1.0, QColor(244, 232, 206, 220))
            card_border, border_width = QColor(182, 134, 58, 230), 1.8
        elif is_hover:
            card_grad.setColorAt(0.0, QColor(255, 255, 255, 238))
            card_grad.setColorAt(1.0, QColor(248, 245, 239, 210))
            card_border, border_width = QColor(196, 152, 76, 200), 1.5
        else:
            card_grad.setColorAt(0.0, QColor(255, 255, 255, 195))
            card_grad.setColorAt(1.0, QColor(241, 245, 252, 155))
            card_border, border_width = QColor(148, 163, 184, 110), 1.0

        painter.fillPath(card_path, QBrush(card_grad))
        inner_card = QPainterPath()
        inner_card.addRoundedRect(card_rect.adjusted(1.0, 1.0, -1.0, -1.0), 11, 11)
        painter.setPen(QPen(QColor(255, 255, 255, 230), 1.0))
        painter.drawPath(inner_card)
        painter.setPen(QPen(card_border, border_width))
        painter.drawPath(card_path)

        if it.thumbnail and not it.thumbnail.isNull():
            tw, th = it.thumbnail.width(), it.thumbnail.height()
            tx = card_x + (self.CARD_WIDTH - tw) / 2.0
            ty = card_y + (self.CARD_HEIGHT - th) / 2.0
            img_rect = QRectF(tx, ty, tw, th)
            painter.save()
            img_clip = QPainterPath()
            img_clip.addRoundedRect(img_rect, 6, 6)
            painter.setClipPath(img_clip, Qt.IntersectClip)
            painter.drawPixmap(int(tx), int(ty), it.thumbnail)
            painter.restore()
            painter.setPen(QPen(QColor(15, 23, 42, 32), 1.0))
            painter.drawRoundedRect(img_rect, 6, 6)

        if is_hover and not self.is_internal_dragging:
            del_rect = QRectF(card_x + self.CARD_WIDTH - 20, card_y + 4, 16, 16)
            del_path = QPainterPath()
            del_path.addEllipse(del_rect)
            is_del_hover = (self.hovered_delete_btn_index == idx)
            painter.fillPath(del_path, QBrush(QColor(239, 68, 68, 240 if is_del_hover else 185)))
            painter.setFont(_drawer_ui_font(7, QFont.DemiBold))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(del_rect, Qt.AlignCenter, "✕")

    def _paint_scrollbar(self, painter, right):
        _, total_rows, visible_rows, _tw, target_h = self._get_grid_metrics()
        if total_rows <= visible_rows:
            return
        chrome_v = self.CARD_Y + self.SHADOW_PAD + 10
        content_h = float(chrome_v + total_rows * self.CARD_HEIGHT + max(0, total_rows - 1) * self.CARD_SPACING)
        track_top = float(self.CARD_Y)
        track_h = max(24.0, float(target_h - chrome_v))
        thumb_h = max(24.0, track_h * (float(target_h) / max(content_h, float(target_h))))
        scroll_span = max(1.0, content_h - float(target_h))
        ratio = max(0.0, min(1.0, -self.scroll_y / scroll_span))
        thumb_y = track_top + (track_h - thumb_h) * ratio
        thumb_path = QPainterPath()
        thumb_path.addRoundedRect(QRectF(right - 7.5, thumb_y, 3.5, thumb_h), 1.75, 1.75)
        painter.fillPath(thumb_path, QBrush(QColor(71, 85, 105, 130)))

    def paintEvent(self, event):
        if not self.drawer_enabled:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        w, h = float(self.width()), float(self.height())
        if self._drawer_width <= self.HANDLE_WIDTH + 6:
            self._paint_collapsed_handle(painter, w, h)
            return

        pad = float(self.SHADOW_PAD)
        left, top, right, bottom, radius = pad, pad, w, h - pad, 18.0
        self._paint_drawer_glass(painter, left, top, right, bottom, radius)

        if not self.items and self.external_insert_slot is None:
            empty_r = QRectF(self.PADDING_LEFT, self.CARD_Y, w - self.PADDING_LEFT - self.PADDING_RIGHT, self.CARD_HEIGHT)
            empty_path = QPainterPath()
            empty_path.addRoundedRect(empty_r, 12, 12)
            painter.fillPath(empty_path, QBrush(QColor(255, 255, 255, 110)))
            painter.setPen(QPen(QColor(100, 116, 139, 90), 1.2, Qt.DashLine))
            painter.drawPath(empty_path)
            painter.setFont(_drawer_ui_font(9))
            painter.setPen(QColor(71, 85, 105))
            painter.drawText(empty_r, Qt.AlignCenter, "将贴图或图片拖至此处收纳")
            return

        self._paint_cards(painter, left, top, right, bottom, radius)
        self._paint_scrollbar(painter, right)
