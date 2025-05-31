# sensor_script.py (Sınıf Yapısıyla Yeniden Düzenlendi)
from gpiozero import DistanceSensor, LED, OutputDevice
from RPLCD.i2c import CharLCD
import time
import sqlite3
import os
import sys
import fcntl  # Linux'a özgü, Windows'ta çalışmaz
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
I2C_PORT = 1  # Raspberry Pi'da genellikle 1

# --- Eşik ve Tarama Değerleri ---
TERMINATION_DISTANCE_CM = 10.0
# Varsayılan tarama açıları: +135'ten -135'e (toplam 270 derece)
DEFAULT_INITIAL_GOTO_ANGLE_ARG = 135
DEFAULT_FINAL_SCAN_ANGLE_ARG = -135
DEFAULT_SCAN_STEP_ANGLE_ARG = 10

# --- Step Motor Ayarları ---
STEP_MOTOR_SETTLE_TIME = 0.05  # Motorun adımdan sonra durulması için bekleme süresi
LOOP_TARGET_INTERVAL_S = 0.15  # Ana döngünün hedeflediği minimum iterasyon süresi
STEPS_PER_REVOLUTION = 4096  # Motorunuzun 1 tam tur için adım sayısı (örn: 28BYJ-48 için 2048 veya 4096)
STEP_DELAY = 0.0012  # Adımlar arası bekleme süresi (hızı etkiler)
STEP_SEQUENCE = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]  # Tam adım için

# --- Dosya Yolları ---
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
LOCK_FILE_PATH = '/tmp/sensor_scan_script.lock'  # Linux dosya sistemi için
PID_FILE_PATH = '/tmp/sensor_scan_script.pid'  # Linux dosya sistemi için

# --- Global Kilit Dosyası Tutucusu ---
# Bu, atexit'in düzgün çalışması için sınıf dışında yönetilebilir.
# Veya atexit'e scanner nesnesinin cleanup metodunu doğrudan register edebiliriz.
# İkinci yaklaşım daha temizdir.
_g_lock_file_handle = None


def acquire_lock_and_pid():
    """
    Betik için bir kilit dosyası ve PID dosyası oluşturur.
    Başka bir örnek çalışıyorsa False döner.
    """
    global _g_lock_file_handle
    try:
        # Önceki PID dosyasını temizle (varsa)
        if os.path.exists(PID_FILE_PATH):
            os.remove(PID_FILE_PATH)
    except OSError as e:
        print(f"[{os.getpid()}] Uyarı: Eski PID dosyası silinemedi: {e}")
        pass  # Çok kritik değil, devam et

    try:
        _g_lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(_g_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Kilit alındı, PID dosyasını yaz
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Kilit ve PID ({os.getpid()}) oluşturuldu.")
        return True
    except (IOError, OSError) as e:  # IOError, fcntl için de geçerli olabilir
        print(f"[{os.getpid()}] Kilit/PID hatası: Başka bir örnek çalışıyor olabilir veya izin sorunu. ({e})")
        if _g_lock_file_handle:
            _g_lock_file_handle.close()
            _g_lock_file_handle = None
        return False


def release_lock_and_pid_files_on_exit():
    """
    Kilit dosyasını serbest bırakır ve PID/Kilit dosyalarını siler.
    Bu fonksiyon atexit ile çağrılacaksa, Scanner sınıfının cleanup'ı içinde yönetilmesi daha iyi.
    Ancak acquire_lock_and_pid global handle kullandığı için burada da bir versiyonu olabilir.
    """
    global _g_lock_file_handle
    pid = os.getpid()
    print(f"[{pid}] `release_lock_and_pid_files_on_exit` çağrıldı.")

    if _g_lock_file_handle:
        try:
            fcntl.flock(_g_lock_file_handle.fileno(), fcntl.LOCK_UN)
            _g_lock_file_handle.close()
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
                            if int(pf.read().strip()) == pid:
                                can_delete = True
                    except:  # PID okuma hatası veya dosya yok
                        pass
                    if can_delete:
                        os.remove(f_path)
                        print(f"[{pid}] Silindi: {f_path}")
                    elif os.path.exists(f_path):  # Başka bir process'in PID'i ise silme
                        print(f"[{pid}] {f_path} başka bir processe ait olabilir, silinmedi.")
                else:  # LOCK_FILE_PATH ise direkt sil
                    os.remove(f_path)
                    print(f"[{pid}] Silindi: {f_path}")
        except OSError as e_rm:
            print(f"[{pid}] Dosya ({f_path}) silme hatası: {e_rm}")


class Scanner:
    def __init__(self, initial_angle, end_angle, step_angle):
        self.sensor = None
        self.yellow_led = None
        self.lcd = None
        self.motor_pins = []
        self.current_motor_step_index = 0
        self.current_motor_angle = 0.0  # Motorun bilinen son açısı
        self.current_scan_id = None
        self.db_conn_local = None  # Sadece tarama sırasında kullanılan bağlantı
        self.script_exit_status = 'interrupted_unexpectedly'
        self.ölçüm_tamponu_hız_için_yerel = []  # Hız hesaplaması için son ölçüm

        self.initial_goto_angle = initial_angle
        self.actual_scan_end_angle = end_angle
        self.actual_scan_step = abs(step_angle)  # Adım her zaman pozitif
        if self.actual_scan_step == 0:
            self.actual_scan_step = DEFAULT_SCAN_STEP_ANGLE_ARG

        # atexit.register(self.cleanup) # __main__ bloğunda scanner nesnesi oluşturulduktan sonra yapılabilir
        # veya global bir cleanup fonksiyonu ile yönetilebilir.
        # Sınıf metodu olarak register etmek daha temiz.

    def _init_hardware(self):
        hardware_ok = True
        try:
            print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
            self.sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=5, partial=True)
            self.yellow_led = LED(YELLOW_LED_PIN)
            self.motor_pins = [OutputDevice(p) for p in [MOTOR_PIN_IN1, MOTOR_PIN_IN2, MOTOR_PIN_IN3, MOTOR_PIN_IN4]]
            for pin in self.motor_pins:
                pin.off()
            self.yellow_led.off()
            self.current_motor_angle = 0.0  # Başlangıçta motor 0 derecede varsayılır
            print(f"[{os.getpid()}] Temel donanımlar ve Step Motor başarıyla başlatıldı.")
        except Exception as e:
            print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}")
            hardware_ok = False

        if hardware_ok:
            try:
                self.lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                                   cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=False)
                self.lcd.clear()
                self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string("Dream Pi Step".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1:
                    self.lcd.cursor_pos = (1, 0)
                    self.lcd.write_string("Hazirlaniyor...".ljust(LCD_COLS)[:LCD_COLS])
                time.sleep(1.0)
                print(f"[{os.getpid()}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
            except Exception as e_lcd_init:
                print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}. LCD devre dışı.")
                self.lcd = None  # LCD kullanılamazsa None olarak ayarla
        else:
            self.lcd = None
        return hardware_ok

    def _init_db_for_scan(self):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            # servo_scans tablosu
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
                               initial_goto_angle_setting
                               REAL,
                               scan_end_angle_setting
                               REAL,
                               scan_step_angle_setting
                               REAL
                           )''')
            # scan_points tablosu
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
            # Önceki 'running' durumundaki taramaları 'interrupted_prior_run' olarak güncelle
            cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'running'")
            conn.commit()  # Önceki güncellemeyi kaydet

            scan_start_time = time.time()
            cursor.execute(
                "INSERT INTO servo_scans (start_time, status, initial_goto_angle_setting, scan_end_angle_setting, scan_step_angle_setting) VALUES (?, ?, ?, ?, ?)",
                (scan_start_time, 'running', self.initial_goto_angle, self.actual_scan_end_angle, self.actual_scan_step)
            )
            self.current_scan_id = cursor.lastrowid
            conn.commit()
            print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {self.current_scan_id}.")
            return True
        except sqlite3.Error as e_db_init:
            print(f"[{os.getpid()}] DB başlatma/tarama kaydı hatası: {e_db_init}")
            self.current_scan_id = None
            return False
        finally:
            if conn:
                conn.close()

    def _apply_step_to_motor(self, sequence_index):
        if not self.motor_pins: return
        step_pattern = STEP_SEQUENCE[sequence_index % len(STEP_SEQUENCE)]
        for i in range(4):  # 4 motor pini için
            if self.motor_pins[i] and hasattr(self.motor_pins[i], 'value') and not self.motor_pins[i].closed:
                self.motor_pins[i].value = step_pattern[i]

    def _move_motor_to_target_angle_incremental(self, target_angle_deg, step_delay=STEP_DELAY):
        degrees_per_step_resolution = 360.0 / STEPS_PER_REVOLUTION / len(STEP_SEQUENCE)  # Gerçek adım başına açı

        # Hedef açıyı motorun kendi koordinat sistemine göre normalleştir (0-360 arasına sıkıştırma)
        # Bu, +/- açıların doğru yönetilmesini sağlar.
        # Ancak, mevcut current_motor_angle zaten bu şekilde tutuluyorsa gerek yok.
        # Bizim sistemimizde current_motor_angle +/- olabiliyor.

        angle_difference = target_angle_deg - self.current_motor_angle

        if abs(angle_difference) < (degrees_per_step_resolution / 2):  # Çok küçük farkları ihmal et
            # self.current_motor_angle = target_angle_deg # İsteğe bağlı: hedefi tam olarak ayarla
            return

        steps_to_move_float = angle_difference / degrees_per_step_resolution
        steps_to_move = round(steps_to_move_float)  # En yakın tam adıma yuvarla

        if steps_to_move == 0:
            return

        direction_is_cw = steps_to_move > 0  # Pozitif fark CW (saat yönü)

        for _ in range(abs(int(steps_to_move))):
            if direction_is_cw:
                self.current_motor_step_index = (self.current_motor_step_index + 1) % len(STEP_SEQUENCE)
            else:  # CCW
                self.current_motor_step_index = (self.current_motor_step_index - 1 + len(STEP_SEQUENCE)) % len(
                    STEP_SEQUENCE)

            self._apply_step_to_motor(self.current_motor_step_index)
            time.sleep(step_delay)
            # Her adımdan sonra açıyı güncelle
            if direction_is_cw:
                self.current_motor_angle += degrees_per_step_resolution
            else:
                self.current_motor_angle -= degrees_per_step_resolution

        # Son olarak, hedef açıya çok yakınsa tam hedef açıya ayarla (birikmiş hataları düzeltmek için)
        # Bu, özellikle uzun hareketlerden sonra faydalı olabilir.
        # Ancak, her adımdan sonra açıyı güncellediğimiz için bu artık daha az kritik.
        # self.current_motor_angle = target_angle_deg # Bu satır, birikmiş float hatalarını temizler.
        # Ancak, gerçek fiziksel pozisyonla senkronizasyonu bozabilir.
        # Dikkatli kullanılmalı. Şimdilik kapalı.

    def _do_scan_at_angle_and_log(self, target_scan_angle, phase_description=""):
        if self.yellow_led and hasattr(self.yellow_led, 'toggle'):
            self.yellow_led.toggle()

        self._move_motor_to_target_angle_incremental(target_scan_angle, step_delay=STEP_DELAY)
        time.sleep(STEP_MOTOR_SETTLE_TIME)  # Motorun durulması için bekle

        loop_iter_timestamp = time.time()
        distance_m = self.sensor.distance if self.sensor else float('inf')
        distance_cm = distance_m * 100

        # Hesaplamalar için motorun bilinen son açısını kullan
        actual_angle_for_calc = self.current_motor_angle
        angle_rad = math.radians(actual_angle_for_calc)
        x_cm = distance_cm * math.cos(angle_rad)
        y_cm = distance_cm * math.sin(angle_rad)

        current_point_xy = None
        if 0 < distance_cm < (self.sensor.max_distance * 100 if self.sensor else float('inf')):
            current_point_xy = (x_cm, y_cm)

        hiz_cm_s = 0.0
        if self.ölçüm_tamponu_hız_için_yerel:  # Tampon boş değilse
            son_veri_noktasi = self.ölçüm_tamponu_hız_için_yerel[-1]
            delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
            delta_zaman = loop_iter_timestamp - son_veri_noktasi['zaman_s']
            if delta_zaman > 0.001:  # Sıfıra bölme hatasını engelle
                hiz_cm_s = delta_mesafe / delta_zaman

        # Hız hesaplaması için tamponu güncelle (sadece son ölçümü tut)
        self.ölçüm_tamponu_hız_için_yerel = [{'mesafe_cm': distance_cm, 'zaman_s': loop_iter_timestamp}]

        if self.lcd:
            try:
                self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string(
                    f"A:{actual_angle_for_calc:<3.0f} M:{distance_cm:5.1f}cm".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1:
                    self.lcd.cursor_pos = (1, 0)
                    self.lcd.write_string(f"{phase_description[:8]} H:{hiz_cm_s:3.0f}".ljust(LCD_COLS)[:LCD_COLS])
            except Exception as e_lcd:
                print(f"[{os.getpid()}] LCD yazma hatası ({phase_description}): {e_lcd}")

        if distance_cm < TERMINATION_DISTANCE_CM:
            print(f"[{os.getpid()}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Tarama durduruluyor.")
            if self.lcd:
                try:
                    self.lcd.clear()
                    self.lcd.cursor_pos = (0, 0)
                    self.lcd.write_string("COK YAKIN! DUR!".ljust(LCD_COLS)[:LCD_COLS])
                    if LCD_ROWS > 1:
                        self.lcd.cursor_pos = (1, 0)
                        self.lcd.write_string(f"{distance_cm:.1f} cm".ljust(LCD_COLS)[:LCD_COLS])
                except:
                    pass  # LCD hatası kritik değil
            if self.yellow_led and hasattr(self.yellow_led, 'on'): self.yellow_led.on()
            self.script_exit_status = 'terminated_close_object'
            time.sleep(1.0)  # Kullanıcının mesajı görmesi için
            return False, None  # Taramayı durdur

        try:
            # Veritabanı bağlantısı tarama döngüsü içinde açılıp kapanmamalı,
            # self.db_conn_local kullanılmalı.
            if self.db_conn_local and self.current_scan_id:
                cursor = self.db_conn_local.cursor()
                cursor.execute(
                    'INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp, x_cm, y_cm) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (self.current_scan_id, actual_angle_for_calc, distance_cm, hiz_cm_s, loop_iter_timestamp, x_cm,
                     y_cm)
                )
                self.db_conn_local.commit()
            else:
                print(f"[{os.getpid()}] DB bağlantısı yok veya scan_id tanımsız, nokta kaydedilemedi.")
        except Exception as e_db_insert:
            print(f"[{os.getpid()}] DB Ekleme Hatası ({phase_description}): {e_db_insert}")

        return True, current_point_xy  # Taramaya devam et

    def _calculate_polygon_area_shoelace(self, points_xy):
        n = len(points_xy)
        area = 0.0
        if n < 3: return 0.0  # Alan için en az 3 nokta gerekir (orijin dahil)
        # Shoelace formülü: Orijin (0,0) ilk nokta olarak kabul edilir.
        # Gelen points_xy listesi zaten (x,y) çiftlerinden oluşmalı.
        # Orijini ekleyerek poligonu kapat.

        # Poligonun köşe noktaları: (0,0), (x1,y1), (x2,y2), ..., (xn,yn)
        # Ancak, gelen points_xy zaten (0,0) etrafındaki noktalar.
        # Bu yüzden (0,0)'ı başa ve sona ekleyerek bir "fan" oluşturuyoruz.

        # Gelen points_xy'nin (0,0) içermediğini varsayıyoruz.
        # Alan hesaplaması için (0,0) noktasını dahil etmeliyiz.
        # Poligon köşe noktaları: (0,0), p1, p2, ..., pn
        # Alan = 0.5 * | (x0*y1 - y0*x1) + (x1*y2 - y1*x2) + ... + (xn*y0 - yn*x0) |

        # points_xy = [(x1,y1), (x2,y2), ...]
        # Poligon = [(0,0)] + points_xy

        polygon_vertices = [(0.0, 0.0)] + points_xy  # Orijini ilk nokta olarak ekle

        m = len(polygon_vertices)  # Yeni nokta sayısı (n+1)
        if m < 3: return 0.0

        for i in range(m):
            x1, y1 = polygon_vertices[i]
            x2, y2 = polygon_vertices[(i + 1) % m]  # Son noktadan ilk noktaya dönüş için % m
            area += (x1 * y2) - (x2 * y1)
        return abs(area) / 2.0

    def _calculate_perimeter(self, points_xy):
        # points_xy = [(x1,y1), (x2,y2), ...]
        # Çevre = Orijin-P1 + P1-P2 + ... + P(n-1)-Pn + Pn-Orijin
        if not points_xy or len(points_xy) < 1: return 0.0

        perimeter = 0.0

        # Orijinden ilk noktaya
        perimeter += math.sqrt(points_xy[0][0] ** 2 + points_xy[0][1] ** 2)

        # Noktalar arası mesafeler
        for i in range(len(points_xy) - 1):
            x1, y1 = points_xy[i]
            x2, y2 = points_xy[i + 1]
            perimeter += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

        # Son noktadan orijine (eğer tarama tam bir daire değilse)
        if len(points_xy) > 1:  # En az iki nokta varsa son nokta orijine bağlanır
            perimeter += math.sqrt(points_xy[-1][0] ** 2 + points_xy[-1][1] ** 2)
        # Eğer tek nokta varsa, zaten orijin-p1 ve p1-orijin (aynı) hesaplanmış olur.
        # Bu yüzden len(points_xy) > 1 koşulu önemli.
        # Eğer tek nokta varsa, çevre = 2 * mesafe_orijin_p1 olmalı.
        # Mevcut durumda, tek nokta için: Orijin-P1 eklenir. Döngü çalışmaz. Sonra P1-Orijin eklenir. Doğru.

        return perimeter

    def _final_analysis(self):
        if not self.current_scan_id:
            print(f"[{os.getpid()}] Analiz için tarama ID bulunamadı.")
            return

        print(f"[{os.getpid()}] Analiz ve son DB işlemleri yapılıyor (ID: {self.current_scan_id})...")
        conn_analysis = None
        alan, cevre, max_g, max_d = 0.0, 0.0, 0.0, 0.0
        try:
            conn_analysis = sqlite3.connect(DB_PATH)
            cursor_analysis = conn_analysis.cursor()

            max_dist_cm_sensor = (self.sensor.max_distance * 100 if self.sensor else 200.0)

            # Analiz için geçerli noktaları çek (sıralı)
            df_all_valid_points = pd.read_sql_query(
                f"SELECT x_cm, y_cm, angle_deg FROM scan_points WHERE scan_id = {self.current_scan_id} AND mesafe_cm > 0.1 AND mesafe_cm < {max_dist_cm_sensor} ORDER BY angle_deg ASC",
                conn_analysis
            )

            if len(df_all_valid_points) >= 2:  # Alan ve çevre için en az 2 nokta (orijin hariç)
                points_for_calc = list(zip(df_all_valid_points['x_cm'], df_all_valid_points['y_cm']))

                alan = self._calculate_polygon_area_shoelace(points_for_calc)
                cevre = self._calculate_perimeter(points_for_calc)

                x_coords = df_all_valid_points['x_cm'].tolist()
                y_coords = df_all_valid_points['y_cm'].tolist()

                # Max derinlik (sensörden en uzak x) ve max genişlik (y'ler arası fark)
                max_d = max(x_coords) if x_coords else 0.0  # Pozitif x ekseni ileri olarak kabul ediliyor
                min_y = min(y_coords) if y_coords else 0.0
                max_y = max(y_coords) if y_coords else 0.0
                max_g = max_y - min_y

                print(
                    f"[{os.getpid()}] TARANAN ALAN ({self.actual_scan_end_angle}° ile {self.initial_goto_angle}°): {alan:.2f} cm²")
                if self.lcd:
                    try:
                        self.lcd.clear()
                        self.lcd.cursor_pos = (0, 0)
                        self.lcd.write_string(f"Alan:{alan:.0f}cm2".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1:
                            self.lcd.cursor_pos = (1, 0)
                            self.lcd.write_string(f"Cevre:{cevre:.0f}cm".ljust(LCD_COLS)[:LCD_COLS])
                    except:
                        pass

                self.script_exit_status = 'completed_analysis'  # Durumu analiz yapıldı olarak güncelle
                cursor_analysis.execute(
                    "UPDATE servo_scans SET hesaplanan_alan_cm2=?, cevre_cm=?, max_genislik_cm=?, max_derinlik_cm=?, status=? WHERE id=?",
                    (alan, cevre, max_g, max_d, self.script_exit_status, self.current_scan_id)
                )
                conn_analysis.commit()
            else:
                self.script_exit_status = 'completed_insufficient_points'
                print(f"[{os.getpid()}] Analiz için yeterli nokta bulunamadı ({len(df_all_valid_points)}).")
                if self.lcd:
                    try:
                        self.lcd.clear()
                        self.lcd.cursor_pos = (0, 0)
                        self.lcd.write_string("Tarama Tamam".ljust(LCD_COLS)[:LCD_COLS])
                        if LCD_ROWS > 1:
                            self.lcd.cursor_pos = (1, 0)
                            self.lcd.write_string("Veri Yetersiz".ljust(LCD_COLS)[:LCD_COLS])
                    except:
                        pass
                # Yetersiz nokta durumunda da status'u güncelle
                cursor_analysis.execute(
                    "UPDATE servo_scans SET status=? WHERE id=?",
                    (self.script_exit_status, self.current_scan_id)
                )
                conn_analysis.commit()

        except Exception as e_final_db:
            print(f"[{os.getpid()}] Son DB işlemleri/Analiz sırasında hata: {e_final_db}")
            if self.script_exit_status.startswith('completed'):  # Eğer 'completed' ise hatayla tamamlandı yap
                self.script_exit_status = 'completed_analysis_error'
                try:  # Hata durumunda bile status'u güncellemeye çalış
                    if conn_analysis and self.current_scan_id:
                        cursor_analysis = conn_analysis.cursor()
                        cursor_analysis.execute("UPDATE servo_scans SET status=? WHERE id=?",
                                                (self.script_exit_status, self.current_scan_id))
                        conn_analysis.commit()
                except Exception as e_status_update:
                    print(f"[{os.getpid()}] Hata durumu güncellenirken ek hata: {e_status_update}")
        finally:
            if conn_analysis:
                conn_analysis.close()

    def cleanup(self):
        pid = os.getpid()
        print(f"[{pid}] `Scanner.cleanup` çağrıldı. Son durum: {self.script_exit_status}")

        # Tarama sırasında kullanılan DB bağlantısını kapat
        if self.db_conn_local:
            try:
                self.db_conn_local.close()
                self.db_conn_local = None
                print(f"[{pid}] Yerel DB bağlantısı kapatıldı.")
            except Exception as e:
                print(f"[{pid}] Yerel DB bağlantısı kapatılırken hata: {e}")

        # Son tarama durumunu veritabanına yaz (eğer 'running' ise)
        if self.current_scan_id:
            conn_exit = None
            try:
                conn_exit = sqlite3.connect(DB_PATH)
                cursor_exit = conn_exit.cursor()
                cursor_exit.execute("SELECT status FROM servo_scans WHERE id = ?", (self.current_scan_id,))
                row = cursor_exit.fetchone()
                # Sadece 'running' ise veya script_exit_status daha anlamlı bir bilgi içeriyorsa güncelle
                if row and (
                        row[0] == 'running' or self.script_exit_status not in ['interrupted_unexpectedly', 'running']):
                    print(
                        f"[{pid}] DB'deki tarama (ID: {self.current_scan_id}) durumu '{self.script_exit_status}' olarak güncelleniyor.")
                    cursor_exit.execute("UPDATE servo_scans SET status = ? WHERE id = ?",
                                        (self.script_exit_status, self.current_scan_id))
                    conn_exit.commit()
                else:
                    print(
                        f"[{pid}] DB'deki tarama (ID: {self.current_scan_id}) durumu zaten güncel veya bilinmiyor: {row[0] if row else 'Yok'}")
            except Exception as e:
                print(f"[{pid}] DB durum güncelleme hatası (çıkışta): {e}")
            finally:
                if conn_exit:
                    conn_exit.close()

        print(f"[{pid}] Donanım kapatılıyor...")
        if self.motor_pins:
            try:
                print(f"[{pid}] Motor merkeze (0°) alınıyor...")
                self._move_motor_to_target_angle_incremental(0.0, step_delay=STEP_DELAY * 0.8)  # Biraz daha hızlı
                time.sleep(0.2)  # Merkeze alma sonrası kısa bekleme
                for pin_obj in self.motor_pins:  # Pinleri kapatmadan önce kapat
                    if hasattr(pin_obj, 'off'): pin_obj.off()
            except Exception as e:
                print(f"[{pid}] Motoru merkeze alma hatası: {e}")
            finally:
                for pin_obj in self.motor_pins:
                    if hasattr(pin_obj, 'close') and not pin_obj.closed:
                        pin_obj.close()
                self.motor_pins = []  # Listeyi temizle
                print(f"[{pid}] Step motor pinleri kapatıldı.")

        if self.yellow_led and hasattr(self.yellow_led, 'close'):
            if hasattr(self.yellow_led, 'is_active') and self.yellow_led.is_active:
                self.yellow_led.off()
            self.yellow_led.close()
            self.yellow_led = None
            print(f"[{pid}] Sarı LED kapatıldı.")

        if self.sensor and hasattr(self.sensor, 'close'):
            self.sensor.close()
            self.sensor = None
            print(f"[{pid}] Mesafe Sensörü kapatıldı.")

        if self.lcd:
            try:
                self.lcd.clear()
                self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string("DreamPi Kapandi".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1:
                    self.lcd.cursor_pos = (1, 0)
                    self.lcd.write_string(f"PID:{pid} Son".ljust(LCD_COLS)[:LCD_COLS])
                # LCD'yi kapatmak için özel bir metot yoksa, CharLCD nesnesi silindiğinde I2C bağlantısı kesilir.
            except Exception as e:
                print(f"[{pid}] LCD temizleme/kapatma mesajı hatası: {e}")
            finally:
                self.lcd = None  # LCD nesnesini serbest bırak
                print(f"[{pid}] LCD kapatıldı.")

        # Kilit ve PID dosyalarını temizleme işi global fonksiyona bırakılabilir veya buraya taşınabilir.
        # Eğer atexit.register(scanner_obj.cleanup) yapıldıysa, global handle'a gerek kalmaz.
        # Bu durumda, release_lock_and_pid_files_on_exit() çağrısı yeterli.
        # Ancak, _g_lock_file_handle'ı bu sınıf içinde yönetmek daha iyi olabilir.
        # Şimdilik, __main__ bloğunda yönetiliyor.

        print(f"[{pid}] `Scanner.cleanup` tamamlandı.")

    def run(self):
        if not self._init_hardware():
            self.script_exit_status = 'error_hardware_init'
            # cleanup zaten atexit ile çağrılacak
            sys.exit(1)

        if not self._init_db_for_scan():
            self.script_exit_status = 'error_db_init'
            sys.exit(1)

        print(f"[{os.getpid()}] Yeni Tarama Deseni Başlıyor (ID: {self.current_scan_id})...")
        if self.lcd:
            try:
                self.lcd.clear()
                self.lcd.cursor_pos = (0, 0)
                self.lcd.write_string(f"ID:{self.current_scan_id} Basliyor".ljust(LCD_COLS)[:LCD_COLS])
                if LCD_ROWS > 1:
                    self.lcd.cursor_pos = (1, 0)
                    self.lcd.write_string(
                        f"{self.initial_goto_angle}to{self.actual_scan_end_angle}".ljust(LCD_COLS)[:LCD_COLS])
            except:
                pass

        scan_aborted_flag = False
        try:
            # Tarama süresince kullanılacak DB bağlantısını aç
            self.db_conn_local = sqlite3.connect(DB_PATH, timeout=10)

            print(f"[{os.getpid()}] İlk pozisyon: {self.initial_goto_angle}°'ye gidiliyor...")
            self._move_motor_to_target_angle_incremental(float(self.initial_goto_angle))
            time.sleep(0.5)  # İlk pozisyona ulaştıktan sonra kısa bekleme
            print(f"[{os.getpid()}] Motor şimdi {self.current_motor_angle:.1f}° pozisyonunda. Ana tarama başlıyor.")

            # Tarama yönünü belirle (initial_goto_angle > actual_scan_end_angle ise negatif adım)
            scan_direction_step = -self.actual_scan_step if self.initial_goto_angle > self.actual_scan_end_angle else self.actual_scan_step

            # range fonksiyonu için bitiş açısını ayarla
            # Eğer CW (saat yönü, açılar azalıyor) ise, end_angle'dan küçük olana kadar git
            # Eğer CCW (saat yönü tersi, açılar artıyor) ise, end_angle'dan büyük olana kadar git
            # Bu yüzden range'in son parametresi end_angle'ın biraz ötesinde olmalı.
            effective_end_for_scan_range = int(self.actual_scan_end_angle + (
                scan_direction_step / abs(scan_direction_step) if scan_direction_step != 0 else -1))

            current_scan_angle = self.current_motor_angle  # Başlangıç açısı

            print(
                f"[{os.getpid()}] Ana Tarama: {current_scan_angle:.1f}° -> {self.actual_scan_end_angle}° (Adım: {scan_direction_step}°)")
            if self.lcd:
                try:
                    self.lcd.cursor_pos = (1, 0)  # İkinci satıra yaz
                    self.lcd.write_string(
                        f"T:{current_scan_angle:.0f}to{self.actual_scan_end_angle:.0f}".ljust(LCD_COLS)[:LCD_COLS])
                except:
                    pass

            # İlk noktayı kaydet (başlangıç pozisyonunda)
            continue_scan, _ = self._do_scan_at_angle_and_log(current_scan_angle, f"Scan:{current_scan_angle:.0f}°")
            if not continue_scan:
                scan_aborted_flag = True

            if not scan_aborted_flag:
                # Python range'i son değeri dahil etmez, bu yüzden effective_end_for_scan_range kullanılır.
                # Adım pozitif veya negatif olabilir.
                target_angles = list(range(int(current_scan_angle + scan_direction_step),
                                           effective_end_for_scan_range,
                                           scan_direction_step))
                # Son hedef açıyı da listeye manuel olarak ekleyebiliriz, eğer range'den dolayı kaçırılıyorsa.
                # Ancak bu, adım büyüklüğüne ve başlangıç/bitiş açılarına bağlı.
                # Şimdilik range'e güveniyoruz. Gerekirse son açıyı ayrıca loglarız.

                for target_angle_in_scan_float in target_angles:
                    target_angle_in_scan = float(target_angle_in_scan_float)
                    loop_iter_start_time = time.time()

                    continue_scan, _ = self._do_scan_at_angle_and_log(target_angle_in_scan,
                                                                      f"Scan:{target_angle_in_scan}°")
                    if not continue_scan:  # Örneğin, nesne çok yakınsa
                        scan_aborted_flag = True
                        break  # İç döngüden çık

                    loop_proc_time = time.time() - loop_iter_start_time
                    sleep_dur = max(0, LOOP_TARGET_INTERVAL_S - loop_proc_time)

                    # Son adımda gereksiz bekleme yapma
                    is_last_scan_step = (
                                abs(target_angle_in_scan - self.actual_scan_end_angle) < abs(scan_direction_step / 2.0))

                    if sleep_dur > 0 and not is_last_scan_step:
                        time.sleep(sleep_dur)
                    if is_last_scan_step:  # Eğer bu son adımsa, döngüden çıkabiliriz.
                        # Ancak, tam bitiş açısında bir ölçüm daha yapmak isteyebiliriz.
                        # Bu, range'in nasıl ayarlandığına bağlı.
                        # Eğer tam bitiş açısı range'de yoksa, burada ayrıca loglanabilir.
                        pass  # Şimdilik döngü sonuna bırakıyoruz.

                # Döngü bittikten sonra, eğer son hedef açı tam olarak ölçülmediyse,
                # ve tarama yarıda kesilmediyse, son bir ölçüm yap.
                if not scan_aborted_flag and abs(self.current_motor_angle - self.actual_scan_end_angle) > abs(
                        scan_direction_step / 2.0):
                    print(f"[{os.getpid()}] Son hedef açı ({self.actual_scan_end_angle}°) için ek ölçüm yapılıyor.")
                    continue_scan, _ = self._do_scan_at_angle_and_log(self.actual_scan_end_angle,
                                                                      f"ScanEnd:{self.actual_scan_end_angle}°")
                    if not continue_scan: scan_aborted_flag = True

            if not scan_aborted_flag:  # Eğer yarıda kesilmediyse tamamlandı say
                self.script_exit_status = 'completed'

        except KeyboardInterrupt:
            self.script_exit_status = 'interrupted_ctrl_c'
            print(f"\n[{os.getpid()}] Ctrl+C ile durduruldu.")
            if self.lcd:
                try:
                    self.lcd.clear()
                    self.lcd.cursor_pos = (0, 0)
                    self.lcd.write_string("DURDURULDU (C)".ljust(LCD_COLS)[:LCD_COLS])
                except:
                    pass
        except Exception as e_main:
            # Eğer nesne çok yakınsa durum zaten ayarlanmıştır, üzerine yazma.
            if self.script_exit_status != 'terminated_close_object':
                self.script_exit_status = 'error_in_loop'
            print(f"[{os.getpid()}] Ana döngü hatası veya erken sonlandırma: {e_main}")
            import traceback
            traceback.print_exc()  # Hatanın detayını yazdır
            if self.lcd and self.script_exit_status != 'terminated_close_object':
                try:
                    self.lcd.clear()
                    self.lcd.cursor_pos = (0, 0)
                    self.lcd.write_string(f"Hata:{str(e_main)[:8]}".ljust(LCD_COLS)[:LCD_COLS])
                except:
                    pass
        finally:
            # Tarama sırasında kullanılan DB bağlantısını burada kapatmak önemli.
            # Cleanup'ta da bir kontrol var ama burası ana kullanım yeri.
            if self.db_conn_local:
                try:
                    self.db_conn_local.close()
                    self.db_conn_local = None
                    print(f"[{os.getpid()}] Tarama sonrası yerel DB bağlantısı kapatıldı.")
                except Exception as e:
                    print(f"[{os.getpid()}] Tarama sonrası yerel DB kapatma hatası: {e}")

        # Tarama bittiyse ve yarıda kesilmediyse analiz yap
        if not scan_aborted_flag and self.script_exit_status == 'completed' and self.current_scan_id:
            self._final_analysis()

        print(f"[{os.getpid()}] Ana işlem bloğu sonlandı. Son durum: {self.script_exit_status}")
        # cleanup metodu atexit ile otomatik çağrılacak.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step Motor ile Alan Tarama Betiği (Sınıf Tabanlı)")
    parser.add_argument("--initial_goto_angle", type=int, default=DEFAULT_INITIAL_GOTO_ANGLE_ARG,
                        help=f"Tarama öncesi gidilecek ilk açı (varsayılan: {DEFAULT_INITIAL_GOTO_ANGLE_ARG})")
    parser.add_argument("--scan_end_angle", type=int, default=DEFAULT_FINAL_SCAN_ANGLE_ARG,
                        help=f"Taramanın biteceği son açı (varsayılan: {DEFAULT_FINAL_SCAN_ANGLE_ARG})")
    parser.add_argument("--scan_step_angle", type=int, default=DEFAULT_SCAN_STEP_ANGLE_ARG,
                        help=f"Tarama adım açısı (varsayılan: {DEFAULT_SCAN_STEP_ANGLE_ARG})")
    args = parser.parse_args()

    if not acquire_lock_and_pid():  # Bu fonksiyon global _g_lock_file_handle'ı ayarlar
        sys.exit(1)  # Kilit alınamazsa çık

    # Scanner nesnesini oluştur
    scanner_app = Scanner(
        initial_angle=args.initial_goto_angle,
        end_angle=args.scan_end_angle,
        step_angle=args.scan_step_angle  # Sınıf içinde abs alınacak
    )

    # Cleanup metodunu atexit'e kaydet. Bu, scanner_app nesnesi var olduğu sürece çalışır.
    # Ve kilit dosyası temizliği de Scanner.cleanup içine taşınabilir veya
    # release_lock_and_pid_files_on_exit global fonksiyonu atexit'e ayrıca eklenebilir.
    # En temizi, Scanner.cleanup'ın kilitleri de yönetmesi. Şimdilik ayrı tutuyoruz.
    atexit.register(scanner_app.cleanup)
    atexit.register(release_lock_and_pid_files_on_exit)  # Bu, _g_lock_file_handle'ı kullanır.

    try:
        scanner_app.run()
    except Exception as e:
        print(f"[{os.getpid()}] Beklenmedik ana hata: {e}")
        import traceback

        traceback.print_exc()
        # script_exit_status'u burada da ayarlamak iyi olabilir, ama cleanup zaten genel bir durumla ilgilenir.
        scanner_app.script_exit_status = 'error_unhandled_exception'
        # sys.exit(1) # atexit fonksiyonlarının çalışması için sys.exit() çağrılabilir.
        # Ancak, normal script sonlanması da atexit'i tetikler.
    finally:
        # atexit.register ile kaydedilen fonksiyonlar script normal bittiğinde veya
        # bir exception ile kesildiğinde (sys.exit() dahil) çağrılır.
        # Bu yüzden burada ayrıca kilit serbest bırakmaya gerek yok, atexit halleder.
        print(f"[{os.getpid()}] __main__ bloğu sonlanıyor.")
