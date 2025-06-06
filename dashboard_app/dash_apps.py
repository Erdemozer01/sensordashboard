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

from scipy.spatial import ConvexHull
from sklearn.cluster import DBSCAN
from sklearn.linear_model import RANSACRegressor
import google.generativeai as genai

# Attempt to import Django models; handle cases where they might not be available
try:
    from django.db.models import Max
    from scanner.models import Scan, ScanPoint

    DJANGO_MODELS_AVAILABLE = True
    print("Dashboard: Django modelleri başarıyla import edildi.")
except ModuleNotFoundError:
    print("UYARI: 'scanner.models' import edilemedi. Django proje yapınızı ve PYTHONPATH'ı kontrol edin.")
    print("Django entegrasyonu olmadan devam edilecek, veritabanı işlemleri çalışmayabilir.")
    DJANGO_MODELS_AVAILABLE = False
    Scan, ScanPoint = None, None
except Exception as e:
    print(f"Django modelleri import edilirken bir hata oluştu: {e}")
    DJANGO_MODELS_AVAILABLE = False
    Scan, ScanPoint = None, None

# Dash and Plotly Libraries
from django_plotly_dash import DjangoDash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt

# Google Generative AI import (with error handling)
try:
    from google import generativeai
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    print("UYARI: 'google.generativeai' kütüphanesi bulunamadı. AI yorumlama özelliği çalışmayacak.")
    GOOGLE_GENAI_AVAILABLE = False

from dotenv import load_dotenv

load_dotenv() # Loads environment variables from .env file
google_api_key = os.getenv("GOOGLE_API_KEY")

# --- CONSTANTS AND APPLICATION INITIALIZATION ---
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
FREE_MOVEMENT_SCRIPT_FILENAME = 'free_movement_script.py'

SENSOR_SCRIPT_PATH = os.path.join(os.getcwd(), SENSOR_SCRIPT_FILENAME)
FREE_MOVEMENT_SCRIPT_PATH = os.path.join(os.getcwd(), FREE_MOVEMENT_SCRIPT_FILENAME)

SENSOR_SCRIPT_LOCK_FILE = '/tmp/sensor_scan_script.lock'
SENSOR_SCRIPT_PID_FILE = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_DURATION_ANGLE = 270.0
DEFAULT_UI_SCAN_STEP_ANGLE = 10.0
DEFAULT_UI_BUZZER_DISTANCE = 10
DEFAULT_UI_INVERT_MOTOR = False
DEFAULT_UI_STEPS_PER_REVOLUTION = 4096
# Define a default servo angle for the UI, matching sensor_script's default
DEFAULT_UI_SERVO_ANGLE = 90


app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- NAVBAR CREATION ---
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

# --- LAYOUT COMPONENTS ---
title_card = dbc.Row(
    [dbc.Col(html.H1("Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12), html.Hr()]
)

# This is the updated control_panel code
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
            value='scan_and_map',
            inline=False,
            className="mb-3",
        ),
        html.Hr(),
        dbc.Row([
            dbc.Col(html.Button('Başlat', id='start-scan-button', n_clicks=0,
                                 className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Durdur', id='stop-scan-button', n_clicks=0,
                                 className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),
        html.Hr(),
        html.Div(id='scan-parameters-wrapper', children=[
            html.H6("Yapay Zeka Seçimi:", className="mt-3"),
            dcc.Dropdown(
                id='ai-model-dropdown',
                options=[
                    {'label': 'Gemini Flash (Hızlı)', 'value': 'gemini-2.5-flash-preview-05-20'},
                    {'label': 'Gemini Pro (Gelişmiş)', 'value': 'gemini-2.5-pro-preview-06-05'},
                    {'label': 'Gemma', 'value': 'gemma-3n-e4b-it'},
                ],
                placeholder="Yorumlama için bir metin modeli seçin...",
                clearable=True,
                className="mb-3"
            ),
            html.Hr(),
            html.H6("Tarama Parametreleri:", className="mt-2"),
            dbc.InputGroup([dbc.InputGroupText("Tarama Açısı (°)", style={"width": "150px"}),
                            dbc.Input(id="scan-duration-angle-input", type="number",
                                      value=DEFAULT_UI_SCAN_DURATION_ANGLE,
                                      min=10, max=720, step=1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "150px"}),
                            dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=0.1,
                                      max=45, step=0.1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Uyarı Mes. (cm)", style={"width": "150px"}),
                            dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE,
                                      min=0,
                                      max=200, step=1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Motor Adım/Tur", style={"width": "150px"}),
                            dbc.Input(id="steps-per-rev-input", type="number", value=DEFAULT_UI_STEPS_PER_REVOLUTION,
                                      min=500, max=10000, step=1)], className="mb-2"),
            # NEW: Servo Angle Slider
            dbc.InputGroup([dbc.InputGroupText("Dikey Açı (°)", style={"width": "150px"}),
                            dcc.Slider(
                                id='servo-angle-slider',
                                min=0, max=180, step=1, value=DEFAULT_UI_SERVO_ANGLE,
                                marks={0: '0°', 45: '45°', 90: '90°', 135: '135°', 180: '180°'},
                                tooltip={"placement": "bottom", "always_visible": True},
                                className="mt-2" # Add some margin
                            )], className="mb-2"),
            dbc.Checkbox(id="invert-motor-checkbox", label="Motor Yönünü Ters Çevir", value=DEFAULT_UI_INVERT_MOTOR,
                         className="mt-2 mb-2"),
        ])
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
        dbc.Tab(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Dropdown(
                                id='graph-selector-dropdown',
                                options=[
                                    {'label': '3D Harita', 'value': '3d_map'}, # Changed value to '3d_map'
                                    {'label': '2D Kartezyen Harita', 'value': 'map'},
                                    {'label': 'Regresyon Analizi', 'value': 'regression'},
                                    {'label': 'Polar Grafik', 'value': 'polar'},
                                    {'label': 'Zaman Serisi (Mesafe)', 'value': 'time'},
                                ],
                                value='3d_map', # Default to 3D map
                                clearable=False,
                                style={'marginTop': '10px'}
                            ),
                            width=6,
                        )
                    ],
                    justify="center",
                    className="mb-3"
                ),
                html.Div(
                    [
                        html.Div(dcc.Graph(id='scan-map-graph-3d', style={'height': '75vh'}), id='container-map-graph-3d'), # NEW: 3D graph container
                        html.Div(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), id='container-map-graph'),
                        html.Div(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}),
                                 id='container-regression-graph'),
                        html.Div(dcc.Graph(id='polar-graph', style={'height': '75vh'}), id='container-polar-graph'),
                        html.Div(dcc.Graph(id='time-series-graph', style={'height': '75vh'}),
                                 id='container-time-series-graph'),
                    ]
                )
            ],
            label="Grafikler",
            tab_id="tab-graphics"
        ),
        dbc.Tab(
            dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]),
            label="Veri Tablosu",
            tab_id="tab-datatable"
        )
    ],
    id="visualization-tabs-main",
    active_tab="tab-graphics"
)

app.layout = html.Div(
    style={'padding': '20px'},
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
                                                        ], className="text-center mt-2"),
                                                        html.Div(id='ai-image', children=[
                                                            html.P(
                                                                "Ortamın görüntüsünü oluşturmak için model seçin"),
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

# --- HELPER FUNCTIONS (omitted for brevity, assume they are the same as provided earlier) ---
# ... (all helper functions: is_process_running, get_latest_scan, add_scan_rays, etc.) ...
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
        if running_scan: return running_scan
        return Scan.objects.order_by('-start_time').first()
    except Exception as e:
        print(f"DB Hatası (get_latest_scan): {e}");
        return None

def add_scan_rays(fig, df):
    if df.empty or not all(col in df.columns for col in ['x_cm', 'y_cm']): return
    x_lines, y_lines = [], []
    for _, row in df.iterrows():
        x_lines.extend([0, row['y_cm'], None])
        y_lines.extend([0, row['x_cm'], None])
    fig.add_trace(
        go.Scatter(x=x_lines, y=y_lines, mode='lines', line=dict(color='rgba(255,100,100,0.4)', dash='dash', width=1),
                   showlegend=False))

def add_sector_area(fig, df):
    if df.empty or not all(col in df.columns for col in ['x_cm', 'y_cm']): return
    poly_x, poly_y = df['y_cm'].tolist(), df['x_cm'].tolist()
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
        df_s = df.copy()
        df_s['timestamp'] = pd.to_datetime(df_s['timestamp'], errors='coerce')
        df_s.dropna(subset=['timestamp'], inplace=True)
        if len(df_s) < 2:
            fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Yetersiz Veri'))
            return
        df_s = df_s.sort_values(by='timestamp')
        min_time = df_s['timestamp'].min()
        max_time = df_s['timestamp'].max()
        padding = pd.Timedelta(seconds=(max_time - min_time).total_seconds() * 0.05)
        if padding.total_seconds() < 2:
            padding = pd.Timedelta(seconds=2)
        x_range_start = min_time - padding
        x_range_end = max_time + padding
        fig.add_trace(go.Scatter(
            x=df_s['timestamp'],
            y=df_s['mesafe_cm'],
            mode='lines+markers',
            name='Mesafe'
        ))
        fig.update_layout(
            xaxis_type='date',
            xaxis_range=[x_range_start, x_range_end],
            xaxis_title="Zaman",
            yaxis_title="Mesafe (cm)",
            xaxis_tickformat='%d %b %Y<br>%H:%M:%S',
            xaxis_rangeselector=dict(
                buttons=list([
                    dict(count=1, label="1dk", step="minute", stepmode="backward"),
                    dict(count=5, label="5dk", step="minute", stepmode="backward"),
                    dict(count=15, label="15dk", step="minute", stepmode="backward"),
                    dict(step="all", label="Tümü")
                ])
            ),
            xaxis_rangeslider_visible=True
        )
    except Exception as e:
        logging.error(f"Zaman serisi grafiği oluşturulurken HATA: {e}")
        fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Grafik Hatası'))

def find_clearest_path(df_valid):
    if df_valid.empty or not all(
        col in df_valid.columns for col in ['mesafe_cm', 'derece']): return "En açık yol için veri yok."
    try:
        # Hata buradaydı: df_filtered yerine df_valid kullanılmalı
        df_filtered = df_valid[df_valid['mesafe_cm'] > 0]
        if df_filtered.empty: return "Geçerli pozitif mesafe bulunamadı."
        cp = df_filtered.loc[df_filtered['mesafe_cm'].idxmax()]
        return f"En Açık Yol: {cp['derece']:.1f}° yönünde, {cp['mesafe_cm']:.1f} cm."
    except Exception as e:
        print(f"En açık yol hesaplama hatası: {e}");
        return "En açık yol hesaplanamadı."

def analyze_polar_regression(df_valid):
    if len(df_valid) < 5 or not all(
        col in df_valid.columns for col in
        ['mesafe_cm', 'derece']): return None, "Polar regresyon için yetersiz veri."
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
        print(f"Polar regresyon hatası: {e}");
        return None, "Polar regresyon hatası."

def analyze_environment_shape(fig, df_valid_input):
    df_valid = df_valid_input.copy()
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
        cluster_points_np = cluster_points_df[['y_cm', 'x_cm']].to_numpy()
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
        hull_area = hull.area
        min_x_val, max_x_val = df['x_cm'].min(), df['x_cm'].max()
        min_y_val, max_y_val = df['y_cm'].min(), df['y_cm'].max()
        width = max_y_val - min_y_val
        depth = max_x_val
        if width < 1 or depth < 1: return "Algılanan şekil çok küçük."
        bbox_area = depth * width
        fill_factor = hull_area / bbox_area if bbox_area > 0 else 0
        if depth > 150 and width < 50 and fill_factor < 0.3: return "Tahmin: Dar ve derin bir boşluk (Koridor)."
        if fill_factor > 0.7 and (
            0.8 < (width / depth if depth > 0 else 0) < 1.2): return "Tahmin: Dolgun, kutu/dairesel bir nesne."
        if fill_factor > 0.6 and width > depth * 2.5: return "Tahmin: Geniş bir yüzey (Duvar)."
        if fill_factor < 0.4: return "Tahmin: İçbükey bir yapı veya dağınık nesneler."
        return "Tahmin: Düzensiz veya karmaşık bir yapı."
    except Exception as e:
        print(f"Geometrik analiz hatası: {e}");
        return "Geometrik analiz hatası."

def yorumla_tablo_verisi_gemini(df, model_name):
    if not GOOGLE_GENAI_AVAILABLE: return "Hata: Google GenerativeAI kütüphanesi yüklenemedi."
    if not google_api_key: return "Hata: `GOOGLE_API_KEY` ayarlanmamış."
    if df is None or df.empty: return "Yorumlanacak tablo verisi bulunamadı."
    try:
        generativeai.configure(api_key=google_api_key)
        model = generativeai.GenerativeModel(model_name=model_name)
        prompt_text = (
            f"Aşağıdaki tablo, bir ultrasonik sensörün yaptığı taramadan elde edilen verileri içermektedir: "
            f"\n\n{df.to_string(index=False)}\n\n"
            "Bu verilere dayanarak, ortamın olası yapısını (örneğin: 'geniş bir oda', 'dar bir koridor', 'köşeye yerleştirilmiş nesne') analiz et ve alanını , çevresini ortamın geometrik şeklini tahmin etmeye çalış "
            "Verilerdeki desenlere göre potansiyel nesneleri (duvar, köşe, sandalye bacağı, kutu, insan vb.) tahmin etmeye çalış. Cevabını Markdown formatında, başlıklar ve listeler kullanarak düzenli bir şekilde sun."
        )
        response = model.generate_content(contents=prompt_text)
        return response.text
    except Exception as e:
        return f"Gemini'den yanıt alınırken bir hata oluştu: {e}"

def summarize_analysis_for_image_prompt(analysis_text, model_name):
    """
    Converts detailed text analysis into a short, visual prompt for an image generation model.
    """
    if not GOOGLE_GENAI_AVAILABLE or not google_api_key:
        return "Özetleme için AI modeline erişilemiyor."
    if not analysis_text or "Hata:" in analysis_text:
        return "Geçersiz analiz metni özetlenemez."

    try:
        generativeai.configure(api_key=google_api_key)
        model = generativeai.GenerativeModel(model_name=model_name)

        summarization_prompt = (
            "Aşağıdaki teknik sensör verisi analizini temel alarak, taranan ortamı betimleyen "
            "kısa, canlı ve görsel bir paragraf oluştur. Bu paragraf, bir yapay zeka resim üreticisi "
            "için komut olarak kullanılacak. Ana geometrik şekillere, tahmin edilen nesnelere (duvar, koridor, kutu gibi) "
            "ve ortamın genel atmosferine odaklan. Sayısal değerler veya teknik jargon kullanma. "
            "Sanki ortama bakıyormuşsun gibi betimle. İşte analiz metni:\n\n"
            f"{analysis_text}"
        )

        response = model.generate_content(contents=summarization_prompt)

        if response.text and len(response.text) > 10:
            print(f"✅ Görüntü için özet prompt oluşturuldu: {response.text}")
            return response.text
        else:
            return f"Şu analize dayanan teknik bir çizim: {analysis_text[:500]}"

    except Exception as e:
        print(f"Görüntü istemi özetlenirken hata oluştu: {e}")
        return f"Şu analize dayanan bir şema: {analysis_text[:500]}"

def generate_image_from_text(analysis_text, model_name="gemini-2.0-flash-exp-image-generation"):
    """
    Generates an image by directly interpreting the provided detailed text analysis.
    Increased output token limit to prevent MAX_TOKENS error.
    """
    if not GOOGLE_GENAI_AVAILABLE:
        return dbc.Alert("Hata: Google GenerativeAI kütüphanesi yüklenemedi.", color="danger")
    if not google_api_key:
        return dbc.Alert("Hata: `GOOGLE_API_KEY` ayarlanmamış.", color="danger")
    if not analysis_text or "Hata:" in analysis_text:
        return dbc.Alert("Resim oluşturmak için geçerli bir metin analizi gerekli.", color="warning")

    try:
        genai.configure(api_key=google_api_key)
        model = genai.GenerativeModel(model_name=model_name)

        # NEW: Configuration setting to allow more output space for the model
        generation_config = genai.types.GenerationConfig(
            max_output_tokens=4096 # Higher value (default is usually lower)
        )

        final_prompt = (
            "Aşağıda bir ultrasonik sensör taramasının metin tabanlı analizi yer almaktadır. "
            "Bu analizi temel alarak, taranan ortamın yukarıdan (top-down) görünümlü bir şematik haritasını veya "
            "gerçekçi bir tasvirini oluştur. Analizde bahsedilen duvarları, boşlukları, koridorları ve olası nesneleri "
            "görselleştir. Senin görevin bu metni bir resme dönüştürmek. Sonuç sadece resim olmalıdır.\n\n"
            f"--- ANALİZ METNİ ---\n{analysis_text}"
        )

        print(">> Doğrudan metin analizinden resim isteniyor (Artırılmış Token Limiti ile)...")
        # NEW: generation_config parameter added
        response = model.generate_content(final_prompt, generation_config=generation_config)

        if response.candidates and response.candidates[0].content.parts:
            part = response.candidates[0].content.parts[0]
            if hasattr(part, 'file_data') and hasattr(part.file_data, 'file_uri') and part.file_data.file_uri:
                print(f"✅ Başarılı: Resim URI'si bulundu: {part.file_data.file_uri}")
                return html.Img(
                    src=part.file_data.file_uri,
                    style={'maxWidth': '100%', 'height': 'auto', 'borderRadius': '10px', 'marginTop': '10px'}
                )

        # Let's make the error message more informative by including finish_reason
        finish_reason = response.candidates[0].finish_reason if response.candidates else 'Bilinmiyor'
        safety_ratings = response.candidates[0].safety_ratings if response.candidates else 'Yok'

        error_message = (f"Model, analiz metninden bir resim oluşturamadı. "
                         f"Bitiş Sebebi: {finish_reason}. Güvenlik Derecelendirmeleri: {safety_ratings}")

        raise Exception(error_message)

    except Exception as e:
        return dbc.Alert(f"Doğrudan analizden resim oluşturulurken bir hata oluştu: {e}", color="danger")

# --- CALLBACK FUNCTIONS ---

@app.callback(
    Output('scan-parameters-wrapper', 'style'),
    Input('mode-selection-radios', 'value')
)
def toggle_parameter_visibility(selected_mode):
    """Toggles visibility of scan parameters based on the selected operating mode."""
    if selected_mode == 'scan_and_map':
        return {'display': 'block'}
    else:
        return {'display': 'none'}


@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('mode-selection-radios', 'value'),
     State('scan-duration-angle-input', 'value'), State('step-angle-input', 'value'),
     State('buzzer-distance-input', 'value'), State('invert-motor-checkbox', 'value'),
     State('steps-per-rev-input', 'value'), State('servo-angle-slider', 'value')],  # NEW State
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks, selected_mode, duration, step, buzzer_dist, invert, steps_rev, servo_angle):
    """Handles starting the sensor script based on selected mode and parameters."""
    if n_clicks == 0:
        return no_update
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid = int(pf.read().strip())
            if is_process_running(pid):
                return dbc.Alert(f"Bir betik zaten çalışıyor (PID:{pid}). Önce durdurun.", color="warning")
        except:
            pass
    for fp_lock_pid in [SENSOR_SCRIPT_LOCK_FILE, SENSOR_SCRIPT_PID_FILE]:
        if os.path.exists(fp_lock_pid):
            try:
                os.remove(fp_lock_pid)
            except OSError as e_rm:
                print(f"Eski dosya silinemedi ({fp_lock_pid}): {e_rm}")
    py_exec = sys.executable
    cmd = []
    if selected_mode == 'scan_and_map':
        if not (isinstance(duration, (int, float)) and 10 <= duration <= 720): return dbc.Alert(
            "Tarama Açısı 10-720 derece arasında olmalı!", color="danger", duration=4000)
        if not (isinstance(step, (int, float)) and 0.1 <= abs(step) <= 45): return dbc.Alert(
            "Adım açısı 0.1-45 arasında olmalı!", color="danger", duration=4000)
        if not (isinstance(buzzer_dist, (int, float)) and 0 <= buzzer_dist <= 200): return dbc.Alert(
            "Uyarı mesafesi 0-200 cm arasında olmalı!", color="danger", duration=4000)
        if not (isinstance(steps_rev, (int, float)) and 500 <= steps_rev <= 10000): return dbc.Alert(
            "Motor Adım/Tur 500-10000 arasında olmalı!", color="danger", duration=4000)
        cmd = [py_exec, SENSOR_SCRIPT_PATH,
               "--scan_duration_angle", str(duration),
               "--step_angle", str(step),
               "--buzzer_distance", str(buzzer_dist),
               "--invert_motor_direction", str(invert),
               "--steps_per_rev", str(steps_rev),
               "--servo_angle", str(servo_angle)]  # NEW: Pass servo_angle
    elif selected_mode == 'free_movement':
        cmd = [py_exec, FREE_MOVEMENT_SCRIPT_PATH]
    else:
        return dbc.Alert("Geçersiz mod seçildi!", color="danger")
    try:
        if not os.path.exists(cmd[1]):
            return dbc.Alert(f"HATA: Betik dosyası bulunamadı: {cmd[1]}", color="danger")
        subprocess.Popen(cmd, start_new_session=True)
        max_wait_time, check_interval, start_time_wait = 7, 0.25, time.time()
        pid_file_found = False
        while time.time() - start_time_wait < max_wait_time:
            if os.path.exists(SENSOR_SCRIPT_PID_FILE):
                pid_file_found = True
                break
            time.sleep(check_interval)
        if pid_file_found:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                new_pid = pf.read().strip()
            mode_name = "Mesafe Ölçüm Modu" if selected_mode == 'scan_and_map' else "Serbest Hareket Modu"
            return dbc.Alert(f"{mode_name} başlatıldı (PID:{new_pid}).", color="success")
        else:
            return dbc.Alert(f"Başlatılamadı. PID dosyası {max_wait_time} saniye içinde oluşmadı.", color="danger")
    except Exception as e:
        return dbc.Alert(f"Betik başlatma hatası: {e}", color="danger")


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True), # Allow duplicate as this might be updated by stop button too
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks):
    if n_clicks == 0: return no_update
    pid_to_kill = None
    message = ""
    color = "warning"
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_to_kill = int(pf.read().strip())
        except (IOError, ValueError):
            pass
    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            os.kill(pid_to_kill, signal.SIGTERM) # Try graceful termination first
            time.sleep(1) # Give process time to terminate
            if is_process_running(pid_to_kill): # If still running, force kill
                os.kill(pid_to_kill, signal.SIGKILL)
                time.sleep(0.5)
            if not is_process_running(pid_to_kill):
                message = f"Çalışan betik (PID:{pid_to_kill}) durduruldu."
                color = "info"
            else:
                message = f"Betik (PID:{pid_to_kill}) durdurulamadı!"
                color = "danger"
        except ProcessLookupError: # Process might have already died
            message = f"Betik (PID:{pid_to_kill}) zaten çalışmıyordu."; color = "warning"
        except Exception as e:
            message = f"Durdurma hatası: {e}"; color = "danger"
    else:
        message = "Çalışan betik bulunamadı."
    # Clean up PID and lock files regardless of kill success
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
    """Updates system status (script, CPU, RAM usage) periodically."""
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
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory().percent
    return status_text, status_class, cpu, f"{cpu:.1f}%", ram, f"{ram:.1f}%"


@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children'),
     Output('current-distance-col', 'style'), Output('max-detected-distance', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n):
    """Updates real-time sensor values (angle, distance, speed, max distance) and applies buzzer styling."""
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
            if buzzer_threshold is not None and pd.notnull(point.mesafe_cm) and 0 < point.mesafe_cm <= buzzer_threshold:
                dist_style.update({'backgroundColor': '#d9534f', 'color': 'white'})
            max_dist_agg = scan.points.filter(mesafe_cm__lt=2500, mesafe_cm__gt=0).aggregate(
                max_dist_val=Max('mesafe_cm'))
            if max_dist_agg and max_dist_agg.get('max_dist_val') is not None:
                max_dist_s = f"{max_dist_agg['max_dist_val']:.1f} cm"
    return angle_s, dist_s, speed_s, dist_style, max_dist_s


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'),
     Output('max-depth', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n):
    """Updates calculated analysis metrics (area, perimeter, max width/depth) for the latest scan."""
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
    """Exports the latest scan data to a CSV file."""
    if not n_clicks_csv: return no_update
    scan = get_latest_scan()
    if not scan: return dcc.send_data_frame(pd.DataFrame().to_csv, "tarama_yok.csv", index=False)
    points_qs = scan.points.all().values()
    if not points_qs: return dcc.send_data_frame(pd.DataFrame().to_csv, f"tarama_id_{scan.id}_nokta_yok.csv",
                                                 index=False)
    df = pd.DataFrame(list(points_qs))
    return dcc.send_data_frame(df.to_csv, f"tarama_id_{scan.id}_noktalar.csv", index=False)


@app.callback(Output('download-excel', 'data'), Input('export-excel-button', 'n_clicks'), prevent_initial_call=True)
def export_excel_callback(n_clicks_excel):
    """Exports the latest scan data and metadata to an Excel file."""
    if not n_clicks_excel: return no_update
    scan = get_latest_scan()
    if not scan: return dcc.send_bytes(b"", "tarama_yok.xlsx")
    try:
        scan_info_data = Scan.objects.filter(id=scan.id).values().first()
        scan_info_df = pd.DataFrame([scan_info_data]) if scan_info_data else pd.DataFrame()
        points_df = pd.DataFrame(list(scan.points.all().values()))
    except Exception as e_excel_data:
        print(f"Excel için veri çekme hatası: {e_excel_data}")
        return dcc.send_bytes(b"", f"veri_cekme_hatasi_{scan.id if scan else 'yok'}.xlsx")
    with io.BytesIO() as buf:
        try:
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                if not scan_info_df.empty: scan_info_df.to_excel(writer, sheet_name=f'Scan_{scan.id}_Info', index=False)
                if not points_df.empty:
                    points_df.to_excel(writer, sheet_name=f'Scan_{scan.id}_Points', index=False)
                elif scan_info_df.empty:
                    pd.DataFrame().to_excel(writer, sheet_name='Veri Yok', index=False)
        except Exception as e_excel_write:
            print(f"Excel yazma hatası: {e_excel_write}")
            pd.DataFrame([{"Hata": str(e_excel_write)}]).to_excel(writer, sheet_name='Hata', index=False)
        return dcc.send_bytes(buf.getvalue(), f"tarama_detaylari_id_{scan.id if scan else 'yok'}.xlsx")


@app.callback(Output('tab-content-datatable', 'children'),
              [Input('visualization-tabs-main', 'active_tab'), Input('interval-component-main', 'n_intervals')])
def render_and_update_data_table(active_tab, n):
    """Renders and updates the data table with the latest scan points."""
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
                                             'overflowY': 'auto', 'overflowX': 'auto'},
                                page_size=50, sort_action="native", filter_action="native", virtualization=True,
                                fixed_rows={'headers': True},
                                style_data_conditional=[
                                    {'if': {'row_index': 'odd'}, 'backgroundColor': 'rgb(248, 248, 248)'}])


@app.callback(
    [
        Output('scan-map-graph-3d', 'figure'),  # NEW: Output for 3D map
        Output('scan-map-graph', 'figure'),
        Output('polar-regression-graph', 'figure'),
        Output('polar-graph', 'figure'),
        Output('time-series-graph', 'figure'),
        Output('environment-estimation-text', 'children'),
        Output('clustered-data-store', 'data')
    ],
    Input('interval-component-main', 'n_intervals')
)
def update_all_graphs(n):
    """
    Main callback that periodically updates all graphs and analyses.
    This function is the visual core of the application.
    """
    # --- DEBUGGING CODE START ---
    print("\n--- Grafik güncelleme tetiklendi ---")
    # Local import for safety, though it's already imported globally
    from scanner.models import Scan, ScanPoint

    scan = get_latest_scan()
    if not scan:
        print(">> DATA_DEBUG: get_latest_scan() fonksiyonu 'None' döndürdü. Veritabanında gösterilecek tarama yok.")

        empty_figs = [go.Figure() for _ in range(5)] # 5 empty figures
        empty_text = html.Div([html.P("Tarama başlatın veya verinin gelmesini bekleyin...")])
        return empty_figs[0], empty_figs[1], empty_figs[2], empty_figs[3], empty_figs[4], empty_text, None
    else:
        print(f">> DATA_DEBUG: Son tarama bulundu. Scan ID: {scan.id}, Durum: {scan.status}")
    # --- DEBUGGING CODE END ---

    # Initialize 5 figures: [0]=3D, [1]=2D, [2]=Regression, [3]=Polar, [4]=TimeSeries
    figs = [go.Figure() for _ in range(5)]
    est_text = html.Div([html.P("Tarama başlatın veya verinin gelmesini bekleyin...")])
    store_data = None
    scan_id_for_revision = 'initial_load'

    if scan:
        scan_id_for_revision = str(scan.id)
        # Fetch all necessary columns, including z_cm for 3D plot
        points_qs = ScanPoint.objects.filter(scan=scan).values('x_cm', 'y_cm', 'z_cm', 'derece', 'mesafe_cm',
                                                               'timestamp')
        df_pts = pd.DataFrame(list(points_qs))
        # Filter out invalid distance readings for analysis
        df_val = df_pts[(df_pts['mesafe_cm'] > 0.1) & (df_pts['mesafe_cm'] < 300.0)].copy()

        # --- NEW: 3D Scatter Plot (figs[0]) ---
        if not df_val.empty and all(k in df_val for k in ['x_cm', 'y_cm', 'z_cm']):
            figs[0].add_trace(go.Scatter3d(
                x=df_val['y_cm'],  # Use y_cm for Plotly's x-axis to match 2D map orientation (forward is positive X, right is positive Y)
                y=df_val['x_cm'],  # Use x_cm for Plotly's y-axis
                z=df_val['z_cm'],
                mode='markers',
                marker=dict(
                    size=3,
                    color=df_val['z_cm'],  # Color by height (Z-coordinate)
                    colorscale='Viridis',
                    showscale=True,
                    colorbar_title='Yükseklik (cm)'
                ),
                name='3D Noktalar'
            ))
            # Sensor position for 3D plot (if desired, though 3D scatter implies origin)
            figs[0].add_trace(go.Scatter3d(
                x=[0], y=[0], z=[0],
                mode='markers',
                marker=dict(size=8, symbol='circle', color='red'),
                name='Sensör Konumu'
            ))


        # --- DEBUGGING CODE START ---
        print(f">> DATA_DEBUG: Tarama #{scan.id} için {len(points_qs)} adet nokta bulundu.")
        if not points_qs:
            print(">> DATA_DEBUG: UYARI! Tarama var ama ilişkili nokta (ScanPoint) yok.")
        # --- DEBUGGING CODE END ---

        if not points_qs.empty: # Check if there are any points at all
            if len(df_val) >= 5: # Enough valid points for meaningful analysis
                # 2D Map (figs[1]) - Clustering, rays, and sector
                # Pass figs[1] to analyze_environment_shape as it will add traces to it
                est_cart, df_clus = analyze_environment_shape(figs[1], df_val.copy())
                store_data = df_clus.to_json(orient='split') # Store clustered data for modal
                add_scan_rays(figs[1], df_val) # Add scan rays to 2D map
                add_sector_area(figs[1], df_val) # Add scanned sector area to 2D map

                # Polar Regression (figs[2])
                line_data, est_polar = analyze_polar_regression(df_val)
                figs[2].add_trace(
                    go.Scatter(x=df_val['derece'], y=df_val['mesafe_cm'], mode='markers', name='Noktalar'))
                if line_data:
                    figs[2].add_trace(
                        go.Scatter(x=line_data['x'], y=line_data['y'], mode='lines', name='Regresyon Çizgisi',
                                   line=dict(color='red', width=3)))

                # Polar Graph (figs[3]) - The original polar plot
                update_polar_graph(figs[3], df_val)

                # Time Series Graph (figs[4])
                update_time_series_graph(figs[4], df_val)

                # Environment estimation text
                clear_path = find_clearest_path(df_val)
                shape_estimation = estimate_geometric_shape(df_val)
                est_text = html.Div([
                    html.P(shape_estimation, className="fw-bold"), html.Hr(),
                    html.P(clear_path, className="fw-bold text-primary"), html.Hr(),
                    html.P(f"Kümeleme: {est_cart}"), html.Hr(),
                    html.P(f"Regresyon: {est_polar}")
                ])
            else:
                est_text = html.Div([html.P("Analiz için yeterli sayıda geçerli nokta bulunamadı.")])
        else: # No scan points found for the latest scan
            est_text = html.Div([html.P(f"Tarama ID #{scan.id} için nokta verisi bulunamadı.")])

    # Add sensor position to 2D, Regression, Polar, and TimeSeries graphs
    # (Note: 3D graph already has sensor position added in its specific block)
    # The add_sensor_position function only adds a 2D marker at (0,0) so it's not suitable for 3D directly.
    for i in range(1, 5): # Apply to figs[1] (2D), figs[2] (Regression), figs[3] (Polar), figs[4] (Time Series)
        add_sensor_position(figs[i])

    # Titles for the 5 figures based on their new index
    titles = [
        'Ortamın 3D Haritası',
        '2D Harita (Projeksiyon)',
        'Açıya Göre Mesafe Regresyonu',
        'Polar Grafik',
        'Zaman Serisi - Mesafe'
    ]
    common_legend = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)

    for i, fig in enumerate(figs):
        fig.update_layout(title_text=titles[i], uirevision=scan_id_for_revision, legend=common_legend,
                          margin=dict(l=40, r=40, t=80, b=40))
        if i == 0:  # 3D Harita Düzeni
            fig.update_layout(scene=dict(
                xaxis_title='Y Ekseni (cm)', # Matching 2D map's Y-axis for forward view
                yaxis_title='X Ekseni (cm)', # Matching 2D map's X-axis
                zaxis_title='Z Ekseni (cm)',
                aspectmode='data', # Ensures equal scaling of axes
                aspectratio=dict(x=1, y=1, z=0.5), # Adjust aspect ratio for better visualization
                camera=dict(
                    eye=dict(x=1.2, y=1.2, z=0.8) # Adjust camera initial view
                )
            ))
        elif i == 1:  # 2D Harita Düzeni (This is the original scan-map-graph)
            fig.update_layout(xaxis_title="Yatay Mesafe (cm)", yaxis_title="Dikey Mesafe (cm)", yaxis_scaleanchor="x",
                              yaxis_scaleratio=1)
        elif i == 2:  # Regresyon Analizi (polar-regression-graph)
            fig.update_layout(xaxis_title="Tarama Açısı (Derece)", yaxis_title="Mesafe (cm)")
        # Polar and Time Series graphs will have their specific layouts set by their update functions.

    # Return all 7 outputs in the correct order
    return figs[0], figs[1], figs[2], figs[3], figs[4], est_text, store_data


@app.callback(
    [Output('container-map-graph-3d', 'style'),  # NEW: 3D graph container style output
     Output('container-map-graph', 'style'),
     Output('container-regression-graph', 'style'),
     Output('container-polar-graph', 'style'),
     Output('container-time-series-graph', 'style')],
    Input('graph-selector-dropdown', 'value')
)
def update_graph_visibility(selected_graph):
    """Controls the visibility of different graph types based on dropdown selection."""
    style_3d = {'display': 'none'}  # Initialize 3D graph style
    style_map = {'display': 'none'}
    style_regression = {'display': 'none'}
    style_polar = {'display': 'none'}
    style_time = {'display': 'none'}

    if selected_graph == '3d_map': # Corresponds to the 'value' in the dropdown
        style_3d = {'display': 'block'}
    elif selected_graph == 'map':
        style_map = {'display': 'block'}
    elif selected_graph == 'regression':
        style_regression = {'display': 'block'}
    elif selected_graph == 'polar':
        style_polar = {'display': 'block'}
    elif selected_graph == 'time':
        style_time = {'display': 'block'}

    return style_3d, style_map, style_regression, style_polar, style_time


@app.callback(
    [Output("cluster-info-modal", "is_open"), Output("modal-title", "children"), Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")], [State("clustered-data-store", "data")], prevent_initial_call=True)
def display_cluster_info(clickData, stored_data_json):
    """Displays detailed information about a clicked cluster in a modal."""
    if not clickData or not stored_data_json: return False, no_update, no_update
    try:
        df_clus = pd.read_json(stored_data_json, orient='split')
        if 'cluster' not in df_clus.columns: return False, "Hata", "Küme verisi bulunamadı."
        cl_label = clickData["points"][0].get('customdata')
        if cl_label is None:
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


@app.callback(
    [Output('ai-yorum-sonucu', 'children'), Output('ai-image', 'children')],
    [Input('ai-model-dropdown', 'value')],
    prevent_initial_call=True
)
def yorumla_model_secimi(selected_model_value):
    """
    Triggers AI-powered environment interpretation and image generation based on the selected AI model.
    """
    if not selected_model_value:
        return [html.Div("Yorum için bir model seçin.", className="text-center"), no_update]

    scan = get_latest_scan()
    if not scan:
        return [dbc.Alert("Analiz edilecek bir tarama bulunamadı.", color="warning"), no_update]

    # Step 1: Get detailed text analysis
    if scan.ai_commentary and scan.ai_commentary.strip():
        yorum_text_from_ai = scan.ai_commentary
        commentary_component = dbc.Alert(
            dcc.Markdown(yorum_text_from_ai, dangerously_allow_html=True, link_target="_blank"), color="info")
    else:
        points_qs = scan.points.all().values('derece', 'mesafe_cm')
        if not points_qs:
            return [dbc.Alert("Yorumlanacak tarama verisi bulunamadı.", color="warning"), no_update]
        df_data_for_ai = pd.DataFrame(list(points_qs))
        if len(df_data_for_ai) > 500:
            df_data_for_ai = df_data_for_ai.sample(n=500, random_state=1)

        yorum_text_from_ai = yorumla_tablo_verisi_gemini(df_data_for_ai, selected_model_value)
        if "Hata:" in yorum_text_from_ai:
            return [dbc.Alert(yorum_text_from_ai, color="danger"),
                    dbc.Alert("Metin yorumu alınamadığı için resim oluşturulamadı.", color="warning")]
        try:
            scan.ai_commentary = yorum_text_from_ai
            scan.save()
        except Exception as e_db_save:
            print(f"Veritabanına AI yorumu kaydedilemedi: {e_db_save}")
        commentary_component = dbc.Alert(
            dcc.Markdown(yorum_text_from_ai, dangerously_allow_html=True, link_target="_blank"), color="success")

    # Image generation uses the obtained analysis text directly
    image_component = generate_image_from_text(yorum_text_from_ai, selected_model_value)

    return [commentary_component, image_component]