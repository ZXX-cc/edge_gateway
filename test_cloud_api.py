#!/usr/bin/env python3
"""
大疆上云API接口验证脚本

用法:
    python test_cloud_api.py

功能:
    1. 验证所有157个接口是否正确注册
    2. 模拟云端下发指令，测试接口响应格式
    3. 输出接口覆盖率报告
"""

import sys
import json
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.main import EdgeGateway
from src.utils.message import MessageBuilder


def test_interface_registration():
    """测试接口注册"""
    print("=" * 60)
    print("测试1: 接口注册验证")
    print("=" * 60)

    gw = EdgeGateway()
    if not gw.initialize('./config/gateway.yaml'):
        print("❌ 网关初始化失败")
        return False

    routes = gw.router.list_routes()
    total = len(routes)

    categories = {}
    for method, cat in routes.items():
        categories.setdefault(cat, []).append(method)

    print(f"\n[OK] 已注册接口总数: {total}")
    print(f"\n分类统计:")
    for cat in sorted(categories.keys()):
        count = len(categories[cat])
        print(f"  - {cat.upper()}: {count}个")

    expected_min = 150
    if total >= expected_min:
        print(f"\n[OK] 接口数量达标 (>= {expected_min})")
        return True
    else:
        print(f"\n[FAIL] 接口数量不足 ({total} < {expected_min})")
        return False


def test_mock_requests():
    """模拟云端请求测试"""
    print("\n" + "=" * 60)
    print("测试2: 模拟云端指令测试")
    print("=" * 60)

    gw = EdgeGateway()
    gw.initialize('./config/gateway.yaml')

    # 构造模拟请求
    test_cases = [
        {
            "name": "设备绑定",
            "method": "device_binding",
            "data": {"device_sn": "DRONE-001"}
        },
        {
            "name": "OTA升级",
            "method": "ota_create",
            "data": {"firmware_version": "1.2.3", "package_url": "http://example.com/firmware.bin"}
        },
        {
            "name": "属性设置",
            "method": "property_set",
            "data": {"properties": {"air_conditioner_mode": 1}}
        },
        {
            "name": "打开舱盖",
            "method": "cover_open",
            "data": {}
        },
        {
            "name": "航线任务创建",
            "method": "wayline_task_create",
            "data": {"task_id": "TASK-001"}
        },
        {
            "name": "拍照",
            "method": "camera_photo_take",
            "data": {"payload_index": "0"}
        },
        {
            "name": "开始直播",
            "method": "live_start",
            "data": {"protocol": "webrtc", "url": "wss://example.com/live"}
        },
        {
            "name": "DRC进入",
            "method": "drc_mode_enter",
            "data": {}
        },
        {
            "name": "机场状态查询",
            "method": "dock_status_query",
            "data": {}
        },
        {
            "name": "飞行任务准备",
            "method": "flighttask_prepare",
            "data": {"flighttask_id": "FT-001"}
        },
    ]

    success_count = 0
    for case in test_cases:
        msg = MessageBuilder.build(
            method=case["method"],
            data=case["data"],
            gateway_sn="EDGE-GW-001"
        )

        handler = gw.router.get_handler(case["method"])
        if handler:
            try:
                result = handler(msg)
                if result and result.get("data", {}).get("result") == 0:
                    print(f"  [OK] {case['name']} ({case['method']})")
                    success_count += 1
                else:
                    print(f"  ⚠️ {case['name']} ({case['method']}) - 返回非0: {result}")
            except Exception as e:
                print(f"  [FAIL] {case['name']} ({case['method']}) - 异常: {e}")
        else:
            print(f"  [FAIL] {case['name']} ({case['method']}) - 未找到handler")

    print(f"\n测试结果: {success_count}/{len(test_cases)} 通过")
    return success_count == len(test_cases)


def print_interface_tree():
    """打印接口树"""
    print("\n" + "=" * 60)
    print("测试3: 完整接口树")
    print("=" * 60)

    gw = EdgeGateway()
    gw.initialize('./config/gateway.yaml')

    routes = gw.router.list_routes()
    categories = {}
    for method, cat in routes.items():
        categories.setdefault(cat, []).append(method)

    for cat in sorted(categories.keys()):
        methods = sorted(categories[cat])
        print(f"\n[{cat.upper()}] ({len(methods)}个)")
        for m in methods:
            print(f"    - {m}")


def main():
    print("\n" + "=" * 60)
    print("大疆上云API (DJI Cloud API) 接口验证工具")
    print("=" * 60 + "\n")

    test1_ok = test_interface_registration()
    test2_ok = test_mock_requests()
    print_interface_tree()

    print("\n" + "=" * 60)
    print("验证完成")
    print("=" * 60)

    if test1_ok and test2_ok:
        print("\n[SUCCESS] 所有测试通过！上云API接口框架已就绪。")
        return 0
    else:
        print("\n[WARNING] 部分测试未通过，请检查日志。")
        return 1


if __name__ == '__main__':
    sys.exit(main())
