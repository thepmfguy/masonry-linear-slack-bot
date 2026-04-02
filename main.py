#!/usr/bin/env python3
"""
Linear -> Slack webhook server.
Receives webhook POST requests from Linear and posts notifications to Slack.
Uses only Python stdlib - no external packages required.

Designed for Render.com Web Service deployment.
"""

import json
import os
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from slack_notifier import (
    log,
    notify_new_ticket,
    notify_reassignment,
    notify_status_change,
    notify_new_comment,
)

PORT = int(os.environ.get("PORT", "10000"))


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Linear webhooks."""

    def _send_response(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _send_json(self, status, data):
        self._send_response(status, json.dumps(data))

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/":
            self._send_response(200, json.dumps({
                "service": "linear-slack-bot",
                "mode": "webhook",
                "status": "running",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }))
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/webhook":
            self._send_json(404, {"error": "not found"})
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "empty body"})
            return

        try:
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log("ERROR: Failed to parse webhook body: %s" % e)
            self._send_json(400, {"error": "invalid JSON"})
            return

        # Return 200 quickly - Linear expects fast responses
        self._send_json(200, {"ok": True})

        # Process the webhook
        try:
            handle_webhook(payload)
        except Exception as e:
            log("ERROR: Webhook handler crashed: %s" % e)

    def log_message(self, format, *args):
        """Override to use our own logging format."""
        log("HTTP %s" % (format % args))


def handle_webhook(payload):
    """Route a Linear webhook payload to the appropriate notification function."""
    action = payload.get("action")
    event_type = payload.get("type")
    data = payload.get("data")

    if not action or not event_type:
        log("WARNING: Webhook missing action or type fields, ignoring")
        return

    if not data:
        log("WARNING: Webhook missing data field, ignoring")
        return

    webhook_id = payload.get("webhookId", "?")
    log("Webhook received: action=%s type=%s webhookId=%s" % (action, event_type, webhook_id))

    # --- Team filter: only process Masonry events ---
    if event_type == "Issue":
        team_data = data.get("team", {})
        team_name = team_data.get("name", "") if isinstance(team_data, dict) else str(team_data)
        if team_name and team_name != "Masonry":
            log("Skipping non-Masonry team event: %s" % team_name)
            return

    if event_type == "Comment":
        issue_data = data.get("issue", {})
        team_data = issue_data.get("team", {})
        team_name = team_data.get("name", "") if isinstance(team_data, dict) else str(team_data)
        if team_name and team_name != "Masonry":
            log("Skipping non-Masonry comment: %s" % team_name)
            return

    # --- Issue events ---
    if event_type == "Issue":
        if action == "create":
            notify_new_ticket(data)

        elif action == "update":
            updated_from = payload.get("updatedFrom") or {}

            # Check for assignee change
            if "assigneeId" in updated_from:
                notify_reassignment(data, updated_from)

            # Check for status change
            if "stateId" in updated_from:
                notify_status_change(data, updated_from)

        elif action == "remove":
            log("Issue removed: %s (no notification sent)" % data.get("identifier", "?"))

    # --- Comment events ---
    elif event_type == "Comment":
        if action == "create":
            notify_new_comment(data)

    else:
        log("Unhandled event type: %s/%s" % (event_type, action))


def main():
    log("Starting Linear-Slack webhook server on port %d" % PORT)
    log("Endpoints:")
    log("  GET  /        - status page")
    log("  GET  /health  - health check")
    log("  POST /webhook - Linear webhook receiver")

    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
    finally:
        server.server_close()
        log("Server stopped.")


if __name__ == "__main__":
    main()
