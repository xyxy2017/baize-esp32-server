import tempfile
import unittest
from pathlib import Path

from core.api.app_demo_store import (
    bind_device,
    bound_device,
    create_device,
    list_devices,
    register_phone_user,
)


class DeviceBindingDateTest(unittest.TestCase):
    def test_binding_endpoints_return_real_binding_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "app_mvp": {
                    "db_path": str(Path(temp_dir) / "binding-date.sqlite3"),
                    "demo_auto_bind_new_devices_to_all_users": False,
                }
            }
            user_id = register_phone_user(
                config, "13800138888", "secret1", "Binding Date User"
            )["user"]["id"]
            device = create_device(
                config,
                device_code="888888",
                display_name="Binding Date Baize",
                source_device_id="binding-date-device",
            )

            bound = bind_device(config, user_id, "888888")
            self.assertIsNotNone(bound["bound_at"])
            self.assertEqual(list_devices(config, user_id)[0]["bound_at"], bound["bound_at"])
            self.assertEqual(bound_device(config, user_id, device["id"])["bound_at"], bound["bound_at"])


if __name__ == "__main__":
    unittest.main()
