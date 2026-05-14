#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/release.sh <version> [--no-push] [--no-release] [--no-pypi] [--tap-path <path>] [--no-tap]

Build, validate, push, and publish a zotero-pdf2zh-next release.

Examples:
  scripts/release.sh 5.1.0
  scripts/release.sh 5.1.1 --no-release
  scripts/release.sh 5.1.1 --no-pypi
  scripts/release.sh 5.1.1 --tap-path /Users/night/Documents/Codes/homebrew-formula
  scripts/release.sh 5.1.1 --no-tap

The script updates the shared plugin/server version, runs validation, commits
the version bump, pushes main, creates the v<version> GitHub release with the
XPI asset using CHANGELOG.md release notes, publishes the server package to
PyPI, updates the fixed "release" GitHub release with update.json for Zotero's
Check for Updates flow, and updates the Homebrew tap formula.

Before running, add a CHANGELOG.md section like:
  ## v5.1.1 - YYYY-MM-DD

Unless --no-push or --no-tap is set, the script also updates the Homebrew tap
formula after the main repo push/release succeeds. The default tap path is
../homebrew-formula when present, then /Users/night/Documents/Codes/homebrew-formula
when present.

PyPI publishing uses UV_PUBLISH_TOKEN. With direnv, put it in .envrc and run
direnv allow before release. Use --no-pypi to skip upload.
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
UPDATE_TAP=1
PUBLISH_PYPI=1
TAP_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)
            PUSH=0
            PUBLISH_RELEASE=0
            PUBLISH_PYPI=0
            ;;
        --no-release)
            PUBLISH_RELEASE=0
            ;;
        --no-pypi)
            PUBLISH_PYPI=0
            ;;
        --tap-path)
            [[ $# -ge 2 ]] || die "--tap-path requires a path"
            TAP_PATH="$2"
            shift
            ;;
        --no-tap)
            UPDATE_TAP=0
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

if [[ -z "$TAP_PATH" ]]; then
    if [[ -d "$REPO_ROOT/../homebrew-formula/.git" ]]; then
        TAP_PATH="$REPO_ROOT/../homebrew-formula"
    elif [[ -d "/Users/night/Documents/Codes/homebrew-formula/.git" ]]; then
        TAP_PATH="/Users/night/Documents/Codes/homebrew-formula"
    fi
fi

if [[ "$UPDATE_TAP" -eq 1 && "$PUSH" -eq 1 ]]; then
    [[ -n "$TAP_PATH" ]] || die "Homebrew tap path not found; use --tap-path <path> or --no-tap"
    [[ -d "$TAP_PATH/.git" ]] || die "Homebrew tap path is not a git repo: $TAP_PATH"
    [[ -f "$TAP_PATH/Formula/zotero-pdf2zh-next.rb" ]] ||
        die "Homebrew formula not found: $TAP_PATH/Formula/zotero-pdf2zh-next.rb"
    [[ -z "$(git -C "$TAP_PATH" status --porcelain)" ]] ||
        die "Homebrew tap worktree is dirty; commit or stash changes first"
    require_command curl
    require_command shasum
    require_command ruby
fi

if [[ "$PUBLISH_PYPI" -eq 1 && -z "${UV_PUBLISH_TOKEN:-}" ]]; then
    die "UV_PUBLISH_TOKEN is required for PyPI publishing; set it in .envrc and run direnv allow, or use --no-pypi"
fi

BRANCH="$(git branch --show-current)"
[[ "$BRANCH" == "main" ]] || die "release must run from main, current branch: $BRANCH"

git diff --quiet || die "tracked worktree changes exist; commit or stash them first"
git diff --cached --quiet || die "staged changes exist; commit or unstage them first"

TAG="v$VERSION"
if gh release view "$TAG" >/dev/null 2>&1; then
    die "GitHub release already exists: $TAG"
fi

CHANGELOG_SECTION="$(awk -v tag="$TAG" '
    $0 ~ "^## " tag "([[:space:]]|-|$)" {
        found = 1
        print
        next
    }
    found && /^## / {
        exit
    }
    found {
        print
    }
' CHANGELOG.md)"
[[ -n "$(printf '%s' "$CHANGELOG_SECTION" | tr -d '[:space:]')" ]] ||
    die "CHANGELOG.md must contain a section starting with: ## $TAG"

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
uv build server --out-dir server/dist --clear --no-sources
PNPM_CONFIG_BLOCK_EXOTIC_SUBDEPS=false \
    PNPM_CONFIG_DANGEROUSLY_ALLOW_ALL_BUILDS=true \
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
    printf '%s\n\n' "$CHANGELOG_SECTION" >"$NOTES_FILE"
    cat >>"$NOTES_FILE" <<EOF
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

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    uv publish server/dist/*
fi

if [[ "$UPDATE_TAP" -eq 1 && "$PUSH" -eq 1 ]]; then
    FORMULA_REL="Formula/zotero-pdf2zh-next.rb"
    FORMULA="$TAP_PATH/$FORMULA_REL"
    TARBALL_URL="https://github.com/NightWatcher314/zotero-pdf2zh-next/archive/$COMMIT.tar.gz"
    SHA256="$(curl -LfsS "$TARBALL_URL" | shasum -a 256 | awk '{print $1}')"
    [[ -n "$SHA256" ]] || die "failed to compute sha256 for $TARBALL_URL"

    URL="$TARBALL_URL" VERSION="$VERSION" SHA256="$SHA256" perl -0pi -e '
s/url "[^"]+"/url "$ENV{URL}"/;
s/version "[^"]+"/version "$ENV{VERSION}"/;
s/sha256 "[^"]+"/sha256 "$ENV{SHA256}"/;
' "$FORMULA"

    git -C "$TAP_PATH" diff -- "$FORMULA_REL"
    ruby -c "$FORMULA"

    git -C "$TAP_PATH" add "$FORMULA_REL"
    if ! git -C "$TAP_PATH" diff --cached --quiet; then
        git -C "$TAP_PATH" commit -m "chore: update zotero-pdf2zh-next to $TAG"
        git -C "$TAP_PATH" push origin HEAD
    fi

    if command -v brew >/dev/null 2>&1; then
        BREW_TAP_PATH="$(brew --repository nightwatcher314/formula 2>/dev/null || true)"
        if [[ -n "$BREW_TAP_PATH" && -d "$BREW_TAP_PATH/.git" ]]; then
            git -C "$BREW_TAP_PATH" pull --ff-only
        fi
        HOMEBREW_NO_AUTO_UPDATE=1 brew readall nightwatcher314/formula
        HOMEBREW_NO_AUTO_UPDATE=1 brew install --formula --dry-run nightwatcher314/formula/zotero-pdf2zh-next
    else
        echo "Skipping Homebrew validation because brew is not installed" >&2
    fi
fi

echo "Released $TAG at $COMMIT"
