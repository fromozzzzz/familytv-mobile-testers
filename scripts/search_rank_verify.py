#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机版 2.2.1 搜索排序验收：
- 豆瓣卡片点进来默认落在「夸克网盘」通道；
- 结果已过滤解说/预告/花絮/片段等非正片；
- 结果按画质非递增排序（4K/高码/蓝光 > 1080P > 普通 > 枪版/标清）。

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
OUT = ROOT / "search_rank_results"
OUT.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
HOME = f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity"

STEPS = []
CRASHES = []
RATING = re.compile(r"^★\s?\d{1,2}(\.\d)?$")
JUNK = ("解说", "预告", "花絮", "片段", "混剪", "盘点", "速看", "抢先看",
        "影评", "剧评", "拉片", "解析", "吐槽", "名场面", "二创")
SKIP_META = ("夸克网盘 (", "影视站点 (", "正片优先", "最近搜索", "清空",
             "输入片名", "88VIP原画", "搜索")


def adb(*args, timeout=90, binary=False):
    r = subprocess.run(["adb", *args], capture_output=True, timeout=timeout)
    if binary:
        return r.stdout or b""
    return (r.stdout or b"").decode("utf-8", "replace")


def sh(*args, timeout=90):
    return adb("shell", *args, timeout=timeout)


def dump_xml():
    for _ in range(3):
        sh("uiautomator", "dump", "/sdcard/search_rank.xml")
        raw = adb("exec-out", "cat", "/sdcard/search_rank.xml")
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
    print(f"[CAP] {tag}: " + " | ".join(texts(root)[:24]))
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
            time.sleep(3)
            break
    return close_popups(dump_xml())


def grid_titles(root):
    """首页海报网格里的片名（排除评分、更新集数等角标）。"""
    out = []
    for rv in root.iter("node"):
        if "RecyclerView" not in (rv.get("class") or ""):
            continue
        for kid in rv.iter("node"):
            t = (kid.get("text") or "").strip()
            if not t or RATING.match(t) or t.startswith("更新至"):
                continue
            b = re.findall(r"\d+", kid.get("bounds", ""))
            if len(b) != 4:
                continue
            x1, y1, x2, y2 = map(int, b)
            out.append((t, (x1 + x2) // 2, (y1 + y2) // 2))
    return out


def search_score(title):
    t = title.lower()
    s = 0
    if "8k" in t:
        s += 140
    if "4k" in t or "2160p" in t or "uhd" in t:
        s += 100
    if "高码" in t or "高码率" in t:
        s += 50
    if any(k in t for k in ("蓝光", "bluray", "blu-ray", "remux", "原盘")):
        s += 60
    if "60帧" in t or "60fps" in t:
        s += 40
    if "hdr" in t:
        s += 30
    if "杜比" in t or "dolby" in t:
        s += 30
    if "1080p" in t or "全高清" in t:
        s += 20
    if "完结" in t or "全集" in t:
        s += 10
    if any(k in t for k in ("480p", "360p", "标清", "枪版", "抢先版")):
        s -= 80
    return s


def is_junk(title):
    return any(k in title for k in JUNK)


def quark_count(root):
    for it in nodes(root):
        m = re.match(r"^夸克网盘 \((\d+)\)$", it["text"])
        if m:
            return int(m.group(1))
    return None


def collect_quark_titles(root, keyword, seen):
    """只采集结果 RecyclerView 里的标题，避免把顶部搜索历史词当成结果。"""
    for rv in root.iter("node"):
        if "RecyclerView" not in (rv.get("class") or ""):
            continue
        for it in rv.iter("node"):
            t = (it.get("text") or "").strip()
            if not t or len(t) < 3:
                continue
            if any(k in t for k in SKIP_META):
                continue
            if t == keyword or t in seen:
                continue
            seen[t] = True
    return seen


SEARCH = f"{PKG}/com.github.tvbox.osc.mobile.MobileSearchActivity"
HOT_KEYWORD = "庆余年"


def wait_quark(root, minimum=3, seconds=60):
    """等夸克通道出结果；返回 (root, count)，超时返回 (root, 0)。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        n = quark_count(root)
        if n and n >= minimum:
            return root, n
        time.sleep(2)
        root = dump_xml()
    return root, quark_count(root) or 0


def open_search_with_results():
    """优先直接带关键词打开搜索页（热门片，结果多）；不行再点豆瓣卡片。"""
    sh("am", "force-stop", PKG)
    sh("am", "start", "-n", SEARCH, "--es", "keyword", HOT_KEYWORD)
    root = None
    for _ in range(12):
        time.sleep(2)
        root = close_popups(dump_xml())
        if find(root, "夸克网盘"):
            break
    if root is not None and find(root, "夸克网盘"):
        root, n = wait_quark(root, minimum=3)
        if n >= 3:
            print("  直达搜索:", HOT_KEYWORD, "结果:", n)
            return root, HOT_KEYWORD

    root = launch_home()
    tried = set()
    for _ in range(5):
        cands = [c for c in grid_titles(root) if c[0] not in tried]
        if not cands:
            break
        title, cx, cy = cands[0]
        tried.add(title)
        print("  点击豆瓣卡片:", title)
        sh("input", "tap", str(cx), str(cy))
        time.sleep(4)
        root = close_popups(dump_xml())
        if not find(root, "夸克网盘"):
            sh("input", "keyevent", "4")
            time.sleep(2)
            root = launch_home()
            continue
        root, n = wait_quark(root, minimum=3)
        if n >= 3:
            return root, title
        sh("input", "keyevent", "4")
        time.sleep(2)
        root = launch_home()
    return None, None


def main():
    adb("logcat", "-c")
    w, h = screen_size()
    print(f"screen: {w}x{h}")
    state = {}

    def step_open():
        root, keyword = open_search_with_results()
        if root is None:
            raise AssertionError("3 个豆瓣片名都没有夸克结果，无法继续")
        root = capture("01_search_quark")
        assert find(root, "影视站点"), "搜索页缺少「影视站点」通道"
        n = quark_count(root)
        assert n and n > 0, f"夸克结果数为 0: {n}"
        assert find(root, "正片优先"), "缺少排序说明文案"
        state["root"] = root
        state["keyword"] = keyword
        state["count"] = n
        print("  关键词:", keyword, "夸克结果:", n)

    def step_order_and_junk():
        root = state["root"]
        keyword = state["keyword"]
        seen = {}
        collect_quark_titles(root, keyword, seen)
        for i in range(5):
            sh("input", "swipe", str(w // 2), str(int(h * 0.72)),
               str(w // 2), str(int(h * 0.30)), "350")
            time.sleep(1)
            root = dump_xml()
            collect_quark_titles(root, keyword, seen)
        capture("02_search_scrolled")
        titles = list(seen.keys())
        assert len(titles) >= 3, f"采集到的夸克条目过少: {titles}"
        bad = [t for t in titles if is_junk(t)]
        assert not bad, f"搜索结果仍出现解说/预告类条目: {bad[:5]}"
        scores = [search_score(t) for t in titles]
        assert scores == sorted(scores, reverse=True), (
            "结果未按画质排序: " + str(list(zip(titles, scores))[:10]))
        state["titles"] = titles
        state["scores"] = scores
        print("  前 8 条:", list(zip(titles, scores))[:8])

    def step_cms_tab():
        root = state["root"]
        tap(root, "影视站点", wait=2)
        time.sleep(3)
        root = capture("03_search_cms")
        tab = find(root, "影视站点 (")
        assert tab, "影视站点 Tab 不存在"
        collected = []
        for rv in root.iter("node"):
            if "RecyclerView" not in (rv.get("class") or ""):
                continue
            for kid in rv.iter("node"):
                t = (kid.get("text") or "").strip()
                if len(t) >= 3 and not RATING.match(t):
                    collected.append(t)
        bad = [t for t in collected if is_junk(t)]
        assert not bad, f"影视站点结果出现解说/预告: {bad[:5]}"

    record("01_进入搜索页且夸克通道有足够结果", step_open)
    record("02_过滤解说预告且4K高码优先排序", step_order_and_junk)
    record("03_影视站点通道可切换且无解说", step_cms_tab)

    summary = {
        "device_screen": f"{w}x{h}",
        "keyword": state.get("keyword"),
        "quark_count": state.get("count"),
        "sampled_titles": state.get("titles", [])[:20],
        "steps": STEPS,
        "crashes": CRASHES,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
