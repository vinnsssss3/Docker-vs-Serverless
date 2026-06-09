# Dockerfile: always-on Flask service used as the Docker-side benchmark target.
# Kept intentionally minimal so the comparison isolates the architectural
# behaviour (always-on vs cold-start) rather than image bloat or unrelated
# runtime overhead.

FROM python:3.11-slim

WORKDIR /app

# Install only what the service actually needs at runtime.
COPY docker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Identical core workload, identical Flask wrapper.
COPY core/ /app/core/
COPY docker/app.py /app/app.py

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

# The CMD form keeps the process as PID 1; Docker's restart policy will
# then determine whether the container is replaced or kept alive.
CMD ["python", "app.py"]
