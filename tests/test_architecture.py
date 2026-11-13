"""架构约束测试:把"结构怎么保持清晰"用测试固定下来。

规则(违反就红):
  1. features/<a> 不能 import features/<b>(功能之间互不依赖)
  2. zbapi / ui / settings / app 不能 import features(依赖方向单向)
  3. 每个功能包必须暴露 FEATURE(key / title / build),key 唯一
  4. 功能包里不允许出现裸的相对 import 越界(如 from ...features.other import)
  5. 插件入口只做装配:init.py 里不得出现业务关键字
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

PROJECT = pathlib.Path(__file__).resolve().parent.parent
PKG = PROJECT / "plugin" / "zbtools" / "zbtools"
if str(PROJECT / "plugin" / "zbtools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "plugin" / "zbtools"))


def _imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            out.append(mod)
        elif isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
    return out


def test_features_are_isolated() -> None:
    feats = sorted(p for p in (PKG / "features").iterdir() if p.is_dir() and p.name != "__pycache__")
    for feat in feats:
        for py in feat.rglob("*.py"):
            rel = py.relative_to(PKG).as_posix()
            for imp in _imports(py):
                for other in feats:
                    if other.name == feat.name:
                        continue
                    if other.name in imp:
                        raise AssertionError(f"{rel} 依赖了另一个功能:{imp}")
    print(f"  [ok] {len(feats)} 个功能互不依赖:{', '.join(f.name for f in feats)}")


def test_shared_layers_do_not_depend_on_features() -> None:
    """zbapi / ui / settings / notes / errors 不依赖 features;app 只允许引用功能注册表。"""
    for py in list((PKG / "zbapi").glob("*.py")) + [
            PKG / n for n in ("ui.py", "settings.py", "notes.py", "errors.py", "proc.py")]:
        for imp in _imports(py):
            if "features" in imp:
                raise AssertionError(f"{py.name} 不该依赖 features:{imp}")
    for imp in _imports(PKG / "app.py"):
        if "features" in imp and not imp.endswith("features"):
            raise AssertionError(f"app.py 只应引用功能注册表,实际:{imp}")
    print("  [ok] 共用层(含 proc.py)不依赖 features(app 只引用注册表)")


def test_shared_layers_do_not_reference_feature_paths() -> None:
    """共用层连 features 的**路径字符串**都不该出现。

    这条规则来自一次真实回归:显示器探测脚本原本放在 features/true_scale/tools/ 下,
    zbapi/screen.py 里硬写了 "features"/"true_scale"/"tools" 拼路径 —— import 检查
    看不出来,但共用层已经反过来依赖了某个功能。现在辅助脚本统一放 plugin/zbtools/tools/。
    """
    for py in (PKG / "zbapi").glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "features" in text:
            raise AssertionError(f"{py.name} 里出现了 features 路径字符串(共用层不该知道功能)")
    tools = PKG.parent / "tools"
    for name in ("display_info.py", "ruler_window.py"):
        if not (tools / name).exists():
            raise AssertionError(f"共用辅助脚本缺失:tools/{name}")
    print("  [ok] 共用层不出现 features 路径;辅助脚本都在 plugin/zbtools/tools/")


def test_feature_contract() -> None:
    """每个功能包都提供完整的 FEATURE(key / title / build),且 key 唯一。"""
    from zbtools.features import FEATURES
    keys = [f.key for f in FEATURES]
    assert len(keys) == len(set(keys)), f"feature key 重复:{keys}"
    for f in FEATURES:
        assert f.key and f.title and callable(f.build), f"FEATURE 不完整:{f}"
        assert ":" not in f.title, f"title 不能含冒号:{f.title!r}"
    print(f"  [ok] FEATURE 契约完整:{[(f.key, f.title) for f in FEATURES]}")


def test_entry_is_thin() -> None:
    """插件入口只做装配(sys.path + register),不放业务逻辑。"""
    entry = (PROJECT / "plugin" / "zbtools" / "init.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in entry.splitlines() if not l.strip().startswith("#"))
    for banned in ("query_mesh3d", "set_transform", "show_note", "add_button"):
        assert banned not in body, f"入口不该出现业务逻辑:{banned}"
    assert re.search(r"register\(\)\s*$", body.strip()), "入口应只调用 register()"
    print("  [ok] 入口只做装配(sys.path + register)")


def test_tooltip_budget() -> None:
    """悬停提示(官方 bubble help,参数名 info)必须短 —— 而且**不许靠 \\n 换行**。

    依据(两条都是官方的):
      * 气泡提示只有一行;官方示例里 33 处 ``info`` 中位 19 字、最长 37 字;
      * 官方明文警告 ZBrush 的 Python 字符串渲染不了反斜杠,**连 ``\\n`` / ``\\t`` 都受影响**
        (``sdk/docs/partials/api_zbrush_string_bug_backslash.html``),双转义/raw string 都没用。

    所以规则就是"写短":理由与实测写进各功能的 README,参数枚举交给【诊断信息】。
    这里用假 ZBrush 真的装配一遍,**检查实际传给 ZBrush 的字符串**(含面板里拼接出来的)。
    """
    sys.path.insert(0, str(PROJECT / "tests"))
    import fake_zbrush                                             # noqa: PLC0415
    fake = fake_zbrush.FakeCommands()
    fake_zbrush.install(fake)
    import zbtools.app as app                                      # noqa: PLC0415
    from zbtools.ui import INFO_MAX                                 # noqa: PLC0415

    app.register()
    assert fake.widget_infos, "一个控件的提示都没记录到(装配失败?)"
    bad_len = {p: i for p, i in fake.widget_infos.items()
               if len(i) > INFO_MAX and not p.endswith("----")}
    bad_nl = {p: i for p, i in fake.widget_infos.items() if "\n" in i or "\t" in i}
    assert not bad_len, ("提示超长(> %d 字):%s" %
                         (INFO_MAX, {p: f"{len(i)} 字:{i[:40]}…" for p, i in bad_len.items()}))
    assert not bad_nl, f"提示里带了换行/制表符(ZBrush 渲染不了):{list(bad_nl)}"
    longest = max(fake.widget_infos.items(), key=lambda kv: len(kv[1]))
    print(f"  [ok] {len(fake.widget_infos)} 条提示都在 {INFO_MAX} 字内、无换行"
          f"(最长 {len(longest[1])} 字:{longest[1]})")


def main() -> int:
    tests = [test_features_are_isolated, test_shared_layers_do_not_depend_on_features,
             test_shared_layers_do_not_reference_feature_paths,
             test_feature_contract, test_entry_is_thin, test_tooltip_budget]
    failed = 0
    for fn in tests:
        print(f"* {(fn.__doc__ or fn.__name__).strip().splitlines()[0]}")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
