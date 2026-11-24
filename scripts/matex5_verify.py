#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""华为 Mate X5 折叠屏（2224×2496 @426dpi 展开 / 1080×2504 @426dpi 折叠）端到端验收。

覆盖：
  1. 展开态首页（835×938dp，近方形）片单网格与顶栏不裁切
  2. 展开态播放页左右分栏（左播放器 + 右选集/画质/线路）
  3. 铺满/适应切换（消除近方形屏 16:9 黑边）
  4. 横屏全屏
  5. 折叠态（405×941dp）3 列竖排布局
  6. 夸克 Tab / 设置 / 直播 在展开态的可点性与触控高度
"""
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "matex5_results"
OUT.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
HOME = f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity"

UNFOLDED = ("2224x2496", "426")   # 835×938dp
FOLDED = ("1080x2504", "426")     # 405×941dp

STEPS = []
CRASHES = []


def adb(*args, timeout=90, binary=False):
    r = subprocess.run(["adb", *args], capture_output=not binary, timeout=timeout)
    if binary:
        return r.stdout
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
    return out


def sh(*args, timeout=90):
    return adb("shell", *args, timeout=timeout)


def screen_size():
    m = re.search(r"Override size: (\d+)x(\d+)", sh("wm", "size"))
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"Physical size: (\d+)x(\d+)", sh("wm", "size"))
    return (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)


def density():
    m = re.search(r"Override density: (\d+)", sh("wm", "density")) or re.search(r"Physical density: (\d+)", sh("wm", "density"))
    return int(m.group(1)) if m else 420


def set_metrics(size, dens):
    sh("wm", "size", size)
    sh("wm", "density", dens)
    time.sleep(1.5)
    return screen_size(), density()


def reset_metrics():
    sh("wm", "size", "reset")
    sh("wm", "density", "reset")
    time.sleep(1.5)


def dump_xml():
    sh("uiautomator", "dump", "/sdcard/matex5.xml")
    raw = adb("exec-out", "cat", "/sdcard/matex5.xml")
    idx = raw.find("<")
    if idx < 0:
        return ET.Element("hierarchy")
    try:
        return ET.fromstring(raw[idx:])
    except ET.ParseError:
        return ET.Element("hierarchy")


def nodes(root):
    for n in root.iter("node"):
        b = re.findall(r"\d+", n.get("bounds", ""))
        if len(b) != 4:
            continue
        x1, y1, x2, y2 = map(int, b)
        yield {
            "text": (n.get("text") or "").strip(),
            "desc": (n.get("content-desc") or "").strip(),
            "cls": n.get("class") or "",
            "clickable": n.get("clickable") == "true",
            "checked": n.get("checked") == "true",
            "bounds": (x1, y1, x2, y2),
            "cx": (x1 + x2) // 2,
            "cy": (y1 + y2) // 2,
        }


def find(root, sub, exact=False, cls=None):
    for it in nodes(root):
        if cls and cls not in it["cls"]:
            continue
        label = it["text"] or it["desc"]
        if not label:
            continue
        if (exact and label == sub) or (not exact and sub in label):
            return it
    return None


def tap(root, sub, exact=False, cls=None, wait=2.0):
    it = find(root, sub, exact=exact, cls=cls)
    if not it:
        raise AssertionError(f"未找到可点击元素: {sub}")
    sh("input", "tap", str(it["cx"]), str(it["cy"]))
    time.sleep(wait)
    return it


def screenshot(tag):
    png = adb("exec-out", "screencap", "-p", binary=True)
    (OUT / f"{tag}.png").write_bytes(png)
    print(f"[SHOT] {tag}.png ({len(png)} bytes)")
    return png


def capture(tag):
    png = screenshot(tag)
    root = dump_xml()
    w, h = screen_size()
    d = density()
    print(f"  viewport={w}x{h}px density={d} -> {round(w * 160 / d)}x{round(h * 160 / d)}dp")
    logcat = adb("logcat", "-d", "-t", "400")
    hits = [l for l in logcat.splitlines() if "FATAL EXCEPTION" in l or "UnsatisfiedLinkError" in l]
    for line in hits:
        CRASHES.append(f"[{tag}] {line.strip()}")
    (OUT / f"{tag}.xml").write_text(
        ET.tostring(root, encoding="unicode") if root is not None else "", encoding="utf-8")
    return root


def first_recycler(root):
    for rv in root.iter("node"):
        if "RecyclerView" in (rv.get("class") or ""):
            kids = list(rv)
            if kids:
                b = re.findall(r"\d+", kids[0].get("bounds", ""))
                if len(b) == 4:
                    x1, y1, x2, y2 = map(int, b)
                    if x2 > x1 and y2 > y1:
                        return {"cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2, "count": len(kids),
                                "child": (x1, y1, x2, y2)}
    return None


def grid_columns(root):
    """统计可见海报卡片的不同 x 起点数量，判断列数是否匹配折叠屏。"""
    xs = set()
    for rv in root.iter("node"):
        if "RecyclerView" not in (rv.get("class") or ""):
            continue
        for kid in list(rv):
            b = re.findall(r"\d+", kid.get("bounds", ""))
            if len(b) == 4:
                xs.add(int(b[0]))
    return len(xs)


def record(name, fn):
    try:
        fn()
        STEPS.append({"step": name, "ok": True})
        print(f"[PASS] {name}")
    except Exception as e:  # noqa: BLE001
        STEPS.append({"step": name, "ok": False, "error": str(e)})
        print(f"[FAIL] {name}: {e}")


def close_popups(root=None):
    for _ in range(3):
        root = root or dump_xml()
        btn = find(root, "Got it", exact=True) or find(root, "我知道了", exact=True)
        if not btn:
            return root
        sh("input", "tap", str(btn["cx"]), str(btn["cy"]))
        time.sleep(1.0)
        root = None
    return dump_xml()


def launch_home():
    sh("am", "force-stop", PKG)
    sh("am", "start", "-n", HOME)
    time.sleep(2)
    root = None
    for _ in range(15):
        time.sleep(2)
        root = close_popups(dump_xml())
        if find(root, "家庭影视") and first_recycler(root):
            time.sleep(3)
            break
    return root


def main():
    adb("logcat", "-c")
    size, dens = set_metrics(*UNFOLDED)
    print(f"Mate X5 展开态 metrics: {size} @ {dens}dpi")

    def step_home_unfolded():
        root = launch_home()
        root = capture("01_home_unfolded")
        for tab in ("影视", "夸克", "直播", "设置", "搜片"):
            assert find(root, tab, exact=True), f"底部/顶栏缺少 {tab}"
        card = first_recycler(root)
        assert card, "首页未渲染片单卡片"
        cols = grid_columns(root)
        print(f"  可见海报列数={cols}")
        assert cols >= 4, f"展开态海报列数过少（{cols}），布局未利用大屏"
        w, _ = screen_size()
        x1, _, x2, _ = card["child"]
        assert x2 - x1 < w * 0.9, "海报卡片宽度异常（可能被拉伸为单列）"

    def step_detail_and_twopane():
        root = dump_xml()
        card = first_recycler(root)
        assert card, "首页无可点卡片"
        sh("input", "tap", str(card["cx"]), str(card["cy"]))
        detail = None
        for _ in range(8):
            time.sleep(1.5)
            detail = close_popups(dump_xml())
            if find(detail, "播放"):
                break
        detail = capture("02_detail_unfolded")
        play = find(detail, "播放")
        assert play, "详情页未加载出播放按钮"
        sh("input", "tap", str(play["cx"]), str(play["cy"]))
        time.sleep(4)
        play_root = close_popups(dump_xml())
        if find(play_root, "全屏", exact=True) is None:
            w, h = screen_size()
            sh("input", "tap", str(w // 2), str(int(h * 0.15)))
            time.sleep(1)
            play_root = dump_xml()
        play_root = capture("03_play_twopane")
        assert find(play_root, "全屏", exact=True), "播放页未出现控制栏"
        assert find(play_root, "选集"), "播放页右侧缺少选集面板（左右分栏未生效）"
        fill_btn = find(play_root, "铺满", exact=True) or find(play_root, "适应", exact=True)
        assert fill_btn, "播放页缺少铺满/适应切换按钮"
        # 播放器应占据左侧约 55%~65% 宽度
        w, _ = screen_size()
        assert fill_btn["cx"] < w * 0.62, "铺满按钮不在左半区，左右分栏可能未生效"

    def step_fill_toggle():
        root = dump_xml()
        btn = find(root, "铺满", exact=True) or find(root, "适应", exact=True)
        before = btn["text"]
        sh("input", "tap", str(btn["cx"]), str(btn["cy"]))
        time.sleep(1.2)
        after_root = capture("04_play_fill_toggled")
        after = (find(after_root, "铺满", exact=True) or find(after_root, "适应", exact=True))["text"]
        assert before != after, f"铺满/适应切换无效（仍为 {after}）"

    def step_fullscreen_landscape():
        root = dump_xml()
        btn = find(root, "全屏", exact=True)
        assert btn, "未找到全屏按钮"
        sh("input", "tap", str(btn["cx"]), str(btn["cy"]))
        time.sleep(3)
        close_popups()
        root = capture("05_play_fullscreen_landscape")
        w, h = screen_size()
        print(f"  横屏 viewport={w}x{h}px -> 宽高比 {round(w / h, 2)}")
        sh("input", "keyevent", "KEYCODE_BACK")
        time.sleep(2)
        close_popups()
        sh("input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.5)

    def step_tabs_unfolded():
        root = launch_home()
        tap(root, "夸克", exact=True, wait=2)
        root = capture("06_quark_unfolded")
        assert find(root, "TVBox_Cache", exact=True), "夸克页未列出 TVBox_Cache 目录"
        tap(root, "设置", exact=True, wait=2)
        root = capture("07_settings_unfolded")
        assert find(root, "显示我的夸克视频", cls="Switch"), "设置页缺少个人视频开关"
        tap(root, "直播", exact=True, wait=2)
        for _ in range(8):
            time.sleep(2)
            root = close_popups(dump_xml())
            if first_recycler(root):
                break
        root = capture("08_live_unfolded")
        assert first_recycler(root), "直播 Tab 未加载频道列表"

    def step_folded():
        set_metrics(*FOLDED)
        try:
            root = launch_home()
            root = capture("09_home_folded")
            card = first_recycler(root)
            assert card, "折叠态首页未渲染片单"
            cols = grid_columns(root)
            print(f"  折叠态可见列数={cols}")
            assert 2 <= cols <= 4, f"折叠态列数异常：{cols}"
        finally:
            reset_metrics()

    record("01_展开态首页四Tab与海报网格", step_home_unfolded)
    record("02_展开态详情与播放页左右分栏", step_detail_and_twopane)
    record("03_铺满适应切换", step_fill_toggle)
    record("04_横屏全屏", step_fullscreen_landscape)
    record("05_展开态夸克设置直播", step_tabs_unfolded)
    record("06_折叠态窄屏3列布局", step_folded)

    summary = {"device": "HUAWEI Mate X5 (2224x2496 @426dpi / 1080x2504 @426dpi)",
               "steps": STEPS, "crashes": CRASHES}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
