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
            dbc.Col(html.Button('Başlat', id='start-scan-button', n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(
                html.Button('Durdur', id='stop-scan-button', n_clicks=0, className="btn btn-danger btn-lg w-100 mb-2"),
                width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),
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
            dbc.InputGroup([dbc.InputGroupText("Tarama Açısı (°)", style={"width": "150px"}),
                            dbc.Input(id="scan-duration-angle-input", type="number",
                                      value=DEFAULT_UI_SCAN_DURATION_ANGLE, min=10, max=720, step=1)],
                           className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "150px"}),
                            dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=0.1,
                                      max=45, step=0.1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Uyarı Mes. (cm)", style={"width": "150px"}),
                            dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE,
                                      min=0, max=200, step=1)], className="mb-2"),
            dbc.InputGroup([dbc.InputGroupText("Motor Adım/Tur", style={"width": "150px"}),
                            dbc.Input(id="steps-per-rev-input", type="number", value=DEFAULT_UI_STEPS_PER_REVOLUTION,
                                      min=500, max=10000, step=1)], className="mb-2"),
            dbc.Checkbox(id="invert-motor-checkbox", label="Motor Yönünü Ters Çevir", value=DEFAULT_UI_INVERT_MOTOR,
                         className="mt-2 mb-2"),
        ])
    ])
])

stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"), dbc.CardBody(dbc.Row([
    dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=3,
            className="text-center border-end"),
    dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]),
            id='current-distance-col', width=3, className="text-center rounded border-end"),
    dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=3,
            className="text-center border-end"),
    dbc.Col(html.Div([html.H6("Max. Algılanan Mesafe:"), html.H4(id='max-detected-distance', children="-- cm")]),
            width=3, className="text-center")
]))], className="mb-3")

system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"), dbc.CardBody([
    dbc.Row([dbc.Col(html.Div([html.H6("Sensör Betiği Durumu:"), html.H5(id='script-status', children="Beklemede")]))],
            className="mb-2"),
    dbc.Row([
        dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                          dbc.Progress(id='cpu-usage', value=0, color="success", style={"height": "20px"},
                                       className="mb-1", label="0%")])),
        dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"),
                          dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"},
                                       className="mb-1", label="0%")]))
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
        html.Div(id='ai-yorum-sonucu',
                 children=[html.P("Yorum ve görüntü almak için yukarıdan bir yapay zeka modeli seçin.")],
                 className="text-center mt-2"),
        html.Div(id='ai-image', children=[], className="text-center mt-2")
    ]))
])

# GRAFİK LAYOUT DÜZELTMESİ: Dört ayrı Div ve bir kontrol callback'i (en sağlam yöntem)
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
        # Her grafik kendi div'i içinde, varsayılan olarak sadece ilki görünür
        html.Div([
            html.Div(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), id='container-map-graph',
                     style={'display': 'block'}),
            html.Div(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), id='container-regression-graph',
                     style={'display': 'none'}),
            html.Div(dcc.Graph(id='polar-graph', style={'height': '75vh'}), id='container-polar-graph',
                     style={'display': 'none'}),
            html.Div(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), id='container-time-series-graph',
                     style={'display': 'none'}),
        ])
    ], label="Grafikler", tab_id="tab-graphics"),
    dbc.Tab(
        dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]),
        label="Veri Tablosu", tab_id="tab-datatable"
    )
], id="visualization-tabs-main", active_tab="tab-graphics")

# ==============================================================================
# --- ANA UYGULAMA LAYOUT'U ---
# ==============================================================================
app.layout = html.Div(style={'padding': '20px'}, children=[
    navbar,
    dbc.Row([
        dbc.Col([control_panel, html.Br(), stats_panel, html.Br(), system_card, html.Br(), export_card], md=4,
                className="mb-3"),
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
    dbc.Modal([dbc.ModalHeader(dbc.ModalTitle(id="modal-title")), dbc.ModalBody(id="modal-body")],
              id="cluster-info-modal", is_open=False, centered=True),
    dcc.Interval(id='interval-component-main', interval=2500, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])


# ==============================================================================
# --- YARDIMCI FONKSİYONLAR ---
# ==============================================================================
def is_process_running(pid):
    if pid is None: return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def get_latest_scan():
    # MIGRATE SORUNU İÇİN DÜZELTME
    from scanner.models import Scan
    try:
        running_scan = Scan.objects.filter(status='RUNNING').order_by('-start_time').first()
        if running_scan: return running_scan
        return Scan.objects.order_by('-start_time').first()
    except Exception:
        return None


def add_scan_rays(fig, df):
    if df.empty or not all(c in df.columns for c in ['x_cm', 'y_cm']): return
    x, y = [], []
    for _, r in df.iterrows(): x.extend([0, r['y_cm'], None]); y.extend([0, r['x_cm'], None])
    fig.add_trace(go.Scatter(x=x, y=y, mode='lines', line=dict(color='rgba(255,100,100,0.4)', dash='dash', width=1),
                             showlegend=False))


def add_sector_area(fig, df):
    if df.empty or not all(c in df.columns for c in ['x_cm', 'y_cm']): return
    fig.add_trace(go.Scatter(x=[0, *df['y_cm'], 0], y=[0, *df['x_cm'], 0], mode='lines', fill='toself',
                             fillcolor='rgba(255,0,0,0.15)', line=dict(color='rgba(255,0,0,0.4)'),
                             name='Taranan Sektör'))


def add_sensor_position(fig):
    fig.add_trace(
        go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))


# ... (Diğer yardımcı fonksiyonlarınız buraya eklenebilir)

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
    except Exception as e:
        return f"Gemini'den metin yanıtı alınırken bir hata oluştu: {e}"


def image_generate(prompt_text):
    if not GOOGLE_GENAI_AVAILABLE: return ["Hata: Google GenerativeAI kütüphanesi yüklenemedi."]
    if not google_api_key: return ["Hata: `GOOGLE_API_KEY` ayarlanmamış."]
    if not prompt_text: return ["Görüntü oluşturmak için bir metin istemi (prompt) gerekli."]
    try:
        genai.configure(api_key=google_api_key)
        model = genai.GenerativeModel(model_name="models/gemini-2.0-flash-preview-image-generation")

        # GÖRÜNTÜ OLUŞTURMA DÜZELTMESİ: Kısa ve net prompt
        short_prompt = prompt_text.split('.')[0]
        full_prompt = f"Bir odaya yerleştirilmiş nesnelerin fotorealistik, yukarıdan aşağıya (top-down view) radar tarama haritası: {short_prompt}"

        config = GenerationConfig(response_modalities=['IMAGE', 'TEXT'])
        response = model.generate_content(full_prompt, generation_config=config)

        image_urls = []
        for part in response.candidates[0].content.parts:
            image_data_part = None
            if hasattr(part, 'mime_type') and part.mime_type.startswith("image/"):
                image_data_part = part
            elif hasattr(part, 'inline_data') and hasattr(part.inline_data,
                                                          'mime_type') and part.inline_data.mime_type.startswith(
                    "image/"):
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

# GRAFİK LAYOUT DÜZELTMESİ: Grafik görünürlüğünü kontrol eden callback
@app.callback(
    Output('container-map-graph', 'style'),
    Output('container-regression-graph', 'style'),
    Output('container-polar-graph', 'style'),
    Output('container-time-series-graph', 'style'),
    Input('graph-selector-dropdown', 'value')
)
def update_graph_visibility(selected_graph):
    styles = [{'display': 'none'}] * 4
    if selected_graph == 'map':
        styles[0] = {'display': 'block'}
    elif selected_graph == 'regression':
        styles[1] = {'display': 'block'}
    elif selected_graph == 'polar':
        styles[2] = {'display': 'block'}
    elif selected_graph == 'time':
        styles[3] = {'display': 'block'}
    return tuple(styles)


@app.callback(
    [
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
    # MIGRATE SORUNU İÇİN DÜZELTME
    from scanner.models import ScanPoint

    scan = get_latest_scan()
    map_fig, reg_fig, pol_fig, ts_fig = go.Figure(), go.Figure(), go.Figure(), go.Figure()
    est_text, store_data = "Tarama verisi bekleniyor...", no_update

    if scan:
        points_qs = ScanPoint.objects.filter(scan=scan).values('x_cm', 'y_cm', 'derece', 'mesafe_cm', 'timestamp')
        if points_qs.exists():
            df_pts = pd.DataFrame(list(points_qs))
            df_val = df_pts[(df_pts['mesafe_cm'] > 0.1) & (df_pts['mesafe_cm'] < 400.0)].copy()

            if len(df_val) >= 5:
                # Tüm analiz ve çizim fonksiyonları burada çağrılır
                est_cart, df_clus = analyze_environment_shape(map_fig, df_val.copy())
                store_data = df_clus.to_json(orient='split')
                add_scan_rays(map_fig, df_val)
                add_sector_area(map_fig, df_val)

                line_data, est_polar = analyze_polar_regression(df_val)
                reg_fig.add_trace(
                    go.Scatter(x=df_val['derece'], y=df_val['mesafe_cm'], mode='markers', name='Noktalar'))
                if line_data:
                    reg_fig.add_trace(
                        go.Scatter(x=line_data['x'], y=line_data['y'], mode='lines', name='Regresyon Çizgisi',
                                   line=dict(color='red', width=3)))

                update_polar_graph(pol_fig, df_val)
                # Kendi kodunuzdaki diğer yardımcı fonksiyonları (update_time_series_graph vb.) burada kullanın

                clear_path = find_clearest_path(df_val)
                shape_estimation = estimate_geometric_shape(df_val)
                est_text = html.Div([
                    html.P(shape_estimation, className="fw-bold"), html.Hr(),
                    html.P(clear_path, className="fw-bold text-primary"), html.Hr(),
                    html.P(f"Kümeleme: {est_cart}"), html.Hr(),
                    html.P(f"Regresyon: {est_polar}")
                ])

    for fig in [map_fig, reg_fig, pol_fig, ts_fig]:
        add_sensor_position(fig)
        fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                          margin=dict(l=40, r=40, t=80, b=40))

    map_fig.update_layout(title_text='Ortamın 2D Haritası', xaxis_title="Yatay Mesafe (cm)",
                          yaxis_title="Dikey Mesafe (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1)
    reg_fig.update_layout(title_text='Açıya Göre Mesafe Regresyonu', xaxis_title="Tarama Açısı (Derece)",
                          yaxis_title="Mesafe (cm)")
    pol_fig.update_layout(title_text='Polar Grafik')
    ts_fig.update_layout(title_text='Zaman Serisi - Mesafe')

    return map_fig, reg_fig, pol_fig, ts_fig, est_text, store_data


@app.callback(
    [Output('ai-yorum-sonucu', 'children'), Output('ai-image', 'children')],
    [Input('ai-model-dropdown', 'value')],
    prevent_initial_call=True
)
def yorumla_model_secimi(selected_model_value):
    # MIGRATE SORUNU İÇİN DÜZELTME
    from scanner.models import Scan

    if not selected_model_value:
        return html.Div("Yorum için bir model seçin.", className="text-center"), no_update

    scan = get_latest_scan()
    if not scan:
        return dbc.Alert("Analiz edilecek bir tarama bulunamadı.", color="warning", duration=4000), no_update

    points_qs = scan.points.all().values('derece', 'mesafe_cm')
    if not points_qs:
        return dbc.Alert("Yorumlanacak tarama verisi bulunamadı.", color="warning", duration=4000), no_update

    df_data_for_ai = pd.DataFrame(list(points_qs))
    if len(df_data_for_ai) > 500:
        df_data_for_ai = df_data_for_ai.sample(n=500, random_state=1)

    print(f"Scan ID {scan.id} için AI yorumu ve görüntü üretiliyor (Model: {selected_model_value})...")
    yorum_text_from_ai = yorumla_tablo_verisi_gemini(df_data_for_ai, selected_model_value)

    if "Hata:" in yorum_text_from_ai or "hata oluştu" in yorum_text_from_ai:
        return dbc.Alert(yorum_text_from_ai, color="danger"), no_update

    base64_images = image_generate(yorum_text_from_ai)

    image_components = []
    if base64_images and not base64_images[0].startswith("Hata"):
        for img_data in base64_images:
            image_components.append(html.Img(src=img_data,
                                             style={'maxWidth': '100%', 'height': 'auto', 'borderRadius': '10px',
                                                    'marginTop': '10px'}))
    else:
        error_msg = base64_images[0] if base64_images else "Bilinmeyen görüntü hatası"
        image_components = [dbc.Alert(error_msg, color="warning")]
        print(f"Görüntü üretilemedi: {error_msg}")

    try:
        scan_obj = Scan.objects.get(id=scan.id)
        scan_obj.ai_commentary = yorum_text_from_ai
        scan_obj.save(update_fields=['ai_commentary'])
        print(f"Scan ID {scan.id} için yeni AI yorumu veritabanına kaydedildi.")
    except Exception as e_db_save:
        print(f"DB Kayıt Hatası: {e_db_save}")

    return (
        dbc.Alert(dcc.Markdown(yorum_text_from_ai, dangerously_allow_html=True, link_target="_blank"), color="success"),
        html.Div(image_components)
    )

# Kalan diğer tüm callback'lerinizi (datatable, export, sistem durumu vb.)
# kendi çalışan kodunuzdan buraya ekleyebilirsiniz.
# Önemli olan, veritabanı modellerini (Scan, ScanPoint)
# sadece ilgili fonksiyonların içinde import etmektir.