"""第一个 ZBrush Python 脚本:在画布中央放一个可编辑立方体并调整视角。

注意:
- 本脚本必须在 ZBrush 内部运行(见 README「如何运行」),本地直接跑不会有效果——
  sdk/api 里是官方类型存根(函数体为空),仅用于 IDE 自动补全/类型检查。
"""

import zbrush.commands as zbc            # 主模块:151+ 个命令(GUI / 建模 / 系统)
import zbrush.utils as zbu               # run_path / run_script / clear_output
import zbrush.zscript_compatibility as zbz  # ZScript 兼容层
from zbrush import *
