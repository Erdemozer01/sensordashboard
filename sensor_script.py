# sensor_script.py (Simetrik Tarama)
from gpiozero import DistanceSensor, LED, OutputDevice
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
YELLOW_LED_PIN = 27
MOTOR_PIN_IN1 = 5; MOTOR_PIN_IN2 = 6; MOTOR_PIN_IN3 = 13; MOTOR_PIN_IN4 = 19

# --- LCD Ayarları ---
LCD_I2C_ADDRESS = 0x27
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1

# --- Eşik ve Tarama Değerleri ---
TERMINATION_DISTANCE_CM = 10.0
DEFAULT_SCAN_START_ANGLE = -135 # <<< Simetrik tarama için başlangıç
DEFAULT_SCAN_END_ANGLE = 135   # <<< Simetrik tarama için bitiş
DEFAULT_SCAN_STEP_ANGLE = 10
SERVO_SETTLE_TIME = 0.05
LOOP_TARGET_INTERVAL_S = 0.15

STEPS_PER_REVOLUTION = 4096
STEP_DELAY = 0.0012
STEP_SEQUENCE = [[1,0,0,0], [1,1,0,0], [0,1,0,0], [0,1,1,0], [0,0,1,0], [0,0,1,1], [0,0,0,1], [1,0,0,1]]

PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'; PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

sensor = None; yellow_led = None; lcd = None
motor_pins_global = []
current_motor_step_index_global = 0
current_motor_angle_global = 0.0  # Motorun başlangıçta 0 (merkez) olduğunu varsayalım
lock_file_handle = None; current_scan_id_global = None
db_conn_main_script_global = None; script_exit_status_global = 'interrupted_unexpectedly'
STEP_MOTOR_SETTLE_TIME = 10.0
SCAN_START_ANGLE = DEFAULT_SCAN_START_ANGLE
SCAN_END_ANGLE = DEFAULT_SCAN_END_ANGLE
SCAN_STEP_ANGLE = DEFAULT_SCAN_STEP_ANGLE

def init_hardware():
    global sensor, yellow_led, motor_pins_global, lcd, current_motor_angle_global
    hardware_ok = True
    try:
        print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN)
        motor_pins_global = [OutputDevice(p) for p in [MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4]]
        for pin in motor_pins_global: pin.off()
        yellow_led.off()

        # Motoru 0 derece (merkez) pozisyonuna al (eğer SCAN_START_ANGLE farklıysa oraya gidecek)
        # Bu, current_motor_angle_global'in doğru ayarlanması için önemli.
        # Eğer motorun gerçek bir "home" pozisyonu varsa, o daha iyi olur.
        # Şimdilik, betik başladığında motorun 0 derecede olduğunu varsayıyoruz.
        current_motor_angle_global = 0.0 # Fiziksel olarak 0'a getirmek için bir homing rutini daha iyi olur.
        print(f"[{os.getpid()}] Temel donanımlar başarıyla başlatıldı. Motor başlangıçta {current_motor_angle_global}° kabul ediliyor.")
    except Exception as e:
        print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}"); hardware_ok = False
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear()
            lcd.cursor_pos = (0,0); lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1,0); lcd.write_string("Hazirlaniyor...".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1.0)
        except Exception as e_lcd_init: print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}."); lcd = None
    else: lcd = None
    return hardware_ok

def _apply_step_to_motor(sequence_index): # Aynı
    global motor_pins_global
    if not motor_pins_global: return
    step_pattern = STEP_SEQUENCE[sequence_index % len(STEP_SEQUENCE)]
    for i in range(4):
        if step_pattern[i] == 1: motor_pins_global[i].on()
        else: motor_pins_global[i].off()

def move_motor_to_target_angle_incremental(target_angle_deg, step_delay=STEP_DELAY):
    global current_motor_angle_global, current_motor_step_index_global

    degrees_per_half_step = 360.0 / STEPS_PER_REVOLUTION
    # Hedef açıya ulaşmak için gereken açı farkı
    # Örneğin, -135'ten -125'e gitmek için angle_difference = -125 - (-135) = +10
    # Örneğin, 0'dan -10'a gitmek için angle_difference = -10 - 0 = -10
    angle_difference = target_angle_deg - current_motor_angle_global

    # Eğer açı farkı çok küçükse (bir adımdan az), hareket etme
    if abs(angle_difference) < degrees_per_half_step / 2: # Yarım adımdan küçükse
        # print(f"  Motor zaten hedef açıya yakın: {current_motor_angle_global:.1f}° -> {target_angle_deg:.1f}°")
        current_motor_angle_global = target_angle_deg # Yine de tam hedefi ata
        return

    steps_to_move = round(abs(angle_difference) / degrees_per_half_step)
    if steps_to_move == 0: return

    direction_is_cw = angle_difference > 0 # Pozitif fark saat yönü (açı artıyor)

    # print(f"  Motor {current_motor_angle_global:.1f}°'den {target_angle_deg:.1f}°'ye hareket ({steps_to_move} half-step, {'CW' if direction_is_cw else 'CCW'})...")

    for _ in range(steps_to_move):
        if direction_is_cw:
            current_motor_step_index_global = (current_motor_step_index_global + 1) % len(STEP_SEQUENCE)
        else:
            current_motor_step_index_global = (current_motor_step_index_global - 1 + len(STEP_SEQUENCE)) % len(STEP_SEQUENCE)
        _apply_step_to_motor(current_motor_step_index_global)
        time.sleep(step_delay)

    current_motor_angle_global = target_angle_deg
    # print(f"  Motor şimdi {current_motor_angle_global:.1f}° pozisyonunda.")


# init_db_for_scan, acquire_lock_and_pid, calculate_... fonksiyonları aynı (Yanıt #50'deki gibi)
# release_resources_on_exit (Yanıt #50'deki gibi, servo yerine motor_pins temizliği)
# ... (Bu fonksiyonların tam içeriğini bir önceki cevaptan (#50) kopyalayın) ...
def init_db_for_scan(): # Aynı
    global current_scan_id_global; conn = None
    try:
        conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS servo_scans(id INTEGER PRIMARY KEY AUTOINCREMENT,start_time REAL UNIQUE,status TEXT,hesaplanan_alan_cm2 REAL DEFAULT NULL,cevre_cm REAL DEFAULT NULL,max_genislik_cm REAL DEFAULT NULL,max_derinlik_cm REAL DEFAULT NULL,start_angle_setting REAL,end_angle_setting REAL,step_angle_setting REAL)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS scan_points (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, angle_deg REAL, mesafe_cm REAL, hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm REAL, FOREIGN KEY(scan_id) REFERENCES servo_scans(id))''')
        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")
        scan_start_time = time.time(); cursor.execute("INSERT INTO servo_scans (start_time, status, start_angle_setting, end_angle_setting, step_angle_setting) VALUES (?, ?, ?, ?, ?)", (scan_start_time, 'running', SCAN_START_ANGLE, SCAN_END_ANGLE, SCAN_STEP_ANGLE)); current_scan_id_global = cursor.lastrowid; conn.commit(); print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {current_scan_id_global}")
    except sqlite3.Error as e: print(f"DB başlatma hatası: {e}"); current_scan_id_global = None
    finally:
        if conn: conn.close()

def acquire_lock_and_pid(): # Aynı
    global lock_file_handle; try:
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError: pass
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w'); fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB);
        with open(PID_FILE_PATH, 'w') as pf: pf.write(str(os.getpid())); return True
    except BlockingIOError:
        print(f"Kilit dosyası mevcut.")
        if lock_file_handle: lock_file_handle.close(); lock_file_handle = None; return False
    except Exception as e:
        print(f"Kilit/PID hatası: {e}"); if lock_file_handle: lock_file_handle.close(); lock_file_handle = None; return False

def calculate_polygon_area_shoelace(points): # Aynı
    n = len(points); area = 0.0; if n < 3: return 0.0
    for i in range(n): x1, y1 = points[i]; x2, y2 = points[(i + 1) % n]; area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0

def calculate_perimeter(points_with_origin): # Aynı
    perimeter = 0.0; n = len(points_with_origin);
    if n > 1: perimeter += math.sqrt(points_with_origin[1][0]**2 + points_with_origin[1][1]**2)
    for i in range(1, n - 1): x1, y1 = points_with_origin[i]; x2, y2 = points_with_origin[i+1]; perimeter += math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
    if n > 1: perimeter += math.sqrt(points_with_origin[-1][0]**2 + points_with_origin[-1][1]**2)
    return perimeter

def release_resources_on_exit():
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, yellow_led, motor_pins_global, lcd
    pid = os.getpid(); print(f"[{pid}] `release_resources_on_exit` çağrıldı. Durum: {script_exit_status_global}")
    if db_conn_main_script_global:
        try: db_conn_main_script_global.close()
        except: pass
    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor(); cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (current_scan_id_global,))
            row = cursor_exit.fetchone()
            if row and row[0] == 'running':
                cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?", (script_exit_status_global, current_scan_id_global))
                conn_exit.commit()
        except Exception as e: print(f"DB status update error on exit: {e}")
        finally:
            if conn_exit: conn_exit.close()
    print(f"[{pid}] Donanım kapatılıyor...")
    if motor_pins_global: # Step motoru ortaya (0 dereceye) alıp pinleri kapat
        try:
            print(f"[{pid}] Motor ortaya (0°) alınıyor...")
            move_motor_to_target_angle_incremental(0) # Hedef 0 derece
            time.sleep(0.5) # Hareketin tamamlanması için
            for pin_obj in motor_pins_global:
                if hasattr(pin_obj, 'close') and not pin_obj.closed: pin_obj.off(); pin_obj.close()
            print(f"[{pid}] Step motor pinleri kapatıldı.")
        except Exception as e_motor_release:
             print(f"[{pid}] Motor pinleri kapatılırken hata: {e_motor_release}")
    if yellow_led and hasattr(yellow_led, 'close'):
        if hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        yellow_led.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    if lcd:
        try: lcd.clear(); lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
        except: pass
    print(f"[{pid}] Kalan donanımlar kapatıldı.")
    if lock_file_handle:
        try: fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
        except: pass
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_delete = False
                    try:
                        with open(f_path, 'r') as pf:
                            if int(pf.read().strip()) == pid: can_delete = True
                    except: pass
                    if can_delete: os.remove(f_path)
                else: os.remove(f_path)
        except: pass
    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motor ile 2D Alan Tarama Betiği")
    parser.add_argument("--start_angle", type=int, default=DEFAULT_SCAN_START_ANGLE, help=f"Tarama başlangıç açısı (derece, varsayılan: {DEFAULT_SCAN_START_ANGLE})")
    parser.add_argument("--end_angle", type=int, default=DEFAULT_SCAN_END_ANGLE, help=f"Tarama bitiş açısı (derece, varsayılan: {DEFAULT_SCAN_END_ANGLE})")
    parser.add_argument("--step_angle", type=int, default=DEFAULT_SCAN_STEP_ANGLE, help=f"Tarama adım açısı (derece, varsayılan: {DEFAULT_SCAN_STEP_ANGLE})")
    args = parser.parse_args()

    SCAN_START_ANGLE = args.start_angle
    SCAN_END_ANGLE = args.end_angle
    SCAN_STEP_ANGLE = args.step_angle
    if SCAN_STEP_ANGLE == 0: SCAN_STEP_ANGLE = 10 # Sıfır olamaz, varsayılana dön

    # Tarama yönünü belirle (adım pozitif veya negatif olabilir)
    actual_scan_step = SCAN_STEP_ANGLE
    if SCAN_START_ANGLE > SCAN_END_ANGLE: # Eğer başlangıç bitişten büyükse, adım negatif olmalı
        if actual_scan_step > 0: actual_scan_step *= -1
    else: # Başlangıç bitişten küçük veya eşitse, adım pozitif olmalı
        if actual_scan_step < 0: actual_scan_step *= -1

    atexit.register(lambda: release_resources_on_exit())

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)
    init_db_for_scan() # Bu, servo_scans tablosuna global SCAN_... ayarlarını yazar
    if not current_scan_id_global: sys.exit(1)

    ölçüm_tamponu_hız_için_yerel = []
    collected_cartesian_points_for_area = []

    # Motorun mevcut açısal pozisyonunu `init_hardware` sonrası (0.0) veya
    # bir önceki taramadan kalan bir değer olarak ayarlamak yerine,
    # her tarama başında `SCAN_START_ANGLE`'a gitmesini sağlayalım.
    current_motor_angle_global = 0.0 # Varsayılan başlangıç (fiziksel olarak da 0'a yakın olmalı)

    print(f"[{os.getpid()}] Motor başlangıç pozisyonuna ({SCAN_START_ANGLE}°) getiriliyor...")
    move_motor_to_target_angle_incremental(SCAN_START_ANGLE) # Hedef açıya git
    print(f"[{os.getpid()}] Motor {current_motor_angle_global:.1f}° pozisyonunda. Tarama başlıyor.")

    if lcd:
        lcd.clear(); lcd.cursor_pos = (0,0); lcd.write_string(f"ID:{current_scan_id_global} Basladi".ljust(LCD_COLS)[:LCD_COLS])
        if LCD_ROWS > 1: lcd.cursor_pos = (1,0); lcd.write_string(f"A:{SCAN_START_ANGLE}-{SCAN_END_ANGLE}".ljust(LCD_COLS)[:LCD_COLS])

    scan_completed_successfully = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        # range'in son değeri dahil olması için doğru bitiş noktasını hesapla
        # Eğer step pozitifse, end_angle'ı dahil etmek için end_angle + 1 (veya step)
        # Eğer step negatifse, end_angle'ı dahil etmek için end_angle - 1 (veya step)
        effective_end_for_range = SCAN_END_ANGLE + (actual_scan_step // abs(actual_scan_step) if actual_scan_step != 0 else 1)

        for target_angle_deg in range(SCAN_START_ANGLE, effective_end_for_range, actual_scan_step):
            loop_iteration_start_time = time.time()
            if yellow_led: yellow_led.toggle()

            # Motoru bir sonraki hedef açıya götür (zaten oradaysa hareket etmez)
            move_motor_to_target_angle_incremental(target_angle_deg)
            time.sleep(STEP_MOTOR_SETTLE_TIME) # Sensör okuması için bekle

            current_timestamp = time.time()
            distance_m = sensor.distance; distance_cm = distance_m * 100

            # Kullanılan gerçek açı motorun o anki pozisyonu olmalı
            actual_current_angle_for_calc = current_motor_angle_global
            angle_rad = math.radians(actual_current_angle_for_calc)
            x_cm = distance_cm * math.cos(angle_rad); y_cm = distance_cm * math.sin(angle_rad)
            if 0 < distance_cm < (sensor.max_distance * 100): collected_cartesian_points_for_area.append((x_cm, y_cm))

            hiz_cm_s = 0.0
            if ölçüm_tamponu_hız_için_yerel:
                son_veri = ölçüm_tamponu_hız_için_yerel[-1]; delta_d = distance_cm - son_veri['mesafe_cm']; delta_t = current_timestamp - son_veri['zaman_s']
                if delta_t > 0.001: hiz_cm_s = delta_d / delta_t

            if lcd:
                try:
                    lcd.cursor_pos = (0,0); lcd.write_string(f"A:{actual_current_angle_for_calc:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: lcd.cursor_pos = (1,0); lcd.write_string(f"Hiz:{hiz_cm_s:5.1f}cm/s".ljust(LCD_COLS)[:LCD_COLS])
                except: pass

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)!")
                if lcd: lcd.clear(); lcd.cursor_pos = (0,0); lcd.write_string("COK YAKIN! DUR!");
                if LCD_ROWS > 1: lcd.cursor_pos = (1,0); lcd.write_string(f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
                if yellow_led: yellow_led.on();
                script_exit_status_global = 'terminated_close_object'; time.sleep(1.5); break

            try:
                cursor_main.execute('INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)',
                                   (current_scan_id_global, actual_current_angle_for_calc, distance_cm, hiz_cm_s, current_timestamp, x_cm, y_cm))
                db_conn_main_script_global.commit()
            except Exception as e: print(f"DB Ekleme Hatası: {e}")
            ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]

            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            is_last_step = (target_angle_deg == SCAN_END_ANGLE) # Hedef açıya ulaşıldı mı
            if sleep_duration > 0 and not is_last_step: time.sleep(sleep_duration)
        else:
            script_exit_status_global = 'completed'
            if len(collected_cartesian_points_for_area) >= 2:
                polygon_vertices = [(0.0, 0.0)] + collected_cartesian_points_for_area
                # Simetrik taramada (-135 to +135), son nokta ile ilk nokta arasında bir açıklık olur.
                # Alan hesabının doğru olması için bu poligonun nasıl yorumlandığı önemlidir.
                # Mevcut calculate_polygon_area_shoelace, (0,0) ve taranan noktalardan oluşan
                # birleşik sektörlerin alanını verir.
                alan = calculate_polygon_area_shoelace(polygon_vertices)
                cevre = calculate_perimeter(polygon_vertices)
                x_coords = [p[0] for p in collected_cartesian_points_for_area if p[0] is not None]
                y_coords = [p[1] for p in collected_cartesian_points_for_area if p[1] is not None]
                max_d = max(x_coords) if x_coords else 0.0; max_g = (max(y_coords) - min(y_coords)) if y_coords else 0.0
                print(f"TARANAN ALAN: {alan:.2f} cm²")
                if lcd: lcd.clear(); lcd.cursor_pos = (0,0); lcd.write_string(f"Alan:{alan:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])
                try:
                    cursor_main.execute("UPDATE servo_scans SET hesaplanan_alan_cm2=?,cevre_cm=?,max_genislik_cm=?,max_derinlik_cm=?,status=? WHERE id=?",
                                       (alan, cevre, max_g, max_d, 'completed_analysis', current_scan_id_global)); db_conn_main_script_global.commit()
                    script_exit_status_global = 'completed_analysis'
                except Exception as e: print(f"DB Alan Güncelleme Hatası: {e}")
            else: script_exit_status_global = 'completed_insufficient_points'
            if lcd and script_exit_status_global != 'completed_analysis': lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("Tarama Tamam".ljust(LCD_COLS)[:LCD_COLS]);
            if LCD_ROWS > 1 and script_exit_status_global == 'completed_insufficient_points': lcd.cursor_pos = (1,0); lcd.write_string("Alan Hesaplanamadi".ljust(LCD_COLS)[:LCD_COLS])
            scan_completed_successfully = True

    except KeyboardInterrupt: script_exit_status_global = 'interrupted_ctrl_c'; print(f"\nCtrl+C")
    except Exception as e: script_exit_status_global = 'error_in_loop'; print(f"Ana döngü hatası: {e}")