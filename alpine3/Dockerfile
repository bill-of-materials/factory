FROM docker.io/library/alpine:3@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

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

RUN set -x \
  && apk add --no-cache \
    ca-certificates \
    curl \
    dumb-init \
  && update-ca-certificates

USER nobody

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
