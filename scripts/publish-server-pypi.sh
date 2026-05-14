#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/publish-server-pypi.sh <version> [--push]

Build and publish only the Python server package to PyPI.

Examples:
  scripts/publish-server-pypi.sh 5.2.3
  scripts/publish-server-pypi.sh 5.2.3 --push

Set UV_PUBLISH_TOKEN before publishing. With direnv, put it in .envrc and run:
  direnv allow
EOF
}

die() {
    echo "publish-server-pypi.sh: $*" >&2
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
PUSH=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --push)
            PUSH=1
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

for cmd in git uv perl; do
    require_command "$cmd"
done

[[ -n "${UV_PUBLISH_TOKEN:-}" ]] ||
    die "UV_PUBLISH_TOKEN is required; set it in .envrc and run direnv allow"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git diff --quiet || die "tracked worktree changes exist; commit or stash them first"
git diff --cached --quiet || die "staged changes exist; commit or unstage them first"

VERSION="$VERSION" perl -0pi -e 's/version = "[^"]+"/version = "$ENV{VERSION}"/' server/pyproject.toml
VERSION="$VERSION" perl -0pi -e 's/VERSION = "[^"]+"/VERSION = "$ENV{VERSION}"/' server/server.py

uv --directory server lock
uv run --directory server python -m unittest discover -s tests
uv build server --out-dir server/dist --clear --no-sources
uv publish server/dist/*

git add server/pyproject.toml server/server.py server/uv.lock
if ! git diff --cached --quiet; then
    git commit -m "chore: publish server $VERSION to pypi"
fi

if [[ "$PUSH" -eq 1 ]]; then
    git push origin "$(git branch --show-current)"
fi

echo "Published server $VERSION to PyPI"
