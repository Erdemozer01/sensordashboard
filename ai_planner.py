import os
import json
from google import generativeai
from dotenv import load_dotenv

# API anahtarını .env dosyasından yükle
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


def get_ai_mission_plan(scan_data_points):
    """
    Ham sensör verilerini alır, Gemini'ye gönderir ve bir görev planı JSON'ı döndürür.
    """
    if not GOOGLE_API_KEY:
        print("HATA: GOOGLE_API_KEY bulunamadı.")
        return None

    generativeai.configure(api_key=GOOGLE_API_KEY)
    model = generativeai.GenerativeModel('gemini-2.0-flash')

    # Gelen [(derece, mesafe), ...] listesini basit bir string'e çevir
    data_string = str(scan_data_points)

    # Gemini'ye hem ne yapacağını öğreten hem de istediğimiz formatı gösteren bir komut (prompt)
    prompt = f"""
    Sen, bir robotik keşif görev planlayıcısısın. Görevin, bir 2D ultrasonik sensörden gelen kaba tarama verilerini analiz etmek ve ilginç veya belirsiz bölgeleri araştırmak için stratejik bir görev planı oluşturmaktır.
    Sensör verileri (derece, cm cinsinden mesafe) çiftlerinden oluşan bir liste olarak veriliyor.

    Cevabın MUTLAKA görevlerin bir listesini içeren geçerli bir JSON nesnesi olmalıdır.
    Her görevin "type" alanı "scan" veya "move" olabilir.
    - "scan" görevleri şunları içermelidir: task_name, type, start_angle, end_angle, step_angle.
    - "move" görevleri şunları içermelidir: task_name, type, target_angle.

    ÖRNEK:
    Giriş Verisi: "[(0, 200), (30, 201), (60, 80), (90, 75), (120, 205)]"
    Senin JSON Cevabın:
    {{
      "analysis": "Tarama, 60 ile 90 derece arasında daha yakın bir nesne kümelenmesi gösteriyor. Hipotezim, bunun uzak bir duvara karşı duran bir mobilya olduğu yönünde. Bu kümeyi detaylı bir şekilde tarayacağım.",
      "mission_plan": [
        {{ "task_name": "Nesne kumesine odaklan", "type": "scan", "start_angle": 55, "end_angle": 95, "step_angle": 2.0 }},
        {{ "task_name": "Eve Don", "type": "move", "target_angle": 0 }}
      ]
    }}
    ---

    ŞİMDİ, AŞAĞIDAKİ VERİ İÇİN BİR GÖREV PLANI OLUŞTUR:
    Giriş Verisi: "{data_string}"
    Senin JSON Cevabın:
    """

    try:
        print("\n[AI Planner] Yapay zekadan görev planı isteniyor...")
        response = model.generate_content(prompt)

        # Yapay zeka cevabındaki potansiyel markdown formatını temizle
        clean_response_text = response.text.replace('```json', '').replace('```', '').strip()

        print(f"[AI Planner] Gelen Ham Cevap:\n{clean_response_text}")

        # Cevabı JSON olarak ayrıştır
        plan_data = json.loads(clean_response_text)

        print(f"[AI Planner] Analiz: {plan_data.get('analysis')}")
        return plan_data.get("mission_plan")

    except Exception as e:
        print(f"[AI Planner] HATA: Yapay zeka planı alınamadı veya ayrıştırılamadı. Hata: {e}")
        return None