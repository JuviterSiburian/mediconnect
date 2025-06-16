import boto3
from botocore.exceptions import NoCredentialsError
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS
from datetime import datetime
import logging
from functools import wraps
import os
from werkzeug.utils import secure_filename
import uuid

# Inisialisasi Flask
app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

# Konfigurasi AWS S3
S3_BUCKET = 'mediconnect-files-s3'
S3_REGION = 'ap-southeast-1'
AWS_ACCESS_KEY = 'AKIA6C7MUM3TVGCZG5M6'  # Ganti dengan access key Anda
AWS_SECRET_KEY = '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'  # Ganti dengan secret key Anda

s3_client = boto3.client(
    's3',
    region_name=S3_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY
)

# Konfigurasi database RDS MySQL
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://admin:Senibudaya07@mediconnect-db.cnaow04girzu.ap-southeast-1.rds.amazonaws.com/mediconnect_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'supersecretkey'

db = SQLAlchemy(app)

# Inisialisasi Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Logging
logging.basicConfig(level=logging.INFO)

# ------------------ HELPER FUNCTIONS ------------------

def upload_file_to_s3(file, bucket_name):
    try:
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        
        s3_client.upload_fileobj(
            file,
            bucket_name,
            unique_filename,
            ExtraArgs={
                "ContentType": file.content_type
            }
        )
        
        file_url = f"https://{bucket_name}.s3.{S3_REGION}.amazonaws.com/{unique_filename}"
        return file_url, unique_filename
        
    except Exception as e:
        app.logger.error(f"Error uploading to S3: {str(e)}")
        return None, None


def delete_file_from_s3(filename, bucket_name):
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=filename)
        return True
    except Exception as e:
        app.logger.error(f"Error deleting from S3: {str(e)}")
        return False

# ------------------ MODELS ------------------

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='pasien')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    appointment_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text)
    
    patient = db.relationship('User', foreign_keys=[patient_id], backref='patient_appointments')
    doctor = db.relationship('User', foreign_keys=[doctor_id], backref='doctor_appointments')

class MedicalRecord(db.Model):
    __tablename__ = 'medical_records'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    diagnosis = db.Column(db.String(255))
    treatment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    patient = db.relationship('User', foreign_keys=[patient_id], backref='medical_records_as_patient')
    doctor = db.relationship('User', foreign_keys=[doctor_id], backref='medical_records_as_doctor')
    attachments = db.relationship('MedicalAttachment', backref='medical_record', cascade='all, delete-orphan')

class MedicalAttachment(db.Model):
    __tablename__ = 'medical_attachments'
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('medical_records.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(512), nullable=False)
    filetype = db.Column(db.String(50))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_file_url(self):
        return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{self.filepath}"

# ------------------ ROUTES ------------------

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role != role:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin/dashboard')
@login_required
@role_required('dokter')
def admin_dashboard():
    return render_template('admin/dashboard_admin.html')

@app.route('/user/dashboard')
@login_required
@role_required('pasien')
def user_dashboard():
    return render_template('user/dashboard_user.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')
    
    if request.method == 'POST':
        try:
            username = request.form.get('username').strip()
            email = request.form.get('email').strip().lower()
            password = request.form.get('password').strip()
            role = request.form.get('role', 'pasien')

            if not all([username, email, password]):
                flash('Semua field wajib diisi', 'error')
                return redirect(url_for('register'))

            if len(password) < 6:
                flash('Password minimal 6 karakter', 'error')
                return redirect(url_for('register'))

            existing_user = User.query.filter(
                (User.username == username) | 
                (User.email == email)
            ).first()
            
            if existing_user:
                flash('Username atau email sudah terdaftar', 'error')
                return redirect(url_for('register'))

            new_user = User(
                username=username,
                email=email,
                role=role
            )
            new_user.password = password

            db.session.add(new_user)
            db.session.commit()

            login_user(new_user)
            flash('Registrasi berhasil!', 'success')
            
            if new_user.role == 'dokter':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('user_dashboard'))

        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error during registration: {str(e)}')
            flash(f'Terjadi kesalahan server: {str(e)}', 'error')
            return redirect(url_for('register'))
        
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            flash('Email dan password wajib diisi', 'error')
            return redirect(url_for('login'))

        user = User.query.filter_by(email=email).first()

        if user and user.verify_password(password):
            login_user(user)
            flash('Login berhasil!', 'success')
            
            if user.role == 'dokter':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('user_dashboard'))
        else:
            flash('Login gagal, periksa kembali email dan password.', 'error')
            return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Anda telah logout', 'info')
    return redirect(url_for('login'))

@app.route('/buat_janji', methods=['GET', 'POST'])
@login_required
@role_required('pasien')
def buat_janji():
    if request.method == 'POST':
        try:
            doctor_id = request.form.get('doctor_id')
            appointment_datetime_str = request.form.get('appointment_datetime')
            notes = request.form.get('notes')

            if not all([doctor_id, appointment_datetime_str, notes]):
                flash('Semua field wajib diisi', 'error')
                return redirect(url_for('buat_janji'))

            doctor = User.query.filter_by(id=doctor_id, role='dokter').first()
            if not doctor:
                flash('Dokter tidak ditemukan', 'error')
                return redirect(url_for('buat_janji'))

            try:
                appointment_datetime = datetime.strptime(appointment_datetime_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Format tanggal dan waktu tidak valid', 'error')
                return redirect(url_for('buat_janji'))

            appointment = Appointment(
                patient_id=current_user.id,
                doctor_id=doctor_id,
                appointment_time=appointment_datetime,
                notes=notes,
                status='pending'
            )
            
            db.session.add(appointment)
            db.session.commit()
            flash('Janji temu berhasil dibuat', 'success')
            return redirect(url_for('riwayat_janji'))

        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error membuat janji: {str(e)}', exc_info=True)
            flash(f'Terjadi kesalahan: {str(e)}', 'error')
            return redirect(url_for('buat_janji'))

    doctors = User.query.filter_by(role='dokter').all()
    return render_template('user/buat_janji.html', doctors=doctors)

@app.route('/riwayat_janji')
@login_required
@role_required('pasien')
def riwayat_janji():
    appointments = Appointment.query.filter_by(patient_id=current_user.id)\
        .order_by(Appointment.appointment_time.desc()).all()
    
    return render_template('user/riwayat_janji.html', 
                         appointments=appointments,
                         now=datetime.now())

@app.route('/rekam_medis', methods=['GET', 'POST'])
@login_required
@role_required('dokter')
def rekam_medis():
    if request.method == 'POST':
        patient_id = request.form['patient_id']
        diagnosis = request.form['diagnosis']
        treatment = request.form['treatment']

        new_record = MedicalRecord(
            patient_id=patient_id,
            doctor_id=current_user.id,
            diagnosis=diagnosis,
            treatment=treatment
        )
        db.session.add(new_record)
        db.session.commit()
        flash('Rekam medis berhasil ditambahkan', 'success')

        return redirect(url_for('rekam_medis', record_id=new_record.id))

    record_id = request.args.get('record_id', type=int)
    records = MedicalRecord.query.order_by(MedicalRecord.created_at.desc()).all()
    selected_record = db.session.get(MedicalRecord, record_id) if record_id else None
    patient = selected_record.patient if selected_record else None
    attachments = selected_record.attachments if selected_record else []

    patients = User.query.filter_by(role='pasien').all()

    return render_template(
        'admin/rekam_medis.html',
        records=records,
        selected_record=selected_record,
        patient=patient,
        attachments=attachments,
        patients=patients
    )

@app.route('/upload_lampiran/<int:record_id>', methods=['POST'])
@login_required
@role_required('dokter')
def upload_lampiran(record_id):
    if 'lampiran' not in request.files:
        flash('Tidak ada file yang diupload', 'error')
        return redirect(url_for('rekam_medis', record_id=record_id))

    file = request.files['lampiran']
    if file.filename == '':
        flash('Tidak ada file yang dipilih', 'error')
        return redirect(url_for('rekam_medis', record_id=record_id))

    if file:
        try:
            file_url, s3_key = upload_file_to_s3(file, S3_BUCKET)
            
            if file_url and s3_key:
                lampiran = MedicalAttachment(
                    record_id=record_id,
                    filename=file_url,
                    filetype=file.content_type,
                    filepath=file_url
                )
                db.session.add(lampiran)
                db.session.commit()
                flash('Lampiran berhasil diupload', 'success')
            else:
                flash('Gagal mengupload lampiran ke S3', 'error')
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error uploading attachment: {str(e)}')
            flash(f'Terjadi kesalahan: {str(e)}', 'error')

    return redirect(url_for('rekam_medis', record_id=record_id))

@app.route('/delete_lampiran/<int:attachment_id>', methods=['POST'])
@login_required
@role_required('dokter')
def delete_lampiran(attachment_id):
    attachment = MedicalAttachment.query.get_or_404(attachment_id)
    record_id = attachment.record_id
    
    record = MedicalRecord.query.get(record_id)
    if not record or record.doctor_id != current_user.id:
        abort(403)
    
    try:
        if delete_file_from_s3(attachment.s3_key, S3_BUCKET):
            db.session.delete(attachment)
            db.session.commit()
            flash('Lampiran berhasil dihapus', 'success')
        else:
            flash('Gagal menghapus lampiran dari S3', 'error')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error deleting attachment: {str(e)}')
        flash(f'Terjadi kesalahan: {str(e)}', 'error')
    
    return redirect(url_for('rekam_medis', record_id=record_id))

@app.route('/medis')
@login_required
@role_required('pasien')
def halaman_medis():
    records = MedicalRecord.query.filter_by(patient_id=current_user.id)\
        .order_by(MedicalRecord.created_at.desc()).all()
    return render_template('user/medis.html', records=records)

@app.route('/dokter/daftar-janji')
@login_required
@role_required('dokter')
def dokter_daftar_janji():
    appointments = db.session.query(Appointment)\
        .filter_by(doctor_id=current_user.id)\
        .order_by(Appointment.appointment_time)\
        .all()
    
    return render_template('admin/daftar_janji.html', appointments=appointments)

@app.route('/dokter/update-appointment/<int:id>/<status>', methods=['POST'])
@login_required
@role_required('dokter')
def update_appointment_status(id, status):
    appointment = db.session.get(Appointment, id)
    
    if appointment.doctor_id != current_user.id:
        flash('Akses ditolak: Bukan janji Anda!', 'danger')
        return redirect(url_for('dokter_daftar_janji'))
    
    appointment.status = status
    db.session.commit()
    
    flash(f'Status janji berhasil diubah ke "{status}"', 'success')
    return redirect(url_for('dokter_daftar_janji'))

# ------------------ RUN ------------------

if __name__ == "__main__":
    with app.app_context():
        # Perbaikan untuk error kolom yang tidak ada
        try:
            db.create_all()
        except Exception as e:
            app.logger.error(f"Error creating tables: {str(e)}")
            # Jika ada error, drop semua tabel dan buat kembali (hanya untuk development)
            if app.config.get('DEBUG'):
                db.drop_all()
                db.create_all()
    
    app.run(host="0.0.0.0", port=5000, debug=True)