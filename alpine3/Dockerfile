FROM docker.io/library/alpine:3@sha256:5b02b42e375f7426f8d65c3af331ca05d9878f9989230354504e0b9dfd431f60

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
