import google.generativeai as genai
import os
from dotenv import load_dotenv
import logging

# Diğer kütüphanelerden gelen gereksiz uyarıları gizle
logging.basicConfig(level=logging.INFO)

try:
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("HATA: GOOGLE_API_KEY bulunamadı. Lütfen .env dosyanızı kontrol edin.")
    else:
        genai.configure(api_key=api_key)

        print("=" * 40)
        print("Hesabınızda Kullanılabilen Modeller:")
        print("=" * 40)

        found_image_model = False
        for m in genai.list_models():
            # Sadece metin istemi alabilen modelleri listele
            if 'generateContent' in m.supported_generation_methods:
                # Resim oluşturma potansiyeli olanları işaretle
                if 'gemini-1.5' in m.name or 'vision' in m.name:
                    print(f"- {m.name}  <-- Resim için potansiyel aday")
                    found_image_model = True
                else:
                    print(f"- {m.name}")

        if not found_image_model:
            print("\nUYARI: Resim oluşturma için uygun bir model ('gemini-1.5' içeren) bulunamadı.")

        print("\nLütfen yukarıdaki listeden 'Resim için potansiyel aday' olarak işaretlenmiş bir modeli seçip")
        print("kodunuzdaki 'gemini-1.5-flash-latest' ile değiştirerek tekrar deneyin.")

except Exception as e:
    print(f"\nBir hata oluştu: {e}")