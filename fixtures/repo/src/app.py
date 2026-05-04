"""Northstar Rentals booking API entry point (mock)."""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_environment_config(env: str) -> dict:
    return json.loads((CONFIG_DIR / f"{env}.json").read_text(encoding="utf-8"))


def get_token(env: str) -> str:
    config = load_environment_config(env)
    return os.environ[config["token_var"]]


def get_volume_id(env: str) -> str:
    config = load_environment_config(env)
    return config["volume_id"]


if __name__ == "__main__":
    print("Northstar Rentals API (mock)")
