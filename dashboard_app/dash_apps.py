from gpiozero import DistanceSensor, LED, Buzzer
from RPLCD.i2c import CharLCD
import RPi.GPIO as GPIO # Step motor kontrolü için RPi.GPIO
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
IN1_PIN = 6   # Sürücü kartındaki IN1'e bağlı GPIO pini
IN2_PIN = 13  # Sürücü kartındaki IN2'ye bağlı GPIO pini
IN3_PIN = 19  # Sürücü kartındaki IN3'e bağlı GPIO pini
IN4_PIN = 26  # Sürücü kartındaki IN4'e bağlı GPIO pini

# --- Diğer Donanım Pinleri ---
YELLOW_LED_PIN = 27 # Durum/Uyarı LED'i
BUZZER_PIN = 17     # Buzzer

# --- LCD Ayarları ---
LCD_I2C_ADDRESS = 0x27      # LCD'nin I2C adresi
LCD_PORT_EXPANDER = 'PCF8574' # Kullanılan I2C port genişletici
LCD_COLS = 16               # LCD sütun sayısı
LCD_ROWS = 2                # LCD satır sayısı
I2C_PORT = 1                # Raspberry Pi I2C portu (genellikle 1)

# ==============================================================================
# --- Varsayılan Tarama ve Eşik Değerleri ---
# ==============================================================================
DEFAULT_TERMINATION_DISTANCE_CM = 1  # Bu mesafeden yakınsa tarama durur (cm)
DEFAULT_BUZZER_DISTANCE = 10         # Buzzer'ın çalmaya başlayacağı mesafe (cm)
DEFAULT_SCAN_START_ANGLE = 0         # Tarama başlangıç açısı (derece)
DEFAULT_SCAN_END_ANGLE = 180         # Tarama bitiş açısı (derece)
DEFAULT_SCAN_STEP_ANGLE = 10         # Tarama adım açısı (derece)

# --- Step Motor Zamanlama Ayarları ---
# Bu değerler motorunuzun tipine ve istediğiniz hıza göre ayarlanmalıdır.
# 28BYJ-48 için 0.001 ile 0.002 arası iyi bir başlangıç olabilir. Daha düşük değer = daha hızlı.
STEP_MOTOR_INTER_STEP_DELAY = 0.0015 # Adım fazları arasındaki gecikme (saniye)
STEP_MOTOR_SETTLE_TIME = 0.05      # Adım grubundan sonra motorun durması için bekleme (saniye)
LOOP_TARGET_INTERVAL_S = 0.6       # Her bir ölçüm döngüsünün hedeflenen süresi (saniye)

# ==============================================================================
# --- Dosya Yolları ve Global Değişkenler ---
# ==============================================================================
try:
    PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError: # __file__ tanımlı değilse (örn: interaktif mod)
    PROJECT_ROOT_DIR = os.getcwd()

DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

# --- Global Değişkenler ---
sensor, yellow_led, lcd, buzzer = None, None, None, None
lock_file_handle, current_scan_id_global, db_conn_main_script_global = None, None, None
script_exit_status_global = 'interrupted_unexpectedly' # Betiğin çıkış durumu

# --- Step Motor Özellikleri (28BYJ-48 Yarım Adım Modu için Tipik) ---
# ÖNEMLİ: Bu değeri kendi motorunuzun datasheet'ine ve sürüş modunuza göre ayarlayın!
# 28BYJ-48 motorlar genellikle iç dişli oranına sahiptir.
# Yarım adım (half-step) modunda, çıkış milinin bir tam turu için 4096 adım yaygın bir değerdir.
STEPS_PER_REVOLUTION_OUTPUT_SHAFT = 4096
DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT # Bir adımda çıkış milinin döndüğü derece
current_motor_angle_global = 0.0 # Motorun mevcut açısını derece cinsinden takip eder
current_step_sequence_index = 0  # Motorun mevcut adım fazını (sekans içindeki indeksi) takip eder

# Yarım adım (half-step) sekansı (8 adım) - ULN2003 ve 28BYJ-48 için yaygın
# Motor sargılarına ve bağlantı sırasına göre bu sekans veya pinlerin sırası değişebilir!
# [IN1, IN2, IN3, IN4]
step_sequence = [
    [1,0,0,0], # Faz 1
    [1,1,0,0], # Faz 2
    [0,1,0,0], # Faz 3
    [0,1,1,0], # Faz 4
    [0,0,1,0], # Faz 5
    [0,0,1,1], # Faz 6
    [0,0,0,1], # Faz 7
    [1,0,0,1]  # Faz 8
]
# Alternatif: Tam Adım (Daha az hassas, potansiyel olarak daha fazla tork)
# step_sequence_full = [[1,1,0,0], [0,1,1,0], [0,0,1,1], [1,0,0,1]]
# Alternatif: Dalga Sürüşü (Wave Drive - Daha az tork)
# step_sequence_wave = [[1,0,0,0], [0,1,0,0], [0,0,1,0], [0,0,0,1]]

# --- Çalışma Zamanı Ayarları (Argümanlarla Değişebilir) ---
TERMINATION_DISTANCE_CM = DEFAULT_TERMINATION_DISTANCE_CM
BUZZER_DISTANCE_CM = DEFAULT_BUZZER_DISTANCE
SCAN_START_ANGLE = DEFAULT_SCAN_START_ANGLE
SCAN_END_ANGLE = DEFAULT_SCAN_END_ANGLE
SCAN_STEP_ANGLE = DEFAULT_SCAN_STEP_ANGLE

# ==============================================================================
# --- GPIO ve Donanım Başlatma Fonksiyonları ---
# ==============================================================================
def setup_gpio_stepper_4in():
    """Step motor için GPIO pinlerini ayarlar."""
    GPIO.setmode(GPIO.BCM) # Broadcom pin numaralandırmasını kullan
    GPIO.setwarnings(False) # GPIO uyarılarını kapat
    GPIO.setup(IN1_PIN, GPIO.OUT)
    GPIO.setup(IN2_PIN, GPIO.OUT)
    GPIO.setup(IN3_PIN, GPIO.OUT)
    GPIO.setup(IN4_PIN, GPIO.OUT)
    # Başlangıçta tüm motor pinlerini LOW (kapalı) yap
    GPIO.output(IN1_PIN, GPIO.LOW)
    GPIO.output(IN2_PIN, GPIO.LOW)
    GPIO.output(IN3_PIN, GPIO.LOW)
    GPIO.output(IN4_PIN, GPIO.LOW)

def init_hardware():
    """Tüm donanım bileşenlerini başlatır."""
    global sensor, yellow_led, lcd, buzzer, current_motor_angle_global
    hardware_ok = True
    pid = os.getpid()
    try:
        print(f"[{pid}] Donanımlar başlatılıyor...")
        setup_gpio_stepper_4in() # Step motor GPIO ayarları
        print(f"[{pid}] 4-girişli step motor pinleri (IN1-IN4) ayarlandı.")

        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN)
        buzzer = Buzzer(BUZZER_PIN)

        yellow_led.off() # Başlangıçta LED kapalı
        buzzer.off()   # Başlangıçta buzzer kapalı
        
        # Step motoru başlangıç açısına (SCAN_START_ANGLE) getir.
        # ÖNEMLİ: Bu, motorun mevcut fiziksel pozisyonunun 0 derece olduğunu varsayar.
        # Daha hassas bir sistem için, açılışta bir "homing" rutini (limit switch ile)
        # veya bilinen bir referans noktasına gitmesi gerekebilir.
        print(f"[{pid}] Step motor başlangıç açısına ({SCAN_START_ANGLE}°) ayarlanıyor...")
        move_motor_to_angle(SCAN_START_ANGLE) # Hedef açıya git
        # current_motor_angle_global, move_motor_to_angle içinde güncellenir.
        # Ancak tam olarak set etmek için:
        current_motor_angle_global = float(SCAN_START_ANGLE) 
        print(f"[{pid}] Step motor yaklaşık {current_motor_angle_global:.2f}° pozisyonuna getirildi.")

        print(f"[{pid}] Temel donanımlar (Sensör, LED, Buzzer, Step Motor) başarıyla başlatıldı.")
    except Exception as e:
        print(f"[{pid}] KRİTİK HATA: Temel donanım başlatma hatası: {e}. GPIO pinlerini ve bağlantıları kontrol edin.");
        hardware_ok = False

    if hardware_ok: # Sadece temel donanımlar sorunsuz başlatıldıysa LCD'yi dene
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear()
            lcd.cursor_pos = (0, 0); lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("Hazirlaniyor...".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1.5) # Mesajın okunması için bekle
            print(f"[{pid}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
        except Exception as e_lcd_init:
            print(f"[{pid}] UYARI: LCD başlatma hatası: {e_lcd_init}. LCD olmadan devam edilecek.");
            lcd = None # LCD kullanılamaz durumda
    else:
        lcd = None # Temel donanım hatası varsa LCD'yi de None yap
    return hardware_ok

# ==============================================================================
# --- Step Motor Kontrol Fonksiyonları (4-Girişli Sürücü için) ---
# ==============================================================================
def _set_step_pins(s1, s2, s3, s4):
    """IN1, IN2, IN3, IN4 pinlerine belirtilen değerleri atar."""
    GPIO.output(IN1_PIN, s1)
    GPIO.output(IN2_PIN, s2)
    GPIO.output(IN3_PIN, s3)
    GPIO.output(IN4_PIN, s4)

def _step_motor_4in(num_steps, direction_clockwise):
    """
    Belirtilen sayıda adımı belirtilen yönde atar (4-girişli sürücü için).
    `direction_clockwise`: True ise saat yönü (açı artışı), False ise tersi.
    """
    global current_step_sequence_index
    
    for _ in range(int(num_steps)): # Adım sayısı tam sayı olmalı
        if direction_clockwise:
            current_step_sequence_index = (current_step_sequence_index + 1) % len(step_sequence)
        else: # Saat yönünün tersi
            current_step_sequence_index = (current_step_sequence_index - 1 + len(step_sequence)) % len(step_sequence)
        
        current_phase = step_sequence[current_step_sequence_index]
        _set_step_pins(current_phase[0], current_phase[1], current_phase[2], current_phase[3])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY) # Adımlar (fazlar) arası gecikme (hızı belirler)
    
    # Adımlar tamamlandıktan sonra motor sargılarının enerjisini kesmek isteyebilirsiniz (güç tasarrufu).
    # Ancak bu, motorun pozisyonunu tutma torkunu kaybetmesine neden olur.
    # Tarama sırasında enerjili kalması genellikle daha iyidir.
    # _set_step_pins(0,0,0,0) # Opsiyonel: Motoru serbest bırakmak için tüm pinleri LOW yap
    
    time.sleep(STEP_MOTOR_SETTLE_TIME) # Adım grubu tamamlandıktan sonra motorun yerleşmesi için bekle

def move_motor_to_angle(target_angle_deg):
    """Motoru mevcut açısından hedef açıya taşır."""
    global current_motor_angle_global
    
    # Hedef açı ile mevcut açı arasındaki farkı hesapla
    angle_diff_deg = target_angle_deg - current_motor_angle_global
    
    # Eğer fark çok küçükse (bir adımın yarısından az), hareket etme
    if abs(angle_diff_deg) < (DEG_PER_STEP / 2.0):
        return

    # Atılması gereken adım sayısını hesapla
    num_steps_to_move = round(abs(angle_diff_deg) / DEG_PER_STEP)
    
    if num_steps_to_move == 0:
        return

    # Hareket yönünü belirle
    # Eğer target_angle > current_motor_angle_global ise, pozitif yönde (açı artışı) hareket etmeli.
    # Bu genellikle saat yönünün tersi (CCW) olarak kabul edilir, ancak motor bağlantısına göre değişebilir.
    # Bizim `step_sequence` artan index ile pozitif açı artışı sağlıyorsa, `direction_clockwise`
    # burada `angle_diff_deg > 0` ile doğru eşleşir.
    direction_positive_angle_change = (angle_diff_deg > 0) 
                                                
    print(f"[{os.getpid()}] Motor {current_motor_angle_global:.2f}°'den {target_angle_deg:.2f}°'ye hareket ediyor ({num_steps_to_move} adım, Yön: {'Açı Artışı (+)' if direction_positive_angle_change else 'Açı Azalışı (-)'}).")
    _step_motor_4in(num_steps_to_move, direction_positive_angle_change)
    
    # Gerçekleşen açıyı, atılan adım sayısına göre güncelle
    actual_angle_moved_this_step = num_steps_to_move * DEG_PER_STEP * (1 if direction_positive_angle_change else -1)
    current_motor_angle_global += actual_angle_moved_this_step
    
    # Çok küçük yuvarlama farkları varsa, hedef açıya eşitle
    if abs(current_motor_angle_global - target_angle_deg) < DEG_PER_STEP:
        current_motor_angle_global = float(target_angle_deg)
    # print(f"[{os.getpid()}] Motor yeni pozisyonu: {current_motor_angle_global:.2f}°")


# ==============================================================================
# --- Veritabanı, Kilit ve Diğer Yardımcı Fonksiyonlar ---
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
        existing_pid = 'Bilinmiyor'; # ... (hata mesajı aynı kalır)
        print(f"[{pid}] UYARI: Kilit dosyası mevcut. Betik zaten çalışıyor olabilir (PID: {existing_pid}). Çıkılıyor.")
        if lock_file_handle: lock_file_handle.close(); lock_file_handle = None
        return False
    except PermissionError as e: # ... (hata mesajı aynı kalır)
        print(f"[{pid}] KRİTİK İZİN HATASI: '{e.filename}' oluşturulamıyor. 'sudo' ile deneyin veya eski dosyaları silin.")
        if lock_file_handle: lock_file_handle.close(); lock_file_handle = None
        return False
    except Exception as e: # ... (hata mesajı aynı kalır)
        print(f"[{pid}] Kilit/PID alınırken beklenmedik hata: {e}")
        if lock_file_handle: lock_file_handle.close(); lock_file_handle = None
        return False

def shoelace_formula(noktalar):
    """Bir poligonun alanını Shoelace formülü ile hesaplar."""
    n = len(noktalar); area = 0.0
    if n < 3: return 0.0
    for i in range(n): area += (noktalar[i][0] * noktalar[(i + 1) % n][1]) - (noktalar[(i + 1) % n][0] * noktalar[i][1])
    return abs(area) / 2.0

def calculate_perimeter(cartesian_points):
    """Verilen kartezyen noktalardan oluşan bir sektörün çevresini hesaplar."""
    perimeter, n = 0.0, len(cartesian_points)
    if n == 0: return 0.0
    perimeter += math.sqrt(cartesian_points[0][0]**2 + cartesian_points[0][1]**2) # (0,0)'dan ilk noktaya
    for i in range(n - 1): perimeter += math.sqrt((cartesian_points[i+1][0] - cartesian_points[i][0])**2 + (cartesian_points[i+1][1] - cartesian_points[i][1])**2)
    perimeter += math.sqrt(cartesian_points[-1][0]**2 + cartesian_points[-1][1]**2) # Son noktadan (0,0)'a
    return perimeter

def release_resources_on_exit():
    """Betik sonlandığında çağrılacak temizleme fonksiyonu."""
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, yellow_led, lcd, buzzer
    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Çıkış durumu: {script_exit_status_global}")
    
    if db_conn_main_script_global:
        try: db_conn_main_script_global.close(); db_conn_main_script_global = None
        except: pass
    
    if current_scan_id_global: # Veritabanı durumunu güncelle
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
    try: # Step motor pinlerini serbest bırak (enerjiyi kes)
        _set_step_pins(0,0,0,0)
        print(f"[{pid}] Step motor pinleri LOW durumuna getirildi (enerji kesildi).")
    except Exception as e: print(f"[{pid}] Step motor pinleri sıfırlanırken hata: {e}")
    
    try: # Tüm GPIO pinlerini temizle
        GPIO.cleanup()
        print(f"[{pid}] GPIO pinleri temizlendi.")
    except Exception as e: print(f"[{pid}] GPIO temizlenirken hata: {e}")

    if yellow_led and hasattr(yellow_led, 'close'):
        if hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        yellow_led.close()
    if buzzer and hasattr(buzzer, 'close'):
        if hasattr(buzzer, 'is_active') and buzzer.is_active: buzzer.off()
        buzzer.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    if lcd:
        try:
            lcd.clear(); lcd.cursor_pos = (0,0); lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1); lcd.clear()
            lcd.cursor_pos = (0,0); lcd.write_string("M.Erdem OZER".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1,0); lcd.write_string("(PhD.)".ljust(LCD_COLS)[:LCD_COLS])
        except: pass

    if lock_file_handle: # Kilit dosyasını serbest bırak
        try: fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close(); lock_file_handle = None
        except: pass
    
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]: # PID ve Kilit dosyalarını sil
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_del = False; # ... (PID dosyası silme mantığı aynı)
                    try:
                        with open(f_path, 'r') as pf_c:
                            if int(pf_c.read().strip()) == pid: can_del = True
                    except: pass
                    if can_del: os.remove(f_path)
                elif f_path == LOCK_FILE_PATH: os.remove(f_path) # Kilit dosyasını her zaman sil
        except: pass
    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")

# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motorlu 2D Alan Tarama Betiği")
    parser.add_argument("--start_angle", type=float, default=DEFAULT_SCAN_START_ANGLE, help="Tarama başlangıç açısı (derece)")
    parser.add_argument("--end_angle", type=float, default=DEFAULT_SCAN_END_ANGLE, help="Tarama bitiş açısı (derece)")
    parser.add_argument("--step_angle", type=float, default=DEFAULT_SCAN_STEP_ANGLE, help="Tarama adım açısı (derece)")
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE, help="Buzzer uyarı mesafesi (cm)")
    args = parser.parse_args()

    SCAN_START_ANGLE, SCAN_END_ANGLE, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM = \
        float(args.start_angle), float(args.end_angle), float(args.step_angle), int(args.buzzer_distance)
    
    if SCAN_STEP_ANGLE <= 0:
        print("UYARI: Adım açısı pozitif olmalıdır. Varsayılan 1 derece kullanılıyor.")
        SCAN_STEP_ANGLE = 1.0

    atexit.register(release_resources_on_exit) # Çıkışta kaynakları serbest bırak

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1) # Bu artık 4-girişli step motoru başlatır
    init_db_for_scan()
    if not current_scan_id_global:
        print(f"[{os.getpid()}] KRİTİK HATA: Veritabanında tarama ID'si oluşturulamadı. Çıkılıyor.")
        sys.exit(1)

    ölçüm_tamponu_hız_için_yerel = [] # Hız hesaplaması için (şu an kullanılmıyor ama kalabilir)
    collected_cartesian_points_for_area = [] # Alan/çevre hesabı için (x,y) noktaları
    pid = os.getpid()

    print(f"[{pid}] Step Motor ile 2D Tarama Başlıyor (Tarama ID: {current_scan_id_global})...")
    if lcd:
        lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string(f"ScanID:{current_scan_id_global} Step".ljust(LCD_COLS)[:LCD_COLS])
        if LCD_ROWS > 1: lcd.cursor_pos=(1,0); lcd.write_string(f"A:{SCAN_START_ANGLE:.0f}-{SCAN_END_ANGLE:.0f} S:{SCAN_STEP_ANGLE:.0f}".ljust(LCD_COLS)[:LCD_COLS])

    scan_completed_successfully = False
    lcd_warning_mode = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        # Tarama yönünü ve adım sayısını belirle
        # `current_motor_angle_global` zaten `init_hardware` içinde `SCAN_START_ANGLE`'e ayarlandı.
        target_loop_angle = float(SCAN_START_ANGLE)

        while True:
            loop_iteration_start_time = time.time()
            
            # Motoru hedef açıya hareket ettir
            move_motor_to_angle(target_loop_angle)
            # Gerçekleşen açı, ölçüm için current_motor_angle_global'dan alınır
            current_effective_degree_for_scan = current_motor_angle_global

            if yellow_led: yellow_led.toggle() # Çalıştığını gösteren LED
            
            # Sensör okuması
            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            # Kartezyen koordinatları hesapla
            angle_rad = math.radians(current_effective_degree_for_scan)
            x_cm = distance_cm * math.cos(angle_rad)
            y_cm = distance_cm * math.sin(angle_rad)

            # Alan hesabı için geçerli noktaları topla
            if 0 < distance_cm < (sensor.max_distance * 100 - 1): # max_distance'dan biraz küçük
                collected_cartesian_points_for_area.append((x_cm, y_cm))

            # Hız hesaplaması (basit, isteğe bağlı)
            hiz_cm_s = 0.0
            # if ölçüm_tamponu_hız_için_yerel: ... (hız hesaplama mantığı buraya gelebilir)

            # Buzzer kontrolü
            is_object_close = distance_cm <= BUZZER_DISTANCE_CM
            if buzzer:
                if is_object_close and not buzzer.is_active: buzzer.on()
                elif not is_object_close and buzzer.is_active: buzzer.off()

            # LCD'ye anlık bilgileri yazdır
            if lcd:
                try:
                    # ... (LCD yazdırma mantığı, `derece` yerine `current_effective_degree_for_scan` kullanılır)
                    # ... (Önceki versiyonla aynı, sadece açı değişkeni farklı)
                    if is_object_close and not lcd_warning_mode:
                        lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("!!! UYARI !!!".center(LCD_COLS)); lcd_warning_mode=True
                        if LCD_ROWS > 1: lcd.cursor_pos=(1,0); lcd.write_string("NESNE YAKIN!".center(LCD_COLS))
                    elif not is_object_close and lcd_warning_mode:
                        lcd.clear(); lcd_warning_mode=False # Normal moda dön, ekranı temizle ve yaz
                    if not lcd_warning_mode: # Sadece normal modda yaz
                        lcd.cursor_pos=(0,0); lcd.write_string(f"A:{current_effective_degree_for_scan:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: lcd.cursor_pos=(1,0); lcd.write_string(f"X{x_cm:3.0f}Y{y_cm:3.0f} H{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
                except Exception as e_lcd_write: print(f"[{pid}] UYARI: LCD yazma hatası: {e_lcd_write}")

            # Çok yakın nesne algılanırsa taramayı sonlandır
            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{pid}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Tarama sonlandırılıyor.")
                if lcd: lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                if yellow_led: yellow_led.on()
                script_exit_status_global = 'terminated_close_object'
                time.sleep(1.0); break # Döngüden çık

            # Veritabanına ölçüm noktasını kaydet
            try:
                cursor_main.execute('''INSERT INTO scan_points (scan_id, derece, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                                    (current_scan_id_global, current_effective_degree_for_scan, distance_cm, hiz_cm_s, current_timestamp, x_cm, y_cm))
                db_conn_main_script_global.commit()
            except Exception as e_db_insert: print(f"[{pid}] HATA: DB Ekleme Hatası: {e_db_insert}")
            
            # Bir sonraki hedef açıyı belirle ve döngü sonlandırma kontrolü
            # Tarama yönüne göre ilerle
            if SCAN_END_ANGLE >= SCAN_START_ANGLE: # İleri tarama (açı artıyor)
                if target_loop_angle >= SCAN_END_ANGLE: break # Bitiş açısına ulaşıldı veya geçildi
                target_loop_angle += SCAN_STEP_ANGLE
                if target_loop_angle > SCAN_END_ANGLE: target_loop_angle = float(SCAN_END_ANGLE) # Son açıyı aşma
            else: # Geri tarama (açı azalıyor)
                if target_loop_angle <= SCAN_END_ANGLE: break # Bitiş açısına ulaşıldı veya geçildi
                target_loop_angle -= SCAN_STEP_ANGLE
                if target_loop_angle < SCAN_END_ANGLE: target_loop_angle = float(SCAN_END_ANGLE) # Son açıyı aşma
            
            # Döngü süresini hedef aralıkta tutmak için bekleme
            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0 : time.sleep(sleep_duration)
        
        # Döngü bittikten sonra (break ile çıkıldıktan sonra) analiz yap
        hesaplanan_alan_cm2, perimeter_cm, max_genislik_cm_scan, max_derinlik_cm_scan = 0.0, 0.0, 0.0, 0.0
        if len(collected_cartesian_points_for_area) >= 2:
            polygon_for_area = [(0.0,0.0)] + collected_cartesian_points_for_area
            hesaplanan_alan_cm2 = shoelace_formula(polygon_for_area)
            perimeter_cm = calculate_perimeter(collected_cartesian_points_for_area)
            # ... (max genişlik/derinlik hesaplama aynı kalır) ...
            x_coords = [p[0] for p in collected_cartesian_points_for_area if p[0] is not None]
            y_coords = [p[1] for p in collected_cartesian_points_for_area if p[1] is not None]
            if x_coords: max_derinlik_cm_scan = max(x_coords) if x_coords else 0.0
            if y_coords: max_genislik_cm_scan = (max(y_coords) - min(y_coords)) if y_coords else 0.0

            print(f"\n[{pid}] TARAMA TAMAMLANDI. Analiz sonuçları:") # ... (analiz sonuçları yazdırma)
            if lcd: lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("Tarama Tamamlandi".ljust(LCD_COLS)[:LCD_COLS]); # ...
            script_exit_status_global = 'completed_analysis'
            try:
                cursor_main.execute("UPDATE servo_scans SET hesaplanan_alan_cm2=?,cevre_cm=?,max_genislik_cm=?,max_derinlik_cm=?,status=? WHERE id=?",
                                    (hesaplanan_alan_cm2, perimeter_cm, max_genislik_cm_scan, max_derinlik_cm_scan, script_exit_status_global, current_scan_id_global))
                db_conn_main_script_global.commit()
            except Exception as e_db_upd: print(f"[{pid}] HATA: DB Analiz Güncelleme: {e_db_upd}")
        else:
            script_exit_status_global = 'completed_insufficient_points'
            # ... (yetersiz nokta mesajları ve DB güncelleme) ...
        scan_completed_successfully = True

    except KeyboardInterrupt:
        script_exit_status_global = 'interrupted_ctrl_c'; print(f"\n[{pid}] Tarama kullanıcı tarafından (Ctrl+C) kesildi.")
        if lcd: lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS])
    except Exception as e:
        script_exit_status_global = 'error_in_loop'; print(f"[{pid}] KRİTİK HATA: Ana döngüde: {e}")
        import traceback; traceback.print_exc()
        if lcd: lcd.clear(); lcd.cursor_pos=(0,0); lcd.write_string("HATA OLUSTU!".ljust(LCD_COLS)[:LCD_COLS])
    finally:
        if not scan_completed_successfully and script_exit_status_global not in ['interrupted_ctrl_c', 'error_in_loop', 'terminated_close_object']:
             script_exit_status_global = 'interrupted_unexpectedly_in_main'
        if buzzer and buzzer.is_active: buzzer.off()
        if yellow_led and yellow_led.is_active: yellow_led.off()
        print(f"[{pid}] Ana betik sonlanıyor. Çıkış durumu: {script_exit_status_global}")
        # atexit fonksiyonu geri kalan temizliği yapacak.
