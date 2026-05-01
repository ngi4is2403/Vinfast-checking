# Thuê sạc xe không cu
Vinfast hãy hợp tác với tao
# Hệ Thống Quản Lý Bãi Đỗ Xe & Sạc Điện (ParkEV)

---

## Cấu Trúc Dự Án

```
parking_system/
├── app.py                  # Flask app chính, điều hướng routes
├── database.py             # Khởi tạo DB MySQL, seed data mẫu
├── config.py               # Cấu hình hệ thống (giá, VietQR, hằng số)
│
├── modules/
│   ├── user_service.py         # Thành viên 1: Quản lý tài khoản & phương tiện
│   ├── parking_service.py      # Thành viên 2: Nghiệp vụ đỗ xe, lấy xe, sạc điện
│   └── report_service.py       # Thành viên 3: Báo cáo tài chính & thống kê
│
├── static/
│   └── css/style.css       # Toàn bộ thiết kế (Glassmorphism, Dark mode)
│
├── templates/
│   ├── base.html           # Layout chung (Inlined JS: Toast, Modals, Theme)
│   ├── landing.html        # Trang chủ giới thiệu chuyên nghiệp
│   ├── auth/
│   │   ├── login.html
│   │   └── register.html
│   ├── user/
│   │   ├── dashboard.html   # Tổng quan cá nhân & số dư ví
│   │   ├── vehicles.html    # Quản lý danh sách xe
│   │   ├── park.html        # Đăng ký gửi xe (Chọn vị trí Khu A/B)
│   │   ├── checkout.html    # Lấy xe & tính phí
│   │   ├── charge.html      # Theo dõi sạc điện
│   │   ├── history.html     # Lịch sử giao dịch (Lọc Python-side)
│   │   └── wallet_qr.html   # Hiển thị mã QR nạp tiền
│   └── admin/
│       ├── dashboard.html   # Tổng quan vận hành bãi đỗ
│       ├── slots.html       # Quản lý 20 vị trí đỗ
│       ├── stations.html    # Quản lý trụ sạc
│       ├── parking_orders.html # Quản lý đơn gửi xe đang hoạt động
│       ├── charging_orders.html # Quản lý đơn sạc đang hoạt động
│       ├── topups.html      # Phê duyệt nạp tiền QR
│       ├── finance.html     # Quản lý thu/chi tài chính
│       └── report.html      # Báo cáo doanh thu doanh nghiệp
```

---

## Database Schema (`database.py`)

Hệ thống sử dụng MySQL với 10 bảng dữ liệu chính, hỗ trợ quan hệ chặt chẽ.

| Bảng | Mô tả |
|------|-------|
| `users` | id, full_name, phone, email, password_hash, role (user/admin), wallet_balance |
| `vehicles` | id, user_id, plate_number, vehicle_type, brand, model, created_at |
| `parking_slots` | id, slot_code, zone (A/B), status (available/occupied), has_charging |
| `charging_stations` | id, station_code, station_type, status (available/busy) |
| `parking_orders` | id, user_id, vehicle_id, slot_id, time_in, time_out, unit_price, total_fee, status |
| `charging_orders` | id, user_id, vehicle_id, station_id, time_start, time_end, total_fee, status |
| `payments` | id, order_type (parking/charging), order_id, amount, paid_at |
| `wallet_transactions` | id, user_id, amount, type (topup/payment), description, created_at |
| `finance_entries` | id, type (income/expense), amount, category, notes, admin_id |
| `pending_topups` | id, user_id, amount, ref_code, status (pending/confirmed/rejected) |

---

### Module 1 — `user_service.py` (Thành viên 1)

**Chức năng chính:**
- `register_user(...)`: Đăng ký tài khoản mới, mã hóa mật khẩu.
- `login(...)`: Xác thực người dùng và phân quyền.
- `update_profile(...)`: Chỉnh sửa thông tin cá nhân.
- `add_vehicle(...)`: Đăng ký phương tiện (kiểm tra biển số duy nhất).
- `get_vehicles(...)`: Lấy danh sách xe của người dùng hiện tại.
- `delete_vehicle(...)`: Xóa xe (chỉ khi không có đơn hàng đang hoạt động).

---

### Module 2 — `parking_service.py` (Thành viên 2)

**Nghiệp vụ gửi xe & Sạc:**
- `create_parking_order(...)`: Khởi tạo đơn gửi xe, tự động gán vị trí và trụ sạc.
- `checkout_parking(...)`: Kết thúc phiên gửi xe, tính toán phí dựa trên loại xe và thời gian thực tế.
- `create_charging_order(...)`: Bắt đầu phiên sạc xe điện.
- `end_charging(...)`: Kết thúc sạc, tính phí dịch vụ.
- `get_user_history(...)`: Truy xuất lịch sử với bộ lọc Tháng/Năm linh hoạt (Python logic).

**Quản lý Ví tiền & QR:**
- `user_topup(...)`: Tạo yêu cầu nạp tiền và sinh định danh `ref_code`.
- `admin_confirm_topup(...)`: Xác nhận nạp tiền, cộng số dư ví điện tử.

---

### Module 3 — `report_service.py` (Thành viên 3)

**Báo cáo & Tài chính Admin:**
- `admin_get_financial_report(...)`: Thống kê doanh thu theo ngày/tháng/năm.
- `admin_add_finance_entry(...)`: Ghi nhận các khoản chi phí vận hành (điện, nước, nhân sự).
- `get_occupancy_stats(...)`: Tính toán tỉ lệ lấp đầy các Khu A và Khu B.
- `get_revenue_chart_data(...)`: Cấu trúc dữ liệu phục vụ hiển thị biểu đồ Chart.js.

---

## UI / Frontend Aesthetics

**Ngôn ngữ thiết kế:**
- **Modern Dark Mode**: Sử dụng bảng màu sâu với các hiệu ứng Glassmorphism (làm mờ hậu cảnh).
- **Confirmation Modals**: Mọi hành động xóa hoặc thanh toán đều yêu cầu xác nhận qua Modal tùy chỉnh.
- **Dynamic Charting**: Sử dụng Chart.js để trực quan hóa dòng tiền doanh nghiệp cho Admin.

---

## Verification Plan

### Automated Verification
- Server Flask khởi động tại `http://localhost:5000`.
- Tài khoản Demo (Mật khẩu mặc định: `123456`):
  - **User**: `user@demo.com`
  - **Admin**: `admin@demo.com`

### Manual Verification
1. Đăng nhập User → Nạp tiền qua QR → Admin phê duyệt.
2. User gửi xe → Chọn Khu B để kích hoạt sạc điện.
3. User kết thúc sạc → Lấy xe → Hệ thống tự khấu trừ ví tiền.
4. Admin kiểm tra báo cáo tháng và biểu đồ doanh thu.
