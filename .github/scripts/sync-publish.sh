#!/usr/bin/env bash
set -euo pipefail
state_ref=refs/heads/sync-incident-state

notice() {
  curl -sS --fail-with-body --max-time 30 -o /dev/null \
    -d "chat_id=${TELEGRAM_CHAT_ID:?}" --data-urlencode "text=$1" \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN:?}/sendMessage"
}

read_state() {
  local remote
  remote=$(git ls-remote --refs origin "$state_ref")
  state_commit=${remote%%$'\t'*}
  state=clear
  if [[ -n "$state_commit" ]]; then
    git fetch --no-tags origin "$state_ref"
    [[ $(git rev-parse FETCH_HEAD) == "$state_commit" ]]
    state=$(git show "$state_commit:incident")
    [[ $state == clear || $state =~ ^(pending|notified):[0-9a-f]{64}$ ]]
  fi
}

write_state() {
  local blob tree next
  local -a parents=()
  if [[ -n "$state_commit" ]]; then parents=(-p "$state_commit"); fi
  blob=$(printf '%s\n' "$1" | git hash-object -w --stdin)
  tree=$(printf '100644 blob %s\tincident\n' "$blob" | git mktree)
  next=$(git -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    commit-tree "$tree" "${parents[@]}" -m 'Record ALAS sync incident')
  git push origin "$next:$state_ref"
  state_commit=$next
}

summary() {
  printf '%s\n' "$*" >> "${GITHUB_STEP_SUMMARY:?}"
}
fail() { printf '%s\n' "::error::$*" >&2; exit 1; }

[[ ${PREPARE_RESULT:-} == success ]] || fail 'Candidate preparation failed; patched was not updated.'
[[ $EXPECTED_PATCHED =~ ^[0-9a-f]{40}$ && $EXPECTED_UPSTREAM =~ ^[0-9a-f]{40}$ ]]
git fetch --no-tags origin refs/heads/patched refs/heads/master
# Explicit lookup after fetch so neither a stale artifact nor a later conflict
# can publish or affect incident state for refs that moved during testing.
remote=$(git ls-remote --refs origin refs/heads/patched refs/heads/master)
[[ $(awk '$2=="refs/heads/patched" {print $1}' <<< "$remote") == "$EXPECTED_PATCHED" ]] || fail 'patched moved during validation.'
[[ $(awk '$2=="refs/heads/master" {print $1}' <<< "$remote") == "$EXPECTED_UPSTREAM" ]] || fail 'master moved during validation.'

read_state
case "$MODE" in
  conflict)
    [[ $FINGERPRINT =~ ^[0-9a-f]{64}$ ]]
    summary "Merge blocked; patched remains at `$EXPECTED_PATCHED`. Conflict: `$FINGERPRINT`."
    case "$state" in
      "notified:$FINGERPRINT")
        printf '%s\n' '::warning::Same unresolved merge conflict; patched unchanged.'
        exit 0 ;;
      "pending:$FINGERPRINT")
        notice "ALAS sync is blocked by a merge conflict. patched was not updated. $RUN_URL"
        write_state "notified:$FINGERPRINT"
        exit 0 ;;
      *)
        write_state "pending:$FINGERPRINT"
        notice "ALAS sync is blocked by a new merge conflict. patched was not updated. $RUN_URL"
        write_state "notified:$FINGERPRINT"
        echo 'handled=true' >> "$GITHUB_OUTPUT"
        # Deliberately fail exactly this first run for GitHub's native email.
        fail 'New merge conflict. patched was not updated.' ;;
    esac ;;
  candidate)
    [[ $CANDIDATE =~ ^[0-9a-f]{40}$ ]]
    git fetch "$RUNNER_TEMP/candidate.bundle" refs/sync/candidate
    [[ $(git rev-parse FETCH_HEAD) == "$CANDIDATE" ]] || fail 'Candidate SHA mismatch.'
    [[ $(git rev-list --parents -n1 "$CANDIDATE") == "$CANDIDATE $EXPECTED_PATCHED $EXPECTED_UPSTREAM" ]] || fail 'Candidate parents mismatch.'
    git push origin "$CANDIDATE:refs/heads/patched"
    [[ $(git ls-remote --refs origin refs/heads/patched | cut -f1) == "$CANDIDATE" ]] || fail 'Could not verify the published SHA.'
    summary "Published validated candidate `$CANDIDATE`." ;;
  unchanged)
    git merge-base --is-ancestor "$EXPECTED_UPSTREAM" "$EXPECTED_PATCHED"
    summary "Patched already includes and was validated against the current mirror." ;;
  *) fail 'Unrecognized preparation result.' ;;
esac

if [[ $state != clear ]]; then
  notice "ALAS sync recovered. The current patched branch passed validation. $RUN_URL"
  write_state clear
fi
