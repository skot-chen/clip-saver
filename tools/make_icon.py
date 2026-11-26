"""Draw the app icon (option B: black/white split + green download button) as a macOS iconset or Windows .ico.

    python tools/make_icon.py assets/icon.iconset   # Mac, then: iconutil -c icns ...
    python tools/make_icon.py assets/ClipSaver.ico   # Windows
Standard library only.
"""
import math
import struct
import sys
import zlib
from pathlib import Path


def png(path, size, pixel):
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend(pixel((x + 0.5) / size, (y + 0.5) / size, 1.0 / size))
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(bytes(rows), 9)) + chunk(b'IEND', b''))


def clamp(v):
    return max(0.0, min(1.0, v))


def rounded_box(x, y, cx, cy, hw, hh, r):
    dx, dy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
    out = (max(dx, 0) ** 2 + max(dy, 0) ** 2) ** 0.5
    return out + min(max(dx, dy), 0) - r


def in_triangle(x, y, a, b, c):
    def side(p, q):
        return (x - q[0]) * (p[1] - q[1]) - (p[0] - q[0]) * (y - q[1])
    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def seg_dist(x, y, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return ((x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2) ** 0.5


def pixel(x, y, px):
    """Option B: black / white diagonal split, a cross on the black half, a loop on
    the white half, and a green download button in the middle (original shapes,
    not the X or Threads logos)."""
    d = rounded_box(x, y, 0.5, 0.5, 0.41, 0.41, 0.18)
    alpha = clamp(0.5 - d / px)
    if alpha <= 0:
        return (0, 0, 0, 0)
    black, white, green = (0.07, 0.07, 0.08), (0.96, 0.96, 0.96), (0x22 / 255, 0xc5 / 255, 0x5e / 255)

    def mix(a, b, t):
        return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))

    # diagonal split (anti-aliased): top-left black, bottom-right white
    t = clamp(0.5 + ((x + y) - 1.0) / (px * 1.41))
    col = mix(black, white, t)
    # thin grey rim so the white half stays visible on light backgrounds
    rim = clamp(1 - abs(d + px * 1.2) / (px * 1.2)) * t
    col = mix(col, (0.80, 0.80, 0.82), rim * 0.9)
    w = 0.028
    # cross on the black half
    cross = min(seg_dist(x, y, 0.20, 0.20, 0.33, 0.33), seg_dist(x, y, 0.33, 0.20, 0.20, 0.33)) - w
    col = mix(col, white, clamp(0.5 - cross / px))
    # loop on the white half: 3/4 circle + short tail
    cx, cy, rr = 0.725, 0.705, 0.075
    ang = math.atan2(y - cy, x - cx)            # y grows downward
    gap = (math.pi / 2 < ang <= math.pi)        # leave the lower-left quarter open
    ring = abs(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 - rr) - w
    if gap:
        ring = 1.0
    ring = min(ring, seg_dist(x, y, cx, cy + rr, cx - 0.05, cy + rr) - w,
               ((x - cx + rr) ** 2 + (y - cy) ** 2) ** 0.5 - w)
    col = mix(col, black, clamp(0.5 - ring / px))
    # green download button in the middle
    disc = ((x - 0.5) ** 2 + (y - 0.5) ** 2) ** 0.5 - 0.175
    col = mix(col, green, clamp(0.5 - disc / px))
    arrow = min(seg_dist(x, y, 0.5, 0.41, 0.5, 0.57), seg_dist(x, y, 0.43, 0.505, 0.5, 0.575),
                seg_dist(x, y, 0.57, 0.505, 0.5, 0.575)) - 0.026
    col = mix(col, (1, 1, 1), clamp(0.5 - arrow / px) * clamp(0.5 - disc / px))
    return (int(col[0] * 255), int(col[1] * 255), int(col[2] * 255), int(alpha * 255))


def ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    # Windows .ico whose entries are PNG images (supported since Windows Vista).
    pngs = []
    for size in sizes:
        tmp = Path(str(path) + f'.{size}.png')
        png(tmp, size, pixel)
        pngs.append((size, tmp.read_bytes()))
        tmp.unlink()
    header = struct.pack('<HHH', 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b'', b''
    for size, data in pngs:
        s = 0 if size == 256 else size
        entries += struct.pack('<BBBBHHII', s, s, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    Path(path).write_bytes(header + entries + blobs)


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        png(out / f'icon_{base}x{base}.png', base, pixel)
        png(out / f'icon_{base}x{base}@2x.png', base * 2, pixel)


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else 'assets/icon.iconset'
    if target.endswith('.ico'):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        ico(target)
    else:
        main(target)
