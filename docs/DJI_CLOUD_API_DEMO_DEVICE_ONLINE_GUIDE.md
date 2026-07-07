# DJI Cloud API Demo 设备在线操作指南

更新时间：2026-07-07

本文记录在 Ubuntu 联调服务器上，让 DJI Cloud API Demo Web 页面显示 `EDGEGW001` 机场和 `DRONESIM001` 飞行器在线的最短流程。该流程用于 Demo 联调，不代表真实 DJI Dock 3 生产接入流程。

## 结论

页面显示在线不强依赖主飞控 SITL。主飞控只提供真实飞控遥测；Demo 页面在线主要依赖：

- Demo 后端订阅 `osd/state/events/status/requests`。
- MySQL `manage_device` 中设备已绑定到 workspace。
- 网关发布标准包装格式的 `thing/product/{sn}/osd`。
- 网关同时发布机场 `EDGEGW001/osd` 和子设备 `DRONESIM001/osd`。
- 旧模拟器没有继续向同一个 MQTT broker 注入旧 SN 数据。

已验证成功的设备：

| 设备 | SN | Demo 显示 | 关键 Topic |
|---|---|---|---|
| 机场/网关 | `EDGEGW001` | DJI Dock2 | `thing/product/EDGEGW001/osd` |
| 飞行器 | `DRONESIM001` | M3D | `thing/product/DRONESIM001/osd` |

## 前置条件

服务器代码需要包含以下提交：

```bash
cd ~/lxz/edge_gateway
git log --oneline -3
```

至少应看到：

```text
9533ebc fix: 同步发布子设备 OSD
e3f908e fix: 按 DJI 格式包装 OSD 和状态上报
```

其中：

- `e3f908e` 解决 Demo 后端把裸 OSD 解析成 `data=null` 并报 `NullPointerException` 的问题。
- `9533ebc` 让网关同时发布 `DRONESIM001/osd`，否则通常只机场在线、飞行器离线。

## 1. 停掉旧模拟器

旧模拟器会向同一个 broker 发布 `EDGE-GW-001`、`DOCK3-SIM-001`、`M3D-SIM-001` 等旧数据，容易干扰 Demo 页面和日志判断。

```bash
ps -ef | egrep 'DOCK3-SIM|M3D-SIM|EDGE-GW-001|mqtt_cloud_simulator|dock3|cloud_simulator|python.*sim'
```

如果看到类似：

```text
python src/nest_simulator_mqtt.py
python tools/cloud_api_simulator.py --gateway-sn EDGE-GW-001 --daemon
```

按 PID 停掉：

```bash
kill <PID1> <PID2>
```

再次确认只剩 `grep` 进程。

## 2. 配置 Demo 后端 MQTT 订阅

Demo 后端配置文件：

```text
/home/fenghuolun/zwz/cloud_api_sample/config/application.yml
```

确认 `cloud-sdk.mqtt.inbound-topic` 包含以下 topic：

```yaml
cloud-sdk:
  mqtt:
    inbound-topic: sys/product/+/status,thing/product/+/requests,thing/product/+/osd,thing/product/+/state,thing/product/+/events
```

检查命令：

```bash
grep -n 'inbound-topic' /home/fenghuolun/zwz/cloud_api_sample/config/application.yml
```

如果修改了配置，重启后端：

```bash
docker restart cloud_api_sample-cloud_api_sample-1
docker logs --tail=120 cloud_api_sample-cloud_api_sample-1 | egrep -i 'Subscriptions|error|exception'
```

成功时日志应包含：

```text
thing/product/+/osd
thing/product/+/state
thing/product/+/events
```

## 3. 绑定设备到默认 Workspace

Demo 默认 workspace：

```text
e3dea0f5-37f2-4d79-ae58-490af3228069
```

执行绑定：

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

期望结果：

| device_sn | workspace_id | bound_status | child_sn | domain | device_type | sub_type |
|---|---|---:|---|---:|---:|---:|
| `EDGEGW001` | `e3dea0f5-37f2-4d79-ae58-490af3228069` | 1 | `DRONESIM001` | 3 | 2 | 0 |
| `DRONESIM001` | `e3dea0f5-37f2-4d79-ae58-490af3228069` | 1 |  | 0 | 91 | 0 |

## 4. 启动网关

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate
python src/main.py -c config/gateway.yaml
```

主飞控没有启动时，日志中可能出现：

```text
等待心跳: udp:127.0.0.1:14550
心跳超时
```

这个不影响 Demo 页面基础在线。只要 MQTT 连接成功并持续发布 OSD，页面仍可显示设备在线。

## 5. 触发拓扑上报

另开终端执行：

```bash
TID=$(uuidgen)
mosquitto_pub -h 127.0.0.1 -p 1883 \
  -t 'thing/product/EDGEGW001/services' \
  -m "{\"bid\":\"$TID\",\"tid\":\"$TID\",\"timestamp\":$(date +%s%3N),\"gateway\":\"EDGEGW001\",\"method\":\"update_topo\",\"need_reply\":1,\"data\":{\"domain\":\"3\",\"type\":2,\"sub_type\":0,\"device_secret\":\"gw-secret\",\"nonce\":\"gw-nonce\",\"thing_version\":\"1.1.2\",\"sub_devices\":[{\"sn\":\"DRONESIM001\",\"domain\":\"0\",\"type\":91,\"sub_type\":0,\"index\":\"A\",\"device_secret\":\"drone-secret\",\"nonce\":\"drone-nonce\",\"thing_version\":\"1.1.2\"}]}}"
```

该命令会让网关处理 `update_topo`，再通过 `sys/product/EDGEGW001/status` 上报拓扑。

## 6. 验证 MQTT 上报

确认机场 OSD：

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/EDGEGW001/osd' -v
```

确认飞行器 OSD：

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/DRONESIM001/osd' -v
```

两条都应该持续输出，并且 payload 应为标准包装格式：

```json
{
  "tid": "...",
  "bid": "...",
  "timestamp": 1783407689785,
  "gateway": "EDGEGW001",
  "data": {
    "latitude": 0.0,
    "longitude": 0.0
  }
}
```

## 7. 验证 Demo 后端接收

```bash
docker logs --tail=200 cloud_api_sample-cloud_api_sample-1 | egrep -i 'EDGEGW001/osd|DRONESIM001/osd|NullPointerException|error|exception'
```

成功时应看到：

```text
received topic: thing/product/EDGEGW001/osd
received topic: thing/product/DRONESIM001/osd
```

不应再出现：

```text
TopicOsdRequest{gateway='null', tid='null', bid='null', data=null}
NullPointerException
```

## 8. 页面确认

访问：

```text
http://192.168.2.188:8082
```

期望结果：

- Dock 卡片显示 `DJI Dock2 - M3D`。
- 机场状态为 `Idle` 或在线状态。
- 飞行器不再是 `Disconnected`。

如果页面仍未刷新，先刷新浏览器页面；如果仍失败，继续看后端日志和数据库绑定状态。

## 常见问题

### 只有机场在线，飞行器还是 Disconnected

优先检查：

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -t 'thing/product/DRONESIM001/osd' -v
```

如果没有消息：

- 确认服务器代码包含 `9533ebc`。
- 重启网关。
- 重新触发 `update_topo`。

如果有消息：

```bash
docker logs --tail=200 cloud_api_sample-cloud_api_sample-1 | egrep -i 'DRONESIM001/osd|DRONESIM001|error|exception'
```

看 Demo 后端是否解析报错。

### 后端报 data=null 或 NullPointerException

说明网关还在发裸 OSD，代码未更新到 `e3f908e` 或网关未重启。

检查：

```bash
cd ~/lxz/edge_gateway
git log --oneline -3
```

更新后重启网关。

### 后端没有收到 osd/state

检查 Demo 后端订阅：

```bash
docker logs --tail=120 cloud_api_sample-cloud_api_sample-1 | egrep -i 'Subscriptions|error|exception'
```

如果只有：

```text
sys/product/+/status
thing/product/+/requests
```

需要按第 2 步补充 `osd/state/events` 并重启 Demo 后端。

### 数据库绑定被清空

后端重建数据库或重新导入数据后，`bound_status` 可能变回 0。重新执行第 3 步绑定 SQL。

### Redis keys 查不到

Demo 后端配置使用：

```yaml
spring:
  redis:
    host: host.docker.internal
    port: 6379
```

它连接的是宿主机 `6379`，不一定是 `docker-redis-1` 容器内部 Redis。排查时优先看 Demo 后端日志和页面状态。
