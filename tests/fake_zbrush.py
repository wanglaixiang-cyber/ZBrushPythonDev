"""测试用假 zbrush 替身(全项目共用,替代此前两份重复实现)。

按实测语义模拟:
  * query_mesh3d 的包围盒(支持"读数随画布 scale 变化"的开关)
  * Tool:Geometry:* Size(网格尺寸)/ Tool:Export:Scale(ZBrush Scale)
  * Scale Master 读数(网格 × Export Scale,按所选单位换算;可模拟"缩放被藏起来")
  * 画布 transform(位置/scale/旋转)、Draw:Perspective
  * 界面记录(按钮/开关/滑块/提示样式/输入框)与 press 行为
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from typing import Any

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(PROJECT, "plugin", "zbtools")
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

UNIT_FACTORS = {"mm": 1.0, "cm": 10.0, "in": 25.4}

FAKE_DISPLAYS = {
    "ok": True, "count": 2, "active_display_id": 3,
    "displays": [
        {"id": 3, "name": "P270 (1)", "is_main": True, "diagonal_inch": 27.111,
         "native_px": [3840, 2160], "logical_pt": [1920, 1080], "ppi": 162.51,
         "logical_ppi": 81.26, "pixel_scale": 2.0, "physical_mm": [598.38, 340.77]},
        {"id": 1, "name": "Built-in Retina Display", "is_main": False, "diagonal_inch": 16.138,
         "native_px": [3456, 2234], "logical_pt": [1728, 1117], "ppi": 255.0,
         "logical_ppi": 127.5, "pixel_scale": 2.0, "physical_mm": [344.24, 222.52]},
    ],
}


class FakeCommands:
    """ZBrush API 的最小忠实替身。"""

    def __init__(self, fail_button: str | None = None, scalemaster_unit: str | None = "mm",
                 rename_on_import: bool = True, press_raises: set[str] | None = None,
                 mesh: float = 2.0, mesh_xyz: tuple[float, float, float] | None = None,
                 bounds: tuple[float, ...] | None = None, export_scale: float = 1.0,
                 geometry_size: float | None = None, canvas: tuple[float, float] = (1920.0, 1080.0),
                 zoom: float = 1.0, rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
                 position: tuple[float, float] = (960.0, 540.0),
                 perspective_exists: bool = True, perspective_value: float = 1.0,
                 transform_dependent_bbox: bool = False, sm_hidden_scale: float = 1.0,
                 scalemaster_values: tuple[float, float, float] | None = None) -> None:
        self.canvas_wh, self.zoom = canvas, zoom
        self.mesh_xyz = mesh_xyz or (mesh, mesh, mesh)
        self.bounds = bounds                       # 给了就以它为准(可造偏心/非立方体)
        self.export_scale = export_scale
        self.geometry_size = geometry_size         # 覆盖 Tool:Geometry:X Size
        self.sm_unit = scalemaster_unit
        self.sm_values = scalemaster_values
        self.sm_hidden_scale = sm_hidden_scale
        self.transform_dependent_bbox = transform_dependent_bbox
        self.perspective_exists, self.perspective_value = perspective_exists, perspective_value
        self.fail_button = fail_button
        self.transform = [position[0], position[1], 0.0, 1.0, 1.0, 1.0, *rotation]
        self._values = {"Draw:Perspective": perspective_value}
        # 记录
        self.palettes: list[tuple[str, int]] = []
        self.subpalettes: list[tuple[str, int]] = []
        self.buttons: dict[str, Any] = {}
        self.sliders: dict[str, Any] = {}
        self.switches: dict[str, Any] = {}
        self.slider_values: dict[str, float] = {}
        self.switch_values: dict[str, float] = {}
        self.set_calls: list[tuple[str, float]] = []
        self.closed: list[str] = []
        #: 测试预置:这些界面项"已经存在"(用于模拟热更新)
        self.existing_items: set[str] = set()
        self.disabled: list[str] = []
        self.pressed: list[str] = []
        self.notes: list[str] = []
        self.note_styles: list[dict] = []
        self.note_buttons: list[dict] = []
        self.asks: list[tuple[str, str]] = []
        self.ask_answer = ""
        self.bbox_read_at_scale: list[float] = []
        # ui.Panel.set_enabled 的替身记录(通用能力,不属于任何功能)
        self.status_calls: list[tuple[str, bool]] = []
        # Tool 列表(tex2pp 的导入/改名要用;true_scale 的 get_active_tool_path 也用)
        self.tools: list[str] = ["Tool:PolySphere", "Tool:Sphere3D"]
        self.active_tool = 0
        # 实测:Tool:Import 会占用当前 Tool 槽,并把名字改成 "!:<路径>"
        self.rename_on_import = rename_on_import
        #: 这些 item path 在被 press 时"命令已入队但调用本身抛异常"
        #: (真机实测过:ZBrush 的 press 会漏出 CPython 的 SystemError)
        self.press_raises: set[str] = set(press_raises or ())
        #: SubTool 唯一 ID(默认两个,模拟"两个带 UV 的模型")
        self.subtool_ids: list[int] = [101, 202]
        self.active_subtool = 0
        self.subtool_selects: list[int] = []
        #: Tool 列表与"每个 Tool 各自的 SubTool ID"(真机:ID 是相对当前 Tool 的,
        #: 两个 Tool 的 SubTool 0 可能同 ID)
        self.tool_subtool_ids: dict[int, list[int]] = {}
        #: 每个 Tool 的几何(用来验证"几何指纹"兜底),格式 {tool_index: (points, size)}
        self.per_tool_geometry: dict[int, tuple[int, float]] = {}
        self.export_writes_file = True        # Tool:Export 是否真的写盘
        self.uv_box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # UV 框
        self.next_filenames: list[str] = []
        self.asked_files: list[tuple[str, str, str]] = []
        # 记录每个控件的额外参数(项目约定:控件必须显式给 width=1.0,否则 ZBrush 会排重叠)
        self.created_order: list[str] = []            # 控件创建顺序(布局测试要用)
        #: 界面项的 title / tooltip(实测:贴图方块的 title 就是贴图名,tooltip 里带尺寸)
        #: Texture 面板里的贴图(ZBrush 的 Texture:Item Info 滑条逐格读它的名字)
        self.palette: list[str] = []
        self.palette_index: int = -1
        #: 当前 SubTool 挂的贴图(ZBrush 的贴图槽 title 就是它)
        self.slot_texture: str = ""
        #: 打开后,`set` 一个开关也会回调它的 fn(模拟"ZBrush 的 set 会触发回调"这种可能)
        self.fire_switch_callbacks = False
        #: 模拟"点弹窗里的贴图条目":{贴图槽路径: 要点的名字} → 点上就挂上去
        self.popup_assign: dict[str, str] = {}
        self.keys: list[str] = []
        self.clicks: list[str] = []
        self.maximums: dict[str, float] = {}
        self.titles: dict[str, str] = {}
        self.infos: dict[str, str] = {}
        self.button_kwargs: dict[str, dict] = {}
        self.slider_kwargs: dict[str, dict] = {}
        self.switch_kwargs: dict[str, dict] = {}
        #: 控件创建时真正传进去的**悬停提示**(官方叫 bubble help,参数名 info)——
        #: 气泡只有真机看得到,所以测试要靠它来检查(见 test_architecture 的提示预算)
        self.widget_infos: dict[str, str] = {}
        self.ask_filename_answer = ""
        self.tool_renames: list[tuple[int, str]] = []
        self.updates: list[tuple[int, bool]] = []
        self.notebar: list[tuple[str, float]] = []
        #: 调过几次 freeze()(含 fade_time),探针要能跑通
        self.freezes: list[float] = []

    # ---- 内部 ----
    @property
    def _mesh(self) -> tuple[float, float, float]:
        if self.bounds is not None:
            b = self.bounds
            return (abs(b[3] - b[0]), abs(b[4] - b[1]), abs(b[5] - b[2]))
        return self.mesh_xyz

    def _sm_factor(self) -> float:
        return UNIT_FACTORS[self.sm_unit or "mm"]

    # ---- 读 ----
    def get(self, item_path: str) -> float:
        if item_path == "Document:Width":
            return self.canvas_wh[0]
        if item_path == "Document:Height":
            return self.canvas_wh[1]
        if item_path.startswith("Tool:Geometry:") and item_path.endswith(" Size"):
            axis = item_path.split(":")[2].split()[0]
            if self.geometry_size is not None:
                return self.geometry_size
            return self._mesh[{"X": 0, "Y": 1, "Z": 2}[axis]]
        if item_path == "Tool:Export:Scale":
            return self.export_scale
        if item_path.startswith("Zplugin:Scale Master:"):
            tail = item_path.rsplit(":", 1)[-1]
            if tail in UNIT_FACTORS:
                return 1.0 if tail == self.sm_unit else 0.0
            if tail in ("X", "Y", "Z"):
                if self.sm_values is not None:                     # 固定读数(不随 scale 变)
                    return float(self.sm_values[{"X": 0, "Y": 1, "Z": 2}[tail]])
                size = self._mesh[{"X": 0, "Y": 1, "Z": 2}[tail]] * self.sm_hidden_scale
                if not self.transform_dependent_bbox:
                    size *= self.transform[3]                      # 实测:随画布 scale 变化
                return size / self._sm_factor()
            return 0.0
        return self._values[item_path]

    def exists(self, item_path: str) -> bool:
        if item_path in self.existing_items:          # 测试可直接"预置存在的界面项"
            return True
        if item_path == "Draw:Perspective":
            return self.perspective_exists
        if item_path == "Tool:Export:Scale":
            return True
        if item_path.startswith("Tool:Geometry:") and item_path.endswith(" Size"):
            return True
        if item_path.startswith("Zplugin:Scale Master"):
            return self.sm_unit is not None
        return item_path in self._values or item_path.endswith(self.palette_hint())

    def get_title(self, item_path: str, full_path: bool = False) -> str:
        if item_path in self.titles:
            return self.titles[item_path]
        if item_path == "Texture:Item Info":
            # 实测:这个滑条的 title 是"当前贴图名",末尾常带一个句点
            if 0 <= self.palette_index < len(self.palette):
                return self.palette[self.palette_index] + "."
            return ""
        if item_path == "Tool:Texture Map:Texture Map":
            return self.slot_texture or item_path.rsplit(":", 1)[-1]
        return item_path.rsplit(":", 1)[-1]

    def get_max(self, item_path: str) -> float:
        if item_path == "Texture:Item Info" and self.palette:
            return float(len(self.palette) - 1)
        return self.maximums.get(item_path, 0.0)

    def press_key(self, shortcut: str, *args: Any, **kwargs: Any) -> None:
        self.keys.append(shortcut)

    def click(self, item_path: str, *args: Any, **kwargs: Any) -> None:
        self.clicks.append(item_path)
        # 模拟:弹窗开着的那个槽 + 点到了对的贴图名 → 贴图槽的 title 变成那张贴图
        if item_path.startswith("PopUp:"):
            wanted = item_path.split("PopUp:", 1)[1]
            # 真机上挂上之后,**贴图槽显示的名字**会变成那张贴图;两个写法指的是同一个槽
            if wanted in set(self.popup_assign.values()):
                self.slot_texture = wanted
                for slot in ("Tool:Texture Map:Texture Map", "Tool:Texture Map:TextureMap"):
                    self.titles[slot] = wanted
                    self.infos[slot] = (f"Texture Map\n\n{wanted}\n\n"
                                        f"Width=48\nHeight=48")

    def get_info(self, item_path: str) -> str:
        return self.infos.get(item_path, "")

    def get_status(self, item_path: str) -> bool:
        return True

    def palette_hint(self) -> str:
        return "NotAZBrushPalette"

    def query_mesh3d(self, prop: int, index: int | None = None) -> list[float]:
        if prop == 3:                        # UV 框(left, top, right, bottom)
            return list(self.uv_box)
        if prop in (0, 1) and self.active_tool in self.per_tool_geometry:
            points = float(self.per_tool_geometry[self.active_tool][0])
            # prop 0 = 点数;prop 1 = 面数(替身按"面数 ≈ 点数"给个合理值)
            return [points if prop == 0 else round(points * 0.75)]
        if prop == 2 and self.active_tool in self.per_tool_geometry:
            size = float(self.per_tool_geometry[self.active_tool][1])
            return [-size / 2, -size / 2, -size / 2, size / 2, size / 2, size / 2]
        assert prop == 2, "只应查询包围盒属性"
        self.bbox_read_at_scale.append(self.transform[3])
        if self.bounds is not None:
            return list(self.bounds)
        m = self._mesh
        return [-m[0] / 2, -m[1] / 2, -m[2] / 2, m[0] / 2, m[1] / 2, m[2] / 2]

    def get_canvas_zoom(self) -> float:
        return self.zoom

    def get_canvas_pan(self) -> tuple[float, float]:
        return (0.0, 0.0)

    def get_transform(self) -> list[float]:
        return list(self.transform)

    def get_active_tool_path(self) -> str:
        return self.tools[self.active_tool]

    def get_active_tool_index(self) -> int:
        return self.active_tool

    # ---- Tool 列表(tex2pp:导入会占用当前槽 → 读名字/改名) ----
    def get_tool_count(self) -> int:
        return len(self.tools)

    def get_tool_path(self, index: int = -1) -> str:
        if index < 0:
            return self.tools[self.active_tool]
        return self.tools[index]

    def set_tool_path(self, index: int, path: str) -> int:
        self.tool_renames.append((int(index), path))
        if not (0 <= int(index) < len(self.tools)):
            return -1
        self.tools[int(index)] = path                    # 官方文档:扩展名会被省略
        return 0

    def update(self, repeat_count: int = 1, redraw_ui: bool = False) -> None:
        self.updates.append((int(repeat_count), bool(redraw_ui)))

    def set_notebar_text(self, text: str, progress: float = 0.0) -> None:
        self.notebar.append((text, float(progress)))

    def freeze(self, fn: Any, fade_time: float = 0.0) -> None:
        """官方:冻结大部分 UI 跑重活(进度条仍工作)。替身直接跑,并记一笔。"""
        self.freezes.append(float(fade_time))
        fn()

    # ---- SubTool(tex2pp 用来判定"① 记住的是哪一个") ----
    def get_active_subtool_index(self) -> int:
        return self.active_subtool

    def get_subtool_id(self, index: int = -1, subToolIndex: int = -1) -> int:
        target = self.active_subtool if subToolIndex < 0 else int(subToolIndex)
        ids = self.tool_subtool_ids.get(self.active_tool, self.subtool_ids)
        if not (0 <= target < len(ids)):
            return -1
        return ids[target]

    def select_subtool(self, index: int = -1) -> bool:
        self.subtool_selects.append(int(index))
        if 0 <= int(index) < len(self.subtool_ids):
            self.active_subtool = int(index)
            return True
        return False

    # ---- 交互(输入框 / 通用启用状态) ----
    def ask_string(self, initial_string: str = "", title: str = "") -> str:
        self.asks.append((initial_string, title))
        return self.ask_answer

    def set_status(self, item_name: str, value: bool) -> None:
        """ui.Panel.set_enabled 走这里(通用能力,不属于任何功能)。"""
        self.status_calls.append((item_name, bool(value)))
        self._values[item_name] = 1.0 if value else 0.0

    # ---- 写 ----
    def set(self, item_path: str, h_value: float | None = None, v_value: float | None = None) -> None:
        if h_value is None:
            return
        self.set_calls.append((item_path, float(h_value)))
        if item_path == "Texture:Item Info" and self.palette:
            self.palette_index = max(0, min(int(h_value), len(self.palette) - 1))
        if item_path in self.sliders:
            self.slider_values[item_path] = float(h_value)
        elif item_path in self.switches:
            self.switch_values[item_path] = float(h_value)
            if self.fire_switch_callbacks:
                fn = self.switches[item_path]
                if callable(fn):
                    try:
                        fn("", float(h_value))
                    except Exception:                                     # noqa: BLE001
                        pass
        else:
            self._values[item_path] = h_value

    def set_transform(self, *args: float) -> None:
        assert len(args) == 9, f"set_transform 需要 9 个参数,收到 {len(args)}"
        self.transform = [float(v) for v in args]

    def show_note(self, text: str, **kwargs: Any) -> None:
        self.notes.append(text)
        self.note_styles.append(dict(kwargs))

    def ask_filename(self, extensions: str = "", default_file_name: str = "",
                     title: str = "") -> str:
        self.asked_files.append((extensions, default_file_name, title))
        return self.ask_filename_answer

    def set_next_filename(self, path: str = "", template_path: str = "") -> None:
        self.next_filenames.append(path)

    def press(self, item_path: str) -> None:
        self.pressed.append(item_path)
        tail = item_path.rsplit(":", 1)[-1]
        if item_path.startswith("Zplugin:Scale Master:") and tail in UNIT_FACTORS:
            self.sm_unit = tail
        # 实测行为:导入结果占用当前 Tool 槽,名字变成 "!:<完整路径>"
        if item_path == "Texture:Import" and self.next_filenames:
            name = os.path.splitext(os.path.basename(self.next_filenames[-1]))[0]
            if name:
                self.palette.append(name)          # 面板里多一张,并选中它(实测行为)
                self.palette_index = len(self.palette) - 1
        if item_path.startswith("Tool:Texture Map:Clone") and self.slot_texture:
            self.palette.append(self.slot_texture)
            self.palette_index = len(self.palette) - 1
        if item_path == "Tool:Import" and self.next_filenames and self.rename_on_import:
            self.tools[self.active_tool] = "!:" + self.next_filenames[-1]
        if item_path == "Tool:Export" and self.export_writes_file and self.next_filenames:
            path = self.next_filenames[-1]
            if path:
                directory = os.path.dirname(path)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("v 0 0 0\n")
        # 真机实测:命令进了队列,但 press 这层偶尔会漏出异常
        if item_path in self.press_raises:
            raise RuntimeError(
                "<built-in method press of PyCapsule object at 0x0> returned a result "
                "with an exception set")

    # ---- 界面构建 ----
    def add_palette(self, item_path: str, docking_bar: int = 0, shortcut: str = "") -> bool:
        self.palettes.append((item_path, docking_bar))
        return True

    def add_subpalette(self, item_path: str, title_mode: int = 0, **kwargs: Any) -> bool:
        self.subpalettes.append((item_path, title_mode))
        return True

    def add_button(self, item_path: str, info: str = "", fn: Any = None, **kwargs: Any) -> bool:
        if self.fail_button and item_path == self.fail_button:
            return False
        self.buttons[item_path] = fn
        self.created_order.append(item_path)
        self.button_kwargs[item_path] = dict(kwargs)
        self.widget_infos[item_path] = info
        return True

    def add_slider(self, item_path: str, cur_value: float, resolution: int, min_value: float,
                   max_value: float, info: str = "", fn: Any = None, **kwargs: Any) -> bool:
        self.sliders[item_path] = fn
        self.slider_values[item_path] = float(cur_value)
        self.created_order.append(item_path)
        self.slider_kwargs[item_path] = dict(kwargs)
        self.widget_infos[item_path] = info
        return True

    def add_switch(self, item_path: str, initial_state: bool, info: str = "", fn: Any = None,
                   **kwargs: Any) -> bool:
        self.switches[item_path] = fn
        self.switch_values[item_path] = 1.0 if initial_state else 0.0
        self.created_order.append(item_path)
        self.switch_kwargs[item_path] = dict(kwargs)
        self.widget_infos[item_path] = info
        return True

    def add_note_button(self, name: str, **kwargs: Any) -> bool:
        self.note_buttons.append({"name": name, **kwargs})
        return True

    def disable(self, item_path: str, **kwargs: Any) -> bool:
        self.disabled.append(item_path)
        return True

    def close(self, item_path: str, **kwargs: Any) -> None:
        self.closed.append(item_path)
        self.existing_items.discard(item_path)        # 关掉就真的不见了


class FakeUtils:
    """zbrush.utils 的替身:记录 run_script / run_path 调用(IOBuffer 探针用)。"""

    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.paths: list[str] = []

    def run_script(self, script: str, shared_environment: bool = True) -> bool:
        self.scripts.append(script)
        return True

    def run_path(self, script_path: str, shared_environment: bool = True) -> bool:
        self.paths.append(script_path)
        return True


#: 最近一次 install 进来的 fake(测试里要用"同一个"实例时读它)
commands: FakeCommands | None = None


def install(fake: FakeCommands):
    """装上假 zbrush,导入插件模块,并把屏幕探测固定为 FAKE_DISPLAYS。"""
    package = types.ModuleType("zbrush")
    package.commands = fake
    package.utils = utils = FakeUtils()
    sys.modules["zbrush"] = package
    global commands
    commands = fake
    sys.modules["zbrush.commands"] = fake
    sys.modules["zbrush.utils"] = utils
    keep = {"zbtools.errors", "zbtools.notes"}          # 叶子模块:保留以免类对象被重建
    for name in [m for m in list(sys.modules) if m == "zbtools" or m.startswith("zbtools.")]:
        if name not in keep:
            del sys.modules[name]

    from zbtools import settings
    settings.CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "settings.json")
    from zbtools.features.true_scale import service
    from zbtools.zbapi import canvas, model, screen
    screen.detect_displays = lambda cfg, force=False: FAKE_DISPLAYS
    screen._DISPLAY_CACHE = FAKE_DISPLAYS
    return service, screen, model, canvas, settings
