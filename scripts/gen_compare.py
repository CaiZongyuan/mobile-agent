# -*- coding: utf-8 -*-
"""生成 Jev(OpenRouter) vs 手工 ADB 操控对比报告 HTML"""
import json, os, base64, html

ROOT = r"D:/Projects/Agents/mobile-agent"
manual = json.load(open(os.path.join(ROOT, "docs/perf-report/perf_data.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "docs")

MIME = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}

def b64(p):
    """返回完整 data URI。优先取同名 .webp（项目约定：PNG 一律转 WebP）。"""
    stem = os.path.splitext(p)[0]
    for ext in ("webp", "png", "jpg", "jpeg"):
        q = stem + "." + ext
        if os.path.exists(q):
            with open(q, "rb") as f:
                return "data:%s;base64,%s" % (MIME[ext], base64.b64encode(f.read()).decode())
    raise FileNotFoundError("截图缺失: %s.(webp|png)" % stem)

jev_steps = []
tin = tout = 0.0; cost = 0.0; model = ""
for line in open(os.path.join(ROOT, "artifacts/jev-run/trace.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    if d["type"] == "step":
        jev_steps.append(d)
        if d.get("usage"):
            u = d["usage"]
            tin += u.get("input_tokens", 0); tout += u.get("output_tokens", 0)
            cost += u.get("cost", 0) or 0
            model = d.get("responseModel") or model

M_TOTAL = manual["total_ms"] / 1000          # 21.8
J_TOTAL = 14.691                              # wallMs from trace result

# 手工阶段聚合 (ms)
manual_phases = [
    ("环境检查（devices/Portal/分辨率）", 0, 5645, "#8b949e"),
    ("冷启动+等待就绪（含 daemon 5.1s + 轮询 17.1s）", 5645, 23972, "#f0883e"),
    ("首页 → 多多买菜（定位+点击+渲染等待）", 23972, 30663, "#4c8dff"),
    ("关弹窗 → 肉蛋分类", 30663, 33981, "#4c8dff"),
    ("解析鸡蛋商品", 33981, 34645, "#39c5cf"),
]
# Jev 分段 (ms)：模型(紫) / 执行(蓝) / 收尾观察(青)
jev_phases = [
    ("① OPEN_APP 拼多多", 0, 928, 928, 2664),
    ("② TAP 多多买菜", 2664, 3160, 3160, 6971),
    ("③ TAP 肉蛋", 6971, 7892, 7892, 11717),
    ("④ DONE + 最终确认观察", 11717, 12714, 12714, 14691),
]
SCALE = 100 / max(M_TOTAL, J_TOTAL)  # %/ms

def gantt(phases):
    rows = []
    for ph in phases:
        name, a, b = ph[0], ph[1], ph[2]
        if len(ph) == 4:  # 单段色块: (名称, 起, 止, 颜色)
            rows.append('<div class="grow"><div class="gbar" style="left:%.2f%%;width:%.2f%%;background:%s"></div><span class="glabel">%s <b>%ds</b></span></div>'
                        % (a * SCALE, max((b - a) * SCALE, .4), ph[3], html.escape(name), round((b - a) / 1000, 1)))
        else:  # 双段: (名称, 模型起, 模型止, 执行止)
            ms, ae2 = ph[3], ph[4]
            rows.append('<div class="grow"><div class="gbar" style="left:%.2f%%;width:%.2f%%;background:#a371f7"></div><div class="gbar" style="left:%.2f%%;width:%.2f%%;background:#4c8dff"></div><span class="glabel">%s <b>模型%ds+执行%ds</b></span></div>'
                        % (a * SCALE, max((ms - a) * SCALE, .4), ms * SCALE, max((ae2 - ms) * SCALE, .4),
                           html.escape(name), round((ms - a) / 1000, 1), round((ae2 - ms) / 1000, 1)))
    return "\n".join(rows)

jev_rows = []
for i, s in enumerate(jev_steps):
    jev_rows.append(
        '<tr><td class="mono">{n}</td><td><span class="dot" style="background:{c}"></span>{op}</td>'
        '<td class="dim">{lb}</td><td class="mono">{cf}</td><td class="mono rt">{ms} ms</td></tr>'.format(
            n=i + 1, c="#a371f7" if s["operation"] == "DONE" else "#4c8dff",
            op=html.escape(s["operation"]), lb=html.escape((s.get("label") or "目标已满足（模型判定）")[:52]),
            cf=s["confidence"], ms=s["latencyMs"]))
jev_table = "\n".join(jev_rows)

img_j1 = b64(os.path.join(ROOT, "artifacts/jev-run/step-01"))
img_j2 = b64(os.path.join(ROOT, "artifacts/jev-run/step-02"))
img_j3 = b64(os.path.join(ROOT, "artifacts/jev-run/step-03"))
img_jf = b64(os.path.join(ROOT, "artifacts/jev-run/final-now"))
img_m1 = b64(os.path.join(ROOT, "docs/perf-report/01-pdd-home"))
img_m2 = b64(os.path.join(ROOT, "docs/perf-report/02-maicai-loaded"))
img_m3 = b64(os.path.join(ROOT, "docs/perf-report/03-meigory"))

def shot(img, t, cap):
    return ('<div class="shotcard"><div class="shotimg"><img src="%s" alt="%s"></div>'
            '<div class="shotcap"><b>%s</b><span>%s</span></div></div>') % (img, t, t, cap)

page = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>对比报告 · mobile-jev 自主决策 vs 手工 ADB 编排</title>
<style>
  :root {{ --bg:#0d1117; --panel:#161b22; --card:#1c2128; --line:#30363d; --txt:#e6edf3; --dim:#8b949e; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--txt); font:14px/1.65 "Segoe UI","Microsoft YaHei",sans-serif; padding:32px 20px 60px; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  h1 {{ font-size:24px; margin-bottom:4px; }}
  h2 {{ font-size:18px; margin:40px 0 14px; padding-left:10px; border-left:3px solid #a371f7; }}
  .sub {{ color:var(--dim); margin-bottom:24px; }}
  .vs {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .vcard {{ flex:1 1 300px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px 20px; }}
  .vcard h3 {{ font-size:15px; margin-bottom:10px; }}
  .vcard .big {{ font-size:30px; font-weight:700; }}
  .vcard ul {{ list-style:none; margin-top:10px; }}
  .vcard li {{ padding:3px 0; color:var(--dim); font-size:13px; border-top:1px dashed var(--line); }}
  .vcard li b {{ color:var(--txt); }}
  .jev {{ border-color:#a371f7; }} .jev h3 {{ color:#a371f7; }}
  .manual h3 {{ color:#f0883e; }}
  .grow {{ position:relative; height:26px; margin:4px 0; background:#21262d; border-radius:4px; }}
  .gbar {{ position:absolute; top:4px; height:16px; border-radius:3px; opacity:.95; }}
  .glabel {{ position:relative; z-index:1; font-size:11px; padding-left:6px; line-height:26px; white-space:nowrap; }}
  .glabel b {{ color:var(--dim); }}
  .axis {{ display:flex; justify-content:space-between; color:var(--dim); font-size:11px; margin:6px 2px 0; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:13px; }}
  th,td {{ padding:7px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  th {{ background:#21262d; color:var(--dim); font-size:12px; }}
  tr:last-child td {{ border-bottom:none; }}
  .mono {{ font-family:Consolas,monospace; font-size:12px; }} .rt {{ text-align:right; }}
  .dim {{ color:var(--dim); }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px; }}
  .shots {{ display:flex; gap:12px; flex-wrap:wrap; }}
  .shotcard {{ flex:1 1 200px; max-width:240px; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  .shotimg img {{ display:block; width:100%; height:auto; }}
  .shotcap {{ padding:8px 10px; font-size:12px; }}
  .shotcap span {{ color:var(--dim); display:block; margin-top:2px; font-size:11px; }}
  .cmp {{ width:100%; border-collapse:collapse; font-size:13px; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  .cmp th, .cmp td {{ padding:8px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
  .cmp th {{ background:#21262d; color:var(--dim); font-weight:600; }}
  .cmp td:nth-child(2) {{ color:#f0883e; }} .cmp td:nth-child(3) {{ color:#a371f7; }}
  .insights {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px 22px; }}
  .insights li {{ margin:9px 0 9px 18px; }}
  .insights b {{ color:#a371f7; }}
  .ok {{ color:#3fb950; }} .warn {{ color:#f0883e; }}
  footer {{ margin-top:48px; color:var(--dim); font-size:12px; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>同一任务、两种驱动方式对比</h1>
  <div class="sub">任务：冷启动 → 拼多多 → 多多买菜 → 肉蛋分类 → 找到鸡蛋 · 设备 OnePlus6 (d5652109) USB + Mobilerun Portal · {model}</div>

  <div class="vs">
    <div class="vcard manual">
      <h3>方案 A · 手工 ADB 编排（我逐步驱动）</h3>
      <div class="big">21.8 s <small style="font-size:14px;color:var(--dim)">/ 22 步 / 0 次 LLM</small></div>
      <ul>
        <li>路径：预知最优路径（人工踩点后硬编码逻辑）</li>
        <li>感知：4 次无障碍树读取，共 2.7s</li>
        <li>开销大头：固定 sleep 缓冲 7.7s（35%）+ adb daemon 冷启动 5.1s</li>
        <li>换目标 = 重写脚本；每步确定、可精确复现</li>
        <li>成本：$0</li>
      </ul>
    </div>
    <div class="vcard jev">
      <h3>方案 B · mobile-jev 自主决策（Jev via OpenRouter）</h3>
      <div class="big">14.7 s <small style="font-size:14px;color:var(--dim)">/ 3 动作 + DONE / 4 次模型调用</small></div>
      <ul>
        <li>路径：模型实时决策，与人工路径完全一致</li>
        <li>模型耗时共 3.5s（0.50~1.0s/次），观察 3.6s，动作执行 3.1s，等待 0s</li>
        <li>事件驱动：无固定 sleep，60ms 轮询 + 400ms settle 上限</li>
        <li>换目标 = 改一行 goal 文字，零代码</li>
        <li>成本：30.8K in + 2.8K out tokens ≈ <b>$0.00129</b></li>
      </ul>
    </div>
  </div>

  <h2>时间线对比（同一秒刻度）</h2>
  <div style="font-size:13px;margin-bottom:6px;color:#f0883e">方案 A · 手工编排 · 总长 {mt}s</div>
  <div>{gantt_m}</div>
  <div class="axis"><span>0s</span><span>5s</span><span>10s</span><span>15s</span><span>20s</span></div>
  <div style="font-size:13px;margin:18px 0 6px;color:#a371f7">方案 B · Jev 自主决策 · 总长 {jt}s</div>
  <div>{gantt_j}</div>
  <div class="axis"><span>0s</span><span>3s</span><span>6s</span><span>9s</span><span>12s</span><span>15s</span></div>
  <div class="dim" style="font-size:12px;margin-top:8px">紫色 = Jev 模型决策（OpenRouter decisions API），蓝色 = 设备动作执行。方案 A 的第二阶段含 adb daemon 冷启动 5.1s 与就绪轮询 17.1s，为当次会话一次性开销。</div>

  <h2>Jev 决策明细</h2>
  <table>
    <thead><tr><th>#</th><th>操作</th><th>目标 / 说明</th><th>置信度</th><th style="text-align:right">模型耗时</th></tr></thead>
    <tbody>{jev_table}</tbody>
  </table>

  <h2>执行现场（Jev 三步 + 完成复核）</h2>
  <div class="shots">
    {s1}{s2}{s3}{s4}
  </div>

  <h2>维度对比</h2>
  <table class="cmp">
    <tr><th style="width:160px">维度</th><th>手工 ADB 编排</th><th>mobile-jev 自主决策</th></tr>
    <tr><td>总耗时</td><td>21.8 s</td><td class="ok"><b>14.7 s（快 32%）</b></td></tr>
    <tr><td>到达路径</td><td>3 次点击（相同）</td><td>2 次点击 + 1 次应用拉起（相同）</td></tr>
    <tr><td>等待策略</td><td>固定 sleep 7.7s</td><td class="ok">事件驱动，等待 0s（就用轮询兜底）</td></tr>
    <tr><td>感知方式</td><td>无障碍树 + Python 解析</td><td>同一棵无障碍树，由仓库 summarizeState 归一化</td></tr>
    <tr><td>决策者</td><td>我（预先编排的确定性逻辑）</td><td>Jev 模型（每步实时选择操作+目标）</td></tr>
    <tr><td>泛化性</td><td class="warn">换任务要重写脚本</td><td class="ok">改一行 goal 即可，本次未做任何任务特化</td></tr>
    <tr><td>单次成本</td><td class="ok">$0（纯本地）</td><td>$0.00129 / 次（OpenRouter 计费）</td></tr>
    <tr><td>可复现性</td><td class="ok">完全确定</td><td>同 prompt 不保证同 trace（置信度/页面状态影响）</td></tr>
    <tr><td>完成判定</td><td>人工确认截图</td><td class="warn">模型 DONE 早于页面渲染 ~1s（conf 0.64），需外部验证器</td></tr>
    <tr><td>风控暴露</td><td>连续滚动曾触发安全验证</td><td>本跑未触发（未滚动）</td></tr>
    <tr><td>接入成本</td><td>已有脚本直接用</td><td>云设备适配：本次写了 ~150 行本地 Portal 适配器 + 端点替换</td></tr>
  </table>

  <h2>结论</h2>
  <ul class="insights">
    <li><b>Jev 更快不是靠模型，而是靠工程</b>：14.7s vs 21.8s 的差距几乎全部来自「事件驱动等待」替代「固定 sleep」——这正是手工方案里我标记的最大可优化项（预估可省 30-50%），Jev 用实测验证了这一点。</li>
    <li><b>模型只花了 3.5s、$0.0013 就选出了与人工完全一致的 3 步路径</b>，且置信度 0.81/0.99/0.98——在结构化决策协议（Jev questions/probabilities）下，小模型+窄输出空间的可靠性相当高。</li>
    <li><b>DONE 偏早是真实风险</b>：第 4 步在"肉蛋 tab 已选中"时即判完成（conf 0.64），此时鸡蛋尚未渲染上屏。事后人工复核鸡蛋可见（30枚/盒 土鸡蛋 ¥22.99），任务真实完成——但换一个没有外部验证的场景就可能假阳性，仓库自带的 demo-verifier 设计是必要的。</li>
    <li><b>两者是互补而非替代</b>：探索/新任务用 Jev（零脚本成本、能泛化）；高频固定流程用编排脚本（$0、完全确定、无模型延迟）。Jev 的 trace（每步置信度+延迟+动作）也可以直接当作编排脚本的"自动踩点工具"。</li>
    <li><b>适配成本很低</b>：Portal 的 a11y_tree_full 与 Mobilerun 云 API 字段完全同构，本地适配器只需实现 observe/act/listApps 三个方法；OpenRouter 的 decisions 端点与 TypeSafe 官方协议一致，仅换 URL、模型名加 ~ 前缀。</li>
  </ul>

  <footer>生成于 2026-09-18 19:05 · 数据：docs/perf-report/perf_data.json（方案A） + artifacts/jev-run/trace.jsonl（方案B） · 模型 {model}</footer>
</div>
</body>
</html>"""

vals = dict(
    model=html.escape(model), mt=M_TOTAL, jt=J_TOTAL,
    gantt_m=gantt(manual_phases), gantt_j=gantt(jev_phases),
    jev_table=jev_table,
    s1=shot(img_j1, "① 桌面", "Jev 第一步直接选择 OPEN_APP 拉起拼多多"),
    s2=shot(img_j2, "② 拼多多首页", "第二步 TAP 多多买菜入口（conf 0.99）"),
    s3=shot(img_j3, "③ 买菜页 · 肉蛋已选中", "第三步 TAP 肉蛋（conf 0.98），此时 DONE 已触发但内容未渲染"),
    s4=shot(img_jf, "④ 完成复核（事后截图）", "鸡蛋可见：30枚/盒 土鸡蛋 ¥22.99，任务真实完成"),
)
out_path = os.path.join(OUT, "jev-vs-manual.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(page.format(**vals))
print("written:", out_path, os.path.getsize(out_path) // 1024, "KB")
