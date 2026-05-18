#!/bin/bash
# Weekly meal plan display update script
# Run this to push current week's meal plan to e-ink display

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/mnt/ssd1/llm/eink-meal-display/display_updates.log"

echo "[$(date)] Starting meal plan update..." >> "$LOG_FILE"

cd "$SCRIPT_DIR"
python3 send_meal_plan.py "$@" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date)] Update successful" >> "$LOG_FILE"
else
    echo "[$(date)] Update failed with code $EXIT_CODE" >> "$LOG_FILE"
fi

echo "" >> "$LOG_FILE"
exit $EXIT_CODE
