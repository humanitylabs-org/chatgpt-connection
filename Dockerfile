# syntax=docker/dockerfile:1.10
# Test candidate carrier only; the app's release recipe remains runtime.package=source.
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.source=https://github.com/humanitylabs-org/chatgpt-connection \
      org.opencontainers.image.revision=$SOURCE_REVISION \
      org.wizard.candidate-only=true
WORKDIR /app
COPY server.py inference.py codex.py account_pool.py SKILL.md LICENSE ./
COPY static ./static
USER 1000:1000
ENV APP_DATA_DIR=/data WIZARD_CHATGPT_BIND=0.0.0.0 WIZARD_CHATGPT_PORT=8801 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8801
CMD ["python3", "/app/server.py"]
