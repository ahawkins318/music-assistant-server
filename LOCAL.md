# Local fork notes

This fork (`ahawkins318/music-assistant-server`) runs on the NAS in place of the official image. Everything
here applies to the `custom-deploy` branch only.

## What the fork changes

`custom-deploy` is the latest stable release tag plus these commits, in order:

1. Keep tracks at different positions of one album distinct (`helpers/compare.py`).
2. Add option to sync only library items unique to a provider ("sync unique only", a per-provider sync
   setting; turn it on for Apple Music).
3. Unmerge earlier merges when syncing only unique items.
4. The deploy files: `Dockerfile.custom`, `scripts/deploy.sh`, this file.
5. Take a provider's leftover tracks off an album when unmerging it (not yet on `library-sync-unique-only`).

The same fixes exist against upstream `dev` on `case4-distinct-album-positions-merged` and
`library-sync-unique-only`. Those are the versions to offer upstream. The `dev` version of the sync patch
also blocks MusicBrainz URL linking for a unique-only provider; stable 2.10 has no such linking, so that
guard and its tests are absent here.

## Deploying

```
./scripts/deploy.sh
```

Requires a clean tree, `custom-deploy` checked out and pushed, and a one-time
`docker login ghcr.io -u ahawkins318`. It pushes `ghcr.io/ahawkins318/music-assistant-server` tagged with the
short SHA and `:latest`; then set the SHA tag on the Music Assistant service in Portainer and redeploy.

The image is the official release image with this branch's `music_assistant/` copied over it, which keeps the
bundled provider credentials (see `Dockerfile.custom`). Consequences:

- `custom-deploy` may only add or modify files under `music_assistant/` and `tests/`. Dependency or
  packaging changes would not reach the image; the script refuses to build if it sees any.
- Keep the GHCR package private: it contains upstream's credentials file.

## Moving to a newer stable release

Back up the Music Assistant `/data` volume first: a release that bumps the database schema migrates it one
way, so the backup is the only route back to the previous image.

```
git fetch upstream --tags
OLD="$(git describe --tags --abbrev=0 custom-deploy)"
NEW="$(git tag --sort=-v:refname | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
git checkout custom-deploy
git rebase --onto "$NEW" "$OLD"
```

Stable tags are cut from release branches, not from `dev`, so use `rebase --onto` as above rather than a
merge. Resolve conflicts, then:

```
.venv/bin/python -m pytest tests/helpers/test_compare.py tests/controllers/music/test_sync_unique_only.py \
  tests/controllers/music/test_albums_match.py tests/controllers/metadata/test_album_reconciliation.py
git push --force-with-lease origin custom-deploy
./scripts/deploy.sh
```

Once the new image is running and its first syncs have finished, audit the library for merges by running 
`tools/audit_music_assistant.py` in the NAS organization repo. 

Notes for the rebase:

- Conflicts land in `models/music_provider.py` and `controllers/music/media/albums.py`. The patch adds a
  `library_sync_unique_only()` check before each library insert in the artist, album and track sync loops,
  and in `albums.match_providers`.
- When a new minor release (2.11, 2.12, ...) is the first to contain `dev`'s MusicBrainz URL linking
  (`albums._verify_musicbrainz_mapping`), take commits 2 and 3 from `library-sync-unique-only` instead of
  replaying the ones here, so the linking guard comes along. Rebase that branch onto current `dev` first if
  it has gone stale.
- If a fix is accepted upstream, its commit drops out of the rebase as already applied.
- `.venv` follows whatever was last installed; if tests fail on import or dependency errors, reinstall it for
  the new tag (`scripts/setup.sh`) and compare failures against the plain tag before blaming the patches.
