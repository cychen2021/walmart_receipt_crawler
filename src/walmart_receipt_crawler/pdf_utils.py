from __future__ import annotations

from pathlib import Path
from typing import Sequence

from rich.console import Console

try:
    # pypdf >=5 removed PdfMerger in favor of PdfWriter.append + merge operations; try both.
    from pypdf import PdfMerger as _PdfMerger  # type: ignore

    PdfMergerClass = _PdfMerger
except Exception:
    try:
        from pypdf import PdfWriter

        PdfMergerClass = None  # signal to use writer concatenation
    except Exception as _e:  # pragma: no cover
        raise RuntimeError("pypdf is required. Install with 'uv add pypdf'.") from _e


def merge_pdfs(
    paths: Sequence[Path], out_path: Path, console: Console | None = None
) -> Path:
    if PdfMergerClass is not None:
        merger = PdfMergerClass()
        for p in paths:
            merger.append(str(p))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f:
            merger.write(f)
        merger.close()
    else:
        # Fallback for newer pypdf versions where PdfMerger was removed.
        writer = PdfWriter()
        from pypdf import PdfReader

        for p in paths:
            reader = PdfReader(str(p))
            for page in reader.pages:
                writer.add_page(page)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f:
            writer.write(f)
    if console:
        console.log(f"Merged {len(paths)} PDF(s) into {out_path}")
    return out_path
