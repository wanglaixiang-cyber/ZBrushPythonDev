# ZBrush Python API 索引(自动生成)

由 `scripts/gen_api_reference.py` 从本地 SDK 生成,共 **145** 个函数。

## 模块

```python
import zbrush.commands as zbc            # 主模块:151+ 个命令(GUI / 建模 / 系统)
import zbrush.utils as zbu               # run_path / run_script / clear_output
import zbrush.zscript_compatibility as zbz  # ZScript 兼容层
```

另有类型:`zbc.Stroke`、`zbc.Strokes`(笔划)、`zbc.IPaletteDockingBar`、`zbc.ISubPaletteTitleMode`(枚举)。

> 完整签名与 docstring 见 `sdk/api/zbrush/commands.py`;每个函数都带示例,见 `sdk/examples/fragments/`。

## Gui API(68 个)

### Create

- `add_button(item_path: str, info: str = '', fn: Callable = None, initially_disabled: bool = False, width: fl…` — Adds a clickable button to a palette or sub-palette.
- `add_note_button(name: str, icon_path: str = '', initially_pressed: bool = False, initially_disabled: bool =…` — Adds a clickable button to the next note dialog which is shown.
- `add_note_switch(name: str, icon_path: str = '', initially_pressed: bool = False, initially_disabled: bool =…` — Adds a switch button to the next note dialog which is shown.
- `add_palette(item_path: str, docking_bar: int, shortcut: str = '') -> bool` — Adds a new palette to the menu.
- `add_slider(item_path: str, cur_value: float, resolution: int, min_value: float, max_value: float, info: str…` — Adds a draggable slider to a palette or sub-palette.
- `add_subpalette(item_path: str, title_mode: int, icon_path: str = '', left_inset: float = 0.0, right_inset: …` — Adds a sub-palette to a palette or another sub-palette.
- `add_switch(item_path: str, initial_state: bool, info: str = '', fn: Callable = None, initially_disabled: bo…` — Adds a switch to a palette or sub-palette.
- `close(item_path: str, show_zoom_rects: bool = False, parent_window: bool = False) -> None` — Closes a palette or sub-palette.
- `delete_interface_item(item_path: str) -> bool` — Deletes an interface item that has been added by a script.
- `show_note(text: str, item_path: str = '', display_duration: float = 0.0, bg_color: int = -1, distance_to_ui…` — Displays a custom note dialog with the controls defined before the call to this function.

### Dialogs

- `ask_filename(extensions: str, default_file_name: str = '', title: str = '') -> str` — Opens a file loading or saving dialog and returns the selected file name.
- `ask_string(initial_string: str = '', title: str = '') -> str` — Displays a text input dialog and returns the string typed by user.
- `message_ok(message: str = '', title: str = '') -> None` — Displays a message dialog with a button to accept the message.
- `message_ok_cancel(message: str = '', title: str = '') -> bool` — Displays a message dialog with buttons to accept or cancel the dialog.
- `message_yes_no(message: str = '', title: str = '') -> bool` — Displays a message dialog with buttons to accept or reject the dialog.
- `message_yes_no_cancel(message: str = '', title: str = '') -> int` — Displays a message dialog with buttons to accept, reject, or cancel the dialog.

### Getters

- `exists(item_path: str) -> bool` — Verifies that the specified interface item exists.
- `get(item_path: str) -> float` — Returns the current numeric value of an interface item.
- `get_canvas_pan() -> tuple[float, float]` — Returns the offset of the for drawing used canvas area in relation to the visible canvas area on the screen.
- `get_canvas_zoom() -> float` — Returns the zoom of the for drawing used canvas area in relation to the visible canvas area on the screen.
- `get_flags(item_path: str) -> int` — [Private] Returns the status flags of the specified interface item.
- `get_from_note(index: int | str) -> Any` — Returns the value of a note gadget shown in the last displayed note.
- `get_height(item_path: str) -> int` — Returns the height in pixels of the specified interface item.
- `get_hotkey(item_path: str) -> str` — Returns the shortcut of the specified interface item.
- `get_id(item_path: str) -> int` — Returns the ID of the specified interface item.
- `get_info(item_path: str) -> str` — Returns the bubble help text of the specified interface item.
- `get_left_mouse_button_state() -> bool` — Returns the current state of the left mouse button.
- `get_max(item_path: str) -> float` — Returns the maximum possible numeric value the given interface item can take.
- `get_min(item_path: str) -> float` — Returns the minimum possible numeric value the given interface item can take.
- `get_mod(item_path: str) -> int` — Returns the current modifiers state of the given interface item.
- `get_mouse_pos(global_coordinates: bool = False) -> tuple[float, float]` — Returns the current mouse position in canvas or global world space coordinates.
- `get_pos(item_path: str, global_coordinates: bool = False) -> float` — Returns the horizontal position of the given interface item in canvas or global coordinates.
- `get_secondary(item_path: str) -> float` — Returns the the secondary numeric value of an interface item.
- `get_status(item_path: str) -> bool` — Returns the if an interface item is currently enabled or disabled, i.e., if it is greyed out or not.
- `get_title(item_path: str, full_path: bool = False) -> str` — Returns the title of the specified interface item.
- `get_width(item_path: str) -> float` — Returns the width in pixels of the specified interface item.
- `is_disabled(item_path: str) -> bool` — Tests if the specified interface item is disabled, i.e., not greyed out and not interactable.
- `is_enabled(item_path: str) -> bool` — Tests if the specified interface item is enabled, i.e., not greyed out and interactable.
- `is_locked(item_path: str) -> bool` — Tests if the specified interface item is locked, i.e., not interactable.
- `is_unlocked(item_path: str) -> bool` — Tests if the specified interface item is unlocked, i.e., interactable.

### Setters

- `set(item_path: str, h_value: float | None = None, v_value: float | None = None) -> None` — Sets the current numeric values of an interface item.
- `set_canvas_pan(h: float, v: float) -> None` — Sets the offset of the for drawing used canvas area in relation to the visible canvas area on the screen.
- `set_canvas_zoom(zoom_factor: float) -> None` — Sets the zoom factor of the canvas, scaling the visible canvas area in relation to the actual canvas size.
- `set_color(r: int, g: int, b: int) -> None` — Sets  the primary color picker used for the brush color in drawing mode and the mesh color in edit mode.
- `set_hotkey(item_path: str, hotkey: str) -> None` — Sets the hotkey of the specified interface item.
- `set_max(item_path: str, value: float) -> None` — Sets the maximum possible numeric the given script generated interface item can take.
- `set_min(item_path: str, value: float) -> None` — Sets the minimum possible numeric the given script generated interface item can take.
- `set_mod(item_path: str, value: float) -> None` — Sets the modifiers state of the given script generated interface item.
- `set_notebar_text(text: str, progress: float = 0.0) -> None` — Displays a message and optionally progress indicator in the top section of the ZBrush UI.
- `set_status(item_name: str, value: bool) -> None` — Sets a script-generated interface item as either being responsive or unresponsive to user interactions.

### Interaction

- `canvas_click(X1: float, Y1: float, X2: float | None = None, Y2: float | None = None, X3: float | None = Non…` — Emulates a click within the canvas area.
- `click(item_path: str, X1: float | None = None, Y1: float | None = None, X2: float | None = None, Y2: float …` — Clicks the interface item at the given path.
- `disable(item_path: str) -> None` — Makes a script-generated interface item unresponsive to user interactions.
- `enable(item_path: str) -> None` — Makes a script-generated interface item responsive for user interactions.
- `freeze(fn: Callable, fade_time: float = 0.0) -> None` — Disables interface updates to avoid slowdowns while heavy operations are performed.
- `hide(item_path: str, show_zoom_rects: bool = False, parent_window: bool = False) -> None` — Removes an interface item from the UI without deleting it.
- `lock(item_path: str) -> None` — Makes an interface item unresponsive to user interactions.
- `maximize(item_path: str, maximize_all: bool = False) -> None` — Expands an interface item to its maximum size.
- `minimize(item_path: str, minimize_all: bool = False) -> None` — Collapses an interface item to its minimum size.
- `press(item_path: str) -> None` — Brings a button into its pressed state.
- `reset(item: int = 0, version: float = 1.5) -> int` — Brings the ZBrush into a default state associated with a particular version.
- `show(item_path: str, show_zoom_rects: bool = False, parent_window: bool = False) -> None` — Scrolls to or shows the given palette or script generated interface item.
- `show_actions(value: int) -> None` — Enables or disables script interface actions being drawn in the UI.
- `stroke(item_path: str, stroke: Stroke) -> None` — Emulates a brush stroke within an interface item.
- `toggle(item_path: str) -> None` — Toggles the state of a switch.
- `un_press(item_path: str) -> None` — Removes the pressed state of a button.
- `unlock(item_path: str) -> None` — Makes an interface item responsive to user interactions.
- `update(repeat_count: int = 1, redraw_ui: bool = False) -> None` — Forces ZBrush to update its internal state and optionally redraw the UI.

## Modeling API(38 个)

### Curves

- `add_curve_point(curve_index: int, x: float, y: float, z: float) -> int` — Adds a new point to the specified curve.
- `add_new_curve() -> int` — Adds a new curve to the current curves list.
- `curves_to_ui() -> int` — Copies the current list of curves to the active brush.
- `delete_curves() -> None` — Deletes the current curves list.
- `new_curves() -> None` — Creates a new curves list, deleting any previously created ones in the process.

### Miscellaneous

- `create_displacement_map(width: int, height: int, smooth: bool = True, sub_poly: int = 0, border: int = 8, u…` — Creates a displacement map for the currently active tool.
- `create_normal_map(width: int, height: int, smooth: bool = True, sub_poly: int = 0, border: int = 8, uv_tile…` — Creates a normal map for the currently active tool.
- `get_polymesh3d_area() -> float` — Returns the area of current of the active sub-tool in the active tool.
- `get_polymesh3d_volume() -> float` — Returns the volume of current of the active sub-tool in the active tool.
- `is_polymesh3d_solid() -> bool` — Returns if the active subtool in the active tool is a solid mesh.
- `pixol_pick(component: int, h_position: float, v_position: float) -> float` — Returns the specified pixol component for given coordinates.
- `query_mesh3d(property: int, index: int | None = None) -> list[float]` — Gets mesh and UV information for the active tool and its sub-tools.

### Tools and Subtools

- `get_active_subtool_index() -> int` — Returns the index of the active sub-tool.
- `get_active_tool_index() -> int` — Returns the index of the active tool.
- `get_active_tool_path() -> str` — Returns the path of the active tool.
- `get_subtool_count(index: int = -1) -> int` — Returns the number of sub-tools in the specified tool index.
- `get_subtool_folder_index(index: int = -1) -> int` — Returns the index of the folder the specified sub-tool is contained in.
- `get_subtool_folder_name(index: int = -1) -> str` — Returns the name of the folder the specified sub-tool is contained in.
- `get_subtool_id(index: int = -1, subToolIndex: int = -1) -> int` — Returns the unique ID for the given sub-tool index and tool index.
- `get_subtool_status(index: int = -1) -> int` — Returns the status flag for the enabled options of a sub-tool in the active tool.
- `get_tool_count() -> int` — Returns the total number of available tools.
- `get_tool_path(index: int = -1) -> str` — Returns the label or file path for the specified tool.
- `locate_subtool(index: int) -> int` — Returns the index of the sub-tool with the given unique ID within the active tool.
- `locate_subtool_by_name(name: str) -> int` — Returns the index of the sub-tool with the given name within the active tool.
- `select_subtool(index: int = -1) -> int` — Activates the sub-tool with the given index within the active tool.
- `select_tool(index: int) -> int` — Selects the tool with the given index.
- `set_subtool_status(index: int = -1, value: int = 0) -> None` — Sets the status flags for the enabled options of a sub-tool in the active tool.
- `set_tool_path(index: int, path: str) -> int` — Sets the name or file path of the specified tool.

### Transforms

- `get_transform() -> list[float]` — Gets the current tool transform.
- `get_transpose() -> list[float]` — Gets current action line values of the current transpose tool.
- `is_transpose_shown() -> bool` — Returns if a transpose tool is active.
- `set_transform(x_position: float | None = None, y_position: float | None = None, z_position: float | None = …` — Sets the current tool transform.
- `set_transpose(x_start: float | None = None, y_start: float | None = None, z_start: float | None = None, x_e…` — Sets current action line values of the current transpose tool.

### ZSpheres

- `add_zsphere(x: float, y: float, z: float, radius: float, parent_index: int, color: int = -1, mask: int = 0,…` — Adds a new ZSphere to the currently active ZSpheres tool.
- `delete_zsphere(index: int) -> int` — Deletes a ZSphere from the currently active ZSpheres tool.
- `edit_zsphere(commands: Callable, store_undo: bool = False) -> None` — Makes the currently active ZSphere tool editable for API calls.
- `get_zsphere(property: int, index: int, sub_index: int) -> float` — Gets the specified property for a ZSphere within the currently active ZSpheres tool.
- `set_zsphere(property: int, index: int, value: float) -> int` — Gets the specified property for a ZSphere within the currently active ZSpheres tool.

## System API(23 个)

### Files

- `get_last_typed_filename() -> str` — Returns the file path for the last file name that was entered by a user in a load or save operation.
- `get_last_used_filename() -> str` — Returns the file path for the last file name that was entered by a user in a load or save operation.
- `get_next_filename() -> str` — Returns the preset file path or file name used for the next load or save operation.
- `increment_filename(base: str, digits: int = 3, add_copy: bool = False) -> str` — Increments a numeric portion in the given file name or path.
- `make_filename(base: str, index: int, digits: int = 0) -> str` — Combines a file name or path with an index, while ensuring a minimum number of digits.
- `resolve_path(path: str) -> str` — Makes the given relative path absolute in relation to the directory of the executing Python module.
- `set_next_filename(path: str = '', template_path: str = '') -> None` — Presets the file path or name used for the next load or save operation.

### Miscellaneous

- `config(version: float) -> None` — Sets ZBrush to the configuration associated with the given `version`.
- `interpolate(time: float, v1: float | list[float], v2: float | list[float], v3: float | list[float] | None =…` — Returns the linear, quadratic, or cubic interpolation at the given offset `time`.
- `randomize(seed: int) -> None` — Reseeds the random number generator of ZBrush.
- `rgb(r: int, g: int, b: int) -> int` — Returns an RGB color in the int-format used by many functions in the ZBrush API.
- `system_info() -> str` — Returns a string containing the system information.
- `zbrush_info(info: int) -> float` — Returns selected information about the running ZBrush instance.

### Timeline

- `delete_keyframe(frame_index: int) -> int` — Deletes the keyframe at the given frame index in the timeline of ZBrush.
- `get_active_track_index() -> int` — Returns the index of the active track as selected in the 'Movie > Timeline Tracks' palette.
- `get_keyframe_time(frame_index: int) -> float` — Gets the normalized document time for the keyframe at the given index in the active track.
- `get_keyframes_count() -> int` — Returns the total number of keyframes in the active track.
- `get_timeline_time() -> float` — Returns the current time.
- `new_keyframe(time: float | None) -> int` — Creates a new keyframe in the active track at the given document time.
- `set_active_track_index(track_index: int) -> int` — Sets active track as selected in the 'Movie > Timeline Tracks' palette by the given index.
- `set_keyframe_time(frame_index: int, time: float) -> int` — Sets the relative document time of the keyframe at the given index.
- `set_timeline_time(time: float) -> int` — Sets the current time.
- `set_timeline_to_keyframe_time(frame_index: int) -> float` — Sets the current time to the time of the keyframe at the given index.

## 其他(官方分类页里未单独列出)

- `canvas_stroke(stroke: Stroke, delayed: bool | None = None, rotation: float | None = None, h_scale: float | …` — Applies a brush stroke within the current canvas area.
- `canvas_strokes(strokes: Strokes, delayed: bool | None = None, rotation: float | None = None, h_scale: float…` — Applies multiple brush strokes within the current canvas area.
- `create_mesh_from_curves(name: str, action: int = 0, thickness: float = 0.0) -> int` — Creates a mesh from the current curves.
- `fade_in(seconds: float = 0.5) -> None` — Not implemented.
- `fade_out(seconds: float = 0.5) -> None` — Not implemented.
- `get_last_stroke() -> Stroke` — Returns the stroke object for the last stroke that has been made.
- `get_next_template_filename() -> str` — Returns the template file path or file name used for the next load or save operation.
- `get_stroke_info(stroke: Stroke, info: int, index: int = 0) -> float` — Gets the given 'info' property for the passed stroke object.
- `has_next_filename() -> bool` — Tests if a preset file name or path has been set.
- `hide_canvas_extras() -> None` — Hides overlay elements in the canvas.
- `load_stroke(path: str) -> Stroke` — Loads a brush stroke from a text file.
- `load_strokes(path: str) -> Strokes` — Loads a collection of brush strokes from a text file.
- `merge_undo() -> None` — Not implemented.
- `modify(item_path: str, width: int = 0, height: int = 0, icon_path: str = '') -> None` — Modifies the height, width, or icon of an script sourced interface item after it has been created.
- `press_key(shortcut: str, fn: Callable = None, h_position: float = -1.0, v_position: float = -1.0) -> None` — Simulates the user pressing a singular key or shortcut.
- `show_canvas_extras() -> None` — Shows overlay elements in the canvas.

