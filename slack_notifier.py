#!/usr/bin/env python3
"""
Slack notification module for Linear webhook events.
Posts formatted mrkdwn messages to a Slack channel.
Uses only Python stdlib.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
SLACK_POST_URL = "https://slack.com/api/chat.postMessage"

# ---------------------------------------------------------------------------
# Hardcoded Slack user mappings for Masonry workspace
# ---------------------------------------------------------------------------
SLACK_USER_MAP = {
    "gaurav": "U08E514NQHX",
    "hemesh": "U08C06C2T7Z",
    "shreyansh": "U09LBMWCMDL",
    "vasanth": "U09Q9UKRUMU",
    "junaid": "U08D3GAAPS4",
    "prateek": "U0ALY4P8C74",
    "rutvik": "U0AMJV6B88J",
}
GAURAV_SLACK_ID = "U08E514NQHX"

# ---------------------------------------------------------------------------
# Priority labels
# ---------------------------------------------------------------------------
PRIORITY_LABELS = {
    0: "No priority",
    1: "\U0001f534 Urgent",
    2: "\U0001f7e0 High",
    3: "\U0001f7e1 Medium",
    4: "\U0001f7e2 Low",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[%s] %s" % (ts, msg), flush=True)

# ---------------------------------------------------------------------------
# User resolution
# ---------------------------------------------------------------------------

def resolve_slack_user(name, email=None):
    """Match by first name against SLACK_USER_MAP. Return <@ID> or plain name."""
    if not name:
        return "Unassigned"
    first_name = name.strip().split()[0].lower()
    uid = SLACK_USER_MAP.get(first_name)
    if uid:
        return "<@%s>" % uid
    return name

# ---------------------------------------------------------------------------
# Slack posting
# ---------------------------------------------------------------------------

def post_to_slack(text):
    """Send a mrkdwn message to the configured Slack channel."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        log("ERROR: SLACK_BOT_TOKEN or SLACK_CHANNEL_ID not set")
        return False

    payload = json.dumps({
        "channel": SLACK_CHANNEL_ID,
        "text": text,
        "mrkdwn": True,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "Bearer %s" % SLACK_BOT_TOKEN,
    }

    req = urllib.request.Request(SLACK_POST_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                log("Slack message posted successfully")
                return True
            else:
                log("Slack API error: %s" % body.get("error", "unknown"))
                return False
    except urllib.error.HTTPError as e:
        log("Slack HTTP error %s: %s" % (e.code, e.read().decode("utf-8", errors="replace")[:500]))
        return False
    except Exception as e:
        log("Slack post failed: %s" % e)
        return False

# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------

def _summarize_ticket(title, description):
    """Create a brief natural language summary from title and description."""
    if description:
        desc = description[:300].strip()
        for end_char in [". ", ".\n", "! ", "?\n"]:
            idx = desc.rfind(end_char)
            if idx > 50:
                desc = desc[:idx + 1]
                break
        return desc
    return "This ticket is about %s." % title.lower().rstrip(".")

# ---------------------------------------------------------------------------
# Notification functions
# ---------------------------------------------------------------------------

def notify_new_ticket(data):
    """Format and post a new ticket notification."""
    identifier = data.get("identifier", "???")
    title = data.get("title", "Untitled")
    url = data.get("url", "")
    description = data.get("description", "")
    priority = PRIORITY_LABELS.get(data.get("priority", 0), "No priority")
    team = (data.get("team") or {}).get("name", "Unknown")
    status = (data.get("state") or {}).get("name", "Unknown")

    assignee = data.get("assignee")
    if assignee:
        assignee_tag = resolve_slack_user(assignee.get("name"), assignee.get("email"))
    else:
        assignee_tag = "Unassigned"

    summary = _summarize_ticket(title, description)
    link = "<%s|%s>" % (url, identifier) if url else identifier

    lines = [
        "\U0001f195 *New ticket created: %s*" % link,
        "",
        summary,
        "",
        "Assigned to %s | Priority: %s | Team: %s | Status: %s" % (assignee_tag, priority, team, status),
        "",
        "<@%s> FYI" % GAURAV_SLACK_ID,
    ]
    text = "\n".join(lines)
    log("NEW TICKET: %s - %s" % (identifier, title))
    return post_to_slack(text)


def notify_reassignment(data, updated_from):
    """Format and post a reassignment notification."""
    identifier = data.get("identifier", "???")
    title = data.get("title", "Untitled")
    url = data.get("url", "")
    link = "<%s|%s>" % (url, identifier) if url else identifier

    # New assignee
    new_assignee = data.get("assignee")
    if new_assignee:
        new_tag = resolve_slack_user(new_assignee.get("name"), new_assignee.get("email"))
    else:
        new_tag = "Unassigned"

    # Old assignee - updatedFrom may have assigneeId but not full name
    # Linear sends assignee info in updatedFrom as assigneeId
    old_assignee_id = updated_from.get("assigneeId", "")
    # We don't get the old assignee name directly from updatedFrom,
    # so we check if there's a name available, otherwise use "Someone"
    old_tag = "Someone"
    if old_assignee_id:
        # Try to find in our user map by checking if any known user had this ID
        # Since we don't have a reverse map, just use "the previous assignee"
        old_tag = "the previous assignee"

    lines = [
        "\U0001f504 *%s has been reassigned*" % link,
        "",
        '"%s" was reassigned from %s to %s.' % (title, old_tag, new_tag),
        "",
        "<@%s> FYI" % GAURAV_SLACK_ID,
    ]
    text = "\n".join(lines)
    log("REASSIGNED: %s to %s" % (identifier, new_tag))
    return post_to_slack(text)


def notify_status_change(data, updated_from):
    """Format and post a status change notification."""
    identifier = data.get("identifier", "???")
    title = data.get("title", "Untitled")
    url = data.get("url", "")
    link = "<%s|%s>" % (url, identifier) if url else identifier

    new_status = (data.get("state") or {}).get("name", "Unknown")
    old_status = updated_from.get("stateName", updated_from.get("stateId", "Unknown"))

    assignee = data.get("assignee")
    if assignee:
        assignee_tag = resolve_slack_user(assignee.get("name"), assignee.get("email"))
    else:
        assignee_tag = "Unassigned"

    lines = [
        "\U0001f4ca *Status update on %s*" % link,
        "",
        '"%s" moved from *%s* \u2192 *%s*' % (title, old_status, new_status),
        "",
        "Assigned to %s | <@%s> FYI" % (assignee_tag, GAURAV_SLACK_ID),
    ]
    text = "\n".join(lines)
    log("STATUS CHANGE: %s %s -> %s" % (identifier, old_status, new_status))
    return post_to_slack(text)


def notify_new_comment(data):
    """Format and post a new comment notification."""
    # Comment data structure from Linear webhook
    comment_body = data.get("body", "")
    comment_user = data.get("user")

    # The issue info is nested under data.issue in comment webhooks
    issue = data.get("issue") or {}
    identifier = issue.get("identifier", "???")
    title = issue.get("title", "Untitled")
    url = issue.get("url", "")
    link = "<%s|%s>" % (url, identifier) if url else identifier

    if comment_user:
        commenter_tag = resolve_slack_user(comment_user.get("name"), comment_user.get("email"))
    else:
        commenter_tag = "Someone"

    # Assignee from the issue
    issue_assignee = issue.get("assignee")
    if issue_assignee:
        assignee_tag = resolve_slack_user(issue_assignee.get("name"), issue_assignee.get("email"))
    else:
        assignee_tag = "Unassigned"

    body_trimmed = comment_body[:500]
    quoted = "\n".join("> " + line for line in body_trimmed.split("\n"))

    lines = [
        "\U0001f4ac *New comment on %s*" % link,
        "",
        '%s commented on "%s":' % (commenter_tag, title),
        quoted,
        "",
        "%s <@%s>" % (assignee_tag, GAURAV_SLACK_ID),
    ]
    text = "\n".join(lines)
    commenter_name = comment_user.get("name", "?") if comment_user else "?"
    log("COMMENT: %s by %s" % (identifier, commenter_name))
    return post_to_slack(text)
