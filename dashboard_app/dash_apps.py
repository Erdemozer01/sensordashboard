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
    from scanner.models import Scan, ScanPoint  # 'scanner' yerine kendi Django uygulama adınızı yazın

    DJANGO_MODELS_AVAILABLE = True
    print("Dashboard: Django modelleri başarıyla import edildi.")
except ModuleNotFoundError:
    print("UYARI: 'scanner.models' import edilemedi. Django proje yapınızı ve PYTHONPATH'ı kontrol edin.")
    print("Django entegrasyonu olmadan devam edilecek, veritabanı işlemleri çalışmayabilir.")
    DJANGO_MODELS_AVAILABLE = False
except Exception as e:
    print(f"Django modelleri import edilirken bir hata oluştu: {e}")
    DJANGO_MODELS_AVAILABLE = False
    Scan, ScanPoint = None, None  # Modelleri None olarak ayarla

# Dash ve Plotly Kütüphaneleri
from django_plotly_dash import DjangoDash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt
import plotly, matplotlib

# Diğer importlar
try:
    from google import genai

    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    print("UYARI: 'google.generativeai' kütüphanesi bulunamadı. AI yorumlama özelliği çalışmayacak.")
    GOOGLE_GENAI_AVAILABLE = False

from dotenv import load_dotenv

load_dotenv()  # .env dosyasından ortam değişkenlerini yükler
google_api_key = os.getenv("GOOGLE_API_KEY")

# ==============================================================================
# --- SABİTLER VE UYGULAMA BAŞLATMA ---
# ==============================================================================
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(os.getcwd(), SENSOR_SCRIPT_FILENAME)  # manage.py ile aynı dizin varsayılıyor
SENSOR_SCRIPT_LOCK_FILE = '/tmp/sensor_scan_script.lock'
SENSOR_SCRIPT_PID_FILE = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_DURATION_ANGLE = 270.0
DEFAULT_UI_SCAN_STEP_ANGLE = 10.0
DEFAULT_UI_BUZZER_DISTANCE = 10
DEFAULT_UI_INVERT_MOTOR = False
DEFAULT_UI_STEPS_PER_REVOLUTION = 4096

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# ==============================================================================
# --- NAVBAR OLUŞTURMA ---
# ==============================================================================
navbar = dbc.NavbarSimple(
    children=[
        dbc.NavItem(dbc.NavLink("Admin Paneli", href="/admin/", external_link=True, target="_blank")),
    ],
    brand="Dream Pi",
    brand_href="/",
    color="primary",
    dark=True,
    sticky="top",
    fluid=True,
    className="mb-4"
)
# ==============================================================================

# ==============================================================================
# --- LAYOUT (ARAYÜZ) BİLEŞENLERİ ---
# ==============================================================================
title_card = dbc.Row(
    [dbc.Col(html.H1("Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12), html.Hr()]
)

control_panel = dbc.Card([
    dbc.CardHeader("Tarama Kontrol ve Ayarları", className="bg-primary text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Button('Başlat', id='start-scan-button', n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Durdur', id='stop-scan-button', n_clicks=0,
                                className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),
        html.Hr(),
        html.H6("Yapay Zeka Seçimi:", className="mt-3"),
        dcc.Dropdown(
            id='ai-model-dropdown',
            options=[
                {'label': 'Gemini Pro (Gelişmiş)', 'value': 'gemini-1.5-pro-latest'},
                # Güncel model adlarını kontrol edin
                {'label': 'Gemini Flash (Hızlı)', 'value': 'gemini-2.5-flash-preview-05-20'},
            ],
            placeholder="Yorumlama için bir model seçin...",
            clearable=True,
            className="mb-3"
        ),
        html.Hr(),
        html.H6("Tarama Parametreleri:", className="mt-2"),
        dbc.InputGroup([dbc.InputGroupText("Tarama Açısı (°)", style={"width": "150px"}),
                        dbc.Input(id="scan-duration-angle-input", type="number", value=DEFAULT_UI_SCAN_DURATION_ANGLE,
                                  min=10, max=720, step=1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "150px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=0.1,
                                  max=45, step=0.1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Uyarı Mes. (cm)", style={"width": "150px"}),
                        dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE, min=0,
                                  max=200, step=1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Motor Adım/Tur", style={"width": "150px"}),
                        dbc.Input(id="steps-per-rev-input", type="number", value=DEFAULT_UI_STEPS_PER_REVOLUTION,
                                  min=500, max=10000, step=1)], className="mb-2"),
        dbc.Checkbox(id="invert-motor-checkbox", label="Motor Yönünü Ters Çevir", value=DEFAULT_UI_INVERT_MOTOR,
                     className="mt-2 mb-2"),
    ])
])

stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"), dbc.CardBody(dbc.Row(
    [dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=3,
             className="text-center border-end"),
     dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]),
             id='current-distance-col', width=3, className="text-center rounded border-end"),
     dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=3,
             className="text-center border-end"),
     dbc.Col(html.Div([html.H6("Max. Algılanan Mesafe:"), html.H4(id='max-detected-distance', children="-- cm")]),
             width=3, className="text-center")]))], className="mb-3")

system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"), dbc.CardBody(
    [dbc.Row([dbc.Col(html.Div([html.H6("Sensör Betiği Durumu:"), html.H5(id='script-status', children="Beklemede")]))],
             className="mb-2"),
     dbc.Row([dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                                dbc.Progress(id='cpu-usage', value=0, color="success", style={"height": "20px"},
                                             className="mb-1", label="0%")])),
              dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"),
                                dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"},
                                             className="mb-1", label="0%")]))])])], className="mb-3")

export_card = dbc.Card([dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"), dbc.CardBody(
    [dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
     dcc.Download(id='download-csv'),
     dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
     dcc.Download(id='download-excel')])], className="mb-3")

analysis_card = dbc.Card(
    [
        dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
        dbc.CardBody(
            [
                dbc.Row(
                    [
                        dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(id='calculated-area', children="-- cm²")]),
                        dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length', children="-- cm")])
                    ]
                ),
                dbc.Row(
                    [
                        dbc.Col([html.H6("Max Genişlik:"), html.H4(id='max-width', children="-- cm")]),
                        dbc.Col([html.H6("Max Derinlik:"), html.H4(id='max-depth', children="-- cm")])
                    ],
                    className="mt-2"
                )
            ]
        )
    ]
)

estimation_card = dbc.Card(
    [
        dbc.CardHeader("Akıllı Ortam Analizi", className="bg-success text-white"),
        dbc.CardBody(html.Div("Tahmin: Bekleniyor...", id='environment-estimation-text', className="text-center"))

    ]
)

visualization_tabs = dbc.Tabs(
    [
        dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita",
                tab_id="tab-map"),
        dbc.Tab(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), label="Regresyon Analizi",
                tab_id="tab-regression"),
        dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik", tab_id="tab-polar"),
        dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)",
                tab_id="tab-time"),
        dbc.Tab(dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]),
                label="Veri Tablosu", tab_id="tab-datatable")
    ],
    id="visualization-tabs-main",
    active_tab="tab-map"
)

app.layout = html.Div(  # EN DIŞ SARMALAYICI dbc.Container yerine html.Div OLARAK DEĞİŞTİRİLDİ
    style={
        'padding': '20px'  # Her yönden 20 piksel boşluk
    },
    children=[
        navbar,
        dbc.Row([
            dbc.Col(
                [
                    control_panel,
                    html.Br(),
                    stats_panel,
                    html.Br(),
                    system_card,
                    html.Br(),
                    export_card
                ],
                md=4,
                className="mb-3"
            ),
            dbc.Col(
                [
                    visualization_tabs,
                    html.Br(),
                    dbc.Row(
                        [
                            dbc.Col(analysis_card, md=8),
                            dbc.Col(estimation_card, md=4)
                        ]
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Card(
                                        [
                                            dbc.CardHeader("Akıllı Yorumlama (Yapay Zeka)",
                                                           className="bg-info text-white"),
                                            dbc.CardBody(
                                                dcc.Loading(
                                                    id="loading-ai-comment",
                                                    type="default",
                                                    children=[
                                                        html.Div(id='ai-yorum-sonucu', children=[
                                                            html.P(
                                                                "Yorum almak için yukarıdan bir yapay zeka modeli seçin."),
                                                        ], className="text-center mt-2")
                                                    ]
                                                )
                                            )
                                        ],
                                        className="mt-3"
                                    )
                                ],
                                md=12
                            )
                        ],
                        className="mt-3"
                    )
                ],
                md=8
            )
        ]),
        dcc.Store(id='clustered-data-store'),
        dbc.Modal(
            [dbc.ModalHeader(dbc.ModalTitle(id="modal-title")), dbc.ModalBody(id="modal-body")],
            id="cluster-info-modal",
            is_open=False,
            centered=True
        ),
        dcc.Interval(id='interval-component-main', interval=2500, n_intervals=0),
        dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
    ]
)


# ==============================================================================

# ==============================================================================
# --- YARDIMCI FONKSİYONLAR ---
# (Bu fonksiyonlarda bir değişiklik yok, önceki tam versiyondaki gibiler)
# ==============================================================================
def is_process_running(pid):
    if pid is None: return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def get_latest_scan():
    if not DJANGO_MODELS_AVAILABLE: return None
    try:
        running_scan = Scan.objects.filter(status=Scan.Status.RUNNING).order_by('-start_time').first()
        if running_scan:
            return running_scan
        return Scan.objects.order_by('-start_time').first()
    except Exception as e:
        print(f"DB Hatası (get_latest_scan): {e}")
        return None


def add_scan_rays(fig, df):
    if df.empty or not all(col in df.columns for col in ['x_cm', 'y_cm']): return
    x_lines, y_lines = [], []
    for _, row in df.iterrows():
        x_lines.extend([0, row['y_cm'], None])  # y_cm x eksenine, x_cm y eksenine çiziliyor olabilir, kontrol edin
        y_lines.extend([0, row['x_cm'], None])
    fig.add_trace(
        go.Scatter(x=x_lines, y=y_lines, mode='lines', line=dict(color='rgba(255,100,100,0.4)', dash='dash', width=1),
                   showlegend=False))


def add_sector_area(fig, df):
    if df.empty or not all(col in df.columns for col in ['x_cm', 'y_cm']): return
    poly_x, poly_y = df['y_cm'].tolist(), df['x_cm'].tolist()  # Yine x ve y eksenleri kontrol edilmeli
    fig.add_trace(go.Scatter(x=[0] + poly_x + [0], y=[0] + poly_y + [0], mode='lines', fill='toself',
                             fillcolor='rgba(255,0,0,0.15)', line=dict(color='rgba(255,0,0,0.4)'),
                             name='Taranan Sektör'))


def add_sensor_position(fig):
    fig.add_trace(
        go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))


def update_polar_graph(fig, df):
    if df.empty or not all(col in df.columns for col in ['mesafe_cm', 'derece']): return
    fig.add_trace(go.Scatterpolar(r=df['mesafe_cm'], theta=df['derece'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 250]),
                                 angularaxis=dict(direction="clockwise", period=360, thetaunit="degrees")))


def update_time_series_graph(fig, df):
    if df.empty or 'timestamp' not in df.columns or 'mesafe_cm' not in df.columns:
        fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Veri Yok'))
        return
    try:
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df_s = df.copy()  # Orijinal DataFrame'i değiştirmemek için kopya al
            df_s['timestamp'] = pd.to_datetime(df_s['timestamp'], unit='s', errors='coerce')
            df_s = df_s.sort_values(by='timestamp')
        else:
            df_s = df.sort_values(by='timestamp')

        fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['mesafe_cm'], mode='lines+markers', name='Mesafe'))
    except Exception as e:
        print(f"Zaman serisi grafiği oluşturulurken HATA: {e}")
        fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Grafik Hatası'))
    fig.update_layout(xaxis_title="Zaman", yaxis_title="Mesafe (cm)", xaxis=dict(tickformat='%H:%M:%S'))


def find_clearest_path(df_valid):
    if df_valid.empty or not all(
        col in df_valid.columns for col in ['mesafe_cm', 'derece']): return "En açık yol için veri yok."
    try:
        df_filtered = df_valid[df_valid['mesafe_cm'] > 0]
        if df_filtered.empty: return "Geçerli pozitif mesafe bulunamadı."
        cp = df_filtered.loc[df_filtered['mesafe_cm'].idxmax()]
        return f"En Açık Yol: {cp['derece']:.1f}° yönünde, {cp['mesafe_cm']:.1f} cm."
    except Exception as e:
        print(f"En açık yol hesaplama hatası: {e}"); return "En açık yol hesaplanamadı."


def analyze_polar_regression(df_valid):
    if len(df_valid) < 5 or not all(
        col in df_valid.columns for col in ['mesafe_cm', 'derece']): return None, "Polar regresyon için yetersiz veri."
    X, y = df_valid[['derece']].values, df_valid['mesafe_cm'].values
    try:
        ransac = RANSACRegressor(random_state=42);
        ransac.fit(X, y)
        slope = ransac.estimator_.coef_[0]
        inf = f"Yüzey dairesel/paralel (Eğim:{slope:.3f})" if abs(slope) < 0.1 else (
            f"Yüzey açı arttıkça uzaklaşıyor (Eğim:{slope:.3f})" if slope > 0 else f"Yüzey açı arttıkça yaklaşıyor (Eğim:{slope:.3f})")
        xr = np.array([df_valid['derece'].min(), df_valid['derece'].max()]).reshape(-1, 1)
        return {'x': xr.flatten(), 'y': ransac.predict(xr)}, "Polar Regresyon: " + inf
    except Exception as e:
        print(f"Polar regresyon hatası: {e}"); return None, "Polar regresyon hatası."


def analyze_environment_shape(fig, df_valid_input):
    df_valid = df_valid_input.copy()  # Fonksiyon içinde DataFrame'i değiştirmemek için kopya al
    if len(df_valid) < 10 or not all(col in df_valid.columns for col in ['y_cm', 'x_cm']):
        df_valid.loc[:, 'cluster'] = -2
        return "Analiz için yetersiz veri.", df_valid
    points_all = df_valid[['y_cm', 'x_cm']].to_numpy()
    try:
        db = DBSCAN(eps=15, min_samples=3).fit(points_all)
        df_valid.loc[:, 'cluster'] = db.labels_
    except Exception as e:
        print(f"DBSCAN hatası: {e}");
        df_valid.loc[:, 'cluster'] = -2
        return "DBSCAN kümeleme hatası.", df_valid
    unique_clusters = set(df_valid['cluster'].unique())
    num_actual_clusters = len(unique_clusters - {-1, -2})
    desc = f"{num_actual_clusters} potansiyel nesne kümesi bulundu." if num_actual_clusters > 0 else "Belirgin bir nesne kümesi bulunamadı (DBSCAN)."
    cmap_len = num_actual_clusters
    colors = plt.cm.get_cmap('viridis', cmap_len if cmap_len > 0 else 1)
    for k_label in unique_clusters:
        if k_label == -2: continue
        cluster_points_df = df_valid[df_valid['cluster'] == k_label]
        if cluster_points_df.empty: continue
        cluster_points_np = cluster_points_df[
            ['y_cm', 'x_cm']].to_numpy()  # DBSCAN için kullanılan sütunlarla aynı olmalı

        if k_label == -1:
            color_val, point_size, name_val = 'rgba(128,128,128,0.3)', 5, 'Gürültü/Diğer'
        else:
            norm_k = (k_label / (cmap_len - 1)) if cmap_len > 1 else 0.0
            raw_col = colors(np.clip(norm_k, 0.0, 1.0))
            color_val = f'rgba({raw_col[0] * 255:.0f},{raw_col[1] * 255:.0f},{raw_col[2] * 255:.0f},0.9)'
            point_size, name_val = 8, f'Küme {k_label}'
        fig.add_trace(go.Scatter(x=cluster_points_np[:, 0], y=cluster_points_np[:, 1], mode='markers',
                                 marker=dict(color=color_val, size=point_size), name=name_val,
                                 customdata=[k_label] * len(cluster_points_np)))
    return desc, df_valid


def estimate_geometric_shape(df_input):
    df = df_input.copy()
    if len(df) < 15 or not all(
        col in df.columns for col in ['x_cm', 'y_cm']): return "Şekil tahmini için yetersiz nokta."
    try:
        points = df[['x_cm', 'y_cm']].values
        hull = ConvexHull(points)
        hull_area = hull.area  # 2D için .area
        min_x_val, max_x_val = df['x_cm'].min(), df['x_cm'].max()
        min_y_val, max_y_val = df['y_cm'].min(), df['y_cm'].max()
        # Sensör 0,0'da ve X ekseni ileri doğru olduğu için derinlik max_x_val olabilir.
        # Genişlik ise Y eksenindeki yayılım.
        width = max_y_val - min_y_val
        depth = max_x_val  # Eğer sensör 0,0'dan başlıyorsa

        if width < 1 or depth < 1: return "Algılanan şekil çok küçük."
        # Bounding box alanı, sensörün 0,0'da olduğunu ve X'in ileri olduğunu varsayarak
        # Eğer min_x_val de 0'dan farklıysa, bbox_area = (max_x_val - min_x_val) * width olurdu.
        # Şimdilik basit bir varsayımla:
        bbox_area = depth * width
        fill_factor = hull_area / bbox_area if bbox_area > 0 else 0

        if depth > 150 and width < 50 and fill_factor < 0.3: return "Tahmin: Dar ve derin bir boşluk (Koridor)."
        if fill_factor > 0.7 and (
                0.8 < (width / depth if depth > 0 else 0) < 1.2): return "Tahmin: Dolgun, kutu/dairesel bir nesne."
        if fill_factor > 0.6 and width > depth * 2.5: return "Tahmin: Geniş bir yüzey (Duvar)."
        if fill_factor < 0.4: return "Tahmin: İçbükey bir yapı veya dağınık nesneler."
        return "Tahmin: Düzensiz veya karmaşık bir yapı."
    except Exception as e:
        print(f"Geometrik analiz hatası: {e}"); return "Geometrik analiz hatası."


def yorumla_tablo_verisi_gemini(df, model_name='gemini-2.5-flash-preview-05-20'):  # Güncel model adını kontrol edin
    if not GOOGLE_GENAI_AVAILABLE: return "Hata: Google GenerativeAI kütüphanesi yüklenemedi."
    if not google_api_key: return "Hata: `GOOGLE_API_KEY` ayarlanmamış."
    if df is None or df.empty: return "Yorumlanacak tablo verisi bulunamadı."
    try:
        client = genai.Client(api_key=google_api_key)
        prompt_text = (
            f"Aşağıdaki tablo, bir hc-sr04 sensörünün yaptığı taramadan elde edilen {df.to_string(index=False)} verileri yorumla"
            "Olası nesneleri (duvar, köşe, sandalye bacağı, kutu, insan.) tahmin etmeye çalış "
            "boş alanları ve genel yerleşim düzenini kısa ve anlaşılır bir dille yorumla. ortamın alanını tahin etmeye çalış."
            f"verilerden yola çıkarak polar grafiğe bağlı 3D GRAFİK OLUŞTUR"
            f"{df.to_string(index=False)} verileriyle ortamın görselini tahminde bulunan resim oluştur"
            "Yorumunu maddeleme veya kısa paragraflar halinde yap. \n\n"
        )
        response = client.models.generate_content(model=model_name, contents=prompt_text)
        return response.text
    except Exception as e:
        return f"Gemini'den yanıt alınırken bir hata oluştu: {e}"


# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# (Bu fonksiyonlarda bir değişiklik yok, önceki tam versiyondaki gibiler)
# ==============================================================================

@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('scan-duration-angle-input', 'value'), State('step-angle-input', 'value'),
     State('buzzer-distance-input', 'value'), State('invert-motor-checkbox', 'value'),
     State('steps-per-rev-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks, duration, step, buzzer_dist, invert, steps_rev):
    # ... (önceki tam koddaki gibi) ...
    if n_clicks == 0: return no_update
    if not (isinstance(duration, (int, float)) and 10 <= duration <= 720): return dbc.Alert(
        "Tarama Açısı 10-720 derece arasında olmalı!", color="danger", duration=4000)
    if not (isinstance(step, (int, float)) and 0.1 <= abs(step) <= 45): return dbc.Alert(
        "Adım açısı 0.1-45 arasında olmalı!", color="danger", duration=4000)
    if not (isinstance(buzzer_dist, (int, float)) and 0 <= buzzer_dist <= 200): return dbc.Alert(
        "Uyarı mesafesi 0-200 cm arasında olmalı!", color="danger", duration=4000)
    if not (isinstance(steps_rev, (int, float)) and 500 <= steps_rev <= 10000): return dbc.Alert(
        "Motor Adım/Tur 500-10000 arasında olmalı!", color="danger", duration=4000)

    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid = int(pf.read().strip())
            if is_process_running(pid): return dbc.Alert(f"Sensör betiği zaten çalışıyor (PID:{pid}). Önce durdurun.",
                                                         color="warning")
        except:
            pass
    for fp_lock_pid in [SENSOR_SCRIPT_LOCK_FILE, SENSOR_SCRIPT_PID_FILE]:
        if os.path.exists(fp_lock_pid):
            try:
                os.remove(fp_lock_pid)
            except OSError as e_rm:
                print(f"Eski dosya silinemedi ({fp_lock_pid}): {e_rm}")
    try:
        py_exec = sys.executable
        cmd = [py_exec, SENSOR_SCRIPT_PATH, "--scan_duration_angle", str(duration), "--step_angle", str(step),
               "--buzzer_distance", str(buzzer_dist), "--invert_motor_direction", str(invert), "--steps_per_rev",
               str(steps_rev)]
        subprocess.Popen(cmd, start_new_session=True)  # Loglamayı daha sonra ekleyebilirsiniz
        max_wait_time = 7;
        check_interval = 0.25;
        start_time_wait = time.time();
        pid_file_found = False
        while time.time() - start_time_wait < max_wait_time:
            if os.path.exists(SENSOR_SCRIPT_PID_FILE): pid_file_found = True; break
            time.sleep(check_interval)
        if pid_file_found:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                new_pid = pf.read().strip()
            return dbc.Alert(f"Tarama başlatıldı (PID:{new_pid}).", color="success")
        else:
            return dbc.Alert(f"Tarama başlatılamadı. PID dosyası {max_wait_time} saniye içinde oluşmadı.",
                             color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatma hatası: {e}", color="danger")


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks):
    # ... (önceki tam koddaki gibi) ...
    if n_clicks == 0: return no_update
    pid_to_kill = None;
    message = "";
    color = "warning"
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_to_kill = int(pf.read().strip())
        except (IOError, ValueError):
            pass
    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            os.kill(pid_to_kill, signal.SIGTERM);
            time.sleep(1)
            if is_process_running(pid_to_kill): os.kill(pid_to_kill, signal.SIGKILL); time.sleep(0.5)
            if not is_process_running(pid_to_kill):
                message = f"Sensör betiği (PID:{pid_to_kill}) durduruldu."; color = "info"
            else:
                message = f"Sensör betiği (PID:{pid_to_kill}) durdurulamadı!"; color = "danger"
        except ProcessLookupError:
            message = f"Sensör betiği (PID:{pid_to_kill}) zaten çalışmıyordu."; color = "warning"
        except Exception as e:
            message = f"Sensör betiği durdurma hatası: {e}"; color = "danger"
    else:
        message = "Çalışan sensör betiği bulunamadı."
    for fp_lock_pid_stop in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
        if os.path.exists(fp_lock_pid_stop):
            try:
                os.remove(fp_lock_pid_stop)
            except OSError:
                pass
    return dbc.Alert(message, color=color)


@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'), Output('cpu-usage', 'value'),
     Output('cpu-usage', 'label'), Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n):
    # ... (önceki tam koddaki gibi) ...
    status_text, status_class, pid_val = "Beklemede", "text-secondary", None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_val = int(pf.read().strip())
        except:
            pass
    if pid_val and is_process_running(pid_val):
        status_text, status_class = f"Çalışıyor (PID:{pid_val})", "text-success"
    else:
        status_text, status_class = "Çalışmıyor", "text-danger"
    cpu = psutil.cpu_percent(interval=0.1);
    ram = psutil.virtual_memory().percent
    return status_text, status_class, cpu, f"{cpu:.1f}%", ram, f"{ram:.1f}%"


@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children'),
     Output('current-distance-col', 'style'), Output('max-detected-distance', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n):
    # ... (önceki tam koddaki gibi) ...
    scan = get_latest_scan()
    angle_s, dist_s, speed_s, max_dist_s = "--°", "-- cm", "-- cm/s", "-- cm"
    dist_style = {'padding': '10px', 'transition': 'background-color 0.5s ease', 'borderRadius': '5px'}
    if scan:
        point = scan.points.order_by('-timestamp').first()
        if point:
            angle_s = f"{point.derece:.1f}°" if pd.notnull(point.derece) else "--°"
            dist_s = f"{point.mesafe_cm:.1f} cm" if pd.notnull(point.mesafe_cm) else "-- cm"
            speed_s = f"{point.hiz_cm_s:.1f} cm/s" if pd.notnull(point.hiz_cm_s) else "-- cm/s"
            buzzer_threshold = scan.buzzer_distance_setting
            if buzzer_threshold is not None and pd.notnull(
                point.mesafe_cm) and 0 < point.mesafe_cm <= buzzer_threshold: dist_style.update(
                {'backgroundColor': '#d9534f', 'color': 'white'})
        max_dist_agg = scan.points.filter(mesafe_cm__lt=2500, mesafe_cm__gt=0).aggregate(max_dist_val=Max('mesafe_cm'))
        if max_dist_agg and max_dist_agg.get(
            'max_dist_val') is not None: max_dist_s = f"{max_dist_agg['max_dist_val']:.1f} cm"
    return angle_s, dist_s, speed_s, dist_style, max_dist_s


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'),
     Output('max-depth', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n):
    # ... (önceki tam koddaki gibi) ...
    scan = get_latest_scan()
    area_s, perim_s, width_s, depth_s = "-- cm²", "-- cm", "-- cm", "-- cm"
    if scan:
        area_s = f"{scan.calculated_area_cm2:.2f} cm²" if pd.notnull(scan.calculated_area_cm2) else "N/A"
        perim_s = f"{scan.perimeter_cm:.2f} cm" if pd.notnull(scan.perimeter_cm) else "N/A"
        width_s = f"{scan.max_width_cm:.2f} cm" if pd.notnull(scan.max_width_cm) else "N/A"
        depth_s = f"{scan.max_depth_cm:.2f} cm" if pd.notnull(scan.max_depth_cm) else "N/A"
    return area_s, perim_s, width_s, depth_s


@app.callback(Output('download-csv', 'data'), Input('export-csv-button', 'n_clicks'), prevent_initial_call=True)
def export_csv_callback(n_clicks_csv):
    # ... (önceki tam koddaki gibi) ...
    if not n_clicks_csv: return no_update
    scan = get_latest_scan()
    if not scan: return dcc.send_data_frame(pd.DataFrame().to_csv, "tarama_yok.csv", index=False)
    points_qs = scan.points.all().values()
    if not points_qs: return dcc.send_data_frame(pd.DataFrame().to_csv, f"tarama_id_{scan.id}_nokta_yok.csv",
                                                 index=False)
    df = pd.DataFrame(list(points_qs));
    return dcc.send_data_frame(df.to_csv, f"tarama_id_{scan.id}_noktalar.csv", index=False)


@app.callback(Output('download-excel', 'data'), Input('export-excel-button', 'n_clicks'), prevent_initial_call=True)
def export_excel_callback(n_clicks_excel):
    # ... (önceki tam koddaki gibi) ...
    if not n_clicks_excel: return no_update
    scan = get_latest_scan()
    if not scan: return dcc.send_bytes(b"", "tarama_yok.xlsx")
    try:
        scan_info_data = Scan.objects.filter(id=scan.id).values().first();
        scan_info_df = pd.DataFrame([scan_info_data]) if scan_info_data else pd.DataFrame()
        points_df = pd.DataFrame(list(scan.points.all().values()))
    except Exception as e_excel_data:
        print(f"Excel için veri çekme hatası: {e_excel_data}"); return dcc.send_bytes(b"",
                                                                                      f"veri_cekme_hatasi_{scan.id if scan else 'yok'}.xlsx")
    with io.BytesIO() as buf:
        try:
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                if not scan_info_df.empty: scan_info_df.to_excel(writer, sheet_name=f'Scan_{scan.id}_Info', index=False)
                if not points_df.empty:
                    points_df.to_excel(writer, sheet_name=f'Scan_{scan.id}_Points', index=False)
                elif scan_info_df.empty:
                    pd.DataFrame().to_excel(writer, sheet_name='Veri Yok', index=False)
        except Exception as e_excel_write:
            print(f"Excel yazma hatası: {e_excel_write}"); pd.DataFrame([{"Hata": str(e_excel_write)}]).to_excel(writer,
                                                                                                                 sheet_name='Hata',
                                                                                                                 index=False)
        return dcc.send_bytes(buf.getvalue(), f"tarama_detaylari_id_{scan.id if scan else 'yok'}.xlsx")


@app.callback(Output('tab-content-datatable', 'children'),
              [Input('visualization-tabs-main', 'active_tab'), Input('interval-component-main', 'n_intervals')])
def render_and_update_data_table(active_tab, n):
    # ... (önceki tam koddaki gibi) ...
    if active_tab != "tab-datatable": return None
    scan = get_latest_scan()
    if not scan: return html.P("Görüntülenecek tarama verisi yok.")
    points_qs = scan.points.order_by('-id').values('id', 'derece', 'mesafe_cm', 'hiz_cm_s', 'x_cm', 'y_cm', 'timestamp')
    if not points_qs: return html.P(f"Tarama ID {scan.id} için nokta verisi bulunamadı.")
    df = pd.DataFrame(list(points_qs))
    if 'timestamp' in df.columns: df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime(
        '%Y-%m-%d %H:%M:%S.%f').str[:-3]
    return dash_table.DataTable(data=df.to_dict('records'),
                                columns=[{"name": i.replace("_", " ").title(), "id": i} for i in df.columns],
                                style_cell={'textAlign': 'left', 'padding': '5px', 'fontSize': '0.9em'},
                                style_header={'backgroundColor': 'rgb(230,230,230)', 'fontWeight': 'bold'},
                                style_table={'minHeight': '65vh', 'height': '70vh', 'maxHeight': '75vh',
                                             'overflowY': 'auto', 'overflowX': 'auto'}, page_size=50,
                                sort_action="native", filter_action="native", virtualization=True,
                                fixed_rows={'headers': True}, style_data_conditional=[
            {'if': {'row_index': 'odd'}, 'backgroundColor': 'rgb(248, 248, 248)'}])


@app.callback(
    [Output('scan-map-graph', 'figure'), Output('polar-regression-graph', 'figure'), Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure'), Output('environment-estimation-text', 'children'),
     Output('clustered-data-store', 'data')], [Input('interval-component-main', 'n_intervals')])
def update_all_graphs(n):
    # ... (önceki tam koddaki gibi, df_val.copy() ve diğer düzeltmeler dahil) ...
    figs = [go.Figure() for _ in range(4)];
    est_cart, est_polar, clear_path, shape_estimation = "Veri bekleniyor...", "Veri bekleniyor...", "", "Veri bekleniyor...";
    store_data = None;
    scan_id_for_revision = 'initial_load'
    scan = get_latest_scan()
    if not scan:
        est_cart = "Tarama başlatın."
    else:
        scan_id_for_revision = str(scan.id)
        points_qs = scan.points.all().values('x_cm', 'y_cm', 'derece', 'mesafe_cm', 'timestamp')
        if not points_qs:
            est_cart = f"Tarama ID {scan.id} için nokta yok."
        else:
            df_pts = pd.DataFrame(list(points_qs))
            df_val = df_pts[(df_pts['mesafe_cm'] > 0.1) & (df_pts['mesafe_cm'] < 250.0)].copy()
            if len(df_val) >= 2:
                add_scan_rays(figs[0], df_val);
                add_sector_area(figs[0], df_val)
                est_cart, df_clus = analyze_environment_shape(figs[0], df_val.copy());
                store_data = df_clus.to_json(orient='split')
                line_data, est_polar = analyze_polar_regression(df_val)
                figs[1].add_trace(
                    go.Scatter(x=df_val['derece'], y=df_val['mesafe_cm'], mode='markers', name='Noktalar'))
                if line_data: figs[1].add_trace(
                    go.Scatter(x=line_data['x'], y=line_data['y'], mode='lines', name='Regresyon',
                               line=dict(color='red', width=3)))
                clear_path = find_clearest_path(df_val);
                shape_estimation = estimate_geometric_shape(df_val.copy())
                update_polar_graph(figs[2], df_val);
                update_time_series_graph(figs[3], df_val.copy())
            else:
                est_cart = "Analiz için yetersiz geçerli nokta."
    for fig in figs: add_sensor_position(fig)
    titles = ['Ortamın 2D Haritası', 'Açıya Göre Mesafe Regresyonu', 'Polar Grafik', 'Zaman Serisi - Mesafe'];
    common_legend = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    for i, fig in enumerate(figs):
        fig.update_layout(title_text=titles[i], uirevision=scan_id_for_revision, legend=common_legend,
                          margin=dict(l=40, r=40, t=60, b=40))
        if i == 0:
            fig.update_layout(xaxis_title="Yatay Mesafe (cm)", yaxis_title="Dikey Mesafe (cm)", yaxis_scaleanchor="x",
                              yaxis_scaleratio=1)
        elif i == 1:
            fig.update_layout(xaxis_title="Tarama Açısı (°)", yaxis_title="Mesafe (cm)")
    final_est_text = html.Div([html.H6("Geometrik Tahmin:", className="mt-2"), html.P(shape_estimation),
                               html.H6("En Açık Yol:", className="mt-2"), html.P(clear_path, className="text-primary"),
                               html.H6("Kümeleme Analizi:", className="mt-2"), html.P(est_cart),
                               html.H6("Polar Regresyon:", className="mt-2"), html.P(est_polar)],
                              style={'fontSize': '0.9em'})
    return figs[0], figs[1], figs[2], figs[3], final_est_text, store_data


@app.callback(
    [Output("cluster-info-modal", "is_open"), Output("modal-title", "children"), Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")], [State("clustered-data-store", "data")], prevent_initial_call=True)
def display_cluster_info(clickData, stored_data_json):
    # ... (önceki tam koddaki gibi) ...
    if not clickData or not stored_data_json: return False, no_update, no_update
    try:
        df_clus = pd.read_json(stored_data_json, orient='split')
        if 'cluster' not in df_clus.columns: return False, "Hata", "Küme verisi bulunamadı."
        cl_label = clickData["points"][0].get('customdata')
        if cl_label is None:  # customdata yoksa, index bazlı dene (daha az güvenilir)
            point_idx = clickData["points"][0].get('pointIndex')
            if point_idx is not None and point_idx < len(df_clus):
                cl_label = df_clus.iloc[point_idx]['cluster']
            else:
                return False, "Hata", "Küme etiketi alınamadı."
        if cl_label == -2:
            title, body = "Analiz Yok", "Bu nokta için küme analizi yapılamadı."
        elif cl_label == -1:
            title, body = "Gürültü Noktası", "Bu nokta bir nesne kümesine ait değil."
        else:
            cl_df_points = df_clus[df_clus['cluster'] == cl_label];
            n_pts = len(cl_df_points);
            w_cl, d_cl = 0, 0
            if n_pts > 0: w_cl = cl_df_points['y_cm'].max() - cl_df_points['y_cm'].min(); d_cl = cl_df_points[
                                                                                                     'x_cm'].max() - \
                                                                                                 cl_df_points[
                                                                                                     'x_cm'].min()
            title = f"Küme #{int(cl_label)} Detayları";
            body = html.Div([html.P(f"Nokta Sayısı: {n_pts}"), html.P(f"Yaklaşık Genişlik (Y): {w_cl:.1f} cm"),
                             html.P(f"Yaklaşık Derinlik (X): {d_cl:.1f} cm")])
        return True, title, body
    except Exception as e:
        return True, "Hata", f"Küme bilgisi gösterilemedi: {e}"


@app.callback(Output('ai-yorum-sonucu', 'children'), [Input('ai-model-dropdown', 'value')], prevent_initial_call=True)
def yorumla_model_secimi(selected_model_value):
    # ... (önceki tam koddaki gibi) ...
    if not selected_model_value: return html.Div("Yorum için bir model seçin.", className="text-center")
    scan = get_latest_scan()
    if not scan: return dbc.Alert("Analiz edilecek bir tarama bulunamadı.", color="warning", duration=4000)
    if scan.ai_commentary: print(f"Scan ID {scan.id} için mevcut AI yorumu kullanılıyor."); return dbc.Alert(
        dcc.Markdown(scan.ai_commentary, dangerously_allow_html=True, link_target="_blank"), color="info")
    points_qs = scan.points.all().values('derece', 'mesafe_cm')
    if not points_qs: return dbc.Alert("Yorumlanacak tarama verisi bulunamadı.", color="warning", duration=4000)
    df_data_for_ai = pd.DataFrame(list(points_qs))
    if len(df_data_for_ai) > 500: print(
        f"AI yorumu için çok fazla nokta ({len(df_data_for_ai)}), 500'e örnekleniyor..."); df_data_for_ai = df_data_for_ai.sample(
        n=500, random_state=1)
    print(f"Scan ID {scan.id} için yeni AI yorumu üretiliyor (Model: {selected_model_value})...");
    yorum_text_from_ai = yorumla_tablo_verisi_gemini(df_data_for_ai, selected_model_value)
    if "Hata:" in yorum_text_from_ai or "hata oluştu" in yorum_text_from_ai: return dbc.Alert(yorum_text_from_ai,
                                                                                              color="danger")
    try:
        scan.ai_commentary = yorum_text_from_ai;
        scan.save()
        print(f"Scan ID {scan.id} için yeni AI yorumu veritabanına kaydedildi.")
    except Exception as e_db_save:
        return dbc.Alert([html.H6("Yorum kaydedilemedi:"), html.P(f"{e_db_save}"), html.Hr(),
                          dcc.Markdown(yorum_text_from_ai, dangerously_allow_html=True, link_target="_blank")],
                         color="warning")
    return dbc.Alert(dcc.Markdown(yorum_text_from_ai, dangerously_allow_html=True, link_target="_blank"),
                     color="success")