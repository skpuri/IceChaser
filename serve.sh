#!/bin/bash
# Quick dev server for IceChaser
# Generates fresh data then serves the frontend

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🏒 Generating fresh playoff odds..."
cd "$SCRIPT_DIR/backend"
python3 generate_data.py

echo ""
echo "🌐 Starting dev server at http://localhost:8080"
echo "   Press Ctrl+C to stop"
cd "$SCRIPT_DIR/frontend"
python3 -m http.server 8080
