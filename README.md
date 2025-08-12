# Jira Gantt Chart Automation

Automates importing tasks/subtasks into Jira from a CSV, updating issue fields (dates, labels, components, etc.), and creating dependency links between issues.

**Scripts**
- `Importer.py` — Creates Tasks & Sub-tasks from a clean CSV (optionally wipes the project first).
- `ColumnUpdater.py` — Updates issue fields from CSV and (optionally) adds dependency links by key.
- `DependencyUpdater.py` — Creates “is blocked by” links by matching summaries.
- `Runner.py` — Orchestrates all three; now forwards `--dry-run` and `--no-wipe` to the right places.

---

## 0) Before Running
- Create a list of tasks to be added to the Gantt Chart.
- Determine what subtasks should be common for all (Design and Approval for us)
- Give GPT (or AI of choice -- bonus points for Agent Mode) the file names "FS_EV_Gantt_Chart.csv" and your list of tasks with the following prompt:

```bash
I’m sending you a CSV file that defines the exact format I want (column names, order, and any sample rows). 
I will also send you a list of tasks that need to be added to a new CSV in the exact same format.  

Your job:
- Keep the exact column order and headers from the original CSV I provide.
- Fill in Start date, Due date, and any other relevant columns for each task.
- Assign realistic durations based on my guidance or your own assumptions.
- Set dependencies in the correct column using task names or keys (matching the CSV’s style).
- Return the result as a CSV file ready to use with my Jira scripts, without changing column names or adding/removing columns.

Here is my original CSV format:
[attach CSV file here]

Here is my new task list:
[list tasks here with any special notes on durations or dependencies]
```


- Run the script once you're sure the csv (the Excel file) is perfect.

---

## 1) Prerequisites

- Python 3.8+
- Jira Cloud account + API token
- Project permissions to create, update, delete, and link issues

Install dependencies:
```bash
pip install requests
```

---

## 2) Environment variables

These scripts require Jira credentials via environment variables.

### **macOS / Linux (bash/zsh)**
```bash
export JIRA_URL="https://your-domain.atlassian.net"
export JIRA_USER="you@example.com"
export JIRA_API_TOKEN="your_api_token"   # Used by Importer.py
export JIRA_TOKEN="your_api_token"       # Used by ColumnUpdater.py / DependencyUpdater.py
export JIRA_START_DATE_FIELD="customfield_12345"  # Optional: Jira custom field ID for Start Date
```
Add these lines to your `~/.bashrc` or `~/.zshrc` to make them permanent.

### **Windows PowerShell**
For the current session:
```powershell
$env:JIRA_URL="https://your-domain.atlassian.net"
$env:JIRA_USER="you@example.com"
$env:JIRA_API_TOKEN="your_api_token"
$env:JIRA_TOKEN="your_api_token"
$env:JIRA_START_DATE_FIELD="customfield_12345"
```

To set permanently (available in all sessions):
```powershell
setx JIRA_URL "https://your-domain.atlassian.net"
setx JIRA_USER "you@example.com"
setx JIRA_API_TOKEN "your_api_token"
setx JIRA_TOKEN "your_api_token"
setx JIRA_START_DATE_FIELD "customfield_12345"
```
*(You will need to open a new terminal after using `setx` for the changes to take effect.)*

---

## 3) CSV format

Typical columns your CSV can include (use what you need):

```
Summary,Issue Type,Description,Start date,Due date,Depends on,IssueKey,StartDate,DueDate,Priority,Labels,Components,FixVersions,AssigneeEmail,EpicKey,ParentKey,Dependencies
```

**Importer.py (minimum):**
- `Summary`
- `Issue Type` (`Task` or `Sub-task`)
- `Depends on` (optional; link target can be a Task name or sibling sub-task like `Design`/`Approval`)

**ColumnUpdater.py (matching):**
- Prefer `IssueKey`; otherwise `Summary` is used to find the issue
- Recognizes `StartDate`, `DueDate`, `Description`, `Priority`, `Labels` (comma-sep), `Components` (comma-sep), `FixVersions` (comma-sep), `AssigneeEmail`, `EpicKey`, `ParentKey`, and `Dependencies` (comma-sep keys)

**DependencyUpdater.py (by summary):**
- `Summary` and `Depends on` (both matched via JQL)

---

## 4) Run everything with `Runner.py`

`Runner.py` calls the scripts in this order:

1) `Importer.py`
2) `ColumnUpdater.py`
3) `DependencyUpdater.py`

It passes:
- `--dry-run` → to all scripts
- `--no-wipe` → only to `Importer.py`

**Defaults in Runner.py:**
- CSV file: `FS_EV_Gantt_Chart.csv`
- Project key: `FS_EV`

### Examples

**Dry run (no changes to Jira):**
```bash
python Runner.py --dry-run
```

**Import without wiping existing issues first (then update + link):**
```bash
python Runner.py --no-wipe
```

**Dry run + no wipe:**
```bash
python Runner.py --dry-run --no-wipe
```

---

## 5) Run scripts individually

### A) Importer — create tasks/subtasks (with optional wipe)
```bash
python Importer.py --csv YOUR.csv --project-key YOURKEY
# add --dry-run to simulate
# add --no-wipe to skip deleting existing issues first
```

- Reads `JIRA_URL`, `JIRA_USER`, `JIRA_API_TOKEN`
- Uses `JIRA_START_DATE_FIELD` if provided
- By default wipes all issues in the project unless `--no-wipe` is set

---

### B) ColumnUpdater — update issue fields (+ optional dependency links by key)
```bash
python ColumnUpdater.py --csv YOUR.csv --project-key YOURKEY --startdate-field customfield_12345
# add --dry-run to simulate
# add --dependencies-direction blocked_by|blocks (default: blocked_by)
```

- Matches by `IssueKey` (preferred) or `Summary`
- Normalizes dates; creates missing Components/FixVersions when needed
- Can create “Blocks” links based on `Dependencies` keys in the CSV

---

### C) DependencyUpdater — link by summary (A is **blocked by** B)
```bash
python DependencyUpdater.py --csv YOUR.csv --project-key YOURKEY
# add --dry-run to
```

- Finds Jira keys by summary (`Summary` and `Depends on`) and links them with a “Blocks” relationship (inward = source, outward = depends-on)

---

## 6) Tips

- Start with `--dry-run` to verify what will happen
- Ensure summaries are unique if you rely on summary matching
- If you see date errors, confirm your CSV dates are `yyyy-mm-dd`
- If Components/FixVersions don’t exist, `ColumnUpdater.py` will create them automatically

---

## 7) Troubleshooting

- **Credentials error:** Double-check env vars (note `JIRA_API_TOKEN` vs `JIRA_TOKEN`)
- **HTTP 400 on create/update:** Inspect the printed body; often caused by invalid dates or required fields missing in your Jira workflow
- **Nothing gets linked:** For summary-based linking, make sure the text in `Summary` / `Depends on` exactly matches Jira issue summaries
