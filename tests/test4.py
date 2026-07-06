# 测试6：完整业务闭环端到端测试（修复版）
import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.mission.manager import MissionManager
from src.modules.telemetry_module import TelemetryModule
from src.modules.waypoint_manager import WaypointManager
from src.modules.mode_control import ModeControl
from src.modules.drone_nest_module import DroneNestModule

# ===================== 小白不用管：模拟硬件对象定义 =====================
# 模拟空的MAVLink master，骗过连接检查（上层方法已经模拟，不会真的调用里面的功能）
class FakeMAV:
    def command_long_send(self, *args, **kwargs):
        pass
    def set_mode_send(self, *args, **kwargs):
        pass
    def mission_request_list_send(self, *args, **kwargs):
        pass

class FakeMaster:
    def __init__(self):
        self.mav = FakeMAV()
        self.target_system = 1
        self.target_component = 1

class FakeConnection:
    def __init__(self):
        self.master = FakeMaster()

class FakeDispatcher:
    def __init__(self):
        self.connection = FakeConnection()

# 机巢专用模拟连接
class FakeNestMAV:
    def drone_nest_command_send(self, ordinary_order, takeoff_land_order):
        pass
class FakeNestConnection:
    def __init__(self):
        self.mav = FakeNestMAV()
class FakeNestDispatcher:
    def __init__(self):
        self.connection = FakeNestConnection()
# =====================================================================

if __name__ == "__main__":
    print("="*70)
    print("🚀 终极测试：全业务闭环端到端验证（修复版）")
    print("流程：任务下发 → 机巢准备 → 起飞 → 航点飞行 → 完成 → 返航 → 降落 → 机巢收尾")
    print("="*70)

    # ===================== 第一步：初始化所有模块并注入 =====================
    print("\n📦 第一步：初始化所有模块并注入到任务管理器")
    print("-"*70)

    # 1. 初始化各个模块
    telemetry = TelemetryModule(mode="simulated")
    wp_manager = WaypointManager()
    mode_ctrl = ModeControl()
    nest = DroneNestModule()
    mission_mgr = MissionManager()

    # 2. 给模块注入模拟连接（核心修复：解决master为空的问题）
    wp_manager.dispatcher = FakeDispatcher()  # 给航点管理器加模拟飞控连接
    nest.dispatcher = FakeNestDispatcher()    # 给机巢加模拟连接

    # 3. 注入到任务管理器
    mission_mgr.set_telemetry_module(telemetry)
    mission_mgr.set_waypoint_manager(wp_manager)
    mission_mgr.set_mode_control(mode_ctrl)
    mission_mgr.set_drone_nest(nest)
    mission_mgr._flight_control = mode_ctrl  # 用模式控制模块模拟飞控

    print("✅ 所有模块初始化并注入完成")
    print("✅ 模拟硬件连接已配置")

    # ===================== 第二步：模拟硬件返回成功 =====================
    print("\n🔧 第二步：模拟硬件返回成功（不用真实无人机也能测流程）")
    print("-"*70)

    # 航点相关模拟
    wp_manager.upload_mission = lambda timeout=30: True
    wp_manager.verify_mission = lambda expected, timeout=5: True
    wp_manager.set_mission_info = lambda name, device_id: None
    wp_manager.set_current_waypoint = lambda wp_num, reset=False, timeout=3, retries=3: True

    # 模式切换相关模拟
    mode_ctrl.set_mode = lambda mode: {"success": True, "message": f"切换到{mode}"}
    mode_ctrl.get_current_mode = lambda: {"mode_name": "GUIDED", "mode_id": 15}

    # 起飞、解锁、任务启动相关模拟
    mission_mgr._wait_for_flight_mode = lambda target, timeout=8: True
    mission_mgr._arm_motors = lambda force=False, timeout=3: True
    mission_mgr._guided_takeoff = lambda alt, timeout=15: True
    mission_mgr._send_mission_start = lambda retries=3, delay=1: True
    mission_mgr._activate_mission_after_transition = lambda target_seq, retries=2: None

    print("✅ 硬件功能模拟配置完成")

    # ===================== 第三步：模拟云端下发任务准备 =====================
    print("\n📥 第三步：模拟云端下发任务（flighttask_prepare）")
    print("-"*70)

    prepare_msg = {
        "data": {
            "flighttask_id": "full_cycle_test_001",
            "wayline_info": {
                "waypoints": [
                    {"latitude": 39.9042, "longitude": 116.4074, "height": 50},
                    {"latitude": 39.9052, "longitude": 116.4084, "height": 60},
                    {"latitude": 39.9062, "longitude": 116.4094, "height": 50}
                ]
            }
        }
    }

    result = mission_mgr.handle_flighttask_prepare(prepare_msg)
    res_data = result.get("data", {}) if isinstance(result, dict) else {}
    print(f"✅ 任务准备结果：result={result.get('result', '未知')}，状态={res_data.get('status', '未知')}")

    # ===================== 第四步：模拟云端下发任务执行 =====================
    print("\n🚀 第四步：模拟云端下发执行指令（flighttask_execute）")
    print("-"*70)

    execute_msg = {
        "data": {
            "flighttask_id": "full_cycle_test_001"
        }
    }

    result = mission_mgr.handle_flighttask_execute(execute_msg)
    res_data = result.get("data", {}) if isinstance(result, dict) else {}
    print(f"✅ 任务执行结果：result={result.get('result', '未知')}，状态={res_data.get('status', '未知')}")

    # ===================== 第五步：模拟航点飞行（进度更新） =====================
    print("\n✈️  第五步：模拟飞行过程，航点逐个推进")
    print("-"*70)

    # 确认监控线程启动（执行成功会自动启动，这里兜底再启动一次）
    if not mission_mgr._monitor_running:
        mission_mgr.start_mission_monitor()

    # 模拟依次飞过航点0 → 1 → 2
    for i in range(3):
        time.sleep(0.6)
        print(f"  📍 模拟飞到第 {i} 个航点")
        wp_manager.current_waypoint = i
        # 手动触发遥测更新
        telemetry.idle_task(None)

    time.sleep(0.6)
    print("✅ 所有航点飞行完毕，触发任务完成逻辑")

    # ===================== 第六步：观察自动返航+收尾逻辑 =====================
    print("\n🛬 第六步：观察任务完成后的自动返航、降落检测、机巢收尾")
    print("-"*70)

    # 模拟飞机返航降落：把遥测高度改成0.3米
    print("  📉 模拟飞机降落，高度降到0.3米...")
    telemetry.telemetry_data["altitudeRelative"] = 0.3
    telemetry.telemetry_data["altitude"] = 0.3

    # 等待收尾线程执行（最多等10秒）
    print("  ⏳ 等待降落检测和机巢收尾...")
    start_wait = time.time()
    while not mission_mgr._cleanup_done and time.time() - start_wait < 10:
        telemetry.idle_task(None)
        time.sleep(0.2)

    # ===================== 最终结果判断 =====================
    print("\n" + "="*70)
    print("📊 测试结果汇总")
    print("="*70)

    checks = [
        ("任务准备成功", mission_mgr._current_task_id == "full_cycle_test_001"),
        ("任务进入完成状态", mission_mgr._status == mission_mgr.MISSION_COMPLETED),
        ("航点进度更新过", mission_mgr._current_waypoint_idx >= 2),
        ("机巢收尾执行完成", mission_mgr._cleanup_done),
    ]

    all_pass = True
    for name, ok in checks:
        status = "✅ 通过" if ok else "❌ 失败"
        print(f"  {status} - {name}")
        if not ok:
            all_pass = False

    print("\n" + "="*70)
    if all_pass:
        print("🎉🎉🎉 恭喜！全业务闭环测试通过！")
        print("整个流程：任务下发→机巢准备→起飞→航点飞行→完成→返航→降落→收尾 全部跑通")
    else:
        print("⚠️  部分项未通过，检查上面的日志找错误原因")
    print("="*70)
