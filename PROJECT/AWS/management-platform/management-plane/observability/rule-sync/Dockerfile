FROM python:3.12-slim
WORKDIR /app
COPY management-plane/observability/rule-sync/sync.py /app/sync.py
RUN mkdir -p /rules && chown 65534:65534 /rules
USER 65534
ENTRYPOINT ["python3","-u","/app/sync.py"]
