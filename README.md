# 无线设备电量

一个 Windows 桌面小工具，用来看当前连接的蓝牙、2.4G 与 USB 无线设备还剩多少电。

## 运行

```powershell
uv sync
uv run wireless-battery-monitor
```

需要 Windows 10/11 与 Python 3.15+。

## 界面

- 紧凑双列卡片，默认窗口内即可平铺展示全部在线设备，不需要滚动。
- 设备不再按「蓝牙 / 2.4G」分组，每张卡片直接显示连接方式与所用连接栈。
- 未连接的已配对设备默认隐藏，点击标题栏的「显示未连接设备」按钮查看，卡片会置灰并显示上次电量与最后在线时间。
- 插拔 2.4G 接收器、USB 设备时通过 Windows 的 `WM_DEVICECHANGE` 广播即时刷新；
  另外每 30 秒自动刷新一次以同步电量变化，也可以手动刷新（F5）。
- 关闭主窗口后收进系统托盘继续在后台监控；存在低电量设备（≤ 25%）时，
  托盘图标会直接显示数量，右键菜单可刷新或退出。

## 支持的数据源

| 模块 | 设备 | 连接方式 | 连接栈 | 电量来源 |
| --- | --- | --- | --- | --- |
| `windows_bluetooth.py` | 通用蓝牙外设 | 蓝牙 | Windows 蓝牙栈 · GATT / HFP | `DEVPKEY_Bluetooth_Battery`（GATT BAS 或 HFP 上报，卡片标注实际来源） |
| `vivo_tws.py` | vivo / iQOO TWS 耳机 | 蓝牙 | GAIA 私有通道 | 厂商串口协议 `0x0207`，左右耳与充电盒分别上报 |
| `logitech_hidpp.py` | 罗技接收器设备 | 2.4G | Logitech HID++ 2.0 | Unified Battery / Battery Status / 电压估算 |
| `gamesir_nova2_lite.py` | GameSir 启明星 2 | 2.4G / USB | GameSir 私有 HID | 厂商通道返回的精确百分比 |
| `xbox_bluetooth_controller.py` | Xbox 兼容蓝牙手柄 | 蓝牙 | Xbox HID | Report ID `0x04` 的 4 档电量 |

其它厂商设备只要向系统上报电量，就会自动出现在列表里；读不到电量的设备也会
保留，并标注「未上报电量」。

## 扩展新外设

每类设备是一个独立模块，共享同一套模型与扫描流程：

```text
src/wireless_battery_monitor/
├── models.py            # WirelessDevice / ScanReport 数据模型
├── scanner.py           # 并发调度各设备源、合并离线缓存
├── cache.py             # 已识别设备的上次在线状态
├── hidwin.py            # Windows HID 原生读写（ctypes）
└── devices/
    ├── base.py                      # DeviceSource 基类
    ├── windows_bluetooth.py         # Windows 蓝牙栈（经典蓝牙 / BLE）
    ├── vivo_tws.py                  # vivo / iQOO TWS 耳机（GAIA 私有通道）
    ├── logitech_hidpp.py            # 罗技 HID++ 2.0 接收器
    ├── gamesir_nova2_lite.py        # GameSir 启明星 2 私有 HID
    └── xbox_bluetooth_controller.py # Xbox 兼容蓝牙手柄
```

模块按具体型号 / 协议命名，新增同类设备时不会出现「bluetooth.py 到底管谁」的歧义。

新增设备只需继承 [base.py](src/wireless_battery_monitor/devices/base.py) 的
`DeviceSource`，实现 `scan()` 返回 `WirelessDevice`，然后在
[devices/__init__.py](src/wireless_battery_monitor/devices/__init__.py) 的
`default_sources()` 里注册，扫描器与界面会自动接管。

## 关于手柄电量

**GameSir 启明星 2（Nova 2 Lite）**

- 2.4G 接收器或 USB 连接时，会额外暴露一个厂商自定义 HID 集合：
  VID `0x3537`、PID `0x1098`（接收器）/ `0x100F`（有线）、Usage Page `0xFF7A`、
  Usage `0x0001`、报告长度 65 字节。向该集合写入 `report id 0 + 01 01`
  请求设备信息，返回帧的第 19 字节即电量（`0xFF` 表示正在充电），
  第 1～6 字节为设备 MAC。该协议参考
  [Dazaike/gamesir-battery](https://github.com/Dazaike/gamesir-battery)（MIT）。
- 通过蓝牙以 Xbox 兼容模式连接时，手柄只暴露标准 Xbox HID 集合（Usage Page
  `0x01` / Usage `0x05`，输入 16 字节、输出与特性报告长度均为 0）。
  协议上电量由 Report ID `0x04` 上报（低 2 位档位 → `10/40/70/100%`），
  但 **Windows 的 Xbox 驱动不会把这帧交给用户态**：`ReadFile` / Raw Input /
  `HidD_GetInputReport` 均收不到数据，XInput 报告 `BATTERY_TYPE_WIRED`，
  GameInput 的 `GetBatteryState` 返回 `GameInputBatteryUnknown`，
  `Windows.Gaming.Input.Gamepad` 甚至枚举不到该设备。
  因此本程序在蓝牙模式下只能显示「未上报」，这是系统限制而非扫描失败。
- 想要精确百分比，请把手柄切到 **2.4G 接收器** 或 **USB 有线** 模式，
  `gamesir_nova2_lite.py` 会走厂商私有 HID 通道读取（已实测可用）。

**键盘与耳机**

经典蓝牙键盘（如 GANSS GS87D）不实现 GATT 电池服务，Windows 蓝牙栈不会
同步电量，因此只能显示「未上报电量」。

部分 TWS 耳机（如漫步者 LolliPods 2022 版）通过 HFP 上报电量，Windows 会把
该值写到 **HFP 服务节点**（`BTHENUM\{0000111E-...}`）的
`DEVPKEY_Bluetooth_Battery` 上，而不是 `BTHENUM\DEV_` 主节点。
`windows_bluetooth.py` 会同时读取主节点与服务节点的电量，因此这类耳机
也能正常显示。卡片上会标注电量实际来自 `GATT 电池服务` 还是
`HFP 电量上报`；HFP / GATT / HID 等服务节点只是数据来源，不会各自列成设备。
只有完全不向系统上报电量的耳机才显示「未上报电量」，
其电量仅在厂商 App 中可见。

**vivo / iQOO TWS 耳机**

iQOO TWS 5（vivo TWS 5 同协议）这类耳机虽然也通过 HFP 给系统一个总电量，
但左右耳和充电盒的数值只走厂商 GAIA 私有通道。配对后系统会为服务
`00001100-D102-11E1-9B23-00025B00A5A5` 建一个节点，`vivo_tws.py` 据此识别机型，
在 RFCOMM 通道上收发以下帧（已在 iQOO TWS 5 上实测）：

```text
FF 04 00 00 000A 0300               握手
FF 03 00 00 001B 0207               电量查询
FF 03 00 05 001B 8207 00 L R C F    上报（L/R 左右耳、C 充电盒、F 标志）
```

卡片会显示「左耳 88% · 右耳 80% · 充电盒 61%」，环形电量取左右耳中较低的一只。
读取需要耳机与电脑保持经典蓝牙连接；若耳机当前只连了 BLE（例如同时连在手机上），
该通道不可用，此时回退显示系统提供的单路总电量。漫步者 LolliPods 2022 版
没有左右耳接口，厂商通道只返回一个总电量，因此不单独适配。

## 许可证

[MIT](LICENSE)
