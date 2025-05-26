# sensor_script.py
from gpiozero import AngularServo, DistanceSensor, LED
import time
import sqlite3
import os
import sys
import fcntl  # Dosya kilitleme için
import atexit # Temiz çıkış işlemleri için

# --- Pin Tanımlamaları ---
TRIG_PIN = 23
ECHO_PIN = 24
SERVO_PIN = 12  # Servo motorunuzu bağladığınız GPIO pini (KENDİNİZE GÖRE DEĞİŞTİRİN!)

RED_LED_PIN = 17
GREEN_LED_PIN = 18
YELLOW_LED_PIN = 27

# --- Eşik Değerleri ve Sabitler ---
OBJECT_THRESHOLD_CM = 20.0
YELLOW_LED_THRESHOLD_CM = 100.0
TERMINATION_DISTANCE_CM = 10.0

SCAN_START_ANGLE = 0
SCAN_END_ANGLE = 180
SCAN_STEP_ANGLE = 10
SERVO_SETTLE_TIME = 0.3
LOOP_TARGET_INTERVAL_S = 0.5 # Her bir açı adımının yaklaşık süresi (servo hareketi + işlemler)

# Proje ana dizinini al (bu betik ana dizinde ise)
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'db.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)

LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

# --- Global Donanım ve Durum Değişkenleri ---
sensor = None
red_led = None
green_led = None
yellow_led = None
servo = None
lock_file_handle = None
current_scan_id_global = None
db_conn_main_script_global = None
script_exit_status_global = 'interrupted_unexpectedly' # atexit için varsayılan durum

def init_hardware():
    global sensor, red_led, green_led, yellow_led, servo
    try:
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        red_led = LED(RED_LED_PIN)
        green_led = LED(GREEN_LED_PIN)
        yellow_led = LED(YELLOW_LED_PIN)
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             min_pulse_width=0.0005, max_pulse_width=0.0025) # SG90 için tipik
        print(f"[{os.getpid()}] Donanım başarıyla başlatıldı.")
        red_led.off(); green_led.off(); yellow_led.off()
        servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"[{os.getpid()}] Donanım başlatma hatası: {e}")
        return False

def init_db_for_scan():
    global current_scan_id_global
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS servo_scans(
                id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL UNIQUE, status TEXT)''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, angle_deg REAL, 
                mesafe_cm REAL, hiz_cm_s REAL, timestamp REAL, 
                FOREIGN KEY(scan_id) REFERENCES servo_scans(id))''')

        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")

        scan_start_time = time.time()
        cursor.execute("INSERT INTO servo_scans (start_time, status) VALUES (?, ?)", (scan_start_time, 'running'))
        current_scan_id_global = cursor.lastrowid
        conn.commit()
        print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {current_scan_id_global}")
    except sqlite3.Error as e_db_init:
        print(f"[{os.getpid()}] Veritabanı başlatma/tarama kaydı oluşturma hatası: {e_db_init}")
        current_scan_id_global = None
    finally:
        if conn:
            conn.close()

def acquire_lock_and_pid():
    global lock_file_handle
    try:
        # Kilit dosyasını 'w' modunda açmak, varsa içeriğini siler ve özel kilit için uygun hale getirir.
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Betik kilidi ({LOCK_FILE_PATH}) ve PID ({PID_FILE_PATH}) başarıyla oluşturuldu.")
        return True
    except BlockingIOError: # Başka bir process kilit tutuyorsa
        print(f"[{os.getpid()}] '{LOCK_FILE_PATH}' kilitli. Sensör betiği zaten çalışıyor olabilir.")
        if lock_file_handle: lock_file_handle.close() # Dosya açıldıysa ama kilit alınamadıysa kapat
        lock_file_handle = None
        return False
    except Exception as e: # Diğer hatalar (örn: izin hatası)
        print(f"[{os.getpid()}] Kilit/PID alınırken beklenmedik bir hata: {e}")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False

def release_resources_on_exit():
    """Program sonlandığında çağrılacak temizleme fonksiyonu."""
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, red_led, green_led, yellow_led, servo

    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Çıkış durumu: {script_exit_status_global}")

    if db_conn_main_script_global:
        try:
            db_conn_main_script_global.close()
            print(f"[{pid}] Ana veritabanı bağlantısı kapatıldı.")
        except Exception as e_db_close: print(f"[{pid}] Ana DB bağlantısı kapatılırken hata: {e_db_close}")

    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ? AND status = 'running'",
                                (script_exit_status_global, current_scan_id_global))
            conn_exit.commit()
            print(f"[{pid}] Tarama ID {current_scan_id_global} durumu '{script_exit_status_global}' olarak güncellendi.")
        except Exception as e_db_update_exit: print(f"[{pid}] Çıkışta tarama durumu güncellenirken DB hatası: {e_db_update_exit}")
        finally:
            if conn_exit: conn_exit.close()

    print(f"[{pid}] Donanım kapatılıyor...")
    if servo and hasattr(servo, 'detach'):
        try:
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2; time.sleep(0.2)
            servo.detach(); servo.close() # Önce detach sonra close
            print(f"[{pid}] Servo kapatıldı.")
        except Exception as e_servo: print(f"[{pid}] Servo kapatılırken hata: {e_servo}")

    for led_obj in [red_led, green_led, yellow_led]:
        if led_obj and hasattr(led_obj, 'close'):
            if hasattr(led_obj, 'is_active') and led_obj.is_active: led_obj.off()
            led_obj.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    print(f"[{pid}] LED'ler ve sensör kapatıldı.")

    if lock_file_handle: # Sadece bu process'in aldığı kilitse bırakılır
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close() # Dosyayı kapatmak önemli
            print(f"[{pid}] Kilit ({LOCK_FILE_PATH}) serbest bırakıldı.")
        except Exception as e_lock: print(f"[{pid}] Kilit serbest bırakılırken hata: {e_lock}")

    # PID dosyasını ve kilit dosyasını (artık handle kapalı olduğu için) sil
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                # PID dosyasını sadece bu process oluşturduysa sil
                if f_path == PID_FILE_PATH:
                    try:
                        with open(f_path, 'r') as pf_check:
                            if int(pf_check.read().strip()) == pid:
                                os.remove(f_path)
                                print(f"[{pid}] Dosya silindi: {f_path}")
                            else:
                                print(f"[{pid}] PID dosyası ({f_path}) başka bir processe ait, silinmedi.")
                    except: # Okuma hatası vs. olursa da silmeyi dene (veya bırak)
                        os.remove(f_path)
                        print(f"[{pid}] PID dosyası ({f_path}) okunamadı ama silindi.")
                elif f_path == LOCK_FILE_PATH: # Kilit dosyası her zaman silinir
                     os.remove(f_path)
                     print(f"[{pid}] Dosya silindi: {f_path}")
        except OSError as e_rm: print(f"[{pid}] Dosya ({f_path}) silinirken hata: {e_rm}")

    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")

if __name__ == "__main__":
    atexit.register(release_resources_on_exit) # Varsayılan status ile kaydet

    if not acquire_lock_and_pid():
        sys.exit(1)

    if not init_hardware():
        sys.exit(1)

    init_db_for_scan()
    if not current_scan_id_global:
        print(f"[{os.getpid()}] HATA: Tarama ID'si oluşturulamadı. Çıkılıyor.")
        sys.exit(1)

    ölçüm_tamponu_hız_için_yerel = []
    ornek_sayaci_yerel = 0

    print(f"[{os.getpid()}] Servo ile 2D Tarama Başlıyor (Tarama ID: {current_scan_id_global})...")

    scan_completed_flag = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        servo.angle = SCAN_START_ANGLE
        print(f"[{os.getpid()}] Servo başlangıç açısına ({SCAN_START_ANGLE}°) getirildi...")
        time.sleep(1.0)

        for angle_deg in range(SCAN_START_ANGLE, SCAN_END_ANGLE + SCAN_STEP_ANGLE, SCAN_STEP_ANGLE):
            loop_iteration_start_time = time.time()

            servo.angle = angle_deg
            time.sleep(SERVO_SETTLE_TIME)

            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            hiz_cm_s = 0.0
            if ölçüm_tamponu_hız_için_yerel:
                son_veri_noktasi = ölçüm_tamponu_hız_için_yerel[-1]
                delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
                delta_zaman = current_timestamp - son_veri_noktasi['zaman_s']
                if delta_zaman > 0.001:
                    hiz_cm_s = delta_mesafe / delta_zaman

            # print(f"  Açı: {angle_deg:3d}°, Mesafe: {distance_cm:6.2f} cm, Hız: {hiz_cm_s:.2f} cm/s") # Konsolu çok doldurabilir

            try:
                cursor_main.execute('''
                    INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                ''', (current_scan_id_global, angle_deg, distance_cm, hiz_cm_s, current_timestamp))
                db_conn_main_script_global.commit()
            except Exception as e_db_insert:
                print(f"[{os.getpid()}] DB Ekleme Hatası: {e_db_insert}")

            ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]
            ornek_sayaci_yerel += 1

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{os.getpid()}] DİKKAT: Nesne çok yakın ({distance_cm:.2f}cm)! Tarama durduruluyor.")
                script_exit_status_global = 'terminated_close_object'
                break # Döngüden çık, finally bloğu ve atexit çalışacak

            if distance_cm > YELLOW_LED_THRESHOLD_CM: yellow_led.on()
            else: yellow_led.toggle()
            max_distance_cm = sensor.max_distance * 100
            is_reading_valid = (distance_cm > 0.0) and (distance_cm < max_distance_cm)
            if is_reading_valid:
                if distance_cm <= OBJECT_THRESHOLD_CM: red_led.on(); green_led.off()
                else: green_led.on(); red_led.off()
            else:
                red_led.off(); green_led.off()

            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0 and (angle_deg < SCAN_END_ANGLE):
                 time.sleep(sleep_duration)
        else: # Döngü normal biterse (break ile çıkılmazsa)
            scan_completed_flag = True
            script_exit_status_global = 'completed'
            print(f"[{os.getpid()}] Tarama normal şekilde tamamlandı.")

    except KeyboardInterrupt:
        print(f"\n[{os.getpid()}] Tarama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
        script_exit_status_global = 'interrupted_ctrl_c'
    except Exception as e_main_loop:
        print(f"[{os.getpid()}] Tarama sırasında ana döngüde beklenmedik bir hata: {e_main_loop}")
        script_exit_status_global = 'error_in_loop'
    # finally bloğu artık atexit tarafından yönetildiği için burada özel bir şey yapmaya gerek yok.
    # atexit, script_exit_status_global'ın son değerini kullanarak DB'yi güncelleyecek.