"""Render the brand PNGs from the shapes in icon.svg.

    python brand/render.py

Pillow draws these rather than rasterising the SVG, because no SVG rasteriser
can be assumed to be installed. If you change icon.svg, change the shapes here
to match -- or drop your own icon.png / logo.png in and skip this entirely.

Sizes follow Home Assistant's brands convention: a square icon at 256x256 and a
wordmark-style logo no wider than 512.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
# Home Assistant serves brand images straight from an integration that has a
# "brand" directory (loader.Integration.has_branding), so this is the functional
# location, not a copy: components/brands falls back through icon.png for every
# other size and for the dark variants.
BRAND = HERE.parent / "custom_components" / "hikvision_nvr" / "brand"
PANEL = HERE.parent / "custom_components" / "hikvision_nvr" / "frontend"
SUPERSAMPLE = 4  # draw big, shrink down: cheap anti-aliasing

NAVY = (22, 32, 43, 255)
DOME_TOP = (232, 238, 245, 255)
DOME_BOTTOM = (183, 196, 212, 255)
MOUNT = (143, 160, 181, 255)
LENS_RING = (13, 22, 32, 255)
LENS = (18, 41, 61, 255)
GLINT = (95, 208, 255, 217)
RED_TOP = (227, 6, 19, 255)
RED_BOTTOM = (163, 4, 16, 255)
WHITE = (255, 255, 255, 255)


def _vertical_gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    """A linear top-to-bottom gradient, used for the dome and the red bar."""
    width, height = size
    gradient = Image.new("RGBA", (1, height))
    for y in range(height):
        ratio = y / max(height - 1, 1)
        gradient.putpixel(
            (0, y),
            tuple(round(a + (b - a) * ratio) for a, b in zip(top, bottom)),
        )
    return gradient.resize(size)


def _masked(base: Image.Image, mask: Image.Image, fill: Image.Image) -> None:
    base.paste(fill, (0, 0), mask)


def draw_icon(size: int = 256) -> Image.Image:
    s = size * SUPERSAMPLE
    unit = s / 256  # the SVG is authored on a 256 grid
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def px(*values: float) -> list[float]:
        return [v * unit for v in values]

    # rounded navy plate
    draw.rounded_rectangle(px(0, 0, 255, 255), radius=56 * unit, fill=NAVY)

    # dome: a half-disc, gradient filled through a mask
    dome_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(dome_mask).pieslice(px(60, 48, 196, 184), 180, 360, fill=255)
    _masked(canvas, dome_mask, _vertical_gradient((s, s), DOME_TOP, DOME_BOTTOM))

    # mount bar under the dome
    draw.rounded_rectangle(px(52, 116, 204, 132), radius=8 * unit, fill=MOUNT)

    # lens
    draw.ellipse(px(98, 68, 158, 128), fill=LENS_RING)
    draw.ellipse(px(107, 77, 149, 119), fill=LENS)
    draw.ellipse(px(113, 83, 127, 97), fill=GLINT)

    # recorder bar
    bar_mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(bar_mask).rounded_rectangle(
        px(44, 156, 212, 208), radius=14 * unit, fill=255
    )
    _masked(canvas, bar_mask, _vertical_gradient((s, s), RED_TOP, RED_BOTTOM))

    # record light and disk lines
    draw.ellipse(px(66, 172, 86, 192), fill=WHITE)
    draw.rounded_rectangle(px(100, 176, 188, 182), radius=3 * unit, fill=WHITE)
    draw.rounded_rectangle(
        px(100, 190, 156, 196), radius=3 * unit, fill=(255, 255, 255, 140)
    )

    return canvas.resize((size, size), Image.LANCZOS)


def draw_logo(icon: Image.Image, width: int = 512, height: int = 160) -> Image.Image:
    """Icon plus wordmark, for README and HACS listings."""
    logo = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    badge = icon.resize((128, 128), Image.LANCZOS)
    logo.paste(badge, (8, (height - 128) // 2), badge)

    draw = ImageDraw.Draw(logo)
    try:
        title = ImageFont.truetype("segoeuib.ttf", 44)
        subtitle = ImageFont.truetype("segoeui.ttf", 22)
    except OSError:  # no system fonts (CI); the icon alone still renders
        title = subtitle = ImageFont.load_default()
    draw.text((156, 46), "Hikvision NVR", font=title, fill=(232, 238, 245, 255))
    draw.text((158, 96), "for Home Assistant", font=subtitle, fill=(143, 160, 181, 255))
    return logo


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    icon.save(BRAND / "icon.png")
    draw_icon(512).save(BRAND / "icon@2x.png")
    draw_logo(icon).save(BRAND / "logo.png")
    draw_logo(draw_icon(1024), 1024, 320).save(BRAND / "logo@2x.png")
    # The panel toolbar loads its mark over HTTP, from the served frontend dir.
    icon.resize((128, 128), Image.LANCZOS).save(PANEL / "icon.png")

    for path in sorted(BRAND.iterdir()) + [PANEL / "icon.png"]:
        with Image.open(path) as image:
            where = path.parent.name
            print(f"{where}/{path.name:14} {image.size[0]}x{image.size[1]}"
                  f"  {path.stat().st_size} B")


if __name__ == "__main__":
    main()
