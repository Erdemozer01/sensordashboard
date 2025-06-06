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
import base64

# Gerekli AI kütüphanelerini import ediyoruz
try:
    from google.generativeai.types import GenerationConfig
    import google.generativeai as genai
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    print("UYARI: 'google.generativeai' kütüphanesi bulunamadı. AI yorumlama özelliği çalışmayacak.")
    GOOGLE_GENAI_AVAILABLE = False
    GenerationConfig = None
    genai = None

# Dash ve Plotly Kütüphaneleri
from django_plotly_dash import DjangoDash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt

from dotenv import load_dotenv

load_dotenv()
google_api_key = os.getenv("GOOGLE_API_KEY")

# ==============================================================================
# --- SABİTLER VE UYGULAMA BAŞLATMA ---
# ==============================================================================
app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# ==============================================================================
# --- ARAYÜZ BİLEŞENLERİNİN TANIMLANMASI ---
# ==============================================================================
navbar = dbc.NavbarSimple(
    children=[dbc.NavItem(dbc.NavLink("Admin Paneli", href="/admin/", external_link=True, target="_blank"))],
    brand="Dream Pi", brand_href="/", color="primary", dark=True, sticky="top", fluid=True, className="mb-4"
)

control_panel = dbc.Card([
    dbc.CardHeader("Kontrol ve Ayarlar", className="bg-primary text-white"),
    dbc.CardBody([
        html.H6("Çalışma Modu:", className="mt-1"),
        dbc.RadioItems(
            id='mode-selection-radios',
            options=[
                {'label': 'Mesafe Ölçümü ve Haritalama', 'value': 'scan_and_map'},
                {'label': 'Serbest Hareket (Gözcü)', 'value': 'free_movement'},
            ],
            value='scan_and_map', inline=False, className="mb-3",
        ),
        html.Hr(),
        dbc.Row([
            dbc.Col(html.Button('Başlat', id='start-scan-button', n_clicks=0, className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Durdur', id='stop-scan-button', n_clicks=0, className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'}, className="mb-3"),
        html.Hr(),
        html.Div(id='scan-parameters-wrapper', children=[
            html.H6("Yapay Zeka Seçimi:", className="mt-3"),
            dcc.Dropdown(
                id='ai-model-dropdown',
                options=[
                    {'label': 'Gemini 1.5 Flash (Hızlı ve Multimodal)', 'value': 'gemini-1.5-flash-latest'},
                    {'label': 'Gemini 1.5 Pro (Gelişmiş)', 'value': 'gemini-1.5-pro-latest'},
                ],
                placeholder="Yorum ve Görüntü için model seçin...", clearable=True, className="mb-3"
            ),
            html.Hr(),
            html.H6("Tarama Parametreleri:", className="mt-2"),
            dbc.InputGroup([dbc.InputGroupText("Tarama Açısı (°)", style={"width": "150px"}), dbc.Input(id="scan-duration-angle-input", type="number", value=270.0, min=10, max=720, step=1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "150px"}), dbc.Input(id="step-angle-input", type="number", value=10.0, min=0.1, max=45, step=0.1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Uyarı Mes. (cm)", style={"width": "150px"}), dbc.Input(id="buzzer-distance-input", type="number", value=10, min=0, max=200, step=1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Motor Adım/Tur", style={"width": "150px"}), dbc.Input(id="steps-per-rev-input", type="number", value=4096, min=500, max=10000, step=1)], className="mb-2"),
            dbc.Checkbox(id="invert-motor-checkbox", label="Motor Yönünü Ters Çevir", value=False, className="mt-2 mb-2"),
        ])
    ])
])

stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"), dbc.CardBody(dbc.Row([
    dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=3, className="text-center border-end"),
    dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]), id='current-distance-col', width=3, className="text-center rounded border-end"),
    dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=3, className="text-center border-end"),
    dbc.Col(html.Div([html.H6("Max. Algılanan Mesafe:"), html.H4(id='max-detected-distance', children="-- cm")]), width=3, className="text-center")
]))], className="mb-3")

system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"), dbc.CardBody([
    dbc.Row([dbc.Col(html.Div([html.H6("Sensör Betiği Durumu:"), html.H5(id='script-status', children="Beklemede")]))], className="mb-2"),
    dbc.Row([
        dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"), dbc.Progress(id='cpu-usage', value=0, color="success", style={"height": "20px"}, className="mb-1", label="0%")])),
        dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"), dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"}, className="mb-1", label="0%")]))
    ])
])], className="mb-3")

export_card = dbc.Card([dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"), dbc.CardBody([
    dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
    dcc.Download(id='download-csv'),
    dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
    dcc.Download(id='download-excel')
])], className="mb-3")

analysis_card = dbc.Card([
    dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(id='calculated-area', children="-- cm²")]),
            dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length', children="-- cm")])
        ]),
        dbc.Row([
            dbc.Col([html.H6("Max Genişlik:"), html.H4(id='max-width', children="-- cm")]),
            dbc.Col([html.H6("Max Derinlik:"), html.H4(id='max-depth', children="-- cm")])
        ], className="mt-2")
    ])
])

estimation_card = dbc.Card([
    dbc.CardHeader("Akıllı Ortam Analizi", className="bg-success text-white"),
    dbc.CardBody(html.Div("Tahmin: Bekleniyor...", id='environment-estimation-text', className="text-center"))
])

ai_card = dbc.Card([
    dbc.CardHeader("Akıllı Yorumlama ve Görselleştirme (Yapay Zeka)", className="bg-info text-white"),
    dbc.CardBody(dcc.Loading(id="loading-ai-comment", type="default", children=[
        html.Div(id='ai-yorum-sonucu', children=[html.P("Yorum ve görüntü almak için yukarıdan bir yapay zeka modeli seçin.")], className="text-center mt-2"),
        html.Div(id='ai-image', children=[], className="text-center mt-2")
    ]))
])

visualization_tabs = dbc.Tabs([
    dbc.Tab([
        dbc.Row([
            dbc.Col(dcc.Dropdown(
                id='graph-selector-dropdown',
                options=[
                    {'label': '2D Kartezyen Harita', 'value': 'map'},
                    {'label': 'Regresyon Analizi', 'value': 'regression'},
                    {'label': 'Polar Grafik', 'value': 'polar'},
                    {'label': 'Zaman Serisi (Mesafe)', 'value': 'time'},
                ],
                value='map', clearable=False, style={'marginTop': '10px'}
            ), width=6)
        ], justify="center", className="mb-3"),
        html.Div([
            html.Div(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), id='container-map-graph', style={'display': 'block'}),
            html.Div(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), id='container-regression-graph', style={'display': 'none'}),
            html.Div(dcc.Graph(id='polar-graph', style={'height': '75vh'}), id='container-polar-graph', style={'display': 'none'}),
            html.Div(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), id='container-time-series-graph', style={'display': 'none'}),
        ])
    ], label="Grafikler", tab_id="tab-graphics"),
    dbc.Tab(
        dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]),
        label="Veri Tablosu", tab_id="tab-datatable"
    )
], id="visualization-tabs-main", active_tab="tab-graphics")

app.layout = html.Div(style={'padding': '20px'}, children=[
    navbar,
    dbc.Row([
        dbc.Col([control_panel, html.Br(), stats_panel, html.Br(), system_card, html.Br(), export_card], md=4, className="mb-3"),
        dbc.Col([
            visualization_tabs,
            dbc.Row([
                dbc.Col(analysis_card, md=7, className="mt-3"),
                dbc.Col(estimation_card, md=5, className="mt-3"),
            ], className="g-2"),
            dbc.Row([dbc.Col(ai_card, width=12, className="mt-3")])
        ], md=8)
    ]),
    dcc.Store(id='clustered-data-store'),
    dbc.Modal([dbc.ModalHeader(dbc.ModalTitle(id="modal-title")), dbc.ModalBody(id="modal-body")], id="cluster-info-modal", is_open=False, centered=True),
    dcc.Interval(id='interval-component-main', interval=2500, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])

# ==============================================================================
# --- YARDIMCI FONKSİYONLAR (EKSİKSİZ) ---
# ==============================================================================
def is_process_running(pid):
    if pid is None: return False
    try: return psutil.pid_exists(pid)
    except Exception: return False

def get_latest_scan():
    from scanner.models import Scan
    try:
        running_scan = Scan.objects.filter(status='RUNNING').order_by('-start_time').first()
        if running_scan: return running_scan
        return Scan.objects.order_by('-start_time').first()
    except Exception: return None

def add_scan_rays(fig, df):
    if df.empty or not all(c in df.columns for c in ['x_cm', 'y_cm']): return
    x, y = [], []
    for _, r in df.iterrows(): x.extend([0, r['y_cm'], None]); y.extend([0, r['x_cm'], None])
    fig.add_trace(go.Scatter(x=x, y=y, mode='lines', line=dict(color='rgba(255,100,100,0.4)', dash='dash', width=1), showlegend=False))

def add_sector_area(fig, df):
    if df.empty or not all(c in df.columns for c in ['x_cm', 'y_cm']): return
    fig.add_trace(go.Scatter(x=[0, *df['y_cm'], 0], y=[0, *df['x_cm'], 0], mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.15)', line=dict(color='rgba(255,0,0,0.4)'), name='Taranan Sektör'))

def add_sensor_position(fig):
    fig.add_trace(go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))

def update_polar_graph(fig, df):
    if df.empty or not all(c in df.columns for c in ['mesafe_cm', 'derece']): return
    fig.add_trace(go.Scatterpolar(r=df['mesafe_cm'], theta=df['derece'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 400]), angularaxis=dict(direction="clockwise", period=360, thetaunit="degrees")))

def update_time_series_graph(fig, df):
    if df.empty or 'timestamp' not in df.columns or 'mesafe_cm' not in df.columns: return
    try:
        df_s = df.copy(); df_s['timestamp'] = pd.to_datetime(df_s['timestamp'], errors='coerce')
        df_s.dropna(subset=['timestamp'], inplace=True)
        if len(df_s) < 2: return
        df_s = df_s.sort_values(by='timestamp')
        min_time, max_time = df_s['timestamp'].min(), df_s['timestamp'].max()
        padding = pd.Timedelta(seconds=max((max_time - min_time).total_seconds() * 0.05, 2.0))
        x_range = [min_time - padding, max_time + padding]
        fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['mesafe_cm'], mode='lines+markers', name='Mesafe'))
        fig.update_layout(xaxis_type='date', xaxis_range=x_range, xaxis_title="Zaman", yaxis_title="Mesafe (cm)")
    except Exception as e: logging.error(f"Zaman serisi hatası: {e}")

def find_clearest_path(df_valid):
    if df_valid.empty or not all(c in df_valid.columns for c in ['mesafe_cm', 'derece']): return "Veri yok."
    try:
        df_filtered = df_valid[df_valid['mesafe_cm'] > 0]
        if df_filtered.empty: return "Pozitif mesafe yok."
        cp = df_filtered.loc[df_filtered['mesafe_cm'].idxmax()]
        return f"En Açık Yol: {cp['derece']:.1f}° yönünde, {cp['mesafe_cm']:.1f} cm."
    except Exception as e: return f"Hesaplama hatası: {e}"

def analyze_polar_regression(df_valid):
    if len(df_valid) < 5 or not all(c in df_valid.columns for c in ['mesafe_cm', 'derece']): return None, "Yetersiz veri."
    X, y = df_valid[['derece']].values, df_valid['mesafe_cm'].values
    try:
        ransac = RANSACRegressor(random_state=42); ransac.fit(X, y)
        slope = ransac.estimator_.coef_[0]
        inf = f"Yüzey dairesel/paralel (Eğim:{slope:.3f})" if abs(slope) < 0.1 else (f"Yüzey açı arttıkça uzaklaşıyor (Eğim:{slope:.3f})" if slope > 0 else f"Yüzey açı arttıkça yaklaşıyor (Eğim:{slope:.3f})")
        xr = np.array([df_valid['derece'].min(), df_valid['derece'].max()]).reshape(-1, 1)
        return {'x': xr.flatten(), 'y': ransac.predict(xr)}, "Polar Regresyon: " + inf
    except Exception as e: return None, f"Regresyon hatası: {e}"

def analyze_environment_shape(fig, df_valid_input):
    df_valid = df_valid_input.copy()
    if len(df_valid) < 10 or not all(c in df_valid.columns for c in ['y_cm', 'x_cm']):
        df_valid.loc[:, 'cluster'] = -2; return "Analiz için yetersiz veri.", df_valid
    points_all = df_valid[['y_cm', 'x_cm']].to_numpy()
    try:
        db = DBSCAN(eps=15, min_samples=3).fit(points_all)
        df_valid.loc[:, 'cluster'] = db.labels_
    except Exception as e:
        df_valid.loc[:, 'cluster'] = -2; return f"DBSCAN hatası: {e}", df_valid
    unique_clusters = set(db.labels_); num_actual_clusters = len(unique_clusters - {-1})
    desc = f"{num_actual_clusters} potansiyel nesne kümesi bulundu." if num_actual_clusters > 0 else "Belirgin bir nesne kümesi bulunamadı."
    colors = plt.cm.get_cmap('viridis', len(unique_clusters))
    for k in unique_clusters:
        cluster_points_df = df_valid[df_valid['cluster'] == k]
        if k == -1: name_val, color_val, point_size = 'Gürültü', 'rgba(128,128,128,0.3)', 5
        else: name_val, color_val, point_size = f'Küme {k}', f'rgba({",".join(map(lambda x: f"{x*255:.0f}", colors(k)[:3]))},0.9)', 8
        fig.add_trace(go.Scatter(x=cluster_points_df['y_cm'], y=cluster_points_df['x_cm'], mode='markers', marker=dict(color=color_val, size=point_size), name=name_val, customdata=[k] * len(cluster_points_df)))
    return desc, df_valid

def estimate_geometric_shape(df_input):
    df = df_input.copy()
    if len(df) < 15 or not all(c in df.columns for c in ['x_cm', 'y_cm']): return "Şekil tahmini için yetersiz nokta."
    try:
        points = df[['x_cm', 'y_cm']].values; hull = ConvexHull(points)
        hull_area = hull.area; width = df['y_cm'].max() - df['y_cm'].min(); depth = df['x_cm'].max()
        if width < 1 or depth < 1: return "Algılanan şekil çok küçük."
        bbox_area = depth * width; fill_factor = hull_area / bbox_area if bbox_area > 0 else 0
        if depth > 150 and width < 50 and fill_factor < 0.3: return "Tahmin: Dar ve derin bir boşluk (Koridor)."
        if fill_factor > 0.7 and (0.8 < (width / depth if depth > 0 else 0) < 1.2): return "Tahmin: Dolgun, kutu/dairesel bir nesne."
        if fill_factor > 0.6 and width > depth * 2.5: return "Tahmin: Geniş bir yüzey (Duvar)."
        if fill_factor < 0.4: return "Tahmin: İçbükey bir yapı veya dağınık nesneler."
        return "Tahmin: Düzensiz veya karmaşık bir yapı."
    except Exception as e: return f"Geometrik analiz hatası: {e}"

def yorumla_tablo_verisi_gemini(df, model_name):
    if not GOOGLE_GENAI_AVAILABLE: return "Hata: Google GenerativeAI kütüphanesi yüklenemedi."
    if not google_api_key: return "Hata: `GOOGLE_API_KEY` ayarlanmamış."
    if df is None or df.empty: return "Yorumlanacak tablo verisi bulunamadı."
    try:
        genai.configure(api_key=google_api_key)
        model = genai.GenerativeModel(model_name=model_name)
        prompt_text = (f"Bir ultrasonik sensörün tarama verileri: {df.to_string(index=False)}\n\n"
                       "Bu verilere dayanarak, ortamın yapısını (oda, koridor, köşe vb.) ve potansiyel nesneleri (duvar, kutu vb.) bir paragrafla analiz et.")
        response = model.generate_content(prompt_text)
        return response.text
    except Exception as e: return f"Gemini metin hatası: {e}"

def image_generate(prompt_text):
    if not GOOGLE_GENAI_AVAILABLE: return ["Hata: Google GenerativeAI kütüphanesi yüklenemedi."]
    if not google_api_key: return ["Hata: `GOOGLE_API_KEY` ayarlanmamış."]
    if not prompt_text: return ["Görüntü oluşturmak için bir metin istemi (prompt) gerekli."]
    try:
        genai.configure(api_key=google_api_key)
        model = genai.GenerativeModel(model_name="models/gemini-2.0-flash-preview-image-generation")
        short_prompt = prompt_text.split('.')[0]
        full_prompt = f"Bir odaya yerleştirilmiş nesnelerin fotorealistik, yukarıdan aşağıya (top-down view) radar tarama haritası: {short_prompt}"
        config = GenerationConfig(response_modalities=['IMAGE', 'TEXT'])
        response = model.generate_content(full_prompt, generation_config=config)
        image_urls = []
        for part in response.candidates[0].content.parts:
            image_data_part = None
            if hasattr(part, 'mime_type') and part.mime_type.startswith("image/"):
                image_data_part = part
            elif hasattr(part, 'inline_data') and hasattr(part.inline_data, 'mime_type') and part.inline_data.mime_type.startswith("image/"):
                image_data_part = part.inline_data
            if image_data_part:
                image_bytes = image_data_part.data
                encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                data_uri = f"data:{image_data_part.mime_type};base64,{encoded_image}"
                image_urls.append(data_uri)
        if not image_urls:
            print("GÖRÜNTÜ YANITI BEKLENENDEN FARKLI:", response)
            return ["Model bir görüntü döndürmedi."]
        return image_urls
    except Exception as e:
        print(f"Görüntü oluşturma hatası: {e}")
        return [f"Görüntü oluşturulurken hata oluştu: {e}"]

# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================
@app.callback(Output('scan-parameters-wrapper', 'style'), Input('mode-selection-radios', 'value'))
def toggle_parameter_visibility(selected_mode):
    return {'display': 'block'} if selected_mode == 'scan_and_map' else {'display': 'none'}

@app.callback(
    Output('scan-status-message', 'children'),
    Input('start-scan-button', 'n_clicks'),
    [State('mode-selection-radios', 'value'), State('scan-duration-angle-input', 'value'), State('step-angle-input', 'value'),
     State('buzzer-distance-input', 'value'), State('invert-motor-checkbox', 'value'), State('steps-per-rev-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks, mode, duration, step, buzzer, invert, steps):
    # Bu fonksiyonun içeriği uzun ve değişmediği için kısaltıldı.
    # Kendi kodunuzdaki çalışan versiyonu burada olmalı.
    return dbc.Alert("Başlatma/Durdurma fonksiyonu burada yer alacak.", color="info")


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    Input('stop-scan-button', 'n_clicks'),
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks):
    # Bu fonksiyonun içeriği uzun ve değişmediği için kısaltıldı.
    return dbc.Alert("Durdurma fonksiyonu burada yer alacak.", color="info")

@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'), Output('cpu-usage', 'value'),
     Output('cpu-usage', 'label'), Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    Input('interval-component-system', 'n_intervals')
)
def update_system_card(n):
    # Bu fonksiyonun içeriği uzun ve değişmediği için kısaltıldı.
    return "Beklemede", "text-secondary", 0, "0%", 0, "0%"


@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children'),
     Output('current-distance-col', 'style'), Output('max-detected-distance', 'children')],
    Input('interval-component-main', 'n_intervals')
)
def update_realtime_values(n):
    from django.db.models import Max
    scan = get_latest_scan()
    # ... (Fonksiyonun geri kalanı)
    return "--°", "-- cm", "-- cm/s", {}, "-- cm"

@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'), Output('max-depth', 'children')],
    Input('interval-component-main', 'n_intervals')
)
def update_analysis_panel(n):
    scan = get_latest_scan()
    # ... (Fonksiyonun geri kalanı)
    return "-- cm²", "-- cm", "-- cm", "-- cm"


@app.callback(
    Output('graph-container-main', 'children'),
    Input('graph-selector-dropdown', 'value')
)
def update_active_graph(selected_graph):
    # Bu eski bir callback, artık kullanılmıyor. Yeni sistem update_all_graphs ile entegre.
    return no_update

@app.callback(
    Output('download-csv', 'data'), Input('export-csv-button', 'n_clicks'), prevent_initial_call=True
)
def export_csv_callback(n_clicks):
    scan = get_latest_scan()
    if not scan: return dcc.send_data_frame(pd.DataFrame().to_csv, "tarama_yok.csv", index=False)
    df = pd.DataFrame(list(scan.points.all().values()))
    return dcc.send_data_frame(df.to_csv, f"tarama_id_{scan.id}.csv", index=False)


@app.callback(
    Output('download-excel', 'data'), Input('export-excel-button', 'n_clicks'), prevent_initial_call=True
)
def export_excel_callback(n_clicks):
    from scanner.models import Scan
    scan = get_latest_scan()
    if not scan: return dcc.send_bytes(b"", "tarama_yok.xlsx")
    # ... (Fonksiyonun geri kalanı)
    return dcc.send_bytes(b"", "hata.xlsx")

@app.callback(
    Output('tab-content-datatable', 'children'),
    [Input('visualization-tabs-main', 'active_tab'), Input('interval-component-main', 'n_intervals')]
)
def render_and_update_data_table(active_tab, n):
    if active_tab != "tab-datatable": return None
    scan = get_latest_scan()
    # ... (Fonksiyonun geri kalanı)
    return html.P("Veri tablosu burada görünecek.")

@app.callback(
    Output('container-map-graph', 'style'),
    Output('container-regression-graph', 'style'),
    Output('container-polar-graph', 'style'),
    Output('container-time-series-graph', 'style'),
    Input('graph-selector-dropdown', 'value')
)
def update_graph_visibility(selected_graph):
    styles = [{'display': 'none'}] * 4
    if selected_graph == 'map': styles[0] = {'display': 'block'}
    elif selected_graph == 'regression': styles[1] = {'display': 'block'}
    elif selected_graph == 'polar': styles[2] = {'display': 'block'}
    elif selected_graph == 'time': styles[3] = {'display': 'block'}
    return tuple(styles)


@app.callback(
    [
        Output('scan-map-graph', 'figure'), Output('polar-regression-graph', 'figure'),
        Output('polar-graph', 'figure'), Output('time-series-graph', 'figure'),
        Output('environment-estimation-text', 'children'), Output('clustered-data-store', 'data')
    ],
    Input('interval-component-main', 'n_intervals')
)
def update_all_graphs(n):
    from scanner.models import ScanPoint
    # Bu fonksiyonun içeriği uzun ve karmaşık olduğundan,
    # kendi çalışan kodunuzdaki versiyonun burada olduğundan emin olun.
    # Ana mantık: get_latest_scan -> df oluştur -> analiz fonksiyonlarını çağır -> figürleri döndür
    return go.Figure(), go.Figure(), go.Figure(), go.Figure(), "Analiz bekleniyor...", no_update

@app.callback(
    [Output("cluster-info-modal", "is_open"), Output("modal-title", "children"), Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")], [State("clustered-data-store", "data")], prevent_initial_call=True
)
def display_cluster_info(clickData, stored_data_json):
    # ... (Fonksiyonun içeriği)
    return False, no_update, no_update

@app.callback(
    [Output('ai-yorum-sonucu', 'children'), Output('ai-image', 'children')],
    [Input('ai-model-dropdown', 'value')],
    prevent_initial_call=True
)
def yorumla_model_secimi(selected_model_value):
    from scanner.models import Scan
    if not selected_model_value: return html.Div("Yorum için bir model seçin."), no_update
    scan = get_latest_scan()
    if not scan: return dbc.Alert("Analiz edilecek tarama yok."), no_update
    points_qs = scan.points.all().values('derece', 'mesafe_cm')
    if not points_qs: return dbc.Alert("Yorumlanacak veri yok."), no_update
    df = pd.DataFrame(list(points_qs))
    yorum_text = yorumla_tablo_verisi_gemini(df, selected_model_value)
    if "Hata:" in yorum_text: return dbc.Alert(yorum_text, color="danger"), no_update
    base64_images = image_generate(yorum_text)
    image_components = [html.Img(src=src, style={'maxWidth': '100%'}) for src in base64_images if not src.startswith("Hata:")] or [dbc.Alert(base64_images[0], color="warning")]
    try:
        scan.ai_commentary = yorum_text; scan.save(update_fields=['ai_commentary'])
    except Exception as e: print(f"DB Kayıt Hatası: {e}")
    return dbc.Alert(dcc.Markdown(yorum_text), color="success"), html.Div(image_components)