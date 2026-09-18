// 本地 Portal(USB/ADB) 设备适配器 + OpenRouter 决策端点封装
// 被 scripts/jev-local/runner.mjs / case-runner.mjs 和 references/mobile-jev 的 studio 共用
import { spawn } from 'node:child_process';
import {
  summarizeState,
  StaleObservationError,
} from '../../references/mobile-jev/scripts/mobile-agent/device.mjs';

const PORTAL = 'content://com.mobilerun.portal';

// ---------- 观察新鲜度校验 ----------
// 与上游 MobilerunDevice.act 的 assertFresh 同构：决策是基于某一次观察做出来的，
// 派发动作前必须确认「屏幕还是那一屏」，否则应当抛 StaleObservationError，
// 让 runAgent 重新观察 + 重新决策，而不是拿旧坐标硬点。
// 缺了这层，Agent 会在页面切换的瞬间点到错误的位置（本适配器早期版本的崩溃就源于此）。
function targetMeaning(observation, id) {
  const target = observation.elements.find((element) => element.id === id);
  if (!target) return null;
  const { bounds, ...meaning } = target;
  return JSON.stringify({
    ...meaning,
    content: observation.elements
      .filter((e2) => e2.id.startsWith(id + '.'))
      .map(({ id: cid, text, label, resourceId, editable, enabled, checked, selected }) =>
        ({ id: cid, text, label, resourceId, editable, enabled, checked, selected })),
  });
}

function assertFresh(current, expected, action, maxAgeMs = 30_000) {
  if (
    !expected
    || current.deviceId !== expected.deviceId
    || !Number.isFinite(expected.observedAt)
    || Date.now() - expected.observedAt > maxAgeMs
    || expected.observedAt > Date.now()
    || current.phone.packageName !== expected.phone?.packageName
    || JSON.stringify(current.screen) !== JSON.stringify(expected.screen)
  ) {
    throw new StaleObservationError(
      'Screen changed or observation expired; observe and decide again.',
    );
  }
  let fresh;
  if (action.type === 'tap-element') {
    fresh = targetMeaning(expected, action.elementId) !== null
      && targetMeaning(current, action.elementId) === targetMeaning(expected, action.elementId);
  } else if (['type', 'clear', 'key'].includes(action.type) && expected.phone.inputElementId) {
    const id = expected.phone.inputElementId;
    fresh = current.phone.isEditable
      && current.phone.inputElementId === id
      && targetMeaning(current, id) === targetMeaning(expected, id);
  } else if (['type', 'clear', 'key'].includes(action.type)) {
    // Portal 的 phone_state 不提供 inputElementId（上游云 API 才有），
    // 本地退一步：至少要求「焦点仍在可编辑元素上」，避免往空处打字。
    fresh = !expected.phone.isEditable || current.phone.isEditable;
  } else if (action.type === 'global' && action.name === 'home') {
    fresh = true; // HOME 与页面内容无关，不受动画影响
  } else {
    fresh = true;
  }
  if (!fresh) {
    throw new StaleObservationError(
      'Target changed meaning since it was observed; observe and decide again.',
    );
  }
}

export async function adb(args, { binary = false, timeout = 30000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn('adb', args, { windowsHide: true });
    const chunks = [];
    let size = 0;
    const timer = setTimeout(() => child.kill('SIGKILL'), timeout);
    child.stdout.on('data', (c) => {
      size += c.length;
      if (size > 30 * 1024 * 1024) child.kill('SIGKILL');
      else chunks.push(c);
    });
    child.stderr.on('data', () => {}); // adb daemon 提示走 stderr，忽略
    child.on('error', (e) => { clearTimeout(timer); reject(e); });
    child.on('close', (code) => {
      clearTimeout(timer);
      const buf = Buffer.concat(chunks);
      if (code !== 0) return reject(new Error(`adb ${args.join(' ')} exit ${code}`));
      resolve(binary ? buf : buf.toString('utf8'));
    });
  });
}

// Portal ContentProvider 返回: Row: 0 result={...}，result 可能是双重编码字符串或直接对象
export async function portalGet(uri) {
  const out = await adb(['shell', 'content', 'query', '--uri', `${PORTAL}/${uri}`]);
  const m = out.match(/result=(.*)/s);
  if (!m) throw new Error(`Portal ${uri} 无 result: ${out.slice(0, 200)}`);
  let val = JSON.parse(m[1]).result;
  if (typeof val === 'string') val = JSON.parse(val);
  return val;
}

const tap = (x, y) => adb(['shell', 'input', 'tap', String(x), String(y)]);
const swipe = (x1, y1, x2, y2, ms) =>
  adb(['shell', 'input', 'swipe', String(x1), String(y1), String(x2), String(y2), String(ms ?? 300)]);
const keyevent = (code) => adb(['shell', 'input', 'keyevent', String(code)]);
const launchApp = (pkg) =>
  adb(['shell', 'monkey', '-p', pkg, '-c', 'android.intent.category.LAUNCHER', '1']);

async function portalType(text, clear) {
  const b64 = Buffer.from(text, 'utf8').toString('base64');
  const bind = ['--bind', `base64_text:s:${b64}`];
  if (!clear) bind.push('--bind', 'clear:b:false');
  await adb(['shell', 'content', 'insert', '--uri', `${PORTAL}/keyboard/input`, ...bind]);
}
const portalClear = () => adb(['shell', 'content', 'insert', '--uri', `${PORTAL}/keyboard/clear`]);

export class PortalDevice {
  constructor(deviceId = 'd5652109') {
    this.deviceId = deviceId;
    this.screen = { width: 1080, height: 2280 };
    this.installedApps = [];
  }

  async assertReady() {
    const out = await adb(['devices', '-l']);
    if (!new RegExp(`${this.deviceId}\\s+device\\b`).test(out))
      throw new Error(`设备 ${this.deviceId} 未就绪`);
    const pong = await adb(['shell', 'content', 'query', '--uri', `${PORTAL}/ping`]);
    if (!pong.includes('pong')) throw new Error('Portal 无响应: ' + pong.slice(0, 120));
    return { id: this.deviceId, name: 'OnePlus6 (USB Portal)', state: 'ready' };
  }

  async observe() {
    const [phone, tree] = await Promise.all([
      portalGet('phone_state'),
      portalGet('a11y_tree_full?filter=false'),
    ]);
    const raw = {
      device_context: { screen_bounds: this.screen },
      phone_state: phone,
      a11y_tree: tree,
    };
    return summarizeState(raw, this.deviceId);
  }

  async screenshot() {
    const png = await adb(['exec-out', 'screencap', '-p'], { binary: true });
    if (!png.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])))
      throw new Error('截图不是 PNG');
    this.lastPng = png; // 缓存最近一帧，方便调用方（案例运行器/标注）复用同一张图
    return png;
  }

  async listApps() {
    const apps = await portalGet('packages');
    this.installedApps = apps
      .filter((a) => typeof a.packageName === 'string' && typeof a.label === 'string')
      .map(({ packageName, label }) => ({ packageName, label }));
    return this.installedApps;
  }

  async act(action, { expected, maxAgeMs = 30_000 } = {}) {
    if (!action || typeof action !== 'object') throw new Error('action 必须是对象');
    // open-app 与页面内容无关，和上游一样不需要重新观察
    if (action.type === 'open-app') {
      if (!this.installedApps.some((a) => a.packageName === action.packageName))
        throw new Error('应用不在已安装列表中');
      return launchApp(action.packageName);
    }
    // 其余动作：先重新观察，确认屏幕没有变，再派发
    const current = await this.observe();
    if (expected) assertFresh(current, expected, action, maxAgeMs);

    switch (action.type) {
      case 'tap':
        return tap(action.x, action.y);
      case 'tap-element': {
        if (!expected) throw new Error('tap-element 必须带上它所属的 observation');
        const node = current.elements.find((e) => e.id === action.elementId);
        if (!node?.enabled || !(node.clickable || node.editable))
          throw new Error('元素不可操作或已消失');
        const x = Math.floor((node.bounds.left + node.bounds.right) / 2);
        const y = Math.floor((node.bounds.top + node.bounds.bottom) / 2);
        return tap(x, y);
      }
      case 'swipe':
        return swipe(action.startX, action.startY, action.endX, action.endY, action.duration ?? 300);
      case 'type':
        return portalType(action.text, action.clear ?? false);
      case 'clear':
        return portalClear();
      case 'key':
        if (action.key !== 'enter') throw new Error('仅支持 enter');
        return keyevent(66);
      case 'global':
        return keyevent({ back: 4, home: 3, recent: 187 }[action.name]);
      default:
        throw new Error(`不支持的动作: ${action.type}`);
    }
  }

  async sendSystemKey(name) {
    return keyevent({ back: 4, home: 3, recent: 187 }[name.toLowerCase()] ?? 3);
  }
}

// ---------- OpenRouter 决策端点 ----------
// 注意：必须把 policy 传入的 url 解构丢弃，否则会用 TypeSafe 官方地址覆盖 OpenRouter 端点
export function makeOpenRouterRequest(pooledRequest) {
  return function openRouterRequest({ url, apiKey, ...rest }) {
    return pooledRequest({
      url: 'https://openrouter.ai/api/alpha/decisions',
      apiKey,
      ...rest,
    });
  };
}
