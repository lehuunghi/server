FROM golang:1.24.7-bookworm AS builder
RUN CGO_ENABLED=0 go install github.com/minio/minio@07c3a429bfed433e49018cb0f78a52145d4bedeb

FROM debian:bookworm-slim
COPY --from=builder /go/bin/minio /usr/local/bin/minio
ENTRYPOINT ["/usr/local/bin/minio"]
