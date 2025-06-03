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
# --- Ultrasonik Sensör Pinleri ---
TRIG_PIN = 23
ECHO_PIN = 24

# --- Step Motor Pin Tanımlamaları (ULN2003 Sürücü Kartı için Örnek) ---
IN1_GPIO_PIN = 6
IN2_GPIO_PIN = 13
IN3_GPIO_PIN = 19
IN4_GPIO_PIN = 26

# --- Diğer Donanım Pinleri ---
YELLOW_LED_PIN = 27
BUZZER_PIN = 17

# --- LCD Ayarları ---
LCD_I2C_ADDRESS = 0x27
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1

# ==============================================================================
# --- Varsayılan Tarama ve Eşik Değerleri ---
# ==============================================================================
DEFAULT_TERMINATION_DISTANCE_CM = 1
DEFAULT_BUZZER_DISTANCE = 10
DEFAULT_SCAN_START_ANGLE = 0.0
DEFAULT_SCAN_END_ANGLE = 180.0
DEFAULT_SCAN_STEP_ANGLE = 10.0
DEFAULT_INVERT_MOTOR_DIRECTION = False

# --- Step Motor Zamanlama Ayarları ---
STEP_MOTOR_INTER_STEP_DELAY = 0.0015
STEP_MOTOR_SETTLE_TIME = 0.05
LOOP_TARGET_INTERVAL_S = 0.6

# ==============================================================================
# --- Dosya Yolları ve Global Değişkenler ---
# ==============================================================================
try:
    PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()

DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

sensor, yellow_led, lcd, buzzer = None, None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
lock_file_handle, current_scan_id_global, db_conn_main_script_global = None, None, None
script_exit_status_global = 'interrupted_unexpectedly'

# ==============================================================================
# --- Step Motor Özellikleri ---
# !!! DİKKAT: BU DEĞER ARTIK PANEL ÜZERİNDEN KONTROL EDİLMEKTEDİR !!!
# ==============================================================================
# Aşağıdaki STEPS_PER_REVOLUTION_OUTPUT_SHAFT sabitini bir varsayılan değere dönüştürüyoruz.
DEFAULT_STEPS_PER_REVOLUTION = 4096
STEPS_PER_REVOLUTION_OUTPUT_SHAFT = DEFAULT_STEPS_PER_REVOLUTION # Başlangıçta varsayılana ayarlı.

DEG_PER_STEP = 0.0  # Başlangıçta tanımla, main içinde doğru değer atanacak
current_motor_angle_global = 0.0
current_step_sequence_index = 0

# Yarım adım (half-step) sekansı (8 adım)
step_sequence = [
    [1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
    [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]
]

# --- Çalışma Zamanı Ayarları (Argümanlarla Değişebilir) ---
TERMINATION_DISTANCE_CM = DEFAULT_TERMINATION_DISTANCE_CM
BUZZER_DISTANCE_CM = DEFAULT_BUZZER_DISTANCE
SCAN_START_ANGLE = DEFAULT_SCAN_START_ANGLE
SCAN_END_ANGLE = DEFAULT_SCAN_END_ANGLE
SCAN_STEP_ANGLE = DEFAULT_SCAN_STEP_ANGLE
INVERT_MOTOR_DIRECTION = DEFAULT_INVERT_MOTOR_DIRECTION


# ==============================================================================
# --- Donanım Başlatma, Step Motor Kontrol ve Diğer Yardımcı Fonksiyonlar ---
# ==============================================================================
def init_hardware():
    global sensor, yellow_led, lcd, buzzer, current_motor_angle_global
    global in1_dev, in2_dev, in3_dev, in4_dev
    hardware_ok = True;
    pid = os.getpid()
    try:
        print(f"[{pid}] Donanımlar başlatılıyor...")
        in1_dev = OutputDevice(IN1_GPIO_PIN, active_high=True, initial_value=False)
        in2_dev = OutputDevice(IN2_GPIO_PIN, active_high=True, initial_value=False)
        in3_dev = OutputDevice(IN3_GPIO_PIN, active_high=True, initial_value=False)
        in4_dev = OutputDevice(IN4_GPIO_PIN, active_high=True, initial_value=False)
        print(f"[{pid}] Step motor pinleri ayarlandı (gpiozero).")
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN);
        yellow_led.off()
        buzzer = Buzzer(BUZZER_PIN);
        buzzer.off()
        print(f"[{pid}] Step motor başlangıç pozisyonuna ({SCAN_START_ANGLE:.1f}°) ayarlanıyor...")
        current_motor_angle_global = 0.0
        move_motor_to_angle(SCAN_START_ANGLE)
        print(f"[{pid}] Step motor yaklaşık {current_motor_angle_global:.2f}° pozisyonuna getirildi.")
        print(f"[{pid}] Motor yönü ters çevirme: {'Aktif' if INVERT_MOTOR_DIRECTION else 'Pasif'}")
    except Exception as e:
        print(f"[{pid}] Donanım başlatma HATA: {e}"); hardware_ok = False
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS,
                          rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear();
            lcd.cursor_pos = (0, 0);
            lcd.write_string("DreamPi Step".ljust(LCD_COLS));
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("gpiozero Hazir".ljust(LCD_COLS))
            time.sleep(1.5);
            print(f"[{pid}] LCD başlatıldı.")
        except Exception as e_lcd:
            print(f"[{pid}] LCD başlatma UYARI: {e_lcd}"); lcd = None
    else:
        lcd = None
    return hardware_ok


def _set_step_pins(s1, s2, s3, s4):
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)


def _step_motor_4in(num_steps, actual_physical_direction_positive):
    global current_step_sequence_index
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (
            1 if actual_physical_direction_positive else -1) + len(step_sequence)) % len(step_sequence)
        phase = step_sequence[current_step_sequence_index]
        _set_step_pins(phase[0], phase[1], phase[2], phase[3])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    time.sleep(STEP_MOTOR_SETTLE_TIME)


def move_motor_to_angle(target_angle_deg):
    global current_motor_angle_global, INVERT_MOTOR_DIRECTION, DEG_PER_STEP
    if DEG_PER_STEP <= 0: print(f"[{os.getpid()}] HATA: DEG_PER_STEP ({DEG_PER_STEP}) geçersiz!"); return

    angle_diff_deg = target_angle_deg - current_motor_angle_global
    if abs(angle_diff_deg) < (DEG_PER_STEP / 2.0): return

    num_steps_to_move = round(abs(angle_diff_deg) / DEG_PER_STEP)
    if num_steps_to_move == 0: return

    logical_direction_positive = (angle_diff_deg > 0)
    physical_direction_positive = not logical_direction_positive if INVERT_MOTOR_DIRECTION else logical_direction_positive

    print(
        f"[{os.getpid()}] Motor: {current_motor_angle_global:.2f}° -> {target_angle_deg:.2f}°. Fark:{angle_diff_deg:.2f}°. Adım:{num_steps_to_move}. MantıksalYön:{'+' if logical_direction_positive else '-'}. FizikselYön:{'+Seq' if physical_direction_positive else '-Seq'}.")
    _step_motor_4in(num_steps_to_move, physical_direction_positive)

    current_motor_angle_global += (num_steps_to_move * DEG_PER_STEP * (1 if logical_direction_positive else -1))
    if abs(current_motor_angle_global - target_angle_deg) < DEG_PER_STEP: current_motor_angle_global = float(
        target_angle_deg)
    print(f"[{os.getpid()}] Motor Yeni Pozisyon (Tahmini): {current_motor_angle_global:.2f}°")


def init_db_for_scan():
    global current_scan_id_global
    pid = os.getpid()
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS servo_scans
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL UNIQUE, status TEXT,
                           hesaplanan_alan_cm2 REAL DEFAULT NULL, cevre_cm REAL DEFAULT NULL,
                           max_genislik_cm REAL DEFAULT NULL, max_derinlik_cm REAL DEFAULT NULL,
                           start_angle_setting REAL, end_angle_setting REAL DEFAULT NULL,
                           step_angle_setting REAL DEFAULT NULL, buzzer_distance_setting REAL,
                           invert_motor_direction_setting BOOLEAN DEFAULT FALSE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS scan_points
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, derece REAL, mesafe_cm REAL,
                           hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm REAL,
                           FOREIGN KEY (scan_id) REFERENCES servo_scans (id) ON DELETE CASCADE)''')
        cursor.execute("UPDATE servo_scans SET status='interrupted_prior_run' WHERE status='running'")
        st = time.time()
        cursor.execute(
            "INSERT INTO servo_scans (start_time,status,start_angle_setting,end_angle_setting,step_angle_setting,buzzer_distance_setting,invert_motor_direction_setting) VALUES (?,?,?,?,?,?,?)",
            (st, 'running', SCAN_START_ANGLE, SCAN_END_ANGLE, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM,
             INVERT_MOTOR_DIRECTION)
        )
        current_scan_id_global = cursor.lastrowid
        conn.commit()
        print(f"[{pid}] DB '{DB_PATH}' hazır. ID: {current_scan_id_global}")
    except sqlite3.Error as e:
        print(f"[{pid}] DB HATA: {e}")
        current_scan_id_global = None
    finally:
        if conn:
            conn.close()


def acquire_lock_and_pid():
    global lock_file_handle
    pid = os.getpid()
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(pid))
        print(f"[{pid}] Kilit ve PID oluşturuldu.")
        return True
    except BlockingIOError:
        ep = '?'
        try:
            with open(PID_FILE_PATH, 'r') as pf_check:
                ep = pf_check.read().strip()
        except Exception:
            pass
        print(f"[{pid}] UYARI: Kilit dosyası mevcut. PID: {ep}. Çıkılıyor.")
        return False
    except Exception as e:
        print(f"[{pid}] Kilit HATA: {e}")
        return False


def shoelace_formula(n):
    area = 0.0
    l = len(n)
    if l < 3:
        return 0.0
    for i in range(l):
        area += (n[i][0] * n[(i + 1) % l][1]) - (n[(i + 1) % l][0] * n[i][1])
    return abs(area) / 2.0


def calculate_perimeter(cp):
    p, l = 0.0, len(cp)
    if l == 0:
        return 0.0
    p += math.sqrt(cp[0][0] ** 2 + cp[0][1] ** 2)
    for i in range(l - 1):
        p += math.sqrt((cp[i + 1][0] - cp[i][0]) ** 2 + (cp[i + 1][1] - cp[i][1]) ** 2)
    p += math.sqrt(cp[-1][0] ** 2 + cp[-1][1] ** 2)
    return p


def release_resources_on_exit():
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, yellow_led, lcd, buzzer, in1_dev, in2_dev, in3_dev, in4_dev
    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Durum: {script_exit_status_global}")
    if db_conn_main_script_global:
        try:
            db_conn_main_script_global.close()
        except Exception:
            pass
    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            cursor_exit.execute("SELECT status FROM servo_scans WHERE id=?", (current_scan_id_global,))
            db_stat = cursor_exit.fetchone()
            if db_stat and db_stat[0] == 'running':
                expected = ['completed_analysis', 'completed_insufficient_points', 'terminated_close_object',
                            'interrupted_ctrl_c', 'error_in_loop']
                final_stat = script_exit_status_global if script_exit_status_global in expected else 'interrupted_unexpectedly'
                cursor_exit.execute("UPDATE servo_scans SET status=? WHERE id=?", (final_stat, current_scan_id_global))
                conn_exit.commit()
        except Exception as e:
            print(f"[{pid}] DB çıkış HATA: {e}")
        finally:
            if conn_exit:
                conn_exit.close()
    print(f"[{pid}] Donanım kapatılıyor...")
    try:
        _set_step_pins(0, 0, 0, 0)
        print(f"[{pid}] Step motor pinleri LOW yapıldı.")
    except Exception:
        pass
    devs_to_close = [in1_dev, in2_dev, in3_dev, in4_dev, yellow_led, buzzer, sensor]
    for dev in devs_to_close:
        if dev and hasattr(dev, 'close'):
            try:
                if hasattr(dev, 'is_active') and getattr(dev, 'is_active', False):
                    dev.off()
                dev.close()
            except Exception:
                pass
    print(f"[{pid}] gpiozero cihazları kapatıldı.")
    if lcd:
        try:
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1)
            lcd.clear()
        except Exception:
            pass
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
        except Exception:
            pass
    for fp in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(fp):
                if fp == PID_FILE_PATH:
                    cdel = False
                    try:
                        with open(fp, 'r') as pfc:
                            if int(pfc.read().strip()) == pid:
                                cdel = True
                    except Exception:
                        pass
                    if cdel:
                        os.remove(fp)
                elif fp == LOCK_FILE_PATH:
                    os.remove(fp)
        except Exception:
            pass
    print(f"[{pid}] Temizleme tamamlandı.")


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motorlu 2D Alan Tarama Betiği (gpiozero)")
    parser.add_argument("--start_angle", type=float, default=DEFAULT_SCAN_START_ANGLE)
    parser.add_argument("--end_angle", type=float, default=DEFAULT_SCAN_END_ANGLE)
    parser.add_argument("--step_angle", type=float, default=DEFAULT_SCAN_STEP_ANGLE)
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE)
    parser.add_argument("--invert_motor_direction", type=lambda x: (str(x).lower() == 'true'),
                        default=DEFAULT_INVERT_MOTOR_DIRECTION, help="Motor dönüş yönünü ters çevir (True/False)")
    parser.add_argument("--steps_per_rev", type=int, default=DEFAULT_STEPS_PER_REVOLUTION,
                        help="Motorun bir tam tur (360 derece) için adım sayısı.")
    
    args = parser.parse_args()

    SCAN_START_ANGLE = float(args.start_angle)
    SCAN_END_ANGLE = float(args.end_angle)
    SCAN_STEP_ANGLE = float(args.step_angle)
    BUZZER_DISTANCE_CM = int(args.buzzer_distance)
    INVERT_MOTOR_DIRECTION = bool(args.invert_motor_direction)
    STEPS_PER_REVOLUTION_OUTPUT_SHAFT = int(args.steps_per_rev)

    pid = os.getpid()

    if STEPS_PER_REVOLUTION_OUTPUT_SHAFT <= 0:
        print(
            f"[{pid}] KRİTİK HATA: STEPS_PER_REVOLUTION_OUTPUT_SHAFT ({STEPS_PER_REVOLUTION_OUTPUT_SHAFT}) sıfır veya negatif olamaz! Betik sonlandırılıyor.")
        sys.exit(1)

    DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT

    if SCAN_STEP_ANGLE < DEG_PER_STEP:
        print(
            f"[{pid}] UYARI: İstenen adım açısı ({SCAN_STEP_ANGLE:.3f}°) motorun minimum adım açısından ({DEG_PER_STEP:.3f}°) küçük. Minimuma ({DEG_PER_STEP:.3f}°) ayarlanıyor.")
        SCAN_STEP_ANGLE = DEG_PER_STEP
    if SCAN_STEP_ANGLE == 0:
        print(f"[{pid}] UYARI: Adım açısı sıfır olamaz. Minimuma ({DEG_PER_STEP:.3f}°) ayarlanıyor.")
        SCAN_STEP_ANGLE = DEG_PER_STEP

    atexit.register(release_resources_on_exit)

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)
    init_db_for_scan()
    if not current_scan_id_global: sys.exit(1)

    collected_cartesian_points_for_area = []

    print(f"[{pid}] Step Motor (gpiozero) ile 2D Tarama Başlıyor (ID: {current_scan_id_global})...")
    print(
        f"[{pid}] Ayarlar: Başlangıç={SCAN_START_ANGLE:.1f}°, Bitiş={SCAN_END_ANGLE:.1f}°, Adım={SCAN_STEP_ANGLE:.1f}°")
    print(f"[{pid}] Motor Yönü Ters Çevirme: {'Aktif' if INVERT_MOTOR_DIRECTION else 'Pasif'}")
    print(f"[{pid}] Motor Kalibrasyon Bilgisi: {DEG_PER_STEP:.4f}° / adım ({STEPS_PER_REVOLUTION_OUTPUT_SHAFT} adım/tur)")

    if lcd:
        lcd.clear();
        lcd.cursor_pos = (0, 0);
        lcd.write_string(f"ScanID:{current_scan_id_global} Step".ljust(LCD_COLS)[:LCD_COLS])
        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
            f"A:{SCAN_START_ANGLE:.0f}-{SCAN_END_ANGLE:.0f} S:{SCAN_STEP_ANGLE:.1f}".ljust(LCD_COLS)[:LCD_COLS])

    scan_completed_successfully = False
    lcd_warning_mode = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        target_loop_angle = float(SCAN_START_ANGLE)
        scan_direction = 1 if SCAN_END_ANGLE >= SCAN_START_ANGLE else -1

        print(
            f"[{pid}] Tarama Başlıyor: {target_loop_angle:.1f}° -> {SCAN_END_ANGLE:.1f}°, Adım İlerlemesi: {SCAN_STEP_ANGLE * scan_direction:.1f}°")

        loop_count = 0
        max_loops = 0
        if SCAN_STEP_ANGLE > (DEG_PER_STEP / 100):
            max_loops = math.ceil(abs(SCAN_END_ANGLE - SCAN_START_ANGLE) / SCAN_STEP_ANGLE) + 5
        else:
            max_loops = STEPS_PER_REVOLUTION_OUTPUT_SHAFT * 2
            print(f"[{pid}] UYARI: Adım açısı çok küçük, maksimum döngü sayısı {max_loops} ile sınırlandırıldı.")

        while loop_count < max_loops:
            loop_count += 1
            loop_iteration_start_time = time.time()

            move_motor_to_angle(target_loop_angle)
            current_effective_degree_for_scan = current_motor_angle_global

            if yellow_led: yellow_led.toggle()

            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            angle_rad = math.radians(current_effective_degree_for_scan)
            x_cm = distance_cm * math.cos(angle_rad)
            y_cm = distance_cm * math.sin(angle_rad)

            if 0 < distance_cm < (sensor.max_distance * 100 - 1):
                collected_cartesian_points_for_area.append((x_cm, y_cm))

            hiz_cm_s = 0.0

            is_object_close = distance_cm <= BUZZER_DISTANCE_CM
            if buzzer:
                if is_object_close and not buzzer.is_active:
                    buzzer.on()
                elif not is_object_close and buzzer.is_active:
                    buzzer.off()

            if lcd:
                try:
                    if is_object_close and not lcd_warning_mode:
                        lcd.clear();
                        lcd.cursor_pos = (0, 0);
                        lcd.write_string("!!! UYARI !!!".center(LCD_COLS));
                        lcd_warning_mode = True
                        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("NESNE YAKIN!".center(LCD_COLS))
                    elif not is_object_close and lcd_warning_mode:
                        lcd.clear();
                        lcd_warning_mode = False
                    if not lcd_warning_mode:
                        lcd.cursor_pos = (0, 0);
                        lcd.write_string(
                            f"A:{current_effective_degree_for_scan:<3.1f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[
                            :LCD_COLS])
                        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                            f"X{x_cm:3.0f}Y{y_cm:3.0f} H{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
                except Exception as e_lcd:
                    print(f"[{pid}] LCD yazma hatası: {e_lcd}")

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{pid}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Sonlandırılıyor.")
                if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string(
                    "COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                if yellow_led: yellow_led.on()
                script_exit_status_global = 'terminated_close_object';
                time.sleep(1.0);
                break

            try:
                cursor_main.execute(
                    "INSERT INTO scan_points (scan_id,derece,mesafe_cm,hiz_cm_s,timestamp,x_cm,y_cm) VALUES (?,?,?,?,?,?,?)",
                    (current_scan_id_global, current_effective_degree_for_scan, distance_cm, hiz_cm_s,
                     current_timestamp, x_cm, y_cm))
                db_conn_main_script_global.commit()
            except Exception as e_db:
                print(f"[{pid}] DB Ekleme Hatası: {e_db}")

            if scan_direction == 1:
                if current_effective_degree_for_scan >= SCAN_END_ANGLE - (DEG_PER_STEP / 2.0): break
            else:
                if current_effective_degree_for_scan <= SCAN_END_ANGLE + (DEG_PER_STEP / 2.0): break

            target_loop_angle += (SCAN_STEP_ANGLE * scan_direction)

            if scan_direction == 1:
                target_loop_angle = min(target_loop_angle, float(SCAN_END_ANGLE))
            else:
                target_loop_angle = max(target_loop_angle, float(SCAN_END_ANGLE))

            if loop_count >= max_loops - 1:
                print(f"[{pid}] UYARI: Maksimum döngü sayısına ({max_loops}) ulaşıldı, tarama sonlandırılıyor.")
                break

            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0: time.sleep(sleep_duration)

        if len(collected_cartesian_points_for_area) >= 2:
            polygon_for_area = [(0.0, 0.0)] + collected_cartesian_points_for_area
            hesaplanan_alan_cm2 = shoelace_formula(polygon_for_area)
            perimeter_cm = calculate_perimeter(collected_cartesian_points_for_area)
            x_coords = [p[0] for p in collected_cartesian_points_for_area if isinstance(p[0], (int, float))]
            y_coords = [p[1] for p in collected_cartesian_points_for_area if isinstance(p[1], (int, float))]
            max_derinlik_cm_scan = max(x_coords) if x_coords else 0.0
            max_genislik_cm_scan = (max(y_coords) - min(y_coords)) if y_coords else 0.0
            print(f"\n[{pid}] TARAMA TAMAMLANDI. Alan: {hesaplanan_alan_cm2:.2f} cm², Çevre: {perimeter_cm:.2f} cm")
            if lcd:
                lcd.clear()
                lcd.cursor_pos = (0, 0)
                lcd.write_string("Tarama Tamam!".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1:
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string(f"Alan:{hesaplanan_alan_cm2:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])
            script_exit_status_global = 'completed_analysis'
            try:
                cursor_main.execute(
                    "UPDATE servo_scans SET hesaplanan_alan_cm2=?, cevre_cm=?, max_genislik_cm=?, max_derinlik_cm=?, status=? WHERE id=?",
                    (hesaplanan_alan_cm2, perimeter_cm, max_genislik_cm_scan, max_derinlik_cm_scan,
                     script_exit_status_global, current_scan_id_global)
                )
                db_conn_main_script_global.commit()
            except Exception as e_db_upd:
                print(f"[{pid}] HATA: DB Analiz Güncelleme: {e_db_upd}")
        else:
            script_exit_status_global = 'completed_insufficient_points'
            print(f"[{pid}] Tarama tamamlandı ancak analiz için yeterli nokta toplanamadı.")
            if lcd:
                lcd.clear()
                lcd.cursor_pos = (0, 0)
                lcd.write_string("Tarama Tamam!".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1:
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string("Alan Hesaplanamadi".ljust(LCD_COLS)[:LCD_COLS])
            try:
                cursor_main.execute(
                    "UPDATE servo_scans SET status = ? WHERE id = ?",
                    (script_exit_status_global, current_scan_id_global)
                )
                db_conn_main_script_global.commit()
            except Exception as e_db_s_upd:
                print(f"[{pid}] HATA: DB Durum Güncelleme (yetersiz nokta): {e_db_s_upd}")
        scan_completed_successfully = True

    except KeyboardInterrupt:
        script_exit_status_global = 'interrupted_ctrl_c'
        print(f"\n[{pid}] Ctrl+C ile kesildi.")
        if lcd:
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS])
    except Exception as e:
        script_exit_status_global = 'error_in_loop'
        print(f"[{pid}] KRİTİK HATA: Ana döngüde: {e}")
        import traceback
        traceback.print_exc()
        if lcd:
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("HATA OLUSTU!".ljust(LCD_COLS)[:LCD_COLS])
    finally:
        if not scan_completed_successfully and script_exit_status_global not in ['interrupted_ctrl_c', 'error_in_loop',
                                                                                 'terminated_close_object']:
            script_exit_status_global = 'interrupted_unexpectedly_in_main'
        if buzzer and hasattr(buzzer, 'is_active') and buzzer.is_active:
            buzzer.off()
        if yellow_led and hasattr(yellow_led, 'is_active') and yellow_led.is_active:
            yellow_led.off()
        print(f"[{pid}] Ana betik sonlanıyor. Çıkış: {script_exit_status_global}")