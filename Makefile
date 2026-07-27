.PHONY: up down restart rebuild reset-data logs ps \
        restart-api restart-streamlit restart-prometheus restart-grafana \
        stop-api stop-streamlit stop-prometheus stop-grafana \
        start-api start-streamlit start-prometheus start-grafana

## Bring up the full stack (build if needed) and print access links once ready.
up:
	docker compose up --build -d
	@bash scripts/print_links.sh

## Stop and remove all containers (keeps named volumes: DB, uploads, Grafana state).
down:
	docker compose down

## Full restart of everything.
restart: down up

## Force a clean image rebuild with no Docker layer cache, then start.
rebuild:
	docker compose build --no-cache
	docker compose up -d
	@bash scripts/print_links.sh

## Wipe all documents, review-queue entries, and uploaded images -- for a
## clean slate before a demo recording. Also clears Grafana's own storage,
## but the dashboard/data-source are auto-reprovisioned from the JSON/YAML
## files on next boot, so nothing needs to be manually reconfigured.
## No rebuild happens here since code isn't changing, just data.
reset-data:
	docker compose down -v
	docker compose up -d
	@bash scripts/print_links.sh

## Tail logs from all services. Ctrl+C to stop watching (containers keep running).
logs:
	docker compose logs -f

## Show container status.
ps:
	docker compose ps

# --- Individual service control -------------------------------------------
# Each of these targets only touches the one named service; the rest of
# the stack keeps running untouched.

stop-api:
	docker compose stop api

stop-streamlit:
	docker compose stop streamlit

stop-prometheus:
	docker compose stop prometheus

stop-grafana:
	docker compose stop grafana

start-api:
	docker compose up -d --build api

start-streamlit:
	docker compose up -d --build streamlit

start-prometheus:
	docker compose up -d prometheus

start-grafana:
	docker compose up -d grafana

restart-api:
	docker compose up -d --build api

restart-streamlit:
	docker compose up -d --build streamlit

restart-prometheus:
	docker compose up -d prometheus

restart-grafana:
	docker compose up -d grafana
