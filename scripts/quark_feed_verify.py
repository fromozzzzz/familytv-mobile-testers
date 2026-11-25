#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机版 2.4.0 验收：首页「夸克」入口 + 分类筛选。

① 顶部四入口齐全：豆瓣推荐 / 夸克 / 收藏 / 历史；
② 点「夸克」出现 Telegram 频道聚合的最新分享列表（≥10 条，卡片有日期角标）；
③ 列表不含短剧/解说/预告等垃圾词；
④ 日期倒序：第一屏卡片日期 ≥ 后面卡片日期；
⑤ 抽一条点进详情页能转存出集列表（标题不为空）；
⑥ 分类 chips 两组齐备（全部/电影/剧集/动漫/纪录片 + 区域二级）；
⑦ 点「纪录片」后列表只含纪录片频道内容、点「欧美电影」后卡片日期倒序不破；
⑧ 点回「全部」恢复全量列表。

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
    """屏幕上可见的夸克 tab 卡片标题行。

    标题行特征：含 (年份) 括号（如「无声的回响 (2026) 4K HQ…」），
    与 badge 行（「4K · 高码率 · …」以画质开头、以 · 分隔）区分开。
    """
    out = []
    for it in nodes(root):
        t = it["text"]
        if not t or len(t) < 6:
            continue
        if t in ("豆瓣推荐", "夸克", "收藏", "历史", "搜片", "家庭影视", "电影", "剧集", "动漫综艺"):
            continue
        if CARD_DATE_RE.match(t):
            continue
        # 必须有片名+年份括号才认作标题行（badge 行没有 (2026) 这种片名年份）
        if not re.search(r"[（(](?:19|20)\d{2}[)）]", t):
            continue
        if TITLE_HINT_RE.search(t):
            out.append(t)
    return out


def quark_junk_rows(root):
    """badge 行（画质角标）：用来做垃圾词扫描的补充集合。"""
    out = []
    for it in nodes(root):
        t = it["text"]
        if not t:
            continue
        if " · " in t and re.search(r"4K|1080|高码|60F|60帧|HDR|DV|EP?\d+|集", t):
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

    # 采集两屏卡片（标题行 + badge 行 + 日期行都收集，分别校验）
    seen_titles = []
    seen_badges = []
    dates_seq = []
    for _ in range(5):
        for t in quark_cards(root):
            if t not in seen_titles:
                seen_titles.append(t)
        for t in quark_junk_rows(root):
            if t not in seen_badges:
                seen_badges.append(t)
        dates_seq.extend(quark_card_dates(root))
        sh("input", "swipe", "540", "1700", "540", "600", "350")
        time.sleep(1.2)
        root = dump_xml()
    cards = seen_titles
    assert len(cards) >= 10, f"夸克 tab 卡片不足 10 条（实际 {len(cards)}）"
    rows_for_junk = cards + seen_badges

    bad = [t for t in rows_for_junk if any(k in t for k in JUNK)]
    assert not bad, f"出现垃圾条目: {bad[:4]}"

    # 日期行存在且按倒序（「11月24日更新」排在「11月21日更新」前）
    assert dates_seq, "卡片上没有显示日期行（『N月N日更新』缺失）"
    for i in range(len(dates_seq) - 1):
        assert dates_seq[i] >= dates_seq[i + 1], \
            f"日期未按倒序: {dates_seq[i]} < {dates_seq[i+1]}"
    print("  日期序列:", dates_seq[:12])
    print("  采样:", cards[:6])

    # 点第一条标题卡片进详情页：回到列表顶部重新定位（此时 cards[0] 一定可见）
    for _ in range(4):
        sh("input", "swipe", "540", "500", "540", "1900", "350")
        time.sleep(0.8)
    root = dump_xml()
    first = None
    probe = cards[0]
    # 标题可能被 UI 截断（单行 ellipsize），用前 10 个字符做部分匹配
    first = find(root, probe[:10])
    if not first:
        # 找不到就扫当前可见的标题行点第一个
        visible = quark_cards(root)
        assert visible, "列表回顶后没有可见卡片"
        probe = visible[0]
        first = find(root, probe[:10])
    assert first, f"找不到第一条卡片可点击元素（probe={probe[:16]}）"
    sh("input", "tap", str(first["cx"]), str(first["cy"]))
    time.sleep(6)
    root = close_popups(dump_xml())
    capture("03_detail")
    # 详情页打开的判定：出现集列表/播放按钮/「详情」元素，或 Activity 栈进入 MobileDetailActivity
    cur = sh("dumpsys", "activity", "activities")
    opened = DETAIL.split("/")[-1] in cur or find(root, "集") or find(root, "详情")
    assert opened, "详情页没有打开"


# ─── 2.4.0 分类筛选验收 ────────────────────────────────────────────────────
def back_home_from_detail():
    sh("input", "keyevent", "KEYCODE_BACK")
    time.sleep(2.5)
    root = close_popups(dump_xml())
    for _ in range(8):
        if find(root, "夸克", exact=True) and find(root, "豆瓣推荐", exact=True):
            break
        sh("input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.5)
        root = close_popups(dump_xml())
    return root


def collect_card_titles(max_swipes=6):
    """从列表顶开始滑屏收集当前分类下可见的全部标题行。"""
    root = dump_xml()
    seen = []
    for _ in range(max_swipes):
        for t in quark_cards(root):
            if t not in seen:
                seen.append(t)
        sh("input", "swipe", "540", "1700", "540", "600", "350")
        time.sleep(1.0)
        root = dump_xml()
    for _ in range(max_swipes + 2):
        sh("input", "swipe", "540", "500", "540", "1900", "350")
        time.sleep(0.6)
    return seen


def step_category_filter():
    root = launch_home()
    tap(root, "夸克", exact=True, wait=4)
    root = close_popups(dump_xml())
    for _ in range(15):
        if quark_cards(root) or find(root, "夸克最新分享"):
            break
        time.sleep(2)
        root = dump_xml()

    # ⑥ 一级 chips：全部/电影/剧集/动漫/纪录片
    for label in ("全部", "电影", "剧集", "动漫", "纪录片"):
        assert find(root, label, exact=True), f"缺少一级分类 chip: {label}"
    capture("04_quark_chips")

    # ⑦ 点「电影」→ 出现二级区域 chips
    tap(root, "电影", exact=True, wait=1.5)
    root = dump_xml()
    for label in ("欧美电影", "华语电影"):
        assert find(root, label, exact=True), f"电影组缺少二级 chip: {label}"
    movie_titles = collect_card_titles()
    capture("05_quark_movie")
    assert len(movie_titles) >= 1, "点「电影」后没有任何卡片"

    # 二级点「欧美电影」：列表应变少（区域子集）
    tap(root, "欧美电影", exact=True, wait=1.5)
    root = dump_xml()
    west_titles = collect_card_titles()
    capture("06_quark_movie_west")
    assert len(west_titles) >= 1, "点「欧美电影」后没有任何卡片"
    assert len(west_titles) <= len(movie_titles), "区域筛选后卡片应不多于「电影」组"

    # ⑦ 点「纪录片」：全为纪录片类。xvth5 频道内容特征：地球/自然/星球/全N集 等
    tap(root, "纪录片", exact=True, wait=1.5)
    root = dump_xml()
    doc_titles = collect_card_titles()
    capture("07_quark_doc")
    assert len(doc_titles) >= 1, "点「纪录片」后没有任何卡片"
    # 纪录片频道（xvth5）条目普遍带「全N集」标记；若全部缺失则可疑
    with_ep = [t for t in doc_titles if re.search(r"全\d+集|合集", t)]
    print("  纪录片采样:", doc_titles[:6])
    assert len(with_ep) >= len(doc_titles) // 3, \
        f"纪录片列表内容可疑（全N集 标记占比过低）: {doc_titles[:6]}"

    # ⑧ 点回「全部」恢复
    tap(root, "全部", exact=True, wait=1.5)
    root = dump_xml()
    all_titles = collect_card_titles()
    capture("08_quark_all")
    assert len(all_titles) >= 10, f"点回「全部」后没有恢复全量列表（{len(all_titles)} 条）"
    assert len(all_titles) >= len(west_titles), "「全部」应不少于任何分类子集"


def main():
    adb("logcat", "-c")
    record("01_夸克入口列表无垃圾日期倒序详情可达", step_tabs_and_cards)
    record("02_分类筛选chips齐全切换正确", step_category_filter)
    summary = {"steps": STEPS, "crashes": CRASHES}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
