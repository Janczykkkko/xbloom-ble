<!--
PR title MUST be a Conventional Commit — it becomes the squash-merge commit that
drives semantic-release. Examples:
  feat: add scale-tare command
  fix: correct pour pattern mapping
  docs: clarify the load-only safety model
Use `feat!:` or a `BREAKING CHANGE:` footer for a breaking change.
-->

## What & why

<!-- What does this change and why? -->

## Checklist

- [ ] PR title is a valid Conventional Commit (`feat:` / `fix:` / `docs:` / …)
- [ ] `ruff check .` passes
- [ ] `pytest -q` passes
- [ ] Docs updated if behaviour/protocol changed
- [ ] **Safety preserved:** no new code path emits a brew-start opcode (`0x42`/`0x46`) on the default load path
- [ ] No personal data (MAC addresses, serials, names, private paths)
