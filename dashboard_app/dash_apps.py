from gpiozero import DistanceSensor, LED, Buzzer, OutputDevice # OutputDevice eklendi
from RPLCD.i2c import CharLCD

import time
import sqlite3
import os
import sys
import fcntl # Dosya kilitleme için (Linux/macOS)
import atexit # Çıkışta fonksiyon çalıştırmak için
import math
import argparse

# ==============================================================================
# --- Pin Tanımlamaları ve Donanım Ayarları ---
# ==============================================================================
# --- Ultrasonik Sensör Pinleri ---
TRIG_PIN = 23
ECHO_PIN = 24

# --- Step Motor Pin Tanımlamaları (ULN2003 Sürücü Kartı için Örnek) ---
# Lütfen bu pinleri kendi Raspberry Pi bağlantınıza göre güncelleyin!
IN1_GPIO_PIN = 6   # Sürücü kartındaki IN1'e bağlı GPIO pini
IN2_GPIO_PIN = 13  # Sürücü kartındaki IN2'ye bağlı GPIO pini
IN3_GPIO_PIN = 19  # Sürücü kartındaki IN3'ye bağlı GPIO pini
IN4_GPIO_PIN = 26  # Sürücü kartındaki IN4'e bağlı GPIO pini

# --- Diğer Donanım Pinleri ---
YELLOW_LED_PIN = 27 # Durum/Uyarı LED'i
BUZZER_PIN = 17     # Buzzer

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
DEFAULT_SCAN_START_ANGLE = 0
DEFAULT_SCAN_END_ANGLE = 180
DEFAULT_SCAN_STEP_ANGLE = 10

# --- Step Motor Zamanlama Ayarları ---
STEP_MOTOR_INTER_STEP_DELAY = 0.0015 # Adım fazları arasındaki gecikme (saniye)
STEP_MOTOR_SETTLE_TIME = 0.05      # Adım grubundan sonra motorun durması için bekleme (saniye)
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

# --- Global Değişkenler ---
sensor, yellow_led, lcd, buzzer = None, None, None, None
# Step motor pinleri için gpiozero OutputDevice nesneleri
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None

lock_file_handle, current_scan_id_global, db_conn_main_script_global = None, None, None
script_exit_status_global = 'interrupted_unexpectedly'

# --- Step Motor Özellikleri ---
STEPS_PER_REVOLUTION_OUTPUT_SHAFT = 4096
DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT
current_motor_angle_global = 0.0
current_step_sequence_index = 0

step_sequence = [
    [1,0,0,0], [1,1,0,0], [0,1,0,0], [0,1,1,0],
    [0,0,1,0], [0,0,1,1], [0,0,0,1], [1,0,0,1]
]

# --- Çalışma Zamanı Ayarları ---
TERMINATION_DISTANCE_CM = DEFAULT_TERMINATION_DISTANCE_CM
BUZZER_DISTANCE_CM = DEFAULT_BUZZER_DISTANCE
SCAN_START_ANGLE = DEFAULT_SCAN_START_ANGLE
SCAN_END_ANGLE = DEFAULT_SCAN_END_ANGLE
SCAN_STEP_ANGLE = DEFAULT_SCAN_STEP_ANGLE

# ==============================================================================
# --- Donanım Başlatma Fonksiyonları ---
# ==============================================================================
def init_hardware():
    """Tüm donanım bileşenlerini başlatır."""
    global sensor, yellow_led, lcd, buzzer, current_motor_angle_global
    global in1_dev, in2_dev, in3_dev, in4_dev # gpiozero nesneleri için
    hardware_ok = True
    pid = os.getpid()
    try:
        print(f"[{pid}] Donanımlar başlatılıyor...")

        # Step motor pinlerini gpiozero OutputDevice olarak başlat
        in1_dev = OutputDevice(IN1_GPIO_PIN, active_high=True, initial_value=False)
        in2_dev = OutputDevice(IN2_GPIO_PIN, active_high=True, initial_value=False)
        in3_dev = OutputDevice(IN3_GPIO_PIN, active_high=True, initial_value=False)
        in4_dev = OutputDevice(IN4_GPIO_PIN, active_high=True, initial_value=False)
        print(f"[{pid}] 4-girişli step motor pinleri (gpiozero) ayarlandı.")

        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN)
        buzzer = Buzzer(BUZZER_PIN)

        yellow_led.off()
        buzzer.off()
        
        print(f"[{pid}] Step motor başlangıç açısına ({SCAN_START_ANGLE}°) ayarlanıyor...")
        move_motor_to_angle(SCAN_START_ANGLE)
        current_motor_angle_global = float(SCAN_START_ANGLE) 
        print(f"[{pid}] Step motor yaklaşık {current_motor_angle_global:.2f}° pozisyonuna getirildi.")

        print(f"[{pid}] Temel donanımlar başarıyla başlatıldı.")
    except Exception as e:
        print(f"[{pid}] KRİTİK HATA: Temel donanım başlatma hatası: {e}.");
        hardware_ok = False

    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear()
            lcd.cursor_pos = (0, 0); lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("gpiozero Hazir".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1.5)
            print(f"[{pid}] LCD Ekran başarıyla başlatıldı.")
        except Exception as e_lcd_init:
            print(f"[{pid}] UYARI: LCD başlatma hatası: {e_lcd_init}.");
            lcd = None
    else:
        lcd = None
    return hardware_ok

# ==============================================================================
# --- Step Motor Kontrol Fonksiyonları (gpiozero ile) ---
# ==============================================================================
def _set_step_pins(s1, s2, s3, s4):
    """IN1, IN2, IN3, IN4 pinlerine (OutputDevice) belirtilen değerleri atar."""
    global in1_dev, in2_dev, in3_dev, in4_dev
    if in1_dev: in1_dev.value = bool(s1) # True/False ata
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)

def _step_motor_4in(num_steps, direction_clockwise):
    """Belirtilen sayıda adımı belirtilen yönde atar (gpiozero ile)."""
    global current_step_sequence_index
    
    for _ in range(int(num_steps)):
        if direction_clockwise:
            current_step_sequence_index = (current_step_sequence_index + 1) % len(step_sequence)
        else:
            current_step_sequence_index = (current_step_sequence_index - 1 + len(step_sequence)) % len(step_sequence)
        
        current_phase = step_sequence[current_step_sequence_index]
        _set_step_pins(current_phase[0], current_phase[1], current_phase[2], current_phase[3])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    
    time.sleep(STEP_MOTOR_SETTLE_TIME)

def move_motor_to_angle(target_angle_deg):
    """Motoru mevcut açısından hedef açıya taşır (gpiozero ile)."""
    global current_motor_angle_global
    angle_diff_deg = target_angle_deg - current_motor_angle_global
    if abs(angle_diff_deg) < (DEG_PER_STEP / 2.0): return

    num_steps_to_move = round(abs(angle_diff_deg) / DEG_PER_STEP)
    if num_steps_to_move == 0: return

    direction_positive_angle_change = (angle_diff_deg > 0)
    print(f"[{os.getpid()}] Motor {current_motor_angle_global:.2f}° -> {target_angle_deg:.2f}° ({num_steps_to_move} adım, Yön: {'+' if direction_positive_angle_change else '-'}).")
    _step_motor_4in(num_steps_to_move, direction_positive_angle_change)
    
    actual_angle_moved = num_steps_to_move * DEG_PER_STEP * (1 if direction_positive_angle_change else -1)
    current_motor_angle_global += actual_angle_moved
    
    if abs(current_motor_angle_global - target_angle_deg) < DEG_PER_STEP:
        current_motor_angle_global = float(target_angle_deg)

# ==============================================================================
# --- Veritabanı, Kilit ve Diğer Yardımcı Fonksiyonlar ---
# (init_db_for_scan, acquire_lock_and_pid, shoelace_formula, calculate_perimeter aynı kalır)
# ==============================================================================
def init_db_for_scan():
    """Veritabanını başlatır ve yeni bir tarama kaydı oluşturur."""
    global current_scan_id_global
    pid = os.getpid()
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS servo_scans (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL UNIQUE, status TEXT, hesaplanan_alan_cm2 REAL DEFAULT NULL, cevre_cm REAL DEFAULT NULL, max_genislik_cm REAL DEFAULT NULL, max_derinlik_cm REAL DEFAULT NULL, start_angle_setting REAL, end_angle_setting REAL, step_angle_setting REAL, buzzer_distance_setting REAL)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS scan_points (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, derece REAL, mesafe_cm REAL, hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm REAL, FOREIGN KEY(scan_id) REFERENCES servo_scans(id) ON DELETE CASCADE)''')
        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")
        scan_start_time = time.time()
        cursor.execute("INSERT INTO servo_scans (start_time, status, start_angle_setting, end_angle_setting, step_angle_setting, buzzer_distance_setting) VALUES (?, ?, ?, ?, ?, ?)", (scan_start_time, 'running', SCAN_START_ANGLE, SCAN_END_ANGLE, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM))
        current_scan_id_global = cursor.lastrowid
        conn.commit()
        print(f"[{pid}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {current_scan_id_global}")
    except sqlite3.Error as e_db_init:
        print(f"[{pid}] KRİTİK HATA: DB başlatma/tarama kaydı hatası: {e_db_init}"); current_scan_id_global = None
    finally:
        if conn: conn.close()

def acquire_lock_and_pid():
    """Betik için kilit dosyası oluşturur ve PID'yi yazar."""
    global lock_file_handle
    pid = os.getpid()
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf: pf.write(str(pid))
        print(f"[{pid}] Betik kilidi ve PID başarıyla oluşturuldu.")
        return True
    except BlockingIOError:
        existing_pid = 'Bilinmiyor';
        try:
            with open(PID_FILE_PATH, 'r') as pf_check: existing_pid = pf_check.read().strip()
        except: pass
        print(f"[{pid}] UYARI: Kilit dosyası mevcut. Betik zaten çalışıyor olabilir (PID: {existing_pid}). Çıkılıyor.")
        if lock_file_handle: lock_file_handle.close(); lock_file_handle = None
        return False
    except PermissionError as e:
        print(f"[{pid}] KRİTİK İZİN HATASI: '{e.filename}' oluşturulamıyor. 'sudo' ile deneyin veya eski dosyaları silin.")
        if lock_file_handle: lock_file_handle.close(); lock_file_handle = None
        return False
    except Exception as e:
        print(f"[{pid}] Kilit/PID alınırken beklenmedik hata: {e}")
        if lock_file_handle: lock_file_handle.close(); lock_file_handle = None
        return False

def shoelace_formula(noktalar):
    n = len(noktalar); area = 0.0
    if n < 3: return 0.0
    for i in range(n): area += (noktalar[i][0] * noktalar[(i + 1) % n][1]) - (noktalar[(i + 1) % n][0] * noktalar[i][1])
    return abs(area) / 2.0

def calculate_perimeter(cartesian_points):
    perimeter, n = 0.0, len(cartesian_points)
    if n == 0: return 0.0
    perimeter += math.sqrt(cartesian_points[0][0]**2 + cartesian_points[0][1]**2)
    for i in range(n - 1): perimeter += math.sqrt((cartesian_points[i+1][0] - cartesian_points[i][0])**2 + (cartesian_points[i+1][1] - cartesian_points[i][1])**2)
    perimeter += math.sqrt(cartesian_points[-1][0]**2 + cartesian_points[-1][1]**2)
    return perimeter

def release_resources_on_exit():
    """Betik sonlandığında çağrılacak temizleme fonksiyonu."""
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, yellow_led, lcd, buzzer
    global in1_dev, in2_dev, in3_dev, in4_dev # gpiozero nesneleri
    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Çıkış durumu: {script_exit_status_global}")
    
    # (Veritabanı güncelleme kısmı aynı kalır)
    if db_conn_main_script_global:
        try: db_conn_main_script_global.close(); db_conn_main_script_global = None
        except: pass
    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (current_scan_id_global,))
            db_status = cursor_exit.fetchone()
            if db_status and db_status[0] == 'running':
                expected_statuses = ['completed_analysis', 'completed_insufficient_points', 'terminated_close_object', 'interrupted_ctrl_c', 'error_in_loop']
                final_status = script_exit_status_global if script_exit_status_global in expected_statuses else 'interrupted_unexpectedly'
                cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?", (final_status, current_scan_id_global))
                conn_exit.commit()
        except Exception as e: print(f"[{pid}] HATA: Çıkışta DB durum güncelleme: {e}")
        finally:
            if conn_exit: conn_exit.close()

    print(f"[{pid}] Donanım kapatılıyor...")
    try: # Step motor pinlerini kapat (enerjiyi kes)
        _set_step_pins(0,0,0,0) # Tüm pinleri LOW yap
        print(f"[{pid}] Step motor pinleri LOW durumuna getirildi.")
    except Exception as e: print(f"[{pid}] Step motor pinleri sıfırlanırken hata: {e}")
    
    # gpiozero OutputDevice nesnelerini kapat
    # RPi.GPIO.cleanup() yerine bu kullanılır
    gpio_devices_to_close = [in1_dev, in2_dev, in3_dev, in4_dev, yellow_led, buzzer, sensor]
    for dev in gpio_devices_to_close:
        if dev and hasattr(dev, 'close'):
            try:
                if hasattr(dev, 'is_active') and dev.is_active: # LED, Buzzer için
                    dev.off()
                dev.close()
            except Exception as e_dev_close:
                print(f"[{pid}] Bir gpiozero cihazı kapatılırken hata: {e_dev_close}")
    print(f"[{pid}] Tüm gpiozero cihazları kapatıldı.")

    if lcd: # LCD kapatma mesajları
        try:
            lcd.clear(); lcd.cursor_pos = (0,0); lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1); lcd.clear()
            lcd.cursor_pos = (0,0); lcd.write_string("M.Erdem OZER".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1,0); lcd.write_string("(PhD.)".ljust(LCD_COLS)[:LCD_COLS])
        except: pass

    # (Kilit ve PID dosyalarını silme kısmı aynı kalır)
    if lock_file_handle:
        try: fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close(); lock_file_handle = None
        except: pass
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_del = False;
                    try:
                        with open(f_path, 'r') as pf_c:
                            if int(pf_c.read().strip()) == pid: can_del = True
                    except: pass
                    if can_del: os.remove(f_path)
                elif f_path == LOCK_FILE_PATH: os.remove(f_path)
        except: pass
    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")

# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# (Bu blok büyük ölçüde aynı kalır, sadece GPIO.cleanup() çağrısı olmaz)
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motorlu 2D Alan Tarama Betiği (gpiozero)")
    parser.add_argument("--start_angle", type=float, default=DEFAULT_SCAN_START_ANGLE)
    parser.add_argument("--end_angle", type=float, default=DEFAULT_SCAN_END_ANGLE)
    parser.add_argument("--step_angle", type=float, default=DEFAULT_SCAN_STEP_ANGLE)
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE)
    args = parser.parse_args()

    SCAN_START_ANGLE, SCAN_END_ANGLE, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM = \
        float(args.start_angle), float(args.end_angle), float(args.step_angle), int(args.buzzer_distance)
    
    if SCAN_STEP_ANGLE <= 0: SCAN_STEP_ANGLE = 1.0

    atexit.register(release_resources_on_exit)

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)
    init_db_for_scan()
    if not current_scan_id_global: sys.exit(1)

    collected_cartesian_points_for_area = []
    pid = os.getpid()

    print(f"[{pid}] Step Motor (gpiozero) ile 2D Tarama Başlıyor (ID: {current_scan_id_global})...")
    if lcd:
        lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string(f"ScanID:{current_scan_id_global} Step".ljust(LCD_COLS)[:LCD_COLS])
        if LCD_ROWS > 1: lcd.cursor_pos=(1,0); lcd.write_string(f"A:{SCAN_START_ANGLE:.0f}-{SCAN_END_ANGLE:.0f} S:{SCAN_STEP_ANGLE:.0f}".ljust(LCD_COLS)[:LCD_COLS])

    scan_completed_successfully = False
    lcd_warning_mode = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()
        target_loop_angle = float(SCAN_START_ANGLE)

        while True:
            loop_iteration_start_time = time.time()
            move_motor_to_angle(target_loop_angle)
            current_effective_degree_for_scan = current_motor_angle_global

            if yellow_led: yellow_led.toggle()
            
            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            angle_rad = math.radians(current_effective_degree_for_scan)
            x_cm, y_cm = distance_cm * math.cos(angle_rad), distance_cm * math.sin(angle_rad)

            if 0 < distance_cm < (sensor.max_distance * 100 - 1):
                collected_cartesian_points_for_area.append((x_cm, y_cm))

            hiz_cm_s = 0.0 # Hız hesaplama eklenebilir

            is_object_close = distance_cm <= BUZZER_DISTANCE_CM
            if buzzer:
                if is_object_close and not buzzer.is_active: buzzer.on()
                elif not is_object_close and buzzer.is_active: buzzer.off()

            if lcd:
                try:
                    if is_object_close and not lcd_warning_mode:
                        lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("!!! UYARI !!!".center(LCD_COLS)); lcd_warning_mode=True
                        if LCD_ROWS > 1: lcd.cursor_pos=(1,0); lcd.write_string("NESNE YAKIN!".center(LCD_COLS))
                    elif not is_object_close and lcd_warning_mode:
                        lcd.clear(); lcd_warning_mode=False
                    if not lcd_warning_mode:
                        lcd.cursor_pos=(0,0); lcd.write_string(f"A:{current_effective_degree_for_scan:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: lcd.cursor_pos=(1,0); lcd.write_string(f"X{x_cm:3.0f}Y{y_cm:3.0f} H{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
                except Exception as e_lcd: print(f"[{pid}] LCD yazma hatası: {e_lcd}")

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{pid}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Sonlandırılıyor.")
                if lcd: lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                if yellow_led: yellow_led.on()
                script_exit_status_global = 'terminated_close_object'
                time.sleep(1.0); break

            try:
                cursor_main.execute("INSERT INTO scan_points (scan_id,derece,mesafe_cm,hiz_cm_s,timestamp,x_cm,y_cm) VALUES (?,?,?,?,?,?,?)",
                                    (current_scan_id_global,current_effective_degree_for_scan,distance_cm,hiz_cm_s,current_timestamp,x_cm,y_cm))
                db_conn_main_script_global.commit()
            except Exception as e_db: print(f"[{pid}] DB Ekleme Hatası: {e_db}")
            
            if SCAN_END_ANGLE >= SCAN_START_ANGLE:
                if target_loop_angle >= SCAN_END_ANGLE: break
                target_loop_angle += SCAN_STEP_ANGLE
                if target_loop_angle > SCAN_END_ANGLE: target_loop_angle = float(SCAN_END_ANGLE)
            else:
                if target_loop_angle <= SCAN_END_ANGLE: break
                target_loop_angle -= SCAN_STEP_ANGLE
                if target_loop_angle < SCAN_END_ANGLE: target_loop_angle = float(SCAN_END_ANGLE)
            
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - (time.time() - loop_iteration_start_time))
            if sleep_duration > 0 : time.sleep(sleep_duration)
        
        # (Döngü sonrası analiz ve DB güncelleme kısmı aynı kalır)
        if len(collected_cartesian_points_for_area) >= 2:
            # ... (alan, çevre hesaplama)
            script_exit_status_global = 'completed_analysis'
            # ... (DB'ye yazma)
        else:
            script_exit_status_global = 'completed_insufficient_points'
        scan_completed_successfully = True

    except KeyboardInterrupt: # ... (aynı kalır)
        script_exit_status_global = 'interrupted_ctrl_c'
    except Exception as e: # ... (aynı kalır)
        script_exit_status_global = 'error_in_loop'
    finally: # ... (aynı kalır)
        if not scan_completed_successfully and script_exit_status_global not in ['interrupted_ctrl_c', 'error_in_loop', 'terminated_close_object']:
             script_exit_status_global = 'interrupted_unexpectedly_in_main'
        print(f"[{pid}] Ana betik sonlanıyor. Çıkış: {script_exit_status_global}")
