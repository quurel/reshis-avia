FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0
COPY app.py models.py forecast.py ./
COPY data/ ./data/
COPY templates/ ./templates/
COPY static/ ./static/
CMD ["sh", "-c", "python -m flask --app app:create_app init-db && exec gunicorn --bind 0.0.0.0:${PORT:-8080} 'app:create_app()'"]
