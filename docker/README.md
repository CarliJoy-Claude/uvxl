# Isolated Claude Code container

Ubuntu 24.04 with uv, Rust (rustup, stable) and Claude Code.
It runs `claude remote-control` in `/workspace`, so you drive it from claude.ai/code or the Claude app.
Only the project directory is mounted; your home directory, SSH keys and host credentials are not.

## First start: log in once

```
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose build
docker compose run --rm claude claude
```

Run `/login` in that interactive session, then exit.
The login is stored in the `claude-config` volume and survives rebuilds.

## Run

```
docker compose up -d
docker compose logs -f claude   # shows the session link
```

The session appears in claude.ai/code as `locked-install`.
Stop it with `docker compose down` (add `-v` to also drop the login and caches).

## Settings

Local settings live in `.env` next to `compose.yaml` (git-ignored, optional).
Compose uses it twice: for the `${...}` values in `compose.yaml`, and through `env_file` as environment of the container.

- `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL`: identity for commits made in the container.
- `HOST_UID`, `HOST_GID`: your host IDs, so files written to the mount belong to you (default 1000).
- `CLAUDE_PERMISSION_MODE`: permission mode for spawned sessions (default `acceptEdits`).
  The container is the isolation boundary, so `bypassPermissions` is an option, but note it can still push with any credentials you give it.
- Any other variable you add is passed into the container as well.

With `HOST_UID` and `HOST_GID` in `.env`, a plain `docker compose build` is enough.

## Notes

- The virtual environment lives at `/home/dev/venv` (`UV_PROJECT_ENVIRONMENT`), not in the host's `.venv`, whose interpreter links only work on the host.
- Cargo builds go to `/home/dev/cargo-target`, apart from the host's `target/`.
- There is no git remote credential in the container: pushing needs one added on purpose.
