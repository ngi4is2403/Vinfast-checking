from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, ParkingLot, ParkingSlot, ChargingStation, Booking, ChargingSession, Payment
from config import Config
from datetime import datetime, timedelta

app = Flask(__name__)
app.config.from_object(Config)

# Khởi tạo Database và Login Manager
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==========================================
# CÁC TUYẾN ĐƯỜNG CHÍNH (ROUTES)
# ==========================================

@app.route('/')
def index():
    """Trang chủ hiển thị danh sách bãi đỗ xe"""
    parking_lots = ParkingLot.query.all()
    return render_template('index.html', parking_lots=parking_lots)

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Đăng ký tài khoản mới"""
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'user')

        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            flash('Email đã tồn tại!', 'danger')
            return redirect(url_for('register'))

        new_user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role=role
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Đăng ký thành công!', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Đăng nhập vào hệ thống"""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        
        flash('Sai email hoặc mật khẩu!', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Đăng xuất"""
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    """Bảng điều khiển cho người dùng / chủ bãi xe"""
    if current_user.role == 'parking_owner':
        # Logic cho chủ bãi xe: Thống kê doanh thu, quản lý bãi
        owned_lots = ParkingLot.query.filter_by(owner_id=current_user.user_id).all()
        return render_template('owner_dashboard.html', lots=owned_lots)
    else:
        # Logic cho người dùng: Lịch sử đặt chỗ, tìm kiếm
        my_bookings = Booking.query.filter_by(user_id=current_user.user_id).all()
        return render_template('user_dashboard.html', bookings=my_bookings)

# ==========================================
# LOGIC XỬ LÝ SẠC XE (CHARGING LOGIC)
# ==========================================

@app.route('/charging/start', methods=['POST'])
@login_required
def start_charging():
    """Bắt đầu phiên sạc xe"""
    station_id = request.json.get('station_id')
    station = ChargingStation.query.get(station_id)
    
    if not station or station.status != 'available':
        return jsonify({'error': 'Trạm sạc không khả dụng'}), 400

    new_session = ChargingSession(
        user_id=current_user.user_id,
        station_id=station.station_id,
        start_time=datetime.utcnow(),
        status='active'
    )
    station.status = 'occupied'
    db.session.add(new_session)
    db.session.commit()
    
    return jsonify({'message': 'Bắt đầu sạc thành công', 'session_id': new_session.session_id})

@app.route('/charging/stop', methods=['POST'])
@login_required
def stop_charging():
    """Kết thúc phiên sạc và tính phí"""
    session_id = request.json.get('session_id')
    energy_end = request.json.get('energy_end') # kWh cuối
    energy_start = request.json.get('energy_start', 0) # kWh đầu
    
    session = ChargingSession.query.get(session_id)
    if not session or session.status != 'active':
        return jsonify({'error': 'Phiên sạc không tồn tại hoặc đã kết thúc'}), 400

    session.end_time = datetime.utcnow()
    session.energy_used = max(0, float(energy_end) - float(energy_start))
    
    # Giả định giá: 3000 VNĐ / kWh
    price_per_kwh = 3000
    session.cost = session.energy_used * price_per_kwh
    
    # Kiểm tra phí quá hạn (Overstay fee)
    # Quy tắc: Miễn phí 15p sau khi sạc đầy, sau đó tính 500 VNĐ / phút
    grace_period = 15 # phút
    # Trong mô phỏng này, chúng ta giả sử người dùng gọi stop_charging ngay sau khi sạc xong
    # Thực tế sẽ có sensor phát hiện xe vẫn đỗ sau khi sạc xong.
    
    session.status = 'completed'
    session.station.status = 'available'
    db.session.commit()
    
    return jsonify({
        'message': 'Kết thúc sạc',
        'energy_used': session.energy_used,
        'cost': session.cost,
        'duration': str(session.end_time - session.start_time)
    })

# ==========================================
# KHỞI TẠO DỮ LIỆU MẪU (DEMO DATA)
# ==========================================

def init_demo_data():
    """Tạo dữ liệu mẫu nếu DB trống"""
    if User.query.first() is None:
        # Tạo admin/owner mẫu
        owner = User(
            name='Nguyễn Văn Chủ', 
            email='owner@test.com', 
            password_hash=generate_password_hash('123456'),
            role='parking_owner'
        )
        db.session.add(owner)
        db.session.commit()

        # Tạo bãi đỗ mẫu
        lot = ParkingLot(
            name='Bãi Đỗ Xe ABC',
            address='123 Đường Láng, Hà Nội',
            total_slots=10,
            owner_id=owner.user_id
        )
        db.session.add(lot)
        db.session.commit()

        # Tạo slot và trạm sạc mẫu
        for i in range(1, 6):
            slot = ParkingSlot(
                parking_id=lot.parking_id,
                slot_number=f'A{i}',
                slot_type='ev_charging' if i <= 2 else 'normal'
            )
            db.session.add(slot)
            db.session.flush()

            if slot.slot_type == 'ev_charging':
                station = ChargingStation(
                    parking_id=lot.parking_id,
                    slot_id=slot.slot_id,
                    power_kw=22.0,
                    charging_type='Fast Charging'
                )
                db.session.add(station)
        
        db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_demo_data()
    app.run(debug=True)
