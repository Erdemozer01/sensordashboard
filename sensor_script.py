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
# Varsayılan tarama açıları: +135'ten -135'e (toplam 270 derece)
DEFAULT_INITIAL_GOTO_ANGLE_ARG = 135
DEFAULT_FINAL_SCAN_ANGLE_ARG = -135
DEFAULT_SCAN_STEP_ANGLE_ARG = 10

# --- Step Motor Ayarları ---
STEP_MOTOR_SETTLE_TIME = 0.05
LOOP_TARGET_INTERVAL_S = 0.15
STEPS_PER_REVOLUTION = 4096  # Motorunuzun 1 tam tur için adım sayısı
STEP_DELAY = 0.0012  # Adımlar arası bekleme süresi (hızı etkiler)
STEP_SEQUENCE = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]

# --- Dosya Yolları ---
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

# --- Global Değişkenler ---
sensor = None
yellow_led = None
lcd = None
motor_pins_global = []
current_motor_step_index_global = 0
current_motor_angle_global = 0.0
lock_file_handle = None
current_scan_id_global = None
db_conn_main_script_global = None
script_exit_status_global = 'interrupted_unexpectedly'

INITIAL_GOTO_ANGLE = DEFAULT_INITIAL_GOTO_ANGLE_ARG
ACTUAL_SCAN_END_ANGLE = DEFAULT_FINAL_SCAN_ANGLE_ARG
ACTUAL_SCAN_STEP = DEFAULT_SCAN_STEP_ANGLE_ARG


def init_hardware():
    global sensor, yellow_led, motor_pins_global, lcd, current_motor_angle_global
    hardware_ok = True
    try:
        print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        yellow_led = LED(YELLOW_LED_PIN)
        motor_pins_global = [OutputDevice(p) for p in [MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4]]
        for pin in motor_pins_global:
            pin.off()
        yellow_led.off()
        current_motor_angle_global = 0.0
        print(f"[{os.getpid()}] Temel donanımlar ve Step Motor başarıyla başlatıldı.")
    except Exception as e:
        print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}")
        hardware_ok = False

    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
            lcd.clear();
            lcd.cursor_pos = (0, 0);
            lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string("Hazirlaniyor...".ljust(LCD_COLS)[:LCD_COLS])
            time.sleep(1.0)
            print(f"[{os.getpid()}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
        except Exception as e_lcd_init:
            print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}.")
            lcd = None
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
                           start_angle_setting
                           REAL,
                           end_angle_setting
                           REAL,
                           step_angle_setting
                           REAL
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
                       )
                           )''')
        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")
        scan_start_time = time.time()
        cursor.execute(
            "INSERT INTO servo_scans (start_time, status, start_angle_setting, end_angle_setting, step_angle_setting) VALUES (?, ?, ?, ?, ?)",
            (scan_start_time, 'running', INITIAL_GOTO_ANGLE, ACTUAL_SCAN_END_ANGLE, ACTUAL_SCAN_STEP))
        current_scan_id_global = cursor.lastrowid
        conn.commit()
        print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {current_scan_id_global}.")
    except sqlite3.Error as e_db_init:
        print(f"[{os.getpid()}] DB başlatma/tarama kaydı hatası: {e_db_init}")
        current_scan_id_global = None
    finally:
        if conn: conn.close()


def acquire_lock_and_pid():
    global lock_file_handle
    try:
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError:
        pass
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Kilit ve PID oluşturuldu.")
        return True
    except Exception as e:
        print(f"Kilit/PID hatası: {e}")
        if lock_file_handle: lock_file_handle.close()
        lock_file_handle = None
        return False


def _apply_step_to_motor(sequence_index):
    global motor_pins_global
    if not motor_pins_global: return
    step_pattern = STEP_SEQUENCE[sequence_index % len(STEP_SEQUENCE)]
    for i in range(4):
        if motor_pins_global[i] and not motor_pins_global[i].closed:
            motor_pins_global[i].value = step_pattern[i]


def move_motor_to_target_angle_incremental(target_angle_deg, step_delay=STEP_DELAY):
    global current_motor_angle_global, current_motor_step_index_global
    degrees_per_step = 360.0 / STEPS_PER_REVOLUTION
    angle_difference = target_angle_deg - current_motor_angle_global
    if abs(angle_difference) < degrees_per_step:
        current_motor_angle_global = target_angle_deg
        return
    steps_to_move = round(abs(angle_difference) / degrees_per_step)
    if steps_to_move == 0:
        current_motor_angle_global = target_angle_deg
        return
    direction_is_cw = angle_difference > 0
    for _ in range(int(steps_to_move)):
        if direction_is_cw:
            current_motor_step_index_global = (current_motor_step_index_global + 1) % len(STEP_SEQUENCE)
        else:
            current_motor_step_index_global = (current_motor_step_index_global - 1 + len(STEP_SEQUENCE)) % len(
                STEP_SEQUENCE)
        _apply_step_to_motor(current_motor_step_index_global)
        time.sleep(step_delay)
    current_motor_angle_global = target_angle_deg


def calculate_polygon_area_shoelace(points):
    n = len(points);
    area = 0.0
    if n < 3: return 0.0
    for i in range(n):
        x1, y1 = points[i];
        x2, y2 = points[(i + 1) % n]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0


def calculate_perimeter(points_with_origin):
    perimeter = 0.0;
    n = len(points_with_origin)
    if n < 2: return 0.0
    if n > 1: perimeter += math.sqrt(points_with_origin[1][0] ** 2 + points_with_origin[1][1] ** 2)
    for i in range(1, n - 1):
        x1, y1 = points_with_origin[i];
        x2, y2 = points_with_origin[i + 1]
        perimeter += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    if n > 2: perimeter += math.sqrt(points_with_origin[-1][0] ** 2 + points_with_origin[-1][1] ** 2)
    return perimeter


def release_resources_on_exit():
    global lock_file_handle, current_scan_id_global, db_conn_main_script_global, script_exit_status_global, sensor, yellow_led, motor_pins_global, lcd
    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Durum: {script_exit_status_global}")

    if db_conn_main_script_global:
        try:
            db_conn_main_script_global.close()
        except:
            pass

    if current_scan_id_global:
        conn_exit = None
        try:
            conn_exit = sqlite3.connect(DB_PATH)
            cursor_exit = conn_exit.cursor()
            cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (current_scan_id_global,))
            row = cursor_exit.fetchone()
            if row and row[0] == 'running':
                cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?",
                                    (script_exit_status_global, current_scan_id_global))
                conn_exit.commit()
        except Exception as e:
            print(f"DB status update error on exit: {e}")
        finally:
            if conn_exit: conn_exit.close()

    print(f"[{pid}] Donanım kapatılıyor...")
    if motor_pins_global:
        try:
            print(f"[{pid}] Motor merkeze (0°) alınıyor...")
            move_motor_to_target_angle_incremental(0.0)
            time.sleep(0.5)
        except Exception as e:
            print(f"Motoru merkeze alma hatası: {e}")
        finally:
            for pin_obj in motor_pins_global:
                if hasattr(pin_obj, 'close') and not pin_obj.closed:
                    pin_obj.off();
                    pin_obj.close()
            print(f"[{pid}] Step motor pinleri kapatıldı.")

    if yellow_led and hasattr(yellow_led, 'close'):
        if hasattr(yellow_led, 'is_active') and yellow_led.is_active: yellow_led.off()
        yellow_led.close()
    if sensor and hasattr(sensor, 'close'): sensor.close()
    if lcd:
        try:
            lcd.clear(); lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
        except:
            pass
    print(f"[{pid}] Kalan donanımlar kapatıldı.")

    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
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
                    if can_delete: os.remove(f_path); print(f"[{pid}] Silindi: {f_path}")
                elif f_path == LOCK_FILE_PATH:
                    os.remove(f_path);
                    print(f"[{pid}] Silindi: {f_path}")
        except OSError as e_rm:
            print(f"Dosya ({f_path}) silme hatası: {e_rm}")
    print(f"[{pid}] Temizleme fonksiyonu tamamlandı.")


def do_scan_at_angle_and_log(target_scan_angle, current_scan_id, db_connection, phase_description=""):
    global current_motor_angle_global, yellow_led, lcd, sensor, script_exit_status_global, ölçüm_tamponu_hız_için_yerel

    if yellow_led and hasattr(yellow_led, 'toggle'): yellow_led.toggle()

    move_motor_to_target_angle_incremental(target_scan_angle, step_delay=STEP_DELAY)
    time.sleep(STEP_MOTOR_SETTLE_TIME)

    loop_iter_timestamp = time.time()
    distance_m = sensor.distance
    distance_cm = distance_m * 100
    actual_angle_for_calc = current_motor_angle_global
    angle_rad = math.radians(actual_angle_for_calc)
    x_cm = distance_cm * math.cos(angle_rad)
    y_cm = distance_cm * math.sin(angle_rad)

    current_point_xy = None
    if 0 < distance_cm < (sensor.max_distance * 100):
        current_point_xy = (x_cm, y_cm)

    hiz_cm_s = 0.0
    if ölçüm_tamponu_hız_için_yerel:
        son_veri_noktasi = ölçüm_tamponu_hız_için_yerel[-1]
        delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
        delta_zaman = loop_iter_timestamp - son_veri_noktasi['zaman_s']
        if delta_zaman > 0.001: hiz_cm_s = delta_mesafe / delta_zaman

    if lcd:
        try:
            lcd.cursor_pos = (0, 0);
            lcd.write_string(f"A:{actual_angle_for_calc:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1:
                lcd.cursor_pos = (1, 0);
                lcd.write_string(f"{phase_description[:8]} H:{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
        except Exception as e_lcd:
            print(f"LCD yazma hatası ({phase_description}): {e_lcd}")

    if distance_cm < TERMINATION_DISTANCE_CM:
        print(f"DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)!")
        if lcd:
            try:
                lcd.clear();
                lcd.cursor_pos = (0, 0);
                lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                    f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
            except:
                pass
        if yellow_led: yellow_led.on()
        script_exit_status_global = 'terminated_close_object'
        time.sleep(1.0)
        return False, None

    try:
        cursor = db_connection.cursor()
        cursor.execute(
            'INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (current_scan_id, actual_angle_for_calc, distance_cm, hiz_cm_s, loop_iter_timestamp, x_cm, y_cm))
        db_connection.commit()
    except Exception as e_db_insert:
        print(f"DB Ekleme Hatası ({phase_description}): {e_db_insert}")

    ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': loop_iter_timestamp}]
    return True, current_point_xy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motor ile Alan Tarama Betiği")
    parser.add_argument("--initial_goto_angle", type=int, default=DEFAULT_INITIAL_GOTO_ANGLE_ARG,
                        help=f"Tarama öncesi gidilecek ilk açı (varsayılan: {DEFAULT_INITIAL_GOTO_ANGLE_ARG})")
    parser.add_argument("--scan_end_angle", type=int, default=DEFAULT_FINAL_SCAN_ANGLE_ARG,
                        help=f"Taramanın biteceği son açı (varsayılan: {DEFAULT_FINAL_SCAN_ANGLE_ARG})")
    parser.add_argument("--step_angle", type=int, default=DEFAULT_SCAN_STEP_ANGLE_ARG,
                        help=f"Tarama adım açısı (varsayılan: {DEFAULT_SCAN_STEP_ANGLE_ARG})")
    args = parser.parse_args()

    INITIAL_GOTO_ANGLE = args.initial_goto_angle
    ACTUAL_SCAN_END_ANGLE = args.scan_end_angle
    ACTUAL_SCAN_STEP = abs(args.step_angle)
    if ACTUAL_SCAN_STEP == 0: ACTUAL_SCAN_STEP = DEFAULT_SCAN_STEP_ANGLE_ARG

    atexit.register(release_resources_on_exit)

    if not acquire_lock_and_pid(): sys.exit(1)
    if not init_hardware(): sys.exit(1)

    init_db_for_scan()
    if not current_scan_id_global: sys.exit(1)

    ölçüm_tamponu_hız_için_yerel = []

    print(f"[{os.getpid()}] Yeni Tarama Deseni Başlıyor (ID: {current_scan_id_global})...")
    if lcd:
        try:
            lcd.clear();
            lcd.cursor_pos = (0, 0);
            lcd.write_string(f"ID:{current_scan_id_global} Basliyor".ljust(LCD_COLS)[:LCD_COLS])
            if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                f"Hedef:{INITIAL_GOTO_ANGLE} -> {ACTUAL_SCAN_END_ANGLE}".ljust(LCD_COLS)[:LCD_COLS])
        except:
            pass

    scan_aborted_flag = False
    try:
        db_conn_main_script_global = sqlite3.connect(DB_PATH, timeout=10)

        print(f"[{os.getpid()}] İlk pozisyon: {INITIAL_GOTO_ANGLE}°'ye gidiliyor...")
        move_motor_to_target_angle_incremental(float(INITIAL_GOTO_ANGLE))
        time.sleep(0.5)
        print(f"[{os.getpid()}] Motor şimdi {current_motor_angle_global:.1f}° pozisyonunda. Ana tarama başlıyor.")

        scan_direction_step = -ACTUAL_SCAN_STEP
        effective_end_for_scan_range = ACTUAL_SCAN_END_ANGLE + (
            scan_direction_step // abs(scan_direction_step) if scan_direction_step != 0 else -1)

        print(
            f"[{os.getpid()}] Ana Tarama: {current_motor_angle_global:.1f}° -> {ACTUAL_SCAN_END_ANGLE}° (Adım: {scan_direction_step}°)")
        if lcd:
            try:
                lcd.cursor_pos = (1, 0); lcd.write_string(
                    f"Tarama:{current_motor_angle_global:.0f}to{ACTUAL_SCAN_END_ANGLE}".ljust(LCD_COLS)[:LCD_COLS])
            except:
                pass

        continue_scan, _ = do_scan_at_angle_and_log(current_motor_angle_global, current_scan_id_global,
                                                    db_conn_main_script_global,
                                                    f"Scan:{current_motor_angle_global:.0f}°")
        if not continue_scan: scan_aborted_flag = True

        if not scan_aborted_flag:
            for target_angle_in_scan in range(int(current_motor_angle_global + scan_direction_step),
                                              int(effective_end_for_scan_range), scan_direction_step):
                loop_iter_start_time = time.time()
                continue_scan, _ = do_scan_at_angle_and_log(float(target_angle_in_scan), current_scan_id_global,
                                                            db_conn_main_script_global, f"Scan:{target_angle_in_scan}°")
                if not continue_scan:
                    scan_aborted_flag = True;
                    break

                loop_proc_time = time.time() - loop_iter_start_time
                sleep_dur = max(0, LOOP_TARGET_INTERVAL_S - loop_proc_time)
                is_last_scan_step = (target_angle_in_scan == ACTUAL_SCAN_END_ANGLE)
                if sleep_dur > 0 and not is_last_scan_step: time.sleep(sleep_dur)
                if is_last_scan_step: break

        if not scan_aborted_flag: script_exit_status_global = 'completed'

    except KeyboardInterrupt:
        script_exit_status_global = 'interrupted_ctrl_c';
        print(f"\nCtrl+C ile durduruldu.")
        if lcd:
            try:
                lcd.clear();
                lcd.cursor_pos = (0, 0);
                lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS]);
            except: pass
    except Exception as e_main:
        if script_exit_status_global != 'terminated_close_object': script_exit_status_global = 'error_in_loop'
        print(f"Ana döngü hatası veya erken sonlandırma: {e_main}")
        if lcd and script_exit_status_global != 'terminated_close_object':
            try:
                lcd.clear();
                lcd.cursor_pos = (0, 0);
                lcd.write_string(f"Hata:{str(e_main)[:8]}".ljust(LCD_COLS)[:LCD_COLS]);
            except: pass
    finally:
        if db_conn_main_script_global:
            try:
                db_conn_main_script_global.close(); db_conn_main_script_global = None
            except:
                pass

    if not scan_aborted_flag and script_exit_status_global == 'completed' and current_scan_id_global:
        print(f"[{os.getpid()}] Analiz ve son DB işlemleri yapılıyor...")
        conn_analysis = None
        alan, cevre, max_g, max_d = 0.0, 0.0, 0.0, 0.0
        try:
            conn_analysis = sqlite3.connect(DB_PATH)
            cursor_analysis = conn_analysis.cursor()
            max_dist_cm = (sensor.max_distance * 100 if sensor else 200)
            df_all_valid_points = pd.read_sql_query(
                f"SELECT x_cm, y_cm, angle_deg FROM scan_points WHERE scan_id = {current_scan_id_global} AND mesafe_cm > 0.1 AND mesafe_cm < {max_dist_cm} ORDER BY angle_deg ASC",
                conn_analysis)

            if len(df_all_valid_points) >= 2:
                polygon_vertices = [(0.0, 0.0)] + list(zip(df_all_valid_points['x_cm'], df_all_valid_points['y_cm']))
                alan = calculate_polygon_area_shoelace(polygon_vertices)
                cevre = calculate_perimeter(polygon_vertices)
                x_coords = df_all_valid_points['x_cm'].tolist()
                y_coords = df_all_valid_points['y_cm'].tolist()
                max_d = max(x_coords) if x_coords else 0.0
                max_g = (max(y_coords) - min(y_coords)) if y_coords else 0.0
                print(f"TARANAN ALAN ({ACTUAL_SCAN_END_ANGLE}° ile {INITIAL_GOTO_ANGLE}°): {alan:.2f} cm²")
                if lcd:
                    try:
                        lcd.clear(); lcd.cursor_pos = (0, 0); lcd.write_string(f"Alan:{alan:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS]);

                    except: pass

                script_exit_status_global = 'completed_analysis'
                cursor_analysis.execute(
                    "UPDATE servo_scans SET hesaplanan_alan_cm2=?,cevre_cm=?,max_genislik_cm=?,max_derinlik_cm=?,status=? WHERE id=?",
                    (alan, cevre, max_g, max_d, script_exit_status_global, current_scan_id_global))
                conn_analysis.commit()
            else:
                script_exit_status_global = 'completed_insufficient_points'
                if lcd:
                    try:
                        lcd.clear();
                        lcd.cursor_pos = (0, 0);
                        lcd.write_string("Tarama Tamam".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: lcd.cursor_pos = (1, 0); lcd.write_string(
                            "Veri Yetersiz".ljust(LCD_COLS)[:LCD_COLS])
                    except:
                        pass
        except Exception as e_final_db:
            print(f"Son DB işlemleri/Analiz sırasında hata: {e_final_db}")
            if script_exit_status_global == 'completed': script_exit_status_global = 'completed_analysis_error'
        finally:
            if conn_analysis: conn_analysis.close()

    print(f"[{os.getpid()}] Ana işlem bloğu sonlandı. Son durum: {script_exit_status_global}")