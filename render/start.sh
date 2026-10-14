#!/bin/sh
# Starts every Zenvy service inside one container (Render free plan).
# If any of them dies, the container exits so Render restarts it.

: "${PORT:=10000}"

# The services find each other on localhost.
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export STT_URL="${STT_URL:-http://127.0.0.1:8001/transcribe}"
export TTS_URL="${TTS_URL:-http://127.0.0.1:8005/synthesize}"
export TEAM_C_BASE_URL="${TEAM_C_BASE_URL:-http://127.0.0.1:8002}"

for name in DATABASE_URL SARVAM_API_KEY AUTH_TOKEN_SECRET AUDIO_ENCRYPTION_KEY; do
  eval "value=\${$name}"
  if [ -z "$value" ]; then
    echo "[start] missing required environment variable: $name" >&2
    exit 1
  fi
done

echo "[start] redis"
redis-server --save "" --appendonly no --bind 127.0.0.1 --port 6379 --daemonize no --loglevel warning &

cd /app/team_c
echo "[start] applying database migrations (only the ones not applied yet)"
python -m app.db.init_db || { echo "[start] migrations failed" >&2; exit 1; }

if [ -n "$SEED_DOCTOR_PASSWORD" ]; then
  echo "[start] seeding hospitals, doctors and doctor logins (safe to repeat)"
  python -m app.db.seed_doctors || echo "[start] seeding failed (continuing)" >&2
fi

echo "[start] data service (team-c) on 8002"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 &

cd /app
echo "[start] speech-to-text on 8001, text-to-speech on 8005"
python -m uvicorn services.stt.main:app --host 127.0.0.1 --port 8001 &
python -m uvicorn services.tts.main:app --host 127.0.0.1 --port 8005 &

echo "[start] waiting for the internal services"
for url in http://127.0.0.1:8002/healthz http://127.0.0.1:8001/health http://127.0.0.1:8005/health; do
  n=0
  until python -c "import urllib.request,sys; urllib.request.urlopen('$url', timeout=2)" 2>/dev/null; do
    n=$((n + 1))
    if [ "$n" -gt 60 ]; then echo "[start] $url did not come up" >&2; exit 1; fi
    sleep 1
  done
done

echo "[start] gateway on port $PORT"
python -m uvicorn services.gateway.main:app --host 0.0.0.0 --port "$PORT" &
GATEWAY_PID=$!

# Exit as soon as ANY child stops, so the platform restarts the whole container.
wait -n 2>/dev/null || wait "$GATEWAY_PID"
echo "[start] a service stopped; exiting" >&2
exit 1
