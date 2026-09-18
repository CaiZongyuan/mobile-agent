"""Portal ContentProvider 查询小工具（双重 JSON 解码）。

用法:
  python portal_q.py phone_state
  python portal_q.py a11y_tree [filter]
  python portal_q.py packages
  python portal_q.py raw <uri>

优先使用 ADB_PATH 环境变量指定的 adb，默认 D:/Environment/WSA/adb/adb.exe。
"""
import json
import os
import re
import subprocess
import sys

ADB = os.environ.get("ADB_PATH", "D:/Environment/WSA/adb/adb.exe")
BASE = "content://com.mobilerun.portal"


def adb(args, timeout=60):
    r = subprocess.run([ADB] + args, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    return r.stdout or "", r.stderr or ""


def query(uri, timeout=60):
    out, err = adb(["shell", "content", "query", "--uri", uri], timeout=timeout)
    if not out.strip():
        return {"_error": "empty", "_stderr": err.strip()}
    m = re.search(r"result=(.*)", out, re.S)
    if not m:
        return {"_error": "no result field", "_raw": out[:500]}
    payload = m.group(1).strip()
    try:
        outer = json.loads(payload)
    except Exception:
        return {"_raw": payload[:2000]}
    inner = outer.get("result") if isinstance(outer, dict) else None
    if isinstance(inner, str):
        try:
            return json.loads(inner)
        except Exception:
            return inner
    return inner if inner is not None else outer


def a11y_full(filtered=True):
    uri = f"{BASE}/a11y_tree_full"
    if not filtered:
        uri += "?filter=false"
    return query(uri)


def walk(node, out, depth=0):
    """把树拍平成 (depth, text, cls, bounds, flags) 列表。"""
    if isinstance(node, dict):
        text = (node.get("text") or "").strip()
        desc = (node.get("contentDescription") or node.get("contentDesc") or "").strip()
        label = text or desc
        b = node.get("boundsInScreen") or node.get("bounds") or ""
        if isinstance(b, dict):
            b = f'{b.get("left")},{b.get("top")},{b.get("right")},{b.get("bottom")}'
        cls = (node.get("className") or "").split(".")[-1]
        flags = []
        for k, short in (("isClickable", "click"), ("isEditable", "edit"),
                         ("isScrollable", "scroll"), ("isVisibleToUser", "vis")):
            if node.get(k):
                flags.append(short)
        if label or (b and node.get("isClickable")):
            out.append({"depth": depth, "label": label, "cls": cls,
                        "bounds": b, "flags": flags,
                        "pkg": node.get("packageName", "")})
        for c in node.get("children") or []:
            walk(c, out, depth + 1)
    elif isinstance(node, list):
        for c in node:
            walk(c, out, depth)


def elements(tree):
    out = []
    walk(tree, out)
    return out


def center(bounds):
    nums = list(map(int, re.findall(r"-?\d+", str(bounds))))
    if len(nums) < 4:
        return None
    x1, y1, x2, y2 = nums[:4]
    return (x1 + x2) // 2, (y1 + y2) // 2


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "phone_state"
    if cmd == "phone_state":
        print(json.dumps(query(f"{BASE}/phone_state"), ensure_ascii=False, indent=2))
    elif cmd == "packages":
        data = query(f"{BASE}/packages")
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif cmd == "a11y_tree":
        flt = not (len(sys.argv) > 2 and sys.argv[2] == "false")
        data = a11y_full(flt)
        els = elements(data)
        print(f"# elements={len(els)}")
        for e in els:
            pad = "  " * e["depth"]
            print(f'{pad}[{e["cls"]}] "{e["label"]}" {e["bounds"]} {"|".join(e["flags"])}')
    elif cmd == "raw":
        print(json.dumps(query(sys.argv[2]), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(query(f"{BASE}/{cmd}"), ensure_ascii=False, indent=2))
