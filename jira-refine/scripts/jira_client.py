#!/usr/bin/env python3
"""Minimal Jira REST client for the jira-refine skill.

Stdlib only (no `requests` dependency) so it runs anywhere Python 3.7+ runs,
including inside opencode/other agent sandboxes with no pip install step.

Auth / target instance come from environment variables:
  JIRA_URL    e.g. https://yourcompany.atlassian.net  (no trailing slash)
  JIRA_TOKEN  API token (Cloud) or Personal Access Token (Server/Data Center)
  JIRA_EMAIL  required for Jira Cloud — the account email the token belongs
              to. Cloud authenticates with Basic auth (email:token), not
              Bearer; Bearer is a Server/Data Center-only scheme. Leave
              JIRA_EMAIL unset when targeting a Server/Data Center instance,
              and this falls back to Bearer auth with JIRA_TOKEN as a PAT.

Every subcommand prints a single JSON document to stdout and exits 0 on
success. On failure it prints {"error": "..."} to stdout and exits 1 — never
raises a raw traceback, so a calling agent can always parse the response.

Usage:
  jira_client.py get TICKET-123
  jira_client.py links TICKET-123
  jira_client.py children TICKET-123      # sub-tasks + epic children
  jira_client.py search "project = ABC AND status = Open"
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(json.dumps({"error": f"missing required env var {name}"}))
    return value


def _auth_header() -> str:
    token = _env("JIRA_TOKEN")
    email = os.environ.get("JIRA_EMAIL")
    if email:
        creds = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
        return f"Basic {creds}"
    return f"Bearer {token}"


def _request(path: str, params: dict | None = None) -> dict:
    base = _env("JIRA_URL").rstrip("/")
    url = f"{base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _auth_header(),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(json.dumps({"error": f"HTTP {e.code} calling {path}", "body": body}))
    except urllib.error.URLError as e:
        raise SystemExit(json.dumps({"error": f"network error calling {path}: {e}"}))


def _adf_to_text(node) -> str:
    """Flatten Atlassian Document Format (v3 description/comment body) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    parts = []
    node_type = node.get("type")
    if node_type == "text":
        parts.append(node.get("text", ""))
    for child in node.get("content", []) or []:
        parts.append(_adf_to_text(child))
    joined = "".join(parts) if node_type == "text" else " ".join(p for p in parts if p)
    if node_type in ("paragraph", "heading", "listItem"):
        joined += "\n"
    return joined


def _simplify_issue(raw: dict) -> dict:
    fields = raw.get("fields", {})
    description = fields.get("description")
    if isinstance(description, dict):
        description = _adf_to_text(description).strip()
    links = []
    for link in fields.get("issuelinks", []) or []:
        other = link.get("outwardIssue") or link.get("inwardIssue")
        if not other:
            continue
        relation = (link.get("type") or {}).get(
            "outward" if "outwardIssue" in link else "inward", "relates to"
        )
        links.append(
            {
                "key": other.get("key"),
                "relation": relation,
                "summary": (other.get("fields") or {}).get("summary"),
                "status": ((other.get("fields") or {}).get("status") or {}).get("name"),
            }
        )
    subtasks = [
        {"key": s.get("key"), "summary": (s.get("fields") or {}).get("summary")}
        for s in fields.get("subtasks", []) or []
    ]
    parent = fields.get("parent")
    return {
        "key": raw.get("key"),
        "summary": fields.get("summary"),
        "description": description,
        "issuetype": (fields.get("issuetype") or {}).get("name"),
        "status": (fields.get("status") or {}).get("name"),
        "priority": (fields.get("priority") or {}).get("name"),
        "labels": fields.get("labels", []),
        "components": [c.get("name") for c in fields.get("components", []) or []],
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "reporter": (fields.get("reporter") or {}).get("displayName"),
        "parent": {"key": parent.get("key"), "summary": (parent.get("fields") or {}).get("summary")}
        if parent
        else None,
        "subtasks": subtasks,
        "issuelinks": links,
    }


def cmd_get(key: str) -> dict:
    raw = _request(f"/rest/api/3/issue/{key}")
    return _simplify_issue(raw)


def cmd_links(key: str) -> dict:
    issue = cmd_get(key)
    return {"key": key, "issuelinks": issue["issuelinks"], "subtasks": issue["subtasks"], "parent": issue["parent"]}


def cmd_children(key: str) -> dict:
    # Works whether `key` is an Epic (classic "Epic Link") or a next-gen parent.
    jql = f'"Epic Link" = {key} OR parent = {key} ORDER BY created ASC'
    result = _request(
        "/rest/api/3/search/jql",
        {"jql": jql, "fields": "summary,status,issuetype", "maxResults": 100},
    )
    issues = [
        {
            "key": i.get("key"),
            "summary": (i.get("fields") or {}).get("summary"),
            "status": ((i.get("fields") or {}).get("status") or {}).get("name"),
            "issuetype": ((i.get("fields") or {}).get("issuetype") or {}).get("name"),
        }
        for i in result.get("issues", [])
    ]
    return {"key": key, "children": issues}


def cmd_search(jql: str) -> dict:
    result = _request(
        "/rest/api/3/search/jql",
        {"jql": jql, "fields": "summary,status,issuetype", "maxResults": 100},
    )
    issues = [
        {
            "key": i.get("key"),
            "summary": (i.get("fields") or {}).get("summary"),
            "status": ((i.get("fields") or {}).get("status") or {}).get("name"),
            "issuetype": ((i.get("fields") or {}).get("issuetype") or {}).get("name"),
        }
        for i in result.get("issues", [])
    ]
    return {"jql": jql, "issues": issues}


COMMANDS = {
    "get": cmd_get,
    "links": cmd_links,
    "children": cmd_children,
    "search": cmd_search,
}


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS:
        print(json.dumps({"error": f"usage: jira_client.py <{'|'.join(COMMANDS)}> <arg>"}))
        sys.exit(1)
    command, arg = sys.argv[1], sys.argv[2]
    result = COMMANDS[command](arg)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
