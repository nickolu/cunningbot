# Deploying

## The pipeline

```
feature branch → PR → merge to main → Pi's systemd timer notices within ~2 min
                                    → git pull --ff-only && docker compose up -d --build
```

`main` is **protected** — direct pushes are rejected. It is also live: merging
is deploying.

## Shipping a change

```bash
git checkout -b feat/<thing>
# ... work ...
python3 -m pytest tests/
git add -A && git commit
git push -u origin feat/<thing>
gh pr create --fill
```

Then the user merges. Nothing else is required — the Pi picks it up.

## The auto-deploy

Installed on the Pi as `cunningbot-autodeploy.timer` (every 2 min) running
`scripts/auto_deploy.sh`:

```bash
git fetch origin main
# no-op and exit 0 if HEAD == origin/main
git pull --ff-only origin main
docker compose up -d --build
```

`up -d --build` rebuilds changed images and restarts every service, so it covers
`requirements.txt`, `Dockerfile`, and `docker-compose.yml` changes as well as
code. There is no restart-only fast path anymore.

Sources of truth: `scripts/README_autodeploy.md`, `scripts/auto_deploy.sh`,
`scripts/cunningbot-autodeploy.{service,timer}`.

## Checking on it

```bash
ssh dad@192.168.1.182

systemctl list-timers cunningbot-autodeploy.timer
journalctl -u cunningbot-autodeploy.service -n 50 --no-pager   # "Deploying <old> -> <new>" / "Deploy complete"

cd /home/dad/cunningbot
git log --oneline -3          # confirm the commit landed
docker compose ps             # all nine services up?
docker compose logs -f --tail=100 cunningbot
docker compose logs --tail=50 weather-poster
```

A no-op auto-deploy run prints nothing — silence in the journal means "already
up to date", not "broken".

## Manual intervention

```bash
ssh dad@192.168.1.182 "cd /home/dad/cunningbot && sudo systemctl start cunningbot-autodeploy.service"   # deploy now
ssh dad@192.168.1.182 "cd /home/dad/cunningbot && git pull && docker compose up -d --build"             # bypass the timer
```

Emergency rollback: revert the commit on GitHub and let the timer deploy the
revert. Resetting the Pi's checkout directly will be undone at the next tick
(`--ff-only` will fail and the deploy will wedge), so revert through `main`.

## Environment

`.env` on the Pi holds `DISCORD_TOKEN`, `OPENAI_API_KEY`, `PERPLEXITY_API_KEY`,
`CLIENT_ID`, `INVITE_URL`. It is gitignored and **not** deployed — a new
variable must be added to the Pi's `.env` by hand *before* merging the code that
reads it, or the affected container crash-loops. `.env.example` documents the set;
update it in the same PR.

## Notes

- `.opencode/skills/deploy-to-pi/SKILL.md` describes the older manual
  `make restart` / `make rebuild` flow. It predates the auto-deploy timer; prefer
  this document.
- Docker builds on the Pi are slow (ARM, `--no-cache` on `make build`). Expect a
  couple of minutes before a merged change is live.
