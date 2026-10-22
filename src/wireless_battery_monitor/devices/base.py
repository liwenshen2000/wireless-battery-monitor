"""设备源基类：新增外设时继承 DeviceSource 并注册到 devices 包。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Sequence

from ..models import WirelessDevice


class DeviceSource(ABC):
    """一类设备或一种连接栈的探测器。

    子类需要声明来源标识、显示名称和连接栈，并实现 :meth:`scan`。扫描不到
    电量时不要丢弃设备，把 ``battery`` 设为 None，界面会显示“未上报电量”。
    """

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    stack: ClassVar[str] = ""

    @abstractmethod
    def scan(self) -> Sequence[WirelessDevice]:
        """返回当前可发现的设备（包括未连接的已配对设备）。"""
        raise NotImplementedError
