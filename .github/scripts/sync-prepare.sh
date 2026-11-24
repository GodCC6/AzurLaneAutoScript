#!/usr/bin/env bash
set -euo pipefail
# Save the trusted copy before checking out untrusted upstream code.
cp .github/scripts/sync-conflict-id.py "$RUNNER_TEMP/sync-conflict-id.py"
cp .github/scripts/sync-smoke.py "$RUNNER_TEMP/sync-smoke.py"
git fetch --no-tags origin refs/heads/patched:refs/remotes/origin/patched \
  refs/heads/master:refs/remotes/origin/master
p=$(git rev-parse refs/remotes/origin/patched)
u=$(git rev-parse refs/remotes/origin/master)
echo "patched=$p" >> "$GITHUB_OUTPUT"
echo "upstream=$u" >> "$GITHUB_OUTPUT"
git checkout --detach "$p"
if ! git -c user.name='github-actions[bot]' \
  -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
  -c rerere.enabled=true -c rerere.autoupdate=false --no-optional-locks merge --no-ff --no-edit "$u"; then
  [[ -f $(git rev-parse --git-path MERGE_HEAD) ]]
  id=$(python3 "$RUNNER_TEMP/sync-conflict-id.py")
  echo "mode=conflict" >> "$GITHUB_OUTPUT"
  echo "fingerprint=$id" >> "$GITHUB_OUTPUT"
  exit 0
fi
c=$(git rev-parse HEAD)
git diff --check "$p" "$c"
git ls-files -z -- '*.py' | xargs -0 -r python3 -m py_compile
python3 "$RUNNER_TEMP/sync-smoke.py"
git diff --quiet HEAD --
if [[ $c == "$p" ]]; then
  echo "mode=unchanged" >> "$GITHUB_OUTPUT"
else
  git update-ref refs/sync/candidate "$c"
  git bundle create "$RUNNER_TEMP/candidate.bundle" refs/sync/candidate "^$p" "^$u"
  echo "mode=candidate" >> "$GITHUB_OUTPUT"
  echo "candidate=$c" >> "$GITHUB_OUTPUT"
fi
