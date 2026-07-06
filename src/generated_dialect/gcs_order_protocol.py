# gcs_order_protocol.py
"""
机巢 GCS Order 协议解析模块
处理机巢控制命令与状态反馈的编码/解码
"""

from enum import IntEnum
from typing import Dict, List, Tuple
from dataclasses import dataclass


# ==================== 地面站 → 机巢主控（命令位） ====================
class GCSCommand(IntEnum):
    """地面站命令定义（二代机巢协议）"""
    NONE = 0x00  # 无指令

    # 起飞/降落命令 (takeoff_land_order - uint8_t)
    TAKEOFF_START = 0x01  # 起飞命令
    TAKEOFF_ABORT = 0x02  # 起飞终止
    LANDING_ABORT = 0x04  # 降落终止

    # 普通命令 (ordinary_order - uint16_t)
    DOOR_OPEN = 0x1101  # 开门
    DOOR_CLOSE = 0x1102  # 关门
    DOOR_STOP = 0x1103  # 门停止

    CHARGE_START = 0x0F01  # 充电 (修正了小写f为大写F，保持规范)
    CHARGE_STOP = 0x0F02  # 不充电

    CLAMP_TIGHTEN = 0x1401  # 夹紧
    CLAMP_RELEASE = 0x1402  # 夹紧释放
    CLAMP_STOP = 0x1403  # 夹紧停止

    ESTOP_ACTIVATE = 0x0A01  # 急停
    ESTOP_DEACTIVATE = 0x0A02  # 解除急停
    CALIBRATE_R = 0x0A03  # R轴校准
    INITIALIZE = 0x0A04  # 初始化
    FAULT_RECOVERY = 0x0A05  # 故障恢复


# ==================== 机巢主控 → 地面站（状态位） ====================
class DoorState(IntEnum):
    """舱门状态（位0-2的组合值）"""
    OPEN = 0b011  # 3: 位2-1-0 = 011
    CLOSED = 0b101  # 5: 位2-1-0 = 101
    MIDDLE = 0b111  # 7: 位2-1-0 = 111


# ==================== 工具类 ====================
@dataclass
class GCSOrderDecoded:
    """GCS Order 解码结果"""
    raw_value: int
    commands: List[str] = None  # 解析出的命令列表
    door_state: str = None  # 舱门状态
    homing: bool = None
    charging: bool = None
    estop: bool = None
    plc_ready: bool = None
    allow_takeoff: bool = None
    takeoff_in_progress: bool = None
    landing_in_progress: bool = None

    def __post_init__(self):
        if self.commands is None:
            self.commands = []

    def __str__(self) -> str:
        """格式化输出"""
        lines = [f"📊 数据解析 (0x{self.raw_value:08X})"]

        if self.commands:
            lines.append(f"  📤 解析命令: {', '.join(self.commands)}")

        if self.door_state is not None:
            lines.append(f"  🚪 舱门状态: {self.door_state}")
            lines.append(f"  🎯 归中状态: {'✅ 已归中' if self.homing else '❌ 未归中'}")
            lines.append(f"  🔋 充电状态: {'⚡ 充电中' if self.charging else '⏸️ 未充电'}")
            lines.append(f"  🚨 急停状态: {'🛑 急停中' if self.estop else '✅ 正常'}")
            lines.append(f"  🛫 PLC备飞: {'✅ 就绪' if self.plc_ready else '❌ 未就绪'}")
            lines.append(f"  🚀 允许起飞: {'✅ 允许' if self.allow_takeoff else '❌ 不允许'}")
            lines.append(f"  🛫 起飞流程: {'▶️ 执行中' if self.takeoff_in_progress else '⏸️ 未执行'}")
            lines.append(f"  🛬 降落流程: {'▶️ 执行中' if self.landing_in_progress else '⏸️ 未执行'}")

        return "\n".join(lines)


class GCSOrderCodec:
    """协议编码/解码器"""

    @staticmethod
    def decode_gcs_commands(combined_order: int) -> Tuple[bool, List[str]]:
        """
        解码组合的地面站命令
        假设 combined_order 是 (ordinary_order << 8) | takeoff_land_order
        """
        # 1. 提取高16位和低8位
        ordinary_order = (combined_order >> 8) & 0xFFFF
        takeoff_land_order = combined_order & 0xFF

        commands = []
        is_valid = False

        # 2. 解析普通指令
        if ordinary_order != 0:
            try:
                commands.append(GCSCommand(ordinary_order).name)
                is_valid = True
            except ValueError:
                commands.append(f"UNKNOWN_ORDINARY(0x{ordinary_order:X})")

        # 3. 解析起降指令
        if takeoff_land_order != 0:
            try:
                commands.append(GCSCommand(takeoff_land_order).name)
                is_valid = True
            except ValueError:
                commands.append(f"UNKNOWN_TAKEOFF(0x{takeoff_land_order:X})")

        return is_valid, commands

    @staticmethod
    def decode_nest_status(status_mask: int) -> GCSOrderDecoded:
        """解码机巢状态 (基于位运算)"""
        result = GCSOrderDecoded(raw_value=status_mask)

        # 提取低3位（位0-2），匹配3/5/7
        door_value = status_mask & 0b111

        if door_value == DoorState.OPEN:
            result.door_state = "打开"
        elif door_value == DoorState.CLOSED:
            result.door_state = "关闭"
        elif door_value == DoorState.MIDDLE:
            result.door_state = "中间状态"
        else:
            result.door_state = f"未知(0b{door_value:03b})"

        # 解析其他状态位（位3-9）
        result.homing = bool((status_mask >> 3) & 1)
        result.charging = bool((status_mask >> 4) & 1)
        result.estop = bool((status_mask >> 5) & 1)
        result.plc_ready = bool((status_mask >> 6) & 1)
        result.allow_takeoff = bool((status_mask >> 7) & 1)
        result.takeoff_in_progress = bool((status_mask >> 8) & 1)
        result.landing_in_progress = bool((status_mask >> 9) & 1)

        return result

    @staticmethod
    def encode_gcs_command(*commands: GCSCommand) -> int:
        """编码地面站命令为一个组合整数"""
        ordinary_order = 0
        takeoff_land_order = 0

        for cmd in commands:
            if cmd in [GCSCommand.TAKEOFF_START, GCSCommand.TAKEOFF_ABORT, GCSCommand.LANDING_ABORT]:
                takeoff_land_order = cmd.value
            else:
                ordinary_order = cmd.value

        # 组合成值：高16位ordinary_order，低8位takeoff_land_order
        return (ordinary_order << 8) | takeoff_land_order

    @staticmethod
    def encode_nest_status(
            door_state: DoorState,
            homing: bool = False,
            charging: bool = False,
            estop: bool = False,
            plc_ready: bool = False,
            allow_takeoff: bool = False,
            takeoff_in_progress: bool = False,
            landing_in_progress: bool = False
    ) -> int:
        """编码机巢状态"""
        # 舱门状态占据低3位（位0-2）
        status_mask = int(door_state.value) & 0b111

        # 设置其他状态位
        if homing: status_mask |= (1 << 3)
        if charging: status_mask |= (1 << 4)
        if estop: status_mask |= (1 << 5)
        if plc_ready: status_mask |= (1 << 6)
        if allow_takeoff: status_mask |= (1 << 7)
        if takeoff_in_progress: status_mask |= (1 << 8)
        if landing_in_progress: status_mask |= (1 << 9)

        return status_mask


REJECT_REASONS = {
    11: {  # 字节11（普通命令拒绝原因）
        1: "起降命令与一般命令冲突",
        2: "PLC超时",
        3: "飞机超时",
    },
    15: {  # 字节15（起降命令拒绝原因）
        1: "起降命令与一般命令冲突",
        2: "PLC超时",
        3: "飞机超时",
        4: "出现多条起降命令",
    }
}

# 优先级说明（当超过两个拒绝时）
REJECT_PRIORITY = ["机巢主控", "PLC", "无人机"]  # 优先级从高到低
# ==================== 测试代码 ====================
if __name__ == "__main__":
    codec = GCSOrderCodec()

    print("=" * 60)
    print("测试1：地面站命令编码与解码 (修正后)")
    print("=" * 60)

    # 编码：开门命令 + 起飞命令
    cmd_value = codec.encode_gcs_command(
        GCSCommand.DOOR_OPEN,
        GCSCommand.TAKEOFF_START
    )
    print(f"命令组合值: 0x{cmd_value:08X} (二进制: {cmd_value:024b})")

    # 解码验证
    is_valid, decoded_cmds = codec.decode_gcs_commands(cmd_value)
    print(f"解码结果: 有效={is_valid}, 命令={decoded_cmds}")

    print("\n" + "=" * 60)
    print("测试2：机巢状态编码与解码")
    print("=" * 60)

    # 编码：舱门打开 + 允许起飞 + PLC就绪
    status_value = codec.encode_nest_status(
        door_state=DoorState.OPEN,
        plc_ready=True,
        allow_takeoff=True
    )
    print(f"状态掩码: 0x{status_value:08X} (二进制: {status_value:010b})")

    decoded_status = codec.decode_nest_status(status_value)
    print(decoded_status)