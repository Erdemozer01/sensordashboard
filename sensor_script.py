# sensor_script.py
from gpiozero import AngularServo, DistanceSensor, LED
from RPLCD.i2c import CharLCD  # LCD için eklendi
import time
import sqlite3
import os
import sys
import fcntl
import atexit

# --- Pin Tanımlamaları ---
TRIG_PIN = 23
ECHO_PIN = 24
SERVO_PIN = 12

RED_LED_PIN = 17
GREEN_LED_PIN = 18
YELLOW_LED_PIN = 27

# --- LCD Ayarları ---
LCD_I2C_ADDRESS = 0x27  # `sudo i2cdetect -y 1` komutuyla bulduğunuz adres
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16  # LCD sütun sayısı (16x2 için 16, 20x4 için 20)
LCD_ROWS = 2  # LCD satır sayısı (16x2 için 2, 20x4 için 4)
I2C_PORT = 1

# --- Eşik Değerleri ---
OBJECT_THRESHOLD_CM = 20.0
YELLOW_LED_THRESHOLD_CM = 100.0
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
red_led = None
green_led = None
yellow_led = None
servo = None
lcd = None
lock_file_handle = None
current_scan_id_global = None
db_conn_main_script_global = None
script_exit_status_global = 'interrupted_unexpectedly'


def init_hardware():
    global sensor, red_led, green_led, yellow_led, servo, lcd
    hardware_ok = True
    try:
        print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        red_led = LED(RED_LED_PIN)
        green_led = LED(GREEN_LED_PIN)
        yellow_led = LED(YELLOW_LED_PIN)
        # Servo için min_pulse_width ve max_pulse_width değerleri SG90 için genellikle uygundur.
        # Farklı servo modelleri veya klonlar için bu değerleri ayarlamanız gerekebilir.
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             initial_angle=None,  # Başlangıçta belirli bir açıya gitmesin, önce ortaya alacağız
                             min_pulse_width=0.0005, max_pulse_width=0.0025)

        red_led.off();
        green_led.off();
        yellow_led.off()

        # Servoyu ortaya al ve bekle
        target_center_angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
        if servo:  # Servo None değilse
            servo.angle = target_center_angle
            print(f"[{os.getpid()}] Servo ({target_center_angle}°) ortaya alındı.")
            time.sleep(0.7)  # Servonun ortaya gelmesi için yeterli süre

        print(f"[{os.getpid()}] Temel donanımlar başarıyla başlatıldı.")
    except Exception as e:
        print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}")
        hardware_ok = False

    # LCD Başlatma (Temel donanımlar başlarsa dene)
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02',
                          auto_linebreaks=False)
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Merhaba")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Dream Pi Hazir!")
            time.sleep(3)
            print(f"[{os.getpid()}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
        except Exception as e_lcd_init:
            print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}. LCD olmadan devam edilecek.")
            lcd = None  # LCD başlatılamazsa None olarak kalsın
    else:
        lcd = None  # Temel donanım başlamadıysa LCD de None

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

        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'Calisiyor'")

        scan_start_time = time.time()
        cursor.execute("INSERT INTO servo_scans (start_time, status) VALUES (?, ?)", (scan_start_time, 'Calisiyor'))
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
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)  # Önceki PID'yi temizle
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


def release_resources_on_exit():
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, red_led, green_led, yellow_led, servo, lcd

    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Betik çıkış durumu: {script_exit_status_global}")

    if db_conn_main_script_global:
        try:
            db_conn_main_script_global.close()
            print(f"[{pid}] Ana veritabanı bağlantısı kapatıldı.")
        except Exception as e_db_close:
            print(f"[{pid}] Ana DB bağlantısı kapatılırken hata: {e_db_close}")

    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            # Sadece 'running' durumundaysa güncelle, zaten farklı bir status ayarlandıysa (örn: terminated_close_object) üzerine yazma.
            cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (current_scan_id_global,))
            current_db_status_row = cursor_exit.fetchone()
            if current_db_status_row and current_db_status_row[0] == 'running':
                cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?",
                                    (script_exit_status_global, current_scan_id_global))
                conn_exit.commit()
                print(
                    f"[{pid}] Tarama ID {current_scan_id_global} durumu '{script_exit_status_global}' olarak güncellendi.")
            elif current_db_status_row:
                print(
                    f"[{pid}] Tarama ID {current_scan_id_global} durumu zaten '{current_db_status_row[0]}', tekrar güncellenmedi.")
            else:
                print(f"[{pid}] Tarama ID {current_scan_id_global} için durum bulunamadı.")
        except Exception as e_db_update_exit:
            print(f"[{pid}] Çıkışta tarama durumu güncellenirken DB hatası: {e_db_update_exit}")
        finally:
            if conn_exit:
                conn_exit.close()

    print(f"[{pid}] Donanım kapatılıyor...")
    if servo:  # servo None değilse
        try:
            print(f"[{pid}] Servo ortaya alınıyor...")
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
            time.sleep(0.5)  # Servonun pozisyona gelmesi için daha uzun bekleme
            servo.detach()
            servo.close()
            print(f"[{pid}] Servo kapatıldı.")
        except Exception as e_servo:
            print(f"[{pid}] Servo kapatılırken hata: {e_servo}")
    else:
        print(f"[{pid}] Servo nesnesi bulunamadı/başlatılamadı, kapatma işlemi atlandı.")

    for led_obj in [red_led, green_led, yellow_led]:
        if led_obj:  # led_obj None değilse
            if hasattr(led_obj, 'is_active') and led_obj.is_active:
                led_obj.off()
            if hasattr(led_obj, 'close'):
                led_obj.close()

    if sensor and hasattr(sensor, 'close'):
        sensor.close()

    if lcd:
        try:
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Dream Pi")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Taramayi yapti")
            time.sleep(3)
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Mehmet Erdem")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("OZER")
            print(f"[{pid}] LCD temizlendi ve mesaj yazıldı.")
        except Exception as e_lcd_clear:
            print(f"[{pid}] LCD temizlenirken hata: {e_lcd_clear}")
    print(f"[{pid}] LED'ler, sensör ve LCD kapatıldı.")

    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
            print(f"[{pid}] Kilit ({LOCK_FILE_PATH}) serbest bırakıldı.")
        except Exception as e_lock:
            print(f"[{pid}] Kilit serbest bırakılırken hata: {e_lock}")

    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                # PID dosyasını sadece bu process oluşturduysa ve ID eşleşiyorsa sil
                if f_path == PID_FILE_PATH:
                    remove_this_pid_file = False
                    try:
                        with open(f_path, 'r') as pf_check:
                            if int(pf_check.read().strip()) == pid:
                                remove_this_pid_file = True
                    except:
                        pass  # Okuma hatası, vb.

                    if remove_this_pid_file:
                        os.remove(f_path);
                        print(f"[{pid}] Dosya silindi: {f_path}")
                    else:
                        print(f"[{pid}] PID dosyası ({f_path}) başka bir processe ait veya okunamadı, silinmedi.")
                elif f_path == LOCK_FILE_PATH:  # Fiziksel kilit dosyası her zaman silinir (eğer varsa)
                    os.remove(f_path);
                    print(f"[{pid}] Kilit fiziksel dosyası ({LOCK_FILE_PATH}) silindi.")
        except OSError as e_rm:
            print(f"[{pid}] Dosya ({f_path}) silinirken hata: {e_rm}")

    print(f"[{pid}] Temizleme fonksiyonu tamamlandı. Betik çıkıyor.")


if __name__ == "__main__":
    atexit.register(lambda: release_resources_on_exit())  # lambda ile sarmalayarak argüman sorununu çözebiliriz
    # script_exit_status_global'ı doğrudan kullanır

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
    if lcd:
        lcd.clear()
        lcd.cursor_pos = (0, 0)
        lcd.write_string(f"ScanID:{current_scan_id_global} Basladi")
        if LCD_ROWS > 1:
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Aci: -- Mes: --")

    scan_completed_flag = False  # Döngünün normal tamamlanıp tamamlanmadığını izler
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)  # Global değişkene ata
        cursor_main = db_conn_main_script_global.cursor()

        print(f"[{os.getpid()}] Servo başlangıç açısına ({SCAN_START_ANGLE}°) ayarlanıyor...")
        servo.angle = SCAN_START_ANGLE
        time.sleep(1.0)  # Servonun başlangıç pozisyonuna gelmesi için

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

            if lcd:
                try:
                    lcd.cursor_pos = (0, 0)
                    lcd.write_string(f"Aci:{angle_deg:<3} ID:{current_scan_id_global:<3}".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1:
                        lcd.cursor_pos = (1, 0)
                        lcd.write_string(f"M:{distance_cm:5.1f} H:{hiz_cm_s:4.1f}".ljust(LCD_COLS)[:LCD_COLS])
                except Exception as e_lcd_write:
                    print(f"LCD Yazma Hatası: {e_lcd_write}")

            if distance_cm > YELLOW_LED_THRESHOLD_CM:
                yellow_led.on()
            else:
                yellow_led.toggle()

            max_distance_cm = sensor.max_distance * 100
            is_reading_valid = (distance_cm > 0.0) and (distance_cm < max_distance_cm)

            if is_reading_valid:
                if distance_cm <= OBJECT_THRESHOLD_CM:
                    red_led.on();
                    green_led.off()
                else:
                    green_led.on();
                    red_led.off()
            else:
                red_led.off();
                green_led.off()

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{os.getpid()}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Tarama durduruluyor.")
                if lcd:
                    lcd.clear();
                    lcd.cursor_pos = (0, 0);
                    lcd.write_string("COK YAKIN! DUR!")

                    if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"{distance_cm:.1f} cm")
                red_led.blink(on_time=0.1, off_time=0.1, n=5, background=False)  # Hızlı yanıp sönsün
                time.sleep(4.0)
                script_exit_status_global = 'terminated_close_object'
                break

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

            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0 and (angle_deg < SCAN_END_ANGLE):
                time.sleep(sleep_duration)

        else:  # `for` döngüsü `break` olmadan tamamlanırsa
            scan_completed_successfully = True
            script_exit_status_global = 'Tamamlandi'
            print(f"[{os.getpid()}] Tarama normal şekilde tamamlandı.")
            if lcd:
                lcd.clear();
                lcd.cursor_pos = (0, 0);
                lcd.write_string("Tarama Tamamlandi")
                if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"ID:{current_scan_id_global}")

    except KeyboardInterrupt:
        print(f"\n[{os.getpid()}] Tarama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
        script_exit_status_global = 'interrupted_ctrl_c'
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("DURDURULDU (C)")
    except Exception as e_main_loop:
        print(f"[{os.getpid()}] Tarama sırasında ana döngüde beklenmedik bir hata: {e_main_loop}")
        script_exit_status_global = 'error_in_loop'
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("HATA OLUSTU!")
