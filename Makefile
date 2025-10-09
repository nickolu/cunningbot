.PHONY: run build start stop restart logs clean install help

# Run the bot directly
run:
	python3 -m bot.main

up:
	docker compose up -d

down:
	docker compose down


build:
	docker compose build --no-cache

start:
	docker compose up -d

stop:
	docker compose down || true

remove:
	docker compose down || true

restart: stop remove start

rebuild: stop remove build start

logs:
	docker compose logs -f

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
