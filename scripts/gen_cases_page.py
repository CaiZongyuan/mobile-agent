#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 artifacts/cases/ 里的长程案例跑批结果渲染成一份「双引擎对照」页面。

同一批生活场景，两条技术路线各跑一遍：
  engine=jev     每步把 state+questions 发给 Jev，让模型在候选集里做选择题
  engine=script  不用模型，人工把步骤写死（按文本定位 + 固定 sleep）

两者共用同一份 PortalDevice（同一套读屏、同一套 adb 执行），
所以耗时差异只来自决策方式，不来自设备层。

输入（由 case-runner.mjs 产出）:
  artifacts/cases/index.json          案例总览
  artifacts/cases/apps.json           Portal 报上来的已安装应用
  artifacts/cases/<id>/case.json      单案例的决策序列与统计
  artifacts/cases/<id>/step-NN/       该步标注图 + observation/request/response/decision
  artifacts/cases/<id>/final/         终态标注图
  artifacts/cases/<id>/after-NN.webp  动作后截图

输出:
  docs/cases/index.html               对照报告页面
  docs/cases/img/<id>/…               页面引用的图片副本（WebP）

用法:
  python scripts/gen_cases_page.py [--cases-dir artifacts/cases] [--out docs/cases]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENGINE_LABEL = {
    "jev": ("Jev 决策", "#a371f7", "每步一次模型调用，模型在候选集里做选择题"),
    "script": ("写死脚本", "#f0883e", "不用模型，步骤与坐标由人工写死"),
}

TONES = {
    "入门": "#3fb950", "纯点击": "#58a6ff", "只读查询": "#79c0ff",
    "多级导航": "#a371f7", "Tab 切换": "#f0883e", "文字输入": "#db61a2",
    "对照实验": "#e3b341", "长程 · 购物": "#58a6ff", "长程 · 系统": "#a371f7",
    "长程 · 输入": "#db61a2", "失败案例": "#d1242f",
}

OP_COLOR = {
    "TAP": "#58a6ff", "TAP_TEXT": "#58a6ff", "TYPE_TEXT": "#3fb950", "OPEN_APP": "#3fb950",
    "DONE": "#e3b341", "WAIT": "#8b949e", "ASSERT": "#3fb950", "SWIPE": "#a371f7",
    "SCROLL_DOWN": "#a371f7", "SCROLL_UP": "#a371f7", "SCROLL_LEFT": "#a371f7",
    "SCROLL_RIGHT": "#a371f7", "BACK": "#f0883e", "HOME": "#f0883e",
    "ENTER": "#3fb950", "BLOCKED": "#d1242f",
}

STATUS_LABEL = {
    "done": ("已完成", "#3fb950"),
    "preview": ("仅预览", "#8b949e"),
    "step_limit": ("步数用尽", "#e3b341"),
    "stuck": ("卡住（重复动作）", "#d1242f"),
    "blocked": ("被阻塞", "#d1242f"),
    "script_failed": ("脚本中断", "#d1242f"),
    "loading_timeout": ("等待超时", "#e3b341"),
    "unstable_screen": ("界面不稳定", "#e3b341"),
    "input_unverified": ("输入未确认", "#e3b341"),
    "decision_limit": ("决策次数上限", "#e3b341"),
}


def e(s):
    return html.escape(str(s if s is not None else ""))


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------ 图片搬运

def stage_images(cases_dir, out_dir, case_ids):
    img_root = os.path.join(out_dir, "img")
    copied = {}
    for cid in case_ids:
        src = os.path.join(cases_dir, cid)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(img_root, cid)
        os.makedirs(dst, exist_ok=True)
        got = {"steps": [], "final": None, "after": []}
        for name in sorted(os.listdir(src)):
            stepdir = os.path.join(src, name)
            if not (name.startswith("step-") and os.path.isdir(stepdir)):
                continue
            for f in sorted(os.listdir(stepdir)):
                if f.endswith("-annotated.webp"):
                    shutil.copy2(os.path.join(stepdir, f), os.path.join(dst, f))
                    got["steps"].append("img/%s/%s" % (cid, f))
        fdir = os.path.join(src, "final")
        if os.path.isdir(fdir):
            for f in sorted(os.listdir(fdir)):
                if f.endswith("-annotated.webp"):
                    shutil.copy2(os.path.join(fdir, f), os.path.join(dst, f))
                    got["final"] = "img/%s/%s" % (cid, f)
        for f in sorted(os.listdir(src)):
            if f.startswith("after-") and f.endswith(".webp"):
                shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
                got["after"].append("img/%s/%s" % (cid, f))
        copied[cid] = got
    return copied


# ------------------------------------------------------------ 样式

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--card:#1c2128;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--blue:#58a6ff;--purple:#a371f7;--green:#3fb950;--red:#d1242f;--orange:#f0883e;--gold:#e3b341;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--txt);font:14px/1.65 "Segoe UI","Microsoft YaHei",sans-serif;padding:36px 20px 80px;}
.wrap{max-width:1180px;margin:0 auto;}
h1{font-size:26px;letter-spacing:.2px;}
h2{font-size:19px;margin:46px 0 6px;padding-left:11px;border-left:3px solid var(--purple);}
h3{font-size:15px;margin:20px 0 8px;color:var(--blue);}
p{margin:8px 0;}
a{color:var(--blue);text-decoration:none;} a:hover{text-decoration:underline;}
.sub{color:var(--dim);margin-bottom:26px;}
.mono{font-family:Consolas,"Cascadia Mono",monospace;font-size:12px;}
.dim{color:var(--dim);} .rt{text-align:right;} .ctr{text-align:center;}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0 6px;}
.kpi{flex:1 1 140px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 16px;}
.kpi .n{font-size:23px;font-weight:700;line-height:1.2;}
.kpi .l{color:var(--dim);font-size:12px;margin-top:2px;}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px;margin:10px 0 16px;}
th,td{padding:7px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;}
th{background:#21262d;color:var(--dim);font-size:12px;font-weight:600;}
tr:last-child td{border-bottom:none;}
.chip{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;border:1px solid;}
.pair{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin:18px 0 26px;}
.pair-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;}
.pair-head .no{font:700 15px/1 Consolas,monospace;color:var(--gold);}
.pair-head .t{font-size:17px;font-weight:600;}
.arms{display:flex;gap:16px;flex-wrap:wrap;margin-top:14px;}
.arm{flex:1 1 460px;background:#0d1117;border:1px solid var(--line);border-radius:10px;padding:14px 16px;}
.arm.jev{border-color:#6e40c9;}
.arm.script{border-color:#9e6a03;}
.arm h4{font-size:14px;margin-bottom:4px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.arm .desc{color:var(--dim);font-size:12px;margin-bottom:10px;}
.tl{margin:8px 0 4px;}
.tlrow{display:flex;align-items:center;gap:8px;font-size:12px;padding:1px 0;}
.tlrow .ix{width:22px;color:var(--dim);font-family:Consolas,monospace;text-align:right;flex:0 0 22px;}
.tlrow .lb{flex:0 0 152px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.tlrow .bar{flex:1 1 auto;height:11px;background:#21262d;border-radius:3px;position:relative;min-width:40px;}
.tlrow .bar>span{position:absolute;left:0;top:0;bottom:0;border-radius:3px;}
.tlrow .ms{flex:0 0 58px;text-align:right;color:var(--dim);font-family:Consolas,monospace;}
.tlrow.err .lb{color:#ff7b72;}
.shots{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0 4px;}
.shot{flex:0 0 auto;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;width:150px;}
.shot img{display:block;width:100%;height:auto;background:#000;}
.shot .cap{padding:5px 7px;font-size:11px;line-height:1.4;}
.shot .cap span{display:block;color:var(--dim);font-size:10px;}
details{margin:10px 0;background:#0d1117;border:1px solid var(--line);border-radius:8px;}
details>summary{cursor:pointer;padding:8px 12px;font-size:12.5px;color:var(--dim);user-select:none;}
details>summary:hover{color:var(--txt);}
details .body{padding:0 12px 12px;}
pre{background:#0d1117;border:1px solid var(--line);border-radius:8px;padding:11px 13px;overflow:auto;max-height:420px;font:12px/1.55 Consolas,monospace;color:#c9d1d9;}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--dim);margin:10px 0 4px;}
.bar{height:18px;border-radius:4px;background:#21262d;position:relative;overflow:hidden;}
.bar>span{position:absolute;left:0;top:0;bottom:0;border-radius:4px;}
.note{background:#161b22;border-left:3px solid var(--gold);padding:11px 15px;border-radius:0 8px 8px 0;font-size:13px;margin:14px 0;}
.win{color:var(--green);font-weight:600;} .lose{color:var(--red);font-weight:600;}
.foot{color:var(--dim);font-size:12px;margin-top:44px;border-top:1px solid var(--line);padding-top:14px;}
ul{margin-left:20px;} li{margin:5px 0;}
"""


def kpi(n, label, color=None):
    c = ' style="color:%s"' % color if color else ""
    return '<div class="kpi"><div class="n"%s>%s</div><div class="l">%s</div></div>' % (c, e(n), e(label))


def op_badge(op):
    c = OP_COLOR.get(op, "#8b949e")
    return '<span class="chip" style="border-color:%s;color:%s">%s</span>' % (c, c, e(op))


def shot_card(rel, cap, sub=""):
    return ('<div class="shot"><img src="%s" alt="%s" loading="lazy">'
            '<div class="cap">%s<span>%s</span></div></div>') % (e(rel), e(cap), e(cap), e(sub))


# ------------------------------------------------------------ 时间线

def timeline(steps, max_ms=None):
    if not steps:
        return '<p class="dim">（没有步骤记录）</p>'
    mx = max_ms or max((s.get("latencyMs") or 1) for s in steps) or 1
    rows = []
    for s in steps:
        ms = s.get("latencyMs") or 0
        pct = max(1.5, ms / mx * 100)
        col = OP_COLOR.get(s.get("operation"), "#8b949e")
        if s.get("status") in ("error", "blocked"):
            col = "#d1242f"
        rows.append(
            '<div class="tlrow%s"><div class="ix">%s</div><div class="lb" title="%s">%s</div>'
            '<div class="bar"><span style="width:%.1f%%;background:%s"></span></div>'
            '<div class="ms">%s</div></div>' % (
                " err" if s.get("status") in ("error", "blocked") else "",
                e(s.get("n")), e(s.get("label")), e((s.get("label") or "")[:40]),
                pct, col, e("%d" % ms)))
    return '<div class="tl">%s</div><p class="dim" style="font-size:11px">横条长度 = 该步耗时（最长 %d ms）</p>' % (
        "".join(rows), mx)


# ------------------------------------------------------------ 请求 / 响应

def prob_bars(probs, topn=10, chosen=None):
    if not probs:
        return ""
    items = sorted(probs.items(), key=lambda kv: -kv[1])[:topn]
    rows = []
    for k, v in items:
        pct = max(0.0, min(1.0, float(v))) * 100
        col = "#d1242f" if chosen is not None and str(k) == str(chosen) else "#58a6ff"
        rows.append('<tr><td class="mono" style="width:34%%">%s</td>'
                    '<td><div class="bar"><span style="width:%.1f%%;background:%s"></span></div></td>'
                    '<td class="mono rt" style="width:56px">%.3f</td></tr>' % (e(k), pct, col, float(v)))
    return '<table style="margin:6px 0 12px"><tbody>%s</tbody></table>' % "".join(rows)


def response_block(case_dir, rep):
    resp = read_json(os.path.join(case_dir, rep, "response.json"))
    if not resp:
        return ""
    parts = []
    for qname, ans in (resp.get("answers") or {}).items():
        if not isinstance(ans, dict):
            continue
        head = ('<p style="margin:12px 0 2px"><b>question <code class="mono">%s</code></b>　'
                'type=<code class="mono">%s</code>　答案=<b style="color:#d1242f">%s</b>　'
                '置信度=<code class="mono">%s</code></p>') % (
            e(qname), e(ans.get("type")), e(ans.get("choice")), e(ans.get("confidence")))
        parts.append(head + prob_bars(ans.get("probabilities") or {}, 10, ans.get("choice")))
    return ('<details><summary>▸ 模型这一刻的原始回答（完整概率分布，%s）</summary><div class="body">'
            '<p class="dim" style="font-size:12px">返回的不是一段话，而是每个问题一张概率表。'
            '标红项就是被采纳的 choice——校验规则要求它必须是最大项、且概率和归一到 1。</p>%s'
            '<p class="dim" style="font-size:12px">实际服务模型 <code class="mono">%s</code>，'
            '响应 id <code class="mono">%s</code></p></div></details>') % (
        e(rep), "".join(parts), e(resp.get("model", "-")), e(resp.get("id", "-")))


def request_block(case_dir, rep):
    req = read_json(os.path.join(case_dir, rep, "request.json"))
    if not req:
        return ""
    state = req.get("state", {})
    els = state.get("elements") or []
    slim = {
        "goal": state.get("goal"),
        "app": state.get("app"),
        "visibleText": (state.get("visibleText") or [])[:10],
        "recentActions": (state.get("recentActions") or [])[-3:],
        "elements": els[:4] + ([{"…": "共 %d 个候选，此处截断" % len(els)}] if len(els) > 4 else []),
    }
    return ('<details><summary>▸ Jev 实际收到的请求体（%s 那一步）</summary><div class="body">'
            '<pre>%s</pre><p class="dim" style="font-size:12px">完整文件：'
            '<code class="mono">%s</code></p></div></details>') % (
        e(rep), e(json.dumps(slim, ensure_ascii=False, indent=2)),
        e(os.path.relpath(os.path.join(case_dir, rep, "request.json"), ROOT).replace("\\", "/")))


def script_block(case):
    script = case.get("script")
    if not script:
        return ""
    rows = []
    for i, st in enumerate(script, 1):
        kind, arg = "", ""
        for k in ("tapText", "tap", "type", "key", "swipe", "sleep", "expect"):
            if k in st:
                kind = k
                arg = st[k]
                break
        if isinstance(arg, list):
            arg = ", ".join(str(x) for x in arg)
        rows.append('<tr><td class="mono ctr">%d</td><td class="mono">%s</td>'
                    '<td class="mono dim">%s</td><td>%s</td></tr>' % (
                        i, e(kind), e(arg), e(st.get("label", ""))))
    return ('<details open><summary>▸ 这就是「写死」的部分：%d 条硬编码步骤</summary><div class="body">'
            '<table><thead><tr><th class="ctr">#</th><th>类型</th><th>参数</th><th>说明</th></tr></thead>'
            '<tbody>%s</tbody></table>'
            '<p class="dim" style="font-size:12px">脚本引擎与 Jev 引擎共用同一份 PortalDevice，'
            '差的只是「下一步做什么」由谁决定：这里由上面这张表决定。</p></div></details>') % (len(script), "".join(rows))


# ------------------------------------------------------------ 单臂渲染

def arm_block(c, case, imgs, engine):
    label, color, desc = ENGINE_LABEL.get(engine, (engine, "#8b949e", ""))
    slabel, scolor = STATUS_LABEL.get(c.get("status", "?"), (c.get("status"), "#8b949e"))
    steps = case.get("decisions") or []
    ev = imgs.get("steps") or []
    picks = []
    if ev:
        picks.append(shot_card(ev[0], "起点", "模型/脚本接手的这一屏"))
    if len(ev) > 2:
        picks.append(shot_card(ev[len(ev) // 2], "中途", "第 %d 步" % (len(ev) // 2 + 1)))
    if imgs.get("after"):
        picks.append(shot_card(imgs["after"][-1], "最后一次动作后", ""))
    if imgs.get("final"):
        picks.append(shot_card(imgs["final"], "终态", "校验用的是这一屏"))

    checks = c.get("checks") or []
    check_html = ""
    if checks:
        rows = "".join(
            '<tr><td class="mono">%s</td><td>%s</td></tr>' % (
                e(x.get("keyword")),
                '<b class="win">出现</b>' if x.get("pass") else '<b class="lose">未出现</b>')
            for x in checks)
        check_html = ('<table><thead><tr><th>终态校验（读无障碍树）</th><th>结果</th></tr></thead>'
                      '<tbody>%s</tbody></table>' % rows)

    fail = c.get("failed")
    fail_html = ""
    if fail:
        fail_html = ('<div class="note" style="border-color:#d1242f">脚本在第 %s 步断了：<br>'
                     '<code class="mono">%s</code><br>'
                     '<span class="dim" style="font-size:12px">手工脚本没有自愈能力——'
                     '找不到预期控件就停在那里，这正是它与决策模型的差别。</span></div>') % (
            e(fail.get("step")), e(fail.get("error")))

    rep = None
    if engine == "jev" and case.get("dir") and os.path.isdir(case.get("dir")):
        sd = sorted(d for d in os.listdir(case["dir"]) if d.startswith("step-"))
        if sd:
            rep = sd[min(1, len(sd) - 1)]

    return """
<div class="arm %s">
  <h4><span class="chip" style="border-color:%s;color:%s">%s</span>
      <span class="chip" style="border-color:%s;color:%s">%s</span>
      <span class="dim" style="font-size:12px">%d 步 · %.1fs</span></h4>
  <div class="desc">%s</div>
  <div class="kpis" style="margin:8px 0">
    %s%s%s%s
  </div>
  <h3 style="margin-top:6px">逐步耗时</h3>
  %s
  <h3>现场</h3>
  <div class="shots">%s</div>
  %s
  %s
  %s
  %s
  %s
  <p class="dim" style="font-size:11.5px">产物目录 <code class="mono">%s</code></p>
</div>""" % (
        engine, color, color, e(label), scolor, scolor, e(slabel),
        c.get("steps", 0), c.get("wallMs", 0) / 1000.0, e(desc),
        kpi("%.1fs" % (c.get("wallMs", 0) / 1000.0), "端到端"),
        kpi("%.1fs" % ((c.get("modelMs") or 0) / 1000.0), "模型推理"),
        kpi("%.1fs" % ((c.get("waitMs") or 0) / 1000.0), "固定等待"),
        kpi("$%.5f" % (c.get("cost") or 0), "模型花费"),
        timeline(steps),
        "".join(picks), check_html, fail_html,
        request_block(case["dir"], rep) if rep else "",
        response_block(case["dir"], rep) if rep else "",
        script_block(c),
        e(os.path.relpath(case.get("dir", ""), ROOT).replace("\\", "/")),
    )


# ------------------------------------------------------------ 一对对照

def delta(jev, script, key, better="lower", fmt="%.1f", scale=1.0, suffix=""):
    a, b = jev.get(key) or 0, script.get(key) or 0
    fa, fb = fmt % (a / scale), fmt % (b / scale)
    if a == b:
        return fa, fb, ""
    jev_better = (a < b) if better == "lower" else (a > b)
    return (('<b class="win">%s</b>' % fa) if jev_better else fa,
            ('<b class="win">%s</b>' % fb) if not jev_better else fb,
            suffix)


def pair_section(idx, pair_id, cases, imgs):
    jev = next((c for c in cases if (c.get("engine") or "jev") == "jev"), None)
    scr = next((c for c in cases if c.get("engine") == "script"), None)
    base = jev or scr
    title = (base.get("title") or "").split("：")[-1]
    tone = base.get("tone", "")
    tcolor = TONES.get(tone, "#8b949e")
    goal = (jev or {}).get("goal") or ""

    # 对照表
    rows = []
    if jev and scr:
        rows.append(("决策方式", "每步一次 Jev 调用", "零模型调用，流程写死在代码里", ""))
        rows.append(("端到端耗时", *delta(jev, scr, "wallMs", "lower", "%.1f", 1000.0, " 秒")[0:2], ""))
        rows.append(("模型推理耗时", "%.1f 秒" % ((jev.get("modelMs") or 0) / 1000.0),
                     "0 秒（无模型）", ""))
        rows.append(("固定等待", "%.1f 秒" % ((jev.get("waitMs") or 0) / 1000.0),
                     "%.1f 秒" % ((scr.get("waitMs") or 0) / 1000.0), ""))
        rows.append(("步骤数", str(jev.get("steps", 0)), str(scr.get("steps", 0)), ""))
        rows.append(("终态校验", "通过" if jev.get("passed") else "未通过",
                     "通过" if scr.get("passed") else "未通过", ""))
        rows.append(("模型花费", "$%.5f" % (jev.get("cost") or 0), "$0", ""))
        jc = sum(1 for x in jev.get("checks") or [] if x.get("pass"))
        sc = sum(1 for x in scr.get("checks") or [] if x.get("pass"))
        rows.append(("校验命中", "%d/%d" % (jc, len(jev.get("checks") or [])),
                     "%d/%d" % (sc, len(scr.get("checks") or [])), ""))
        rows.append(("换任务要改什么", "改一行 goal 字符串", "重写整张步骤表 / 重新量坐标", ""))
    table = ('<table><thead><tr><th style="width:22%%">维度</th><th>Jev 决策</th>'
             '<th>写死脚本</th></tr></thead><tbody>%s</tbody></table>' % "".join(
        '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(a), b, cc) for a, b, cc, _ in rows)) if rows else ""

    arms = ""
    if jev:
        arms += arm_block(jev, jev, imgs.get(jev["id"], {}), "jev")
    if scr:
        arms += arm_block(scr, scr, imgs.get(scr["id"], {}), "script")

    return """
<section class="pair" id="%s">
  <div class="pair-head">
    <span class="no">%02d</span><span class="t">%s</span>
    <span class="chip" style="border-color:%s;color:%s">%s</span>
  </div>
  <div class="note"><b>场景</b>　%s</div>
  <p class="dim" style="font-size:12.5px">%s</p>
  %s
  <div class="arms">%s</div>
</section>""" % (
        e(pair_id), idx, e(title), tcolor, tcolor, e(tone),
        e(goal), e(base.get("why")),
        table, arms,
    )


# ------------------------------------------------------------ 应用盘点

def apps_section(apps_data):
    if not apps_data:
        return ""
    pkgs = apps_data.get("packages") or []
    sys_apps = [a for a in pkgs if a.get("isSystemApp")]
    third = [a for a in pkgs if not a.get("isSystemApp")]
    used = {
        "com.xunmeng.pinduoduo": "长程场景 L1",
        "com.oneplus.deskclock": "长程场景 L2",
        "com.oneplus.note": "长程场景 L3",
        "com.mobilerun.portal": "感知层本体",
        "com.android.chrome": "早期示例",
    }

    def rows(items):
        out = []
        for a in sorted(items, key=lambda x: x.get("label", "")):
            tag = used.get(a["packageName"])
            note = ('<span class="chip" style="border-color:#e3b341;color:#e3b341">%s</span>' % e(tag)) if tag else ""
            out.append("<tr><td>%s</td><td class='mono dim'>%s</td><td class='mono dim rt'>%s</td><td>%s</td></tr>"
                       % (e(a.get("label")), e(a.get("packageName")),
                          e((a.get("versionName") or "")[:20]), note))
        return "".join(out)

    return """
<h2>附录一 · 这台手机装了什么</h2>
<p>清单不是手工整理的，而是直接问 Portal：<code class="mono">content://com.mobilerun.portal/packages</code>
一次性返回全部「可被 LAUNCHER Intent 启动」的应用，共 <b>%d</b> 个（系统 %d / 第三方 %d）。
Agent 只有拿着这份白名单，才有资格选择 <code class="mono">OPEN_APP</code> 这个动作——
「能打开哪些 App」对模型来说是<b>已知输入</b>，不是它自己猜的。</p>
<div class="kpis">%s%s%s</div>
<h3>系统应用（%d）</h3>
<table><thead><tr><th>名称</th><th>包名</th><th class="rt">版本</th><th>本项目用途</th></tr></thead><tbody>%s</tbody></table>
<h3>第三方应用（%d）</h3>
<table><thead><tr><th>名称</th><th>包名</th><th class="rt">版本</th><th>本项目用途</th></tr></thead><tbody>%s</tbody></table>
""" % (len(pkgs), len(sys_apps), len(third),
       kpi(len(pkgs), "可启动应用"), kpi(len(sys_apps), "系统应用"), kpi(len(third), "第三方应用"),
       len(sys_apps), rows(sys_apps), len(third), rows(third))


# ------------------------------------------------------------ 总结

def summary_section(cases):
    jevs = [c for c in cases if (c.get("engine") or "jev") == "jev"]
    scrs = [c for c in cases if c.get("engine") == "script"]
    rows = []
    for c in cases:
        slabel, scolor = STATUS_LABEL.get(c.get("status", "?"), (c.get("status"), "#8b949e"))
        rows.append("<tr><td class='mono'>%s</td><td>%s</td><td>%s</td>"
                    "<td><span class='chip' style='border-color:%s;color:%s'>%s</span></td>"
                    "<td class='mono rt'>%s</td><td class='mono rt'>%.1fs</td>"
                    "<td class='mono rt'>%.1fs</td><td class='mono rt'>$%.5f</td>"
                    "<td>%s</td></tr>" % (
                        e(c["id"]),
                        '<span class="chip" style="border-color:%s;color:%s">%s</span>' % (
                            ENGINE_LABEL.get(c.get("engine", "jev"))[1],
                            ENGINE_LABEL.get(c.get("engine", "jev"))[1],
                            e(ENGINE_LABEL.get(c.get("engine", "jev"))[0])),
                        e((c.get("title") or "")[:26]),
                        scolor, scolor, e(slabel),
                        e(c.get("steps")), (c.get("wallMs") or 0) / 1000.0,
                        (c.get("modelMs") or 0) / 1000.0, c.get("cost") or 0,
                        e(c.get("expect"))))

    okj = sum(1 for c in jevs if c.get("status") == "done")
    oks = sum(1 for c in scrs if c.get("status") == "done")
    return """
<h2>总览：两种引擎，同一批生活场景</h2>
<div class="kpis">
%s%s%s%s%s
</div>
<table><thead><tr><th>案例</th><th>引擎</th><th>场景</th><th>终态</th>
<th class="rt">步数</th><th class="rt">端到端</th><th class="rt">模型耗时</th><th class="rt">花费</th>
<th>校验基准</th></tr></thead><tbody>%s</tbody></table>""" % (
        kpi(len(cases), "案例总数"),
        kpi("%d / %d" % (okj, len(jevs)), "Jev 臂跑通", "#a371f7"),
        kpi("%d / %d" % (oks, len(scrs)), "脚本臂跑通", "#f0883e"),
        kpi("%.0fs" % (sum((c.get("wallMs") or 0) for c in cases) / 1000.0), "累计端到端"),
        kpi("$%.5f" % sum(c.get("cost") or 0 for c in cases), "累计模型花费"),
        "".join(rows))


# ------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases-dir", default=os.path.join(ROOT, "artifacts/cases"))
    ap.add_argument("--defs", default=os.path.join(ROOT, "scripts/jev-local/cases.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs/cases"))
    args = ap.parse_args()

    # 以 cases.json 的顺序为准，逐个去读 artifacts 里的 case.json。
    # 不依赖 index.json 汇总，好处是「冻结样本」这类没参与本轮跑批的产物也能进页面。
    defs = read_json(args.defs, {}).get("cases") or []
    cases = []
    for d in defs:
        case = read_json(os.path.join(args.cases_dir, d["id"], "case.json"))
        if not case:
            print("  · 跳过 %s（没有 case.json，尚未跑过）" % d["id"])
            continue
        merged = {**d, **case}
        tim = (case.get("result") or {}).get("timings") or {}
        merged.update({
            "engine": case.get("engine") or d.get("engine") or "jev",
            "dir": os.path.join(args.cases_dir, d["id"]),
            "steps": (case.get("result") or {}).get("steps", 0),
            "status": (case.get("result") or {}).get("status", "?"),
            "wallMs": tim.get("wallMs", 0), "modelMs": tim.get("modelMs", 0),
            "waitMs": tim.get("waitMs", 0), "modelCalls": tim.get("modelCalls", 0),
            "cost": sum((x.get("usage") or {}).get("cost", 0) for x in (case.get("decisions") or [])),
            "checks": (case.get("final") or {}).get("checks") or [],
            "passed": (case.get("final") or {}).get("passed"),
            "failed": (case.get("result") or {}).get("failed"),
            "decisions": case.get("decisions") or [],
        })
        cases.append(merged)

    if not cases:
        sys.exit("没有任何 case.json，先跑 case-runner.mjs")
    apps_data = read_json(os.path.join(args.cases_dir, "apps.json"), {})
    os.makedirs(args.out, exist_ok=True)
    imgs = stage_images(args.cases_dir, args.out, [c["id"] for c in cases])
    imgs["_dir"] = args.cases_dir

    order = []
    for c in cases:
        key = c.get("pair") or c["id"]
        if key not in order:
            order.append(key)
    groups = {k: [c for c in cases if (c.get("pair") or c["id"]) == k] for k in order}

    pairs_html = []
    for i, k in enumerate(order, 1):
        pairs_html.append(pair_section(i, k, groups[k], imgs))

    nav = " · ".join('<a href="#%s">%02d %s</a>' % (e(k), i, e((groups[k][0].get("title") or "").split("：")[-1][:24]))
                     for i, k in enumerate(order, 1))

    gen_at = (cases[0].get("at") or "")[:19].replace("T", " ")
    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>长程生活场景 · Jev 决策 vs 写死脚本</title>
<style>%s</style></head><body><div class="wrap">
<h1>长程生活场景 · 两种决策方式对照</h1>
<p class="sub">同一台 OnePlus6，同一套 Mobilerun Portal 读屏 + adb 执行，<b>只有「下一步做什么」由谁决定</b>这一点不同：
一组交给 Jev 逐步决策，另一组把步骤人工写死在代码里。</p>
<div class="note">
<b>怎么读标注图：</b>
<b>灰虚线框</b> = 无障碍树里解析出来、但没进候选集的元素（多为纯装饰性容器）；
<b>彩色实框 + 编号</b> = 真正递给模型的候选操作，编号就是请求里 <code class="mono">elements[index]</code> 的下标；
<b>红色准星</b> = 模型这一步实际选中的目标。图片底部信息带写着目标、解析/候选计数与本次置信度。
</div>
<p class="dim">快速跳转：%s</p>
%s
%s
%s
<div class="foot">
设备 OnePlus6（%s）· Mobilerun Portal v0.7.25 · 决策端点 <code class="mono">openrouter.ai/api/alpha/decisions</code> ·
模型 <code class="mono">~typesafe/jev-latest</code> · 全部截图按项目约定压成 WebP · 生成于 %s
</div>
</div></body></html>""" % (
        CSS, nav, summary_section(cases), "".join(pairs_html), apps_section(apps_data),
        e((cases[0].get("device") or {}).get("id", "d5652109")), e(gen_at),
    )

    out_html = os.path.join(args.out, "index.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(doc)
    print("写出 %s (%.1f KB)" % (out_html, os.path.getsize(out_html) / 1024.0))
    for c in cases:
        g = imgs.get(c["id"], {})
        print("  %-24s %-7s %-13s 步数 %-3s 图 %d" % (
            c["id"], c.get("engine"), c.get("status"), c.get("steps"), len(g.get("steps") or [])))


if __name__ == "__main__":
    main()
