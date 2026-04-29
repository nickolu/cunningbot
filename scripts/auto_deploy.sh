#!/usr/bin/env bash
# Pulls and rebuilds cunningbot when origin/main has new commits.
# Intended to be run on a timer (see scripts/cunningbot-autodeploy.{service,timer}).
set -euo pipefail

REPO_DIR="${CUNNINGBOT_REPO_DIR:-/home/dad/cunningbot}"
BRANCH="${CUNNINGBOT_BRANCH:-main}"

cd "$REPO_DIR"

git fetch --quiet origin "$BRANCH"

local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse "origin/$BRANCH")

if [ "$local_sha" = "$remote_sha" ]; then
    exit 0
fi

echo "Deploying $local_sha -> $remote_sha"
git pull --ff-only origin "$BRANCH"
docker compose up -d --build
echo "Deploy complete"
