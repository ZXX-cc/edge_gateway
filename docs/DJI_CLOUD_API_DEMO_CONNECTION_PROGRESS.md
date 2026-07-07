# DJI Cloud API Demo 接入进度记录

更新时间：2026-07-07

本文记录 Edge Gateway 接入 DJI Cloud API Demo Docker 环境的当前状态、已验证链路、关键命令和后续事项。该记录针对 Ubuntu 服务器内的联调环境，不代表真实 DJI Dock 3 设备接入完成。

## 当前结论

Edge Gateway 已成功接入 DJI Cloud API Demo：

```text
Edge Gateway
  -> Mosquitto MQTT Broker 127.0.0.1:1883
  -> DJI Cloud API Demo 后端 127.0.0.1:6789
  -> MySQL / Redis 在线缓存
  -> DJI Cloud API Demo Web
```

Demo Web 页面已显示：

| 页面 | 设备 | SN | 模型 | 状态 | Workspace |
|---|---|---|---|---|---|
| Dock | 机场/网关 | `EDGEGW001` | `DJI Dock2` | Online | Test Group One |
| Dock 子设备 | 飞行器 | `DRONESIM001` | `M3D` | Online | Test Group One |
| Aircraft | 飞行器 | `DRONESIM001` | `M3D` | Online | Test Group One |

当前能证明的已跑通范围：

- Edge Gateway 可以连接宿主机 Mosquitto。
- 网关持续发布 `thing/product/{sn}/osd` 和 `thing/product/{sn}/events`。
- Demo 后端可以接收 `sys/product/{sn}/status` 的 `update_topo`。
- Demo MySQL `manage_device` 已写入并绑定 `EDGEGW001` 和 `DRONESIM001`。
- Demo Web 已能显示 Dock 和 Aircraft 在线。

## 当前环境

Docker 端口状态：

| 服务 | 容器/进程 | 端口 | 状态 |
|---|---|---|---|
| Demo Web/Nginx | `cloud_api_sample-nginx-1` | `8082 -> 8080` | 已运行 |
| Demo 后端 | `cloud_api_sample-cloud_api_sample-1` | `6789 -> 6789` | 已运行 |
| Demo MySQL | `cloud_api_sample-mysql-1` | `3306 -> 3306` | 已运行 |
| MQTT Broker | 宿主机 `mosquitto` | `1883` | 已运行 |

说明：

- 当前 `1883` 由宿主机 Mosquitto 提供，不是 Docker 内 EMQX。
- 当前未开放 `8083`，所以 DRC WebSocket/MQTT over WebSocket 通道尚未验证。
- Demo README 已声明 DJI Cloud API Demo 于 2025-04-10 停止维护，仅作为联调参考，不应暴露公网或用于生产。

## 关键配置

DJI Demo 后端的 topic 匹配规则对 SN 有约束，`EDGE-GW-001` 这种带横杠的 SN 会导致 Demo 后端无法正确路由。联调时改用纯字母数字：

```yaml
gateway:
  sn: "EDGEGW001"

nanomq:
  enabled: true
  host: "127.0.0.1"
  port: 1883
  username: ""
  password: ""
  topic_prefix: "thing/product"
```

当前为了兼容 DJI Demo 设备字典，拓扑使用 Demo 已支持的枚举：

| 设备 | domain | type | sub_type | Demo 显示 |
|---|---:|---:|---:|---|
| 网关/机场 | `3` | `2` | `0` | DJI Dock2 |
| 飞行器 | `0` | `91` | `0` | M3D |

风险：这只是为了先跑通 DJI Demo。真实 Dock 3 接入时，需要按官方 Dock 3 产品枚举和 Demo 后端字典能力重新校验。

## 已执行流程

### 1. 确认 Demo 和 MQTT

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}'
ss -tlnp | egrep ':1883|:8083|:6789|:18083'
docker logs --tail=200 cloud_api_sample-cloud_api_sample-1 | egrep -i 'mqtt|connect|error|fail|refused'
systemctl status mosquitto --no-pager || true
```

确认结果：

- `6789` 由 Demo 后端容器暴露。
- `1883` 由宿主机 Mosquitto 监听。
- Demo 后端日志中 `mqttInbound` 启动，无明显 MQTT 连接失败。

### 2. 清理重复网关进程

出现过同一个 `tid` 收到两个 `services_reply` 的情况，其中一个是旧格式 `Unknown method: update_topo`。这通常表示有旧网关进程或旧模拟器仍在同一个 broker 上监听。

```bash
ps -ef | grep '[p]ython.*src/main.py'
pkill -f 'python.*src/main.py'
```

### 3. 启动网关

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

启动阶段曾遇到并修复：

- `web/mavlink/viewlink/mapping.py` 中 `"PHOTO": 1,` 后存在非法字符 `§`，导致 `SyntaxError`。
- 删除非法字符后，`python -m py_compile web/mavlink/viewlink/mapping.py src/main.py` 通过。

当前网关启动后可观察到：

- MQTT 连接成功。
- OSD/heartbeat 持续发布。
- MAVLink/SITL 未连接时，OSD 值仍为 0。

### 4. MQTT 监听

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGEGW001/#' -v
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'sys/product/EDGEGW001/#' -v
```

已观察到：

- `thing/product/EDGEGW001/osd`
- `thing/product/EDGEGW001/events`
- `thing/product/EDGEGW001/services`
- `thing/product/EDGEGW001/services_reply`
- `thing/product/EDGEGW001/state`
- `sys/product/EDGEGW001/status`，由 `update_topo` 触发

### 5. 触发拓扑上报

通过 `services` 下发 `update_topo`，网关处理后会重新通过 `sys/product/{sn}/status` 上报给 Demo 后端：

```bash
TID=$(uuidgen)

mosquitto_pub -h 127.0.0.1 -p 1883 \
  -t 'thing/product/EDGEGW001/services' \
  -m "{\"bid\":\"$TID\",\"tid\":\"$TID\",\"timestamp\":$(date +%s%3N),\"gateway\":\"EDGEGW001\",\"method\":\"update_topo\",\"need_reply\":1,\"data\":{\"domain\":\"3\",\"type\":2,\"sub_type\":0,\"device_secret\":\"gw-secret\",\"nonce\":\"gw-nonce\",\"thing_version\":\"1.1.2\",\"sub_devices\":[{\"sn\":\"DRONESIM001\",\"domain\":\"0\",\"type\":91,\"sub_type\":0,\"index\":\"A\",\"device_secret\":\"drone-secret\",\"nonce\":\"drone-nonce\",\"thing_version\":\"1.1.2\"}]}}"
```

首次上报后，Demo MySQL 中出现设备：

```bash
docker exec -i cloud_api_sample-mysql-1 mysql -uroot -proot cloud_sample -e "
SELECT device_sn, workspace_id, bound_status, child_sn, domain, device_type, sub_type
FROM manage_device
WHERE device_sn IN ('EDGEGW001','DRONESIM001');
"
```

首次结果中 `workspace_id` 为空，`bound_status=0`，说明设备已发现但未绑定。

### 6. 绑定到默认 Workspace

Demo 默认 workspace：

```text
e3dea0f5-37f2-4d79-ae58-490af3228069
```

绑定命令：

```bash
docker exec -i cloud_api_sample-mysql-1 mysql -uroot -proot cloud_sample -e "
SET @ws='e3dea0f5-37f2-4d79-ae58-490af3228069';
SET @now=CAST(UNIX_TIMESTAMP(NOW(3))*1000 AS UNSIGNED);

UPDATE manage_device
SET workspace_id=@ws,
    bound_status=1,
    bound_time=@now
WHERE device_sn IN ('EDGEGW001','DRONESIM001');

SELECT device_sn, workspace_id, bound_status, child_sn, domain, device_type, sub_type
FROM manage_device
WHERE device_sn IN ('EDGEGW001','DRONESIM001');
"
```

绑定后结果：

| device_sn | workspace_id | bound_status | child_sn | domain | device_type | sub_type |
|---|---|---:|---|---:|---:|---:|
| `EDGEGW001` | `e3dea0f5-37f2-4d79-ae58-490af3228069` | 1 | `DRONESIM001` | 3 | 2 | 0 |
| `DRONESIM001` | `e3dea0f5-37f2-4d79-ae58-490af3228069` | 1 |  | 0 | 91 | 0 |

### 7. 刷新在线缓存

后端重启会清理在线缓存。重启 Demo 后端后，需要等待后端完成启动，再重新触发一次 `update_topo`：

```bash
docker restart cloud_api_sample-cloud_api_sample-1
docker logs -f cloud_api_sample-cloud_api_sample-1 | egrep -i 'started|mqttInbound|error|exception'
```

后端恢复后，重新执行第 5 步的 `update_topo` 命令。

## 当前限制和后续事项

| 项目 | 当前状态 | 后续动作 |
|---|---|---|
| Demo 页面在线显示 | 已完成 | 保持网关运行，后端重启后重发 `update_topo` |
| OSD 遥测 | MQTT 有数据，但值为 0 | 启动 SITL/MAVLink，并对齐网关 `mavlink.connection.port` |
| DJI Demo 设备枚举 | 当前用 Dock2 + M3D 兼容 | 真实 Dock3 需更新/适配 Demo 字典和官方枚举 |
| DRC WebSocket | 未验证，`8083` 未开放 | 后续需要配置 MQTT WS/DRC broker |
| 自动绑定 | 当前用 MySQL 手工绑定 | 后续可补 `airport_organization_*` 与 Demo 后端的自动绑定流程 |
| 旧回包干扰 | 曾出现旧格式 `Unknown method` | 联调前清理旧网关进程和旧模拟器 |
| 生产可用性 | 不可用于生产 | 仅内网联调；Demo 已停止维护且有安全风险 |

## 下一步建议

1. 固化一份 `config/gateway.demo.yaml`，避免默认配置和 Demo 联调配置混在一起。
2. 增加一个 `tools/mqtt_dji_demo_topology_seed.py`，自动完成 `update_topo` 和默认 workspace 绑定校验。
3. 启动 ArduPilot SITL，并对齐 MAVLink 端口，让 Demo Web 显示真实经纬度、高度、电量等 OSD。
4. 补充 Demo 后端 `8083`/DRC 配置，验证 `drc_mode_enter` 和 `drc/down` 控制链路。
5. 将 Dock2/M3D 兼容模式与真实 Dock3 模式分离，避免误把 Demo 兼容枚举当作 Dock3 正式结论。
