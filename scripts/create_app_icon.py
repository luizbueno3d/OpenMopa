from __future__ import annotations

import math
import io
import struct
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "MOPA Luiz.app" / "Contents" / "Resources"
ICONSET = RESOURCES / "applet.iconset"
ICNS = RESOURCES / "applet.icns"
PREVIEW = RESOURCES / "mopa-luiz-icon-preview.png"


def rr(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def scale_points(points, scale: int) -> list[tuple[int, int]]:
    return [(int(x * scale), int(y * scale)) for x, y in points]


def draw_icon(size: int = 1024) -> Image.Image:
    scale = 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def s(value: float) -> int:
        return int(round(value * scale))

    # Shadow and rounded app tile.
    shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    rr(sd, (s(74), s(86), s(950), s(940)), s(206), (0, 0, 0, 210))
    shadow = shadow.filter(ImageFilter.GaussianBlur(s(34)))
    image.alpha_composite(shadow)

    rr(draw, (s(70), s(58), s(954), s(942)), s(196), (13, 18, 20, 255))
    rr(draw, (s(96), s(84), s(928), s(916)), s(168), (22, 30, 32, 255), (76, 92, 91, 255), s(6))

    # Subtle machined grid.
    for mm in range(176, 850, 86):
        alpha = 26 if mm % 172 else 38
        draw.line((s(mm), s(142), s(mm), s(880)), fill=(189, 204, 194, alpha), width=s(2))
        draw.line((s(142), s(mm), s(882), s(mm)), fill=(189, 204, 194, alpha), width=s(2))

    # Inner field plate.
    rr(draw, (s(166), s(166), s(858), s(858)), s(72), (217, 210, 190, 245), (245, 240, 221, 110), s(5))

    # Engraved dark field inside plate.
    rr(draw, (s(206), s(206), s(818), s(818)), s(50), (18, 28, 29, 255), (120, 136, 128, 120), s(4))
    for mm in range(246, 790, 84):
        draw.line((s(mm), s(226), s(mm), s(798)), fill=(216, 208, 188, 28), width=s(2))
        draw.line((s(226), s(mm), s(798), s(mm)), fill=(216, 208, 188, 28), width=s(2))

    # Laser path / MOPA pulse mark.
    beam = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    bd = ImageDraw.Draw(beam)
    bd.line(scale_points([(272, 322), (740, 686)], scale), fill=(255, 38, 38, 78), width=s(54))
    bd.line(scale_points([(276, 322), (738, 682)], scale), fill=(255, 67, 58, 230), width=s(14))
    bd.line(scale_points([(276, 322), (738, 682)], scale), fill=(255, 228, 210, 235), width=s(4))
    beam = beam.filter(ImageFilter.GaussianBlur(s(1.0)))
    image.alpha_composite(beam)

    m_path = [(238, 678), (330, 442), (424, 676), (518, 442), (612, 676)]
    draw.line(scale_points(m_path, scale), fill=(4, 22, 22, 255), width=s(78), joint="curve")
    draw.line(scale_points(m_path, scale), fill=(38, 208, 168, 255), width=s(52), joint="curve")
    draw.line(scale_points(m_path, scale), fill=(206, 255, 244, 245), width=s(13), joint="curve")

    # Galvo head / mirror.
    head = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    hd = ImageDraw.Draw(head)
    hd.ellipse((s(238), s(206), s(454), s(422)), fill=(8, 12, 13, 190))
    hd.ellipse((s(254), s(190), s(448), s(384)), fill=(229, 231, 224, 255), outline=(121, 131, 128, 255), width=s(6))
    hd.ellipse((s(294), s(230), s(408), s(344)), fill=(52, 61, 62, 255), outline=(245, 246, 237, 120), width=s(5))
    hd.line(scale_points([(330, 288), (742, 680)], scale), fill=(255, 255, 255, 120), width=s(8))
    head = head.filter(ImageFilter.GaussianBlur(s(0.2)))
    image.alpha_composite(head)

    # Frame target and red mark point.
    rr(draw, (s(640), s(572), s(792), s(724)), s(20), (0, 0, 0, 0), (38, 208, 168, 255), s(16))
    draw.ellipse((s(700), s(634), s(760), s(694)), fill=(255, 48, 48, 255), outline=(255, 235, 224, 230), width=s(5))
    draw.ellipse((s(715), s(649), s(745), s(679)), fill=(255, 228, 210, 255))

    # Lower label stripe, abstracted for small sizes.
    rr(draw, (s(250), s(778), s(774), s(832)), s(28), (11, 18, 19, 220), (38, 208, 168, 120), s(3))
    draw.line(scale_points([(306, 806), (718, 806)], scale), fill=(38, 208, 168, 230), width=s(8))

    # Specular top edge.
    draw.arc((s(128), s(106), s(896), s(884)), start=206, end=324, fill=(255, 255, 255, 42), width=s(8))

    return image.resize((size, size), Image.Resampling.LANCZOS)


def save_iconset() -> None:
    ICONSET.mkdir(exist_ok=True)
    for path in ICONSET.glob("*.png"):
        path.unlink()

    source = draw_icon(1024)
    PREVIEW.write_bytes(b"")
    source.save(PREVIEW)

    specs = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for name, out_size in specs:
        source.resize((out_size, out_size), Image.Resampling.LANCZOS).save(ICONSET / name)

    write_icns(
        source,
        [
            ("icp4", 16),
            ("icp5", 32),
            ("icp6", 64),
            ("ic07", 128),
            ("ic08", 256),
            ("ic09", 512),
            ("ic10", 1024),
        ],
        ICNS,
    )
    subprocess.run(["touch", str(ROOT / "MOPA Luiz.app")], check=True)


def write_icns(source: Image.Image, chunks: list[tuple[str, int]], out_path: Path) -> None:
    payload = bytearray()
    for chunk_type, out_size in chunks:
        buffer = io.BytesIO()
        source.resize((out_size, out_size), Image.Resampling.LANCZOS).save(buffer, format="PNG")
        png = buffer.getvalue()
        payload.extend(chunk_type.encode("ascii"))
        payload.extend(struct.pack(">I", len(png) + 8))
        payload.extend(png)

    out_path.write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + bytes(payload))


if __name__ == "__main__":
    save_iconset()
