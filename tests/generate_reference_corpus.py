#!/usr/bin/env python3
"""Generate reference hero images and Pillow-rendered .argb reference files.

NOTE: This script is an offline reference generation tool and oracle generator.
It requires Pillow (`pip install Pillow` or `python-pillow` in an external environment).
The resulting reference files in `tests/fixtures/artwork_reference/` are committed
to git as the permanent verification oracle.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "artwork_reference"
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def _fast_feather_mask(
    width: int, height: int, feather: int, *, horizontal: bool = False
) -> Image.Image:
    length = width if horizontal else height
    gradient = bytearray(length)
    denominator = max(1, feather - 1)
    for position in range(feather):
        gradient[position] = int(255 * position / denominator)
    gradient[feather : length - feather] = b"\xff" * (length - 2 * feather)
    for position in range(feather):
        gradient[length - feather + position] = int(255 * (feather - 1 - position) / denominator)

    strip_size = (length, 1) if horizontal else (1, length)
    strip = Image.frombytes("L", strip_size, bytes(gradient))
    return strip.resize((width, height), Image.Resampling.NEAREST)


def render_pillow_artwork(source_path: Path, width: int, height: int, dim_factor: float) -> bytes:
    dim_millis = round(max(0.0, min(1.0, dim_factor)) * 1000)
    brightness = 1.0 - dim_millis / 1000

    with Image.open(source_path) as artwork:
        artwork = artwork.convert("RGB")
        src_w, src_h = artwork.width, artwork.height

        # Backdrop: Determine source crop bounding box in source coordinates
        target_ar = width / height
        src_ar = src_w / src_h
        if src_ar > target_ar:
            crop_w = round(src_h * target_ar)
            crop_left = (src_w - crop_w) // 2
            crop_box = (crop_left, 0, crop_left + crop_w, src_h)
        else:
            crop_h = round(src_w / target_ar)
            crop_top = (src_h - crop_h) // 2
            crop_box = (0, crop_top, src_w, crop_top + crop_h)

        # Downscaled backdrop: Resize cropped source to 1/8 scale and blur
        downscale = 8
        low_w = max(1, width // downscale)
        low_h = max(1, height // downscale)
        backdrop_low = artwork.resize((low_w, low_h), Image.Resampling.BILINEAR, box=crop_box)
        blur_radius = max(2, (width // 60) // downscale)
        backdrop_low = backdrop_low.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        backdrop_low = ImageEnhance.Brightness(backdrop_low).enhance(0.45 * brightness)

        # Upscale blurred backdrop to target dimensions
        backdrop = backdrop_low.resize((width, height), Image.Resampling.BILINEAR)

        # Foreground: Contain the complete artwork and feather the exposed axis
        artwork_dimmed = ImageEnhance.Brightness(artwork).enhance(0.75 * brightness)
        fg_scale = min(width / src_w, height / src_h)
        fg_width = max(1, round(src_w * fg_scale))
        fg_height = max(1, round(src_h * fg_scale))
        foreground = artwork_dimmed.resize((fg_width, fg_height), Image.Resampling.LANCZOS)

        mask = None
        if fg_width < width:
            feather = max(1, min(fg_width // 4, (width - fg_width) // 2 + fg_width // 8))
            mask = _fast_feather_mask(fg_width, fg_height, feather, horizontal=True)
        elif fg_height < height:
            feather = max(1, min(fg_height // 4, (height - fg_height) // 2 + fg_height // 8))
            mask = _fast_feather_mask(fg_width, fg_height, feather)

        position = ((width - fg_width) // 2, (height - fg_height) // 2)
        backdrop.paste(foreground, position, mask)

        return backdrop.convert("RGBA").tobytes("raw", "BGRA")


def create_pattern_image(width: int, height: int) -> Image.Image:
    im = Image.new("RGB", (width, height), color=(20, 30, 40))
    draw = ImageDraw.Draw(im)

    # Draw color stripes and diagonal shapes
    for y in range(height):
        r = int(255 * y / max(1, height - 1))
        for x in range(0, width, 20):
            g = int(255 * x / max(1, width - 1))
            b = int(255 * ((x + y) % max(1, width)) / max(1, width - 1))
            draw.rectangle([x, y, min(width - 1, x + 19), y], fill=(r, g, b))

    # Add geometric shapes (circles, rectangles)
    draw.rectangle(
        [width // 8, height // 8, width // 4, height // 4],
        fill=(240, 50, 50),
    )
    draw.ellipse(
        [
            width // 2 - width // 8,
            height // 2 - height // 8,
            width // 2 + width // 8,
            height // 2 + height // 8,
        ],
        fill=(50, 240, 50),
    )
    draw.rectangle(
        [3 * width // 4, 3 * height // 4, 7 * width // 8, 7 * height // 8],
        fill=(50, 50, 240),
    )
    return im


def create_highfreq_image(width: int, height: int) -> Image.Image:
    """Zone plate plus deterministic noise: broadband content all the way to Nyquist.

    The smooth gradients of create_pattern_image barely move a sinc kernel, so cases built
    from it understated the Lanczos delta against Pillow roughly fourfold versus real Steam
    hero art. This pattern is deliberately harder than real artwork, so a green corpus is
    evidence about production content rather than about the fixture.
    """
    buf = bytearray(width * height * 3)
    center_x, center_y = width / 2.0, height / 2.0
    k = math.pi / (2.0 * max(1.0, min(center_x, center_y)))
    seed = 0x2545F491
    i = 0
    for y in range(height):
        dy = y - center_y
        for x in range(width):
            dx = x - center_x
            base = 128.0 + 110.0 * math.cos(k * (dx * dx + dy * dy))
            seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
            noise = ((seed >> 16) & 0xFF) - 128
            buf[i] = max(0, min(255, int(base + noise * 0.45)))
            buf[i + 1] = max(0, min(255, int(base * 0.85 + noise * 0.55)))
            buf[i + 2] = max(0, min(255, int(base * 0.70 + noise * 0.40)))
            i += 3
    return Image.frombytes("RGB", (width, height), bytes(buf))


def main() -> None:
    cases = [
        # (case_id, src_w, src_h, target_w, target_h, dim_factor, note)
        # 1. Wide hero on standard 16:9 (vertical letterbox/feather)
        ("hero_wide_to_16x9", 960, 310, 320, 240, 0.4, "letterbox vertical feather"),
        # 2. Standard 16:9 hero on 16:9 (exact aspect fit, F=0 opaque)
        ("hero_16x9_to_16x9", 640, 360, 320, 180, 0.0, "exact match opaque"),
        # 3. Standard 16:9 hero on ultrawide 21:9 (horizontal pillarbox/feather)
        ("hero_16x9_to_ultrawide", 640, 360, 420, 180, 0.5, "pillarbox horizontal feather"),
        # 4. Portrait hero on 16:9 (horizontal pillarbox/feather)
        ("hero_portrait_to_16x9", 300, 450, 320, 180, 0.2, "portrait pillarbox"),
        # 5. Full 1080p target (1920x620 hero on 1920x1080)
        ("hero_1080p_wide", 1920, 620, 1920, 1080, 0.4, "full resolution letterbox"),
        # 6. Real monitor 1600x900 target (exercises Lanczos downscale with fg_scale = 1600/1920 = 0.833)
        ("hero_1600x900_downscale", 1920, 620, 1600, 900, 0.4, "lanczos downscale 1600x900"),
        # 7. Real monitor 1280x1024 target (exercises Lanczos downscale with fg_scale = 1280/1920 = 0.667)
        ("hero_1280x1024_downscale", 1920, 620, 1280, 1024, 0.3, "lanczos downscale 1280x1024"),
        # 8. Broadband content at a real monitor size: the case that actually stresses the
        #    kernel, and the one deliberately kept at full resolution as the upper bound.
        ("hero_highfreq_1600x900", 1920, 620, 1600, 900, 0.4, "broadband lanczos downscale"),
        # 9. Second broadband scale (fg_scale 0.667), kept small since the bound is case 8.
        ("hero_highfreq_640x512", 960, 310, 640, 512, 0.3, "broadband lanczos downscale 0.667"),
        # 10. PNG source: Steam stores some hero art as PNG under the library_hero.jpg name.
        ("hero_png_source", 640, 360, 960, 540, 0.4, "png source letterbox"),
    ]
    highfreq_cases = {"hero_highfreq_1600x900", "hero_highfreq_640x512"}
    png_cases = {"hero_png_source"}

    manifest = {}

    for case_id, src_w, src_h, target_w, target_h, dim_factor, note in cases:
        if case_id in highfreq_cases:
            src_img = create_highfreq_image(src_w, src_h)
        else:
            src_img = create_pattern_image(src_w, src_h)
        if case_id in png_cases:
            src_path = FIXTURES_DIR / f"{case_id}_input.png"
            src_img.save(src_path, "PNG")
        else:
            src_path = FIXTURES_DIR / f"{case_id}_input.jpg"
            src_img.save(src_path, "JPEG", quality=92)

        argb_bytes = render_pillow_artwork(src_path, target_w, target_h, dim_factor)
        out_path = (
            FIXTURES_DIR / f"{case_id}_ref_{target_w}x{target_h}_d{int(dim_factor * 100):02d}.argb"
        )
        out_path.write_bytes(argb_bytes)

        sha = hashlib.sha256(argb_bytes).hexdigest()
        manifest[case_id] = {
            "src_path": src_path.name,
            "src_size": [src_w, src_h],
            "target_size": [target_w, target_h],
            "dim_factor": dim_factor,
            "note": note,
            "ref_file": out_path.name,
            "sha256": sha,
            "byte_len": len(argb_bytes),
        }
        print(
            f"Generated {case_id}: {out_path.name} ({len(argb_bytes)} bytes, sha256={sha[:12]}...)"
        )

    manifest_path = FIXTURES_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Saved manifest to {manifest_path}")


if __name__ == "__main__":
    main()
