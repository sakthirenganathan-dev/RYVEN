"""RYVEN 3.0 — Read-Only Hardware & AI Capability Diagnostic Service.

Safely detects host hardware (CPU, RAM, GPU, VRAM, Disk) and Ollama status without
modifying registry, installing software, downloading models, or exposing secrets.
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional
import httpx
import psutil
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging_config import logger


class HardwareProfile(BaseModel):
    """Normalized snapshot of host hardware and runtime environment."""

    os_name: str
    os_version: str
    architecture: str
    hostname: str

    cpu_name: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    cpu_usage_pct: float

    ram_total_gb: float
    ram_available_gb: float
    ram_used_pct: float

    disk_total_gb: float
    disk_free_gb: float
    disk_used_pct: float

    gpu_available: bool = False
    gpu_name: str = "None"
    gpu_vendor: str = "None"
    vram_gb: Optional[float] = None

    ollama_online: bool = False
    ollama_version: Optional[str] = None
    installed_models: List[Dict[str, Any]] = Field(default_factory=list)

    recommended_tier: str = "7B"  # "3B", "7B", "14B" based on RAM & VRAM
    timestamp: float = Field(default_factory=time.time)


class HardwareDiagnostics:
    """Safe read-only hardware and model capability detector."""

    _cached_profile: Optional[HardwareProfile] = None
    _cache_ttl_seconds: float = 60.0

    @classmethod
    async def get_profile(cls, force_refresh: bool = False) -> HardwareProfile:
        """Retrieve cached hardware profile or perform fresh inspection."""
        now = time.time()
        if not force_refresh and cls._cached_profile is not None:
            if now - cls._cached_profile.timestamp < cls._cache_ttl_seconds:
                return cls._cached_profile

        profile = await cls._inspect_hardware()
        cls._cached_profile = profile
        return profile

    @classmethod
    async def _inspect_hardware(cls) -> HardwareProfile:
        """Inspect host hardware and local Ollama daemon."""
        # 1. OS & Platform
        os_name = f"{platform.system()} {platform.release()}"
        os_version = platform.version()
        arch = platform.machine()
        hostname = platform.node()

        # 2. CPU
        cpu_name = platform.processor() or "Unknown CPU"
        cores_phys = psutil.cpu_count(logical=False) or 1
        cores_log = psutil.cpu_count(logical=True) or cores_phys
        cpu_pct = psutil.cpu_percent(interval=None)

        # 3. RAM
        mem = psutil.virtual_memory()
        ram_total = round(mem.total / (1024**3), 2)
        ram_avail = round(mem.available / (1024**3), 2)
        ram_pct = mem.percent

        # 4. Storage Disk
        drive = os.path.splitdrive(os.path.abspath("."))[0] or "/"
        if not drive.endswith(("\\", "/")):
            drive += "\\"
        disk = psutil.disk_usage(drive)
        disk_total = round(disk.total / (1024**3), 2)
        disk_free = round(disk.free / (1024**3), 2)
        disk_pct = disk.percent

        # 5. GPU & VRAM Detection (Safe, non-destructive)
        gpu_info = cls._detect_gpu()

        # 6. Ollama Capability & Installed Models
        ollama_info = await cls._detect_ollama()

        # 7. Recommended Model Parameter Tier
        tier = "3B"
        if ram_total >= 15.0 or (gpu_info["vram_gb"] and gpu_info["vram_gb"] >= 6.0):
            tier = "7B"
        if ram_total >= 31.0 and (gpu_info["vram_gb"] and gpu_info["vram_gb"] >= 12.0):
            tier = "14B"

        return HardwareProfile(
            os_name=os_name,
            os_version=os_version,
            architecture=arch,
            hostname=hostname,
            cpu_name=cpu_name,
            cpu_cores_physical=cores_phys,
            cpu_cores_logical=cores_log,
            cpu_usage_pct=cpu_pct,
            ram_total_gb=ram_total,
            ram_available_gb=ram_avail,
            ram_used_pct=ram_pct,
            disk_total_gb=disk_total,
            disk_free_gb=disk_free,
            disk_used_pct=disk_pct,
            gpu_available=gpu_info["available"],
            gpu_name=gpu_info["name"],
            gpu_vendor=gpu_info["vendor"],
            vram_gb=gpu_info["vram_gb"],
            ollama_online=ollama_info["online"],
            ollama_version=ollama_info["version"],
            installed_models=ollama_info["models"],
            recommended_tier=tier,
            timestamp=time.time(),
        )

    @classmethod
    def _detect_gpu(cls) -> Dict[str, Any]:
        """Detect GPU vendor, device name, and VRAM safely."""
        info = {
            "available": False,
            "name": "None",
            "vendor": "None",
            "vram_gb": None,
        }

        # 1. Check PyTorch if available
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                name = torch.cuda.get_device_name(0)
                vram = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2)
                vendor = "NVIDIA" if "nvidia" in name.lower() or "geforce" in name.lower() or "rtx" in name.lower() else "Unknown"
                return {"available": True, "name": name, "vendor": vendor, "vram_gb": vram}
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"PyTorch GPU detection skipped: {e}")

        # 2. Windows CIM/WMI video controller detection
        if platform.system() == "Windows":
            try:
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                if res.returncode == 0 and res.stdout.strip():
                    import json
                    parsed = json.loads(res.stdout)
                    # Handle single device or list of devices
                    devices = parsed if isinstance(parsed, list) else [parsed]
                    for dev in devices:
                        name = dev.get("Name", "")
                        if not name:
                            continue
                        name_lower = name.lower()
                        adapter_ram = dev.get("AdapterRAM") or 0
                        vram_gb = round(adapter_ram / (1024**3), 2) if adapter_ram > 0 else None

                        vendor = "Unknown"
                        if "nvidia" in name_lower or "geforce" in name_lower or "quadro" in name_lower:
                            vendor = "NVIDIA"
                        elif "amd" in name_lower or "radeon" in name_lower:
                            vendor = "AMD"
                        elif "intel" in name_lower or "iris" in name_lower or "arc" in name_lower:
                            vendor = "Intel"

                        # Prefer dedicated NVIDIA/AMD over integrated if multiple
                        if vendor in ("NVIDIA", "AMD") or not info["available"]:
                            info = {
                                "available": True,
                                "name": name,
                                "vendor": vendor,
                                "vram_gb": vram_gb,
                            }
                            if vendor in ("NVIDIA", "AMD"):
                                break
            except Exception as exc:
                logger.debug(f"CIM GPU detection skipped: {exc}")

        return info

    @classmethod
    async def _detect_ollama(cls) -> Dict[str, Any]:
        """Probe local Ollama daemon for status, version, and installed model metadata."""
        base_url = settings.ollama_base_url
        result: Dict[str, Any] = {
            "online": False,
            "version": None,
            "models": [],
        }

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                v_res = await client.get(f"{base_url}/api/version")
                if v_res.status_code == 200:
                    result["online"] = True
                    result["version"] = v_res.json().get("version")

                tags_res = await client.get(f"{base_url}/api/tags")
                if tags_res.status_code == 200:
                    raw_models = tags_res.json().get("models", [])
                    clean_models = []
                    for m in raw_models:
                        size_bytes = m.get("size", 0)
                        size_gb = round(size_bytes / (1024**3), 2)
                        details = m.get("details", {})
                        clean_models.append({
                            "name": m.get("name"),
                            "size_gb": size_gb,
                            "parameter_size": details.get("parameter_size", "Unknown"),
                            "quantization_level": details.get("quantization_level", "Unknown"),
                            "family": details.get("family", "Unknown"),
                            "format": details.get("format", "gguf"),
                        })
                    result["models"] = clean_models
        except Exception as exc:
            logger.debug(f"Ollama detection probe failed: {exc}")

        return result
