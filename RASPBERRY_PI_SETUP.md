# ManchatBot Raspberry Pi Deployment Guide

This guide explains how to deploy ManchatBot on a Raspberry Pi using Docker.

## Prerequisites

- Raspberry Pi (3 or newer recommended) with Raspberry Pi OS installed
- SSH access to your Raspberry Pi
- Your Discord bot token and OpenAI API key

## 1. SSH into your Raspberry Pi

```bash
ssh dad@192.168.1.182
```

## 2. Install Docker

```bash
# Update and install dependencies
sudo apt update
sudo apt upgrade -y
sudo apt install -y curl

# Install Docker
curl -sSL https://get.docker.com | sh

# Add your user to the docker group to avoid using sudo
sudo usermod -aG docker $USER

# Log out and back in for group changes to take effect
exit
```

Reconnect via SSH, then verify Docker is working:

```bash
docker --version
```

## 3. Install Docker Compose

```bash
# Install required packages
sudo apt install -y python3-pip libffi-dev

# Install Docker Compose
sudo pip3 install docker-compose
```

## 4. Deploy ManchatBot

### Copy your local files to the Raspberry Pi

From your local machine (not the Raspberry Pi), use SCP to copy your project files:

```bash
# On your local machine, navigate to the parent directory of manchatbot
cd /Users/nickolus/git/personal/

# Copy the entire directory to the Raspberry Pi
# Replace username and raspberry-pi-ip-address with your actual values
scp -r manchatbot dad@192.168.1.182:~/
```

Alternatively, you can use rsync which is more efficient for larger projects:

```bash
# On your local machine
rsync -avz --progress manchatbot/ dad@192.168.1.182:~/manchatbot/
```

After copying the files, SSH back into your Raspberry Pi and navigate to the project:

```bash
ssh dad@192.168.1.182
cd manchatbot
```

Your existing `.env` file with all credentials will be included in the copied files, so no need to recreate it.

### Build and run the Docker container

```bash
docker-compose up -d
```

The `-d` flag runs the container in detached mode (background).

## 5. Verify the Bot is Running

Check the logs to see if the bot started correctly:

```bash
docker-compose logs -f
```

Press Ctrl+C to exit the logs.

## 6. Managing the Bot

### View container status

```bash
docker-compose ps
```

### Stop the bot

```bash
docker-compose down
```

### Restart the bot

```bash
docker-compose restart
```

### Update the bot

Pull the latest code and rebuild:

```bash
git pull
docker-compose up -d --build
```

## 7. Setting Up Auto-start on Boot

To ensure your bot starts automatically whenever your Raspberry Pi reboots:

```bash
# Create a systemd service file
sudo nano /etc/systemd/system/manchatbot.service
```

Add the following content (update the paths as needed):

```
[Unit]
Description=ManchatBot Discord Bot
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/your-username/manchatbot
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl enable manchatbot.service
sudo systemctl start manchatbot.service
```

## Troubleshooting

If your bot doesn't start properly, check the logs:

```bash
docker-compose logs
```

For persistent state issues, make sure the volume mapping in docker-compose.yml is correct and the container has write permissions to the core directory.

## Performance Optimization

To optimize performance on the Raspberry Pi:

1. **Monitor resource usage**:
   ```bash
   # Check CPU and memory usage
   htop
   ```

2. **Reduce logging verbosity** if needed by modifying the logging level in `bot/core/logger.py`

3. **Consider using a swap file** if your bot is memory-constrained:
   ```bash
   # Create a 1GB swap file
   sudo fallocate -l 1G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

4. **State persistence**: The Docker setup is configured with volume mappings to preserve your bot's state, ensuring that personality settings and other state data persists between container restarts.
