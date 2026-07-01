# Releasing

Releases are **automated** with
[python-semantic-release](https://python-semantic-release.readthedocs.io/) from the
[Conventional Commits](https://www.conventionalcommits.org/) history.

## How a release happens

1. Work lands on `main` **via pull requests** (branch-protected; direct pushes are blocked).
   Each PR title is a Conventional Commit and is validated by the **PR Title** workflow.
2. On every push to `main`, the **Release** workflow runs `semantic-release`:
   - inspects commits since the last tag,
   - decides the bump — `fix:`/`perf:` → patch, `feat:` → minor, `BREAKING CHANGE:` → major,
   - updates the version (`pyproject.toml` + `xbloom_ble/__init__.py`) and `CHANGELOG.md`,
   - commits `chore(release): vX.Y.Z`, tags `vX.Y.Z`, and creates a **GitHub Release** with notes,
   - builds the sdist+wheel and **publishes to PyPI** via Trusted Publishing (OIDC — no token).

No manual version bumps or tags. If a push contains only `docs:`/`chore:`/`ci:` commits, no release is cut.

## One-time setup (required before the first release)

These need account access and are **not** done by CI:

1. **PyPI Trusted Publisher.** On <https://pypi.org> → the `xbloom-ble` project (create it if new via a
   first manual upload, or use PyPI's "pending publisher") → **Publishing** → add a GitHub publisher:
   - Owner: `Janczykkkko` · Repository: `xbloom-ble`
   - Workflow filename: `release.yml`
   - Environment: `pypi`
2. **Enable the workflow.** Set repo variable **`RELEASE_ENABLED=true`**
   (Settings → Secrets and variables → Actions → Variables). The Release job is skipped until then.
3. **(If `main` requires PRs)** add a **`RELEASE_PAT`** secret — a fine-grained PAT with
   `contents: read/write` (and `pull-requests: write`) on this repo — so the `chore(release)` commit
   and tag can be pushed past branch protection. Without it the job uses `GITHUB_TOKEN`, which cannot
   push to a protected branch.

## Commit / PR title conventions

`type(optional-scope): summary` — types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, `revert`. Breaking change: append `!` (e.g. `feat!:`) or add a
`BREAKING CHANGE:` footer.
