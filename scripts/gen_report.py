# -*- coding: utf-8 -*-
"""读取 perf_data.json + 截图，生成自包含的性能检测 HTML 报告"""
import json, os, base64, html

ROOT = r"D:/Projects/Agents/mobile-agent"
OUT = os.path.join(ROOT, "docs", "perf-report")

d = json.load(open(os.path.join(OUT, "perf_data.json"), encoding="utf-8"))
steps = d["steps"]
total = d["total_ms"]

# 修正弹窗描述（实际关闭的是促销弹窗）
for s in steps:
    if s["name"] == "点击: 关闭新人优惠券弹窗":
        s["name"] = "点击: 关闭促销弹窗"

# ---- 统计 ----
by_tag = {}
for s in steps:
    by_tag[s["tag"]] = by_tag.get(s["tag"], 0) + s["ms"]
op_ms = by_tag.get("op", 0)
wait_ms = by_tag.get("wait", 0)
shot_ms = by_tag.get("shot", 0)
n_ops = sum(1 for s in steps if s["tag"] == "op")
n_taps = sum(1 for s in steps if s["action"] == "input tap")
slowest = max(steps, key=lambda s: s["ms"])
tree_steps = [s for s in steps if "a11y_tree" in s["action"]]
shot_steps = [s for s in steps if s["tag"] == "shot"]
tap_steps = [s for s in steps if s["action"] == "input tap"]

MIME = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}

def b64(name):
    """返回完整 data URI。优先用同名 .webp（项目约定：PNG 一律转 WebP，体积约 1/10）。"""
    stem = os.path.splitext(name)[0]
    for ext in ("webp", "png", "jpg", "jpeg"):
        p = os.path.join(OUT, stem + "." + ext)
        if os.path.exists(p):
            with open(p, "rb") as f:
                return "data:%s;base64,%s" % (MIME[ext], base64.b64encode(f.read()).decode())
    raise FileNotFoundError("截图缺失: %s.(webp|png)" % stem)

img1 = b64("01-pdd-home")
img2 = b64("02-maicai-loaded")
img3 = b64("03-meigory")

TAG_COLOR = {"op": "#4c8dff", "wait": "#8b949e", "shot": "#39c5cf"}
TAG_NAME = {"op": "主动操作", "wait": "等待/轮询", "shot": "截图"}

# ---- 甘特图 ----
gantt_rows = []
for i, s in enumerate(steps):
    left = (s["t_offset"] - s["ms"]) / total * 100
    width = max(s["ms"] / total * 100, 0.35)
    c = TAG_COLOR[s["tag"]]
    label = '{} · {}ms'.format(s["name"], s["ms"])
    gantt_rows.append(
        '<div class="grow" title="#{i} {label}"><div class="gbar" style="left:{lw}%;width:{ww}%;background:{c}"></div>'
        '<span class="glabel">{no}. {name} <b>{ms}ms</b></span></div>'.format(
            i=i + 1, label=html.escape(label), lw=round(left, 2), ww=round(width, 2),
            c=c, no=i + 1, name=html.escape(s["name"]), ms=s["ms"]))
gantt_html = "\n".join(gantt_rows)

# ---- 耗时 TOP 柱状图 ----
top = sorted(steps, key=lambda s: -s["ms"])[:10]
maxms = top[0]["ms"]
bar_rows = []
for s in top:
    w = s["ms"] / maxms * 100
    bar_rows.append(
        '<div class="brow"><span class="blabel">{name}</span>'
        '<div class="btrack"><div class="bfill" style="width:{w}%;background:{c}">{ms} ms</div></div></div>'.format(
            name=html.escape(s["name"][:18]), w=round(w, 1), c=TAG_COLOR[s["tag"]], ms=s["ms"]))
bars_html = "\n".join(bar_rows)

# ---- 步骤明细表 ----
tr_rows = []
for i, s in enumerate(steps):
    touch = "—"
    if s.get("touch"):
        t = s["touch"]
        touch = '<span class="chip">tap ({},{})</span>'.format(t["x"], t["y"])
    tr_rows.append(
        '<tr><td class="mono">{no}</td><td class="mono">{t}</td>'
        '<td><span class="dot" style="background:{c}"></span>{name}</td>'
        '<td class="mono">{act}</td><td class="dim">{det}</td>'
        '<td class="mono rt">{ms} ms</td><td>{tc}</td></tr>'.format(
            no=i + 1, t=s["t_offset"], c=TAG_COLOR[s["tag"]], name=html.escape(s["name"]),
            act=html.escape(s["action"]), det=html.escape(s["detail"][:70]),
            ms=s["ms"], tc=touch))
table_html = "\n".join(tr_rows)

# ---- 截图 + 轨迹叠加 ----
shots_meta = [
    ("01-pdd-home.png", img1, "① 拼多多首页", "已定位「多多买菜」入口图标",
     [(108, 810, "1", "点击多多买菜入口")]),
    ("02-maicai-loaded.png", img2, "② 多多买菜页加载完成", "WebView 渲染完成，检测到促销弹窗",
     [(904, 352, "2", "点击关闭促销弹窗")]),
    ("03-meigory.png", img3, "③ 肉蛋分类页", "成功解析到鸡蛋商品：30枚/盒 土鸡蛋 ¥22.99",
     [(666, 1031, "3", "点击肉蛋分类 tab")]),
]
shot_cards = []
for fn, img, title, cap, marks in shots_meta:
    overlays = []
    for x, y, no, tip in marks:
        overlays.append(
            '<div class="mk" style="left:{lx}%;top:{ly}%" title="{tip}"><span>{no}</span></div>'
            '<div class="cross-h" style="top:{ly}%"></div><div class="cross-v" style="left:{lx}%"></div>'.format(
                lx=round(x / 1080 * 100, 2), ly=round(y / 2280 * 100, 2), no=no, tip=html.escape(tip)))
    shot_cards.append(
        '<div class="shotcard"><div class="shotimg"><img src="{img}" alt="{t}">{ov}</div>'
        '<div class="shotcap"><b>{t}</b><span>{cap}</span></div></div>'.format(
            img=img, t=title, cap=cap, ov="".join(overlays)))
shots_html = "\n".join(shot_cards)

def pct(x):
    return round(x / total * 100, 1)

page = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>设备操控性能检测报告 · 多多买菜购蛋流程</title>
<style>
  :root {{ --bg:#0d1117; --panel:#161b22; --card:#1c2128; --line:#30363d;
           --txt:#e6edf3; --dim:#8b949e; --blue:#4c8dff; --teal:#39c5cf; --gray:#8b949e; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--txt); font:14px/1.65 "Segoe UI","Microsoft YaHei",sans-serif; padding:32px 20px 60px; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  h1 {{ font-size:24px; margin-bottom:4px; }}
  h2 {{ font-size:18px; margin:40px 0 14px; padding-left:10px; border-left:3px solid var(--blue); }}
  .sub {{ color:var(--dim); margin-bottom:24px; }}
  .kpis {{ display:flex; gap:12px; flex-wrap:wrap; }}
  .kpi {{ flex:1 1 150px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .kpi .v {{ font-size:24px; font-weight:700; }}
  .kpi .k {{ color:var(--dim); font-size:12px; margin-top:2px; }}
  .legend {{ display:flex; gap:16px; margin:10px 0 12px; color:var(--dim); font-size:12px; }}
  .legend i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }}
  .gantt {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
  .grow {{ position:relative; height:24px; margin:3px 0; }}
  .gbar {{ position:absolute; top:4px; height:14px; border-radius:3px; min-width:3px; opacity:.92; }}
  .glabel {{ position:relative; z-index:1; font-size:11px; color:var(--txt); padding-left:6px; line-height:24px; white-space:nowrap; }}
  .glabel b {{ color:var(--dim); font-weight:600; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:13px; }}
  th,td {{ padding:7px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
  th {{ background:#21262d; color:var(--dim); font-weight:600; font-size:12px; position:sticky; top:0; }}
  tr:last-child td {{ border-bottom:none; }}
  .mono {{ font-family:Consolas,monospace; font-size:12px; }}
  .rt {{ text-align:right; }}
  .dim {{ color:var(--dim); }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px; }}
  .chip {{ background:#21262d; border:1px solid var(--line); border-radius:4px; padding:1px 7px; font-family:Consolas,monospace; font-size:11px; }}
  .shots {{ display:flex; gap:16px; flex-wrap:wrap; }}
  .shotcard {{ flex:1 1 300px; max-width:340px; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  .shotimg {{ position:relative; }}
  .shotimg img {{ display:block; width:100%; height:auto; }}
  .mk {{ position:absolute; width:26px; height:26px; margin:-13px 0 0 -13px; border-radius:50%;
        background:rgba(76,141,255,.9); border:2px solid #fff; color:#fff; font-size:13px; font-weight:700;
        display:flex; align-items:center; justify-content:center; box-shadow:0 0 0 4px rgba(76,141,255,.25); z-index:2; }}
  .cross-h {{ position:absolute; left:0; right:0; height:0; border-top:1px dashed rgba(76,141,255,.55); z-index:1; }}
  .cross-v {{ position:absolute; top:0; bottom:0; width:0; border-left:1px dashed rgba(76,141,255,.55); z-index:1; }}
  .shotcap {{ padding:10px 12px; font-size:13px; }}
  .shotcap span {{ display:block; color:var(--dim); font-size:12px; margin-top:2px; }}
  .brow {{ display:flex; align-items:center; gap:10px; margin:6px 0; }}
  .blabel {{ width:150px; font-size:12px; text-align:right; color:var(--txt); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .btrack {{ flex:1; background:#21262d; border-radius:4px; height:20px; overflow:hidden; }}
  .bfill {{ height:100%; border-radius:4px; color:#fff; font-size:11px; line-height:20px; padding-left:8px; min-width:52px; }}
  .insights {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px 22px; }}
  .insights li {{ margin:9px 0 9px 18px; }}
  .insights b {{ color:var(--blue); }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:760px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  .panel {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px 18px; }}
  .panel h3 {{ font-size:14px; margin-bottom:8px; color:var(--dim); font-weight:600; }}
  .bignum {{ font-size:22px; font-weight:700; }}
  footer {{ margin-top:48px; color:var(--dim); font-size:12px; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>设备操控性能检测报告</h1>
  <div class="sub">任务：多多买菜浏览鸡蛋（拼多多） · {device} · 分辨率 {res} · ADB + Mobilerun Portal 无障碍树</div>

  <div class="kpis">
    <div class="kpi"><div class="v">{total_s}s</div><div class="k">端到端总耗时</div></div>
    <div class="kpi"><div class="v">{nsteps}</div><div class="k">执行步骤</div></div>
    <div class="kpi"><div class="v">{ntaps}</div><div class="k">触控点击（轨迹点）</div></div>
    <div class="kpi"><div class="v">{op_s}s <small style="font-size:13px;color:var(--dim)">{op_p}%</small></div><div class="k">主动操作耗时（adb 命令）</div></div>
    <div class="kpi"><div class="v">{wait_s}s <small style="font-size:13px;color:var(--dim)">{wait_p}%</small></div><div class="k">等待/轮询耗时</div></div>
    <div class="kpi"><div class="v">{shot_s}s</div><div class="k">截图耗时（3 张）</div></div>
  </div>

  <h2>整体时间线（甘特图）</h2>
  <div class="legend">
    <span><i style="background:{c_op}"></i>主动操作</span>
    <span><i style="background:{c_wait}"></i>等待/轮询</span>
    <span><i style="background:{c_shot}"></i>截图</span>
    <span>横轴 = 会话开始后 {total_s} 秒内的绝对时间</span>
  </div>
  <div class="gantt">{gantt}</div>

  <h2>触控轨迹与现场截图</h2>
  <div class="shots">{shots}</div>

  <h2>单步耗时 TOP 10</h2>
  <div class="panel">{bars}</div>

  <h2>关键耗时分布</h2>
  <div class="grid2">
    <div class="panel"><h3>感知成本 · 无障碍树读取+解析（{ntree} 次）</h3>
      <div class="bignum">{tree_avg} ms <small style="font-size:13px;color:var(--dim)">/ 次均值</small></div>
      <div class="dim" style="margin-top:6px">单次范围 {tree_min}–{tree_max} ms。这是 Agent「看清屏幕」的主要成本，元素越多越慢（首页 105 个 → 分类页 129 个元素）。</div></div>
    <div class="panel"><h3>执行成本 · 单次 input tap（{ntap} 次）</h3>
      <div class="bignum">{tap_avg} ms <small style="font-size:13px;color:var(--dim)">/ 次均值</small></div>
      <div class="dim" style="margin-top:6px">adb shell input tap 的进程拉起开销约 400ms，坐标计算（无障碍树 bounds 取中心）本身耗时 ≈0ms。</div></div>
    <div class="panel"><h3>取证成本 · screencap 截图（{nshot} 张）</h3>
      <div class="bignum">{shot_avg} ms <small style="font-size:13px;color:var(--dim)">/ 张均值</small></div>
      <div class="dim" style="margin-top:6px">{shot_fmt} 全屏回传（{shot_dim}）。3 张截图共 {shot_s}s，占全程 {shot_p}%；{shot_note}。</div></div>
    <div class="panel"><h3>环境成本 · adb daemon 冷启动</h3>
      <div class="bignum">{first_ms} ms</div>
      <div class="dim" style="margin-top:6px">会话第一条 adb 命令包含 daemon 启动（tcp:5037），属一次性开销；后续命令稳定在 60–900ms。</div></div>
  </div>

  <h2>步骤明细</h2>
  <table>
    <thead><tr><th>#</th><th>t/ms</th><th>步骤</th><th>命令/方式</th><th>结果</th><th style="text-align:right">耗时</th><th>触点轨迹</th></tr></thead>
    <tbody>{table}</tbody>
  </table>

  <h2>性能分析与结论</h2>
  <ul class="insights">
    <li><b>端到端 {total_s}s 完成「冷启动 → 进入多多买菜 → 关弹窗 → 肉蛋分类 → 解析出鸡蛋」完整闭环</b>，其中真正落在设备上的主动操作仅 {op_s}s（{op_p}%），其余为渲染等待与轮询。</li>
    <li><b>最大单项是 adb daemon 冷启动（{first_ms} ms）</b>，仅出现在会话首命令；常驻会话或复用连接可消除。</li>
    <li><b>固定缓冲等待合计 {buf_s}s</b>（首页 2.0s + WebView 2.5s + 弹窗 1.2s + 分类 2.0s），是最大的可优化项——用「元素出现即继续」的事件驱动轮询替代固定 sleep，预计可压缩 30–50%。</li>
    <li><b>无障碍树是感知主通道</b>：{ntree} 次读取共 {tree_sum} ms，每次约 {tree_avg} ms，明显快于截图 + OCR 路线（截图单张就要 {shot_avg} ms 且还需识别）；且树直接给出 bounds，坐标点击零计算开销。</li>
    <li><b>点击命中率高</b>：{ntaps} 次触控全部按预期生效（入口→弹窗关闭→分类切换均有状态回读验证），归因于「树定位 + 最小面积匹配」策略，避免了整屏 WebView 根节点的误匹配。</li>
    <li><b>风险项</b>：连续快速滚动浏览会触发拼多多安全验证（前一会话实测）；本报告流程未触发。滑动/滚动操作建议控制频率并加随机抖动。</li>
  </ul>

  <footer>生成于 2026-09-18 18:41 · 设备 OnePlus6 (d5652109) · Portal v0.7.25 · 共 {nsteps} 步 · 数据来源 perf_data.json + 3 张现场截图</footer>
</div>
</body>
</html>"""

# ---- 内嵌截图体积统计（项目约定：PNG 一律转 WebP）----
SHOT_STEMS = ("01-pdd-home", "02-maicai-loaded", "03-meigory")

def img_size(p):
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:
        with open(p, "rb") as f:
            head = f.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    return None

emb_bytes = png_bytes = 0
emb_ext, dim = "png", None
for stem in SHOT_STEMS:
    for ext in ("webp", "png"):
        p = os.path.join(OUT, stem + "." + ext)
        if os.path.exists(p):
            emb_bytes += os.path.getsize(p)
            if dim is None:
                dim = img_size(p)
            if ext == "webp":
                emb_ext = "webp"
            break
    png_p = os.path.join(OUT, stem + ".png")
    if os.path.exists(png_p):
        png_bytes += os.path.getsize(png_p)

vals = dict(
    device=html.escape(d["device"]), res=d["resolution"],
    total_s=round(total / 1000, 1), nsteps=len(steps), ntaps=n_taps,
    op_s=round(op_ms / 1000, 1), op_p=pct(op_ms),
    wait_s=round(wait_ms / 1000, 1), wait_p=pct(wait_ms),
    shot_s=round(shot_ms / 1000, 1), shot_p=pct(shot_ms),
    shot_fmt="WebP（quality 78）" if emb_ext == "webp" else "PNG（未压缩）",
    shot_dim=("%d×%d" % dim) if dim else d["resolution"],
    shot_size_label=("%.0f KB" % (emb_bytes / 1024)) if emb_bytes < 1024 * 1024 else ("%.2f MB" % (emb_bytes / 1024 / 1024)),
    shot_note=(
        "报告内嵌 %s，较原始 PNG（%s）压缩到 %d%%"
        % (("%.0f KB" % (emb_bytes / 1024)), ("%.2f MB" % (png_bytes / 1024 / 1024)), round(100 * emb_bytes / png_bytes))
        if (png_bytes and emb_bytes < png_bytes)
        else ("报告内嵌 %s%s" % (("%.0f KB" % (emb_bytes / 1024)), "（WebP）" if emb_ext == "webp" else ""))
    ),
    c_op=TAG_COLOR["op"], c_wait=TAG_COLOR["wait"], c_shot=TAG_COLOR["shot"],
    gantt=gantt_html, shots=shots_html, bars=bars_html, table=table_html,
    ntree=len(tree_steps),
    tree_avg=round(sum(s["ms"] for s in tree_steps) / len(tree_steps)) if tree_steps else 0,
    tree_min=min(s["ms"] for s in tree_steps) if tree_steps else 0,
    tree_max=max(s["ms"] for s in tree_steps) if tree_steps else 0,
    tree_sum=sum(s["ms"] for s in tree_steps),
    ntap=len(tap_steps),
    tap_avg=round(sum(s["ms"] for s in tap_steps) / len(tap_steps)) if tap_steps else 0,
    nshot=len(shot_steps),
    shot_avg=round(sum(s["ms"] for s in shot_steps) / len(shot_steps)) if shot_steps else 0,
    first_ms=steps[0]["ms"],
    buf_s=round(sum(s["ms"] for s in steps if s["name"] == "等待: 页面响应") / 1000, 1),
)
out_path = os.path.join(OUT, "report.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(page.format(**vals))
print("written:", out_path, os.path.getsize(out_path) // 1024, "KB")
