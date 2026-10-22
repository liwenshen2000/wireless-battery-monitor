"""Windows HID 原生访问：枚举集合与异步读写。

ctypes 直接调用 SetupAPI / hid.dll / kernel32，避免依赖第三方 HID 包。
用于读取厂商自定义通道（例如 GameSir 2.4G 接收器）和 Xbox 兼容手柄的
原始输入报文。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

_DIGCF_PRESENT = 0x00000002
_DIGCF_DEVICEINTERFACE = 0x00000010

_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OVERLAPPED = 0x40000000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000

_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0 = 0
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_HIDP_STATUS_SUCCESS = 0x00110000


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


_hid = ctypes.WinDLL("hid.dll")
_setupapi = ctypes.WinDLL("setupapi.dll")
_kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

_setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(_GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
]
_setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE

_setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(_GUID),
    wintypes.DWORD,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
]
_setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

_setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

_setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
_setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

_hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
_hid.HidD_GetAttributes.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_HIDD_ATTRIBUTES),
]
_hid.HidD_GetAttributes.restype = wintypes.BOOL
_hid.HidD_GetPreparsedData.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(ctypes.c_void_p),
]
_hid.HidD_GetPreparsedData.restype = wintypes.BOOL
_hid.HidP_GetCaps.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(_HIDP_CAPS),
]
_hid.HidP_GetCaps.restype = wintypes.ULONG
_hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
_hid.HidD_FreePreparsedData.restype = wintypes.BOOL
_hid.HidD_GetProductString.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.ULONG,
]
_hid.HidD_GetProductString.restype = wintypes.BOOL
_hid.HidD_GetSerialNumberString.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.ULONG,
]
_hid.HidD_GetSerialNumberString.restype = wintypes.BOOL
_hid.HidD_SetNumInputBuffers.argtypes = [wintypes.HANDLE, wintypes.ULONG]
_hid.HidD_SetNumInputBuffers.restype = wintypes.BOOL

_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(_OVERLAPPED),
]
_kernel32.ReadFile.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(_OVERLAPPED),
]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
_kernel32.ResetEvent.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.GetOverlappedResult.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
]
_kernel32.GetOverlappedResult.restype = wintypes.BOOL
_kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
_kernel32.CancelIoEx.restype = wintypes.BOOL


@dataclass(frozen=True, slots=True)
class HidDeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    usage_page: int
    usage: int
    input_report_length: int
    output_report_length: int
    product: str = ""
    serial: str = ""

    @property
    def is_bluetooth(self) -> bool:
        """经典蓝牙 HID 或 BLE HOGP 接口。"""
        lower = self.path.lower()
        return (
            "00001124-0000-1000-8000-00805f9b34fb" in lower
            or "00001812-0000-1000-8000-00805f9b34fb" in lower
        )


def _read_hid_string(handle, getter) -> str:
    buffer = ctypes.create_unicode_buffer(128)
    if not getter(handle, buffer, ctypes.sizeof(buffer)):
        return ""
    return buffer.value.strip()


def _query_device(path: str) -> HidDeviceInfo | None:
    handle = _kernel32.CreateFileW(
        path,
        0,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        return None

    try:
        attributes = _HIDD_ATTRIBUTES(Size=ctypes.sizeof(_HIDD_ATTRIBUTES))
        if not _hid.HidD_GetAttributes(handle, ctypes.byref(attributes)):
            return None

        preparsed = ctypes.c_void_p()
        if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
            return None
        try:
            caps = _HIDP_CAPS()
            if _hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) != _HIDP_STATUS_SUCCESS:
                return None
            return HidDeviceInfo(
                path=path,
                vendor_id=attributes.VendorID,
                product_id=attributes.ProductID,
                usage_page=caps.UsagePage,
                usage=caps.Usage,
                input_report_length=caps.InputReportByteLength,
                output_report_length=caps.OutputReportByteLength,
                product=_read_hid_string(handle, _hid.HidD_GetProductString),
                serial=_read_hid_string(
                    handle, _hid.HidD_GetSerialNumberString
                ),
            )
        finally:
            _hid.HidD_FreePreparsedData(preparsed)
    finally:
        _kernel32.CloseHandle(handle)


def enumerate_hid_devices() -> list[HidDeviceInfo]:
    """枚举当前已连接的 HID 集合。"""
    guid = _GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))

    handle = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid),
        None,
        None,
        _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE,
    )
    if handle == _INVALID_HANDLE_VALUE:
        return []

    devices: list[HidDeviceInfo] = []
    try:
        index = 0
        while True:
            interface = _SP_DEVICE_INTERFACE_DATA(
                cbSize=ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            )
            if not _setupapi.SetupDiEnumDeviceInterfaces(
                handle, None, ctypes.byref(guid), index, ctypes.byref(interface)
            ):
                break
            index += 1

            required = wintypes.DWORD()
            _setupapi.SetupDiGetDeviceInterfaceDetailW(
                handle,
                ctypes.byref(interface),
                None,
                0,
                ctypes.byref(required),
                None,
            )
            if required.value == 0:
                continue

            buffer = ctypes.create_string_buffer(required.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize：64 位为 8，32 位为 6
            details = ctypes.cast(
                buffer, ctypes.POINTER(wintypes.DWORD)
            )
            details.contents.value = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(
                handle,
                ctypes.byref(interface),
                buffer,
                required.value,
                ctypes.byref(required),
                None,
            ):
                continue

            path = ctypes.wstring_at(ctypes.addressof(buffer) + 4)
            info = _query_device(path)
            if info is not None:
                devices.append(info)
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(handle)
    return devices


class HidHandle:
    """可异步读写的 HID 句柄。"""

    def __init__(self, info: HidDeviceInfo, read: bool = True, write: bool = False):
        access = (_GENERIC_READ if read else 0) | (
            _GENERIC_WRITE if write else 0
        )
        self.info = info
        self._handle = _kernel32.CreateFileW(
            info.path,
            access,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OVERLAPPED,
            None,
        )
        if self._handle == _INVALID_HANDLE_VALUE:
            raise OSError(
                ctypes.get_last_error(), f"无法打开 HID 设备：{info.path}"
            )

        if read:
            _hid.HidD_SetNumInputBuffers(self._handle, 64)
        self._read_event = (
            _kernel32.CreateEventW(None, True, False, None) if read else None
        )
        self._write_event = (
            _kernel32.CreateEventW(None, True, False, None) if write else None
        )

    @property
    def read_length(self) -> int:
        return self.info.input_report_length or 65

    @property
    def write_length(self) -> int:
        return self.info.output_report_length or 65

    def _transfer(
        self,
        *,
        write: bool,
        payload: bytes | None = None,
        size: int,
        timeout_ms: int,
    ) -> tuple[bool, bytes | None]:
        event = self._write_event if write else self._read_event
        if event is None or self._handle == _INVALID_HANDLE_VALUE:
            return False, None

        buffer = ctypes.create_string_buffer(size)
        if payload is not None:
            ctypes.memmove(buffer, payload[:size], min(len(payload), size))

        overlapped = _OVERLAPPED(hEvent=event)
        transferred = wintypes.DWORD()
        _kernel32.ResetEvent(event)

        if write:
            started = _kernel32.WriteFile(
                self._handle,
                buffer,
                size,
                ctypes.byref(transferred),
                ctypes.byref(overlapped),
            )
        else:
            started = _kernel32.ReadFile(
                self._handle,
                buffer,
                size,
                ctypes.byref(transferred),
                ctypes.byref(overlapped),
            )

        if started:
            return True, (None if write else buffer.raw[: transferred.value])

        error = ctypes.get_last_error()
        if error != _ERROR_IO_PENDING:
            return False, None

        wait = _kernel32.WaitForSingleObject(event, timeout_ms)
        if wait != _WAIT_OBJECT_0:
            _kernel32.CancelIoEx(self._handle, ctypes.byref(overlapped))
            _kernel32.WaitForSingleObject(event, 100)
            return False, None

        if not _kernel32.GetOverlappedResult(
            self._handle,
            ctypes.byref(overlapped),
            ctypes.byref(transferred),
            False,
        ):
            return False, None
        return True, (None if write else buffer.raw[: transferred.value])

    def write(self, payload: bytes, timeout_ms: int = 1000) -> bool:
        ok, _ = self._transfer(
            write=True,
            payload=payload,
            size=self.write_length,
            timeout_ms=timeout_ms,
        )
        return ok

    def read(self, timeout_ms: int = 1000, size: int | None = None) -> bytes | None:
        ok, data = self._transfer(
            write=False,
            size=size or self.read_length,
            timeout_ms=timeout_ms,
        )
        return data if ok else None

    def close(self) -> None:
        if self._handle != _INVALID_HANDLE_VALUE:
            _kernel32.CloseHandle(self._handle)
            self._handle = _INVALID_HANDLE_VALUE
        for event in (self._read_event, self._write_event):
            if event:
                _kernel32.CloseHandle(event)
        self._read_event = None
        self._write_event = None

    def __enter__(self) -> "HidHandle":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
