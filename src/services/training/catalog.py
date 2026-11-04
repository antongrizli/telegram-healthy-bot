"""Exercise catalog loader, indexing, and lookup."""
from __future__ import annotations

import difflib
import logging
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "exercise_catalog.yaml"


def normalize_catalog_string(s: str) -> str:
    """Normalize string for catalog matching: casefold, german umlauts and accent stripping."""
    s = s.lower().strip()
    s = s.replace("ß", "ss")
    s = s.replace("ä", "a").replace("ö", "o").replace("ü", "u")
    # Decompose unicode characters and strip diacritics
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Replace hyphens, underscores and extra spaces
    cleaned = "".join(c if c.isalnum() else " " for c in stripped)
    return " ".join(cleaned.split())


class ExerciseCatalog:
    _instance: Optional[ExerciseCatalog] = None

    def __init__(self, path: Path = CATALOG_PATH):
        self.path = path
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.normalized_index: Dict[str, str] = {}  # normalized_name -> catalog_key
        self.all_names: List[str] = []
        self._load()

    @classmethod
    def get_instance(cls) -> ExerciseCatalog:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self):
        if not self.path.exists():
            logger.warning("Exercise catalog not found at %s", self.path)
            return

        with open(self.path, "r", encoding="utf-8") as f:
            items = yaml.safe_load(f) or []

        for item in items:
            key = item["key"]
            self.entries[key] = item
            names_by_lang = item.get("names", {})
            for lang, names in names_by_lang.items():
                for name in names:
                    norm = normalize_catalog_string(name)
                    if norm:
                        self.normalized_index[norm] = key
                        if norm not in self.all_names:
                            self.all_names.append(norm)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.entries.get(key)

    def find_by_name(self, name: str, threshold: float = 0.85) -> Optional[Dict[str, Any]]:
        """Find catalog entry by name via exact or fuzzy normalized match."""
        norm = normalize_catalog_string(name)
        if not norm:
            return None

        # 1. Exact match
        if norm in self.normalized_index:
            return self.entries.get(self.normalized_index[norm])

        # 2. Substring match for short phrases (e.g. "Latzug" in "Latzugmaschine")
        for indexed_name, key in self.normalized_index.items():
            if norm == indexed_name or (len(norm) >= 4 and norm in indexed_name) or (len(indexed_name) >= 4 and indexed_name in norm):
                # If length difference is small enough
                if abs(len(norm) - len(indexed_name)) <= 4:
                    return self.entries.get(key)

        # 3. Fuzzy match
        matches = difflib.get_close_matches(norm, self.all_names, n=1, cutoff=threshold)
        if matches:
            matched_key = self.normalized_index[matches[0]]
            return self.entries.get(matched_key)

        return None

    def get_cues(self, key: str, lang: str = "ru") -> List[str]:
        entry = self.entries.get(key)
        if not entry:
            return []
        cues_dict = entry.get("cues", {})
        if lang in cues_dict:
            return cues_dict[lang]
        if "ru" in cues_dict:
            return cues_dict["ru"]
        if "en" in cues_dict:
            return cues_dict["en"]
        return next(iter(cues_dict.values()), [])

    def get_alternatives(self, key: str) -> List[Dict[str, Any]]:
        entry = self.entries.get(key)
        if not entry:
            return []
        alt_keys = entry.get("alternatives", [])
        return [self.entries[k] for k in alt_keys if k in self.entries]


# Global accessor
def get_catalog() -> ExerciseCatalog:
    return ExerciseCatalog.get_instance()
