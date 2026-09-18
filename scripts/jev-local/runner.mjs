// 本地 Jev 运行器：Portal(USB/ADB) 设备适配器 + OpenRouter jev-latest 决策
// 用法: node runner.mjs [--goal "目标"] [--steps 14] [--preview] [--explain] [--out artifacts目录]
//   --explain  每步额外产出标注截图 + 该步的 observation/request/decision（见 explain/step-NN/）
import { execFile as execFileCb, spawn, execFileSync } from 'node:child_process';
import { promisify } from 'node:util';
import { setTimeout as delay } from 'node:timers/promises';
import { writeFileSync, mkdirSync, appendFileSync, statSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const execFile = promisify(execFileCb);
const REPO = 'D:/Projects/Agents/mobile-agent/references/mobile-jev';
const mod = (p) => import(pathToFileURL(join(REPO, 'scripts/mobile-agent', p)).href);

const { runAgent } = await mod('agent.mjs');
const { TypeSafePolicy } = await mod('policy.mjs');
const { pooledRequest } = await mod('http.mjs');
import { PortalDevice, makeOpenRouterRequest } from './portal-device.mjs';
import { saveShot } from './webp.mjs';

process.loadEnvFile('D:/Projects/Agents/mobile-agent/.env');

// ---------- 参数 ----------
const argOf = (flag, def) => {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : def;
};
const GOAL = argOf('--goal', '打开拼多多，进入多多买菜，在肉蛋分类里找到鸡蛋商品');
const MAX_STEPS = Number(argOf('--steps', '14'));
const PREVIEW = process.argv.includes('--preview');
// --explain: 每步额外产出一张「标注截图」+ 该步的 observation / request / decision
const EXPLAIN = process.argv.includes('--explain');
const VENV_PY = 'C:/Users/zongy/.workbuddy/binaries/python/envs/default/Scripts/python.exe';
const ANNOTATOR = 'D:/Projects/Agents/mobile-agent/scripts/tools/annotate_screen.py';
const OUT = argOf('--out', 'D:/Projects/Agents/mobile-agent/artifacts/jev-run');
mkdirSync(OUT, { recursive: true });
const tracePath = join(OUT, 'trace.jsonl');
writeFileSync(tracePath, '');

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const trace = (obj) => appendFileSync(tracePath, JSON.stringify(obj) + '\n');

// ---------- 主流程 ----------
const device = new PortalDevice();

// 记录「模型决策时看到的那一屏」：runAgent 每步都是先 device.observe() 再 policy.decide()，
// 所以这里抓到的是最后一次 observe 的结果，正好与本次请求体同源。
// （act() 内部为 tap-element 复观察时也会走这里，但那时 onStep 早已触发，不影响）
const rawObserve = device.observe.bind(device);
let lastObs = null;
device.observe = async () => {
  const o = await rawObserve();
  lastObs = o;
  return o;
};

// 拦下模型真正收到的请求体
let lastBody = null;
const baseRequest = makeOpenRouterRequest(pooledRequest);
const policy = new TypeSafePolicy({
  apiKey: process.env.OPENROUTER_API_KEY,
  model: '~typesafe/jev-latest',
  request: EXPLAIN
    ? async (opts) => {
        lastBody = opts.body;
        return baseRequest(opts);
      }
    : baseRequest,
});

const pad2 = (n) => String(n).padStart(2, '0');

// 为一步产出「标注截图」+ 该步完整的证据链（observation / request / decision）
async function explainStep(n, d) {
  const dir = join(OUT, 'explain', 'step-' + pad2(n));
  mkdirSync(dir, { recursive: true });
  const obsPath = join(dir, 'observation.json');
  const reqPath = join(dir, 'request.json');
  const decPath = join(dir, 'decision.json');
  writeFileSync(obsPath, JSON.stringify(lastObs, null, 2));
  if (lastBody) writeFileSync(reqPath, JSON.stringify(lastBody, null, 2));
  writeFileSync(decPath, JSON.stringify(d, null, 2));

  const tmpPng = join(dir, '_screen.png');
  try {
    writeFileSync(tmpPng, await device.screenshot());
    const out = join(dir, 'step-annotated.webp');
    const args = [ANNOTATOR, '--image', tmpPng, '--observation', obsPath, '--out', out, '--json'];
    if (lastBody) args.push('--request', reqPath, '--decision', decPath);
    const r = execFileSync(VENV_PY, args, { encoding: 'utf8' });
    const info = JSON.parse(r.trim().split('\n').pop());
    log(`  标注: ${out} 解析 ${info.parsed_elements} → 候选 ${info.candidates} 选中 ${info.chosen || '-'}`);
    trace({ type: 'explain', n, dir, ...info });
  } catch (e) {
    log('  标注失败:', (e.stderr || e.message || '').toString().trim().slice(0, 300));
  } finally {
    rmSync(tmpPng, { force: true });
  }
}

const stepNo = { n: 0 };
const t0 = Date.now();

log('目标:', GOAL);
log('模式:', PREVIEW ? 'preview（只决策不执行）' : 'execute');
const ready = await device.assertReady();
log('设备就绪:', JSON.stringify(ready));
trace({ type: 'meta', goal: GOAL, mode: PREVIEW ? 'preview' : 'execute', device: ready, t0 });

const result = await runAgent({
  device,
  policy,
  goal: GOAL,
  execute: !PREVIEW,
  maxSteps: MAX_STEPS,
  onStep: async (d) => {
    stepNo.n++;
    log(
      `step ${stepNo.n} [${d.status}] ${d.operation} ${d.target ?? ''} ` +
        `conf=${d.confidence} model=${d.latencyMs}ms`,
    );
    trace({
      type: 'step', n: stepNo.n, status: d.status, operation: d.operation, target: d.target,
      choice: d.choice, confidence: d.confidence, targetConfidence: d.targetConfidence,
      latencyMs: d.latencyMs, requestedModel: d.requestedModel, responseModel: d.responseModel,
      usage: d.usage, probabilities: d.operationProbabilities, label: d.label,
      elapsedMs: Date.now() - t0,
    });
    if (EXPLAIN) await explainStep(stepNo.n, d);
  },
  onAction: async (a) => {
    log(`  → 执行: ${a.label} (${a.executedMs}ms)`);
    trace({ type: 'action', n: stepNo.n, label: a.label, executedMs: a.executedMs, action: a.action });
    try {
      const png = await device.screenshot();
      // 项目约定：截图统一压成 WebP（1080x2280 PNG ~1.7MB -> ~180KB）
      const file = await saveShot(png, join(OUT, `step-${String(stepNo.n).padStart(2, '0')}`));
      const kbSize = (statSync(file).size / 1024).toFixed(0);
      log(`  截图: ${file} (${kbSize} KB)`);
    } catch (e) {
      log('  截图失败:', e.message);
    }
  },
  onObservation: async (o) => {
    trace({
      type: 'observation', n: stepNo.n, screenChanged: o.screenChanged,
      app: o.observation.phone.packageName, elements: o.observation.elements.length,
    });
  },
});

log('结果:', JSON.stringify(result.timings), 'status=' + result.status);
trace({ type: 'result', status: result.status, steps: result.steps, timings: result.timings });
console.log('\n=== 最终状态:', result.status, '| 步数:', result.steps, '| 总耗时', Math.round(result.timings.wallMs / 100) / 10 + 's');
if (result.decision?.reason) console.log('原因:', result.decision.reason);
console.log('trace:', tracePath);
