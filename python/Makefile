# XActions Growth Bot — setup & run
#
# One-command setup:  make install
#   1. installs Python dependencies
#   2. installs the Chromium browser (Playwright)
#   3. opens Chromium so you can log in to X — the session is saved automatically
#
# After `make install`, just edit .env + your persona files and run `make run`.

PY ?= python

.PHONY: help install deps browser login run dry stop status clean

# Default target
help:
	@echo "XActions Growth Bot"
	@echo ""
	@echo "  make install   One-shot setup: deps + browser + X login (do this first)"
	@echo "  make deps      Install Python dependencies only"
	@echo "  make browser   Install the Chromium browser only"
	@echo "  make login     Open Chromium to log in to X and save the session"
	@echo ""
	@echo "  make run       Start the Growth Bot"
	@echo "  make dry       Test run — generates but does NOT post (verify your persona)"
	@echo "  make stop      Stop a running Growth Bot"
	@echo "  make status    Show today's safety usage (likes/comments/follows/posts)"
	@echo ""
	@echo "First run:  cp .env.example .env  →  edit .env  →  make install  →  make run"

# ── One-command setup ────────────────────────────────────────────────────────
install: deps browser login
	@echo ""
	@echo "✅ Setup complete. Edit your persona files in config/, then run:  make run"

# ── Individual steps ─────────────────────────────────────────────────────────
deps:
	@echo "📦 Installing Python dependencies…"
	$(PY) -m pip install -r xeepy/requirements.txt

browser:
	@echo "🌐 Installing Chromium (Playwright)…"
	$(PY) -m playwright install chromium
	@echo "   (If you hit missing-library errors on Linux, run: $(PY) -m playwright install-deps chromium)"

login:
	@echo "🔐 Launching login…"
	$(PY) setup_login.py

# ── Run / manage ─────────────────────────────────────────────────────────────
run:
	@if [ ! -f .env ]; then echo "❌ No .env found. Run: cp .env.example .env  and fill it in."; exit 1; fi
	@if [ ! -f data/session.json ]; then echo "❌ No session found. Run: make login"; exit 1; fi
	$(PY) growth_bot.py

dry:
	$(PY) growth_bot.py --dry-run --no-headless

stop:
	@pkill -f "$(PY) growth_bot.py" 2>/dev/null && echo "🛑 Stopped." || echo "Nothing running."

status:
	$(PY) -m xeepy.safety_monitor --status

clean:
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned caches."
