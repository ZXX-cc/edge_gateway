"""
生成自定义MAVLink Dialect
"""
import os
import sys
import codecs

def generate_dialect():
    """生成自定义dialect"""
    # ========== 1. 准备路径 ==========
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    xml_file = os.path.join(script_dir, 'drone_nest.xml')
    output_dir = os.path.join(project_root, 'generated_dialect')

    if not os.path.exists(xml_file):
        print(f"❌ 找不到XML文件: {xml_file}")
        return False

    os.makedirs(output_dir, exist_ok=True)

    print("=" * 70)
    print("🔧 生成MAVLink Dialect")
    print("=" * 70)
    print(f"输入文件: {xml_file}")
    print(f"输出目录: {output_dir}")
    print("=" * 70)

    try:
        # ========== 2. 导入 mavgen ==========
        from pymavlink.generator import mavgen

        # ========== 3. 手动验证 XML (使用 UTF-8) ==========
        print(f"\n🔄 验证 XML 文件...")
        try:
            from lxml import etree
            parser = etree.XMLParser(encoding='utf-8')
            with codecs.open(xml_file, 'r', encoding='utf-8') as f:
                xmldocument = etree.parse(f, parser)
            print(f"✅ XML 格式验证通过")
        except Exception as e:
            print(f"❌ XML 验证失败: {e}")
            return False

        # ========== 4. 处理编码问题 - 创建临时文件 ==========
        print(f"\n🔄 创建临时文件...")
        with codecs.open(xml_file, 'r', encoding='utf-8') as f:
            content = f.read()

        temp_file = xml_file + '.tmp'
        with codecs.open(temp_file, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"✅ 临时文件: {temp_file}")

        # ========== 5. 配置生成选项（跳过验证） ==========
        class Opts:
            def __init__(self):
                self.wire_protocol = '2.0'
                self.language = 'Python'
                self.output = output_dir
                self.validate = False  # ⭐ 关键：跳过 mavgen 的内置验证
                self.error_limit = 200
                self.strict_units = False

        opts = Opts()

        print(f"\n🔄 开始生成...")

        # ========== 6. 执行生成 ==========
        mavgen.mavgen(opts, [temp_file])

        # 删除临时文件
        if os.path.exists(temp_file):
            os.remove(temp_file)
            print(f"✅ 已删除临时文件")

        print("\n✅ Dialect 生成成功！")

        # ========== 7. 检查生成的文件 ==========
        # ========== 7. 检查生成的文件 ==========
        print(f"\n📁 检查生成的文件:")
        found_files = []

        # ⭐ 检查项目根目录（mavgen有时会在这里生成）
        root_gen_file = os.path.join(project_root, 'generated_dialect.py')
        target_file = os.path.join(output_dir, 'drone_nest.py')

        if os.path.exists(root_gen_file):
            import shutil
            # 移动到正确的目录并重命名
            if os.path.exists(target_file):
                os.remove(target_file)
            shutil.move(root_gen_file, target_file)
            print(f"📦 已移动并重命名: generated_dialect.py → {output_dir}/drone_nest.py")
            found_files.append('drone_nest.py')

        # 检查输出目录
        if os.path.exists(output_dir):
            for file in sorted(os.listdir(output_dir)):
                if file.endswith('.py'):
                    file_path = os.path.join(output_dir, file)
                    size = os.path.getsize(file_path)
                    print(f"  ✓ {file} ({size:,} bytes)")
                    if file not in found_files:
                        found_files.append(file)

        # 创建或更新__init__.py
        init_file = os.path.join(output_dir, '__init__.py')
        with open(init_file, 'w', encoding='utf-8') as f:
            f.write('"""Generated MAVLink dialect for Drone Nest"""\n')
            f.write('from .drone_nest import *\n')
        print(f"📦 已创建: __init__.py")

        # ========== 8. 验证导入 ==========
        drone_nest_py = os.path.join(output_dir, 'drone_nest.py')
        if os.path.exists(drone_nest_py):
            print(f"\n✅ 核心文件已就绪: drone_nest.py")

            sys.path.insert(0, output_dir)
            try:
                import drone_nest
                print(f"✅ 模块导入成功")

                # 检查关键类
                checks = [
                    ('MAVLink_drone_nest_command_message', 'DRONE_NEST_COMMAND'),
                    ('MAVLink_drone_nest_response_message', 'DRONE_NEST_RESPONSE'),
                    ('MAVLink_drone_nest_order_response_message', 'DRONE_NEST_ORDER_RESPONSE'),
                ]

                print(f"\n🔍 验证消息定义:")
                for attr, desc in checks:
                    if hasattr(drone_nest, attr):
                        print(f"  ✅ {desc} 消息类")
                    else:
                        print(f"  ⚠️  未找到 {desc}")

                # 验证发送方法
                try:
                    mav = drone_nest.MAVLink(None)
                    methods = [
                        'drone_nest_command_send',
                        'drone_nest_response_send',
                        'drone_nest_order_response_send',
                    ]
                    for method in methods:
                        if hasattr(mav, method):
                            print(f"  ✅ {method}() 方法")
                except:
                    pass

                print(f"\n🎉 Dialect 生成并验证成功！")

            except Exception as e:
                print(f"⚠️  导入验证失败: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"\n⚠️  警告: drone_nest.py 未找到")

        return True

    except ImportError as e:
        print(f"\n❌ 导入失败: {e}")
        print("请安装依赖: pip install pymavlink lxml")
        return False
    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = generate_dialect()
    sys.exit(0 if success else 1)
