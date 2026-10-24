.PHONY: dev backend frontend test e2e seed reset health preflight call agent clean install

PY := ./.venv/Scripts/python.exe

install:
	$(PY) -m pip install -r backend/requirements.txt
	cd frontend && npm install

backend:
	$(PY) -m uvicorn app.main:app --app-dir backend --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	$(PY) -m pytest backend/tests -q

e2e:
	$(PY) scripts/e2e_demo.py

seed reset:
	$(PY) -c "import sys;sys.path.insert(0,'backend');from app.db import init_db;from app.services.demo_reset import reset;init_db();print(reset())"

health:
	curl -s http://localhost:8000/health && echo && curl -s http://localhost:8000/api/status

preflight:
	$(PY) scripts/discover_cartesia.py

agent:
	$(PY) scripts/setup_cartesia_agent.py

call:
	$(PY) scripts/smoke_call.py

clean:
	rm -f stillhere.db stillhere.db-wal stillhere.db-shm
