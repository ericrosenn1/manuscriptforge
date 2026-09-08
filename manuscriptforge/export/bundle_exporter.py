from __future__ import annotations

import zipfile
from pathlib import Path


def export_bundle(run_dir: Path, bundle_path: Path | None = None) -> Path:
    bundle_path = bundle_path or run_dir / "manuscriptforge_bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file() and path != bundle_path:
                archive.write(path, path.relative_to(run_dir))
    return bundle_path
