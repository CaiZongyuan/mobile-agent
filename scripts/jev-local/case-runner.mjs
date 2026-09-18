// 批量真机案例执行器 —— 一条流水线上跑两种「决策引擎」，便于同题对照。
//
//   引擎 A: jev     —— 每步把 state+questions 发给 Jev，让模型在候选集里做选择题
//   引擎 B: script  —— 不用模型：人工把步骤写死（固定坐标/固定文本 + 固定 sleep）
//
// 两者共用同一份 PortalDevice（同一套读屏 + 同一套 adb 执行），
// 因此耗时差异只可能来自「决策方式」，不会来自设备层。
//
// 用法:
//   node scripts/jev-local/case-runner.mjs                     # 跑全部
//   node scripts/jev-local/case-runner.mjs --only L1-cart-jev
//   node scripts/jev-local/case-runner.mjs --engine script      # 只跑脚本类案例
//   node scripts/jev-local/case-runner.mjs --dry                # 只决策不执行
//   node scripts/jev-local/case-runner.mjs --list
// 产物: artifacts/cases/<id>/{step-NN/,after-NN.webp,case.json} + artifacts/cases/index.json
import { execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { format } from 'node:util';
import { writeFileSync, mkdirSync, readFileSync, rmSync, renameSync, existsSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PortalDevice, makeOpenRouterRequest, adb } from './portal-device.mjs';
import { saveShot } from './webp.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, '..', '..');
const REPO = new URL('../../references/mobile-jev/scripts/mobile-agent/', import.meta.url);
const VENV_PY = 'C:/Users/zongy/.workbuddy/binaries/python/envs/default/Scripts/python.exe';
const ANNOTATOR = join(ROOT, 'scripts/tools/annotate_screen.py');

process.loadEnvFile(join(ROOT, '.env'));

const { runAgent } = await import(new URL('agent.mjs', REPO).href);
const { TypeSafePolicy } = await import(new URL('policy.mjs', REPO).href);
const { pooledRequest } = await import(new URL('http.mjs', REPO).href);

// ---------- 参数 ----------
const argOf = (flag, def) => {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : def;
};
const OUT = argOf('--out', join(ROOT, 'artifacts/cases'));
const DRY = process.argv.includes('--dry');
const ENGINE_FILTER = argOf('--engine', '');
const LAUNCH_SETTLE_MS = Number(argOf('--settle', '3000'));

const all = JSON.parse(readFileSync(join(HERE, 'cases.json'), 'utf8')).cases;
const only = argOf('--only', '') ? new Set(argOf('--only').split(',').map((s) => s.trim())) : null;
const cases = all.filter((c) => (!only || only.has(c.id))
  && (!ENGINE_FILTER || (c.engine || 'jev') === ENGINE_FILTER));

if (process.argv.includes('--list')) {
  for (const c of cases) console.log(`${c.id.padEnd(22)} ${(c.engine || 'jev').padEnd(7)} ${c.title}`);
  process.exit(0);
}

mkdirSync(OUT, { recursive: true });
const at = () => new Date().toISOString().slice(11, 19);
// console.log 只在「首参」是格式串时替换占位符；这里首参是时间戳，必须走 util.format
const log = (...a) => console.log(`${at()} ${format(...a)}`);
const pad2 = (n) => String(n).padStart(2, '0');
const writeJson = (p, o) => writeFileSync(p, JSON.stringify(o, null, 2));

// ---------- 设备 ----------
const device = new PortalDevice();
const ready = await device.assertReady();
log('[device] %s state=%s', ready.id, ready.state);
const apps = await device.listApps();
log('[apps] 已安装可启动应用 %d 个', apps.length);

// 记录「决策/动作前看到的那一屏」：两种引擎都靠它给标注图配正确的 observation
const rawObserve = device.observe.bind(device);
let lastObs = null;
device.observe = async () => (lastObs = await rawObserve());

// 截获 Jev 的请求体与原始返回
let lastBody = null;
let lastRaw = null;
const baseRequest = makeOpenRouterRequest(pooledRequest);
const policy = new TypeSafePolicy({
  apiKey: process.env.OPENROUTER_API_KEY,
  model: '~typesafe/jev-latest',
  request: async (opts) => {
    lastBody = opts.body;
    const res = await baseRequest(opts);
    lastRaw = res;
    return res;
  },
});

// ---------- 标注 ----------
function annotateStep(dir, n, decision, { withRequest = true } = {}) {
  mkdirSync(dir, { recursive: true });
  const obsPath = join(dir, 'observation.json');
  const reqPath = join(dir, 'request.json');
  const decPath = join(dir, 'decision.json');
  const resPath = join(dir, 'response.json');
  writeJson(obsPath, lastObs);
  if (withRequest && lastBody) writeJson(reqPath, lastBody);
  writeJson(decPath, decision ?? {});
  if (withRequest && lastRaw) {
    try {
      const txt = Buffer.isBuffer(lastRaw) ? lastRaw.toString('utf8') : String(lastRaw);
      JSON.parse(txt);
      writeFileSync(resPath, txt);
    } catch { /* 非 JSON 就跳过 */ }
  }

  const tmpPng = join(dir, '_screen.png');
  try {
    writeFileSync(tmpPng, device.lastPng);
    const outPath = join(dir, `step-${pad2(n)}-annotated.webp`);
    const args = [ANNOTATOR, '--image', tmpPng, '--observation', obsPath,
      '--out', outPath, '--json'];
    if (withRequest && lastBody) args.push('--request', reqPath, '--decision', decPath);
    const r = execFileSync(VENV_PY, args, { encoding: 'utf8' });
    const info = JSON.parse(r.trim().split('\n').pop());
    return { ...info, file: outPath };
  } catch (e) {
    log('  标注失败: %s', (e.stderr || e.message || '').toString().trim().slice(0, 200));
    return null;
  } finally {
    rmSync(tmpPng, { force: true });
  }
}

// ---------- 引擎 B：写死的脚本（不使用模型） ----------
// 步骤类型：
//   {tapText:"搜索", label:"点搜索框"}   —— 按文本定位元素并点中心（确定性 UI 树查找）
//   {tapText:"7", optional:true}         —— optional：找不到就跳过，不判失败（用于一次性弹窗）
//   {waitFor:"点击加入购物车", timeout:8000} —— 轮询屏幕直到该文本出现（等待改为「等元素」）
//   {tap:[x,y]}                          —— 直接点固定坐标（最典型的「写死」）
//   {type:"洗衣液"}                       —— 走 Portal 键盘输入
//   {key:"back"|"enter"|"home"}           —— 按键
//   {swipe:[x1,y1,x2,y2,ms]}              —— 滑动
//   {sleep:2000}                          —— 固定等待（手工方案最大的一笔开销）
//   {expect:"购物车"}                      —— 断言屏幕上出现该文本，失败即视为脚本跑挂
function centerOf(node) {
  const b = node.bounds;
  return { x: Math.floor((b.left + b.right) / 2), y: Math.floor((b.top + b.bottom) / 2) };
}

function findText(obs, text) {
  const hit = (obs.elements || []).filter((e) => {
    const s = `${e.text || ''} ${e.label || ''}`;
    return s.includes(text);
  });
  if (!hit.length) return null;
  // 同一文本可能命中容器与子节点，取面积最小的那个（通常才是真正可点的控件）
  return hit.sort((a, b) => {
    const aa = (a.bounds.right - a.bounds.left) * (a.bounds.bottom - a.bounds.top);
    const bb = (b.bounds.right - b.bounds.left) * (b.bounds.bottom - b.bounds.top);
    return aa - bb;
  })[0];
}

const hasText = (obs, text) => (obs.elements || []).some(
  (e) => `${e.text || ''} ${e.label || ''}`.includes(text));

function describeScriptStep(st) {
  if (st.label) return st.label;
  if (st.tapText) return `${st.optional ? '?' : ''}Tap ${st.tapText}`;
  if (st.tap) return `Tap (${st.tap.join(', ')})`;
  if (st.type) return `Type ${st.type}`;
  if (st.key) return st.key.toUpperCase();
  if (st.swipe) return 'Swipe';
  if (st.sleep) return `Wait ${st.sleep}ms`;
  if (st.waitFor) return `Wait for "${st.waitFor}"`;
  if (st.expect) return `Expect ${st.expect}`;
  return '?';
}

async function runScriptCase(c, dir) {
  const timings = { modelMs: 0, actionMs: 0, observationMs: 0, waitMs: 0, wallMs: 0 };
  const steps = [];
  const annotated = [];
  const t0 = Date.now();
  let failed = null;
  let shotSeq = 0;
  let n = 0;

  for (const st of c.script) {
    n += 1;
    const s0 = Date.now();
    const label = describeScriptStep(st);
    let operation = 'WAIT';
    try {
      if (st.sleep) {
        operation = 'WAIT';
        await delay(st.sleep);
        timings.waitMs += Date.now() - s0;
      } else if (st.waitFor) {
        operation = 'WAIT';
        const deadline = Date.now() + (st.timeout ?? 8000);
        let ok = false;
        while (Date.now() < deadline) {
          const o0 = Date.now();
          const obs = await device.observe();
          timings.observationMs += Date.now() - o0;
          if (hasText(obs, st.waitFor)) { ok = true; break; }
          await delay(500);
        }
        if (!ok) throw new Error(`等待超时：${st.timeout ?? 8000}ms 内没有出现「${st.waitFor}」`);
      } else if (st.expect) {
        operation = 'ASSERT';
        const o0 = Date.now();
        const obs = await device.observe();
        timings.observationMs += Date.now() - o0;
        if (!hasText(obs, st.expect))
          throw new Error(`断言失败：屏幕上没有出现「${st.expect}」`);
      } else if (st.tapText) {
        operation = 'TAP_TEXT';
        const o0 = Date.now();
        const obs = await device.observe();
        timings.observationMs += Date.now() - o0;
        const node = findText(obs, st.tapText);
        if (!node) {
          if (st.optional) { operation = 'SKIP'; steps.push({
            n, status: 'skipped', operation: 'SKIP', target: null,
            label: `${label}（未出现，跳过）`, confidence: null, latencyMs: Date.now() - s0,
            scriptStep: st }); continue; }
          throw new Error(`定位失败：树上找不到文本「${st.tapText}」`);
        }
        const a0 = Date.now();
        const c0 = centerOf(node);
        await device.act({ type: 'tap', x: c0.x, y: c0.y });
        timings.actionMs += Date.now() - a0;
      } else if (st.tap) {
        operation = 'TAP';
        const a0 = Date.now();
        await device.act({ type: 'tap', x: st.tap[0], y: st.tap[1] });
        timings.actionMs += Date.now() - a0;
      } else if (st.type) {
        operation = 'TYPE_TEXT';
        const a0 = Date.now();
        await device.act({ type: 'type', text: st.type, clear: st.clear !== false });
        timings.actionMs += Date.now() - a0;
      } else if (st.key) {
        operation = st.key.toUpperCase();
        const a0 = Date.now();
        // enter 走键盘事件，back/home/recent 走系统全局按键
        await device.act(st.key === 'enter'
          ? { type: 'key', key: 'enter' }
          : { type: 'global', name: st.key });
        timings.actionMs += Date.now() - a0;
      } else if (st.swipe) {
        operation = 'SWIPE';
        const a0 = Date.now();
        await device.act({
          type: 'swipe', startX: st.swipe[0], startY: st.swipe[1],
          endX: st.swipe[2], endY: st.swipe[3], duration: st.swipe[4] ?? 400,
        });
        timings.actionMs += Date.now() - a0;
      } else {
        throw new Error('未知步骤: ' + JSON.stringify(st));
      }
    } catch (e) {
      failed = { step: n, label, error: String(e.message || e).slice(0, 300) };
    }

    const elapsed = Date.now() - s0;
    steps.push({
      n, status: failed?.step === n ? 'error' : 'action',
      operation, target: null, label, confidence: null, latencyMs: elapsed, scriptStep: st,
    });
    log('  step %d [%s] %s  %sms%s', n, failed?.step === n ? 'err' : 'ok',
      label, elapsed, failed?.step === n ? `  ← ${failed.error}` : '');

    // 每步都留一张「动作前」的标注图 + 一张「动作后」的截图（与 Jev 引擎同口径）
    try {
      // 脚本的第一步可能是纯 sleep，此时还没有任何 observation，
      // 标注器会拿到 None 而崩；这里补一次观察。
      if (!lastObs) await device.observe();
      await device.screenshot();
      const sd = join(dir, `step-${pad2(n)}`);
      const info = annotateStep(sd, n, { operation, target: null, label, confidence: null },
        { withRequest: false });
      if (info) annotated.push({ n, operation, target: null, ...info });
      const f = await saveShot(device.lastPng, join(dir, `after-${pad2(++shotSeq)}`));
      log('  截图 %s', f.split(/[\\/]/).pop());
    } catch (e) {
      log('  截图失败: %s', e.message);
    }

    if (failed) {
      // 手工脚本不会自愈：一旦定位/断言失败就停在这里，这正是它与决策模型的差别
      log('  ⚠ 脚本中断：%s', failed.error);
      break;
    }
  }

  timings.wallMs = Date.now() - t0;
  return { status: failed ? 'script_failed' : (DRY ? 'preview' : 'done'), steps: n,
    decision: failed ? { status: 'blocked', reason: failed.error } : { status: 'done', reason: '脚本按预期跑完' },
    timings, failed, stepsDetail: steps, annotated };
}

// ---------- 主循环 ----------
const index = [];
for (const c of cases) {
  const dir = join(OUT, c.id);

  // frozen=true：产物已归档（例如失败样本），不重跑，只从已有 case.json 重建索引条目
  if (c.frozen) {
    const saved = existsSync(join(dir, 'case.json'))
      ? JSON.parse(readFileSync(join(dir, 'case.json'), 'utf8')) : null;
    log('\n=== [%s] 已冻结，跳过重跑%s', c.id, saved ? '' : '（且没有历史产物）');
    if (saved) {
      index.push({
        ...saved, frozen: true,
        finalApp: saved.final?.app, passed: saved.final?.passed,
        checks: saved.final?.checks, cost: (saved.decisions ?? []).reduce((s, x) => s + (x.usage?.cost ?? 0), 0),
        wallMs: saved.result?.timings?.wallMs ?? 0,
        modelMs: saved.result?.timings?.modelMs ?? 0,
        waitMs: saved.result?.timings?.waitMs ?? 0,
        steps: saved.result?.steps ?? 0,
        status: saved.result?.status ?? '?',
      });
    }
    continue;
  }

  // 重跑同一个案例时，先把上一次的产物整体改名归档。
  // 用 rename 而不是删除，是为了在 Agent 沙箱（批量删除会被安全策略拦下）里也能跑通；
  // 归档目录名带时间戳，事后可以人工比对两轮差异。
  if (existsSync(dir)) {
    const stamp = new Date().toISOString().slice(5, 19).replace(/[-:T]/g, '');
    let bak = `${dir}.old-${stamp}`;
    for (let i = 2; existsSync(bak); i++) bak = `${dir}.old-${stamp}-${i}`;
    renameSync(dir, bak);
    log('  （旧产物已归档为 %s）', bak.split(/[\\/]/).pop());
  }
  mkdirSync(dir, { recursive: true });
  const engine = c.engine || 'jev';
  log('\n=== [%s] (%s) %s', c.id, engine, c.title);

  await adb(['shell', 'input', 'keyevent', '3']);
  await delay(1000);

  if (c.app) {
    log('  启动 %s', c.app);
    await device.act({ type: 'open-app', packageName: c.app });
    await delay(LAUNCH_SETTLE_MS);
  } else if (c.app === null && c.openApp) {
    await device.act({ type: 'open-app', packageName: c.openApp });
    await delay(LAUNCH_SETTLE_MS);
  }

  // prelude：把设备恢复到两条臂共同的起始状态（不计入该臂的步数与耗时）。
  // 对照组实验最怕的就是「上一臂改了状态、下一臂从不同起点出发」——
  // 比如多多买菜的购物车里还留着上一臂加进去的商品。
  let preludeFailed = null;
  if (c.prelude?.length && !DRY) {
    log('  [prelude] 重置起始状态（%d 步，不计入本臂）', c.prelude.length);
    const preDir = join(dir, 'prelude');
    mkdirSync(preDir, { recursive: true });
    const pr = await runScriptCase({ id: c.id + '-prelude', script: c.prelude }, preDir);
    preludeFailed = pr.failed;
    log('  [prelude] %s', pr.failed ? ('失败: ' + pr.failed.error) : '完成');
  }

  const steps = [];
  const annotated = [];
  let shotSeq = 0;
  let result;

  if (engine === 'script') {
    if (DRY) {
      log('  (--dry 模式下不跑脚本)');
      result = { status: 'preview', steps: 0, timings: { wallMs: 0 }, decision: null };
    } else {
      const r = await runScriptCase(c, dir);
      result = r;
      steps.push(...r.stepsDetail);
      annotated.push(...r.annotated);
      shotSeq = r.steps;
    }
  } else {
    let n = 0;
    result = await runAgent({
      device, policy, goal: c.goal, execute: !DRY, maxSteps: c.steps ?? 12,
      // 要输入的字符串由调用方注入（模型只决定「该打字」+ 选哪一条），
      // 这是上游的刻意设计：模型永远不自由编造文本。缺了它，TYPE_TEXT 那一步会返回 needs_input。
      texts: c.texts ?? [],
      onStep: async (d) => {
        n += 1;
        steps.push({
          n, status: d.status, operation: d.operation, target: d.target,
          label: d.label, confidence: d.confidence,
          targetConfidence: d.targetConfidence, latencyMs: d.latencyMs,
          probabilities: d.operationProbabilities, usage: d.usage,
        });
        log('  step %d [%s] %s %s conf=%s %sms',
          n, d.status, d.operation, d.target ?? '', d.confidence, d.latencyMs);
        try {
          await device.screenshot();
          const sd = join(dir, `step-${pad2(n)}`);
          const info = annotateStep(sd, n, d);
          if (info) annotated.push({ n, operation: d.operation, target: d.target, ...info });
        } catch (e) {
          log('  标注失败: %s', (e.stderr || e.message || '').toString().trim().slice(0, 200));
        }
      },
      onObservation: async (o) => {
        if (DRY) return;
        try {
          await device.screenshot();
          await saveShot(device.lastPng, join(dir, `after-${pad2(++shotSeq)}`));
          log('  动作后截图 after-%s (app=%s)', pad2(shotSeq), o.observation.phone.packageName || '-');
        } catch (e) { log('  截图失败: %s', e.message); }
      },
    });
  }

  // 收尾：最后再观察一次，用于「终态校验」与整体截图
  await delay(1200);
  const finalObs = await device.observe();
  await device.screenshot();
  const finalDir = join(dir, 'final');
  const finalAnn = annotateStep(finalDir, steps.length + 1,
    { operation: result.decision?.status || result.status, target: null, label: 'final state' },
    { withRequest: false });
  const finalShot = await saveShot(device.lastPng, join(dir, 'final-state'));

  const checks = (c.verify || []).map((kw) => ({ keyword: kw, pass: hasText(finalObs, kw) }));

  const summary = {
    ...c, engine, at: new Date().toISOString(), device: ready,
    result: {
      status: result.status, steps: result.steps, timings: result.timings,
      reason: result.decision?.reason ?? null, failed: result.failed ?? null,
    },
    decisions: steps,
    annotated,
    final: {
      app: finalObs.phone.packageName, activity: finalObs.phone.activityName,
      elements: finalObs.elements.length, checks,
      passed: checks.length ? checks.every((x) => x.pass) : null,
      screenshot: finalShot, annotated: finalAnn?.file ?? null,
    },
    dir,
  };
  writeJson(join(dir, 'case.json'), summary);

  const t = result.timings || {};
  index.push({
    id: c.id, title: c.title, tone: c.tone, engine, pair: c.pair ?? null,
    goal: c.goal, why: c.why, expect: c.expect, script: c.script ?? null,
    status: result.status, steps: result.steps,
    wallMs: t.wallMs ?? 0, modelMs: t.modelMs ?? 0, actionMs: t.actionMs ?? 0,
    observationMs: t.observationMs ?? 0, waitMs: t.waitMs ?? 0,
    modelCalls: t.modelCalls ?? 0,
    cost: steps.reduce((s, x) => s + (x.usage?.cost ?? 0), 0),
    finalApp: finalObs.phone.packageName, passed: summary.final.passed,
    checks, failed: result.failed ?? null,
    finalScreenshot: finalShot, dir,
  });
  log('  => %s  步数 %d  总耗时 %ss  终态校验=%s',
    result.status, result.steps, Math.round((t.wallMs ?? 0) / 100) / 10,
    summary.final.passed === null ? 'n/a' : (summary.final.passed ? '通过' : '未通过'));
}

// 允许分多轮跑（先跑 jev 臂、再补 script 臂），因此与已有 index.json 合并，按 id 覆盖
const indexPath = join(OUT, 'index.json');
let prev = [];
try { prev = JSON.parse(readFileSync(indexPath, 'utf8')).cases ?? []; } catch { /* 首次跑 */ }
const order = JSON.parse(readFileSync(join(HERE, 'cases.json'), 'utf8')).cases.map((x) => x.id);
const merged = [...prev.filter((p) => !index.some((n) => n.id === p.id)), ...index]
  .sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
writeJson(indexPath, { at: new Date().toISOString(), cases: merged });
log('\n汇总: %s（本轮 %d 条，累计 %d 条）', indexPath, index.length, merged.length);
for (const r of index) {
  console.log(`  ${r.id.padEnd(24)} ${String(r.engine).padEnd(7)} ${String(r.status).padEnd(13)} `
    + `${String(r.steps).padStart(2)} 步  ${String(r.wallMs).padStart(6)}ms  校验=${r.passed === null ? 'n/a' : (r.passed ? 'ok' : 'FAIL')}`);
}
