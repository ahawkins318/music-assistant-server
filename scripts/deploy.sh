#!/usr/bin/env bash
#
# Builds Dockerfile.custom and pushes it to GHCR, tagged with both the git
# short SHA (immutable, so Portainer can never mistake a stale pull for a
# fresh one) and :latest (for convenience). Assumes a one-time
# `docker login ghcr.io -u ahawkins318` has already been done.
#
# The image is the official release this branch is based on, with this
# checkout's music_assistant package copied over it (see Dockerfile.custom
# for why). That only holds while the branch differs from that release in
# package and test files alone, which is checked below.
#
# Always targets linux/amd64 via buildx, regardless of the host machine's
# own architecture — the NAS this deploys to is x86. See LOCAL.md for moving
# the branch to a newer stable release.
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="ghcr.io/ahawkins318/music-assistant-server"
DEPLOY_BRANCH="custom-deploy"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree has uncommitted changes — commit before deploying" >&2
  git status --short
  exit 1
fi

# Checks the branch NAME, not just the commit: a fix branch freshly cut from
# here would otherwise pass and push an image that is missing the others.
CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || true)"
if [[ "$CURRENT_BRANCH" != "$DEPLOY_BRANCH" ]]; then
  echo "error: deploy.sh only runs from $DEPLOY_BRANCH (currently on '${CURRENT_BRANCH:-detached HEAD}')." >&2
  echo "Testing a build change? Use 'docker buildx build -f Dockerfile.custom --build-arg BASE_TAG=<release> .' without --push instead." >&2
  exit 1
fi

git fetch origin "$DEPLOY_BRANCH" --quiet
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$DEPLOY_BRANCH")" ]]; then
  echo "error: local $DEPLOY_BRANCH is not in sync with origin/$DEPLOY_BRANCH — push (or pull) first" >&2
  exit 1
fi

# The official image's dependencies and non-package files are used as-is, so
# anything else that changed since the release would be silently left out,
# and a file upstream deleted would survive in the image.
BASE_TAG="$(git describe --tags --abbrev=0 HEAD)"
if [[ ! "$BASE_TAG" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "error: $DEPLOY_BRANCH is based on '$BASE_TAG', which is not a stable release tag (see LOCAL.md)" >&2
  exit 1
fi
DRIFT="$(git diff --name-status "$BASE_TAG" HEAD \
  | grep -vE $'^[AM]\t(music_assistant|tests)/' \
  | grep -vE $'^A\t(Dockerfile\\.custom|LOCAL\\.md|scripts/deploy\\.sh)$' || true)"
if [[ -n "$DRIFT" ]]; then
  echo "error: $DEPLOY_BRANCH differs from release $BASE_TAG outside music_assistant/ and tests/, or deletes files:" >&2
  echo "$DRIFT" >&2
  echo "Keep such changes off $DEPLOY_BRANCH; it may only add to music_assistant/ and tests/ (see LOCAL.md)." >&2
  exit 1
fi

SHA="$(git rev-parse --short HEAD)"

# Build from the committed tree, not the checkout, so ignored local files
# (__pycache__, a local app_secrets.json) can't end up in the image.
CONTEXT="$(mktemp -d)"
trap 'rm -rf "$CONTEXT"' EXIT
git archive HEAD music_assistant Dockerfile.custom | tar -x -C "$CONTEXT"

echo "Building and pushing ${IMAGE}:${SHA} (release ${BASE_TAG} + fork) for linux/amd64 ..."
docker buildx build --platform linux/amd64 \
  -f "$CONTEXT/Dockerfile.custom" \
  --build-arg "BASE_TAG=${BASE_TAG}" \
  --label "org.opencontainers.image.revision=${SHA}" \
  --label "org.opencontainers.image.source=https://github.com/ahawkins318/music-assistant-server" \
  -t "${IMAGE}:${SHA}" -t "${IMAGE}:latest" \
  --push "$CONTEXT"

cat <<EOF

Pushed:
  ${IMAGE}:${SHA}
  ${IMAGE}:latest

Next: in Portainer, set the Music Assistant service's image to the ${SHA} tag and redeploy.
That tag can never be stale — if you'd rather stay on :latest, make sure
"pull new image" / "always pull" is enabled before redeploying.
EOF
