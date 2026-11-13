#!/usr/bin/env python3
"""跑全部测试(项目没有引入 pytest,保持零依赖)。"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
TESTS = ["test_architecture.py", "test_true_scale.py", "test_ui.py",
         "test_ruler_geometry.py", "test_ruler.py", "test_tex2pp.py",
         "test_depthimg.py", "test_proc.py"]


def main() -> int:
    failed = []
    for name in TESTS:
        print(f"\n{'=' * 60}\n=== {name}\n{'=' * 60}")
        code = subprocess.run([sys.executable, str(HERE / name)], cwd=HERE).returncode
        if code:
            failed.append(name)
    print("\n" + "=" * 60)
    print("全部通过 ✓" if not failed else f"失败:{', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
