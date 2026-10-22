"""罗技 2.4G 设备源（HID++ 2.0）。

支持 Unifying / Bolt / LIGHTSPEED 接收器。Windows 上使用 pywinusb 打开
接收器的厂商自定义 HID 接口，并调用以下 HID++ 特性：

* ``0x1004`` Unified Battery
* ``0x1000`` Battery Status
* ``0x1001`` Battery Voltage
* ``0x0005`` Device Name
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Sequence

import pywinusb.hid as hid

from ..models import Connection, DeviceKind, WirelessDevice
from .base import DeviceSource

_LOGITECH_VENDOR_ID = 0x046D
_HIDPP_USAGE_PAGE = 0xFF00
_HIDPP_LONG_USAGE = 0x0002
_LONG_REPORT_ID = 0x11
_SOFTWARE_ID = 0x0A
_LONG_REPORT_SIZE = 20

_FEATURE_ROOT = 0x0000
_FEATURE_DEVICE_NAME = 0x0005
_FEATURE_BATTERY_STATUS = 0x1000
_FEATURE_BATTERY_VOLTAGE = 0x1001
_FEATURE_BATTERY_UNIFIED = 0x1004

_ERROR_FUNCTION = 0x08
_DEVICE_INDEXES = range(1, 7)

_SCAN_FEATURE_PRIORITY = (
    _FEATURE_BATTERY_UNIFIED,
    _FEATURE_BATTERY_STATUS,
    _FEATURE_BATTERY_VOLTAGE,
)

# 锂电常见电压区间到电量的近似映射，仅用于不支持百分比上报的设备。
_VOLTAGE_CURVE = (
    (3000, 0),
    (3300, 5),
    (3450, 10),
    (3550, 20),
    (3650, 35),
    (3750, 55),
    (3850, 75),
    (3950, 90),
    (4050, 100),
)


@dataclass(slots=True)
class _BatteryReading:
    level: int
    charging: bool = False
    full: bool = False
    estimated: bool = False


class _HidppSession:
    """绑定一个 HID++ 长报文接口的请求/响应会话。"""

    def __init__(self, device) -> None:
        if not device.is_opened():
            device.open(shared=True)
        self._device = device
        self._reports: queue.Queue[list[int]] = queue.Queue()
        self._device.set_raw_data_handler(
            lambda data: self._reports.put(list(data))
        )

    def close(self) -> None:
        try:
            self._device.set_raw_data_handler(None)
        except Exception:
            pass
        try:
            self._device.close()
        except Exception:
            pass

    def _drain(self) -> None:
        while True:
            try:
                self._reports.get_nowait()
            except queue.Empty:
                break

    def _write(
        self,
        device_index: int,
        feature_index: int,
        function: int,
        params: bytes = b"",
    ) -> None:
        payload = bytes(
            [
                _LONG_REPORT_ID,
                device_index,
                feature_index,
                (function << 4) | _SOFTWARE_ID,
            ]
        ) + params
        self._device.send_output_report(
            list(payload.ljust(_LONG_REPORT_SIZE, b"\x00"))
        )

    def _matches(
        self, report: list[int], device_index: int, feature_index: int
    ) -> bool:
        return (
            len(report) >= 4
            and report[0] == _LONG_REPORT_ID
            and report[1] == device_index
            and report[2] == feature_index
        )

    def request(
        self,
        device_index: int,
        feature_index: int,
        function: int,
        params: bytes = b"",
        timeout: float = 0.6,
    ) -> bytes | None:
        self._drain()
        self._write(device_index, feature_index, function, params)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                report = self._reports.get(
                    timeout=max(0.01, deadline - time.monotonic())
                )
            except queue.Empty:
                break
            if not self._matches(report, device_index, feature_index):
                continue
            answer_function = report[3] >> 4
            if answer_function == _ERROR_FUNCTION:
                return None
            if answer_function == function:
                return bytes(report)
        return None

    def _probe_devices(self, timeout: float) -> dict[int, int]:
        for device_index in _DEVICE_INDEXES:
            self._write(device_index, _FEATURE_ROOT, 0x00, b"\x00\x05")

        found: dict[int, int] = {}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                report = self._reports.get(
                    timeout=max(0.01, deadline - time.monotonic())
                )
            except queue.Empty:
                break
            if (
                len(report) < 5
                or report[0] != _LONG_REPORT_ID
                or report[2] != 0
                or report[1] not in _DEVICE_INDEXES
                or report[3] >> 4 == _ERROR_FUNCTION
            ):
                continue
            found[report[1]] = report[4]
        return found

    def discover(self) -> dict[int, int]:
        """批量探测接收器上已连接的设备，返回 {设备索引: 名称特性索引}。"""
        self._drain()
        found = self._probe_devices(1.1)

        # 设备刚休眠时首个响应可能超过 1 秒，再补发一次。
        if not found:
            self._drain()
            found = self._probe_devices(0.8)
        return found

    def feature_index(
        self, device_index: int, feature_id: int, timeout: float = 0.35
    ) -> int | None:
        response = self.request(
            device_index,
            _FEATURE_ROOT,
            0x00,
            bytes([feature_id >> 8, feature_id & 0xFF]),
            timeout=timeout,
        )
        if not response or len(response) < 5:
            return None
        return response[4] or None


def _battery_status_flags(status: int) -> tuple[bool, bool]:
    """Battery Status / Unified Battery 的充电状态枚举。"""
    return status in (1, 2, 4), status == 3


def _estimate_from_voltage(millivolts: int) -> int:
    if millivolts <= _VOLTAGE_CURVE[0][0]:
        return 0
    if millivolts >= _VOLTAGE_CURVE[-1][0]:
        return 100
    for (low_mv, low_pct), (high_mv, high_pct) in zip(
        _VOLTAGE_CURVE, _VOLTAGE_CURVE[1:]
    ):
        if low_mv <= millivolts <= high_mv:
            span = high_mv - low_mv
            ratio = (millivolts - low_mv) / span if span else 0
            return round(low_pct + ratio * (high_pct - low_pct))
    return 0


def _read_unified_battery(
    session: _HidppSession, device_index: int, feature: int
) -> _BatteryReading | None:
    response = session.request(device_index, feature, 0x01)
    if not response or len(response) < 8:
        return None

    level = response[4]
    approximation = response[5]
    charging, full = _battery_status_flags(response[6] & 0x0F)
    estimated = False

    if not 0 < level <= 100:
        # 未给出百分比时，Unified Battery 会返回一个粗略档位。
        level = {8: 90, 4: 50, 2: 20, 1: 5, 0: 0}.get(approximation, 0)
        estimated = level > 0

    if level <= 0 and not full:
        return None
    return _BatteryReading(
        level=100 if full else level,
        charging=charging,
        full=full,
        estimated=estimated,
    )


def _read_battery_status(
    session: _HidppSession, device_index: int, feature: int
) -> _BatteryReading | None:
    response = session.request(device_index, feature, 0x00)
    if not response or len(response) < 7:
        return None

    level = response[4]
    charging, full = _battery_status_flags(response[6] & 0x0F)
    if not 0 < level <= 100:
        if not full:
            return None
        level = 100
    return _BatteryReading(level=level, charging=charging, full=full)


def _read_battery_voltage(
    session: _HidppSession, device_index: int, feature: int
) -> _BatteryReading | None:
    response = session.request(device_index, feature, 0x00)
    if not response or len(response) < 7:
        return None

    millivolts = int.from_bytes(response[4:6], "big")
    if not 2500 <= millivolts <= 4500:
        return None

    flags = response[6]
    charging = bool(flags & 0x80)
    full = (flags & 0x03) == 0x01
    return _BatteryReading(
        level=100 if full else _estimate_from_voltage(millivolts),
        charging=charging,
        full=full,
        estimated=True,
    )


def _read_battery(
    session: _HidppSession, device_index: int
) -> _BatteryReading | None:
    readers = {
        _FEATURE_BATTERY_UNIFIED: _read_unified_battery,
        _FEATURE_BATTERY_STATUS: _read_battery_status,
        _FEATURE_BATTERY_VOLTAGE: _read_battery_voltage,
    }
    for feature_id in _SCAN_FEATURE_PRIORITY:
        feature = session.feature_index(device_index, feature_id)
        if feature is None:
            continue
        reading = readers[feature_id](session, device_index, feature)
        if reading is not None:
            return reading
    return None


def _read_name(
    session: _HidppSession, device_index: int, feature: int | None
) -> str:
    if not feature:
        feature = session.feature_index(device_index, _FEATURE_DEVICE_NAME)
    if not feature:
        return ""

    count_response = session.request(device_index, feature, 0x00)
    if not count_response or len(count_response) < 5:
        return ""
    length = count_response[4]
    if not 0 < length <= 128:
        return ""

    chunks = bytearray()
    previous: bytes | None = None
    for offset in range(0, length, 16):
        expected = min(16, length - offset)
        data: bytes | None = None
        # 接收器偶尔会重发上一帧；内容与上一段相同则视为迟到响应。
        for _ in range(3):
            chunk = session.request(
                device_index, feature, 0x01, bytes([offset])
            )
            if not chunk or len(chunk) < 4 + expected:
                continue
            candidate = chunk[4 : 4 + expected]
            # 接收器可能重发上一段；不同 offset 的响应长度不同，
            # 因此用前缀判断而不是整体相等。
            if previous is not None and previous.startswith(candidate):
                continue
            data = candidate
            break
        if data is None:
            break
        chunks.extend(data)
        previous = data
    return bytes(chunks[:length]).decode("utf-8", "replace").strip("\x00 ")


def _infer_kind(name: str) -> DeviceKind:
    lower = name.lower()
    if any(
        token in lower
        for token in (
            "g502",
            "g304",
            "g305",
            "g309",
            "g403",
            "g703",
            "g903",
            "g pro",
            "superlight",
            "mx master",
            "mx anywhere",
            "mx any",
            "lift",
            "trackball",
            "mouse",
            "鼠标",
        )
    ):
        return DeviceKind.MOUSE
    if any(
        token in lower
        for token in (
            "g915",
            "g815",
            "g715",
            "g713",
            "g413",
            "g613",
            "mx keys",
            "keyboard",
            "k380",
            "k780",
            "k855",
            "k865",
            "pop keys",
            "键盘",
        )
    ):
        return DeviceKind.KEYBOARD
    if any(
        token in lower
        for token in (
            "g733",
            "g535",
            "g435",
            "g pro x",
            "headset",
            "astro",
            "zone",
            "耳机",
        )
    ):
        return DeviceKind.HEADSET
    if any(token in lower for token in ("gamepad", "controller", "手柄")):
        return DeviceKind.GAMEPAD
    return DeviceKind.OTHER


def _find_receivers() -> list:
    candidates = []
    for device in hid.find_all_hid_devices():
        if device.vendor_id != _LOGITECH_VENDOR_ID:
            continue
        try:
            device.open(shared=True)
            caps = device.hid_caps
        except Exception:
            try:
                device.close()
            except Exception:
                pass
            continue
        try:
            if (
                caps
                and caps.usage_page == _HIDPP_USAGE_PAGE
                and caps.usage == _HIDPP_LONG_USAGE
            ):
                candidates.append(device)
            else:
                device.close()
        except Exception:
            device.close()
    return candidates


class LogitechSource(DeviceSource):
    """罗技 Unifying / Bolt / LIGHTSPEED 2.4G 接收器。"""

    key = "logitech"
    label = "Logitech HID++ 2.0"
    stack = "Logitech HID++ 2.0"

    def scan(self) -> Sequence[WirelessDevice]:
        devices: list[WirelessDevice] = []
        for device in _find_receivers():
            # _find_receivers 已经打开设备，_HidppSession 会接管这个句柄。
            product_name = (getattr(device, "product_name", "") or "").strip()
            instance_id = getattr(device, "instance_id", "") or "receiver"
            session: _HidppSession | None = None
            try:
                session = _HidppSession(device)
                connected = session.discover()
                for index, name_feature in sorted(connected.items()):
                    reading = _read_battery(session, index)
                    if reading is None:
                        # 设备刚唤醒时首次查询常超时，稍后重试一次。
                        time.sleep(0.2)
                        reading = _read_battery(session, index)
                    name = _read_name(session, index, name_feature)
                    if reading is None and not name:
                        continue
                    if not name:
                        name = f"Logitech 无线设备 {index}"
                    devices.append(
                        WirelessDevice(
                            id=f"logitech:{instance_id}:{index}",
                            name=name,
                            connection=Connection.WIRELESS_24G,
                            stack=self.stack,
                            battery=reading.level if reading else None,
                            kind=_infer_kind(name),
                            charging=reading.charging if reading else False,
                            full=reading.full if reading else False,
                            estimated=reading.estimated if reading else False,
                            detail=(
                                product_name
                                if product_name
                                and "receiver" not in product_name.lower()
                                else "Logitech 2.4G 接收器"
                            ),
                            source_id=self.key,
                        )
                    )
            except Exception:
                # 单个接收器异常不影响其它接收器。
                continue
            finally:
                if session is not None:
                    session.close()
                else:
                    try:
                        device.close()
                    except Exception:
                        pass

        devices.sort(key=lambda item: item.name.lower())
        return devices
