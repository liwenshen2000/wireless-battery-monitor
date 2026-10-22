"""Xbox 兼容蓝牙手柄设备源（含 GameSir 启明星 2 的 Xbox 模式）。

Windows 通过经典蓝牙 HID 暴露手柄，电量由 Report ID ``0x04`` 上报：

* ``data[1] & 0x03``：电量档位（0/1/2/3 → 10/40/70/100%）
* ``data[1] & 0x10``：正在充电
* ``data[1] & 0x0C``：外部供电状态

该协议只提供 4 档粗粒度电量，因此界面会标记“电量估算”。手柄只在有输入
或电量变化时发送报文，这里为每台设备维持一个后台监听线程，扫描时读取最近
一次收到的电量。
"""

from __future__ import annotations

import threading
from typing import Sequence

from ..hidwin import HidDeviceInfo, HidHandle, enumerate_hid_devices
from ..models import Connection, DeviceKind, WirelessDevice
from .base import DeviceSource

_XBOX_VENDOR_ID = 0x045E
_XBOX_PRODUCT_IDS = {
    0x02E0,  # GameSir Nova 2 Lite / 启明星 2（Xbox 模式）
    0x02E1,
    0x02FD,
    0x02FF,
    0x0B00,
    0x0B05,
    0x0B0A,
    0x0B13,  # GameSir T4 Nova Lite
    0x0B20,
    0x0B21,
    0x0B22,
}
_GAMEPAD_USAGE_PAGE = 0x01
_GAMEPAD_USAGE = 0x05
_BATTERY_REPORT_ID = 0x04
_LEVEL_PERCENT = (10, 40, 70, 100)

_GAMESIR_PIDS = {0x02E0: "GameSir 启明星2", 0x0B13: "GameSir T4 Nova Lite"}

# 连续空闲 60 秒后检查设备是否已移除；手柄平时不发报文属正常现象。
_IDLE_CHECKS = 60


class _Monitor(threading.Thread):
    """持续读取手柄输入报文，缓存最近一次电量报文。"""

    def __init__(self, info: HidDeviceInfo) -> None:
        super().__init__(daemon=True, name=f"xbox-hid-{info.product_id:04x}")
        self.info = info
        self._flags: int | None = None
        self._lock = threading.Lock()

    @property
    def latest(self) -> int | None:
        with self._lock:
            return self._flags

    def run(self) -> None:
        idle = 0
        try:
            with HidHandle(self.info, read=True, write=False) as handle:
                while True:
                    data = handle.read(
                        1000, self.info.input_report_length or 16
                    )
                    if data is None:
                        idle += 1
                        if idle >= _IDLE_CHECKS:
                            idle = 0
                            if not any(
                                item.path == self.info.path
                                for item in enumerate_hid_devices()
                            ):
                                break
                        continue
                    idle = 0
                    if len(data) >= 2 and data[0] == _BATTERY_REPORT_ID:
                        with self._lock:
                            self._flags = data[1]
        except OSError:
            pass
        finally:
            _release_monitor(self)


_monitors: dict[str, _Monitor] = {}
_monitors_lock = threading.Lock()


def _release_monitor(monitor: _Monitor) -> None:
    with _monitors_lock:
        current = _monitors.get(monitor.info.path)
        if current is monitor:
            _monitors.pop(monitor.info.path, None)


def _ensure_monitor(info: HidDeviceInfo) -> _Monitor:
    with _monitors_lock:
        monitor = _monitors.get(info.path)
        if monitor is not None and monitor.is_alive():
            return monitor
        monitor = _Monitor(info)
        _monitors[info.path] = monitor
        monitor.start()
        return monitor


def _address_from_serial(serial: str) -> str:
    digits = "".join(ch for ch in serial if ch in "0123456789abcdefABCDEF")
    if len(digits) != 12:
        return ""
    raw = digits.upper()
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def _display_name(info: HidDeviceInfo) -> str:
    if info.product_id in _GAMESIR_PIDS:
        return f"{_GAMESIR_PIDS[info.product_id]}（Xbox 模式）"
    product = info.product.strip()
    if not product or "HID-compliant" in product:
        return "Xbox Wireless Controller"
    return product


class XboxSource(DeviceSource):
    """经典蓝牙 Xbox HID 手柄（GameSir 兼容模式）。"""

    key = "xbox"
    label = "Xbox HID"
    stack = "Xbox HID（蓝牙）"

    def scan(self) -> Sequence[WirelessDevice]:
        devices: list[WirelessDevice] = []
        for info in enumerate_hid_devices():
            if (
                info.vendor_id != _XBOX_VENDOR_ID
                or info.product_id not in _XBOX_PRODUCT_IDS
                or info.usage_page != _GAMEPAD_USAGE_PAGE
                or info.usage != _GAMEPAD_USAGE
                or not info.is_bluetooth
            ):
                continue

            monitor = _ensure_monitor(info)
            flags = monitor.latest

            battery = None
            charging = False
            full = False
            if flags is not None:
                level = flags & 0x03
                battery = _LEVEL_PERCENT[level]
                charging = bool(flags & 0x10)
                full = level == 3 and not charging

            address = _address_from_serial(info.serial)
            devices.append(
                WirelessDevice(
                    id=f"xbox:{address or info.serial or info.path}",
                    name=_display_name(info),
                    connection=Connection.BLUETOOTH,
                    stack=self.stack,
                    battery=battery,
                    kind=DeviceKind.GAMEPAD,
                    charging=charging,
                    full=full,
                    estimated=battery is not None,
                    address=address,
                    detail=f"PID {info.product_id:04X} · 蓝牙",
                    source_id=self.key,
                )
            )
        return devices
