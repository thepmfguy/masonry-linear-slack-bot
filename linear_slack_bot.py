#!/usr/bin/env python3
"""
Linear -> Slack notification bot.
Polls Linear for issue updates and posts formatted messages to Slack.
Uses only Python stdlib - no external packages required.

Modified for Render.com deployment:
- Reads config from environment variables first, falls back to .env file
- No type hints for Python 3.8+ compatibility
- State file uses current directory
"""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
STATE_PATH = Path("./state.json")

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
# Hardcoded Slack user mappings for 16px Agency team
# ---------------------------------------------------------------------------
SLACK_USER_MAP = {
    "akshay": "U088D28B9HQ",
    "asif": "U088FA91UDA",
    "gaurav": "U089CE8GUBX",
}
GAURAV_SLACK_ID = "U089CE8GUBX"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[%s] %s" % (ts, msg), flush=True)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _parse_env_file():
    """Parse .env file into a dict. Returns empty dict if file not found."""
    env = {}
    if not ENV_PATH.exists():
        return env
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def load_env():
    """Load config from environment variables first, then fall back to .env file."""
    required_keys = ["LINEAR_API_KEY", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"]
    config = {}

    # Try environment variables first
    for key in required_keys:
        val = os.environ.get(key)
        if val:
            config[key] = val

    # Fall back to .env file for any missing keys
    if len(config) < len(required_keys):
        file_env = _parse_env_file()
        for key in required_keys:
            if key not in config and key in file_env:
                config[key] = file_env[key]

    return config

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state():
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log("WARNING: Could not load state: %s. Starting fresh." % e)
    # Default state: look back 24 hours on first run
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "last_checked": since,
        "issue_states": {},
        "slack_user_cache": {},
    }


def save_state(state):
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        log("ERROR: Could not save state: %s" % e)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_request(url, data=None, headers=None, method="GET"):
    """Make an HTTP request and return parsed JSON or None on error."""
    headers = headers or {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        log("HTTP %s for %s: %s" % (e.code, url, body[:500]))
        return None
    except urllib.error.URLError as e:
        log("URLError for %s: %s" % (url, e.reason))
        return None
    except Exception as e:
        log("Request error for %s: %s" % (url, e))
        return None

# ---------------------------------------------------------------------------
# Linear API
# ---------------------------------------------------------------------------

LINEAR_API_URL = "https://api.linear.app/graphql"

ISSUES_QUERY = """
query($since: DateTimeOrDuration!) {
  issues(
    filter: { updatedAt: { gt: $since } }
    first: 100
    orderBy: updatedAt
  ) {
    nodes {
      id
      identifier
      title
      description
      url
      priority
      createdAt
      updatedAt
      assignee {
        id
        name
        email
      }
      team {
        name
      }
      state {
        name
      }
      comments {
        nodes {
          id
          body
          createdAt
          user {
            name
            email
          }
        }
      }
    }
  }
}
"""


def fetch_updated_issues(api_key, since):
    payload = json.dumps({"query": ISSUES_QUERY, "variables": {"since": since}}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": api_key,
    }
    result = http_request(LINEAR_API_URL, data=payload, headers=headers, method="POST")
    if not result:
        return []
    if "errors" in result:
        log("Linear API errors: %s" % result["errors"])
        return []
    try:
        return result["data"]["issues"]["nodes"]
    except (KeyError, TypeError) as e:
        log("Unexpected Linear response structure: %s" % e)
        return []

# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def lookup_slack_user_by_name(name):
    """Check hardcoded SLACK_USER_MAP by first name (case-insensitive)."""
    if not name:
        return None
    first_name = name.strip().split()[0].lower()
    uid = SLACK_USER_MAP.get(first_name)
    if uid:
        return "<@%s>" % uid
    return None


def lookup_slack_user_by_email(email, token, state):
    """Resolve email -> Slack <@USERID> tag via API. Uses cache."""
    cache = state.setdefault("slack_user_cache", {})
    if email in cache:
        return "<@%s>" % cache[email]

    url = "https://slack.com/api/users.lookupByEmail?email=%s" % urllib.parse.quote(email)
    headers = {"Authorization": "Bearer %s" % token}
    result = http_request(url, headers=headers)
    if result and result.get("ok") and result.get("user", {}).get("id"):
        uid = result["user"]["id"]
        cache[email] = uid
        return "<@%s>" % uid
    return None


def resolve_user(assignee, token, state):
    """Return a Slack <@USERID> mention or plain name for an assignee dict.
    Priority: hardcoded map -> state cache -> Slack API -> plain name.
    """
    if not assignee:
        return "Unassigned"
    name = assignee.get("name", "Unknown")
    email = assignee.get("email")

    # 1. Check hardcoded map by first name
    tag = lookup_slack_user_by_name(name)
    if tag:
        return tag

    # 2. Check cached lookups + Slack API by email
    if email:
        tag = lookup_slack_user_by_email(email, token, state)
        if tag:
            return tag

    return name


def resolve_user_name_only(name):
    """Resolve just a name string (no email) via hardcoded map."""
    if not name or name == "Unassigned":
        return "Unassigned"
    tag = lookup_slack_user_by_name(name)
    if tag:
        return tag
    return name


def post_to_slack(token, channel, text):
    """Post a mrkdwn-formatted text message to Slack."""
    url = "https://slack.com/api/chat.postMessage"
    payload = json.dumps({
        "channel": channel,
        "text": text,
        "mrkdwn": True,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "Bearer %s" % token,
    }
    result = http_request(url, data=payload, headers=headers, method="POST")
    if result and result.get("ok"):
        return True
    error = result.get("error", "unknown") if result else "no response"
    log("Slack post failed: %s" % error)
    return False

# ---------------------------------------------------------------------------
# Message builders (mrkdwn text, not Block Kit)
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


def build_new_ticket_msg(issue, assignee_tag):
    identifier = issue.get("identifier", "???")
    title = issue.get("title", "Untitled")
    url = issue.get("url", "")
    description = issue.get("description", "")
    priority = PRIORITY_LABELS.get(issue.get("priority", 0), "No priority")
    team = (issue.get("team") or {}).get("name", "Unknown")
    status = (issue.get("state") or {}).get("name", "Unknown")

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
    return "\n".join(lines)


def build_reassignment_msg(issue, old_tag, new_tag):
    identifier = issue.get("identifier", "???")
    title = issue.get("title", "Untitled")
    url = issue.get("url", "")
    link = "<%s|%s>" % (url, identifier) if url else identifier

    lines = [
        "\U0001f504 *%s* has been reassigned" % link,
        "",
        '"%s" was reassigned from %s to %s.' % (title, old_tag, new_tag),
        "",
        "<@%s> FYI" % GAURAV_SLACK_ID,
    ]
    return "\n".join(lines)


def build_comment_msg(issue, commenter_tag, comment_body, assignee_tag):
    identifier = issue.get("identifier", "???")
    title = issue.get("title", "Untitled")
    url = issue.get("url", "")
    link = "<%s|%s>" % (url, identifier) if url else identifier
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
    return "\n".join(lines)


def build_status_change_msg(issue, old_status, new_status, assignee_tag):
    identifier = issue.get("identifier", "???")
    title = issue.get("title", "Untitled")
    url = issue.get("url", "")
    link = "<%s|%s>" % (url, identifier) if url else identifier

    lines = [
        "\U0001f4ca *Status update on %s*" % link,
        "",
        '"%s" moved from *%s* \u2192 *%s*' % (title, old_status, new_status),
        "",
        "Assigned to %s | <@%s> FYI" % (assignee_tag, GAURAV_SLACK_ID),
    ]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def process_issues(issues, state, config):
    """Process issues, detect events, post to Slack. Returns event count."""
    token = config["SLACK_BOT_TOKEN"]
    channel = config["SLACK_CHANNEL_ID"]
    last_checked = state["last_checked"]
    stored = state["issue_states"]
    events_posted = 0

    for issue in issues:
        issue_id = issue["id"]
        identifier = issue.get("identifier", "???")
        assignee = issue.get("assignee")
        assignee_id = assignee["id"] if assignee else None
        assignee_name = assignee.get("name", "Unassigned") if assignee else "Unassigned"
        status = (issue.get("state") or {}).get("name", "Unknown")
        created_at = issue.get("createdAt", "")

        assignee_tag = resolve_user(assignee, token, state)

        # -- Detect events --

        if issue_id not in stored:
            # Possibly a new ticket
            if created_at > last_checked:
                log("NEW TICKET: %s - %s" % (identifier, issue.get("title", "")))
                msg = build_new_ticket_msg(issue, assignee_tag)
                if post_to_slack(token, channel, msg):
                    events_posted += 1
        else:
            prev = stored[issue_id]

            # Reassignment
            if assignee_id != prev.get("assignee_id"):
                old_name = prev.get("assignee_name", "Unassigned")
                old_tag = resolve_user_name_only(old_name)
                log("REASSIGNED: %s from %s to %s" % (identifier, old_name, assignee_name))
                msg = build_reassignment_msg(issue, old_tag, assignee_tag)
                if post_to_slack(token, channel, msg):
                    events_posted += 1

            # Status change
            if status != prev.get("status"):
                old_status = prev.get("status", "Unknown")
                log("STATUS CHANGE: %s %s -> %s" % (identifier, old_status, status))
                msg = build_status_change_msg(issue, old_status, status, assignee_tag)
                if post_to_slack(token, channel, msg):
                    events_posted += 1

        # Comments
        comments = (issue.get("comments") or {}).get("nodes", [])
        prev_last_comment = stored.get(issue_id, {}).get("last_comment_id")
        latest_comment_id = prev_last_comment

        for comment in comments:
            c_id = comment.get("id", "")
            c_created = comment.get("createdAt", "")
            if c_created > last_checked and c_id != prev_last_comment:
                commenter = comment.get("user")
                commenter_tag = resolve_user(commenter, token, state) if commenter else "Someone"
                body = comment.get("body", "")
                log("COMMENT: %s by %s" % (identifier, commenter.get("name", "?") if commenter else "?"))
                msg = build_comment_msg(issue, commenter_tag, body, assignee_tag)
                if post_to_slack(token, channel, msg):
                    events_posted += 1
                if not latest_comment_id or c_id > latest_comment_id:
                    latest_comment_id = c_id

        # -- Update stored state for this issue --
        stored[issue_id] = {
            "assignee_id": assignee_id,
            "assignee_name": assignee_name,
            "status": status,
            "last_comment_id": latest_comment_id,
        }

    return events_posted

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log("Bot check starting...")

    config = load_env()
    required_keys = ["LINEAR_API_KEY", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"]
    for key in required_keys:
        if key not in config:
            log("ERROR: Missing required config key: %s" % key)
            return

    state = load_state()
    since = state["last_checked"]
    log("Checking for updates since %s" % since)

    issues = fetch_updated_issues(config["LINEAR_API_KEY"], since)
    log("Found %d updated issue(s)" % len(issues))

    if issues:
        count = process_issues(issues, state, config)
        log("Posted %d event(s) to Slack" % count)
    else:
        log("No updates found")

    # Update last_checked to now
    state["last_checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    save_state(state)
    log("Bot check finished.")


if __name__ == "__main__":
    main()
