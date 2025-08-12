#!/usr/bin/env python3
import os
import re
import csv
import argparse
import time
from typing import Dict, Any, Optional, Tuple, List
import requests
from datetime import datetime

# === Configuration ===
REQUEST_DELAY = 0.1   # seconds between API calls
MAX_RETRIES   = 1     # retry once on transient POST failures

# === Environment ===
JIRA_URL = os.environ.get("JIRA_URL")
JIRA_USER = os.environ.get("JIRA_USER")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN")
JIRA_START_DATE_FIELD = os.environ.get("JIRA_START_DATE_FIELD")  # Optional custom start date field

SESSION = requests.Session()
SESSION.auth = (JIRA_USER, JIRA_API_TOKEN)
SESSION.headers.update({
    "Accept": "application/json",
    "Content-Type": "application/json"
})

# === Helpers ===
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def clean_date(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    s = str(val).strip()
    return s if DATE_RE.match(s) else None

def require_env():
    missing = [k for k, v in [("JIRA_URL", JIRA_URL), ("JIRA_USER", JIRA_USER), ("JIRA_API_TOKEN", JIRA_API_TOKEN)] if not v]
    if missing:
        raise SystemExit(f"Error: Missing environment variables: {', '.join(missing)}")

def jira_post(path: str, json: Dict[str, Any], dry_run: bool = False) -> Tuple[int, Dict[str, Any]]:
    url = f"{JIRA_URL.rstrip('/')}{path}"
    if dry_run:
        # Print only the summary for readability
        fields = json.get("fields", {})
        print(f"[DRY-RUN] POST {url} :: {fields.get('summary', '(no summary)')}")
        return 200, {"key": "DRY-KEY"}
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = SESSION.post(url, json=json)
            if r.status_code < 400:
                return r.status_code, (r.json() if r.text else {})
            else:
                print(f"Jira POST error {r.status_code}: {r.text}")
                return r.status_code, (r.json() if r.text else {})
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES:
                print(f"Retrying POST after error: {e}")
                time.sleep(1.0)
                continue
            raise

def jira_get(path: str, params: Dict[str, Any] = None) -> Tuple[int, Dict[str, Any]]:
    url = f"{JIRA_URL.rstrip('/')}{path}"
    r = SESSION.get(url, params=params or {})
    if r.status_code >= 400:
        print(f"Jira GET error {r.status_code}: {r.text}")
    return r.status_code, (r.json() if r.text else {})

def jira_delete(path: str, dry_run: bool = False) -> int:
    url = f"{JIRA_URL.rstrip('/')}{path}"
    if dry_run:
        print(f"[DRY-RUN] DELETE {url}")
        return 204
    r = SESSION.delete(url)
    if r.status_code >= 400:
        print(f"Jira DELETE error {r.status_code}: {r.text}")
    return r.status_code

def search_issues(jql: str, fields: str = "key", max_per_page: int = 100) -> List[Dict[str, Any]]:
    start_at = 0
    issues: List[Dict[str, Any]] = []
    while True:
        code, resp = jira_get("/rest/api/3/search", params={
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_per_page,
            "fields": fields
        })
        if code != 200:
            raise SystemExit(f"Failed to search issues with JQL: {jql}")
        batch = resp.get("issues", [])
        if not batch:
            break
        issues.extend(batch)
        if start_at + len(batch) >= resp.get("total", 0):
            break
        start_at += len(batch)
    return issues

def wipe_project(project_key: str, dry_run: bool = False):
    print(f"Searching for issues to delete in project {project_key}...")
    issues = search_issues(f"project = {project_key}", fields="key")
    if not issues:
        print("No issues found to delete.")
        return
    total = len(issues)
    print(f"Deleting {total} issues in project {project_key}...")
    for i, issue in enumerate(issues, 1):
        key = issue["key"]
        code = jira_delete(f"/rest/api/3/issue/{key}?deleteSubtasks=true", dry_run=dry_run)
        ok = code in (200, 202, 204)
        if i == 1 or i % 10 == 0 or i == total:
            print(f"[{i}/{total}] Delete {key}: {'OK' if ok else f'HTTP {code}'}")
        time.sleep(REQUEST_DELAY)
    print("Wipe complete.")

def to_adf(text: str) -> Dict[str, Any]:
    safe = (text or "").strip()
    if not safe:
        safe = " "
    return {
        "type": "doc",
        "version": 1,
        "content": [{
            "type": "paragraph",
            "content": [{
                "type": "text",
                "text": safe
            }]
        }]
    }

def create_issue(fields: Dict[str, Any], dry_run: bool = False) -> Optional[str]:
    code, resp = jira_post("/rest/api/3/issue", {"fields": fields}, dry_run=dry_run)
    if code in (200, 201):
        return resp.get("key")
    return None

def link_issue(inward_key: str, outward_key: str, dry_run: bool = False) -> bool:
    payload = {
        "type": {"name": "Blocks"},
        "inwardIssue": {"key": inward_key},
        "outwardIssue": {"key": outward_key}
    }
    code, _ = jira_post("/rest/api/3/issueLink", payload, dry_run=dry_run)
    return code in (200, 201)

def parse_args():
    ap = argparse.ArgumentParser(description="Import Jira Tasks + Sub-tasks from CLEAN CSV. Wipes project by default unless --no-wipe.")
    ap.add_argument("--csv", required=True, help="Path to CLEAN CSV with tasks/subtasks")
    ap.add_argument("--project-key", required=True, help="Jira project key")
    ap.add_argument("--dry-run", action="store_true", help="Only print what would happen, do not modify Jira")
    ap.add_argument("--no-wipe", action="store_true", help="Do NOT delete all issues in the project before import")
    return ap.parse_args()

def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main():
    require_env()
    args = parse_args()
    rows = read_csv(args.csv)

    # Sanity check: each Task should be followed by two Sub-tasks (Design, Approval)
    # We won't hard fail, but we could print a warning if pattern deviates.
    # (Omitted for speed)

    if not args.no_wipe:
        wipe_project(args.project_key, dry_run=args.dry_run)

    created: Dict[str, str] = {}
    pending_links: List[Tuple[str, str]] = []
    current_parent: Optional[str] = None
    total_rows = len(rows)

    print("Creating tasks and subtasks...")
    for idx, row in enumerate(rows, 1):
        summary = row["Summary"]
        description = to_adf(row.get("Description", ""))
        issue_type = row["Issue Type"]
        start_date = row.get("Start date")
        due_date = row.get("Due date")
        depends_on = (row.get("Depends on") or "").strip()

        if issue_type == "Task":
            fields: Dict[str, Any] = {
                "summary": summary,
                "project": {"key": args.project_key},
                "issuetype": {"name": "Task"},
                "description": description
            }
            due_clean = clean_date(due_date)
            if due_clean:
                fields["duedate"] = due_clean
            if JIRA_START_DATE_FIELD:
                start_clean = clean_date(start_date)
                if start_clean:
                    fields[JIRA_START_DATE_FIELD] = start_clean

            key = create_issue(fields, dry_run=args.dry_run)
            if not key:
                raise SystemExit(f"Failed to create task '{summary}'")
            created[summary] = key
            current_parent = summary

            if idx == 1 or idx % 10 == 0 or idx == total_rows:
                print(f"[{idx}/{total_rows}] Created Task: {summary} ({key})")

        elif issue_type == "Sub-task":
            if not current_parent or current_parent not in created:
                raise SystemExit(f"Subtask '{summary}' found without a valid parent task")
            fields = {
                "summary": summary,
                "project": {"key": args.project_key},
                "issuetype": {"name": "Sub-task"},
                "description": description,
                "parent": {"key": created[current_parent]}
            }
            due_clean = clean_date(due_date)
            if due_clean:
                fields["duedate"] = due_clean
            if JIRA_START_DATE_FIELD:
                start_clean = clean_date(start_date)
                if start_clean:
                    fields[JIRA_START_DATE_FIELD] = start_clean

            key = create_issue(fields, dry_run=args.dry_run)
            if not key:
                raise SystemExit(f"Failed to create subtask '{summary}' for parent '{current_parent}'")
            created[f"{current_parent}:{summary}"] = key

            if depends_on:
                # Allow dependency on another Task or on a sibling subtask name
                dep_key = (
                    created.get(depends_on) or
                    created.get(f"{depends_on}:Design") or
                    created.get(f"{depends_on}:Approval")
                )
                if dep_key:
                    pending_links.append((dep_key, key))
                else:
                    print(f"Warning: Could not resolve dependency '{depends_on}' for '{summary}'")

        time.sleep(REQUEST_DELAY)

    if pending_links:
        print("Linking dependencies...")
        for dep_key, tgt_key in pending_links:
            link_issue(dep_key, tgt_key, dry_run=args.dry_run)
            time.sleep(REQUEST_DELAY)
        print("Dependency linking complete.")
    else:
        print("No dependencies to link.")

    print("Import complete.")
    if args.dry_run:
        print("Dry-run mode: no issues actually created.")

if __name__ == "__main__":
    main()
