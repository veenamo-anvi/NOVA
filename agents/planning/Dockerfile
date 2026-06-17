FROM python:3.12-slim

WORKDIR /app

# pulp bundles the CBC solver binary; no extra system packages needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8081

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
