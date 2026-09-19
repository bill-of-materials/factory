FROM ghcr.io/bill-of-materials/alpine3:latest@sha256:9eee99e4d67d44850ef7bb0681ad8cb746f565d9cd47bce8da5ddcf986a163f2

LABEL org.opencontainers.image.title="ghcr.io/bill-of-materials/ninjam"
LABEL org.opencontainers.image.base.name="ghcr.io/bill-of-materials/alpine3"

ARG IMAGE_SOURCE
ARG IMAGE_REVISION
ARG IMAGE_CREATED
ARG NINJAM_REPO="https://github.com/justinfrankel/ninjam"
ARG NINJAM_REV=f4c0eff

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
