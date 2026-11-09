#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/src:$PWD${PYTHONPATH:+:$PYTHONPATH}"
pytest \
    --cov=app_core.services \
    --cov=app_core.db \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-fail-under=99 \
    -q
