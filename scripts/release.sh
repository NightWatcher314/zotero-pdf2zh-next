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
Check for Updates flow, and publishes the Homebrew formula through a bottle PR.

Before running, add a CHANGELOG.md section like:
  ## v5.1.1 - YYYY-MM-DD

Unless --no-push or --no-tap is set, the script also updates the Homebrew tap
formula after the main repo push/release succeeds. The default tap path is
../homebrew-formula when present, then /Users/night/Documents/Codes/homebrew-formula
when present.

PyPI publishing uses UV_PUBLISH_TOKEN when available. Otherwise the script
dispatches the trusted-publishing workflow. Use --no-pypi to skip upload.
EOF
}

die() {
    echo "release.sh: $*" >&2
    exit 1
}

TEMP_PATHS=()
cleanup() {
    local path
    for path in "${TEMP_PATHS[@]}"; do
        if [[ -n "$path" && -e "$path" ]]; then
            rm -rf "$path"
        fi
    done
}
trap cleanup EXIT

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
    [[ "$(git -C "$TAP_PATH" branch --show-current)" == "main" ]] ||
        die "Homebrew tap must be on main: $TAP_PATH"
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
GITHUB_REPO="NightWatcher314/zotero-pdf2zh-next"
PYPI_PACKAGE="zotero-pdf2zh-next"
PYPI_VERSION_URL="https://pypi.org/pypi/$PYPI_PACKAGE/$VERSION/json"
PYPI_CHECK_URL="https://pypi.org/simple/$PYPI_PACKAGE/"
PYPI_STATUS=""
PYPI_COMPLETE=0
PUBLISH_PYPI_VIA_GITHUB=0

pypi_release_complete() {
    local response
    response="$(curl -fsS "$PYPI_VERSION_URL")" || return 1
    printf '%s' "$response" | node -e '
const fs = require("fs");
const version = process.argv[1];
const data = JSON.parse(fs.readFileSync(0, "utf8"));
const files = new Set(data.urls.map((item) => item.filename));
const expected = [
  `zotero_pdf2zh_next-${version}-py3-none-any.whl`,
  `zotero_pdf2zh_next-${version}.tar.gz`,
];
const missing = expected.filter((filename) => !files.has(filename));
if (missing.length > 0) {
  console.error(`PyPI release is missing: ${missing.join(", ")}`);
  process.exit(1);
}
' "$VERSION"
}

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    if ! PYPI_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$PYPI_VERSION_URL")"; then
        die "failed to query PyPI: $PYPI_VERSION_URL"
    fi
    case "$PYPI_STATUS" in
        200)
            if pypi_release_complete; then
                PYPI_COMPLETE=1
                echo "PyPI release is complete; upload will be skipped: $PYPI_PACKAGE==$VERSION"
            else
                if [[ -z "$PYPI_TOKEN" ]]; then
                    [[ "$PUSH" -eq 1 ]] ||
                        die "PyPI release is incomplete; publishing without a token requires push access"
                    PUBLISH_PYPI_VIA_GITHUB=1
                fi
            fi
            ;;
        404)
            if [[ -z "$PYPI_TOKEN" ]]; then
                [[ "$PUSH" -eq 1 ]] ||
                    die "PyPI publishing without UV_PUBLISH_TOKEN requires push access"
                PUBLISH_PYPI_VIA_GITHUB=1
            fi
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
if [[ "$PUBLISH_RELEASE" -eq 1 || "$PUBLISH_PYPI_VIA_GITHUB" -eq 1 ]]; then
    if ! REMOTE_TAG_REFS="$(git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}")"; then
        die "failed to query remote GitHub tag: $TAG"
    fi
    REMOTE_TAG_COMMIT="$(printf '%s\n' "$REMOTE_TAG_REFS" | awk '
        $2 ~ /\^\{\}$/ { peeled = $1 }
        $2 !~ /\^\{\}$/ { direct = $1 }
        END { print peeled ? peeled : direct }
    ')"
    if [[ -n "$REMOTE_TAG_COMMIT" ]]; then
        [[ "$REMOTE_TAG_COMMIT" == "$COMMIT" ]] ||
            die "GitHub tag $TAG points to $REMOTE_TAG_COMMIT, expected $COMMIT"
    fi
    if [[ "$PUBLISH_RELEASE" -eq 1 ]] &&
        gh release view "$TAG" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
        [[ -n "${REMOTE_TAG_COMMIT:-}" ]] || die "GitHub release exists without a resolvable tag: $TAG"
        GITHUB_RELEASE_EXISTS=1
    fi
fi

if [[ "$PUSH" -eq 1 ]]; then
    git push origin "$BRANCH"
fi

if [[ "$PUBLISH_PYPI_VIA_GITHUB" -eq 1 && -z "$REMOTE_TAG_COMMIT" ]]; then
    if LOCAL_TAG_COMMIT="$(git rev-parse "$TAG^{commit}" 2>/dev/null)"; then
        [[ "$LOCAL_TAG_COMMIT" == "$COMMIT" ]] ||
            die "local tag $TAG points to $LOCAL_TAG_COMMIT, expected $COMMIT"
    else
        git tag -a "$TAG" -m "$TAG" "$COMMIT"
    fi
    git push origin "$TAG"
    REMOTE_TAG_COMMIT="$COMMIT"
fi

if [[ "$PUBLISH_PYPI" -eq 1 ]]; then
    if [[ "$PYPI_COMPLETE" -eq 0 ]]; then
        if [[ "$PUBLISH_PYPI_VIA_GITHUB" -eq 1 ]]; then
            gh workflow run publish-pypi.yml \
                --repo "$GITHUB_REPO" \
                --ref "$BRANCH" \
                -f tag="$TAG"
        else
            UV_PUBLISH_TOKEN="$PYPI_TOKEN" \
                uv publish --check-url "$PYPI_CHECK_URL" server/dist/*
        fi
    fi

    PYPI_VERIFIED=0
    for _ in {1..60}; do
        if pypi_release_complete; then
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
    TEMP_PATHS+=("$NOTES_FILE")
    printf '%s\n\n' "$CHANGELOG_SECTION" >"$NOTES_FILE"
    cat >>"$NOTES_FILE" <<EOF
Zotero automatic updates use the fixed release asset:
https://github.com/NightWatcher314/zotero-pdf2zh-next/releases/download/release/update.json
EOF

    if [[ "$GITHUB_RELEASE_EXISTS" -eq 1 ]]; then
        echo "Reusing existing GitHub release: $TAG"
        if ! gh release view "$TAG" --repo "$GITHUB_REPO" --json assets --jq '.assets[].name' |
            grep -Fxq 'zotero-pdf2zh-next.xpi'; then
            gh release upload "$TAG" plugin/build/zotero-pdf2zh-next.xpi --repo "$GITHUB_REPO"
        fi
    else
        gh release create "$TAG" \
            plugin/build/zotero-pdf2zh-next.xpi \
            --repo "$GITHUB_REPO" \
            --target "$COMMIT" \
            --title "$TAG" \
            --notes-file "$NOTES_FILE" \
            --latest
    fi

    if gh release view release --repo "$GITHUB_REPO" >/dev/null 2>&1; then
        gh release upload release plugin/build/update.json --repo "$GITHUB_REPO" --clobber
    else
        gh release create release \
            plugin/build/update.json \
            --repo "$GITHUB_REPO" \
            --target "$COMMIT" \
            --title "Zotero update manifest" \
            --notes "Stable update manifest used by Zotero Check for Updates." \
            --latest=false
    fi
fi

if [[ "$UPDATE_TAP" -eq 1 && "$PUSH" -eq 1 ]]; then
    TAP_REPO="NightWatcher314/homebrew-formula"
    TAP_BRANCH="zotero-pdf2zh-next-$TAG"
    TAP_REMOTE="$(git -C "$TAP_PATH" remote get-url origin)"
    TAP_TEMP_ROOT="$(mktemp -d)"
    TAP_WORKTREE="$TAP_TEMP_ROOT/tap"
    TEMP_PATHS+=("$TAP_TEMP_ROOT")

    git clone --quiet "$TAP_PATH" "$TAP_WORKTREE"
    git -C "$TAP_WORKTREE" remote set-url origin "$TAP_REMOTE"
    git -C "$TAP_WORKTREE" fetch --quiet origin main
    if git -C "$TAP_WORKTREE" ls-remote --exit-code --heads origin "$TAP_BRANCH" >/dev/null 2>&1; then
        git -C "$TAP_WORKTREE" fetch --quiet origin "$TAP_BRANCH"
        git -C "$TAP_WORKTREE" switch --quiet -c "$TAP_BRANCH" "origin/$TAP_BRANCH"
    else
        git -C "$TAP_WORKTREE" switch --quiet -c "$TAP_BRANCH" origin/main
    fi

    FORMULA_REL="Formula/zotero-pdf2zh-next.rb"
    FORMULA="$TAP_WORKTREE/$FORMULA_REL"
    TARBALL_URL="https://github.com/NightWatcher314/zotero-pdf2zh-next/archive/$COMMIT.tar.gz"
    SHA256="$(curl -LfsS "$TARBALL_URL" | shasum -a 256 | awk '{print $1}')"
    [[ -n "$SHA256" ]] || die "failed to compute sha256 for $TARBALL_URL"

    URL="$TARBALL_URL" VERSION="$VERSION" SHA256="$SHA256" perl -0pi -e '
s/url "[^"]+"/url "$ENV{URL}"/;
s/version "[^"]+"/version "$ENV{VERSION}"/;
s/sha256 "[^"]+"/sha256 "$ENV{SHA256}"/;
' "$FORMULA"

    git -C "$TAP_WORKTREE" diff -- "$FORMULA_REL"
    ruby -c "$FORMULA"
    if command -v brew >/dev/null 2>&1; then
        HOMEBREW_NO_AUTO_UPDATE=1 brew style "$FORMULA"
    fi

    git -C "$TAP_WORKTREE" add "$FORMULA_REL"
    if ! git -C "$TAP_WORKTREE" diff --cached --quiet; then
        git -C "$TAP_WORKTREE" commit -m "chore: update zotero-pdf2zh-next to $TAG"
    fi
    TAP_HEAD_SHA="$(git -C "$TAP_WORKTREE" rev-parse HEAD)"
    git -C "$TAP_WORKTREE" push --set-upstream origin "$TAP_BRANCH"

    TAP_PR_NUMBER="$(gh pr list \
        --repo "$TAP_REPO" \
        --head "$TAP_BRANCH" \
        --state open \
        --json number \
        --jq '.[0].number // empty')"
    if [[ -z "$TAP_PR_NUMBER" ]]; then
        TAP_PR_URL="$(gh pr create \
            --repo "$TAP_REPO" \
            --base main \
            --head "$TAP_BRANCH" \
            --title "chore: update zotero-pdf2zh-next to $TAG" \
            --body "Build and publish Homebrew bottles for zotero-pdf2zh-next $TAG.")"
        TAP_PR_NUMBER="${TAP_PR_URL##*/}"
    fi

    TAP_CHECKS_FOUND=0
    for _ in {1..30}; do
        if [[ "$(gh pr checks "$TAP_PR_NUMBER" \
            --repo "$TAP_REPO" \
            --json name \
            --jq 'length' 2>/dev/null || true)" -gt 0 ]]; then
            TAP_CHECKS_FOUND=1
            break
        fi
        sleep 2
    done
    [[ "$TAP_CHECKS_FOUND" -eq 1 ]] || die "Homebrew bottle checks did not start for PR #$TAP_PR_NUMBER"
    gh pr checks "$TAP_PR_NUMBER" --repo "$TAP_REPO" --watch --fail-fast

    PREVIOUS_PUBLISH_RUN="$(gh run list \
        --repo "$TAP_REPO" \
        --workflow publish.yml \
        --event workflow_dispatch \
        --limit 1 \
        --json databaseId \
        --jq '.[0].databaseId // empty')"
    gh workflow run publish.yml \
        --repo "$TAP_REPO" \
        --ref main \
        -f pull_request="$TAP_PR_NUMBER" \
        -f head_sha="$TAP_HEAD_SHA"

    TAP_PUBLISH_RUN=""
    for _ in {1..30}; do
        TAP_PUBLISH_RUN="$(gh run list \
            --repo "$TAP_REPO" \
            --workflow publish.yml \
            --event workflow_dispatch \
            --limit 1 \
            --json databaseId \
            --jq '.[0].databaseId // empty')"
        if [[ -n "$TAP_PUBLISH_RUN" && "$TAP_PUBLISH_RUN" != "$PREVIOUS_PUBLISH_RUN" ]]; then
            break
        fi
        sleep 2
    done
    [[ -n "$TAP_PUBLISH_RUN" && "$TAP_PUBLISH_RUN" != "$PREVIOUS_PUBLISH_RUN" ]] ||
        die "Homebrew bottle publish workflow did not start"
    gh run watch "$TAP_PUBLISH_RUN" --repo "$TAP_REPO" --exit-status

    TAP_PR_STATE="$(gh pr view "$TAP_PR_NUMBER" --repo "$TAP_REPO" --json state --jq .state)"
    [[ "$TAP_PR_STATE" == "CLOSED" || "$TAP_PR_STATE" == "MERGED" ]] ||
        die "Homebrew bottle PR #$TAP_PR_NUMBER was not closed by brew pr-pull"
    git -C "$TAP_PATH" pull --ff-only
    grep -Fq "bottle do" "$TAP_PATH/$FORMULA_REL" ||
        die "Homebrew formula does not contain a bottle block after publishing"
    grep -Eq 'sha256 arm64_[a-z_]+' "$TAP_PATH/$FORMULA_REL" ||
        die "Homebrew formula does not contain an Apple Silicon bottle"

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
