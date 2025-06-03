from gpiozero import DistanceSensor, LED, Buzzer, OutputDevice
from RPLCD.i2c import CharLCD
import time
import sqlite3
import os
import sys
import fcntl  # Dosya kilitleme için (Linux/macOS)
import atexit  # Çıkışta fonksiyon çalıştırmak için
import math
import argparse

# ==============================================================================
# --- Pin Tanımlamaları ve Donanım Ayarları ---
# ==============================================================================
TRIG_PIN = 23
ECHO_PIN = 24
IN1_GPIO_PIN = 6
IN2_GPIO_PIN = 13
IN3_GPIO_PIN = 19
IN4_GPIO_PIN = 26
YELLOW_LED_PIN = 27
BUZZER_PIN = 17
LCD_I2C_ADDRESS, LCD_PORT_EXPANDER, LCD_COLS, LCD_ROWS, I2C_PORT = 0x27, 'PCF8574', 16, 2, 1

# ==============================================================================
# --- Varsayılan Değerler ---
# ==============================================================================
DEFAULT_TERMINATION_DISTANCE_CM = 1
DEFAULT_BUZZER_DISTANCE = 10
DEFAULT_SCAN_STEP_ANGLE = 10.0
DEFAULT_INVERT_MOTOR_DIRECTION = False
DEFAULT_STEPS_PER_REVOLUTION = 4096  # Kalibrasyonla bulduğumuz doğru değer
STEP_MOTOR_INTER_STEP_DELAY = 0.0015
STEP_MOTOR_SETTLE_TIME = 0.05
LOOP_TARGET_INTERVAL_S = 0.6

# ==============================================================================
# --- Global Değişkenler ve Dosya Yolları ---
# ==============================================================================
try:
    PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()

DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH, PID_FILE_PATH = '/tmp/sensor_scan_script.lock', '/tmp/sensor_scan_script.pid'

# Donanım ve durum değişkenleri
sensor, yellow_led, lcd, buzzer = None, None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
lock_file_handle, current_scan_id_global, db_conn_main_script_global = None, None, None
script_exit_status_global = 'interrupted_unexpectedly'
STEPS_PER_REVOLUTION_OUTPUT_SHAFT = DEFAULT_STEPS_PER_REVOLUTION
DEG_PER_STEP = 0.0
current_motor_angle_global = 0.0
current_step_sequence_index = 0
step_sequence = [[1,0,0,0],[1,1,0,0],[0,1,0,0],[0,1,1,0],[0,0,1,0],[0,0,1,1],[0,0,0,1],[1,0,0,1]]
# Çalışma zamanı ayarları
BUZZER_DISTANCE_CM = DEFAULT_BUZZER_DISTANCE
SCAN_STEP_ANGLE = DEFAULT_SCAN_STEP_ANGLE
INVERT_MOTOR_DIRECTION = DEFAULT_INVERT_MOTOR_DIRECTION

# ==============================================================================
# --- Yardımcı Fonksiyonlar ---
# ==============================================================================

def init_hardware():
    global sensor, yellow_led, lcd, buzzer, current_motor_angle_global, in1_dev, in2_dev, in3_dev, in4_dev
    pid, hardware_ok = os.getpid(), True
    try:
        in1_dev = OutputDevice(IN1_GPIO_PIN, active_high=True, initial_value=False)
        in2_dev = OutputDevice(IN2_GPIO_PIN, active_high=True, initial_value=False)
        in3_dev = OutputDevice(IN3_GPIO_PIN, active_high=True, initial_value=False)
        in4_dev = OutputDevice(IN4_GPIO_PIN, active_high=True, initial_value=False)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=2)
        yellow_led, buzzer = LED(YELLOW_LED_PIN), Buzzer(BUZZER_PIN)
        current_motor_angle_global = 0.0
    except Exception as e: print(f"[{pid}] Donanım Başlatma HATA: {e}"); hardware_ok = False
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear(); lcd.cursor_pos = (0,0); lcd.write_string("Oto-Scan Hazir".ljust(LCD_COLS)); time.sleep(1.5)
        except Exception as e_lcd: print(f"[{pid}] LCD Başlatma UYARI: {e_lcd}"); lcd = None
    return hardware_ok

def _set_step_pins(s1, s2, s3, s4):
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)

def _step_motor_4in(num_steps, direction_positive):
    global current_step_sequence_index
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    time.sleep(STEP_MOTOR_SETTLE_TIME)

def move_motor_to_angle(target_angle_deg):
    global current_motor_angle_global
    if DEG_PER_STEP <= 0: return
    angle_diff = target_angle_deg - current_motor_angle_global
    if abs(angle_diff) < (DEG_PER_STEP / 2.0): return
    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0: return
    logical_dir_positive = (angle_diff > 0)
    physical_dir_positive = not logical_dir_positive if INVERT_MOTOR_DIRECTION else logical_dir_positive
    print(f"Motor Hareketi: {current_motor_angle_global:.1f}° -> {target_angle_deg:.1f}°, Adım: {num_steps}")
    _step_motor_4in(num_steps, physical_dir_positive)
    current_motor_angle_global += (num_steps * DEG_PER_STEP * (1 if logical_dir_positive else -1))

def init_db_for_scan():
    global current_scan_id_global
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()
        # Tabloları oluşturma (varsa değiştirmez)
        cursor.execute('''CREATE TABLE IF NOT EXISTS servo_scans (id INTEGER PRIMARY KEY, start_time REAL, status TEXT, hesaplanan_alan_cm2 REAL, cevre_cm REAL, max_genislik_cm REAL, max_derinlik_cm REAL, start_angle_setting REAL, end_angle_setting REAL, step_angle_setting REAL, buzzer_distance_setting REAL, invert_motor_direction_setting BOOLEAN)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS scan_points (id INTEGER PRIMARY KEY, scan_id INTEGER, derece REAL, mesafe_cm REAL, hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm, FOREIGN KEY (scan_id) REFERENCES servo_scans (id) ON DELETE CASCADE)''')
        # Önceki takılı kalmış taramaları temizle
        cursor.execute("UPDATE servo_scans SET status='interrupted_prior_run' WHERE status='running'")
        # Yeni tarama kaydı ekle
        cursor.execute("INSERT INTO servo_scans (start_time, status, start_angle_setting, end_angle_setting, step_angle_setting, buzzer_distance_setting, invert_motor_direction_setting) VALUES (?,?,?,?,?,?,?)", (time.time(), 'running', -135.0, 135.0, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION))
        current_scan_id_global = cursor.lastrowid
        conn.commit()
    except Exception as e: print(f"DB Hatası: {e}"); current_scan_id_global = None
    finally:
        if conn: conn.close()

def acquire_lock_and_pid():
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf: pf.write(str(os.getpid()))
        return True
    except Exception: return False

def release_resources_on_exit():
    global script_exit_status_global
    pid = os.getpid()
    print(f"[{pid}] Kaynaklar serbest bırakılıyor... Durum: {script_exit_status_global}")
    if current_scan_id_global:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE servo_scans SET status=? WHERE id=?", (script_exit_status_global, current_scan_id_global))
                conn.commit()
        except Exception as e: print(f"DB çıkış HATA: {e}")
    _set_step_pins(0,0,0,0)
    for dev in [in1_dev, in2_dev, in3_dev, in4_dev, yellow_led, buzzer, sensor]:
        if dev and hasattr(dev, 'close'): dev.close()
    if lcd:
        try: lcd.clear()
        except Exception: pass
    if lock_file_handle:
        try: fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
        except Exception: pass
    for fp in [PID_FILE_PATH, LOCK_FILE_PATH]:
        if os.path.exists(fp): os.remove(fp)
    print(f"[{pid}] Temizleme tamamlandı.")

def shoelace_formula(points): return 0.5 * abs(sum(p1[0]*p2[1] - p2[0]*p1[1] for p1, p2 in zip(points, points[1:] + points[:1])))
def calculate_perimeter(points):
    perimeter = math.hypot(points[0][0], points[0][1])
    for i in range(len(points) - 1): perimeter += math.hypot(points[i+1][0] - points[i][0], points[i+1][1] - points[i][1])
    perimeter += math.hypot(points[-1][0], points[-1][1])
    return perimeter

# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--step_angle", type=float, default=DEFAULT_SCAN_STEP_ANGLE)
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE)
    parser.add_argument("--invert_motor_direction", type=lambda x: str(x).lower()=='true', default=DEFAULT_INVERT_MOTOR_DIRECTION)
    parser.add_argument("--steps_per_rev", type=int, default=DEFAULT_STEPS_PER_REVOLUTION)
    args = parser.parse_args()

    SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM = float(args.step_angle), int(args.buzzer_distance)
    INVERT_MOTOR_DIRECTION, STEPS_PER_REVOLUTION_OUTPUT_SHAFT = bool(args.invert_motor_direction), int(args.steps_per_rev)
    
    pid = os.getpid()
    atexit.register(release_resources_on_exit)

    if not acquire_lock_and_pid(): print(f"[{pid}] Başka bir betik çalışıyor. Çıkılıyor."); sys.exit(1)
    if not init_hardware(): sys.exit(1)
    
    DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT
    if SCAN_STEP_ANGLE < DEG_PER_STEP: SCAN_STEP_ANGLE = DEG_PER_STEP

    init_db_for_scan()
    if not current_scan_id_global: print(f"[{pid}] Veritabanı oturumu oluşturulamadı. Çıkılıyor."); sys.exit(1)

    # --- Sabit Tarama Sekansı Tanımları ---
    HOME_POSITION, RIGHT_TURN_POSITION = 0.0, -135.0
    SCAN_START_ANGLE, SCAN_END_ANGLE = RIGHT_TURN_POSITION, RIGHT_TURN_POSITION + 270.0 # = +135.0
    
    print(f"[{pid}] Otomatik Tarama Senaryosu Başlatılıyor...")
    
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        # ADIM 1: Başlangıçta 135 Derece Sağa Dön
        print(f"[{pid}] ADIM 1: Merkeze ({HOME_POSITION}°) göre 135 derece sağa ({RIGHT_TURN_POSITION}°) dönülüyor...")
        move_motor_to_angle(RIGHT_TURN_POSITION)
        time.sleep(1.0)
        
        # ADIM 2: 270 Derece Sola Dönerek Tarama Yap
        print(f"[{pid}] ADIM 2: Tarama başlıyor. [{SCAN_START_ANGLE}°] -> [{SCAN_END_ANGLE}°]")
        
        collected_points = []
        target_angle = float(SCAN_START_ANGLE)
        scan_direction = 1 if SCAN_END_ANGLE >= SCAN_START_ANGLE else -1
        
        while True:
            move_motor_to_angle(target_angle)
            dist_cm = sensor.distance * 100
            angle_rad = math.radians(current_motor_angle_global)
            x_cm, y_cm = dist_cm * math.cos(angle_rad), dist_cm * math.sin(angle_rad)
            
            print(f"  Okuma: {current_motor_angle_global:.1f}° -> {dist_cm:.1f} cm")
            if lcd: lcd.write_string(f"A:{current_motor_angle_global:<5.1f} M:{dist_cm:5.1f}cm".ljust(LCD_COLS));
            
            if 0 < dist_cm < (sensor.max_distance * 100 - 1): collected_points.append((x_cm, y_cm))
            
            cursor_main.execute("INSERT INTO scan_points (scan_id, derece, mesafe_cm, x_cm, y_cm, timestamp) VALUES (?,?,?,?,?,?)", (current_scan_id_global, current_motor_angle_global, dist_cm, x_cm, y_cm, time.time()))
            
            if dist_cm < BUZZER_DISTANCE_CM: buzzer.on()
            else: buzzer.off()

            if (scan_direction == 1 and current_motor_angle_global >= SCAN_END_ANGLE) or (scan_direction == -1 and current_motor_angle_global <= SCAN_END_ANGLE):
                print(f"[{pid}] Tarama bitti, son açıya ulaşıldı."); break
            
            target_angle += (SCAN_STEP_ANGLE * scan_direction)
            time.sleep(LOOP_TARGET_INTERVAL_S)

        # Analiz ve Veritabanı Güncelleme
        if len(collected_points) >= 2:
            polygon = [(0,0)] + collected_points
            area, perimeter = shoelace_formula(polygon), calculate_perimeter(collected_points)
            x_coords, y_coords = [p[0] for p in collected_points], [p[1] for p in collected_points]
            width, depth = (max(y_coords) - min(y_coords)), max(x_coords)
            cursor_main.execute("UPDATE servo_scans SET hesaplanan_alan_cm2=?, cevre_cm=?, max_genislik_cm=?, max_derinlik_cm=? WHERE id=?", (area, perimeter, width, depth, current_scan_id_global))
            script_exit_status_global = 'completed_analysis'
        else:
            script_exit_status_global = 'completed_insufficient_points'
        
        db_conn_main_script_global.commit()

    except KeyboardInterrupt:
        script_exit_status_global = 'interrupted_ctrl_c'
        print(f"\n[{pid}] Ctrl+C ile kesildi.")
    except Exception as e:
        script_exit_status_global = 'error_in_loop'
        print(f"[{pid}] KRİTİK HATA: Ana döngüde: {e}")
    finally:
        # ADIM 3: Başlangıç Konumuna Geri Dön
        if script_exit_status_global not in ['error_in_loop']:
             print(f"[{pid}] ADIM 3: İşlem sonu. Merkeze ({HOME_POSITION}°) geri dönülüyor...")
             move_motor_to_angle(HOME_POSITION)
             print(f"[{pid}] Merkeze dönüldü.")
        
        if db_conn_main_script_global: db_conn_main_script_global.close()
        
        # atexit fonksiyonu geri kalanı halledecek
        print(f"[{pid}] Betik sonlanıyor.")