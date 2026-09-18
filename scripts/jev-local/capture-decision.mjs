// 抓取一次真实的 Jev 决策：完整请求上下文 + 模型原始返回
// 用法: node scripts/jev-local/capture-decision.mjs "目标文字"
import { mkdirSync, writeFileSync } from 'node:fs';
import { PortalDevice, makeOpenRouterRequest } from './portal-device.mjs';

const OUT = 'D:/Projects/Agents/mobile-agent/artifacts/jev-capture';
const REPO = new URL('../../references/mobile-jev/scripts/mobile-agent/', import.meta.url);

process.loadEnvFile('D:/Projects/Agents/mobile-agent/.env');

const { TypeSafePolicy } = await import(new URL('policy.mjs', REPO).href);
const { pooledRequest } = await import(new URL('http.mjs', REPO).href);

const GOAL = process.argv[2] || '打开拼多多，进入多多买菜，找到鸡蛋商品';

const device = new PortalDevice();
await device.assertReady();
const [observation, apps] = await Promise.all([device.observe(), device.listApps()]);

console.log('[observe] app=%s elements=%d apps=%d',
  observation.phone.packageName || '(none)', observation.elements.length, apps.length);

mkdirSync(OUT, { recursive: true });
writeFileSync(`${OUT}/observation.json`, JSON.stringify(observation, null, 2));

let captured = null;
const base = makeOpenRouterRequest(pooledRequest);
const policy = new TypeSafePolicy({
  apiKey: process.env.OPENROUTER_API_KEY,
  model: '~typesafe/jev-latest',
  request: async (opts) => {
    const res = await base(opts);
    captured = { url: opts.url, body: opts.body, raw: res.toString('utf8'), timing: res.timing };
    return res;
  },
});

const decision = await policy.decide({ goal: GOAL, observation, history: [], texts: [], apps });

writeFileSync(`${OUT}/request.json`, JSON.stringify(captured.body, null, 2));
writeFileSync(`${OUT}/response.json`, captured.raw);
writeFileSync(`${OUT}/decision.json`, JSON.stringify(decision, null, 2));
writeFileSync(
  `${OUT}/meta.json`,
  JSON.stringify({ goal: GOAL, url: captured.url, timing: captured.timing }, null, 2),
);

console.log('[decide] %s -> %s (conf %s / targetConf %s)',
  decision.operation, decision.label || '-', decision.confidence, decision.targetConfidence);
console.log('[decide] status=%s choice=%s model=%s latency=%sms',
  decision.status, decision.choice, decision.responseModel, decision.latencyMs);
console.log('[size] request=%d bytes  response=%d bytes',
  Buffer.byteLength(JSON.stringify(captured.body)), captured.raw.length);
console.log('[usage] %s', JSON.stringify(decision.usage));
