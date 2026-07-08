"""Loads the function modules by file path for testing.

Each Cloud Function deploys from its own source dir and they all have a
main.py, so a plain `import main` would collide. We load each under a unique
name with importlib. Importing is side-effect-free (no BigQuery client is
created until a function actually runs).
"""

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FUNCTIONS = _ROOT / "functions"

# Each function folder is on sys.path at runtime (Cloud Functions adds the
# source dir), so main.py does sibling imports like `from error_log import ...`.
# Replicate that here so the modules import under test.
for _fn_dir in _FUNCTIONS.iterdir():
    if _fn_dir.is_dir():
        sys.path.insert(0, str(_fn_dir))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Pure-stdlib CSV/store_id logic shared by the scrape processor and insert_normalized.py
scrape_lib = _load("scrape_lib", _FUNCTIONS / "fn_retail_store_scrape_processor" / "_lib.py")

# The matcher — normalisation helpers + matching layers
matching = _load("matching_main", _FUNCTIONS / "fn_retail_store_matching" / "main.py")

# The geocoder — response parsing
geocoder = _load("geocoder_main", _FUNCTIONS / "fn_store_geocoder" / "main.py")
