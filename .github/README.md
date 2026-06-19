# CI workflow (disabled)

`ci.yml.disabled` is the GitHub Actions workflow (ruff + format check + mypy + pytest). It is
parked here instead of `.github/workflows/` because the token used for the initial push lacked
the `workflow` OAuth scope.

To enable CI, either:
- rename it back to `.github/workflows/ci.yml` via the GitHub web UI (the web editor has workflow
  permission), or
- locally run `gh auth refresh -s workflow -h github.com`, then
  `git mv .github/ci.yml.disabled .github/workflows/ci.yml && git commit && git push`.
