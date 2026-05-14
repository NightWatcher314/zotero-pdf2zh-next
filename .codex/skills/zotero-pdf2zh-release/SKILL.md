---
name: zotero-pdf2zh-release
description: Synchronize zotero-pdf2zh-next release versions across the Zotero plugin, Python server, PyPI package, README, and Homebrew formula. Use when preparing a new zotero-pdf2zh-next release, bumping the shared version number, validating builds, publishing PyPI artifacts locally, or updating the NightWatcher314/homebrew-formula tap after pushing the main repo.
---

# Zotero PDF2ZH Release

Keep the plugin and server on the same version.

Update these files together:
- `plugin/package.json`
- `server/pyproject.toml`
- `server/server.py`
- `README.md` if it shows the current unified version
- `CHANGELOG.md` with a `## v<version> - YYYY-MM-DD` section before release

Use this workflow:

1. Inspect current release state.
   Run `rg -n '\"version\":|version = \"|VERSION = \"' plugin/package.json server/pyproject.toml server/server.py README.md`.

2. Bump the shared version in the main repo.
   Keep plugin and server identical.

   Before running `scripts/release.sh`, add the release notes to `CHANGELOG.md`.
   The script will fail if `CHANGELOG.md` does not contain a matching `## v<version>` section, and GitHub release notes are generated from that section.

3. Validate the main repo.
   Run `pnpm --dir plugin build`.
   Run `uv run --directory server python -m unittest discover -s tests`.
   Run `uv build server --out-dir server/dist --clear --no-sources`.

4. Commit and push the main repo first.
   The Homebrew formula currently points to a source tarball for a pushed main-repo commit, so do not update the formula before the main repo commit exists on GitHub.

5. Publish to PyPI.
   The server package name is `zotero-pdf2zh-next`.
   Unified release publishing uses `uv publish server/dist/*`.
   Keep the token in `.envrc` as `UV_PUBLISH_TOKEN`; `.envrc` must stay ignored by git.
   `scripts/release.sh <version>` publishes XPI, PyPI, and Homebrew by default. Use `--no-pypi` only when intentionally skipping PyPI upload.

6. Sync the Homebrew tap if the release version or source commit changed.
   Update `NightWatcher314/homebrew-formula` file `Formula/zotero-pdf2zh-next.rb`.
   Keep the formula aligned with the server CLI entrypoint `zotero-pdf2zh-next`.
   Keep `python@3.13` pinned in the formula; do not switch back to unversioned `python` unless the `pdf2zh_next -> pydantic-core` build chain is confirmed to work on newer Python.
   Recompute the tarball SHA from the pushed main-repo commit before editing the formula.

7. Validate the Homebrew tap after editing the formula.
   Run `HOMEBREW_NO_AUTO_UPDATE=1 brew readall nightwatcher314/formula`.
   Run `HOMEBREW_NO_AUTO_UPDATE=1 brew install --formula --dry-run nightwatcher314/formula/zotero-pdf2zh-next`.

8. Commit and push the tap repo separately.

When updating distribution-related release details, also review:
- `server/uv.lock`
- `server/Dockerfile`
- `compose.yaml`
- `CLAUDE.md`

Preferred final output:
- main repo commit hash
- tap repo commit hash if changed
- validation commands run
- PyPI publish result, or note if `--no-pypi` was used
- any remaining manual step, if one exists
