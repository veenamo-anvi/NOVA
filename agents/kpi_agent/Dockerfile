FROM python:3.12-slim

WORKDIR /app

# CPU-only PyTorch wheel keeps the image small and the build fast.
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir influxdb-client==1.48.0 httpx==0.28.1

COPY . .

# Update CMD entrypoint: trains on first boot if MODEL_PATH is absent.
CMD ["python", "-u", "kpi_agent.py"]
