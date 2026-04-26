"""Helpers for resolving Swiss city coordinates from `ch_ortsverzeichnis.csv`."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import unicodedata


def normalize_place_name(value: str) -> str:
    """Normalize place names for matching."""
    if not value:
        return ""

    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\bste[.\- ]", "sainte ", text)
    text = re.sub(r"\bst[.\- ]", "saint ", text)
    text = re.sub(r"[()\[\]{},.;:/\\\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_ch_ortsverzeichnis(path: Path) -> Dict[str, List[Dict[str, object]]]:
    """Load Swiss place directory and index it by postcode."""
    if not path.exists():
        raise FileNotFoundError(f"Missing Swiss place directory: {path}")

    index: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as f_in:
        reader = csv.DictReader(f_in, delimiter=";")
        for row in reader:
            postcode = (row.get("PLZ4") or "").strip()
            if not postcode:
                continue

            try:
                east = float((row.get("E") or "").replace(",", "."))
                north = float((row.get("N") or "").replace(",", "."))
            except ValueError:
                continue

            share_raw = (row.get("Adressenanteil") or "0").replace("%", "").strip().replace(",", ".")
            try:
                share = float(share_raw)
            except ValueError:
                share = 0.0

            city_name = (row.get("Ortschaftsname") or "").strip()
            community_name = (row.get("Gemeindename") or "").strip()

            index[postcode].append(
                {
                    "postcode": postcode,
                    "city_name": city_name,
                    "community_name": community_name,
                    "city_name_norm": normalize_place_name(city_name),
                    "community_name_norm": normalize_place_name(community_name),
                    "share": share,
                    "coordinates": (north, east),
                }
            )

    return index


def resolve_city_coordinates(
        index: Dict[str, List[Dict[str, object]]],
        postcode: str,
        city: str,
        country: str,
) -> Tuple[float, float] | None:
    """Resolve Swiss city coordinates from the local ortsverzeichnis."""
    if not postcode and not city:
        return None

    if country and country.strip().upper() not in {"CH", "CHE", "SWITZERLAND"}:
        return None

    candidates = index.get(str(postcode).strip(), [])
    if not candidates:
        return None

    normalized_city = normalize_place_name(city)

    def candidate_matches(candidate: Dict[str, object]) -> bool:
        aliases = [candidate.get("city_name_norm", ""), candidate.get("community_name_norm", "")]
        aliases = [alias for alias in aliases if alias]
        for alias in aliases:
            if normalized_city == alias or normalized_city in alias or alias in normalized_city:
                return True
        return False

    matching_candidates = [candidate for candidate in candidates if candidate_matches(candidate)]
    if matching_candidates:
        chosen = max(matching_candidates, key=lambda c: float(c.get("share", 0.0)))
    else:
        chosen = max(candidates, key=lambda c: float(c.get("share", 0.0)))

    coordinates = chosen.get("coordinates")
    if isinstance(coordinates, tuple) and len(coordinates) == 2:
        return float(coordinates[0]), float(coordinates[1])
    return None
