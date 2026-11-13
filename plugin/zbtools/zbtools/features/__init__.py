"""功能注册表:新功能在这里挂上即可。

约定:
  * 一个功能一个包:features/<key>/,包内至少提供 FEATURE(key / title / build)
  * 功能之间**不要互相 import**;共用代码放 zbtools/zbapi 或 zbtools/ui.py
  * 新功能配置键统一加前缀 "<key>__"(见 settings.feature_prefix)
"""

from __future__ import annotations

from .true_scale import FEATURE as TRUE_SCALE

#: 功能按需打包:比如发行版只带「实际大小」时,features/ruler 整个目录不存在。
#: 所以这里逐个 try —— 缺哪个就少注册哪个,而不是让整个插件起不来。
_FEATURES = [TRUE_SCALE]
try:
    from .ruler import FEATURE as RULER                                  # noqa: PLC0415
    _FEATURES.append(RULER)
except ImportError:
    pass
try:
    from .meshtools import FEATURE as MESHTOOLS                           # noqa: PLC0415
    _FEATURES.append(MESHTOOLS)
except ImportError:
    pass

#: 贴图转色:低模贴图 → 高模 polypaint(外部管线 + PLY 导回)
#: 也按需打包(发行版可以只带「实际大小」)
try:
    from .tex2pp import FEATURE as TEX2PP                             # noqa: PLC0415
    _FEATURES.append(TEX2PP)
except ImportError:
    pass

#: 图片转深度图:选中模型的贴图 → 灰度深度图 → 送回 ZBrush(和上面那个无关的独立功能)
try:
    from .depthimg import FEATURE as DEPTHIMG                             # noqa: PLC0415
    _FEATURES.append(DEPTHIMG)
except ImportError:
    pass

FEATURES = _FEATURES

__all__ = ["FEATURES"]
