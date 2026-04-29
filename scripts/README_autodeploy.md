# Auto-deploy on push to `main`

A systemd timer on the Pi polls `origin/main` every 2 minutes. If new commits
are present, it pulls and runs `docker compose up -d --build`. The effect is
that merging a PR (e.g. from the GitHub mobile app) deploys the change a
couple of minutes later with no SSH from the laptop.

## One-time install on the Pi

```bash
ssh dad@192.168.1.182
cd /home/dad/cunningbot
git pull

sudo cp scripts/cunningbot-autodeploy.service /etc/systemd/system/
sudo cp scripts/cunningbot-autodeploy.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cunningbot-autodeploy.timer
```

## Verifying

```bash
systemctl list-timers cunningbot-autodeploy.timer
journalctl -u cunningbot-autodeploy.service -n 50 --no-pager
```

A no-op run prints nothing. A real deploy logs `Deploying <old> -> <new>` then
`Deploy complete`.

## Manual trigger

```bash
sudo systemctl start cunningbot-autodeploy.service
```

## Disable

```bash
sudo systemctl disable --now cunningbot-autodeploy.timer
```
