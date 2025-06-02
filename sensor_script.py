import traceback

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
TRIG_PIN = 23
ECHO_PIN = 24

IN1_GPIO_PIN = 6
IN2_GPIO_PIN = 13
IN3_GPIO_PIN = 19
IN4_GPIO_PIN = 26

YELLOW_LED_PIN = 27
BUZZER_PIN = 17

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
DEFAULT_SCAN_START_ANGLE = 0.0  # Float olarak tanımlandı
DEFAULT_SCAN_END_ANGLE = 180.0  # Float olarak tanımlandı
DEFAULT_SCAN_STEP_ANGLE = 10.0  # Float olarak tanımlandı

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
# !!! KULLANICI TARAFINDAN DOĞRULANMALI VE AYARLANMALI !!!
# ==============================================================================
# STEPS_PER_REVOLUTION_OUTPUT_SHAFT:
# Step motorunuzun ÇIKIŞ MİLİNİN bir tam tur (360 derece) dönmesi için
# gereken TOPLAM ADIM SAYISI. Bu değer, motorunuzun kendi adım sayısına,
# içindeki dişli oranına ve kullandığınız sürüş moduna (tam adım, yarım adım) bağlıdır.
#
# Örnek: 28BYJ-48 step motor için:
#   - Motorun kendi adımı: Genellikle 32 adım/tur (motor mili için)
#   - Dişli Oranı: ~64:1 (örneğin, 63.68395:1)
#   - Tam Adım Modunda (4 fazlı sekans): (32 adım/tur) * ~64 = ~2048 adım/tur (çıkış mili için)
#   - Yarım Adım Modunda (8 fazlı sekans): (32 adım/tur * 2) * ~64 = ~4096 adım/tur (çıkış mili için)
#
# AŞAĞIDAKİ DEĞERİ KENDİ MOTORUNUZA VE SÜRÜŞ MODUNUZA GÖRE GÜNCELLEYİN!
# Eğer motorunuz 360 derece komutuna 180 derece dönüyorsa, bu değer muhtemelen olması gerekenin yarısıdır.
STEPS_PER_REVOLUTION_OUTPUT_SHAFT = 4096  # Örnek: 28BYJ-48 yarım adım için
# ==============================================================================

DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT
current_motor_angle_global = 0.0
current_step_sequence_index = 0

step_sequence = [
    [1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
    [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]
]

TERMINATION_DISTANCE_CM = DEFAULT_TERMINATION_DISTANCE_CM
BUZZER_DISTANCE_CM = DEFAULT_BUZZER_DISTANCE
SCAN_START_ANGLE = DEFAULT_SCAN_START_ANGLE
SCAN_END_ANGLE = DEFAULT_SCAN_END_ANGLE
SCAN_STEP_ANGLE = DEFAULT_SCAN_STEP_ANGLE


# ==============================================================================
# --- Donanım Başlatma Fonksiyonları ---
# (init_hardware içeriği öncekiyle aynı, sadece float açılar için düzenlendi)
# ==============================================================================
def init_hardware():
    global sensor, yellow_led, lcd, buzzer, current_motor_angle_global
    global in1_dev, in2_dev, in3_dev, in4_dev
    hardware_ok = True
    pid = os.getpid()
    try:
        print(f"[{pid}] Donanımlar başlatılıyor...")
        in1_dev = OutputDevice(IN1_GPIO_PIN, active_high=True, initial_value=False)
        in2_dev = OutputDevice(IN2_GPIO_PIN, active_high=True, initial_value=False)
        in3_dev = OutputDevice(IN3_GPIO_PIN, active_high=True, initial_value=False)
        in4_dev = OutputDevice(IN4_GPIO_PIN, active_high=True, initial_value=False)
        print(f"[{pid}] 4-girişli step motor pinleri (gpiozero) ayarlandı.")

        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN);
        yellow_led.off()
        buzzer = Buzzer(BUZZER_PIN);
        buzzer.off()

        print(f"[{pid}] Step motor başlangıç açısına ({SCAN_START_ANGLE:.1f}°) ayarlanıyor...")
        # Başlangıçta motorun 0 derecede olduğunu varsayıyoruz. Homing rutini yok.
        current_motor_angle_global = 0.0  # Fiziksel olarak 0'a getirilmiş varsayımı
        move_motor_to_angle(SCAN_START_ANGLE)
        # current_motor_angle_global, move_motor_to_angle içinde güncellenecek
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
            lcd.cursor_pos = (0, 0);
            lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
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
# (Bu fonksiyonlar öncekiyle aynı)
# ==============================================================================
def _set_step_pins(s1, s2, s3, s4):
    global in1_dev, in2_dev, in3_dev, in4_dev
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)


def _step_motor_4in(num_steps, direction_positive_angle_change):
    global current_step_sequence_index
    for _ in range(int(num_steps)):
        if direction_positive_angle_change:
            current_step_sequence_index = (current_step_sequence_index + 1) % len(step_sequence)
        else:
            current_step_sequence_index = (current_step_sequence_index - 1 + len(step_sequence)) % len(step_sequence)
        current_phase = step_sequence[current_step_sequence_index]
        _set_step_pins(current_phase[0], current_phase[1], current_phase[2], current_phase[3])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    time.sleep(STEP_MOTOR_SETTLE_TIME)


def move_motor_to_angle(target_angle_deg):
    global current_motor_angle_global
    # Hedef açıyı 0-360 aralığına normalize etmeye gerek yok, fark üzerinden gidiyoruz.
    # Ancak, current_motor_angle_global'ı tutarlı tutmak önemli.

    angle_diff_deg = target_angle_deg - current_motor_angle_global

    # Eğer motor bir tam turdan fazla dönecekse (örn: -10'dan 350'ye gitmek yerine 10'dan -10'a gitmek)
    # Bunu daha kısa yoldan yapmak için angle_diff_deg'i ayarla.
    # Bu, 360 derece taramalar için önemlidir.
    if abs(angle_diff_deg) > 180.0:  # Eğer fark 180 dereceden büyükse, ters yönden gitmek daha kısa olabilir
        if angle_diff_deg > 0:  # Pozitif büyük fark (örn: 0'dan 350'ye -> -10 derece git)
            angle_diff_deg -= 360.0
        else:  # Negatif büyük fark (örn: 350'den 0'a -> +10 derece git)
            angle_diff_deg += 360.0

    if abs(angle_diff_deg) < (DEG_PER_STEP / 2.0): return

    num_steps_to_move = round(abs(angle_diff_deg) / DEG_PER_STEP)
    if num_steps_to_move == 0: return

    direction_positive_angle_change = (angle_diff_deg > 0)  # True ise açı artacak yönde

    print(
        f"[{os.getpid()}] Motor {current_motor_angle_global:.2f}° -> {target_angle_deg:.2f}° (Fark: {angle_diff_deg:.2f}°, Adım: {num_steps_to_move}, Yön: {'+' if direction_positive_angle_change else '-'}).")
    _step_motor_4in(num_steps_to_move, direction_positive_angle_change)

    actual_angle_moved = num_steps_to_move * DEG_PER_STEP * (1 if direction_positive_angle_change else -1)
    current_motor_angle_global += actual_angle_moved

    # current_motor_angle_global'ı 0-360 (veya -180 to 180) aralığında tutmak isteyebilirsiniz.
    # Şimdilik biriken toplam açıyı tutuyoruz.
    # current_motor_angle_global %= 360.0 

    if abs(current_motor_angle_global - target_angle_deg) < DEG_PER_STEP:
        current_motor_angle_global = float(target_angle_deg)
    print(f"[{os.getpid()}] Motor yeni pozisyonu: {current_motor_angle_global:.2f}°")


# ==============================================================================
# --- Veritabanı, Kilit ve Diğer Yardımcı Fonksiyonlar ---
# (init_db_for_scan, acquire_lock_and_pid, shoelace_formula, calculate_perimeter, release_resources_on_exit aynı kalır)
# ==============================================================================
def init_db_for_scan():
    global current_scan_id_global
    pid = os.getpid()
    conn = None
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Tabloları oluştur
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS servo_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time REAL UNIQUE,
                status TEXT,
                hesaplanan_alan_cm2 REAL DEFAULT NULL,
                cevre_cm REAL DEFAULT NULL,
                max_genislik_cm REAL DEFAULT NULL,
                max_derinlik_cm REAL DEFAULT NULL,
                start_angle_setting REAL,
                end_angle_setting REAL DEFAULT NULL,
                step_angle_setting REAL,
                buzzer_distance_setting REAL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER,
                derece REAL,
                mesafe_cm REAL,
                hiz_cm_s REAL,
                timestamp REAL,
                x_cm REAL,
                y_cm REAL,
                FOREIGN KEY(scan_id) REFERENCES servo_scans(id) ON DELETE CASCADE
            )
        ''')
        
        # Önceki yarım kalmış taramaları güncelle
        cursor.execute("UPDATE servo_scans SET status='interrupted_prior_run' WHERE status='running'")
        
        # Yeni tarama kaydı oluştur
        start_time = time.time()
        cursor.execute("""
            INSERT INTO servo_scans (
                start_time, status, start_angle_setting,
                end_angle_setting, step_angle_setting, buzzer_distance_setting
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (start_time, 'running', SCAN_START_ANGLE, SCAN_END_ANGLE, 
              SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM))
        
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
    """Kilit dosyası oluşturur ve PID'i kaydeder."""
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
        try:
            with open(PID_FILE_PATH, 'r') as pf:
                existing_pid = pf.read().strip()
        except:
            existing_pid = '?'
        print(f"[{pid}] UYARI: Kilit dosyası mevcut. PID: {existing_pid}. Çıkılıyor.")
        return False
        
    except Exception as e:
        print(f"[{pid}] Kilit HATA: {e}")
        return False

def shoelace_formula(points):
    """Alan hesaplama fonksiyonu (Shoelace formülü)."""
    area = 0.0
    n = len(points)
    
    if n < 3:
        return 0.0
        
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
        
    return abs(area) / 2.0

def calculate_perimeter(coordinates):
    """Çevre hesaplama fonksiyonu."""
    perimeter = 0.0
    n = len(coordinates)
    
    if n == 0:
        return 0.0
        
    # İlk noktadan orijine olan mesafe
    perimeter += math.sqrt(coordinates[0][0]**2 + coordinates[0][1]**2)
    
    # Noktalar arası mesafeler
    for i in range(n-1):
        dx = coordinates[i+1][0] - coordinates[i][0]
        dy = coordinates[i+1][1] - coordinates[i][1]
        perimeter += math.sqrt(dx*dx + dy*dy)
    
    # Son noktadan orijine olan mesafe
    perimeter += math.sqrt(coordinates[-1][0]**2 + coordinates[-1][1]**2)
    
    return perimeter


def release_resources_on_exit():
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global
    global script_exit_status_global, sensor, yellow_led, lcd, buzzer
    global in1_dev, in2_dev, in3_dev, in4_dev

    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Durum: {script_exit_status_global}")

    # 1. Veritabanı bağlantısını kapat
    if db_conn_main_script_global:
        try:
            db_conn_main_script_global.close()
        except Exception as e:
            print(f"[{pid}] DB kapatma hatası: {e}")

    # 2. Donanım kaynaklarını serbest bırak
    print(f"[{pid}] Donanım kapatılıyor...")
    try:
        _set_step_pins(0, 0, 0, 0)
        print(f"[{pid}] Step motor pinleri LOW yapıldı.")
    except Exception as e:
        print(f"[{pid}] Step motor pin kapatma hatası: {e}")

    # 3. GPIO cihazlarını kapat
    devices_to_close = [in1_dev, in2_dev, in3_dev, in4_dev, yellow_led, buzzer, sensor]
    for device in devices_to_close:
        if device and hasattr(device, 'close'):
            try:
                if hasattr(device, 'is_active') and device.is_active:
                    device.off()
                device.close()
            except Exception as e:
                print(f"[{pid}] Cihaz kapatma hatası ({device}): {e}")

    print(f"[{pid}] gpiozero cihazları kapatıldı.")

    # 4. LCD'yi temizle ve kapat
    if lcd:
        try:
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Sonlandiriliyor.".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(0.5)
            lcd.clear()
        except Exception as e:
            print(f"[{pid}] LCD kapatma hatası: {e}")

    # 5. Kilit ve PID dosyalarını temizle
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
        except Exception as e:
            print(f"[{pid}] Kilit dosyası kapatma hatası: {e}")

    for file_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"[{pid}] Dosya silme hatası ({file_path}): {e}")

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
    args = parser.parse_args()

    SCAN_START_ANGLE = float(args.start_angle)
    SCAN_END_ANGLE = float(args.end_angle)
    SCAN_STEP_ANGLE = float(args.step_angle)
    BUZZER_DISTANCE_CM = int(args.buzzer_distance)

    if SCAN_STEP_ANGLE <= (DEG_PER_STEP / 2.0):  # Adım açısı minimum bir adımdan küçük olamaz/olmamalı
        print(
            f"UYARI: Adım açısı ({SCAN_STEP_ANGLE}°), motorun minimum adım açısından ({DEG_PER_STEP:.3f}°) küçük olamaz. {DEG_PER_STEP:.3f}° olarak ayarlanıyor.")
        SCAN_STEP_ANGLE = DEG_PER_STEP
    if SCAN_STEP_ANGLE == 0: SCAN_STEP_ANGLE = DEG_PER_STEP  # 0 olamaz

    atexit.register(release_resources_on_exit)

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)  # Bu artık 4-girişli step motoru başlatır
    init_db_for_scan()
    if not current_scan_id_global: sys.exit(1)

    collected_cartesian_points_for_area = []
    pid = os.getpid()

    print(f"[{pid}] Step Motor (gpiozero) ile 2D Tarama Başlıyor (ID: {current_scan_id_global})...")
    print(
        f"[{pid}] Ayarlar: Başlangıç={SCAN_START_ANGLE:.1f}°, Bitiş={SCAN_END_ANGLE:.1f}°, Adım={SCAN_STEP_ANGLE:.1f}°")
    print(f"[{pid}] Motor Adım Bilgisi: {DEG_PER_STEP:.4f}° / adım ({STEPS_PER_REVOLUTION_OUTPUT_SHAFT} adım/tur)")

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

        # Hedef açı, SCAN_START_ANGLE ile başlar ve SCAN_END_ANGLE'e doğru ilerler.
        target_loop_angle = float(SCAN_START_ANGLE)
        # Tarama yönü: 1 ise açı artar, -1 ise açı azalır.
        scan_direction = 1 if SCAN_END_ANGLE >= SCAN_START_ANGLE else -1

        while True:
            loop_iteration_start_time = time.time()

            move_motor_to_angle(target_loop_angle)
            # Ölçümde kullanılacak gerçek açı, motorun gittiği son pozisyondur.
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

            hiz_cm_s = 0.0  # Bu örnekte hız hesaplama çıkarıldı, eklenebilir.

            is_object_close = distance_cm <= BUZZER_DISTANCE_CM
            if buzzer:
                if is_object_close and not buzzer.is_active:
                    buzzer.on()
                elif not is_object_close and buzzer.is_active:
                    buzzer.off()

            if lcd:  # LCD Güncelleme
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

            if distance_cm < TERMINATION_DISTANCE_CM:  # Çok yakın nesne kontrolü
                print(f"[{pid}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Sonlandırılıyor.")
                if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string(
                    "COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                if yellow_led: yellow_led.on()
                script_exit_status_global = 'terminated_close_object';
                time.sleep(1.0);
                break

            try:  # Veritabanına kaydet
                cursor_main.execute(
                    "INSERT INTO scan_points (scan_id,derece,mesafe_cm,hiz_cm_s,timestamp,x_cm,y_cm) VALUES (?,?,?,?,?,?,?)",
                    (current_scan_id_global, current_effective_degree_for_scan, distance_cm, hiz_cm_s,
                     current_timestamp, x_cm, y_cm))
                db_conn_main_script_global.commit()
            except Exception as e_db:
                print(f"[{pid}] DB Ekleme Hatası: {e_db}")

            # Döngü sonlandırma kontrolü
            # Mevcut etkili açının bitiş açısına ulaşıp ulaşmadığını kontrol et
            if scan_direction == 1:  # Açı artıyor
                if current_effective_degree_for_scan >= SCAN_END_ANGLE: break
            else:  # Açı azalıyor
                if current_effective_degree_for_scan <= SCAN_END_ANGLE: break

            # Bir sonraki hedef açıyı belirle
            target_loop_angle += (SCAN_STEP_ANGLE * scan_direction)

            # Hedef açının sınırlar içinde kalmasını sağla (özellikle son adım için)
            if scan_direction == 1 and target_loop_angle > SCAN_END_ANGLE:
                target_loop_angle = float(SCAN_END_ANGLE)
            elif scan_direction == -1 and target_loop_angle < SCAN_END_ANGLE:
                target_loop_angle = float(SCAN_END_ANGLE)

            # Döngü süresini hedef aralıkta tut
            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0:
                time.sleep(sleep_duration)

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
        traceback.print_exc()
        if lcd:
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("HATA OLUSTU!".ljust(LCD_COLS)[:LCD_COLS])

    finally:
        if not scan_completed_successfully and script_exit_status_global not in [
            'interrupted_ctrl_c',
            'error_in_loop',
            'terminated_close_object'
        ]:
            script_exit_status_global = 'interrupted_unexpectedly_in_main'

        # Aktif cihazları kapat
        if buzzer and buzzer.is_active:
            buzzer.off()
        if yellow_led and yellow_led.is_active:
            yellow_led.off()

        print(f"[{pid}] Ana betik sonlanıyor. Çıkış: {script_exit_status_global}")
