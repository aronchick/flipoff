#!/bin/sh
# Fast-forward a FlipOff checkout from its origin and rebuild the container
# only when the checked-out commit differs from the last one deployed.
#
# Fail-safe by design: it never resets, stashes, or discards local work. A
# dirty tree, a detached or wrong branch, or a diverged history stops the run
# with a non-zero exit, so the unit shows as failed and the reason is in the
# journal (journalctl -u flipoff-update).
set -eu

dir=${FLIPOFF_DIR:?set FLIPOFF_DIR to the flipoff checkout}
branch=${FLIPOFF_BRANCH:-main}
state_dir=${STATE_DIRECTORY:-/var/lib/flipoff-update}
deployed_file="$state_dir/deployed-commit"

# Git runs as the checkout's owner, so it uses their SSH keys and config and
# never leaves root-owned files in .git. Docker needs root.
owner=$(stat -c %U "$dir")
owner_home=$(getent passwd "$owner" | cut -d: -f6)
g() { runuser -u "$owner" -- env HOME="$owner_home" git -C "$dir" "$@"; }

current=$(g symbolic-ref --quiet --short HEAD || echo "(detached)")
if [ "$current" != "$branch" ]; then
  echo "refusing to update: $dir is on $current, expected $branch" >&2
  exit 1
fi

dirty=$(g status --porcelain)
if [ -n "$dirty" ]; then
  echo "refusing to update: $dir has local changes:" >&2
  echo "$dirty" >&2
  exit 1
fi

g fetch --quiet origin "$branch"
before=$(g rev-parse HEAD)
if ! g merge --ff-only --quiet FETCH_HEAD; then
  echo "refusing to update: $branch has diverged from origin/$branch" >&2
  exit 1
fi
head=$(g rev-parse HEAD)
[ "$before" = "$head" ] || echo "fast-forwarded $before -> $head"

# Compare against what was last deployed, not just what was fetched, so a
# build that failed on a previous run is retried instead of forgotten.
deployed=$(cat "$deployed_file" 2>/dev/null || true)
if [ "$deployed" = "$head" ]; then
  echo "up to date at $head"
  exit 0
fi

echo "deploying $head (previously ${deployed:-unknown})"
cd "$dir"
docker compose up -d --build --wait --wait-timeout 180
mkdir -p "$state_dir"
echo "$head" > "$deployed_file"
# Each rebuild leaves the previous image dangling; keep the disk from filling.
docker image prune -f >/dev/null
echo "deployed $head"
