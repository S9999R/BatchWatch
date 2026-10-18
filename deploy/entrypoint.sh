#!/bin/sh
# web:   serve the site (PORT, default 7860)
# watch: one smart-update pass. Needs NEBIUS_API_KEY, plus GITHUB_REPO (owner/name) and GITHUB_TOKEN
#        (a token that may push to that repo) to publish the result to the "data" branch.
set -eu
mode="${1:-web}"

if [ "$mode" = "web" ]; then
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-7860}" --proxy-headers --forwarded-allow-ips="*"
fi

if [ "$mode" = "watch" ]; then
  : "${GITHUB_REPO:?set GITHUB_REPO=owner/name}"
  : "${GITHUB_TOKEN:?set GITHUB_TOKEN to a token that can push to the repo}"
  remote="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
  work="$(mktemp -d)"
  if git ls-remote --exit-code --heads "$remote" data >/dev/null 2>&1; then
    git clone -q --depth 1 --branch data "$remote" "$work/live"
  else
    git init -q -b data "$work/live" && git -C "$work/live" remote add origin "$remote"
  fi
  BATCHWATCH_DATA_DIR="$work/live" python -m batchwatch.watch
  cd "$work/live"
  git config user.name "batchwatch-bot"
  git config user.email "batchwatch-bot@users.noreply.github.com"
  git add -A
  if git diff --cached --quiet; then exit 0; fi
  git commit -qm "watch (Nebius Serverless Job): $(date -u +%Y-%m-%dT%H:%MZ)"
  git push -q origin data
  exit 0
fi

echo "usage: entrypoint.sh [web|watch]" >&2
exit 2
