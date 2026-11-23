#!/bin/bash
# Cron wrapper: generate playoff odds and append output to the cron log
LOG=/tmp/icechaser_cron.log
TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
{
  echo "===== IceChaser odds run started $TS ====="
} >> "$LOG"

python3 /root/.openclaw/workspace/projects/icechaser/backend/generate_data_v3.py >> "$LOG" 2>&1
EXIT=$?

{
  echo "===== IceChaser odds run finished $(date -u '+%Y-%m-%d %H:%M:%S UTC') exit=$EXIT ====="
} >> "$LOG"

exit $EXIT
