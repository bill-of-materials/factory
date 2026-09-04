FROM ghcr.io/bill-of-materials/alpine3:latest@sha256:f9fa5247b549a8c3fd30ff6af09e3e3a9b0eed76123f686535048f247a6f54c9

LABEL org.opencontainers.image.title="ghcr.io/bill-of-materials/snapcast-server"
LABEL org.opencontainers.image.base.name="ghcr.io/bill-of-materials/alpine3"

ARG IMAGE_SOURCE
ARG IMAGE_REVISION
ARG IMAGE_CREATED
ARG SNAPCAST_VERSION=0.35.0-r1

LABEL org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${IMAGE_REVISION}" \
      org.opencontainers.image.created="${IMAGE_CREATED}"

USER root

# Alpine edge is required because snapcast-server is currently packaged there.
RUN set -eux \
    && printf '%s\n' \
        'https://dl-cdn.alpinelinux.org/alpine/edge/main' \
        'https://dl-cdn.alpinelinux.org/alpine/edge/community' \
        > /etc/apk/repositories \
    && apk add --no-cache \
        "snapcast-server=${SNAPCAST_VERSION}" \
    && mkdir -p /app /run/snapcast \
    && chown -R app:app /app /run/snapcast

USER app

WORKDIR /app

ENTRYPOINT ["/usr/bin/dumb-init", "--"]

CMD ["/usr/bin/snapserver", "-c", "/app/snapserver.conf"]
