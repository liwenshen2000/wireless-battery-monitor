"""PySide6 图形界面。"""

from __future__ import annotations

import sys
from typing import Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .device_events import DeviceChangeWatcher
from .models import (
    LOW_BATTERY_THRESHOLD,
    Connection,
    DeviceKind,
    ScanReport,
    WirelessDevice,
)
from .scanner import scan

AUTO_REFRESH_SECONDS = 30
AUTO_REFRESH_HINT = f"每 {AUTO_REFRESH_SECONDS} 秒 · 插拔即更新"

INK = "#203028"
ACCENT = "#277B58"
GREEN = "#2D8C65"
AMBER = "#C98A2E"
RED = "#D2574A"

KIND_LABELS = {
    DeviceKind.MOUSE: "鼠标",
    DeviceKind.KEYBOARD: "键盘",
    DeviceKind.HEADSET: "耳机",
    DeviceKind.GAMEPAD: "手柄",
    DeviceKind.PHONE: "手机",
    DeviceKind.TABLET: "平板",
    DeviceKind.SPEAKER: "音箱",
    DeviceKind.WATCH: "手表",
    DeviceKind.OTHER: "无线设备",
}


def _font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "Segoe UI"])
    font.setPointSize(size)
    font.setWeight(weight)
    return font


def _battery_color(level: int) -> QColor:
    if level <= LOW_BATTERY_THRESHOLD:
        return QColor(RED)
    if level <= 50:
        return QColor(AMBER)
    return QColor(GREEN)


class ScanTask(QThread):
    result = Signal(object)

    def run(self) -> None:
        try:
            report = scan()
        except Exception as exc:  # 后台线程中的意外错误也反馈到界面
            report = ScanReport(errors=(f"扫描失败：{exc}",))
        self.result.emit(report)


class BrandMark(QWidget):
    """圆角渐变底色的电池标识。"""

    def __init__(self, size: int = 46, radius: int = 14, parent=None) -> None:
        super().__init__(parent)
        self._radius = radius
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        height = self.height()

        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0.0, QColor("#1E7455"))
        gradient.setColorAt(1.0, QColor("#3DA379"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(
            QRectF(0, 0, width, height), self._radius, self._radius
        )

        scale = width / 46.0
        body = QRectF(13 * scale, 17 * scale, 17 * scale, 12 * scale)
        pen = QPen(QColor(255, 255, 255, 235))
        pen.setWidthF(max(1.4, 1.8 * scale))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, 2.6 * scale, 2.6 * scale)
        painter.drawRoundedRect(
            QRectF(30 * scale, 20 * scale, 2.6 * scale, 6 * scale),
            1.2 * scale,
            1.2 * scale,
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 220))
        fill = body.adjusted(2.5 * scale, 2.5 * scale, 0, -2.5 * scale)
        fill.setWidth(body.width() * 0.58)
        painter.drawRoundedRect(fill, 1.2 * scale, 1.2 * scale)


class BatteryRing(QWidget):
    def __init__(
        self,
        level: int | None,
        charging: bool,
        full: bool,
        offline: bool = False,
        size: int = 48,
        parent=None,
    ):
        super().__init__(parent)
        self._level = None if level is None else max(0, min(100, level))
        self._charging = charging
        self._full = full
        self._offline = offline
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = self._size
        margin = size * 0.11
        rect = QRectF(
            margin, margin, size - margin * 2, size - margin * 2
        )
        stroke = max(3.0, size * 0.085)

        if self._level is None or self._offline:
            track_pen = QPen(
                QColor("#DCE3DD" if self._level is None else "#C9D2CA"),
                max(2.0, size * 0.055),
            )
            track_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(track_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
            painter.setPen(QColor("#98A49C"))
            painter.setFont(
                _font(max(8, round(size * 0.21)), QFont.Weight.DemiBold)
            )
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "—" if self._level is None else f"{self._level}%",
            )
            return

        track_pen = QPen(QColor("#E7ECE6"), stroke)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 90 * 16, -360 * 16)

        color = _battery_color(self._level)
        level_pen = QPen(color, stroke)
        level_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(level_pen)
        painter.drawArc(
            rect, 90 * 16, -round(360 * 16 * (self._level / 100))
        )

        painter.setPen(QColor(color).darker(118))
        painter.setFont(
            _font(max(8, round(size * 0.19)), QFont.Weight.DemiBold)
        )
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            f"{self._level}%",
        )


class ElidedLabel(QLabel):
    def __init__(
        self, text: str, color: str = "#1D2C24", parent=None
    ) -> None:
        super().__init__(parent)
        self._full_text = text
        self._color = color
        self.setToolTip(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QColor(self._color))
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            self.width(),
        )
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )


class SummaryTile(QFrame):
    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("summaryTile")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setFixedHeight(76)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(1)

        title_label = QLabel(title)
        title_label.setObjectName("summaryTitle")
        self.value_label = QLabel("—")
        self.value_label.setFont(_font(18, QFont.Weight.DemiBold))
        self.value_label.setStyleSheet(f"color: {INK};")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("summarySub")

        layout.addWidget(title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)

    def set_value(self, text: str, color: str = INK) -> None:
        self.value_label.setText(text)
        self.value_label.setStyleSheet(f"color: {color};")


def _chip(text: str, foreground: str, background: str) -> QLabel:
    chip = QLabel(text)
    chip.setStyleSheet(
        f"color: {foreground}; background: {background};"
        "border-radius: 6px; padding: 2px 6px; font-size: 9px;"
    )
    return chip


class NoticeBanner(QFrame):
    def __init__(self, text: str, tone: str, parent=None) -> None:
        super().__init__(parent)
        colors = {
            "info": ("#EFF5F8", "#D5E3EB", "#3C7199"),
            "warning": ("#FBF3E5", "#E9C98D", "#8A5A12"),
            "danger": ("#FCEDEA", "#F1C6BF", "#A33F34"),
        }
        background, border, foreground = colors[tone]
        self.setStyleSheet(
            f"background: {background}; border: 1px solid {border};"
            "border-radius: 12px;"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 6, 11, 6)
        layout.setSpacing(8)

        dot = QLabel()
        dot.setFixedSize(7, 7)
        dot.setStyleSheet(
            f"background: {foreground}; border-radius: 3px;"
        )
        message = QLabel(text)
        message.setWordWrap(True)
        message.setStyleSheet(
            f"color: {foreground}; font-size: 10px; background: transparent;"
        )
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(message, 1)


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._checked = checked
        self._position = 1.0 if checked else 0.0
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("自动刷新")

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, emit: bool = True) -> None:
        if checked == self._checked:
            return
        self._checked = checked
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()
        if emit:
            self.toggled.emit(checked)

    def _on_animation(self, value) -> None:
        self._position = float(value)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        position = self._position
        off = QColor("#D5DDD6")
        on = QColor(ACCENT)
        track = QColor(
            round(off.red() + (on.red() - off.red()) * position),
            round(off.green() + (on.green() - off.green()) * position),
            round(off.blue() + (on.blue() - off.blue()) * position),
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(1, 1, 38, 20), 10, 10)

        knob_x = 3 + position * 18
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(knob_x, 3, 16, 16))


def _connection_colors(connection: Connection) -> tuple[str, str]:
    if connection is Connection.BLUETOOTH:
        return "#3C7199", "#EAF2F8"
    if connection is Connection.WIRELESS_24G:
        return "#9A6A1E", "#FBF1DF"
    return "#5C6C62", "#EFF3EE"


class DeviceCard(QFrame):
    """紧凑卡片：一屏可平铺展示多台设备。"""

    def __init__(self, device: WirelessDevice, parent=None) -> None:
        super().__init__(parent)
        if not device.connected:
            self.setObjectName("deviceCardOffline")
        elif device.is_low:
            self.setObjectName("deviceCardLow")
        else:
            self.setObjectName("deviceCard")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setFixedHeight(76)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14 if device.connected else 0)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(34, 61, 45, 16))
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        layout.addWidget(
            BatteryRing(
                device.battery,
                device.charging,
                device.full,
                offline=not device.connected,
                size=48,
            ),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        center = QVBoxLayout()
        center.setSpacing(3)
        center.setContentsMargins(0, 0, 0, 0)

        name = ElidedLabel(
            device.name,
            color="#1D2C24" if device.connected else "#7C887F",
        )
        name.setFont(_font(9, QFont.Weight.DemiBold))
        center.addWidget(name)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(4)
        foreground, background = _connection_colors(device.connection)
        meta_row.addWidget(
            _chip(device.connection.label, foreground, background)
        )
        meta_row.addWidget(
            _chip(
                KIND_LABELS.get(device.kind, "无线设备"),
                "#5C6C62",
                "#EFF3EE",
            )
        )
        if device.estimated and device.connected:
            meta_row.addWidget(_chip("估算", "#9A6A1E", "#FBF1DF"))

        if device.components and device.connected:
            summary = ElidedLabel(
                " · ".join(
                    f"{component.label} {component.battery}%"
                    for component in device.components
                ),
                color="#5C6C62",
            )
            summary.setFont(_font(9))
        else:
            summary = ElidedLabel(device.stack, color="#8C9891")
            summary.setFont(_font(9))
        meta_row.addWidget(summary, 1)
        center.addLayout(meta_row)
        layout.addLayout(center, 1)

        right = QVBoxLayout()
        right.setSpacing(1)
        right.setContentsMargins(0, 0, 0, 0)
        right.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        if not device.connected:
            status_text, status_fg, status_bg = (
                "未连接",
                "#7C887F",
                "#EEF1EE",
            )
        elif device.battery is None:
            status_text, status_fg, status_bg = (
                "未上报",
                "#7A6A4F",
                "#F4EFE4",
            )
        elif device.full:
            status_text, status_fg, status_bg = (
                "已充满",
                "#267B55",
                "#E8F4EC",
            )
        elif device.charging:
            status_text, status_fg, status_bg = (
                "充电中",
                "#267B55",
                "#E8F4EC",
            )
        elif device.is_low:
            status_text, status_fg, status_bg = (
                "电量偏低",
                "#B14438",
                "#FBEAE7",
            )
        else:
            status_text, status_fg, status_bg = (
                "已连接",
                "#5B6B61",
                "#EFF3EE",
            )
        status = QLabel(status_text)
        status.setStyleSheet(
            f"color: {status_fg}; background: {status_bg};"
            "border-radius: 7px; padding: 3px 8px; font-size: 9px;"
            "font-weight: 600;"
        )
        right.addWidget(status, 0, Qt.AlignmentFlag.AlignRight)

        if not device.connected:
            remembered = QLabel(
                f"上次 {device.battery}%"
                if device.battery is not None
                else "无电量记录"
            )
            remembered.setObjectName("cardSource")
            right.addWidget(remembered, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(right)

        detail_parts = [device.name, f"连接方式：{device.connection.label}"]
        if device.address:
            detail_parts.append(f"MAC {device.address}")
        if device.detail:
            detail_parts.append(device.detail)
        detail_parts.append(f"连接栈：{device.stack}")
        if device.components:
            detail_parts.append(
                "部件电量："
                + "，".join(
                    f"{component.label} {component.battery}%"
                    for component in device.components
                )
            )
        if device.last_seen:
            detail_parts.append(
                f"最后在线 {device.last_seen:%Y-%m-%d %H:%M}"
            )
        if not device.connected:
            detail_parts.append("该设备当前不在线")
        elif device.battery is None:
            detail_parts.append(
                "系统未提供电量：蓝牙 Xbox 模式的手柄通常不上报电量，"
                "GameSir 启明星 2 可插入 2.4G 接收器或改用 USB 连接读取；"
                "部分蓝牙键盘 / 耳机也没有电池服务"
            )
        self.setToolTip("\n".join(detail_parts))


class SectionHeader(QWidget):
    def __init__(
        self,
        title: str,
        count: int,
        meta: str,
        dot_color: str,
        trailing: QWidget | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 0)
        layout.setSpacing(8)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background: {dot_color}; border-radius: 4px;"
        )
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        count_label = QLabel(str(count))
        count_label.setObjectName("sectionCount")
        meta_label = QLabel(meta)
        meta_label.setObjectName("sectionMeta")

        layout.addWidget(dot)
        layout.addWidget(title_label)
        layout.addWidget(count_label)
        layout.addStretch(1)
        layout.addWidget(meta_label)
        if trailing is not None:
            layout.addWidget(trailing)


class EmptyState(QFrame):
    def __init__(
        self,
        title: str,
        body: str,
        button_text: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("emptyCard")
        self.setMinimumHeight(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        mark = BrandMark(56, radius=17)
        heading = QLabel(title)
        heading.setObjectName("emptyTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message = QLabel(body)
        message.setObjectName("emptyBody")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        message.setMaximumWidth(560)

        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(heading)
        layout.addWidget(message)

        if button_text:
            self.button = QPushButton(button_text)
            self.button.setObjectName("ghostButton")
            self.button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addSpacing(4)
            layout.addWidget(
                self.button, 0, Qt.AlignmentFlag.AlignHCenter
            )


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("无线设备电量")
        self.resize(1000, 780)
        self.setMinimumSize(860, 640)

        self._task: ScanTask | None = None
        self._busy = False
        self._busy_dots = 0
        self._pending_refresh = False
        self._report: ScanReport | None = None
        self._fingerprint = None
        self._notice_key = None
        self._show_offline = False
        self._tray: QSystemTrayIcon | None = None
        self._tray_hint_shown = False
        self._quitting = False

        self._build_ui()
        self._apply_styles()
        self._setup_tray()

        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(AUTO_REFRESH_SECONDS * 1000)
        self._auto_timer.timeout.connect(self._on_auto_tick)
        self._auto_timer.start()

        self._device_events = DeviceChangeWatcher(parent=self)
        self._device_events.changed.connect(self._on_devices_changed)

        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(380)
        self._dots_timer.timeout.connect(self._tick_busy_label)

        self._update_summary(None)
        self._show_placeholder(
            "正在检测设备…", "正在读取蓝牙、2.4G 与 USB 外设的电池信息。"
        )
        QTimer.singleShot(120, self.refresh)

    # ---------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 14)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(11)
        brand = BrandMark(40, radius=12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("无线设备电量")
        title.setObjectName("appTitle")
        subtitle = QLabel("蓝牙 · 2.4G · USB 外设 · 实时电量看板")
        subtitle.setObjectName("appSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addWidget(brand)
        header.addLayout(titles)
        header.addStretch(1)

        self.update_label = QLabel("准备扫描")
        self.update_label.setObjectName("updateLabel")
        self.update_label.setMinimumWidth(150)
        self.update_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.update_label)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self.notice_layout = QVBoxLayout()
        self.notice_layout.setContentsMargins(0, 10, 0, 0)
        self.notice_layout.setSpacing(6)
        root.addLayout(self.notice_layout)

        summary = QHBoxLayout()
        summary.setContentsMargins(0, 12, 0, 0)
        summary.setSpacing(10)
        self.total_tile = SummaryTile("设备总数", "已连接 · 未连接")
        self.average_tile = SummaryTile("平均电量", "全部设备平均值")
        self.low_tile = SummaryTile(
            "低电量设备", f"电量 ≤ {LOW_BATTERY_THRESHOLD}%"
        )
        summary.addWidget(self.total_tile)
        summary.addWidget(self.average_tile)
        summary.addWidget(self.low_tile)
        root.addLayout(summary)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self.scroll.viewport().setAutoFillBackground(False)
        self.content = QWidget()
        self.content.setObjectName("scrollContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 12, 4, 4)
        self.content_layout.setSpacing(6)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

        footer_line = QFrame()
        footer_line.setObjectName("footerLine")
        footer_line.setFixedHeight(1)
        root.addWidget(footer_line)

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 9, 2, 0)
        footer.setSpacing(8)
        auto_label = QLabel("自动刷新")
        auto_label.setObjectName("footerText")
        self.auto_switch = ToggleSwitch(True)
        self.auto_hint = QLabel(AUTO_REFRESH_HINT)
        self.auto_hint.setObjectName("footerMuted")
        self.auto_switch.toggled.connect(self._on_auto_toggled)
        footer.addWidget(auto_label)
        footer.addWidget(self.auto_switch)
        footer.addWidget(self.auto_hint)
        footer.addStretch(1)
        source = QLabel(
            "数据来源：Windows 蓝牙栈 / Logitech HID++ / Xbox HID / GameSir HID"
        )
        source.setObjectName("footerMuted")
        footer.addWidget(source)
        root.addLayout(footer)

        QShortcut(QKeySequence.StandardKey.Refresh, self, self.refresh)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Microsoft YaHei UI", "Segoe UI";
                color: #203028;
            }
            QWidget#root { background-color: #F4F6F2; }
            QWidget#scrollContent { background: transparent; }

            QLabel#appTitle {
                font-size: 18px; font-weight: 700; color: #1B2B22;
                background: transparent;
            }
            QLabel#appSubtitle {
                font-size: 11px; color: #7B877F; background: transparent;
            }
            QLabel#updateLabel {
                font-size: 11px; color: #849188; background: transparent;
            }
            QLabel#summaryTitle {
                font-size: 10px; color: #7D8981; background: transparent;
            }
            QLabel#summarySub {
                font-size: 9px; color: #929D96; background: transparent;
            }
            QFrame#summaryTile {
                background: #FFFFFF;
                border: 1px solid #E6EBE5;
                border-radius: 14px;
            }
            QFrame#deviceCard {
                background: #FFFFFF;
                border: 1px solid #E6EBE5;
                border-radius: 13px;
            }
            QFrame#deviceCardLow {
                background: #FFFCFB;
                border: 1px solid #F0CDC8;
                border-radius: 13px;
            }
            QFrame#deviceCardOffline {
                background: #F8FAF7;
                border: 1px dashed #DCE3DD;
                border-radius: 13px;
            }
            QLabel#cardSource {
                font-size: 9px; color: #98A39C; background: transparent;
            }
            QLabel#sectionTitle {
                font-size: 12px; font-weight: 600; color: #2A3A31;
                background: transparent;
            }
            QLabel#sectionMeta {
                font-size: 9px; color: #8D9991; background: transparent;
            }
            QLabel#sectionCount {
                font-size: 9px; font-weight: 600; color: #607067;
                background: #E9EFE9; border-radius: 7px; padding: 1px 5px;
            }
            QFrame#sectionEmpty {
                background: transparent;
                border: 1px dashed #D8E1D9;
                border-radius: 14px;
            }
            QLabel#emptyText {
                font-size: 11px; color: #94A098; background: transparent;
            }
            QFrame#emptyCard {
                background: #FBFCFA;
                border: 1px solid #E3EAE3;
                border-radius: 20px;
            }
            QLabel#emptyTitle {
                font-size: 16px; font-weight: 600; color: #26362C;
                background: transparent;
            }
            QLabel#emptyBody {
                font-size: 11px; color: #7E8B83; background: transparent;
            }
            QPushButton#refreshButton {
                background: #277B58;
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#refreshButton:hover { background: #2F8B65; }
            QPushButton#refreshButton:pressed { background: #1F6649; }
            QPushButton#refreshButton:disabled {
                background: #AFC3B6; color: #F3F7F4;
            }
            QPushButton#ghostButton {
                background: transparent;
                color: #277B58;
                border: 1px solid #C9DACF;
                border-radius: 10px;
                padding: 8px 18px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#ghostButton:hover {
                background: #EFF5F1;
                border-color: #AFCDBB;
            }
            QPushButton#offlineButton {
                background: #EFF4F0;
                color: #2A6E52;
                border: 1px solid #D6E3D9;
                border-radius: 9px;
                padding: 5px 12px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton#offlineButton:hover {
                background: #E4EEE7;
                border-color: #C3D8C9;
            }
            QFrame#footerLine { background: #E2E8E2; border: none; }
            QLabel#footerText {
                font-size: 11px; color: #5F6D64; background: transparent;
            }
            QLabel#footerMuted {
                font-size: 10px; color: #96A199; background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #D2DAD3;
                border-radius: 4px;
                min-height: 34px;
            }
            QScrollBar::handle:vertical:hover { background: #BFC9C1; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            """
        )

    # ------------------------------------------------------------ 刷新流程

    def refresh(self) -> None:
        if self._busy:
            # 扫描期间又收到插拔事件时，等本轮结束后再补一次。
            self._pending_refresh = True
            return
        self._set_busy(True)
        self._task = ScanTask(self)
        self._task.result.connect(self._on_scan_result)
        self._task.finished.connect(self._on_task_finished)
        self._task.start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_button.setEnabled(not busy)
        if busy:
            self._busy_dots = 0
            self.refresh_button.setText("扫描中")
            self.update_label.setText("正在扫描…")
            self._dots_timer.start()
        else:
            self._dots_timer.stop()
            self.refresh_button.setText("刷新")

    def _tick_busy_label(self) -> None:
        self._busy_dots = (self._busy_dots + 1) % 4
        self.update_label.setText(
            "正在扫描" + "·" * self._busy_dots + "…"
        )

    def _on_task_finished(self) -> None:
        task = self.sender()
        if task is self._task:
            self._task = None
        if isinstance(task, QThread):
            task.deleteLater()
        if self._pending_refresh:
            self._pending_refresh = False
            QTimer.singleShot(0, self.refresh)

    def _on_scan_result(self, report: ScanReport) -> None:
        self._set_busy(False)
        self._report = report
        self._update_summary(report)
        self._update_notices(report)
        self._update_content(report)
        self._update_tray(report)

        scanned = report.scanned_at.strftime("%H:%M:%S")
        seconds = max(0.1, report.duration_ms / 1000)
        self.update_label.setText(f"{scanned} 更新 · {seconds:.1f}s")

    def _on_auto_tick(self) -> None:
        if self.auto_switch.isChecked():
            self.refresh()

    def _on_devices_changed(self) -> None:
        if self.auto_switch.isChecked():
            self.refresh()

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            self.auto_hint.setText(AUTO_REFRESH_HINT)
            self._auto_timer.start()
            self.refresh()
        else:
            self.auto_hint.setText("已暂停")
            self._auto_timer.stop()

    # ------------------------------------------------------------ 内容更新

    def _update_summary(self, report: ScanReport | None) -> None:
        if report is None:
            self.total_tile.set_value("—")
            self.total_tile.subtitle_label.setText("已连接 · 未连接")
            self.average_tile.set_value("—")
            self.average_tile.subtitle_label.setText("全部设备平均值")
            self.low_tile.set_value("0")
            return

        online = report.online_devices
        offline = report.offline_devices
        unknown = report.unknown_battery_count
        average = report.average_battery

        parts = [f"已连接 {len(online)}"]
        if offline:
            parts.append(f"未连接 {len(offline)}")
        if unknown:
            parts.append(f"未上报 {unknown}")
        self.total_tile.set_value(str(len(online) + len(offline)))
        self.total_tile.subtitle_label.setText(" · ".join(parts))

        if average is None:
            self.average_tile.set_value("—")
            self.average_tile.subtitle_label.setText("暂无可用电量")
        else:
            self.average_tile.set_value(
                f"{average}%", _battery_color(average).name()
            )
            self.average_tile.subtitle_label.setText(
                f"按 {len(report.known_battery_devices)} 台已连接设备计算"
            )
        self.low_tile.set_value(
            str(report.low_battery_count),
            RED if report.low_battery_count else INK,
        )

    def _update_notices(self, report: ScanReport) -> None:
        key = (
            report.errors,
            report.low_battery_count,
            report.unknown_battery_count,
        )
        if key == self._notice_key:
            return
        self._notice_key = key

        while self.notice_layout.count():
            item = self.notice_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        if report.low_battery_count:
            self.notice_layout.addWidget(
                NoticeBanner(
                    f"{report.low_battery_count} 台设备电量偏低，"
                    "建议及时充电。",
                    "danger",
                )
            )
        if report.unknown_battery_count:
            self.notice_layout.addWidget(
                NoticeBanner(
                    f"{report.unknown_battery_count} 台已连接设备没有向系统"
                    "上报电量。蓝牙 Xbox 模式的手柄不提供电量，"
                    "GameSir 启明星 2 改用 2.4G 接收器或 USB 连接可读取精确值；"
                    "键盘与耳机通常需要厂商工具。",
                    "info",
                )
            )
        if report.errors:
            self.notice_layout.addWidget(
                NoticeBanner("；".join(report.errors), "warning")
            )
        has_notice = bool(
            report.errors
            or report.low_battery_count
            or report.unknown_battery_count
        )
        self.notice_layout.setContentsMargins(
            0, 10 if has_notice else 0, 0, 0
        )

    def _fingerprint_of(self, report: ScanReport) -> tuple:
        return (
            tuple(
                (
                    device.id,
                    device.name,
                    device.battery,
                    device.charging,
                    device.full,
                    device.connected,
                )
                for device in report.devices
            ),
            report.errors,
            self._show_offline,
        )

    def _update_content(self, report: ScanReport) -> None:
        fingerprint = self._fingerprint_of(report)
        if fingerprint == self._fingerprint:
            return
        self._fingerprint = fingerprint

        scrollbar = self.scroll.verticalScrollBar()
        scroll_position = scrollbar.value()

        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        online = report.online_devices
        offline = report.offline_devices

        if not online and not offline:
            self._add_empty_state(
                "未发现无线设备",
                "蓝牙设备需要先与 Windows 配对并连接；2.4G 支持罗技 "
                "Unifying / Bolt / LIGHTSPEED 接收器，以及 GameSir "
                "启明星 2 的接收器或 USB 连接。",
                with_button=True,
            )
            QTimer.singleShot(0, lambda: scrollbar.setValue(scroll_position))
            return

        toggle = None
        if offline:
            toggle = QPushButton(
                "隐藏未连接设备"
                if self._show_offline
                else f"显示未连接设备 ({len(offline)})"
            )
            toggle.setObjectName("offlineButton")
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle.clicked.connect(self._toggle_offline)

        meta = f"实时读取 · {len(online)} 台" if online else "当前无在线设备"
        if report.unknown_battery_count:
            meta += f" · {report.unknown_battery_count} 台未上报电量"
        self.content_layout.addWidget(
            SectionHeader(
                "已连接设备", len(online), meta, "#2D8C65", toggle
            )
        )

        if online:
            self._add_cards(online)
        else:
            self._add_empty_section(
                "当前没有已连接的无线设备，可开启设备后重新扫描。"
            )

        if self._show_offline and offline:
            self.content_layout.addSpacing(2)
            self.content_layout.addWidget(
                SectionHeader(
                    "未连接设备",
                    len(offline),
                    "已配对或曾识别 · 灰色为上次电量",
                    "#9AA69D",
                )
            )
            self._add_cards(offline)

        QTimer.singleShot(0, lambda: scrollbar.setValue(scroll_position))

    def _toggle_offline(self) -> None:
        self._show_offline = not self._show_offline
        self._fingerprint = None
        if self._report is not None:
            self._update_content(self._report)
            if self._show_offline:
                QTimer.singleShot(0, self._scroll_to_offline)

    def _scroll_to_offline(self, attempts: int = 3) -> None:
        scrollbar = self.scroll.verticalScrollBar()
        if scrollbar.maximum() <= 0 and attempts:
            # 新卡片刚加入布局时滚动范围尚未计算，等下一轮事件循环。
            QTimer.singleShot(
                10, lambda: self._scroll_to_offline(attempts - 1)
            )
            return
        scrollbar.setValue(scrollbar.maximum())

    def _add_cards(self, devices: Sequence[WirelessDevice]) -> None:
        """两列网格，所有设备尽量在同一屏内展示。"""
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        columns = 2 if self.width() >= 820 else 1
        for index, device in enumerate(devices):
            grid.addWidget(DeviceCard(device), index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        self.content_layout.addWidget(container)

    def _add_empty_section(self, text: str) -> None:
        empty = QFrame()
        empty.setObjectName("sectionEmpty")
        empty.setMinimumHeight(58)
        layout = QHBoxLayout(empty)
        layout.setContentsMargins(16, 12, 16, 12)
        label = QLabel(text)
        label.setObjectName("emptyText")
        layout.addWidget(label)
        self.content_layout.addWidget(empty)

    def _add_empty_state(
        self, title: str, body: str, with_button: bool = False
    ) -> None:
        empty = EmptyState(
            title,
            body,
            button_text="重新扫描" if with_button else None,
        )
        if with_button:
            empty.button.clicked.connect(self.refresh)
        self.content_layout.addWidget(empty)

    def _show_placeholder(self, title: str, body: str) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._add_empty_state(title, body)

    # ------------------------------------------------------------ 托盘

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(_app_icon(0), self)
        self._tray.setToolTip("无线设备电量 · 正在检测")

        menu = QMenu(self)
        show_action = menu.addAction("显示主窗口")
        show_action.triggered.connect(self._restore_window)
        refresh_action = menu.addAction("立即刷新")
        refresh_action.triggered.connect(self.refresh)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self._quit)
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(
        self, reason: QSystemTrayIcon.ActivationReason
    ) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_window()

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _update_tray(self, report: ScanReport | None) -> None:
        if self._tray is None:
            return
        if report is None:
            self._tray.setIcon(_app_icon(0))
            self._tray.setToolTip("无线设备电量 · 正在检测")
            return
        count = report.low_battery_count
        self._tray.setIcon(_app_icon(count))
        if count:
            self._tray.setToolTip(
                f"无线设备电量 · {count} 台设备电量 ≤ "
                f"{LOW_BATTERY_THRESHOLD}%"
            )
        else:
            self._tray.setToolTip("无线设备电量 · 所有设备电量正常")

    def _quit(self) -> None:
        self._quitting = True
        self._shutdown()
        QApplication.quit()

    # ------------------------------------------------------------ 生命周期

    def _shutdown(self) -> None:
        self._auto_timer.stop()
        self._dots_timer.stop()
        self._device_events.stop()
        if self._tray is not None:
            self._tray.hide()
        if self._task is not None and self._task.isRunning():
            self._task.wait(4000)

    def closeEvent(self, event) -> None:
        if self._tray is not None and not self._quitting:
            # 关闭窗口只是收进托盘，扫描与电量监控继续在后台运行。
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._tray.showMessage(
                    "无线设备电量",
                    "程序已转入后台运行，双击托盘图标可重新打开。",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            return
        self._shutdown()
        super().closeEvent(event)


def _app_icon(low_count: int = 0) -> QIcon:
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    if low_count:
        # 托盘图标缩到 16px 也要能读出数字，直接以数量作为主图形。
        alert = QLinearGradient(0, 0, 128, 128)
        alert.setColorAt(0.0, QColor("#C34A3D"))
        alert.setColorAt(1.0, QColor("#DA6A5D"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(alert)
        painter.drawRoundedRect(QRectF(6, 6, 116, 116), 30, 30)

        font = QFont()
        font.setFamilies(["Microsoft YaHei UI", "Segoe UI"])
        font.setPixelSize(
            68 if low_count < 10 else 48 if low_count < 100 else 32
        )
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            QRectF(6, 6, 116, 116),
            Qt.AlignmentFlag.AlignCenter,
            str(low_count) if low_count < 100 else "99+",
        )
        painter.end()
        return QIcon(pixmap)

    gradient = QLinearGradient(0, 0, 128, 128)
    gradient.setColorAt(0.0, QColor("#1E7455"))
    gradient.setColorAt(1.0, QColor("#3DA379"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(6, 6, 116, 116), 30, 30)

    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(6)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(36, 48, 46, 32), 7, 7)
    painter.drawRoundedRect(QRectF(84, 56, 7, 16), 3, 3)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawRoundedRect(QRectF(44, 55, 24, 18), 3, 3)
    painter.end()
    return QIcon(pixmap)


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("无线设备电量")
    app.setOrganizationName("Wireless Battery Monitor")
    app.setStyle("Fusion")
    app.setFont(_font(9))
    app.setWindowIcon(_app_icon())

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
