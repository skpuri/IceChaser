#!/bin/bash
LOG=/tmp/icechaser_cron.log
{
  echo "=== IceChaser odds cron run: $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
  python3 /root/.openclaw/workspace/projects/icechaser/backend/generate_data_v3.py
  echo "=== exit=$? at $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
} >> "$LOG" 2>&1
