FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# To enable the Claude CLI backend, install it and set CLAUDE_CLI_PATH:
#   RUN apt-get update && apt-get install -y nodejs npm \
#    && npm install -g @anthropic-ai/claude-code
# (left out by default; the service falls back to Gemini or the mock backend.)

COPY . .

EXPOSE 8082

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8082"]
