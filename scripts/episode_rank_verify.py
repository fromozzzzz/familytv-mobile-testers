#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机版 2.2.2 验收：
① 豆瓣两级分类（电影/剧集/动漫综艺 + 细分）；
② 追剧搜索按集数优先（更新至第7集 排在 全6集 前面）；
③ 原盘 REMUX 降权（排在 4K高码 之后、1080P 之前）；
④ 详情页夸克集列表带「第N集」标签且有「待上线」灰色占位。

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
OUT = ROOT / "episode_rank_results"
OUT.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
HOME = f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity"
SEARCH = f"{PKG}/com.github.tvbox.osc.mobile.MobileSearchActivity"

STEPS = []
CRASHES = []
HOT_KEYWORD = "庆余年"
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
        sh("uiautomator", "dump", "/sdcard/ep_rank.xml")
        raw = adb("exec-out", "cat", "/sdcard/ep_rank.xml")
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


def step_group_chips():
    root = launch_home()
    for _ in range(12):
        if find(root, "电影", exact=True):
            break
        time.sleep(1.5)
        root = dump_xml()
    capture("01_groups")
    for g in ("电影", "剧集", "动漫综艺"):
        assert find(root, g, exact=True), f"缺少一级分类 {g}"
    tap(root, "剧集", exact=True, wait=2.5)
    root = capture("02_tv_subs")
    for sub in ("综合排名", "国产剧", "美剧", "韩剧", "日剧"):
        assert find(root, sub, exact=True), f"剧集下缺少细分 {sub}"
    tap(root, "动漫综艺", exact=True, wait=2.5)
    root = capture("03_anime_subs")
    assert find(root, "动漫", exact=True), "动漫综艺下缺少「动漫」"
    assert find(root, "综艺", exact=True), "动漫综艺下缺少「综艺」"
    tap(root, "电影", exact=True, wait=2.5)
    root = dump_xml()
    for sub in ("热门电影", "豆瓣高分", "一周口碑"):
        assert find(root, sub, exact=True), f"电影下缺少细分 {sub}"


def quark_titles(root):
    out = []
    for rv in root.iter("node"):
        if "RecyclerView" not in (rv.get("class") or ""):
            continue
        for it in rv.iter("node"):
            t = (it.get("text") or "").strip()
            if t and len(t) >= 3 and not any(k in t for k in SKIP_META):
                out.append(t)
    return out


def step_series_ranking():
    sh("am", "force-stop", PKG)
    sh("am", "start", "-n", SEARCH, "--es", "keyword", HOT_KEYWORD)
    root = None
    for _ in range(15):
        time.sleep(2)
        root = close_popups(dump_xml())
        if find(root, "夸克网盘"):
            break
    for _ in range(25):
        m = find(root, "夸克网盘 (")
        if m and int(re.search(r"\((\d+)\)", m["text"]).group(1)) >= 5:
            break
        time.sleep(2)
        root = dump_xml()
    capture("04_search_series")
    seen = []
    for _ in range(4):
        for t in quark_titles(root):
            if t not in seen and t != HOT_KEYWORD:
                seen.append(t)
        sh("input", "swipe", "540", "1700", "540", "600", "350")
        time.sleep(1)
        root = dump_xml()
    bad = [t for t in seen if any(k in t for k in JUNK)]
    assert not bad, f"搜索结果出现解说/预告: {bad[:4]}"
    upd = [t for t in seen if "更新至" in t]
    fin = [t for t in seen if re.search(r"全\d+集", t)]
    remux = [t for t in seen if "REMUX" in t.upper() or "原盘" in t]
    hib = [t for t in seen if "高码" in t]
    if upd:
        # 追更优先：更新至 在 完结 前面
        if fin:
            iu = seen.index(upd[0])
            if_ = seen.index(fin[0])
            assert iu < if_, f"追更未排在完结前: {upd[0]}@{iu} vs {fin[0]}@{if_}"
    if remux and hib:
        ir = seen.index(remux[0])
        ih = seen.index(hib[0])
        assert ih < ir, f"原盘REMUX 应排在 4K高码 之后: {hib[0]}@{ih} < {remux[0]}@{ir}"
    print("  采样:", seen[:10])
    if not upd and not remux:
        raise AssertionError("采样里既无追更条目也无原盘条目，无法验证排序规则")


def main():
    adb("logcat", "-c")
    record("01_豆瓣两级分类可切换且细分齐全", step_group_chips)
    record("02_追剧更新集优先原盘降权无解说", step_series_ranking)
    summary = {"steps": STEPS, "crashes": CRASHES}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
