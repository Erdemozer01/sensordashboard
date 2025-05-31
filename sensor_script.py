# sensor_script.py (Göreli Açılar Her Zaman Pozitif Artacak Şekilde Güncellendi)
from gpiozero import DistanceSensor, LED, OutputDevice
from RPLCD.i2c import CharLCD
import time
import sqlite3
import os
import sys
import fcntl # Linux'a özgü
import atexit
import math
import argparse
import pandas as pd

# --- Sabitler ---
TRIG_PIN = 23
ECHO_PIN = 24
YELLOW_LED_PIN = 27
MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4 = 5, 6, 13, 19
LCD_I2C_ADDRESS, LCD_PORT_EXPANDER, LCD_COLS, LCD_ROWS, I2C_PORT = 0x27, 'PCF8574', 16, 2, 1
TERMINATION_DISTANCE_CM = 10.0
DEFAULT_INITIAL_GOTO_ANGLE_ARG = 135
DEFAULT_FINAL_SCAN_ANGLE_ARG = -135
DEFAULT_SCAN_STEP_ANGLE_ARG = 10
STEP_MOTOR_SETTLE_TIME, LOOP_TARGET_INTERVAL_S = 0.05, 0.15
STEPS_PER_REVOLUTION, STEP_DELAY = 4096, 0.0012
STEP_SEQUENCE = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH, PID_FILE_PATH = '/tmp/sensor_scan_script.lock', '/tmp/sensor_scan_script.pid'

_g_lock_file_handle = None

def acquire_lock_and_pid():
    global _g_lock_file_handle
    try:
        if os.path.exists(PID_FILE_PATH): os.remove(PID_FILE_PATH)
    except OSError as e: print(f"[{os.getpid()}] Uyarı: Eski PID dosyası silinemedi: {e}")
    try:
        _g_lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(_g_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf: pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Kilit ve PID ({os.getpid()}) oluşturuldu.")
        return True
    except (IOError, OSError) as e:
        print(f"[{os.getpid()}] Kilit/PID hatası: {e}")
        if _g_lock_file_handle: _g_lock_file_handle.close(); _g_lock_file_handle = None
        return False

def release_lock_and_pid_files_on_exit():
    global _g_lock_file_handle; pid = os.getpid()
    print(f"[{pid}] `release_lock_and_pid_files_on_exit` çağrıldı.")
    if _g_lock_file_handle:
        try:
            fcntl.flock(_g_lock_file_handle.fileno(), fcntl.LOCK_UN); _g_lock_file_handle.close(); _g_lock_file_handle = None
            print(f"[{pid}] Kilit dosyası serbest bırakıldı.")
        except Exception as e: print(f"[{pid}] Kilit serbest bırakma hatası: {e}")
    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    can_delete = False
                    try:
                        with open(f_path, 'r') as pf:
                            if int(pf.read().strip()) == pid: can_delete = True
                    except: pass
                    if can_delete: os.remove(f_path); print(f"[{pid}] Silindi: {f_path}")
                    elif os.path.exists(f_path): print(f"[{pid}] {f_path} başka processe ait, silinmedi.")
                else: os.remove(f_path); print(f"[{pid}] Silindi: {f_path}")
        except OSError as e_rm: print(f"[{pid}] Dosya ({f_path}) silme hatası: {e_rm}")

def normalize_angle_0_360(angle_deg):
    return angle_deg % 360.0

class Scanner:
    def __init__(self, initial_angle_conceptual, end_angle_conceptual, step_angle):
        self.sensor = None; self.yellow_led = None; self.lcd = None
        self.motor_pins = []; self.current_motor_step_index = 0
        self.current_physical_motor_angle = 0.0
        self.relative_zero_physical_angle = 0.0
        self.current_scan_id = None; self.db_conn_local = None
        self.script_exit_status = 'interrupted_unexpectedly'
        self.ölçüm_tamponu_hız_için_yerel = []
        self.conceptual_initial_goto_physical_angle = float(initial_angle_conceptual)
        self.conceptual_final_physical_angle = float(end_angle_conceptual)
        self.scan_arc_degrees = abs(self.conceptual_final_physical_angle - self.conceptual_initial_goto_physical_angle)
        self.scan_direction_sign = -1.0 if self.conceptual_initial_goto_physical_angle > self.conceptual_final_physical_angle else 1.0
        self.actual_scan_step = abs(float(step_angle)) or DEFAULT_SCAN_STEP_ANGLE_ARG
        # print(f"DEBUG_INIT: InitialConceptual: {self.conceptual_initial_goto_physical_angle}, FinalConceptual: {self.conceptual_final_physical_angle}, Arc: {self.scan_arc_degrees}, DirectionSign: {self.scan_direction_sign}, Step: {self.actual_scan_step}")

    def _init_hardware(self): # Değişiklik yok
        hardware_ok = True
        try:
            self.sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=5, partial=True)
            self.yellow_led = LED(YELLOW_LED_PIN)
            self.motor_pins = [OutputDevice(p) for p in [MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4]]
            for pin in self.motor_pins: pin.off()
            self.yellow_led.off(); self.current_physical_motor_angle = 0.0
        except Exception as e: hardware_ok = False; print(f"Temel donanım hatası: {e}")
        if hardware_ok:
            try:
                self.lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
                self.lcd.clear(); self.lcd.write_string("Dream Pi Hazir"); time.sleep(1)
            except Exception as e_lcd: self.lcd = None; print(f"LCD hatası: {e_lcd}")
        return hardware_ok

    def _init_db_for_scan(self): # Değişiklik yok
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS servo_scans (id INTEGER PRIMARY KEY AUTOINCREMENT, start_time REAL UNIQUE, status TEXT, hesaplanan_alan_cm2 REAL DEFAULT NULL, cevre_cm REAL DEFAULT NULL, max_genislik_cm REAL DEFAULT NULL, max_derinlik_cm REAL DEFAULT NULL, initial_goto_angle_setting REAL, scan_end_angle_setting REAL, scan_step_angle_setting REAL)")
            cursor.execute("CREATE TABLE IF NOT EXISTS scan_points (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, angle_deg REAL, mesafe_cm REAL, hiz_cm_s REAL, timestamp REAL, x_cm REAL, y_cm REAL, FOREIGN KEY(scan_id) REFERENCES servo_scans(id))")
            cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'"); conn.commit()
            scan_start_time = time.time()
            cursor.execute("INSERT INTO servo_scans (start_time, status, initial_goto_angle_setting, scan_end_angle_setting, scan_step_angle_setting) VALUES (?, ?, ?, ?, ?)",
                           (scan_start_time, 'running', self.conceptual_initial_goto_physical_angle, self.conceptual_final_physical_angle, self.actual_scan_step))
            self.current_scan_id = cursor.lastrowid; conn.commit()
            return True
        except sqlite3.Error as e_db: print(f"DB hatası: {e_db}"); return False
        finally:
            if conn: conn.close()

    def _apply_step_to_motor(self, sequence_index): # Değişiklik yok
        if not self.motor_pins: return
        step_pattern = STEP_SEQUENCE[sequence_index % len(STEP_SEQUENCE)]
        for i in range(4):
            if self.motor_pins[i] and hasattr(self.motor_pins[i], 'value') and not self.motor_pins[i].closed:
                self.motor_pins[i].value = step_pattern[i]

    def _move_motor_to_target_physical_angle(self, target_physical_angle_deg, step_delay=STEP_DELAY): # Değişiklik yok
        degrees_per_step = 360.0 / STEPS_PER_REVOLUTION
        target_physical_normalized = normalize_angle_0_360(target_physical_angle_deg)
        diff_cw = (target_physical_normalized - self.current_physical_motor_angle + 360.0) % 360.0
        diff_ccw = (self.current_physical_motor_angle - target_physical_normalized + 360.0) % 360.0
        angle_difference_to_move = 0; direction_is_cw = True
        if abs(diff_cw) < 1e-3 and abs(diff_ccw) < 1e-3 : self.current_physical_motor_angle = target_physical_normalized; return
        if diff_cw <= diff_ccw: angle_difference_to_move = diff_cw; direction_is_cw = True
        else: angle_difference_to_move = diff_ccw; direction_is_cw = False
        if abs(angle_difference_to_move) < (degrees_per_step / 2.0): self.current_physical_motor_angle = target_physical_normalized; return
        steps_to_move = round(angle_difference_to_move / degrees_per_step)
        if steps_to_move == 0: self.current_physical_motor_angle = target_physical_normalized; return
        for _ in range(int(steps_to_move)):
            if direction_is_cw:
                self.current_motor_step_index = (self.current_motor_step_index + 1) % len(STEP_SEQUENCE)
                self.current_physical_motor_angle += degrees_per_step
            else:
                self.current_motor_step_index = (self.current_motor_step_index - 1 + len(STEP_SEQUENCE)) % len(STEP_SEQUENCE)
                self.current_physical_motor_angle -= degrees_per_step
            self._apply_step_to_motor(self.current_motor_step_index); time.sleep(step_delay)
            self.current_physical_motor_angle = normalize_angle_0_360(self.current_physical_motor_angle)
        self.current_physical_motor_angle = target_physical_normalized

    def _do_scan_at_angle_and_log(self, physical_target_for_motor_deg, angle_to_log_in_db_deg, phase_description=""): # Değişiklik yok
        self._move_motor_to_target_physical_angle(physical_target_for_motor_deg, step_delay=STEP_DELAY)
        time.sleep(STEP_MOTOR_SETTLE_TIME)
        loop_iter_timestamp = time.time()
        distance_m = self.sensor.distance if self.sensor else float('inf'); distance_cm = distance_m * 100
        physical_angle_for_trig = self.current_physical_motor_angle
        angle_rad_for_trig = math.radians(physical_angle_for_trig)
        x_cm = distance_cm * math.cos(angle_rad_for_trig); y_cm = distance_cm * math.sin(angle_rad_for_trig)
        normalized_angle_to_log = normalize_angle_0_360(angle_to_log_in_db_deg) # Bu zaten 0-scan_arc aralığında olmalı
        # print(f"DEBUG_SCAN_LOG: Phase: {phase_description}, PhysicalAngle: {physical_angle_for_trig:.2f}, LogAngle: {normalized_angle_to_log:.2f}, Dist_cm: {distance_cm:.2f}")
        if self.yellow_led and hasattr(self.yellow_led, 'toggle'): self.yellow_led.toggle()
        hiz_cm_s = 0.0
        if self.ölçüm_tamponu_hız_için_yerel:
            son_veri_noktasi = self.ölçüm_tamponu_hız_için_yerel[-1]
            delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
            delta_zaman = loop_iter_timestamp - son_veri_noktasi['zaman_s']
            if delta_zaman > 0.001: hiz_cm_s = delta_mesafe / delta_zaman
        self.ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': loop_iter_timestamp}]
        if self.lcd:
            try:
                self.lcd.cursor_pos = (0, 0); self.lcd.write_string(f"A:{normalized_angle_to_log:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(f"{phase_description[:8]} H:{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
            except Exception as e_lcd: print(f"LCD yazma hatası: {e_lcd}")
        if distance_cm < TERMINATION_DISTANCE_CM:
            # print(f"DEBUG_SCAN_LOG: TERMINATION condition met. Distance {distance_cm:.2f} < {TERMINATION_DISTANCE_CM}. Phase: {phase_description}")
            if self.lcd: # ... (termination LCD) ...
                try:
                    self.lcd.clear(); self.lcd.cursor_pos = (0, 0); self.lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
                except: pass
            if self.yellow_led and hasattr(self.yellow_led, 'on'): self.yellow_led.on()
            self.script_exit_status = 'terminated_close_object'; time.sleep(1.0); return False, None
        try:
            if self.db_conn_local and self.current_scan_id:
                cursor = self.db_conn_local.cursor()
                cursor.execute('INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)',
                               (self.current_scan_id, normalized_angle_to_log, distance_cm, hiz_cm_s, loop_iter_timestamp, x_cm, y_cm))
                self.db_conn_local.commit()
        except Exception as e_db: print(f"DB Ekleme Hatası: {e_db}")
        return True, None # current_point_xy kaldırıldı, kullanılmıyordu

    def _calculate_polygon_area_shoelace(self, points_xy): # Değişiklik yok
        if len(points_xy) < 2: return 0.0
        polygon_vertices = [(0.0, 0.0)] + points_xy; area = 0.0; m = len(polygon_vertices)
        if m < 3: return 0.0
        for i in range(m): x1, y1 = polygon_vertices[i]; x2, y2 = polygon_vertices[(i + 1) % m]; area += (x1 * y2) - (x2 * y1)
        return abs(area) / 2.0

    def _calculate_perimeter(self, points_xy): # Değişiklik yok
        if not points_xy: return 0.0; perimeter = 0.0
        perimeter += math.sqrt(points_xy[0][0]**2 + points_xy[0][1]**2)
        for i in range(len(points_xy) - 1):
            x1, y1 = points_xy[i]; x2, y2 = points_xy[i+1]
            perimeter += math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
        if len(points_xy) > 0 : perimeter += math.sqrt(points_xy[-1][0]**2 + points_xy[-1][1]**2)
        return perimeter

    def _final_analysis(self): # Değişiklik yok
        if not self.current_scan_id: return
        # ... (geri kalan analiz kodları aynı)
        conn_analysis = None; alan, cevre, max_g, max_d = 0.0, 0.0, 0.0, 0.0
        try:
            conn_analysis = sqlite3.connect(DB_PATH)
            cursor_analysis = conn_analysis.cursor()
            max_dist_cm_sensor = (self.sensor.max_distance * 100 if self.sensor else 200.0)
            df_all_valid_points = pd.read_sql_query(f"SELECT x_cm, y_cm, angle_deg FROM scan_points WHERE scan_id = {self.current_scan_id} AND mesafe_cm > 0.1 AND mesafe_cm < {max_dist_cm_sensor} ORDER BY angle_deg ASC", conn_analysis)
            if len(df_all_valid_points) >= 2:
                points_for_calc = list(zip(df_all_valid_points['x_cm'], df_all_valid_points['y_cm']))
                alan = self._calculate_polygon_area_shoelace(points_for_calc)
                cevre = self._calculate_perimeter(points_for_calc)
                x_coords = df_all_valid_points['x_cm'].tolist(); y_coords = df_all_valid_points['y_cm'].tolist()
                max_d = max(x_coords) if x_coords else 0.0; min_y = min(y_coords) if y_coords else 0.0
                max_y = max(y_coords) if y_coords else 0.0; max_g = max_y - min_y
                if self.lcd: # ... (LCD mesajları)
                    try:
                        self.lcd.clear(); self.lcd.cursor_pos = (0, 0); self.lcd.write_string(f"Alan:{alan:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(f"Cevre:{cevre:.0f}cm".ljust(LCD_COLS)[:LCD_COLS])
                    except: pass
                self.script_exit_status = 'completed_analysis'
                cursor_analysis.execute("UPDATE servo_scans SET hesaplanan_alan_cm2=?, cevre_cm=?, max_genislik_cm=?, max_derinlik_cm=?, status=? WHERE id=?",
                                        (alan, cevre, max_g, max_d, self.script_exit_status, self.current_scan_id)); conn_analysis.commit()
            else:
                self.script_exit_status = 'completed_insufficient_points'
                if self.lcd: # ... (LCD mesajları)
                    try:
                        self.lcd.clear(); self.lcd.cursor_pos = (0, 0); self.lcd.write_string("Tarama Tamam".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string("Veri Yetersiz".ljust(LCD_COLS)[:LCD_COLS])
                    except: pass
                cursor_analysis.execute("UPDATE servo_scans SET status=? WHERE id=?", (self.script_exit_status, self.current_scan_id)); conn_analysis.commit()
        except Exception as e_final_db: # ... (hata yönetimi aynı)
            print(f"Son DB işlemleri/Analiz hatası: {e_final_db}")
            if self.script_exit_status.startswith('completed'): self.script_exit_status = 'completed_analysis_error'
            try:
                if conn_analysis and self.current_scan_id:
                    cursor_analysis = conn_analysis.cursor()
                    cursor_analysis.execute("UPDATE servo_scans SET status=? WHERE id=?", (self.script_exit_status, self.current_scan_id)); conn_analysis.commit()
            except Exception as e_status_update: print(f"Hata durumu güncellenirken ek hata: {e_status_update}")
        finally:
            if conn_analysis: conn_analysis.close()

    def cleanup(self): # Değişiklik yok
        pid = os.getpid(); print(f"[{pid}] `Scanner.cleanup` çağrıldı. Son durum: {self.script_exit_status}")
        if self.db_conn_local: # ... (DB kapatma aynı)
            try: self.db_conn_local.close(); self.db_conn_local = None
            except Exception as e: print(f"Yerel DB kapatma hatası: {e}")
        if self.current_scan_id: # ... (DB status update aynı)
            conn_exit = None
            try:
                conn_exit = sqlite3.connect(DB_PATH)
                cursor_exit = conn_exit.cursor()
                cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (self.current_scan_id,)); row = cursor_exit.fetchone()
                if row and (row[0] == 'running' or self.script_exit_status not in ['interrupted_unexpectedly', 'running']):
                    cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?", (self.script_exit_status, self.current_scan_id)); conn_exit.commit()
            except Exception as e: print(f"DB durum güncelleme hatası (çıkışta): {e}")
            finally:
                if conn_exit: conn_exit.close()
        if self.motor_pins: # ... (Motoru fiziksel 0'a götürme aynı)
            try:
                self._move_motor_to_target_physical_angle(0.0, step_delay=STEP_DELAY * 0.8); time.sleep(0.2)
                for pin_obj in self.motor_pins:
                    if hasattr(pin_obj, 'off'): pin_obj.off()
            except Exception as e: print(f"Motoru merkeze alma hatası: {e}")
            finally:
                for pin_obj in self.motor_pins:
                    if hasattr(pin_obj, 'close') and not pin_obj.closed: pin_obj.close()
                self.motor_pins = []
        if self.yellow_led and hasattr(self.yellow_led, 'close'): # ... (LED kapatma aynı)
            if hasattr(self.yellow_led, 'is_active') and self.yellow_led.is_active: self.yellow_led.off()
            self.yellow_led.close(); self.yellow_led = None
        if self.sensor and hasattr(self.sensor, 'close'): # ... (Sensör kapatma aynı)
            self.sensor.close(); self.sensor = None
        if self.lcd: # ... (LCD kapatma aynı)
            try:
                self.lcd.clear(); self.lcd.cursor_pos=(0,0); self.lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: self.lcd.cursor_pos=(1,0); self.lcd.write_string(f"PID:{pid} Son".ljust(LCD_COLS)[:LCD_COLS])
            except Exception as e: print(f"LCD kapatma mesajı hatası: {e}")
            finally: self.lcd = None
        print(f"[{pid}] `Scanner.cleanup` tamamlandı.")

    def run(self):
        if not self._init_hardware(): self.script_exit_status = 'error_hardware_init'; sys.exit(1)
        if not self._init_db_for_scan(): self.script_exit_status = 'error_db_init'; sys.exit(1)

        if self.lcd: # ... (LCD mesajları aynı) ...
            try:
                self.lcd.clear(); self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string(f"ID:{self.current_scan_id} Basliyor".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(f"Fiz:{self.conceptual_initial_goto_physical_angle:.0f}to{self.conceptual_final_physical_angle:.0f}".ljust(LCD_COLS)[:LCD_COLS])
            except: pass

        scan_aborted_flag = False
        try:
            self.db_conn_local = sqlite3.connect(DB_PATH, timeout=10)

            print(f"[{os.getpid()}] İlk FİZİKSEL pozisyon: {self.conceptual_initial_goto_physical_angle}°'ye gidiliyor...")
            self._move_motor_to_target_physical_angle(self.conceptual_initial_goto_physical_angle)
            time.sleep(0.5)

            self.relative_zero_physical_angle = self.current_physical_motor_angle # Bu nokta göreli 0
            print(f"[{os.getpid()}] Motor FİZİKSEL {self.current_physical_motor_angle:.1f}°. Bu nokta GÖRELİ 0°.")

            current_relative_angle_for_log = 0.0 # Tarama göreli 0'dan başlar

            print(f"[{os.getpid()}] Ana Tarama: Göreli 0°'dan başlayarak {self.scan_arc_degrees:.1f}° yay boyunca {self.actual_scan_step:.1f}° adımlarla (Yön: {self.scan_direction_sign}).")
            if self.lcd:
                try: self.lcd.cursor_pos = (1, 0); self.lcd.write_string(f"Rel:0to{self.scan_arc_degrees*self.scan_direction_sign:.0f}".ljust(LCD_COLS)[:LCD_COLS])
                except: pass

            # İlk ölçümü GÖRELİ 0°'da yap
            # print(f"DEBUG_RUN: İlk ölçüm. Fiziksel Hedef: {self.relative_zero_physical_angle:.2f}, Kaydedilecek Göreli Açı: {current_relative_angle_for_log:.2f}")
            continue_scan, _ = self._do_scan_at_angle_and_log(
                physical_target_for_motor_deg=self.relative_zero_physical_angle, # Motor zaten bu pozisyonda olmalı
                angle_to_log_in_db_deg=current_relative_angle_for_log, # Bu 0.0 olacak
                phase_description=f"Scan:{normalize_angle_0_360(current_relative_angle_for_log):.0f}°(R)"
            )
            # print(f"DEBUG_RUN: İlk ölçüm sonrası continue_scan: {continue_scan}")
            if not continue_scan: scan_aborted_flag = True

            if not scan_aborted_flag:
                num_steps_in_scan = 0
                if self.actual_scan_step > 1e-3:
                    num_steps_in_scan = math.floor(self.scan_arc_degrees / self.actual_scan_step)

                # print(f"DEBUG_RUN: Tarama yayında atılacak adım sayısı: {num_steps_in_scan}")

                for i in range(num_steps_in_scan):
                    loop_iter_start_time = time.time()

                    # <<< DEĞİŞİKLİK BURADA: Göreli açı her zaman pozitif artar >>>
                    current_relative_angle_for_log += self.actual_scan_step
                    # Fiziksel hedef, göreli açıya ve tarama yönüne göre hesaplanır
                    current_physical_target_for_motor = self.relative_zero_physical_angle + (current_relative_angle_for_log * self.scan_direction_sign)

                    # print(f"DEBUG_RUN: Adım {i+1}/{num_steps_in_scan}. Fiziksel Hedef: {current_physical_target_for_motor:.2f}, Kaydedilecek Göreli Açı: {current_relative_angle_for_log:.2f}")
                    continue_scan, _ = self._do_scan_at_angle_and_log(
                        physical_target_for_motor_deg=current_physical_target_for_motor,
                        angle_to_log_in_db_deg=current_relative_angle_for_log, # Her zaman pozitif artan
                        phase_description=f"Scan:{normalize_angle_0_360(current_relative_angle_for_log):.0f}°(R)"
                    )
                    if not continue_scan: scan_aborted_flag = True; break

                    loop_proc_time = time.time() - loop_iter_start_time
                    sleep_dur = max(0, LOOP_TARGET_INTERVAL_S - loop_proc_time)
                    if sleep_dur > 0 and (i < num_steps_in_scan -1) :
                        time.sleep(sleep_dur)

                if not scan_aborted_flag:
                    # <<< DEĞİŞİKLİK BURADA: Son göreli açı her zaman pozitif scan_arc_degrees olur >>>
                    final_relative_angle_for_log = self.scan_arc_degrees
                    final_physical_target_for_motor = self.relative_zero_physical_angle + (final_relative_angle_for_log * self.scan_direction_sign)
                    print(f"[{os.getpid()}] Tarama yayının sonu ({final_relative_angle_for_log}° göreli) için ek ölçüm.")
                    # print(f"DEBUG_RUN: Son ölçüm. Fiziksel Hedef: {final_physical_target_for_motor:.2f}, Kaydedilecek Göreli Açı: {final_relative_angle_for_log:.2f}")
                    continue_scan, _ = self._do_scan_at_angle_and_log(
                        physical_target_for_motor_deg=final_physical_target_for_motor,
                        angle_to_log_in_db_deg=final_relative_angle_for_log, # Her zaman pozitif scan_arc_degrees
                        phase_description=f"End:{normalize_angle_0_360(final_relative_angle_for_log):.0f}°(R)"
                    )
                    if not continue_scan: scan_aborted_flag = True

            if not scan_aborted_flag: self.script_exit_status = 'completed'
        except KeyboardInterrupt: # ... (Hata yönetimi aynı)
            self.script_exit_status = 'interrupted_ctrl_c'; print(f"\n[{os.getpid()}] Ctrl+C ile durduruldu.")
            if self.lcd:
                try: self.lcd.clear(); self.lcd.cursor_pos = (0, 0); self.lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS])
                except: pass
        except Exception as e_main:
            if self.script_exit_status != 'terminated_close_object': self.script_exit_status = 'error_in_loop'
            print(f"[{os.getpid()}] Ana döngü hatası: {e_main}"); import traceback; traceback.print_exc()
            if self.lcd and self.script_exit_status != 'terminated_close_object':
                try: self.lcd.clear(); self.lcd.cursor_pos = (0, 0); self.lcd.write_string(f"Hata:{str(e_main)[:8]}".ljust(LCD_COLS)[:LCD_COLS])
                except: pass
        finally: # ... (DB kapatma aynı)
            if self.db_conn_local:
                try: self.db_conn_local.close(); self.db_conn_local = None;
                except Exception as e: print(f"Tarama sonrası yerel DB kapatma hatası: {e}")
        if not scan_aborted_flag and self.script_exit_status == 'completed' and self.current_scan_id:
            self._final_analysis()
        print(f"[{os.getpid()}] Ana işlem bloğu sonlandı. Son durum: {self.script_exit_status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motor ile Alan Tarama Betiği (Göreli Sıfır Mantığı)")
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
        print(f"[{os.getpid()}] Beklenmedik ana hata: {e}"); import traceback; traceback.print_exc()
        scanner_app.script_exit_status = 'error_unhandled_exception'
    finally:
        print(f"[{os.getpid()}] __main__ bloğu sonlanıyor.")

