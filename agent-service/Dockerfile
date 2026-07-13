FROM python:3.11-slim

ARG TORCH_VERSION=2.13.0+cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY app/__init__.py ./app/__init__.py
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      "torch==${TORCH_VERSION}"
RUN pip install --no-cache-dir .
COPY app ./app
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
