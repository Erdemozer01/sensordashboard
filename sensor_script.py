# sensor_script.py (Pozitif Açı Yönetimi ve En Kısa Yol Hesaplanarak)
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

# --- Sabitler (Değişiklik Yok) ---
TRIG_PIN = 23
ECHO_PIN = 24
YELLOW_LED_PIN = 27
MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4 = 5, 6, 13, 19
LCD_I2C_ADDRESS, LCD_PORT_EXPANDER, LCD_COLS, LCD_ROWS, I2C_PORT = 0x27, 'PCF8574', 16, 2, 1
TERMINATION_DISTANCE_CM = 10.0
DEFAULT_INITIAL_GOTO_ANGLE_ARG = 135  # Kavramsal başlangıç
DEFAULT_FINAL_SCAN_ANGLE_ARG = -135  # Kavramsal bitiş (135'ten 270 derece sola)
DEFAULT_SCAN_STEP_ANGLE_ARG = 10
STEP_MOTOR_SETTLE_TIME, LOOP_TARGET_INTERVAL_S = 0.05, 0.15
STEPS_PER_REVOLUTION, STEP_DELAY = 4096, 0.0012
STEP_SEQUENCE = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH, PID_FILE_PATH = '/tmp/sensor_scan_script.lock', '/tmp/sensor_scan_script.pid'

_g_lock_file_handle = None  # Kilit dosyası için global tutucu


def acquire_lock_and_pid():  # Değişiklik yok
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


def release_lock_and_pid_files_on_exit():  # Değişiklik yok
    global _g_lock_file_handle;
    pid = os.getpid()
    print(f"[{pid}] `release_lock_and_pid_files_on_exit` çağrıldı.")
    if _g_lock_file_handle:
        try:
            fcntl.flock(_g_lock_file_handle.fileno(), fcntl.LOCK_UN);
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


# <<< YENİ YARDIMCI FONKSİYON >>>
def normalize_angle(angle_deg):
    """Verilen açıyı 0 ile 359.9... derece arasına normalleştirir."""
    return angle_deg % 360.0


class Scanner:
    def __init__(self, initial_angle_conceptual, end_angle_conceptual, step_angle):
        self.sensor = None;
        self.yellow_led = None;
        self.lcd = None
        self.motor_pins = [];
        self.current_motor_step_index = 0
        # self.current_motor_angle her zaman 0-359.9 aralığında olacak
        self.current_motor_angle = 0.0
        self.current_scan_id = None;
        self.db_conn_local = None
        self.script_exit_status = 'interrupted_unexpectedly'
        self.ölçüm_tamponu_hız_için_yerel = []

        # Gelen kavramsal (negatif veya >360 olabilen) açıları sakla
        self.conceptual_initial_goto_angle = float(initial_angle_conceptual)
        self.conceptual_actual_scan_end_angle = float(end_angle_conceptual)

        self.actual_scan_step = abs(float(step_angle)) or DEFAULT_SCAN_STEP_ANGLE_ARG

    def _init_hardware(self):  # Değişiklik yok (current_motor_angle 0.0 olarak başlar)
        hardware_ok = True
        try:
            print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
            self.sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=5, partial=True)
            self.yellow_led = LED(YELLOW_LED_PIN)
            self.motor_pins = [OutputDevice(p) for p in [MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4]]
            for pin in self.motor_pins: pin.off()
            self.yellow_led.off();
            self.current_motor_angle = 0.0  # Her zaman 0.0 ile başlar
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

    def _init_db_for_scan(self):  # Değişiklik yok
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            # initial_goto_angle_setting vb. sütunlar artık kavramsal açıları saklayacak
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS servo_scans (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL UNIQUE, status TEXT, hesaplanan_alan_cm2 REAL DEFAULT NULL, cevre_cm REAL DEFAULT NULL, max_genislik_cm REAL DEFAULT NULL, max_derinlik_cm REAL DEFAULT NULL, initial_goto_angle_setting REAL, scan_end_angle_setting REAL, scan_step_angle_setting REAL)")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS scan_points (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, angle_deg REAL, mesafe_cm REAL, hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm REAL, FOREIGN KEY(scan_id) REFERENCES servo_scans(id))")
            cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'");
            conn.commit()
            scan_start_time = time.time()
            cursor.execute(
                "INSERT INTO servo_scans (start_time, status, initial_goto_angle_setting, scan_end_angle_setting, scan_step_angle_setting) VALUES (?, ?, ?, ?, ?)",
                (scan_start_time, 'running', self.conceptual_initial_goto_angle, self.conceptual_actual_scan_end_angle,
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

    def _apply_step_to_motor(self, sequence_index):  # Değişiklik yok
        if not self.motor_pins: return
        step_pattern = STEP_SEQUENCE[sequence_index % len(STEP_SEQUENCE)]
        for i in range(4):
            if self.motor_pins[i] and hasattr(self.motor_pins[i], 'value') and not self.motor_pins[i].closed:
                self.motor_pins[i].value = step_pattern[i]

    # <<< MOTOR KONTROL FONKSİYONU TAMAMEN YENİLENDİ >>>
    def _move_motor_to_target_angle_incremental(self, target_angle_conceptual_deg, step_delay=STEP_DELAY):
        degrees_per_step = 360.0 / STEPS_PER_REVOLUTION

        # Hedef açıyı 0-360 aralığına normalleştir
        target_angle_normalized = normalize_angle(target_angle_conceptual_deg)
        # Mevcut açı zaten her zaman 0-360 aralığında tutuluyor

        # En kısa yolu bul (saat yönü vs tersi)
        # Saat yönündeki fark:
        diff_cw = (target_angle_normalized - self.current_motor_angle + 360.0) % 360.0
        # Saat yönü tersindeki fark:
        diff_ccw = (self.current_motor_angle - target_angle_normalized + 360.0) % 360.0

        steps_to_move = 0
        direction_is_cw = True  # Varsayılan yön

        if diff_cw == 0 and diff_ccw == 0:  # Zaten hedefte
            self.current_motor_angle = target_angle_normalized  # Emin olmak için
            return

        if diff_cw <= diff_ccw:
            angle_difference_to_move = diff_cw
            direction_is_cw = True
        else:
            angle_difference_to_move = diff_ccw
            direction_is_cw = False  # Saat yönü tersi

        if abs(angle_difference_to_move) < (degrees_per_step / 2.0):
            self.current_motor_angle = target_angle_normalized
            return

        steps_to_move = round(angle_difference_to_move / degrees_per_step)

        if steps_to_move == 0:
            self.current_motor_angle = target_angle_normalized
            return

        # print(f"Debug Move: Current: {self.current_motor_angle:.2f}, TargetConceptual: {target_angle_conceptual_deg:.2f}, TargetNorm: {target_angle_normalized:.2f}, DiffCW: {diff_cw:.2f}, DiffCCW: {diff_ccw:.2f}, AngleToMove: {angle_difference_to_move:.2f}, Steps: {steps_to_move}, DirCW: {direction_is_cw}")

        for i in range(int(steps_to_move)):
            if direction_is_cw:
                self.current_motor_step_index = (self.current_motor_step_index + 1) % len(STEP_SEQUENCE)
                self.current_motor_angle += degrees_per_step
            else:  # CCW
                self.current_motor_step_index = (self.current_motor_step_index - 1 + len(STEP_SEQUENCE)) % len(
                    STEP_SEQUENCE)
                self.current_motor_angle -= degrees_per_step

            self._apply_step_to_motor(self.current_motor_step_index)
            time.sleep(step_delay)

            # Her adımdan sonra açıyı normalleştir
            self.current_motor_angle = normalize_angle(self.current_motor_angle)

        # Hareket tamamlandıktan sonra, birikmiş float hatalarını önlemek için
        # mevcut açıyı tam olarak hedeflenen normalize edilmiş açıya ayarla.
        self.current_motor_angle = target_angle_normalized
        # print(f"Debug Move End: Final Current Angle: {self.current_motor_angle:.2f}")

    def _do_scan_at_angle_and_log(self, target_conceptual_angle, phase_description=""):
        # Motoru verilen KAVRAMSAL hedef açıya götür. Fonksiyon içerde normalizasyon ve en kısa yolu halleder.
        self._move_motor_to_target_angle_incremental(target_conceptual_angle, step_delay=STEP_DELAY)
        time.sleep(STEP_MOTOR_SETTLE_TIME)

        loop_iter_timestamp = time.time()
        distance_m = self.sensor.distance if self.sensor else float('inf')
        distance_cm = distance_m * 100

        # Loglama ve hesaplamalar için her zaman normalize edilmiş (0-359.9) açıyı kullan
        actual_angle_for_calc_and_log = self.current_motor_angle
        angle_rad = math.radians(
            actual_angle_for_calc_and_log)  # math.cos/sin için açı 0-360 olmak zorunda değil ama tutarlılık için
        x_cm = distance_cm * math.cos(angle_rad)
        y_cm = distance_cm * math.sin(angle_rad)
        # ... (geri kalan hız, LCD, termination check, DB loglama kısımları aynı) ...
        # ... Sadece 'actual_angle_for_calc' yerine 'actual_angle_for_calc_and_log' kullanılıyor. ...
        if self.yellow_led and hasattr(self.yellow_led, 'toggle'): self.yellow_led.toggle()
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
                    f"A:{actual_angle_for_calc_and_log:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
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
                # DB'ye her zaman 0-359.9 aralığındaki açıyı kaydet
                cursor.execute(
                    'INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (self.current_scan_id, actual_angle_for_calc_and_log, distance_cm, hiz_cm_s, loop_iter_timestamp,
                     x_cm, y_cm))
                self.db_conn_local.commit()
            else:
                print(f"[{os.getpid()}] DB bağlantısı yok veya scan_id tanımsız, nokta kaydedilemedi.")
        except Exception as e_db_insert:
            print(f"[{os.getpid()}] DB Ekleme Hatası ({phase_description}): {e_db_insert}")
        return True, current_point_xy

    def _calculate_polygon_area_shoelace(self, points_xy):  # Değişiklik yok
        if len(points_xy) < 2: return 0.0
        polygon_vertices = [(0.0, 0.0)] + points_xy;
        area = 0.0;
        m = len(polygon_vertices)
        if m < 3: return 0.0
        for i in range(m): x1, y1 = polygon_vertices[i]; x2, y2 = polygon_vertices[(i + 1) % m]; area += (x1 * y2) - (
                    x2 * y1)
        return abs(area) / 2.0

    def _calculate_perimeter(self, points_xy):  # Değişiklik yok
        if not points_xy: return 0.0; perimeter = 0.0
        perimeter += math.sqrt(points_xy[0][0] ** 2 + points_xy[0][1] ** 2)
        for i in range(len(points_xy) - 1):
            x1, y1 = points_xy[i];
            x2, y2 = points_xy[i + 1]
            perimeter += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        if len(points_xy) > 0: perimeter += math.sqrt(points_xy[-1][0] ** 2 + points_xy[-1][1] ** 2)
        return perimeter

    def _final_analysis(self):  # Değişiklik yok (DB'den okunan angle_deg zaten 0-359.9 olmalı)
        if not self.current_scan_id: print(f"[{os.getpid()}] Analiz için tarama ID bulunamadı."); return
        print(f"[{os.getpid()}] Analiz ve son DB işlemleri yapılıyor (ID: {self.current_scan_id})...")
        conn_analysis = None;
        alan, cevre, max_g, max_d = 0.0, 0.0, 0.0, 0.0
        try:
            conn_analysis = sqlite3.connect(DB_PATH)
            cursor_analysis = conn_analysis.cursor()
            max_dist_cm_sensor = (self.sensor.max_distance * 100 if self.sensor else 200.0)
            # DB'den angle_deg ASC ile çekildiğinde, 0-359.9 aralığında sıralı gelmeli
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
                self.script_exit_status = 'completed_insufficient_points'  # ... (geri kalanı aynı)
        # ... (try-except-finally bloğunun geri kalanı aynı) ...
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

    def cleanup(self):  # Değişiklik yok (motoru 0'a götürme hala geçerli)
        pid = os.getpid();
        print(f"[{pid}] `Scanner.cleanup` çağrıldı. Son durum: {self.script_exit_status}")
        if self.db_conn_local:
            try:
                self.db_conn_local.close(); self.db_conn_local = None; print(f"[{pid}] Yerel DB bağlantısı kapatıldı.")
            except Exception as e:
                print(f"[{pid}] Yerel DB bağlantısı kapatılırken hata: {e}")
        if self.current_scan_id:  # ... (DB status update aynı)
            conn_exit = None
            try:
                conn_exit = sqlite3.connect(DB_PATH)
                cursor_exit = conn_exit.cursor()
                cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (self.current_scan_id,));
                row = cursor_exit.fetchone()
                if row and (
                        row[0] == 'running' or self.script_exit_status not in ['interrupted_unexpectedly', 'running']):
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
                print(f"[{pid}] Motor merkeze (0°) alınıyor...")  # Buradaki 0.0 kavramsal bir 0'dır
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
        if self.yellow_led and hasattr(self.yellow_led, 'close'):  # ... (geri kalanı aynı)
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

    def run(self):
        if not self._init_hardware(): self.script_exit_status = 'error_hardware_init'; sys.exit(1)
        if not self._init_db_for_scan(): self.script_exit_status = 'error_db_init'; sys.exit(1)

        print(f"[{os.getpid()}] Yeni Tarama Deseni Başlıyor (ID: {self.current_scan_id})...")
        if self.lcd:  # ... (LCD mesajları aynı) ...

            scan_aborted_flag = False

        try:
            self.db_conn_local = sqlite3.connect(DB_PATH, timeout=10)

            # 1. İlk pozisyona git (Kavramsal açı kullanılır, _move_motor... fonksiyonu normalizasyonu halleder)
            print(f"[{os.getpid()}] İlk pozisyon: {self.conceptual_initial_goto_angle}°'ye gidiliyor...")
            self._move_motor_to_target_angle_incremental(self.conceptual_initial_goto_angle)
            time.sleep(0.5)
            print(
                f"[{os.getpid()}] Motor şimdi {self.current_motor_angle:.1f}° (normalize) pozisyonunda. Ana tarama başlıyor.")

            # Tarama döngüsü: Kavramsal açılar üzerinden ilerle
            # Dashboard'dan gelen "scan_arc" mantığına göre, her zaman sola (negatif adım) doğru tarama yapılır.
            # Ancak, _move_motor... en kısa yolu bulacağı için bu sorun değil.
            # Daha basit bir yaklaşım: dashboard'ın hesapladığı conceptual_actual_scan_end_angle'a doğru gitmek.

            current_conceptual_target_for_scan = self.conceptual_initial_goto_angle
            target_conceptual_end_angle = self.conceptual_actual_scan_end_angle

            # Adım yönünü belirle (kavramsal açılara göre)
            # Eğer dashboard initial=135, arc=270 -> end=-135 gönderdiyse, direction negatif olacak.
            scan_direction_sign = -1.0 if current_conceptual_target_for_scan > target_conceptual_end_angle else 1.0

            # İlk noktayı kaydet
            print(
                f"[{os.getpid()}] Ana Tarama: {current_conceptual_target_for_scan:.1f}° -> {target_conceptual_end_angle}° (Adım: {self.actual_scan_step * scan_direction_sign:.1f}°, Normalize Edilmiş Açı Gösterilir)")
            if self.lcd:  # ... (LCD mesajı) ...

                continue_scan, _ = self._do_scan_at_angle_and_log(current_conceptual_target_for_scan,
                                                              f"Scan:{normalize_angle(current_conceptual_target_for_scan):.0f}°")
            if not continue_scan: scan_aborted_flag = True

            if not scan_aborted_flag:
                # Toplam kaç adım atılacağını hesapla (kavramsal açılar üzerinden)
                total_conceptual_span = abs(target_conceptual_end_angle - current_conceptual_target_for_scan)
                num_remaining_steps_in_scan = 0
                if self.actual_scan_step > 1e-3:  # Sıfıra bölmeyi engelle
                    num_remaining_steps_in_scan = math.floor(total_conceptual_span / self.actual_scan_step)

                for i in range(num_remaining_steps_in_scan):
                    loop_iter_start_time = time.time()
                    current_conceptual_target_for_scan += (self.actual_scan_step * scan_direction_sign)

                    continue_scan, _ = self._do_scan_at_angle_and_log(current_conceptual_target_for_scan,
                                                                      f"Scan:{normalize_angle(current_conceptual_target_for_scan):.0f}°")
                    if not continue_scan: scan_aborted_flag = True; break

                    loop_proc_time = time.time() - loop_iter_start_time
                    sleep_dur = max(0, LOOP_TARGET_INTERVAL_S - loop_proc_time)
                    if sleep_dur > 0 and (i < num_remaining_steps_in_scan - 1):  # Son adımda bekleme
                        time.sleep(sleep_dur)

                # Son hedef açıya tam olarak gitmek ve ölçüm yapmak (eğer arada durmadıysak)
                if not scan_aborted_flag:
                    # Kavramsal bitiş açısında son bir ölçüm (eğer döngü tam oraya gelmediyse)
                    # self.current_motor_angle zaten son pozisyonda olmalı (normalize edilmiş).
                    # conceptual_target_conceptual_end_angle'a gitmeyi deneyelim.
                    # Fark çok küçükse zaten gitmeyecek.
                    print(
                        f"[{os.getpid()}] Son hedef açı ({target_conceptual_end_angle}° kavramsal) için ek ölçüm/pozisyonlama.")
                    continue_scan, _ = self._do_scan_at_angle_and_log(target_conceptual_end_angle,
                                                                      f"ScanEnd:{normalize_angle(target_conceptual_end_angle):.0f}°")
                    if not continue_scan: scan_aborted_flag = True

            if not scan_aborted_flag: self.script_exit_status = 'completed'
        # ... (try-except-finally bloğunun geri kalanı aynı) ...
        except KeyboardInterrupt:
            self.script_exit_status = 'interrupted_ctrl_c';
            print(f"\n[{os.getpid()}] Ctrl+C ile durduruldu.")
            if self.lcd:
                try: self.lcd.clear(); self.lcd.cursor_pos = (0, 0); self.lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS])
                except: pass

        except Exception as e_main:
            if self.script_exit_status != 'terminated_close_object': self.script_exit_status = 'error_in_loop'
            print(f"[{os.getpid()}] Ana döngü hatası: {e_main}"); import traceback; traceback.print_exc()
            if self.lcd and self.script_exit_status != 'terminated_close_object':
                try: self.lcd.clear(); self.lcd.cursor_pos = (0, 0); self.lcd.write_string(f"Hata:{str(e_main)[:8]}".ljust(LCD_COLS)[:LCD_COLS])
                except: pass
        finally:
            if self.db_conn_local:
                try: self.db_conn_local.close(); self.db_conn_local = None; print(f"[{os.getpid()}] Tarama sonrası yerel DB bağlantısı kapatıldı.")
                except Exception as e: print(f"[{os.getpid()}] Tarama sonrası yerel DB kapatma hatası: {e}")
        if not scan_aborted_flag and self.script_exit_status == 'completed' and self.current_scan_id:
            self._final_analysis()
        print(f"[{os.getpid()}] Ana işlem bloğu sonlandı. Son durum: {self.script_exit_status}")

if __name__ == "__main__":  # Değişiklik yok
    parser = argparse.ArgumentParser(description="Step Motor ile Alan Tarama Betiği (Sınıf Tabanlı - Pozitif Açı)")
    parser.add_argument("--initial_goto_angle", type=float, default=DEFAULT_INITIAL_GOTO_ANGLE_ARG)
    parser.add_argument("--scan_end_angle", type=float, default=DEFAULT_FINAL_SCAN_ANGLE_ARG)
    parser.add_argument("--scan_step_angle", type=float, default=DEFAULT_SCAN_STEP_ANGLE_ARG)
    args = parser.parse_args()
    if not acquire_lock_and_pid(): sys.exit(1)
    scanner_app = Scanner(initial_angle_conceptual=args.initial_goto_angle,
                          end_angle_conceptual=args.scan_end_angle,
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
