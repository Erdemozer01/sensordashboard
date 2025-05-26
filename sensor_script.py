# sensor_script.py
from gpiozero import AngularServo, DistanceSensor, LED
import time
import sqlite3
import os
import sys
import fcntl
import atexit

# --- Pin Tanımlamaları ---
TRIG_PIN = 23
ECHO_PIN = 24
SERVO_PIN = 12  # Servo motorunuzu bağladığınız GPIO pini (Örnek, kendinize göre değiştirin!)

RED_LED_PIN = 17
GREEN_LED_PIN = 18
YELLOW_LED_PIN = 27

# --- Eşik Değerleri ve Sabitler ---
OBJECT_THRESHOLD_CM = 20.0
YELLOW_LED_THRESHOLD_CM = 100.0
TERMINATION_DISTANCE_CM = 10.0

SCAN_START_ANGLE = 0
SCAN_END_ANGLE = 180
SCAN_STEP_ANGLE = 10  # Daha hassas tarama için 5 veya daha düşük yapabilirsiniz
SERVO_SETTLE_TIME = 0.3  # Servonun pozisyonuna gelmesi ve sensörün sabitlenmesi için

DB_NAME = 'db.sqlite3'  # Veritabanı dosyasının adı
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'

# --- Global Donanım Değişkenleri ---
sensor = None
red_led = None
green_led = None
yellow_led = None
servo = None
lock_file_handle = None
current_scan_id = None  # Mevcut taramanın ID'si


def init_hardware():
    global sensor, red_led, green_led, yellow_led, servo
    try:
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        red_led = LED(RED_LED_PIN)
        green_led = LED(GREEN_LED_PIN)
        yellow_led = LED(YELLOW_LED_PIN)
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             min_pulse_width=0.0005, max_pulse_width=0.0025)  # SG90 için tipik
        print("Donanım başarıyla başlatıldı.")
        red_led.off();
        green_led.off();
        yellow_led.off()
        servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2  # Başlangıçta ortaya al
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"Donanım başlatma hatası: {e}")
        return False


def init_db():
    global current_scan_id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS servo_scans
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       start_time
                       REAL
                       UNIQUE,
                       status
                       TEXT
                   )
                   ''')
    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS scan_points
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       scan_id
                       INTEGER,
                       angle_deg
                       REAL,
                       mesafe_cm
                       REAL,
                       timestamp
                       REAL,
                       FOREIGN
                       KEY
                   (
                       scan_id
                   ) REFERENCES servo_scans
                   (
                       id
                   )
                       )
                   ''')
    # Önceki "running" durumundaki taramaları "interrupted" olarak işaretle (isteğe bağlı)
    cursor.execute("UPDATE servo_scans SET status = 'interrupted' WHERE status = 'running'")

    # Yeni bir tarama kaydı oluştur
    scan_start_time = time.time()
    cursor.execute("INSERT INTO servo_scans (start_time, status) VALUES (?, ?)", (scan_start_time, 'running'))
    current_scan_id = cursor.lastrowid  # Bu taramanın ID'sini al
    conn.commit()
    conn.close()
    print(f"Veritabanı '{DB_NAME}' hazırlandı. Yeni tarama ID: {current_scan_id}")


def acquire_lock():
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(f"Betik kilidi ({LOCK_FILE_PATH}) başarıyla alındı (PID: {os.getpid()}).")
        return True
    except BlockingIOError:
        print(f"'{LOCK_FILE_PATH}' kilitli. Sensör betiği zaten çalışıyor olabilir.")
        if lock_file_handle: lock_file_handle.close()
        return False
    except Exception as e:
        print(f"Kilit ({LOCK_FILE_PATH}) alınırken beklenmedik bir hata: {e}")
        if lock_file_handle: lock_file_handle.close()
        return False


def release_lock_and_update_scan_status(status='completed'):
    global lock_file_handle, current_scan_id
    if current_scan_id:  # Tarama ID'si varsa durumu güncelle
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE servo_scans SET status = ? WHERE id = ?", (status, current_scan_id))
            conn.commit()
            print(f"Tarama ID {current_scan_id} durumu '{status}' olarak güncellendi.")
        except Exception as e_db_update:
            print(f"Tarama durumu güncellenirken DB hatası: {e_db_update}")
        finally:
            if conn: conn.close()

    print("Program sonlanıyor, kilit dosyası serbest bırakılıyor...")
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
        except Exception as e_lock:
            print(f"Kilit serbest bırakılırken hata: {e_lock}")
    try:
        if os.path.exists(LOCK_FILE_PATH): os.remove(LOCK_FILE_PATH)
    except OSError as e_rm:
        print(f"Kilit dosyası ({LOCK_FILE_PATH}) silinirken hata: {e_rm}")


if __name__ == "__main__":
    atexit.register(release_lock_and_update_scan_status, 'interrupted_at_exit')  # Beklenmedik çıkışlar için

    if not acquire_lock():
        sys.exit(1)

    if not init_hardware():
        sys.exit(1)

    init_db()  # Bu fonksiyon current_scan_id'yi ayarlar
    if not current_scan_id:
        print("HATA: Tarama ID'si oluşturulamadı. Veritabanı hatası olabilir.")
        sys.exit(1)

    atexit.unregister(release_lock_and_update_scan_status)  # Önceki register'ı kaldır
    atexit.register(release_lock_and_update_scan_status,
                    status='interrupted_by_script_exit')  # Yeni status ile register et

    print(f"Servo ile 2D Tarama Başlıyor (Tarama ID: {current_scan_id})...")
    print(f"Açı Aralığı: {SCAN_START_ANGLE}° - {SCAN_END_ANGLE}° (Adım: {SCAN_STEP_ANGLE}°)")

    db_conn_main = None
    scan_completed_normally = False
    try:
        db_conn_main = sqlite3.connect(DB_NAME, timeout=10)
        cursor_main = db_conn_main.cursor()

        servo.angle = SCAN_START_ANGLE
        print(f"Servo başlangıç açısına ({SCAN_START_ANGLE}°) getirildi...")
        time.sleep(1.5)  # Servonun tam yerine oturması için

        for angle_deg in range(SCAN_START_ANGLE, SCAN_END_ANGLE + SCAN_STEP_ANGLE, SCAN_STEP_ANGLE):
            loop_start_time = time.time()

            servo.angle = angle_deg
            time.sleep(SERVO_SETTLE_TIME)

            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            print(f"  Açı: {angle_deg:3d}°, Mesafe: {distance_cm:6.2f} cm")

            try:
                cursor_main.execute('''
                                    INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, timestamp)
                                    VALUES (?, ?, ?, ?)
                                    ''', (current_scan_id, angle_deg, distance_cm, current_timestamp))
                db_conn_main.commit()
            except Exception as e_db_insert:
                print(f"DB Ekleme Hatası: {e_db_insert}")

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"DİKKAT: Nesne çok yakın ({distance_cm:.2f}cm)! Tarama acil durduruluyor.")
                release_lock_and_update_scan_status(status='terminated_close_object')
                scan_completed_normally = False  # Bu zaten atexit tarafından 'interrupted' olacak ama yine de
                sys.exit(1)  # Acil çıkış

            # LED Kontrolleri
            if distance_cm > YELLOW_LED_THRESHOLD_CM:
                yellow_led.on()
            else:
                yellow_led.toggle()

            max_distance_cm = sensor.max_distance * 100
            is_reading_valid = (distance_cm > 0.0) and (distance_cm < max_distance_cm)
            if is_reading_valid:
                if distance_cm <= OBJECT_THRESHOLD_CM:
                    red_led.on(); green_led.off()
                else:
                    green_led.on(); red_led.off()
            else:
                red_led.off();
                green_led.off()

            loop_processing_time = time.time() - loop_start_time
            sleep_duration = SERVO_SETTLE_TIME - loop_processing_time  # Aslında bu sleep_duration zaten servo_settle_time'ın bir parçası
            # Ek bir sleep gerekirse SCAN_STEP_DELAY gibi bir sabit eklenebilir.
            # Şimdilik sadece servo_settle_time yeterli.
            # Eğer tarama adımları arasında ek bekleme istenirse:
            # time.sleep(max(0, SOME_ADDITIONAL_DELAY - loop_processing_time))

        scan_completed_normally = True
        print("Tarama normal şekilde tamamlandı.")

    except KeyboardInterrupt:
        print("\nTarama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
        release_lock_and_update_scan_status(status='interrupted_ctrl_c')
    except Exception as e_main_loop:
        print(f"Tarama sırasında ana döngüde beklenmedik bir hata: {e_main_loop}")
        release_lock_and_update_scan_status(status=f'error_in_loop')
    finally:
        if scan_completed_normally:
            # atexit zaten 'interrupted_by_script_exit' yapacak normal çıkışta, bunu 'completed' yapalım.
            atexit.unregister(release_lock_and_update_scan_status)  # Önceki register'ı kaldır
            release_lock_and_update_scan_status(status='completed')  # Durumu 'completed' yap
        # Eğer KeyboardInterrupt veya başka bir exception ile çıkıldıysa, atexit'teki status geçerli olur.
        # Ya da burada spesifik olarak set edebiliriz. `atexit` daha genel bir fallback olur.

        if db_conn_main:
            db_conn_main.close()
            print("Ana veritabanı bağlantısı kapatıldı.")

        print("\nDonanım kapatılıyor...")
        if servo:
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
            time.sleep(0.5)
            servo.detach()
            print("Servo bağlantısı kesildi.")

        # LED'leri ve sensörü kapat (init_hardware içinde zaten global yapıldı)
        if red_led and hasattr(red_led, 'is_active') and red_led.is_active: red_led.off()
        if red_led and hasattr(red_led, 'close'): red_led.close()
        if green_led and hasattr(green_led, 'is_active') and green_led.is_active: green_led.off()
        if green_led and hasattr(green_led, 'close'): green_led.close()
        if yellow_led and hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        if yellow_led and hasattr(yellow_led, 'close'): yellow_led.close()
        if sensor and hasattr(sensor, 'close'): sensor.close()

        print("Sensör betiği sonlandı.")