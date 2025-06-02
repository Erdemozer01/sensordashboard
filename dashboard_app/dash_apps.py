from django_plotly_dash import DjangoDash

from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt  # analyze_environment_shape içinde renk haritası için kullanılıyor

import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time
import io
import signal
import psutil
import numpy as np
from scipy.spatial import ConvexHull
from simplification.cutil import simplify_coords
from sklearn.linear_model import RANSACRegressor
from sklearn.cluster import DBSCAN

# ==============================================================================
# --- SABİTLER VE UYGULAMA BAŞLATMA ---
# ==============================================================================
try:
    # __file__ bu betik Django context'inde çalıştığında tanımlı olmayabilir.
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:
    # Eğer __file__ tanımlı değilse (örneğin interaktif bir shell'de),
    # mevcut çalışma dizinini kök dizin olarak varsayalım.
    PROJECT_ROOT_DIR = os.getcwd()
    # print(f"UYARI: __file__ tanımlı değil. PROJECT_ROOT_DIR şuna ayarlandı: {PROJECT_ROOT_DIR}")

DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_START_ANGLE = 0
DEFAULT_UI_SCAN_END_ANGLE = 180
DEFAULT_UI_SCAN_STEP_ANGLE = 10
DEFAULT_UI_BUZZER_DISTANCE = 10

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# ==============================================================================
# --- LAYOUT (ARAYÜZ) BİLEŞENLERİ ---
# ==============================================================================
title_card = dbc.Row([
    dbc.Col(html.H1("Dream Pi Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12),
    html.Hr(),
])

control_panel = dbc.Card([
    dbc.CardHeader("Tarama Kontrol ve Ayarları", className="bg-primary text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Taramayı Durdur', id='stop-scan-button', n_clicks=0,
                                className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),
        html.Hr(),
        html.H6("Tarama Parametreleri:", className="mt-2"),
        dbc.InputGroup([dbc.InputGroupText("Başl. Açı (°)", style={"width": "120px"}),
                        dbc.Input(id="start-angle-input", type="number", value=DEFAULT_UI_SCAN_START_ANGLE, min=0,
                                  max=180, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Bitiş Açısı (°)", style={"width": "120px"}),
                        dbc.Input(id="end-angle-input", type="number", value=DEFAULT_UI_SCAN_END_ANGLE, min=0, max=180,
                                  step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "120px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Buzzer Mes. (cm)", style={"width": "120px"}),
                        dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE, min=0,
                                  max=200, step=1)], className="mb-2"),
    ])
])

stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
    dbc.CardBody(
        dbc.Row([
            dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=4,
                    className="text-center"),
            dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]),
                    id='current-distance-col', width=4, className="text-center rounded"),
            dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=4,
                    className="text-center")
        ]))
], className="mb-3")

system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
    dbc.CardBody([
        dbc.Row([dbc.Col(html.Div([html.H6("Sensör Durumu:"), html.H5(id='script-status', children="Beklemede")]))],
                className="mb-2"),
        dbc.Row([
            dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                              dbc.Progress(id='cpu-usage', value=0, color="success", style={"height": "20px"},
                                           className="mb-1", label="0%")])),
            dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"),
                              dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"},
                                           className="mb-1", label="0%")]))
        ])])
], className="mb-3")

export_card = dbc.Card([
    dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"),
    dbc.CardBody([
        dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
        dcc.Download(id='download-csv'),
        dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
        dcc.Download(id='download-excel'),
    ])
], className="mb-3")

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
    # Div olarak değiştirildi, birden fazla P içerebilir
])

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
    active_tab="tab-map",
)

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([
            control_panel,
            dbc.Row(html.Div(style={"height": "15px"})),
            stats_panel,
            dbc.Row(html.Div(style={"height": "15px"})),
            system_card,
            dbc.Row(html.Div(style={"height": "15px"})),
            export_card
        ], md=4, className="mb-3"),
        dbc.Col([
            visualization_tabs,
            dbc.Row(html.Div(style={"height": "15px"})),
            dbc.Row([
                dbc.Col(analysis_card, md=8),
                dbc.Col(estimation_card, md=4)
            ])
        ], md=8)
    ]),
    # --- ETKİLEŞİM İÇİN EKLENEN YENİ BİLEŞENLER ---
    dcc.Store(id='clustered-data-store'),  # Tıklanan küme verisini saklamak için
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle(id="modal-title")),
        dbc.ModalBody(id="modal-body"),
    ], id="cluster-info-modal", is_open=False, centered=True),
    # -------------------------------------------
    dcc.Interval(id='interval-component-main', interval=3000, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])


# ==============================================================================
# --- YARDIMCI FONKSİYONLAR ---
# ==============================================================================
def is_process_running(pid):
    """Verilen PID'nin çalışıp çalışmadığını kontrol eder."""
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def get_db_connection():
    """Salt okunur modda SQLite veritabanı bağlantısı döndürür."""
    try:
        if not os.path.exists(DB_PATH):
            return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        return conn, None
    except sqlite3.OperationalError as e:
        return None, f"DB Kilitli/Hata: {e}"
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


def get_latest_scan_id_from_db(conn_param=None):
    """Veritabanından en son tarama ID'sini alır."""
    internal_conn, conn_to_use, latest_id = False, conn_param, None
    if not conn_to_use:
        conn_to_use, error = get_db_connection()
        if error:
            print(f"DB Hatası (get_latest_scan_id): {error}")
            return None
        internal_conn = True
    if conn_to_use:
        try:
            df_scan_running = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if not df_scan_running.empty:
                latest_id = int(df_scan_running['id'].iloc[0])
            else:
                df_scan_last = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1",
                                                 conn_to_use)
                if not df_scan_last.empty:
                    latest_id = int(df_scan_last['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}")
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id


# --- GRAFİK YARDIMCI FONKSİYONLARI ---
def add_scan_rays(fig, df):
    """Grafiğe tarama ışınlarını ekler."""
    x_lines, y_lines = [], []
    for index, row in df.iterrows():
        x_lines.extend([0, row['y_cm'], None])
        y_lines.extend([0, row['x_cm'], None])
    fig.add_trace(go.Scatter(x=x_lines, y=y_lines, mode='lines',
                             line=dict(color='rgba(255, 100, 100, 0.4)', dash='dash', width=1), showlegend=False))


def add_sector_area(fig, df):
    """Grafiğe taranan sektör alanını ekler."""
    poly_x, poly_y = df['y_cm'].tolist(), df['x_cm'].tolist()
    sector_polygon_x, sector_polygon_y = [0] + poly_x, [0] + poly_y
    fig.add_trace(
        go.Scatter(x=sector_polygon_x, y=sector_polygon_y, mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.15)',
                   line=dict(color='rgba(255,0,0,0.4)'), name='Taranan Sektör Alanı'))


def add_sensor_position(fig):
    """Grafiğe sensörün pozisyonunu ekler."""
    fig.add_trace(go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'),
                             name='Sensör Pozisyonu'))


def update_polar_graph(fig, df):
    """Polar grafiği günceller."""
    fig.add_trace(go.Scatterpolar(r=df['mesafe_cm'], theta=df['derece'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 200]), angularaxis=dict(direction="clockwise")))


def update_time_series_graph(fig, df):
    """Zaman serisi grafiğini günceller."""
    df_time_sorted = df.sort_values(by='timestamp')
    datetime_series = pd.to_datetime(df_time_sorted['timestamp'], unit='s')
    fig.add_trace(
        go.Scatter(x=datetime_series, y=df_time_sorted['mesafe_cm'], mode='lines+markers', name='Mesafe (cm)'))
    fig.update_layout(xaxis_title="Zaman", yaxis_title="Mesafe (cm)")


# --- ANALİZ YARDIMCI FONKSİYONLARI (YENİ VE GÜNCELLENMİŞ) ---
def find_clearest_path(df_valid):
    """Verilen veri setindeki en uzak noktayı bularak en açık yolu tespit eder."""
    if df_valid.empty:
        return "En açık yol tespiti için veri yok."
    try:
        clearest_point = df_valid.loc[df_valid['mesafe_cm'].idxmax()]
        angle = clearest_point['derece']
        distance = clearest_point['mesafe_cm']
        return f"En Açık Yol: {angle:.0f}° yönünde, yaklaşık {distance:.0f} cm."
    except Exception as e:
        return f"En açık yol hesaplanamadı: {e}"


def analyze_polar_regression(df_valid):
    """Açıya karşı mesafeyi analiz eder (polar veri üzerinde regresyon)."""
    if len(df_valid) < 5:  # Regresyon için minimum nokta sayısı
        return None, "Polar regresyon için yetersiz veri."
    X, y = df_valid[['derece']].values, df_valid['mesafe_cm'].values
    try:
        ransac = RANSACRegressor(random_state=42)  # random_state tekrarlanabilirlik için
        ransac.fit(X, y)
        slope = ransac.estimator_.coef_[0]
        inference = ""
        if abs(slope) < 0.1:  # Eğim eşik değeri (ayarlanabilir)
            inference = f"Polar Regresyon: Yüzey dairesel veya tarama yayına paralel görünüyor (Eğim: {slope:.3f} cm/derece)."
        elif slope > 0:
            inference = f"Polar Regresyon: Yüzey, açı arttıkça (sensörün sağına doğru) uzaklaşıyor (Eğim: {slope:.3f} cm/derece)."
        else:  # slope < 0
            inference = f"Polar Regresyon: Yüzey, açı arttıkça (sensörün sağına doğru) yaklaşıyor (Eğim: {slope:.3f} cm/derece)."

        # Grafik üzerinde çizilecek çizginin x (derece) ve y (mesafe) değerleri
        x_range = np.array([df_valid['derece'].min(), df_valid['derece'].max()]).reshape(-1, 1)
        y_predicted = ransac.predict(x_range)
        line_data = {'x': x_range.flatten(), 'y': y_predicted}
        return line_data, inference
    except Exception as e:
        print(f"Polar regresyon analizi sırasında hata: {e}")
        return None, "Polar regresyon analizi sırasında bir hata oluştu."


def analyze_environment_shape(fig, df_valid):
    """
    Ortam şeklini analiz eder, grafiğe geometrik çıkarımları (duvar, küme vb.) çizer
    ve küme etiketleri eklenmiş bir dataframe ile metinsel bir özet döndürür.
    """
    points_all = df_valid[['y_cm', 'x_cm']].to_numpy()
    if len(points_all) < 10:
        df_valid['cluster'] = -2  # -2: Analiz için yetersiz veri
        return "Analiz için yetersiz veri.", df_valid

    # DBSCAN ile kümeleme
    db = DBSCAN(eps=5, min_samples=2).fit(points_all)  # eps ve min_samples ayarlanabilir
    labels = db.labels_
    df_valid['cluster'] = labels  # Küme etiketlerini dataframe'e ekle

    descriptions = []
    # --- RANSAC ile Duvar/Koridor/Köşe Tespiti (Önceki versiyonlardaki mantık buraya entegre edilebilir) ---
    # Örnek:
    # if koridor_tespit_edildi:
    #     descriptions.append("Bir koridor algılandı.")
    #     fig.add_trace(...) # Koridor çizgilerini ekle
    # elif köşe_tespit_edildi:
    #     descriptions.append("Bir köşe algılandı.")
    #     fig.add_trace(...) # Köşe çizgilerini ekle
    # else:
    #     descriptions.append("Belirgin bir geometrik yapı (koridor/köşe) bulunamadı.")
    # Şimdilik basit bir metin ekleyelim:
    unique_clusters = set(labels)
    num_actual_clusters = len(unique_clusters - {-1})  # Gürültü olmayan küme sayısı
    if num_actual_clusters > 0:
        descriptions.append(f"{num_actual_clusters} adet potansiyel nesne kümesi tespit edildi.")
    else:
        descriptions.append("Belirgin bir nesne kümesi bulunamadı.")

    # Kümeleri grafiğe çiz
    colors = plt.cm.get_cmap('viridis', len(unique_clusters) if len(unique_clusters) > 0 else 1)
    for k_label in unique_clusters:
        cluster_points_to_plot = points_all[labels == k_label]
        if k_label == -1:  # Gürültü noktaları
            color_val, point_size, name_val = 'rgba(128,128,128,0.3)', 5, 'Gürültü/Diğer'
        else:  # Küme noktaları
            norm_k = k_label / (len(unique_clusters) - 1) if len(unique_clusters) > 1 else 0
            raw_col = colors(norm_k)
            color_val = f'rgba({raw_col[0] * 255},{raw_col[1] * 255},{raw_col[2] * 255},0.9)'
            point_size, name_val = 8, f'Küme {k_label}'
        fig.add_trace(go.Scatter(
            x=cluster_points_to_plot[:, 0], y=cluster_points_to_plot[:, 1], mode='markers',
            marker=dict(color=color_val, size=point_size), name=name_val,
            customdata=[k_label] * len(cluster_points_to_plot)  # Tıklama için küme etiketini sakla
        ))

    return " ".join(descriptions) if descriptions else "Ortam analizi yapılamadı.", df_valid


# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================

# --- KONTROL CALLBACK'LERİ ---
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('start-angle-input', 'value'),
     State('end-angle-input', 'value'),
     State('step-angle-input', 'value'),
     State('buzzer-distance-input', 'value')],
    prevent_initial_call=True)
def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val, buzzer_distance_val):
    if n_clicks_start == 0: return no_update
    start_a = start_angle_val if start_angle_val is not None else DEFAULT_UI_SCAN_START_ANGLE
    end_a = end_angle_val if end_angle_val is not None else DEFAULT_UI_SCAN_END_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE
    buzzer_d = buzzer_distance_val if buzzer_distance_val is not None else DEFAULT_UI_BUZZER_DISTANCE
    if not (0 <= start_a <= 180 and 0 <= end_a <= 180): return dbc.Alert("Açılar 0-180 arasında olmalıdır!",
                                                                         color="danger")
    if not (1 <= abs(step_a) <= 45): return dbc.Alert("Adım açısı 1-45 arasında olmalıdır!", color="danger")
    if not (0 <= buzzer_d <= 200): return dbc.Alert("Buzzer mesafesi 0-200 cm arasında olmalıdır!", color="danger")
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip(); current_pid = int(pid_str) if pid_str else None
        except (IOError, ValueError):
            current_pid = None
    if current_pid and is_process_running(current_pid): return dbc.Alert(f"Betik zaten çalışıyor (PID: {current_pid}).",
                                                                         color="warning")
    for f_path in [LOCK_FILE_PATH_FOR_DASH, PID_FILE_PATH_FOR_DASH]:
        if os.path.exists(f_path):
            try:
                os.remove(f_path)
            except OSError as e:
                return dbc.Alert(f"Kalıntı dosya ({f_path}) silinemedi: {e}.", color="danger")
    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(f"Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}",
                                                                    color="danger")
        cmd = [python_executable, SENSOR_SCRIPT_PATH, "--start_angle", str(start_a), "--end_angle", str(end_a),
               "--step_angle", str(step_a), "--buzzer_distance", str(buzzer_d)]
        log_file_path = os.path.join(PROJECT_ROOT_DIR, 'sensor_script.log')
        with open(log_file_path, 'w') as log_file:
            subprocess.Popen(cmd, start_new_session=True, stdout=log_file, stderr=log_file)
        time.sleep(2.5)
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                new_pid_str = pf_new.read().strip()
            return dbc.Alert(f"Sensör okumaları başladı (PID: {new_pid_str})...", color="success")
        else:
            log_content = "";  # ... (log okuma ve hata gösterme mantığı eklenebilir)
            return dbc.Alert(f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Logları kontrol edin.", color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken hata: {str(e)}", color="danger")


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True)
def handle_stop_scan_script(n_clicks_stop):
    if n_clicks_stop == 0: return no_update
    pid_to_kill = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip(); pid_to_kill = int(pid_str) if pid_str else None
        except (IOError, ValueError):
            pid_to_kill = None
    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            os.kill(pid_to_kill, signal.SIGTERM);
            time.sleep(2.0)
            if is_process_running(pid_to_kill): os.kill(pid_to_kill, signal.SIGKILL); time.sleep(0.5)
            if not is_process_running(pid_to_kill):
                for f_path in [PID_FILE_PATH_FOR_DASH, LOCK_FILE_PATH_FOR_DASH]:
                    if os.path.exists(f_path): os.remove(f_path)
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) durduruldu.", color="info")
            else:
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulamadı!", color="danger")
        except Exception as e:
            return dbc.Alert(f"Betik durdurulurken hata: {e}", color="danger")
    else:
        for f_path in [PID_FILE_PATH_FOR_DASH, LOCK_FILE_PATH_FOR_DASH]:
            if os.path.exists(f_path): os.remove(f_path)
        return dbc.Alert("Çalışan betik bulunamadı.", color="warning")


# --- GÜNCELLEME CALLBACK'LERİ ---
@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children'),
     Output('current-distance-col', 'style')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    conn, error = get_db_connection()
    angle_str, distance_str, speed_str = "--°", "-- cm", "-- cm/s"
    distance_style = {'padding': '10px', 'transition': 'background-color 0.5s ease', 'borderRadius': '5px'}
    if error: return angle_str, distance_str, speed_str, distance_style
    if conn:
        try:
            latest_id = get_latest_scan_id_from_db(conn)
            if latest_id:
                df_point = pd.read_sql_query(
                    f"SELECT mesafe_cm, derece, hiz_cm_s FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC LIMIT 1",
                    conn)
                df_settings = pd.read_sql_query(
                    f"SELECT buzzer_distance_setting FROM servo_scans WHERE id = {latest_id}", conn)
                buzzer_thr = float(
                    df_settings['buzzer_distance_setting'].iloc[0]) if not df_settings.empty and pd.notnull(
                    df_settings['buzzer_distance_setting'].iloc[0]) else None
                if not df_point.empty:
                    dist, angle, speed = df_point['mesafe_cm'].iloc[0], df_point['derece'].iloc[0], \
                    df_point['hiz_cm_s'].iloc[0]
                    angle_str, distance_str, speed_str = f"{angle:.0f}°" if pd.notnull(
                        angle) else "--°", f"{dist:.1f} cm" if pd.notnull(
                        dist) else "-- cm", f"{speed:.1f} cm/s" if pd.notnull(speed) else "-- cm/s"
                    if buzzer_thr is not None and pd.notnull(dist) and dist <= buzzer_thr:
                        distance_style['backgroundColor'], distance_style['color'] = '#d9534f', 'white'
        except Exception as e:
            print(f"Anlık değerler güncellenirken hata: {e}")
        finally:
            conn.close()
    return angle_str, distance_str, speed_str, distance_style


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'),
     Output('max-depth', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n_intervals):
    conn, error = get_db_connection()
    area, perim, width, depth = "-- cm²", "-- cm", "-- cm", "-- cm"
    if error: return area, perim, width, depth
    if conn:
        try:
            latest_id = get_latest_scan_id_from_db(conn)
            if latest_id:
                df_scan = pd.read_sql_query(
                    f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {latest_id}",
                    conn)
                if not df_scan.empty:
                    r = df_scan.iloc[0]
                    area = f"{r['hesaplanan_alan_cm2']:.2f} cm²" if pd.notnull(r['hesaplanan_alan_cm2']) else "N/A"
                    perim = f"{r['cevre_cm']:.2f} cm" if pd.notnull(r['cevre_cm']) else "N/A"
                    width = f"{r['max_genislik_cm']:.2f} cm" if pd.notnull(r['max_genislik_cm']) else "N/A"
                    depth = f"{r['max_derinlik_cm']:.2f} cm" if pd.notnull(r['max_derinlik_cm']) else "N/A"
        except Exception as e:
            print(f"Analiz paneli DB sorgu hatası: {e}")
        finally:
            conn.close()
    return area, perim, width, depth


@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'), Output('cpu-usage', 'value'),
     Output('cpu-usage', 'label'), Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    status_text, status_class = "Beklemede", "text-secondary"
    pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip(); pid = int(pid_str) if pid_str else None
        except (IOError, ValueError):
            pass
    if pid and is_process_running(pid):
        status_text, status_class = f"Çalışıyor (PID: {pid})", "text-success"
    else:
        status_text, status_class = "Çalışmıyor", "text-danger"
    cpu, ram = psutil.cpu_percent(0.1), psutil.virtual_memory().percent
    return status_text, status_class, cpu, f"{cpu:.1f}%", ram, f"{ram:.1f}%"


@app.callback(
    Output('download-csv', 'data'),
    [Input('export-csv-button', 'n_clicks')],
    prevent_initial_call=True)
def export_csv_callback(n_clicks):
    if n_clicks == 0: return no_update
    conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        latest_id = get_latest_scan_id_from_db(conn)
        if latest_id:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
            return dcc.send_data_frame(df.to_csv, f"tarama_id_{latest_id}_noktalar.csv", index=False)
    finally:
        if conn: conn.close()
    return no_update


@app.callback(
    Output('download-excel', 'data'),
    [Input('export-excel-button', 'n_clicks')],
    prevent_initial_call=True)
def export_excel_callback(n_clicks):
    if n_clicks == 0: return no_update
    conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        latest_id = get_latest_scan_id_from_db(conn)
        if latest_id:
            df_points = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC",
                                          conn)
            df_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {latest_id}", conn)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                df_points.to_excel(writer, sheet_name=f'Scan_{latest_id}_Points', index=False)
                df_info.to_excel(writer, sheet_name=f'Scan_{latest_id}_Info', index=False)
            buf.seek(0)
            return dcc.send_bytes(buf.read(), f"tarama_detaylari_id_{latest_id}.xlsx")
    finally:
        if conn: conn.close()
    return no_update


@app.callback(
    Output('tab-content-datatable', 'children'),
    [Input('visualization-tabs-main', 'active_tab'), Input('interval-component-main', 'n_intervals')]
)
def render_and_update_data_table(active_tab, n_intervals):
    if active_tab == "tab-datatable":
        conn, error = get_db_connection()
        if not conn: return dbc.Alert("Veritabanı bağlantısı kurulamadı.", color="danger")
        try:
            latest_id = get_latest_scan_id_from_db(conn)
            if not latest_id: return html.P("Henüz görüntülenecek tarama verisi yok.")
            query = f"SELECT id, derece, mesafe_cm, hiz_cm_s, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC"
            df = pd.read_sql_query(query, conn)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%Y-%m-%d %H:%M:%S.%f').str[:-3]
            return dash_table.DataTable(data=df.to_dict('records'),
                                        columns=[{"name": i.replace("_", " ").title(), "id": i} for i in df.columns],
                                        style_table={'height': '70vh', 'overflowY': 'auto'}, page_size=20)
        except Exception as e:
            return dbc.Alert(f"Tablo oluşturulurken hata: {e}", color="danger")
        finally:
            if conn: conn.close()
    return None


# --- ANA GÖRSEL GÜNCELLEME CALLBACK'İ (GÜNCELLENMİŞ) ---
@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('polar-regression-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure'),
     Output('environment-estimation-text', 'children'),
     Output('clustered-data-store', 'data')],  # Kümelenmiş veriyi saklamak için
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n_intervals):
    fig_map, fig_polar_regression, fig_polar, fig_time = go.Figure(), go.Figure(), go.Figure(), go.Figure()
    estimation_text_cartesian = "Kartezyen analiz için veri bekleniyor..."
    estimation_text_polar = "Polar analiz için veri bekleniyor..."
    clearest_path_text = ""
    id_to_plot, conn, clustered_store_data = None, None, None

    try:
        conn, error_msg_conn = get_db_connection()
        if conn and not error_msg_conn:
            id_to_plot = get_latest_scan_id_from_db(conn_param=conn)
            if id_to_plot:
                df_points = pd.read_sql_query(
                    f"SELECT x_cm, y_cm, derece, mesafe_cm, timestamp FROM scan_points WHERE scan_id = {id_to_plot} ORDER BY derece ASC",
                    conn)
                if not df_points.empty:
                    df_valid = df_points[(df_points['mesafe_cm'] > 1.0) & (df_points['mesafe_cm'] < 250.0)].copy()
                    if len(df_valid) >= 2:
                        # 1. Kartezyen Analiz ve Harita
                        add_sensor_position(fig_map)
                        add_scan_rays(fig_map, df_valid)
                        add_sector_area(fig_map, df_valid)
                        estimation_text_cartesian, df_clustered = analyze_environment_shape(fig_map, df_valid)
                        clustered_store_data = df_clustered.to_json(date_format='iso',
                                                                    orient='split')  # Kümelenmiş veriyi sakla

                        # 2. Polar Regresyon Analizi
                        polar_line_data, estimation_text_polar = analyze_polar_regression(df_valid)
                        fig_polar_regression.add_trace(
                            go.Scatter(x=df_valid['derece'], y=df_valid['mesafe_cm'], mode='markers',
                                       name='Ölçülen Noktalar'))
                        if polar_line_data:
                            fig_polar_regression.add_trace(
                                go.Scatter(x=polar_line_data['x'], y=polar_line_data['y'], mode='lines',
                                           name='Regresyon Çizgisi', line=dict(color='red', width=3)))

                        # 3. En Açık Yol Tespiti
                        clearest_path_text = find_clearest_path(df_valid)

                        # 4. Diğer Grafikler
                        update_polar_graph(fig_polar, df_valid)
                        update_time_series_graph(fig_time, df_valid)
                    else:
                        estimation_text_cartesian = "Analiz için yeterli geçerli nokta bulunamadı."
                        add_sensor_position(fig_map)  # Boş haritaya sensör pozisyonunu ekle
                else:
                    estimation_text_cartesian = f"Tarama ID {id_to_plot} için nokta bulunamadı."
                    add_sensor_position(fig_map)
            else:
                estimation_text_cartesian = "Tarama başlatın."
                add_sensor_position(fig_map)
        else:
            estimation_text_cartesian = f"Veritabanı bağlantı hatası: {error_msg_conn}"

    except Exception as e:
        import traceback
        print(f"HATA: Grafikleme hatası: {e}\n{traceback.format_exc()}")
        estimation_text_cartesian = f"Kritik Grafikleme Hatası: {e}"
    finally:
        if conn: conn.close()

    # Her durumda figürlerin layout'larını ayarla
    common_legend = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    fig_map.update_layout(title_text='Ortamın 2D Haritası (Analizli)', xaxis_title="Yatay Mesafe (cm)",
                          yaxis_title="Dikey Mesafe (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1,
                          uirevision=id_to_plot, legend=common_legend)
    fig_polar_regression.update_layout(title_text='Açıya Göre Mesafe Regresyonu', xaxis_title="Açı (Derece)",
                                       yaxis_title="Mesafe (cm)", uirevision=id_to_plot, legend=common_legend)
    fig_polar.update_layout(title_text='Polar Grafik', uirevision=id_to_plot, legend=common_legend)
    fig_time.update_layout(title_text='Zaman Serisi - Mesafe', uirevision=id_to_plot, legend=common_legend)

    # Tahmin metinlerini birleştir
    final_estimation_text = html.Div([
        html.P(clearest_path_text, className="fw-bold text-info"),
        html.Hr(),
        html.P(estimation_text_cartesian),
        html.Hr(),
        html.P(estimation_text_polar)
    ])

    return fig_map, fig_polar_regression, fig_polar, fig_time, final_estimation_text, clustered_store_data


# --- ETKİLEŞİM İÇİN YENİ CALLBACK ---
@app.callback(
    [Output("cluster-info-modal", "is_open"),
     Output("modal-title", "children"),
     Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")],  # Sadece ana haritadaki tıklamaları dinle
    [State("clustered-data-store", "data")],
    prevent_initial_call=True,
)
def display_cluster_info(clickData, stored_data):
    if not clickData or not stored_data:
        return False, no_update, no_update  # Modal kapalı kalsın

    try:
        df_clustered = pd.read_json(stored_data, orient='split')
        if 'cluster' not in df_clustered.columns:  # Beklenen sütun yoksa
            return False, "Hata", "Küme verisi bulunamadı."

        # Tıklanan noktanın x ve y koordinatlarını al (Plotly'nin döndürdüğü)
        # analyze_environment_shape içinde kümeler (y_cm, x_cm) koordinatlarına göre çizilmişti.
        clicked_plotly_x = clickData["points"][0]["x"]  # Bu bizim 'y_cm' sütunumuza denk geliyor
        clicked_plotly_y = clickData["points"][0]["y"]  # Bu bizim 'x_cm' sütunumuza denk geliyor

        # Tıklanan noktaya en yakın veri noktasını bul (geometrik olarak)
        # (df_clustered['y_cm'] - clicked_plotly_x)**2 + (df_clustered['x_cm'] - clicked_plotly_y)**2
        # Bu, Plotly'nin koordinat sistemi ile DataFrame'deki sütun adları arasındaki eşleşmeye dikkat etmeyi gerektirir.
        # analyze_environment_shape fonksiyonu, kümeleri figüre eklerken (y_cm, x_cm) kullanır.
        # Bu yüzden clickData'dan gelen x, df'deki y_cm'e; clickData'dan gelen y, df'deki x_cm'e karşılık gelir.
        distances = np.sqrt(
            (df_clustered['y_cm'] - clicked_plotly_x) ** 2 + (df_clustered['x_cm'] - clicked_plotly_y) ** 2)

        if distances.empty:  # Eğer df_clustered boşsa veya distances hesaplanamadıysa
            return False, "Hata", "Mesafe hesaplanamadı."

        closest_point_index = distances.idxmin()
        closest_point = df_clustered.loc[closest_point_index]
        cluster_label = closest_point['cluster']

        if cluster_label == -1:  # Gürültü noktası
            title = "Gürültü Noktası"
            body = "Bu nokta bir kümeye ait değil (gürültü olarak sınıflandırıldı)."
        elif cluster_label == -2:  # Analiz için yetersiz veri durumu
            title = "Analiz Yapılamadı"
            body = "Bu bölge için analiz yapılamadı (yetersiz veri)."
        else:  # Geçerli bir küme
            cluster_df = df_clustered[df_clustered['cluster'] == cluster_label]
            num_points = len(cluster_df)
            # Kümenin yaklaşık boyutları (y_cm ve x_cm sütunlarına göre)
            width = cluster_df['y_cm'].max() - cluster_df['y_cm'].min() if num_points > 0 else 0
            depth = cluster_df['x_cm'].max() - cluster_df['x_cm'].min() if num_points > 0 else 0

            title = f"Küme #{int(cluster_label)} Detayları"
            body = html.Div([
                html.P(f"Nokta Sayısı: {num_points}"),
                html.P(f"Yaklaşık Genişlik (Yatay Eksen): {width:.1f} cm"),
                html.P(f"Yaklaşık Derinlik (Dikey Eksen): {depth:.1f} cm"),
                # İsteğe bağlı: Kümenin ortalama mesafesi, açı aralığı vb. eklenebilir
            ])
        return True, title, body  # Modal'ı aç ve içeriği doldur
    except Exception as e:
        import traceback
        print(f"Modal hatası: {e}\n{traceback.format_exc()}")
        return True, "Hata", f"Küme bilgisi gösterilirken bir hata oluştu: {e}"

