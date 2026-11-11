# dev-test

A Claude Code skill that does developer testing for a JIRA ticket in any repo.

`/dev-test ABC-123`:

1. Reads the ticket, its comments and linked tickets, and turns the acceptance criteria into a numbered list. It stops and asks if something is unclear.
2. Diffs your branch against the default branch and finds who else uses the changed code, including other repos, so it can list backward-compatibility risks.
3. Designs test cases: happy path, failure flows, corner cases, regression, backward compatibility and security. **It waits for your approval before running anything.**
4. Runs them: your repo's own test suite, the UI through the browser (with screenshots), API calls and read-only DB checks.
5. Gives a verdict (READY, READY WITH RISKS or NOT READY) and lists the blockers.
6. Writes a spreadsheet with Summary, Test Cases, Defects, AC Coverage and Backward Compatibility tabs, plus a CSV and a short `summary.md` you can post to JIRA.

It never tests against production, never marks a case PASS without evidence, and asks before writing to shared environments.

## Install

```bash
git clone <this-repo> ~/dev-test-skill
cd ~/dev-test-skill
./install.sh            # links into ~/.claude/skills/dev-test, so `git pull` updates it
```

Restart Claude Code. Other options: `./install.sh --copy`, `./install.sh --project /path/to/repo` (one repo only), `./install.sh --uninstall`.

## Prerequisites

| Need | For | If missing |
|---|---|---|
| `python3` (3.8+, no packages) | building the .xlsx | no spreadsheet; results stay in `results.json` |
| An Atlassian/JIRA MCP connected to Claude Code, **or** env vars `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | reading the ticket | you paste the ticket in |
| Playwright MCP or Chrome DevTools MCP | UI testing | UI cases are reported as NOT RUN |
| A DB MCP (optional) | read-only data checks | DB checks are skipped |

## Per-repo setup (optional, recommended)

Copy [`skills/dev-test/templates/dev-test.config.json`](skills/dev-test/templates/dev-test.config.json) to `<your-repo>/.claude/dev-test.config.json` and fill in:

- `environments`: base URL per environment (`local`, `sit`, …)
- `never_test_against`: hosts the skill must refuse, such as your live domains
- `login`: login path and the **names** of the env vars that hold test credentials. Never put the credentials themselves in the file. If they aren't set, the skill opens the login page and lets you log in yourself.
- `test_commands` / `static_checks`: e.g. `vendor/bin/phpunit`, `npm test`, `vendor/bin/phpstan analyse`
- `related_repos`: sibling repos to search for callers of changed code

Without the file, the skill asks for the base URL and detects the test commands.

Reports go to `<repo>/dev-test-reports/<TICKET>/`. Add that folder to the repo's `.gitignore`.

## Usage

```
/dev-test ABC-123
/dev-test ABC-123 --env sit
/dev-test ABC-123 --url http://localhost:8080
/dev-test ABC-123 --repo ../other-repo
```

Run it again on the same ticket to re-test only the FAIL and BLOCKED cases, plus regression.

## Layout

```
skills/dev-test/
  SKILL.md                      the workflow Claude follows
  references/test-design.md     corner-case and backward-compatibility catalogue
  templates/dev-test.config.json
  templates/results.example.json  schema for results.json
  scripts/build_report.py       results.json -> .xlsx / .csv / summary.md (stdlib only)
tests/                          python3 -m unittest discover -s tests
```

The `skills/<name>/` layout is the one Claude Code plugins use, so turning this into a plugin later only needs a `.claude-plugin/plugin.json` manifest and a marketplace entry. The skill itself doesn't change.

## Changing the skill

- Edit `SKILL.md` for the workflow and `references/test-design.md` for what gets tested.
- If you change the `results.json` shape, update `results.example.json` and `build_report.py` together, then run the tests.
