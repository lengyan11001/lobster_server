from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OEM_ROOT = ROOT / "client_static" / "oem"
MANIFEST_PATH = OEM_ROOT / "manifest.json"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
BRAND_VERSIONS = {
    "hikong": "2026.08.06.2",
    "jinghai": "2026.08.06.2",
}


def _trim(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if not bbox:
        raise ValueError("The source image has no visible pixels")
    return image.crop(bbox)


def _hikong_mark() -> Image.Image:
    source = Image.open(OEM_ROOT / "hikong" / "logo_1024.png").convert("RGBA")
    # The upper portion is the standalone HK symbol; the lower portion is the wordmark.
    mark_area = source.crop((0, 0, source.width, round(source.height * 0.60)))
    pixels = mark_area.load()
    color_mask = Image.new("L", mark_area.size, 0)
    mask_pixels = color_mask.load()
    for y in range(mark_area.height):
        for x in range(mark_area.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha > 20 and max(red, green, blue) - min(red, green, blue) > 20:
                mask_pixels[x, y] = 255
    bbox = color_mask.getbbox()
    if not bbox:
        raise ValueError("Could not isolate the Hikong mark from logo_1024.png")
    padding = 2
    bbox = (
        max(0, bbox[0] - padding),
        max(0, bbox[1] - padding),
        min(mark_area.width, bbox[2] + padding),
        min(mark_area.height, bbox[3] + padding),
    )
    return mark_area.crop(bbox)


def _jinghai_mark() -> Image.Image:
    source = Image.open(OEM_ROOT / "jinghai" / "logo_source.png").convert("RGBA")
    pixels = source.load()
    mask = Image.new("L", source.size, 0)
    mask_pixels = mask.load()
    for y in range(source.height):
        for x in range(source.width):
            red, green, blue, alpha = pixels[x, y]
            # The supplied source uses a white mark over a teal panel. Preserve the
            # antialiased mark edge while excluding the saturated panel and border.
            neutral = max(red, green, blue) - min(red, green, blue) <= 36
            whiteness = min(red, green, blue)
            if alpha > 0 and neutral and whiteness > 170:
                mask_pixels[x, y] = min(alpha, max(0, (whiteness - 170) * 3))

    bbox = mask.getbbox()
    if not bbox:
        raise ValueError("Could not isolate the Jinghai mark from logo_source.png")
    mask = mask.crop(bbox)
    mark = Image.new("RGBA", mask.size, (11, 120, 149, 0))
    mark.putalpha(mask)
    return _trim(mark)


def _render_icon(mark: Image.Image, size: int, width_ratio: float) -> Image.Image:
    target_width = round(size * width_ratio)
    target_height = round(size * 0.76)
    scale = min(target_width / mark.width, target_height / mark.height)
    dimensions = (
        max(1, round(mark.width * scale)),
        max(1, round(mark.height * scale)),
    )
    resized = mark.resize(dimensions, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    offset = ((size - resized.width) // 2, (size - resized.height) // 2)
    canvas.alpha_composite(resized, offset)
    return canvas


def _generate_brand(brand: str, mark: Image.Image, width_ratio: float) -> None:
    brand_root = OEM_ROOT / brand
    rendered: dict[int, Image.Image] = {}
    for size in ICON_SIZES:
        image = _render_icon(mark, size, width_ratio)
        image.save(brand_root / f"icon_{size}.png", optimize=True)
        rendered[size] = image

    rendered[1024].save(
        brand_root / "desktop.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES if size <= 256],
    )
    if brand == "jinghai":
        rendered[1024].save(brand_root / "logo_mark_1024.png", optimize=True)


def _asset_stats(path: Path) -> tuple[int, str]:
    payload = path.read_bytes()
    return len(payload), hashlib.sha256(payload).hexdigest()


def _replace_brand_section(text: str, brand: str, replacement) -> str:
    start = text.index(f'    "{brand}": {{')
    next_brand = text.find('\n    "', start + 8)
    end = next_brand if next_brand >= 0 else text.rfind("\n  }")
    section = text[start:end]
    return text[:start] + replacement(section) + text[end:]


def _update_manifest() -> None:
    text = MANIFEST_PATH.read_text(encoding="utf-8")

    for brand, version in BRAND_VERSIONS.items():
        brand_root = OEM_ROOT / brand

        def update_section(section: str) -> str:
            section = re.sub(
                r'("version":\s*")[^"]+("[,])',
                rf'\g<1>{version}\2',
                section,
                count=1,
            )
            records = re.findall(
                r'\{"key": "([^"]+)", "url": "([^"]+)", "size": \d+, "sha256": "[0-9a-f]+"\}',
                section,
            )
            for key, url in records:
                if not url.startswith("/client/oem/"):
                    raise ValueError(f"Unexpected OEM asset URL: {url}")
                path = OEM_ROOT / url.removeprefix("/client/oem/")
                if not path.is_file():
                    raise FileNotFoundError(path)
                size, digest = _asset_stats(path)
                pattern = re.compile(
                    rf'(\{{"key": "{re.escape(key)}", "url": "{re.escape(url)}", "size": )\d+'
                    rf'(, "sha256": ")[0-9a-f]+("\}})'
                )
                section, count = pattern.subn(rf'\g<1>{size}\g<2>{digest}\3', section, count=1)
                if count != 1:
                    raise ValueError(f"Could not update manifest record for {brand}/{key}")
            return section

        text = _replace_brand_section(text, brand, update_section)

    MANIFEST_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    _generate_brand("hikong", _hikong_mark(), width_ratio=0.84)
    _generate_brand("jinghai", _jinghai_mark(), width_ratio=0.86)
    _update_manifest()
    print("Regenerated Hikong and Jinghai icon sets and refreshed the OEM manifest.")


if __name__ == "__main__":
    main()
