#!/usr/bin/env bash
cd /root/.openclaw/workspace/projects/icechaser
python3 backend/backfill_history.py --sims 10000 2>&1