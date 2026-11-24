#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机版 2.2.0 首页验收：底部三栏（影视/直播/设置，无夸克）+ 顶部三入口
（豆瓣推荐/收藏/历史）+ 仅三个豆瓣分类 + 无资源站胶囊。

在 GitHub Actions 的 Pixel 6 / Android 13 模拟器上运行。
"""
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "home_v2_results"
OUT.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
HOME = f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity"

STEPS = []
CRASHES = []
RATING = re.compile(r"^★\s?\d{1,2}(\.\d)?$")
JUNK = ("解说", "预告", "花絮", "片段", "混剪", "盘点", "速看")
SITE_NAMES = ("非凡资源", "非凡资源1", "量子资源", "量子资源2", "暴风资源", "暴风资源2")
DOUBAN_ALL = ("热门电影", "热门剧集", "豆瓣高分", "一周口碑", "国产剧", "美剧",
              "韩剧", "日剧", "动漫", "综艺")


def adb(*args, timeout=90, binary=False):
    r = subprocess.run(["adb", *args], capture_output=True, timeout=timeout)
    if binary:
        return r.stdout or b""
    return (r.stdout or b"").decode("utf-8", "replace")


def sh(*args, timeout=90):
    return adb("shell", *args, timeout=timeout)


def dump_xml():
    for _ in range(3):
        sh("uiautomator", "dump", "/sdcard/homev2.xml")
        raw = adb("exec-out", "cat", "/sdcard/homev2.xml")
        idx = raw.find("<")
        if idx < 0:
            time.sleep(1)
            continue
        try:
            return ET.fromstring(raw[idx:])
        except ET.ParseError:
            time.sleep(1)
    return ET.Element("hierarchy")


def nodes(root):
    for n in root.iter("node"):
        b = re.findall(r"\d+", n.get("bounds", ""))
        if len(b) != 4:
            continue
        x1, y1, x2, y2 = map(int, b)
        yield {
            "text": (n.get("text") or "").strip(),
            "cls": n.get("class") or "",
            "bounds": (x1, y1, x2, y2),
            "cx": (x1 + x2) // 2,
            "cy": (y1 + y2) // 2,
        }


def texts(root):
    return [it["text"] for it in nodes(root) if it["text"]]


def find(root, sub, exact=False):
    for it in nodes(root):
        if not it["text"]:
            continue
        if (exact and it["text"] == sub) or (not exact and sub in it["text"]):
            return it
    return None


def tap(root, sub, exact=False, wait=2.0):
    it = find(root, sub, exact=exact)
    if not it:
        raise AssertionError(f"未找到元素: {sub}")
    sh("input", "tap", str(it["cx"]), str(it["cy"]))
    time.sleep(wait)
    return it


def screen_size():
    out = sh("wm", "size")
    m = re.search(r"(\d+)x(\d+)", out)
    if not m:
        return (1080, 2400)
    return (int(m.group(1)), int(m.group(2)))


def capture(tag):
    (OUT / f"{tag}.png").write_bytes(adb("exec-out", "screencap", "-p", binary=True))
    root = dump_xml()
    (OUT / f"{tag}.xml").write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    print(f"[CAP] {tag}: " + " | ".join(texts(root)[:20]))
    for line in adb("logcat", "-d", "-t", "800").splitlines():
        if "FATAL EXCEPTION" in line:
            msg = line.strip()
            if msg not in CRASHES:
                CRASHES.append(f"[{tag}] {msg}")
    return root


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
        b = find(root, "Got it", exact=True) or find(root, "我知道了", exact=True)
        if not b:
            return root
        sh("input", "tap", str(b["cx"]), str(b["cy"]))
        time.sleep(1)
        root = None
    return dump_xml()


def launch_home():
    sh("am", "force-stop", PKG)
    sh("am", "start", "-n", HOME)
    root = None
    for _ in range(18):
        time.sleep(2)
        root = close_popups(dump_xml())
        if find(root, "豆瓣推荐"):
            time.sleep(4)
            break
    return close_popups(dump_xml())


def grid_texts(root):
    out = []
    for rv in root.iter("node"):
        if "RecyclerView" not in (rv.get("class") or ""):
            continue
        for kid in rv.iter("node"):
            t = (kid.get("text") or "").strip()
            if t:
                out.append(t)
    return out


def wait_grid(root, minimum=8, tries=12):
    for _ in range(tries):
        if len(grid_texts(root)) >= minimum:
            return root
        time.sleep(1.5)
        root = dump_xml()
    return root


def main():
    adb("logcat", "-c")
    w, h = screen_size()
    print(f"screen: {w}x{h}")
    root = None

    def bottom_texts(r):
        return [it["text"] for it in nodes(r)
                if it["text"] and it["cy"] > h * 0.85]

    def step_bottom_nav():
        nonlocal root
        root = launch_home()
        root = capture("01_home_douban")
        bottom = bottom_texts(root)
        assert "影视" in bottom, f"底部缺少「影视」: {bottom}"
        assert "直播" in bottom, f"底部缺少「直播」: {bottom}"
        assert "设置" in bottom, f"底部缺少「设置」: {bottom}"
        assert "夸克" not in bottom, f"底栏仍有「夸克」: {bottom}"

    def step_home_tabs():
        nonlocal root
        for tab in ("豆瓣推荐", "收藏", "历史"):
            assert find(root, tab, exact=True), f"顶部缺少入口 {tab}"
        for site in SITE_NAMES:
            assert not find(root, site, exact=True), f"首页仍出现资源站 {site}"
        assert find(root, "家庭影视"), "缺少应用标题"

    def step_categories():
        nonlocal root
        for _ in range(12):
            if find(root, "热门电影", exact=True):
                break
            time.sleep(1.5)
            root = dump_xml()
        for chip in ("热门电影", "热门剧集", "豆瓣高分"):
            assert find(root, chip, exact=True), f"缺少豆瓣分类 {chip}"
        for hidden in DOUBAN_ALL[3:]:
            assert not find(root, hidden, exact=True), f"仍显示多余分类 {hidden}"
        assert not find(root, "一周口碑", exact=True), "仍显示「一周口碑」"

    def step_grid():
        nonlocal root
        root = wait_grid(root, minimum=10)
        root = capture("02_douban_grid")
        items = grid_texts(root)
        assert len(items) >= 10, f"豆瓣网格内容过少: {items[:10]}"
        rated = [t for t in items if RATING.match(t)]
        assert rated, f"未看到豆瓣评分角标: {items[:12]}"
        bad = [t for t in items if any(k in t for k in JUNK)]
        assert not bad, f"首页出现解说/预告类条目: {bad[:5]}"
        print("  评分角标:", rated[:6])

    def step_collect():
        nonlocal root
        tap(root, "收藏", exact=True, wait=2)
        time.sleep(2)
        root = capture("03_collect_tab")
        assert find(root, "暂无收藏") or find(root, "共 "), "收藏页无内容也无空态"
        assert not find(root, "热门电影", exact=True), "收藏页仍显示豆瓣分类"
        assert not find(root, "豆瓣高分", exact=True), "收藏页仍显示豆瓣分类"

    def step_history():
        nonlocal root
        tap(root, "历史", exact=True, wait=2)
        time.sleep(2)
        root = capture("04_history_tab")
        assert find(root, "暂无观看记录") or find(root, "共 "), "历史页无内容也无空态"
        assert not find(root, "热门电影", exact=True), "历史页仍显示豆瓣分类"

    def step_back_to_douban():
        nonlocal root
        tap(root, "豆瓣推荐", exact=True, wait=2)
        root = wait_grid(dump_xml(), minimum=8)
        root = capture("05_back_to_douban")
        assert find(root, "热门电影", exact=True), "切回豆瓣推荐后分类未恢复"
        assert len(grid_texts(root)) >= 8, "切回豆瓣推荐后网格为空"

    def step_live_setting():
        nonlocal root
        tap(root, "直播", exact=True, wait=2)
        time.sleep(2)
        root = capture("06_live_tab")
        assert find(root, "电视频道直播"), "直播页未打开"
        tap(root, "设置", exact=True, wait=2)
        time.sleep(2)
        root = capture("07_setting_tab")
        assert find(root, "手机端设置"), "设置页未打开"
        assert find(root, "我的夸克视频"), "设置页缺少我的夸克视频入口"
        bottom = bottom_texts(root)
        assert "夸克" not in bottom, f"设置页底栏出现「夸克」: {bottom}"
        tap(root, "影视", exact=True, wait=2)
        time.sleep(2)
        root = dump_xml()
        assert find(root, "豆瓣推荐", exact=True), "返回影视页失败"

    def step_card_to_search():
        nonlocal root
        target = None
        for rv in root.iter("node"):
            if "RecyclerView" not in (rv.get("class") or ""):
                continue
            for kid in rv.iter("node"):
                t = (kid.get("text") or "").strip()
                if t and not RATING.match(t) and not t.startswith("更新至") and len(t) >= 2:
                    b = re.findall(r"\d+", kid.get("bounds", ""))
                    if len(b) == 4:
                        x1, y1, x2, y2 = map(int, b)
                        target = ((x1 + x2) // 2, (y1 + y2) // 2, t)
                        break
            if target:
                break
        assert target, "网格里没找到可点的影片标题"
        print("  点击:", target[2])
        sh("input", "tap", str(target[0]), str(target[1]))
        time.sleep(5)
        root = close_popups(dump_xml())
        root = capture("08_search_from_douban")
        assert find(root, target[2]) or find(root, "夸克网盘"), "点卡片后未进入搜索页"

    record("01_底部三栏为影视/直播/设置且无夸克", step_bottom_nav)
    record("02_顶部三入口为豆瓣推荐/收藏/历史且无资源站", step_home_tabs)
    record("03_豆瓣分类仅热门电影/热门剧集/豆瓣高分", step_categories)
    record("04_豆瓣网格与评分角标正常", step_grid)
    record("05_收藏入口内嵌列表", step_collect)
    record("06_历史入口内嵌列表", step_history)
    record("07_切回豆瓣推荐正常", step_back_to_douban)
    record("08_直播与设置页正常且底栏无夸克", step_live_setting)
    record("09_点豆瓣卡片进入搜索", step_card_to_search)

    summary = {"device_screen": f"{w}x{h}", "steps": STEPS, "crashes": CRASHES}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
