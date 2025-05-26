from gpiozero import AngularServo
import time

SERVO_PIN_TEST = 12 # Kendi pininizi yazın
# Deneme amaçlı pulse width değerleri, SG90 için genellikle 0.0005-0.0025 aralığıdır
# min_pw = 0.0005
# max_pw = 0.0025

# Farklı değerler deneyebilirsiniz, örn:
min_pw = 0.0006
max_pw = 0.0024

servo_test = AngularServo(SERVO_PIN_TEST, min_angle=0, max_angle=180, min_pulse_width=min_pw, max_pulse_width=max_pw)

try:
    print("Servo testi başlıyor...")
    print("Ortaya gidiyor (90 derece)...")
    servo_test.angle = 90
    time.sleep(2)

    print("Minimum açıya gidiyor (0 derece)...")
    servo_test.angle = 0
    time.sleep(2)

    print("Maksimum açıya gidiyor (180 derece)...")
    servo_test.angle = 180
    time.sleep(2)

    print("Tekrar ortaya gidiyor...")
    servo_test.angle = 90
    time.sleep(1)

except KeyboardInterrupt:
    print("Test durduruldu.")
finally:
    print("Servo serbest bırakılıyor.")
    servo_test.detach() # veya servo_test.close()

print("Test bitti.")