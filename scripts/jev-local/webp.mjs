/**
 * webp.mjs — Node 侧截图落盘助手（项目约定：所有 PNG 截图一律转 WebP）。
 *
 * 为什么不在 Node 里直接编码 WebP：
 *   能编 WebP 的库（sharp / @jsquash/webp）要么是 native 二进制、要么要额外
 *   装 wasm 包；而托管 Python venv 里已经有 Pillow + WebP 编码器。
 *   所以这里只做一件事：把 PNG 字节喂给 `scripts/tools/webp_shot.py pipe`。
 *
 * 拿不到 Pillow 时自动退化为写 PNG，绝不因为压缩失败而丢掉截图。
 */

import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, '..', '..');
const TOOL = join(ROOT, 'scripts', 'tools', 'webp_shot.py');

/** 装了 Pillow 的解释器；可用 WEBP_PYTHON 覆盖 */
export const PYTHON =
  process.env.WEBP_PYTHON ||
  'C:/Users/zongy/.workbuddy/binaries/python/envs/default/Scripts/python.exe';

export const WEBP_QUALITY = Number(process.env.WEBP_QUALITY || 78);
export const WEBP_MAX_WIDTH = Number(process.env.WEBP_MAX_WIDTH || 720); // 0 = 不缩放

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** PNG 缓冲区 -> WebP 缓冲区；失败返回 null（调用方退化为 PNG） */
export function pngToWebp(png, { quality = WEBP_QUALITY, maxWidth = WEBP_MAX_WIDTH } = {}) {
  return new Promise((done) => {
    if (!existsSync(TOOL)) return done(null);
    const args = [TOOL, 'pipe', '-', '--quality', String(quality)];
    if (maxWidth) args.push('--max-width', String(maxWidth));
    const p = spawn(PYTHON, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    const chunks = [];
    let err = '';
    p.stdout.on('data', (c) => chunks.push(c));
    p.stderr.on('data', (c) => (err += c));
    p.on('error', () => done(null));
    p.on('close', (code) => {
      const buf = Buffer.concat(chunks);
      if (code === 0 && buf.length > 12 && buf.subarray(0, 4).toString() === 'RIFF') done(buf);
      else {
        if (err) process.stderr.write('[webp] ' + err.trim() + '\n');
        done(null);
      }
    });
    p.stdin.end(png);
  });
}

/**
 * 保存一张截图到 outBase（不含扩展名），返回实际写出的文件名。
 * 有 Pillow 写 .webp，否则写 .png；并清掉另一扩展名的陈旧文件。
 */
export async function saveShot(png, outBase, opts = {}) {
  if (!(Buffer.isBuffer(png) && png.subarray(0, 8).equals(PNG_MAGIC))) {
    throw new Error('saveShot 只接受 PNG 缓冲区');
  }
  mkdirSync(dirname(outBase), { recursive: true });
  const webp = await pngToWebp(png, opts);
  const ext = webp ? 'webp' : 'png';
  const file = `${outBase}.${ext}`;
  const { unlinkSync } = await import('node:fs');
  for (const other of ['png', 'webp']) {
    if (other === ext) continue;
    const stale = `${outBase}.${other}`;
    if (existsSync(stale)) unlinkSync(stale);
  }
  writeFileSync(file, webp || png);
  return file;
}
