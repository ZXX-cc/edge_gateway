# 模拟故障注入与 HMS 验收

## 1. 验收目标

在不连接真实负载、实体机巢的条件下，使用机巢模拟器运行时控制文件完成以下证据闭环：

- 机巢心跳和飞机心跳丢失；
- 急停、舱盖异常、充电异常、机巢 busy、机巢 fault/error_code；
- MQTT 网关客户端断开/自动恢复、重复请求幂等和错误 topic 官方错误码；
- Dock3 `hms`、`events`、`osd/state`、数据库操作记录；
- 网关、MQTT、MAVLink 原始、任务、告警、机巢 PLC、飞控、相机/负载八类独立日志；
- 隔离数据库的数据保留清理和运行中记录保护。

这里的 `state` 按 Dock3 `pushMode` 验收：机巢遥测字段属于 `pushMode=0`，应进入 OSD；脚本另用 `silent_mode` 验证 `pushMode=1` 的 state 上报，并确认重复请求不会重复生成 state。

## 2. 启动机巢模拟器

机巢模拟器增加了 `--fault-state-file`。控制文件可在进程运行中原子更新，删除文件或写入 `active=false` 会恢复模拟器原状态。

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

pkill -f 'src/nest_simulator.py.*--port 14551' || true
nohup python src/nest_simulator.py \
  --host 0.0.0.0 --port 14551 \
  --drone-host 0.0.0.0 --drone-port 14552 \
  --fault-state-file /tmp/edge_gateway_nest_faults.json \
  > /tmp/nest_simulator.log 2>&1 &
```

本项不要求真实 SITL；不启动 SITL 时去掉两个 `--drone-*` 参数即可。模拟器默认状态仍保持健康，故障只作为运行时覆盖。

## 3. 一键验收

先启动本机 MQTT broker、机巢模拟器和网关，再执行：

```bash
cd ~/lxz/edge_gateway
source .venv/bin/activate

bash scripts/run_test_with_log.sh \
  --name fault-hms-full-acceptance \
  --tail-log /tmp/nest_simulator.log \
  -- \
  bash scripts/fault_hms_acceptance.sh
```

脚本会短暂使用与网关相同的 MQTT client ID，使网关发生一次被动断开，然后验证自动重连和 OSD 恢复。该操作只影响当前测试 broker；如需先跳过，可单独运行 `tools/mqtt_fault_injection_acceptance.py --skip-gateway-reconnect`。

## 4. 通过标准与证据

通过时终端最后必须出现：

```text
[通过] 全部验收完成
```

证据保存在 `logs/acceptance_evidence/<时间>/`：

| 证据 | 内容 |
| --- | --- |
| `acceptance.log` | 一键验收完整输出 |
| `00-fault-unit.log` | 故障覆盖、HMS 映射和 MQTT 幂等缓存单元证据 |
| `independent_logs/` | 八类独立日志路由证据 |
| `retention_audit.db` | 隔离 SQLite 数据保留证据，不触碰生产 MongoDB |
| `fault_injection/fault_injection_summary.json` | 每种故障的 HMS key、操作 ID、OSD/state、MQTT 结果 |
| `actual_log_tails/` | 本次实际运行的分类日志尾部和故障注入 JSONL |
| `SHA256SUMS` | 证据文件完整性校验 |

验收脚本无论成功或失败都会把控制文件恢复为 `active=false`。如果测试进程被强制杀死，可手动恢复：

```bash
printf '%s\n' '{"active":false,"scenario":"manual-recovery","faults":{}}' \
  > /tmp/edge_gateway_nest_faults.json
```

## 5. 当前边界

- 本脚本证明模拟器到网关、MQTT 和数据库的故障闭环，不替代真实机巢故障码、充电阈值和固件 HMS 文案精校。
- 舱盖异常当前使用协议故障状态 `gate_state=6`；`3/4/5` 分别保留为开启中、关闭中、暂停中。真机接入后仍需按固件实际枚举确认。
- 充电异常当前定义为“充电位为真且电压低于 10V或电流不大于 0A”，用于无硬件验收；生产阈值必须由真实机巢规范确认。
- MongoDB 操作记录使用当前 `config/gateway.yaml` 的生产配置进行只读验收；数据保留删除测试始终使用新建隔离 SQLite 文件。

## 6. 已通过证据

2026-07-13 在 Ubuntu、代码提交 `0934955` 上完成正式验收：

- 5 个故障注入专项单测通过；
- 7 个故障场景及恢复全部通过；
- 重复请求仅生成 1 条 state，并重放缓存回包；
- 错误 topic 返回 Dock3 官方错误码 `325001`；
- MQTT 被动断开后自动重连并恢复 OSD；
- MongoDB 后端 `edge_gateway` 回读 12 条已完成 `nest_status_sync` 操作记录；
- 八类独立日志和隔离 SQLite 数据保留通过。

服务器证据目录：`logs/acceptance_evidence/20260713_104438/`。
