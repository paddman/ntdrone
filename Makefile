.PHONY: install dev worker test lint up down logs seed

install:
	python -m pip install -e '.[dev]'

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	python -m app.worker

test:
	pytest

lint:
	ruff check app tests scripts

up:
	cp -n .env.example .env || true
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker

seed:
	python scripts/seed_demo.py
