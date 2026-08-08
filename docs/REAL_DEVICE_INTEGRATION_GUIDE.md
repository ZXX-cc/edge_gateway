# Edge Gateway Windows 真机接入与启动说明

更新时间：2026-07-17

- 飞控使用独立的 MAVLink 串口、UDP 或 TCP 链路，对应 YAML 中的 `mavlink`。
- 机巢使用独立的 TCP 链路，对应 YAML 中的 `nest_mavlink`。

## 1. 必须记住的路径和命令

| 项目            | Windows 位置                                          |
| --------------- | ----------------------------------------------------- |
| 项目目录        | `C:\Project\edge_gateway`                             |
| 唯一使用的 YAML | `C:\Project\edge_gateway\config\gateway.windows.yaml` |
| YAML 原始模板   | `C:\Project\edge_gateway\config\gateway.yaml`         |
| 主程序          | `C:\Project\edge_gateway\src\main.py`                 |
| 测试脚本目录    | `C:\Project\edge_gateway\tools`                       |
| 日志目录        | `C:\Project\edge_gateway\logs`                        |

只修改 `config\gateway.windows.yaml`，不要直接修改仓库模板 `config\gateway.yaml`。

在 Windows CMD 中启动网关的完整命令是：

```bat
cd /d C:\Project\edge_gateway
call .venv\Scripts\activate.bat
python src\main.py -c config\gateway.windows.yaml
```

在 PowerShell 中启动网关的完整命令是：

```powershell
Set-Location C:\Project\edge_gateway
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python .\src\main.py -c .\config\gateway.windows.yaml
```

## 2. 固定连接结构

现场连接固定为：

```text
                         +--> 飞控串口、UDP 或 TCP（mavlink）
Windows Edge Gateway ----+
                         +--> 机巢 TCP（nest_mavlink）

Windows Edge Gateway --------> MQTT Broker --------> 客户平台
Windows Edge Gateway --------> MongoDB
```

两个设备链路必须同时启用：

```yaml
mavlink:
  enabled: true

nest_mavlink:
  enabled: true
```

不要把飞控和机巢配置成同一个 IP/端口，也不要让两个程序同时占用同一个飞控串口。

## 3. 接入前向厂家和平台取得的信息

| 类别       | 必须取得的信息                                                  | 写入位置                      |
| ---------- | --------------------------------------------------------------- | ----------------------------- |
| 网关身份   | 真实网关 SN、`device_secret`、`nonce`                           | `gateway`、`device.topology`  |
| 飞行器身份 | 真实飞行器 SN、domain、type、sub_type、index、密钥、nonce       | `device.sub_devices`          |
| 飞控       | 固件类型、连接方式、COM 口或 IP/端口、波特率、目标 sysid/compid | `mavlink` 及飞控模块          |
| 机巢       | 真机 IP、TCP 端口、协议版本、MAVLink XML/头文件                 | `nest_mavlink` 及机巢协议代码 |
| MQTT       | Broker 地址、端口、用户名、密码、是否使用 TLS                   | `nanomq`                      |
| MongoDB    | 地址、端口、数据库、用户名、密码、authSource                    | `database.mongodb`            |
| ViewLink   | SDK 目录、负载 IP、端口、连接类型                               | 负载代码；当前尚未完整配置化  |
| 直播       | RTSP/HTTP 视频源、推流地址、FFmpeg                              | `media.video_sources`         |
| RTK        | NTRIP 地址、端口、挂载点、用户名和密码                          | `rtk`                         |

没有取得真实机巢 TCP 端口时不能继续配置。仓库默认 YAML 中的机巢端口只是开发配置，不能当作真机端口。

当前飞控模式按 ArduPilot Copter、Plane 和 QuadPlane 实现。客户使用 PX4 或厂家自定义模式时，需要先完成代码适配。

## 4. 创建和检查 Windows YAML

### 4.1 YAML 在哪里

实际运行文件固定为：

```text
C:\Project\edge_gateway\config\gateway.windows.yaml
```

该文件不是仓库自带文件。第一次部署时，在 PowerShell 中创建：

```powershell
Set-Location C:\Project\edge_gateway
Copy-Item .\config\gateway.yaml .\config\gateway.windows.yaml
notepad .\config\gateway.windows.yaml
```

如果文件已经存在，不要再次复制，否则会覆盖已经填写的参数。

配置中包含设备密钥和密码，不要提交 `gateway.windows.yaml`。需要提交模板时，只能提交删除真实凭据后的 example 文件。

### 4.2 修改原则

在文件中搜索并修改现有节点，不要在文件末尾再次粘贴同名的 `gateway`、`mavlink`、`nest_mavlink` 或 `device` 节点。

YAML 使用两个空格缩进，不使用 Tab。Windows 路径优先写成 `C:/Project/...`。

下面这种写法少了结尾引号，会造成 `expected <block end>`：

```yaml
media:
  storage_path: "C:/Project/edge_gateway/data/media
```

正确写法是：

```yaml
media:
  storage_path: "C:/Project/edge_gateway/data/media"
```

## 5. 必须修改的 YAML 节点

所有配置都在同一个文件中修改：

```text
C:\Project\edge_gateway\config\gateway.windows.yaml
```

不要依赖行号，使用编辑器搜索下表中的 YAML 节点名：

| 要配置的内容 | YAML 节点                                                |
| ------------ | -------------------------------------------------------- |
| 生产模式     | `runtime.mode`                                           |
| 网关 SN      | `gateway.sn`                                             |
| MongoDB      | `database.mongodb`                                       |
| MQTT         | `nanomq`                                                 |
| 飞控连接     | `mavlink.connection`                                     |
| 机巢连接     | `nest_mavlink.connection`                                |
| 网关拓扑密钥 | `device.topology`                                        |
| 飞行器拓扑   | `device.sub_devices`                                     |
| 自动收尾开关 | `mission.completion.enabled`                             |
| Windows 路径 | `media.storage_path`、`logging.dir`、`request_trace.dir` |

### 5.1 运行模式、网关身份和 MongoDB

```yaml
runtime:
  mode: "production"

gateway:
  sn: "CHANGE_ME_GATEWAY_SN"
  name: "现场网关名称"
  product_type: "edge-gateway"

database:
  backend: "mongodb"
  mongodb:
    host: "127.0.0.1"
    port: 27017
    database: "edge_gateway"
    username: "edge_gateway"
    password: "edge_gateway"
    auth_source: "admin"
    connect_timeout_ms: 5000
```

### 5.2 MQTT

如果网关和 Mosquitto 安装在同一台电脑：

```yaml
nanomq:
  enabled: true
  host: "127.0.0.1"
  port: 1883
  username: ""
  password: ""
  topic_prefix: "thing/product"
```

如果连接远端 Broker：

```yaml
nanomq:
  enabled: true
  host: "CHANGE_ME_MQTT_HOST"
  port: 1883
  username: "CHANGE_ME_MQTT_USERNAME"
  password: "CHANGE_ME_MQTT_PASSWORD"
  topic_prefix: "thing/product"
```

主程序已经支持 MQTT 用户名和密码。当前 `nanomq.ssl` 尚未接入 `src/mqtt/client.py`，需要 TLS/8883 时必须先完成第 10.4 节的代码修改，不能只修改端口。

### 5.3 飞控连接：`mavlink`

`mavlink` 只连接真实飞控。根据现场接口从下面三种连接中选择一种。

#### 串口直连

```yaml
mavlink:
  enabled: true
  connection:
    type: "serial"
    port: "COM3"
    baudrate: 921600
  system_id: 255
  component_id: 190
```

将 `COM3` 和波特率替换为设备管理器及飞控参数中的实际值。

#### 飞控向 Windows 发送 UDP

```yaml
mavlink:
  enabled: true
  connection:
    type: "udp"
    host: "0.0.0.0"
    port: 14550
  system_id: 255
  component_id: 190
```

飞控必须把 MAVLink 目标 IP 设置为 Windows 有线网卡 IP，目标端口与 YAML 一致。

#### 网关主动连接飞控 TCP

```yaml
mavlink:
  enabled: true
  connection:
    type: "tcp"
    host: "192.168.1.20"
    port: 5760
  system_id: 255
  component_id: 190
```

飞控或 MAVLink 转发器必须先监听该 TCP 端口。

`mavlink.system_id` 和 `mavlink.component_id` 是网关发包使用的源 ID，不是飞控目标 ID。当前 `FlightControl`、`ModeControl` 和 `WaypointManager` 仍默认把目标飞控写成 sysid `1`、compid `1`。真实飞控不是 `1/1` 时，必须先完成第 10.2 节的目标 ID 配置化改造。

### 5.4 机巢连接：`nest_mavlink`

`nest_mavlink` 只连接真实机巢。按照现有 v1.3 协议，机巢为 TCP Server，Windows 网关为 TCP Client：

```yaml
nest_mavlink:
  enabled: true
  connection:
    type: "tcp"
    host: "192.168.1.11"
    port: 0
  system_id: 255
  component_id: 1
```

必须执行以下修改：

1. 将 `host` 替换为厂家确认的真实机巢 IP。
2. 将 `port: 0` 替换为厂家确认的真实 TCP 端口。
3. 删除默认配置中的 `nest_mavlink.sitl` 整个节点，避免现场人员误用。
4. 确认厂家协议要求的地面站源 ID 是否为 sysid `255`、compid `1`。

端口仍为 `0` 时禁止启动真机网关。

### 5.5 网关和飞行器拓扑

```yaml
device:
  firmware_version: "1.0.0"
  storage_capacity: 64000

  topology:
    device_secret: "CHANGE_ME_GATEWAY_DEVICE_SECRET"
    nonce: "CHANGE_ME_GATEWAY_NONCE"

  sub_devices:
    - sn: "CHANGE_ME_AIRCRAFT_SN"
      domain: "0"
      type: 60
      sub_type: 0
      index: "A"
      device_secret: "CHANGE_ME_AIRCRAFT_DEVICE_SECRET"
      nonce: "CHANGE_ME_AIRCRAFT_NONCE"
      thing_version: "1.1.2"
      online: true
```

当前代码固定使用网关 `domain="3"`、`type=119`、`sub_type=0`、`thing_version="1.1.2"`。子设备只接受 `("0", 60, 0)` 或 `("0", 91, 0)`。

这些值必须与客户平台分配的型号一致。如果平台给出的值不同，需要修改 `src/cloud_api/dock3_device.py`，不能填写虚假型号绕过校验。

把 `topology` 和 `sub_devices` 加到现有 `device` 节点中，并保留原来的 `limits` 和 `safety` 配置。

### 5.6 首次联调安全配置

首次只做链路和状态检查时关闭自动收尾，避免网关自动执行返航、降落、夹紧、关门或充电：

```yaml
mission:
  completion:
    enabled: false
    auto_return_home: true
    auto_land: true
    nest_recovery: true
    retry_count: 3
    retry_interval_seconds: 2.0
```

只有飞控和机巢动作全部完成真机验收后，才能把 `enabled` 改为 `true`。

### 5.7 Windows 路径和其他设备

```yaml
media:
  storage_path: "C:/Project/edge_gateway/data/media"

logging:
  dir: "C:/Project/edge_gateway/logs"

request_trace:
  enabled: true
  dir: "C:/Project/edge_gateway/logs/request_trace"

payload:
  control_path: "direct_gateway"
  flight_controller_fallback_enabled: false
  devices:
    gimbal:
      channels:
        control:
          host: "吊舱 IP"
          port: 2000
          sdk_dir: "C:/Path/To/ViewLink-SDK/bin/win64"
        video:
          endpoint: "rtsp://吊舱视频流地址"
    mapping_camera:
      channels:
        video:
          endpoint: "相机视频流地址"
        post_landing_raw_data:
          endpoint: "相机原始数据传输地址"
    lidar:
      channels:
        post_landing_raw_data:
          endpoint: "雷达原始数据传输地址"
    spectral_camera:
      channels:
        video:
          endpoint: "光谱视频流地址"
        post_landing_raw_data:
          endpoint: "光谱原始数据传输地址"
    speaker:
      channels:
        control_audio:
          endpoint: "喊话器控制地址"

rtk:
  enabled: false
  active_source: "auto"
  min_inject_interval: 0.0
  sources:
    F9P:
      type: "tcp"
      host: "192.168.1.13"
      port: 8600
      priority: 10
    NTRIP:
      type: "ntrip"
      host: "NTRIP服务器地址"
      port: 8002
      mountpoint: "NTRIP挂载点"
      username: "NTRIP用户名"
      password: "NTRIP密码"
      priority: 5
```

不使用直播时不需要配置 `media.video_sources`，也不要启动直播任务。需要直播时，再把现场提供的真实 RTSP/HTTP 地址写入 `media.video_sources`。不使用 RTK 时保持 `rtk.enabled: false`。使用 F9P 基准站时配置 `sources.F9P` 的 TCP 地址即可；使用网络差分时配置 `sources.NTRIP` 的挂载点、用户名和密码；`active_source: "auto"` 会按 `priority` 自动选择，数值越大越优先。

## 6. Windows 真机连接和网络检查

### 6.1 连接机巢

1. 使用网线将 Windows 电脑与机巢所在网络连接。
2. 将 Windows 有线网卡设置为与机巢同一网段且未被占用的静态 IP。
3. 在管理员 PowerShell 中执行：

```powershell
$NestIp = Read-Host "请输入机巢 IP"
$NestPort = [int](Read-Host "请输入厂家提供的机巢 TCP 端口")
ping $NestIp
Test-NetConnection $NestIp -Port $NestPort
```

必须看到 `TcpTestSucceeded : True`。如果不通，先检查网卡 IP、掩码、交换机、防火墙和机巢监听端口，不要通过修改 Python 代码解决网络不通。

### 6.2 连接飞控

串口连接时，在 PowerShell 中查询端口：

```powershell
Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name
```

启动网关前关闭 Mission Planner、QGroundControl 及其他占用同一 COM 口的程序。

TCP 飞控连接可执行：

```powershell
Test-NetConnection 192.168.1.20 -Port 5760
```

UDP 飞控不能使用 `Test-NetConnection -Port` 判断数据是否到达。应确认飞控目标 IP 是 Windows 网卡 IP，并在启动网关后检查 HEARTBEAT 日志。

UDP 端口需要放行时，在管理员 PowerShell 执行：

```powershell
New-NetFirewallRule `
  -DisplayName "Edge Gateway MAVLink 14550" `
  -Direction Inbound `
  -Action Allow `
  -Protocol UDP `
  -LocalPort 14550
```

## 7. 启动网关

### 7.1 启动 MongoDB 和 Mosquitto

启动顺序为：先启动 MongoDB，再启动 MQTT Broker，最后启动 Edge Gateway。

在 Windows 管理员 CMD 中执行：

```bat
net start MongoDB
net start mosquitto
```

如果提示“服务已经启动”，表示该服务当前已经在运行，可以继续下一步。

检查 MongoDB：

```bat
mongosh "mongodb://127.0.0.1:27017"
```

能够进入 `test>` 或类似提示符，说明 MongoDB 已经正常监听。退出 mongosh：

```javascript
exit
```

检查 MQTT/Mosquitto：

第一个 CMD 窗口订阅测试 topic：

```bat
mosquitto_sub -h 127.0.0.1 -p 1883 -t test/topic
```

第二个 CMD 窗口发布测试消息：

```bat
mosquitto_pub -h 127.0.0.1 -p 1883 -t test/topic -m hello
```

第一个窗口能收到 `hello`，说明 MQTT Broker 可用。

也可以使用管理员 PowerShell 检查服务和端口：

```powershell
Start-Service MongoDB
Start-Service mosquitto
Get-Service MongoDB, mosquitto
Test-NetConnection 127.0.0.1 -Port 27017
Test-NetConnection 127.0.0.1 -Port 1883
```

MongoDB 始终需要启动。只有使用本机 Broker 时才需要启动 Mosquitto；使用远端 MQTT 时，本机 Mosquitto 可以不启动，并将 `mosquitto_sub`、`mosquitto_pub` 和 `Test-NetConnection` 中的地址端口替换为真实 Broker 地址和端口。

`mongosh` 是 MongoDB 命令行客户端，不是数据库服务本身；`mosquitto_sub` 和 `mosquitto_pub` 是 MQTT 测试客户端，不是网关程序。基础服务确认正常后，再继续启动网关。

### 7.2 检查 YAML 和 Python 环境

CMD：

```bat
cd /d C:\Project\edge_gateway
call .venv\Scripts\activate.bat
python --version
python -m pip check
python -c "import yaml; yaml.safe_load(open(r'config\gateway.windows.yaml', encoding='utf-8')); print('YAML格式正确')"
findstr /N /C:"CHANGE_ME" config\gateway.windows.yaml
findstr /N /C:"port: 0" config\gateway.windows.yaml
```

正确结果应满足：

- Python 为 3.11.x。
- `pip check` 输出 `No broken requirements found`。
- YAML 输出 `YAML格式正确`。
- `findstr` 没有输出；有输出表示仍有参数未填写。
- `nest_mavlink.connection.port` 已经不是 `0`。

### 7.3 启动命令

使用 CMD 时执行：

```bat
cd /d C:\Project\edge_gateway
call .venv\Scripts\activate.bat
python src\main.py -c config\gateway.windows.yaml
```

使用 PowerShell 时执行：

```powershell
Set-Location C:\Project\edge_gateway
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python .\src\main.py -c .\config\gateway.windows.yaml
```

启动时必须带 `-c config\gateway.windows.yaml`。不带 `-c` 会读取仓库默认 `config\gateway.yaml`，从而使用错误的 SN、IP 和端口。

### 7.4 启动成功的判断

基础服务成功：

```text
MongoDB数据库初始化成功: edge_gateway
MQTT connected to broker
Edge Gateway started successfully
Entering async main loop
```

飞控直连成功：

```text
[Connection] 已连接到系统 <飞控sysid>
MAVLink dispatcher started
```

机巢 TCP 成功：

```text
[Connection] TCP连接已建立: <机巢IP>:<机巢端口>
Nest MAVLink dispatcher started
```

TCP 已连接只代表 socket 建立成功。还必须在日志中持续收到机巢 `12004` 状态，并在发送动作后收到 `12005` 回复。

PowerShell 检查机巢原始帧：

```powershell
Get-ChildItem .\logs\mavlink_raw -Recurse -File |
  Select-String "id=12004|DRONE_NEST_RESPONSE"
```

动作测试后检查：

```powershell
Get-ChildItem .\logs\mavlink_raw -Recurse -File |
  Select-String "id=12005|DRONE_NEST_ORDER_RESPONSE"
```

### 7.5 停止网关

1. 停止平台下发新任务。
2. 确认飞行器已落地并上锁。
3. 确认机巢舱盖、夹紧和充电机构没有运动。
4. 在网关窗口按 `Ctrl+C`。

不要在起飞、返航、降落、舱盖或夹紧动作进行中直接关闭窗口。

## 8. MQTT 平台连接

客户平台和 Edge Gateway 必须连接同一个 MQTT Broker，并使用与 `gateway.sn` 完全一致的 topic：

| 方向              | Topic                                                   |
| ----------------- | ------------------------------------------------------- |
| 平台下发服务      | `thing/product/{gateway_sn}/services`                   |
| 网关服务回包      | `thing/product/{gateway_sn}/services_reply`             |
| 平台设置属性      | `thing/product/{gateway_sn}/property/set`               |
| 网关属性回包      | `thing/product/{gateway_sn}/property/set_reply`         |
| DRC               | `thing/product/{gateway_sn}/drc/down`、`drc/up`         |
| OSD               | `thing/product/{device_sn}/osd`                         |
| State/Event       | `thing/product/{gateway_sn}/state`、`events`            |
| 网关请求/平台回复 | `thing/product/{gateway_sn}/requests`、`requests_reply` |
| 拓扑上报/平台回复 | `sys/product/{gateway_sn}/status`、`status_reply`       |

网关启动后会请求绑定状态并上报拓扑。平台必须正确回复 `requests_reply` 和 `status_reply`。只看到 MQTT 已连接，不代表绑定和业务闭环已经完成。

如果其他电脑需要连接本机 Mosquitto，不能继续使用只监听 `127.0.0.1` 的配置。需要为局域网配置 listener、账号密码和 Windows 防火墙，但不要把匿名 1883 或 MongoDB 27017 暴露到公网。

## 9. 真机测试脚本

所有测试脚本都必须显式指定同一个 YAML：

```text
--config config\gateway.windows.yaml
```

### 9.1 先执行只读检查

MongoDB 运行时审计：

```bat
python tools\mongodb_runtime_audit.py --config config\gateway.windows.yaml --write-check
```

该命令只写入并清理带 `__mongo_audit_` 前缀的临时数据库记录，不控制硬件。

查询网关状态：

```bat
python tools\mqtt_smoke_test.py --config config\gateway.windows.yaml --method gateway_state_query --expect-result 0 --timeout 10
```

PowerShell 查询机巢和飞控属性：

```powershell
python .\tools\mqtt_smoke_test.py `
  --config .\config\gateway.windows.yaml `
  --method property_get `
  --payload '{"properties":["cover_state","drone_charge_state","environment_temperature","wind_speed","rainfall","emergency_stop_state","latitude","longitude","height"]}' `
  --expect-result 0 `
  --timeout 10
```

只监听 OSD，不修改拓扑或属性：

```bat
python tools\mqtt_dock3_osd_state_test.py --config config\gateway.windows.yaml --drone-sn CHANGE_ME_AIRCRAFT_SN --skip-topo --skip-state --require-live-drone --dock-samples 1 --drone-samples 1 --timeout 30
```

将 `CHANGE_ME_AIRCRAFT_SN` 替换为真实飞行器 SN。

生产 Broker 启用账号或 TLS 后，现有共用测试客户端需要先完成第 10.4 节改造，否则测试脚本无法连接。

### 9.2 动作脚本必须先改造

所有会控制真机的测试脚本都要增加：

- `--real-device`：明确进入真机模式。
- `--confirm-device-sn`：输入的 SN 必须与 YAML 一致。
- `--confirm-dangerous`：起飞、返航、降落、DRC、舱盖、夹紧、充电和上下电二次确认。
- `--read-only`：禁止发送动作和属性修改。
- `--only`、`--skip`：每次只执行一个测试项。
- `--dry-run`：只打印 topic 和 payload，不发布。
- 动作前状态门禁、超时、急停路径和动作后真实状态回读。

以下脚本不能直接在真机上整套运行：

| 脚本                                                  | 原因                                                 | 修改要求                                          |
| ----------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------- |
| `tools/e2e_full_chain_test.py`                        | 聚合 CMD、DRC、航线、媒体、HMS 和 OTA                | 拆成可选择的独立安全步骤                          |
| `tools/mqtt_dock3_cmd_test.py`                        | 会开关舱盖、充电、夹紧、上下电，并包含 format/reboot | 删除危险批量项，增加单动作确认和 12004/12005 回读 |
| `tools/mqtt_dock3_drc_test.py`                        | 会发送杆量、速度和指点飞行                           | 增加围栏、坐标、高度、速度及急停限制              |
| `tools/mqtt_dock3_wayline_test.py`                    | 可切换 AUTO 并执行航线                               | 只允许加载审核后的真实航线                        |
| `tools/mqtt_dock3_real_wayline_breakpoint_test.py`    | 会执行、暂停、取消和恢复航线                         | 基础飞行验收后再在封闭场地使用                    |
| `tools/mqtt_dock3_payload_test.py`                    | 会转动云台、拍照和录像                               | 限制角度和速度，验证真实硬件 ACK                  |
| `tools/mqtt_dock3_firmware_config_test.py`            | 包含 OTA 和配置回滚                                  | Windows 真机禁止 OTA                              |
| `tools/mqtt_dock3_real_device_management_e2e_test.py` | 可离线、重连和解绑                                   | 首次禁止 unbind 和 offline/reconnect              |
| `tools/mqtt_dock3_real_property_e2e_test.py`          | 会修改可写属性                                       | 增加 `--read-only` 后再使用                       |

在第 10.1 节代码修复前，MQTT CMD 返回 `result=0` 只表示当前代码认为命令已经发送，不证明机巢机构已完成动作。必须同时检查 `12005` 和后续 `12004`。

## 10. 项目代码需要修改的位置

| 改造内容                | 主要文件                                                                                                                                  |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 机巢 ACK 和最终状态     | `src/device/manager.py`、`src/modules/drone_nest_module.py`                                                                               |
| 飞控目标 ID             | `src/utils/config.py`、`src/main.py`、`src/modules/flight_control.py`、`src/modules/mode_control.py`、`src/modules/waypoint_manager.py`   |
| 机巢协议                | `src/mavlink_definitions/drone_nest.xml`、`src/generated_dialect/`、`src/device/nest_property_mapper.py`、`src/device/nest_hms_mapper.py` |
| MQTT TLS 和测试工具认证 | `src/mqtt/client.py`、`tools/tool_config.py`、`tools/mqtt_test_client.py`                                                                 |
| ViewLink 自动连接       | `src/modules/gimbal_control.py`、`src/main.py`、`src/payload/camera.py`、`src/payload/gimbal.py`                                          |
| Windows OTA             | `src/device/manager.py`                                                                                                                   |

### 10.1 P0：机巢命令等待真实 ACK 和最终状态

主要文件：

- `src/device/manager.py`
- `src/modules/drone_nest_module.py`

当前 `_send_nest_command()` 没有正确检查 `send_gcs_command()` 的返回值，发送后会较早回复 `result=0`。必须修改为：

1. 检查命令是否真实发送成功。
2. 为 12003 建立 pending command，保存 method、tid、命令字和超时。
3. 收到 12005 后关联原命令，并处理拒绝原因。
4. 等待后续 12004 确认舱盖、夹紧、充电等物理状态已经到位。
5. 超时或拒绝必须返回失败，不能使用本地目标值表示完成。

### 10.2 P0：飞控目标 sysid/compid 配置化

主要文件：

- `src/utils/config.py`
- `src/main.py`
- `src/modules/flight_control.py`
- `src/modules/mode_control.py`
- `src/modules/waypoint_manager.py`

需要为 `mavlink` 增加 `target_system_id` 和 `target_component_id`，由 `Config.get_mavlink_config()` 读取，并在 `src/main.py` 创建 `FlightControl`、`ModeControl` 和 `WaypointManager` 时传入。

HEARTBEAT、COMMAND_ACK、MISSION_ACK 和 MISSION_CURRENT 必须按目标 ID 过滤。如果现场飞控固定为 `1/1`，可先使用当前默认值；否则完成本项前禁止执行飞行动作。

### 10.3 P0：核对真实机巢协议

主要文件：

- `src/mavlink_definitions/drone_nest.xml`
- `src/generated_dialect/drone_nest.py`
- `src/generated_dialect/gcs_order_protocol.py`
- `src/modules/drone_nest_module.py`
- `src/device/nest_property_mapper.py`
- `src/device/nest_hms_mapper.py`

必须使用厂家最终 XML/头文件和真实十六进制报文核对：

- 12003、12004、12005 的字段类型、顺序、长度和 CRC extra。
- 温度字段的有符号类型和缩放。
- `boolstate1`、`boolstate2` 每一位的极性。
- 大灯、舱盖、夹紧、充电的状态值。
- 12005 的收到状态、拒绝原因和命令编码。

XML 修改后执行：

```bat
python src\mavlink_definitions\generate_dialect.py
```

不要使用 `force_gen.py`，该文件包含开发电脑的硬编码 Conda 路径。

### 10.4 P0：MQTT 鉴权、TLS 和测试客户端

主要文件：

- `src/utils/config.py`
- `src/mqtt/client.py`
- `tools/tool_config.py`
- `tools/mqtt_test_client.py`

主网关需要读取 CA、客户端证书、TLS 版本和 hostname 校验配置。测试工具需要读取 username/password/TLS，并在连接前调用 Paho 的 `username_pw_set()` 和 `tls_set()`。

密码优先通过受保护配置文件或环境变量传递，不要长期放在命令行历史中。

### 10.5 P1：ViewLink 真机配置化和自动连接

主要文件：

- `src/modules/gimbal_control.py`
- `src/utils/config.py`
- `src/main.py`
- `src/payload/camera.py`
- `src/payload/gimbal.py`

当前 SDK 目录默认硬编码为 `C:/workspace/ViewLink-SDK-2025/bin/win64`，负载 IP 默认 `192.168.1.119`，端口默认 `2000`。主程序只创建对象，不会自动 initialize/connect。

需要把 SDK 目录、IP、端口、连接类型和自动连接开关写入 YAML，并在启动时读取。连接失败必须上报不可用，不能把本地状态变化当作硬件成功。

### 10.6 P1：Windows 真机 OTA

主要文件：

- `src/device/manager.py`

当前真实 OTA 调用 Linux `/usr/local/bin/flash_firmware.sh`。Windows 找不到脚本时仍可能在等待后上报完成，这不代表设备真实刷写成功。生产环境必须禁用 OTA，直到实现 Windows/设备专用刷写 adapter、签名校验、断电保护、回滚和版本回读。

### 10.7 P1：飞控固件和其他负载

- PX4 或厂家自定义飞控：修改 `src/mavlink/flight_modes.py`、`src/modules/flight_control.py` 和航线模块。
- 探照灯、喊话器、红外、PSDK/ESDK：检查 `src/payload/third_party.py`，未接真实适配器的入口会返回不支持，不再固定成功回包。
- 正式媒体和直播：检查 `src/media/manager.py`，补齐真实 OSS 凭据、断点续传和实际推流协议。

## 11. 当前已经实现的功能

下表区分“代码已有入口”和“真机已经完成验证”。

| 功能域            | 当前代码能力                                                               | 当前真机边界                                |
| ----------------- | -------------------------------------------------------------------------- | ------------------------------------------- |
| Windows 运行      | Python 3.11、MongoDB、Mosquitto、YAML 启动链路                             | 电脑仍需执行完整真机联调                    |
| MQTT              | services、property、requests、status、events、OSD、state、DRC topic 和回包 | 用户名密码已支持；生产 TLS 未完成           |
| MongoDB           | 任务、航点、遥测、操作、告警、媒体、绑定和保留策略                         | 需在 Windows 实库执行审计和压测             |
| 设备上线和拓扑    | 上线、心跳、绑定、解绑、update_topo、多子设备缓存                          | 真实平台型号、密钥和组织字段需确认          |
| Dock 3 Properties | 49 项官方属性 schema、读写校验、OSD/state 分流                             | 17 项物理语义需要真机确认                   |
| 飞控遥测          | HEARTBEAT、GPS、姿态、电池、高度、速度和模式解析                           | 真机字段、单位、目标 ID 和丢包行为待验收    |
| 基础飞控          | 模式切换、预解锁、解锁、起飞、返航、降落及 ACK 检查                        | 当前目标 ID 默认 `1/1`；PX4 未适配          |
| 航线              | prepare、execute、progress、pause、resume、undo、stop 和断点保存           | 真机任务终态和恢复流程未完成验收            |
| 机巢协议          | 12003 命令、12004 状态、12005 回复及状态映射                               | 真实端口、帧、ACK、状态位和故障码待验证     |
| 机巢 CMD          | 舱盖、充电、夹紧、大灯、急停、初始化、故障恢复和飞机上下电入口             | 部分回包只代表已发送，需完成 10.1 节        |
| 一键启航和收尾    | 起飞请求、飞控执行、返航、降落、夹紧、关门和充电编排                       | 真实耗时状态机和失败恢复未完成              |
| DRC               | enter/exit、心跳、杆量、速度、指点飞行和超时管理                           | 真机安全边界未验收                          |
| 相机和云台        | MAVLink 相机命令、云台控制、ViewLink DLL、拍照和录像入口                   | SDK 自动连接和硬件 ACK 未完成               |
| 第三方负载        | 喊话器适配器、负载状态查询和 PSDK/ESDK method 入口                          | 未接真实适配器的入口返回不支持              |
| 媒体和直播        | 本地扫描、删除、上传队列、HTTP PUT、FFmpeg 启停                            | 正式 OSS、WebRTC/GB28181 等未完成全协议验收 |
| HMS 和日志        | 告警、查询、清除、分类日志、上传进度和原始帧日志                           | 真实故障码、阈值和对象存储待确认            |
| 配置更新          | config update/get/rollback 和本地持久化                                    | 生产字段白名单和权限需补齐                  |
| OTA               | 下载、MD5 和状态机入口                                                     | Windows 没有真实刷写 adapter，生产必须禁用  |
| 全局状态机        | 设备、任务、DRC、OTA 和急停命令准入                                        | 真机状态源及时性待验收                      |
| RTK               | NTRIP 客户端和 RTCM 注入入口                                               | 真实账号、飞控兼容性和定位结果未验证        |

当前项目已经具备主要协议和业务框架，但不能仅根据代码入口判断真机可用。机巢 ACK、目标飞控 ID、真实协议、生产 TLS、ViewLink 自动连接和 Windows OTA 是当前明确缺口。

## 12. 真机联调顺序

1. 先完成 MongoDB、MQTT、YAML 和网络检查。
2. 启动网关，确认飞控 HEARTBEAT 和机巢 12004 持续到达。
3. 平台确认 requests/status 回复、绑定、拓扑、OSD、state 和 events。
4. 先执行只读脚本，不控制任何机构。
5. 完成第 10.1 节后，先测试大灯，再单独测试舱盖、夹紧和充电。
6. 飞控地面测试应拆桨或使用厂家认可的安全台架。
7. 在封闭场地依次验证起飞、返航、降落、短航线、暂停恢复、DRC 和一键启航。
8. 全程保留日志、MAVLink 原始帧、request trace、MQTT 消息和 MongoDB 记录。

## 13. 常见故障

| 现象                           | 优先检查                                                             |
| ------------------------------ | -------------------------------------------------------------------- |
| YAML 报 `expected <block end>` | 引号是否成对、缩进是否一致、是否重复粘贴同名节点                     |
| MongoDB 初始化失败             | 服务、账号、密码、`auth_source` 和 27017                             |
| MQTT 离线                      | `nanomq.host/port`、账号密码、1883、是否错误使用 TLS 端口            |
| 网关启动但平台没有设备         | 真实 SN、requests_reply、status_reply、拓扑密钥和平台绑定            |
| 飞控心跳超时                   | COM 占用、波特率、UDP 目标 IP/端口、TCP listener 和飞控 MAVLink 输出 |
| 飞控有心跳但动作无 ACK         | 飞控目标 sysid/compid 是否为当前默认 `1/1`                           |
| 机巢 TCP 已连接但无状态        | 真实端口、12004 周期、MAVLink 版本、XML/CRC 和厂家附加协议头         |
| CMD 回包成功但机构没动         | 检查 12005 拒绝原因和后续 12004，当前回包可能只代表已发送            |
| 机巢温度异常                   | 检查字段有符号类型、缩放和真实 XML                                   |
| ViewLink 不可用                | SDK 目录、DLL 依赖、设备 IP/端口及 initialize/connect                |
| 测试脚本连不上生产 Broker      | 共用测试客户端尚未配置 username/password/TLS                         |
| 直播失败                       | FFmpeg PATH、RTSP/HTTP 输入和真实推流协议                            |

## 14. 真机上线前必须完成

1. `config\gateway.windows.yaml` 中没有 `CHANGE_ME`，机巢端口不为 `0`。
2. 网关分别连接飞控和机巢，两条链路均持续收到真实数据。
3. 飞控目标 sysid/compid 已确认；不是 `1/1` 时已完成代码配置化。
4. 机巢协议字段、CRC、状态位和 12005 拒绝原因已用真实帧确认。
5. 机巢命令等待 12005，并用 12004 确认最终物理状态。
6. 生产 MQTT 鉴权/TLS 已完成，匿名端口不暴露到公网。
7. 平台绑定、拓扑、OSD/state/events 和命令回包闭环已通过。
8. 所有动作脚本已增加真机确认、安全门禁和单项执行。
9. ViewLink、媒体、RTK 等现场启用能力已分别完成硬件验证。
10. 未实现的 OTA 和第三方负载功能已在生产环境禁用。
11. 急停、断网、断电、超时、重复命令和恢复流程已经验收。
