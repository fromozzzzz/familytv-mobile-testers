#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夸克网盘链路真机验收（模拟器）：服务端 Cookie 同步 → 分享转存 → 原画播放 → 立即清理。

环境变量：
  SHARE_URL   夸克分享链接（如 https://pan.quark.cn/s/xxxx）
  EXPECT_MEMBER  期望会员标识（默认 88VIP）
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
OUT = ROOT / "quark_results"
OUT.mkdir(parents=True, exist_ok=True)
PKG = "com.studio.osc.mobile"
HOME = f"{PKG}/com.github.tvbox.osc.mobile.MobileHomeActivity"
SHARE_URL = os.environ.get("SHARE_URL", "")
EXPECT_MEMBER = os.environ.get("EXPECT_MEMBER", "88VIP")

STEPS = []
CRASHES = []


def adb(*args, timeout=90, binary=False):
    r = subprocess.run(["adb", *args], capture_output=True, timeout=timeout)
    if binary:
        return r.stdout or b""
    return (r.stdout or b"").decode("utf-8", "replace")


def sh(*args, timeout=90):
    return adb("shell", *args, timeout=timeout)


def dump_xml():
    sh("uiautomator", "dump", "/sdcard/quark.xml")
    raw = adb("exec-out", "cat", "/sdcard/quark.xml")
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
        raise AssertionError(f"未找到元素: {sub}")
    sh("input", "tap", str(it["cx"]), str(it["cy"]))
    time.sleep(wait)
    return it


def capture(tag):
    png = adb("exec-out", "screencap", "-p", binary=True)
    (OUT / f"{tag}.png").write_bytes(png)
    root = dump_xml()
    (OUT / f"{tag}.xml").write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    texts = [f"{it['text'] or it['desc']}" for it in nodes(root) if (it["text"] or it["desc"])]
    print(f"[CAP] {tag}: " + " | ".join(texts[:14]))
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
    for _ in range(12):
        time.sleep(2)
        root = close_popups(dump_xml())
        if find(root, "家庭影视"):
            break
    return root


def main():
    adb("logcat", "-c")
    root = None

    def step_sync():
        nonlocal root
        root = launch_home()
        time.sleep(10)  # 后台同步 mobile_quark.json
        root = capture("01_home_sync")
        banner = find(root, "云端状态")
        assert banner, "首页缺少夸克云端状态栏"
        print("  banner:", banner["text"])
        assert "已就绪" in banner["text"], f"未同步到可用登录态: {banner['text']}"

    def step_quark_tab():
        nonlocal root
        root = dump_xml()
        tap(root, "夸克", exact=True, wait=3)
        root = capture("02_quark_tab")
        assert find(root, "TVBox_Cache"), "夸克页缺少 TVBox_Cache 目录约定"
        assert find(root, "我的夸克视频"), "夸克页缺少个人视频区块"

    def step_transfer_and_play():
        nonlocal root
        assert SHARE_URL, "未提供 SHARE_URL"
        root = dump_xml()
        edit = None
        for it in nodes(root):
            if "EditText" in it["cls"]:
                edit = it
                break
        assert edit, "夸克页未找到分享链接输入框"
        sh("input", "tap", str(edit["cx"]), str(edit["cy"]))
        time.sleep(0.5)
        sh("input", "text", SHARE_URL)
        time.sleep(0.8)
        tap(dump_xml(), "打开", exact=True, wait=3)
        root = None
        for _ in range(12):
            time.sleep(3)
            root = close_popups(dump_xml())
            if find(root, "播放") or find(root, "第01集") or find(root, "第 01 集"):
                break
        root = capture("03_share_transferred")
        assert find(root, "播放") or find(root, "第01集") or find(root, "第 01 集"), \
            "转存后未出现剧集/播放入口"
        play = find(root, "播放") or find(root, "第01集") or find(root, "第 01 集")
        sh("input", "tap", str(play["cx"]), str(play["cy"]))
        time.sleep(8)
        root = close_popups(dump_xml())
        root = capture("04_play_original")
        assert find(root, "原画") or find(root, "画质"), "播放页缺少画质（原画）入口"
        assert find(root, "播放出错") is None, "播放失败：出现『播放出错』提示"

    def step_cleanup():
        nonlocal root
        sh("input", "keyevent", "KEYCODE_BACK")
        time.sleep(2)
        sh("input", "keyevent", "KEYCODE_BACK")
        time.sleep(2)
        root = launch_home()
        tap(root, "夸克", exact=True, wait=3)
        root = dump_xml()
        btn = find(root, "立即清理")
        assert btn, "未找到『立即清理全部临时缓存』按钮"
        sh("input", "tap", str(btn["cx"]), str(btn["cy"]))
        time.sleep(1.5)
        dlg = dump_xml()
        ok = find(dlg, "立即清理", exact=True)
        assert ok, "清理确认弹窗未出现"
        sh("input", "tap", str(ok["cx"]), str(ok["cy"]))
        time.sleep(6)
        root = capture("05_cleanup_done")

    record("01_服务端夸克登录态同步", step_sync)
    record("02_夸克页目录约定", step_quark_tab)
    record("03_分享转存与原画播放", step_transfer_and_play)
    record("04_播完清理", step_cleanup)

    summary = {"steps": STEPS, "crashes": CRASHES, "shareUrl": SHARE_URL}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if CRASHES or any(not s["ok"] for s in STEPS):
        sys.exit(1)


if __name__ == "__main__":
    main()
