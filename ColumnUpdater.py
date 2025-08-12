
#!/usr/bin/env python3
"""
Jira Spreadsheet Updater
------------------------
Reads a CSV and pushes fields (dates, labels, components, versions, description, etc.)
to matching Jira issues. Also creates "is blocked by" dependencies.

Match priority:
1) IssueKey column (e.g., F22E-123)
2) If missing: JQL by Summary + Project Key

Authentication:
- Prefer environment variables: JIRA_URL, JIRA_USER, JIRA_TOKEN
- Or pass via CLI flags

Usage example:
  python jira_updater.py --project-key F22E --csv updates.csv --startdate-field customfield_12345 --sleep 0.1 --dry-run

CSV expected columns (use any subset):
- IssueKey (preferred) or Summary (for matching)
- StartDate (any common format; will be normalized to yyyy-mm-dd)
- DueDate (any common format; normalized to yyyy-mm-dd)
- Description
- Priority (e.g., High, Medium)
- Labels (comma-separated)
- Components (comma-separated names)
- FixVersions (comma-separated names)
- Dependencies (comma-separated issue keys that this issue *depends on*; we will create "is blocked by" links)
- AssigneeEmail (optional; resolves to accountId in Cloud)
- EpicKey (optional; link to an Epic by issue key)
- ParentKey (optional; for subtasks)

Notes:
- Start date is often a custom field in Jira Cloud; pass its id with --startdate-field if you want it set.
- This script updates issues regardless of issue type (task, story, sub-task, epic, etc.).
- Safe by default with --dry-run; remove to perform updates.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

import requests

DATE_FMT = "%Y-%m-%d"

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)

def normalize_date(s: str) -> Optional[str]:
    if not s or str(s).strip() == "" or str(s).strip().lower() in {"nan", "none", "null"}:
        return None
    s = str(s).strip()
    # Try multiple formats robustly
    fmts = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y",
        "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
        "%Y/%m/%d"
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            return dt.strftime(DATE_FMT)
        except Exception:
            pass
    # Try ISO partials (e.g., 2025-8-9)
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime(DATE_FMT)
    except Exception:
        return None

class Jira:
    def __init__(self, base_url: str, user: str, token: str, sleep: float = 0.1):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, token)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.sleep = sleep
        self.user_cache: Dict[str, Optional[str]] = {}
        self.version_cache: Dict[str, Dict[str, Any]] = {}
        self.component_cache: Dict[str, Dict[str, Any]] = {}

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def get(self, path: str, params: Optional[dict] = None) -> requests.Response:
        r = self.session.get(self._url(path), params=params)
        self._throttle()
        self._check(r)
        return r

    def post(self, path: str, payload: dict) -> requests.Response:
        r = self.session.post(self._url(path), data=json.dumps(payload))
        self._throttle()
        self._check(r)
        return r

    def put(self, path: str, payload: dict) -> requests.Response:
        r = self.session.put(self._url(path), data=json.dumps(payload))
        self._throttle()
        self._check(r)
        return r

    def _throttle(self):
        if self.sleep > 0:
            time.sleep(self.sleep)

    def _check(self, resp: requests.Response):
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Jira API error {resp.status_code}: {detail}")

    # --------- Helpers ---------
    def search_issue_by_summary(self, project_key: str, summary: str) -> Optional[str]:
        # Exact match on summary to be safe
        jql = f'project = "{project_key}" AND summary ~ "\"{summary}\"" ORDER BY created DESC'
        r = self.post("/rest/api/3/search", {"jql": jql, "maxResults": 5, "fields": ["summary", "issuetype"]})
        issues = r.json().get("issues", [])
        # Prefer exact summary match (case-insensitive)
        for it in issues:
            if it["fields"]["summary"].strip().lower() == summary.strip().lower():
                return it["key"]
        # fallback first
        return issues[0]["key"] if issues else None

    def resolve_user_account_id(self, email: str) -> Optional[str]:
        if email in self.user_cache:
            return self.user_cache[email]
        # Cloud: /user/search?query=
        r = self.get("/rest/api/3/user/search", params={"query": email})
        users = r.json()
        acct = users[0]["accountId"] if users else None
        self.user_cache[email] = acct
        return acct

    def get_or_create_version(self, project_id: str, name: str) -> Dict[str, Any]:
        if project_id not in self.version_cache:
            self.version_cache[project_id] = {}
        if name in self.version_cache[project_id]:
            return self.version_cache[project_id][name]
        # List versions
        r = self.get(f"/rest/api/3/project/{project_id}/versions")
        versions = r.json()
        for v in versions:
            if v["name"].strip().lower() == name.strip().lower():
                self.version_cache[project_id][name] = v
                return v
        # Create new version
        payload = {"name": name, "projectId": project_id}
        v = self.post("/rest/api/3/version", payload).json()
        self.version_cache[project_id][name] = v
        return v

    def list_project_components(self, project_id: str) -> Dict[str, Any]:
        if project_id in self.component_cache:
            return self.component_cache[project_id]
        r = self.get(f"/rest/api/3/project/{project_id}/components")
        comps = {c["name"].strip().lower(): c for c in r.json()}
        self.component_cache[project_id] = comps
        return comps

    def get_project_meta(self, project_key: str) -> Dict[str, Any]:
        r = self.get(f"/rest/api/3/project/{project_key}")
        return r.json()

    # --------- Update ---------
    def update_issue_fields(self, issue_key: str, fields: Dict[str, Any], dry_run: bool = True):
        if dry_run:
            return {"dry_run": True, "issue": issue_key, "fields": fields}
        r = self.put(f"/rest/api/3/issue/{issue_key}", {"fields": fields})
        return r.json() if r.text else {"ok": True}

    def add_issue_link_is_blocked_by(self, issue_key: str, depends_on_key: str, dry_run: bool = True):
        payload = {
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": issue_key},      # A is blocked by B  => inward = A
            "outwardIssue": {"key": depends_on_key} # outward = B
        }
        if dry_run:
            return {"dry_run": True, "link": payload}
        return self.post("/rest/api/3/issueLink", payload).json()

def build_fields(row: Dict[str, Any], startdate_field: Optional[str], jira: Jira, project: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}

    # Dates
    start = normalize_date(row.get("StartDate", ""))
    due = normalize_date(row.get("DueDate", ""))
    if startdate_field and start:
        fields[startdate_field] = start
    if due:
        fields["duedate"] = due

    # Description
    desc = row.get("Description")
    if isinstance(desc, str) and desc.strip():
        fields["description"] = desc

    # Priority
    prio = row.get("Priority")
    if isinstance(prio, str) and prio.strip():
        fields["priority"] = {"name": prio.strip()}

    # Labels
    labels = row.get("Labels")
    if isinstance(labels, str) and labels.strip():
        fields["labels"] = [x.strip() for x in labels.split(",") if x.strip()]

    # Components
    comps = row.get("Components")
    if isinstance(comps, str) and comps.strip():
        comp_map = jira.list_project_components(project["id"])
        fields["components"] = []
        for name in [x.strip() for x in comps.split(",") if x.strip()]:
            c = comp_map.get(name.lower())
            if c:
                fields["components"].append({"id": c["id"]})
            else:
                # Create component if missing
                created = jira.post("/rest/api/3/component", {"name": name, "projectId": project["id"]}).json()
                comp_map[name.lower()] = created
                fields["components"].append({"id": created["id"]})

    # FixVersions
    vers = row.get("FixVersions")
    if isinstance(vers, str) and vers.strip():
        project_id = project["id"]
        fields["fixVersions"] = []
        for vname in [x.strip() for x in vers.split(",") if x.strip()]:
            v = jira.get_or_create_version(project_id, vname)
            fields["fixVersions"].append({"id": v["id"]})

    # Assignee by email -> accountId (Cloud)
    ass_email = row.get("AssigneeEmail")
    if isinstance(ass_email, str) and ass_email.strip():
        acct = jira.resolve_user_account_id(ass_email.strip())
        if acct:
            fields["assignee"] = {"accountId": acct}

    # Epic link (Cloud often customfield_10014)
    epic_key = row.get("EpicKey")
    if isinstance(epic_key, str) and epic_key.strip():
        # Some Cloud sites still use customfield_10014 for Epic Link. Allow override via env or CLI?
        epic_field = os.environ.get("EPIC_LINK_FIELD", "customfield_10014")
        fields[epic_field] = epic_key.strip()

    # Parent for subtasks (by key)
    parent_key = row.get("ParentKey")
    if isinstance(parent_key, str) and parent_key.strip():
        fields["parent"] = {"key": parent_key.strip()}

    return fields

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jira-url", default=env("JIRA_URL"), help="Base URL, e.g. https://your-domain.atlassian.net")
    ap.add_argument("--jira-user", default=env("JIRA_USER"), help="Email/username for Jira")
    ap.add_argument("--jira-token", default=env("JIRA_TOKEN"), help="API token")
    ap.add_argument("--project-key", required=True, help="Project key, e.g. F22E")
    ap.add_argument("--csv", required=True, help="CSV path")
    ap.add_argument("--startdate-field", default=os.environ.get("START_DATE_FIELD"), help="Custom field id for Start date (e.g., customfield_12345)")
    ap.add_argument("--sleep", type=float, default=0.1, help="Seconds to sleep between API calls")
    ap.add_argument("--dry-run", action="store_true", help="Print actions without updating Jira")
    ap.add_argument("--max", type=int, default=None, help="Update at most N rows (for testing)")
    ap.add_argument("--dependencies-direction", choices=["blocked_by", "blocks"], default="blocked_by",
                    help="If 'blocked_by', creates links so the issue is blocked by listed Dependencies. If 'blocks', the issue blocks the listed issues.")
    args = ap.parse_args()

    if not args.jira_url or not args.jira_user or not args.jira_token:
        ap.error("Jira credentials are missing. Set env vars JIRA_URL, JIRA_USER, JIRA_TOKEN or pass flags.")

    jira = Jira(args.jira_url, args.jira_user, args.jira_token, sleep=args.sleep)
    project = jira.get_project_meta(args.project_key)

    # Read CSV
    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updated = 0
    linked = 0
    skipped = 0

    for i, row in enumerate(rows, start=1):
        if args.max and updated >= args.max:
            break

        key = (row.get("IssueKey") or "").strip()
        summary = (row.get("Summary") or "").strip()

        if not key:
            if not summary:
                print(f"[{i}] SKIP: row has neither IssueKey nor Summary")
                skipped += 1
                continue
            match = jira.search_issue_by_summary(args.project_key, summary)
            if not match:
                print(f"[{i}] SKIP: no issue found for Summary='{summary}'")
                skipped += 1
                continue
            key = match

        fields = build_fields(row, args.startdate_field, jira, project)

        try:
            jira.update_issue_fields(key, fields, dry_run=args.dry_run)
            updated += 1
            if updated % 10 == 0:
                print(f"[{updated}] Updated through {key}")
        except Exception as e:
            print(f"[{i}] ERROR updating {key}: {e}", file=sys.stderr)

        # Dependencies
        deps_raw = row.get("Dependencies")
        if isinstance(deps_raw, str) and deps_raw.strip():
            deps = [x.strip() for x in deps_raw.split(",") if x.strip()]
            for dep in deps:
                try:
                    if args.dependencies_direction == "blocked_by":
                        jira.add_issue_link_is_blocked_by(key, dep, dry_run=args.dry_run)
                    else:
                        # Opposite direction
                        jira.add_issue_link_is_blocked_by(dep, key, dry_run=args.dry_run)
                    linked += 1
                except Exception as e:
                    print(f"[{i}] ERROR linking {key} <-> {dep}: {e}", file=sys.stderr)

    print(f"Done. Updated: {updated}, Linked: {linked}, Skipped: {skipped}. Dry-run={args.dry_run}")
    if args.dry_run:
        print("No changes were made. Remove --dry-run to apply updates.")

if __name__ == "__main__":
    main()
