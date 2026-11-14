.DEFAULT_GOAL := help
COMPOSE := docker compose
API := http://localhost:8000/api
SIM := http://localhost:8090/api
JSON := -H 'content-type: application/json'
CELL ?= cell-1
ROBOT ?= robot-1

.PHONY: help up down reset logs ps test lint e2e enroll jobs jobs-invalid onprem-stop onprem-start person person-at-c clear feed-off feed-on unplug plug

help: ## Show this list
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

up: ## Build and start everything
	$(COMPOSE) up --build -d
	@echo
	@echo "  web app         http://localhost:3000"
	@echo "  site simulator  http://localhost:8090"
	@echo "  cloud api       $(API)/health"

down: ## Stop everything and keep the data
	$(COMPOSE) down

reset: ## Stop everything and delete all data
	$(COMPOSE) down -v

logs: ## Follow the logs of every service
	$(COMPOSE) logs -f --tail=50

ps: ## Show service status
	$(COMPOSE) ps

test: ## Run the unit tests
	uv run pytest

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

e2e: ## Run the end to end checks against the running stack
	uv run python scripts/e2e.py

enroll: ## Ask the site to enroll both robots
	@curl -fsS -X POST $(API)/robots/robot-1/enroll > /dev/null
	@curl -fsS -X POST $(API)/robots/robot-2/enroll > /dev/null
	@echo "enrollment requested for robot-1 and robot-2"

jobs: ## Send N random jobs across the enrolled robots, N=6
	@uv run python scripts/submit_jobs.py --count $(or $(N),6)

jobs-invalid: ## Send jobs the site has to reject
	@uv run python scripts/submit_jobs.py --invalid

onprem-stop: ## Stop the on-prem server
	$(COMPOSE) stop onprem

onprem-start: ## Start the on-prem server again
	$(COMPOSE) start onprem

person: ## Walk a person into a cell, CELL=cell-1 or cell-2
	@curl -fsS -X POST $(SIM)/people $(JSON) -d '{"cell":"$(CELL)"}'; echo

person-at-c: ## Walk a person to shared station C
	@curl -fsS -X POST $(SIM)/people $(JSON) -d '{"station":"C"}'; echo

clear: ## Everyone walks out of the cells
	@curl -fsS -X POST $(SIM)/people/leave; echo

feed-off: ## Stop the fusion feed
	@curl -fsS -X POST $(SIM)/feed $(JSON) -d '{"on":false}'; echo

feed-on: ## Start the fusion feed again
	@curl -fsS -X POST $(SIM)/feed $(JSON) -d '{"on":true}'; echo

unplug: ## Cut a robot's link, ROBOT=robot-1
	@curl -fsS -X POST $(SIM)/robots/$(ROBOT)/fault $(JSON) -d '{"connected":false}'; echo

plug: ## Restore a robot's link, ROBOT=robot-1
	@curl -fsS -X POST $(SIM)/robots/$(ROBOT)/fault $(JSON) -d '{"connected":true}'; echo
