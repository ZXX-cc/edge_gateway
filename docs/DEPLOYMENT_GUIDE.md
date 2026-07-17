# Edge Gateway Windows 部署文档

本文档面向客户 Windows 电脑，说明 Edge Gateway 的安装、配置、首次启动、开机自启和升级方法。命令默认在 Windows PowerShell 中执行，示例安装目录为 `C:\edge_gateway`。

当前项目以 Windows 10/11 64 位系统、Python 3.11、MongoDB 和 Mosquitto 为推荐组合。接入真实飞控、机巢、负载或公网 MQTT 前，必须完成现场协议、凭证、网络和硬件 ACK 验收。

研发服务器上的 Ubuntu 联调方式继续参考 `docs/UBUNTU_SERVER_STARTUP_GUIDE.md`，不要把该文档中的 `apt`、`systemctl` 和 Bash 命令交给 Windows 客户执行。

## 1. 系统结构

```text
云端平台/云端模拟器
        |
        | MQTT TCP 1883
        v
MQTT Broker <-> Edge Gateway <-> MongoDB TCP 27017
                     |  |
       MAVLink 14550 |  | 机巢 MAVLink 14551
                     v  v
                 飞控     机巢
```

默认端口：

| 端口 | 协议 | 用途 | Windows 防火墙要求 |
|---:|---|---|---|
| `1883` | TCP | MQTT Broker | Broker 和网关同机时无需对外开放 |
| `27017` | TCP | MongoDB | 同机部署时只监听本机，不对外开放 |
| `14550` | UDP/TCP | 飞控标准 MAVLink | UDP 飞控从其他设备接入时按来源 IP 放行 |
| `14551` | UDP/TCP | 机巢自定义 MAVLink | 真实机巢从其他设备接入时按来源 IP 放行 |
| `8090` | TCP | Web Console | 可选；仅管理网开放 |
| `8095` | TCP | Demo Platform | 可选；仅管理网开放 |

`config/gateway.yaml` 中的配置段仍叫 `nanomq`，但实际可以连接 Mosquitto、EMQX 或 NanoMQ。当前 `cloud.*` 不作为网关的 MQTT 连接配置。

## 2. Windows 兼容边界

部署前先确认以下限制：

- `scripts/*.sh` 是 Ubuntu/Bash 脚本，Windows PowerShell 不能直接执行。
- 根目录 `start.sh` 是早期树莓派/NanoMQ 脚本，不是 Windows 部署入口。
- Web Console 可以在 Windows 上查看 MQTT、发送 API 和 MAVLink 命令，但当前页面内的“SITL 启动/停止”使用 `os.setsid/os.killpg`，只支持 Unix。Windows 上应从 WSL2 或另一台 Linux 电脑启动 SITL。
- `config/gateway.yaml` 默认的 `/data/media`、`/dev/video0` 等是 Linux 路径，Windows 部署必须修改。
- `src/nest_simulator.py` 默认包含 `/tmp` 和 `/home/...` 路径，Windows 启动时必须显式提供 Windows 路径参数。
- 当前 MQTT Python 客户端读取用户名和密码，但尚未应用 `nanomq.ssl` 配置。未完成 TLS 适配前，MQTT 只能部署在本机或受信任网络。

## 3. 安装清单

建议使用管理员账号安装以下软件：

| 软件 | 建议版本 | 安装要求 |
|---|---|---|
| Windows | Windows 10/11 64 位 | 安装最新安全更新 |
| Python | 3.11 64 位 | 勾选 `Add python.exe to PATH` 和 `py launcher` |
| Git for Windows | 当前稳定版 | 用于获取和升级项目 |
| MongoDB Community Server | 当前受支持稳定版 | MSI 安装时选择 `Install MongoDB as a Service` |
| MongoDB Shell | 与服务器兼容 | 用于初始化账号，可选安装 Compass |
| MongoDB Database Tools | 与服务器兼容 | 升级前使用 `mongodump` 备份 |
| Eclipse Mosquitto | 当前稳定版 64 位 | 安装为 Windows 服务，并包含命令行客户端 |
| NSSM | 当前稳定版 | 可选，用于把 Python 网关注册为 Windows 服务 |

安装完成后重新打开 PowerShell，检查：

```powershell
py --version
git --version
Get-Service MongoDB, mosquitto
```

如果命令找不到 Mosquitto 客户端，可直接使用：

```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" --help
```

## 4. 安装项目

建议安装到短路径，避免中文、空格和 Windows 路径长度问题：

```powershell
Set-Location C:\
git clone <REPOSITORY_URL> C:\edge_gateway
Set-Location C:\edge_gateway

py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r .\requirements-ubuntu.txt
python -m pip check
```

Windows 当前也使用 `requirements-ubuntu.txt`，因为它实际是项目的 UTF-8 通用依赖清单。不要使用历史 `requirements.txt`，其中包含不可移植的 Windows 本地 wheel 路径。

验证关键依赖：

```powershell
python -c "import yaml, paho.mqtt.client, pymavlink, sqlalchemy, pymongo, flask; print('dependencies ok')"
python -m compileall -q .\src .\tools .\web
```

如果 `pymavlink` 或 `lxml` 安装失败，优先确认使用的是 Python 3.11 64 位，并升级 `pip/setuptools/wheel`。不要在客户电脑上混用 Microsoft Store Python、32 位 Python 和多个虚拟环境。

## 5. 配置 MongoDB

启动并检查 MongoDB 服务：

```powershell
Set-Service MongoDB -StartupType Automatic
Start-Service MongoDB
Get-Service MongoDB
Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 27017
```

使用 `mongosh` 连接本机 MongoDB，并创建网关账号：

```javascript
use admin
db.createUser({
  user: "edge_gateway",
  pwd: passwordPrompt(),
  roles: [{ role: "readWrite", db: "edge_gateway" }]
})
```

生产环境应在 MongoDB 配置文件中启用授权，并确保只绑定 `127.0.0.1`。常见配置文件位置为：

```text
C:\Program Files\MongoDB\Server\<版本>\bin\mongod.cfg
```

修改 MongoDB 配置后重启：

```powershell
Restart-Service MongoDB
```

仅做临时演示且无法安装 MongoDB 时，可以显式使用 SQLite：

```powershell
$env:DATABASE_BACKEND = "sqlite"
python .\src\main.py -c .\config\gateway.yaml
```

SQLite 会在项目目录生成 `missions.db`。它只适合本机联调，不作为正式交付数据库。

## 6. 配置 Mosquitto

本机 Broker 的最小检查：

```powershell
Set-Service mosquitto -StartupType Automatic
Start-Service mosquitto
Get-Service mosquitto
Test-NetConnection 127.0.0.1 -Port 1883
```

Mosquitto 默认配置通常位于：

```text
C:\Program Files\mosquitto\mosquitto.conf
```

仅限本机联调的配置示例：

```conf
listener 1883 127.0.0.1
allow_anonymous true
```

需要账号认证时，使用 Mosquitto 自带工具创建密码文件，并在配置中设置 `allow_anonymous false` 和 `password_file`。修改后执行：

```powershell
Restart-Service mosquitto
```

若 `1883` 已由 Docker Desktop 中的 EMQX 等 Broker 占用，不要同时启动 Mosquitto。先定位监听进程：

```powershell
$Listener = Get-NetTCPConnection -State Listen |
  Where-Object LocalPort -eq 1883 |
  Select-Object -First 1
$Listener | Select-Object LocalAddress, LocalPort, OwningProcess
Get-Process -Id $Listener.OwningProcess
```

## 7. 创建 Windows 配置

先备份默认配置：

```powershell
Copy-Item .\config\gateway.yaml .\config\gateway.windows.yaml
notepad .\config\gateway.windows.yaml
```

客户部署建议使用 `config/gateway.windows.yaml`，至少修改以下内容：

```yaml
runtime:
  mode: "production"             # 模拟联调保持 test

gateway:
  sn: "REALDOCK001"              # 使用平台分配的真实纯字母数字 SN

database:
  backend: "mongodb"
  mongodb:
    host: "127.0.0.1"
    port: 27017
    database: "edge_gateway"
    username: "edge_gateway"
    password: "REPLACE_WITH_REAL_PASSWORD"
    auth_source: "admin"
    connect_timeout_ms: 5000

nanomq:
  host: "127.0.0.1"
  port: 1883
  username: ""
  password: ""

device:
  topology:
    device_secret: "REPLACE_WITH_REAL_SECRET"
    nonce: "REPLACE_WITH_REAL_NONCE"

media:
  storage_path: "C:/edge_gateway/data/media"
  video_sources:
    normal: "rtsp://127.0.0.1/live/normal"
    wide: "rtsp://127.0.0.1/live/wide"
    zoom: "rtsp://127.0.0.1/live/zoom"
    ir: "rtsp://127.0.0.1/live/ir"

logging:
  dir: "C:/edge_gateway/logs"

request_trace:
  dir: "C:/edge_gateway/logs/request_trace"
```

YAML 中建议使用正斜杠 `/` 表示 Windows 路径，避免反斜杠转义问题。生产模式会拒绝测试 SN、空 `device_secret` 和空 `nonce`。

不要把客户密码、设备密钥或 nonce 提交到 Git。客户配置文件至少限制为仅管理员和运行账号可读。

## 8. 配置真实设备链路

### 8.1 UDP 飞控

飞控通过网络向 Windows 电脑发送 MAVLink 时：

```yaml
mavlink:
  enabled: true
  connection:
    type: "udp"
    host: "0.0.0.0"
    port: 14550
```

飞控必须把 MAVLink 目标设置为 Windows 电脑 IP 的 `14550/UDP`。

### 8.2 串口飞控

先在设备管理器确认 COM 口，例如 `COM3`：

```yaml
mavlink:
  enabled: true
  connection:
    type: "serial"
    port: "COM3"
    baudrate: 921600
```

关闭会独占串口的 QGC、Mission Planner 或串口调试工具后再启动网关。同一个 COM 口不能同时被多个程序打开。

### 8.3 机巢链路

`nest_mavlink.connection.host` 是机巢目标地址，不能设置为 `0.0.0.0`：

```yaml
nest_mavlink:
  enabled: true
  connection:
    type: "udp"
    host: "192.168.1.50"
    port: 14551
```

暂时不接某条链路时，把对应 `enabled` 设置为 `false`。网关可能在设备离线时仍保持进程运行，因此必须检查实际链路日志，不能只看窗口是否存在。

## 9. 配置 Windows 防火墙

如果真实飞控和机巢从其他设备向 Windows 发送 UDP，以管理员身份执行：

```powershell
New-NetFirewallRule `
  -DisplayName "Edge Gateway MAVLink 14550" `
  -Direction Inbound -Action Allow -Protocol UDP -LocalPort 14550 `
  -RemoteAddress <FLIGHT_CONTROLLER_IP>

New-NetFirewallRule `
  -DisplayName "Edge Gateway Nest 14551" `
  -Direction Inbound -Action Allow -Protocol UDP -LocalPort 14551 `
  -RemoteAddress <NEST_IP>
```

只有局域网管理员需要访问 Web/Demo 时才开放：

```powershell
New-NetFirewallRule `
  -DisplayName "Edge Gateway Web 8090" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8090 `
  -RemoteAddress LocalSubnet
```

不要开放 MongoDB `27017` 到公网。Web Console 和 Demo Platform 当前没有内置登录，也不能暴露到公网。

## 10. 首次前台启动

先启动 MongoDB、Broker和真实设备/模拟器，再启动网关：

```powershell
Set-Location C:\edge_gateway
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python .\src\main.py -c .\config\gateway.windows.yaml
```

正常启动的关键输出：

```text
MongoDB数据库初始化成功: edge_gateway
MQTT connected to broker
MAVLink dispatcher started
Nest MAVLink dispatcher started
Edge Gateway started successfully
Entering async main loop
```

没有连接某条设备链路时，对应 dispatcher 可能不会出现。按 `Ctrl+C` 可正常停止网关。

验证 MongoDB：

```powershell
python .\tools\mongodb_runtime_audit.py `
  --config .\config\gateway.windows.yaml `
  --write-check
```

验证 MQTT 请求/回包：

```powershell
python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method dock_status_query `
  --timeout 8
```

成功标准是收到同一 `tid/method` 的正确 reply，且命令退出码为 `0`。

## 11. 配置开机自启

### 11.1 推荐：NSSM Windows 服务

将受信任来源下载的 `nssm.exe` 放到 `C:\tools\nssm\win64\nssm.exe`。以管理员身份打开 PowerShell：

```powershell
$Nssm = "C:\tools\nssm\win64\nssm.exe"
$Project = "C:\edge_gateway"

New-Item -ItemType Directory -Force "$Project\logs" | Out-Null

& $Nssm install EdgeGateway "$Project\.venv\Scripts\python.exe"
& $Nssm set EdgeGateway AppDirectory "$Project"
& $Nssm set EdgeGateway AppParameters "src\main.py -c config\gateway.windows.yaml"
& $Nssm set EdgeGateway AppStdout "$Project\logs\service_stdout.log"
& $Nssm set EdgeGateway AppStderr "$Project\logs\service_stderr.log"
& $Nssm set EdgeGateway AppRotateFiles 1
& $Nssm set EdgeGateway AppRotateOnline 1
& $Nssm set EdgeGateway AppRotateBytes 10485760
& $Nssm set EdgeGateway AppExit Default Restart
& $Nssm set EdgeGateway Start SERVICE_AUTO_START

Start-Service EdgeGateway
Get-Service EdgeGateway
```

停止和重启：

```powershell
Stop-Service EdgeGateway
Restart-Service EdgeGateway
```

Windows 原生 `sc.exe create` 不能直接把普通 Python 控制台程序可靠地注册为服务，因为 Python 程序没有实现 Windows Service Control Manager 协议。必须使用 NSSM、WinSW 或经过验证的同类包装器。

NSSM 默认可能使用本地系统账号运行。正式部署应在 Windows“服务”的登录设置中改为专用低权限账号，并只授予项目配置、日志、媒体目录和所需 COM 口的访问权限。

### 11.2 不安装服务包装器

可以在任务计划程序中创建“系统启动时”任务：

- 程序：`C:\edge_gateway\.venv\Scripts\python.exe`
- 参数：`src\main.py -c config\gateway.windows.yaml`
- 起始于：`C:\edge_gateway`
- 勾选“使用最高权限运行”。
- 设置失败后每 1 分钟重启，至少重试 3 次。

任务运行账号必须拥有项目、日志、媒体和串口访问权限。

## 12. 安全要求

- 同一个 Broker 上，同一网关 SN 只能运行一个网关实例。
- MongoDB 只允许本机连接，并启用认证。
- MQTT 未完成 TLS 客户端接入前，只能使用本机或受信任网络。
- Web/Demo 没有登录认证，只允许管理网访问。
- Windows Defender/杀毒软件若拦截 Python、UDP 或日志目录，应创建最小范围的程序/目录规则，不要整体关闭安全软件。
- 真实飞控操作前确认空域、人员、螺旋桨、急停、任务和 DRC 状态。
- 客户密钥不得写入截图、工单、Git 或共享聊天记录。

## 13. 升级与回滚

升级前停止网关并备份：

```powershell
Stop-Service EdgeGateway

Copy-Item .\config\gateway.windows.yaml `
  .\config\gateway.windows.yaml.backup -Force

mongodump `
  --host 127.0.0.1 `
  --username edge_gateway `
  --authenticationDatabase admin `
  --db edge_gateway `
  --out C:\edge_gateway_backup\mongodb
```

更新代码和依赖：

```powershell
Set-Location C:\edge_gateway
git fetch --all --prune
git checkout <RELEASE_TAG_OR_COMMIT>

.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements-ubuntu.txt
python -m pip check

Start-Service EdgeGateway
Get-Service EdgeGateway
```

回滚时停止服务，切回已验证的发布标签或提交，恢复匹配配置。只有数据模型不兼容或数据损坏时才恢复 MongoDB 备份。

## 14. 常见故障

| 现象 | 检查与处理 |
|---|---|
| PowerShell 禁止激活脚本 | 当前窗口执行 `Set-ExecutionPolicy -Scope Process Bypass` |
| `python` 版本不对 | 使用 `py -3.11` 重建 `.venv`，确认 64 位 Python |
| MongoDB 连接超时 | `Get-Service MongoDB`、端口、账号、密码和 `auth_source` |
| MQTT 进入 offline mode | `Get-Service mosquitto`、1883 监听、账号、ACL 和 `nanomq.host/port` |
| 同一请求收到多个 reply | 任务管理器中存在多个 `python.exe` 或相同 SN 的其他客户端 |
| 串口无法打开 | 检查 COM 号、波特率和 QGC/Mission Planner 串口占用 |
| UDP 飞控无心跳 | 检查飞控目标 IP、14550 防火墙和 Windows 网卡地址 |
| 机巢命令无响应 | 检查 `nest_mavlink.host/port` 和 `logs/nest_plc/` |
| 媒体目录不可写 | 把 `media.storage_path` 改为当前账号可写的 Windows 目录 |
| Web 中启动 SITL 失败 | 当前按钮不支持 Windows，改从 WSL2/Linux 外部启动 SITL |
| 服务启动后立即停止 | 查看 `logs/service_stderr.log` 和网关日期日志 |

日常操作步骤见 `docs/USER_GUIDE.md`，MQTT 字段和错误码见 `docs/INTERFACE_DESIGN_SPEC.md`。
