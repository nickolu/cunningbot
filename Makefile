.PHONY: run build start stop restart logs clean install help

# Run the bot directly
run:
	python3 -m bot.main

# Docker commands
build:
	docker build -t manchatbot-1 .

start:
	docker run -d --name manchatbot-1 --env-file .env -v "$(shell pwd)/bot/core/app_state.json:/app/bot/core/app_state.json" manchatbot-1

stop:
	docker stop manchatbot-1 || true

remove:
	docker rm manchatbot-1 || true

restart: stop remove start

rebuild: stop remove build start

logs:
	docker logs -f manchatbot-1

clean: stop remove
	@echo "Stopped and removed container"

# Development commands
install:
	pip install -r requirements.txt

up:
	./docker-compose up -d

down:
	./docker-compose down

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
