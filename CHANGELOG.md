# CHANGELOG


## v0.1.0 (2026-07-01)

### Bug Fixes

- Correct recipe value ranges to xBloom Studio published specs
  ([`6d069ff`](https://github.com/Janczykkkko/xbloom-ble/commit/6d069ff4ca2ecc5562ab697d0db3ddf5f6d348fe))

grind 1-80, pour temp 40-95C, flow 3.0-3.5 ml/s, rpm 60-120 (0 for center) — the previous bounds
  were placeholder guesses. Marked firm per the specs; tests + README updated.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>

- Write-without-response on ffe1 + real ffe2 notification decoder
  ([`9db4e90`](https://github.com/Janczykkkko/xbloom-ble/commit/9db4e90bc09108eb97756bba78460f1bd2c49c69))

The load never actually worked from the packaged client: it hardcoded a Write Request
  (response=True) on ffe1, which the machine rejects with GATT 'Unlikely Error'. The vendor app only
  ever uses Write Commands (write-without-response) on ffe1 — verified from its HCI capture — so
  switch to response=False.

Also rewrite the notification decoder for the real ffe2 frame format (58 02 07 | TYPE | SUB |
  LEN(u32) | c1 | payload | crc), which is distinct from the 58 01 01 command frames we send. ACKs
  are matched by the echoed TYPE byte; machine state comes from 0x57 status frames (0x1f = armed).
  Validated byte-for-byte against the captured notification stream and confirmed end-to-end on
  hardware: the machine now loads a recipe, arms, and prompts for approval.

Tests updated to the real notification format with golden captured frames.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>

### Continuous Integration

- Add CI, semantic PR-title checks, and automated release to PyPI
  ([`c8931e3`](https://github.com/Janczykkkko/xbloom-ble/commit/c8931e3df31fddec1cee5c95638bce16fdc7815f))

- CI (ruff + pytest on Python 3.10-3.13) on PRs and pushes to main. - PR titles validated as
  Conventional Commits. - python-semantic-release on main: version bump + changelog + tag + GitHub
  Release notes, then Trusted-Publishing to PyPI (gated on RELEASE_ENABLED until the PyPI publisher
  is configured; see docs/RELEASING.md). - Ruff-clean the codebase; PR template, badges,
  CONTRIBUTING + RELEASING docs.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>

### Documentation

- Document the automated PyPI trusted-publishing release flow
  ([#1](https://github.com/Janczykkkko/xbloom-ble/pull/1),
  [`f3849b5`](https://github.com/Janczykkkko/xbloom-ble/commit/f3849b5f1f20812e82be75dc303905ca3edb5acf))

Documents the now-live release pipeline and adds a manual `workflow_dispatch` trigger.

- `docs/RELEASING.md`: reflect the configured Trusted Publisher + `RELEASE_PAT`/`RELEASE_ENABLED`;
  document the squash-merge → semantic-release → PyPI flow and manual re-trigger. -
  `CONTRIBUTING.md`: clarify releases publish via Trusted Publishing (no tokens). - `release.yml`:
  add `workflow_dispatch`.

Merging this is also the first run of the pipeline end-to-end — semantic-release should cut
  **v0.1.0** to PyPI (accumulated `feat`/`fix` on main).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-authored-by: Janczykkkko <Janczykkkko@users.noreply.github.com>

- Link the xBloom recipe ledger (xbloom.lodywgumce.tv)
  ([`4347a72`](https://github.com/Janczykkkko/xbloom-ble/commit/4347a72a8321d5ede2007f97ffd756374c7348ef))

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>

- Note the tool is designed for agentic use
  ([`cc30323`](https://github.com/Janczykkkko/xbloom-ble/commit/cc303237b751dc345a006b53e899e46767dacbff))

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>

- Readme covers cloud sync (intro, library API)
  ([`d3db9ca`](https://github.com/Janczykkkko/xbloom-ble/commit/d3db9ca26ef75b7c0392ac11b3d2b3591452b872))

### Features

- Cloud recipe sync with managed-recipe safety model
  ([`f3233e1`](https://github.com/Janczykkkko/xbloom-ble/commit/f3233e17ad4b9218354aa7e8ef79c024098a667e))

Add 'xbloom cloud' (optional [cloud] extra): push recipes to the xBloom app account via the
  unofficial cloud REST API (login/sync/add-recipe/list/delete/ fetch), ported from
  cryptofishbug/xbloom-recipe-cli (MIT).

Safety: tool-created recipes are named 'AUTO <name>' by default and sync is idempotent
  (update-or-add by name); update/delete/prune only ever touch AUTO recipes — hand-made recipes are
  never modified or removed (enforced + tested). sync_recipe(prefix='') lets a caller manage recipes
  by its own deterministic name instead.

Correct the cloud recipe mapping against real app data: pour pattern spiral/ring -> code 2 (was 3),
  and agitation -> isEnableVibrationAfter (was Before). Add Recipe.effective_ratio for grandWater.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
