# Hệ Thống Quản Lý Bãi Đỗ Xe & Sạc Điện

## Mô Tả

Xây dựng hệ thống web hoàn chỉnh gồm backend Python (Flask), cơ sở dữ liệu SQLite (không cần cài đặt MySQL), và giao diện HTML/CSS/JS hiện đại. Hệ thống phục vụ **3 vai trò**: User, Admin bãi đỗ, Tổng giám đốc.

---

## Quyết Định Kỹ Thuật Cần Xác Nhận

> [!IMPORTANT]
> **Database**: Dùng **SQLite** (file-based, không cần cài đặt server) thay vì MySQL để dễ chạy ngay. Nếu bạn muốn MySQL/PostgreSQL, vui lòng nói rõ.

> [!IMPORTANT]
> **Auth**: Dùng session-based login (Flask session) — không dùng JWT token để đơn giản hóa frontend.

> [!NOTE]
> Dữ liệu mẫu (seed data) sẽ được tạo sẵn để demo ngay sau khi chạy.

---

## Cấu Trúc Dự Án

```
parking_system/
├── app.py                  # Flask app chính, routes
├── database.py             # Khởi tạo DB, seed data (MySQL)
├── config.py               # Cấu hình (giá, hằng số)
│
├── modules/
│   ├── user_service.py         # Thành viên 1: Tài khoản & phương tiện
│   ├── parking_service.py      # Thành viên 2: Gửi xe, lấy xe, sạc
│   ├── report_service.py       # Thành viên 3: Báo cáo tháng
│   └── booking_service.py      # [MỚI v2] Đặt lịch trước, thanh toán & check-in
│
├── static/
│   ├── css/style.css
│   └── js/main.js
│
└── templates/
    ├── base.html
    ├── landing.html
    ├── auth/
    │   ├── login.html
    │   └── register.html
    ├── user/
    │   ├── dashboard.html
    │   ├── vehicles.html
    │   ├── park.html
    │   ├── checkout.html
    │   ├── charge.html
    │   ├── history.html
    │   ├── bookings.html       # [MỚI] Form đặt lịch + danh sách
    │   └── booking_confirm.html# [MỚI] Xác nhận & countdown
    └── admin/
        ├── dashboard.html
        ├── slots.html
        ├── stations.html
        ├── parking_orders.html
        ├── charging_orders.html
        └── bookings.html       # [MỚI] Admin quản lý đặt lịch
```

---

## Proposed Changes

### Database Schema (`database.py`)

#### Bảng dữ liệu

| Bảng | Mô tả |
|------|-------|
| `users` | id, full_name, phone, email, password_hash, role (user/admin/director), balance, created_at |
| `vehicles` | id, user_id, plate_number, vehicle_type, brand, model, color, battery_capacity, created_at |
| `parking_slots` | id, slot_code, slot_type (small/large), floor_area, zone (A/B), status (available/occupied/reserved), has_charging, notes |
| `charging_stations` | id, station_code, station_type (slow/fast), power_kw, status (available/busy/maintenance), area |
| `parking_orders` | id, user_id, vehicle_id, slot_id, time_in, time_out, status, unit_price, total_fee, notes, **booking_id**, **booking_credit**, **early_fee** |
| `charging_orders` | id, user_id, vehicle_id, station_id, charge_type, time_start, time_end, kwh_consumed, total_fee, status |
| `payments` | id, order_type, order_id, amount, paid_at, method |
| `wallet_transactions` | id, user_id, tx_type (topup/payment), amount, balance_after, description, ref_type, ref_id |
| `bookings` ⭐ | id, user_id, vehicle_id, slot_id, scheduled_time, duration_hours, total_fee, penalty_fee, refund_fee, status (pending/active/completed/no_show/cancelled), checkin_at, checkout_at |

---

### Module 1 — `user_service.py` (Thành viên 1)

**Chức năng:**
- `register_user(full_name, phone, email, password)` → validate & hash password → insert users
- `login(email_or_phone, password)` → xác thực → trả về user + role
- `update_profile(user_id, ...)` → cập nhật thông tin cá nhân
- `add_vehicle(user_id, plate, type, ...)` → kiểm tra biển số unique toàn hệ thống
- `update_vehicle(vehicle_id, user_id, ...)` → chỉ chủ xe được sửa
- `delete_vehicle(vehicle_id, user_id)` → kiểm tra không có đơn active
- `get_vehicles(user_id)` → danh sách xe của user

---

### Module 2 — `parking_service.py` (Thành viên 2)

**Gửi xe:**
- `get_available_slots(vehicle_type)` → lọc slot theo loại xe
- `create_parking_order(user_id, vehicle_id, slot_id)` → kiểm tra xe chưa active, slot available
- `checkout(order_id, user_id)` → tính phí, update slot → available, tạo payment

**Lấy xe — Bảng giá:**
| Loại xe | Giá/giờ | Tối đa/ngày |
|---------|---------|-------------|
| Xe máy | 5.000đ | 25.000đ |
| Ô tô | 20.000đ | 120.000đ |
| Xe máy điện | 8.000đ | 40.000đ |
| Ô tô điện | 25.000đ | 150.000đ |

**Sạc:**
- `get_available_stations(charge_type)` → lọc trụ theo loại
- `create_charging_order(user_id, vehicle_id, station_id, charge_type)`
- `end_charging(order_id, kwh)` → tính phí sạc, update station → available

**Giá sạc:**
- Phí giữ chỗ: 10.000đ/lượt
- Sạc chậm: 8.000đ/kWh
- Sạc nhanh: 12.000đ/kWh

**Admin:**
- `get_active_parking_orders()` — xe đang trong bãi
- `get_active_charging_orders()` — trụ đang sạc
- `admin_confirm_checkout(order_id)` — xác nhận xe ra
- `update_slot_status(slot_id, status)`
- `update_station_status(station_id, status)`

---

### Module 4 — `booking_service.py` ⭐ [Mới — Version 2]

**Luồng nghiệp vụ:**

```
User đặt lịch → trả phí NGAY → slot bị giữ (reserved)
     ↓
  ┌──────────────────────────────────────────────────────┐
  │ Đến SỚM (trước scheduled_time)                       │
  │ → Check-in sớm được                                  │
  │ → Phí đỗ từ time_in → scheduled_time tính thêm khi out│
  │ → Từ scheduled_time → expire_at: booking đã cover    │
  └──────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────┐
  │ Đến ĐÚNG/MUỘN (trong khung giờ)                      │
  │ → Check-in, booking credit cover 100%                │
  │ → Không thu thêm phí                                 │
  └──────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────┐
  │ Hết khung giờ (no-show)                              │
  │ → Mất toàn bộ phí, slot tự giải phóng               │
  └──────────────────────────────────────────────────────┘
```

**Quy tắc huỷ:**

| Thời điểm huỷ | Phạt | Hoàn lại |
|---|---|---|
| Trước `scheduled_time` | 30% | 70% |
| Trong khung giờ (sau `scheduled_time`) | 50% | 50% |
| Sau `expire_at` (hết giờ) | Không huỷ được | — |

**Hàm chính:**
- `create_booking(user_id, vehicle_id, slot_id, scheduled_time, duration_hours)` → validate, trừ ví, giữ slot
- `checkin_booking(booking_id, user_id)` → check-in sớm/đúng/muộn, tạo parking_order có `booking_credit`
- `cancel_booking(booking_id, user_id)` → phạt 30% hoặc 50% tuỳ thời điểm, hoàn ví
- `auto_mark_no_show()` → quét tự động, đánh dấu expired booking
- `get_user_bookings(user_id)` → danh sách kèm trường tính toán (countdown, penalty)
- `get_booking_by_id(booking_id, user_id)` → chi tiết 1 booking

**Admin:**
- `admin_get_all_bookings(status_filter, search_plate)` → toàn bộ đặt lịch, lọc đa chiều
- `admin_checkin_booking(booking_id)` → check-in thay user (không giới hạn thời gian)
- `admin_cancel_booking(booking_id, refund_percent)` → huỷ + chọn % hoàn tiền tuỳ ý

**Tích hợp với `parking_service.checkout_parking`:**
- Khi `parking_orders.booking_id IS NOT NULL`, hàm checkout tự động:
  - Tính phí phần đến sớm (trước `scheduled_time`)
  - Tính phí phần ở thêm (sau `expire_at`)
  - Khấu trừ `booking_credit` đã trả trước
  - **Kiểm tra số dư:** Nếu thiếu tiền (do phí sớm/quá giờ) → Chặn lấy xe, yêu cầu nạp thêm. **BẮT BUỘC** thanh toán hết mới cho lấy xe.
  - Đánh dấu booking `completed`
- **Luồng Check-in đồng nhất:** Nút "Check-in sớm" ở trang Đặt lịch sẽ nhảy sang trang Gửi xe với thông tin điền sẵn, đảm bảo trải nghiệm người dùng nhất quán.

### UI / Frontend

**Giao diện sẽ dùng:**
- Dark mode gradient design
- Chart.js cho biểu đồ báo cáo của Tổng giám đốc
- Responsive layout với CSS Grid/Flexbox
- Micro-animations và hover effects

**Trang theo vai trò:**

| Role | Trang |
|------|-------|
| Guest | Landing, Login, Register |
| User | Dashboard, Vehicles, Park, Checkout, Charge, History, **Bookings** ⭐, **Booking Confirm** ⭐ |
| Admin | Dashboard, Slots, Stations, Parking Orders, Charging Orders, **Bookings (quản lý)** ⭐ |
| Director | Report (với Chart.js) |

---

## Verification Plan

### Automated
- Chạy `python app.py` → server khởi động tại `localhost:5000`
- Seed data tự động tạo tài khoản demo:
  - **User**: `user@demo.com` / `123456`
  - **Admin**: `admin@demo.com` / `123456`
  - **Director**: `director@demo.com` / `123456`

### Manual
- Đăng nhập thử 3 vai trò khác nhau
- Flow gửi xe: Gửi xe → Lấy xe → Kiểm tra lịch sử
- Flow sạc: Sạc → Kết thúc sạc → Kiểm tra hóa đơn
- **Flow đặt lịch (v2):**
  - Đặt lịch → Thanh toán ngay → Nhận trang xác nhận + countdown.
  - Đến sớm → Check-in sớm → Hiển thị countdown chờ đến giờ vào lịch.
  - Đến giờ hẹn → Hệ thống tự kích hoạt booking, trừ tiền phần đỗ sớm từ ví.
  - Lấy xe → Nếu thiếu tiền (do ở quá lâu) → Hệ thống chặn và yêu cầu nạp thêm tiền.
  - Huỷ trước giờ hẹn → Hoàn 70%.
  - Huỷ trong khung giờ → Hoàn 50%.
- Xem báo cáo tháng với biểu đồ.

---

> [!IMPORTANT]
> Hệ thống sử dụng **MySQL** để đảm bảo tính nhất quán dữ liệu cho các giao dịch ví và booking.

> [!NOTE]
> Hệ thống đã bao gồm **seed data mẫu** (slots, stations, demo accounts) để có thể sử dụng ngay sau khi chạy.
