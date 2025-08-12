#!/usr/bin/env python3
import csv, os, argparse, time, json, requests

class Jira:
    def __init__(self, base_url, user, token, sleep=0.0):
        self.base_url = base_url.rstrip('/')
        self.s = requests.Session()
        self.s.auth = (user, token)
        self.sleep = sleep

    def _u(self, path):
        return self.base_url + path

    def _t(self):
        if self.sleep:
            time.sleep(self.sleep)

    def _check(self, r):
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise RuntimeError(f"Jira API error {r.status_code}: {detail}")

    def get(self, path):
        r = self.s.get(self._u(path)); self._t(); self._check(r); return r

    def post(self, path, payload):
        r = self.s.post(self._u(path), data=json.dumps(payload), headers={'Content-Type': 'application/json'})
        self._t()
        self._check(r)
        return r

    def search_by_summary(self, project_key, summary):
        jql = f'project = "{project_key}" AND summary ~ "{summary}"'
        payload = {"jql": jql, "maxResults": 10, "fields": ["summary"]}
        r = self.post("/rest/api/3/search", payload)
        return r.json()

    def link_is_blocked_by(self, issue_key: str, depends_on_key: str, dry: bool=True):
        payload = {
            "type":{"name":"Blocks"},
            "inwardIssue":{"key": issue_key},
            "outwardIssue":{"key": depends_on_key}
        }
        if dry:
            return {"dry_run": True, "link": payload}
        r = self.post("/rest/api/3/issueLink", payload)
        # Handle empty body responses safely
        if (r.status_code in (200, 201, 204)) and (not (r.text or "").strip()):
            return {"ok": True, "status": r.status_code}
        try:
            return r.json()
        except Exception:
            return {"ok": r.ok, "status": r.status_code, "text": r.text}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jira-url', default=os.getenv('JIRA_URL'))
    ap.add_argument('--jira-user', default=os.getenv('JIRA_USER'))
    ap.add_argument('--jira-token', default=os.getenv('JIRA_TOKEN'))
    ap.add_argument('--project-key', required=True)
    ap.add_argument('--csv', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--sleep', type=float, default=0.0)
    args = ap.parse_args()

    jira = Jira(args.jira_url, args.jira_user, args.jira_token, sleep=args.sleep)

    with open(args.csv, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    processed = 0
    linked = 0
    link_counter = 0

    for r in rows:
        src_sum = r.get('Summary')
        dep_sum = r.get('Depends on')
        if not dep_sum or not src_sum:
            continue
        processed += 1

        src_results = jira.search_by_summary(args.project_key, src_sum)
        dep_results = jira.search_by_summary(args.project_key, dep_sum)

        try:
            src_key = src_results['issues'][0]['key']
            dep_key = dep_results['issues'][0]['key']
        except (KeyError, IndexError):
            print(f"[ERROR] Could not find keys for: {src_sum} or {dep_sum}")
            continue

        try:
            jira.link_is_blocked_by(src_key, dep_key, dry=args.dry_run)
            linked += 1
            link_counter += 1
            if link_counter % 10 == 0:
                print(f"[progress] Linked {link_counter} so far... last: {src_key} <- {dep_key}")
        except Exception as e:
            print(f"[ERROR] linking {src_key} <- {dep_key}: {e}")
            continue

    print(f"\nFinal Summary: Processed {processed} tasks with dependencies, successfully linked {linked} of them.")

if __name__ == '__main__':
    main()
