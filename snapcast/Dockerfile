FROM ghcr.io/bill-of-materials/alpine3:latest@sha256:f270aba2d86db2b289df0a9d167d4b306bd45f419dd09a8dc7fbec275b6e526c

LABEL org.opencontainers.image.title="ghcr.io/bill-of-materials/snapcast-server"
LABEL org.opencontainers.image.description="Snapcast server (snapserver) on the alpine3 base image."
LABEL org.opencontainers.image.base.name="ghcr.io/bill-of-materials/alpine3"

ARG IMAGE_SOURCE
ARG IMAGE_REVISION
ARG IMAGE_CREATED
# renovate: datasource=repology depName=alpine_edge/snapcast
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
