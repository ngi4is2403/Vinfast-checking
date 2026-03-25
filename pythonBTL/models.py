from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):
    """Bảng người dùng: lưu thông tin đăng nhập và vai trò"""
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='user') # user, parking_owner, admin
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='active')

    # Quan hệ
    bookings = db.relationship('Booking', backref='user', lazy=True)
    parking_lots = db.relationship('ParkingLot', backref='owner', lazy=True)

    def get_id(self):
        return str(self.user_id)

class ParkingLot(db.Model):
    """Bảng bãi đỗ xe: thông tin vị trí và quy mô"""
    __tablename__ = 'parking_lots'
    parking_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    total_slots = db.Column(db.Integer, default=0)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='active')

    # Quan hệ
    slots = db.relationship('ParkingSlot', backref='parking', lazy=True)
    stations = db.relationship('ChargingStation', backref='parking', lazy=True)

class ParkingSlot(db.Model):
    """Bảng vị trí đỗ xe cụ thể"""
    __tablename__ = 'parking_slots'
    slot_id = db.Column(db.Integer, primary_key=True)
    parking_id = db.Column(db.Integer, db.ForeignKey('parking_lots.parking_id'), nullable=False)
    slot_number = db.Column(db.String(20), nullable=False)
    slot_type = db.Column(db.String(20), default='normal') # normal, ev_charging
    status = db.Column(db.String(20), default='available') # available, occupied, reserved
    sensor_id = db.Column(db.String(50))

    # Quan hệ
    bookings = db.relationship('Booking', backref='slot', lazy=True)
    charging_station = db.relationship('ChargingStation', backref='slot', uselist=False)

class ChargingStation(db.Model):
    """Bảng trạm sạc điện: thông tin kỹ thuật và trạng thái"""
    __tablename__ = 'charging_stations'
    station_id = db.Column(db.Integer, primary_key=True)
    parking_id = db.Column(db.Integer, db.ForeignKey('parking_lots.parking_id'), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey('parking_slots.slot_id'), unique=True)
    power_kw = db.Column(db.Float, nullable=False)
    charging_type = db.Column(db.String(50)) # AC, DC, Fast Charging
    status = db.Column(db.String(20), default='available')

    # Quan hệ
    sessions = db.relationship('ChargingSession', backref='station', lazy=True)

class Booking(db.Model):
    """Bảng đặt chỗ: theo dõi việc thuê chỗ đỗ xe"""
    __tablename__ = 'bookings'
    booking_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey('parking_slots.slot_id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, confirmed, completed, cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Quan hệ
    payment = db.relationship('Payment', backref='booking', uselist=False)

class ChargingSession(db.Model):
    """Bảng phiên sạc: ghi nhận tiêu thụ điện và thời gian"""
    __tablename__ = 'charging_sessions'
    session_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False)
    station_id = db.Column(db.Integer, db.ForeignKey('charging_stations.station_id'), nullable=False)
    start_time = db.Column(db.DateTime, default=datetime.utcnow)
    end_time = db.Column(db.DateTime)
    energy_used = db.Column(db.Float, default=0.0) # kWh
    cost = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='active')

class Payment(db.Model):
    """Bảng thanh toán: quản lý giao dịch tài chính"""
    __tablename__ = 'payments'
    payment_id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.booking_id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50)) # momo, vnpay, card, cash
    payment_status = db.Column(db.String(20), default='pending')
    transaction_id = db.Column(db.String(100), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Sensor(db.Model):
    """Bảng cảm biến (IoT): cập nhật trạng thái thực tế của chỗ đỗ"""
    __tablename__ = 'sensors'
    sensor_id = db.Column(db.String(50), primary_key=True)
    slot_id = db.Column(db.Integer, db.ForeignKey('parking_slots.slot_id'))
    sensor_type = db.Column(db.String(50)) # parking_sensor, charging_sensor
    status = db.Column(db.String(20))
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
