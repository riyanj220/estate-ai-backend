"""The labelled query set and how a result is compared with it."""

import json
from pathlib import Path

import yaml

from apps.catalog import selectors as catalog

PATH = Path(__file__).resolve().parent / "labelled_queries.yaml"


def load() -> list[dict]:
    return yaml.safe_load(PATH.read_text(encoding="utf-8"))


def answer_of(item: dict) -> str:
    return json.dumps(item["answer"])


def named(params: dict) -> dict:
    """`params` with city and locality ids as names (localities sorted), as the set writes them."""
    shown = dict(params)
    for key, value in params.items():
        name = key.removeprefix("prefer_")
        if name == "city":
            shown[key] = catalog.get_city(value).name
        elif name == "locality":
            names = catalog.locality_names([catalog.parse_uuid(v) for v in value.split(",")])
            shown[key] = sorted(names.values())
    return shown
