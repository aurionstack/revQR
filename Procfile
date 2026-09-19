release: alembic upgrade head
web: newrelic-admin run-program uvicorn app.main:app --host=0.0.0.0 --port=${PORT:-5000} --proxy-headers --forwarded-allow-ips="*"
