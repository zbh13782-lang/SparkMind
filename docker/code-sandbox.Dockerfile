FROM python:3.14-slim

RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin sandbox
USER 65532:65532
WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
