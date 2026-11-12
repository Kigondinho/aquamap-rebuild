"""Dataset provenance: where data came from, when, under which licence, and with which limits.

Each dataset file gets a sidecar ``<name>.provenance.json`` next to it. The pipeline loader
(a later phase) copies these records into the ``spatial_layers`` table.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

OSM_LICENCE = "Open Database Licence (ODbL) 1.0 - https://opendatacommons.org/licenses/odbl/1-0/"
OSM_ATTRIBUTION = "© OpenStreetMap contributors (https://www.openstreetmap.org/copyright)"
SRTM_LICENCE = (
    "SRTM is a public-domain NASA/NGA dataset distributed by USGS; USGS requests credit when "
    "redistributed or published (tilezen/joerd docs/attribution.md)."
)
SRTM_ATTRIBUTION = "SRTM terrain data courtesy of the U.S. Geological Survey"

_TOOLS = ("geopandas", "shapely", "pyproj", "rasterio", "numpy", "osmnx", "requests")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def tool_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name in _TOOLS:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


@dataclass
class Provenance:
    """One acquired dataset. ``retrieved_at`` is the UTC time the download finished."""

    dataset: str
    source_name: str
    source_url: str
    licence: str
    attribution: str
    method: str
    parameters: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    retrieved_at: str = field(default_factory=utc_now_iso)
    files: list[dict[str, Any]] = field(default_factory=list)
    feature_count: int | None = None
    crs: str | None = None
    stage: str = "raw"  # raw | processed
    derived_from: list[str] = field(default_factory=list)
    processing_log: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=tool_versions)

    def add_file(self, path: Path, root: Path | None = None) -> None:
        shown = path.relative_to(root).as_posix() if root and path.is_relative_to(root) else path.name
        self.files.append({"path": shown, "bytes": path.stat().st_size, "sha256": sha256_file(path)})


def sidecar_path(data_file: Path) -> Path:
    return data_file.with_name(data_file.name + ".provenance.json")


def write_provenance(record: Provenance, data_file: Path) -> Path:
    target = sidecar_path(data_file)
    target.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def read_provenance(data_file: Path) -> dict[str, Any] | None:
    target = sidecar_path(data_file)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def verify_checksums(data_file: Path) -> list[str]:
    """Compare a file with the hash in its sidecar. Returns problems (empty list means it matches)."""
    record = read_provenance(data_file)
    if record is None:
        return [f"{data_file.name}: no provenance sidecar"]
    for entry in record.get("files", []):
        if Path(entry["path"]).name == data_file.name:
            actual = sha256_file(data_file)
            if actual != entry["sha256"]:
                return [f"{data_file.name}: checksum differs from provenance record (file changed since download)"]
            return []
    return [f"{data_file.name}: not listed in its provenance record"]
