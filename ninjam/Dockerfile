FROM ghcr.io/bill-of-materials/alpine3:latest@sha256:f270aba2d86db2b289df0a9d167d4b306bd45f419dd09a8dc7fbec275b6e526c

LABEL org.opencontainers.image.title="ghcr.io/bill-of-materials/ninjam"
LABEL org.opencontainers.image.description="NINJAM server (ninjamsrv) built from source on the alpine3 base image."
LABEL org.opencontainers.image.base.name="ghcr.io/bill-of-materials/alpine3"

ARG IMAGE_SOURCE
ARG IMAGE_REVISION
ARG IMAGE_CREATED
ARG NINJAM_REPO="https://github.com/justinfrankel/ninjam"
# renovate: datasource=git-refs depName=https://github.com/justinfrankel/ninjam branch=main
ARG NINJAM_REV=f4c0eff3a1d8d5f3ead470d0c7405c3ee37da24e

LABEL org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${IMAGE_REVISION}" \
      org.opencontainers.image.created="${IMAGE_CREATED}"

USER root

RUN set -eux \
      && apk add --update git make g++ libstdc++ \
      && mkdir /tmp-build /app/config \
      && git clone "${NINJAM_REPO}" /tmp-build \
      && cd /tmp-build/ninjam/server && git checkout "${NINJAM_REV}" && make \
      && cp ninjamsrv /usr/bin \
      && cp example.cfg /app/config/ninjam_server.cfg \
      && cp cclicense.txt /app \
      && apk del git make g++ \
      && rm -rf /var/cache/apk/* /tmp-build

USER app

EXPOSE 2049

CMD ["/usr/bin/ninjamsrv", "/app/config/ninjam_server.cfg"]
