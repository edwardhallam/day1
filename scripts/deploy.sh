#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-/opt/stacks/delivery-tracking}"
BRANCH="${BRANCH:-main}"
HEARTBEAT_VAR="${HEARTBEAT_VAR:-DAY1_COMPOSE_DEPLOY_URL}"
FORCE_BUILD="${FORCE_BUILD:-0}"

log() {
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

send_heartbeat() {
  command -v heartbeat-ping >/dev/null 2>&1 || return 0

  if [ -r /etc/heartbeat-urls.conf ]; then
    heartbeat-ping "$HEARTBEAT_VAR" || true
  elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -n heartbeat-ping "$HEARTBEAT_VAR" || true
  else
    heartbeat-ping "$HEARTBEAT_VAR" || true
  fi
}

cd "$REPO_DIR"

changed=0
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
  log "Fetching origin/${BRANCH}"
  git fetch origin "$BRANCH"
  if ! git diff --quiet HEAD "origin/${BRANCH}"; then
    log "Fast-forwarding to origin/${BRANCH}"
    git merge --ff-only "origin/${BRANCH}"
    changed=1
  else
    log "Already at origin/${BRANCH}"
  fi
else
  log "No git remote configured; deploying current local checkout"
fi

log "Validating compose config"
docker compose config --quiet

log "Pulling registry images"
docker compose pull --ignore-buildable

if [ "$changed" = "1" ] || [ "$FORCE_BUILD" = "1" ]; then
  log "Building local application images"
  docker compose build
else
  log "Skipping local image build; git checkout unchanged"
fi

log "Applying stack"
docker compose up -d --remove-orphans

send_heartbeat

log "Deploy complete"
