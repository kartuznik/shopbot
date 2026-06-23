#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/bots/shopbot"
DB_PATH="$PROJECT_DIR/data/shopbot.db"
SERVICE_NAME="shopbot"

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  logger -t surveybot-check "Service $SERVICE_NAME is not active, restarting"
  systemctl restart "$SERVICE_NAME"
  exit 0
fi

if [ ! -f "$DB_PATH" ]; then
  logger -t surveybot-check "Database file not found: $DB_PATH"
  exit 0
fi

INTEGRITY="$(sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>/dev/null || true)"
if [ "$INTEGRITY" != "ok" ]; then
  logger -t surveybot-check "DB integrity failed ($INTEGRITY), restarting $SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  exit 1
fi

logger -t surveybot-check "Health check passed"
