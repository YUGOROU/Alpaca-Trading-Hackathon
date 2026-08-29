# Agent image — deps baked in so scheduled runs never reinstall (README §6).
FROM python:3.12-slim

WORKDIR /app

# Install deps first (cached layer — only rebuilds when requirements change).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the code.
COPY . .

# State + ledger live on a mounted volume so they persist across runs.
ENV AGENT_STATE_PATH=/data/state.json \
    AGENT_LEDGER_PATH=/data/ledger.jsonl \
    LOOP_INTERVAL_SECONDS=1800 \
    WEBUI_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

# Default: the always-on scheduler. Override with `python run_webui.py` for the UI.
CMD ["python", "loop.py"]
