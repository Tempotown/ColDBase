# This repository builds ZeroClaw from vendored upstream source in vendor/zeroclaw/.
# Use one of:
#   docker compose build zeroclaw
#   docker build -f vendor/zeroclaw/Dockerfile --target dev vendor/zeroclaw

FROM alpine:3.21

RUN echo "This repo no longer builds from the root Dockerfile." >&2 \
 && echo "Use: docker compose build zeroclaw" >&2 \
 && exit 1
