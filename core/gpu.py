"""GPU 監控：背景執行緒輪詢本機 nvidia-smi。

無 nvidia-smi（如純客戶端壓測機）時，start() 不啟動、stop() 回 None，不影響測試流程。
取樣 GPU 0 的使用率與已用記憶體（多卡可日後擴充）。
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

_QUERY = [
    "nvidia-smi",
    "--query-gpu=utilization.gpu,memory.used",
    "--format=csv,noheader,nounits",
    "-i", "0",
]


@dataclass
class GpuStats:
    util_mean: Optional[float] = None
    util_max: Optional[float] = None
    mem_mean_mb: Optional[float] = None
    mem_max_mb: Optional[float] = None
    samples: int = 0


class GpuSampler:
    """背景定時取樣。用法：sampler.start() ... sampler.stop() -> GpuStats|None。"""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.available = shutil.which("nvidia-smi") is not None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._util: list = []
        self._mem: list = []

    def _sample_once(self):
        try:
            out = subprocess.run(_QUERY, capture_output=True, text=True, timeout=5)
            line = out.stdout.strip().splitlines()
            if not line:
                return
            util_s, mem_s = line[0].split(",")
            self._util.append(float(util_s.strip()))
            self._mem.append(float(mem_s.strip()))
        except (subprocess.SubprocessError, ValueError, OSError):
            pass

    def _loop(self):
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self.interval)

    def start(self):
        if not self.available:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> Optional[GpuStats]:
        if not self.available or self._thread is None:
            return None
        self._stop.set()
        self._thread.join(timeout=self.interval + 2)
        if not self._util:
            return GpuStats(samples=0)
        return GpuStats(
            util_mean=sum(self._util) / len(self._util),
            util_max=max(self._util),
            mem_mean_mb=sum(self._mem) / len(self._mem),
            mem_max_mb=max(self._mem),
            samples=len(self._util),
        )
