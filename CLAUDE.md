# Claude Instructions

Follow the repository guidance in `AGENTS.md`. It is the source of truth for
what may be modified, what must not be edited manually, testing commands,
generation rules, and GitHub Actions security requirements.

Critical points:

- Do not manually edit `vertesia_client/openapi/` or `spec/`.
- Keep secrets out of tracked files; `.env` is local only.
- Use `python3 -m unittest discover -s test` as the primary test command.
- For workflow edits, keep GitHub Actions pinned by full commit SHA with an
  exact tag comment, then run this when available:

```sh
uvx zizmor@1.24.1 --no-exit-codes --persona=auditor .github
```
