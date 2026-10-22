"""Windows 蓝牙栈设备源。

Windows 会把设备上报的电量同步到 ``DEVPKEY_Bluetooth_Battery`` 属性上
（设置 > 蓝牙和设备中显示的电量即来源于此）。属性不一定落在 ``BTHENUM\\DEV_``
主节点上：BLE 的 GATT 电池服务通常在主节点，而经典 TWS 耳机通过 HFP 上报的
电量会落在 HFP 服务节点（GUID ``0x111E``）上，因此两类节点都要读取。
这里使用 CfgMgr32 API 直接读取，避免依赖 PowerShell 或额外服务。

**连接状态**不能只看 PnP 节点是否 present / started：已配对但断开的设备
同样会保留 present 的幻影节点。因此在线状态以 ``bthprops.cpl`` 的
``BluetoothFindFirstDevice`` 返回的 ``fConnected`` 为准（经典蓝牙与 BLE
共用同一份适配器设备列表）。
"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from datetime import datetime, timedelta
from typing import Sequence
from uuid import UUID

from ..models import Connection, DeviceKind, WirelessDevice
from .base import DeviceSource

_CR_SUCCESS = 0
_CR_BUFFER_SMALL = 26

_CM_GETIDLIST_FILTER_PRESENT = 0x00000100
_CM_LOCATE_DEVNODE_NORMAL = 0
_CM_LOCATE_DEVNODE_PHANTOM = 0x00000001
_DN_STARTED = 0x00000008

# DEVPKEY_Bluetooth_Battery = {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2
_BLUETOOTH_BATTERY_KEY = ("104ea319-6ee2-4701-bd47-8ddbf425bbe5", 2)
# 同一属性组的第 3 项表示 Windows 是否报告设备正在充电。
_BLUETOOTH_CHARGING_KEY = ("104ea319-6ee2-4701-bd47-8ddbf425bbe5", 3)
# DEVPKEY_Device_FriendlyName = {A45C254E-DF1C-4EFD-8020-67D146A850E0}, 14
_FRIENDLY_NAME_KEY = ("a45c254e-df1c-4efd-8020-67d146a850e0", 14)
# DEVPKEY_DeviceContainer_Category = {78C34FC8-104A-4ACA-9EA4-524D52996E57}, 94
_CONTAINER_CATEGORY_KEY = ("78c34fc8-104a-4aca-9ea4-524d52996e57", 94)
# DEVPKEY_Bluetooth_LastConnectedTime = {2BD67D8B-8BEB-48D5-87E0-6CDA3428040A}, 11
_LAST_CONNECTED_KEY = ("2bd67d8b-8beb-48d5-87e0-6cda3428040a", 11)
_VT_FILETIME = 64

# 主节点形如 ``BTHENUM\DEV_<MAC>``，服务节点形如
# ``BTHENUM\{GUID}_VID&...\...&0&<MAC>_C00000000``，MAC 都位于末尾；
# 少数 BLE 节点的实例路径可能在 MAC 后还有后缀，再退回 DEV_ 匹配。
_DEVICE_ID_RE = re.compile(r"([0-9A-Fa-f]{12})(?:_C[0-9A-Fa-f]+)?$")
_DEVICE_ID_ANY_RE = re.compile(r"DEV_([0-9A-Fa-f]{12})", re.IGNORECASE)
# 服务节点形如 ``BTHENUM\{0000111e-...}_VID&...``，花括号内即蓝牙服务 GUID。
_SERVICE_GUID_RE = re.compile(
    r"\\\{([0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})\}_"
)
# 免提协议（HFP AG / HF），TWS 耳机通过它上报电量。
_HFP_GUIDS = ("0000111E", "0000111F")
# 标准的 GATT 电池服务（Battery Service）。
_BATTERY_SERVICE_GUID = "0000180F"

_STACK_DEFAULT = "Windows 蓝牙栈"
_STACK_GATT = "GATT 电池服务"
_STACK_HFP = "HFP 电量上报"
_GENERIC_NAMES = {
    "蓝牙设备",
    "bluetooth device",
    "蓝牙外围设备",
    "bluetooth peripheral device",
    "未知设备",
    "unknown device",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        value = UUID(text)
        return cls(
            value.fields[0],
            value.fields[1],
            value.fields[2],
            (ctypes.c_ubyte * 8)(*value.bytes[8:]),
        )


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.ULONG)]

    @classmethod
    def make(cls, guid: str, pid: int) -> "_DEVPROPKEY":
        return cls(_GUID.parse(guid), pid)


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", wintypes.WORD),
        ("wMonth", wintypes.WORD),
        ("wDayOfWeek", wintypes.WORD),
        ("wDay", wintypes.WORD),
        ("wHour", wintypes.WORD),
        ("wMinute", wintypes.WORD),
        ("wSecond", wintypes.WORD),
        ("wMilliseconds", wintypes.WORD),
    ]


class _BLUETOOTH_ADDRESS(ctypes.Union):
    _fields_ = [
        ("ullLong", ctypes.c_ulonglong),
        ("rgBytes", ctypes.c_ubyte * 6),
    ]


class _BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("Address", _BLUETOOTH_ADDRESS),
        ("ulClassofDevice", wintypes.DWORD),
        ("fConnected", wintypes.BOOL),
        ("fRemembered", wintypes.BOOL),
        ("fAuthenticated", wintypes.BOOL),
        ("stLastSeen", _SYSTEMTIME),
        ("stLastUsed", _SYSTEMTIME),
        ("szName", wintypes.WCHAR * 248),
    ]


class _BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("fReturnAuthenticated", wintypes.BOOL),
        ("fReturnRemembered", wintypes.BOOL),
        ("fReturnUnknown", wintypes.BOOL),
        ("fReturnConnected", wintypes.BOOL),
        ("fIssueInquiry", wintypes.BOOL),
        ("cTimeoutMultiplier", ctypes.c_ubyte),
        ("hRadio", wintypes.HANDLE),
    ]


class _BLUETOOTH_FIND_RADIO_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD)]


_cfgmgr = ctypes.WinDLL("CfgMgr32.dll")

_GET_ID_LIST_SIZE = _cfgmgr.CM_Get_Device_ID_List_SizeW
_GET_ID_LIST_SIZE.argtypes = [
    ctypes.POINTER(wintypes.ULONG),
    wintypes.LPCWSTR,
    wintypes.ULONG,
]
_GET_ID_LIST_SIZE.restype = wintypes.ULONG

_GET_ID_LIST = _cfgmgr.CM_Get_Device_ID_ListW
_GET_ID_LIST.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.ULONG,
    wintypes.ULONG,
]
_GET_ID_LIST.restype = wintypes.ULONG

_LOCATE_DEVNODE = _cfgmgr.CM_Locate_DevNodeW
_LOCATE_DEVNODE.argtypes = [
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPCWSTR,
    wintypes.ULONG,
]
_LOCATE_DEVNODE.restype = wintypes.ULONG

_GET_DEVNODE_STATUS = _cfgmgr.CM_Get_DevNode_Status
_GET_DEVNODE_STATUS.argtypes = [
    ctypes.POINTER(wintypes.ULONG),
    ctypes.POINTER(wintypes.ULONG),
    wintypes.DWORD,
    wintypes.ULONG,
]
_GET_DEVNODE_STATUS.restype = wintypes.ULONG

_GET_DEVNODE_PROPERTY = _cfgmgr.CM_Get_DevNode_PropertyW
_GET_DEVNODE_PROPERTY.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(_DEVPROPKEY),
    ctypes.POINTER(wintypes.ULONG),
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.POINTER(wintypes.ULONG),
    wintypes.ULONG,
]
_GET_DEVNODE_PROPERTY.restype = wintypes.ULONG


def _device_ids(present_only: bool) -> list[str]:
    flags = _CM_GETIDLIST_FILTER_PRESENT if present_only else 0
    length = wintypes.ULONG()
    if _GET_ID_LIST_SIZE(ctypes.byref(length), None, flags) != _CR_SUCCESS:
        return []

    buffer = ctypes.create_unicode_buffer(length.value)
    if (
        _GET_ID_LIST(None, buffer, length.value, flags)
        != _CR_SUCCESS
    ):
        return []

    raw = ctypes.wstring_at(ctypes.addressof(buffer), length.value)
    return [part for part in raw.split("\x00") if part]


def _locate(instance_id: str) -> int | None:
    devinst = wintypes.DWORD()
    # 先按在线节点定位；未连接设备再退回幻影（phantom）节点。
    for flags in (_CM_LOCATE_DEVNODE_NORMAL, _CM_LOCATE_DEVNODE_PHANTOM):
        if (
            _LOCATE_DEVNODE(ctypes.byref(devinst), instance_id, flags)
            == _CR_SUCCESS
        ):
            return devinst.value
    return None


def _is_started(devinst: int) -> bool:
    status = wintypes.ULONG()
    problem = wintypes.ULONG()
    if _GET_DEVNODE_STATUS(
        ctypes.byref(status), ctypes.byref(problem), devinst, 0
    ) != _CR_SUCCESS:
        return False
    return bool(status.value & _DN_STARTED)


def _read_property(
    devinst: int, guid: str, pid: int
) -> tuple[int, bytes] | None:
    key = _DEVPROPKEY.make(guid, pid)
    prop_type = wintypes.ULONG()
    size = wintypes.ULONG()

    result = _GET_DEVNODE_PROPERTY(
        devinst,
        ctypes.byref(key),
        ctypes.byref(prop_type),
        None,
        ctypes.byref(size),
        0,
    )
    if result != _CR_BUFFER_SMALL or size.value == 0:
        return None

    buffer = ctypes.create_string_buffer(size.value)
    result = _GET_DEVNODE_PROPERTY(
        devinst,
        ctypes.byref(key),
        ctypes.byref(prop_type),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        ctypes.byref(size),
        0,
    )
    if result != _CR_SUCCESS:
        return None
    return prop_type.value, buffer.raw[: size.value]


def _read_string(devinst: int, guid: str, pid: int) -> str:
    result = _read_property(devinst, guid, pid)
    if not result:
        return ""
    _, data = result
    return data.decode("utf-16-le", "replace").split("\x00")[0].strip()


def _read_string_list(devinst: int, guid: str, pid: int) -> list[str]:
    result = _read_property(devinst, guid, pid)
    if not result:
        return []
    _, data = result
    return [
        part.strip()
        for part in data.decode("utf-16-le", "replace").split("\x00")
        if part.strip()
    ]


def _read_int(devinst: int, guid: str, pid: int) -> int | None:
    result = _read_property(devinst, guid, pid)
    if not result:
        return None
    _, data = result
    if len(data) in (1, 2, 4, 8):
        return int.from_bytes(data, "little")
    return None


def _last_connected_time(devinst: int) -> datetime | None:
    """读取 DEVPKEY_Bluetooth_LastConnectedTime（FILETIME）。"""
    result = _read_property(devinst, *_LAST_CONNECTED_KEY)
    if not result:
        return None
    prop_type, data = result
    if prop_type != _VT_FILETIME or len(data) != 8:
        return None
    ticks = int.from_bytes(data, "little")
    if ticks <= 0:
        return None
    try:
        return datetime(1601, 1, 1) + timedelta(microseconds=ticks // 10)
    except OverflowError:
        return None


def _connected_addresses() -> dict[str, bool]:
    """读取适配器设备列表的真实连接状态（MAC → fConnected）。

    CfgMgr 的 PnP 节点即使设备已断开也会保持 present，无法区分是否在线；
    ``bthprops.cpl`` 的 fConnected 才是当前连接状态的权威来源。
    读取失败时返回空字典，由调用方回退到 PnP 判断。
    """
    try:
        api = ctypes.WinDLL("bthprops.cpl")
    except OSError:
        return {}

    api.BluetoothFindFirstRadio.argtypes = [
        ctypes.POINTER(_BLUETOOTH_FIND_RADIO_PARAMS),
        ctypes.POINTER(wintypes.HANDLE),
    ]
    api.BluetoothFindFirstRadio.restype = wintypes.HANDLE
    api.BluetoothFindNextRadio.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    api.BluetoothFindNextRadio.restype = wintypes.BOOL
    api.BluetoothFindRadioClose.argtypes = [wintypes.HANDLE]
    api.BluetoothFindRadioClose.restype = wintypes.BOOL
    api.BluetoothFindFirstDevice.argtypes = [
        ctypes.POINTER(_BLUETOOTH_DEVICE_SEARCH_PARAMS),
        ctypes.POINTER(_BLUETOOTH_DEVICE_INFO),
    ]
    api.BluetoothFindFirstDevice.restype = wintypes.HANDLE
    api.BluetoothFindNextDevice.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BLUETOOTH_DEVICE_INFO),
    ]
    api.BluetoothFindNextDevice.restype = wintypes.BOOL
    api.BluetoothFindDeviceClose.argtypes = [wintypes.HANDLE]
    api.BluetoothFindDeviceClose.restype = wintypes.BOOL

    radio_params = _BLUETOOTH_FIND_RADIO_PARAMS(
        dwSize=ctypes.sizeof(_BLUETOOTH_FIND_RADIO_PARAMS)
    )
    radio = wintypes.HANDLE()
    find_radio = api.BluetoothFindFirstRadio(
        ctypes.byref(radio_params), ctypes.byref(radio)
    )
    if not find_radio:
        return {}

    result: dict[str, bool] = {}
    try:
        while True:
            params = _BLUETOOTH_DEVICE_SEARCH_PARAMS(
                dwSize=ctypes.sizeof(_BLUETOOTH_DEVICE_SEARCH_PARAMS),
                fReturnAuthenticated=True,
                fReturnRemembered=True,
                fReturnUnknown=False,
                fReturnConnected=True,
                fIssueInquiry=False,
                cTimeoutMultiplier=2,
                hRadio=radio,
            )
            info = _BLUETOOTH_DEVICE_INFO(
                dwSize=ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
            )
            find_device = api.BluetoothFindFirstDevice(
                ctypes.byref(params), ctypes.byref(info)
            )
            if find_device:
                try:
                    while True:
                        raw = f"{info.Address.ullLong:012X}"
                        address = ":".join(
                            raw[i : i + 2] for i in range(0, 12, 2)
                        )
                        result[address] = result.get(
                            address, False
                        ) or bool(info.fConnected)
                        info = _BLUETOOTH_DEVICE_INFO(
                            dwSize=ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
                        )
                        if not api.BluetoothFindNextDevice(
                            find_device, ctypes.byref(info)
                        ):
                            break
                finally:
                    api.BluetoothFindDeviceClose(find_device)
            if radio.value:
                ctypes.windll.kernel32.CloseHandle(radio)
            radio = wintypes.HANDLE()
            if not api.BluetoothFindNextRadio(
                find_radio, ctypes.byref(radio)
            ):
                break
    finally:
        api.BluetoothFindRadioClose(find_radio)
    return result


def _format_address(instance_id: str) -> str:
    match = _DEVICE_ID_RE.search(instance_id) or _DEVICE_ID_ANY_RE.search(
        instance_id
    )
    if not match:
        return ""
    raw = match.group(1).upper()
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def _name_key(name: str) -> str:
    """名称指纹：去掉空白并转小写，用于关联同一设备的经典 / BLE 节点。"""
    return re.sub(r"\s+", "", name).lower()


def _battery_stack(instance_id: str) -> str:
    """按实际提供电量的节点类型，标注数据来自哪条协议。

    主节点与 ``BTHLE\\`` 节点的电量由 GATT 电池服务同步而来；经典 TWS
    耳机的电量则来自 HFP 服务节点。仅用于展示，不影响读取逻辑。
    """
    upper = instance_id.upper()
    match = _SERVICE_GUID_RE.search(upper)
    guid = match.group(1) if match else ""
    if guid.startswith(_HFP_GUIDS):
        return _STACK_HFP
    if not guid or guid.startswith(_BATTERY_SERVICE_GUID):
        return _STACK_GATT
    return _STACK_DEFAULT


def _infer_kind(name: str, categories: list[str]) -> DeviceKind:
    category_text = " ".join(categories).lower()
    if "input.mouse" in category_text:
        return DeviceKind.MOUSE
    if "input.keyboard" in category_text:
        return DeviceKind.KEYBOARD
    if "headset" in category_text or "headphone" in category_text:
        return DeviceKind.HEADSET
    if "input.gaming" in category_text:
        return DeviceKind.GAMEPAD
    if "audio.speaker" in category_text:
        return DeviceKind.SPEAKER
    if "tablet" in category_text:
        return DeviceKind.TABLET
    if "watch" in category_text:
        return DeviceKind.WATCH
    if "phone" in category_text:
        return DeviceKind.PHONE

    lower = name.lower()
    keyword_map = (
        (DeviceKind.GAMEPAD, ("controller", "gamepad", "手柄", "joy-con", "xbox")),
        (
            DeviceKind.HEADSET,
            (
                "headphone",
                "headset",
                "airpods",
                "pods",
                "tws",
                "buds",
                "耳机",
                "lollipods",
            ),
        ),
        (DeviceKind.SPEAKER, ("speaker", "soundbar", "音箱")),
        (
            DeviceKind.KEYBOARD,
            (
                "keyboard",
                "键盘",
                "ganss",
                "gs87",
                "keychron",
                "nuphy",
                "anne pro",
            ),
        ),
        (DeviceKind.MOUSE, ("mouse", "鼠标", "trackball")),
        (DeviceKind.TABLET, ("ipad", "tablet", "平板")),
        (DeviceKind.PHONE, ("iphone", "phone", "手机", "pixel", "galaxy")),
        (DeviceKind.WATCH, ("watch", "手表", "手环", "band")),
    )
    for kind, keywords in keyword_map:
        if any(keyword in lower for keyword in keywords):
            return kind
    return DeviceKind.OTHER


def _drop_duplicate_nodes(
    devices: list[WirelessDevice],
) -> list[WirelessDevice]:
    """同一设备可能同时暴露经典蓝牙和 BLE 节点，保留信息更全的那个。"""
    known = [device for device in devices if device.has_battery]
    result: list[WirelessDevice] = []
    for device in devices:
        if device.has_battery:
            result.append(device)
            continue
        name = device.name.strip().lower()
        mirrored = any(
            other.kind is device.kind
            and (
                name.endswith(other.name.strip().lower())
                or other.name.strip().lower().endswith(name)
            )
            for other in known
        )
        if not mirrored:
            result.append(device)
    return result


class BluetoothSource(DeviceSource):
    """Windows 蓝牙栈（经典蓝牙与 BLE 共用）。"""

    key = "bluetooth"
    label = "Windows 蓝牙栈"
    stack = _STACK_DEFAULT

    def scan(self) -> Sequence[WirelessDevice]:
        connected_map = _connected_addresses()
        candidates: dict[str, dict] = {}

        for instance_id in _device_ids(present_only=False):
            upper = instance_id.upper()
            if not (
                upper.startswith("BTHENUM\\")
                or upper.startswith("BTHLE\\")
            ):
                continue

            address = _format_address(instance_id)
            if not address:
                continue

            devinst = _locate(instance_id)
            if devinst is None:
                continue

            # 名称、在线状态只取主节点，避免服务节点的 "Hands-Free AG"
            # 之类后缀被当成设备名。
            primary = upper.startswith(
                ("BTHENUM\\DEV_", "BTHLE\\DEV_")
            )

            entry = candidates.setdefault(
                address,
                {
                    "names": [],
                    "battery": None,
                    "charging": False,
                    "stack": _STACK_DEFAULT,
                    "categories": [],
                    "started": False,
                    "last_connected": None,
                },
            )
            if primary:
                entry["started"] = entry["started"] or _is_started(devinst)

                last_connected = _last_connected_time(devinst)
                if last_connected is not None and (
                    entry["last_connected"] is None
                    or last_connected > entry["last_connected"]
                ):
                    entry["last_connected"] = last_connected

                name = _read_string(devinst, *_FRIENDLY_NAME_KEY)
                if name and name.lower() not in _GENERIC_NAMES:
                    entry["names"].append(name)

                categories = _read_string_list(
                    devinst, *_CONTAINER_CATEGORY_KEY
                )
                entry["categories"].extend(categories)

            battery = _read_int(devinst, *_BLUETOOTH_BATTERY_KEY)
            if battery is not None and 0 <= battery <= 100:
                entry["battery"] = battery
                entry["stack"] = _battery_stack(instance_id)

            charging = _read_int(devinst, *_BLUETOOTH_CHARGING_KEY)
            if charging is not None:
                entry["charging"] = bool(charging)

        # BLE 节点可能使用随机地址，无法直接与适配器设备列表中的 MAC 对应；
        # 用当前已连接设备的名称做后缀匹配，把同一台设备的两个节点关联起来。
        connected_names = {
            _name_key(max(entry["names"], key=len))
            for address, entry in candidates.items()
            if connected_map.get(address) and entry["names"]
        }

        now = datetime.now()
        devices: list[WirelessDevice] = []
        for address, entry in candidates.items():
            if not entry["names"]:
                continue
            battery = entry["battery"]
            name = max(entry["names"], key=len)

            if not connected_map:
                # 蓝牙 API 不可用时退回 PnP 状态。
                connected = entry["started"]
            elif address in connected_map:
                connected = connected_map[address]
            elif (
                entry["last_connected"] is not None
                and now - entry["last_connected"] <= timedelta(minutes=2)
            ):
                connected = True
            else:
                key = _name_key(name)
                connected = any(
                    key.endswith(other) or other.endswith(key)
                    for other in connected_names
                )

            devices.append(
                WirelessDevice(
                    id=f"bt:{address}",
                    name=name,
                    connection=Connection.BLUETOOTH,
                    stack=entry["stack"],
                    battery=battery,
                    kind=_infer_kind(name, entry["categories"]),
                    connected=connected,
                    charging=entry["charging"] if battery is not None else False,
                    address=address,
                    source_id=self.key,
                )
            )

        devices.sort(key=lambda device: device.name.lower())
        return _drop_duplicate_nodes(devices)
