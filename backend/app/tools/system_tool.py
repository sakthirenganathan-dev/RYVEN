"""SystemStatusTool returning hardware telemetry using psutil."""

import os
from typing import Any, Dict
import psutil
from app.tools.base import BaseTool


class SystemStatusTool(BaseTool):
    """Tool that monitors CPU, memory, disk, and battery telemetry."""

    name = "system_status"
    description = "Provides current hardware diagnostics: CPU utilization, RAM usage, storage metrics, and battery state."
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Collect current system telemetry."""
        # CPU
        cpu_percent = psutil.cpu_percent(interval=None)

        # Virtual Memory (RAM)
        mem = psutil.virtual_memory()
        ram_total_gb = round(mem.total / (1024**3), 2)
        ram_used_gb = round(mem.used / (1024**3), 2)
        ram_percent = mem.percent

        # Primary Storage Disk
        drive_path = os.path.splitdrive(os.path.abspath("."))[0] or "/"
        if not drive_path.endswith(("\\", "/")):
            drive_path += "\\"
        disk = psutil.disk_usage(drive_path)
        disk_total_gb = round(disk.total / (1024**3), 2)
        disk_used_gb = round(disk.used / (1024**3), 2)
        disk_free_gb = round(disk.free / (1024**3), 2)
        disk_percent = disk.percent

        # Battery (if available on laptop/portable device)
        battery_data = {
            "available": False,
            "percent": None,
            "power_plugged": None,
            "time_left_seconds": None,
        }
        try:
            battery = psutil.sensors_battery()
            if battery is not None:
                battery_data = {
                    "available": True,
                    "percent": round(battery.percent, 1),
                    "power_plugged": battery.power_plugged,
                    "time_left_seconds": (
                        battery.secsleft
                        if battery.secsleft != psutil.BATTERY_TIME_UNLIMITED
                        and battery.secsleft != psutil.BATTERY_TIME_UNKNOWN
                        else None
                    ),
                }
        except Exception:
            # On systems where battery sensors are unsupported or raise, fall back gracefully
            pass

        # Build human-readable response message
        msg_parts = [
            f"CPU: {cpu_percent}%",
            f"RAM: {ram_used_gb}/{ram_total_gb} GB ({ram_percent}%)",
            f"Disk: {disk_used_gb}/{disk_total_gb} GB used ({disk_percent}%)",
        ]
        if battery_data["available"]:
            state = "Plugged in" if battery_data["power_plugged"] else "On Battery"
            msg_parts.append(f"Battery: {battery_data['percent']}% ({state})")

        human_message = f"System Diagnostics: {', '.join(msg_parts)}."

        return {
            "cpu_percent": cpu_percent,
            "ram_total_gb": ram_total_gb,
            "ram_used_gb": ram_used_gb,
            "ram_percent": ram_percent,
            "disk": {
                "total_gb": disk_total_gb,
                "used_gb": disk_used_gb,
                "free_gb": disk_free_gb,
                "percent": disk_percent,
            },
            "battery": battery_data,
            "message": human_message,
        }
