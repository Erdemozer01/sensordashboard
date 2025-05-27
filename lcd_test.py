# lcd_test.py
from RPLCD.i2c import CharLCD
import time

# --- LCD Ayarları ---
# Lütfen bu ayarları kendi LCD'nize ve bağlantınıza göre doğrulayın/güncelleyin:
LCD_I2C_ADDRESS = 0x27  # sudo i2cdetect -y 1 komutuyla bulduğunuz adres
LCD_PORT_EXPANDER = 'PCF8574'  # Çoğu I2C LCD modülünde bu entegre kullanılır
LCD_COLS = 16  # LCD'nizin sütun sayısı (16x2 için 16, 20x4 için 20)
LCD_ROWS = 2  # LCD'nizin satır sayısı (16x2 için 2, 20x4 için 4)
# Raspberry Pi'de I2C portu genellikle 1'dir. Farklı bir port kullanıyorsanız (örn: I2C0 için port=0) değiştirin.
I2C_PORT = 1

lcd = None  # Global tanımla ki finally bloğunda kullanılabilsin

try:
    print(f"LCD Testi Başlatılıyor...")
    print(f"  Adres: {hex(LCD_I2C_ADDRESS)}")
    print(f"  Genişletici: {LCD_PORT_EXPANDER}")
    print(f"  Boyut: {LCD_COLS}x{LCD_ROWS}")
    print(f"  I2C Port: {I2C_PORT}")

    lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER,
                  address=LCD_I2C_ADDRESS,
                  port=I2C_PORT,
                  cols=LCD_COLS,
                  rows=LCD_ROWS,
                  dotsize=8,  # Karakter piksel boyutu, genellikle 8
                  charmap='A02',  # Genellikle Batı Avrupa karakter seti için uygun
                  auto_linebreaks=True)  # Satır sonuna gelince otomatik alta geç

    print("LCD başarıyla başlatıldı. Ekran temizleniyor...")
    lcd.clear()
    time.sleep(0.5)

    # 1. Satıra Yazma
    lcd.cursor_pos = (0, 0)  # Satır 0, Sütun 0
    lcd.write_string("Merhaba Dream Pi!")
    print("Satır 1'e yazıldı: Merhaba Dream Pi!")
    time.sleep(2)

    # 2. Satıra Yazma (Eğer LCD_ROWS >= 2 ise)
    if LCD_ROWS >= 2:
        lcd.cursor_pos = (1, 0)  # Satır 1, Sütun 0
        lcd.write_string("Test Basarili :)")
        print("Satır 2'ye yazıldı: Test Basarili :)")
    time.sleep(3)

    # Değişken Değerleri Yazdırma ve Ekranı Güncelleme
    print("Değişkenler LCD'ye yazdırılıyor...")
    for i in range(5):
        lcd.clear()
        lcd.cursor_pos = (0, 0)
        lcd.write_string(f"Sayac Degeri: {i}")
        if LCD_ROWS >= 2:
            lcd.cursor_pos = (1, 0)
            lcd.write_string(f"Zaman: {time.strftime('%M:%S')}")  # Sadece Dakika:Saniye
        time.sleep(1)

    lcd.clear()
    lcd.write_string("Test Tamamlandi!")
    print("Test tamamlandı.")
    time.sleep(2)
    lcd.clear()

except Exception as e:
    print(f"LCD testi sırasında bir HATA oluştu: {e}")
    print("Lütfen şunları kontrol edin:")
    print("  - I2C bağlantılarınız (SDA, SCL, VCC, GND).")
    print("  - LCD I2C adresiniz (sudo i2cdetect -y 1 ile doğrulayın).")
    print("  - RPLCD kütüphanesinin kurulu olduğundan emin olun (`pip install RPLCD`).")
    print("  - Raspberry Pi'de I2C arayüzünün etkinleştirildiğinden emin olun (`sudo raspi-config`).")

finally:
    if lcd:
        # LCD'yi kapatma veya temizleme (RPLCD için genellikle clear yeterli)
        try:
            lcd.clear()
            # Bazı RPLCD versiyonları veya LCD modelleri arka ışığı kontrol etmeyi destekler
            # lcd.backlight_enabled = False
        except Exception as e_close:
            print(f"LCD kapatılırken/temizlenirken hata: {e_close}")
        print("LCD kaynakları serbest bırakıldı (ekran temizlendi).")