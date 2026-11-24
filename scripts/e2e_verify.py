#!/usr/bin/env python3
"""真实 Android 模拟器端到端 UI + 网络 + 折叠屏自适应自动化验收脚本。"""
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "e2e_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
CRASHES = []
STEPS = []


def adb(*args, check=False, timeout=60):
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args} failed: {r.stderr}")
    return r.stdout


def capture(tag):
    """复用 mobile-agent-testers 的 screen.py 做截图 + 控件解析 + a11y 无障碍检测 + 崩溃采集。"""
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "android" / "screen.py"), str(OUT_DIR), tag],
        capture_output=True, text=True, timeout=60
    )
    out = p.stdout + ("\n" + p.stderr if p.stderr else "")
    (OUT_DIR / f"{tag}.log.txt").write_text(out, encoding="utf-8")
    print(f"\n=== SCREEN {tag} ===\n{out}")
    if "FATAL EXCEPTION" in out or "UnsatisfiedLinkError" in out or "ERRORS SINCE LAST SCREENSHOT" in out:
        for line in out.splitlines():
            if any(k in line for k in ("FATAL", "Exception", "Error", "UnsatisfiedLinkError")):
                CRASHES.append(f"[{tag}] {line}")
    return dump_xml()


def dump_xml():
    adb("shell", "uiautomator", "dump", "/sdcard/uidump.xml")
    raw = adb("exec-out", "cat", "/sdcard/uidump.xml")
    idx = raw.find("<")
    if idx < 0:
        return ET.Element("hierarchy")
    try:
        return ET.fromstring(raw[idx:])
    except ET.ParseError:
        return ET.Element("hierarchy")


def iter_nodes(root):
    for n in root.iter("node"):
        b = re.findall(r"\d+", n.get("bounds", ""))
        if len(b) != 4:
            continue
        x1, y1, x2, y2 = map(int, b)
        yield {
            "node": n,
            "text": (n.get("text") or "").strip(),
            "desc": (n.get("content-desc") or "").strip(),
            "cls": n.get("class") or "",
            "clickable": n.get("clickable") == "true",
            "checked": n.get("checked") == "true",
            "bounds": (x1, y1, x2, y2),
            "cx": (x1 + x2) // 2,
            "cy": (y1 + y2) // 2,
        }


def find_node(root, substring, exact=False, cls_filter=None):
    for item in iter_nodes(root):
        if cls_filter and cls_filter not in item["cls"]:
            continue
        label = item["text"] or item["desc"]
        if not label:
            continue
        if (exact and label == substring) or (not exact and substring in label):
            return item
    return None


def tap_node(root, substring, exact=False, cls_filter=None, wait=2.0):
    item = find_node(root, substring, exact=exact, cls_filter=cls_filter)
    if not item:
        raise AssertionError(f"未找到可点击元素: {substring} (cls={cls_filter})")
    adb("shell", "input", "tap", str(item["cx"]), str(item["cy"]))
    time.sleep(wait)
    return item


def assert_text(root, substring, present=True):
    item = find_node(root, substring, exact=False)
    if present and not item:
        all_texts = [x["text"] or x["desc"] for x in iter_nodes(root) if (x["text"] or x["desc"])]
        raise AssertionError(f"断言失败：页面未包含 '{substring}'。当前文本={all_texts[:30]}")
    if not present and item:
        raise AssertionError(f"断言失败：页面不应出现 '{substring}'，但出现了")


def record(name, fn):
    try:
        fn()
        STEPS.append({"step": name, "ok": True})
        print(f"[PASS] {name}")
    except Exception as e:
        STEPS.append({"step": name, "ok": False, "error": str(e)})
        print(f"[FAIL] {name}: {e}")


def main():
    adb("logcat", "-c")
    # 关闭输入法弹窗干扰，授予基础权限
    adb("shell", "am", "start", "-n", f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity")

    def step_home():
        root = None
        for _ in range(10):
            time.sleep(2)
            root = dump_xml()
            if find_node(root, "家庭影视") and find_node(root, "电影") or find_node(root, "搜片"):
                break
        root = capture("01_home_movies")
        for tab in ("影视", "夸克", "直播", "设置", "搜片"):
            assert_text(root, tab, True)

    def step_quark_default_hidden():
        root = dump_xml()
        tap_node(root, "夸克", exact=True, wait=2)
        root = capture("02_quark_default_hidden")
        assert_text(root, "默认已隐藏", True)
        assert_text(root, "TVBox_Cache", True)
        assert_text(root, "TVBox_MyVideos", True)

    def step_settings_toggle():
        root = dump_xml()
        tap_node(root, "设置", exact=True, wait=2)
        root = capture("03_settings_default")
        sw_personal = find_node(root, "显示我的夸克视频", cls_filter="Switch")
        sw_clean = find_node(root, "播完 24 小时后次日自动清理", cls_filter="Switch")
        assert sw_personal and not sw_personal["checked"], f"个人视频开关应默认关闭: {sw_personal}"
        assert sw_clean and sw_clean["checked"], f"24h 清理开关应默认开启: {sw_clean}"

        # 开启个人视频开关并回夸克 Tab 检验切换效果
        adb("shell", "input", "tap", str(sw_personal["cx"]), str(sw_personal["cy"]))
        time.sleep(1.5)
        tap_node(dump_xml(), "夸克", exact=True, wait=2)
        root = capture("04_quark_personal_enabled")
        assert_text(root, "默认已隐藏", False)
        assert_text(root, "TVBox_MyVideos", True)

        # 还原关闭
        tap_node(root, "设置", exact=True, wait=1.5)
        sw2 = find_node(dump_xml(), "显示我的夸克视频", cls_filter="Switch")
        if sw2 and sw2["checked"]:
            adb("shell", "input", "tap", str(sw2["cx"]), str(sw2["cy"]))
            time.sleep(1)

    def step_live():
        tap_node(dump_xml(), "直播", exact=True, wait=2)
        root = capture("05_live_tab")
        assert_text(root, "电视频道直播", True)
        tap_node(root, "立即观看", wait=5)
        root = capture("06_live_play_portrait")
        assert_text(root, "全屏横屏", True)
        tap_node(root, "全屏横屏", wait=2)
        capture("07_live_fullscreen")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.5)

    def step_search_pansou():
        adb("shell", "am", "start", "-n", f"{PKG}/com.github.tvbox.osc.mobile.MobileSearchActivity", "--es", "keyword", "庆余年")
        root = None
        for _ in range(10):
            time.sleep(2)
            root = dump_xml()
            n = find_node(root, "夸克网盘 (")
            if n and "(0)" not in n["text"]:
                break
        root = capture("08_search_pansou_quark")
        item = find_node(root, "夸克网盘 · 88VIP原画")
        assert item is not None, "未检索到 PanSou 公开夸克网盘结果"
        adb("shell", "input", "tap", str(item["cx"]), str(item["cy"]))
        time.sleep(3)
        capture("09_quark_share_detail")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)

    def step_detail_and_player():
        adb("shell", "am", "start", "-n", f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity")
        time.sleep(3)
        tap_node(dump_xml(), "影视", exact=True, wait=5)
        root = dump_xml()
        card = None
        for rv in root.iter("node"):
            if "RecyclerView" in (rv.get("class") or ""):
                children = list(rv)
                if children:
                    b = re.findall(r"\d+", children[0].get("bounds", ""))
                    if len(b) == 4:
                        x1, y1, x2, y2 = map(int, b)
                        if x2 > x1 and y2 > y1:
                            card = {"cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2}
                            break
        assert card is not None, "首页未找到可点击影视卡片"
        adb("shell", "input", "tap", str(card["cx"]), str(card["cy"]))
        time.sleep(5)
        root = capture("10_movie_detail")
        play_btn = find_node(root, "播放")
        assert play_btn is not None, "详情页未加载出播放按钮"
        adb("shell", "input", "tap", str(play_btn["cx"]), str(play_btn["cy"]))
        time.sleep(5)
        root = capture("11_play_portrait")
        assert_text(root, "全屏", True)
        tap_node(root, "全屏", exact=True, wait=3)
        capture("12_play_landscape")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)

    def step_foldable_unfolded():
        try:
            adb("shell", "wm", "size", "1800x2000")
            adb("shell", "wm", "density", "420")
            adb("shell", "am", "start", "-n", f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity")
            time.sleep(4)
            capture("13_foldable_unfolded_movies")
        finally:
            adb("shell", "wm", "size", "reset")
            adb("shell", "wm", "density", "reset")

    record("01_首页加载Liqian与四Tab", step_home)
    record("02_夸克个人目录默认隐藏", step_quark_default_hidden)
    record("03_设置页开关状态与联动切换", step_settings_toggle)
    record("04_电视直播竖屏与全屏", step_live)
    record("05_PanSou公开夸克盘搜与分享详情", step_search_pansou)
    record("06_影视详情与路线B半屏全屏播放", step_detail_and_player)
    record("07_华为折叠屏展开态自适应布局", step_foldable_unfolded)

    summary = {"steps": STEPS, "crashes": CRASHES}
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
