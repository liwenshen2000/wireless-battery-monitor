"""vivo / iQOO TWS 耳机：通过厂商 GAIA 通道读取左右耳与充电盒电量。

Windows 只会把 HFP 上报的一个总电量写进 PnP 属性，左右耳与充电盒的独立
数值需要走耳机自带的 GAIA 串口协议。配对后系统会为厂商服务
``00001100-D102-11E1-9B23-00025B00A5A5`` 建立一个节点，据此识别机型。

帧格式（与官方 App 在 vivo TWS 5 / iQOO TWS 5 上一致）::

    FF <version> 00 <len> <vendor:2> <command:2> [payload...]

* 握手：``FF 04 00 00 000A 0300``
* 查询：``FF 03 00 00 001B 0207``
* 上报：``FF 03 00 05 001B 8207 00 <左> <右> <盒> <flags>``
* 时间同步：耳机发 ``001B 8509``，回 ``001B 0509`` + 7 字节本地时间
"""

from __future__ import annotations

import re
import socket
import time
from datetime import datetime
from typing import Sequence

from ..models import (
    BatteryComponent,
    Connection,
    DeviceKind,
    WirelessDevice,
)
from .base import DeviceSource
from .windows_bluetooth import (
    _connected_addresses,
    _device_ids,
    _format_address,
    _FRIENDLY_NAME_KEY,
    _locate,
    _read_string,
)

_GAIA_SERVICE = "00001100-D102-11E1-9B23-00025B00A5A5"
_VENDOR = 0x001B
_CMD_BATTERY = 0x0207
_CMD_BATTERY_REPLY = 0x8207
_CMD_TIME_SYNC = 0x8509
_CMD_TIME_REPLY = 0x0509
_HEADER_SIZE = 8
_CHANNELS = (15, 3, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
_KNOWN_CHANNELS: dict[str, int] = {}
_DEV_NAME_RE = re.compile(
    r"^BTHENUM\\DEV_([0-9A-F]{12})\\", re.IGNORECASE
)


def _frame(version: int, command: int, payload: bytes = b"") -> bytes:
    return bytes(
        [
            0xFF,
            version,
            0x00,
            len(payload),
            (_VENDOR >> 8) & 0xFF,
            _VENDOR & 0xFF,
            (command >> 8) & 0xFF,
            command & 0xFF,
        ]
    ) + payload


def _time_payload() -> bytes:
    now = datetime.now()
    return bytes(
        [
            now.year // 100,
            now.year % 100,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
        ]
    )


def _parse(buffer: bytearray) -> list[tuple[int, bytes]]:
    """从字节流中切出完整帧，返回 ``(command, payload)`` 列表。"""
    frames: list[tuple[int, bytes]] = []
    while True:
        start = buffer.find(b"\xff")
        if start < 0:
            buffer.clear()
            return frames
        del buffer[:start]
        if len(buffer) < _HEADER_SIZE:
            return frames
        if buffer[2] != 0x00:
            del buffer[:1]
            continue
        command = (buffer[6] << 8) | buffer[7]
        payload_length = buffer[3]
        total = _HEADER_SIZE + payload_length
        if len(buffer) < total:
            return frames
        frames.append((command, bytes(buffer[_HEADER_SIZE:total])))
        del buffer[:total]


def _query(sock: socket.socket) -> tuple[int, int, int] | None:
    sock.send(_frame(4, 0x0300))
    time.sleep(0.3)
    sock.send(_frame(3, _CMD_BATTERY))

    buffer = bytearray()
    deadline = time.monotonic() + 3.0
    sock.settimeout(0.4)
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            continue
        if not chunk:
            break
        buffer += chunk
        for command, payload in _parse(buffer):
            if command == _CMD_TIME_SYNC:
                sock.send(_frame(3, _CMD_TIME_REPLY, _time_payload()))
            elif command == _CMD_BATTERY_REPLY and len(payload) >= 4:
                return payload[1], payload[2], payload[3]
    return None


def _read_battery(address: str) -> tuple[int, int, int] | None:
    known = _KNOWN_CHANNELS.get(address)
    channels = (
        (known,) + tuple(c for c in _CHANNELS if c != known)
        if known is not None
        else _CHANNELS
    )
    for channel in channels:
        sock = socket.socket(
            socket.AF_BLUETOOTH,
            socket.SOCK_STREAM,
            socket.BTPROTO_RFCOMM,
        )
        sock.settimeout(2.0)
        try:
            sock.connect((address, channel))
        except OSError:
            sock.close()
            continue
        try:
            result = _query(sock)
        except OSError:
            result = None
        finally:
            sock.close()
        if result is not None:
            _KNOWN_CHANNELS[address] = channel
            return result
    return None


def _gaia_addresses() -> dict[str, str]:
    """返回 ``{地址: 设备名}``，只包含暴露 vivo GAIA 服务的耳机。"""
    names: dict[str, str] = {}
    addresses: set[str] = set()
    for instance_id in _device_ids(present_only=False):
        address = _format_address(instance_id)
        if not address:
            continue
        if _GAIA_SERVICE.lower() in instance_id.lower():
            addresses.add(address)
            continue
        match = _DEV_NAME_RE.match(instance_id)
        if match:
            devinst = _locate(instance_id)
            if devinst is not None:
                name = _read_string(devinst, *_FRIENDLY_NAME_KEY)
                if name:
                    names[address] = name
    return {
        address: names.get(address, "vivo TWS")
        for address in addresses
    }


class VivoTwsSource(DeviceSource):
    """vivo / iQOO TWS 耳机（GAIA 私有电量通道）。"""

    key = "vivo"
    label = "vivo / iQOO 耳机"
    stack = "GAIA 私有通道"

    def scan(self) -> Sequence[WirelessDevice]:
        candidates = _gaia_addresses()
        if not candidates:
            return ()
        connected = _connected_addresses()

        devices: list[WirelessDevice] = []
        for address, name in candidates.items():
            if not connected.get(address):
                continue
            battery = _read_battery(address)
            if battery is None:
                continue
            left, right, case = battery
            devices.append(
                WirelessDevice(
                    id=f"bt:{address}",
                    name=name,
                    connection=Connection.BLUETOOTH,
                    stack=self.stack,
                    battery=min(left, right),
                    kind=DeviceKind.HEADSET,
                    address=address,
                    detail="左右耳与充电盒电量由 GAIA 通道分别上报",
                    source_id=self.key,
                    components=(
                        BatteryComponent("左耳", left),
                        BatteryComponent("右耳", right),
                        BatteryComponent("充电盒", case),
                    ),
                )
            )
        return devices
