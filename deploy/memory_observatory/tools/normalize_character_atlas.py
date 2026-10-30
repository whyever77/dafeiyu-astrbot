"""Rebuild the 4x2 character sheets on an integer grid with transparent gutters.

The generated sheets are 1774x887, so each frame sits on a 443.5 px boundary and
Canvas bilinear sampling can blend neighbouring frames. This tool crops every
frame on integer boundaries, places it in a 444x444 cell and separates the cells
with a 2 px transparent gutter. Run it once after regenerating the artwork:

    python -B deploy/memory_observatory/tools/normalize_character_atlas.py

Requires Pillow (development-only; the observatory server uses the standard library).
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image

CELL = 444
GAP = 2
PAD = 2
COLS, ROWS = 4, 2
NAMES = ("idle", "walk", "desk", "shelf", "window", "rest")
SHEET_SIZE = (PAD * 2 + COLS * CELL + (COLS - 1) * GAP,
              PAD * 2 + ROWS * CELL + (ROWS - 1) * GAP)


def assert_gutters(image: Image.Image) -> None:
    alpha = image.getchannel("A")
    for col in range(COLS - 1):
        start = PAD + col * (CELL + GAP) + CELL
        for x in range(start, start + GAP):
            assert alpha.crop((x, 0, x + 1, image.height)).getbbox() is None, f"gutter column {x} is not transparent"
    for row in range(ROWS - 1):
        start = PAD + row * (CELL + GAP) + CELL
        for y in range(start, start + GAP):
            assert alpha.crop((0, y, image.width, y + 1)).getbbox() is None, f"gutter row {y} is not transparent"


def rebuild(path: Path) -> tuple[tuple[int, int], tuple[int, int]]:
    source = Image.open(path).convert("RGBA")
    width, height = source.size
    cell_w, cell_h = width / COLS, height / ROWS
    sheet = Image.new("RGBA", SHEET_SIZE, (0, 0, 0, 0))
    for row in range(ROWS):
        for col in range(COLS):
            left, right = math.ceil(col * cell_w), math.ceil((col + 1) * cell_w)
            top, bottom = math.ceil(row * cell_h), math.ceil((row + 1) * cell_h)
            cell = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
            cell.paste(source.crop((left, top, right, bottom)), (0, 0))
            sheet.alpha_composite(cell, (PAD + col * (CELL + GAP), PAD + row * (CELL + GAP)))
    assert_gutters(sheet)
    sheet.save(path, optimize=True)
    return source.size, sheet.size


def main() -> None:
    parser = argparse.ArgumentParser(description="把角色精灵图重排为整数网格并加透明隔断")
    parser.add_argument("directory", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "static" / "character")
    args = parser.parse_args()
    for name in NAMES:
        path = args.directory / f"{name}.png"
        before, after = rebuild(path)
        print(f"{path.name}: {before[0]}x{before[1]} -> {after[0]}x{after[1]}")


if __name__ == "__main__":
    main()
