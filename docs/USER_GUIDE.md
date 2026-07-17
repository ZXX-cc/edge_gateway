# Edge Gateway Windows 使用文档

本文档面向 Windows 客户端操作人员，说明 Edge Gateway 的日常启动、状态检查、Web/Demo 操作、常用 MQTT 命令、日志查询和安全停机。

首次安装、MongoDB/MQTT 配置、真实设备参数和开机自启请先阅读 `docs/DEPLOYMENT_GUIDE.md`。本文默认项目位于 `C:\edge_gateway`，客户配置为 `config\gateway.windows.yaml`。

## 1. 日常运行组件

| 组件 | Windows 运行方式 | 是否必需 |
|---|---|---|
| MongoDB | Windows 服务 `MongoDB` | 默认必需；SQLite 临时模式除外 |
| MQTT Broker | Windows 服务 `mosquitto` 或 Docker EMQX | 必需 |
| Edge Gateway | 前台 Python、NSSM 服务或任务计划 | 必需 |
| 飞控 | 串口、UDP/TCP 真实设备或外部 SITL | 按业务需要 |
| 机巢 | 真实机巢或 Windows 机巢模拟器 | 按业务需要 |
| 云端 | 真实平台或 Windows 云端模拟器 | 按业务需要 |
| Web Console | `web\app.py` | 可选 |
| Demo Platform | `demo_platform\app.py` | 可选 |

Windows 不直接执行仓库中的 `scripts/*.sh`。文档中的 PowerShell 命令已经替代这些 Ubuntu 脚本。

## 2. 每日启动

### 2.1 启动基础服务

以管理员身份打开 PowerShell：

```powershell
Start-Service MongoDB
Start-Service mosquitto

Get-Service MongoDB, mosquitto
Test-NetConnection 127.0.0.1 -Port 1883
Test-NetConnection 127.0.0.1 -Port 27017
```

两项服务应显示 `Running`，端口检查的 `TcpTestSucceeded` 应为 `True`。

如果 Broker 在 Docker Desktop 或其他电脑上，不要启动本机 Mosquitto，改为检查配置中的 Broker 地址：

```powershell
Test-NetConnection <MQTT_BROKER_IP> -Port 1883
```

### 2.2 启动 Edge Gateway 服务

使用 NSSM 部署时：

```powershell
Start-Service EdgeGateway
Get-Service EdgeGateway
```

状态应为 `Running`。查看最近日志：

```powershell
Get-Content C:\edge_gateway\logs\service_stderr.log -Tail 50
```

### 2.3 前台启动

调试或未配置 Windows 服务时：

```powershell
Set-Location C:\edge_gateway
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python .\src\main.py -c .\config\gateway.windows.yaml
```

按 `Ctrl+C` 正常停止。不要同时启动 NSSM 服务和前台网关，否则相同 SN 会产生重复回包。

## 3. 启动 Windows 模拟链路

客户连接真实飞控和真实机巢时跳过本节。Windows 原生可以运行云端模拟器和机巢模拟器；ArduPilot SITL 建议在 WSL2 或另一台 Linux 电脑运行。

### 3.1 云端模拟器

打开一个新的 PowerShell：

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1

python .\tools\mqtt_cloud_simulator.py `
  --host 127.0.0.1 `
  --port 1883 `
  --sn EDGEGW001 `
  --bind-status bound `
  --enable-status `
  --max-requests 0 `
  --timeout 86400
```

模拟器必须先于网关启动，才能处理网关启动时主动发送的 `requests/status`。

### 3.2 机巢模拟器

另开 PowerShell，显式覆盖 Linux 默认路径：

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1

New-Item -ItemType Directory -Force .\logs\nest_simulator | Out-Null
New-Item -ItemType Directory -Force .\data | Out-Null

python .\src\nest_simulator.py `
  --host 0.0.0.0 `
  --port 14551 `
  --command-log .\logs\nest_simulator\command_trace.log `
  --nest-status-log .\logs\nest_simulator\nest_status.log `
  --fault-state-file .\data\nest_faults.json `
  --fault-log .\logs\nest_simulator\fault_injection.log `
  --mission-file .\tools\ardupilot_sitl\HD.copter-sitl.waypoints
```

`config\gateway.windows.yaml` 的 `nest_mavlink.connection.host/port` 应为 `127.0.0.1:14551`。

### 3.3 ArduPilot SITL

当前 Web Console 的“SITL 启动/停止”按钮不支持 Windows 原生环境。推荐方式：

1. 在 WSL2、Linux 虚拟机或另一台 Linux 电脑安装 ArduPilot。
2. 将 SITL MAVLink 输出设置为 Windows 电脑 IP 的 `14550/UDP`。
3. Windows 防火墙只允许该 SITL 来源 IP 访问 `14550/UDP`。
4. SITL 出现 heartbeat 后再启动或重启 Edge Gateway。

Linux/WSL2 侧输出示例：

```bash
sim_vehicle.py -v ArduCopter --out=udp:<WINDOWS_IP>:14550
```

WSL2 网络模式不同，Windows 主机 IP 不一定是 `127.0.0.1`。以 WSL2 实际路由和 Windows 网卡地址为准。

## 4. 状态检查

检查进程和端口：

```powershell
Get-Service MongoDB, mosquitto, EdgeGateway

Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 1883,27017,8090,8095 } |
  Select-Object LocalAddress, LocalPort, OwningProcess

Get-NetUDPEndpoint |
  Where-Object { $_.LocalPort -in 14550,14551 } |
  Select-Object LocalAddress, LocalPort, OwningProcess

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'src\\main.py|nest_simulator.py|web\\app.py|demo_platform\\app.py' } |
  Select-Object ProcessId, CommandLine
```

网关日志中应根据实际部署看到：

```text
MongoDB数据库初始化成功: edge_gateway
MQTT connected to broker
MAVLink dispatcher started
Nest MAVLink dispatcher started
Edge Gateway started successfully
Entering async main loop
```

进程为 `Running` 不代表全部链路可用。没有真实飞控时不会有有效飞控 OSD，没有机巢链路时机场动作不会完成。

## 5. 观察 MQTT 消息

使用 Mosquitto 客户端订阅当前网关：

```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" `
  -h 127.0.0.1 `
  -p 1883 `
  -t "thing/product/EDGEGW001/#" `
  -t "sys/product/EDGEGW001/#" `
  -v
```

正常运行时常见 Topic：

- `thing/product/{sn}/osd`：定频遥测。
- `thing/product/{sn}/state`：属性和状态变化。
- `thing/product/{sn}/events`：心跳、任务、告警和操作事件。
- `thing/product/{sn}/services_reply`：服务回包。
- `thing/product/{sn}/property/set_reply`：属性设置回包。
- `sys/product/{sn}/status`：设备拓扑和状态上报。

真实客户 SN 不是 `EDGEGW001` 时，命令、Web、Demo 和测试工具必须全部改用同一个 SN。

## 6. 使用 Web Console

### 6.1 启动

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1

$env:WEB_HOST = "127.0.0.1"
$env:WEB_PORT = "8090"
$env:MQTT_HOST = "127.0.0.1"
$env:MQTT_PORT = "1883"
$env:GATEWAY_SN = "EDGEGW001"
$env:MAVLINK_URL = "udpout:127.0.0.1:14550"

python .\web\app.py
```

浏览器打开：

```text
http://127.0.0.1:8090
```

需要从局域网访问时把 `WEB_HOST` 改为 `0.0.0.0`，并只向管理网开放 Windows 防火墙端口。页面没有内置登录。

### 6.2 页面操作

推荐顺序：

1. 确认顶部 MQTT 状态为“已连接”。
2. 确认飞控已经从真实设备或外部 SITL向 `14550` 发送数据。
3. 查看 MQTT 消息流和 OSD 高度、电量、经纬度、GPS、飞行模式。
4. 在 API 冒烟区域发送业务 method，并核对 request/reply。
5. 结束飞控操作时执行降落或返航，并确认真实状态完成。

基础查询示例：

```text
channel: services
method:  dock_status_query
payload: {}
```

Windows 限制：

- 不使用页面中的“SITL 启动/停止”按钮。
- MAVLink 按钮会直接向飞控发命令，不是页面动画。
- 接入真实设备时，必须先确认空域、急停、人员和任务占用状态。

## 7. 使用 Demo Platform

Demo Platform 用于业务演示和验收，不会替代 Broker 或网关：

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\demo_platform\requirements.txt

$env:DEMO_HOST = "127.0.0.1"
$env:DEMO_PORT = "8095"
$env:MQTT_HOST = "127.0.0.1"
$env:MQTT_PORT = "1883"
$env:GATEWAY_SN = "EDGEGW001"
$env:DRONE_SN = "DRONESIM001"
$env:REAL_HD_WAYPOINTS_PATH = "C:/edge_gateway/tools/ardupilot_sitl/HD.copter-sitl.waypoints"

python .\demo_platform\app.py
```

浏览器打开 `http://127.0.0.1:8095`。主要功能：

- 基础上线巡检。
- Dock 3 CMD 机场动作。
- DRC 进入、控制、心跳和退出。
- 航线准备、执行、进度、暂停、恢复和返航。
- 飞行日志、原始 MQTT 报文和复测证据。

真实航线不能只看页面返回成功，还要同时确认飞控/QGC 实际执行、`flighttask_progress` 更新、OSD 位置变化以及最终任务状态。

## 8. 命令行常用操作

先进入虚拟环境：

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1
```

所有工具都可以通过 `--config .\config\gateway.windows.yaml` 读取客户 MQTT 地址和 SN。

### 8.1 查询机场状态

```powershell
python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method dock_status_query `
  --timeout 8
```

### 8.2 读取属性

```powershell
python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method property_get `
  --payload '{"properties":["silent_mode","drc_state"]}' `
  --timeout 8
```

### 8.3 设置可写属性

```powershell
python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method property_set `
  --payload '{"silent_mode":1}' `
  --expect-property-result silent_mode=0 `
  --timeout 8
```

必须检查 `property/set_reply` 中每个字段的 `state.result`。请求成功到达 Broker 不代表只读属性已被修改。

### 8.4 DRC 进入和退出

```powershell
python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method drc_mode_enter `
  --payload '{}' `
  --timeout 12

python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method drc_mode_exit `
  --payload '{}' `
  --timeout 12
```

`drc_mode_enter/exit` 走 `services/services_reply`，不能发送到 `drc/down`。进入 DRC 前必须保证飞控在线、任务空闲且没有急停、OTA 或其他控制占用。

### 8.5 严格检查重复回包

```powershell
python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method dock_status_query `
  --quiet-stale `
  --fail-on-stale `
  --timeout 8
```

严格模式失败通常表示同一 SN 存在多个网关进程、旧模拟器或其他响应端。必须先清理重复发布源。

## 9. 常用业务流程

### 9.1 设备上线与绑定

1. 真实云平台或云端模拟器先启动。
2. 网关连接 Broker，并上报在线和拓扑。
3. 网关主动发送 `airport_bind_status`。
4. 云端通过 `requests_reply` 返回绑定状态。
5. 平台确认网关和子设备 SN、组织及在线状态。

专项验收：

```powershell
python .\tools\mqtt_dock3_organization_test.py `
  --config .\config\gateway.windows.yaml `
  --timeout 20
```

### 9.2 机场动作

```powershell
python .\tools\mqtt_dock3_cmd_test.py `
  --config .\config\gateway.windows.yaml `
  --timeout 20 `
  --quiet-stale `
  --fail-on-stale
```

舱盖、推杆、充电和补光灯等动作必须同时检查：

- `services_reply` 接收结果。
- 进度和完成事件。
- `14551` 机巢最终应答。
- `logs\nest_plc\` 中的动作轨迹。
- 真实机构和传感器状态。

首次 accepted 回包不代表物理动作已经完成。

### 9.3 航线任务

典型顺序：

```text
flighttask_prepare
  -> flighttask_execute
  -> flighttask_progress
  -> pause/resume（可选）
  -> return/terminate（按任务结果）
```

模拟专项：

```powershell
python .\tools\mqtt_dock3_wayline_test.py `
  --config .\config\gateway.windows.yaml `
  --timeout 20 `
  --quiet-stale `
  --fail-on-stale
```

真实航线执行前确认飞控在线、起飞点、航线文件、空域、电池、GPS/RTK、机巢状态和返航策略。不要在飞行器已在空中、DRC 占用或其他任务运行时重复下发。

### 9.4 其他功能

| 功能 | Windows 命令工具 |
|---|---|
| 媒体文件 | `tools\mqtt_dock3_media_test.py` |
| 直播 | `tools\mqtt_dock3_live_test.py` |
| HMS/日志 | `tools\mqtt_dock3_hms_log_test.py` |
| 固件/配置 | `tools\mqtt_dock3_firmware_config_test.py` |
| 相机/云台/负载 | `tools\mqtt_dock3_payload_test.py` |

这些工具可能使用模拟 adapter。真实相机、负载、直播源、OSS 和固件升级需要单独完成硬件 ACK、凭证和数据安全验收。

## 10. 日志和请求追踪

Windows 客户配置默认将日志写到 `C:\edge_gateway\logs`：

| 路径 | 内容 |
|---|---|
| `logs\YYYY-MM\edge_gateway_YYYY-MM-DD.log` | 网关总日志 |
| `logs\gateway\YYYY-MM\` | 主控、连接和状态机 |
| `logs\mqtt\YYYY-MM\` | MQTT 收发和回包 |
| `logs\mavlink_raw\YYYY-MM\` | MAVLink 原始帧 |
| `logs\flight_control\YYYY-MM\` | 飞控模式、解锁和控制 |
| `logs\nest_plc\YYYY-MM\` | 机巢协议和动作 |
| `logs\mission\YYYY-MM\` | 航线和任务 |
| `logs\alarm\YYYY-MM\` | HMS 和告警 |
| `logs\payload\YYYY-MM\` | 相机、云台和负载 |
| `logs\request_trace\YYYY-MM\*.jsonl` | MQTT 请求全生命周期 |

查看当天总日志：

```powershell
$Month = Get-Date -Format "yyyy-MM"
$Day = Get-Date -Format "yyyy-MM-dd"
Get-Content ".\logs\$Month\edge_gateway_$Day.log" -Tail 100 -Wait
```

按 method 查询请求：

```powershell
python .\tools\request_trace_query.py `
  --config .\config\gateway.windows.yaml `
  --method dock_status_query `
  --days 1
```

按 `tid` 查询完整生命周期：

```powershell
python .\tools\request_trace_query.py `
  --config .\config\gateway.windows.yaml `
  --tid <TID> `
  --days 1
```

## 11. 安全停止和重启

停止操作前：

1. 停止平台继续下发命令。
2. 确认航线、DRC、OTA、媒体上传等操作已结束或安全中止。
3. 确认飞行器已经安全降落，机巢动作处于可停止状态。

NSSM 服务方式：

```powershell
Stop-Service EdgeGateway
Get-Service EdgeGateway
```

前台方式在网关窗口按 `Ctrl+C`，等待离线事件和数据库连接关闭。不要直接结束全部 `python.exe`，否则可能同时终止模拟器、Web、Demo 或其他 Python 程序。

建议停机顺序：

1. Edge Gateway。
2. Web Console 和 Demo Platform。
3. 外部 SITL、机巢模拟器和云端模拟器。
4. 最后按需停止 Mosquitto 和 MongoDB。

重启服务：

```powershell
Restart-Service EdgeGateway
Get-Service EdgeGateway
```

重启后先检查设备在线、任务恢复状态和重复回包，再恢复控制操作。

## 12. 常见问题

| 现象 | 处理 |
|---|---|
| 网关服务显示 Running 但无 MQTT | 检查 `logs\service_stderr.log`、Broker 地址、账号和 ACL |
| 没有飞控 OSD | 检查 COM 口或飞控 UDP 是否发到 Windows `14550` |
| 机场 OSD 或动作不更新 | 检查真实机巢/模拟器和 `14551`、`logs\nest_plc\` |
| Web 显示错误 SN | 启动前设置正确的 `$env:GATEWAY_SN` |
| Web 点击启动 SITL 失败 | Windows 不支持当前页面内启动方式，改用 WSL2/Linux 外部 SITL |
| 请求超时 | 检查 Topic、SN、method、网关订阅和状态机准入日志 |
| 收到两个 reply | 检查服务和任务管理器中是否有两个相同 SN 的网关进程 |
| DRC/任务被拒绝 | 处理急停、设备离线、任务占用、DRC 占用或 OTA 前置条件 |
| 串口拒绝访问 | 关闭 QGC/Mission Planner 等占用 COM 口的程序 |
| 路径出现 `/data` 或 `/tmp` | 使用 `gateway.windows.yaml`，并为模拟器显式传 Windows 路径 |
| PowerShell JSON 参数解析失败 | 使用单引号包围 JSON，例如 `'{}'` 或 `'{"silent_mode":1}'` |

业务状态和异常分支见 `docs/业务流程.md`，接口字段和 DJI 错误码见 `docs/INTERFACE_DESIGN_SPEC.md`。
