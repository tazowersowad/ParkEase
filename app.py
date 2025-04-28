# app.py

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_mysqldb import MySQL
from werkzeug.security import generate_password_hash, check_password_hash
import config
from flask_dance.contrib.google import make_google_blueprint, google
import os


# Initialize app
app = Flask(__name__)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # Allow HTTP for localhost testing

google_bp = make_google_blueprint(
    client_id="297504059400-cq11f1iu5onrlovfau8rfmun9c3fhfjo.apps.googleusercontent.com",
    client_secret="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",
    scope=["profile", "email"],
    redirect_url="/login/callback"
)
app.register_blueprint(google_bp, url_prefix="/login")

# Load configuration
app.config['MYSQL_HOST'] = config.MYSQL_HOST
app.config['MYSQL_USER'] = config.MYSQL_USER
app.config['MYSQL_PASSWORD'] = config.MYSQL_PASSWORD
app.config['MYSQL_DB'] = config.MYSQL_DB
app.config['MYSQL_CURSORCLASS'] = config.MYSQL_CURSORCLASS
app.secret_key = config.SECRET_KEY

# Initialize MySQL
mysql = MySQL(app)

# Initialize Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# User Class
class User(UserMixin):
    def __init__(self, id, name, email, role):
        self.id = id
        self.name = name
        self.email = email
        self.role = role

# Load user function
@login_manager.user_loader
def load_user(user_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    if user:
        return User(user['id'], user['name'], user['email'], user['role'])
    return None

# ==============================
# Callback Routes
# ==============================

@app.route('/login/callback')
def login_callback():
    if not google.authorized:
        flash("Google login failed!", "danger")
        return redirect(url_for('login'))

    resp = google.get("/oauth2/v2/userinfo")
    assert resp.ok, resp.text
    user_info = resp.json()

    email = user_info['email']
    name = user_info.get('name', 'No Name')

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    user = cur.fetchone()

    if not user:
        # New user -> Auto signup into database
        cur.execute("INSERT INTO users (name, email) VALUES (%s, %s)", (name, email))
        mysql.connection.commit()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

    cur.close()

    user_obj = User(user['id'], user['name'], user['email'], user['role'])
    login_user(user_obj)

    flash('Logged in successfully with Google!', 'success')

    if user['role'] == 'admin':
        return redirect(url_for('admin_dashboard'))
    else:
        return redirect(url_for('dashboard'))

# ==============================
# Public and Driver Routes
# ==============================

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password)

        cur = mysql.connection.cursor()
        try:
            cur.execute("INSERT INTO users (name, email, password) VALUES (%s, %s, %s)", (name, email, hashed_password))
            mysql.connection.commit()
            flash('Account created successfully! Please log in.', 'success')
            return redirect(url_for('login'))
        except:
            flash('Error: Email already exists.', 'danger')
            return redirect(url_for('signup'))
        finally:
            cur.close()
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()

        if user and check_password_hash(user['password'], password):
            user_obj = User(user['id'], user['name'], user['email'], user['role'])
            login_user(user_obj)
            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                flash('Logged in successfully!', 'success')
                return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials.', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('home'))

from datetime import datetime, timedelta

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role != 'driver':
        return redirect(url_for('admin_dashboard'))

    cur = mysql.connection.cursor()

    # First: check bookings nearing expiry (within 1 hour)
    now = datetime.now()
    one_hour_later = now + timedelta(hours=1)

    cur.execute("""
        SELECT * FROM bookings 
        WHERE user_id = %s AND exit_time BETWEEN %s AND %s
    """, (current_user.id, now, one_hour_later))
    expiring_bookings = cur.fetchall()

    # Check if already notified, if not => create notification
    for booking in expiring_bookings:
        # Check if a notification already exists for this booking
        cur.execute("""
            SELECT * FROM notifications
            WHERE user_id = %s AND title = %s
        """, (current_user.id, f"Booking Ending Soon: {booking['spot_name']}"))
        existing = cur.fetchone()

        if not existing:
            # Insert new notification
            message = f"Your booking at {booking['spot_name']} will expire soon (Exit time: {booking['exit_time'].strftime('%Y-%m-%d %H:%M')})."
            cur.execute("""
                INSERT INTO notifications (user_id, title, message)
                VALUES (%s, %s, %s)
            """, (current_user.id, f"Booking Ending Soon: {booking['spot_name']}", message))
            mysql.connection.commit()

    # Now: fetch notifications normally
    cur.execute("""
        SELECT * FROM notifications
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (current_user.id,))
    notifications = cur.fetchall()

    cur.close()

    return render_template('dashboard.html', notifications=notifications)


@app.route('/book-parking', methods=['GET', 'POST'])
@login_required
def book_parking():
    if current_user.role != 'driver':
        return redirect(url_for('admin_dashboard'))

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM parking_spots")
    parking_spots = cur.fetchall()
    cur.close()

    return render_template('book_parking.html', parking_spots=parking_spots)

@app.route('/confirm-booking', methods=['POST'])
@login_required
def confirm_booking():
    spot_name = request.form['spot_name']
    price = request.form['price']
    booking_type = request.form['booking_type']
    entry_time = request.form['entry_time']
    exit_time = request.form['exit_time']

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO bookings (user_id, spot_name, price, booking_type, entry_time, exit_time)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (current_user.id, spot_name, price, booking_type, entry_time, exit_time))
    mysql.connection.commit()
    cur.close()

    flash('Parking spot booked successfully!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/booking-history')
@login_required
def booking_history():
    if current_user.role != 'driver':
        return redirect(url_for('admin_dashboard'))

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM bookings WHERE user_id = %s ORDER BY created_at DESC", (current_user.id,))
    bookings = cur.fetchall()
    cur.close()

    return render_template('booking_history.html', bookings=bookings)

@app.route('/personal-details', methods=['GET', 'POST'])
@login_required
def personal_details():
    if current_user.role != 'driver':
        return redirect(url_for('admin_dashboard'))

    cur = mysql.connection.cursor()
    if request.method == 'POST':
        phone_number = request.form['phone_number']
        vehicle_type = request.form['vehicle_type']
        vehicle_model_name = request.form['vehicle_model_name']
        vehicle_registration_no = request.form['vehicle_registration_no']

        cur.execute("""
            UPDATE users 
            SET phone_number = %s, vehicle_type = %s, vehicle_model_name = %s, vehicle_registration_no = %s
            WHERE id = %s
        """, (phone_number, vehicle_type, vehicle_model_name, vehicle_registration_no, current_user.id))
        mysql.connection.commit()
        flash('Personal details updated successfully!', 'success')
        return redirect(url_for('personal_details'))

    cur.execute("SELECT * FROM users WHERE id = %s", (current_user.id,))
    user = cur.fetchone()
    cur.close()

    return render_template('personal_details.html', user=user)

@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    if current_user.role != 'driver':
        return redirect(url_for('admin_dashboard'))

    cur = mysql.connection.cursor()
    if request.method == 'POST':
        booking_id = request.form['booking_id']
        rating = request.form['rating']
        comment = request.form['comment']

        cur.execute("""
            INSERT INTO feedbacks (user_id, booking_id, rating, comment)
            VALUES (%s, %s, %s, %s)
        """, (current_user.id, booking_id, rating, comment))
    mysql.connection.commit()
    flash('Thank you for your feedback!', 'success')
    return redirect(url_for('dashboard'))

    cur.execute("SELECT * FROM bookings WHERE user_id = %s", (current_user.id,))
    bookings = cur.fetchall()
    cur.close()

    return render_template('feedback.html', bookings=bookings)

# ==============================
# Admin Routes
# ==============================

@app.route('/admin-dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users WHERE role = 'driver'")
    users = cur.fetchall()

    cur.execute("""
        SELECT bookings.*, users.name as driver_name 
        FROM bookings 
        JOIN users ON bookings.user_id = users.id
        ORDER BY bookings.created_at DESC
    """)
    bookings = cur.fetchall()

    cur.execute("""
        SELECT feedbacks.*, users.name as driver_name, bookings.spot_name
        FROM feedbacks
        JOIN users ON feedbacks.user_id = users.id
        JOIN bookings ON feedbacks.booking_id = bookings.id
        ORDER BY feedbacks.created_at DESC
    """)
    feedbacks = cur.fetchall()

    cur.execute("SELECT * FROM parking_spots ORDER BY created_at DESC")
    parking_spots = cur.fetchall()

    cur.close()

    return render_template('admin_dashboard.html', users=users, bookings=bookings, feedbacks=feedbacks, parking_spots=parking_spots)

@app.route('/edit-driver/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_driver(user_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cur = mysql.connection.cursor()

    if request.method == 'POST':
        phone_number = request.form['phone_number']
        vehicle_type = request.form['vehicle_type']
        vehicle_model_name = request.form['vehicle_model_name']
        vehicle_registration_no = request.form['vehicle_registration_no']

        cur.execute("""
            UPDATE users
            SET phone_number = %s, vehicle_type = %s, vehicle_model_name = %s, vehicle_registration_no = %s
            WHERE id = %s
        """, (phone_number, vehicle_type, vehicle_model_name, vehicle_registration_no, user_id))
        mysql.connection.commit()
        flash('Driver updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()

    return render_template('edit_driver.html', user=user)

@app.route('/delete-feedback/<int:feedback_id>', methods=['POST'])
@login_required
def delete_feedback(feedback_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM feedbacks WHERE id = %s", (feedback_id,))
    mysql.connection.commit()
    cur.close()

    flash('Feedback deleted successfully!', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/add-parking-spot', methods=['GET', 'POST'])
@login_required
def add_parking_spot():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = request.form['name']
        address = request.form['address']
        latitude = request.form['latitude']
        longitude = request.form['longitude']
        price_hourly = request.form['price_hourly']
        price_monthly = request.form['price_monthly']

        cur = mysql.connection.cursor()
        cur.execute("""
            INSERT INTO parking_spots (name, address, latitude, longitude, price_hourly, price_monthly)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (name, address, latitude, longitude, price_hourly, price_monthly))
        mysql.connection.commit()
        cur.close()

        flash('Parking spot added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('add_parking_spot.html')

@app.route('/edit-parking-spot/<int:spot_id>', methods=['GET', 'POST'])
@login_required
def edit_parking_spot(spot_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cur = mysql.connection.cursor()

    if request.method == 'POST':
        name = request.form['name']
        address = request.form['address']
        latitude = request.form['latitude']
        longitude = request.form['longitude']
        price_hourly = request.form['price_hourly']
        price_monthly = request.form['price_monthly']

        cur.execute("""
            UPDATE parking_spots
            SET name = %s, address = %s, latitude = %s, longitude = %s, price_hourly = %s, price_monthly = %s
            WHERE id = %s
        """, (name, address, latitude, longitude, price_hourly, price_monthly, spot_id))
        mysql.connection.commit()
        flash('Parking spot updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    cur.execute("SELECT * FROM parking_spots WHERE id = %s", (spot_id,))
    spot = cur.fetchone()
    cur.close()

    return render_template('edit_parking_spot.html', spot=spot)

@app.route('/delete-parking-spot/<int:spot_id>', methods=['POST'])
@login_required
def delete_parking_spot(spot_id):
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM parking_spots WHERE id = %s", (spot_id,))
    mysql.connection.commit()
    cur.close()

    flash('Parking spot deleted successfully!', 'info')
    return redirect(url_for('admin_dashboard'))

@app.route('/send-notification', methods=['GET', 'POST'])
@login_required
def send_notification():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cur = mysql.connection.cursor()

    if request.method == 'POST':
        user_id = request.form['user_id']
        title = request.form['title']
        message = request.form['message']

        cur.execute("""
            INSERT INTO notifications (user_id, title, message)
            VALUES (%s, %s, %s)
        """, (user_id, title, message))
        mysql.connection.commit()
        cur.close()

        flash('Notification sent successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    # Get all drivers to show in dropdown
    cur.execute("SELECT id, name FROM users WHERE role = 'driver'")
    drivers = cur.fetchall()
    cur.close()

    return render_template('send_notification.html', drivers=drivers)

# Run app
if __name__ == '__main__':
    app.run(debug=True)
