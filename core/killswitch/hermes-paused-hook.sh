#!/bin/sh
# Hermes shell hook for the events pre_tool_call and pre_llm_call.
# 1. While the pause flag exists, it blocks every tool call and every model turn.
# 2. While the budget flag says "slowdown", it adds a reminder to every model turn.
# Root owns this file. The Hermes managed config points at it.
# Output is one JSON line. Exit code 0 means the hook ran. The block comes from the JSON.
FLAG=/var/lib/agent-fleet/PAUSED
BUDGET=/var/lib/agent-fleet/budget.json
if [ -e "$FLAG" ]; then
  printf '%s\n' '{"action":"block","message":"The fleet is paused by the owner. Do not start new work. End your turn now."}'
  exit 0
fi
if [ -r "$BUDGET" ] && grep -q '"slowdown": *true' "$BUDGET" 2>/dev/null; then
  printf '%s\n' '{"context":"Budget alert: a free daily model limit is near. Slow down. Do only the most important task. Read /fleet-state/status/budget.json."}'
  exit 0
fi
printf '%s\n' '{}'
exit 0
