FROM docker.io/library/alpine:3@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

LABEL org.opencontainers.image.title="alpine3"
LABEL org.opencontainers.image.base.name="alpine:3"

ARG IMAGE_SOURCE
ARG IMAGE_REVISION
ARG IMAGE_CREATED

LABEL org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${IMAGE_REVISION}" \
      org.opencontainers.image.created="${IMAGE_CREATED}"

ENV LANG="C.UTF-8"
ENV LC_ALL="C.UTF-8"

SHELL ["/bin/ash", "-eo", "pipefail", "-c"]

RUN set -eux \
    && apk add --no-cache \
        ca-certificates \
        curl \
        dumb-init \
        tzdata \
    && update-ca-certificates

# Dedicated runtime user/group
RUN addgroup -g 10001 -S app \
    && adduser \
        -S \
        -D \
        -H \
        -u 10001 \
        -G app \
        -s /sbin/nologin \
        app

# Runtime directories
RUN mkdir -p /app /tmp \
    && chmod 1777 /tmp \
    && chown -R app:app /app

WORKDIR /app

USER app

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/bin/sh"]
