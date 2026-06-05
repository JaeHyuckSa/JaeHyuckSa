---
name: github-issue-finder
description: "Find open-source GitHub issues worth contributing to: beginner-friendly issues, bugs, feature requests, repository issue exploration, and contribution reports. Exclude documentation-only issues."
---

# github-issue-finder

Find contribution-worthy GitHub issues. Exclude docs-only work. Verify nobody is already working on each recommendation. Save a short report to `reports/find-issues/YYYY-MM-DD-HHMMSS.md`.

## Collect

Use `python3 github_issue_exporter.py <owner/repo> --output outputs/<repo>-open-issues.json` for full issue/comment/linked-PR JSON. Use `gh issue list/view` for live checks.

## Exclude

Never recommend docs-only issues: docs/documentation labels, README, typo, docstring, spelling, proofreading, translation, or documentation work.

## Verify

Before recommending an issue, run:

```bash
gh pr list --repo <owner/repo> --search "<issue_number>" --state all --json number,title,state,url
gh search prs "fixes #<issue_number>" --repo <owner/repo> --state open --limit 5
gh pr list --repo <owner/repo> --search "#<issue_number> in:title" --state all --limit 5
gh api "repos/<owner/repo>/issues/<issue_number>/timeline" --paginate --jq '.[] | select(.event == "cross-referenced") | .source.issue.number' 2>/dev/null | head -10
gh issue view <issue_number> --repo <owner/repo> --json assignees,comments --jq '{assignees, recent_comments: [.comments[-5:][].body]}'
```

Exclude if any check shows an assignee, open/linked PR, timeline cross-reference, or recent "working on it" signal.

## Recommend

Prefer clear, narrow, reproducible issues with maintainer signal and no active owner. Include difficulty, confidence, evidence, false-positive risk, tool limits, and excluded docs count.
