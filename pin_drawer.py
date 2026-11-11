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
import json
import time
import uuid
import ctypes
import logging
import cv2
import numpy as np
from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtCore import (
    Qt, QRect, QRectF, QPoint, QPointF, QSize, pyqtProperty,
    QPropertyAnimation, QEasingCurve, QTimer
)
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QImage, QFont,
    QPainterPath, QCursor, QLinearGradient
)

# 基础目录与持久化存储路径
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DRAWER_DIR = os.path.join(BASE_DIR, "capture_drawer")
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
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.pixmap = pixmap
        self.resize(108, 108)
        if pixmap and not pixmap.isNull():
            self.ghost_pixmap = pixmap.scaled(82, 82, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            self.ghost_pixmap = QPixmap()

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

        # macOS 亮色冰晶毛玻璃卡片底色
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, QColor(255, 255, 255, 235))
        grad.setColorAt(1.0, QColor(240, 246, 255, 215))
        painter.fillPath(path, QBrush(grad))

        painter.setPen(QPen(QColor(56, 189, 248, 230), 2.0))
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

        painter.setPen(QPen(QColor(15, 23, 42, 30), 1.0))
        painter.drawRoundedRect(img_rect, 6, 6)


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
        # 仅使用 Qt 原生透明背景，绝不调用会导致 Win11 直角黑框的旧版 SetWindowCompositionAttribute
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        # 抽屉宽度属性 (用于 QPropertyAnimation 平滑动画)
        self._drawer_width = self.HANDLE_WIDTH
        self._is_expanded = False
        self._target_expanded_width = 260

        # 抽屉功能总开关（通过 Ctrl+1 彻底开启或关闭整个抽屉功能）
        self.drawer_enabled = True
        self.hover_wake_enabled = True

        # 实时采样模糊后的桌面背景缓存
        self._frosted_bg_pixmap = None
        self._frosted_bg_screen_x = 0

        # 抽屉数据列表
        self.items = DrawerStorage.load_items()

        # 内部拖拽排序状态
        self.dragging_internal_index = None
        self.internal_drag_start_pos = None
        self.is_internal_dragging = False
        self.drag_offset_x = 0
        self.drag_ghost_window = None

        # 外部 PinnedWindow 拖入挤压预览状态
        self.external_drag_pin = None
        self.external_insert_slot = None

        # 鼠标悬停卡片与删除按钮索引
        self.hovered_card_index = None
        self.hovered_delete_btn_index = None

        # 横向滚轮滚动偏移量
        self.scroll_x = 0.0
        self.target_scroll_x = 0.0

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
        self._recalc_slot_targets()
        for it in self.items:
            it.current_x = it.target_x

        self._update_geometry()
        self.show()

    # ==========================================
    # 实时桌面背景采样 + 高斯磨砂模糊 (真正的 macOS 毛玻璃)
    # ==========================================
    def _capture_and_blur_background(self, exclude_win=None):
        """截取抽屉即将展开区域的屏幕画面，并进行高斯模糊与 macOS 冰晶亮色混色"""
        try:
            avail = self._get_active_screen_rect()
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            if not screen:
                return

            # 一次性预采样足够宽的右侧条带，确保拖入新卡片动态变宽时全程拥有无缝毛玻璃背景
            full_w = min(1100, max(self._target_expanded_width + 160, 520))
            full_h = self.DRAWER_HEIGHT
            full_x = avail.right() + 1 - full_w
            full_y = avail.top() + self.TOP_OFFSET

            # 临时将正在拖拽的贴图窗口从屏幕截图中剔除，避免将贴图自身摄入背景毛玻璃
            excl_hwnd = int(exclude_win.winId()) if exclude_win is not None else 0
            if excl_hwnd:
                try:
                    ctypes.windll.user32.SetWindowDisplayAffinity(excl_hwnd, 0x11)
                except Exception:
                    pass

            try:
                grabbed = screen.grabWindow(0, full_x, full_y, full_w, full_h)
            finally:
                if excl_hwnd:
                    try:
                        ctypes.windll.user32.SetWindowDisplayAffinity(excl_hwnd, 0x00)
                    except Exception:
                        pass

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
            else:
                # 抹除最右侧收起状态感应条自身的像素干扰，用左侧相邻背景自然延展填充
                handle_px = min(w - 2, self.HANDLE_WIDTH + 6)
                if handle_px > 0 and w > handle_px + 2:
                    arr[:, w - handle_px:] = arr[:, w - handle_px - 2:w - handle_px - 1]

            # 4 倍降采样 + 大核双道高斯模糊 + 双三次插值升采样，获得苹果级丝滑磨砂背景
            small_w = max(8, w // 4)
            small_h = max(8, h // 4)
            small = cv2.resize(arr, (small_w, small_h), interpolation=cv2.INTER_AREA)
            blurred = cv2.GaussianBlur(small, (25, 25), 0)
            blurred = cv2.GaussianBlur(blurred, (15, 15), 0)
            up = cv2.resize(blurred, (w, h), interpolation=cv2.INTER_CUBIC)

            # 与 macOS 亮色冰晶毛玻璃基底 (RGB: 236, 241, 249) 进行光学混色
            # 保留 48% 背景真实色彩透射 + 52% 冰晶磨砂柔光，彻底告别死黑，无论在白底还是暗底上都晶莹通透
            frost_tint = np.empty_like(up, dtype=np.uint8)
            frost_tint[:, :, 0] = 236
            frost_tint[:, :, 1] = 241
            frost_tint[:, :, 2] = 249
            blended = cv2.addWeighted(up, 0.48, frost_tint, 0.52, 0)

            out_qimg = QImage(blended.data, w, h, w * 3, QImage.Format_RGB888).copy()
            self._frosted_bg_pixmap = QPixmap.fromImage(out_qimg)
            self._frosted_bg_screen_x = full_x
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

    def _get_active_screen_rect(self):
        """获取当前光标所在屏幕的可用区域 (避开任务栏)"""
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    def _update_geometry(self):
        avail = self._get_active_screen_rect()
        target_right = avail.right() + 1
        x = target_right - self._drawer_width
        y = avail.top() + self.TOP_OFFSET
        self.setGeometry(x, y, self._drawer_width, self.DRAWER_HEIGHT)

    def _recalc_expanded_width(self):
        """根据当前截图张数智能计算紧凑展开宽度"""
        count = len(self.items)
        if self.external_insert_slot is not None:
            count += 1

        if count == 0:
            self._target_expanded_width = 230
            return

        needed = self.PADDING_LEFT + count * (self.CARD_WIDTH + self.CARD_SPACING) - self.CARD_SPACING + self.PADDING_RIGHT
        avail = self._get_active_screen_rect()
        max_w = min(1100, int(avail.width() * 0.85))
        self._target_expanded_width = max(140, min(max_w, needed))

    def _slot_x(self, slot_idx):
        """计算第 slot_idx 个卡片的标准基准 X 坐标"""
        return self.PADDING_LEFT + slot_idx * (self.CARD_WIDTH + self.CARD_SPACING) + self.scroll_x

    def _recalc_slot_targets(self):
        """根据挤压逻辑计算每个卡片的目标 X 坐标"""
        if self.is_internal_dragging and self.dragging_internal_index is not None:
            drag_idx = self.dragging_internal_index
            drag_x = self.items[drag_idx].current_x
            target_slot = self._calc_slot_from_x(drag_x + self.CARD_WIDTH / 2)

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
                it.target_x = self._slot_x(eff_slot)

        elif self.external_insert_slot is not None:
            ins = self.external_insert_slot
            for i, it in enumerate(self.items):
                eff_slot = i + 1 if i >= ins else i
                it.target_x = self._slot_x(eff_slot)

        else:
            for i, it in enumerate(self.items):
                it.target_x = self._slot_x(i)

        if not self.physics_timer.isActive():
            self.physics_timer.start()

    def _calc_slot_from_x(self, x):
        """根据 X 坐标计算对应的槽位索引 (0 ~ N)"""
        rel_x = x - self.PADDING_LEFT - self.scroll_x
        slot = int((rel_x + (self.CARD_WIDTH + self.CARD_SPACING) / 2) // (self.CARD_WIDTH + self.CARD_SPACING))
        return max(0, min(len(self.items), slot))

    # ==========================================
    # 展开、回缩与 Ctrl+1 功能总开关控制
    # ==========================================
    def expand(self, exclude_win=None):
        """向左优雅展开极简储藏柜"""
        if not self.drawer_enabled:
            return
        if self._is_expanded and self.slide_anim.state() == QPropertyAnimation.Stopped:
            return
        self._recalc_expanded_width()
        # 在展开前瞬间采样桌面背景生成高斯毛玻璃贴图
        if not self._is_expanded and self._drawer_width <= self.HANDLE_WIDTH + 6:
            self._capture_and_blur_background(exclude_win=exclude_win)

        self._is_expanded = True
        self.auto_retract_timer.stop()

        self.slide_anim.stop()
        self.slide_anim.setStartValue(self._drawer_width)
        self.slide_anim.setEndValue(self._target_expanded_width)
        self.slide_anim.start()

    def retract(self):
        """向右迅速回缩至屏幕右边缘"""
        if not self._is_expanded and self._drawer_width == self.HANDLE_WIDTH:
            return
        self._is_expanded = False
        self.auto_retract_timer.stop()

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
            self.edge_radar_timer.stop()
            self.auto_retract_timer.stop()
            self.slide_anim.stop()
            self._is_expanded = False
            self.external_drag_pin = None
            self.external_insert_slot = None
            self._drawer_width = self.HANDLE_WIDTH
            self._update_geometry()
            self.hide()
        else:
            self._is_expanded = False
            self._drawer_width = self.HANDLE_WIDTH
            self._update_geometry()
            self.show()
            self.edge_radar_timer.start()
        self.update()

    # ==========================================
    # 毫秒级边缘雷达光标检测
    # ==========================================
    def _check_edge_radar(self):
        if not self.drawer_enabled or not self.hover_wake_enabled:
            return

        gpos = QCursor.pos()
        avail = self._get_active_screen_rect()
        target_right = avail.right() + 1
        top_y = avail.top() + self.TOP_OFFSET

        if not self._is_expanded:
            dist_to_right = target_right - gpos.x()
            in_hotspot_y = (top_y - 15 <= gpos.y() <= top_y + self.DRAWER_HEIGHT + 20)
            if 0 <= dist_to_right <= 28 and in_hotspot_y:
                self.expand()
        else:
            if not self.is_internal_dragging and not self.external_drag_pin:
                drawer_rect = self.geometry()
                if not drawer_rect.adjusted(-12, -12, 12, 12).contains(gpos):
                    if not self.auto_retract_timer.isActive():
                        self.auto_retract_timer.start(160)
                else:
                    self.auto_retract_timer.stop()

    def _on_auto_retract_timeout(self):
        if self._is_expanded and not self.is_internal_dragging and not self.external_drag_pin:
            gpos = QCursor.pos()
            if not self.geometry().adjusted(-8, -8, 8, 8).contains(gpos):
                self.retract()

    # ==========================================
    # 外部 PinnedWindow 接近判定与拖入缩小联动
    # ==========================================
    def check_proximity(self, global_pos):
        """判断光标是否靠近抽屉 (用于外部贴图自动缩小为 100x100 迷你卡片)"""
        if not self.drawer_enabled:
            return False
        avail = self._get_active_screen_rect()
        target_right = avail.right() + 1
        top_y = avail.top() + self.TOP_OFFSET
        dist_to_right = target_right - global_pos.x()
        dist_to_y = abs(global_pos.y() - (top_y + self.DRAWER_HEIGHT / 2))

        effective_w = max(280, (self._target_expanded_width + 50) if self._is_expanded else 280)
        return (0 <= dist_to_right <= effective_w and dist_to_y <= 125)

    def on_external_pin_drag(self, pin_win, global_pos):
        """当外部 PinnedWindow 拖动至抽屉附近时联动"""
        if not self.drawer_enabled:
            return

        if not self._is_expanded:
            if self.check_proximity(global_pos):
                self.expand(exclude_win=pin_win)
            return

        drawer_rect = self.geometry()
        if drawer_rect.contains(global_pos):
            self.auto_retract_timer.stop()
            self.external_drag_pin = pin_win
            local_x = global_pos.x() - drawer_rect.x()
            new_slot = self._calc_slot_from_x(local_x)
            if new_slot != self.external_insert_slot:
                self.external_insert_slot = new_slot
                self._recalc_slot_targets()
                self._recalc_expanded_width()
                if self._drawer_width < self._target_expanded_width and self.slide_anim.state() == QPropertyAnimation.Stopped:
                    self.set_drawer_width(self._target_expanded_width)
        else:
            if self.external_insert_slot is not None:
                self.external_insert_slot = None
                self._recalc_slot_targets()

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
            new_item.current_x = self._slot_x(ins_slot)
            new_item.target_x = new_item.current_x
            self.items.insert(ins_slot, new_item)

            DrawerStorage.save_items(self.items)

            self.external_drag_pin = None
            self.external_insert_slot = None
            self._recalc_slot_targets()
            self._recalc_expanded_width()

            pin_win._dropped_to_drawer = True
            pin_win.close()

            self.auto_retract_timer.start(300)
            self.update()
            return True

        self.external_drag_pin = None
        self.external_insert_slot = None
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
        self._recalc_slot_targets()
        self._recalc_expanded_width()
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
        if abs(self.target_scroll_x - self.scroll_x) > 0.5:
            self.scroll_x += (self.target_scroll_x - self.scroll_x) * 0.3
            moving = True
        else:
            self.scroll_x = self.target_scroll_x

        for i, it in enumerate(self.items):
            if self.is_internal_dragging and i == self.dragging_internal_index:
                continue
            diff = it.target_x - it.current_x
            if abs(diff) > 0.5:
                it.current_x += diff * 0.38
                moving = True
            else:
                it.current_x = it.target_x

        self.update()
        if not moving and not self.is_internal_dragging:
            self.physics_timer.stop()

    # ==========================================
    # 鼠标交互与事件处理
    # ==========================================
    def wheelEvent(self, event):
        if not self._is_expanded:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        self.target_scroll_x += delta * 0.8

        total_w = self.PADDING_LEFT + len(self.items) * (self.CARD_WIDTH + self.CARD_SPACING) + self.PADDING_RIGHT
        max_scroll = 0.0
        min_scroll = min(0.0, self.width() - total_w)
        self.target_scroll_x = max(min_scroll, min(max_scroll, self.target_scroll_x))
        if not self.physics_timer.isActive():
            self.physics_timer.start()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        pos = event.pos()

        # 1. 点击卡片右上角的删除小圆钮 (✕)
        if self.hovered_delete_btn_index is not None and self.hovered_delete_btn_index < len(self.items):
            del_item = self.items.pop(self.hovered_delete_btn_index)
            DrawerStorage.remove_item_file(del_item)
            DrawerStorage.save_items(self.items)
            self.hovered_delete_btn_index = None
            self._recalc_slot_targets()
            self._recalc_expanded_width()
            if self._is_expanded:
                self.set_drawer_width(self._target_expanded_width)
            self.update()
            return

        # 2. 点击卡片准备拖动排序或拖出桌面
        if self.hovered_card_index is not None and self.hovered_card_index < len(self.items):
            self.dragging_internal_index = self.hovered_card_index
            self.internal_drag_start_pos = event.globalPos()
            self.is_internal_dragging = False
            self.drag_offset_x = pos.x() - self.items[self.dragging_internal_index].current_x
            return

        if not self._is_expanded and self.drawer_enabled:
            self.expand()

    def mouseMoveEvent(self, event):
        pos = event.pos()
        gpos = event.globalPos()

        if self.dragging_internal_index is not None and self.dragging_internal_index < len(self.items):
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

                    drag_item.current_x = -9999.0
                    for i, it in enumerate(self.items):
                        if i == self.dragging_internal_index:
                            continue
                        eff = i - 1 if i > self.dragging_internal_index else i
                        it.target_x = self._slot_x(eff)
                else:
                    if self.drag_ghost_window and self.drag_ghost_window.isVisible():
                        self.drag_ghost_window.hide()

                    drag_item.current_x = pos.x() - self.drag_offset_x
                    self._recalc_slot_targets()

                self.update()
                return

        prev_card = self.hovered_card_index
        prev_del = self.hovered_delete_btn_index
        self.hovered_card_index = None
        self.hovered_delete_btn_index = None

        if self._is_expanded:
            for i, it in enumerate(self.items):
                card_r = QRectF(it.current_x, self.CARD_Y, self.CARD_WIDTH, self.CARD_HEIGHT)
                if card_r.contains(pos):
                    self.hovered_card_index = i
                    del_btn_r = QRectF(it.current_x + self.CARD_WIDTH - 20, self.CARD_Y + 4, 16, 16)
                    if del_btn_r.contains(pos):
                        self.hovered_delete_btn_index = i
                    break

        if prev_card != self.hovered_card_index or prev_del != self.hovered_delete_btn_index:
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        if self.is_internal_dragging and self.dragging_internal_index is not None:
            gpos = event.globalPos()
            drag_item = self.items[self.dragging_internal_index]

            if self.drag_ghost_window:
                self.drag_ghost_window.close()
                self.drag_ghost_window = None

            if not self.rect().contains(event.pos()):
                removed_item = self.items.pop(self.dragging_internal_index)
                DrawerStorage.remove_item_file(removed_item)
                DrawerStorage.save_items(self.items)

                if self.main_app and hasattr(self.main_app, "create_pinned_window"):
                    pw = int(removed_item.width * removed_item.zoom_factor)
                    ph = int(removed_item.height * removed_item.zoom_factor)
                    spawn_pos = QPoint(max(0, gpos.x() - pw // 2), max(0, gpos.y() - ph // 2))
                    self.main_app.create_pinned_window(
                        removed_item.pixmap,
                        initial_pos=spawn_pos,
                        zoom_factor=removed_item.zoom_factor
                    )

                self.dragging_internal_index = None
                self.is_internal_dragging = False
                self._recalc_slot_targets()
                self._recalc_expanded_width()

                self.retract()
                self.update()
                return

            target_slot = self._calc_slot_from_x(drag_item.current_x + self.CARD_WIDTH / 2)
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

    def mouseDoubleClickEvent(self, event):
        """双击卡片直接取出放回桌面，抽屉立即回缩"""
        if event.button() == Qt.LeftButton and self.hovered_card_index is not None:
            idx = self.hovered_card_index
            if idx < len(self.items):
                removed_item = self.items.pop(idx)
                DrawerStorage.remove_item_file(removed_item)
                DrawerStorage.save_items(self.items)

                if self.main_app and hasattr(self.main_app, "create_pinned_window"):
                    cur_pos = QCursor.pos()
                    pw = int(removed_item.width * removed_item.zoom_factor)
                    ph = int(removed_item.height * removed_item.zoom_factor)
                    spawn_pos = QPoint(max(20, cur_pos.x() - pw // 2), max(20, cur_pos.y() - ph // 2))
                    self.main_app.create_pinned_window(
                        removed_item.pixmap,
                        initial_pos=spawn_pos,
                        zoom_factor=removed_item.zoom_factor
                    )

                self.hovered_card_index = None
                self._recalc_slot_targets()
                self._recalc_expanded_width()
                self.retract()
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
    def paintEvent(self, event):
        if not self.drawer_enabled:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # ----------------------------------------------------
        # 1. 收起状态时的边缘极简磨砂感应条 (无任何直角黑边)
        # ----------------------------------------------------
        if self._drawer_width <= self.HANDLE_WIDTH + 6:
            pill_rect = QRectF(4, 24, w - 6, h - 48)
            pill_path = QPainterPath()
            pill_path.addRoundedRect(pill_rect, 5, 5)

            # 柔和外发光阴影
            shadow_p = QPainterPath()
            shadow_p.addRoundedRect(pill_rect.adjusted(-1.5, -1.5, 1.5, 1.5), 6, 6)
            painter.fillPath(shadow_p, QBrush(QColor(15, 23, 42, 28)))

            # 冰晶亮色半透明胶囊
            grad = QLinearGradient(0, pill_rect.top(), 0, pill_rect.bottom())
            grad.setColorAt(0.0, QColor(245, 248, 255, 215))
            grad.setColorAt(1.0, QColor(225, 232, 246, 215))
            painter.fillPath(pill_path, QBrush(grad))
            painter.setPen(QPen(QColor(148, 163, 184, 140), 1.0))
            painter.drawPath(pill_path)

            # 居中精致指示条
            cx = pill_rect.center().x()
            painter.setPen(QPen(QColor(56, 189, 248, 230), 2.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(cx, pill_rect.top() + 18), QPointF(cx, pill_rect.bottom() - 18))
            return

        # ----------------------------------------------------
        # 2. 储藏柜主体：外圈多层柔和圆角阴影 + 真实高斯磨砂毛玻璃
        # ----------------------------------------------------
        pad = float(self.SHADOW_PAD)
        left = pad
        top = pad
        right = w
        bottom = h - pad
        radius = 18.0

        # 2.1 绘制外圈柔和多层投影 (全部在圆角路径内，四周 100% 透明无直角框)
        for i, alpha in enumerate([4, 7, 11, 16, 22]):
            offset = float(5 - i) * 1.3
            sp = self._build_left_rounded_path(left - offset, top - offset + 1.0, right, bottom + offset + 1.0, radius + offset)
            painter.fillPath(sp, QBrush(QColor(15, 23, 42, alpha)))

        # 2.2 主体圆角剪裁路径
        drawer_path = self._build_left_rounded_path(left, top, right, bottom, radius)

        painter.save()
        painter.setClipPath(drawer_path)

        # 绘制采样并高斯模糊后的屏幕背景 (与桌面绝对坐标对齐，滑动时具有真实物理透镜折射感)
        if self._frosted_bg_pixmap and not self._frosted_bg_pixmap.isNull():
            bg_offset_x = self._frosted_bg_screen_x - self.x()
            painter.drawPixmap(int(bg_offset_x), 0, self._frosted_bg_pixmap)
        else:
            painter.fillPath(drawer_path, QBrush(QColor(235, 240, 248, 225)))

        # 叠加 macOS 冰晶毛玻璃柔光渐变层 (亮色通透质感，彻底告别黑漆漆)
        glass_sheen = QLinearGradient(0, top, 0, bottom)
        glass_sheen.setColorAt(0.0, QColor(255, 255, 255, 115))
        glass_sheen.setColorAt(0.5, QColor(245, 248, 255, 75))
        glass_sheen.setColorAt(1.0, QColor(225, 234, 248, 105))
        painter.fillRect(QRectF(left, top, right - left, bottom - top), QBrush(glass_sheen))

        painter.restore()

        # 2.3 双层精致玻璃高光边缘：外层微暗轮廓线确保在纯白网页上依然边界分明，内层纯白高光展现玻璃折射
        inner_path = self._build_left_rounded_path(left + 1.0, top + 1.0, right, bottom - 1.0, radius - 1.0)
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.4))
        painter.drawPath(inner_path)

        painter.setPen(QPen(QColor(100, 116, 139, 85), 1.0))
        painter.drawPath(drawer_path)

        # ----------------------------------------------------
        # 3. 内部截图卡片渲染 (每张图都有专属清晰毛玻璃底框)
        # ----------------------------------------------------
        if not self.items and self.external_insert_slot is None:
            empty_r = QRectF(self.PADDING_LEFT, self.CARD_Y, w - self.PADDING_LEFT - self.PADDING_RIGHT, self.CARD_HEIGHT)
            empty_path = QPainterPath()
            empty_path.addRoundedRect(empty_r, 12, 12)
            painter.fillPath(empty_path, QBrush(QColor(255, 255, 255, 110)))
            painter.setPen(QPen(QColor(100, 116, 139, 90), 1.2, Qt.DashLine))
            painter.drawPath(empty_path)

            painter.setFont(QFont("Microsoft YaHei UI", 9))
            painter.setPen(QColor(71, 85, 105))
            painter.drawText(empty_r, Qt.AlignCenter, "将贴图拖至此处收纳")
            return

        # 绘制外部拖入挤压预览占位槽
        if self.external_insert_slot is not None:
            slot_x = self._slot_x(self.external_insert_slot)
            slot_rect = QRectF(slot_x, self.CARD_Y, self.CARD_WIDTH, self.CARD_HEIGHT)
            slot_path = QPainterPath()
            slot_path.addRoundedRect(slot_rect, 12, 12)
            painter.fillPath(slot_path, QBrush(QColor(224, 242, 254, 160)))
            painter.setPen(QPen(QColor(14, 165, 233, 210), 1.8, Qt.DashLine))
            painter.drawPath(slot_path)

        # 绘制所有截图卡片
        for i, it in enumerate(self.items):
            card_x = it.current_x
            is_hover = (self.hovered_card_index == i)
            is_drag = (self.is_internal_dragging and self.dragging_internal_index == i)

            card_rect = QRectF(card_x, self.CARD_Y, self.CARD_WIDTH, self.CARD_HEIGHT)

            # 卡片底部微投影
            card_shadow = QPainterPath()
            card_shadow.addRoundedRect(card_rect.adjusted(0, 1.5, 0, 2.0), 12, 12)
            painter.fillPath(card_shadow, QBrush(QColor(15, 23, 42, 22)))

            card_path = QPainterPath()
            card_path.addRoundedRect(card_rect, 12, 12)

            # 每张截图专属清晰毛玻璃底框
            card_grad = QLinearGradient(0, card_rect.top(), 0, card_rect.bottom())
            if is_drag:
                card_grad.setColorAt(0.0, QColor(240, 249, 255, 230))
                card_grad.setColorAt(1.0, QColor(224, 242, 254, 210))
                card_border = QColor(14, 165, 233, 220)
                border_width = 1.8
            elif is_hover:
                card_grad.setColorAt(0.0, QColor(255, 255, 255, 235))
                card_grad.setColorAt(1.0, QColor(245, 248, 255, 205))
                card_border = QColor(56, 189, 248, 190)
                border_width = 1.5
            else:
                card_grad.setColorAt(0.0, QColor(255, 255, 255, 195))
                card_grad.setColorAt(1.0, QColor(241, 245, 252, 155))
                card_border = QColor(148, 163, 184, 110)
                border_width = 1.0

            painter.fillPath(card_path, QBrush(card_grad))

            # 卡片内圈纯白玻璃高光线
            inner_card = QPainterPath()
            inner_card.addRoundedRect(card_rect.adjusted(1.0, 1.0, -1.0, -1.0), 11, 11)
            painter.setPen(QPen(QColor(255, 255, 255, 230), 1.0))
            painter.drawPath(inner_card)

            # 卡片外圈清晰轮廓线
            painter.setPen(QPen(card_border, border_width))
            painter.drawPath(card_path)

            # 居中圆角绘制高清缩略图 (附带细微边框让纯白截图也清晰可辨)
            if it.thumbnail and not it.thumbnail.isNull():
                tw = it.thumbnail.width()
                th = it.thumbnail.height()
                tx = card_x + (self.CARD_WIDTH - tw) / 2.0
                ty = self.CARD_Y + (self.CARD_HEIGHT - th) / 2.0
                img_rect = QRectF(tx, ty, tw, th)

                painter.save()
                img_clip = QPainterPath()
                img_clip.addRoundedRect(img_rect, 6, 6)
                painter.setClipPath(img_clip)
                painter.drawPixmap(int(tx), int(ty), it.thumbnail)
                painter.restore()

                painter.setPen(QPen(QColor(15, 23, 42, 32), 1.0))
                painter.drawRoundedRect(img_rect, 6, 6)

            # 悬停时在卡片右上角显示精致删除小圆钮 (✕)
            if is_hover:
                del_rect = QRectF(card_x + self.CARD_WIDTH - 20, self.CARD_Y + 4, 16, 16)
                del_path = QPainterPath()
                del_path.addEllipse(del_rect)

                is_del_hover = (self.hovered_delete_btn_index == i)
                del_bg = QColor(239, 68, 68, 240 if is_del_hover else 185)
                painter.fillPath(del_path, QBrush(del_bg))
                painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Bold))
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(del_rect, Qt.AlignCenter, "✕")
