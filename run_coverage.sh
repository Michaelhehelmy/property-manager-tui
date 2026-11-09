#!/data/data/com.termux/files/usr/bin/bash
set -e
cd "$(dirname "$0")"

pip install pytest pytest-cov --quiet

pytest \
    --cov=services \
    --cov=db \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    --cov-fail-under=99 \
    -q