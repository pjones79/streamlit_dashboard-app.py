"""Local image processing for dashboard assets (e.g. drop near-black mattes)."""
from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path


def png_drop_near_black(path: Path, *, threshold: int = 52) -> bytes | None:
    """
    Return PNG bytes with dark pixels (luminance at or below ``threshold``) set fully transparent.

    Removes common solid-black / dark-grey backgrounds from marketing renders so the
    aircraft blends with the page; does not require the source file to ship with alpha.
    """
    if not path.is_file():
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        import numpy as np
    except ImportError:
        return None

    img = Image.open(path).convert("RGBA")
    arr = np.asarray(img, dtype=np.uint8).copy()
    luma = (
        0.299 * arr[..., 0].astype(np.float32)
        + 0.587 * arr[..., 1].astype(np.float32)
        + 0.114 * arr[..., 2].astype(np.float32)
    )
    dark = luma <= float(threshold)
    arr[..., 3] = np.where(dark, np.uint8(0), arr[..., 3])
    out = Image.fromarray(arr, mode="RGBA")
    buf = BytesIO()
    out.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


@lru_cache(maxsize=8)
def _png_drop_near_black_cached(resolved_path: str, mtime_ms: int) -> bytes | None:
    return png_drop_near_black(Path(resolved_path))


def virgin_png_for_ui(path: Path) -> bytes | None:
    """Cached on path + mtime so edits to the asset are picked up without server restart."""
    if not path.is_file():
        return None
    try:
        ms = int(path.stat().st_mtime * 1000)
    except OSError:
        return None
    return _png_drop_near_black_cached(str(path.resolve()), ms)
