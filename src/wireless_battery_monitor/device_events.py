"""监听 Windows 设备热插拔广播，用于事件驱动的刷新。

Windows 在设备节点增删时向所有顶层窗口广播 ``WM_DEVICECHANGE``。这里用
Qt 的原生事件过滤器接收，经过去抖后发出 :attr:`DeviceChangeWatcher.changed`，
避免一次插拔引发的连续消息造成多次扫描。
"""

from __future__ import annotations

from ctypes import wintypes

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QCoreApplication,
    QObject,
    QTimer,
    Signal,
)

WM_DEVICECHANGE = 0x0219

DBT_DEVNODES_CHANGED = 0x0007
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004

_TRIGGER_EVENTS = frozenset(
    (DBT_DEVNODES_CHANGED, DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE)
)


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, on_change) -> None:
        super().__init__()
        self._on_change = on_change

    def nativeEventFilter(self, event_type, message) -> bool:
        try:
            address = message if isinstance(message, int) else int(message)
            if not address:
                return False
            msg = wintypes.MSG.from_address(address)
        except (TypeError, ValueError):
            return False

        if (
            msg.message == WM_DEVICECHANGE
            and int(msg.wParam) in _TRIGGER_EVENTS
        ):
            self._on_change()
        return False


class DeviceChangeWatcher(QObject):
    """把连续的设备变化消息去抖成一次 ``changed`` 信号。"""

    changed = Signal()

    def __init__(
        self,
        debounce_ms: int = 700,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._filter = _NativeFilter(self._schedule)
        self._application = QCoreApplication.instance()
        if self._application is not None:
            self._application.installNativeEventFilter(self._filter)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self.changed.emit)

    def _schedule(self) -> None:
        # 插拔接收器时驱动会连续发出多条消息，重新计时合并成一次刷新。
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._application is not None:
            self._application.removeNativeEventFilter(self._filter)
