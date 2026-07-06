# 真实全链路测试：MAV消息 → 遥测模块 → 上报模块 → 本地MQTT服务器
import sys
import os
import time

# 把项目根目录加入Python搜索路径（解决找不到模块的报错）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 第0步：先加载你的真实配置文件（必须最先做！） ==========
from src.utils.config import load_config

# 加载 config/gateway.yaml，和项目主程序读的是同一份配置
load_config("./config/gateway.yaml")

# 导入项目原生模块（全部是你项目里的真实代码）
from src.mqtt.client import MQTTClient
from src.modules.telemetry_module import TelemetryModule
from src.cloud_api.telemetry_reporter import TelemetryReporter
from src.core.message_dispatcher import MAVLinkMessage


# ===================== 模拟飞控MAV消息体 =====================
# 完全模拟真实pymavlink消息的格式，有get_type()方法
class FakeMavMsg:
    def __init__(self, msg_type: str, **kwargs):
        self._msg_type = msg_type
        # 动态给消息加字段，模拟真实飞控发的参数
        for k, v in kwargs.items():
            setattr(self, k, v)

    def get_type(self):
        """和真实飞控消息行为完全一致，返回消息类型字符串"""
        return self._msg_type


def create_mav_msg(msg_type: str, sysid: int = 1, compid: int = 190, **fields) -> MAVLinkMessage:
    """
    严格按照你项目里的MAVLinkMessage类构造消息
    参数顺序：msg, timestamp, sysid, compid
    compid用你配置里的190，和项目完全一致
    """
    fake_msg = FakeMavMsg(msg_type, **fields)
    return MAVLinkMessage(fake_msg, time.time(), sysid, compid)


# ===================== 主测试流程 =====================
def test_full_real_link():
    print("=" * 60)
    print("🚀 开始全链路真实测试")
    print("=" * 60)

    # ---------------- 第1步：连接本地MQTT ----------------
    print("\n[1/5] 连接本地 NanoMQ/Mosquitto 服务器...")
    mqtt_client = MQTTClient()
    connect_ok = mqtt_client.connect()

    if not connect_ok:
        print("❌ MQTT连接失败！排查方向：")
        print("   1. 检查mosquitto服务有没有启动")
        print("   2. 检查配置文件里nanomq的host和port对不对")
        return
    print("✅ MQTT连接成功")

    # ---------------- 第2步：初始化遥测模块 ----------------
    print("\n[2/5] 初始化遥测模块（real模式）...")
    telemetry = TelemetryModule(mode="real")
    print("✅ 遥测模块初始化完成")

    # ---------------- 第3步：启动上报器 ----------------
    print("\n[3/5] 启动遥测上报器，OSD每2秒定时上报...")
    drone_sn = "drone_test_001"  # 测试用无人机SN
    reporter = TelemetryReporter(
        telemetry_module=telemetry,
        mqtt_client=mqtt_client,
        device_sn=drone_sn,
        report_interval=2  # 2秒上报一次OSD，方便观察
    )
    print(f"✅ 上报器启动成功，无人机SN：{drone_sn}")
    print(f"   OSD主题：thing/product/{drone_sn}/osd")
    print(f"   State主题：thing/product/{drone_sn}/state")

    # ---------------- 第4步：模拟发消息，验证效果 ----------------
    print("\n[4/5] 模拟飞控发送MAVLink消息...")
    time.sleep(1)

    # 场景1：发心跳消息 → 连接状态从disconnected变connected → 触发State立即上报
    print("\n--- 发送心跳消息（连接状态变化）---")
    heartbeat_msg = create_mav_msg(
        "HEARTBEAT",
        base_mode=217,
        custom_mode=4
    )
    telemetry.on_message(heartbeat_msg)
    print("✅ 心跳消息已发送 → 去MQTTX看state主题，应该立刻收到一条消息")

    time.sleep(1)

    # 场景2：发位置消息 → 更新经纬度高度 → 下次OSD上报会带新数据
    print("\n--- 发送位置消息（更新经纬度高度）---")
    pos_msg = create_mav_msg(
        "GLOBAL_POSITION_INT",
        lat=399042000,  # 纬度 × 1e7 = 39.9042°
        lon=116407400,  # 经度 × 1e7 = 116.4074°
        alt=100000,  # 海拔 单位mm = 100m
        relative_alt=80000  # 相对起飞高度 单位mm = 80m
    )
    telemetry.on_message(pos_msg)
    print("✅ 位置消息已发送 → 去MQTTX看osd主题，高度应该变成80m")

    time.sleep(1)

    # 场景3：发电池消息 → 电量变化 → 触发State立即上报
    print("\n--- 发送电池消息（电量变化）---")
    bat_msg = create_mav_msg(
        "BATTERY_STATUS",
        voltages=[11100],  # 电压 单位mV = 11.1V
        current_battery=150,  # 电流 单位10mA = 1.5A
        battery_remaining=75  # 剩余电量 75%
    )
    telemetry.on_message(bat_msg)
    print("✅ 电池消息已发送 → 去MQTTX看state主题，应该立刻收到第二条消息")

    # ---------------- 第5步：等待观察定时上报 ----------------
    print("\n[5/5] 等待6秒，观察3次定时OSD上报...")
    print("   提示：去MQTTX里看osd主题，每2秒收到一条新消息")
    time.sleep(6)

    # 测试结束，清理资源
    reporter.stop()
    mqtt_client.disconnect()

    print("\n" + "=" * 60)
    print("🎉 全链路测试完成！")
    print("✅ 成功标准：")
    print("   1. MQTTX的osd主题每2秒收到一条消息，数据和发送的一致")
    print("   2. 发心跳/电池消息后，state主题立刻收到变更消息")
    print("=" * 60)


if __name__ == "__main__":
    test_full_real_link()
