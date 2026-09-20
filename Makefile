.PHONY: setup data test demo clean

VENV := .venv
PY := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

setup:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements.txt

data:
	$(PY) scripts/make_packets.py
	$(PY) scripts/seed_db.py

test:
	LLM_MODE=replay $(PY) -m pytest tests/ -v

demo:
	LLM_MODE=replay $(PY) -m uvicorn app:app --host 0.0.0.0 --port 8000

clean:
	rm -f coop.db
	rm -rf data/packets/*/*.png data/packets/*/*.pdf
