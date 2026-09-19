# -*- coding: utf-8 -*-
"""
make_logo.py —— 由源图生成应用图标 app.ico 与仓库用 logo PNG

流程：
  1. 用 dev\\decode_image.ps1 把源图解码成裸 BGRA 数据
  2. 本脚本自动识别「棋盘格透明背景」-> 定位 logo 外接框与圆角半径
  3. 用圆角矩形遮罩重建 alpha 通道，去掉 JPEG 边缘杂色
  4. 面积平均（盒式滤波）+ 预乘 alpha 降采样，输出：
       app.ico            多尺寸图标（16/24/32/48/64/128/256）
       docs/logo.png      512x512 透明 PNG，供 README 展示

用法：
  powershell -ExecutionPolicy Bypass -File dev\\decode_image.ps1 <源图> .build\\logo_src.raw
  python dev\\make_logo.py .build\\logo_src.raw

只依赖标准库（zlib / struct）。
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
PREVIEW_SIZE = 512

# 判定背景时允许的颜色偏差（吸收 JPEG 压缩噪点）
BG_TOLERANCE = 26
# 遮罩向内收缩的像素数，用来丢掉源图边缘的杂色描边
EDGE_INSET = 2


# --------------------------------------------------------------------------- #
# 读取裸 BGRA
# --------------------------------------------------------------------------- #
class Image:
    def __init__(self, width: int, height: int, pixels: bytearray) -> None:
        self.w = width
        self.h = height
        self.px = pixels  # RGBA，自上而下，每像素 4 字节

    def get(self, x: int, y: int):
        i = (y * self.w + x) * 4
        return self.px[i], self.px[i + 1], self.px[i + 2], self.px[i + 3]


def load_raw(path: Path) -> Image:
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError("raw 文件太短")
    width, height, stride = struct.unpack_from("<iii", data, 0)
    body = data[12:]
    if len(body) < stride * height:
        raise ValueError(f"raw 数据不完整：期望 {stride * height}，实际 {len(body)}")

    out = bytearray(width * height * 4)
    for y in range(height):
        src = y * stride
        dst = y * width * 4
        for x in range(width):
            b, g, r = body[src], body[src + 1], body[src + 2]
            out[dst] = r
            out[dst + 1] = g
            out[dst + 2] = b
            out[dst + 3] = 255
            src += 4
            dst += 4
    return Image(width, height, out)


# --------------------------------------------------------------------------- #
# 背景识别
# --------------------------------------------------------------------------- #
def sample_background_colors(img: Image):
    """从四角取样，得到棋盘格的两种底色。"""
    patch = max(2, min(img.w, img.h) // 100)
    corners = [
        (2, 2), (img.w - 2 - patch, 2),
        (2, img.h - 2 - patch), (img.w - 2 - patch, img.h - 2 - patch),
    ]
    colors = []
    for cx, cy in corners:
        r = g = b = 0
        n = 0
        for y in range(cy, min(cy + patch, img.h)):
            for x in range(cx, min(cx + patch, img.w)):
                pr, pg, pb, _ = img.get(x, y)
                r += pr
                g += pg
                b += pb
                n += 1
        colors.append((r // n, g // n, b // n))
    return colors


def make_background_test(img: Image):
    colors = sample_background_colors(img)

    def is_background(r: int, g: int, b: int) -> bool:
        for cr, cg, cb in colors:
            if (abs(r - cr) <= BG_TOLERANCE
                    and abs(g - cg) <= BG_TOLERANCE
                    and abs(b - cb) <= BG_TOLERANCE):
                return True
        return False

    return is_background, colors


def find_bounds(img: Image, is_background) -> tuple:
    """找出 logo 的外接框；顺带过滤 JPEG 噪点造成的零星像素。"""
    col_hits = [0] * img.w
    row_hits = [0] * img.h
    for y in range(img.h):
        for x in range(img.w):
            r, g, b, _ = img.get(x, y)
            if not is_background(r, g, b):
                col_hits[x] += 1
                row_hits[y] += 1

    min_run = max(2, min(img.w, img.h) // 200)
    xs = [x for x, c in enumerate(col_hits) if c >= min_run]
    ys = [y for y, c in enumerate(row_hits) if c >= min_run]
    if not xs or not ys:
        raise ValueError("没有识别出前景区域，请检查源图背景是否干净")
    return xs[0], ys[0], xs[-1], ys[-1]


def estimate_radius(img: Image, is_background, left: int, top: int,
                    right: int, bottom: int) -> int:
    """圆角半径：圆角起点所在行的内缩量就等于半径。"""
    max_r = min(right - left, bottom - top) // 2

    def inset_at(y: int) -> int:
        for x in range(left, right + 1):
            r, g, b, _ = img.get(x, y)
            if not is_background(r, g, b):
                return x - left
        return max_r

    probe = inset_at(top + 1)
    # 交叉验证：内缩量归零的行号应当与半径接近
    zero_row = None
    for dy in range(1, max_r + 1):
        if top + dy <= bottom and inset_at(top + dy) <= 1:
            zero_row = dy
            break
    if zero_row and abs(zero_row - probe) <= max(4, probe // 3):
        return max(1, (probe + zero_row) // 2)
    return max(1, probe)


# --------------------------------------------------------------------------- #
# 遮罩 + 缩放
# --------------------------------------------------------------------------- #
def in_rounded_rect(px: float, py: float, left: float, top: float,
                    right: float, bottom: float, radius: float) -> bool:
    if px < left or px > right or py < top or py > bottom:
        return False
    cx = min(max(px, left + radius), right - radius)
    cy = min(max(py, top + radius), bottom - radius)
    dx, dy = px - cx, py - cy
    return dx * dx + dy * dy <= radius * radius


def apply_mask(img: Image, left: int, top: int, right: int, bottom: int, radius: int) -> Image:
    """把圆角矩形之外全部置为透明，并裁到外接框。"""
    left += EDGE_INSET
    top += EDGE_INSET
    right -= EDGE_INSET
    bottom -= EDGE_INSET
    radius = max(1, radius - EDGE_INSET)

    cw = right - left + 1
    ch = bottom - top + 1
    out = bytearray(cw * ch * 4)
    for y in range(ch):
        for x in range(cw):
            si = ((y + top) * img.w + (x + left)) * 4
            di = (y * cw + x) * 4
            out[di] = img.px[si]
            out[di + 1] = img.px[si + 1]
            out[di + 2] = img.px[si + 2]
            out[di + 3] = 255 if in_rounded_rect(
                x + left + 0.5, y + top + 0.5, left, top, right, bottom, radius
            ) else 0
    return Image(cw, ch, out)


def resize_rgba(img: Image, size: int) -> bytes:
    """面积平均 + 预乘 alpha 降采样，返回 RGBA 字节。"""
    sw, sh = img.w, img.h
    acc = bytearray(size * size * 4)
    x_scale = sw / size
    y_scale = sh / size

    for dy in range(size):
        y0 = dy * y_scale
        y1 = (dy + 1) * y_scale
        sy0, sy1 = int(y0), min(sh, max(int(y1 + 0.999), int(y0) + 1))
        for dx in range(size):
            x0 = dx * x_scale
            x1 = (dx + 1) * x_scale
            sx0, sx1 = int(x0), min(sw, max(int(x1 + 0.999), int(x0) + 1))

            total = 0.0
            sum_a = 0.0
            sum_r = sum_g = sum_b = 0.0
            for y in range(sy0, sy1):
                wy = min(y + 1, y1) - max(y, y0)
                if wy <= 0:
                    continue
                for x in range(sx0, sx1):
                    wx = min(x + 1, x1) - max(x, x0)
                    if wx <= 0:
                        continue
                    w = wx * wy
                    i = (y * sw + x) * 4
                    a = img.px[i + 3]
                    total += w
                    if a:
                        aw = a * w
                        sum_a += aw
                        sum_r += img.px[i] * aw
                        sum_g += img.px[i + 1] * aw
                        sum_b += img.px[i + 2] * aw
            di = (dy * size + dx) * 4
            if total <= 0 or sum_a <= 0:
                continue
            acc[di] = min(255, int(sum_r / sum_a + 0.5))
            acc[di + 1] = min(255, int(sum_g / sum_a + 0.5))
            acc[di + 2] = min(255, int(sum_b / sum_a + 0.5))
            acc[di + 3] = min(255, int(sum_a / total + 0.5))
    return bytes(acc)


# --------------------------------------------------------------------------- #
# 写出 PNG / ICO
# --------------------------------------------------------------------------- #
def write_png(path: Path, rgba: bytes, size: int) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        raw += rgba[y * size * 4:(y + 1) * size * 4]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def rgba_to_ico_image(rgba: bytes, size: int) -> bytes:
    """ICO 内嵌的 32bpp BMP：BITMAPINFOHEADER + 自下而上的 BGRA + AND 掩码。"""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        row = y * size * 4
        for x in range(size):
            i = row + x * 4
            xor += bytes((rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))
    row_bytes = ((size + 31) // 32) * 4
    return header + bytes(xor) + bytes(row_bytes * size)


def write_ico(path: Path, images) -> None:
    count = len(images)
    offset = 6 + 16 * count
    directory = struct.pack("<HHH", 0, 1, count)
    entries = b""
    payload = b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII",
                               0 if size >= 256 else size,
                               0 if size >= 256 else size,
                               0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(directory + entries + payload)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    raw_path = Path(argv[1])
    if not raw_path.is_absolute():
        raw_path = (ROOT / raw_path).resolve()
    if not raw_path.is_file():
        print(f"[错误] 找不到 {raw_path}")
        return 1

    print(f"读取 {raw_path}")
    img = load_raw(raw_path)
    print(f"  尺寸 {img.w} x {img.h}")

    is_background, colors = make_background_test(img)
    print("  背景色样本：" + "  ".join(f"#{r:02X}{g:02X}{b:02X}" for r, g, b in colors))

    left, top, right, bottom = find_bounds(img, is_background)
    print(f"  logo 外接框 ({left},{top}) - ({right},{bottom})  "
          f"尺寸 {right - left + 1} x {bottom - top + 1}")

    radius = estimate_radius(img, is_background, left, top, right, bottom)
    print(f"  估计圆角半径 {radius}px")

    masked = apply_mask(img, left, top, right, bottom, radius)
    print(f"  遮罩后尺寸 {masked.w} x {masked.h}（已内缩 {EDGE_INSET}px 去除边缘杂色）")

    ico_images = []
    for size in ICO_SIZES:
        rgba = resize_rgba(masked, size)
        ico_images.append((size, rgba_to_ico_image(rgba, size)))

    ico_path = ROOT / "app.ico"
    write_ico(ico_path, ico_images)
    print(f"已生成 {ico_path}  ({ico_path.stat().st_size / 1024:.1f} KB, {len(ICO_SIZES)} 个尺寸)")

    preview_path = ROOT / "docs" / "logo.png"
    write_png(preview_path, resize_rgba(masked, PREVIEW_SIZE), PREVIEW_SIZE)
    print(f"已生成 {preview_path}  ({preview_path.stat().st_size / 1024:.1f} KB, {PREVIEW_SIZE}x{PREVIEW_SIZE})")

    # 顺带导出一张小尺寸预览，便于 QuickCheck
    small_path = ROOT / ".build" / "logo_preview_48.png"
    write_png(small_path, resize_rgba(masked, 48), 48)
    print(f"已生成 {small_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
