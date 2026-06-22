"""執行期間的即時進度條（手刻，僅標準函式庫）。

只負責「暫態進度」顯示，與 reporter.py 的「最終摘要」分離：
  - 寫到 stderr（非 stdout）：摘要與 CSV 路徑走 stdout，進度條走 stderr，
    使用者把 stdout 導向檔案時 log 不會被 `\\r` 洗版，終端機仍看得到進度。
  - 自動依 TTY 啟用：非 TTY（CI、重導向、管線）時完全靜默，不印任何東西。
"""
from __future__ import annotations

import sys
import time

_FILLED = "█"      # 已完成
_EMPTY = "░"       # 未完成


class ProgressBar:
    """以 `\\r` 原地重畫的單行進度條。

    用法：建立後每完成一筆呼叫 update(completed, failed=...)，全部結束呼叫 close()。
    non-TTY 或 total<=0 時所有方法皆為 no-op，呼叫端不需特別判斷。
    """

    def __init__(self, total, *, label="", width=24, stream=None,
                 enabled=None, min_interval=0.1):
        self.total = int(total)
        self.label = label
        self.width = width
        self.stream = stream if stream is not None else sys.stderr
        # enabled 預設依 stream 是否為 TTY 自動判斷；total<=0 一律停用
        if enabled is None:
            enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = bool(enabled) and self.total > 0
        self.min_interval = min_interval
        self._start = time.perf_counter()
        self._last_draw = 0.0
        self._last_pct = -1
        self._maxlen = 0          # 記住上次行長，重畫時補空白清掉殘字

    def update(self, completed, *, failed=0):
        """重畫進度。以整數百分比與 min_interval 節流，完成時必畫。"""
        if not self.enabled:
            return
        now = time.perf_counter()
        pct = int(completed * 100 / self.total)
        done = completed >= self.total
        if not done and pct == self._last_pct and (now - self._last_draw) < self.min_interval:
            return
        self._last_pct = pct
        self._last_draw = now

        filled = int(self.width * completed / self.total)
        bar = _FILLED * filled + _EMPTY * (self.width - filled)
        elapsed = now - self._start
        parts = [f"測試進度 {self.label} |{bar}| {pct:3d}% {completed}/{self.total}"]
        if failed:
            parts.append(f"失敗{failed}")
        parts.append(f"已用 {elapsed:.1f}s")
        if completed and not done:
            eta = elapsed / completed * (self.total - completed)
            parts.append(f"剩餘 {eta:.1f}s")
        body = " ".join(parts)

        # 補空白清掉上一行較長的殘字（如完成時「剩餘」消失），避免依賴 ANSI escape
        pad = max(0, self._maxlen - len(body))
        self._maxlen = len(body)
        self.stream.write("\r" + body + " " * pad)
        self.stream.flush()

    def close(self):
        """收尾換行，讓後續的摘要輸出從新的一行開始。"""
        if not self.enabled:
            return
        self.stream.write("\n")
        self.stream.flush()
