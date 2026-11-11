---
name: dev-test
description: Developer testing for a JIRA ticket in any repo - reads the ticket and its acceptance criteria, maps what the branch changed, designs happy, failure, corner-case, regression and backward-compatibility test cases, runs them (UI through the browser, APIs, existing test suites), flags blockers and produces an .xlsx test sheet with evidence. Use for "dev test ABC-123", "test this ticket", "regression test my branch", "write and run test cases for <ticket>", "is this ready for QA". Usage: /dev-test <TICKET-ID> [--env local|sit] [--url <base-url>] [--repo <path>]
---

# dev-test

Test a ticket's change the way a careful developer would before handing it to QA: understand what was asked, work out what actually changed, design cases that try to break it, run them, and leave a sheet anyone can read.

The skill's base directory is shown when it loads. Call it `SKILL_DIR` below; the report builder is `SKILL_DIR/scripts/build_report.py` (Python 3 standard library only, nothing to install).

## Hard rules

1. **Never test against production.** Before any request or navigation, check the target host against `never_test_against` in the config and against anything that looks like a live customer domain. If unsure, ask.
2. **No PASS without evidence.** Every PASS needs a screenshot, a response snippet, a log line or test output in `evidence`. The report builder rejects a PASS with no evidence.
3. **Ask before writes to shared environments** (creating orders, changing store settings, sending anything). Local is fine.
4. **Never trigger real side effects**: payments, SMS, emails, push notifications, calls to third parties. Use sandbox modes or skip and mark NOT RUN with the reason.
5. **Never write secrets** (passwords, tokens, API keys, session cookies) into the report, the results file, screenshots' filenames or JIRA comments.
6. **Stop at the two gates** (end of Phase 1 if the criteria are ambiguous, end of Phase 3 always) and wait for the user.
7. Clean up any test data you created, or list it in the summary if you can't.

## Phase 0 - Set up

1. Parse the arguments. No TICKET-ID → print the usage line and stop.
2. Repo = `--repo`, else the current working directory. Find the default branch (`git symbolic-ref refs/remotes/origin/HEAD`, else `main`, else `master`).
3. Load `<repo>/.claude/dev-test.config.json` if it exists. If not, ask once for the base URL of the app to test (or "API/back-end only") and offer to create the file from `SKILL_DIR/templates/dev-test.config.json`. Environment = `--env`, else config `default_environment`, else `local`. `--url` overrides the base URL.
4. Detect what is available and say it in one short line each, then carry on with what works:
   - **JIRA**: any connected Atlassian MCP tool that reads an issue (names vary: `getJiraIssue`, `fetch_jira_ticket`, …). Else the REST API with env vars `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (`GET /rest/api/3/issue/<KEY>?expand=renderedFields` plus `/comment`). Else ask the user to paste the ticket.
   - **Browser**: Playwright MCP (`browser_*` tools), else Chrome DevTools MCP. Else UI cases become NOT RUN with reason "no browser tool".
   - **Stack**: `composer.json`/`phpunit.xml`, `package.json` scripts, `pytest.ini`/`pyproject.toml`, `go.mod`, etc. Config `test_commands` / `static_checks` win over detection.
5. Report folder = `<repo>/<report_dir or dev-test-reports>/<TICKET-ID>/`, with `screenshots/` inside. Make sure the report folder is in `.gitignore`; if not, tell the user and offer to add it.

## Phase 1 - Understand the ticket

1. Fetch summary, type, status, description, acceptance criteria (field or section), every comment, linked and parent/epic tickets (summary only), attachment names, and any Figma/Confluence/Loom links. Read linked Confluence pages if a tool can.
2. Write the acceptance criteria as numbered, checkable statements `AC-1 … AC-n`. If the ticket has no explicit criteria, derive them from the description and comments and mark them `(derived)`.
3. List assumptions, ambiguities and missing criteria (e.g. "what happens when the value is empty?", "does this apply to existing records?").
4. Show a short summary: what the ticket wants in two or three plain sentences, the AC list, the open questions.
5. **Gate:** if any open question would change an expected result, stop and ask. Otherwise state the assumption you are taking and continue.

## Phase 2 - Map what changed

1. Find the change: the ticket's branch or PR if one can be found (branch name or PR title containing the ticket key), else the current branch. `git diff <default>...HEAD --stat` then the full diff for relevant files. Include uncommitted changes and say so.
2. List changed files, functions, endpoints/routes, UI screens, DB tables or migrations, config keys, feature flags, crons/queues.
3. For each change, find its callers and consumers: grep the repo, and the repos listed in config `related_repos` (or sibling repos in the workspace) for the function, endpoint, table or field names.
4. Build the backward-compatibility risk list (`BC-1 …`). Read `SKILL_DIR/references/test-design.md` § Backward compatibility and check every item against this diff.
5. Note anything that blocks release on its own, before testing (e.g. migration that locks a large live table, removed field still read by another repo, debug output left in).

## Phase 3 - Design the test cases

Read `SKILL_DIR/references/test-design.md` for the full corner-case catalogue, then build the matrix. Every case has:

`id` (TC-001…), `ac` (list of AC ids, or empty for regression/compat), `category`, `priority` (P0 blocks release … P3 cosmetic), `title`, `preconditions`, `test_data`, `steps`, `expected`, and how it will run: `UI`, `API`, `DB`, `Automated` or `Manual`.

Cover every category, or write "N/A - <reason>":

| Category | Minimum |
|---|---|
| Happy path | at least one per AC |
| Failure flow | invalid input, missing/expired session, wrong role, downstream failure, duplicate submit, user-facing error text |
| Corner case | relevant items from the catalogue: empty/null, boundaries, special chars/unicode, timezone, money rounding, large data, concurrency, back/refresh |
| Regression | features that share the changed files, tables or endpoints |
| Backward compatibility | one case per BC risk |
| Security | authorisation, injection/escaping of input, nothing sensitive in responses or logs |

Show the matrix as a table, plus an AC → TC traceability table and any AC with no case.

**Gate:** wait for the user to approve, add or remove cases. Do not run anything before this.

## Phase 4 - Run

Write results into `<report folder>/results.json` as you go (schema: `SKILL_DIR/templates/results.example.json`), so a crash loses nothing.

- **Automated first**: run the repo's test commands and static checks. Compare with the default branch if something fails, so a failure that already existed is reported as pre-existing, not as caused by this change.
- **UI**: navigate, log in, run each step. Log in with credentials from the env vars named in config `login`; if they are not set, open the login page and ask the user to log in themselves, then continue. After each check take a screenshot to `screenshots/<TC-ID>-<n>.png`. Collect console errors and failed (4xx/5xx) network requests for every UI case and record them in `notes`, even when the case passes.
- **API**: `curl` the endpoint with the case's data; record the status code and the relevant part of the body (secrets masked).
- **DB**: read-only queries only, through whatever DB tool is connected. Never run INSERT/UPDATE/DELETE/DDL from this skill.
- Status per case: `PASS`, `FAIL`, `BLOCKED` (could not run because of something else - say what), `NOT RUN` (deliberately skipped - say why). Fill `actual` for every case that ran.
- For every FAIL, add a defect: severity `Blocker` / `Major` / `Minor`, steps to reproduce, expected vs actual, suspected file and line if you can point to it. Do not fix code in this skill; offer to hand the defect to `/fix-bug` or a normal fix afterwards.

## Phase 5 - Verdict and report

1. Verdict:
   - `NOT READY` if there is any Blocker defect, any P0 case FAIL, or any AC with no passing case.
   - `READY WITH RISKS` if there are Major/Minor defects, or BLOCKED/NOT RUN cases on P0/P1, or unresolved BC risks.
   - `READY` otherwise.
2. Build the sheet:
   ```bash
   python3 "SKILL_DIR/scripts/build_report.py" "<report folder>/results.json"
   ```
   It validates the results (fails on PASS without evidence, unknown statuses, a verdict that contradicts the defects) and writes `<KEY>-dev-test-<date>.xlsx`, `test-cases.csv` and `summary.md` next to `results.json`. If it fails validation, fix `results.json` - do not weaken the rule.
3. In chat, show: verdict, counts by status, blockers, the AC coverage table, and the path of the .xlsx (full absolute path).
4. Offer to post `summary.md` as a JIRA comment. Post only after the user says yes, and only the summary (no screenshots or data that might hold customer details).

## Re-runs

`/dev-test <KEY>` on a ticket with an existing `results.json`: offer to re-run only FAIL/BLOCKED cases plus regression cases, keep earlier PASS evidence, and record the run date. The builder keeps one sheet per date.
