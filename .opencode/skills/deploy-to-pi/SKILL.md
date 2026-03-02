---
name: deploy-to-pi
description: Deploy cunningbot to the Raspberry Pi by committing, pushing, SSHing in, pulling, and restarting the bot
compatibility: opencode
---

## What I do

1. Commit any staged/unstaged changes with an appropriate commit message
2. Push to origin
3. SSH into the Pi (`ssh dad@192.168.1.182`)
4. `cd /home/dad/cunningbot`
5. `git pull`
6. Decide whether to `make restart` or `make rebuild`:
   - Use `make rebuild` if any dependencies changed (e.g. `requirements.txt`, `Dockerfile`, `docker-compose.yml`)
   - Use `make restart` for all other changes (code-only changes)

## When to use me

Use this when the user wants to deploy the latest changes to the Raspberry Pi running cunningbot on the local network.

## Notes

- The Pi is at `192.168.1.182`, user `dad`
- The bot runs via Docker Compose in `/home/dad/cunningbot`
- `make restart` = stop + remove + start (no rebuild)
- `make rebuild` = stop + remove + build (no-cache) + start
