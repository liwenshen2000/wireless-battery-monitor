"""设备与扫描结果的共享数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

LOW_BATTERY_THRESHOLD = 25


class Connection(str, Enum):
    """设备当前使用的物理连接方式。"""

    BLUETOOTH = "bluetooth"
    WIRELESS_24G = "wireless_24g"
    USB = "usb"

    @property
    def label(self) -> str:
        return {
            Connection.BLUETOOTH: "蓝牙",
            Connection.WIRELESS_24G: "2.4G",
            Connection.USB: "USB",
        }[self]


class DeviceKind(str, Enum):
    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    HEADSET = "headset"
    GAMEPAD = "gamepad"
    PHONE = "phone"
    TABLET = "tablet"
    SPEAKER = "speaker"
    WATCH = "watch"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class BatteryComponent:
    """可分解上报的部件电量，例如 TWS 耳机的左耳 / 右耳 / 充电盒。"""

    label: str
    battery: int
    charging: bool = False


@dataclass(frozen=True, slots=True)
class WirelessDevice:
    """一台无线设备；``battery`` 为 None 表示当前读不到电量。"""

    id: str
    name: str
    connection: Connection
    stack: str
    battery: int | None = None
    kind: DeviceKind = DeviceKind.OTHER
    connected: bool = True
    charging: bool = False
    full: bool = False
    estimated: bool = False
    address: str = ""
    detail: str = ""
    source_id: str = ""
    last_seen: datetime | None = None
    components: tuple[BatteryComponent, ...] = ()

    @property
    def has_battery(self) -> bool:
        return self.battery is not None

    @property
    def is_low(self) -> bool:
        return (
            self.battery is not None
            and self.battery < LOW_BATTERY_THRESHOLD
        )


@dataclass(frozen=True, slots=True)
class ScanReport:
    devices: tuple[WirelessDevice, ...] = ()
    errors: tuple[str, ...] = ()
    scanned_at: datetime = field(default_factory=datetime.now)
    duration_ms: int = 0

    @property
    def online_devices(self) -> tuple[WirelessDevice, ...]:
        return tuple(d for d in self.devices if d.connected)

    @property
    def offline_devices(self) -> tuple[WirelessDevice, ...]:
        return tuple(d for d in self.devices if not d.connected)

    @property
    def known_battery_devices(self) -> tuple[WirelessDevice, ...]:
        return tuple(d for d in self.online_devices if d.has_battery)

    @property
    def unknown_battery_count(self) -> int:
        return sum(1 for d in self.online_devices if not d.has_battery)

    @property
    def average_battery(self) -> int | None:
        known = self.known_battery_devices
        if not known:
            return None
        return round(sum(d.battery for d in known) / len(known))

    @property
    def low_battery_count(self) -> int:
        return sum(1 for d in self.online_devices if d.is_low)
