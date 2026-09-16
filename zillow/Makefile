.DEFAULT_GOAL := run

PYTHON ?= python3
ARGS ?=

.PHONY: run setup inventory migrate

# Install only when the environment is missing or requirements have changed.
.venv/.installed: requirements.txt
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements.txt
	@touch $@

setup: .venv/.installed

# Resume the named Crawlee queue with bounded retries.
run: setup
	.venv/bin/python run.py $(ARGS)

# Refresh the official parcel inventory without browsing Zillow.
inventory: setup
	.venv/bin/python inventory.py

# Refuse to change data while make run owns the crawl lock.
migrate: setup
	.venv/bin/python migrate.py
