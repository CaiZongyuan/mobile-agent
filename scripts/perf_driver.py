# -*- coding: utf-8 -*-
"""带性能检测的设备操控驱动：跑一次完整多多买菜流程，记录每步耗时/轨迹/截图，输出 perf_data.json"""
import json, re, subprocess, time, os, sys

ROOT = r"D:/Projects/Agents/mobile-agent"
OUT = os.path.join(ROOT, "docs", "perf-report")
os.makedirs(OUT, exist_ok=True)
ADB = "adb"

# 项目约定：截图一律存 WebP（PNG 单张 1.5MB，WebP 约 0.15MB）。
# 需要 Pillow；缺了会优雅退化为 PNG，不阻塞流程。
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
WEBP_QUALITY = 78
WEBP_MAX_WIDTH = 720      # 0 = 原始 1080 宽。720 宽在报告里文字依然清晰，体积再降一档

steps = []
T0 = time.time()

def log_step(name, action, detail, ms, touch=None, shot=None, tag="op"):
    steps.append(dict(name=name, action=action, detail=detail, ms=round(ms),
                      touch=touch, shot=shot, tag=tag,
                      t_offset=round((time.time() - T0) * 1000)))

def run(cmd, timeout=30):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    return r, (time.time() - t0) * 1000

def shot(name):
    t0 = time.time()
    r = subprocess.run([ADB, "exec-out", "screencap", "-p"], capture_output=True, timeout=25)
    raw = r.stdout
    data, ext, note = raw, "png", ""
    try:
        from webp_shot import png_to_webp
        data = png_to_webp(raw, quality=WEBP_QUALITY, max_width=WEBP_MAX_WIDTH)
        ext, note = "webp", " · 压缩到 %.0f%%" % (100 * len(data) / max(len(raw), 1))
    except Exception as e:
        ext = "png"
        note = " · WebP 不可用(%s)，回退 PNG" % type(e).__name__
    ms = (time.time() - t0) * 1000
    # 清掉另一扩展名的陈旧文件，避免新旧混用
    for other in ("png", "webp"):
        if other != ext:
            stale = os.path.join(OUT, name + "." + other)
            if os.path.exists(stale):
                os.remove(stale)
    fn = name + "." + ext
    with open(os.path.join(OUT, fn), "wb") as f:
        f.write(data)
    log_step("截图: " + name, "screencap",
             "屏幕截图保存为 %s（%.0f KB%s）" % (fn, len(data) / 1024, note), ms, tag="shot")
    return fn

def phone_state():
    r, ms = run([ADB, "shell", "content", "query", "--uri",
                 "content://com.mobilerun.portal/phone_state"])
    m = re.search(r"result=(.*)", r.stdout, re.S)
    if not m:
        return None, ms
    try:
        return json.loads(json.loads(m.group(1))["result"]), ms
    except Exception:
        return None, ms

def get_tree():
    r, fm = run([ADB, "shell", "content", "query", "--uri",
                 "content://com.mobilerun.portal/a11y_tree"])
    t0 = time.time()
    texts = []
    try:
        m = re.search(r"result=(.*)", r.stdout, re.S)
        tree = json.loads(json.loads(m.group(1))["result"])
        def walk(n):
            if isinstance(n, dict):
                t = (n.get("text") or n.get("contentDesc") or "").strip()
                b = n.get("bounds", "")
                if t and b:
                    texts.append((t, b))
                for c in n.get("children") or []:
                    walk(c)
            elif isinstance(n, list):
                for c in n:
                    walk(c)
        walk(tree)
    except Exception as e:
        return texts, fm, (time.time() - t0) * 1000, str(e)
    return texts, fm, (time.time() - t0) * 1000, None

def center(b):
    x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", b))
    return (x1 + x2) // 2, (y1 + y2) // 2

def find(texts, kw):
    for t, b in texts:
        if kw in t:
            return t, center(b)
    return None, None

def tap(x, y, what):
    r, ms = run([ADB, "shell", "input", "tap", str(x), str(y)])
    log_step("点击: " + what, "input tap",
             "触点 ({}, {}) → {}".format(x, y, what), ms,
             touch={"type": "tap", "x": x, "y": y})

def sleep_s(sec, why):
    t0 = time.time()
    time.sleep(sec)
    log_step("等待: 页面响应", "wait", why, (time.time() - t0) * 1000, tag="wait")

def has_captcha(texts):
    return any("安全验证" in t for t, _ in texts)

aborted = None

# ---- 1. 设备连接检查 ----
r, ms = run([ADB, "devices", "-l"])
m_dev = re.search(r"d5652109\s+device\b", r.stdout)
log_step("设备连接检查", "adb devices -l",
         ("设备 d5652109 (OnePlus6) 状态=device" if m_dev else "设备未就绪: " + r.stdout[:120]), ms)

# ---- 2. Portal 状态读取 ----
st, ms = phone_state()
log_step("Portal 状态读取", "content query phone_state",
         "当前前台: {}".format(st.get("currentApp") if st else "读取失败"), ms)

# ---- 3. 屏幕分辨率 ----
r, ms = run([ADB, "shell", "wm", "size"])
log_step("读取屏幕分辨率", "wm size", r.stdout.strip().replace("\n", " / "), ms)

# ---- 4. 重置应用状态（避免上次会话残留页面）----
r, ms = run([ADB, "shell", "am", "force-stop", "com.xunmeng.pinduoduo"])
log_step("重置拼多多状态", "am force-stop", "强制停止并清除会话残留页面", ms)

# ---- 5. 启动拼多多 ----
r, ms = run([ADB, "shell", "monkey", "-p", "com.xunmeng.pinduoduo",
             "-c", "android.intent.category.LAUNCHER", "1"])
log_step("启动拼多多", "monkey LAUNCHER", "冷启动 com.xunmeng.pinduoduo", ms)

# ---- 6. 等待前台就绪 ----
t0 = time.time(); ok = False; act = ""
while time.time() - t0 < 15:
    st, _ = phone_state()
    if st and st.get("packageName") == "com.xunmeng.pinduoduo":
        act = str(st.get("activityName", ""))
        if "MainFrame" in act or "NewPage" in act:
            ok = True
            break
    time.sleep(0.6)
log_step("等待拼多多前台就绪", "poll phone_state",
         "前台: {}".format(act if ok else "超时，继续尝试"),
         (time.time() - t0) * 1000, tag="wait")
sleep_s(2, "首页渲染缓冲")
s_home = shot("01-pdd-home")

# ---- 6. 读首页元素树，定位多多买菜入口 ----
texts, fm, pm, err = get_tree()
log_step("读取无障碍树（首页）", "a11y_tree fetch+parse",
         "获取 {} 个可见元素{}".format(len(texts), "，解析异常: " + err if err else ""), fm + pm)
if has_captcha(texts):
    aborted = "首页出现拼多多安全验证（滑块验证码），需人工完成"
    shot("99-captcha")
else:
    t, c = find(texts, "多多买菜")
    if c:
        x, y = c[0], c[1] - 65  # 点标签上方的图标区域
        detail = "入口图标 ({},{})，元素: {}".format(x, y, t[:30])
    else:
        x, y = 108, 810
        detail = "未在树中找到入口文本，使用经验坐标 (108,810)"
    log_step("定位多多买菜入口", "parse a11y_tree", detail, 0.1)

    # ---- 7. 点击进入多多买菜 ----
    tap(x, y, "多多买菜入口")
    t0 = time.time(); entered = False
    while time.time() - t0 < 15:
        st, _ = phone_state()
        if st and "NewPage" in str(st.get("activityName")):
            entered = True
            break
        time.sleep(0.5)
    log_step("等待多多买菜页面加载", "poll phone_state",
             "NewPageActivity 前台" + ("" if entered else "（超时）"),
             (time.time() - t0) * 1000, tag="wait")
    sleep_s(2.5, "WebView 渲染缓冲")
    s_maicai = shot("02-maicai-loaded")

    # ---- 8. 读买菜页树：检测弹窗/验证码 ----
    texts, fm, pm, err = get_tree()
    log_step("读取无障碍树（买菜页）", "a11y_tree fetch+parse",
             "获取 {} 个可见元素".format(len(texts)), fm + pm)
    if has_captcha(texts):
        aborted = "买菜页出现拼多多安全验证（滑块验证码），需人工完成"
        shot("99-captcha")
    else:
        # ---- 9. 关闭新人券弹窗 ----
        t, c = find(texts, "关闭弹窗")
        if c:
            tap(c[0], c[1], "关闭新人优惠券弹窗")
            sleep_s(1.2, "弹窗关闭动画")
        else:
            log_step("新人券弹窗检测", "parse a11y_tree", "未检测到弹窗，跳过关闭", 0.1)

        # ---- 10. 进入肉蛋分类（未找到时重试一次）----
        t, c = find(texts, "肉蛋")
        if not c:
            sleep_s(2, "未找到肉蛋 tab，等待列表二次加载")
            texts, fm2, pm2, err2 = get_tree()
            log_step("重读无障碍树（买菜页重试）", "a11y_tree fetch+parse",
                     "获取 {} 个可见元素".format(len(texts)), fm2 + pm2)
            t, c = find(texts, "肉蛋")
        if c:
            tap(c[0], c[1], "肉蛋分类 tab")
        else:
            log_step("定位肉蛋分类", "parse a11y_tree", "重试后仍未找到肉蛋 tab", 0.1)
        sleep_s(2, "分类页数据加载")
        s_cat = shot("03-meigory")

        # ---- 11. 解析鸡蛋商品 ----
        texts, fm, pm, err = get_tree()
        log_step("读取无障碍树（分类页）", "a11y_tree fetch+parse",
                 "获取 {} 个可见元素".format(len(texts)), fm + pm)
        if has_captcha(texts):
            aborted = "分类页出现拼多多安全验证（滑块验证码），需人工完成"
            shot("99-captcha")
        else:
            eggs = []
            for t, b in texts:
                if "鸡蛋" in t and "加入购物车" not in t and "已加入" not in t:
                    eggs.append(t)
            # 去重
            seen = set(); eggs = [e for e in eggs if not (e in seen or seen.add(e))]
            log_step("解析鸡蛋商品列表", "parse a11y_tree",
                     "找到 {} 款鸡蛋: {}".format(len(eggs), " | ".join(e[:40] for e in eggs[:5])), 0.1)

# ---- 汇总数据 ----
total_ms = round((time.time() - T0) * 1000)
data = {
    "device": "OnePlus6 (d5652109) via USB + Mobilerun Portal",
    "resolution": "1080x2280",
    "total_ms": total_ms,
    "aborted": aborted,
    "steps": steps,
}
with open(os.path.join(OUT, "perf_data.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("DONE total_ms=", total_ms, "steps=", len(steps), "aborted=", aborted)
