"""Draw the app icon (blue rounded square + white play-and-download mark) as a macOS iconset.

    python tools/make_icon.py assets/icon.iconset   # Mac, then: iconutil -c icns ...
    python tools/make_icon.py assets/ClipSaver.ico   # Windows
Standard library only.
"""
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


def pixel(x, y, px):
    # background: rounded square, macOS-style margin
    d = rounded_box(x, y, 0.5, 0.5, 0.41, 0.41, 0.09)
    alpha = clamp(0.5 - d / px)
    if alpha <= 0:
        return (0, 0, 0, 0)
    t = y
    r, g, b = 0.12 + 0.10 * t, 0.43 - 0.12 * t, 0.92 - 0.10 * t
    white = 0.0
    # play triangle
    if in_triangle(x, y, (0.40, 0.27), (0.40, 0.55), (0.63, 0.41)):
        white = 1.0
    # download bar
    if rounded_box(x, y, 0.5, 0.70, 0.17, 0.035, 0.035) < 0:
        white = 1.0
    r, g, b = (r + (1 - r) * white, g + (1 - g) * white, b + (1 - b) * white)
    return (int(r * 255), int(g * 255), int(b * 255), int(alpha * 255))


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
