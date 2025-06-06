import logging
import os
import sys
import subprocess
import time
import io
import signal
import psutil
import pandas as pd
import numpy as np
from matplotlib.pyplot import figure
from scipy.spatial import ConvexHull
from sklearn.cluster import DBSCAN
from sklearn.linear_model import RANSACRegressor

try:
    from django.db.models import Max
    from scanner.models import Scan, ScanPoint

    DJANGO_MODELS_AVAILABLE = True
except Exception as e:
    print(f"UYARI: Django modelleri import edilemedi: {e}")
    DJANGO_MODELS_AVAILABLE = False
    Scan, ScanPoint = None, None

from django_plotly_dash import DjangoDash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt

try:
    import google.generativeai

    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False

from dotenv import load_dotenv

load_dotenv()
google_api_key = os.getenv("GOOGLE_API_KEY")

# --- SABİTLER VE UYGULAMA BAŞLATMA ---
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(os.getcwd(), SENSOR_SCRIPT_FILENAME)
SENSOR_SCRIPT_PID_FILE = '/tmp/sensor_scan_script.pid'

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])


# --- YARDIMCI FONKSİYONLAR (Yardımcı fonksiyonlar aynı kalabilir, buraya eklemiyorum) ---
# ... (Önceki kodunuzdaki tüm yardımcı fonksiyonlar buraya gelecek) ...
# get_latest_scan, add_scan_rays, update_polar_graph vs. hepsi...

def is_process_running(pid):
    if pid is None: return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def get_latest_scan():
    if not DJANGO_MODELS_AVAILABLE: return None
    try:
        # Önce tamamlanmış veya hata vermiş en son taramayı bul
        latest_completed = Scan.objects.exclude(status=Scan.Status.RUNNING).order_by('-start_time').first()
        # Eğer çalışan bir tarama varsa, onu tercih et
        running_scan = Scan.objects.filter(status=Scan.Status.RUNNING).order_by('-start_time').first()
        return running_scan if running_scan else latest_completed
    except Exception as e:
        print(f"DB Hatası (get_latest_scan): {e}");
        return None


# ... Diğer tüm yardımcı fonksiyonlarınızı buraya ekleyin... (add_scan_rays, analyze_environment_shape, vs.)

# --- LAYOUT ---
# Layout'u daha basit ve hataya dayanıklı hale getirelim.
# Özellikle AI kısmını butonla tetiklenecek şekilde değiştirelim.

app.layout = html.Div(style={'padding': '20px'}, children=[
    dbc.NavbarSimple(brand="Dream Pi", brand_href="/", color="primary", dark=True, sticky="top", fluid=True, className="mb-4"),
    dbc.Row([
        # --- KONTROL KOLONU ---
        dbc.Col(md=4, children=[
            dbc.Card([
                dbc.CardHeader("Kontrol Paneli"),
                dbc.CardBody([
                    html.H6("Tarama Betiğini Kontrol Et"),
                    dbc.Row([
                        # --- DEĞİŞİKLİK BURADA ---
                        dbc.Col(dbc.Button('Yeni Tarama Başlat', id='start-scan-button', color="success", className="w-100")),
                        # --- DEĞİŞİKLİK BURADA ---
                        dbc.Col(dbc.Button('Çalışan Taramayı Durdur', id='stop-scan-button', color="danger", className="w-100")),
                    ]),
                    html.Div(id='scan-status-message', className="text-center mt-2", style={'minHeight': '40px'}),
                    html.Hr(),
                    html.H6("Yapay Zeka Analizi (Son Tarama İçin)"),
                    # --- DEĞİŞİKLİK BURADA ---
                    dbc.Button('Yorum ve Resim Oluştur', id='run-ai-button', color="info", className="w-100 mt-2"),
                ])
            ]),
            dbc.Card(id='system-card', className="mt-3"),
            dbc.Card(id='stats-card', className="mt-3"),
        ]),
        # --- GÖRSELLEŞTİRME KOLONU ---
        dbc.Col(md=8, children=[
            dcc.Graph(id='main-graph', style={'height': '70vh'}),
            dbc.Card([
                dbc.CardHeader("Yapay Zeka Analiz Sonuçları"),
                dbc.CardBody(
                    dcc.Loading(children=[
                        html.Div(id='ai-yorum-sonucu'),
                        html.Div(id='ai-image')
                    ])
                )
            ], className="mt-3")
        ])
    ]),
    dcc.Interval(id='interval-component', interval=2500, n_intervals=0),
])


# --- CALLBACK'LER ---

# Sistem ve sensör durumunu güncelleyen callback
@app.callback(
    [Output('system-card', 'children'), Output('stats-card', 'children')],
    Input('interval-component', 'n_intervals')
)
def update_status_cards(n):
    # Sistem durumu (CPU/RAM)
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    system_body = [
        dbc.CardHeader("Sistem Durumu"),
        dbc.CardBody([
            html.P(f"CPU Kullanımı: {cpu}%"), dbc.Progress(value=cpu),
            html.P(f"RAM Kullanımı: {ram}%", className="mt-2"), dbc.Progress(value=ram, color="info"),
        ])
    ]

    # Sensör anlık değerleri
    scan = get_latest_scan()
    angle_s, dist_s = "--°", "-- cm"
    if scan and (point := scan.points.order_by('-timestamp').first()):
        angle_s = f"{point.derece:.1f}°"
        dist_s = f"{point.mesafe_cm:.1f} cm"
    stats_body = [
        dbc.CardHeader("Anlık Sensör Değerleri"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([html.H6("Mevcut Açı:"), html.H4(angle_s)]),
                dbc.Col([html.H6("Mevcut Mesafe:"), html.H4(dist_s)])
            ])
        ])
    ]
    return system_body, stats_body


# Tarama başlatma/durdurma
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks'), Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_scan_buttons(start_clicks, stop_clicks):
    # Bu kısım basitleştirildi. Kendi başlat/durdur kodunuzu buraya entegre edebilirsiniz.
    # Önemli olan `subprocess.Popen` ile `sensor_script.py`'yi doğru argümanlarla çağırmak.
    # Örnek: subprocess.Popen(['python', SENSOR_SCRIPT_PATH, '--scan_duration_angle', '270', ...])
    return "Buton özelliği şimdilik pasif. Betiği manuel çalıştırın."


# Ana grafiği güncelleyen callback
@app.callback(
    Output('main-graph', 'figure'),
    Input('interval-component', 'n_intervals')
)
def update_main_graph(n):
    scan = get_latest_scan()
    if not scan:
        return go.Figure(layout={'title': 'Veri bekleniyor... Lütfen bir tarama başlatın.'})

    points_qs = scan.points.all().values('x_cm', 'y_cm', 'derece', 'mesafe_cm')
    if not points_qs:
        return go.Figure(layout={'title': f'Tarama #{scan.id} bulundu ama hiç noktası yok.'})

    df = pd.DataFrame(list(points_qs))
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['y_cm'], y=df['x_cm'], mode='markers',
        marker=dict(size=5, color=df['derece'], colorscale='Viridis', showscale=True),
        name='Tarama Noktaları'
    ))
    fig.add_trace(
        go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))
    fig.update_layout(title_text=f'Tarama Sonuçları (ID: {scan.id})', xaxis_title="Yatay Mesafe (cm)",
                      yaxis_title="Dikey Mesafe (cm)")
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


# --- YENİ TASARIM: AYRI YAPAY ZEKA CALLBACK'İ ---

def yorumla_tablo_verisi_gemini(df):
    # Bu fonksiyon önceki versiyonla aynı kalabilir
    # ...
    return "Yapay zeka metin yorumu (örnek)"


def image_generate(prompt_text):
    # Bu fonksiyon da en son düzelttiğimiz haliyle kalmalı
    # ...
    return dbc.Alert("Resim oluşturma özelliği geçici olarak pasif.", color="warning")


@app.callback(
    [Output('ai-yorum-sonucu', 'children'), Output('ai-image', 'children')],
    Input('run-ai-button', 'n_clicks'),
    prevent_initial_call=True
)
def run_ai_analysis(n_clicks):
    if n_clicks == 0:
        return no_update, no_update

    scan = get_latest_scan()
    if not scan:
        return dbc.Alert("Analiz edilecek bir tarama bulunamadı.", color="danger"), ""

    points_qs = scan.points.all().values('derece', 'mesafe_cm')
    if not points_qs:
        return dbc.Alert(f"Tarama #{scan.id} için nokta verisi bulunamadı.", color="danger"), ""

    df_for_ai = pd.DataFrame(list(points_qs))

    # 1. Metin Yorumu Oluştur
    yorum_metni = yorumla_tablo_verisi_gemini(df_for_ai)

    # 2. Resim Oluştur
    resim_bileseni = image_generate(yorum_metni)

    yorum_bileseni = dbc.Alert(dcc.Markdown(yorum_metni), color="success")

    return yorum_bileseni, resim_bileseni