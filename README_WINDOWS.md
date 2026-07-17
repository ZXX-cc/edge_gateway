# Edge Gateway Windows 客户说明

本文档是 Windows 客户电脑的项目入口。研发团队原有的 Ubuntu 联调说明仍保留在 `README.md`，两套环境不要混用启动命令。

## 文档入口

| 文档 | 用途 |
|---|---|
| `docs/DEPLOYMENT_GUIDE.md` | Windows 安装、Python 环境、MongoDB、Mosquitto、真实设备、Windows 服务、升级和排障 |
| `docs/USER_GUIDE.md` | Windows 日常启停、模拟链路、Web/Demo、MQTT 操作、日志和安全停机 |
| `docs/INTERFACE_DESIGN_SPEC.md` | MQTT Topic、method、字段、回包和 DJI 错误码 |
| `docs/业务流程.md` | 设备、任务、飞控、机巢及异常业务流程 |

首次部署必须先完整执行 `docs/DEPLOYMENT_GUIDE.md`，不要直接跳到使用步骤。

## 推荐环境

- Windows 10/11 64 位。
- Python 3.11 64 位。
- MongoDB Windows 服务。
- Mosquitto Windows 服务或受信任网络中的 EMQX。
- 项目目录建议使用 `C:\edge_gateway`，避免中文、空格和过长路径。

## 已部署环境快速启动

以管理员身份打开 PowerShell：

```powershell
Start-Service MongoDB
Start-Service mosquitto
Start-Service EdgeGateway

Get-Service MongoDB, mosquitto, EdgeGateway
```

如果网关没有注册为 Windows 服务，可前台启动：

```powershell
Set-Location C:\edge_gateway
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python .\src\main.py -c .\config\gateway.windows.yaml
```

不要同时启动 `EdgeGateway` 服务和前台网关，否则相同网关 SN 会产生重复回包。

## 可选页面

| 页面 | 默认地址 | 说明 |
|---|---|---|
| Web Console | `http://127.0.0.1:8090` | 查看 MQTT/OSD、发送 API 和 MAVLink 命令 |
| Demo Platform | `http://127.0.0.1:8095` | 按业务流程演示和验收 |

页面默认没有用户登录功能，不得直接暴露到公网。

## Windows 注意事项

- 不执行仓库中的 `scripts/*.sh` 和根目录 `start.sh`，它们是 Ubuntu/Bash 脚本。
- 默认配置中的 `/data/media`、`/dev/video0`、`/tmp` 等 Linux 路径必须替换为 Windows 路径。
- 当前 Web Console 页面内的“SITL 启动/停止”只支持 Unix；Windows 应从 WSL2 或另一台 Linux 电脑启动 SITL。
- 当前 MQTT 客户端尚未应用 `nanomq.ssl` 配置，未完成 TLS 适配前只允许本机或受信任网络使用。
- 接入真实飞控前必须确认 COM 口或 UDP 地址、防火墙、空域、急停和任务占用状态。

详细步骤和故障处理以 `docs/DEPLOYMENT_GUIDE.md`、`docs/USER_GUIDE.md` 为准。
