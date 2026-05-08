#!/usr/bin/env python3
import re

# 1. device/manager.py
with open('src/device/manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

aliases = '''

    def handle_airport_bind_status(self, msg: dict) -> dict:
        """Airport bind status query (airport_bind_status) - alias of workspace_bind_status"""
        return self.handle_workspace_bind_status(msg)

    def handle_airport_organization_get(self, msg: dict) -> dict:
        """Get airport organization (airport_organization_get) - alias of workspace_get"""
        return self.handle_workspace_get(msg)

    def handle_airport_organization_bind(self, msg: dict) -> dict:
        """Airport organization bind (airport_organization_bind) - alias of device_binding"""
        return self.handle_device_binding(msg)

    def handle_airsense_warning(self, msg: dict) -> dict:
        """
        AirSense warning (airsense_warning)

        Request: { level: int, description: str }
        Response: { result }
        """
        data = msg.get('data', {})
        level = data.get('level', 0)
        description = data.get('description', '')
        logger.info(f"AirSense warning: level={level}, desc={description}")
        return MessageBuilder.build_reply(msg, result=0)
'''

content = content.rstrip() + aliases + '\n'
with open('src/device/manager.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('device/manager.py updated')

# 2. payload/camera.py
with open('src/payload/camera.py', 'r', encoding='utf-8') as f:
    content = f.read()

aliases2 = '''

    def handle_camera_exposure_mode_set(self, msg: dict) -> dict:
        """Camera exposure mode set (camera_exposure_mode_set) - alias of set_exposure_mode"""
        return self.handle_set_exposure_mode(msg)

    def handle_camera_focus_mode_set(self, msg: dict) -> dict:
        """Camera focus mode set (camera_focus_mode_set) - alias of set_focus_mode"""
        return self.handle_set_focus_mode(msg)

    def handle_camera_focus_value_set(self, msg: dict) -> dict:
        """Camera focus value set (camera_focus_value_set) - alias of set_focus_value"""
        return self.handle_set_focus_value(msg)
'''

content = content.rstrip() + aliases2 + '\n'
with open('src/payload/camera.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('payload/camera.py updated')

# 3. mission/manager.py
with open('src/mission/manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

aliases3 = '''

    def handle_fly_to_point(self, msg: dict) -> dict:
        """Fly to point (fly_to_point) - alias of flyto"""
        return self.handle_flyto(msg)
'''

content = content.rstrip() + aliases3 + '\n'
with open('src/mission/manager.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('mission/manager.py updated')

# 4. media/manager.py
with open('src/media/manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

aliases4 = '''

    def handle_live_lens_change(self, msg: dict) -> dict:
        """Live lens change (live_lens_change) - alias of set_livestream_lens"""
        return self.handle_set_livestream_lens(msg)

    def handle_live_set_quality(self, msg: dict) -> dict:
        """Live set quality (live_set_quality) - alias of set_livestream_quality"""
        return self.handle_set_livestream_quality(msg)
'''

content = content.rstrip() + aliases4 + '\n'
with open('src/media/manager.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('media/manager.py updated')
