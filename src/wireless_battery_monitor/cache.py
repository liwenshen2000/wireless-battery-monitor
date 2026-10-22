"""设备缓存：记住最近一次在线状态，用于展示“支持但当前未连接”的设备。

缓存只保存曾在扫描中成功发现的设备，写入
``%LOCALAPPDATA%/WirelessBatteryMonitor/devices.json``。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from .models import BatteryComponent, Connection, DeviceKind, WirelessDevice

_VERSION = 1
_MAX_RECORDS = 64
_RETENTION = timedelta(days=90)


def _default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "WirelessBatteryMonitor" / "devices.json"


def _connection(value: str) -> Connection:
    try:
        return Connection(value)
    except ValueError:
        return Connection.BLUETOOTH


def _kind(value: str) -> DeviceKind:
    try:
        return DeviceKind(value)
    except ValueError:
        return DeviceKind.OTHER


def _components(value) -> tuple[BatteryComponent, ...]:
    if not isinstance(value, list):
        return ()
    result: list[BatteryComponent] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        battery = item.get("battery")
        if not isinstance(battery, int):
            continue
        result.append(
            BatteryComponent(
                label=str(item.get("label") or ""),
                battery=battery,
                charging=bool(item.get("charging", False)),
            )
        )
    return tuple(result)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class DeviceCache:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._records: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        try:
            payload = json.loads(self._path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict) or payload.get("version") != _VERSION:
            return {}
        devices = payload.get("devices")
        if not isinstance(devices, dict):
            return {}
        return {
            key: value
            for key, value in devices.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def _save(self) -> None:
        payload = {"version": _VERSION, "devices": self._records}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                delete=False,
                suffix=".tmp",
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                temporary = Path(handle.name)
            temporary.replace(self._path)
        except OSError:
            pass

    def _prune(self) -> bool:
        cutoff = datetime.now() - _RETENTION
        changed = False
        kept: list[tuple[datetime, str, dict]] = []
        for key, record in self._records.items():
            seen = _parse_time(record.get("last_seen", ""))
            if seen is None or seen < cutoff:
                changed = True
                continue
            kept.append((seen, key, record))

        if len(kept) > _MAX_RECORDS:
            kept.sort(key=lambda item: item[0], reverse=True)
            kept = kept[:_MAX_RECORDS]
            changed = True

        self._records = {key: record for _, key, record in kept}
        return changed

    def remember(self, devices: Sequence[WirelessDevice]) -> None:
        """记录当前在线设备；同时清理过期记录。"""
        now = datetime.now()
        changed = False
        for device in devices:
            if not device.connected:
                continue
            record = {
                "name": device.name,
                "connection": device.connection.value,
                "stack": device.stack,
                "battery": device.battery,
                "kind": device.kind.value,
                "charging": device.charging,
                "full": device.full,
                "estimated": device.estimated,
                "address": device.address,
                "detail": device.detail,
                "source_id": device.source_id,
                "last_seen": now.isoformat(timespec="seconds"),
                "components": [
                    {
                        "label": component.label,
                        "battery": component.battery,
                        "charging": component.charging,
                    }
                    for component in device.components
                ],
            }
            previous = self._records.get(device.id)
            comparable = {
                key: value
                for key, value in record.items()
                if key != "last_seen"
            }
            previous_fields = (
                {
                    key: value
                    for key, value in previous.items()
                    if key != "last_seen"
                }
                if previous
                else None
            )
            previous_seen = (
                _parse_time(previous.get("last_seen", ""))
                if previous
                else None
            )
            # last_seen 每次扫描都会变化，没必要每 30 秒写盘一次；
            # 其余字段不变时最多每 5 分钟刷新一次时间戳。
            if (
                previous_fields != comparable
                or previous_seen is None
                or now - previous_seen > timedelta(minutes=5)
            ):
                changed = True
            self._records[device.id] = record

        changed = self._prune() or changed
        if changed:
            self._save()

    def records(self, exclude: set[str]) -> list[WirelessDevice]:
        """把缓存记录转换成未连接设备，跳过当前扫描已发现的 id。"""
        devices: list[WirelessDevice] = []
        for key, record in self._records.items():
            if key in exclude:
                continue
            last_seen = _parse_time(record.get("last_seen", ""))
            battery = record.get("battery")
            devices.append(
                WirelessDevice(
                    id=key,
                    name=str(record.get("name") or key),
                    connection=_connection(record.get("connection", "")),
                    stack=str(record.get("stack") or ""),
                    battery=battery if isinstance(battery, int) else None,
                    kind=_kind(record.get("kind", "")),
                    connected=False,
                    charging=bool(record.get("charging", False)),
                    full=bool(record.get("full", False)),
                    estimated=bool(record.get("estimated", False)),
                    address=str(record.get("address") or ""),
                    detail=str(record.get("detail") or ""),
                    source_id=str(record.get("source_id") or ""),
                    last_seen=last_seen,
                    components=_components(record.get("components")),
                )
            )

        return devices

    def enrich(self, device: WirelessDevice) -> WirelessDevice:
        """为蓝牙栈原生枚举到的未连接设备补上上次电量与时间。"""
        record = self._records.get(device.id)
        if record is None:
            return device
        battery = record.get("battery")
        return replace(
            device,
            battery=(
                device.battery
                if device.battery is not None
                else battery if isinstance(battery, int) else None
            ),
            charging=bool(record.get("charging", False)),
            full=bool(record.get("full", False)),
            estimated=bool(record.get("estimated", False)),
            last_seen=_parse_time(record.get("last_seen", "")),
            components=_components(record.get("components")),
        )
