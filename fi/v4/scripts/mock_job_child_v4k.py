#!/usr/bin/env python3
"""V4K fixed-function child for benign Windows Job Object tests only.

Never imports Vivado APIs or runs a user-provided command. Do not use it for FI.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import time


def exclusive_marker(path: str, marker: bytes) -> None:
    target = Path(path)
    if not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise RuntimeError('V4K child marker directory invalid')
    with target.open('xb') as f:
        f.write(marker)
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    a = sys.argv[1:]
    if a == ['healthy']:
        return 0
    if len(a) == 2 and a[0] == 'sleep':
        # Only fixed literal is accepted, preventing arbitrary delay/commands.
        if a[1] != 'V4K_FIXED_SLEEP':
            return 70
        time.sleep(20)
        return 0
    if len(a) == 2 and a[0] == 'grandchild':
        exclusive_marker(a[1], b'V4K_GRANDCHILD_ALIVE\n')
        time.sleep(20)
        return 0
    if len(a) == 3 and a[0] == 'tree' and a[2] == 'V4K_FIXED_TREE':
        ack = Path(a[1])
        grandchild_ack = ack.with_name('grandchild_ready.txt')
        if ack.exists() or grandchild_ack.exists():
            return 71
        # The grandchild must inherit the same Windows Job Object. Never
        # request breakaway or detach. The command is fixed to this script.
        subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
                          'grandchild', str(grandchild_ack)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(300):
            if grandchild_ack.exists():
                if grandchild_ack.read_bytes() != b'V4K_GRANDCHILD_ALIVE\n':
                    return 72
                exclusive_marker(str(ack), b'V4K_PARENT_AND_CHILD_READY\n')
                time.sleep(20)
                return 0
            time.sleep(0.01)
        return 73
    return 69


if __name__ == '__main__':
    raise SystemExit(main())
