.PHONY: dev backend frontend test e2e seed reset health preflight call agent clean install stdb stdb-server stdb-sql chaos verify demo-up arm sim api-contracts

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

# ---- SpacetimeDB ----
STDB := /c/Users/User/AppData/Local/SpacetimeDB/spacetime.exe

stdb-server:
	$(STDB) start --listen-addr 127.0.0.1:3000

stdb:
	$(PY) scripts/stdb_setup.py

stdb-sql:
	cd spacetime && $(STDB) sql --server local stillhere "SELECT * FROM plan_proposals"

chaos:
	$(PY) scripts/chaos_check.py

# Full acceptance gate: unit + e2e + chaos
verify:
	$(PY) -m pytest backend/tests -q
	$(PY) scripts/e2e_demo.py
	$(PY) scripts/chaos_check.py

api-contracts:
	$(PY) scripts/gen_api_contracts.py

# ---- demo operation ----
# Bring everything up from cold (idempotent).
demo-up:
	$(PY) scripts/demo_up.py

# Verify a real call is possible, without placing one.
arm:
	$(PY) scripts/call_now.py --check

# Rehearse the call in the terminal. No provider cost.
sim:
	$(PY) scripts/sim_call.py
