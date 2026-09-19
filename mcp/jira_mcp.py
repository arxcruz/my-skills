#!/usr/bin/env -S uv run -s
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "atlassian-python-api",
#     "mcp>=1.28.0,<2.0.0",
#     "python-dotenv",
#     "truststore"
# ]
# ///

"""
Standalone Jira MCP server — exposes only the Jira tools from the bragai MCP suite.

Environment variables:
  JIRA_URL            - Jira instance base URL (required)
  JIRA_USERNAME       - Username or email (or JIRA_USER)
  JIRA_PASSWORD       - Password or API token (or JIRA_TOKEN)
  JIRA_BEARER_TOKEN   - Bearer / OAuth token (used when JIRA_AUTH=bearer or no username)
  JIRA_AUTH           - "basic" (default) or "bearer"
  JIRA_CLOUD          - Set to "false"/"0"/"no" to use Server/DC mode (default: Cloud)
  JIRA_SMOKE_JQL      - Override the startup smoke-test JQL

Load from file with --env /path/to/file or --env-file /path/to/file.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from atlassian import Jira
from mcp.server.fastmcp import FastMCP

try:
    from dotenv import load_dotenv
    HAS_DOTENV = True
except ImportError:
    load_dotenv = None
    HAS_DOTENV = False


def _load_env_from_cli_and_defaults() -> None:
    if not HAS_DOTENV:
        return
    argv = sys.argv
    env_path: Optional[str] = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--env", "--env-file"):
            if i + 1 < len(argv):
                env_path = argv[i + 1]
                del argv[i: i + 2]
                break
            logging.getLogger("jira_mcp").warning(f"{arg} requires a path; ignoring")
            del argv[i]
            break
        else:
            i += 1
    if env_path:
        path = Path(env_path)
        if path.is_file():
            load_dotenv(path, override=False)
            logging.getLogger("jira_mcp").info(f"Loaded env from {path}")
        else:
            logging.getLogger("jira_mcp").warning(f"Env file not found: {path}")
        return
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("jira_mcp")


@dataclass
class Deps:
    jira: Optional[Jira] = None
    is_jira_cloud: bool = True


app = FastMCP("jira-mcp")
deps = Deps()


def _require(service: Any, name: str) -> Any:
    if service is None:
        raise RuntimeError(f"{name} is not configured")
    return service


def _wrap(func_name: str, result: Any, context: dict | None = None) -> str:
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            return str(result)
    return str(result)


def _find_story_points_field(jira: Jira) -> Optional[str]:
    try:
        fields = jira.get_all_fields()
        ranked_patterns = ["story points", "storypoints", "story point estimate", "story point", "storypoint"]
        best_rank = len(ranked_patterns)
        best_field_id: Optional[str] = None
        for field in fields:
            field_name = (field.get("name") or "").lower().strip()
            field_id = field.get("id", "")
            for rank, pattern in enumerate(ranked_patterns):
                if field_name == pattern and rank < best_rank:
                    best_rank = rank
                    best_field_id = field_id
                    break
            if best_rank == 0:
                break
        if best_field_id:
            log.info(f"Found story points field: {best_field_id} (rank {best_rank})")
            return best_field_id
        log.warning("Story points field not found via name matching")
        return None
    except Exception as e:
        log.warning(f"Failed to discover story points field: {e}")
        return None


def _find_epic_name_field(jira: Jira) -> Optional[str]:
    try:
        fields = jira.get_all_fields()
        epic_name_patterns = ["epic name", "epicname", "epic title", "epictitle", "epic summary", "epicsummary"]
        for field in fields:
            field_name = (field.get("name", "")).lower()
            field_id = field.get("id", "")
            for pattern in epic_name_patterns:
                if pattern in field_name:
                    log.info(f"Found epic name field: {field_name} ({field_id})")
                    return field_id
        log.warning("Epic name field not found via name matching")
        return None
    except Exception as e:
        log.warning(f"Failed to discover epic name field: {e}")
        return None


def _find_assigned_team_field(jira: Jira) -> Optional[str]:
    try:
        fields = jira.get_all_fields()
        assigned_team_patterns = ["assigned team", "assignedteam", "team", "team name", "teamname"]
        for field in fields:
            field_name = (field.get("name", "")).lower()
            field_id = field.get("id", "")
            for pattern in assigned_team_patterns:
                if pattern in field_name:
                    log.info(f"Found assigned team field: {field_name} ({field_id})")
                    return field_id
        log.warning("Assigned team field not found via name matching")
        return None
    except Exception as e:
        log.warning(f"Failed to discover assigned team field: {e}")
        return None


def _integration_health(configured: bool, ok: bool, *, detail: dict | None = None, error: str | None = None) -> dict:
    entry: dict = {"configured": configured, "ok": ok}
    if detail:
        entry["detail"] = detail
    if error:
        entry["error"] = error
    return entry


def _probe_jira_health() -> dict:
    if deps.jira is None:
        return _integration_health(False, False, error="not configured")
    jira = deps.jira
    sess = getattr(jira, "_session", None)
    auth_mode = "unknown"
    if sess is not None:
        if getattr(sess, "auth", None):
            auth_mode = "basic"
        elif str(sess.headers.get("Authorization", "")).startswith("Bearer"):
            auth_mode = "bearer"
    account_id = None
    account_error = None
    try:
        if hasattr(jira, "myself"):
            me = jira.myself()
            account_id = (me or {}).get("accountId")
    except Exception as e:
        account_error = str(e)
    smoke_jql = os.getenv("JIRA_SMOKE_JQL", "project is not EMPTY order by updated desc")
    smoke_count = 0
    smoke_error = None
    try:
        raw = _jira_jql(jira, smoke_jql, fields=["summary"], limit=5)
        smoke_count = len(raw.get("issues") or [])
    except Exception as e:
        smoke_error = str(e)
    ok = smoke_error is None and account_error is None
    detail = {
        "url": getattr(jira, "url", None),
        "cloud": getattr(jira, "cloud", None),
        "is_jira_cloud": deps.is_jira_cloud,
        "auth_mode": auth_mode,
        "account_id": account_id,
        "smoke_jql": smoke_jql,
        "smoke_issue_count": smoke_count,
    }
    if account_error:
        detail["account_error"] = account_error
    error = smoke_error or account_error
    return _integration_health(True, ok, detail=detail, error=error)


def _jira_connection_health_payload() -> dict:
    probe = _probe_jira_health()
    if not probe.get("configured"):
        return {"configured": False, "ok": False, "error": probe.get("error") or "not configured", "smoke_error": probe.get("error") or "not configured"}
    detail = dict(probe.get("detail") or {})
    err = probe.get("error")
    detail["smoke_error"] = err
    detail["ok"] = probe.get("ok", False)
    return detail


def _extract_user(raw) -> dict | None:
    user = (raw or [None])[0] if isinstance(raw, list) else raw
    if isinstance(user, dict) and (user.get("accountId") or user.get("key")):
        return {
            "accountId": user.get("accountId") or user.get("key"),
            "name": user.get("name"),
            "displayName": user.get("displayName"),
            "emailAddress": user.get("emailAddress"),
            "active": user.get("active"),
            "timeZone": user.get("timeZone"),
        }
    return None


def _resolve_account_id(jira: Jira, search_str: str) -> str | None:
    for lookup in [
        lambda: jira.user_find_by_user_string(query=search_str),
        lambda: jira.user_find_by_user_string(username=search_str),
    ]:
        try:
            raw = lookup()
            users = raw if isinstance(raw, list) else ([raw] if raw else [])
            for u in users:
                if isinstance(u, dict) and u.get("accountId"):
                    return u["accountId"]
        except Exception:
            continue
    return None


def _create_issue_link(jira: Jira, link_type: str, inward_issue_key: str, outward_issue_key: str, link_comment: Optional[str], dry_run: bool) -> dict:
    payload = {"type": {"name": link_type}, "inwardIssue": {"key": inward_issue_key}, "outwardIssue": {"key": outward_issue_key}}
    if link_comment:
        payload["comment"] = {"body": link_comment}
    if dry_run:
        return {"dry_run": True, "action": "jira_add_issue_link", "payload": payload}
    try:
        types = jira.get_issue_link_types()
        available = set()
        if isinstance(types, dict) and "issueLinkTypes" in types:
            available = {t.get("name") for t in types.get("issueLinkTypes", []) if t.get("name")}
        elif isinstance(types, list):
            available = {t.get("name") for t in types if isinstance(t, dict) and t.get("name")}
        if available and link_type not in available:
            log.warning("Link type '%s' not in available types: %s", link_type, sorted(available))
    except Exception:
        pass
    resp = jira.create_issue_link(payload)
    return {"ok": True, "payload": payload, "response": resp}


def _jira_jql(jira: Jira, jql: str, fields: Optional[list[str] | str] = None, limit: Optional[int] = None) -> dict:
    kwargs: dict[str, Any] = {}
    if limit is not None:
        kwargs["limit"] = limit
    if getattr(jira, "cloud", False) and hasattr(jira, "enhanced_jql"):
        raw = jira.enhanced_jql(jql=jql, fields=fields or "*all", **kwargs)
    else:
        raw = jira.jql(jql, fields=fields or "*all", **kwargs)
    if not isinstance(raw, dict):
        return {"issues": [], "total": 0}
    issues = raw.get("issues") or []
    if raw.get("total") is None:
        raw = {**raw, "total": len(issues)}
    return raw


def _init_jira_from_env() -> tuple[Optional[Jira], bool]:
    url = os.getenv("JIRA_URL")
    bearer_token = os.getenv("JIRA_BEARER_TOKEN")
    username = os.getenv("JIRA_USERNAME") or os.getenv("JIRA_USER")
    password = os.getenv("JIRA_PASSWORD") or os.getenv("JIRA_TOKEN")
    auth_mode = (os.getenv("JIRA_AUTH") or "").strip().lower()
    cloud_env = os.getenv("JIRA_CLOUD", "").lower()
    is_cloud = cloud_env not in ("0", "false", "no")
    if url and "atlassian.net" in url.lower() and cloud_env == "":
        is_cloud = True
    if not url:
        log.info("Jira not configured (set JIRA_URL and credentials)")
        return None, is_cloud
    use_bearer = auth_mode == "bearer" or (bearer_token and not username and auth_mode != "basic")
    if username and password and not use_bearer:
        log.info("Jira configured with basic auth (cloud=%s)", is_cloud)
        try:
            client = Jira(url=url, username=username, password=password, cloud=is_cloud)
            _verify_jira_client(client, is_cloud)
            return client, is_cloud
        except Exception as e:
            log.error("Failed to connect to Jira with basic auth: %s", e)
            return None, is_cloud
    if bearer_token and use_bearer:
        log.info("Jira configured with Bearer token (cloud=%s)", is_cloud)
        try:
            client = Jira(url=url, token=bearer_token, cloud=is_cloud)
            _verify_jira_client(client, is_cloud)
            return client, is_cloud
        except Exception as e:
            log.error("Failed to connect to Jira with Bearer token: %s", e)
            return None, is_cloud
    if password and not username:
        log.info("Jira not configured (JIRA_USERNAME required with JIRA_TOKEN for Cloud)")
        return None, is_cloud
    log.info("Jira not configured (set JIRA_URL and credentials)")
    return None, is_cloud


def _verify_jira_client(jira: Jira, is_cloud: bool) -> None:
    try:
        if is_cloud and hasattr(jira, "myself"):
            me = jira.myself()
            log.info("Jira auth OK (accountId=%s)", (me or {}).get("accountId", "unknown"))
        else:
            _jira_jql(jira, "project is not EMPTY order by updated desc", fields=["summary"], limit=1)
            log.info("Jira auth OK (smoke JQL)")
    except Exception as e:
        log.warning("Jira auth verification failed: %s", e)


# ---------- Jira tools ----------

@app.tool()
def jira_get_transitions(issue_key: str) -> str:
    """List available workflow transitions for a Jira issue.

    Args:
        issue_key (str): Issue key like "ABC-123".

    Returns:
        str: Transition options from Jira (e.g., a list of objects with "id" and "name").

    Note:
        Call jira_transition_issue only after using this to select a valid transition_id.
    """
    jira = _require(deps.jira, "Jira")
    res = jira.get_issue_transitions(issue_key)
    return _wrap("jira_get_transitions", res, {"issue_key": issue_key})


@app.tool()
def jira_get_issues(
    issue_keys: list[str],
    fields: Optional[list[str] | str] = None,
    expand: Optional[list[str] | str] = None,
    all_fields: bool = False,
) -> str:
    """Get multiple Jira issues with controllable field selection.

    Args:
        issue_keys (list[str]): List of issue keys like ["ABC-123", "ABC-456"].
        fields (Optional[list[str] | str]): Specific fields to request, or a comma-separated string.
        expand (Optional[list[str] | str]): Jira expand options (e.g., ["changelog", "renderedFields"]).
        all_fields (bool): When True, fetch "*all" fields for each issue.

    Returns:
        str: Minified JSON string with {"issues": [...], "count": N}.
    """
    jira = _require(deps.jira, "Jira")
    if all_fields:
        fields_arg = "*all"
    else:
        if fields is None:
            basic = ["summary", "description", "status", "priority", "issuetype", "assignee", "reporter", "duedate", "updated", "project", "labels"]
            assigned_team_field_id = _find_assigned_team_field(jira)
            if assigned_team_field_id:
                basic.append(assigned_team_field_id)
            fields_arg = ",".join(basic)
        elif isinstance(fields, list):
            fields_arg = ",".join(fields)
        else:
            fields_arg = fields
    if expand is None:
        expand_arg = None
    elif isinstance(expand, list):
        expand_arg = ",".join(expand)
    else:
        expand_arg = expand
    results: list[dict] = []
    for k in issue_keys or []:
        try:
            issue = jira.get_issue(k, fields=fields_arg, expand=expand_arg)
            results.append(issue)
        except Exception as e:
            results.append({"key": k, "error": str(e)})
    res = {"issues": results, "count": len(results)}
    return _wrap("jira_get_issues", res)


def _clip(text: Any, limit: int) -> str:
    t = text if isinstance(text, str) else ("" if text is None else str(text))
    return t if len(t) <= limit else t[:limit] + f"...[+{len(t) - limit} chars]"


def _brief_issue(issue: dict, sp_field: Optional[str], team_field: Optional[str], base_url: str,
                 max_comments: int, max_comment_chars: int, max_description_chars: int) -> dict:
    """Reduce a raw Jira issue (REST v2 payload) to what an LLM needs to plan work. Pure function."""
    f = issue.get("fields") or {}

    def nm(o: Any) -> Optional[str]:
        return (o or {}).get("name") if isinstance(o, dict) else None

    def ref(o: dict) -> dict:
        f2 = (o or {}).get("fields") or {}
        return {"key": (o or {}).get("key"), "type": nm(f2.get("issuetype")), "status": nm(f2.get("status")), "summary": f2.get("summary")}

    links = []
    for lk in f.get("issuelinks") or []:
        t = lk.get("type") or {}
        if lk.get("outwardIssue"):
            links.append({"rel": t.get("outward"), **ref(lk["outwardIssue"])})
        elif lk.get("inwardIssue"):
            links.append({"rel": t.get("inward"), **ref(lk["inwardIssue"])})

    all_comments = ((f.get("comment") or {}).get("comments")) or []
    shown = all_comments[-max_comments:] if max_comments > 0 else []
    comments = []
    for c in shown:
        a = c.get("author") or {}
        body = re.sub(r"\[~accountid:[^\]]+\]", "@user", c.get("body") or "")
        item = {"author": a.get("displayName"), "date": (c.get("created") or "")[:10], "body": _clip(body, max_comment_chars)}
        if a.get("accountType") == "app":
            item["bot"] = True
        comments.append(item)

    team = f.get(team_field) if team_field else None
    out = {
        "key": issue.get("key"),
        "link": f"{base_url}/browse/{issue.get('key')}" if base_url and issue.get("key") else None,
        "summary": f.get("summary"),
        "type": nm(f.get("issuetype")),
        "status": nm(f.get("status")),
        "resolution": nm(f.get("resolution")),
        "priority": nm(f.get("priority")),
        "labels": f.get("labels") or [],
        "components": [nm(c) for c in (f.get("components") or [])],
        "fix_versions": [nm(v) for v in (f.get("fixVersions") or [])],
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "reporter": (f.get("reporter") or {}).get("displayName"),
        "created": (f.get("created") or "")[:10],
        "resolved": (f.get("resolutiondate") or "")[:10] or None,
        "story_points": f.get(sp_field) if sp_field else None,
        "assigned_team": nm(team) if isinstance(team, dict) else team,
        "parent": ref(f["parent"]) if f.get("parent") else None,
        "subtasks": [ref(x) for x in (f.get("subtasks") or [])],
        "links": links,
        "description": _clip(f.get("description"), max_description_chars),
        "comments": comments,
        "comments_total": (f.get("comment") or {}).get("total", len(all_comments)),
    }
    if out["comments_total"] > len(comments):
        out["comments_omitted_older"] = out["comments_total"] - len(comments)
    return out


@app.tool()
def jira_get_issue_brief(issue_keys: list[str], max_comments: int = 20, max_comment_chars: int = 1500, max_description_chars: int = 8000) -> str:
    """Read-only, compact briefing of one or more Jira issues, sized for an LLM context.

    Prefer this over jira_get_issues(all_fields=True), which returns ~75 KB (150+ fields) per issue.

    Args:
        issue_keys (list[str]): Issue keys like ["ABC-123"].
        max_comments (int): Newest N comments to include (older ones are counted, not sent).
        max_comment_chars (int): Per-comment text cap.
        max_description_chars (int): Description text cap.

    Returns:
        str: Minified JSON {"issues": [...], "count": N}. Each issue has key, link, summary, type, status,
             resolution, priority, labels, components, fix_versions, assignee, reporter, created, resolved,
             story_points, assigned_team, parent, subtasks, links (with relation + status), description,
             comments (author, date, body; bot=true for app accounts), comments_total.
    """
    jira = _require(deps.jira, "Jira")
    sp_field = _find_story_points_field(jira)
    team_field = _find_assigned_team_field(jira)
    fields = ["summary", "description", "status", "resolution", "issuetype", "priority", "labels", "components",
              "fixVersions", "parent", "subtasks", "issuelinks", "comment", "assignee", "reporter", "created", "resolutiondate"]
    fields += [x for x in (sp_field, team_field) if x]
    base_url = getattr(jira, "url", "").rstrip("/")
    results: list[dict] = []
    for k in issue_keys or []:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+$", k or ""):
            results.append({"key": k, "error": "Invalid issue key format"})
            continue
        try:
            raw = jira.get_issue(k, fields=",".join(fields))
            results.append(_brief_issue(raw, sp_field, team_field, base_url, max_comments, max_comment_chars, max_description_chars))
        except Exception as e:
            results.append({"key": k, "error": str(e)})
    return _wrap("jira_get_issue_brief", {"issues": results, "count": len(results)})


@app.tool()
def jira_search(jql: str) -> str:
    """Search Jira using JQL and return a compact list of issues.

    Args:
        jql (str): Jira Query Language string, e.g., "assignee = currentUser() AND status != Closed".

    Returns:
        str: Mapping shaped like {total, issues: [...]}, where each issue has key, id, summary,
             status, priority, project, issuetype, assignee, duedate, updated, assigned_team, link.

    Note:
        Only essential fields are requested from Jira to reduce payload size.
    """
    jira = _require(deps.jira, "Jira")
    wanted_fields = ["summary", "status", "priority", "assignee", "reporter", "project", "issuetype", "duedate", "updated", "labels"]
    assigned_team_field_id = _find_assigned_team_field(jira)
    if assigned_team_field_id:
        wanted_fields.append(assigned_team_field_id)
    raw = _jira_jql(jira, jql, fields=wanted_fields)
    base_url = getattr(jira, "url", "").rstrip("/")

    def _name(obj: dict | None, key: str = "name") -> Optional[str]:
        return (obj or {}).get(key)

    compact_issues = []
    for it in (raw.get("issues", []) or []):
        f = (it.get("fields") or {})
        issue_data = {
            "key": it.get("key"),
            "id": it.get("id"),
            "summary": f.get("summary"),
            "status": _name(f.get("status")),
            "priority": _name(f.get("priority")),
            "project": (f.get("project") or {}).get("key"),
            "issuetype": _name(f.get("issuetype")),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "duedate": f.get("duedate"),
            "updated": f.get("updated"),
            "labels": f.get("labels", []),
            "link": f"{base_url}/browse/{it.get('key')}" if base_url and it.get("key") else None,
        }
        if assigned_team_field_id and assigned_team_field_id in f:
            assigned_team_value = f.get(assigned_team_field_id)
            if isinstance(assigned_team_value, dict):
                issue_data["assigned_team"] = _name(assigned_team_value)
            else:
                issue_data["assigned_team"] = assigned_team_value
        compact_issues.append(issue_data)
    total = raw.get("total")
    if total is None:
        total = len(compact_issues)
    res = {"total": total, "issues": compact_issues}
    return _wrap("jira_search", res, {"jql": jql})


@app.tool()
def jira_add_comment(
    issue_key: str,
    comment: str,
    dry_run: bool = True,
    visibility_type: str | None = None,
    visibility_value: str | None = None,
    is_internal: bool = False,
) -> str:
    """Add a comment to a Jira issue.

    Args:
        issue_key (str): Target issue key (e.g., "ABC-123").
        comment (str): Comment content formatted in Jira Wiki Markup.
        dry_run (bool): When True, do not modify; return a preview payload.
        visibility_type (str | None): "group" or "role".
        visibility_value (str | None): The group or role name.
        is_internal (bool): When True, post as a JSM internal note.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    visibility = None
    if visibility_type and visibility_value:
        visibility = {"type": visibility_type, "value": visibility_value}
    if dry_run:
        res = {"dry_run": True, "action": "jira_add_comment", "issue_key": issue_key, "comment_preview": comment[:200], "visibility": visibility, "is_internal": is_internal}
        return _wrap("jira_add_comment", res, {"issue_key": issue_key})
    if is_internal:
        url = f"rest/api/2/issue/{issue_key}/comment"
        body: dict = {"body": comment, "properties": [{"key": "sd.public.comment", "value": {"internal": True}}]}
        if visibility:
            body["visibility"] = visibility
        res = jira.post(url, data=body)
    else:
        res = jira.issue_add_comment(issue_key, comment, visibility=visibility)
    return _wrap("jira_add_comment", res, {"issue_key": issue_key})


@app.tool()
def jira_edit_comment(issue_key: str, comment_id: str, comment: str, dry_run: bool = True) -> str:
    """Edit an existing comment on a Jira issue.

    Args:
        issue_key (str): Target issue key (e.g., "ABC-123").
        comment_id (str): The ID of the comment to edit (numeric).
        comment (str): New comment content formatted in Jira Wiki Markup.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if not re.match(r"^[A-Z][A-Z0-9_]+-\d+$", issue_key, re.IGNORECASE):
        return _wrap("jira_edit_comment", {"error": f"Invalid issue_key format: {issue_key}"})
    if not re.match(r"^\d+$", comment_id):
        return _wrap("jira_edit_comment", {"error": f"Invalid comment_id format (must be numeric): {comment_id}"})
    if dry_run:
        res = {"dry_run": True, "action": "jira_edit_comment", "issue_key": issue_key, "comment_id": comment_id, "comment_preview": comment[:200]}
        return _wrap("jira_edit_comment", res, {"issue_key": issue_key, "comment_id": comment_id})
    url = f"rest/api/2/issue/{issue_key}/comment/{comment_id}"
    res = jira.put(url, data={"body": comment})
    return _wrap("jira_edit_comment", res, {"issue_key": issue_key, "comment_id": comment_id})


@app.tool()
def jira_delete_comment(issue_key: str, comment_id: str, dry_run: bool = True) -> str:
    """Delete a comment from a Jira issue.

    Args:
        issue_key (str): Target issue key (e.g., "ABC-123").
        comment_id (str): The ID of the comment to delete (numeric).
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if not re.match(r"^[A-Z][A-Z0-9_]+-\d+$", issue_key, re.IGNORECASE):
        return _wrap("jira_delete_comment", {"error": f"Invalid issue_key format: {issue_key}"})
    if not re.match(r"^\d+$", comment_id):
        return _wrap("jira_delete_comment", {"error": f"Invalid comment_id format (must be numeric): {comment_id}"})
    if dry_run:
        res = {"dry_run": True, "action": "jira_delete_comment", "issue_key": issue_key, "comment_id": comment_id}
        return _wrap("jira_delete_comment", res, {"issue_key": issue_key, "comment_id": comment_id})
    url = f"rest/api/2/issue/{issue_key}/comment/{comment_id}"
    res = jira.delete(url)
    return _wrap("jira_delete_comment", res, {"issue_key": issue_key, "comment_id": comment_id})


@app.tool()
def jira_get_resolutions() -> str:
    """List available resolutions in the Jira instance.

    Returns:
        str: List of resolution objects with id and name.
    """
    jira = _require(deps.jira, "Jira")
    res = jira.get_all_resolutions()
    return _wrap("jira_get_resolutions", res, {})


@app.tool()
def jira_transition_issue(
    issue_key: str,
    transition_id: int,
    resolution: Optional[str | dict] = None,
    dry_run: bool = True,
) -> str:
    """Transition a Jira issue to a new workflow status.

    Args:
        issue_key (str): Target issue key (e.g., "ABC-123").
        transition_id (int): Transition identifier from jira_get_transitions.
        resolution (str | dict | None): Optional resolution name or dict {"name"|"id"}.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    resolution_dict = None
    if resolution is not None:
        if isinstance(resolution, str):
            resolution_dict = {"name": resolution}
        elif isinstance(resolution, dict):
            resolution_dict = resolution
    if dry_run:
        res = {"dry_run": True, "action": "jira_transition_issue", "issue_key": issue_key, "transition_id": transition_id, "resolution": resolution_dict}
        return _wrap("jira_transition_issue", res, {"issue_key": issue_key})
    url = f"rest/api/2/issue/{issue_key}/transitions"
    data: dict = {"transition": {"id": str(transition_id)}}
    if resolution_dict:
        data["fields"] = {"resolution": resolution_dict}
    res = jira.post(url, data=data)
    return _wrap("jira_transition_issue", res, {"issue_key": issue_key})


@app.tool()
def jira_get_user(username: str) -> str:
    """Look up a Jira user and return compact info.

    Args:
        username (str): Username, email, display name, or account ID to search.

    Returns:
        str: Mapping with accountId, name, displayName, emailAddress, active, timeZone.
    """
    jira = _require(deps.jira, "Jira")
    try:
        raw = jira.user_find_by_user_string(query=username)
        result = _extract_user(raw)
        if result:
            return _wrap("jira_get_user", result, {"username": username})
    except Exception:
        pass
    try:
        raw = jira.user_find_by_user_string(username=username)
        result = _extract_user(raw)
        if result:
            return _wrap("jira_get_user", result, {"username": username})
    except Exception:
        pass
    return _wrap("jira_get_user", {"error": f"User '{username}' not found"}, {"username": username})


@app.tool()
def jira_assign_issue(issue_key: str, username: str, dry_run: bool = True) -> str:
    """Assign a Jira issue to a user.

    Args:
        issue_key (str): Target issue key.
        username (str): Username, email, display name, or accountId to resolve.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    account_id = _resolve_account_id(jira, username)
    if dry_run:
        res = {"dry_run": True, "action": "jira_assign_issue", "issue_key": issue_key, "assignee": username, "resolved_account_id": account_id}
        return _wrap("jira_assign_issue", res, {"issue_key": issue_key})
    if not account_id:
        return _wrap("jira_assign_issue", {"error": f"Could not resolve '{username}' to a Jira accountId", "suggestion": "Try the user's email address or display name"}, {"issue_key": issue_key})
    res = jira.assign_issue(issue_key, account_id)
    return _wrap("jira_assign_issue", res, {"issue_key": issue_key})


@app.tool()
def jira_set_priority(issue_key: str, priority: str | dict, dry_run: bool = True) -> str:
    """Set the priority of a Jira issue.

    Args:
        issue_key (str): Target issue key.
        priority (str | dict): Priority name (e.g., "High") or a dict with name and/or id.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    fields = {"priority": priority if isinstance(priority, dict) else {"name": str(priority)}}
    if dry_run:
        res = {"dry_run": True, "action": "jira_set_priority", "issue_key": issue_key, "fields": fields}
        return _wrap("jira_set_priority", res, {"issue_key": issue_key})
    res = jira.update_issue_field(issue_key, fields)
    return _wrap("jira_set_priority", res, {"issue_key": issue_key})


@app.tool()
def jira_set_text_field(
    issue_key: str,
    field: str,
    value: str | int | float | list | dict,
    dry_run: bool = True,
) -> str:
    """Update a field on a Jira issue.

    Args:
        issue_key (str): Target issue key.
        field (str): Field name or custom field ID (e.g., "summary", "customfield_12324746").
        value: New value to set (str, int, float, list, or dict depending on field type).
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    fields = {field: value}
    if dry_run:
        res = {"dry_run": True, "action": "jira_set_text_field", "issue_key": issue_key, "fields": fields}
        return _wrap("jira_set_text_field", res, {"issue_key": issue_key})
    res = jira.update_issue_field(issue_key, fields)
    return _wrap("jira_set_text_field", res, {"issue_key": issue_key})


@app.tool()
def jira_connection_health() -> str:
    """Report Jira connection/auth status and optionally smoke-test JQL access.

    Returns:
        str: JSON with url, cloud, auth_mode, account_id, smoke_issue_count, and smoke_error.
    """
    return _wrap("jira_connection_health", _jira_connection_health_payload())


@app.tool()
def jira_debug_fields() -> str:
    """Debug function to see what fields are available in Jira instance.

    Returns:
        str: Information about available fields for debugging.
    """
    jira = _require(deps.jira, "Jira")
    try:
        fields = jira.get_all_fields()
        total_fields = len(fields) if fields else 0
        custom_fields = []
        story_point_candidates = []
        assigned_team_candidates = []
        for field in (fields or []):
            field_id = field.get("id", "")
            field_name = field.get("name", "")
            field_type = field.get("type", "")
            if field_id.startswith("customfield_"):
                custom_fields.append({"id": field_id, "name": field_name, "type": field_type})
                if any(p in field_name.lower() for p in ["story", "point", "sp"]):
                    story_point_candidates.append({"id": field_id, "name": field_name, "type": field_type})
                if any(p in field_name.lower() for p in ["assigned team", "assignedteam", "team"]):
                    assigned_team_candidates.append({"id": field_id, "name": field_name, "type": field_type})
        result = {"total_fields": total_fields, "custom_fields_count": len(custom_fields), "story_point_candidates": story_point_candidates, "assigned_team_candidates": assigned_team_candidates, "first_5_custom_fields": custom_fields[:5] if custom_fields else []}
        return _wrap("jira_debug_fields", result)
    except Exception as e:
        return _wrap("jira_debug_fields", {"error": str(e)})


@app.tool()
def jira_set_story_points(issue_key: str, story_points: int, dry_run: bool = True) -> str:
    """Set story points on a Jira issue.

    Args:
        issue_key (str): Target issue key.
        story_points (int): Story points value (numeric).
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    sp_float = float(story_points)
    if dry_run:
        res = {"dry_run": True, "action": "jira_set_story_points", "issue_key": issue_key, "story_points": sp_float, "note": "Will dynamically discover story points field ID and set as float"}
        return _wrap("jira_set_story_points", res, {"issue_key": issue_key})
    candidate_ids: list[str] = []
    discovered_id = _find_story_points_field(jira)
    if discovered_id:
        candidate_ids.append(discovered_id)
    for known in ("customfield_10028", "customfield_10016"):
        if known not in candidate_ids:
            candidate_ids.append(known)
    errors: list[str] = []
    for field_id in candidate_ids:
        for value in (sp_float, story_points):
            try:
                res = jira.update_issue_field(issue_key, {field_id: value})
                return _wrap("jira_set_story_points", {"success": True, "field_id_used": field_id, "value_type": type(value).__name__, "story_points": value, "response": res}, {"issue_key": issue_key})
            except Exception as e:
                errors.append(f"{field_id}({type(value).__name__}): {e}")
                continue
    res = {"error": "Failed to set story points on all candidate fields", "candidates_tried": candidate_ids, "errors": errors}
    return _wrap("jira_set_story_points", res, {"issue_key": issue_key})


@app.tool()
def jira_set_labels(issue_key: str, labels: list[str], dry_run: bool = True) -> str:
    """Set/update labels on a Jira issue (replaces all existing labels).

    Args:
        issue_key (str): Target issue key.
        labels (list[str]): List of labels to set.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    label_list = [str(label) for label in labels if label is not None and str(label).strip()]
    fields = {"labels": label_list}
    if dry_run:
        res = {"dry_run": True, "action": "jira_set_labels", "issue_key": issue_key, "labels": label_list, "fields": fields}
        return _wrap("jira_set_labels", res, {"issue_key": issue_key})
    res = jira.update_issue_field(issue_key, fields)
    return _wrap("jira_set_labels", res, {"issue_key": issue_key})


@app.tool()
def jira_add_labels(issue_key: str, labels: list[str], dry_run: bool = True) -> str:
    """Add labels to a Jira issue (preserves existing labels).

    Args:
        issue_key (str): Target issue key.
        labels (list[str]): List of labels to add.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if dry_run:
        res = {"dry_run": True, "action": "jira_add_labels", "issue_key": issue_key, "labels_to_add": labels}
        return _wrap("jira_add_labels", res, {"issue_key": issue_key})
    try:
        issue = jira.get_issue(issue_key, fields="labels")
        current_labels = issue.get("fields", {}).get("labels", [])
        existing_label_set = set(current_labels)
        new_labels_to_add = [str(label) for label in labels if label is not None and str(label).strip()]
        final_labels = list(existing_label_set) + [label for label in new_labels_to_add if label not in existing_label_set]
        res = jira.update_issue_field(issue_key, {"labels": final_labels})
        return _wrap("jira_add_labels", {"success": True, "previous_labels": current_labels, "added_labels": new_labels_to_add, "final_labels": final_labels, "response": res}, {"issue_key": issue_key})
    except Exception as e:
        res = {"error": f"Failed to add labels to issue {issue_key}: {str(e)}", "issue_key": issue_key, "labels_to_add": labels}
        return _wrap("jira_add_labels", res, {"issue_key": issue_key})


@app.tool()
def jira_remove_labels(issue_key: str, labels: list[str], dry_run: bool = True) -> str:
    """Remove specific labels from a Jira issue.

    Args:
        issue_key (str): Target issue key.
        labels (list[str]): List of labels to remove.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if dry_run:
        res = {"dry_run": True, "action": "jira_remove_labels", "issue_key": issue_key, "labels_to_remove": labels}
        return _wrap("jira_remove_labels", res, {"issue_key": issue_key})
    try:
        issue = jira.get_issue(issue_key, fields="labels")
        current_labels = issue.get("fields", {}).get("labels", [])
        labels_to_remove_set = set(str(label) for label in labels if label is not None and str(label).strip())
        final_labels = [label for label in current_labels if label not in labels_to_remove_set]
        res = jira.update_issue_field(issue_key, {"labels": final_labels})
        return _wrap("jira_remove_labels", {"success": True, "previous_labels": current_labels, "removed_labels": list(labels_to_remove_set), "final_labels": final_labels, "response": res}, {"issue_key": issue_key})
    except Exception as e:
        res = {"error": f"Failed to remove labels from issue {issue_key}: {str(e)}", "issue_key": issue_key, "labels_to_remove": labels}
        return _wrap("jira_remove_labels", res, {"issue_key": issue_key})


@app.tool()
def jira_set_assigned_team(issue_key: str, assigned_team: Union[str, dict], dry_run: bool = True) -> str:
    """Set the assigned team for a Jira issue.

    Args:
        issue_key (str): Target issue key.
        assigned_team (str | dict): Team name or a dict with name and/or id.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if dry_run:
        res = {"dry_run": True, "action": "jira_set_assigned_team", "issue_key": issue_key, "assigned_team": assigned_team, "note": "Will dynamically discover assigned team field ID"}
        return _wrap("jira_set_assigned_team", res, {"issue_key": issue_key})
    assigned_team_field_id = _find_assigned_team_field(jira)
    if not assigned_team_field_id:
        res = {"error": "Failed to find Assigned Team field in Jira instance", "suggestion": "Use jira_debug_fields to identify the correct field ID or contact administrator"}
        return _wrap("jira_set_assigned_team", res, {"issue_key": issue_key})
    if isinstance(assigned_team, dict):
        team_obj: dict[str, Any] = {}
        if "name" in assigned_team and isinstance(assigned_team["name"], str):
            team_obj["name"] = assigned_team["name"]
        if "id" in assigned_team and (isinstance(assigned_team["id"], str) or isinstance(assigned_team["id"], int)):
            team_obj["id"] = str(assigned_team["id"])
        if not team_obj:
            return _wrap("jira_set_assigned_team", {"error": "Invalid assigned_team dict: must contain 'name' or 'id'", "provided": assigned_team}, {"issue_key": issue_key})
        fields = {assigned_team_field_id: team_obj}
    else:
        fields = {assigned_team_field_id: {"name": str(assigned_team)}}
    try:
        res = jira.update_issue_field(issue_key, fields)
        return _wrap("jira_set_assigned_team", {"success": True, "field_id_used": assigned_team_field_id, "assigned_team": assigned_team, "response": res}, {"issue_key": issue_key})
    except Exception as e:
        return _wrap("jira_set_assigned_team", {"error": f"Failed to set assigned team: {str(e)}", "issue_key": issue_key, "assigned_team": assigned_team, "field_id_used": assigned_team_field_id}, {"issue_key": issue_key})


@app.tool()
def jira_set_components(issue_key: str, components: list[Union[str, dict]], dry_run: bool = True) -> str:
    """Set/update components on a Jira issue (replaces all existing components).

    Args:
        issue_key (str): Target issue key.
        components (list[Union[str, dict]]): Component names or objects with "name"/"id".
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    comp_objs = []
    src = components if isinstance(components, list) else [components]
    for c in src:
        if isinstance(c, dict):
            obj: dict[str, Any] = {}
            if "name" in c and isinstance(c["name"], str):
                obj["name"] = c["name"]
            if "id" in c and (isinstance(c["id"], str) or isinstance(c["id"], int)):
                obj["id"] = str(c["id"])
            if obj:
                comp_objs.append(obj)
        else:
            comp_objs.append({"name": str(c)})
    fields = {"components": comp_objs}
    if dry_run:
        res = {"dry_run": True, "action": "jira_set_components", "issue_key": issue_key, "components": comp_objs, "fields": fields}
        return _wrap("jira_set_components", res, {"issue_key": issue_key})
    res = jira.update_issue_field(issue_key, fields)
    return _wrap("jira_set_components", res, {"issue_key": issue_key})


@app.tool()
def jira_set_parent(parent_key: str, issue_keys: list[str], dry_run: bool = True) -> str:
    """Set the parent of one or more issues.

    Args:
        parent_key (str): Parent issue key (e.g., "SDLC-8184").
        issue_keys (list[str]): Issue keys to add under the parent.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if dry_run:
        return _wrap("jira_set_parent", {"dry_run": True, "action": "jira_set_parent", "parent_key": parent_key, "issue_keys": issue_keys})
    try:
        for key in issue_keys:
            jira.update_issue_field(key, {"parent": {"key": parent_key}})
        return _wrap("jira_set_parent", {"success": True, "parent_key": parent_key, "issue_keys": issue_keys})
    except Exception as rest_err:
        log.info("REST parent update failed (%s), trying Agile API fallback", rest_err)
    try:
        jira.post(f"rest/agile/1.0/epic/{parent_key}/issue", data={"issues": issue_keys})
        return _wrap("jira_set_parent", {"success": True, "parent_key": parent_key, "issue_keys": issue_keys, "method": "agile_api"})
    except Exception as agile_err:
        return _wrap("jira_set_parent", {"error": "Failed to set parent", "parent_key": parent_key, "issue_keys": issue_keys, "rest_error": str(rest_err)[:200], "agile_error": str(agile_err)[:200]})


@app.tool()
def jira_clear_parent(issue_keys: list[str], dry_run: bool = True) -> str:
    """Remove the parent from one or more issues.

    Args:
        issue_keys (list[str]): Issue keys to unlink from their parent.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if dry_run:
        return _wrap("jira_clear_parent", {"dry_run": True, "action": "jira_clear_parent", "issue_keys": issue_keys})
    try:
        for key in issue_keys:
            jira.update_issue_field(key, {"parent": {"key": None}})
        return _wrap("jira_clear_parent", {"success": True, "issue_keys": issue_keys})
    except Exception as rest_err:
        log.info("REST clear parent failed (%s), trying Agile API fallback", rest_err)
    try:
        jira.post("rest/agile/1.0/epic/none/issue", data={"issues": issue_keys})
        return _wrap("jira_clear_parent", {"success": True, "issue_keys": issue_keys, "method": "agile_api"})
    except Exception as agile_err:
        return _wrap("jira_clear_parent", {"error": "Failed to clear parent", "issue_keys": issue_keys, "rest_error": str(rest_err)[:200], "agile_error": str(agile_err)[:200]})


@app.tool()
def jira_create_issue(
    project: str,
    issue_type: str,
    summary: str,
    description: str = "",
    assignee: Optional[str] = None,
    priority: Optional[Union[str, dict]] = None,
    labels: Optional[list[str]] = None,
    components: Optional[list[Union[str, dict]]] = None,
    epic_name: Optional[str] = None,
    assigned_team: Optional[Union[str, dict]] = None,
    dry_run: bool = True,
) -> str:
    """Create a new Jira issue.

    Args:
        project (str): Project key (e.g., "ABC", "SSE", "PROJ").
        issue_type (str): Issue type name (e.g., "Task", "Bug", "Story", "Epic").
        summary (str): One-line summary.
        description (str): Detailed description.
        assignee (Optional[str]): Username or account ID.
        priority (str | dict | None): Priority name or dict.
        labels (Optional[list[str]]): List of labels.
        components (list[str | dict] | None): List of component names or dicts.
        epic_name (Optional[str]): Epic name, required when issue_type is "Epic".
        assigned_team (str | dict | None): Optional assigned team name or dict.
        dry_run (bool): When True, returns preview without creating the issue.

    Returns:
        str: {key, id, self} on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    fields: dict[str, Any] = {"project": {"key": project}, "issuetype": {"name": issue_type}, "summary": summary}
    if description:
        fields["description"] = description
    if assignee:
        if deps.is_jira_cloud:
            fields["assignee"] = {"accountId": assignee}
        else:
            fields["assignee"] = {"name": assignee}
    if priority:
        if isinstance(priority, dict):
            pr: dict[str, Any] = {}
            if "name" in priority:
                pr["name"] = str(priority["name"])
            if "id" in priority:
                pr["id"] = str(priority["id"])
            if pr:
                fields["priority"] = pr
        else:
            fields["priority"] = {"name": str(priority)}
    if labels:
        fields["labels"] = [str(label) for label in (labels if isinstance(labels, list) else [labels]) if label is not None]
    if components:
        comp_objs = []
        src = components if isinstance(components, list) else [components]
        for c in src:
            if isinstance(c, dict):
                obj: dict[str, Any] = {}
                if "name" in c and isinstance(c["name"], str):
                    obj["name"] = c["name"]
                if "id" in c and (isinstance(c["id"], str) or isinstance(c["id"], int)):
                    obj["id"] = str(c["id"])
                if obj:
                    comp_objs.append(obj)
            else:
                comp_objs.append({"name": str(c)})
        if comp_objs:
            fields["components"] = comp_objs
    if assigned_team:
        assigned_team_field_id = _find_assigned_team_field(jira)
        if assigned_team_field_id:
            if isinstance(assigned_team, dict):
                team_obj: dict[str, Any] = {}
                if "name" in assigned_team and isinstance(assigned_team["name"], str):
                    team_obj["name"] = assigned_team["name"]
                if "id" in assigned_team and (isinstance(assigned_team["id"], str) or isinstance(assigned_team["id"], int)):
                    team_obj["id"] = str(assigned_team["id"])
                if team_obj:
                    fields[assigned_team_field_id] = team_obj
            else:
                fields[assigned_team_field_id] = {"name": str(assigned_team)}
        elif dry_run:
            fields["assigned_team_placeholder"] = f"Assigned Team: {assigned_team} (field discovery needed)"
        else:
            log.warning(f"Could not find Assigned Team field to set value: {assigned_team}")
    if issue_type.lower() == "epic":
        if not epic_name:
            return _wrap("jira_create_issue", {"error": "Epic Name is required when creating an Epic"})
        epic_name_field_id = _find_epic_name_field(jira)
        if epic_name_field_id:
            fields[epic_name_field_id] = epic_name
        else:
            if dry_run:
                fields["epic_name_placeholder"] = f"Epic Name: {epic_name} (field discovery needed)"
            else:
                common_epic_fields = ["customfield_10011", "customfield_10014", "customfield_10002"]
                field_set = False
                for field_id in common_epic_fields:
                    try:
                        fields.update({field_id: epic_name})
                        field_set = True
                        log.info(f"Using fallback Epic Name field: {field_id}")
                        break
                    except Exception:
                        continue
                if not field_set:
                    return _wrap("jira_create_issue", {"error": "Could not find Epic Name field. Use jira_debug_fields to identify the correct field ID."})
    if dry_run:
        return _wrap("jira_create_issue", {"dry_run": True, "action": "jira_create_issue", "fields": fields})
    result = jira.issue_create(fields)
    if result and "key" in result:
        return _wrap("jira_create_issue", {"key": result["key"], "id": result.get("id"), "self": result.get("self")})
    return _wrap("jira_create_issue", {"error": "Issue creation failed", "details": result})


@app.tool()
def jira_get_link_types() -> str:
    """List available Jira issue link types for your Jira instance.

    Returns:
        str: Provider response, typically an issueLinkTypes list with id and name.
    """
    jira = _require(deps.jira, "Jira")
    res = jira.get_issue_link_types()
    return _wrap("jira_get_link_types", res)


@app.tool()
def jira_add_issue_link(
    link_type: str,
    inward_issue_key: str,
    outward_issue_key: str,
    link_comment: str = "",
    dry_run: bool = True,
) -> str:
    """Create a link between two Jira issues.

    Args:
        link_type (str): Link type name (e.g., "Relates", "Blocks", "Triggers").
        inward_issue_key (str): The issue showing the "inward" relationship.
        outward_issue_key (str): The issue showing the "outward" relationship.
        link_comment (str): Optional comment in Jira Wiki Markup.
        dry_run (bool): When True, do not create; returns a preview payload.
    """
    jira = _require(deps.jira, "Jira")
    res = _create_issue_link(jira, link_type, inward_issue_key, outward_issue_key, link_comment, dry_run)
    return _wrap("jira_add_issue_link", res)


@app.tool()
def jira_delete_issue_link(link_id: str) -> str:
    """Delete a Jira issue link by its unique ID.

    Args:
        link_id (str): Link ID to remove as returned by Jira.

    Returns:
        str: Provider response.
    """
    jira = _require(deps.jira, "Jira")
    res = jira.remove_issue_link(link_id)
    return _wrap("jira_delete_issue_link", res)


@app.tool()
def jira_issue_worklog(issue_key: str, started: str, time_sec: int, comment: Optional[str] = None, dry_run: bool = True) -> str:
    """Log time spent on a Jira issue.

    Args:
        issue_key (str): The issue key to log time against.
        started (str): Start time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS.sssZ).
        time_sec (int): Time spent in seconds.
        comment (str): Optional comment in Jira Wiki Markup.
        dry_run (bool): When True, do not log time; return a preview payload.

    Returns:
        str: Provider response.
    """
    jira: Jira = _require(deps.jira, "Jira")
    if dry_run:
        res = {"dry_run": True, "action": "jira_issue_worklog", "issue_key": issue_key, "started": started, "time_sec": time_sec, "comment": comment}
        return _wrap("jira_issue_worklog", res)
    res = jira.issue_worklog(issue_key, started, time_sec, comment)
    return _wrap("jira_issue_worklog", res)


@app.tool()
def jira_get_all_agile_boards(project_key: str = None, board_name: str = None, board_type: str = None) -> str:
    """Get all Jira agile boards.

    Args:
        project_key (str): Filter by project key.
        board_name (str): Filter by board name.
        board_type (str): Filter by board type.

    Returns:
        str: A list of agile boards.
    """
    jira: Jira = _require(deps.jira, "Jira")
    res = jira.get_all_agile_boards(project_key=project_key, board_name=board_name, board_type=board_type)
    return _wrap("jira_get_all_agile_boards", res)


@app.tool()
def jira_get_issues_for_board(board_id: str) -> str:
    """Get all Jira issues for a board.

    Args:
        board_id (str): The ID of the board.

    Returns:
        str: A list of issues.
    """
    jira: Jira = _require(deps.jira, "Jira")
    res = jira.get_issues_for_board(board_id)
    return _wrap("jira_get_issues_for_board", res)


@app.tool()
def jira_get_all_active_sprints(board_id: str, state: str = "active") -> str:
    """Get Jira sprints for a board, filtered by state.

    Args:
        board_id (str): The ID of the board.
        state (str): Sprint state filter: "active", "future", "closed", or comma-separated combo.

    Returns:
        str: A list of sprints with id, name, state, startDate, endDate.
    """
    jira: Jira = _require(deps.jira, "Jira")
    try:
        res = jira.get_all_sprints_from_board(board_id, state=state)
    except AttributeError:
        res = jira.get_all_sprint(board_id, state=state)
    return _wrap("jira_get_all_active_sprints", res)


@app.tool()
def jira_get_all_issues_for_sprint_in_board(board_id: int, sprint_id: int) -> str:
    """Get all Jira issues for a sprint in a board.

    Args:
        board_id (int): The ID of the board.
        sprint_id (int): The ID of the sprint.

    Returns:
        str: A list of issues.
    """
    jira: Jira = _require(deps.jira, "Jira")
    res = jira.get_all_issues_for_sprint_in_board(board_id=board_id, sprint_id=sprint_id)
    return _wrap("jira_get_all_issues_for_sprint_in_board", res)


@app.tool()
def jira_add_issues_to_sprint(sprint_id: int, issue_keys: list[str], dry_run: bool = True) -> str:
    """Add issues to a Jira sprint.

    Args:
        sprint_id (int): The ID of the sprint.
        issue_keys (list[str]): The issue keys to add.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response.
    """
    jira: Jira = _require(deps.jira, "Jira")
    if dry_run:
        res = {"dry_run": True, "action": "jira_add_issues_to_sprint", "sprint_id": sprint_id, "issue_keys": issue_keys}
        return _wrap("jira_add_issues_to_sprint", res)
    res = jira.add_issues_to_sprint(sprint_id, issue_keys)
    return _wrap("jira_add_issues_to_sprint", res)


@app.tool()
def jira_add_issues_to_backlog(issue_keys: list[str], dry_run: bool = True) -> str:
    """Move issues to the backlog (remove from sprint).

    Args:
        issue_keys (list[str]): The issue keys to move to the backlog.
        dry_run (bool): When True, do not modify; return a preview payload.

    Returns:
        str: Provider response.
    """
    jira: Jira = _require(deps.jira, "Jira")
    if dry_run:
        res = {"dry_run": True, "action": "jira_add_issues_to_backlog", "issue_keys": issue_keys}
        return _wrap("jira_add_issues_to_backlog", res)
    res = jira.add_issues_to_backlog(issue_keys)
    return _wrap("jira_add_issues_to_backlog", res)


@app.tool()
def jira_download_attachment(attachment_id: str) -> str:
    """Download a Jira attachment by its ID.

    Args:
        attachment_id (str): Attachment ID (numeric).

    Returns:
        str: JSON with filename, mimeType, size, encoding ("utf-8" or "base64"), and content.
    """
    jira = _require(deps.jira, "Jira")
    if not re.match(r"^\d+$", attachment_id):
        return _wrap("jira_download_attachment", {"error": f"Invalid attachment_id (must be numeric): {attachment_id}"})
    meta_url = f"rest/api/2/attachment/{attachment_id}"
    try:
        meta = jira.get(meta_url)
    except Exception as e:
        return _wrap("jira_download_attachment", {"error": f"Failed to get attachment metadata: {e}"})
    filename = meta.get("filename", "unknown")
    mime_type = meta.get("mimeType", "application/octet-stream")
    size = meta.get("size", 0)
    content_url = meta.get("content", "")
    if not content_url:
        return _wrap("jira_download_attachment", {"error": "No content URL in attachment metadata"})
    try:
        session = getattr(jira, "_session", None)
        if not session:
            return _wrap("jira_download_attachment", {"error": "Could not obtain session from Jira instance"})
        response = session.get(content_url)
        response.raise_for_status()
        raw_bytes = response.content
    except Exception as e:
        return _wrap("jira_download_attachment", {"error": f"Failed to download attachment: {e}"})
    text_mimes = ("text/", "application/json", "application/yaml", "application/xml", "image/svg+xml")
    is_text = any(mime_type.startswith(t) if t.endswith("/") else mime_type == t for t in text_mimes)
    if is_text:
        try:
            content = raw_bytes.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            content = base64.b64encode(raw_bytes).decode("ascii")
            encoding = "base64"
    else:
        content = base64.b64encode(raw_bytes).decode("ascii")
        encoding = "base64"
    return _wrap("jira_download_attachment", {"filename": filename, "mimeType": mime_type, "size": size, "encoding": encoding, "content": content})


@app.tool()
def jira_add_attachment(issue_key: str, file_path: str, dry_run: bool = True) -> str:
    """Upload a local file as an attachment to a Jira issue.

    Args:
        issue_key (str): Target issue key (e.g., "ABC-123").
        file_path (str): Absolute path to the file on disk.
        dry_run (bool): When True, do not upload; return a preview payload.

    Returns:
        str: Attachment metadata on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if not re.match(r"^[A-Z][A-Z0-9_]+-\d+$", issue_key, re.IGNORECASE):
        return _wrap("jira_add_attachment", {"error": f"Invalid issue_key format: {issue_key}"})
    path = Path(file_path)
    if not path.is_file():
        return _wrap("jira_add_attachment", {"error": f"File not found: {file_path}"})
    if dry_run:
        return _wrap("jira_add_attachment", {"dry_run": True, "action": "jira_add_attachment", "issue_key": issue_key, "filename": path.name, "size_bytes": path.stat().st_size})
    try:
        result = jira.add_attachment(issue_key, str(path))
        if isinstance(result, dict):
            return _wrap("jira_add_attachment", {"success": True, "issue_key": issue_key, "id": result.get("id"), "filename": result.get("filename"), "size": result.get("size"), "mimeType": result.get("mimeType")})
        return _wrap("jira_add_attachment", {"success": True, "issue_key": issue_key, "response": str(result)})
    except Exception as e:
        return _wrap("jira_add_attachment", {"error": f"Failed to upload attachment: {e}", "issue_key": issue_key, "file_path": file_path})


@app.tool()
def jira_delete_attachment(attachment_id: str, dry_run: bool = True) -> str:
    """Delete an attachment from a Jira issue.

    Args:
        attachment_id (str): Attachment ID (numeric).
        dry_run (bool): When True, do not delete; return a preview payload.

    Returns:
        str: Confirmation on success, or a dry-run preview object.
    """
    jira = _require(deps.jira, "Jira")
    if not re.match(r"^\d+$", attachment_id):
        return _wrap("jira_delete_attachment", {"error": f"Invalid attachment_id (must be numeric): {attachment_id}"})
    if dry_run:
        return _wrap("jira_delete_attachment", {"dry_run": True, "action": "jira_delete_attachment", "attachment_id": attachment_id})
    try:
        jira.remove_attachment(attachment_id)
        return _wrap("jira_delete_attachment", {"success": True, "attachment_id": attachment_id})
    except Exception as e:
        return _wrap("jira_delete_attachment", {"error": f"Failed to delete attachment: {e}", "attachment_id": attachment_id})


# ---------- Startup ----------

def startup() -> None:
    deps.jira, deps.is_jira_cloud = _init_jira_from_env()
    log.info("Startup complete: Jira=%s", bool(deps.jira))


if __name__ == "__main__":
    _load_env_from_cli_and_defaults()
    startup()
    app.run("stdio")
