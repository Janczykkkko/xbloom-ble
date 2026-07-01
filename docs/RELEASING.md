# Releasing

Releases are **fully automated** — no manual version bumps, tags, or uploads.
[python-semantic-release](https://python-semantic-release.readthedocs.io/) reads the
[Conventional Commits](https://www.conventionalcommits.org/) on `main` and, when a release is
warranted, ships it to **PyPI** via **Trusted Publishing** (OIDC — no API tokens or passwords).

## How a release happens

1. Work lands on `main` **via pull requests only** (branch-protected; direct pushes blocked,
   squash-merge only). The PR **title** is a Conventional Commit, validated by the **PR Title**
   workflow, and becomes the squash commit that drives the release.
2. On every push to `main`, the **Release** workflow runs `semantic-release`, which:
   - inspects commits since the last tag and picks the bump —
     `fix:`/`perf:` → **patch**, `feat:` → **minor**, `BREAKING CHANGE:`/`!` → **major**;
   - updates the version (`pyproject.toml` + `xbloom_ble/__init__.py`) and `CHANGELOG.md`;
   - commits `chore(release): vX.Y.Z`, tags it, and creates a **GitHub Release with notes**;
   - builds the sdist + wheel and **publishes to PyPI via Trusted Publishing**.

If a batch of commits contains only `docs:`/`chore:`/`ci:`/`test:` changes, **no release is cut**.

## Cutting a release

Just **merge a PR to `main`**. If it (or anything unreleased on `main`) contains a `feat:` or
`fix:`, a new version publishes automatically within a minute or two — watch the **Actions → Release**
run and the [PyPI project](https://pypi.org/project/xbloom-ble/).

You can also trigger the Release workflow **manually** from **Actions → Release → Run workflow**
(`workflow_dispatch`) — handy to re-run after a transient PyPI hiccup.

## Configuration (already set up for this repo)

For reference / forks — this is the one-time, account-level setup CI can't do itself. **All of it is
already configured for `Janczykkkko/xbloom-ble`:**

1. **PyPI Trusted Publisher** — on <https://pypi.org>, a GitHub publisher for project `xbloom-ble`:
   owner `Janczykkkko`, repo `xbloom-ble`, workflow `release.yml` (environment `pypi`, or *Any*).
   No PyPI token is ever stored.
2. **`RELEASE_ENABLED=true`** repo *variable* — the Release job is skipped unless this is set (so the
   pipeline can exist before PyPI is wired up, without red runs).
3. **`RELEASE_PAT`** repo *secret* — a fine-grained PAT (`contents: read/write`, `pull-requests:
   read/write`, scoped to this repo) so semantic-release's `chore(release)` commit + tag can be pushed
   past branch protection. Without it the job falls back to `GITHUB_TOKEN`, which cannot push to a
   protected branch.

## Commit / PR title conventions

`type(optional-scope): summary` — types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, `revert`. Breaking change: append `!` (e.g. `feat!:`) or add a
`BREAKING CHANGE:` footer. Only `feat`/`fix`/`perf` (and breaking changes) cut a release.
