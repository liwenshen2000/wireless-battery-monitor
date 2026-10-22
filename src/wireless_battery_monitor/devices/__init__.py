"""设备源注册表。

每个模块对应一类具体设备 / 协议：

* ``windows_bluetooth``：Windows 蓝牙栈（经典蓝牙与 BLE 通用）
* ``vivo_tws``：vivo / iQOO TWS 耳机（GAIA 通道读取左右耳与充电盒）
* ``logitech_hidpp``：罗技 HID++ 2.0 接收器（LIGHTSPEED / Unifying / Bolt）
* ``gamesir_nova2_lite``：GameSir 启明星 2（Nova 2 Lite）2.4G / USB
* ``xbox_bluetooth_controller``：Xbox 兼容蓝牙手柄

新增外设支持时：

1. 在 ``devices/`` 下新建以具体型号命名的模块，继承 :class:`DeviceSource`；
2. 在 :func:`default_sources` 中注册即可，扫描器与界面无需改动。
"""

from __future__ import annotations

from .base import DeviceSource
from .gamesir_nova2_lite import GameSirSource
from .logitech_hidpp import LogitechSource
from .vivo_tws import VivoTwsSource
from .windows_bluetooth import BluetoothSource
from .xbox_bluetooth_controller import XboxSource

__all__ = ["DeviceSource", "default_sources"]


def default_sources() -> tuple[DeviceSource, ...]:
    return (
        BluetoothSource(),
        VivoTwsSource(),
        LogitechSource(),
        GameSirSource(),
        XboxSource(),
    )
