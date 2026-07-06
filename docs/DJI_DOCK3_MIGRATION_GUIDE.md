# DJI Dock 3 迁移指南

本文档用于指导当前项目从 DJI Dock/Dock1 风格实现迁移到 DJI Dock 3 上云 API。后续协议验收以 Dock3 官方文档为唯一基准。

## 1. 迁移结论

| 项目 | 结论 |
|---|---|
| 当前代码状态 | 已有大量 DJI Cloud API 风格 method，但机场和航线部分仍偏 Dock/Dock1 |
| 目标协议 | DJI Dock 3 |
| 当前阶段 | 先跑通模拟全链路，再补齐 Dock3 字段和业务语义 |
| 实机范围 | 不接实体设备，仅用 Ubuntu 服务器内模拟器 |
| 数据存储目标 | MongoDB 服务型 NoSQL，当前仍是 SQLite |

## 2. DJI Dock 3 官方文档索引

| 类别 | Dock3 文档 |
|---|---|
| 设备属性 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/properties.html |
| 设备管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/device.html |
| 组织管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/organization.html |
| 直播功能 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/live.html |
| 媒体管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/media.html |
| 航线管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/wayline.html |
| HMS 管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/hms.html |
| 远程调试 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/cmd.html |
| 固件升级 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/firmware.html |
| 远程日志 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/log.html |
| 配置更新 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/config.html |
| 指令飞行 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/drc.html |
| 自定义飞行区 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/custom-fly-region.html |
| PSDK 功能 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/psdk.html |
| PSDK 互联互通 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/psdk-transmit-custom-data.html |
| ESDK 互联互通 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/esdk-transmit-custom-data.html |
| 远程解禁 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/flysafe.html |
| AirSense | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/airsense.html |
| 远程控制 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/remote-control.html |

## 3. DJI Dock1 对照文档索引

Dock1 文档仅用于差异对照，不能作为后续最终验收标准。

| 类别 | Dock1 文档 |
|---|---|
| 设备属性 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/properties.html |
| 设备管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/device.html |
| 组织管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/organization.html |
| 直播功能 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/live.html |
| 媒体管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/file.html |
| 航线管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/wayline.html |
| HMS 管理 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/hms.html |
| 远程调试 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/cmd.html |
| 固件升级 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/firmware.html |
| 远程日志 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/log.html |
| 配置更新 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/config.html |
| 指令飞行 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/drc.html |
| AirSense | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/airsense.html |
| 自定义飞行区 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/custom-flight-area.html |
| PSDK 功能 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/psdk.html |
| PSDK 互联互通 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/psdk-transmit-custom-data.html |
| ESDK 互联互通 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/esdk-transmit-custom-data.html |
| 远程解禁 | https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock1/flysafe.html |

## 4. 迁移工作方法

每个 Dock3 文档类别都按同一张矩阵推进：

| 字段 | 说明 |
|---|---|
| 类别 | properties/device/organization/live/media/wayline 等 |
| 文档来源 | Dock3 官方 URL |
| topic | services、services_reply、requests、requests_reply、events、osd、state、drc/down、drc/up |
| method/property/event | 官方名称 |
| 方向 | 云端到网关、网关到云端、双向 |
| 请求体 | Dock3 请求 payload |
| 响应体 | Dock3 reply payload |
| 枚举/单位 | Dock3 官方定义 |
| 当前实现 | 已路由、旧 Dock1 风格、占位、缺失 |
| 模拟来源 | SITL、机巢模拟器、上云 API 模拟器、固定模拟状态 |
| 负责人 | 人员1、人员2、人员3 |
| 验收用例 | MQTT payload、预期 reply、预期 osd/state/events |

## 5. 首批优先迁移范围

| 优先级 | 类别 | 原因 | 负责人 |
|---|---|---|---|
| P0 | 设备属性 | osd/state/properties 是云端识别 Dock3 状态的基础 | 人员1 |
| P0 | 设备管理 | 绑定、上线、基础控制是链路入口 | 人员1 |
| P0 | 组织管理 | `airport_bind_status` 等启动流程依赖该类接口 | 人员1 |
| P0 | 航线管理 | 当前已有任务模块，但注释仍引用 Dock1，需要迁移 | 人员1 + 人员2 |
| P0 | 远程调试 | 当前阶段需要用来验证模拟控制链路 | 人员1 + 人员3 |
| P0 | 指令飞行 | 与 SITL/MAVLink 联动最直接 | 人员1 + 人员2 |
| P0 | HMS 管理 | 告警和容错需要统一输出 | 人员1 |
| P1 | 媒体管理 | 当前可先模拟文件和上传状态 | 人员1 + 人员3 |
| P1 | 直播功能 | 当前可先验证请求和状态，不做真实推流 |
| P1 | 固件升级 | 先模拟升级任务和进度，不执行真实升级 |
| P1 | 远程日志 | 先模拟日志列表和上传状态 |
| P1 | 配置更新 | 先验证请求、回包和状态持久化 |

## 6. 暂缓范围

| 类别 | 暂缓原因 | 当前处理 |
|---|---|---|
| PSDK 功能 | 真实负载业务后续补 | 保留 method 占位和模拟 reply |
| PSDK 互联互通 | 透传协议需后续定义 | 保留接口和 trace |
| ESDK 互联互通 | 透传协议需后续定义 | 保留接口和 trace |
| AirSense | 当前无实体传感器 | 固定模拟状态或暂不输出 |
| 远程解禁 | 需要明确云端文件/证书流程 | 保留接口，占位验证 |
| 自定义飞行区 | 后续与任务和地图联动 | 先验证配置下发和缓存 |
| 远程控制 | Dock3 语义需单独核对 | 等 DRC 和远程调试基础跑通后处理 |

## 7. 当前代码对照入口

| Dock3 类别 | 当前可能对应文件 | 当前状态 |
|---|---|---|
| 设备属性 | `src/mavlink/telemetry.py`, `src/device/manager.py`, `src/cloud_api/telemetry_reporter.py` | 待 Dock3 字段校验 |
| 设备管理 | `src/device/manager.py`, `src/cloud_api/connection_manager.py` | 已有大量 method，需核对 Dock3 |
| 组织管理 | `src/cloud_api/connection_manager.py`, `src/device/manager.py` | 有绑定相关请求，需核对 Dock3 |
| 直播功能 | `src/media/manager.py` | 有模拟/占位能力 |
| 媒体管理 | `src/media/manager.py`, `src/payload/camera.py` | 有文件和上传状态，需核对 Dock3 |
| 航线管理 | `src/mission/manager.py`, `src/modules/waypoint_manager.py` | 当前注释引用 Dock1，需迁移 |
| HMS 管理 | `src/device/manager.py`, `src/database/alarm_db.py` | 有告警模型和 handler |
| 远程调试 | `src/device/manager.py`, `src/cloud_api/dock_manager.py` | 需核对 Dock3 method |
| 固件升级 | `src/device/manager.py` | 有 OTA 逻辑，需模拟化和异步化验收 |
| 远程日志 | `src/modules/log_manager.py`, `src/database/operation_db.py` | 有基础 handler |
| 配置更新 | `src/device/manager.py` | 有配置更新 handler |
| 指令飞行 | `src/drc/controller.py`, `src/mission/manager.py`, `src/modules/flight_control.py` | 与 SITL 联调相关 |
| 自定义飞行区 | `src/modules/flight_area_manager.py` | 有基础 handler |
| PSDK/ESDK | `src/modules/custom_data_manager.py`, `src/payload/third_party.py` | 暂缓，保留透传 |
| 远程解禁 | `src/modules/flysafe_manager.py` | 有基础 handler |
| AirSense | `src/device/manager.py` | 先模拟状态 |

## 8. Dock3 验收顺序

1. 建立 Dock3 properties 字段表。
2. 对照当前 `osd` 和 `state` 输出，标出字段缺失、枚举不一致、单位不一致。
3. 建立 Dock3 services method 表。
4. 对照 `src/main.py` 当前 223 个已注册 method，标出已路由、旧 Dock1 风格、占位、缺失。
5. 为 P0 method 写 MQTT payload 样例。
6. 用上云 API 模拟器发布请求。
7. 验证 `services_reply`、`state`、`events`、`osd`。
8. 将失败用例记录到 request trace。
9. 再补业务逻辑和 MongoDB 持久化。

## 9. 当前禁止事项

- 禁止把 Dock1 字段当作 Dock3 最终标准。
- 禁止在未更新迁移矩阵的情况下直接改 method payload。
- 禁止在 MQTT 回调中阻塞等待 MAVLink 或数据库。
- 禁止将真实硬件差异作为当前模拟阶段阻塞项。
- 禁止把 MongoDB 写成当前已完成实现。

