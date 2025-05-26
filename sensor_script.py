# sensor_script.py
from gpiozero import AngularServo, DistanceSensor, LED
import time
import sqlite3
import os
import sys
import fcntl  # Dosya kilitleme için (Linux/Unix tabanlı sistemlerde)
import atexit  # Temiz çıkış işlemleri için

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
SCAN_STEP_ANGLE = 10  # Daha hassas tarama için 5 yapabilirsiniz
SERVO_SETTLE_TIME = 0.3  # Servonun pozisyonuna gelmesi ve sensörün sabitlenmesi için
# DELTA_T_SECONDS, ana döngüdeki işlemlerin süresi ve bu sleep ile ayarlanır.
# Her bir tarama adımı arasındaki toplam süreyi hedefler.
# Örneğin, servo hareketi + sensör okuma + DB yazma + LED işlemleri zaten bir miktar zaman alır.
# Kalan süreyi beklemek için kullanılabilir veya sadece servo settle time yeterli olabilir.
# Şimdilik bunu döngü içinde ayarlayacağız.
LOOP_TARGET_INTERVAL_S = 0.5  # Her bir açı adımının yaklaşık ne kadar süreceği (servo hareketi dahil)

DB_NAME = 'db.sqlite3'
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

# --- Global Donanım Değişkenleri ---
sensor = None
red_led = None
green_led = None
yellow_led = None
servo = None
lock_file_handle = None  # Kilit dosyasının handle'ı
current_scan_id_global = None  # Veritabanındaki mevcut taramanın ID'si (atexit için global)
db_conn_main_script_global = None  # Ana döngüdeki DB bağlantısı (atexit için global)


def init_hardware():
    global sensor, red_led, green_led, yellow_led, servo
    try:
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        red_led = LED(RED_LED_PIN)
        green_led = LED(GREEN_LED_PIN)
        yellow_led = LED(YELLOW_LED_PIN)
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             min_pulse_width=0.0005, max_pulse_width=0.0025)  # SG90 için tipik
        print(f"[{os.getpid()}] Donanım başarıyla başlatıldı.")
        red_led.off();
        green_led.off();
        yellow_led.off()
        servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2  # Başlangıçta ortaya al
        time.sleep(0.5)  # Servonun yerine oturması için
        return True
    except Exception as e:
        print(f"[{os.getpid()}] Donanım başlatma hatası: {e}")
        return False


def init_db_for_scan():
    global current_scan_id_global  # Global değişkeni kullanacağımızı belirtelim
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
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
                           TEXT
                       )''')
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
                           FOREIGN
                           KEY
                       (
                           scan_id
                       ) REFERENCES servo_scans
                       (
                           id
                       ))''')

        cursor.execute("UPDATE servo_scans SET status = 'interrupted_on_new_start' WHERE status = 'running'")

        scan_start_time = time.time()
        cursor.execute("INSERT INTO servo_scans (start_time, status) VALUES (?, ?)", (scan_start_time, 'running'))
        current_scan_id_global = cursor.lastrowid  # Global değişkene ata
        conn.commit()
        print(f"[{os.getpid()}] Veritabanı '{DB_NAME}' hazırlandı. Yeni tarama ID: {current_scan_id_global}")
    except sqlite3.Error as e_db_init:
        print(f"[{os.getpid()}] Veritabanı başlatma/tarama kaydı oluşturma hatası: {e_db_init}")
        current_scan_id_global = None
    finally:
        if conn:
            conn.close()


def acquire_lock_and_pid():
    global lock_file_handle
    # Önce eski PID dosyasını (varsa) silmeyi deneyin, fcntl kilidi daha güvenilir
    try:
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError:
        pass

    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Betik kilidi ({LOCK_FILE_PATH}) ve PID ({PID_FILE_PATH}) başarıyla oluşturuldu.")
        return True
    except BlockingIOError:
        print(f"[{os.getpid()}] '{LOCK_FILE_PATH}' kilitli. Sensör betiği zaten çalışıyor olabilir.")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False
    except Exception as e:
        print(f"[{os.getpid()}] Kilit/PID alınırken beklenmedik bir hata: {e}")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False


def release_resources_on_exit(script_exit_status='completed_normally'):
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global
    global sensor, red_led, green_led, yellow_led, servo

    pid = os.getpid()
    print(f"[{pid}] Program sonlanıyor. Çıkış durumu: {script_exit_status}")

    if db_conn_main_script_global:
        try:
            db_conn_main_script_global.close()
            print(f"[{pid}] Ana veritabanı bağlantısı kapatıldı.")
        except Exception as e_db_close:
            print(f"[{pid}] Ana DB bağlantısı kapatılırken hata: {e_db_close}")

    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_NAME)
            cursor_exit = conn_exit.cursor()
            # Sadece 'running' durumundakini güncelle, zaten tamamlanmışsa elleme
            cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ? AND status = 'running'",
                                (script_exit_status, current_scan_id_global))
            conn_exit.commit()
            print(f"[{pid}] Tarama ID {current_scan_id_global} durumu '{script_exit_status}' olarak güncellendi.")
        except Exception as e_db_update_exit:
            print(f"[{pid}] Çıkışta tarama durumu güncellenirken DB hatası: {e_db_update_exit}")
        finally:
            if conn_exit: conn_exit.close()

    print(f"[{pid}] Donanım kapatılıyor...")
    if servo and hasattr(servo, 'detach'):  # Servo başlatılmışsa
        try:
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2;
            time.sleep(0.2)
            servo.detach();
            servo.close()
            print(f"[{pid}] Servo kapatıldı.")
        except Exception as e_servo:
            print(f"[{pid}] Servo kapatılırken hata: {e_servo}")

    if red_led and hasattr(red_led, 'close'): red_led.off(); red_led.close()
    if green_led and hasattr(green_led, 'close'): green_led.off(); green_led.close()
    if yellow_led and hasattr(yellow_led, 'close'): yellow_led.off(); yellow_led.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    print(f"[{pid}] LED'ler ve sensör kapatıldı.")

    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
        except Exception as e_lock:
            print(f"[{pid}] Kilit serbest bırakılırken hata: {e_lock}")

    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:  # Önce PID, sonra kilit dosyası
        try:
            if os.path.exists(f_path): os.remove(f_path); print(f"[{pid}] Dosya silindi: {f_path}")
        except OSError as e_rm:
            print(f"[{pid}] Dosya ({f_path}) silinirken hata: {e_rm}")

    print(f"[{pid}] Temizleme tamamlandı. Betik çıkıyor.")


if __name__ == "__main__":
    atexit.register(release_resources_on_exit, script_exit_status='script_terminated_unexpectedly')

    if not acquire_lock_and_pid():
        sys.exit(1)

    if not init_hardware():
        sys.exit(1)

    init_db_for_scan()
    if not current_scan_id_global:
        print(f"[{os.getpid()}] HATA: Tarama ID'si oluşturulamadı. Çıkılıyor.")
        sys.exit(1)

    # atexit handler'ını, tarama ID'si belli olduktan sonra daha anlamlı bir varsayılan status ile güncelle
    atexit.unregister(release_resources_on_exit)  # Önceki genel register'ı kaldır
    atexit.register(release_resources_on_exit, script_exit_status='interrupted_during_scan')  # Yeni varsayılan

    ölçüm_tamponu_hız_için_yerel = []
    ornek_sayaci_yerel = 0  # Yerel sayaç, global ornek_sayaci ile karışmaması için

    print(f"[{os.getpid()}] Servo ile 2D Tarama Başlıyor (Tarama ID: {current_scan_id_global})...")
    print(f"Açı Aralığı: {SCAN_START_ANGLE}° - {SCAN_END_ANGLE}° (Adım: {SCAN_STEP_ANGLE}°)")

    scan_completed_successfully = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_NAME, timeout=10)
        cursor_main = db_conn_main_script_global.cursor()

        servo.angle = SCAN_START_ANGLE
        print(f"[{os.getpid()}] Servo başlangıç açısına ({SCAN_START_ANGLE}°) getirildi...")
        time.sleep(1.0)  # Servonun yerine tam oturması için

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
                if delta_zaman > 0.001:  # Çok küçük zaman farkları için sıfıra bölmeyi engelle
                    hiz_cm_s = delta_mesafe / delta_zaman

            print(f"  Açı: {angle_deg:3d}°, Mesafe: {distance_cm:6.2f} cm, Hız: {hiz_cm_s:.2f} cm/s")

            try:
                cursor_main.execute('''
                                    INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp)
                                    VALUES (?, ?, ?, ?, ?)
                                    ''', (current_scan_id_global, angle_deg, distance_cm, hiz_cm_s, current_timestamp))
                db_conn_main_script_global.commit()
            except Exception as e_db_insert:
                print(f"[{os.getpid()}] DB Ekleme Hatası: {e_db_insert}")

            ölçüm_tamponu_hız_için_yerel.append({
                'mesafe_cm': distance_cm,
                'zaman_s': current_timestamp
            })
            if len(ölçüm_tamponu_hız_için_yerel) > 1:
                ölçüm_tamponu_hız_için_yerel.pop(0)

            ornek_sayaci_yerel += 1

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{os.getpid()}] DİKKAT: Nesne çok yakın ({distance_cm:.2f}cm)! Tarama acil durduruluyor.")
                atexit.unregister(release_resources_on_exit)
                release_resources_on_exit(script_exit_status='terminated_close_object')
                sys.exit(1)

            if distance_cm > YELLOW_LED_THRESHOLD_CM:
                yellow_led.on()
            else:
                yellow_led.toggle()
            max_distance_cm = sensor.max_distance * 100
            is_reading_valid = (distance_cm > 0.0) and (distance_cm < max_distance_cm)
            if is_reading_valid:
                if distance_cm <= OBJECT_THRESHOLD_CM:
                    red_led.on(); green_led.off()
                else:
                    green_led.on(); red_led.off()
            else:
                red_led.off();
                green_led.off()

            loop_processing_time = time.time() - loop_iteration_start_time
            # Her adımın toplamda LOOP_TARGET_INTERVAL_S sürmesini hedefle
            # Zaten SERVO_SETTLE_TIME kadar beklendi, kalanını bekle
            additional_sleep = LOOP_TARGET_INTERVAL_S - SERVO_SETTLE_TIME - loop_processing_time
            if additional_sleep > 0 and (angle_deg < SCAN_END_ANGLE):
                time.sleep(additional_sleep)

        scan_completed_successfully = True  # Tarama normal bitti
        print(f"[{os.getpid()}] Tarama normal şekilde tamamlandı.")

    except KeyboardInterrupt:
        print(f"\n[{os.getpid()}] Tarama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
        atexit.unregister(release_resources_on_exit)
        release_resources_on_exit(script_exit_status='interrupted_ctrl_c')
    except Exception as e_main_loop:
        print(f"[{os.getpid()}] Tarama sırasında ana döngüde beklenmedik bir hata: {e_main_loop}")
        atexit.unregister(release_resources_on_exit)
        release_resources_on_exit(script_exit_status=f'error_in_loop')
    finally:
        if scan_completed_successfully:
            atexit.unregister(release_resources_on_exit)  # atexit'i son bir kez daha çağırıp status'u 'completed' yap
            release_resources_on_exit(script_exit_status='completed')
        # Diğer durumlarda (KeyboardInterrupt, Exception), atexit zaten uygun status ile çağrılmış olacak.
        # release_resources_on_exit() zaten atexit tarafından çağrılacak, burada tekrar çağırmaya gerek yok
        # ancak db_conn_main_script_global burada açık kalmış olabilir, bu yüzden atexit'e güveniyoruz.
        print(f"[{os.getpid()}] Ana `finally` bloğu çalıştı, atexit temizliği devralacak.")