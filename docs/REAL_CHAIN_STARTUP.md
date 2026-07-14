# 真实链路启动说明（EMQX MQTT）

本文面向“无 DJI 云端 MQTT 客户端、由 EMQX 接收/转发 MQTT 消息”的部署口径。

当前网关仍连接 `config/gateway.yaml` 中的 `nanomq.host/port`。这里的 `nanomq` 可以指向本机 NanoMQ、Mosquitto 或生产 EMQX；`cloud.*` 不作为当前启动链路的 MQTT 连接配置。

## 生产模式原则

生产模式不再自动生成模拟拓扑数据：

- 不允许使用 `EDGEGW001`、`DRONESIM001`、`DRONE-SIM-*` 等测试 SN。
- `update_topo` 所需的 `device_secret` 和 `nonce` 必须来自配置或启动参数。
- 子设备如果需要在启动时预置，也必须显式提供 `sn/domain/type/sub_type/index/device_secret/nonce/thing_version`。
- 如果真实链路暂时无法提供某字段，可以由启动参数提供占位的真实部署值，但必须由部署方维护，不能由代码自动拼接。

## 最小启动命令

仅上报网关自身拓扑：

```bash
python src/main.py \
  -c config/gateway.yaml \
  --runtime-mode production \
  --gateway-sn REALDOCK001 \
  --gateway-device-secret "$GATEWAY_DEVICE_SECRET" \
  --gateway-nonce "$GATEWAY_NONCE"
```

带一个真实子设备：

```bash
python src/main.py \
  -c config/gateway.yaml \
  --runtime-mode production \
  --gateway-sn REALDOCK001 \
  --gateway-device-secret "$GATEWAY_DEVICE_SECRET" \
  --gateway-nonce "$GATEWAY_NONCE" \
  --sub-device-json '{"sn":"AIRCRAFT001","domain":"0","type":60,"sub_type":0,"index":"A","device_secret":"AIRCRAFT_SECRET","nonce":"AIRCRAFT_NONCE","thing_version":"1.1.2","online":true}'
```

## 配置方式

也可以写入配置文件：

```yaml
runtime:
  mode: "production"

gateway:
  sn: "REALDOCK001"

nanomq:
  host: "10.0.0.10"
  port: 1883
  username: "edge_gateway"
  password: "********"

device:
  topology:
    device_secret: "GATEWAY_DEVICE_SECRET"
    nonce: "GATEWAY_NONCE"
  sub_devices:
    - sn: "AIRCRAFT001"
      domain: "0"
      type: 60
      sub_type: 0
      index: "A"
      device_secret: "AIRCRAFT_SECRET"
      nonce: "AIRCRAFT_NONCE"
      thing_version: "1.1.2"
      online: true
```

## 启动失败排查

生产模式启动前会执行预检。出现以下报错时按提示补字段：

- `gateway.sn looks like a test identity`：换成真实网关 SN。
- `device.topology.device_secret is required`：通过配置或 `--gateway-device-secret` 提供。
- `device.topology.nonce is required`：通过配置或 `--gateway-nonce` 提供。
- `device.sub_devices[i].device_secret/nonce is required`：子设备启动预置缺少真实拓扑元数据。

## MQTT Topic

生产模式不改变 Topic：

- 下行服务：`thing/product/{sn}/services`
- 属性设置：`thing/product/{sn}/property/set`
- 设备请求：`thing/product/{sn}/requests`
- 设备请求回包：`thing/product/{sn}/requests_reply`
- 拓扑上报：`sys/product/{sn}/status`
- 拓扑回包：`sys/product/{sn}/status_reply`

EMQX 侧只需要按上述 topic 接收/转发，不需要 DJI 云端 MQTT 客户端参数。
