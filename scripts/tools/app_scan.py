#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""应用可解析性扫描：把手机上的 App 一个个拉起来，只「看」不「动」，
记录每个 App 首屏能解析出多少无障碍节点——用来回答
「这套感知方案在哪些 App 上信息丰富、在哪些上等于白板」。

对每个 App 采集：
  · 解析到的元素数 / 其中可点·可滚·可输入的数量
  · 前台 Activity、屏幕上可见文本条数
  · 首屏截图（按项目约定落 WebP）

全程只做「启动 + 读树 + 截图」，不发送任何点击/输入，不改变 App 内任何状态。

用法（用托管 venv 解释器）:
  python scripts/tools/app_scan.py --apps com.sankuai.meituan,com.xingin.xhs
  python scripts/tools/app_scan.py --all-third-party --limit 12
  python scripts/tools/app_scan.py --apps com.taobao.taobao --settle 5 --out artifacts/app-scan
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import portal_q  # noqa: E402
from webp_shot import png_to_webp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))


def adb_bytes(args, timeout=60):
    return subprocess.run([portal_q.ADB] + args, capture_output=True,
                          timeout=timeout).stdout


def launch(pkg):
    subprocess.run([portal_q.ADB, "shell", "monkey", "-p", pkg,
                    "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True, text=True, timeout=60)


def home():
    subprocess.run([portal_q.ADB, "shell", "input", "keyevent", "3"],
                   capture_output=True, text=True, timeout=30)


def scan_one(pkg, label, settle, out_dir, quality=78):
    home()
    time.sleep(0.8)
    launch(pkg)
    time.sleep(settle)

    phone = portal_q.query("content://com.mobilerun.portal/phone_state")
    tree = portal_q.query("content://com.mobilerun.portal/a11y_tree_full?filter=false")
    els = portal_q.elements(tree) if isinstance(tree, (dict, list)) else []

    def flag_count(k):
        n = 0
        stack = [tree]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                if cur.get(k):
                    n += 1
                stack.extend(cur.get("children") or [])
            elif isinstance(cur, list):
                stack.extend(cur)
        return n

    texts, seen = [], set()
    for e in els:
        t = (e.get("label") or "").strip()
        if t and t not in seen:
            seen.add(t)
            texts.append(t)

    shot = None
    if out_dir:
        png = adb_bytes(["exec-out", "screencap", "-p"])
        if png[:8] == b"\x89PNG\r\n\x1a\n":
            webp = png_to_webp(png, quality=quality, max_width=720)
            os.makedirs(out_dir, exist_ok=True)
            shot = os.path.join(out_dir, "%s.webp" % pkg.replace(".", "_"))
            with open(shot, "wb") as f:
                f.write(webp)

    return {
        "packageName": pkg,
        "label": label,
        "foreground": phone.get("packageName"),
        "activity": phone.get("activityName"),
        "launched": phone.get("packageName") == pkg,
        "elements": len(els),
        "clickable": flag_count("isClickable"),
        "scrollable": flag_count("isScrollable"),
        "editable": flag_count("isEditable"),
        "visibleTextCount": len(texts),
        "sampleText": texts[:14],
        "screenshot": os.path.relpath(shot, ROOT).replace("\\", "/") if shot else None,
    }


def main():
    ap = argparse.ArgumentParser(description="应用可解析性扫描（只读，不点击）")
    ap.add_argument("--apps", help="逗号分隔的包名；不给则用 --all-third-party")
    ap.add_argument("--all-third-party", action="store_true", help="扫描全部第三方应用")
    ap.add_argument("--limit", type=int, default=0, help="配合 --all-third-party 限制数量")
    ap.add_argument("--settle", type=float, default=4.0, help="启动后等待秒数")
    ap.add_argument("--out", default=os.path.join(ROOT, "artifacts/app-scan"))
    args = ap.parse_args()

    pkgs = portal_q.query("content://com.mobilerun.portal/packages")
    by_pkg = {a["packageName"]: a.get("label", "") for a in pkgs}

    if args.apps:
        targets = [(p.strip(), by_pkg.get(p.strip(), "?")) for p in args.apps.split(",") if p.strip()]
    elif args.all_third_party:
        targets = [(a["packageName"], a.get("label", "")) for a in pkgs if not a.get("isSystemApp")]
        if args.limit:
            targets = targets[: args.limit]
    else:
        ap.error("需要 --apps 或 --all-third-party")

    results = []
    for i, (pkg, label) in enumerate(targets, 1):
        print("[%d/%d] %s (%s) ..." % (i, len(targets), label, pkg), flush=True)
        try:
            r = scan_one(pkg, label, args.settle, args.out)
        except Exception as exc:  # 单个 App 失败不影响整批
            r = {"packageName": pkg, "label": label, "error": str(exc)[:200]}
        results.append(r)
        print("      elements=%s clickable=%s editable=%s launched=%s" % (
            r.get("elements"), r.get("clickable"), r.get("editable"), r.get("launched")), flush=True)

    home()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n写出 %s.json" % args.out)


if __name__ == "__main__":
    main()
