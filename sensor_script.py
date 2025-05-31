# sensor_script.py (Motor Kontrol Düzeltmesi)
from gpiozero import DistanceSensor, LED, OutputDevice
from RPLCD.i2c import CharLCD
import time
import sqlite3
import os
import sys
import fcntl  # Linux'a özgü
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
DEFAULT_INITIAL_GOTO_ANGLE_ARG = 135
DEFAULT_FINAL_SCAN_ANGLE_ARG = -135
DEFAULT_SCAN_STEP_ANGLE_ARG = 10

# --- Step Motor Ayarları ---
STEP_MOTOR_SETTLE_TIME = 0.05
LOOP_TARGET_INTERVAL_S = 0.15
STEPS_PER_REVOLUTION = 4096
STEP_DELAY = 0.001
STEP_SEQUENCE = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]

# --- Dosya Yolları ---
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'

_g_lock_file_handle = None


def acquire_lock_and_pid():
    global _g_lock_file_handle
    try:
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError as e:
        print(f"[{os.getpid()}] Uyarı: Eski PID dosyası silinemedi: {e}")
    try:
        _g_lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(_g_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Kilit ve PID ({os.getpid()}) oluşturuldu.")
        return True
    except (IOError, OSError) as e:
        print(f"[{os.getpid()}] Kilit/PID hatası: {e}")
        if _g_lock_file_handle: _g_lock_file_handle.close(); _g_lock_file_handle = None
        return False


def release_lock_and_pid_files_on_exit():
    global _g_lock_file_handle
    pid = os.getpid()
    print(f"[{pid}] `release_lock_and_pid_files_on_exit` çağrıldı.")
    if _g_lock_file_handle:
        try:
            fcntl.flock(_g_lock_file_handle.fileno(), fcntl.LOCK_UN)
            _g_lock_file_handle.close();
            _g_lock_file_handle = None
            print(f"[{pid}] Kilit dosyası serbest bırakıldı.")
        except Exception as e:
            print(f"[{pid}] Kilit serbest bırakma hatası: {e}")
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_delete = False
                    try:
                        with open(f_path, 'r') as pf:
                            if int(pf.read().strip()) == pid: can_delete = True
                    except:
                        pass
                    if can_delete:
                        os.remove(f_path); print(f"[{pid}] Silindi: {f_path}")
                    elif os.path.exists(f_path):
                        print(f"[{pid}] {f_path} başka processe ait, silinmedi.")
                else:
                    os.remove(f_path); print(f"[{pid}] Silindi: {f_path}")
        except OSError as e_rm:
            print(f"[{pid}] Dosya ({f_path}) silme hatası: {e_rm}")


class Scanner:
    def __init__(self, initial_angle, end_angle, step_angle):
        self.sensor = None
        self.yellow_led = None
        self.lcd = None
        self.motor_pins = []
        self.current_motor_step_index = 0
        self.current_motor_angle = 0.0
        self.current_scan_id = None
        self.db_conn_local = None
        self.script_exit_status = 'interrupted_unexpectedly'
        self.ölçüm_tamponu_hız_için_yerel = []
        self.initial_goto_angle = initial_angle
        self.actual_scan_end_angle = end_angle
        self.actual_scan_step = abs(step_angle) or DEFAULT_SCAN_STEP_ANGLE_ARG

    def _init_hardware(self):
        hardware_ok = True
        try:
            print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
            self.sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=5, partial=True)
            self.yellow_led = LED(YELLOW_LED_PIN)
            self.motor_pins = [OutputDevice(p) for p in [MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4]]
            for pin in self.motor_pins: pin.off()
            self.yellow_led.off();
            self.current_motor_angle = 0.0
            print(f"[{os.getpid()}] Temel donanımlar ve Step Motor başarıyla başlatıldı.")
        except Exception as e:
            print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}");
            hardware_ok = False
        if hardware_ok:
            try:
                self.lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                                   cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
                self.lcd.clear();
                self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(
                    "Hazirlaniyor...".ljust(LCD_COLS)[:LCD_COLS])
                time.sleep(1.0);
                print(f"[{os.getpid()}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
            except Exception as e_lcd_init:
                print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}. LCD devre dışı.");
                self.lcd = None
        else:
            self.lcd = None
        return hardware_ok

    def _init_db_for_scan(self):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS servo_scans (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL UNIQUE, status TEXT, hesaplanan_alan_cm2 REAL DEFAULT NULL, cevre_cm REAL DEFAULT NULL, max_genislik_cm REAL DEFAULT NULL, max_derinlik_cm REAL DEFAULT NULL, initial_goto_angle_setting REAL, scan_end_angle_setting REAL, scan_step_angle_setting REAL)")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS scan_points (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, angle_deg REAL, mesafe_cm REAL, hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm REAL, FOREIGN KEY(scan_id) REFERENCES servo_scans(id))")
            cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'");
            conn.commit()
            scan_start_time = time.time()
            cursor.execute(
                "INSERT INTO servo_scans (start_time, status, initial_goto_angle_setting, scan_end_angle_setting, scan_step_angle_setting) VALUES (?, ?, ?, ?, ?)",
                (scan_start_time, 'running', self.initial_goto_angle, self.actual_scan_end_angle,
                 self.actual_scan_step))
            self.current_scan_id = cursor.lastrowid;
            conn.commit()
            print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {self.current_scan_id}.")
            return True
        except sqlite3.Error as e_db_init:
            print(f"[{os.getpid()}] DB başlatma/tarama kaydı hatası: {e_db_init}");
            self.current_scan_id = None;
            return False
        finally:
            if conn: conn.close()

    def _apply_step_to_motor(self, sequence_index):
        if not self.motor_pins: return
        step_pattern = STEP_SEQUENCE[sequence_index % len(STEP_SEQUENCE)]
        for i in range(4):
            if self.motor_pins[i] and hasattr(self.motor_pins[i], 'value') and not self.motor_pins[i].closed:
                self.motor_pins[i].value = step_pattern[i]

    def _move_motor_to_target_angle_incremental(self, target_angle_deg, step_delay=STEP_DELAY):
        # <<< DÜZELTME BURADA >>>
        degrees_per_step = 360.0 / STEPS_PER_REVOLUTION  # len(STEP_SEQUENCE)'e bölme kaldırıldı.

        angle_difference = target_angle_deg - self.current_motor_angle

        if abs(angle_difference) < (degrees_per_step / 2.0):  # Çok küçük farkları ihmal et
            # self.current_motor_angle = target_angle_deg # Hedefe çok yakınsa direkt ayarla
            return

        steps_to_move_float = angle_difference / degrees_per_step
        steps_to_move = round(steps_to_move_float)

        if steps_to_move == 0:
            # self.current_motor_angle = target_angle_deg # Eğer adım sayısı 0 ise hedefteyiz demektir.
            return

        direction_is_cw = steps_to_move > 0

        for _ in range(abs(int(steps_to_move))):
            if direction_is_cw:
                self.current_motor_step_index = (self.current_motor_step_index + 1) % len(STEP_SEQUENCE)
            else:
                self.current_motor_step_index = (self.current_motor_step_index - 1 + len(STEP_SEQUENCE)) % len(
                    STEP_SEQUENCE)

            self._apply_step_to_motor(self.current_motor_step_index)
            time.sleep(step_delay)
            # Her adımdan sonra açıyı KÜMÜLATİF olarak güncellemek yerine,
            # adım sayısına göre teorik pozisyonu takip etmek daha iyi olabilir.
            # Ancak mevcut yöntem de dikkatli kullanılırsa çalışır.
            # Şimdilik mevcut kümülatif güncellemeyi koruyoruz ama en sonda hedef açıya eşitleyeceğiz.

        # <<< EKLEME: Hareket sonrası açıyı tam hedef açıya eşitle >>>
        # Bu, birikmiş float hatalarını ve yuvarlama farklarını düzeltir.
        self.current_motor_angle = target_angle_deg
        # print(f"Motor {target_angle_deg}° hedefine ulaştı. Mevcut Açı: {self.current_motor_angle:.2f}°")

    def _do_scan_at_angle_and_log(self, target_scan_angle, phase_description=""):
        if self.yellow_led and hasattr(self.yellow_led, 'toggle'): self.yellow_led.toggle()
        self._move_motor_to_target_angle_incremental(target_scan_angle, step_delay=STEP_DELAY)
        time.sleep(STEP_MOTOR_SETTLE_TIME)
        loop_iter_timestamp = time.time()
        distance_m = self.sensor.distance if self.sensor else float('inf')
        distance_cm = distance_m * 100
        actual_angle_for_calc = self.current_motor_angle
        angle_rad = math.radians(actual_angle_for_calc)
        x_cm = distance_cm * math.cos(angle_rad);
        y_cm = distance_cm * math.sin(angle_rad)
        current_point_xy = (x_cm, y_cm) if 0 < distance_cm < (
            self.sensor.max_distance * 100 if self.sensor else float('inf')) else None
        hiz_cm_s = 0.0
        if self.ölçüm_tamponu_hız_için_yerel:
            son_veri_noktasi = self.ölçüm_tamponu_hız_için_yerel[-1]
            delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
            delta_zaman = loop_iter_timestamp - son_veri_noktasi['zaman_s']
            if delta_zaman > 0.001: hiz_cm_s = delta_mesafe / delta_zaman
        self.ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': loop_iter_timestamp}]
        if self.lcd:
            try:
                self.lcd.cursor_pos = (0, 0);
                self.lcd.write_string(
                    f"A:{actual_angle_for_calc:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(
                    f"{phase_description[:8]} H:{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
            except Exception as e_lcd:
                print(f"[{os.getpid()}] LCD yazma hatası ({phase_description}): {e_lcd}")
        if distance_cm < TERMINATION_DISTANCE_CM:
            print(f"[{os.getpid()}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Tarama durduruluyor.")
            if self.lcd:
                try:
                    self.lcd.clear();
                    self.lcd.cursor_pos = (0, 0);
                    self.lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(
                        f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
                except:
                    pass
            if self.yellow_led and hasattr(self.yellow_led, 'on'): self.yellow_led.on()
            self.script_exit_status = 'terminated_close_object';
            time.sleep(1.0);
            return False, None
        try:
            if self.db_conn_local and self.current_scan_id:
                cursor = self.db_conn_local.cursor()
                cursor.execute(
                    'INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (self.current_scan_id, actual_angle_for_calc, distance_cm, hiz_cm_s, loop_iter_timestamp, x_cm,
                     y_cm))
                self.db_conn_local.commit()
            else:
                print(f"[{os.getpid()}] DB bağlantısı yok veya scan_id tanımsız, nokta kaydedilemedi.")
        except Exception as e_db_insert:
            print(f"[{os.getpid()}] DB Ekleme Hatası ({phase_description}): {e_db_insert}")
        return True, current_point_xy

    def _calculate_polygon_area_shoelace(self, points_xy):
        if len(points_xy) < 2: return 0.0  # Orijinle birlikte en az 3 nokta gerekir
        polygon_vertices = [(0.0, 0.0)] + points_xy;
        area = 0.0;
        m = len(polygon_vertices)
        if m < 3: return 0.0
        for i in range(m): x1, y1 = polygon_vertices[i]; x2, y2 = polygon_vertices[(i + 1) % m]; area += (x1 * y2) - (
                    x2 * y1)
        return abs(area) / 2.0

    def _calculate_perimeter(self, points_xy):
        if not points_xy: return 0.0; perimeter = 0.0
        perimeter += math.sqrt(points_xy[0][0] ** 2 + points_xy[0][1] ** 2)
        for i in range(len(points_xy) - 1):
            x1, y1 = points_xy[i];
            x2, y2 = points_xy[i + 1]
            perimeter += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        if len(points_xy) > 0: perimeter += math.sqrt(points_xy[-1][0] ** 2 + points_xy[-1][1] ** 2)
        return perimeter

    def _final_analysis(self):
        if not self.current_scan_id: print(f"[{os.getpid()}] Analiz için tarama ID bulunamadı."); return
        print(f"[{os.getpid()}] Analiz ve son DB işlemleri yapılıyor (ID: {self.current_scan_id})...")
        conn_analysis = None;
        alan, cevre, max_g, max_d = 0.0, 0.0, 0.0, 0.0
        try:
            conn_analysis = sqlite3.connect(DB_PATH)
            cursor_analysis = conn_analysis.cursor()
            max_dist_cm_sensor = (self.sensor.max_distance * 100 if self.sensor else 200.0)
            df_all_valid_points = pd.read_sql_query(
                f"SELECT x_cm, y_cm, angle_deg FROM scan_points WHERE scan_id = {self.current_scan_id} AND mesafe_cm > 0.1 AND mesafe_cm < {max_dist_cm_sensor} ORDER BY angle_deg ASC",
                conn_analysis)
            if len(df_all_valid_points) >= 2:
                points_for_calc = list(zip(df_all_valid_points['x_cm'], df_all_valid_points['y_cm']))
                alan = self._calculate_polygon_area_shoelace(points_for_calc)
                cevre = self._calculate_perimeter(points_for_calc)
                x_coords = df_all_valid_points['x_cm'].tolist();
                y_coords = df_all_valid_points['y_cm'].tolist()
                max_d = max(x_coords) if x_coords else 0.0;
                min_y = min(y_coords) if y_coords else 0.0
                max_y = max(y_coords) if y_coords else 0.0;
                max_g = max_y - min_y
                print(f"[{os.getpid()}] TARANAN ALAN: {alan:.2f} cm²")
                if self.lcd:
                    try:
                        self.lcd.clear();
                        self.lcd.cursor_pos = (0, 0);
                        self.lcd.write_string(f"Alan:{alan:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(
                            f"Cevre:{cevre:.0f}cm".ljust(LCD_COLS)[:LCD_COLS])
                    except:
                        pass
                self.script_exit_status = 'completed_analysis'
                cursor_analysis.execute(
                    "UPDATE servo_scans SET hesaplanan_alan_cm2=?, cevre_cm=?, max_genislik_cm=?, max_derinlik_cm=?, status=? WHERE id=?",
                    (alan, cevre, max_g, max_d, self.script_exit_status, self.current_scan_id));
                conn_analysis.commit()
            else:
                self.script_exit_status = 'completed_insufficient_points'
                print(f"[{os.getpid()}] Analiz için yeterli nokta bulunamadı ({len(df_all_valid_points)}).")
                if self.lcd:
                    try:
                        self.lcd.clear();
                        self.lcd.cursor_pos = (0, 0);
                        self.lcd.write_string("Tarama Tamam".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(
                            "Veri Yetersiz".ljust(LCD_COLS)[:LCD_COLS])
                    except:
                        pass
                cursor_analysis.execute("UPDATE servo_scans SET status=? WHERE id=?",
                                        (self.script_exit_status, self.current_scan_id));
                conn_analysis.commit()
        except Exception as e_final_db:
            print(f"[{os.getpid()}] Son DB işlemleri/Analiz sırasında hata: {e_final_db}")
            if self.script_exit_status.startswith('completed'): self.script_exit_status = 'completed_analysis_error'
            try:
                if conn_analysis and self.current_scan_id:
                    cursor_analysis = conn_analysis.cursor()
                    cursor_analysis.execute("UPDATE servo_scans SET status=? WHERE id=?",
                                            (self.script_exit_status, self.current_scan_id));
                    conn_analysis.commit()
            except Exception as e_status_update:
                print(f"[{os.getpid()}] Hata durumu güncellenirken ek hata: {e_status_update}")
        finally:
            if conn_analysis: conn_analysis.close()

    def cleanup(self):
        pid = os.getpid();
        print(f"[{pid}] `Scanner.cleanup` çağrıldı. Son durum: {self.script_exit_status}")
        if self.db_conn_local:
            try:
                self.db_conn_local.close(); self.db_conn_local = None; print(f"[{pid}] Yerel DB bağlantısı kapatıldı.")
            except Exception as e:
                print(f"[{pid}] Yerel DB bağlantısı kapatılırken hata: {e}")
        if self.current_scan_id:
            conn_exit = None
            try:
                conn_exit = sqlite3.connect(DB_PATH)
                cursor_exit = conn_exit.cursor()
                cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (self.current_scan_id,));
                row = cursor_exit.fetchone()
                if row and (
                        row[0] == 'running' or self.script_exit_status not in ['interrupted_unexpectedly', 'running']):
                    print(
                        f"[{pid}] DB'deki tarama (ID: {self.current_scan_id}) durumu '{self.script_exit_status}' olarak güncelleniyor.")
                    cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?",
                                        (self.script_exit_status, self.current_scan_id));
                    conn_exit.commit()
            except Exception as e:
                print(f"[{pid}] DB durum güncelleme hatası (çıkışta): {e}")
            finally:
                if conn_exit: conn_exit.close()
        print(f"[{pid}] Donanım kapatılıyor...")
        if self.motor_pins:
            try:
                print(f"[{pid}] Motor merkeze (0°) alınıyor...")
                self._move_motor_to_target_angle_incremental(0.0, step_delay=STEP_DELAY * 0.8);
                time.sleep(0.2)
                for pin_obj in self.motor_pins:
                    if hasattr(pin_obj, 'off'): pin_obj.off()
            except Exception as e:
                print(f"[{pid}] Motoru merkeze alma hatası: {e}")
            finally:
                for pin_obj in self.motor_pins:
                    if hasattr(pin_obj, 'close') and not pin_obj.closed: pin_obj.close()
                self.motor_pins = [];
                print(f"[{pid}] Step motor pinleri kapatıldı.")
        if self.yellow_led and hasattr(self.yellow_led, 'close'):
            if hasattr(self.yellow_led, 'is_active') and self.yellow_led.is_active: self.yellow_led.off()
            self.yellow_led.close();
            self.yellow_led = None;
            print(f"[{pid}] Sarı LED kapatıldı.")
        if self.sensor and hasattr(self.sensor, 'close'):
            self.sensor.close();
            self.sensor = None;
            print(f"[{pid}] Mesafe Sensörü kapatıldı.")
        if self.lcd:
            try:
                self.lcd.clear();
                self.lcd.cursor_pos = (0, 0);
                self.lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(
                    f"PID:{pid} Son".ljust(LCD_COLS)[:LCD_COLS])
            except Exception as e:
                print(f"[{pid}] LCD temizleme/kapatma mesajı hatası: {e}")
            finally:
                self.lcd = None; print(f"[{pid}] LCD kapatıldı.")
        print(f"[{pid}] `Scanner.cleanup` tamamlandı.")

    # sensor_script.py -> Scanner sınıfı -> run() metodu:
    def run(self):
        if not self._init_hardware():
            self.script_exit_status = 'error_hardware_init'
            sys.exit(1)  # atexit cleanup'ı çağıracak

        # Veritabanı başlatmayı bu test için atlayabiliriz veya açık bırakabiliriz
        # if not self._init_db_for_scan():
        #     self.script_exit_status = 'error_db_init'
        #     sys.exit(1)

        print(f"[{os.getpid()}] >>> Basit Açı Testi Başlıyor... <<<")

        # TEST 1: Sağa 90 derece
        test_target_angle_1 = 120
        print(f"Motor +{test_target_angle_1}° pozisyonuna götürülüyor...")
        print(f"Mevcut Açı (önce): {self.current_motor_angle:.2f}°")
        self._move_motor_to_target_angle_incremental(test_target_angle_1)
        print(f"Motor hareket tamamlandı. Sonraki Mevcut Açı: {self.current_motor_angle:.2f}°")
        time.sleep(1)  # Motorun pozisyonunu gözlemlemek için 3 saniye bekle

        # TEST 2: Sola (-45 dereceye, yani 0'dan -45'e)
        test_target_angle_2 = -120
        print(f"Motor {test_target_angle_2}° pozisyonuna götürülüyor...")
        print(f"Mevcut Açı (önce): {self.current_motor_angle:.2f}°")
        self._move_motor_to_target_angle_incremental(test_target_angle_2)
        print(f"Motor hareket tamamlandı. Sonraki Mevcut Açı: {self.current_motor_angle:.2f}°")
        time.sleep(1)

        # TEST 3: Tekrar 0 dereceye (merkeze)
        test_target_angle_3 = 0.0
        print(f"Motor {test_target_angle_3}° pozisyonuna götürülüyor...")
        print(f"Mevcut Açı (önce): {self.current_motor_angle:.2f}°")
        self._move_motor_to_target_angle_incremental(test_target_angle_3)
        print(f"Motor hareket tamamlandı. Sonraki Mevcut Açı: {self.current_motor_angle:.2f}°")
        time.sleep(1)

        print(f"[{os.getpid()}] >>> Basit Açı Testi Bitti. Betik sonlandırılıyor. <<<")
        self.script_exit_status = 'test_completed'  # Durumu ayarla
        # Testten sonra betiğin normal tarama döngüsüne girmesini engelle:
        return  # veya sys.exit("Açı testi tamamlandı.") kullanabilirsiniz.
        # return, __main__ bloğundaki finally'nin çalışmasını sağlar,
        # sys.exit() ise atexit'leri doğrudan tetikler.
        # atexit zaten kayıtlı olduğu için return yeterli.

        # --- BURADAN SONRASI NORMAL run() METODUNUN DEVAMIYDI, TEST İÇİN YORUMA ALINDI ---
        # print(f"[{os.getpid()}] Yeni Tarama Deseni Başlıyor (ID: {self.current_scan_id})...")
        # ... (run metodunun geri kalanı)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motor ile Alan Tarama Betiği (Sınıf Tabanlı)")
    parser.add_argument("--initial_goto_angle", type=int, default=DEFAULT_INITIAL_GOTO_ANGLE_ARG)
    parser.add_argument("--scan_end_angle", type=int, default=DEFAULT_FINAL_SCAN_ANGLE_ARG)
    parser.add_argument("--scan_step_angle", type=int, default=DEFAULT_SCAN_STEP_ANGLE_ARG)
    args = parser.parse_args()
    if not acquire_lock_and_pid(): sys.exit(1)
    scanner_app = Scanner(initial_angle=args.initial_goto_angle, end_angle=args.scan_end_angle,
                          step_angle=args.scan_step_angle)
    atexit.register(scanner_app.cleanup)
    atexit.register(release_lock_and_pid_files_on_exit)
    try:
        scanner_app.run()
    except Exception as e:
        print(f"[{os.getpid()}] Beklenmedik ana hata: {e}");
        import traceback;

        traceback.print_exc()
        scanner_app.script_exit_status = 'error_unhandled_exception'
    finally:
        print(f"[{os.getpid()}] __main__ bloğu sonlanıyor.")

