from gpiozero import Buzzer
from time import sleep

# sensor_script.py'deki BUZZER_PIN ile aynı pini kullanın
BUZZER_PIN_TEST = 22
buzzer = None

print(f"Buzzer Testi Başlatılıyor (GPIO{BUZZER_PIN_TEST})...")

try:
    buzzer = Buzzer(BUZZER_PIN_TEST)

    print("Buzzer 2 saniye boyunca çalacak...")
    buzzer.on()  # Buzzer'ı aç
    sleep(2)  # 2 saniye bekle
    buzzer.off()  # Buzzer'ı kapat
    sleep(0.5)  # Kısa bir sessizlik

    print("Buzzer 3 kez bip sesi çıkaracak...")
    for _ in range(3):
        buzzer.beep(on_time=0.2, off_time=0.2, n=1, background=False)  # Tek bip, bekle
        # Veya buzzer.on(); sleep(0.2); buzzer.off(); sleep(0.2) şeklinde de olur.

    print("Test tamamlandı.")

except Exception as e:
    print(f"Buzzer testi sırasında bir hata oluştu: {e}")
    print("GPIO pin numarasını ve bağlantılarınızı kontrol edin.")
finally:
    if buzzer:
        buzzer.off()  # Her ihtimale karşı kapalı olduğundan emin ol
        buzzer.close()  # GPIO kaynaklarını serbest bırak
    print("Buzzer kaynakları serbest bırakıldı.")