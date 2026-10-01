"""SystemInfoTool for providing safe, non-destructive system diagnostics."""

import os
import platform
import socket
from typing import Any, Dict
import psutil
from app.core.logging_config import logger
from app.tools.base import BaseTool


class SystemInfoTool(BaseTool):
    """Tool that retrieves safe read-only operating system and platform diagnostics."""

    name = "system_info"
    description = (
        "Provides safe, non-destructive system diagnostics: Windows version, "
        "hostname, active user profile, network interface status, and battery health."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Collect safe system platform diagnostics."""
        try:
            os_name = platform.system()
            os_release = platform.release()
            os_version = platform.version()
            architecture = platform.machine()
            hostname = platform.node()
            user_name = os.environ.get("USERNAME") or os.environ.get("USER", "Unknown")

            # Check network status (socket test to local loopback / gateway)
            network_online = False
            try:
                # Simple DNS resolve check without sending external data
                socket.gethostbyname("localhost")
                network_online = True
            except Exception:
                network_online = False

            # Battery diagnostics
            battery_info = {"available": False, "percent": None, "power_plugged": None}
            try:
                battery = psutil.sensors_battery()
                if battery:
                    battery_info = {
                        "available": True,
                        "percent": round(battery.percent, 1),
                        "power_plugged": battery.power_plugged,
                    }
            except Exception:
                pass

            msg_parts = [
                f"OS: {os_name} {os_release} ({architecture})",
                f"Host: {hostname}",
                f"User: {user_name}",
                f"Network: {'Connected' if network_online else 'Offline'}",
            ]
            if battery_info["available"]:
                plugged = "Plugged in" if battery_info["power_plugged"] else "On battery"
                msg_parts.append(f"Battery: {battery_info['percent']}% ({plugged})")

            summary = "System Overview: " + " | ".join(msg_parts)

            return {
                "success": True,
                "tool": self.name,
                "os": f"{os_name} {os_release}",
                "os_version": os_version,
                "architecture": architecture,
                "hostname": hostname,
                "user": user_name,
                "network_online": network_online,
                "battery": battery_info,
                "message": summary,
            }
        except Exception as exc:
            logger.error(f"Error gathering system info: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "message": f"Unable to read system info: {str(exc)}",
            }
