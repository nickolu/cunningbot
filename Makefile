.PHONY: run build start stop restart logs clean install help

# Run the bot directly
run:
	python3 -m bot.main

# Docker commands
build:
	docker build -t manchatbot .

start:
	docker run -d --name manchatbot --env-file .env -v "$(shell pwd)/bot/core/app_state.json:/app/bot/core/app_state.json" manchatbot

stop:
	docker stop manchatbot || true

remove:
	docker rm manchatbot || true

restart: stop remove start

logs:
	docker logs -f manchatbot

clean: stop remove
	@echo "Stopped and removed container"

# Development commands
install:
	pip install -r requirements.txt

# Help command
help:
	@echo "Available commands:"
	@echo "  run       - Run the bot directly"
	@echo "  build     - Build the Docker image"
	@echo "  start     - Run the bot in a Docker container"
	@echo "  stop      - Stop the running container"
	@echo "  restart   - Restart the container"
	@echo "  logs      - View container logs"
	@echo "  clean     - Stop and remove the container"
	@echo "  install   - Install Python dependencies"
	@echo "  help      - Show this help message"
