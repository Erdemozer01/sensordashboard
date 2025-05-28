# sensors/hardware.py
import time
import os
from gpiozero import AngularServo, DistanceSensor, LED
from RPLCD.i2c import CharLCD

from .config import (TRIG_PIN, ECHO_PIN, SERVO_PIN, RED_LED_PIN, GREEN_LED_PIN,
                     YELLOW_LED_PIN, LCD_I2C_ADDRESS, LCD_PORT_EXPANDER,
                     LCD_COLS, LCD_ROWS, I2C_PORT, SCAN_START_ANGLE, SCAN_END_ANGLE)

# Global donanım değişkenleri
sensor = None
red_led = None
green_led = None
yellow_led = None
servo = None
lcd = None


def init_hardware():
    """Tüm donanım bileşenlerini başlatır."""
    global sensor, red_led, green_led, yellow_led, servo, lcd
    hardware_ok = True

    try:
        print(f"[{os.getpid()}] Donanımlar başlatılıyor...")
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.0, queue_len=2)
        red_led = LED(RED_LED_PIN)
        green_led = LED(GREEN_LED_PIN)
        yellow_led = LED(YELLOW_LED_PIN)

        servo = AngularServo(SERVO_PIN, min_angle=0, max_angle=180,
                             initial_angle=None,
                             min_pulse_width=0.0005, max_pulse_width=0.0025)

        red_led.off()
        green_led.off()
        yellow_led.off()

        # Servoyu ortaya al ve bekle
        target_center_angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
        if servo:
            servo.angle = target_center_angle
            print(f"[{os.getpid()}] Servo ({target_center_angle}°) ortaya alındı.")
            time.sleep(0.7)

        print(f"[{os.getpid()}] Temel donanımlar başarıyla başlatıldı.")
    except Exception as e:
        print(f"[{os.getpid()}] Temel donanım başlatma hatası: {e}")
        hardware_ok = False

    # LCD Başlatma
    if hardware_ok:
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, dotsize=8, charmap='A02',
                          auto_linebreaks=False)
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Merhaba")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Dream Pi Hazir!")
            time.sleep(3)
            print(f"[{os.getpid()}] LCD Ekran (Adres: {hex(LCD_I2C_ADDRESS)}) başarıyla başlatıldı.")
        except Exception as e_lcd_init:
            print(f"[{os.getpid()}] UYARI: LCD başlatma hatası: {e_lcd_init}. LCD olmadan devam edilecek.")
            lcd = None
    else:
        lcd = None

    return hardware_ok


def cleanup_hardware():
    """Tüm donanım bileşenlerini güvenli bir şekilde kapatır."""
    global sensor, red_led, green_led, yellow_led, servo, lcd
    pid = os.getpid()

    print(f"[{pid}] Donanım kapatılıyor...")

    if servo:
        try:
            print(f"[{pid}] Servo ortaya alınıyor...")
            servo.angle = (SCAN_START_ANGLE + SCAN_END_ANGLE) / 2
            time.sleep(0.5)
            servo.detach()
            servo.close()
            print(f"[{pid}] Servo kapatıldı.")
        except Exception as e_servo:
            print(f"[{pid}] Servo kapatılırken hata: {e_servo}")

    for led_obj in [red_led, green_led, yellow_led]:
        if led_obj:
            if hasattr(led_obj, 'is_active') and led_obj.is_active:
                led_obj.off()
            if hasattr(led_obj, 'close'):
                led_obj.close()

    if sensor and hasattr(sensor, 'close'):
        sensor.close()

    if lcd:
        try:
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Dream Pi")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Taramayi yapti")
            time.sleep(3)
            lcd.clear()
            lcd.cursor_pos = (0, 0)
            lcd.write_string("Mehmet Erdem")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("OZER")
            print(f"[{pid}] LCD temizlendi ve mesaj yazıldı.")
        except Exception as e_lcd_clear:
            print(f"[{pid}] LCD temizlenirken hata: {e_lcd_clear}")

    print(f"[{pid}] LED'ler, sensör ve LCD kapatıldı.")


def update_lcd_display(message_line1="", message_line2=""):
    """LCD ekranını günceller."""
    if lcd:
        try:
            if message_line1:
                lcd.cursor_pos = (0, 0)
                lcd.write_string(message_line1.ljust(LCD_COLS)[:LCD_COLS])
            if message_line2 and LCD_ROWS > 1:
                lcd.cursor_pos = (1, 0)
                lcd.write_string(message_line2.ljust(LCD_COLS)[:LCD_COLS])
        except Exception as e:
            print(f"LCD güncelleme hatası: {e}")


def set_servo_angle(angle):
    """Servoyu belirtilen açıya ayarlar."""
    if servo:
        servo.angle = angle
        time.sleep(SERVO_SETTLE_TIME)