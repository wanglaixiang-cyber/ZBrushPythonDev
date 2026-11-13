"""最小复现脚本:只建一个「面板 + 子面板 + 一个按钮」,用来隔离排查 UI 问题。

用法:ZBrush > ZScript > Python Scripting > Load > 本文件

排查逻辑:
  - 看不到 SmokeTest 面板        -> ZBrush 版本/权限层面的问题(先重启 ZBrush 再试)
  - 有面板但里面是空白长条、没按钮 -> item path 挂载问题(把控制台输出发我)
  - 按钮出来了                   -> UI 机制正常,ZBTools 的问题在别处

控制台会打印每一步的返回值,失败会直接弹提示。
"""

from __future__ import annotations

import zbrush.commands as zbc

PALETTE = "SmokeTest"
SUB = f"{PALETTE}:Panel"
BUTTON = f"{SUB}:Hello"

failures: list[str] = []


def hello(sender: str) -> None:
    print("[SmokeTest] 按钮回调被触发,sender =", sender)
    zbc.show_note("SmokeTest 按钮正常", display_duration=4.0)


def step(what: str, result: object) -> None:
    ok = result is True
    print(f"[SmokeTest] {what} -> {result!r} {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append(f"{what} -> {result!r}")


if zbc.exists(PALETTE):
    print("[SmokeTest] 已存在旧面板,先关闭")
    zbc.close(PALETTE)

step(f'add_palette("{PALETTE}", docking_bar=1)', zbc.add_palette(PALETTE, docking_bar=1))
step(f'add_subpalette("{SUB}", 0)', zbc.add_subpalette(SUB, 0))
step(f'add_button("{BUTTON}")', zbc.add_button(BUTTON, "冒烟测试按钮", hello))

print("[SmokeTest] exists(PALETTE) =", zbc.exists(PALETTE))
print("[SmokeTest] exists(SUB)     =", zbc.exists(SUB))
print("[SmokeTest] exists(BUTTON)  =", zbc.exists(BUTTON))

if failures:
    zbc.show_note("SmokeTest 有失败步骤:\n" + "\n".join(failures), display_duration=10.0)
else:
    print("[SmokeTest] 三步都返回 True。请在右侧菜单栏找 'SmokeTest' 面板。")
