"""Build the app icon from the user's logo design (art/logo-design.png).

Dark tile, the logo's X + loop in white, download arrow redrawn thicker in coral.
Needs Pillow. Writes art/icon-1024.png, art/ClipSaver.icns, art/ClipSaver.ico,
docs/assets/icon.png and clipsaver/logo.png.

    python tools/build_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
BG = (22, 24, 29)
INK = (245, 245, 245)
ARROW = (0xff, 0x7a, 0x45)   # coral, same as the app's default theme
PAD = 24
BOX = (412 - PAD, 386 - PAD, 1048 + PAD, 1076 + PAD)   # the mark inside the design file


def is_green(p):
    r, g, b = p
    return g > 120 and g - r > 50 and g - b > 40


def mark_on_dark(scale):
    src = Image.open(ROOT / 'art' / 'logo-design.png').convert('RGB')
    base = src.crop(BOX)
    w, h = base.size
    base = base.resize((w * scale, h * scale), Image.LANCZOS)
    W, H = base.size
    out = Image.new('RGB', (W, H), BG)
    op, bp = out.load(), base.load()
    for y in range(H):
        for x in range(W):
            p = bp[x, y]
            if is_green(p) or (y > H * 0.66 and abs(p[1] - p[0]) > 25):
                continue                      # drop the original thin arrow
            t = min(p) / 255
            if t > 0.89:
                continue                      # paper -> background
            op[x, y] = tuple(int(INK[k] * (1 - t) + BG[k] * t) for k in range(3))
    # redraw the arrow thicker, on a 4x layer for smooth edges (coordinates in the design file)
    k = 4
    layer = Image.new('L', (W * k, H * k), 0)
    d = ImageDraw.Draw(layer)
    width = 34 * scale * k

    def P(x, y):
        return ((x - BOX[0]) * scale * k, (y - BOX[1]) * scale * k)

    def dot(x, y):
        d.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=255)

    def seg(a, b):
        d.line([a, b], fill=255, width=int(width))
        dot(*a)
        dot(*b)

    seg(P(700, 842), P(700, 1012))
    seg(P(630, 944), P(700, 1014))
    seg(P(770, 944), P(700, 1014))
    # tray: a shallow smile drawn as many short round-capped pieces
    pts = [P(572 + t / 80 * 256, 1036 + 34 * math.sin(math.pi * t / 80)) for t in range(81)]
    for a, b in zip(pts, pts[1:]):
        seg(a, b)
    layer = layer.resize((W, H), Image.LANCZOS)
    out.paste(Image.new('RGB', (W, H), ARROW), (0, 0), layer)
    return out


def main():
    S, inset = 1024, 100
    tile = S - 2 * inset
    rad = int(tile * 0.225)
    canvas = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    shadow = Image.new('L', (S, S), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((inset, inset + 12, S - inset, S - inset + 12), rad, fill=80)
    canvas.paste(Image.new('RGBA', (S, S), (0, 0, 0, 255)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((inset, inset, S - inset, S - inset), rad, fill=255)
    canvas.paste(Image.new('RGBA', (S, S), BG + (255,)), (0, 0), mask)
    ImageDraw.Draw(canvas).rounded_rectangle((inset, inset, S - inset, S - inset), rad, outline=(52, 56, 64, 255), width=3)
    mark = mark_on_dark(2)
    h = int(tile * 0.66)
    w = int(mark.size[0] * h / mark.size[1])
    canvas.paste(mark.resize((w, h), Image.LANCZOS), ((S - w) // 2, (S - h) // 2 + 4))

    canvas.save(ROOT / 'art' / 'icon-1024.png')
    canvas.save(ROOT / 'art' / 'ClipSaver.icns')
    canvas.save(ROOT / 'art' / 'ClipSaver.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    canvas.resize((256, 256), Image.LANCZOS).save(ROOT / 'docs' / 'assets' / 'icon.png')
    canvas.resize((128, 128), Image.LANCZOS).save(ROOT / 'clipsaver' / 'logo.png')


if __name__ == '__main__':
    main()
