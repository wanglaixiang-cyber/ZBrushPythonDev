"""功能:网格修复(**空存根** —— 分区保留,功能已清空)。

为什么留个空包:
  * ``app.py`` 遍历 ``FEATURES`` 逐个建子面板,所以本包只要还在注册表里,
    ZBrush 面板上就**仍然显示「---- 网格修复 ----」这一区**(区里是空的);
  * 想重新填功能时,不用再动 app.py / 注册表 / 打包脚本,直接往这个包里写即可。

本次清空删掉的东西(实现 + 专属支撑 + 测试,备份见 ``_backup/*.tar.gz``):
  * ``service.py``            —— 体检 / 封洞 / 减面 / 文件夹布尔预检的业务逻辑
  * ``tools/mesh_pipeline.py``—— 外部子进程管线(trimesh / manifold3d …)
  * ``tests/test_meshtools.py``、``scripts/probe_mesh_io.py``、``scripts/probe_folder_boolean.py``
  * ``tests/fake_zbrush.py`` 里 meshtools 专属的替身接口

要重新实现(照 ``docs/DEVELOPMENT.md`` 第五节):
  1. 在本包里写 ``service.py``(业务逻辑),需要外部子进程就再加 ``tools/``
  2. ``build(panel, cfg)`` 里用 ``panel.button / switch / slider`` 挂控件
  3. 配置键统一加 ``meshtools__`` 前缀(``settings.feature_prefix``)
  4. 设计参考(CLI 契约、实测结论、失败条件)见 ``docs/DESIGN_MESHTOOLS.md``

契约不变:``FEATURE.key`` / ``title`` 保持稳定(key 用于配置前缀与诊断,别改)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ... import ui


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    build: Callable[[ui.Panel, dict[str, Any]], None]


#: 面板标签:功能已清空,所以是空的(重新实现时按 "key": "面板上的名字" 往里加)
LABELS: dict[str, str] = {}


def paths(plugin_title: str = "ZBTools") -> dict[str, str]:
    """该功能在面板里的全部 item path(功能清空后为空表)。"""
    return {k: f"{plugin_title}:{FEATURE.title}:{v}" for k, v in LABELS.items()}


def build(panel: ui.Panel, cfg: dict[str, Any]) -> None:
    """空 build:不挂任何控件。

    ★ 从这里重新开始:往 panel 上挂本功能的按钮 / 开关 / 滑块。
    """
    return None


FEATURE = Feature(key="meshtools", title="网格修复", build=build)
