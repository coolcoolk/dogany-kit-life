#!/usr/bin/env bash
# Manifest lifecycle runs in the instance root, after schema migrations.
# Required: failure aborts install before it can acknowledge delivery complete.
set -euo pipefail
exec python3 "$PWD/database/satiety_migrate.py" --auto --instance "$PWD"
