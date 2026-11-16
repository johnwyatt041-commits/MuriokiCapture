# -*- coding: utf-8 -*-
"""
frosted_ui.py - 截图工具栏 & 托盘菜单 macOS 风格亮色冰晶毛玻璃组件
提供：
- FrostedBarWidget：用于截图工具栏 (SnipasteToolbar、ToolOptionsBar、CropToolbar)
- FrostedTrayMenu：用于系统托盘右键菜单的实时屏幕采样冰晶毛玻璃菜单
"""

import numpy as np
import cv2
from PyQt5.QtWidgets import QWidget, QMenu, QApplication
from PyQt5.QtCore import Qt, QRect, QRectF, QPoint
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QImage, QPainterPath,
    QLinearGradient
)


def create_frosted_pixmap(source_pixmap_or_image=None, width=100, height=100, screen_x=0, screen_y=0, tint_ratio=0.38):
    """
    基于源 Pixmap 或屏幕实时采样，生成与 Pin Drawer 视觉标准一致的 macOS 亮色冰晶毛玻璃 QPixmap。
    采用 4 倍降采样 + 双道大核高斯模糊 + 双三次升采样 + 亮色冰晶基底 (RGB: 236, 241, 249) 光学混色。
    默认保留 62% 真实背景色彩透射 + 38% 冰晶磨砂柔光；暗色背景自动适度提亮磨砂层以确保文字清晰可读。
    """
    try:
        w = max(16, int(width))
        h = max(16, int(height))

        grabbed = None
        dpr = 1.0
        if source_pixmap_or_image is not None and not source_pixmap_or_image.isNull():
            if isinstance(source_pixmap_or_image, QPixmap):
                dpr = float(source_pixmap_or_image.devicePixelRatio() or 1.0)
                qimg = source_pixmap_or_image.toImage().convertToFormat(QImage.Format_RGB888)
            else:
                dpr = float(source_pixmap_or_image.devicePixelRatio() or 1.0)
                qimg = source_pixmap_or_image.convertToFormat(QImage.Format_RGB888)
        else:
            screen = QApplication.screenAt(QPoint(screen_x, screen_y)) or QApplication.primaryScreen()
            if not screen:
                return None
            s_geo = screen.geometry()
            rel_x = int(screen_x - s_geo.x())
            rel_y = int(screen_y - s_geo.y())
            grabbed = screen.grabWindow(0, rel_x, rel_y, w, h)
            if grabbed.isNull():
                return None
            dpr = float(grabbed.devicePixelRatio() or 1.0)
            qimg = grabbed.toImage().convertToFormat(QImage.Format_RGB888)

        img_w, img_h = qimg.width(), qimg.height()
        if img_w < 8 or img_h < 8:
            return None

        ptr = qimg.bits()
        ptr.setsize(qimg.byteCount())
        arr = np.frombuffer(ptr, np.uint8).reshape((img_h, qimg.bytesPerLine()))[:, :img_w * 3].reshape((img_h, img_w, 3)).copy()

        mean_val = float(arr.mean())
        # 若在特殊受限会话下截屏为全黑，降级为雅致亮冰晶底色避免黑框
        if mean_val < 0.8:
            arr[:, :, 0] = 228
            arr[:, :, 1] = 235
            arr[:, :, 2] = 246
            eff_tint = float(tint_ratio)
        elif mean_val < 55.0:
            # 暗色背景（如深色任务栏/夜色壁纸）适度增加磨砂透光柔白比例，保证深色字体对比度清晰
            eff_tint = max(float(tint_ratio), 0.48)
        else:
            eff_tint = float(tint_ratio)

        # 4 倍降采样 (大尺寸性能优化)
        sw = max(8, img_w // 4)
        sh = max(8, img_h // 4)
        small = cv2.resize(arr, (sw, sh), interpolation=cv2.INTER_AREA)

        # 双道大核高斯模糊 (与 Pin Drawer 算法完全一致)
        blurred = cv2.GaussianBlur(small, (25, 25), 0)
        blurred = cv2.GaussianBlur(blurred, (15, 15), 0)

        # 双三次插值升采样还原
        up = cv2.resize(blurred, (img_w, img_h), interpolation=cv2.INTER_CUBIC)

        # 与 macOS 亮色冰晶毛玻璃基底 (RGB: 236, 241, 249) 光学混色
        frost_tint = np.empty_like(up, dtype=np.uint8)
        frost_tint[:, :, 0] = 236
        frost_tint[:, :, 1] = 241
        frost_tint[:, :, 2] = 249
        blended = cv2.addWeighted(up, 1.0 - eff_tint, frost_tint, eff_tint, 0)

        out_img = QImage(blended.data, img_w, img_h, img_w * 3, QImage.Format_RGB888).copy()
        out_pm = QPixmap.fromImage(out_img)
        if dpr != 1.0:
            out_pm.setDevicePixelRatio(dpr)
        return out_pm
    except Exception:
        return None


def draw_frosted_frame(painter, total_rect, radius=12.0, bg_pixmap=None, shadow_pad=8.0, is_active=True, bg_offset=None):
    """
    在指定 QPainter 上绘制完整的 macOS 风格毛玻璃圆角底板 (与 Pin Drawer 视觉标准一致)：
    - 多层柔和外阴影 (全部处于 shadow_pad 边缘内，四周 100% 透明无直角边)
    - 高斯模糊背景层 (物理透镜全局坐标投影，呈现真实光线折射)
    - 冰晶微光渐变层 (macOS Sheen)
    - 双层精致玻璃边缘线 (内层纯白折射高光 + 外层轻度微暗轮廓线)
    """
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    w = float(total_rect.width())
    h = float(total_rect.height())
    pad = float(shadow_pad)

    inner_x = pad
    inner_y = pad
    inner_w = max(4.0, w - 2 * pad)
    inner_h = max(4.0, h - 2 * pad)
    inner_rect = QRectF(inner_x, inner_y, inner_w, inner_h)

    # 1. 绘制多层柔和圆角阴影 (5 道层次渐进衰减，四周 100% 透明无直角黑框)
    if pad >= 4.0:
        alphas = [4, 7, 11, 16, 22]
        for i, a in enumerate(alphas):
            offset = float(len(alphas) - i) * (pad / float(len(alphas) + 1))
            sp = QPainterPath()
            sr = inner_rect.adjusted(-offset, -offset + 1.0, offset, offset + 1.0)
            sp.addRoundedRect(sr, radius + offset, radius + offset)
            painter.fillPath(sp, QBrush(QColor(15, 23, 42, a)))

    # 2. 玻璃主体圆角路径
    body_path = QPainterPath()
    body_path.addRoundedRect(inner_rect, radius, radius)

    painter.save()
    painter.setClipPath(body_path)

    # 绘制高斯模糊采样背景
    if bg_pixmap and not bg_pixmap.isNull():
        if bg_offset is not None:
            painter.drawPixmap(int(bg_offset.x()), int(bg_offset.y()), bg_pixmap)
        else:
            painter.drawPixmap(int(inner_x), int(inner_y), int(inner_w), int(inner_h), bg_pixmap)
    else:
        # 降级中性高级磨砂玻璃底色
        painter.fillPath(body_path, QBrush(QColor(250, 249, 247, 228)))

    # 叠加 macOS Sequoia 高级中性毛玻璃柔光渐变层
    glass_sheen = QLinearGradient(0, inner_y, 0, inner_y + inner_h)
    glass_sheen.setColorAt(0.0, QColor(255, 255, 255, 112))
    glass_sheen.setColorAt(0.5, QColor(250, 249, 247, 52))
    glass_sheen.setColorAt(1.0, QColor(238, 236, 232, 82))
    painter.fillRect(inner_rect, QBrush(glass_sheen))

    painter.restore()

    # 3. 双层精致玻璃轮廓线
    inner_stroke_r = inner_rect.adjusted(1.0, 1.0, -1.0, -1.0)
    inner_stroke_p = QPainterPath()
    inner_stroke_p.addRoundedRect(inner_stroke_r, max(1.0, radius - 1.0), max(1.0, radius - 1.0))
    painter.setPen(QPen(QColor(255, 255, 255, 220), 1.2))
    painter.drawPath(inner_stroke_p)

    painter.setPen(QPen(QColor(115, 113, 108, 85 if is_active else 50), 1.0))
    painter.drawPath(body_path)

    painter.restore()


class FrostedBarWidget(QWidget):
    """
    浮动磨砂工具栏/菜单栏基类 (用于 SnipasteToolbar、ToolOptionsBar、CropToolbar)
    - 预留 SHADOW_PAD = 8 像素透明边缘，绘制多层柔和圆角阴影，无生硬直角黑边
    - 动态自适应屏幕/选区背景坐标，拖动或位置变更时实时更新背景切片，物理透镜效果丝滑流动
    """
    SHADOW_PAD = 8
    CORNER_RADIUS = 10.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._frosted_bg = None

    def update_frosted_bg(self):
        try:
            pad = self.SHADOW_PAD
            w = max(16, self.width() - 2 * pad)
            h = max(16, self.height() - 2 * pad)
            parent = self.parent()

            # 1. 优先使用 ScreenshotTool 的预模糊全屏画面 (零截屏开销，极速秒切片)
            if parent is not None and hasattr(parent, 'blurred_screen') and parent.blurred_screen and not parent.blurred_screen.isNull():
                slice_rect = QRect(self.x() + pad, self.y() + pad, w, h)
                pm_rect = parent.blurred_screen.rect()
                intersected = slice_rect.intersected(pm_rect)
                if not intersected.isNull() and intersected.width() > 10 and intersected.height() > 10:
                    self._frosted_bg = parent.blurred_screen.copy(intersected)
                    self.update()
                    return

            # 2. 次选 ScreenshotTool 的原始全屏截屏 (进行局部实时高斯模糊)
            if parent is not None and hasattr(parent, 'original_screen') and parent.original_screen and not parent.original_screen.isNull():
                slice_rect = QRect(self.x() + pad, self.y() + pad, w, h)
                pm_rect = parent.original_screen.rect()
                intersected = slice_rect.intersected(pm_rect)
                if not intersected.isNull() and intersected.width() > 10 and intersected.height() > 10:
                    slice_pm = parent.original_screen.copy(intersected)
                    self._frosted_bg = create_frosted_pixmap(source_pixmap_or_image=slice_pm, width=w, height=h)
                    self.update()
                    return

            # 3. 独立悬浮模式：按全局屏幕坐标实时采样高斯模糊
            gpos = self.mapToGlobal(QPoint(pad, pad))
            self._frosted_bg = create_frosted_pixmap(
                width=w,
                height=h,
                screen_x=gpos.x(),
                screen_y=gpos.y()
            )
            self.update()
        except Exception:
            pass

    def moveEvent(self, event):
        super().moveEvent(event)
        self.update_frosted_bg()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_frosted_bg()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_frosted_bg()

    def paintEvent(self, event):
        painter = QPainter(self)
        parent = self.parent()
        bg_pm = None
        bg_offset = None

        # 优先使用父级全屏预模糊图 (数学级精准物理对齐，极速秒刷，动态移动绝不卡顿)
        if parent is not None and hasattr(parent, 'blurred_screen') and parent.blurred_screen and not parent.blurred_screen.isNull():
            bg_pm = parent.blurred_screen
            bg_offset = QPoint(-self.x(), -self.y())
        elif self._frosted_bg and not self._frosted_bg.isNull():
            bg_pm = self._frosted_bg
            bg_offset = None

        draw_frosted_frame(
            painter=painter,
            total_rect=self.rect(),
            radius=self.CORNER_RADIUS,
            bg_pixmap=bg_pm,
            shadow_pad=self.SHADOW_PAD,
            is_active=True,
            bg_offset=bg_offset
        )


class FrostedTrayMenu(QMenu):
    """
    右下角系统托盘右键菜单专属 macOS 风格亮色冰晶毛玻璃菜单：
    - 在 setVisible(True) 窗口映射显示之前瞬间采样目标桌面坐标像素，无自摄、无 Win11 直角黑框
    - 预留 SHADOW_PAD = 8 透明边距绘制多层柔和圆角阴影
    - 每次隐藏后自动清空旧采样缓存，确保下次在任意新窗口或任务栏上弹出均实时呈现真实动态底层毛玻璃
    """
    SHADOW_PAD = 8
    CORNER_RADIUS = 11.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._frosted_bg = None
        self.setStyleSheet("""
            QMenu {
                background: transparent;
                border: none;
                padding: 13px 12px;
                font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Emoji", sans-serif;
                font-size: 12.5px;
            }
            QMenu::item {
                background: transparent;
                padding: 6px 26px 6px 14px;
                border-radius: 7px;
                color: #18181B;
                margin: 1px 0px;
            }
            QMenu::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(250, 241, 222, 0.94), stop:1 rgba(240, 224, 190, 0.88));
                border: 1px solid rgba(182, 134, 58, 0.72);
                color: #7A5110;
                font-weight: 600;
            }
            QMenu::item:disabled {
                color: #9CA3AF;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(15, 23, 42, 0.12);
                margin: 5px 10px;
            }
        """)

    def _capture_bg_at_current_geo(self):
        try:
            pad = self.SHADOW_PAD
            geo = self.geometry()
            w = max(16, geo.width() - 2 * pad)
            h = max(16, geo.height() - 2 * pad)
            gx = geo.x() + pad
            gy = geo.y() + pad
            pm = create_frosted_pixmap(width=w, height=h, screen_x=gx, screen_y=gy, tint_ratio=0.40)
            if pm and not pm.isNull():
                self._frosted_bg = pm
        except Exception:
            pass

    def setVisible(self, visible):
        if visible and not self.isVisible():
            # Qt 在调用 setVisible(True) 前已计算好精确不超屏坐标 geo，且此时窗口尚未显示
            self._capture_bg_at_current_geo()
        super().setVisible(visible)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._frosted_bg = None

    def paintEvent(self, event):
        painter = QPainter(self)
        draw_frosted_frame(
            painter=painter,
            total_rect=self.rect(),
            radius=self.CORNER_RADIUS,
            bg_pixmap=self._frosted_bg,
            shadow_pad=self.SHADOW_PAD,
            is_active=True,
            bg_offset=None
        )
        painter.end()
        super().paintEvent(event)




