# Cài đặt mặc định với PostgreSQL và Cloudflare R2

Bản fork này chọn PostgreSQL cho dữ liệu và tìm kiếm, Cloudflare R2 (qua S3 API) cho nội dung thư và tệp đính kèm trong **thiết lập lần đầu**. Build mặc định bật `postgres` và `s3`, đồng thời giữ RocksDB để người dùng có thể chọn backend khác trong màn hình thiết lập.

Các mặc định này không tự chuyển dữ liệu của hệ thống đã cài. Thay đổi backend của hệ thống đang có dữ liệu cần quy trình di chuyển dữ liệu.

## Thông tin cần chuẩn bị

Tạo một database PostgreSQL và user có quyền tạo bảng trong database đó. Tạo một bucket R2 và credentials S3 có quyền Object Read & Write.

| Biến môi trường | Giá trị mặc định / ý nghĩa |
|---|---|
| `STALWART_POSTGRES_HOST` | `localhost`; Docker Compose mẫu đặt thành `postgres` |
| `STALWART_POSTGRES_PORT` | `5432`; giá trị không hợp lệ được báo ở bước validation |
| `STALWART_POSTGRES_DATABASE` | `stalwart` |
| `STALWART_POSTGRES_USER` | `stalwart` |
| `STALWART_POSTGRES_PASSWORD` | Mật khẩu database |
| `STALWART_R2_ACCOUNT_ID` | Dùng để tạo endpoint R2 thông thường |
| `STALWART_R2_ENDPOINT` | Nếu có, ưu tiên hơn account ID; dùng đúng S3 API endpoint của bucket |
| `STALWART_R2_BUCKET` | Tên bucket |
| `STALWART_R2_ACCESS_KEY_ID` | Access Key ID của credentials S3 |
| `STALWART_R2_SECRET_ACCESS_KEY` | Secret Access Key của credentials S3 |

Region mặc định là `auto`. Endpoint thông thường có dạng `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. Với bucket có jurisdiction riêng, dùng endpoint chính xác do Cloudflare cung cấp.

Thông tin kết nối được điền sẵn từ môi trường khi Bootstrap được đọc. Mật khẩu và khóa được lưu dưới dạng **tham chiếu biến môi trường**, không đưa giá trị secret vào phản hồi mặc định. Nếu dùng các tham chiếu này, tiếp tục cấp các biến credential cho service/container sau mỗi lần khởi động lại.

Có thể sửa host, port, database, TLS, pool, bucket, endpoint hoặc chuyển nguồn credential sang giá trị trực tiếp/file trong màn hình thiết lập. Có thể chọn backend khác tại đây. PostgreSQL mặc định dùng kết nối nội bộ không TLS; bật TLS và giữ xác minh chứng chỉ khi kết nối database từ xa.

Trước khi hoàn tất thiết lập với S3/R2, server ghi một object thử có khóa ngẫu nhiên, đọc và so sánh dữ liệu, rồi xóa object. Lỗi được trả về ở trường blob store, và cấu hình local chưa được ghi. Phép thử có giới hạn thời gian và cũng thử xóa object sau lỗi ghi/đọc.

## Docker Compose

Từ thư mục gốc repository:

```sh
cp resources/deployment/.env.example resources/deployment/.env
chmod 600 resources/deployment/.env
```

Điền mật khẩu PostgreSQL và thông tin R2 trong file vừa tạo. File này được gitignore.

```sh
docker compose --env-file resources/deployment/.env -f resources/deployment/compose.yaml up -d --build
```

Compose build Dockerfile của chính fork này, chạy PostgreSQL với volume riêng, và chờ database healthy trước khi khởi động server. Mở `http://<server>:8080/admin`, dùng mật khẩu bootstrap trong log để hoàn tất thiết lập:

```sh
docker compose --env-file resources/deployment/.env -f resources/deployment/compose.yaml logs server
```

Cấu hình lưu trong volume `server-config`; PostgreSQL lưu trong `postgres-data`. Các cổng mail và HTTPS được khai báo trong Compose. Cấu hình domain, DNS và TLS theo hệ thống của bạn trong bước thiết lập.

## Linux: cài từ source build

Khi fork chưa có release, dùng binary build từ chính repository này. Cần Rust stable và các công cụ build, bao gồm libclang cho RocksDB.

```sh
cargo build --release -p stalwart
cp resources/deployment/.env.example resources/deployment/.env
chmod 600 resources/deployment/.env
```

Sửa `STALWART_POSTGRES_HOST` thành `localhost` hoặc host PostgreSQL thực tế; điền password và R2 credentials. Cài bằng:

```sh
sudo sh install.sh --binary target/release/stalwart --env-file resources/deployment/.env
```

Script chép file môi trường vào `/etc/stalwart/stalwart.env` trong lần cài đầu, cấp quyền `0640`, và khởi động service. Khi cài lại, script giữ nguyên file môi trường đã có. Muốn cập nhật kết nối/credential sau đó, sửa file của service và khởi động lại; cập nhật cấu hình storage đã lưu qua admin UI khi cần.

Có thể thêm `PREFIX` cuối lệnh để chọn vị trí cài. Các mặc định storage áp dụng trên cả Linux, MacOS, FreeBSD và Docker khi chạy binary của fork.

## Linux: cài từ release

`install.sh` mặc định tải từ `lehuunghi/server/releases/latest/download`; cần có release và asset phù hợp với kiến trúc. Script không tự dùng binary Stalwart upstream khi fork chưa có release.

```sh
sudo sh install.sh --env-file resources/deployment/.env
```

Có thể thay nguồn tải bằng `STALWART_DOWNLOAD_BASE_URL`. Nguồn tải phải chứa binary đã build với các thay đổi của fork nếu muốn các mặc định PostgreSQL/R2 này.

## Kiểm thử

```sh
python3 resources/scripts/test_install.py
cargo check -p stalwart
cargo test -p stalwart -p jmap bootstrap_defaults_tests -- --nocapture
STORE=PostgreSql BLOB_STORE=S3 cargo test -p tests --features "postgres s3" store:: -- --nocapture
```

Workflow `PostgreSQL and R2 defaults` chạy các kiểm tra này khi push lên main. Integration tests dùng PostgreSQL và MinIO trong container dùng riêng; không dùng bucket R2 thật. Cần kiểm tra thêm gửi/đọc thư có tệp đính kèm và khởi động lại server với PostgreSQL/R2 thực tế trước khi đưa hệ thống vào sử dụng.

Ở phiên bản này, `config.json` local chứa DataStore, còn blob/search nằm trong registry. Không chép toàn bộ Bootstrap hoặc cấu hình TOML của bản cũ vào file này.
