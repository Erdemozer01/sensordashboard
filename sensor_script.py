# sensor_script.py
from gpiozero import AngularServo, DistanceSensor, LED, Buzzer
from RPLCD.i2c import CharLCD
import time
import sqlite3
import os
import sys
import fcntl
import atexit
import math
import argparse

# --- Pin Tanımlamaları ---
TRIG_PIN = 23
ECHO_PIN = 24
SERVO_PIN = 12
YELLOW_LED_PIN = 27
BUZZER_PIN = 17

# --- LCD Ayarları ---
LCD_I2C_ADDRESS = 0x27
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1

# --- Varsayılan Eşik ve Tarama Değerleri ---
DEFAULT_TERMINATION_DISTANCE_CM = 1
DEFAULT_BUZZER_DISTANCE = 10
DEFAULT_SCAN_START_ANGLE = 0
DEFAULT_SCAN_END_ANGLE = 180
DEFAULT_SCAN_STEP_ANGLE = 10
SERVO_SETTLE_TIME = 0.3
LOOP_TARGET_INTERVAL_S = 0.6

PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

# --- Global Değişkenler ---
sensor = None
yellow_led = None
servo = None
lcd = None
buzzer = None
lock_file_handle = None
current_scan_id_global = None
db_conn_main_script_global = None
script_exit_status_global = 'interrupted_unexpectedly'

# --- Çalışma Zamanı Ayarları (Argümanlarla Değişebilir) ---
TERMINATION_DISTANCE_CM = DEFAULT_TERMINATION_DISTANCE_CM
BUZZER_DISTANCE_CM = DEFAULT_BUZZER_DISTANCE
SCAN_START_ANGLE = DEFAULT_SCAN_START_ANGLE
SCAN_END_ANGLE = DEFAULT_SCAN_END_ANGLE
SCAN_STEP_ANGLE = DEFAULT_SCAN_STEP_ANGLE


def init_hardware():
    global sensor, yellow_led, servo, lcd, buzzer
    hardware_ok = True
    try:
        print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN)
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             initial_angle=None,
                             min_pulse_width=0.0005, max_pulse_width=0.0025)
        buzzer = Buzzer(BUZZER_PIN)

        yellow_led.off()
        buzzer.off()
        target_center_angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
        if servo: servo.angle = target_center_angle; time.sleep(0.7)
        print(f"[{os.getpid()}] Temel donanımlar başarıyla başlatıldı.")
    except Exception as e:
        print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}");
        hardware_ok = False
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear()
            lcd.cursor_pos = (0, 0);
            lcd.write_string("Dream Pi".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("Hazirlaniyor...".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1.5)
            print(f"[{os.getpid()}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
        except Exception as e_lcd_init:
            print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}.");
            lcd = None
    else:
        lcd = None
    return hardware_ok


def init_db_for_scan():
    global current_scan_id_global
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # BUZZER_DISTANCE_SETTING SÜTUNUNUN BURADA OLDUĞUNDAN EMİN OLUN:
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS servo_scans
                       (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL UNIQUE, status TEXT,
                        hesaplanan_alan_cm2 REAL DEFAULT NULL, cevre_cm REAL DEFAULT NULL,
                        max_genislik_cm REAL DEFAULT NULL, max_derinlik_cm REAL DEFAULT NULL,
                        start_angle_setting REAL, end_angle_setting REAL, step_angle_setting REAL,
                        buzzer_distance_setting REAL)''') # <--- BU SÜTUN
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS scan_points
                       (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, angle_deg REAL, mesafe_cm REAL,
                        hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm REAL,
                        FOREIGN KEY(scan_id) REFERENCES servo_scans(id))''')
        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")
        scan_start_time = time.time()
        # BUZZER_DISTANCE_CM'NİN BURADA EKLENDİĞİNDEN EMİN OLUN:
        cursor.execute("""
                       INSERT INTO servo_scans
                       (start_time, status, start_angle_setting, end_angle_setting, step_angle_setting, buzzer_distance_setting)
                       VALUES (?, ?, ?, ?, ?, ?)""", # <--- 6. SORU İŞARETİ
                       (scan_start_time, 'running', SCAN_START_ANGLE, SCAN_END_ANGLE, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM)) # <--- 6. DEĞER
        current_scan_id_global = cursor.lastrowid
        conn.commit()
        print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {current_scan_id_global}")
    except sqlite3.Error as e_db_init:
        print(f"[{os.getpid()}] DB başlatma/tarama kaydı hatası: {e_db_init}");
        current_scan_id_global = None
    finally:
        if conn: conn.close()


def acquire_lock_and_pid():
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Betik kilidi ve PID başarıyla oluşturuldu.")
        return True
    except BlockingIOError:
        pid = 'Bilinmiyor'
        try:
            with open(PID_FILE_PATH, 'r') as pf: pid = pf.read().strip()
        except (FileNotFoundError, ValueError): pass
        print(f"[{os.getpid()}] Kilit dosyası mevcut. Betik zaten çalışıyor olabilir (PID: {pid}). Çıkılıyor.")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False
    except PermissionError as e:
        print(f"[{os.getpid()}] KRİTİK İZİN HATASI: '{e.filename}' dosyası oluşturulamıyor/yazılamıyor.")
        print(f"[{os.getpid()}] Çözüm: Betiği 'sudo' ile çalıştırmayı veya 'sudo rm {LOCK_FILE_PATH}' ile eski dosyayı silmeyi deneyin.")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False
    except Exception as e:
        print(f"[{os.getpid()}] Kilit/PID alınırken beklenmedik bir hata oluştu: {type(e).__name__}: {e}")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False


def shoelace_formula(noktalar):
    n = len(noktalar)
    if n < 3: return 0.0
    area = 0.0
    for i in range(n):
        x1, y1 = noktalar[i]
        x2, y2 = noktalar[(i + 1) % n]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0


def calculate_perimeter(cartesian_points_for_perimeter_calc):
    perimeter = 0.0
    n = len(cartesian_points_for_perimeter_calc)
    if n == 0: return 0.0
    perimeter += math.sqrt(cartesian_points_for_perimeter_calc[0][0] ** 2 + cartesian_points_for_perimeter_calc[0][1] ** 2)
    for i in range(n - 1):
        x1, y1 = cartesian_points_for_perimeter_calc[i]
        x2, y2 = cartesian_points_for_perimeter_calc[i + 1]
        perimeter += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    perimeter += math.sqrt(cartesian_points_for_perimeter_calc[-1][0] ** 2 + cartesian_points_for_perimeter_calc[-1][1] ** 2)
    return perimeter


def release_resources_on_exit():
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, yellow_led, servo, lcd, buzzer
    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Betik çıkış durumu: {script_exit_status_global}")
    if db_conn_main_script_global:
        try: db_conn_main_script_global.close()
        except: pass
    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (current_scan_id_global,))
            current_db_status_row = cursor_exit.fetchone()
            if current_db_status_row and current_db_status_row[0] == 'running':
                if script_exit_status_global not in ['completed_analysis', 'terminated_close_object', 'interrupted_ctrl_c', 'error_in_loop']:
                    script_exit_status_global = 'interrupted_unexpectedly'
                cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?", (script_exit_status_global, current_scan_id_global))
                conn_exit.commit()
        except Exception as e:
            print(f"DB status update error on exit: {e}")
        finally:
            if conn_exit: conn_exit.close()
            
    print(f"[{pid}] Donanım kapatılıyor...")
    if servo and hasattr(servo, 'detach'):
        try:
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2; time.sleep(0.5); servo.detach(); servo.close()
        except: pass
    if yellow_led and hasattr(yellow_led, 'close'):
        if hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        yellow_led.close()
    if buzzer and hasattr(buzzer, 'close'):
        if hasattr(buzzer, 'is_active') and buzzer.is_active: buzzer.off()
        buzzer.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    if lcd:
        try:
            lcd.clear()
            lcd.cursor_pos = (0, 0); lcd.write_string("Dream Pi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(2.0)
            lcd.clear()
            lcd.cursor_pos = (0, 0); lcd.write_string("Mehmet Erdem".ljust(LCD_COLS)[:LCD_COLS])
            lcd.cursor_pos = (1, 0); lcd.write_string("OZER (PhD.) ".ljust(LCD_COLS)[:LCD_COLS])
        except: pass
        
    print(f"[{pid}] Kalan donanımlar ve LCD kapatıldı.")
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
        except: pass
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_delete_pid = False
                    try:
                        with open(f_path, 'r') as pf_check:
                            if int(pf_check.read().strip()) == pid: can_delete_pid = True
                    except: pass
                    if can_delete_pid: os.remove(f_path)
                elif f_path == LOCK_FILE_PATH:
                    os.remove(f_path)
        except: pass
    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Servo Motorlu 2D Alan Tarama Betiği")
    parser.add_argument("--start_angle", type=int, default=DEFAULT_SCAN_START_ANGLE, help="Tarama başlangıç açısı (derece)")
    parser.add_argument("--end_angle", type=int, default=DEFAULT_SCAN_END_ANGLE, help="Tarama bitiş açısı (derece)")
    parser.add_argument("--step_angle", type=int, default=DEFAULT_SCAN_STEP_ANGLE, help="Tarama adım açısı (derece)")
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE, help="Buzzer uyarı mesafesi (cm)")
    
    args = parser.parse_args()

    SCAN_START_ANGLE = args.start_angle
    SCAN_END_ANGLE = args.end_angle
    SCAN_STEP_ANGLE = args.step_angle
    BUZZER_DISTANCE_CM = args.buzzer_distance
    if SCAN_STEP_ANGLE <= 0: SCAN_STEP_ANGLE = 1

    actual_scan_step = SCAN_STEP_ANGLE
    if SCAN_START_ANGLE > SCAN_END_ANGLE:
        actual_scan_step = -SCAN_STEP_ANGLE

    atexit.register(release_resources_on_exit)

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)
    init_db_for_scan()
    if not current_scan_id_global: sys.exit(1)

    ölçüm_tamponu_hız_için_yerel = []
    collected_cartesian_points_for_area = []

    print(f"[{os.getpid()}] Servo ile 2D Tarama ve Alan Hesabı Başlıyor (Tarama ID: {current_scan_id_global})...")
    if lcd:
        lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string(f"ScanID:{current_scan_id_global} Basladi".ljust(LCD_COLS)[:LCD_COLS])
        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"A:{SCAN_START_ANGLE}-{SCAN_END_ANGLE} S:{abs(SCAN_STEP_ANGLE)}".ljust(LCD_COLS)[:LCD_COLS])

    scan_completed_successfully = False
    lcd_warning_mode = False # LCD'nin uyarı modunda olup olmadığını takip eder
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        print(f"[{os.getpid()}] Servo başlangıç açısına ({SCAN_START_ANGLE}°) ayarlanıyor...")
        if servo: servo.angle = SCAN_START_ANGLE
        time.sleep(1.0)

        effective_end_angle = SCAN_END_ANGLE + (actual_scan_step // abs(actual_scan_step) if actual_scan_step != 0 else 1)

        for angle_deg in range(SCAN_START_ANGLE, effective_end_angle, actual_scan_step):
            loop_iteration_start_time = time.time()

            if yellow_led: yellow_led.toggle()
            if servo: servo.angle = angle_deg
            time.sleep(SERVO_SETTLE_TIME)

            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            angle_rad = math.radians(angle_deg)
            x_cm = distance_cm * math.cos(angle_rad)
            y_cm = distance_cm * math.sin(angle_rad)

            if 0 < distance_cm < (sensor.max_distance * 100):
                collected_cartesian_points_for_area.append((x_cm, y_cm))

            hiz_cm_s = 0.0
            if ölçüm_tamponu_hız_için_yerel:
                son_veri_noktasi = ölçüm_tamponu_hız_için_yerel[-1]
                delta_mesafe = abs(distance_cm - son_veri_noktasi['mesafe_cm'])
                delta_zaman = abs(current_timestamp - son_veri_noktasi['zaman_s'])
                if delta_zaman > 0.001: hiz_cm_s = delta_mesafe / delta_zaman

            # --- LCD & Buzzer Kontrolü ---
            is_object_close = distance_cm <= BUZZER_DISTANCE_CM

            if buzzer:
                if is_object_close and not buzzer.is_active:
                    buzzer.on()
                elif not is_object_close and buzzer.is_active:
                    buzzer.off()

            if lcd:
                try:
                    if is_object_close and not lcd_warning_mode:
                        lcd.clear()
                        lcd.cursor_pos = (0, 0); lcd.write_string("!!! UYARI !!!".center(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("DOKUNMA BANA!".center(LCD_COLS)[:LCD_COLS])
                        lcd_warning_mode = True
                    elif not is_object_close and lcd_warning_mode:
                        lcd.clear()
                        lcd.cursor_pos = (0, 0); lcd.write_string(f"A:{angle_deg:<3} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"X{x_cm:3.0f}Y{y_cm:3.0f} H{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
                        lcd_warning_mode = False
                    elif not is_object_close and not lcd_warning_mode:
                        lcd.cursor_pos = (0, 0); lcd.write_string(f"A:{angle_deg:<3} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"X{x_cm:3.0f}Y{y_cm:3.0f} H{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
                except Exception as e:
                    print(f"LCD yazma hatası: {e}")
                    pass

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)!")
                if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
                if yellow_led: yellow_led.on()
                script_exit_status_global = 'terminated_close_object'
                time.sleep(1.5); break

            try:
                cursor_main.execute('''INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                                    (current_scan_id_global, angle_deg, distance_cm, hiz_cm_s, current_timestamp, x_cm, y_cm))
                db_conn_main_script_global.commit()
            except Exception as e:
                print(f"DB Ekleme Hatası: {e}")

            ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]
            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            is_last_step_in_range = (angle_deg == SCAN_END_ANGLE)
            if sleep_duration > 0 and not is_last_step_in_range: time.sleep(sleep_duration)
        else:
            hesaplanan_alan_cm2 = 0.0; perimeter_cm = 0.0
            max_genislik_cm_scan = 0.0; max_derinlik_cm_scan = 0.0

            if len(collected_cartesian_points_for_area) >= 2:
                polygon_vertices_for_area_calc = [(0.0, 0.0)] + collected_cartesian_points_for_area
                hesaplanan_alan_cm2 = shoelace_formula(polygon_vertices_for_area_calc)
                perimeter_cm = calculate_perimeter(polygon_vertices_for_area_calc)
                x_coords = [p[0] for p in collected_cartesian_points_for_area if p[0] is not None]
                y_coords = [p[1] for p in collected_cartesian_points_for_area if p[1] is not None]
                if x_coords: max_derinlik_cm_scan = max(x_coords) if x_coords else 0.0
                if y_coords: max_genislik_cm_scan = (max(y_coords) - min(y_coords)) if y_coords else 0.0
                print(f"\n[{os.getpid()}] TARANAN SEKTÖR ALANI: {hesaplanan_alan_cm2:.2f} cm^2")

                if lcd:
                    lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("Tarama Tamamlandi".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"Alan:{hesaplanan_alan_cm2:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])
                script_exit_status_global = 'completed_analysis'
                try:
                    cursor_main.execute("""UPDATE servo_scans SET hesaplanan_alan_cm2 = ?, cevre_cm = ?, max_genislik_cm = ?, max_derinlik_cm = ?, status = ? WHERE id = ?""",
                                        (hesaplanan_alan_cm2, perimeter_cm, max_genislik_cm_scan, max_derinlik_cm_scan, script_exit_status_global, current_scan_id_global))
                    db_conn_main_script_global.commit()
                    print(f"[{os.getpid()}] Analiz verileri veritabanına yazıldı. Alan: {hesaplanan_alan_cm2:.2f}")
                except Exception as e_db_update:
                    print(f"[{os.getpid()}] DB Analiz Güncelleme Hatası: {e_db_update}")
            else:
                script_exit_status_global = 'completed_insufficient_points'
                if lcd:
                    lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("Tarama Tamamlandi".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("Alan Hesaplanamadi".ljust(LCD_COLS)[:LCD_COLS])
            scan_completed_successfully = True

    except KeyboardInterrupt:
        script_exit_status_global = 'interrupted_ctrl_c'; print(f"\n[{os.getpid()}] Ctrl+C ile durduruldu.")
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS])
    except Exception as e:
        script_exit_status_global = 'error_in_loop'; print(f"[{os.getpid()}] Ana döngü hatası: {e}")
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("HATA OLUSTU!".ljust(LCD_COLS)[:LCD_COLS])