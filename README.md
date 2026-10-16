# Power Platform ALM Pipeline

Automated CI/CD for Power Platform apps/solutions: pack → Solution Checker →
dependency check → Claude code review, then gated promotion Dev → Staging →
Production.

## How it's organized

```
.github/workflows/
  _reusable-ci.yml   # shared CI gate — called by every per-app workflow
  _reusable-cd.yml   # shared deploy pipeline — Staging then Production
  process-hub.yml    # sample per-app workflow — copy this for each new app
solutions/
  process-hub/src/   # unpacked solution source (pac solution unpack output)
scripts/
  dependency-check.ps1   # catches missing child-flow refs, hard-coded URLs, etc.
  claude_review.py       # sends canvas app / flow source to Claude for review
```

Adding a new app later is three steps: unpack its solution into
`solutions/<app-name>/src`, copy `process-hub.yml` to `<app-name>.yml` and
change the three `with:` values, done. The CI/CD logic itself never needs
to change.

## One-time setup

### 1. Create a service principal (once per tenant)

If you have PAC CLI locally:

```bash
pac auth create --url https://<your-dev-env>.crm.dynamics.com
pac admin create-service-principal --environment https://<your-dev-env>.crm.dynamics.com
```

This prints a Tenant ID, App ID (client ID), and Client Secret, and grants
the app user access to that environment. Repeat the environment-grant step
(not the app creation) for Staging and Production — same App ID, access
added to each environment:

```bash
pac admin application-user create --application-id <app-id> --environment https://<staging-env>.crm.dynamics.com
pac admin application-user create --application-id <app-id> --environment https://<production-env>.crm.dynamics.com
```

If you don't have PAC CLI or admin rights, send these exact commands to
whoever administers your Power Platform tenant.

### 2. Assign the right security role

In each environment's Power Platform admin center, give the application
user a security role with enough privilege to import solutions and run
Solution Checker (System Administrator is simplest to start; narrow it
later if your org requires least-privilege).

### 3. Add repo secrets

In GitHub: Settings → Secrets and variables → Actions → New repository
secret. Add:

| Secret | Value |
|---|---|
| `POWER_PLATFORM_TENANT_ID` | from step 1 |
| `POWER_PLATFORM_CLIENT_ID` | from step 1 |
| `POWER_PLATFORM_CLIENT_SECRET` | from step 1 |
| `ANTHROPIC_API_KEY` | an Anthropic API key (console.anthropic.com) for the Claude review step |

### 4. Configure the production approval gate

Settings → Environments → New environment → name it exactly `production` →
add yourself (or whoever signs off) as a required reviewer. This makes
`_reusable-cd.yml`'s production job pause for manual approval every time.

### 5. Get your first app's source into the repo

```bash
pac auth create --url https://<your-dev-env>.crm.dynamics.com
pac solution unpack --zipfile MySolution.zip --folder solutions/process-hub/src --packagetype Unmanaged
```

(Export the unmanaged solution from Dev first via `pac solution export` or
Studio's Solutions area if you don't already have the zip.)

### 6. Edit the per-app workflow

Open `.github/workflows/process-hub.yml`, replace the three placeholder
environment URLs, rename the file if this isn't actually Process Hub.

## What happens on a pull request

1. Pack the solution from source.
2. Run Solution Checker — fails the build on high/critical findings.
3. Run the dependency-check script — catches missing child-flow references,
   undocumented environment variables, hard-coded environment URLs.
4. Run the Claude review — formula quality, naming, dead controls,
   structural issues. Writes `out/claude-review.md`, uploaded as a build
   artifact (wire up a PR-comment step later if you want it inline on the
   PR instead of just an artifact).
5. On merge to `main`, the managed solution is imported to Staging
   automatically, then held for approval before Production.

## Known limitations / next steps to harden this further

- **Solution Checker requires `windows-latest` runners** — already set, but
  worth knowing if you're watching Action minutes.
- The dependency-check script is a heuristic (regex/XML pattern matching),
  not a guarantee. The only fully reliable dependency check is actually
  importing into a disposable "canary" environment first — worth adding as
  an extra job once you have a spare environment to burn for it.
- The Claude review step currently only *warns* on high-severity findings
  (doesn't fail the build) — flip the commented `sys.exit(1)` in
  `claude_review.py` once you've watched it run for a few cycles and trust
  its judgment.
- No PR-comment integration yet (findings land in the build artifact only).
  Easy to add with `actions/github-script` if you want them inline on PRs.
