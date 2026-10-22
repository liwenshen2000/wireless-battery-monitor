"""GameSir 2.4G / USB 设备源（启明星 2 / Nova 2 Lite）。

GameSir 在 2.4G 接收器和有线模式下会暴露一个厂商自定义 HID 集合：
VID ``0x3537``、Usage Page ``0xFF7A``、Usage ``0x0001``，报告长度 65 字节。
向该集合写入 ``report id 0 + 01 01`` 后，手柄会回复一帧设备信息，电量位于
第 19 字节（``0xFF`` 表示正在充电）。

协议参考：Dazaike/gamesir-battery（MIT）与 xpadneo 的 GameSir 设备定义。
"""

from __future__ import annotations

import time
from typing import Sequence

from ..hidwin import HidDeviceInfo, HidHandle, enumerate_hid_devices
from ..models import Connection, DeviceKind, WirelessDevice
from .base import DeviceSource

_GAMESIR_VENDOR_ID = 0x3537
_VENDOR_USAGE_PAGE = 0xFF7A
_VENDOR_USAGE = 0x0001

_PID_DONGLE = 0x1098
_PID_WIRED = 0x100F

_REPORT_LENGTH = 65
_BATTERY_OFFSET = 19
_MAC_OFFSET = 1
_MAC_LENGTH = 6
_FIRMWARE_OFFSET = 11
_CHARGING_SENTINEL = 0xFF
_STATE_FRAME_PREFIX = b"\xf4\x00"
_RESPONSE_PREFIX = b"\x00\x01"


def _request_payload(length: int) -> bytes:
    # 65 字节输出报告：report id 0，随后是 GetDeviceInfo 的 01 01。
    payload = bytes([0x00, 0x01, 0x01])
    return payload.ljust(max(length, _REPORT_LENGTH), b"\x00")


def _query(info: HidDeviceInfo) -> bytes | None:
    """向厂商通道请求设备信息，返回原始回复帧。

    读取与写入分别使用独立句柄：接收器在同一个句柄上混合读写时可能
    无法收到中断回复。
    """
    try:
        writer = HidHandle(info, read=True, write=True)
    except OSError:
        return None

    try:
        reader = HidHandle(info, read=True, write=False)
    except OSError:
        writer.close()
        return None

    with writer, reader:
        payload = _request_payload(info.output_report_length)
        read_size = max(info.input_report_length or 0, _REPORT_LENGTH)
        for attempt in range(3):
            if attempt:
                time.sleep(0.25)
            if not writer.write(payload):
                continue
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                data = reader.read(remaining, read_size)
                if not data:
                    continue
                if data.startswith(_STATE_FRAME_PREFIX):
                    continue  # 手柄主动上报的状态帧，不是设备信息
                if len(data) > _BATTERY_OFFSET and data.startswith(
                    _RESPONSE_PREFIX
                ):
                    return data
    return None


def _format_mac(raw: bytes) -> str:
    return ":".join(f"{byte:02X}" for byte in raw)


def _parse(
    info: HidDeviceInfo, frame: bytes
) -> WirelessDevice | None:
    raw_level = frame[_BATTERY_OFFSET]
    charging = raw_level == _CHARGING_SENTINEL
    level = 100 if charging else raw_level
    if level > 100:
        return None

    mac = ""
    if len(frame) >= _MAC_OFFSET + _MAC_LENGTH:
        mac = _format_mac(
            frame[_MAC_OFFSET : _MAC_OFFSET + _MAC_LENGTH]
        )

    firmware = ""
    if len(frame) >= _FIRMWARE_OFFSET + 2:
        firmware = (
            f"{frame[_FIRMWARE_OFFSET]:02X}"
            f"{frame[_FIRMWARE_OFFSET + 1]:02X}"
        )

    wireless = info.product_id == _PID_DONGLE
    connection = (
        Connection.WIRELESS_24G if wireless else Connection.USB
    )
    detail_parts = ["Nova 2 Lite"]
    if firmware:
        detail_parts.append(f"固件 {firmware}")
    detail_parts.append("2.4G 接收器" if wireless else "USB 连接")

    return WirelessDevice(
        id=f"gamesir:{mac or info.serial or info.path}",
        name="GameSir 启明星2",
        connection=connection,
        stack="GameSir 私有 HID（Usage Page 0xFF7A）",
        battery=level,
        kind=DeviceKind.GAMEPAD,
        charging=charging,
        full=level >= 100 and not charging,
        estimated=False,
        address=mac,
        detail=" · ".join(detail_parts),
        source_id="gamesir",
    )


class GameSirSource(DeviceSource):
    """GameSir 启明星 2 / Nova 2 Lite。"""

    key = "gamesir"
    label = "GameSir 私有 HID"
    stack = "GameSir 私有 HID（Usage Page 0xFF7A）"

    def scan(self) -> Sequence[WirelessDevice]:
        devices: list[WirelessDevice] = []
        for info in enumerate_hid_devices():
            if (
                info.vendor_id != _GAMESIR_VENDOR_ID
                or info.usage_page != _VENDOR_USAGE_PAGE
                or info.usage != _VENDOR_USAGE
                or info.product_id not in (_PID_DONGLE, _PID_WIRED)
            ):
                continue
            frame = _query(info)
            if frame is None:
                continue
            device = _parse(info, frame)
            if device is not None:
                devices.append(device)
        return devices
