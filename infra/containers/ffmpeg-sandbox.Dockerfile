FROM jrottenberg/ffmpeg:6.1-alpine

RUN addgroup -S -g 65532 nbu && adduser -S -D -H -u 65532 -G nbu nbu

USER 65532:65532
WORKDIR /work

ENTRYPOINT ["ffmpeg"]
