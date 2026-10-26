# CS2 trading system

Công cụ cá nhân để swing trade skin CS2 dựa trên dữ liệu. Ý tưởng chính:

1. Mỗi ngày lưu giá của toàn bộ item CS2 trên Skinport (API công khai, không cần tài khoản).
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

## Đọc kết quả `scan`

| Cột | Ý nghĩa |
|---|---|
| buy | Giá rao thấp nhất hiện tại trên Skinport |
| target | Giá bán trung vị 30 ngày, tức mức kỳ vọng bán lại |
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
| `sell_fee` | 0.08 | Phí bán của Skinport. **Hãy kiểm tra lại mức phí hiện tại trên trang Skinport.** |
| `min_price` / `max_price` | 2 / 300 | Khoảng giá item được xét (USD) |
| `min_volume_30d` | 60 | Tối thiểu số lượt bán trong 30 ngày |
| `min_listings` | 3 | Tối thiểu số tin đang rao bán |
| `min_discount` | 0.10 | Rẻ hơn trung vị 30 ngày ít nhất 10% |
| `min_net_roi` | 0.05 | Lãi ròng tối thiểu 5% sau phí |
| `max_momentum_drop` | 0.10 | Bỏ qua item có giá 7 ngày giảm hơn 10% so với 30 ngày |

## Giới hạn cần biết

- Giá rao thấp nhất trên Skinport có thể là món có float xấu hoặc pattern kém hơn các món khác cùng tên. Trước khi mua thật, luôn mở trang item để xem kỹ.
- Bộ lọc dựa trên dữ liệu tổng hợp 7/30/90 ngày của Skinport. Lịch sử tự lưu sẽ dày dần qua mỗi lần chạy `daily`.
- Paper trade giả định bán được ngay ở giá rao thấp nhất. Thực tế có thể phải chờ lâu hơn.
- Chưa so giá với các sàn khác (CSFloat, Steam). Phần này có thể thêm ở bản sau.

## Chạy test

```bash
python -m unittest discover -s tests
```
