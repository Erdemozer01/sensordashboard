# sensor_script.py
from gpiozero import DistanceSensor, LED
import time
import sqlite3
import os
import sys
import fcntl  # Dosya kilitleme için (Linux/Unix tabanlı sistemlerde)
import atexit  # Temiz çıkış için

# --- Ultrasonik Sensör ve LED Pinleri ---
TRIG_PIN = 23
ECHO_PIN = 24
RED_LED_PIN = 17
GREEN_LED_PIN = 18
YELLOW_LED_PIN = 27

# --- Eşik Değerleri ve Sabitler ---
OBJECT_THRESHOLD_CM = 20.0
YELLOW_LED_THRESHOLD_CM = 100.0
TERMINATION_DISTANCE_CM = 10.0
DELTA_T_SECONDS = 0.25  # İstenen örnekleme aralığı

DB_NAME = 'live_sensor_data.sqlite3'  # Django projesi ile aynı dizinde olacak
LOCK_FILE_PATH = '/tmp/sensor_script.lock'  # Kilit dosyası yolu (sistem genelinde tek olmalı)

# --- Global Donanım Değişkenleri ---
sensor = None
red_led = None
green_led = None
yellow_led = None
lock_file_handle = None  # Kilit dosyasının handle'ını saklamak için


def init_hardware():
    """Donanımı başlatır."""
    global sensor, red_led, green_led, yellow_led
    try:
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=5)
        red_led = LED(RED_LED_PIN)
        green_led = LED(GREEN_LED_PIN)
        yellow_led = LED(YELLOW_LED_PIN)
        print("Donanım başarıyla başlatıldı.")
        red_led.off()
        green_led.off()
        yellow_led.off()
        return True
    except Exception as e:
        print(f"Donanım başlatma hatası: {e}")
        return False


def init_db():
    """Veritabanını ve tabloyu oluşturur/hazırlar."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS measurements
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       ornek_no
                       INTEGER
                       UNIQUE,
                       zaman_s
                       REAL,
                       mesafe_cm
                       REAL,
                       hiz_cm_s
                       REAL
                   )
                   ''')
    # Her yeni çalıştırmada eski verileri temizle (isteğe bağlı)
    cursor.execute("DELETE FROM measurements")
    print("Veritabanındaki eski kayıtlar temizlendi.")
    conn.commit()
    conn.close()
    print(f"Veritabanı '{DB_NAME}' hazırlandı.")


def acquire_lock():
    """Betik için bir kilit almaya çalışır."""
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(f"Betik kilidi ({LOCK_FILE_PATH}) başarıyla alındı (PID: {os.getpid()}).")
        return True
    except BlockingIOError:
        print(f"'{LOCK_FILE_PATH}' kilitli. Sensör betiği zaten çalışıyor olabilir.")
        if lock_file_handle:
            lock_file_handle.close()
        return False
    except Exception as e:
        print(f"Kilit ({LOCK_FILE_PATH}) alınırken beklenmedik bir hata: {e}")
        if lock_file_handle:
            lock_file_handle.close()
        return False


def release_lock_on_exit():
    """Program sonlandığında kilidi serbest bırakır ve dosyayı siler."""
    global lock_file_handle
    print("Program sonlanıyor, kilit dosyası serbest bırakılıyor...")
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
        except Exception as e:
            print(f"Kilit serbest bırakılırken hata: {e}")
    try:
        if os.path.exists(LOCK_FILE_PATH):
            os.remove(LOCK_FILE_PATH)
            print(f"Kilit dosyası ({LOCK_FILE_PATH}) silindi.")
    except OSError as e:
        print(f"Kilit dosyası ({LOCK_FILE_PATH}) silinirken hata: {e}")


if __name__ == "__main__":
    atexit.register(release_lock_on_exit)  # Her türlü çıkışta kilidi bırakmayı garanti et

    if not acquire_lock():
        print("Betik zaten çalıştığı için çıkılıyor.")
        sys.exit(1)  # Başka bir kopya çalışıyorsa çık

    if not init_hardware():
        sys.exit(1)  # Donanım başlamazsa çık

    init_db()

    ölçüm_tamponu_hız_için = []
    ornek_sayaci = 0

    print(f"Otonom Mesafe Ölçümü Başlıyor (SQLite'a '{DB_NAME}' kaydedilecek)...")
    print(f"Veriler yaklaşık {DELTA_T_SECONDS} saniye aralıklarla kaydedilecek.")
    print("Durdurmak için Ctrl+C kullanın.")
    print("----------------------------------------------------")

    db_conn = None
    try:
        db_conn = sqlite3.connect(DB_NAME, timeout=10)
        cursor = db_conn.cursor()

        while True:
            loop_start_time = time.time()
            current_timestamp = time.time()

            distance_m = sensor.distance
            distance_cm = distance_m * 100

            hiz_cm_s = 0.0
            if ölçüm_tamponu_hız_için:
                son_ölçüm = ölçüm_tamponu_hız_için[-1]
                delta_mesafe_cm = distance_cm - son_ölçüm['mesafe_cm']
                delta_zaman_s = current_timestamp - son_ölçüm['zaman_s']
                if delta_zaman_s > 0:
                    hiz_cm_s = delta_mesafe_cm / delta_zaman_s

            print(f"Örnek: {ornek_sayaci}, Mesafe: {distance_cm:.2f} cm, Hız: {hiz_cm_s:.2f} cm/s")

            try:
                cursor.execute('''
                               INSERT INTO measurements (ornek_no, zaman_s, mesafe_cm, hiz_cm_s)
                               VALUES (?, ?, ?, ?)
                               ''', (ornek_sayaci, current_timestamp, distance_cm, hiz_cm_s))
                db_conn.commit()
            except sqlite3.IntegrityError:
                print(f"UYARI: Örnek no {ornek_sayaci} zaten mevcut veya başka bir DB hatası.")
            except Exception as e_db:
                print(f"Veritabanına yazma hatası: {e_db}")

            ölçüm_tamponu_hız_için = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]

            ornek_sayaci += 1

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"DİKKAT: Nesne çok yakın ({distance_cm:.2f}cm)! Veri toplama durduruluyor.")
                break

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

            loop_processing_time = time.time() - loop_start_time
            sleep_duration = DELTA_T_SECONDS - loop_processing_time
            if sleep_duration > 0:
                time.sleep(sleep_duration)

    except KeyboardInterrupt:
        print("\nVeri toplama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
    except Exception as e_main:
        print(f"Ana döngüde beklenmedik bir hata oluştu: {e_main}")
    finally:
        if db_conn:
            db_conn.close()
            print("Veritabanı bağlantısı kapatıldı.")

        print("\nPinler temizleniyor...")
        if red_led and hasattr(red_led, 'is_active') and red_led.is_active: red_led.off()
        if red_led and hasattr(red_led, 'close'): red_led.close()
        if green_led and hasattr(green_led, 'is_active') and green_led.is_active: green_led.off()
        if green_led and hasattr(green_led, 'close'): green_led.close()
        if yellow_led and hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        if yellow_led and hasattr(yellow_led, 'close'): yellow_led.close()
        if sensor and hasattr(sensor, 'close'): sensor.close()

        print("Sensör betiği sonlandı.")