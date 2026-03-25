# Walkthrough: Parking & EV Charging Management System

## Project Overview
Hệ thống quản lý bãi đỗ xe và trạm sạc xe điện đã hoàn tất với đầy đủ các tính năng backend và giao diện premium.

## Core Features Implemented

### 1. User Authentication (Đăng ký / Đăng nhập)
- Hỗ trợ phân quyền người dùng (User) và Chủ bãi xe (Owner).
- Mã hóa mật khẩu bảo mật bằng `werkzeug.security`.

### 2. Dashboard cho Người dùng
- Xem danh sách bãi đỗ xe có sẵn.
- Theo dõi lịch sử đặt chỗ và sạc xe.
- Giả lập phiên sạc xe với tính toán kWh và chi phí thực tế.

### 3. Dashboard cho Chủ bãi xe
- Thống kê doanh thu và lượt đặt chỗ.
- Quản lý danh sách các bãi đỗ xe sở hữu.
- Theo dõi trạng thái trạm sạc (Đang dùng / Trống).

### 4. Database Logic (models.py)
- Triển khai đầy đủ 8 bảng: `Users`, `ParkingLots`, `ParkingSlots`, `ChargingStations`, `Bookings`, `ChargingSessions`, `Payments`, `Sensors`.
- Thiết lập quan hệ (Relationships) chặt chẽ giữa các bảng để quản lý dữ liệu hiệu quả.

## Screenshots / UI Flow

### Home Page
Trang chủ với thiết kế hiện đại, giới thiệu dịch vụ và danh sách bãi đỗ tiêu biểu.
![Home Page Mockup](file:///c:/Users/ACER/Downloads/pythonBTL/static/css/styles.css)

### User Dashboard
Giao diện quản lý cá nhân cho khách hàng, cho phép theo dõi phiên sạc và lịch sử.

### Owner Dashboard
Giao diện quản lý cho chủ bãi xe với các biểu đồ thống kê cơ bản và danh sách cơ sở kinh doanh.

## How to Run
1. Đảm bảo đã cài đặt MySQL và tạo database `parking_management`.
2. Cài đặt dependencies: `pip install -r requirements.txt`.
3. Chỉnh sửa [config.py](file:///c:/Users/ACER/Downloads/pythonBTL/config.py) với thông tin MySQL của bạn.
4. Chạy ứng dụng: `python app.py`.
5. Truy cập `http://127.0.0.1:5000`.

> [!NOTE]
> Hệ thống tự động tạo dữ liệu mẫu (Owner mẫu, Bãi đỗ mẫu) trong lần chạy đầu tiên để bạn dễ dàng trải nghiệm.

## Technical Highlights
- **Backend API**: Thiết kế hướng tới việc dễ dàng tích hợp với cảm biến IoT thực tế.
- **Frontend**: Sử dụng Vanilla CSS với hệ thống biến (variables) HSL giúp giao diện nhất quán và cao cấp.
- **Vietnamese Support**: Toàn bộ UI và Code Comments được viết bằng tiếng Việt theo yêu cầu.
