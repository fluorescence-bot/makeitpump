#!/usr/bin/env bash
# Publish the code-only part of this (private) repo to the public org repo.
#
#   scripts/publish-to-org.sh             filter main and push it to the org
#   scripts/publish-to-org.sh --dry-run   filter and check, but don't push
#   scripts/publish-to-org.sh --force     overwrite the org's main (first publish
#                                         over a repo with unrelated history)
#
# Clones main from GitHub (not your working copy), keeps only the paths below
# with their full history, and drops everything else (course/, reports,
# sketches, ...). filter-repo is deterministic, so re-running it later produces
# the same commits plus the new ones, and the push is a plain fast-forward.
#
# Needs git-filter-repo: `uv tool install git-filter-repo` (or pip install).

set -euo pipefail

SOURCE=https://github.com/alexandermittet/DIGIFAB.git
TARGET=https://github.com/fluorescence-bot/rainmixer.git
BRANCH=main

# Everything that goes public. Old roots (gui/, firmware/, reglo_gui/, ...) keep
# the history from before the code moved into code/.
KEEP=(
    code/ scripts/ .github/
    gui/ firmware/ reglo_gui/ dilution_controller.py mock_gui.py
    README.md CLAUDE.md LICENSE .gitignore .gitattributes
)

say() { printf '\033[1;35m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

dry_run=false
push_flags=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=true ;;
        --force)   push_flags=(--force) ;;
        *)         die "Unknown option: $arg" ;;
    esac
done

command -v git-filter-repo >/dev/null 2>&1 \
    || die "git-filter-repo not found. Install it with: uv tool install git-filter-repo"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

say "Cloning $BRANCH from $SOURCE"
git clone --quiet --single-branch --branch "$BRANCH" "$SOURCE" "$work/repo"
cd "$work/repo"

say "Keeping only: ${KEEP[*]}"
args=()
for p in "${KEEP[@]}"; do args+=(--path "$p"); done
git filter-repo --force --quiet "${args[@]}"

say "Checking nothing private slipped through"
leaked="$(git log --all --name-only --format= | sort -u \
    | grep -E '^course/|^portfolios/|^sketches|^notes/|^prototypes/|report\.(tex|pdf)$' || true)"
[[ -z "$leaked" ]] || { echo "$leaked"; die "Filtered history still contains the files above"; }

echo "  $(git rev-list --count HEAD) commits, top-level: $(git ls-files | cut -d/ -f1 | sort -u | tr '\n' ' ')"

if $dry_run; then
    say "Dry run: not pushing. Result was in $work/repo (deleted on exit)."
    exit 0
fi

say "Pushing to $TARGET ($BRANCH)"
git push "${push_flags[@]}" "$TARGET" "$BRANCH:$BRANCH" \
    || die "Push rejected. If the org repo has unrelated history (e.g. its initial commit), re-run with --force."
say "Done: ${TARGET%.git}"
