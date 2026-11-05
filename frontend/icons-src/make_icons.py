"""Draws the Prism app icons (a beam of white light entering a prism and leaving as a spectrum).
Needs Pillow only:  python3 icons-src/make_icons.py   (run from frontend/; writes public/icons/*.png)
icons-src/icon.svg is the same drawing as vector."""
from PIL import Image, ImageDraw

BANDS = ["#ff5d5d", "#ffa94d", "#ffe066", "#69db7c", "#4dabf7", "#b197fc"]


def render(size: int, scale: float) -> Image.Image:
    S = size * 4
    bg = Image.new("RGB", (S, S))
    px = bg.load()
    for y in range(S):  # diagonal gradient, navy to blue
        for x in range(S):
            t = (x + y) / (2 * S)
            px[x, y] = (int(22 + (59 - 22) * t), int(32 + (91 - 32) * t), int(84 + (219 - 84) * t))
    im = bg.convert("RGBA")
    def layer(draw_fn):  # ImageDraw replaces pixels instead of blending, so each element is its own layer
        nonlocal im
        l = Image.new("RGBA", im.size, (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(l))
        im = Image.alpha_composite(im, l)

    k = S * scale / 100
    o = S * (1 - scale) / 2
    P = lambda x, y: (o + x * k, o + y * k)
    apex, bl, br = (50, 24), (22, 74), (78, 74)
    lp = (apex[0] + (bl[0] - apex[0]) * .55, apex[1] + (bl[1] - apex[1]) * .55)   # beam enters here
    rp = (apex[0] + (br[0] - apex[0]) * .55, apex[1] + (br[1] - apex[1]) * .55)   # spectrum leaves here
    # spectrum fan
    top, step = 38, 7.4
    for i, c in enumerate(BANDS):
        y0 = top + i * step
        col = tuple(int(c[j:j + 2], 16) for j in (1, 3, 5)) + (255,)
        layer(lambda d, y0=y0, col=col: d.polygon([P(*rp), P(97, y0), P(97, y0 + step + .3)], fill=col))
    # incoming beam and the path inside the glass
    layer(lambda d: d.line([P(3, 66), P(*lp)], fill=(255, 255, 255, 255), width=int(3.2 * k)))
    layer(lambda d: d.line([P(*lp), P(*rp)], fill=(255, 255, 255, 150), width=int(2 * k)))
    # the prism: translucent glass with a bright edge
    layer(lambda d: d.polygon([P(*apex), P(*bl), P(*br)], fill=(255, 255, 255, 46)))
    layer(lambda d: d.line([P(*apex), P(*bl), P(*br), P(*apex)], fill=(255, 255, 255, 255), width=int(3 * k), joint="curve"))
    for v in (apex, bl, br):  # round the corners
        layer(lambda d, v=v: d.ellipse([P(v[0], v[1])[0] - 1.5 * k, P(v[0], v[1])[1] - 1.5 * k, P(v[0], v[1])[0] + 1.5 * k, P(v[0], v[1])[1] + 1.5 * k], fill=(255, 255, 255, 255)))
    return im.convert("RGB").resize((size, size), Image.LANCZOS)


for name, size, scale in [("icon-192", 192, .9), ("icon-512", 512, .9), ("maskable-512", 512, .7), ("apple-touch-icon", 180, .9)]:
    render(size, scale).save(f"public/icons/{name}.png")
