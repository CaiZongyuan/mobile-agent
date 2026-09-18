// 采集一屏的完整证据链，并渲染成「标注截图」：
//   截图 + observation（解析到的元素）+ Jev 的 request(state/questions) + response + decision
//
// 产物（default artifacts/screen-explain/）：
//   observation.json          无障碍树裁剪后的元素（含 bounds）
//   request.json              Jev 实际收到的 state + questions
//   response.json             模型原始返回（完整概率分布）
//   decision.json             归一化后的决策（含 confidence / 选中项的 action）
//   screen-annotated.webp     标注截图（元素框 + 编号 + 模型选中准星 + 信息带）
//   screen-annotated-thumb.webp
//
// 用法:
//   node scripts/jev-local/explain-screen.mjs [--goal "目标"] [--out 目录] [--no-decide]
//   --no-decide  只观察不调用模型（离线也能出图，但标注里没有「候选操作」和选中的那一层）
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { PortalDevice, makeOpenRouterRequest } from './portal-device.mjs';

const ROOT = 'D:/Projects/Agents/mobile-agent';
const REPO = new URL('../../references/mobile-jev/scripts/mobile-agent/', import.meta.url);
const ANNOTATOR = join(ROOT, 'scripts/tools/annotate_screen.py');
// Pillow 只装在托管 venv 里（基座 python 没有 WebP 编码器）
const VENV_PY = 'C:/Users/zongy/.workbuddy/binaries/python/envs/default/Scripts/python.exe';

process.loadEnvFile(join(ROOT, '.env'));

const { TypeSafePolicy } = await import(new URL('policy.mjs', REPO).href);
const { pooledRequest } = await import(new URL('http.mjs', REPO).href);

const argOf = (flag, def) => {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : def;
};
const GOAL = argOf('--goal', '打开拼多多，进入多多买菜，在肉蛋分类里找到鸡蛋商品');
const OUT = argOf('--out', join(ROOT, 'artifacts/screen-explain'));
const DECIDE = !process.argv.includes('--no-decide');

mkdirSync(OUT, { recursive: true });

const write = (name, data) =>
  writeFileSync(join(OUT, name), typeof data === 'string' ? data : JSON.stringify(data, null, 2));

const device = new PortalDevice();
const ready = await device.assertReady();
console.log('[device] %s state=%s', ready.id, ready.state);

// ---- 1. 截图（渲染器需要真实像素，先落 PNG 再删）
const png = await device.screenshot();
const tmpPng = join(OUT, '_screen.png');
writeFileSync(tmpPng, png);
console.log('[shot] %d KB', Math.round(png.length / 1024));

// ---- 2. 观察
const [observation, apps] = await Promise.all([device.observe(), device.listApps()]);
write('observation.json', observation);
console.log('[observe] app=%s elements=%d apps=%d',
  observation.phone.packageName || '(none)', observation.elements.length, apps.length);

// ---- 3. 决策（顺带拦下模型真正收到的请求体）
let decision = null;
if (DECIDE) {
  let captured = null;
  const base = makeOpenRouterRequest(pooledRequest);
  const policy = new TypeSafePolicy({
    apiKey: process.env.OPENROUTER_API_KEY,
    model: '~typesafe/jev-latest',
    request: async (opts) => {
      const res = await base(opts);
      captured = { url: opts.url, body: opts.body, raw: res.toString('utf8') };
      return res;
    },
  });
  decision = await policy.decide({ goal: GOAL, observation, history: [], texts: [], apps });
  write('request.json', captured.body);
  write('response.json', captured.raw);
  write('decision.json', decision);
  console.log('[decide] %s -> %s (conf %s / targetConf %s) %sms',
    decision.operation, decision.label || '-', decision.confidence,
    decision.targetConfidence ?? '-', decision.latencyMs);
}

// ---- 4. 渲染标注截图
const annotated = join(OUT, 'screen-annotated.webp');
const args = [
  ANNOTATOR, '--image', tmpPng, '--observation', join(OUT, 'observation.json'),
  '--out', annotated, '--thumb', '540', '--json',
];
if (DECIDE) {
  args.push('--request', join(OUT, 'request.json'), '--decision', join(OUT, 'decision.json'));
}
let info = null;
try {
  const out = execFileSync(VENV_PY, args, { encoding: 'utf8' });
  info = JSON.parse(out.trim().split('\n').pop());
  console.log('[annotate] 解析 %d → 候选 %d（%s） 选中 %s',
    info.parsed_elements, info.candidates, JSON.stringify(info.color_counts), info.chosen || '-');
  console.log('[annotate] %s (%s KB)', annotated, info.size_kb);
  if (info.unmatched_indices?.length)
    console.warn('[annotate] ⚠ 未能反查的 index: %s', info.unmatched_indices.join(','));
} catch (e) {
  console.error('[annotate] 渲染失败:', (e.stderr || e.message || '').toString().trim().slice(0, 400));
} finally {
  rmSync(tmpPng, { force: true });
}

write('summary.json', {
  goal: GOAL, at: new Date().toISOString(), device: ready,
  observation: {
    app: observation.phone.packageName,
    elements: observation.elements.length,
    screen: observation.screen,
    fingerprint: observation.fingerprint,
  },
  decision: decision && {
    status: decision.status, operation: decision.operation, target: decision.target,
    confidence: decision.confidence, targetConfidence: decision.targetConfidence,
    label: decision.label, latencyMs: decision.latencyMs, usage: decision.usage,
  },
  annotate: info,
});

console.log('\n产物目录:', OUT);
