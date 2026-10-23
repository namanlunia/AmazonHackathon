.PHONY: venv install run sim test lint fmt

VENV = .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
UVICORN = $(VENV)/bin/uvicorn
PYTEST = $(VENV)/bin/pytest
RUFF = $(VENV)/bin/ruff

venv:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

install:
	$(PIP) install -r requirements-dev.txt

run:
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000

sim:
	$(PYTHON) scripts/simulate_mesh.py

test:
	$(PYTEST) -q

lint:
	$(RUFF) check .

fmt:
	$(RUFF) format .
