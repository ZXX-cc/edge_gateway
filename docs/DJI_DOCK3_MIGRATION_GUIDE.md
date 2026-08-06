# DJI Dock 3 迁移指南

本文档用于指导当前项目从 DJI Dock/Dock1 风格实现迁移到 DJI Dock 3 上云 API。迁移目标是优先满足客户业务需求：客户需求涉及的能力必须按 Dock3 官方文档验收，客户暂未提及或当前业务未用到的 Dock3 能力可以延期迁移。

## 1. 迁移结论

| 项目 | 结论 |
|---|---|
| 当前代码状态 | 已有大量 DJI Cloud API 风格 method，但机场和航线部分仍偏 Dock/Dock1 |
| 目标协议 | DJI Dock 3 |
| 当前阶段 | Dock3 P0 链路已通过 Ubuntu 模拟验证，进入客户需求优先的分组迁移 |
| 实机范围 | 不接实体设备，仅用 Ubuntu 服务器内模拟器 |
| 数据存储目标 | MongoDB 服务型 NoSQL，当前仍是 SQLite |

客户需求驱动的迁移进度以 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 为准。本文档保留迁移方法、官方文档入口和禁止事项。

优先级规则：

| 等级 | 处理 |
|---|---|
| 客户 P0/P1 需求 | 必须优先开发和验收，云端协议按 Dock3 标准落地 |
| 客户 P2 需求 | 必须有入口、错误码、日志、adapter/mock 和限制说明，不能影响 P0/P1 |
| 支撑客户主链路的 Dock3 能力 | 即使客户文档未单独写出，也要作为链路依赖实现，例如上线、绑定、拓扑、OSD/state/reply |
| 客户暂未提及且当前业务未用到的 Dock3 能力 | 可以标为 Deferred，不进入 30 天 P0/P1 强迁移，但不能返回误导性成功 |

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

客户需求涉及的 Dock3 文档类别按同一张矩阵推进：

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
| 当前实现 | 已路由、旧 Dock1 风格、占位、缺失、Deferred |
| 模拟来源 | SITL、机巢模拟器、上云 API 模拟器、固定模拟状态 |
| 负责人 | 人员1、人员2、人员3 |
| 验收用例 | MQTT payload、预期 reply、预期 osd/state/events |

## 5. 首批优先迁移范围

详细 P0/P1/P2 功能 backlog 以 `docs/P0_P1_P2_DELIVERY_BACKLOG.md` 为准。下表只保留 Dock3 分组层面的粗粒度迁移顺序。

| 优先级 | 类别 | 原因 | 负责人 |
|---|---|---|---|
| P0 | 设备属性 | osd/state/properties 是云端识别 Dock3 状态的基础 | 人员1 |
| P0 | 设备管理 | 绑定、上线、基础控制是链路入口 | 人员1 |
| P0 | 组织管理 | `airport_bind_status` 等启动流程依赖该类接口 | 人员1 |
| P0 | 航线管理 | 当前已有任务模块，但注释仍引用 Dock1，需要迁移 | 人员1 + 人员2 |
| P0 | 远程调试 | 当前阶段需要用来验证模拟控制链路 | 人员1 + 人员3 |
| P0 | 指令飞行 | 与 SITL/MAVLink 联动最直接 | 人员1 + 人员2 |
| P0 | 机巢核心状态 | 舱盖、夹紧、充电、急停、故障、备飞状态是上云状态和控制安全的基础 | 人员2 + 人员3 |
| P1 | 一键启航主流程 | 需要串联机巢、飞控、航线和状态事件，属于业务闭环硬要求 | 人员1 + 人员2 + 人员3 |
| P1 | 媒体管理 | 当前可先模拟文件和上传状态 | 人员1 + 人员3 |
| P1 | 直播功能 | 当前可先验证请求和状态，不做真实推流 |
| P1 | HMS 管理 | 告警和容错需要统一输出，但可在 P0 基础状态稳定后补齐 | 人员1 |
| P1 | 固件升级 | 先模拟升级任务和进度，不执行真实升级 |
| P1 | 远程日志 | 先模拟日志列表和上传状态 |
| P1 | 配置更新 | 先验证请求、回包和状态持久化 |

## 6. 暂缓范围

| 类别 | 暂缓原因 | 当前处理 |
|---|---|---|
| 客户暂未提及的 Dock3 官方能力 | 当前业务闭环未使用 | 标为 Deferred，后续客户明确使用时再迁移 |
| PSDK 功能 | 真实负载业务后续补 | 保留 method 占位和模拟 reply |
| PSDK 互联互通 | 透传协议需后续定义 | 保留接口和 trace |
| ESDK 互联互通 | 透传协议需后续定义 | 保留接口和 trace |
| AirSense | 当前无实体传感器，30 天内按 P2 处理 | 固定模拟状态或暂不输出 |
| 远程解禁 | 需要明确云端文件/证书流程，30 天内按 P2 处理 | 保留接口，占位验证 |
| 自定义飞行区高级能力 | 后续与任务和地图深度联动，30 天内按 P2 处理 | 先验证基础配置下发和缓存 |
| 远程控制高级能力 | Dock3 语义需单独核对，30 天内按 P2 处理 | 等 DRC 和远程调试基础跑通后处理 |

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
| 自定义飞行区 | `src/modules/flight_area_manager.py`, `src/modules/flight_area_device_applier.py`, `src/main.py` | 已有基础 handler、requests 获取、下载缓存和 MAVLink fence 生效器，仍需按 Dock3 官方模板补齐 |
| PSDK/ESDK | `src/modules/custom_data_manager.py`, `src/payload/third_party.py` | 暂缓，保留透传 |
| 远程解禁 | `src/modules/flysafe_manager.py` | 有基础 handler |
| AirSense | `src/device/manager.py` | 先模拟状态 |

## 7.1 自定义飞行区当前差距（2026-08-06）

官方资料：

- Dock3 自定义飞行区文档：https://developer.dji.com/doc/cloud-api-tutorial/cn/api-reference/dock-to-cloud/mqtt/dock/dock3/custom-fly-region.html
- Dock 自定义飞行区功能时序图：https://developer.dji.com/doc/cloud-api-tutorial/cn/feature-set/dock-feature-set/custom-flight-area.html
- 官方自定义飞行区文件模板：https://terra-1-g.djicdn.com/fee90c2e03e04e8da67ea6f56365fc76/SDK%20%E6%96%87%E6%A1%A3/CloudAPI/custom-flight-area-file-template.json
- 甲方自定义飞行区样例：http://fsimage.guihuao.com/geofence/geofence_1df20749f4117e1120b6176eca56004f.json

当前代码已有链路：

- `src/main.py` 已创建 `FlightAreaManager` 和 `FlightAreaDeviceApplier`，并注册 `flight_areas_update`、`flight_areas_get`。
- `src/modules/flight_area_manager.py` 已支持 `flight_areas_update` 触发 `requests` 通道主动发送 `flight_areas_get`，收到 `requests_reply` 后下载文件、校验 `size/checksum`、缓存到本地并上报 `flight_areas_sync_progress`。
- `src/modules/flight_area_manager.py` 已保留 `flight_areas_get` services 兼容查询，能返回当前缓存文件列表。
- `src/modules/flight_area_device_applier.py` 已把下载后的多边形飞行区转换为 MAVLink fence mission items，并等待飞控 ACK 后再向平台报告同步结果；已支持官方/甲方样例中的顶层 `geofence_type=nfz/dfence`，`nfz` 映射禁飞区，`dfence` 映射飞行围栏；已支持 `Point + properties.subType=Circle + radius` 圆形区域，内部近似转换为 36 个围栏顶点。
- `FlightAreaManager.update_aircraft_location()` 已接入飞行器 OSD 位置，按自定义飞行区边界计算 `area_distance/is_in_area`，并只在状态变化时通过 `flight_areas_drone_location` 推送：远离不重复上报，`outside -> near` 上报靠近边界，`near/outside -> inside` 上报进入围栏，`inside -> near/outside` 上报离开围栏，`near -> outside` 上报离开边界范围。

甲方样例结论：

- 文件名 `geofence_1df20749f4117e1120b6176eca56004f.json` 中的 `1df20749f4117e1120b6176eca56004f` 与文件内容 MD5 一致，checksum 规则按 DJI API 的 `geofence_{fileMD5}.json` 执行。
- 文件 HTTP 响应 `Content-Length=358`，`Content-Type=application/json`，`Content-Md5=HfIHSfQRfhEgthduylYATw==`，下载侧仍需按平台下发的 `size/checksum` 做最终校验。
- 文件格式为 GeoJSON `FeatureCollection`，含 1 个 `Polygon` Feature，`geofence_type=nfz`，属于禁飞区。
- 已将该样例保存为 `tests/fixtures/custom_fly_region/geofence_1df20749f4117e1120b6176eca56004f.json`，并新增 `tests/test_dock3_custom_fly_region.py` 覆盖 MD5 规则和 `nfz` 解析。

删除/清空语义：

- 云端平台只在平台侧删除/清空某个自定义飞行区时，网关不会自动感知；必须再次下发 `flight_areas_update`，让网关主动 `flight_areas_get` 拉取最新文件列表。
- `flight_areas_get` 的 `requests_reply` 返回 `data.output.files: []` 时，以云端为空为准，网关清空本地缓存，并向设备侧应用空飞行区列表。
- 当前 MAVLink fence 替代链路中，空飞行区列表会进入 `apply_files([])`，即上传 0 个 fence item，用于清空飞控侧已有围栏。
- 如果只删除其中一个区域，平台需要重新生成新的 `geofence_{fileMD5}.json` 文件，并在 `files` 中返回新的文件信息；网关按新文件覆盖旧缓存。

官方时序链路：

1. 平台/地理服务打包自定义飞行区文件，并上传到对象存储。
2. 平台下发 `flight_areas_update` 到 `thing/product/{gateway_sn}/services`，通知机场/网关自定义飞行区文件已更新。
3. 机场/网关主动发送 `flight_areas_get` 到 `thing/product/{gateway_sn}/requests`，向平台获取最新文件信息。
4. 平台通过 `thing/product/{gateway_sn}/requests_reply` 返回最新自定义飞行区文件信息，至少包含下载 URL 和文件 MD5。
5. 机场/网关把文件 URL 和 MD5 传递给设备侧，设备侧检查本地文件版本。
6. 如果文件版本一致，直接通过 `flight_areas_sync_progress` 上报同步完成。
7. 如果文件版本不一致，以云端版本为准，下载最新文件并更新设备侧自定义飞行区数据，同时通过 `flight_areas_sync_progress` 上报同步中/完成/失败状态。
8. 设备侧后续在靠近边界、进入围栏、离开围栏或离开边界范围时推送飞行区距离/告警信息，机场/网关通过 `flight_areas_drone_location` 上报平台；该事件不按固定周期轮询上报。

当前代码与时序差距：

- 已满足：`flight_areas_update -> requests flight_areas_get -> requests_reply -> 下载文件 -> 校验 size/checksum -> 缓存 -> flight_areas_sync_progress` 的网关侧基础链路。
- 已满足：云端返回 `files: []` 时按清空全部自定义飞行区处理。
- 已满足：`flight_areas_drone_location` 已按状态变化触发，支持靠近边界、进入围栏、离开围栏和离开边界范围的事件推送。
- 部分满足：设备侧当前是 MAVLink fence 生效器，能把 Polygon 和 Point/Circle 转成飞控 fence，但还不是 DJI 官方时序里的飞行器/机场原生 FlySafe 数据升级链路。
- 未满足：还没有比较“飞行器端已有文件版本”和“云端文件版本”的设备侧查询能力，当前主要按云端返回文件重新下载/覆盖。
- 未满足：还没有覆盖该时序的 MQTT 专项脚本，后续脚本应按上述 8 步保存完整日志。

当前欠缺：

- 缺官方模板文件的本地留存。当前已有甲方 Polygon/nfz 样例 fixture，后续仍应新增 `docs/reference/custom-flight-area-file-template.json` 作为官方离线参考。
- 缺官方字段矩阵。至少需要覆盖文件名 `geofence_{fileMD5}.json`、`FeatureCollection`、`Feature.id`、`geofence_type=dfence/nfz`、`geometry.type=Polygon/Point`、`properties.subType=Circle`、`radius`、`properties.enable` 当前版本不支持等规则。
- 缺 `flight_areas_sync_progress.reason` 全量映射。当前只覆盖 1、3、5、8、12、13，仍需补齐官方 2、4、6、7、9、10、11 的中文含义和触发场景。
- 待精校 `flight_areas_drone_location` 与官方字段矩阵的完全一致性；当前事件数据除 `area_id/area_distance/is_in_area` 外，还包含 `area_mode/state/event/latitude/longitude` 便于平台调试状态变化来源。
- 缺 requests/reply 专项验收脚本。需要覆盖 `flight_areas_update -> requests flight_areas_get -> requests_reply files -> download -> apply -> flight_areas_sync_progress`。
- 缺设备侧文件版本比较。当前以云端 `files` 列表为准覆盖本地缓存，还没有从飞行器/机场侧查询已有自定义飞行区文件版本。
- 缺下载安全边界。需要校验 URL 过期时间、文件大小上限、文件数量上限、checksum 与文件名 MD5 的关系，避免异常文件占满本地存储或错误生效。
- 缺真实设备侧 FlySafe SDK 适配。当前代码使用 MAVLink fence 作为可验证替代路径，不能完全等同 Dock3 官方 FlySafe/自定义飞行区真实生效链路。

建议补充文件：

- `docs/reference/custom-flight-area-file-template.json`：保存官方模板，便于离线对照字段。
- `docs/DOCK3_CUSTOM_FLY_REGION_FIELD_MATRIX.md`：记录 method、topic、payload、reason、文件格式、解析规则、错误码。
- `tests/fixtures/custom_fly_region/geofence_<md5>.json`：继续补充 dfence、非法字段等样例；甲方 Polygon/nfz 样例已落盘。
- `tests/test_dock3_custom_fly_region.py`：继续补充 checksum、reason、requests/reply 和缓存替换逻辑；当前已覆盖甲方样例 MD5、`nfz` 解析、Point/Circle 解析和 `files: []` 清空语义。
- `tools/mqtt_dock3_custom_fly_region_test.py`：云端模拟器专项验收脚本，日志保存到 `logs/acceptance_evidence/<时间>_custom_fly_region/`。

## 8. Dock3 验收顺序

1. 建立客户需求涉及的 Dock3 properties 字段表。
2. 对照当前 `osd` 和 `state` 输出，标出字段缺失、枚举不一致、单位不一致。
3. 建立客户需求涉及的 Dock3 services method 表。
4. 对照 `src/main.py` 当前 243 个已注册 method，标出已路由、旧 Dock1 风格、占位、缺失、Deferred；method 分组和 P0/P1/P2 归类见 `docs/METHOD_COVERAGE_LEDGER.md`。
5. 为 P0 method 写 MQTT payload 样例。
6. 用上云 API 模拟器发布请求。
7. 验证 `services_reply`、`state`、`events`、`osd`。
8. 将失败用例记录到 request trace。
9. 再补业务逻辑和 MongoDB 持久化。

## 9. 2026-07-06 人员1首轮联调发现

人员1已在 Ubuntu 服务器完成 MQTT 基础链路测试。结论是链路可用，但 Dock3 topic/reply/payload 规则尚未完成审计。

| 编号 | 类别 | 现象 | Dock3 迁移判断 |
|---|---|---|---|
| D3-AUDIT-001 | OSD 上报 | `thing/product/EDGE-GW-001/osd` 持续输出经纬度、高度、姿态、电池、GPS | 先确认 Dock3 properties/osd 字段名、单位和枚举，再决定是否改 telemetry 输出 |
| D3-AUDIT-002 | Services | `dock_status_query` 和 `airport_bind_status` 可收到 `services_reply` | services 闭环可作为后续 Dock3 method 验收基础 |
| D3-AUDIT-003 | 状态一致性 | `dock_status_query` 返回的 `drone_status` 未同步 OSD 实时遥测 | 已修复并通过 Ubuntu 服务器复测：`online`、经纬度、高度、电量、飞行模式、GPS 已来自实时遥测快照 |
| D3-AUDIT-004 | Properties | `property_set` 下行到 `property/set` 后实际回 `services_reply`，不是 `property/set_reply` | 已修复并通过 Ubuntu 服务器复测：`property/set` 来源回 `property/set_reply`；本轮新增 properties 专项脚本，按大类验证 `property/set_reply` 和 `property_get` |
| D3-AUDIT-005 | DRC | 前一轮将 `drc_mode_enter/exit` 当作 `drc/down -> drc/up` 测试 | 官方对照后已修正并通过 Ubuntu 复测：`drc_mode_enter/exit` 走 `services -> services_reply`；误发到 `drc/down` 应返回 DJI 错误码 `325001` |
| D3-AUDIT-006 | DRC 与飞控状态 | `drc_mode_enter` 返回 `result=0`，并可通过 `property_get` 读到 `drc_state=2`；`drc_mode_exit` 后 `property_get` 返回 `drc_state=0` | MQTT 协议链路和状态同步已通，飞控是否真正切到可控模式和 `mode_code/gear` 映射交由人员2继续核对 |
| D3-AUDIT-007 | Properties | Dock3 `property/set_reply` 示例按属性名逐项返回 `{state:{result}}`，不是通用 `data.result/results` | 代码已改为 Dock3 property reply 结构，同时兼容旧 `{properties:{...}}` 请求体 |
| D3-AUDIT-008 | DRC 状态 | Dock3 文档提示 `drc_status_notify` 已废弃，更准确状态来自机场属性 `drc_state` 或 DRC 心跳 | 代码保留兼容事件，同时在事件触发时同步更新 `drc_state` 属性；`drc_state` 属于 `pushMode=0`，随 OSD/property_get 表达，Ubuntu 复测 enter=2、exit=0 |
| D3-AUDIT-009 | 日志可追踪性 | 协议迁移需要按 tid/bid 追踪 request、handler、reply、state/event | 日志已改为 `logs/YYYY-MM/edge_gateway_YYYY-MM-DD.log`，关键 MQTT payload/reply/state/event 输出 `字段名(中文名)`，便于按 Dock3 字段审计 |
| D3-AUDIT-010 | 设备拓扑 | `update_topo` 应走 `sys/product/{sn}/status -> status_reply`，不是普通 services reply | 已通过 Ubuntu 复测：绑定后发布 `update_topo tid=f0845cda-bf5b-40b2-a0f7-d2e4cbb65c6b` 到 status topic，并收到 `status_reply result=0` |
| D3-AUDIT-011 | Properties 可写边界 | Dock3 properties 当前确认的顶层 `rw` 属性为 `air_transfer_enable`、`silent_mode`、`user_experience_improvement` | 已通过 Ubuntu 专项复测：`python tools/mqtt_dock3_properties_test.py --timeout 12 --quiet-stale` 输出 `dock3 properties test ok (accepted=10, ignored_total=10)` |
| D3-AUDIT-012 | Device topology 字段边界 | Dock3 device 页 `update_topo` 上报字段包括网关 `domain/type/sub_type/device_secret/nonce/thing_version/sub_devices` 和子设备 `sn/domain/type/sub_type/index/device_secret/nonce/thing_version` | 代码已新增 `src/cloud_api/dock3_device.py` 统一构造和校验；专项验收命令为 `python tools/mqtt_dock3_device_test.py --timeout 12` |

下一步由人员1按 `docs/DOCK3_FULL_MIGRATION_PROGRESS.md` 分组推进：

1. device topology 专项复测：`python tools/mqtt_dock3_device_test.py --timeout 12`。
2. organization 的 `requests/requests_reply` 云端模拟器验收。
3. DRC 的 `drc/down/drc/up` 控制数据和 DRC 心跳。
4. device `update_topo` 拓扑字段精校：专项通过后继续核对 `domain/type/sub_type/sub_devices` 和多设备拓扑。
5. wayline `flighttask_prepare/execute/progress` 基础闭环。

## 10. 错误码迁移阶段

错误码以后以 DJI 上云 API 官方错误码为唯一基准，官方页面见 `docs/71.error-code.md` 和 https://developer.dji.com/doc/cloud-api-tutorial/cn/error-code.html。

| 阶段 | 范围 | 当前状态 |
|---|---|---|
| P0 | MQTT 回包边界归一 | 已接入 `src/utils/dji_error_codes.py`，`services_reply`、`property/set_reply`、`drc/up` 不应再暴露 `-1/-2/-3` |
| P0 | 请求格式 / topic 错误 | `drc_mode_enter` 误发到 `drc/down` 应返回 `325001` |
| P0 | handler 异常 | 对外返回 `319999`，日志保留 traceback |
| P0 | 未知 method | 对外返回 `314000` |
| P1 | 业务模块直出官方码 | 后续逐个模块把内部 `-1/-2/-3` 改成更精确的官方码 |
| P1 | request trace | 记录原始内部码、DJI 结果码、中文描述、tid、method、topic |

人员1新增或修改接口时，必须同步更新 `docs/INTERFACE_DESIGN_SPEC.md` 的错误码和 API 定义表。

## 11. 当前禁止事项

- 禁止把 Dock1 字段当作 Dock3 最终标准。
- 禁止在未更新迁移矩阵的情况下直接改 method payload。
- 禁止在 MQTT 回调中阻塞等待 MAVLink 或数据库。
- 禁止将真实硬件差异作为当前模拟阶段阻塞项。
- 禁止把 MongoDB 写成当前已完成实现。
