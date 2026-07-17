# Edge Gateway Windows 部署文档

本文档说明如何在 Windows 10/11 64 位电脑上部署并启动 Edge Gateway。示例安装目录为 `C:\edge_gateway`，默认使用本机 MongoDB 和 Mosquitto。

## 1. 安装软件

依次安装：

1. Python 3.11 64 位：<https://www.python.org/downloads/windows/>
2. MongoDB Community Server：<https://www.mongodb.com/try/download/community>
3. MongoDB Shell：<https://www.mongodb.com/try/download/shell>
4. Mosquitto 64 位：<https://mosquitto.org/download/>

安装 Python 时勾选 `Add python.exe to PATH`。安装 MongoDB 时选择安装为 Windows 服务。

安装完成后，重新打开 PowerShell并执行：

```powershell
py -3.11 --version
Get-Service MongoDB, mosquitto
```

## 2. 解压项目

将项目压缩包解压到：

```text
C:\edge_gateway
```

目录中应能看到：

```text
config
demo_platform
docs
src
tools
requirements-ubuntu.txt
```

不要把项目放在中文目录、桌面同步目录或路径过长的目录中。

## 3. 安装 Python 依赖

打开 PowerShell：

```powershell
Set-Location C:\edge_gateway
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r .\requirements-ubuntu.txt
python -m pip install -r .\demo_platform\requirements.txt
python -m pip check
```

看到 `No broken requirements found` 表示依赖安装完成。

## 4. 配置 MongoDB

启动 MongoDB：

```powershell
Start-Service MongoDB
Get-Service MongoDB
```

在 PowerShell 中运行 `mongosh`，创建网关数据库账号：

```javascript
use admin
db.createUser({
  user: "edge_gateway",
  pwd: "edge_gateway_2026",
  roles: [{ role: "readWrite", db: "edge_gateway" }]
})
```

如果提示用户已存在，可跳过创建。正式使用时应将示例密码替换为现场密码，并同步修改下一节中的配置。

## 5. 配置 Mosquitto

启动 Mosquitto：

```powershell
Start-Service mosquitto
Get-Service mosquitto
Test-NetConnection 127.0.0.1 -Port 1883
```

`TcpTestSucceeded` 显示 `True` 表示 MQTT 端口正常。

如果服务无法启动，检查 `C:\Program Files\mosquitto\mosquitto.conf` 是否存在重复的 `listener 1883`。本机测试可使用：

```conf
listener 1883 127.0.0.1
allow_anonymous true
```

修改配置后执行：

```powershell
Restart-Service mosquitto
```

## 6. 创建 Windows 配置

复制默认配置：

```powershell
Set-Location C:\edge_gateway
Copy-Item .\config\gateway.yaml .\config\gateway.windows.yaml
notepad .\config\gateway.windows.yaml
```

修改以下配置。

### 6.1 运行模式和设备编号

本地联调使用：

```yaml
gateway:
  sn: "EDGEGW001"

runtime:
  mode: "test"
```

### 6.2 MongoDB

```yaml
database:
  backend: "mongodb"
  mongodb:
    host: "127.0.0.1"
    port: 27017
    database: "edge_gateway"
    username: "edge_gateway"
    password: "edge_gateway_2026"
    auth_source: "admin"
    connect_timeout_ms: 5000
```

### 6.3 MQTT

```yaml
nanomq:
  enabled: true
  host: "127.0.0.1"
  port: 1883
  username: ""
  password: ""
  topic_prefix: "thing/product"
```

### 6.4 飞控连接

飞控通过 UDP 连接时：

```yaml
mavlink:
  enabled: true
  connection:
    type: "udp"
    host: "0.0.0.0"
    port: 14550
```

飞控通过串口连接时：

```yaml
mavlink:
  enabled: true
  connection:
    type: "serial"
    port: "COM3"
    baudrate: 921600
```

将 `COM3` 改为设备管理器中显示的实际串口。QGC、Mission Planner 和网关不能同时占用同一个串口。

暂时不接飞控时设置：

```yaml
mavlink:
  enabled: false
```

### 6.5 机巢连接

本机运行机巢模拟器时：

```yaml
nest_mavlink:
  enabled: true
  connection:
    type: "udp"
    host: "127.0.0.1"
    port: 14551
```

连接真实机巢时，将 `host` 改为机巢 IP。

### 6.6 Windows 路径

```yaml
media:
  storage_path: "C:/edge_gateway/data/media"

logging:
  dir: "C:/edge_gateway/logs"

request_trace:
  dir: "C:/edge_gateway/logs/request_trace"
```

Windows 路径建议使用 `/`，避免 YAML 中的反斜杠转义问题。

## 7. 启动机巢模拟器（可选）

不接真实机巢时，打开一个新的 PowerShell：

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

保持该窗口运行。

## 8. 启动云端模拟器（可选）

不连接真实云端平台时，打开一个新的 PowerShell：

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1

python .\tools\mqtt_cloud_simulator.py `
  --host 127.0.0.1 `
  --port 1883 `
  --sn EDGEGW001 `
  --bind-status bound `
  --enable-status `
  --max-requests 0
```

保持该窗口运行。

## 9. 启动 Edge Gateway

先启动 MongoDB、Mosquitto和需要使用的设备/模拟器，再打开一个新的 PowerShell：

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1
python .\src\main.py -c .\config\gateway.windows.yaml
```

正常启动时可以看到：

```text
MongoDB数据库初始化成功: edge_gateway
MQTT connected to broker
Edge Gateway started successfully
Entering async main loop
```

保持该窗口运行。

## 10. 启动操作页面

打开一个新的 PowerShell：

```powershell
Set-Location C:\edge_gateway
.\.venv\Scripts\Activate.ps1

$env:DEMO_HOST = "127.0.0.1"
$env:DEMO_PORT = "8095"
$env:MQTT_HOST = "127.0.0.1"
$env:MQTT_PORT = "1883"
$env:GATEWAY_SN = "EDGEGW001"
$env:DRONE_SN = "DRONESIM001"
$env:REAL_HD_WAYPOINTS_PATH = "C:/edge_gateway/tools/ardupilot_sitl/HD.copter-sitl.waypoints"

python .\demo_platform\app.py
```

浏览器打开：

```text
http://127.0.0.1:8095
```

## 11. 验证部署

1. 页面左下角 MQTT 显示“已连接”。
2. 飞行总览显示网关编号 `EDGEGW001`。
3. 机巢模拟器运行时，舱盖、推杆和充电状态能够显示。
4. 点击“刷新”，页面能够继续收到数据。
5. 打开“APIDock3 命令”，执行“核心属性查询”，页面返回成功。

未连接飞控时，飞行器遥测显示等待数据属于正常情况。

## 12. Windows 防火墙

本机访问不需要开放页面端口。需要其他局域网电脑访问时，将操作页面的 `DEMO_HOST` 改为 `0.0.0.0`，并以管理员身份执行：

```powershell
New-NetFirewallRule `
  -DisplayName "Edge Gateway Demo 8095" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 8095 `
  -RemoteAddress LocalSubnet
```

飞控通过其他设备发送 UDP 时，放行 `14550/UDP`。机巢使用其他电脑时，放行 `14551/UDP`。不要把 MongoDB `27017` 开放到公网。

## 13. 停止顺序

在各 PowerShell 窗口中按 `Ctrl+C`，按以下顺序停止：

1. 操作页面。
2. Edge Gateway。
3. 云端模拟器。
4. 机巢模拟器。

MongoDB 和 Mosquitto 可保持 Windows 服务运行。需要停止时执行：

```powershell
Stop-Service mosquitto
Stop-Service MongoDB
```

## 14. 常见问题

| 问题 | 处理方法 |
|---|---|
| PowerShell 不允许激活虚拟环境 | 执行 `Set-ExecutionPolicy -Scope Process Bypass` |
| `python` 命令不是 3.11 | 删除 `.venv`，使用 `py -3.11 -m venv .venv` 重新创建 |
| MongoDB 连接失败 | 检查 MongoDB 服务、账号、密码和 `auth_source` |
| MQTT 未连接 | 检查 Mosquitto 服务和 `1883` 端口 |
| 网关收到重复回包 | 关闭重复启动的网关或模拟器窗口 |
| 飞控无数据 | 检查 COM 口占用，或检查 UDP 目标 IP 和 `14550` 端口 |
| 机巢无数据 | 检查机巢模拟器窗口和 `14551` 配置 |
| 页面无法打开 | 检查 Demo Platform 窗口是否运行以及 `8095` 端口 |
| Windows 路径报错 | 检查配置是否使用 `C:/...` 格式 |

日常操作见 [Edge Gateway 使用手册](USER_GUIDE.md)。
