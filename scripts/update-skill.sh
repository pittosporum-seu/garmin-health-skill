#!/usr/bin/env bash
# Update this Git checkout to a published stable Garmin Health Skill release.
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target=""
dry_run=0

usage() {
  printf '%s\n' "Usage: bash scripts/update-skill.sh [--latest | --version vX.Y.Z] [--dry-run]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      target=""
      ;;
    --version)
      shift
      [[ $# -gt 0 ]] || { usage >&2; exit 2; }
      target="$1"
      ;;
    --dry-run)
      dry_run=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
  shift
done

[[ -d "$skill_dir/.git" ]] || {
  printf '%s\n' "This updater requires a Git checkout of garmin-health-skill." >&2
  exit 1
}

origin="$(git -C "$skill_dir" remote get-url origin)"
case "$origin" in
  *github.com*/pittosporum-seu/garmin-health-skill*) ;;
  *)
    printf '%s\n' "Refusing to update: origin is not pittosporum-seu/garmin-health-skill." >&2
    exit 1
    ;;
esac

[[ -z "$(git -C "$skill_dir" status --porcelain)" ]] || {
  printf '%s\n' "Refusing to update a working tree with local changes. Commit, stash, or remove them first." >&2
  exit 1
}

git -C "$skill_dir" fetch --tags --prune origin
if [[ -z "$target" ]]; then
  target="$(git -C "$skill_dir" tag --list 'v[0-9]*' --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
fi
[[ -n "$target" ]] || {
  printf '%s\n' "No stable vX.Y.Z release tag is available from origin." >&2
  exit 1
}
git -C "$skill_dir" rev-parse -q --verify "refs/tags/$target" >/dev/null || {
  printf 'Release tag not found: %s\n' "$target" >&2
  exit 1
}

if [[ "$dry_run" -eq 1 ]]; then
  printf 'Would update %s to %s. No checkout or dependency install was performed.\n' "$skill_dir" "$target"
  exit 0
fi

git -C "$skill_dir" checkout --detach "$target"
version="$(tr -d '\r\n' < "$skill_dir/VERSION")"
[[ "v$version" == "$target" ]] || {
  printf 'Release tag %s does not match VERSION %s.\n' "$target" "$version" >&2
  exit 1
}
"$skill_dir/.venv/bin/python" -m pip install -r "$skill_dir/requirements.txt"
printf 'Updated Garmin Health Skill to %s. Verify with: %s/.venv/bin/python %s/garmin_health_cli.py --version\n' "$target" "$skill_dir" "$skill_dir"
