# Cài đặt mặc định với PostgreSQL và Cloudflare R2

Bản fork này chọn PostgreSQL cho dữ liệu và tìm kiếm, Cloudflare R2 (qua S3 API) cho nội dung thư và tệp đính kèm trong **thiết lập lần đầu**. Build mặc định bật `postgres` và `s3`, đồng thời giữ RocksDB để người dùng có thể chọn backend khác trong màn hình thiết lập.

Các mặc định này không tự chuyển dữ liệu của hệ thống đã cài. Thay đổi backend của hệ thống đang có dữ liệu cần quy trình di chuyển dữ liệu.

## Một lệnh cài đặt tự động, có sẵn domain và thông tin quản trị/API

`install-auto.sh` dành cho VPS **Ubuntu/Debian amd64 hoặc arm64 mới**. Script tự cài dependency còn thiếu, build binary của fork trong Docker, tạo PostgreSQL với database/user/password, kiểm tra kết nối R2, đặt domain/hostname, tạo DKIM và quản trị viên, hoàn tất Bootstrap qua JMAP, rồi khởi động lại và kiểm tra đăng nhập/API. Không cần hoàn tất wizard trong trình duyệt hoặc tự cài Rust/PostgreSQL.

Lưu bản mẫu sau thành `/root/stalwart.json`, điền domain và S3 credentials của bucket R2 đã tạo. Đây là các thông tin riêng của bạn nên không đưa file đã điền vào GitHub. Domain/keys trong repository chỉ là mẫu.

```json
{
  "STALWART_DOMAIN": "example.com",
  "STALWART_HOSTNAME": "mail.example.com",
  "STALWART_R2_ACCOUNT_ID": "YOUR_CLOUDFLARE_ACCOUNT_ID",
  "STALWART_R2_BUCKET": "YOUR_R2_BUCKET",
  "STALWART_R2_ACCESS_KEY_ID": "YOUR_R2_ACCESS_KEY_ID",
  "STALWART_R2_SECRET_ACCESS_KEY": "YOUR_R2_SECRET_ACCESS_KEY",
  "STALWART_REQUEST_TLS_CERTIFICATE": true
}
```

Chạy lệnh sau trên VPS, từ tài khoản có quyền sudo:

```sh
curl -fsSL https://raw.githubusercontent.com/lehuunghi/server/main/install-auto.sh -o /tmp/stalwart-install-auto.sh && sudo sh /tmp/stalwart-install-auto.sh --config /root/stalwart.json
```

Nếu máy chưa có `curl`, dùng `wget -O /tmp/stalwart-install-auto.sh https://raw.githubusercontent.com/lehuunghi/server/main/install-auto.sh` rồi chạy phần `sudo sh ...` phía trên. Script tự cài các dependency còn lại. Từ checkout có sẵn: `sudo sh install-auto.sh --config /root/stalwart.json --source "$PWD"`.

Sau khi xong, script hiển thị:

| Thông tin | Ví dụ |
|---|---|
| Domain | `example.com` |
| Trang quản trị | `https://mail.example.com/admin` |
| Quản trị viên | `admin@example.com` |
| Mật khẩu | Sinh ngẫu nhiên và xác minh đăng nhập sau restart |
| JMAP session | `https://mail.example.com/jmap/session` |
| JMAP API | `https://mail.example.com/jmap/` |
| OAuth token endpoint | `https://mail.example.com/auth/token` |
| Xác thực API | HTTP Basic với tài khoản/mật khẩu quản trị, qua HTTPS |

Thông tin này được lưu vào `/opt/stalwart/credentials.json` với quyền `0600`; có thể xem lại bằng `sudo cat /opt/stalwart/credentials.json`. File `.env` và trạng thái cài đặt cũng có quyền `0600`. Mật khẩu bootstrap tạm được xóa khỏi môi trường trước khi tạo lại container. Không có bearer token được sinh sẵn; endpoint OAuth được cung cấp để ứng dụng thực hiện luồng cấp token phù hợp.

Chạy lại cùng lệnh và cùng JSON sẽ giữ nguyên mật khẩu/database và kiểm tra lại dịch vụ. Nếu JSON thay đổi, script dừng để tránh vô tình đổi password của PostgreSQL đang có dữ liệu. Muốn tùy chỉnh một hệ thống đã chạy, dùng trang quản trị; khi đổi credential môi trường, cập nhật `/opt/stalwart/.env` và trạng thái môi trường tương ứng trong `/opt/stalwart/deployment.json` trước khi chạy lại installer. Không xóa volume để cài lại hệ thống đang có dữ liệu.

Các tùy chọn:

- `--prefix /opt/stalwart`: chọn thư mục chứa cấu hình, source và credentials; mỗi prefix có tên Compose project riêng.
- `--source /path/to/server`: dùng checkout có sẵn, tránh clone lại.
- `--ref main`: chọn branch hoặc tag khi clone lần đầu; cài lại dùng source đã lưu.
- Bỏ `STALWART_HOSTNAME` để tự dùng `mail.<domain>`.
- Thay account ID bằng `STALWART_R2_ENDPOINT` khi cần endpoint jurisdiction riêng.
- PostgreSQL mặc định tự tạo nội bộ. Có thể điền thêm `STALWART_POSTGRES_HOST`, `STALWART_POSTGRES_PORT`, `STALWART_POSTGRES_DATABASE`, `STALWART_POSTGRES_USER`, `STALWART_POSTGRES_PASSWORD` để dùng một database trống có sẵn. Kết nối từ container dùng hostname/IP truy cập được, không dùng `localhost` của VPS.

**DNS vẫn cần thuộc quyền quản lý của bạn:** trỏ bản ghi A/AAAA của hostname về VPS, đặt MX cho domain và các bản ghi mail theo trang quản trị. Script không tự thay DNS Cloudflare vì R2 S3 credentials không có quyền DNS. ACME cần DNS đúng và cổng HTTPS 443 truy cập được; chứng chỉ hợp lệ có thể được cấp sau khi hoàn tất cài đặt. Cổng bootstrap 8080 chỉ công bố trên loopback; quản trị từ xa dùng HTTPS 443. Script kiểm tra dịch vụ HTTPS tại loopback với chứng chỉ khởi tạo, không coi việc đó là xác minh chứng chỉ công khai.

Lần build đầu có thể mất nhiều phút và cần đủ RAM/dung lượng cho Rust/RocksDB; script không phụ thuộc việc fork đã có release. PostgreSQL và cấu hình server nằm trong Docker volumes, có restart policy `unless-stopped`. Các cổng 25/443/465/587/143/993/4190 cần chưa bị dịch vụ khác chiếm.

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
python3 resources/scripts/test_auto_setup.py
cargo check -p stalwart
cargo test -p stalwart -p jmap bootstrap_defaults_tests -- --nocapture
STORE=PostgreSql BLOB_STORE=S3 cargo test -p tests --features "postgres s3" store:: -- --nocapture --test-threads=1
```

Workflow `PostgreSQL and R2 defaults` chạy các kiểm tra này khi push lên main, kiểm tra parser Compose cho cấu hình sinh tự động, và kiểm tra bootstrap/restart/đăng nhập/API với binary server thật. Integration tests dùng PostgreSQL và MinIO trong container dùng riêng; MinIO được build từ một commit upstream cố định, không dùng image Docker Hub `latest` đã ngừng phân phối. Chạy store tests tuần tự để tránh hai test tạo cùng bảng PostgreSQL. Không dùng bucket R2 thật. Cần kiểm tra thêm gửi/đọc thư có tệp đính kèm và khởi động lại server với PostgreSQL/R2 thực tế trước khi đưa hệ thống vào sử dụng.

Ở phiên bản này, `config.json` local chứa DataStore, còn blob/search nằm trong registry. Không chép toàn bộ Bootstrap hoặc cấu hình TOML của bản cũ vào file này.
