# lcd_test.py

import time
# Eğer RPLCD kütüphanesi yüklü değilse, terminale şunu yazın:
# pip install RPLCD
from RPLCD.i2c import CharLCD

# ===================================================================
# --- AYARLAR ---
# 'i2cdetect -y 1' komutundan aldığınız adresi buraya yazın!
# En yaygın adresler 0x27 veya 0x3f'dir.
LCD_I2C_ADDRESS = 0x27
# ===================================================================

# Diğer standart ayarlar
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1  # Raspberry Pi 1 için 0, sonraki modeller için 1

lcd = None
print("LCD Testi Başlatılıyor...")
print(f"Hedef I2C Adresi: {hex(LCD_I2C_ADDRESS)}")

try:
    # LCD'yi başlatmayı dene
    lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER,
                  address=LCD_I2C_ADDRESS,
                  port=I2C_PORT,
                  cols=LCD_COLS,
                  rows=LCD_ROWS,
                  auto_linebreaks=False)  # Test için otomatik satır atlamayı kapat

    print("✓ LCD başarıyla başlatıldı.")

    # Ekranı temizle
    lcd.clear()
    print("✓ Ekran temizlendi.")

    # Ekrana yazı yazdır
    lcd.write_string("Merhaba Dunya!")
    lcd.cursor_pos = (1, 0)  # Alt satıra geç
    lcd.write_string(f"Adres: {hex(LCD_I2C_ADDRESS)}")

    print(f"✓ Ekrana 'Merhaba Dunya!' ve '{hex(LCD_I2C_ADDRESS)}' adresi yazıldı.")
    print("10 saniye boyunca yazılar ekranda kalacak...")

    time.sleep(10)

    print("✓ Test tamamlandı. Ekran temizleniyor.")
    lcd.clear()

except Exception as e:
    print("\n" + "=" * 40)
    print("!!! HATA: LCD ile iletişim kurulamadı!")
    print(f"Detaylı Hata Mesajı: {e}")
    print("=" * 40)
    print("\nLütfen şunları kontrol edin:")
    print("1. 'sudo i2cdetect -y 1' komutunun çıktısını kontrol edin. Adres doğru mu?")
    print("2. Kablo bağlantılarını (SDA, SCL, 5V, GND) tekrar kontrol edin.")
    print("3. 'sudo raspi-config' ile I2C arayüzünün etkin olduğundan emin olun.")
    print("4. Gerekirse 'pip install RPLCD' komutuyla kütüphaneyi yükleyin.")