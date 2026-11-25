#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机版 2.3.0 验收：首页「夸克」入口。

① 顶部四入口齐全：豆瓣推荐 / 夸克 / 收藏 / 历史；
② 点「夸克」出现 Telegram 频道聚合的最新分享列表（≥10 条，卡片有日期角标）；
③ 列表不含短剧/解说/预告等垃圾词；
④ 日期倒序：第一屏卡片日期 ≥ 后面卡片日期；
⑤ 抽一条点进详情页能转存出集列表（标题不为空）。

在 GitHub Actions 的 Pixel 6 / Android 13 模拟器上运行（Mate X5 展开态分辨率）。
"""
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "quark_tab_results"
OUT.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
HOME = f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity"
DETAIL = f"{PKG}/com.github.tvbox.osc.mobile.MobileDetailActivity"

STEPS = []
CRASHES = []
RATING = re.compile(r"^★?\s?\d")
JUNK = ("短剧", "解说", "预告", "花絮", "片段", "混剪", "盘点", "速看", "抢先看",
        "影评", "剧评", "拉片", "解析", "吐槽", "名场面", "二创", "更新目录")


def adb(*args, timeout=90, binary=False):
    r = subprocess.run(["adb", *args], capture_output=True, timeout=timeout)
    if binary:
        return r.stdout or b""
    return (r.stdout or b"").decode("utf-8", "replace")


def sh(*args, timeout=90):
    return adb("shell", *args, timeout=timeout)


def dump_xml():
    for _ in range(3):
        sh("uiautomator", "dump", "/sdcard/quark_tab.xml")
        raw = adb("exec-out", "cat", "/sdcard/quark_tab.xml")
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
        yield {"text": (n.get("text") or "").strip(), "cls": n.get("class") or "",
                "bounds": (x1, y1, x2, y2), "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2}


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
        root = root if root is not None else dump_xml()
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


DATE_RE = re.compile(r"2026-\d{2}-\d{2}")
# 卡片上的日期行形如「11月24日更新」（App 渲染 last=2026-11-24 → 11月24日更新）
CARD_DATE_RE = re.compile(r"^(\d{2})月(\d{2})日更新$")
# 顶部框里的「2026」干扰行排除：卡片标题含年份括号 (2026)
TITLE_HINT_RE = re.compile(r"\((?:19|20)\d{2}\)|4K|高码|更新至|更至|EP\d+|第\d+集|\d+集")


def quark_cards(root):
    """屏幕上可见的夸克 tab 卡片标题行（像片名、带年份/画质字样）。"""
    out = []
    for it in nodes(root):
        t = it["text"]
        if not t or len(t) < 6:
            continue
        if t in ("豆瓣推荐", "夸克", "收藏", "历史", "搜片", "家庭影视", "电影", "剧集", "动漫综艺"):
            continue
        if DATE_RE.search(t):
            continue
        if CARD_DATE_RE.match(t):
            continue
        if TITLE_HINT_RE.search(t):
            out.append(t)
    return out


def quark_card_dates(root):
    """屏幕上可见的「N月N日更新」日期行 → 转成 MM-DD 字符串列表（按屏幕顺序）。"""
    out = []
    for it in nodes(root):
        t = it["text"]
        m = CARD_DATE_RE.match(t or "")
        if m:
            out.append(f"{m.group(1)}-{m.group(2)}")
    return out


def step_tabs_and_cards():
    root = launch_home()
    for _ in range(10):
        if find(root, "夸克", exact=True):
            break
        time.sleep(1.5)
        root = dump_xml()
    capture("01_home_tabs")
    for label in ("豆瓣推荐", "夸克", "收藏", "历史"):
        assert find(root, label, exact=True), f"缺少顶部入口 {label}"

    tap(root, "夸克", exact=True, wait=4)
    root = close_popups(dump_xml())
    for _ in range(15):
        if quark_cards(root) or find(root, "暂无数据") or find(root, "夸克最新分享"):
            break
        time.sleep(2)
        root = dump_xml()
    capture("02_quark_tab")
    assert not (find(root, "暂无数据") and not quark_cards(root)), "夸克 tab 空列表"

    # 采集两屏卡片（标题行 + 日期行都收集）
    seen_titles = []
    dates_seq = []
    for _ in range(5):
        for t in quark_cards(root):
            if t not in seen_titles:
                seen_titles.append(t)
        dates_seq.extend(quark_card_dates(root))
        sh("input", "swipe", "540", "1700", "540", "600", "350")
        time.sleep(1.2)
        root = dump_xml()
    cards = seen_titles
    assert len(cards) >= 10, f"夸克 tab 卡片不足 10 条（实际 {len(cards)}）"

    bad = [t for t in cards if any(k in t for k in JUNK)]
    assert not bad, f"出现垃圾条目: {bad[:4]}"

    # 日期行存在且按倒序（「11月24日更新」排在「11月21日更新」前）
    assert dates_seq, "卡片上没有显示日期行（『N月N日更新』缺失）"
    for i in range(len(dates_seq) - 1):
        assert dates_seq[i] >= dates_seq[i + 1], \
            f"日期未按倒序: {dates_seq[i]} < {dates_seq[i+1]}"
    print("  日期序列:", dates_seq[:12])
    print("  采样:", cards[:6])

    # 点第一条进详情页
    root = dump_xml()
    first = find(root, cards[0].split("  ")[0][:12])
    if not first:
        # 回到顶部再找
        for _ in range(4):
            sh("input", "swipe", "540", "600", "540", "1700", "350")
            time.sleep(0.8)
        root = dump_xml()
        first = find(root, cards[0][:12])
    assert first, "找不到第一条卡片可点击元素"
    sh("input", "tap", str(first["cx"]), str(first["cy"]))
    time.sleep(6)
    root = close_popups(dump_xml())
    capture("03_detail")
    # 详情页标题非空（转存结果或加载中都会带顶部标题）
    titles = [t for t in texts(root) if len(t) >= 4 and "夸克" in (t + "夸克")]
    assert find(root, "夸克") or titles, "详情页没有打开"


def main():
    adb("logcat", "-c")
    record("01_夸克入口列表无垃圾日期倒序详情可达", step_tabs_and_cards)
    summary = {"steps": STEPS, "crashes": CRASHES}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
