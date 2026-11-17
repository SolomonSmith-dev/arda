# CI access to the Claude API without a stored key

`.github/workflows/code-review.yml` gets a fresh Anthropic access token for each run through workload identity federation (WIF): GitHub issues the job a short-lived OIDC token, Anthropic exchanges it for an access token that lasts about 10 minutes, and the review uses that. Nothing secret is stored in the repository. The job only gets a token when its OIDC claims match the federation rule below.

## One-time setup (about 10 minutes)

### 1. Anthropic Console

Console > **Workload identity** (`platform.claude.com/settings/workload-identity-federation`) > **Connect workload** > **GitHub Actions**. Needs the admin or owner role.

| Field | Value |
|---|---|
| Issuer | `https://token.actions.githubusercontent.com` (prefilled) |
| Audience | `https://api.anthropic.com` |
| Subject | `repo:SolomonSmith-dev/arda:pull_request` (exact match, no `*`) |
| Claims | `repository` = `SolomonSmith-dev/arda`, `event_name` = `pull_request` |
| Service account | new, e.g. `arda-ci` |
| Workspace | one workspace that has a spend limit |
| Scope (Advanced rule options) | `workspace:inference` |
| Token lifetime | 600 seconds |

Optional, tighter: a condition that only this workflow file may use the rule, `claims.workflow_ref.startsWith("SolomonSmith-dev/arda/.github/workflows/code-review.yml@")`. Check the expression with the wizard's test before saving.

Note the rule ID (`fdrl_...`) and the organization ID (Console > Settings > Organization).

### 2. GitHub repository variables

Settings > Secrets and variables > Actions > **Variables** (not Secrets; these are IDs):

| Variable | Value |
|---|---|
| `ANTHROPIC_FEDERATION_RULE_ID` | `fdrl_...` |
| `ANTHROPIC_ORGANIZATION_ID` | the organization UUID |
| `ANTHROPIC_WORKSPACE_ID` | only if the rule covers more than one workspace |

### 3. Check, then remove the old key

Re-run the `review` job on an open pull request. A green run with review comments means it works. Then delete the `ANTHROPIC_API_KEY` repository secret and revoke that key in the Console.

## What runs where

- **Same-repository pull requests:** reviewed. Anyone who can push a branch here can run this workflow, as with the old secret.
- **Fork pull requests:** skipped. GitHub gives them no OIDC token on `pull_request`, and their subject would not match anyway.
- **Dependabot pull requests:** skipped. Lint and tests in `ci.yml` still run.

## When it fails

- `Set repository variables ...`: step 2 is missing.
- `Token exchange failed with HTTP 401`: the claims did not match the rule. The rule's authentication history in the Console shows which matcher denied it.
- `HTTP 400`: a malformed rule, organization or workspace ID.
- 401s from the review step itself after a long run: the token lasts about 10 minutes, and the review has a 15 minute timeout. Reviews here take under a minute.
