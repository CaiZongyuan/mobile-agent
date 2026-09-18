"""webp_shot.py — 设备截图 / 图片转 WebP 的统一工具。

项目约定：**所有 PNG 截图一律转 WebP**。1080x2280 的 PNG 单张 1.2~1.9MB，
转 WebP（q=78）后通常 60~150KB，压缩率约 92%，肉眼几乎无差；
嵌入 HTML 报告时能让自包含单文件从 MB 级降到百 KB 级。

用法（必须用装了 Pillow 的托管 venv 解释器）：

    # 抓一张（直接落 WebP，不留 PNG）
    python webp_shot.py grab out.webp [--quality 78] [--max-width 720] [--serial d5652109]

    # 转换已有图片（文件或目录）
    python webp_shot.py convert a.png b.png dir/ [--recursive] [--delete]

    # 只报体积，不写文件
    python webp_shot.py stat dir/

作为库使用：

    from webp_shot import png_to_webp, grab_webp
    data = png_to_webp(png_bytes, quality=78, max_width=720)
    info = grab_webp('shot.webp')          # {'bytes':.., 'width':.., 'height':.., 'ms':..}
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import time

ADB = os.environ.get("ADB", "adb")
DEFAULT_QUALITY = 78
DEFAULT_MAX_WIDTH = 0  # 0 = 不缩放，保持原分辨率


def _pil():
    """延迟导入 Pillow，缺失时给出明确指引。"""
    try:
        from PIL import Image  # noqa: F401

        return Image
    except ImportError:
        sys.exit(
            "缺少 Pillow。请用托管 venv 的解释器运行本脚本：\n"
            "  C:/Users/zongy/.workbuddy/binaries/python/envs/default/Scripts/python.exe\n"
            "（基座 python 未装 Pillow，venv 内已装好且含 WebP 编码器）"
        )


def png_to_webp(data: bytes, quality: int = DEFAULT_QUALITY, max_width: int = DEFAULT_MAX_WIDTH) -> bytes:
    """PNG/JPEG 字节流 -> WebP 字节流。"""
    Image = _pil()
    im = Image.open(io.BytesIO(data))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")  # 截图不需要 alpha，丢掉可显著减小体积
    if max_width and im.width > max_width:
        h = round(im.height * max_width / im.width)
        im = im.resize((max_width, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def grab_webp(
    out_path: str,
    quality: int = DEFAULT_QUALITY,
    max_width: int = DEFAULT_MAX_WIDTH,
    serial: str | None = None,
) -> dict:
    """adb screencap -> 直接写成 WebP（不落任何中间 PNG 文件）。

    返回 {'path','bytes','width','height','ms'}；失败抛 RuntimeError。
    """
    cmd = [ADB]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "screencap", "-p"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("screencap 失败: " + r.stderr.decode("utf-8", "replace")[:200])
    png = r.stdout
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        # 部分 ROM 会在 PNG 前插 CRLF，剥掉再判
        png = png.lstrip(b"\r\n")
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("screencap 返回的不是 PNG（可能是设备锁屏或 adb 未就绪）")

    Image = _pil()
    webp = png_to_webp(png, quality=quality, max_width=max_width)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(webp)
    w, h = Image.open(io.BytesIO(png)).size
    return {
        "path": out_path,
        "bytes": len(webp),
        "raw_bytes": len(png),
        "width": w,
        "height": h,
        "ms": int((time.time() - t0) * 1000),
    }


def convert_one(
    src: str,
    quality: int = DEFAULT_QUALITY,
    max_width: int = DEFAULT_MAX_WIDTH,
    out_dir: str | None = None,
    delete: bool = False,
    force: bool = False,
) -> dict | None:
    """单文件转换。已是 .webp 或目标已存在且未 force 时跳过（返回 None）。"""
    low = src.lower()
    if low.endswith(".webp"):
        return None
    if not low.endswith((".png", ".jpg", ".jpeg")):
        return None
    base = os.path.splitext(os.path.basename(src))[0] + ".webp"
    dst = os.path.join(out_dir, base) if out_dir else os.path.join(os.path.dirname(src), base)
    if os.path.exists(dst) and not force:
        return None
    with open(src, "rb") as f:
        raw = f.read()
    webp = png_to_webp(raw, quality=quality, max_width=max_width)
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    with open(dst, "wb") as f:
        f.write(webp)
    if delete and os.path.abspath(src) != os.path.abspath(dst):
        os.remove(src)
    return {"src": src, "dst": dst, "raw_bytes": len(raw), "bytes": len(webp)}


def iter_images(paths: list[str], recursive: bool = False, exts=(".png", ".jpg", ".jpeg")):
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for dp, dn, fn in os.walk(p):
                dn[:] = [d for d in dn if d not in ("node_modules", ".git", ".next", "__pycache__")]
                for f in sorted(fn):
                    if f.lower().endswith(exts):
                        yield os.path.join(dp, f)
                if not recursive:
                    break
        else:
            print("跳过（不存在）:", p, file=sys.stderr)


def mb(n: int) -> str:
    return "%.2f MB" % (n / 1024 / 1024)


def cmd_grab(a):
    info = grab_webp(a.out, quality=a.quality, max_width=a.max_width, serial=a.serial)
    print(
        "已保存 %s  %s -> %s  %dx%d  %dms"
        % (info["path"], mb(info["raw_bytes"]), kb(info["bytes"]), info["width"], info["height"], info["ms"])
    )


def kb(n: int) -> str:
    return "%.0f KB" % (n / 1024) if n < 1024 * 1024 else mb(n)


def cmd_convert(a):
    done, skipped, saved_raw, saved_new = [], 0, 0, 0
    for src in iter_images(a.paths, recursive=a.recursive):
        r = convert_one(
            src, quality=a.quality, max_width=a.max_width, out_dir=a.out_dir, delete=a.delete, force=a.force
        )
        if r is None:
            skipped += 1
            continue
        done.append(r)
        saved_raw += r["raw_bytes"]
        saved_new += r["bytes"]
        print("  %-46s %8s -> %8s  (%s%%)" % (
            os.path.relpath(r["dst"], a.base or "."),
            kb(r["raw_bytes"]), kb(r["bytes"]),
            "%.0f" % (100 * r["bytes"] / max(r["raw_bytes"], 1)),
        ))
        if a.delete:
            print("      （原图已删除）")
    if not done:
        print("无需转换（已是 webp 或目标已存在）。跳过 %d 个。" % skipped)
        return
    print(
        "\n转换 %d 个：%s -> %s，省 %s（压缩到 %.0f%%）%s"
        % (
            len(done), mb(saved_raw), mb(saved_new), mb(saved_raw - saved_new),
            100 * saved_new / max(saved_raw, 1), "，跳过 %d 个" % skipped if skipped else "",
        )
    )


def cmd_pipe(a):
    """从 stdin 读 PNG/JPEG，写 WebP 到 stdout 或文件。

    供 Node 侧（runner.mjs / studio）复用一个 WebP 编码器，避免为了转格式
    去装满血的 native 依赖（sharp 等）。
    """
    raw = sys.stdin.buffer.read()
    if not raw:
        sys.exit("stdin 为空")
    webp = png_to_webp(raw, quality=a.quality, max_width=a.max_width)
    if a.out and a.out != "-":
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "wb") as f:
            f.write(webp)
        print("%s  %s -> %s" % (a.out, kb(len(raw)), kb(len(webp))), file=sys.stderr)
    else:
        sys.stdout.buffer.write(webp)
        sys.stdout.buffer.flush()


def cmd_stat(a):
    rows = []
    for src in iter_images(a.paths, recursive=a.recursive, exts=(".png", ".jpg", ".jpeg", ".webp")):
        rows.append((os.path.relpath(src, a.base or "."), os.path.getsize(src)))
    if not rows:
        print("未找到图片。")
        return
    rows.sort(key=lambda r: -r[1])
    png = sum(s for rel, s in rows if not rel.lower().endswith(".webp"))
    webp = sum(s for rel, s in rows if rel.lower().endswith(".webp"))
    for rel, s in rows:
        print("  %8s  %s" % (kb(s), rel))
    print("\n共 %d 个，%s" % (len(rows), mb(sum(r[1] for r in rows))))
    if webp:
        print("  其中 PNG/JPEG %s · WebP %s" % (mb(png), mb(webp)))
        if png:
            print("  ⚠ 仍有未转换的 PNG：%s。转换命令：" % mb(png))
            print("    webp_shot.py convert <目录> --recursive --delete")


def _add_common(p, has_defaults):
    """把 --quality/--max-width 挂到主解析器和每个子命令上。

    子命令侧用 SUPPRESS：不显式给值时就不写属性，避免子解析器的默认值
    反过来覆盖主解析器上用户给的值（argparse 的经典坑）。
    """
    d = DEFAULT_QUALITY if has_defaults else argparse.SUPPRESS
    p.add_argument("--quality", type=int, default=d, help="WebP 质量，默认 78")
    d = DEFAULT_MAX_WIDTH if has_defaults else argparse.SUPPRESS
    p.add_argument("--max-width", type=int, default=d, help="最大宽度，0=不缩放")


def build_parser(fill_defaults=True):
    p = argparse.ArgumentParser(description="设备截图 / 图片转 WebP 工具（项目约定：PNG 一律转 WebP）")
    _add_common(p, fill_defaults)
    p.add_argument("--base", default=os.getcwd(), help="打印相对路径的基准目录")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grab", help="从设备抓一张截图并直接存为 WebP")
    _add_common(g, False)
    g.add_argument("out", help="输出 .webp 路径")
    g.add_argument("--serial", default=None, help="设备序列号（多设备时用）")
    g.set_defaults(func=cmd_grab)

    c = sub.add_parser("convert", help="把已有 PNG/JPEG 转成 WebP")
    _add_common(c, False)
    c.add_argument("paths", nargs="+", help="文件或目录")
    c.add_argument("--recursive", action="store_true", help="目录递归")
    c.add_argument("--out-dir", default=None, help="输出目录（默认与源文件同目录）")
    c.add_argument("--delete", action="store_true", help="转换成功后删除原图")
    c.add_argument("--force", action="store_true", help="目标已存在也覆盖")
    c.set_defaults(func=cmd_convert)

    s = sub.add_parser("stat", help="统计图片体积")
    _add_common(s, False)
    s.add_argument("paths", nargs="+")
    s.add_argument("--recursive", action="store_true")
    s.set_defaults(func=cmd_stat)

    pp = sub.add_parser("pipe", help="从 stdin 读图片，转 WebP 后写文件或 stdout（供 Node 调用）")
    _add_common(pp, False)
    pp.add_argument("out", nargs="?", default="-", help="输出路径，- 表示写 stdout")
    pp.set_defaults(func=cmd_pipe)

    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
