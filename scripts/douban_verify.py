#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机版首页「豆瓣优先」验收：豆瓣推荐默认展示 + 评分角标 + 点卡片跨站搜片 + 分类切换。

同时校验资源站「解说/预告」垃圾条目已被过滤。
"""
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "douban_results"
OUT.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
HOME = f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity"

STEPS = []
CRASHES = []
RATING = re.compile(r"^★\s?\d{1,2}(\.\d)?$")
JUNK = ("解说", "预告", "花絮", "片段", "混剪", "盘点", "速看")


def adb(*args, timeout=90, binary=False):
    r = subprocess.run(["adb", *args], capture_output=True, timeout=timeout)
    if binary:
        return r.stdout or b""
    return (r.stdout or b"").decode("utf-8", "replace")


def sh(*args, timeout=90):
    return adb("shell", *args, timeout=timeout)


def dump_xml():
    sh("uiautomator", "dump", "/sdcard/douban.xml")
    raw = adb("exec-out", "cat", "/sdcard/douban.xml")
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


def capture(tag):
    (OUT / f"{tag}.png").write_bytes(adb("exec-out", "screencap", "-p", binary=True))
    root = dump_xml()
    (OUT / f"{tag}.xml").write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    print(f"[CAP] {tag}: " + " | ".join(texts(root)[:18]))
    for line in adb("logcat", "-d", "-t", "600").splitlines():
        if "FATAL EXCEPTION" in line:
            CRASHES.append(f"[{tag}] {line.strip()}")
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
    for _ in range(15):
        time.sleep(2)
        root = close_popups(dump_xml())
        if find(root, "豆瓣推荐") or find(root, "家庭影视"):
            time.sleep(4)
            break
    return close_popups(dump_xml())


def grid_texts(root):
    """海报网格里的角标/标题文本（RecyclerView 子节点）。"""
    out = []
    for rv in root.iter("node"):
        if "RecyclerView" not in (rv.get("class") or ""):
            continue
        for kid in rv.iter("node"):
            t = (kid.get("text") or "").strip()
            if t:
                out.append(t)
    return out


def main():
    adb("logcat", "-c")
    root = None

    def step_douban_default():
        nonlocal root
        root = launch_home()
        for _ in range(10):
            if find(root, "热门电影"):
                break
            time.sleep(2)
            root = dump_xml()
        root = capture("01_home_douban")
        assert find(root, "豆瓣推荐"), "首页缺少「豆瓣推荐」入口"
        for chip in ("热门电影", "热门剧集", "豆瓣高分"):
            assert find(root, chip, exact=True), f"缺少豆瓣分类 {chip}"
        items = grid_texts(root)
        assert len(items) >= 10, f"豆瓣网格内容过少: {items[:10]}"
        rated = [t for t in items if RATING.match(t)]
        assert rated, f"未看到豆瓣评分角标: {items[:12]}"
        print("  评分角标:", rated[:6])

    def step_no_junk():
        nonlocal root
        items = grid_texts(root)
        bad = [t for t in items if any(k in t for k in JUNK)]
        assert not bad, f"首页仍出现解说/预告类垃圾条目: {bad[:5]}"

    def step_switch_section():
        nonlocal root
        root = dump_xml()
        tap(root, "热门剧集", exact=True, wait=2)
        for _ in range(10):
            time.sleep(1.5)
            root = dump_xml()
            if len(grid_texts(root)) >= 8:
                break
        root = capture("02_section_tv_hot")
        items = grid_texts(root)
        assert len(items) >= 8, f"热门剧集未加载: {items[:8]}"

    def step_tap_to_search():
        nonlocal root
        # 找网格里第一个「标题」节点（非评分角标）点击
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
        root = capture("03_search_from_douban")
        assert find(root, target[2]) or find(root, "夸克"), "点击豆瓣卡片后未进入搜索页"
        assert find(root, "站点") or find(root, "夸克"), "搜索页缺少结果标签"

    record("01_首页默认豆瓣推荐与评分角标", step_douban_default)
    record("02_首页无解说预告垃圾条目", step_no_junk)
    record("03_切换豆瓣分类(热门剧集)", step_switch_section)
    record("04_点豆瓣卡片跨站搜片", step_tap_to_search)

    summary = {"steps": STEPS, "crashes": CRASHES}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()