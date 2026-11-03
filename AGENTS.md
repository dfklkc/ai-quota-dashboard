# Project working instructions

- Keep this project lightweight. Prefer removing redundant polling, repeated reads,
  inaccessible legacy controls and disabled-source work over adding new features.
  Preserve the user's enabled sources, current-session semantics and display preferences.
- Keep missing, failed, expired or switched-account data explicit. Never invent a
  full quota, a zero balance, a current identity or a successful synchronization.
- Run checks appropriate to each change. Runtime changes require Python regressions,
  relevant macOS checks, a reproducible build and release-package verification.
- Check publishable content before every push and release. Run
  `python3 scripts/check_public_content.py --history` before changing repository
  visibility, and at least `python3 scripts/check_public_content.py` before publishing
  later updates. Review any newly approved binary asset separately.
- Never commit or upload local credentials, account configuration, browser sessions,
  API keys, quota caches, machine-specific paths or backups. Keep examples anonymous;
  public source, documentation and clean installers are the publication scope.
- After a verified optimization, commit and push the change to the existing GitHub
  repository. For app/runtime changes, update the version and publish a tested,
  downloadable Release with matching checksums. Documentation-only changes need
  a commit and push, but do not require rebuilding unchanged installers.
- Preserve the established GitHub repository and its public visibility. Do not
  force-push, rewrite history, expand access or change the user's accounts as an
  incidental optimization. Follow explicit user instructions if the workflow changes.
