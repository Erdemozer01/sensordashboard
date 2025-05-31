# sensor_script.py
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
import pandas as pd
# --- Pin Tanımlamaları ---
TRIG_PIN = 23
ECHO_PIN = 24
YELLOW_LED_PIN = 27
MOTOR_PIN_IN1 = 5
MOTOR_PIN_IN2 = 6
MOTOR_PIN_IN3 = 13
MOTOR_PIN_IN4 = 19

# --- LCD Ayarları ---
LCD_I2C_ADDRESS = 0x27
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1

# --- Eşik ve Tarama Değerleri ---
TERMINATION_DISTANCE_CM = 10.0
DEFAULT_SCAN_EXTENT_ANGLE = 135  # Merkezden her iki yana taranacak açı
DEFAULT_SCAN_STEP_ANGLE = 10
STEP_MOTOR_SETTLE_TIME = 0.05  # Sensör okuması için bekleme (motor hareketinden sonra)
LOOP_TARGET_INTERVAL_S = 0.2  # Her bir açı adımının yaklaşık toplam süresi (hızı etkiler)

STEPS_PER_REVOLUTION = 4096
STEP_DELAY = 0.0012  # Adımlar arası gecikme (motor hızını etkiler)
STEP_SEQUENCE = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0], [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1],
                 [1, 0, 0, 1]]

PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

sensor = None;
yellow_led = None;
lcd = None
motor_pins_global = []
current_motor_step_index_global = 0  # Sekans içindeki mevcut adım (0-7)
current_motor_angle_global = 0.0  # Motorun mevcut açısı (derece)
lock_file_handle = None;
current_scan_id_global = None
db_conn_main_script_global = None;
script_exit_status_global = 'interrupted_unexpectedly'

# Komut satırından gelen parametreler bu global değişkenlere atanacak
SCAN_EXTENT_ARG = DEFAULT_SCAN_EXTENT_ANGLE
SCAN_STEP_ARG = DEFAULT_SCAN_STEP_ANGLE


def perform_scan_segment(start_angle, end_angle, step_value, phase_description="Tarama Fazi"):
    """
    Belirtilen başlangıç ve bitiş açıları arasında, verilen adım değeriyle
    step motoru hareket ettirerek tarama yapar ve verileri kaydeder.

    Args:
        start_angle (int): Bu segment için taramanın başlayacağı açı (derece).
        end_angle (int): Bu segment için taramanın biteceği açı (derece).
        step_value (int): Her adımda motorun döneceği açı (pozitif veya negatif olabilir).
        phase_description (str): LCD ve loglarda gösterilecek faz açıklaması.

    Returns:
        bool: Tarama normal tamamlandıysa True, yakın engel nedeniyle sonlandıysa False.
    """
    global current_motor_angle_global, yellow_led, lcd, sensor, script_exit_status_global
    global ölçüm_tamponu_hız_için_yerel, collected_cartesian_points_for_area
    global db_conn_main_script_global  # Fonksiyon içinde cursor oluşturmak için db bağlantısını almalı

    if not db_conn_main_script_global:
        print(f"[{os.getpid()}] HATA: {phase_description} için veritabanı bağlantısı yok!")
        return False  # Veya bir exception fırlat

    cursor_main = db_conn_main_script_global.cursor()

    print(f"[{os.getpid()}] {phase_description}: {start_angle}° -> {end_angle}° (Adım: {abs(step_value)}°)")
    if lcd:
        try:
            lcd.cursor_pos = (1, 0)  # Genellikle ikinci satıra faz bilgisi
            lcd.write_string(f"{phase_description[:LCD_COLS - 1]}".ljust(LCD_COLS)[:LCD_COLS])  # Fazı göster, sığdır
        except Exception as e_lcd:
            print(f"LCD'ye faz yazma hatası: {e_lcd}")

    # Motoru bu fazın başlangıç açısına hassas bir şekilde getir
    # (Eğer zaten oradaysa move_motor_to_target_angle_incremental çok az hareket eder veya etmez)
    move_motor_to_target_angle_incremental(start_angle, step_delay=STEP_DELAY)
    time.sleep(0.2)  # Yerleşmesi için kısa bir ek bekleme

    # range'in son değeri dahil olması için doğru bitiş noktasını hesapla
    # Adım pozitifse end_angle + step, negatifse end_angle - step (yani yine end_angle + step)
    effective_end_for_range = end_angle + (step_value // abs(step_value) if step_value != 0 else 1)

    for target_angle_deg in range(start_angle, effective_end_for_range, step_value):
        loop_iteration_start_time = time.time()

        if yellow_led and hasattr(yellow_led, 'toggle'):  # Sarı LED program çalıştığı sürece yanıp söner
            yellow_led.toggle()

        # Motoru bir sonraki hedef açıya götür
        # current_motor_angle_global zaten bir önceki adımdan doğru olmalı,
        # move_motor_to_target_angle_incremental onu target_angle_deg'e getirir.
        move_motor_to_target_angle_incremental(target_angle_deg, step_delay=STEP_DELAY)
        time.sleep(STEP_MOTOR_SETTLE_TIME)  # Sensör okuması için bekle

        current_timestamp = time.time()
        distance_m = sensor.distance
        distance_cm = distance_m * 100

        # Kullanılan gerçek açı motorun o anki pozisyonu olmalı (move_motor_... fonksiyonu bunu günceller)
        actual_angle_for_calc = current_motor_angle_global
        angle_rad = math.radians(actual_angle_for_calc)
        x_cm = distance_cm * math.cos(angle_rad)
        y_cm = distance_cm * math.sin(angle_rad)

        # Alan hesabı için geçerli noktaları topla (sadece x,y yeterli olabilir)
        if 0 < distance_cm < (sensor.max_distance * 100):
            collected_cartesian_points_for_area.append((x_cm, y_cm))

        hiz_cm_s = 0.0
        if ölçüm_tamponu_hız_için_yerel:  # En az bir önceki ölçüm varsa
            son_veri_noktasi = ölçüm_tamponu_hız_için_yerel[-1]
            delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
            delta_zaman = current_timestamp - son_veri_noktasi['zaman_s']
            if delta_zaman > 0.001:  # Sıfıra bölmeyi engelle
                hiz_cm_s = delta_mesafe / delta_zaman

        if lcd:
            try:
                lcd.cursor_pos = (0, 0)
                lcd.write_string(f"A:{actual_angle_for_calc:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                # İkinci satıra faz bilgisini veya hızı yazdırabiliriz
                if LCD_ROWS > 1:
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string(f"H:{hiz_cm_s:5.1f} {phase_description[:3]}".ljust(LCD_COLS)[:LCD_COLS])
            except Exception as e_lcd_write:
                print(f"LCD Yazma Hatası ({phase_description}): {e_lcd_write}")

        # Çok Yakın Engel Durumu
        if distance_cm < TERMINATION_DISTANCE_CM:
            print(
                f"[{os.getpid()}] DİKKAT: {phase_description} sırasında NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Tarama durduruluyor.")
            if lcd:
                lcd.clear();
                lcd.cursor_pos = (0, 0);
                lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                    f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
            if yellow_led: yellow_led.on()  # Tehlike anında sarı LED sabit yansın
            script_exit_status_global = 'terminated_close_object'
            time.sleep(1.0)  # Alarmın fark edilmesi ve LCD'nin okunması için
            return False  # Taramayı durdurmak için False döndür

        # Veritabanına Kaydet
        try:
            cursor_main.execute('''
                                INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ''', (current_scan_id_global, actual_angle_for_calc, distance_cm, hiz_cm_s,
                                      current_timestamp, x_cm, y_cm))
            db_conn_main_script_global.commit()
        except Exception as e_db_insert:
            print(f"[{os.getpid()}] DB Ekleme Hatası ({phase_description}): {e_db_insert}")

        ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]

        # Döngü Zamanlaması
        loop_processing_time = time.time() - loop_iteration_start_time
        sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)

        is_last_step_in_segment = (target_angle_deg == end_angle)
        if sleep_duration > 0 and not is_last_step_in_segment:
            time.sleep(sleep_duration)

    print(f"[{os.getpid()}] {phase_description} tamamlandı.")
    return True  # Bu faz normal tamamlandı

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
        current_motor_angle_global = 0.0  # Motorun başlangıçta 0 (merkez) olduğunu varsay
        # Fiziksel olarak 0'a getirmek için bir "homing" rutini eklenebilir. Şimdilik varsayım.
        print(
            f"[{os.getpid()}] Temel donanımlar başarıyla başlatıldı. Motor başlangıçta {current_motor_angle_global}° kabul ediliyor.")
    except Exception as e:
        print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}");
        hardware_ok = False
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear()
            lcd.cursor_pos = (0, 0);
            lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("Hazirlaniyor...".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1.0)
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
                           NULL,
                           cevre_cm
                           REAL
                           DEFAULT
                           NULL,
                           max_genislik_cm
                           REAL
                           DEFAULT
                           NULL,
                           max_derinlik_cm
                           REAL
                           DEFAULT
                           NULL,
                           scan_extent_angle_setting
                           REAL,
                           step_angle_setting
                           REAL
                       )''')  # start/end yerine extent
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
                       ))''')
        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")
        scan_start_time = time.time()
        # SCAN_EXTENT_ARG ve SCAN_STEP_ARG global değişkenlerini kullan
        cursor.execute("""
                       INSERT INTO servo_scans (start_time, status, scan_extent_angle_setting, step_angle_setting)
                       VALUES (?, ?, ?, ?)
                       """, (scan_start_time, 'running', SCAN_EXTENT_ARG, SCAN_STEP_ARG))
        current_scan_id_global = cursor.lastrowid
        conn.commit()
        print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {current_scan_id_global}")
    except sqlite3.Error as e_db_init:
        print(f"[{os.getpid()}] DB başlatma/tarama kaydı hatası: {e_db_init}");
        current_scan_id_global = None
    finally:
        if conn: conn.close()


def acquire_lock_and_pid():  # Aynı
    global lock_file_handle;
    try:
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError:
        pass
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w');
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB);
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid())); return True
    except BlockingIOError:
        print(f"Kilit dosyası mevcut."); if lock_file_handle: lock_file_handle.close(); lock_file_handle = None; return False
    except Exception as e: print(f"Kilit/PID hatası: {e}");
    if lock_file_handle: lock_file_handle.close(); lock_file_handle = None; return False


def _apply_step_to_motor(sequence_index):  # Aynı
    global motor_pins_global
    if not motor_pins_global: return
    step_pattern = STEP_SEQUENCE[sequence_index % len(STEP_SEQUENCE)]
    for i in range(4):
        if motor_pins_global[i] and not motor_pins_global[i].closed:
            if step_pattern[i] == 1:
                motor_pins_global[i].on()
            else:
                motor_pins_global[i].off()


def move_motor_to_target_angle_incremental(target_angle_deg, step_delay=STEP_DELAY):  # Aynı
    global current_motor_angle_global, current_motor_step_index_global
    degrees_per_half_step = 360.0 / STEPS_PER_REVOLUTION
    angle_difference = target_angle_deg - current_motor_angle_global
    if abs(angle_difference) < degrees_per_half_step / 2:
        current_motor_angle_global = target_angle_deg;
        return
    steps_to_move = round(abs(angle_difference) / degrees_per_half_step)
    if steps_to_move == 0: current_motor_angle_global = target_angle_deg; return
    direction_is_cw = angle_difference > 0
    for _ in range(steps_to_move):
        if direction_is_cw:
            current_motor_step_index_global = (current_motor_step_index_global + 1) % len(STEP_SEQUENCE)
        else:
            current_motor_step_index_global = (current_motor_step_index_global - 1 + len(STEP_SEQUENCE)) % len(
                STEP_SEQUENCE)
        _apply_step_to_motor(current_motor_step_index_global)
        time.sleep(step_delay)
    current_motor_angle_global = target_angle_deg


def calculate_polygon_area_shoelace(points):  # Aynı
    n = len(points);
    area = 0.0;
    if n < 3: return 0.0
    for i in range(n): x1, y1 = points[i]; x2, y2 = points[(i + 1) % n]; area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0


def calculate_perimeter(points_with_origin):  # Aynı
    perimeter = 0.0;
    n = len(points_with_origin);
    if n > 1: perimeter += math.sqrt(points_with_origin[1][0] ** 2 + points_with_origin[1][1] ** 2)
    for i in range(1, n - 1): x1, y1 = points_with_origin[i]; x2, y2 = points_with_origin[
        i + 1]; perimeter += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    if n > 1: perimeter += math.sqrt(points_with_origin[-1][0] ** 2 + points_with_origin[-1][1] ** 2)
    return perimeter


def release_resources_on_exit():  # Motoru 0'a getirme önemli
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global
    global sensor, yellow_led, motor_pins_global, lcd
    pid = os.getpid();
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Durum: {script_exit_status_global}")
    # ... (DB status update - önceki gibi) ...
    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH);
            cursor_exit = conn_exit.cursor();
            cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (current_scan_id_global,));
            row = cursor_exit.fetchone()
            if row and row[0] == 'running': cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?",
                                                                (script_exit_status_global,
                                                                 current_scan_id_global)); conn_exit.commit()
        except Exception as e:
            print(f"DB status update error on exit: {e}")
        finally:
            if conn_exit: conn_exit.close()
    print(f"[{pid}] Donanım kapatılıyor...")
    if motor_pins_global:
        try:
            print(f"[{pid}] Motor merkeze (0°) alınıyor...");
            move_motor_to_target_angle_incremental(0);
            time.sleep(0.5)
            for pin_obj in motor_pins_global:
                if hasattr(pin_obj, 'close') and not pin_obj.closed: pin_obj.off(); pin_obj.close()
            print(f"[{pid}] Step motor pinleri kapatıldı.")
        except Exception as e_motor_release:
            print(f"[{pid}] Motor pinleri kapatılırken hata: {e_motor_release}")
    # ... (Kalan LED, sensör, LCD kapatma ve kilit/PID silme - önceki gibi) ...
    if yellow_led and hasattr(yellow_led, 'close'):
        if hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        yellow_led.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    if lcd:
        try: lcd.clear(); lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
        except: pass


    print(f"[{pid}] Kalan donanımlar kapatıldı.")
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
        except:
            pass

    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_delete = False;
                    try:
                        with open(f_path, 'r') as pf:
                            if int(pf.read().strip()) == pid: can_delete = True
                    except:
                        pass
                    if can_delete: os.remove(f_path)
                else:
                    os.remove(f_path)
        except:
            pass
    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")


def perform_scan_phase(start_angle, end_angle, step, phase_description="Tarama Fazı"):
    """Belirli bir açı aralığında tarama yapar ve noktaları kaydeder."""
    global current_scan_id_global, db_conn_main_script_global, yellow_led, lcd
    global ölçüm_tamponu_hız_için_yerel, collected_cartesian_points_for_area, script_exit_status_global

    cursor_main = db_conn_main_script_global.cursor()  # Global bağlantıyı kullan
    print(f"[{os.getpid()}] {phase_description}: {start_angle}° -> {end_angle}° (Adım: {abs(step)}°)")
    if lcd:
        lcd.cursor_pos = (1, 0)  # İkinci satıra faz bilgisini yaz
        lcd.write_string(f"{phase_description}".ljust(LCD_COLS)[:LCD_COLS])

    # Motoru bu fazın başlangıç açısına getir
    move_motor_to_target_angle_incremental(start_angle)
    time.sleep(0.2)  # Yerleşme için ek bekleme

    effective_end_for_range = end_angle + (step // abs(step) if step != 0 else 1)
    for target_angle_deg in range(start_angle, effective_end_for_range, step):
        loop_iteration_start_time = time.time()
        if yellow_led: yellow_led.toggle()

        # Hedef açıya git (eğer zaten orada değilse)
        if abs(current_motor_angle_global - target_angle_deg) > (360.0 / STEPS_PER_REVOLUTION / 2):
            move_motor_to_target_angle_incremental(target_angle_deg)
        time.sleep(STEP_MOTOR_SETTLE_TIME)

        current_timestamp = time.time()
        distance_m = sensor.distance;
        distance_cm = distance_m * 100
        actual_angle_for_calc = current_motor_angle_global
        angle_rad = math.radians(actual_angle_for_calc)
        x_cm = distance_cm * math.cos(angle_rad);
        y_cm = distance_cm * math.sin(angle_rad)

        if 0 < distance_cm < (sensor.max_distance * 100):
            # Alan hesabı için (açı, x, y) olarak sakla, sonra sırala
            collected_cartesian_points_for_area.append({'angle': actual_angle_for_calc, 'x': x_cm, 'y': y_cm})

        hiz_cm_s = 0.0
        if ölçüm_tamponu_hız_için_yerel:
            son_veri = ölçüm_tamponu_hız_için_yerel[-1];
            delta_d = distance_cm - son_veri['mesafe_cm'];
            delta_t = current_timestamp - son_veri['zaman_s']
            if delta_t > 0.001: hiz_cm_s = delta_d / delta_t

        if lcd:
            try:
                lcd.cursor_pos = (0, 0);
                lcd.write_string(f"A:{actual_angle_for_calc:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                    f"{phase_description[:8]}".ljust(LCD_COLS)[:LCD_COLS])  # Fazı göster
            except:
                pass

        if distance_cm < TERMINATION_DISTANCE_CM:
            print(f"DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)!")
            if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("COK YAKIN! DUR!");
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
            if yellow_led: yellow_led.on();
            script_exit_status_global = 'terminated_close_object';
            time.sleep(1.5)
            raise Exception("Acil Durum: Çok Yakın Engel!")  # Ana try bloğunda yakalanacak

        try:
            cursor_main.execute(
                'INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (current_scan_id_global, actual_angle_for_calc, distance_cm, hiz_cm_s, current_timestamp, x_cm, y_cm))
            db_conn_main_script_global.commit()
        except Exception as e:
            print(f"DB Ekleme Hatası: {e}")
        ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]

        loop_processing_time = time.time() - loop_iteration_start_time
        sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
        is_last_step = (target_angle_deg == end_angle)
        if sleep_duration > 0 and not is_last_step: time.sleep(sleep_duration)
    print(f"[{os.getpid()}] {phase_description} tamamlandı.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motor ile 2D Alan Tarama Betiği")
    parser.add_argument("--scan_extent", type=int, default=DEFAULT_SCAN_EXTENT_ANGLE,
                        help=f"Merkezden her yöne tarama açısı (varsayılan: {DEFAULT_SCAN_EXTENT_ANGLE})")
    parser.add_argument("--step_angle", type=int, default=DEFAULT_SCAN_STEP_ANGLE,
                        help=f"Tarama adım açısı (varsayılan: {DEFAULT_SCAN_STEP_ANGLE})")
    args = parser.parse_args()

    SCAN_EXTENT_PARAM = args.scan_extent
    SCAN_STEP_PARAM = args.step_angle
    if SCAN_STEP_PARAM <= 0: SCAN_STEP_PARAM = DEFAULT_SCAN_STEP_ANGLE
    if SCAN_EXTENT_PARAM <= 0: SCAN_EXTENT_PARAM = DEFAULT_SCAN_EXTENT_ANGLE

    atexit.register(lambda: release_resources_on_exit())

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)

    # init_db_for_scan, global SCAN_EXTENT_ARG ve SCAN_STEP_ARG kullanır
    # Bunları parser'dan gelenlerle güncelleyelim.
    # Ancak init_db_for_scan'daki servo_scans tablosu start_angle_setting, end_angle_setting bekliyor.
    # Bu yeni tarama mantığı için bunu güncelleyelim.
    # SCAN_START_ANGLE ve SCAN_END_ANGLE'ı simetrik tarama için ayarlayalım.
    SCAN_START_ANGLE = -SCAN_EXTENT_PARAM
    SCAN_END_ANGLE = SCAN_EXTENT_PARAM
    SCAN_STEP_ANGLE = SCAN_STEP_PARAM  # Bu global init_db'de kullanılacak.
    init_db_for_scan()
    if not current_scan_id_global: sys.exit(1)

    ölçüm_tamponu_hız_için_yerel = []
    collected_cartesian_points_for_area = []  # Alan hesabı için tüm (x,y) noktaları

    current_motor_angle_global = 0.0

    print(
        f"[{os.getpid()}] İki Fazlı Simetrik Tarama Başlıyor (ID: {current_scan_id_global}). +/-{SCAN_EXTENT_PARAM}° , Adım: {SCAN_STEP_PARAM}°")
    if lcd:
        lcd.clear();
        lcd.cursor_pos = (0, 0);
        lcd.write_string(f"ID:{current_scan_id_global} IkiFaz".ljust(LCD_COLS)[:LCD_COLS])
        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
            f"+/-{SCAN_EXTENT_PARAM} S:{SCAN_STEP_PARAM}".ljust(LCD_COLS)[:LCD_COLS])

    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)

        # Motoru başlangıç pozisyonu olan 0 dereceye getir
        print(f"[{os.getpid()}] Motor merkeze (0°) getiriliyor...")
        move_motor_to_target_angle_incremental(0)
        time.sleep(0.5)

        # FAZ 1: 0'dan +SCAN_EXTENT_PARAM'a Saat Yönünde
        perform_scan_segment(0, SCAN_EXTENT_PARAM, SCAN_STEP_PARAM, phase_name="Faz1: CW")

        # Ara Hareket: Merkeze Geri Dönüş
        print(f"[{os.getpid()}] Motor merkeze (0°) geri dönüyor...")
        move_motor_to_target_angle_incremental(0)
        if lcd:
            lcd.cursor_pos = (0, 0);
            lcd.write_string("Merkeze Donuluyor".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(" ".ljust(LCD_COLS))  # Alt satırı temizle
        time.sleep(0.5)

        # FAZ 2: 0'dan -SCAN_EXTENT_PARAM'a Saat Yönü Tersi
        perform_scan_segment(0, -SCAN_EXTENT_PARAM, -SCAN_STEP_PARAM, phase_name="Faz2: CCW")

        # Tarama bitti, motoru son olarak merkeze (0) al (release_resources_on_exit de yapacak ama burada da iyi)
        move_motor_to_target_angle_incremental(0)

        # Alan hesabı için toplanan tüm noktaları kullan (collected_cartesian_points_for_area)
        # Bu liste her iki fazdan da (0->X ve 0->-X) noktalar içerecek.
        # Shoelace formülü için bu noktaların açısal olarak sıralı olması gerekir.
        # Veya veritabanından çekip sıralayabiliriz.
        # `collected_cartesian_points_for_area` listesi şu an (x,y) tuple'ları değil, {'angle', 'x', 'y', 'dist'} dict'leri içeriyor.
        # Bunu düzeltelim: Sadece (x,y) tuple'ları saklasın.

        # Düzeltilmiş: Alan hesabı için DB'den sıralı çek
        if db_conn_main_script_global: db_conn_main_script_global.close(); db_conn_main_script_global = None

        conn_analysis = sqlite3.connect(DB_PATH)
        cursor_analysis = conn_analysis.cursor()
        # Tüm geçerli noktaları açıya göre sıralı çek
        df_all_valid_points = pd.read_sql_query(
            f"SELECT x_cm, y_cm FROM scan_points WHERE scan_id = {current_scan_id_global} AND mesafe_cm > 0 AND mesafe_cm < {sensor.max_distance * 100} ORDER BY angle_deg ASC",
            conn_analysis
        )

        final_cartesian_points_for_area = []
        if not df_all_valid_points.empty:
            final_cartesian_points_for_area = list(zip(df_all_valid_points['x_cm'], df_all_valid_points['y_cm']))

        if len(final_cartesian_points_for_area) >= 2:
            polygon_vertices = [(0.0, 0.0)] + final_cartesian_points_for_area
            alan = calculate_polygon_area_shoelace(polygon_vertices)
            cevre = calculate_perimeter(polygon_vertices)
            x_coords = [p[0] for p in final_cartesian_points_for_area if p[0] is not None]
            y_coords = [p[1] for p in final_cartesian_points_for_area if p[1] is not None]
            max_d = max(x_coords) if x_coords else 0.0
            max_g = (max(y_coords) - min(y_coords)) if y_coords else 0.0

            print(f"TARANAN ALAN (-{SCAN_EXTENT_PARAM}° ile +{SCAN_EXTENT_PARAM}°): {alan:.2f} cm²")
            if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string(
                f"Alan:{alan:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])

            script_exit_status_global = 'completed_analysis'
            try:
                cursor_analysis.execute(
                    "UPDATE servo_scans SET hesaplanan_alan_cm2=?,cevre_cm=?,max_genislik_cm=?,max_derinlik_cm=?,status=? WHERE id=?",
                    (alan, cevre, max_g, max_d, script_exit_status_global, current_scan_id_global))
                conn_analysis.commit()
            except Exception as e:
                print(f"DB Alan Güncelleme Hatası: {e}")
        else:
            script_exit_status_global = 'completed_insufficient_points'
            if lcd: lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string("Tarama Tamam".ljust(LCD_COLS)[:LCD_COLS]);
            if LCD_ROWS > 1 and script_exit_status_global == 'completed_insufficient_points': lcd.cursor_pos = (1,
                                                                                                                0); lcd.write_string(
                "Alan Hesaplanamadi".ljust(LCD_COLS)[:LCD_COLS])

        conn_analysis.close()
        scan_completed_successfully = True  # Bu satır artık burada

    except KeyboardInterrupt:
        script_exit_status_global = 'interrupted_ctrl_c'; print(f"\nCtrl+C")
    except Exception as e:
        script_exit_status_global = f'error_in_loop: {str(e)[:50]}'; print(f"Ana döngü hatası: {e}")
