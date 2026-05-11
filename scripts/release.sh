#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/release.sh <version> [--no-push] [--no-release]

Build, validate, push, and publish a zotero-pdf2zh-next release.

Examples:
  scripts/release.sh 5.1.0
  scripts/release.sh 5.1.1 --no-release

The script updates the shared plugin/server version, runs validation, commits
the version bump, pushes main, creates the v<version> GitHub release with the
XPI asset, and updates the fixed "release" GitHub release with update.json for
Zotero's Check for Updates flow.
EOF
}

die() {
    echo "release.sh: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

if [[ $# -gt 0 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
    usage
    exit 0
fi

if [[ $# -lt 1 ]]; then
    usage
    exit 2
fi

VERSION="$1"
shift
PUSH=1
PUBLISH_RELEASE=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)
            PUSH=0
            PUBLISH_RELEASE=0
            ;;
        --no-release)
            PUBLISH_RELEASE=0
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
    shift
done

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] ||
    die "version must look like semver, got: $VERSION"

for cmd in git gh node pnpm uv perl; do
    require_command "$cmd"
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BRANCH="$(git branch --show-current)"
[[ "$BRANCH" == "main" ]] || die "release must run from main, current branch: $BRANCH"

git diff --quiet || die "tracked worktree changes exist; commit or stash them first"
git diff --cached --quiet || die "staged changes exist; commit or unstage them first"

TAG="v$VERSION"
if gh release view "$TAG" >/dev/null 2>&1; then
    die "GitHub release already exists: $TAG"
fi

node -e '
const fs = require("fs");
const version = process.argv[1];
const file = "plugin/package.json";
const data = JSON.parse(fs.readFileSync(file, "utf8"));
data.version = version;
fs.writeFileSync(file, JSON.stringify(data, null, 4) + "\n");
' "$VERSION"

VERSION="$VERSION" perl -0pi -e 's/version = "[^"]+"/version = "$ENV{VERSION}"/' server/pyproject.toml
VERSION="$VERSION" perl -0pi -e 's/VERSION = "[^"]+"/VERSION = "$ENV{VERSION}"/' server/server.py
VERSION="$VERSION" perl -0pi -e 's/(当前统一版本:\n\n- `)[^`]+(`)/$1$ENV{VERSION}$2/' README.md

uv --directory server lock
uv run --directory server python -m unittest discover -s tests
pnpm --dir plugin build

node -e '
const fs = require("fs");
const version = process.argv[1];
const manifest = JSON.parse(fs.readFileSync("plugin/build/addon/manifest.json", "utf8"));
const update = JSON.parse(fs.readFileSync("plugin/build/update.json", "utf8"));
const addon = update.addons["zotero-pdf2zh-next@nightwatcher.local"];
if (manifest.version !== version) {
  throw new Error(`manifest version ${manifest.version} != ${version}`);
}
if (!addon || addon.updates[0].version !== version) {
  throw new Error("update.json does not point at the requested version");
}
' "$VERSION"

git add README.md plugin/package.json server/pyproject.toml server/server.py server/uv.lock
if ! git diff --cached --quiet; then
    git commit -m "chore: release $TAG"
fi

COMMIT="$(git rev-parse HEAD)"

if [[ "$PUSH" -eq 1 ]]; then
    git push origin "$BRANCH"
fi

if [[ "$PUBLISH_RELEASE" -eq 1 ]]; then
    NOTES_FILE="$(mktemp)"
    trap 'rm -f "$NOTES_FILE"' EXIT
    cat >"$NOTES_FILE" <<EOF
Release $TAG.

Zotero automatic updates use the fixed release asset:
https://github.com/NightWatcher314/zotero-pdf2zh-next/releases/download/release/update.json
EOF

    gh release create "$TAG" \
        plugin/build/zotero-pdf2zh-next.xpi \
        --target "$COMMIT" \
        --title "$TAG" \
        --notes-file "$NOTES_FILE" \
        --latest

    if gh release view release >/dev/null 2>&1; then
        gh release upload release plugin/build/update.json --clobber
    else
        gh release create release \
            plugin/build/update.json \
            --target "$COMMIT" \
            --title "Zotero update manifest" \
            --notes "Stable update manifest used by Zotero Check for Updates." \
            --latest=false
    fi
fi

echo "Released $TAG at $COMMIT"
