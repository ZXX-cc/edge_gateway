import os
import hashlib
from datetime import datetime

class FileManager:
    def __init__(self):
        # FIX: 定义固件保存根目录，自动创建（修复路径为空）
        self.firmware_dir = "./ota_firmware"
        os.makedirs(self.firmware_dir, exist_ok=True)
        self.current_firmware_path = None

    def save_firmware(self, firmware_data, target_version):
        """保存固件到本地，返回完整路径"""
        try:
            # 生成唯一文件名
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            file_name = f"firmware_{target_version}_{timestamp}.bin"
            file_path = os.path.join(self.firmware_dir, file_name)

            # 写入固件数据
            with open(file_path, "wb") as f:
                f.write(firmware_data)

            # FIX: 赋值并返回有效路径（修复返回None）
            self.current_firmware_path = file_path
            print(f"[INFO] 固件保存成功：{file_path}")
            return file_path
        except Exception as e:
            print(f"[ERROR] 固件保存失败：{str(e)}")
            return None

    def get_firmware_path(self):
        """获取当前固件路径"""
        return self.current_firmware_path

    def verify_firmware(self, file_path, expected_md5):
        """校验固件MD5"""
        if not os.path.exists(file_path):
            return False
        with open(file_path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        return md5 == expected_md5
