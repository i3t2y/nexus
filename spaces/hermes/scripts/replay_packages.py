"""Ephemeral Package Replay（借鉴 HuggingMes）。

HF 免费 Space 重启后 pip 装的包会丢。本脚本在容器启动时：
1. 记录本次运行中新 pip install 的包到 installed_packages.log
2. 下次启动若日志存在，重装这些包

用法：
  - 启动前: python scripts/replay_packages.py replay   # 重装历史包
  - 运行中需装包: pip install X && python scripts/replay_packages.py add X
"""
from __future__ import annotations

import os
import subprocess
import sys

_LOG = os.getenv("REPLAY_LOG", "/app/installed_packages.log")


def replay() -> None:
    if not os.path.exists(_LOG):
        print("[replay] no log, skip")
        return
    with open(_LOG) as f:
        pkgs = [ln.strip() for ln in f if ln.strip()]
    if not pkgs:
        return
    print(f"[replay] reinstalling {len(pkgs)} packages...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], check=False)
    print("[replay] done")


def add(name: str) -> None:
    """记录一个包名到日志（去重）。"""
    existing = set()
    if os.path.exists(_LOG):
        existing = {ln.strip() for ln in open(_LOG) if ln.strip()}
    existing.add(name)
    with open(_LOG, "w") as f:
        f.write("\n".join(sorted(existing)) + "\n")
    print(f"[replay] recorded {name}, total {len(existing)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "replay"
    if cmd == "replay":
        replay()
    elif cmd == "add" and len(sys.argv) > 2:
        add(sys.argv[2])
    else:
        print("usage: replay_packages.py [replay|add <pkg>]")
