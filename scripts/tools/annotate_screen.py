#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把一次真实的屏幕观察渲染成"标注截图"。

在一张手机截图上叠出三层信息，让读者一眼看懂 Agent 到底看到了什么：

  1. 哪些元素被**解析出来**了          —— 灰色虚线框（无障碍树裁剪后剩下的节点）
  2. 哪些元素**被提供了候选操作**      —— 实线彩色框 + 编号徽标（编号 = Jev 请求里的 index）
       · 蓝色 = 可 TAP      · 紫色 = 可 SCROLL      · 绿色 = 可输入
  3. 模型**实际选了哪个**              —— 红色准星 + 标签（来自 decision.json）

底部附一条信息带：目标 / 解析与候选计数 / 候选操作清单 / 本次决策与置信度。

数据来源只需 (截图, observation.json)，可选补 request.json 和 decision.json 以获得
"候选操作"和"选中项"两层的精确信息。编号→元素的映射通过复刻 describeAction 的标签
生成逻辑得到，并与 request 里的 label 逐一校验（校验结果会打到 stdout）。

项目约定：所有截图一律输出 WebP。

用法:
  python annotate_screen.py --image screen.png --observation observation.json \\
      [--request request.json] [--decision decision.json] \\
      --out screen-annotated.webp [--thumb 540] [--no-parsed] [--title "..."]

退出码: 0 成功；2 参数/文件问题。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("需要 Pillow：请用托管 venv 的 python 运行本脚本")

# ---------------------------------------------------------------- 视觉常量

C_TAP = (11, 107, 203)      # 蓝：可点
C_SCROLL = (130, 80, 223)   # 紫：可滚
C_TYPE = (26, 127, 55)      # 绿：可输入
C_PARSED = (110, 118, 129)  # 灰：解析到但未提供候选
C_CHOICE = (209, 36, 47)    # 红：模型选中
C_STRIP_BG = (13, 17, 23)
C_STRIP_FG = (230, 237, 243)
C_STRIP_DIM = (139, 148, 158)

FONT_CANDIDATES = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "Deng.ttf", "simsun.ttc"]
FONT_BOLD_CANDIDATES = ["msyhbd.ttc", "msyh.ttc", "simhei.ttf", "Deng.ttf"]


def _font(names, size):
    for n in names:
        p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", n)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------- 数据装载

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def prune_ids(elements):
    """observation.elements 是扁平列表（id 即树路径），这里只做事后查询用的索引。"""
    return {e["id"]: e for e in elements}


def descendants(by_id, node_id):
    """返回 node_id 自身 + 所有后代（按 observation 顺序）。"""
    out = []
    for eid, e in by_id.items():
        if eid == node_id or eid.startswith(node_id + "."):
            out.append(e)
    return out


# ---------------------------------------------------------------- 标签复刻
# 与 references/mobile-jev/scripts/mobile-agent/actions.mjs 的 describeAction 保持一致，
# 用来把 request.state.elements[i].label 反查回 observation 里的元素 id。

def describe_label(by_id, node_id):
    node = by_id.get(node_id)
    if node is None:
        return None
    if node.get("editable"):
        focus = node.get("hint") or node.get("label") or node.get("text") or "empty input field"
        return "Focus text input: %s." % focus
    labels = []
    for e in descendants(by_id, node_id):
        for v in (e.get("text"), e.get("label")):
            if v and v not in labels:
                labels.append(v)
    body = " / ".join(labels) if labels else node_id
    return "Tap %s." % body


def strip_label(full):
    """buildQuestions 里对 describeAction 结果做了 replace(/^Tap |\\.$/g, '')。"""
    if full is None:
        return None
    s = full
    if s.startswith("Tap "):
        s = s[4:]
    if s.endswith("."):
        s = s[:-1]
    return s


def map_indices(obs_elements, req_elements):
    """index -> elementId。优先按复刻出来的 label 精确匹配，其次按「唯一同标签」匹配。"""
    by_id = prune_ids(obs_elements)
    by_label = {}
    for eid, node in by_id.items():
        if not node.get("enabled", True):
            continue
        if not (node.get("clickable") or node.get("editable") or node.get("scrollable")):
            continue
        by_label.setdefault(strip_label(describe_label(by_id, eid)), []).append(eid)

    mapping, unmatched = {}, []
    for item in req_elements or []:
        idx, lbl = str(item.get("index")), item.get("label")
        cands = by_label.get(lbl) or []
        if len(cands) == 1:
            mapping[idx] = cands[0]
        elif cands:
            # 标签撞车：取面积最小的那个（上层容器通常更大）
            def area(eid):
                b = by_id[eid]["bounds"]
                return (b["right"] - b["left"]) * (b["bottom"] - b["top"])

            mapping[idx] = min(cands, key=area)
        else:
            unmatched.append(idx)
    return mapping, unmatched


# ---------------------------------------------------------------- 绘制原语

def dashed_rect(draw, box, color, width=2, dash=14, gap=10, alpha=150):
    """虚线矩形（RGBA 画布上直接给半透明色）。"""
    x1, y1, x2, y2 = box
    fill = color + (alpha,)
    for x in range(int(x1), int(x2), dash + gap):
        draw.line([(x, y1), (min(x + dash, x2), y1)], fill=fill, width=width)
        draw.line([(x, y2), (min(x + dash, x2), y2)], fill=fill, width=width)
    for y in range(int(y1), int(y2), dash + gap):
        draw.line([(x1, y), (x1, min(y + dash, y2))], fill=fill, width=width)
        draw.line([(x2, y), (x2, min(y + dash, y2))], fill=fill, width=width)


def badge(draw, xy, text, color, font):
    """元素编号徽标，贴在框左上角外侧。"""
    x, y = xy
    pad_x, pad_y = 8, 3
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = w + pad_x * 2, h + pad_y * 2
    x = max(0, min(x, draw.im.size[0] - w))
    y = max(0, y - h)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=5, fill=color + (255,))
    draw.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=(255, 255, 255, 255))
    return y


def chip(draw, xy, text, color, font, anchor_left=True):
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 9, 4
    w, h = tw + pad_x * 2, th + pad_y * 2
    if not anchor_left:
        x -= w
    draw.rounded_rectangle([x, y, x + w, y + h], radius=5, fill=color + (235,))
    draw.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=(255, 255, 255, 255))
    return w, h


def wrap_text(draw, text, font, max_w):
    """贪心换行：优先在空格/· 处断，超长片段再按字符切（中文友好）。"""
    if max_w <= 0 or draw.textlength(text, font=font) <= max_w:
        return [text]
    tokens = re.findall(r"\S+\s*", text) or [text]
    lines, cur = [], ""
    for tk in tokens:
        if draw.textlength(cur + tk, font=font) <= max_w:
            cur += tk
            continue
        if cur:
            lines.append(cur.rstrip())
            cur = ""
        if draw.textlength(tk, font=font) <= max_w:
            cur = tk
            continue
        buf = ""
        for ch in tk:
            if draw.textlength(buf + ch, font=font) <= max_w:
                buf += ch
            else:
                lines.append(buf)
                buf = ch
        cur = buf
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [text]


def crosshair(draw, cx, cy, color, r=34, width=5):
    """选中目标的准星：圆环 + 十字 + 中心点。"""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (255,), width=width)
    draw.line([(cx - r * 1.9, cy), (cx - r * 1.15, cy)], fill=color + (255,), width=width)
    draw.line([(cx + r * 1.15, cy), (cx + r * 1.9, cy)], fill=color + (255,), width=width)
    draw.line([(cx, cy - r * 1.9), (cx, cy - r * 1.15)], fill=color + (255,), width=width)
    draw.line([(cx, cy + r * 1.15), (cx, cy + r * 1.9)], fill=color + (255,), width=width)
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=color + (255,))


# ---------------------------------------------------------------- 主渲染

def ops_color(operations):
    if not operations:
        return None
    if "TAP" in operations:
        return C_TAP
    if any(o.startswith("SCROLL_") for o in operations):
        return C_SCROLL
    if "TYPE_TEXT" in operations:
        return C_TYPE
    return C_TAP


def render(args):
    obs = load_json(args.observation)
    req = load_json(args.request) if args.request else None
    dec = load_json(args.decision) if args.decision else None

    screen = obs.get("screen") or {}
    sw, sh = int(screen.get("width", 1080)), int(screen.get("height", 2280))
    obs_elements = obs.get("elements") or []

    img = Image.open(args.image).convert("RGB")
    scale = img.width / float(sw)
    # 统一把 observation 坐标系映射到图片像素
    def to_px(b):
        return (b["left"] * scale, b["top"] * scale, b["right"] * scale, b["bottom"] * scale)

    W = img.width
    canvas = Image.new("RGBA", (W, img.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    f_badge = _font(FONT_BOLD_CANDIDATES, max(15, int(22 * scale)))
    f_chip = _font(FONT_BOLD_CANDIDATES, max(14, int(21 * scale)))

    # ---- 1. 解析到但未提供候选的元素（灰虚线）
    by_id = prune_ids(obs_elements)
    req_elements = (req or {}).get("state", {}).get("elements") or []
    mapping, unmatched = ({}, [])
    if req_elements:
        mapping, unmatched = map_indices(obs_elements, req_elements)
    offered_ids = set(mapping.values())

    n_parsed_drawn = 0
    screen_area = max(1.0, float(sw) * float(sh))
    if not args.no_parsed:
        for e in obs_elements:
            if e["id"] in offered_ids:
                continue
            b = e["bounds"]
            if b["right"] - b["left"] < 6 or b["bottom"] - b["top"] < 6:
                continue
            # 全屏根节点画虚线框只会糊满整屏，跳过
            if ((b["right"] - b["left"]) * (b["bottom"] - b["top"])) / screen_area >= 0.9:
                continue
            dashed_rect(draw, to_px(b), C_PARSED, width=2, alpha=140)
            n_parsed_drawn += 1

    # ---- 2. 提供了候选的元素（实线彩框 + 编号）
    counts = {"TAP": 0, "SCROLL": 0, "TYPE": 0}
    box_by_index = {}
    chosen_idx = str(dec.get("target")) if dec and dec.get("target") is not None else None
    n_fullscreen_skipped = 0
    for item in req_elements:
        idx = str(item.get("index"))
        eid = mapping.get(idx)
        if not eid:
            continue
        node = by_id.get(eid)
        if node is None:
            continue
        ops = item.get("operations") or []
        color = ops_color(ops)
        if color is None:
            continue
        if color is C_TAP:
            counts["TAP"] += 1
        elif color is C_SCROLL:
            counts["SCROLL"] += 1
        else:
            counts["TYPE"] += 1
        box = to_px(node["bounds"])
        b = node["bounds"]
        full = ((b["right"] - b["left"]) * (b["bottom"] - b["top"])) / screen_area >= 0.9
        # 全屏根容器（a11y 的顶层 FrameLayout）画出来就是一个套满整屏的大框，
        # 纯噪声；除非它就是模型选中的目标，否则只登记坐标不描边。
        if full and idx != chosen_idx:
            n_fullscreen_skipped += 1
            box_by_index[idx] = (box, node, ops)
            continue
        draw.rectangle(box, outline=color + (255,), width=max(3, int(4 * scale)))
        badge(draw, (box[0], box[1] - max(2, int(3 * scale))), idx, color, f_badge)
        box_by_index[idx] = (box, node, ops)

    # 没有 request 时退化成按 observation 自身属性着色
    if not req_elements:
        for e in obs_elements:
            if e.get("editable"):
                c, counts["TYPE"] = C_TYPE, counts["TYPE"] + 1
            elif e.get("scrollable"):
                c, counts["SCROLL"] = C_SCROLL, counts["SCROLL"] + 1
            elif e.get("clickable"):
                c, counts["TAP"] = C_TAP, counts["TAP"] + 1
            else:
                continue
            draw.rectangle(to_px(e["bounds"]), outline=c + (255,), width=max(3, int(4 * scale)))

    # ---- 3. 模型选中的目标（红准星）
    chosen_txt = ""
    if dec:
        op = dec.get("operation") or "-"
        tgt = dec.get("target")
        chosen_txt = "%s%s" % (op, (" [%s]" % tgt) if tgt else "")
        box = box_by_index.get(str(tgt), (None, None, None))[0] if tgt is not None else None
        if box:
            cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
            crosshair(draw, cx, cy, C_CHOICE, r=max(20, int(30 * scale)))
            chip(draw, (cx + max(30, int(44 * scale)), cy - max(14, int(20 * scale))),
                 chosen_txt, C_CHOICE, f_chip)

    img = Image.alpha_composite(img.convert("RGBA"), canvas)

    # ---- 4. 底部信息带
    goal = (req or {}).get("state", {}).get("goal") or obs.get("phone", {}).get("currentApp") or ""
    questions = (req or {}).get("questions") or {}
    op_keys = list((questions.get("operation") or {}).get("criteria", {}).keys())
    n_candidates = len(req_elements)
    lines = []

    lines.append(("目标", goal, C_STRIP_FG))
    if req_elements:
        detail = "解析到 %d 个元素 → 提供候选 %d 个（可点 %d · 可滚 %d · 可输入 %d）" % (
            len(obs_elements), n_candidates, counts["TAP"], counts["SCROLL"], counts["TYPE"])
        lines.append(("感知", detail, C_STRIP_FG))
        lines.append(("候选操作", " · ".join(op_keys) if op_keys else "—", C_STRIP_FG))
    else:
        lines.append(("感知", "解析到 %d 个元素（未提供 request.json，仅按元素自身属性着色）"
                      % len(obs_elements), C_STRIP_FG))

    if dec:
        conf = dec.get("confidence")
        tconf = dec.get("targetConfidence")
        lat = dec.get("latencyMs")
        bits = [chosen_txt or "-"]
        if conf is not None:
            bits.append("置信度 %.2f" % conf)
        if tconf is not None:
            bits.append("目标置信度 %.2f" % tconf)
        if lat is not None:
            bits.append("模型 %.1f ms" % lat)
        if dec.get("usage"):
            bits.append("$%.5f" % (dec["usage"].get("cost") or 0))
        lines.append(("决策", " · ".join(bits), C_STRIP_FG))

    if unmatched:
        lines.append(("提示", "%d 个候选的 label 无法反查回元素（已跳过）: %s"
                      % (len(unmatched), ",".join(unmatched[:12])), (240, 180, 80)))

    f_lbl = _font(FONT_BOLD_CANDIDATES, max(17, int(26 * scale)))
    f_txt = _font(FONT_CANDIDATES, max(17, int(26 * scale)))
    lh = int(42 * scale)
    pad = int(20 * scale)
    legend_h = int(40 * scale)

    probe = ImageDraw.Draw(Image.new("RGB", (W, 10)))
    lw = max(probe.textbbox((0, 0), lbl, font=f_lbl)[2] for lbl, _, _ in lines) + int(18 * scale)
    val_w = max(int(80 * scale), W - pad * 2 - lw)

    wrapped, n_rows = [], 0
    for lbl, val, col in lines:
        parts = wrap_text(probe, val, f_txt, val_w)
        wrapped.append((lbl, parts, col))
        n_rows += len(parts)

    strip_h = pad * 2 + lh * n_rows + legend_h + int(12 * scale)
    total_h = img.height + strip_h
    out = Image.new("RGBA", (W, total_h), C_STRIP_BG + (255,))
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)

    y = img.height + pad
    for lbl, parts, col in wrapped:
        d.text((pad, y), lbl, font=f_lbl, fill=(88, 166, 255, 255))
        for i, part in enumerate(parts):
            d.text((pad + lw, y + i * lh), part, font=f_txt, fill=col + (255,))
        y += lh * len(parts)

    # 图例
    y += int(4 * scale)
    lx = pad
    items = [("可 TAP", C_TAP, "solid"), ("可 SCROLL", C_SCROLL, "solid"),
             ("可输入", C_TYPE, "solid"), ("解析到但未提供候选", C_PARSED, "dashed"),
             ("模型选中", C_CHOICE, "ring")]
    sw_sz = int(14 * scale)
    for name, col, kind in items:
        if kind == "solid":
            d.rectangle([lx, y + int(4 * scale), lx + sw_sz * 2, y + int(4 * scale) + sw_sz],
                        fill=col + (255,))
        elif kind == "dashed":
            dashed_rect(d, (lx, y + int(4 * scale), lx + sw_sz * 2, y + int(4 * scale) + sw_sz),
                        col, width=2, dash=6, gap=5, alpha=255)
        else:
            d.ellipse([lx + sw_sz // 2, y + int(4 * scale), lx + sw_sz * 2, y + int(4 * scale) + sw_sz],
                      outline=col + (255,), width=3)
        lx += sw_sz * 2 + int(8 * scale)
        d.text((lx, y), name, font=f_txt, fill=C_STRIP_DIM + (255,))
        lx += d.textbbox((0, 0), name, font=f_txt)[2] + int(24 * scale)

    final = out.convert("RGB")

    # ---- 5. 落盘
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    q = args.quality
    final.save(args.out, "WEBP", quality=q, method=6)

    thumbs = []
    if args.thumb:
        t = final.copy()
        t.thumbnail((args.thumb, 10000), Image.LANCZOS)
        tp = os.path.splitext(args.out)[0] + "-thumb.webp"
        t.save(tp, "WEBP", quality=q, method=6)
        thumbs.append(tp)

    return {
        "out": args.out,
        "thumbs": thumbs,
        "screen": "%dx%d" % (sw, sh),
        "parsed_elements": len(obs_elements),
        "parsed_drawn": n_parsed_drawn,
        "candidates": n_candidates,
        "fullscreen_skipped": n_fullscreen_skipped,
        "candidate_ops": op_keys,
        "color_counts": counts,
        "unmatched_indices": unmatched,
        "chosen": chosen_txt,
        "size_kb": round(os.path.getsize(args.out) / 1024.0, 1),
    }


def build_parser():
    p = argparse.ArgumentParser(
        description="把屏幕观察渲染成标注截图（项目约定：输出 WebP）")
    p.add_argument("--image", required=True, help="原始截图（PNG/WEBP 均可）")
    p.add_argument("--observation", required=True, help="observation.json")
    p.add_argument("--request", help="request.json（提供候选操作信息）")
    p.add_argument("--decision", help="decision.json（标出模型选中项）")
    p.add_argument("--out", required=True, help="输出 .webp 路径")
    p.add_argument("--quality", type=int, default=88, help="WebP 质量，默认 88")
    p.add_argument("--thumb", type=int, default=0, help="额外输出缩略图宽度，0=不输出")
    p.add_argument("--no-parsed", action="store_true", help="不画「解析到但未提供候选」的灰框")
    p.add_argument("--json", action="store_true", help="把统计结果以 JSON 打到 stdout")
    return p


def main():
    args = build_parser().parse_args()
    for path in filter(None, [args.image, args.observation, args.request, args.decision]):
        if not os.path.exists(path):
            print("找不到文件: %s" % path, file=sys.stderr)
            return 2
    try:
        info = render(args)
    except Exception as e:  # 参数/数据问题统一退出码 2，便于调用方判断
        print("渲染失败: %s" % e, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print("写出 %s (%.1f KB)" % (info["out"], info["size_kb"]))
        print("  解析元素 %d / 候选 %d / 着色 %s"
              % (info["parsed_elements"], info["candidates"], info["color_counts"]))
        if info["unmatched_indices"]:
            print("  ⚠ 未能反查的候选 index: %s" % info["unmatched_indices"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
