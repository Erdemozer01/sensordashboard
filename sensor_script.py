# sensor_script.py
from gpiozero import AngularServo, DistanceSensor, LED
from RPLCD.i2c import CharLCD  # LCD için eklendi
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

# --- LCD Ayarları ---
# Lütfen bu ayarları kendi LCD'nize ve bağlantınıza göre doğrulayın/güncelleyin:
LCD_I2C_ADDRESS = 0x27  # `sudo i2cdetect -y 1` komutuyla bulduğunuz adres
LCD_PORT_EXPANDER = 'PCF8574'  # Çoğu I2C LCD modülünde bu entegre kullanılır
LCD_COLS = 16  # LCD'nizin sütun sayısı (16x2 için 16, 20x4 için 20)
LCD_ROWS = 2  # LCD'nizin satır sayısı (16x2 için 2, 20x4 için 4)
I2C_PORT = 1  # Raspberry Pi'de I2C portu genellikle 1'dir

# --- Eşik Değerleri ve Sabitler ---
OBJECT_THRESHOLD_CM = 20.0
YELLOW_LED_THRESHOLD_CM = 100.0
TERMINATION_DISTANCE_CM = 10.0

SCAN_START_ANGLE = 0
SCAN_END_ANGLE = 180
SCAN_STEP_ANGLE = 10  # Daha hassas tarama için 5 veya daha düşük yapabilirsiniz
SERVO_SETTLE_TIME = 0.3
LOOP_TARGET_INTERVAL_S = 0.6  # Her bir açı adımının yaklaşık süresi (LCD yazma dahil)

# Proje ana dizinini al (bu betik ana dizinde ise)
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'  # dash_apps.py ile aynı olmalı!
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)

LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

# --- Global Donanım ve Durum Değişkenleri ---
sensor = None
red_led = None
green_led = None
yellow_led = None
servo = None
lcd = None  # LCD için global değişken
lock_file_handle = None
current_scan_id_global = None
db_conn_main_script_global = None  # Ana döngüdeki DB bağlantısı (atexit için global)
script_exit_status_global = 'interrupted_unexpectedly'  # atexit için varsayılan durum


def init_hardware():
    global sensor, red_led, green_led, yellow_led, servo, lcd
    try:
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        red_led = LED(RED_LED_PIN)
        green_led = LED(GREEN_LED_PIN)
        yellow_led = LED(YELLOW_LED_PIN)
        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             min_pulse_width=0.0005, max_pulse_width=0.0025)  # SG90 için tipik

        print(f"[{os.getpid()}] Temel donanımlar başarıyla başlatıldı.")
        red_led.off();
        green_led.off();
        yellow_led.off()
        servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2  # Başlangıçta ortaya al

        # LCD Başlatma
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02',
                          auto_linebreaks=False)  # auto_linebreaks False daha iyi kontrol sağlar
            lcd.clear()
            lcd.write_string("Dream Pi Hazir!")
            print(f"[{os.getpid()}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
        except Exception as e_lcd_init:
            print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}. LCD olmadan devam edilecek.")
            lcd = None  # LCD başlatılamazsa None olarak kalsın

        time.sleep(0.5)  # Tüm donanımların kendine gelmesi için
        return True
    except Exception as e:
        print(f"[{os.getpid()}] Genel donanım başlatma hatası: {e}")
        return False


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
    # Önceki çalıştırmadan kalmış olabilecek PID dosyasını temizle (fcntl kilidi daha güvenilir)
    try:
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError:
        pass

    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # Non-blocking lock

        # Kilit başarıyla alındıysa, PID dosyasını oluştur/güncelle
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Betik kilidi ({LOCK_FILE_PATH}) ve PID ({PID_FILE_PATH}) başarıyla oluşturuldu.")
        return True
    except BlockingIOError:  # Başka bir process kilit tutuyorsa
        print(f"[{os.getpid()}] '{LOCK_FILE_PATH}' kilitli. Sensör betiği zaten çalışıyor olabilir.")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False
    except Exception as e:  # Diğer hatalar (örn: izin hatası)
        print(f"[{os.getpid()}] Kilit/PID alınırken beklenmedik bir hata: {e}")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False


def release_resources_on_exit():
    """Program sonlandığında çağrılacak temizleme fonksiyonu."""
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, red_led, green_led, yellow_led, servo, lcd

    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Çıkış durumu: {script_exit_status_global}")

    if db_conn_main_script_global:  # Ana döngüdeki DB bağlantısını kapat
        try:
            db_conn_main_script_global.close()
            print(f"[{pid}] Ana veritabanı bağlantısı kapatıldı.")
        except Exception as e_db_close:
            print(f"[{pid}] Ana DB bağlantısı kapatılırken hata: {e_db_close}")

    # Tarama durumunu güncelle (sadece 'running' ise)
    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ? AND status = 'running'",
                                (script_exit_status_global, current_scan_id_global))
            conn_exit.commit()
            print(
                f"[{pid}] Tarama ID {current_scan_id_global} durumu '{script_exit_status_global}' olarak güncellendi.")
        except Exception as e_db_update_exit:
            print(f"[{pid}] Çıkışta tarama durumu güncellenirken DB hatası: {e_db_update_exit}")
        finally:
            if conn_exit:
                conn_exit.close()

    print(f"[{pid}] Donanım kapatılıyor...")
    if servo and hasattr(servo, 'detach'):
        try:
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
            time.sleep(0.2)  # Servonun pozisyona gelmesi için kısa bekleme
            servo.detach()
            servo.close()
            print(f"[{pid}] Servo kapatıldı.")
        except Exception as e_servo:
            print(f"[{pid}] Servo kapatılırken hata: {e_servo}")

    # LED'leri kapat
    for led_obj in [red_led, green_led, yellow_led]:
        if led_obj and hasattr(led_obj, 'close'):  # Önce var mı diye kontrol et
            if hasattr(led_obj, 'is_active') and led_obj.is_active:
                led_obj.off()
            led_obj.close()

    # Sensörü kapat
    if sensor and hasattr(sensor, 'close'):
        sensor.close()

    # LCD'yi temizle
    if lcd:
        try:
            lcd.clear()
            # lcd.backlight_enabled = False # İsteğe bağlı
        except Exception as e_lcd_clear:
            print(f"[{pid}] LCD temizlenirken hata: {e_lcd_clear}")
    print(f"[{pid}] LED'ler, sensör ve LCD (temizlendi) kapatıldı.")

    # Kilit dosyasını serbest bırak ve PID dosyasını sil
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()  # Dosyayı kapatmak önemli
            print(f"[{pid}] Kilit ({LOCK_FILE_PATH}) serbest bırakıldı.")
        except Exception as e_lock:
            print(f"[{pid}] Kilit serbest bırakılırken hata: {e_lock}")

    # Fiziksel kilit ve PID dosyalarını sil
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                # PID dosyasını sadece bu process oluşturduysa silmeye çalış
                if f_path == PID_FILE_PATH:
                    can_delete_pid_file = False
                    try:
                        with open(f_path, 'r') as pf_check:
                            if int(pf_check.read().strip()) == pid:
                                can_delete_pid_file = True
                    except:  # Okuma hatası, dosya boş olabilir vs.
                        pass  # Silmeyi yine de deneyebiliriz veya bırakabiliriz

                    if can_delete_pid_file:
                        os.remove(f_path)
                        print(f"[{pid}] PID dosyası silindi: {f_path}")
                    else:
                        # Eğer PID dosyası başka bir processe aitse veya okunamadıysa,
                        # ve kilit dosyası da silinecekse, bu durumda bırakmak daha güvenli olabilir.
                        # Ancak fcntl kilidi ana mekanizma olduğu için fiziksel kilit dosyası silinebilir.
                        print(f"[{pid}] PID dosyası ({f_path}) ya başka processe ait ya da okunamadı, silinmedi.")

                elif f_path == LOCK_FILE_PATH:  # Fiziksel kilit dosyası her zaman silinir
                    os.remove(f_path)
                    print(f"[{pid}] Kilit fiziksel dosyası ({LOCK_FILE_PATH}) silindi.")
        except OSError as e_rm:
            print(f"[{pid}] Dosya ({f_path}) silinirken hata: {e_rm}")

    print(f"[{pid}] Temizleme fonksiyonu tamamlandı. Betik çıkıyor.")


# --- Ana Betik Başlangıcı ---
if __name__ == "__main__":
    # atexit.register en üste konulmalı ki her türlü çıkışta (normal, sys.exit, exception) çalışsın
    # script_exit_status_global'ın son değeri atexit'e parametre olarak geçer.
    atexit.register(lambda: release_resources_on_exit(script_exit_status_global))

    if not acquire_lock_and_pid():
        sys.exit(1)  # Başka bir kopya çalışıyorsa veya kilit alınamadıysa çık

    if not init_hardware():
        sys.exit(1)  # Donanım başlamazsa çık

    init_db_for_scan()  # Bu fonksiyon current_scan_id_global'i ayarlar
    if not current_scan_id_global:
        print(f"[{os.getpid()}] HATA: Tarama ID'si oluşturulamadı. Veritabanı sorunu olabilir. Çıkılıyor.")
        sys.exit(1)  # Tarama ID'si alınamazsa çık

    ölçüm_tamponu_hız_için_yerel = []
    ornek_sayaci_yerel = 0  # Bu sayaç aslında şu anki kullanımda çok kritik değil

    print(f"[{os.getpid()}] Servo ile 2D Tarama Başlıyor (Tarama ID: {current_scan_id_global})...")
    if lcd:
        lcd.clear()
        lcd.cursor_pos = (0, 0)
        lcd.write_string(f"Scan ID:{current_scan_id_global}")
        if LCD_ROWS > 1:
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Taran. Basladi")

    scan_completed_successfully = False  # Döngünün normal tamamlanıp tamamlanmadığını izlemek için
    try:
        # Ana döngü için veritabanı bağlantısını global değişkene ata
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)
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
            if ölçüm_tamponu_hız_için_yerel:  # En az bir önceki ölçüm varsa
                son_veri_noktasi = ölçüm_tamponu_hız_için_yerel[-1]
                delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
                delta_zaman = current_timestamp - son_veri_noktasi['zaman_s']
                if delta_zaman > 0.001:  # Çok küçük zaman farkları için sıfıra bölmeyi engelle
                    hiz_cm_s = delta_mesafe / delta_zaman

            # LCD'ye Yazdır
            if lcd:
                try:
                    # LCD'ye yazarken string formatlamasına dikkat edin, LCD_COLS'u aşmasın
                    lcd.cursor_pos = (0, 0)
                    aci_str = f"Aci:{angle_deg:<3}"[:LCD_COLS]  # Satırı aşmaması için kırp
                    lcd.write_string(aci_str.ljust(LCD_COLS))  # Satırı doldurmak için boşluk ekle

                    if LCD_ROWS > 1:
                        lcd.cursor_pos = (1, 0)
                        mesafe_str = f"M:{distance_cm:5.1f}cm"[:LCD_COLS]  # Satırı aşmaması için kırp
                        lcd.write_string(mesafe_str.ljust(LCD_COLS))
                except Exception as e_lcd_write:
                    print(f"LCD Yazma Hatası: {e_lcd_write}")  # Konsola logla, program devam etsin

            # LED Mantığı
            if distance_cm > YELLOW_LED_THRESHOLD_CM:
                yellow_led.on()
            else:
                yellow_led.toggle()

            max_distance_cm = sensor.max_distance * 100
            is_reading_valid = (distance_cm > 0.0) and (distance_cm < max_distance_cm)

            if is_reading_valid:
                if distance_cm <= OBJECT_THRESHOLD_CM:  # 20cm
                    red_led.on();
                    green_led.off()
                else:
                    green_led.on();
                    red_led.off()
            else:
                red_led.off();
                green_led.off()

            # Çok Yakın Engel Durumu
            if distance_cm < TERMINATION_DISTANCE_CM:  # 10cm
                print(f"[{os.getpid()}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Tarama durduruluyor.")
                if lcd:
                    lcd.clear()
                    lcd.cursor_pos = (0, 0);
                    lcd.write_string("COK YAKIN! DUR!")
                    if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"{distance_cm:.1f} cm")
                red_led.blink(on_time=0.2, off_time=0.2);
                green_led.off();
                yellow_led.off()  # Kırmızı yanıp sönsün
                script_exit_status_global = 'terminated_close_object'
                time.sleep(1.5)  # Alarmın fark edilmesi için
                break  # Döngüden çık, atexit temizliği ve DB güncellemesini yapacak

            # Veritabanına Kaydet
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

            # Döngü Zamanlaması
            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0 and (angle_deg < SCAN_END_ANGLE):  # Son adımda fazladan bekleme yapma
                time.sleep(sleep_duration)

        else:  # Döngü `break` ile değil de normal tamamlanırsa (yani for döngüsü biterse)
            scan_completed_successfully = True
            script_exit_status_global = 'completed'  # atexit'in doğru durumu kaydetmesi için
            print(f"[{os.getpid()}] Tarama normal şekilde tamamlandı.")
            if lcd:
                lcd.clear()
                lcd.cursor_pos = (0, 0);
                lcd.write_string("Tarama Tamamlandi")
                if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(f"ID:{current_scan_id_global}")


    except KeyboardInterrupt:
        print(f"\n[{os.getpid()}] Tarama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
        script_exit_status_global = 'interrupted_ctrl_c'
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("DURDURULDU (C)")
    except Exception as e_main_loop:
        print(f"[{os.getpid()}] Tarama sırasında ana döngüde beklenmedik bir hata: {e_main_loop}")
        script_exit_status_global = f'error_in_loop'  # Hata tipini de ekleyebiliriz
        if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("HATA OLUSTU!")

