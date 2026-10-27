# CS2 trading system

Công cụ cá nhân để swing trade skin CS2 dựa trên dữ liệu. Ý tưởng chính:

1. Mỗi ngày lấy giá rao thấp nhất của toàn bộ item CS2 trên **CSFloat**, cùng lịch sử bán của khoảng 200 item được rao nhiều nhất. Tool dùng API công khai, không cần tài khoản hay API key.
2. Tìm item đang được rao bán **rẻ hơn rõ rệt so với giá bán trung vị 30 ngày**, vẫn **bán chạy**, và **không đang trong đà rơi**.
3. Chỉ giữ những cơ hội vẫn **có lời sau phí bán**.
4. Chạy **paper trade** (giao dịch giả lập bằng tiền ảo) để đo lãi/lỗ thật trước khi bỏ tiền thật.

Tool **không đăng nhập, không mua bán** gì cả. Nó chỉ đọc giá công khai.

## Cài đặt

Cần Python 3.10 trở lên.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.json config.json
```

## Cách dùng

```bash
python -m cs2ts daily     # lấy giá hôm nay + chạy paper trade + in báo cáo
python -m cs2ts scan      # xem danh sách cơ hội hiện tại (top 30)
python -m cs2ts scan --top 100 --csv signals.csv
python -m cs2ts report    # xem lại báo cáo paper trade
```

Chạy `daily` **mỗi ngày một lần**, đều đặn ít nhất 3–4 tuần. Có thể hẹn giờ tự chạy:

- macOS/Linux: `crontab -e`, thêm dòng
  `0 9 * * * cd /đường/dẫn/cs2-trading-system && .venv/bin/python -m cs2ts daily >> data/daily.log 2>&1`
- Windows: dùng Task Scheduler để chạy `python -m cs2ts daily` trong thư mục này.

Dữ liệu được lưu trong `data/cs2ts.sqlite`. Đừng xoá file này, vì lịch sử paper trade nằm ở đó.

## Dashboard

```bash
python -m cs2ts dashboard          # mở http://localhost:8000 trên trình duyệt
```

Dashboard gồm:

- Các chỉ số chính: giá trị tài khoản, lãi đã chốt, lãi tạm tính, tỉ lệ thắng và tiền mặt.
- Biểu đồ giá trị tài khoản theo từng ngày.
- Bảng vị thế đang giữ, có đánh dấu món còn trong 7 ngày khóa.
- Bảng lệnh đã đóng, kèm lý do bán.
- Bảng cơ hội hôm nay. Bấm vào một dòng để xem lịch sử giá của item đó.

Dashboard chỉ đọc dữ liệu, không thay đổi gì. Bấm "Tải lại" sau mỗi lần chạy `daily` để thấy số mới.

Muốn xem thử giao diện khi chưa có dữ liệu thật:

```bash
python -m cs2ts demo               # tạo 60 ngày dữ liệu giả trong data/demo.sqlite
python -m cs2ts dashboard --demo
```

Dữ liệu demo hoàn toàn ngẫu nhiên. Lãi/lỗ trong demo **không** nói lên điều gì về chiến lược.

## Chạy trên server bằng Docker

Container chạy dashboard ở cổng 8000 và tự chạy `daily` mỗi ngày lúc 12:15 (giờ Việt Nam). Dữ liệu nằm trong thư mục `data/` trên máy chủ, nên vẫn giữ nguyên khi cập nhật hay khởi động lại container.

```bash
cp config.example.json config.json   # bắt buộc trước lần chạy đầu
docker compose up -d --build
docker compose logs -f               # xem log của các lần chạy hằng ngày
```

- Đổi giờ chạy: sửa `DAILY_AT` trong `docker-compose.yml`.
- Nếu container khởi động lại trong ngày mà hôm đó đã có dữ liệu, tool sẽ không chạy lại, để tiết kiệm lượt gọi API của CSFloat.
- Cập nhật code: chép code mới lên server rồi chạy lại `docker compose up -d --build`.

## Đọc kết quả `scan`

| Cột | Ý nghĩa |
|---|---|
| buy | Giá rao thấp nhất hiện tại trên sàn |
| target | Giá mục tiêu: mức một bản "thường" hay bán được gần đây, tức mức kỳ vọng bán lại |
| net | Lãi ròng mỗi món nếu bán được ở target, sau phí bán |
| roi | Lãi ròng / giá mua |
| disc | Giá hiện tại rẻ hơn trung vị 30 ngày bao nhiêu % |
| mom | Trung vị 7 ngày so với 30 ngày; âm nhiều nghĩa là item đang rơi giá |
| vol30 | Số lượt bán trong 30 ngày, càng cao càng dễ bán lại |

## Luật paper trade

- Mua 1 món cho mỗi cơ hội tốt nhất, trong giới hạn ngân sách (`paper_budget`), số vị thế (`paper_max_positions`) và giá mỗi món (`paper_max_per_position`).
- **Không bán trong 7 ngày đầu**, mô phỏng thời gian khóa trade của Steam.
- Sau đó bán khi một trong ba điều kiện xảy ra:
  - giá rao thấp nhất chạm target;
  - lỗ quá `stop_loss` (mặc định 20%);
  - giữ quá `max_hold_days` (mặc định 45 ngày).
- Giá bán giả định = giá rao thấp nhất × 0.99 (bán rẻ hơn 1% để bán nhanh), rồi trừ phí bán.

## Chỉnh tham số (`config.json`)

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `source` | `csfloat` | Nguồn giá: `csfloat` hoặc `skinport`. Skinport đang chặn IP Việt Nam. |
| `sell_fee` | 0.045 | Tổng phần bị mất khi bán: phí bán của CSFloat (~2%) cộng phí rút tiền (~2.5%). **Hãy kiểm tra lại mức phí hiện tại trên CSFloat.** |
| `min_price` / `max_price` | 2 / 300 | Khoảng giá item được xét (USD) |
| `min_volume_30d` | 60 | Tối thiểu số lượt bán trong 30 ngày |
| `min_listings` | 3 | Tối thiểu số tin đang rao bán |
| `min_discount` | 0.10 | Rẻ hơn trung vị 30 ngày ít nhất 10% |
| `min_net_roi` | 0.05 | Lãi ròng tối thiểu 5% sau phí |
| `max_momentum_drop` | 0.10 | Bỏ qua item có giá 7 ngày giảm hơn 10% so với 30 ngày |
| `csfloat_universe_size` | 200 | Số item (rao nhiều nhất trong khoảng giá) được lấy lịch sử bán mỗi lần chạy |
| `csfloat_request_delay` | 1.0 | Số giây nghỉ giữa các lần gọi API, để không bị giới hạn |

## Giới hạn cần biết

- Giá rao thấp nhất có thể là món có float xấu hoặc pattern kém hơn các món khác cùng tên. Trước khi mua thật, luôn mở trang item để xem kỹ.
- Với CSFloat, **giá mục tiêu** là trung vị giá bán của các bản "thường" trong 40 lượt bán gần nhất. Tool bỏ qua các bản bán đắt nhờ sticker, float hay pattern hiếm, vì chúng làm giá trung bình bị đẩy lên và tạo ra cơ hội giả. Item nào có ít hơn 5 lượt bán bản thường sẽ bị bỏ qua. Số lượt bán và xu hướng 7 ngày so với 30 ngày lấy từ dữ liệu theo ngày của CSFloat.
- **Xu hướng giá** (cột `mom`) được tính từ chính lịch sử giá mục tiêu mà tool lưu mỗi ngày. Vì vậy trong tuần đầu chưa có bộ lọc xu hướng; từ ngày thứ 7 trở đi bộ lọc mới đầy đủ.
- Mỗi lần chạy `daily` với CSFloat mất khoảng 7 phút, vì tool nghỉ 1 giây giữa mỗi lần gọi API (2 lần gọi cho mỗi item).
- CSFloat chỉ cho khoảng **500 lần gọi lịch sử mỗi 24 giờ**. Với `csfloat_universe_size` = 200, mỗi ngày tool dùng khoảng 400 lần, nên **chỉ chạy `daily` một lần mỗi ngày**. Nếu sắp hết lượt, tool tự dừng sớm. Nếu sắp chạm giới hạn gọi API, tool tự dừng sớm và giữ phần dữ liệu đã lấy được.
- Paper trade giả định bán được ngay ở giá rao thấp nhất. Thực tế có thể phải chờ lâu hơn.
- Chưa so giá giữa các sàn. Phần này có thể thêm ở bản sau.

## Chạy test

```bash
python -m unittest discover -s tests
```
