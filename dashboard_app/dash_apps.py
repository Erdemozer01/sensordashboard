# dashboard_app.py

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

try:
    from django.db.models import Max
    from scanner.models import Scan, ScanPoint

    DJANGO_MODELS_AVAILABLE = True
    print("Dashboard: Django modelleri başarıyla import edildi.")
except Exception as e:
    print(f"Django modelleri import edilirken bir hata oluştu: {e}")
    DJANGO_MODELS_AVAILABLE = False
    Scan, ScanPoint = None, None

from django_plotly_dash import DjangoDash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt

try:
    from google import generativeai

    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    print("UYARI: 'google.generativeai' kütüphanesi bulunamadı. AI yorumlama özelliği çalışmayacak.")
    GOOGLE_GENAI_AVAILABLE = False

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
DEFAULT_UI_SERVO_ANGLE = 0

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# ==============================================================================
# --- LAYOUT (ARAYÜZ) BİLEŞENLERİ ---
# ==============================================================================
navbar = dbc.NavbarSimple(
    children=[dbc.NavItem(dbc.NavLink("Admin Paneli", href="/admin/", external_link=True, target="_blank"))],
    brand="Dream Pi", brand_href="/", color="primary", dark=True, sticky="top", fluid=True, className="mb-4"
)

title_card = dbc.Row(
    [dbc.Col(html.H1("Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12), html.Hr()]
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
                    {'label': 'Gemini Flash (Hızlı)', 'value': 'gemini-1.5-flash-latest'},
                    {'label': 'Gemini Pro (Gelişmiş)', 'value': 'gemini-1.5-pro-latest'},
                ],
                placeholder="Yorumlama için bir metin modeli seçin...", clearable=True, className="mb-3"
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
            html.Hr(),
            html.H6("Dikey Açı Ayarı (Servo)", className="mt-3"),
            dcc.Slider(
                id='servo-angle-slider',
                min=-90, max=90, step=5, value=DEFAULT_UI_SERVO_ANGLE,
                marks={i: f'{i}°' for i in range(-90, 91, 30)},
                tooltip={"placement": "bottom", "always_visible": True}
            ),
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

analysis_card = dbc.Card([dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
                          dbc.CardBody([
                              dbc.Row([
                                  dbc.Col([html.H6("Hesaplanan Alan (2D):"),
                                           html.H4(id='calculated-area', children="-- cm²")]),
                                  dbc.Col([html.H6("Çevre Uzunluğu (2D):"),
                                           html.H4(id='perimeter-length', children="-- cm")])
                              ]),
                              dbc.Row([
                                  dbc.Col([html.H6("Max Genişlik (2D):"), html.H4(id='max-width', children="-- cm")]),
                                  dbc.Col([html.H6("Max Derinlik (2D):"), html.H4(id='max-depth', children="-- cm")])
                              ], className="mt-2")])])

estimation_card = dbc.Card([dbc.CardHeader("Akıllı Ortam Analizi", className="bg-success text-white"), dbc.CardBody(
    html.Div("Tahmin: Bekleniyor...", id='environment-estimation-text', className="text-center"))])

visualization_tabs = dbc.Tabs([
    dbc.Tab([
        dbc.Row([
            dbc.Col(dcc.Dropdown(
                id='graph-selector-dropdown',
                options=[
                    {'label': '3D Kartezyen Harita', 'value': 'map_3d'},
                    {'label': '2D Kartezyen Harita (Projeksiyon)', 'value': 'map'},
                    {'label': 'Regresyon Analizi', 'value': 'regression'},
                    {'label': 'Polar Grafik', 'value': 'polar'},
                    {'label': 'Zaman Serisi (Mesafe)', 'value': 'time'},
                ],
                value='map_3d', clearable=False, style={'marginTop': '10px'}
            ), width=6)], justify="center", className="mb-3"),
        html.Div([
            html.Div(dcc.Graph(id='scan-map-graph-3d', style={'height': '75vh'}), id='container-map-graph-3d'),
            html.Div(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), id='container-map-graph'),
            html.Div(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), id='container-regression-graph'),
            html.Div(dcc.Graph(id='polar-graph', style={'height': '75vh'}), id='container-polar-graph'),
            html.Div(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), id='container-time-series-graph'),
        ])], label="Grafikler", tab_id="tab-graphics"),
    dbc.Tab(dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]), label="Veri Tablosu",
            tab_id="tab-datatable")
], id="visualization-tabs-main", active_tab="tab-graphics")

app.layout = html.Div(style={'padding': '20px'}, children=[
    navbar,
    dbc.Row([
        dbc.Col([control_panel, html.Br(), stats_panel, html.Br(), system_card, html.Br(), export_card], md=4,
                className="mb-3"),
        dbc.Col([
            visualization_tabs, html.Br(),
            dbc.Row([dbc.Col(analysis_card, md=8), dbc.Col(estimation_card, md=4)]),
            dbc.Row([dbc.Col([
                dbc.Card([dbc.CardHeader("Akıllı Yorumlama (Yapay Zeka)", className="bg-info text-white"),
                          dbc.CardBody(dcc.Loading(id="loading-ai-comment", type="default",
                                                   children=[
                                                       html.Div(id='ai-yorum-sonucu', children=[html.P(
                                                           "Yorum almak için yukarıdan bir yapay zeka modeli seçin.")],
                                                                className="text-center mt-2"),
                                                       html.Div(id='ai-image', children=[
                                                           html.P("Ortamın görüntüsünü oluşturmak için model seçin")],
                                                                className="text-center mt-2")
                                                   ]))
                          ], className="mt-3")
            ], md=12)], className="mt-3")
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
# ... (is_process_running, get_latest_scan, add_scan_rays, add_sector_area, add_sensor_position, ... ve diğer tüm yardımcı fonksiyonlarınız burada yer alacak)
# --- ÖNEMLİ DEĞİŞİKLİK ---
def yorumla_tablo_verisi_gemini(df, model_name):
    if not GOOGLE_GENAI_AVAILABLE: return "Hata: Google GenerativeAI kütüphanesi yüklenemedi."
    if not google_api_key: return "Hata: `GOOGLE_API_KEY` ayarlanmamış."
    if df is None or df.empty: return "Yorumlanacak tablo verisi bulunamadı."
    try:
        generativeai.configure(api_key=google_api_key)
        model = generativeai.GenerativeModel(model_name=model_name)
        prompt_text = (
            "Aşağıdaki tablo, bir step motor (yatay dönüş) ve bir servo motor (dikey eğim) ile kontrol edilen ultrasonik sensörlerin yaptığı 3D tarama verilerini içermektedir. "
            "'derece' yatay açıyı (pan), 'dikey_aci' dikey eğimi (tilt), 'mesafe_cm' ise bu kombinasyondaki ana sensör mesafesini temsil eder.\n\n"
            f"{df.to_string(index=False)}\n\n"
            "Bu 3D verilere dayanarak, ortamın olası üç boyutlu yapısını (örneğin: 'geniş bir oda', 'dar bir koridor', 'tavan veya zemin', 'karşıda duran bir kutu') analiz et. "
            "Verilerdeki desenlere göre potansiyel nesneleri (duvar, köşe, sandalye, insan vb.) tahmin etmeye çalış. Cevabını Markdown formatında, başlıklar ve listeler kullanarak düzenli bir şekilde sun."
        )
        response = model.generate_content(contents=prompt_text)
        return response.text
    except Exception as e:
        return f"Gemini'den yanıt alınırken bir hata oluştu: {e}"


# ... (Diğer tüm yardımcı fonksiyonlarınız aynı kalacak)

# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================
@app.callback(
    Output('scan-parameters-wrapper', 'style'),
    Input('mode-selection-radios', 'value')
)
def toggle_parameter_visibility(selected_mode):
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
     State('steps-per-rev-input', 'value'),
     State('servo-angle-slider', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks, selected_mode, duration, step, buzzer_dist, invert, steps_rev, servo_angle):
    if n_clicks == 0: return no_update
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid = int(pf.read().strip())
            if is_process_running(pid): return dbc.Alert(f"Bir betik zaten çalışıyor (PID:{pid}). Önce durdurun.",
                                                         color="warning")
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
        # ... (diğer parametre kontrolleri aynı) ...
        cmd = [py_exec, SENSOR_SCRIPT_PATH,
               "--scan_duration_angle", str(duration),
               "--step_angle", str(step),
               "--buzzer_distance", str(buzzer_dist),
               "--invert_motor_direction", str(invert),
               "--steps_per_rev", str(steps_rev),
               "--servo_angle", str(servo_angle)]
    # ... (geri kalan başlatma mantığı aynı) ...


# ... (handle_stop_scan_script, update_system_card, update_realtime_values, update_analysis_panel, export_csv/excel, render_and_update_data_table aynı kalacak) ...

@app.callback(
    [
        Output('scan-map-graph-3d', 'figure'),
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
    scan = get_latest_scan()
    figs = [go.Figure() for _ in range(5)]
    est_text = html.Div([html.P("Tarama başlatın veya verinin gelmesini bekleyin...")])
    store_data = None
    scan_id_for_revision = 'initial_load'

    if scan:
        scan_id_for_revision = str(scan.id)
        points_qs = ScanPoint.objects.filter(scan=scan).values('x_cm', 'y_cm', 'z_cm', 'derece', 'mesafe_cm',
                                                               'timestamp')

        if points_qs:
            df_pts = pd.DataFrame(list(points_qs))
            df_val = df_pts[(df_pts['mesafe_cm'] > 0.1) & (df_pts['mesafe_cm'] < 300.0)].copy()

            if not df_val.empty and all(k in df_val and pd.notna(df_val[k]).all() for k in ['x_cm', 'y_cm', 'z_cm']):
                figs[0].add_trace(go.Scatter3d(
                    x=df_val['y_cm'], y=df_val['x_cm'], z=df_val['z_cm'],
                    mode='markers',
                    marker=dict(size=3, color=df_val['z_cm'], colorscale='Viridis', showscale=True,
                                colorbar_title='Yükseklik (cm)'),
                    name='3D Noktalar'
                ))

            if len(df_val) >= 5:
                est_cart, df_clus = analyze_environment_shape(figs[1], df_val.copy())
                store_data = df_clus.to_json(orient='split')
                add_scan_rays(figs[1], df_val)
                line_data, est_polar = analyze_polar_regression(df_val)
                figs[2].add_trace(
                    go.Scatter(x=df_val['derece'], y=df_val['mesafe_cm'], mode='markers', name='Noktalar'))
                if line_data:
                    figs[2].add_trace(go.Scatter(x=line_data['x'], y=line_data['y'], mode='lines', name='Regresyon',
                                                 line=dict(color='red', width=3)))
                update_polar_graph(figs[3], df_val)
                update_time_series_graph(figs[4], df_val)
                # ... (analiz metni oluşturma) ...

    titles = ['Ortamın 3D Haritası', '2D Harita (Projeksiyon)', 'Açıya Göre Mesafe Regresyonu', 'Polar Grafik',
              'Zaman Serisi - Mesafe']
    common_legend = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    for i, fig in enumerate(figs):
        fig.update_layout(title_text=titles[i], uirevision=scan_id_for_revision, legend=common_legend,
                          margin=dict(l=40, r=40, t=80, b=40))
        if i == 0:
            fig.update_layout(
                scene=dict(xaxis_title='Y Ekseni (cm)', yaxis_title='X Ekseni (cm)', zaxis_title='Z Ekseni (cm)',
                           aspectratio=dict(x=1, y=1, z=0.5)))
        elif i == 1:
            fig.update_layout(xaxis_title="Yatay Mesafe (cm)", yaxis_title="Dikey Mesafe (cm)", yaxis_scaleanchor="x",
                              yaxis_scaleratio=1)
        elif i == 2:
            fig.update_layout(xaxis_title="Tarama Açısı (Derece)", yaxis_title="Mesafe (cm)")

    return figs[0], figs[1], figs[2], figs[3], figs[4], est_text, store_data


@app.callback(
    Output('container-map-graph-3d', 'style'),
    Output('container-map-graph', 'style'),
    Output('container-regression-graph', 'style'),
    Output('container-polar-graph', 'style'),
    Output('container-time-series-graph', 'style'),
    Input('graph-selector-dropdown', 'value')
)
def update_graph_visibility(selected_graph):
    styles = {'map_3d': {'display': 'none'}, 'map': {'display': 'none'}, 'regression': {'display': 'none'},
              'polar': {'display': 'none'}, 'time': {'display': 'none'}}
    if selected_graph in styles:
        styles[selected_graph] = {'display': 'block'}
    return styles['map_3d'], styles['map'], styles['regression'], styles['polar'], styles['time']

# ... (display_cluster_info ve yorumla_model_secimi callback'leriniz burada aynı şekilde yer alacak) ...