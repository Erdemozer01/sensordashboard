import time
import atexit
import sys

# Gerekli GPIO kütüphanelerini import et
try:
    from gpiozero import DistanceSensor, Buzzer, OutputDevice, LED
    from RPLCD.i2c import CharLCD
except ImportError:
    print("HATA: Gerekli kütüphaneler (gpiozero, RPLCD) bulunamadı. Lütfen yükleyin.")
    sys.exit(1)

# ==============================================================================
# --- Pin Tanımlamaları ve Donanım Ayarları ---
# ==============================================================================
TRIG_PIN, ECHO_PIN = 23, 24
IN1_GPIO_PIN, IN2_GPIO_PIN, IN3_GPIO_PIN, IN4_GPIO_PIN = 6, 13, 19, 26
BUZZER_PIN = 17
STATUS_LED_PIN = 27
LCD_I2C_ADDRESS = 0x27
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1
# ==============================================================================

# ==============================================================================
# --- Parametreler ---
# ==============================================================================
STEPS_PER_REVOLUTION = 4096
STEP_MOTOR_INTER_STEP_DELAY = 0.0015
STEP_MOTOR_SETTLE_TIME = 0.05

SWEEP_ANGLE_MAX = 60
ALGILAMA_ESIGI_CM = 20
BUZZER_BIP_SURESI = 0.03

LED_BLINK_ON_SURESI = 0.5
LED_BLINK_OFF_SURESI = 0.5
LCD_TIME_UPDATE_INTERVAL = 1.0
# ==============================================================================

# --- Global Değişkenler ---
sensor, buzzer, lcd, status_led = None, None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None

current_motor_angle_global = 0.0
current_step_sequence_index = 0
DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]

current_lcd_message_type = None
last_lcd_time_update = 0
led_is_blinking = False
init_hardware_called_successfully = False


# ==============================================================================

# ==============================================================================
# --- Donanım ve Yardımcı Fonksiyonlar ---
# ==============================================================================

def init_hardware():
    global sensor, buzzer, lcd, status_led, in1_dev, in2_dev, in3_dev, in4_dev, led_is_blinking, init_hardware_called_successfully
    print("Donanımlar başlatılıyor...")
    try:
        in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(
            IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=5)
        buzzer = Buzzer(BUZZER_PIN);
        buzzer.off()
        status_led = LED(STATUS_LED_PIN)
        status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI, background=True)
        led_is_blinking = True

        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS,
                          rows=LCD_ROWS, auto_linebreaks=False)
            lcd.clear()
            print("✓ LCD başarıyla başlatıldı.")
        except Exception as e_lcd:
            print(f"UYARI: LCD başlatılamadı! Hata: {e_lcd}")
            lcd = None
        print("✓ Donanımlar başarıyla başlatıldı.")
        init_hardware_called_successfully = True
        return True
    except Exception as e:
        print(f"HATA: Donanım başlatılamadı! Detay: {e}")
        init_hardware_called_successfully = False
        return False


def release_resources_on_exit():
    print("\nProgram sonlandırılıyor, kaynaklar serbest bırakılıyor...")
    _set_step_pins(0, 0, 0, 0)
    if lcd:
        try:
            lcd.clear()
        except:
            pass
    if status_led:
        try:
            status_led.off()
        except:
            pass
    for dev in [sensor, buzzer, lcd, status_led, in1_dev, in2_dev, in3_dev, in4_dev]:
        if dev and hasattr(dev, 'close'):
            try:
                dev.close()
            except Exception:
                pass
    print("✓ Temizleme tamamlandı.")


def _set_step_pins(s1, s2, s3, s4):
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)


def _single_step_motor(direction_positive):
    global current_step_sequence_index, current_motor_angle_global
    current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
        step_sequence)) % len(step_sequence)
    _set_step_pins(*step_sequence[current_step_sequence_index])
    current_motor_angle_global += (DEG_PER_STEP * (1 if direction_positive else -1))
    if current_motor_angle_global > 180: current_motor_angle_global -= 360
    if current_motor_angle_global < -180: current_motor_angle_global += 360
    time.sleep(STEP_MOTOR_INTER_STEP_DELAY)


def _move_motor_steps(num_steps, direction_positive):
    global current_step_sequence_index, current_motor_angle_global
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
            step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        current_motor_angle_global += (DEG_PER_STEP * (1 if direction_positive else -1))
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    if current_motor_angle_global > 180: current_motor_angle_global -= 360
    if current_motor_angle_global < -180: current_motor_angle_global += 360


def move_motor_to_absolute_angle(target_angle_deg):
    global current_motor_angle_global
    angle_diff = target_angle_deg - current_motor_angle_global
    if angle_diff > 180:
        angle_diff -= 360
    elif angle_diff < -180:
        angle_diff += 360
    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0:
        time.sleep(STEP_MOTOR_SETTLE_TIME)
        return
    direction_positive = (angle_diff > 0)
    _move_motor_steps(num_steps, direction_positive)
    current_motor_angle_global = target_angle_deg
    time.sleep(STEP_MOTOR_SETTLE_TIME)


# GÜNCELLENDİ: Fonksiyon adı ve içeriği tek bip için değiştirildi
def kisa_uyari_bip(bip_suresi):
    """Buzzer'ı bir defa kısa bir süre öttürür."""
    if buzzer:
        buzzer.on()
        time.sleep(bip_suresi)
        buzzer.off()


def update_lcd_display(message_type):
    global current_lcd_message_type, lcd, last_lcd_time_update
    now = time.time()
    if message_type == current_lcd_message_type and message_type != "normal_time": return
    if message_type == "normal_time" and (
            now - last_lcd_time_update < LCD_TIME_UPDATE_INTERVAL) and current_lcd_message_type == "normal_time": return
    if not lcd: return
    try:
        lcd.clear()
        if message_type == "alert_greeting":
            lcd.write_string("Merhaba")
            lcd.cursor_pos = (1, 0);
            lcd.write_string("Dream Pi")
        elif message_type == "normal_time":
            lcd.write_string("Dream Pi")
            lcd.cursor_pos = (1, 0);
            lcd.write_string(time.strftime("%H:%M:%S"))
            last_lcd_time_update = now
        current_lcd_message_type = message_type
    except Exception as e:
        print(f"LCD Yazma Hatası: {e}");
        current_lcd_message_type = "error"


# ==============================================================================

# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================
if __name__ == "__main__":
    atexit.register(release_resources_on_exit)
    if not init_hardware():
        sys.exit(1)

    print("\n>>> Sürekli Tarama Modu V4.2 Başlatıldı (Tek Kısa Bip) <<<")
    print(f"Algılama Eşiği: < {ALGILAMA_ESIGI_CM} cm")
    print("Durdurmak için Ctrl+C tuşlarına basın.")

    update_lcd_display("normal_time")
    current_direction_positive = True

    try:
        while True:
            _single_step_motor(current_direction_positive)

            if current_direction_positive and current_motor_angle_global >= SWEEP_ANGLE_MAX:
                current_motor_angle_global = SWEEP_ANGLE_MAX
                print(f"Sağ limit ({SWEEP_ANGLE_MAX}°) ulaşıldı, sola dönülüyor...")
                current_direction_positive = False
            elif not current_direction_positive and current_motor_angle_global <= -SWEEP_ANGLE_MAX:
                current_motor_angle_global = -SWEEP_ANGLE_MAX
                print(f"Sol limit (-{SWEEP_ANGLE_MAX}°) ulaşıldı, sağa dönülüyor...")
                current_direction_positive = True

            mesafe = sensor.distance * 100
            object_detected = (mesafe < ALGILAMA_ESIGI_CM)

            if status_led:
                if object_detected:
                    if led_is_blinking:
                        status_led.off();
                        time.sleep(0.01);
                        status_led.on()
                        led_is_blinking = False
                else:
                    if not led_is_blinking:
                        status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI, background=True)
                        led_is_blinking = True

            if object_detected:
                kisa_uyari_bip(BUZZER_BIP_SURESI)  # GÜNCELLENDİ: cift_bip yerine kisa_uyari_bip çağrılıyor
                update_lcd_display("alert_greeting")
            else:
                update_lcd_display("normal_time")

    except KeyboardInterrupt:
        print("\nKullanıcı tarafından durduruluyor...")
    finally:
        print("Program sonlanıyor...")
        if init_hardware_called_successfully:
            print("Motor başlangıç pozisyonuna (0°) getiriliyor...")
            move_motor_to_absolute_angle(0)
        else:
            print("Donanım başlatılamadığı için motor homing atlanıyor, pinler sıfırlanacak.")
            _set_step_pins(0, 0, 0, 0)
        print("Çıkış işlemleri tamamlandı.")