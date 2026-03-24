#!/usr/bin/env python3
"""
Wrapper that runs the Linear-Slack bot in a loop.
Designed for Render.com Background Worker service.
"""
import time
import signal
import sys

from linear_slack_bot import main as run_check

running = True


def shutdown(signum, frame):
    global running
    print("Shutting down gracefully...")
    running = False


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

print("Linear-Slack Bot started. Checking every 3 minutes.")
while running:
    try:
        run_check()
    except Exception as e:
        print(f"Error in check cycle: {e}")
    if running:
        time.sleep(180)
print("Bot stopped.")
