# sensor_script.py
from gpiozero import AngularServo, DistanceSensor, LED
from RPLCD.i2c import CharLCD
import time
import sqlite3
import os
import sys
import fcntl
import atexit
import math

# --- Pin Tanımlamaları ---
TRIG_PIN = 23
ECHO_PIN = 24
SERVO_PIN = 12  # KENDİNİZE GÖRE DEĞİŞTİRİN!
YELLOW_LED_PIN = 27

# --- LCD Ayarları ---
LCD_I2C_ADDRESS = 0x27
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1

# --- Eşik Değerleri ve Sabitler ---
TERMINATION_DISTANCE_CM = 10.0
SCAN_START_ANGLE = 0
SCAN_END_ANGLE = 180
SCAN_STEP_ANGLE = 10
SERVO_SETTLE_TIME = 0.3
LOOP_TARGET_INTERVAL_S = 0.6

PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

# --- Global Donanım ve Durum Değişkenleri ---
sensor = None
yellow_led = None
servo = None
lcd = None
lock_file_handle = None
current_scan_id_global = None
db_conn_main_script_global = None
script_exit_status_global = 'interrupted_unexpectedly'


def init_hardware():
    global sensor, yellow_led, servo, lcd
    hardware_ok = True
    try:
        print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN)
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             initial_angle=None,
                             min_pulse_width=0.0005, max_pulse_width=0.0025)
        yellow_led.off()
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
            print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}."); lcd = None
    else:
        lcd = None
    return hardware_ok


def init_db_for_scan():
    global current_scan_id_global
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
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
                           TEXT,
                           hesaplanan_alan_cm2
                           REAL
                           DEFAULT
                           NULL
                       )''')  # Alan sütunu eklendi
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
                           hiz_cm_s
                           REAL,
                           timestamp
                           REAL,
                           x_cm
                           REAL,
                           y_cm
                           REAL,
                           FOREIGN
                           KEY
                       (
                           scan_id
                       ) REFERENCES servo_scans
                       (
                           id
                       ))''')  # x,y sütunları eklendi
        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")
        scan_start_time = time.time()
        cursor.execute("INSERT INTO servo_scans (start_time, status) VALUES (?, ?)", (scan_start_time, 'running'))
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
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError:
        pass
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Betik kilidi ve PID başarıyla oluşturuldu.")
        return True
    except BlockingIOError:
        print(f"[{os.getpid()}] Kilit dosyası mevcut. Betik zaten çalışıyor olabilir.")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None;
        return False
    except Exception as e:
        print(f"[{os.getpid()}] Kilit/PID alınırken hata: {e}")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None;
        return False


def calculate_polygon_area_shoelace(cartesian_points_with_origin):
    n = len(cartesian_points_with_origin)
    if n < 3: return 0.0
    area = 0.0
    for i in range(n):
        x1, y1 = cartesian_points_with_origin[i]
        x2, y2 = cartesian_points_with_origin[(i + 1) % n]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0


def release_resources_on_exit():
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, yellow_led, servo, lcd
    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Çıkış durumu: {script_exit_status_global}")
    if db_conn_main_script_global:
        try:
            db_conn_main_script_global.close()
        except:
            pass
    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (current_scan_id_global,))
            current_db_status_row = cursor_exit.fetchone()
            if current_db_status_row and current_db_status_row[0] == 'running':
                cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?",
                                    (script_exit_status_global, current_scan_id_global))
                conn_exit.commit()
        except Exception as e:
            print(f"DB status update error on exit: {e}")
        finally:
            if conn_exit: conn_exit.close()
    print(f"[{pid}] Donanım kapatılıyor...")
    if servo and hasattr(servo, 'detach'):
        try:
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2; time.sleep(0.5); servo.detach(); servo.close()
        except:
            pass
    if yellow_led and hasattr(yellow_led, 'close'):
        if hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        yellow_led.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    if lcd:
        try:
            lcd.clear();
            lcd.cursor_pos = (0, 0);
            lcd.write_string("Dream Pi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
        except:
            pass
    print(f"[{pid}] Kalan donanımlar ve LCD kapatıldı.")
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
        except:
            pass
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_delete_pid = False
                    try:
                        with open(f_path, 'r') as pf_check:
                            if int(pf_check.read().strip()) == pid: can_delete_pid = True
                    except:
                        pass
                    if can_delete_pid: os.remove(f_path)
                elif f_path == LOCK_FILE_PATH:
                    os.remove(f_path)
        except:
            pass
    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")


if __name__ == "__main__":
    atexit.register(lambda: release_resources_on_exit())

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)
    init_db_for_scan()
    if not current_scan_id_global: sys.exit(1)

    ölçüm_tamponu_hız_için_yerel = []
    collected_cartesian_points_for_area = []  # Alan hesabı için (x,y) noktalarını tut

    print(f"[{os.getpid()}] Servo ile 2D Tarama ve Alan Hesabı Başlıyor (Tarama ID: {current_scan_id_global})...")
    if lcd:
        lcd.clear();
        lcd.cursor_pos = (0, 0);
        lcd.write_string(f"ScanID:{current_scan_id_global} Basladi".ljust(LCD_COLS)[:LCD_COLS])
        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("Aci: -- Mes: --".ljust(LCD_COLS)[:LCD_COLS])

    scan_completed_successfully = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        print(f"[{os.getpid()}] Servo başlangıç açısına ({SCAN_START_ANGLE}°) ayarlanıyor...")
        servo.angle = SCAN_START_ANGLE
        time.sleep(1.0)

        for angle_deg in range(SCAN_START_ANGLE, SCAN_END_ANGLE + SCAN_STEP_ANGLE, SCAN_STEP_ANGLE):
            loop_iteration_start_time = time.time()

            if yellow_led: yellow_led.toggle()

            servo.angle = angle_deg
            time.sleep(SERVO_SETTLE_TIME)

            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            angle_rad = math.radians(angle_deg)
            x_cm = distance_cm * math.cos(angle_rad)
            y_cm = distance_cm * math.sin(angle_rad)
            # Sadece geçerli mesafeleri alan hesabına dahil et (isteğe bağlı filtreleme)
            if 0 < distance_cm < (sensor.max_distance * 100):
                collected_cartesian_points_for_area.append((x_cm, y_cm))

            hiz_cm_s = 0.0
            if ölçüm_tamponu_hız_için_yerel:
                son_veri_noktasi = ölçüm_tamponu_hız_için_yerel[-1]
                delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
                delta_zaman = current_timestamp - son_veri_noktasi['zaman_s']
                if delta_zaman > 0.001: hiz_cm_s = delta_mesafe / delta_zaman

            if lcd:
                try:
                    lcd.cursor_pos = (0, 0)
                    lcd.write_string(f"A:{angle_deg:<3} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1:
                        lcd.cursor_pos = (1, 0)
                        lcd.write_string(f"X:{x_cm:4.0f} Y:{y_cm:4.0f} H:{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
                except Exception as e_lcd_write:
                    print(f"LCD Yazma Hatası: {e_lcd_write}")

            # Kırmızı/Yeşil LED'ler çıkarıldı. Sarı LED zaten yukarıda toggle ediliyor.
            # Çok Yakın Engel Durumu
            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{os.getpid()}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)!")
                if lcd:
                    lcd.clear();
                    lcd.cursor_pos = (0, 0);
                    lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                        f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
                if yellow_led: yellow_led.on()  # Tehlike anında sabit yan
                script_exit_status_global = 'terminated_close_object'
                time.sleep(1.5);
                break

            try:
                cursor_main.execute('''
                                    INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                    ''',
                                    (current_scan_id_global, angle_deg, distance_cm, hiz_cm_s, current_timestamp, x_cm,
                                     y_cm))
                db_conn_main_script_global.commit()
            except Exception as e_db_insert:
                print(f"[{os.getpid()}] DB Ekleme Hatası: {e_db_insert}")

            ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]

            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0 and (angle_deg < SCAN_END_ANGLE): time.sleep(sleep_duration)

        else:  # for döngüsü normal biterse
            if len(collected_cartesian_points_for_area) >= 2:  # Orijin ile birlikte en az 3 nokta eder
                polygon_vertices = [(0.0, 0.0)] + collected_cartesian_points_for_area
                # Eğer tarama tam daire değilse ve son nokta ile orijin arasında bir çizgi olmasını istiyorsak,
                # Shoelace bunu (0,0) başa eklendiği için zaten bir nevi yapar.
                # Alternatif olarak, son noktadan orijine bir çizgi ekleyip (eğer açı 180 ise x_son,y_son -> 0,0)
                # ve ilk noktadan orijine (0,0 -> x_ilk,y_ilk) olan çizgileri de dahil edebiliriz.
                # Mevcut haliyle, (0,0) ve taranan noktaların oluşturduğu fan şeklindeki alan hesaplanır.

                hesaplanan_alan_cm2 = calculate_polygon_area_shoelace(polygon_vertices)
                print(f"\n[{os.getpid()}] TARANAN SEKTÖR ALANI: {hesaplanan_alan_cm2:.2f} cm^2")
                if lcd:
                    lcd.clear();
                    lcd.cursor_pos = (0, 0);
                    lcd.write_string("Tarama Tamamlandi".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                        f"Alan:{hesaplanan_alan_cm2:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])

                cursor_main.execute(
                    "UPDATE servo_scans SET hesaplanan_alan_cm2 = ?, status = 'completed_area_calc' WHERE id = ?",
                    (hesaplanan_alan_cm2, current_scan_id_global))
                db_conn_main_script_global.commit()
                script_exit_status_global = 'completed_area_calc'
            else:
                print(f"[{os.getpid()}] Alan hesaplamak için yeterli nokta toplanamadı.")
                script_exit_status_global = 'completed_insufficient_points'
                if lcd:
                    lcd.clear();
                    lcd.cursor_pos = (0, 0);
                    lcd.write_string("Tarama Tamamlandi".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                        "Alan Hesaplanamadi".ljust(LCD_COLS)[:LCD_COLS])
            scan_completed_successfully = True

    except KeyboardInterrupt:
        print(f"\n[{os.getpid()}] Tarama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
        script_exit_status_global = 'interrupted_ctrl_c'
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS])
    except Exception as e_main_loop:
        print(f"[{os.getpid()}] Tarama sırasında ana döngüde beklenmedik bir hata: {e_main_loop}")
        script_exit_status_global = 'error_in_loop'
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("HATA OLUSTU!".ljust(LCD_COLS)[:LCD_COLS])