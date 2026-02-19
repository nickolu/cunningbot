"""ZIP code to latitude/longitude lookup.

Loads the bundled CSV once at import time into an in-memory dict.
The CSV is baked into the Docker image via COPY . . and is always available
at docs/zipCodeToLatLong.csv relative to the project root.
"""

import csv
import os
from typing import Dict, Optional, Tuple

_ZIP_TABLE: Dict[str, Tuple[float, float]] = {}


def _load_zip_table() -> None:
    csv_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../docs/zipCodeToLatLong.csv")
    )
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Strip whitespace from all keys and values to handle CSV formatting quirks
            stripped = {k.strip(): v.strip() for k, v in row.items()}
            zip_code = stripped.get("zip", "").zfill(5)
            try:
                lat = float(stripped["latitude"])
                lon = float(stripped["longitude"])
                _ZIP_TABLE[zip_code] = (lat, lon)
            except (ValueError, KeyError):
                pass


_load_zip_table()


def lookup_zip(zip_code: str) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) for the given ZIP code, or None if not found."""
    normalized = zip_code.strip().zfill(5)
    return _ZIP_TABLE.get(normalized)


def is_valid_zip(zip_code: str) -> bool:
    """Return True if the ZIP code exists in the lookup table."""
    return lookup_zip(zip_code) is not None
