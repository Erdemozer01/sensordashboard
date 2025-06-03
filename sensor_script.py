from gpiozero import DistanceSensor, LED, Buzzer, OutputDevice
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
TRIG_PIN, ECHO_PIN = 23, 24
IN1_GPIO_PIN, IN2_GPIO_PIN, IN3_GPIO_PIN, IN4_GPIO_PIN = 6, 13, 19, 26
YELLOW_LED_PIN, BUZZER_PIN = 27, 17
LCD_I2C_ADDRESS, LCD_PORT_EXPANDER, LCD_COLS, LCD_ROWS, I2C_PORT = 0x27, 'PCF8574', 16, 2, 1

# ==============================================================================
# --- Varsayılan Değerler ---
# ==============================================================================
DEFAULT_SCAN_DURATION_ANGLE = 270.0
DEFAULT_SCAN_STEP_ANGLE = 10.0
DEFAULT_BUZZER_DISTANCE = 10
DEFAULT_INVERT_MOTOR_DIRECTION = False
DEFAULT_STEPS_PER_REVOLUTION = 4096
STEP_MOTOR_INTER_STEP_DELAY, STEP_MOTOR_SETTLE_TIME, LOOP_TARGET_INTERVAL_S = 0.0015, 0.05, 0.6

# ==============================================================================
# --- Global Değişkenler ve Dosya Yolları ---
# ==============================================================================
try: PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError: PROJECT_ROOT_DIR = os.getcwd()
DB_NAME_ONLY, DB_PATH = 'live_scan_data.sqlite3', os.path.join(PROJECT_ROOT_DIR, 'live_scan_data.sqlite3')
LOCK_FILE_PATH, PID_FILE_PATH = '/tmp/sensor_scan_script.lock', '/tmp/sensor_scan_script.pid'
sensor, yellow_led, lcd, buzzer = None, None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
lock_file_handle, current_scan_id_global, db_conn_main_script_global = None, None, None
script_exit_status_global = 'interrupted_unexpectedly'
STEPS_PER_REVOLUTION_OUTPUT_SHAFT = DEFAULT_STEPS_PER_REVOLUTION
DEG_PER_STEP, current_motor_angle_global, current_step_sequence_index = 0.0, 0.0, 0
step_sequence = [[1,0,0,0],[1,1,0,0],[0,1,0,0],[0,1,1,0],[0,0,1,0],[0,0,1,1],[0,0,0,1],[1,0,0,1]]
SCAN_DURATION_ANGLE_PARAM, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION = DEFAULT_SCAN_DURATION_ANGLE, DEFAULT_SCAN_STEP_ANGLE, DEFAULT_BUZZER_DISTANCE, DEFAULT_INVERT_MOTOR_DIRECTION

# ==============================================================================
# --- Yardımcı Fonksiyonlar ---
# ==============================================================================
def init_hardware():
    global sensor, yellow_led, lcd, buzzer, current_motor_angle_global, in1_dev, in2_dev, in3_dev, in4_dev
    pid, hardware_ok = os.getpid(), True
    try:
        in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=2)
        yellow_led, buzzer = LED(YELLOW_LED_PIN), Buzzer(BUZZER_PIN)
        yellow_led.off(); buzzer.off()
        current_motor_angle_global = 0.0
    except Exception as e: print(f"[{pid}] Donanım Başlatma HATA: {e}"); hardware_ok = False
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=True)
            lcd.clear(); lcd.cursor_pos = (0,0); lcd.write_string("Tarama Hazir".ljust(LCD_COLS)); time.sleep(1.5)
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
    if DEG_PER_STEP <= 0: print(f"HATA: DEG_PER_STEP ({DEG_PER_STEP}) geçersiz!"); return

    normalized_current_angle = current_motor_angle_global % 360.0
    normalized_target_angle = target_angle_deg % 360.0
    angle_diff = normalized_target_angle - normalized_current_angle

    if abs(angle_diff) > 180.0:
        if angle_diff > 0: angle_diff -= 360.0
        else: angle_diff += 360.0

    if abs(angle_diff) < (DEG_PER_STEP / 2.0): return
    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0: return

    logical_dir_positive = (angle_diff > 0)
    physical_dir_positive = not logical_dir_positive if INVERT_MOTOR_DIRECTION else logical_dir_positive

    print(f"Motor Hareketi: Fiziksel {current_motor_angle_global:.1f}° (Norm: {normalized_current_angle:.1f}°) -> Hedef Fiz. {target_angle_deg:.1f}° (Norm: {normalized_target_angle:.1f}°). Fark: {angle_diff:.1f}°, Adım: {num_steps}, Yön: {'CCW' if logical_dir_positive else 'CW'}")
    _step_motor_4in(num_steps, physical_dir_positive)

    current_motor_angle_global_cumulative = current_motor_angle_global + (num_steps * DEG_PER_STEP * (1 if logical_dir_positive else -1))

    # Hedefe ulaşıldıysa, kümülatif açıyı hedefin tam değerine ayarla
    temp_normalized_current = current_motor_angle_global_cumulative % 360.0
    if abs(temp_normalized_current - normalized_target_angle) < DEG_PER_STEP or \
       abs(temp_normalized_current - (normalized_target_angle if normalized_target_angle != 0 else 360)) < DEG_PER_STEP :
         current_motor_angle_global = target_angle_deg # Kümülatif hedefi al
    else:
         current_motor_angle_global = current_motor_angle_global_cumulative


def init_db_for_scan(logical_start_angle, logical_end_angle):
    global current_scan_id_global
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS servo_scans (id INTEGER PRIMARY KEY, start_time REAL, status TEXT, hesaplanan_alan_cm2 REAL, cevre_cm REAL, max_genislik_cm REAL, max_derinlik_cm REAL, start_angle_setting REAL, end_angle_setting REAL, step_angle_setting REAL, buzzer_distance_setting REAL, invert_motor_direction_setting BOOLEAN)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS scan_points (id INTEGER PRIMARY KEY, scan_id INTEGER, derece REAL, mesafe_cm REAL, hiz_cm_s REAL DEFAULT 0, timestamp REAL, x_cm REAL, y_cm REAL, FOREIGN KEY (scan_id) REFERENCES servo_scans (id) ON DELETE CASCADE)''')
            cursor.execute("UPDATE servo_scans SET status='interrupted_prior_run' WHERE status='running'")
            cursor.execute("INSERT INTO servo_scans (start_time, status, start_angle_setting, end_angle_setting, step_angle_setting, buzzer_distance_setting, invert_motor_direction_setting) VALUES (?,?,?,?,?,?,?)", (time.time(), 'running', logical_start_angle, logical_end_angle, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION))
            current_scan_id_global = cursor.lastrowid
    except Exception as e: print(f"DB Hatası (init_db_for_scan): {e}"); current_scan_id_global = None

def acquire_lock_and_pid():
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w'); fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf: pf.write(str(os.getpid())); return True
    except Exception: return False

def release_resources_on_exit():
    global script_exit_status_global
    pid = os.getpid(); print(f"[{pid}] Kaynaklar serbest bırakılıyor... Durum: {script_exit_status_global}")
    if current_scan_id_global:
        try:
            with sqlite3.connect(DB_PATH, timeout=10) as conn: conn.execute("UPDATE servo_scans SET status=? WHERE id=?", (script_exit_status_global, current_scan_id_global))
        except Exception as e: print(f"DB çıkış HATA: {e}")
    _set_step_pins(0,0,0,0)
    for dev in [sensor, yellow_led, buzzer, in1_dev, in2_dev, in3_dev, in4_dev]:
        if dev and hasattr(dev, 'close'):
            try: dev.close()
            except Exception: pass
    if lcd:
        try: lcd.clear(); lcd.close()
        except Exception: pass
    if lock_file_handle:
        try: fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
        except Exception: pass
    for fp in [PID_FILE_PATH, LOCK_FILE_PATH]:
        if os.path.exists(fp):
            try: os.remove(fp)
            except OSError: pass
    print(f"[{pid}] Temizleme tamamlandı.")

def shoelace_formula(points): return 0.5 * abs(sum(points[i][0]*points[(i+1)%len(points)][1] - points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points))))
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
    parser.add_argument("--scan_duration_angle", type=float, default=DEFAULT_SCAN_DURATION_ANGLE)
    parser.add_argument("--step_angle", type=float, default=DEFAULT_SCAN_STEP_ANGLE)
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE)
    parser.add_argument("--invert_motor_direction", type=lambda x: str(x).lower()=='true', default=DEFAULT_INVERT_MOTOR_DIRECTION)
    parser.add_argument("--steps_per_rev", type=int, default=DEFAULT_STEPS_PER_REVOLUTION)
    args = parser.parse_args()

    SCAN_DURATION_ANGLE_PARAM = float(args.scan_duration_angle)
    SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM = float(args.step_angle), int(args.buzzer_distance)
    INVERT_MOTOR_DIRECTION, STEPS_PER_REVOLUTION_OUTPUT_SHAFT = bool(args.invert_motor_direction), int(args.steps_per_rev)

    pid = os.getpid(); atexit.register(release_resources_on_exit)
    if not acquire_lock_and_pid(): print(f"[{pid}] Başka bir betik çalışıyor. Çıkılıyor."); sys.exit(1)
    if not init_hardware(): sys.exit(1)

    DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT
    if SCAN_STEP_ANGLE < DEG_PER_STEP: SCAN_STEP_ANGLE = DEG_PER_STEP

    PHYSICAL_HOME_POSITION = 0.0
    INITIAL_RIGHT_TURN_DEGREES = 135.0
    PRE_SCAN_PHYSICAL_POSITION = (PHYSICAL_HOME_POSITION - INITIAL_RIGHT_TURN_DEGREES + 360.0) % 360.0

    LOGICAL_SCAN_START_ANGLE = 0.0
    LOGICAL_SCAN_END_ANGLE = SCAN_DURATION_ANGLE_PARAM

    init_db_for_scan(LOGICAL_SCAN_START_ANGLE, LOGICAL_SCAN_END_ANGLE)
    if not current_scan_id_global: print(f"[{pid}] Veritabanı oturumu oluşturulamadı. Çıkılıyor."); sys.exit(1)

    print(f"[{pid}] Yeni Otomatik Tarama Başlatılıyor...")
    if lcd: lcd.clear(); lcd.write_string(f"Scan: {LOGICAL_SCAN_END_ANGLE:.0f} derece".ljust(LCD_COLS))

    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        db_conn_main_script_global.execute("PRAGMA journal_mode = WAL;")
        cursor_main = db_conn_main_script_global.cursor()

        print(f"[{pid}] ADIM 0: Motor fiziksel başlangıç ({PHYSICAL_HOME_POSITION}°) pozisyonuna getiriliyor...")
        move_motor_to_angle(PHYSICAL_HOME_POSITION)
        time.sleep(0.5)

        print(f"[{pid}] ADIM 1: Tarama öncesi pozisyona ({PRE_SCAN_PHYSICAL_POSITION}°) dönülüyor...")
        move_motor_to_angle(PRE_SCAN_PHYSICAL_POSITION)
        time.sleep(1.0)

        physical_scan_reference_angle = current_motor_angle_global
        print(f"[{pid}] ADIM 2: Tarama başlıyor. Mantıksal [{LOGICAL_SCAN_START_ANGLE}° -> {LOGICAL_SCAN_END_ANGLE}°]. Fiziksel referans: {physical_scan_reference_angle:.1f}°")

        collected_points, current_logical_angle = [], LOGICAL_SCAN_START_ANGLE

        while True:
            target_physical_angle_for_step = physical_scan_reference_angle + current_logical_angle
            move_motor_to_angle(target_physical_angle_for_step)

            dist_cm = sensor.distance * 100
            angle_rad_for_calc = math.radians(current_logical_angle)
            x_cm, y_cm = dist_cm * math.cos(angle_rad_for_calc), dist_cm * math.sin(angle_rad_for_calc)

            print(f"  Okuma: Mantıksal {current_logical_angle:.1f}° (Fiz: {current_motor_angle_global:.1f}°) -> {dist_cm:.1f} cm")

            if lcd:
                try:
                    lcd.cursor_pos = (0, 0); lcd.write_string(f"Aci(L):{current_logical_angle:<6.1f}".ljust(LCD_COLS))
                    lcd.cursor_pos = (1, 0); lcd.write_string(f"Mesafe: {dist_cm:<5.1f}cm".ljust(LCD_COLS))
                except Exception: pass

            if 0 < dist_cm < (sensor.max_distance * 100 - 1): collected_points.append((x_cm, y_cm))
            cursor_main.execute("INSERT INTO scan_points (scan_id, derece, mesafe_cm, x_cm, y_cm, timestamp) VALUES (?,?,?,?,?,?)", (current_scan_id_global, current_logical_angle, dist_cm, x_cm, y_cm, time.time()))
            db_conn_main_script_global.commit()

            if dist_cm < BUZZER_DISTANCE_CM and buzzer: buzzer.on()
            elif buzzer: buzzer.off()

            if abs(current_logical_angle - LOGICAL_SCAN_END_ANGLE) < (SCAN_STEP_ANGLE / 20.0) or current_logical_angle >= LOGICAL_SCAN_END_ANGLE:
                print(f"[{pid}] Tarama bitti, mantıksal son açıya ({current_logical_angle:.1f}°) ulaşıldı."); break

            current_logical_angle += SCAN_STEP_ANGLE
            current_logical_angle = min(current_logical_angle, LOGICAL_SCAN_END_ANGLE)

            time.sleep(max(0, LOOP_TARGET_INTERVAL_S - STEP_MOTOR_SETTLE_TIME))

        if len(collected_points) >= 3:
            polygon = [(0,0)] + collected_points
            area, perimeter = shoelace_formula(polygon), calculate_perimeter(collected_points)
            x_coords = [p[0] for p in collected_points if isinstance(p, (list, tuple)) and len(p) > 0 and isinstance(p[0], (int,float))]
            y_coords = [p[1] for p in collected_points if isinstance(p, (list, tuple)) and len(p) > 1 and isinstance(p[1], (int,float))]
            width = (max(y_coords) - min(y_coords)) if y_coords else 0.0
            depth = max(x_coords) if x_coords else 0.0
            cursor_main.execute("UPDATE servo_scans SET hesaplanan_alan_cm2=?, cevre_cm=?, max_genislik_cm=?, max_derinlik_cm=?, status=? WHERE id=?", (area, perimeter, width, depth, 'completed_analysis', current_scan_id_global))
            script_exit_status_global = 'completed_analysis'
        else: script_exit_status_global = 'completed_insufficient_points'
        db_conn_main_script_global.commit()

    except KeyboardInterrupt: script_exit_status_global = 'interrupted_ctrl_c'; print(f"\n[{pid}] Ctrl+C ile kesildi.")
    except Exception as e: script_exit_status_global = 'error_in_loop'; import traceback; traceback.print_exc(); print(f"[{pid}] KRİTİK HATA: Ana döngüde: {e}")
    finally:
        if script_exit_status_global not in ['error_in_loop']:
             print(f"[{pid}] ADIM 3: İşlem sonu. Fiziksel başlangıca ({PHYSICAL_HOME_POSITION}°) geri dönülüyor...")
             move_motor_to_angle(PHYSICAL_HOME_POSITION)
             print(f"[{pid}] Fiziksel başlangıca dönüldü.")
        if db_conn_main_script_global: db_conn_main_script_global.close()
        print(f"[{pid}] Betik sonlanıyor.")