#!/usr/bin/env python3
"""CLI entrypoint for Triple Pipeline visualization build."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.triple_pipeline_viz import run_cli


if __name__ == "__main__":
    run_cli()
