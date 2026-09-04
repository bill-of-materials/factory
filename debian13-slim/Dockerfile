FROM docker.io/library/debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

LABEL org.opencontainers.image.title="ghcr.io/bill-of-materials/debian13-slim"
LABEL org.opencontainers.image.base.name="ghcr.io/bill-of-materials/debian:trixie-slim"

ARG IMAGE_SOURCE
ARG IMAGE_REVISION
ARG IMAGE_CREATED

LABEL org.opencontainers.image.source=$IMAGE_SOURCE
LABEL org.opencontainers.image.revision=$IMAGE_REVISION
LABEL org.opencontainers.image.created=$IMAGE_CREATED

ENV DEBIAN_FRONTEND=noninteractive
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN set -euxo pipefail \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      dumb-init \
      tzdata \
  && update-ca-certificates \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN set -euxo pipefail \
  && groupadd --gid 10001 app \
  && useradd \
    --system \
    --uid 10001 \
    --gid app \
    --create-home \
    --home-dir /app \
    --shell /usr/sbin/nologin \
    app

USER app

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["bash"]
