# Tự động xuất bản container lên GHCR

Image của repository này: `ghcr.io/lehuunghi/server`.

Workflow [Publish GHCR](https://github.com/lehuunghi/server/actions/workflows/ghcr.yml)
build trực tiếp từ mã nguồn trong repository, sử dụng Dockerfile ở thư mục gốc.
Không cần token cá nhân, Docker Hub hay registry Gitea.

## Khi nào chạy

- Push hoặc merge vào `main`: xuất bản `latest`, `main` và `sha-<commit>`.
- Push tag bắt đầu bằng `v`: xuất bản tag gốc, tag bỏ tiền tố `v` và tag commit.
  Ví dụ `v1.2.3` tạo `v1.2.3`, `1.2.3`, `sha-<commit>`.
- Chạy thủ công bằng **Actions → Publish GHCR → Run workflow**.
  Chạy từ nhánh khác không ghi đè `latest`.
- Pull request không xuất bản image. Workflow CI vẫn kiểm tra mã nguồn.
- `latest` luôn theo lần build thành công của nhánh mặc định, không bị tag cũ ghi đè.

Hai kiến trúc `linux/amd64` và `linux/arm64` được build trên runner tương ứng.
Chỉ sau khi cả hai build thành công, workflow mới cập nhật các tag.
Tên image, tag và digest được hiển thị trong phần Summary của lượt chạy.

## Tải image

```sh
docker pull ghcr.io/lehuunghi/server:latest
```

GHCR tạo package mới ở chế độ riêng tư mặc định. Nếu cần tải không đăng nhập,
sau lần xuất bản đầu tiên vào **GitHub → Packages → server → Package settings →
Change visibility → Public**. Nếu giữ riêng tư, đăng nhập GHCR bằng tài khoản có
quyền đọc package trước khi pull.

Workflow dùng `GITHUB_TOKEN` với `contents: read` và `packages: write`.
Nhãn `org.opencontainers.image.source` liên kết image với repository.
Nếu đã tạo package cùng tên bằng token khác, cấp quyền Actions của repository
trong trang **Package settings → Manage Actions access**.

## Khi fork chưa chạy Actions

Mở tab **Actions** và bật workflow nếu GitHub đang tắt Actions cho fork.
Sau đó chọn **Publish GHCR → Run workflow** trên `main`.
Nếu repository áp dụng giới hạn Actions hoặc Packages, kiểm tra lỗi trong lượt chạy;
không cần thêm `BUILD_ON`, `GITEA_TOKEN` hay token Docker Hub cho workflow này.

Image giữ các tính năng PostgreSQL và S3/R2 cùng cấu hình mặc định của bản fork. Pipeline phát hành binary kế thừa vẫn chạy thủ công bằng workflow CI.
