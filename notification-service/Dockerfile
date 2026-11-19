FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto ./proto
COPY db.py server.py ./

ENV PYTHONUNBUFFERED=1 GRPC_PORT=50051
EXPOSE 50051

CMD ["python", "server.py"]
