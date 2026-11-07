FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 JOURNAL_PATH=/var/lib/sentinel/trades.db
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /var/lib/sentinel && chown app:app /app /var/lib/sentinel
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["gunicorn", "web:app", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "120"]
