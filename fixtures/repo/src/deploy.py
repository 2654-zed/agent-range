"""Deployment orchestrator (mock)."""
from __future__ import annotations

import sys

from app import load_environment_config


def deploy(env: str) -> None:
    config = load_environment_config(env)
    print(f"Deploying to {config['deploy_target']} ({env})")


if __name__ == "__main__":
    env = sys.argv[1] if len(sys.argv) > 1 else "staging"
    deploy(env)
