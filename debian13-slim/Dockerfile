FROM docker.io/library/debian:trixie-slim@sha256:4e401d95de7083948053197a9c3913343cd06b706bf15eb6a0c3ccd26f436a0e

LABEL org.opencontainers.image.title="debian13-slim"
LABEL org.opencontainers.image.base.name="debian:trixie-slim"

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

# create a non-login, non-root runtime user explicitly (more predictable than "nobody")
RUN useradd \
    --system \
    --uid 10001 \
    --gid 10001 \
    --no-create-home \
    --home-dir /nonexistent \
    --shell /usr/sbin/nologin \
    app

USER app

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["bash"]
