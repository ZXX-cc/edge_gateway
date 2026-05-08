import sys
import os

# 1. 自动定位 site-packages 路径
site_pkg = r"C:\Users\13645\miniconda3\envs\backend\Lib\site-packages"
if site_pkg not in sys.path:
    sys.path.append(site_pkg)

from pymavlink.generator import mavgen


# 2. 定义配置类（模拟命令行参数）
class MAVOptions:
    def __init__(self):
        self.language = 'Python'
        self.wire_format = '2.0'
        self.output = 'generated_nest.py'  # 输出到当前目录
        self.validate = False
        self.error_limit = 200
        self.strict_units = False


print("🚀 正在强行启动 MAVLink 生成器 (Internal Function Mode)...")

try:
    # 确保能找到 XML 文件（如果脚本在子目录，请确保路径正确）
    xml_file = 'nest_protocol.xml'
    if not os.path.exists(xml_file):
        # 尝试去上一级目录找
        xml_file = os.path.join('..', 'nest_protocol.xml')

    opts = MAVOptions()
    args = [xml_file]

    # 调用真正的核心生成函数
    mavgen.mavgen(opts, args)

    print(f"✅ 生成成功！")
    print(f"📍 文件位置: {os.path.abspath(opts.output)}")

except Exception as e:
    print(f"❌ 依然失败: {e}")
    print("\n尝试最后的终极手段：直接执行物理路径下的脚本...")
    tools_path = os.path.join(site_pkg, "pymavlink", "tools", "mavgen.py")
    os.system(f'python "{tools_path}" --lang=Python --wire-format=2.0 {xml_file} -o generated_nest.py')
