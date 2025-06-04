import os
import sys
import time
import argparse
import fcntl
import atexit
import math

# ==============================================================================
# --- DJANGO ENTEGRASYONU ---
# Bu bölüm, betiğin Django ortamını ve modellerini tanımasını sağlar.
# 'dream_pi_project' yerine kendi Django projenizin adını yazın.
# ==============================================================================
try:
    # Betiğin çalıştığı dizini ve proje ana dizinini Python yoluna ekle
    sys.path.append(os.getcwd())
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dream_pi_project.settings')
    import django
    django.setup()
    from django.utils import timezone
    from scanner.models import Scan, ScanPoint
    print("SensorScript: Django entegrasyonu başarılı.")
except Exception as e:
    print(f"SensorScript: Django entegrasyonu BAŞARISIZ: {e}")
    sys.exit(1)
# ==============================================================================


# ==============================================================================
# --- Donanım ve GPIO Kütüphaneleri ---
# ==============================================================================
from gpiozero import DistanceSensor, LED, Buzzer, OutputDevice
from RPLCD.i2c import CharLCD
# ==============================================================================


# ==============================================================================
# --- KONTROL DEĞİŞKENİ ---
# ==============================================================================
MOTOR_BAGLI = True
# ==============================================================================


# ==============================================================================
# --- Pin Tanımlamaları ve Donanım Ayarları ---
# ==============================================================================
TRIG_PIN, ECHO_PIN = 23, 24
IN1_GPIO_PIN, IN2_GPIO_PIN, IN3_GPIO_PIN, IN4_GPIO_PIN = 6, 13, 19, 26
YELLOW_LED_PIN, BUZZER_PIN = 27, 17
# NOT: Bu adresi 'i2cdetect -y 1' komutunun çıktısına göre güncellemeniz gerekebilir.
LCD_I2C_ADDRESS, LCD_PORT_EXPANDER, LCD_COLS, LCD_ROWS, I2C_PORT = 0x27, 'PCF8574', 16, 2, 1
# ==============================================================================


# ==============================================================================
# --- Varsayılan Değerler ---
# ==============================================================================
DEFAULT_SCAN_DURATION_ANGLE = 270.0
DEFAULT_SCAN_STEP_ANGLE = 10.0
DEFAULT_BUZZER_DISTANCE = 10
DEFAULT_INVERT_MOTOR_DIRECTION = False
DEFAULT_STEPS_PER_REVOLUTION = 4096
STEP_MOTOR_INTER_STEP_DELAY, STEP_MOTOR_SETTLE_TIME, LOOP_TARGET_INTERVAL_S = 0.0015, 0.05, 0.6
# ==============================================================================


# ==============================================================================
# --- Global Değişkenler ---
# ==============================================================================
LOCK_FILE_PATH, PID_FILE_PATH = '/tmp/sensor_scan_script.lock', '/tmp/sensor_scan_script.pid'
sensor, yellow_led, buzzer, lcd = None, None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
lock_file_handle = None
current_scan_object_global = None # Mevcut 'Scan' model nesnesini tutacak
script_exit_status_global = Scan.Status.ERROR # Varsayılan çıkış durumu

STEPS_PER_REVOLUTION_OUTPUT_SHAFT = DEFAULT_STEPS_PER_REVOLUTION
DEG_PER_STEP, current_motor_angle_global, current_step_sequence_index = 0.0, 0.0, 0
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0], [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]
SCAN_DURATION_ANGLE_PARAM, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION = DEFAULT_SCAN_DURATION_ANGLE, DEFAULT_SCAN_STEP_ANGLE, DEFAULT_BUZZER_DISTANCE, DEFAULT_INVERT_MOTOR_DIRECTION
# ==============================================================================


# ==============================================================================
# --- Donanım ve Yardımcı Fonksiyonlar ---
# ==============================================================================
def init_hardware():
    global sensor, yellow_led, buzzer, lcd, current_motor_angle_global, in1_dev, in2_dev, in3_dev, in4_dev
    pid, hardware_ok = os.getpid(), True
    try:
        if MOTOR_BAGLI:
            in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        else:
            print("[UYARI] Motor bağlı değil. Motor pinleri atlanıyor.")

        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=2)
        yellow_led, buzzer = LED(YELLOW_LED_PIN), Buzzer(BUZZER_PIN)
        yellow_led.off(); buzzer.off()
        current_motor_angle_global = 0.0
    except Exception as e:
        print(f"[{pid}] Donanım Başlatma HATA: {e}"); hardware_ok = False

    # LCD başlatma ve hata yakalama iyileştirildi
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02', auto_linebreaks=True)
            lcd.clear()
            lcd.write_string("Dream Pi Hazir".ljust(LCD_COLS))
            print("LCD başarıyla başlatıldı.")
            time.sleep(2)
        except Exception as e_lcd:
            print(f"!!! KRİTİK UYARI: LCD başlatılamadı! Hata: {e_lcd}")
            lcd = None
    return hardware_ok

def _set_step_pins(s1, s2, s3, s4):
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)

def _step_motor_4in(num_steps, direction_positive):
    global current_step_sequence_index
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    time.sleep(STEP_MOTOR_SETTLE_TIME)

def move_motor_to_angle(target_angle_deg):
    global current_motor_angle_global
    if not MOTOR_BAGLI:
        current_motor_angle_global = target_angle_deg
        return
    if DEG_PER_STEP <= 0: print(f"HATA: DEG_PER_STEP ({DEG_PER_STEP}) geçersiz!"); return
    normalized_current_angle = current_motor_angle_global % 360.0
    normalized_target_angle = target_angle_deg % 360.0
    angle_diff = normalized_target_angle - normalized_current_angle
    if abs(angle_diff) > 180.0:
        angle_diff -= 360.0 if angle_diff > 0 else -360.0
    if abs(angle_diff) < (DEG_PER_STEP / 2.0): return
    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0: return
    logical_dir_positive = (angle_diff > 0)
    physical_dir_positive = not logical_dir_positive if INVERT_MOTOR_DIRECTION else logical_dir_positive
    _step_motor_4in(num_steps, physical_dir_positive)
    current_motor_angle_global += (num_steps * DEG_PER_STEP * (1 if logical_dir_positive else -1))

def shoelace_formula(points): return 0.5 * abs(sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points))))
def calculate_perimeter(points):
    perimeter = math.hypot(points[0][0], points[0][1])
    for i in range(len(points) - 1): perimeter += math.hypot(points[i+1][0] - points[i][0], points[i+1][1] - points[i][1])
    perimeter += math.hypot(points[-1][0], points[-1][1])
    return perimeter

def create_scan_entry(start_angle, end_angle, step_angle, buzzer_dist, invert_dir):
    """Veritabanında yeni bir Scan nesnesi oluşturur."""
    global current_scan_object_global
    try:
        Scan.objects.filter(status=Scan.Status.RUNNING).update(status=Scan.Status.ERROR)
        current_scan_object_global = Scan.objects.create(start_angle_setting=start_angle, end_angle_setting=end_angle, step_angle_setting=step_angle, buzzer_distance_setting=buzzer_dist, invert_motor_direction_setting=invert_dir, status=Scan.Status.RUNNING)
        print(f"Yeni tarama kaydı veritabanında oluşturuldu: ID #{current_scan_object_global.id}")
        return True
    except Exception as e:
        print(f"DB Hatası (create_scan_entry): {e}"); return False

def acquire_lock_and_pid():
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w'); fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf: pf.write(str(os.getpid()))
        return True
    except Exception: return False

def release_resources_on_exit():
    pid = os.getpid(); print(f"[{pid}] Kaynaklar serbest bırakılıyor... Durum: {script_exit_status_global}")
    if current_scan_object_global:
        try:
            scan_to_update = Scan.objects.get(id=current_scan_object_global.id)
            if scan_to_update.status == Scan.Status.RUNNING: scan_to_update.status = script_exit_status_global; scan_to_update.save()
        except Exception as e: print(f"DB çıkış HATA: {e}")
    if MOTOR_BAGLI: _set_step_pins(0, 0, 0, 0)
    if lcd:
        try: lcd.clear()
        except Exception as e: print(f"LCD temizlenirken hata: {e}")
    for dev in [sensor, yellow_led, buzzer, in1_dev, in2_dev, in3_dev, in4_dev, lcd]:
        if dev and hasattr(dev, 'close'):
            try: dev.close()
            except Exception: pass
    if lock_file_handle:
        try: fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN); lock_file_handle.close()
        except Exception: pass
    for fp in [PID_FILE_PATH, LOCK_FILE_PATH]:
        if os.path.exists(fp):
            try: os.remove(fp)
            except OSError: pass
    print(f"[{pid}] Temizleme tamamlandı.")
# ==============================================================================


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan_duration_angle", type=float, default=DEFAULT_SCAN_DURATION_ANGLE)
    parser.add_argument("--step_angle", type=float, default=DEFAULT_SCAN_STEP_ANGLE)
    parser.add_argument("--buzzer_distance", type=int, default=DEFAULT_BUZZER_DISTANCE)
    parser.add_argument("--invert_motor_direction", type=lambda x: str(x).lower() == 'true', default=DEFAULT_INVERT_MOTOR_DIRECTION)
    parser.add_argument("--steps_per_rev", type=int, default=DEFAULT_STEPS_PER_REVOLUTION)
    args = parser.parse_args()

    SCAN_DURATION_ANGLE_PARAM = float(args.scan_duration_angle)
    SCAN_STEP_ANGLE = float(args.step_angle)
    BUZZER_DISTANCE_CM = int(args.buzzer_distance)
    INVERT_MOTOR_DIRECTION = bool(args.invert_motor_direction)
    STEPS_PER_REVOLUTION_OUTPUT_SHAFT = int(args.steps_per_rev)

    pid = os.getpid()
    atexit.register(release_resources_on_exit)
    if not acquire_lock_and_pid(): print(f"[{pid}] Başka bir betik çalışıyor. Çıkılıyor."); sys.exit(1)
    if not init_hardware(): print(f"[{pid}] Donanım başlatılamadı. Çıkılıyor."); sys.exit(1)

    DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION_OUTPUT_SHAFT
    if SCAN_STEP_ANGLE < DEG_PER_STEP: SCAN_STEP_ANGLE = DEG_PER_STEP

    PHYSICAL_HOME_POSITION = 0.0
    INITIAL_RIGHT_TURN_DEGREES = 135.0
    PRE_SCAN_PHYSICAL_POSITION = (PHYSICAL_HOME_POSITION - INITIAL_RIGHT_TURN_DEGREES + 360.0) % 360.0
    LOGICAL_SCAN_START_ANGLE = 0.0
    LOGICAL_SCAN_END_ANGLE = SCAN_DURATION_ANGLE_PARAM

    if not create_scan_entry(LOGICAL_SCAN_START_ANGLE, LOGICAL_SCAN_END_ANGLE, SCAN_STEP_ANGLE, BUZZER_DISTANCE_CM, INVERT_MOTOR_DIRECTION):
        print(f"[{pid}] Veritabanı oturumu oluşturulamadı. Çıkılıyor."); sys.exit(1)

    print(f"[{pid}] Yeni Otomatik Tarama Başlatılıyor (ID: #{current_scan_object_global.id})...")

    try:
        print(f"[{pid}] ADIM 0: Motor fiziksel başlangıç ({PHYSICAL_HOME_POSITION}°) pozisyonuna getiriliyor...")
        move_motor_to_angle(PHYSICAL_HOME_POSITION)
        time.sleep(0.5)

        print(f"[{pid}] ADIM 1: Tarama öncesi pozisyona ({PRE_SCAN_PHYSICAL_POSITION}°) dönülüyor...")
        move_motor_to_angle(PRE_SCAN_PHYSICAL_POSITION)
        time.sleep(1.0)

        physical_scan_reference_angle = current_motor_angle_global
        print(f"[{pid}] ADIM 2: Tarama başlıyor. Mantıksal [{LOGICAL_SCAN_START_ANGLE}° -> {LOGICAL_SCAN_END_ANGLE}°].")

        collected_points, current_logical_angle = [], LOGICAL_SCAN_START_ANGLE

        while True:
            target_physical_angle_for_step = physical_scan_reference_angle + current_logical_angle
            move_motor_to_angle(target_physical_angle_for_step)

            if yellow_led: yellow_led.on(); time.sleep(0.05)
            dist_cm = sensor.distance * 100
            if yellow_led: yellow_led.off()

            print(f"  Okuma: Mantıksal {current_logical_angle:.1f}° -> {dist_cm:.1f} cm")

            if buzzer: buzzer.on() if dist_cm < BUZZER_DISTANCE_CM else buzzer.off()

            # LCD'ye yazma ve hata yakalama iyileştirildi
            if lcd:
                try:
                    if dist_cm < BUZZER_DISTANCE_CM:
                        lcd.cursor_pos = (0, 0); lcd.write_string("! YAKIN NESNE !".ljust(LCD_COLS))
                        lcd.cursor_pos = (1, 0); lcd.write_string(f"Mesafe: {dist_cm:<5.1f}cm".ljust(LCD_COLS))
                    else:
                        lcd.cursor_pos = (0, 0); lcd.write_string(f"Aci(L):{current_logical_angle:<6.1f}".ljust(LCD_COLS))
                        lcd.cursor_pos = (1, 0); lcd.write_string(f"Mesafe: {dist_cm:<5.1f}cm".ljust(LCD_COLS))
                except Exception as e_lcd_loop:
                    print(f"UYARI: Döngü içinde LCD'ye yazılamadı: {e_lcd_loop}")

            angle_rad_for_calc = math.radians(current_logical_angle)
            x_cm, y_cm = dist_cm * math.cos(angle_rad_for_calc), dist_cm * math.sin(angle_rad_for_calc)

            if 0 < dist_cm < (sensor.max_distance * 100 - 1):
                collected_points.append((x_cm, y_cm))

            ScanPoint.objects.create(scan=current_scan_object_global, derece=current_logical_angle, mesafe_cm=dist_cm, x_cm=x_cm, y_cm=y_cm, timestamp=timezone.now())

            if abs(current_logical_angle - LOGICAL_SCAN_END_ANGLE) < (SCAN_STEP_ANGLE / 20.0) or current_logical_angle >= LOGICAL_SCAN_END_ANGLE:
                print(f"[{pid}] Tarama bitti, mantıksal son açıya ({current_logical_angle:.1f}°) ulaşıldı."); break

            current_logical_angle += SCAN_STEP_ANGLE
            current_logical_angle = min(current_logical_angle, LOGICAL_SCAN_END_ANGLE)
            time.sleep(max(0, LOOP_TARGET_INTERVAL_S - STEP_MOTOR_SETTLE_TIME))

        if len(collected_points) >= 3:
            polygon = [(0, 0)] + collected_points
            area, perimeter = shoelace_formula(polygon), calculate_perimeter(collected_points)
            x_coords = [p[0] for p in collected_points]; y_coords = [p[1] for p in collected_points]
            width = (max(y_coords) - min(y_coords)) if y_coords else 0.0
            depth = max(x_coords) if x_coords else 0.0
            current_scan_object_global.calculated_area_cm2 = area; current_scan_object_global.perimeter_cm = perimeter
            current_scan_object_global.max_width_cm = width; current_scan_object_global.max_depth_cm = depth
            script_exit_status_global = Scan.Status.COMPLETED
        else:
            script_exit_status_global = Scan.Status.INSUFFICIENT_POINTS
        current_scan_object_global.status = script_exit_status_global
        current_scan_object_global.save()

    except KeyboardInterrupt:
        script_exit_status_global = Scan.Status.INTERRUPTED
        print(f"\n[{pid}] Ctrl+C ile kesildi.")
    except Exception as e:
        script_exit_status_global = Scan.Status.ERROR
        import traceback
        traceback.print_exc()
        print(f"[{pid}] KRİTİK HATA: Ana döngüde: {e}")
    finally:
        if script_exit_status_global not in [Scan.Status.ERROR]:
            print(f"[{pid}] ADIM 3: İşlem sonu. Fiziksel başlangıca ({PHYSICAL_HOME_POSITION}°) geri dönülüyor...")
            move_motor_to_angle(PHYSICAL_HOME_POSITION)
            print(f"[{pid}] Fiziksel başlangıca dönüldü.")
        print(f"[{pid}] Betik sonlanıyor.")