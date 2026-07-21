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
the version bump, pushes main, publishes the server package to PyPI, creates
the v<version> GitHub release with the XPI asset using CHANGELOG.md release
notes, updates the fixed "release" GitHub release with update.json for Zotero's
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
PYPI_TOKEN="${UV_PUBLISH_TOKEN:-}"
unset UV_PUBLISH_TOKEN

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

for cmd in git gh node npx uv perl curl; do
    require_command "$cmd"
done
PNPM=(npx --yes pnpm@10.34.5)

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

BRANCH="$(git branch --show-current)"
[[ "$BRANCH" == "main" ]] || die "release must run from main, current branch: $BRANCH"

git diff --quiet || die "tracked worktree changes exist; commit or stash them first"
git diff --cached --quiet || die "staged changes exist; commit or unstage them first"

TAG="v$VERSION"
PYPI_PACKAGE="zotero-pdf2zh-next"
PYPI_VERSION_URL="https://pypi.org/pypi/$PYPI_PACKAGE/$VERSION/json"
PYPI_STATUS=""

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    if ! PYPI_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$PYPI_VERSION_URL")"; then
        die "failed to query PyPI: $PYPI_VERSION_URL"
    fi
    case "$PYPI_STATUS" in
        200)
            echo "PyPI release already exists; upload will be skipped: $PYPI_PACKAGE==$VERSION"
            ;;
        404)
            [[ -n "$PYPI_TOKEN" ]] ||
                die "UV_PUBLISH_TOKEN is required for PyPI publishing; set it in .envrc and run direnv allow, or use --no-pypi"
            ;;
        *)
            die "unexpected PyPI response for $PYPI_PACKAGE==$VERSION: HTTP $PYPI_STATUS"
            ;;
    esac
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
node -e '
const fs = require("fs");
const version = process.argv[1];
function replaceExactlyOnce(file, pattern, replacement, label) {
  const text = fs.readFileSync(file, "utf8");
  const matches = [...text.matchAll(pattern)];
  if (matches.length !== 1) {
    throw new Error(`expected one ${label} marker, found ${matches.length}`);
  }
  fs.writeFileSync(file, text.replace(pattern, replacement));
}

replaceExactlyOnce(
  "README.md",
  /(<!-- release-version --> `)[^`]+(`)/g,
  `$1${version}$2`,
  "README release-version",
);
replaceExactlyOnce(
  "server/uv.lock",
  /(\[\[package\]\]\nname = "zotero-pdf2zh-next"\nversion = ")[^"]+(")/g,
  `$1${version}$2`,
  "server lock version",
);
' "$VERSION"

UV_LOCK_INDEX="$(awk -F '"' '/^source = \{ registry = / { print $2; exit }' server/uv.lock)"
[[ -n "$UV_LOCK_INDEX" ]] || die "server/uv.lock does not contain a registry source"
UV_DEFAULT_INDEX="$UV_LOCK_INDEX" uv --directory server lock --locked
UV_DEFAULT_INDEX="$UV_LOCK_INDEX" \
    uv run --directory server --locked python -m unittest discover -s tests
uv build server --out-dir server/dist --clear --no-sources
CI=true "${PNPM[@]}" --dir plugin install --frozen-lockfile
"${PNPM[@]}" --dir plugin lint:check
"${PNPM[@]}" --dir plugin build

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

GITHUB_RELEASE_EXISTS=0
if [[ "$PUBLISH_RELEASE" -eq 1 ]] && gh release view "$TAG" >/dev/null 2>&1; then
    RELEASE_COMMIT="$(gh api "repos/{owner}/{repo}/commits/$TAG" --jq .sha)"
    [[ "$RELEASE_COMMIT" == "$COMMIT" ]] ||
        die "GitHub release $TAG points to $RELEASE_COMMIT, expected $COMMIT"
    GITHUB_RELEASE_EXISTS=1
fi

if [[ "$PUSH" -eq 1 ]]; then
    git push origin "$BRANCH"
fi

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    if [[ "$PYPI_STATUS" == "404" ]]; then
        UV_PUBLISH_TOKEN="$PYPI_TOKEN" uv publish server/dist/*
    fi

    PYPI_VERIFIED=0
    for _ in {1..6}; do
        if curl -fsS -o /dev/null "$PYPI_VERSION_URL"; then
            PYPI_VERIFIED=1
            break
        fi
        sleep 5
    done
    [[ "$PYPI_VERIFIED" -eq 1 ]] ||
        die "PyPI did not expose $PYPI_PACKAGE==$VERSION after publishing"
fi

if [[ "$PUBLISH_RELEASE" -eq 1 ]]; then
    NOTES_FILE="$(mktemp)"
    trap 'rm -f "$NOTES_FILE"' EXIT
    printf '%s\n\n' "$CHANGELOG_SECTION" >"$NOTES_FILE"
    cat >>"$NOTES_FILE" <<EOF
Zotero automatic updates use the fixed release asset:
https://github.com/NightWatcher314/zotero-pdf2zh-next/releases/download/release/update.json
EOF

    if [[ "$GITHUB_RELEASE_EXISTS" -eq 1 ]]; then
        echo "Reusing existing GitHub release: $TAG"
        gh release upload "$TAG" plugin/build/zotero-pdf2zh-next.xpi --clobber
    else
        gh release create "$TAG" \
            plugin/build/zotero-pdf2zh-next.xpi \
            --target "$COMMIT" \
            --title "$TAG" \
            --notes-file "$NOTES_FILE" \
            --latest
    fi

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
    fi
    git -C "$TAP_PATH" push origin HEAD

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
