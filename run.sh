#!/usr/bin/env bash
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
exec python -u controller.py --url "$SIM_URL"
