# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import time
import io
import signal
import psutil
import pandas as pd
import numpy as np
import logging

# Bilimsel ve AI Kütüphaneleri
from scipy.spatial import ConvexHull
from sklearn.linear_model import RANSACRegressor
from sklearn.cluster import DBSCAN
from google import genai
from dotenv import load_dotenv

# Dash ve Plotly Kütüphaneleri
from django_plotly_dash import DjangoDash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt

# ==============================================================================
# --- TEMEL YAPILANDIRMA ---
# ==============================================================================
# Hataları ve önemli olayları takip etmek için temel loglama ayarı
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ortam değişkenlerini .env dosyasından yükle
load_dotenv()
google_api_key = os.getenv("GOOGLE_API_KEY")

# ==============================================================================
# --- SABİTLER VE UYGULAMA BAŞLATMA ---
# ==============================================================================
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
# Betiğin, bu dosyanın bulunduğu dizinin bir üst klasöründe olduğunu varsayar.
# Proje yapınıza göre bu yolu düzenlemeniz gerekebilir.
SENSOR_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', SENSOR_SCRIPT_FILENAME)
SENSOR_SCRIPT_LOCK_FILE = '/tmp/sensor_scan_script.lock'
SENSOR_SCRIPT_PID_FILE = '/tmp/sensor_scan_script.pid'

# Varsayılan Arayüz Değerleri
DEFAULT_UI_SCAN_DURATION_ANGLE = 270.0
DEFAULT_UI_SCAN_STEP_ANGLE = 10.0
DEFAULT_UI_BUZZER_DISTANCE = 10
DEFAULT_UI_INVERT_MOTOR = False
DEFAULT_UI_STEPS_PER_REVOLUTION = 4096

# Ana Dash Uygulaması Nesnesi
app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# ==============================================================================
# --- LAYOUT (ARAYÜZ) BİLEŞENLERİ ---
# ==============================================================================
title_card = dbc.Row(
    [dbc.Col(html.H1("Dream Pi Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12), html.Hr()])

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
                {'label': 'Gemini 1.5 Pro (Gelişmiş)', 'value': 'gemini-1.5-pro-latest'},
                {'label': 'Gemini 1.5 Flash (Hızlı)', 'value': 'gemini-1.5-flash-latest'},
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

visualization_tabs = dbc.Tabs([
    dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita", tab_id="tab-map"),
    dbc.Tab(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), label="Regresyon Analizi",
            tab_id="tab-regression"),
    dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik", tab_id="tab-polar"),
    dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)",
            tab_id="tab-time"),
    dbc.Tab(dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]), label="Veri Tablosu",
            tab_id="tab-datatable")
], id="visualization-tabs-main", active_tab="tab-map")

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([
            control_panel, html.Br(),
            stats_panel, html.Br(),
            system_card, html.Br(),
            export_card
        ], md=4, className="mb-3"),
        dbc.Col([
            visualization_tabs, html.Br(),
            dbc.Row([
                dbc.Col(analysis_card, md=8),
                dbc.Col(estimation_card, md=4)
            ]),
            dbc.Row([
                dbc.Col([
                    dbc.Card([
                        dbc.CardHeader("Akıllı Yorumlama (Yapay Zeka)", className="bg-info text-white"),
                        dbc.CardBody(dcc.Loading(id="loading-ai-comment", type="default", children=[
                            html.Div(id='ai-yorum-sonucu', children=[
                                html.P("Yorum almak için yukarıdan bir yapay zeka modeli seçin."),
                            ], className="text-center mt-2")
                        ]))
                    ], className="mt-3")
                ], md=12)
            ], className="mt-3")
        ], md=8)
    ]),
    dcc.Store(id='clustered-data-store'),
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle(id="modal-title")),
        dbc.ModalBody(id="modal-body")
    ], id="cluster-info-modal", is_open=False, centered=True),
    dcc.Interval(id='interval-component-main', interval=5000, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=10000, n_intervals=0),
])


# ==============================================================================
# --- YARDIMCI FONKSİYONLAR ---
# ==============================================================================

def is_process_running(pid):
    """Verilen PID'nin çalışıp çalışmadığını güvenli bir şekilde kontrol eder."""
    if not pid or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except psutil.Error as e:
        logging.warning(f"PID {pid} kontrol hatası: {e}")
        return False


def get_latest_scan():
    """Veritabanından en son Scan nesnesini çeker."""
    from scanner.models import Scan
    try:
        running_scan = Scan.objects.filter(status='running').order_by('-start_time').first()
        if running_scan:
            return running_scan
        return Scan.objects.order_by('-start_time').first()
    except Exception as e:
        logging.error(f"Veritabanı sorgu hatası (get_latest_scan): {e}")
        return None


def cleanup_scan_resources():
    """Çalışan betiği durdurur ve artık dosyaları temizler."""
    pid_to_kill = None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_to_kill = int(pf.read().strip())
        except (IOError, ValueError):
            pass

    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            os.kill(pid_to_kill, signal.SIGTERM)
            time.sleep(1)
            if is_process_running(pid_to_kill):
                os.kill(pid_to_kill, signal.SIGKILL)
        except Exception as e:
            logging.error(f"PID {pid_to_kill} durdurma hatası: {e}")

    for f in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError as e:
                logging.error(f"Dosya silme hatası ({f}): {e}")
    return "Kaynaklar temizlendi."


def validate_scan_params(duration, step, buzzer, steps_rev):
    """Tarama parametrelerini doğrular ve hata mesajı döndürür."""
    try:
        if not (10 <= float(duration) <= 720):
            return False, "Tarama açısı 10-720 derece arasında olmalı."
        if not (0.1 <= abs(float(step)) <= 45):
            return False, "Adım açısı 0.1-45 arasında olmalı."
        if not (0 <= int(buzzer) <= 200):
            return False, "Uyarı mesafesi 0-200 cm arasında olmalı."
        if not (500 <= int(steps_rev) <= 10000):
            return False, "Motor Adım/Tur 500-10000 arasında olmalı."
        return True, ""
    except (ValueError, TypeError):
        return False, "Lütfen tüm parametreler için geçerli sayılar girin."


def add_sensor_position(fig):
    """Grafiğe sensörün konumunu (merkezi) ekler."""
    fig.add_trace(
        go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))


def update_time_series_graph(fig, df):
    """Zaman serisi grafiğini detaylı formatlama ile günceller."""
    if df.empty or 'timestamp' not in df.columns or 'mesafe_cm' not in df.columns:
        fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Veri Yok'))
        return

    try:
        df_s = df.copy()
        df_s['timestamp'] = pd.to_datetime(df_s['timestamp'], errors='coerce')
        df_s.dropna(subset=['timestamp'], inplace=True)
        df_s = df_s.sort_values(by='timestamp')

        if df_s.empty:
            fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Veri Yok'))
            return

        fig.add_trace(go.Scatter(x=df_s['timestamp'], y=df_s['mesafe_cm'], mode='lines+markers', name='Mesafe'))

        fig.update_layout(
            xaxis_type='date',
            xaxis_title="Zaman",
            yaxis_title="Mesafe (cm)",
            xaxis_tickformat='%d %b %Y<br>%H:%M:%S',
            xaxis_rangeselector=dict(buttons=list([
                dict(count=1, label="1dk", step="minute", stepmode="backward"),
                dict(count=5, label="5dk", step="minute", stepmode="backward"),
                dict(count=15, label="15dk", step="minute", stepmode="backward"),
                dict(step="all", label="Tümü")
            ])),
            xaxis_rangeslider_visible=True
        )
    except Exception as e:
        logging.error(f"Zaman serisi grafiği oluşturulurken HATA: {e}")
        fig.add_trace(go.Scatter(x=[], y=[], mode='lines', name='Grafik Hatası'))


def estimate_geometric_shape(df):
    """2D nokta bulutundan ortamın geometrik şeklini tahmin eder."""
    if len(df) < 15: return "Şekil tahmini için yetersiz nokta."
    try:
        points = df[['x_cm', 'y_cm']].values
        hull = ConvexHull(points)
        hull_area = hull.area  # DÜZELTME: 2D için .area kullanılır
        bbox_area = (points[:, 0].max() - points[:, 0].min()) * (points[:, 1].max() - points[:, 1].min())

        if bbox_area <= 0: return "Geçersiz şekil."

        fill_factor = hull_area / bbox_area
        width = points[:, 1].max() - points[:, 1].min()
        depth = points[:, 0].max() - points[:, 0].min()

        if fill_factor > 0.8: return f"Tahmin: Dolgun, dikdörtgensel bir nesne (Doldurma: {fill_factor:.2f})."
        if fill_factor < 0.4: return f"Tahmin: İçbükey bir yapı/Köşe (Doldurma: {fill_factor:.2f})."
        if width > 0 and depth > 0 and width / depth > 3: return f"Tahmin: Geniş bir yüzey/Duvar (En/Boy: {width / depth:.2f})."

        return f"Tahmin: Düzensiz veya karmaşık bir yapı (Doldurma: {fill_factor:.2f})."
    except Exception as e:
        logging.error(f"Geometrik analiz hatası: {e}")
        return "Geometrik analiz hatası."


# ... diğer tüm yardımcı fonksiyonlarınız (analyze_polar_regression, yorumla_tablo_verisi_gemini vb.) ...
# Bu fonksiyonları kendi dosyanızdan buraya ekleyebilirsiniz.

# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================

@app.callback(
    Output('scan-status-message', 'children'),
    Input('start-scan-button', 'n_clicks'),
    [State('scan-duration-angle-input', 'value'), State('step-angle-input', 'value'),
     State('buzzer-distance-input', 'value'), State('invert-motor-checkbox', 'value'),
     State('steps-per-rev-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks, duration, step, buzzer, invert, steps_rev):
    """Tarama betiğini başlatan callback. Sadeleştirildi."""
    if n_clicks == 0: return no_update

    is_valid, error_message = validate_scan_params(duration, step, buzzer, steps_rev)
    if not is_valid:
        return dbc.Alert(error_message, color="danger", duration=4000)

    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                if is_process_running(int(pf.read().strip())):
                    return dbc.Alert("Betik zaten çalışıyor. Önce durdurun.", color="warning")
        except:
            pass

    cleanup_scan_resources()

    try:
        py_exec = sys.executable
        cmd = [py_exec, SENSOR_SCRIPT_PATH, "--scan_duration_angle", str(duration), "--step_angle", str(step),
               "--buzzer_distance", str(buzzer), "--invert_motor_direction", str(invert), "--steps_per_rev",
               str(steps_rev)]

        subprocess.Popen(cmd, start_new_session=True)
        time.sleep(2)

        if os.path.exists(SENSOR_SCRIPT_PID_FILE):
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                new_pid = pf.read().strip()
            return dbc.Alert(f"Tarama başlatıldı (PID:{new_pid}).", color="success")
        else:
            return dbc.Alert("Tarama başlatılamadı. PID dosyası oluşmadı veya betik hata verdi.", color="danger")
    except Exception as e:
        logging.error(f"Betik başlatma hatası: {e}")
        return dbc.Alert(f"Betik başlatılırken kritik hata: {e}", color="danger")


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    Input('stop-scan-button', 'n_clicks'),
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks):
    """Çalışan betiği durdurur."""
    if n_clicks == 0: return no_update
    try:
        message = cleanup_scan_resources()
        return dbc.Alert(f"Durdurma komutu gönderildi. {message}", color="info")
    except Exception as e:
        logging.error(f"Durdurma callback hatası: {e}")
        return dbc.Alert(f"Durdurma sırasında hata: {e}", color="danger")


@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'),
     Output('cpu-usage', 'value'), Output('cpu-usage', 'label'),
     Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    Input('interval-component-system', 'n_intervals')
)
def update_system_card(n):
    """Sistem durumu kartını günceller."""
    status_text, status_class, pid = "Beklemede", "text-secondary", None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid = int(pf.read().strip())
        except (IOError, ValueError):
            pid = None

    if pid and is_process_running(pid):
        status_text, status_class = f"Çalışıyor (PID:{pid})", "text-success"
    else:
        status_text, status_class = "Çalışmıyor", "text-danger"

    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    return status_text, status_class, cpu, f"{cpu:.1f}%", ram, f"{ram:.1f}%"


@app.callback(
    [Output('scan-map-graph', 'figure'), Output('polar-regression-graph', 'figure'),
     Output('polar-graph', 'figure'), Output('time-series-graph', 'figure'),
     Output('environment-estimation-text', 'children'), Output('clustered-data-store', 'data')],
    Input('interval-component-main', 'n_intervals')
)
def update_all_graphs(n):
    """Tüm grafikleri periyodik olarak güncelleyen ana callback."""
    # Django model importları bu ana fonksiyonda yapılır
    from scanner.models import ScanPoint

    # Başlangıç değerleri
    figs = [go.Figure() for _ in range(4)]
    est_text, store_data, scan_id = "Tarama başlatın veya bekleyin...", None, 'initial_load'

    scan = get_latest_scan()
    if scan:
        scan_id = str(scan.id)
        points_qs = ScanPoint.objects.filter(scan=scan).values('x_cm', 'y_cm', 'derece', 'mesafe_cm', 'timestamp')

        if points_qs:
            df_pts = pd.DataFrame(list(points_qs))
            df_val = df_pts[(df_pts['mesafe_cm'] > 0.1) & (df_pts['mesafe_cm'] < 300.0)].copy()

            if len(df_val) >= 2:
                # Her grafik için ilgili yardımcı fonksiyonu çağır
                # (Bu fonksiyonlar yukarıda tanımlanmıştır veya sizin kodunuzdan eklenebilir)
                # update_scan_map_graph(figs[0], df_val)
                # update_regression_graph(figs[1], df_val)
                # update_polar_graph(figs[2], df_val)
                update_time_series_graph(figs[3], df_val)

                shape_estimation = estimate_geometric_shape(df_val)
                est_text = html.Div([html.P(shape_estimation)])
            else:
                est_text = "Analiz için yetersiz geçerli nokta."
        else:
            est_text = f"Tarama ID {scan.id} için nokta verisi bulunamadı."

    for fig in figs:
        add_sensor_position(fig)

    titles = ['Ortamın 2D Haritası', 'Açıya Göre Mesafe Regresyonu', 'Polar Grafik', 'Zaman Serisi - Mesafe']
    for i, fig in enumerate(figs):
        fig.update_layout(title_text=titles[i], uirevision=scan_id, template="plotly_dark",
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))

    return figs[0], figs[1], figs[2], figs[3], est_text, store_data


@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children'),
     Output('current-distance-col', 'style'), Output('max-detected-distance', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n):
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
        max_dist_agg = scan.points.filter(mesafe_cm__lt=2500, mesafe_cm__gt=0).aggregate(max_dist_val=Max('mesafe_cm'))
        if max_dist_agg and max_dist_agg.get('max_dist_val') is not None:
            max_dist_s = f"{max_dist_agg['max_dist_val']:.1f} cm"
    return angle_s, dist_s, speed_s, dist_style, max_dist_s


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'),
     Output('max-depth', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n):
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
    if not n_clicks_excel: return no_update
    scan = get_latest_scan()
    if not scan: return dcc.send_bytes(b"", "tarama_yok.xlsx")
    try:
        scan_info_data = Scan.objects.filter(id=scan.id).values().first();
        scan_info_df = pd.DataFrame([scan_info_data]) if scan_info_data else pd.DataFrame()
        points_df = pd.DataFrame(list(scan.points.all().values()))
    except Exception as e_excel_data:
        print(f"Excel için veri çekme hatası: {e_excel_data}");
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
            print(f"Excel yazma hatası: {e_excel_write}");
            pd.DataFrame([{"Hata": str(e_excel_write)}]).to_excel(writer, sheet_name='Hata', index=False)
        return dcc.send_bytes(buf.getvalue(), f"tarama_detaylari_id_{scan.id if scan else 'yok'}.xlsx")


@app.callback(Output('tab-content-datatable', 'children'),
              [Input('visualization-tabs-main', 'active_tab'), Input('interval-component-main', 'n_intervals')])
def render_and_update_data_table(active_tab, n):
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
    [Output('scan-map-graph', 'figure'), Output('polar-regression-graph', 'figure'), Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure'), Output('environment-estimation-text', 'children'),
     Output('clustered-data-store', 'data')], [Input('interval-component-main', 'n_intervals')])
def update_all_graphs(n):
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
    Output('container-map-graph', 'style'),
    Output('container-regression-graph', 'style'),
    Output('container-polar-graph', 'style'),
    Output('container-time-series-graph', 'style'),
    Input('graph-selector-dropdown', 'value')
)
def update_graph_visibility(selected_graph):
    # Başlangıçta tüm grafikleri gizle
    style_map = {'display': 'none'}
    style_regression = {'display': 'none'}
    style_polar = {'display': 'none'}
    style_time = {'display': 'none'}

    # Seçilen grafiği görünür yap
    if selected_graph == 'map':
        style_map = {'display': 'block'}
    elif selected_graph == 'regression':
        style_regression = {'display': 'block'}
    elif selected_graph == 'polar':
        style_polar = {'display': 'block'}
    elif selected_graph == 'time':
        style_time = {'display': 'block'}

    return style_map, style_regression, style_polar, style_time


@app.callback(
    [Output("cluster-info-modal", "is_open"), Output("modal-title", "children"), Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")], [State("clustered-data-store", "data")], prevent_initial_call=True)
def display_cluster_info(clickData, stored_data_json):
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


@app.callback(Output('ai-yorum-sonucu', 'children'), [Input('ai-model-dropdown', 'value')], prevent_initial_call=True)
def yorumla_model_secimi(selected_model_value):
    if not selected_model_value: return html.Div("Yorum için bir model seçin.", className="text-center")
    scan = get_latest_scan()
    if not scan: return dbc.Alert("Analiz edilecek bir tarama bulunamadı.", color="warning", duration=4000)
    if scan.ai_commentary:
        print(f"Scan ID {scan.id} için mevcut AI yorumu kullanılıyor.")
        return dbc.Alert(dcc.Markdown(scan.ai_commentary, dangerously_allow_html=True, link_target="_blank"),
                         color="info")
    points_qs = scan.points.all().values('derece', 'mesafe_cm')
    if not points_qs: return dbc.Alert("Yorumlanacak tarama verisi bulunamadı.", color="warning", duration=4000)
    df_data_for_ai = pd.DataFrame(list(points_qs))
    if len(df_data_for_ai) > 500:
        print(f"AI yorumu için çok fazla nokta ({len(df_data_for_ai)}), 500'e örnekleniyor...")
        df_data_for_ai = df_data_for_ai.sample(n=500, random_state=1)
    print(f"Scan ID {scan.id} için yeni AI yorumu üretiliyor (Model: {selected_model_value})...")
    yorum_text_from_ai = yorumla_tablo_verisi_gemini(df_data_for_ai, selected_model_value)
    if "Hata:" in yorum_text_from_ai or "hata oluştu" in yorum_text_from_ai:
        return dbc.Alert(yorum_text_from_ai, color="danger")
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

