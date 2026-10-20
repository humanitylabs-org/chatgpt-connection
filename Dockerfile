# App-owned ChatGPT OAuth and private fixed Codex gateway (stdlib only).
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WIZARD_CHATGPT_PORT=8801
LABEL org.opencontainers.image.source="https://github.com/humanitylabs-org/wizard-app-store" \
      org.opencontainers.image.description="App-owned ChatGPT login, renewal and box-only Codex gateway. Reports wizard.ai-connection/2 status." \
      org.opencontainers.image.licenses="MIT"
RUN mkdir -p /app/static
COPY --chmod=0444 server.py codex.py inference.py SKILL.md LICENSE /app/
COPY --chmod=0444 static/ /app/static/
USER 1000:1000
WORKDIR /app
EXPOSE 8801
CMD ["python3", "/app/server.py"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python3", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('WIZARD_CHATGPT_PORT','8801')+'/health', timeout=3).read()"]
