#!/bin/bash
cd "$(dirname "$0")/.."
uv run python experiments/run_exp01.py 2>&1 | tee experiments/exp01_run.log
