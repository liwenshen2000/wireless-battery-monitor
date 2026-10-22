"""聚合并发执行各设备源，合并缓存中的未连接设备。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Sequence

from .cache import DeviceCache
from .devices import default_sources
from .models import ScanReport, WirelessDevice

_cache = DeviceCache()

_PROTOCOL_SOURCES = {"xbox", "gamesir", "vivo"}


def _dedupe(devices: Sequence[WirelessDevice]) -> list[WirelessDevice]:
    """同一设备可能同时出现在蓝牙 PnP 和协议设备源中，保留信息更全的。"""
    protocol = [
        device
        for device in devices
        if device.connected
        and device.source_id in _PROTOCOL_SOURCES
    ]
    protocol_addresses = {
        device.address for device in protocol if device.address
    }
    result: list[WirelessDevice] = []
    seen_ids: set[str] = set()
    for device in devices:
        if device.id in seen_ids:
            continue
        if (
            protocol
            and device.connected
            and device.source_id == "bluetooth"
            and (
                not device.address
                or device.address in protocol_addresses
            )
        ):
            continue
        seen_ids.add(device.id)
        result.append(device)
    return result


def _dedupe_offline(
    devices: Sequence[WirelessDevice],
) -> list[WirelessDevice]:
    """未连接列表同样可能重复（蓝牙幻影节点 + 协议缓存），按设备去重。"""
    ordered = sorted(
        devices,
        key=lambda device: (
            device.source_id in _PROTOCOL_SOURCES,
            device.has_battery,
            device.last_seen is not None,
        ),
        reverse=True,
    )
    unique: dict[tuple, WirelessDevice] = {}
    for device in ordered:
        key = (
            (device.kind, device.address)
            if device.address
            else ("id", device.id)
        )
        unique.setdefault(key, device)
    return sorted(
        unique.values(),
        key=lambda device: device.last_seen or datetime.min,
        reverse=True,
    )


def _sort_key(device: WirelessDevice) -> tuple:
    return (
        not device.connected,
        not device.has_battery,
        not device.is_low,
        device.battery if device.battery is not None else 100,
        device.name.lower(),
    )


def scan() -> ScanReport:
    started = time.perf_counter()
    devices: list[WirelessDevice] = []
    errors: list[str] = []

    sources = default_sources()
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = [(source, pool.submit(source.scan)) for source in sources]
        for source, future in futures:
            try:
                devices.extend(future.result())
            except Exception as exc:
                # 单个数据源失败不应影响其它设备展示
                errors.append(f"{source.label}扫描失败：{exc}")

    found_ids = {device.id for device in devices}
    devices = _dedupe(devices)
    online = [device for device in devices if device.connected]
    offline = [
        _cache.enrich(device) for device in devices if not device.connected
    ]

    _cache.remember(online)
    offline.extend(_cache.records(found_ids))
    offline = _dedupe_offline(offline)

    ordered = sorted(online + offline, key=_sort_key)
    return ScanReport(
        devices=tuple(ordered),
        errors=tuple(errors),
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
