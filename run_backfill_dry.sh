#!/usr/bin/env bash
cd /root/.openclaw/workspace/projects/icechaser
python3 backend/backfill_history.py --dry-run --start 2025-10-07 --end 2025-10-09 2>&1