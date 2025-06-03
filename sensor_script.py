from gpiozero import DistanceSensor, LED, Buzzer, OutputDevice, Servo
from RPLCD.i2c import CharLCD
import time
import sqlite3
import os
import sys
import fcntl
import atexit
import math
import argparse

# ==============================================================================
# --- Pin Tanımlamaları ve Donanım Ayarları ---
# ==============================================================================
# Yatay Tarama (Stepper Motor)
TRIG_PIN, ECHO_PIN = 23, 24
IN1_GPIO_PIN, IN2_GPIO_PIN, IN3_GPIO_PIN, IN4_GPIO_PIN = 6, 13, 19, 26
# Dikey Tarama (Servo Motor) - 3D İÇİN EKLENDİ
SERVO_PIN = 18
# Diğer Donanımlar
YELLOW_LED_PIN, BUZZER_PIN = 27, 17
LCD_I2C_ADDRESS, LCD_PORT_EXPANDER, LCD_COLS, LCD_ROWS, I2C_PORT = 0x27, 'PCF8574', 16, 2, 1

# ==============================================================================
# --- Varsayılan Değerler ---
# ==============================================================================
# Yatay Tarama Ayarları
DEFAULT_HORIZONTAL_SCAN_ANGLE = 270.0
DEFAULT_HORIZONTAL_STEP_ANGLE = 10.0
DEFAULT_STEPS_PER_REVOLUTION = 4096
# Dikey Tarama Ayarları - 3D İÇİN EKLENDİ
DEFAULT_VERTICAL_START_ANGLE = -30.0  # Orta noktanın 30 derece altı
DEFAULT_VERTICAL_END_ANGLE = 30.0  # Orta noktanın 30 derece üstü
DEFAULT_VERTICAL_STEP_ANGLE = 15.0  # Her dikey adımın derecesi
# Genel Ayarlar
DEFAULT_BUZZER_DISTANCE = 10
DEFAULT_INVERT_MOTOR_DIRECTION = False
STEP_MOTOR_INTER_STEP_DELAY, STEP_MOTOR_SETTLE_TIME, LOOP_TARGET_INTERVAL_S = 0.0015, 0.05, 0.1
SERVO_SETTLE_TIME = 0.4  # Servo hareketinden sonra bekleme süresi

# ==============================================================================
# --- Global Değişkenler ve Dosya Yolları ---
# ==============================================================================
try:
    PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()
DB_NAME_ONLY, DB_PATH = '3d_scan_data.sqlite3', os.path.join(PROJECT_ROOT_DIR,
                                                             '3d_scan_data.sqlite3')  # DB Adı Değiştirildi
LOCK_FILE_PATH, PID_FILE_PATH = '/tmp/3d_sensor_scan_script.lock', '/tmp/3d_sensor_scan_script.pid'
sensor, yellow_led, lcd, buzzer, servo = None, None, None, None, None  # Servo eklendi
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
lock_file_handle, current_scan_id_global, db_conn_main_script_global = None, None, None
script_exit_status_global = 'interrupted_unexpectedly'
STEPS_PER_REVOLUTION_OUTPUT_SHAFT = DEFAULT_STEPS_PER_REVOLUTION
DEG_PER_STEP, current_motor_angle_global, current_step_sequence_index = 0.0, 0.0, 0
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0], [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1],
                 [1, 0, 0, 1]]
# Parametreler için global değişkenler
HORIZONTAL_SCAN_ANGLE, HORIZONTAL_STEP_ANGLE = DEFAULT_HORIZONTAL_SCAN_ANGLE, DEFAULT_HORIZONTAL_STEP_ANGLE
VERTICAL_START_ANGLE, VERTICAL_END_ANGLE, VERTICAL_STEP_ANGLE = DEFAULT_VERTICAL_START_ANGLE, DEFAULT_VERTICAL_END_ANGLE, DEFAULT_VERTICAL_STEP_ANGLE
BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION = DEFAULT_BUZZER_DISTANCE, DEFAULT_INVERT_MOTOR_DIRECTION


# ==============================================================================
# --- Yardımcı Fonksiyonlar ---
# ==============================================================================
def init_hardware():
    global sensor, yellow_led, lcd, buzzer, servo, current_motor_angle_global, in1_dev, in2_dev, in3_dev, in4_dev
    pid, hardware_ok = os.getpid(), True
    try:
        # Stepper ve Sensör
        in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(
            IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=2)
        # Servo - 3D İÇİN EKLENDİ
        servo = Servo(SERVO_PIN)
        servo.mid()  # Başlangıçta ortaya al
        time.sleep(1)
        servo.detach()  # Tıtremeyi önlemek için başlangıçta gücü kes
        # Diğer donanımlar
        yellow_led, buzzer = LED(YELLOW_LED_PIN), Buzzer(BUZZER_PIN)
        yellow_led.off();
        buzzer.off()
        current_motor_angle_global = 0.0
    except Exception as e:
        print(f"[{pid}] Donanım Başlatma HATA: {e}"); hardware_ok = False
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS,
                          rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=True)
            lcd.clear();
            lcd.cursor_pos = (0, 0);
            lcd.write_string("3D Tarama Hazir".ljust(LCD_COLS));
            time.sleep(1.5)
        except Exception as e_lcd:
            print(f"[{pid}] LCD Başlatma UYARI: {e_lcd}"); lcd = None
    return hardware_ok


# --- 3D İÇİN YENİ: Servo Kontrol Fonksiyonu ---
# gpiozero Servo'nun `value` özelliği -1 (en sol) ile 1 (en sağ) arasında çalışır.
# Biz ise -90 ile 90 derece arasını kullanacağız. Bu fonksiyon bu dönüşümü yapar.
def move_servo_to_vertical_angle(angle_deg):
    if not servo: return
    # Açıyı -1 ile 1 arasına haritala (-90 derece = -1, 0 derece = 0, 90 derece = 1)
    # Bu, 180 derecelik bir toplam aralığı varsayar.
    servo_value = max(-1.0, min(1.0, angle_deg / 90.0))
    servo.value = servo_value
    time.sleep(SERVO_SETTLE_TIME)


# --- Stepper Motor Fonksiyonları (Değişiklik yok) ---
def _set_step_pins(s1, s2, s3, s4):
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)


def _step_motor_4in(num_steps, direction_positive):
    global current_step_sequence_index
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
            step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    time.sleep(STEP_MOTOR_SETTLE_TIME)


def move_motor_to_angle(target_angle_deg):
    global current_motor_angle_global
    if DEG_PER_STEP <= 0: return
    normalized_current_angle = current_motor_angle_global % 360.0
    normalized_target_angle = target_angle_deg % 360.0
    angle_diff = normalized_target_angle - normalized_current_angle
    if abs(angle_diff) > 180.0:
        if angle_diff > 0:
            angle_diff -= 360.0
        else:
            angle_diff += 360.0
    if abs(angle_diff) < (DEG_PER_STEP / 2.0): return
    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0: return
    logical_dir_positive = (angle_diff > 0)
    physical_dir_positive = not logical_dir_positive if INVERT_MOTOR_DIRECTION else logical_dir_positive
    _step_motor_4in(num_steps, physical_dir_positive)
    current_motor_angle_global_cumulative = current_motor_angle_global + (
                num_steps * DEG_PER_STEP * (1 if logical_dir_positive else -1))
    temp_normalized_current = current_motor_angle_global_cumulative % 360.0
    if abs(temp_normalized_current - normalized_target_angle) < DEG_PER_STEP or \
            abs(temp_normalized_current - (
            normalized_target_angle if normalized_target_angle != 0 else 360)) < DEG_PER_STEP:
        current_motor_angle_global = target_angle_deg
    else:
        current_motor_angle_global = current_motor_angle_global_cumulative


# --- 3D İÇİN DEĞİŞTİRİLDİ: Veritabanı Fonksiyonu ---
def init_db_for_scan():
    global current_scan_id_global
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            cursor = conn.cursor()
            # 2D analiz sütunları kaldırıldı, 3D ayarları eklendi
            cursor.execute('''CREATE TABLE IF NOT EXISTS servo_scans
                              (
                                  id
                                  INTEGER
                                  PRIMARY
                                  KEY,
                                  start_time
                                  REAL,
                                  status
                                  TEXT,
                                  horizontal_scan_angle
                                  REAL,
                                  horizontal_step_angle
                                  REAL,
                                  vertical_start_angle
                                  REAL,
                                  vertical_end_angle
                                  REAL,
                                  vertical_step_angle
                                  REAL,
                                  buzzer_distance_setting
                                  REAL,
                                  invert_motor_direction_setting
                                  BOOLEAN
                              )''')
            # 'derece' -> 'yatay_derece', 'dikey_derece' ve 'z_cm' eklendi
            cursor.execute('''CREATE TABLE IF NOT EXISTS scan_points
            (
                id
                INTEGER
                PRIMARY
                KEY,
                scan_id
                INTEGER,
                yatay_derece
                REAL,
                dikey_derece
                REAL,
                mesafe_cm
                REAL,
                timestamp
                REAL,
                x_cm
                REAL,
                y_cm
                REAL,
                z_cm
                REAL,
                FOREIGN
                KEY
                              (
                scan_id
                              ) REFERENCES servo_scans
                              (
                                  id
                              ) ON DELETE CASCADE)''')
            cursor.execute("UPDATE servo_scans SET status='interrupted_prior_run' WHERE status='running'")
            cursor.execute("""
                           INSERT INTO servo_scans (start_time, status, horizontal_scan_angle, horizontal_step_angle,
                                                    vertical_start_angle, vertical_end_angle, vertical_step_angle,
                                                    buzzer_distance_setting, invert_motor_direction_setting)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (time.time(), 'running', HORIZONTAL_SCAN_ANGLE, HORIZONTAL_STEP_ANGLE,
                            VERTICAL_START_ANGLE, VERTICAL_END_ANGLE, VERTICAL_STEP_ANGLE,
                            BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION))
            current_scan_id_global = cursor.lastrowid
    except Exception as e:
        print(f"DB Hatası (init_db_for_scan): {e}"); current_scan_id_global = None


def acquire_lock_and_pid():
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w');
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid())); return True
    except Exception:
        return False


def release_resources_on_exit():
    global script_exit_status_global
    pid = os.getpid();
    print(f"[{pid}] Kaynaklar serbest bırakılıyor... Durum: {script_exit_status_global}")
    if current_scan_id_global:
        try:
            with sqlite3.connect(DB_PATH, timeout=10) as conn:
                conn.execute("UPDATE servo_scans SET status=? WHERE id=?",
                             (script_exit_status_global, current_scan_id_global))
        except Exception as e:
            print(f"DB çıkış HATA: {e}")
    _set_step_pins(0, 0, 0, 0)
    # Servo dahil tüm donanımları kapat
    for dev in [sensor, yellow_led, buzzer, servo, in1_dev, in2_dev, in3_dev, in4_dev]:
        if dev and hasattr(dev, 'close'):
            try:
                dev.close()
            except Exception:
                pass
    if lcd:
        try:
            lcd.clear(); lcd.close()
        except Exception:
            pass
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
        except Exception:
            pass
    for fp in [PID_FILE_PATH, LOCK_FILE_PATH]:
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
    print(f"[{pid}] Temizleme tamamlandı.")


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU (3D Tarama için güncellendi) ---
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="3D Scanner Control Script")
    # Yatay Tarama Argümanları
    parser.add_argument("--h_scan_angle", type=float, default=DEFAULT_HORIZONTAL_SCAN_ANGLE)
    parser.add_argument("--h_step_angle", type=float, default=DEFAULT_HORIZONTAL_STEP_ANGLE)
    # Dikey Tarama Argümanları
    parser.add_argument("--v_start_angle", type=float, default=DEFAULT_VERTICAL_START_ANGLE)
    parser.add_argument("--v_end_angle", type=float, default=DEFAULT_VERTICAL_END_ANGLE)
    parser.add_argument("--v_step_angle", type=float, default=DEFAULT_VERTICAL_STEP_ANGLE)
    # Genel Argümanlar
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE)
    parser.add_argument("--invert_motor_direction", type=lambda x: str(x).lower() == 'true',
                        default=DEFAULT_INVERT_MOTOR_DIRECTION)
    parser.add_argument("--steps_per_rev", type=int, default=DEFAULT_STEPS_PER_REVOLUTION)
    args = parser.parse_args()

    # Parametreleri ata
    HORIZONTAL_SCAN_ANGLE, HORIZONTAL_STEP_ANGLE = args.h_scan_angle, args.h_step_angle
    VERTICAL_START_ANGLE, VERTICAL_END_ANGLE, VERTICAL_STEP_ANGLE = args.v_start_angle, args.v_end_angle, args.v_step_angle
    BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION = args.buzzer_distance, args.invert_motor_direction
    STEPS_PER_REVOLUTION_OUTPUT_SHAFT = args.steps_per_rev

    pid = os.getpid();
    atexit.register(release_resources_on_exit)
    if not acquire_lock_and_pid(): print(f"[{pid}] Başka bir betik çalışıyor. Çıkılıyor."); sys.exit(1)
    if not init_hardware(): sys.exit(1)

    DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT
    if HORIZONTAL_STEP_ANGLE < DEG_PER_STEP: HORIZONTAL_STEP_ANGLE = DEG_PER_STEP

    PHYSICAL_HOME_POSITION = 0.0
    PRE_SCAN_PHYSICAL_POSITION = (PHYSICAL_HOME_POSITION - (HORIZONTAL_SCAN_ANGLE / 2.0) + 360.0) % 360.0

    LOGICAL_SCAN_START_ANGLE = 0.0
    LOGICAL_SCAN_END_ANGLE = HORIZONTAL_SCAN_ANGLE

    init_db_for_scan()
    if not current_scan_id_global: print(f"[{pid}] Veritabanı oturumu oluşturulamadı. Çıkılıyor."); sys.exit(1)

    print(f"[{pid}] Yeni 3D Tarama Başlatılıyor...")
    if lcd: lcd.clear(); lcd.write_string(f"3D Scan Basliyor".ljust(LCD_COLS))

    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        db_conn_main_script_global.execute("PRAGMA journal_mode = WAL;")
        cursor_main = db_conn_main_script_global.cursor()

        print(f"[{pid}] ADIM 0: Motorlar başlangıç pozisyonuna getiriliyor...")
        move_motor_to_angle(PRE_SCAN_PHYSICAL_POSITION)  # Yatay motor tarama başlangıcına
        move_servo_to_vertical_angle(VERTICAL_START_ANGLE)  # Dikey motor tarama başlangıcına
        time.sleep(1.0)

        physical_scan_reference_angle = current_motor_angle_global
        print(f"[{pid}] ADIM 1: Tarama başlıyor...")
        if yellow_led: yellow_led.blink()

        # --- DIŞ DÖNGÜ: DİKEY TARAMA ---
        v_angle_range = range(int(VERTICAL_START_ANGLE), int(VERTICAL_END_ANGLE) + 1, int(VERTICAL_STEP_ANGLE))
        for current_vertical_angle in v_angle_range:
            move_servo_to_vertical_angle(current_vertical_angle)
            print(f"Dikey Açı Ayarlandı: {current_vertical_angle}°")

            # --- İÇ DÖNGÜ: YATAY TARAMA ---
            current_logical_angle = LOGICAL_SCAN_START_ANGLE
            while True:
                target_physical_angle_for_step = physical_scan_reference_angle + current_logical_angle
                move_motor_to_angle(target_physical_angle_for_step)

                dist_cm = sensor.distance * 100

                # --- 3D Koordinat Hesaplaması ---
                h_rad = math.radians(current_logical_angle)
                v_rad = math.radians(current_vertical_angle)

                # Küresel koordinatlardan Kartezyen'e dönüşüm
                x_cm = dist_cm * math.cos(v_rad) * math.cos(h_rad)
                y_cm = dist_cm * math.cos(v_rad) * math.sin(h_rad)
                z_cm = dist_cm * math.sin(v_rad)

                print(
                    f"  H:{current_logical_angle:.1f}° V:{current_vertical_angle:.1f}° -> {dist_cm:.1f} cm (X:{x_cm:.1f}, Y:{y_cm:.1f}, Z:{z_cm:.1f})")

                if lcd:
                    try:
                        lcd.cursor_pos = (0, 0);
                        lcd.write_string(
                            f"H:{current_logical_angle:<4.0f} V:{current_vertical_angle:<4.0f}".ljust(LCD_COLS))
                        lcd.cursor_pos = (1, 0);
                        lcd.write_string(f"Mesafe: {dist_cm:<5.1f}cm".ljust(LCD_COLS))
                    except Exception:
                        pass

                # Veritabanına 3D veriyi kaydet
                cursor_main.execute("""INSERT INTO scan_points
                                       (scan_id, yatay_derece, dikey_derece, mesafe_cm, x_cm, y_cm, z_cm, timestamp)
                                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (current_scan_id_global, current_logical_angle, current_vertical_angle, dist_cm,
                                     x_cm, y_cm, z_cm, time.time()))
                db_conn_main_script_global.commit()

                if dist_cm < BUZZER_DISTANCE_CM and buzzer:
                    buzzer.on()
                elif buzzer:
                    buzzer.off()

                # Yatay taramanın sonu
                if abs(current_logical_angle - LOGICAL_SCAN_END_ANGLE) < (
                        HORIZONTAL_STEP_ANGLE / 2.0) or current_logical_angle >= LOGICAL_SCAN_END_ANGLE:
                    break

                current_logical_angle += HORIZONTAL_STEP_ANGLE
                current_logical_angle = min(current_logical_angle, LOGICAL_SCAN_END_ANGLE)

                time.sleep(max(0, LOOP_TARGET_INTERVAL_S - STEP_MOTOR_SETTLE_TIME))

        script_exit_status_global = 'completed_3d_scan'
        print(f"[{pid}] 3D Tarama başarıyla tamamlandı.")

    except KeyboardInterrupt:
        script_exit_status_global = 'interrupted_ctrl_c'; print(f"\n[{pid}] Ctrl+C ile kesildi.")
    except Exception as e:
        script_exit_status_global = 'error_in_loop'; import traceback; traceback.print_exc(); print(
            f"[{pid}] KRİTİK HATA: Ana döngüde: {e}")
    finally:
        if yellow_led: yellow_led.off()

        if script_exit_status_global not in ['error_in_loop']:
            print(f"[{pid}] ADIM 2: İşlem sonu. Motorlar başlangıç pozisyonuna geri dönüyor...")
            move_motor_to_angle(PHYSICAL_HOME_POSITION)
            if servo:
                servo.mid()
                time.sleep(1)
                servo.detach()
            print(f"[{pid}] Motorlar başlangıç pozisyonuna döndü.")
        if db_conn_main_script_global: db_conn_main_script_global.close()
        print(f"[{pid}] Betik sonlanıyor.")